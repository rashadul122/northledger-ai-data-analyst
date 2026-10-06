#!/usr/bin/env python3
"""Fixtures for the report checks in tools/check_ui.js: four INVENTED business files, each run through
the browser adapter (engine/nl_browser.py) natively, plus the site's own sample.

    python tools/make_ui_fixtures.py --out DIR      # writes DIR/<name>.csv, DIR/<name>.json and
                                                    # DIR/<name>.profile.json

The files are made up for the checks (no real people, accounts or businesses) and cover the chart
rules the report draws from the data's roles:
  rent-roll          a rent roll: buildings, units, tenant names (flagged as personal), rent
                     charged and paid, status; 36 months, a second date column, TBD amounts
  sales-ledger       orders with region, category, channel and four measures; 42 months with a
                     planted step up in the latest 12 (a CONFIRMED change) and year-end peaks
  web-analytics      a web analytics export, one row per day, source and device; 30 months with
                     a tracking outage that leaves conversions empty (the missingness heatmap)
  cafe-invoices-18m  18 months of supplier invoices with a contact email: too short for a
                     forecast or a tested change, so most chart rules say why they are absent
  rent-roll-gated    300 rent payments whose paid dates mix day-first and month-first, with TBD
                     amounts: the cleaning sets aside too many rows and the analysis stops
  shop-margins-36m   36 months of shop sales: a net margin whose averages are below zero (a change
                     with no percentage), a fee that moved 3% (under the 5% bar), a flat measure,
                     and a forecast too short to be offered
  sample             engine/sample-messy.csv at its pinned date (engine/pack.json)
  sales-ledger-charts  the sales ledger run again with an AI plan that asks for eight charts from the chart menu
                     (CHARTS_PLAN, tools/fixtures/viz/spec.json menu), one of which the file cannot support, so the
                     report carries the charts the engine built (rep.viz.charts, rule V records in rep.charts) and a
                     refusal with its reason (rep.viz.refused); when the adapter cannot make it, the file holds
                     {"__fixture_error": why} and only the check that reads it fails
  viz-hostile-header  120 orders whose second column is headed <img src=x onerror=alert(1)>, run with an AI plan
                     that asks for a Pareto of that column (3 levels): the engine refuses it, and the refusal names the
                     column as the file does (rep.viz.refused[].columns), which the page must print as text
  reviews-plan-drop  the SYNTHETIC reviews (tools/fixtures/eval/reviews_synthetic.csv) run with an AI plan that sets
                     aside the "All Electronics" department (17.3% of the rows) on an "umbrella ... overlapping" reason,
                     review_text kept and its theme chart asked for (the final evaluation, 1 Oct 2026): the report
                     carries ai_plan.row_drops (the notice, the reason, the engine's check), health.explain (its newest
                     row is 3.5 years old) and the theme chart of the kept text
  official-cube     a SYNTHETIC statistical table in Statistics Canada's shape (tools/fixtures/structure/make_cubes.py: a total and
                     five regions, a tenth of the regions' cells suppressed, 48 months moved to end in Aug 2026), read by its
                     structure (wave 4, track B): the report carries an estimand, a sum-check, a not-allocated step, an official
                     aggregate's process grade, a forecast with its back-test and a dropped row forecast; when the adapter
                     cannot make it, the file holds {"__fixture_error": why} and only the check that reads it fails
  orders-private     240 orders with a buyer's email, a free-text note and the member of staff, run
                     with an AI plan (PRIVATE_PLAN) and the choices code / withhold / keep: the
                     coded email and the kept staff name fail their tests (the withheld note's date
                     test is not run), so the report carries the "values the data tests flagged"
                     download (engine/nl_browser.py _flagged_cells), the email's values as "value coded"

Each <name>.profile.json is what the page would send the AI planner before its own filter, and
<name>.landed.json the map the worker sends beside it ({header: the engine's landed name}): the adapter's
plan_profile_json with the scan's flags and the visitor's choices, as engine/worker.js asks for it after
those choices. orders-private is profiled under PRIVATE_CHOICES (code, withhold, keep), the choices its checks
make; every other file under the default (every flagged column withheld). Each carries time,
analysis_limits and each column's looks_personal and privacy_flag.

Deterministic (fixed seed, no clock). Needs numpy and pandas, as the adapter does.
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
AS_OF = "2026-09-15"


def months(start, n):
    y, m = start
    return [dt.date(y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1, 1) for i in range(n)]


def write_files(out):
    rng = np.random.default_rng(20260924)
    # 1) rent roll: 3 buildings, 58 units, 36 months (Sep 2023 - Aug 2026), one row per unit-month
    first = ["Avery", "Jordan", "Priya", "Mateo", "Chen", "Fatima", "Liam", "Sofia", "Noah", "Amara", "Ethan", "Yuki", "Omar", "Grace", "Ravi", "Elena"]
    last = ["Tremblay", "Nguyen", "Singh", "Rossi", "Okafor", "Martin", "Kowalski", "Haddad", "Clarke", "Silva", "Park", "Dubois"]
    blds = [("Carlaw Lofts", 24, 1850), ("Pape Court", 20, 1620), ("Queen East Walk-Up", 14, 1480)]
    rows = []
    for b, n, base in blds:
        for u in range(1, n + 1):
            unit = "%s-%d%02d" % (b.split()[0][:3].upper(), 1 + (u - 1) // 6, u)
            rent0 = base + rng.integers(-120, 180)
            tenant = rng.choice(first) + " " + rng.choice(last)
            for i, mo in enumerate(months((2023, 9), 36)):
                if rng.random() < 0.035:            # tenant turnover: new name, re-let at a higher rent
                    tenant = rng.choice(first) + " " + rng.choice(last); rent0 = int(rent0 * 1.06)
                if i and i % 12 == 0: rent0 = int(round(rent0 * 1.025))   # guideline increase each September
                charged = rent0
                late_p = 0.06 + 0.004 * i           # late payments creep up over time
                r = rng.random()
                status = "Late" if r < late_p else ("Partial" if r < late_p + 0.03 else "Paid")
                paid = charged if status != "Partial" else round(charged * rng.uniform(0.4, 0.8), 2)
                day = int(rng.integers(1, 6)) if status == "Paid" else int(rng.integers(8, 27))
                pd_ = dt.date(mo.year, mo.month, day)
                rows.append({"period": mo.strftime("%Y-%m-%d"), "building": b, "unit": unit, "tenant_name": tenant,
                             "rent_charged": "%.2f" % charged, "rent_paid": "%.2f" % paid, "status": status,
                             "payment_date": pd_.strftime("%Y-%m-%d")})
    df = pd.DataFrame(rows)
    idx = rng.choice(len(df), 40, replace=False)
    df.loc[idx[:12], "rent_paid"] = "TBD"                                  # unposted payments
    df.loc[idx[12:20], "status"] = df.loc[idx[12:20], "status"].str.upper()   # casing drift
    df.loc[idx[20:28], "building"] = df.loc[idx[20:28], "building"] + " "     # trailing spaces
    p = df.loc[idx[28:40], "payment_date"]
    df.loc[idx[28:40], "payment_date"] = pd.to_datetime(p).dt.strftime("%d/%m/%Y")   # a second date format
    df = pd.concat([df, df.sample(9, random_state=3)]).reset_index(drop=True)          # re-exported rows
    df.to_csv(os.path.join(out, "rent-roll.csv"), index=False)

    # 2) sales ledger: 42 months (Mar 2023 - Aug 2026), orders with region, category, channel, 3+ measures
    cats = {"Coffee": 18.0, "Tea": 12.5, "Bakery": 6.5, "Grocery": 9.0, "Equipment": 145.0, "Gift Cards": 50.0, "Snacks": 4.0, "Dairy": 5.5}
    regions = ["Toronto", "Ottawa", "Hamilton", "London", "Kingston"]
    channels = ["Online", "Store", "Wholesale"]
    rows = []; oid = 100000
    for i, mo in enumerate(months((2023, 3), 42)):
        season = 1 + 0.35 * (mo.month in (11, 12)) - 0.12 * (mo.month in (1, 2))
        growth = 1.0 if i < 30 else 1.24                   # a planted 24% step up in the latest 12 months
        n = int(rng.poisson(190 * season * growth))
        for _ in range(n):
            c = rng.choice(list(cats), p=[.22, .12, .16, .14, .04, .06, .16, .10])
            oid += 1
            day = int(rng.integers(1, 29))
            q = int(rng.integers(1, 7)) if c != "Equipment" else 1
            price = round(cats[c] * rng.uniform(0.85, 1.25), 2)
            disc = round(rng.choice([0, 0, 0, 0.05, 0.1]), 2)
            rows.append({"order_date": dt.date(mo.year, mo.month, day).isoformat(), "order_id": "SO-%d" % oid,
                         "region": rng.choice(regions, p=[.4, .18, .16, .14, .12]), "category": c,
                         "channel": rng.choice(channels, p=[.45, .4, .15]), "qty": q, "unit_price": price,
                         "discount": disc, "amount": round(q * price * (1 - disc), 2)})
    df = pd.DataFrame(rows)
    idx = rng.choice(len(df), 70, replace=False)
    df.loc[idx[:15], "region"] = df.loc[idx[:15], "region"].str.lower()
    df["amount"] = df["amount"].astype(object)
    df.loc[idx[15:25], "amount"] = ""
    df.loc[idx[25:30], "amount"] = "-" + df.loc[idx[25:30], "amount"].astype(str)   # refunds keyed as negatives
    df.loc[idx[30:45], "order_date"] = pd.to_datetime(df.loc[idx[30:45], "order_date"]).dt.strftime("%Y/%m/%d")
    df = pd.concat([df, df.sample(14, random_state=5)]).reset_index(drop=True)
    df.to_csv(os.path.join(out, "sales-ledger.csv"), index=False)

    # 3) web analytics export (GA-style): one row per day x source x device, 30 months (Mar 2024 - Aug 2026)
    srcs = [("google / organic", 420), ("(direct) / (none)", 260), ("newsletter / email", 90), ("instagram / social", 120), ("bing / organic", 45), ("partner-site / referral", 30)]
    devs = [("desktop", 0.46), ("mobile", 0.48), ("tablet", 0.06)]
    rows = []
    start = dt.date(2024, 3, 1); end = dt.date(2026, 8, 31)
    d = start; k = 0
    while d <= end:
        k += 1
        wk = 0.82 if d.weekday() >= 5 else 1.0
        trend = 1 + 0.012 * ((d - start).days / 30.4)
        for s, base in srcs:
            for dv, sh in devs:
                sess = int(rng.poisson(base * sh * wk * trend / 3))
                eng = int(rng.binomial(sess, 0.58)) if sess else 0
                conv = int(rng.binomial(eng, 0.035)) if eng else 0
                outage = dt.date(2025, 6, 10) <= d <= dt.date(2025, 7, 4)   # tag manager outage: no conversions recorded
                rows.append({"date": d.isoformat(), "session_source_medium": s, "device_category": dv,
                             "sessions": sess, "engaged_sessions": eng, "conversions": "" if outage else conv,
                             "avg_engagement_time_s": round(rng.gamma(4, 18), 1) if sess else ""})
        d += dt.timedelta(days=1)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(out, "web-analytics.csv"), index=False)

    # 4) short file: 18 months of cafe supplier invoices (Mar 2025 - Aug 2026), with a contact email column
    sup = [("North Roast Coffee", "Coffee beans", 640), ("Lakeshore Dairy", "Dairy", 310), ("Queen St Bakery", "Baked goods", 420),
           ("CleanPro Supply", "Cleaning", 95), ("Hydro Toronto", "Utilities", 380)]
    rows = []
    for i, mo in enumerate(months((2025, 3), 18)):
        for s, cat, base in sup:
            for _ in range(int(rng.integers(2, 6)) if cat not in ("Utilities",) else 1):
                day = int(rng.integers(1, 28))
                rows.append({"invoice_date": dt.date(mo.year, mo.month, day).isoformat(), "supplier": s, "category": cat,
                             "amount": "%.2f" % (base / 3 * rng.uniform(0.7, 1.4) * (1 + 0.01 * i)),
                             "paid_by": rng.choice(["Card", "EFT", "Cheque"], p=[.5, .4, .1]),
                             "contact_email": s.split()[0].lower() + "@example.com"})
    df = pd.DataFrame(rows)
    df.loc[3, "amount"] = "n/a"; df.loc[17, "category"] = "dairy "
    df.to_csv(os.path.join(out, "cafe-invoices-18m.csv"), index=False)
    # 5) a rent roll the cleaning gate stops: ambiguous paid dates and TBD amounts
    rows = ["building,unit,rent_due,amount_paid,date_paid"]
    for i in range(300):
        d, m = i % 28 + 1, i % 12 + 1
        paid = "%02d/%02d/2025" % (d, m) if i % 3 == 0 else "%02d/%02d/2025" % (m, d) if i % 3 == 1 else "2025-%02d-%02d" % (m, d)
        rows.append("Pape Ave %d,%d,2025-%02d-01,%s,%s" % (i % 3 + 1, 100 + i % 12, m, "TBD" if i % 3 == 0 else "%d" % (1500 + (i % 7) * 50), paid))
    with open(os.path.join(out, "rent-roll-gated.csv"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    # 6) 36 months of shop sales: net margin averages below zero (a change with no percentage), a fee
    #    that falls a true 3% (moved, size not yet shown), a flat wait time, and a forecast too short
    #    to be offered (the site review of 24 Sep 2026)
    import math
    import random as _random
    r2 = _random.Random(29)
    rows = ["sold_on,region,net_margin,fee,wait_minutes"]
    for k in range(36):
        y, m = 2023 + (k + 8) // 12, (k + 8) % 12 + 1
        n = int(round(150 * (1.25 if k >= 24 else 1.0) * math.exp(r2.gauss(0, 0.05))))
        for _ in range(n):
            margin = r2.gauss(-2.6 if k >= 24 else -4.1, 1.2)
            fee = r2.gauss(80.0 * (0.97 if k >= 24 else 1.0), 1.5)
            wait = r2.gauss(20.0, 3.0)
            rows.append("%04d-%02d-%02d,%s,%.3f,%.2f,%.1f" % (y, m, r2.randint(1, 28), r2.choice(["North", "South", "East"]), margin, fee, wait))
    with open(os.path.join(out, "shop-margins-36m.csv"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


# The AI plan the orders-private run is given (hand-written, as the planner could write it): it types every
# column, the three personal ones included, so each gets a data test, and the choices below decide what the
# card and the download may show of each (tools/check_ui.js reads both back).
PRIVATE_PLAN = {
    "goal": "How does spend move month by month?", "understanding": "Orders with the buyer, a note and the member of staff.",
    "kind": "transactions", "primary": "spend", "operations": [], "analyses": [{"type": "distribution", "columns": ["spend"]}],
    "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                {"name": "customer_email", "semantic_type": "identifier", "role": "key"},
                {"name": "notes", "semantic_type": "date", "role": "metadata"},
                {"name": "staff_name", "semantic_type": "identifier", "role": "key"},
                {"name": "spend", "semantic_type": "flow_amount", "role": "target", "unit": "currency"}],
    # the web searches (final review, 30 Sep 2026): items of fixed terms; the adapter builds the one whose terms are
    # all on engine/context_terms.json and drops the ones that name the kept staff or a coded buyer; the old free-text
    # list is never read
    "context": [{"indicator": "consumer spending", "region": "Canada", "years": [2024, 2025]},
                {"indicator": "retail sales", "region": "Dana Whitfield"},
                {"sector": "buyer01@example.org", "indicator": "retail sales"}, {"indicator": "Marco Bellini"}],
    "context_queries": ["Dana Whitfield sales 2024"]}
PRIVATE_CHOICES = {"customer_email": "code", "notes": "withhold", "staff_name": "keep"}

# The AI plan the sales-ledger-charts run is given: the chart menu's names with the ledger's columns in each chart's
# argument order (spec.json menu), and one chart the file cannot support (it has no free-text column), so the engine
# builds some and refuses one with its reason (engine/nl_viz.py validate_directive).
CHARTS_PLAN = {
    "goal": "What drove the rise in sales, and where in the year did it happen?", "understanding": "Orders with region, category, channel and amounts.",
    "kind": "transactions", "primary": "amount", "operations": [], "analyses": [{"type": "compare", "columns": ["amount"], "by": "channel"}],
    "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                {"name": "order_id", "semantic_type": "identifier", "role": "key"},
                {"name": "region", "semantic_type": "category", "role": "dimension"},
                {"name": "category", "semantic_type": "category", "role": "dimension"},
                {"name": "channel", "semantic_type": "category", "role": "dimension"},
                {"name": "qty", "semantic_type": "count", "role": "measure"},
                {"name": "unit_price", "semantic_type": "level", "role": "measure", "unit": "currency"},
                {"name": "discount", "semantic_type": "percentage", "role": "measure"},
                {"name": "amount", "semantic_type": "flow_amount", "role": "target", "unit": "currency"}],
    "charts": [{"kind": "contribution_waterfall", "columns": ["region", "amount"], "why": "The goal asks what moved sales; region is the breakdown."},
               {"kind": "calendar_heatmap", "columns": ["amount"], "why": "Monthly totals side by side show where in the year sales rose."},
               {"kind": "change_heatmap", "columns": ["region", "amount"], "why": "Where in the year each region moved."},
               {"kind": "crosstab_heatmap", "columns": ["region", "channel"], "why": "Which channels each region sells through."},
               {"kind": "theme_rating_heatmap", "columns": ["region", "qty"], "why": "A chart this file cannot support: it has no free-text column."},
               {"kind": "group_ranges", "columns": ["amount", "channel"], "why": "Whether order sizes differ by channel."},
               {"kind": "pareto", "columns": ["category", "amount"], "why": "Which categories carry most sales."},
               {"kind": "slope", "columns": ["category", "amount"], "why": "Each category before and after the rise."}]}


# the chart review of 30 Sep 2026: a column header that is markup, named in a chart the engine refuses
HOSTILE = "<img src=x onerror=alert(1)>"
HOSTILE_PLAN = {
    "goal": "Which kind of order carries the amount?", "understanding": "Orders.", "kind": "transactions",
    "primary": "amount", "operations": [], "analyses": [],
    "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                {"name": HOSTILE, "semantic_type": "category", "role": "segment"},
                {"name": "amount", "semantic_type": "flow_amount", "role": "target"}],
    "charts": [{"kind": "pareto", "columns": [HOSTILE, "amount"], "why": "Which kind carries the amount."}]}


# The AI plan the reviews-plan-drop run is given (the live reviews plan's step, cut to the synthetic file's columns)
DROP_PLAN = {
    "goal": "What do customers praise and complain about, and how do the departments compare?", "kind": "text_corpus",
    "understanding": "Product reviews, one row per review, with a 1-5 star rating, a department and a brand.",
    "primary": "rating",
    "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"},
                {"name": "verified_purchase", "semantic_type": "boolean", "role": "segment"},
                {"name": "helpful_votes", "semantic_type": "count", "role": "driver"},
                {"name": "department", "semantic_type": "category", "role": "segment"},
                {"name": "brand", "semantic_type": "entity", "role": "entity"},
                {"name": "review_title", "semantic_type": "free_text", "role": "metadata"},
                {"name": "review_text", "semantic_type": "free_text", "role": "driver"}],
    "operations": [{"op": "exclude_rows", "column": "department", "values": ["All Electronics"]},
                   {"op": "set_aside", "columns": ["review_title"]}],
    "analyses": [{"type": "compare", "columns": ["rating"], "by": "department"},
                 {"type": "themes", "columns": ["review_text"]}],
    "quality_risks": ["'All Electronics' reads as an umbrella department overlapping the specific ones, so it was "
                      "excluded; if it held unique reviews they are not covered."],
    "charts": [{"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"], "why": "Which words the low ratings use."}]}


def write_hostile(out):
    """viz-hostile-header.csv: 120 invented orders over 24 months; the second column's header is markup."""
    import csv as _csv
    import random as _random
    r = _random.Random(5)
    with open(os.path.join(out, "viz-hostile-header.csv"), "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f, lineterminator="\n")
        w.writerow(["order_date", HOSTILE, "amount"])
        for k in range(120):
            d = dt.date(2024, 1, 1) + dt.timedelta(days=6 * k)
            w.writerow([d.isoformat(), ["online", "store", "phone"][k % 3], "%.2f" % r.uniform(20, 90)])


def write_private(out):
    """orders-private.csv: invented buyers (example.org addresses), invented staff, notes that hold a phone
    number or a date, and a spend column with a few "n/a"."""
    import random as _random
    r = _random.Random(7)
    staff = ["Dana Whitfield", "Marco Bellini", "Ines Duarte", "Tom Kaczmarek"]
    rows = ["order_date,customer_email,notes,staff_name,spend"]
    for k in range(240):
        d = dt.date(2024, 1, 1) + dt.timedelta(days=3 * k)
        note = ("call 416-555-%04d" % r.randint(0, 9999)) if k % 3 == 0 else (d + dt.timedelta(days=9)).isoformat()
        spend = "n/a" if k % 29 == 5 else "%.2f" % r.uniform(5, 95)
        rows.append("%s,buyer%02d@example.org,%s,%s,%s" % (d.isoformat(), r.randint(1, 60), note, staff[k % 4], spend))
    with open(os.path.join(out, "orders-private.csv"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="folder for the CSV files and their reports")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    write_files(a.out)
    sys.path.insert(0, os.path.join(SITE, "engine"))
    os.environ["NL_BROWSER_STRICT"] = "1"      # a v2 defect stops here instead of hiding behind the v1 fallback
    import nl_browser
    pack = json.load(open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8"))
    jobs = [("sample", os.path.join(SITE, "engine", pack["sample"]["file"]), pack["sample"]["as_of"])]
    jobs += [(n, os.path.join(a.out, n + ".csv"), AS_OF) for n in ("rent-roll", "sales-ledger", "web-analytics", "cafe-invoices-18m", "rent-roll-gated", "shop-margins-36m")]
    write_private(a.out)
    jobs += [("orders-private", os.path.join(a.out, "orders-private.csv"), AS_OF)]
    for name, path, as_of in jobs:
        data = open(path, "rb").read()
        rep = nl_browser.run(data, os.path.basename(path), "", None, as_of)
        if not rep.get("ok"):
            sys.exit("%s: the adapter refused it: %s" % (name, rep.get("error")))
        # the profile as the worker answers the page's "profile" message, after the visitor's choices: the
        # scan's flags {column: kind} and those choices
        flags = {f["column"]: f["kind"] for f in rep["privacy"]["flagged"]}
        choices = PRIVATE_CHOICES if name == "orders-private" else {}
        got = json.loads(nl_browser.plan_profile_json(data, os.path.basename(path), json.dumps(flags), json.dumps(choices), as_of))
        with open(os.path.join(a.out, name + ".profile.json"), "w", encoding="utf-8") as f:
            json.dump(got["profile"], f)
        with open(os.path.join(a.out, name + ".landed.json"), "w", encoding="utf-8") as f:
            json.dump(got["landed"], f)
        if name == "orders-private":
            # the planned run: when the adapter cannot make it, its report says why and only the check that
            # reads it fails (the other reports stand)
            dec = dict(PRIVATE_CHOICES, __plan__=PRIVATE_PLAN)
            rep = nl_browser.run(data, os.path.basename(path), "", dec, as_of)
            if not rep.get("ok") or not (rep.get("downloads") or {}).get("contract_flagged_csv"):
                why = "the planned run gave no flagged-values download: %s" % rep.get("error")
                print("%-18s %s" % (name, why), file=sys.stderr)
                rep = {"__fixture_error": why}
        with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rep, f)
        if "__fixture_error" not in rep:
            print("%-18s %d charts, %d findings" % (name, len(rep["charts"]), len(rep["findings"])))
    # the charts an AI plan asks for (CHARTS_PLAN): a failure here leaves its reason in the file, never stops the others
    name, path = "sales-ledger-charts", os.path.join(a.out, "sales-ledger.csv")
    try:
        rep = nl_browser.run(open(path, "rb").read(), "sales-ledger.csv", "", {"__plan__": CHARTS_PLAN}, AS_OF)
        viz = (rep.get("viz") or {}) if rep.get("ok") else {}
        if not rep.get("ok"):
            rep = {"__fixture_error": "the planned run failed: %s" % rep.get("error")}
        elif not viz.get("charts"):
            rep = {"__fixture_error": "the planned run built no chart; refused: %s" % json.dumps(viz.get("refused"))[:400]}
    except Exception as e:                      # the engine's chart registry is new: say what broke, keep the rest
        rep = {"__fixture_error": "the planned run raised %s: %s" % (type(e).__name__, e)}
    with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(rep, f)
    if "__fixture_error" in rep:
        print("%-18s %s" % (name, rep["__fixture_error"]), file=sys.stderr)
    else:
        print("%-18s %d charts (%d from the chart registry, %d refused), %d findings" % (name, len(rep["charts"]), len(rep["viz"]["charts"]),
              len(rep["viz"]["refused"]), len(rep["findings"])))
    # the final evaluation (1 Oct 2026): a plan step that sets a sixth of the rows aside, a stale file, a kept text
    name = "reviews-plan-drop"
    try:
        rep = nl_browser.run(open(os.path.join(SITE, "tools", "fixtures", "eval", "reviews_synthetic.csv"), "rb").read(),
                             "reviews_synthetic.csv", "", {"__plan__": DROP_PLAN, "review_text": "keep"}, "2026-09-30")
        if not rep.get("ok"):
            rep = {"__fixture_error": "the planned run failed: %s" % rep.get("error")}
        elif not (rep.get("ai_plan") or {}).get("row_drops") or not rep["health"].get("explain"):
            rep = {"__fixture_error": "the run disclosed no row drop or no health explanation"}
    except Exception as e:
        rep = {"__fixture_error": "the planned run raised %s: %s" % (type(e).__name__, e)}
    with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(rep, f)
    print("%-18s %s" % (name, rep.get("__fixture_error") or "%d row drop(s)" % len(rep["ai_plan"]["row_drops"])),
          file=sys.stderr if "__fixture_error" in rep else sys.stdout)
    # a statistical table read by its structure (wave 4, track B): synthetic, in Statistics Canada's shape
    name = "official-cube"
    try:
        sys.path.insert(0, os.path.join(HERE, "fixtures", "structure"))
        import make_cubes as MC
        df = pd.read_csv(__import__("io").BytesIO(MC.partition(0.10)), dtype=str, keep_default_na=False)
        ix = lambda m: int(m[:4]) * 12 + int(m[5:7]) - 1 + 44            # the table's 48 months end in Aug 2026
        df["REF_DATE"] = df["REF_DATE"].map(lambda m: "%04d-%02d" % (ix(m) // 12, ix(m) % 12 + 1))
        data = df.to_csv(index=False, quoting=1).encode("utf-8")
        with open(os.path.join(a.out, name + ".csv"), "wb") as f:
            f.write(data)
        rep = nl_browser.run(data, name + ".csv", "", None, AS_OF)
        if not rep.get("ok"):
            rep = {"__fixture_error": "the run failed: %s" % rep.get("error")}
        elif not (rep.get("estimand") and (rep.get("forecast") or {}).get("audit") and rep["estimand"].get("inference")):
            rep = {"__fixture_error": "the run carries no estimand, audit or official-aggregate record: %s" % json.dumps(
                {k: bool(rep.get(k)) for k in ("estimand", "structure")})}
    except Exception as e:
        rep = {"__fixture_error": "the run raised %s: %s" % (type(e).__name__, e)}
    with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(rep, f)
    print("%-18s %s" % (name, rep.get("__fixture_error") or "%s; audit %s" % (rep["story"]["headline"], rep["forecast"]["audit"]["status"])),
          file=sys.stderr if "__fixture_error" in rep else sys.stdout)
    # wave 5 (generality): a table with NO total row and a quarterly table, both synthetic (make_cubes.py); the page says so
    for name, mk, need in (("wave5-no-total", lambda: MC.no_total(hide=[("Charlie", 38), ("Bravo", 34)]),
                            lambda r: (r.get("estimand") or {}).get("built_from")),
                           ("wave5-quarterly", lambda: MC.periodic("quarter", "iso", stock=True),
                            lambda r: ((r.get("estimand") or {}).get("period") or {}).get("kind") == "quarter")):
        try:
            data = mk()
            with open(os.path.join(a.out, name + ".csv"), "wb") as f:
                f.write(data)
            rep = nl_browser.run(data, name + ".csv", "", None, AS_OF)
            if not rep.get("ok"):
                rep = {"__fixture_error": "the run failed: %s" % rep.get("error")}
            elif not need(rep):
                rep = {"__fixture_error": "the run carries no built_from / period record: %s" % json.dumps(rep.get("estimand"))[:300]}
        except Exception as e:
            rep = {"__fixture_error": "the run raised %s: %s" % (type(e).__name__, e)}
        with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rep, f)
        print("%-18s %s" % (name, rep.get("__fixture_error") or rep["estimand"]["text"][:80]),
              file=sys.stderr if "__fixture_error" in rep else sys.stdout)
    # a header that is markup, in a chart the engine refuses (the chart review of 30 Sep 2026)
    name = "viz-hostile-header"
    write_hostile(a.out)
    try:
        rep = nl_browser.run(open(os.path.join(a.out, name + ".csv"), "rb").read(), name + ".csv", "",
                             {"__plan__": HOSTILE_PLAN}, AS_OF)
        if not rep.get("ok"):
            rep = {"__fixture_error": "the planned run failed: %s" % rep.get("error")}
        elif not any(HOSTILE in (x.get("columns") or []) for x in (rep.get("viz") or {}).get("refused") or []):
            rep = {"__fixture_error": "the engine did not refuse the chart on the header: %s" % json.dumps(rep["viz"])[:400]}
    except Exception as e:
        rep = {"__fixture_error": "the planned run raised %s: %s" % (type(e).__name__, e)}
    with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(rep, f)
    print("%-18s %s" % (name, rep.get("__fixture_error") or "%d refused" % len(rep["viz"]["refused"])),
          file=sys.stderr if "__fixture_error" in rep else sys.stdout)


if __name__ == "__main__":
    main()
