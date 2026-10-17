#!/usr/bin/env python3
"""Fixtures for the Word write-up (src/js/46-report-docx.js; tools/check_report_docx.mjs).

    ../agent-demo/venv/bin/python tools/fixtures/report-docx/make_docx_fixtures.py UI_FIXTURE_DIR

UI_FIXTURE_DIR is a folder tools/make_ui_fixtures.py wrote (the engine's own reports, run natively). For each of
`sample` (the site's messy rental ledger) and `orders-private` (a file with a withheld, a coded and a kept personal
column) this writes <name>.json: {about, results, engine, forbid}
  results  what the page posts to /report and keeps for the PDF (nl_browser.results_for_ai of the report), so the
           Word write-up is built from the same input as the PDF;
  engine   the slice of the engine's full report the write-up reads beyond it: input, health.columns (name, type,
           flags, completeness), cleaning (counts, fixes, set-aside reasons), answer, story.cannot_answer, limitations,
           privacy, estimand;
  forbid   values of withheld columns that must never appear in the document (orders-private only).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def main(src):
    engine_root = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
    sys.path.insert(0, engine_root)
    sys.path.insert(0, os.path.join(SITE, "engine"))
    import nl_browser
    import csv
    for name in ("sample", "orders-private"):
        rep = json.load(open(os.path.join(src, name + ".json"), encoding="utf-8"))
        res = nl_browser.results_for_ai(rep)
        h = rep.get("health") or {}
        cols = [{k: c.get(k) for k in ("name", "type", "n", "flagged", "withheld", "completeness")} for c in h.get("columns") or []]
        cl = rep.get("cleaning") or {}
        eng = {"input": {k: (rep.get("input") or {}).get(k) for k in ("name", "rows", "columns")},
               "health": {"columns": cols, "score": h.get("score")},
               "cleaning": {k: cl.get(k) for k in ("rows_in", "rows_clean", "rows_quarantined", "fixes", "quarantine_reasons")},
               "answer": rep.get("answer"), "story": {"cannot_answer": (rep.get("story") or {}).get("cannot_answer") or []},
               "limitations": rep.get("limitations") or [], "privacy": rep.get("privacy"), "estimand": rep.get("estimand")}
        forbid = []
        if name == "orders-private":
            wh = [c["name"] for c in cols if c.get("withheld")]
            with open(os.path.join(src, name + ".csv"), encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    for c in wh:
                        v = (row.get(c) or "").strip()
                        # a value specific enough to find (a note's words, a phone number), never a bare date or number
                        if len(v) >= 10 and re.search(r"[A-Za-z]", v) and v not in forbid:
                            forbid.append(v)
        out = {"about": "made by make_docx_fixtures.py from tools/make_ui_fixtures.py's %s report" % name,
               "results": res, "engine": eng, "forbid": forbid[:200]}
        with open(os.path.join(HERE, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True, allow_nan=False, default=str)
            f.write("\n")
        print(name, len(json.dumps(out)), "bytes,", len(res.get("charts") or []), "charts,", len(forbid), "forbidden values")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
