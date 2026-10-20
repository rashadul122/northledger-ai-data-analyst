#!/usr/bin/env python3
"""Tests for engine/nl_sales.py, the quarterly sales report from a CRM deals export (Track F; CONTRACT-v2 §5.19).

    NL_ENGINE_ROOT=<copy of northledger-core> python tools/test_nl_sales.py

On the SYNTHETIC CRM deals export (tools/make_sample_sales.py, engine/sample-crm-deals-synthetic.csv), run through the
whole engine (nl_browser.run) as a visitor's file would be, and on exports built here from it (other CRMs' column names,
columns taken away):
  * every metric recomputed independently with pandas (groupby on the raw CSV, not nl_sales' own loops): won revenue,
    won deals, average deal, win rates by segment, cycle medians and means, open and weighted pipeline, stalled deals,
    the top reps
  * the deals/size split adds up to the change exactly, overall and per segment, and equals the price-volume formula
  * the planted facts are found: growth from bigger (Enterprise) deals with fewer deals, SMB's win rate down, longer
    cycles, a stalled share of the pipeline; each finding's cause is the data's split or "driver to be confirmed"
  * a missing column is said plainly with what it costs (no created date: no cycle), never guessed
  * reps: withheld -> no rep section and the reason; coded -> Rep A/B/C and no code; kept -> names; kept but hidden ->
    letters, and no name anywhere in the record
  * fiscal quarters, the asked quarter, a target's coverage, the latest COMPLETE quarter by default
  * Salesforce, HubSpot and Pipedrive column names map to the same report
Prints PASS/FAIL per test, exits 1 on any failure.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, os.path.join(SITE, "engine"))
os.environ.setdefault("NL_BROWSER_STRICT", "1")

import pandas as pd  # noqa: E402

import nl_browser as nb  # noqa: E402
import nl_sales as nsl  # noqa: E402
import nl_scenarios as nsc  # noqa: E402

AS_OF = "2026-01-05"
SAMPLE = os.path.join(SITE, "engine", "sample-crm-deals-synthetic.csv")
NAMES = ["Avery Lindqvist", "Jordan Okafor", "Priya Raman", "Mateo Alvarez", "Sofia Brennan", "Noah Whitaker",
         "Lena Kowalski", "Caleb Mensah"]
_CACHE = {}
FAILS = []


def run(data: bytes = None, decisions=None, name="sample-crm-deals-synthetic.csv", settings=None):
    key = (data is None, json.dumps(decisions or {}, sort_keys=True), name, json.dumps(settings or {}, sort_keys=True),
           hash(data) if data else 0)
    if key in _CACHE:
        return _CACHE[key]
    if data is None:
        with open(SAMPLE, "rb") as f:
            data = f.read()
    rep = nb.run(data, name, "", decisions or {}, AS_OF)
    assert rep["ok"], rep["error"]
    s = rep["sales"]
    if settings:
        s = json.loads(nb.sales_json(json.dumps({"sha256": rep["input"]["sha256"], "settings": settings})))
    _CACHE[key] = (rep, s)
    return rep, s


def raw():
    d = pd.read_csv(SAMPLE, parse_dates=["Created Date", "Close Date", "Last Stage Change Date"])
    d["q"] = d["Close Date"].dt.to_period("Q").astype(str)
    d["won"] = d["Stage"] == "Closed Won"
    d["lost"] = d["Stage"] == "Closed Lost"
    d["open"] = ~(d["won"] | d["lost"])
    return d


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def test(fn):
    try:
        fn()
        print("PASS %s" % fn.__name__)
    except AssertionError as e:
        FAILS.append(fn.__name__)
        print("FAIL %s: %s" % (fn.__name__, e))
    return fn


# ----------------------------------------------------------------------------------------------- quarters
@test
def quarters_calendar_and_fiscal():
    import datetime as dt
    assert nsl.quarter_of(dt.date(2025, 11, 3)) == (2025, 4)
    assert nsl.quarter_of(dt.date(2025, 11, 3), 4) == (2026, 3)      # FY2026 = Apr 2025 to Mar 2026
    assert nsl.quarter_of(dt.date(2026, 2, 1), 4) == (2026, 4)
    assert nsl.quarter_of(dt.date(2025, 4, 1), 4) == (2026, 1)
    assert nsl.quarter_start(2026, 1, 4) == dt.date(2025, 4, 1) and nsl.quarter_end(2026, 4, 4) == dt.date(2026, 3, 31)
    assert nsl.quarter_start(2025, 4) == dt.date(2025, 10, 1) and nsl.quarter_end(2025, 4) == dt.date(2025, 12, 31)
    assert nsl.quarter_label(2026, 3, 4) == "FY2026 Q3" and nsl.quarter_label(2025, 4) == "2025 Q4"
    for fs in range(1, 13):
        for m in range(1, 13):
            d = dt.date(2025, m, 15)
            fy, q = nsl.quarter_of(d, fs)
            assert nsl.quarter_start(fy, q, fs) <= d <= nsl.quarter_end(fy, q, fs), (fs, m)


# ----------------------------------------------------------------------------------------------- the headline
@test
def mapping_reads_the_salesforce_names():
    _rep, s = run(decisions={"opportunity_owner": "keep"})
    m = {x["role"]: x["column"] for x in s["mapping"]}
    assert m == {"id": "Opportunity ID", "amount": "Amount", "stage": "Stage", "outcome": "Stage",
                 "segment": "Account Segment", "owner": "Opportunity Owner", "created": "Created Date",
                 "close": "Close Date", "probability": "Probability (%)", "stage_changed": "Last Stage Change Date"}, m
    assert s["fit"]["ok"] and not s["cannot"], s["cannot"]


@test
def default_quarter_is_the_latest_complete_one():
    _rep, s = run()
    q = s["quarter"]
    # deals closed on 2 to 5 January 2026 make 2026 Q1 the latest quarter, but it is not over on the export date
    assert q["label"] == "2025 Q4" and q["prev_label"] == "2025 Q3" and q["complete"], q
    assert [x["label"] for x in s["quarters"]][:2] == ["2026 Q1", "2025 Q4"] and not s["quarters"][0]["complete"]


@test
def headline_metrics_match_pandas():
    _rep, s = run()
    d = raw()
    g = s["growth"]
    for qq, key, nkey, akey in (("2025Q3", "prev", "deals_prev", "avg_prev"), ("2025Q4", "cur", "deals_cur", "avg_cur")):
        w = d[d.won & (d.q == qq)]
        assert close(g[key], w.Amount.sum()), (qq, g[key], w.Amount.sum())
        assert g[nkey] == len(w), (qq, g[nkey], len(w))
        assert close(g[akey], w.Amount.mean(), 1e-6), (qq, g[akey], w.Amount.mean())
    k = {x["id"]: x for x in s["kpis"]}
    c4 = d[(d.won | d.lost) & (d.q == "2025Q4")]
    assert close(k["win_rate"]["value"], 100.0 * c4.won.sum() / len(c4), 1e-5), k["win_rate"]
    assert k["revenue"]["text"] == format(int(round(g["cur"])), ","), k["revenue"]


@test
def growth_split_adds_up_and_is_the_price_volume_formula():
    _rep, s = run()
    g = s["growth"]
    assert g["adds_up"]
    assert close(g["count_effect"] + g["size_effect"], g["change"]), g
    size, count = nsc.price_volume(g["prev"], g["deals_prev"], g["cur"], g["deals_cur"])
    assert close(size, g["size_effect"]) and close(count, g["count_effect"])
    # count effect = the change in deals at the old average deal
    assert close(g["count_effect"], (g["deals_cur"] - g["deals_prev"]) * g["prev"] / g["deals_prev"])
    for r in s["segments"]["rows"]:
        if r["change"] is not None:
            assert close(r["count_effect"] + r["size_effect"], r["change"]), r["label"]
    wf = next(c for c in s["charts"] if c["chart"] == "pvm_waterfall")
    st = wf["data"]["steps"]
    assert close(st[0]["value"] + st[1]["value"] + st[2]["value"], st[3]["value"]), st


@test
def segments_match_pandas():
    _rep, s = run()
    d = raw()
    rows = {r["label"]: r for r in s["segments"]["rows"]}
    assert set(rows) == {"Enterprise", "Mid-Market", "SMB"}, rows.keys()
    for seg in rows:
        for qq, sfx in (("2025Q3", "prev"), ("2025Q4", "cur")):
            x = d[(d["Account Segment"] == seg) & (d.q == qq)]
            w = x[x.won]
            assert close(rows[seg]["rev_" + sfx], w.Amount.sum()), (seg, qq)
            assert rows[seg]["won_" + sfx] == len(w)
            assert close(rows[seg]["win_" + sfx], 100.0 * x.won.sum() / (x.won.sum() + x.lost.sum()), 1e-5), (seg, qq)
            assert close(rows[seg]["avg_" + sfx], w.Amount.mean(), 1e-6)


@test
def cycle_matches_pandas():
    _rep, s = run()
    d = raw()
    cy = s["cycle"]
    for qq, sfx in (("2025Q3", "prev"), ("2025Q4", "cur")):
        w = d[d.won & (d.q == qq)]
        days = (w["Close Date"] - w["Created Date"]).dt.days
        assert close(cy["median_" + sfx], days.median()), (qq, cy["median_" + sfx], days.median())
        assert close(cy["mean_" + sfx], days.mean(), 1e-3), (qq, cy["mean_" + sfx], days.mean())


@test
def pipeline_matches_pandas():
    _rep, s = run()
    d = raw()
    o = d[d.open]
    p = s["pipeline"]
    assert p["n"] == len(o) and close(p["value"], o.Amount.sum()), (p["n"], len(o))
    assert close(p["weighted"], (o.Amount * o["Probability (%)"] / 100.0).sum()), p["weighted"]
    asof = pd.Timestamp(AS_OF)
    past = o["Close Date"] < asof
    quiet = (~past) & ((asof - o["Last Stage Change Date"]).dt.days > 30)
    st = p["stalled"]
    assert st["past_close"] == int(past.sum()) and st["no_change"] == int(quiet.sum()), st
    assert close(st["value"], o[past | quiet].Amount.sum()), st
    by = {x["label"]: x for x in p["stages"]}
    for stage, x in o.groupby("Stage"):
        assert close(by[stage]["value"], x.Amount.sum()), stage
    # in the order of their probability
    assert [x["label"] for x in p["stages"]] == ["Prospecting", "Qualification", "Proposal/Price Quote",
                                                 "Negotiation/Review"], [x["label"] for x in p["stages"]]
    nq = o[o["Close Date"].dt.to_period("Q").astype(str) == "2026Q1"]
    assert p["next"]["label"] == "2026 Q1" and close(p["next"]["value"], nq.Amount.sum())


@test
def top_reps_match_pandas():
    _rep, s = run(decisions={"opportunity_owner": "keep"})
    d = raw()
    q4 = d[d.q == "2025Q4"]
    g = q4[q4.won].groupby("Opportunity Owner").Amount.agg(["sum", "count"])
    g = g[g["count"] >= 5].sort_values("sum", ascending=False).head(3)
    rows = s["reps"]["rows"]
    assert [r["label"] for r in rows] == list(g.index), ([r["label"] for r in rows], list(g.index))
    for r in rows:
        x = q4[q4["Opportunity Owner"] == r["label"]]
        assert close(r["revenue"], x[x.won].Amount.sum()) and r["won"] == int(x.won.sum())
        assert close(r["win_rate"], 100.0 * x.won.sum() / (x.won.sum() + x.lost.sum()), 1e-5)


# ----------------------------------------------------------------------------------------------- the planted facts
@test
def planted_growth_from_bigger_enterprise_deals():
    _rep, s = run()
    g = s["growth"]
    assert g["change"] > 0 and g["size_effect"] > 0 and g["count_effect"] < 0 and g["lead"] == "bigger deals", g
    ent = next(r for r in s["segments"]["rows"] if r["label"] == "Enterprise")
    assert ent["avg_cur"] / ent["avg_prev"] > 1.3 and ent["size_effect"] > 0.9 * ent["change"], ent
    w = s["findings"]["working"][0]
    assert w["subject"] == "Enterprise" and "bigger deals" in w["text"] and "driver to be confirmed" in w["text"], w
    assert "bigger deals" in s["summary"]["headline"] and "Enterprise grew most" in s["summary"]["lines"][0]


@test
def planted_smb_win_rate_down():
    _rep, s = run()
    smb = next(r for r in s["segments"]["rows"] if r["label"] == "SMB")
    assert smb["win_prev"] - smb["win_cur"] >= 10, smb
    f = [i for i in s["findings"]["change"] if i["kind"] == "segment_win_rate" and i["subject"] == "SMB"]
    assert f and f[0]["driver"] == "driver to be confirmed" and "why: driver to be confirmed" in f[0]["text"], f
    assert any("SMB win rate" in t for t in s["summary"]["change"]), s["summary"]["change"]


@test
def planted_longer_cycles_and_stalled_deals():
    _rep, s = run()
    cy = s["cycle"]
    assert cy["median_cur"] - cy["median_prev"] >= 10, cy
    assert all(x["median_cur"] > x["median_prev"] for x in cy["segments"]), cy["segments"]
    kinds = [i["kind"] for i in s["findings"]["change"]]
    assert "cycle" in kinds and "stalled" in kinds, kinds
    assert 15 <= s["pipeline"]["stalled"]["share"] <= 40, s["pipeline"]["stalled"]


@test
def every_finding_names_its_driver_or_the_data_split():
    _rep, s = run(decisions={"opportunity_owner": "keep"}, settings={"target": 900000})
    for side in ("working", "change"):
        for i in s["findings"][side]:
            assert i["driver"] in ("driver to be confirmed", "") and (
                "driver to be confirmed" in i["text"] or i["kind"] == "coverage"), i
            if i["kind"] == "segment_revenue":
                assert "the data shows it came from" in i["text"], i


# ----------------------------------------------------------------------------------------------- missing columns
def without(*cols, rename=None):
    d = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
    d = d.drop(columns=list(cols))
    if rename:
        d = d.rename(columns=rename)
    b = io.StringIO()
    d.to_csv(b, index=False)
    return b.getvalue().encode("utf-8")


@test
def missing_columns_are_said_not_guessed():
    _rep, s = run(without("Created Date", "Probability (%)", "Last Stage Change Date", "Account Segment"),
                  name="deals-no-dates.csv")
    roles = {c["role"]: c["text"] for c in s["cannot"]}
    assert set(roles) >= {"created", "probability", "stage_changed", "segment"}, roles
    assert "sales cycle" in roles["created"] and "cannot be measured" in roles["created"]
    assert s["ok"] and not s["cycle"]["ok"] and s["cycle"]["why"] == nsl.CANNOT["created"]
    assert not s["segments"]["ok"] and s["pipeline"]["weighted"] is None
    assert s["pipeline"]["stalled"]["no_change"] == 0 and not s["pipeline"]["stalled"]["by_change"]
    assert not any(k["id"] == "cycle" for k in s["kpis"])


@test
def no_amount_means_no_report_and_says_why():
    _rep, s = run(without("Amount"), name="deals-no-amount.csv")
    assert not s["ok"] and not s["fit"]["ok"] and "Amount" in s["fit"]["missing"], s["fit"]
    assert "needs Amount" in s["why"] or "needs Amount" in s["fit"]["why"], s["why"]
    assert any(c["role"] == "amount" and "no revenue" in c["text"] for c in s["cannot"])


@test
def no_close_date_means_no_quarter():
    _rep, s = run(without("Close Date"), name="deals-no-close.csv")
    assert not s["ok"] and "Close date" in s["fit"]["missing"], s["fit"]


# ----------------------------------------------------------------------------------------------- reps and privacy
@test
def reps_withheld_coded_kept_hidden():
    _rep, s = run()                                         # the default decision: withhold
    assert not s["reps"]["ok"] and s["reps"]["why"] == nsl.OWNER_WITHHELD
    assert any(c["role"] == "owner" and c["text"] == nsl.OWNER_WITHHELD for c in s["cannot"])
    blob = json.dumps(s)
    assert not any(n in blob for n in NAMES), "a withheld name is in the record"
    _rep, s = run(decisions={"Opportunity Owner": "code"})
    labels = [r["label"] for r in s["reps"]["rows"]]
    blob = json.dumps(s)
    assert labels == ["Rep A", "Rep B", "Rep C"] and "rdc_" not in blob and not any(n in blob for n in NAMES), labels
    _rep, s = run(decisions={"opportunity_owner": "keep"})
    assert all(r["label"] in NAMES for r in s["reps"]["rows"]) and s["reps"]["names"]
    _rep, s = run(decisions={"opportunity_owner": "keep"}, settings={"rep_names": False})
    blob = json.dumps(s)
    assert [r["label"] for r in s["reps"]["rows"]] == ["Rep A", "Rep B", "Rep C"] and not any(n in blob for n in NAMES)
    # the same ranking whichever way the reps are named
    _r, kept = run(decisions={"opportunity_owner": "keep"})
    assert [r["revenue"] for r in kept["reps"]["rows"]] == [r["revenue"] for r in s["reps"]["rows"]]


@test
def small_cells_are_hidden():
    d = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
    # a fourth segment with 3 won deals in each quarter
    idx = d[(d["Stage"] == "Closed Won") & (d["Account Segment"] == "Mid-Market")].index[:6]
    d.loc[idx, "Account Segment"] = "Public Sector"
    b = io.StringIO()
    d.to_csv(b, index=False)
    _rep, s = run(b.getvalue().encode(), name="deals-small.csv")
    ps = next(r for r in s["segments"]["rows"] if r["label"] == "Public Sector")
    assert ps["rev_cur"] is None or ps["won_cur"] >= 5
    assert "<5" in ps["texts"].values(), ps["texts"]
    assert ps["change"] is None and s["segments"]["note"], s["segments"]["note"]


# ----------------------------------------------------------------------------------------------- settings
@test
def fiscal_quarter_asked_quarter_and_target():
    _rep, s = run(settings={"fiscal_start": 4})
    assert s["quarter"]["label"] == "FY2026 Q3" and s["quarter"]["prev_label"] == "FY2026 Q2", s["quarter"]
    _rep, base = run()
    assert close(s["growth"]["cur"], base["growth"]["cur"])        # Oct to Dec in both calendars
    _rep, s = run(settings={"quarter": "2025 Q3"})
    assert s["quarter"]["label"] == "2025 Q3" and s["quarter"]["prev_label"] == "2025 Q2"
    assert s["growth"]["suppressed"] and "Fewer than 5" in s["growth"]["text"]   # nothing closed in 2025 Q2
    _rep, s = run(settings={"target": "1,000,000"})
    cv = s["pipeline"]["coverage"]
    assert cv and cv["label"] == "2026 Q1" and close(cv["x"], s["pipeline"]["next"]["value"] / 1e6, 1e-4), cv
    assert "cover" in s["pipeline"]["text"]
    _rep, s = run(settings={"currency": "$"})
    assert s["kpis"][0]["text"].startswith("$"), s["kpis"][0]


@test
def the_visitor_may_choose_a_column():
    _rep, s = run(settings={"columns": {"segment": "", "created": "Last Stage Change Date"}})
    m = {x["role"]: (x["column"], x["how"]) for x in s["mapping"]}
    assert m["segment"] == (None, "visitor") and m["created"] == ("Last Stage Change Date", "visitor"), m
    assert not s["segments"]["ok"]


# ----------------------------------------------------------------------------------------------- other CRMs
@test
def hubspot_and_pipedrive_names_map():
    d = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
    hs = d.rename(columns={"Opportunity ID": "Record ID", "Account Segment": "Customer Tier", "Opportunity Owner": "Deal owner",
                           "Stage": "Deal Stage", "Probability (%)": "Deal probability", "Created Date": "Create Date",
                           "Last Stage Change Date": "Last Activity Date"})
    hs["Deal Stage"] = hs["Deal Stage"].map(lambda v: {"Closed Won": "closedwon", "Closed Lost": "closedlost"}.get(v, v.lower()))
    b = io.StringIO()
    hs.to_csv(b, index=False)
    _rep, s = run(b.getvalue().encode(), name="hubspot-deals.csv")
    m = {x["role"]: x["column"] for x in s["mapping"]}
    assert m["outcome"] == "Deal Stage" and m["created"] == "Create Date" and m["segment"] == "Customer Tier" and \
        m["probability"] == "Deal probability" and m["id"] == "Record ID" and m["stage_changed"] == "Last Activity Date", m
    _r, base = run()
    assert close(s["growth"]["cur"], base["growth"]["cur"]) and close(s["segments"]["rows"][0]["win_cur"],
                                                                    base["segments"]["rows"][0]["win_cur"])
    pd_ = d.copy()
    pd_["Status"] = pd_["Stage"].map(lambda v: "won" if v == "Closed Won" else "lost" if v == "Closed Lost" else "open")
    pd_["Stage"] = pd_["Stage"].map(lambda v: "Closed" if v.startswith("Closed") else v)
    pd_ = pd_.rename(columns={"Opportunity ID": "Deal ID", "Amount": "Value", "Account Segment": "Segment",
                              "Created Date": "Deal created", "Close Date": "Expected close date"})
    b = io.StringIO()
    pd_.to_csv(b, index=False)
    _rep, s = run(b.getvalue().encode(), name="pipedrive-deals.csv")
    m = {x["role"]: x["column"] for x in s["mapping"]}
    assert m["outcome"] == "Status" and m["stage"] == "Stage" and m["amount"] == "Value" and m["close"] == "Expected close date", m
    assert close(s["growth"]["cur"], base["growth"]["cur"]) and close(s["pipeline"]["value"], base["pipeline"]["value"])


@test
def a_file_that_is_not_deals_does_not_fit():
    rep = nb.run(open(os.path.join(SITE, "engine", "sample-messy.csv"), "rb").read(), "sample-messy.csv", "", {}, "2026-09-15")
    s = rep["sales"]
    assert not s["ok"] and not s["fit"]["ok"] and s["fit"]["missing"], s["fit"]


@test
def record_is_json_safe_and_charts_are_registry_records():
    _rep, s = run(decisions={"opportunity_owner": "keep"})
    json.dumps(s, allow_nan=False)
    import nl_viz as nv
    ids = set()
    for c in s["charts"]:
        assert c["kind"] == nv.REGISTRY[c["chart"]]["kind"] and c["id"].startswith("viz.8") and c["id"] not in ids
        ids.add(c["id"])
        assert nv.record_bytes(c) <= nv.bytes_cap(c["kind"]), (c["id"], nv.record_bytes(c))
        assert c["table"]["rows"] and c["summary"] and c["title"]
    assert [c["chart"] for c in s["charts"]] == ["pvm_waterfall", "grouped_bar", "grouped_bar", "grouped_bar", "bar_chart"]


@test
def the_registry_engine_call_form():
    """build(inputs, settings): Track E's deliver_json hands every input's kept rows; the deals one is read."""
    d = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False).drop(columns=["Opportunity Owner"])
    other = pd.DataFrame({"x": ["1"]})
    s = nsl.build([{"role": "other", "primary": True, "df": other}, {"role": "deals", "primary": False, "df": d}],
                  {"as_of": AS_OF})
    _rep, base = run()
    assert s["ok"] and close(s["growth"]["cur"], base["growth"]["cur"]) and s["quarter"]["label"] == "2025 Q4", s.get("why")
    assert not nsl.build([], {})["ok"]


@test
def an_old_export_is_read_as_of_its_last_activity_not_today():
    """20 Oct 2026, live: the sample (last activity 4 Jan 2026) read on 20 Oct 2026 reported "2026 Q1 down 98.7%" (two deals cut off by
    the export) and every open deal stalled. The export date is read off the deals; a date the visitor chose stands."""
    d = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
    s = nsl.build(d, {c: c for c in d.columns}, "2026-10-20", {})
    assert s["ok"] and s["as_of"] == "2026-01-04" and s["quarter"]["label"] == "2025 Q4", (s.get("as_of"), s.get("quarter"))
    assert s["notes"][0].startswith("The export appears to date from 2026-01-04"), s["notes"][0]
    assert s["growth"]["cur"] > s["growth"]["prev"], s["growth"]
    chosen = nsl.build(d, {c: c for c in d.columns}, "2026-10-20", {"as_of": "2026-10-20"})
    assert chosen["as_of"] == "2026-10-20" and "as_of_read" not in chosen
    fresh = nsl.build(d, {c: c for c in d.columns}, AS_OF, {})
    assert fresh["as_of"] == AS_OF and "as_of_read" not in fresh


if __name__ == "__main__":
    print("%d failed" % len(FAILS) if FAILS else "ALL PASS")
    sys.exit(1 if FAILS else 0)
