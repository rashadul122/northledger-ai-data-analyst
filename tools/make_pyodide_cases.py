#!/usr/bin/env python3
"""
The cases tools/check_pyodide_cube.mjs runs in Pyodide beside the retail-shaped cube (wave 5e), and what the NATIVE engine says of
them. The packed engine in the page's Python 3.12 must say the same: roles, figures, whether a table is read. Each case is a file of the
regression pack (tools/fixtures/structure/regress/) read through the adapter exactly as the page does, planner off.

    python tools/make_pyodide_cases.py --write     # (re)write tools/fixtures/structure/pyodide_cases.json from the native engine
    python tools/make_pyodide_cases.py --check     # the json equals what the native engine says now (exit 1 when not)

The expected values are WRITTEN in the json (reviewed in a diff), never computed in the .mjs; tools/test_nl_regress.py runs --check so an
engine edit that changes a case cannot go unseen. The cases were chosen for what differs between Python 3.9 + a Mac's BLAS and Pyodide's
3.12 + its own: the sorted-lookup subset search with parts reporting in disjoint periods (r02), the weekly cadence (r07), the accent fold
(r06: unicodedata), and the unnamed-aggregate fit on a rate panel with and without its aggregate (the one decision a BLAS could tip).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, os.path.join(SITE, "engine"))
sys.path.insert(0, os.path.join(HERE, "fixtures", "structure"))
sys.path.insert(0, HERE)

import check_business_corpus as CB  # noqa: E402
import nl_browser as NB  # noqa: E402

AS_OF = "2026-09-30"
REGRESS = os.path.join(HERE, "fixtures", "structure", "regress")
OUT = os.path.join(HERE, "fixtures", "structure", "pyodide_cases.json")
CASES = [
    ("r02_combined_member_disjoint_parts", "r02_combined_disjoint_periods.csv"),
    ("r06_french_statcan", "r06_french_statcan.csv"),
    ("r07_weekly_official", "r07_weekly_official.csv"),
    ("rate_panel_with_its_aggregate", "pyodide_rate_with_aggregate.csv.gz"),
    ("rate_panel_without_one", "pyodide_rate_without_aggregate.csv.gz"),
    # wave 5f: a plain file with Total rows in a branch margin and two number columns (the adapter leaves the Total rows out and says so), an
    # average in dollars beside its whole-country row (a level, never a 12-month total), a sensitive-category column (withheld: a refusal)
    ("f12_marginalised_business_file", "f12_seed4_weekly_total_rows_two_measures.csv"),
    ("f05_average_dollars_whole_row", "pyodide_average_dollars.csv.gz"),
    ("f01_sensitive_category_dimension", "pyodide_sensitive_dimension.csv.gz"),
]


def read(name: str) -> bytes:
    p = os.path.join(REGRESS, name)
    with (gzip.open(p, "rb") if p.endswith(".gz") else open(p, "rb")) as fh:
        return fh.read()


def ensure_rate_panels() -> None:
    """The two rate panels (39 provinces, 79 months) are written once from make_cubes, as gzip, so the .mjs reads files and no generator."""
    import make_cubes as MC
    for agg, name in ((True, "pyodide_rate_with_aggregate.csv.gz"), (False, "pyodide_rate_without_aggregate.csv.gz")):
        p = os.path.join(REGRESS, name)
        if not os.path.exists(p):
            with gzip.GzipFile(p, "wb", mtime=0) as fh:
                fh.write(MC.rate_panel(39, 79, aggregate=agg))


def ensure_wave_5f_files() -> None:
    import make_cubes as MC
    for name, data in (("pyodide_average_dollars.csv.gz", MC.average_dollars()), ("pyodide_sensitive_dimension.csv.gz", MC.sensitive_dimension())):
        p = os.path.join(REGRESS, name)
        if not os.path.exists(p):
            with gzip.GzipFile(p, "wb", mtime=0) as fh:
                fh.write(data)


def say(data: bytes) -> dict:
    """What the engine says of one file, in the few fields the page-side check compares (the same names in the .mjs)."""
    NB._PROFILE_CACHE.clear()
    rep = NB.run(data, "table.csv", "", {}, AS_OF)
    s = CB.summarise(rep)
    fixes = (rep.get("cleaning") or {}).get("fixes") or []
    out = {"ok": s["ok"], "estimand": s["estimand"], "refused": s["refused"], "kind": s["structure_kind"], "usable": s["usable"],
           "roles": s["roles"], "source": s.get("source"), "prior": s.get("prior"), "latest": s.get("latest"), "pct": s.get("pct"),
           # wave 5f: how the figure is aggregated, what was withheld, how many rows the adapter left out and what the ledger says of the amount
           "aggregation": s.get("aggregation"),
           "flagged": sorted("%s:%s" % (f["column"], f["decision"]) for f in (rep.get("privacy") or {}).get("flagged") or []),
           "left_out": sum(int(f.get("count") or 0) for f in fixes if f.get("rule") in ("total_rows_left_out", "partial_month_left_out"))}
    try:
        led = {str(x.get("id")): x.get("value") for x in json.loads(rep["downloads"]["ledger_json"]).get("analysis_ledger") or []}
        out["ledger_amount"] = [led.get("measure.amount.total.prior12"), led.get("measure.amount.total.last12")]
    except Exception:  # noqa: BLE001
        out["ledger_amount"] = None
    return out


def build() -> dict:
    ensure_rate_panels()
    ensure_wave_5f_files()
    return {name: say(read(f)) | {"file": f} for name, f in CASES}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    cases = build()
    if a.write:
        with open(OUT, "w") as fh:
            json.dump(cases, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("wrote %s (%d cases)" % (OUT, len(cases)))
        return 0
    old = json.load(open(OUT))
    bad = [n for n in cases if json.loads(json.dumps(cases[n])) != old.get(n)]
    for n in bad:
        print("DIFFERS %s: now %s, json %s" % (n, json.dumps(cases[n], sort_keys=True)[:300], json.dumps(old.get(n), sort_keys=True)[:300]))
    print("pyodide cases: %s" % ("all %d match the native engine" % len(cases) if not bad else "%d differ" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
