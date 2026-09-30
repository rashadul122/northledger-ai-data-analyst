"""brand_ratings_2022.csv: star counts per brand, derived from real reviews, for the weighted-ranking test
(tools/test_nl_browser.py, test_eval_a_ranking_of_averages_weighs_each_brand_by_its_rows).

Source: Amazon Reviews 2023, Video Games (McAuley Lab, UC San Diego; Hou et al. 2024), as
.work/eval/prep_datasets.py cut it into .work/eval/data/qual_amazon_vg_reviews.csv (a seeded 1.5% sample). The
reviews are released for research and are not republished here: this file holds AGGREGATED COUNTS ONLY, no review
text, title, reviewer or product id. For each brand with a review dated in 2022, the number of 1, 2, 3, 4 and 5 star
reviews that year (the rows the engine keeps: a row that repeats an earlier one exactly is counted once, as the
engine's cleaner sets the repeat aside; a row with no brand is left out).

    python tools/fixtures/eval/make_brand_ratings.py
"""
import csv
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".work", "eval", "data", "qual_amazon_vg_reviews.csv"))
OUT = os.path.join(HERE, "brand_ratings_2022.csv")

seen = set()
counts = {}
with open(SRC, encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        key = tuple(r.values())
        if key in seen:
            continue
        seen.add(key)
        brand, star = (r.get("brand") or "").strip(), (r.get("rating") or "").strip()
        if not r.get("review_date", "").startswith("2022") or not brand or star not in ("1", "2", "3", "4", "5"):
            continue
        counts.setdefault(brand, Counter())[int(star)] += 1
with open(OUT, "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["brand", "stars_1", "stars_2", "stars_3", "stars_4", "stars_5"])
    for b in sorted(counts):
        w.writerow([b] + [counts[b][s] for s in (1, 2, 3, 4, 5)])
print("%s: %d brands, %d reviews" % (os.path.relpath(OUT), len(counts), sum(sum(c.values()) for c in counts.values())))
