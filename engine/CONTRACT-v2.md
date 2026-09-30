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
   the tests.
3. **Absent means absent.** A number the engine does not measure in this release is `null`, never
   `0`, and the enclosing object says why in a `note` or `not_measured` field. Lists that nothing
   filled are `[]`.
4. **Text fields** go through the v1 scrubber (withheld values, phone numbers, email addresses and
   the engine's internal table name are replaced). No text field carries a number the engine did not
   write.
5. **Flagged columns never appear in chart data** (`charts[].data`): not as a series, a matrix row
   or column, a category source or a label, whatever the visitor decided for them. A withheld column
   also never appears in `health.columns[].top_values`, `numeric` or `dates`.
6. Every float is finite or `null` (the page parses with `JSON.parse`).

## 1. Top level

| key | type | v | meaning |
|---|---|---|---|
| `ok`, `error`, `engine`, `input`, `timings`, `privacy`, `health`, `cleaning`, `roles`, `findings`, `forecast`, `story`, `downloads` | | 1 | as in v1 |
| `contract_version` | int | 2 | `2` |
| `primary_metric` | object or null | 2 | `{finding_id, claim_key, grade}`: the claim the engine tested in its `primary` family, or null |
| `tests_run` | object | 2 | `{families: [{name, size, fdr_method, level}], claims_tested}` from the gate's family pass |
| `methods` | list | 2 | `{id, name, assumptions[], applies_to[] (finding ids), desktop_only}` for each method that ran |
| `limitations` | list | 2 | `{kind: data|statistical|causal|forecast|external, text, finding_ids[]}` |
| `reproducibility` | object | 2 | the provenance block, §2.6 |
| `charts` | list | 2 | chart records, §3 |
| `charts_suppressed` | list | 2 | `{rule, type, why}` for each §5 chart whose rule did not fire (§5 "Suppression") |
| `llm` | object | 2 | `{used: false, model: null, consent: false, guard: {...}}`; the adapter never calls a model |
| `scenarios` | object | 2 | the headline claim broken down for the report writer (design B, 29 Sep 2026): `{basis, items[], refused[], note}`, §5.8. Always present; `{basis: null, items: [], refused: [], note: ""}` on a refusal |
| `summary` | object | 2 | the manager's bottom line (fixer round, 24 Sep 2026): `{lines: [{kind: "moved"|"act"|"plan", text, finding_ids[]}], labels: {finding_id: plain label}, monitoring: [finding_id]}`. `lines` holds at most three sentences built from the engine's facts, every number printed with `narrate.story_number` from a fact the line lists: what moved and why (the primary claim, the steps where what the file covers changed and its like-for-like restatement, a fall since a peak), what to act on (the CONFIRMED business claims, or none), and the planning number (the primary series' forecast, graded). `labels` names each business and forecast claim in the reader's words ("Rent, monthly total", "Ledger lines a month"). `monitoring` lists the claims about the ledger's own line count when the file has a money total: kept off the first screen, the tiles and the bottom line; they stay in the tables and the analyst view |

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
| `effect` | `{estimate, ci [lo,hi], level, ci_fcr [lo,hi], fcr_level, method, scale, unit, note}`. Change claims: fractions (0.2 = +20%), the 95% test-inversion interval and, for a CONFIRMED claim, the selection-adjusted (FCR) bound: one-sided (Benjamini-Yekutieli), so the far end is null (a rise reads `[lower, null]`) and `fcr_level` is its level, e.g. 0.9933. A change claim with no ratio (monthly averages at or below zero, `gate._no_ratio`): `scale: "difference"`, `estimate` the difference in the measure's own units (the fact's value), `unit` that unit, no interval, never a percentage. Forecast: the next month's point and 80% range; when the engine does not offer the forecast (`forecast.available` false or grade NOT_ENOUGH_DATA), `estimate`, `ci` and `level` are null and `note` says it is not offered. State facts: the value, no interval. `scale: "money"` marks the forecast of a series the engine totals as money (its additive kind `money`): the page prints it and its range in whole units. |
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

### 2.5 `forecast`

v1 keys plus `band{level, method, n_errors, max_level, conditional_coverage_p10_p90, scope, dependence, widened_by_max, n_effective_h1}`, `coverage{hits, n, wilson, binomial_p, christoffersen_ind_p, christoffersen_cc_p}`, `baseline_test{method, stat, df, n, p, gain}`, `models[]` ordered by MASE (the headline measure) with `{name, label, mase, mape, mape_suppressed, msis, skill_vs_sn, mae, coverage, dm, champion, baseline, applicable}`. `coverage` (`{hits, n, rate}`) is measured for the champion's shipped range only, and `dm` (`{stat, p, n, of}`) is the whole method's test (its model choice at each replayed month included) against seasonal-naive, carried on the champion's row; both null on the other rows, `break: null`, `interventions: []`, `forecastability: null`, `decision_edge: null`. All from the forecast result the engine verified.

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

Suppressed rules (no chart drawn, one line in `charts_suppressed`): #2b driver waterfall (S2 is not built in R1), and any row above whose condition did not hold.

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
When the engine refuses its own business analysis (its gate: it set aside more than
`gate.DEFAULT_POLICY.max_quarantine_rate`, 20%, of the rows), no analysis is drawn from the rows it kept either:
`items` is empty, `refused` holds one line ("trend, extremes: the engine set aside 30.0% of the rows (180 of
600), over its 20% limit, so no analysis is drawn from the rest"), and `gate` is `{over, pct, limit, aside,
rows}`. When the gate trips, `plan_signals` is empty (no data test, step or analysis signal), so the planner is
not asked again: a plan cannot make an unreadable cell readable, and the one change a planner can make, setting
the unreadable column aside and reading another, is the wrong-axis result the gate exists to prevent (review
cases f and g). The page does not ask again either when a report's `ai_analyses.gate.over` is true or its
headline is the gate's, whatever signals the report carries.
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
row count the engine's own gate chose there).

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
sensitivity, gaps, the forecast, the facts) with the per-segment groups (`contribution`, `per_unit`, `gap`:
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
level that entered leaves out the mix and says so; `assumes` is "price, volume and mix add up to the change", or
"price and volume add up to the change" with no mix item), `per_unit` (overall and per
segment: both windows and the change in percent, `per_unit.change_pct` and `per_unit.<segment>.change_pct`, kind
`change`, unit "%": "revenue per unit 41 to 47.9", "+16.8%"), `run_rate` (the latest 12 months and their average
month; never a 3-month annualised rate), `sensitivity` (each 1% of the measure over a year at the latest level), `gap` (each segment's distance to
the largest level in the latest 12 months, chosen among ALL the levels (a folded one or one that entered too) and so
named in the label ("below Uptown (new)'s, the largest store by total sales in the latest 12 months"), in the measure
and in percentage points of the total, and, with units, what its units would have made at the largest's price per
unit when that is higher; never for "other", the largest itself or a level that exited), `forecast`, `facts` (months and
distinct dates in the kept rows, the first and the last date). `kind` is `amount`, `change`, `count`, `percent`,
`points`, `per_unit` or `date` (a fact's first or last date: `value` is the date text). `text` is
`nl_scenarios._fmt_item(value, kind, unit)`, the one function, in the adapter's own formats (`_fmt`, `_amt`,
`_pct_text`): a rise carries "+", a fall the true minus sign, a change in percent is `kind: change, unit: "%"`, a
share `kind: percent`. `value` is finite, rounded to 1e-6; an item whose value cannot be formed is absent.
`window` is `prior`, `latest`, `both` or null. `assumes` is the item's assumption in words (a run rate, a
sensitivity, a what-if, the price, volume and mix split, a forecast sum), else null.

**Grades (final review, 30 September 2026).** The headline items (`headline.*`: the claim itself) carry the claim's
`grade`, in the page's words as `findings[].grade` has them (`CONFIRMED`, `WATCH`, `NOT_ENOUGH_DATA`), with
`parent_grade` null and `grade_words` "the claim itself, graded WATCH". Every derived item (`contribution`,
`price_volume_mix`, `per_unit`, `run_rate`, `sensitivity`, `gap`) is not graded itself: `grade` null, `parent_grade`
the claim's grade and `grade_words` "part of a change graded CONFIRMED; not graded itself" (a region that fell 1.63%
inside a CONFIRMED rise read CONFIRMED before). A forecast item carries the forecast's `grade` ("the engine's
forecast, graded CONFIRMED (usable for planning)"), a fact `grade` null ("a fact about the rows, not graded"); both
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

## 6. What this contract does not carry yet (R1)

Tipping points (S1/M10), drivers and reversals (S2), per-claim power, posterior probabilities (C8), the real-data placebo and the cross-environment receipt, Little's MCAR test, restatement lists and the structural-break screen are `null`/`[]` with their reason. The page must show them as "not measured", never as zero.
