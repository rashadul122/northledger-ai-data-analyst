"""A KEPT person-name column that the plan makes its segment: the scenarios block may break the change down by it;
every chart must refuse it (never name it or its levels)."""
import os, sys
if len(sys.argv) > 1 and sys.argv[1] == "lax":
    os.environ["NL_BROWSER_STRICT"] = ""
from common import *
import re
if len(sys.argv) > 1 and sys.argv[1] == "lax":
    os.environ.pop("NL_BROWSER_STRICT", None)
rng = random.Random(2)
reps = ["Sarah Jones", "Priya Khan", "Tariq Hassan", "Olga Petrova"]
rows = []
for k in range(26):
    y, m = 2024 + k // 12, k % 12 + 1
    for r in reps:
        for _ in range(7):
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), r, rng.choice(["web", "store"]), "%.2f" % rng.uniform(20, 90)])
data = csv_bytes(["order_date", "sales_rep", "channel", "revenue"], rows)
PLAN = {"goal": "Which sales reps drove revenue?", "kind": "transactions", "primary": "revenue",
        "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                    {"name": "sales_rep", "semantic_type": "category", "role": "segment"},
                    {"name": "channel", "semantic_type": "category", "role": "segment"},
                    {"name": "revenue", "semantic_type": "flow_amount", "role": "target"}],
        "operations": [], "analyses": []}
for label, plan in (("auto", PLAN), ("ai asks by rep", dict(PLAN, charts=[
        {"kind": "contribution_waterfall", "columns": ["Sales Rep", "revenue"], "why": "x"},
        {"kind": "change_heatmap", "columns": ["SALES-REP"], "why": "x"},
        {"kind": "slope", "columns": [], "why": "x"}]))):
    rep = run(data, "reps.csv", plan, as_of="2026-03-15", sales_rep="keep")
    print("\n===", label, "| strict" if os.environ.get("NL_BROWSER_STRICT") else "| lax")
    print("error:", rep.get("error"))
    print("flagged:", [(f["column"], f["decision"]) for f in rep["privacy"]["flagged"]])
    print("scenarios segment:", ((rep.get("scenarios") or {}).get("basis") or {}).get("segment"))
    summary(rep)
    hits = sorted({w for c in (rep.get("viz") or {}).get("charts") or [] for s in strings(c) for w in re.findall(r"Sarah|Priya|Tariq|Olga|sales_rep|Sales Rep", s)})
    print("names in viz:", hits)
