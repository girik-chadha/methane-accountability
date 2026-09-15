# RESEARCH.md — Background, evidence, and design rationale

Reference document for the Methane Alert Accountability System.
Not loaded automatically. Read this when you need the problem background,
the source evidence, the data-source details, the prior-art analysis, or the
reasoning behind a design or stack decision.

Compiled 15 September 2026 for NextStep Hacks 2026 (HackAlphaX, Devpost),
theme "Earth Forward". Submission deadline 20 September 2026, 22:00 UK time.

---

## 1. Why methane, and why this specific gap

### 1.1 Methane's position in the climate problem

Methane is the second-largest driver of anthropogenic warming after CO2, and a
disproportionate share of emissions comes from a small number of "super-emitters"
detectable by satellite. This asymmetry is the entire basis for satellite-based
monitoring: a small number of interventions can produce outsized reductions.

Because methane is short-lived but potent, stopping a leak today reduces
near-term warming faster than almost any other available action. This is why
the UN treats super-emitters as a high-leverage target.

Scale of the super-emitter population, from Schuit et al. (2023),
*Atmospheric Chemistry and Physics* — "Automated detection and monitoring of
methane super-emitters using satellite data":

- 2,974 plumes detected in 2021 alone
- Mean estimated source rate 44 tonnes CH4 per hour
- 5th-95th percentile range 8-122 t/h
- Originating from 94 persistent emission clusters plus hundreds of transient sources
- Attribution by sector: ~35% urban areas and/or landfills, 24% gas
  infrastructure, 21% oil infrastructure, 20% coal mines

The detection instrument (TROPOMI, aboard Copernicus Sentinel-5P, in orbit since
2017) gives daily global coverage at up to 7 x 5.5 km resolution and produces on
the order of 12 million observations per day — which is why automated detection
was necessary in the first place. Sentinel-3 can detect leaks of at least
10 t/h daily; Sentinel-2 provides higher-resolution pinpointing.

Note for on-camera claims: the frequently-quoted figure that methane is
responsible for roughly a third of warming to date is widely cited but was not
verified against a primary source during this research. Verify before stating
it as fact, or attribute it loosely ("commonly estimated at around a third").

### 1.2 The gap: detection works, response does not

UNEP's International Methane Emissions Observatory (IMEO) launched the Methane
Alert and Response System (MARS) at COP27 in 2022, with the system becoming
operational in 2023. It is the first global system providing **free**
satellite-based notifications of large methane emission events to governments
and companies.

The IEA reports MARS has notified **more than 5,000 actionable events across
34 countries**.

Response rates:

- **2024:** UNEP notified governments of 1,200 major methane emissions and
  received **15 replies — approximately 1%**. UNEP's own 2025 report called this
  "a stark statistic."
- **2025:** the rate rose roughly tenfold to **12%**. UNEP press release
  (Nairobi, 22 October 2025), accompanying the report *An Eye on Methane: From
  measurement to momentum*: "Government and industry responses to UNEP's more
  than 3,500 satellite methane alerts climbed from one to 12 per cent in the past
  year." And: "nearly 90 per cent remain unanswered."
- UNEP's framing of the residual: "88 per cent of methane-emitting sources
  detected via MARS have not received any response — amounting to over 900
  individual sources around the world."

Actual mitigation actions, counted year by year (via Eye on Global Transparency,
23 April 2026, reporting on the MARS annual report):

- 2023: 1 case
- 2024: 11 cases
- 2025: 23 cases
- 2026 (to April): 6 cases

Against 900+ unanswered sources, this is the core of the argument.

### 1.3 When action does happen, the payoff is large

- Two leaks, in Algeria and Nigeria, were plugged after MARS notifications.
  Inger Andersen, UNEP Executive Director, at COP29: the fixes were "relatively
  simple," and the avoided annual emissions were equivalent to **taking one
  million cars off the road**.
- Cumulatively, the sites where mitigation occurred had been releasing
  **1.2 million tonnes of methane — the same climate impact as the annual
  emissions of 24 million petrol-powered cars.**

### 1.4 The bottleneck is administrative, not technical

This is the most important finding for the product design.

Per the IEA's technical guidance document (prepared with IMEO) on responding to
MARS notifications:

- As of early 2026, **24 countries and nine subnational governments** across all
  continents have designated focal points to receive IMEO notifications.
- **In countries that have nominated a focal point, nearly a third of emission
  sources receive a response** — versus 12% globally.
- Several countries, including Argentina, Brazil, Mexico and Yemen, have notably
  high response rates.
- Regional example: across Central Asia, MARS detected and notified 298
  oil-and-gas emission sources in 2025, with a regional response rate of 22% and
  nearly 20 recorded mitigation cases.
- The IEA proposes a **five-step sequential process** for responding to
  notifications: (1) receive, assign and classify the notification; (2) notify
  the operator associated with the source; and onward through verification and
  mitigation. Recommended timelines vary by urgency, which is determined by the
  event's emission rate and whether the source is recurrent.
- The IEA's conclusion is explicit: "further measures may be required to
  transform satellite alerts into actionable responses for governments and
  companies."

**Interpretation.** Simply assigning one named person to read the alerts roughly
triples the response rate. The failure is not awareness and not detection. It is
that an alert lands in an unowned inbox with no case file, no deadline, and no
consequence for deletion. That is a workflow gap, and workflow gaps are
buildable.

### 1.5 One-sentence framing for the pitch

Satellites already pinpoint the world's largest methane leaks and the UN emails
them to governments for free, but 88% of those alerts — over 900 sources — get
no response at all, even though the handful that were acted on eliminated
emissions equivalent to a million cars, and countries that simply assign one
person to read the emails respond three times more often.

---

## 2. Data sources

### 2.1 UNEP IMEO MARS — primary dataset

- **Portal:** methanedata.unep.org (Eye on Methane data platform)
- **Downloads:** MARS "sources and plumes" as CSV and GeoJSON; "response rate by
  country"; "world's top 50 methane emitters"
- **Access:** direct download, **no form, no registration, no API key**
- **Licence:** CC BY-NC-SA 4.0 (non-commercial, ShareAlike). Fine for a
  hackathon demo. UNEP IMEO **must** be attributed in the UI.
- **Publication lag:** 30-75 days. This is not real-time. The product must never
  imply live alerting.
- **Key fields** (from the published data dictionary):
  - `country`
  - `lat`, `lon`
  - `location_basin`
  - `persistency_category` — Undetermined / Absent / Sporadic / Frequent / Persistent
  - `feedback_government` — Yes / No / Not available
  - `feedback_operator` — Yes / No / Not available
  - `asset_type`
  - `ch4_fluxrate` — **kg/h**
  - `ch4_fluxrate_std` — uncertainty on the flux rate
  - per-plume detection dates and satellite

**Critical caveats on this dataset:**

1. `ch4_fluxrate` is an instantaneous snapshot at satellite overpass, **not** a
   cumulative total. Any "tonnes leaked over time" figure is an estimate resting
   on an explicit persistence assumption, which must be stated in the UI.
2. The `feedback_*` fields are only populated Yes/No for oil-and-gas sources with
   plumes after feedback tracking began; otherwise "Not available". Filter
   accordingly, and do not treat "Not available" as "No".
3. There is **no operator or company name field**. Attribution requires an
   external spatial join.
4. A "response" means only that the focal point replied with information. It does
   **not** mean the leak was fixed.
5. UNEP explicitly cautions that its country data should not be read as a
   definitive ranking of total national methane emissions, because some countries
   are harder to observe from space. Absence of alerts is not absence of leaks.

### 2.2 Infrastructure data for attribution

**Global Energy Monitor (GEM)**
- Licence CC BY 4.0 (attribution required, commercial use permitted)
- Formats: Excel, GeoJSON, GeoPackage, shapefile
- **Access risk: form-gated download, not an API.** Pull on day one and commit
  the snapshot to the repo.
- Relevant trackers:
  - Global Gas Infrastructure Tracker — 4,158 pipelines, ~1.5 million km,
    1,207 LNG terminal projects (pipelines release November 2025)
  - Global Oil Infrastructure Tracker — 1,634 oil pipeline projects, ~487,000 km
  - Global Oil and Gas Extraction Tracker — 6,481 active extraction areas across
    95 countries (March 2026 release)
  - Global Oil and Gas Plant Tracker — 16,559 units, 171 countries (August 2026)
  - **Global Energy Ownership Tracker** — 27,000+ entities, 25,000+ ownership
    relationships, 31,000+ assets. This is the one that resolves an asset to a
    parent company.
  - **Global Methane Emitters Tracker** — already attributes remotely-sensed
    plumes to assets. Review this before building, to avoid duplicating it and to
    understand their methodology.

**EDF OGIM (Oil and Gas Infrastructure Mapping database)**
- Hosted on Zenodo, no form gate
- Approximately 2.9 GB GeoPackage
- Finer-grained facility-level coverage than GEM in some regions

**Prior-art reference for the attribution technique:**
`github.com/data-desk-eco/nom-de-plume` — an open pipeline attributing methane
plumes to infrastructure, using a 750 m match radius. Worth reading for approach;
do not copy wholesale.

### 2.3 Constants that must be sourced, not guessed

Every one of these goes in `backend/app/config.py` with its source in a comment:

- GWP100 for methane (IPCC AR6) — used for CO2e conversion
- Natural gas price basis — used for wasted-product valuation
- Persistence assumption for annualising an instantaneous flux rate
- Satellite geolocation uncertainty radius — used to set the attribution search
  radius. Derive from the instrument's stated resolution, do not invent.
- Earth mean radius: 6,371,008.8 m (IUGG) — used in the local projection

---

## 3. Prior art and the novelty claim

### 3.1 What already exists

- **UNEP's own Eye on Methane map** lets users filter plumes by response status
  and publishes per-country response rates. The basic "who responded"
  visualisation therefore exists. This is the closest prior art and must be
  acknowledged.
- **Carbon Mapper** maps plumes with operator attribution, but is
  detection-focused rather than response-focused.
- **GEM's Global Methane Emitters Tracker** attributes remotely-sensed plumes to
  assets.
- **Academic detection pipelines** are mature and saturated — Schuit et al.'s
  CNN-plus-SVM approach, the AI4CH4 project (vision transformers plus SAM on
  TROPOMI), GHGSat's work. Detection is not open territory.
- **Hackathon projects** exist on plume *classification* from satellite imagery
  (e.g. `github.com/mat10599/QB_hackaton`, and Devpost entries including Unit8's
  "Methane Leaks" and "MethaneTrack" which cluster leaks and link them to
  infrastructure).

### 3.2 What does not exist

No tool found that treats **non-response as the subject**: taking the set of
alerts that received no reply, attributing each to a likely operator, quantifying
the cumulative climate and economic cost of the silence, and managing each as a
case with an escalation clock.

The novelty is the accountability and case-management layer, not the detection
and not the mapping. Frame it that way and the claim holds. Claim novelty on
detection and it collapses immediately.

Caveat: past Devpost galleries were not searched exhaustively. Treat "novel" as
"no close prior art found," not as a guarantee.

---

## 4. Solution design

### 4.1 Theory of change

Satellite detects leak
→ UNEP alerts government/operator (this already exists and already fails)
→ **[this system: attribute to likely owner, generate evidence dossier, assign
case, run escalation clock, publish unanswered cases]**
→ operator faces a costed, named, timed, publicly visible demand
→ the leak is fixed, or the refusal is on the record

The system inserts itself precisely at the step where the existing chain breaks.

### 4.2 Users, in order of realism

1. **National focal point or regulator (primary).** 24 countries plus nine
   subnational governments already have someone designated to receive these
   alerts, and as far as can be determined that person has no tooling — just an
   inbox. They need: which of my country's leaks are open, who likely owns each,
   how long has each been silent, what do I send, what has escalated. They
   already hold the mandate and lack the tool, which makes them a realistic user.
2. **Operators (secondary).** The lever is economic, not moral: escaping methane
   is saleable product. A dossier stating "this facility is venting approximately
   X tonnes/hour, worth approximately $Y per day at current gas prices, at these
   coordinates" is a maintenance work order with a payback calculation attached.
   This reframing is the single most persuasive element of the project.
3. **Journalists and NGOs (tertiary).** The public register is the enforcement
   mechanism: it makes silence visible.

### 4.3 Architecture, six layers

**Layer 1 — Ingest.** Pull MARS sources and plumes. Normalise into a canonical
`leak_event` record: coordinates, detection dates, emission rate and its
uncertainty, persistence category, response flags. Store append-only so
"days since detection" and "days of silence" are computable over time.

**Layer 2 — Attribution.** The technically hard layer, and the one that makes the
project real. MARS gives a point on Earth and no owner. Resolve that point to a
facility, then walk the ownership chain to a named parent company.

Naive nearest-neighbour is wrong and will be broken by the first informed
question, because: pipelines are lines not points; several assets often sit
within a few hundred metres of each other; satellite geolocation carries its own
error radius; and the plume centroid is displaced downwind of the actual source.

Therefore:
- spatial indexing (R-tree / Shapely STRtree) over mixed point and line
  geometries, so lookups are fast over tens of thousands of assets
- a **candidate set** per leak, within a radius derived from the sensor's stated
  geolocation uncertainty — never a single nearest-wins answer
- a scoring function combining distance, asset-type prior (compressor stations
  and wellheads leak; a solar farm does not), and asset scale
- explicit confidence tiers: HIGH (one close candidate), MEDIUM (clear leader),
  LOW (several plausible), NONE (nothing in radius)
- a UI that says "3 candidate operators, ambiguous" when it is ambiguous

Rigour about uncertainty here is what separates this from a toy.

**Layer 3 — Costing.** Convert emission rate to: estimated tonnes over the
observed period (with the persistence assumption stated), CO2e via IPCC AR6
GWP100, and lost product value at a stated gas price. Three numbers, each with
its assumption visible alongside it.

**Layer 4 — Case engine.** Each leak becomes a case with a state machine:
`detected → attributed → dossier_generated → notified → awaiting_response →
escalated → resolved / stale`. Days-in-state drives escalation tiers. This is
what makes it a tool rather than a chart, and it demos well because a case can be
shown moving between states.

**Layer 5 — Dossier generation.** A one-page artefact per case: map, coordinates,
detection timeline, emission rate with uncertainty, likely operator with
confidence tier and the alternative candidates, estimated wasted product value,
applicable regulatory hook, and the country's focal point. This is the thing a
regulator would actually send.

**Layer 6 — Public register.** Open leaderboard of unanswered cases by operator
and by country, ranked by unaddressed tonnage and days of silence.

### 4.4 The safety constraint, and why it is a feature

**No automated outbound communication, ever.**

Attribution here is probabilistic, built from open data with real error bars.
Algorithmically accusing a named real company of an environmental violation and
emailing it to them, or publishing it as established fact, is how a student
project becomes a legal problem for the student. It is also simply wrong when the
match is one of the ambiguous ones.

Therefore: generate the dossier, display the intended recipient, and require a
human decision to send — where "send" is a no-op or a sandboxed demo mailbox in
this build. Put the confidence tier on the face of every dossier. State this
explicitly in the demo video: "we generate the evidence pack; a human regulator
decides whether to send it." It reads as maturity rather than as a missing
feature, and judges notice.

---

## 5. Mapping to the judging criteria

The rubric has six criteria and only one is Technology. A project that trains
nothing from scratch but nails a real, quantified problem end to end beats a
clever ML demo that half works.

- **Originality** — the non-response accountability angle; no close prior art
- **Adherence to Track** — methane waste is the entire product, not decoration
- **Completion** — one frictionless CSV underpins the core; deliverable solo
- **Learning** — satellite data, geospatial matching, GWP climate maths,
  uncertainty handling
- **Design** — map plus case detail plus leaderboard is demo-friendly; budget
  real time here, it is a full sixth of the score
- **Technology** — spatial indexing, a scoring function with confidence tiers,
  optional predictive and optimisation layers

### 5.1 Optional layers if time allows, to deepen the technical core

1. **Response prediction.** Fit logistic regression or survival analysis using
   leak size, country, persistence category and historical response rate to
   predict time-to-response. Survival analysis is the correct tool because the
   data is censored: leaks that have not been responded to yet have unknown true
   time-to-fix. Defensible line by line.
2. **Inspection allocation under constraint.** If inspectors can only check N
   sites this month, which N minimise total unaddressed emissions over the next
   year? A weighted knapsack/scheduling variant, not a sort.

### 5.2 Demo structure, 5 minutes

1. Open on the global register: N sources, M tonnes/hour, unanswered, with the
   88% statistic
2. Zoom to one specific persistent high-rate leak that received no response
3. Click it: candidate operators with confidence tiers, detection timeline,
   costed daily loss
4. Generate the dossier live on screen
5. Show the escalation clock and a case changing state
6. Close on the aggregate: if these open cases were closed, X million tonnes
   CO2e — framed against the million-cars equivalent from the leaks that *were*
   fixed

### 5.3 Honest limitations to state in the demo

Naming these scores points on Learning rather than costing points on Completion:

- Attribution is probabilistic, not established fact
- Flux rates are instantaneous snapshots; annualised figures rest on a stated
  assumption
- MARS data lags 30-75 days; this is historical accountability, not live alerting
- A logged "response" means a reply was received, not that a leak was fixed
- Detection is easier over some terrain than others, so absence of alerts is not
  absence of leaks
- UNEP cautions against reading its country data as a ranking of total national
  emissions

---

## 6. Build order

Attribution before frontend. Non-negotiable.

1. WSL2 environment, repo skeleton, CLAUDE.md, config constants
2. MARS ingest into SQLite, with a test asserting the unanswered count against a
   hand-checked figure
3. ETL script producing the normalised asset snapshot
4. **Attribution: spatial index, candidate radius, scoring, confidence tiers,
   tests**
5. Costing module, with assumptions surfaced in the output
6. Case state machine and escalation clock
7. Frontend: map, case list, case detail
8. Dossier route
9. Public register, polish, deploy
10. Demo video

Rationale for the ordering: if the asset data turns out too sparse to produce
convincing matches, that becomes apparent at step 4 while the product is still
pivotable, rather than at step 7 with a finished UI sitting on top of nothing.

Fallback if attribution proves intractable: ship the same product with
attribution presented as candidate sets only, with no single named operator.

---

## 7. Stack decisions and rationale

### 7.1 Environment

Development happens in **WSL2 Ubuntu (ARM64)**, not native Windows.

Reason: Python on Windows ARM64 remains officially experimental (tier 3), and
the geospatial stack is the worst-affected part of it. `pyproj` does now ship
official `win_arm64` wheels, but Shapely, GDAL and Fiona exist only as unofficial
experimental builds explicitly marked "not intended for production use." On Linux
aarch64, every required package has proper manylinux wheels and installs cleanly
via pip.

Verified working on this machine (Snapdragon X Plus, 16 GB, Windows 11):
shapely 2.1.2, geopandas 1.1.4, pyproj 3.8.0, pyogrio 0.13.0, numpy 2.5.3,
pandas 3.0.5, fastapi 0.141.1 — all native ARM64 wheels, no build errors.

Claude Code runs natively inside WSL (`linux-arm64` build). VS Code stays on the
Windows side, connected through the WSL extension. Work inside the Linux
filesystem (`~/dev/...`), never `/mnt/c/...`, because cross-boundary file access
is dramatically slower.

### 7.2 The two-tier data pipeline

**The single most important architectural decision.**

Heavy geospatial work (reading GEM Excel/GeoJSON and OGIM GeoPackage, normalising
geometries) happens **once, offline**, in `etl/`, where geopandas and pyogrio are
permitted. Output is a compact normalised file committed to the repo.

The **runtime service never imports geopandas.** It reads only the pre-processed
output.

Reasons: the infrastructure datasets are multi-gigabyte geographic files needing
awkward native libraries; keeping them in the runtime path would make deployment
painful and page loads slow. It also makes results reproducible, which matters
for the writeup. Slow heavy work offline, fast light work at runtime.

### 7.3 Component choices

| Layer | Choice | Reason |
|---|---|---|
| Backend | FastAPI | Async, typed, fast to write, trivial deploy |
| State | SQLite | Single file, no server to install or deploy; sufficient at this scale |
| Spatial index | Shapely 2.x STRtree | Handles mixed point/line geometries, C-fast, already a dependency |
| Alternative index | scipy cKDTree | If the asset set ends up points-only |
| Frontend | Next.js | Fast to build, deploys to Vercel in one step |
| Map | MapLibre GL JS | No access token, no billing account, no signup (unlike Mapbox) |
| Charts | Recharts | Adequate, low friction |
| Styling | Tailwind | Speed; Design is a judged criterion so this gets real time |
| Dossier PDF | Styled HTML route + print stylesheet | Zero extra dependencies. WeasyPrint needs cairo/pango (painful); Playwright is heavyweight |
| Deploy | Vercel (frontend) + Render (backend) | Free tier, GitHub-linked, x86 Linux so the ARM situation never touches deployment |

### 7.4 Coordinate handling

A local tangent-plane (equirectangular) projection about the leak point, rather
than a projection library:

```python
R = 6_371_008.8  # mean Earth radius, metres (IUGG)

def to_local_xy(lat, lon, lat0, lon0):
    """Equirectangular projection about (lat0, lon0). Returns metres."""
    lat, lon, lat0, lon0 = map(np.radians, (lat, lon, lat0, lon0))
    x = R * (lon - lon0) * np.cos(lat0)
    y = R * (lat - lat0)
    return x, y
```

Justification: matching radii are under roughly 10 km, where distortion is
negligible (well under a metre of error). Avoiding a PROJ dependency keeps the
runtime lean, and every line is explainable. `pyproj` is used **only in tests**,
to validate this maths against true geodesic distances for a set of known point
pairs.

### 7.5 Claude Code working practices for this project

- **Units are the primary bug risk.** `ch4_fluxrate` is kg/h. A 1000x error
  invalidates the headline number and the whole project with it. Every conversion
  gets a test.
- **Test geometry before building on it.** Hand-written golden cases: two
  coordinates with a known distance, a point with a known nearest segment, an
  ambiguous three-candidate case. Plausible-but-wrong haversine and
  point-to-segment implementations are common; tests catch them in seconds,
  eyeballing a map does not.
- **Named constants only,** all in `config.py` with cited sources. No inline
  magic numbers.
- **Vertical slices with stated acceptance criteria.** "Ingest MARS, filter to
  unanswered, return JSON, with a test asserting the count matches a hand-checked
  number" is a good slice. "Build the attribution layer" is not.
- **Log every non-obvious decision in DECISIONS.md** as it is made. Far easier
  than reconstructing the reasoning months later for an interview.

---

## 8. Alternatives considered and rejected

Recorded so the choice is defensible and so there are fallbacks if the primary
idea fails.

### 8.1 England illegal dry-weather sewage spills — strongest backup

- **Problem:** a storm overflow discharge on a dry day is presumptively illegal,
  since overflows are only permitted during heavy rain. Surfers Against Sewage /
  Top of the Poops FOI analysis of Environment Agency data found **more than
  187,241 hours** of illegal dry-weather discharge in 2025 (an updated SAS figure
  puts it above 204,000 hours, with South West Water highest at 46,191 hours).
  2025 was the first year dry-spill data was mandatory. It is worsening:
  8,576 dry-spill discharges in all of 2025, versus **7,280 in January-May 2026
  alone**. SAS notes these dry spills are not yet counted in companies'
  Environmental Performance Assessment ratings — a live data-to-decision gap.
  Since January 2025 the EA classifies every dry-day spill, however small, as a
  pollution incident.
- **Data:** National Storm Overflow Hub (streamwaterdata.co.uk) per-company
  ArcGIS REST APIs, published within ~1 hour of a discharge under Environment Act
  s81; EA real-time rainfall API (environment.data.gov.uk/flood-monitoring,
  ~1,000 gauges, 15-minute totals, Open Government Licence, **no key**); and
  **POOPy** (`github.com/AlexLipp/POOPy`, GPL-3.0), which already standardises
  every company's messy API behind one interface.
- **Algorithm:** for each discharge, find the nearest rainfall gauge, apply the
  EA's dry-day definition (no rainfall above 0.25 mm on the day of the spill or
  the preceding 24 hours), flag, tally illegal hours per company. Pure
  thresholding, spatial joins and time-series arithmetic. No ML.
- **Prior art:** Top of the Poops (annual and per-overflow history), SAS Safer
  Seas & Rivers Service, SewageMap.co.uk (live downstream impact). None
  operationalise the EA dry-day rule continuously to auto-flag presumptively
  illegal spills and rank companies by illegal hours.
- **Risks:** near-real-time EDM data is unaudited and differs from official EDM
  Annual Returns; monitors produce false positives; EA rainfall lags 1-2 days, so
  "near-real-time" is realistically "yesterday".
- **Verdict:** lowest completion risk of any candidate, because POOPy removes the
  hardest integration work. **Rejected as primary only because it is UK-only**,
  and this hackathon is global. Retain as the primary fallback.

### 8.2 EPC-driven retrofit prioritisation

- ~29.2 million domestic energy performance certificates for England and Wales,
  free REST API (epc.opendatacommunities.org, free key by registration), with
  current and potential ratings, CO2 estimates and recommended measures per
  property.
- Budget-constrained greedy optimisation: maximise tonnes CO2 saved per pound.
- Technically safest of all candidates, least novel, UK-only. Rejected.

### 8.3 Bottom trawling in marine protected areas

- Oceana UK's *The Trawled Truth* (May 2025): an estimated **31,227 hours** of
  suspected bottom trawling in UK offshore MPAs in 2024 (revised up from 20,600
  after aligning to MMO gear codes); only **38 of the UK's 377 MPAs** are fully
  protected by law; 43% of activity by UK vessels, 38% French.
- Global Fishing Watch API is free with a registered key and is **global**, not
  UK-only.
- **Rejected on prior art:** ZME Science has already built and open-sourced
  (`github.com/tibipuiu/uk-mpa-analysis`, ukmpas.zmescience.com) a Flask app on
  exactly the GFW 4Wings API plus MPA boundaries stack. Only viable with a sharp
  new angle, e.g. seabed blue-carbon disturbed in tonnes rather than hours.

### 8.4 UK wind curtailment

- Britain spent **£1,467,027,582** in 2025 switching off wind turbines and paying
  replacement generation (wastedwind.energy, on the Elexon Insights API).
- **Rejected on prior art:** wastedwind.energy and
  renewables-map.robinhawkes.com already do this well.

### 8.5 Global candidates considered late (unverified to the same standard)

Noted because the early shortlist was wrongly UK-skewed, and these are genuinely
global. None were researched to the depth of the methane candidate.

- **Wildfires inside protected areas.** NASA FIRMS gives global active-fire
  detections within 3 hours of satellite observation (some US/Canada in real
  time), free with a registered MAP_KEY, CSV via API. Overlay against the World
  Database on Protected Areas for fire-hours burning inside legally protected
  land, ranked by country. Genuinely global and live.
- **Light pollution in dark-sky reserves.** VIIRS night-lights against designated
  dark-sky areas, measuring encroachment over time.

### 8.6 Categories deliberately avoided

Oversaturated at hackathons: generic carbon-footprint calculators, webcam
recycling sorters, tree-counting and deforestation-alert viewers, generic
smart-irrigation apps, generic air-quality maps, and methane plume *detection*
from imagery (mature academic work plus existing hackathon entries).

---

## 9. Competition logistics

- **NextStep Hacks 2026**, HackAlphaX, on Devpost. Theme: "Earth Forward".
- **Deadline:** 21 September 2026 01:00 GMT+4 = **20 September 2026, 22:00 UK
  time.** The final day is effectively a half-day.
- 786 registered participants. Realistically expect on the order of 80-150 actual
  submissions, of which perhaps 15-25 are serious.
- **Required submission:** a demo/pitch video no longer than 5 minutes, a link to
  the code repository, and a link to the live application if applicable.
- **Cross-submission to other hackathons this month is permitted**, provided the
  other hackathon also allows it.
- If continuing prior work, the Devpost entry **must** specify what was built
  before the hackathon and what was built during it. This project starts from
  scratch on 15 September 2026, which is worth stating plainly.
- Judging criteria: Originality, Adherence to Track, Completion, Learning,
  Design, Technology.

---

## 10. Source list

Primary:
- UNEP, "Better data driving action on methane emissions, but more work needed",
  press release, Nairobi, 22 October 2025 (report: *An Eye on Methane: From
  measurement to momentum*)
- IEA, "Responding to Satellite Notifications from the Methane Alert and Response
  System", technical guidance with IMEO
- UNEP IMEO Eye on Methane data platform, methanedata.unep.org — download page,
  MARS data dictionary, MARS FAQ, and the MARS country-response-rate snapshot
  (April 2026)
- Schuit, B.J., Maasakkers, J.D., et al. (2023), "Automated detection and
  monitoring of methane super-emitters using satellite data", *Atmospheric
  Chemistry and Physics*

Secondary and journalistic:
- Eye on Global Transparency, "UNEP Describes Methane Mitigation Successes;
  Transparency Issues Remain", 23 April 2026
- Down To Earth, "Only 1 per cent methane emissions alerts to governments and
  companies have received responses: UNEP", 15 November 2024
- IISD/SDG Knowledge Hub, "UN Report Calls for Scaling Action on Methane Through
  Data-driven Solutions"
- Times of Central Asia, UNEP interview on Central Asia methane, 2026

For the rejected alternatives:
- Surfers Against Sewage, "SAS investigation exposes illegal sewage dumping at
  England's 'cleanest' beaches"
- The Guardian / reporting on EA whistleblower FOI data, 1 September 2026
- Environment Agency blog, "What are dry day spills?", 28 August 2024, and
  "Bathing Season 2025 storm overflow EDM data analysed", 4 December 2025
- Oceana UK, *The Trawled Truth*, May 2025
- NASA FIRMS documentation, firms.modaps.eosdis.nasa.gov and
  earthdata.nasa.gov/data/tools/firms

Environment and tooling:
- Anthropic, Claude Code setup documentation,
  docs.anthropic.com/en/docs/claude-code/setup
- cgohlke/win_arm64-wheels (experimental Windows ARM64 wheels; explicitly not for
  production use)
- Python on Windows ARM64 status discussion (tier-3 experimental)
