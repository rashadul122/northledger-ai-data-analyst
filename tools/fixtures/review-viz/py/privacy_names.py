"""PRIVACY: person names inside a free-text column, tokenised by the theme x rating heatmap."""
from common import *
import re
rng = random.Random(7)
staff = ["Sarah", "Priya", "Mohammed", "Jonas"]
cust_first = ["Emily", "Rashid", "Olga", "Tariq", "Hannah", "Diego", "Mei", "Kwame"]
cust_last = ["Jones", "Khan", "Petrova", "Hassan", "Schmidt", "Lopez", "Wong", "Mensah"]
tmpl = ["{s} at the front desk was so helpful", "great service from {s} today", "{s} sorted my refund quickly",
        "waited ages, nobody helped, not even {s}", "the room was clean and {s} was friendly",
        "slow checkout and rude staff", "lovely breakfast and quiet room", "noisy street, thin walls",
        "{c} here, my booking got lost again", "asked for {s} and got great help"]
rows = []
for i in range(360):
    y, m = 2024 + (i // 15) // 12, (i // 15) % 12 + 1
    s = rng.choice(staff); cf = rng.choice(cust_first); cl = rng.choice(cust_last)
    t = rng.choice(tmpl).format(s=s, c=cf + " " + cl)
    rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), cf + " " + cl, "%s.%s@example.com" % (cf.lower(), cl.lower()),
                 str(rng.randint(1, 5)), rng.choice(["rooms", "food", "spa"]), t])
data = csv_bytes(["review_date", "customer_name", "email", "rating", "department", "review_text"], rows)
open(os.path.join(OUT, "reviews_names.csv"), "wb").write(data)

PLAN = {"goal": "What do guests say at each rating?", "kind": "survey",
        "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                    {"name": "rating", "semantic_type": "rating", "role": "target"},
                    {"name": "department", "semantic_type": "category", "role": "segment"},
                    {"name": "review_text", "semantic_type": "free_text", "role": "driver"}],
        "operations": [], "analyses": []}
names_lc = {n.lower() for n in staff + cust_first + cust_last}

def scan(label, rep):
    print("\n=== %s" % label)
    summary(rep)
    print(" privacy.flagged:", [(f.get("column"), f.get("kind"), f.get("decision")) for f in (rep.get("privacy") or {}).get("flagged") or []])
    hits = set()
    for c in (rep.get("viz") or {}).get("charts") or []:
        for s in strings(c):
            for w in re.findall(r"[A-Za-z]+", s):
                if w.lower() in names_lc:
                    hits.add((c["chart"], w))
    print(" NAMES IN viz records:", sorted(hits))
    rfa = NB.results_for_ai(rep)
    h2 = set()
    for s in strings(rfa.get("charts") or []):
        for w in re.findall(r"[A-Za-z]+", s):
            if w.lower() in names_lc:
                h2.add(w)
    print(" NAMES IN results_for_ai.charts:", sorted(h2))
    th = [c for c in (rep.get("viz") or {}).get("charts") or [] if c["chart"] == "theme_rating_heatmap"]
    if th:
        print(" theme rows:", th[0]["data"]["rows"])
        print(" theme summary:", th[0]["summary"])
    return rfa

r1 = scan("no plan (engine picks), default decisions", run(data, "reviews_names.csv"))
r2 = scan("plan with analyses=[] and no charts (engine picks)", run(data, "reviews_names.csv", PLAN))
r3 = scan("plan chooses theme_rating_heatmap", run(data, "reviews_names.csv", dict(PLAN, charts=[{"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"], "why": "words"}])))
r4 = scan("plan + themes analysis + theme chart", run(data, "reviews_names.csv", dict(PLAN, analyses=[{"type": "themes", "columns": ["review_text"]}], charts=[{"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"], "why": "words"}])))
r5 = scan("customer_name KEPT, theme chart", run(data, "reviews_names.csv", dict(PLAN, charts=[{"kind": "theme_rating_heatmap", "columns": ["review_text", "rating"], "why": "words"}]), customer_name="keep"))
# what did the analysis see?
for a in ((run(data, "reviews_names.csv", dict(PLAN, analyses=[{"type": "themes", "columns": ["review_text"]}])).get("ai_analyses") or {}).get("items") or []):
    print("\nTHEMES ANALYSIS table rows:", a.get("table", {}).get("rows"))
