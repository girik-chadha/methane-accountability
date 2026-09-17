"""Attribute a MARS source to a SET of candidate infrastructure assets.

Runtime path: shapely, numpy and pandas only. No geopandas, pyogrio, pyproj
or fiona (backend/tests/test_geo.py enforces this by static scan).

Pipeline for one source:
  1. search radius from the detecting satellites' resolutions (config),
  2. search centre: the published location, optionally back-projected upwind
     (MARS_UPWIND_OFFSET_M is measured to be 0 for MARS, see config),
  3. candidate generation from an STRtree over assets stored in lon/lat,
     with every distance measured in a local plane whose ORIGIN IS THE LEAK,
     lines and polygon boundaries clipped to a window about the leak first,
  4. ordering by (kind compatibility, route fidelity, distance, scale) and
     tier assignment: OPERATOR_NAMED / ASSET_ONLY / UNMAPPED.

Attribution is probabilistic. Every result carries the full candidate set, an
ambiguity flag, and the path taken; nothing here states a match as fact.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree

from backend.app.config import (
    ASSETS_PARQUET_PATH,
    ASSETS_SIGMA_SIDECAR_PATH,
    EXTRACTION_SIGMA_PERCENTILE,
    EXTRACTION_SIGMA_SENSITIVITY_PERCENTILES,
    SIGMA_BASIS_ASSUMED_PREFIX,
    SIGMA_SENSITIVITY_MULTIPLIERS,
    FEEDBACK_NO,
    GEO_VALIDATED_SEPARATION_M,
    MARS_DB_PATH,
    MARS_UPWIND_OFFSET_M,
    MAX_CANDIDATES_LISTED,
    PLUMES_TABLE,
    RADIUS_SENSITIVITY_MULTIPLIERS,
    ROUTE_FIDELITY_TRACED_RANGE,
    SATELLITE_RESOLUTION_M,
    SEARCH_RADIUS_PIXELS,
    SOURCE_TYPE_COMPATIBLE_KINDS,
    SOURCES_TABLE,
    TIER_ASSET_ONLY,
    TIER_OPERATOR_NAMED,
    TIER_UNMAPPED,
)
from backend.app.geo import (
    bbox_for_radius_m,
    offset_latlon,
    point_distance_m,
    point_to_segment_distance_m,
)

MODE_WIND_CORRECTED = "wind_corrected"
MODE_SYMMETRIC_NO_WIND = "symmetric_no_wind"
MODE_SYMMETRIC_ZERO_OFFSET = "symmetric_zero_offset"

AMBIGUITY_NONE = "none"
AMBIGUITY_PLACEHOLDER_GROUP = "placeholder_group"
AMBIGUITY_COMPETING = "competing"

# Row layout of the columnar asset table held by AssetIndex.
_TABLE_COLUMNS = (
    "asset_id", "physical_id", "source_dataset", "name", "asset_kind", "country",
    "operator", "owner", "parent", "status", "placeholder_group",
    "placeholder_group_size", "route_fidelity", "scale",
    "location_accuracy", "sigma_m", "sigma_basis",
)
BASIS_OUTLINE_EQ_RADIUS = "measured:outline_eq_radius"
SIGMA_CLASS_FALLBACK = "all"


class UnsourcedSatelliteError(ValueError):
    """A detecting satellite has no sourced resolution in config."""


# --- data ------------------------------------------------------------------------


@dataclass(frozen=True)
class Leak:
    """The projection origin. Always the (possibly back-projected) source."""

    lat: float
    lon: float


@dataclass(frozen=True)
class Asset:
    asset_id: str
    physical_id: str
    source_dataset: str
    name: str | None
    asset_kind: str
    country: str | None
    operator: str | None
    owner: str | None
    parent: str | None
    status: str | None
    geometry: shapely.Geometry
    placeholder_group: str | None = None
    placeholder_group_size: int = 1
    route_fidelity: float = math.nan
    scale: float = math.nan
    location_accuracy: str | None = None
    sigma_m: float = 0.0
    sigma_basis: str = "measured:test"

    @property
    def named_party(self) -> str | None:
        """Operator, else parent, else owner; None when nobody is recorded."""
        return self.operator or self.parent or self.owner

    @property
    def group_key(self) -> str:
        """Identity for ambiguity counting: placeholder group, else physical id."""
        return self.placeholder_group or self.physical_id


@dataclass(frozen=True)
class SigmaScale:
    """One scenario of the uncertainty budget.

    enabled=False zeroes every asset sigma, reproducing the slice-4B
    plume-only radius. assumed_multiplier scales only sigmas whose basis
    starts with "assumed:"; measured and sourced sigmas never move.
    extraction_percentile swaps the measured outline percentile used for
    extraction units without an outline. plume_multiplier scales the plume
    sigma (the SEARCH_RADIUS_PIXELS sensitivity).
    """

    enabled: bool = True
    assumed_multiplier: float = 1.0
    extraction_percentile: int = EXTRACTION_SIGMA_PERCENTILE
    plume_multiplier: float = 1.0


def pair_threshold_m(plume_sigma_m: float, asset_sigma_m: float) -> float:
    """sqrt(plume^2 + asset^2), capped at the validated projection envelope."""
    if plume_sigma_m < 0.0 or asset_sigma_m < 0.0:
        raise ValueError("sigmas must be non-negative")
    return min(math.hypot(plume_sigma_m, asset_sigma_m), GEO_VALIDATED_SEPARATION_M)


@dataclass(frozen=True)
class Candidate:
    asset: Asset
    distance_m: float
    inside: bool
    compatible: bool
    route_traced: bool
    sigma_m: float = 0.0
    threshold_m: float = math.nan

    def sort_key(self) -> tuple[int, int, float, float]:
        """Compatible first, traced routes first, then nearest, then largest."""
        scale = self.asset.scale if not math.isnan(self.asset.scale) else 0.0
        return (0 if self.compatible else 1, 0 if self.route_traced else 1, self.distance_m, -scale)


@dataclass(frozen=True)
class SearchCentre:
    lat: float
    lon: float
    mode: str
    shift_m: float


@dataclass
class Attribution:
    source_name: str
    tier: str
    leak: Leak
    published_lat: float
    published_lon: float
    search_mode: str
    shift_m: float
    radius_m: float
    radius_capped: bool
    satellites: tuple[str, ...]
    n_candidates: int
    n_groups: int
    n_parties: int
    ambiguity: str
    candidates: list[Candidate] = field(default_factory=list)
    plume_sigma_m: float = 0.0
    window_m: float = 0.0

    @property
    def top(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


# --- asset index -----------------------------------------------------------------


class AssetIndex:
    """Columnar asset table plus an STRtree over lon/lat geometries."""

    def __init__(self, table: pd.DataFrame, geometries: np.ndarray) -> None:
        if len(table) != len(geometries):
            raise ValueError("table and geometries differ in length")
        missing = [c for c in _TABLE_COLUMNS if c not in table.columns]
        if missing:
            raise ValueError(f"asset table missing columns {missing}")
        self.table = table.reset_index(drop=True)
        self.geometries = geometries
        self.tree = STRtree(geometries)
        self.sigma_percentiles: dict[str, dict[str, float]] | None = None
        self._sigma_cache: dict[SigmaScale, np.ndarray] = {}

    @classmethod
    def from_parquet(cls, path: Path = ASSETS_PARQUET_PATH) -> AssetIndex:
        if not path.exists():
            raise FileNotFoundError(f"No asset table at {path}; run etl/build_assets.py")
        frame = pd.read_parquet(path)
        geometries = shapely.from_wkb(frame["geometry_wkb"].to_numpy())
        index = cls(frame.drop(columns=["geometry_wkb"]), geometries)
        sidecar = path.with_name(ASSETS_SIGMA_SIDECAR_PATH.name)
        if sidecar.exists():
            index.sigma_percentiles = json.loads(sidecar.read_text())["extraction_eq_radius_m"]
        return index

    @classmethod
    def from_assets(cls, assets: Sequence[Asset]) -> AssetIndex:
        rows = [{c: getattr(a, c) for c in _TABLE_COLUMNS} for a in assets]
        table = pd.DataFrame(rows, columns=list(_TABLE_COLUMNS))
        return cls(table, np.array([a.geometry for a in assets], dtype=object))

    def __len__(self) -> int:
        return len(self.table)

    def asset(self, index: int) -> Asset:
        row = self.table.iloc[index]

        def text(column: str) -> str | None:
            value = row[column]
            return None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)

        def number(column: str) -> float:
            value = row[column]
            return math.nan if value is None or pd.isna(value) else float(value)

        return Asset(
            asset_id=str(row["asset_id"]), physical_id=str(row["physical_id"]),
            source_dataset=str(row["source_dataset"]), name=text("name"),
            asset_kind=str(row["asset_kind"]), country=text("country"),
            operator=text("operator"), owner=text("owner"), parent=text("parent"),
            status=text("status"), geometry=self.geometries[index],
            placeholder_group=text("placeholder_group"),
            placeholder_group_size=int(row["placeholder_group_size"]) if not pd.isna(row["placeholder_group_size"]) else 1,
            route_fidelity=number("route_fidelity"), scale=number("scale"),
        )

    def effective_sigmas_m(self, scale: SigmaScale) -> np.ndarray:
        """Per-asset sigma under a scenario, vectorised and cached."""
        cached = self._sigma_cache.get(scale)
        if cached is not None:
            return cached
        sigma = self.table["sigma_m"].to_numpy(dtype=float).copy()
        if not scale.enabled:
            sigma[:] = 0.0
        else:
            basis_series = self.table["sigma_basis"].astype(str)
            basis = basis_series.to_numpy()
            assumed = basis_series.str.startswith(SIGMA_BASIS_ASSUMED_PREFIX).to_numpy(dtype=bool)
            sigma[assumed] *= scale.assumed_multiplier
            if scale.extraction_percentile != EXTRACTION_SIGMA_PERCENTILE:
                if self.sigma_percentiles is None:
                    raise ValueError("no sigma sidecar loaded; cannot change the extraction percentile")
                measured = basis == BASIS_OUTLINE_EQ_RADIUS
                accuracy = self.table["location_accuracy"].astype(object).to_numpy()
                for i in np.flatnonzero(measured):
                    cls = accuracy[i] if accuracy[i] in self.sigma_percentiles else SIGMA_CLASS_FALLBACK
                    sigma[i] = self.sigma_percentiles[cls][f"p{scale.extraction_percentile}"]
        if np.isnan(sigma).any() or (sigma < 0).any():
            raise ValueError("asset sigma must be non-negative and present for every asset")
        self._sigma_cache[scale] = sigma
        return sigma

    def query_window(self, bbox: tuple[float, float, float, float]) -> np.ndarray:
        """Indices of assets whose envelope intersects the lon/lat box.

        A box that straddles the antimeridian (min_lon > max_lon) is split.
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        if min_lon <= max_lon:
            return self.tree.query(shapely.box(min_lon, min_lat, max_lon, max_lat))
        west = self.tree.query(shapely.box(min_lon, min_lat, 180.0, max_lat))
        east = self.tree.query(shapely.box(-180.0, min_lat, max_lon, max_lat))
        return np.unique(np.concatenate([west, east]))


# --- radius and centre -----------------------------------------------------------


def search_radius_m(satellites: Iterable[str]) -> float:
    """SEARCH_RADIUS_PIXELS times the finest sourced resolution among the
    detecting satellites, in metres. Raises if any satellite is unsourced."""
    names = sorted(set(satellites))
    if not names:
        raise ValueError("no detecting satellites given; cannot derive a radius")
    unsourced = [s for s in names if s not in SATELLITE_RESOLUTION_M]
    if unsourced:
        raise UnsourcedSatelliteError(
            f"no sourced spatial resolution for {unsourced}; add it to "
            "SATELLITE_RESOLUTION_M in backend/app/config.py with a citation"
        )
    return SEARCH_RADIUS_PIXELS * min(SATELLITE_RESOLUTION_M[s] for s in names)


def search_centre(
    lat: float, lon: float, wind_u: float | None, wind_v: float | None,
    upwind_offset_m: float = MARS_UPWIND_OFFSET_M,
) -> SearchCentre:
    """Back-project the published point upwind by `upwind_offset_m`.

    wind_u/wind_v are eastward/northward wind components (m/s, MARS
    convention). A plume centroid lies DOWNWIND of its source, so the source
    estimate moves against the wind: shift = -offset * (u, v) / |(u, v)|.
    With no usable wind, or a zero offset, the published point is used and
    the mode says which happened.
    """
    if upwind_offset_m < 0.0:
        raise ValueError("upwind_offset_m must be non-negative")
    has_wind = (
        wind_u is not None and wind_v is not None
        and not math.isnan(wind_u) and not math.isnan(wind_v)
        and math.hypot(wind_u, wind_v) > 0.0
    )
    if not has_wind:
        return SearchCentre(lat, lon, MODE_SYMMETRIC_NO_WIND, 0.0)
    if upwind_offset_m == 0.0:
        return SearchCentre(lat, lon, MODE_SYMMETRIC_ZERO_OFFSET, 0.0)
    speed = math.hypot(wind_u, wind_v)
    dx_m = -upwind_offset_m * wind_u / speed
    dy_m = -upwind_offset_m * wind_v / speed
    new_lat, new_lon = offset_latlon(lat, lon, dx_m, dy_m)
    return SearchCentre(new_lat, new_lon, MODE_WIND_CORRECTED, upwind_offset_m)


# --- distances, origin always the leak -------------------------------------------


def _segments(geometry: shapely.Geometry) -> Iterable[tuple[tuple[float, float], tuple[float, float]]]:
    """Consecutive (lat, lon) vertex pairs of every LineString part."""
    for part in shapely.get_parts(geometry):
        coords = shapely.get_coordinates(part)
        for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
            yield (lat1, lon1), (lat2, lon2)


def _min_segment_distance_m(leak: Leak, lines: shapely.Geometry) -> float:
    origin = (leak.lat, leak.lon)
    best = math.inf
    for a, b in _segments(lines):
        best = min(best, point_to_segment_distance_m(origin, a, b))
    return best


def distance_to_geometry_m(
    leak: Leak, geometry: shapely.Geometry, window: tuple[float, float, float, float]
) -> tuple[float, bool]:
    """Distance from the leak to a geometry, in metres, and whether the leak
    lies inside it (polygons only; distance is then 0).

    Points: point_distance_m with the LEAK as first argument, i.e. the
    projection origin. Lines: clipped to `window` (a lon/lat box about the
    leak) before any vertex is projected, then the minimum over segments.
    Polygons: containment is tested on the unclipped polygon; otherwise the
    polygon's BOUNDARY is clipped to the window and measured like a line, so
    no artificial window edge is ever measured and no vertex beyond the
    window is ever projected. Infinity means nothing lies inside the window.
    """
    if geometry is None or geometry.is_empty:
        return math.inf, False
    kind = geometry.geom_type
    if kind == "Point":
        return point_distance_m(leak.lat, leak.lon, geometry.y, geometry.x), False
    if kind == "MultiPoint":
        return min(point_distance_m(leak.lat, leak.lon, p.y, p.x) for p in geometry.geoms), False
    if kind in ("LineString", "MultiLineString"):
        clipped = shapely.clip_by_rect(geometry, *window)
        return _min_segment_distance_m(leak, clipped), False
    if kind in ("Polygon", "MultiPolygon"):
        if shapely.contains(geometry, shapely.Point(leak.lon, leak.lat)):
            return 0.0, True
        boundary = shapely.clip_by_rect(geometry.boundary, *window)
        return _min_segment_distance_m(leak, boundary), False
    raise TypeError(f"unsupported geometry type {kind!r}")


# --- candidates ------------------------------------------------------------------


def _is_compatible(source_type: str | None, asset_kind: str) -> bool:
    allowed = SOURCE_TYPE_COMPATIBLE_KINDS.get(source_type or "")
    return True if allowed is None else asset_kind in allowed


def _route_traced(asset: Asset) -> bool:
    if asset.asset_kind != "gas_pipeline":
        return True
    low, high = ROUTE_FIDELITY_TRACED_RANGE
    return not math.isnan(asset.route_fidelity) and low <= asset.route_fidelity <= high


def generate_candidates(
    index: AssetIndex, leak: Leak, plume_sigma_m: float, source_type: str | None = None,
    scale: SigmaScale = SigmaScale(),
) -> list[Candidate]:
    """Every asset whose distance is within its own pair threshold
    sqrt(plume_sigma^2 + asset_sigma^2) (or which contains the leak), ordered.

    The STRtree window is sized by the largest threshold any asset could have,
    so an imprecise asset (large sigma) further away is still examined. The
    ordering is by raw distance inside the thresholds, not by normalised
    distance: an imprecise asset must not outrank a precise one merely for
    being imprecise. Returns the full set; callers cap for reporting.
    """
    if plume_sigma_m <= 0.0:
        raise ValueError("plume_sigma_m must be positive")
    sigmas = index.effective_sigmas_m(scale)
    window_m = pair_threshold_m(plume_sigma_m, float(sigmas.max()) if len(sigmas) else 0.0)
    window = bbox_for_radius_m(leak.lat, leak.lon, window_m)
    candidates: list[Candidate] = []
    for i in index.query_window(window):
        threshold = pair_threshold_m(plume_sigma_m, float(sigmas[i]))
        distance, inside = distance_to_geometry_m(leak, index.geometries[i], window)
        if not inside and distance > threshold:
            continue
        asset = index.asset(int(i))
        candidates.append(Candidate(
            asset=asset, distance_m=distance, inside=inside,
            compatible=_is_compatible(source_type, asset.asset_kind),
            route_traced=_route_traced(asset), sigma_m=float(sigmas[i]), threshold_m=threshold,
        ))
    candidates.sort(key=Candidate.sort_key)
    return candidates


def _ambiguity(candidates: Sequence[Candidate]) -> tuple[int, int, str]:
    """(distinct physical groups, distinct named parties, flag).

    n_parties counts distinct operator/parent/owner strings among candidates
    that name anyone: 111 competing wells of one operator are ambiguous as
    assets but not as an accountability target.
    """
    groups = {c.asset.group_key for c in candidates}
    parties = {c.asset.named_party for c in candidates if c.asset.named_party}
    if len(candidates) <= 1:
        return len(groups), len(parties), AMBIGUITY_NONE
    if len(groups) == 1:
        return 1, len(parties), AMBIGUITY_PLACEHOLDER_GROUP
    return len(groups), len(parties), AMBIGUITY_COMPETING


def attribute(
    index: AssetIndex,
    source_name: str,
    lat: float,
    lon: float,
    source_type: str | None,
    satellites: Iterable[str],
    wind_u: float | None,
    wind_v: float | None,
    scale: SigmaScale = SigmaScale(),
) -> Attribution:
    """Full attribution of one MARS source under one uncertainty scenario.

    radius_m is the plume-only radius (kept for reporting and for the
    scale.enabled=False comparison); each candidate carries its own pair
    threshold. window_m is the largest threshold examined.
    """
    derived = search_radius_m(satellites) * scale.plume_multiplier
    capped = derived > GEO_VALIDATED_SEPARATION_M
    radius = min(derived, GEO_VALIDATED_SEPARATION_M)
    centre = search_centre(lat, lon, wind_u, wind_v)
    leak = Leak(centre.lat, centre.lon)
    candidates = generate_candidates(index, leak, radius, source_type, scale)
    window_m = max((c.threshold_m for c in candidates), default=radius)
    n_groups, n_parties, ambiguity = _ambiguity(candidates)
    if not candidates:
        tier = TIER_UNMAPPED
    elif candidates[0].asset.named_party:
        tier = TIER_OPERATOR_NAMED
    else:
        tier = TIER_ASSET_ONLY
    return Attribution(
        source_name=source_name, tier=tier, leak=leak, published_lat=lat, published_lon=lon,
        search_mode=centre.mode, shift_m=centre.shift_m, radius_m=radius, radius_capped=capped,
        plume_sigma_m=radius, window_m=window_m,
        satellites=tuple(sorted(set(satellites))), n_candidates=len(candidates),
        n_groups=n_groups, n_parties=n_parties, ambiguity=ambiguity,
        candidates=candidates[:MAX_CANDIDATES_LISTED],
    )


# --- batch over the unanswered MARS cases ---------------------------------------


def load_cases(db_path: Path = MARS_DB_PATH) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Unanswered sources and their plumes (satellite, wind) from mars.db."""
    with sqlite3.connect(db_path) as connection:
        sources = pd.read_sql(
            f"SELECT source_name, lat, lon, country, source_type FROM {SOURCES_TABLE} "
            "WHERE feedback_government = ? AND feedback_operator = ?",
            connection, params=(FEEDBACK_NO, FEEDBACK_NO),
        )
        plumes = pd.read_sql(
            f"SELECT source_name, satellite, wind_u, wind_v FROM {PLUMES_TABLE} WHERE source_name IS NOT NULL",
            connection,
        )
    return sources, plumes[plumes.source_name.isin(sources.source_name)]


def attribute_all(
    index: AssetIndex, sources: pd.DataFrame, plumes: pd.DataFrame, scale: SigmaScale = SigmaScale()
) -> list[Attribution]:
    by_source = {name: group for name, group in plumes.groupby("source_name")}
    results = []
    for row in sources.itertuples(index=False):
        group = by_source.get(row.source_name)
        if group is None or group.empty:
            raise ValueError(f"{row.source_name} has no plumes; cannot derive a radius")
        with_wind = group.dropna(subset=["wind_u", "wind_v"])
        wind = with_wind.iloc[0] if len(with_wind) else None
        results.append(attribute(
            index, row.source_name, float(row.lat), float(row.lon), row.source_type,
            group.satellite.tolist(),
            float(wind.wind_u) if wind is not None else None,
            float(wind.wind_v) if wind is not None else None,
            scale,
        ))
    return results


def scenario_row(results: Sequence[Attribution]) -> dict[str, float]:
    tiers = pd.Series([r.tier for r in results])
    mapped = [r for r in results if r.tier != TIER_UNMAPPED]
    sets = pd.Series([r.n_candidates for r in mapped]) if mapped else pd.Series([0])
    groups = pd.Series([r.n_groups for r in mapped]) if mapped else pd.Series([0])
    parties = pd.Series([r.n_parties for r in mapped]) if mapped else pd.Series([0])
    return {
        "named": int((tiers == TIER_OPERATOR_NAMED).sum()), "asset_only": int((tiers == TIER_ASSET_ONLY).sum()),
        "unmapped": int((tiers == TIER_UNMAPPED).sum()),
        "named_pct": float((tiers == TIER_OPERATOR_NAMED).mean() * 100),
        "set_p50": float(sets.median()), "set_p90": float(sets.quantile(0.9)), "set_max": int(sets.max()),
        "groups_p50": float(groups.median()), "parties_p50": float(parties.median()),
        "parties_2plus": int((parties >= 2).sum()),
    }


def scenarios() -> dict[str, SigmaScale]:
    rows = {
        "4B: plume sigma only (asset sigma off)": SigmaScale(enabled=False),
        "4D default: plume + asset sigma": SigmaScale(),
    }
    for k in SIGMA_SENSITIVITY_MULTIPLIERS:
        rows[f"assumed asset sigmas x{k:g}"] = SigmaScale(assumed_multiplier=k)
    for p in EXTRACTION_SIGMA_SENSITIVITY_PERCENTILES:
        if p != EXTRACTION_SIGMA_PERCENTILE:
            rows[f"extraction sigma = outline p{p}"] = SigmaScale(extraction_percentile=p)
    for k in RADIUS_SENSITIVITY_MULTIPLIERS:
        rows[f"plume sigma x{k:g} (pixels sensitivity)"] = SigmaScale(plume_multiplier=k)
    return rows


def summarise(index: AssetIndex, sources: pd.DataFrame, plumes: pd.DataFrame) -> pd.DataFrame:
    total = len(sources)
    table_rows = {}
    default_results: list[Attribution] | None = None
    for name, scale in scenarios().items():
        results = attribute_all(index, sources, plumes, scale)
        table_rows[name] = scenario_row(results)
        if scale == SigmaScale():
            default_results = results
    assert default_results is not None
    table = pd.DataFrame(table_rows).T

    print(f"ATTRIBUTION OVER {total:,} UNANSWERED MARS CASES — uncertainty-budget scenarios\n")
    with pd.option_context("display.width", 220, "display.max_columns", None, "display.float_format", "{:,.1f}".format):
        print(table.to_string())
    print("\n  named_pct = OPERATOR_NAMED share; set_* = candidate-set size over mapped cases;")
    print("  groups_p50 / parties_p50 = median distinct physical groups / distinct named parties; parties_2plus = mapped cases with 2+ parties.")

    frame = pd.DataFrame({
        "source_name": [r.source_name for r in default_results], "tier": [r.tier for r in default_results],
        "n_candidates": [r.n_candidates for r in default_results], "n_groups": [r.n_groups for r in default_results],
        "n_parties": [r.n_parties for r in default_results], "ambiguity": [r.ambiguity for r in default_results],
        "search_mode": [r.search_mode for r in default_results], "radius_m": [r.radius_m for r in default_results],
        "window_m": [r.window_m for r in default_results],
        "top_kind": [r.top.asset.asset_kind if r.top else None for r in default_results],
        "top_sigma_m": [r.top.sigma_m if r.top else math.nan for r in default_results],
        "top_distance_m": [r.top.distance_m if r.top else math.nan for r in default_results],
    }).merge(sources[["source_name", "country", "source_type"]], on="source_name")

    print("\nDEFAULT SCENARIO")
    unmapped = int((frame.tier == TIER_UNMAPPED).sum())
    print(f"  UNMAPPED: {unmapped:,} of {total:,} cases have no asset within its pair threshold.")
    print("  search modes: " + ", ".join(f"{m} {n:,}" for m, n in frame.search_mode.value_counts().items()))
    print(f"  plume sigma m: p50 {frame.radius_m.median():,.0f}; largest pair threshold examined p50 {frame.window_m.median():,.0f}")
    mapped = frame[frame.tier != TIER_UNMAPPED]
    print("  ambiguity: " + ", ".join(f"{a} {n:,}" for a, n in mapped.ambiguity.value_counts().items()))
    competing = mapped[mapped.ambiguity == AMBIGUITY_COMPETING]
    print(f"  competing cases by distinct named parties: 0 -> {int((competing.n_parties == 0).sum())}, "
          f"1 -> {int((competing.n_parties == 1).sum())}, 2+ -> {int((competing.n_parties >= 2).sum())}")
    print("\n  top candidate kind (mapped):")
    print(mapped.top_kind.value_counts().head(12).to_string())
    print("\n  per country (top 15):")
    by_country = pd.crosstab(frame.country, frame.tier).reindex(columns=[TIER_OPERATOR_NAMED, TIER_ASSET_ONLY, TIER_UNMAPPED], fill_value=0)
    by_country["cases"] = by_country.sum(axis=1)
    print(by_country.sort_values("cases", ascending=False).head(15).to_string())
    print("\n  per source_type (top 12):")
    by_type = pd.crosstab(frame.source_type, frame.tier).reindex(columns=[TIER_OPERATOR_NAMED, TIER_ASSET_ONLY, TIER_UNMAPPED], fill_value=0)
    by_type["cases"] = by_type.sum(axis=1)
    print(by_type.sort_values("cases", ascending=False).head(12).to_string())
    return frame


def main() -> int:
    index = AssetIndex.from_parquet()
    print(f"Loaded {len(index):,} assets; sigma sidecar {'loaded' if index.sigma_percentiles else 'MISSING'}")
    sources, plumes = load_cases()
    summarise(index, sources, plumes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
