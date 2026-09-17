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

## 2026-09-15 — Coverage go/no-go: OGIM and the plant tracker do not close the gap
etl/coverage_check.py, counts only, no join. MARS: 1,394 unanswered cases in
31 countries. Asset counts per case country from every snapshot in data/raw/,
with the five documented MARS-to-GEM country aliases applied and OGIM's
uppercase UN spellings matched case-insensitively.

Sources measured:
- OGIM v2.7 (EDF, 2.9 GB, 17 layers, all EPSG:4326): 4,537,369 wells,
  1,858,109 pipeline features, 132,220 tank batteries, 98,047 equipment
  points, 17,742 fields; counted with SQLite-side GROUP BY via pyogrio, never
  loaded. README: "six continents and 152 countries" from 188 integrated public
  datasets. But the Data_Catalog layer shows only 26 distinct source countries
  plus 7 'VARIOUS' global datasets; wells exist for 27 countries only, 78% of
  them in the United States. Granular facility coverage is North America,
  Argentina, Brazil, Australia and parts of Europe; elsewhere OGIM records come
  from the global polygon and VIIRS-flare datasets. The README also warns that
  absence of tank batteries "most likely implies a gap in reporting rather than
  the absence of infrastructure".
- GEM Global Oil and Gas Plant Tracker (August 2026): gas- and oil-FIRED POWER
  PLANTS (capacity in MW), 14,099 units at 6,300 plants, 100% coordinates, GEM
  Entity IDs 99% filled. Not upstream infrastructure; a third null convention
  (absent cells, no tokens).

Top of the table (cases / GEM extraction with xy / OGIM facility points /
OGIM wells / OGIM dedicated sources):
  United States 245 / 1,887 / 17,068 / 3,550,528 / 45
  Turkmenistan  214 /     3 /     42 /         0 /  0
  Algeria       212 /    52 /     30 /         0 /  0
  Iran          126 /   109 /    110 /         0 / 10
  Venezuela      96 /   104 /    104 /    20,697 /  3
  Uzbekistan     87 /    18 /      3 /         0 /  0
  Kazakhstan     57 /    48 /     25 /         0 /  0
  Syria          43 /    24 /      2 /         0 /  0

Bottom line, over all 1,394 cases, using upstream point assets (GEM
extraction units with coordinates + OGIM facility points + OGIM wells):
  >= 10 assets in country:  98.4% of cases   (a meaningless bar)
  >= 100 assets in country: 49.1% covered, 50.9% not
  fewer assets than cases:  579 cases = 41.5% (Turkmenistan, Algeria,
                            Uzbekistan, Syria, Bahrain, Jordan)
GEM extraction alone at >= 100: 44.3%. OGIM moves that to 49.1%, almost
entirely via Venezuela and Libya. For the countries that dominate the MARS
backlog (Central Asia, North Africa, Middle East) no source in data/raw/ has
national facility data; these gaps are out of scope by construction of the
public sources, not confirmed absence of infrastructure.

Partial mitigation to note for later: OGIM's VIIRS flare detections exist
where nothing else does (Turkmenistan 52, Algeria 219, Uzbekistan 78) and 218
cases are flare-type sources; a flare detection locates an asset but names no
operator.

DECISION PENDING (scope): whether to attribute only where coverage exists and
report NONE honestly elsewhere, or to add a further source for the sparse
countries. Nothing was normalised or joined.

## 2026-09-15 — Asset table (slice 4A): what is in data/processed/assets.parquet and why
etl/build_assets.py, 4,756,915 assets, WKB geometry, gitignored (296 MB,
rebuilt from committed snapshots in ~2 minutes).
- Pipelines are densified along the WGS84 geodesic to at most 2 km between
  vertices (PIPELINE_DENSIFY_MAX_SPACING_M). Measured basis: at 2 km the worst
  chord-versus-geodesic error is 0.51-0.66 m against the 10 m tolerance.
  2,571 routed pipelines with a usable status, 2.16 M vertices.
- Usable-status filters are GEM's own vocabularies (config): pipelines
  operating/idle/mothballed/mixed; LNG operating/idled/mothballed; extraction
  operating/mothballed/in-development/UGS/unrecorded. Proposed, cancelled,
  shelved, retired, abandoned assets cannot hold gas today. OGIM keeps
  everything except OGIM_STATUS = PROPOSED; abandoned wells stay because they
  are a documented methane source class. 963 routed pipelines, 795 LNG units
  and 504 extraction units were dropped on status.
- The 28 truncated, 110 projected-CRS and 1 empty outlines are excluded by
  the same classification as the 3c inspection; 917 outlines remain, each
  carrying physical_id = its unit's point asset so the pair collapses.
- Pipeline dedupe: the three routed "capacity expansion / included in other
  ProjectID / bidirectionality" features overlap their named parent by only
  0.2-3.6% of their length (measured with a ~100 m buffer), so they are
  distinct routes and are KEPT with route_type carried. The real duplicates
  are 201 routed features in 88 groups with byte-identical geometry (parallel
  lines I/II, phases); those collapse through placeholder_group.
- placeholder_group = identical geometry across ALL sources (sha1 of WKB):
  411,658 assets in 166,726 groups, largest 273. OGIM equipment records share
  their facility's coordinate, which is exactly the collapse wanted.
- route_fidelity (pipelines) = geodesic length / declared LengthMergedKm:
  p10 0.64, p50 0.98, p90 1.37.
- A named party means operator OR parent OR owner. GEM pipelines have no
  operator column, only Owner/Parent, so "operator or parent" alone would
  have hidden 501 owners. 85.4% of assets name someone.
- No GEM Entity IDs are carried: the extraction tracker has none and the
  ownership workbook does not cover extraction assets (slice 3c).

## 2026-09-15 — Search radius derivation, and the one number that is an assumption
radius = SEARCH_RADIUS_PIXELS x min resolution over the source's detecting
satellites, capped at the 10 km validated projection envelope (cap reported).
Resolutions are per instrument, keyed by MARS's exact satellite strings.
NONE could be sourced from UNEP: the data dictionary and methodology page
list instruments without resolutions, the bundled PDF yields no text, and the
IEA/IMEO guidance's "pixel size of approximately [N] metres" has undecodable
digits. Each value is therefore the operating agency's published GSD for the
bands used (ESA 20 m Sentinel-2 SWIR; USGS 30 m Landsat SWIR; ASI 30 m
PRISMA; DLR 30 m EnMAP; NASA JPL 60 m EMIT; ESA 7 km TROPOMI; ESA 500 m
Sentinel-3 SLSTR; NOAA 750 m VIIRS M-bands; Planet 30 m Tanager). GOES is
deliberately unsourced and raises; it appears on no unanswered case.

SEARCH_RADIUS_PIXELS = 3 is an ASSUMPTION, flagged for ratification: source
pixel plus one pixel of geolocation error plus one of analyst placement. It
is the only unsourced number in the attribution path, and the batch summary
prints a sensitivity line at 2x and 5x so its effect is visible.

## 2026-09-15 — Wind back-projection is built, tested, and applied with a measured offset of 0 m
The MARS data dictionary defines a plume row's lat/lon as "coordinate of the
source location", and all 22,900 plume rows with wind carry coordinates
byte-identical to their source's. The published point is UNEP's origin
estimate, not a plume centroid, so back-projecting it upwind would move the
search away from that estimate. MARS_UPWIND_OFFSET_M is therefore 0.0, with
the measurement as its citation. The mechanism (search_centre) is kept and
tested against a known wind vector for any dataset that does publish
centroids, and every result records which path was taken. This means the
"wind-corrected versus symmetric" split for MARS is 0% / 100%, by measurement.

## 2026-09-15 — Distances: the leak is always the origin; clipping is a guard, not a correction
distance_to_geometry_m projects about the leak only. A test pins the
convention by showing the flipped argument order gives a different number
(up to 16.5 m over 10 km at |lat| 70). Lines and polygon BOUNDARIES are
clipped to the search window before any vertex is projected; containment is
tested on the unclipped polygon. Measured and recorded so nobody over-claims
it: because the projection is affine in (lon, lat), a straight lon/lat edge
stays straight in the plane, so the distance to a long chord is identical
(to the millimetre) with or without clipping. Clipping guarantees that no
vertex outside the envelope is ever projected, and avoids projecting
thousands of vertices of a 60 km outline per candidate; it does not change
the answer for straight edges. Ordering inside the radius is
(kind compatibility, route traced, distance, scale): inside the localisation
radius every position is equally plausible, so an ordinal prior needs no
numeric weight and none was invented.

## 2026-09-15 — First attribution run over the 1,394 unanswered cases (slice 4B)
backend/app/attribution.py over 4,756,915 assets; loads and runs in ~10 s.
  OPERATOR_NAMED   217  (15.6%)
  ASSET_ONLY       103  ( 7.4%)   mostly VIIRS flare detections: 52 of the
                                  218 flare-type cases resolve to a flare
                                  that names nobody
  UNMAPPED       1,074  (77.0%)
Search: radius min 60 m, p50 90 m, max 10 km (one TROPOMI-only case capped);
100% symmetric about UNEP's published source location (offset 0, measured);
14 cases have no wind on any plume. Mapped cases: median candidate set 1,
p90 4, max 111 (a gas-storage field of one operator's wells). Ambiguity:
189 none, 124 competing, 7 placeholder groups.

Per country the result follows the coverage check exactly: United States
105 named / 13 asset-only / 127 unmapped; Turkmenistan 5 / 1 / 208; Algeria
3 / 13 / 196; Uzbekistan 2 / 4 / 81. Per source type, "Gas disposal
facility" is 254/266 unmapped and "Transmission Pipelines" 163/200, while
"Pipeline valve" is 32/48 named.

Radius sensitivity: cases with any candidate at 1x / 2x / 5x the derived
radius: 23.0% / 32.9% / 45.1%. So the three-pixel radius is binding for
roughly a fifth of cases, and coverage is binding for the rest: even at 5x
(median 450 m) 55% of cases find nothing. Two things this points at, both
left for the next decision rather than tuned here:
1. Asset REPRESENTATION error is not in the radius. A GEM field point stands
   for an outline whose median diagonal is 12 km; a "medium"-accuracy GEM
   route can sit hundreds of metres from the pipe. The radius currently
   models only the plume's localisation, so a correctly located leak at a
   field's edge is UNMAPPED at 90 m.
2. In the sparse countries no radius helps; those cases are UNMAPPED because
   no public source has the assets (slice 3d).
The compatibility prior never decides a tier; it only orders candidates
inside the radius. GOES remains unsourced; no case needed it.

## 2026-09-15 — Uncertainty budget (slice 4D): per-pair thresholds, and which sigmas are measured
The per-leak radius modelled only plume localisation. It is replaced by a
per-PAIR match threshold sqrt(plume_sigma^2 + asset_sigma^2), capped at the
10 km projection envelope. plume_sigma is unchanged (SEARCH_RADIUS_PIXELS x
resolution). asset_sigma is per asset, stored in assets.parquet as sigma_m
with a sigma_basis string, and the STRtree window is sized by the largest
threshold any asset could have so imprecise assets further away are still
examined. Ordering inside the thresholds stays by raw distance: an imprecise
asset must not outrank a precise one merely for being imprecise.

MEASURED from our own data
- extraction unit WITH an outline: sigma 0 for the polygon (distance to the
  nearest edge already carries the extent) and 0 for the unit's point (the
  polygon represents it; the pair collapses on physical_id).
- extraction unit WITHOUT an outline: the equivalent-circle radius
  sqrt(area/pi) of the 917 usable outlines, p50, split by GEM Location
  accuracy: exact 3,317 m (n=879), approximate 4,844 m (n=20, weak). All
  percentiles (p25 1,901 / p50 3,295 / p75 5,190 / p90 8,167 m) are written
  by the build to data/processed/assets_sigma.json so the sensitivity run
  never copies a number by hand. Why p50: sigma is a 1-sigma scale, not a
  bound; a uniform disc of radius r has RMS point-to-centre distance 0.71 r,
  so the median radius is already conservative for the median field. Caveat:
  outlined units skew offshore (49% vs 21% of point-only units), so the
  measured sigma may overstate typical point-only extent.

SOURCED
- VIIRS flare detections: 750 m, the M-band pixel (NOAA VIIRS specification,
  the same citation as SATELLITE_RESOLUTION_M).

ASSUMED (every one scaled x0.5 and x2 in the sensitivity table)
- GEM pipelines by RouteAccuracy: very high (within meters) 10 m; high 100 m;
  medium 500 m; low 2,000 m; "no route" with geometry 2,000 m. GEM defines
  only the first class in metres; the rest are a decade scale. Supporting
  evidence, route_fidelity within +/-10% by class: very high 70%, high 59%,
  medium 43%, low 35% (p10-p90 fidelity: very high 0.85-1.20, high
  0.75-1.32, medium 0.67-1.46, low 0.57-1.40). That orders the classes as
  assumed but does not fix their metres, and placeholder chords tens of km
  off are beyond any sigma.
- GEM LNG terminals by Accuracy: exact 500 m (a site is several hundred
  metres across and a leak can be anywhere on it), approximate 2,000 m.
- OGIM, one value for the whole source: 100 m. The README states no
  positional precision; records are mostly government registry coordinates.

## 2026-09-15 — Uncertainty budget results (slice 4D); data layer frozen
Scenario table over the 1,394 cases (named / asset-only / unmapped; candidate
set p50 / p90 over mapped cases; mapped cases with 2+ distinct named parties):
  4B plume sigma only              217 / 103 / 1,074   set 1 / 4    parties2+  45
  4D default (plume + asset sigma) 356 / 323 /   715   set 1 / 4    parties2+ 103
  assumed sigmas x0.5              329 / 321 /   744   set 1 / 4               83
  assumed sigmas x2                409 / 316 /   669   set 1 / 5              152
  extraction sigma = outline p25   294 / 334 /   766   set 1 / 4               81
  extraction sigma = outline p75   406 / 320 /   668   set 1 / 4              139
  extraction sigma = outline p90   464 / 308 /   622   set 2 / 5              197
  plume sigma x2                   397 / 319 /   678   set 2 / 5              153
  plume sigma x5                   438 / 328 /   628   set 2 / 16             235

Default: OPERATOR_NAMED 25.5%, ASSET_ONLY 23.2%, UNMAPPED 51.3%. Mapped
cases 320 -> 679. Median candidate set stays 1 and p90 stays 4; median
distinct parties stays 1; of 260 competing cases, 130 name exactly one party
and 101 name two or more. So the thresholds did not dissolve the tiers.
Across every assumed-sigma scenario the named share moves within
23.6-29.3%; only the extraction p90 choice (33.3%) or a 5x plume sigma
(31.4%, with p90 sets of 16) pushes further, and both loosen ambiguity.
The honest figure is a quarter named, a quarter asset-only, half unmapped.

Where the gain came from: 262 mapped cases now top on a VIIRS flare
detection (sourced 750 m sigma), which is why ASSET_ONLY tripled; "Flare"
sources went from 149 to 70 unmapped. Per country: United States 154 / 41 /
50; Iran 35 / 45 / 46; Venezuela 21 / 55 / 20; Turkmenistan 8 / 16 / 190;
Algeria 7 / 55 / 150; Uzbekistan 7 / 16 / 64. "Transmission Pipelines"
improved only from 163 to 139 unmapped: the remaining pipeline cases sit
where GEM has no traced route, not where the route is imprecise, so no
per-class sigma reaches them. Runtime 44 s for nine scenarios, 5.8 GB peak.

The data layer is frozen at this state. Two open items are recorded, not
acted on: SEARCH_RADIUS_PIXELS and the pipeline/LNG/OGIM sigmas remain
assumptions with sensitivity shown; and the extraction sigma may overstate
point-only units because outlined units skew offshore.

## 2026-09-15 — Costing (slice 5): observed durations, sourced constants, and one caught 1000x error
backend/app/costing.py, pure functions, kg throughout, kg_to_tonnes the only
exit. annualised_emission_kg(fluxrate_kg_per_h, duration_h) has NO default
duration; the name is kept from the spec but the docstring says it is flux x
duration with no per-year scaling.

Duration is observed, per source, as first-to-last plume tile_date. Over the
1,394 cases: 833 have a span (2+ detections, span > 0), 561 are single
detections and get None (never zero), 65 have no reported flux on any plume.
Span days p10 0.1, p25 22, p50 309, p75 762, p90 1,084, max 1,914; 126 spans
are under one day. The costed subset is therefore 784 of 1,394 (355 of the
679 mapped). Assumption stated on every printout: continuous emission at the
mean detected rate between first and last detection. That is an UPPER-BOUND
model: it overstates intermittent sources (MARS's own persistency field, the
share of clear overpasses with a detection, is null for 1,167 sources and
was not applied; it is the obvious refinement) and understates sources still
emitting after their last detection.

Constants, all in config with sources:
- GWP-100 fossil methane 29.8 (+/- 11), IPCC AR6 WG1 Ch.7 Table 7.15,
  machine-verified against the chapter PDF text; the non-fossil row is 27.0.
- Methane HHV 890.8 kJ/mol (NIST WebBook) -> 55.53 MJ/kg; 1 Btu(IT) =
  1055.05585262 J (NIST SP 811); density 0.6785 kg/m3 derived from IUPAC
  molar mass and the exact SI gas constant at ISO 13443 conditions (15 C,
  101.325 kPa), ideal gas, 0.2% real-gas effect ignored, display only.
- Price: Henry Hub spot, monthly average, August 2026, 2.78 USD/MMBtu (EIA
  series RNGWHHD, retrieved 2026-09-15). One benchmark, one month; 2026
  monthly averages so far span 2.77-7.72, and every printout says so.
- Unit guard: MARS's dictionary states typical detectable emissions of
  500-10,000 kg/h; a flux column whose median is outside that band is
  refused as a unit error (tonnes/h or g/h). Tested with the real column
  scaled by 1/1000 and 1000.

Results (costed subsets only; nothing mixes costed and uncosted sources):
  all cases, 784 costed:     CH4 29.3 Mt   CO2e 874 Mt   gas 43.2 bcm   USD 4.29 bn
  mapped, 355 costed:        CH4 15.5 Mt   CO2e 461 Mt   gas 22.8 bcm   USD 2.26 bn
The first draft of the HHV derivation divided by 1000 twice and printed the
value as 4.3 million USD; test_energy_content_hand_checked caught it before
anything was reported. Recorded because it is the exact failure the project's
unit rules exist for, and it happened.

## 2026-09-15 — Duration cap (slice 5B): per-detection attributable window
The uncapped first-to-last model credited a source seen twice five years
apart with five years of emission; 29.3 Mt CH4 was not defensible. Each
detection is now credited with one attributable window centred on it,
overlapping windows merged, no double counting. Window = 11.1 days, MEASURED
as the median gap between consecutive detections of the same source over all
18,855 gaps in the snapshot (p25 3.0, p75 67.9 days); the choice of the
median is the one modelling decision. The uncapped span is retained and
labelled UPPER BOUND. A single detection is credited one window, so capped
totals exist for every case with a flux; the ratio is reported on the same
784 (all) / 355 (mapped) cases so it compares like with like.

  all cases, 784 with 2+ detections:  capped 5.92 Mt CH4 / 176 Mt CO2e /
    USD 866 M  vs  uncapped 29.3 Mt / 874 Mt / USD 4,291 M   ratio 0.202
    capped over all 1,329 flux-bearing cases: 6.56 Mt / 196 Mt / USD 960 M
  mapped, 355 with 2+ detections:     capped 3.30 Mt / 98 Mt / USD 483 M  vs
    uncapped 15.5 Mt / 461 Mt / USD 2,264 M   ratio 0.214
    capped over all 632 flux-bearing mapped cases: 3.63 Mt / 108 Mt / USD 531 M
  Median attributed duration falls from 335 to 33 days.

Per-case daily figures, which depend on no duration assumption and are what
a UI should lead with: kg CH4 per day p50 52,416 (p90 220,603), USD per day
p50 7,669 (p90 32,276), days since last detection to the snapshot date
p10 44 / p50 236 / p90 533 (includes the 30-75 day publication lag).
MARS persistency was not applied, by instruction.

## 2026-09-17 — Web export (slice 6A): one JSON blob, built offline, gated on the hand-checked count
etl/export_web.py runs the default attribution scenario and the costing once
and writes web/data.json (392 KB) in exactly the shape the committed frontend
reads: C (one entry per Natural Earth country, zero-case countries included
so the map draws) and L (leaks keyed by the country's Natural Earth name).
The field set was taken by grepping the frontend for every property it
reads, not from the demo data: C n c r bb cs usd ch4 nm as um fmax; L id ty
d nd f ch u lat lon sat t op cand. The demo's iso, co2 and co were never
read and are not emitted. `fmax` (max kg/h in the country) is required for
marker sizing and was missing from the written spec.

The correctness gate is MARS_UNANSWERED_CASE_COUNT = 1,394 in config, cited
to the 2026-09-15 count, checked twice (frame rows and sum of country
counts) with an explicit raise rather than `assert`, so -O cannot strip it.

Units: the frame is kg and kg/h throughout, built from `ch4_fluxrate` only;
`total_emission` (tonnes) is never read. `--inspect` traces every derived
column back to the kg/h rate (median 2,184 kg/h inside the MARS 500-10,000
band; kg_per_day == flux x 24; usd_per_day recomputed) and raises on any
break. The single tonne conversion is `ch` (t CH4/day) via kg_to_tonnes at
the presentation boundary, hand-checked: 2,480 kg/h -> 59,520 kg/day ->
59.5 t/day -> USD 8,708/day.

Mapping choices:
- d = days since LAST detection to the snapshot date (the frontend anchors
  its timeline at snapshot minus d), not days since first detection.
- ty = MARS source_type verbatim (25 values), no re-labelling.
- sat = instrument only ("Sentinel-2 - ESA" -> "Sentinel-2"); agencies are
  credited in the footer.
- op = the top candidate's named party only when the tier is
  OPERATOR_NAMED; GEM share brackets ("[100.00%]", "[unknown %]") are
  stripped for display but every co-owner is kept, "; "-joined (28 of 356
  named cases), so no party is hidden. cand = n_candidates.
- The 65 cases with no reported flux keep f, ch, u = null. Zero would be an
  invented number. Country usd/ch4 sums skip them. Known consequence, left
  for the frontend slice: the page calls l.f.toLocaleString() in the
  country leak list, so 15 country views (Russia 25, US 7, Kazakhstan 6...)
  will throw until a null guard is agreed.
- Country outlines: no Natural Earth file is on disk; the 175 thinned
  ne_110m rings embedded in the committed demo were extracted verbatim to
  data/raw/country_rings.json with a provenance block. Four MARS names
  resolve through the existing MARS_TO_GEM_COUNTRY_ALIASES (Iran, Russia,
  Syria, Vietnam); any other miss raises. Bahrain has no polygon at 1:110m
  and is listed in COUNTRIES_WITHOUT_NE110M_POLYGON: empty rings, bbox from
  its own leaks, reachable from the list.

Result: 1,394 leaks in 31 countries; named 356 / asset 323 / unmapped 715,
matching the frozen 4D run. Daily totals over the 1,329 cases with a flux:
235,370 t CH4/day, USD 34.4 M/day (consistent with the slice-5B per-case
figures; mean is far above the median because of a heavy tail, e.g.
USA_S_1066 at 146,438 kg/h).

.gitignore had its last two patterns joined on one line, so neither the
parquet nor OGIM was ignored; fixed. web/data.json is committed: it is the
runtime's only data input.

## 2026-09-17 — Correction: a name is shown only when the candidate set names ONE party
The first export put the top candidate's party on every OPERATOR_NAMED case.
For 94 of the 356, the candidate set names two or more distinct parties on
different assets (e.g. USA_S_1063: a pipeline owned by four companies at
31 m, a Texaco well at 55 m). Picking the nearest there asserts what the
geometry cannot support. Rule now: the frontend label `named` (and `op`)
requires tier OPERATOR_NAMED AND n_parties < 2; otherwise the case is shown
as `asset` with op null, so the pill under-claims rather than over-claims.
Co-owners of a SINGLE asset are one party string and stay joined with GEM's
"; " (16 cases): they are all genuinely owners. The attribution tier in the
backend and in cases.json is unchanged; `shown_as` records the label.
Shown split becomes named 262 / asset 417 / unmapped 715.
The Operators tab aggregates by individual party (split on "; "), each
owner credited with the whole leak, stated in the caveats; before, a joint
venture would have appeared as a phantom operator with one leak.

## 2026-09-17 — Frontend null guards for the 65 uncosted cases
"Change nothing else" never meant crash. Every call site of f/ch/u
(marker radius, timeline, stat tiles, narrative, record table, country
list, operators footer, tooltip) is guarded; the 65 cases render "flux not
reported" and a dash. Counts stay on 1,394; methane and money on 1,329; the
split is stated in the caveats from the data (NL/NF), not hardcoded. Two
further caveats added: daily figures are rates at detection and must not be
multiplied out (the page never does; verified by grep), and co-owner
listing. Headline moved from the demo's 99,550 t/day to 235,370 t/day; the
mean 7,374 kg/h against a median of 2,184 is the expected super-emitter
skew, and 34.4 M USD/day / 3.511 USD per (kg/h)-day = 9.8 M kg/h checks.

## 2026-09-17 — Runtime service (slice 6B): serves two committed JSON files, computes nothing
backend/app/main.py: fastapi + uvicorn, imports only config (a test scans
for pandas/numpy/shapely/pyarrow/sqlite3 and the geospatial stack). Loads
web/data.json (map blob, 0.39 MB) and web/cases.json (per-case detail,
1.65 MB, fetched only by the drill-down) once at startup and refuses to
start on a missing file, a wrong case count, or an id mismatch between the
two. Endpoints: /healthz, /api/summary, /api/cases?country&tier,
/api/cases/{id}, /api/geojson, /api/data, / (StaticFiles). `capped` and
`upper_bound` are separate objects in /api/summary totals and in every
case; nothing sums across them. Summary totals reproduce slice 5B exactly.
No outbound call of any kind exists in the service.

Observed while smoke-testing, not changed: for 24% of cases with both
models (185 of 784), capped > "upper_bound". The capped model's windows
overhang the first and last detection by half a window each, so for a
densely detected source the merged union exceeds the true span by up to
one window (11.1 days); the excess is bounded, never more. Among the 185
the span is p50 2.1 days, max 97 (USA_S_1063: 5 detections in 3.1 h ->
capped 269.5 h vs span 3.1 h). "Upper bound" is an upper bound on the
continuous-emission assumption, not on the capped model. The UI should label the span model "first-to-last span" rather than
imply it dominates; left for the frontend slice.

## 2026-09-17 — Frontend boots from /api/data (Task 4); the page carries no data of its own
web/index.html lost its inlined demo blob (`const DATA = {...}`, 397 KB on
one line) and now starts with
`const {C, L} = await (await fetch('/api/data')).json();`. The script tag
became `<script type="module">` because a top-level await is legal only in a
module; nothing else in the block changed. Checked before choosing that: the
page has no inline `on*=` handlers and the script never uses `this`, so
module scope and strict mode cannot break anything. The page went from 447
KB to 50 KB and the served blob is the single source of truth, so the
frontend can never disagree with the API. Verified by executing the served
script in jsdom against the running service: no errors, 176 country
outlines, 1,394 alerts, 235.4 kt/day and USD 34.4 M/day on the headline,
Russia drill-down with 59 rows including an uncosted one rendering "flux
not reported". A test pins the fetch line, the module tag, the absence of
the demo blob and a 100 KB size ceiling. Consequence: the page requires the
service; opening index.html from disk shows nothing, by design.

## 2026-09-17 — Visible licence footer (Task 5)
CC BY-NC-SA 4.0 asks for attribution that is reasonably visible; a Sources
drawer two clicks deep is arguable, a footer is not. One line at the base
of the sidebar names UNEP IMEO MARS with the snapshot date and licence, the
30-75 day publication lag (so nobody reads the map as live), and Global
Energy Monitor with CC BY 4.0, each linked to its source. The drawer keeps
the full source list. A test pins the footer's content.

## 2026-09-17 — web/standalone.html: the page that needs no server
Because index.html now boots from /api/data, the demo and the live link
both depend on the service being up. etl/export_web.py therefore also
writes web/standalone.html: index.html with data.json inlined in place of
the single boot line (WEB_BOOT_LINE in config), nothing else touched, so
the UI is byte-identical. `python -m etl.export_web --standalone`
regenerates it from the two committed files in 0.4 s without the database
or the asset table; every full export rewrites it too, so the three files
cannot drift, and a test asserts the committed standalone equals
build_standalone(index.html, data.json) byte for byte. "</" inside the
blob is written "<\/" so no data value can close the script tag; JS reads
the string unchanged. The file is committed (440 KB), so it survives a
failed deploy on any clone. Verified in jsdom: the served page and the
standalone file opened from a file:// URL with fetch forbidden produce
identical sidebar HTML, markers and footer at world, country and leak
level. The video should be recorded against this file.

## 2026-09-17 — The op rule, re-verified against the written data
Raised again after Task 4. The rule from the earlier correction is in force
in etl/export_web.py (web_tier, leak_record, detail_record) and in the
committed data: 262 shown named, none with n_parties >= 2; 94
OPERATOR_NAMED cases, USA_S_1063 among them, are shown as asset with op
null; 16 named cases carry co-owners of a single asset joined with GEM's
"; " (e.g. USA_S_225: Tallgrass Energy LP; Phillips 66, n_parties 1); no
non-named case carries an op. n_parties counts distinct named-party
strings across candidates, so a joint-venture string on one asset is one
party, and the same string plus a different party on another asset is two
and demotes the case. The Operators tab splits on "; " and credits each
owner. The "28 of 356" figure in the slice-6A entry describes the state
before that correction and is superseded by it.
