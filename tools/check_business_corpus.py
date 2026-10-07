#!/usr/bin/env python3
"""
Run the business-file corpus (tools/fixtures/structure/make_business.py: 200 ordinary business tables in plain layout) through an
engine and write what each said, one record a file, to a JSON file. The test (tools/test_nl_business.py) reads the same records
and judges them; this script is also how the corpus is run under another engine (the old one, for the before-and-after):

    NL_BROWSER_DIR=/path/to/engine  python tools/check_business_corpus.py --out out.json [--detail] [--start 0 --count 200]

--detail runs the file with its total rows taken out (the reference: what the engine says of the plain detail rows).
One engine process; nothing is fetched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ADAPTER_DIR = os.path.abspath(os.environ.get("NL_BROWSER_DIR") or os.path.join(SITE, "engine"))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ADAPTER_DIR)
sys.path.insert(0, os.path.join(HERE, "fixtures", "structure"))
import make_business as MB  # noqa: E402
import nl_browser as NB  # noqa: E402

AS_OF = "2026-09-30"


def summarise(rep: dict) -> dict:
    """What the engine said: the headline, whether it refused, and the figure it stands behind (the estimand's, else the core's
    change finding of the measure)."""
    est = rep.get("estimand") or {}
    fig = est.get("figures") or {}
    out = {"ok": bool(rep.get("ok")), "error": str(rep.get("error") or "")[:120],
           "headline": str((rep.get("story") or {}).get("headline") or "")[:300],
           "structure_kind": (rep.get("structure") or {}).get("kind"), "usable": (rep.get("structure") or {}).get("usable"),
           "roles": {d.get("column"): d.get("role") for d in (rep.get("structure") or {}).get("dims") or []},
           "estimand": bool(est), "complete": est.get("complete"),
           "refused": (not est) and "business analysis did not run" in str((rep.get("story") or {}).get("headline") or ""),
           "aggregation": (est.get("measure") or {}).get("aggregation"), "comparison": est.get("comparison"),
           "period": (est.get("period") or {}).get("kind") if est else None}
    if fig.get("latest", {}).get("value") is not None:
        out.update(source="estimand", prior=fig["prior"]["value"], latest=fig["latest"]["value"],
                   pct=(fig.get("change_pct") or {}).get("value"))
    else:
        led = {}
        try:
            for x in json.loads(rep["downloads"]["ledger_json"]).get("analysis_ledger") or []:
                led[str(x.get("id"))] = x.get("value")
        except Exception:  # noqa: BLE001
            led = {}
        ch = [(f.get("id"), f.get("value")) for f in rep.get("findings") or []
              if f.get("kind") == "business" and str(f.get("id")).endswith(".change") and not str(f.get("id")).startswith("measure.volume")]
        tot = [c for c in ch if ".total." in str(c[0])]
        pick = (tot or ch or [(None, None)])[0]
        out.update(source="finding" if pick[0] else None, finding=pick[0], pct=pick[1])
        base = pick[0][:-len(".change")] if pick[0] else ""
        for k in ("last12", "last12_mean"):
            if base and (base + "." + k) in led:
                out["latest"] = led[base + "." + k]
        for k in ("prior12", "prior12_mean"):
            if base and (base + "." + k) in led:
                out["prior"] = led[base + "." + k]
    return out


def run_corpus(start: int = 0, count: int = 200, detail: bool = False, log=None) -> list:
    res = []
    for i in range(start, start + count):
        full, plain, sp = MB.make(i)
        t0 = time.perf_counter()
        NB._PROFILE_CACHE.clear()
        try:
            rep = NB.run(plain if detail else full, "table.csv", "", {}, AS_OF)
            rec = summarise(rep)
        except Exception as exc:  # noqa: BLE001 - a crash is a result
            rec = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]), "crash": True}
        rec.update(i=i, freq=sp["freq"], kind=sp["kind"], total=sp["total"], total_name=sp["total_name"],
                   seconds=round(time.perf_counter() - t0, 2))
        res.append(rec)
        if log:
            log("%3d %-5s %-6s total=%-7s %s pct=%s %s" % (i, sp["freq"], sp["kind"], sp["total"], rec.get("source"),
                                                          rec.get("pct"), "REFUSED" if rec.get("refused") else ""))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    res = run_corpus(a.start, a.count, a.detail, log=lambda s: (print(s), sys.stdout.flush()))
    with open(a.out, "w") as fh:
        json.dump({"engine": ADAPTER_DIR, "detail": a.detail, "records": res}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
