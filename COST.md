# COST.md — what NorthLedger costs to run

Last updated: Phase 0 (estimates become measurements at M2; every number below gets a
measured value in the monitoring page once the pipeline is live).

## Monthly run cost

| Item | Cost | Notes |
|---|---|---|
| BigQuery sandbox | $0 | 1 TiB queries + 10 GiB storage free; local Parquet mirror handles durability (60-day table expiry) |
| Airflow (local) | $0 | Runs on the build machine; scheduler + 2 workers |
| dbt Core | $0 | Open source |
| Power BI | $0 → $14 | Free tier through build; Pro for the 2–3 scheduled-refresh demo months only, then cancel |
| LLM insight layer | ~$3–8 | Batch API, weekly memo workload (~10M in + 3M out tokens/mo); Ollama local = $0 alternative |
| Fabric trial | $0 | 60-day window, recorded during the data-engineering phase before expiry |
| **Total (build phase)** | **≈$0–22/mo** | |

## Cost-awareness discipline

- Every dashboard's refresh query is costed (bytes scanned logged in the monitoring page).
- The exec report's 3 heavy queries are partition-pruned by date; monthly bytes budgeted.
- The 10x-volume plan: partition + cluster keys documented per table in dbt schema files;
  incremental merge strategy on the transaction fact; estimated cost at 100M rows stated
  in the model card, not discovered at 3am.

## What I'd cut first if the bill mattered

1. Materialized intermediate marts → views (recompute cost vs storage trade-off stated).
2. Memo cadence weekly → biweekly (halves LLM spend).
3. Pro license months (only the demo-video window needs scheduled refresh).