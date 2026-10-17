#!/usr/bin/env python3
"""Write engine/sample-sales-synthetic.csv: a SYNTHETIC sales ledger for the "Ask with SQL" demo (Track B,
plan/DASHBOARD-DESIGN.md).

    python tools/make_sample_sales.py           # (re)write engine/sample-sales-synthetic.csv
    python tools/make_sample_sales.py --check   # exit 1 if the file on disk differs

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the file on disk differs")
    a = ap.parse_args()
    t = text()
    if a.check:
        try:
            with open(OUT, encoding="utf-8") as f:
                same = f.read() == t
        except OSError:
            same = False
        print("sample-sales-synthetic.csv: %s" % ("up to date" if same else "DIFFERS (run tools/make_sample_sales.py)"))
        return 0 if same else 1
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write(t)
    print("wrote %s: %d rows, %d bytes (SYNTHETIC)" % (os.path.relpath(OUT), t.count("\n") - 1, len(t.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
