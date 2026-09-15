"""Tests for backend/app/geo.py.

Ground truth is pyproj.Geod(ellps="WGS84"), which solves the true geodesic on
the same ellipsoid whose constants geo.py uses. pyproj appears ONLY here, never
in the runtime path.

Two kinds of test live in this file, and they are different in kind:

1. Golden cases with values derived by hand from the closed-form definitions of
   M and N, then confirmed against pyproj. A change to these means the maths
   changed.
2. Envelope sweeps asserting our error against pyproj stays under
   GEO_ERROR_TOLERANCE_M inside the validated envelope, plus tests that DOCUMENT
   the behaviour outside it rather than pretending it is handled.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pyproj import Geod

from backend.app.config import (
    GEO_ERROR_TOLERANCE_M,
    GEO_VALIDATED_LATITUDE_LIMIT_DEG,
    GEO_VALIDATED_SEPARATION_M,
    GEO_WORST_CASE_ERROR_M,
    WGS84_ECCENTRICITY_SQUARED,
    WGS84_SEMI_MAJOR_AXIS_M,
)
from backend.app.geo import (
    meridional_radius_m,
    point_distance_m,
    point_to_segment_distance_m,
    prime_vertical_radius_m,
    to_local_xy,
)

GEOD = Geod(ellps="WGS84")


def geodesic_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """True WGS84 geodesic distance in metres, via pyproj. Ground truth."""
    return float(GEOD.inv(lon1, lat1, lon2, lat2)[2])


def geodesic_point_to_segment_m(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    samples: int = 4000,
) -> float:
    """Ground truth for point-to-segment: densify the geodesic a->b and minimise.

    Sampling resolution over a 10 km segment at 4000 points is about 2.5 m, so
    the sampled minimum can overshoot the true minimum by roughly a metre near a
    perpendicular foot. That is why this is used for envelope sweeps with a
    metres-scale tolerance, not for sub-metre golden assertions.
    """
    interior = GEOD.npts(a[1], a[0], b[1], b[0], samples)
    points = [(a[0], a[1])] + [(lat, lon) for lon, lat in interior] + [(b[0], b[1])]
    return min(geodesic_distance_m(p[0], p[1], lat, lon) for lat, lon in points)


# --- Radii of curvature: hand-derived golden values --------------------------


def test_meridional_radius_at_equator_is_a_times_one_minus_e_squared() -> None:
    """At the equator sin(lat) = 0, so M reduces to a(1 - e^2) exactly."""
    expected = WGS84_SEMI_MAJOR_AXIS_M * (1.0 - WGS84_ECCENTRICITY_SQUARED)
    assert meridional_radius_m(0.0) == pytest.approx(expected, abs=1e-9)
    assert meridional_radius_m(0.0) == pytest.approx(6335439.327293, abs=1e-5)


def test_prime_vertical_radius_at_equator_is_exactly_a() -> None:
    """At the equator N reduces to a exactly."""
    assert prime_vertical_radius_m(0.0) == pytest.approx(
        WGS84_SEMI_MAJOR_AXIS_M, abs=1e-9
    )


def test_prime_vertical_exceeds_meridional_away_from_the_poles() -> None:
    """N > M everywhere except at the poles, where they coincide."""
    for lat in (0.0, 15.0, 30.0, 45.0, 60.0, 75.0):
        assert prime_vertical_radius_m(lat) > meridional_radius_m(lat)
    assert prime_vertical_radius_m(90.0) == pytest.approx(
        meridional_radius_m(90.0), rel=1e-12
    )


def test_radii_are_symmetric_in_hemisphere() -> None:
    for lat in (10.0, 45.0, 68.4):
        assert meridional_radius_m(lat) == pytest.approx(meridional_radius_m(-lat))
        assert prime_vertical_radius_m(lat) == pytest.approx(
            prime_vertical_radius_m(-lat)
        )


# --- to_local_xy: hand-checked golden cases ---------------------------------


def test_origin_maps_to_zero() -> None:
    assert to_local_xy(41.5, -3.25, 41.5, -3.25) == (0.0, 0.0)


def test_pure_northward_offset_at_equator() -> None:
    """0.01 deg of latitude at the equator = M(0) * radians(0.01).

    Hand check: 6335439.327293 * 1.7453292519943296e-4 = 1105.742758 m.
    Along a meridian this is also the exact geodesic distance.
    """
    x_m, y_m = to_local_xy(0.01, 0.0, 0.0, 0.0)
    assert x_m == pytest.approx(0.0, abs=1e-9)
    assert y_m == pytest.approx(1105.742758, abs=1e-5)
    assert y_m == pytest.approx(geodesic_distance_m(0.0, 0.0, 0.01, 0.0), abs=1e-5)


def test_pure_eastward_offset_at_equator() -> None:
    """0.05 deg of longitude at the equator = a * radians(0.05).

    Hand check: 6378137.0 * 8.726646259971648e-4 = 5565.974540 m. Along the
    equator this is also the exact geodesic distance.
    """
    x_m, y_m = to_local_xy(0.0, 0.05, 0.0, 0.0)
    assert y_m == pytest.approx(0.0, abs=1e-9)
    assert x_m == pytest.approx(5565.974540, abs=1e-5)
    assert x_m == pytest.approx(geodesic_distance_m(0.0, 0.0, 0.0, 0.05), abs=1e-5)


def test_eastward_offset_at_latitude_sixty() -> None:
    """cos(60) = 0.5 exactly, so this is N(60) * 0.5 * radians(0.1).

    Hand check: 6394209.173848 * 0.5 * 1.7453292519943296e-3 = 5580.000157 m.
    """
    x_m, _ = to_local_xy(60.0, 0.1, 60.0, 0.0)
    assert x_m == pytest.approx(5580.000157, abs=1e-5)
    assert x_m == pytest.approx(
        geodesic_distance_m(60.0, 0.0, 60.0, 0.1), abs=1e-2
    )


def test_signs_are_east_positive_north_positive() -> None:
    x_east, _ = to_local_xy(0.0, 0.01, 0.0, 0.0)
    x_west, _ = to_local_xy(0.0, -0.01, 0.0, 0.0)
    _, y_north = to_local_xy(0.01, 0.0, 0.0, 0.0)
    _, y_south = to_local_xy(-0.01, 0.0, 0.0, 0.0)
    assert x_east > 0 and x_west < 0
    assert y_north > 0 and y_south < 0


def test_rejects_swapped_lat_lon_arguments() -> None:
    """A longitude passed as a latitude is the likeliest caller error."""
    with pytest.raises(ValueError, match="latitude"):
        to_local_xy(120.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="latitude"):
        to_local_xy(0.0, 0.0, -95.0, 0.0)


# --- Antimeridian and high latitude: documented behaviour -------------------


def test_antimeridian_is_handled_by_wrapping() -> None:
    """179.99E and 179.99W are 0.02 deg apart, not 359.98 deg apart.

    Hand check: a * radians(0.02) = 2226.389816 m at the equator.
    """
    x_m, y_m = to_local_xy(0.0, -179.99, 0.0, 179.99)
    assert x_m == pytest.approx(2226.389816, abs=1e-5)
    assert y_m == pytest.approx(0.0, abs=1e-9)
    assert x_m == pytest.approx(
        geodesic_distance_m(0.0, 179.99, 0.0, -179.99), abs=1e-5
    )


def test_antimeridian_distance_matches_geodesic_within_tolerance() -> None:
    for lat in (0.0, 30.0, 60.0, -45.0):
        ours = point_distance_m(lat, 179.995, lat, -179.995)
        truth = geodesic_distance_m(lat, 179.995, lat, -179.995)
        assert abs(ours - truth) < GEO_ERROR_TOLERANCE_M


def test_antimeridian_sign_convention() -> None:
    """Crossing westward gives a negative x, as for any other westward step."""
    x_m, _ = to_local_xy(0.0, 179.99, 0.0, -179.99)
    assert x_m < 0


def test_exactly_opposite_longitude_wraps_to_the_negative_branch() -> None:
    """A 180 deg difference is antipodal in longitude and the sign is arbitrary.

    Documenting, not endorsing: the projection is meaningless at this
    separation. We assert only that it does not blow up or wrap to +360.
    """
    x_m, _ = to_local_xy(0.0, 180.0, 0.0, 0.0)
    assert math.isfinite(x_m)
    assert x_m == pytest.approx(-WGS84_SEMI_MAJOR_AXIS_M * math.pi, rel=1e-12)


def test_accuracy_degrades_at_high_latitude_as_documented() -> None:
    """Outside the envelope the error grows. Recorded, not fixed.

    These are the numbers quoted in the geo.py module docstring, for a 10 km
    separation. If they change, the docstring is wrong.
    """
    expected_worst = {80.0: 17.14, 85.0: 34.73, 89.0: 181.71}
    # A 1 degree azimuth grid: the worst azimuth is sharp enough near the pole
    # that a coarser sweep understates the error.
    for lat, expected in expected_worst.items():
        worst = max(
            abs(
                point_distance_m(lat, 0.0, *_offset(lat, 0.0, float(azimuth), 10_000.0))
                - 10_000.0
            )
            for azimuth in range(0, 360)
        )
        assert worst == pytest.approx(expected, abs=0.5), lat
        assert worst > GEO_ERROR_TOLERANCE_M, (
            f"lat {lat} is outside the validated envelope and is expected to "
            "exceed the tolerance; if it no longer does, the envelope in "
            "config.py can be widened"
        )


def test_accuracy_degrades_with_separation_as_documented() -> None:
    """Beyond 10 km the error grows. These match the geo.py docstring."""
    for separation_m, expected in ((25_000.0, 18.79), (50_000.0, 75.38), (100_000.0, 303.33)):
        worst = max(
            abs(
                point_distance_m(
                    45.0, 0.0, *_offset(45.0, 0.0, float(azimuth), separation_m)
                )
                - separation_m
            )
            for azimuth in range(0, 360)
        )
        assert worst == pytest.approx(expected, abs=1.0), separation_m


def test_projection_is_degenerate_at_the_pole() -> None:
    """At a pole cos(lat0) is 0, so all longitude information is lost.

    This is a real limitation, asserted so nobody assumes polar support. MARS
    data reaches only 68.4 degrees, so it never arises in practice.
    """
    for lon in (0.0, 45.0, 90.0, 179.0):
        x_m, _ = to_local_xy(90.0, lon, 90.0, 0.0)
        assert abs(x_m) < 1e-6
    assert point_distance_m(90.0, 0.0, 90.0, 90.0) == pytest.approx(0.0, abs=1e-6)


def test_point_distance_is_slightly_asymmetric_as_documented() -> None:
    """The origin is the first argument, so argument order matters a little."""
    worst = 0.0
    for lat in range(-70, 71, 10):
        for azimuth in range(0, 360, 15):
            lat2, lon2 = _offset(float(lat), 0.0, azimuth, GEO_VALIDATED_SEPARATION_M)
            forward = point_distance_m(float(lat), 0.0, lat2, lon2)
            backward = point_distance_m(lat2, lon2, float(lat), 0.0)
            worst = max(worst, abs(forward - backward))
    assert worst > 0.0, "if this is exactly zero the projection changed"
    assert worst < 20.0, f"asymmetry grew beyond the documented 16.5 m: {worst}"


# --- Envelope sweep against pyproj ------------------------------------------


def _offset(
    lat: float, lon: float, azimuth_deg: float, distance_m: float
) -> tuple[float, float]:
    """Return the point `distance_m` from (lat, lon) along `azimuth_deg`."""
    lon2, lat2, _ = GEOD.fwd(lon, lat, azimuth_deg, distance_m)
    return lat2, lon2


def test_point_distance_within_tolerance_across_the_envelope() -> None:
    """The headline accuracy claim, swept over latitude, azimuth and separation."""
    worst = 0.0
    worst_at: tuple[float, float, float] | None = None
    latitudes = [
        lat / 2.0
        for lat in range(
            -int(GEO_VALIDATED_LATITUDE_LIMIT_DEG * 2),
            int(GEO_VALIDATED_LATITUDE_LIMIT_DEG * 2) + 1,
            10,
        )
    ]
    separations = [10.0, 100.0, 500.0, 1_000.0, 2_500.0, 5_000.0, 7_500.0, 10_000.0]
    for lat in latitudes:
        for separation_m in separations:
            for azimuth in range(0, 360, 5):
                lat2, lon2 = _offset(lat, 0.0, float(azimuth), separation_m)
                ours = point_distance_m(lat, 0.0, lat2, lon2)
                error = abs(ours - separation_m)
                if error > worst:
                    worst, worst_at = error, (lat, separation_m, float(azimuth))

    assert worst < GEO_ERROR_TOLERANCE_M, f"worst {worst:.4f} m at {worst_at}"
    assert worst == pytest.approx(GEO_WORST_CASE_ERROR_M, abs=0.1), (
        f"measured worst-case error is now {worst:.4f} m at {worst_at}; "
        "update GEO_WORST_CASE_ERROR_M in config.py and DECISIONS.md"
    )


def test_relative_error_stays_under_one_part_in_a_thousand() -> None:
    for lat in (0.0, 30.0, 45.0, 60.0, 70.0, -70.0, -33.3):
        for azimuth in range(0, 360, 15):
            lat2, lon2 = _offset(lat, 0.0, float(azimuth), GEO_VALIDATED_SEPARATION_M)
            ours = point_distance_m(lat, 0.0, lat2, lon2)
            truth = geodesic_distance_m(lat, 0.0, lat2, lon2)
            assert abs(ours - truth) / truth < 1e-3


def test_distance_is_zero_for_identical_points() -> None:
    assert point_distance_m(31.87393, 6.20307, 31.87393, 6.20307) == 0.0


def test_distance_matches_geodesic_at_real_mars_coordinates() -> None:
    """Two real sources from the 2026-09-15 snapshot, offset by short hops."""
    for lat, lon in ((29.19107, 21.4286), (8.40613, -64.79417), (67.706, 30.0)):
        for azimuth in (0.0, 45.0, 137.0, 271.0):
            lat2, lon2 = _offset(lat, lon, azimuth, 3_500.0)
            ours = point_distance_m(lat, lon, lat2, lon2)
            truth = geodesic_distance_m(lat, lon, lat2, lon2)
            assert abs(ours - truth) < GEO_ERROR_TOLERANCE_M


# --- point_to_segment_distance_m --------------------------------------------

SEGMENT_A = (0.0, 0.0)
SEGMENT_B = (0.0, 0.1)


def test_nearest_approach_is_perpendicular_to_the_interior() -> None:
    """p sits due north of the segment midpoint, so the foot is interior.

    Hand check: the answer is 0.005 deg of latitude at the equator,
    M(0) * radians(0.005) = 552.871379 m. Note this is strictly less than the
    distance to either endpoint (5593.37 m), which is what proves the interior
    case is actually being exercised.
    """
    p = (0.005, 0.05)
    distance = point_to_segment_distance_m(p, SEGMENT_A, SEGMENT_B)
    assert distance == pytest.approx(552.871379, abs=1e-5)
    assert distance < point_distance_m(*p, *SEGMENT_A)
    assert distance < point_distance_m(*p, *SEGMENT_B)
    assert distance == pytest.approx(
        geodesic_point_to_segment_m(p, SEGMENT_A, SEGMENT_B), abs=1.0
    )


def test_nearest_approach_is_the_endpoint_b() -> None:
    """p lies beyond b, so the answer is the distance to b itself.

    Hand check: 0.05 deg of longitude east of b and 0.005 deg of latitude north,
    hypot(5565.974540, 552.871379) = 5593.365633 m.
    """
    p = (0.005, 0.15)
    distance = point_to_segment_distance_m(p, SEGMENT_A, SEGMENT_B)
    assert distance == pytest.approx(5593.365633, abs=1e-5)
    assert distance == pytest.approx(point_distance_m(*p, *SEGMENT_B), abs=1e-9)
    assert distance < point_distance_m(*p, *SEGMENT_A)


def test_nearest_approach_is_the_endpoint_a() -> None:
    """The mirror image, clamping at the other end of the segment."""
    p = (0.005, -0.05)
    distance = point_to_segment_distance_m(p, SEGMENT_A, SEGMENT_B)
    assert distance == pytest.approx(5593.365633, abs=1e-5)
    assert distance == pytest.approx(point_distance_m(*p, *SEGMENT_A), abs=1e-9)
    assert distance < point_distance_m(*p, *SEGMENT_B)


def test_point_on_the_segment_is_zero() -> None:
    assert point_to_segment_distance_m((0.0, 0.05), SEGMENT_A, SEGMENT_B) == (
        pytest.approx(0.0, abs=1e-6)
    )


def test_point_at_an_endpoint_is_zero() -> None:
    assert point_to_segment_distance_m(SEGMENT_A, SEGMENT_A, SEGMENT_B) == (
        pytest.approx(0.0, abs=1e-9)
    )


def test_degenerate_segment_reduces_to_point_distance() -> None:
    """a == b must not divide by zero."""
    p = (0.0, 0.01)
    degenerate = point_to_segment_distance_m(p, SEGMENT_A, SEGMENT_A)
    assert degenerate == pytest.approx(point_distance_m(*p, *SEGMENT_A), abs=1e-9)
    assert degenerate == pytest.approx(1113.194908, abs=1e-5)
    assert degenerate == pytest.approx(
        geodesic_distance_m(*p, *SEGMENT_A), abs=1e-5
    )


def test_degenerate_segment_at_the_point_itself_is_zero() -> None:
    assert point_to_segment_distance_m(SEGMENT_A, SEGMENT_A, SEGMENT_A) == 0.0


def test_segment_endpoint_order_does_not_matter() -> None:
    for p in ((0.005, 0.05), (0.005, 0.15), (0.005, -0.05), (0.02, 0.07)):
        forward = point_to_segment_distance_m(p, SEGMENT_A, SEGMENT_B)
        reversed_ = point_to_segment_distance_m(p, SEGMENT_B, SEGMENT_A)
        assert forward == pytest.approx(reversed_, abs=1e-9)


def test_segment_distance_never_exceeds_endpoint_distances() -> None:
    for p in ((0.005, 0.05), (0.02, -0.01), (-0.03, 0.12), (0.001, 0.099)):
        distance = point_to_segment_distance_m(p, SEGMENT_A, SEGMENT_B)
        assert distance <= point_distance_m(*p, *SEGMENT_A) + 1e-9
        assert distance <= point_distance_m(*p, *SEGMENT_B) + 1e-9


def test_segment_distance_within_tolerance_across_the_envelope() -> None:
    """Sweep against densified-geodesic ground truth."""
    worst = 0.0
    worst_at: tuple[float, float, float] | None = None
    for lat in (-70.0, -45.0, -20.0, 0.0, 20.0, 45.0, 68.4, 70.0):
        for azimuth_a in (0.0, 73.0, 155.0, 249.0, 318.0):
            for offset_m, length_m in ((250.0, 1_000.0), (5_000.0, 4_000.0), (9_800.0, 3_000.0)):
                a = _offset(lat, 0.0, azimuth_a, offset_m)
                b = _offset(a[0], a[1], (azimuth_a + 97.0) % 360.0, length_m)
                ours = point_to_segment_distance_m((lat, 0.0), a, b)
                truth = geodesic_point_to_segment_m((lat, 0.0), a, b)
                error = abs(ours - truth)
                if error > worst:
                    worst, worst_at = error, (lat, offset_m, length_m)
    assert worst < GEO_ERROR_TOLERANCE_M, f"worst {worst:.4f} m at {worst_at}"


def test_segment_matches_a_real_pipeline_shaped_case() -> None:
    """A 6 km segment passing about 800 m from a leak, at a real MARS latitude."""
    leak = (31.87393, 6.20307)
    a = _offset(leak[0], leak[1], 0.0, 800.0)
    b = _offset(a[0], a[1], 90.0, 6_000.0)
    ours = point_to_segment_distance_m(leak, a, b)
    assert ours == pytest.approx(800.0, abs=GEO_ERROR_TOLERANCE_M)
    assert ours == pytest.approx(
        geodesic_point_to_segment_m(leak, a, b), abs=GEO_ERROR_TOLERANCE_M
    )


def test_runtime_path_does_not_import_geopandas_or_pyproj() -> None:
    """Architecture guard: backend/app/ must import neither geopandas nor pyproj.

    Static scan of the source, because a sys.modules check would pass simply
    because nothing happened to import them yet in this process. pyproj is
    allowed in backend/tests/, which is why only backend/app/ is scanned.
    """
    import ast

    app_dir = Path(__file__).resolve().parents[1] / "app"
    forbidden = {"geopandas", "pyogrio", "pyproj", "fiona"}
    offenders: list[str] = []
    for source_file in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(source_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".")[0] in forbidden:
                    offenders.append(f"{source_file.name}:{node.lineno} {name}")
    assert not offenders, f"forbidden imports in backend/app/: {offenders}"
