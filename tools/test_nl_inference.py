#!/usr/bin/env python3
"""
Tests for engine/nl_inference.py, the report's inference (wave 4, track A2; plan/WAVE4-A-DESIGN.md 3 and 6,
CONTRACT-v2.md 5.11):

    python tools/test_nl_inference.py            # everything (the size check simulates 6,000 series, about 10 s)
    NL_FAST=1 python tools/test_nl_inference.py  # skips the [slow] size check

  T1  the trend test: its simulated size at 9 years (<= 7.5%, the Newey-West range it replaced > 15% at momentum
      0.8), recomputed here and equal to the pinned table; the table is the current script's and measured the
      current procedure; its verdicts on a line, a constant, a trend in noise and the FX rate; the fixed-b cubic is
      not used; the trend analysis quotes the size in its method text
  T2  an interval's measured coverage replaces "built to hold", quoting the benchmark condition the gate matches
  T3  overlapping windows: n_eff about n / 12 for a random walk's 12-month changes; the history items print it,
      count the windows that rose, use the extremes under 10 independent windows, and add the non-overlapping changes
  P0-13 the forecast audit: a too-narrow range fails, a seasonal random walk is no better than seasonal naive (not
      passes), a reviews-like series holding 1 of 12 fails, the cheap audit agrees with the full re-run; a row count
      the layout or the calendar fixes is not forecast
  T4  an official aggregate's record on the published FX layout, and none without the publisher's columns or with
      published standard errors

Self-running like the other suites: PASS/FAIL per test, exit 1 on any failure.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import os
import subprocess
import sys
import time
import types

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_DIR = os.path.join(SITE, "engine")
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

import nl_browser as NB  # noqa: E402
import nl_inference as NI  # noqa: E402
import nl_scenarios as NS  # noqa: E402
from northledger import forecast as F  # noqa: E402
from northledger import gate as G  # noqa: E402

import sim_trend_size as SIM  # noqa: E402

EVAL_FX = os.path.join(HERE, "fixtures", "eval", "fx_usd_cad.csv")
FX_LIVE_PLANS = os.path.join(HERE, "fixtures", "eval", "fx_live_plans_2026-10-01.json")
SAMPLE = os.path.join(ENGINE_DIR, "sample-messy.csv")
EVAL_FX_PLAN = {"goal": "How has the Canadian dollar price of one U.S. dollar moved?", "kind": "time_series",
                "understanding": "The Bank of Canada daily U.S. dollar rate.", "primary": "VALUE",
                "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date", "unit": ""},
                            {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"},
                            {"name": "STATUS", "semantic_type": "metadata", "role": "metadata", "unit": ""}],
                "operations": [{"op": "set_aside", "columns": ["STATUS"]}, {"op": "exclude_blank", "column": "VALUE"}],
                "analyses": [{"type": "trend", "columns": ["VALUE"]}, {"type": "distribution", "columns": ["VALUE"]}]}
# the published file's constant columns of StatCan 33-10-0036-01's U.S. dollar series (tools/test_nl_browser.py
# FX_PUBLISHED): the FX fixture rebuilt to the 15 columns the live run read
FX_PUBLISHED = (("GEO", "Canada"), ("DGUID", "2021A000011124"), ("Type of currency", "U.S. dollar, daily average"),
                ("UOM", "Dollars"), ("UOM_ID", "81"), ("SCALAR_FACTOR", "units"), ("SCALAR_ID", "0"),
                ("VECTOR", "v111666248"), ("COORDINATE", "1.25"))
_CACHE: dict = {}


def _run(data: bytes, name: str, decisions=None, as_of=None):
    key = (data, name, json.dumps(decisions, sort_keys=True), as_of)
    if key not in _CACHE:
        _CACHE[key] = NB.run(data, name, "", decisions, as_of)
    return _CACHE[key]


def _fx_published() -> bytes:
    with open(EVAL_FX, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_ALL)
    w.writerow(["REF_DATE"] + [k for k, _v in FX_PUBLISHED] + ["VALUE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS"])
    for r in rows:
        w.writerow([r["REF_DATE"]] + [v for _k, v in FX_PUBLISHED] + [r["VALUE"], r["STATUS"], "", "", "4"])
    return buf.getvalue().encode("utf-8")


def _fx_yearly():
    """The FX fixture's complete calendar years (all 12 months hold a rate) and their average rate, read here."""
    vals, months = {}, {}
    with open(EVAL_FX, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["VALUE"] and float(r["VALUE"]) != 0:
                y = int(r["REF_DATE"][:4])
                vals.setdefault(y, []).append(float(r["VALUE"]))
                months.setdefault(y, set()).add(r["REF_DATE"][5:7])
    yrs = sorted(y for y in vals if len(months[y]) == 12)
    return np.array(yrs, float), np.array([math.fsum(vals[y]) / len(vals[y]) for y in yrs])


# ------------------------------------------------------------------------------------------------ T1
def test_t1_size_at_9_years_is_at_most_7_5pct_and_newey_west_is_over_15pct_slow():
    """[slow] 2,000 no-trend series of 9 years at momentum 0, 0.4 and 0.8 through the report's trend test, recomputed
    here: its false-alarm rate at its 5% level is at most 7.5% in each, the Newey-West range the adapter used before
    finds a trend in over 15% at momentum 0.8, and the numbers equal the pinned table's (one seed per cell)."""
    if os.environ.get("NL_FAST"):
        print("        (skipped: NL_FAST)")
        return
    table = {(c["n"], c["rho"]): c for c in NI.tables()["trend_size"]["cells"]}
    for rho in (0.0, 0.4, 0.8):
        got = SIM._cell((9, rho, 2000))
        assert got["size"] <= NI.SIZE_MAX, got
        assert got == table[(9, rho)], (got, table[(9, rho)])
        if rho == 0.8:
            assert got["newey_west"] > 0.15, got
        assert got["unrestricted"] > got["size"], got      # why the null is imposed on the bootstrap


def test_t1_the_size_table_is_the_current_scripts_and_measured_the_current_procedure():
    doc = NI.tables()
    assert doc.get("script_sha256") == SIM.script_sha(), "re-run tools/sim_trend_size.py: the script changed"
    assert doc.get("procedure_sha256") == SIM.procedure_sha(), "re-run tools/sim_trend_size.py: the test changed"
    cells = doc["trend_size"]["cells"]
    assert {(c["n"], c["rho"]) for c in cells} == {(n, r) for n in SIM.NS for r in SIM.RHOS}
    assert doc["series_per_cell"] == 2000 and doc["test"]["B"] == NI.TREND_B and doc["test"]["seed"] == NI.SEED
    # every simulated condition of the grid keeps the test at or under the 7.5% it is held to
    worst = max(cells, key=lambda c: c["size"])
    assert worst["size"] <= NI.SIZE_MAX, worst
    assert all(c["newey_west"] > c["size"] for c in cells if c["n"] <= 30), [c for c in cells if c["newey_west"] <= c["size"]]
    out = subprocess.run([sys.executable, os.path.join(HERE, "sim_trend_size.py"), "--check"], capture_output=True,
                         text=True)
    assert out.returncode == 0 and out.stdout.startswith("PASS"), out.stdout + out.stderr


def test_t1_the_fixed_b_cubic_is_checked_and_not_used():
    fb = NI.tables()["fixed_b"]
    assert fb["used"] is False and fb["verified"]["recalled"]["trend_slope"] is False, fb["verified"]
    assert set(fb["formulas"]) == {"recalled", "secondary"} and "trend" in fb["note"], fb
    # nothing in the engine reads a fixed-b critical value
    src = open(os.path.join(ENGINE_DIR, "nl_inference.py"), encoding="utf-8").read()
    assert "2.9694" not in src, "the unverified cubic is in the engine"


def test_t1_verdicts_on_a_line_a_constant_a_trend_in_noise_and_the_fx_rate():
    t = np.arange(2000, 2015, dtype=float)
    line = NI.trend_test(t, 3.0 + 0.5 * t)
    assert line["verdict"] == "rising" and line["why"] == "exact_line" and abs(line["slope"] - 0.5) < 1e-9, line
    flat = NI.trend_test(t, np.full(len(t), 7.0))
    assert flat["verdict"] == "no_settled_direction" and flat["slope"] == 0.0, flat
    rng = np.random.default_rng(5)
    e = np.zeros(30)
    for i in range(1, 30):
        e[i] = 0.4 * e[i - 1] + rng.normal(0, 1.0)
    tt = np.arange(1990, 2020, dtype=float)
    up = NI.trend_test(tt, 0.6 * (tt - 1990) + e)
    assert up["verdict"] == "rising" and up["p"] < 0.01 and up["ci"][0] > 0, up
    down = NI.trend_test(tt, -0.6 * (tt - 1990) + e)
    assert down["verdict"] == "falling" and down["ci"][1] < 0, down
    # the FX rate's 9 yearly averages: Newey-West said "rose" (95% range 0.00329 to 0.0175); no settled direction now
    yrs, ys = _fx_yearly()
    fx = NI.trend_test(yrs, ys)
    b, se, _lag = NB._hac_slope(yrs, ys)
    tc = NB._tcrit(len(ys) - 2)
    assert b - tc * se > 0, "the old test no longer claims a rise here"
    assert fx["verdict"] == "no_settled_direction" and fx["p"] >= 0.05 and fx["ci"][0] < 0 < fx["ci"][1], fx
    assert fx["size"]["cell"] == {"n": 9, "rho": 0.6} and fx["size"]["simulated"] <= NI.SIZE_MAX, fx["size"]
    # the range is the symmetric percentile-t range: it excludes no change exactly when p < 0.05
    for rec in (up, down, fx):
        mid = 0.5 * (rec["ci"][0] + rec["ci"][1])
        assert abs(mid - rec["slope"]) < 1e-9 * max(1.0, abs(rec["slope"])), rec
        assert (rec["ci"][0] > 0 or rec["ci"][1] < 0) == (rec["p"] < 0.05), rec


def test_t1_a_random_walk_is_not_graded_a_trend_and_gaps_in_the_years_are_read():
    rng = np.random.default_rng(11)
    claimed = 0
    for _ in range(40):
        y = np.cumsum(rng.normal(0, 1, 12))
        claimed += NI.trend_test(np.arange(12, dtype=float), y)["verdict"] in ("rising", "falling")
    assert claimed <= 2, claimed            # the screen: a driftless walk's slope is not a trend
    t = np.array([2001, 2002, 2003, 2005, 2006, 2007, 2008, 2010, 2011, 2012], float)
    rec = NI.trend_test(t, 2.0 * t + rng.normal(0, 1, len(t)))
    assert rec["n"] == 10 and rec["verdict"] == "rising" and abs(rec["slope"] - 2.0) < 0.5, rec


def test_t1_is_deterministic_and_fast():
    yrs, ys = _fx_yearly()
    NI.trend_test(yrs, ys)
    t0 = time.perf_counter()
    a = NI.trend_test(yrs, ys)
    secs = time.perf_counter() - t0
    assert a == NI.trend_test(yrs, ys), "two runs of the same series differ"
    assert secs < 0.1, secs                 # the design's budget (Pyodide is about 10x native)


def test_t1_the_trend_analysis_quotes_the_simulated_size_and_carries_the_test():
    rep = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    assert rep["ok"], rep["error"]
    tr = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "trend")
    rec = tr["test"]
    assert tr["tests"] == [rec] and rec["series"] == "VALUE" and rec["verdict"] == "no_settled_direction", rec
    sz = rec["size"]
    assert ("On 2,000 simulated no-trend series like this one (9 years, momentum 0.6) the test's false-alarm rate at "
            "its 5%% level was %.1f%% and it claimed a direction in %.1f%% of them; the Newey-West range used before "
            "found a trend in %.1f%%." % (100 * sz["simulated"], 100 * sz["claims"], 100 * sz["newey_west"])) \
        in tr["method"], tr["method"]
    assert "Newey-West errors" not in tr["method"] and "rose by" not in tr["sentence"], tr
    assert tr["sentence"].startswith("VALUE shows no clear rise or fall over 2017 to 2025: its yearly average moves by "
                                     "%s CAD per USD per year, with a 95%% range of %s to %s, which includes no change"
                                     % (NB._fmt(rec["slope"]), NB._fmt(rec["ci"][0]), NB._fmt(rec["ci"][1]))), tr["sentence"]
    row = tr["table"]["rows"][0]
    assert row[3] == NB._fmt(rec["slope"]) and row[4] == "%s to %s" % (NB._fmt(rec["ci"][0]), NB._fmt(rec["ci"][1]))


# ------------------------------------------------------------------------------------------------ T2
def test_t2_the_interval_wording_quotes_the_measured_cell():
    data = open(SAMPLE, "rb").read()
    rep = _run(data, "sample-messy.csv", None, "2026-09-15")
    assert rep["ok"], rep["error"]
    with open(os.path.join(ENGINE_ROOT, "benchmark", "engine_benchmark.json"), encoding="utf-8") as fh:
        full = json.load(fh)
    ledger = {x["id"]: x for x in json.loads(rep["downloads"]["ledger_json"])["analysis_ledger"]}
    seen = 0
    for f in rep["findings"]:
        cov = (f.get("effect") or {}).get("coverage")
        if f.get("estimand") != "ratio_of_average_month" or f["effect"]["ci"] is None:
            assert cov is None, f["id"]
            continue
        t = ledger[f["id"]]["test"]
        fact = types.SimpleNamespace(claim_key=ledger[f["id"]]["claim_key"], test=t)
        level = NB._claim_level(fact)[0]
        want = NI.interval_coverage(full, G.claim_type(fact), t["n"], t["sigma_hat"], t["phi_hat"], level,
                                    None if t.get("seasonal") is None else bool(t["seasonal"]))
        assert cov is not None and cov["cell"] == want["cell"], (f["id"], cov, want)
        if want["measured"] is None:
            # no simulated condition is like the claim (gate.benchmark_far): nothing is quoted, and it says so
            assert cov["measured"] is None and cov["unlike"], cov
        else:
            assert abs(cov["measured"] - want["measured"]) < 1e-9, (f["id"], cov, want)
            assert cov["nominal"] == 0.95 and cov["lo"] <= cov["measured"] <= cov["hi"], cov
        words = NI.coverage_clause(cov, f["effect"]["level"])
        assert words in f["why"] and "built to hold" not in f["why"], f["why"][:300]
        if f.get("watch"):
            assert words in f["watch"]["reason"] and "built to hold" not in f["watch"]["reason"]
        seen += 1
    assert seen >= 4, seen
    st = rep["story"]
    blob = json.dumps([st[k] for k in ("headline", "what_happened", "why", "whats_next")])
    assert "built to hold" not in blob and "in the benchmark's simulated series nearest this one it held the true " \
                                            "change" in blob, blob[:400]
    # the browser's cut of the receipt quotes the same conditions as the full receipt
    import pack_engine as PK
    raw = open(os.path.join(ENGINE_ROOT, "benchmark", "engine_benchmark.json"), "rb").read()
    cut = json.loads(PK.cut_receipt(raw).decode("utf-8"))
    assert NI.coverage_cells(cut) == NI.coverage_cells(full), "the pack's cut changes the coverage quote"
    # no receipt: the label is a label
    assert NI.coverage_clause(None, 0.95) == "labelled 95%; coverage not measured for this kind of series"


# ------------------------------------------------------------------------------------------------ T3
def test_t3_white_noise_increments_give_n_eff_about_n_over_12():
    rng = np.random.default_rng(3)
    got = []
    for _ in range(30):
        level = np.cumsum(rng.normal(0, 1, 300))            # white-noise increments: a random walk
        ch = level[12:] - level[:-12]                         # 288 overlapping 12-month changes
        got.append(NI.n_eff_overlapping(ch, 12) / (len(ch) / 12.0))
    assert 0.7 <= float(np.mean(got)) <= 1.3, np.mean(got)
    assert NI.n_eff_overlapping([1.0, 2.0], 12) == 2.0 and NI.n_eff_overlapping([5.0] * 30, 12) == 30 / 12.0


def test_t3_the_history_items_print_n_eff_counts_and_the_non_overlapping_changes():
    rep = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    H = {it["id"]: it for it in rep["scenarios"]["items"] if it["group"] == "history_range"}
    for lag in (12, 3):
        b = "history_range.m%d" % lag
        n = int(H[b + ".windows"]["value"])
        neff = int(H[b + ".n_eff"]["value"])
        assert 1 <= neff < n and H[b + ".rose"]["kind"] == "count" and "%" not in H[b + ".rose"]["text"], H[b + ".rose"]
        lab = H[b + ".windows"]["label"]
        assert "worth about %d independent ones" % neff in lab and "it rose in %d of the %d" % (
            int(H[b + ".rose"]["value"]), n) in lab, lab
        assert "%" not in lab.split("Taking")[0].replace("1 in 10", ""), lab
        assert {b + ".nonoverlap." + k for k in ("n", "min", "median", "max")} <= set(H), sorted(H)
        nn = int(H[b + ".nonoverlap.n"]["value"])
        assert nn == ({12: 9, 3: 38}[lag]), nn
        for k, it in H.items():
            if it["kind"] == "change":
                v = it["value"]
                assert v == NI.sig2(v), (k, v)                    # 2 significant figures
                assert it["text"] == NS._fmt_item(v, "change", it["unit"], sig=2), it
    # FX's 104 overlapping 12-month windows are worth about 13 independent ones: the 10th and 90th percentiles stand
    assert {"history_range.m12.p10", "history_range.m12.p90"} <= set(H) and "history_range.m12.min" not in H
    # a 48-month level: 37 overlapping 12-month windows worth fewer than 10, so the extremes are stated
    rng = np.random.default_rng(4)
    lines = ["REF_DATE,VALUE"]
    lvl = 1.3
    for m in range(48):
        lvl += rng.normal(0, 0.01)
        lines.append("%04d-%02d-15,%.4f" % (2022 + m // 12, m % 12 + 1, lvl))
    plan = {"goal": "How has the rate moved?", "primary": "VALUE",
            "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date"},
                        {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"}]}
    rep2 = NB.run(("\n".join(lines) + "\n").encode(), "short_rate.csv", "", {"__plan__": plan}, "2026-01-15")
    H2 = {it["id"]: it for it in rep2["scenarios"]["items"] if it["group"] == "history_range"}
    assert H2["history_range.m12.n_eff"]["value"] < NS.HISTORY_NEFF_MIN, H2["history_range.m12.n_eff"]
    assert {"history_range.m12.min", "history_range.m12.max"} <= set(H2) and "history_range.m12.p10" not in H2, sorted(H2)
    assert "(the lowest)" in H2["history_range.m12.windows"]["label"], H2["history_range.m12.windows"]["label"]


# ------------------------------------------------------------------------------------------------ P0-13
def _seasonal(seed, n=96, phi=0.5, sd=0.06):
    rng = np.random.default_rng(seed)
    m = np.arange(n)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = phi * e[i - 1] + rng.normal(0, sd)
    return [F.month_index("2018-01") + i for i in range(n)], np.round(800 * (1 + 0.3 * np.sin(2 * np.pi * m / 12))
                                                                       * np.exp(0.004 * m + e))


def _seasonal_random_walk(seed, n=96):
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    y[:12] = 600 + 150 * np.sin(2 * np.pi * np.arange(12) / 12)
    for i in range(12, n):
        y[i] = y[i - 12] + rng.normal(0, 40)
    return [F.month_index("2018-01") + i for i in range(n)], np.maximum(np.round(y), 1)


def test_audit_a_too_narrow_band_fails():
    months, y = _seasonal(2)
    fr = F.run_forecast(months, y, {"horizon": 12})
    narrow = copy.deepcopy(fr)
    for row in narrow.forward:
        row["lo80"], row["hi80"] = row["point"] * 0.99, row["point"] * 1.01
    a = NI.forecast_audit(months, y, narrow)
    assert a["status"] == "fails" and a["trusted"] is False, a
    assert all(h["wilson"][1] < 0.8 for h in a["horizons"]), a["horizons"]
    assert a["grade_label"] == "the engine's grade (not trusted: the back-test failed)", a["grade_label"]


def test_audit_a_seasonal_random_walk_is_no_better_than_seasonal_naive():
    for seed in (1, 2, 3):
        months, y = _seasonal_random_walk(seed)
        fr = F.run_forecast(months, y, {"horizon": 12})
        a = NI.forecast_audit(months, y, fr)
        assert a["status"] != "passes" and not a["trusted"], (seed, a["status"], a["label"])
        assert all(abs(h["rel_mae"] - 1.0) <= 0.15 for h in a["horizons"]), [h["rel_mae"] for h in a["horizons"]]


# The live reviews forecast's shown 80% range, as ratios of its point at 1 to 12 months ahead (the evaluation's
# prediction run, 1 Oct 2026: its month-ahead range held 10 of 12 replayed months and it was graded RECOMMEND, then
# held 1 of the 12 held-out months). Ratios only; no review is in this file.
REVIEWS_BAND = ((0.789, 1.245), (0.77, 1.252), (0.761, 1.262), (0.765, 1.264), (0.768, 1.296), (0.763, 1.297),
                (0.769, 1.404), (0.768, 1.412), (0.78, 1.415), (0.754, 1.42), (0.712, 1.397), (0.732, 1.442))


def _reviews_like():
    """A synthetic monthly count of reviews: holiday peaks (Dec, Jan), growth of about 10% a year for eight years, and
    a fall of 38% for the last eleven months, as the review site's own counts fell after the evaluation's cut."""
    rng = np.random.default_rng(20261001)
    n = 96
    m = np.arange(n)
    moy = m % 12
    seas = np.where(moy == 11, 1.55, np.where(moy == 0, 1.75, np.where(moy == 1, 1.2, 1.0)))
    level = 430 * 1.008 ** m * np.where(m >= n - 11, 0.62, 1.0)
    return [F.month_index("2018-01") + i for i in m], np.round(level * seas * np.exp(rng.normal(0, 0.05, n)))


def test_audit_a_reviews_like_series_holding_1_of_12_fails():
    months, y = _reviews_like()
    fr = F.run_forecast(months, y, {"horizon": 12})
    shown = copy.deepcopy(fr)
    shown.champion = "loglinear_season"           # the live forecast's model
    shown.error_mode = "ratio"
    shown.forward = [{"point": 100.0, "lo80": 100.0 * lo, "hi80": 100.0 * hi} for lo, hi in REVIEWS_BAND]
    a = NI.forecast_audit(months, y, shown)
    h12 = next(h for h in a["horizons"] if h["h"] == 12)
    assert (h12["held"], h12["of"]) == (1, 12), h12
    assert h12["status"] == "fails" and a["status"] == "fails" and a["trusted"] is False, a
    assert "1 of 12 at 12 months" in a["label"], a["label"]


def test_audit_the_cheap_audit_agrees_with_the_full_rerun():
    for seed in (1, 2):
        months, y = _seasonal(seed)
        fr = F.run_forecast(months, y, {"horizon": 12})
        a = NI.forecast_audit(months, y, fr)
        b = NI.forecast_audit(months, y, fr, mode="full")
        assert a["status"] == b["status"], (seed, a["label"], b["label"])
        for x, z in zip(a["horizons"], b["horizons"]):
            assert x["of"] == z["of"] and abs(x["held"] - z["held"]) <= 3, (x, z)


def test_audit_a_skilled_forecast_passes_at_one_month_and_the_rule_is_the_stricter_one():
    months, y = _seasonal(1, phi=0.2, sd=0.02)
    fr = F.run_forecast(months, y, {"horizon": 12})
    a = NI.forecast_audit(months, y, fr)
    h1 = a["horizons"][0]
    assert h1["status"] == "passes" and h1["wilson"][0] >= 0.6 and h1["n_eff"] >= 8 and h1["rel_mae"] < 1, h1
    assert NI.AUDIT_PASS == {"wilson_lo": 0.60, "n_eff": 8.0, "rel_mae": 1.0}, NI.AUDIT_PASS
    # an audited horizon with fewer than 8 effective checks is never "passes", however well it held
    assert all(h["status"] != "passes" for h in a["horizons"] if h["n_eff"] < 8), a["horizons"]
    t0 = time.perf_counter()
    NI.forecast_audit(months, y, fr)
    assert time.perf_counter() - t0 < 0.3


def test_audit_row_counts_fixed_by_the_layout_or_the_calendar_are_not_series():
    assert NI.row_series_artifact([465.0] * 79)["kind"] == "layout"
    assert "465 a month" in NI.row_series_artifact([465.0] * 79)["reason"]
    assert NI.row_series_artifact([20.0, 22.0], None, {"layout": "long statistical table"})["kind"] == "layout"
    # a structured cube's slice (track A1): one row a month inside, the outer table's 465 a month in the reason
    sl = NI.row_series_artifact([1.0] * 79, None, {"layout": "structured cube slice", "rows_a_month": 465})
    assert sl["kind"] == "layout" and sl["reason"] == "rows per month are fixed by the table's layout (465 a month)", sl
    daily = [(30, 30), (31, 31), (28, 28), (31, 31), (30, 30), (31, 31), (22, 22)]
    assert NI.row_series_artifact([30, 31, 28, 31, 30, 31, 22], daily)["kind"] == "calendar"
    events = [(140, 30), (151, 31), (120, 28), (160, 31), (150, 30), (149, 31)]
    assert NI.row_series_artifact([140, 151, 120, 160, 150, 149], events) is None
    # the FX report: each row is one date, so its row forecast is dropped, out of the tiles and the bottom line
    rep = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    fc = rep["forecast"]
    assert not fc["available"] and fc["row_forecast_dropped"]["kind"] == "calendar" and fc["forecast"] == [], fc
    assert fc["reason"].startswith("No forecast of the rows a month is shown: each row is one date"), fc["reason"]
    f = next(x for x in rep["findings"] if x["id"] == "forecast.monthly_rows.next")
    assert f["layout_artifact"] is True and f["effect"]["estimate"] is None, f
    tiles = next(c for c in rep["charts"] if c["id"] == "kpi")["data"]["tiles"]
    assert "forecast.monthly_rows.next" not in [t["finding_id"] for t in tiles], tiles
    assert not [ln for ln in rep["summary"]["lines"] if ln["kind"] == "plan"], rep["summary"]["lines"]
    assert rep["story"]["whats_next"] == ["No forecast of monthly rows in fx_usd_cad.csv is shown: %s."
                                          % fc["row_forecast_dropped"]["reason"]], rep["story"]["whats_next"]
    assert all(x["layout_artifact"] is False for x in rep["findings"] if x["id"] != "forecast.monthly_rows.next")
    # the audit is still attached, for the record, to the series it was about
    assert fc["audit"]["series"] == "monthly rows in fx_usd_cad.csv" and fc["audit"]["horizons"], fc["audit"]


def test_audit_the_sample_report_carries_its_forecasts_audit():
    rep = _run(open(SAMPLE, "rb").read(), "sample-messy.csv", None, "2026-09-15")
    fc = rep["forecast"]
    a = fc["audit"]
    assert fc["available"] and fc["row_forecast_dropped"] is None and a["series"] == fc["label"], fc
    assert [h["h"] for h in a["horizons"]] == [1, 3, 6] and a["origins"] == 11, a
    assert fc["trusted"] is (a["status"] == "passes"), (fc["trusted"], a["status"])
    out = NB.results_for_ai(rep)["forecast"]
    assert out["audit"] == {"label": a["label"], "status": a["status"], "trusted": a["trusted"],
                            "grade_label": a["grade_label"]}, out


# ------------------------------------------------------------------------------------------------ T4
def test_t4_an_official_aggregate_is_described_not_tested():
    with open(FX_LIVE_PLANS, encoding="utf-8") as fh:
        plan = json.load(fh)["plan_2"]
    rep = _run(_fx_published(), "quant_fx_usd.csv", {"__plan__": plan}, "2026-09-30")
    assert rep["ok"], rep["error"]
    f = next(x for x in rep["findings"] if x["id"] == "measure.value.change")
    inf = f["inference"]
    assert inf["mode"] == "official_aggregate" and inf["publisher"] == "statcan", inf
    assert inf["how_known"][0] == "publisher columns REF_DATE, DGUID, VECTOR, COORDINATE, STATUS (Statistics Canada)"
    assert inf["describe"]["change_pct"] == f["value"] and inf["describe"]["prior"] and inf["describe"]["latest"], inf
    assert abs(100.0 * (inf["describe"]["latest"] / inf["describe"]["prior"] - 1.0) - f["value"]) < 1e-6, inf
    assert inf["grade_label"].startswith("process grade: month-to-month noise in ") and \
        inf["grade_label"].endswith("; not a test of the published figure"), inf["grade_label"]
    # the row count is never an official aggregate; the engine's grade stands beside the record
    assert next(x for x in rep["findings"] if x["id"] == "measure.volume.change_pct")["inference"] is None
    assert f["grade"] in ("WATCH", "CONFIRMED", "NOT_ENOUGH_DATA")
    # no publisher's columns: no record
    plain = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    assert all(x["inference"] is None for x in plain["findings"]), [x["id"] for x in plain["findings"] if x["inference"]]
    # published sampling errors: the estimate could be tested against them, so it is not described as fully observed
    assert NI.publishes_errors([{"name": "Statistics", "values": ["Estimate", "Standard error of estimate"]}])
    assert not NI.publishes_errors([{"name": "GEO", "values": ["Canada"]}])
    assert NI.publisher_of(["REF_DATE", "VALUE", "STATUS"]) is None
    assert NI.publisher_of(["ref_date", "dguid", "vector", "value"])["key"] == "statcan"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        t0 = time.perf_counter()
        try:
            fn()
            print("  PASS  %s (%.1fs)" % (fn.__name__, time.perf_counter() - t0))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  %s: %s: %s" % (fn.__name__, type(exc).__name__, str(exc)[:600]))
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    if failed:
        sys.exit(1)
    print("ALL TESTS PASSED")
