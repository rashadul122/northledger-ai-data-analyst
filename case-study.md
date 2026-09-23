# Case study: RentSafeTO Building Health

*Rashadul Islam Roman, NorthLedger Insights, Toronto. Sample work on public data. Every figure
below was filled in by build.py from the data files in data/; none was typed. City data to
2026-09-21 (latest evaluation in the file); built 2026-09-23 12:22:35 UTC.*

## The client question

A Toronto operator of older purpose-built rental buildings asks: how do our buildings compare on
the City's RentSafeTO evaluations, which fixes are worth the most points, and can we see a lost
green sign coming?

## What the data is

City of Toronto open data (Open Government Licence, Toronto): the apartment building
evaluations since 2023, the evaluations before 2023, and the building registration file.
22,052 rows in the three files. The latest evaluation of each building gives
3,588 buildings and 327,317 units.

Contains information licensed under the Open Government Licence – Toronto. This is independent analysis; it is
not produced by, affiliated with or endorsed by the City of Toronto or RentSafeTO.

## What the audit found in the data

- The engine audited and cleaned all three files: 22,052 rows in =
  22,051 clean + 1 quarantined, nothing dropped without a reason.
- The City's own score counts an area that could not be entered as zero points: counting a
  refused item as zero reproduces 99.4% of the 360 affected scores
  exactly; leaving it out reproduces 5.0%.
- The published tier weights reproduce 6,666 of 6,681 scores exactly.
- One year label reads 202510; the
  completion date sets the year instead, and the label is flagged, not dropped.

## What it means for an operator

- Citywide, 82.5% of buildings hold a green sign,
  16.5% yellow and 1.0% red.
- Book access to every area before evaluation day: a refused area costs full marks.
- The single most valuable fix across the city is Building Exterior, worth 0.97
  points per building on average.
- Paperwork items score the lowest mark more often (9.4%) than high-risk
  items (5.6%).
- Same building, next evaluation: the proactive score changed by +3.6 points on
  average over 3,093 repeat evaluations (pairs of
  evaluations of the same building), and 65.5% of them scored higher.
  Split by each pair's first score: pairs that started under 70 moved +22.5 points on average (126 pairs, 96.0% higher), and pairs that started at 95 and over moved -1.8 (596 pairs, 32.2% higher). Low starters rising while high starters fall is the pattern regression toward the middle produces (the 100-point cap also limits high starters), so part of the average may not be repair. Repairs and the second round of a new scoring tool are not yet separated, so this
  is a measurement to watch, not a result to act on.

## What we could not predict

A numpy logistic model of losing the green sign was tested against simple baselines on a later
season. For the next evaluation, our model failed the rule fixed before the results were seen: to be offered, it had to beat simple baselines on both measures. It predicted the next score more closely (average miss 4.97 points, against 7.11 for repeating the last score), but it was no better at flagging which buildings would fall below green (Brier score 0.1249 against 0.1211 for the band's past rate; lower is better), so this page makes no prediction for individual buildings. The last score plus the usual change for its band misses by 5.08 points.

## What the engine does today, and does not

Mechanically yes, insightful not yet. On the messy reviews file the engine now goes from 48,384 messy rows to a cleaned table, a checked story and a backtested forecast in 55.1 s (before the engine work it kept 41 rows). On the RentSafeTO files everything it printed reproduced, but the brief is not one a landlord could use: the landlord answers (ward and pillar scorecard, points per fix, risk of losing green) still come from the site's own build script, not from the engine.

## What a client gets

- A scored Data Health Audit with every finding re-runnable.
- The interactive report and scorecard. A private version with the client's own buildings against
  anonymised peers can be built on request; it is not shown publicly, and the public page ranks no landlord.
- A Power BI project export (structure checked; not yet opened in Power BI Desktop).
