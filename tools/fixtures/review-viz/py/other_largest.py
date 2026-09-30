"""A new store with 2 soft-launch rows in the prior window and 120 rows after: folded into 'other' (its prior total rests
on 2 rows). Does the waterfall's summary still name 'the largest contributions' correctly?"""
from common import *
rng = random.Random(12)
rows = []
for k in range(24):
    y, m = 2024 + (k + 2) // 12, (k + 2) % 12 + 1
    for st in ["North", "South", "East", "West", "Flagship"]:
        if st == "Flagship":
            n = (1 if k in (9, 10) else 0) if k < 12 else 10
        else:
            n = 6
        for _ in range(n):
            base = 100 if st != "Flagship" else 400
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), st, "%.2f" % (base * rng.uniform(0.9, 1.1) * (1.03 if k >= 12 and st == "North" else 1))])
data = csv_bytes(["order_date", "store", "revenue"], rows)
PLAN = {"goal": "Which stores drove revenue?", "kind": "transactions", "primary": "revenue",
        "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                    {"name": "store", "semantic_type": "category", "role": "segment"},
                    {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "USD"}],
        "operations": [], "analyses": [], "charts": [{"kind": "contribution_waterfall", "columns": ["store", "revenue"], "why": "x"}]}
rep = run(data, "f.csv", PLAN, as_of="2026-03-15")
summary(rep)
print("scenario segment:", rep["scenarios"]["basis"]["segment"])
for c in rep["viz"]["charts"]:
    print("steps:", [(s["label"], s["text"]) for s in c["data"]["steps"]])
    print("summary:", c["summary"])
    print("suppressed:", c["suppressed"])
