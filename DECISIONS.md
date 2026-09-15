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
