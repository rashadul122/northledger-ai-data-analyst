"""TIES: a rows-counted pareto where levels tie on rows (the engine's ranked chart is its reconciliation);
a crosstab/calendar summary with ties; a single category level."""
from common import *
rng = random.Random(4)
# 10 brands, row counts with ties: Zeta 30, Alpha 30, Mike 20, Bravo 20, Echo 20, ... ; insertion order puts Zeta first
counts = [("Zeta", 30), ("Alpha", 30), ("Mike", 20), ("Bravo", 20), ("Echo", 20), ("Kilo", 12), ("Delta", 12),
          ("Lima", 8), ("Golf", 8), ("Hotel", 6)]
rows = []
for b, n in counts:
    for i in range(n):
        rows.append(["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), b, str(rng.randint(1, 5))])
rng.shuffle(rows)
data = csv_bytes(["review_date", "brand", "stars"], rows)
PLAN = {"goal": "Which brands draw the reviews? top brands", "kind": "survey",
        "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                    {"name": "brand", "semantic_type": "entity", "role": "entity"},
                    {"name": "stars", "semantic_type": "rating", "role": "target"}],
        "operations": [], "analyses": [], "charts": [{"kind": "pareto", "columns": ["brand"], "why": "x"}]}
rep = run(data, "b.csv", PLAN)
summary(rep)
rk = next((c for c in rep["charts"] if c["id"] == "ranked.brand"), None)
print("engine ranked.brand bars:", rk and [(b["label"], b["rows"]) for b in rk["data"]["bars"]], "total", rk and rk["data"].get("total"))
for c in rep["viz"]["charts"]:
    if c["chart"] == "pareto":
        print("pareto bars:", [(b["label"], b["text"]) for b in c["data"]["bars"]], c["data"]["k80"])
# single category level
rows1 = [["2024-%02d-01" % (k % 12 + 1) if k < 12 else "2025-%02d-01" % (k % 12 + 1), "Only", "%.2f" % (100 + k)] for k in range(24) for _ in range(6)]
rep1 = run(csv_bytes(["order_date", "store", "revenue"], rows1), "one.csv",
           {"goal": "x", "kind": "transactions", "primary": "revenue", "columns": [
               {"name": "order_date", "semantic_type": "date", "role": "date"},
               {"name": "store", "semantic_type": "category", "role": "segment"},
               {"name": "revenue", "semantic_type": "flow_amount", "role": "target"}], "operations": [], "analyses": [],
            "charts": [{"kind": k, "columns": c, "why": "x"} for k, c in [("contribution_waterfall", ["store", "revenue"]),
                        ("slope", ["store"]), ("crosstab_heatmap", ["store", "store"]), ("change_heatmap", ["store", "revenue"]),
                        ("group_ranges", ["revenue", "store"]), ("pareto", ["store"])]]}, as_of="2026-01-15")
print("\nSINGLE LEVEL:"); summary(rep1)
