#!/usr/bin/env python3
"""Write the monthly reporting pack demo's two extra files (Track E, plan/DASHBOARD-DESIGN.md), beside the SYNTHETIC
sales ledger they go with (tools/make_sample_sales.py, engine/sample-sales-synthetic.csv):

    engine/sample-pack-budget-synthetic.csv   the plan: month, region, scenario (Budget for every month of 2026;
                                              Forecast, the July reforecast, for July to December), revenue, cost,
                                              units; a scenario column, so the pack must find it
    engine/sample-pack-kpi-synthetic.csv      a KPI extract, one row per month (24 months to September 2026), each
                                              KPI beside its target (paired columns: active_customers and
                                              active_customers_target ...); flows, levels, a rate and a score, one
                                              missing month and one KPI without a target

    python tools/make_sample_pack.py           # (re)write both files
    python tools/make_sample_pack.py --check   # exit 1 if a file on disk differs

SYNTHETIC: every figure is invented by this script (the budget is the sales ledger's own prior-year month, region by
region, grown by a fixed rate; the KPIs come from a seeded random walk). No company, customer or person is in them.
The file names say so, and so does the page's sample button.

The pack's planted facts for September 2026 (tools/test_nl_pack.py recomputes every figure from these files with
pandas, never from the engine): revenue about 12% below budget, with EMEA (whose budget is ambitious, and whose Q3 has
the ledger's planted shock) carrying more than the whole shortfall; the churn rate up 1.2 percentage points in the
month (2.2 to 3.4) and 1.4 points over its target; NPS down 7 points; support tickets up about 32%, with no target;
headcount flat (a count at a date: never summed over months); NPS has no July 2026 value (a gap the pack leaves as a
gap). Deterministic: a fixed seed and no clock. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_sample_sales as MS  # noqa: E402  (the ledger the budget is drawn from: one source of the synthetic company)

ENGINE = os.path.join(HERE, "..", "engine")
BUDGET = os.path.join(ENGINE, "sample-pack-budget-synthetic.csv")
KPI = os.path.join(ENGINE, "sample-pack-kpi-synthetic.csv")
SEED = 20261017
GROWTH = {"EMEA": 0.45, "Americas": 0.06, "APAC": 0.10}    # the budget: last year's month grown by this (EMEA's
#                                                            ambitious: the month falls short of it)
FORECAST_CUT = {"EMEA": 0.90, "Americas": 1.00, "APAC": 1.04}  # the July reforecast against the budget
KPI_MONTHS = ["%04d-%02d" % (y, m) for y in (2024, 2025, 2026) for m in range(1, 13)
              if "2024-10" <= "%04d-%02d" % (y, m) <= "2026-09"]


def _ledger_by_month():
    """{(YYYY-MM, region): [revenue, cost, units]} summed from the synthetic ledger's own rows."""
    out = {}
    for r in MS.rows():
        k = (r[1][:7], r[2])
        a = out.setdefault(k, [0.0, 0.0, 0])
        a[0] += float(r[7])
        a[1] += float(r[8])
        a[2] += int(r[6])
    return out


def budget_text() -> str:
    led = _ledger_by_month()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["month", "region", "scenario", "revenue", "cost", "units"])
    for m in range(1, 13):
        month = "2026-%02d" % m
        prior = "2025-%02d" % m
        for region in ("EMEA", "Americas", "APAC"):
            rev, cost, units = led[(prior, region)]
            g = 1 + GROWTH[region]
            b = [round(rev * g, -2), round(cost * g, -2), int(round(units * g, -1))]
            w.writerow([month, region, "Budget", "%.0f" % b[0], "%.0f" % b[1], b[2]])
            if m >= 7:
                f = FORECAST_CUT[region]
                w.writerow([month, region, "Forecast", "%.0f" % round(b[0] * f, -2), "%.0f" % round(b[1] * f, -2),
                            int(round(b[2] * f, -1))])
    return buf.getvalue()


def kpi_text() -> str:
    rng = random.Random(SEED)
    rows = []
    active, head = 1840.0, 62
    for i, month in enumerate(KPI_MONTHS):
        last = month == "2026-09"
        new = int(round(rng.gauss(40, 4)))
        churn = round(rng.gauss(2.1, 0.12), 1) if not last else 3.4      # percent of active customers
        active = round(active * (1 - churn / 100) + new)
        nps = int(round(rng.gauss(41, 1.5))) if not last else 33
        tickets = int(round(rng.gauss(400, 18))) if not last else 545
        if month in ("2025-04", "2026-03"):
            head += 2
        rows.append([month, int(active), 1850, new, 40, "%.1f" % churn, "2.0", "" if month == "2026-07" else nps, 40,
                     head, 66, tickets])                                 # no target for support tickets
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["month", "active_customers", "active_customers_target", "new_customers", "new_customers_target",
                "churn_rate_pct", "churn_rate_pct_target", "nps", "nps_target", "headcount", "headcount_target",
                "support_tickets"])
    w.writerows(rows)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a file on disk differs")
    a = ap.parse_args()
    bad = 0
    for path, t in ((BUDGET, budget_text()), (KPI, kpi_text())):
        if a.check:
            try:
                with open(path, encoding="utf-8") as f:
                    same = f.read() == t
            except OSError:
                same = False
            print("%s: %s" % (os.path.basename(path), "up to date" if same else "DIFFERS (run tools/make_sample_pack.py)"))
            bad += 0 if same else 1
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(t)
        print("wrote %s: %d rows (SYNTHETIC)" % (os.path.relpath(path), t.count("\n") - 1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
