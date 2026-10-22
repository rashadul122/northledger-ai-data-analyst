#!/usr/bin/env python3
"""Tests for engine/nl_sql.py, Ask with SQL (Track B; CONTRACT §5.16).

    NL_ENGINE_ROOT=<copy of northledger-core> python tools/test_nl_sql.py

Checks, on the SYNTHETIC sales sample (tools/make_sample_sales.py) and small files built here:
  * the static check and the SQLite authorizer refuse writes, PRAGMA/ATTACH, other tables (sqlite_master), functions
    off the allowlist (load_extension, randomblob) and withheld/coded columns; the fence holds even when the static
    check is bypassed
  * the caps: at most ROW_CAP rows returned, a runaway query stopped by the time cap
  * small cells: a result row over fewer than 5 source rows is hidden when countable, and the answer says plainly when not
  * the decomposition adds up to the whole change, split by split (rate + mix, or contributions)
  * the planted causes of the sample's EMEA gross-margin drop are found, with magnitudes recomputed here independently
    with pandas (a different code path: pivot tables, not nl_sql's own loop)
  * every number in the explanation's lead sentence is a figure this test recomputes; the record is JSON-safe
Prints PASS/FAIL per test, exits 1 on any failure.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, os.path.join(SITE, "engine"))
os.environ.setdefault("NL_BROWSER_STRICT", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import nl_browser as nb  # noqa: E402
import nl_sql as ns  # noqa: E402

AS_OF = "2026-10-01"
SAMPLE = os.path.join(SITE, "engine", "sample-sales-synthetic.csv")
QUESTION = "Why is gross margin down in EMEA this quarter?"
_STATE = {}
_REP = {}


def sales_state():
    """The engine's own state after a run of the sample (the frame the SQL panel reads)."""
    if not _STATE:
        with open(SAMPLE, "rb") as f:
            rep = nb.run(f.read(), "sample-sales-synthetic.csv", "", {}, AS_OF)
        assert rep["ok"], rep["error"]
        _STATE.update(dict(nb._LAST_ANALYST))
        _REP.update(rep)
    return _STATE


def raw():
    d = pd.read_csv(SAMPLE, parse_dates=["order_date"])
    d["q"] = d["order_date"].dt.to_period("Q")
    return d


# ----------------------------------------------------------------------------------------------- the fence
WRITES = [
    "INSERT INTO kept_rows (region) VALUES ('x')",
    "DELETE FROM kept_rows",
    "UPDATE kept_rows SET revenue = 0",
    "DROP TABLE kept_rows",
    "CREATE TABLE t (x)",
    "ALTER TABLE kept_rows ADD COLUMN y",
    "REPLACE INTO kept_rows (region) VALUES ('x')",
    "PRAGMA table_info(kept_rows)",
    "ATTACH DATABASE 'x.db' AS x",
    "DETACH DATABASE x",
    "VACUUM",
    "SELECT load_extension('evil')",
    "SELECT 1; DELETE FROM kept_rows",
    "SELECT * FROM kept_rows; SELECT 2",
    "WITH a AS (SELECT 1) DELETE FROM kept_rows",
    "SELECT * FROM sqlite_master",
    "SELECT name FROM sqlite_schema",
    "SELECT * FROM other_table",
    "SELECT * FROM kept_rows, sqlite_master",
    "SELECT * FROM kept_rows JOIN sqlite_temp_master ON 1",
    "SELECT * FROM pragma_table_info('kept_rows')",
    "SELECT randomblob(10)",
    "SELECT * FROM kept_rows WHERE region = ?",
    "SELECT 'unterminated FROM kept_rows",
    "SELECT * FROM kept_rows /* comment",
]


def test_the_static_check_refuses_anything_but_one_read():
    st = sales_state()
    cols = ns._cols(st)
    for q in WRITES:
        r = ns.check_sql(q, cols, [])
        assert not r["ok"], q
        out = ns.run_sql(st, q)
        assert not out["ok"] and out["error"].startswith("refused"), (q, out)
    ok = ns.check_sql('SELECT region, SUM("revenue") AS r FROM kept_rows WHERE country = \'Germany\' GROUP BY 1;', cols, [])
    assert ok["ok"] and not ok["sql"].endswith(";"), ok
    # a string or a comment holding a banned word is data, not a statement
    ok = ns.check_sql("SELECT COUNT(*) FROM kept_rows WHERE channel = 'DROP TABLE' -- DELETE", cols, [])
    assert ok["ok"], ok
    # replace() the function is a read
    assert ns.check_sql("SELECT replace(country, 'a', 'b') FROM kept_rows", cols, [])["ok"]


def test_the_authorizer_holds_when_the_static_check_is_bypassed():
    st = sales_state()
    real = ns.check_sql
    ns.check_sql = lambda sql, c, h=None: {"ok": True, "sql": sql}
    try:
        for q in ("SELECT * FROM sqlite_master", "SELECT randomblob(10)", "SELECT load_extension('x')",
                  "DELETE FROM kept_rows", "SELECT * FROM pragma_table_info('kept_rows')",
                  "CREATE TEMP TABLE t AS SELECT 1", "PRAGMA writable_schema = 1"):
            out = ns.run_sql(st, q)
            assert not out["ok"], (q, out)
        n = ns.run_sql(st, "SELECT COUNT(*) FROM kept_rows")
        assert n["ok"] and n["rows"][0][0] == len(st["df"]), n
    finally:
        ns.check_sql = real


def test_withheld_columns_are_denied():
    d = pd.read_csv(SAMPLE).head(600)
    d["customer_email"] = ["c%d@example.com" % i for i in range(len(d))]
    rep = nb.run(d.to_csv(index=False).encode(), "orders.csv", "", {"customer_email": "withhold"}, AS_OF)
    assert rep["ok"], rep["error"]
    st = dict(nb._LAST_ANALYST)
    sch = ns.schema(st)
    assert "customer_email" not in json.dumps(sch), sch
    for q in ("SELECT customer_email FROM kept_rows", 'SELECT "customer_email", revenue FROM kept_rows',
              "SELECT COUNT(DISTINCT customer_email) FROM kept_rows"):
        out = ns.run_sql(st, q)
        assert not out["ok"] and "withheld" in out["error"], (q, out)
    # the fence itself: a frame that DID hold the column (a state built by hand) still cannot read it
    st2 = {"df": d, "kinds": {c: ("number" if c in ("units", "revenue", "cost") else "text") for c in d.columns},
           "hint": {}, "shape": {}, "hidden": ["customer_email"]}
    real = ns.check_sql
    ns.check_sql = lambda sql, c, h=None: {"ok": True, "sql": sql}
    try:
        out = ns.run_sql(st2, "SELECT customer_email FROM kept_rows")
        assert not out["ok"] and "withheld column" in out["error"], out
        out = ns.run_sql(st2, "SELECT * FROM kept_rows")
        assert not out["ok"], out
        out = ns.run_sql(st2, "SELECT region FROM kept_rows LIMIT 1")
        assert out["ok"], out
    finally:
        ns.check_sql = real


def test_the_caps():
    st = sales_state()
    out = ns.run_sql(st, "SELECT * FROM kept_rows")
    assert out["ok"] and out["row_count"] == ns.ROW_CAP and out["truncated"], (out["row_count"], out.get("truncated"))
    assert out["small_cells"]["state"] == "raw_rows"
    t0 = ns.TIME_CAP_S
    ns.TIME_CAP_S = 0.3
    try:
        out = ns.run_sql(st, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT MAX(x) FROM c")
        assert not out["ok"] and "limit" in out["error"], out
    finally:
        ns.TIME_CAP_S = t0
    assert ns.check_sql("x" * (ns.MAX_SQL_CHARS + 1), [], [])["ok"] is False


def test_small_cells_are_hidden_or_said_plainly():
    st = sales_state()
    d = raw()
    q = ("SELECT country, product_line, channel, SUM(revenue) AS revenue FROM kept_rows "
         "WHERE region = 'EMEA' AND order_date >= '2026-07-01' GROUP BY country, product_line, channel")
    out = ns.run_sql(st, q)
    e = d[(d.region == "EMEA") & (d.order_date >= "2026-07-01")]
    n = e.groupby(["country", "product_line", "channel"]).size()
    # owner's decision (19 Oct 2026, AI Analyst v2 phase A): no result row is hidden for its row count
    assert out["ok"] and out["small_cells"]["state"] == "applied" and out["small_cells"]["hidden"] == 0, out["small_cells"]
    assert out["row_count"] == len(n)
    shown = {tuple(r[:3]) for r in out["rows"]}
    assert shown == set(n.index)
    sub = ns.run_sql(st, "SELECT AVG(r) FROM (SELECT country, SUM(revenue) AS r FROM kept_rows GROUP BY country)")
    assert sub["ok"] and sub["small_cells"]["hidden"] == 0, sub


# ----------------------------------------------------------------------------------------------- the analyst steps
def _pandas_decompose(e, dim, p0, p1):
    """Rate and mix by group, recomputed with pivot tables (independent of nl_analyst.rate_mix_parts)."""
    g = e[e.q.isin([p0, p1])]
    rev = g.pivot_table(index=dim, columns="q", values="revenue", aggfunc="sum", fill_value=0.0)
    cost = g.pivot_table(index=dim, columns="q", values="cost", aggfunc="sum", fill_value=0.0)
    cnt = g.pivot_table(index=dim, columns="q", values="revenue", aggfunc="size", fill_value=0)
    big = cnt.min(axis=1) >= 5
    if (~big).any():                          # small groups pooled, as the engine pools them
        rev.loc["__pool"] = rev[~big].sum()
        cost.loc["__pool"] = cost[~big].sum()
        rev, cost = rev.loc[list(rev.index[big]) + ["__pool"]], cost.loc[list(cost.index[big]) + ["__pool"]]
    gp = rev - cost
    W = rev / rev.sum()
    R = gp / rev
    tot = gp.sum() / rev.sum()
    rbar = (tot[p0] + tot[p1]) / 2
    rate = (W[p0] + W[p1]) / 2 * (R[p1] - R[p0]) * 100
    mix = (W[p1] - W[p0]) * ((R[p0] + R[p1]) / 2 - rbar) * 100
    return rate, mix, (tot[p1] - tot[p0]) * 100, W, R


def _asked(question=QUESTION):
    """The engine's own plan for the question, run by the analyst loop as a planner's round (analyst_more_json)."""
    import nl_analyst as na
    st = sales_state()
    steps = ns.plan_steps(st, question)
    out = na.run_steps(st["df"], st["kinds"], steps, st["hint"], prior=[])
    return steps, out


def _num(step, key):
    return step["numbers"][key]["value"]


def test_the_question_is_read():
    st = sales_state()
    rq = ns.read_question(st, QUESTION)
    assert rq["slice"] == {"region": "EMEA"}, rq["slice"]
    assert rq["grain"] == "Q"
    assert rq["measure"]["kind"] == "ratio" and rq["measure"]["num"] == ["revenue", "cost"] and rq["measure"]["den"] == "revenue"
    assert ns.read_question(st, "How did revenue in APAC move this month?")["grain"] == "M"
    rq = ns.read_question(st, "How did revenue for Online in Japan change?")
    assert rq["slice"] == {"country": "Japan", "channel": "Online"} and rq["measure"]["kind"] == "flow", rq
    steps = ns.plan_steps(st, QUESTION)
    assert [s["op"] for s in steps] == ["sql", "rate_mix", "rate_mix", "rate_mix"], steps
    assert {s["args"]["by"] for s in steps[1:]} == {"country", "product_line", "channel"}
    assert all(s["args"]["where"] == {"region": "EMEA"} and s["args"]["less"] == "cost" for s in steps[1:])
    # a flow reuses the loop's own change op; an average its mix op
    assert {s["op"] for s in ns.plan_steps(st, "Why did revenue fall in EMEA this quarter?")[1:]} == {"change"}


def test_rate_mix_parts_add_up():
    import nl_analyst as na
    rng = np.random.default_rng(3)
    for _ in range(50):
        k = int(rng.integers(2, 7))
        n0 = {i: float(rng.uniform(1, 50)) for i in range(k)}
        d0 = {i: float(rng.uniform(50, 100)) for i in range(k)}
        n1 = {i: float(rng.uniform(1, 50)) for i in range(k) if i == 0 or rng.random() > 0.2}
        d1 = {i: float(rng.uniform(50, 100)) for i in n1}
        p = na.rate_mix_parts(n0, d0, n1, d1, 100.0)
        assert abs(p["rate"] + p["mix"] - p["total"]) < 1e-9, p
        assert abs(sum(g["rate"] + g["mix"] for g in p["groups"].values()) - p["total"]) < 1e-9
    # an average is the same split with rows as the denominator: op_mix's within + mix total equals ours
    p = na.rate_mix_parts({"a": 10.0, "b": 40.0}, {"a": 5.0, "b": 5.0}, {"a": 30.0, "b": 20.0}, {"a": 15.0, "b": 5.0})
    assert abs(p["total"] - (50 / 20 - 50 / 10)) < 1e-12


def test_the_planted_causes_are_found_with_their_magnitudes():
    steps, out = _asked()
    assert not out["refused"], out["refused"]
    by_step = {s["args"].get("by"): s for s in out["steps"] if s["op"] == "rate_mix"}
    assert all(s["ok"] for s in out["steps"]), [s["refused"] for s in out["steps"]]
    d = raw()
    e = d[d.region == "EMEA"]
    p1, p0, py = pd.Period("2026Q3"), pd.Period("2026Q2"), pd.Period("2025Q3")
    agg = e.groupby("q")[["revenue", "cost"]].sum()
    gm = (agg.revenue - agg.cost) / agg.revenue
    for s in by_step.values():
        assert abs(_num(s, "prev") - gm[p0]) < 1e-12 and abs(_num(s, "cur") - gm[p1]) < 1e-12
        assert abs(_num(s, "year_value") - gm[py]) < 1e-12
        assert abs(_num(s, "change") - (gm[p1] - gm[p0]) * 100) < 1e-9
        assert abs(_num(s, "rate") + _num(s, "mix") - _num(s, "change")) < 1e-9, "the split adds up to the change"
        assert s["numbers"]["prev_label"]["text"] == "2026 Q2" and s["numbers"]["cur_label"]["text"] == "2026 Q3"
        assert "partial" not in s["numbers"]   # the latest COMPLETE quarter is read (asked_windows)

    # cause 1, the mix: Accessories' share of EMEA revenue jumps; by product line the move is mostly mix, led by it
    rate, mix, _chg, W, R = _pandas_decompose(e, "product_line", p0, p1)
    s = by_step["product_line"]
    assert abs(_num(s, "rate") - rate.sum()) < 1e-9 and abs(_num(s, "mix") - mix.sum()) < 1e-9
    assert mix.idxmin() == "Accessories" and mix["Accessories"] < -3.0, mix
    assert W.loc["Accessories", p1] > 2 * W.loc["Accessories", p0]
    assert s["numbers"]["top"]["text"] == "Accessories" and s["numbers"]["top_kind"]["text"] == "mix"
    assert abs(_num(s, "top_effect") - mix["Accessories"]) < 1e-9
    assert abs(_num(s, "top_w0") - W.loc["Accessories", p0]) < 1e-12 and abs(_num(s, "top_w1") - W.loc["Accessories", p1]) < 1e-12
    rows = {r[0]: r for r in s["table"]["rows"]}
    assert abs(rows["Accessories"][6] - round(mix["Accessories"], 4)) < 1e-9

    # cause 2, the discount: Online's own margin falls (rate), the largest effect by channel
    rate_c, mix_c, _c, Wc, Rc = _pandas_decompose(e, "channel", p0, p1)
    s = by_step["channel"]
    assert s["numbers"]["top"]["text"] == "Online" and s["numbers"]["top_kind"]["text"] == "rate"
    assert abs(_num(s, "top_effect") - rate_c["Online"]) < 1e-9 and rate_c["Online"] < -5.0
    assert (Rc.loc["Online", p0] - Rc.loc["Online", p1]) * 100 > 15
    assert abs(_num(s, "top_r0") - Rc.loc["Online", p0]) < 1e-12

    # the mix shows inside the two countries: their own margins fall (rate by country), the others' hold
    rate_k, _mk, _ck, _Wk, Rk = _pandas_decompose(e, "country", p0, p1)
    s = by_step["country"]
    assert s["numbers"]["top"]["text"] == "Germany" and s["numbers"]["top_kind"]["text"] == "rate"
    assert abs(_num(s, "top_effect") - rate_k["Germany"]) < 1e-9
    for g in ("Germany", "France"):
        assert rate_k[g] < -1.0, (g, rate_k[g])
    for g in ("United Kingdom", "Spain", "Italy"):
        assert rate_k[g] > -1.0, (g, rate_k[g])
    rows = {r[0]: r for r in s["table"]["rows"]}
    assert abs(rows["France"][5] - round(rate_k["France"], 4)) < 1e-9


def test_every_number_in_an_answer_is_a_ledger_value():
    _steps, out = _asked()
    texts = {}
    for s in out["steps"]:
        for v in s["numbers"].values():
            texts.setdefault(s["id"], set()).add(v["text"])
    assert len(out["insights"]) == 4
    for ins in out["insights"]:
        (sid,) = ins["steps"]
        filled = re.sub(r"\{[A-Z]\d+\.([a-z0-9_]+)\}", lambda m: "", ins["template"])
        assert not re.search(r"\d", filled.replace("5 or more", "")), ins["template"]   # the template holds no figure
    lead = out["insights"][1]["text"]
    d = raw()
    e = d[d.region == "EMEA"]
    agg = e.groupby("q")[["revenue", "cost"]].sum()
    gm = (agg.revenue - agg.cost) / agg.revenue * 100
    for p in ("2026Q3", "2026Q2", "2025Q3"):
        assert ("%.1f%%" % gm[pd.Period(p)]) in lead, (p, lead)
    assert "fell" in lead and "EMEA" in lead
    # every insight with a figure carries a chart registry record the dashboard can pin
    figs = [i["figure"] for i in out["insights"] if i.get("figure")]
    assert len(figs) == 3 and all(f["chart"] in ("grouped_bar", "bar_chart") and f["id"].startswith("viz.7") for f in figs)
    json.dumps(out, allow_nan=False)


def test_the_sql_step():
    import nl_analyst as na
    st = sales_state()
    _steps, out = _asked()
    s = out["steps"][0]
    assert s["op"] == "sql" and s["ok"], s
    cols, rows = s["table"]["columns"], s["table"]["rows"]
    d = raw()
    e = d[d.region == "EMEA"]
    agg = e.groupby("q")[["revenue", "cost"]].sum()
    assert rows[-1][0] == "2026-Q3" and abs(rows[-1][cols.index("gross_margin_pct")] - round((1 - agg.cost.iloc[-1] / agg.revenue.iloc[-1]) * 100, 4)) < 1e-9
    assert _num(s, "rows") == 12 and _num(s, "hidden") == 0
    # a planner's hostile or withheld SQL is refused at the check, with its reason, and nothing of it runs
    bad = [{"op": "sql", "args": {"sql": "SELECT * FROM sqlite_master"}},
           {"op": "sql", "args": {"sql": "DELETE FROM kept_rows"}},
           {"op": "sql", "args": {"sql": "SELECT 1; DROP TABLE kept_rows"}},
           {"op": "rate_mix", "args": {"measure": "revenue", "denominator": "revenue", "by": "channel", "grain": "week"}}]
    o = na.run_steps(st["df"], st["kinds"], bad, st["hint"], prior=[])
    assert [x["ok"] for x in o["steps"]] == [False] * 4, o["steps"]
    assert "sqlite_master" in o["steps"][0]["refused"] and "SELECT or WITH" in o["steps"][1]["refused"]
    assert "more than one statement" in o["steps"][2]["refused"] and "quarter" in o["steps"][3]["refused"]
    assert not o["insights"]


def test_the_adapter_and_the_step_schema():
    import nl_analyst as na
    sales_state()
    nb._LAST_ANALYST.clear()
    nb._LAST_ANALYST.update({k: v for k, v in _STATE.items() if not k.startswith("_sql")})
    steps = json.loads(nb.analyst_plan_json(QUESTION))
    assert [s["op"] for s in steps] == ["sql", "rate_mix", "rate_mix", "rate_mix"]
    out = json.loads(nb.analyst_more_json(json.dumps(steps), "[]"))
    assert len(out["insights"]) == 4
    nb._LAST_ANALYST.clear()
    assert json.loads(nb.analyst_plan_json(QUESTION)) == []
    sc = na.step_schema(sales_state()["shape"])
    ops = {v["properties"]["op"]["const"]: v for v in sc["oneOf"]}
    assert "sql" in ops and "rate_mix" in ops
    assert ops["rate_mix"]["properties"]["args"]["properties"]["denominator"]["enum"] == ["units", "revenue", "cost"]
    assert ops["rate_mix"]["properties"]["args"]["properties"]["grain"]["enum"] == ["quarter", "month", "year"]
    # the engine's own rounds never run the planner-only ops
    rep_ops = {s["op"] for s in _STATE_REP()["analyst"]["steps"]}
    assert not rep_ops & {"sql", "rate_mix"}, rep_ops


def _STATE_REP():
    if not _REP:
        with open(SAMPLE, "rb") as f:
            _REP.update(nb.run(f.read(), "sample-sales-synthetic.csv", "", {}, AS_OF))
    return _REP


def _run_q(question):
    import nl_analyst as na
    st = sales_state()
    steps = ns.plan_steps(st, question)
    return steps, na.run_steps(st["df"], st["kinds"], steps, st["hint"], prior=[])


def test_the_asked_quarter_sets_the_windows_of_the_reused_change_op():
    steps, out = _run_q("Why did revenue fall this quarter?")
    assert [s["op"] for s in steps] == ["sql", "change", "change", "change"], steps
    assert all(s["args"].get("grain") == "quarter" for s in steps[1:])
    d = raw()
    q = d.groupby("q")["revenue"].sum()
    for s in out["steps"][1:]:
        assert s["ok"], s["refused"]
        n = s["numbers"]
        assert n["prev_label"]["text"] == "2026 Q2" and n["cur_label"]["text"] == "2026 Q3" and n["yago_label"]["text"] == "2025 Q3"
        assert abs(n["prev"]["value"] - q[pd.Period("2026Q2")]) < 1e-6 and abs(n["cur"]["value"] - q[pd.Period("2026Q3")]) < 1e-6
        assert abs(n["yago"]["value"] - q[pd.Period("2025Q3")]) < 1e-6
        assert abs(n["change"]["value"] - (q[pd.Period("2026Q3")] / q[pd.Period("2026Q2")] - 1)) < 1e-12
        assert abs(n["yago_change"]["value"] - (q[pd.Period("2026Q3")] / q[pd.Period("2025Q3")] - 1)) < 1e-12
        by = s["args"]["by"]
        g = d[d.q.isin([pd.Period("2026Q2"), pd.Period("2026Q3")])].pivot_table(index=by, columns="q", values="revenue", aggfunc="sum").fillna(0.0)
        delta = (g[pd.Period("2026Q3")] - g[pd.Period("2026Q2")])
        top = delta.idxmin()
        assert n["top"]["text"] == top and abs(n["top_delta"]["value"] - delta[top]) < 1e-6, (by, n["top"], delta)
    t = out["insights"][1]["text"]
    assert t.startswith("The total of revenue fell from") and "in 2026 Q2 to" in t and "in 2026 Q3" in t and "against" in t and "2025 Q3" in t, t


def test_a_monthly_question_reads_months():
    steps, out = _run_q("Why did revenue fall last month?")
    d = raw()
    mo = d.groupby(d.order_date.dt.to_period("M"))["revenue"].sum()
    s = out["steps"][1]
    n = s["numbers"]
    assert s["ok"] and n["prev_label"]["text"] == "2026-08" and n["cur_label"]["text"] == "2026-09" and n["yago_label"]["text"] == "2025-09"
    assert abs(n["cur"]["value"] - mo[pd.Period("2026-09")]) < 1e-6 and abs(n["prev"]["value"] - mo[pd.Period("2026-08")]) < 1e-6
    assert abs(n["yago"]["value"] - mo[pd.Period("2025-09")]) < 1e-6
    assert len(out["steps"][0]["table"]["rows"]) == 36
    # a named period and the margin question's windows (rate_mix reads the same asked windows)
    steps, out = _run_q("What moved revenue in Q2 2026?")
    assert steps[1]["args"]["period"] == "2026-Q2" and out["steps"][1]["numbers"]["cur_label"]["text"] == "2026 Q2"
    assert out["steps"][1]["numbers"]["prev_label"]["text"] == "2026 Q1" and out["steps"][1]["numbers"]["yago_label"]["text"] == "2025 Q2"
    steps, out = _run_q("What happened to gross margin in 2025?")
    s = out["steps"][1]
    assert steps[1]["args"]["period"] == "2025" and s["op"] == "rate_mix" and s["ok"]
    assert s["numbers"]["prev_label"]["text"] == "2024" and s["numbers"]["cur_label"]["text"] == "2025" and "year_label" not in s["numbers"]
    y = d.groupby(d.order_date.dt.year)[["revenue", "cost"]].sum()
    assert abs(s["numbers"]["cur"]["value"] - (1 - y.cost[2025] / y.revenue[2025])) < 1e-12
    # a window the file does not hold whole is refused with its reason, never compared on part of its months
    steps, out = _run_q("How has revenue changed since 2025?")
    assert steps[1]["args"]["period"] == "since:2025" and not out["steps"][1]["ok"]
    assert "not all in the file" in out["steps"][1]["refused"], out["steps"][1]["refused"]
    steps, out = _run_q("How did revenue change in Q4 2023?")
    assert not out["steps"][1]["ok"] and "not all in the file" in out["steps"][1]["refused"]
    steps, out = _run_q("How did revenue change in Q4 2026?")
    assert not out["steps"][1]["ok"] and "not complete in the file" in out["steps"][1]["refused"]


def test_the_sample_is_the_generator_output():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_sample_sales.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def _panel_state():
    """A small panel: three countries a year, 2018 to 2025, one row each (a World Bank table read whole)."""
    rows = []
    for y in range(2018, 2026):
        for c, base, g in (("Atlantis", 100.0, 0.02), ("Borduria", 50.0, -0.01), ("Carpania", 10.0, 0.10)):
            rows.append({"Year": pd.Timestamp("%d-01-01" % y), "Country Name": c, "Population": base * (1 + g) ** (y - 2018)})
    df = pd.DataFrame(rows)
    return {"df": df, "kinds": {"Year": "date", "Country Name": "text", "Population": "number"},
            "hint": {"date": "Year", "entity": "Country Name", "primary": "Population", "mkind": {"Population": "stock"}},
            "shape": {"measures": [{"column": "Population", "kind": "stock"}], "dimensions": ["Country Name"], "date": "Year"}}


def _ran(state, q):
    out = __import__("nl_analyst").run_steps(state["df"], state["kinds"], ns.plan_steps(state, q), state["hint"])
    st = next(s for s in out["steps"] if s.get("op") == "sql")
    assert st.get("ok"), st
    return st["args"]["sql"], st["table"]


def test_a_ranking_question_ranks_the_members_in_one_period():
    st = _panel_state()
    sql, tb = _ran(st, "Which country had the largest population in 2025?")
    assert "ORDER BY" in sql and "LIMIT" in sql and "'2025'" in sql, sql
    assert [r[0] for r in tb["rows"]] == ["Atlantis", "Borduria", "Carpania"], tb
    sql, tb = _ran(st, "Which countries have the smallest population?")          # no year: the latest in the file
    assert "'2025'" in sql and tb["rows"][0][0] == "Carpania", (sql, tb)
    sql, tb = _ran(st, "Top 2 countries by population in 2018")
    assert len(tb["rows"]) == 2 and tb["rows"][0][0] == "Atlantis", tb


def test_a_named_member_and_a_since_read_its_own_rows_by_year():
    st = _panel_state()
    sql, tb = _ran(st, "How did Carpania's population change since 2020?")
    assert "'Carpania'" in sql and ">= '2020-01-01'" in sql and "GROUP BY" not in sql, sql
    assert [r[0] for r in tb["rows"]] == [str(y) for y in range(2020, 2026)], tb         # years, never quarters
    ans = __import__("nl_explore").answer_of("q", [{"op": "sql", "ok": True, "args": {"sql": sql}, "table": tb}])
    assert ans["text"].startswith("Population (Carpania) went from") and "2020" in ans["text"] and "1,960" not in ans["text"], ans


def test_a_yearly_file_compares_years_when_no_period_is_named():
    st = _panel_state()
    rq = ns.read_question(st, "How has population changed?")
    assert rq["grain"] == "Y" and "compares years" in " ".join(rq["notes"]), rq
    assert rq["rank"] is None, rq
    assert ns.read_question(st, "What was the population in 2020?")["rank"] is None     # "what was" is not a ranking


def test_a_ranking_by_a_ratio_is_said_by_the_ratio_and_a_period_question_is_no_ranking():
    import nl_explore as nx
    rep = nb.run(open(SAMPLE, "rb").read(), "s.csv", "", None, AS_OF)
    st = nb._LAST_ANALYST
    assert ns.read_question(st, "Which month had the most revenue?")["rank"] is None
    out = json.loads(nb.analyst_more_json(nb.analyst_plan_json("Which region has the highest gross margin?"), "[]", "q"))
    # a ranking of the latest quarter names it (21 Oct 2026)
    assert re.match(r"gross margin pct in \d{4}-Q[1-4]: ", out["answer"]["text"]), out["answer"]["text"]


def test_one_series_is_asked_about_by_its_own_rows_never_quarters_hidden_as_small_cells():
    rows = "observation_date,RATEX\n" + "\n".join("%04d-%02d-01,%.3f" % (2000 + i // 12, i % 12 + 1, 3 + (i % 37) / 10.0) for i in range(240))
    nb.run(rows.encode(), "RATEX.csv", "", None, "2021-01-10")
    st = nb._LAST_ANALYST
    def ask(q):
        out = json.loads(nb.analyst_more_json(nb.analyst_plan_json(q), "[]", q))
        return out.get("answer") or {}
    a = ask("When was it highest?")
    assert ", highest: 6.6 in " in a["text"] and "No rows" not in a["text"], a
    a = ask("How does the rate now compare with 2005?")
    assert "2005" in a["text"] and "2019" in a["text"], a
    assert ns._natural_grain(st) == "M"


def test_answers_to_the_ais_own_queries_read_as_a_person_would_say_them():
    import nl_explore as nx
    def say(sql, cols, rows, **k):
        return nx.answer_of("q", [{"op": "sql", "ok": True, "args": {"sql": sql}, "table": {"columns": cols, "rows": rows}}], **k)["text"]
    assert say("SELECT yr, avg_u FROM y ORDER BY avg_u DESC LIMIT 10", ["yr", "avg_u"], [[1982, 9.71], [2010, 9.61]]) == \
        "avg u, highest: 9.71 in 1982, then 9.61 in 2010."                                  # a year, never "1,982"
    assert say("SELECT a FROM l", ["latest_reading", "avg_last_12m", "last_month_below_now"], [[2.78, 2.92, "2026-08-01"]]) == \
        "Latest reading 2.78; avg last 12m 2.92; last month below now 2026-08."
    assert say("SELECT metric, value FROM m", ["metric", "value"], [["latest", 4.2], ["reference_year_average", 3.68]]) == \
        "Latest 4.2; reference year average 3.68."
    assert say('SELECT "observation_date", "X" AS inflation FROM t ORDER BY "X" DESC LIMIT 10', ["observation_date", "inflation"],
               [["1980-06-01", 15.77], ["1980-05-01", 15.12]]) == "inflation, highest: 15.77 in 1980-06, then 15.12 in 1980-05."
    assert say("SELECT c, known, net_score FROM t ORDER BY net_score DESC", ["character", "known", "net_score"],
               [["Han", 814, 1.67], ["Luke", 825, 1.58]]).startswith("net score: Han (1.67)")


def test_the_years_a_question_names_frame_the_answer_when_the_rows_hold_them():
    import nl_explore as nx
    rows = [[y, v] for y, v in ((2001, 5.66), (2007, 7.31), (2008, 8.58), (2009, 14.57), (2019, 4.24), (2026, 5.23))]
    def say(q, rows=rows):
        return nx.answer_of(q, [{"op": "sql", "ok": True, "args": {"sql": "SELECT yr, detroit_rate FROM t ORDER BY yr"},
                                 "table": {"columns": ["yr", "detroit_rate"], "rows": rows}}])["text"]
    # the visitor's own question (18 Oct 2026): from 2008, never from the first row the query happened to return
    assert say("How did Detroit change from 2008 to 2026?").startswith("detroit_rate went from 8.58 in 2008 to 5.23 in 2026"), say("x 2008 to 2026")
    assert "peaked at 14.57 in 2009" in say("How did Detroit change from 2008 to 2026?")
    # no years named, or a year the rows do not hold: the rows' own first and last, never a year the table lacks
    assert say("How did Detroit change?").startswith("detroit_rate went from 5.66 in 2001")
    assert say("How did Detroit change from 1990 to 2026?").startswith("detroit_rate went from 5.66 in 2001")


def test_a_cut_off_top_list_never_names_its_last_row_the_lowest():
    import nl_explore as nx
    rows = [["Las Vegas-Henderson-North Las Vegas, NV", "2020-04", 34.1], ["Elkhart-Goshen, IN", "2020-04", 33.0],
            ["Atlantic City-Hammonton, NJ", "2020-05", 30.7], ["Monroe, MI", "2020-04", 25.4]]
    sql = 'SELECT "Metro area", "Year", "Unemployment rate (%)" FROM t ORDER BY "Unemployment rate (%)" DESC LIMIT 4'
    t = nx.answer_of("Which metro had the highest unemployment in 2020?", [{"op": "sql", "ok": True, "args": {"sql": sql},
                     "table": {"columns": ["Metro area", "Year", "Unemployment rate (%)"], "rows": rows}}])["text"]
    # the visitor's Ask (18 Oct 2026): Monroe is the 4th of a top list, never "the lowest"
    assert "lowest" not in t and t.startswith("Unemployment rate (%), highest: Las Vegas-Henderson-North Las Vegas, NV (34.1, 2020-04)"), t


def test_a_cut_off_series_never_ends_at_the_cut():
    import nl_explore as nx
    rows = [["2008-01", 7.4], ["2009-06", 15.6], ["2024-08", 4.8]]
    t = nx.answer_of("How did Detroit change from 2008 to 2026?", [{"op": "sql", "ok": True, "args": {"sql": "SELECT m, r FROM t"},
                     "numbers": {"rows": {"value": 220, "text": "220", "kind": "int"}},
                     "table": {"columns": ["Year", "Unemployment rate (%)"], "rows": rows}}])["text"]
    # 220 months, 200 kept (18 Oct 2026): never "went from ... to 4.8 in 2024-08" as if the series ended there, never a peak past the cut
    assert "went from" not in t and "peaked" not in t and "of 220" in t, t


def main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("  FAIL  %s: %s: %s" % (name, type(exc).__name__, str(exc)[:400]))
    print("\n%d/%d passed" % (len(tests) - bad, len(tests)))
    return 1 if bad else 0



def test_an_implicit_column_alias_is_the_querys_own_name():
    """20 Oct 2026: an AI's "SUM(units) u" was refused as an unknown column (the worker's checkSql agrees)."""
    cols = ["region", "units"]
    assert ns.check_sql("SELECT region, SUM(units) u FROM kept_rows GROUP BY 1 ORDER BY u DESC", cols)["ok"]
    assert ns.check_sql("SELECT region r FROM kept_rows", cols)["ok"]
    assert not ns.check_sql("SELECT nope FROM kept_rows", cols)["ok"]


def test_text_compares_without_case():
    """21 Oct 2026, live: the cleaner reads "Dog" as DOG; the AI wrote WHERE animal_type = 'Dog' from the file's own spelling
    and matched none of 24,443 dogs."""
    import pandas as pd
    st = {"df": pd.DataFrame({"animal_type": ["DOG"] * 30 + ["CAT"] * 20, "n": [1.0] * 50}), "kinds": {"animal_type": "text", "n": "number"}}
    out = ns.run_sql(st, "SELECT COUNT(*) AS c FROM kept_rows WHERE animal_type = 'Dog'")
    assert out["ok"] and out["rows"][0][0] == 30, out


def test_a_row_named_by_two_labels_is_named_by_both():
    """21 Oct 2026, live: "dogs: highest in 2024 (3,460), lowest in 2024 (1)", the 1 a rare outcome of 2024, not 2024 as a whole."""
    nx = __import__("nl_explore")
    cols = ["year", "outcome_type", "dogs"]
    rows = [["2023", "Adoption", 3100], ["2024", "Adoption", 3460], ["2024", "Lost", 1], ["2023", "Transfer", 900]]
    sql = "SELECT substr(datetime,1,4) AS year, outcome_type, COUNT(*) AS dogs FROM kept_rows GROUP BY year, outcome_type"
    t = nx.answer_of("q", [{"op": "sql", "ok": True, "args": {"sql": sql}, "table": {"columns": cols, "rows": rows}}])["text"]
    assert "2024, Adoption (3,460)" in t and "2024, Lost (1)" in t, t


def test_the_latest_period_is_the_latest_complete_one():
    """21 Oct 2026, live: a salon year ending on 1 Jan 2026 ranked its stylists on that one day (2026-Q1), unnamed in the answer."""
    import pandas as pd
    d = list(pd.date_range("2025-01-01", "2025-12-31", freq="D")) + [pd.Timestamp("2026-01-01")]
    st = {"df": pd.DataFrame({"date": d, "v": [1.0] * len(d)}), "kinds": {"date": "date", "v": "number"}}
    assert ns._latest_period(st, "Q") == "2025-Q4" and ns._latest_period(st, "M") == "2025-12", ns._latest_period(st, "Q")
    st2 = {"df": pd.DataFrame({"date": d[:-1], "v": [1.0] * (len(d) - 1)}), "kinds": {"date": "date", "v": "number"}}
    assert ns._latest_period(st2, "Q") == "2025-Q4"
    nx = __import__("nl_explore")
    sql = ("SELECT stylist, SUM(price) AS price FROM kept_rows WHERE strftime('%Y', date) || '-Q' || ((CAST(strftime('%m', date) AS "
           "INTEGER) + 2) / 3) = '2025-Q4' GROUP BY stylist ORDER BY price DESC LIMIT 10")
    t = nx.answer_of("q", [{"op": "sql", "ok": True, "args": {"sql": sql},
                            "table": {"columns": ["stylist", "price"], "rows": [["Rafael", 7000], ["Jonah", 6500]]}}])["text"]
    assert "in 2025-Q4" in t and t.index("2025-Q4") < t.index("Rafael"), t


def test_a_member_that_is_no_period_is_not_highest_in():
    """21 Oct 2026, live: "revenue: highest in Rafael (28,627), lowest in Mei (24,495)"."""
    nx = __import__("nl_explore")
    sql = 'SELECT "stylist", SUM("price") AS revenue FROM kept_rows GROUP BY "stylist"'
    rows = [["Mei", 24495], ["Rafael", 28627], ["Jonah", 27240]]
    t = nx.answer_of("q", [{"op": "sql", "ok": True, "args": {"sql": sql}, "table": {"columns": ["stylist", "revenue"], "rows": rows}}])["text"]
    assert t == "revenue: highest Rafael (28,627); lowest Mei (24,495).", t


def test_a_flow_with_no_period_named_is_ranked_over_the_whole_file():
    """22 Oct 2026, live: the same revenue ranking was the year's when the AI answered and one quarter's when its call failed."""
    st = sales_state()
    whole = ns.engine_sql(st, "Which region has the most revenue?")
    assert whole and "GROUP BY" in whole and "-Q" not in whole and "strftime" not in whole, whole
    q = ns.engine_sql(st, "Which region has the most revenue this quarter?")
    assert q and "-Q" in q, q


def test_which_member_is_answered_by_the_members_when_the_ai_split_them_by_month():
    """22 Oct 2026, live: "which stylist brings in the most revenue" got the AI's month x stylist query and the answer
    "highest in 2025-06, Jonah (3,069)"; the engine ranks the members over the whole file instead."""
    st = sales_state()
    nb._LAST_ANALYST.clear(); nb._LAST_ANALYST.update(st)             # the sales run's frame, whatever ran since
    q = "Which region brings in the most revenue?"
    ai = [{"op": "sql", "args": {"sql": "SELECT substr(order_date,1,7) AS month, region, SUM(revenue) AS revenue FROM kept_rows "
                                        "GROUP BY month, region ORDER BY revenue DESC"}}]
    out = json.loads(nb.analyst_more_json(json.dumps(ai), "[]", q))
    t = out["answer"]["text"]
    assert not re.search(r"\b20\d\d-\d\d\b", t), t
    d = raw()
    top = d.groupby("region")["revenue"].sum().sort_values(ascending=False).index[0]
    assert t.split(":", 1)[1].strip().startswith(top), (t, top)
    # a question about one period is the AI's to read: no re-ranking
    out2 = json.loads(nb.analyst_more_json(json.dumps(ai), "[]", "Which region brought in the most revenue in 2025?"))
    assert re.search(r"\b20\d\d-\d\d\b", out2["answer"]["text"]), out2["answer"]["text"]

if __name__ == "__main__":
    sys.exit(main())
