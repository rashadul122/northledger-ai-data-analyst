#!/usr/bin/env python3
"""Tests for engine/nl_period_forecast.py (AI Analyst v2 phase F, 20 Oct 2026): a yearly or quarterly series' own forecast.

  * a trending yearly series: a trend model beats carrying the last value forward, and the 80% band's out-of-sample audit holds
    near 80% (trusted);
  * a seasonal quarterly series: the same quarter a year earlier wins;
  * too short a series: no forecast, and why;
  * the planner's what-ifs: a steady change per period compounded on the forecast path, and nothing outside -20..20;
  * the engine run: a FRED-style annual file gets rep.period_forecast, the dashboard's engine-drawn forecast visual, and the writer's card.
Self-running (PASS/FAIL per test, exit 1 on any failure)."""
from __future__ import annotations

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(SITE, "engine"), os.path.join(os.path.dirname(SITE), "northledger-core")]
os.environ.setdefault("NL_BROWSER_STRICT", "1")
import nl_period_forecast as F  # noqa: E402


def test_a_trending_yearly_series_beats_naive_and_its_band_holds():
    rnd = random.Random(1)
    ys = [str(1950 + i) for i in range(70)]
    vs = [100 + 2.0 * i + rnd.gauss(0, 3) for i in range(70)]
    o = F.forecast(ys, vs, 12)
    assert o["ok"] and o["model"] in ("drift", "damped trend"), o["model"]
    assert o["mae"][o["model"]] < o["mae"]["naive"], o["mae"]
    assert [p["period"] for p in o["points"]] == ["2020", "2021", "2022"]
    assert o["points"][0]["lo"] < o["points"][0]["value"] < o["points"][0]["hi"]
    assert 0.65 <= o["audit"]["share"] <= 0.95 and o["trusted"], o["audit"]
    assert o["points"][2]["hi"] - o["points"][2]["lo"] >= o["points"][0]["hi"] - o["points"][0]["lo"]     # wider further out


def test_a_seasonal_quarterly_series_reads_the_same_quarter_a_year_earlier():
    rnd = random.Random(2)
    ps, vs = [], []
    for k in range(48):
        ps.append("%d-Q%d" % (2012 + k // 4, k % 4 + 1))
        vs.append(50 + [0, 20, -10, 35][k % 4] + rnd.gauss(0, 1.5))
    o = F.forecast(ps, vs, 3)
    assert o["ok"] and o["model"] == "seasonal naive", (o["model"], o["mae"])
    assert [p["period"] for p in o["points"]] == ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"]


def test_too_short_a_series_has_no_forecast():
    o = F.forecast([str(2000 + i) for i in range(9)], list(range(9)), 12)
    assert not o["ok"] and "15 years" in o["why"], o


def test_what_ifs_compound_on_the_forecast_path_and_stay_in_bounds():
    o = F.forecast([str(1950 + i) for i in range(40)], [100 + i for i in range(40)], 12)
    w = F.what_if(o, [{"label": "faster", "per_period_pct": 2}, {"label": "wild", "per_period_pct": 50}, {"label": "none", "per_period_pct": 0}])
    assert len(w) == 1 and w[0]["label"] == "faster", w
    b = [p["value"] for p in o["points"]]
    assert all(abs(q["value"] - b[k] * 1.02 ** (k + 1)) < 1e-9 for k, q in enumerate(w[0]["points"]))


def test_an_annual_file_gets_its_forecast_visual_and_the_writers_card():
    import nl_browser as NB
    rnd = random.Random(5)
    rows = ["observation_date,DEBT"] + ["%d-01-01,%.3f" % (1960 + i, 40 + 1.1 * i + rnd.gauss(0, 2)) for i in range(60)]
    plan = {"goal": "Where is debt heading?", "kind": "other", "primary": "DEBT", "what_if": [{"label": "faster spending", "per_period_pct": 2}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "debt.csv", "", {"__plan__": plan}, "2026-10-20")
    pf = rep.get("period_forecast") or {}
    assert pf.get("ok") and pf["grain"] == "year" and [p["period"] for p in pf["points"]] == ["2020", "2021", "2022"], pf
    vis = [v for v in rep["dashboard"].get("visuals") or [] if v["id"] == "forecast"]
    assert vis and vis[0]["by"] == "engine" and vis[0]["rows"][-1][0] == "2022-01-01", vis
    ra = NB.results_for_ai(rep)["period_forecast"]
    assert ra["points"][0]["text"].startswith("2020: ") and "band" in ra["points"][0]["text"] and ra["what_if"][0]["label"] == "faster spending", ra
    assert "back-tested" in (rep["forecast"].get("reason") or ""), rep["forecast"].get("reason")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            import traceback
            traceback.print_exc()
            print("FAIL", t.__name__, type(exc).__name__, exc)
    print("%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
    sys.exit(1 if bad else 0)
