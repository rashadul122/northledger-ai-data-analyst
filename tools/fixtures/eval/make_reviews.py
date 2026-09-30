"""reviews_synthetic.csv: a SYNTHETIC product-review file in the shape of the evaluation's review sample, for the
scrubber and scenario-segment tests (tools/test_nl_browser.py, test_eval_c_* and test_eval_e_*).

Nothing here comes from the research-licensed review data (.work/eval/data/qual_amazon_vg_reviews.csv): no review,
title, brand, reviewer or product id is copied. Only the SHAPE is mirrored: the same eight columns in the same order
(review_date, rating, verified_purchase, helpful_votes, department, brand, review_title, review_text), department
names that are the generic store sections, a yes/no verified_purchase column that comes before department, and a
free-text review column in which a few reviews are one ordinary word. The live baseline of 30 Sep 2026 found one
review whose whole text was "this": with review_text withheld (the page's default), the scrubber then replaced every
"this" in the engine's own sentences ("a result at least [withheld] strong"). This file reproduces that case with
reviews written here: "this", "good", "free", "yes", "Excellent", "Great game".

Every text is made from the phrase lists below by a seeded generator (random.Random(20261001)), so the file is the
same on every run. Dates run from 2020-01-01 to 2023-03-31 (39 months, about 60 to 75 reviews a month).

    python tools/fixtures/eval/make_reviews.py           # write reviews_synthetic.csv
    python tools/fixtures/eval/make_reviews.py --check   # exit 1 if the file is not what this script writes
"""
import csv
import datetime
import io
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reviews_synthetic.csv")
SEED = 20261001

DEPARTMENTS = (("Video Games", 0.46), ("Computers", 0.26), ("All Electronics", 0.18),
               ("Cell Phones & Accessories", 0.06), ("Toys & Games", 0.03), ("Software", 0.01))
BRANDS = ["Brand %s%s" % (a, b) for a in "ABCDEFGHIJKLMNOP" for b in "QRST"]        # 64 made-up brand names
TITLES = ("Five stars", "Works well", "Good buy", "Not bad", "Fun for the family", "Stopped working", "As described",
          "Great value", "Disappointed", "Solid controller", "Would buy again", "Too small", "Fast shipping",
          "Kids love it", "Returned it", "Better than expected")
SENTENCES = ("The controller feels solid in the hand.", "Setup took about ten minutes.",
             "My kids play it every weekend.", "The battery lasts a long time.", "It stopped working after two weeks.",
             "Shipping was quick and the box was intact.", "Graphics look sharp on our television.",
             "The buttons are a little stiff.", "Great value for the price.",
             "Customer support replaced it without fuss.", "The cable is too short for our room.",
             "Sound quality is better than expected.", "It came with a scratch on the case.",
             "Easy to pair with the console.", "The game crashes on the third level.", "I would buy this again.",
             "The instructions were hard to follow.", "It fits the stand perfectly.", "The fan gets loud after an hour.",
             "My son uses it for school and for games.", "The colours are brighter than in the photos.",
             "It charges fully overnight.", "The menu is slow to respond.", "We gave one to our nephew as well.")
# whole reviews that are one ordinary word or two: a scrubber must never take these for personal data
ONE_WORD = ("this", "good", "free", "yes", "Excellent", "Great game")


def rows():
    rng = random.Random(SEED)
    out = []
    d, end = datetime.date(2020, 1, 1), datetime.date(2023, 3, 31)
    while d <= end:
        # about 75 reviews a month, a little fewer in the latest year
        per_day = 2 if d.year < 2022 or (d.year == 2022 and d.month < 4) else 1
        for _ in range(per_day + rng.randint(0, 1)):
            dept = rng.choices([x for x, _w in DEPARTMENTS], weights=[w for _x, w in DEPARTMENTS])[0]
            rating = rng.choices([1, 2, 3, 4, 5], weights=[6, 4, 8, 20, 62])[0]
            text = " ".join(rng.sample(SENTENCES, rng.randint(1, 2)))
            out.append([d.isoformat(), str(rating), "yes" if rng.random() < 0.9 else "no",
                        str(rng.choice([0, 0, 0, 0, 1, 1, 2, 3, 5])), dept, rng.choice(BRANDS), rng.choice(TITLES), text])
        d += datetime.timedelta(days=1)
    for k, word in enumerate(ONE_WORD):              # spread through the file, one review each
        out[(k + 1) * len(out) // (len(ONE_WORD) + 1)][7] = word
    return out


def text():
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["review_date", "rating", "verified_purchase", "helpful_votes", "department", "brand", "review_title",
                "review_text"])
    w.writerows(rows())
    return buf.getvalue()


if __name__ == "__main__":
    t = text()
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8", newline="") as fh:
            same = fh.read() == t
        print("%s: %s" % (os.path.relpath(OUT), "as written" if same else "DIFFERS from what make_reviews.py writes"))
        sys.exit(0 if same else 1)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write(t)
    print("%s: %d rows" % (os.path.relpath(OUT), t.count("\n") - 1))
