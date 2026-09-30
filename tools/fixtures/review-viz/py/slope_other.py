"""SUPPRESSION: 'other' made of one small level with 2 rows before and 3 after (5 in all): the slope prints each window's
total of 'other', resting on 2 and on 3 rows. Also the pareto 'other' of one small level."""
from common import *
import math, pandas as pd
rng = random.Random(9)
stores = ["North", "South", "East", "West", "Kiosk"]
rows = []
for k in range(24):                                # 2024-03 .. 2026-02: exactly the two windows
    y, m = 2024 + (k + 2) // 12, (k + 2) % 12 + 1
    for st in stores:
        if st == "Kiosk":
            n = 1 if k in (1, 5) or k in (13, 17, 21) else 0     # 2 rows before, 3 in the latest 12 months
        else:
            n = 6
        for _ in range(n):
            rev = round(rng.uniform(50, 150), 2) if st != "Kiosk" else round(rng.uniform(900, 1100), 2)
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), st, "%.2f" % rev])
data = csv_bytes(["order_date", "store", "revenue"], rows)
PLAN = {"goal": "Which stores drove revenue?", "kind": "transactions", "primary": "revenue",
        "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                    {"name": "store", "semantic_type": "category", "role": "segment"},
                    {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "USD"}],
        "operations": [], "analyses": [],
        "charts": [{"kind": "slope", "columns": ["store", "revenue"], "why": "x"},
                   {"kind": "contribution_waterfall", "columns": ["store", "revenue"], "why": "x"}]}
rep = run(data, "k.csv", PLAN, as_of="2026-03-15")
summary(rep)
df = clean_df(rep); df["v"] = pd.to_numeric(df["revenue"]); df["ym"] = df["order_date"].str[:7]
b = rep["scenarios"]["basis"]; print("windows", b["windows"], "levels", b["segment"])
k = df[df["store"] == "Kiosk"]
pm = NB._month_range(*b["windows"]["prior"]); lm = NB._month_range(*b["windows"]["latest"])
print("Kiosk rows prior", int(k["ym"].isin(pm).sum()), "latest", int(k["ym"].isin(lm).sum()),
      "totals", round(k[k["ym"].isin(pm)]["v"].sum(), 2), round(k[k["ym"].isin(lm)]["v"].sum(), 2))
for c in rep["viz"]["charts"]:
    if c["chart"] == "slope":
        print("SLOPE rows:", [(r["label"], r["a_text"], r["b_text"], r["change_text"]) for r in c["data"]["rows"]])
        print("  suppressed:", c["suppressed"]); print("  summary:", c["summary"])
    if c["chart"] == "contribution_waterfall":
        print("WF steps:", [(s["label"], s["text"]) for s in c["data"]["steps"]]); print("  suppressed:", c["suppressed"])
# the scenarios block's own drove table (existing), for comparison
import nl_scenarios as NS
t = NS.table(rep["scenarios"]); print("SCENARIOS table (existing):", t and t["rows"])
# pareto on the same file with 8+ levels: add levels so a pareto applies
stores2 = ["S%02d" % i for i in range(9)]
rows2 = [[r[0], stores2[i % 9], r[2]] for i, r in enumerate(rows) if r[1] != "Kiosk"] + [["2025-06-01", "Tiny", "4000.00"], ["2025-07-01", "Tiny", "3100.00"]]
rep2 = run(csv_bytes(["order_date", "store", "revenue"], rows2), "p.csv", dict(PLAN, charts=[{"kind": "pareto", "columns": ["store", "revenue"], "why": "x"}]), as_of="2026-03-15")
summary(rep2)
for c in [c for c in rep2["viz"]["charts"] if c["chart"] == "pareto"]:
    print("PARETO other:", c["data"]["other"], "| table tail:", c["table"]["rows"][-3:], "| supp:", c["suppressed"])
print([r for r in rep2["viz"]["refused"]])
