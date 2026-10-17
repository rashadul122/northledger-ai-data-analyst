#!/usr/bin/env python3
"""
check_universal: the "any dataset" gate (universal track, 17 Oct 2026). Every file of ../corpus/universal (manifest.json: real public
files of 15+ shapes, each with what a competent report MUST show and must NOT do) goes through the engine as a visitor's file goes
(nl_browser.run, no AI), and each report is held to the rules a blank or wrong report broke:

  * it runs (no error) and says something: a headline that is not a placeholder ("did not run", "no date column");
  * it draws: at least MIN_CHARTS charts the page can draw (the renderer's own checks, run in Node on the records);
  * a table whose rows measure different things is never added up across them (an indicator table's charts come from nl_explore).

    ../agent-demo/venv/bin/python tools/check_universal.py [--only NAME] [--out DIR]

Writes OUT/universal.json (one entry per file: status, headline, charts, timings) and prints one line per file; exit 1 when a file
fails. The must-show / must-not lists are printed beside each report for a reviewer: this check enforces only what code can judge.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
CORPUS = os.path.join(os.path.dirname(SITE), "corpus", "universal")
MIN_CHARTS = 2
PLACEHOLDER = ("The monthly change test did not run", "No column holds dates", "This run produced no gated facts")

NODE_CHECK = r"""
global.window = { NLU: {} }; global.document = { readyState: 'complete', addEventListener() {} };
require(process.argv[1] + '/src/js/55-nl-viz.js');
const fs = require('fs'); const recs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = recs.map(r => { let k = 'table'; try { k = window.NLV.kindOf(r); } catch (e) {} return k; });
process.stdout.write(JSON.stringify(out));
"""


def drawable(records: list) -> list:
    """The kind each record is drawn as by the page's renderer (55-nl-viz.js; hbar records by the grammar's own rule)."""
    if not records:
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(records, fh)
        path = fh.name
    try:
        r = subprocess.run(["node", "-e", NODE_CHECK, SITE, path], capture_output=True, text=True, timeout=60)
        kinds = json.loads(r.stdout) if r.returncode == 0 and r.stdout else ["?"] * len(records)
    finally:
        os.unlink(path)
    # the grammar kinds (hbar, lines, area, multiples) are drawn by 51-nl2-charts.js: checked here by its rule for hbar
    out = []
    for rec, k in zip(records, kinds):
        if rec.get("kind") == "hbar":
            d = rec.get("data") or {}
            ok = isinstance(d.get("series"), list) and 1 <= len(d["series"]) <= 2 and isinstance(d.get("rows"), list) and \
                1 <= len(d["rows"]) <= 20 and all(isinstance(x.get("values"), list) and len(x["values"]) == len(d["series"])
                                                   for x in d["rows"])
            out.append("hbar" if ok else "table")
        elif rec.get("kind") in ("lines", "area", "multiples"):
            out.append(rec["kind"])
        else:
            out.append(k)
    return out


NODE_SHARE = r"""
const fs = require('fs'); const recs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
import(process.argv[1] + '/src/charts.js').then((m) => {
  process.stdout.write(JSON.stringify(recs.map((r, i) => { try { return m.sanitizeChart(r, i + 1).kind; } catch (e) { return 'error'; } })));
});
"""
WORKER = os.path.join(os.path.dirname(SITE), "insight-proxy")


def shared(records: list) -> list:
    """The kind each record keeps in a SHARED report (the worker's own check, insight-proxy/src/charts.js sanitizeChart): a record the
    worker cannot read is shown there as its table, which is how a visitor's shared link came back with no chart (17 Oct 2026)."""
    if not records or not os.path.isdir(WORKER):
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(records, fh)
        path = fh.name
    try:
        r = subprocess.run(["node", "-e", NODE_SHARE, WORKER, path], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout else ["?"] * len(records)
    finally:
        os.unlink(path)


def one(entry: dict) -> dict:
    sys.path[:0] = [os.path.join(SITE, "engine"), os.path.join(os.path.dirname(SITE), "northledger-core")]
    import nl_browser
    path = os.path.join(CORPUS, entry["file"])
    with open(path, "rb") as fh:
        data = fh.read()
    t0 = time.time()
    rep = nl_browser.run(data, entry["file"], "", None, None)
    secs = round(time.time() - t0, 1)
    recs = list((rep.get("viz") or {}).get("charts") or [])
    kinds = drawable(recs)
    share_kinds = shared(recs)
    drawn = [k for k in kinds if k != "table"]
    other = [c.get("type") for c in rep.get("charts") or [] if isinstance(c, dict) and c.get("type") not in ("viz", "table", "kpi_tiles",
                                                                                                         "benchmark_strip")]
    story = rep.get("story") or {}
    head = str(story.get("headline") or "")
    fails = []
    if rep.get("error"):
        fails.append("error: " + str(rep["error"])[:200])
    if not head or head.startswith(PLACEHOLDER):
        fails.append("placeholder headline: " + head[:120])
    lost = [c.get("title") for c, k, s in zip(recs, kinds, share_kinds) if k != "table" and s == "table"]
    if lost:
        fails.append("%d chart(s) drawn on the page become tables in a shared report: %s" % (len(lost), "; ".join(map(str, lost[:3]))))
    # a published series named by a code (FRED, a census): the headline and the bottom line name it in words, and a title says what it is
    codes = [h for h in str((rep.get("downloads") or {}).get("clean_csv") or "").split("\n", 1)[0].split(",")
             if re.fullmatch(r"[A-Z][A-Z0-9]{7,40}", h or "")]
    lines = [head] + [str(x.get("text") or "") for x in (rep.get("summary") or {}).get("lines") or [] if isinstance(x, dict)]
    leaked = [c for c in codes if any(re.search(re.escape(c), s, re.I) for s in lines)]
    if leaked:
        fails.append("the headline or bottom line prints the code %s instead of what it measures" % leaked[0])
    if codes and not (rep.get("title") or {}).get("title"):
        fails.append("a code-named series has no title saying what it is")
    # a series' headline says where it stands, never only what its forecast says (17 Oct 2026: "Monthly ... forecast at 2.759 for 2026-10")
    if re.match(r"^(?:Monthly|Quarterly|Weekly|Daily|Annual|Yearly)\s.+\sforecast at\s", head) and \
            len((rep.get("roles") or {}).get("measures") or []) == 1:
        fails.append("the headline is the forecast, not where the series stands: " + head[:100])
    # the manifest's own checks for this file (headline, words that must never appear, the title's lookup offer, Ask's answers)
    ck = entry.get("checks") or {}
    blob = json.dumps({k: rep.get(k) for k in ("story", "summary", "analyst", "explore", "title")}, default=str)
    if ck.get("headline_re") and not re.search(ck["headline_re"], head):
        fails.append("headline does not match %r: %s" % (ck["headline_re"], head[:140]))
    if ck.get("text_not_re"):
        bad = re.search(ck["text_not_re"], blob)
        if bad:
            fails.append("the report says %r" % bad.group(0)[:80])
    if ck.get("title_lookup") and ((rep.get("title") or {}).get("lookup") or {}).get("id") != ck["title_lookup"]:
        fails.append("no offer to look %s up on FRED" % ck["title_lookup"])
    for qa in ck.get("ask") or []:
        try:
            got = json.loads(nl_browser.analyst_more_json(nl_browser.analyst_plan_json(qa["q"]), "[]", qa["q"]))
            txt = str((got.get("answer") or {}).get("text") or "")
        except Exception as exc:  # noqa: BLE001
            txt = "error: %s" % type(exc).__name__
        if not re.search(qa["answer_re"], txt):
            fails.append("Ask %r answered %r" % (qa["q"][:50], txt[:120]))
    if len(drawn) + len(other) < MIN_CHARTS:
        fails.append("only %d drawable charts (%d viz records, %d drawn; engine charts %s)" % (
            len(drawn) + len(other), len(recs), len(drawn), other))
    return {"file": entry["file"], "shape": entry.get("shape"), "secs": secs, "ok": not fails, "fails": fails,
            "headline": head, "what_happened": list(story.get("what_happened") or [])[:6], "title": rep.get("title"),
            "charts": [{"title": c.get("title"), "chart": c.get("chart"), "drawn_as": k,
                        "shared_as": share_kinds[i] if i < len(share_kinds) else None} for i, (c, k) in enumerate(zip(recs, kinds))],
            "engine_charts": other, "explore": bool(rep.get("explore")), "date": (rep.get("roles") or {}).get("date"),
            "rows": (rep.get("input") or {}).get("rows"), "expect": entry.get("expect"), "must_not": entry.get("must_not")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "nl-universal"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    entries = man["files"] if isinstance(man, dict) and "files" in man else man
    if a.only:
        entries = [e for e in entries if a.only in e["file"]]
    results = []
    for e in entries:
        # each file in its own process: one file's failure or memory never reaches the next
        r = subprocess.run([sys.executable, "-W", "ignore", __file__, "--_one", json.dumps(e)], capture_output=True, text=True,
                           timeout=900)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            res = {"file": e["file"], "ok": False, "fails": ["crashed: " + (r.stderr or "")[-300:]], "shape": e.get("shape")}
        results.append(res)
        print("%s %-52s %6ss  %s" % ("PASS" if res["ok"] else "FAIL", e["file"][:52], res.get("secs", "-"),
                                    "; ".join(res.get("fails") or []) or res.get("headline", "")[:110]), flush=True)
    with open(os.path.join(a.out, "universal.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    bad = [r for r in results if not r["ok"]]
    print("UNIVERSAL: %d of %d files pass; details in %s" % (len(results) - len(bad), len(results),
                                                             os.path.join(a.out, "universal.json")))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--_one":
        print(json.dumps(one(json.loads(sys.argv[2])), default=str))
        sys.exit(0)
    sys.exit(main())
