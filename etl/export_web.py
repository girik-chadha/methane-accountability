"""Export the unanswered MARS backlog for the single-file web frontend.

Build-time only. This script runs the attribution over data/processed/
assets.parquet (4.7 M rows) and the costing over data/processed/mars.db, and
joins them into ONE ROW PER CASE, then writes web/data.json in the shape the
frontend reads. Production never runs it and never reads either input; it
serves only the JSON this script writes.

Output shape (every key is read by web/index.html; nothing else is emitted):
  {"C": [{n, c, r, bb, cs, usd, ch4, nm, as, um, fmax}, ...],   one per
        Natural Earth country, zero-case countries included so the map draws
   "L": {"<country n>": [{id, ty, d, nd, f, ch, u, lat, lon, sat, t, op, cand}]}}

Units: every mass column in the frame is kg, every rate kg/h. The ONLY
conversion to tonnes is `ch` (tonnes CH4 per day), done here at the
presentation boundary via costing.kg_to_tonnes. `f` is kg/h, `u` USD/day.

Usage:
    python etl/export_web.py --inspect     # print dtypes/head, write nothing
    python etl/export_web.py               # write web/data.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# One-off ETL script: may import from the backend package. The reverse
# direction (backend importing from etl) is forbidden.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.attribution import (  # noqa: E402
    AssetIndex,
    Attribution,
    attribute_all,
    load_cases,
)
from backend.app.config import (  # noqa: E402
    CH4_FLUXRATE_UNIT,
    COUNTRIES_WITHOUT_NE110M_POLYGON,
    COUNTRY_RINGS_PATH,
    DETECTION_ATTRIBUTABLE_WINDOW_DAYS,
    FEEDBACK_NO,
    MARS_DB_PATH,
    PLUMES_TABLE,
    MARS_SNAPSHOT_DATE,
    MARS_TO_GEM_COUNTRY_ALIASES,
    MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H,
    MARS_UNANSWERED_CASE_COUNT,
    PARTY_SHARE_PATTERN,
    SATELLITE_AGENCY_SEPARATOR,
    SOURCES_TABLE,
    TIER_ASSET_ONLY,
    TIER_OPERATOR_NAMED,
    TIER_UNMAPPED,
    WEB_CASES_DETAIL_PATH,
    WEB_BOOT_LINE,
    WEB_CANDIDATE_CARDS,
    WEB_DATA_PATH,
    WEB_DIR,
    WEB_STANDALONE_PATH,
    WEB_DATA_TARGET_BYTES,
    WEB_TIER_LABELS,
)
from backend.app.costing import (  # noqa: E402
    HOURS_PER_DAY,
    CaseCost,
    cost_cases,
    kg_to_tonnes,
    load_case_plumes,
    wasted_gas_value_usd,
)

INSPECT_HEAD_ROWS = 10
# Presentation rounding only. MARS publishes coordinates to 5 decimals.
COORD_DECIMALS = 5
TONNES_DECIMALS = 1


class CaseCountError(AssertionError):
    """The backlog filter produced a different number of cases than the
    hand-checked figure in config. Never weaken this: fix the filter."""


class CountryNameError(ValueError):
    """A MARS country name resolves to zero or several Natural Earth names."""


# --- stage 1: the cases frame ----------------------------------------------------


def load_source_state(db_path: Path = MARS_DB_PATH) -> pd.DataFrame:
    """Per-source state columns for the unanswered backlog, verbatim from
    the sources table (published column names, no conversion)."""
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql(
            "SELECT source_name, country, sector, source_type, persistency_category, "
            f"n_plumes_detected, last_plume_date FROM {SOURCES_TABLE} "
            "WHERE feedback_government = ? AND feedback_operator = ?",
            connection, params=(FEEDBACK_NO, FEEDBACK_NO),
        )
    return frame


def _attribution_row(result: Attribution) -> dict[str, object]:
    top = result.top
    asset = top.asset if top else None
    return {
        "source_name": result.source_name,
        "tier": result.tier,
        "n_candidates": result.n_candidates,
        "n_groups": result.n_groups,
        "n_parties": result.n_parties,
        "ambiguity": result.ambiguity,
        "radius_m": result.radius_m,
        "radius_capped": result.radius_capped,
        "top_named_party": asset.named_party if asset else None,
        "top_asset_kind": asset.asset_kind if asset else None,
        "top_asset_name": asset.name if asset else None,
        "top_source_dataset": asset.source_dataset if asset else None,
        "top_distance_m": top.distance_m if top else math.nan,
        "cands": candidate_cards(result),
    }


def candidate_cards(result: Attribution, limit: int = WEB_CANDIDATE_CARDS) -> list[dict[str, object]]:
    """The first `limit` candidates in the attribution's own order, as the
    frontend cards: k kind, n name, ds dataset, p named party (display form,
    None when the dataset names nobody), d distance m, th match threshold m.
    Every value is exported; the page invents nothing."""
    return [
        {
            "k": c.asset.asset_kind,
            "n": c.asset.name,
            "ds": c.asset.source_dataset,
            "p": display_party(c.asset.named_party),
            "d": _number(c.distance_m, 1),
            "th": _number(c.threshold_m, 1),
        }
        for c in result.candidates[:limit]
    ]


def load_detection_rows(db_path: Path = MARS_DB_PATH) -> pd.DataFrame:
    """Every plume of every unanswered source: tile_date, satellite, and the
    kg/h rate (null where MARS reported none, e.g. VIIRS rows)."""
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql(
            f"SELECT p.source_name, p.tile_date, p.satellite, p.ch4_fluxrate FROM {PLUMES_TABLE} p "
            f"JOIN {SOURCES_TABLE} s ON p.source_name = s.source_name "
            "WHERE s.feedback_government = ? AND s.feedback_operator = ? ORDER BY p.source_name, p.tile_date",
            connection, params=(FEEDBACK_NO, FEEDBACK_NO),
        )


def detection_rows(plumes: pd.DataFrame) -> pd.Series:
    """source_name -> [[time to the minute, instrument, kg/h or None], ...] in
    time order. A missing rate is None, never zero, never filled in."""
    frame = plumes.assign(detected_at=pd.to_datetime(plumes["tile_date"])).sort_values(["source_name", "detected_at"])
    return (
        frame.groupby("source_name")
        .apply(lambda g: [[t.strftime("%Y-%m-%dT%H:%M"), instrument_name(sat), _number(rate)]
                          for t, sat, rate in zip(g["detected_at"], g["satellite"], g["ch4_fluxrate"])],
               include_groups=False)
        .rename("det")
    )


def _cost_row(cost: CaseCost, first_detection: pd.Timestamp, last_detection: pd.Timestamp) -> dict[str, object]:
    return {
        "source_name": cost.source_name,
        "n_detections": cost.n_detections,
        "first_detection": first_detection,
        "last_detection": last_detection,
        "days_since_first_detection": (MARS_SNAPSHOT_DATE - first_detection.date()).days,
        "days_since_last_detection": cost.days_since_last_detection,
        "mean_fluxrate_kg_per_h": cost.mean_fluxrate_kg_per_h,
        "kg_per_day": cost.kg_per_day,
        "usd_per_day": cost.usd_per_day,
        "capped_duration_h": cost.capped_duration_h,
        "capped_ch4_kg": cost.capped_ch4_kg,
        "capped_co2e_kg": cost.capped_co2e_kg,
        "capped_usd": cost.capped_usd,
        "upper_bound_duration_h": cost.duration_h,
        "upper_bound_ch4_kg": cost.ch4_kg,
        "upper_bound_co2e_kg": cost.co2e_kg,
        "upper_bound_usd": cost.wasted_gas_usd,
    }


def build_cases_frame() -> tuple[pd.DataFrame, list[Attribution]]:
    """One row per unanswered case: source state + attribution + costing.

    Returns the frame and the full Attribution objects (with candidate sets)
    in the same order, for the per-case detail the export will need.
    Raises CaseCountError unless the row count equals the hand-checked
    MARS_UNANSWERED_CASE_COUNT.
    """
    state = load_source_state()
    sources, case_plumes = load_cases()
    index = AssetIndex.from_parquet()
    results = attribute_all(index, sources, case_plumes)
    attribution = pd.DataFrame([_attribution_row(r) for r in results])

    satellites = (
        case_plumes.groupby("source_name")["satellite"]
        .agg(lambda s: sorted(set(s)))
        .rename("satellites")
    )

    plumes = load_case_plumes()
    span = plumes.groupby("source_name")["detected_at"].agg(["min", "max"])
    costs = cost_cases(plumes)
    costing = pd.DataFrame([
        _cost_row(c, span.loc[c.source_name, "min"], span.loc[c.source_name, "max"]) for c in costs
    ])

    detections = detection_rows(load_detection_rows())

    frame = (
        sources[["source_name", "lat", "lon"]]
        .merge(state, on="source_name", how="left", validate="one_to_one")
        .merge(satellites, on="source_name", how="left", validate="one_to_one")
        .merge(detections, on="source_name", how="left", validate="one_to_one")
        .merge(attribution, on="source_name", how="left", validate="one_to_one")
        .merge(costing, on="source_name", how="left", validate="one_to_one")
    )
    check_case_count(len(frame))
    if frame["tier"].isna().any() or frame["n_detections"].isna().any():
        raise ValueError("a case is missing its attribution or costing row")
    return frame, results


def check_case_count(count: int) -> None:
    """The correctness gate. An explicit raise, not `assert`, so -O cannot strip it."""
    if count != MARS_UNANSWERED_CASE_COUNT:
        raise CaseCountError(
            f"expected {MARS_UNANSWERED_CASE_COUNT} unanswered cases, got {count}; the backlog filter is wrong"
        )


# --- stage 2: frame -> frontend payload -----------------------------------------


def display_party(named_party: str | None) -> str | None:
    """Strip GEM share brackets for display; keep every party and the "; " order."""
    if named_party is None or (isinstance(named_party, float) and math.isnan(named_party)):
        return None
    cleaned = re.sub(PARTY_SHARE_PATTERN, "", named_party).strip()
    return cleaned or None


def instrument_name(satellite: str) -> str:
    """'Sentinel-2 - ESA' -> 'Sentinel-2'; a string with no agency is unchanged."""
    return satellite.split(SATELLITE_AGENCY_SEPARATOR, 1)[0].strip()


def tier_label(tier: str) -> str:
    try:
        return WEB_TIER_LABELS[tier]
    except KeyError:
        raise ValueError(f"tier {tier!r} has no frontend label; add it to WEB_TIER_LABELS") from None


def web_tier(tier: str, n_parties: int) -> str:
    """Frontend label. OPERATOR_NAMED means the TOP candidate names someone;
    the frontend's "named" means a name can be put on the pill. They differ
    when the candidate set names two or more distinct parties on different
    assets: the geometry cannot choose between them, so nobody is named and
    the case is shown as asset-only. Co-owners of ONE asset are a single
    party string and are not affected."""
    label = tier_label(tier)
    if label == WEB_TIER_LABELS[TIER_OPERATOR_NAMED] and n_parties >= 2:
        return WEB_TIER_LABELS[TIER_ASSET_ONLY]
    return label


def load_country_rings(path: Path = COUNTRY_RINGS_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"No country rings at {path}")
    return json.loads(path.read_text())["countries"]


def resolve_country(mars_name: str, ring_names: set[str]) -> str:
    """Natural Earth name for a MARS country name. Exact match first, then
    the documented aliases (case-insensitive); exactly one hit or raise."""
    if mars_name in ring_names:
        return mars_name
    if mars_name in COUNTRIES_WITHOUT_NE110M_POLYGON:
        return mars_name
    folded = {n.casefold(): n for n in ring_names}
    hits = sorted({folded[a.casefold()] for a in MARS_TO_GEM_COUNTRY_ALIASES.get(mars_name, ()) if a.casefold() in folded})
    if len(hits) != 1:
        raise CountryNameError(f"MARS country {mars_name!r} resolves to {hits} in the country rings; need exactly one")
    return hits[0]


def _number(value: object, decimals: int | None = None) -> float | int | None:
    """None for NaN/None; otherwise rounded. Null is never replaced by zero."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value)) if decimals is None else round(float(value), decimals)


def leak_record(row: pd.Series) -> dict[str, object]:
    """One frontend leak. f kg/h, ch TONNES CH4 per day (the only tonne
    conversion in the pipeline), u USD per day. Null flux stays null."""
    kg_per_day = row["kg_per_day"]
    return {
        "id": row["source_name"],
        "ty": row["source_type"],
        "d": int(row["days_since_last_detection"]),
        "nd": int(row["n_detections"]),
        "f": _number(row["mean_fluxrate_kg_per_h"]),
        "ch": _number(kg_to_tonnes(kg_per_day), TONNES_DECIMALS) if not pd.isna(kg_per_day) else None,
        "u": _number(row["usd_per_day"]),
        "lat": round(float(row["lat"]), COORD_DECIMALS),
        "lon": round(float(row["lon"]), COORD_DECIMALS),
        "sat": [instrument_name(s) for s in row["satellites"]],
        "t": web_tier(row["tier"], int(row["n_parties"])),
        "op": display_party(row["top_named_party"]) if web_tier(row["tier"], int(row["n_parties"])) == WEB_TIER_LABELS[TIER_OPERATOR_NAMED] else None,
        "cand": int(row["n_candidates"]),
        "det": _checked_detections(row),
        "cands": list(row["cands"]),
        "cap": _duration_block(row, "capped"),
        "ub": _duration_block(row, "upper_bound"),
    }


def _duration_block(row: pd.Series, prefix: str) -> dict[str, float] | None:
    """{h: hours credited, t: TONNES CH4 over those hours} for one costing
    model, or None where the model gives nothing (no flux; or a single
    detection for the first-to-last span). Tonnes only at this boundary."""
    hours, kg = row[f"{prefix}_duration_h"], row[f"{prefix}_ch4_kg"]
    if pd.isna(hours) or pd.isna(kg):
        return None
    return {"h": _number(hours, 1), "t": _number(kg_to_tonnes(kg), TONNES_DECIMALS)}


def _checked_detections(row: pd.Series) -> list[list[object]]:
    """The exported detection rows must be exactly the counted detections;
    a mismatch means the plumes query and the costing disagree, so raise."""
    det = list(row["det"])
    if len(det) != int(row["n_detections"]):
        raise ValueError(f"{row['source_name']}: {len(det)} detection rows but n_detections={row['n_detections']}")
    if not det:
        raise ValueError(f"{row['source_name']} has no detection rows")
    return det


def build_payload(frame: pd.DataFrame, rings: list[dict]) -> dict[str, object]:
    """Frame + rings -> {"C": [...], "L": {...}}. Sums are taken over the
    unrounded kg/USD values and rounded once."""
    ring_names = {c["n"] for c in rings}
    frame = frame.assign(ne_name=frame["country"].map(lambda n: resolve_country(n, ring_names)))

    leaks: dict[str, list[dict[str, object]]] = {}
    for ne_name, group in frame.groupby("ne_name", sort=False):
        leaks[str(ne_name)] = [leak_record(row) for _, row in group.iterrows()]

    countries: list[dict[str, object]] = []
    seen: set[str] = set()

    def country_record(name: str, continent: str, r: list, bb: list[float]) -> dict[str, object]:
        group = frame[frame["ne_name"] == name]
        tiers = pd.Series([web_tier(t, int(n)) for t, n in zip(group["tier"], group["n_parties"])], dtype=object)
        return {
            "n": name, "c": continent, "r": r, "bb": bb,
            "cs": int(len(group)),
            "usd": _number(group["usd_per_day"].sum()) if len(group) else 0,
            "ch4": _number(kg_to_tonnes(group["kg_per_day"].sum()), TONNES_DECIMALS) if len(group) else 0,
            "nm": int((tiers == WEB_TIER_LABELS[TIER_OPERATOR_NAMED]).sum()),
            "as": int((tiers == WEB_TIER_LABELS[TIER_ASSET_ONLY]).sum()),
            "um": int((tiers == WEB_TIER_LABELS[TIER_UNMAPPED]).sum()),
            "fmax": _number(group["mean_fluxrate_kg_per_h"].max()) if group["mean_fluxrate_kg_per_h"].notna().any() else 0,
        }

    for ring in rings:
        countries.append(country_record(ring["n"], ring["c"], ring["r"], ring["bb"]))
        seen.add(ring["n"])
    for name, continent in COUNTRIES_WITHOUT_NE110M_POLYGON.items():
        if name in seen:
            raise ValueError(f"{name} is listed as absent from the rings but is present")
        group = frame[frame["ne_name"] == name]
        if group.empty:
            continue
        bb = [float(group["lon"].min()), float(group["lat"].min()), float(group["lon"].max()), float(group["lat"].max())]
        countries.append(country_record(name, continent, [], [round(v, COORD_DECIMALS) for v in bb]))

    total = sum(c["cs"] for c in countries)
    check_case_count(total)
    if sum(len(v) for v in leaks.values()) != total:
        raise ValueError("leak records and country case counts disagree")
    return {"C": countries, "L": leaks, "M": blob_meta()}


def blob_meta() -> dict[str, object]:
    """Constants the page states next to its numbers, from config, never
    typed into the script: the attributable window and the snapshot date."""
    return {"window_days": DETECTION_ATTRIBUTABLE_WINDOW_DAYS, "snapshot": MARS_SNAPSHOT_DATE.isoformat()}


def write_json(payload: dict[str, object], path: Path) -> int:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def write_payload(payload: dict[str, object], path: Path = WEB_DATA_PATH) -> int:
    return write_json(payload, path)


# --- stage 2b: standalone page, the offline fallback --------------------------------

class StandaloneError(RuntimeError):
    """index.html does not carry exactly one boot line, so it cannot be inlined."""


def build_standalone(index_html: str, payload_text: str) -> str:
    """index.html with the data blob inlined in place of WEB_BOOT_LINE.

    Byte-identical UI: nothing but that one line changes. The script stays a
    module (an inline module runs from file:// with no server). "</" inside the
    blob is written as "<\\/" so no value could ever close the script tag; the
    JavaScript string is unchanged by that escape."""
    n = index_html.count(WEB_BOOT_LINE)
    if n != 1:
        raise StandaloneError(f"expected exactly one boot line in index.html, found {n}")
    declaration = WEB_BOOT_LINE.split(" = await ")[0]  # "const {C, L, M}", from the same constant
    inlined = declaration + " = " + payload_text.replace("</", "<\\/") + ";"
    return index_html.replace(WEB_BOOT_LINE, inlined)


def write_standalone(
    index_path: Path = WEB_DIR / "index.html",
    data_path: Path = WEB_DATA_PATH,
    out_path: Path = WEB_STANDALONE_PATH,
) -> int:
    """Regenerate web/standalone.html from the committed page and blob. Needs
    neither the database nor the asset table, so it runs in well under a second."""
    html = build_standalone(index_path.read_text(encoding="utf-8"), data_path.read_text(encoding="utf-8"))
    out_path.write_text(html, encoding="utf-8")
    return len(html.encode("utf-8"))


# --- stage 3: per-case detail for the drill-down API -------------------------------

CAPPED_MODEL = (
    f"Each detection credited with one {DETECTION_ATTRIBUTABLE_WINDOW_DAYS:g}-day window centred on it "
    "(the measured median gap between consecutive detections), overlapping windows merged, at the mean "
    "detected rate. The headline model."
)
UPPER_BOUND_MODEL = (
    "Continuous emission at the mean detected rate from the first to the last detection. "
    "Null for a single detection. Overstates intermittent sources; an UPPER BOUND, not an estimate."
)


def _mass_block(row: pd.Series, prefix: str) -> dict[str, object]:
    """kg values as stored, plus the tonne view at this presentation boundary."""
    ch4_kg = row[f"{prefix}_ch4_kg"]
    return {
        "duration_h": _number(row[f"{prefix}_duration_h"], 1),
        "ch4_kg": _number(ch4_kg),
        "ch4_t": _number(kg_to_tonnes(ch4_kg), TONNES_DECIMALS) if not pd.isna(ch4_kg) else None,
        "co2e_kg": _number(row[f"{prefix}_co2e_kg"]),
        "co2e_t": _number(kg_to_tonnes(row[f"{prefix}_co2e_kg"]), TONNES_DECIMALS) if not pd.isna(row[f"{prefix}_co2e_kg"]) else None,
        "usd": _number(row[f"{prefix}_usd"]),
    }


def candidate_record(candidate) -> dict[str, object]:
    asset = candidate.asset
    return {
        "kind": asset.asset_kind,
        "name": asset.name,
        "dataset": asset.source_dataset,
        "named_party": display_party(asset.named_party),
        "distance_m": _number(candidate.distance_m, 1),
        "threshold_m": _number(candidate.threshold_m, 1),
        "sigma_m": _number(candidate.sigma_m, 1),
        "inside": bool(candidate.inside),
        "compatible": bool(candidate.compatible),
        "route_traced": bool(candidate.route_traced),
    }


def detail_record(row: pd.Series, result: Attribution) -> dict[str, object]:
    """Everything the drill-down needs for one case. `capped` and
    `upper_bound` are separate objects and are never merged or summed."""
    if result.source_name != row["source_name"]:
        raise ValueError("attribution result does not match the frame row")
    shown = web_tier(row["tier"], int(row["n_parties"]))
    return {
        "id": row["source_name"],
        "country": row["ne_name"],
        "mars_country": row["country"],
        "sector": row["sector"],
        "source_type": row["source_type"],
        "persistency_category": row["persistency_category"],
        "lat": round(float(row["lat"]), COORD_DECIMALS),
        "lon": round(float(row["lon"]), COORD_DECIMALS),
        "detections": {
            "n": int(row["n_detections"]),
            "first": row["first_detection"].isoformat(),
            "last": row["last_detection"].isoformat(),
            "days_since_first": int(row["days_since_first_detection"]),
            "days_since_last": int(row["days_since_last_detection"]),
            "satellites": list(row["satellites"]),
        },
        "rate": {
            "reported": not pd.isna(row["mean_fluxrate_kg_per_h"]),
            "mean_fluxrate_kg_per_h": _number(row["mean_fluxrate_kg_per_h"], 1),
            "kg_per_day": _number(row["kg_per_day"]),
            "ch4_t_per_day": _number(kg_to_tonnes(row["kg_per_day"]), TONNES_DECIMALS) if not pd.isna(row["kg_per_day"]) else None,
            "usd_per_day": _number(row["usd_per_day"]),
        },
        "capped": _mass_block(row, "capped"),
        "upper_bound": _mass_block(row, "upper_bound"),
        "attribution": {
            "tier": row["tier"],
            "shown_as": shown,
            "likely_operator": display_party(row["top_named_party"]) if shown == WEB_TIER_LABELS[TIER_OPERATOR_NAMED] else None,
            "n_candidates": int(row["n_candidates"]),
            "n_groups": int(row["n_groups"]),
            "n_parties": int(row["n_parties"]),
            "ambiguity": row["ambiguity"],
            "search_mode": result.search_mode,
            "plume_sigma_m": _number(result.plume_sigma_m, 1),
            "radius_capped": bool(row["radius_capped"]),
            "candidates": [candidate_record(c) for c in result.candidates],
        },
    }


def build_detail(frame: pd.DataFrame, results: list[Attribution], rings: list[dict]) -> dict[str, object]:
    ring_names = {c["n"] for c in rings}
    frame = frame.assign(ne_name=frame["country"].map(lambda n: resolve_country(n, ring_names)))
    by_name = {r.source_name: r for r in results}
    cases = {row["source_name"]: detail_record(row, by_name[row["source_name"]]) for _, row in frame.iterrows()}
    check_case_count(len(cases))
    return {"models": {"capped": CAPPED_MODEL, "upper_bound": UPPER_BOUND_MODEL}, "cases": cases}


# --- inspection --------------------------------------------------------------------


def check_units(frame: pd.DataFrame) -> None:
    """Trace every rate and mass column in the frame back to its unit.

    MARS carries kg/h (ch4_fluxrate) and TONNES (total_emission) in one
    table. The frame must be built from the kg/h column only, and every
    derived figure must be reproducible from it in kg. Raises on any break."""
    flux = frame["mean_fluxrate_kg_per_h"]
    median = float(flux.median())
    low, high = MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H
    if not low <= median <= high:
        raise ValueError(f"flux median {median:g} outside {low:g}-{high:g} kg/h: unit error")
    with_flux = frame.dropna(subset=["mean_fluxrate_kg_per_h"])
    if not (with_flux["kg_per_day"] == with_flux["mean_fluxrate_kg_per_h"] * HOURS_PER_DAY).all():
        raise ValueError("kg_per_day is not flux_kg_per_h x 24")
    recomputed = with_flux["kg_per_day"].map(wasted_gas_value_usd)
    if not ((with_flux["usd_per_day"] - recomputed).abs() < 1e-6).all():
        raise ValueError("usd_per_day is not wasted_gas_value_usd(kg_per_day)")
    tonne_columns = [c for c in frame.columns if "tonne" in c.lower() or "total_emission" in c.lower()]
    if tonne_columns:
        raise ValueError(f"tonne-denominated columns must not exist in the frame: {tonne_columns}")
    print("UNITS")
    print(f"  ch4_fluxrate is {CH4_FLUXRATE_UNIT} (publisher PDF); total_emission (tonnes) is never read by this export")
    print(f"  mean_fluxrate_kg_per_h median {median:,.0f}, inside the MARS typical band {low:,.0f}-{high:,.0f} kg/h")
    print(f"  kg_per_day == flux x {HOURS_PER_DAY:g} for all {len(with_flux):,} cases with a flux")
    print(f"  usd_per_day == wasted_gas_value_usd(kg_per_day) for all {len(with_flux):,}")
    print("  no tonne-denominated column in the frame; tonnes appear only at export via kg_to_tonnes")
    print()


def inspect(frame: pd.DataFrame) -> None:
    print(f"CASES FRAME: {len(frame):,} rows x {len(frame.columns)} columns\n")
    check_units(frame)
    print("DTYPES")
    print(frame.dtypes.to_string())
    print("\nNULLS (columns with any)")
    nulls = frame.isna().sum()
    print(nulls[nulls > 0].to_string() if (nulls > 0).any() else "  none")
    print(f"\nHEAD({INSPECT_HEAD_ROWS}), transposed so every column is visible")
    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_colwidth", 60):
        print(frame.head(INSPECT_HEAD_ROWS).T.to_string())
    print("\nVALUE COUNTS")
    for column in ("tier", "ambiguity", "top_asset_kind", "top_source_dataset", "persistency_category"):
        print(f"\n  {column}")
        print(frame[column].value_counts(dropna=False).to_string())
    print("\n  satellites (distinct strings)")
    print(sorted({s for row in frame["satellites"] for s in row}))
    print("\n  source_type")
    print(frame["source_type"].value_counts().to_string())


def report(payload: dict[str, object], size_bytes: int, frame: pd.DataFrame | None = None) -> None:
    countries = payload["C"]
    leaks = payload["L"]
    n_leaks = sum(len(v) for v in leaks.values())
    with_cases = [c for c in countries if c["cs"] > 0]
    no_flux = sum(1 for v in leaks.values() for l in v if l["f"] is None)
    print(f"Wrote {WEB_DATA_PATH.relative_to(PROJECT_ROOT)}: {size_bytes:,} bytes "
          f"({size_bytes / 1e6:.2f} MB; target under {WEB_DATA_TARGET_BYTES / 1e6:.0f} MB "
          f"{'OK' if size_bytes < WEB_DATA_TARGET_BYTES else 'EXCEEDED'})")
    print(f"  leaks {n_leaks:,} (gate: {MARS_UNANSWERED_CASE_COUNT:,}); countries {len(countries)} drawn, "
          f"{len(with_cases)} with cases")
    print(f"  tiers shown: named {sum(c['nm'] for c in countries):,}, asset {sum(c['as'] for c in countries):,}, "
          f"unmapped {sum(c['um'] for c in countries):,}")
    joint = sum(1 for v in leaks.values() for l in v if l["op"] and "; " in l["op"])
    print(f"  named cases whose single top asset has several co-owners (joined with '; '): {joint:,}")
    print(f"  leaks with no reported flux (f, ch, u null): {no_flux:,}")
    print(f"  daily totals over cases with a flux: {sum(c['ch4'] for c in countries):,.1f} t CH4/day, "
          f"USD {sum(c['usd'] for c in countries):,.0f}/day")
    if frame is not None:
        relabelled = int(((frame["tier"] == TIER_OPERATOR_NAMED) & (frame["n_parties"] >= 2)).sum())
        print(f"  attribution OPERATOR_NAMED {int((frame['tier'] == TIER_OPERATOR_NAMED).sum()):,}, of which "
              f"{relabelled:,} name 2+ distinct parties on different assets and are shown as asset-only with op null")
    ringless = [c["n"] for c in countries if not c["r"]]
    if ringless:
        print(f"  countries without a polygon (list-only): {ringless}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inspect", action="store_true", help="print dtypes and head of the cases frame; write nothing")
    parser.add_argument("--standalone", action="store_true",
                        help="rebuild web/standalone.html from the committed index.html and data.json only; "
                             "no database, no asset table, nothing else written")
    args = parser.parse_args(argv)
    if args.standalone:
        _report_standalone(write_standalone())
        return 0
    frame, results = build_cases_frame()
    if args.inspect:
        inspect(frame)
        return 0
    check_units(frame)
    rings = load_country_rings()
    payload = build_payload(frame, rings)
    size = write_payload(payload)
    report(payload, size, frame)
    detail_size = write_json(build_detail(frame, results, rings), WEB_CASES_DETAIL_PATH)
    print(f"Wrote {WEB_CASES_DETAIL_PATH.relative_to(PROJECT_ROOT)}: {detail_size:,} bytes "
          f"({detail_size / 1e6:.2f} MB), per-case detail with capped and upper_bound kept separate")
    _report_standalone(write_standalone())
    return 0


def _report_standalone(size: int) -> None:
    print(f"Wrote {WEB_STANDALONE_PATH.relative_to(PROJECT_ROOT)}: {size:,} bytes ({size / 1e6:.2f} MB), "
          "index.html with data.json inlined; opens from disk with no server")


if __name__ == "__main__":
    sys.exit(main())
