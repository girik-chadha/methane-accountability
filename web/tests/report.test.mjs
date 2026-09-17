// Renders the committed standalone page in jsdom (no server, fetch forbidden),
// drills into USA_S_1063, and pins every detection row and candidate card the
// report shows to the exported record in web/data.json. Run by
// backend/tests/test_report_render.py; exits non-zero on any mismatch.
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FILE = join(WEB, "standalone.html");
const ID = "USA_S_1063";

const html = readFileSync(FILE, "utf8");
const src = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
assert.ok(!/\brn\(\)|Math\.random/.test(src), "no pseudo-random generator may exist in the page");
const dom = new JSDOM(html, { url: "file://" + FILE, pretendToBeVisual: true, runScripts: "outside-only" });
const w = dom.window;
w.fetch = () => { throw new Error("standalone page must not fetch"); };
const errors = [];
w.addEventListener("error", e => errors.push(String(e.error || e.message)));
await w.eval("(async () => {\n" + src + "\n})()");
const d = w.document;
const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
click(d.querySelector('[data-cont="North America"]'));
click([...d.querySelectorAll("[data-ctry]")].find(e => e.dataset.ctry === "United States of America"));
click([...d.querySelectorAll("[data-leak]")].find(e => e.dataset.leak === ID));
assert.deepEqual(errors, []);

const blob = JSON.parse(readFileSync(join(WEB, "data.json"), "utf8"));
const leak = blob.L["United States of America"].find(l => l.id === ID);
const text = el => { const walker = el.ownerDocument.createTreeWalker(el, 4); const parts = []; let n;
  while ((n = walker.nextNode())) { const t = n.textContent.trim(); if (t) parts.push(t); }
  return parts.join(" ").replace(/\s+/g, " "); };

// detection rows: one per exported detection, in order, gaps only where the dates show one
const rows = [...d.querySelectorAll(".rep .det:not(.gap)")].map(text);
assert.equal(rows.length, leak.det.length);
leak.det.forEach(([ts, inst, rate], i) => {
  const expected = `${ts.slice(0, 10)} ${ts.slice(11)} ${inst} ${rate == null ? "rate not reported" : rate.toLocaleString() + " kg/h"}`;
  assert.equal(rows[i], expected);
});
assert.equal(d.querySelectorAll(".rep .det.gap").length, 0, `${ID}'s detections are all on one day: no gap rows`);

// candidate cards: one per exported card, showing kind, name, dataset, party, distance, threshold
const cards = [...d.querySelectorAll(".rep .cand")].map(text);
assert.equal(cards.length, leak.cands.length + (leak.cand > leak.cands.length ? 1 : 0));
leak.cands.forEach((c, i) => {
  const card = cards[i];
  const kind = c.k.replace(/_/g, " ").replace(/^./, s => s.toUpperCase());
  assert.ok(card.startsWith(c.n || kind), `card ${i} name`);
  assert.ok(card.includes(`${kind}, ${c.ds}`), `card ${i} kind and dataset`);
  assert.ok(card.includes(c.p ? `Recorded party ${c.p}` : "No owner recorded in this dataset"), `card ${i} party`);
  assert.ok(card.includes(`${c.d.toLocaleString()} m away`), `card ${i} distance`);
  assert.ok(card.includes(`matched within ${c.th.toLocaleString()} m`), `card ${i} threshold`);
});
assert.ok(!/belong to one parent|further unit|night-time infrared|production unit/.test(d.querySelector(".rep").textContent));

// the hand-checked facts, independent of the blob
assert.equal(rows[0], "2026-01-21 17:25 Sentinel-2 76,779 kg/h");
assert.equal(rows[4], "2026-01-21 20:32 VIIRS rate not reported");
assert.ok(cards[0].startsWith("Whistler Pipeline | Midland Lateral Gas pipeline, GEM-GGIT-Pipelines-2025-11 Recorded party First Infrastructure Capital Advisors LLC; MPLX LP; Stonepeak Partners LP; West Texas Gas Inc 31.1 m away matched within 60.8 m"));
assert.ok(cards[2].startsWith("Flare detection Flare detection, OGIM-v2.7 No owner recorded in this dataset 458.2 m away matched within 752.4 m"));
// narrative: the capped model leads (20,692.6 t CH4 over 269.5 h -> 616.6 kt CO2e at GWP-100 29.8);
// the 3.1 h first-to-last span is smaller than the capped figure, so no "upper bound" sentence;
// nothing multiplies the daily rate by days of silence, and the stub button is gone
const plain = text(d.querySelector(".rep .plain"));
assert.ok(plain.includes(`Crediting each of its ${leak.nd} detections with a ${blob.M.window_days}-day window`), plain);
assert.ok(plain.includes("20.7 kt of methane over 11 days: 616.6 kt of CO₂-equivalent"), plain);
assert.ok(!plain.includes("13.01 Mt") && !/upper bound/.test(plain), plain);
assert.equal(leak.cap.t, 20692.6);
assert.equal(d.querySelector(".rep .cta"), null, "the evidence-pack stub must be gone");
assert.ok(!/evidence pack/i.test(d.querySelector(".rep").textContent));
assert.ok(text(d.querySelector(".rep ul.cav")).includes(`${blob.M.window_days}-day window`));
console.log(`ok: ${ID} renders ${rows.length} detection rows and ${leak.cands.length} candidate cards, all from the export`);
