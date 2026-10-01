"""Builds final5-results.json: the engine results of the final evaluation's two cases (1 Oct 2026) for the report PDF's
checks (tools/check_report_pdf.mjs, "the final evaluation's cases").

    /Users/mdrashad/data-analytics-portfolio/agent-demo/venv/bin/python tools/fixtures/report-pdf/make_final5.py

The site's packed engine (engine/northledger-browser.zip), unzipped into a temporary folder and run natively as the
page's engine worker runs it (the source trees off sys.path, NL_BROWSER_STRICT unset); results = nl_browser.results_json,
whole. Two runs:
  fx       tools/fixtures/eval/fx_usd_cad.csv (StatCan 33-10-0036-01, the U.S. dollar rate) at 2026-09-29, the plan of
           make_fx_history.py: a trend (a line of a level, 1.25 to 1.40 CAD per USD) and a distribution (12 bins); the
           plan's exclude_blank sets aside 569 of the 3,526 rows (16.1%).
  reviews  tools/fixtures/eval/reviews_synthetic.csv (SYNTHETIC, make_reviews.py; never the research-licensed review
           rows) at 2026-09-30, review_text kept: the plan sets aside the "All Electronics" department (445 of 2,565
           rows, 17.3%) on an "umbrella ... overlapping" reason the engine checks (none of the rows repeats a kept row),
           compares the rating by department (5 groups left) and asks for the theme chart of the kept review_text; the
           newest row is 1,279 days old, so the health score is 0 and says why.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
ZIP = os.path.join(SITE, "engine", "northledger-browser.zip")
EVAL = os.path.join(SITE, "tools", "fixtures", "eval")
OUT = os.path.join(HERE, "final5-results.json")
FX_PLAN = {"goal": "How has the Canadian dollar price of one U.S. dollar moved?", "kind": "time_series",
           "understanding": "The Bank of Canada daily U.S. dollar rate.", "primary": "VALUE",
           "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date", "unit": ""},
                       {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"},
                       {"name": "STATUS", "semantic_type": "metadata", "role": "metadata", "unit": ""}],
           "operations": [{"op": "set_aside", "columns": ["STATUS"]}, {"op": "exclude_blank", "column": "VALUE"}],
           "analyses": [{"type": "trend", "columns": ["VALUE"]}, {"type": "distribution", "columns": ["VALUE"]}],
           "quality_risks": ["VALUE is blank on 569 rows marked '..' (no rate that day); those rows were excluded."]}
REVIEWS_PLAN = {
    "goal": "What do customers praise and complain about, and how do the departments compare?", "kind": "text_corpus",
    "understanding": "Product reviews, one row per review, with a 1-5 star rating, a department and a brand.",
    "primary": "rating",
    "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"},
                {"name": "verified_purchase", "semantic_type": "boolean", "role": "segment"},
                {"name": "helpful_votes", "semantic_type": "count", "role": "driver"},
                {"name": "department", "semantic_type": "category", "role": "segment"},
                {"name": "brand", "semantic_type": "entity", "role": "entity"},
                {"name": "review_title", "semantic_type": "free_text", "role": "metadata"},
                {"name": "review_text", "semantic_type": "free_text", "role": "driver"}],
    "operations": [{"op": "exclude_rows", "column": "department", "values": ["All Electronics"]},
                   {"op": "set_aside", "columns": ["review_title"]}],
    "analyses": [{"type": "compare", "columns": ["rating"], "by": "department"},
                 {"type": "themes", "columns": ["review_text"]}],
    "quality_risks": ["'All Electronics' reads as an umbrella department overlapping the specific ones, so it was "
                      "excluded; if it held unique reviews they are not covered.",
                      "Ratings are skewed high, so averages sit near the ceiling."],
    "charts": [{"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"],
                "why": "Which words the low ratings use."}]}
RUNS = {"fx": ("fx_usd_cad.csv", {"__plan__": FX_PLAN}, "2026-09-29"),
        "reviews": ("reviews_synthetic.csv", {"__plan__": REVIEWS_PLAN, "review_text": "keep"}, "2026-09-30")}
# run in a child process whose sys.path holds only the unzipped pack (and the standard library and site-packages)
RUN = r"""
import json, os, sys
pack = sys.argv[1]
sys.path[:] = [p for p in sys.path if "northledger-core" not in p and "portfolio-website" not in p]
sys.path.insert(0, pack)
os.environ.pop("NL_BROWSER_STRICT", None)
import nl_browser as NB
assert NB.__file__.startswith(pack), NB.__file__
req = json.load(sys.stdin)
rep = json.loads(NB.run_json(open(req["csv"], "rb").read(), req["name"], "", json.dumps(req["dec"]), req["as_of"]))
assert rep["ok"], rep["error"]
json.dump({"snapshot": rep["engine"]["snapshot"], "results": json.loads(NB.results_json(json.dumps(rep)))}, sys.stdout,
          allow_nan=False)
"""


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="nl-final5-pack-")
    runs, snap = {}, None
    try:
        with zipfile.ZipFile(ZIP) as z:
            z.extractall(tmp)
        for key, (name, dec, as_of) in RUNS.items():
            csv = os.path.join(EVAL, name)
            got = subprocess.run([sys.executable, "-c", RUN, tmp], input=json.dumps({"csv": csv, "name": name, "dec": dec,
                                                                                     "as_of": as_of}),
                                 capture_output=True, text=True, cwd=tmp, check=True)
            run = json.loads(got.stdout)
            snap = run["snapshot"]
            runs[key] = {"csv": "tools/fixtures/eval/" + name, "csv_sha256": hashlib.sha256(open(csv, "rb").read()).hexdigest(),
                         "as_of": as_of, "decisions": {k: v for k, v in dec.items() if k != "__plan__"},
                         "plan": dec["__plan__"], "results": run["results"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    fx, rv = runs["fx"]["results"], runs["reviews"]["results"]
    bins = [c for c in fx["charts"] if c.get("kind") == "bars"]
    assert bins and len(bins[0]["series"]) == 12, [len(c.get("series") or []) for c in bins]
    assert rv["plan_row_drops"] and rv["plan_row_drops"][0]["notice"] and rv["health_explain"], rv["plan_row_drops"]
    assert any(c.get("chart") == "theme_rating_heatmap" for c in rv["charts"]), [c.get("kind") for c in rv["charts"]]
    out = {"about": __doc__.strip().split("\n\n")[-1].replace("\n", " "),
           "made_with": "tools/fixtures/report-pdf/make_final5.py (the packed engine, run natively)",
           "engine_snapshot": snap, "zip_sha256": hashlib.sha256(open(ZIP, "rb").read()).hexdigest(), "runs": runs}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    print("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))


if __name__ == "__main__":
    main()
