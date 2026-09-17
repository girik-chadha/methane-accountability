"""Build data/processed/assets.parquet: every usable asset in one flat schema.

Sources (all committed snapshots under data/raw/, see backend/app/config.py):
  - GEM Global Oil and Gas Extraction Tracker: units with coordinates become
    points; field outlines become polygons. The 28 outlines truncated at
    Excel's cell limit, the 110 Poland outlines stored in a projected CRS and
    the 1 empty geometry are excluded, using the same classification as
    etl/inspect_extraction.py.
  - GEM gas pipelines: routed features only (712 empty geometries dropped),
    densified along the geodesic to at most PIPELINE_DENSIFY_MAX_SPACING_M.
  - GEM LNG terminals: points.
  - OGIM v2.7: every point layer (facilities, wells, VIIRS flare detections).

Derived columns that feed confidence later:
  - route_fidelity: pipelines only, geodesic geometry length / GEM's declared
    LengthMergedKm. Per feature, unlike GEM's per-project RouteAccuracy.
  - placeholder_group: assets sharing byte-identical geometry (identical
    coordinate pair for points). They are physically indistinguishable and
    must collapse to ONE candidate group in attribution.

Pipeline deduplication: the three routed features whose RouteType says they
share a route with another ProjectID were measured to overlap their parents by
0.2-3.6% of their length, so they are distinct routes and are kept, with
route_type carried through. Byte-identical routes (201 features in 88 groups,
parallel lines and phases) are collapsed via placeholder_group instead.

Geometry is written as WKB so backend/ can read it with shapely alone.

Usage:
    python etl/build_assets.py                    # full build
    python etl/build_assets.py --skip-ogim-wells  # fast validation build
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import Geod

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import (  # noqa: E402
    ASSETS_PARQUET_PATH,
    ASSETS_SIGMA_SIDECAR_PATH,
    EXTRACTION_SIGMA_PERCENTILE,
    EXTRACTION_SIGMA_SENSITIVITY_PERCENTILES,
    LNG_SIGMA_BY_ACCURACY_M,
    OGIM_SIGMA_M,
    PIPELINE_SIGMA_BY_ROUTE_ACCURACY_M,
    VIIRS_FLARE_SIGMA_M,
    EXTRACTION_FIELD_SHEET,
    GEM_EXTRACTION_USABLE_STATUSES,
    GEM_EXTRACTION_XLSX,
    GEM_GAS_PIPELINES_GPKG,
    GEM_LNG_TERMINALS_GPKG,
    GEM_LNG_USABLE_STATUSES,
    GEM_PIPELINE_USABLE_STATUSES,
    GEM_UNKNOWN_PARTY_TOKENS,
    OGIM_EXCLUDED_STATUSES,
    OGIM_GPKG,
    OGIM_LAYER_ASSET_KINDS,
    OGIM_NULL_NUMERIC,
    OGIM_NULL_TEXT,
    PIPELINE_DENSIFY_MAX_SPACING_M,
    PLACEHOLDER_GROUP_MIN_SIZE,
    RAW_DIR,
)
from etl.inspect_extraction import outline_table  # noqa: E402
from etl.xlsx_stream import read_sheet_raw  # noqa: E402

GEOD = Geod(ellps="WGS84")
M_PER_KM = 1_000.0
OGIM_CHUNK_ROWS = 500_000

ASSET_COLUMNS = [
    "asset_id", "physical_id", "source_dataset", "native_id", "name", "asset_kind",
    "country", "operator", "owner", "parent", "status", "geom_type", "lon", "lat",
    "geometry_wkb", "n_vertices", "scale", "scale_unit", "route_fidelity",
    "route_type", "route_accuracy", "placeholder_group", "placeholder_group_size",
    "location_accuracy", "sigma_m", "sigma_basis",
]

M2_PER_KM2 = 1_000_000.0
BASIS_OUTLINE_EDGE = "measured:outline_edge"
BASIS_OUTLINE_PRESENT = "measured:outline_present"
BASIS_OUTLINE_EQ_RADIUS = "measured:outline_eq_radius"
BASIS_VIIRS = "sourced:viirs_pixel"
BASIS_OGIM = "assumed:ogim_source"
EXTRACTION_ACCURACY_FALLBACK = "all"

SOURCE_GEM_EXTRACTION = "GEM-GOGET-2026-03"
SOURCE_GEM_PIPELINES = "GEM-GGIT-Pipelines-2025-11"
SOURCE_GEM_LNG = "GEM-GGIT-LNG-2025-09"
SOURCE_OGIM = "OGIM-v2.7"


# --- small pure helpers (tested) -------------------------------------------------


def clean_party(value: object) -> str | None:
    """Operator/Owner/Parent text, or None when GEM/OGIM say 'not recorded'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    low = text.casefold()
    if low in GEM_UNKNOWN_PARTY_TOKENS or low.startswith("unknown") or text == OGIM_NULL_TEXT:
        return None
    return text


def clean_text(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return None if text == "" or text == OGIM_NULL_TEXT or text == "--" else text


def to_number(value: object) -> float:
    number = pd.to_numeric(value, errors="coerce")
    if number is None or pd.isna(number) or float(number) == OGIM_NULL_NUMERIC:
        return float("nan")
    return float(number)


def geodesic_length_m(geom: shapely.Geometry) -> float:
    """Sum of geodesic segment lengths over all LineString parts."""
    parts = shapely.get_parts(geom)
    total = 0.0
    for part in parts:
        coords = shapely.get_coordinates(part)
        if len(coords) < 2:
            continue
        _, _, seg = GEOD.inv(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
        total += float(np.sum(seg))
    return total


def densify_geodesic(geom: shapely.Geometry, max_spacing_m: float) -> shapely.Geometry:
    """Insert vertices along the geodesic so no segment exceeds max_spacing_m.

    Original vertices are kept exactly; inserted vertices lie on the WGS84
    geodesic between them (pyproj Geod.npts), so a straight chord between any
    two consecutive vertices now stays within a metre of the true path.
    """
    parts_out = []
    for part in shapely.get_parts(geom):
        coords = shapely.get_coordinates(part)
        if len(coords) < 2:
            continue
        new = [tuple(coords[0])]
        _, _, lengths = GEOD.inv(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
        for (lon1, lat1), (lon2, lat2), length in zip(coords[:-1], coords[1:], lengths):
            if length > max_spacing_m:
                n_inserted = math.ceil(length / max_spacing_m) - 1
                new.extend(GEOD.npts(lon1, lat1, lon2, lat2, n_inserted))
            new.append((lon2, lat2))
        parts_out.append(shapely.linestrings(new))
    if not parts_out:
        return shapely.LineString()
    return parts_out[0] if len(parts_out) == 1 else shapely.multilinestrings(parts_out)


def assign_placeholder_groups(assets: pd.DataFrame) -> pd.DataFrame:
    """Group assets with byte-identical geometry; points by identical (lon, lat)."""
    keys = assets["geometry_wkb"].map(lambda b: hashlib.sha1(b).hexdigest())
    sizes = keys.map(keys.value_counts())
    grouped = sizes >= PLACEHOLDER_GROUP_MIN_SIZE
    assets = assets.copy()
    assets["placeholder_group"] = np.where(grouped, "pg_" + keys.str.slice(0, 12), None)
    assets["placeholder_group_size"] = sizes.astype(int)
    return assets


def frame(rows: list[dict], defaults: dict | None = None) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    for column in ASSET_COLUMNS:
        if column not in out.columns:
            out[column] = (defaults or {}).get(column, None)
    return out[ASSET_COLUMNS]


# --- sources ----------------------------------------------------------------------


def build_extraction() -> pd.DataFrame:
    fields = read_sheet_raw(RAW_DIR / GEM_EXTRACTION_XLSX, EXTRACTION_FIELD_SHEET)
    usable = fields["Status"].fillna("").isin(GEM_EXTRACTION_USABLE_STATUSES)
    lat = pd.to_numeric(fields["Latitude"], errors="coerce")
    lon = pd.to_numeric(fields["Longitude"], errors="coerce")
    has_xy = lat.notna() & lon.notna()

    outlines = outline_table(fields["Field outline (WKT)"])
    measurable = (outlines.geom_type != "UNPARSEABLE") & ~outlines["is_empty"] & outlines.lonlat_range_ok
    excluded = int((~measurable).sum())
    outlined = outlines.index[measurable & usable.reindex(outlines.index).fillna(False)]
    percentiles = extraction_sigma_percentiles(
        pd.DataFrame({
            "eq_radius_m": np.sqrt(outlines.loc[outlined, "area_km2"].to_numpy() * M2_PER_KM2 / math.pi),
            "accuracy": fields.loc[outlined, "Location accuracy"].fillna("").to_numpy(),
        })
    )
    has_outline = set(outlined)

    rows = []
    for index, unit in fields[usable & has_xy].iterrows():
        point = shapely.Point(lon[index], lat[index])
        unit_id = f"gem_extraction:{unit['Unit ID']}"
        accuracy = clean_text(unit["Location accuracy"])
        if index in has_outline:
            sigma, basis = 0.0, BASIS_OUTLINE_PRESENT
        else:
            sigma, basis = extraction_point_sigma(percentiles, accuracy), BASIS_OUTLINE_EQ_RADIUS
        rows.append({
            "asset_id": unit_id, "physical_id": unit_id, "source_dataset": SOURCE_GEM_EXTRACTION,
            "native_id": unit["Unit ID"], "name": clean_text(unit["Unit Name"]), "asset_kind": "extraction_unit",
            "country": unit["Country/Area"], "operator": clean_party(unit["Operator"]),
            "owner": clean_party(unit["Owner(s)"]), "parent": clean_party(unit["Parent(s)"]),
            "status": unit["Status"], "geom_type": "Point", "lon": float(lon[index]), "lat": float(lat[index]),
            "geometry_wkb": shapely.to_wkb(point), "n_vertices": 1,
            "location_accuracy": accuracy, "sigma_m": sigma, "sigma_basis": basis,
        })
    for index in outlines.index[measurable]:
        unit = fields.loc[index]
        if not usable[index]:
            continue
        geom = shapely.from_wkt(unit["Field outline (WKT)"])
        unit_id = f"gem_extraction:{unit['Unit ID']}"
        rows.append({
            "asset_id": f"gem_extraction_outline:{unit['Unit ID']}", "physical_id": unit_id,
            "source_dataset": SOURCE_GEM_EXTRACTION, "native_id": unit["Unit ID"],
            "name": clean_text(unit["Unit Name"]), "asset_kind": "field_outline",
            "country": unit["Country/Area"], "operator": clean_party(unit["Operator"]),
            "owner": clean_party(unit["Owner(s)"]), "parent": clean_party(unit["Parent(s)"]),
            "status": unit["Status"], "geom_type": geom.geom_type, "lon": float("nan"), "lat": float("nan"),
            "geometry_wkb": shapely.to_wkb(geom), "n_vertices": int(shapely.get_num_coordinates(geom)),
            "location_accuracy": clean_text(unit["Location accuracy"]), "sigma_m": 0.0, "sigma_basis": BASIS_OUTLINE_EDGE,
        })
    out = frame(rows)
    print(f"  extraction: {int((out.asset_kind == 'extraction_unit').sum()):,} units, "
          f"{int((out.asset_kind == 'field_outline').sum()):,} outlines "
          f"({excluded} defective outlines excluded; {int((~usable).sum()):,} units with unusable status)")
    print("  extraction sigma from outlines, equivalent radius m: " + "; ".join(
        f"{cls} n={v['n']} " + " ".join(f"p{p}={v[f'p{p}']:,.0f}" for p in EXTRACTION_SIGMA_SENSITIVITY_PERCENTILES)
        for cls, v in percentiles.items()))
    return out, percentiles


def extraction_sigma_percentiles(outlined: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Equivalent-circle radius percentiles of the usable outlines, per GEM
    Location accuracy class plus an "all" fallback. MEASURED, not assumed."""
    out: dict[str, dict[str, float]] = {}
    classes = {EXTRACTION_ACCURACY_FALLBACK: outlined}
    for cls, group in outlined.groupby("accuracy"):
        if cls:
            classes[str(cls)] = group
    for cls, group in classes.items():
        out[cls] = {"n": int(len(group))}
        for p in EXTRACTION_SIGMA_SENSITIVITY_PERCENTILES:
            out[cls][f"p{p}"] = float(np.percentile(group["eq_radius_m"], p))
    return out


def extraction_point_sigma(percentiles: dict[str, dict[str, float]], accuracy: str | None,
                           percentile: int = EXTRACTION_SIGMA_PERCENTILE) -> float:
    cls = accuracy if accuracy in percentiles else EXTRACTION_ACCURACY_FALLBACK
    return percentiles[cls][f"p{percentile}"]


def build_pipelines() -> pd.DataFrame:
    pipes = pyogrio.read_dataframe(RAW_DIR / GEM_GAS_PIPELINES_GPKG)
    routed = ~shapely.is_empty(pipes.geometry.values)
    usable = pipes["Status"].isin(GEM_PIPELINE_USABLE_STATUSES)
    rows = []
    for _, pipe in pipes[routed & usable].iterrows():
        dense = densify_geodesic(pipe.geometry, PIPELINE_DENSIFY_MAX_SPACING_M)
        length_km = geodesic_length_m(dense) / M_PER_KM
        declared_km = to_number(pipe["LengthMergedKm"])
        asset_id = f"gem_pipeline:{pipe['ProjectID']}"
        rows.append({
            "asset_id": asset_id, "physical_id": asset_id, "source_dataset": SOURCE_GEM_PIPELINES,
            "native_id": pipe["ProjectID"],
            "name": " | ".join(p for p in (clean_text(pipe["PipelineName"]), clean_text(pipe["SegmentName"])) if p),
            "asset_kind": "gas_pipeline", "country": pipe["CountriesOrAreas"], "operator": None,
            "owner": clean_party(pipe["Owner"]), "parent": clean_party(pipe["Parent"]),
            "status": pipe["Status"], "geom_type": dense.geom_type, "lon": float("nan"), "lat": float("nan"),
            "geometry_wkb": shapely.to_wkb(dense), "n_vertices": int(shapely.get_num_coordinates(dense)),
            "scale": to_number(pipe["CapacityBcm/y"]), "scale_unit": "bcm/y",
            "route_fidelity": length_km / declared_km if declared_km and declared_km > 0 else float("nan"),
            "route_type": pipe["RouteType"], "route_accuracy": pipe["RouteAccuracy"],
            "sigma_m": PIPELINE_SIGMA_BY_ROUTE_ACCURACY_M[pipe["RouteAccuracy"]],
            "sigma_basis": f"assumed:route_accuracy:{pipe['RouteAccuracy']}",
        })
    out = frame(rows)
    print(f"  pipelines: {len(out):,} routed & usable of {len(pipes):,} "
          f"({int((~routed).sum())} empty, {int((routed & ~usable).sum())} routed but unusable status); "
          f"{int(out.n_vertices.sum()):,} vertices after densification")
    return out


def build_lng() -> pd.DataFrame:
    lng = pyogrio.read_dataframe(RAW_DIR / GEM_LNG_TERMINALS_GPKG)
    usable = lng["Status"].isin(GEM_LNG_USABLE_STATUSES)
    rows = []
    for _, unit in lng[usable].iterrows():
        asset_id = f"gem_lng:{unit['UnitID']}"
        rows.append({
            "asset_id": asset_id, "physical_id": f"gem_lng_terminal:{unit['ProjectID']}",
            "source_dataset": SOURCE_GEM_LNG, "native_id": unit["UnitID"],
            "name": " | ".join(p for p in (clean_text(unit["TerminalName"]), clean_text(unit["UnitName"])) if p),
            "asset_kind": "lng_terminal", "country": unit["Country/Area"],
            "operator": clean_party(unit["Operator"]), "owner": clean_party(unit["Owner"]),
            "parent": clean_party(unit["Parent"]), "status": unit["Status"], "geom_type": "Point",
            "lon": float(unit.geometry.x), "lat": float(unit.geometry.y),
            "geometry_wkb": shapely.to_wkb(unit.geometry), "n_vertices": 1,
            "scale": to_number(unit["CapacityinBcm/y"]), "scale_unit": "bcm/y",
            "location_accuracy": unit["Accuracy"], "sigma_m": LNG_SIGMA_BY_ACCURACY_M[unit["Accuracy"]],
            "sigma_basis": f"assumed:lng_accuracy:{unit['Accuracy']}",
        })
    out = frame(rows)
    print(f"  LNG: {len(out):,} usable units of {len(lng):,}")
    return out


def build_ogim(skip_wells: bool = False) -> pd.DataFrame:
    path = RAW_DIR / OGIM_GPKG
    frames = []
    for layer, kind in OGIM_LAYER_ASSET_KINDS.items():
        if skip_wells and kind == "well":
            print(f"  OGIM {layer}: SKIPPED (--skip-ogim-wells)")
            continue
        total = int(pyogrio.read_info(path, layer=layer)["features"])
        fields = set(pyogrio.read_info(path, layer=layer)["fields"])
        scale_column = "GAS_CAPACITY_MMCFD" if "GAS_CAPACITY_MMCFD" in fields else None
        columns = ["OGIM_ID", "FAC_NAME", "FAC_TYPE", "OGIM_STATUS", "OPERATOR", "COUNTRY"] + ([scale_column] if scale_column else [])
        kept = 0
        for start in range(0, total, OGIM_CHUNK_ROWS):
            chunk = pyogrio.read_dataframe(path, layer=layer, columns=columns, skip_features=start, max_features=OGIM_CHUNK_ROWS)
            chunk = chunk[~chunk["OGIM_STATUS"].isin(OGIM_EXCLUDED_STATUSES)]
            geoms = chunk.geometry.values
            coords = shapely.get_coordinates(geoms)
            ids = "ogim:" + chunk["OGIM_ID"].astype(str)
            part = pd.DataFrame({
                "asset_id": ids, "physical_id": ids, "source_dataset": SOURCE_OGIM,
                "native_id": chunk["OGIM_ID"].astype(str), "name": chunk["FAC_NAME"].map(clean_text),
                "asset_kind": kind, "country": chunk["COUNTRY"].values,
                "operator": chunk["OPERATOR"].map(clean_party), "owner": None, "parent": None,
                "status": chunk["OGIM_STATUS"].map(clean_text), "geom_type": "Point",
                "lon": coords[:, 0], "lat": coords[:, 1], "geometry_wkb": list(shapely.to_wkb(geoms)),
                "n_vertices": 1,
                "scale": chunk[scale_column].map(to_number) if scale_column else float("nan"),
                "scale_unit": "MMcf/d" if scale_column else None,
                "location_accuracy": None,
                "sigma_m": VIIRS_FLARE_SIGMA_M if kind == "flare_detection" else OGIM_SIGMA_M,
                "sigma_basis": BASIS_VIIRS if kind == "flare_detection" else BASIS_OGIM,
            })
            frames.append(frame(part.to_dict("records")) if len(part) < 1000 else part.reindex(columns=ASSET_COLUMNS))
            kept += len(part)
        print(f"  OGIM {layer}: {kept:,} of {total:,}")
    return pd.concat(frames, ignore_index=True)[ASSET_COLUMNS] if frames else frame([])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    skip_wells = "--skip-ogim-wells" in argv
    print("Building assets:")
    extraction, percentiles = build_extraction()
    parts = [extraction, build_pipelines(), build_lng(), build_ogim(skip_wells)]
    assets = pd.concat(parts, ignore_index=True)
    if assets["sigma_m"].isna().any() or (assets["sigma_m"] < 0).any():
        raise ValueError("every asset needs a non-negative sigma_m")
    assets = assign_placeholder_groups(assets)
    if assets["asset_id"].duplicated().any():
        raise ValueError(f"duplicate asset_id: {assets.loc[assets['asset_id'].duplicated(), 'asset_id'].head().tolist()}")

    ASSETS_PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)
    assets.to_parquet(ASSETS_PARQUET_PATH, index=False)
    ASSETS_SIGMA_SIDECAR_PATH.write_text(json.dumps({
        "extraction_eq_radius_m": percentiles,
        "extraction_percentile_used": EXTRACTION_SIGMA_PERCENTILE,
        "assumed": {"pipeline_by_route_accuracy_m": PIPELINE_SIGMA_BY_ROUTE_ACCURACY_M,
                    "lng_by_accuracy_m": LNG_SIGMA_BY_ACCURACY_M, "ogim_m": OGIM_SIGMA_M},
        "sourced": {"viirs_flare_m": VIIRS_FLARE_SIGMA_M},
    }, indent=2))
    named = assets[["operator", "owner", "parent"]].notna().any(axis=1)
    grouped = assets["placeholder_group"].notna()
    print(f"\nWrote {ASSETS_PARQUET_PATH.relative_to(PROJECT_ROOT)}: {len(assets):,} assets"
          + (" (OGIM wells skipped)" if skip_wells else ""))
    print(assets["asset_kind"].value_counts().to_string())
    print(f"\n  with a named operator/owner/parent: {int(named.sum()):,} ({named.mean() * 100:.1f}%)")
    print(f"  in a placeholder_group (identical geometry): {int(grouped.sum()):,} assets in "
          f"{assets.loc[grouped, 'placeholder_group'].nunique():,} groups; largest "
          f"{int(assets['placeholder_group_size'].max())}")
    print("  sigma_m by basis (assets, median m):")
    for basis, group in assets.groupby("sigma_basis"):
        print(f"    {basis:<44} {len(group):>9,}  {group['sigma_m'].median():>8,.0f}")
    fidelity = assets["route_fidelity"].dropna()
    if len(fidelity):
        print(f"  route_fidelity (pipelines): p10 {fidelity.quantile(.1):.2f} p50 {fidelity.median():.2f} p90 {fidelity.quantile(.9):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
