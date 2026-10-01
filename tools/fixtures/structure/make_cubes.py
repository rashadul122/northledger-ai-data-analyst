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
                     if float(val) == int(val) else "%.1f" % val, st, "", "", decimals])
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
        vals = ["%s %s" % (first[i % 10], last[(i * 3 + 1) % 10]) for i in range(labels)]
    else:
        vals = ["Segment %02d: %s" % (i, "services for regional %s and allied trades" % ("retail" if i % 2 else "wholesale"))
                for i in range(labels)]
    out = []
    for i in range(rows):
        mo = MONTHS[i % 36]
        lab = vals[rng.randint(len(vals))] if labels < rows else "Note number %05d about the visit" % i
        out.append(["%s-%02d" % (mo, 1 + i % 28), lab, "%.2f" % (20 + 80 * rng.rand())])
    return _csv(["date", header, "amount"], out)


ALL = {"partition": partition, "partition_suppressed": lambda: partition(0.10), "hierarchy": hierarchy,
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
