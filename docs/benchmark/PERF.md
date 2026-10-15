# Engine speed on wide panels: where the time goes

Measured natively (Python 3.12, pandas 2.2.3) with `cProfile` on `co2_panel` from the corpus:
OWID CO2 data, about 50,000 rows and 79 columns. The whole run takes 481 s against the 30 s
budget. Pyodide on a phone is several times slower than this.

| stage (rep.timings) | seconds |
|---|---|
| read | 13 |
| profile | 71 |
| decide | 17 |
| clean | 84 |
| analyze | 170 |
| story | 94 |

## Hotspots, by cumulative time

| where | cum. s | what it is |
|---|---|---|
| `northledger/clean.py` `clean_frame` → `_rule_numeric` (231 calls) | 162 | the numeric rule, column by column |
| `clean.py` `_coerce_numeric_detail` (11.6 M calls) | 92 | a Python call per cell |
| `clean.py` `_numeric_body` (10.4 M calls) | 59 | the same, per cell |
| `clean.py` `_decimal_convention` (231 calls) | 43 | per-column string scans |
| `sqlite3.Connection.execute` (1,036 calls) | 158 | engine queries; `run_analyze` and `health.score_table` |
| `health.py` `score_table` → `_profile_column` | 151 | profiling each column |
| `clean.py` `_changed_mask` / `_is_na` (19.6 M calls) | 30 | per-cell missing-value checks |
| adapter `_engine_reading` (replays the cleaner's rules) | 77 | the same numeric rule again: the "one parser" design |
| adapter `_profile_facts` | 28 | |
| `nl_viz.build` | 20 | |
| analyst loop (`nl_analyst.explore`) | < 6 | inside its budget on every corpus file |

## What would fix it (all in northledger-core, which this repo does not hold)

1. **Vectorise the numeric rule.** Run `_coerce_numeric_detail` / `_numeric_body` once per *distinct* value
   (`Series.unique()` then `map`), not once per cell. A 50,000-row column of years or rounded figures holds a few
   hundred distinct strings. Better still, take a fast path when a column fully matches `^-?\d+(\.\d+)?$` and
   carries no decimal-comma evidence: one `pd.to_numeric` call. Expected: most of the 162 s.
2. **Missing-value checks as one vector op.** `_is_na` per cell (19.6 M calls) can be
   `s.isna() | s.str.strip().str.lower().isin(NULL_TOKENS)`.
3. **SQLite.** 1,036 queries take 158 s. Check that per-column profiling reads the table once into a frame
   instead of running one `SELECT` per column and test, and that the landed table has the indexes its `GROUP BY`
   queries need.
4. **Then the adapter's replay** (`_engine_reading`) gets the same speed-up for free, because it calls the
   cleaner's own rules. The adapter must not take its own short cut: the owner's "one parser" decision of
   29 Sep 2026 requires the report to read every cell exactly as the engine did.

The adapter-side items (`_profile_facts`, `nl_viz.build`) total under 50 s and are worth doing only after the
core items, which hold over 80% of the time.
