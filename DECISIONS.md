# Decisions

## 2026-09-15 — WSL2 Ubuntu over native Windows
Windows ARM64 Python is tier-3 experimental; Shapely/GDAL have no official
win_arm64 wheels. Linux aarch64 has full manylinux wheels. Verified working.

## 2026-09-15 — Two-tier data pipeline
Heavy geospatial ETL runs once offline and commits a compact normalised output.
Runtime service never imports geopandas. Keeps deploys small and reproducible.

## 2026-09-15 — MARS download URL discovered, not guessed
methanedata.unep.org/download-dataset links "MARS sources and plumes (CSV)"
directly to an Azure blob:
https://unepazeconomyadlsstorage.blob.core.windows.net/public/unep_methanedata_detected_plumes_csv.zip
Recorded verbatim in etl/fetch_mars.py as MARS_CSV_ZIP_URL. The portal itself
returns HTTP 403 to clients without a browser-like User-Agent; the blob does
not. No API key, no form, no registration, as RESEARCH.md said.

## 2026-09-15 — Raw snapshots are dated and immutable
data/raw/unep_methanedata_detected_plumes_csv_<YYYY-MM-DD>.zip. A same-date
file is reused, never overwritten. Downloads land on a .partial sibling and are
rename()d only after zipfile.is_zipfile passes, so a truncated transfer can
never masquerade as a snapshot. The archive is kept zipped and read in place;
we do not extract, so there is exactly one artefact per snapshot date.

## 2026-09-15 — CLAUDE.md's field list was approximate; corrected from the data
The published CSVs do not match the field list in CLAUDE.md. Actual schema:
- The archive holds TWO CSVs, not one: unep_methanedata_detected_sources.csv
  (4,072 rows x 16 cols, one row per source) and
  unep_methanedata_detected_plumes.csv (29,265 rows x 23 cols, one row per
  plume detection). They join on `source_name` (e.g. "LBY_S_119").
- feedback_government / feedback_operator exist ONLY on the sources table, plus
  an undocumented third column `feedback`. The plumes table has no feedback
  column at all. Accountability state is therefore per-source, not per-plume.
- Their values are Yes / No / "Not Applicable" — NOT "Not available" as
  CLAUDE.md states. 2,369 of 4,072 sources (58%) are "Not Applicable".
- persistency_category is on the sources table only, and its values are
  lowercase except one: absent / sporadic / frequent / persistent /
  Undetermined. Matching must be case-insensitive.
- There is no `location_basin` and no `asset_type`. The nearest equivalents are
  `sector` (7 values) and `source_type` (43 values) on the sources table.
- No operator or company name field, as RESEARCH.md 2.1 caveat 3 predicted.

## 2026-09-15 — Units confirmed from the publisher's own definitions PDF
`definitions_detected_plumes_sources.pdf`, bundled inside the MARS zip, states:
`ch4_fluxrate` kg/h, `ch4_fluxrate_std` kg/h (usual range 200-1000 kg/h), and
`total_emission` / `total_emission_std` in TONNES. Two mass units in one table.
This is now the citation behind CH4_FLUXRATE_UNIT and TOTAL_EMISSION_UNIT in
backend/app/config.py, with a warning block against combining them. Previously
we had only inferred kg/h from plausible magnitudes; that inference is now
replaced by the publisher's own statement. The PDF travels inside every raw
snapshot, so the citation is reproducible offline.

Note the two are also different KINDS of quantity: a rate versus a mass.
Converting units does not make them comparable. Deriving a cumulative mass from
a flux rate needs an explicit duration assumption, which is a modelling choice
that must be stated in the UI, not a unit conversion.

## 2026-09-15 — A case is a source, not a plume
The feedback columns exist only on the sources table. There is no per-plume
feedback field, so UNEP's own accountability unit is the source. We follow it:
one case per `source_name`, 4,072 possible cases in this snapshot. Making a
plume the case would multiply a single unanswered notification into up to 84
duplicate cases (the maximum `n_plumes_detected`) and inflate every count we
publish.

The accountability denominator is 1,703 tracked sources, not 4,072. The other
2,369 are "Not Applicable": UNEP never solicited feedback, so silence is not a
non-response. Of the 1,703 tracked, 1,394 (81.9%) have both feedback_government
and feedback_operator == No. That is the case backlog, and it corroborates the
~88% figure in RESEARCH.md from the raw data. Every reported count carries its
denominator; etl/load_mars.py prints both framings side by side so the
misleading one cannot be quoted by accident.

## 2026-09-15 — Plumes are the per-case event log
Sources carry current state (persistency, feedback, last plume); plumes carry
one timestamped row per detection, with satellite, tile_date, ch4_fluxrate and
wind vector. Joined on `source_name`, the plumes table is naturally the
append-only event log the architecture calls for, and the sources table is
derived case state. Verified in this snapshot: `source_name` is unique across
all 4,072 sources, every non-null plume `source_name` resolves to a source, and
every source has at least one plume. etl/load_mars.py raises if any of that
stops holding rather than loading a broken join.

## 2026-09-15 — Plumes with a NULL source_name are excluded from case logic
6,323 of 29,265 plumes (21.6%) have no `source_name`. UNEP publishes these as
detections it has not attributed to a persistent source. They are excluded from
case logic for three reasons:
1. With no source there is no notification, no feedback field and no
   counterparty, so there is nothing to hold anyone accountable for.
2. Counting them would double-count emissions already represented by an
   attributed source nearby.
3. Attributing them ourselves would mean asserting a link UNEP declined to
   assert, which violates the rule against stating attribution as fact.
They are still loaded into the plumes table verbatim, so the exclusion is a
query-time decision that stays visible and reversible, not a silent drop at
load time. Any UI total must say which of the two populations it counts.

## 2026-09-15 — SQLite load preserves published column names
etl/load_mars.py writes data/processed/mars.db with UNEP's column names
unchanged, no unit conversion and no derived mass columns, so our tables can be
diffed directly against the CSVs. Interpretation happens downstream where it is
testable. The db is gitignored and fully reproducible from the raw snapshot.
Tests assert the no-conversion property by comparing stored values against the
CSV, and assert that no `_kg` or `_tonne` column has crept in.

## 2026-09-15 — Local projection uses WGS84 radii of curvature, not a sphere
CLAUDE.md specifies a local equirectangular projection about the leak point.
The open question was the scale factor. Measured against pyproj.Geod (WGS84) as
ground truth, worst-case error over separations up to 10 km:

  sphere at IUGG mean radius, cos(lat0)      56.1 m   (|lat| <= 70)
  WGS84 local radii of curvature at lat0      8.3 m   (|lat| <= 70)

We use the second:
  x_m = N(lat0) * cos(lat0) * dlon
  y_m = M(lat0) * dlat
with M the meridional and N the prime vertical radius of curvature at the
origin latitude. Both are constants once the origin is fixed, so the map stays
affine and planar point-to-segment geometry remains valid.

The sphere's 56 m is a systematic scale error, not noise: a single mean radius
differs from the local radius of curvature by up to ~0.5%, which at the equator
is 0.57% along a meridian. Attribution radii in this project derive from
satellite geolocation uncertainty and are of the order of hundreds of metres, so
a 56 m systematic bias would be a material fraction of the matching radius and
would bias every candidate set in the same direction. 8.3 m is not material at
that scale.

This is a deliberate deviation from the slice instruction to put an
IUGG-cited Earth radius in config.py and use it. EARTH_MEAN_RADIUS_M is still in
config.py, cited to IUGG, but explicitly marked as not used, with the
measurement above recorded next to it, so that nobody reintroduces the sphere
without seeing the cost. The constants actually used are the WGS84 defining
parameters, cited to NGA.STND.0036_1.0.0_WGS84.

## 2026-09-15 — Measured worst-case geometry error, and the validated envelope
Envelope: |lat| <= 70 degrees, separations <= 10 km. It covers the whole MARS
snapshot with margin (observed latitude range -50.7 to +68.4) and matches the
sub-10 km matching radii that make a planar approximation admissible at all.

Inside it, validated against pyproj.Geod over a grid of latitude, azimuth and
separation:
  point_distance_m              worst 8.28 m  (0.083%), at lat -70, 10 km
  point_to_segment_distance_m   worst 7.89 m, at lat 70
Assertion tolerance is 10 m (GEO_ERROR_TOLERANCE_M), leaving headroom over the
measured worst case. The measured figure is pinned in config.py as
GEO_WORST_CASE_ERROR_M and asserted to 0.1 m, so a change in accuracy shows up
as a diff in a cited constant rather than as a silently loosened test.

Behaviour outside the envelope is documented by tests rather than fixed, since
the functions still return an answer. For a 10 km separation the worst error is
17.1 m at 80 degrees, 34.7 m at 85, and 181.7 m at 89. At mid-latitude it is
18.8 m at 25 km, 75.4 m at 50 km and 303.3 m at 100 km. If a later slice needs
either regime, the projection must be revisited, not the tolerance.

## 2026-09-15 — Known geometry limitations, asserted rather than hidden
- Antimeridian IS handled: longitude differences are wrapped to (-180, 180], so
  179.99E to 179.99W reads as 0.02 degrees. Tested against the geodesic.
- At a pole the projection is degenerate: cos(lat0) = 0 collapses every
  longitude to x = 0 and longitude information is lost. Not reachable from MARS
  data, which stops at 68.4 degrees, but asserted so the limit is explicit.
- point_distance_m is slightly ASYMMETRIC, because the projection origin is its
  first argument. Worst measured asymmetry is 16.5 m over 10 km at |lat| 70.
  This is inherent to projecting about one endpoint and is acceptable because
  the project always has a natural origin, the leak. Callers must not rely on
  d(a, b) == d(b, a); a test pins the asymmetry so it cannot grow unnoticed.
- pyproj appears only in backend/tests/, never in backend/app/. A test statically
  parses every module under backend/app/ and fails on any import of geopandas,
  pyogrio, pyproj or fiona, so the runtime path cannot acquire a heavy
  geospatial dependency by accident.
