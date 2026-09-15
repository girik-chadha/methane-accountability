"""Hand-checked tests for the vertex-spacing measurement and the CRS refusal.

The spacing distribution is the point of slice 3b, so its arithmetic gets the
same treatment as geo.py: golden values derived by hand from the WGS84 closed
forms, confirmed against pyproj, and an independent scalar re-derivation of the
vectorised chord-deviation measurement.
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString

from backend.app.config import EARTH_MEAN_RADIUS_M, INFRA_REQUIRED_EPSG
from etl.inspect_infra import CrsError, inspect_layer, require_epsg, segment_table

GEOD = Geod(ellps="WGS84")


def series(*geoms: object) -> gpd.GeoSeries:
    return gpd.GeoSeries(list(geoms), crs=f"EPSG:{INFRA_REQUIRED_EPSG}")


# --- spacing ------------------------------------------------------------------


def test_equator_spacings_match_hand_values() -> None:
    """Vertices at lon 0, 0.05, 0.15 on the equator.

    Hand check: a * radians(0.05) = 5565.974540 m, a * radians(0.10) =
    11131.949079 m. Along the equator these are exact geodesic distances.
    """
    table = segment_table(series(LineString([(0.0, 0.0), (0.05, 0.0), (0.15, 0.0)])))
    assert list(table["spacing_m"].round(6)) == [5565.974540, 11131.949079]
    assert list(table["feature"]) == [0, 0]


def test_chord_deviation_is_zero_along_the_equator() -> None:
    """The equator is a geodesic, straight in (lon, lat), and uniform in scale."""
    table = segment_table(series(LineString([(0.0, 0.0), (0.1, 0.0)])))
    assert abs(table["deviation_m"].iloc[0]) < 1e-6


def test_meridian_deviation_is_the_along_track_term_only() -> None:
    """A meridian is a geodesic, so its true cross-track gap is zero, but the
    measurement is not: equal steps of latitude are not equal metres, so the
    geodesic midpoint (half the DISTANCE) sits slightly off the latitude
    midpoint. Hand derivation: |L/2 - dist(A -> latitude midpoint)|.

    This documents that "deviation" is an upper bound including an along-track
    term, and pins that term at about half a metre over 55 km.
    """
    lon, lat1, lat2 = 20.0, -30.0, -30.5
    table = segment_table(series(LineString([(lon, lat1), (lon, lat2)])))
    _, _, length_m = GEOD.inv(lon, lat1, lon, lat2)
    _, _, to_lat_mid_m = GEOD.inv(lon, lat1, lon, (lat1 + lat2) / 2.0)
    expected_m = abs(length_m / 2.0 - to_lat_mid_m)
    assert table["deviation_m"].iloc[0] == pytest.approx(expected_m, abs=1e-3)
    assert 0.4 < expected_m < 0.7


def test_multipart_parts_are_not_joined_into_a_segment() -> None:
    """A MultiLineString with two far-apart parts yields two segments, not three."""
    multi = MultiLineString([[(10.0, 45.0), (10.5, 45.0)], [(20.0, -30.0), (20.0, -30.01)]])
    table = segment_table(series(multi))
    assert len(table) == 2
    assert table["spacing_m"].max() < 50_000.0, "a part-spanning segment would be ~8,000 km"


def test_zero_length_segment_from_repeated_vertex() -> None:
    table = segment_table(series(LineString([(0.0, 0.0), (0.0, 0.0), (0.01, 0.0)])))
    assert table["spacing_m"].iloc[0] == 0.0
    assert table["spacing_m"].iloc[1] == pytest.approx(1113.194908, abs=1e-5)


def test_non_line_geometries_are_ignored() -> None:
    from shapely.geometry import GeometryCollection, Point

    table = segment_table(series(Point(0.0, 0.0), GeometryCollection(), LineString([(0.0, 0.0), (0.01, 0.0)])))
    assert len(table) == 1
    assert table["feature"].iloc[0] == 2


def test_chord_deviation_matches_independent_scalar_derivation() -> None:
    """Re-derive the deviation for one segment with scalar pyproj calls.

    East-west 0.5 degrees at latitude 45: the geodesic bulges poleward of the
    parallel. Independent scalar computation must agree with the vectorised
    table to the millimetre.
    """
    lon1, lat1, lon2, lat2 = 10.0, 45.0, 10.5, 45.0
    table = segment_table(series(LineString([(lon1, lat1), (lon2, lat2)])))

    azimuth, _, length_m = GEOD.inv(lon1, lat1, lon2, lat2)
    mid_lon, mid_lat, _ = GEOD.fwd(lon1, lat1, azimuth, length_m / 2.0)
    _, _, expected_m = GEOD.inv(mid_lon, mid_lat, (lon1 + lon2) / 2.0, (lat1 + lat2) / 2.0)

    assert table["deviation_m"].iloc[0] == pytest.approx(expected_m, abs=1e-3)
    assert expected_m == pytest.approx(30.409, abs=0.01)


def test_chord_deviation_agrees_with_sagitta_formula() -> None:
    """Physical cross-check: deviation ~ L^2 tan(lat) / (8 R) for an E-W chord.

    EARTH_MEAN_RADIUS_M is used here for an order-of-magnitude bound only;
    its config docstring forbids it in the projection, not in a sanity check.
    Agreement to 1% at 40 km confirms the measurement is the geodesic-vs-chord
    sagitta and not some artefact of the midpoint construction.
    """
    for lat, lon_span in ((45.0, 0.5), (60.0, 0.72)):
        table = segment_table(series(LineString([(0.0, lat), (lon_span, lat)])))
        length_m = table["spacing_m"].iloc[0]
        formula_m = length_m**2 * math.tan(math.radians(lat)) / (8.0 * EARTH_MEAN_RADIUS_M)
        assert table["deviation_m"].iloc[0] == pytest.approx(formula_m, rel=0.01)


# --- CRS refusal --------------------------------------------------------------


def test_require_epsg_accepts_4326() -> None:
    assert require_epsg("EPSG:4326", "test").to_epsg() == 4326


@pytest.mark.parametrize("bad", ["EPSG:3857", "EPSG:4269", "EPSG:32633"])
def test_require_epsg_refuses_other_crs(bad: str) -> None:
    """Includes NAD83 (4269): a geographic CRS that is not WGS84 is the sneaky case."""
    with pytest.raises(CrsError, match="Refusing to reproject"):
        require_epsg(bad, "test")


def test_require_epsg_refuses_missing_crs() -> None:
    with pytest.raises(CrsError, match="no CRS"):
        require_epsg(None, "test")


def test_inspect_layer_refuses_a_real_non_4326_geopackage(tmp_path: Path) -> None:
    """The refusal must happen at the file level, before any data is loaded."""
    path = tmp_path / "bad.gpkg"
    gpd.GeoDataFrame(
        {"n": [1]}, geometry=[LineString([(0.0, 0.0), (1000.0, 1000.0)])], crs="EPSG:3857"
    ).to_file(path, layer="l", driver="GPKG")
    with pytest.raises(CrsError, match="EPSG:3857"):
        inspect_layer(path, "l")
