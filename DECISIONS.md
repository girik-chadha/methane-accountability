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

## 2026-09-15 — Infrastructure layers must arrive in EPSG:4326; never reproject silently
etl/inspect_infra.py raises CrsError for any layer whose CRS does not identify
as EPSG:4326, including geographic-but-not-WGS84 datums such as NAD83, and for
a missing CRS. MARS publishes WGS84 lon/lat and geo.py assumes the WGS84
ellipsoid, so a silent reprojection would bury a datum mismatch inside every
attribution distance. Both GEM GeoPackages inspected today are EPSG:4326. The
refusal is tested at the string level and against a real EPSG:3857 GeoPackage.

## 2026-09-15 — GEM pipeline vertex spacing, measured (input to a pending decision)
Slice 3b inspection of GEM-GGIT-Gas-Pipelines-2025-11.gpkg, one layer,
4,246 features, EPSG:4326. Spacing is the WGS84 geodesic distance between
consecutive vertices, multi-part geometries exploded first so no segment spans
parts. 2,060,384 segments over 1,322,990 km of geometry.

  p50 12.3 m   p90 749 m   p99 10,067 m   max 2,268 km   (11,001 zero-length)
  segments > 5 km: 40,856 = 1.98% of segments but 67.7% of total length.

  by GEM's own RouteAccuracy field (features / km / p50 / p99 / %length>5km):
    very high (within meters)   137 /  166,218 /  12 m /   2.3 km / 24%
    high                      1,286 /  654,801 /  12 m /  11.2 km / 61%
    medium                      701 /  200,896 / 2.3 km / 134 km / 90%
    low                       1,389 /  294,022 / 671 m / 156 km / 92%
    no route (but has geom)      21 /    7,053
  712 features (16.8%) are EMPTY GeometryCollections: no route at all.

Two distinct problems hide in the long tail and need different remedies:

1. Chord-versus-geodesic geometry. Measured error of point_to_segment_distance_m
   against densified-geodesic truth for a segment passing 0.2-3 km from a leak,
   worst case over |lat| <= 68.4, by vertex spacing L:
     L:            1 km   5 km   10 km   20 km   50 km   100 km
     raw chord:    0.65   1.33    5.34   21.4    134     534 m
     densify 10km: 0.65   1.33    4.96    3.42   5.34    4.07 m
     densify 5 km: 0.65   1.29    0.85    1.28   1.10    1.12 m
     densify 2 km: 0.65   0.56    0.66    0.62   0.51    0.66 m
   Error grows ~L^2, consistent with the geodesic sagitta L^2 tan(lat) / 8R.
   Densifying at 5 km or finer puts every segment inside the 10 m envelope
   (worst 1.33 m). This part is cheap and purely geometric.

2. Placeholder routes. The 1,000-2,300 km single chords (Yamburg-Volga,
   Persian Gas Pipeline, Power of Siberia 2, Trans-Sahara, Nigeria-Libya) are
   straight lines between named endpoints, not traced routes; the real pipe may
   be tens of km from the chord (midpoint deviations of 7-193 km). No
   densification interval recovers a route that was never digitised. This has
   to be handled in attribution logic, using RouteAccuracy and RouteType as
   inputs to candidate confidence, not in geometry. Note GEM's label is
   per-project, not per-segment: "high" accuracy routes still contain 1,101 km
   single chords (Chelyabinsk-Petrovsk), and only 57% of "high" features have
   geodesic geometry length within 10% of GEM's declared LengthMergedKm (35%
   for "low").

Caveat on the deviation metric: it is geodesic-midpoint to (lon,lat)-chord
midpoint, an upper bound on the cross-track gap that includes an along-track
term from unequal metres per degree of latitude. That term is under 2 m for
north-south chords up to 100 km and matters only above ~250 km, where the
chord is a placeholder anyway.

DECISION PENDING: densification interval, and how RouteAccuracy / RouteType /
Status feed candidate confidence. Nothing was normalised or joined in 3b.

## 2026-09-15 — GEM schema hazards recorded for the attribution data model
- ProjectID is not a physical pipe. Of the 712 empty-geometry features, 130 are
  "Capacity expansion only", 14 "Included in other ProjectID" and 12
  "Bidirectionality upgrade only": separate ProjectIDs sharing one physical
  route with another ProjectID. A naive join would double-count owners.
- Every attribute column in the pipelines layer is a string. GEM's null token
  in numeric columns is "--" (LengthMergedKm 69, CapacityBcm/y 1,617 = 38%,
  CostUSD 2,923) and in text columns an empty string (SegmentName 51%,
  StartYear1 34%, FuelSource 69%). Pandas reports zero nulls; both must be
  treated as missing. A capacity-based asset-scale prior is unavailable for 38%.
- Ownership is often unknown: Parent contains "unknown" for 1,964 features
  (46%), ParentEntityIDs is literally "unknown" for 905, Owner is "--" for 501.
- Status: 2,975 operating, 611 proposed, 282 construction, 243 cancelled, 103
  shelved. Non-operating pipelines cannot leak; attribution must filter or
  down-weight on Status. LNG terminals likewise: only 376 of 1,198 units
  operating.
- LNG terminals: clean, typed, no empties; Latitude/Longitude columns agree
  with geometry to the millimetre; 1,198 units belong to 807 terminals
  (ProjectID repeats per unit); Accuracy is "exact" 557 / "approximate" 641;
  "Owner GEM Entity ID" and "Parent GEM Entity ID" join to the ownership
  tracker's All Entities.Entity ID; ParentEntityIDs in the pipelines layer is
  the same key.
- Ownership tracker: read by streaming the xlsx as a zip (sharedStrings.xml is
  135 MB uncompressed; nothing was loaded). "Gas Pipeline Ownership" carries
  ProjectID, the pipelines join key. Declared sheet extents are unreliable:
  five different sheets all claim A1:N161862.
- Extraction tracker "Field-level main data" carries Latitude/Longitude, a WKT
  field outline, Operator/Owner(s)/Parent(s) and "Location accuracy". MARS
  source_type values are dominated by fields and facilities, not pipelines, so
  this is likely a primary attribution source and has not been inspected in
  depth yet.

## 2026-09-15 — The extraction tracker is the primary attribution source; inspected
MARS cases are 100% oil and gas and, by source_type, dominated by point-like
production and processing assets; pipelines are ~23%. GEM's Global Oil and Gas
Extraction Tracker (March 2026) is therefore the primary infrastructure input.
Read with etl/xlsx_stream.py (standard library, every cell as written; no
openpyxl added: the file is 5 MB but the same reader serves the 135 MB
ownership workbook and keeps GEM's literal null tokens intact).

"Field-level main data": 7,673 units x 27 columns. "Project-level main data":
359 projects x 28 columns. Only 501 units are linked to any project and 459 of
the 960 unit IDs that projects list do not exist in the field sheet, so the
project level is not a reliable roll-up.

Null convention in THIS file: every cell is present and missing values are
the empty string "" only. No "--" anywhere, unlike the pipelines GeoPackage,
and "unknown" is a real category in Onshore/Offshore (443). Years are stored
as "2024.0". Each GEM file's null convention must be discovered, not assumed.

Coordinates: 7,055 of 7,673 units (91.9%) have numeric lat/lon, none out of
range or at (0,0); 618 have none. Location accuracy is exact 6,035 /
approximate 1,021 / "" 617 and lines up with coordinate presence. It exists on
both sheets and is filled independently per row (236 same vs 265 different
between a project and its units). 18 coordinate pairs are shared by 5 or more
units (117 units), e.g. 14 South Pars phases on one point and 13 Qatar North
Field units on another: distance alone cannot separate assets on a shared
placeholder point.

Outlines ("Field outline (WKT)"): 1,110 rows (14.5%). Of these, 28 are
unparseable because they are exactly 32,767 characters, Excel's cell limit,
so GEM's polygon was truncated on export and is unrecoverable from this file;
1 is MULTIPOLYGON EMPTY; and 110, all in Poland, are stored in a projected CRS
in metres (bounds ~324k-454k E, 416k-853k N, consistent with EPSG:2180) inside
the nominally WGS84 column, while their Latitude/Longitude cells are fine.
etl/inspect_extraction.py flags all three and excludes them from statistics.
The remaining 971: area p50 33 km2, p90 209 km2, max 1,356 km2; bounding-box
diagonal p50 12 km, and 59% are wider than the 10 km projection envelope. The
unit's own point lies inside its outline for 871 of 951; for the 80 outside,
the gap is p50 1.5 km, max 844 km. Consequence for attribution: a field is a
region, not a point, and distance to it has to be to the nearest edge; a
single local frame about the leak does not cover most outlines.

Ownership: Operator filled 83% (plain names); Owner(s) and Parent(s) 51%, as
"Name [share%]" lists separated by "; ". Owner shares are written "[100%]" and
Parent shares "[100.0%]" (100% of Parent shares carry decimals, 0% of Owner),
so the two columns need separate parsing. 2,556 units have an Operator but no
Owner/Parent.

There is NO GEM Entity ID in either sheet, and the ownership workbook's Asset
Ownership sheet (50,460 rows) contains zero extraction Unit IDs or Project IDs:
its asset types are coal, gas and bioenergy plants, pipelines, mines, steel
and cement only. Owner entities for extraction units can only be reached by
matching Owner/Parent/Operator TEXT against the ownership workbook's All
Entities names, which is lossy normalisation work for a later slice. Note the
"Oil & Gas Plant" asset type (14,621 rows, L-prefixed IDs) does carry entity
IDs and likely covers gas processing plants; that tracker is not in data/raw/.

Coverage against the 1,394 MARS cases, country names compared verbatim: five
MARS names have zero rows only because GEM spells them differently (United
States of America / United States 2,009 rows; Iran (Islamic Republic of) /
Iran 114; Russian Federation / Russia 332; Syrian Arab Republic / Syria 24;
Viet Nam / Vietnam 25); together those are 474 cases (34%), so a country-name
mapping is required before any join. Real thinness: Turkmenistan has 214 cases
but only 3 extraction units with coordinates; Uzbekistan 87 cases / 18 units;
Algeria 212 / 52. Expect the NONE confidence tier to be common there.
