from common import *
import re
exec(open(os.path.join(HERE, "privacy_titles.py")).read().split("def scan")[0])
rep = run(data, "t.csv")
print("engine charts (non-V):", [(c["id"], c.get("rule")) for c in rep["charts"] if c.get("rule") != "V"])
for c in rep["charts"]:
    if c.get("rule") != "V" and "review_title" in json.dumps(c):
        print("  existing chart touching review_title:", c["id"], json.dumps(c.get("data"))[:300])
# staff column holding single first names, withheld: are those words filtered from themes?
rows2 = [r + [r[4].split()[1].strip("!") if r[4].split()[0] in ("Thanks", "Ask", "Thank") else "Sarah"] for r in rows]
d2 = csv_bytes(["review_date", "guest", "rating", "department", "review_title", "staff_member"], rows2)
rep2 = run(d2, "t2.csv", dict(PLAN, charts=[{"kind": "theme_rating_heatmap", "columns": ["review_title", "rating"], "why": "w"}]), staff_member="withhold")
print("flagged:", [(f.get("column"), f.get("kind"), f.get("decision")) for f in rep2["privacy"]["flagged"]])
summary(rep2)
for c in rep2["viz"]["charts"]:
    if c["chart"] == "theme_rating_heatmap":
        print(" rows:", c["data"]["rows"])
