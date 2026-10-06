"""Inference for the report (wave 4, track A2; plan/WAVE4-A-DESIGN.md section 3, CONTRACT-v2.md section 5.11).

Four things a professor's review found the report claiming without having measured them:

T1  trend_test        a yearly trend's direction, on 8 to 200 points. The Newey-West range it replaces found a
                      "trend" in 22% to 47% of 9-year series that had none (tools/sim_trend_size.py); this test's
                      size is simulated and pinned in inference_tables.json, and the method text quotes it.
T2  interval_coverage how often a change claim's 95% interval held the true change, measured on the benchmark's
                      simulated series nearest the claim, in place of the core's "built to hold" promise.
T3  n_eff_overlapping how many of a history range's overlapping windows are independent, and the non-overlapping
                      changes beside them (nl_scenarios._history_range).
P0-13 forecast_audit  a rolling-origin back-test of the forecast the report shows, horizon by horizon, against
                      seasonal naive; and row_series_artifact, which keeps a row count that a table's layout or
                      the calendar fixes from being forecast at all.
T4  official_inference an official aggregate (a published total or level) is described, not tested: the record
                      that says so, from the publisher's own columns.

Everything here is numpy and the standard library (it runs in Pyodide, which has no scipy). Every random draw is
seeded, so one file gives one report. The core (northledger) is read, never changed.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES_FILE = "inference_tables.json"       # tools/sim_trend_size.py writes it; it ships beside this module

# --------------------------------------------------------------------------- T1: the trend test
SEED = 20261001                 # every draw of the trend test; a fixed seed makes it a function of the data alone
TREND_B = 999                   # bootstrap draws (the p-value's resolution is 1/1,000)
ALPHA = 0.05                    # the test's level
SIZE_MAX = 0.075                # a direction is claimed only where the simulated size is at most this
RHO_LO, RHO_HI = -0.5, 0.95     # the momentum the bootstrap may assume (a unit root is the random-walk screen's job)
BIAS_DRAWS = 400                # series per grid point for the bias function (common random numbers)
BIAS_GRID = 40                  # grid points of true momentum, -0.95 to 0.99
TREND_NAME = "Prais-Winsten AR(1) slope, parametric bootstrap under no trend, random-walk screen"

_BIAS_CACHE: Dict[Tuple[Any, ...], Any] = {}
_TABLES: Dict[str, Any] = {}


def _np() -> Any:
    import numpy as np
    return np


def _grid() -> Any:
    return _np().linspace(-0.95, 0.99, BIAS_GRID)


def _ar1(rng: Any, rho: Any, rows: int, length: int) -> Any:
    """`rows` stationary AR(1) paths of `length` steps with unit innovations; `rho` a number or one per row."""
    np = _np()
    r = np.broadcast_to(np.asarray(rho, dtype=float), (rows,)).copy()
    e = rng.standard_normal((rows, length))
    x = np.empty((rows, length))
    x[:, 0] = e[:, 0] / np.sqrt(1.0 - r * r)
    for i in range(1, length):
        x[:, i] = r * x[:, i - 1] + e[:, i]
    return x


def _lag1(E: Any, adj: Any) -> Any:
    """Lag-1 autocorrelation of each row of residuals E, over the adjacent pairs `adj` (consecutive years)."""
    np = _np()
    num = (E[:, 1:] * E[:, :-1])[:, adj].sum(axis=1) / max(int(adj.sum()), 1)
    den = (E * E).sum(axis=1) / E.shape[1]
    ok = den > 0
    return np.where(ok, num / np.where(ok, den, 1.0), 0.0)


def _resid(Y: Any, tc: Any, trend: bool) -> Any:
    """Least-squares residuals of each row on a constant and, with `trend`, the centred time `tc`."""
    R = Y - Y.mean(axis=1, keepdims=True)
    if trend:
        R = R - ((Y * tc).sum(axis=1) / float((tc * tc).sum()))[:, None] * tc[None, :]
    return R


def _bias(off: Any, trend: bool) -> Any:
    """The mean lag-1 autocorrelation of the residuals at each true momentum of the grid, for this design (the
    years observed) and model (with or without a trend line): g in rho_tilde = 2 rho_hat - g(rho_hat), the one
    bootstrap round of bias correction, computed once per design on fixed draws (common random numbers) so that it
    is smooth in the momentum and the same on every run."""
    np = _np()
    key = (tuple(int(x) for x in off), bool(trend))
    if key in _BIAS_CACHE:
        return _BIAS_CACHE[key]
    rng = np.random.default_rng(SEED + 1)
    length = int(off[-1]) + 1
    E = rng.standard_normal((BIAS_DRAWS, length))
    t = np.asarray(off, dtype=float)
    tc = t - t.mean()
    adj = np.diff(off) == 1
    out = []
    for r in _grid():
        x = np.empty((BIAS_DRAWS, length))
        x[:, 0] = E[:, 0] / math.sqrt(1.0 - r * r)
        for i in range(1, length):
            x[:, i] = r * x[:, i - 1] + E[:, i]
        out.append(float(_lag1(_resid(x[:, off], tc, trend), adj).mean()))
    g = np.asarray(out)
    _BIAS_CACHE[key] = g
    return g


def _momentum(Y: Any, off: Any, tc: Any, adj: Any, trend: bool) -> Any:
    """The bias-corrected lag-1 momentum of each row: from the residuals of a trend line (`trend`, what the slope is
    fitted with) or of the mean alone (no trend: what the bootstrap under the null draws from), held to
    [RHO_LO, RHO_HI]."""
    np = _np()
    rh = _lag1(_resid(Y, tc, trend), adj)
    return np.clip(2.0 * rh - np.interp(rh, _grid(), _bias(off, trend)), RHO_LO, RHO_HI)


def _prais_winsten(Y: Any, tc: Any, gaps: Any, rho: Any) -> Tuple[Any, Any, Any]:
    """(slope, its standard error, t) of each row by Prais-Winsten AR(1) GLS with that row's momentum: the first
    point scaled by sqrt(1 - rho^2), each later one quasi-differenced by rho^gap (a gap of d years has momentum
    rho^d) and rescaled so every transformed error has the same variance."""
    np = _np()
    r = np.asarray(rho, dtype=float).reshape(-1, 1)
    w0 = np.sqrt(1.0 - r * r)
    a = r ** gaps[None, :]
    s = np.sqrt((1.0 - r * r) / (1.0 - r ** (2.0 * gaps[None, :])))
    shape = (max(Y.shape[0], r.shape[0]), Y.shape[1])

    def tr(Z: Any) -> Any:
        Z = np.broadcast_to(Z, shape)
        return np.concatenate([w0 * Z[:, :1], s * (Z[:, 1:] - a * Z[:, :-1])], axis=1)
    ys, x1, x2 = tr(Y), tr(np.ones((1, Y.shape[1]))), tr(tc[None, :])
    s11, s12, s22 = (x1 * x1).sum(1), (x1 * x2).sum(1), (x2 * x2).sum(1)
    s1y, s2y = (x1 * ys).sum(1), (x2 * ys).sum(1)
    det = s11 * s22 - s12 * s12
    ok = det > 0
    det = np.where(ok, det, 1.0)
    b = (s11 * s2y - s12 * s1y) / det
    c = (s22 * s1y - s12 * s2y) / det
    res = ys - c[:, None] * x1 - b[:, None] * x2
    s2 = (res * res).sum(1) / max(Y.shape[1] - 2, 1)
    se = np.sqrt(np.maximum(s2 * s11 / det, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(ok & (se > 0), b / np.where(se > 0, se, 1.0), np.nan)
    return b, se, t


def _t_stat(Y: Any, off: Any, tc: Any, gaps: Any, adj: Any) -> Any:
    """The trend test's statistic for each row: the Prais-Winsten t of the slope, the momentum from the trend
    line's residuals (bias-corrected)."""
    return _prais_winsten(Y, tc, gaps, _momentum(Y, off, tc, adj, True))[2]


def tables() -> Dict[str, Any]:
    """inference_tables.json beside this module ({} when it is missing): the simulated sizes."""
    if "doc" not in _TABLES:
        try:
            with open(os.path.join(HERE, TABLES_FILE), encoding="utf-8") as fh:
                _TABLES["doc"] = json.load(fh)
        except (OSError, ValueError):
            _TABLES["doc"] = {}
    return _TABLES["doc"]


def size_cell(n: int, rho: float) -> Optional[Dict[str, Any]]:
    """The simulated condition nearest a series: its number of years (on a log scale), then its momentum under no
    trend. None when no table ships."""
    cells = ((tables().get("trend_size") or {}).get("cells")) or []
    if not cells:
        return None
    ns = sorted({int(c["n"]) for c in cells})
    nn = min(ns, key=lambda k: (abs(math.log(max(n, 1) / float(k))), k))
    row = [c for c in cells if int(c["n"]) == nn]
    return min(row, key=lambda c: (abs(float(c["rho"]) - float(rho)), float(c["rho"])))


def trend_test(t: Sequence[float], y: Sequence[float], B: int = TREND_B, seed: int = SEED) -> Dict[str, Any]:
    """Is there a settled direction in a short yearly series? (T1; replaces _hac_slope in _a_trend.)

    1. The slope: least squares gives residuals whose lag-1 autocorrelation, bias-corrected with one bootstrap
       round (rho~ = 2 rho^ - mean rho^*), held to [-0.5, 0.95], is the momentum of a Prais-Winsten AR(1) GLS fit;
       its slope and t are the estimate and the statistic.
    2. The p-value: a parametric bootstrap UNDER NO TREND. The bootstrap's momentum is estimated with the null
       imposed (residuals from the mean, bias-corrected the same way), and every draw repeats step 1 in full.
       The design's first version drew at the trend line's own momentum: on 1,000 no-trend series of 9 years its
       size was 9.3%, 11.6% and 14.9% at momentum 0, 0.4 and 0.8, because a series that looks like a trend leaves
       residuals with less momentum than it has. With the null imposed (Davidson and MacKinnon's restricted
       bootstrap) it was 5.0%, 4.4% and 5.1%.
    3. The 95% range: the slope plus or minus the 95th percentile of |t*| times its standard error (a symmetric
       percentile-t range from the same draws), so it excludes no change exactly when p < 0.05.
    4. The random-walk screen: the share of driftless random walks on the same years whose |t| is at least the
       observed one (t does not depend on the scale, so the walk's step size does not matter).
    5. The verdict: "rising" or "falling" only when p < 0.05, the screen's share is at most 0.05 and the simulated
       size of the nearest condition is at most 7.5%; "no_settled_direction" when p >= 0.05; else "not_graded"."""
    np = _np()
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(t, kind="mergesort")
    t, y = t[order], y[order]
    n = int(len(t))
    out: Dict[str, Any] = {"name": TREND_NAME, "n": n, "rho": None, "rho_null": None, "slope": None, "se": None,
                           "t": None, "ci": [None, None], "level": 0.95, "p": None, "p_random_walk": None,
                           "B": int(B), "seed": int(seed), "size": None, "verdict": "not_graded", "why": ""}
    if n < 3 or len(set(t.tolist())) != n or not np.all(np.isfinite(y)):
        out["why"] = "too few distinct years with a value"
        return out
    off = np.round(t - t[0]).astype(int)
    tc = t - t.mean()
    gaps = np.diff(off).astype(float)
    adj = gaps == 1
    Y = y[None, :]
    line = _resid(Y, tc, True)[0]
    spread = float(((y - y.mean()) ** 2).sum())
    if spread == 0:
        out.update(slope=0.0, se=0.0, ci=[0.0, 0.0], p=1.0, p_random_walk=1.0, verdict="no_settled_direction",
                   why="constant")
        return out
    if float((line * line).sum()) <= 1e-20 * spread:
        # the values lie exactly on a straight line: its slope is the trend, with no noise to test it against
        b = float((y * tc).sum() / (tc * tc).sum())
        out.update(slope=b, se=0.0, ci=[b, b], p=1.0 / (B + 1.0), p_random_walk=1.0 / (B + 1.0),
                   verdict="rising" if b > 0 else "falling", why="exact_line")
        return out
    rho = _momentum(Y, off, tc, adj, True)
    b, se, tt = _prais_winsten(Y, tc, gaps, rho)
    rho0 = _momentum(Y, off, tc, adj, False)
    out.update(rho=float(rho[0]), rho_null=float(rho0[0]), slope=float(b[0]), se=float(se[0]))
    t_obs = float(tt[0])
    if not math.isfinite(t_obs):
        out["why"] = "the values lie on a straight line or do not vary, so no test applies"
        return out
    out["t"] = t_obs
    length = int(off[-1]) + 1
    rng = np.random.default_rng(int(seed))
    ts = np.abs(np.nan_to_num(_t_stat(_ar1(rng, rho0[0], int(B), length)[:, off], off, tc, gaps, adj), nan=0.0))
    q = float(np.quantile(ts, 1.0 - ALPHA))
    p = (1.0 + float(np.sum(ts >= abs(t_obs)))) / (B + 1.0)
    walks = np.cumsum(rng.standard_normal((int(B), length)), axis=1)[:, off]
    tw = np.abs(np.nan_to_num(_t_stat(walks, off, tc, gaps, adj), nan=0.0))
    p_rw = (1.0 + float(np.sum(tw >= abs(t_obs)))) / (B + 1.0)
    out.update(ci=[float(b[0] - q * se[0]), float(b[0] + q * se[0])], p=p, p_random_walk=p_rw)
    cell = size_cell(n, float(rho0[0]))
    if cell is not None:
        out["size"] = {"nominal": ALPHA, "simulated": cell.get("size"), "claims": cell.get("claims"),
                       "newey_west": cell.get("newey_west"), "series": cell.get("series"),
                       "cell": {"n": int(cell["n"]), "rho": float(cell["rho"])}}
    if p >= ALPHA:
        out["verdict"], out["why"] = "no_settled_direction", "p_at_least_level"
    elif p_rw > ALPHA:
        out["why"] = "random_walk"
    elif cell is None or cell.get("size") is None or float(cell["size"]) > SIZE_MAX:
        out["why"] = "size_not_measured" if cell is None else "size_above_limit"
    else:
        out["verdict"] = "rising" if b[0] > 0 else "falling"
        out["why"] = "p_below_level"
    return out


def size_words(rec: Dict[str, Any]) -> str:
    """The method text's quote of the simulated size (AM5): the test's false-alarm rate on no-trend series like
    this one, how often it claimed a direction there, and what the Newey-West range used before found."""
    sz = (rec or {}).get("size") or {}
    cell = sz.get("cell") or {}
    if sz.get("simulated") is None or not cell:
        return ("Its size is not measured for this release (no simulated table ships with it), so no direction "
                "is claimed.")
    k = int(sz.get("series") or 0)
    return ("On %s simulated no-trend series like this one (%d years, momentum %.1f) the test's false-alarm rate "
            "at its 5%% level was %s and it claimed a direction in %s of them; the Newey-West range used before "
            "found a trend in %s."
            % (format(k, ","), int(cell["n"]), float(cell["rho"]), _pct1(sz["simulated"]),
               _pct1(sz.get("claims")), _pct1(sz.get("newey_west"))))


def _pct1(x: Any) -> str:
    try:
        v = 100.0 * float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "%.1f%%" % v


# --------------------------------------------------------------------------- T2: an interval's measured coverage
COVERAGE_NOMINAL = 0.95
COVERAGE_MIN_N = 100            # a momentum bucket of fewer simulated intervals is not quoted on its own
COVERAGE_BUCKETS = (("phi_hat < 0.5", None, 0.5), ("0.5 <= phi_hat < 0.8", 0.5, 0.8), ("phi_hat >= 0.8", 0.8, None))
COVERAGE_CELL_KEYS = ("name", "claim", "role", "gated", "months", "cv", "phi", "level", "shift", "seasonal",
                      "white_sd", "amount_sd", "trend", "outlier_months", "missing_months")


def _bucket(phi_hat: float) -> str:
    for name, lo, hi in COVERAGE_BUCKETS:
        if (lo is None or phi_hat >= lo) and (hi is None or phi_hat < hi):
            return name
    return COVERAGE_BUCKETS[-1][0]


def coverage_cells(receipt: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The benchmark's change conditions whose intervals were scored against a known true change: a constant
    planted change (no trend, no outlier months, no missing months, not a mix of moved and unmoved levels), with
    at least COVERAGE_MIN_N intervals. Fields as gate.nearest_benchmark_cell matches them."""
    out: List[Dict[str, Any]] = []
    res = (receipt or {}).get("results") or {}
    # the full receipt (northledger-core/benchmark) holds them in results.change; the browser pack's cut of it
    # (tools/pack_engine.py cut_receipt) in results.change_coverage, values unchanged
    for r in res.get("change_coverage") or res.get("change") or []:
        c = r.get("cell") or {}
        if r.get("interval_coverage") is None or int(r.get("n_with_interval") or 0) < COVERAGE_MIN_N:
            continue
        if c.get("role") not in ("null", "alt") or float(c.get("trend") or 0.0) != 0.0 \
                or int(c.get("outlier_months") or 0) or int(c.get("missing_months") or 0):
            continue
        out.append({"name": c.get("name"), "claim": c.get("claim"), "months": c.get("months"), "cv": c.get("cv"),
                    "phi": c.get("phi"), "phi_hat_mean": r.get("phi_hat_mean"), "level": c.get("level"),
                    "seasonal": c.get("seasonal"), "gated": c.get("gated"), "amount_sd": c.get("amount_sd"),
                    "n": int(r["n_with_interval"]), "coverage": float(r["interval_coverage"]),
                    "wilson95": list(r.get("interval_coverage_wilson95") or [None, None]),
                    "by_diagnostic": {k: v for k, v in (r.get("interval_coverage_by_diagnostic") or {}).items()
                                      if k in {b[0] for b in COVERAGE_BUCKETS}}})
    return out


def interval_coverage(receipt: Optional[Dict[str, Any]], claim: str, months: float, cv: float, phi_hat: float,
                      level: Optional[float], seasonal: Optional[bool]) -> Optional[Dict[str, Any]]:
    """{nominal, measured, lo, hi, n, cell, bucket}: how often the 95% interval held the true change in the
    benchmark condition nearest the claim (gate.nearest_benchmark_cell, the engine's own matching), within the
    claim's momentum bucket when that bucket holds COVERAGE_MIN_N intervals, else over the whole condition, with
    its 95% Wilson interval. None when no receipt measured this code or no condition of the claim's type was."""
    from northledger import gate as _gate
    cells = [c for c in coverage_cells(receipt) if str(c.get("claim") or "volume") == claim]
    if not cells:
        return None
    best = _gate.nearest_benchmark_cell({"cells": cells}, float(months), float(cv), float(phi_hat), level,
                                        claim=claim, seasonal=seasonal)
    if best is None:
        return None
    bucket = _bucket(float(phi_hat))
    far = _gate.benchmark_far(phi_hat, best, cells) if hasattr(_gate, "benchmark_far") else None
    if far:
        # no simulated condition is like this claim (the gate's own rule for quoting a measured rate): none is quoted
        return {"nominal": COVERAGE_NOMINAL, "measured": None, "lo": None, "hi": None, "n": None,
                "cell": str(best.get("name") or ""), "bucket": None, "claim_bucket": bucket, "unlike": far,
                "source": "benchmark receipt (results.change: interval_coverage)"}
    sub = (best.get("by_diagnostic") or {}).get(bucket) or {}
    if int(sub.get("n") or 0) >= COVERAGE_MIN_N and sub.get("coverage") is not None:
        k, n = int(sub["k"]), int(sub["n"])
        lo, hi = wilson(k, n)
        measured, used = float(sub["coverage"]), bucket
    else:
        n = int(best["n"])
        measured = float(best["coverage"])
        lo, hi = wilson(measured * n, n)
        used = "all"
    return {"nominal": COVERAGE_NOMINAL, "measured": measured, "lo": lo, "hi": hi, "n": n,
            "cell": str(best.get("name") or ""), "bucket": used, "claim_bucket": bucket,
            "source": "benchmark receipt (results.change: interval_coverage)"}


def coverage_clause(cov: Optional[Dict[str, Any]], level: Optional[float] = None) -> str:
    """The clause that replaces the core's "built to hold the true change 95% of the time; ..." (AM5)."""
    lvl = "%g%%" % round(100.0 * float(level or COVERAGE_NOMINAL), 4)
    if not cov or cov.get("measured") is None:
        return "labelled %s; coverage not measured for this kind of series" % lvl
    return ("a %s interval by construction; in the benchmark's simulated series nearest this one it held the true "
            "change %s of the time (%s to %s)" % (lvl, _pct1(cov["measured"]), _pct1(cov["lo"]), _pct1(cov["hi"])))


def coverage_sentence(cov: Optional[Dict[str, Any]], level: Optional[float] = None) -> str:
    """The story's sentence form of coverage_clause (the core's "It was built to hold ..." sentence)."""
    return "It is %s." % coverage_clause(cov, level)


# --------------------------------------------------------------------------- statistics shared below
def wilson(k: float, n: float, z: float = 1.959964) -> Tuple[Optional[float], Optional[float]]:
    """95% Wilson score interval of k/n, for a real-valued k and n (an effective count)."""
    try:
        k, n = float(k), float(n)
    except (TypeError, ValueError):
        return None, None
    if not n > 0:
        return None, None
    p = min(max(k / n, 0.0), 1.0)
    d = 1.0 + z * z / n
    c = (p + z * z / (2.0 * n)) / d
    h = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def acf(x: Sequence[float], lag: int) -> Optional[float]:
    """The lag-`lag` autocorrelation (the standard estimator: demeaned, over n times the variance)."""
    np = _np()
    v = np.asarray(x, dtype=float)
    if len(v) <= lag or lag < 1:
        return None
    d = v - v.mean()
    den = float((d * d).sum())
    if den <= 0:
        return None
    return float((d[lag:] * d[:-lag]).sum() / den)


# --------------------------------------------------------------------------- T3: overlapping windows
def n_eff_overlapping(changes: Sequence[float], k: int) -> float:
    """How many independent changes `changes` (one per window of k months, one ending each month) are worth:
    n / (1 + 2 sum of the autocorrelations at lags 1 to k-1), the sum stopped at the first negative one, at least 1.
    Overlapping k-month changes of a random walk give about n / k."""
    n = len(changes)
    if n < 3:
        return float(max(n, 1))
    s = 0.0
    for j in range(1, max(int(k), 1)):
        r = acf(changes, j)
        if r is None:
            if j == 1:
                return max(1.0, n / float(max(int(k), 1)))      # no variation at all: the overlap is all there is
            break
        if r < 0:
            break
        s += r
    return max(1.0, n / (1.0 + 2.0 * s))


def sig2(v: float) -> float:
    """v rounded to 2 significant figures."""
    if v == 0 or not math.isfinite(v):
        return v
    return float("%.2g" % v)


# --------------------------------------------------------------------------- P0-13: the forecast audit
AUDIT_ORIGINS = 23              # the last 23 forecast origins
AUDIT_MIN_HISTORY = 45          # each with at least the months the core needs to forecast at all
AUDIT_HORIZONS = (1, 3, 6, 12)
AUDIT_PASS = {"wilson_lo": 0.60, "n_eff": 8.0, "rel_mae": 1.0}      # AM5: the stricter pass rule; rel_mae AT MOST 1
AUDIT_FAIL = {"wilson_hi": 0.80, "rel_mae": 1.0}                    # fails when rel_mae is OVER 1
AUDIT_REL_TOL = 1e-9            # "no worse than seasonal naive": a ratio of 1 (up to the last bits) is not "over 1"
BENCHMARK_MODEL = "seasonal_naive"
BENCHMARK_NOTE = "the model is the seasonal-naive benchmark itself"


def _month_ix(m: Any) -> int:
    """A month as the core's month_index (year * 12 + month - 1), from that index or a YYYY-MM label."""
    if not isinstance(m, str):
        return int(m)
    s = str(m)
    return int(s[:4]) * 12 + int(s[5:7]) - 1


def _month_lab(i: int) -> str:
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def _n_eff_hits(hits: Sequence[bool], errs: Sequence[float]) -> float:
    """The hits' effective count: n (1 - r) / (1 + r), r the hits' lag-1 autocorrelation (the errors' when every
    check hit or every one missed), held to [0, 0.95]."""
    n = len(hits)
    if n < 2:
        return float(n)
    r = acf([1.0 if h else 0.0 for h in hits], 1)
    if r is None:
        r = acf(errs, 1)
    r = min(max(float(r or 0.0), 0.0), 0.95)
    return max(1.0, min(float(n), n * (1.0 - r) / (1.0 + r)))


def forecast_audit(months: Sequence[Any], values: Sequence[float], fr: Any,
                   horizons: Sequence[int] = AUDIT_HORIZONS, mode: str = "cheap",
                   origins: int = AUDIT_ORIGINS, min_history: int = AUDIT_MIN_HISTORY) -> Dict[str, Any]:
    """A rolling-origin back-test of the forecast the report shows (P0-13; the core is not changed).

    The series is trimmed as the core's run_forecast trims it (its most recent max_history months, or the months
    from fit_from on). The origins are the last `origins` months that have at least `min_history` months before
    them. At each one the shown model (fr.champion) is refitted with forecast.fit_predict on the months before it,
    and the 80% range actually shown at each horizon h (its width relative to its point in ratio mode, its offsets in
    additive mode, fr.error_mode) is applied to that refit; seasonal naive is refitted the same way. mode="full"
    re-runs the core's whole run_forecast at each origin instead (a check that the cheap audit agrees; slow).

    Per horizon: the checks held of those made; their effective count from the hits' lag-1 autocorrelation; the 95%
    Wilson interval of the coverage on that effective count; the MAE, its ratio to seasonal naive's (rel_mae) and
    MASE (the MAE over the in-sample seasonal-naive MAE before the first origin). Status: "fails" when the Wilson
    upper end is under 0.80 or rel_mae is over 1; "passes" when the Wilson lower end is at least 0.60, the effective
    count at least 8 and rel_mae is no more than 1 (no worse than seasonal naive); "unclear" otherwise. When the
    shown model IS seasonal naive its rel_mae is 1.00 by construction, so it is judged on its coverage alone
    (`benchmark_is_model`, said in the label). Overall: fails if any horizon fails, passes if every audited horizon
    passes; trusted only when it passes."""
    from northledger import forecast as _fc
    np = _np()
    cfg = dict(getattr(fr, "config", None) or {})
    mon = [_month_ix(m) for m in months]
    y = np.asarray(values, dtype=float)
    keep = int(cfg.get("max_history") or len(y))
    if len(y) > keep:
        mon, y = mon[-keep:], y[-keep:]
    if cfg.get("fit_from"):
        f0 = _month_ix(str(cfg["fit_from"]))
        idx = [i for i, m in enumerate(mon) if m >= f0]
        mon, y = [mon[i] for i in idx], y[idx]
    n = len(y)
    rec: Dict[str, Any] = {"method": "rolling origin", "mode": mode, "model": str(getattr(fr, "champion", "")),
                           "benchmark_is_model": str(getattr(fr, "champion", "")) == BENCHMARK_MODEL,
                           "origins": 0, "first": None, "last": None, "min_history": int(min_history),
                           "horizons": [], "status": "not_run", "trusted": False, "label": "", "grade_label": "",
                           "rule": "passes: Wilson lower end >= 0.6, effective checks >= 8 and MAE no worse than "
                                   "seasonal naive's; fails: Wilson upper end < 0.8 or MAE over seasonal naive's",
                           "why": ""}
    first = max(int(min_history), n - int(origins))
    org = list(range(first, n))
    if not org or not np.all(np.isfinite(y)):
        rec["why"] = ("the series has %d months; the back-test needs %d before its first check"
                      % (n, int(min_history)))
        rec["grade_label"] = "the engine's grade (no back-test: too few months)"
        return rec
    m0 = mon[0] % _fc.SEASON
    step_pos: List[List[float]] = []
    if cfg.get("step_month") and not cfg.get("fit_from"):
        for st in (cfg.get("steps") or [{"month": cfg["step_month"], "size": None}]):
            try:
                p_ = _month_ix(str(st["month"])) - mon[0]
            except (KeyError, ValueError, TypeError):
                continue
            size = st.get("size")
            if 0 < p_ < n:
                step_pos.append([int(p_), 1.0 + float(size) if size is not None and math.isfinite(float(size))
                                 and float(size) > -1.0 else 1.0])
    mcfg = dict(cfg, step_pos=step_pos) if step_pos else cfg
    nn = cfg.get("nonnegative", "auto")
    floor0 = bool(np.all(y >= 0)) if nn in ("auto", None) else bool(nn)
    fwd = list(getattr(fr, "forward", None) or [])
    hs = [int(h) for h in horizons if 1 <= int(h) <= len(fwd)]
    H = max(hs) if hs else 1
    mode_err = str(getattr(fr, "error_mode", "ratio"))
    champ = str(getattr(fr, "champion", ""))
    P = np.full((len(org), H), np.nan)
    S = np.full((len(org), H), np.nan)
    LO = np.full((len(org), H), np.nan)
    HI = np.full((len(org), H), np.nan)
    for i, k in enumerate(org):
        try:
            if mode == "full":
                sub = _fc.run_forecast(mon[:k], y[:k], dict(cfg, horizon=H))
                for j, row in enumerate(sub.forward[:H]):
                    P[i, j], LO[i, j], HI[i, j] = row["point"], row["lo80"], row["hi80"]
            else:
                p, _ = _fc.fit_predict(champ, y[:k], H, m0, mcfg)
                P[i] = np.maximum(p, 0.0) if floor0 else p
            s, _ = _fc.fit_predict("seasonal_naive", y[:k], H, m0, mcfg)
            S[i] = np.maximum(s, 0.0) if floor0 else s
        except Exception:  # noqa: BLE001 - an origin no model fits is left out of the audit
            continue
    base = y[:first]
    d12 = np.abs(base[_fc.SEASON:] - base[:-_fc.SEASON]) if len(base) > _fc.SEASON else np.array([])
    scale = float(np.mean(d12)) if len(d12) else 0.0
    if not scale > 0:
        d1 = np.abs(np.diff(base))
        scale = float(np.mean(d1)) if len(d1) else 0.0
    out_h = []
    for h in hs:
        row = fwd[h - 1]
        pt, lo, hi = float(row.get("point", float("nan"))), float(row.get("lo80", float("nan"))), \
            float(row.get("hi80", float("nan")))
        hits: List[bool] = []
        errs: List[float] = []
        ae: List[float] = []
        ae_sn: List[float] = []
        for i, k in enumerate(org):
            tg = k + h - 1
            if tg >= n or not math.isfinite(P[i, h - 1]) or not math.isfinite(S[i, h - 1]):
                continue
            pi = float(P[i, h - 1])
            if mode == "full":
                blo, bhi = float(LO[i, h - 1]), float(HI[i, h - 1])
            elif not (math.isfinite(pt) and math.isfinite(lo) and math.isfinite(hi)):
                continue
            elif mode_err == "ratio":
                if not pt > 0 or not pi > 0:
                    continue
                blo, bhi = pi * (lo / pt), pi * (hi / pt)
            else:
                blo, bhi = pi + (lo - pt), pi + (hi - pt)
            if floor0 and blo < 0:
                blo, bhi = 0.0, max(bhi, 0.0)
            if not (math.isfinite(blo) and math.isfinite(bhi)):
                continue
            act = float(y[tg])
            hits.append(bool(blo <= act <= bhi))
            errs.append(act - pi)
            ae.append(abs(act - pi))
            ae_sn.append(abs(act - float(S[i, h - 1])))
        of = len(hits)
        if not of:
            continue
        held = int(sum(hits))
        ne = _n_eff_hits(hits, errs)
        wl, wh = wilson(held / float(of) * ne, ne)
        mae = math.fsum(ae) / of
        mae_sn = math.fsum(ae_sn) / of
        rel = (mae / mae_sn) if mae_sn > 0 else None
        mase = (mae / scale) if scale > 0 else None
        if (wh is not None and wh < AUDIT_FAIL["wilson_hi"]) \
                or (rel is not None and rel > AUDIT_FAIL["rel_mae"] + AUDIT_REL_TOL):
            status = "fails"
        elif wl is not None and wl >= AUDIT_PASS["wilson_lo"] and ne >= AUDIT_PASS["n_eff"] and rel is not None \
                and rel <= AUDIT_PASS["rel_mae"] + AUDIT_REL_TOL:
            status = "passes"
        else:
            status = "unclear"
        out_h.append({"h": h, "held": held, "of": of, "n_eff": round(ne, 2), "wilson": [wl, wh], "mae": mae,
                      "mae_seasonal_naive": mae_sn, "rel_mae": rel, "mase": mase, "status": status})
    rec["horizons"] = out_h
    rec["origins"] = len(org)
    rec["first"], rec["last"] = _month_lab(mon[org[0]]), _month_lab(mon[org[-1]])
    if not out_h:
        rec["why"] = "the shown forecast has no 80% range to check"
        rec["grade_label"] = "the engine's grade (no back-test: no range was shown)"
        return rec
    st = [x["status"] for x in out_h]
    rec["status"] = "fails" if "fails" in st else "passes" if all(s == "passes" for s in st) else "unclear"
    rec["trusted"] = rec["status"] == "passes"
    rec["label"] = "back-tested: held %s" % ", ".join(
        "%d of %d at %d month%s" % (x["held"], x["of"], x["h"], "" if x["h"] == 1 else "s") for x in out_h)
    if rec["benchmark_is_model"]:
        rec["label"] += "; %s" % BENCHMARK_NOTE
    rec["grade_label"] = {"fails": "the engine's grade (not trusted: the back-test failed)",
                          "passes": "the engine's grade (the back-test passed)",
                          "unclear": "the engine's grade (the back-test neither passed nor failed it)"}[rec["status"]]
    return rec


def row_series_artifact(counts: Sequence[float], per_month: Optional[Sequence[Tuple[int, int]]] = None,
                        layout: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Why a monthly row count is no business series (P0-13), or None. `counts`: the row count of each month of the
    series; `per_month`: (rows, distinct dates) of each month in the analysis table; `layout`: the long table the
    adapter read as one column per series.

    "layout": every month holds the same number of rows (a cube holds one row per series a month; a structured
    cube's slice states the outer table's count as layout["rows_a_month"]), or the file is a long statistical table
    read one column per series (its rows a month are its dates a month);
    "calendar": one row per date in at least 95% of the months, on a daily or business-day cadence (a median of 15
    or more dates a month): the count is the calendar's days, not activity."""
    c = [float(x) for x in counts if x is not None]
    if layout and layout.get("rows_a_month"):
        # a structured cube's slice (track A1 states the outer table's rows a month on the inner run's layout)
        k = int(layout["rows_a_month"])
        return {"kind": "layout", "rows_a_month": k,
                "reason": "rows per month are fixed by the table's layout (%s a month)" % format(k, ",")}
    if layout:
        return {"kind": "layout", "rows_a_month": int(round(c[-1])) if c else None,
                "reason": "the file is a long statistical table read as one column per series, so its rows a "
                          "month are the dates its series have values for, which the table's layout fixes"}
    if len(c) >= 3 and max(c) == min(c) and c[0] > 0:
        return {"kind": "layout", "rows_a_month": int(round(c[0])),
                "reason": "rows per month are fixed by the table's layout (%s a month)" % format(int(round(c[0])), ",")}
    pm = [(int(a), int(b)) for a, b in (per_month or []) if a]
    if len(pm) >= 6:
        one = sum(1 for a, b in pm if a == b) / float(len(pm))
        med = sorted(b for _a, b in pm)[len(pm) // 2]
        if one >= 0.95 and med >= 15:
            return {"kind": "calendar", "rows_a_month": None,
                    "reason": "each row is one date, so the rows a month count the dates the file has a value "
                              "for, which the calendar sets, not activity"}
    return None


# --------------------------------------------------------------------------- T4: official aggregates
PUBLISHERS = {
    # the publisher's signature columns (a hint: engine/flag_vocab.json, track A1's vocabulary, takes over when it
    # ships beside this module)
    "statcan": {"name": "Statistics Canada", "signature": ["REF_DATE", "DGUID", "VECTOR", "COORDINATE", "STATUS"]},
    "eurostat": {"name": "Eurostat", "signature": ["TIME_PERIOD", "OBS_VALUE", "OBS_FLAG"]},
}
SIGNATURE_MIN = 3               # columns of a publisher's signature a file must hold
_SE_WORDS = re.compile(r"standard error|coefficient of variation|confidence interval|margin of error|"
                       r"low 95%|high 95%|lower bound|upper bound", re.I)


def _norm_col(c: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).lower())


def _publishers() -> Dict[str, Dict[str, Any]]:
    out = {k: dict(v) for k, v in PUBLISHERS.items()}
    try:
        with open(os.path.join(HERE, "flag_vocab.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        for k, v in (doc.get("publishers") or {}).items():
            if isinstance(v, dict) and v.get("signature"):
                out.setdefault(k, {"name": k})["signature"] = list(v["signature"])
    except (OSError, ValueError, AttributeError):
        pass
    return out


def publisher_of(header: Iterable[Any]) -> Optional[Dict[str, Any]]:
    """{key, name, columns}: the publisher whose signature columns the file's header holds (SIGNATURE_MIN or more,
    its date column among them), or None."""
    have = {_norm_col(h): str(h) for h in header}
    best = None
    for key, p in _publishers().items():
        sig = [s for s in p.get("signature") or []]
        got = [have[_norm_col(s)] for s in sig if _norm_col(s) in have]
        if len(got) >= SIGNATURE_MIN and _norm_col(sig[0]) in have and (best is None or len(got) > len(best[2])):
            best = (key, p.get("name") or key, got)
    return None if best is None else {"key": best[0], "name": best[1], "columns": best[2]}


def publishes_errors(columns: Iterable[Dict[str, Any]]) -> bool:
    """True when a column of the file names sampling-error figures (a "Statistics" dimension with "Standard error",
    "Coefficient of variation", ... among its values): a test against those errors would then be possible, and the
    published estimate is not described as a fully observed aggregate."""
    for c in columns:
        if _SE_WORDS.search(str(c.get("name") or "")):
            return True
        for v in c.get("values") or []:
            if _SE_WORDS.search(str(v)):
                return True
    return False


def official_inference(publisher: Dict[str, Any], hidden: Iterable[str], why_total: str, measure_words: str,
                       describe: Dict[str, Any], months: Optional[int], aggregation: str,
                       quality: Optional[Dict[str, int]] = None, revisions: str = "") -> Dict[str, Any]:
    """The findings[].inference record of an official aggregate (T4): how it is known to be one, the change it
    describes, and what the engine's grade is then a grade of. `hidden`: column names never to name."""
    gone = {_norm_col(h) for h in hidden}
    named = [c for c in publisher["columns"] if _norm_col(c) not in gone]
    held = len(publisher["columns"]) - len(named)
    cols = ", ".join(named) + ((" and %d column%s you withheld" % (held, "" if held == 1 else "s")) if held else "")
    how = ["publisher columns %s (%s)" % (cols, publisher["name"]), why_total, measure_words]
    what = "monthly totals" if aggregation == "sum" else "monthly averages"
    return {"mode": "official_aggregate", "publisher": publisher["key"],
            "how_known": [x for x in how if x],
            "describe": describe,
            "revisions": revisions or "the file marks no value as revised or preliminary",
            "quality": quality,
            "grade_label": "process grade: month-to-month noise in %s%s; not a test of the published %s"
                           % (("%d " % months) if months else "", what,
                              "total" if aggregation == "sum" else "figure")}
