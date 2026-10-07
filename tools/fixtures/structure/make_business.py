"""
A corpus of ordinary BUSINESS tables in plain layout (wave 5e): date, branch (or region or store), optionally a product, and one
measure (units, an amount); no metadata columns, no flags, no publisher. The file a shop, a clinic or a help desk exports.
The engine's headline for such a file is the plain sum of its detail rows, or the "Total" row where the file has one (never both);
it is never refused and never misread.

Drawn per file from a seed: monthly, weekly (Mondays) or daily dates; small counts (0 to 3 a row), units or large amounts with
cents; 2 to 5 branches, no product or 1 to 3; with and without a total row (named Total, All, All regions or Grand total, in
the branch column, the product column or both); rows sorted by date or shuffled. The total rows are exact sums of the detail
rows. `truth` holds what the plain sum is, from the spec alone (never from the engine).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Tuple

import numpy as np

BRANCH_POOL = ["North", "South", "East", "West", "Central", "Harbour", "Uptown", "Riverside"]
PRODUCT_POOL = ["Widgets", "Gadgets", "Gizmos"]
DATE_NAMES = ["Date", "Order Date", "date", "Day", "Sale date"]
BRANCH_NAMES = ["Branch", "Region", "Store", "Location", "branch"]
MEASURES = {"units": ["Units", "Qty", "Quantity", "Units sold"], "small": ["Tickets", "Refunds", "Complaints", "Incidents"],
            "amount": ["Amount", "Sales", "Revenue", "Total sales"]}
TOTAL_NAMES = ["Total", "Total", "Total", "All", "All regions", "Grand total"]


def _dates(freq: str, n: int, start: dt.date) -> List[dt.date]:
    if freq == "month":
        out, y, m = [], start.year, start.month
        for _ in range(n):
            out.append(dt.date(y, m, 1))
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out
    step = 7 if freq == "week" else 1
    return [start + dt.timedelta(days=step * i) for i in range(n)]


def spec_of(i: int) -> Dict[str, Any]:
    rng = np.random.RandomState(1000 + i)
    freq = ("month", "month", "month", "week", "day")[i % 5] if i % 10 else "month"
    n = {"month": int(rng.randint(30, 49)), "week": int(rng.randint(112, 131)), "day": int(rng.randint(500, 701))}[freq]
    kind = ("small", "units", "amount")[int(rng.randint(0, 3))]
    nb = int(rng.randint(2, 6))
    npd = int(rng.choice([0, 0, 1, 2, 3]))
    if freq == "day":
        nb, npd = min(nb, 3), min(npd, 2)
    total = [None, None, "branch", "branch", "product", "both"][int(rng.randint(0, 6))]
    if total in ("product", "both") and npd == 0:
        total = "branch"
    return {"i": i, "freq": freq, "periods": n, "kind": kind, "branches": [str(x) for x in rng.choice(BRANCH_POOL, nb, replace=False)],
            "products": PRODUCT_POOL[:npd], "total": total, "total_name": TOTAL_NAMES[int(rng.randint(0, len(TOTAL_NAMES)))],
            "shuffled": bool(rng.rand() < 0.4), "date_name": DATE_NAMES[int(rng.randint(0, len(DATE_NAMES)))],
            "branch_name": BRANCH_NAMES[int(rng.randint(0, len(BRANCH_NAMES)))],
            "measure_name": MEASURES[kind][int(rng.randint(0, len(MEASURES[kind])))], "seed": 7000 + i}


def make(i: int) -> Tuple[bytes, bytes, Dict[str, Any]]:
    """(the file, the file with its total rows taken out, the spec) of corpus file i."""
    sp = spec_of(i)
    rng = np.random.RandomState(sp["seed"])
    start = {"month": dt.date(2021, 1, 1), "week": dt.date(2021, 1, 4), "day": dt.date(2021, 1, 1)}[sp["freq"]]
    dates = _dates(sp["freq"], sp["periods"], start)
    prods = sp["products"] or [None]
    cells: Dict[Tuple[str, Any], np.ndarray] = {}
    for bi, b in enumerate(sp["branches"]):
        for pj, p in enumerate(prods):
            t = np.arange(sp["periods"], dtype=float)
            trend = 1.0 + (0.004 if sp["freq"] == "month" else 0.0008 if sp["freq"] == "week" else 0.0001) * t * (1 + 0.5 * rng.rand())
            if sp["kind"] == "small":
                lam = 0.2 + 1.3 * rng.rand()
                v = np.minimum(rng.poisson(lam * trend), 3).astype(float)
            elif sp["kind"] == "units":
                v = np.round(np.maximum(1, (40 + 120 * rng.rand() + 25 * bi + 15 * pj) * trend * (1 + 0.12 * rng.randn(sp["periods"]))))
            else:
                base = 800 + 5000 * rng.rand() + 600 * bi + 300 * pj
                v = np.round(np.maximum(1.0, base * trend * (1 + 0.1 * rng.randn(sp["periods"]))) * 100) / 100.0
            cells[(b, p)] = v
    fmt = (lambda x: "%.2f" % x) if sp["kind"] == "amount" else (lambda x: "%d" % int(round(x)))

    def rows(with_total: bool) -> List[List[str]]:
        out: List[List[str]] = []
        tb = with_total and sp["total"] in ("branch", "both")
        tp = with_total and sp["total"] in ("product", "both")
        for k, d in enumerate(dates):
            ds = d.isoformat()
            for b in sp["branches"]:
                for p in prods:
                    out.append([ds, b] + ([p] if p else []) + [fmt(cells[(b, p)][k])])
                if tp:
                    out.append([ds, b, sp["total_name"] if sp["total"] == "product" else "Total",
                                fmt(sum(cells[(b, p)][k] for p in prods))])
            if tb:
                for p in prods:
                    out.append([ds, sp["total_name"] if sp["total"] == "branch" else "All", ] + ([p] if p else []) +
                               [fmt(sum(cells[(b, p)][k] for b in sp["branches"]))])
                if tp:
                    out.append([ds, "All", "Total", fmt(sum(cells[k2][k] for k2 in cells))])
        return out

    head = [sp["date_name"], sp["branch_name"]] + (["Product"] if sp["products"] else []) + [sp["measure_name"]]

    def render(r: List[List[str]]) -> bytes:
        if sp["shuffled"]:
            rr = np.random.RandomState(sp["seed"] + 1)
            r = [r[j] for j in rr.permutation(len(r))]
        return ("\n".join([",".join(head)] + [",".join(x) for x in r]) + "\n").encode("utf-8")
    full, detail = rows(True), rows(False)
    truth = {"detail_by_date": {d.isoformat(): float(sum(cells[k][i2] for k in cells)) for i2, d in enumerate(dates)}}
    sp = dict(sp, has_total=sp["total"] is not None, truth=truth)
    return render(full), render(detail), sp
