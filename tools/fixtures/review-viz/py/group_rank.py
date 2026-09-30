"""group_ranges: a small group with the highest average is shrunk below a big one; what does the summary say?"""
from common import *
rng = random.Random(5)
rows = []
for g, n, mu in (("Big", 400, 60), ("Mid", 200, 55), ("Small", 6, 75), ("Low", 300, 40)):
    for _ in range(n):
        rows.append(["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), g, "%.2f" % max(1, rng.gauss(mu, 25))])
data = csv_bytes(["order_date", "store", "basket"], rows)
PLAN = {"goal": "Compare basket size by store", "kind": "transactions",
        "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                    {"name": "store", "semantic_type": "category", "role": "segment"},
                    {"name": "basket", "semantic_type": "level", "role": "target"}],
        "operations": [], "analyses": [], "charts": [{"kind": "group_ranges", "columns": ["basket", "store"], "why": "x"}]}
rep = run(data, "g.csv", PLAN)
for c in rep["viz"]["charts"]:
    print([(r["label"], r["n"], r["texts"]["center"]) for r in c["data"]["rows"]])
    print("summary:", c["summary"]); print("subtitle:", c["subtitle"])
print(rep["viz"]["refused"])
