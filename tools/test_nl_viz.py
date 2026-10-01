#!/usr/bin/env python3
"""
Tests for engine/nl_viz.py, the chart registry's engine side (Batch 1; CONTRACT-v2.md 5.9, tools/fixtures/viz/spec.json).

    python tools/test_nl_viz.py                        # the adapter in engine/
    NL_BROWSER_DIR=/some/copy python tools/test_nl_viz.py   # a copy (used to prove red: an adapter without nl_viz)
    python tools/test_nl_viz.py --write-fixture         # rewrite tools/fixtures/viz/engine-charts-ship2.json

Runs the adapter natively on ship2 (tools/fixtures/scenarios), the FX rates and the synthetic reviews
(tools/fixtures/eval) and a synthetic stores file built here, and checks:
  * every chart re-computed from downloads.clean_csv with plain pandas (the waterfalls and the slope, the
    calendar, change, crosstab and theme x rating heatmaps, the correlations, the group ranges and the Pareto);
  * the spec's reconciliations (the scenarios items, the trend series, the compare and themes analyses, the corr
    chart), and that the ship2 and FX records equal the spec's real examples;
  * suppression ('<5' cells, levels folded into 'other', no margins) and privacy (no withheld, coded or kept
    flagged column in any role or label, and none of their values);
  * the plan's charts[] checked and refused with the engine's reasons, never as a plan signal; the engine's own
    picks by the design's scores, deterministically;
  * NaN-free records, byte-identical across runs and string-hash seeds; results_for_ai's caps and byte budget;
  * profile.chart_limits on ship2 (the spec's example), the FX rates and the reviews;
  * every record against spec.json's schema and its kind's invariants (validate_spec.py), with the owner's byte
    caps (6,000 a record, 12,000 a heatmap), and trimmed when over them.
Self-running (PASS/FAIL per test, exit 1 on any failure). Python 3.9, pandas and numpy; nothing is fetched.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import os
import random
import re
import subprocess
import sys

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ADAPTER_DIR = os.path.abspath(os.environ.get("NL_BROWSER_DIR") or os.path.join(SITE, "engine"))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
VIZ_DIR = os.path.join(HERE, "fixtures", "viz")
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ADAPTER_DIR)
sys.path.insert(0, VIZ_DIR)
import nl_browser as NB  # noqa: E402
import nl_scenarios as NS  # noqa: E402
import validate_spec as VS  # noqa: E402

SPEC = VS.load(os.path.join(VIZ_DIR, "spec.json"))
FIXTURE = os.path.join(VIZ_DIR, "engine-charts-ship2.json")
HEATMAP_BYTES = 12000          # the owner's decision (30 Sep 2026): a heatmap's byte cap; every other kind 6,000
assert SPEC["caps"].get("HEATMAP_BYTES") == HEATMAP_BYTES, "spec.json caps.HEATMAP_BYTES is not the owner's 12,000"
MENU = list(SPEC["menu"])


def _nv():
    """nl_viz, imported when a test needs it (an adapter without it fails each test, not the whole file)."""
    import nl_viz
    return nl_viz


# ------------------------------------------------------------------------ the files
SHIP2 = os.path.join(HERE, "fixtures", "scenarios", "ship2_privacy_orders.csv")
SHIP2_AS_OF = "2026-09-29"
FX = os.path.join(HERE, "fixtures", "eval", "fx_usd_cad.csv")
REVIEWS = os.path.join(HERE, "fixtures", "eval", "reviews_synthetic.csv")
SHIP2_PLAN = {"goal": "How did order revenue develop over 2024 to 2025?", "understanding": "Orders.",
              "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                          {"name": "region", "semantic_type": "category", "role": "segment"},
                          {"name": "revenue", "semantic_type": "flow_amount", "role": "target"},
                          {"name": "units", "semantic_type": "count", "role": "driver"}],
              "operations": [], "analyses": [], "primary": "revenue"}
SHIP2_CHARTS = [{"kind": "contribution_waterfall", "columns": ["region", "revenue"], "why": "What moved revenue."},
                {"kind": "pvm_waterfall", "columns": ["revenue", "units", "region"], "why": "Price or volume?"},
                {"kind": "calendar_heatmap", "columns": ["revenue"], "why": "Monthly totals side by side."},
                {"kind": "change_heatmap", "columns": ["region", "revenue"], "why": "Where in the year."},
                {"kind": "group_ranges", "columns": ["revenue", "region"], "why": "Order size by region."},
                {"kind": "pareto", "columns": ["region", "revenue"], "why": "Which regions carry revenue."},
                {"kind": "slope", "columns": ["region", "revenue"], "why": "Each region before and after."}]
FX_PLAN = {"goal": "How has the Canadian dollar price of one U.S. dollar moved?", "kind": "time_series",
           "understanding": "The Bank of Canada daily U.S. dollar rate.", "primary": "VALUE",
           "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date", "unit": ""},
                       {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"},
                       {"name": "STATUS", "semantic_type": "metadata", "role": "metadata", "unit": ""}],
           "operations": [{"op": "set_aside", "columns": ["STATUS"]}, {"op": "exclude_blank", "column": "VALUE"}],
           "analyses": [{"type": "trend", "columns": ["VALUE"]}, {"type": "distribution", "columns": ["VALUE"]}]}
REVIEWS_PLAN = {
    "goal": "What do reviewers say at each rating, and which brands draw the reviews?", "kind": "survey",
    "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"},
                {"name": "verified_purchase", "semantic_type": "boolean", "role": "segment"},
                {"name": "helpful_votes", "semantic_type": "count", "role": "driver"},
                {"name": "department", "semantic_type": "category", "role": "segment"},
                {"name": "brand", "semantic_type": "entity", "role": "entity"},
                {"name": "review_title", "semantic_type": "free_text", "role": "driver"}],
    "operations": [], "analyses": [{"type": "themes", "columns": ["review_title"]}],
    "charts": [{"kind": "theme_rating_heatmap", "columns": ["review_title", "rating"], "why": "Words by rating."},
               {"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"], "why": "Words by rating."},
               {"kind": "pareto", "columns": ["brand"], "why": "Which brands draw reviews."},
               {"kind": "crosstab_heatmap", "columns": ["department", "rating", "helpful_votes"], "why": "Votes."},
               {"kind": "change_heatmap", "columns": ["department"], "why": "Which departments moved."}]}
STORES_PLAN = {"goal": "Which stores drove revenue, and where in the year?", "kind": "transactions", "primary": "revenue",
               "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                           {"name": "store", "semantic_type": "category", "role": "segment"},
                           {"name": "channel", "semantic_type": "category", "role": "segment"},
                           {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "USD"},
                           {"name": "units", "semantic_type": "count", "role": "driver"},
                           {"name": "cost", "semantic_type": "flow_amount", "role": "driver", "unit": "USD"},
                           {"name": "stars", "semantic_type": "rating", "role": "driver"}],
               "operations": [], "analyses": [{"type": "compare", "columns": ["revenue"], "by": "store"}]}
STORES_CHARTS = [{"kind": "change_heatmap", "columns": ["store", "revenue"], "why": "Where in the year each store moved."},
                 {"kind": "crosstab_heatmap", "columns": ["store", "channel", "revenue"], "why": "Store by channel."},
                 {"kind": "crosstab_heatmap", "columns": ["channel", "stars"], "why": "Ratings by channel."},
                 {"kind": "correlation_heatmap", "columns": [], "why": "Do the measures move together?"},
                 {"kind": "pareto", "columns": ["store", "revenue"], "why": "Which stores carry revenue."},
                 {"kind": "contribution_waterfall", "columns": ["store", "revenue"], "why": "What moved revenue."},
                 {"kind": "slope", "columns": ["store"], "why": "Before and after."},
                 {"kind": "group_ranges", "columns": ["revenue", "store"], "why": "Order size by store."}]
CORR_CHARTS = [{"kind": "correlation_heatmap", "columns": [], "why": "Do the measures move together?"},
               {"kind": "correlation_heatmap", "columns": ["revenue", "units", "cost"], "why": "The three flows."}]


def _stores_bytes() -> bytes:
    """26 months of orders from 10 stores (Uptown opens in the latest 12 months with 3 orders, Mall closes after the
    first 12 months), 3 channels, revenue, units, cost and a 1-5 star rating: every Batch 1 chart has rows to read,
    and the small-cell rules have a level to fold."""
    rng = random.Random(5)
    stores = ["Store %s" % c for c in "ABCDEFGH"] + ["Mall", "Uptown"]
    rows = []
    for k in range(26):
        y, m = 2024 + k // 12, k % 12 + 1
        for si, st in enumerate(stores):
            if (st == "Uptown" and k < 24) or (st == "Mall" and k >= 12):
                continue
            n = (3 if k == 24 else 0) if st == "Uptown" else 6 + si % 4
            for _ in range(n):
                u = rng.randint(1, 9)
                p = rng.uniform(20, 40) * (1.1 if k >= 14 else 1.0) * (1 + 0.05 * si)
                rev = u * p
                rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), st, rng.choice(["web", "phone", "store"]),
                             "%.2f" % rev, "%d" % u, "%.2f" % (rev * rng.uniform(0.5, 0.7)), "%d" % rng.randint(1, 5)])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["order_date", "store", "channel", "revenue", "units", "cost", "stars"])
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


CASES = {
    "ship2": (lambda: _read(SHIP2), "ship2_privacy_orders.csv", None, SHIP2_AS_OF),
    "ship2_ai": (lambda: _read(SHIP2), "ship2_privacy_orders.csv", {"__plan__": dict(SHIP2_PLAN, charts=SHIP2_CHARTS)},
                 SHIP2_AS_OF),
    "fx": (lambda: _read(FX), "fx_usd_cad.csv", {"__plan__": FX_PLAN}, "2026-09-29"),
    "reviews": (lambda: _read(REVIEWS), "reviews_synthetic.csv", None, "2026-09-30"),
    "reviews_ai": (lambda: _read(REVIEWS), "reviews_synthetic.csv", {"__plan__": REVIEWS_PLAN, "review_text": "keep"},
                   "2026-09-30"),
    "stores_ai": (_stores_bytes, "stores.csv", {"__plan__": dict(STORES_PLAN, charts=STORES_CHARTS)}, "2026-03-15"),
    "stores_corr": (_stores_bytes, "stores.csv", {"__plan__": dict(STORES_PLAN, charts=CORR_CHARTS)}, "2026-03-15"),
    "stores": (_stores_bytes, "stores.csv", None, "2026-03-15"),
}
_CACHE = {}


def _case(name: str):
    if name not in _CACHE:
        make, fname, dec, as_of = CASES[name]
        rep = NB.run(make(), fname, "", copy.deepcopy(dec), as_of)
        assert rep["ok"], (name, rep["error"])
        _CACHE[name] = rep
    return _CACHE[name]


def _viz(rep):
    assert "viz" in rep and isinstance(rep["viz"], dict), "the report has no viz block"
    return rep["viz"]


def _rec(rep, chart):
    got = [c for c in _viz(rep)["charts"] if c["chart"] == chart]
    assert got, "no %s among %s (refused: %s)" % (chart, [c["chart"] for c in _viz(rep)["charts"]], _viz(rep)["refused"])
    return got[0]


def _clean(rep):
    import pandas as pd
    return pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]), dtype=str, keep_default_na=False)


def _num(s):
    import pandas as pd
    return pd.to_numeric(s.where(s != ""), errors="coerce")


def _check(rec):
    """validate_spec's schema and invariants, with the owner's byte caps (spec.json caps: HEATMAP_BYTES for a heatmap,
    CHART_BYTES for every other kind, which check_record applies by kind)."""
    errs = VS.validate(rec, SPEC["schema"], SPEC["schema"])
    return errs or VS.check_record(rec, SPEC)


def _pctl(xs, p):
    import numpy as np
    return float(np.percentile(np.asarray(xs, dtype=float), p))


def _seq_tiers(values):
    shown = [v for row in values for v in row if v is not None]
    q1, q2 = _pctl(shown, 100 / 3.0), _pctl(shown, 200 / 3.0)
    return [[0 if v is None else 1 if v <= q1 else 2 if v <= q2 else 3 for v in row] for row in values], \
        (min(shown), q1, q2, max(shown))


def _div_tiers(values):
    mags = [abs(v) for row in values for v in row if v is not None and v != 0]
    q1, q2 = _pctl(mags, 100 / 3.0), _pctl(mags, 200 / 3.0)

    def t(v):
        if v is None or v == 0:
            return 0
        k = 1 if abs(v) <= q1 else 2 if abs(v) <= q2 else 3
        return k if v > 0 else -k
    return [[t(v) for v in row] for row in values], (q1, q2, max(mags))


def _close(a, b, rel=1e-6):
    return abs(float(a) - float(b)) <= rel * max(1.0, abs(float(b)))


def _grid_equal(rec, values, n, text_of, scale):
    """The record's grid against a grid recomputed here: values (to 1e-6), n, texts, tiers and the legend."""
    d = rec["data"]
    R, C = len(d["rows"]), len(d["cols"])
    assert len(values) == R and all(len(r) == C for r in values), (R, C)
    for i in range(R):
        for j in range(C):
            want, got = values[i][j], d["values"][i][j]
            nn = n[i][j]
            if nn == 0:
                assert got is None and d["text"][i][j] == "" and d["n"][i][j] == 0, (rec["id"], i, j, got, nn)
            elif nn < 5:
                assert got is None and d["text"][i][j] == "<5" and d["n"][i][j] is None, (rec["id"], i, j, got, nn)
            else:
                assert got is not None and _close(got, want), (rec["id"], i, j, got, want)
                assert d["n"][i][j] == nn and d["text"][i][j] == text_of(got), (rec["id"], i, j, d["text"][i][j], nn)
    shown = [[d["values"][i][j] if (n[i][j] or 0) >= 5 else None for j in range(C)] for i in range(R)]
    tiers, b = (_seq_tiers if scale == "sequential" else _div_tiers)(shown)
    assert d["tier"] == tiers, (rec["id"], d["tier"], tiers)
    assert [x["tier"] for x in d["legend"]] == sorted({t for row in tiers for t in row if t}), d["legend"]
    return b


# ------------------------------------------------------------------------ the block and the engine's picks
def test_blank_report_and_a_refused_run_carry_an_empty_viz():
    assert NB.blank_report()["viz"] == {"version": SPEC["version"], "charts": [], "refused": [], "chosen_by": "none"}
    rep = NB.run(b"", "empty.csv", "")
    assert not rep["ok"] and rep["viz"] == NB.blank_report()["viz"], rep.get("viz")
    assert _nv().VERSION == SPEC["version"] and NB.VIZ_VERSION == SPEC["version"]
    caps = SPEC["caps"]
    nv = _nv()
    for k in ("VIZ_MAX", "VIZ_AUTO", "HEAT_MAX", "SMALL_CELL", "AI_CHARTS_MAX", "CHART_BYTES", "HEATMAP_BYTES",
              "CHART_LIMITS_MAX"):
        assert getattr(nv, k) == caps[k], k
    assert nv.HEATMAP_BYTES == HEATMAP_BYTES and list(nv.MENU) == MENU
    assert (nv.bytes_cap("heatmap"), nv.bytes_cap("waterfall"), nv.bytes_cap("pareto")) == (12000, 6000, 6000)
    for name, m in SPEC["menu"].items():
        r = nv.REGISTRY[name]
        assert (r["kind"], r["section"], r["args_text"], r["what"], r["limit_needs"]) == \
            (m["kind"], m["section"], m["args_text"], m["what"], m["limit_needs"]), name
        assert [role for role, _o in r["args"]] == [a["role"] for a in m["args"]], name
    for name, sc in SPEC["auto_rank"]["base"]["scores"].items():
        assert list(nv.RANK_BASE[name]) == sc, name
    assert list(nv.RANK_KINDS) == SPEC["auto_rank"]["base"]["columns"]
    assert {k: list(v) for k, v in nv.GOAL_KEYWORDS.items()} == SPEC["auto_rank"]["goal_keywords"]


def test_ship2_engine_picks_the_top_four_by_the_designs_scores():
    nv = _nv()
    rep = _case("ship2")
    v = _viz(rep)
    assert v["chosen_by"] == "engine" and v["refused"] == [], v
    assert [c["id"] for c in v["charts"]] == ["viz.1.contribution_waterfall", "viz.2.calendar_heatmap",
                                              "viz.3.group_ranges", "viz.4.pvm_waterfall"], [c["id"] for c in v["charts"]]
    for c in v["charts"]:
        assert c["chosen_by"] == "engine" and c["why"] == "Chosen by the engine: " + SPEC["menu"][c["chart"]]["what"], c["why"]
    # the order is the design's: highest score first, ties in menu order, only charts whose limit is ok and that build
    lim = {x["chart"]: x for x in NB.profile_for_ai(_read(SHIP2), "ship2_privacy_orders.csv")["chart_limits"]}
    kind = SPEC["auto_rank"]["base"]["columns"].index("other")
    score = {c: SPEC["auto_rank"]["base"]["scores"][c][kind] for c in MENU}
    order = sorted(MENU, key=lambda c: (-score[c], MENU.index(c)))
    picked = [c["chart"] for c in v["charts"]]
    ok = [c for c in order if lim[c]["ok"]]
    assert picked == ok[:4], (picked, ok)
    # the plan's kind, primary column and goal move the ranking (the bonuses), and the picks do not change run to run
    plan = dict(SHIP2_PLAN, kind="survey", goal="Which region is best? Compare the groups.")
    rep2 = NB.run(_read(SHIP2), "ship2_privacy_orders.csv", "", {"__plan__": plan}, SHIP2_AS_OF)
    got = [c["chart"] for c in rep2["viz"]["charts"]]
    assert got[0] == "group_ranges", got                 # survey 8 + primary 3 + goal 'best', 'compare', 'group' (+2)
    rep3 = NB.run(_read(SHIP2), "ship2_privacy_orders.csv", "", {"__plan__": plan}, SHIP2_AS_OF)
    assert json.dumps(rep3["viz"]) == json.dumps(rep2["viz"])


def test_ship2_and_fx_records_equal_the_specs_real_examples():
    ex = {k: v["record"] for k, v in SPEC["examples"].items()}
    auto, ai, fx = _case("ship2"), _case("ship2_ai"), _case("fx")
    for rep, chart, name in ((auto, "contribution_waterfall", "waterfall"), (auto, "calendar_heatmap", "heatmap"),
                             (auto, "group_ranges", "dot_range"), (auto, "pvm_waterfall", "edge_waterfall_pvm"),
                             (ai, "slope", "slope"), (fx, "calendar_heatmap", "edge_heatmap_diverging")):
        r = _rec(rep, chart)
        want = ex[name]
        for k in ("kind", "chart", "section", "grade", "parent_grade", "measure"):
            if name == "edge_heatmap_diverging" and k == "measure":
                continue
            assert r[k] == want[k], (chart, k, r[k], want[k])
        d, w = r["data"], want["data"]
        if name == "slope":
            assert sorted(d["rows"], key=lambda x: x["label"]) == sorted(w["rows"], key=lambda x: x["label"]), d["rows"]
            assert [x["label"] for x in d["rows"]] == [x["label"] for x in w["rows"]], [x["label"] for x in d["rows"]]
        else:
            assert d == w, (chart, json.dumps(d)[:400], json.dumps(w)[:400])
        if name in ("waterfall", "heatmap", "dot_range", "edge_waterfall_pvm"):
            assert r["table"] == want["table"], (chart, r["table"], want["table"])
        # the examples' own anchors: the dot range's came from a run with a compare analysis (this one has none), and
        # the FX example lists none where the engine anchors the claim its months reconcile with
        if name not in ("dot_range", "edge_heatmap_diverging"):
            assert r["anchors"][:1] == want["anchors"][:1], (chart, r["anchors"], want["anchors"])
        elif name == "edge_heatmap_diverging":
            assert r["anchors"] == ["finding:measure.value.change"], r["anchors"]


def test_every_record_passes_the_specs_schema_and_invariants():
    seen = set()
    for name in CASES:
        rep = _case(name)
        for c in _viz(rep)["charts"]:
            errs = _check(c)
            assert not errs, (name, c["id"], errs[:5])
            assert VS.js_bytes(c) <= SPEC["caps"]["HEATMAP_BYTES" if c["kind"] == "heatmap" else "CHART_BYTES"], (name, c["id"])
            assert VS.js_bytes(c) == _nv().record_bytes(c), c["id"]
            seen.add(c["chart"])
    assert seen == set(MENU), "no case built %s" % sorted(set(MENU) - seen)


# ------------------------------------------------------------------------ recomputed from the download
def _windows(rep):
    b = rep["scenarios"]["basis"]
    return NB._month_range(*b["windows"]["prior"]), NB._month_range(*b["windows"]["latest"])


def test_waterfalls_and_slope_recompute_from_the_download_and_reconcile_with_the_scenarios():
    import pandas as pd
    for name, seg, measure in (("ship2", "region", "revenue"), ("ship2_ai", "region", "revenue")):
        rep = _case(name)
        df = _clean(rep)
        pm, lm = _windows(rep)
        mon = df["order_date"].str[:7]
        v = _num(df[measure])
        u = _num(df["units"])
        g = df[seg]
        tot = {w: {k: float(v[(mon.isin(ms)) & (g == k)].sum()) for k in sorted(set(g))} for w, ms in (("p", pm), ("l", lm))}
        uni = {w: {k: float(u[(mon.isin(ms)) & (g == k)].sum()) for k in sorted(set(g))} for w, ms in (("p", pm), ("l", lm))}
        R0, R1 = sum(tot["p"].values()), sum(tot["l"].values())
        it = {x["id"]: x for x in rep["scenarios"]["items"]}
        wf = _rec(rep, "contribution_waterfall")
        st = wf["data"]["steps"]
        assert st[0]["label"] == "12 months before" and _close(st[0]["value"], R0) and _close(st[-1]["value"], R1)
        want = sorted(((k, tot["l"][k] - tot["p"][k]) for k in tot["p"]), key=lambda kv: (-abs(kv[1]), kv[0]))
        assert [(s["label"], round(s["value"], 2)) for s in st[1:-1]] == [(k, round(c, 2)) for k, c in want], st
        assert [s["text"] for s in st[1:-1]] == [NS._fmt_item(round(c, 6), "change", "") for _k, c in want]
        # the spec's reconciliation: the steps add up to change.value, the last total is headline.latest (1e-6)
        assert abs(math.fsum(s["value"] for s in st[1:-1]) - wf["data"]["change"]["value"]) <= 1e-6 * R1
        assert abs(st[-2]["to"] - it["headline.latest"]["value"]) <= 1e-6 * R1 and \
            wf["data"]["change"]["value"] == it["headline.change"]["value"]
        pvm = _rec(rep, "pvm_waterfall")
        U0, U1 = sum(uni["p"].values()), sum(uni["l"].values())
        P0 = R0 / U0
        price = sum(uni["l"][k] * (tot["l"][k] / uni["l"][k] - tot["p"][k] / uni["p"][k]) for k in tot["p"])
        volume = (U1 - U0) * P0
        mix = sum((uni["l"][k] - U1 * uni["p"][k] / U0) * (tot["p"][k] / uni["p"][k]) for k in tot["p"])
        got = {s["label"]: s["value"] for s in pvm["data"]["steps"][1:-1]}
        for k, x in (("Price", price), ("Volume", volume), ("Mix", mix)):
            assert abs(got[k] - x) <= 1e-6 * R1, (k, got[k], x)
        assert abs(math.fsum(got.values()) - pvm["data"]["change"]["value"]) <= 1e-6 * R1
    sl = _rec(_case("ship2_ai"), "slope")
    rows = sl["data"]["rows"]
    assert [r["label"] for r in rows] == [k for k, _b in sorted(tot["l"].items(), key=lambda kv: (-kv[1], kv[0]))]
    for r in rows:
        assert _close(r["a"], tot["p"][r["label"]]) and _close(r["b"], tot["l"][r["label"]]), r
        assert r["change_text"] == NS._fmt_item(round(100.0 * (r["b"] - r["a"]) / r["a"], 6), "change", "%"), r
    it = {x["id"]: x for x in _case("ship2_ai")["scenarios"]["items"]}
    assert abs(math.fsum(r["a"] for r in rows) - it["headline.prior"]["value"]) <= 1e-6 * R0
    assert abs(math.fsum(r["b"] for r in rows) - it["headline.latest"]["value"]) <= 1e-6 * R1
    del pd


def test_calendar_recomputes_from_the_download_and_matches_the_trend_series():
    # ship2: total revenue by month (sequential); reviews: rows by month; fx: the month's average rate (weekend zeros
    # read as no value) as its percent change from the month before (diverging)
    for name, measure, date in (("ship2", "revenue", "order_date"), ("reviews", None, "review_date"),
                                ("fx", "value", "ref_date")):
        rep = _case(name)
        rec = _rec(rep, "calendar_heatmap")
        df = _clean(rep)
        mon = df[date].str[:7]
        if measure is None:
            by = {m: (float(n), int(n)) for m, n in mon.value_counts().items()}
        else:
            v = _num(df[measure])
            if name == "fx":
                v = v.where(v != 0)
            ok = v.notna()
            agg = v[ok].groupby(mon[ok]).agg(["sum", "mean", "count"])
            by = {m: ((r["sum"] if name != "fx" else r["mean"]), int(r["count"])) for m, r in agg.iterrows()}
        years = rec["data"]["rows"]
        assert years == [str(y) for y in range(int(min(by)[:4]), int(max(by)[:4]) + 1)][-12:], years
        values, n = [], []
        for y in years:
            rv, rn = [], []
            for j in range(12):
                m = "%s-%02d" % (y, j + 1)
                if name != "fx":
                    x = by.get(m)
                    rv.append(x[0] if x else None)
                    rn.append(x[1] if x else 0)
                else:
                    p = NB._shift_month(m, -1)
                    if m in by and p in by and by[p][0] > 0:
                        rv.append(100.0 * (by[m][0] / by[p][0] - 1.0))
                        rn.append(min(by[m][1], by[p][1]))
                    else:
                        rv.append(None)
                        rn.append(0)
            values.append(rv)
            n.append(rn)
        fmt = (lambda x: NS._fmt_item(round(x, 6), "change", "%")) if name == "fx" else NB._fmt
        _grid_equal(rec, values, n, fmt, "diverging" if name == "fx" else "sequential")
        # the spec's reconciliation: each month's total (or average) equals the engine's own monthly series
        key = {"ship2": "trend.total:revenue", "reviews": "trend.volume", "fx": "trend.value"}[name]
        tr = next(c for c in rep["charts"] if c["id"] == key)["data"]
        over = [(m, x) for m, x in zip(tr["months"], tr["values"]) if x is not None and m in by]
        assert over and all(_close(by[m][0], x) for m, x in over), key
        assert rec["section"] == "headline", (name, rec["section"])       # the measure is the primary claim's
        assert rec["anchors"] == ["finding:" + next(c for c in rep["charts"] if c["id"] == key)["finding_ids"][0]]


def _size_order(df, col, measure, date="order_date"):
    """WAVE 4, segments by size (nl_viz.Ctx.levels with the chart's measure): a flow's levels by the sum of |measure| over
    the latest 12 months, then by rows, then by name (they were by rows, then name). Without a measure: by rows."""
    rows = df[col].value_counts()
    size = {}
    if measure is not None:
        mon = df[date].str[:7]
        last = sorted(set(mon))[-1]
        keep = mon >= NB._shift_month(last, -11)
        v = _num(df[measure]).abs()
        ok = keep & v.notna()
        size = v[ok].groupby(df[col][ok]).sum().to_dict()
    return sorted(rows.index, key=lambda k: (-size.get(k, 0.0), -rows[k], k))


def test_change_heatmap_recomputes_and_its_levels_add_up_to_the_engine_series():
    rep = _case("stores_ai")
    rec = _rec(rep, "change_heatmap")
    df = _clean(rep)
    mon = df["order_date"].str[:7]
    v = _num(df["revenue"])
    levels = _size_order(df, "store", "revenue")
    assert rec["data"]["rows"] == levels, rec["data"]["rows"]
    months = sorted(set(mon))
    cols = [m for m in NB._month_range(months[0], months[-1]) if NB._shift_month(m, -12) >= months[0]][-24:]
    assert rec["data"]["cols"] == cols, rec["data"]["cols"]
    values, n = [], []
    for g in levels:
        rv, rn = [], []
        for m in cols:
            p = NB._shift_month(m, -12)
            a, b = (df["store"] == g) & (mon == p), (df["store"] == g) & (mon == m)
            na, nb = int(a.sum()), int(b.sum())
            sa, sb = float(v[a].sum()), float(v[b].sum())
            if not na or not nb or (min(na, nb) >= 5 and sa <= 0):
                rv.append(None)
                rn.append(0)
            else:
                rv.append(100.0 * (sb / sa - 1.0) if sa > 0 else None)
                rn.append(min(na, nb))
        values.append(rv)
        n.append(rn)
    _grid_equal(rec, values, n, lambda x: NS._fmt_item(round(x, 6), "change", "%"), "diverging")
    # the spec's reconciliation: each level's months add up to the engine's monthly series (all levels together)
    tr = next(c for c in rep["charts"] if c["id"] == "trend.total:revenue")["data"]
    for m, x in zip(tr["months"], tr["values"]):
        if x is not None:
            assert _close(float(v[mon == m].sum()), x), m
    assert rec["section"] == "drove" and rec["parent_grade"] == rep["scenarios"]["basis"]["grade"], rec["parent_grade"]
    # refused when fewer than half its cells rest on 5 or more rows (ship2's regions: 3 orders a region-month)
    ref = {r["chart"]: r["why"] for r in _viz(_case("ship2_ai"))["refused"]}
    assert ref["change_heatmap"].endswith("region has 17 of 96"), ref


def test_crosstab_recomputes_suppresses_under_5_and_prints_no_margins():
    rep = _case("stores_ai")
    recs = [c for c in _viz(rep)["charts"] if c["chart"] == "crosstab_heatmap"]
    assert len(recs) == 2, [c["id"] for c in _viz(rep)["charts"]]
    df = _clean(rep)
    for rec, a, b, measure in ((recs[0], "store", "channel", "revenue"), (recs[1], "channel", "stars", None)):
        ga, gb = df[a], df[b]
        la = _size_order(df, a, measure)
        lb = sorted(gb.unique(), key=lambda x: float(x)) if b == "stars" else _size_order(df, b, measure)
        assert rec["data"]["rows"] == la and rec["data"]["cols"] == lb, (rec["data"]["rows"], rec["data"]["cols"])
        v = _num(df[measure]) if measure else None
        values, n = [], []
        check = 0.0
        for x in la:
            rv, rn = [], []
            for y in lb:
                m = (ga == x) & (gb == y)
                k = int(m.sum())
                s = float(v[m].sum()) if v is not None else float(k)
                check += s
                rv.append(s if k else None)
                rn.append(k)
            values.append(rv)
            n.append(rn)
        fmt = NB._fmt if measure else (lambda x: format(int(x), ","))
        _grid_equal(rec, values, n, fmt, "sequential")
        # the spec's reconciliation: the cells (suppressed ones included) add up to the rows or the total read
        whole = float(v.sum()) if v is not None else float(len(df))
        assert _close(check, whole) and rec["inputs"]["rows"] == len(df), (check, whole)
        supp = sum(1 for row in n for k in row if 0 < k < 5)
        assert rec["suppressed"]["cells"] == supp and sum(t == "<5" for r in rec["data"]["text"] for t in r) == supp
        # no margins: the table is the grid, a row label and one column per level, no total row or column
        t = rec["table"]
        assert t["cols"] == [a] + lb and [r[0] for r in t["rows"]] == la, t["cols"]
        assert not any(("total" in str(c).lower()) for c in t["cols"]) and not any(r[0].lower() == "total" for r in t["rows"])
    assert recs[0]["suppressed"]["cells"] >= 1, "the store x channel crosstab has no cell under 5 rows to hide"


def _tokens(t):
    """A text's theme words, as the spec reads them: letters in any script after NFC, 3 or more characters starting with
    a letter, an apostrophe inside, common words left out (written here, not the adapter's own tokenizer)."""
    import unicodedata
    return [w for w in re.findall(r"[^\W\d_](?:[^\W\d_]|'){2,}", unicodedata.normalize("NFC", t).lower()) if w not in NB._STOP]


def test_theme_rating_heatmap_counts_the_themes_words_by_rating_and_all_is_the_analysis():
    rep = _case("reviews_ai")
    rec = _rec(rep, "theme_rating_heatmap")
    df = _clean(rep)
    texts = df["review_title"].str.strip()
    has = texts != ""
    stars = _num(df["rating"])[has]
    words = {i: set(_tokens(t)) for i, t in texts[has].items()}
    pairs = {i: set(" ".join(p) for p in zip(_tokens(t), _tokens(t)[1:])) for i, t in texts[has].items()}
    levels = sorted(set(stars.tolist()))
    assert rec["data"]["cols"] == [NB._fmt(x) for x in levels] + ["all"], rec["data"]["cols"]
    ana = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "themes")
    table = {r[0]: r for r in ana["table"]["rows"]}
    values, n = [], []
    for w in rec["data"]["rows"]:
        src = pairs if " " in w else words
        use = {i for i, s in src.items() if w in s}
        rv, rn = [], []
        for lv in levels:
            base = [i for i in stars.index if stars[i] == lv]
            rv.append(100.0 * len(use & set(base)) / len(base))
            rn.append(len(base))
        # the spec's reconciliation: each word's texts across the ratings add up to its count in the analysis
        assert len(use) == int(table[w][1].replace(",", "")), w
        rv.append(100.0 * len(use) / int(has.sum()))
        rn.append(int(has.sum()))
        values.append(rv)
        n.append(rn)
    _grid_equal(rec, values, n, lambda x: NB._fmt(x) + "%", "sequential")
    assert [r[-1] for r in rec["data"]["text"]] == [table[w][2] for w in rec["data"]["rows"]], "'all' is not the analysis"
    assert rec["anchors"] == ["analysis:%d" % (rep["ai_analyses"]["items"].index(ana) + 1)], rec["anchors"]
    # the review text is flagged (free text) and kept by the visitor, who ticked the box that sends it to the AI (an AI
    # plan ran): option B (owner, 1 Oct 2026), the theme chart reads it as its text, with every name protection
    # (test_final5_the_theme_chart_reads_a_kept_free_text_column_with_every_name_protection)
    got = [c for c in _viz(rep)["charts"] if c["chart"] == "theme_rating_heatmap"]
    assert [c["inputs"]["columns"] for c in got] == [["review_title", "rating"], ["review_text", "rating"]], \
        ([c["inputs"]["columns"] for c in got], _viz(rep)["refused"])
    assert not [r for r in _viz(rep)["refused"] if r["chart"] == "theme_rating_heatmap"], _viz(rep)["refused"]
    assert not _check(got[1]), _check(got[1])[:3]


def test_correlation_heatmap_is_the_corr_chart_and_recomputes():
    import numpy as np
    rep = _case("stores_corr")
    corr = next(c for c in rep["charts"] if c["id"] == "corr")["data"]
    recs = [c for c in _viz(rep)["charts"] if c["chart"] == "correlation_heatmap"]
    assert len(recs) == 2 and recs[0]["data"]["rows"] == corr["measures"] and \
        recs[1]["data"]["rows"] == ["revenue", "units", "cost"], [r["data"]["rows"] for r in recs]
    df = _clean(rep)
    win = rep["reproducibility"]["parameters"]["window"]
    inwin = df["order_date"].str[:7].isin(NB._month_range(win["start"], win["end"]))
    for rec in recs:
        ms = rec["data"]["rows"]
        for i, a in enumerate(ms):
            for j, b in enumerate(ms):
                x, y = _num(df.loc[inwin, a]), _num(df.loc[inwin, b])
                ok = x.notna() & y.notna()
                r = float(np.corrcoef(x[ok], y[ok])[0, 1])
                got = rec["data"]["values"][i][j]
                assert rec["data"]["n"][i][j] == int(ok.sum())
                if i == j:
                    assert got == 1 and rec["data"]["tier"][i][j] == 0 and rec["data"]["text"][i][j] == "1"
                    continue
                ci, cj = corr["measures"].index(a), corr["measures"].index(b)
                assert abs(got - corr["r"][ci][cj]) <= 1e-6 and abs(got - r) <= 1e-6, (a, b, got, r)
                k = 1 if abs(r) < 0.3 else 2 if abs(r) < 0.6 else 3
                assert rec["data"]["tier"][i][j] == (k if r > 0 else -k), (a, b, r)
        assert rec["anchors"] == ["chart:corr"] and rec["measure"]["kind"] == "correlation"
    # three heatmaps at most: the stores plan asks for a fourth (the correlation) after three others
    ref = {r["chart"]: r["why"] for r in _viz(_case("stores_ai"))["refused"]}
    assert ref.get("correlation_heatmap") == _nv().R_HEAT, ref


def _boot(v, seed=20260925, B=999):
    import numpy as np
    rng = np.random.default_rng(seed)
    m = v[rng.integers(0, len(v), size=(B, len(v)))].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def test_group_ranges_are_the_compare_analysis_and_recompute():
    import numpy as np
    for name, seg in (("ship2", "region"), ("stores_ai", "store")):
        rep = _case(name)
        rec = _rec(rep, "group_ranges")
        df = _clean(rep)
        v = _num(df["revenue"])
        for r in rec["data"]["rows"]:
            x = v[df[seg] == r["label"]].to_numpy(dtype=float)
            lo, hi = _boot(x)
            assert r["n"] == len(x) >= 5 and _close(r["center"], x.mean()) and _close(r["median"], float(np.median(x)))
            assert _close(r["lo"], lo) and _close(r["hi"], hi), (r, lo, hi)
            assert r["texts"] == {"n": format(len(x), ","), "center": NB._fmt(x.mean()), "lo": NB._fmt(lo),
                                  "hi": NB._fmt(hi), "median": NB._fmt(float(np.median(x)))}, r["texts"]
        small = df[seg].value_counts()
        assert {r["label"] for r in rec["data"]["rows"]} == {k for k, c in small.items() if c >= 5}
    # the plan's own compare analysis (stores): the same rows, to its printed digits, and anchored to it
    rep = _case("stores_ai")
    rec = _rec(rep, "group_ranges")
    ana = rep["ai_analyses"]["items"][0]
    assert ana["type"] == "compare" and rec["anchors"] == ["analysis:1"], rec["anchors"]
    assert [row[:5] for row in ana["table"]["rows"]] == rec["table"]["rows"], (ana["table"]["rows"][:2], rec["table"]["rows"][:2])
    assert rec["suppressed"]["cells"] == 1 and "Uptown" not in json.dumps(rec), rec["suppressed"]


def test_pareto_recomputes_k80_and_keeps_levels_under_5_rows_in_other():
    for name, seg, measure in (("stores_ai", "store", "revenue"), ("reviews_ai", "brand", None)):
        rep = _case(name)
        rec = _rec(rep, "pareto")
        df = _clean(rep)
        vals = _num(df[measure]) if measure else None
        tot = {k: (float(vals[df[seg] == k].sum()) if measure else float((df[seg] == k).sum())) for k in set(df[seg])}
        rows = df[seg].value_counts()
        whole = sum(tot.values())
        allr = sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))
        bars = [(k, x) for k, x in allr if rows[k] >= 5][:20]
        # 'other' rests on 5 or more rows: the smallest bars join it until it does (review of the chart registry)
        n_rows = int(sum(rows[k] for k in tot))
        merged = 0
        while bars and 0 < n_rows - sum(int(rows[k]) for k, _x in bars) < 5:
            bars.pop()
            merged += 1
        d = rec["data"]
        assert [(b["label"], round(b["value"], 4)) for b in d["bars"]] == [(k, round(x, 4)) for k, x in bars], d["bars"][:3]
        run = 0.0
        for b, (_k, x) in zip(d["bars"], bars):
            run += x
            assert abs(b["cum_pct"] - 100.0 * run / whole) <= 1e-4 and b["cum_text"] == NB._pct_text(100.0 * run / whole)
        acc, k80 = 0.0, None
        for i, (_k, x) in enumerate(allr):
            acc += x
            if 100.0 * acc / whole >= 80:
                k80 = i + 1
                break
        assert d["k80"]["k"] == k80 and d["k80"]["of"] == len(tot), d["k80"]
        # the spec's reconciliation: the bars and 'other' add up to total.value
        other = d["other"]["value"] if d["other"] else 0.0
        assert abs(math.fsum([b["value"] for b in d["bars"]] + [other]) - whole) <= 1e-6 * whole and _close(d["total"]["value"], whole)
        small = [k for k in tot if rows[k] < 5]
        assert rec["suppressed"]["cells"] == len(small) and not ({b["label"] for b in d["bars"]} & set(small))
        o_rows = n_rows - sum(int(rows[b["label"]]) for b in d["bars"])
        assert o_rows == 0 or o_rows >= 5, (name, o_rows)
        assert (merged > 0) == ("so that it rests on 5 or more rows" in rec["suppressed"]["why"]), (merged, rec["suppressed"])
    # a category with fewer than 8 levels: refused with the limit's reason (ship2's regions)
    ref = {r["chart"]: r["why"] for r in _viz(_case("ship2_ai"))["refused"]}
    assert ref["pareto"] == SPEC["chart_limits"]["example_ship2"][8]["why"], ref


# ------------------------------------------------------------------------ small cells and privacy
def test_no_printed_figure_rests_on_fewer_than_5_rows():
    for name in CASES:
        for c in _viz(_case(name))["charts"]:
            d = c["data"]
            if c["kind"] == "heatmap":
                for i, row in enumerate(d["values"]):
                    for j, v in enumerate(row):
                        if v is not None:
                            assert d["n"][i][j] >= 5, (name, c["id"], i, j)
                        else:
                            assert d["text"][i][j] in ("", "<5"), (name, c["id"], d["text"][i][j])
            elif c["kind"] == "dot_range":
                assert all(r["n"] >= 5 for r in d["rows"]), (name, c["id"])


def test_a_waterfall_and_a_slope_fold_a_level_under_5_rows_into_other():
    rep = _case("stores_ai")
    sc = rep["scenarios"]
    assert "Uptown" in sc["basis"]["segment"]["entered"] and "Mall" in sc["basis"]["segment"]["exited"], sc["basis"]
    df = _clean(rep)
    pm, lm = _windows(rep)
    mon = df["order_date"].str[:7]
    up = int(((df["store"] == "Uptown") & mon.isin(lm)).sum())
    assert 0 < up < 5, up
    for chart in ("contribution_waterfall", "slope"):
        rec = _rec(rep, chart)
        labels = [s["label"] for s in rec["data"]["steps"]] if chart != "slope" else [r["label"] for r in rec["data"]["rows"]]
        assert not any(l.startswith("Uptown") for l in labels), labels
        assert "other" in labels and "Mall (left)" in labels, labels
        assert rec["suppressed"]["cells"] == 1 and "counted only in 'other'" in rec["suppressed"]["why"], rec["suppressed"]
        assert "Uptown" not in json.dumps(rec), chart
    # 'other' rests on 5 or more rows: Uptown's 3 orders are folded together with the smallest level in both windows
    wf = _rec(rep, "contribution_waterfall")
    it = {x["id"]: x for x in sc["items"]}
    other = next(s for s in wf["data"]["steps"] if s["label"] == "other")
    both = [x for x in sc["items"] if x["group"] == "contribution" and x["id"].endswith(".change")
            and x["segment"] not in ("Uptown", "Mall")]
    smallest = min(both, key=lambda x: (abs(x["value"]), x["segment"]))
    assert abs(other["value"] - (it["contribution.uptown.change"]["value"] + smallest["value"])) <= 1e-6, other
    assert smallest["segment"] not in [s["label"] for s in wf["data"]["steps"]]


def _people(rep):
    df = __import__("pandas").read_csv(SHIP2, dtype=str, keep_default_na=False)
    return [v for c in ("customer_name", "email") for v in df[c].unique().tolist() if len(v) >= 4]


def test_privacy_no_flagged_column_in_any_role_or_label():
    charts = SHIP2_CHARTS + [{"kind": "pareto", "columns": ["customer_name", "revenue"], "why": "top customers"},
                             {"kind": "crosstab_heatmap", "columns": ["region", "email"], "why": "x"},
                             {"kind": "group_ranges", "columns": ["revenue", "Customer_Name"], "why": "x"}]
    for dec in ("withhold", "code", "keep"):
        rep = NB.run(_read(SHIP2), "ship2_privacy_orders.csv", "",
                     {"__plan__": dict(SHIP2_PLAN, charts=charts), "customer_name": dec, "email": dec}, SHIP2_AS_OF)
        assert rep["ok"], rep["error"]
        v = _viz(rep)
        assert {c["chart"] for c in v["charts"]} >= {"contribution_waterfall", "slope"}, v
        refused = [r for r in v["refused"] if r["why"] == _nv().R_PERSONAL]
        assert len(refused) == 3, (dec, v["refused"])
        if dec == "withhold":
            assert all("a column you withheld" in r["columns"] for r in refused), refused
        blob = json.dumps(v["charts"] + [c for c in rep["charts"] if c["rule"] == "V"], ensure_ascii=False)
        names = ("customer_name", "email", "Customer_Name")
        assert not any(n in blob for n in names), (dec, [n for n in names if n in blob])
        leaked = [p for p in _people(rep) if p in blob]
        assert not leaked, (dec, leaked[:3])
        if dec == "withhold":
            assert not any(n in json.dumps(v, ensure_ascii=False) for n in names), v["refused"]
        # the results the report writer receives hold no flagged column either
        res = json.dumps(NB.results_for_ai(rep)["charts"], ensure_ascii=False)
        assert not any(n in res for n in names) and not any(p in res for p in _people(rep)[:50])
    # the planner's chart limits never name or count a flagged column, whatever the visitor chose
    for dec in (None, {"customer_name": "keep", "email": "keep"}, {"customer_name": "code", "email": "code"}):
        prof = NB.profile_for_ai(_read(SHIP2), "ship2_privacy_orders.csv", decisions=dec)
        lim = json.dumps(prof["chart_limits"])
        assert "customer_name" not in lim and "email" not in lim, (dec, lim)
        assert [x["why"] for x in prof["chart_limits"]] == [x["why"] for x in SPEC["chart_limits"]["example_ship2"]], dec


# ------------------------------------------------------------------------ the plan's charts[]
def test_directive_validation_refuses_with_the_engines_reasons_and_never_as_a_plan_signal():
    nv = _nv()
    heat = [{"kind": "calendar_heatmap", "columns": [], "why": "a"},
            {"kind": "calendar_heatmap", "columns": ["units"], "why": "b"},
            {"kind": "calendar_heatmap", "columns": ["revenue"], "why": "c"},
            {"kind": "calendar_heatmap", "columns": ["revenue"], "why": "the same again"}]
    items = [{"kind": "sankey", "columns": ["region"], "why": ""},
             {"kind": "pareto", "columns": [], "why": ""},
             {"kind": "slope", "columns": ["warehouse"], "why": ""},
             {"kind": "group_ranges", "columns": ["region", "revenue"], "why": ""},
             {"kind": "contribution_waterfall", "columns": ["units", "revenue"], "why": ""},
             "not an object"] + heat + [
             {"kind": "crosstab_heatmap", "columns": ["region", "region"], "why": "x"}]
    plan = dict(SHIP2_PLAN, charts=items)
    rep = NB.run(_read(SHIP2), "ship2_privacy_orders.csv", "", {"__plan__": plan}, SHIP2_AS_OF)
    ref = [(r["chart"], r["why"]) for r in _viz(rep)["refused"]]
    built = [c["chart"] for c in _viz(rep)["charts"]]
    assert built == ["calendar_heatmap", "calendar_heatmap", "calendar_heatmap"], built
    want = [("sankey", nv.R_MENU),
            ("pareto", nv.R_ARGS % "[category, total?]"),
            ("slope", nv.R_MISSING),
            ("group_ranges", "region is not a number column the engine reads"),
            ("contribution_waterfall", "units is not a category column"),
            ("", nv.R_MENU)]
    assert ref[:6] == want, ref[:6]
    assert ("calendar_heatmap", nv.R_TWICE) in ref and ("crosstab_heatmap", "the same column twice") in ref, ref
    # at most 3 heatmaps, and at most 8 charts
    four = [{"kind": "calendar_heatmap", "columns": [m], "why": ""} for m in ("revenue", "units")] + \
        [{"kind": "calendar_heatmap", "columns": [], "why": ""}] + [{"kind": "change_heatmap", "columns": ["region"], "why": ""}]
    many = [{"kind": k, "columns": c, "why": ""} for k, c in (
        ("contribution_waterfall", ["region"]), ("contribution_waterfall", ["region", "revenue"]), ("slope", ["region"]),
        ("slope", ["region", "revenue"]), ("pvm_waterfall", ["revenue", "units"]),
        ("pvm_waterfall", ["revenue", "units", "region"]), ("group_ranges", ["revenue", "region"]),
        ("group_ranges", ["units", "region"]), ("calendar_heatmap", ["revenue"]))]
    rep2 = NB.run(_read(SHIP2), "ship2_privacy_orders.csv", "", {"__plan__": dict(SHIP2_PLAN, charts=many)}, SHIP2_AS_OF)
    assert len(_viz(rep2)["charts"]) == 8 and _viz(rep2)["refused"] == [
        {"chart": "calendar_heatmap", "columns": ["revenue"], "why": nv.R_MAX, "chosen_by": "ai"}], _viz(rep2)["refused"]
    stores_heat = [x for x in _viz(_case("stores_ai"))["refused"] if x["why"] == nv.R_HEAT]
    assert len(stores_heat) == 1, _viz(_case("stores_ai"))["refused"]
    del four
    # a chart whose limit says ok false is refused with the limit's why (ship2: pareto, correlation)
    ai = {r["chart"]: r["why"] for r in _viz(_case("ship2_ai"))["refused"]}
    lim = {x["chart"]: x["why"] for x in SPEC["chart_limits"]["example_ship2"]}
    assert ai["pareto"] == lim["pareto"] and ai["change_heatmap"] == lim["change_heatmap"], ai
    # the AI's why is kept (cut to 200 at a word), an engine pick's starts "Chosen by the engine: "
    assert [c["why"] for c in _viz(_case("ship2_ai"))["charts"]][:2] == ["What moved revenue.", "Price or volume?"]
    # _validate_plan reads 12 items, cuts each to its caps, and refuses nothing (a refusal is never a plan signal)
    raw = dict(SHIP2_PLAN, charts=[{"kind": "pareto" * 10, "columns": ["x" * 200] * 20, "why": "word " * 80}] * 15)
    vp, refused = NB._validate_plan(raw, ["order_date", "region", "revenue", "units"])
    assert len(vp["charts"]) == 12 and refused == [], (len(vp["charts"]), refused)
    it = vp["charts"][0]
    assert len(it["kind"]) == 40 and len(it["columns"]) == 12 and all(len(c) == 120 for c in it["columns"])
    assert len(it["why"]) <= 200 and it["why"].endswith("…") and not it["why"].endswith(" …"), it["why"]
    base = NB.run(_read(SHIP2), "ship2_privacy_orders.csv", "", {"__plan__": SHIP2_PLAN}, SHIP2_AS_OF)
    assert rep["plan_signals"] == base["plan_signals"] == [], rep["plan_signals"]
    assert rep["ai_plan"]["refused"] == base["ai_plan"]["refused"], rep["ai_plan"]["refused"]


def test_viz_is_nan_free_and_byte_identical_across_runs_and_hash_seeds():
    for name in ("ship2", "ship2_ai", "stores_ai"):
        make, fname, dec, as_of = CASES[name]
        a = json.dumps(_viz(_case(name)), allow_nan=False, ensure_ascii=False)
        b = json.dumps(NB.run(make(), fname, "", copy.deepcopy(dec), as_of)["viz"], allow_nan=False, ensure_ascii=False)
        assert a == b, name
        json.loads(NB.run_json(make(), fname, "", json.dumps(dec) if dec else None, as_of))
    code = ("import json,os,sys; os.environ['NL_BROWSER_STRICT']='1'; sys.path.insert(0,%r); sys.path.insert(0,%r); "
            "import nl_browser as NB; r=NB.run(open(%r,'rb').read(),'ship2_privacy_orders.csv','',"
            "{'__plan__': json.loads(%r)},%r); sys.stdout.write(json.dumps(r['viz'],ensure_ascii=False))" % (
                ENGINE_ROOT, ADAPTER_DIR, SHIP2, json.dumps(dict(SHIP2_PLAN, charts=SHIP2_CHARTS)), SHIP2_AS_OF))
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=300)
        assert out.returncode == 0, out.stderr[-500:]
        assert out.stdout == json.dumps(_viz(_case("ship2_ai")), ensure_ascii=False), "differs under PYTHONHASHSEED=%s" % seed


# ------------------------------------------------------------------------ what the report writer receives
def test_results_for_ai_carries_the_records_whole_after_the_analyses_charts_within_caps():
    nv = _nv()
    for name in CASES:
        rep = _case(name)
        res = NB.results_for_ai(rep)
        assert res["ok"] and len(json.dumps(res)) <= NB.RESULTS_MAX_BYTES, name
        ch = res["charts"]
        assert len(ch) <= nv.AI_CHARTS_MAX, (name, len(ch))
        kinds = [c.get("kind") for c in ch]
        first_viz = next((i for i, c in enumerate(ch) if nv.is_record(c)), len(ch))
        assert all(not nv.is_record(c) for c in ch[:first_viz]) and all(nv.is_record(c) for c in ch[first_viz:]), kinds
        assert first_viz <= 6
        want = _viz(rep)["charts"][:nv.AI_CHARTS_MAX - first_viz]
        # as the record leaves the adapter (nl_viz.for_sending): with a suppressed cell, inputs.rows is null
        sent = [json.loads(json.dumps(r).replace(rep["input"]["name"], NB.FILE_WORD)) for r in want]
        for r in sent:
            if r["suppressed"]["cells"] > 0:
                r["inputs"]["rows"] = None
        assert ch[first_viz:] == sent, name
    # the model's card, which the worker makes from each record: never the data, a table of at most 12 rows
    rep = _case("reviews_ai")
    cards = nv.cards_for_ai(_viz(rep))
    assert len(cards) == len(_viz(rep)["charts"]) and all("data" not in c for c in cards)
    assert all(len(c["table"]["rows"]) <= 12 for c in cards) and any(len(r["table"]["rows"]) > 12 for r in _viz(rep)["charts"])
    assert [set(c) for c in cards][0] == {"n", "id", "chart", "kind", "title", "section", "supports", "anchors", "grade",
                                          "parent_grade", "summary", "table"}


def test_the_byte_budget_drops_viz_records_after_the_scenario_items_and_before_the_analyses():
    nv = _nv()
    rep = copy.deepcopy(_case("fx"))
    rep["viz"]["charts"] = [copy.deepcopy(rep["viz"]["charts"][0]) for _ in range(9)]
    for i, c in enumerate(rep["viz"]["charts"]):
        c["id"] = "viz.%d.calendar_heatmap" % (i + 1)
    whole_old = NB.RESULTS_MAX_BYTES
    NB.RESULTS_MAX_BYTES = 10 ** 9
    try:
        whole = NB.results_for_ai(rep)
    finally:
        NB.RESULTS_MAX_BYTES = whole_old
    n_ana = sum(1 for c in whole["charts"] if not nv.is_record(c))
    assert n_ana == 2 and len(whole["charts"]) == nv.AI_CHARTS_MAX, [c.get("kind") for c in whole["charts"]]
    size = len(json.dumps(whole))
    cut = size - 15000                         # room for all but about two records, once every scenario item is gone
    NB.RESULTS_MAX_BYTES = cut
    try:
        got = NB.results_for_ai(rep)
    finally:
        NB.RESULTS_MAX_BYTES = whole_old
    assert len(json.dumps(got)) <= cut
    assert got["scenarios"]["items"] == [], "the scenario items go first"
    assert got["analyses"] == whole["analyses"] and [c for c in got["charts"] if not nv.is_record(c)] == \
        [c for c in whole["charts"] if not nv.is_record(c)], "an analysis went before the viz records"
    kept = [c["id"] for c in got["charts"] if nv.is_record(c)]
    assert kept == [c["id"] for c in whole["charts"] if nv.is_record(c)][:len(kept)] and 0 < len(kept) < 8, kept


def test_chart_limits_ship2_is_the_specs_example_and_fx_and_reviews_say_what_they_lack():
    prof = NB.profile_for_ai(_read(SHIP2), "ship2_privacy_orders.csv")
    assert prof["chart_limits"] == SPEC["chart_limits"]["example_ship2"], json.dumps(prof["chart_limits"], indent=1)
    fx = {x["chart"]: x for x in NB.profile_for_ai(_read(FX), "fx_usd_cad.csv")["chart_limits"]}
    rv = {x["chart"]: x for x in NB.profile_for_ai(_read(REVIEWS), "reviews_synthetic.csv")["chart_limits"]}
    for lim in (fx, rv):
        assert list(lim) == MENU and len(lim) <= 16
        for c, x in lim.items():
            assert set(x) == {"chart", "ok", "why"} and isinstance(x["ok"], bool) and len(x["why"]) <= 160, x
            assert x["why"].startswith(SPEC["menu"][c]["limit_needs"] + "; "), x
    assert [c for c in MENU if fx[c]["ok"]] == ["calendar_heatmap"], fx
    assert fx["calendar_heatmap"]["why"].endswith("the file spans 116 months (2017-01 to 2026-08)"), fx["calendar_heatmap"]
    assert fx["correlation_heatmap"]["why"].endswith("the file has 1 (VALUE)"), fx["correlation_heatmap"]
    assert [c for c in MENU if rv[c]["ok"]] == ["contribution_waterfall", "calendar_heatmap", "change_heatmap",
                                              "crosstab_heatmap", "group_ranges", "pareto", "slope"], rv
    # the review text is flagged (free text), withheld by default: never counted as the free-text column
    assert rv["theme_rating_heatmap"]["why"].endswith("no free-text column") and "review_text" not in json.dumps(rv)
    assert rv["change_heatmap"]["why"].endswith("verified_purchase has 71 of 78"), rv["change_heatmap"]
    # a yearly panel: its time column is a year column, which has no months, so no chart that reads months says ok
    rows = ["country,year,co2"] + ["%s,%d,%d" % (c, y, 100 + y % 7) for c in ("A", "B", "C") for y in range(1990, 2024)]
    yl = {x["chart"]: x for x in NB.profile_for_ai(("\n".join(rows) + "\n").encode(), "co2.csv")["chart_limits"]}
    for c in ("contribution_waterfall", "pvm_waterfall", "calendar_heatmap", "change_heatmap", "slope"):
        assert not yl[c]["ok"] and yl[c]["why"].endswith("no column the engine reads as dates"), yl[c]
    # what the engine then builds from the same files with no chart asked for
    assert [c["chart"] for c in _viz(_case("fx"))["charts"]] == ["calendar_heatmap"]
    assert [c["chart"] for c in _viz(_case("reviews"))["charts"]] == ["contribution_waterfall", "calendar_heatmap",
                                                                      "crosstab_heatmap", "group_ranges"]


def test_a_record_over_its_byte_cap_is_trimmed_and_says_so():
    nv = _nv()
    make, fname, dec, as_of = CASES["fx"]
    old = nv.HEATMAP_BYTES
    nv.HEATMAP_BYTES = 4000
    try:
        rep = NB.run(make(), fname, "", copy.deepcopy(dec), as_of)
    finally:
        nv.HEATMAP_BYTES = old
    rec = _rec(rep, "calendar_heatmap")
    years = rec["data"]["rows"]
    assert years[-1] == "2026" and years[0] > "2017" and VS.js_bytes(rec) <= 4000, (years, VS.js_bytes(rec))
    assert rec["subtitle"].endswith("; %s to 2026 shown: the chart's size limit" % years[0]), rec["subtitle"]
    assert rec["inputs"]["months"][0] == years[0] + "-01" or rec["inputs"]["months"][0] == years[0] + "-02"
    full = _rec(_case("fx"), "calendar_heatmap")
    k = len(full["data"]["rows"]) - len(years)
    shown = [[v for v in row] for row in full["data"]["values"][k:]]
    assert rec["data"]["values"] == shown and rec["data"]["tier"] == _div_tiers(shown)[0], "tiers are not on what is kept"
    sp = copy.deepcopy(SPEC)
    sp["caps"]["HEATMAP_BYTES"] = 4000                 # a heatmap: validate_spec checks it against HEATMAP_BYTES
    assert not VS.validate(rec, SPEC["schema"], SPEC["schema"]) and not VS.check_record(rec, sp)
    # a Pareto over its cap folds its last bars into other
    old = nv.CHART_BYTES
    nv.CHART_BYTES = 3000
    try:
        make, fname, dec, as_of = CASES["reviews_ai"]
        rep = NB.run(make(), fname, "", copy.deepcopy(dec), as_of)
    finally:
        nv.CHART_BYTES = old
    rec = _rec(rep, "pareto")
    assert len(rec["data"]["bars"]) < 20 and VS.js_bytes(rec) <= 3000 and "shown: the chart's size limit" in rec["subtitle"]
    assert rec["data"]["other"]["cum_pct"] == 100 and rec["data"]["k80"] == _rec(_case("reviews_ai"), "pareto")["data"]["k80"]


def test_rule_v_records_in_rep_charts():
    for name in CASES:
        rep = _case(name)
        v = [c for c in rep["charts"] if c["rule"] == "V"]
        assert [c["data"] for c in v] == _viz(rep)["charts"], name
        fids = {f["id"] for f in rep["findings"]}
        for c in v:
            assert set(c) == {"id", "rule", "type", "title", "view", "default_visible", "finding_ids", "why_shown",
                              "source", "data"} and c["type"] == "viz" and c["view"] == "manager" and c["default_visible"]
            assert c["id"] == c["data"]["id"] and c["title"] == c["data"]["title"] and c["why_shown"].strip()
            assert c["finding_ids"] == [a[8:] for a in c["data"]["anchors"] if a.startswith("finding:")] and \
                set(c["finding_ids"]) <= fids, c["finding_ids"]


DRIVER_LINE = "which segments drive a change is not computed in this release"


def test_the_driver_line_goes_when_a_contribution_waterfall_is_built():
    # integration pass, 30 Sep 2026: the report listed "#2b driver waterfall: which segments drive a change is not
    # computed in this release" beside the contribution waterfall the registry built. The line goes when one is built
    # (by the AI's pick or the engine's own), and stays, word for word, when none is.
    seen = {True: [], False: []}
    for name in CASES:
        rep = _case(name)
        wf = [c for c in _viz(rep)["charts"] if c["chart"] == "contribution_waterfall"]
        lines = [s for s in rep["charts_suppressed"] if s["rule"] == "#2b"]
        if wf:
            assert not lines, (name, lines)
            assert not any("not computed" in s["why"] and s["type"] == "driver_waterfall" for s in rep["charts_suppressed"])
            assert any(c["rule"] == "V" and c["data"]["chart"] == "contribution_waterfall" for c in rep["charts"]), name
        else:
            assert lines == [{"rule": "#2b", "type": "driver_waterfall", "why": DRIVER_LINE}], (name, lines)
        seen[bool(wf)].append(name)
    # both paths are exercised: the engine's pick (ship2, stores) and the AI's (ship2_ai, stores_ai), and files without
    assert {"ship2", "ship2_ai", "stores_ai"} <= set(seen[True]) and "fx" in seen[False], seen
    # the rule on its own: a report whose registry built none keeps the line; one that built it loses only that line
    nv = _nv()
    other = {"rule": "#3", "type": "forecast", "why": "x"}
    rep = {"charts_suppressed": [{"rule": "#2b", "type": "driver_waterfall", "why": DRIVER_LINE}, other]}
    nv.drop_driver_line(rep, {"charts": [{"chart": "pareto"}]})
    assert len(rep["charts_suppressed"]) == 2
    nv.drop_driver_line(rep, {"charts": [{"chart": "pareto"}, {"chart": "contribution_waterfall"}]})
    assert rep["charts_suppressed"] == [other], rep["charts_suppressed"]


def test_the_category_month_reason_names_the_waterfall_only_when_one_is_built():
    # pre-deploy pass, 30 Sep 2026: the category-by-month heatmap's reason said "which segments drive the change is
    # not computed in this release" beside the contribution waterfall that computes them. It now says the map counts
    # rows and flags no driver or reversal, and points at the waterfall only when the registry built one.
    nv = _nv()
    seen = {True: [], False: []}
    for name in CASES:
        rep = _case(name)
        wf = any(c["chart"] == "contribution_waterfall" for c in _viz(rep)["charts"])
        heat = [c for c in rep["charts"] if c["id"].startswith("catmonth.")]
        for c in heat:
            assert c["why_shown"] == NB.CATMONTH_WHY % (NB.CATMONTH_WATERFALL if wf else NB.CATMONTH_NO_WATERFALL), \
                (name, c["id"], c["why_shown"])
            assert "not computed" not in c["why_shown"], (name, c["why_shown"])
        if heat:
            seen[wf].append(name)
    assert seen[True] and seen[False], seen
    # the rule on its own: only a catmonth reason in its no-waterfall words changes, and only when a waterfall is built
    was = NB.CATMONTH_WHY % NB.CATMONTH_NO_WATERFALL
    rep = {"charts": [{"id": "catmonth.region", "why_shown": was}, {"id": "ranked.region", "why_shown": was},
                      {"id": "catmonth.other", "why_shown": "something else"}]}
    nv.mend_catmonth_why(rep, {"charts": [{"chart": "pareto"}]})
    assert [c["why_shown"] for c in rep["charts"]] == [was, was, "something else"], rep
    nv.mend_catmonth_why(rep, {"charts": [{"chart": "contribution_waterfall"}]})
    assert [c["why_shown"] for c in rep["charts"]] == [NB.CATMONTH_WHY % NB.CATMONTH_WATERFALL, was, "something else"], rep


def _brands_bytes() -> bytes:
    """24 months of 2,000 reviews: 6 departments, 400 brands of 5 reviews each (more values than the chart limits read
    as a category's levels) and a 1-5 star rating."""
    rng = random.Random(11)
    rows = []
    for i in range(2000):
        rows.append(["%d-%02d-%02d" % (2024 + (i % 24) // 12, i % 12 + 1, 1 + i % 28), "Department %d" % (i % 6 + 1),
                     "Brand %03d" % (i // 5 + 1), str(rng.randint(1, 5))])
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["review_date", "department", "brand", "rating"])
    w.writerows(rows)
    return out.getvalue().encode()


def test_a_pareto_refusal_says_why_the_column_it_names_is_not_charted():
    # pre-deploy pass, 30 Sep 2026: a Pareto of brand (the Amazon reviews: 3,401 values, typed entity) was refused with
    # the file's limit, "department has 6", naming another column. The refusal now says what keeps THAT column out,
    # then what the Pareto needs and the largest category the limits count; a column the limit itself names keeps it.
    nv = _nv()
    plan = {"goal": "Which brands draw the reviews?", "kind": "survey", "primary": "rating",
            "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                        {"name": "department", "semantic_type": "category", "role": "segment"},
                        {"name": "brand", "semantic_type": "entity", "role": "entity"},
                        {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"}],
            "operations": [], "analyses": [],
            "charts": [{"kind": "pareto", "columns": ["brand"], "why": "Which brands draw the reviews."},
                       {"kind": "pareto", "columns": ["department"], "why": "Which departments."}]}
    rep = NB.run(_brands_bytes(), "brands.csv", "", {"__plan__": plan}, "2026-01-15")
    assert rep["ok"], rep["error"]
    assert not rep["privacy"]["flagged"], rep["privacy"]["flagged"]
    # a column the plan types as an entity, of 8 to 5,000 levels, is a Pareto's category (review of the chart registry,
    # 30 Sep 2026): the top 20 and 'other'; department (6 levels) is refused by its own count
    rec = _rec(rep, "pareto")
    assert rec["inputs"]["columns"] == ["brand"] and len(rec["data"]["bars"]) == 20, rec["inputs"]
    assert rec["data"]["other"]["n_entities"] == 380 and rec["data"]["k80"]["of"] == 400, rec["data"]["other"]
    assert not _check(rec), _check(rec)[:3]
    ref = {r["columns"][0]: r["why"] for r in _viz(rep)["refused"] if r["chart"] == "pareto"}
    assert ref == {"department": "department has 6 levels; a Pareto needs 8 or more"}, ref
    # the planner's profile, before any plan, counts the brand column as a Pareto's category the plan may type
    lim = {x["chart"]: x for x in NB.profile_for_ai(_brands_bytes(), "brands.csv", as_of="2026-01-15")["chart_limits"]}
    assert lim["pareto"]["ok"] and lim["pareto"]["why"].endswith("brand has 400 levels, a category when the plan types it one"), lim["pareto"]
    # the same column the plan does not type: the refusal says what keeps it out
    plan2 = dict(plan, columns=[c for c in plan["columns"] if c["name"] != "brand"])
    rep = NB.run(_brands_bytes(), "brands.csv", "", {"__plan__": plan2}, "2026-01-15")
    ref = {r["columns"][0]: r["why"] for r in _viz(rep)["refused"] if r["chart"] == "pareto"}
    assert ref == {
        "brand": "brand has 400 values; past 300, a column is a category only when the plan types it a category or an "
                 "entity",
        "department": nv.REGISTRY["pareto"]["limit_needs"] + "; department has 6"}, ref
    assert not [c for c in _viz(rep)["charts"] if c["chart"] == "pareto"], "a Pareto was built"
    # a category the limits count, with fewer than 8 levels, that the limit does not name: its own level count
    lim = {"pareto": {"chart": "pareto", "ok": False, "why": nv.REGISTRY["pareto"]["limit_needs"] + "; region has 7"}}
    ctx = type("C", (), {})()
    ctx.lim_detail = {"usable": {"region": {"header": "region", "distinct": 7, "top_values": ["a"]},
                                 "channel": {"header": "channel", "distinct": 3, "top_values": ["b"]},
                                 "product": {"header": "product", "distinct": 50, "kind": "text"}},
                      "cats": ["region", "channel"]}
    ctx.header = lambda land: land
    assert nv._pareto_refusal(ctx, "channel", lim["pareto"]["why"]) == \
        "channel has 3 levels, and the Pareto needs a category of 8 or more levels (the largest, region, has 7 levels)"
    # a text column of 300 values or fewer that the profile lists no levels for: its values are too long (a column the
    # privacy scan reads as free text is flagged first, and a flagged column is refused as personal before this)
    assert nv._pareto_refusal(ctx, "product", lim["pareto"]["why"]) == \
        "product has values too long to count as a category's levels (the middle one is over 60 characters), and the " \
        "Pareto needs a category of 8 or more levels (the largest, region, has 7 levels)"
    assert nv._pareto_refusal(ctx, "region", lim["pareto"]["why"]) == lim["pareto"]["why"]
    # a column the limits never read (not in the detail) keeps the limit's own words
    assert nv._pareto_refusal(ctx, "gone", lim["pareto"]["why"]) == lim["pareto"]["why"]


def test_no_em_dash_and_every_string_within_its_cap():
    for name in CASES:
        blob = json.dumps(_viz(_case(name)), ensure_ascii=False)
        assert "\u2014" not in blob and "\u2013" not in blob, name


def _fixture():
    auto, ai = _case("ship2"), _case("ship2_ai")
    nv = _nv()
    return {"about": "The charts the engine emits for ship2 (tools/fixtures/scenarios/ship2_privacy_orders.csv, analysis "
                     "date %s, the personal columns withheld as by default): engine_pick with no chart asked for, "
                     "ai_pick for the plan's charts below (two of them refused). Records as rep.viz holds them and as "
                     "results_for_ai sends them; cards as the model sees them. Rewrite with: python "
                     "tools/test_nl_viz.py --write-fixture" % SHIP2_AS_OF,
            "spec_version": SPEC["version"], "engine_snapshot": auto["engine"]["snapshot"],
            "byte_caps": {"record": nv.CHART_BYTES, "heatmap": nv.HEATMAP_BYTES},
            "engine_pick": {"viz": _viz(auto)},
            "ai_pick": {"plan_charts": SHIP2_CHARTS, "viz": _viz(ai), "cards": nv.cards_for_ai(_viz(ai))}}


def test_engine_charts_ship2_fixture_is_what_the_engine_emits():
    with open(FIXTURE, encoding="utf-8") as fh:
        want = json.load(fh)
    got = json.loads(json.dumps(_fixture(), ensure_ascii=False))
    assert got == want, "tools/fixtures/viz/engine-charts-ship2.json differs from the engine's charts; rewrite it " \
                        "with --write-fixture if the change is intended"
    for r in want["engine_pick"]["viz"]["charts"] + want["ai_pick"]["viz"]["charts"]:
        assert not _check(r), r["id"]


# ------------------------------------------------------------------------ the chart review of 30 Sep 2026
# Synthetic files for the review's findings (tools/fixtures/review-viz/ holds the review's own repro scripts, rerun
# against the fix). Every name below is made up.
STAFF = ["Sarah", "Priya", "Mohammed", "Jonas"]
GUEST_FIRST = ["Emily", "Rashid", "Olga", "Tariq", "Hannah", "Diego", "Mei", "Kwame"]
GUEST_LAST = ["Jones", "Khan", "Petrova", "Hassan", "Schmidt", "Lopez", "Wong", "Mensah"]
TITLE_TEMPLATES = ["Thanks {s}!", "{s} was great", "Ask for {s}", "Rude staff", "Lovely room", "Too noisy", "{s} rocks",
                   "Great value", "Never again", "Thank you {s}"]
TITLES_PLAN = {"goal": "What do guests say at each rating?", "kind": "survey",
               "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                           {"name": "rating", "semantic_type": "rating", "role": "target"},
                           {"name": "department", "semantic_type": "category", "role": "segment"},
                           {"name": "review_title", "semantic_type": "free_text", "role": "driver"}],
               "operations": [], "analyses": [{"type": "themes", "columns": ["review_title"]}],
               "charts": [{"kind": "theme_rating_heatmap", "columns": ["review_title", "rating"], "why": "Words by rating."}]}


def _csv(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _titles_bytes(staff_col=False, unrated=0, templates=TITLE_TEMPLATES, rated=(1, 5)):
    """360 hotel reviews over 24 months: a guest's name (flagged as personal), a rating, a department and a short title
    that names a member of staff in half the templates ("Thanks Sarah!", "Ask for Priya"); staff_col adds the member of
    staff as a column of its own; the first `unrated` reviews have no rating."""
    rng = random.Random(11)
    rows = []
    for i in range(360):
        k = i // 15
        st, gf, gl = rng.choice(STAFF), rng.choice(GUEST_FIRST), rng.choice(GUEST_LAST)
        r, title = rng.randint(*rated), rng.choice(templates).format(s=st)
        rows.append(["%04d-%02d-%02d" % (2024 + k // 12, k % 12 + 1, rng.randint(1, 28)), gf + " " + gl,
                     "" if i < unrated else str(r), rng.choice(["rooms", "food", "spa"]), title] + ([st] if staff_col else []))
    return _csv(["review_date", "guest", "rating", "department", "review_title"] + (["staff_member"] if staff_col else []), rows)


def _words(rec_rows):
    return set(w for x in rec_rows for w in str(x).split())


def test_review_names_are_never_themes_under_withhold_code_and_keep():
    # review of the chart registry (30 Sep 2026): review titles that named staff put "sarah", "mohammed", "jonas" and
    # "priya" among the theme words, in the chart, the themes analysis and results_for_ai, whatever the visitor chose
    names = {n.lower() for n in STAFF + GUEST_FIRST + GUEST_LAST}
    for staff_col in (True, False):
        for dec in ("withhold", "code", "keep"):
            d = {"__plan__": TITLES_PLAN, "guest": dec}
            if staff_col:
                d["staff_member"] = dec
            rep = NB.run(_titles_bytes(staff_col), "titles.csv", "", d, "2026-01-15")
            assert rep["ok"], rep["error"]
            got = {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}
            assert got.get("guest") == dec and (not staff_col or got.get("staff_member") == dec), got
            rec = _rec(rep, "theme_rating_heatmap")
            ana = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "themes")
            words = _words(rec["data"]["rows"]) | _words(r[0] for r in ana["table"]["rows"])
            assert not words & names, (staff_col, dec, sorted(words & names))
            # the real themes stay
            assert {"great", "room", "value", "staff", "noisy"} <= words, (staff_col, dec, sorted(words))
            res = NB.results_for_ai(rep)
            blob = json.dumps(res["charts"] + res["tables"] + res["analyses"], ensure_ascii=False).lower()
            assert not [n for n in names if re.search(r"(?<![a-z])%s(?![a-z])" % n, blob)], (staff_col, dec)
            assert not _check(rec), _check(rec)[:3]
    # the review file's own words stay themes: none of its words is taken for a name, in the titles or the kept texts,
    # and the title chart still reads "great" and "controller"
    import pandas as pd
    df = pd.read_csv(REVIEWS, dtype=str, keep_default_na=False)
    for col in ("review_title", "review_text"):
        texts = [t for t in df[col].tolist() if t]
        assert not NB._proper_nouns(texts), (col, sorted(NB._proper_nouns(texts)))
        assert not {"great", "controller", "battery", "value", "price"} & NB._theme_drop(texts), col
    assert "controller" in _words(_rec(_case("reviews_ai"), "theme_rating_heatmap")["data"]["rows"])
    plan = dict(REVIEWS_PLAN, analyses=[{"type": "themes", "columns": ["review_text"]}], charts=[])
    rep = NB.run(_read(REVIEWS), "reviews_synthetic.csv", "", {"__plan__": plan, "review_text": "keep"}, "2026-09-30")
    ana = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "themes")
    texts = [t for t in _clean(rep)["review_text"].str.strip() if t]
    for w, c, _p in ana["table"]["rows"]:
        want = sum(1 for t in texts if w in ({" ".join(p) for p in zip(_tokens(t), _tokens(t)[1:])} if " " in w
                                             else set(_tokens(t))))
        assert int(c.replace(",", "")) == want, (w, c, want)


def _final5_reviews_bytes():
    """The synthetic reviews (fixtures/eval/make_reviews.py) with people in them, all invented here: a customer_name
    column (flagged by its name) whose surname "fairweather" some texts write in lower case, a given name in lower case
    ("emily", engine/first_names.txt), a word the texts always capitalise mid-sentence ("Quillon"), and a complaint that
    sits in the low ratings ("It stopped working after a week.")."""
    import pandas as pd
    df = pd.read_csv(REVIEWS, dtype=str, keep_default_na=False)
    people = ["Marisol Fairweather", "Tobias Quennell", "Ingrid Vasquez", "Odalys Brightwater", "Kenji Arborfield"]
    df.insert(1, "customer_name", [people[i % len(people)] for i in range(len(df))])
    rng = random.Random(20261001)
    texts = []
    for i, (t, r) in enumerate(zip(df["review_text"], df["rating"])):
        extra = []
        if r in ("1", "2") and rng.random() < 0.7:
            extra.append("It stopped working after a week.")
        if i % 7 == 0:
            extra.append("thanks to fairweather at the help desk.")
        if i % 9 == 0:
            extra.append("my sister emily loves it.")
        if i % 8 == 0:
            extra.append("Ask for Quillon at the counter.")
        texts.append(" ".join([t] + extra))
    df["review_text"] = texts
    return df.to_csv(index=False).encode("utf-8")


FINAL5_THEME_PLAN = dict(REVIEWS_PLAN, analyses=[{"type": "themes", "columns": ["review_text"]}],
                         columns=REVIEWS_PLAN["columns"] + [{"name": "review_text", "semantic_type": "free_text",
                                                             "role": "driver"}],
                         charts=[{"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"],
                                  "why": "What the low ratings complain about."}])
FINAL5_PEOPLE = {"marisol", "fairweather", "tobias", "quennell", "ingrid", "vasquez", "odalys", "brightwater", "kenji",
                 "arborfield", "emily", "quillon"}


def test_final5_the_theme_chart_reads_a_kept_free_text_column_with_every_name_protection():
    # option B (owner, 1 Oct 2026): the AI may read a personal column the visitor keeps and agrees to send (the page's
    # ticked box; an AI plan runs only after it). The final evaluation's reviews kept review_text, and the AI's
    # theme_rating_heatmap on it was refused ("a personal column is never charted"), so "what do customers complain
    # about" had no answer by rating. The theme chart (only it, and only as its text) may now read such a column, with
    # every name protection: the tokens of every OTHER flagged column's values, the given names, a word the texts write
    # as a name. A withheld or coded text is never read, and a kept column never plays another part.
    data = _final5_reviews_bytes()
    for dec in ("withhold", "code", "keep"):
        rep = NB.run(data, "reviews_people.csv", "", {"__plan__": FINAL5_THEME_PLAN, "review_text": "keep",
                                                      "customer_name": dec}, "2026-09-30")
        assert rep["ok"], rep["error"]
        got = {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}
        assert got.get("review_text") == "keep" and got.get("customer_name") == dec, got
        recs = [c for c in _viz(rep)["charts"] if c["chart"] == "theme_rating_heatmap"]
        assert len(recs) == 1, (dec, _viz(rep)["refused"])
        rec = recs[0]
        assert rec["inputs"]["columns"] == ["review_text", "rating"] and not _check(rec), (rec["inputs"], _check(rec)[:3])
        # a real complaint theme, and where it sits: the low ratings
        rows, cols = rec["data"]["rows"], rec["data"]["cols"]
        assert "stopped working" in rows, (dec, rows)
        sw = rec["data"]["values"][rows.index("stopped working")]
        assert sw[cols.index("1")] > 5 * sw[cols.index("5")], (cols, sw)
        # no person's name: not a token of the other flagged column, not a given name, not a word written as a name
        ana = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "themes")
        words = _words(rows) | _words(r[0] for r in ana["table"]["rows"])
        assert not words & FINAL5_PEOPLE, (dec, sorted(words & FINAL5_PEOPLE))
        # the chart counts the analysis's own words (its 'all' column is the analysis)
        assert [r[-1] for r in rec["data"]["text"]] == [r[2] for r in ana["table"]["rows"] if r[0] in rows], dec
        res = NB.results_for_ai(rep)
        sent = [c for c in res["charts"] if isinstance(c, dict) and c.get("chart") == "theme_rating_heatmap"]
        blob = json.dumps(res["charts"] + res["tables"] + res["analyses"], ensure_ascii=False).lower()
        assert len(sent) == 1 and not [n for n in FINAL5_PEOPLE if re.search(r"(?<![a-z])%s(?![a-z])" % n, blob)], dec
    # a withheld or coded text is never read; a kept column never plays the rating's part (nor any other chart's)
    for dec in ("withhold", "code"):
        rep = NB.run(data, "reviews_people.csv", "", {"__plan__": FINAL5_THEME_PLAN, "review_text": dec,
                                                      "customer_name": "withhold"}, "2026-09-30")
        ref = [r for r in _viz(rep)["refused"] if r["chart"] == "theme_rating_heatmap"]
        assert ref and ref[0]["why"] == _nv().R_PERSONAL and not [c for c in _viz(rep)["charts"]
                                                                 if c["chart"] == "theme_rating_heatmap"], (dec, ref)
    plan = dict(FINAL5_THEME_PLAN, charts=[{"kind": "theme_rating_heatmap", "columns": ["review_text", "customer_name"],
                                            "why": "x"},
                                           {"kind": "crosstab_heatmap", "columns": ["department", "customer_name"], "why": "x"}])
    rep = NB.run(data, "reviews_people.csv", "", {"__plan__": plan, "review_text": "keep", "customer_name": "keep"},
                 "2026-09-30")
    assert [r["why"] for r in _viz(rep)["refused"]] == [_nv().R_PERSONAL] * 2 and \
        not [c for c in _viz(rep)["charts"] if c["chosen_by"] == "ai"], _viz(rep)["refused"]


def test_the_engine_never_picks_themes_or_a_pareto_of_an_untyped_text_column():
    # the review: with no plan the engine charted a Pareto of the review titles, staff names among its bars, and with a
    # plan and no chart asked for it picked the theme heatmap of the same titles
    for d in (None, {"__plan__": dict(TITLES_PLAN, charts=[])}):
        rep = NB.run(_titles_bytes(), "titles.csv", "", copy.deepcopy(d), "2026-01-15")
        assert rep["ok"], rep["error"]
        picked = [c["chart"] for c in _viz(rep)["charts"]]
        assert _viz(rep)["chosen_by"] == "engine" and not {"theme_rating_heatmap", "pareto"} & set(picked), picked
    # a Pareto the engine picks reads only a column the plan types as a category
    rep = NB.run(_stores_bytes(), "stores.csv", "", None, "2026-03-15")
    assert "pareto" not in [c["chart"] for c in _viz(rep)["charts"]], [c["chart"] for c in _viz(rep)["charts"]]
    ctx = _nv().Ctx(rep, {"reading": None, "plan": None, "pub": None})
    try:
        _nv()._b_theme(ctx, None)
        raise AssertionError("the theme heatmap was built with no chart asked for")
    except _nv().Refused as exc:
        assert "must ask for it by name" in str(exc), exc


def _stylist_bytes():
    """A salon's 240 bookings: the stylist (a person's name under a heading the personal-column check does not know),
    a guest's first name inside one of the desks, the channel and the price."""
    rng = random.Random(3)
    # first names alone: the engine's own scan reads a name as a given name and a family name, so it leaves them
    stylists = ["Sarah", "Emily", "Laura", "Peter", "Martin", "Anna", "David", "Maria", "Paul", "Diego"]
    rows = []
    for i in range(240):
        k = i // 10
        rows.append(["%04d-%02d-%02d" % (2024 + k // 12, k % 12 + 1, rng.randint(1, 28)), stylists[i % 10],
                     ["Lobby", "Terrace", "Emily corner", "Bar"][i % 4], rng.choice(["web", "phone"]),
                     "Emily Rivers" if i % 7 == 0 else "Olga Brook", "%.2f" % rng.uniform(30, 90)])
    return _csv(["booked", "stylist", "desk", "channel", "guest", "price"], rows)


def test_a_chart_never_prints_a_persons_name_as_a_level():
    plan = {"goal": "Which stylists bring in the most?", "kind": "transactions", "primary": "price",
            "columns": [{"name": "booked", "semantic_type": "date", "role": "date"},
                        {"name": "stylist", "semantic_type": "category", "role": "segment"},
                        {"name": "desk", "semantic_type": "category", "role": "segment"},
                        {"name": "channel", "semantic_type": "category", "role": "segment"},
                        {"name": "price", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [], "analyses": [],
            "charts": [{"kind": "pareto", "columns": ["stylist", "price"], "why": "x"},
                       {"kind": "crosstab_heatmap", "columns": ["stylist", "channel"], "why": "x"},
                       {"kind": "group_ranges", "columns": ["price", "stylist"], "why": "x"},
                       {"kind": "change_heatmap", "columns": ["stylist", "price"], "why": "x"},
                       {"kind": "crosstab_heatmap", "columns": ["desk", "channel"], "why": "x"},
                       {"kind": "group_ranges", "columns": ["price", "desk"], "why": "x"}]}
    for dec in ("withhold", "code", "keep"):
        rep = NB.run(_stylist_bytes(), "salon.csv", "", {"__plan__": plan, "guest": dec}, "2026-01-15")
        assert rep["ok"], rep["error"]
        assert {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}.get("guest") == dec, rep["privacy"]
        assert "stylist" not in {f["column"] for f in rep["privacy"]["flagged"]}, "the check flagged stylist itself"
        why = {(r["chart"], r["columns"][0]): r["why"] for r in _viz(rep)["refused"]}
        # (c) a column whose values look like people's names: never a Pareto's bars or a crosstab's rows
        assert why.get(("pareto", "stylist")) == "the values of stylist look like people's names", why
        assert why.get(("crosstab_heatmap", "stylist")) == "the values of stylist look like people's names", why
        assert why.get(("group_ranges", "price")) and why.get(("change_heatmap", "stylist")), why
        # (d) a level holding a token of a flagged column's values ("Emily corner", a guest's first name)
        assert "reads as personal data" in why.get(("crosstab_heatmap", "desk"), ""), why
        blob = json.dumps(_viz(rep)["charts"], ensure_ascii=False)
        assert not [n for n in ("Sarah", "Emily", "Laura", "Diego") if n in blob], blob[:300]


def test_theme_words_are_read_in_any_script():
    rng = random.Random(21)
    phr = ["Great stay \U0001F600", "terrible \U0001F44E service", "très bien, hôtel propre", "café was cold",
           "great café", "slow service", "clean room", "noisy room", "friendly staff", "hôtel trop cher",
           "naïve décor but clean room", "café au lait"]
    rows = [["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), str(rng.randint(1, 5)), rng.choice(phr)]
            for _i in range(300)]
    plan = {"goal": "What do reviewers say at each rating?", "kind": "survey",
            "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                        {"name": "rating", "semantic_type": "rating", "role": "target"},
                        {"name": "title", "semantic_type": "free_text", "role": "driver"}],
            "operations": [], "analyses": [{"type": "themes", "columns": ["title"]}],
            "charts": [{"kind": "theme_rating_heatmap", "columns": ["title", "rating"], "why": "x"}]}
    rep = NB.run(_csv(["review_date", "rating", "title"], rows), "u.csv", "", {"__plan__": plan}, "2026-01-15")
    words = _words(_rec(rep, "theme_rating_heatmap")["data"]["rows"])
    assert {"hôtel", "café"} <= words and not {"tel", "caf"} & words, sorted(words)
    # "cafe" + a combining accent is the same word as "café" (NFC)
    cafe = next(r for r in next(a for a in rep["ai_analyses"]["items"] if a["type"] == "themes")["table"]["rows"]
                if r[0] == "café")
    df = _clean(rep)
    import unicodedata
    n = sum(1 for t in df["title"] if "café" in unicodedata.normalize("NFC", t).lower())
    assert int(cafe[1]) == n, (cafe, n)


def test_a_text_with_no_rating_is_counted_in_all_only():
    # the review: 12 texts with no rating refused the chart "could not be reconciled"
    plan = dict(TITLES_PLAN)
    rep = NB.run(_titles_bytes(unrated=12), "titles.csv", "", {"__plan__": plan}, "2026-01-15")
    rec = _rec(rep, "theme_rating_heatmap")
    assert "12 texts with no rating are in 'all' only" in rec["subtitle"], rec["subtitle"]
    assert "12 texts have no rating and are counted only in 'all'" in rec["summary"], rec["summary"]
    df = _clean(rep)
    t = df["review_title"].str.strip()
    stars = _num(df["rating"])
    ana = {r[0]: r for r in next(a for a in rep["ai_analyses"]["items"] if a["type"] == "themes")["table"]["rows"]}
    d = rec["data"]
    drop = NB._theme_drop([v for v in t if v])
    for i, w in enumerate(d["rows"]):
        use = t.map(lambda x: w in (NB._theme_pairs(NB._theme_tokens(x, drop)) if " " in w
                                    else [y for y in NB._theme_tokens(x, drop) if y]))
        per = [int((use & (stars == float(c))).sum()) for c in d["cols"][:-1]]
        assert sum(per) + int((use & stars.isna()).sum()) == int(ana[w][1].replace(",", "")), w
        assert d["text"][i][-1] == ana[w][2] and d["n"][i][-1] == 360, (w, d["text"][i][-1], d["n"][i][-1])
    assert not _check(rec), _check(rec)[:3]


def test_all_is_whole_percents_without_n_when_texts_are_hidden():
    # the review: with a rating of 3 texts suppressed, 'all' x its n less the shown cells recovered how many of those 3
    # texts use each word. 'all' is now whole percents and has no n; results_for_ai carries no rows read either
    rng = random.Random(21)
    rows = []
    for i in range(300):
        r = 1 if i in (7, 99, 150) else rng.choice([2, 3, 4, 5])
        rows.append(["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), str(r),
                     rng.choice(["great stay", "slow service", "clean room", "noisy room", "great breakfast", "rude staff"])])
    plan = {"goal": "What do reviewers say at each rating?", "kind": "survey",
            "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                        {"name": "rating", "semantic_type": "rating", "role": "target"},
                        {"name": "title", "semantic_type": "free_text", "role": "driver"}],
            "operations": [], "analyses": [],
            "charts": [{"kind": "theme_rating_heatmap", "columns": ["title", "rating"], "why": "x"}]}
    rep = NB.run(_csv(["review_date", "rating", "title"], rows), "h.csv", "", {"__plan__": plan}, "2026-01-15")
    rec = _rec(rep, "theme_rating_heatmap")
    d = rec["data"]
    assert rec["suppressed"]["cells"] == len(d["rows"]) and d["cols"][0] == "1", rec["suppressed"]
    for i in range(len(d["rows"])):
        v, t, n = d["values"][i][-1], d["text"][i][-1], d["n"][i][-1]
        assert n is None and float(v).is_integer() and t == "%d%%" % v, (v, t, n)
    assert ("in %s of all texts" % d["text"][0][-1]) in rec["summary"], rec["summary"]
    assert "'all' in whole percents" in rec["subtitle"], rec["subtitle"]
    assert not _check(rec), _check(rec)[:3]
    sent = [c for c in NB.results_for_ai(rep)["charts"] if _nv().is_record(c)]
    assert sent and sent[0]["inputs"]["rows"] is None and rec["inputs"]["rows"] == len(_clean(rep)), sent[0]["inputs"]


def _slope_other_bytes(tiny_rows=((1, 5), (13, 17, 21))):
    rng = random.Random(9)
    rows = []
    for k in range(24):
        y, m = 2024 + (k + 2) // 12, (k + 2) % 12 + 1
        for st in ["North", "South", "East", "West", "Kiosk"]:
            n = (1 if (k in tiny_rows[0] or k in tiny_rows[1]) else 0) if st == "Kiosk" else 6
            for _ in range(n):
                rev = round(rng.uniform(50, 150), 2) if st != "Kiosk" else round(rng.uniform(900, 1100), 2)
                rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), st, "%.2f" % rev])
    return rows


STORE_PLAN = {"goal": "Which stores drove revenue?", "kind": "transactions", "primary": "revenue",
              "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                          {"name": "store", "semantic_type": "category", "role": "segment"},
                          {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "USD"}],
              "operations": [], "analyses": []}


def test_a_slopes_other_rests_on_5_rows_in_each_window():
    # the review: an 'other' of 2 rows before and 3 after printed both of its totals
    rows = _slope_other_bytes()
    rep = NB.run(_csv(["order_date", "store", "revenue"], rows), "k.csv", "",
                 {"__plan__": dict(STORE_PLAN, charts=[{"kind": "slope", "columns": ["store", "revenue"], "why": "x"}])},
                 "2026-03-15")
    rec = _rec(rep, "slope")
    df = _clean(rep)
    pm, lm = _windows(rep)
    mon = df["order_date"].str[:7]
    shown = [r["label"] for r in rec["data"]["rows"] if r["label"] != "other"]
    oth = ~df["store"].isin(shown)
    assert "other" in [r["label"] for r in rec["data"]["rows"]], rec["data"]["rows"]
    for win in (pm, lm):
        n = int((oth & mon.isin(win)).sum())
        assert n == 0 or n >= 5, (win[0], n)
    assert "rests on 5 or more rows in each window" in rec["suppressed"]["why"], rec["suppressed"]
    assert not _check(rec), _check(rec)[:3]


def test_a_paretos_other_rests_on_5_rows_and_a_refusal_says_the_true_reason():
    # 'other' made of one level of 2 rows: the smallest bar joins it
    rng = random.Random(4)
    rows = [["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), "S%02d" % i, "%.2f" % rng.uniform(80, 120)]
            for i in range(9) for _ in range(6)] + [["2025-06-01", "Tiny", "40.00"], ["2025-07-01", "Tiny", "35.00"]]
    ch = [{"kind": "pareto", "columns": ["store", "revenue"], "why": "x"}]
    rep = NB.run(_csv(["order_date", "store", "revenue"], rows), "p.csv", "", {"__plan__": dict(STORE_PLAN, charts=ch)},
                 "2026-01-15")
    rec = _rec(rep, "pareto")
    d = rec["data"]
    assert len(d["bars"]) == 8 and d["other"]["n_entities"] == 2, (len(d["bars"]), d["other"])
    assert "1 more level is in 'other' so that it rests on 5 or more rows" in rec["summary"], rec["summary"]
    assert not _check(rec), _check(rec)[:3]
    # a level of 2 rows among the largest: refused with its reason, not "could not be reconciled"
    rows2 = [["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), "S%02d" % i, "%.2f" % rng.uniform(80, 120)]
             for i in range(9) for _ in range(6)] + [["2025-06-01", "Tiny", "4000.00"], ["2025-07-01", "Tiny", "3100.00"]]
    rep = NB.run(_csv(["order_date", "store", "revenue"], rows2), "p.csv", "", {"__plan__": dict(STORE_PLAN, charts=ch)},
                 "2026-01-15")
    why = [r["why"] for r in _viz(rep)["refused"] if r["chart"] == "pareto"]
    assert why == ["a level of store with fewer than 5 rows is among the largest, so the bars' running share cannot "
                   "show where 80% of the total revenue is reached"], why


def test_the_waterfall_summary_says_when_other_is_the_largest_part():
    rng = random.Random(12)
    rows = []
    for k in range(24):
        y, m = 2024 + (k + 2) // 12, (k + 2) % 12 + 1
        for st in ["North", "South", "East", "West", "Flagship"]:
            n = ((1 if k in (9, 10) else 0) if k < 12 else 10) if st == "Flagship" else 6
            for _ in range(n):
                rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), st,
                             "%.2f" % ((400 if st == "Flagship" else 100) * rng.uniform(0.9, 1.1))])
    rep = NB.run(_csv(["order_date", "store", "revenue"], rows), "f.csv", "", {"__plan__": dict(STORE_PLAN, charts=[
        {"kind": "contribution_waterfall", "columns": ["store", "revenue"], "why": "x"}])}, "2026-03-15")
    rec = _rec(rep, "contribution_waterfall")
    other = next(s for s in rec["data"]["steps"] if s["label"] == "other")
    assert "Most of the change sits in the smaller levels of store combined ('other', %s)" % other["text"] in rec["summary"], \
        rec["summary"]
    assert "The largest contributions:" not in rec["summary"], rec["summary"]


def test_a_calendar_says_why_it_shows_fewer_years():
    rng = random.Random(30)
    rows = []
    for y in range(1996, 2026):
        for m in range(1, 13):
            for _ in range(5):
                rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), "%.2f" % rng.uniform(40, 160),
                             "%.3f" % (100 + (y - 1996) * 2 + rng.uniform(-3, 3))])
    plan = {"goal": "How has net sales moved by month?", "kind": "time_series_panel", "primary": "net_sales",
            "columns": [{"name": "date", "semantic_type": "date", "role": "date"},
                        {"name": "net_sales", "semantic_type": "flow_amount", "role": "target"},
                        {"name": "price_index", "semantic_type": "level", "role": "driver"}],
            "operations": [], "analyses": [],
            "charts": [{"kind": "calendar_heatmap", "columns": ["net_sales"], "why": "x"},
                       {"kind": "calendar_heatmap", "columns": ["price_index"], "why": "y"}]}
    rep = NB.run(_csv(["date", "net_sales", "price_index"], rows), "c.csv", "", {"__plan__": plan}, "2026-01-10")
    for c in _viz(rep)["charts"]:
        assert c["data"]["rows"][0] == "2014" and c["subtitle"].endswith("; 2014 to 2025 shown: the latest 12 years"), \
            c["subtitle"]
        assert "size limit" not in c["subtitle"]


def test_group_ranges_say_what_the_ranking_and_the_figure_are():
    rng = random.Random(5)
    rows = [["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), g, "%.2f" % max(1, rng.gauss(mu, 25))]
            for g, n, mu in (("Big", 400, 60), ("Mid", 200, 55), ("Small", 6, 75), ("Low", 300, 40)) for _ in range(n)]
    plan = {"goal": "Compare basket size by store", "kind": "transactions",
            "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                        {"name": "store", "semantic_type": "category", "role": "segment"},
                        {"name": "basket", "semantic_type": "level", "role": "target"}],
            "operations": [], "analyses": [], "charts": [{"kind": "group_ranges", "columns": ["basket", "store"], "why": "x"}]}
    rep = NB.run(_csv(["order_date", "store", "basket"], rows), "g.csv", "", {"__plan__": plan}, "2026-01-15")
    rec = _rec(rep, "group_ranges")
    top, bot = rec["data"]["rows"][0], rec["data"]["rows"][-1]
    assert rec["summary"].startswith("Ranked by each store's average weighted by its rows, %s is highest (its own average "
                                     "%s, 95%% range %s to %s) and %s lowest" % (top["label"], top["texts"]["center"],
                                                                                top["texts"]["lo"], top["texts"]["hi"],
                                                                                bot["label"])), rec["summary"]
    assert "its weighted average (" not in rec["summary"]
    # the dots in the ranking's order: the compare analysis's (weighted) order
    cmp_ = NB._a_compare(_clean(rep).assign(basket=lambda x: _num(x["basket"])), None, None, ["basket"],
                         {"columns": plan["columns"]}, None, by="store")
    assert [r["label"] for r in rec["data"]["rows"]] == [r[0] for r in cmp_["table"]["rows"]]


def test_a_diverging_legend_runs_each_side_to_its_own_extreme():
    rec = _rec(_case("fx"), "calendar_heatmap")
    d = rec["data"]
    shown = [v for row in d["values"] for v in row if v is not None]
    lo, hi = min(shown), max(shown)
    leg = {x["tier"]: x["text"] for x in d["legend"]}
    fmt = lambda v: _nv()._chg(_nv()._r6(v), "%")       # noqa: E731
    assert leg[-3].startswith(fmt(lo) + " to ") and leg[3].endswith(" to " + fmt(hi)), (leg, lo, hi)
    assert abs(lo) != abs(hi)


def test_a_calendar_of_billions_is_written_compact():
    rng = random.Random(8)
    rows = [["%04d-%02d-%02d" % (y, m, d), "%.2f" % (rng.uniform(2.1e8, 2.9e8) * (1 + (y - 2023) * 0.1))]
            for y in (2023, 2024, 2025) for m in range(1, 13) for d in (3, 9, 15, 21, 27)]
    plan = {"goal": "Monthly revenue", "kind": "time_series_panel", "primary": "revenue",
            "columns": [{"name": "day", "semantic_type": "date", "role": "date"},
                        {"name": "revenue", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [], "analyses": [], "charts": [{"kind": "calendar_heatmap", "columns": ["revenue"], "why": "x"}]}
    rep = NB.run(_csv(["day", "revenue"], rows), "b.csv", "", {"__plan__": plan}, "2026-01-10")
    assert rep["ok"], rep["error"]
    rec = _rec(rep, "calendar_heatmap")
    texts = [t for row in rec["data"]["text"] for t in row if t]
    assert texts and all(len(t) <= 12 and t[-1] in "BM" for t in texts), texts[:4]
    for i, row in enumerate(rec["data"]["values"]):
        for j, v in enumerate(row):
            if v is not None:
                assert rec["data"]["text"][i][j] == _nv()._compact(v), (v, rec["data"]["text"][i][j])
    assert all(x["text"].count("B") == 2 or x["text"].count("M") >= 1 for x in rec["data"]["legend"]), rec["data"]["legend"]
    assert not _check(rec), _check(rec)[:3]
    assert _nv()._compact(1234567890) == "1.235B" and _nv()._compact(456700000) == "456.7M" and \
        _nv()._compact(-999970000) == "−1B"


def test_a_withheld_columns_name_in_a_record_is_replaced_not_the_record_dropped():
    # the review: a withheld column named "phone" dropped the whole themes chart whose subjects say "phone"
    rng = random.Random(8)
    subj = ["Phone not working", "Refund request", "Phone battery issue", "Late delivery", "Wrong item sent",
            "Phone screen cracked", "Cannot log in", "Billing question"]
    rows = [["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), "+1 416 555 %04d" % rng.randint(0, 9999),
             str(rng.randint(1, 5)), rng.choice(subj)] for _i in range(300)]
    plan = {"goal": "What do customers write about?", "kind": "survey",
            "columns": [{"name": "opened", "semantic_type": "date", "role": "date"},
                        {"name": "csat", "semantic_type": "rating", "role": "target"},
                        {"name": "subject", "semantic_type": "free_text", "role": "driver"}],
            "operations": [], "analyses": [],
            "charts": [{"kind": "theme_rating_heatmap", "columns": ["subject", "csat"], "why": "x"}]}
    rep = NB.run(_csv(["opened", "phone", "csat", "subject"], rows), "t.csv", "", {"__plan__": plan}, "2026-01-15")
    assert {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]} == {"phone": "withhold"}, rep["privacy"]
    rec = _rec(rep, "theme_rating_heatmap")
    assert not [w for w in rec["data"]["rows"] if "phone" in w.split()] and "screen" in rec["data"]["rows"], rec["data"]["rows"]
    sent = [c for c in NB.results_for_ai(rep)["charts"] if _nv().is_record(c)]
    assert [c["id"] for c in sent] == [rec["id"]], sent
    # a string that still names it reads "[withheld column]"
    scrub = NB._NameScrub(["phone"])
    r2 = copy.deepcopy(rec)
    r2["summary"] = "Subjects that mention the phone most often."
    out = _nv().for_sending(r2, scrub)
    assert out["summary"] == "Subjects that mention the [withheld column] most often." and out["title"] == rec["title"], out


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        with open(FIXTURE, "w", encoding="utf-8") as fh:
            json.dump(_fixture(), fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print("wrote %s" % FIXTURE)
        sys.exit(0)
    print("adapter under test: %s" % ("engine/ (the site)" if not os.environ.get("NL_BROWSER_DIR")
                                      else "a copy (NL_BROWSER_DIR)"))
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print("  PASS  %s" % fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  %s: %s: %s" % (fn.__name__, type(exc).__name__, str(exc)[:400]))
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    if failed:
        sys.exit(1)
    print("ALL TESTS PASSED")
