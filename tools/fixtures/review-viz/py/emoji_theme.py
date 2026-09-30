"""Theme x rating with emojis and accents; recompute every cell with pandas; a rating level with < 5 texts."""
from common import *
import re, math, unicodedata, pandas as pd
rng = random.Random(21)
phr = ["Great stay 😀😀", "terrible 👎 service", "love it ❤️❤️", "très bien, hôtel propre", "café was cold ☕",
       "great café 🎉", "slow service 🐢", "clean room ✨", "noisy room 😡", "friendly staff 🙂", "great breakfast 🥐",
       "🔥🔥🔥", "hôtel trop cher", "naïve décor but clean room"]
rows = []
for i in range(300):
    r = rng.choice([2, 3, 4, 5, 5, 4]) if i != 7 and i != 99 and i != 150 else 1   # rating 1 on 3 texts only
    rows.append(["2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)), str(r), rng.choice(phr)])
data = csv_bytes(["review_date", "rating", "title"], rows)
PLAN = {"goal": "What do reviewers say at each rating?", "kind": "survey",
        "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                    {"name": "rating", "semantic_type": "rating", "role": "target"},
                    {"name": "title", "semantic_type": "free_text", "role": "driver"}],
        "operations": [], "analyses": [{"type": "themes", "columns": ["title"]}],
        "charts": [{"kind": "theme_rating_heatmap", "columns": ["title", "rating"], "why": "x"}]}
rep = run(data, "emoji.csv", PLAN)
summary(rep)
print("flagged:", rep["privacy"]["flagged"])
df = clean_df(rep)
for c in rep["viz"]["charts"]:
    if c["chart"] != "theme_rating_heatmap":
        continue
    d = c["data"]
    print("rows:", d["rows"]); print("cols:", d["cols"]); print("summary:", c["summary"]); print("supp:", c["suppressed"])
    tx = df["title"].astype(str).str.strip(); rt = pd.to_numeric(df["rating"])
    has = tx != ""
    bad = []
    for i, w in enumerate(d["rows"]):
        def uses(t):
            toks = [x for x in re.findall(r"[^\W\d_](?:[^\W\d_]|'){2,}", unicodedata.normalize("NFC", t).lower()) if x not in NB._STOP]
            return (w in set(" ".join(p) for p in zip(toks, toks[1:]))) if " " in w else (w in set(toks))
        u = tx[has].map(uses)
        for j, lv in enumerate(d["cols"][:-1]):
            base = int((rt[has] == float(lv)).sum()); hits = int((u & (rt[has] == float(lv))).sum())
            got = d["values"][i][j]
            if base < 5:
                if got is not None or d["text"][i][j] != "<5": bad.append((w, lv, got))
            elif abs(got - 100.0 * hits / base) > 1e-6: bad.append((w, lv, got, 100.0 * hits / base))
    print("cell mismatches:", bad)
    # complementary disclosure: the suppressed rating's hits from 'all' and the shown cells (values and n are in the record)
    j1 = d["cols"].index("1")
    for i, w in enumerate(d["rows"][:4]):
        allv, alln = d["values"][i][-1], d["n"][i][-1]
        if alln is None:
            print("  word %-10s 'all' is %s with no n: the rating-1 hits cannot be worked back" % (w, d["text"][i][-1]))
            continue
        shown = sum(d["values"][i][j] * d["n"][i][j] / 100 for j in range(len(d["cols"]) - 1) if d["values"][i][j] is not None)
        print("  word %-10s rating-1 hits recovered: %.6f" % (w, allv * alln / 100 - shown))
    rt1 = df[(pd.to_numeric(df["rating"]) == 1)]["title"].tolist()
    print("  the 3 rating-1 texts:", rt1)
