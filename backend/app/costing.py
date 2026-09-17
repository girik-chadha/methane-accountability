"""Cost a MARS source: methane mass, CO2-equivalent and wasted-gas value.

Units. Every function here takes and returns KILOGRAMS of methane. The only
way out of kilograms is kg_to_tonnes, which exists for presentation and
nothing else. A function's name carries its unit.

The mass is a MODEL, not a measurement. MARS publishes an instantaneous flux
rate (kg/h) at each satellite overpass. Turning that into a mass needs a
duration, and the duration is an assumption the caller must state:
annualised_emission_kg has no default for it. The batch in this module
derives each source's duration from its own detection record, first to last
plume (observed_duration_h), assuming emission at the mean detected rate,
continuously, over that span. That overstates intermittent sources and
understates sources still emitting after their last detection. A source
detected once has no observable duration and gets None, never zero.

Constants (GWP, heating value, density, price) live in backend/app/config.py
with their sources. The gas price is one named benchmark on one date; it
moves, and the presentation must say so.
"""

from __future__ import annotations

import math
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from backend.app.config import (
    DETECTION_ATTRIBUTABLE_WINDOW_H,
    FEEDBACK_NO,
    GAS_PRICE_BENCHMARK,
    GAS_PRICE_PERIOD,
    GAS_PRICE_USD_PER_MMBTU,
    KG_PER_TONNE,
    MARS_DB_PATH,
    MARS_SNAPSHOT_DATE,
    MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H,
    METHANE_DENSITY_KG_PER_M3,
    METHANE_GWP100_FOSSIL_AR6,
    METHANE_HHV_MJ_PER_KG,
    MJ_PER_MMBTU,
    PLUMES_TABLE,
    SOURCES_TABLE,
    TIER_UNMAPPED,
)

SECONDS_PER_HOUR = 3600.0
HOURS_PER_DAY = 24.0


# --- conversions, kg in, kg out ---------------------------------------------------


def observed_duration_h(detection_times: Sequence[datetime]) -> float | None:
    """Hours from the first to the last detection, or None.

    None when there are fewer than two detections or all detections share one
    timestamp: a single observation cannot tell how long a source emitted, and
    zero would silently turn every such source into zero mass.
    """
    if len(detection_times) < 2:
        return None
    span_s = (max(detection_times) - min(detection_times)).total_seconds()
    if span_s <= 0.0:
        return None
    return span_s / SECONDS_PER_HOUR


def attributable_duration_h(
    detection_times: Sequence[datetime], window_h: float = DETECTION_ATTRIBUTABLE_WINDOW_H
) -> float | None:
    """Hours attributable to the detections: the union of a window of
    `window_h` centred on each detection. Overlaps merge, so dense detection
    records stay continuous while sparse ones stop implying continuity. A
    single detection is credited with one window. None with no detections.
    """
    if window_h <= 0.0 or not math.isfinite(window_h):
        raise ValueError("window_h must be finite and positive")
    if not detection_times:
        return None
    half = window_h / 2.0
    intervals = sorted((t - _h(half), t + _h(half)) for t in detection_times)
    total_s = 0.0
    start, end = intervals[0]
    for begin, finish in intervals[1:]:
        if begin <= end:
            end = max(end, finish)
        else:
            total_s += (end - start).total_seconds()
            start, end = begin, finish
    total_s += (end - start).total_seconds()
    return total_s / SECONDS_PER_HOUR


def _h(hours: float):
    from datetime import timedelta

    return timedelta(hours=hours)


def annualised_emission_kg(fluxrate_kg_per_h: float, duration_h: float) -> float:
    """Methane mass in kg emitted at `fluxrate_kg_per_h` for `duration_h`.

    Despite the name this is flux times duration with no per-year scaling; the
    duration is a modelling assumption and must be stated by the caller, which
    is why it has no default.
    """
    if not math.isfinite(fluxrate_kg_per_h) or fluxrate_kg_per_h < 0.0:
        raise ValueError(f"fluxrate_kg_per_h must be finite and non-negative, got {fluxrate_kg_per_h!r}")
    if not math.isfinite(duration_h) or duration_h <= 0.0:
        raise ValueError(f"duration_h must be finite and positive, got {duration_h!r}; a source "
                         "with no observable duration must be reported as None, not costed")
    return fluxrate_kg_per_h * duration_h


def _check_mass_kg(ch4_kg: float) -> None:
    if not math.isfinite(ch4_kg) or ch4_kg < 0.0:
        raise ValueError(f"ch4_kg must be finite and non-negative, got {ch4_kg!r}")


def co2e_kg(ch4_kg: float) -> float:
    """CO2-equivalent mass in kg, IPCC AR6 GWP-100 for fossil-origin methane."""
    _check_mass_kg(ch4_kg)
    return ch4_kg * METHANE_GWP100_FOSSIL_AR6


def methane_kg_to_mmbtu(ch4_kg: float) -> float:
    """Energy content on a higher-heating-value basis, in million Btu."""
    _check_mass_kg(ch4_kg)
    return ch4_kg * METHANE_HHV_MJ_PER_KG / MJ_PER_MMBTU


def methane_kg_to_standard_m3(ch4_kg: float) -> float:
    """Volume at ISO 13443 reference conditions (15 C, 101.325 kPa), for display."""
    _check_mass_kg(ch4_kg)
    return ch4_kg / METHANE_DENSITY_KG_PER_M3


def wasted_gas_value_usd(ch4_kg: float) -> float:
    """Market value of the methane as gas, at the one named benchmark price
    in config (GAS_PRICE_BENCHMARK, GAS_PRICE_PERIOD). Prices move."""
    return methane_kg_to_mmbtu(ch4_kg) * GAS_PRICE_USD_PER_MMBTU


def kg_to_tonnes(kg: float) -> float:
    """PRESENTATION BOUNDARY. The only conversion out of kilograms."""
    return kg / KG_PER_TONNE


def check_fluxrate_series_is_kg_per_h(values: Sequence[float]) -> float:
    """Guard against a flux column that is secretly in another unit.

    A column in kg/h has a median inside MARS's own stated typical detectable
    range (500-10,000 kg/h). The same column in tonnes/h has a median a
    thousand times smaller and in g/h a thousand times larger; both are
    refused. Returns the median so callers can log it.
    """
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if not clean:
        raise ValueError("no finite flux values to check")
    median = float(pd.Series(clean).median())
    low, high = MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H
    if not low <= median <= high:
        raise ValueError(
            f"flux column median {median:g} is outside the MARS typical range {low:g}-{high:g} kg/h; "
            "this looks like a unit error (tonnes/h or g/h passed as kg/h)"
        )
    return median


# --- one case ----------------------------------------------------------------------


@dataclass(frozen=True)
class CaseCost:
    """One case. The HEADLINE mass is capped_*: each detection credited with
    one attributable window. ch4_kg / co2e_kg / wasted_gas_usd are the
    UNCAPPED first-to-last span, an UPPER BOUND, kept for comparison. The
    daily figures depend on no duration assumption at all."""

    source_name: str
    n_detections: int
    duration_h: float | None
    mean_fluxrate_kg_per_h: float | None
    ch4_kg: float | None
    co2e_kg: float | None
    wasted_gas_usd: float | None
    capped_duration_h: float | None = None
    capped_ch4_kg: float | None = None
    capped_co2e_kg: float | None = None
    capped_usd: float | None = None
    kg_per_day: float | None = None
    usd_per_day: float | None = None
    days_since_last_detection: int | None = None

    @property
    def has_observed_duration(self) -> bool:
        return self.duration_h is not None


def cost_case(source_name: str, detection_times: Sequence[datetime], fluxrates_kg_per_h: Sequence[float | None]) -> CaseCost:
    """Cost one source from its own plumes. None propagates; zero is never
    substituted for an unobservable duration or an unreported flux."""
    fluxes = [f for f in fluxrates_kg_per_h if f is not None and math.isfinite(f)]
    mean_flux = sum(fluxes) / len(fluxes) if fluxes else None
    duration = observed_duration_h(detection_times)
    capped_duration = attributable_duration_h(detection_times)
    last = max(detection_times) if detection_times else None
    since = (MARS_SNAPSHOT_DATE - last.date()).days if last is not None else None

    uncapped = (None, None, None)
    capped = (None, None, None)
    daily = (None, None)
    if mean_flux is not None:
        per_day = mean_flux * HOURS_PER_DAY
        daily = (per_day, wasted_gas_value_usd(per_day))
        if duration is not None:
            mass = annualised_emission_kg(mean_flux, duration)
            uncapped = (mass, co2e_kg(mass), wasted_gas_value_usd(mass))
        if capped_duration is not None:
            mass = annualised_emission_kg(mean_flux, capped_duration)
            capped = (mass, co2e_kg(mass), wasted_gas_value_usd(mass))
    return CaseCost(
        source_name, len(detection_times), duration, mean_flux, *uncapped,
        capped_duration_h=capped_duration, capped_ch4_kg=capped[0], capped_co2e_kg=capped[1], capped_usd=capped[2],
        kg_per_day=daily[0], usd_per_day=daily[1], days_since_last_detection=since,
    )


# --- batch over the unanswered cases ----------------------------------------------


def load_case_plumes(db_path: Path = MARS_DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as connection:
        frame = pd.read_sql(
            f"SELECT p.source_name, p.tile_date, p.ch4_fluxrate FROM {PLUMES_TABLE} p "
            f"JOIN {SOURCES_TABLE} s ON p.source_name = s.source_name "
            "WHERE s.feedback_government = ? AND s.feedback_operator = ?",
            connection, params=(FEEDBACK_NO, FEEDBACK_NO),
        )
    frame["detected_at"] = pd.to_datetime(frame["tile_date"])
    check_fluxrate_series_is_kg_per_h(frame["ch4_fluxrate"].dropna().tolist())
    return frame


def cost_cases(plumes: pd.DataFrame) -> list[CaseCost]:
    return [
        cost_case(name, group["detected_at"].tolist(), group["ch4_fluxrate"].tolist())
        for name, group in plumes.groupby("source_name")
    ]


def totals(costs: Sequence[CaseCost]) -> dict[str, float | int]:
    """Totals over the costed subset ONLY. Sources without an observed
    duration or a reported flux are counted, never summed."""
    costed = [c for c in costs if c.ch4_kg is not None]
    capped_all = [c for c in costs if c.capped_ch4_kg is not None]
    return {
        "cases": len(costs), "costed": len(costed),
        "no_observed_duration": sum(1 for c in costs if c.duration_h is None),
        "no_flux": sum(1 for c in costs if c.mean_fluxrate_kg_per_h is None),
        "ch4_kg": sum(c.ch4_kg for c in costed),
        "co2e_kg": sum(c.co2e_kg for c in costed),
        "usd": sum(c.wasted_gas_usd for c in costed),
        "duration_h_p50": float(pd.Series([c.duration_h for c in costed]).median()) if costed else math.nan,
        # capped over the SAME cases as the uncapped total, so the ratio compares like with like
        "capped_ch4_kg": sum(c.capped_ch4_kg for c in costed),
        "capped_co2e_kg": sum(c.capped_co2e_kg for c in costed),
        "capped_usd": sum(c.capped_usd for c in costed),
        "capped_duration_h_p50": float(pd.Series([c.capped_duration_h for c in costed]).median()) if costed else math.nan,
        # capped over every case with a flux, single detections included (one window each)
        "capped_all_costed": len(capped_all),
        "capped_all_ch4_kg": sum(c.capped_ch4_kg for c in capped_all),
        "capped_all_co2e_kg": sum(c.capped_co2e_kg for c in capped_all),
        "capped_all_usd": sum(c.capped_usd for c in capped_all),
    }


def print_totals(label: str, t: dict[str, float | int]) -> None:
    print(f"{label}")
    print(f"  cases {t['cases']:,}; {t['no_flux']:,} have no reported flux and cannot be costed at all")
    if t["costed"]:
        ratio = t["capped_ch4_kg"] / t["ch4_kg"] if t["ch4_kg"] else math.nan
        print(f"  {t['costed']:,} cases with 2+ detections (same cases in both columns):"
              f"      {'CAPPED (default)':>18} {'UNCAPPED, UPPER BOUND':>22} {'ratio':>7}")
        print(f"    duration p50 days {t['capped_duration_h_p50'] / HOURS_PER_DAY:>29,.0f} {t['duration_h_p50'] / HOURS_PER_DAY:>22,.0f}")
        print(f"    CH4 t             {kg_to_tonnes(t['capped_ch4_kg']):>29,.0f} {kg_to_tonnes(t['ch4_kg']):>22,.0f} {ratio:>7.3f}")
        print(f"    CO2e t            {kg_to_tonnes(t['capped_co2e_kg']):>29,.0f} {kg_to_tonnes(t['co2e_kg']):>22,.0f} {ratio:>7.3f}")
        print(f"    value million USD {t['capped_usd'] / 1e6:>29,.1f} {t['usd'] / 1e6:>22,.1f} {ratio:>7.3f}")
    print(f"  capped, ALL {t['capped_all_costed']:,} cases with a flux (single detections credited one window each):")
    print(f"    CH4 {kg_to_tonnes(t['capped_all_ch4_kg']):,.0f} t   CO2e {kg_to_tonnes(t['capped_all_co2e_kg']):,.0f} t   "
          f"value {t['capped_all_usd'] / 1e6:,.1f} million USD")
    print(f"  GWP-100 fossil methane {METHANE_GWP100_FOSSIL_AR6} (AR6); price {GAS_PRICE_BENCHMARK}, {GAS_PRICE_PERIOD}: "
          f"{GAS_PRICE_USD_PER_MMBTU} USD/MMBtu (prices move)")
    print()


def main() -> int:
    plumes = load_case_plumes()
    costs = cost_cases(plumes)
    by_name = {c.source_name: c for c in costs}

    durations = pd.Series([c.duration_h for c in costs if c.duration_h is not None]) / HOURS_PER_DAY
    print(f"OBSERVED-DURATION COVERAGE over {len(costs):,} unanswered cases")
    print(f"  with an observed span (2+ detections, span > 0): {len(durations):,}; single detection: "
          f"{sum(1 for c in costs if c.n_detections == 1):,}; no reported flux: {sum(1 for c in costs if c.mean_fluxrate_kg_per_h is None):,}")
    print(f"  span days: p10 {durations.quantile(.1):,.1f}  p25 {durations.quantile(.25):,.0f}  p50 {durations.median():,.0f}  "
          f"p75 {durations.quantile(.75):,.0f}  p90 {durations.quantile(.9):,.0f}  max {durations.max():,.0f}; under 1 day: {int((durations < 1).sum()):,}")
    print(f"  capped model: each detection credited with one {DETECTION_ATTRIBUTABLE_WINDOW_H / HOURS_PER_DAY:.1f}-day window "
          "centred on it, overlaps merged (measured median inter-detection gap).")
    print("  uncapped model (UPPER BOUND): continuous emission at the mean detected rate between first and last detection.")
    daily = [c for c in costs if c.kg_per_day is not None]
    kg_day = pd.Series([c.kg_per_day for c in daily]); usd_day = pd.Series([c.usd_per_day for c in daily])
    since = pd.Series([c.days_since_last_detection for c in costs if c.days_since_last_detection is not None])
    print(f"\nPER-CASE DAILY FIGURES (no duration assumption), {len(daily):,} cases with a flux:")
    print(f"  kg CH4 per day  p50 {kg_day.median():>10,.0f}  p90 {kg_day.quantile(.9):>10,.0f}  (kg/h p50 {kg_day.median() / HOURS_PER_DAY:,.0f})")
    print(f"  USD per day     p50 {usd_day.median():>10,.0f}  p90 {usd_day.quantile(.9):>10,.0f}")
    print(f"  days since last detection (to {MARS_SNAPSHOT_DATE}): p10 {since.quantile(.1):,.0f}  p50 {since.median():,.0f}  p90 {since.quantile(.9):,.0f}\n")

    from backend.app.attribution import AssetIndex, attribute_all, load_cases  # noqa: PLC0415  (heavy; only for the split)
    sources, case_plumes = load_cases()
    index = AssetIndex.from_parquet()
    results = attribute_all(index, sources, case_plumes)
    mapped = [by_name[r.source_name] for r in results if r.tier != TIER_UNMAPPED]

    print_totals("ALL 1,394 CASES", totals(costs))
    print_totals(f"MAPPED SUBSET ({len(mapped):,} cases with a candidate under the default scenario)", totals(mapped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
