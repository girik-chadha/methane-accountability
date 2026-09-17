"""Tests for the FastAPI service. A tiny fixture blob, plus one check
against the committed real blob. No pandas anywhere near backend/app/main.py."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.config import WEB_BOOT_LINE
from backend.app.main import DataError, create_app

CAPPED = {"duration_h": 266.4, "ch4_kg": 660672, "ch4_t": 660.7, "co2e_kg": 19688026, "co2e_t": 19688.0, "usd": 96661}
UPPER = {"duration_h": 3.1, "ch4_kg": 238676, "ch4_t": 238.7, "co2e_kg": 7112546, "co2e_t": 7112.5, "usd": 34920}
NONE_BLOCK = {"duration_h": None, "ch4_kg": None, "ch4_t": None, "co2e_kg": None, "co2e_t": None, "usd": None}


def _leak(id_: str, t: str, f: int | None, op: str | None) -> dict:
    ch = None if f is None else round(f * 24 / 1000, 1)
    u = None if f is None else round(f * 24 * 0.1463)
    return {"id": id_, "ty": "Flare", "d": 100, "nd": 2, "f": f, "ch": ch, "u": u,
            "lat": 50.0, "lon": 30.0, "sat": ["EMIT"], "t": t, "op": op, "cand": 1}


def _detail(id_: str, capped: dict, upper: dict) -> dict:
    return {"id": id_, "country": "Russia", "mars_country": "Russian Federation", "sector": "Oil and Gas",
            "source_type": "Flare", "persistency_category": "absent", "lat": 50.0, "lon": 30.0,
            "detections": {"n": 2, "first": "2026-01-01T00:00:00", "last": "2026-01-02T00:00:00",
                           "days_since_first": 101, "days_since_last": 100, "satellites": ["EMIT - NASA"]},
            "rate": {"reported": True, "mean_fluxrate_kg_per_h": 2480.0, "kg_per_day": 59520, "ch4_t_per_day": 59.5, "usd_per_day": 8708},
            "capped": capped, "upper_bound": upper,
            "attribution": {"tier": "OPERATOR_NAMED", "shown_as": "named", "likely_operator": "A", "n_candidates": 1,
                            "n_groups": 1, "n_parties": 1, "ambiguity": "none", "search_mode": "symmetric_zero_offset",
                            "plume_sigma_m": 60.0, "radius_capped": False, "candidates": []}}


@pytest.fixture
def site(tmp_path: Path) -> tuple[Path, Path, Path]:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>t</title>ok")
    blob = {
        "C": [
            {"n": "Russia", "c": "Europe", "r": [[[30, 50], [31, 50], [31, 51], [30, 50]]], "bb": [30, 50, 31, 51],
             "cs": 2, "usd": 8708, "ch4": 59.5, "nm": 1, "as": 0, "um": 1, "fmax": 2480},
            {"n": "Bahrain", "c": "Asia", "r": [], "bb": [50.5, 26.1, 50.5, 26.1],
             "cs": 1, "usd": 0, "ch4": 0, "nm": 0, "as": 1, "um": 0, "fmax": 0},
            {"n": "Fiji", "c": "Oceania", "r": [[[178, -17], [179, -17], [179, -18], [178, -17]]], "bb": [178, -18, 179, -17],
             "cs": 0, "usd": 0, "ch4": 0, "nm": 0, "as": 0, "um": 0, "fmax": 0},
        ],
        "L": {
            "Russia": [_leak("RUS_S_001", "named", 2480, "A"), _leak("RUS_S_002", "unmapped", None, None)],
            "Bahrain": [_leak("BHR_S_001", "asset", None, None)],
        },
        "M": {"window_days": 11.1, "snapshot": "2026-09-15"},
    }
    detail = {"models": {"capped": "capped model", "upper_bound": "upper bound model"},
              "cases": {"RUS_S_001": _detail("RUS_S_001", CAPPED, UPPER),
                        "RUS_S_002": _detail("RUS_S_002", NONE_BLOCK, NONE_BLOCK),
                        "BHR_S_001": _detail("BHR_S_001", NONE_BLOCK, NONE_BLOCK)}}
    data_path, detail_path = web / "data.json", web / "cases.json"
    data_path.write_text(json.dumps(blob))
    detail_path.write_text(json.dumps(detail))
    return data_path, detail_path, web


@pytest.fixture
def client(site) -> TestClient:
    data_path, detail_path, web = site
    return TestClient(create_app(data_path, detail_path, web, expected_cases=3))


def test_healthz_reports_the_case_count(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok", "cases": 3, "snapshot_date": "2026-09-15"}


def test_summary_counts_costed_separately_and_keeps_models_apart(client: TestClient) -> None:
    s = client.get("/api/summary").json()
    assert (s["cases"], s["costed"], s["not_costed"]) == (3, 1, 2)
    assert s["tiers"] == {"named": 1, "asset": 1, "unmapped": 1}
    assert s["countries_with_cases"] == 2
    assert s["daily"]["ch4_t_per_day"] == 59.5 and s["daily"]["usd_per_day"] == 8708
    assert "not an annual total" in s["daily"]["basis"]
    assert s["totals"]["capped"]["ch4_kg"] == 660672 and s["totals"]["capped"]["cases"] == 1
    assert s["totals"]["upper_bound"]["ch4_kg"] == 238676 and s["totals"]["upper_bound"]["cases"] == 1
    assert s["totals"]["capped"]["model"] != s["totals"]["upper_bound"]["model"]
    assert s["attribution"]["mars"]["licence"] == "CC BY-NC-SA 4.0" and s["attribution"]["gem"]["licence"] == "CC BY 4.0"
    assert [c["n"] for c in s["by_country"]] == ["Russia", "Bahrain"]


def test_cases_filters_by_country_and_tier(client: TestClient) -> None:
    assert client.get("/api/cases").json()["count"] == 3
    r = client.get("/api/cases", params={"country": "russia"}).json()
    assert r["count"] == 2 and all(c["country"] == "Russia" for c in r["cases"])
    r = client.get("/api/cases", params={"country": "Russia", "tier": "unmapped"}).json()
    assert [c["id"] for c in r["cases"]] == ["RUS_S_002"]
    assert client.get("/api/cases", params={"tier": "HIGH"}).status_code == 400
    assert client.get("/api/cases", params={"country": "Atlantis"}).status_code == 404


def test_case_detail_has_capped_and_upper_bound_as_separate_objects(client: TestClient) -> None:
    c = client.get("/api/cases/RUS_S_001").json()
    assert c["capped"] == CAPPED and c["upper_bound"] == UPPER
    assert c["capped"]["ch4_kg"] != c["upper_bound"]["ch4_kg"]
    assert set(c["models"]) == {"capped", "upper_bound"}
    assert c["op"] == "A" and c["t"] == "named" and c["country"] == "Russia"
    assert c["attribution"]["likely_operator"] == "A"
    assert client.get("/api/cases/NOPE").status_code == 404
    uncosted = client.get("/api/cases/RUS_S_002").json()
    assert uncosted["f"] is None and uncosted["capped"] == NONE_BLOCK and uncosted["upper_bound"] == NONE_BLOCK


def test_geojson_is_one_point_per_case(client: TestClient) -> None:
    g = client.get("/api/geojson").json()
    assert g["type"] == "FeatureCollection" and len(g["features"]) == 3
    f = next(x for x in g["features"] if x["properties"]["id"] == "RUS_S_001")
    assert f["geometry"] == {"type": "Point", "coordinates": [30.0, 50.0]}
    assert f["properties"]["op"] == "A" and f["properties"]["country"] == "Russia"


def test_data_and_index_are_served(client: TestClient) -> None:
    d = client.get("/api/data").json()
    assert set(d) == {"C", "L", "M"} and len(d["C"]) == 3
    r = client.get("/")
    assert r.status_code == 200 and "ok" in r.text


def test_missing_blob_or_wrong_count_refuses_to_start(site, tmp_path: Path) -> None:
    data_path, detail_path, web = site
    with pytest.raises(DataError):
        create_app(tmp_path / "absent.json", detail_path, web, expected_cases=3)
    with pytest.raises(DataError):
        create_app(data_path, detail_path, web, expected_cases=1394)


def test_main_imports_no_dataframe_or_geometry_library() -> None:
    """Runtime is fastapi + uvicorn. The build inputs never reach production."""
    forbidden = {"pandas", "numpy", "shapely", "pyarrow", "geopandas", "pyogrio", "pyproj", "fiona", "sqlite3"}
    tree = ast.parse(Path(main.__file__).read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert name.split(".")[0] not in forbidden, f"main.py imports {name}"


def test_committed_blob_serves_the_full_backlog() -> None:
    """The real artefacts must load with the hand-checked count."""
    client = TestClient(main.app)
    assert client.get("/healthz").json()["cases"] == 1394
    s = client.get("/api/summary").json()
    assert s["cases"] == 1394 and s["costed"] + s["not_costed"] == 1394
    assert s["totals"]["capped"]["cases"] >= s["totals"]["upper_bound"]["cases"]
    one = client.get("/api/cases/VEN_S_039").json()
    assert one["f"] == 2480 and one["ch"] == 59.5 and one["capped"]["ch4_t"] == 660.7 and one["upper_bound"]["ch4_kg"] is None


def test_committed_index_boots_from_the_api_not_inlined_data() -> None:
    """Task 4: the page holds no dataset of its own. It fetches /api/data once
    at boot, inside a module script so the top-level await is legal."""
    html = (Path(main.__file__).resolve().parents[2] / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count(WEB_BOOT_LINE) == 1
    assert '<script type="module">' in html
    assert "const DATA" not in html and "DATA.countries" not in html and "DATA.leaks" not in html
    assert len(html.encode("utf-8")) < 100_000, "the demo blob is back in the page"
