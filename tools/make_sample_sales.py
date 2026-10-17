#!/usr/bin/env python3
"""Write engine/sample-sales-synthetic.csv: a SYNTHETIC sales ledger for the "Ask with SQL" demo (Track B,
plan/DASHBOARD-DESIGN.md), and engine/sample-crm-deals-synthetic.csv: the same invented company's CRM deals export for
the quarterly sales report (Track F; see deal_rows below).

    python tools/make_sample_sales.py           # (re)write both files
    python tools/make_sample_sales.py --check   # exit 1 if a file on disk differs

SYNTHETIC: every order, country mix, price and cost is invented by this script. No company, customer or
person is in it. The file name says so, the page's button says so, and so does this docstring.

What it holds: one row per order, 1 October 2023 to 30 September 2026 (three years, twelve quarters):
order_id, order_date, region (EMEA, Americas, APAC), country, product_line, channel, units, revenue, cost.
revenue and cost are flows (money, summed); gross margin = (revenue - cost) / revenue, recomputed from sums.

The planted answer (the demo's question: "Why is gross margin down in EMEA this quarter?"). In the latest
quarter, 2026 Q3, and only in EMEA:
  1. MIX: in Germany and France the orders swing to Accessories, the lowest-margin product line (about 15%
     margin against 40% to 75% for the others), in larger bulk orders, so Accessories' share of EMEA revenue
     jumps;
  2. DISCOUNT: EMEA's Online channel sells at a 12% price cut (revenue down, cost unchanged), so Online's own
     margin falls (a rate effect).
Everything else is stable with noise (prices +-5%, costs +-3%) and a mild seasonal swing in order counts.
tools/test_nl_sql.py checks the engine's explanation finds both causes, with magnitudes it recomputes
independently with pandas.

Deterministic: a fixed seed and no clock. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "engine", "sample-sales-synthetic.csv")
SEED = 20261016
FIRST = dt.date(2023, 10, 1)
LAST = dt.date(2026, 9, 30)
SHOCK_FROM = dt.date(2026, 7, 1)          # the latest quarter, 2026 Q3: where the planted causes live
ORDERS_PER_DAY = 5.0

REGIONS = {
    "EMEA": (0.36, {"Germany": 0.28, "France": 0.24, "United Kingdom": 0.24, "Spain": 0.12, "Italy": 0.12}),
    "Americas": (0.40, {"United States": 0.55, "Canada": 0.2, "Brazil": 0.15, "Mexico": 0.1}),
    "APAC": (0.24, {"Japan": 0.35, "Australia": 0.3, "India": 0.2, "Singapore": 0.15}),
}
# product line: (order weight, list unit price, unit cost as a share of list price, units low, units high)
LINES = {
    "Hardware": (0.33, 420.0, 0.62, 1, 12),
    "Software": (0.25, 180.0, 0.25, 1, 40),
    "Services": (0.20, 950.0, 0.60, 1, 5),
    "Accessories": (0.22, 35.0, 0.85, 5, 80),
}
# channel: (order weight, price discount always given)
CHANNELS = {"Direct": (0.40, 0.0), "Online": (0.35, 0.03), "Partner": (0.25, 0.08)}
MIX_COUNTRIES = ("Germany", "France")      # planted cause 1
MIX_ACCESSORIES_WEIGHT = 0.50              # Accessories' order weight there in the shock quarter (0.22 before)
MIX_ACCESSORIES_UNITS = (40, 160)          # bulk orders
ONLINE_DISCOUNT = 0.12                     # planted cause 2: EMEA Online, the shock quarter


def pick(rng: random.Random, weights: dict) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] if not isinstance(weights[k], tuple) else weights[k][0] for k in keys])[0]


def rows() -> list:
    rng = random.Random(SEED)
    out = []
    n = 0
    day = FIRST
    while day <= LAST:
        season = 1.0 + 0.15 * math.sin(2 * math.pi * (day.timetuple().tm_yday / 365.25))
        k = max(0, int(round(rng.gauss(ORDERS_PER_DAY * season, 1.6))))
        for _ in range(k):
            region = pick(rng, REGIONS)
            country = pick(rng, REGIONS[region][1])
            channel = pick(rng, CHANNELS)
            shock = day >= SHOCK_FROM and region == "EMEA"
            lw = {k_: v[0] for k_, v in LINES.items()}
            if shock and country in MIX_COUNTRIES:
                rest = 1.0 - MIX_ACCESSORIES_WEIGHT
                base = sum(v for k_, v in lw.items() if k_ != "Accessories")
                lw = {k_: (MIX_ACCESSORIES_WEIGHT if k_ == "Accessories" else v / base * rest) for k_, v in lw.items()}
            line = pick(rng, lw)
            _w, price, cost_share, lo, hi = LINES[line]
            if shock and country in MIX_COUNTRIES and line == "Accessories":
                lo, hi = MIX_ACCESSORIES_UNITS
            units = rng.randint(lo, hi)
            unit_price = price * (1 + rng.uniform(-0.05, 0.05))
            unit_cost = price * cost_share * (1 + rng.uniform(-0.03, 0.03))
            disc = CHANNELS[channel][1]
            if shock and channel == "Online":
                disc += ONLINE_DISCOUNT
            revenue = round(units * unit_price * (1 - disc), 2)
            cost = round(units * unit_cost, 2)
            n += 1
            out.append(["SO-%06d" % n, day.isoformat(), region, country, line, channel, units,
                        "%.2f" % revenue, "%.2f" % cost])
        day += dt.timedelta(days=1)
    return out


def text() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["order_id", "order_date", "region", "country", "product_line", "channel", "units", "revenue", "cost"])
    w.writerows(rows())
    return buf.getvalue()


# ----------------------------------------------------------------------------------------------- the CRM deals export
# Track F (plan/DASHBOARD-DESIGN.md, "Track F: quarterly sales report from a CRM export"): the same invented company's
# deals, as a CRM's opportunity report exports them (Salesforce's own column names, so the engine has to map them).
# SYNTHETIC: every deal, amount, date and rep name is invented by this script; the names are made up from common first
# and last names and belong to no one.
#
# One row per deal (opportunity). The export is taken on 5 January 2026 (DEALS_AS_OF): deals closed (won or lost) in
# 2025 Q3 and 2025 Q4, a few closed in the first days of 2026 Q1 (an incomplete quarter, never the one reported), and
# the open pipeline on the export date (stage, probability, expected close date, last stage change).
#
# The planted facts the report must find (tools/test_nl_sales.py checks each against pandas):
#   1. GROWTH FROM BIGGER DEALS: won revenue grows from Q3 to Q4 while the number of won deals falls: the average deal
#      size carries all of the growth, and most of it is Enterprise, whose average won deal grows by about 40%;
#   2. SMB WIN RATE DOWN: SMB wins about 38% of the deals it closes in Q3 and about 22% in Q4 (more lost);
#   3. CYCLES LENGTHENING: the median days from created to close of won deals rise from about 45 to about 60;
#   4. STALLED PIPELINE: about a quarter of the open deals is past its expected close date, or has had no stage change
#      for more than 30 days.
DEALS_OUT = os.path.join(HERE, "..", "engine", "sample-crm-deals-synthetic.csv")
DEALS_SEED = 20261017
DEALS_AS_OF = dt.date(2026, 1, 5)
DEAL_HEADER = ["Opportunity ID", "Account Segment", "Opportunity Owner", "Stage", "Amount", "Probability (%)",
               "Created Date", "Close Date", "Last Stage Change Date"]
REPS = {  # rep: weight per segment (Enterprise, Mid-Market, SMB)
    "Avery Lindqvist": (5, 1, 0), "Jordan Okafor": (4, 2, 0), "Priya Raman": (3, 3, 1), "Mateo Alvarez": (1, 4, 1),
    "Sofia Brennan": (0, 4, 2), "Noah Whitaker": (0, 2, 4), "Lena Kowalski": (0, 1, 5), "Caleb Mensah": (0, 0, 5),
}
SEGMENTS = ("Enterprise", "Mid-Market", "SMB")
# (quarter start, segment): (won, lost, mean won amount, median cycle days of won deals)
DEAL_PLAN = {
    ("2025Q3", "Enterprise"): (16, 22, 90000.0, 70), ("2025Q4", "Enterprise"): (16, 21, 127000.0, 92),
    ("2025Q3", "Mid-Market"): (30, 52, 28000.0, 44), ("2025Q4", "Mid-Market"): (29, 51, 28500.0, 60),
    ("2025Q3", "SMB"): (60, 98, 6000.0, 30), ("2025Q4", "SMB"): (44, 156, 6100.0, 41),
}
QUARTER_START = {"2025Q3": dt.date(2025, 7, 1), "2025Q4": dt.date(2025, 10, 1)}
# the open pipeline on the export date: stage, probability, count per segment (Enterprise, Mid-Market, SMB)
OPEN_STAGES = (("Prospecting", 10, (6, 9, 14)), ("Qualification", 20, (5, 8, 11)), ("Proposal/Price Quote", 50, (4, 7, 8)),
               ("Negotiation/Review", 75, (3, 5, 5)))
EARLY_Q1 = (("Mid-Market", "Closed Won"), ("SMB", "Closed Won"), ("SMB", "Closed Lost"))   # closed 2 to 5 January


def _rep(rng: random.Random, seg_i: int) -> str:
    names = [r for r in REPS if REPS[r][seg_i]]
    return rng.choices(names, weights=[REPS[r][seg_i] for r in names])[0]


def _amount(rng: random.Random, mean: float) -> float:
    return round(max(mean * 0.15, rng.gauss(mean, mean * 0.25)) / 50.0) * 50.0


def deal_rows() -> list:
    rng = random.Random(DEALS_SEED)
    deals = []
    for (q, seg), (won, lost, mean, cyc) in sorted(DEAL_PLAN.items()):
        q0, si = QUARTER_START[q], SEGMENTS.index(seg)
        for k in range(won + lost):
            is_won = k < won
            close = q0 + dt.timedelta(days=rng.randrange(92))
            days = max(7, int(round(rng.gauss(cyc, cyc * 0.2)))) if is_won else max(5, int(round(rng.gauss(cyc * 0.8, cyc * 0.3))))
            created = close - dt.timedelta(days=days)
            amt = _amount(rng, mean if is_won else mean * 0.9)
            deals.append([seg, _rep(rng, si), "Closed Won" if is_won else "Closed Lost", amt, 100 if is_won else 0,
                          created, close, close])
    for seg, stage in EARLY_Q1:
        si = SEGMENTS.index(seg)
        close = DEALS_AS_OF - dt.timedelta(days=rng.randrange(1, 4))
        created = close - dt.timedelta(days=rng.randrange(30, 70))
        won = stage == "Closed Won"
        deals.append([seg, _rep(rng, si), stage, _amount(rng, (28000.0, 6000.0)[si - 1]), 100 if won else 0,
                      created, close, close])
    for stage, prob, counts in OPEN_STAGES:
        for si, n in enumerate(counts):
            seg = SEGMENTS[si]
            mean = (110000.0, 30000.0, 6000.0)[si]
            for _ in range(n):
                created = DEALS_AS_OF - dt.timedelta(days=rng.randrange(8, 160))
                # most open deals moved stage in the last three weeks; about one in six has not for 35 to 90 days
                quiet = rng.random() < 0.17
                changed = max(created, DEALS_AS_OF - dt.timedelta(days=rng.randrange(35, 90) if quiet else rng.randrange(1, 21)))
                # about one open deal in seven is past its expected close date (still open on the export date)
                if rng.random() < 0.14:
                    close = DEALS_AS_OF - dt.timedelta(days=rng.randrange(5, 50))
                else:
                    close = DEALS_AS_OF + dt.timedelta(days=rng.randrange(10, 150))
                deals.append([seg, _rep(rng, si), stage, _amount(rng, mean), prob, created, close, changed])
    deals.sort(key=lambda d: (d[5], d[6], d[0], d[1]))
    out = []
    for i, (seg, rep, stage, amt, prob, created, close, changed) in enumerate(deals, 1):
        out.append(["006-%05d" % (40000 + i), seg, rep, stage, "%.2f" % amt, str(prob), created.isoformat(),
                    close.isoformat(), changed.isoformat()])
    return out


def deals_text() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(DEAL_HEADER)
    w.writerows(deal_rows())
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the file on disk differs")
    a = ap.parse_args()
    bad = 0
    for out, t in ((OUT, text()), (DEALS_OUT, deals_text())):
        if a.check:
            try:
                with open(out, encoding="utf-8") as f:
                    same = f.read() == t
            except OSError:
                same = False
            print("%s: %s" % (os.path.basename(out), "up to date" if same else "DIFFERS (run tools/make_sample_sales.py)"))
            bad += 0 if same else 1
            continue
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(t)
        print("wrote %s: %d rows, %d bytes (SYNTHETIC)" % (os.path.relpath(out), t.count("\n") - 1, len(t.encode("utf-8"))))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
