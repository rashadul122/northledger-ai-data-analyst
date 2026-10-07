# The wave 5e regression pack

Each file here is a small table from one of the 21 defects an independent reviewer confirmed in the wave 5d build (bfcdc66), or from
fuzz seed 3160 (a regression found on a fresh range). `tools/test_nl_regress.py` has one test per case with an EXPLICIT assertion: the
true figure computed here with pandas from the file, or a plain "one member shown" or refusal, and never a confident wrong number.
Every test fails on bfcdc66 and passes now. Run all of them with `python tools/test_nl_regress.py` (about 1 minute, no network), or some
with `python tools/test_nl_regress.py r07 s03` (39 tests, about 50 seconds).

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

Added by this wave beyond the reviewer's list (each is red on the commit before its fix, and r07b, r10b, r15b are red on bfcdc66 too):

| case | what it pins |
|---|---|
| r07b | a daily table with no weekend rows is compared over whole weeks, a date against the same weekday (+22% before for a shop open Monday to Friday); a holiday's 5-day gap keeps a publisher's table daily |
| r10b | a MemoryError and the wall guard refuse a table of series and leave a business file alone |
| r15b | a member that says total and is decidedly not the sum of the others says so ("do not add up to it") |
| r23 | a dimension of measures (a rate and its standard error) is never an adjusted pair (`r23_measure_dimension_rate_and_standard_error.csv.gz`, fuzz seed 6) |
| r24-r30 | the second independent review: a plain ledger with a Void status or p/c/r codes and a second number is never refused (R01, R02); a scale word under any header is applied (R08); a scale in the unit's words is applied once (R03); a balance in a currency is a level (R05); an incomplete sum says so (R07); a contradicted named whole says so (R04); a short table's headline agrees with its estimand (R09) |

Wave 5f (the second fuzz, an independent generator; `plan/WAVE4-A-DESIGN.md` "Wave 5f"). Each is red on w5e-harden 09d3edc and green on w5f-harden; each has a negative case in the same test:

| case | fixture | before | after |
|---|---|---|---|
| f01-f04 (C) | built by `make_cubes.sensitive_dimension()` and ledgers in the tests | a five-word "Marital status" / "Indigenous identity" / "ICD code" column was an ordinary dimension: a member printed in the estimand, ICD codes in the profile | flagged by its header, withheld by default in every layout, refusal names the column and "choose Keep"; no value in any path |
| f05, f06 (A) | `f05_seed1_average_dollars.csv`, `f05_seed125_average_dollars_cents.csv` | an average rent in dollars beside a whole-country row: the 12-month TOTAL ($19.6K, seed 1) | "average level over the window", $1.6K (a mean, never added) |
| f07 (F) | `f07_seed10_countries.csv`, `f07_seed142_countries_rest_of_world.csv` | Canada among countries taken for the whole of GEO: $147.8B alone | one member shown, "not a national figure" |
| f08 (H) | built in the test | dates written 31.12.2019, 12/31/2019, Jan 2019, 2019M01 gave no analysis | rewritten as ISO dates before the file is read, the ISO twin's analysis; 03/04/2019 is refused, never guessed |
| f09 (E) | `f10_seed44_french_spaced_numbers.csv` | "708 219,6": a coordinate read as the measure, "average coordonn_e +0.0%" | numbers read, "Total, 12 months to Nov 2025: -4.1% ($1.4B)" |
| f10 (D) | `f11_seed34_daily_partial_month.csv` | "12 months to Nov 2023" over 26 days of November | the partial month left out and said; too short to compare |
| f11 (B) | `f12_seed4_weekly_total_rows_two_measures.csv` | a Total row added to the rows it totals: 54,446,750 where the plain sum is 27,223,377 | 108 Total rows left out (verified cell by cell), said so |
| pyodide f01, f05, f12 | `pyodide_sensitive_dimension.csv.gz`, `pyodide_average_dollars.csv.gz`, f12 | native = Pyodide: roles, figures, aggregation, withheld columns, rows left out, ledger totals |

The suspected items the task named are `s01` (sensitive headers), `s02` (the long-ID rule), `s03` (a copy in a five-member dimension), `s04` (a
25-sector uncoded tree beyond the old 22-candidate cap) and `s05` (the unnamed-aggregate decision does not sit on a borderline). The two rate
panels `pyodide_rate_*.csv.gz` are for `tools/check_pyodide_cube.mjs` (see `tools/make_pyodide_cases.py`), not for a regression.

Nothing here is a licensed or personal file: every table is synthetic, made by `tools/fixtures/structure/make_cubes.py` or by the
reviewer's own generator, and no raw review text is in the repo.
