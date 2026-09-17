"""Hand-constructed cases for backend/app/attribution.py.

Every geometry is built in the test so each expected value can be derived by
hand from the geo.py golden values (0.01 deg lat at the equator = 1105.7428 m;
0.05 deg lon at the equator = 5565.9745 m).
"""

from __future__ import annotations

import math

import pytest
import shapely
from pyproj import Geod

from backend.app.attribution import (
    AMBIGUITY_COMPETING,
    AMBIGUITY_NONE,
    AMBIGUITY_PLACEHOLDER_GROUP,
    MODE_SYMMETRIC_NO_WIND,
    MODE_SYMMETRIC_ZERO_OFFSET,
    MODE_WIND_CORRECTED,
    Asset,
    AssetIndex,
    Leak,
    SigmaScale,
    UnsourcedSatelliteError,
    attribute,
    pair_threshold_m,
    distance_to_geometry_m,
    generate_candidates,
    search_centre,
    search_radius_m,
)
from backend.app.config import (
    GEO_ERROR_TOLERANCE_M,
    GEO_VALIDATED_SEPARATION_M,
    SATELLITE_RESOLUTION_M,
    SEARCH_RADIUS_PIXELS,
    TIER_ASSET_ONLY,
    TIER_OPERATOR_NAMED,
    TIER_UNMAPPED,
)
from backend.app.geo import bbox_for_radius_m, offset_latlon, point_distance_m, to_local_xy

GEOD = Geod(ellps="WGS84")


def asset(asset_id: str, geometry: shapely.Geometry, kind: str = "well", **overrides: object) -> Asset:
    fields = dict(
        asset_id=asset_id, physical_id=asset_id, source_dataset="test", name=asset_id,
        asset_kind=kind, country="Testland", operator=None, owner=None, parent=None,
        status="operating", geometry=geometry,
    )
    fields.update(overrides)
    return Asset(**fields)


def point(lat: float, lon: float) -> shapely.Point:
    return shapely.Point(lon, lat)


# --- search radius -----------------------------------------------------------------


def test_radius_uses_the_finest_sourced_resolution() -> None:
    radius = search_radius_m(["EMIT - NASA", "Sentinel-2 - ESA", "Landsat - NASA/USGS"])
    assert radius == SEARCH_RADIUS_PIXELS * SATELLITE_RESOLUTION_M["Sentinel-2 - ESA"] == 60.0


def test_radius_raises_for_an_unsourced_satellite_and_names_it() -> None:
    with pytest.raises(UnsourcedSatelliteError, match="GOES - NOAA"):
        search_radius_m(["Sentinel-2 - ESA", "GOES - NOAA"])
    with pytest.raises(ValueError):
        search_radius_m([])


# --- wind back-projection ------------------------------------------------------------


def test_wind_back_projection_moves_the_centre_upwind_by_the_offset() -> None:
    """Wind (u, v) = (3, 4) m/s blows towards the north-east; the source lies
    upwind, so a 1000 m offset moves the centre by (-600, -800) m."""
    centre = search_centre(0.0, 0.0, wind_u=3.0, wind_v=4.0, upwind_offset_m=1000.0)
    x, y = to_local_xy(centre.lat, centre.lon, 0.0, 0.0)
    assert (x, y) == pytest.approx((-600.0, -800.0), abs=1e-6)
    assert centre.mode == MODE_WIND_CORRECTED and centre.shift_m == 1000.0
    assert GEOD.inv(0.0, 0.0, centre.lon, centre.lat)[2] == pytest.approx(1000.0, abs=GEO_ERROR_TOLERANCE_M)


def test_wind_back_projection_at_latitude_uses_the_projection_inverse() -> None:
    centre = search_centre(45.0, 10.0, wind_u=0.0, wind_v=-2.0, upwind_offset_m=500.0)
    x, y = to_local_xy(centre.lat, centre.lon, 45.0, 10.0)
    assert (x, y) == pytest.approx((0.0, 500.0), abs=1e-6), "southerly wind -> source is north"


def test_missing_wind_falls_back_to_the_published_point_and_says_so() -> None:
    for u, v in ((None, None), (math.nan, 1.0), (0.0, 0.0)):
        centre = search_centre(10.0, 20.0, u, v, upwind_offset_m=1000.0)
        assert (centre.lat, centre.lon, centre.mode, centre.shift_m) == (10.0, 20.0, MODE_SYMMETRIC_NO_WIND, 0.0)


def test_zero_offset_is_the_mars_default_and_is_reported_as_such() -> None:
    centre = search_centre(10.0, 20.0, 3.0, 4.0)
    assert (centre.lat, centre.lon, centre.mode) == (10.0, 20.0, MODE_SYMMETRIC_ZERO_OFFSET)


# --- origin convention -------------------------------------------------------------


def test_the_leak_is_the_projection_origin_and_flipping_the_order_changes_the_result() -> None:
    """point_distance_m is asymmetric by up to 16.5 m over 10 km at |lat| 70.
    distance_to_geometry_m must equal the leak-as-origin value, not the other."""
    leak = Leak(70.0, 30.0)
    lon2, lat2, _ = GEOD.fwd(30.0, 70.0, 125.0, 9_500.0)
    target = point(lat2, lon2)
    window = bbox_for_radius_m(leak.lat, leak.lon, GEO_VALIDATED_SEPARATION_M)
    measured, inside = distance_to_geometry_m(leak, target, window)
    leak_origin = point_distance_m(leak.lat, leak.lon, lat2, lon2)
    asset_origin = point_distance_m(lat2, lon2, leak.lat, leak.lon)
    assert abs(leak_origin - asset_origin) > 5.0, "the two conventions must be distinguishable here"
    assert measured == leak_origin and measured != asset_origin and not inside


# --- candidate generation ----------------------------------------------------------


def test_nearer_asset_ranks_first_and_operator_sets_the_tier() -> None:
    """Leak at the origin; one well 500 m north, one 5 km east (both compatible)."""
    near = asset("near", point(0.0, 0.0).__class__(0.0, 0.0))  # placeholder, replaced below
    near_lat, near_lon = offset_latlon(0.0, 0.0, 0.0, 500.0)
    far_lat, far_lon = offset_latlon(0.0, 0.0, 5_000.0, 0.0)
    index = AssetIndex.from_assets([
        asset("far", point(far_lat, far_lon), operator="Far Operator Ltd"),
        asset("near", point(near_lat, near_lon)),
    ])
    result = attribute(index, "TST_S_001", 0.0, 0.0, "Well head", ["Sentinel-5P/TROPOMI - ESA"], None, None)
    assert [c.asset.asset_id for c in result.candidates] == ["near", "far"]
    assert result.candidates[0].distance_m == pytest.approx(500.0, abs=1e-6)
    assert result.candidates[1].distance_m == pytest.approx(5_000.0, abs=1e-6)
    assert result.tier == TIER_ASSET_ONLY, "top candidate names nobody, even though the second does"
    assert result.n_candidates == 2 and result.ambiguity == AMBIGUITY_COMPETING
    assert result.n_parties == 1, "only 'far' names anyone"
    assert result.radius_capped and result.radius_m == GEO_VALIDATED_SEPARATION_M


def test_operator_named_tier_when_the_top_candidate_names_someone() -> None:
    lat, lon = offset_latlon(0.0, 0.0, 0.0, 100.0)
    index = AssetIndex.from_assets([asset("a", point(lat, lon), parent="Parent Co [100%]")])
    result = attribute(index, "TST_S_002", 0.0, 0.0, "Well head", ["EMIT - NASA"], 1.0, 1.0)
    assert result.tier == TIER_OPERATOR_NAMED and result.radius_m == 180.0
    assert result.candidates[0].asset.named_party == "Parent Co [100%]"


def test_nothing_in_radius_is_unmapped_not_a_crash() -> None:
    lat, lon = offset_latlon(0.0, 0.0, 0.0, 5_000.0)
    index = AssetIndex.from_assets([asset("far", point(lat, lon))])
    result = attribute(index, "TST_S_003", 0.0, 0.0, "Flare", ["Sentinel-2 - ESA"], None, None)
    assert result.tier == TIER_UNMAPPED and result.n_candidates == 0 and result.top is None
    assert result.ambiguity == AMBIGUITY_NONE and result.radius_m == 60.0
    assert generate_candidates(index, Leak(0.0, 0.0), 60.0) == []


def test_compatible_kind_outranks_a_nearer_incompatible_one_inside_the_radius() -> None:
    """Inside the radius all positions are equally plausible, so a well beats
    a nearer LNG terminal for a 'Well head' source; for 'Shipping' the
    ordering flips."""
    lng_lat, lng_lon = offset_latlon(0.0, 0.0, 0.0, 100.0)
    well_lat, well_lon = offset_latlon(0.0, 0.0, 0.0, 150.0)
    index = AssetIndex.from_assets([
        asset("lng", point(lng_lat, lng_lon), kind="lng_terminal"),
        asset("well", point(well_lat, well_lon), kind="well"),
    ])
    leak = Leak(0.0, 0.0)
    assert [c.asset.asset_id for c in generate_candidates(index, leak, 180.0, "Well head")] == ["well", "lng"]
    assert [c.asset.asset_id for c in generate_candidates(index, leak, 180.0, "Shipping")] == ["lng", "well"]
    assert [c.asset.asset_id for c in generate_candidates(index, leak, 180.0, "O&G source (generic)")] == ["lng", "well"]


def test_placeholder_group_of_three_collapses_to_one_group() -> None:
    lat, lon = offset_latlon(0.0, 0.0, 30.0, 40.0)
    shared = point(lat, lon)
    index = AssetIndex.from_assets([
        asset(f"phase_{i}", shared, kind="extraction_unit", placeholder_group="pg_x", placeholder_group_size=3)
        for i in range(3)
    ])
    result = attribute(index, "TST_S_004", 0.0, 0.0, "Well head", ["Sentinel-2 - ESA"], None, None)
    assert result.n_candidates == 3 and result.n_groups == 1 and result.n_parties == 0
    assert result.ambiguity == AMBIGUITY_PLACEHOLDER_GROUP
    assert all(c.distance_m == pytest.approx(50.0, abs=1e-6) for c in result.candidates)


def test_candidates_are_a_set_not_nearest_wins() -> None:
    assets = [asset(f"w{i}", point(*offset_latlon(0.0, 0.0, 15.0 * i, 0.0))) for i in range(1, 7)]
    result = attribute(AssetIndex.from_assets(assets), "TST_S_005", 0.0, 0.0, "Well head", ["Sentinel-2 - ESA"], None, None)
    assert result.n_candidates == 3, "15, 30, 45 m are inside a 60 m radius; 75 and 90 are not"
    assert [c.asset.asset_id for c in result.candidates] == ["w1", "w2", "w3"]


# --- polygons ------------------------------------------------------------------------


def square(lat0: float, lon0: float, west_m: float, east_m: float, south_m: float, north_m: float) -> shapely.Polygon:
    """Axis-aligned (in the local plane about lat0/lon0) rectangle as lon/lat."""
    corners = [(west_m, south_m), (east_m, south_m), (east_m, north_m), (west_m, north_m)]
    return shapely.Polygon([(lon, lat) for lat, lon in (offset_latlon(lat0, lon0, x, y) for x, y in corners)])


def test_leak_inside_a_field_polygon_is_a_zero_distance_candidate() -> None:
    field = square(0.0, 0.0, -3_000.0, 3_000.0, -3_000.0, 3_000.0)
    index = AssetIndex.from_assets([asset("field", field, kind="field_outline", operator="Field Op")])
    result = attribute(index, "TST_S_006", 0.0, 0.0, "Well head", ["Sentinel-2 - ESA"], None, None)
    assert result.tier == TIER_OPERATOR_NAMED
    assert result.candidates[0].inside and result.candidates[0].distance_m == 0.0
    assert result.radius_m == 60.0, "inside counts even though every edge is far beyond the radius"


def test_leak_outside_a_polygon_measures_the_nearest_edge_not_the_centroid() -> None:
    """Polygon from 1 km to 4 km east; nearest edge is exactly 1000 m away."""
    field = square(0.0, 0.0, 1_000.0, 4_000.0, -2_000.0, 2_000.0)
    distance, inside = distance_to_geometry_m(Leak(0.0, 0.0), field, bbox_for_radius_m(0.0, 0.0, 5_000.0))
    assert not inside and distance == pytest.approx(1_000.0, abs=0.01)


def test_polygon_wider_than_the_envelope_is_clipped_before_projection() -> None:
    """At lat 60 a polygon whose near edge is a slanted 100 km chord 1-3 km
    east of the leak and whose far edge is 60 km away.

    What clipping guarantees: no vertex outside the search window is ever
    projected (every projected point stays inside the validated envelope),
    and the result still matches the geodesic truth for the near edge.

    What it does NOT change, recorded here so nobody relies on it: because the
    projection is affine in (lon, lat), a straight lon/lat edge stays straight
    in the plane, so the distance to a long chord is the same whether or not
    the chord is cut at the window. Clipping is a guard and a speed-up, not a
    correction; measured below to be identical to the millimetre.
    """
    lat0, lon0 = 60.0, 30.0
    near_south = offset_latlon(lat0, lon0, 1_000.0, -50_000.0)
    near_north = offset_latlon(lat0, lon0, 3_000.0, 50_000.0)
    far_north = offset_latlon(lat0, lon0, 60_000.0, 50_000.0)
    far_south = offset_latlon(lat0, lon0, 60_000.0, -50_000.0)
    polygon = shapely.Polygon([(lon, lat) for lat, lon in (near_south, near_north, far_north, far_south)])
    leak = Leak(lat0, lon0)
    radius = 5_000.0
    window = bbox_for_radius_m(lat0, lon0, radius)

    # Ground truth: the near edge is a straight line in lon/lat; sample it
    # densely and take the least geodesic distance to the leak.
    (lat_a, lon_a), (lat_b, lon_b) = near_south, near_north
    samples = [(lat_a + (lat_b - lat_a) * t, lon_a + (lon_b - lon_a) * t) for t in (i / 200_000 for i in range(200_001))]
    truth = min(GEOD.inv(lon0, lat0, lon, lat)[2] for lat, lon in samples if abs(lat - lat0) < 0.1)

    clipped_distance, inside = distance_to_geometry_m(leak, polygon, window)
    assert not inside
    assert abs(clipped_distance - truth) < GEO_ERROR_TOLERANCE_M

    # Every vertex the clipped path projects lies inside the window, while the
    # unclipped boundary reaches 60 km east and 50 km north/south of the leak.
    clipped_boundary = shapely.clip_by_rect(polygon.boundary, *window)
    for lon, lat in shapely.get_coordinates(clipped_boundary):
        assert window[0] - 1e-9 <= lon <= window[2] + 1e-9 and window[1] - 1e-9 <= lat <= window[3] + 1e-9
        x, y = to_local_xy(lat, lon, lat0, lon0)
        assert math.hypot(x, y) <= radius * math.sqrt(2) + 1.0
    farthest = max(math.hypot(*to_local_xy(lat, lon, lat0, lon0)) for lon, lat in shapely.get_coordinates(polygon.boundary))
    assert farthest > 70_000.0, "the unclipped boundary would project vertices far outside the envelope"

    from backend.app.attribution import _min_segment_distance_m
    naive = _min_segment_distance_m(leak, polygon.boundary)
    assert naive == pytest.approx(clipped_distance, abs=1e-3), "affine projection: same answer for a straight edge"


def test_polygon_entirely_outside_the_window_is_not_a_candidate() -> None:
    field = square(0.0, 0.0, 20_000.0, 30_000.0, -5_000.0, 5_000.0)
    distance, inside = distance_to_geometry_m(Leak(0.0, 0.0), field, bbox_for_radius_m(0.0, 0.0, 5_000.0))
    assert not inside and distance == math.inf
    index = AssetIndex.from_assets([asset("field", field, kind="field_outline")])
    assert generate_candidates(index, Leak(0.0, 0.0), 5_000.0) == []


# --- lines -----------------------------------------------------------------------------


def test_pipeline_distance_is_to_the_nearest_segment_after_clipping() -> None:
    """A north-south pipeline 800 m east of the leak, 100 km long."""
    south = offset_latlon(0.0, 0.0, 800.0, -50_000.0)
    north = offset_latlon(0.0, 0.0, 800.0, 50_000.0)
    line = shapely.LineString([(south[1], south[0]), (north[1], north[0])])
    distance, inside = distance_to_geometry_m(Leak(0.0, 0.0), line, bbox_for_radius_m(0.0, 0.0, 2_000.0))
    assert not inside and distance == pytest.approx(800.0, abs=0.01)


def test_traced_pipeline_outranks_a_placeholder_route_at_equal_distance() -> None:
    lat, lon = offset_latlon(0.0, 0.0, 500.0, 0.0)
    line = shapely.LineString([(lon, lat - 0.01), (lon, lat + 0.01)])
    index = AssetIndex.from_assets([
        asset("placeholder", line, kind="gas_pipeline", route_fidelity=0.3),
        asset("traced", line, kind="gas_pipeline", route_fidelity=1.02),
    ])
    ranked = generate_candidates(index, Leak(0.0, 0.0), 1_000.0, "Transmission Pipelines")
    assert [c.asset.asset_id for c in ranked] == ["traced", "placeholder"]
    assert ranked[0].route_traced and not ranked[1].route_traced


def test_asset_index_round_trips_through_parquet(tmp_path) -> None:
    import pandas as pd

    from etl.build_assets import ASSET_COLUMNS  # noqa: F401  (schema reference only)
    lat, lon = offset_latlon(0.0, 0.0, 0.0, 50.0)
    rows = pd.DataFrame([{
        "asset_id": "a", "physical_id": "a", "source_dataset": "t", "native_id": "a", "name": "A",
        "asset_kind": "well", "country": "X", "operator": "Op", "owner": None, "parent": None,
        "status": "operating", "geom_type": "Point", "lon": lon, "lat": lat,
        "geometry_wkb": shapely.to_wkb(point(lat, lon)), "n_vertices": 1, "scale": math.nan,
        "scale_unit": None, "route_fidelity": math.nan, "route_type": None, "route_accuracy": None,
        "placeholder_group": None, "placeholder_group_size": 1,
        "location_accuracy": "exact", "sigma_m": 0.0, "sigma_basis": "measured:outline_present",
    }])
    path = tmp_path / "assets.parquet"
    rows.to_parquet(path, index=False)
    index = AssetIndex.from_parquet(path)
    result = attribute(index, "TST_S_007", 0.0, 0.0, "Well head", ["Sentinel-2 - ESA"], None, None)
    assert result.tier == TIER_OPERATOR_NAMED and result.candidates[0].asset.operator == "Op"


def test_competing_assets_of_one_operator_count_as_one_party() -> None:
    wells = [asset(f"w{i}", point(*offset_latlon(0.0, 0.0, 10.0 * i, 0.0)), operator="One Operator") for i in range(1, 4)]
    result = attribute(AssetIndex.from_assets(wells), "TST_S_008", 0.0, 0.0, "Well head", ["Sentinel-2 - ESA"], None, None)
    assert result.ambiguity == AMBIGUITY_COMPETING and result.n_groups == 3 and result.n_parties == 1


# --- uncertainty budget: per-pair thresholds ------------------------------------------


def test_pair_threshold_combines_in_quadrature_and_caps_at_the_envelope() -> None:
    assert pair_threshold_m(60.0, 80.0) == pytest.approx(100.0)
    assert pair_threshold_m(60.0, 0.0) == 60.0
    assert pair_threshold_m(9_000.0, 9_000.0) == GEO_VALIDATED_SEPARATION_M
    with pytest.raises(ValueError):
        pair_threshold_m(-1.0, 0.0)


def test_each_pair_is_gated_by_its_own_threshold() -> None:
    """Plume sigma 60 m (Sentinel-2). An asset with sigma 80 m is matched to
    100 m; one with sigma 0 only to 60 m."""
    at = lambda m: point(*offset_latlon(0.0, 0.0, 0.0, m))  # noqa: E731
    index = AssetIndex.from_assets([
        asset("imprecise_in", at(95.0), sigma_m=80.0, sigma_basis="assumed:test"),
        asset("imprecise_out", at(105.0), sigma_m=80.0, sigma_basis="assumed:test"),
        asset("precise_out", at(70.0), sigma_m=0.0, sigma_basis="measured:test"),
        asset("precise_in", at(50.0), sigma_m=0.0, sigma_basis="measured:test"),
    ])
    found = generate_candidates(index, Leak(0.0, 0.0), 60.0)
    assert [c.asset.asset_id for c in found] == ["precise_in", "imprecise_in"]
    assert found[1].threshold_m == pytest.approx(100.0) and found[1].sigma_m == 80.0


def test_assumed_multiplier_scales_only_assumed_sigmas() -> None:
    at = lambda m: point(*offset_latlon(0.0, 0.0, 0.0, m))  # noqa: E731
    index = AssetIndex.from_assets([
        asset("assumed", at(95.0), sigma_m=80.0, sigma_basis="assumed:route_accuracy:high"),
        asset("measured", at(95.0), sigma_m=80.0, sigma_basis="measured:outline_eq_radius"),
    ])
    leak = Leak(0.0, 0.0)
    assert {c.asset.asset_id for c in generate_candidates(index, leak, 60.0)} == {"assumed", "measured"}
    halved = generate_candidates(index, leak, 60.0, scale=SigmaScale(assumed_multiplier=0.5))
    assert [c.asset.asset_id for c in halved] == ["measured"], "sqrt(60^2 + 40^2) = 72 m no longer reaches 95 m"
    assert halved[0].sigma_m == 80.0


def test_sigma_disabled_reproduces_the_plume_only_radius() -> None:
    at = lambda m: point(*offset_latlon(0.0, 0.0, 0.0, m))  # noqa: E731
    index = AssetIndex.from_assets([asset("a", at(95.0), sigma_m=80.0, sigma_basis="assumed:test")])
    assert generate_candidates(index, Leak(0.0, 0.0), 60.0, scale=SigmaScale(enabled=False)) == []
    assert len(generate_candidates(index, Leak(0.0, 0.0), 60.0)) == 1


def test_window_grows_to_the_largest_asset_sigma() -> None:
    """A low-accuracy pipeline 1.5 km away has sigma 2 km, so it must be
    examined and matched although the plume sigma is only 60 m."""
    south = offset_latlon(0.0, 0.0, 1_500.0, -20_000.0)
    north = offset_latlon(0.0, 0.0, 1_500.0, 20_000.0)
    line = shapely.LineString([(south[1], south[0]), (north[1], north[0])])
    index = AssetIndex.from_assets([
        asset("low_route", line, kind="gas_pipeline", sigma_m=2_000.0, sigma_basis="assumed:route_accuracy:low", route_fidelity=0.9),
        asset("well", point(*offset_latlon(0.0, 0.0, 0.0, 30.0)), sigma_m=0.0),
    ])
    found = generate_candidates(index, Leak(0.0, 0.0), 60.0, "Transmission Pipelines")
    assert [c.asset.asset_id for c in found] == ["low_route", "well"], "compatible kind first, then the well"
    assert found[0].distance_m == pytest.approx(1_500.0, abs=0.05) and found[0].threshold_m == pytest.approx(math.hypot(60.0, 2_000.0))


def test_extraction_percentile_scenario_requires_the_sidecar() -> None:
    index = AssetIndex.from_assets([asset("u", point(0.0, 0.0), kind="extraction_unit", sigma_m=3_000.0,
                                          sigma_basis="measured:outline_eq_radius", location_accuracy="exact")])
    with pytest.raises(ValueError, match="sidecar"):
        index.effective_sigmas_m(SigmaScale(extraction_percentile=90))
    index.sigma_percentiles = {"all": {"p90": 8_000.0}, "exact": {"p90": 7_500.0}}
    assert index.effective_sigmas_m(SigmaScale(extraction_percentile=90)).tolist() == [7_500.0]
    assert index.effective_sigmas_m(SigmaScale()).tolist() == [3_000.0]
