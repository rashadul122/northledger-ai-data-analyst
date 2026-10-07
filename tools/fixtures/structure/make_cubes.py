#!/usr/bin/env python3
"""
Synthetic statistical cubes for tools/test_nl_structure.py (WAVE 4, track A1; the design's M2: every unit-test cube is
synthetic, none is an official table). Each function returns the CSV bytes of one cube, built from a fixed seed, so the
tests read the same bytes every run:

    import make_cubes as MC
    data = MC.partition()                    # Total + 5 regions that add up exactly

    python tools/fixtures/structure/make_cubes.py OUTDIR    # writes every cube as OUTDIR/<name>.csv

Values are whole thousands of dollars (DECIMALS 0, SCALAR_FACTOR thousands) unless a cube says otherwise. A total is the
exact sum of its parts as published; a suppressed cell is blank with its flag.
"""
from __future__ import annotations

import csv
import io
import os
import sys

import numpy as np

MONTHS = ["%04d-%02d" % (2019 + i // 12, i % 12 + 1) for i in range(48)]          # Jan 2019 - Dec 2022
SEASON = np.array([0.82, 0.80, 0.95, 0.98, 1.04, 1.06, 1.05, 1.07, 1.00, 1.02, 1.08, 1.30])


def _csv(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _series(rng, level: float, n: int = len(MONTHS), growth: float = 0.003, seasonal: bool = True) -> np.ndarray:
    t = np.arange(n)
    s = SEASON[t % 12] if seasonal else np.ones(n)
    noise = 1.0 + 0.02 * rng.standard_normal(n)
    return np.round(level * (1.0 + growth) ** t * s * noise)


_META_HEAD = ["REF_DATE", "GEO", "DGUID"]


def _official(header_dims, records, uom="Dollars", scalar="thousands", decimals="0", status=None):
    """records: [(month, geo, dims tuple, value or None)] -> a StatCan-shaped CSV."""
    head = _META_HEAD + list(header_dims) + ["UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID", "VECTOR", "VALUE",
                                              "STATUS", "SYMBOL", "TERMINATED", "DECIMALS"]
    vec, rows = {}, []
    geos = {}
    for (mo, geo, dims, val, *rest) in records:
        key = (geo,) + tuple(dims)
        vec.setdefault(key, "v%d" % (100000 + len(vec)))
        geos.setdefault(geo, "2021A%09d" % (len(geos) + 11))
        st = rest[0] if rest else ""
        u = uom(key) if callable(uom) else uom
        rows.append([mo, geo, geos[geo]] + list(dims) + [u, "81", scalar, "3" if scalar == "thousands" else "0",
                                                         vec[key], "" if val is None else "%d" % val
                     if float(val) == int(val) else "%.*f" % (max(1, int(decimals)), val), st, "", "", decimals])
    return _csv(head, rows)


def partition(blank_share: float = 0.0, seed: int = 1, rounding_gap: int = 0) -> bytes:
    """Test 1 (and 2 with blank_share 0.10): Total and 5 regions; the Total is the exact sum of the regions as published;
    with blank_share, that share of the region cells is suppressed ("x", blank value), the Total still whole. With
    `rounding_gap` (wave 5c), the Total of each of the latest 12 months is that many thousands above its parts' sum: the
    publisher's rounding, a real residual of rounding_gap x 12 x 1,000 dollars."""
    rng = np.random.RandomState(seed)
    regions = ["North", "South", "East", "West", "Centre"]
    vals = {r: _series(rng, lv) for r, lv in zip(regions, (5200, 8100, 3300, 6100, 2400))}
    total = sum(vals.values())
    hide = rng.rand(len(regions), len(MONTHS)) < blank_share
    rec = []
    for i, mo in enumerate(MONTHS):
        rec.append((mo, "Total", ("Retail sales",), total[i] + (rounding_gap if i >= 36 else 0), "A"))
        for k, r in enumerate(regions):
            if hide[k, i]:
                rec.append((mo, r, ("Retail sales",), None, "x"))
            else:
                rec.append((mo, r, ("Retail sales",), vals[r][i], "A"))
    return _official(["Sales"], rec)


def partition_suppressed_sum(seed: int = 1, blank_share: float = 0.10) -> float:
    """The suppressed cells' true sum in the latest 12 months less the 12 before, in base units (what the
    unallocated part of test 2 must equal)."""
    rng = np.random.RandomState(seed)
    regions = ["North", "South", "East", "West", "Centre"]
    vals = {r: _series(rng, lv) for r, lv in zip(regions, (5200, 8100, 3300, 6100, 2400))}
    hide = rng.rand(len(regions), len(MONTHS)) < blank_share
    lat = sum(vals[r][i] for k, r in enumerate(regions) for i in range(36, 48) if hide[k, i])
    pri = sum(vals[r][i] for k, r in enumerate(regions) for i in range(24, 36) if hide[k, i])
    return 1000.0 * (lat - pri), int(hide.sum())


# a 3-level hierarchy, a single-child component and an "excluding" member
_TREE = {"Total retail [1-3]": ["Food stores [1]", "Home stores [2]", "Fuel stations [3]"],
         "Food stores [1]": ["Grocery [11]", "Specialty food [12]"],
         "Grocery [11]": ["Supermarkets [111]", "Convenience [112]"],
         "Home stores [2]": ["Furniture [21]", "Electronics [22]"]}
_LEAVES = {"Supermarkets [111]": 1800, "Convenience [112]": 1200, "Specialty food [12]": 2000,
           "Furniture [21]": 1000, "Electronics [22]": 2500, "Fuel stations [3]": 1500}
COMPONENT = ("Online electronics [2299]", "Electronics [22]")
EXCLUDING = ("Total retail excluding food", "Total retail [1-3]")


def _tree_values(rng):
    vals = {k: _series(rng, v) for k, v in _LEAVES.items()}

    def get(m):
        if m not in vals:
            vals[m] = sum(get(c) for c in _TREE[m])
        return vals[m]
    get("Total retail [1-3]")
    vals[COMPONENT[0]] = np.round(vals[COMPONENT[1]] * 0.85)
    vals[EXCLUDING[0]] = vals["Total retail [1-3]"] - vals["Food stores [1]"]
    return vals


def hierarchy(codes: bool = True, shuffle: bool = False, seed: int = 3) -> bytes:
    """Test 3 (codes) and test 4 (codes stripped, members in a shuffled order): Total > 3 store types > ... (3 levels),
    "Online electronics" a component of Electronics (inside it, never a part), and "Total retail excluding food" (an
    alternative total). Two regions, so every family is checked in more than one context."""
    rng = np.random.RandomState(seed)
    members = list(_tree_values(np.random.RandomState(seed)).keys())
    order = list(members)
    if shuffle:
        np.random.RandomState(seed + 100).shuffle(order)
    rec = []
    for geo, f in (("Canada", 1.0), ("Ontario", 0.4)):
        vals = _tree_values(np.random.RandomState(seed + (0 if geo == "Canada" else 7)))
        for i, mo in enumerate(MONTHS):
            for m in order:
                lb = m if codes else strip_code(m)
                rec.append((mo, geo, (lb,), vals[m][i], "A"))
    # Canada is not the sum of one region here: only the NAICS-like column is checked
    return _official(["Industry"], rec)


def strip_code(label: str) -> str:
    return label[:label.rindex("[")].strip() if "[" in label else label


def adjusted(seed: int = 5) -> bytes:
    """Test 5: an unadjusted copy and a seasonally adjusted copy labelled only "A" and "B"; each region is adjusted on its
    own, so the adjusted regions do not add up to the adjusted total."""
    rng = np.random.RandomState(seed)
    regions = ["R1", "R2", "R3"]
    nsa = {r: _series(rng, lv) for r, lv in zip(regions, (4000, 2500, 1500))}
    nsa["All regions"] = sum(nsa[r] for r in regions)
    t = np.arange(len(MONTHS))
    sa = {}
    for k, r in enumerate(["All regions"] + regions):
        fac = SEASON[t % 12] * (1.0 + 0.03 * (k + 1) * np.sin(t))       # each adjusted with its own factors
        sa[r] = np.round(nsa[r] / fac)
    rec = []
    for i, mo in enumerate(MONTHS):
        for r in ["All regions"] + regions:
            rec.append((mo, r, ("A",), nsa[r][i], ""))
            rec.append((mo, r, ("B",), sa[r][i], ""))
    return _official(["Type"], rec)


def adjusted_additive(seed: int = 6) -> bytes:
    """A headline with its seasonally adjusted copy, the way a statistical office publishes it: 6 regions and their total,
    each as "Unadjusted" and "Seasonally adjusted" (the same factors for every region, so the adjusted regions add up to
    the adjusted total exactly). Used by the structure charts' tests (a month-on-month calendar needs 5 or more parts)."""
    rng = np.random.RandomState(seed)
    regions = ["North", "South", "East", "West", "Central", "Islands"]
    nsa = {r: _series(rng, lv, growth=0.004) for r, lv in zip(regions, (3000, 2600, 2100, 1700, 1300, 700))}
    nsa["All regions"] = sum(nsa[r] for r in regions)
    t = np.arange(len(MONTHS))
    factor = SEASON[t % 12] * (1.0 + 0.01 * np.sin(t / 3.0))
    sa = {r: np.round(nsa[r] / factor) for r in regions}
    sa["All regions"] = sum(sa[r] for r in regions)
    rec = []
    for i, mo in enumerate(MONTHS):
        for r in ["All regions"] + regions:
            rec.append((mo, r, ("Unadjusted",), nsa[r][i], "A"))
            rec.append((mo, r, ("Seasonally adjusted",), sa[r][i], "A"))
    return _official(["Adjustments"], rec)


def rate(seed: int = 7) -> bytes:
    """Test 6: an unemployment-like rate (Percent): Canada is the labour-force-weighted average of 5 provinces, never
    their sum, and never their plain average."""
    rng = np.random.RandomState(seed)
    provs = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    weights = np.array([0.40, 0.25, 0.15, 0.12, 0.08])
    base = np.array([6.0, 7.5, 9.0, 4.5, 11.0])
    rec = []
    for i, mo in enumerate(MONTHS):
        r = np.round(base + 0.6 * np.sin(i / 5.0) + 0.3 * rng.standard_normal(5), 1)
        can = round(float((r * weights).sum()), 1)
        rec.append((mo, "Canada", ("Unemployment rate",), can, ""))
        for p, x in zip(provs, r):
            rec.append((mo, p, ("Unemployment rate",), float(x), ""))
    return _official(["Labour force characteristics"], rec, uom="Percent", scalar="units", decimals="1")


def index_two_bases(seed: int = 9) -> bytes:
    """Test 7: a price index published on two bases (2002=100 and 2012=100), the base named only by UOM and a "Base"
    column; levels are never averaged across bases."""
    rng = np.random.RandomState(seed)
    rec = []
    for i, mo in enumerate(MONTHS):
        lvl = 140.0 * (1.002 ** i) * (1 + 0.003 * rng.standard_normal())
        for geo in ("Canada", "Ontario"):
            g = 1.0 if geo == "Canada" else 1.03
            rec.append((mo, geo, ("2002 base",), round(lvl * g, 1), ""))
            rec.append((mo, geo, ("2012 base",), round(lvl * g / 1.21, 1), ""))

    def uom(key):
        return "2002=100" if "2002" in key[1] else "2012=100"
    return _official(["Base"], rec, uom=uom, scalar="units", decimals="1")


def mixed_units(seed: int = 11) -> bytes:
    """Test 8: a dimension that mixes dollars and units ("Sales value" in Dollars, "Units sold" in Number)."""
    rng = np.random.RandomState(seed)
    rec = []
    for geo, f in (("Canada", 1.0), ("Quebec", 0.3)):
        dol = _series(rng, 9000 * f)
        units = _series(rng, 120 * f)
        for i, mo in enumerate(MONTHS):
            rec.append((mo, geo, ("Sales value",), dol[i], ""))
            rec.append((mo, geo, ("Units sold",), units[i], ""))

    def uom(key):
        return "Dollars" if key[1] == "Sales value" else "Number"
    return _official(["Statistics"], rec, uom=uom)


def business_export(seed: int = 13, total_rows: bool = True) -> bytes:
    """Test 9 (total_rows) and test 11 (no total): a business export with no metadata columns: date, region, product,
    sales. With total_rows, "All" regions and "Total" products are the exact sums."""
    rng = np.random.RandomState(seed)
    regions = ["North", "South", "East", "West"]
    products = ["Widgets", "Gadgets", "Gizmos"]
    v = {(r, p): _series(rng, 100 + 40 * i + 25 * j, seasonal=False) for i, r in enumerate(regions)
         for j, p in enumerate(products)}
    rows = []
    for i, mo in enumerate(MONTHS):
        d = mo + "-01"
        for r in regions:
            for p in products:
                rows.append([d, r, p, "%d" % v[(r, p)][i]])
            if total_rows:
                rows.append([d, r, "Total", "%d" % sum(v[(r, p)][i] for p in products)])
        if total_rows:
            for p in products:
                rows.append([d, "All", p, "%d" % sum(v[(r, p)][i] for r in regions)])
            rows.append([d, "All", "Total", "%d" % sum(v[k][i] for k in v)])
    return _csv(["date", "region", "product", "sales"], rows)


def eurostat(seed: int = 15) -> bytes:
    """Test 12: a Eurostat-style table with flags embedded in OBS_VALUE ("123.4 p", ":" for not available) and an
    OBS_FLAG column; EU27 is the sum of three countries."""
    rng = np.random.RandomState(seed)
    geos = ["DE", "FR", "IT"]
    vals = {g: _series(rng, lv) / 10.0 for g, lv in zip(geos, (4000, 3000, 2000))}
    rows = []
    for i, mo in enumerate(MONTHS):
        tp = mo
        eu = sum(vals[g][i] for g in geos)
        rows.append(["M", "MIO_EUR", "EU27_2020", tp, "%.1f" % eu, ""])
        for g in geos:
            if i % 17 == 5 and g == "IT":
                rows.append(["M", "MIO_EUR", g, tp, ":", ""])
            elif i % 11 == 3:
                rows.append(["M", "MIO_EUR", g, tp, "%.1f p" % vals[g][i], "p"])
            else:
                rows.append(["M", "MIO_EUR", g, tp, "%.1f" % vals[g][i], ""])
    return _csv(["freq", "unit", "geo", "TIME_PERIOD", "OBS_VALUE", "OBS_FLAG"], rows)


def ons(seed: int = 17) -> bytes:
    """Test 12: an ONS-style table with "[x]" for a value that is not available."""
    rng = np.random.RandomState(seed)
    regions = ["England", "Wales", "Scotland"]
    vals = {r: _series(rng, lv) for r, lv in zip(regions, (9000, 900, 1100))}
    rows = []
    for i, mo in enumerate(MONTHS):
        tot = sum(vals[r][i] for r in regions)
        rows.append([mo, "Great Britain", "%d" % tot, "Pounds", "thousands"])
        for r in regions:
            v = "[x]" if (r == "Wales" and i % 9 == 4) else "%d" % vals[r][i]
            rows.append([mo, r, v, "Pounds", "thousands"])
    return _csv(["date", "region", "value", "unit", "scalar_factor"], rows)


def big(n_geo: int = 14, n_ind: int = 30, n_months: int = 96, seed: int = 19) -> bytes:
    """Test 16: about 40,000 rows: 14 regions (a total and 13 parts) x 30 industries (a total and 29 parts) x 96 months."""
    rng = np.random.RandomState(seed)
    months = ["%04d-%02d" % (2015 + i // 12, i % 12 + 1) for i in range(n_months)]
    parts = rng.randint(50, 900, size=(n_geo - 1, n_ind - 1)).astype(float)
    rec = []
    for i, mo in enumerate(months):
        grid = np.round(parts * (1.0 + 0.002 * i) * SEASON[i % 12])
        full = np.zeros((n_geo, n_ind))
        full[1:, 1:] = grid
        full[1:, 0] = grid.sum(axis=1)
        full[0, 1:] = grid.sum(axis=0)
        full[0, 0] = grid.sum()
        for g in range(n_geo):
            geo = "Canada" if g == 0 else "Region %02d" % g
            for k in range(n_ind):
                ind = "All industries" if k == 0 else "Industry %02d" % k
                rec.append((mo, geo, (ind,), full[g, k], ""))
    return _official(["Industry"], rec)


def health_region(seed: int = 21) -> bytes:
    """Test 10: an official-like cube whose second dimension ("Health region", 25 long labels) the privacy check never
    releases (a sensitive header, AM1): withheld, the cube cannot tell its rows apart."""
    rng = np.random.RandomState(seed)
    regs = ["Health region number %02d of the eastern district" % i for i in range(1, 26)]
    rec = []
    for i, mo in enumerate(MONTHS[:36]):
        for geo in ("Canada", "Ontario"):
            for r in regs:
                rec.append((mo, geo, (r,), float(rng.randint(100, 900)), ""))
    return _official(["Health region"], rec)


def category_long(rows: int = 1000, labels: int = 30, header: str = "Industry segment", names: bool = False,
                  seed: int = 23) -> bytes:
    """Test 13: a business file whose text column the engine's scan reads as free text (20 or more different values):
    30 long category labels over 1,000 rows (released), 30 people's names under "Stylist" (kept flagged), or 5,000
    distinct texts (kept flagged)."""
    rng = np.random.RandomState(seed)
    first = ["Marisol", "Jonah", "Priya", "Tomasz", "Aiko", "Kwame", "Lucia", "Dmitri", "Fatima", "Oskar"]
    last = ["Fairweather", "Okafor", "Lindqvist", "Moreau", "Tanaka", "Haddad", "Novak", "Silva", "Brennan", "Kowalski"]
    if names:
        # four-word names (a given name, a middle name, two family names): long enough for the engine's scan to read the
        # column as free text, so the privacy release is asked about it
        vals = ["%s %s %s %s" % (first[i % 10], first[(i // 10 + 3) % 10], last[(i % 10 + i // 10) % 10],
                                 last[(i // 10 + 5) % 10]) for i in range(labels)]
    else:
        vals = ["Segment %02d: %s" % (i, "services for regional %s and allied trades" % ("retail" if i % 2 else "wholesale"))
                for i in range(labels)]
    out = []
    for i in range(rows):
        mo = MONTHS[i % 36]
        lab = vals[rng.randint(len(vals))] if labels < rows else "Note number %05d about the visit" % i
        out.append(["%s-%02d" % (mo, 1 + i % 28), lab, "%.2f" % (20 + 80 * rng.rand())])
    return _csv(["date", header, "amount"], out)


# ----------------------------------------------------------------------------------------------- wave 5 (generality)
# Every cube below is SYNTHETIC, built here from a description of a trap category (the sealed acceptance set was never
# opened, and no official table is copied): a table of provinces with no national row, a combined member, units and
# dollars in one VALUE column, a rate with standard-error members, a quarterly stock, repeated industry names under
# different parents, a wide table whose columns are periods.
REGIONS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
_LV = (5200, 8100, 3300, 6100, 2400)


def counts(uom: str = "Persons", label: str = "Group A", with_total: bool = True, seed: int = 31, header: str = "Group") -> bytes:
    """G3: Total and 5 regions of a COUNT (persons or a number), whose label says what is counted (or nothing: "Group A"
    is the ambiguous case: it cannot say whether the count accumulates over time or is a level)."""
    rng = np.random.RandomState(seed)
    vals = {r: _series(rng, lv / 10.0) for r, lv in zip(REGIONS, _LV)}
    total = sum(vals.values())
    rec = []
    for i, mo in enumerate(MONTHS):
        if with_total:
            rec.append((mo, "Total", (label,), total[i], "A"))
        for r in REGIONS:
            rec.append((mo, r, (label,), vals[r][i], "A"))
    return _official([header], rec, uom=uom, scalar="units")


def no_total_values(seed: int = 41, regions=None) -> dict:
    """The regions' true monthly values (thousands of dollars), before any cell is suppressed: {region: array of 48}."""
    rng = np.random.RandomState(seed)
    regs = list(regions or REGIONS)
    return {r: _series(rng, lv) for r, lv in zip(regs, _LV * 4)}


def no_total(hide=(), combined: bool = False, nested: bool = False, uom: str = "Dollars", label: str = "Retail sales",
             seed: int = 41, regions=None, geo_header: str = "GEO", rate: bool = False, total: bool = False) -> bytes:
    """G1: a table of regions with NO total row (the national figure is the sum of its regions, and the table does not
    list it). hide: [(region, month index)] cells suppressed ("x", blank value). combined adds "Prairie group" = Alpha +
    Bravo + Charlie (a member that equals the sum of 2 or more others: never added together with its own parts); nested
    adds "Centre block" = Charlie + Delta. rate: the values are percentages (first region the highest, so no member is
    mistaken for a published aggregate). total adds the "Total" row (the NEGATIVE case: a table with a total row is read by
    its total and never summed twice)."""
    regs = list(regions or REGIONS)
    vals = no_total_values(seed, regs)
    if rate:
        vals = {r: np.round(np.array([14.0 - 1.5 * k + 0.4 * np.sin(i / 5.0) + 0.1 * (i % 3) for i in range(len(MONTHS))]), 1)
                for k, r in enumerate(regs)}
    gone = {(r, int(i)) for r, i in hide}
    rec = []
    for i, mo in enumerate(MONTHS):
        if total:
            rec.append((mo, "Total", (label,), sum(vals[r][i] for r in regs), "A"))
        for r in regs:
            if (r, i) in gone:
                rec.append((mo, r, (label,), None, "x"))
            else:
                rec.append((mo, r, (label,), vals[r][i], "A"))
        if combined:
            rec.append((mo, "Prairie group", (label,), vals[regs[0]][i] + vals[regs[1]][i] + vals[regs[2]][i], "A"))
        if nested:
            rec.append((mo, "Centre block", (label,), vals[regs[2]][i] + vals[regs[3]][i], "A"))
    data = _official(["Sales"], rec, uom=uom, scalar="thousands" if uom == "Dollars" else "units",
                     decimals="1" if rate else "0")
    if geo_header != "GEO":
        data = data.replace(b'"GEO"', ('"%s"' % geo_header).encode(), 1)
    return data

def measures_units_dollars(seed: int = 51) -> bytes:
    """G2: Total and 3 regions, each in two measures held in ONE value column under "Statistics": "Sales value" (Dollars, in
    thousands) and "Units sold" (Number). Total is the exact sum of its regions for both."""
    rng = np.random.RandomState(seed)
    regs = ["Alpha", "Bravo", "Charlie"]
    units = {r: _series(rng, lv / 20.0) for r, lv in zip(regs, (5200, 8100, 3300))}
    price = {r: 3.2 + 0.4 * k for k, r in enumerate(regs)}
    dol = {r: np.round(units[r] * price[r]) for r in regs}
    rec = []
    for i, mo in enumerate(MONTHS):
        rec.append((mo, "Total", ("Sales value",), sum(dol[r][i] for r in regs), "A"))
        rec.append((mo, "Total", ("Units sold",), sum(units[r][i] for r in regs), "A"))
        for r in regs:
            rec.append((mo, r, ("Sales value",), dol[r][i], "A"))
            rec.append((mo, r, ("Units sold",), units[r][i], "A"))

    def uom(key):
        return "Dollars" if key[1] == "Sales value" else "Number"
    return _official(["Statistics"], rec, uom=uom, scalar="units")


def measures_rate_se(seed: int = 53, members=None) -> bytes:
    """G2: Canada and 4 provinces; "Labour force characteristics" holds, in one value column, an Unemployment rate (Percent,
    Canada the employment-weighted average of the provinces), Employment (Persons, Canada the exact sum) and the Standard
    error of the unemployment rate (Percent: small numbers that must never be the headline or be summed)."""
    rng = np.random.RandomState(seed)
    provs = ["Alpha", "Bravo", "Charlie", "Delta"]
    emp = {p: np.round(_series(rng, lv, growth=0.002)) for p, lv in zip(provs, (5200, 8100, 3300, 6100))}
    rate = {p: np.round(5.0 + k + 0.5 * np.sin(np.arange(len(MONTHS)) / 6.0 + k) + 0.1 * rng.standard_normal(len(MONTHS)), 1)
            for k, p in enumerate(provs)}
    names = members or ("Unemployment rate", "Employment", "Standard error of the unemployment rate")
    rec = []
    for i, mo in enumerate(MONTHS):
        tot_emp = sum(emp[p][i] for p in provs)
        can_rate = round(float(sum(rate[p][i] * emp[p][i] for p in provs) / tot_emp), 1)
        for nm in names:
            if nm == "Unemployment rate":
                rec.append((mo, "Canada", (nm,), can_rate, "A"))
                for p in provs:
                    rec.append((mo, p, (nm,), rate[p][i], "A"))
            elif nm == "Employment":
                rec.append((mo, "Canada", (nm,), tot_emp, "A"))
                for p in provs:
                    rec.append((mo, p, (nm,), emp[p][i], "A"))
            else:
                rec.append((mo, "Canada", (nm,), round(0.08 + 0.01 * np.cos(i / 4.0), 2), "A"))
                for k, p in enumerate(provs):
                    rec.append((mo, p, (nm,), round(0.15 + 0.03 * k + 0.01 * np.cos(i / 4.0 + k), 2), "A"))

    def uom(key):
        return "Persons" if key[1] == "Employment" else "Percent"
    return _official(["Labour force characteristics"], rec, uom=uom, scalar="units", decimals="2")

# G5: repeated member names. An industry table whose names repeat under different parents ("Other", "Services"):
_DUP_TREE = {"All industries": ["Retail", "Wholesale", "Transport"],
             "Retail": ["Food", "Other", "Services"], "Wholesale": ["Machinery", "Other", "Services"],
             "Transport": ["Road", "Other", "Services"]}
_DUP_LEAF = {("Retail", "Food"): 3000, ("Retail", "Other"): 1200, ("Retail", "Services"): 800,
             ("Wholesale", "Machinery"): 2500, ("Wholesale", "Other"): 900, ("Wholesale", "Services"): 700,
             ("Transport", "Road"): 2000, ("Transport", "Other"): 600, ("Transport", "Services"): 500}


def dup_names(variant: str = "coordinate", seed: int = 61) -> bytes:
    """G5: the industry names "Other" and "Services" repeat under three parents. variant: "coordinate" (a dotted COORDINATE
    "1.<member id>": the member id is its second part), "code" (an "Industry code" column), "parent" (an "Industry group" column
    holding the parent's name), "plain" (the same names, no id and no parent column: nothing tells the repeats apart, the table
    is refused) or "same_parent" (a parent column, but a second "Other" under the same parent: true duplicates, refused)."""
    rng = np.random.RandomState(seed)
    leaf = {k: _series(rng, v) for k, v in _DUP_LEAF.items()}
    members = []                                              # (id, name, parent, series)
    mid = 0
    for par, kids in list(_DUP_TREE.items())[1:]:
        for kid in kids:
            mid += 1
            members.append((mid, kid, par, leaf[(par, kid)]))
    par_sum = {par: sum(m[3] for m in members if m[2] == par) for par in ("Retail", "Wholesale", "Transport")}
    for par in ("Retail", "Wholesale", "Transport"):
        mid += 1
        members.append((mid, par, "All industries", par_sum[par]))
    mid += 1
    members.append((mid, "All industries", "", sum(par_sum.values())))
    if variant == "same_parent":
        # two rows named "Other" under Retail on every date: true duplicates
        members.append((mid + 1, "Other", "Retail", leaf[("Retail", "Other")] * 0 + 7))
    head = ["REF_DATE", "GEO", "DGUID", "Industry"] + (["Industry group"] if variant in ("parent", "same_parent") else []) + \
        (["Industry code"] if variant == "code" else []) + ["UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID", "VECTOR"] + \
        (["COORDINATE"] if variant == "coordinate" else []) + ["VALUE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS"]
    rows = []
    for i, mo in enumerate(MONTHS):
        for (m_id, name, par, ser) in members:
            r = [mo, "Canada", "2021A000000011", name]
            if variant in ("parent", "same_parent"):
                r.append(par)
            if variant == "code":
                r.append("C%03d" % (100 + m_id))
            r += ["Dollars", "81", "thousands", "3", "v%d" % (200000 + m_id)]
            if variant == "coordinate":
                r.append("1.%d" % m_id)
            r += ["%d" % ser[i], "A", "", "", "0"]
            rows.append(r)
    return _csv(head, rows)


def dup_names_series(seed: int = 61):
    """The members' true monthly series of dup_names (thousands of dollars): {(parent, name): array}, parents summed."""
    rng = np.random.RandomState(seed)
    leaf = {k: _series(rng, v) for k, v in _DUP_LEAF.items()}
    out = dict(leaf)
    for par in ("Retail", "Wholesale", "Transport"):
        out[("All industries", par)] = sum(v for (p, _n), v in leaf.items() if p == par)
    out[("", "All industries")] = sum(out[("All industries", p)] for p in ("Retail", "Wholesale", "Transport"))
    return out

# G4: quarterly and annual tables. Values and totals as partition(): Total and 4 regions that add up exactly.
def periodic(freq: str = "quarter", style: str = "iso", stock: bool = False, years: int = 12, seed: int = 71,
             first_year: int = 2012, with_total: bool = True) -> bytes:
    """A Total and 4 regions published every quarter (freq "quarter") or every year (freq "year"). style: how the period is
    written: "iso" (2012-01, the first month of the quarter; 2012-01 for a year), "q" (2012-Q1), "qc" (2012Q1), "qf" (Q1 2012),
    "year" (2012), "dec" (2012-12-31 for a year, 2012-03-31 for a quarter: the period's last day). stock: a count of persons
    (Population: a level), else dollars (a flow)."""
    rng = np.random.RandomState(seed)
    regs = ["North", "South", "East", "West"]
    per_year = 4 if freq == "quarter" else 1
    n = years * per_year
    t = np.arange(n)
    vals = {}
    for r, lv in zip(regs, (5200, 8100, 3300, 6100)):
        seas = SEASON[(t % per_year) * (12 // per_year)] if per_year > 1 else np.ones(n)
        vals[r] = np.round(lv * (1.003 ** (t * 12 // per_year)) * (seas if not stock else 1.0) * (1 + 0.01 * rng.standard_normal(n)))
    total = sum(vals.values())

    def label(i):
        y = first_year + i // per_year
        q = i % per_year
        if freq == "year":
            return {"iso": "%d-01" % y, "year": "%d" % y, "dec": "%d-12-31" % y}.get(style, "%d-01" % y)
        return {"iso": "%d-%02d" % (y, 3 * q + 1), "q": "%d-Q%d" % (y, q + 1), "qc": "%dQ%d" % (y, q + 1),
                "qf": "Q%d %d" % (q + 1, y), "dec": "%d-%02d-%02d" % (y, 3 * q + 3, 31 if q in (0, 3) else 30)}[style]
    rec = []
    for i in range(n):
        if with_total:
            rec.append((label(i), "Total", ("Population" if stock else "Retail sales",), total[i], "A"))
        for r in regs:
            rec.append((label(i), r, ("Population" if stock else "Retail sales",), vals[r][i], "A"))
    return _official(["Sales"], rec, uom="Persons" if stock else "Dollars", scalar="units" if stock else "thousands")

def wide_period(freq: str = "quarter", flags: bool = True, years: int = 8, first_year: int = 2016, seed: int = 81):
    """G7: a Eurostat-style WIDE table: columns freq, unit, geo\\TIME_PERIOD and one column per period (2016Q1 ... or 2016-01 ... or
    2016 ...); EU27_2020 is the exact sum of DE, FR and IT. With flags, ":" stands for a value not available (IT in some periods,
    EU27 stays whole) and "123.4 p" for a provisional one (DE's last two periods). Returns (csv bytes, {"periods", "colon", "p",
    "values": {geo: [true values]}, "labels": [...]})."""
    rng = np.random.RandomState(seed)
    geos = ["DE", "FR", "IT"]
    per_year = {"quarter": 4, "month": 12, "year": 1}[freq]
    n = years * per_year
    labels = []
    for i in range(n):
        y, k = first_year + i // per_year, i % per_year
        labels.append({"quarter": "%dQ%d" % (y, k + 1), "month": "%d-%02d" % (y, k + 1), "year": "%d" % y}[freq])
    vals = {g: np.round(_series(rng, lv / 10.0, n=n, growth=0.004 if freq != "month" else 0.0004, seasonal=False), 1)
            for g, lv in zip(geos, (4000, 3000, 2000))}
    vals["EU27_2020"] = sum(vals[g] for g in geos)
    rows, colon, prov = [], 0, 0
    for g in ["EU27_2020"] + geos:
        cells = []
        for i in range(n):
            txt = "%.1f" % vals[g][i]
            if flags and g == "IT" and i % 7 == 2:
                txt, colon = ":", colon + 1
            elif flags and g == "DE" and i >= n - 2:
                txt, prov = txt + " p", prov + 1
            cells.append(txt)
        rows.append([("Q" if freq == "quarter" else "M" if freq == "month" else "A"), "MIO_EUR", g] + cells)
    data = _csv(["freq", "unit", "geo\\TIME_PERIOD"] + labels, rows)
    return data, {"periods": n, "colon": colon, "p": prov, "values": vals, "labels": labels}

# wave 5b: rate and index tables whose published aggregate is read by its name, by evidence, or not at all
RATE_PROVINCES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
_RATE_BASE = (8.0, 5.0, 6.5, 10.0, 11.5, 4.0)          # Alpha, first in the file, lies in the MIDDLE of the others' range
_RATE_WEIGHT = (0.30, 0.22, 0.18, 0.14, 0.10, 0.06)


def rate_table(aggregate: str = "none", agg_first: bool = False, parts: int = 6, noise: float = 0.5, decimals: int = 1,
               seed: int = 71, index: bool = False, countries: bool = False) -> bytes:
    """Wave 5b, follow-up 3 (aggregate "Canada_out" and "total_out": wave 5c, a named whole outside the others' range): an unemployment-like rate (Percent; with index=True a price index on one base, "2012=100") of
    `parts` provinces. aggregate: "none" (NO total member: the first province lies strictly inside the others' range in every
    cell, the table the old rule read as the published aggregate: "one province reported as the national figure"), "Canada" (a row
    named as a whole country), "total" (a row named "All provinces"), "unnamed" (a row called "Zeta group", the labour-force-weighted
    average of the provinces, to the digit published). agg_first lists that row first, else last. countries=True names the
    provinces after countries (Canada and the United States, both in the middle of the range, among them, none a total): two
    whole countries' names say nothing about which is the table's total."""
    rng = np.random.RandomState(seed)
    names = (["Canada", "Mexico", "United States", "Brazil", "Chile", "Peru"] if countries else RATE_PROVINCES)[:parts]
    base = np.array(_RATE_BASE[:parts]) * (14.0 if index else 1.0)
    w = np.array(_RATE_WEIGHT[:parts])
    w = w / w.sum()
    agg_name = {"Canada": "Canada", "total": "All provinces", "unnamed": "Zeta group", "Canada_out": "Canada",
                "total_out": "All provinces", "combined": "Frontier area"}.get(aggregate)
    label = "Consumer price index" if index else "Unemployment rate"
    rec = []
    for i, mo in enumerate(MONTHS):
        r = np.round(base + (0.6 * (14.0 if index else 1.0)) * np.sin(i / 5.0) + noise * (14.0 if index else 1.0) * rng.standard_normal(parts), decimals)
        rows = [(mo, p, (label,), float(x), "") for p, x in zip(names, r)]
        if agg_name:
            # "Canada_out" / "total_out" (wave 5c): a named whole whose values lie OUTSIDE the others' range in every month (the
            # listed provinces are a subset of its parts), so the range cannot verify it and nothing can reproduce it
            out = 2.0 if aggregate.endswith("_out") else 1.0
            agg = (mo, agg_name, (label,), round(float((r * w).sum()) * out, decimals), "")
            if aggregate == "combined":
                # wave 5d: NO total member. "Frontier area" is the weighted average of the first THREE provinces only (a regional group)
                agg = (mo, agg_name, (label,), round(float((r[:3] * w[:3]).sum() / w[:3].sum()), decimals), "")
            rows = [agg] + rows if agg_first else rows + [agg]
        rec.extend(rows)
    return _official(["Labour force characteristics"], rec, uom="2012=100" if index else "Percent", scalar="units",
                     decimals=str(decimals))


def rate_panel(n_members: int = 39, months: int = 79, aggregate: bool = True, decimals: int = 1, seed: int = 5) -> bytes:
    """Wave 5b: a rate of many members (provinces drawn with a common factor, loadings, a base each and their own noise, one decimal)
    and, with `aggregate`, a row called "Zeta group" that is their weighted average to the digit published. For the search's time."""
    rng = np.random.RandomState(seed)
    base, load = rng.uniform(3, 12, n_members), rng.uniform(0.5, 1.5, n_members)
    f = np.cumsum(rng.normal(0, 0.15, months)) + 0.8 * np.sin(np.arange(months) / 6.0)
    x = np.array([np.round(base[p] + load[p] * f + rng.normal(0, rng.uniform(0.1, 0.7), months), decimals) for p in range(n_members)])
    w = rng.dirichlet(np.ones(n_members))
    agg = np.round((x * w[:, None]).sum(axis=0), decimals)
    rec = []
    for t in range(months):
        mo = "%04d-%02d" % (2016 + t // 12, t % 12 + 1)
        rec.extend((mo, "Prov %02d" % p, ("Unemployment rate",), float(x[p, t]), "") for p in range(n_members))
        if aggregate:
            rec.append((mo, "Zeta group", ("Unemployment rate",), float(agg[t]), ""))
    return _official(["Labour force characteristics"], rec, uom="Percent", scalar="units", decimals=str(decimals))


# --------------------------------------------------------------------------------------- wave 5d: what the fuzz tester found
_SCALE_ID = {"units": "0", "thousands": "3", "millions": "6", "billions": "9"}


def _official_v(dim_names, records, spec, status_of=None):
    """A StatCan-shaped CSV (COORDINATE and all) whose series differ in unit, scale and DECIMALS. records: [(period, geo, dims tuple,
    value or None, status)]; spec(key) -> (uom, scale word, decimals) for a series key (geo,) + dims. A value prints with its own
    decimals; a blank value keeps its status."""
    head = _META_HEAD + list(dim_names) + ["UOM", "UOM_ID", "SCALAR_FACTOR", "SCALAR_ID", "VECTOR", "COORDINATE", "VALUE", "STATUS",
                                           "SYMBOL", "TERMINATED", "DECIMALS"]
    vec, geos, pos, rows = {}, {}, [dict() for _ in range(1 + len(dim_names))], []
    for (mo, geo, dims, val, status) in records:
        key = (geo,) + tuple(dims)
        vec.setdefault(key, "v%d" % (41000000 + 4 * len(vec)))
        geos.setdefault(geo, "2021A%09d" % (len(geos) + 11))
        for k, m in enumerate(key):
            pos[k].setdefault(m, len(pos[k]) + 1)
        uom, scale, dec = spec(key)
        coord = ".".join(str(pos[k][m]) for k, m in enumerate(key))
        rows.append([mo, geo, geos[geo]] + list(dims) + [uom, "81" if uom == "Dollars" else "223", scale, _SCALE_ID[scale], vec[key],
                                                          coord, "" if val is None else "%.*f" % (int(dec), val), status, "", "", str(dec)])
    return _csv(head, rows)


def dollars_beside_units(seed: int = 91, scale: str = "millions", industry: bool = False, price: float = 9.0) -> bytes:
    """Wave 5d, cause A. "All regions" (the exact sum, as published) over Glenhaven and Wynstead, each in two measures held in ONE value
    column under "Estimates": "Sales value" (Dollars, in `scale`, one decimal) and "Units sold" (Number, units, no decimal: nine
    digits at the total, the shape of a national ID number). SCALAR_FACTOR and DECIMALS differ by series. With `industry`, a second
    dimension: "Full range" (a total with no cue in its name) over "Stationery [31]" and "Bicycles [41]"."""
    rng = np.random.RandomState(seed)
    f = {"thousands": 1e3, "millions": 1e6}[scale]
    n = len(MONTHS)
    t = np.arange(n)
    inds = (("Stationery [31]", 0.6), ("Bicycles [41]", 0.4)) if industry else ((None, 1.0),)
    lat = {}
    for g, lvl in (("Glenhaven", 700.0), ("Wynstead", 900.0)):
        for ind, sh in inds:
            lat[(g, ind)] = lvl * sh * 1.004 ** t * SEASON[t % 12] * (1.0 + 0.01 * rng.standard_normal(n))
    rec = []
    for i, mo in enumerate(MONTHS):
        for g in ("All regions", "Glenhaven", "Wynstead"):
            for ind in (["Full range"] + [x for x, _s in inds]) if industry else [None]:
                gs = ("Glenhaven", "Wynstead") if g == "All regions" else (g,)
                ins = [x for x, _s in inds] if ind in (None, "Full range") else [ind]
                dol = sum(lat[(a, b)][i] for a in gs for b in ins)
                for measure, val in (("Sales value", round(dol, 1)), ("Units sold", float(round(dol * f / price)))):
                    rec.append((mo, g, (measure,) + ((ind,) if industry else ()), val, "A"))

    def spec(key):
        return ("Dollars", scale, 1) if key[1] == "Sales value" else ("Number", "units", 0)
    return _official_v(["Estimates"] + (["Type of business"] if industry else []), rec, spec)


def small_combined(seed: int = 92, total: bool = False, sibling: bool = False) -> bytes:
    """Wave 5d, cause B (a flow). Three regions and "Inland provinces", which is the sum of two of them (Alder and Birch) and larger than the
    third (Cedar); NO total row. With `total` (the negative case) a "Total" row is added: the sum of the three. With `sibling` the third
    region is smaller than Birch (not than the group) in every month: a region that bounds it, as a component would be bounded."""
    rng = np.random.RandomState(seed)
    vals = {r: _series(rng, lv) for r, lv in zip(("Alder", "Birch", "Cedar"), (1000, 750, 1350))}
    if sibling:
        vals = {r: _series(rng, lv, growth=0.003) for r, lv in zip(("Alder", "Birch", "Cedar"), (1000, 1500, 600))}
    rec = []
    for i, mo in enumerate(MONTHS):
        if total:
            rec.append((mo, "Total", ("Wholesale sales",), sum(vals[r][i] for r in vals), "A"))
        for r in vals:
            rec.append((mo, r, ("Wholesale sales",), vals[r][i], "A"))
        rec.append((mo, "Inland provinces", ("Wholesale sales",), vals["Alder"][i] + vals["Birch"][i], "A"))
    return _official(["Characteristics"], rec)


def component_under_a_part(seed: int = 93, copy: bool = False) -> bytes:
    """Wave 5d, the negative of cause B: one place and an industry dimension with NO codes, an unnamed total "Full range" over
    Stationery, Bicycles and Toys, and "Online stationery", a component INSIDE Stationery (smaller than it everywhere). With `copy`, also
    "Stationery (own brand)", a single-child member that is the same series as Stationery."""
    rng = np.random.RandomState(seed)
    vals = {m: _series(rng, lv) for m, lv in (("Stationery", 1500), ("Bicycles", 1000), ("Toys", 800))}
    online = np.round(vals["Stationery"] * 0.4)
    rec = []
    for i, mo in enumerate(MONTHS):
        rec.append((mo, "Canada", ("Full range",), sum(vals[m][i] for m in vals), "A"))
        for m in vals:
            rec.append((mo, "Canada", (m,), vals[m][i], "A"))
        rec.append((mo, "Canada", ("Online stationery",), online[i], "A"))
        if copy:
            rec.append((mo, "Canada", ("Stationery (own brand)",), vals["Stationery"][i], "A"))
    return _official(["Type of business"], rec)


def alt_total_rate(seed: int = 94, alt: str = "Total excl. Seasonal shops") -> bytes:
    """Wave 5d, cause C. A vacancy rate (Percent): Canada over two regions (named), and a "Type of business" dimension with
    "Total, all industries" (the weighted average of two industries), the two industries, and an alternative total `alt` (the total
    less Seasonal shops: Apparel alone)."""
    rng = np.random.RandomState(seed)
    n = len(MONTHS)
    t = np.arange(n)
    ind = {"Seasonal shops [11]": 6.0 + 0.5 * np.sin(t / 5.0), "Apparel [21]": 5.0 + 0.4 * np.cos(t / 7.0)}
    wt = {"Seasonal shops [11]": 0.35, "Apparel [21]": 0.65}
    rec = []
    geo = {"Harnmoor": 0.0, "Ulmford": 1.1}
    gw = {"Harnmoor": 0.55, "Ulmford": 0.45}
    noise = {(g, k): 0.15 * rng.standard_normal(n) for g in geo for k in ind}
    for i, mo in enumerate(MONTHS):
        cells = {}
        for g in geo:
            for k in ind:
                cells[(g, k)] = round(float(ind[k][i] + geo[g] + noise[(g, k)][i]), 1)
            cells[(g, "Total, all industries")] = round(sum(wt[k] * cells[(g, k)] for k in ind), 1)
            cells[(g, alt)] = cells[(g, "Apparel [21]")]
        for k in list(ind) + ["Total, all industries", alt]:
            cells[("Canada", k)] = round(sum(gw[g] * cells[(g, k)] for g in geo), 1)
        for g in ("Canada", "Harnmoor", "Ulmford"):
            for k in (alt, "Apparel [21]", "Total, all industries", "Seasonal shops [11]"):
                rec.append((mo, g, ("Vacancy rate", k), cells[(g, k)], ""))
    return _official(["Sales", "Type of business"], rec, uom="Percent", scalar="units", decimals="1")


def heavy_suppression(small: bool = False, total_gap: float = 0.0, seed: int = 97, name: str = "Canada", complete: int = 5) -> bytes:
    """Wave 5d, cause D. A total (`name`) over five regions, 30 months, a flow, with so many suppressed cells (25 of the 30 months have a
    region blank: 10, 8 and 7 months of three of them) that only 5 months are complete. `small`: counts of 5 to 40 (the total is about 100
    rounding tolerances less than the default): a match in 5 cells says little. `total_gap`: the total is that share above the sum."""
    rng = np.random.RandomState(seed)
    months = MONTHS[:30]
    regs = ["Ravdale", "Glenholm", "Pellcombe", "Falholm", "Lornstead"]
    lv = (1.0, 1.4, 0.9, 1.2, 0.7)
    base = 8.0 if small else 9000.0
    vals = {r: np.round(base * k * (1.0 + 0.004) ** np.arange(30) * (1.0 + 0.03 * rng.standard_normal(30))) for r, k in zip(regs, lv)}
    gone = 30 - complete                                   # months with a region blank: three regions share the first `gone` months
    cut = (gone // 3 + (1 if gone % 3 > 0 else 0), (2 * gone) // 3 + (1 if gone % 3 > 1 else 0))
    hide = {"Ravdale": range(0, cut[0]), "Pellcombe": range(cut[0], cut[1]), "Lornstead": range(cut[1], gone)}
    rec = []
    for i, mo in enumerate(months):
        rec.append((mo, name, ("Wholesale sales",), round(sum(vals[r][i] for r in regs) * (1.0 + total_gap)), "A"))
        for r in regs:
            if i in hide.get(r, ()):
                rec.append((mo, r, ("Wholesale sales",), None, "x"))
            else:
                rec.append((mo, r, ("Wholesale sales",), vals[r][i], "A"))
    return _official(["Characteristics"], rec, uom="Number" if small else "Dollars", scalar="units" if small else "thousands")


def quarterly_adjusted(years: int = 9, basis: bool = True, neutral: bool = False, seed: int = 95, gap: float = 0.25, regions: int = 3) -> bytes:
    """Wave 5d, cause E. A quarterly flow (dollars in thousands) for two regions and NO total row; with `basis`, every region as an
    unadjusted copy and a seasonally adjusted one (the same annual totals, a third of the seasonality; labelled "A" and "B" with
    `neutral`); the second region is `gap` larger than the first (0 makes two regions whose annual totals ARE alike). Without
    `basis` (the negative case) the two regions alone."""
    rng = np.random.RandomState(seed)
    nq = 4 * years
    q = np.arange(nq)
    season = np.array([0.88, 0.98, 1.04, 1.10])
    season = season / season.mean()
    # wave 5e: three regions by default (a dimension of places is added only from 3 parts); `regions=2` keeps the old pair
    regs = (("Osswick", 1000.0), ("Ormvale", 1000.0 * (1.0 + gap)), ("Pellmoor", 1000.0 * (1.0 + 2.0 * gap)))[:regions]
    label = lambda k: "Q%d %d" % (k % 4 + 1, 2014 + k // 4)       # noqa: E731
    rec = []
    nsa = {r: np.round(lv * 1.01 ** q * season[q % 4] * (1.0 + 0.01 * rng.standard_normal(nq))) for r, lv in regs}
    sa = {r: np.round(nsa[r] / season[q % 4] * (1.0 + 0.002 * rng.standard_normal(nq))) for r, _l in regs}
    for k in range(nq):
        for r, _l in regs:
            if basis:
                rec.append((label(k), r, ("B" if neutral else "Seasonally adjusted",), sa[r][k], "A"))
                rec.append((label(k), r, ("A" if neutral else "Unadjusted",), nsa[r][k], "A"))
            else:
                rec.append((label(k), r, ("Orders",), nsa[r][k], "A"))
    return _official(["Basis" if basis else "Estimates"], rec)



OWNERS = ("Michael Penhallow", "Karen Telford", "Daniel Askerby", "Laura Quillon", "Peter Brandmoor")


def long_panel_with_owner(seed: int = 98, column: str = "Account owner", phones: bool = False, neutral: bool = False) -> bytes:
    """Wave 5d, the privacy gap. A long statistical table of five exchange-rate-like series with no relation among them (a panel: the
    layout pass turns it into one column per series, each series named from its text columns), and a personal column beside the
    currency: the series' account owner (a person's name; with `phones`, a phone number written 555-010-xxxx under "Contact"; with
    `neutral`, "Group A" ... "Group E": a category, the negative case)."""
    rng = np.random.RandomState(seed)
    n = 60
    months = ["%04d-%02d" % (2018 + i // 12, i % 12 + 1) for i in range(n)]
    cur = (("U.S. dollar", 1.30), ("Euro", 1.45), ("Japanese yen", 0.0095), ("Pound sterling", 1.70), ("Swiss franc", 1.40))
    level = {c: base * np.exp(np.cumsum(0.01 * rng.standard_normal(n))) for c, base in cur}
    rec = []
    for i, mo in enumerate(months):
        for k, (c, _b) in enumerate(cur):
            who = ("Group %s" % "ABCDE"[k]) if neutral else (("555-010-%04d" % (1100 + 137 * k)) if phones else OWNERS[k])
            rec.append((mo, "Canada", (c, who), float(level[c][i]), ""))
    uom = {"U.S. dollar": "Dollars", "Euro": "Euros", "Japanese yen": "Yen", "Pound sterling": "Pounds", "Swiss franc": "Francs"}
    return _official_v(["Type of currency", column], rec, lambda key: (uom[key[1]], "units", 4))     # each in its own unit: a panel


def regions_with_personal(column: str = "Account owner", phones: bool = False, seed: int = 99, months: int = 48) -> bytes:
    """Wave 5d, the privacy gap (fuzz seeds 13, 66, 147, 181, 217, 218): a stock (labour force, persons in thousands) for three regions and NO
    total row, and a personal column that is one to one with the region: the region's account owner (a person's name) or, with `phones`,
    its contact phone number (555-010-xxxx). The personal column's labels are longer than the regions', so the table's one
    dimension was the personal column and the regions an alias of it, the structure layer found nothing to slice, and the layout pass named
    each series from ALL its text columns: "Tarnstead | Michael Penhallow" was a column of the table the report was written from."""
    rng = np.random.RandomState(seed)
    regs = (("Dunham", 10112.0), ("Tarnstead", 2045.0), ("Elmvale", 3363.0))
    mo = ["%04d-%02d" % (2020 + i // 12, i % 12 + 1) for i in range(months)]
    rec = []
    for i, m in enumerate(mo):
        for k, (r, lv) in enumerate(regs):
            who = ("555-010-%04d" % (3016 + 291 * k)) if phones else OWNERS[k]
            rec.append((m, r, ("Labour force", who), round(lv * (1.0 + 0.002 * i) * (1.0 + 0.01 * rng.standard_normal())), "A"))
    return _official_v(["Statistics", column], rec, lambda key: ("Persons", "thousands", 0))


def rate_sector_alt(seed: int = 96, with_total: bool = True, geo_total: bool = True, noisy_total: bool = False) -> bytes:
    """Wave 5d (CHECK 1 finding). A rate (Percent): a "Sector" dimension of four coded sectors, "Full range [11-41]" (their weighted average; a
    range code holds the four codes, no word says total) and "Total except Footwear and Grocery" (the weighted average of the other two:
    an alternative whose first word is "Total"), and a GEO of two regions and "Total". With `with_total` False the table has only the
    alternative (the negative case): nothing is the sector total. With `geo_total` False the GEO has no total row. With `noisy_total` the
    range-coded total is NOT an exact weighted average of the sectors (a quarter point of noise: no fit can verify it)."""
    rng = np.random.RandomState(seed)
    n = len(MONTHS)
    t = np.arange(n)
    secs = {"Tools [11]": 5.2 + 0.4 * np.sin(t / 6.0), "Footwear [21]": 7.4 + 0.5 * np.cos(t / 5.0), "Grocery [31]": 6.1 + 0.3 * np.sin(t / 9.0),
            "Cosmetics [41]": 8.3 + 0.4 * np.cos(t / 8.0)}
    w = {"Tools [11]": 0.20, "Footwear [21]": 0.30, "Grocery [31]": 0.25, "Cosmetics [41]": 0.25}
    geo = {"Dunmoor": 0.0, "Cormmoor": 0.9}
    gw = {"Dunmoor": 0.6, "Cormmoor": 0.4}
    noise = {(g, k): 0.12 * rng.standard_normal(n) for g in geo for k in secs}
    rec = []
    for i, mo in enumerate(MONTHS):
        cells = {}
        for g in geo:
            for k in secs:
                cells[(g, k)] = round(float(secs[k][i] + geo[g] + noise[(g, k)][i]), 1)
            cells[(g, "Full range [11-41]")] = round(sum(w[k] * cells[(g, k)] for k in secs)
                                                     + (0.25 * float(rng.standard_normal()) if noisy_total else 0.0), 1)
            rest = ("Tools [11]", "Cosmetics [41]")
            cells[(g, "Total except Footwear and Grocery")] = round(sum(w[k] * cells[(g, k)] for k in rest) / sum(w[k] for k in rest), 1)
        names = list(secs) + (["Full range [11-41]"] if with_total else []) + ["Total except Footwear and Grocery"]
        for k in names:
            if geo_total:
                rec.append((mo, "Total", ("Unemployment rate", k), round(sum(gw[g] * cells[(g, k)] for g in geo), 1), ""))
            for g in geo:
                rec.append((mo, g, ("Unemployment rate", k), cells[(g, k)], ""))
    return _official(["Statistics", "Sector"], rec, uom="Percent", scalar="units", decimals="1")


ALL = {"partition": partition, "partition_suppressed": lambda: partition(0.10), "hierarchy": hierarchy, "adjusted_additive": adjusted_additive,
       "hierarchy_nocodes": lambda: hierarchy(codes=False, shuffle=True), "adjusted": adjusted, "rate": rate,
       "index_two_bases": index_two_bases, "mixed_units": mixed_units, "business_export": business_export,
       "business_flat": lambda: business_export(total_rows=False), "eurostat": eurostat, "ons": ons, "big": big,
       "health_region": health_region, "category_long": category_long}

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for name, fn in ALL.items():
        with open(os.path.join(out, name + ".csv"), "wb") as fh:
            fh.write(fn())
    print("wrote %d cubes to %s" % (len(ALL), out))


def sa_copy(gap: float = 0.05, seed: int = 211, years: int = 4) -> bytes:
    """Wave 5e (P5): a table of Canada's retail-like flow as an unadjusted series and a seasonally adjusted copy the way an agency makes
    it (the unadjusted one over its seasonal factors, so the two share their noise), whose level is `gap` above the unadjusted one for
    every year (not benchmarked to it). Two members, no total, one region: the copy is told by its shape, not by a 3% constant."""
    rng = np.random.RandomState(seed)
    n = 12 * years
    t = np.arange(n)
    months = ["%d-%02d" % (2019 + i // 12, i % 12 + 1) for i in range(n)]
    nsa = np.round(5000.0 * 1.004 ** t * SEASON[t % 12] * (1.0 + 0.02 * rng.standard_normal(n)))
    sa = np.round(nsa / SEASON[t % 12] * (1.0 + gap))
    rec = []
    for i, mo in enumerate(months):
        rec.append((mo, "Canada", ("Unadjusted",), nsa[i], ""))
        rec.append((mo, "Canada", ("Seasonally adjusted",), sa[i], ""))
    return _official(["Adjustments"], rec)


def five_regions(copy: bool = False, seed: int = 301, years: int = 4) -> bytes:
    """Wave 5e (suspected item: the twin guard allowed at most 4 members). Five regions of a retail-like flow, no total row, 48 months. With
    `copy`, the fifth is the first one again at 1.3 times its level and with its noise (a price basis, an adjusted copy): one quantity twice,
    which no total's name or sum shows, so it is never added to its original. Without, five regions with their own noise."""
    rng = np.random.RandomState(seed)
    n = 12 * years
    t = np.arange(n)
    months = ["%d-%02d" % (2019 + i // 12, i % 12 + 1) for i in range(n)]
    regs = {}
    for k, name in enumerate(("Alder", "Birch", "Cedar", "Dune")):
        regs[name] = np.round((3000.0 + 600 * k) * (1.0 + 0.003 + 0.0004 * k) ** t * SEASON[t % 12] * (1.0 + 0.02 * rng.standard_normal(n)))
    regs["Elm"] = np.round(regs["Alder"] * 1.3 * (1.0 + 0.001 * rng.standard_normal(n))) if copy else \
        np.round(2200.0 * (1.0 + 0.004) ** t * SEASON[t % 12] * (1.0 + 0.02 * rng.standard_normal(n)))
    rec = [(mo, r, ("Retail sales",), regs[r][i], "A") for i, mo in enumerate(months) for r in regs]
    return _official(["Characteristics"], rec)


def tree_25(seed: int = 311, years: int = 3) -> bytes:
    """Wave 5e (suspected item: a 25-sector uncoded tree beyond the 22-candidate cap lost its breakdown). A total over 25 sectors, no codes,
    36 months, one region; the total is the sum of the sectors."""
    rng = np.random.RandomState(seed)
    n = 12 * years
    t = np.arange(n)
    months = ["%d-%02d" % (2020 + i // 12, i % 12 + 1) for i in range(n)]
    sect = {"Sector %s" % chr(65 + k): np.round((800.0 + 90 * k) * 1.003 ** t * SEASON[t % 12] * (1.0 + 0.02 * rng.standard_normal(n))) for k in range(25)}
    tot = sum(sect.values())
    rec = []
    for i, mo in enumerate(months):
        rec.append((mo, "Canada", ("All sectors",), tot[i], "A"))
        for k, v in sect.items():
            rec.append((mo, "Canada", (k,), v[i], "A"))
    return _official(["Sector"], rec)
