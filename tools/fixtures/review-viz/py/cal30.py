"""A 30-year calendar (trimming), a flow and a level (diverging), with negatives, and tier recomputation by numpy."""
from common import *
import math, numpy as np, pandas as pd
rng = random.Random(30)
rows = []
for y in range(1996, 2026):
    for m in range(1, 13):
        n = 5 if not (y == 2019 and m == 7) else 3            # one month on 3 rows
        if y == 2020 and m == 4:
            n = 0                                               # a month with 0 rows
        for _ in range(n):
            amt = rng.uniform(-40, 160) * (1 + (y - 1996) * 0.03)  # negative amounts too
            idx = 100 + (y - 1996) * 2 + rng.uniform(-3, 3) - (60 if y == 2008 else 0)
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), "%.2f" % amt, "%.3f" % idx])
data = csv_bytes(["date", "net_sales", "price_index"], rows)
PLAN = {"goal": "How has net sales moved by month and season?", "kind": "time_series_panel", "primary": "net_sales",
        "columns": [{"name": "date", "semantic_type": "date", "role": "date"},
                    {"name": "net_sales", "semantic_type": "flow_amount", "role": "target", "unit": "EUR"},
                    {"name": "price_index", "semantic_type": "level", "role": "driver"}],
        "operations": [], "analyses": [],
        "charts": [{"kind": "calendar_heatmap", "columns": ["net_sales"], "why": "x"},
                   {"kind": "calendar_heatmap", "columns": ["price_index"], "why": "y"}]}
rep = run(data, "cal30.csv", PLAN, as_of="2026-01-10")
summary(rep)
df = clean_df(rep); df["ym"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m")
for c in rep["viz"]["charts"]:
    d = c["data"]; col = c["inputs"]["columns"][0]
    v = pd.to_numeric(df[col])
    print("\n", c["title"], "|", c["subtitle"]); print(" years:", d["rows"][0], "..", d["rows"][-1], len(d["rows"]), "bytes", NV.record_bytes(c))
    print(" summary:", c["summary"]); print(" legend:", d["legend"])
    flow = c["measure"]["kind"] != "change_pct"
    bad = []
    vals = []
    for i, y in enumerate(d["rows"]):
        row = []
        for j in range(12):
            ym = "%s-%02d" % (y, j + 1); g = v[df["ym"] == ym]
            if flow:
                n = len(g); want = math.fsum(g) if n else None
            else:
                p = NB._shift_month(ym, -1); gp = v[df["ym"] == p]
                n = min(len(g), len(gp)) if len(g) and len(gp) else 0
                want = 100 * (g.mean() / gp.mean() - 1) if n else None
            got, t = d["values"][i][j], d["text"][i][j]
            if n == 0: ok = got is None and t == ""
            elif n < 5: ok = got is None and t == "<5"
            else: ok = got is not None and abs(got - want) <= 1e-6 * max(1, abs(want))
            if not ok: bad.append((ym, n, got, want, t))
            row.append(got if n >= 5 else None)
        vals.append(row)
    # tiers: tertiles by numpy linear percentiles, as the spec says; and the share of cells in each tier
    if flow:
        sh = [x for r in vals for x in r if x is not None]; q1, q2 = np.percentile(sh, [100/3, 200/3])
        want_t = [[0 if x is None else 1 if x <= q1 else 2 if x <= q2 else 3 for x in r] for r in vals]
    else:
        mg = [abs(x) for r in vals for x in r if x is not None and x != 0]; q1, q2 = np.percentile(mg, [100/3, 200/3])
        want_t = [[0 if (x is None or x == 0) else (1 if abs(x) <= q1 else 2 if abs(x) <= q2 else 3) * (1 if x > 0 else -1) for x in r] for r in vals]
    print(" tiers equal numpy:", want_t == d["tier"], "| cell mismatches:", bad[:5])
    from collections import Counter
    print(" tier counts:", sorted(Counter(t for r in d["tier"] for t in r).items()))
    # a text vs value check: the text is the formatter of the value
    tv = [(d["text"][i][j], d["values"][i][j]) for i in range(len(d["rows"])) for j in range(12) if d["values"][i][j] is not None]
    fmt = NB._fmt if flow else (lambda x: NV._chg(NV._r6(x), "%"))
    print(" texts == fmt(value):", all(t == fmt(x) for t, x in tv))
