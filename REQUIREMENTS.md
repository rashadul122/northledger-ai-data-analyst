# REQUIREMENTS.md — NorthLedger executive KPI brief (BRD-lite)

**To:** the finance-stakeholder review committee (role-played for the build; the
reviewer's questions are real interview questions)
**From:** the analytics function
**Re:** automated weekly executive intelligence

## The ask (in the stakeholder's words)

"We need Monday's numbers in front of us Monday morning — revenue trajectory, fraud-loss
rate, retention, forecast — with a one-paragraph story of what changed and what we should
watch. Today, someone re-types it by Wednesday and we're arguing about whose spreadsheet
is right."

## Success criteria (how they'll judge it)

| # | Criterion | Measure |
|---|---|---|
| 1 | Monday 08:00, without a human | publish SLA hit rate ≥ 95% (measured, on the dashboard) |
| 2 | Numbers we trust | every KPI traceable to the KPI dictionary; reconciliation deltas = 0 |
| 3 | A story, not a wall of numbers | 1-page memo: what changed / what it means / what to do; every figure validated |
| 4 | Forward view | 12-month forecast with 80% bands; band integrity reported monthly |
| 5 | Honest failure | memo WITHHELD (not wrong) if validation fails; we get the alert, not a bad brief |

## Out of scope (explicit)

Real-time streaming; mobile apps; any manual number entry (if a human types a number,
it's a defect).

## The senior probes this must survive (from the research; answers live in the docs)

- "What's the grain of the fact table?" → transaction grain, degenerate dims; decision log
- "How do you handle late-arriving data?" → quarantine + backfill DAG; RUNBOOK
- "What breaks at 10x volume?" → partition/cluster keys + incremental merges; COST.md
- "Can I regenerate every number?" → one command; CI gate; KPI-DICTIONARY change policy
- "What does it cost?" → COST.md, measured bytes on the monitoring page