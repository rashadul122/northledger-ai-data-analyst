#!/usr/bin/env python3
"""Fixtures for the report checks in tools/check_ui.js: four INVENTED business files, each run through
the browser adapter (engine/nl_browser.py) natively, plus the site's own sample.

    python tools/make_ui_fixtures.py --out DIR      # writes DIR/<name>.csv and DIR/<name>.json

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
    for name, path, as_of in jobs:
        rep = nl_browser.run(open(path, "rb").read(), os.path.basename(path), "", None, as_of)
        if not rep.get("ok"):
            sys.exit("%s: the adapter refused it: %s" % (name, rep.get("error")))
        with open(os.path.join(a.out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rep, f)
        print("%-18s %d charts, %d findings" % (name, len(rep["charts"]), len(rep["findings"])))


if __name__ == "__main__":
    main()
