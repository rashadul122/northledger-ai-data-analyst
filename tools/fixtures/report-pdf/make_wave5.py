"""Builds wave5-results.json: the engine's results on five SYNTHETIC statistical tables, for the report writer's wave 5 checks.

    python3 tools/fixtures/report-pdf/make_wave5.py

The site's packed engine (engine/northledger-browser.zip), unzipped into a temporary folder and run natively as the page's engine
worker runs it (the source trees off sys.path, NL_BROWSER_STRICT unset), on tables built here from tools/fixtures/structure/
make_cubes.py (none is an official table; every number is drawn from a fixed seed), the planner off, analysis date 2026-09-30:
results = nl_browser.results_json(nl_browser.run_json(...)), whole, what the page POSTs to /report as `results`:

  nototal    five regions and NO total row (make_cubes.no_total with two suppressed cells): the headline is the sum of the five
             regions, built from 5 regions, the sum-check "not possible (no total row)", 10 matched months
  quarterly  a Total and four regions of a stock (persons) published every quarter (make_cubes.periodic): 4-quarter averages,
             quarters in every word, no forecast and no audit
  measures   a Total and three regions in two measures held in one value column (make_cubes.measures_units_dollars): one measure
             shown (Sales value, chosen by the engine's default order), the other listed under left out and why
  onemember  (wave 5b) a rate of six provinces with NO total member, the first province in the middle of the others' range
             (make_cubes.rate_table): one member shown by dominance, "one member shown: Echo; this table has no total member, so
             this is not a national figure", never described as a published total
  namedtotal (wave 5c) a price index on two bases with a named whole, Canada, and one province (make_cubes.index_two_bases): Canada is the
             aggregate by its name though no sum-check can verify it ("Canada: the named total; not verifiable by a sum-check (an index
             cannot be summed)"), never the more dominant Ontario
  quarterly_flow (wave 5c) a Total and four regions of dollars published every quarter (make_cubes.periodic): the claim, the chart's
             supports and inputs.op and the unallocated item's assumes count quarters, never 12 months
  ambiguous  (wave 5c) a Total and five regions of persons that cannot say whether they accumulate (make_cubes.counts): averaged, and
             the unallocated residual is exactly 0 (never -3.41e-13)
  refused    (wave 5b) a table of series with totals (make_cubes.partition) read while the structure layer cannot run (nl_structure
             is made unimportable in the run): the business analysis did not run, structure {kind: "error", error: {stage, type,
             message}}, no estimand, no figure
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
OUT = os.path.join(HERE, "wave5-results.json")
AS_OF = "2026-09-30"
sys.path.insert(0, os.path.join(HERE, "..", "structure"))
import make_cubes as MC  # noqa: E402

CASES = {
    "nototal": ("regions_no_total.csv", lambda: MC.no_total(hide=[("Charlie", 38), ("Bravo", 34)])),
    "quarterly": ("population_quarterly.csv", lambda: MC.periodic("quarter", "iso", stock=True)),
    "measures": ("sales_and_units.csv", MC.measures_units_dollars),
    "onemember": ("provinces_no_total.csv", lambda: MC.rate_table("none")),
    "refused": ("regions_layer_down.csv", MC.partition),
    "namedtotal": ("price_index_two_bases.csv", MC.index_two_bases),
    "quarterly_flow": ("sales_quarterly.csv", lambda: MC.periodic("quarter", "iso", stock=False)),
    "ambiguous": ("persons_ambiguous.csv", MC.counts),
}
BLOCKED = {"refused"}                          # the structure layer cannot be imported in these runs
RUN = r"""
import json, os, sys
pack = sys.argv[1]
sys.path[:] = [p for p in sys.path if "northledger-core" not in p and "portfolio-website" not in p]
sys.path.insert(0, pack)
os.environ.pop("NL_BROWSER_STRICT", None)
import nl_browser as NB
assert NB.__file__.startswith(pack), NB.__file__
req = json.load(sys.stdin)
if req.get("block"):
    sys.modules["nl_structure"] = None             # `import nl_structure` raises: the layer cannot run
rep = json.loads(NB.run_json(open(req["csv"], "rb").read(), req["name"], "", json.dumps({}), req["as_of"]))
assert rep["ok"], rep["error"]
json.dump({"snapshot": rep["engine"]["snapshot"], "results": json.loads(NB.results_json(json.dumps(rep)))}, sys.stdout,
          allow_nan=False)
"""


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="nl-wave5-pack-")
    out = {"about": __doc__.strip().split("\n\n")[0], "made_with": "python3 tools/fixtures/report-pdf/make_wave5.py (the packed engine, run natively)",
           "zip_sha256": hashlib.sha256(open(ZIP, "rb").read()).hexdigest(), "as_of": AS_OF, "cases": {}}
    try:
        with zipfile.ZipFile(ZIP) as z:
            z.extractall(tmp)
        for key, (name, mk) in CASES.items():
            csv = os.path.join(tmp, name)
            with open(csv, "wb") as fh:
                fh.write(mk())
            got = subprocess.run([sys.executable, "-c", RUN, tmp], input=json.dumps({"csv": csv, "name": name, "as_of": AS_OF,
                                                                                    "block": key in BLOCKED}),
                                 capture_output=True, text=True, cwd=tmp, check=True)
            run = json.loads(got.stdout)
            res = run["results"]
            if key in BLOCKED:
                assert not res.get("estimand") and res["structure"]["kind"] == "error", "%s: the run was not refused" % key
            else:
                assert res.get("estimand"), "%s: the run carries no estimand" % key
            out["engine_snapshot"] = run["snapshot"]
            out["cases"][key] = {"name": name, "csv_sha256": hashlib.sha256(open(csv, "rb").read()).hexdigest(), "results": res}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    print("wrote %s (%d bytes; %s)" % (OUT, os.path.getsize(OUT), ", ".join(
        "%s: %s" % (k, (v["results"].get("estimand") or {"text": v["results"]["structure"]["reason"]})["text"][:70])
        for k, v in out["cases"].items())))


if __name__ == "__main__":
    main()
