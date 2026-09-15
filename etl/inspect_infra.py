"""Inspect the Global Energy Monitor infrastructure snapshots in data/raw/.

INSPECTION ONLY. No normalisation, no spatial join, no matching. This prints
what the files actually contain so the attribution data model can be designed
against the real schema rather than an assumed one.

The question this slice exists to answer is the distribution of spacing
between consecutive vertices of the pipeline linestrings. Our planar geometry
(backend/app/geo.py) measures distance to a straight chord between consecutive
vertices, but a real pipeline follows the ground and, between two distant
vertices, the straight chord in our projection is not the pipeline. That error
is separate from the 8.28 m projection envelope and scales with vertex spacing,
so the real spacing distribution decides whether we must densify, and how
finely.

Two things are measured per segment, both with pyproj.Geod on WGS84 as ground
truth, in metres:
  - spacing:   geodesic distance between the two consecutive vertices
  - deviation: geodesic distance between the geodesic midpoint of the segment
               and the midpoint of the straight chord in (lon, lat) space.
               Our local projection is affine in (lon, lat), so the chord's
               midpoint in the projected plane is that same point. This is an
               UPPER BOUND on the chord's perpendicular offset from the
               geodesic at its widest: it also carries a small along-track
               term, because equal steps of latitude are not equal metres
               (about 0.5 m per 55 km on a meridian, where the true
               cross-track gap is zero). For east-west chords, which is where
               the gap is large, the along-track term vanishes by symmetry.

Every layer's CRS must be EPSG:4326. Anything else raises. We never reproject
silently, because a datum mismatch hidden inside attribution distances is the
kind of bug that does not announce itself.

geopandas, pyogrio and pyproj are allowed here. This is etl/, not backend/.

Usage:
    python etl/inspect_infra.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS, Geod

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from etl.xlsx_stream import (  # noqa: E402
    SHARED_STRINGS_MEMBER,
    list_sheets,
    resolve,
    resolve_shared_strings,
    sheet_dimension,
    stream_rows,
)
from backend.app.config import (  # noqa: E402
    GEM_EXTRACTION_XLSX,
    GEM_GAS_PIPELINES_GPKG,
    GEM_LNG_TERMINALS_GPKG,
    GEM_OWNERSHIP_XLSX,
    GEO_ERROR_TOLERANCE_M,
    INFRA_REQUIRED_EPSG,
    INFRA_VERTEX_SPACING_FLAG_M,
    RAW_DIR,
)

GEOD = Geod(ellps="WGS84")

# Reporting choices for this inspection, not scientific constants.
SAMPLE_ROWS = 5
GEOMETRY_WKT_PREVIEW_CHARS = 90
PERCENTILES = (50, 90, 99)
# Bands for the spacing histogram, in metres. Chosen to bracket the flag
# threshold and the validated projection envelope.
SPACING_BAND_EDGES_M = (0, 100, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, np.inf)
LONGEST_SEGMENTS_TO_LIST = 10
# Attribute columns to break the spacing distribution down by, IF the layer has
# them. Discovered by inspection, not assumed: absent columns are skipped.
BREAKDOWN_COLUMNS = ("RouteAccuracy", "RouteType", "Status")
SPACING_BREAKDOWN_COLUMN = "RouteAccuracy"
XLSX_HEADER_SEARCH_ROWS = 5


class CrsError(RuntimeError):
    """A layer is not in the required CRS. We refuse rather than reproject."""


# --- Small printing helpers ---------------------------------------------------


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def subhead(text: str) -> None:
    print()
    print(f"--- {text} ---")


def wide_display() -> pd.option_context:
    return pd.option_context(
        "display.max_rows", None,
        "display.max_columns", None,
        "display.width", 200,
        "display.max_colwidth", 100,
    )


# --- CRS ----------------------------------------------------------------------


def require_epsg(crs_like: object, context: str, epsg: int = INFRA_REQUIRED_EPSG) -> CRS:
    """Return the parsed CRS, or raise CrsError if it is not `epsg`.

    `to_epsg()` returns None for a CRS pyproj cannot confidently identify, which
    also fails here: an unidentifiable CRS is not EPSG:4326.
    """
    if crs_like is None:
        raise CrsError(f"{context}: no CRS at all. Refusing to assume EPSG:{epsg}.")
    crs = CRS.from_user_input(crs_like)
    found = crs.to_epsg()
    if found != epsg:
        raise CrsError(
            f"{context}: CRS is {crs.name!r} (EPSG:{found}), required EPSG:{epsg}. "
            "Refusing to reproject silently; fix the input or make the decision "
            "explicit in DECISIONS.md."
        )
    return crs


# --- Vertex spacing -----------------------------------------------------------


def _wrap_lon_delta_deg(delta: np.ndarray) -> np.ndarray:
    """Wrap longitude differences to (-180, 180]."""
    return (delta + 180.0) % 360.0 - 180.0


def segment_table(lines: gpd.GeoSeries) -> pd.DataFrame:
    """One row per consecutive-vertex segment across all LineString parts.

    Multi-part geometries are exploded first so that a segment never spans two
    parts. Columns: feature (positional index into `lines`), lon1, lat1, lon2,
    lat2, spacing_m, deviation_m, mid_lat.
    """
    geoms = lines.values
    parts, part_feature = shapely.get_parts(geoms, return_index=True)
    is_line = shapely.get_type_id(parts) == shapely.GeometryType.LINESTRING
    parts, part_feature = parts[is_line], part_feature[is_line]

    coords, part_index = shapely.get_coordinates(parts, return_index=True)
    same_part = part_index[:-1] == part_index[1:]
    first, second = coords[:-1][same_part], coords[1:][same_part]
    feature = part_feature[part_index[:-1][same_part]]

    lon1, lat1 = first[:, 0], first[:, 1]
    lon2, lat2 = second[:, 0], second[:, 1]

    azimuth, _, spacing_m = GEOD.inv(lon1, lat1, lon2, lat2)
    geo_mid_lon, geo_mid_lat, _ = GEOD.fwd(lon1, lat1, azimuth, spacing_m / 2.0)
    chord_mid_lat = (lat1 + lat2) / 2.0
    chord_mid_lon = lon1 + _wrap_lon_delta_deg(lon2 - lon1) / 2.0
    _, _, deviation_m = GEOD.inv(geo_mid_lon, geo_mid_lat, chord_mid_lon, chord_mid_lat)

    return pd.DataFrame(
        {
            "feature": feature,
            "lon1": lon1, "lat1": lat1, "lon2": lon2, "lat2": lat2,
            "spacing_m": spacing_m,
            "deviation_m": deviation_m,
            "mid_lat": chord_mid_lat,
        }
    )


def report_breakdowns(gdf: gpd.GeoDataFrame, usable: gpd.GeoSeries, segments: pd.DataFrame) -> None:
    """Value counts of route-quality columns, and spacing stats per RouteAccuracy."""
    present = [c for c in BREAKDOWN_COLUMNS if c in gdf.columns]
    if not present:
        return
    geoms = gdf.geometry.values
    no_route = shapely.is_empty(geoms) | shapely.is_missing(geoms)
    for column in present:
        subhead(f"value counts: {column}  (all features | of which empty geometry)")
        table = pd.DataFrame(
            {
                "features": gdf[column].value_counts(dropna=False),
                "empty_geom": gdf.loc[no_route, column].value_counts(dropna=False),
            }
        ).fillna(0).astype(int).sort_values("features", ascending=False)
        print(table.to_string())

    if SPACING_BREAKDOWN_COLUMN not in gdf.columns:
        return
    subhead(f"spacing and chord deviation by {SPACING_BREAKDOWN_COLUMN}")
    feature_index = usable.index[segments["feature"].to_numpy()]
    group = gdf.loc[feature_index, SPACING_BREAKDOWN_COLUMN].to_numpy()
    spacing = segments["spacing_m"].to_numpy()
    deviation = segments["deviation_m"].to_numpy()
    print(
        f"  {'value':<10} {'features':>8} {'segments':>10} {'km':>11} {'p50 m':>9} {'p90 m':>9} "
        f"{'p99 m':>10} {'max km':>8} {'%len>5km':>9} {'%len dev>10m':>13}"
    )
    for value in pd.unique(group):
        mask = group == value
        sp, dv = spacing[mask], deviation[mask]
        features = int(pd.Series(feature_index[mask]).nunique())
        print(
            f"  {str(value) or '<empty>':<10} {features:>8,} {int(mask.sum()):>10,} {sp.sum() / 1000:>11,.0f} "
            f"{np.percentile(sp, 50):>9,.1f} {np.percentile(sp, 90):>9,.0f} {np.percentile(sp, 99):>10,.0f} "
            f"{sp.max() / 1000:>8,.0f} {sp[sp > INFRA_VERTEX_SPACING_FLAG_M].sum() / sp.sum() * 100:>8.1f}% "
            f"{sp[dv > GEO_ERROR_TOLERANCE_M].sum() / sp.sum() * 100:>12.1f}%"
        )


def report_vertex_spacing(gdf: gpd.GeoDataFrame, label: str) -> None:
    """Print the consecutive-vertex spacing distribution for line geometries."""
    # shapely predicates on the raw array: GeoSeries.notna() warns whenever
    # empty geometries are present, and this layer has some.
    geoms = gdf.geometry.values
    usable = gdf.geometry[~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)]
    segments = segment_table(usable)
    spacing = segments["spacing_m"].to_numpy()
    deviation = segments["deviation_m"].to_numpy()

    banner(f"{label} — consecutive-vertex spacing (geodesic, WGS84, metres)")
    if len(spacing) == 0:
        print("!!! no LineString segments found")
        return

    zero_length = int((spacing == 0.0).sum())
    over_flag = spacing > INFRA_VERTEX_SPACING_FLAG_M
    total_km = spacing.sum() / 1000.0
    print(f"  segments:                    {len(spacing):>12,}")
    print(f"  features contributing:       {segments['feature'].nunique():>12,}")
    print(f"  zero-length (repeated vertex):{zero_length:>11,}")
    print(f"  total length:                {total_km:>12,.1f} km")
    print(f"  min:                         {spacing.min():>12,.2f} m")
    for pct in PERCENTILES:
        print(f"  p{pct:<3}                        {np.percentile(spacing, pct):>12,.2f} m")
    print(f"  mean:                        {spacing.mean():>12,.2f} m")
    print(f"  max:                         {spacing.max():>12,.2f} m")
    print()
    print(
        f"  segments > {INFRA_VERTEX_SPACING_FLAG_M / 1000:.0f} km:             "
        f"{int(over_flag.sum()):>12,}  "
        f"({over_flag.mean() * 100:.3f}% of segments, "
        f"{spacing[over_flag].sum() / 1000:,.1f} km = "
        f"{spacing[over_flag].sum() / spacing.sum() * 100:.2f}% of length)"
    )
    print(
        f"  features with any such segment:{segments.loc[over_flag, 'feature'].nunique():>10,} "
        f"of {segments['feature'].nunique():,}"
    )

    subhead("spacing histogram")
    edges = np.array(SPACING_BAND_EDGES_M, dtype=float)
    band = np.digitize(spacing, edges[1:-1], right=True)
    print(f"  {'band':>18} {'segments':>10} {'% segs':>8} {'length km':>12} {'% length':>9}")
    for i in range(len(edges) - 1):
        mask = band == i
        lo, hi = edges[i], edges[i + 1]
        name = f"{lo / 1000:g}-{hi / 1000:g} km" if np.isfinite(hi) else f"> {lo / 1000:g} km"
        print(
            f"  {name:>18} {int(mask.sum()):>10,} {mask.mean() * 100:>7.2f}% "
            f"{spacing[mask].sum() / 1000:>12,.1f} {spacing[mask].sum() / spacing.sum() * 100:>8.2f}%"
        )

    subhead("chord-vs-geodesic deviation at segment midpoint (metres)")
    print("  Geodesic midpoint to (lon, lat)-chord midpoint: an upper bound on how")
    print("  far the straight chord sits from the geodesic between the same two")
    print("  vertices (includes a small along-track term; see module docstring).")
    print("  Unrelated to the projection envelope; this is the cost of treating")
    print("  the chord as the pipeline.")
    print(f"  max:                         {deviation.max():>12,.3f} m")
    for pct in PERCENTILES:
        print(f"  p{pct:<3}                        {np.percentile(deviation, pct):>12,.3f} m")
    for threshold in (1.0, GEO_ERROR_TOLERANCE_M, 100.0):
        over = deviation > threshold
        print(
            f"  segments deviating > {threshold:>5.0f} m: {int(over.sum()):>10,}  "
            f"({over.mean() * 100:.4f}%, {spacing[over].sum() / 1000:,.1f} km of length)"
        )

    report_breakdowns(gdf, usable, segments)

    subhead(f"{LONGEST_SEGMENTS_TO_LIST} longest segments")
    name_cols = [c for c in gdf.columns if "name" in c.lower()][:2]
    id_cols = [c for c in gdf.columns if c.lower().endswith("id")][:1]
    longest = segments.nlargest(LONGEST_SEGMENTS_TO_LIST, "spacing_m")
    for _, row in longest.iterrows():
        feature = usable.index[int(row["feature"])]
        ident = " | ".join(str(gdf.at[feature, c]) for c in id_cols + name_cols)
        print(
            f"  {row['spacing_m'] / 1000:>8.1f} km  dev {row['deviation_m']:>7.1f} m  "
            f"lat {row['mid_lat']:>6.1f}  ({row['lon1']:.3f},{row['lat1']:.3f})->"
            f"({row['lon2']:.3f},{row['lat2']:.3f})  {ident}"
        )


# --- GeoPackage inspection ----------------------------------------------------


def inspect_layer(path: Path, layer: str) -> None:
    label = f"{path.name} :: {layer}"
    info = pyogrio.read_info(path, layer=layer)

    banner(f"{label} — metadata (pyogrio.read_info, before loading)")
    print(f"  declared geometry type: {info['geometry_type']!r}")
    print(f"  declared feature count: {info['features']:,}")
    print(f"  declared field count:   {len(info['fields'])}")
    print(f"  CRS as stored:          {info['crs']!r}")
    crs = require_epsg(info["crs"], f"{label} (metadata)")
    print(f"  CRS check:              OK, {crs.name} = EPSG:{crs.to_epsg()}")

    gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
    require_epsg(gdf.crs, f"{label} (loaded GeoDataFrame)")

    banner(f"{label} — shape")
    print(f"  rows:    {len(gdf):,}")
    print(f"  columns: {len(gdf.columns)} (including geometry)")

    banner(f"{label} — geometry types actually present")
    print(gdf.geometry.geom_type.value_counts(dropna=False).to_string())
    print(f"  null geometries:  {int(gdf.geometry.isna().sum()):,}")
    print(f"  empty geometries: {int(gdf.geometry.is_empty.sum()):,}")
    n_coords = shapely.get_num_coordinates(gdf.geometry.values)
    print(f"  vertices total:   {int(n_coords.sum()):,}   per feature min {n_coords.min()} "
          f"median {int(np.median(n_coords))} max {n_coords.max()}")
    bounds = gdf.total_bounds
    print(f"  bounds (lon/lat): x {bounds[0]:.4f}..{bounds[2]:.4f}  y {bounds[1]:.4f}..{bounds[3]:.4f}")

    banner(f"{label} — columns, dtypes, nulls, distinct")
    # "nulls" alone misleads here: GEM writes missing text as an empty string,
    # which pandas does not count as NA. Both are shown.
    geometry_name = gdf.geometry.name
    empty_strings = [
        int((gdf[c] == "").sum())
        if c != geometry_name and pd.api.types.is_string_dtype(gdf[c]) else 0
        for c in gdf.columns
    ]
    summary = pd.DataFrame(
        {
            "dtype": gdf.dtypes.astype(str),
            "nulls": gdf.isna().sum(),
            "empty_str": empty_strings,
            "missing_pct": ((gdf.isna().sum() + np.array(empty_strings)) / len(gdf) * 100).round(1),
            "distinct": [gdf[c].nunique(dropna=True) if c != geometry_name else len(gdf) for c in gdf.columns],
        }
    )
    with wide_display():
        print(summary)

    banner(f"{label} — first {SAMPLE_ROWS} rows, transposed")
    sample = pd.DataFrame(gdf.head(SAMPLE_ROWS).drop(columns=gdf.geometry.name))
    sample["geometry (WKT, truncated)"] = (
        gdf.geometry.head(SAMPLE_ROWS).to_wkt().str.slice(0, GEOMETRY_WKT_PREVIEW_CHARS) + "…"
    )
    sample["geometry vertex count"] = n_coords[:SAMPLE_ROWS]
    with wide_display():
        print(sample.T)

    if gdf.geometry.geom_type.isin(["LineString", "MultiLineString"]).any():
        report_vertex_spacing(gdf, label)


def inspect_geopackage(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing snapshot: {path}")
    layers = pyogrio.list_layers(path)
    banner(f"{path.name} — {len(layers)} layer(s), {path.stat().st_size / 1e6:.1f} MB")
    for name, geometry_type in layers:
        print(f"  {name!r:<24} declared geometry: {geometry_type!r}")
    for name, _ in layers:
        inspect_layer(path, str(name))


# --- XLSX inspection without loading the workbook -----------------------------


def _dimension_rows(dimension: str | None) -> str:
    if not dimension or ":" not in dimension:
        return "?"
    last = dimension.split(":")[1]
    return "".join(ch for ch in last if ch.isdigit()) or "?"


def inspect_xlsx(path: Path) -> None:
    """List sheet names, declared extent and header row, streaming only.

    Uses etl/xlsx_stream.py. Only the first few rows of each sheet and the
    shared strings those rows reference are read, so the 135 MB string table
    of the ownership workbook is never loaded.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing snapshot: {path}")
    banner(f"{path.name} — {path.stat().st_size / 1e6:.1f} MB, sheets and headers only")
    with zipfile.ZipFile(path) as bundle:
        sheets = list_sheets(bundle)
        if SHARED_STRINGS_MEMBER in bundle.namelist():
            shared = bundle.getinfo(SHARED_STRINGS_MEMBER)
            print(f"  sharedStrings.xml: {shared.file_size / 1e6:.0f} MB uncompressed; streamed, not loaded")

        per_sheet = []
        wanted: set[int] = set()
        for name, member in sheets:
            dimension = sheet_dimension(bundle, member)
            rows = [cells for _, cells in stream_rows(bundle, member, XLSX_HEADER_SEARCH_ROWS)]
            header_row = max(range(len(rows)), key=lambda i: len(rows[i]), default=None)
            cells = rows[header_row] if header_row is not None else {}
            wanted.update(int(raw) for kind, raw in cells.values() if kind == "s")
            per_sheet.append((name, member, dimension, header_row, cells))
        strings = resolve_shared_strings(bundle, wanted)

    for name, member, dimension, header_row, cells in per_sheet:
        subhead(f"sheet {name!r}  ({member})")
        print(f"  declared extent: {dimension}  (~{_dimension_rows(dimension)} rows)")
        if header_row is None:
            print("  (no rows found)")
            continue
        print(f"  header = widest of first {XLSX_HEADER_SEARCH_ROWS} rows: row {header_row + 1}, {len(cells)} cells")
        for position, column in enumerate(sorted(cells)):
            print(f"  {position:>3}  {resolve(cells[column], strings)}")


# --- Main ---------------------------------------------------------------------


def list_raw_dir() -> None:
    banner(f"data/raw/ contents")
    for entry in sorted(RAW_DIR.iterdir()):
        print(f"  {entry.name:<60} {entry.stat().st_size / 1e6:>8.1f} MB")
        if entry.suffix == ".zip" and entry.name.startswith("gem-"):
            with zipfile.ZipFile(entry) as bundle:
                for member in bundle.infolist():
                    print(f"      └ {member.filename:<56} {member.file_size / 1e6:>8.1f} MB")


def main() -> int:
    list_raw_dir()
    for name in (GEM_GAS_PIPELINES_GPKG, GEM_LNG_TERMINALS_GPKG):
        inspect_geopackage(RAW_DIR / name)
    for name in (GEM_OWNERSHIP_XLSX, GEM_EXTRACTION_XLSX):
        inspect_xlsx(RAW_DIR / name)
    banner("DONE — inspection only; nothing normalised, nothing joined, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
