"""Read .xlsx worksheets with the standard library only, streaming the XML.

Why not openpyxl or pandas.read_excel: GEM's ownership workbook carries a
135 MB sharedStrings.xml that both would load into memory whole, and both
apply type inference that quietly rewrites GEM's literal null tokens ("--",
empty strings) into NaN or leaves them inconsistently. This reader returns
every cell as the raw text stored in the file, or None where the cell is
absent, so an inspection sees exactly what the publisher wrote.

An .xlsx is a zip. The pieces used here:
  xl/workbook.xml            sheet names and relationship ids
  xl/_rels/workbook.xml.rels relationship id -> worksheet member path
  xl/worksheets/sheetN.xml   rows of cells; a cell's "r" attribute is its
                             reference (e.g. "C7"), "t" its type
  xl/sharedStrings.xml       the string table that cells of type "s" index

Cell types: "s" shared string (value is an index), "inlineStr" inline text,
"str" formula result text, "b" boolean, "e" error, "n" or absent numeric.
Numbers come back as the decimal text Excel stored, e.g. "31.5" or "2019".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
SHARED_STRINGS_MEMBER = "xl/sharedStrings.xml"

# A cell as stored: (type code, raw text). Type "s" means raw text is an index
# into the shared string table and still needs resolving.
RawCell = tuple[str, str]

_COLUMN_LETTERS = re.compile(r"^([A-Z]+)")
_ALPHABET = 26
_ORD_BEFORE_A = ord("A") - 1


def _tag(local: str) -> str:
    return f"{{{NS_MAIN}}}{local}"


def column_index(ref: str) -> int:
    """Zero-based column index of a cell reference: "A1" -> 0, "AB7" -> 27."""
    match = _COLUMN_LETTERS.match(ref)
    if not match:
        raise ValueError(f"not a cell reference: {ref!r}")
    index = 0
    for letter in match.group(1):
        index = index * _ALPHABET + (ord(letter) - _ORD_BEFORE_A)
    return index - 1


def list_sheets(bundle: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Return [(sheet name, worksheet member path)] in workbook order."""
    rels = ET.fromstring(bundle.read("xl/_rels/workbook.xml.rels"))
    targets: dict[str, str] = {}
    for rel in rels.iter(f"{{{NS_PKG}}}Relationship"):
        target = rel.attrib["Target"]
        targets[rel.attrib["Id"]] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    workbook = ET.fromstring(bundle.read("xl/workbook.xml"))
    return [
        (sheet.attrib["name"], targets[sheet.attrib[f"{{{NS_REL}}}id"]])
        for sheet in workbook.iter(_tag("sheet"))
    ]


def sheet_member(bundle: zipfile.ZipFile, sheet: str) -> str:
    for name, member in list_sheets(bundle):
        if name == sheet:
            return member
    raise KeyError(f"no sheet named {sheet!r}; sheets are {[n for n, _ in list_sheets(bundle)]}")


def _cell(element: ET.Element) -> RawCell | None:
    kind = element.attrib.get("t", "n")
    if kind == "inlineStr":
        raw = "".join(t.text or "" for t in element.iter(_tag("t")))
        return (kind, raw)
    value = element.find(_tag("v"))
    if value is None or value.text is None:
        return None
    return (kind, value.text)


def stream_rows(
    bundle: zipfile.ZipFile, member: str, limit: int | None = None
) -> Iterator[tuple[int, dict[int, RawCell]]]:
    """Yield (1-based row number, {column index: raw cell}) for each stored row.

    Absent cells are simply missing from the dict. Rows are placed by their
    own "r" attribute and cells by theirs, so gaps do not shift anything.
    """
    yielded = 0
    fallback_row = 0
    with bundle.open(member) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag != _tag("row"):
                continue
            fallback_row += 1
            row_number = int(element.attrib.get("r", fallback_row))
            cells: dict[int, RawCell] = {}
            fallback_column = -1
            for cell_element in element.iter(_tag("c")):
                fallback_column += 1
                ref = cell_element.attrib.get("r")
                column = column_index(ref) if ref else fallback_column
                fallback_column = column
                cell = _cell(cell_element)
                if cell is not None:
                    cells[column] = cell
            element.clear()
            yield row_number, cells
            yielded += 1
            if limit is not None and yielded >= limit:
                return


def sheet_dimension(bundle: zipfile.ZipFile, member: str) -> str | None:
    """The sheet's declared extent, e.g. "A1:AD27199", or None if not written.

    Writers are not obliged to keep this accurate; treat it as a hint.
    """
    with bundle.open(member) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag == _tag("dimension"):
                return element.attrib.get("ref")
            if element.tag == _tag("sheetData"):
                return None
    return None


def resolve_shared_strings(bundle: zipfile.ZipFile, wanted: set[int]) -> dict[int, str]:
    """Stream the shared string table, keeping only the indices in `wanted`.

    Stops as soon as the highest wanted index has been passed, so a request for
    a few header strings near the front of a huge table is cheap.
    """
    found: dict[int, str] = {}
    if not wanted or SHARED_STRINGS_MEMBER not in bundle.namelist():
        return found
    stop_after = max(wanted)
    index = -1
    with bundle.open(SHARED_STRINGS_MEMBER) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag != _tag("si"):
                continue
            index += 1
            if index in wanted:
                found[index] = "".join(t.text or "" for t in element.iter(_tag("t")))
            element.clear()
            if index >= stop_after:
                break
    return found


def resolve(cell: RawCell | None, strings: dict[int, str]) -> str | None:
    if cell is None:
        return None
    kind, raw = cell
    if kind == "s":
        return strings.get(int(raw), f"<shared string {raw} unresolved>")
    return raw


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        count = seen.get(name, 0)
        out.append(name if count == 0 else f"{name}.{count}")
        seen[name] = count + 1
    return out


def read_sheet_raw(path: Path, sheet: str, header_row: int = 1) -> pd.DataFrame:
    """Load one sheet as a DataFrame of raw cell text, None where absent.

    No type inference at all: every value is the text stored in the file.
    `header_row` is 1-based; rows above it are skipped, rows below are data.
    Columns with no header get names like "_col_28". Rows stored in the file
    with no cells at all are dropped, since they carry nothing.
    """
    with zipfile.ZipFile(path) as bundle:
        member = sheet_member(bundle, sheet)
        header: dict[int, RawCell] | None = None
        rows: list[dict[int, RawCell]] = []
        wanted: set[int] = set()
        for row_number, cells in stream_rows(bundle, member):
            if row_number < header_row or not cells:
                continue
            wanted.update(int(raw) for kind, raw in cells.values() if kind == "s")
            if row_number == header_row:
                header = cells
            else:
                rows.append(cells)
        strings = resolve_shared_strings(bundle, wanted)

    if header is None:
        raise ValueError(f"sheet {sheet!r} has no row {header_row} to use as a header")

    width = max([max(header)] + [max(r) for r in rows]) + 1
    names = _unique_names(
        [resolve(header.get(i), strings) or f"_col_{i}" for i in range(width)]
    )
    data = [[resolve(row.get(i), strings) for i in range(width)] for row in rows]
    return pd.DataFrame(data, columns=names, dtype=object)
