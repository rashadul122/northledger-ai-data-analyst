"""Builds retail-results.json: a statistical table's engine results for the report PDF's checks (wave 4, track B).

    python3 tools/fixtures/report-pdf/make_retail.py [--csv PATH]

The site's packed engine (engine/northledger-browser.zip), unzipped into a temporary folder and run natively as the
page's engine worker runs it (the source trees off sys.path, NL_BROWSER_STRICT unset), on Statistics Canada table
20-10-0056-01, retail trade by province and by store type, 36,735 rows, 465 series x 79 months (the file is not in the
repository: it is the visitor's CSV; this maker reads it from --csv, or from $NL_RETAIL_CSV, or from the owner's
.work/eval folder, and records its sha256), the planner off (decisions {}), analysis date 2026-09-30:
rep = nl_browser.run_json(...); results = nl_browser.results_json(rep), whole. What the page POSTs to /report as
`results`. It holds an estimand (what the headline measures: the 12-month total of Total retail sales in Canada, the
change and its units), a sum-check, the engine's grade as a PROCESS grade (month-to-month noise of 36 monthly totals,
not a test of a published total), a forecast with its back-test (nl_inference.forecast_audit), a contribution waterfall
with its not-allocated step and each part's own change, and 53 of the 75 scenario items (the payload budget keeps the
parts that moved against the change). The hand-written report that goes with it is retail-response.json (a TEST FIXTURE,
never a live reply).
"""
import argparse
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
MAIN = "/Users/mdrashad/data-analytics-portfolio/portfolio-website"
DEFAULT_CSV = MAIN + "/.work/eval/data/statcan/retail_sales_provinces_2020_2026.csv"
OUT = os.path.join(HERE, "retail-results.json")
AS_OF = "2026-09-30"
NAME = "retail_sales_provinces_2020_2026.csv"
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
rep = json.loads(NB.run_json(open(req["csv"], "rb").read(), req["name"], "", json.dumps({}), req["as_of"]))
assert rep["ok"], rep["error"]
json.dump({"snapshot": rep["engine"]["snapshot"], "results": json.loads(NB.results_json(json.dumps(rep)))}, sys.stdout,
          allow_nan=False)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.environ.get("NL_RETAIL_CSV") or DEFAULT_CSV)
    a = ap.parse_args()
    if not os.path.exists(a.csv):
        sys.exit("the retail table is not here: %s (pass --csv or set NL_RETAIL_CSV)" % a.csv)
    tmp = tempfile.mkdtemp(prefix="nl-retail-pack-")
    try:
        with zipfile.ZipFile(ZIP) as z:
            z.extractall(tmp)
        got = subprocess.run([sys.executable, "-c", RUN, tmp], input=json.dumps({"csv": a.csv, "name": NAME, "as_of": AS_OF}),
                             capture_output=True, text=True, cwd=tmp, check=True)
        run = json.loads(got.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res = run["results"]
    assert res.get("estimand") and (res.get("forecast") or {}).get("audit"), "the run carries no estimand or no forecast audit"
    out = {"about": __doc__.strip().split("\n\n")[-1].replace("\n", " "),
           "made_with": "python3 tools/fixtures/report-pdf/make_retail.py (the packed engine, run natively)",
           "engine_snapshot": run["snapshot"], "zip_sha256": hashlib.sha256(open(ZIP, "rb").read()).hexdigest(),
           "csv_sha256": hashlib.sha256(open(a.csv, "rb").read()).hexdigest(), "as_of": AS_OF, "name": NAME, "plan": None,
           "results": res}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    print("wrote %s (%d bytes; %d scenario items kept of %s)" % (
        OUT, os.path.getsize(OUT), len(res["scenarios"]["items"]), (res["scenarios"].get("refused") or [""])[0][:60]))


if __name__ == "__main__":
    main()
