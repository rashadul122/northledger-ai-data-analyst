"""The final review's zero cases (30 Sep 2026, made-up series), on the evidence rule as it stands: at least 3 separate
runs for "resumes", only the zeros the note describes read as missing (edge and undated zeros stay 0, counted apart),
and the same series for the profile and the analyses. Writes zero_attack.out.

    python tools/fixtures/review5/zero_attack.py
"""
import warnings

import numpy as np
import pandas as pd

import _here  # noqa: F401  (paths)
import _out
import nl_browser as NB


def case(title, vals, dates=None, groups=None, word="value"):
    z, ev = NB._zero_shape(np.asarray(vals, dtype=float), dates, groups)
    print("\n== %s\n   zeros=%d evidence=%s" % (title, z, None if not ev else {k: v for k, v in ev.items() if k != "mask"}))
    if ev:
        print("   NOTE: " + NB._zero_note("col", {"zeros": ev["zeros"], "word": word, "evidence": ev}))
    return z, ev


def main():
    warnings.simplefilter("ignore", FutureWarning)     # pandas' "M" month-end alias, as the review wrote it
    rng = np.random.default_rng(3)
    d = pd.date_range("2025-01-01", periods=120, freq="D")
    s = 1000 + rng.normal(0, 20, 120); s[[17, 58, 91]] = 0
    case("3 real zero sales days between steady days (typed level): 3 separate runs", s, d)
    s = 1000 + rng.normal(0, 200, 120); s[[17, 58, 91]] = 0
    case("3 zero sales days between volatile days", s, d)
    s = 1000 + rng.normal(0, 50, 120); s[d.dayofweek == 6] = 0
    case("store closed Sundays", s, d)
    p = np.full(90, 19.99); p[44] = 0
    case("price 19.99, one free-promo day (a single zero is never enough)", p, pd.date_range("2025-01-01", periods=90), word="price")
    p = np.full(90, 19.99); p[[20, 60]] = 0
    case("price 19.99, two free days (two runs are not enough)", p, pd.date_range("2025-01-01", periods=90), word="price")
    b = np.array([1000, 1020, 990, 0, 1010, 1005] + [1000 + 10 * i for i in range(30)], dtype=float)
    case("monthly balance paid off once, redrawn to a similar level", b,
         pd.date_range("2023-01-31", periods=len(b), freq="M"), word="balance")
    dd = pd.date_range("2021-01-01", periods=400, freq="D")
    fx = 1.30 + np.cumsum(rng.normal(0, 0.003, 400)); fx[dd.dayofweek >= 5] = 0
    case("FX with 0 on weekends", fx, dd, word="rate")
    fx2 = fx.copy(); fx2[[2, 3, 4]] = 0
    case("FX weekends, one holiday run of 4 (Sat to Tue): those 4 stay 0", fx2, dd, word="rate")
    e = 1.30 + rng.normal(0, 0.005, 60); e[[10, 20, 30, 40, 50]] = 0; e[-1] = 0
    case("5 interleaved zeros + one on the LAST day (the edge stays 0)", e, pd.date_range("2025-01-01", periods=60), word="rate")
    e = 1.30 + rng.normal(0, 0.005, 60); e[[10, 20, 30]] = 0
    dts = list(pd.date_range("2025-01-01", periods=60)) + [pd.NaT] * 3
    case("3 interleaved dated zeros + 3 zeros on undated rows (the undated stay 0)", list(e) + [0.0] * 3, dts, word="rate")
    g = np.repeat(["A", "B"], 60)
    dp = np.tile(pd.date_range("2025-01-01", periods=60), 2)
    v = np.concatenate([1.30 + rng.normal(0, 0.005, 60), 50 + rng.normal(0, 0.5, 60)])
    v[[10, 25, 40, 70, 85, 100]] = 0
    case("panel, read WITHOUT groups (the old profile)", v, dp)
    grp = NB._series_groups([("desk", g)], dp)
    print("\n   _series_groups picks the series column: %s" % (None if grp is None else sorted(set(grp))))
    case("panel, read within the series _series_groups finds (the profile and the analyses, now)", v, dp, grp)
    return 0


if __name__ == "__main__":
    _out.run(main, __file__)
