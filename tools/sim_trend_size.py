#!/usr/bin/env python3
"""The trend test's size, simulated: engine/inference_tables.json (wave 4, track A2, T1; CONTRACT-v2.md 5.11).

    python tools/sim_trend_size.py               # every cell (about 4 minutes on 4 workers), writes the table
    python tools/sim_trend_size.py --check       # exit 1 if the table was not written by this script as it is now
    python tools/sim_trend_size.py --cells 9:0,9:0.4,9:0.8 --series 2000 --no-write   # a few cells, printed

For each number of years n and true momentum rho of the grid, SERIES no-trend series (stationary AR(1), unit
innovations, years 0..n-1) go through engine/nl_inference.trend_test exactly as the report runs it (its own fixed
seed: the size of the procedure the report uses, not of a re-seeded variant). Per cell:

  size        the share with p < 0.05: the test's false-alarm rate at its 5% level
  claims      the share the report would call "rising" or "falling" (p < 0.05 and the random-walk screen's share
              at most 0.05); the size gate itself is left out, since it reads this table
  newey_west  the share whose Newey-West 95% range excluded zero: the trend test the adapter ran before
              (nl_browser._hac_slope, lag floor(4 (n/100)^(2/9)), t with n - 2 degrees of freedom), which then
              said "rose" or "fell"
  unrestricted the size of the design's first version of the test, whose bootstrap drew at the trend line's own
              momentum instead of the no-trend one (kept for the record: why the null is imposed)

Each cell's draws come from numpy's SeedSequence(MASTER_SEED, spawn key (n, 100 rho)), so a cell's numbers do not
depend on the order the workers run in. The fixed-b section checks the critical-value polynomial a design note
recalled for Kiefer and Vogelsang (2005), cv(b) = 1.96 + 2.9694 b + 0.4160 b^2 - 0.5324 b^3 for the Bartlett
kernel (and the same cubic as a secondary source prints it), by simulating the fixed-b limit at n = 1,000: for the
mean (the case the polynomial is for) and for a linear trend's slope (the case a trend test needs). The engine does
not use it either way (inference_tables.json "fixed_b.used" is false): the bootstrap's own size is measured instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_DIR = os.path.join(SITE, "engine")
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
OUT = os.path.join(ENGINE_DIR, "inference_tables.json")
MASTER_SEED = 20261001
NS = (8, 9, 10, 12, 15, 19, 30, 60, 120)       # the design's grid, plus 60 and 120 (it covers 8 to 200 years)
RHOS = (0.0, 0.2, 0.4, 0.6, 0.8)
SERIES = 2000
WORKERS = 4
# The design note's recalled cubic, and the same cubic as a secondary source prints it (R. Susmel, University of
# Houston, econometrics lecture notes "ec1-13", 2025: "CV(L/T) = 1.96 + 2.9694 b + 0.416 b^2 - .05324 b^3", the 95%
# two-sided critical value of a t-test). The primary paper (Kiefer and Vogelsang 2005, Econometric Theory 21) could
# not be read here, so both are checked against the simulated limit instead.
FIXED_B = {"formulas": {"recalled": {"text": "cv(b) = 1.96 + 2.9694 b + 0.4160 b^2 - 0.5324 b^3",
                                     "coef": (1.96, 2.9694, 0.4160, -0.5324),
                                     "source": "the design note (WAVE4-A-DESIGN.md 3, T1), from memory"},
                        "secondary": {"text": "cv(b) = 1.96 + 2.9694 b + 0.416 b^2 - 0.05324 b^3",
                                      "coef": (1.96, 2.9694, 0.416, -0.05324),
                                      "source": "R. Susmel, University of Houston, lecture notes ec1-13 (2025), "
                                                "citing Kiefer and Vogelsang (2005)"}},
           "bs": (0.1, 0.2, 0.3, 0.5, 0.7, 1.0), "n": 1000, "reps": 40000, "tolerance": 0.05}


def _paths() -> None:
    for p in (ENGINE_ROOT, ENGINE_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def script_sha() -> str:
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


PROCEDURE = ("_ar1", "_lag1", "_resid", "_bias", "_momentum", "_prais_winsten", "_t_stat", "trend_test")


def procedure_sha() -> str:
    """sha256 over the source of the trend test's functions and its constants (engine/nl_inference.py): the
    procedure this table measured. A change to any of them makes the table stale (tools/test_nl_inference.py)."""
    import inspect
    _paths()
    import nl_inference as NI
    parts = [inspect.getsource(getattr(NI, f)) for f in PROCEDURE]
    parts.append(repr((NI.SEED, NI.TREND_B, NI.ALPHA, NI.RHO_LO, NI.RHO_HI, NI.BIAS_DRAWS, NI.BIAS_GRID)))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _cell(args):
    """One (n, rho) cell: SERIES no-trend series through the report's trend test, Newey-West, and the variant."""
    n, rho, series = args
    _paths()
    import numpy as np
    import nl_inference as NI
    os.environ.setdefault("NL_BROWSER_STRICT", "1")
    import nl_browser as NB
    ss = np.random.SeedSequence(MASTER_SEED, spawn_key=(int(n), int(round(100 * rho))))
    rng = np.random.default_rng(ss)
    Y = NI._ar1(rng, rho, series, n)
    t = np.arange(n, dtype=float)
    tc = t - t.mean()
    off = np.arange(n)
    gaps = np.ones(n - 1)
    adj = gaps == 1
    tcrit = NB._tcrit(n - 2)
    size = claims = nw = unres = 0
    for i in range(series):
        y = Y[i]
        r = NI.trend_test(t, y)
        if r["p"] is not None and r["p"] < NI.ALPHA:
            size += 1
            if r["p_random_walk"] is not None and r["p_random_walk"] <= NI.ALPHA:
                claims += 1
        b, se, _lag = NB._hac_slope(t, y)
        nw += int(se > 0 and abs(b) > tcrit * se)
        # the design's first version: the bootstrap at the trend line's own momentum
        Yi = y[None, :]
        rho_t = NI._momentum(Yi, off, tc, adj, True)
        t_obs = float(NI._prais_winsten(Yi, tc, gaps, rho_t)[2][0])
        g = np.random.default_rng(NI.SEED)
        ts = np.abs(np.nan_to_num(NI._t_stat(NI._ar1(g, rho_t[0], NI.TREND_B, n), off, tc, gaps, adj), nan=0.0))
        p_u = (1.0 + float(np.sum(ts >= abs(t_obs)))) / (NI.TREND_B + 1.0)
        unres += int(p_u < NI.ALPHA)

    def share(k):
        return round(k / float(series), 4)

    def mcse(k):
        p = k / float(series)
        return round(math.sqrt(p * (1 - p) / series), 4)
    return {"n": int(n), "rho": float(rho), "series": int(series), "size": share(size), "size_mcse": mcse(size),
            "claims": share(claims), "newey_west": share(nw), "newey_west_mcse": mcse(nw),
            "unrestricted": share(unres)}


def _bartlett_t(e, X):
    """|t| of the last regressor's coefficient with a Bartlett HAC variance of bandwidth M, for each row of errors e
    (reps, n), returned for each M in FIXED_B["bs"] * n: autocovariances by FFT."""
    import numpy as np
    n = e.shape[1]
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = e @ X @ xtx_inv.T                      # (reps, k)
    u = e - beta @ X.T
    v = u[:, :, None] * X[None, :, :]              # (reps, n, k) scores
    q = (v @ xtx_inv[:, -1])                       # (reps, n): the last coefficient's influence
    f = np.fft.rfft(q, 2 * n, axis=1)
    ac = np.fft.irfft(f * np.conj(f), 2 * n, axis=1)[:, :n]    # sum_t q_t q_{t+j}, j = 0..n-1
    out = {}
    for b in FIXED_B["bs"]:
        M = b * n
        j = np.arange(n)
        w = np.where(j < M, 1.0 - j / M, 0.0)
        var = ac[:, 0] + 2.0 * (ac[:, 1:] * w[1:]).sum(axis=1)
        out[b] = np.abs(beta[:, -1]) / np.sqrt(np.maximum(var, 1e-300))
    return out


def fixed_b_check() -> dict:
    """The fixed-b limit of the Bartlett-kernel HAC t-test (bandwidth M = b n, i.i.d. errors, n = 1,000, 40,000
    draws): the 95th percentile of |t| for the mean (the case the published cubic is for) and for a linear trend's
    slope (the case the trend test needs), beside each version of the cubic."""
    import numpy as np
    n, reps = FIXED_B["n"], FIXED_B["reps"]
    rng = np.random.default_rng(np.random.SeedSequence(MASTER_SEED, spawn_key=(999,)))
    t = np.arange(n, dtype=float)
    designs = {"mean": np.ones((n, 1)), "trend_slope": np.column_stack([np.ones(n), (t - t.mean()) / n])}
    q = {k: {b: [] for b in FIXED_B["bs"]} for k in designs}
    for _ in range(reps // 1000):
        e = rng.standard_normal((1000, n))
        for k, X in designs.items():
            for b, v in _bartlett_t(e, X).items():
                q[k][b].append(v)
    sim = {k: {b: float(np.quantile(np.concatenate(q[k][b]), 0.95)) for b in FIXED_B["bs"]} for k in designs}
    rows = [dict({"b": b}, **{k: round(sim[k][b], 4) for k in designs},
                 **{"formula_" + f: round(sum(c * b ** i for i, c in enumerate(v["coef"])), 4)
                    for f, v in FIXED_B["formulas"].items()}) for b in FIXED_B["bs"]]
    gaps = {f: {k: round(max(abs(sim[k][b] - sum(c * b ** i for i, c in enumerate(v["coef"])))
                             for b in FIXED_B["bs"]), 4) for k in designs}
            for f, v in FIXED_B["formulas"].items()}
    tol = FIXED_B["tolerance"]
    return {"formulas": {f: {"text": v["text"], "source": v["source"]} for f, v in FIXED_B["formulas"].items()},
            "n": n, "reps": reps, "tolerance": tol, "simulated_95pct_abs_t": rows, "max_abs_gap": gaps,
            "verified": {f: {k: bool(g[k] <= tol) for k in designs} for f, g in gaps.items()},
            "used": False,
            "note": ("Neither version of the cubic could be checked against Kiefer and Vogelsang (2005) itself. "
                     "Against the simulated fixed-b limit for the mean, the recalled version is off by up to %.3f and "
                     "the secondary source's by up to %.3f; for a linear trend's slope, the case a trend test needs, "
                     "the limit is far above both (by up to %.3f and %.3f): fixed-b limits depend on the deterministic "
                     "regressors, and the cubic is not the trend case's. The engine does not use it; the bootstrap's "
                     "own size is measured in trend_size instead."
                     % (gaps["recalled"]["mean"], gaps["secondary"]["mean"], gaps["recalled"]["trend_slope"],
                        gaps["secondary"]["trend_slope"]))}


def build(cells, series, workers, with_fixed_b=True) -> dict:
    t0 = time.perf_counter()
    jobs = [(n, rho, series) for n, rho in cells]
    if workers > 1:
        with mp.get_context("spawn").Pool(workers) as pool:
            got = pool.map(_cell, jobs)
    else:
        got = [_cell(j) for j in jobs]
    _paths()
    import nl_inference as NI
    doc = {"what": "Simulated sizes of the report's trend test (engine/nl_inference.trend_test) and of the "
                   "Newey-West range it replaced, on no-trend AR(1) series; the method text quotes the nearest "
                   "cell (CONTRACT-v2.md 5.11).",
           "script": "tools/sim_trend_size.py", "script_sha256": script_sha(), "procedure_sha256": procedure_sha(),
           "master_seed": MASTER_SEED, "series_per_cell": series,
           "test": {"name": NI.TREND_NAME, "B": NI.TREND_B, "seed": NI.SEED, "alpha": NI.ALPHA,
                    "size_max": NI.SIZE_MAX, "rho_range": [NI.RHO_LO, NI.RHO_HI]},
           "trend_size": {"cells": sorted(got, key=lambda c: (c["n"], c["rho"]))}}
    if with_fixed_b:
        doc["fixed_b"] = fixed_b_check()
    doc["_seconds"] = round(time.perf_counter() - t0, 1)
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--cells", default="")
    ap.add_argument("--series", type=int, default=SERIES)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    if a.check:
        try:
            with open(OUT, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            print("FAIL: %s is missing" % OUT)
            return 1
        if doc.get("script_sha256") != script_sha():
            print("FAIL: inference_tables.json was written by another version of tools/sim_trend_size.py; re-run it")
            return 1
        if doc.get("procedure_sha256") != procedure_sha():
            print("FAIL: the trend test in engine/nl_inference.py changed since inference_tables.json measured it; "
                  "re-run tools/sim_trend_size.py")
            return 1
        want = {(n, r) for n in NS for r in RHOS}
        got = {(c["n"], c["rho"]) for c in (doc.get("trend_size") or {}).get("cells") or []}
        if want - got or doc.get("series_per_cell") != SERIES:
            print("FAIL: inference_tables.json lacks cells %s" % sorted(want - got))
            return 1
        print("PASS: inference_tables.json is this script's (sha %s)" % doc["script_sha256"][:12])
        return 0
    cells = ([(int(x.split(":")[0]), float(x.split(":")[1])) for x in a.cells.split(",") if x]
             if a.cells else [(n, r) for n in NS for r in RHOS])
    doc = build(cells, a.series, max(1, min(a.workers, 4)), with_fixed_b=not a.cells)
    for c in doc["trend_size"]["cells"]:
        print("n=%2d rho=%.1f  size %.3f (+-%.3f)  claims %.3f  Newey-West %.3f  unrestricted %.3f"
              % (c["n"], c["rho"], c["size"], c["size_mcse"], c["claims"], c["newey_west"], c["unrestricted"]))
    if doc.get("fixed_b"):
        print(json.dumps(doc["fixed_b"], indent=1))
    print("%.1f s" % doc.pop("_seconds"))
    if not a.no_write and not a.cells:
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("wrote %s" % os.path.relpath(OUT, SITE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
