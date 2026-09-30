"""PRIVACY: person names inside a SHORT text column (review titles, <4 words: not flagged as free text by the scan)."""
from common import *
import re
rng = random.Random(11)
staff = ["Sarah", "Priya", "Mohammed", "Jonas"]
first = ["Emily", "Rashid", "Olga", "Tariq", "Hannah", "Diego", "Mei", "Kwame"]
last = ["Jones", "Khan", "Petrova", "Hassan", "Schmidt", "Lopez", "Wong", "Mensah"]
tmpl = ["Thanks {s}!", "{s} was great", "Ask for {s}", "Rude staff", "Lovely room", "Too noisy",
        "{s} rocks", "Great value", "Never again", "Thank you {s}"]
rows = []
for i in range(360):
    k = i // 15
    y, m = 2024 + k // 12, k % 12 + 1
    s = rng.choice(staff); cf = rng.choice(first); cl = rng.choice(last)
    rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), cf + " " + cl, str(rng.randint(1, 5)),
                 rng.choice(["rooms", "food", "spa"]), rng.choice(tmpl).format(s=s)])
data = csv_bytes(["review_date", "guest", "rating", "department", "review_title"], rows)
PLAN = {"goal": "What do guests say at each rating? review words", "kind": "survey",
        "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                    {"name": "rating", "semantic_type": "rating", "role": "target"},
                    {"name": "department", "semantic_type": "category", "role": "segment"},
                    {"name": "review_title", "semantic_type": "free_text", "role": "driver"}],
        "operations": [], "analyses": []}
low = {n.lower() for n in staff + first + last}

def scan(label, rep):
    print("\n=== %s" % label)
    summary(rep)
    print(" flagged:", [(f.get("column"), f.get("kind"), f.get("decision")) for f in (rep.get("privacy") or {}).get("flagged") or []])
    for c in (rep.get("viz") or {}).get("charts") or []:
        hit = sorted({w for s in strings(c) for w in re.findall(r"[A-Za-z]+", s) if w.lower() in low})
        if hit:
            print(" NAMES in", c["chart"], hit)
            if c["chart"] == "theme_rating_heatmap":
                print("   rows:", c["data"]["rows"]); print("   summary:", c["summary"]); print("   table row 0:", c["table"]["rows"][0])
    rfa = NB.results_for_ai(rep)
    hit = sorted({w for s in strings(rfa.get("charts") or []) for w in re.findall(r"[A-Za-z]+", s) if w.lower() in low})
    print(" NAMES in results_for_ai.charts:", hit)
    return rep

scan("auto (no plan)", run(data, "t.csv"))
scan("plan, no charts (engine picks, goal mentions review)", run(data, "t.csv", PLAN))
rep = scan("plan picks theme_rating_heatmap", run(data, "t.csv", dict(PLAN, charts=[{"kind": "theme_rating_heatmap", "columns": ["review_title", "rating"], "why": "words by rating"}])))
rep2 = scan("plan picks theme chart + themes analysis", run(data, "t.csv", dict(PLAN, analyses=[{"type": "themes", "columns": ["review_title"]}], charts=[{"kind": "theme_rating_heatmap", "columns": ["review_title", "rating"], "why": "words by rating"}])))
for a in (rep2.get("ai_analyses") or {}).get("items") or []:
    print(" themes analysis rows:", (a.get("table") or {}).get("rows"))
# guest (a person name column) kept by the visitor
scan("guest kept + theme chart", run(data, "t.csv", dict(PLAN, charts=[{"kind": "theme_rating_heatmap", "columns": ["review_title", "rating"], "why": "words"}]), guest="keep"))
json.dump(rep["viz"], open(os.path.join(OUT, "titles_viz.json"), "w"), indent=1)
