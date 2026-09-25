#!/usr/bin/env python3
"""Write engine/sample-messy.csv: a SYNTHETIC, deliberately messy ledger for the in-browser demo.

    python tools/make_sample_messy.py           # (re)write engine/sample-messy.csv
    python tools/make_sample_messy.py --check   # exit 1 if the file on disk differs

The business is invented: a small Toronto operator with five rental buildings, logging
rent received and maintenance spending in one spreadsheet, January 2022 to August 2026.
Every property, vendor and note is made up. There are no people in it: no tenant names,
no emails, no phone numbers.

The mess is the kind a real export carries, planted on purpose so the engine has something
to find:
  * four date spellings (ISO, day-first with slashes, "Mar 15, 2024", 2024/03/15)
  * stray whitespace and inconsistent casing in the text columns
  * amounts typed as "$1,850.00", placeholders ("N/A", "-", "TBD", blanks)
  * yes/no written six ways
  * exact duplicate rows (entered twice)
  * impossible values: negative amounts, dates in 2027, a 30th of February

Deterministic: a fixed seed and no clock, so the same file comes out every time.
Standard library only.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import io
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "engine", "sample-messy.csv")
SEED = 20260923
FIRST = (2022, 1)
LAST = (2026, 8)          # the last complete month; the demo analyses it as of 2026-09-15

# Five invented buildings. Two are bought part-way (July 2024, March 2025), so volume steps up.
PROPERTIES = [
    {"name": "Danforth Triplex", "units": ["Main", "Upper", "Lower"], "rent": [2150, 2400, 1650],
     "from": (2022, 1)},
    {"name": "Leslieville Fourplex", "units": ["Unit 1", "Unit 2", "Unit 3", "Unit 4"],
     "rent": [1900, 1950, 2050, 2250], "from": (2022, 1)},
    {"name": "Parkdale Walk-Up", "units": ["Unit %d" % i for i in range(1, 13)],
     "rent": [1550 + 45 * i for i in range(12)], "from": (2022, 1)},
    {"name": "Junction Six-Plex", "units": ["Unit 1", "Unit 2", "Unit 3", "Unit 4", "Unit 5", "Unit 6"],
     "rent": [1750, 1800, 1850, 1900, 2000, 2100], "from": (2024, 7)},
    {"name": "East York Low-Rise", "units": ["Unit %d" % i for i in range(101, 119)],
     "rent": [1850 + 30 * (i % 6) for i in range(18)], "from": (2025, 3)},
]

# (category, vendor, typical cost, notes, months it is most common in)
JOBS = [
    ("Plumbing", "Beaches Plumbing Co.", 320, ["Replaced kitchen faucet cartridge", "Cleared bathroom drain",
                                              "Fixed running toilet", "Replaced shut-off valve"], None),
    ("Electrical", "Leslieville Electric Ltd", 410, ["Replaced GFCI outlet", "Swapped hallway light fixture",
                                                    "Breaker tripping, traced to dryer"], None),
    ("Heating", "Don Valley HVAC", 540, ["Furnace service", "Replaced furnace igniter",
                                        "Boiler pressure reset", "Bled radiators"], (10, 11, 12, 1, 2, 3)),
    ("Cooling", "Don Valley HVAC", 380, ["AC not cooling, recharged", "Window unit replaced"], (6, 7, 8)),
    ("Snow Removal", "Snowline Property Services", 180, ["Walkway and steps cleared", "Salted front steps"],
     (12, 1, 2, 3)),
    ("Pest Control", "Queen West Pest Control", 260, ["Mice treatment, basement", "Follow-up treatment"], None),
    ("Appliance", "Riverdale Appliance Repair", 290, ["Fridge not cooling", "Dryer belt replaced",
                                                     "Stove element replaced"], None),
    ("Cleaning", "Greenwood Cleaning", 150, ["Common area clean", "Turnover clean"], None),
    ("Landscaping", "Greenwood Cleaning", 220, ["Lawn cut and edging", "Leaf cleanup"], (5, 6, 7, 8, 9, 10)),
]

HEADER = ["Date", "Property", "Unit", "Category", "Amount", "Paid?", "Vendor", "Notes"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def spell_date(rng: random.Random, d: dt.date) -> str:
    r = rng.random()
    if r < 0.70:
        s = d.isoformat()
    elif r < 0.85:
        s = "%02d/%02d/%04d" % (d.day, d.month, d.year)          # day-first, as Canadian exports are
    elif r < 0.95:
        s = "%s %d, %d" % (MONTHS[d.month - 1], d.day, d.year)
    else:
        s = "%04d/%02d/%02d" % (d.year, d.month, d.day)
    return pad(rng, s, 0.03)


def pad(rng: random.Random, s: str, p: float) -> str:
    r = rng.random()
    if r < p / 2:
        return " " + s
    if r < p:
        return s + "  "
    return s


def recase(rng: random.Random, s: str, p: float) -> str:
    r = rng.random()
    if r < p / 3:
        return s.upper()
    if r < 2 * p / 3:
        return s.lower()
    if r < p:
        return " " + s + " "
    return s


def spell_amount(rng: random.Random, v: float) -> str:
    r = rng.random()
    if r < 0.012:
        return rng.choice(["N/A", "-", "", "n/a"])
    if r < 0.016:
        return "TBD"                                             # not a number: set aside
    if r < 0.10:
        return "${:,.2f}".format(v)
    return ("%.2f" % v) if rng.random() < 0.5 else ("%d" % round(v) if v == round(v) else "%.2f" % v)


def spell_paid(rng: random.Random, paid: bool) -> str:
    r = rng.random()
    if r < 0.70:
        return "Y" if paid else "N"
    if r < 0.85:
        return "Yes" if paid else "No"
    if r < 0.93:
        return "yes" if paid else "no"
    return "TRUE" if paid else "FALSE"


def build() -> str:
    rng = random.Random(SEED)
    rows = []
    for (y, m) in months():
        idx = (y - 2022) * 12 + (m - 1)
        for p in PROPERTIES:
            if (y, m) < p["from"]:
                continue
            for unit, base in zip(p["units"], p["rent"]):
                if rng.random() < 0.03:
                    continue                                     # a vacant month
                raise_steps = max(0, (y - 2022) - (0 if m >= 2 else 1))
                rent = round(base * (1.025 ** raise_steps), 0)
                day = rng.choice([1, 1, 1, 2, 3, 5])
                late = rng.random() < 0.06
                rows.append([spell_date(rng, dt.date(y, m, day)), recase(rng, p["name"], 0.08),
                             unit, recase(rng, "Rent", 0.05), spell_amount(rng, rent),
                             spell_paid(rng, not late), "", ""])
        # maintenance: a base load that grows with the portfolio, plus seasonal jobs
        n_units = sum(len(p["units"]) for p in PROPERTIES if (y, m) >= p["from"])
        base_jobs = int(round(n_units * (1.2 + 0.008 * idx)))
        for _ in range(base_jobs + rng.randint(-2, 3)):
            cat, vendor, cost, notes, season = rng.choice(JOBS)
            if season and m not in season and rng.random() < 0.8:
                continue
            p = rng.choice([q for q in PROPERTIES if (y, m) >= q["from"]])
            unit = rng.choice(p["units"] + ["Common area"])
            v = max(40.0, round(rng.gauss(cost, cost * 0.35), 2))
            day = rng.randint(1, calendar.monthrange(y, m)[1])
            rows.append([spell_date(rng, dt.date(y, m, day)), recase(rng, p["name"], 0.08), unit,
                         recase(rng, cat, 0.10), spell_amount(rng, v), spell_paid(rng, rng.random() > 0.12),
                         pad(rng, vendor, 0.05), pad(rng, rng.choice(notes), 0.04)])

    # impossible values
    for i in rng.sample(range(len(rows)), 6):
        rows[i][4] = "-%s" % rows[i][4].lstrip("$-") if rows[i][4] not in ("", "N/A", "-", "n/a", "TBD") \
            else "-250.00"
    for i in rng.sample(range(len(rows)), 3):
        rows[i][0] = "2027-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28))   # typed a year ahead
    rows[rng.randrange(len(rows))][0] = "2025-02-30"                             # no such day

    # rows entered twice (a re-sent receipt, a double paste), scattered through the file
    for _ in range(55):
        i = rng.randrange(len(rows))
        rows.insert(i + 1, list(rows[i]))

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    w.writerows(rows)
    return buf.getvalue()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the file on disk differs")
    a = ap.parse_args(argv)
    text = build()
    if a.check:
        try:
            with open(OUT, encoding="utf-8", newline="") as fh:
                same = fh.read() == text
        except FileNotFoundError:
            same = False
        print("sample-messy.csv %s" % ("matches its generator" if same else "DIFFERS from its generator"))
        return 0 if same else 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    n = text.count("\n") - 1
    print("wrote %s: %d rows, %d bytes" % (os.path.relpath(OUT), n, len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
