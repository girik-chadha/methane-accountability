"""Hand-checked tests for backend/app/costing.py. Every number derives from
the config constants or from arithmetic shown in the test."""

from __future__ import annotations

import inspect
import math
from datetime import datetime, timedelta

import pytest

from backend.app.config import (
    GAS_PRICE_USD_PER_MMBTU,
    MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H,
    METHANE_DENSITY_KG_PER_M3,
    METHANE_GWP100_FOSSIL_AR6,
    METHANE_HHV_MJ_PER_KG,
    MJ_PER_MMBTU,
)
from backend.app.config import DETECTION_ATTRIBUTABLE_WINDOW_H, MARS_SNAPSHOT_DATE
from backend.app.costing import (
    annualised_emission_kg,
    attributable_duration_h,
    check_fluxrate_series_is_kg_per_h,
    co2e_kg,
    cost_case,
    kg_to_tonnes,
    methane_kg_to_mmbtu,
    methane_kg_to_standard_m3,
    observed_duration_h,
    wasted_gas_value_usd,
)

T0 = datetime(2025, 3, 1, 10, 0, 0)


# --- constants are what the citations say ---------------------------------------------


def test_gwp_and_physical_constants_match_their_citations() -> None:
    assert METHANE_GWP100_FOSSIL_AR6 == 29.8
    assert METHANE_HHV_MJ_PER_KG == pytest.approx(55.53, abs=0.01), "890.8 kJ/mol / 16.043 g/mol"
    assert METHANE_DENSITY_KG_PER_M3 == pytest.approx(0.6785, abs=5e-4), "PM/RT at 15 C, 101.325 kPa"
    assert MJ_PER_MMBTU == pytest.approx(1055.056, abs=1e-3)
    assert GAS_PRICE_USD_PER_MMBTU > 0.0


# --- duration ---------------------------------------------------------------------------


def test_observed_duration_is_first_to_last_in_hours() -> None:
    assert observed_duration_h([T0 + timedelta(hours=48), T0, T0 + timedelta(hours=5)]) == 48.0


def test_single_or_simultaneous_detections_have_no_duration() -> None:
    assert observed_duration_h([T0]) is None
    assert observed_duration_h([T0, T0]) is None
    assert observed_duration_h([]) is None


def test_duration_parameter_has_no_default() -> None:
    parameter = inspect.signature(annualised_emission_kg).parameters["duration_h"]
    assert parameter.default is inspect.Parameter.empty


# --- mass -------------------------------------------------------------------------------


def test_mass_is_flux_times_duration() -> None:
    assert annualised_emission_kg(3_500.0, 24.0) == 84_000.0
    assert kg_to_tonnes(annualised_emission_kg(3_500.0, 24.0)) == 84.0


def test_mass_rejects_missing_or_zero_duration_and_bad_flux() -> None:
    with pytest.raises(ValueError, match="duration"):
        annualised_emission_kg(3_500.0, 0.0)
    with pytest.raises(ValueError):
        annualised_emission_kg(3_500.0, math.nan)
    with pytest.raises(ValueError):
        annualised_emission_kg(-1.0, 24.0)
    with pytest.raises(TypeError):
        annualised_emission_kg(3_500.0)  # type: ignore[call-arg]


# --- CO2e, energy, volume, value -----------------------------------------------------


def test_co2e_uses_ar6_fossil_methane_gwp100() -> None:
    assert co2e_kg(1_000.0) == pytest.approx(29_800.0)
    assert co2e_kg(0.0) == 0.0
    with pytest.raises(ValueError):
        co2e_kg(-5.0)


def test_energy_content_hand_checked() -> None:
    """1 kg CH4 = 55.53 MJ HHV = 55.53 / 1055.056 MMBtu = 0.05263 MMBtu."""
    assert methane_kg_to_mmbtu(1.0) == pytest.approx(0.05263, abs=2e-5)
    assert methane_kg_to_mmbtu(1_000.0) == pytest.approx(52.63, abs=0.02)


def test_volume_hand_checked() -> None:
    """1 kg at 0.6785 kg/m3 is 1.474 m3."""
    assert methane_kg_to_standard_m3(1.0) == pytest.approx(1.474, abs=2e-3)


def test_value_is_energy_times_the_benchmark_price() -> None:
    assert wasted_gas_value_usd(1_000.0) == pytest.approx(52.63 * GAS_PRICE_USD_PER_MMBTU, rel=1e-3)
    assert wasted_gas_value_usd(0.0) == 0.0


# --- the 1000x trap -----------------------------------------------------------------


def test_a_flux_column_in_tonnes_is_refused_and_kg_is_accepted() -> None:
    kg_per_h = [2_140.0, 468.0, 12_804.0, 3_500.0, 900.0]  # median 2,140: MARS's actual column
    assert check_fluxrate_series_is_kg_per_h(kg_per_h) == 2_140.0
    tonnes_per_h = [v / 1_000.0 for v in kg_per_h]
    with pytest.raises(ValueError, match="unit error"):
        check_fluxrate_series_is_kg_per_h(tonnes_per_h)
    g_per_h = [v * 1_000.0 for v in kg_per_h]
    with pytest.raises(ValueError, match="unit error"):
        check_fluxrate_series_is_kg_per_h(g_per_h)
    low, high = MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H
    assert low == 500.0 and high == 10_000.0


def test_tonnes_never_leak_into_the_kg_path() -> None:
    """84 t passed as if it were kg gives a CO2e a thousand times too small;
    the presentation boundary is the only place tonnes appear."""
    mass_kg = annualised_emission_kg(3_500.0, 24.0)
    assert co2e_kg(mass_kg) == pytest.approx(1_000.0 * co2e_kg(kg_to_tonnes(mass_kg)))


# --- one case end to end ------------------------------------------------------------


def test_cost_case_hand_checked() -> None:
    """Two detections 10 days apart, fluxes 3,000 and 5,000 kg/h.
    duration 240 h, mean 4,000 kg/h, mass 960,000 kg, CO2e 28,608,000 kg."""
    cost = cost_case("TST_S_1", [T0, T0 + timedelta(days=10)], [3_000.0, 5_000.0])
    assert cost.duration_h == 240.0 and cost.mean_fluxrate_kg_per_h == 4_000.0
    assert cost.ch4_kg == 960_000.0
    assert cost.co2e_kg == pytest.approx(28_608_000.0)
    assert cost.wasted_gas_usd == pytest.approx(methane_kg_to_mmbtu(960_000.0) * GAS_PRICE_USD_PER_MMBTU)
    assert kg_to_tonnes(cost.ch4_kg) == 960.0


def test_cost_case_single_detection_is_none_not_zero() -> None:
    cost = cost_case("TST_S_2", [T0], [3_000.0])
    assert cost.duration_h is None and cost.ch4_kg is None and cost.co2e_kg is None and cost.wasted_gas_usd is None
    assert cost.mean_fluxrate_kg_per_h == 3_000.0 and not cost.has_observed_duration


def test_cost_case_without_any_flux_is_none() -> None:
    cost = cost_case("TST_S_3", [T0, T0 + timedelta(days=3)], [None, math.nan])
    assert cost.duration_h == 72.0 and cost.mean_fluxrate_kg_per_h is None and cost.ch4_kg is None


def test_costed_totals_exclude_uncosted_cases_rather_than_zeroing_them() -> None:
    from backend.app.costing import totals

    costs = [
        cost_case("a", [T0, T0 + timedelta(days=1)], [1_000.0]),
        cost_case("b", [T0], [1_000.0]),
    ]
    t = totals(costs)
    assert t["cases"] == 2 and t["costed"] == 1 and t["no_observed_duration"] == 1
    assert t["ch4_kg"] == 24_000.0


# --- duration cap (slice 5B) -----------------------------------------------------------

W = DETECTION_ATTRIBUTABLE_WINDOW_H  # 266.4 h


def test_window_is_the_measured_median_gap() -> None:
    assert W == pytest.approx(11.1 * 24.0)


def test_single_detection_is_credited_one_window() -> None:
    assert attributable_duration_h([T0]) == pytest.approx(W)
    assert attributable_duration_h([]) is None


def test_far_apart_detections_do_not_imply_continuity() -> None:
    """Two detections five years apart: 2 windows, not 5 years."""
    five_years = T0 + timedelta(days=5 * 365)
    assert attributable_duration_h([T0, five_years]) == pytest.approx(2 * W)
    assert observed_duration_h([T0, five_years]) == pytest.approx(5 * 365 * 24.0), "uncapped stays the span"


def test_overlapping_windows_merge_without_double_counting() -> None:
    """Detections 1 day apart with an 11.1-day window: union is 12.1 days."""
    assert attributable_duration_h([T0, T0 + timedelta(days=1)]) == pytest.approx(W + 24.0)
    assert attributable_duration_h([T0, T0]) == pytest.approx(W), "same instant: one window"
    times = [T0, T0 + timedelta(days=1), T0 + timedelta(days=100)]
    assert attributable_duration_h(times) == pytest.approx(2 * W + 24.0)


def test_window_can_be_overridden_and_must_be_positive() -> None:
    # 24 h windows centred on detections 48 h apart: [-12, 12] and [36, 60], no overlap -> 48 h
    assert attributable_duration_h([T0, T0 + timedelta(hours=48)], window_h=24.0) == 48.0
    # 30 h apart with the same window: [-12, 12] and [18, 42] -> 48 h; 20 h apart: [-12, 12] and [8, 32] merge -> 44 h
    assert attributable_duration_h([T0, T0 + timedelta(hours=20)], window_h=24.0) == 44.0
    with pytest.raises(ValueError):
        attributable_duration_h([T0], window_h=0.0)


def test_cost_case_capped_and_daily_figures_hand_checked() -> None:
    """Detections 10 days apart (240 h), fluxes 3,000 and 5,000 kg/h.
    Windows [-133.2, 133.2] and [106.8, 373.2] h overlap -> 506.4 h capped.
    Capped mass 4,000 x 506.4 = 2,025,600 kg. Daily: 96,000 kg/day."""
    cost = cost_case("TST_S_4", [T0, T0 + timedelta(days=10)], [3_000.0, 5_000.0])
    assert cost.capped_duration_h == pytest.approx(506.4)
    assert cost.capped_ch4_kg == pytest.approx(2_025_600.0)
    assert cost.capped_co2e_kg == pytest.approx(2_025_600.0 * 29.8)
    assert cost.capped_usd == pytest.approx(wasted_gas_value_usd(2_025_600.0))
    assert cost.ch4_kg == 960_000.0, "uncapped upper bound unchanged"
    assert cost.kg_per_day == 96_000.0
    assert cost.usd_per_day == pytest.approx(wasted_gas_value_usd(96_000.0))
    assert cost.days_since_last_detection == (MARS_SNAPSHOT_DATE - (T0 + timedelta(days=10)).date()).days == 553


def test_single_detection_gets_capped_mass_but_no_uncapped_mass() -> None:
    cost = cost_case("TST_S_5", [T0], [3_000.0])
    assert cost.ch4_kg is None and cost.duration_h is None
    assert cost.capped_duration_h == pytest.approx(W) and cost.capped_ch4_kg == pytest.approx(3_000.0 * W)
    assert cost.kg_per_day == 72_000.0


def test_no_flux_has_no_daily_or_capped_figures() -> None:
    cost = cost_case("TST_S_6", [T0, T0 + timedelta(days=3)], [None])
    assert cost.capped_ch4_kg is None and cost.kg_per_day is None and cost.usd_per_day is None
    assert cost.days_since_last_detection is not None
