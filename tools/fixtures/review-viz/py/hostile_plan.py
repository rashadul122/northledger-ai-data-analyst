from common import *
exec(open(os.path.join(HERE, "correct_tx.py")).read().split("data = make()")[0])
evil = [None, 5, "x", [], {"kind": "<script>alert(1)</script>", "columns": ["store"], "why": "<img src=x onerror=alert(1)>"},
        {"kind": "pareto", "columns": [["store"]], "why": 7}, {"kind": "pareto", "columns": "store"},
        {"kind": "pareto", "columns": ["store", "revenue"], "why": "<b>why</b> " * 100},
        {"kind": "pareto", "columns": ["store", "revenue"], "why": "dup"},
        {"kind": "__proto__", "columns": ["__proto__"]}, {"kind": "correlation_heatmap", "columns": ["revenue"] * 12},
        {"kind": "crosstab_heatmap", "columns": ["store", "Store", "STORE"]}] + [{"kind": "slope", "columns": ["store"]}] * 1000
rep = run(make(), "tx.csv", dict(PLAN, charts=evil), as_of="2026-03-15")
print("ok", rep["ok"], rep.get("error"))
print("ai_plan.charts kept:", len((rep.get("ai_plan") or {}).get("charts") or []))
summary(rep)
print("why of built:", [c["why"][:80] for c in rep["viz"]["charts"]])
