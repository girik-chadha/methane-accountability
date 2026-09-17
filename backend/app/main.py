"""FastAPI service for the unanswered-alert register.

Runtime dependencies: fastapi and uvicorn only. This module imports neither
pandas, numpy, shapely nor pyarrow (backend/tests/test_main.py scans for
them), and never touches data/raw/ or data/processed/. Everything it serves
is precomputed by etl/export_web.py into web/data.json (the map blob) and
web/cases.json (per-case detail). Both are loaded once at startup and the
process refuses to start if either is missing or the case count is wrong.

There is NO outbound communication anywhere in this service: no email, SMS,
webhook or outbound HTTP. Dossiers are displayed; a human decides.

Wherever costing appears, `capped` (the headline model) and `upper_bound`
(first-to-last span) are separate objects and are never merged.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from backend.app.config import (
    GAS_PRICE_BENCHMARK,
    GAS_PRICE_PERIOD,
    GAS_PRICE_USD_PER_MMBTU,
    GEM_ATTRIBUTION,
    GEM_LICENCE,
    MARS_ATTRIBUTION,
    MARS_LICENCE,
    MARS_PUBLICATION_LAG_DAYS_MAX,
    MARS_PUBLICATION_LAG_DAYS_MIN,
    MARS_SNAPSHOT_DATE,
    MARS_UNANSWERED_CASE_COUNT,
    METHANE_GWP100_FOSSIL_AR6,
    WEB_CASES_DETAIL_PATH,
    WEB_DATA_PATH,
    WEB_DIR,
    WEB_TIER_LABELS,
)

TIER_LABELS = frozenset(WEB_TIER_LABELS.values())
RATE_BASIS = (
    "Every daily figure is the mean rate at the time of detection, as if every leak ran at once. "
    "It is not an annual total and must not be multiplied out."
)


class DataError(RuntimeError):
    """The precomputed blob is missing or inconsistent. Fail at startup, never serve."""


def load_json(path: Path) -> dict:
    if not path.exists():
        raise DataError(f"{path} is missing; run etl/export_web.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _country_index(blob: dict) -> dict[str, dict]:
    return {c["n"]: c for c in blob["C"]}


def _leak_index(blob: dict) -> dict[str, dict]:
    """id -> leak record with its country attached. Ids must be unique."""
    index: dict[str, dict] = {}
    for country, leaks in blob["L"].items():
        for leak in leaks:
            if leak["id"] in index:
                raise DataError(f"duplicate case id {leak['id']}")
            index[leak["id"]] = {**leak, "country": country}
    return index


def _sum_block(details: dict[str, dict], key: str) -> dict[str, float | int]:
    """Total one costing model over the cases where it exists. `capped` and
    `upper_bound` are summed separately and never combined."""
    rows = [d[key] for d in details.values() if d[key]["ch4_kg"] is not None]
    return {
        "cases": len(rows),
        "ch4_kg": sum(r["ch4_kg"] for r in rows),
        "co2e_kg": sum(r["co2e_kg"] for r in rows),
        "usd": sum(r["usd"] for r in rows),
    }


def build_summary(blob: dict, detail: dict) -> dict:
    countries = [c for c in blob["C"] if c["cs"] > 0]
    leaks = [l for ls in blob["L"].values() for l in ls]
    costed = [l for l in leaks if l["f"] is not None]
    return {
        "snapshot_date": MARS_SNAPSHOT_DATE.isoformat(),
        "publication_lag_days": [MARS_PUBLICATION_LAG_DAYS_MIN, MARS_PUBLICATION_LAG_DAYS_MAX],
        "cases": len(leaks),
        "costed": len(costed),
        "not_costed": len(leaks) - len(costed),
        "tiers": {
            "named": sum(c["nm"] for c in countries),
            "asset": sum(c["as"] for c in countries),
            "unmapped": sum(c["um"] for c in countries),
        },
        "countries_with_cases": len(countries),
        "daily": {
            "ch4_t_per_day": round(sum(c["ch4"] for c in countries), 1),
            "usd_per_day": round(sum(c["usd"] for c in countries)),
            "basis": RATE_BASIS,
        },
        "totals": {
            "capped": {**_sum_block(detail["cases"], "capped"), "model": detail["models"]["capped"]},
            "upper_bound": {**_sum_block(detail["cases"], "upper_bound"), "model": detail["models"]["upper_bound"]},
        },
        "constants": {
            "gwp100_fossil_methane": METHANE_GWP100_FOSSIL_AR6,
            "gas_price_usd_per_mmbtu": GAS_PRICE_USD_PER_MMBTU,
            "gas_price_benchmark": f"{GAS_PRICE_BENCHMARK}, {GAS_PRICE_PERIOD}",
        },
        "attribution": {
            "mars": {"name": MARS_ATTRIBUTION, "licence": MARS_LICENCE},
            "gem": {"name": GEM_ATTRIBUTION, "licence": GEM_LICENCE},
        },
        "by_country": sorted(
            ({k: c[k] for k in ("n", "c", "cs", "nm", "as", "um", "usd", "ch4")} for c in countries),
            key=lambda c: -c["usd"],
        ),
    }


def create_app(
    data_path: Path = WEB_DATA_PATH,
    detail_path: Path = WEB_CASES_DETAIL_PATH,
    web_dir: Path = WEB_DIR,
    expected_cases: int = MARS_UNANSWERED_CASE_COUNT,
) -> FastAPI:
    blob = load_json(data_path)
    detail = load_json(detail_path)
    if set(blob) != {"C", "L", "M"} or set(detail) != {"models", "cases"}:
        raise DataError("web blobs do not have the expected top-level keys")
    leaks = _leak_index(blob)
    if len(leaks) != expected_cases:
        raise DataError(f"{data_path} holds {len(leaks)} cases, expected {expected_cases}")
    if set(detail["cases"]) != set(leaks):
        raise DataError("cases.json and data.json disagree on case ids")
    countries = _country_index(blob)
    country_by_fold = {n.casefold(): n for n in countries}
    summary = build_summary(blob, detail)

    app = FastAPI(title="Methane alert accountability register", docs_url="/api/docs", redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "cases": len(leaks), "snapshot_date": MARS_SNAPSHOT_DATE.isoformat()}

    @app.get("/api/summary")
    def api_summary() -> dict:
        return summary

    @app.get("/api/data")
    def api_data() -> dict:
        return blob

    @app.get("/api/cases")
    def api_cases(
        country: str | None = Query(default=None, description="Country name as shown on the map"),
        tier: str | None = Query(default=None, description="named | asset | unmapped"),
    ) -> dict:
        if tier is not None and tier not in TIER_LABELS:
            raise HTTPException(status_code=400, detail=f"tier must be one of {sorted(TIER_LABELS)}")
        rows = list(leaks.values())
        if country is not None:
            name = country_by_fold.get(country.casefold())
            if name is None:
                raise HTTPException(status_code=404, detail=f"unknown country {country!r}")
            rows = [r for r in rows if r["country"] == name]
        if tier is not None:
            rows = [r for r in rows if r["t"] == tier]
        return {"count": len(rows), "cases": rows}

    @app.get("/api/cases/{case_id}")
    def api_case(case_id: str) -> dict:
        leak = leaks.get(case_id)
        if leak is None:
            raise HTTPException(status_code=404, detail=f"no case {case_id!r}")
        d = detail["cases"][case_id]
        return {
            **leak,
            "detections": d["detections"],
            "rate": d["rate"],
            "capped": d["capped"],
            "upper_bound": d["upper_bound"],
            "models": detail["models"],
            "attribution": d["attribution"],
            "sector": d["sector"],
            "persistency_category": d["persistency_category"],
        }

    @app.get("/api/geojson")
    def api_geojson() -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                    "properties": {k: r[k] for k in ("id", "country", "t", "ty", "f", "ch", "u", "d", "nd", "op", "cand")},
                }
                for r in leaks.values()
            ],
        }

    if not (web_dir / "index.html").exists():
        raise DataError(f"{web_dir / 'index.html'} is missing")
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


app = create_app()
