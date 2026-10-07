# The wave 5e regression pack

Each file here is a small table from one of the 21 defects an independent reviewer confirmed in the wave 5d build (bfcdc66), or from
fuzz seed 3160 (a regression found on a fresh range). `tools/test_nl_regress.py` has one test per case with an EXPLICIT assertion: the
true figure computed here with pandas from the file, or a plain "one member shown" or refusal, and never a confident wrong number.
Every test fails on bfcdc66 and passes now. Run all of them with `python tools/test_nl_regress.py` (about 1 minute, no network), or some
with `python tools/test_nl_regress.py r07 s03`.

| case | files | what the old engine printed | what is right now (and the negative that stays true) |
|---|---|---|---|
| r01 | `r01_*small_counts.csv` | five branches of 0 to 2 events a month, no total row: "the biggest is the total of the other four, 48 of 48 cells", +0.0% | a business file is added up (8 then 9 events, +12.5%); an official table is added up only as parts of places, else one member |
| r02 | `r02_combined_disjoint_periods.csv` | a combined member added to its own parts when two parts report in disjoint periods: $262.4M (36% too high) | the five regions, $183.4M then $193.3M (+5.41%) |
| r03 | `r03_current_and_chained_prices.csv` | two bases of one quantity summed (2x) | one member shown, never the sum |
| r04 | `r04_sa_copy_*.csv` | an adjusted copy 5% above the unadjusted one added to it | the unadjusted figure; a 2% copy is the control |
| r05 | `r05_*.csv` | "Less than", "without", "other than" parts dropped as alternative totals, the table 4% short | parts that add up to a total are parts; with no total, one member |
| r06 | `r06_*.csv` | a French StatCan table: VECTEUR as the dimension, "milliers" not applied (1,000x small) | Canada, dollars in thousands, $230.2M, +4.3%, like the English twin |
| r07 | `r07_weekly_official.csv` | a weekly table read in "12 months" ending in a partial month: -3.7% for a flat series | 52 weeks against the 52 before, +0.08%, said in weeks |
| r08 | `r08_average_earnings_canada.csv` | a dollar average called a "12-month total", Ontario shown beside Canada | Canada's own series, a level (a mean, never a sum) |
| r09 | `r09_*.csv` | "All other provinces" and a lone country read as the aggregate of a rate | one member shown, and said not to be a national figure |
| r10 | `r10_*.csv` | a `not_cube` verdict fell to the old row-average path: "Average value ... 12,840" | a plain refusal with the layer's own reason |
| r11 | `r10_partition_for_caps.csv` | an exception in the profile pass let a plan read the table the old way | the same refusal as a run without a plan |
| r12 | `r12_451_members.csv.gz` | 451 members refused with a false reason | read; only the search for relations is bounded |
| r13 | `r13_time_budget.csv` | the answer depended on a wall-clock budget | the same answer at every count budget |
| r14 | `r14_*.csv.gz` | 18 "of which" components in a 399-member tree took 7.6 s | bounded by counts, under a second |
| r15 | `r15_*.csv` | a total no cell could check was "sum-checked", "adds up", "Each total was checked" | none of those words at an evidence level not reached |
| r16 | `r16_short_prior_window.csv` | a level's "12-month average" over two months, "complete: true" | half a window in both, matched, else no figure |
| r17 | `r17_value_nine_digits.csv` | a VALUE column withheld as national ID numbers, the table refused | released with "Read as the table's measure, not personal data: VALUE"; withheld again on the visitor's choice |
| r18 | `r18_label_hint.csv` | "Seasonally adjusted, Canada, ..." (the first constant column) | "Employed persons, Canada, ..." (what is measured) |
| r19 | `r19_metadata_echo.csv` | a constant text column ("Analyst") echoed into the report | never echoed |
| r20 | `r20_*.csv` | inventory, subscriptions and a grade book taken for series tables; French headers unknown | business files are never refused; the French table is read |
| r21 | `r21_built_from_parts.csv` | "the sum of 4 regions ... in the published totals" | "built from the table's 4 regions" |
| r22 | `r22_seed3160_*` | a 3-level industry tree with no codes summed with its own total: +6.9%, truth +4.3% | the total that adds up to its leaves is the headline |

The suspected items the task named are `s01` (sensitive headers), `s02` (the long-ID rule), `s03` (a copy in a five-member dimension), `s04` (a
25-sector uncoded tree beyond the old 22-candidate cap) and `s05` (the unnamed-aggregate decision does not sit on a borderline). The two rate
panels `pyodide_rate_*.csv.gz` are for `tools/check_pyodide_cube.mjs` (see `tools/make_pyodide_cases.py`), not for a regression.

Nothing here is a licensed or personal file: every table is synthetic, made by `tools/fixtures/structure/make_cubes.py` or by the
reviewer's own generator, and no raw review text is in the repo.
