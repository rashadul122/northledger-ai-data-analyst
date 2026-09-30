"""The chart review of 30 Sep 2026: its repro scripts, rerun against the site's adapter (engine/). Every file
they read or make is synthetic (made by the scripts, seeded). Run with the adapter's Python (pandas):
    python tools/fixtures/review-viz/py/<script>.py
"""
import os, sys, io, csv, json, copy, random
os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
# where a script writes what it makes (the CSV it ran, the records): REVIEW_VIZ_OUT, else a temp folder
import tempfile
OUT = os.environ.get("REVIEW_VIZ_OUT") or tempfile.mkdtemp(prefix="review-viz-")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, os.path.join(SITE, "engine"))
sys.path.insert(0, os.path.join(SITE, "tools", "fixtures", "viz"))
import nl_browser as NB
import nl_viz as NV
import validate_spec as VS
SPEC = VS.load(os.path.join(SITE, "tools", "fixtures", "viz", "spec.json"))

def csv_bytes(header, rows):
    buf = io.StringIO(); w = csv.writer(buf, lineterminator="\n"); w.writerow(header); w.writerows(rows)
    return buf.getvalue().encode("utf-8")

def run(data, name, plan=None, as_of="2026-09-30", **dec):
    d = dict(dec)
    if plan is not None:
        d["__plan__"] = plan
    return NB.run(data, name, "", d or None, as_of)

def summary(rep, show_refused=True):
    v = rep.get("viz") or {}
    print("ok", rep.get("ok"), rep.get("error"), "chosen_by", v.get("chosen_by"))
    for c in v.get("charts") or []:
        errs = VS.validate(c, SPEC["schema"], SPEC["schema"]) or VS.check_record(c, SPEC)
        print(" CHART", c["id"], "|", c["title"], "| supp", c["suppressed"], "| spec errs:", errs[:3])
    if show_refused:
        for r in v.get("refused") or []:
            print(" REFUSED", r)

def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield str(k); yield from strings(v)
    elif isinstance(o, list):
        for x in o:
            yield from strings(x)

def clean_df(rep):
    import pandas as pd
    return pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]), dtype=str, keep_default_na=False)
