# NorthLedger report contract, version 2

*What `engine/nl_browser.py` returns (`run()` / `run_json()`), 24 September 2026. It implements
§4.2 of `plan/NorthLedger-Confidence-Architecture-2026-09-23.md` on the R1 engine
(`northledger-core`, snapshot in `engine.snapshot`) and adds the chart data the §5 visual grammar
needs. Tests: `tools/test_nl_browser.py` (the `test_v2_*` tests).*

## 0. Rules that hold for every field

1. **Every v1 key is kept, with its v1 type.** A v1 page keeps working. One v1 key changes meaning,
   as §4.2 says: `health.score` now carries the **weakest** health dimension (`score_min`), not the
   mean. The mean is `health.score_mean`. A page that labels `health.score` must say "weakest
   dimension" (§4.2 rules).
2. **The adapter computes no statistic.** Every number in `findings`, `forecast`, `tests_run` and
   `engine.benchmark` is a value the engine wrote (a fact, a fact's payload or test summary, a gate
   signal, the forecast result, or the benchmark receipt the engine ships). Each finding lists the
   fact ids its numbers came from in `trace`. The only arithmetic the adapter does is (a) the test
   statistic `t = (delta_hat - bar_log(bar, direction)) / se`, from the engine's own recorded
   estimate, standard error and `stats.bar_log`, and (b) the chart data of §3, which is descriptive
   (counts, means, shares, bins, correlations) and is re-computed from the two CSV downloads by
   the tests. Wave 4 (track A2) adds the inference of section 5.11, computed by `engine/nl_inference.py` from the
   engine's own tables and results and from the benchmark receipt the engine ships: the trend test, an interval's
   measured coverage, the history windows' effective count, the forecast audit and the official-aggregate record.
3. **Absent means absent.** A number the engine does not measure in this release is `null`, never
   `0`, and the enclosing object says why in a `note` or `not_measured` field. Lists that nothing
   filled are `[]`.
4. **Text fields** go through the v1 scrubber (withheld values, phone numbers, email addresses and
   the engine's internal table name are replaced). No text field carries a number the engine did not
   write. A withheld (or coded) value is looked for only when it is specific (live baseline, 30 September 2026:
   one review whose whole text was "this" turned "a result at least this strong" into "a result at least
   [withheld] strong", 62 times in one PDF): a free-text column's value only whole and 20 characters or more;
   any other value 2 words or more, or one word of 6 characters or more that is not a common English word
   (`nl_browser._COMMON_WORDS`: everyday words and every word of 6 letters or more in the engine's and the
   adapter's own sentences, names taken out); never a number (`nl_browser._specific`).
5. **Flagged columns never appear in chart data** (`charts[].data`): not as a series, a matrix row
   or column, a category source or a label, whatever the visitor decided for them. A withheld column
   also never appears in `health.columns[].top_values`, `numeric` or `dates`.
6. Every float is finite or `null` (the page parses with `JSON.parse`).

## 1. Top level

| key | type | v | meaning |
|---|---|---|---|
| `ok`, `error`, `engine`, `input`, `timings`, `privacy`, `health`, `cleaning`, `roles`, `findings`, `forecast`, `story`, `downloads` | | 1 | as in v1 |
| `contract_version` | int | 2 | `2` |
| `primary_metric` | object or null | 2 | `{finding_id, claim_key, grade}`: the claim the engine tested in its `primary` family, or null. The gate's primary is set from the AI plan's primary column, as the engine's own name for it (a long table's value column is its lead series, `input.layout.lead`); when the plan named a measure the engine tested and the gate's primary is not about it, the engine's claim for that measure is the primary (live baseline, 30 September 2026: the plan named VALUE and the report led with the row count): a level's, a rate's or a rating's average month, an amount's or a count's first total in the engine's order (the one `scenarios` breaks down), else its average. The row count never leads when the plan named a measure the engine tested; the gate's grades and families are unchanged. `summary`, the tiles and `results_for_ai.primary` follow it |
| `tests_run` | object | 2 | `{families: [{name, size, fdr_method, level}], claims_tested}` from the gate's family pass |
| `methods` | list | 2 | `{id, name, assumptions[], applies_to[] (finding ids), desktop_only}` for each method that ran |
| `limitations` | list | 2 | `{kind: data|statistical|causal|forecast|external, text, finding_ids[]}` |
| `reproducibility` | object | 2 | the provenance block, §2.6 |
| `charts` | list | 2 | chart records, §3 |
| `charts_suppressed` | list | 2 | `{rule, type, why}` for each §5 chart whose rule did not fire (§5 "Suppression") |
| `llm` | object | 2 | `{used: false, model: null, consent: false, guard: {...}}`; the adapter never calls a model |
| `scenarios` | object | 2 | the headline claim broken down for the report writer (design B, 29 Sep 2026): `{basis, items[], refused[], note}`, §5.8. Always present; `{basis: null, items: [], refused: [], note: ""}` on a refusal |
| `summary` | object | 2 | the manager's bottom line (fixer round, 24 Sep 2026): `{lines: [{kind: "moved"|"act"|"plan", text, finding_ids[]}], labels: {finding_id: plain label}, monitoring: [finding_id]}`. `lines` holds at most three sentences built from the engine's facts, every number printed with `narrate.story_number` from a fact the line lists: what moved and why (the primary claim, the steps where what the file covers changed and its like-for-like restatement, a fall since a peak), what to act on (the CONFIRMED business claims, or none), and the planning number (the primary series' forecast, graded). `labels` names each business and forecast claim in the reader's words ("Rent, monthly total", "Ledger lines a month"); an average of a measure the AI plan reads is named from its column and the plan's own words for it, its `label` when the plan gives one and its unit when that names a real unit ("Average value (CAD per USD), the average month", "Average USD/CAD exchange rate (CAD per USD), ..."), never from a value in the file (a long table with one series names it after its value column, never after a column that holds one value throughout: the live FX file's rate was "canada", from GEO). `monitoring` lists the claims about the ledger's own line count when the file has a money total: kept off the first screen, the tiles and the bottom line; they stay in the tables and the analyst view |
| `estimand` | object or null | 2 | WAVE 4 (track A1, 1 October 2026): what the headline IS, for a file read by its structure (§5.10); null otherwise. `{text, slice[{dim, member, role, why}], measure{label (the slice's member of a measure dimension, "Total retail sales", else the column), uom, scale, scale_applied, type, aggregation}, comparison{latest[a,b], prior[a,b]}, figures{prior, latest, change, change_pct}: {value, text} (base units, the file's scale applied; `prior`/`latest` also carry `months`), sum_checks[{dim, total, parts, by, complete_cells, within_tolerance, max_rel_residual, max_residual{value, text}, suppressed_parts, unallocated_latest{value, text}, unallocated_prior{value, text}, verdict}] (`suppressed_parts`, 6 October 2026: how many parts of the dimension lack a month's value in either window, 0 when the not-allocated amount is only the rounding of the published figures; engine report only, not in `results_for_ai`. The `unallocated_*` texts of a gap that would read as zero are written out in whole units, "−$3,000", never "$0.0B"), excluded[{what, dim, why}], plan_source: "engine_default"\|"ai"\|"ai_corrected", slice_id, reconciles, complete, months_used, months_left_out[], corrections[], inference}` (`inference` is track A2's record of the headline claim, the same record as that finding's `findings[].inference` (§5.11 T4), a copy; null when the table is not an official aggregate). `text`: "Canada · Retail trade [44-45] · Total retail sales · Unadjusted; dollars (file in thousands ×1,000); 12-month totals Aug 2025–Jul 2026 vs Aug 2024–Jul 2025". `reconciles` is true when the slice's monthly values equal the engine's charted series to 1e-6 (the headline claim's trend chart), null when the engine charted none to compare with, false only in a refused block (§5.8: `basis.reconciles` is false then too, and true otherwise). The page and the PDF print it first; `results_for_ai` sends it first. WAVE 5 (7 October 2026, section 5.12) adds `built_from` (a table with no total row), `measure_choice` (a table whose members are different measures), `period` and `periods_used` (a quarterly or an annual table), `measure.type_basis` and `measure.type_why`, and sum-check entries with `verdict: "not possible (no total row)"`, `built_from_parts` and `suppressed_part_months` |
| `structure` | object or null | 2 | the table's structure (§5.10, `nl_structure.public`): `{kind, usable, reason, version, publisher, official, series, months, rows, date, measure, metadata[], flag_column, dims[], slices[], breakdowns[], flags, corrections[], hash, detect_seconds, slice{id, where, column}, file_health}`; null for a file read as before (a business export whose members add up, a panel with no relation, a file with no dimension). A table refused for its structure (`kind: "cube_incomplete"`) carries it with the reason |

A refusal (`ok: false`) returns every key with its empty value.

## 2. Blocks

### 2.1 `engine`

`snapshot` (12 hex), `version` (v1), plus:
- `semver`: the engine's `__version__`.
- `decision_code_snapshot`: the id a benchmark receipt stamps (sha256 of `northledger/*.py` without `benchmark.py`), from the pack stamp in the browser.
- `environment`: `{python, numpy, pandas, sqlite, pyodide}` of the runtime that produced the report (`loop.environment()`; `pyodide` is null natively).
- `restated_since_previous`: `null` (no restatement list is recorded in R1).
- `benchmark`: from the slim change receipt (`northledger/benchmark_receipt.json`) and the forecast part of `benchmark/engine_benchmark.json`, each quoted only when the engine's own staleness check passes:
  - `receipt`, `snapshot` (the change-path snapshot it measured), `available` (bool), `note` (why not, when not);
  - `matched_cell`: the receipt cell nearest the primary tested claim's diagnostics (`gate.nearest_benchmark_cell`, the engine's own matching): `{name, for_finding, n (months), phi (mean estimated momentum), cv_pct, level (rows a month), k, N, rate, lower95, upper95}`, or null. `rate`, `lower95`, `upper95` are **percent** (the receipt's k/N and its 95% Clopper-Pearson interval), as the engine prints a quoted rate;
  - every cell also carries `shift_pct` (the true change in that condition), `at_bar` (true when the no-change condition's true change was exactly the bar, not zero), `routed` (a count condition under `gate.ROUTE_MIN_ROWS_A_MONTH` rows a month: every verdict there is WATCH by rule, so its rate is zero by construction, not by measurement), `seasonal`, `white_sd`, `effective_level` (a monthly-total condition's effective rows a month, else null);
  - `worst_cell`: the gated cell with the highest k/N, same fields, plus `above_target` (its Clopper-Pearson lower end is above `target_pct`, the engine's 1% aim);
  - `file`: the primary tested claim's own diagnostics `{for_finding, claim, months, cv_pct, phi, rows_a_month, effective_rows_a_month, amount_cv, unlike?}` (rows a month for a volume or a total; a monthly total's EFFECTIVE rows a month, rows / (1 + CV^2) of its row amounts, on which the gate matches and routes it), printed beside the matched condition; `match`: `"close"` when they sit within 0.1 momentum, 5 noise points, the same months and x1.5 (effective) rows a month of the matched cell, `"none"` when no measured condition of the claim's type is like it (`gate.benchmark_far`: its momentum more than 0.45 from the nearest condition's, or more than 0.3 beyond every one of them; `file.unlike` says which, and no rate is quoted), else `"nearest"`;
  - `routed`: `{for_finding, condition, kind: "total"|"count", rows_a_month, min_rows_a_month, effective_rows_a_month, min_effective_rows_a_month, amount_cv}` when the primary claim is in a condition the engine holds at WATCH by rule (`gate.uncertified_condition`), else null: no false-confirm rate applies to it. A monthly total is routed on its effective rows a month against `gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS`, a count on its rows a month against `gate.ROUTE_MIN_ROWS_A_MONTH`, and the page states the claim's own rule;
  - `certification`: the release check of the receipt's gated no-change conditions by the benchmark's own judge (`benchmark.judge_nulls_certify`, Holm): `{cells, passed, failed[cell], cap_pct, family_level, form}`, or null;
  - `power_matched`: the primary claim's `power` (below); `target_pct`: 1.0;
  - `forecast_coverage_80`: `forecast.coverage_evidence` of the shipped receipt: `{steady: {lo, hi}, momentum: {lo, hi}}` (pooled coverage shares), or null;
  - `placebo_real`, `power_at_bar_matched`, `cross_env`: `null`; `not_measured` lists them.

### 2.2 `health`

v1 `score` (**= `score_min`**), `issues`, plus:
- `score_min`, `score_mean` (the engine's Data Health Score), `weakest` (the dimension name at the min).
- `dimensions`: five `{name, score, applicable, ci, ci_method, k, n}`; `ci` is a 95% Wilson interval (engine `forecast.wilson`) on the 0-100 scale where the dimension is one proportion: completeness (non-empty cells of all cells) and uniqueness (non-duplicate rows of all rows, only when no id-like column lowers it). Validity, consistency and timeliness are means of column ratios or a decay: `ci` null, `ci_method` says so.
- `sample`: `{method: "all"|"sampled", n, seed: null}`.
- `columns`: one per landed column: `{name, type, n, flagged, withheld, completeness{k,n,pct,ci}, validity{k,n,pct,ci,dominant_format}, uniqueness{applicable,k,n,pct,ci}, weakest, claim_health, distinct, top_values[[value,count]], numeric{min,median,max}, dates{min,max,order}, quarantined_by_rule{rule: rows}, fixes_by_rule{rule: cells}, safe_for[finding ids]}`. `pct` is 0-100, `ci` a 95% Wilson interval on 0-100. Completeness = non-empty of all rows; validity = values of the column's dominant type of its non-empty values (the claim's validity, `measure._claim_validity`); uniqueness = distinct of non-empty, applicable only to an id-like column. `weakest` is the lowest applicable of the three. `top_values`, `numeric`, `dates` are empty/null for a withheld column; `numeric` and `dates` come from the cleaned table.
- `missingness`: `{matrix_columns[], by_month[{month, rows, nulls{column: k}}], nullity_corr{columns[], matrix[][], n}, mcar{test: "little", p: null, conclusion}}` over the cleaned table's rows in the analysis window, flagged columns left out. Little's MCAR test is not run in R1.
- `csv_text_numbers` (30 September 2026): null, or `{columns[], lowers{validity, score_min, score_mean}, note}`. **Decision: the line is dropped, not reworded.** The engine's health check writes "`<col>`: N of N numbers are stored as text; they will sort '10' before '9'." for every number column of a CSV (every file the page reads is CSV text, landed as text) and marks the column's validity down by half (`northledger/health.py`, `_NUM_AS_TEXT_PENALTY`). The line says nothing about this file, and it is not true of the analysis, which reads those values as numbers (the cleaner converts them), so nothing in the report sorts '10' before '9'; a reworded line would still be a non-issue in a list of issues. So the adapter leaves it out of `issues` and out of what it sends the AI (`results_for_ai.health_issues`, which also drops it from a report saved before this rule); no engine file is changed. The core's score is NOT changed: it keeps the mark-down. `columns` names the number columns marked down (never a withheld one); `lowers` says which scores the mark-down lowers, read by undoing it with the engine's own constant and rounding (`health._NUM_AS_TEXT_PENALTY`, `health._pct`) and the cleaner's validity cap as `health.reflect_cleaning` applies it (no counterfactual number is printed). When it lowers the weakest dimension or the mean, `note` says so in plain words and the page prints it in the Data health area (the health tile, the trust strip's Data health line and the analyst view's Quality by dimension): "The score counts numbers stored as text, which every CSV has: validity, the weakest dimension here, is marked down for the numbers in revenue and units, although the engine reads them as numbers." (the mean only: "validity is marked down for the numbers in amount, which lowers the mean of the five"); else `note` is "". It lowers the score on most files: the site's sample's mean reads 87.1 where it would read 88.3 (its weakest dimension is consistency, unaffected), and a clean twelve-region orders file scores 75.0 on its weakest dimension, validity, where it would score 100. When the weakest dimension is lowered (the only score `results_for_ai` sends, `health_score`), the writer's `health_issues` start with one line: "The health score counts numbers stored as text, which every CSV has: it is validity, the weakest dimension here, marked down for them, although the engine reads them as numbers."
- `explain` (1 October 2026): what set the score, in plain words, naming no column: the weakest dimension, why, and the
  other checks' average, e.g. "0 because the newest row is 3.5 years old (the timeliness check); the other checks
  averaged 96.4." (`nl_browser._health_explain`: the newest row's age and the future-dated rows from the engine's own
  timeliness lines, the exact duplicate rows, the numbers-stored-as-text mark-down when that is what lowers validity);
  "100: every check the engine ran scored 100." when every check scored 100; "" with no score. The core's score is
  unchanged. The page prints it on the health tile and in the trust strip's Data health line; `results_for_ai` sends it
  as `health_explain` (for the PDF's health tile and data table) and, when the weakest dimension sits 10 points or more
  under the others' average and the numbers-stored-as-text line does not already say why, first among
  `health_issues` ("The health score is 0 because ...").
- `accuracy`: `{measured: false, audited_rows: 0, errors: 0, upper95: null, text: "not measured"}`.

### 2.3 `cleaning`

v1 keys plus:
- `quarantine_by_month`: `[{month, count}]` of set-aside rows by the month the cleaner's date reader gives them (`month: null` for undated rows).
- `rules`: `[{name, kind, column, reason}]`, the rule set the cleaner used.

### 2.4 `findings[]`

v1 `id, claim, verdict, why, kind, value`, plus:

| key | meaning |
|---|---|
| `grade` | `CONFIRMED` (RECOMMEND), `WATCH`, `NOT_ENOUGH_DATA` (INSUFFICIENT); internal code stays in `verdict` |
| `role` | the gate's family (`primary`/`secondary`) for a tested claim, else `secondary`; `parent_id`: for a like-for-like claim (its test's `like_for_like_of`), the claim it restates on the levels present throughout; else null |
| `estimand`, `unit` | `ratio_of_average_month`/`month` for a change claim; `next_month_value`/`month` for a forecast; null otherwise |
| `effect` | `{estimate, ci [lo,hi], level, ci_fcr [lo,hi], fcr_level, method, scale, unit, note, coverage}`. `coverage` (wave 4, T2, section 5.11): for a change claim with an interval, `{nominal: 0.95, measured, lo, hi, n, cell, bucket, claim_bucket, source}`: how often the 95% interval held the true change in the benchmark condition nearest the claim (`gate.nearest_benchmark_cell`), within the claim's momentum bucket when that bucket holds 100 intervals or more (`bucket` names it; `"all"` when the whole condition is quoted), with its 95% Wilson interval (`lo`, `hi`, fractions); `measured: null` and `unlike` (the reason) when no condition is like the claim (`gate.benchmark_far`); else null (no receipt measured this code, or no interval). Change claims: fractions (0.2 = +20%), the 95% test-inversion interval and, for a CONFIRMED claim, the selection-adjusted (FCR) bound: one-sided (Benjamini-Yekutieli), so the far end is null (a rise reads `[lower, null]`) and `fcr_level` is its level, e.g. 0.9933. A change claim with no ratio (monthly averages at or below zero, `gate._no_ratio`): `scale: "difference"`, `estimate` the difference in the measure's own units (the fact's value), `unit` that unit, no interval, never a percentage. Forecast: the next month's point and 80% range; when the engine does not offer the forecast (`forecast.available` false or grade NOT_ENOUGH_DATA), `estimate`, `ci` and `level` are null and `note` says it is not offered. State facts: the value, no interval. `scale: "money"` marks the forecast of a series the engine totals as money (its additive kind `money`): the page prints it and its range in whole units. |
| `test` | null, or `{name, method, ran, not_run_reason, null (min_effect), bar, n_months, n_rows, phi_hat, B, statistic, statistic_name, df, p, p_mc_interval, p_point_null, q, family, family_size, fdr_method, family_line}`. Forecast: the Diebold-Mariano test against seasonal-naive (`statistic`, `p`, `n_months`). |
| `health` | the claim health (the minimum over the columns it reads, M9) |
| `checks` | `{claim_health, drift_screen{hit, reason}, tipping_point{value: null, ..., source: "not_computed"}, reversal{flagged: null, segment: null}}` (S1/S2 are not built in R1) |
| `watch` | for a WATCH grade: `{reason, checks_failed[], settle, movement, routed}`: the engine's reason, the gate checks that held it back, what would settle it, whether it moved (`{kind: "moved"` (the 95% interval lies wholly on one side of zero, not wholly past the bar) `| "cleared"` (wholly past the bar; another check holds it) `| "unclear"` (the interval includes zero)` `| "stepped"` (the gate holds it for a change in what the file covers and the steps at those months explain the series: `steps[{month, size, levels[[level, verb]], fact_id}]`, largest first; its as-filed interval spans the steps and is not printed)`, direction}`, null with no interval), and whether it is held at WATCH by rule (`gate.uncertified_condition`); else null |
| `error_rate` | for a CONFIRMED change claim, the engine's conditional false-confirm sentence and its numbers: `{sentence, rate, lower95, upper95, k, n, cell, fact_ids}` (percent). For a CONFIRMED forecast, the benchmark's measured rate of false "beats seasonal-naive" advice in its seasonal random-walk condition nearest the series' months: `{sentence, rate, lower95, upper95 (exact 95% Clopper-Pearson), k, n, cell, months, series_months, source, fact_ids: []}`, quoted only when `benchmark/engine_benchmark.json` measured this decision code. Else null with `error_rate_note` saying why |
| `drivers` | `[]` (S2 not built) |
| `composition` | for a change claim whose file coverage changed inside the modelled months (the engine's composition record on its test), else null: `{column, fact_id, words, steps[{month, kind: "entered"\|"left", level}], levels_kept, like_for_like}`. `words` are the engine's own; `like_for_like` is `{finding_id, fact_id, claim, estimate (fraction), ci, level, grade, tested}`: a tested like-for-like claim of its own (its test names this claim in `like_for_like_of`), or the engine's descriptive like-for-like fact (`tested: false`, no interval, `finding_id` null when it is not a finding), or null. Null too when the category is flagged as possible personal data |
| `posterior` | `{shown: false, p_exceeds_bar: null, calibration_ref: null}` |
| `power` | `{at_bar: null, bar, months_to_80pct: null, nearest, routed, routed_rule, design}`; `routed_rule` `{kind: "total"|"count", rows_a_month, effective_rows_a_month, line}` names the rule that holds the claim at WATCH, else null: `nearest` is the receipt's power cell (a true 20% change planted) nearest the claim's months, noise, estimated momentum and, for a volume, rows a month, by `gate.nearest_benchmark_cell` (cell fields as above, percent); `routed` true when the claim is held at WATCH by rule, so no power applies |
| `needed_to_upgrade`, `columns_read`, `verified`, `tested_times` (null), `chart_ids`, `trace` | the engine's settle text; the columns the claim reads; whether the ledger re-ran it; the charts that show it; fact ids |
| `inference` | wave 4, T4 (section 5.11): null, or for an official aggregate (a published total or level) `{mode: "official_aggregate", publisher, how_known[], describe{change_pct, prior, latest}, revisions, quality, grade_label}`: the change is described, not tested; the engine's grade stands and `grade_label` says what it is a grade of ("process grade: month-to-month noise in 36 monthly averages; not a test of the published figure") |
| `layout_artifact` | wave 4, P0-13: true for the forecast findings of a row count the table's layout or the calendar fixes (`forecast.row_forecast_dropped`); such a finding has no estimate or range, and is kept out of the headline tiles and the bottom line; else false |

**A table read by its structure (WAVE 4, §5.10).** Its findings are the engine's own on the slice it chose (the headline
series: one row a month, the measure in base units, named "Total retail sales" or "<label> total" so the engine adds it
up): a change claim about that series and its forecast, never a claim about the table's row count (465 rows a month is
the table's layout) and never a data-quality finding on its metadata or flag columns (those are in
`structure.file_health.dropped`). A business claim's `estimand` field keeps its v2 meaning (`ratio_of_average_month`); what
the headline is in the reader's terms is the top-level `estimand` (§1).

### 2.5 `forecast`

v1 keys plus `band{level, method, n_errors, max_level, conditional_coverage_p10_p90, scope, dependence, widened_by_max, n_effective_h1}`, `coverage{hits, n, wilson, binomial_p, christoffersen_ind_p, christoffersen_cc_p}`, `baseline_test{method, stat, df, n, p, gain}`, `models[]` ordered by MASE (the headline measure) with `{name, label, mase, mape, mape_suppressed, msis, skill_vs_sn, mae, coverage, dm, champion, baseline, applicable}`. `coverage` (`{hits, n, rate}`) is measured for the champion's shipped range only, and `dm` (`{stat, p, n, of}`) is the whole method's test (its model choice at each replayed month included) against seasonal-naive, carried on the champion's row; both null on the other rows, `break: null`, `interventions: []`, `forecastability: null`, `decision_edge: null`. All from the forecast result the engine verified.

Wave 4 (P0-13, section 5.11):
- `audit`: null, or the rolling-origin back-test of the series the block is about (`nl_inference.forecast_audit`):
  `{method: "rolling origin", mode: "cheap", model, series, origins, first, last, min_history: 45, horizons[{h, held,
  of, n_eff, wilson [lo, hi], mae, mae_seasonal_naive, rel_mae, mase, status}], status: "passes"|"fails"|"unclear"|
  "not_run", trusted, label, grade_label, rule, why}`. `label` reads "back-tested: held 22 of 23 at 1 month, 21 of 21
  at 3 months, ..."; `grade_label` is "the engine's grade (not trusted: the back-test failed)" when it fails.
- `trusted`: the audit's verdict (true only when it passes), or null with no audit.
- `row_forecast_dropped`: null, or `{kind: "layout"|"calendar", reason, rows_a_month, series, slug}` when the engine's
  monthly row count is fixed by the table's layout (every month the same count, or a long table read one column per
  series) or by the calendar (one row per date, 15 or more dates a month). The block then shows the next forecast
  series the engine made, or none (`available: false`, `reason` "No forecast of the rows a month is shown: ..."), and
  the story's "what's next" says so in one line.
- `frequency` (wave 5, 7 October 2026): `"quarter"`, `"half-year"` or `"year"` when the table is read in those periods
  (`estimand.period.step` is not 1): the block is `available: false`, has no `audit`, and its `reason` is "No forecast is
  shown: the table is quarterly (one value a quarter), and the forecast reads monthly series only, so it has no back-test
  either."; the story's "what's next" is that one line, and no forecast finding is made (section 5.12).

### 2.6 `reproducibility` (provenance)

`{input_sha256, engine_snapshot (full 64 hex), decision_code_snapshot, environment, parameters{as_of, objective, window{start, end} or null, gate_policy, forecast_config}, seeds{claim id or "forecast.<series>": seed}, B{claim id: {test, interval}}, figures_reproduced{k, n, failed}}`. One seed per recipe: every fact of a change test shares its claim's seed and resample sizes, every fact of a forecast its series' seed. `figures_reproduced` sums the audit's and the analysis's ledger re-runs (a hard count, never a percentage).

## 3. Charts

Each record: `{id, rule, type, title, view: manager|analyst, default_visible, finding_ids[], why_shown, source, data}`. `rule` names the §5 row that fired (`"#2"`), `why_shown` the condition as it held on this file. At most six records are `view: manager` with `default_visible: true` (§5 counts the KPI tiles and the findings table among its six, so at most four are drawn as charts); beyond that a chart is moved to the analyst view and `why_shown` says so, counting the drawn charts and naming them.

Definitions (the tests re-compute every one from `downloads.clean_csv` / `downloads.quarantine_csv`):
- **month**: `pd.to_datetime(<date column>, errors="coerce").strftime("%Y-%m")`, the engine's own analysis-table rule. **window**: the engine's analysis window `[start, end]` (`measure.window`), all months in it listed, empty ones included.
- **label**: a category as written in the clean CSV download (whole-number float columns written without ".0").
- Only rows of the cleaned table (`clean_csv`) are charted, except #12, which also reads the set-aside rows.

| id pattern | rule | data |
|---|---|---|
| `kpi` | #1 | `tiles[{finding_id, claim, value, unit, grade, ci, ci_level, scale, kind, movement}]`: at most 3, business claims (an offered forecast counts; `summary.monitoring` claims never), the primary claim first, then by strength of evidence (CONFIRMED, WATCH that moved, other WATCH), the primary claim's own like-for-like restatement and forecast before others, then the larger change; `claim` is the plain label (`summary.labels`); data-quality tiles only when the file has no business claim. A forecast that is not offered has no tile; a change with no ratio carries `scale: "difference"` and its unit |
| `trend.<claim_key>` | #2 | `months[]` (the test's model months), `values[]` (the engine's own monthly series for the claim, run from its payload SQL; null where a month is under the engine's rows-a-month floor), `rows[]`, `month_floor` (0 = none), `windows{prior[a,b], latest[a,b]}`, `window_means{prior, latest}` (their ratio is the headline), `effect{estimate, ci, level}`, `unit`, `scale` (`"money"` for a money total, printed in whole units, else null), `steps[{month, kind: "entered"\|"left"\|"step", level, source}]` (the months inside the chart where the claim's composition record says a level starts or stops, and the one level shift the engine's step screen found on this claim's series, `kind: "step"`, `level` null; `source` the fact that says so), `like_for_like` (null, or the claim's `composition.like_for_like` plus `values[]` on the same months, `window_means{prior, latest}`, `column`, `levels_kept`: a tested like-for-like claim's own series, or the claim's series restricted to the levels present throughout, as the engine's descriptive fact restricts its two sums; its window means' ratio is its estimate). When the like-for-like claim is a finding, it is in the chart's `finding_ids` after the claim |
| `fan.<slug>` | #4 | `history[{month, actual}]`, `forward[{month, h, value, lo, hi, widened_by}]`, `level`, `scope`, `scale` (as `trend`) |
| `replay.<slug>` | #5 | `months[{month, actual, point, lo, hi, in_band}]` (the one-month-ahead replay), `hits`, `n`, `scale` |
| `findings_table` | #13 | `rows[{finding_id, claim, grade, effect, ci, ci_level, scale, unit, note, movement, settle, p, q, family}]`, `manager_columns`, `analyst_columns` |
| `cleaning.before_after` | #12 | `months[]`, `landed[]`, `kept[]`, `set_aside[]`, `undated_set_aside`, `date_formats[]` (the reader the cleaner used) |
| `season.<series>` | #3 | `years[]`, `months[1..12]`, `values[year][month]` (count, or mean of the measure), `n[year][month]` (rows) over the window; fires at 24+ window months |
| `models.<slug>` | #6 | `rows` = `forecast.models`, `columns` in order |
| `ranked.<dimension>` | #7 | `bars[{label, rows, share_pct, fact_id}]` top 5 by rows (ties by label) + `other` + `missing`, `total` over the window; `fact_id` is the engine's share fact the bar equals |
| `catmonth.<dimension>` | #8 | `months[]`, `categories[]` (top 5 + "other", + "(missing)" when any), `counts[category][month]`; R1 substitute condition (S2 not built): a tested change claim and a dimension with 2-50 levels, for the first 3 such columns in the engine's order; no reversal flags |
| `dist.<claim_key>` | #10 | `edges[]` (Sturges bins, `numpy.histogram` semantics over both windows' monthly values), `prior{values[], counts[]}`, `latest{values[], counts[]}`, `scale` |
| `missingness` | #11 | `columns[]`, `months[]`, `rows[]`, `nulls[column][month]`; fires when a column is over 1% empty in the window |
| `benchmark` | #14 | `matched_cell`, `worst_cell`, `file`, `match`, `routed`, `certification`, `target_pct` (as `engine.benchmark`), or `available: false` and `note` |
| `corr` | #9 | `measures[]`, `r[i][j]` (Pearson, pairwise complete rows), `n[i][j]`; fires at 3+ measures; `default_visible: false` (§5: only when the owner asks) |

Suppressed rules (no chart drawn, one line in `charts_suppressed`): #2b driver waterfall (S2 is not built in R1), and any row above whose condition did not hold. The #2b line goes when the chart registry built a `contribution_waterfall` (§5.9, a rule `V` record in `charts`, which then accounts for #2b; `nl_viz.drop_driver_line`, integration pass, 30 September 2026): the report no longer says the drivers are not computed beside the chart that computes them.

## 4. Failure handling

If building the v2 blocks raises on some file, the adapter keeps the whole v1 report, returns
the v2 keys empty and adds one `limitations` entry saying the confidence details could not be
built. With the environment variable `NL_BROWSER_STRICT` set (the tests set it) it stops the run
instead, so a defect cannot hide behind the fallback.

## 5. The AI plan's blocks: one reading, the privacy promise (29 September 2026)

### 5.1 One parser

The data tests (`contracts`), the AI's analyses (`ai_analyses`) and the planner's profile (`profile_json`)
read every cell exactly as the engine's cleaner read it. The adapter replays the cleaner's own repair and
conversion rules (`clean.standard_rules`, the rules `clean_table` ran, in its order, with its own code) on the
landed table, so every row, kept or set aside, has the value the engine gave it: its decimal comma, its k and M
suffixes, its accounting negatives, its percent sign (`"12%"` is 0.12), its placeholders (`N/A`, `NULL`, a dash:
blank, never unreadable) and its day/month rule (a date that could be either way round in a column holding
both orders is not read). The plan's column names map to the engine's landed names by the engine's own column
map (`intake.normalise_columns`). No engine file is changed.

### 5.2 `contracts.tests[]`

Each test also carries `ambiguous` (dates the engine set aside because day and month could be either way round:
counted as failed, never as unreadable, never a misread), `engine_read_as` (`numbers`, `dates`, `years` or
`text`: how the engine read the column), `would_read` (for a column the engine reads as text: how many of its
values the engine's own reader would read as the type), `mixed_scale` (`{fractions, percents}` for a
percentage column holding both 0.12 and 12: flagged and signalled) and `private` (`"withhold"`, `"code"` or
null). A cell is unreadable when its text is not blank, is not one of the engine's placeholders, and the engine
holds no value for it. A percentage column is on one scale: a value written with a % sign is already a percent;
the values written without one are fractions only when at least 95% of them lie between 0 and 1; a value outside
the chosen scale is out of range. A withheld or coded column's number and date tests are not run (the engine
reads its codes): `action` says so. Its examples and its cells in `downloads.contract_flagged_csv` read
`value withheld` or `value coded`; every other example is scrubbed as the download is.
A level whose 0 the AI's analyses read as "no value" (5.3) gets a note row right after its own test (30 September
2026): `note: true`, `zeros` (how many), `checked` (the values the analyses read in the column), `failed` 0,
`signal` false, and `action` (= `brief`) "note: 550 zero values in VALUE are not counted by the AI's analyses (they
fall on weekends between non-zero rates, the pattern of a day with no value); the engine's own reading is unchanged
and the tests changed no value" (with, when some zeros stay 0, "; 3 other zero values stay 0 (...)" inside the
brackets, as 5.3 says). It never fails, never signals the planner and flags no cell; the report writer
reads it among the data-test lines. A column whose tests the visitor turned off gets no note row.

### 5.3 `ai_analyses`

`{items[], refused[], rows: {kept, set_aside}, date, note}`. Every analysis reads only the rows the engine kept
(the rows of `downloads.clean_csv`), as the engine read them; `rows` counts them and the rows it set aside.
`date` is the axis: ONLY the layout's date or the column the plan gives the date ROLE (a column merely typed date
with another role is never the axis), read by the engine as dates or as whole years. A kept row with no date
drops out of a time analysis and its sentence counts it; a kept cell the engine could not read is counted as
missing and the sentence counts it. A flow or a count is aggregated by year as its yearly total (every row
added), a level as its yearly average; the sentence says which. A year is complete when all 12 of its months
(all 4 quarters for quarterly rows) hold a value. A trend whose 95% range includes zero claims no direction.
**The trend test (wave 4, T1, section 5.11).** The trend item carries `test` (its lead series) and `tests` (every
series): `{name, series, n, rho, rho_null, slope, se, t, ci [lo, hi], level: 0.95, p, p_random_walk, B, seed, size:
{nominal: 0.05, simulated, claims, newey_west, series, cell: {n, rho}}, verdict: "rising"|"falling"|
"no_settled_direction"|"not_graded", why}`. The sentence says "rose" or "fell" only on a rising or falling verdict, "shows
no clear rise or fall" when p >= 0.05, and "moves by ..., but no direction is claimed: <why>" otherwise; the table's slope
and range are the test's (per decade when the years span 20 or more). The method text quotes the simulated size of the
condition nearest the lead series (`size_words`). Only rising or falling may appear in a title (track C).
When the engine refuses its own business analysis (its gate: it set aside more than
`gate.DEFAULT_POLICY.max_quarantine_rate`, 20%, of the rows), no analysis is drawn from the rows it kept either:
`items` is empty, `refused` holds one line ("trend, extremes: the engine set aside 30.0% of the rows (180 of
600), over its 20% limit, so no analysis is drawn from the rest"), and `gate` is `{over, pct, limit, aside,
rows}`. When the gate trips, `plan_signals` is empty (no data test, step or analysis signal), so the planner is
not asked again: a plan cannot make an unreadable cell readable, and the one change a planner can make, setting
the unreadable column aside and reading another, is the wrong-axis result the gate exists to prevent (review
cases f and g). The page does not ask again either when a report's `ai_analyses.gate.over` is true or its
headline is the gate's, whatever signals the report carries.
**Rows the plan sets aside (`ai_plan.row_drops`, 1 October 2026).** Every plan step that sets rows aside
(`exclude_rows`, `keep_rows`, `exclude_blank`, and `date_from_year`'s rows before 1900) is one item `{op, column, rows,
of (the file's rows), pct, valued, reason, check, compared, text, notice}`; its line in `ai_plan.applied` gives its share
("dropped 6,694 rows (19.8%) where ..."). `reason` is the plan's own words: the first quality risk that names one of the
step's values, else one that names its column beside a word for setting rows aside; "" when none does. When the
reason claims the rows repeat or overlap others (duplicate, overlap, double count, repeat, umbrella, already counted),
the engine checks it: `check` says how many set-aside rows equal a kept row on every column but the step's own and the
key-like ones (`compared`), each value trimmed ("none of these rows duplicates a kept row", or "N of these rows (x%)
duplicate a kept row"); else `check` is "". `text` is the whole disclosure (the plan card, the PDF's Appendix A);
`notice`, from 10% of the rows (`PLAN_DROP_NOTICE_PCT`), is one line for the summary ("The AI plan set aside 6,694 rows
(19.8%): <reason> The engine checked: ..."; else ""), which the page prints under the bottom line and the PDF in "About
this report". `valued` (read from 10% of the rows, else null) is how many of the rows hold a usable value of the plan's
primary measure: filled (not blank, not one of the engine's placeholder words), a number by the engine's reader in a
column it reads as numbers, and not a 0 the analyses read as no value (a level's placeholder zeros); every row when the
plan names no primary measure. When the `valued` rows reach 10% of the file's rows (the integration pass, 1 October
2026: the FX plan's 569 rows with a blank VALUE are 16.1% of the file but hold no rate, and asked for a re-plan that
could change nothing) the step is also a plan signal of kind `other` with its column and the detail "the plan's filter
set aside 6,694 of 33,878 rows (19.8%); keep them unless the goal needs them excluded" (with ", N of them (x% of the
rows) with a value in <primary>" after the share when not every row holds one, and the check), so the one re-plan may
keep the rows. A step whose rows hold no usable value is disclosed as above and never a signal. A step's values are named only when they are 1 to 3 short values of a column the
visitor did not withhold or code.
A column the visitor withheld or coded is never an axis, a group, a driver or a measure; a withheld one is
never named ("a column you withheld").
**Zero as a placeholder (30 September 2026; the evidence rule, final review the same day).** A column the plan
types `level` (a rate, price, index, balance or ratio) may use 0 for "no value", and the analyses then read its
zeros as missing, but only on the evidence (the share of zeros alone deleted a 0% policy rate held for seven years,
paid-off balances and stockouts): the zeros are at least 1% of its values and at least 95% of the others are
positive; read in date order within each series (with no date, in the rows' order) at least 80% of them sit alone
or 2 or 3 together between positive values, and no run is 5 or more long (5 dated zeros in a row are a real period
of zero); and either they fall on one or two days of the week that hold at least 80% of them and at most half of the
other values (the FX weekends), or the values on either side of a run are within 10% of each other for at least 80%
of the runs and for at least 3 separate runs (`PLACEHOLDER_RESUME_RUNS`: one zero, or two, is never enough; final
review, 30 September 2026). Zeros at random between values that jump about are real. **The series (final review, 30
September 2026):** when the dates repeat (fewer than 95% of the dated rows have a date of their own), the text column
that, with the date, tells at least 95% of the dated rows apart, the one with the fewest values (then the first); no
series when the dates alone tell the rows apart or no column does (`_series_groups`); the profile and the analyses
read it the same way (it was the plan's entity in the analyses and no series in the profile). **Only the zeros the
evidence describes are read as missing** (it was every zero of the column): on the weekday evidence, the zeros on
those days in a run of 1 to 3 between positive values; on the other, the zeros of such a run whose values on either
side are within 10%. A zero at the start or the end of a series, on a row with no date, or outside that pattern
stays 0 and is counted apart. Each sentence that reads the column says how many it left out and the evidence, true
of every one of them, never a bare "a rate of 0 is a placeholder": "(550 zero values in VALUE are not counted: they
fall on weekends between non-zero rates, the pattern of a day with no value)", "(149 zero values in usd_cad are not
counted: they sit alone or two or three together between non-zero rates that pick up where they left off, the
pattern of a missing value)" ("rates", "prices", "index values", "balances" or "ratios" when the column's name or unit
says so, a unit "per" another being a rate; else "values"), then the zeros that stay 0: "; 3 other zero values stay 0
(1 at the start or end of a series, 2 on rows with no date)" ("outside that pattern" for the rest); the Data tests
card's note row says the same. The profile's `zeros_missing_if_level` is the same rule on the rows the engine kept,
in the file's date order and within the same series. The engine's own table, its checks and every download keep the
zeros. A flow or a count (`flow_amount`, `count`), where 0 is real, and every other type keep
their zeros.
**Groups ranked on 5 rows.** `compare` leaves out a group with fewer than 5 values (`COMPARE_MIN_GROUP`) and says
how many ("8 groups with fewer than 5 rows are left out."), and how many more groups beyond the 12 with the most
rows are not shown. `rank` ranks an entry whose figure adds up or averages several rows (transactions, reviews)
only on 5 rows or more in the ranked year (with no date, over the file; `RANK_MIN_ROWS` = `COMPARE_MIN_GROUP`): the
entries below are left out of the table, the chart and the map, and counted in the sentence ("8 brand entries
with fewer than 5 rows in 2022 are not ranked."). A panel (one row per entry and date, at most 1% of the rows
repeating a pair: countries by year) ranks each entry's own figure, with no minimum.
**Groups ranked by an average are ranked by a weighted average (30 September 2026).** The minimum alone left brands
with 5 to 8 five-star reviews on top of a ranking of 5,581 reviews. `rank` on an average (a level or a rating; not a
panel) and `compare` rank each group by the IMDb-style weighted rating `(n * its average + m * the average of every
row in the ranking) / (n + m)`: its own n rows plus m rows at the overall average (the ranked year's rows, or the
file's, every entry counted, those under 5 rows too; decibels weighed as energies). **m is the file's own (final
review, 30 September 2026; it was a fixed 10):** the empirical-Bayes weight, the pooled variance of the rows within a
group over the variance of the groups' true averages, by the method of moments on the groups with 5 or more rows
(the variance of their averages, ddof 1, less the average of each one's sampling variance), rounded to whole rows and
held to 5 to 50 (`SHRINK_M_MIN`, `SHRINK_M_MAX`); when the averages differ no more than chance makes them it is 50,
and when it cannot be estimated (fewer than 5 groups of 5 rows, `SHRINK_MIN_GROUPS`, or no spread at all) it is
`SHRINK_M` = 10, and the sentence says so: "(10, a set value: fewer than five brand groups have 5 or more rows, too
few to estimate it from)". **Why 5 groups (final review, 30 September 2026; it was 2):** the variance of the groups'
true averages has one fewer degree of freedom than there are groups, a relative error of about sqrt(2 / (groups -
1)), 100% with 3 groups and 71% with 5; simulated (100 rows a group, true m 25 and 11), the estimate lands within a
factor of 2 of the truth in 25 to 29% of files with 2 groups, 37 to 41% with 3 and 54 to 60% with 5, and reads "no
more than chance" in 24 to 36%, 11 to 19% and 2 to 6%. `rank` estimates it on the ranked scope's entries, `compare`
on its groups. **When the chance reading applies, `compare` never offers a range for the gap** (final review, 30
September 2026: "a gap of 0.199 (95% range 0.0111 to 0.396)" beside "no more than chance" in 92 of 300 files with no
true difference): the top and the bottom are the two ends picked out of every group compared, so the sentence reads
"a gap of 0.199 between the top and the bottom of the 12 groups, the two ends picked out of them, which chance alone
can make this wide". `rank`'s table is `[<entity>, "Weighted
average", "Average <measure>", "Rows", "Change over 10 years"]` (the last column left out when no row has a figure 10
years before), its chart and map carry the weighted averages, its title reads "Highest average <measure> by <entity>
in <year>", and its sentence names each of the top three's weighted average, own average and rows and states m:
"Each brand's average is pulled toward the overall 3.84 stars (the average of all 5,581 rows in 2022) by the
equivalent of 13 rows at that average (13 is estimated from how much the brand averages differ against how much rows
differ within a brand), so an average on a few rows counts for less than one on many." (a held estimate reads
"estimated at 524 from ..., held to 50"). `compare` keeps its columns (`Rows`, `Average`, the 95% range, `Median`) and
adds `Weighted average` last; its rows, its highest and its lowest follow the weighted average; its sentence states
its m the same way. A ranking of totals (a flow or a count: its sum) and a panel's own figures are not weighted. On
the preflight's reviews file (2022, m = 13) the top ten are G-STORY (4.92 on 12 reviews, weighted 4.36), Elgato
(4.91 on 11, 4.33), KIWI design (4.42 on 53, 4.30), Exquisite Gaming and ivoler (5.0 on 8, 4.28), PERFECTSIGHT and
sisma (4.89 on 9, 4.27), GeekShare (4.69 on 13, 4.26), daydayup (4.59 on 17, 4.26) and Bethesda (4.60 on 15, 4.25);
the 5.0-star brands on 5 or 6 reviews leave the top 10 (the test fixture: `tools/fixtures/eval/brand_ratings_2022.csv`,
star counts only). The departments' `compare` there holds m at 50 (estimated at 524).

### 5.4 `profile_json(data, name, flagged, decisions, as_of)` (the planner's profile)

`flagged` is the scan's `{column: kind}`, or `{column: {kind, decision}}`, or `privacy.flagged`; `decisions`
is the visitor's `{column: "withhold" | "code" | "keep"}` (by either spelling of the name). Every column the
engine's scan flagged or the adapter's personal-column check added (5.5) is flagged whatever `flagged` says; a
flagged column with no decision is withheld, the engine's default. Withheld: absent (never named, never
counted, never the time column). Coded: its name, `filled`, `distinct`, `blank`, `numeric_share`,
`date_share`, `integers`, `percent_sign`, `looks_personal: true` and `privacy_flag`, and no field that holds a
value. Kept: like any column, `looks_personal: false`, and no `privacy_flag` (the page's own filter marks it the
same way, and sends it only after the visitor has ticked the box that names it).
The facts are the engine's reading under these same decisions (5.6): a coded column is read as its codes (its
shares describe the codes the engine reads), a kept one like any other. The page asks ONCE, after the visitor's
choices and only after "Continue with the AI" (engine/worker.js `profile`, answered by
`plan_profile_json`: `{profile, landed: {header: landed name}}` for every column of the file; `landed` stays in
the page, which uses it in its own second filter to match each profiled column to its flag exactly and to keep
every spelling of a withheld column's name out of what it sends). The scan's run leaves its reading
behind (keyed by the file's sha256 and its decisions, every flagged column withheld), so a profile under those
decisions costs no second landing; any other choice costs one profile pass of the engine.
`time` and `analysis_limits` are over the rows the engine kept, skip every withheld or coded column before
choosing, and name only analyses the planner can ask for.
The profile's `name` is `"[your file]"`, never the file's name (the page's /report payload says the same).
`context_terms_version` is the version of `engine/context_terms.json`, the list the adapter builds the report's web
searches from (5.8, the web searches): the planner's `plan.context` must use its terms.
What a column not flagged (or kept) shows: a number column its `min`, `median` and `max`, and, when some of its
values are exactly 0, `zeros` (how many) and `zeros_missing_if_level` (true when they have the pattern of a missing
value under 5.3's evidence rule, on the rows the engine kept, in the file's date order and within the same series
as the analyses read (`_series_groups`, never by a withheld or coded column): typed `level`, the analyses read them
as missing; a 0% rate held for years, paid-off balances or stockouts at random are false; the worker's profile
filter must pass both for the planner to see them); a text or date column
of at most 300 distinct values (median length 60 characters or fewer) its 12 commonest values (`top_values`)
and, above 12 distinct, every value (`values`), each cut at 60 characters, unless its values look personal
(`looks_personal: true`); the date column's first and last month (`time`). The page's consent says exactly this.

**`profile.structure` (WAVE 4, track A1, 1 October 2026).** For a table read by its structure (§5.10) the profile adds
`structure` = `nl_structure.profile_block`: `{kind: "cube", publisher, series, months, date, measure{column, type, uom,
scale}, metadata[] (column names the profile shows), flag_column, dims[{column, role, members, total?, parts?, depths?,
nsa?, sa?, component_of?, components?, alternatives?}], slices[{id, use, where{column: member or "*"}, default?}],
breakdowns[{id, dim, parts, depth}], rules[]}`, at most 6,000 bytes (`PROFILE_CAP`, JSON with no spaces, UTF-8); trimmed in
order: the rules, the drill-down slices and breakdowns, the member maps; past that the block is dropped (never the
profile). Privacy: only dimensions the profile shows (never a withheld or coded column, which the structure never read),
and only member strings that column's own `values`/`top_values` already hold, cut at 60 characters as the profile cuts
them; a member not there is left out. Slice ids match `^S\d{1,2}$`, breakdown ids `^B\d{1,2}$`. The plan may name
`"slice": "S1"`, `"breakdowns": ["B1", "B2"]` (at most 4) and `"momentum_slice"`; `_validate_plan` keeps an id only
when the cached structure holds it, and with a slice id a `keep_rows`/`exclude_rows`/`exclude_blank` on a structure
dimension is ignored with a note in `ai_plan.refused` ("keep_rows on Type was ignored: the slice S2 chooses the table's
rows"). Retail (465 series): 1,993 bytes, S1 headline, S2 momentum (seasonally adjusted), S3 component (e-commerce), B1
GEO (13 parts), B2 NAICS depth 1 (9 parts).

*Wave 5 additions to the block* (7 October 2026, section 5.12): a dimension that names what is measured adds
`measures: {dim, members[{id: "M1", name, uom, type, default, precision}]}` (ids `^M\d{1,2}$`, in file order; a plan names one
with `"measure_member": "M1"`, an id only; `_validate_plan` keeps it only when the table holds it and it is not a precision
member, else a note in `ai_plan.refused`); a dimension with no total row is `{role: "partition", no_total: true, parts: N}` (no
`total`) and its slice leaves the dimension out of `where`; a quarterly, half-yearly or annual table adds
`period: {kind, window}`. The roles are the worker's vocabulary (`insight-proxy/src/plan.js STRUCTURE_ROLES`): a rate's
published aggregate is `"aggregate"` (the adapter wrote `"rate_aggregate"`, which the worker would have dropped the whole block
for), and a dimension of one member is left out of `dims`.

### 5.5 The adapter's personal-column check

On top of the engine's scan (never instead of it): a column the scan did not flag joins `privacy.flagged`, with
the same default (withhold), when at least 60% of its values are email addresses, phone numbers written with
separators, account or card numbers written in groups of four digits (4-4-4, 4-4-4-4, or 4-6-5: never read as a
phone), or street addresses (a house number, the street's name, then a street type; "12 ct", "12 Ct Paper
Towels", "10 Sq Ft Tile" and "3 Way Switch" are not addresses: no street's name stands between the number and
the type, and a short type such as Ct, St or Way counts only with nothing numeric after it); or when its name
says it holds people and at least 60% of its values could be a person's name. The name says it holds people when
its last word (after "assigned", "on duty" or a number) is a word for a person (member, customer, client, user,
technician, attendee, salesperson, rep, agent, driver, staff, author, owner, assignee, who, and the like in
Spanish, Portuguese, Italian, French, German, Dutch, Nordic, Polish and Turkish), when a word for a name stands
with no thing right before it (customer_name, Name, nombre, nom, vorname; never product_name), when it is
`<verb>ed_by` (created_by, sold_by) or `assigned_to`, or when it holds a word for a name or a person in Russian,
Arabic, Chinese, Japanese, Korean or Hindi; never user_agent, customer_id or member_since. A value could be a
person's name when it is 1 to 4 words of letters in any script or case, with initials ("J. Smith"), particles
("de la", "van") and the comma form ("Fairweather, Marisol"), and no word that says firm, role, tier, software or
a way to pay ("Acme Corp", "Gold Member", "Sales Manager", "Google Chrome", "Card"). There is no minimum number of distinct values: a
column of one person's name is flagged. Its `kind` is `person's name`, `email`, `phone number`, `account or card
number` or `street address`. It is registered as the engine registers a flagged column (a pending row in its
column register), so the engine's own decide, code and withhold apply to it. A column of names under a heading
the check does not know ("Stylist") can still be missed, and the page says so before anything is sent.

**A category is not free text: `privacy.released` (WAVE 4, the lead's amendment AM1, 1 October 2026).** The engine's scan
flags any column of 20 or more different wordy values as free text, so an official table's industry column (30 NAICS
labels, each on 1,185 rows) was withheld and the table read as nonsense. Before any decision (`_decide_and_guard`), the
adapter lifts that flag (`_release_categories`) only when ALL hold: the scan's only reason is free text (its `kinds` are
empty: no value shape and no name hint); at most 300 different values (`RELEASE_MAX_DISTINCT`) and at most 5% of its
filled cells (`RELEASE_MAX_SHARE`); every label on 5 rows or more (`RELEASE_MIN_REPEAT`); its values do not look like
people's names (`_looks_like_names`, and fewer than 30% of its labels name-shaped by `_name_shaped`: 1 to 4 capitalised
words of letters with none of a category's glue words, whatever list of given names is at hand; "Oskar Lucia Brennan
Tanaka" under "Stylist" stays flagged); its name does not say it holds people (`_person_hint`); and its name is not
sensitive (AM1, `SENSITIVE_HEADER`: diagnos, condition, disease, illness, medic, health, symptom, treatment, drug,
religio, faith, ethnic, race, nationality, citizenship, gender, sex, sexual, orientation, disab, pregnan, criminal,
offence/offense, convict, union, political, party, vote, salary, wage, income, debt, credit, immigra, visa: health,
religion, ethnicity and the like are sensitive even when categorical). A release deletes the column's row from the
engagement's column register (runtime state, never engine code) and is recorded as `privacy.released[{column (landed),
header, distinct, rows, min_repeat, text, why}]`, `text` "Read as a category, not personal data: <header> (<n> labels)".
The visitor can still withhold it: a `withhold` or `code` decision for it (by its header or landed name) keeps the flag
and that decision. The page's consent step shows every released column in those words with Use (default) and Withhold
(src/js/50-try.js `releasedHtml`; `tools/check_ui.js` try-released-category-*); a withhold reaches the run and changes the
plan key (`T.planKeyText`, which counts a released column's withhold as a choice), and the worker re-runs (engine/worker.js
`onRun`) and re-profiles (`onProfile`) for it; Use sends no choice. `privacy.released` is `[]` when nothing was released.

### 5.6 A withheld column drives no cleaning rule

The engine derives its cleaning rules from every column it profiles and has no option to leave one out. So,
after its values are read for the scrubber and before the engine profiles or cleans anything, a withheld column
is landed as text no rule reads: each distinct value, byte for byte as the file holds it (case, spaces and a
placeholder such as N/A included), becomes its own opaque code of capital letters; only an empty cell stays
empty. No cleaning rule reads or changes its values, so no row is set aside or changed because of what it holds;
the data-health check still counts its empty cells (not its spellings), and both exact-duplicate checks compare
its codes, which are equal exactly where the file's values are: the column only keeps otherwise-identical rows
apart, as they are in the file (the health's duplicate count equals the file's own).

### 5.7 `results_json` / `results_for_ai` (the report writer, and the charts and tables a share link carries)

The analyses' own charts (1 October 2026): a line chart's series are its lines, at most 4 (`LEGACY_LINES_MAX`); a bar
chart's series are its bars, every one up to 24 (`LEGACY_BARS_MAX`; one cap of 4 for both sent 4 of the FX histogram's
12 bins); a scatter keeps a sample of 120 points. The worker's `sanitizeLegacyChart` keeps the same caps (the integration
pass, 1 October 2026; it cut any series list to 4 before).
`plan_row_drops` carries each item of `ai_plan.row_drops` as `{rows, of, pct, text, notice}` for the PDF; the writer
reads the steps' shares in `plan_applied`, and a step of 10% or more first among the `limitations`, at most 240
characters: "The AI plan set aside 6,694 rows (19.8%); the engine checked: none of these rows duplicates a kept row. Its
reason: ..." (a reason that carries a figure is left to `quality_risks`: the guard reads a limitation's figures as the
engine's). `reading` is cut at a word with an ellipsis, never mid-word (so is the plan's `understanding` and `goal`, at
600 characters, when the plan is read).

`row_noun` (the integration pass, 1 October 2026) is not the adapter's: the page adds it to the results it posts to
/report, copied from the plan that ran (`plan.row_noun`, one lowercase word of 3 to 20 letters a to z naming what one row
is, "review"; anything else is not sent: src/js/50-try.js `T.rowNoun`), and the worker's count-noun check reads it and its
plural as words for rows. A share link's copy of the results (src/js/45-report-pdf.js `shareResults`) keeps, whatever its
cap, `row_noun`, `health_explain` and every `plan_row_drops` item `{rows, of, pct, text, notice}`, so the worker's shared
PDF says what set the health score and which rows the plan set aside.

Wave 4 (section 5.11): `forecast.audit` `{label, status, trusted, grade_label}` when the forecast shown was
back-tested, and `findings[].interval_coverage` `{measured, lo, hi}` (fractions) when its why quotes an interval's
measured coverage; the worker keeps only keys it knows, so track C adds them to its schema and its guard.

No text names a withheld column (a finding, health issue, fix or limitation about one is left out; any other
mention reads "a column you withheld"), no withheld column's data test line is sent, and no text quotes a cell
as an example ("(for example ...)" is cut). `plan_signals` (the planner's feedback) follows the same rules.
A flagged column the visitor kept is not withheld: its name and values go like any column's. Option B (the owner's
decision, 29 September 2026) lets the page send one only after the visitor ticks a box that names it; the writer
is then told so once, at the end of `reading`, in one line that names the kept columns and holds no value: "The
visitor chose to send these personal columns to the AI: staff_name." With no kept column there is no such line.
Each analysis's table goes once (final review, 30 September 2026): in `tables`, under the analysis's own title, in
the analyses' order; `analyses[]` carries `{title, sentence, method}` and no `table` (it was the same table a second
time; the worker reads `tables` for `[TABLE:n]` and for the figures it accepts, and needs no `analyses[].table`).
`primary` (integration pass, 30 September 2026) is the claim the report leads with, `{id, claim, grade}`, or null: the
claim `scenarios` breaks down (its `basis`, the total of the plan's primary column), else the engine's primary claim
(`primary_metric`: the gate's primary, set from the plan's primary column, so the average when that column is a rate
or a price and the breakdown is refused), else null. `claim` is the same text as its finding's in `findings`, `grade`
the report's grade word; a claim about a withheld column is never sent, so `primary` is then null. The PDF's key
figures lead with it (an FX file's with the average rate, the three-currency f3 file's with the EUR total, never the
row count the engine's own gate chose there; since the live baseline of 30 September 2026 `primary_metric` is that
claim too, §1). Each analysis's `sentence` and `method` go whole, or cut after the last whole word that fits the
worker's caps (700 and 300 characters, `ANALYSIS_TEXT_MAX`) with an ellipsis, never inside a word, a figure or the
"[your file]" placeholder (`_cut_words`; a method note once arrived as "...trained only on the blocks befor").

**The estimand first (WAVE 4, 1 October 2026).** `results_for_ai` starts `{ok, estimand, structure, input, goal, ...}`:
`estimand` is the report's `estimand` (§1) with every text through the same `safe()` (`_estimand_for_ai`: text, slice
`{dim, member, why}`, measure, comparison, figures `{value, text}`, sum_checks `{dim, total, parts, verdict,
unallocated_latest}`, excluded `{what, why}`, plan_source, inference (`_inference_for_ai`: mode, publisher, describe, how_known, revisions, quality, grade_label, the words through the same scrubber)), or null; `structure` is its summary
(`_structure_for_ai`: kind, usable, publisher, series, months, dims `{column, role, members, total, parts, nsa, sa,
depths}`, slice, reason, flags `{column, by_kind, quality_of_headline}`, corrections `{dim, kind}`), or null. Every figure in
them is also a scenario item (§5.8), so the worker's guard can index it. Then the forecast audit and the trend tests
(track A2). Wave 5 (section 5.12) adds, only when they apply: `estimand.built_from`, `estimand.measure_choice`,
`estimand.period` (`{kind, noun, nouns, window}`, a quarterly or an annual table), `estimand.measure.type_basis`
(always), `estimand.sum_checks[].built_from_parts` and `suppressed_part_months`, `estimand.figures.<k>.months` (only when a
window figure adds up fewer periods than the whole window), and `forecast: {frequency, reason}` for a table with no
forecast because of its period.

**The trend test, the forecast's back-test and the layout rows (WAVE 4, track B, 6 October 2026).** `analyses[]` carries
`{title, sentence, method}` and, for a trend analysis, `test`: `{verdict, name, n, p, p_random_walk, size {nominal, simulated,
claims, newey_west}}` in about 240 bytes (`_trend_test_for_ai`; the test's slope, range, momentum and simulated cell stay in the
report). `verdict` is one of "rising", "falling", "no_settled_direction" or "not_graded" (a word the writer has no name for
is sent as "not_graded"); `name` is the method's (at most 90 characters), never the series' name; a number that is not finite is
left out. `forecast.audit` is `{label, status, trusted, grade_label}` (status "passes", "unclear" or "fails"; a seasonal-naive
champion can pass on coverage alone; the report states ONE count of how the range held, the audit's: `forecast.coverage` is
sent only when there is no audit). A row count the table's layout fixes is no number: `findings[].layout_artifact` is true
(and `value` is left out) and `forecast` is `{row_forecast_dropped: true, reason}`; no audit goes with it. When the payload is
over its budget, the scenario items that go first are the facts, then the parts with the smallest contributions; a part that
moved against the change and the unallocated part are kept to the end (`scenarios.refused[0]` says how many were left out).

### 5.8 `scenarios` and the web searches (design B of `plan/AI-INSIGHTS-DESIGN.md`, 29 September 2026; the searches built from a fixed list, 30 September 2026)

The report writer may quote a figure only when the payload holds it. `scenarios` holds the figures it needed and
the engine does not state: where the change sits, price against volume against mix, figures per unit, the run
rate, what each 1% is worth, the gap to the largest segment and the forecast added up. `engine/nl_scenarios.py`
computes them at the end of `run()`, after the true headline; they are descriptive arithmetic on the rows the
engine kept (the rows of `downloads.clean_csv`, without any column the visitor withheld or coded), never a
statistic, and no engine file is changed.

`basis`: `{finding_id, claim, grade, grade_words, measure, how, unit, windows{prior[a,b], latest[a,b]},
rows{prior, latest, units_missing}, segment{column, levels[], folded[], entered[], exited[]}, reconciles}`, or null
when no claim is broken down. **The claim (final review, 30 September 2026):** the plan's primary column's total,
its first in the engine's order (one currency, one kind of row); else the engine's primary claim when it is a total
or a row count, or, when that is an average, the total of the same measure; with no primary to follow, the first
total, else the row count. When the primary is a level or an average (the plan types it `level`, `percentage`,
`log_scale`, `rating`, `ordinal` or `duration`, or the engine's primary is an average with no total of its measure:
FX rates, a balance, a rating) the block is refused, never read as a row count: `basis` null and `refused` first
"the headline is an average, so it has no parts that add up; see the headline finding" (the page takes its key
figures from the primary finding). A total of one kind of row, one currency or without a status the engine leaves
out is broken down on those same rows (the engine's own conditions); an average or a like-for-like restatement
never is. `how` is `total` (`measure` the column) or `count` (`measure` "rows"); `unit` is the currency's code when
the claim is in one currency (the engine's currency split: "EUR" whatever unit the plan gave the column), else the
AI plan's unit for the measure ("" for none), and every item of the block is in it; `windows` are the claim chart's
(the latest 12 months against the 12 before); `rows` counts the claim's rows in each window, and `units_missing` the
rows with no units (null when no units column is read). `segment.levels` are the levels shown (a level with rows in
only one window among them); `folded` the levels (and "(blank)") folded into "other"; `entered` the levels with rows
only in the latest window, `exited` those with rows only in the window before.

**Reconciliation.** In every month of both windows the rows' monthly total (or count) equals the claim's charted
value (`charts[trend.<claim_key>].data.values`) to 1e-6 of the value (at least 1e-6 absolute), or the whole block
is refused: `items` is empty, `basis.reconciles` false and `refused` holds "the breakdown could not be reconciled
with the engine's own monthly totals, so it is not shown". The contributions and the price, volume and mix parts
each add up (math.fsum) to the change to the same tolerance, or that group is left out and says so.

`items[]`: `{id, group, segment, label, value, text, kind, unit, grade, parent_grade, grade_words, assumes,
inputs{columns[], window, op}}`.
**Order (30 September 2026): the worker's cap.** The worker keeps up to 160 items (`insight-proxy/src/report.js`
`capScenarioItems`, `SCENARIO_TOP_SEGMENTS` 6) by this priority, and the adapter writes them in the same order, so
a reader that keeps the first N items keeps exactly what the cap keeps: the core first, every item of every group in
the order of `group` (the headline, contributions, price, volume and mix, figures per unit, the run rate, the
sensitivity, gaps, the forecast, the historical range, the facts) with the per-segment groups (`contribution`, `per_unit`, `gap`:
`nl_scenarios.PER_SEGMENT`) cut to the 6 segments with the largest |contribution| (`nl_scenarios.TOP_SEGMENTS`,
ranked by `nl_scenarios.segment_rank` as the worker ranks them: by the size of the contribution item of kind `change`
whose unit is not "%", then where the segment first appears); then the rest, segment by segment in that rank, each
segment's items in the order of `group`. Within a group each part keeps its order (segments by (-|contribution|,
name)). A file with 12 segments and units whose segments all moved with the total has 146 items: a core of 80 (the
facts last among them) and 66 more, the 7th to 12th segments' 11 items each (the test's twelve regions moved both
ways, so they have no share items: 134, a core of 74 and 60 more); with 6 segments or fewer every item is in the core
(ship2's 58 items come in the order of `group`). The groups: `headline` (the two windows' totals, the change, the change in
percent), `contribution` (per segment: its two totals, its contribution, its share of the change, its own change in
percent; a level with rows in only one window is labelled "Uptown (new in the latest 12 months)" or "Mall (not in
the latest 12 months)", its missing window's total 0 and no own change in percent for one that entered),
`price_volume_mix` (price at the latest units, volume at the prior price per unit, mix at each segment's prior price
per unit; they add up to the change; mix only with a segment whose every level has units in the window before, so a
level that entered leaves out the mix and says so; `assumes` states the base and the arithmetic, never an
assumption (they add up by construction): "measured against the 12 months before; the three parts add up exactly to
the change", or "... the two parts add up exactly to the change" with no mix item), `per_unit` (overall and per
segment: both windows and the change in percent, `per_unit.change_pct` and `per_unit.<segment>.change_pct`, kind
`change`, unit "%": "revenue per unit 41 to 47.9", "+16.8%"), `run_rate` (the latest 12 months and their average
month; never a 3-month annualised rate), `sensitivity` (each 1% of the measure over a year at the latest level), `gap` (each segment's distance to
the largest level in the latest 12 months, chosen among ALL the levels (a folded one or one that entered too) and so
named in the label ("below Uptown (new)'s, the largest store by total sales in the latest 12 months"), in the measure
and in percentage points of the total, and, with units, what its units would have made at the largest's price per
unit when that is higher; never for "other", the largest itself or a level that exited), `forecast`, `history_range`
(below), `facts` (months and distinct dates in the kept rows, the first and the last date). `kind` is `amount`, `change`, `count`, `percent`,
`points`, `per_unit` or `date` (a fact's first or last date: `value` is the date text). `text` is
`nl_scenarios._fmt_item(value, kind, unit)`, the one function, in the adapter's own formats (`_fmt`, `_amt`,
`_pct_text`): a rise carries "+", a fall the true minus sign, a change in percent is `kind: change, unit: "%"`, a
share `kind: percent`. `value` is finite, rounded to 1e-6; an item whose value cannot be formed is absent.
`window` is `prior`, `latest`, `both`, `history` (a historical range item) or null. `assumes` is the item's assumption in words (a run rate, a
sensitivity, a what-if, the price, volume and mix split, a forecast sum), else null.

**Grades (final review, 30 September 2026).** The headline items (`headline.*`: the claim itself) carry the claim's
`grade`, in the page's words as `findings[].grade` has them (`CONFIRMED`, `WATCH`, `NOT_ENOUGH_DATA`), with
`parent_grade` null and `grade_words` "the claim itself, graded WATCH". Every derived item (`contribution`,
`price_volume_mix`, `per_unit`, `run_rate`, `sensitivity`, `gap`) is not graded itself: `grade` null, `parent_grade`
the claim's grade and `grade_words` "part of a change graded CONFIRMED; not graded itself" (a region that fell 1.63%
inside a CONFIRMED rise read CONFIRMED before). A forecast item carries the forecast's `grade` ("the engine's
forecast, graded CONFIRMED (usable for planning)"), a fact `grade` null ("a fact about the rows, not graded"), a
historical range item `grade` null ("a fact about the file's past, not graded: history, not a forecast"); all three
have `parent_grade` null.

**Guards.** A share of the change needs the total to move by at least 1% of its prior level, and every segment to
move the way the total did: when any contribution has the opposite sign to the change, or any share would pass 100%,
no share item is given (the amounts are) and `refused` says "shares of the change are not given: segments moved in
opposite directions, so shares of the net change would exceed 100%". A segment's own
change a prior total above zero; a figure per unit units above zero (and units on every row of the claim in both
windows, none negative). The units column is one of the engine's measures it adds up as units
(`measure.additive_kind`), for a money total only; with several, the plan's count column or a quantity word
decides, else none is read.

**The segment column (privacy).** A category, geography or segment column (the plan's role or type, else the
engine's dimension role) in the kept rows' frame, NOT in `privacy.flagged` at all (whatever the visitor decided: a
kept column's consent covered the AI report, not a breakdown), never one the plan calls a key, entity,
identifier, code or free text, never a number column or a column the claim already reads, with 2 to 12 levels
over the claim's rows in the two windows, at least 2 of them with 5 rows in each window, no level that the report's
scrubber would change (a phone number or an email address) and no value over 80 characters. A level with rows in
only one window is its own row (entered or exited: a store that opened, one that closed), never folded; only a level
in both windows with fewer than 5 of the claim's rows in either folds into "other", as does a blank; the gap group
never names "other".

**Forecast items** only when `forecast.available` and its grade is CONFIRMED (usable for planning): for 3, 6 and
12 months (as many as there are points) the sums of the points (`base`) and of their own 80% lows and highs,
labelled "the months' own 80% ranges added up, at least as wide as an 80% range for the total". A forecast label
leads with what the sum is ("Low: the months' own 80% ranges added up, ...") and ends with the series and its
months, so a reader that cuts labels short keeps the meaning. A forecast not usable for planning adds a line to
`refused` and no item.

**The historical range (`history_range`; review of the live baseline, 30 September 2026).** The engine forecasts
counts and totals only, so a report on an exchange rate had no outlook at all. For a level (the plan's primary column
typed `level` or `percentage`: a rate, a price, an index; with no primary in the plan, the engine's primary claim when
it is the average of a measure the engine does not add up), whose engine average-month claim exists, with 36 months
or more of monthly averages (3 years), the block states, for 12-month and 3-month windows, every past window (one
ending each month; they overlap) whose first and last month hold a value: the change of the monthly average across it,
and of those changes the 10th, 50th and 90th percentiles (linear interpolation), the share of the windows in which it
rose (kind `percent`) and the window count (kind `count`). Items `history_range.m12.{windows, p10, p50, p90, rose}` and
`history_range.m3.*`; the changes are kind `change` in the plan's unit (`points` for a percentage). The monthly
average is the mean of the month's values in the kept rows; a level's zeros that mark "no value" (the analyses' own
evidence, `nl_browser._zero_shape`) are not counted, and each item's `assumes` then says so ("550 zero values in
VALUE are not counted: they fall on weekends between non-zero rates, the pattern of a day with no value"), else
null. Every label says it is history, not a forecast; the windows item's label is the sentence the writer may copy:
"In the 104 past 12-month windows (Jan 2017 to Aug 2026; they overlap, one ending each month), the change in the
monthly average of value ran from −0.0635 CAD (1 in 10 lower) to +0.0785 CAD (1 in 10 higher); the middle was
+0.0162 CAD. This is history, not a forecast." (its figures are the percentile items' own texts). A level with
fewer months adds "no historical range of <measure>: it has N months with a value, fewer than the 36 (3 years) it
needs" to `refused`; a flow, a count, a rating or a row count has none.
**Wave 4 (T3, section 5.11): the windows overlap.** For each lag the block adds `history_range.mL.n_eff` (kind
`count`): how many independent windows the n overlapping ones are worth, n / (1 + 2 x the sum of the changes'
autocorrelations at lags 1 to L-1, stopped at the first negative one), at least 1, rounded (FX: 104 12-month windows,
about 13). `history_range.mL.rose` is now the COUNT of windows that rose (kind `count`, "68"), never a percentage.
With fewer than 10 independent windows (`HISTORY_NEFF_MIN`) the range is the lowest and the highest change
(`history_range.mL.min`, `.max`) instead of the 10th and 90th percentiles. The non-overlapping changes go beside them:
`history_range.mL.nonoverlap.{n, min, median, max}`, the change ending in the latest month and every L months before
it (for 12 months, one a year ending in the same calendar month). Every change item's value and text are at 2
significant figures ("−0.040 CAD"). The windows item's label is the sentence the writer may copy: "In the 104 past
12-month windows (Jan 2017 to Aug 2026; they overlap, one ending each month, so they are worth about 13 independent
ones), the change in the monthly average of value ran from −0.063 CAD (1 in 10 lower) to +0.078 CAD (1 in 10 higher);
the middle was +0.016 CAD, and it rose in 68 of the 104. Taking one window a year, each ending in Aug, the 9 changes ran
from −0.062 CAD to +0.056 CAD, with a middle of +0.017 CAD. This is history, not a forecast." (with "(the lowest)" and
"(the highest)" under 10 independent windows). `note` then adds that the historical range
items are facts about the past, not a forecast and not graded. **The worker (`insight-proxy/src/report.js`
`SCENARIO_GROUPS`) keeps only the groups it knows: until `history_range` is added there, it drops these items from
`/report`.**

**The segment the goal names (live baseline, 30 September 2026).** The question asked which departments stand out,
and keeping the review text switched the breakdown to `verified_purchase`, the engine's first dimension. The
candidates (above) are ordered: first the columns the goal names (the plan's goal, else the visitor's question; never
the default question), in the order it names them, a column being named when every word of its name is in the goal,
singular or plural ("departments" names `department`); then the plan's segment and geography roles, its category and
geography types, and the engine's dimensions, as before. The first that qualifies segments the claim.

`refused[]` says, in plain words, what was not computed and why (no dates, the engine's analysis did not run, the
date column withheld, the headline an average, no claim that adds up, no segment column, no units, shares of a change
under 1% or of segments that moved in opposite directions, no mix, a forecast not usable). `note` says what the block
is, and that the figures are shown rounded and add up before rounding.

**`results_for_ai`** carries `scenarios` 1:1 (the same keys, figures and texts; every word through the same
`safe()` as every other text, so a withheld column's name reads "a column you withheld" and the file's name
"[your file]") and appends ONE table to `tables`, after the analyses' tables: "Where the change in <measure> came
from" (it was "What drove the change in <measure>"), columns `[<segment column>, "12 months before", "Latest 12
months", "Change", "Share of the change", "Own change"]` less any column no row fills (the shares, when none is
given), a row per segment (at most 12, a level that entered or exited named so) whose cells are those items' own
texts. The frozen interface is `tools/fixtures/scenarios/scenarios-ship2.json` (ship2_privacy_orders.csv, analysis
date 2026-09-29, the personal columns withheld as by default; re-synced 30 September 2026 for the cap's order, the
same 58 items, and again for `parent_grade`, `grade_words`, `segment.entered`/`exited`, the gap labels and the note),
and the PDF check's `tools/fixtures/report-pdf/ship2-results-v2.json` carries it whole.

**The byte budget (30 September 2026).** The worker takes a `/report` body of at most 96,000 bytes
(`REPORT_MAX_BYTES`), which holds `results_for_ai` with the visitor's question and the planner's searches. When
`results_for_ai` would be over `RESULTS_MAX_BYTES` = 90,000 bytes (its JSON with `\u` escapes, never less than the
UTF-8 bytes the page sends), scenario items are dropped from the tail of the cap's order until it fits: the facts
first, then the per-segment detail beyond the top 6 segments (the smallest segment's last item first); only if that
is not enough do the core's segments go (the sixth, then the fifth, ...) and then whole groups from the end of the
order. It drops no more than it needs. `scenarios.refused` then starts with "N of the M scenario items are left out
to keep what the report writer receives under 90,000 bytes: the facts first, then the segments with the smallest
contributions", and the "Where the change came from" table is rebuilt from the items kept. Never an oversize
payload (final review, 30 September 2026): with every scenario item gone and the payload still over, the analyses go
from the last in the plan's order, each with its chart and its table, and `analyses_refused` starts with "N of the M
analyses are left out to keep what the report writer receives under 90,000 bytes: the last ones in the plan's
order"; past that (a payload the caps cannot make) the charts, the tables and the findings go from the end, and a
payload still over is `{ok: false, error}` with the reason, never sent oversize. Everything else is untouched, and a
payload under the budget carries `scenarios` 1:1. A planned run of a 12-territory orders file with six analyses
(`tools/test_nl_browser.py`, `_territories_file`) is 98,677 bytes whole and 89,395 after dropping 17 of its 134
items: the 4 facts and the smallest segments' per-unit and gap items.

**The web searches (`ai_plan.context_queries`; an allow-list by construction, final review, 30 September 2026).**
The report writer runs these searches on a search engine, where the visitor's consent does not reach, so a search
is never free text: not the planner's, and never anything from the visitor's file. The block-list check it replaces
(a free-text `plan.context_queries` vetted against the file) let names through in a dozen ways: a client column typed
as a category or a segment, a word inside a value, ł ø ı ð and "ue" spellings, CJK names, two-letter surnames, cp1252
and "|" files, and its own "ordinary words" rule (`tools/fixtures/review5`). Now:
* **The list** is `engine/context_terms.json` (packed beside the adapter, `CONTEXT_TERMS_FILE`), human-readable and
  version-stamped (`version`, sent in the profile as `context_terms_version`): `indicators` (30 macro and market
  indicators: "consumer price inflation", "retail sales", "exchange rate", "housing starts", "sales growth", ...),
  `sectors` (83 generic industries, never a brand: "video games", "grocery retail", "rental housing", ...) and
  `regions` (350: world regions, every country and territory by its English short name, Canada's provinces and
  territories, the US states and the District of Columbia, and 50 major cities; `region_groups` counts each group).
  Every term is ASCII and holds no digit.
* **The plan** names what it wants as `plan.context`: at most 4 items (`CONTEXT_MAX`; at most 12 are read),
  each `{"indicator": <a term from indicators, required>, "sector": <a term from sectors, optional>, "region":
  <a term from regions, optional>, "years": [from, to] or [year], optional}`. An optional term that is absent, null
  or an empty string is left out; a year is an integer (a string of 4 digits is read as its integer) from 1900 to
  2099, `from` not after `to`. Every term is compared after normalising (NFKD, accents dropped, case-folded, every
  run of characters that are not letters or digits one space, trimmed: "Côte d'Ivoire" is the list's
  "Cote d'Ivoire"). Other keys in an item are ignored. The plan's old free-text `context_queries` is never read.
* **The adapter builds each search** itself, from the list's own spelling of the terms and the years only:
  `[sector] indicator [region] [from] [to]` (the second year only when it differs), joined by single spaces:
  "consumer price inflation Canada 2024 2025", "video games sales growth 2022". An item whose indicator is missing,
  or with any term not on its list, or with years that are not a range from 1900 to 2099, is dropped whole, as is
  one that builds a search already built, or a fifth one. `ai_plan.context_queries` is the list built (an explicit
  `[]` means no search, also when the plan sent no `context` or no list); `ai_plan.context` the items kept, in the
  list's spelling, `years` always `[from, to]`; `ai_plan.context_queries_dropped` one reason per item dropped ("an
  indicator not on the list", "a sector not on the list", "a region not on the list", "years that are not a range
  from 1900 to 2099", "not an item of list terms", "the same search twice", "more than 4 searches", "the list of
  search terms could not be read"). A term that happens to equal a value in the file (a region column holding
  "Ontario") is still only a list term: nothing else from the file can be in a search. The block-list check
  (`_QueryGuard`) is retired: a search holds only list terms and years, so there is nothing left for it to find.
* **The page** always sends `context_queries` to /report as an array: `ai_plan.context_queries`, or `[]` when the
  run has no `ai_plan` (the visitor ran without a plan, or the plan or the profile failed); the plan's own words are
  never sent. A run with no plan has no `ai_plan`.

### 5.9 Charts chosen from the data (viz) (the chart registry, `plan/CHART-REGISTRY-DESIGN.md`, frozen 30 September 2026)

The frozen interface is `tools/fixtures/viz/spec.json` (version `2026-09-30.1`): the caps, the menu, the record's
JSON Schema (draft 2020-12) and each kind's invariants, the colours, the plan's rules and 14 example records;
`tools/fixtures/viz/validate_spec.py` checks it (and breaks copies of the examples to prove it catches each break).
This section summarises it; where they differ, the spec wins and this section is fixed.

**The rule.** The engine computes every value and writes every printed string of a chart (cell texts, step texts,
legend lines, labels, table cells, the summary) and every colour tier. Renderers never format a data value; only
axis ticks are formatted locally. The AI only selects charts; the engine validates each choice against the data and
builds it, or refuses it with a reason. A chart reconciles with the engine's own overlapping figures (the scenarios
items, the trend chart's monthly series, the compare analysis, the corr chart) to 1e-6, or it is refused. Each chart
renders the same on the page, in the PDF and in the share viewer, from its record alone.

**Caps.** `VIZ_MAX` 8 charts a report (and items kept from the plan), `VIZ_AUTO` 4 (the engine's own picks when no
AI chart is built), `HEAT_MAX` 3 heatmaps, `SMALL_CELL` 5 rows, `AI_CHARTS_MAX` 10 charts in `results_for_ai` and
/report (the analyses' charts and the viz records together), `CHART_BYTES` 6,000: one record as `JSON.stringify`
writes it, in UTF-8 bytes (Python writes a whole-number float as an integer and measures
`json.dumps(record, separators=(',', ':'), ensure_ascii=False)` in UTF-8: the same number). About 120 heatmap cells
fit (a 10-year calendar is 5,996 bytes); the 12 × 24 grid cap is a display cap, and a record over the byte cap is
trimmed by the engine (a calendar's oldest year, a change heatmap's oldest month, a crosstab's last row into
"other", a Pareto's last bar into "other"; the subtitle says what is shown), or refused ("too large to draw").

**The menu (Batch 1)** and the plan's columns, in order: `contribution_waterfall` [category, total?] and
`pvm_waterfall` [total, units, category?] (kind `waterfall`, section `drove`, from the scenarios items of 5.8);
`calendar_heatmap` [measure?] (month × year: totals for a flow, month-on-month % change for a level, rate or index;
section `headline` when its measure is the primary claim's, else `other`); `change_heatmap` [category, measure?]
(segment × month, year-on-year % change, diverging; `drove`); `crosstab_heatmap` [category, category, measure?]
(`other`); `theme_rating_heatmap` [text, rating] (the themes analysis's words × the rating levels; `other`);
`correlation_heatmap` [measure, measure, measure, ...] (3 to 12, or [] for the engine's measures; `other`);
`group_ranges` [measure, category] (the compare analysis's bootstrap ranges as a dot plot, kind `dot_range`;
`other`); `pareto` [category, total?] (the top 20, the running share, k80; `other`); `slope` [category, total?]
(each segment's prior and latest total; `scenarios`). Sections name the AI report's parts: `headline` the headline,
`drove` what drove it, `other` other findings, `scenarios` scenarios. Each entry's data conditions, privacy rule,
source and reconciliation are in the spec's `menu`.

**The record** (`rep.viz.charts[]`, and whole in `results_for_ai.charts[]`): `{id ("viz.<n>.<chart>"), chart (the
menu name), kind (the draw kind, at most 10 characters: waterfall | heatmap | dot_range | pareto | slope | table),
title, subtitle, section, supports (the claim or analysis it illustrates), anchors[] ("finding:<id>",
"scenario:<item id>", "analysis:<n>", "chart:<id>"), grade, parent_grade (a chart derived from a graded claim has
grade null and the claim's grade here, as 5.8's items do), chosen_by ("ai" | "engine"; an engine pick's why starts
"Chosen by the engine: "), why, measure{label, unit, kind}, data (by kind), table{cols, rows} (the table view:
every figure the chart shows), summary, suppressed{cells, why}, source, inputs{columns, rows, months, op},
degraded? ({from, why}, set only by a producer or reader that degraded the record to a table)}`. `data` by kind:
- `waterfall`: `steps[{label, value, text, kind: total|step, from, to}]` (the two totals first and last, from 0;
  each step continues the running total), `basis{split: segment|price_volume_mix, finding_id, column, prior, latest}`,
  `change{value, text}`; the steps add up to the change;
- `heatmap`: `rows[]` (≤ 12), `cols[]` (≤ 24), `values[][]` (number or null), `text[][]` (≤ 12 characters),
  `tier[][]` (0, or ±1..3), `n[][]`, `scale` (sequential | diverging), `legend[{tier, text}]`, `row_label`,
  `col_label`. A shown cell has n ≥ 5; a suppressed one reads "<5" (value and n null, tier 0); an empty one reads ""
  (n 0, tier 0). Tiers are tertiles of the shown values (of |value| on a diverging scale, the sign its value's);
  correlation uses |r| cut points 0.3 and 0.6;
- `dot_range`: `rows[{label, n, center, lo, hi, median, texts{n, center, lo, hi, median}}]`;
- `pareto`: `bars[{label, value, text, cum_pct, cum_text}]` (≤ 20, levels with 5 or more rows), `other{…,
  n_entities}` or null, `total{value, text}`, `k80{k, of, text}` (k counts levels in order of their totals, every
  level included, to 80% of the whole, so it may lie beyond the bars);
- `slope`: `rows[{label, a, b, a_text, b_text, change_text}]`, `a_label`, `b_label`;
- `table`: `{}`: the record is its table.

**Unknown kind → table, never dropped.** A reader that does not know a record's kind, or finds its data malformed,
draws its table; the worker degrades such a record to kind `table` at the same index (a chart is never removed, so
`[CHART:n]` keeps its meaning). The analyses' own charts (`line`, `bars`, `scatter`) keep their shape and readers.

**Privacy.** No flagged column (withheld, coded, or kept by the visitor) in any role or label, and no level the
scrubber would change or over 80 characters; no printed figure rests on fewer than 5 rows: a heatmap cell is
suppressed, a bar, row or step is folded into "other". A refusal never names a withheld column. The worker suppresses
again any heatmap cell with a value and n under 5.

*People's names* (the chart review of 30 September 2026: review titles that named staff put four staff names among
the theme words, and with no plan the engine charted a Pareto of those titles):
- The adapter reads the exact tokens of every flagged column's values (withheld, coded or kept; a column flagged as
  free text left out, its words being the file's vocabulary) before any is coded, and keeps them in the adapter
  (`Scrubber.flag_tokens`). A level holding one (a token of 2 or more characters with a letter; any length for a theme
  word) is never printed: the chart is refused "a level too long to print or that reads as personal data".
- A theme word (the themes analysis and the theme heatmap alike) is never such a token, a given name
  (`engine/first_names.txt`: `/usr/share/dict/propernames`, public domain, lower-cased, less 36 ordinary words, packed
  in the zip), or a word the texts capitalise in 60% or more of its occurrences that are not a sentence's first (the
  proper-noun rule; off when capitals are most of such occurrences, as in titles written in Title Case). A name stands
  as a break: no two-word phrase joins across it. Words are letters in any script after NFC ("hôtel", "café").
- A column whose values look like people's names (the adapter's person-name test, `nl_browser._looks_like_names`: the
  personal-column check's rule on its heading and values, or 60% of its distinct values shaped like a name and 30%
  beginning with a given name) is never a chart's levels: a Pareto, a crosstab, a change heatmap, group ranges, a
  waterfall or a slope of it is refused "the values of <column> look like people's names".
- The engine never picks the theme heatmap itself (only when the AI plan asks for it by name), and its own Pareto reads
  only a column the plan types category, entity or geography.

*Recoverable cells* (best effort; no secondary suppression). A suppressed cell must not be worked back from exact
totals beside it. A record as it leaves the adapter (`results_for_ai`, `nl_viz.for_sending`) or the page (the share
body, `src/js/50-try.js shareSafe`) carries `inputs.rows` null when it suppresses a cell (the rows read, less the shown
cells' rows, gave a suppressed cell's). The theme heatmap's `all` column, when a rating column is suppressed or 1 to 4
texts have no rating, is in whole percents with its n null (its share times its n, less the shown cells, gave how many
of the hidden texts use each word); that shown cell with n null is the one exception to "a shown cell has n of 5 or
more" (spec invariants.heatmap). A Pareto's and a slope's "other" rests on 5 or more rows (the slope's in each window
it has rows in): the smallest bar, or the level in both windows with the smallest contribution, joins it until it does.
Figures that combine across charts (a crosstab's cells and a Pareto of the same column, say) are not checked against
each other.

**The plan and the profile.** `plan.charts: [{kind (a menu name), columns, why}]`, at most 8: the worker keeps items
whose kind is on the menu and whose columns fit its args (at most 12, each cut to 120), drops one that names a
column the profile marks personal and a repeat, cuts `why` to 200 at a word (`plan_directive.worker_rules`); the
engine checks each again (`validate_directive`) and lists a refusal in `rep.viz.refused[{chart, columns, why,
chosen_by}]`. A refusal never triggers a re-plan. `profile_json` adds `chart_limits: [{chart, ok, why}]` beside
`analysis_limits` (at most 16, why at most 160 code points: the chart's needs, "; ", what the file has, never naming
a withheld or coded column); the planner never plans a chart whose limit says ok false.

**Hooks.** `engine/nl_viz.py` builds after `rep["scenarios"]`: `rep["viz"] = {version, charts, refused,
chosen_by}` (`blank_report` carries it empty) and each record is appended to `rep["charts"]` as `{id, rule: "V",
type: "viz", title, view: "manager", default_visible: true, finding_ids, why_shown, source, data: the record}`,
drawn in the page's viz area (not counted in section 3's six manager records). `results_for_ai.charts` holds the
analyses' charts first, then the viz records; under the byte budget, viz records go from the end after the scenario
items and before any analysis. The worker sends the model one card per viz chart (`{n, kind, title, section,
supports, grade, parent_grade, summary, table}`, never its data; the guard indexes only the summary and the
table), moves a misplaced `[CHART:n]` into its section (G10), and /report returns the validated charts and tables:
the page and the share viewer draw those, never their own copy.

**Colours** (the build's tokens, `build.py`; the PDF uses light only): sequential tiers #cfe9e4 #add9d2 #87c7bc
(dark #143c3f #154b4a #155b55); falls #d9e7f8 #b7d1f1 #94bcea (dark #19304a #1f4168 #265286); rises #f1e2d1
#e5c8a8 #d9ae80 (dark #332d26 #503b24 #6d4822); cell text #16232e (dark #e9eef3); suppressed fill #eef0f1, text
#56646f, dashed #b9b5aa (dark #202b36, #9aabb8, #3b4d60); empty: no fill, dashed #e2dfd6 (dark #26364a); waterfall
totals #24425c, rises #0b7366, falls #945400 (dark #b9cce0, #2bb5a3, #e3a33b). Colour is never the only channel: a
number in every cell with a ○◐● glyph for its tier (drawn as vectors in the PDF), a + or − on every change, a table
view for every chart, and its accessible name is its title and summary. Every chart fits a 320-px phone (288 px of
drawing, no sideways page scroll; the narrow layouts are in the spec's `phone_fit`).

**What the engine does where the spec leaves a choice (wave 1A, `engine/nl_viz.py`, 30 September 2026).**
- *Byte caps* (the owner's decision on the spec's byte-cap conflict): `CHART_BYTES` 6,000 a record, except a heatmap,
  `HEATMAP_BYTES` 12,000 (a 10-year calendar is about 6,000 bytes; the worker's `sanitizeChart` uses the same two
  numbers). A grid over its cap is trimmed as the spec says and its subtitle ends "<first> to <last> shown: the chart's
  size limit". spec.json records the decision as `caps.HEATMAP_BYTES` (12,000, same version), and validate_spec.py's
  `check_record` applies `HEATMAP_BYTES` to a heatmap and `CHART_BYTES` to every other kind.
- *The rows*: every chart reads the rows of `downloads.clean_csv` (the frame the engine's own charts read), without
  every flagged column; a kept one too, with one exception (option B, the owner's decision of 1 October 2026: the AI
  may read the personal columns the visitor keeps). The theme × rating heatmap's text may be a column the scan flagged
  as free text that the visitor kept, when an AI plan ran (the page runs one only after the visitor ticks the box that
  names the column): `nl_viz.Ctx.kept_text`, never a column whose values look like people's names, and never in any
  other part or chart (the rating, and every other chart's columns, are refused "a personal column is never charted"
  when flagged). Its words keep every name protection: the tokens of every other flagged column's values
  (`Scrubber.flag_tokens_by`, as the themes analysis of the same column reads them: `Ctx.names_but`), the given names
  (`engine/first_names.txt`) and the words the texts write as a name mid-sentence. A withheld or coded text is never
  read. The chart limits count such a kept text for this chart only. Otherwise it reads an
  unflagged column the plan types `free_text` (or, with no plan entry, one of more than 300 values or a median over 60
  characters). Its rows are the themes analysis's words and phrases (`_a_themes` on the same texts, and equal to the
  plan's own themes analysis of that column when it ran); after the rating columns it has an `all` column, the themes
  analysis's own share and text, and every word's texts across the ratings add up to its count there (a text with no
  rating refuses the chart).
- *Small cells in a waterfall and a slope*: a level in both windows with fewer than 5 of the claim's rows in either, one
  that entered with fewer than 5 in the latest window and one that left with fewer than 5 before are folded into
  "other" (recounted on the claim's own rows, which must add up to `scenarios.basis.rows`); when "other" then rests on
  fewer than 5 rows in all, the level in both windows with the smallest contribution is folded in with it, and past 12
  parts the smallest go the same way; `suppressed` counts the levels under 5 rows and says which were folded for
  "other" or for the cap. A level that entered or left is labelled "<level> (new)" or "<level> (left)". The steps come
  in the invariant's order: levels in both windows by the size of their contribution, then "other", then the levels
  that entered or left; the slope's rows by their latest total, "other" last.
- *Reconciliation*: a calendar or change heatmap is checked against the claim's trend chart (`trend.total:<m>`,
  `trend.<m>` or `trend.volume`) where the months overlap, else its season chart; with neither, or no month in
  common, it is refused. A level's zeros that mark "no value" (`nl_browser._zero_shape`, the analyses' evidence) are not
  averaged, and the subtitle says how many. A correlation heatmap names only measures of the engine's corr chart (#9) and
  copies its r; group ranges are the compare analysis run on the same rows (`_a_compare`), and equal the plan's own
  compare analysis of the same measure and groups to its printed digits when it ran; a Pareto's ranking equals the
  engine's `ranked.<column>` chart when that chart counts the same rows.
- *The limits in a run*: `validate_directive` re-computes `chart_limits` over the rows the engine read (the profile's
  rule, with the plan's types: a column typed `free_text` is text, one typed `rating` a rating; a column of short values
  the plan types category, entity or geography counts as a Pareto's category up to 5,000 levels, the top 20 and
  "other", where the profile lists 300; in the planner's profile, before any plan, such a column makes the Pareto's limit
  ok "when the plan types it one"). A limit that says ok false refuses the chart with its why; a refusal says the true
  reason (a level under 5 rows among the largest, a ranking that differs from the engine's ranked chart), never "could
  not be reconciled" for those. `plan.charts` passes through `_validate_plan` cut to its caps (12 items read,
  `kind` 40 characters, at most 12 columns of 120, `why` 200 at a word); nothing about a chart is added to
  `ai_plan.refused` or `plan_signals`.
- *results_for_ai*: the viz records go whole after the analyses' charts (the worker makes the model's card from each,
  `nl_viz.cards_for_ai` makes the same card here), as `nl_viz.for_sending` makes them: a word that names a withheld column
  reads "[withheld column]" (a record was left out whole until the chart review of 30 September 2026; the theme heatmap
  leaves out a word of a withheld column's name, its row only), `inputs.rows` is null beside a suppressed cell, and a
  record a rewrite takes over a cap is not sent.
- *Texts the engine writes*: a theme heatmap counts a text with no rating in `all` only and says how many (it was
  refused); a calendar trimmed to its latest 12 years says so, and "the chart's size limit" only when the byte cap took
  more; the group ranges' summary names the ranking (each average weighted by its rows) and prints each group's own
  average; a contribution waterfall's summary says when "other" is the largest part; a diverging legend runs each side
  to its own extreme (the falls to the largest fall, the rises to the largest rise); a heatmap figure too wide for 12
  characters is written compact in every cell and the legend ("1.235B", "456.7M").
- *Themes' ties*: words used by as many texts are in the word's order (they were in the order a set gave them, which
  changed with the string-hash seed).
  Under the byte budget they go from the end after the scenario items and before any analysis; the scenario items'
  estimate leaves out the "Where the change came from" table's rows, so once it fits, the items dropped last come back
  while the payload still fits (it drops no more than it needs).

**§5.8, a table read by its structure: `basis.source: "structure"` (WAVE 4, track A1, 1 October 2026;
`nl_scenarios.build_structure`).** The block breaks down the slice's own series by the structure, never by raw rows.
`basis`: `{source: "structure", finding_id, claim, grade, grade_words, measure (the slice's column), how ("total" for a flow
or a count, else "average"), unit (the table's unit of measure), windows, slice{dim: member}, slice_id, breakdowns[{id, dim,
key, parent, parts, depth, shares_given, unallocated{prior, latest, contribution}}], estimand (its text), reconciles}`.
Items: `headline.{prior, latest, change, change_pct}` (the estimand's figures and texts, the claim's grade); per
breakdown (at most 4: the plan's `breakdowns`, else every one) and part, in group `contribution`, segment = the member:
`contribution.<dim>.<member>` (its contribution to the change, kind change), `growth.<dim>.<member>` (its own change in
percent, kind change, unit "%", only when its 24 months are all published), `share_level.<dim>.<member>` (its share of the
latest window, kind percent), `share_change.<dim>.<member>` (its share of the change, only when every part moved the way
the total did: else `refused` says so, MIXED_SHARES); and `contribution.<dim>.unallocated`, the total less its published
parts as a change, its `assumes` naming both windows' unallocated amounts. Its label and `assumes` name the cause by the
chart's own rule (wave 5, 7 October 2026: `sum_checks[].suppressed_parts`, the number of parts that lack a month's value in
either window): "(suppressed cells)" when some part does, "(rounding)" when none does (a table in thousands adds up to within
a few units), and the amount is the real residual written out in whole units ("+$1,000", "−$6,000": never "$0.0B"; exactly
nothing is "$0"). A breakdown of a dimension with NO total row has no `unallocated` item (`basis.breakdowns[].no_total` true:
the headline is the sum of those parts, so nothing is left over). `<dim>` is
`nl_scenarios.dim_key` (an acronym in brackets, "naics", else the slug, "geo"); `<member>` is `member_keys` (its code,
"455", "44_45", "459a", else its slug, "ontario"). The forecast items as before; `facts.{months, first, last}` of the
headline series. Texts are the estimand's (`nl_structure.money`: "$834.7B", "+$10.2B", "−$144.7M", a figure that rounds
to zero unsigned, "$0.0B"; `nl_structure.pct`: "+3.2%"), not `_fmt_item`. Grades: headline items carry the claim's grade;
every derived item has grade null, the claim's grade as `parent_grade`, and for an official table `grade_words`
"descriptive arithmetic on published totals; part of a change graded WATCH, not graded itself". Reconciliation: the
slice's monthly values equal the engine's charted series to 1e-6 (`estimand.reconciles`), or the whole block is refused
(NOT_RECONCILED); each breakdown's parts and unallocated add up (math.fsum) to the change to 1e-6, or that breakdown is left
out with a reason. In a quarterly or an annual table every period word of the block is the period's own (`basis.period`
{kind, noun, nouns, step, window}, only when it is not monthly): "the latest 4 quarters against the 4 before", "the 3 matched
quarters before", "the latest year", `facts.months`' label "Quarters with a value in the headline series", its first and last
texts "Q1 2012", "Q4 2023"; the ids stay. A rate or an index has no contributions: "a rate is never added or averaged across members: each
member's own rate is published, and the headline is the published aggregate, so there are no contributions". The writer's
table (`nl_scenarios.table`) for a structure basis is the first breakdown: `[<dim>, "Contribution to the change", "Share
of the change", "Own change", "Share of the latest year"]` less any column no row fills. In the payload byte budget (`_budget_drop_order`) a breakdown's `contribution.<dim>.unallocated` item is the last of the segment items
dropped, however small, because it is what makes the parts add up to the change. Retail: 75 items (4 headline, 13
GEO and 9 NAICS parts with contribution, growth and share_level, 2 unallocated, 3 facts; no share of the change: the
Northwest Territories and furniture moved against the total).

**§5.9, a table read by its structure (WAVE 4).** *Segments by size:* `Ctx.levels(land, measure)` orders a category's
levels by the size of the chart's measure: the sum of |measure| over the latest 12 months for a flow or a count, rows ×
|mean| for a level; then rows; then name (it was rows, then name); `_top_levels` passes the chart's measure for the change
heatmap and the crosstab, so the 11 largest levels are shown and the rest folded into "other". The group ranges keep the
compare analysis's selection (the 12 groups with the most rows) because they must equal its table. *Structure charts*
(`nl_viz._build_structure`, in the slice's run, up to 8 charts and 3 heatmaps): per breakdown a `contribution_waterfall`
and a `change_heatmap`, then one `calendar_heatmap`.
- `contribution_waterfall`: the DECOMPOSITION OF THE CHANGE (final integration pass, 6 October 2026; before it the totals were
  the prior and the latest LEVELS, $834.7B and $864.0B, so a $10B part was 1% of the height under the spec's "the value axis
  always includes 0"). From the structure's scenario items: a first total `Start` (value 0, from 0, to 0, text "$0"), the 10
  largest parts by |contribution|, the rest folded into "other parts (n)", a not-allocated step when it is drawn (below), and
  a last total `Total change` (value = `change.value`, from 0). Every frozen-spec invariant holds as written: the totals are
  first and last, a step starts where the one before ended, the steps add up (fsum) to `change.value`, and the last total
  equals the first plus the change; the value axis includes 0 and the parts fill it. The prior and latest levels are in the
  record's `subtitle` and `summary` ("went from $834.7B (2024-08 to 2025-07) to $864.0B (2025-08 to 2026-07)") and in the
  estimand panel, not in the chart. The not-allocated step (the total less its published parts) is DRAWN only when it is 1% or
  more of the change (the share the worker names it from, G20) or when leaving it out would break the spec's add-up (the
  steps must add up to the change to 1e-6 of it); then it is "Not allocated: suppressed cells" when a part of the dimension
  lacks a month's value in either window (`estimand.sum_checks[].suppressed_parts` > 0, or no sum-check was found), else
  "Not allocated: rounding" (a report made before 6 October 2026 says "unallocated (suppressed cells)"; the page and the PDF read
  all three). Below that it is left out and the record's `summary` and `source` carry the gap: "The parts add up to the change
  (gap $1,000, rounding)", "(each total checked against its parts: adds up; gap $1,000, rounding)"; `basis.split`
  "segment" with `basis.column` the dimension; the table `[Step, Contribution, Own change]` carries each part's growth;
  `source` begins "structure:"; `inputs.rows` null (each part rests on its 12 published months in each window). A step's
  label is the member's, cut at 80 characters with its trailing code kept ("... leather goods retailers [458]").
- `change_heatmap` (section "drove"): each part of the breakdown, month by month, against the same month a year before in
  percent, on the headline slice's own dimensions (the unadjusted series, so the comparison is like for like); the 11
  largest parts by their latest 12 months and one "other (n parts)" row (the folded parts' sum in a month where every one
  has a value; its subtitle names them); the latest 24 such months; the diverging scale and tiers of §5.9's heatmaps.
  **A cell's `n` is the number of sum-checked published parts its figure adds up from** (`nl_structure.support`: a
  province's retail total adds up from its 9 industries; Canada's adjusted total from its 13 provinces; the part count
  with a value in the month, the fewer of the two months for a change), because a published figure rests on one table
  row and the spec's small-cell rule (5 rows) is about records. A cell on fewer than 5 parts reads "<5"; fewer than half
  its cells shown refuses the chart ("fewer than half its cells rest on 5 or more published parts ..."); a table of one
  dimension with no part below its parts has none. `source` begins "structure:"; `inputs.rows` null.
- `calendar_heatmap` (section "other": its measure is the adjusted copy of the headline's, not the claim's): the
  month-on-month change in percent of the seasonally adjusted slice (S2; `measure.kind` "change_pct", the diverging
  scale), the latest 12 years; cell `n` as above. Refused with "the table publishes no seasonally adjusted series: the
  month-on-month change of an unadjusted series shows its season, not momentum" when there is no adjustment dimension, and
  for a rate or an index (it changes in points).
The records keep the frozen spec's shape (spec.json: a step and `basis` take no other key, a shown cell needs n at least
5), so the design's `basis.source` and per-step `growth_pct` live in `source` and the table until the spec (track C) adds
them. A plan's `contribution_waterfall` or `change_heatmap` of a structure dimension is that chart (chosen_by "ai"), a
plan's `calendar_heatmap` the calendar; a plan's `crosstab_heatmap` is refused ("a crosstab of a table of series would add
rows that mix totals and parts; the structure's breakdowns show where the change sits") and any other chart of a
structure dimension is too ("rows of a table of series mix totals and parts: the structure's breakdowns show GEO
instead"). A table refused for its structure (`structure.kind` "cube_incomplete"), or one whose slice could not run, gets
no chart over its raw rows: `viz.refused` holds `{chart: "all", why: "rows of a table of series mix totals and parts"}`.
Retail: 2 waterfalls (GEO, NAICS depth 1), 2 heatmaps (GEO with 11 provinces and "other (2 parts)", NAICS with the 9
industries), the calendar (n 13); the GEO heatmap shows 24 "<5" cells (a territory's industries suppressed in a month).

### 5.10 Structure (WAVE 4, track A1: `engine/nl_structure.py`, `plan/WAVE4-A-DESIGN.md` sections 1, 2 and 4)

An official table (StatCan 20-10-0056-01: 465 series × 79 months, one row each) holds totals beside their parts, an
adjusted copy beside the unadjusted one and components beside their parents; adding its rows counts the same dollar three
times. `nl_structure.detect(reading, hidden)` reads what the table is from the engine's own reading after the visitor's
decisions (a withheld or coded column is never read: `hidden`), by behaviour (names are hints), within a 1-second timer
(`BUDGET_S`; 0.14 s native on the retail file, 0.04 s on a synthetic 40,320-row cube).

*Roles of columns.* The date: the column with the most dates the engine reads. The measure: the number that moves, VALUE/
OBS_VALUE a hint; a cell the engine could not read is parsed for an embedded publisher flag ("123.4 p", ":", "[x]").
Metadata by behaviour: `constant` (UOM, SCALAR_FACTOR, DECIMALS), `empty` (SYMBOL, TERMINATED), `series_id` (1:1 with the
dimensions' key, or a metadata-named column: VECTOR), `alias` (1:1 with one dimension: DGUID with GEO), `flag` (at most 20
short codes, with blanks, or a code whose rows have a blank measure: STATUS), `unit`, `other`. A series id or an alias is
never a dimension: a withheld NAICS is never rebuilt from VECTOR. Dimensions: the text columns left with 2 to 400 members
(at most 8). The date and the dimensions must tell the rows apart (at most 1% repeat), else `kind: "cube_incomplete"` for an
official table (a publisher's signature from `engine/flag_vocab.json`, or 3 or more metadata-named columns) and
`"not_cube"` for a business file; a business file with a second measure column is `"not_cube"` (read as before).

*Relations, per dimension, on the unadjusted cells* (another dimension's adjusted copy left out). A sum-check of a total T
against parts P: on the cells where T has a value and every part has a row, the residual r = T − Σ(parts with a value);
tolerance `max(0.5 × 10^-DECIMALS × factor × (|P| + 1), 1e-6 × |T|)` (half a unit of the last published digit, in base
units, per term; DECIMALS from the metadata, else from the values); it passes on 95% or more of the complete cells, 6 or
more of them over 3 or more months, and, for a non-negative flow, no incomplete cell's r below −tolerance (an incomplete
cell's r is the UNALLOCATED, suppressed share). Search: (1) flat: the 3 most dominant members (the share of cells where a
member is at least every other) and any named as a total (total, all, overall, grand, aggregate, combined; a range code
[44-45]; _T, TOTAL, _Z) against every other member but the alternatives named "excluding/except/ex./less/without/other
than" outside brackets; (2) a hierarchy from codes (a bracket suffix, a leading code, dotted codes: a member's parent is
the most specific code that contains it, a prefix or a range), each family sum-checked and a failing family repaired by
dropping its deepest-coded members (459 = 459A + 459B); (3) a hierarchy without codes: for each parent, the most dominant
first (at most 30), the members it bounds in 99% of its cells (at most 22, the largest), a meet-in-the-middle subset sum on
a 3-cell fingerprint (2^11 subsets a half), each match verified on every cell, the coarsest verified partition (0.5 s at
most); (4) a member left out of the tree: an alternative total (named so, or the total less one or two tree members), else
a component of the smallest verified member that bounds it everywhere, a coded one within its code-ancestor's subtree,
the nearest before it in the publisher's own order (Cannabis retailers [459993] under Miscellaneous retailers [459B]); (5)
no partition but one member bounds the others in an official table or under a total's name: `components` (`measure` when
the dimension or its members name measures: Sales); (6) a rate or an index (Percent, rate, per N; index, NNNN=100) is
never summed or averaged across members (AM4): its published aggregate is the member inside the others' range in 99% of
the cells that is named as a total or comes first (`rate_aggregate`); (7) nothing verified: an official table reads one
member (`single`: the total-named one, else the most covered and dominant), a business export adds its members up
(`flat_additive`, read as before). An adjusted pair is found first, by behaviour: two members (of a dimension of 2 to 4)
whose calendar-year totals agree within 3% and one three times as seasonal (the variance of its month means, detrended
by a centred 2×12 average, of the logs) is the unadjusted copy (`adjustment`, `nsa`, `sa`); whether the adjusted parts
of each partition add up to the adjusted total is recorded (`sa_adds_up`: retail's do). A dimension whose unit of measure
changes with its members (dollars beside units, an index on two bases) is a `measure` dimension with `unit_of`: each slice
fixes one member, never mixed. Roles: partition | hierarchy | adjustment | measure | components | rate_aggregate | single |
flat_additive | constant | unresolved | parts (wave 5: no total row, section 5.12).

*Measure type and scale.* From the unit: a currency is a flow (a stock under inventories, outstanding, balance, holdings,
assets, debt, stock), persons or a number a count (a stock under employment, population, labour force), percent, rate or
per N a rate, index or NNNN=100 an index; with no unit, the engine's own additive kind of the measure's name. Months: a
flow or a count is added up, anything else averaged. SCALAR_FACTOR ("thousands"), SCALAR_ID (3) or UNIT_MULT (6) is applied
row by row, so every figure is in base units ("$864.0B").

*The windows.* The core's own: the latest 12 months ending at the last month with a value, and the 12 before. A flow's or a
count's window figure adds its months up, so a month the headline lacks in either window would make the change compare 11
months with 12 (a Total published but for one suppressed month read as down 5%); the comparison then uses the months with a
value in both windows, the same calendar month in each (`nl_structure.matched_months`): `estimand.complete` false,
`months_used` k, `months_left_out` the latest window's months with no counterpart, the text "totals of the 11 months with a
value in both windows (Jun 2022 left out)", each scenario label and the waterfall's totals "the 11 matched months before".
Under 6 such months no figure is printed (`figures.*.value` null) and the breakdowns are not formed. A level's window figure
is the mean of the months it has, so its windows stay whole.

*Usable, and the slices.* A table with a relation (partition, hierarchy, adjustment, components, rate_aggregate) is read
by its structure (`usable`); a panel with no relation and at most 60 series is `"panel_no_relations"` and read side by
side by the long-table layout as before (the FX files); past 60 series it is read one member at a time. S1, the headline:
the root of each partition or hierarchy, the unadjusted copy, a measure dimension's total-named (else currency, else
first) member, the components' parent, the rate's aggregate, rule 7's single member; a flat-additive dimension "*" (added
up). S2 momentum: S1 with the adjusted copy. Then components, alternatives and other measures (S3...; at most 12).
Breakdowns (B1...): each partition or hierarchy whose S1 member is its root, its root's children (a hierarchy at depth 1),
the other dimensions at S1; none for a rate or an index.

*The run* (`nl_browser.run`). The fast path: before the AI plan is applied, the structure the scan's run or the planner's
profile pass found under these same choices (`_PROFILE_CACHE`; a plan's run that finds none runs the profile pass once);
a plan is validated against it (slice ids), its rows are checked (`check_rows`) and the slice runs. The hook, between the
audit (`run_loop`) and the business analysis (`run_analyze`): the reading built early from the audit's cleaning (reused
afterwards), the structure detected and cached with the profile's facts. A usable structure runs the whole engine on the
slice (`_run_slice`: `nl_structure.slice_bytes`, the date and one measure column in base units, one row a month; a flow's
column is named so the engine adds it up: "Total retail sales", "<label> total", a count "<label> count", a level "value")
and returns that report (its run's layout states the table's rows a month, 465 for the retail file, which track A2's row-count drop reads as `layout.rows_a_month`; after the structure and the estimand are copied on, `nl_browser._official_inference(rep, header, layout, hidden)` of track A2 is called, with the file's own header and withheld columns: it is part of this engine, the call is not guarded) with the file's own `input` (name, bytes, rows, columns, sha256) and `input.layout = {layout:
"structured cube slice", slice, where, column, rows_in, rows_out, series}`, its `privacy` (flagged and released), its
health in `structure.file_health` (`{score, issues, dropped, rows_in, rows_clean, rows_quarantined, note}`: the engine's
issues on the whole file less those on metadata and flag columns, the core's score unchanged), the plan as applied to the
file (`ai_plan`, with `structure_slice{id, where, plan_source, column}`), and the timings of both runs added up. A
limitation leads the list ("This file is a statistical table: 36,735 rows, 465 series over 79 months ... the report reads
one series ...") and a cleaning step `structure_slice` says so. Retail, planner off: 15 to 22 s native (it was 29 s; the 12 s
audit of the whole file stays, the 13 s business analysis of 36,735 rows becomes 0.4 s on 79), 0.14 s of it the structure.

*A plan's rows* (`check_rows(S, positions)`): the series the plan's kept rows map to, per dimension: `total_with_parts`
(a member kept with an ancestor), `two_adjustments`, `component_with_parent`, `alt_with_total`, `mixed_units`,
`rate_members` (several members of a rate or an index), `unverified_members` (several of a `single` dimension). A
violation replaces the plan's rows with the default slice: `estimand.plan_source` "ai_corrected", `structure.corrections`
the violations, a line in `ai_plan.refused`, and `plan_signals` empty (a correction is never sent back to the planner). A
plan's slice id is the AI's choice ("ai"); rows that form a slice are too; a plan that sets no rows aside reads the
default ("engine_default"). The plan's analyses that read only the measure and the date run on the slice; one that
groups or filters by a structure dimension is refused ("compare: it reads GEO, a dimension of a table of series whose
rows mix totals and parts; the structure's breakdowns answer it"); `long_to_wide` is not needed.

*The refusal.* A table whose readable columns cannot tell its rows apart (`cube_incomplete`: a dimension withheld or set
aside) gets no business analysis: the story's headline is "The business analysis did not run: the date and the columns
the engine may read (GEO, Sales, Adjustments) do not tell the rows apart: 34,365 of 36,735 rows repeat a date and a
series, so a column that names the series is withheld or set aside." and no chart reads its rows.

*Flags* (`structure.flags`): `{column, publisher, by_kind{kind: rows}, codes{code: {kind, rows, blank_measure, missing}},
unrecognised[], blank_without_flag, quality_of_headline{code: months}}`; a code's kind is the publisher's
(`engine/flag_vocab.json`, versioned), and which codes stand for a missing value is learned from the file (90% of its rows
blank). Retail: `by_kind {suppressed: 5430, not_available: 533, too_unreliable: 162}`, `quality_of_headline {A: 45, "": 34}`.
`structure.hash` is a deterministic hash of what was detected.

### 5.11 Inference labels (wave 4, track A2: `engine/nl_inference.py`, `plan/WAVE4-A-DESIGN.md` section 3)

**T1, the trend test** (`trend_test`, read by `nl_browser._a_trend`; Newey-West's `_hac_slope` is kept only as the size
simulation's reference). Least squares gives residuals whose lag-1 autocorrelation, bias-corrected by one bootstrap
round (rho~ = 2 rho^ - mean rho^*, the bias function simulated once per design on fixed draws), held to [-0.5, 0.95], is
the momentum of a Prais-Winsten AR(1) GLS fit; its slope and t are the estimate and the statistic. The p-value is a
parametric bootstrap (999 draws, seed 20261001) UNDER NO TREND, its momentum estimated with the null imposed
(residuals from the mean, bias-corrected the same way): the design's first version drew at the trend line's own
momentum, and its size at 9 years was 8.2% to 17.8% (momentum 0 to 0.8); imposing the null brings it to 3.8% to 5.6%.
The 95% range is the slope plus or minus the 95th percentile of the draws' |t| times its standard error, so it
excludes no change exactly when p < 0.05. The random-walk screen is the share of driftless random walks on the same
years whose |t| reaches the observed one. Verdict: rising or falling only when p < 0.05, the screen's share is at most
0.05 and the simulated size of the nearest condition is at most 7.5%; no_settled_direction when p >= 0.05; else
not_graded. `engine/inference_tables.json` (written by `tools/sim_trend_size.py`; its script and the procedure's source
are hashed, so a stale table fails `--check` and `tools/test_nl_inference.py`) holds, for 2,000 no-trend AR(1) series per
condition, n in {8, 9, 10, 12, 15, 19, 30, 60, 120} x momentum in {0, 0.2, 0.4, 0.6, 0.8}: the test's false-alarm rate
(2.6% to 7.0%; every condition at most 7.5%), how often it claimed a direction (0% to 2.5%) and the Newey-West range's
rate (7.6% to 50.1%). The fixed-b critical-value cubic a design note recalled for Kiefer and Vogelsang (2005) is checked
there by simulation and NOT used: it matches the fixed-b limit for a mean (within 0.04) but not for a trend's slope
(off by up to 0.93), and a secondary source prints its last coefficient as -0.05324, which is off by 0.5 for the mean.

**T2, an interval's measured coverage.** The core's clause "built to hold the true change 95% of the time; in simulated
series with strong momentum, which the model can underestimate, it held it less often" (`gate.interval_caution`)
becomes, in `findings[].why`, `watch.reason` and the story's sentence about that interval, "a 95% interval by
construction; in the benchmark's simulated series nearest this one it held the true change 95.2% of the time (94.1% to
96.1%)" (`coverage_clause`; "It is ..." in the story), from `effect.coverage`; with no receipt, or no condition like the
claim, "labelled 95%; coverage not measured for this kind of series". The figures come from the benchmark receipt the
engine ships (the pack's cut keeps them as `results.change_coverage`).

**T3, the history windows**: section 5.8.

**P0-13, the forecast audit** (`forecast_audit`, in `_forecast_block`): the origins are the last 23 months with 45 or
more months before them (the core's minimum); at each, the shown model is refitted (`forecast.fit_predict`) on the
months before it, and the 80% range actually shown at each horizon (its width relative to its point in ratio mode, its
offsets in additive mode) is applied to the refit; seasonal naive is refitted the same way. Per horizon (1, 3, 6 and 12
months, as far as the forecast reaches): held of checked; n_eff from the hits' lag-1 autocorrelation (the errors' when
every check hit or every one missed), n (1 - r) / (1 + r) with r in [0, 0.95]; the Wilson interval of the coverage on
n_eff; MAE, rel_MAE (against seasonal naive) and MASE. A horizon fails when the Wilson upper end is under 0.80 or
rel_MAE is over 1, passes when the Wilson lower end is at least 0.60, n_eff at least 8 and rel_MAE is at most 1 (AM5:
no worse than seasonal naive; amended 6 October 2026, it said "under 1", which a seasonal-naive champion can never meet,
its rel_MAE being 1.00 by construction: StatCan retail held 21 of 23, 19 of 21, 17 of 18 and 11 of 12 at 1, 3, 6 and 12
months and was stuck at "unclear"), and is unclear otherwise; a model that IS the seasonal-naive benchmark
(`benchmark_is_model`) is judged on its coverage alone and its label ends "; the model is the seasonal-naive benchmark
itself"; the audit fails if any horizon fails, passes if all pass, and is `trusted` only then. `mode="full"`
re-runs the core's whole `run_forecast` at each origin (the test suite's check that the cheap audit agrees). The bottom
line says "Plan on about ..." only for a CONFIRMED and trusted forecast; a CONFIRMED forecast whose audit is unclear is
"a guide rather than a plan", and one whose audit fails reads "the engine graded it ..., but that grade is not trusted:
the back-test of its range failed". The guard's wording rule (no "usable" or "plan on it" when the audit fails) is
track C's.

**T4, official aggregates** (`_official_inference`, after the v2 blocks; track A1 calls it again on a structured slice's
inner report after copying its structure): `findings[].inference` (section 2.4) on the business change claims about the
published measure, never a row count, when the file's header holds a publisher's signature (3 or more of StatCan's
REF_DATE, DGUID, VECTOR, COORDINATE, STATUS, its date among them, or of Eurostat's; `engine/flag_vocab.json` takes over
when it ships), the headline is the root of every additive dimension (with track A1's structure: its slice member of
each partition or hierarchy is that dimension's total; without it: the engine read no dimension of two or more values,
or read a long table one column per series) and no column publishes sampling errors. A withheld column is counted,
never named. Track B shows "WATCH (process grade)" beside the described change; track C states such a change "in the
published totals", never "significant" or "confirmed".

*On a table read by its structure* (the merge of tracks A1 and A2, 6 October 2026) the slice's own run calls
`_official_inference` first, with the slice's two-column header and no structure yet (it returns at once: no publisher's
signature), and the slice's call after the structure and the estimand are copied on is the one that matters; it replaces
the first call's records (the function owns `findings[].inference` and `estimand.inference`). With a structure the status
column is read from `structure.flags` (the slice's health holds only the date and the measure): `quality` is
`{column, codes}` with the headline's months by code (retail: `{A: 45, "": 34}`), and `revisions` counts the headline's
months the publisher marked revised or preliminary ("the file marks 2 months of the headline revised and 1 preliminary").
`describe.prior` and `describe.latest` are the estimand's own 12-month totals (a flow or a count; else its 12-month
averages), not the finding's monthly averages: retail $834.7B to $864.0B, +3.5%. The record is copied into
`estimand.inference` (§1), which `results_for_ai` sends first.

### 5.12 Generality (WAVE 5, 7 October 2026: `engine/nl_structure.py`, `plan/WAVE4-A-DESIGN.md` "Wave 5")

Five gaps the golden runner's offline predictions named, fixed generally (no table id, no publisher's table name; the
StatCan column layout is still recognised as before). Every test cube is synthetic (`tools/fixtures/structure/make_cubes.py`).
The retail dev file is unchanged in every figure and every scenario id: the only differences are the new keys
(`estimand.measure.type_basis`) and the unallocated items' label, text and `assumes` (gap 6).

**1. No-total partitions** (`role: "parts"`). In an official table, when no member is a total of the others (the flat search,
the coded hierarchy and the subset-sum hierarchy found none, and no name says "total"), the members are the PARTS of a table
with no total row (`_parts_only`). Left out of the parts: a COMBINED member (one that equals the sum of 2 or more others:
`nl_structure._subset_partition` on a 3-cell fingerprint, each match verified on every cell, the 30 most dominant members,
0.5 s), a member identical to an earlier one, a member inside a coded parent that is also listed (459993 inside 459, kept
only when the parent bounds it everywhere), and an alternative total named "excluding". A subtotal is never read as the total:
a code-free hierarchy whose root leaves more than max(1, members / 6) members explained only by bounding is rejected (13
provinces and a Prairies group, no Canada row). A plain dimension of an official table is no longer read as "components of its
largest member" (step 5 of the search keeps that for a total's name or a dimension that names measures). A flow's headline for
that dimension is the sum of the parts, month by month: the slice's `where` holds the token `"(sum of the parts)"`; in COMPLETE
months only (every part has a value) when the table's own latest windows (anchored at the last month any part has a value) hold
at least 6 months complete in both and the last complete month is within 3 months of that anchor; else the REPORTED parts are
summed and the estimand counts what was suppressed and is marked incomplete. Any other measure (a stock, a rate, an index, an
ambiguous count) has no valid aggregate: `role: "single"` with `single_by: "dominance"` (the most covered, then the most
dominant member), the estimand's `excluded` says "one member shown, not a national figure: this table has no total row, and a
stock is never added or averaged across members" ("a total" for a dimension that is not geographic), and such a table is read
by its structure when its dimension is geographic (a table of currencies stays a panel read side by side). The estimand:
`text` "<measure> · the sum of 5 regions; built from 5 regions; this table has no total row; complete months only: the 2
months where a part is suppressed are left out; dollars (...); totals of the 10 months with a value in both windows (...)", and
`built_from` `{dim, noun ("regions" for a geographic dimension, else "members"), n, text, complete_months_only, incomplete,
suppressed_part_months (part-months with no value in the two comparison windows), months_dropped[], latest_month_in_table,
combined[], duplicates[], dims[]}`; `sum_checks[]` has `{dim, total: null, parts: 5, by: "parts", verdict: "not possible (no
total row)", built_from_parts: true, suppressed_part_months, unallocated_*: "none ..."}`; `excluded[]` names each combined member
("equals the sum of Alpha, Bravo and Charlie (each also a member): left out of the sum, never added to its own parts"). The
breakdown's parts carry contribution and growth as for any partition; there is no `unallocated` item. `check_rows` adds
`combined_with_parts`. In the report, `structure.dims[]` has `role: "parts"`, `no_total: true`, `combined`, `duplicates`,
`nested`, `complete_only`, and the structure table's "The headline reads" cell says "the sum of its 5 parts (no total row)".
T4 (official aggregate): the headline is "the sum of the table's 5 published regions (it has no total row)"; described, not
tested. Example (`results_for_ai.estimand.built_from`, a table of five regions with two suppressed cells):

```json
{
 "dim": "GEO",
 "noun": "regions",
 "n": 5,
 "text": "built from 5 regions; this table has no total row",
 "complete_months_only": true,
 "incomplete": false,
 "suppressed_part_months": 2,
 "months_dropped": [
  "2021-11",
  "2022-03"
 ],
 "combined": []
}
```

**2. Measure dimensions.** A dimension whose members carry different units, or whose labels say they are different measure types
(a rate, a count, an index, the precision of another member), in one value column, is detected FIRST (`_measure_dims`): each
member typed on its own (`_member_types`: its unit, the unit column's value for it, and the words of its label), the table's
measure becomes the default member's, and the other dimensions' sum-checks read the default member's cells only. Detection:
the unit varies with the dimension, or at least two kinds appear (or one member is a precision member) and at least half the
labels hold a type word (rate, percent, index, a stock or a flow word, standard error ...), with 2 to 40 members. A precision
member (standard error, margin of error, confidence interval, coefficient of variation: `_PRECISION`) has `type:
"precision"`: never the default, never the headline (a plan naming it is refused with a note), never summed, no slice. The
documented default (planner off): a currency flow, then a count flow, then a stock, then an ambiguous count, then a rate or an
index; a total's name breaks a tie, then file order. `dims[].measures[]` = `{id: "M1", index, name, uom, type, type_basis,
type_why, aggregation, currency, precision, default}` (ids in file order). The profile block lists them and a plan may name one
with `"measure_member": "M1"`; the other members are `other_measure` slices (S3 ...). `estimand.measure_choice` =
`{dim, chosen{id, name, uom, type, type_basis}, by: "default"|"plan", rule, alternatives[{id, name, uom, type, precision, why}]}`;
the estimand's text says "one measure shown: Employment, chosen by the engine's default order (a currency flow, then a count
flow, then a stock, then a rate or an index; never a precision member)" or "..., chosen by the plan"; each alternative is in
`excluded`. `check_rows` flags several members of a measure dimension as `mixed_units` (their units differ) or
`mixed_measures`. Example (profile block, `plan.measure_member`, `estimand.measure_choice`):

```json
{"structure.measures": {
 "dim": "Labour force characteristics",
 "members": [
  {
   "id": "M1",
   "name": "Unemployment rate",
   "uom": "Percent",
   "type": "rate",
   "default": false,
   "precision": false
  },
  {
   "id": "M2",
   "name": "Employment",
   "uom": "Persons",
   "type": "stock",
   "default": true,
   "precision": false
  },
  {
   "id": "M3",
   "name": "Standard error of the unemployment rate",
   "uom": "Percent",
   "type": "precision",
   "default": false,
   "precision": true
  }
 ]
},
 "plan": {"measure_member": "M1"},
 "estimand.measure_choice (plan M1)": {
 "dim": "Labour force characteristics",
 "chosen": {
  "id": "M1",
  "name": "Unemployment rate",
  "uom": "Percent",
  "type": "rate",
  "type_basis": "a rate: a level, never summed"
 },
 "by": "plan",
 "rule": "a currency flow, then a count flow, then a stock, then a rate or an index; never a precision member",
 "alternatives": [
  {
   "id": "M2",
   "name": "Employment",
   "uom": "Persons",
   "type": "stock",
   "precision": false,
   "why": "another measure: one measure is shown, never mixed with this one"
  },
  {
   "id": "M3",
   "name": "Standard error of the unemployment rate",
   "uom": "Percent",
   "type": "precision",
   "precision": true,
   "why": "the precision of another member: never the headline, never summed"
  }
 ]
}}
```

**3. Unknown-type counts.** A measure is summed over time only when it is POSITIVELY a flow (`nl_structure._classify`): a
currency (unless a stock word is in the labels), or a flow word in the labels (sales, receipts, revenue, permits, births, deaths,
visits, trips, arrivals, nights, starts, completions, shipments, orders, bookings, transactions, claims, admissions, exports,
imports, production, output, sold ...); a count (persons, a number, units) with a stock word (employment, population, inventory,
households, dwellings, vacancies ...) is a stock; a count with no word that says which is AMBIGUOUS and is averaged ("average
level over the window"), so a total is never misstated and the percent change is right either way; an index or a rate named in
the unit or the labels is never summed. The words come from the measure's column, the members' labels and the value of a column
that holds one value throughout. `measure.type_basis` is "positively a flow", "positively a stock", "ambiguous: averaged" (a
rate or an index: "a rate: a level, never summed"), with `type_why` the cue ("a currency (Dollars)", "the labels say permits,
which accumulates over a period"); `aggregation` "sum over months" only for a flow. The slice's column name is "<label> total"
or "<label> count" only for a flow, else "value" (a level to the core). Example (`estimand.measure`, persons with no clue in
the labels):

```json
{
 "label": "Group A",
 "uom": "Persons",
 "scale": "units",
 "scale_applied": 1.0,
 "type": "count",
 "type_basis": "ambiguous: averaged",
 "type_why": "a count (Persons) with no word in the labels that says it accumulates over time",
 "aggregation": "mean over months"
}
```

**4. Non-monthly frequency.** `S["period"]` = `{kind, noun, nouns, step, phase, per_year, window, adjective}` from the dates: one
value a period and a steady gap (80% of the gaps) of 3, 6 or 12 months is quarterly, half-yearly or annual, else monthly (daily,
weekly and irregular tables are read by month, as before). Period labels the engine does not read as dates (2012-Q1, 2012Q1, Q1
2012, 2012-H2, and a bare year under a date-like header such as TIME_PERIOD, year or period) are read when the table has no
date column (`_period_labels`); the slice's file then carries the period's first day. A period is keyed by the month it starts in
(a quarter's phase is kept), so every window is a 12-month span of month keys that holds exactly 4 quarters or 1 year:
`estimand.comparison` is the first and last PERIOD of each window (["2023-01", "2023-10"] for Q1 to Q4 2023), `periods_used` the
periods compared (`months_used` keeps its old key), a flow needs half a window matched (6 months, 2 quarters, 1 year), a level
needs the whole window. The estimand prints the change with 4 (quarterly) or 1 (annual) values a window: "persons; 4-quarter
averages Q1 2023–Q4 2023 vs Q1 2022–Q4 2022", "annual totals 2023 vs 2022", "totals of the 3 quarters with a value in both
windows (Q2 2023 left out) ..."; `estimand.period` `{kind, noun, nouns, step, window, adjective}`; `measure.aggregation` "mean
over quarters". The core's own grade is untouched (4 values cannot be tested: NOT_ENOUGH_DATA) and is not faked; the headline is
"Population, Total, 4 quarters to Q4 2023: +3.0% (34.1K) in the published totals". Stocks and levels keep the window average at
every frequency (the point-in-time change, latest against a year earlier, is not used). The forecast block is
`{available: false, frequency: "quarter", reason}` with no audit (section 2.5), no forecast finding is made, the story's
"what's next" is the reason, and the month-by-year heatmap, trend and forecast charts are not drawn (`charts_suppressed`, rule
"period"); the structure charts' heatmaps and calendar are refused ("the chart reads monthly values; this table is quarterly
(one value a quarter), so it is not drawn"), the waterfall stays. The engine's own sentences count months ("the average month",
"the latest 12 months against the 12 before", "48 months of history"); about such a table `nl_browser._period_text` says quarters
or years (the numbers stand: only the unit's name and the window's length), in the story, summary, findings, methods,
limitations, charts, forecast and cleaning. The writer's words (`45-report-pdf.js periodOf, periodWords, sumCheckWords(c, U, P)`):
"4 quarters before", "Latest 4 quarters", "Quarters checked", "not allocated to a part in the latest 4 quarters". Example
(`estimand`, a quarterly stock, and `results_for_ai.forecast`):

```json
{
 "text": "Total; persons; 4-quarter averages Q1 2023–Q4 2023 vs Q1 2022–Q4 2022",
 "period": {
  "kind": "quarter",
  "noun": "quarter",
  "nouns": "quarters",
  "step": 3,
  "window": 4,
  "adjective": "quarterly"
 },
 "comparison": {
  "latest": [
   "2023-01",
   "2023-10"
  ],
  "prior": [
   "2022-01",
   "2022-10"
  ]
 },
 "figures": {
  "prior": {
   "value": 33060.25,
   "text": "33.1K",
   "months": 4
  },
  "latest": {
   "value": 34058.75,
   "text": "34.1K",
   "months": 4
  },
  "change": {
   "value": 998.5,
   "text": "+998"
  },
  "change_pct": {
   "value": 3.020243,
   "text": "+3.0%"
  }
 },
 "complete": true,
 "periods_used": 4
}
```

```json
{
 "frequency": "quarter",
 "reason": "No forecast is shown: the table is quarterly (one value a quarter), and the forecast reads monthly series only, so it has no back-test either."
}
```

**5. Repeated member names.** A name that stands for two members (Other under three parents) no longer refuses the table
(`_key_members`): for a dimension whose name column has a finer key beside it (an id: at least as many values as names, each
belonging to ONE name, and at least half the names belonging to one id), the members are that key's values, printed by their
name and qualified when two share it: an id column ("Other (C104)"), the one part of a dotted COORDINATE that tells the
dimension's members apart ("Other (id 5)": every part is checked against every dimension, never assumed by position, and a
withheld COORDINATE is never read), or, with no id, a parent column whose values are members' names ("Other (Retail)"). A
grouping (stores inside regions) is not an id: its names do not belong to one value each. `structure.keys[]` = `{dim, by,
duplicates[]}`; the id or parent column is metadata (`member_id`, `parent`). The only refusal is a table where nothing tells the
repeats apart (no id, or a parent that repeats too): `kind: "cube_incomplete"` with "the date and the columns the engine may
read (Industry) do not tell the rows apart: \"Other\" appears more than once for the same date (up to 3 times); 192 of 624 rows
repeat a date and a series, so a column that names the series is withheld or set aside" (and, when a series code such as
COORDINATE is withheld, that keeping it on the consent card may tell them apart).

**6. The unallocated item** (section 5.8): its label and `assumes` name the cause by the chart's rule and the amount is the real
residual.

**7. A wide table of periods** (stretch). A table whose header holds 6 or more period columns of one family (5 years)
(2016-01, 2016M01, 2016Q1, 2016-Q1, 2016H1, 2016) beside the columns that name the series is reshaped before anything reads it
(`nl_structure.wide_to_long`, called by `run()` and the profile pass): one row per series and period, the id columns kept
(`geo\\TIME_PERIOD` is `geo`), `TIME_PERIOD` and `OBS_VALUE` the cell's own text with an embedded flag (":" , "123.4 p") left in
it, where the structure layer strips it and counts it by the publisher's vocabulary (`structure.flags.by_kind`, `codes`); an
empty cell is no observation. `structure.wide` = `{family, periods, first, last, id_columns[], rows_in, rows_out}`, a sentence in the
first limitation, and the downloads carry no visitor line numbers. The planner's profile reads the file as it was sent.
Unit codes that hold a currency (MIO_EUR) are currencies.

*For the worker.* `validateStructure` takes the roles as listed in section 5.4 and drops unknown keys: it needs `measures`
(members validated as profile values), `no_total`, `period` and the plan's `measure_member` (`^M\d{1,2}$`); `structureRules` should
word a partition with no `total` ("has no total row: its parts are added up") and a measure dimension. `validateResults` should
keep the new estimand keys (`built_from`, `measure_choice`, `period`, `measure.type_basis`, `sum_checks[].built_from_parts` and
`suppressed_part_months`, `figures.*.months`) and `forecast.frequency`; the engineTitle fallback says "in the sum of the published
regions" for a headline with `built_from` and the period's words for a quarterly table; the guard should accept "4 quarters" where
it accepts "12 months".

## 6. What this contract does not carry yet (R1)

Tipping points (S1/M10), drivers and reversals (S2), per-claim power, posterior probabilities (C8), the real-data placebo and the cross-environment receipt, Little's MCAR test, restatement lists and the structural-break screen are `null`/`[]` with their reason. The page must show them as "not measured", never as zero.
