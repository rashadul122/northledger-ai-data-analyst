#!/usr/bin/env python3
"""
The WAVE 5e regression pack: the 21 defects an independent adversarial reviewer confirmed in the wave 5d build (bfcdc66), and the
fresh-seed failure (fuzz seed 3160), each as a small table in tools/fixtures/structure/regress/ and a test with an EXPLICIT
assertion of the right outcome: the true figure, or a plain "one member shown" / refusal statement, and never a confident wrong
number. Every case says in its docstring what the old engine printed.

    python tools/test_nl_regress.py

Self-running like the other suites: prints PASS/FAIL per test, exits 1 on any failure. One engine process; no network.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import sys
import time

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ADAPTER_DIR = os.path.abspath(os.environ.get("NL_BROWSER_DIR") or os.path.join(SITE, "engine"))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ADAPTER_DIR)
sys.path.insert(0, os.path.join(HERE, "fixtures", "structure"))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import nl_browser as NB  # noqa: E402
import nl_structure as NS  # noqa: E402
import make_cubes as MC  # noqa: E402

AS_OF = "2026-09-30"
REGRESS = os.path.join(HERE, "fixtures", "structure", "regress")


# ----------------------------------------------------------------------------- helpers
def fixture(name: str) -> bytes:
    p = os.path.join(REGRESS, name)
    if not os.path.exists(p) and os.path.exists(p + ".gz"):
        p += ".gz"
    with (gzip.open(p, "rb") if p.endswith(".gz") else open(p, "rb")) as fh:
        return fh.read()


def frame(name: str) -> "pd.DataFrame":
    return pd.read_csv(io.BytesIO(fixture(name)), dtype=str, keep_default_na=False)


def run_bytes(data: bytes, decisions=None, plan=None, name="table.csv"):
    NB._PROFILE_CACHE.clear()
    d = dict(decisions or {})
    if plan is not None:
        d["__plan__"] = plan
    return NB.run(data, name, "", d, AS_OF)


def run_file(name: str, decisions=None, plan=None):
    return run_bytes(fixture(name), decisions, plan)


PLAN = {"goal": "How did sales change?", "columns": [{"name": "VALUE", "semantic_type": "flow_amount", "role": "target"}],
        "operations": [], "primary": "VALUE", "analyses": []}


def est(rep):
    return rep.get("estimand") or {}


def st(rep):
    return rep.get("structure") or {}


def figs(rep):
    """(prior, latest, change_pct) of the estimand in base units, or None."""
    f = est(rep).get("figures") or {}
    if not f or (f.get("latest") or {}).get("value") is None:
        return None
    return (f["prior"]["value"], f["latest"]["value"], (f.get("change_pct") or {}).get("value"))


def headline(rep):
    return str((rep.get("story") or {}).get("headline") or "")


def dim(rep, col):
    return next((d for d in st(rep).get("dims") or [] if d.get("column") == col), None)


def refused(rep) -> bool:
    """A plain refusal: no estimand and the story says the business analysis did not run."""
    return not est(rep) and "business analysis did not run" in headline(rep)


def one_member_shown(rep) -> bool:
    """One member is shown and the text says it is not a total: the estimand names it ("one member shown")."""
    t = json.dumps([est(rep).get("text"), est(rep).get("excluded"), est(rep).get("single_member")], default=str)
    return "one member shown" in t


def close(a, b, rel=1e-6):
    return a is not None and b is not None and abs(a - b) <= rel * max(1.0, abs(b))


def window_sums(df: "pd.DataFrame", mask, scale: float = 1.0, date: str = "REF_DATE", value: str = "VALUE"):
    """The plain truth, from the file alone: (prior 12 months, latest 12 months) of the selected rows' VALUE added up by month,
    the windows ending at the last month any selected row has a value, in base units."""
    sub = df[mask].copy()
    sub["v"] = pd.to_numeric(sub[value].str.replace(",", ""), errors="coerce")
    sub = sub[sub["v"].notna()]
    sub["m"] = sub[date].str[:7]
    by = sub.groupby("m")["v"].sum() * scale
    months = sorted(by.index)
    last = months[-1]
    y, mo = int(last[:4]), int(last[5:7])

    def shift(k):
        i = y * 12 + mo - 1 + k
        return "%04d-%02d" % (i // 12, i % 12 + 1)
    lat = [m for m in months if shift(-11) <= m <= last]
    pri = [m for m in months if shift(-23) <= m <= shift(-12)]
    return float(by[pri].sum()), float(by[lat].sum())


def _as_engine_reads(rep, prior, latest):
    """A flow's window figure is a sum; a level's (a rate, a stock, an ambiguous count) is the mean of its 12 months."""
    if str((est(rep).get("measure") or {}).get("aggregation") or "").startswith("mean"):
        return prior / 12.0, latest / 12.0
    return prior, latest


def check_truth(rep, prior, latest, what=""):
    prior, latest = _as_engine_reads(rep, prior, latest)
    f = figs(rep)
    assert f is not None, "%s: no figure; headline: %s" % (what, headline(rep))
    assert close(f[0], prior) and close(f[1], latest), \
        "%s: engine prior/latest %r, truth %r; headline: %s" % (what, f[:2], (prior, latest), headline(rep))


def safe_or_true(rep, prior, latest, what=""):
    """The right figure, or a plain statement that no total is shown: never a different figure."""
    prior, latest = _as_engine_reads(rep, prior, latest)
    f = figs(rep)
    if f is not None and close(f[0], prior) and close(f[1], latest):
        return "figure"
    assert refused(rep) or one_member_shown(rep), \
        "%s: a figure that is not the truth and no statement; figures %r truth %r; headline: %s" % (what, f, (prior, latest), headline(rep))
    return "one member" if one_member_shown(rep) else "refusal"


# ----------------------------------------------------------------------------- the 21 findings and seed 3160
def test_r01_a_sum_check_that_cannot_fail_never_declares_a_branch_the_total_of_the_others():
    """Finding 1. Five branches with 0 to 2 events a month and NO total row: the old engine declared the biggest the total of the
    other four ("sum-checked: 48 of 48 cells within tolerance"; the tolerance of 2.5 counts is as big as the data) and printed it
    alone, +0.0%. The truth is the sum of the five, 8 then 9 events (+12.5%). A business export is added up; an official table
    is added up (a geographic dimension) or shows one member and says so."""
    df = frame("r01_business_small_counts.csv")
    rep = run_file("r01_business_small_counts.csv")
    pri, lat = window_sums(df, df["Branch"] != "")
    assert (pri, lat) == (8.0, 9.0), (pri, lat)
    # a business export no relation is found in is read by the engine's own analysis, which adds its rows: the change is the sum's
    import check_business_corpus as CB
    said = CB.summarise(rep)
    assert not said["refused"] and said["pct"] is not None, said
    assert abs(said["pct"] - 100.0 * (lat / pri - 1.0)) < 1e-6, (said["pct"], 100.0 * (lat / pri - 1.0))
    assert "sum-checked" not in json.dumps(rep.get("estimand"))
    dfo = frame("r01_official_small_counts.csv")
    repo = run_file("r01_official_small_counts.csv")
    pri, lat = window_sums(dfo, dfo["GEO"] != "", 1.0)
    assert (pri, lat) == (8.0, 9.0), (pri, lat)
    got = safe_or_true(repo, pri, lat, "official layout")
    assert "sum-checked" not in json.dumps(repo.get("estimand")) or got == "figure"


def test_r02_a_combined_member_is_never_added_to_its_own_parts_when_two_parts_report_in_disjoint_periods():
    """Finding 2. Inland provinces = Birch + Cedar; Dune reports months 1 to 20 only, Elm months 29 to 48 only. The old engine's
    3-cell fingerprint needed a month with every part present, found none, read "none found" as "no combined member" and added
    Inland to Birch and Cedar: $262.4M, 36% too high. The truth is the five real regions: $183.4M then $193.3M (+5.41%)."""
    df = frame("r02_combined_disjoint_periods.csv")
    rep = run_file("r02_combined_disjoint_periods.csv")
    pri, lat = window_sums(df, df["GEO"] != "Inland provinces", 1000.0)
    assert round(lat / 1e6, 1) == 193.3 and round(pri / 1e6, 1) == 183.4, (pri, lat)
    check_truth(rep, pri, lat, "five real regions")
    g = dim(rep, "GEO")
    assert g["role"] == "parts" and "Inland provinces" in (g.get("combined") or {}), g


def test_r03_two_bases_of_one_quantity_are_never_added():
    """Finding 3. Current prices and chained dollars, both "Dollars", no total row: the old engine read the absence of a relation
    as proof that the two are disjoint parts and printed their sum, $128.1M (2x). One member is shown, never the sum."""
    df = frame("r03_current_and_chained_prices.csv")
    rep = run_file("r03_current_and_chained_prices.csv")
    both = window_sums(df, df["Prices"] != "", 1000.0)
    cur = window_sums(df, df["Prices"] == "Current prices", 1000.0)
    f = figs(rep)
    assert f is None or not close(f[1], both[1]), "the two bases were added: %r" % (f,)
    assert one_member_shown(rep) or refused(rep), headline(rep)
    if f is not None:
        chained = window_sums(df, df["Prices"] != "Current prices", 1000.0)
        assert close(f[1], cur[1]) or close(f[1], chained[1]), (f, cur, chained)


def test_r04_a_seasonally_adjusted_copy_far_from_the_unadjusted_one_is_never_added_to_it():
    """Finding 4. The adjusted copy's annual level 5% above the unadjusted one (the 3% constant of wave 5d was the StatCan
    benchmarking): the old engine added the two, $146.5M. Never again, whatever the gap: the unadjusted series is shown (its figure
    is the truth), as an adjustment pair when their shape says so, else as one member that is not the total. A copy made the way
    an agency makes it (the adjusted series is the unadjusted one over its seasonal factors, so the two share their noise) is found
    by its SHAPE at 5% and at 10%: the dimension is an adjustment and the headline is the unadjusted copy."""
    for name in ("r04_sa_copy_5pct_above.csv", "r04_sa_copy_2pct_control.csv"):
        df = frame(name)
        rep = run_file(name)
        both = window_sums(df, df["Adjustments"] != "", 1000.0)
        f = figs(rep)
        assert f is None or not close(f[1], both[1]), "%s: the two copies were added: %r" % (name, f)
        pri, lat = window_sums(df, df["Adjustments"] == "Unadjusted", 1000.0)
        got = safe_or_true(rep, pri, lat, name)
        a = dim(rep, "Adjustments")
        assert a["total"] == "Unadjusted" and a["role"] in ("adjustment", "single"), a
    import make_cubes as MC
    for gap in (0.05, 0.10):
        data = MC.sa_copy(gap=gap)
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
        rep = run_bytes(data)
        a = dim(rep, "Adjustments")
        assert a["role"] == "adjustment" and a["total"] == "Unadjusted", (gap, a)
        pri, lat = window_sums(df, df["Adjustments"] == "Unadjusted", 1000.0)
        check_truth(rep, pri, lat, "shared-noise copy %.0f%% above" % (100 * gap))


def test_r05_real_parts_whose_labels_hold_an_alternative_word_are_parts_not_alternative_totals():
    """Finding 5. "Less than high school", "Less than 15 years", "Persons without disabilities", "Languages other than English or
    French" are ordinary parts. The old engine matched the word and DROPPED the member from a no-total headline ("an alternative
    total: never added to the parts"), 4% short and "in the published totals". A name nominates; evidence decides. With a total row
    the members add up to it (all parts, none an alternative); without one nothing can verify the member, and the dimension is not
    positively identified as parts, so ONE member is shown: the sum of the others is never printed as the table's."""
    # with a total row: every member is a part of the total
    df = frame("r05_age_with_total.csv")
    rep = run_file("r05_age_with_total.csv")
    g = dim(rep, "Age group")
    assert g["role"] == "partition" and g["total"] == "Total, all ages" and g["parts"] == 3, g
    assert not g.get("alternatives"), g
    pri, lat = window_sums(df, df["Age group"] == "Total, all ages", 1000.0)
    check_truth(rep, pri, lat, "age with a total")
    # without a total row: never the sum of the members that were not dropped
    for name, col, dropped in (("r05_education_less_than.csv", "Education", "Less than high school"),
                               ("r05_age_no_total.csv", "Age group", "Less than 15 years"),
                               ("r05_disability.csv", "Disability status", "Persons without disabilities"),
                               ("r05_language_other_than.csv", "Language", "Languages other than English or French")):
        df = frame(name)
        # wave 5f (C): "Disability status" names a sensitive category, so it is withheld by default (a plain refusal naming the column); the
        # visitor's Keep reads it, and the case is the same as in wave 5e
        keep = {"disability_status": "keep"} if col == "Disability status" else None
        if keep:
            r0 = run_file(name)
            assert refused(r0) and "Disability status" in st(r0)["reason"], (headline(r0), st(r0).get("reason"))
        rep = run_file(name, keep)
        every = window_sums(df, df[col] != "", 1000.0)
        rest = window_sums(df, df[col] != dropped, 1000.0)
        f = figs(rep)
        assert f is None or not close(f[1], rest[1]), "%s: the member %r was dropped from the headline" % (name, dropped)
        got = safe_or_true(rep, every[0], every[1], name)
        d = dim(rep, col)
        assert dropped not in (d.get("alternatives") or {}), (name, d)


def test_r06_a_french_statcan_table_is_read_like_its_english_twin():
    """Finding 6. VECTEUR was the dimension (v100000 the headline member), no unit, "milliers" not applied: the level 1,000x too
    small. French headers (GEO, UNITE DE MESURE, FACTEUR SCALAIRE=milliers, VECTEUR, COORDONNEE, VALEUR, STATUT) read like their
    English twin: Canada, dollars in thousands, $230.2M, +4.3%."""
    en = run_file("r06_english_control.csv")
    fr = run_file("r06_french_statcan.csv")
    assert figs(en) is not None and close(figs(en)[1], 230.2e6, 1e-3)
    assert figs(fr) is not None, headline(fr)
    assert close(figs(fr)[0], figs(en)[0]) and close(figs(fr)[1], figs(en)[1]), (figs(fr), figs(en))
    d = dim(fr, "G\u00c9O")
    assert d is not None and d["role"] == "partition" and d["total"] == "Canada", st(fr).get("dims")
    assert est(fr)["measure"]["uom"] == "Dollars" and est(fr)["measure"]["scale"] == "milliers", est(fr)["measure"]
    assert not any("v100000" in str(x.get("member")) for x in est(fr).get("slice") or [])


def test_r07_a_weekly_table_is_compared_over_whole_weeks_never_over_months_ending_in_a_partial_month():
    """Finding 7. A weekly table (Mondays) was read as monthly: "12 months to Feb 2023" held 51 Mondays against 53 before (-3.7%
    for a flat series). The trailing 52 weeks against the 52 before: 52,058 and 52,017 (+0.08%), said in weeks."""
    df = frame("r07_weekly_official.csv")
    rep = run_file("r07_weekly_official.csv")
    d = pd.to_datetime(df["REF_DATE"])
    sub = df[df["GEO"] == "Canada"].copy()
    sub["v"] = pd.to_numeric(sub["VALUE"])
    sub["d"] = pd.to_datetime(sub["REF_DATE"])
    sub = sub.sort_values("d")
    lat, pri = sub["v"].iloc[-52:].sum(), sub["v"].iloc[-104:-52].sum()
    assert (round(pri), round(lat)) == (52017, 52058), (pri, lat)
    f = figs(rep)
    assert f is not None, headline(rep)
    assert close(f[0], pri, 1e-6) and close(f[1], lat, 1e-6), (f, pri, lat)
    t = est(rep).get("text", "") + headline(rep)
    assert "52" in t and "week" in t, t
    assert "12 months" not in headline(rep) and "12-month" not in est(rep).get("text", ""), (headline(rep), est(rep).get("text"))


def test_r07b_a_daily_table_without_weekend_rows_is_compared_over_whole_weeks_the_same_weekday_against_the_same_weekday():
    """Finding 7, the neighbour the reviewer did not try. A daily table with no weekend rows (a shop that is closed, a market) was paired with the
    date 365 days earlier: that is the day before in the week, so a Tuesday met a Monday and every Monday was dropped, the latest window's Tuesday
    to Friday against the prior window's Monday to Thursday. With Monday at 0.6 and Friday at 1.4 of a normal day, a business that grew 5% a year
    read +22%. Now the span is whole weeks (364 days) and a date is set against the same weekday. A holiday's longer gap does not stop the table from
    being daily (Good Friday with Easter Monday and a weekend is a gap of 5)."""
    import datetime as dt
    rng = np.random.RandomState(11)
    days = [d for d in (dt.date(2021, 1, 4) + dt.timedelta(days=i) for i in range(800)) if d.weekday() < 5]
    wd = {0: 0.6, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.4}
    rows, truth = ["Date,Branch,Sales"], {}
    for d in days:
        tot = 0
        for b, base in (("North", 200.0), ("South", 120.0)):
            v = round(base * wd[d.weekday()] * 1.05 ** ((d - days[0]).days / 365.0) * (1 + 0.03 * rng.randn()), 2)
            rows.append("%s,%s,%.2f" % (d.isoformat(), b, v))
            tot += v
        rows.append("%s,Total,%.2f" % (d.isoformat(), tot))
        truth[d.isoformat()] = tot
    rep = run_bytes(("\n".join(rows) + "\n").encode())
    e = est(rep)
    assert e and not refused(rep), headline(rep)[:200]
    c = e["comparison"]
    assert (c["latest"][1], len(c["latest"][0])) == (days[-1].isoformat(), 10), c
    lat_days = (dt.date.fromisoformat(c["latest"][1]) - dt.date.fromisoformat(c["latest"][0])).days + 1
    assert lat_days % 7 == 0, ("a window of %d days is not whole weeks" % lat_days, c)
    sums = [sum(v for k, v in truth.items() if c[w][0] <= k <= c[w][1]) for w in ("prior", "latest")]
    f = figs(rep)
    assert f is not None and close(f[0], sums[0], 1e-6) and close(f[1], sums[1], 1e-6), (f, sums)
    pct = 100.0 * (sums[1] / sums[0] - 1.0)
    assert 3.0 < pct < 8.0, "a business that grew 5% a year reads %.1f%%" % pct
    # holiday gaps: a gap of 5 days now and then is a hole, not another rhythm
    ts = [d.isoformat() for d in days if d not in (dt.date(2021, 4, 5), dt.date(2022, 4, 18), dt.date(2022, 4, 15))]
    assert (NS._cadence(ts, tolerant=True) or {}).get("cadence") == "day"
    assert NS._cadence(ts) is None          # a business file with such a gap is not read as daily (it is read as before)


def test_r08_a_whole_country_row_is_the_headline_and_an_average_in_dollars_is_a_level():
    """Finding 8. Average weekly earnings (dollars) with Canada beside Ontario and Quebec: rule 6 took Ontario, the most
    dominant, and typed the measure a flow ("12-month totals" of a weekly average). Canada's own series is the headline and an
    average of dollars is averaged, never summed: the 12-month mean of Canada, 1,202 then 1,245 (+3.5%)."""
    df = frame("r08_average_earnings_canada.csv")
    rep = run_file("r08_average_earnings_canada.csv")
    sub = df[df["GEO"] == "Canada"].copy()
    sub["v"] = pd.to_numeric(sub["VALUE"])
    sub = sub.sort_values("REF_DATE")
    lat, pri = sub["v"].iloc[-12:].mean(), sub["v"].iloc[-24:-12].mean()
    f = figs(rep)
    assert f is not None, headline(rep)
    assert close(f[0], pri, 1e-6) and close(f[1], lat, 1e-6), (f, pri, lat)
    assert est(rep)["measure"]["aggregation"].startswith("mean"), est(rep)["measure"]
    assert "totals" not in est(rep)["text"].split(";")[-1], est(rep)["text"]
    g = dim(rep, "GEO")
    assert g.get("total") == "Canada", g


def test_r09_a_leftover_group_or_a_lone_country_is_never_read_as_the_aggregate_of_a_rate():
    """Finding 9. "All other provinces" (a rate with no national row) was the published aggregate of Ontario, Quebec and Alberta:
    it is the rest, not the whole. Canada among Mexico, Brazil and Chile was the aggregate of three other countries. Neither is:
    one member is shown, and the text says it is not a national figure."""
    for name, col, bad in (("r09_all_other_provinces_rate.csv", "GEO", "All other provinces"),
                           ("r09_countries_rate.csv", "Country", "Canada")):
        rep = run_file(name)
        d = dim(rep, col)
        assert d["role"] != "rate_aggregate", (name, d)
        assert one_member_shown(rep) or refused(rep), (name, est(rep).get("text"))
        assert "published totals" not in headline(rep), headline(rep)


def test_r10_a_not_cube_verdict_on_a_table_of_series_refuses_and_never_averages_the_rows():
    """Finding 10. A table with a publisher's columns that the layer calls not_cube (one reference period; more than the caps)
    fell to the old engine: "Average value over the period was 12,840" (Canada counted with its provinces) and "Average
    coordinate over the period". Every such verdict on a table that looks like a table of series is a plain refusal."""
    rep = run_file("r10_census_one_period.csv")
    assert refused(rep), headline(rep) + json.dumps((rep.get("story") or {}).get("what_happened"))[:200]
    assert st(rep).get("kind") == "error" and "reference period" in (st(rep).get("reason") or "").lower() + json.dumps(st(rep)).lower(), st(rep)
    data = fixture("r10_partition_for_caps.csv")
    keep = (NS.MAX_SERIES, NS.MAX_CELLS, NS.MAX_DIMS)
    try:
        for attr, val in (("MAX_SERIES", 3), ("MAX_CELLS", 100), ("MAX_DIMS", 0)):
            setattr(NS, attr, val)
            rep = run_bytes(data)
            setattr(NS, attr, dict(zip(("MAX_SERIES", "MAX_CELLS", "MAX_DIMS"), keep))[attr])
            assert refused(rep), "%s lowered: %s" % (attr, headline(rep)[:200])
    finally:
        NS.MAX_SERIES, NS.MAX_CELLS, NS.MAX_DIMS = keep


def test_r10b_a_memory_error_and_a_wall_guard_trip_refuse_a_table_of_series_and_leave_a_business_file_alone():
    """Finding 10, the other verdicts: a MemoryError and the wall guard both make `detect` say not_cube. On a table that looks like a table of
    series that is a plain refusal; on an ordinary business file (no publisher, no flag column) the file is read as it always was."""
    official = fixture("r10_partition_for_caps.csv")
    business = fixture("r01_business_small_counts.csv")
    real = NS._detect
    try:
        for exc in (MemoryError(), NS._WallGuard()):
            def boom(*a, **k):
                raise exc
            NS._detect = boom
            rep = run_bytes(official)
            assert refused(rep), "%s: %s" % (type(exc).__name__, headline(rep)[:200])
            assert st(rep).get("kind") == "error", st(rep)
            rep2 = run_bytes(business)
            assert not refused(rep2) and rep2.get("ok"), "%s: a business file was refused: %s" % (type(exc).__name__, headline(rep2)[:200])
    finally:
        NS._detect = real


def test_r11_an_exception_in_the_profile_pass_never_lets_a_plan_run_read_a_table_of_series_the_old_way():
    """Finding 11. With a plan, the profile pass (cached or run) raising outside `detect` returned {ok: False} and the plan was
    applied to the whole file: "Average value ... 9,301" (the Total and its regions averaged). Now the same refusal as a run
    without a plan."""
    data = fixture("r10_partition_for_caps.csv")
    ok = run_bytes(data, plan=PLAN)
    assert figs(ok) is not None and not refused(ok), headline(ok)
    real = NB._profile_facts

    def boom(*a, **k):
        raise RuntimeError("simulated failure in the profile pass")
    NB._profile_facts = boom
    try:
        bad = run_bytes(data, plan=PLAN)
    finally:
        NB._profile_facts = real
    assert refused(bad), headline(bad)
    assert st(bad).get("kind") == "error" and (st(bad).get("error") or {}).get("stage") == "profile", st(bad)


def test_r12_a_dimension_of_more_than_400_members_is_read_not_refused_with_a_false_reason():
    """Finding 12. 451 members (a Total and 450 industries that add up): the old engine called the column "metadata", saw dates
    repeat and refused with "no column the engine may read tells the repeats apart". A series key has no cap; only the search
    for relations is bounded: the Total is checked against the rest (linear) and the headline is its own series."""
    data = fixture("r12_451_members.csv")
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    rep = run_bytes(data)
    col = [c for c in df.columns if c not in ("REF_DATE", "GEO", "DGUID", "UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID", "VECTOR",
                                             "COORDINATE", "VALUE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS")][0]
    tot = [m for m in df[col].unique() if m.lower().startswith("total")][0]
    pri, lat = window_sums(df, df[col] == tot, 1000.0)
    assert not (st(rep).get("kind") == "cube_incomplete"), st(rep).get("reason")
    check_truth(rep, pri, lat, "the total of 450 industries")
    d = dim(rep, col)
    assert d["role"] in ("partition", "hierarchy") and d["total"] == tot, d


def test_r13_the_answer_never_depends_on_the_wall_clock():
    """Finding 13. The combined-member search ran under a wall-clock budget; out of time it read "nothing found" as "no combined
    member" and added Inland provinces to its parts ($250.9M instead of $181.9M). Every search is bounded by counts: the same
    answer with every budget at zero, and a wall-clock guard may only refuse the whole table."""
    df = frame("r13_time_budget.csv")
    data = fixture("r13_time_budget.csv")
    base = run_bytes(data)
    pri, lat = window_sums(df, (df["GEO"] != "Inland provinces"), 1000.0)
    check_truth(base, pri, lat, "three real regions")
    saved = {k: getattr(NS, k) for k in dir(NS) if k.isupper() and ("BUDGET" in k or "GUARD" in k) and isinstance(getattr(NS, k), float)}
    try:
        for k in saved:
            setattr(NS, k, 0.0)
        starved = run_bytes(data)
    finally:
        for k, v in saved.items():
            setattr(NS, k, v)
    f0, f1 = figs(base), figs(starved)
    assert (f1 is not None and close(f1[0], f0[0]) and close(f1[1], f0[1])) or refused(starved), \
        "a zero time budget changed the figure: %r vs %r; headline: %s" % (f0, f1, headline(starved))


def test_r14_a_leftover_member_search_is_bounded_by_counts_and_takes_well_under_a_second():
    """Finding 14. 18 "of which" components in a 399-member tree took `_alternatives` O(left x members^2): 7.6 s native for a
    detection that takes 0.07 s without them. A sorted lookup over a few cells, bounded by a count."""
    t0 = time.perf_counter()
    S = NS.detect(_reading(fixture("r14_399_members_18_components.csv")), ())
    took = time.perf_counter() - t0
    assert S["kind"] == "cube", (S["kind"], S["reason"])
    assert took < 3.0, "detection took %.1f s" % took
    d = next(x for x in S["dims"] if x["role"] in ("hierarchy", "partition"))
    assert len(d["components"]) >= 15, len(d["components"])


def _reading(data: bytes):
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    land = {h: NB._engine_slug(h) for h in df.columns}
    texts = df.rename(columns=land)
    values = texts.copy().astype(object)
    for c in values.columns:
        t = texts[c].str.strip()
        f = t[t != ""]
        if not len(f):
            continue
        num = pd.to_numeric(f.str.replace(",", "", regex=False), errors="coerce")
        if num.notna().mean() >= 0.95:
            values[c] = pd.to_numeric(t.str.replace(",", "", regex=False), errors="coerce")
            continue
        if f.str.match(r"^\d{4}-\d{2}(?:-\d{2})?$").mean() >= 0.95:
            values[c] = pd.to_datetime(t.where(t != ""), errors="coerce")
    return NB._Reading(values, texts, np.ones(len(df), bool), land, {})


def test_r15_a_total_that_no_cell_could_check_is_never_said_to_add_up():
    """Finding 15. Every month one of four regions is suppressed (no complete cell): the Total's own series is the headline, and
    the old engine printed verdict "adds_up" with 0 complete cells, "sum-checked" and "Each total was checked against its parts"
    (also when the Total is 20% above the parts). None of those words at an evidence level not reached."""
    for name in ("r15_zero_complete_cells.csv", "r15_total_20pct_high.csv"):
        rep = run_file(name)
        assert figs(rep) is not None, name
        chk = est(rep)["sum_checks"][0]
        assert chk["complete_cells"] in (0, None) and chk["verdict"] != "adds_up", chk
        said = json.dumps([rep.get("limitations"), est(rep).get("excluded"), headline(rep), est(rep).get("text")])
        for phrase in ("sum-checked", "Each total was checked against its parts", "in the published totals", "adds_up"):
            assert phrase not in said, "%s: %r printed with 0 complete cells" % (name, phrase)


def test_r15b_a_member_that_says_total_and_is_decidedly_not_the_sum_of_the_others_says_so():
    """Finding 15, the neighbour. A member called Total that is half of the sum of the three provinces beside it: one member is shown (the sums
    contradict it as the total of the others, so nothing is added and nothing is called published), and the words say it was CHECKED and does
    not add up, never "not checked against the other members"."""
    import make_cubes as MC
    rng = np.random.RandomState(3)
    t = np.arange(48)
    seas = np.array([0.9, 0.85, 1.0, 1.0, 1.05, 1.05, 1.0, 1.0, 1.0, 1.05, 1.1, 1.25])
    ser = {g: np.round(lv * 1.003 ** t * seas[t % 12] * (1 + 0.02 * rng.randn(48))) for g, lv in (("Ontario", 8000), ("Quebec", 5000), ("Alberta", 3000))}
    tot = np.round(sum(ser.values()) * 0.5)
    months = ["%d-%02d" % (2020 + i // 12, i % 12 + 1) for i in range(48)]
    rec = []
    for i, m in enumerate(months):
        rec.append((m, "Total", ("Retail sales",), tot[i], "A"))
        rec.extend((m, g, ("Retail sales",), v[i], "A") for g, v in ser.items())
    rep = run_bytes(MC._official(["Sales"], rec))
    assert not refused(rep) and figs(rep) is not None, headline(rep)[:200]
    f = figs(rep)
    assert close(f[0], tot[-24:-12].sum() * 1000, 1e-6) and close(f[1], tot[-12:].sum() * 1000, 1e-6), (f, tot[-24:-12].sum(), tot[-12:].sum())
    t_ = headline(rep) + " " + est(rep).get("text", "")
    assert "do not add up to it" in t_, t_
    assert "not checked against the other members" not in t_ and "published totals" not in t_, t_
    d = dim(rep, "GEO")
    assert d["role"] == "single" and d["single_by"] == "name" and d.get("named_contradicted") is True, d


def test_r16_a_levels_window_is_matched_too_two_months_never_stand_for_twelve():
    """Finding 16. A rate whose headline series starts in Nov 2021: the prior "12-month average" was two months and the estimand
    said complete: true, +0.683 points. A level needs half a window in both windows, on the months both have, else no figure."""
    rep = run_file("r16_short_prior_window.csv")
    e = est(rep)
    f = e.get("figures") or {}
    assert (f.get("change") or {}).get("value") is None or e.get("complete") is False, (f, e.get("complete"))
    assert e.get("complete") is not True, e.get("complete")
    assert "12-month averages Jan 2022" not in e.get("text", "") or (f.get("change") or {}).get("value") is None, e.get("text")


def test_r17_the_documented_value_column_of_an_official_table_is_released_with_a_consent_line():
    """Finding 17. A VALUE column of nine-digit integers (annual dollars in thousands) was withheld by default as national ID
    numbers and the table refused. The publisher's documented value column that parses as numbers is read, with the consent-card
    line "Read as the table's measure, not personal data: VALUE"; the visitor can still withhold it (the old refusal)."""
    df = frame("r17_value_nine_digits.csv")
    rep = run_file("r17_value_nine_digits.csv")
    assert figs(rep) is not None, headline(rep)
    rel = (rep.get("privacy") or {}).get("released") or []
    assert any(x.get("text") == "Read as the table's measure, not personal data: VALUE" for x in rel), rel
    assert not any(f.get("column") == "value" for f in (rep.get("privacy") or {}).get("flagged") or []), rep["privacy"]
    kept = run_file("r17_value_nine_digits.csv", {"VALUE": "keep"})
    assert figs(kept) == figs(rep), (figs(kept), figs(rep))
    held = run_file("r17_value_nine_digits.csv", {"VALUE": "withhold"})
    assert refused(held), headline(held)


def test_r18_the_measure_is_named_by_what_is_measured_not_by_the_first_constant_column():
    """Finding 18. "Data type = Seasonally adjusted" came before "Labour force characteristics = Employed persons", so the
    headline read "Seasonally adjusted, Canada, ...". It reads "Employed persons, Canada, ..."."""
    rep = run_file("r18_label_hint.csv")
    assert headline(rep).startswith("Employed persons, Canada"), headline(rep)
    assert "Seasonally adjusted," not in headline(rep)


def test_r19_the_structure_block_never_echoes_a_constant_text_column_the_scan_did_not_know():
    """Finding 19. structure.metadata[].value copied "Okonkwo Adebayo-Smith" (a constant "Analyst" column) into the report."""
    rep = run_file("r19_metadata_echo.csv")
    blob = json.dumps(rep.get("structure"))
    assert "Okonkwo" not in blob and "Adebayo" not in blob, [m for m in st(rep)["metadata"] if m.get("column") == "Analyst"]
    units = [m for m in st(rep)["metadata"] if m.get("column") in ("UOM", "SCALAR_FACTOR", "DECIMALS")]
    assert units and all("value" in m for m in units), units


def test_r20_a_plain_business_file_is_never_taken_for_a_table_of_series_and_a_french_one_is_known():
    """Finding 20. An inventory (Unit, Status, Action), a subscription list (Status, Units, Frequency) and a grade book (Status
    A to D) were "series tables" and would be refused whenever the layer failed; a French official table was not recognised."""
    for name in ("r20_business_inventory.csv", "r20_business_subscriptions.csv", "r20_business_gradebook.csv"):
        assert NB.looks_like_series_table(fixture(name)) is None, (name, NB.looks_like_series_table(fixture(name)))
    # nor is the structure layer's own reading of them "official": three columns that any business file has (Unit, Status, Action) are no
    # publisher's; the file was read one SKU at a time ("one member shown") before, and is read as it always was now
    for name in ("r20_business_inventory.csv", "r20_business_subscriptions.csv", "r20_business_gradebook.csv"):
        rep = run_file(name)
        assert not est(rep) and not st(rep).get("official") and (rep.get("estimand") is None), (name, st(rep).get("kind"), headline(rep)[:100])
        assert not refused(rep), name
    assert NB.looks_like_series_table(fixture("r20_official_french_headers.csv")) is not None
    assert NB.looks_like_series_table(fixture("r06_french_statcan.csv")) is not None


def test_r21_a_sum_the_engine_built_is_never_called_a_published_total():
    """Finding 21. "the sum of 4 regions ... in the published totals" for a table with no total row. The headline says what it
    is: built from the table's parts."""
    rep = run_file("r21_built_from_parts.csv")
    assert figs(rep) is not None, headline(rep)
    assert "in the published totals" not in headline(rep), headline(rep)
    assert "built from" in headline(rep) or "no total row" in headline(rep), headline(rep)


def test_r22_seed_3160_an_industry_total_that_adds_up_to_its_leaves_is_never_summed_with_them():
    """Seed 3160 (fresh range 3000, wave 5d: +6.9% where the truth was +4.3%). A flow count, 2 regions with no total row, a
    3-level industry tree with NO codes, the total named "Full range", an "excluding" alternative total, 12% blank cells. The
    industry dimension was read as parts and 16 members (15 leaves and the total) were added. The leaves add up to the total
    within 0.0065% on 39 complete region-months: the total is real and checkable."""
    rep = run_file("r22_seed3160_industry_parts.csv")
    truth = json.load(open(os.path.join(REGRESS, "r22_seed3160_truth.json")))
    lat = truth["acceptable"]["latent"]["latest"]
    d = dim(rep, "North American Industry Classification System (NAICS)")
    assert d["role"] in ("partition", "hierarchy") and d["total"] == "Full range", d
    f = figs(rep)
    assert f is None or f[1] <= 1.02 * lat, "the figure counts something twice: %r vs the latent %r" % (f, lat)
    assert refused(rep) or one_member_shown(rep) or est(rep).get("complete") is False or (
        f is not None and abs(f[2] - truth["acceptable"]["latent"]["change_pct"]) < 0.6), (f, headline(rep))


# ----------------------------------------------------------------------------- the reviewer's suspected items, settled by a test each
def test_r23_a_dimension_of_measures_is_never_an_adjusted_pair_a_rate_and_its_standard_error_move_together():
    """Found by the fuzz (seeds 6, 103, 115, 164, 195, 212 of the development range), not by the reviewer: wave 5e's shape test for copies read
    "Unemployment rate" and "Standard error of the unemployment rate" (two quantities in two units that move together under a steady ratio)
    as a seasonally adjusted pair, over-wrote the dimension's role (measure) and crashed the structure layer ("zero-size array"): the table was
    refused, and it had been read before. A dimension that names what is measured is never an adjustment. The headline is Employment, Total."""
    df = frame("r23_measure_dimension_rate_and_standard_error.csv.gz")
    rep = run_file("r23_measure_dimension_rate_and_standard_error.csv.gz")
    assert not refused(rep), headline(rep)[:300]
    d = dim(rep, "Principal statistics")
    assert d["role"] == "measure", d
    sub = df[(df["GEO"] == "Total") & (df["Principal statistics"] == "Employment")].copy()
    sub["v"] = pd.to_numeric(sub["VALUE"])
    sub = sub.sort_values("REF_DATE")
    lat, pri = sub["v"].iloc[-4:].mean(), sub["v"].iloc[-8:-4].mean()          # persons employed: a stock, its window figure is the mean of 4 quarters
    f = figs(rep)
    assert f is not None and close(f[0], pri, 1e-6) and close(f[1], lat, 1e-6), (f, pri, lat)
    assert "Employment" in headline(rep) and "published totals" in headline(rep), headline(rep)


# ----------------------------------------------------------------------------- the second independent review (wave 5e): R01 to R09
def _csv_rows(head, rows):
    import csv as _csv
    b = io.StringIO()
    w = _csv.writer(b, lineterminator="\n")
    w.writerow(head)
    w.writerows(rows)
    return b.getvalue().encode()


def test_r24_an_ordinary_ledger_with_a_status_column_and_a_second_number_is_read_never_refused_as_a_table_of_series():
    """Review R01 and R02. An orders ledger (Date, Region, Product, Status Paid/Open/Void/Hold with no Value on a Void order, Value, Qty) and a
    help-desk export (Status p/c/r, Hours, Cost) were refused as "a table of series with totals": a short code whose rows have no value, or a
    status named like a publisher's flag, passed the long-format test, and the second number column made the layer say not_cube. A file with
    two number columns and no publisher's mark is a business export, read as before. The negative: the same shape WITH a publisher's columns
    (a Statistics Canada table that also carries a second varying number) is still refused."""
    import datetime as dt
    rng = np.random.RandomState(1)
    rows = []
    for i in range(36):
        d = dt.date(2021 + i // 12, i % 12 + 1, 1)
        for r in ("North", "South", "East"):
            for p in ("Gadgets", "Widgets"):
                st_ = ("Paid", "Open", "Void", "Hold")[int(rng.randint(4))]
                rows.append([d.isoformat(), r, p, st_, "" if st_ == "Void" else "%.2f" % rng.uniform(10, 90), int(rng.randint(1, 20))])
    ledger = _csv_rows(["Date", "Region", "Product", "Status", "Value", "Qty"], rows)
    rep = run_bytes(ledger)
    assert rep.get("ok") and not refused(rep) and st(rep).get("kind") != "error", headline(rep)[:300]
    rows = []
    for i in range(36):
        d = dt.date(2021 + i // 12, i % 12 + 1, 1)
        for team in ("Tier1", "Tier2", "Tier3"):
            for cat in ("Billing", "Login", "Bug"):
                hrs = round(float(rng.uniform(1, 9)), 1)
                rows.append([d.isoformat(), team, cat, ("p", "c", "r")[int(rng.randint(3))], hrs, round(hrs * 40, 2)])
    rep2 = run_bytes(_csv_rows(["Date", "Team", "Category", "Status", "Hours", "Cost"], rows))
    assert rep2.get("ok") and not refused(rep2) and st(rep2).get("kind") != "error", headline(rep2)[:300]
    # negative: a publisher's table with a second varying number column is never read the old way: it is refused, or (wave 5f: a number the date
    # and the other dimensions already tell apart is a second measure, not a dimension) read by its structure with the VALUE column as the measure
    df = frame("r10_partition_for_caps.csv")
    df["EXTRA"] = (pd.to_numeric(df["VALUE"], errors="coerce").fillna(0) * 0.37 + np.arange(len(df)) % 7).round(2).astype(str)
    rep3 = run_bytes(df.to_csv(index=False).encode())
    if not refused(rep3):
        pri, lat = window_sums(df.drop(columns="EXTRA"), df["GEO"] == "Total", 1000.0)
        check_truth(rep3, pri, lat, "the VALUE column is the measure, the extra number is not")


def test_r25_a_scale_word_in_a_column_of_any_header_is_applied_and_an_ambiguous_word_is_not():
    """Review R08. A constant column whose only value is a scale word (Thousands, Millions, Milliers, Miles, Tausend) is the table's scale
    whatever its header (Scale, Factor, Facteur, Magnitude): the figure printed $148.9K where the file said thousands of dollars ($148.9M).
    The negative: a constant column that holds a short or ambiguous word ("Mill", "Mil") in a table with no publisher's mark is left alone."""
    import make_cubes as MC
    rng = np.random.RandomState(12)
    regs = ["Bayern", "Hessen", "Sachsen", "Saarland"]
    v = {r: np.round(MC._series(rng, lv)) for r, lv in zip(regs, (5000, 3000, 2000, 800))}
    tot = sum(v.values())

    def table(header, word):
        rows = []
        for i, mo in enumerate(MC.MONTHS):
            rows.append([mo, "Total", "Sales", "Dollars", word, "%d" % tot[i]])
            rows.extend([mo, r, "Sales", "Dollars", word, "%d" % v[r][i]] for r in regs)
        return MC._csv(["Date", "Region", "Measure", "Unit", header, "Value"], rows)
    for header, word, k in (("Scale", "Thousands", 1e3), ("Factor", "Thousands", 1e3), ("Magnitude", "Millions", 1e6), ("Facteur", "Milliers", 1e3),
                            ("Factor", "Miles", 1e3), ("Faktor", "Tausend", 1e3), ("Unit multiplier", "Thousands", 1e3)):
        rep = run_bytes(table(header, word))
        f = figs(rep)
        assert f is not None and close(f[1], tot[-12:].sum() * k, 1e-6), (header, word, f, tot[-12:].sum() * k)
    rep = run_bytes(table("Size", "Mill"))
    f = figs(rep)
    assert f is None or close(f[1], tot[-12:].sum(), 1e-6), ("an ambiguous word was applied", f)


def test_r26_a_scale_the_unit_of_measure_carries_is_applied_once_and_the_slice_measure_is_never_an_id():
    """Review R03. A table with no scale column whose UNIT says the scale ("USD millions", "Millions of dollars", "Dollars (millions)", "$ billions",
    "CAD thousands", "Millions de dollars", "Thousands of persons") printed the raw numbers ("$308.2K" for $308.2B). The scale is applied to the
    figures and dropped from the printed unit (never said twice), also where a SCALAR_FACTOR column says "units". And the figure, now eleven digits,
    is not taken for a national ID number and withheld by the engine's scan (the slice the structure layer writes is its own column): the
    report still has its facts. The negative: SCALAR_FACTOR "millions" with UOM "Dollars" is applied once."""
    import make_cubes as MC
    rng = np.random.RandomState(17)
    regions = ["England", "Wales", "Scotland"]
    vals = {r: MC._series(rng, lv, growth=0.02) for r, lv in zip(regions, (9000, 900, 1100))}
    tot = np.array([sum(vals[r][i] for r in regions) for i in range(len(MC.MONTHS))])

    def table(unit):
        rows = []
        for i, mo in enumerate(MC.MONTHS):
            rows.append([mo, "Great Britain", "%d" % tot[i], unit])
            rows.extend([mo, r, "%d" % vals[r][i], unit] for r in regions)
        return MC._csv(["date", "region", "value", "unit"], rows)
    for unit, k in (("USD millions", 1e6), ("Millions of dollars", 1e6), ("Dollars (millions)", 1e6), ("$ billions", 1e9),
                    ("CAD thousands", 1e3), ("Millions de dollars", 1e6)):
        rep = run_bytes(table(unit))
        f = figs(rep)
        assert f is not None and close(f[1], tot[-12:].sum() * k, 1e-6), (unit, f, tot[-12:].sum() * k)
        assert len(rep.get("findings") or []) > 0 and "no gated facts" not in headline(rep), (unit, headline(rep)[:200])
        assert not re.search(r"(?i)million|billion|thousand", str((st(rep).get("measure") or {}).get("uom") or "")), st(rep).get("measure")
    # a StatCan-shaped table: UOM says the scale, SCALAR_FACTOR says units
    rec = []
    for i, mo in enumerate(MC.MONTHS):
        rec.append((mo, "Total", ("Sales",), tot[i] / 10.0, "A"))
        for r in regions:
            rec.append((mo, r, ("Sales",), vals[r][i] / 10.0, "A"))
    for unit, k in (("Millions of dollars", 1e6), ("Thousands of persons", 1e3)):
        rep = run_bytes(MC._official(["Sales"], rec, uom=unit, scalar="units"))
        f = figs(rep)
        want = (tot[-12:] / 10.0).sum() * k
        assert f is not None and close(f[1], want, 1e-4), (unit, f, want)
    rep = run_bytes(MC._official(["Sales"], rec, uom="Dollars", scalar="millions"))
    f = figs(rep)
    want = (tot[-12:] / 10.0).sum() * 1e6
    assert f is not None and close(f[1], want, 1e-4), ("SCALAR_FACTOR millions with UOM Dollars is applied once", f, want)


def test_r27_a_balance_in_a_currency_is_a_level_not_a_flow_and_a_price_per_unit_is_never_summed():
    """Review R05. A measure in a currency was a flow unless the labels held one of seven words (inventory, outstanding, balance, holding, asset,
    debt, stock): total deposits, loans, liabilities, net worth, money supply M2, savings, market capitalization, reserves, equity were printed as
    12-month totals (12 times the level), and an exchange rate "Dollars per unit of foreign currency" as a sum. A balance word makes the
    measure a stock (a mean of the months); with a flow word beside it ("new loans issued") it is ambiguous (averaged), never summed; the unit
    that says per something is a level. The negative: retail sales in dollars is still a flow."""
    for label in ("Total deposits", "Loans", "Liabilities", "Net worth", "Money supply M2", "Savings", "Market capitalization", "Reserves", "Equity"):
        c = NS._classify("Dollars", label + " VALUE", "VALUE", says=label)
        assert c["type"] == "stock" and c["aggregation"] == "mean over months", (label, c)
    c = NS._classify("Dollars", "Net sales of loans VALUE", "VALUE", says="Net sales of loans")
    assert c["aggregation"] == "mean over months" and c["type"] != "flow", c
    c = NS._classify("Dollars per unit of foreign currency", "VALUE", "VALUE")
    assert c["aggregation"] == "mean over months", c
    for label in ("Retail sales", "Total revenue", "Wages and salaries"):
        assert NS._classify("Dollars", label + " VALUE", "VALUE", says=label)["type"] == "flow", label


def test_r28_a_sum_of_reported_parts_with_suppressed_cells_says_it_is_incomplete_in_the_headline():
    """Review R07. Five regions, no total row, 7 of 60 region-months suppressed all in the earlier window: the headline read "the sum of 5
    regions, the 12 matched months of 12 to Dec 2021: +16.5%" (the true change is +3.8%) and only the estimand's text, further down, said the sum
    of the reported parts is incomplete. The headline now says it, and does not call twelve months of twelve "matched"."""
    import make_cubes as MC
    n = 36
    t = np.arange(n)
    rng = np.random.RandomState(2)
    regs = {"Alder": 5000, "Birch": 7000, "Cedar": 3000, "Dune": 6000, "Elm": 4000}
    vals = {r: np.round(lv * 1.0032 ** t * MC.SEASON[t % 12] * (1 + 0.01 * rng.standard_normal(n))) for r, lv in regs.items()}
    hide = {("Alder", i) for i in range(12, 19)}
    rec = [(mo, r, ("Retail sales",), None if (r, i) in hide else vals[r][i], "x" if (r, i) in hide else "A")
           for i, mo in enumerate(MC.MONTHS[:n]) for r in regs]
    rep = run_bytes(MC._official(["Sales"], rec))
    h = headline(rep)
    assert "incomplete" in h and "suppressed" in h, h
    assert "matched months of 12" not in h, h
    # the negative: no suppression, no such words
    rec = [(mo, r, ("Retail sales",), vals[r][i], "A") for i, mo in enumerate(MC.MONTHS[:n]) for r in regs]
    h2 = headline(run_bytes(MC._official(["Sales"], rec)))
    assert "incomplete" not in h2 and "matched" not in h2, h2


def test_r29_a_named_whole_and_a_named_total_the_others_do_not_add_up_to_say_so():
    """Review R04. "Canada" beside four provinces that add up to 85% of it was described as "could not be checked against the other members"
    (the check ran and failed), while the same table with the member named "Total" read "in the published totals". Both now say what the
    check found: named as the total, and the other members do not add up to it (for a total that bounds them: they bound it and do not add up)."""
    import make_cubes as MC
    rng = np.random.RandomState(1)
    prov = {"Ontario": 9000, "Quebec": 6000, "Alberta": 4000, "British Columbia": 5000}
    vals = {r: MC._series(rng, lv) for r, lv in prov.items()}
    whole = np.round(sum(vals.values()) / 0.85)
    for name in ("Canada", "Total"):
        rec = []
        for i, mo in enumerate(MC.MONTHS):
            rec.append((mo, name, ("Retail sales",), whole[i], "A"))
            rec.extend((mo, r, ("Retail sales",), vals[r][i], "A") for r in prov)
        h = headline(run_bytes(MC._official(["Sales"], rec)))
        assert "do not add up to it" in h, (name, h)
        assert "not checked" not in h and "could not be checked" not in h and "in the published totals" not in h, (name, h)


def test_r30_a_short_table_does_not_say_no_change_is_tested_beside_a_percent_change():
    """Review R09. A monthly table of 18 months: the story headline said "too short to compare the latest 12 months with the 12 before, so no
    change is tested" while the estimand of the same report printed +3.3% on the 6 months both windows hold. The headline now says both: the
    change on the matched periods, and that the table is too short to test it."""
    import make_cubes as MC
    n = 18
    t = np.arange(n)
    months = ["%04d-%02d" % (2021 + i // 12, i % 12 + 1) for i in range(n)]
    rng = np.random.RandomState(8)
    regs = {"Alder": 5000, "Birch": 7000, "Cedar": 3000}
    v = {r: np.round(lv * 1.003 ** t * MC.SEASON[t % 12] * (1 + 0.02 * rng.standard_normal(n))) for r, lv in regs.items()}
    tot = sum(v.values())
    rec = []
    for i, mo in enumerate(months):
        rec.append((mo, "Total", ("Retail sales",), tot[i], "A"))
        rec.extend((mo, r, ("Retail sales",), v[r][i], "A") for r in regs)
    rep = run_bytes(MC._official(["Sales"], rec))
    h = headline(rep)
    assert "no change is tested" not in h, h
    assert "too short" in h and "+3.3%" in h.replace("\u2212", "-"), h
    assert abs(est(rep)["figures"]["change_pct"]["value"] - 100 * (tot[12:18].sum() / tot[0:6].sum() - 1)) < 0.05


def test_s01_sensitive_headers_are_never_released_as_categories_whatever_the_language():
    """Suspected: SENSITIVE_HEADER lacked visible minority, Indigenous, cause of death, ICD, HIV, marital, and their French, German and
    Spanish cognates. A category with such a header is sensitive even when it is categorical: never released, so withheld by default."""
    sensitive = ["Visible minority status", "Indigenous identity", "Aboriginal identity", "Cause of death", "ICD-10 code", "HIV status",
                 "Marital status", "Minorit\u00e9 visible", "Identit\u00e9 autochtone", "\u00c9tat matrimonial", "Cause de d\u00e9c\u00e8s",
                 "Familienstand", "Estado civil", "Causa de muerte", "Discapacidad", "Behinderung", "Religi\u00f3n", "First Nations", "Inuit"]
    for h in sensitive:
        assert NB.SENSITIVE_HEADER.search(h), "%r is not a sensitive header" % h
    for h in ["Industry", "Region", "Product category", "Genre", "Brand", "Colour", "Store", "Sector", "Age group", "Education", "Language",
              "Braids", "Maids", "Raids"]:
        assert not NB.SENSITIVE_HEADER.search(h), "%r is read as sensitive" % h


def test_s02_the_long_id_rule_flags_an_id_repeated_and_a_repeated_large_measure_and_never_a_measure_that_varies():
    """Suspected: ids of 9 or more digits repeated 3.3 times or more, and a repeated large measure, are flagged by the long-ID rule. Settled:
    both are flagged (a category called by 9 digits is an ID; a measure that repeats on 70% of its rows reads as one: the visitor can Keep it and
    the default is the private one); a column of measures is never one (a different value on most rows), and a publisher's documented value
    column is released instead (P10)."""
    import numpy as np
    rng = np.random.RandomState(5)
    n = 400
    ids = pd.Series(["%09d" % (100000000 + 7919 * (i % 100)) for i in range(n)])                  # 100 ids, each on 4 rows (25% distinct)
    assert NB._personal_kind("Customer", ids) == "id_number"
    ids33 = pd.Series(["%09d" % (100000000 + 7919 * (i % 120)) for i in range(n)])                # 120 ids, each on 3.3 rows (30% distinct)
    assert NB._personal_kind("Ref", ids33) == "id_number"
    ids29 = pd.Series(["%09d" % (100000000 + 7919 * (i % 140)) for i in range(n)])                # 140 ids (35% distinct): not an ID by this rule
    assert NB._personal_kind("Ref", ids29) is None
    repeated_measure = pd.Series(["%d" % (500000000 + 1000000 * (i % 50)) for i in range(n)])     # a balance that repeats
    assert NB._personal_kind("Balance", repeated_measure) == "id_number"
    varying = pd.Series(["%d" % (500000000 + int(x)) for x in rng.randint(0, 10 ** 8, n)])        # a different value on most rows
    assert NB._personal_kind("Balance", varying) is None


def test_s03_a_copy_in_a_five_member_dimension_is_never_added_to_its_original():
    """Suspected: the twin guard looked at dimensions of at most 4 members, so a copy among five regions went through. Now the pairs are
    compared by shape whatever the number of members (the 40 largest)."""
    import make_cubes as MC
    rep = run_bytes(MC.five_regions(copy=True))
    g = dim(rep, "GEO")
    assert g["role"] != "parts", g
    assert one_member_shown(rep) or refused(rep), est(rep).get("text")
    # negative: five regions with their own noise are five parts, added
    rep2 = run_bytes(MC.five_regions(copy=False))
    g2 = dim(rep2, "GEO")
    assert g2["role"] == "parts" and g2["parts"] == 5, g2


def test_s04_a_25_sector_tree_with_no_codes_keeps_its_hierarchy_beyond_the_old_22_candidate_cap():
    """Suspected: a 25-sector uncoded tree beyond the 22-candidate cap lost its breakdown. The subset search reads 32 candidates (2^16
    subsets a half); the total is found and the 25 sectors are its breakdown."""
    import make_cubes as MC
    S = NS.detect(_reading(MC.tree_25()), ())
    g = next(d for d in S["dims"] if d["column"] == "Sector")
    assert g["role"] in ("partition", "hierarchy") and g["total"] == "All sectors" and len(g["parts"]) == 25, g
    assert S["breakdowns"] and len(S["breakdowns"][0]["parts"]) == 25


def test_s05_the_unnamed_aggregate_decision_does_not_sit_on_a_borderline():
    """Suspected: borderline fits in `_unnamed_aggregate` may depend on BLAS (Pyodide's differs from the Mac's). The accepted and the refused
    panel are both far from the thresholds (a true aggregate fits to 1e-9 of a unit, a non-aggregate to many units), and a perturbation of
    1e-9 relative on every value changes nothing: the decision is not a coin the BLAS tosses. (The packed engine in Pyodide agrees with
    native on both panels: tools/check_pyodide_cube.mjs.)"""
    import make_cubes as MC
    for agg in (True, False):
        data = MC.rate_panel(39, 79, aggregate=agg)
        S0 = NS.detect(_reading(data), ())
        g0 = next(d for d in S0["dims"] if d["column"] == "GEO")
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
        v = pd.to_numeric(df["VALUE"], errors="coerce")
        rng = np.random.RandomState(9)
        df["VALUE"] = [("%.12g" % (x * (1.0 + 1e-9 * rng.randn()))) if x == x else "" for x in v]
        S1 = NS.detect(_reading(df.to_csv(index=False).encode()), ())
        g1 = next(d for d in S1["dims"] if d["column"] == "GEO")
        assert (g0["role"], g0.get("total")) == (g1["role"], g1.get("total")), (agg, g0["role"], g1["role"])
        if agg:
            assert g0["role"] == "rate_aggregate" and g0["sum_check"]["fit_rms"] < 0.2 * g0["sum_check"]["typical_member_fit_rms"], g0["sum_check"]


def test_the_cases_pyodide_runs_say_what_the_native_engine_says():
    """tools/check_pyodide_cube.mjs runs five of these files in Pyodide (Python 3.12) and compares them with tools/fixtures/structure/
    pyodide_cases.json: the expected values are written there, and this test keeps them equal to what the native engine says now (run
    `python tools/make_pyodide_cases.py --write` after an engine change that moves one, and read the diff)."""
    import make_pyodide_cases as MP
    now = MP.build()
    old = json.load(open(MP.OUT))
    assert set(now) == set(old), (sorted(now), sorted(old))
    for n in now:
        assert json.loads(json.dumps(now[n])) == old[n], (n, now[n], old[n])


# ----------------------------------------------------------------------------- wave 5f
# The third pass (branch w5f-harden). Fixtures: tools/fixtures/structure/regress/f*.csv (minimal tables, many from fuzz v2's shrink2) and
# make_cubes.py. Same rule as above: an EXPLICIT assertion of the right outcome, red on 09d3edc, and a negative that must not change.
def everything(rep, data=None):
    """All the text the engine hands anywhere: the saved report and what the report writer is given (and the planner's profile)."""
    out = json.dumps(rep, default=str) + json.dumps(NB.results_for_ai(rep), default=str)
    if data is not None:
        out += NB.profile_json(data, "t.csv")
    return out


def test_f01_a_sensitive_category_dimension_is_flagged_and_withheld_by_default_and_none_of_its_members_is_ever_printed():
    """Wave 5f, C (fuzz v2 seeds 18 23 30 45 118 120 135 147 148 198 218 237 262). A five-word category column under a header that names a
    sensitive category (Marital status, Indigenous identity, HIV status, ICD code, Cause of death, Visible minority ...) was no personal
    column to the scan, so it sat as an ordinary dimension of an official cube and one member was printed in the estimand
    ("Canada · All industries · Non-Indigenous identity"), with no consent line. Now the HEADER flags it, in any layout, withheld by
    default with a one-click Keep. An official cube that needs it is refused naming the column (never a value) and "choose Keep"; kept,
    its members may appear (the visitor decided)."""
    labels = ("Single", "Married", "Widowed", "Divorced", "Total, all marital statuses")
    data = MC.sensitive_dimension()
    rep = run_bytes(data)
    fl = {f["column"]: f for f in rep["privacy"]["flagged"]}
    assert set(fl) == {"marital_status"} and fl["marital_status"]["decision"] == "withhold" and "sensitive" in fl["marital_status"]["kind"], fl
    assert st(rep).get("kind") == "cube_incomplete" and refused(rep), (st(rep).get("kind"), headline(rep))
    assert "Marital status" in st(rep)["reason"] and "Keep" in st(rep)["reason"], st(rep)["reason"]
    blob = everything(rep, data)
    assert not [v for v in labels if v in blob], [v for v in labels if v in blob]
    # kept by the visitor: read, and the report says the column was flagged and kept
    rep_k = run_bytes(data, {"marital_status": "keep"})
    assert rep_k["privacy"]["flagged"][0]["decision"] == "keep" and est(rep_k) and "Total, all marital statuses" in est(rep_k)["text"], est(rep_k)
    # negative: the same labels under a neutral header are an ordinary dimension: nothing flagged, the cube is read
    rep_n = run_bytes(MC.sensitive_dimension(column="Group"))
    assert rep_n["privacy"]["flagged"] == [] and est(rep_n), rep_n["privacy"]


def _ledger(extra_column: str, labels, seed: int = 7, months: int = 30):
    """A plain business ledger: Date, Region, <extra_column>, Amount (a row per month, region and label)."""
    rng = np.random.RandomState(seed)
    rows = []
    for i in range(months):
        for r, lv in (("North", 900.0), ("South", 700.0), ("East", 500.0)):
            for k, lb in enumerate(labels):
                rows.append(["%04d-%02d-01" % (2021 + i // 12, i % 12 + 1), r, lb, "%d" % round(lv * (1 + 0.1 * k) * (1.0 + 0.01 * i) * (1 + 0.03 * rng.randn()))])
    return ("Date,Region,%s,Amount\n" % extra_column + "\n".join(",".join(x) for x in rows) + "\n").encode()


def test_f02_a_sensitive_column_of_a_plain_ledger_is_withheld_and_the_ledger_is_still_analysed_without_it():
    """Wave 5f, C (seeds 147 and 262: Date, Region, ICD code, Amount). The business layout has no cube to refuse: the column is flagged
    from its header, withheld (landed as opaque codes, scrubbed), and the analysis runs without it. No ICD code is in the report, in what
    the report writer is given or in the planner's profile."""
    codes = ["C34.9", "X44.9", "K74.6"]
    for header in ("ICD code", "Visible minority", "Cause of death", "HIV status", "Religion", "Etat matrimonial"):
        labels = codes if header == "ICD code" else ["Chinese", "Black", "South Asian"] if header == "Visible minority" else \
            ["Chronic liver disease", "Accidental poisoning", "Heart disease"] if header == "Cause of death" else \
            ["HIV negative", "HIV positive", "Unknown"] if header == "HIV status" else ["Sikh", "Buddhist", "No religious affiliation"]
        data = _ledger(header, labels)
        rep = run_bytes(data)
        fl = {f["column"]: f for f in rep["privacy"]["flagged"]}
        # (the engine's own scan already names some headers, religion among them: "named like personal data"; the others are named here)
        assert len(fl) == 1 and list(fl.values())[0]["decision"] == "withhold" and \
            ("sensitive" in list(fl.values())[0]["kind"] or "personal data" in list(fl.values())[0]["kind"]), (header, fl)
        assert rep["ok"] and "Total amount" in headline(rep), (header, headline(rep))
        blob = everything(rep, data)
        assert not [v for v in labels if v in blob], (header, [v for v in labels if v in blob])
    # negative: a column of the same shape under an ordinary header is not flagged
    rep_n = run_bytes(_ledger("Channel", ["Online", "Store", "Phone"]))
    assert rep_n["privacy"]["flagged"] == [], rep_n["privacy"]["flagged"]


def test_f03_the_sensitive_vocabulary_covers_four_languages_with_word_boundaries_and_never_a_measure():
    """Wave 5f, C. The flagging vocabulary is strict: whole words or phrases (accents folded, case ignored), English with French, Spanish and
    German names; never "sex" in Essex, "race" in Terrace, "aids" in "Aids and appliances", "union" in Reunion; and a number column with
    many different values under such a header is a measure, not a category."""
    hits = ["Marital status", "Marital", "Indigenous identity", "HIV status", "ICD code", "ICD-10", "Cause of death", "Religion", "Ethnicity",
            "Ethnic origin", "Visible minority", "Race", "Sex", "Gender", "Sexual orientation", "Disability", "Diagnosis", "Health status",
            "Political party", "Trade union", "AIDS", "Criminal record", "Nationality", "Genetic", "Aboriginal", "First Nations", "Inuit",
            "\u00c9tat matrimonial", "Minorit\u00e9 visible", "Identit\u00e9 autochtone", "Cause de d\u00e9c\u00e8s", "Orientation sexuelle", "Sexe",
            "Estado civil", "Raza", "Religi\u00f3n", "Discapacidad", "Causa de muerte", "Familienstand", "Geschlecht", "Behinderung", "Todesursache",
            "Konfession", "Staatsangeh\u00f6rigkeit", "VIH", "sida"]
    misses = ["Aids and appliances", "Essex", "Terrace", "Grace period", "Sextant", "Unisex", "Reunion", "Racetrack", "Party size", "Union Station",
              "Condition", "Health insurance", "Genre", "Region", "Branch", "Product", "Income", "Salary", "Amount", "Embrace", "Trace",
              "Medical supplies", "Drug", "Credit", "Hivemind", "Facility", "Placebo", "Tracer", "Sixty", "Visa"]
    assert [h for h in hits if not NB._sensitive_header(h)] == [], [h for h in hits if not NB._sensitive_header(h)]
    assert [h for h in misses if NB._sensitive_header(h)] == [], [(h, NB._sensitive_header(h)) for h in misses if NB._sensitive_header(h)]
    cats = pd.Series(["Single", "Married", "Widowed"] * 40)
    amounts = pd.Series(["%d" % (1000 + 7 * i) for i in range(120)])
    assert NB._personal_kind("Marital status", cats) == "sensitive_category"
    assert NB._personal_kind("Disability benefit", amounts) is None            # a measure under a sensitive header
    assert NB._personal_kind("Marital status", pd.Series(["1", "2", "3", "9"] * 30)) == "sensitive_category"   # a coded category is not a measure


def test_f04_a_sensitive_column_never_names_a_series_nor_leaks_through_a_constant_label_or_the_layout_notes():
    """Wave 5f, C: every path a column's values can take to an output. (a) The long-table layout names each series from its text columns:
    a sensitive column one to one with the series never does (it is set aside, withheld); (b) a CONSTANT sensitive column is the sort of
    column `label_hint` reads and the metadata block echoes: neither prints its value; (c) the refusal names the column, never a value."""
    # (a) the long panel: the series' "Religion" is a name per series
    data = MC.long_panel_with_owner(column="Religion")
    rep = run_bytes(data)
    lay = rep["input"]["layout"]
    assert lay and "Religion" in lay.get("personal_set_aside", {}), lay
    blob = everything(rep, data)
    assert not [n for n in MC.OWNERS if n in blob or n.split()[1].lower() in blob.lower()], "an owner's name is in an output"
    # (b) a constant sensitive column of an official cube: its single value is never echoed
    rows = MC.partition().decode().splitlines()
    head = rows[0].split(",")
    j = head.index('"Sales"')
    head[j] = '"Marital status"'
    rows = [",".join(head)] + [",".join(r.split(",")[:j] + ['"Widowed"'] + r.split(",")[j + 1:]) for r in rows[1:]]
    data2 = ("\n".join(rows) + "\n").encode()
    rep2 = run_bytes(data2)
    assert [f["column"] for f in rep2["privacy"]["flagged"]] == ["marital_status"], rep2["privacy"]["flagged"]
    assert "Widowed" not in everything(rep2, data2), "the constant value of a withheld column is in an output"
    assert est(rep2), "the cube is still read without a constant column"
    # (d) a WIDE table of periods (Eurostat's shape) with a sensitive id column among the series' identifiers: reshaped to long before the scan,
    # flagged after landing, never read
    wide = MC.wide_period()[0]
    dfw = pd.read_csv(io.BytesIO(wide), dtype=str, keep_default_na=False)
    vals = ["Widowed", "Single", "Married"]
    dfw.insert(1, "Marital status", [vals[i % 3] for i in range(len(dfw))])
    data3 = dfw.to_csv(index=False).encode()
    rep3 = run_bytes(data3)
    assert [f["column"] for f in rep3["privacy"]["flagged"]] == ["marital_status"], rep3["privacy"]["flagged"]
    assert not [v for v in vals if v in everything(rep3, data3)], "a value of a withheld id column of a wide table is in an output"


def mean_windows(df: "pd.DataFrame", mask, scale: float = 1.0, date: str = "REF_DATE", value: str = "VALUE"):
    """(prior, latest) 12-month MEANS of the selected rows' VALUE (rows added up by month first), in base units: what a level is."""
    pri, lat = window_sums(df, mask, scale, date, value)
    return pri / 12.0, lat / 12.0


def test_f05_a_dollar_average_beside_a_whole_country_row_is_a_level_never_a_12_month_total():
    """Wave 5f, A (fuzz v2 F10: seeds 1 2 40 46 75 81 86 106 117 130 144 152 175 243 245 253 290, and 125). Average rent, average weekly
    earnings, median income, price per unit: dollars, provinces and a whole-country row. The file has NO word for what is measured (the
    measure's name is in the table's title, not in the CSV), so the dollar unit read as a flow: the engine printed the whole-country row's
    12-month TOTAL (seed 1: 18,853 where the level is 1,571; the % change was right). The evidence is in the cells: the whole row is not the
    sum of the provinces and lies BETWEEN the smallest and the largest, which a total of non-negative parts never does. The measure is a
    level: averaged over the window, never added over months or across members, the whole row read as its aggregate."""
    for fname in ("f05_seed1_average_dollars.csv", "f05_seed125_average_dollars_cents.csv"):
        df = frame(fname)
        whole = "Total" if "f05_seed1_" in fname else "United States"
        rep = run_file(fname)
        e = est(rep)
        assert e and "average level over the window" in e["text"] and "12-month totals" not in e["text"], (fname, e.get("text"))
        assert e["measure"]["aggregation"].startswith("mean") and not e["measure"]["type_basis"].startswith("positively"), e["measure"]
        pri, lat = mean_windows(df, df["GEO"] == whole)
        f = figs(rep)
        assert f is not None and close(f[0], pri) and close(f[1], lat), (fname, f, (pri, lat), headline(rep))
        g = dim(rep, "GEO")
        assert g["role"] == "rate_aggregate" and g["total"] == whole, g
    # synthetic: every spelling of the whole's name, with and without a word for the measure
    for kw in ({}, {"whole": "National"}, {"whole": "Total"}, {"words": ("Average weekly earnings",)}):
        data = MC.average_dollars(**kw)
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
        whole = kw.get("whole", "Canada")
        rep = run_bytes(data)
        assert "12-month totals" not in est(rep)["text"] and est(rep)["measure"]["aggregation"].startswith("mean"), (kw, est(rep)["text"])
        pri, lat = mean_windows(df, df["GEO"] == whole, 1000.0)
        f = figs(rep)
        assert f is not None and close(f[0], pri) and close(f[1], lat), (kw, f, (pri, lat))
    # negative: the whole row IS the sum of the provinces (a flow, verified): 12-month totals, as before
    data = MC.average_dollars(sum_total=True)
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    rep = run_bytes(data)
    assert "12-month totals" in est(rep)["text"] and dim(rep, "GEO")["role"] == "partition", est(rep)["text"]
    pri, lat = window_sums(df, df["GEO"] == "Canada", 1000.0)
    check_truth(rep, pri, lat, "a verified sum is still a flow")


def test_f06_the_words_of_a_dimension_that_names_what_is_measured_say_it_is_a_level():
    """Wave 5f, A. The words that describe the measure are the measure column's header, the constant labels AND the members of a dimension
    whose header names what is measured (Estimates, Statistics, Indicator ...): two average wages (with and without overtime) are one kind
    of measure, so the dimension is not split into measures, and its members' words say the dollars are a level. An industry's or a
    region's label does not (negative: a region called "Average Joe's" does not make a table of sales a level)."""
    data = MC.average_dollars(sum_total=True, words=("Average hourly wage, with overtime", "Average hourly wage, without overtime"))
    rep = run_bytes(data)
    m = est(rep)["measure"]
    assert m["aggregation"].startswith("mean") and m["type_basis"].startswith("ambiguous"), m
    # negative: the same sums under an ordinary measure name, and a region whose name holds "average"
    rows = MC.partition().decode().replace("North", "Average Joe's North").encode()
    rep_n = run_bytes(rows)
    assert est(rep_n)["measure"]["aggregation"].startswith("sum") and "12-month totals" in est(rep_n)["text"], est(rep_n)["text"]


def test_f07_a_whole_countrys_name_among_other_countries_is_one_more_member_never_the_whole():
    """Wave 5f, F (fuzz v2 seeds 10 142 275, and 135). A table of COUNTRIES under the header GEO (Canada, Mexico, Brazil, Chile ...) and NO total
    row: Canada was taken for the whole of GEO ("one member shown: Canada, named as the whole of GEO; the other members do not add up to
    it") and printed alone, 15% of the real total (seed 275: $805.9M where the sum of the six is $5.5B). A name nominates; the cells decide:
    two or more country names make a table of countries, and a whole country's name that the others do not add up to and that is smaller than
    one of them is not their total. A flow's countries are added as the parts of a set of places; with no evidence of a flow, one member is
    shown and said not to be a national figure; and never the headline as 'the whole'."""
    for fname, col in (("f07_seed10_countries.csv", "GEO"), ("f07_seed142_countries_rest_of_world.csv", "GEO")):
        df = frame(fname)
        rep = run_file(fname)
        g = dim(rep, col)
        # never "named as the whole": the member shown (if one is) is the largest, and the text says it is not a national figure
        assert g.get("single_by") != "name" and not g.get("named_contradicted"), (fname, g.get("role"), g.get("total"), g.get("single_by"))
        assert g["role"] == "parts" or "this table has no total member, so this is not a national figure" in est(rep)["text"], est(rep)["text"]
        assert "named as the whole" not in est(rep)["text"] and "do not add up to it" not in est(rep)["text"], est(rep)["text"]
        pri, lat = window_sums(df, df[col] != "", 1.0)
        assert safe_or_true(rep, pri, lat, fname) in ("figure", "one member", "refusal")
    data = MC.countries_table(words="Value of shipments", leftover=True)
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    rep = run_bytes(data)
    assert dim(rep, "GEO")["role"] == "parts" and dim(rep, "GEO")["parts"] == 6, dim(rep, "GEO")
    pri, lat = window_sums(df, df["GEO"] != "", 1000.0)
    check_truth(rep, pri, lat, "six countries, a flow word")
    # negative: Canada beside its provinces, whose sum it is, is still their total (a table of one country's provinces)
    rep_p = run_bytes(MC.partition().decode().replace("Total", "Canada").encode())
    assert dim(rep_p, "GEO")["role"] == "partition" and dim(rep_p, "GEO")["total"] == "Canada", dim(rep_p, "GEO")
    # negative: a total with provinces missing (Canada bounds every province and is above their sum) is NOT demoted to a member
    data_m = MC.partition().decode().replace("Total", "Canada").encode()
    d2 = pd.read_csv(io.BytesIO(data_m), dtype=str, keep_default_na=False)
    keep = ~((d2["GEO"] == "West") | (d2["GEO"] == "Centre"))
    rep_m = run_bytes(d2[keep].to_csv(index=False).encode())
    g_m = dim(rep_m, "GEO")
    assert g_m["total"] == "Canada" and g_m.get("named_contradicted"), g_m


def _daily_ledger(fmt_date, days: int = 800, start=(2021, 1, 1), branches=("North", "South"), seed: int = 5):
    """A plain daily ledger: Date, Branch, Amount (a row a day and branch), dates rendered by fmt_date(datetime.date)."""
    import datetime as dt
    rng = np.random.RandomState(seed)
    d0 = dt.date(*start)
    rows = []
    for k in range(days):
        d = d0 + dt.timedelta(days=k)
        for bi, b in enumerate(branches):
            rows.append([fmt_date(d), b, "%d" % round((900 + 300 * bi) * (1.0 + 0.0004 * k) * (1.0 + 0.05 * rng.randn()))])
    import csv as _csv
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["Date", "Branch", "Amount"])
    w.writerows(rows)
    return buf.getvalue().encode()


def test_f08_dates_in_formats_the_core_cannot_read_are_rewritten_before_the_file_is_read():
    """Wave 5f, H (the second review's R06). Dates written 31.12.2019, 12/31/2019, "Jan 2019", "2019 Jan", 2019M01 or 20190131 gave NO
    analysis (the core reads year-month-day): a European d.m.y export is common. They are rewritten as ISO dates in the adapter before the
    file is landed, and the analysis is the one the ISO twin gets. A day/month pair that could be read either way round (03/04/2019) is settled
    by the column's other rows (a first or second field above 12), else the file is refused with a plain reason."""
    import datetime as dt
    iso = _daily_ledger(lambda d: d.isoformat())
    base = headline(run_bytes(iso))
    assert base and "Total amount" in base, base
    fmts = {"d.m.Y": lambda d: d.strftime("%d.%m.%Y"), "m/d/Y": lambda d: d.strftime("%m/%d/%Y"), "d/m/Y": lambda d: d.strftime("%d/%m/%Y"),
            "b d, Y": lambda d: d.strftime("%b %d, %Y"), "d b Y": lambda d: d.strftime("%d %b %Y"), "Ymd": lambda d: d.strftime("%Y%m%d"),
            "m/d/Y H:M": lambda d: d.strftime("%m/%d/%Y 00:00")}
    for name, f in fmts.items():
        rep = run_bytes(_daily_ledger(f))
        assert rep["ok"] and headline(rep) == base, (name, headline(rep)[:200], base[:200])
        assert any(x.get("rule") == "dates_read" for x in rep["cleaning"]["fixes"]), (name, rep["cleaning"]["fixes"][:2])
    monthly = lambda fn: ("Date,Region,Amount\n" + "\n".join("%s,%s,%d" % (fn(dt.date(2019 + i // 12, i % 12 + 1, 1)), r, 500 + 40 * i + 90 * k)
                                                              for i in range(36) for k, r in enumerate(("East", "West")))).encode() + b"\n"
    base_m = headline(run_bytes(monthly(lambda d: d.isoformat())))
    for name, fn in {"b Y": lambda d: d.strftime("%b %Y"), "Y b": lambda d: d.strftime("%Y %b"), "YMmm": lambda d: d.strftime("%YM%m"),
                     "B Y": lambda d: d.strftime("%B %Y")}.items():
        rep = run_bytes(monthly(fn))
        assert rep["ok"] and headline(rep) == base_m, (name, headline(rep)[:200], base_m[:200])
    # ambiguous: every day is 12 or below
    amb = ("Date,Branch,Amount\n" + "\n".join("%02d/%02d/%d,N,%d" % (1 + i % 12, 1 + (i * 5) % 12, 2020 + i // 144, 100 + i) for i in range(300))).encode()
    r_amb = run_bytes(amb)
    assert r_amb["error"] and "day/month/year or month/day/year" in r_amb["error"] and not r_amb["ok"], r_amb["error"]
    # settled by another row: one date has a first field of 25
    settled = amb.replace(b"01/01/2020", b"25/01/2020", 1)
    assert run_bytes(settled)["ok"]
    # negatives: an ISO file passes through byte for byte; a text column that only holds a date now and then is left alone; an Amount column of
    # six-digit numbers is no date (the heading must say so)
    assert NB.normalize_file(iso)[0] is iso
    mixed = ("Date,Branch,Note,Amount\n" + "\n".join("2021-%02d-01,N,%s,%d" % (1 + i % 12, "call on 03/04/2019" if i % 7 == 0 else "ok", 100 + i) for i in range(40))).encode()
    assert NB.normalize_file(mixed)[0] is mixed
    six = ("Date,Branch,Amount\n" + "\n".join("2021-%02d-01,N,%d" % (1 + i % 12, 201901 + i) for i in range(40))).encode()
    assert NB.normalize_file(six)[0] is six


def test_f09_numbers_written_with_spaces_and_a_decimal_comma_are_read_and_a_series_id_is_found_by_behaviour():
    """Wave 5f, E (fuzz v2 seeds 44 and 177: French tables, "708 219,6"; seed 129: Spanish, COORDENADA). The core reads neither "708 219,6" (a space
    between the thousands, a decimal comma) nor a column the engine's list of metadata names does not know in Spanish. The value column was
    text, a coordinate was the 'measure' (a constant figure) or a dimension of 14 members, the structure layer found no headline slice, and
    the OLD path ran: 'average coordonnee 0.0%' led the report. Now the numbers are rewritten before the file is read, a column one to one with
    the combination of the others is a series id in any language, and a table of series the layer finds no headline in is refused."""
    df = frame("f10_seed44_french_spaced_numbers.csv")
    val = [c for c in df.columns if c.upper() == "VALEUR"][0]
    assert any("\xa0" in v for v in df[val]), "the fixture holds a non-breaking space between the thousands"
    rep = run_file("f10_seed44_french_spaced_numbers.csv")
    twin = df.copy()
    twin[val] = twin[val].str.replace("\xa0", "", regex=False).str.replace(",", ".", regex=False)
    rep_t = run_bytes(twin.to_csv(index=False).encode())
    assert figs(rep) is not None and figs(rep) == figs(rep_t), (figs(rep), figs(rep_t), headline(rep))
    assert any(x.get("rule") == "numbers_read" for x in rep["cleaning"]["fixes"]), rep["cleaning"]["fixes"][:3]
    assert "coordonn" not in headline(rep).lower(), headline(rep)
    # a series id by behaviour (Spanish COORDENADA is no name the engine lists)
    rep_s = run_bytes(MC.spanish_with_coordinate())
    assert est(rep_s) and "COORDENADA" not in [d["column"] for d in st(rep_s)["dims"] if d.get("role") != "constant"], st(rep_s)["dims"]
    g = dim(rep_s, "GEO")
    assert g["role"] == "partition" and g["total"] == "Todas las regiones", g
    # fail closed: a value column that cannot be read as numbers is a refusal naming the reason, never the row-average path
    bad = df.copy()
    bad[val] = bad[val] + " $"
    rep_b = run_bytes(bad.to_csv(index=False).encode())
    assert refused(rep_b) or est(rep_b), headline(rep_b)
    assert "coordonn" not in headline(rep_b).lower() and "average" not in headline(rep_b).lower().split("(")[0], headline(rep_b)
    # negatives: US thousands "1,234" and a bare decimal comma "123,4" are left to the core (ambiguous / already read)
    us = ("Date,Branch,Amount\n" + "\n".join("2021-%02d-01,N,\"%s\"" % (1 + i % 12, "{:,}".format(1000 + 37 * i)) for i in range(40))).encode()
    assert NB.normalize_file(us)[0] is us
    comma = ("Date,Branch,Amount\n" + "\n".join("2021-%02d-01,N,\"%d,%d\"" % (1 + i % 12, 10 + i, i % 10) for i in range(40))).encode()
    assert NB.normalize_file(comma)[0] is comma


def test_f10_a_daily_or_weekly_file_is_compared_over_whole_months_never_a_month_the_file_stops_in_the_middle_of():
    """Wave 5f, D (fuzz v2 seeds 34 65 79 131 132). A daily file ending on 21 November was read "12 months to Nov 2023": 26 days of the last month
    against a whole month a year before (-1.8% where the 365 days say +0.6%). The rows of a month the file stops in the middle of (and a first
    month that enters the comparison) are left out and said so; the months compared are whole."""
    import datetime as dt
    full = _daily_ledger(lambda d: d.isoformat(), days=730 + 31 + 30)                  # 2021-01-01 .. 2023-02-06 + : ends mid-month
    last = dt.date(2021, 1, 1) + dt.timedelta(days=730 + 31 + 30 - 1)
    assert last.day not in (28, 29, 30, 31)
    rep = run_bytes(full)
    assert any(x.get("rule") == "partial_month_left_out" for x in rep["cleaning"]["fixes"]), rep["cleaning"]["fixes"][:3]
    assert any("in the middle of" in x["text"] for x in rep["limitations"]), [x["text"][:90] for x in rep["limitations"]]
    # the figure: the plain monthly totals of the whole months only
    df = pd.read_csv(io.BytesIO(full), dtype=str)
    df["v"] = pd.to_numeric(df["Amount"])
    df["m"] = df["Date"].str[:7]
    by = df.groupby("m")["v"].sum()
    whole = [m for m in sorted(by.index) if m < last.strftime("%Y-%m")]
    lat, pri = by[whole[-12:]].sum(), by[whole[-24:-12]].sum()
    led = {x["id"]: x["value"] for x in json.loads(rep["downloads"]["ledger_json"])["analysis_ledger"]}
    assert close(led["measure.amount.total.last12"], lat) and close(led["measure.amount.total.prior12"], pri), \
        (led.get("measure.amount.total.last12"), led.get("measure.amount.total.prior12"), lat, pri)
    # the repro of seed 34 (a daily file whose first month is partial too: the comparison is refused as too short, never made over a partial month)
    r34 = run_file("f11_seed34_daily_partial_month.csv")
    assert "12 months to Nov 2023" not in headline(r34) and ("too short" in headline(r34) or "Oct 2023" in headline(r34)), headline(r34)
    # negatives: a daily file that ends on the last day of a month is not trimmed; a monthly file is untouched
    whole_file = _daily_ledger(lambda d: d.isoformat(), days=365 + 365 + 31 + 28)                  # 2021-01-01 .. 2023-02-28
    assert NB.ledger_tidy(whole_file) is None
    monthly = ("Date,Region,Amount\n" + "\n".join("%04d-%02d-01,E,%d" % (2019 + i // 12, i % 12 + 1, 500 + i) for i in range(36))).encode()
    assert NB.ledger_tidy(monthly) is None


def test_f11_total_rows_in_a_plain_file_are_left_out_when_the_cells_say_they_are_totals_and_a_real_branch_is_never_dropped():
    """Wave 5f, B (fuzz v2 seeds 4 13 29 36 88 126 140 163 176 193 201 203 217 229 246 257 270 294, and the live product today). A business file with
    a literal Total / All row in the branch column, the product column or both, and two number columns (units and an amount), is not read by the
    structure layer, so the plain path ADDED the Total rows to the rows they total: the level 2 to 4 times too big (the % change was right). A
    member NOMINATED by a total word is a total only if the cells say so (its rows equal the sum of the others', cell by cell, by a check that could
    have failed): then it is left out and said so. A name that merely holds a total word (All Saints Church, Total Wine, Head Office) is a member and
    is counted. A bare total phrase that no cell can verify is left out too, with a plain word in the headline; a loose name never."""
    import test_nl_business as TB
    import check_business_corpus as CBC
    import make_business as MB
    for i in (203, 204, 207, 209, 212, 215):
        full, _detail, sp = MB.make_pivot(i)
        NB._PROFILE_CACHE.clear()
        rep = NB.run(full, "table.csv", "", {}, AS_OF)
        why = TB.judge_pivot(i, CBC.pivot_summary(rep))
        assert not why, (i, sp["freq"], sp["margin"], sp["measure_names"], why)
        if sp["margin"] is not None and sp["measures"] == 2:
            assert any(x.get("rule") == "total_rows_left_out" for x in rep["cleaning"]["fixes"]), (i, rep["cleaning"]["fixes"][:3])
    # the repro of seed 4 (Store: Total, All other branches, Tarnfield; Qty and Amount; weekly): the level is the plain sum, not twice
    df = frame("f12_seed4_weekly_total_rows_two_measures.csv")
    rep = run_file("f12_seed4_weekly_total_rows_two_measures.csv")
    led = {x["id"]: x["value"] for x in json.loads(rep["downloads"]["ledger_json"])["analysis_ledger"]}
    d2 = df[df["Store"] != "Total"].copy()
    d2["v"] = pd.to_numeric(d2["Amount"])
    d2["m"] = d2["Date"].str[:7]
    by = d2.groupby("m")["v"].sum()
    months = sorted(by.index)
    assert close(led["measure.amount.total.last12"], by[months[-12:]].sum()) or close(led["measure.amount.total.last12"], by[months[-13:-1]].sum()), \
        (led["measure.amount.total.last12"], by[months[-12:]].sum())
    # how a name nominates (the cells still decide): a bare total phrase, or a name that ENDS in a total word, is "exact"; a name that merely
    # holds or starts with a total word, or holds "all", is "loose" (a real branch until the cells say otherwise); a part of something is never
    for name, want in (("Total", "exact"), ("All", "exact"), ("All branches", "exact"), ("Grand total", "exact"), ("Company total", "exact"),
                       ("Branch total", "exact"), ("Total for all products", "exact"), ("All Saints Church", "loose"), ("Total Wine", "loose"),
                       ("Total Fitness", "loose"), ("Head Office", None), ("Totalmente", None), ("All other branches", None),
                       ("Total excl. Seasonal shops", None)):
        assert NB._total_nomination(name) == want, (name, NB._total_nomination(name), want)
    # negatives: a REAL branch with a total word is counted (its rows are in the sums), and says nothing
    big = ["Date,Store,Amount"] + ["%04d-%02d-01,%s,%d" % (2019 + i // 12, i % 12 + 1, s, 100 * (k + 1) + i) for i in range(36)
                                  for k, s in enumerate(("All Saints Church", "North", "South"))]
    t = NB.ledger_tidy(("\n".join(big) + "\n").encode())
    assert t is None or not [x for x in t["totals"] if x["left_out"]], t
    # a bare Total that is NOT the sum and is not above it is a member of its own (counted, said so); one above the sum is left out, unverified
    # (two number columns, so the structure layer does not read the file and the adapter decides)
    def ledger2(total_of):
        rows = ["Date,Store,Qty,Amount"]
        for i in range(36):
            d = "%04d-%02d-01" % (2019 + i // 12, i % 12 + 1)
            a, b = 100 + i, 150 + 2 * i
            rows += ["%s,North,%d,%d" % (d, i % 4, a), "%s,South,%d,%d" % (d, (i + 1) % 4, b), "%s,Total,%d,%d" % (d, i % 4 + (i + 1) % 4, total_of(a, b, i))]
        return ("\n".join(rows) + "\n").encode()
    t2 = NB.ledger_tidy(ledger2(lambda a, b, i: 120 + i))
    assert t2 and t2["totals"][0]["status"] == "contradicted" and not t2["totals"][0]["left_out"], t2
    rep2 = run_bytes(ledger2(lambda a, b, i: 120 + i))
    assert any("not the sum of the other rows" in x["text"] for x in rep2["limitations"]), [x["text"][:80] for x in rep2["limitations"]]
    t3 = NB.ledger_tidy(ledger2(lambda a, b, i: a + b + 40))
    assert t3 and t3["totals"][0]["status"] == "contradicted_bounding" and t3["totals"][0]["left_out"], t3
    rep3 = run_bytes(ledger2(lambda a, b, i: a + b + 40))
    assert "could not be checked" in headline(rep3), headline(rep3)
    # the same file with the Total equal to the sum: verified, left out, no word in the headline
    rep4 = run_bytes(ledger2(lambda a, b, i: a + b))
    assert "could not be checked" not in headline(rep4) and any(x.get("rule") == "total_rows_left_out" for x in rep4["cleaning"]["fixes"]), headline(rep4)


# ----------------------------------------------------------------------------- runner
def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if only:
        tests = [(n, f) for n, f in tests if any(o in n for o in only)]
    failed = 0
    for n, f in tests:
        t0 = time.perf_counter()
        try:
            f()
            print("  PASS  %s  (%.1f s)" % (n, time.perf_counter() - t0))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  %s  (%.1f s)\n        %s: %s" % (n, time.perf_counter() - t0, type(exc).__name__, str(exc)[:900]))
        sys.stdout.flush()
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    print("ALL TESTS PASSED" if not failed else "SOME TESTS FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
