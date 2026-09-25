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

## 5. What this contract does not carry yet (R1)

Tipping points (S1/M10), drivers and reversals (S2), per-claim power, posterior probabilities (C8), the real-data placebo and the cross-environment receipt, Little's MCAR test, restatement lists and the structural-break screen are `null`/`[]` with their reason. The page must show them as "not measured", never as zero.
