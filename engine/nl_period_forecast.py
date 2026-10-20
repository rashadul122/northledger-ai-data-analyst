"""
nl_period_forecast: a forecast for a yearly or quarterly series, back-tested on its own past (AI Analyst v2 phase F, 20 Oct 2026).

The core's forecast (northledger/forecast.py) reads monthly series only, so a FRED annual table had "no forecast". This module
gives such a series a forecast a reader can judge, with nothing the engine cannot show:

  * models: naive (the last value), drift (the last value plus the average step), damped trend (Holt's linear trend with a damped
    slope, its smoothing chosen on the training years), and for quarters a seasonal naive (the same quarter a year earlier);
  * a rolling-origin back-test: from origin MIN_TRAIN on, each model is fit on the periods before the origin and forecasts H
    periods ahead; the model with the lowest mean absolute error over the horizons is chosen, and its skill against naive is said;
  * an 80% band at each horizon from the empirical quantile of the chosen model's back-test errors at that horizon (no normal
    assumption), and only with at least MIN_ERRORS of them;
  * an out-of-sample audit of the band at one period ahead: each replay's band built only from the errors before it, so "held k of
    n" is how often the band held the value it had not seen. The forecast is trusted only when n >= MIN_AUDIT and the share held is
    within TRUST_LO..TRUST_HI of the 80% aimed at.

    forecast(periods, values, step) -> {"ok", "grain", "model", "points": [{period, value, lo, hi}], "audit", "trusted", ...}
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

LEVEL = 0.80
HORIZON = {12: 3, 3: 4}          # periods ahead: 3 years, 4 quarters
MIN_N = {12: 15, 3: 20}          # periods of history before any forecast
MIN_TRAIN = {12: 8, 3: 12}
MIN_ERRORS = 8
MIN_AUDIT = 10
TRUST_LO, TRUST_HI = 0.65, 0.95


def _naive(y: Sequence[float], h: int, season: int) -> List[float]:
    return [y[-1]] * h


def _drift(y: Sequence[float], h: int, season: int) -> List[float]:
    if len(y) < 2:
        return _naive(y, h, season)
    d = (y[-1] - y[0]) / (len(y) - 1)
    return [y[-1] + d * (k + 1) for k in range(h)]


def _seasonal_naive(y: Sequence[float], h: int, season: int) -> List[float]:
    if season <= 1 or len(y) < season:
        return _naive(y, h, season)
    return [y[len(y) - season + (k % season)] for k in range(h)]


def _damped(y: Sequence[float], h: int, season: int) -> List[float]:
    """Holt's linear trend with a damped slope (phi 0.9); alpha and beta by the least one-step squared error on a small grid."""
    if len(y) < 4:
        return _drift(y, h, season)
    phi = 0.9
    best: Optional[Tuple[float, float, float]] = None
    for a in (0.2, 0.4, 0.6, 0.8, 1.0):
        for b in (0.05, 0.1, 0.2, 0.4):
            lvl, tr, sse = y[0], y[1] - y[0], 0.0
            for t in range(1, len(y)):
                f = lvl + phi * tr
                sse += (y[t] - f) ** 2
                nl = a * y[t] + (1 - a) * (lvl + phi * tr)
                tr = b * (nl - lvl) + (1 - b) * phi * tr
                lvl = nl
            if best is None or sse < best[0]:
                best = (sse, a, b)
    _, a, b = best
    lvl, tr = y[0], y[1] - y[0]
    for t in range(1, len(y)):
        nl = a * y[t] + (1 - a) * (lvl + phi * tr)
        tr = b * (nl - lvl) + (1 - b) * phi * tr
        lvl = nl
    out, damp = [], 0.0
    for k in range(h):
        damp += phi ** (k + 1)
        out.append(lvl + damp * tr)
    return out


MODELS: Dict[str, Callable[[Sequence[float], int, int], List[float]]] = {
    "naive": _naive, "drift": _drift, "damped trend": _damped, "seasonal naive": _seasonal_naive}
MODEL_WORDS = {"naive": "the last value carried forward", "drift": "the last value plus the series' average step",
               "damped trend": "a trend that flattens as it goes (damped Holt)",
               "seasonal naive": "the same quarter a year earlier"}


def _quantile(xs: List[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    pos = q * (len(s) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _next_period(p: str, step: int, k: int) -> str:
    if step == 12:
        return str(int(p[:4]) + k)
    y, q = int(p[:4]), int(p[-1])
    n = (y * 4 + q - 1) + k
    return "%d-Q%d" % (n // 4, n % 4 + 1)


def forecast(periods: Sequence[str], values: Sequence[Optional[float]], step: int) -> Dict[str, Any]:
    """periods: "YYYY" (step 12) or "YYYY-Qn" (step 3), in time order with no gap; values their figures (None ends the use)."""
    if step not in HORIZON:
        return {"ok": False, "why": "the forecast reads yearly or quarterly series here"}
    pairs = [(str(p), float(v)) for p, v in zip(periods, values) if v is not None and math.isfinite(float(v))]
    if len(pairs) < MIN_N[step]:
        return {"ok": False, "why": "it needs %d %s of history and the series has %d" % (MIN_N[step], "years" if step == 12 else "quarters", len(pairs))}
    ps, y = [p for p, _ in pairs], [v for _, v in pairs]
    n, H, season = len(y), HORIZON[step], 4 if step == 3 else 1
    names = [m for m in MODELS if m != "seasonal naive" or step == 3]
    errs: Dict[str, List[List[float]]] = {m: [[] for _ in range(H)] for m in names}
    for t in range(MIN_TRAIN[step], n):
        for m in names:
            f = MODELS[m](y[:t], H, season)
            for k in range(H):
                if t + k < n:
                    errs[m][k].append(y[t + k] - f[k])
    mae = {m: sum(sum(abs(e) for e in es) / len(es) for es in errs[m] if es) / max(1, sum(1 for es in errs[m] if es)) for m in names}
    best = min(names, key=lambda m: (mae[m], m != "naive"))      # a tie keeps the simplest
    fc = MODELS[best](y, H, season)
    pts = []
    for k in range(H):
        e = errs[best][k]
        lo = hi = None
        if len(e) >= MIN_ERRORS:
            lo, hi = fc[k] + _quantile(e, (1 - LEVEL) / 2), fc[k] + _quantile(e, (1 + LEVEL) / 2)
        pts.append({"period": _next_period(ps[-1], step, k + 1), "value": fc[k], "lo": lo, "hi": hi})
    # the band's out-of-sample audit at one period ahead: each replay's band from the errors before it only
    e1 = errs[best][0]
    held = tried = 0
    for i in range(MIN_ERRORS, len(e1)):
        past = e1[:i]
        a, b = _quantile(past, (1 - LEVEL) / 2), _quantile(past, (1 + LEVEL) / 2)
        tried += 1
        held += a <= e1[i] <= b
    share = held / tried if tried else None
    trusted = bool(tried >= MIN_AUDIT and share is not None and TRUST_LO <= share <= TRUST_HI and pts[0]["lo"] is not None)
    noun = "year" if step == 12 else "quarter"
    skill = 1 - mae[best] / mae["naive"] if mae.get("naive") else None
    return {"ok": True, "grain": noun, "model": best, "model_words": MODEL_WORDS[best], "level": int(LEVEL * 100),
            "points": pts, "last_period": ps[-1], "last_value": y[-1], "history_n": n,
            "mae": {m: mae[m] for m in names}, "skill_vs_naive": skill,
            "audit": {"held": held, "tried": tried, "share": share,
                      "label": "back-tested: the %d%% band held %d of %d at 1 %s ahead" % (int(LEVEL * 100), held, tried, noun)},
            "trusted": trusted,
            "note": ("Chosen by a rolling back-test over the series' own past: %s. The band is how far the back-test's errors ran, "
                     "not a promise; it does not cover a break the past never showed (a war, a policy change, a revision)."
                     % MODEL_WORDS[best])}


WHAT_IF_MAX = 3


def what_if(fc: Dict[str, Any], items: Any) -> List[Dict[str, Any]]:
    """The AI's what-ifs on the engine's forecast: each a steady change per period (percent, -20..20) compounded on the forecast path,
    period by period. Arithmetic on the forecast, never a forecast of its own: it says what the path would be under the assumption."""
    out: List[Dict[str, Any]] = []
    if not fc.get("ok") or not isinstance(items, list):
        return out
    for w in items[:WHAT_IF_MAX]:
        if not isinstance(w, dict):
            continue
        try:
            pct = float(w.get("per_period_pct"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pct) or pct == 0 or abs(pct) > 20 or not isinstance(w.get("label"), str):
            continue
        pts = [{"period": q["period"], "value": q["value"] * (1 + pct / 100.0) ** (k + 1), "base": q["value"]} for k, q in enumerate(fc["points"])]
        out.append({"label": w["label"][:120], "per_period_pct": pct, "points": pts})
    return out
