"""Tests for the standard-library xlsx reader, against a hand-built workbook.

The fixture is written cell by cell as XML so every case the reader must
handle is present on purpose: shared strings, inline strings, numbers stored
as text, booleans, a gap in the middle of a row, a row with no cells, cells
without "r" attributes, an empty shared string, and a duplicate header name.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from etl.xlsx_stream import (
    column_index,
    list_sheets,
    read_sheet_raw,
    resolve_shared_strings,
    sheet_dimension,
    sheet_member,
    stream_rows,
)

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

WORKBOOK = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook {_NS} {_NS_R}><sheets>
  <sheet name="Data" sheetId="1" r:id="rId1"/>
  <sheet name="Notes" sheetId="2" r:id="rId2"/>
</sheets></workbook>"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="x" Target="/xl/worksheets/sheet2.xml"/>
</Relationships>"""

SHARED = f"""<?xml version="1.0" encoding="UTF-8"?>
<sst {_NS} count="6" uniqueCount="6">
  <si><t>id</t></si>
  <si><t>name</t></si>
  <si><t>lat</t></si>
  <si><r><t>Al</t></r><r><t>pha</t></r></si>
  <si><t></t></si>
  <si><t>late</t></si>
</sst>"""

# Row 1 header: id | name | lat | name (duplicate). Row 2 full. Row 3 has a gap
# at B and an empty shared string at D. Row 4 has no cells. Row 5 has cells
# without r attributes. Row 6 uses an inline string and a boolean.
SHEET1 = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet {_NS}><dimension ref="A1:D6"/><sheetData>
  <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>1</v></c></row>
  <row r="2"><c r="A2"><v>7</v></c><c r="B2" t="s"><v>3</v></c><c r="C2"><v>31.5</v></c><c r="D2" t="s"><v>5</v></c></row>
  <row r="3"><c r="A3"><v>8</v></c><c r="C3"><v>1e-3</v></c><c r="D3" t="s"><v>4</v></c></row>
  <row r="4"></row>
  <row r="5"><c><v>9</v></c><c t="s"><v>3</v></c><c><v>-12.25</v></c></row>
  <row r="6"><c r="A6" t="inlineStr"><is><t>x9</t></is></c><c r="B6" t="b"><v>1</v></c><c r="C6" t="s"/></row>
</sheetData></worksheet>"""

SHEET2 = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet {_NS}><sheetData>
  <row r="1"><c r="A1" t="inlineStr"><is><t>title</t></is></c></row>
  <row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2" t="s"><v>5</v></c></row>
  <row r="3"><c r="A3"><v>1</v></c><c r="B3"><v>2</v></c></row>
</sheetData></worksheet>"""


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.xlsx"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("xl/workbook.xml", WORKBOOK)
        bundle.writestr("xl/_rels/workbook.xml.rels", RELS)
        bundle.writestr("xl/sharedStrings.xml", SHARED)
        bundle.writestr("xl/worksheets/sheet1.xml", SHEET1)
        bundle.writestr("xl/worksheets/sheet2.xml", SHEET2)
    return path


@pytest.mark.parametrize(
    "ref, expected",
    [("A1", 0), ("Z9", 25), ("AA1", 26), ("AB7", 27), ("ZZ1", 701), ("AAA1", 702), ("C", 2)],
)
def test_column_index(ref: str, expected: int) -> None:
    assert column_index(ref) == expected


def test_column_index_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        column_index("7")


def test_list_sheets_handles_relative_and_absolute_targets(workbook: Path) -> None:
    with zipfile.ZipFile(workbook) as bundle:
        assert list_sheets(bundle) == [("Data", "xl/worksheets/sheet1.xml"), ("Notes", "xl/worksheets/sheet2.xml")]
        assert sheet_member(bundle, "Notes") == "xl/worksheets/sheet2.xml"
        with pytest.raises(KeyError):
            sheet_member(bundle, "Missing")


def test_sheet_dimension_present_and_absent(workbook: Path) -> None:
    with zipfile.ZipFile(workbook) as bundle:
        assert sheet_dimension(bundle, "xl/worksheets/sheet1.xml") == "A1:D6"
        assert sheet_dimension(bundle, "xl/worksheets/sheet2.xml") is None


def test_stream_rows_places_cells_by_reference(workbook: Path) -> None:
    with zipfile.ZipFile(workbook) as bundle:
        rows = dict(stream_rows(bundle, "xl/worksheets/sheet1.xml"))
    assert rows[3] == {0: ("n", "8"), 2: ("n", "1e-3"), 3: ("s", "4")}, "gap at B must not shift C and D"
    assert rows[4] == {}
    assert rows[5] == {0: ("n", "9"), 1: ("s", "3"), 2: ("n", "-12.25")}, "cells without r fall back to sequence"
    assert rows[6] == {0: ("inlineStr", "x9"), 1: ("b", "1")}, "a cell with no <v> is absent"


def test_stream_rows_limit(workbook: Path) -> None:
    with zipfile.ZipFile(workbook) as bundle:
        assert [n for n, _ in stream_rows(bundle, "xl/worksheets/sheet1.xml", limit=2)] == [1, 2]


def test_resolve_shared_strings_keeps_only_wanted_and_joins_runs(workbook: Path) -> None:
    with zipfile.ZipFile(workbook) as bundle:
        found = resolve_shared_strings(bundle, {3, 4})
    assert found == {3: "Alpha", 4: ""}


def test_resolve_shared_strings_empty_request(workbook: Path) -> None:
    with zipfile.ZipFile(workbook) as bundle:
        assert resolve_shared_strings(bundle, set()) == {}


def test_read_sheet_raw_everything_as_written(workbook: Path) -> None:
    frame = read_sheet_raw(workbook, "Data")
    assert list(frame.columns) == ["id", "name", "lat", "name.1"]
    assert len(frame) == 4, "the row with no cells is dropped"
    assert frame.iloc[0].tolist() == ["7", "Alpha", "31.5", "late"]
    assert frame.iloc[1].tolist() == ["8", None, "1e-3", ""], "absent is None, empty shared string is ''"
    assert frame.iloc[2].tolist() == ["9", "Alpha", "-12.25", None]
    assert frame.iloc[3].tolist() == ["x9", "1", None, None]
    assert frame.dtypes.eq(object).all(), "no type inference"


def test_read_sheet_raw_header_row_offset(workbook: Path) -> None:
    frame = read_sheet_raw(workbook, "Notes", header_row=2)
    assert list(frame.columns) == ["id", "late"]
    assert frame.iloc[0].tolist() == ["1", "2"]


def test_read_sheet_raw_missing_header_row(workbook: Path) -> None:
    with pytest.raises(ValueError, match="no row 9"):
        read_sheet_raw(workbook, "Notes", header_row=9)
