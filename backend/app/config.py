"""Central configuration and cited constants.

Hard rule: no magic numbers anywhere else in the codebase. Every value here
carries the source it came from. If a value is not known, it is absent from
this file and the code that needs it raises, rather than guessing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# =============================================================================
# UNITS — the single biggest bug risk in this project
# =============================================================================
#
# Source for everything in this block: `definitions_detected_plumes_sources.pdf`,
# the data-definitions document that UNEP IMEO bundles inside
# `unep_methanedata_detected_plumes_csv.zip` (the "MARS sources and plumes"
# download from methanedata.unep.org/download-dataset). It ships in every raw
# snapshot under data/raw/, so the citation travels with the data.
#
# !!! WARNING: THE MARS DATASET USES TWO DIFFERENT MASS UNITS. !!!
#
#   ch4_fluxrate      kg/h     kilograms per hour   (plumes table)
#   ch4_fluxrate_std  kg/h     kilograms per hour   (plumes table)
#   total_emission    tonnes   metric tonnes        (plumes table)
#   total_emission_std tonnes  metric tonnes        (plumes table)
#
# The flux-rate fields and the total-emission fields MUST NEVER be added,
# compared, summed, averaged or plotted together without an explicit
# conversion. A tonnes value mistaken for kg is a 1000x error that silently
# invalidates every downstream cost figure. Convert with KG_PER_TONNE below,
# and only ever in one direction at a time.
#
# Note also that these are different KINDS of quantity, not merely different
# units: ch4_fluxrate is a RATE (mass per unit time, an instantaneous snapshot
# at satellite overpass) while total_emission is a MASS (a cumulative total).
# Converting units does not make them comparable. Turning a flux rate into a
# cumulative mass additionally requires an explicit, stated duration
# assumption, which is a modelling decision and not a unit conversion.
#
# In this codebase, kg is the base mass unit for all internal computation.
# Conversion to tonnes happens only at the presentation boundary.

CH4_FLUXRATE_UNIT: Final[str] = "kg/h"
"""Unit of the MARS `ch4_fluxrate` column: kilograms per hour."""

CH4_FLUXRATE_STD_UNIT: Final[str] = "kg/h"
"""Unit of the MARS `ch4_fluxrate_std` column: kilograms per hour.

The bundled definitions PDF gives a usual range of 200-1000 kg/h for this
uncertainty figure. That is a sanity check on our reading of the column, not a
filter: values outside it are not invalid.
"""

TOTAL_EMISSION_UNIT: Final[str] = "tonnes"
"""Unit of the MARS `total_emission` and `total_emission_std` columns: TONNES.

NOT kilograms. See the warning block above. This column is also ~99.6% null in
the 2026-09-15 snapshot (119 non-null of 29,265 plumes), so nothing load-bearing
should be built on it.
"""

KG_PER_TONNE: Final[int] = 1000
"""Definition of the metric tonne (SI). The only mass conversion factor here."""

BASE_MASS_UNIT: Final[str] = "kg"
"""All internal computation is in kg. Convert to tonnes for display only."""


# =============================================================================
# MARS DATASET — published schema facts
# =============================================================================
#
# Source: the CSVs themselves, as inspected by etl/fetch_mars.py against the
# 2026-09-15 snapshot, cross-checked against the bundled definitions PDF and
# the online dictionary at methanedata.unep.org/dict-mars-plumes.

MARS_ARCHIVE_STEM: Final[str] = "unep_methanedata_detected_plumes_csv"
"""Stem of the published archive; local snapshots append the download date."""

MARS_SOURCES_MEMBER: Final[str] = "unep_methanedata_detected_sources.csv"
"""One row per emission source. This is the unit of accountability."""

MARS_PLUMES_MEMBER: Final[str] = "unep_methanedata_detected_plumes.csv"
"""One row per plume detection. This is the per-source event log."""

MARS_JOIN_KEY: Final[str] = "source_name"
"""Column joining plumes to sources, e.g. "LBY_S_119".

Nullable on the plumes table: unattributed plumes carry no source_name.
"""

# Sentinel values of the feedback_government / feedback_operator / feedback
# columns on the sources table. Verified against the 2026-09-15 snapshot; these
# three exhaust the observed values. Note it is "Not Applicable", NOT the
# "Not available" that earlier project notes claimed.
FEEDBACK_YES: Final[str] = "Yes"
FEEDBACK_NO: Final[str] = "No"
FEEDBACK_NOT_APPLICABLE: Final[str] = "Not Applicable"
"""Feedback was never solicited for this source, so silence is not a failure.

"Not Applicable" must never be counted as a non-response. UNEP only tracks
feedback for oil-and-gas sources with plumes detected after feedback tracking
began; everything else is out of scope for accountability.
"""

MARS_LICENCE: Final[str] = "CC BY-NC-SA 4.0"
MARS_ATTRIBUTION: Final[str] = "UNEP IMEO, Methane Alert and Response System (MARS)"
"""Attribution is a licence condition and must appear in the UI."""

MARS_PUBLICATION_LAG_DAYS_MIN: Final[int] = 30
MARS_PUBLICATION_LAG_DAYS_MAX: Final[int] = 75
"""Source: methanedata.unep.org. MARS data is NOT real-time; the UI must not
imply live alerting."""


# =============================================================================
# PATHS
# =============================================================================

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RAW_DIR: Final[Path] = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR: Final[Path] = PROJECT_ROOT / "data" / "processed"
MARS_DB_PATH: Final[Path] = PROCESSED_DIR / "mars.db"

SOURCES_TABLE: Final[str] = "sources"
PLUMES_TABLE: Final[str] = "plumes"
