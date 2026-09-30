# The chart registry's frozen interface (`spec.json`)

Wave 0 of `plan/CHART-REGISTRY-DESIGN.md`, frozen 30 September 2026, version `2026-09-30.1`. Summarised in
`engine/CONTRACT-v2.md` section 5.9; where the two differ, `spec.json` wins.

One file, three builders:

| side | builds | reads from `spec.json` |
|---|---|---|
| engine (A) | `engine/nl_viz.py` and its hooks | `caps`, `menu`, `schema`, `invariants`, `tiers`, `chart_limits`, `plan_directive.engine_rules`, `auto_rank`, `pipeline.engine` |
| page and PDF (B) | `src/js/55-nl-viz.js`, the 51/52 hooks, the PDF writer's VIZ dispatch | `draw_kinds`, `schema`, `colors`, `tiers.glyph_rule`, `accessibility`, `phone_fit`, `pipeline.page`, `pipeline.pdf` |
| worker (C) | `src/charts.js` (`sanitizeChart`), plan/report/figures/share | `schema`, `invariants`, `plan_directive.worker_rules`, `chart_limits.rules`, `pipeline.worker` |

## The flow

1. **The engine emits it.** The profile carries `chart_limits` (one `{chart, ok, why}` per menu chart). The
   planner may return `plan.charts: [{kind, columns, why}]` (at most 8, `kind` a *menu* name). The engine checks
   each item again, builds it into a record that passes `schema` and `invariants`, or refuses it with a reason in
   `rep.viz.refused`. With no AI chart built it picks up to 4 itself (`auto_rank`). Every printed string and every
   colour tier is written here; the record is complete on its own.
2. **The page and the PDF draw it.** From the record alone, by its draw `kind`: `waterfall`, `heatmap`,
   `dot_range`, `pareto`, `slope`, or `table`. They never format a data value (only axis ticks), and always offer
   the record's `table` as the table view.
3. **The worker sanitizes it.** `sanitizeChart` cuts every field to the schema's caps and checks the kind's data.
   **An unknown kind, malformed data or a record over its byte cap (`HEATMAP_BYTES` for a heatmap, `CHART_BYTES`
   for every other kind) becomes kind `table` at the same index; a chart is never dropped**, so `[CHART:n]` means the same chart on the page, in the PDF and in a shared link. The model
   gets a card per chart (title, section, supports, grade, summary, table; never the data).
4. **/report returns the validated list.** The page and the share viewer draw the charts /report sent back, never
   their own copy (the old drift: the page kept its own list while the proxy dropped charts).

## The examples

`examples` holds 14 records: one per draw kind, plus the edge cases each side must survive: a price-volume-mix
waterfall, a diverging heatmap at the byte cap, an all-empty heatmap, a 1-row heatmap, suppressed cells, negative
totals in a waterfall, 12 long segment labels, and non-Latin labels (the PDF prints `[name N]` placeholders for
scripts it cannot show). Each says where its figures come from (`data_from`, `about`): most are real, computed
with the adapter's own formatters (`nl_browser._fmt`/`_amt`/`_pct_text`, `nl_scenarios._fmt_item`) from
`../scenarios/` (ship2: prior 128,256, latest 152,214, change +23,958; East +16,220, North +7,116, West +330,
South +292; price +20,527, volume +2,051, mix +1,380), `../eval/fx_usd_cad.csv` and `../eval/brand_ratings_2022.csv`;
the rest are marked illustrative. Builders use them as fixtures: the engine's tests compare their own records'
shape with them, the page and the PDF render all 14 (at 320 px too), and the worker round-trips them through
`sanitizeChart` unchanged (and the degraded ones as tables).

## Checking

```
python3 tools/fixtures/viz/validate_spec.py
```

Strict JSON (no NaN, no duplicate keys); the caps, menu, draw kinds and design colours as specified; colour
contrast (text 4.5:1 on every tier, marks 3:1) in both themes; every example against the schema and its kind's
invariants (waterfall running totals, heatmap cell states and tiers, Pareto running share and k80, byte size as
`JSON.stringify` measures it, against `HEATMAP_BYTES` for a heatmap and `CHART_BYTES` for every other kind); then 15
deliberately broken copies, each of which must be caught (among them a waterfall over 6,000 bytes and a heatmap over
12,000), and one copy that must be kept (a heatmap between the two caps). With the
`jsonschema` package installed it also checks the schema as draft 2020-12 and re-validates the examples. An engine
test can import `check_record()` and `validate()` from it to check its own records.

## Changing it

The spec is frozen for the builders of wave 1: a change bumps `version`, updates section 5.9 when it touches the
summary, and passes `validate_spec.py`. Two things to know before relying on a number:

- The byte caps (`caps`): `CHART_BYTES` (6,000) for every kind but the heatmap, `HEATMAP_BYTES` (12,000) for a
  heatmap: the owner's decision on the spec's byte-cap conflict (30 Sep 2026), recorded in `caps` and `caps_notes`
  under the same version, since the engine (`nl_viz.bytes_cap`) and the worker (`src/charts.js` `bytesCap`) already
  applied it. 6,000 bytes hold about 120 heatmap cells with their table view, so the 12 × 24 display cap was
  unreachable; at 12,000 a 10 × 24 calendar (240 cells) is 10,355 bytes. The engine still trims a grid over its cap
  (`pipeline.engine`).
- `chart_limits` holds at most 16 entries; Batch 1 uses 10, and Batch 2's 7 would make 17.
