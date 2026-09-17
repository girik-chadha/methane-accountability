"""Central configuration and cited constants.

Hard rule: no magic numbers anywhere else in the codebase. Every value here
carries the source it came from. If a value is not known, it is absent from
this file and the code that needs it raises, rather than guessing.
"""

from __future__ import annotations

from datetime import date
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


# =============================================================================
# GEODESY — reference ellipsoid and the validated envelope of backend/app/geo.py
# =============================================================================
#
# geo.py uses a local equirectangular projection about the leak point. Its scale
# factors are the LOCAL RADII OF CURVATURE of the WGS84 ellipsoid at the origin
# latitude, not a single global Earth radius. See DECISIONS.md, 2026-09-15, for
# the measurement behind that choice.

WGS84_SEMI_MAJOR_AXIS_M: Final[float] = 6378137.0
"""WGS84 semi-major axis a, in metres. Defining constant.

Source: NGA.STND.0036_1.0.0_WGS84, "Department of Defense World Geodetic System
1984", version 1.0.0, 2014, table 3.1. This is the ellipsoid pyproj's
Geod(ellps="WGS84") uses, so our tests compare like with like.
"""

WGS84_INVERSE_FLATTENING: Final[float] = 298.257223563
"""WGS84 inverse flattening 1/f. Defining constant. Source: as above."""

WGS84_FLATTENING: Final[float] = 1.0 / WGS84_INVERSE_FLATTENING
"""Derived from the defining constant above, not independently sourced."""

WGS84_ECCENTRICITY_SQUARED: Final[float] = WGS84_FLATTENING * (
    2.0 - WGS84_FLATTENING
)
"""First eccentricity squared, e^2 = f(2 - f). Derived, standard identity."""

EARTH_MEAN_RADIUS_M: Final[float] = 6371008.771
"""IUGG mean radius R1 = (2a + b) / 3, in metres.

Source: International Union of Geodesy and Geophysics, as reported in the IUGG
geodetic reference system; the same value appears in NGA.STND.0036_1.0.0_WGS84.

DELIBERATELY NOT USED BY geo.py. It is recorded here as a signpost: treating
the Earth as a sphere of this radius was measured at up to 56 m of error over a
10 km separation, against 8.3 m for the ellipsoidal form we use, because a
single mean radius carries a systematic scale error of ~0.5% against the local
radius of curvature. Do not reintroduce it into the projection. See DECISIONS.md.
"""

GEO_VALIDATED_LATITUDE_LIMIT_DEG: Final[float] = 70.0
"""Absolute latitude up to which geo.py's accuracy is tested and asserted.

Chosen because the 2026-09-15 MARS snapshot spans -50.7 to +68.4 degrees, so
this envelope covers every row in the dataset with margin. The projection still
returns an answer outside it, with degrading accuracy; it does not raise.
"""

GEO_VALIDATED_SEPARATION_M: Final[float] = 10_000.0
"""Separation up to which geo.py's accuracy is tested and asserted, in metres.

CLAUDE.md fixes attribution matching radii below about 10 km, which is what
makes the planar approximation admissible in the first place.
"""

GEO_WORST_CASE_ERROR_M: Final[float] = 8.28
"""Measured worst-case error against pyproj.Geod inside the envelope, in metres.

Measured by backend/tests/test_geo.py over a grid of latitudes, azimuths and
separations. Equivalently 0.083% of the separation. Recorded so that a future
change which degrades accuracy is visible as a diff here, not just a test
tweak.
"""

GEO_ERROR_TOLERANCE_M: Final[float] = 10.0
"""Assertion tolerance inside the envelope, in metres.

Headroom over the measured 8.28 m worst case. A failure against this tolerance
means the projection changed, not that the tolerance was too tight.
"""


# =============================================================================
# GLOBAL ENERGY MONITOR — infrastructure snapshots for attribution
# =============================================================================
#
# Source: Global Energy Monitor, Global Gas Infrastructure Tracker (GGIT) and
# Global Energy Ownership Tracker, downloaded 2026-09-15 via GEM's form-gated
# download page and committed under data/raw/ as immutable snapshots. Licence
# CC BY 4.0: GEM must be attributed wherever this data is displayed.

GEM_ATTRIBUTION: Final[str] = "Global Energy Monitor"
GEM_LICENCE: Final[str] = "CC BY 4.0"

GEM_GAS_PIPELINES_GPKG: Final[str] = "GEM-GGIT-Gas-Pipelines-2025-11.gpkg"
"""GGIT gas pipelines, November 2025 release. Linestring geometries."""

GEM_LNG_TERMINALS_GPKG: Final[str] = "GEM-GGIT-LNG-Terminals-2025-09.gpkg"
"""GGIT LNG terminals, September 2025 release. Point geometries."""

GEM_OWNERSHIP_XLSX: Final[str] = "Global-Energy-Ownership-Tracker-August-2026-V2.xlsx"
"""Global Energy Ownership Tracker, August 2026 V2. Resolves assets to parents."""

GEM_EXTRACTION_XLSX: Final[str] = "Global-Oil-and-Gas-Extraction-Tracker-March-2026.xlsx"
"""Global Oil and Gas Extraction Tracker, March 2026 release."""

INFRA_REQUIRED_EPSG: Final[int] = 4326
"""Every infrastructure layer must arrive in EPSG:4326 (WGS84 lon/lat).

This is the datum MARS coordinates are published in and the ellipsoid geo.py
assumes. A layer in any other CRS is refused, never silently reprojected,
because a silent reprojection would hide a datum mismatch inside the
attribution distances.
"""

INFRA_VERTEX_SPACING_FLAG_M: Final[float] = 5_000.0
"""Consecutive-vertex spacing above which a pipeline segment is flagged, metres.

Chosen for the slice-3b inspection as half of GEO_VALIDATED_SEPARATION_M: a
straight chord between vertices this far apart is a questionable stand-in for
the pipeline's true path, and its endpoints can lie outside the validated
projection envelope even when the segment passes close to a leak. This is a
reporting threshold, not a densification interval; that decision is pending
and will be recorded in DECISIONS.md when made.
"""

EXTRACTION_FIELD_SHEET: Final[str] = "Field-level main data"
"""Sheet of GEM_EXTRACTION_XLSX with one row per field/unit (Unit ID)."""

EXTRACTION_PROJECT_SHEET: Final[str] = "Project-level main data"
"""Sheet of GEM_EXTRACTION_XLSX with one row per project, listing its units."""

EXTRACTION_UNIT_LIST_SEPARATOR: Final[str] = ", "
"""Separator GEM uses inside "Units (list of IDs)". Observed 2026-09-15."""

GEM_GOGPT_XLSX: Final[str] = "Global Oil and Gas Plant Tracker (GOGPT) - August 2026.xlsx"
"""GEM Global Oil and Gas Plant Tracker: gas- and oil-FIRED POWER PLANTS, not
upstream processing. Units sheet "Gas & Oil Units". Carries GEM Entity IDs."""

GOGPT_UNITS_SHEET: Final[str] = "Gas & Oil Units"

OGIM_GPKG: Final[str] = "OGIM_v2.7.gpkg"
"""EDF Oil and Gas Infrastructure Mapping database v2.7 (Zenodo,
doi:10.5281/zenodo.7466757), README dated 2025-03-28. 2.9 GB, gitignored;
17 layers, EPSG:4326. COUNTRY is an uppercase UN member-state name, and a
comma-separated list for lines/polygons spanning several countries."""

MARS_TO_GEM_COUNTRY_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "United States of America": ("United States",),
    "Iran (Islamic Republic of)": ("Iran",),
    "Russian Federation": ("Russia",),
    "Syrian Arab Republic": ("Syria",),
    "Viet Nam": ("Vietnam",),
}
"""MARS (UN-style) country names whose GEM spelling differs. Found by the
verbatim comparison in slice 3c; these five cover 474 of 1,394 cases. OGIM
uses the UN spelling, so it needs no aliases. Matching is case-insensitive."""


# =============================================================================
# ASSET TABLE — data/processed/assets.parquet, built by etl/build_assets.py
# =============================================================================

ASSETS_PARQUET_PATH: Final[Path] = PROCESSED_DIR / "assets.parquet"
"""Every usable infrastructure asset in one flat schema, geometry as WKB so
the runtime can read it with shapely alone."""

PIPELINE_DENSIFY_MAX_SPACING_M: Final[float] = 2_000.0
"""Maximum vertex spacing after geodesic densification of pipelines, metres.

Source: DECISIONS.md 2026-09-15, "GEM pipeline vertex spacing, measured":
with 2 km spacing the worst chord-versus-geodesic error of
point_to_segment_distance_m against densified-geodesic truth is 0.51-0.66 m
for any original spacing up to 100 km, against GEO_ERROR_TOLERANCE_M = 10 m.
"""

# Status vocabularies are GEM's own, observed 2026-09-15. An asset is usable
# for attributing a detected leak only if it can physically hold or move
# hydrocarbons today; proposed, cancelled, shelved and retired assets cannot.
GEM_PIPELINE_USABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {"operating", "idle", "mothballed", "mixed status"}
)
GEM_LNG_USABLE_STATUSES: Final[frozenset[str]] = frozenset({"operating", "idled", "mothballed"})
GEM_EXTRACTION_USABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {"operating", "mothballed", "in-development", "underground gas storage", ""}
)
"""Empty string = status not recorded (213 units); unknown is not unusable."""

OGIM_EXCLUDED_STATUSES: Final[frozenset[str]] = frozenset({"PROPOSED"})
"""OGIM_STATUS values excluded: a permitted-but-undrilled well cannot leak.
Abandoned wells are retained; they are a documented methane source class."""

OGIM_NULL_TEXT: Final[str] = "N/A"
OGIM_NULL_NUMERIC: Final[float] = -999.0
"""OGIM's null conventions, from the README schema and observed in the data."""

OGIM_LAYER_ASSET_KINDS: Final[dict[str, str]] = {
    "Crude_Oil_Refineries": "refinery",
    "Equipment_and_Components": "equipment_component",
    "Gathering_and_Processing": "gathering_processing",
    "Injection_and_Disposal": "injection_disposal",
    "LNG_Facilities": "lng_facility",
    "Natural_Gas_Compressor_Stations": "compressor_station",
    "Natural_Gas_Flaring_Detections": "flare_detection",
    "Offshore_Platforms": "offshore_platform",
    "Oil_and_Natural_Gas_Wells": "well",
    "Petroleum_Terminals": "petroleum_terminal",
    "Stations_Other": "station_other",
    "Tank_Battery": "tank_battery",
}
"""OGIM point layers included, and the asset_kind each becomes. Polygon and
line layers (fields, basins, blocks, pipelines) are not included from OGIM;
GEM covers pipelines with per-feature declared lengths, and OGIM's field
polygons carry no operator."""

GEM_UNKNOWN_PARTY_TOKENS: Final[frozenset[str]] = frozenset({"", "--", "n/a", "unknown"})
"""Casefolded values of Operator/Owner/Parent that mean 'not recorded'. A value
that merely STARTS with 'unknown' (e.g. 'unknown [unknown %]') also counts."""

PLACEHOLDER_GROUP_MIN_SIZE: Final[int] = 2
"""Assets sharing identical geometry form a placeholder_group once there are
at least this many. Source: definition; two identical coordinates are already
physically indistinguishable."""


# =============================================================================
# ATTRIBUTION — search radius, wind handling, priors, tiers
# =============================================================================

# --- Satellite spatial resolution, metres, keyed by the exact MARS `satellite`
# string. NONE of these numbers come from UNEP: the MARS data dictionary and
# the "approach & methodology" page name the instruments without resolutions,
# the bundled definitions PDF does not state them, and the IEA/IMEO guidance
# "Responding to Satellite Notifications from MARS" says only that high
# resolution satellites have "a pixel size of approximately [N] metres" (the
# digits are undecodable in the PDF text). Each value below is therefore the
# ground sampling distance published by the instrument's operating agency,
# for the bands used in methane retrieval, and cited as such. Instruments not
# listed here make search_radius_m raise; nothing is guessed.
SATELLITE_RESOLUTION_M: Final[dict[str, float]] = {
    # ESA, Sentinel-2 MSI User Guide: SWIR bands B11 (1610 nm) and B12
    # (2190 nm) at 20 m. Methane retrieval uses B11/B12.
    "Sentinel-2 - ESA": 20.0,
    # USGS, Landsat 8-9 OLI/TIRS Collection 2 Data Format Control Book: SWIR
    # bands 6 and 7 at 30 m.
    "Landsat - NASA/USGS": 30.0,
    # ASI, PRISMA Products Specification: hyperspectral VNIR/SWIR at 30 m GSD.
    "PRISMA - ASI": 30.0,
    # DLR, EnMAP mission description: 30 m ground sampling distance.
    "EnMAP - DLR": 30.0,
    # NASA JPL, EMIT instrument description: ~60 m GSD from the ISS.
    "EMIT - NASA": 60.0,
    # ESA, Sentinel-5P TROPOMI: 5.5 x 7 km at nadir since 6 Aug 2019; the
    # larger dimension is used.
    "Sentinel-5P/TROPOMI - ESA": 7_000.0,
    # ESA, Sentinel-3 SLSTR: SWIR bands S5/S6 at 500 m.
    "Sentinel-3 - ESA": 500.0,
    # NOAA/NASA, VIIRS: moderate-resolution (M) bands at 750 m nadir.
    "VIIRS - NASA/NOAA": 750.0,
    # Planet / Carbon Mapper, Tanager-1: ~30 m GSD imaging spectrometer.
    "Planet Tanager": 30.0,
    # "GOES - NOAA" is deliberately absent (5 plumes in the snapshot, none on
    # an unanswered case); a GOES-only source will raise until sourced.
}

SEARCH_RADIUS_PIXELS: Final[int] = 3
"""ASSUMPTION, not a measurement, flagged for ratification.

The published source location is localised to the instrument's pixel; allow
one further pixel of image geolocation error and one of analyst placement of
the plume origin. Three pixel widths therefore bound the source position.
UNEP does not publish a location uncertainty. If a sourced figure appears,
replace this and re-run.
"""

MARS_UPWIND_OFFSET_M: Final[float] = 0.0
"""Upwind offset applied to MARS coordinates before searching, metres.

MEASURED, 2026-09-15 snapshot: the MARS data dictionary defines a plume row's
lat/lon as "coordinate of the source location", and all 22,900 plume rows with
wind data carry coordinates byte-identical to their source's. UNEP has already
placed the origin; the published point is not a plume centroid. Back-projecting
it upwind would move the search away from UNEP's estimate, so the offset is
zero. The wind machinery is retained (and tested) for any dataset that does
publish centroids.
"""

SOURCE_TYPE_COMPATIBLE_KINDS: Final[dict[str, frozenset[str]]] = {
    "Gas disposal facility": frozenset({"flare_detection", "gathering_processing", "injection_disposal", "tank_battery", "station_other", "equipment_component", "extraction_unit", "field_outline"}),
    "Flare": frozenset({"flare_detection", "gathering_processing", "refinery", "lng_facility", "lng_terminal", "offshore_platform", "petroleum_terminal", "extraction_unit", "field_outline", "equipment_component", "tank_battery"}),
    "Transmission Pipelines": frozenset({"gas_pipeline", "compressor_station"}),
    "Pipeline": frozenset({"gas_pipeline", "compressor_station", "station_other"}),
    "Pipeline valve": frozenset({"gas_pipeline", "compressor_station", "station_other"}),
    "Transmission Compressor Station": frozenset({"compressor_station", "gas_pipeline", "station_other"}),
    "Compressor station": frozenset({"compressor_station", "gas_pipeline", "station_other", "gathering_processing"}),
    "Transmission Stations": frozenset({"compressor_station", "station_other", "gas_pipeline"}),
    "Blowdown stack": frozenset({"compressor_station", "gas_pipeline", "gathering_processing", "station_other"}),
    "Well head": frozenset({"well", "extraction_unit", "field_outline", "tank_battery", "equipment_component"}),
    "Well pad": frozenset({"well", "extraction_unit", "field_outline", "tank_battery", "equipment_component"}),
    "Abandoned wells": frozenset({"well", "extraction_unit", "field_outline"}),
    "Gathering and boosting facilities": frozenset({"gathering_processing", "compressor_station", "tank_battery", "station_other", "equipment_component", "extraction_unit", "field_outline"}),
    "Tank batteries": frozenset({"tank_battery", "gathering_processing", "equipment_component", "extraction_unit", "field_outline"}),
    "Pit": frozenset({"tank_battery", "injection_disposal", "gathering_processing", "well", "equipment_component", "extraction_unit", "field_outline"}),
    "Gas Processing Unit": frozenset({"gathering_processing", "compressor_station", "station_other", "lng_facility", "refinery"}),
    "Production Offshore": frozenset({"offshore_platform", "extraction_unit", "field_outline", "well"}),
    "Offshore platform": frozenset({"offshore_platform", "extraction_unit", "field_outline", "well"}),
    "Petrochemical plant": frozenset({"refinery", "gathering_processing", "station_other"}),
    "Petrochemical plant component": frozenset({"refinery", "gathering_processing", "station_other"}),
    "UGS": frozenset({"injection_disposal", "station_other", "compressor_station", "extraction_unit"}),
    "Shipping": frozenset({"lng_terminal", "lng_facility", "petroleum_terminal"}),
    "LNG Liquefaction": frozenset({"lng_facility", "lng_terminal"}),
}
"""Which asset kinds are a plausible physical match for a MARS source_type.

STRUCTURAL ASSUMPTION, not a measurement: an ordinal prior, used only to order
candidates that are all inside the search radius (where they are positionally
indistinguishable). It carries no numeric weight, so no number was invented.
Source types absent here ("O&G facility (generic)", "O&G source (generic)")
treat every kind as compatible.
"""

ROUTE_FIDELITY_TRACED_RANGE: Final[tuple[float, float]] = (0.9, 1.1)
"""A pipeline whose geodesic length is within this ratio of GEM's declared
length is treated as a traced route; outside it, as a possible placeholder.
Source: the +/-10% reporting band used in DECISIONS.md 2026-09-15 (57% of
"high" accuracy features fall inside it, 35% of "low")."""

TIER_OPERATOR_NAMED: Final[str] = "OPERATOR_NAMED"
TIER_ASSET_ONLY: Final[str] = "ASSET_ONLY"
TIER_UNMAPPED: Final[str] = "UNMAPPED"

MAX_CANDIDATES_LISTED: Final[int] = 25
"""Reporting cap on the candidates carried in a result; the full count is
always reported alongside."""

RADIUS_SENSITIVITY_MULTIPLIERS: Final[tuple[float, ...]] = (2.0, 5.0)
"""Batch summary re-runs candidate generation at these multiples of the
derived radius, to show whether the radius is the binding constraint."""


# =============================================================================
# UNCERTAINTY BUDGET — per-pair match threshold = sqrt(plume_sigma^2 + asset_sigma^2)
# =============================================================================
#
# plume_sigma is SEARCH_RADIUS_PIXELS x resolution (above). asset_sigma is per
# asset and recorded in assets.parquet as sigma_m with a sigma_basis string
# saying whether it was MEASURED from our data, SOURCED from a specification,
# or ASSUMED. Only "assumed:" bases are scaled in the sensitivity table.

ASSETS_SIGMA_SIDECAR_PATH: Final[Path] = PROCESSED_DIR / "assets_sigma.json"
"""Written by etl/build_assets.py: the measured extraction-outline percentiles
and the assumed/sourced sigmas actually applied, so the attribution run can
show sensitivity without any number being copied by hand."""

EXTRACTION_SIGMA_PERCENTILE: Final[int] = 50
"""Percentile of the equivalent-circle radius sqrt(area / pi) of the usable
field outlines used as sigma for extraction units WITHOUT an outline.

MEASURED (2026-09-15 snapshot, 917 usable outlines): p25 1.90 km, p50 3.29 km,
p75 5.19 km, p90 8.17 km; by GEM Location accuracy, p50 3.32 km for "exact"
(n=879) and 4.84 km for "approximate" (n=20). The median is used because sigma
is a 1-sigma scale, not a bound: a uniform disc of radius r already has RMS
point-to-centre distance 0.71 r, so the median radius is conservative for the
median field. Caveat: outlined units skew offshore (49% vs 21%), so this may
overstate the extent of typical point-only units. Sensitivity runs p25-p90.
"""

EXTRACTION_SIGMA_SENSITIVITY_PERCENTILES: Final[tuple[int, ...]] = (25, 50, 75, 90)

PIPELINE_SIGMA_BY_ROUTE_ACCURACY_M: Final[dict[str, float]] = {
    "very high (within meters)": 10.0,
    "high": 100.0,
    "medium": 500.0,
    "low": 2_000.0,
    "no route": 2_000.0,
}
"""ASSUMED positional error of a GEM route, per GEM RouteAccuracy class.

GEM defines only "very high (within meters)" in metres; the others are
interpreted on a decade scale. Supporting evidence, route_fidelity (geodesic
length / declared length) within +/-10%: very high 70%, high 59%, medium 43%,
low 35%; that orders the classes as assumed but does not fix their metres.
"no route" features that nevertheless carry geometry (13) are treated as low.
Placeholder chords can be tens of km off; no sigma repairs those.
"""

LNG_SIGMA_BY_ACCURACY_M: Final[dict[str, float]] = {"exact": 500.0, "approximate": 2_000.0}
"""ASSUMED. GEM's Accuracy says whether the point is on the terminal, not how
big the terminal is; a leak can be anywhere on a site that is typically
several hundred metres across, hence 500 m for "exact". "approximate"
points are placed by locality; 2 km."""

OGIM_SIGMA_M: Final[float] = 100.0
"""ASSUMED, one value for the whole source: the OGIM README states no
positional precision. Records are mostly government registry coordinates
(well surface holes, facility points), typically accurate to tens of metres,
with some facility points placed by name at a coarser scale."""

VIIRS_FLARE_SIGMA_M: Final[float] = SATELLITE_RESOLUTION_M["VIIRS - NASA/NOAA"]
"""SOURCED: a VIIRS flare detection is located to its 750 m M-band pixel
(NOAA VIIRS specification, the same citation as SATELLITE_RESOLUTION_M)."""

SIGMA_BASIS_ASSUMED_PREFIX: Final[str] = "assumed:"
SIGMA_SENSITIVITY_MULTIPLIERS: Final[tuple[float, ...]] = (0.5, 2.0)
"""Multipliers applied to every ASSUMED asset sigma in the sensitivity table."""


# =============================================================================
# COSTING — GWP, methane properties, gas price. kg is the base unit throughout.
# =============================================================================

METHANE_GWP100_FOSSIL_AR6: Final[float] = 29.8
"""IPCC AR6 WG1, Chapter 7 (Forster et al., 2021), Table 7.15, "CH4-fossil",
GWP-100 = 29.8 +/- 11. Machine-verified against the chapter PDF text on
2026-09-15 (the same row gives GWP-20 82.5 and GWP-500 10.0; the non-fossil
row gives GWP-100 27.0). Fossil-origin methane is the right row for oil and
gas sources because the metric includes the CO2 its oxidation adds."""

METHANE_GWP100_FOSSIL_AR6_UNCERTAINTY: Final[float] = 11.0
"""Same table, the +/- on 29.8. Carried so a presentation can show it."""

METHANE_MOLAR_MASS_KG_PER_MOL: Final[float] = 0.016043
"""C 12.011 + 4 x H 1.008 = 16.043 g/mol, IUPAC standard atomic weights."""

GAS_CONSTANT_J_PER_MOL_K: Final[float] = 8.314462618
"""Exact by definition of the SI (2019 redefinition), CODATA 2018."""

STANDARD_REFERENCE_TEMPERATURE_K: Final[float] = 288.15
STANDARD_REFERENCE_PRESSURE_PA: Final[float] = 101_325.0
"""ISO 13443 standard reference conditions for natural gas: 15 C, 101.325 kPa."""

METHANE_DENSITY_KG_PER_M3: Final[float] = (
    STANDARD_REFERENCE_PRESSURE_PA * METHANE_MOLAR_MASS_KG_PER_MOL
    / (GAS_CONSTANT_J_PER_MOL_K * STANDARD_REFERENCE_TEMPERATURE_K)
)
"""Ideal-gas density at ISO 13443 reference conditions, derived from the
defining constants above: 0.6785 kg/m3. Real methane has a compressibility of
about 0.998 there, a 0.2% effect, ignored. Used only to present a volume."""

METHANE_HHV_KJ_PER_MOL: Final[float] = 890.8
"""Standard enthalpy of combustion of methane (gas), NIST Chemistry WebBook,
SRD 69: delta_c H = -890.8 +/- 0.4 kJ/mol. Higher heating value basis."""

METHANE_HHV_MJ_PER_KG: Final[float] = METHANE_HHV_KJ_PER_MOL / METHANE_MOLAR_MASS_KG_PER_MOL / 1000.0
"""Derived: 890.8 kJ/mol / 0.016043 kg/mol = 55,526 kJ/kg = 55.53 MJ/kg.
(A first draft divided by 1000 twice; backend/tests/test_costing.py caught
the 1000x error, which is the reason those tests exist.)"""

MJ_PER_MMBTU: Final[float] = 1055.05585262
"""1 Btu (International Table) = 1055.05585262 J exactly, NIST SP 811."""

GAS_PRICE_USD_PER_MMBTU: Final[float] = 2.78
"""Henry Hub natural gas spot price, MONTHLY AVERAGE for August 2026, US EIA
series RNGWHHD (Dollars per Million Btu), retrieved 2026-09-15 from
eia.gov/dnav/ng/hist/rngwhhdM.htm. PRICES MOVE: the 2026 monthly averages
so far run from 2.77 (April) to 7.72 (January). One benchmark, one month;
a presentation must say so."""

GAS_PRICE_BENCHMARK: Final[str] = "Henry Hub spot, monthly average, US EIA series RNGWHHD"
GAS_PRICE_PERIOD: Final[str] = "August 2026"
GAS_PRICE_RETRIEVED: Final[str] = "2026-09-15"

MARS_TYPICAL_DETECTABLE_FLUX_KG_PER_H: Final[tuple[float, float]] = (500.0, 10_000.0)
"""MARS data dictionary, ch4_fluxrate: "typical emissions detectable by
satellites ranging from 500 to 10,000 kg/h". Used as a column-level unit
guard: a flux column whose median falls outside this band is not in kg/h."""

DETECTION_ATTRIBUTABLE_WINDOW_DAYS: Final[float] = 11.1
"""MEASURED. Each detection is credited with emission for one typical
inter-detection interval, centred on it, instead of implying continuity to
the next detection however far away. The interval is the median gap between
consecutive detections of the same source over all attributed MARS plumes in
the 2026-09-15 snapshot (18,855 gaps: p25 3.0, p50 11.1, p75 67.9 days). It
is the period within which a still-emitting source would typically have been
seen again. Choosing the median rather than another percentile is the one
modelling choice here. Overlapping windows are merged, never double counted,
so frequently re-detected sources are still treated as continuous."""

DETECTION_ATTRIBUTABLE_WINDOW_H: Final[float] = DETECTION_ATTRIBUTABLE_WINDOW_DAYS * 24.0

MARS_SNAPSHOT_DATE: Final[date] = date(2026, 9, 15)
"""Date of the committed raw snapshot (unep_methanedata_detected_plumes_csv_2026-09-15.zip);
the plumes table's latest insert_date is the same day. 'Days since last
detection' is measured to this date, so it includes MARS's 30-75 day
publication lag."""

MARS_UNANSWERED_CASE_COUNT: Final[int] = 1_394
"""Hand-checked count of sources with feedback_government == No AND
feedback_operator == No in the 2026-09-15 snapshot (1,394 of 1,703 tracked
sources; DECISIONS.md 2026-09-15 "A case is a source, not a plume"; asserted
by backend/tests/test_load_mars.py). The web export refuses to write any
other number of cases: a different count means the backlog filter is wrong."""


# =============================================================================
# WEB EXPORT — etl/export_web.py writes web/data.json; the runtime only serves it
# =============================================================================

COUNTRY_RINGS_PATH: Final[Path] = RAW_DIR / "country_rings.json"
"""Natural Earth 1:110m admin-0 country outlines (public domain), thinned and
rounded to 2 decimals, as extracted from the committed demo frontend at
7da3618 on 2026-09-17. Carries NAME, CONTINENT, ISO_A3 and a bbox per
country. No Natural Earth file is downloaded at build or run time."""

WEB_DATA_PATH: Final[Path] = PROJECT_ROOT / "web" / "data.json"
"""The precomputed blob the frontend boots from. Produced by the export,
never by the runtime."""

WEB_DATA_TARGET_BYTES: Final[int] = 1_000_000
"""Size target for web/data.json, so the whole map boots in one request.
A larger file is reported, not refused."""

WEB_TIER_LABELS: Final[dict[str, str]] = {
    TIER_OPERATOR_NAMED: "named",
    TIER_ASSET_ONLY: "asset",
    TIER_UNMAPPED: "unmapped",
}
"""Attribution tier to the frontend's `t` vocabulary. The three tiers
exhaust the frontend's TIER table; anything else must raise."""

COUNTRIES_WITHOUT_NE110M_POLYGON: Final[dict[str, str]] = {"Bahrain": "Asia"}
"""MARS case countries that Natural Earth omits at 1:110m (too small), with
their continent. They get an empty ring set and a bbox from their own
leaks, so the country is reachable from the list even though it does not
draw. Any other MARS country missing from the rings is an error."""

SATELLITE_AGENCY_SEPARATOR: Final[str] = " - "
"""MARS satellite strings are "<instrument> - <agency>" ("Sentinel-2 - ESA").
The frontend shows the instrument only; the agencies are credited in the
sources footer."""

PARTY_SHARE_PATTERN: Final[str] = r"\s*\[[^\]]*\]"
"""GEM ownership strings carry a share bracket per party: "Name [100.00%]",
"Name [unknown %]". Stripped for display only; every party is kept, joined
by GEM's own "; " separator, so co-owners are never hidden."""

WEB_DIR: Final[Path] = PROJECT_ROOT / "web"
"""Static frontend root served by backend/app/main.py."""

WEB_CASES_DETAIL_PATH: Final[Path] = WEB_DIR / "cases.json"
"""Per-case detail the drill-down API serves: costing with `capped` and
`upper_bound` as SEPARATE objects, and the full candidate set. Produced by
the export alongside data.json; the runtime cannot recompute either."""

WEB_CANDIDATE_CARDS: Final[int] = 5
"""Candidates carried per leak in web/data.json, in the attribution's own
order. The 4D run's candidate-set p90 is 3 (DECISIONS 2026-09-15), so five
cards show the complete set for over nine cases in ten; the page states the
count of any not shown. Never a fabricated card: only exported values."""

WEB_BOOT_LINE: Final[str] = "const {C, L, M} = await (await fetch('/api/data')).json();"
"""The one line of web/index.html that loads data. The page holds no data of
its own; the standalone build replaces exactly this line with the blob."""

WEB_STANDALONE_PATH: Final[Path] = WEB_DIR / "standalone.html"
"""index.html with web/data.json inlined in place of WEB_BOOT_LINE, so the
page opens from disk with no server. The demo fallback and offline backup;
regenerated by `python -m etl.export_web --standalone` and by every full
export, and a test pins it byte-for-byte to index.html + data.json."""

