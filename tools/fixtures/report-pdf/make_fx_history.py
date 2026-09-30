"""Builds fx-history-results.json: the FX run's engine results for the report PDF's checks (the historical range).

    python3 tools/fixtures/report-pdf/make_fx_history.py

The site's packed engine (engine/northledger-browser.zip), unzipped into a temporary folder and run natively as the
page's engine worker runs it (the source trees off sys.path, NL_BROWSER_STRICT unset), on
tools/fixtures/eval/fx_usd_cad.csv (the Bank of Canada daily U.S. dollar rate, 2017 to 2026) under the plan below and
analysis date 2026-09-29: rep = nl_browser.run_json(...); results = nl_browser.results_json(rep), whole. A level
(VALUE, typed level) gets no forecast (the engine forecasts counts and totals only) and its historical range instead:
group history_range, ten items (the 12-month and 3-month windows: their count, 10th, 50th and 90th percentiles and
the share that rose), facts about the past with no grade. The same run as insight-proxy/test/scenarios-fx-history.json.
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
CSV = os.path.join(SITE, "tools", "fixtures", "eval", "fx_usd_cad.csv")
OUT = os.path.join(HERE, "fx-history-results.json")
AS_OF = "2026-09-29"
PLAN = {"goal": "How has the Canadian dollar price of one U.S. dollar moved?", "kind": "time_series",
        "understanding": "The Bank of Canada daily U.S. dollar rate.", "primary": "VALUE",
        "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date", "unit": ""},
                    {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"},
                    {"name": "STATUS", "semantic_type": "metadata", "role": "metadata", "unit": ""}],
        "operations": [{"op": "set_aside", "columns": ["STATUS"]}, {"op": "exclude_blank", "column": "VALUE"}],
        "analyses": [{"type": "trend", "columns": ["VALUE"]}, {"type": "distribution", "columns": ["VALUE"]}]}
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
rep = json.loads(NB.run_json(open(req["csv"], "rb").read(), "fx_usd_cad.csv", "", json.dumps({"__plan__": req["plan"]}),
                             req["as_of"]))
assert rep["ok"], rep["error"]
json.dump({"snapshot": rep["engine"]["snapshot"], "results": json.loads(NB.results_json(json.dumps(rep)))}, sys.stdout,
          allow_nan=False)
"""


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="nl-fx-pack-")
    try:
        with zipfile.ZipFile(ZIP) as z:
            z.extractall(tmp)
        got = subprocess.run([sys.executable, "-c", RUN, tmp], input=json.dumps({"csv": CSV, "plan": PLAN, "as_of": AS_OF}),
                             capture_output=True, text=True, cwd=tmp, check=True)
        run = json.loads(got.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    hist = [x["id"] for x in run["results"]["scenarios"]["items"] if x["group"] == "history_range"]
    assert len(hist) == 10, hist
    out = {"about": __doc__.strip().split("\n\n")[-1].replace("\n", " "),
           "made_with": "python3 tools/fixtures/report-pdf/make_fx_history.py (the packed engine, run natively)",
           "engine_snapshot": run["snapshot"], "zip_sha256": hashlib.sha256(open(ZIP, "rb").read()).hexdigest(),
           "csv_sha256": hashlib.sha256(open(CSV, "rb").read()).hexdigest(), "as_of": AS_OF, "plan": PLAN,
           "results": run["results"]}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    print("wrote %s (%d bytes; %d history items)" % (OUT, os.path.getsize(OUT), len(hist)))


if __name__ == "__main__":
    main()
