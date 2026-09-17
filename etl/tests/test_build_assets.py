"""Hand-checked tests for etl/build_assets.py helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import shapely
from pyproj import Geod

from etl.build_assets import (
    assign_placeholder_groups,
    clean_party,
    densify_geodesic,
    geodesic_length_m,
    to_number,
)

GEOD = Geod(ellps="WGS84")


def test_geodesic_length_of_equator_line() -> None:
    """(0,0)->(0.05,0)->(0.15,0): a*rad(0.15) = 16697.923619 m along the equator."""
    line = shapely.LineString([(0.0, 0.0), (0.05, 0.0), (0.15, 0.0)])
    assert geodesic_length_m(line) == pytest.approx(16697.923619, abs=1e-5)


def test_densify_inserts_geodesic_vertices_and_keeps_endpoints() -> None:
    """5565.97 m at 2 km max spacing -> ceil(2.78) = 3 segments of ~1855.3 m."""
    line = shapely.LineString([(0.0, 0.0), (0.05, 0.0)])
    dense = densify_geodesic(line, 2_000.0)
    coords = shapely.get_coordinates(dense)
    assert len(coords) == 4
    assert tuple(coords[0]) == (0.0, 0.0) and tuple(coords[-1]) == (0.05, 0.0)
    _, _, spacing = GEOD.inv(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    assert spacing.max() <= 2_000.0
    assert spacing == pytest.approx([5565.974540 / 3] * 3, abs=1e-3)
    assert geodesic_length_m(dense) == pytest.approx(5565.974540, abs=1e-3)


def test_densify_leaves_short_segments_alone_and_handles_multipart() -> None:
    multi = shapely.MultiLineString([[(0.0, 0.0), (0.01, 0.0)], [(10.0, 45.0), (10.5, 45.0)]])
    dense = densify_geodesic(multi, 2_000.0)
    parts = shapely.get_parts(dense)
    assert len(parts) == 2
    assert shapely.get_num_coordinates(parts[0]) == 2, "1113 m segment must not be densified"
    assert shapely.get_num_coordinates(parts[1]) == 21, "39423 m -> 20 segments -> 21 vertices"


def test_densified_vertices_lie_on_the_geodesic_not_the_parallel() -> None:
    """East-west at lat 45: inserted vertices bulge poleward of the parallel."""
    dense = densify_geodesic(shapely.LineString([(10.0, 45.0), (10.5, 45.0)]), 2_000.0)
    mid_lat = shapely.get_coordinates(dense)[10, 1]
    assert mid_lat > 45.0
    assert (mid_lat - 45.0) * 111_000 == pytest.approx(30.4, abs=1.0), "~30 m sagitta measured in 3b"


@pytest.mark.parametrize(
    "value, expected",
    [("Gazprom PJSC [100.00%]", "Gazprom PJSC [100.00%]"), ("--", None), ("", None), (None, None),
     ("unknown", None), ("unknown [unknown %]", None), ("N/A", None), ("  ORLEN SA [100%] ", "ORLEN SA [100%]"),
     ("Exxon Mobil Corp [unknown %]; TC Energy Corp [unknown %]", "Exxon Mobil Corp [unknown %]; TC Energy Corp [unknown %]")],
)
def test_clean_party(value: object, expected: str | None) -> None:
    assert clean_party(value) == expected


def test_to_number_handles_gem_and_ogim_null_conventions() -> None:
    assert to_number("13.8") == 13.8
    assert np.isnan(to_number("--")) and np.isnan(to_number("")) and np.isnan(to_number(-999.0))


def test_placeholder_groups_collapse_identical_geometry_only() -> None:
    same = shapely.to_wkb(shapely.Point(52.1813, 26.8614))
    other = shapely.to_wkb(shapely.Point(52.1814, 26.8614))
    assets = pd.DataFrame({"asset_id": list("abcd"), "geometry_wkb": [same, same, same, other]})
    out = assign_placeholder_groups(assets)
    assert out.placeholder_group_size.tolist() == [3, 3, 3, 1]
    assert out.placeholder_group.iloc[0] == out.placeholder_group.iloc[2]
    assert pd.isna(out.placeholder_group.iloc[3])


def test_extraction_sigma_percentiles_by_accuracy_class_with_fallback() -> None:
    from etl.build_assets import extraction_point_sigma, extraction_sigma_percentiles

    outlined = pd.DataFrame({
        "eq_radius_m": [1000.0, 2000.0, 3000.0, 4000.0, 10_000.0],
        "accuracy": ["exact", "exact", "exact", "approximate", ""],
    })
    pct = extraction_sigma_percentiles(outlined)
    assert set(pct) == {"all", "exact", "approximate"}
    assert pct["exact"]["n"] == 3 and pct["exact"]["p50"] == 2000.0
    assert pct["approximate"]["p50"] == 4000.0 and pct["all"]["p50"] == 3000.0
    assert extraction_point_sigma(pct, "exact") == 2000.0
    assert extraction_point_sigma(pct, None) == 3000.0, "unknown class falls back to all"
    assert extraction_point_sigma(pct, "exact", percentile=90) == pytest.approx(2800.0)
