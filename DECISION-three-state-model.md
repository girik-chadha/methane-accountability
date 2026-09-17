# DECISION: Three-state case model (answered + unanswered)

Date: 2026-09-17
Status: agreed, to implement when backend is wired to real MARS data

## Context
Demo dataset currently shows only the 1,394 UNANSWERED cases (gov=No AND operator=No).
Per-country counts in the demo are illustrative placements, NOT real — do not read into them
(e.g. "Canada = 6" is a demo artifact). Real coordinates/countries come from mars.db + assets.parquet.

## The funnel (real data)
- 4,072 sources in MARS
- 2,369 tagged "Not Applicable" by UNEP -> no accountability process applies -> excluded
- 1,703 tracked for a response
- 1,394 = both government AND operator replied "No" -> UNANSWERED (current app)

## Decision: show answered cases too, as SUCCESS stories
Reframe from pure red/doom to an arc: ignored -> acted-on. More motivating, same dataset,
fits Earth Forward track. Green success markers alongside red.

## CRITICAL distinction (do not blur — a sharp judge will catch it)
- A logged "response" in MARS = someone REPLIED, not that the leak was FIXED.
- Actual mitigation cases are tiny: 1 (2023), 11 (2024), 23 (2025), 6 (2026 to April).
- => Money-saved / environment-saved figures attach ONLY to confirmed mitigation cases,
  NOT to every "responded" case.

## Three-state model
1. UNANSWERED (red)  — gov=No AND operator=No. 1,394 cases. Wasted gas / harm ongoing.
2. REPLIED (amber)   — someone answered, but no confirmed fix. Show as "engaged, outcome unconfirmed".
3. FIXED (green)     — confirmed mitigation case. Attach harm-avoided + money-recovered figures HERE only.

## Hero stat (verified, UNEP, COP29 / Inger Andersen)
Algeria + Nigeria leaks plugged ~= 1 million cars off the road. Use for the "what responding achieves" moment.

## Optional UI
Filter toggle defaulting to unanswered, letting a judge watch the 4,072 narrow to the 1,394 backlog —
makes the funnel visual instead of asking them to trust the number.
