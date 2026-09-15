"""Hand-checked tests for the numeric helpers in etl/inspect_extraction.py."""

from __future__ import annotations

import pandas as pd
import pytest
from pyproj import Geod

from backend.app.config import WGS84_SEMI_MAJOR_AXIS_M
from etl.inspect_extraction import (
    coordinate_usability,
    null_token_tally,
    outline_table,
    split_unit_list,
)

GEOD = Geod(ellps="WGS84")


def test_null_token_tally_counts_absent_and_literal_tokens() -> None:
    series = pd.Series([None, "", "", "--", "unknown", "real", "N/A"], dtype=object)
    assert null_token_tally(series) == {"absent": 1, "''": 2, "'--'": 1, "'N/A'": 1, "'unknown'": 1}


def test_coordinate_usability_classifies_every_failure_mode() -> None:
    lat = pd.Series(["31.5", "", "abc", "91", "0", "10", "10", None], dtype=object)
    lon = pd.Series(["6.2", "", "5", "0", "0", "20", "20", "7"], dtype=object)
    stats = coordinate_usability(lat, lon)
    assert stats == {
        "rows": 8, "both_numeric": 5, "one_numeric_only": 2, "neither_numeric": 1,
        "out_of_range": 1, "at_0_0": 1, "usable": 3, "distinct_usable_points": 2,
    }


def test_outline_area_of_a_small_equatorial_square() -> None:
    """0.1 x 0.1 degrees at the equator.

    Hand check: width a * radians(0.1) = 11131.949 m; height is 0.1 degrees of
    latitude at the equator = 10 * 1105.7428 = 11057.428 m (from the geo.py
    golden value). Product 123.09 km2. The ellipsoidal area differs from that
    rectangle only by the tiny change of cos(lat) across 0.1 degrees.
    """
    wkt = pd.Series(["POLYGON ((0 0, 0.1 0, 0.1 0.1, 0 0.1, 0 0))", "", None], dtype=object)
    table = outline_table(wkt)
    assert len(table) == 1 and table.index[0] == 0
    assert table.geom_type.iloc[0] == "Polygon" and table.valid.iloc[0]
    assert table.area_km2.iloc[0] == pytest.approx(123.09, rel=2e-3)
    _, _, diagonal_m = GEOD.inv(0.0, 0.0, 0.1, 0.1)
    assert table.extent_km.iloc[0] == pytest.approx(diagonal_m / 1000.0, abs=1e-6)
    assert diagonal_m == pytest.approx(15_690.0, abs=10.0)


def test_outline_area_is_unsigned_and_multipolygons_sum() -> None:
    clockwise = "POLYGON ((0 0, 0 0.1, 0.1 0.1, 0.1 0, 0 0))"
    two = "MULTIPOLYGON (((0 0, 0.1 0, 0.1 0.1, 0 0.1, 0 0)), ((1 0, 1.1 0, 1.1 0.1, 1 0.1, 1 0)))"
    table = outline_table(pd.Series([clockwise, two], dtype=object))
    assert table.area_km2.iloc[0] == pytest.approx(123.09, rel=2e-3)
    assert table.area_km2.iloc[1] == pytest.approx(2 * 123.09, rel=2e-3)
    assert table.geom_type.tolist() == ["Polygon", "MultiPolygon"]


def test_outline_table_flags_unparseable_and_invalid() -> None:
    bowtie = "POLYGON ((0 0, 1 1, 1 0, 0 1, 0 0))"
    table = outline_table(pd.Series(["POLYGON ((0 0", bowtie], dtype=object))
    assert table.geom_type.tolist() == ["UNPARSEABLE", "Polygon"]
    assert table.valid.tolist() == [False, False]
    assert pd.isna(table.area_km2.iloc[0])


def test_outline_table_flags_projected_coordinates_instead_of_measuring_them() -> None:
    """Metres stored in a degrees column must not become a bogus area."""
    projected = "POLYGON ((300000 400000, 300100 400000, 300100 400100, 300000 400100, 300000 400000))"
    table = outline_table(pd.Series([projected], dtype=object))
    assert table.geom_type.iloc[0] == "Polygon" and table.valid.iloc[0]
    assert not table.lonlat_range_ok.iloc[0]
    assert pd.isna(table.area_km2.iloc[0]) and pd.isna(table.extent_km.iloc[0])


def test_outline_table_flags_empty_geometry() -> None:
    table = outline_table(pd.Series(["MULTIPOLYGON EMPTY"], dtype=object))
    assert table["is_empty"].iloc[0] and table.lonlat_range_ok.iloc[0]
    assert pd.isna(table.area_km2.iloc[0])


def test_split_unit_list() -> None:
    assert split_unit_list("L100000312129, L100000312143") == ["L100000312129", "L100000312143"]
    assert split_unit_list("L1") == ["L1"]
    assert split_unit_list("") == [] and split_unit_list(None) == []


def test_equator_width_constant_used_in_hand_check() -> None:
    """Pins the number the area hand check relies on."""
    import math
    assert WGS84_SEMI_MAJOR_AXIS_M * math.radians(0.1) == pytest.approx(11131.949, abs=1e-3)
