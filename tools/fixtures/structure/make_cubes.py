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


def partition(blank_share: float = 0.0, seed: int = 1) -> bytes:
    """Test 1 (and 2 with blank_share 0.10): Total and 5 regions; the Total is the exact sum of the regions as published;
    with blank_share, that share of the region cells is suppressed ("x", blank value), the Total still whole."""
    rng = np.random.RandomState(seed)
    regions = ["North", "South", "East", "West", "Centre"]
    vals = {r: _series(rng, lv) for r, lv in zip(regions, (5200, 8100, 3300, 6100, 2400))}
    total = sum(vals.values())
    hide = rng.rand(len(regions), len(MONTHS)) < blank_share
    rec = []
    for i, mo in enumerate(MONTHS):
        rec.append((mo, "Total", ("Retail sales",), total[i], "A"))
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
