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
- MARS field `ch4_fluxrate` is in kg/h (kilograms per hour).
- All internal computation uses kg as the base mass unit.
- Convert to tonnes only at the presentation boundary: tonnes = kg / 1000.
- Any function returning a mass must state the unit in its name or docstring.
- Write a test for every conversion. A 1000x error here invalidates the project.

## Data sources
- MARS sources and plumes: methanedata.unep.org, CSV and GeoJSON, direct
  download, no API key. Licence CC BY-NC-SA 4.0, so UNEP IMEO must be attributed
  in the UI. Publication lag is 30 to 75 days. This is NOT real-time data and the
  product must never imply it is.
  Key fields: country, lat, lon, location_basin, persistency_category,
  feedback_government (Yes/No/Not available), feedback_operator (same),
  asset_type, ch4_fluxrate (kg/h), ch4_fluxrate_std, detection dates, satellite.
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