"""Hand-checked tests for etl/export_web.py. No parquet, no database."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backend.app.config import MARS_UNANSWERED_CASE_COUNT
from etl.export_web import (
    CaseCountError,
    CountryNameError,
    build_payload,
    candidate_cards,
    detection_rows,
    check_case_count,
    display_party,
    instrument_name,
    leak_record,
    resolve_country,
    detail_record,
    tier_label,
    web_tier,
)
from backend.app.attribution import Asset, Attribution, Candidate, Leak
import shapely

RINGS = [
    {"n": "Russia", "c": "Europe", "r": [[[30.0, 50.0], [40.0, 50.0], [40.0, 60.0], [30.0, 50.0]]], "bb": [30.0, 50.0, 40.0, 60.0]},
    {"n": "Fiji", "c": "Oceania", "r": [[[178.0, -17.0], [179.0, -17.0], [179.0, -18.0], [178.0, -17.0]]], "bb": [178.0, -18.0, 179.0, -17.0]},
]


def _row(**overrides) -> pd.Series:
    base = {
        "source_name": "RUS_S_001", "country": "Russian Federation", "source_type": "Flare",
        "days_since_last_detection": 240, "n_detections": 1, "mean_fluxrate_kg_per_h": 2480.0,
        "kg_per_day": 59520.0, "usd_per_day": 8708.169873, "lat": 8.406134, "lon": -64.794171,
        "satellites": ["EMIT - NASA", "Sentinel-2 - ESA"], "tier": "OPERATOR_NAMED",
        "top_named_party": "TC Energy Corp [100.00%]", "n_candidates": 3, "n_parties": 1,
        "ne_name": "Russia", "sector": "Oil and Gas", "persistency_category": "absent", "n_groups": 3,
        "ambiguity": "competing", "radius_capped": False,
        "first_detection": pd.Timestamp("2026-01-18T18:11:41"), "last_detection": pd.Timestamp("2026-01-18T18:11:41"),
        "days_since_first_detection": 240,
        "capped_duration_h": 266.4, "capped_ch4_kg": 660672.0, "capped_co2e_kg": 19688025.6, "capped_usd": 96660.685589,
        "upper_bound_duration_h": math.nan, "upper_bound_ch4_kg": math.nan, "upper_bound_co2e_kg": math.nan, "upper_bound_usd": math.nan,
        "det": [["2026-01-18T18:11", "EMIT", 2480.0]],
        "cands": [{"k": "gas_pipeline", "n": "Line A", "ds": "GEM-GGIT-Pipelines-2025-11", "p": "TC Energy Corp", "d": 31.1, "th": 60.8}],
    }
    base.update(overrides)
    return pd.Series(base)


def test_case_count_gate_is_the_hand_checked_figure() -> None:
    check_case_count(MARS_UNANSWERED_CASE_COUNT)
    with pytest.raises(CaseCountError):
        check_case_count(MARS_UNANSWERED_CASE_COUNT - 1)


def test_display_party_strips_share_brackets_but_keeps_every_party() -> None:
    assert display_party("TC Energy Corp [100.00%]") == "TC Energy Corp"
    assert display_party("A LLC [unknown %]; B LP [unknown %]") == "A LLC; B LP"
    assert display_party("TENGE Oil & Gas") == "TENGE Oil & Gas"
    assert display_party(None) is None
    assert display_party(math.nan) is None


def test_instrument_name_drops_the_agency_only() -> None:
    assert instrument_name("Sentinel-2 - ESA") == "Sentinel-2"
    assert instrument_name("Landsat - NASA/USGS") == "Landsat"
    assert instrument_name("Sentinel-5P/TROPOMI - ESA") == "Sentinel-5P/TROPOMI"
    assert instrument_name("Planet Tanager") == "Planet Tanager"


def test_tier_labels_are_exhaustive_and_unknown_raises() -> None:
    assert [tier_label(t) for t in ("OPERATOR_NAMED", "ASSET_ONLY", "UNMAPPED")] == ["named", "asset", "unmapped"]
    with pytest.raises(ValueError):
        tier_label("HIGH")


def test_web_tier_drops_the_name_when_competing_assets_name_different_parties() -> None:
    assert web_tier("OPERATOR_NAMED", 1) == "named"
    assert web_tier("OPERATOR_NAMED", 2) == "asset"
    assert web_tier("ASSET_ONLY", 2) == "asset"
    assert web_tier("UNMAPPED", 0) == "unmapped"


def test_leak_record_co_owners_of_one_asset_are_joined_but_competing_parties_are_null() -> None:
    joint = leak_record(_row(top_named_party="A LLC [unknown %]; B LP [unknown %]", n_candidates=1, n_parties=1))
    assert joint["t"] == "named" and joint["op"] == "A LLC; B LP"
    competing = leak_record(_row(top_named_party="A LLC [unknown %]; B LP [unknown %]", n_candidates=3, n_parties=2))
    assert competing["t"] == "asset" and competing["op"] is None


def test_resolve_country_exact_alias_absent_and_unknown() -> None:
    names = {"Russia", "Fiji", "United States of America"}
    assert resolve_country("Russia", names) == "Russia"
    assert resolve_country("Russian Federation", names) == "Russia"
    assert resolve_country("United States of America", names) == "United States of America"
    assert resolve_country("Bahrain", names) == "Bahrain"
    with pytest.raises(CountryNameError):
        resolve_country("Atlantis", names)


def test_leak_record_hand_checked_units() -> None:
    """2480 kg/h -> 59,520 kg/day -> 59.5 t/day; USD from the HHV and Henry Hub constants."""
    rec = leak_record(_row())
    assert rec["f"] == 2480
    assert rec["ch"] == pytest.approx(59.5)
    assert rec["u"] == 8708
    assert rec["ch"] * 1000 == pytest.approx(rec["f"] * 24, rel=1e-3), "ch must be tonnes of the kg/h rate x 24"
    assert rec["sat"] == ["EMIT", "Sentinel-2"]
    assert rec["t"] == "named" and rec["op"] == "TC Energy Corp" and rec["cand"] == 3
    assert rec["d"] == 240 and rec["nd"] == 1 and rec["id"] == "RUS_S_001" and rec["ty"] == "Flare"
    assert rec["lat"] == 8.40613 and rec["lon"] == -64.79417


def test_leak_record_op_is_null_unless_named_and_null_flux_stays_null() -> None:
    asset_only = leak_record(_row(tier="ASSET_ONLY", top_named_party=None))
    assert asset_only["t"] == "asset" and asset_only["op"] is None
    no_flux = leak_record(_row(mean_fluxrate_kg_per_h=math.nan, kg_per_day=math.nan, usd_per_day=math.nan))
    assert no_flux["f"] is None and no_flux["ch"] is None and no_flux["u"] is None


def test_build_payload_aggregates_and_includes_zero_case_countries(monkeypatch) -> None:
    monkeypatch.setattr("etl.export_web.MARS_UNANSWERED_CASE_COUNT", 3)
    frame = pd.DataFrame([
        _row().to_dict(),
        _row(source_name="RUS_S_002", tier="UNMAPPED", top_named_party=None, n_candidates=0,
             mean_fluxrate_kg_per_h=1000.0, kg_per_day=24000.0, usd_per_day=3511.0).to_dict(),
        _row(source_name="BHR_S_001", country="Bahrain", tier="ASSET_ONLY", top_named_party=None,
             lat=26.1, lon=50.5, mean_fluxrate_kg_per_h=math.nan, kg_per_day=math.nan, usd_per_day=math.nan).to_dict(),
    ])
    payload = build_payload(frame, RINGS)
    by_name = {c["n"]: c for c in payload["C"]}
    assert set(by_name) == {"Russia", "Fiji", "Bahrain"}
    russia = by_name["Russia"]
    assert (russia["cs"], russia["nm"], russia["as"], russia["um"]) == (2, 1, 0, 1)
    assert russia["usd"] == 12219 and russia["ch4"] == pytest.approx(83.5) and russia["fmax"] == 2480
    assert russia["r"] == RINGS[0]["r"] and russia["c"] == "Europe"
    fiji = by_name["Fiji"]
    assert (fiji["cs"], fiji["usd"], fiji["ch4"], fiji["fmax"]) == (0, 0, 0, 0)
    bahrain = by_name["Bahrain"]
    assert bahrain["r"] == [] and bahrain["c"] == "Asia" and bahrain["bb"] == [50.5, 26.1, 50.5, 26.1]
    assert (bahrain["cs"], bahrain["as"], bahrain["usd"], bahrain["fmax"]) == (1, 1, 0, 0)
    assert set(payload["L"]) == {"Russia", "Bahrain"}
    assert [l["id"] for l in payload["L"]["Russia"]] == ["RUS_S_001", "RUS_S_002"]


def test_build_payload_refuses_a_wrong_case_count(monkeypatch) -> None:
    monkeypatch.setattr("etl.export_web.MARS_UNANSWERED_CASE_COUNT", 2)
    with pytest.raises(CaseCountError):
        build_payload(pd.DataFrame([_row().to_dict()]), RINGS)


def _attribution(named_party: str | None) -> Attribution:
    asset = Asset(asset_id="a1", physical_id="p1", source_dataset="GEM-GGIT-Pipelines-2025-11", name="Test Pipe",
                  asset_kind="gas_pipeline", country="Russia", operator=None, owner=None, parent=named_party,
                  status="operating", geometry=shapely.LineString([(30.0, 50.0), (31.0, 50.0)]))
    cand = Candidate(asset=asset, distance_m=31.05, inside=False, compatible=True, route_traced=True, sigma_m=100.0, threshold_m=116.6)
    return Attribution(source_name="RUS_S_001", tier="OPERATOR_NAMED", leak=Leak(50.0, 30.5), published_lat=50.0,
                       published_lon=30.5, search_mode="symmetric_zero_offset", shift_m=0.0, radius_m=60.0,
                       radius_capped=False, satellites=("EMIT - NASA",), n_candidates=1, n_groups=1, n_parties=1,
                       ambiguity="none", candidates=[cand], plume_sigma_m=60.0)


def test_detail_record_keeps_capped_and_upper_bound_separate_and_hand_checked() -> None:
    rec = detail_record(_row(n_candidates=1, n_groups=1, ambiguity="none"), _attribution("TC Energy Corp [100.00%]"))
    assert set(rec) >= {"capped", "upper_bound", "rate", "attribution", "detections"}
    assert rec["capped"] == {"duration_h": 266.4, "ch4_kg": 660672, "ch4_t": 660.7, "co2e_kg": 19688026, "co2e_t": 19688.0, "usd": 96661}
    assert rec["upper_bound"] == {"duration_h": None, "ch4_kg": None, "ch4_t": None, "co2e_kg": None, "co2e_t": None, "usd": None}
    assert rec["capped"]["ch4_kg"] == pytest.approx(2480 * 266.4, rel=1e-6), "capped mass = flux x capped duration"
    assert rec["rate"]["ch4_t_per_day"] == 59.5 and rec["rate"]["reported"] is True
    assert rec["attribution"]["shown_as"] == "named" and rec["attribution"]["likely_operator"] == "TC Energy Corp"
    cand = rec["attribution"]["candidates"][0]
    assert cand["named_party"] == "TC Energy Corp" and cand["distance_m"] == 31.1 and cand["threshold_m"] == 116.6
    assert rec["detections"]["satellites"] == ["EMIT - NASA", "Sentinel-2 - ESA"]


def test_detail_record_refuses_a_mismatched_attribution() -> None:
    bad = _attribution(None)
    bad.source_name = "OTHER"
    with pytest.raises(ValueError):
        detail_record(_row(), bad)


# --- standalone page ----------------------------------------------------------------

from pathlib import Path  # noqa: E402

from backend.app.config import (  # noqa: E402
    MARS_PUBLICATION_LAG_DAYS_MAX, MARS_PUBLICATION_LAG_DAYS_MIN, MARS_SNAPSHOT_DATE,
    WEB_BOOT_LINE, WEB_DATA_PATH, WEB_DIR, WEB_STANDALONE_PATH,
)
from etl.export_web import StandaloneError, build_standalone  # noqa: E402

PAGE = '<script type="module">\n' + WEB_BOOT_LINE + '\nconst NL=Object.values(L).flat().length;\n</script>'


def test_build_standalone_replaces_only_the_boot_line() -> None:
    out = build_standalone(PAGE, '{"C":[],"L":{"Russia":[{"id":"RUS_S_001"}]}}')
    assert out == ('<script type="module">\nconst {C, L, M} = {"C":[],"L":{"Russia":[{"id":"RUS_S_001"}]}};\n'
                   'const NL=Object.values(L).flat().length;\n</script>')
    assert "fetch(" not in out


def test_build_standalone_cannot_close_the_script_tag_and_keeps_the_value() -> None:
    out = build_standalone(PAGE, '{"C":[],"L":{"X":[{"op":"A </script> B"}]}}')
    assert "</script> B" not in out.split("const {C, L, M} = ")[1].split("\nconst NL")[0]
    assert '"A <\\/script> B"' in out and "const {C, L, M} = " in out  # JS reads "\/" as "/", so the party string is unchanged


def test_build_standalone_refuses_zero_or_two_boot_lines() -> None:
    with pytest.raises(StandaloneError):
        build_standalone("<script></script>", "{}")
    with pytest.raises(StandaloneError):
        build_standalone(PAGE + PAGE, "{}")


def test_committed_standalone_is_index_plus_data_byte_for_byte() -> None:
    """Regenerable in one command; drift between the three committed files is a failure."""
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    expected = build_standalone(index, WEB_DATA_PATH.read_text(encoding="utf-8"))
    assert WEB_STANDALONE_PATH.read_text(encoding="utf-8") == expected
    assert "fetch(" not in WEB_STANDALONE_PATH.read_text(encoding="utf-8").split("</aside>")[-1]


def test_index_carries_the_visible_licence_footer() -> None:
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    footer = index.split('<footer class="lic">')[1].split("</footer>")[0]
    assert "UNEP IMEO" in footer and "CC BY-NC-SA 4.0" in footer
    assert "Global Energy Monitor" in footer and "CC BY 4.0" in footer
    assert MARS_SNAPSHOT_DATE.strftime("%-d %B %Y") in footer, "footer snapshot date must match config"
    assert f"{MARS_PUBLICATION_LAG_DAYS_MIN} to {MARS_PUBLICATION_LAG_DAYS_MAX} days" in footer


# --- real detection rows and candidate cards (no PRNG anywhere) --------------------

import json  # noqa: E402


def test_detection_rows_keep_time_order_instrument_and_null_rates() -> None:
    plumes = pd.DataFrame({
        "source_name": ["X", "X", "X", "Y"],
        "tile_date": ["2026-01-21T20:32:00", "2026-01-21T17:25:29", "2026-01-21T18:52:00", "2025-03-01T00:00:00"],
        "satellite": ["VIIRS - NASA/NOAA", "Sentinel-2 - ESA", "VIIRS - NASA/NOAA", "EMIT - NASA"],
        "ch4_fluxrate": [math.nan, 76779.0, math.nan, 2480.0],
    })
    det = detection_rows(plumes)
    assert det["X"] == [["2026-01-21T17:25", "Sentinel-2", 76779.0], ["2026-01-21T18:52", "VIIRS", None],
                        ["2026-01-21T20:32", "VIIRS", None]]
    assert det["Y"] == [["2025-03-01T00:00", "EMIT", 2480.0]]


def test_candidate_cards_are_the_exported_values_in_order_and_capped() -> None:
    result = _attribution("TC Energy Corp [100.00%]")
    cards = candidate_cards(result)
    assert cards == [{"k": "gas_pipeline", "n": "Test Pipe", "ds": "GEM-GGIT-Pipelines-2025-11", "p": "TC Energy Corp",
                      "d": 31.1, "th": 116.6}]
    assert candidate_cards(_attribution(None))[0]["p"] is None
    result.candidates = result.candidates * 7
    assert len(candidate_cards(result)) == 5 and len(candidate_cards(result, limit=2)) == 2


def test_leak_record_carries_detections_and_cards_and_refuses_a_count_mismatch() -> None:
    rec = leak_record(_row())
    assert rec["det"] == [["2026-01-18T18:11", "EMIT", 2480.0]] and rec["cands"][0]["n"] == "Line A"
    with pytest.raises(ValueError):
        leak_record(_row(n_detections=2))
    with pytest.raises(ValueError):
        leak_record(_row(det=[], n_detections=0))


def test_committed_blob_usa_s_1063_matches_the_plumes_table_and_cases_json() -> None:
    """Hand-checked: five detections on 2026-01-21, one Sentinel-2 rate of
    76,779 kg/h and four VIIRS rows with no rate; three candidates led by the
    Whistler pipeline at 31.1 m within 60.8 m. The page renders these and nothing else."""
    blob = json.loads(WEB_DATA_PATH.read_text(encoding="utf-8"))
    leak = next(l for l in blob["L"]["United States of America"] if l["id"] == "USA_S_1063")
    assert leak["det"] == [["2026-01-21T17:25", "Sentinel-2", 76779.0], ["2026-01-21T18:52", "VIIRS", None],
                           ["2026-01-21T19:21", "VIIRS", None], ["2026-01-21T19:42", "VIIRS", None],
                           ["2026-01-21T20:32", "VIIRS", None]]
    assert leak["nd"] == 5 and leak["f"] == 76779 and leak["t"] == "asset" and leak["op"] is None
    assert [c["k"] for c in leak["cands"]] == ["gas_pipeline", "well", "flare_detection"]
    assert leak["cands"][0] == {"k": "gas_pipeline", "n": "Whistler Pipeline | Midland Lateral", "ds": "GEM-GGIT-Pipelines-2025-11",
                                "p": "First Infrastructure Capital Advisors LLC; MPLX LP; Stonepeak Partners LP; West Texas Gas Inc",
                                "d": 31.1, "th": 60.8}
    assert leak["cands"][2]["p"] is None and leak["cands"][2]["d"] == 458.2 and leak["cands"][2]["th"] == 752.4
    detail = json.loads((WEB_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]["USA_S_1063"]["attribution"]
    assert [(c["kind"], c["distance_m"], c["threshold_m"]) for c in detail["candidates"]] == \
           [(c["k"], c["d"], c["th"]) for c in leak["cands"]]
    for country in blob["L"].values():
        for l in country:
            assert len(l["det"]) == l["nd"] > 0 and len(l["cands"]) == min(l["cand"], 5)


def test_leak_record_duration_blocks_are_tonnes_and_null_where_unobserved() -> None:
    """capped 660,672 kg over 266.4 h -> 660.7 t; the span is null for a single detection."""
    rec = leak_record(_row())
    assert rec["cap"] == {"h": 266.4, "t": 660.7} and rec["ub"] is None
    both = leak_record(_row(upper_bound_duration_h=3.1, upper_bound_ch4_kg=238676.0))
    assert both["ub"] == {"h": 3.1, "t": 238.7}
    assert leak_record(_row(capped_duration_h=math.nan, capped_ch4_kg=math.nan))["cap"] is None


def test_committed_blob_meta_comes_from_config() -> None:
    from backend.app.config import DETECTION_ATTRIBUTABLE_WINDOW_DAYS
    blob = json.loads(WEB_DATA_PATH.read_text(encoding="utf-8"))
    assert blob["M"] == {"window_days": DETECTION_ATTRIBUTABLE_WINDOW_DAYS, "snapshot": MARS_SNAPSHOT_DATE.isoformat()}
    leak = next(l for l in blob["L"]["United States of America"] if l["id"] == "USA_S_1063")
    assert leak["cap"] == {"h": 269.5, "t": 20692.6} and leak["ub"] == {"h": 3.1, "t": 238.7}
