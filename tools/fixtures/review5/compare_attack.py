"""The final review's compare case (30 Sep 2026): 12 stores with no true difference. A sentence contradicts itself when
it reads "no more than chance" and still offers a range for the gap between the top and the bottom (the review found
92 of 300). Counts both, on the rule as it stands. Writes compare_attack.out.

    python tools/fixtures/review5/compare_attack.py
"""
import numpy as np
import pandas as pd

import _here  # noqa: F401  (paths)
import _out
import nl_browser as NB


def main():
    plan = {"columns": [{"name": "rating", "semantic_type": "rating", "role": "target"},
                        {"name": "store", "semantic_type": "category", "role": "segment"}]}
    chance = contra = 0
    first = None
    for seed in range(300):
        rng = np.random.default_rng(seed)
        df = pd.DataFrame({"store": np.repeat(["S%02d" % i for i in range(12)], 200), "rating": rng.normal(3.5, 1.0, 2400)})
        t = NB._a_compare(df, None, None, ["rating"], plan, None, by="store").get("sentence", "")
        gap = t[t.index(": a gap of"):t.index(". Each group")]
        if "no more than chance" in t:
            chance += 1
            first = first or t
            if "range" in gap:
                contra += 1
    print("12 groups, no true difference, 300 files: the chance reading in %d; of those, a range offered for the gap: %d"
          % (chance, contra))
    print(first)
    return 1 if contra else 0


if __name__ == "__main__":
    _out.run(main, __file__)
