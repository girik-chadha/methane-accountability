"""Inspect GEM's Global Oil and Gas Extraction Tracker workbook.

INSPECTION ONLY. No normalisation, no spatial join, no matching.

Why this file matters more than the pipelines: the MARS case load is entirely
oil and gas, and by source_type it is dominated by point-like production and
processing assets (gas disposal, flares, well heads, gathering and boosting,
tank batteries, gas processing). Pipelines are under a quarter. This tracker
is the closest thing GEM publishes to those assets, so its coordinates,
outlines and ownership fields are the primary attribution inputs.

Reading: etl/xlsx_stream.py, standard library only, every cell as the raw
text GEM wrote. That is deliberate: the null convention differs between GEM
files (the pipelines GeoPackage used "--" and "") and pandas' Excel reader
would erase the distinction.

The ownership workbook's Asset Ownership sheet is scanned as well, to find
whether this tracker's Unit/Project IDs appear there. Its 135 MB string table
is streamed with an early stop at the highest index the sheet references, so
the scan costs about two seconds, not minutes.

Usage:
    python etl/inspect_extraction.py
"""

from __future__ import annotations

import sqlite3
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Geod

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import (  # noqa: E402
    EXTRACTION_FIELD_SHEET,
    EXTRACTION_PROJECT_SHEET,
    EXTRACTION_UNIT_LIST_SEPARATOR,
    FEEDBACK_NO,
    GEM_EXTRACTION_XLSX,
    GEM_OWNERSHIP_XLSX,
    GEO_VALIDATED_SEPARATION_M,
    MARS_DB_PATH,
    RAW_DIR,
    SOURCES_TABLE,
)
from etl.xlsx_stream import (  # noqa: E402
    list_sheets,
    read_sheet_raw,
    resolve,
    resolve_shared_strings,
    sheet_dimension,
    sheet_member,
    stream_rows,
)

GEOD = Geod(ellps="WGS84")

# Reporting choices, not scientific constants.
SAMPLE_ROWS = 5
WKT_PREVIEW_CHARS = 90
# Tokens GEM has been seen to use for "no value" across its files. Counted per
# column so the convention of THIS file is reported, not assumed.
NULL_TOKEN_CANDIDATES = ("", "--", "-", "N/A", "n/a", "NA", "unknown", "Unknown", "TBD", "none", "None")
AREA_BAND_EDGES_KM2 = (0, 1, 10, 100, 1_000, 10_000, np.inf)
# A coordinate pair shared by at least this many units is listed by name.
SHARED_POINT_REPORT_MIN_UNITS = 5
COORD_CLUSTER_LIST = 8
OWNERSHIP_ASSET_SHEET = "Asset Ownership"
# Source: Microsoft, "Excel specifications and limits": total number of
# characters that a cell can contain is 32,767.
EXCEL_CELL_CHAR_LIMIT = 32_767
ID_COLUMN_MARKER = "ID"

M_PER_KM = 1_000.0
M2_PER_KM2 = 1_000_000.0


# --- printing -------------------------------------------------------------------


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def subhead(text: str) -> None:
    print()
    print(f"--- {text} ---")


def wide() -> pd.option_context:
    return pd.option_context(
        "display.max_rows", None, "display.max_columns", None,
        "display.width", 200, "display.max_colwidth", 100,
    )


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:5.1f}%" if d else "  n/a"


# --- pure helpers (tested) ------------------------------------------------------


def null_token_tally(series: pd.Series) -> dict[str, int]:
    """Count absent cells and each candidate null token in a raw-text column."""
    tally = {"absent": int(series.isna().sum())}
    for token in NULL_TOKEN_CANDIDATES:
        count = int((series == token).sum())
        if count:
            tally[repr(token)] = count
    return tally


def coordinate_usability(lat: pd.Series, lon: pd.Series) -> dict[str, int]:
    """Classify raw lat/lon text into usable and the ways it can be unusable."""
    lat_n = pd.to_numeric(lat, errors="coerce")
    lon_n = pd.to_numeric(lon, errors="coerce")
    both = lat_n.notna() & lon_n.notna()
    in_range = both & lat_n.abs().le(90) & lon_n.abs().le(180)
    at_origin = in_range & lat_n.eq(0) & lon_n.eq(0)
    usable = in_range & ~at_origin
    pairs = pd.Series(list(zip(lat_n[usable], lon_n[usable])))
    return {
        "rows": int(len(lat)),
        "both_numeric": int(both.sum()),
        "one_numeric_only": int((lat_n.notna() ^ lon_n.notna()).sum()),
        "neither_numeric": int((~lat_n.notna() & ~lon_n.notna()).sum()),
        "out_of_range": int((both & ~in_range).sum()),
        "at_0_0": int(at_origin.sum()),
        "usable": int(usable.sum()),
        "distinct_usable_points": int(pairs.nunique()),
    }


OUTLINE_COLUMNS = ("geom_type", "valid", "is_empty", "lonlat_range_ok", "area_km2", "extent_km")
MAX_ABS_LON_DEG = 180.0
MAX_ABS_LAT_DEG = 90.0


def outline_table(wkt: pd.Series) -> pd.DataFrame:
    """Per row with a non-empty WKT: type, validity, geodesic area and extent.

    area_km2 is the WGS84 ellipsoidal area from pyproj (unsigned). extent_km is
    the geodesic length of the bounding-box diagonal, the size that matters
    against the 10 km projection envelope.

    lonlat_range_ok is False when any coordinate lies outside |lon| <= 180,
    |lat| <= 90: a geometry stored in a projected CRS (metres) inside a column
    that is nominally WGS84 degrees. Such rows, and empty geometries, get NaN
    area and extent rather than a number computed from meaningless input.
    """
    present = wkt.fillna("").astype(str).str.strip()
    has = present != ""
    geoms = shapely.from_wkt(present[has].to_numpy(), on_invalid="ignore")
    rows = []
    for index, geom in zip(present[has].index, geoms):
        if geom is None:
            rows.append((index, "UNPARSEABLE", False, False, False, np.nan, np.nan))
            continue
        valid = bool(shapely.is_valid(geom))
        if geom.is_empty:
            rows.append((index, geom.geom_type, valid, True, True, np.nan, np.nan))
            continue
        minx, miny, maxx, maxy = shapely.bounds(geom)
        range_ok = bool(
            max(abs(minx), abs(maxx)) <= MAX_ABS_LON_DEG and max(abs(miny), abs(maxy)) <= MAX_ABS_LAT_DEG
        )
        if not range_ok:
            rows.append((index, geom.geom_type, valid, False, False, np.nan, np.nan))
            continue
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        _, _, diagonal_m = GEOD.inv(minx, miny, maxx, maxy)
        rows.append((index, geom.geom_type, valid, False, True, abs(area_m2) / M2_PER_KM2, diagonal_m / M_PER_KM))
    return pd.DataFrame(rows, columns=["row", *OUTLINE_COLUMNS]).set_index("row")


def split_unit_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(EXTRACTION_UNIT_LIST_SEPARATOR.strip()) if part.strip()]


# --- sections -------------------------------------------------------------------


def report_sheets(path: Path) -> None:
    banner(f"{path.name} — {path.stat().st_size / 1e6:.1f} MB — sheets")
    with zipfile.ZipFile(path) as bundle:
        for name, member in list_sheets(bundle):
            dimension = sheet_dimension(bundle, member)
            stored = sum(1 for _, cells in stream_rows(bundle, member) if cells)
            print(f"  {name!r:<36} {member:<28} declared extent {str(dimension):<14} rows with cells {stored:>7,}")


def report_columns_and_nulls(frame: pd.DataFrame, label: str) -> None:
    banner(f"{label} — {len(frame):,} rows x {len(frame.columns)} columns; null convention")
    print("  Every cell as GEM wrote it. 'absent' = no cell in the file; the rest are literal tokens.")
    print(f"  {'#':>3}  {'column':<28} {'absent':>6} {'\"\"':>6} {'other null tokens':<24} {'numeric':>7} {'distinct':>8}  top values")
    for position, column in enumerate(frame.columns):
        series = frame[column]
        tally = null_token_tally(series)
        others = {k: v for k, v in tally.items() if k not in ("absent", "''")}
        numeric = int(pd.to_numeric(series, errors="coerce").notna().sum())
        real = series[series.notna() & ~series.isin(NULL_TOKEN_CANDIDATES)]
        top = "; ".join(f"{str(k)[:22]!r}:{v}" for k, v in real.value_counts().head(2).items())
        print(
            f"  {position:>3}  {column[:28]:<28} {tally['absent']:>6} {tally.get(chr(39) * 2, 0):>6} "
            f"{str(others) if others else '-':<24} {numeric:>7} {real.nunique():>8}  {top}"
        )
    seen = sorted({k for c in frame.columns for k in null_token_tally(frame[c]) if k != "absent"})
    print(f"\n  null tokens present anywhere in this sheet: {seen if seen else 'none'}; absent cells: "
          f"{int(frame.isna().sum().sum()):,}")


def report_coordinates(frame: pd.DataFrame, label: str, name_column: str, id_column: str) -> None:
    banner(f"{label} — Latitude / Longitude usability")
    stats = coordinate_usability(frame["Latitude"], frame["Longitude"])
    for key, value in stats.items():
        print(f"  {key:<24} {value:>7,}  {pct(value, stats['rows']) if key != 'rows' else ''}")

    lat = pd.to_numeric(frame["Latitude"], errors="coerce")
    lon = pd.to_numeric(frame["Longitude"], errors="coerce")
    usable = lat.notna() & lon.notna() & ~(lat.eq(0) & lon.eq(0))
    points = frame.loc[usable].assign(_lat=lat[usable], _lon=lon[usable])
    cluster = points.groupby(["_lat", "_lon"]).size().sort_values(ascending=False)
    shared = cluster[cluster >= SHARED_POINT_REPORT_MIN_UNITS]
    subhead(f"coordinate pairs shared by >= {SHARED_POINT_REPORT_MIN_UNITS} rows: {len(shared)} pairs, "
            f"{int(shared.sum()):,} rows")
    print("  A shared point means several assets sit on one placeholder coordinate; any leak near it")
    print("  cannot be attributed between them by distance.")
    for (plat, plon), count in shared.head(COORD_CLUSTER_LIST).items():
        members = points[(points._lat == plat) & (points._lon == plon)]
        countries = ", ".join(members["Country/Area"].value_counts().head(2).index)
        names = "; ".join(str(n)[:24] for n in members[name_column].head(3))
        print(f"  ({plat:.4f}, {plon:.4f})  x{count:<4} {countries:<22} e.g. {names}")

    subhead("Location accuracy vs. coordinate presence")
    table = pd.crosstab(frame["Location accuracy"].fillna("<absent>"), usable.map({True: "usable coords", False: "no coords"}))
    print(table.to_string())


def report_outlines(frame: pd.DataFrame, label: str, column: str) -> pd.DataFrame:
    banner(f"{label} — {column}")
    table = outline_table(frame[column])
    print(f"  rows with an outline: {len(table):,} / {len(frame):,}  ({pct(len(table), len(frame))})")
    if table.empty:
        return table
    unparseable = table.geom_type == "UNPARSEABLE"
    print(f"  unparseable WKT:      {int(unparseable.sum()):,}")
    if unparseable.any():
        lengths = frame.loc[table.index[unparseable], column].fillna("").str.len()
        print(f"    their string lengths: {lengths.value_counts().head(3).to_dict()}  "
              f"(Excel's cell limit is {EXCEL_CELL_CHAR_LIMIT:,} characters)")
    print(f"  invalid geometries:   {int((~table.valid & ~unparseable).sum()):,}")
    print(f"  empty geometries:     {int(table["is_empty"].sum()):,}")
    projected = ~table.lonlat_range_ok & ~unparseable
    print(f"  coordinates outside lon/lat range: {int(projected.sum()):,}  "
          "(a projected CRS in metres stored as if WGS84 degrees; excluded from area/extent below)")
    if projected.any():
        by_country = frame.loc[table.index[projected], "Country/Area"].value_counts()
        print("    by country: " + ", ".join(f"{k} {v}" for k, v in by_country.items()))
        example = shapely.bounds(shapely.from_wkt(frame.loc[table.index[projected][0], column]))
        print(f"    example bounds: {np.round(example, 1).tolist()}")
    subhead("geometry types")
    print(table.geom_type.value_counts().to_string())

    area = table.area_km2.dropna()
    subhead("outline area, km2 (WGS84 ellipsoidal, pyproj)")
    for name, value in (("min", area.min()), ("p10", area.quantile(.1)), ("p50", area.median()),
                        ("p90", area.quantile(.9)), ("p99", area.quantile(.99)), ("max", area.max()),
                        ("total", area.sum())):
        print(f"  {name:<6} {value:>14,.2f}")
    edges = np.array(AREA_BAND_EDGES_KM2, dtype=float)
    band = np.digitize(area.to_numpy(), edges[1:-1], right=True)
    print(f"  {'band km2':>16} {'outlines':>9} {'%':>7}")
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        name = f"{lo:g}-{hi:g}" if np.isfinite(hi) else f"> {lo:g}"
        n = int((band == i).sum())
        print(f"  {name:>16} {n:>9,} {n / len(area) * 100:>6.1f}%")

    extent = table.extent_km.dropna()
    subhead("outline extent, km (geodesic bounding-box diagonal)")
    print(f"  p50 {extent.median():,.1f}   p90 {extent.quantile(.9):,.1f}   max {extent.max():,.1f}")
    envelope_km = GEO_VALIDATED_SEPARATION_M / M_PER_KM
    over = int((extent > envelope_km).sum())
    print(f"  outlines wider than the {envelope_km:.0f} km projection envelope: {over:,} / {len(extent):,} "
          f"({pct(over, len(extent))}). Distance to such an outline cannot be computed in one local")
    print("  frame about the leak; it needs the outline's nearest edge, not its centroid.")

    subhead("does the row's Latitude/Longitude fall inside its own outline?")
    lat = pd.to_numeric(frame.loc[table.index, "Latitude"], errors="coerce")
    lon = pd.to_numeric(frame.loc[table.index, "Longitude"], errors="coerce")
    measurable = (table.geom_type != "UNPARSEABLE") & ~table["is_empty"] & table.lonlat_range_ok
    have_point = lat.notna() & lon.notna() & measurable
    geoms = shapely.from_wkt(frame.loc[table.index[have_point], column].to_numpy())
    pts = shapely.points(lon[have_point].to_numpy(), lat[have_point].to_numpy())
    inside = shapely.contains(geoms, pts)
    print(f"  outline rows with a point: {int(have_point.sum()):,}; point inside outline: {int(inside.sum()):,}; "
          f"outside: {int((~inside).sum()):,}")
    if (~inside).any():
        lines = shapely.shortest_line(geoms[~inside], pts[~inside])
        lines = lines[shapely.get_num_coordinates(lines) == 2]
        coords = shapely.get_coordinates(lines).reshape(-1, 2, 2)
        _, _, gap_m = GEOD.inv(coords[:, 0, 0], coords[:, 0, 1], coords[:, 1, 0], coords[:, 1, 1])
        print(f"  for those outside, geodesic gap to the outline edge: p50 {np.median(gap_m) / M_PER_KM:,.1f} km, "
              f"max {gap_m.max() / M_PER_KM:,.1f} km  (planar nearest point, so approximate)")
    return table


def report_ownership_text(frame: pd.DataFrame, label: str) -> None:
    banner(f"{label} — Operator / Owner(s) / Parent(s): fill and format")
    rows = len(frame)
    for column in ("Operator", "Owner(s)", "Parent(s)"):
        series = frame[column].fillna("")
        filled = series[~series.isin(NULL_TOKEN_CANDIDATES)]
        multi = filled.str.contains(";").sum()
        bracket = filled.str.contains(r"\[").sum()
        percent_forms = filled.str.extract(r"\[(\d+(?:\.\d+)?)%\]")[0].dropna()
        decimals = percent_forms.str.contains(r"\.").mean() * 100 if len(percent_forms) else float("nan")
        print(f"  {column:<10} filled {len(filled):>6,} / {rows:,} ({pct(len(filled), rows)})  distinct {filled.nunique():>5,}  "
              f"with share [..%] {bracket:>5,}  multi-party ';' {multi:>5,}  share written with decimals {decimals:5.1f}%")
    subhead("samples")
    sample = frame.loc[frame["Owner(s)"].fillna("") != "", ["Operator", "Owner(s)", "Parent(s)"]].head(4)
    with wide():
        print(sample.to_string(index=False))
    both_empty = (frame["Owner(s)"].fillna("") == "") & (frame["Parent(s)"].fillna("") == "")
    op_only = both_empty & (frame["Operator"].fillna("") != "")
    print(f"\n  rows with no Owner and no Parent: {int(both_empty.sum()):,}; of those, Operator is filled: {int(op_only.sum()):,}")


def report_location_accuracy(fields: pd.DataFrame, projects: pd.DataFrame) -> None:
    banner("Location accuracy — value counts, and per-field vs per-project")
    for label, frame in ((EXTRACTION_FIELD_SHEET, fields), (EXTRACTION_PROJECT_SHEET, projects)):
        subhead(label)
        print(frame["Location accuracy"].fillna("<absent>").replace("", "<empty>").value_counts().to_string())
    if "Project location type" in projects.columns:
        subhead("Project location type")
        print(projects["Project location type"].replace("", "<empty>").value_counts().to_string())

    subhead("consistency between a project's accuracy and its units' accuracies")
    unit_accuracy = fields.set_index("Unit ID")["Location accuracy"]
    same = differ = missing = 0
    projects_with_units = 0
    for _, row in projects.iterrows():
        units = split_unit_list(row["Units (list of IDs)"])
        if not units:
            continue
        projects_with_units += 1
        for unit in units:
            if unit not in unit_accuracy.index:
                missing += 1
            elif unit_accuracy[unit] == row["Location accuracy"]:
                same += 1
            else:
                differ += 1
    print(f"  projects listing units: {projects_with_units:,} of {len(projects):,}")
    print(f"  unit references: same accuracy as project {same:,}, different {differ:,}, unit ID not in field sheet {missing:,}")
    print("  The column exists on BOTH sheets and is filled independently; it is per-row at each level.")


def report_id_columns(fields: pd.DataFrame, projects: pd.DataFrame) -> None:
    banner("ID columns and the join to the ownership tracker")
    for label, frame in ((EXTRACTION_FIELD_SHEET, fields), (EXTRACTION_PROJECT_SHEET, projects)):
        subhead(label)
        id_columns = [c for c in frame.columns if ID_COLUMN_MARKER in c]
        for column in id_columns:
            series = frame[column].fillna("")
            filled = series[series != ""]
            sample = ", ".join(str(v)[:30] for v in filled.head(2))
            print(f"  {column:<24} filled {len(filled):>6,} / {len(frame):,} ({pct(len(filled), len(frame))})  "
                  f"unique {filled.nunique():>6,}  e.g. {sample}")
        entity = [c for c in frame.columns if "entity" in c.lower()]
        print(f"  columns mentioning 'entity': {entity if entity else 'NONE'}")
    print("\n  Neither sheet carries a GEM Entity ID for Operator, Owner(s) or Parent(s). The only")
    print("  GEM-namespaced keys are Unit ID and Project ID (both 'L' + digits). Resolving owners to")
    print("  entities therefore has to go through the ownership workbook; checked below.")
    overlap = set(fields["Unit ID"]) & set(projects["Project ID"])
    print(f"  Unit IDs that are also Project IDs: {len(overlap):,} (shared namespace or not?)")
    listed = {u for v in projects["Units (list of IDs)"] for u in split_unit_list(v)}
    print(f"  distinct units listed by projects: {len(listed):,}; of which present in field sheet: "
          f"{len(listed & set(fields['Unit ID'])):,}; field units belonging to no project: "
          f"{len(set(fields['Unit ID']) - listed):,}")


def report_ownership_join(fields: pd.DataFrame, projects: pd.DataFrame) -> None:
    """Scan the ownership workbook's Asset Ownership sheet for this tracker's IDs."""
    path = RAW_DIR / GEM_OWNERSHIP_XLSX
    banner(f"{path.name} :: {OWNERSHIP_ASSET_SHEET} — do extraction IDs appear?")
    with zipfile.ZipFile(path) as bundle:
        member = sheet_member(bundle, OWNERSHIP_ASSET_SHEET)
        header: dict[int, str] = {}
        raw_rows = []
        wanted: set[int] = set()
        for row_number, cells in stream_rows(bundle, member):
            if row_number == 1:
                wanted.update(int(r) for k, r in cells.values() if k == "s")
                header_cells = cells
                continue
            raw_rows.append(cells)
            wanted.update(int(r) for k, r in cells.values() if k == "s")
        strings = resolve_shared_strings(bundle, wanted)
        header = {i: resolve(c, strings) or f"_col_{i}" for i, c in header_cells.items()}
        width = max(header) + 1
        data = [[resolve(row.get(i), strings) for i in range(width)] for row in raw_rows]
    assets = pd.DataFrame(data, columns=[header.get(i, f"_col_{i}") for i in range(width)])
    print(f"  rows: {len(assets):,}   columns: {list(assets.columns)}")
    subhead("Asset Type")
    print(assets["Asset Type"].value_counts(dropna=False).to_string())

    unit_ids = set(fields["Unit ID"]); project_ids = set(projects["Project ID"])
    for column in ("Asset ID", "Asset Unit ID"):
        series = assets[column].fillna("")
        in_units = series.isin(unit_ids); in_projects = series.isin(project_ids)
        subhead(f"{column}: matches against extraction tracker")
        print(f"  filled {int((series != '').sum()):,};  = a field Unit ID: {int(in_units.sum()):,} rows "
              f"({series[in_units].nunique():,} distinct units);  = a Project ID: {int(in_projects.sum()):,} rows "
              f"({series[in_projects].nunique():,} distinct projects)")
        hit = assets[in_units | in_projects]
        if len(hit):
            print("  Asset Type of matching rows:", hit["Asset Type"].value_counts().to_dict())
            owner_filled = (hit["Immediate Owner Entity ID"].fillna("") != "").sum()
            print(f"  of matching rows, Immediate Owner Entity ID filled: {owner_filled:,} / {len(hit):,}")
    matched_units = set(assets["Asset ID"]).union(set(assets["Asset Unit ID"])) & unit_ids
    print(f"\n  field units with at least one ownership row: {len(matched_units):,} / {len(unit_ids):,} "
          f"({pct(len(matched_units), len(unit_ids))})")


def report_samples(frame: pd.DataFrame, label: str, wkt_column: str) -> None:
    banner(f"{label} — first {SAMPLE_ROWS} rows, transposed")
    sample = frame.head(SAMPLE_ROWS).copy()
    sample[wkt_column] = sample[wkt_column].fillna("").str.slice(0, WKT_PREVIEW_CHARS)
    with wide():
        print(sample.T)


def report_mars_country_coverage(fields: pd.DataFrame) -> None:
    banner("coverage check: extraction rows per MARS case country (no matching, counts only)")
    if not MARS_DB_PATH.exists():
        print(f"  {MARS_DB_PATH} not found; run etl/load_mars.py. Skipped.")
        return
    with sqlite3.connect(MARS_DB_PATH) as connection:
        cases = pd.read_sql(
            f"SELECT country, COUNT(*) AS cases FROM {SOURCES_TABLE} "
            f"WHERE feedback_government = ? AND feedback_operator = ? GROUP BY country ORDER BY cases DESC",
            connection, params=(FEEDBACK_NO, FEEDBACK_NO),
        )
    by_country = fields["Country/Area"].value_counts()
    usable = coordinate_usability(fields["Latitude"], fields["Longitude"])["usable"]
    print(f"  {'MARS case country':<28} {'cases':>6} {'extraction rows':>16} {'with coords':>12}  note")
    lat = pd.to_numeric(fields["Latitude"], errors="coerce"); lon = pd.to_numeric(fields["Longitude"], errors="coerce")
    has = lat.notna() & lon.notna()
    total_cases = cases["cases"].sum()
    for _, row in cases.iterrows():
        n = int(by_country.get(row["country"], 0))
        with_coords = int((has & (fields["Country/Area"] == row["country"])).sum())
        note = "" if n else "NO ROWS: true gap or a country-name mismatch"
        print(f"  {row['country'][:28]:<28} {row['cases']:>6,} {n:>16,} {with_coords:>12,}  {note}")
    print(f"\n  total cases {total_cases:,}; extraction rows with usable coordinates overall {usable:,}")
    print("  Country names are compared verbatim on purpose; harmonising them is normalisation.")


def main() -> int:
    path = RAW_DIR / GEM_EXTRACTION_XLSX
    if not path.exists():
        raise FileNotFoundError(f"Missing snapshot: {path}")

    report_sheets(path)
    fields = read_sheet_raw(path, EXTRACTION_FIELD_SHEET)
    projects = read_sheet_raw(path, EXTRACTION_PROJECT_SHEET)

    report_columns_and_nulls(fields, EXTRACTION_FIELD_SHEET)
    report_columns_and_nulls(projects, EXTRACTION_PROJECT_SHEET)
    report_coordinates(fields, EXTRACTION_FIELD_SHEET, "Unit Name", "Unit ID")
    report_coordinates(projects, EXTRACTION_PROJECT_SHEET, "Project Name", "Project ID")
    report_outlines(fields, EXTRACTION_FIELD_SHEET, "Field outline (WKT)")
    report_outlines(projects, EXTRACTION_PROJECT_SHEET, "Project outline (WKT)")
    report_ownership_text(fields, EXTRACTION_FIELD_SHEET)
    report_ownership_text(projects, EXTRACTION_PROJECT_SHEET)
    report_location_accuracy(fields, projects)
    report_id_columns(fields, projects)
    report_ownership_join(fields, projects)
    report_samples(fields, EXTRACTION_FIELD_SHEET, "Field outline (WKT)")
    report_mars_country_coverage(fields)
    banner("DONE — inspection only; nothing normalised, nothing joined, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
