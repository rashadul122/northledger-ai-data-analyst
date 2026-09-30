"""A withheld column named 'phone' and ticket subjects that use the word 'phone': is the whole chart silently dropped
from results_for_ai while the page's rep.viz keeps it?"""
from common import *
rng = random.Random(8)
subj = ["Phone not working", "Refund request", "Phone battery issue", "Late delivery", "Wrong item sent",
        "Phone screen cracked", "Cannot log in", "Billing question"]
rows = []
for i in range(300):
    rows.append(["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), "+1 416 555 %04d" % rng.randint(0, 9999),
                 str(rng.randint(1, 5)), rng.choice(subj)])
data = csv_bytes(["opened", "phone", "csat", "subject"], rows)
PLAN = {"goal": "What do customers write about at each satisfaction score?", "kind": "survey",
        "columns": [{"name": "opened", "semantic_type": "date", "role": "date"},
                    {"name": "csat", "semantic_type": "rating", "role": "target"},
                    {"name": "subject", "semantic_type": "free_text", "role": "driver"}],
        "operations": [], "analyses": [],
        "charts": [{"kind": "theme_rating_heatmap", "columns": ["subject", "csat"], "why": "x"},
                   {"kind": "crosstab_heatmap", "columns": ["subject", "csat"], "why": "y"}]}
rep = run(data, "tickets.csv", PLAN)
summary(rep)
print("flagged:", [(f["column"], f["decision"]) for f in rep["privacy"]["flagged"]])
rfa = NB.results_for_ai(rep)
print("rep.viz ids:", [c["id"] for c in rep["viz"]["charts"]])
print("results_for_ai viz ids:", [c.get("id") for c in rfa.get("charts") or [] if NV.is_record(c)])
print("all results_for_ai chart ids:", [c.get("id") for c in rfa.get("charts") or []])
