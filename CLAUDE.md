# Methane Alert Accountability System

## What this is
A case-management and accountability layer for UNEP IMEO's Methane Alert and
Response System (MARS). MARS detects large methane plumes from satellites and
notifies governments and operators for free. Around 88% of notifications get no
response (over 900 sources). The gap is workflow, not detection: countries with
a designated focal point respond roughly 3x more often. This system attributes
each unanswered plume to a likely operator, costs it, and tracks it as a case
with an escalation clock.

## Hard rules — never violate
1. NO OUTBOUND COMMUNICATION. No email, SMS, webhook, or API call that contacts
   a real company, regulator, or individual. Ever. In any code path. Dossiers are
   generated and displayed only; a human decides whether to send. Do not add a
   send feature even if it seems useful.
2. NEVER state attribution as fact. Attribution is probabilistic and derived from
   open data. Every attributed operator must carry a confidence tier and the
   alternative candidates. UI copy says "likely operator", never "responsible for".
3. NEVER invent numbers. Every constant lives in backend/app/config.py with a
   cited source. No magic numbers inline. If a value is unknown, fail loudly.
4. NEVER fabricate data rows. If a dataset is missing, the code raises. No
   placeholder, sample, or mock leak records in anything that could reach the UI.

## Units — the single biggest bug risk
MARS USES TWO DIFFERENT MASS UNITS IN THE SAME TABLE. Confirmed from the
`definitions_detected_plumes_sources.pdf` bundled inside the download.
- `ch4_fluxrate` is in kg/h (kilograms per hour). It is a RATE, and an
  instantaneous snapshot at satellite overpass, not a cumulative total.
- `ch4_fluxrate_std` is also in kg/h. The PDF gives a usual range of
  200-1000 kg/h, useful as a sanity check on our reading, not as a filter.
- `total_emission` and `total_emission_std` are in TONNES, not kg. They are a
  MASS. Never combine them with fluxrate values without converting. Note this
  column is ~99.6% null, so nothing load-bearing should rest on it.
- Converting units does not make a rate comparable to a mass. Going from
  kg/h to a cumulative mass needs an explicit, stated duration assumption;
  that is a modelling decision, not a unit conversion, and must surface in
  the UI.
- All internal computation uses kg as the base mass unit.
- Convert to tonnes only at the presentation boundary: tonnes = kg / 1000.
- Any function returning a mass must state the unit in its name or docstring.
- Write a test for every conversion. A 1000x error here invalidates the project.

## Data sources
- MARS sources and plumes: methanedata.unep.org, CSV and GeoJSON, direct
  download, no API key. Licence CC BY-NC-SA 4.0, so UNEP IMEO must be attributed
  in the UI. Publication lag is 30 to 75 days. This is NOT real-time data and the
  product must never imply it is.
  The CSV download URL is a direct Azure blob link, recorded as
  MARS_CSV_ZIP_URL in etl/fetch_mars.py. methanedata.unep.org itself returns
  HTTP 403 to clients without a browser-like User-Agent; the blob does not.

  TWO TABLES, ONE ZIP. The archive contains both
  `unep_methanedata_detected_sources.csv` and
  `unep_methanedata_detected_plumes.csv`, plus the definitions PDF and the
  licence text. Schema below verified against the 2026-09-15 snapshot.

  sources (4,072 rows x 16 cols), one row per emission source:
    source_name (unique join key, e.g. "LBY_S_119"), lon, lat, country,
    sector, source_type, persistency, persistency_std, persistency_category,
    n_plumes_detected, id_last_plume, last_plume_date, notified,
    feedback, feedback_operator, feedback_government

  plumes (29,265 rows x 23 cols), one row per plume detection:
    id_plume, source_name (NULLABLE), satellite, tile_date, lat, lon,
    actionable, notified, country, sector, detection_institution,
    quantification_institution, tile, ch4_fluxrate, ch4_fluxrate_std,
    wind_u, wind_v, total_emission, total_emission_std, wind_speed,
    last_update, insert_date, tile_background

  Schema facts that earlier drafts of this file got wrong:
  - There is NO `location_basin` and NO `asset_type`. Use `sector` (7 values)
    and `source_type` (43 values), both on the sources table only.
  - The feedback columns live ONLY on sources, never on plumes. So a case is
    a source, not a plume.
  - Feedback values are Yes / No / "Not Applicable" — NOT "Not available".
    "Not Applicable" means UNEP never solicited feedback for that source and
    must NEVER be counted as a non-response. It is 2,369 of 4,072 sources
    (58%), so the accountability denominator is 1,703, not 4,072. Always show
    the denominator.
  - `feedback` is derived: Yes if either party replied. Prefer the two
    explicit party columns.
  - `persistency_category` values are lowercase except one: absent, sporadic,
    frequent, persistent, Undetermined. Match case-insensitively.
    `persistency` is a 0-1 float, null for every Undetermined row.
  - 6,323 plumes (21.6%) have a NULL `source_name` and are unattributed.
  - There is no operator or company name field anywhere. Attribution needs the
    external spatial join.
  - `wind_u`, `wind_v`, `wind_speed` are on the plumes table and are the
    handle on the downwind-displacement problem.
- Infrastructure for attribution: Global Energy Monitor trackers (CC BY 4.0,
  form-gated download) and/or EDF OGIM (Zenodo, GeoPackage).
- Raw snapshots live in data/raw/ and are never re-downloaded at runtime.

## Architecture
- etl/            one-off heavy geospatial conversion; geopandas/pyogrio allowed
- backend/app/    FastAPI service; the runtime path must NOT import geopandas
- data/raw/       committed immutable source snapshots
- data/processed/ normalised outputs produced by etl/
- SQLite for state: append-only event log for detections, derived case state.

## Attribution rules
- Produce a candidate SET per leak, never a single nearest-wins answer.
- Search radius is derived from satellite geolocation uncertainty, not guessed.
- Score combines distance, asset-type prior, and asset scale.
- Confidence tiers: HIGH (one close candidate), MEDIUM (clear leader),
  LOW (several plausible), NONE (nothing in radius).
- The plume centroid is displaced downwind of the true source. Acknowledge this
  in both the scoring and the UI.

## Coordinate handling
Local equirectangular projection about the leak point, in backend/app/geo.py.
Valid because matching radii are under about 10 km, where distortion is
negligible. pyproj is used ONLY in tests, to validate our maths against true
geodesic distances.

## Engineering conventions
- Python 3.12, pandas 3.x, numpy 2.x, shapely 2.x. Modern APIs only; do not write
  pandas 1.x-era code (no .append, no inplace-by-default assumptions).
- Type hints on every function. Pure functions wherever possible.
- Tests before implementation for anything numeric or geometric.
- Every non-obvious decision gets a line in DECISIONS.md.

## Definition of done for a slice
It runs, it has a test asserting a hand-checked value, units are documented,
no new magic numbers were introduced, and DECISIONS.md is updated if a choice
was made.

## Background and research
RESEARCH.md holds the full problem evidence with sources, dataset access details
and caveats, prior-art analysis, the six-layer solution design, stack rationale,
rejected alternatives, and competition logistics. Read it when you need context
for a decision, the source for a claim, or the reasoning behind an existing
choice. It is not loaded automatically.