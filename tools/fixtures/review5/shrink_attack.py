"""The final review's m simulations (30 Sep 2026), on the rule as it stands: m is estimated only from 5 or more groups
of 5 rows (SHRINK_MIN_GROUPS), else it is the set value 10 and the sentence says so. Writes shrink_attack.out.

    python tools/fixtures/review5/shrink_attack.py
"""
import collections

import numpy as np

import _here  # noqa: F401  (paths)
import _out
import nl_browser as NB


def sim(rng, title, ns, tau, sigma=1.0, reps=2000):
    ms, kinds = [], collections.Counter()
    for _ in range(reps):
        mu = rng.normal(0, tau, len(ns))
        m, how = NB._shrink_m([rng.normal(mu[i], sigma, n) for i, n in enumerate(ns)])
        ms.append(m)
        kinds[how["kind"]] += 1
    ms = np.array(ms)
    true = sigma ** 2 / tau ** 2 if tau else float("inf")
    near = float(np.mean((ms >= true / 2) & (ms <= true * 2))) if tau else float("nan")
    print("%-50s true m=%-4s median=%-3d p10=%-3d p90=%-3d within x2: %5.1f%%  kinds=%s" % (
        title, ("%.0f" % true) if tau else "inf", np.median(ms), np.percentile(ms, 10), np.percentile(ms, 90),
        100 * near, dict(kinds)))


def main():
    rng = np.random.default_rng(11)
    print("SHRINK_MIN_GROUPS = %d, SHRINK_MIN_ROWS = %d, set value %d" % (NB.SHRINK_MIN_GROUPS, NB.SHRINK_MIN_ROWS, NB.SHRINK_M))
    for k in (2, 3, 4, 5, 6, 8, 12, 20):
        sim(rng, "%d groups x 100 rows, true m 25" % k, [100] * k, 0.2)
    for k in (2, 3, 4, 5, 6, 8, 12, 20):
        sim(rng, "%d groups x 100 rows, true m 11" % k, [100] * k, 0.3)
    sim(rng, "no spread: 20 groups x 50 rows (tau 0)", [50] * 20, 0.0)
    sim(rng, "imbalance: 1 x 10,000 + 10 x 5, true m 25", [10000] + [5] * 10, 0.2)
    sim(rng, "imbalance: 1 x 10,000 + 1 x 5, true m 25", [10000, 5], 0.2)
    print()
    for label, groups in (("identical constant groups (5)", [np.ones(10)] * 5),
                          ("two groups", [np.arange(10.), np.arange(10.) + 1]),
                          ("four groups of 5+ rows", [np.arange(10.) + i for i in range(4)])):
        m, how = NB._shrink_m(groups)
        print("%-32s m=%d how=%s -> %s" % (label, m, how, NB._m_words(m, how, "brand")))
    return 0


if __name__ == "__main__":
    _out.run(main, __file__)
