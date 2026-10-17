#!/usr/bin/env python3
"""Tests for engine/nl_sql.py, Ask with SQL (Track B; CONTRACT §5.13).

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
    assert out["ok"] and out["small_cells"]["state"] == "applied", out["small_cells"]
    assert out["small_cells"]["hidden"] == int((n < 5).sum()) > 0, (out["small_cells"], int((n < 5).sum()))
    assert out["row_count"] == int((n >= 5).sum())
    shown = {tuple(r[:3]) for r in out["rows"]}
    assert shown == {k for k, v in n.items() if v >= 5}
    sub = ns.run_sql(st, "SELECT AVG(r) FROM (SELECT country, SUM(revenue) AS r FROM kept_rows GROUP BY country)")
    assert sub["ok"] and sub["small_cells"]["state"] == "not_feasible" and "not" in sub["small_cells"]["note"], sub


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
        assert s["numbers"]["partial"]["text"] == ""

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


def test_the_sample_is_the_generator_output():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_sample_sales.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


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


if __name__ == "__main__":
    sys.exit(main())
