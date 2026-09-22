# RUNBOOK.md — operating the NorthLedger pipeline

## Daily (automated — this page is what YOU read when it breaks)

| 06:00 | ingestion batch (extract → stage → test → transform → publish) |
| 08:00 | AI memo: pulls KPI deltas, drafts brief, validates every number against source, publishes |
| Mon 09:00 | model refresh + forecast backtest append |

## What breaks, how you know, who to page

| Failure | Signal | Immediate action | Root-cause path |
|---|---|---|---|
| Source schema drift | dbt source-freshness test red; ingest rejects rows into quarantine | No action needed at 06:00 — quarantined rows counted in the data-health line; investigate by 10:00 | `dbt build --select source:*` diff; the source contract lives in staging docs |
| Late-arriving events | freshness SLA breach (data older than 26h) | Backfill DAG (one command, below) | check upstream publish timestamps before suspecting us |
| Memo number-validation failure | memo not published + alert | The memo is WITHHELD, not wrong — read the validation log; fix the underlying mart, never bypass the validator | validation log + the failing mart's dbt tests |
| BigQuery quota | queries rejected | fall back to Parquet mirror for reads (documented path) | review bytes/query on the monitoring page |
| Warehouse table expiry (sandbox 60d) | missing-table error | one-command rebuild from Parquet (below) | this is expected sandbox behavior, not an incident |

## One-command operations

```bash
# rebuild everything from raw (fresh clone test):
dbt build --target prod
# backfill late data:
airflow dags trigger backfill_transactions
# regenerate the exec report numbers (reproducibility gate):
make regenerate-kpis   # fails CI if any published number can't be reproduced
```

## Rollback

- dbt: models are atomic swaps; previous table version kept as `<model>__prev` for one cycle.
- Dashboards: Power BI deployment pipeline (dev → test → prod) with one-click revert.
- Memo: withheld on validation failure (never a bad memo; absence is the failure mode).

## Page policy (solo operation)

Freshness breach or memo-withheld = same-day fix. Anything else = the weekly review.
An incident that repeats twice becomes a postmortem entry (see INCIDENT-POSTMORTEM template).