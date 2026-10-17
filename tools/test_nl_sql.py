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


def sales_state():
    """The engine's own state after a run of the sample (the frame the SQL panel reads)."""
    if not _STATE:
        with open(SAMPLE, "rb") as f:
            rep = nb.run(f.read(), "sample-sales-synthetic.csv", "", {}, AS_OF)
        assert rep["ok"], rep["error"]
        _STATE.update(dict(nb._LAST_ANALYST))
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


# ----------------------------------------------------------------------------------------------- the explanation
def _pandas_decompose(e, dim, p0, p1):
    """Rate and mix by group, recomputed with pivot tables (independent of nl_sql's loop)."""
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


def test_the_question_is_read():
    st = sales_state()
    rq = ns.read_question(st, QUESTION)
    assert rq["slice"] == {"region": "EMEA"}, rq["slice"]
    assert rq["grain"] == "Q"
    assert rq["measure"]["kind"] == "ratio" and rq["measure"]["num"] == ["revenue", "cost"] and rq["measure"]["den"] == "revenue"
    assert ns.read_question(st, "How did revenue in APAC move this month?")["grain"] == "M"
    rq = ns.read_question(st, "How did revenue for Online in Japan change?")
    assert rq["slice"] == {"country": "Japan", "channel": "Online"} and rq["measure"]["kind"] == "flow", rq


def test_each_split_adds_up_to_the_whole_change():
    st = sales_state()
    for q in (QUESTION, "Why did revenue fall in EMEA this quarter?", "What moved the average price per unit in APAC?"):
        ex = ns.explain(st, q)
        assert ex["ok"], (q, ex.get("why"))
        for key, comp in ex["comparisons"].items():
            assert comp["by"], (q, key)
            for b in comp["by"]:
                s = sum(x["effect"] for x in b["segments"])
                assert abs(s - comp["change"]) < 1e-6 * max(1.0, abs(comp["change"])), (q, key, b["dimension"], s, comp["change"])
                if b["kind"] == "ratio":
                    assert abs(b["rate"] + b["mix"] - comp["change"]) < 1e-9 * max(1, abs(comp["change"])), (q, b["dimension"])
                    assert abs(b["residual"]) < 1e-9


def test_the_planted_causes_are_found_with_their_magnitudes():
    st = sales_state()
    ex = ns.explain(st, QUESTION)
    assert ex["ok"], ex.get("why")
    d = raw()
    e = d[d.region == "EMEA"]
    p1, p0, py = pd.Period("2026Q3"), pd.Period("2026Q2"), pd.Period("2025Q3")
    agg = e.groupby("q")[["revenue", "cost"]].sum()
    gm = (agg.revenue - agg.cost) / agg.revenue * 100
    assert ex["period"]["latest"] == "2026 Q3" and ex["period"]["previous"] == "2026 Q2" and ex["period"]["year_ago"] == "2025 Q3"
    assert not ex["period"]["partial"], ex["period"]
    for k, p in (("latest", p1), ("previous", p0), ("year_ago", py)):
        assert abs(ex["values"][k] - gm[p]) < 1e-9, (k, ex["values"][k], gm[p])
    comp = ex["comparisons"]["vs_previous"]
    by = {b["dimension"]: b for b in comp["by"]}
    assert set(by) == {"country", "product_line", "channel"}, set(by)

    # cause 1, the mix: Accessories' share of EMEA revenue jumps; its mix effect is negative and the biggest of its split
    rate, mix, chg, W, R = _pandas_decompose(e, "product_line", p0, p1)
    seg = {s["group"]: s for s in by["product_line"]["segments"]}
    for g in rate.index:
        if g == "__pool":
            continue
        assert abs(seg[g]["rate"] - rate[g]) < 1e-9 and abs(seg[g]["mix"] - mix[g]) < 1e-9, (g, seg[g], rate[g], mix[g])
    assert abs(by["product_line"]["mix"] - mix.sum()) < 1e-9 and abs(by["product_line"]["rate"] - rate.sum()) < 1e-9
    assert mix.idxmin() == "Accessories" and mix["Accessories"] < -3.0, mix
    assert W.loc["Accessories", p1] > 2 * W.loc["Accessories", p0], W.loc["Accessories"]
    assert abs(by["product_line"]["mix"]) > abs(by["product_line"]["rate"]), "by product line the move is mostly mix"

    # cause 2, the discount: Online's own margin falls (rate), the biggest rate effect by channel
    rate_c, mix_c, _c, Wc, Rc = _pandas_decompose(e, "channel", p0, p1)
    segc = {s["group"]: s for s in by["channel"]["segments"]}
    assert abs(segc["Online"]["rate"] - rate_c["Online"]) < 1e-9, (segc["Online"], rate_c["Online"])
    assert rate_c.idxmin() == "Online" and rate_c["Online"] < -5.0, rate_c
    assert (Rc.loc["Online", p0] - Rc.loc["Online", p1]) * 100 > 15, Rc.loc["Online"]

    # the mix shows inside the two countries: their own margins fall (a rate effect by country), the others' do not
    rate_k, _mk, _ck, _Wk, Rk = _pandas_decompose(e, "country", p0, p1)
    segk = {s["group"]: s for s in by["country"]["segments"]}
    for g in ("Germany", "France"):
        assert abs(segk[g]["rate"] - rate_k[g]) < 1e-9 and rate_k[g] < -1.0, (g, rate_k[g])
    for g in ("United Kingdom", "Spain", "Italy"):
        assert rate_k[g] > -1.0, (g, rate_k[g])

    # the drivers the engine names: the planted causes at the top, in the direction of the change
    drv = [(x["dimension"], x["group"], x["effect_kind"]) for x in ex["drivers"]]
    assert ("product_line", "Accessories", "mix") in drv[:3], drv
    assert ("channel", "Online", "rate") in drv[:3], drv
    assert ("country", "Germany", "rate") in drv[:3], drv
    assert ("country", "France", "rate") in drv, drv
    assert all(x["effect"] < 0 for x in ex["drivers"])

    # the text: the lead sentence's figures are this test's own, rounded as printed
    lead = ex["text"][0]
    for v in (gm[p1], gm[p0], gm[py]):
        assert ("%.1f%%" % v) in lead, (v, lead)
    assert ("%.1f points" % abs(gm[p1] - gm[p0])) in lead, lead
    assert "never an average" in ex["text"][1]
    joined = " ".join(ex["text"])
    for dname, b in by.items():
        assert ("rate effect %+.1f points" % b["rate"]).replace("+-", "-") in joined.replace("rate effect +", "rate effect +"), (dname, joined)


def test_the_answer_record():
    st = sales_state()
    a = ns.answer(st, QUESTION)
    json.dumps(a, allow_nan=False)
    assert a["ok"] and a["sql_by"] == "engine" and a["query"]["ok"], a.get("query")
    q = a["query"]
    last = q["rows"][-1]
    d = raw()
    e = d[d.region == "EMEA"]
    agg = e.groupby("q")[["revenue", "cost"]].sum()
    assert last[0] == "2026-Q3" and abs(last[q["columns"].index("gross_margin_pct")] - round((1 - agg.cost.iloc[-1] / agg.revenue.iloc[-1]) * 100, 4)) < 1e-9
    assert a["chart"]["chart"] in ns._menu() and a["chart"]["by"] == "engine"
    assert a["pin"]["source"] == "ask-sql" and a["pin"]["filters"] == [{"column": "region", "value": "EMEA"}]
    assert a["pin"]["measure_def"]["den"] == "revenue"
    # the AI's SQL, refused, falls back to the engine's and says why
    b = ns.answer(st, QUESTION, "SELECT * FROM sqlite_master")
    assert b["sql_by"] == "engine" and "sqlite_master" in b["ai_refused"], b["ai_refused"]
    # the AI's SQL, run as written; its why with a digit is blanked; its chart checked
    c = ns.answer(st, QUESTION, "SELECT channel, SUM(revenue) FROM kept_rows WHERE region = 'EMEA' GROUP BY channel",
                  {"chart": "bar_chart", "args": ["channel", "revenue"], "title": "Revenue by channel, 2026 Q3"}, "it is 12% down")
    assert c["sql_by"] == "ai" and c["why"] == "" and c["chart"]["by"] == "ai" and c["chart"]["title"] == "Revenue by channel, 2026 Q3"
    for bad in ({"chart": "pie", "args": []}, {"chart": "bar_chart", "args": ["customer_email"]},
                {"chart": "bar_chart", "args": ["channel", "revenue", "cost", "units"]}):
        r = ns.answer(st, QUESTION, "", bad)
        assert r["chart_refused"] and r["chart"]["by"] == "engine", (bad, r["chart_refused"])
    t = ns.check_chart({"chart": "bar_chart", "args": ["channel"], "title": "Down 7.4 points"}, st)
    assert t["ok"] and t["spec"]["title"] == "", t
    # deterministic
    assert json.dumps(ns.answer(st, QUESTION), sort_keys=True) == json.dumps(a, sort_keys=True)


def test_the_adapter_wrappers():
    sales_state()
    nb._LAST_ANALYST.clear()
    nb._LAST_ANALYST.update({k: v for k, v in _STATE.items() if not k.startswith("_sql")})
    sch = json.loads(nb.sql_schema_json())
    assert sch["ok"] and sch["table"] == "kept_rows"
    roles = {c["name"]: c["role"] for c in sch["columns"]}
    assert roles["order_date"] == "date" and roles["revenue"] == "measure" and roles["region"] == "dimension"
    assert roles["order_id"] == "identifier"
    assert all(set(c) <= {"name", "type", "role", "unit", "kind"} for c in sch["columns"])
    a = json.loads(nb.sql_answer_json(QUESTION, "", "null", ""))
    assert a["ok"] and a["explain"]["ok"]
    nb._LAST_ANALYST.clear()
    assert json.loads(nb.sql_answer_json(QUESTION))["ok"] is False


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
