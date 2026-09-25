#!/usr/bin/env python3
"""Forecast Lab: backtested forecasts of U.S. retail trade and food services (FRED RSAFSNA).

    python build_forecast.py            # read data/fred/*.csv, write data/forecast_lab.json
    python build_forecast.py --check    # recompute and compare with the file on disk

Inputs are the fredgraph.csv files that tools/fetch_fred.py downloads (RSAFSNA, not
seasonally adjusted, and RSAFS, seasonally adjusted). This script never touches the
network, so a build is reproducible from the files and their sha256s.

What it does, all in numpy:
  * six forecasting methods on log levels, each refit at every origin with only the data
    available at that origin;
  * a rolling-origin backtest: 24 origins, horizons 1..12, scored by MAE, MAPE and MASE;
  * the champion is the method with the lowest out-of-sample MASE, even when it is a
    simple baseline, and the page says which one won;
  * 80% bands sized from the champion's own past errors at each horizon (no in-sample
    sigma, no sqrt(h) rule), with the band's coverage checked k of n with a Wilson CI;
  * a classical seasonal decomposition of RSAFSNA, set beside the seasonal factors that
    Census's own adjustment implies (RSAFSNA / RSAFS);
  * the erratum for v1 of this page, recomputed from the data rather than typed.

Every figure the site shows about this lab is in data/forecast_lab.json, raw and as a
display string. Nothing here runs on a schedule.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))
import fetch_fred  # noqa: E402

OUT = os.path.join(HERE, "data", "forecast_lab.json")
FRED_DIR = os.path.join(HERE, "data", "fred")

SERIES_ID = "RSAFSNA"          # modelled series (not seasonally adjusted)
SA_ID = "RSAFS"                # companion: Census's seasonally adjusted version
H = 12                         # forecast horizons 1..H
N_EVAL = 24                    # evaluation origins
COVID = ("2020-03", "2021-06")  # pandemic window, handled explicitly (see method text)
POOL_START = "2012-01"         # first origin whose errors may size a band
SEAS_K = 10                    # seasonal factor = mean of the last K clean years per month
OLS_WINDOW = 120               # log-linear model: trailing window in months
OLS_ANCHOR = 3                 # ... anchored on the mean of its last 3 residuals
MASE_WINDOW = 120              # MASE scale: seasonal-naive MAE over the trailing 10 years
BAND = (0.10, 0.90)            # 80% band quantiles of past log errors
MIN_POOL = 10                  # fewer past errors than this: no band is drawn
COVERAGE_OK = (0.65, 0.95)     # gate: 80% band coverage must land in this range
MIN_COVERAGE_N = 20            # below this, coverage is "too few to judge"
CLUSTER_REPS = 10000           # bootstrap draws for the coverage interval that allows for overlap
CLUSTER_SEED = 20260923        # fixed, so the interval reproduces exactly
DECOMP_SHOW_FROM = "2016-01"   # decomposition months written for the chart
HOLT_ALPHA = tuple(round(0.1 * i, 1) for i in range(1, 10))
HOLT_BETA = (0.01, 0.05, 0.1, 0.2)
HOLT_PHI = (0.8, 0.9, 0.95, 0.98)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ----------------------------------------------------------------------------- series
class Series:
    """A monthly series with its log, calendar month and pandemic-window mask."""

    def __init__(self, obs, covid=COVID):
        self.months = [m for m, _ in obs]
        for a, b in zip(self.months, self.months[1:]):
            if add_months(a, 1) != b:
                raise ValueError("series has a gap between %s and %s" % (a, b))
        self.y = np.array([v for _, v in obs], dtype=float)
        if (self.y <= 0).any():
            raise ValueError("log models need positive values")
        self.L = np.log(self.y)
        self.n = len(self.y)
        self.idx = {m: i for i, m in enumerate(self.months)}
        self.mon = np.array([int(m[5:7]) for m in self.months])
        self.covid = np.array([covid[0] <= m <= covid[1] for m in self.months])
        cov = np.flatnonzero(self.covid)
        self.cs, self.ce = (int(cov[0]), int(cov[-1])) if len(cov) else (None, None)

    def near_covid(self, t: int, radius: int) -> bool:
        """True if month t is within `radius` months of the pandemic window."""
        return self.cs is not None and t - radius <= self.ce and t + radius >= self.cs

    def pair_ok(self, t: int, lag: int) -> bool:
        """A difference y[t] - y[t-lag] is usable only if neither end is in the window."""
        return t - lag >= 0 and not (self.covid[t] or self.covid[t - lag])


def add_months(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    z = y * 12 + (m - 1) + k
    return "%04d-%02d" % (z // 12, z % 12 + 1)


def month_label(ym: str) -> str:
    return "%s %s" % (MONTHS[int(ym[5:7]) - 1], ym[:4])


def future_months(S: Series, o: int, h: int = H) -> np.ndarray:
    return np.array([(S.mon[o] + k - 1) % 12 + 1 for k in range(1, h + 1)])


# ----------------------------------------------------------------------------- seasonality
def seasonal_indices(S: Series, o: int, k: int = SEAS_K):
    """Classical decomposition on logs using data up to o: 2x12 centred moving average for
    trend; the seasonal index of a month is the mean detrended value over its last k clean
    years. A month is not clean when its moving average touches the pandemic window."""
    L = S.L[: o + 1]
    T = len(L)
    w = np.r_[0.5, np.ones(11), 0.5] / 12.0
    trend = np.full(T, np.nan)
    for t in range(6, T - 6):
        trend[t] = float(np.dot(w, L[t - 6: t + 7]))
    d = L - trend
    s = np.zeros(12)
    used = {}
    for m in range(1, 13):
        vals = [d[t] for t in range(T) if S.mon[t] == m and not np.isnan(d[t]) and not S.near_covid(t, 6)]
        if not vals:
            raise ValueError("no clean history for calendar month %d" % m)
        take = vals[-k:]
        s[m - 1] = float(np.mean(take))
        used[MONTHS[m - 1]] = len(take)
    return s - s.mean(), trend, used


# ----------------------------------------------------------------------------- models
# Each model: f(S, o, h) -> (log forecasts for o+1..o+h, info dict). Only S.L[:o+1] may be read.

def m_naive(S, o, h=H):
    return np.full(h, S.L[o]), {}


def m_snaive(S, o, h=H):
    out = np.array([S.L[o + k - 12 * ((k - 1) // 12 + 1)] for k in range(1, h + 1)])
    return out, {}


def m_drift(S, o, h=H):
    d = [S.L[t] - S.L[t - 1] for t in range(1, o + 1) if S.pair_ok(t, 1)]
    g = float(np.mean(d))
    return S.L[o] + g * np.arange(1, h + 1), {"monthly_log_growth": g, "n_differences": len(d)}


def m_snaive_drift(S, o, h=H):
    d = [S.L[t] - S.L[t - 12] for t in range(12, o + 1) if S.pair_ok(t, 12)]
    g = float(np.mean(d))
    out = np.array([S.L[o + k - 12 * ((k - 1) // 12 + 1)] + ((k - 1) // 12 + 1) * g for k in range(1, h + 1)])
    return out, {"annual_log_growth": g, "n_differences": len(d)}


def _holt_pass(a, alpha, beta, phi, skip, reset):
    """Damped Holt in error-correction form. Pandemic months are skipped (the state is
    carried forward by its own forecast); the first month after the window resets the level."""
    lvl = a[0]
    b = float(np.mean(np.diff(a[:13])))
    sse = 0.0
    for t in range(1, len(a)):
        f = lvl + phi * b
        if skip[t]:
            lvl, b = f, phi * b
            continue
        if reset[t]:
            lvl, b = a[t], phi * b
            continue
        e = a[t] - f
        sse += e * e
        lvl = f + alpha * e
        b = phi * b + beta * e
    return lvl, b, sse


def m_holt(S, o, h=H):
    s, _trend, _ = seasonal_indices(S, o)
    a = S.L[: o + 1] - s[S.mon[: o + 1] - 1]
    skip = S.covid[: o + 1].copy()
    reset = np.zeros(o + 1, dtype=bool)
    if S.ce is not None and o > S.ce:
        reset[S.ce + 1] = True
    best = None
    for al in HOLT_ALPHA:
        for be in HOLT_BETA:
            if be > al:
                continue
            for ph in HOLT_PHI:
                lvl, b, sse = _holt_pass(a, al, be, ph, skip, reset)
                if best is None or sse < best[0]:
                    best = (sse, al, be, ph, lvl, b)
    _sse, al, be, ph, lvl, b = best
    cum = np.cumsum(ph ** np.arange(1, h + 1))
    out = lvl + cum * b + s[future_months(S, o, h) - 1]
    return out, {"alpha": al, "beta": be, "phi": ph}


def m_loglinear(S, o, h=H):
    lo = max(0, o - OLS_WINDOW + 1)
    ts = np.array([t for t in range(lo, o + 1) if not S.covid[t]])
    t0 = float(o)                      # centre time on the origin (keeps the solve well scaled)

    def design(tt, mm):
        cols = [np.ones(len(tt)), (np.asarray(tt, float) - t0) / 12.0]
        cols += [(np.asarray(mm) == m).astype(float) for m in range(2, 13)]
        return np.column_stack(cols)

    X = design(ts, S.mon[ts])
    with np.errstate(all="ignore"):     # numpy 2.0 + Accelerate can raise spurious matmul warnings
        beta, *_ = np.linalg.lstsq(X, S.L[ts], rcond=None)
        resid = S.L[ts] - X @ beta
        Xf = design(np.arange(o + 1, o + h + 1), future_months(S, o, h))
        anchor = float(np.mean(resid[-OLS_ANCHOR:]))
        out = Xf @ beta + anchor
    return out, {"annual_log_trend": float(beta[1]), "n_months": int(len(ts)), "anchor": anchor}


MODELS = [
    {"id": "naive", "kind": "baseline", "name": "Naive (last value)", "fn": m_naive,
     "how": "Every future month equals the last observed month."},
    {"id": "snaive", "kind": "baseline", "name": "Seasonal naive", "fn": m_snaive,
     "how": "Each future month equals the same month one year earlier."},
    {"id": "drift", "kind": "baseline", "name": "Drift", "fn": m_drift,
     "how": "The last value, growing at the average monthly log change of the whole history "
            "(pandemic-window changes excluded). It ignores seasonality."},
    {"id": "snaive_drift", "kind": "baseline", "name": "Seasonal naive with drift", "fn": m_snaive_drift,
     "how": "The same month one year earlier, grown by the average 12-month log change of the whole "
            "history (pandemic-window changes excluded). A seasonal random walk with drift."},
    {"id": "holt", "kind": "fitted", "name": "Damped Holt (log, deseasonalised)", "fn": m_holt,
     "how": "Seasonal factors are removed, a damped-trend exponential smoother is fitted on the log "
            "level by one-step squared error over a parameter grid, then the factors are put back."},
    {"id": "loglinear", "kind": "fitted", "name": "Log-linear trend + season", "fn": m_loglinear,
     "how": "Least squares on the log level over the last 10 years: a straight trend plus 11 month "
            "effects, then shifted so it starts from where the series actually is (the mean of the "
            "last 3 residuals)."},
]
MODEL_BY_ID = {m["id"]: m for m in MODELS}


# ----------------------------------------------------------------------------- backtest
def origin_is_clean(S: Series, o: int) -> bool:
    """An origin can be scored and can size bands only if its last 12 months (the seasonal
    lookback) are all outside the pandemic window."""
    return S.cs is None or o - 11 > S.ce or o < S.cs


def run_origins(S: Series, origins, models=MODELS):
    """Log forecasts per model: {id: array (len(origins), H)}; horizons past the data are kept."""
    out = {m["id"]: np.full((len(origins), H), np.nan) for m in models}
    info = {m["id"]: [] for m in models}
    for i, o in enumerate(origins):
        for m in models:
            f, inf = m["fn"](S, o, H)
            out[m["id"]][i] = f
            info[m["id"]].append(inf)
    return out, info


def actual_log(S: Series, origins):
    A = np.full((len(origins), H), np.nan)
    for i, o in enumerate(origins):
        for k in range(1, H + 1):
            t = o + k
            if t < S.n and not S.covid[t]:
                A[i, k - 1] = S.L[t]
    return A


def mase_scale(S: Series, o: int) -> float:
    ts = [t for t in range(max(12, o - MASE_WINDOW + 1), o + 1) if S.pair_ok(t, 12)]
    return float(np.mean(np.abs(S.y[ts] - S.y[np.array(ts) - 12])))


def score(S, origins, F, A):
    """MAE ($M), MAPE (%) and MASE by horizon and overall, over forecasts with an actual."""
    yf = np.exp(F)
    ya = np.exp(A)
    ok = ~np.isnan(A)
    scale = np.array([mase_scale(S, o) for o in origins])[:, None]
    ae = np.abs(ya - yf)
    ape = ae / ya * 100.0
    ase = ae / scale
    by_h = []
    for k in range(H):
        m = ok[:, k]
        by_h.append({"h": k + 1, "n": int(m.sum()),
                     "mae": float(ae[m, k].mean()), "mape": float(ape[m, k].mean()),
                     "mase": float(ase[m, k].mean())})
    return {"overall": {"n": int(ok.sum()), "mae": float(ae[ok].mean()), "mape": float(ape[ok].mean()),
                        "mase": float(ase[ok].mean())},
            "by_h": by_h}


def quantile(v, q):
    return float(np.quantile(np.asarray(v, float), q))   # numpy default: linear interpolation


def band_pool(pool_origins, PF, PA, o, k):
    """Past log errors at horizon k usable at origin o: the target must be observed by o."""
    vals = []
    for i, po in enumerate(pool_origins):
        if po + k <= o and not np.isnan(PA[i, k - 1]):
            vals.append(PA[i, k - 1] - PF[i, k - 1])
    return vals


def wilson(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - hw, c + hw)


def coverage_cluster_bootstrap(hits, valid, target_months, reps=CLUSTER_REPS, seed=CLUSTER_SEED):
    """95% interval for band coverage that allows for overlapping forecasts.

    Forecasts made from neighbouring origins share target months, so a single unusual month
    produces many misses at once. Resampling whole target months (every forecast of the same
    month together) with replacement keeps that dependence; the Wilson interval assumes each
    forecast is independent and is too narrow. hits/valid are (origins x horizons) arrays and
    target_months[i][k] names the month forecast i, k targets.
    """
    by_month = {}
    for i, row in enumerate(target_months):
        for k, m in enumerate(row):
            if valid[i][k]:
                kk, nn = by_month.get(m, (0, 0))
                by_month[m] = (kk + int(bool(hits[i][k])), nn + 1)
    months = sorted(by_month)
    K = np.array([by_month[m][0] for m in months], dtype=float)
    N = np.array([by_month[m][1] for m in months], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(months), size=(reps, len(months)))
    rates = K[idx].sum(1) / N[idx].sum(1)
    lo, hi = np.percentile(rates, [2.5, 97.5])
    return {"lo": rnd(float(lo), 4), "hi": rnd(float(hi), 4), "reps": int(reps), "seed": int(seed),
            "target_months": len(months), "target_months_with_misses": int(np.sum(K < N)),
            "method": ("bootstrap over target months: every forecast of the same month is resampled together "
                       "(%d draws, seed %d, 2.5th to 97.5th percentile)" % (reps, seed))}


# ----------------------------------------------------------------------------- v1 erratum
def v1_fit_forecast(obs, fit_years=15, h=H):
    """Exact replica of v1's model: OLS on LEVELS, linear trend + 11 month dummies, fit on the
    calendar years >= last_year - fit_years + 1. Returns (forecast, in-sample MAPE %, n_fit, start)."""
    last_year = int(obs[-1][0][:4])
    fit = [x for x in obs if int(x[0][:4]) >= last_year - fit_years + 1]
    yv = np.array([v for _, v in fit])
    t = np.arange(len(fit), dtype=float)
    mm = [int(m[5:7]) for m, _ in fit]
    X = np.column_stack([np.ones(len(fit)), t] + [np.array([1.0 if m == k else 0.0 for m in mm]) for k in range(2, 13)])
    with np.errstate(all="ignore"):
        beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
        resid = yv - X @ beta
        cy, cm = int(fit[-1][0][:4]), int(fit[-1][0][5:7])
        rows = []
        for k in range(1, h + 1):
            cm += 1
            if cm == 13:
                cm, cy = 1, cy + 1
            rows.append([1.0, len(fit) + k - 1] + [1.0 if cm == j else 0.0 for j in range(2, 13)])
        f = np.array(rows) @ beta
    return f, float((np.abs(resid) / yv).mean() * 100), len(fit), fit[0][0]


def v1_erratum(sa_obs):
    y = np.array([v for _, v in sa_obs])
    n = len(sa_obs)
    f, mape_in, n_fit, fit_start = v1_fit_forecast(sa_obs)
    holdouts = []
    for back in (0, 12):                      # the two most recent 12-month holdouts
        end = n - back
        train = sa_obs[: end - 12]
        act = y[end - 12: end]
        fv, _, _, _ = v1_fit_forecast(train)
        naive = np.full(12, train[-1][1])
        snaive = np.array([v for _, v in train[-12:]])
        holdouts.append({
            "first": sa_obs[end - 12][0], "last": sa_obs[end - 1][0],
            "v1_mape": float((np.abs(act - fv) / act).mean() * 100),
            "naive_mape": float((np.abs(act - naive) / act).mean() * 100),
            "snaive_mape": float((np.abs(act - snaive) / act).mean() * 100),
        })
    # the same cliff rule the lab applies to its own first step, on the SA series v1 used
    Ssa = Series(sa_obs)
    moves = [abs(Ssa.L[t] - Ssa.L[t - 1]) for t in range(max(1, Ssa.n - 120), Ssa.n) if Ssa.pair_ok(t, 1)]
    med = float(np.median(moves))
    return {
        "sa_median_monthly_move_pct": float((math.exp(med) - 1) * 100),
        "sa_cliff_limit_pct": float((math.exp(3 * med) - 1) * 100),
        "series": SA_ID, "fit_start": fit_start, "fit_end": sa_obs[-1][0], "n_fit_months": n_fit,
        "in_sample_mape": mape_in,
        "last_actual": {"month": sa_obs[-1][0], "value": float(y[-1])},
        "first_forecast": {"month": add_months(sa_obs[-1][0], 1), "value": float(f[0])},
        "first_step_pct": float((f[0] / y[-1] - 1) * 100),
        "twelfth_forecast": {"month": add_months(sa_obs[-1][0], 12), "value": float(f[-1])},
        "holdouts": holdouts,
    }


# ----------------------------------------------------------------------------- decomposition
def decomposition(S: Series, sa_obs):
    s, trend, used = seasonal_indices(S, S.n - 1)
    sa = {m: v for m, v in sa_obs}
    ours = [(math.exp(s[m - 1]) - 1) * 100 for m in range(1, 13)]
    # Census's implied factor: NSA / SA, averaged by calendar month over the same clean years
    census = []
    for m in range(1, 13):
        ts = [t for t in range(S.n) if S.mon[t] == m and S.months[t] in sa and not S.near_covid(t, 6)]
        ts = ts[-SEAS_K:]
        census.append((float(np.mean([S.y[t] / sa[S.months[t]] for t in ts])) - 1) * 100)
    start = S.idx.get(DECOMP_SHOW_FROM, 0)
    rows = []
    for t in range(start, S.n):
        tr = None if np.isnan(trend[t]) else float(math.exp(trend[t]))
        fac = (math.exp(s[S.mon[t] - 1]) - 1) * 100
        rem = None if tr is None else (S.y[t] / (tr * math.exp(s[S.mon[t] - 1])) - 1) * 100
        cen = (S.y[t] / sa[S.months[t]] - 1) * 100 if S.months[t] in sa else None
        rows.append({"month": S.months[t], "observed": float(S.y[t]), "trend": rnd(tr, 1),
                     "seasonal_pct": rnd(fac, 3), "remainder_pct": rnd(rem, 3),
                     "census_seasonal_pct": rnd(cen, 3), "in_covid_window": bool(S.covid[t])})
    peak = int(np.argmax(ours)) + 1
    trough = int(np.argmin(ours)) + 1
    gap = [abs(a - b) for a, b in zip(ours, census)]
    return {
        "method": "classical decomposition on logs (2x12 centred moving-average trend; seasonal factor = "
                  "mean detrended value of each calendar month over its last %d clean years; factors "
                  "normalised to average zero on the log scale)" % SEAS_K,
        "years_per_month_used": used,
        "factors": [{"month": MONTHS[m - 1], "ours_pct": rnd(ours[m - 1], 3), "census_implied_pct": rnd(census[m - 1], 3)}
                    for m in range(1, 13)],
        "peak_month": MONTHS[peak - 1], "peak_pct": rnd(ours[peak - 1], 3),
        "trough_month": MONTHS[trough - 1], "trough_pct": rnd(ours[trough - 1], 3),
        "max_gap_vs_census_pp": rnd(max(gap), 3), "mean_gap_vs_census_pp": rnd(float(np.mean(gap)), 3),
        "series": rows,
    }


# ----------------------------------------------------------------------------- helpers
def rnd(v, d):
    if v is None:
        return None
    return float(round(float(v), d))


def fmt_b(v_m: float) -> str:
    """US$ millions -> '$773.9B'."""
    return "$" + format(v_m / 1000.0, ",.1f") + "B"


def fmt_pct(v: float, dp: int = 2, sign: bool = False) -> str:
    s = format(v, ",.%df" % dp) + "%"
    return ("+" + s) if (sign and v >= 0) else s


def fmt_num(v: float, dp: int = 2) -> str:
    return format(v, ",.%df" % dp)


def span_text(first: str, last: str) -> str:
    months = (int(last[:4]) * 12 + int(last[5:7])) - (int(first[:4]) * 12 + int(first[5:7])) + 1
    y, m = divmod(months, 12)
    parts = []
    if y:
        parts.append("%d year%s" % (y, "" if y == 1 else "s"))
    if m:
        parts.append("%d month%s" % (m, "" if m == 1 else "s"))
    return " ".join(parts)


def source_record(sid: str, fred_dir: str = FRED_DIR):
    path = os.path.join(fred_dir, sid + ".csv")
    with open(path, "rb") as f:
        raw = f.read()
    info = fetch_fred.describe(sid, raw)
    rec = dict(fetch_fred.SERIES[sid])
    rec.update({"id": sid, "csv": "data/fred/%s.csv" % sid, "url": fetch_fred.URL % sid,
                "bytes": info["bytes"], "sha256": info["sha256"], "rows": info["rows"],
                "first": info["first"], "last": info["last"], "retrieved_at": None})
    src = os.path.join(fred_dir, "SOURCES.json")
    if os.path.exists(src):
        with open(src) as f:
            book = json.load(f).get("series", {}).get(sid, {})
        if book.get("sha256") == info["sha256"]:
            rec["retrieved_at"] = book.get("completed_at") or book.get("requested_at")
            rec["http_status"] = book.get("http_status")
            rec["last_modified_header"] = book.get("last_modified_header")
        else:
            rec["provenance_warning"] = "the CSV on disk does not match the last recorded download"
    return rec


def first_step_check(S: Series, fc1: float) -> dict:
    """Is a first forecast month (log value fc1, for the month after the last observation)
    consistent with how this calendar transition has moved before, and small once our
    seasonal factors are removed? A cliff is a seasonally adjusted first step larger than
    3x the median absolute seasonally adjusted monthly move of the last 10 years."""
    last = S.n - 1
    s_now, _, _ = seasonal_indices(S, last)
    m_last, m_next = S.mon[last], (S.mon[last] % 12) + 1
    trans = [S.L[t + 1] - S.L[t] for t in range(S.n - 1)
             if S.mon[t] == m_last and S.pair_ok(t + 1, 1)]
    trans = trans[-SEAS_K:]
    step_nsa = (math.exp(fc1 - S.L[last]) - 1) * 100
    step_sa = (math.exp((fc1 - s_now[m_next - 1]) - (S.L[last] - s_now[m_last - 1])) - 1) * 100
    sa_moves = [abs((S.L[t] - s_now[S.mon[t] - 1]) - (S.L[t - 1] - s_now[S.mon[t - 1] - 1]))
                for t in range(max(1, S.n - 120), S.n) if S.pair_ok(t, 1)]
    lo_t, hi_t = (math.exp(min(trans)) - 1) * 100, (math.exp(max(trans)) - 1) * 100
    sa_limit = (math.exp(3 * float(np.median(sa_moves))) - 1) * 100
    return {
        "from": S.months[last], "to": add_months(S.months[last], 1),
        "nsa_change_pct": rnd(step_nsa, 3),
        "same_transition_history_pct": {"min": rnd(lo_t, 3), "max": rnd(hi_t, 3), "n_years": len(trans)},
        "within_history": bool(lo_t <= step_nsa <= hi_t),
        "seasonally_adjusted_change_pct": rnd(step_sa, 3),
        "sa_cliff_limit_pct": rnd(sa_limit, 3),
        "sa_cliff_rule": "a cliff is a seasonally adjusted first step larger than 3x the median absolute "
                         "seasonally adjusted monthly move of the last 10 years",
        "no_cliff": bool(abs(step_sa) <= sa_limit),
    }


def select_as_you_go(S: Series, eval_origins, pool_origins, F_all, A_all, pos):
    """The selection effect, measured: at each scored origin pick the method with the lowest MASE
    on the errors known by then (forecasts from earlier origins whose target month was already
    observed), use that method's forecasts from this origin, and score them. Ties go to the
    alphabetically first method id. Compare with the fixed champion, which was picked with
    hindsight on all the scored origins at once."""
    ids = sorted(m["id"] for m in MODELS)
    pi = np.array([pos[po] for po in pool_origins])
    po_arr = np.array(pool_origins)
    scale = np.array([mase_scale(S, po) for po in pool_origins])[:, None]
    Ap = A_all[pi]
    target = po_arr[:, None] + np.arange(1, H + 1)[None, :]
    ase = {m: np.abs(np.exp(Ap) - np.exp(F_all[m][pi])) / scale for m in ids}
    picks, apes = [], []
    for o in eval_origins:
        known = (po_arr[:, None] < o) & (target <= o) & ~np.isnan(Ap)
        best = min((float(ase[m][known].mean()), m) for m in ids)[1]
        picks.append({"origin": S.months[o], "model": best})
        for k in range(1, H + 1):
            a = A_all[pos[o], k - 1]
            if not np.isnan(a):
                apes.append(abs(math.exp(a) - math.exp(F_all[best][pos[o], k - 1])) / math.exp(a) * 100.0)
    counts = {}
    for p_ in picks:
        counts[p_["model"]] = counts.get(p_["model"], 0) + 1
    return {"method": ("at each scored origin, the method with the lowest MASE on the errors known by then "
                       "(earlier forecasts whose target month was already observed, from %s on) is used for "
                       "that origin's 12 forecasts; ties go to the first method id" % S.months[pool_origins[0]]),
            "picks": picks, "times_picked": dict(sorted(counts.items())), "n": len(apes),
            "mape": float(np.mean(apes))}


# ----------------------------------------------------------------------------- build
def build(obs, sa_obs, sources=None, n_eval=N_EVAL):
    S = Series(obs)
    last = S.n - 1
    # evaluation origins: the last n_eval origins whose 12 horizons are all observed
    eval_origins = list(range(last - H - n_eval + 1, last - H + 1))
    for o in eval_origins:
        if not origin_is_clean(S, o) or S.covid[o + 1: o + H + 1].any():
            raise ValueError("evaluation origin %s touches the pandemic window" % S.months[o])
    # band pool: every clean origin from POOL_START up to the month before the last observation
    p0 = S.idx.get(POOL_START, 24)
    pool_origins = [o for o in range(p0, last) if origin_is_clean(S, o)]
    all_origins = sorted(set(pool_origins) | set(eval_origins) | {last})
    F_all, info_all = run_origins(S, all_origins)
    A_all = actual_log(S, all_origins)
    pos = {o: i for i, o in enumerate(all_origins)}
    ev_i = [pos[o] for o in eval_origins]
    pool_i = [pos[o] for o in pool_origins]
    Aev = A_all[ev_i]

    scores = {m["id"]: score(S, eval_origins, F_all[m["id"]][ev_i], Aev) for m in MODELS}
    ranking = sorted(scores, key=lambda k: (scores[k]["overall"]["mase"], k))
    champ = ranking[0]
    champ_m = MODEL_BY_ID[champ]

    # coverage of the champion's 80% band in the evaluation window; the band at each origin is
    # sized only from errors whose targets were already observed at that origin
    Fp, Ap = F_all[champ][pool_i], A_all[pool_i]
    hits = np.zeros((len(eval_origins), H), dtype=bool)
    valid = np.zeros((len(eval_origins), H), dtype=bool)
    origin_rows = []
    min_pool_seen = None
    for i, o in enumerate(eval_origins):
        lo_b, hi_b = [], []
        for k in range(1, H + 1):
            vals = band_pool(pool_origins, Fp, Ap, o, k)
            min_pool_seen = len(vals) if min_pool_seen is None else min(min_pool_seen, len(vals))
            f = F_all[champ][pos[o], k - 1]
            if len(vals) >= MIN_POOL:
                lo, hi = f + quantile(vals, BAND[0]), f + quantile(vals, BAND[1])
                valid[i, k - 1] = True
                hits[i, k - 1] = lo <= Aev[i, k - 1] <= hi
                lo_b.append(rnd(math.exp(lo), 1))
                hi_b.append(rnd(math.exp(hi), 1))
            else:
                lo_b.append(None)
                hi_b.append(None)
        origin_rows.append({
            "origin": S.months[o],
            "months": [add_months(S.months[o], k) for k in range(1, H + 1)],
            "actual": [rnd(math.exp(a), 1) for a in Aev[i]],
            "forecasts": {m["id"]: [rnd(math.exp(v), 1) for v in F_all[m["id"]][pos[o]]] for m in MODELS},
            "champion_lo80": lo_b, "champion_hi80": hi_b,
        })
    kc, nc = int(hits[valid].sum()), int(valid.sum())
    wl, wh = wilson(kc, nc)
    cov_by_h = []
    for k in range(H):
        kk, nn = int(hits[valid[:, k], k].sum()), int(valid[:, k].sum())
        a, b = wilson(kk, nn)
        cov_by_h.append({"h": k + 1, "k": kk, "n": nn, "rate": rnd(kk / nn, 4) if nn else None,
                         "wilson95": [rnd(a, 4), rnd(b, 4)] if nn else None})
    coverage = {"nominal": 0.8, "k": kc, "n": nc, "rate": rnd(kc / nc, 4) if nc else None,
                "wilson95": [rnd(wl, 4), rnd(wh, 4)] if nc else None,
                "min_pool_size": min_pool_seen, "by_h": cov_by_h,
                "judgeable": nc >= MIN_COVERAGE_N,
                "independence_note": ("forecasts from neighbouring origins overlap in time, so the Wilson interval is "
                                      "optimistic; cluster95 resamples whole target months instead")}
    coverage["cluster95"] = (coverage_cluster_bootstrap(hits, valid, [r_["months"] for r_ in origin_rows])
                             if nc else None)

    # final forecast from the last observation, every model, band from its own error pool
    fmonths = [add_months(S.months[last], k) for k in range(1, H + 1)]
    final = {}
    for m in MODELS:
        f = F_all[m["id"]][pos[last]]
        Fpm = F_all[m["id"]][pool_i]
        lo_b, hi_b, sizes = [], [], []
        for k in range(1, H + 1):
            vals = band_pool(pool_origins, Fpm, Ap, last, k)
            sizes.append(len(vals))
            if len(vals) >= MIN_POOL:
                lo_b.append(rnd(math.exp(f[k - 1] + quantile(vals, BAND[0])), 1))
                hi_b.append(rnd(math.exp(f[k - 1] + quantile(vals, BAND[1])), 1))
            else:
                lo_b.append(None)
                hi_b.append(None)
        final[m["id"]] = {"point": [rnd(math.exp(v), 1) for v in f], "lo80": lo_b, "hi80": hi_b,
                          "pool_sizes": sizes,
                          "params": {k: (rnd(v, 6) if isinstance(v, float) else v)
                                     for k, v in info_all[m["id"]][pos[last]].items()}}

    first_step = first_step_check(S, F_all[champ][pos[last], 0])

    sel = select_as_you_go(S, eval_origins, pool_origins, F_all, A_all, pos)
    sel["fixed_champion"] = champ
    sel["fixed_champion_mape"] = scores[champ]["overall"]["mape"]
    sel["selection_effect_pp"] = rnd(sel["mape"] - sel["fixed_champion_mape"], 4)
    sel["mape"] = rnd(sel["mape"], 4)
    sel["fixed_champion_mape"] = rnd(sel["fixed_champion_mape"], 4)

    # gate, in the engine's words
    snaive_mase = scores["snaive"]["overall"]["mase"]
    beats_snaive = champ == "snaive" or scores[champ]["overall"]["mase"] < snaive_mase
    cov_ok = coverage["judgeable"] and COVERAGE_OK[0] <= (coverage["rate"] or 0) <= COVERAGE_OK[1]
    if not coverage["judgeable"]:
        verdict, reason = "WATCH", "too few checked forecasts to judge the band"
    elif not cov_ok:
        verdict, reason = "WATCH", ("the 80%% band held %d of %d times, outside %d%% to %d%%"
                                    % (kc, nc, round(COVERAGE_OK[0] * 100), round(COVERAGE_OK[1] * 100)))
    elif not first_step["no_cliff"]:
        verdict, reason = "WATCH", "the first forecast month jumps more than the series ever does"
    else:
        verdict, reason = "RECOMMEND", "champion chosen out of sample; its 80% band has not been shown to be off"
    gate = {"verdict": verdict, "reason": reason, "champion_beats_seasonal_naive": bool(beats_snaive),
            "champion_beats_naive": bool(scores[champ]["overall"]["mase"] < scores["naive"]["overall"]["mase"]),
            "coverage_in_range": bool(cov_ok), "coverage_range": list(COVERAGE_OK),
            "champion_is_baseline": champ_m["kind"] == "baseline"}

    erratum = v1_erratum(sa_obs)
    decomp = decomposition(S, sa_obs)

    # KPIs for the brief: level and month-over-month on the SA series, year-over-year on both
    sa_y = np.array([v for _, v in sa_obs])
    kpis = {
        "month": sa_obs[-1][0],
        "sa_value": float(sa_y[-1]),
        "sa_mom_pct": float((sa_y[-1] / sa_y[-2] - 1) * 100),
        "sa_yoy_pct": float((sa_y[-1] / sa_y[-13] - 1) * 100),
        "nsa_value": float(S.y[last]),
        "nsa_yoy_pct": float((S.y[last] / S.y[last - 12] - 1) * 100),
    }

    lab = {
        "schema": "forecast_lab/1",
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "built_by": "build_forecast.py",
        "schedule": None,
        "series": (sources or {}).get(SERIES_ID),
        "companion_series": (sources or {}).get(SA_ID),
        "data": {"first": S.months[0], "last": S.months[last], "n_months": S.n,
                 "span": span_text(S.months[0], S.months[last])},
        "covid_window": {"start": COVID[0], "end": COVID[1], "n_months": int(S.covid.sum())},
        "models": [{"id": m["id"], "kind": m["kind"], "name": m["name"], "how": m["how"]} for m in MODELS],
        "backtest": {
            "design": {"n_origins": len(eval_origins), "first_origin": S.months[eval_origins[0]],
                       "last_origin": S.months[eval_origins[-1]], "horizons": H,
                       "forecasts_per_model": len(eval_origins) * H, "refit_every_origin": True,
                       "band_pool_first_origin": S.months[pool_origins[0]], "band_pool_origins": len(pool_origins),
                       "band_quantiles": list(BAND), "min_pool": MIN_POOL,
                       "mase_scale": "mean absolute 12-month difference over the %d months before each origin, "
                                     "pairs touching the pandemic window excluded" % MASE_WINDOW},
            "scores": {k: {"overall": {kk: (rnd(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v["overall"].items()},
                           "by_h": [{kk: (rnd(vv, 4) if isinstance(vv, float) else vv) for kk, vv in r.items()}
                                    for r in v["by_h"]]}
                       for k, v in scores.items()},
            "ranking": ranking,
            "champion": champ,
            "coverage": coverage,
            "realtime_selection": sel,
            "gate": gate,
            "origins": origin_rows,
        },
        "forecast": {"origin": S.months[last], "months": fmonths, "champion": champ, "by_model": final,
                     "first_step": first_step},
        "decomposition": decomp,
        "erratum_v1": erratum,
        "kpis": kpis,
    }
    lab["display"] = display_strings(lab)
    lab["method"], lab["limits"] = method_text(lab), limits_text(lab)
    lab["erratum_text"] = erratum_text(lab)
    lab["reproduce"] = {"commands": ["python tools/fetch_fred.py", "python build_forecast.py",
                                     "python build_forecast.py --check"],
                        "inputs": [{"path": s["csv"], "sha256": s["sha256"]}
                                   for s in (lab["series"], lab["companion_series"]) if s]}
    return lab


def display_strings(lab):
    """Every figure the page shows, formatted once here so the page and the check agree."""
    bt, fc, er, kp, dc = lab["backtest"], lab["forecast"], lab["erratum_v1"], lab["kpis"], lab["decomposition"]
    champ = bt["champion"]
    sc = bt["scores"]
    cov = bt["coverage"]
    f0 = fc["by_model"][champ]

    def b_or_na(v):
        return fmt_b(v) if v is not None else "n/a"

    d = {
        "series_label": (lab["series"] or {}).get("label", "U.S. retail trade and food services"),
        "data_first_month": month_label(lab["data"]["first"]),
        "data_last_month": month_label(lab["data"]["last"]),
        "data_span": lab["data"]["span"],
        "data_n_months": format(lab["data"]["n_months"], ","),
        "covid_window": "%s to %s" % (month_label(lab["covid_window"]["start"]), month_label(lab["covid_window"]["end"])),
        "backtest_origins": str(bt["design"]["n_origins"]),
        "backtest_first_origin": month_label(bt["design"]["first_origin"]),
        "backtest_last_origin": month_label(bt["design"]["last_origin"]),
        "backtest_forecasts": format(bt["design"]["forecasts_per_model"], ","),
        "champion_name": MODEL_BY_ID[champ]["name"],
        "champion_kind": MODEL_BY_ID[champ]["kind"],
        "champion_mape": fmt_pct(sc[champ]["overall"]["mape"], 2),
        "champion_mase": fmt_num(sc[champ]["overall"]["mase"], 2),
        "gate_verdict": bt["gate"]["verdict"],
        "gate_reason": bt["gate"]["reason"],
        "coverage_k_of_n": "%d of %d" % (cov["k"], cov["n"]),
        "coverage_pct": fmt_pct(100 * cov["rate"], 1) if cov["rate"] is not None else "n/a",
        "coverage_ci": ("%s to %s" % (fmt_pct(100 * cov["wilson95"][0], 1), fmt_pct(100 * cov["wilson95"][1], 1))
                        if cov["wilson95"] else "n/a"),
        "coverage_ci_cluster": ("%s to %s" % (fmt_pct(100 * cov["cluster95"]["lo"], 1), fmt_pct(100 * cov["cluster95"]["hi"], 1))
                                if cov.get("cluster95") else "n/a"),
        "coverage_miss_months": ("%d of %d" % (cov["cluster95"]["target_months_with_misses"], cov["cluster95"]["target_months"])
                                 if cov.get("cluster95") else "n/a"),
        "realtime_selection_mape": fmt_pct(bt["realtime_selection"]["mape"], 2),
        "selection_effect": fmt_num(bt["realtime_selection"]["selection_effect_pp"], 2) + " points",
        "forecast_first_month": month_label(fc["months"][0]),
        "forecast_last_month": month_label(fc["months"][-1]),
        "forecast_first_value": fmt_b(f0["point"][0]),
        "forecast_first_lo80": b_or_na(f0["lo80"][0]),
        "forecast_first_hi80": b_or_na(f0["hi80"][0]),
        "forecast_last_value": fmt_b(f0["point"][-1]),
        "forecast_last_lo80": b_or_na(f0["lo80"][-1]),
        "forecast_last_hi80": b_or_na(f0["hi80"][-1]),
        "first_step_nsa": fmt_pct(fc["first_step"]["nsa_change_pct"], 1, sign=True),
        "first_step_sa": fmt_pct(fc["first_step"]["seasonally_adjusted_change_pct"], 2, sign=True),
        "seasonal_peak": "%s %s" % (dc["peak_month"], fmt_pct(dc["peak_pct"], 1, sign=True)),
        "seasonal_trough": "%s %s" % (dc["trough_month"], fmt_pct(dc["trough_pct"], 1, sign=True)),
        "seasonal_max_gap_vs_census": fmt_num(dc["max_gap_vs_census_pp"], 1) + " pp",
        "kpi_month": month_label(kp["month"]),
        "kpi_sa_value": fmt_b(kp["sa_value"]),
        "kpi_sa_mom": fmt_pct(kp["sa_mom_pct"], 2, sign=True),
        "kpi_sa_yoy": fmt_pct(kp["sa_yoy_pct"], 2, sign=True),
        "kpi_nsa_value": fmt_b(kp["nsa_value"]),
        "kpi_nsa_yoy": fmt_pct(kp["nsa_yoy_pct"], 2, sign=True),
        "v1_in_sample_mape": fmt_pct(er["in_sample_mape"], 2),
        "v1_fit_months": str(er["n_fit_months"]),
        "v1_fit_span": span_text(er["fit_start"], er["fit_end"]),
        "v1_first_step": fmt_pct(er["first_step_pct"], 2, sign=True),
        "v1_last_actual": fmt_b(er["last_actual"]["value"]),
        "v1_first_forecast": fmt_b(er["first_forecast"]["value"]),
        "v1_sa_typical_move": fmt_pct(er["sa_median_monthly_move_pct"], 2),
    }
    for m in lab["models"]:
        d["mape_" + m["id"]] = fmt_pct(sc[m["id"]]["overall"]["mape"], 2)
        d["mase_" + m["id"]] = fmt_num(sc[m["id"]]["overall"]["mase"], 2)
    for i, hd in enumerate(er["holdouts"]):
        tag = "recent" if i == 0 else "prior"
        d["v1_holdout_%s_window" % tag] = "%s to %s" % (month_label(hd["first"]), month_label(hd["last"]))
        d["v1_holdout_%s_mape" % tag] = fmt_pct(hd["v1_mape"], 2)
        d["v1_holdout_%s_naive_mape" % tag] = fmt_pct(hd["naive_mape"], 2)
        d["v1_holdout_%s_snaive_mape" % tag] = fmt_pct(hd["snaive_mape"], 2)
    return d


def method_text(lab):
    d, bt = lab["display"], lab["backtest"]
    fitted_best = min((m for m in lab["models"] if m["kind"] == "fitted"),
                      key=lambda m: bt["scores"][m["id"]]["overall"]["mase"])
    if bt["gate"]["champion_is_baseline"]:
        won = ("A simple baseline won: %s beat both fitted models out of sample, so the forecast below uses it."
               % d["champion_name"])
    else:
        won = "%s won out of sample, ahead of every baseline." % d["champion_name"]
    return [
        "Data: FRED series %s (%s, not seasonally adjusted, millions of dollars), %s monthly values from %s to %s."
        % (SERIES_ID, d["series_label"], d["data_n_months"], d["data_first_month"], d["data_last_month"]),
        "Pandemic months (%s) are treated as missing whenever a model estimates anything: averages skip "
        "changes that touch them, the log-linear fit leaves them out, and the Holt smoother carries its state "
        "across them and restarts its level on the first month after. No scored forecast starts from, looks "
        "back into, or lands in that window." % d["covid_window"],
        "Six methods, all on the log of sales and all refit at every origin using only the data available at "
        "that point: naive, seasonal naive, drift, seasonal naive with drift, damped Holt and a log-linear trend "
        "plus month effects.",
        "Rolling-origin backtest: %s origins (%s to %s), each forecasting 1 to 12 months ahead, so %s "
        "forecasts per method, each compared with what was later reported. Methods are ranked by MASE "
        "(error relative to a seasonal-naive yardstick); MAPE is shown beside it."
        % (d["backtest_origins"], d["backtest_first_origin"], d["backtest_last_origin"], d["backtest_forecasts"]),
        "%s It scored MAPE %s (MASE %s), against seasonal naive %s and naive %s. The best fitted model, %s, "
        "scored %s." % (won, d["champion_mape"], d["champion_mase"], d["mape_snaive"], d["mape_naive"],
                        fitted_best["name"], d["mape_" + fitted_best["id"]]),
        "The 80%% band comes from the champion's own past errors at the same horizon: the 10th and 90th "
        "percentiles of its log errors on every checkable forecast since %s, pandemic-touching ones excluded. "
        "It is not an in-sample standard error. Checked the same way in the backtest (each band built only from "
        "errors known at the time), it held %s times (%s; 95%% interval %s)."
        % (month_label(bt["design"]["band_pool_first_origin"]), d["coverage_k_of_n"], d["coverage_pct"],
           d["coverage_ci"]),
        "Seasonal decomposition: %s. The strongest month is %s and the weakest is %s against trend; the largest "
        "gap to the factors implied by Census's own adjustment (RSAFSNA / RSAFS) is %s."
        % (lab["decomposition"]["method"], d["seasonal_peak"], d["seasonal_trough"], d["seasonal_max_gap_vs_census"]),
    ]


def erratum_text(lab):
    """The correction to v1 of this page, worded from the recomputed numbers."""
    d, er = lab["display"], lab["erratum_v1"]
    h0, h1 = er["holdouts"][0], er["holdouts"][1]
    lost = h0["v1_mape"] > h0["naive_mape"] and h1["v1_mape"] > h1["naive_mape"]
    cliff = abs(er["first_step_pct"]) > er["sa_cliff_limit_pct"]
    verdict = ("so it lost to the naive baseline both times" if lost else
               "so it did not beat the naive baseline both times" if (h0["v1_mape"] > h0["naive_mape"] or
                                                                        h1["v1_mape"] > h1["naive_mape"])
               else "so it beat the naive baseline both times")
    return [
        "Correction to v1 of this page: it described FRED series RSAFS as retail sales excluding food services, "
        "not seasonally adjusted. RSAFS is retail trade and food services, seasonally adjusted, so v1's month "
        "effects were fitted to a series that had already had its seasonality removed.",
        "v1 fitted a straight-line trend plus month effects to dollar levels over %s months (%s). The %s "
        "error it showed was measured on the same months it was fitted to. Re-run as a forecast on the two most "
        "recent 12-month holdouts, it missed by %s (%s) and %s (%s), against %s and %s for simply repeating the "
        "last value, %s."
        % (d["v1_fit_months"], d["v1_fit_span"], d["v1_in_sample_mape"], d["v1_holdout_recent_mape"],
           d["v1_holdout_recent_window"], d["v1_holdout_prior_mape"], d["v1_holdout_prior_window"],
           d["v1_holdout_recent_naive_mape"], d["v1_holdout_prior_naive_mape"], verdict),
        "Its first forecast month (%s) sat %s away from the last actual (%s). The seasonally adjusted series "
        "typically moves %s a month (median over the last 10 years), so %s. The Forecast Lab replaces it."
        % (d["v1_first_forecast"], d["v1_first_step"], d["v1_last_actual"], d["v1_sa_typical_move"],
           "that first step was a cliff, more than three typical months in one" if cliff else
           "that first step was within three typical months"),
    ]


def limits_text(lab):
    d = lab["display"]
    return [
        "The backtest uses today's revised history. A planner at each origin saw the advance estimates, "
        "which Census later revises, so real-world accuracy at the time would likely have been somewhat worse.",
        "Trading-day and holiday effects (how many weekends a month has, when Easter falls) are not modelled; "
        "Census's seasonal adjustment accounts for them.",
        "Values are nominal dollars, not adjusted for inflation.",
        "The %s scored origins cover one period (%s to %s). The %s scored forecasts overlap in time, so they "
        "are not independent: the band's misses fall in %s target months, and resampling whole target months puts "
        "the coverage at about %s, wider than the Wilson interval (%s)."
        % (d["backtest_origins"], d["backtest_first_origin"], d["backtest_last_origin"], d["backtest_forecasts"],
           d["coverage_miss_months"], d["coverage_ci_cluster"], d["coverage_ci"]),
        "The champion was picked on these same origins, which flatters it: re-picked at each origin from only the "
        "errors known by then, the forecasts scored MAPE %s, against %s for the champion chosen on all %s origins "
        "at once." % (d["realtime_selection_mape"], d["champion_mape"], d["backtest_origins"]),
        "The point forecast is the exponential of a log forecast, so it sits near the median rather than the mean.",
        "Built from a one-off download recorded in data/fred/SOURCES.json; nothing refreshes it on a schedule. "
        "Not investment advice.",
    ]


# ----------------------------------------------------------------------------- io
VOLATILE = ("generated_at",)


def canonical(lab):
    return {k: v for k, v in lab.items() if k not in VOLATILE}


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".forecast_lab.")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, allow_nan=False)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def load_inputs(fred_dir=FRED_DIR):
    return fetch_fred.load(SERIES_ID, fred_dir), fetch_fred.load(SA_ID, fred_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build data/forecast_lab.json from the FRED CSVs.")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--fred-dir", default=FRED_DIR)
    ap.add_argument("--check", action="store_true", help="recompute and compare with --out; write nothing")
    a = ap.parse_args(argv)
    obs, sa = load_inputs(a.fred_dir)
    sources = {sid: source_record(sid, a.fred_dir) for sid in (SERIES_ID, SA_ID)}
    lab = build(obs, sa, sources)
    if a.check:
        if not os.path.exists(a.out):
            print("FORECAST LAB CHECK: FAIL - %s does not exist; run build_forecast.py" % a.out)
            return 1
        with open(a.out) as f:
            old = json.load(f)
        new = json.loads(json.dumps(lab, allow_nan=False))
        if canonical(old) != canonical(new):
            diff = sorted(k for k in set(old) | set(new) if k not in VOLATILE and old.get(k) != new.get(k))
            print("FORECAST LAB CHECK: FAIL - %s differs from a fresh build in: %s" % (a.out, ", ".join(diff)))
            return 1
        print("FORECAST LAB CHECK: PASS - %s reproduces from %s" % (
            os.path.basename(a.out), ", ".join(i["path"] for i in lab["reproduce"]["inputs"])))
        return 0
    write_json(a.out, lab)
    d = lab["display"]
    print("wrote %s" % a.out)
    print("data %s to %s (%s months); backtest %s origins x 12 horizons" % (
        d["data_first_month"], d["data_last_month"], d["data_n_months"], d["backtest_origins"]))
    for mid in lab["backtest"]["ranking"]:
        s = lab["backtest"]["scores"][mid]["overall"]
        print("  %-13s MASE %.3f  MAPE %.2f%%  MAE $%.0fM" % (mid, s["mase"], s["mape"], s["mae"]))
    print("champion: %s (%s); gate %s: %s" % (d["champion_name"], d["champion_kind"], d["gate_verdict"],
                                             lab["backtest"]["gate"]["reason"]))
    print("80%% band coverage %s (%s, 95%% CI %s)" % (d["coverage_k_of_n"], d["coverage_pct"], d["coverage_ci"]))
    print("first forecast %s %s [%s, %s]; SA first step %s; no_cliff=%s" % (
        d["forecast_first_month"], d["forecast_first_value"], d["forecast_first_lo80"], d["forecast_first_hi80"],
        d["first_step_sa"], lab["forecast"]["first_step"]["no_cliff"]))
    print("v1 erratum: in-sample %s; holdouts %s / %s vs naive %s / %s; first step %s" % (
        d["v1_in_sample_mape"], d["v1_holdout_recent_mape"], d["v1_holdout_prior_mape"],
        d["v1_holdout_recent_naive_mape"], d["v1_holdout_prior_naive_mape"], d["v1_first_step"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
