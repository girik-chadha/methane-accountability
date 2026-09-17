"""Local planar geometry for matching leaks against nearby infrastructure.

All functions here are pure and operate on WGS84 degrees, returning metres.

The projection
--------------
A local equirectangular (equidistant cylindrical) projection about an origin
point, which for our purposes is the leak. Latitude and longitude differences
from the origin are scaled by the ellipsoid's local radii of curvature AT THE
ORIGIN LATITUDE:

    x_m = N(lat0) * cos(lat0) * (lon - lon0)
    y_m = M(lat0) * (lat - lat0)

where M is the meridional radius of curvature and N the prime vertical radius.
Both are constants once the origin is fixed, so the map is affine: straight
lines stay straight and the planar point-to-segment formula below is valid.

Why this is admissible: matching radii in this project are under about 10 km,
where the distortion is small and measured. Inside the validated envelope
(|lat| <= 70 degrees, separations <= 10 km) the worst-case error against true
WGS84 geodesic distance is 8.28 m, or 0.083%. Outside it, accuracy degrades
smoothly and these functions still return an answer rather than raising; see
the limitations below and the tests, which document the real behaviour.

Limitations, measured not assumed
---------------------------------
- Accuracy falls off with latitude as meridians converge: about 17 m at 80
  degrees, 35 m at 85, and 182 m at 89, for a 10 km separation.
- Accuracy falls off with separation: about 19 m at 25 km and 303 m at 100 km,
  at mid-latitude. These functions are not intended for such distances.
- At a pole the projection is degenerate: cos(lat0) is zero, so every longitude
  collapses to x = 0 and longitude information is lost entirely.
- point_distance_m is very slightly ASYMMETRIC, because the projection origin
  is its first argument. Worst measured asymmetry inside the envelope is 16.5 m
  over a 10 km separation. Where symmetry matters, fix the origin explicitly
  (in this project, the leak) rather than relying on the argument order.

pyproj is deliberately NOT imported here. It appears only in the tests, as the
independent ground truth we validate this module against. geopandas must never
be imported anywhere under backend/.
"""

from __future__ import annotations

import math

from backend.app.config import (
    WGS84_ECCENTRICITY_SQUARED,
    WGS84_SEMI_MAJOR_AXIS_M,
)

# A WGS84 geographic point as (latitude_degrees, longitude_degrees).
LatLon = tuple[float, float]

_MAX_LATITUDE_DEG = 90.0
_HALF_TURN_DEG = 180.0
_FULL_TURN_DEG = 360.0


def _check_latitude(lat_deg: float, name: str) -> None:
    """Raise if a latitude is outside [-90, 90] degrees."""
    if not -_MAX_LATITUDE_DEG <= lat_deg <= _MAX_LATITUDE_DEG:
        raise ValueError(
            f"{name} must be a latitude in [-90, 90] degrees, got {lat_deg!r}. "
            "Check for a lat/lon argument swap."
        )


def _wrapped_longitude_delta_deg(lon_deg: float, lon0_deg: float) -> float:
    """Return lon - lon0 wrapped to (-180, 180] degrees.

    Without this, a pair straddling the antimeridian would appear to be almost
    a full turn apart instead of adjacent.
    """
    return (
        (lon_deg - lon0_deg + _HALF_TURN_DEG) % _FULL_TURN_DEG
    ) - _HALF_TURN_DEG


def meridional_radius_m(lat_deg: float) -> float:
    """Return the WGS84 meridional radius of curvature M at `lat_deg`, in metres.

    M is the local radius governing north-south distance:
        M = a(1 - e^2) / (1 - e^2 sin^2(lat))^(3/2)
    """
    _check_latitude(lat_deg, "lat_deg")
    sin_squared = math.sin(math.radians(lat_deg)) ** 2
    return (
        WGS84_SEMI_MAJOR_AXIS_M
        * (1.0 - WGS84_ECCENTRICITY_SQUARED)
        / (1.0 - WGS84_ECCENTRICITY_SQUARED * sin_squared) ** 1.5
    )


def prime_vertical_radius_m(lat_deg: float) -> float:
    """Return the WGS84 prime vertical radius of curvature N at `lat_deg`, in metres.

    N is the local radius governing east-west distance, before the cos(lat)
    convergence factor is applied:
        N = a / sqrt(1 - e^2 sin^2(lat))
    """
    _check_latitude(lat_deg, "lat_deg")
    sin_squared = math.sin(math.radians(lat_deg)) ** 2
    return WGS84_SEMI_MAJOR_AXIS_M / math.sqrt(
        1.0 - WGS84_ECCENTRICITY_SQUARED * sin_squared
    )


def to_local_xy(
    lat: float, lon: float, lat0: float, lon0: float
) -> tuple[float, float]:
    """Project (lat, lon) into a local plane about (lat0, lon0).

    Returns (x_m, y_m) in METRES: x_m is eastward positive, y_m is northward
    positive, and the origin (lat0, lon0) maps to (0.0, 0.0).

    Longitude differences are wrapped, so the antimeridian is handled
    correctly. Accuracy is stated in the module docstring; the projection does
    not raise outside its validated envelope.
    """
    _check_latitude(lat, "lat")
    _check_latitude(lat0, "lat0")

    delta_lon_rad = math.radians(_wrapped_longitude_delta_deg(lon, lon0))
    delta_lat_rad = math.radians(lat - lat0)
    lat0_rad = math.radians(lat0)

    x_m = prime_vertical_radius_m(lat0) * math.cos(lat0_rad) * delta_lon_rad
    y_m = meridional_radius_m(lat0) * delta_lat_rad
    return x_m, y_m


def point_distance_m(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Return the distance from point 1 to point 2, in METRES.

    Computed in the local plane about point 1. Very slightly asymmetric in its
    arguments; see the module docstring.
    """
    x_m, y_m = to_local_xy(lat2, lon2, lat1, lon1)
    return math.hypot(x_m, y_m)


def point_to_segment_distance_m(p: LatLon, a: LatLon, b: LatLon) -> float:
    """Return the distance from point `p` to the segment `a`-`b`, in METRES.

    Each argument is a (latitude_degrees, longitude_degrees) pair. The segment
    is treated as a straight line in the local plane about `p`, which is what
    lets a pipeline be matched as a linestring rather than only as its
    endpoints.

    The nearest approach may be perpendicular to the segment's interior or at
    either endpoint; both are handled by clamping the projection parameter to
    [0, 1]. A degenerate segment where `a == b` reduces to the point-to-point
    distance rather than dividing by zero.
    """
    origin_lat, origin_lon = p
    ax_m, ay_m = to_local_xy(a[0], a[1], origin_lat, origin_lon)
    bx_m, by_m = to_local_xy(b[0], b[1], origin_lat, origin_lon)

    # p is the projection origin, so it sits at (0, 0) and the vector from a to
    # p is simply (-ax_m, -ay_m).
    seg_x_m = bx_m - ax_m
    seg_y_m = by_m - ay_m
    length_squared = seg_x_m * seg_x_m + seg_y_m * seg_y_m

    if length_squared == 0.0:
        # Degenerate segment: a and b are the same point.
        return math.hypot(ax_m, ay_m)

    # Position of the nearest approach along a->b, clamped to the segment.
    t = -(ax_m * seg_x_m + ay_m * seg_y_m) / length_squared
    t = max(0.0, min(1.0, t))

    nearest_x_m = ax_m + t * seg_x_m
    nearest_y_m = ay_m + t * seg_y_m
    return math.hypot(nearest_x_m, nearest_y_m)


def offset_latlon(
    lat0: float, lon0: float, dx_m: float, dy_m: float
) -> tuple[float, float]:
    """Move (lat0, lon0) by dx_m east and dy_m north; return (lat, lon).

    The exact inverse of to_local_xy about the same origin, using the same
    local radii of curvature, so to_local_xy(*offset_latlon(...), lat0, lon0)
    returns (dx_m, dy_m) to floating-point precision. Accuracy is that of the
    projection itself: intended for offsets inside the validated envelope.
    Longitude is wrapped back into (-180, 180].
    """
    _check_latitude(lat0, "lat0")
    lat0_rad = math.radians(lat0)
    lat = lat0 + math.degrees(dy_m / meridional_radius_m(lat0))
    lon = lon0 + math.degrees(dx_m / (prime_vertical_radius_m(lat0) * math.cos(lat0_rad)))
    return lat, _wrapped_longitude_delta_deg(lon, 0.0)


def bbox_for_radius_m(
    lat0: float, lon0: float, radius_m: float
) -> tuple[float, float, float, float]:
    """Lon/lat bounding box (minlon, minlat, maxlon, maxlat) of the disc of
    `radius_m` about (lat0, lon0), for indexing geometries stored in degrees.

    Built from offset_latlon at the four cardinal points, so it inherits the
    projection's accuracy and is exact in the sense that every point within
    radius_m in the local plane lies inside it. Callers must still measure the
    true distance to whatever the box returns.
    """
    if radius_m < 0.0:
        raise ValueError(f"radius_m must be non-negative, got {radius_m!r}")
    _, min_lon = offset_latlon(lat0, lon0, -radius_m, 0.0)
    _, max_lon = offset_latlon(lat0, lon0, radius_m, 0.0)
    min_lat, _ = offset_latlon(lat0, lon0, 0.0, -radius_m)
    max_lat, _ = offset_latlon(lat0, lon0, 0.0, radius_m)
    return min_lon, min_lat, max_lon, max_lat
