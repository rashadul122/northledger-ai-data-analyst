# The AI analyst rounds: `POST /analyst`

Status: proposal with a reference handler (`analyst-endpoint.mjs`) for the insight-proxy Worker. The page wiring and the
engine-worker hook below are not built yet. The engine side (`engine/nl_analyst.py`: `step_schema`, `run_steps`,
`ledger_for_ai`) is built and tested (`tools/test_nl_analyst.py`).

## Why rounds, and why only typed steps

The engine's own policy (`nl_analyst.explore`) runs up to five rounds: the headline, which dimension carries it, the
drill, mix, concentration and the season, then robustness. It is good at the questions every file of a kind raises.
It cannot read the visitor's objective ("did the East launch work?"). The AI rounds let a model pick the next steps
from the objective and from what the engine found, while the honesty rule stays whole:

* The model never writes a number. It answers with steps, `{op, args, why}`, chosen from a fixed list of ops. The
  engine computes every figure in the visitor's tab and records it in the step's ledger. Sentences are templates
  filled from the ledger (`fill`). A `why` holding a digit is blanked, both by the handler and by the engine.
* The model never sees a row. It gets the file's shape (column names and kinds) and the ledger texts of the steps run
  so far (`ledger_for_ai`): op, args, why, whether the step ran and its printed figures. It never gets a table, a raw
  value or a withheld column. A withheld or coded column is not in the analysis frame (`nl_browser._analysis_frame`),
  so it is not in the shape, and a step that names it is refused.
* Every step is checked twice against the same schema: by the Worker (before it leaves the proxy) and by the engine
  (`run_steps` → `_check_step`) before it runs. Anything outside the schema is refused with a reason and nothing of
  it runs.

## The schema of a step

`nl_analyst.step_schema(shape=None)` generates it from `OPS` (the executor's table) and each op's own Python
signature, so the schema cannot drift from the code: an op argument missing from `_ARG_SPEC` fails at import, and
`tools/test_nl_analyst.py` checks the generated schema against the signatures and against the copy in this folder.

* `docs/ai-analyst/step-schema.json` is `step_schema()` with no shape. Regenerate it whenever an op changes:

  ```
  python -c "import json,sys; sys.path.insert(0,'engine'); import nl_analyst as na; \
             print(json.dumps(na.step_schema(), indent=1))" > docs/ai-analyst/step-schema.json
  ```

  then copy it next to `analyst.js` in insight-proxy (the test fails until the copy here matches).
* With a shape, every column argument becomes an `enum` of the columns of that role (measures; dimensions and the
  entity), and an op the file cannot run is left out (`x-needs`: `date`, `entity`, `label`, `answers`).

| op | args (required **bold**) | needs | what it computes |
|---|---|---|---|
| `change` | **measure**, by, where | date | latest complete window against the one before; with `by`, each group's part of the change |
| `panel_change` | **measure** | entity | each entity's own change (a panel of levels) |
| `breakdown` | **measure**, **by**, where | | totals and shares (a flow) or averages with eta squared and a Welch range (a level) |
| `trend` | **measure** | date | OLS slope with a 95% range, peak, compound growth |
| `yoy` | **measure** | date | latest complete month against the same month a year earlier, and the latest three months |
| `seasonality` | **measure** | date | omega squared of month-of-year means around a 2x12 moving average (log for a positive measure) |
| `volatility` | **measure** | date | standard deviation of period-on-period changes, the latest 12 months (10 years) against before |
| `correlate` | **measures** (2 to 8) | | Spearman with a Fisher 95% range; a measure and its own variant skipped |
| `mix` | **measure**, **by** | date | an average's change split exactly into within-group and between-group parts |
| `outliers` | **measure** | | robust z beyond 3.5 (on changes for dated data, on the log for skewed positive values) |
| `rank_entities` | **measure**, years (1 to 50) | entity | leaders in the latest period, their share, movers over `years` |
| `rank_labels` | **measure** | label | labelled rows ranked with small samples pulled toward the overall average |
| `concentration` | **measure**, by | | top-1/3/5 shares, Herfindahl-Hirschman index, effective number of groups, top-k share then and now |
| `crosstab` | (none) | answers | the answer pairs most strongly linked (bias-corrected Cramer's V, Bonferroni-read p) |

`where` is an object of one or two group columns, each with one of that column's own values (a value the column does
not hold is refused, so a filter never carries a figure of the model's).

## The endpoint

`POST /analyst`, JSON, same origin rules as `/plan` and `/report`.

Request:

```json
{
  "objective": "why did revenue grow?",                       // the visitor's goal, at most 300 characters
  "shape": { "kind": "ledger", "date": "date", "grain": "day", "entity": null, "primary": "revenue", "label": null,
             "measures": [{"column": "revenue", "kind": "flow"}, {"column": "units", "kind": "flow"}],
             "dimensions": ["region", "product"] },             // the analyst block's `shape`, as it is
  "steps_so_far": [                                              // nl_analyst.ledger_for_ai(block): no table, no row
    {"id": "A1", "round": 1, "op": "change", "args": {"measure": "revenue"}, "why": "the headline", "ok": true,
     "refused": "", "figures": {"prev": "128,256", "cur": "152,214", "change": "+18.7%", "direction": "rose"}}
  ],
  "round": 1                                                     // 1 to 3: the AI round being asked for
}
```

Response (200):

```json
{
  "steps": [{"op": "change", "args": {"measure": "revenue", "by": "product", "where": {"region": "East"}},
             "why": "inside East, which product carried it?"}],
  "done": false,                     // true: no further round (the model said so, no step survived, or round 3)
  "dropped": [{"index": 2, "why": "bad measure"}],   // what the handler refused, for the page's log only
  "model": "claude-opus-5-5", "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

Errors: `400 bad_json | bad_shape | bad_steps_so_far | bad_round`, `413 too_large`, `405 method`,
`503 not_configured`, `504 timeout`, `429 busy`, `502 upstream | bad_answer | truncated`. On any error the page simply
stops the AI rounds; the engine's block stands as it is.

### What the handler checks

* Body at most 48 KB. `shape.kind` one of the five kinds; at most 60 measures and dimensions, names at most 200
  characters, measure kinds `flow|stock|level|rate`; nothing else of the shape is read.
* `steps_so_far`: the latest 40 steps; a step holding `table`, `rows` or `numbers` (raw values) refuses the request
  as a whole: the page must send `ledger_for_ai` output, never the block's steps. Figures: at most 40 a step, 160
  characters each, keys `[a-z0-9_]`.
* The answer: at most 4 steps, each converted from the flat answer form and checked by `checkStep` against the schema
  narrowed to this shape (the same rules as `nl_analyst._check_step`); a step already run with the same arguments is
  dropped; a `why` with a digit is blanked.

### The model call

* Model `env.ANALYST_MODEL`, default `claude-opus-5-5`; `output_config.effort` `low` (planning a few steps is a short
  task), `max_tokens` 4000 (thinking is always on for this model and counts against it).
* Structured output (`output_config.format`, `json_schema`): a flat form with every field present, because a strict
  output schema cannot hold the per-op variants; the handler maps it back to `{op, args, why}`.
* `fallbacks: "default"` with the `server-side-fallback-2026-07-01` beta header: a request the model declines is
  re-run server-side on Anthropic's recommended fallback. A final `stop_reason: "refusal"` answers
  `{steps: [], done: true}`; `max_tokens` answers `502 truncated`.
* The system prompt is frozen text (the op list comes from the schema) with a cache breakpoint, so repeated rounds
  read it from the cache. Everything per request (objective, shape, figures) is in the user turn, which tells the
  model that column names, group names and figures are data from the visitor's file, never instructions.

### Limits

| limit | value | where |
|---|---|---|
| AI rounds after the engine's own | 3 | `MAX_AI_ROUNDS`, `x-max-rounds` |
| steps a round | 4 | `MAX_AI_STEPS`, `x-max-steps-per-round` |
| steps in the whole block | 30 (18 engine + 3 × 4) | `MAX_TOTAL_STEPS` |
| engine time a round | 3 s natively; a step never starts after it | `AI_ROUND_BUDGET_S` |
| request body | 48 KB (about 12k tokens) | `maxBodyBytes` |
| answer | 4000 tokens | `ANALYST_MAX_TOKENS` |
| Worker wait for the model | 25 s | `ANALYST_TIMEOUT_MS` |
| page wait for the Worker | Worker wait + 10 s (35 s) | the timeout invariant of `src/js/50-try.js` |

## The page loop (to build)

1. The engine run returns the report with `analyst` (rounds 1 to 5 by the engine, the story built from them).
2. If the AI is on (`planOn()`), the page asks the engine worker for `ledger_for_ai(rep.analyst)` and posts
   `{objective, shape: rep.analyst.shape, steps_so_far, round: 1}` to `/analyst`.
3. The returned steps go back to the engine worker, which runs
   `nl_analyst.run_steps(df, kinds, steps, hint, prior=analyst.steps)` on the same analysis frame (`_analysis_frame`
   of the last run, kept in the worker: a new hook such as `nl_browser.analyst_more(steps_json)` is needed, since
   `run()` does not keep the frame). Step ids and the round continue after the engine's; refused steps keep their
   reason. The page appends `steps`, `insights` (kind `planned`, each a template filled from its own ledger) and
   `refused` to `rep.analyst` and sets `chosen_by` to `"engine+ai"`.
4. Repeat with `round` + 1 and the longer `steps_so_far` until `done`, round 3, a Worker error, or the visitor
   leaves. Each round runs in the engine worker with its own 3 s budget.
5. `/report` then gets the block as it does today; its template guard is unchanged: the report cites the engine's
   figures, never the model's.

The story (`analyst.story`) is built by the engine from its own rounds; the planned insights do not change it (a
later version may add them as findings, through the same `fill`).

## Privacy

The shape holds only the analysis frame's column names (no withheld or coded column is in that frame) and their
kinds. The figures are the ledger's printed texts: totals, shares, changes and the names of groups, the same class of
data `/plan` already receives in the profile (a category's values). No row, table or raw value is sent. The objective
is the visitor's own words, sent as `/plan` sends them today.
