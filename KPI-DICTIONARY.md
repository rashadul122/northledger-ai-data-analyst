# NorthLedger — KPI Dictionary v1.0

**Owner:** Rashadul Islam Roman · **Status:** Phase 0 (governance-first scaffold)
**Rule:** one page per executive KPI — formula, grain, owner, freshness SLA, change policy.
This is the artifact behind "source-of-truth datasets" in senior postings: every dashboard
number traces to a row here, and every change here is versioned.

## Module A — Fraud & Risk Operations

| KPI | Formula | Grain | Freshness SLA | Notes |
|---|---|---|---|---|
| **Fraud loss rate** | SUM(fraud_amount) / SUM(transaction_amount), rolling 30d | daily | T+1 06:00 | Reported in bps; exclude known-benign rules before the loss call |
| **Alert precision @ operating threshold** | TP / (TP + FP) at the deployed score cutoff | daily | T+1 06:00 | Threshold changes require a model-card entry + 2-week shadow run |
| **Cost-of-fraud vs false-positive burden** | fraud_value_captured − (FP_reviews × review_cost) | weekly | Mon 09:00 | review_cost = fully-loaded analyst hour / expected reviews per hour |
| **Recall in dollars** | fraud_value_captured / total_fraud_value | weekly | Mon 09:00 | Never reported as % of counts alone — dollars drive the decision |

## Module B — Customer Growth

| KPI | Formula | Grain | Freshness SLA | Notes |
|---|---|---|---|---|
| **CLV (cohort-adjusted)** | Σ(-margin_i × retention_p_i × discount) per cohort, SCD2 state at decision time | monthly | 3rd business day | SCD2 lookup is mandatory — measuring against today's state leaks |
| **Churn risk exposure** | SUM(clv_at_risk × p_churn) for active base | weekly | Mon 09:00 | p_churn from the deployed model; exposure in $ |
| **Campaign lift (incrementality)** | (Treat conv − Control conv) / Control conv, with CI + power stated | per campaign | readout +14d | No peeking; guardrail breaches logged even when convenient |

## Module C — Executive Intelligence

| KPI | Formula | Grain | Freshness SLA | Notes |
|---|---|---|---|---|
| **Forecast band integrity** | % of actuals landing inside the published 80% band | monthly | 1st | Target 75–85%; sustained >90% = bands too wide, review |
| **KPI delta brief coverage** | briefs published / briefs scheduled, zero number-validation failures | daily | 08:00 | Memo may only narrate validated figures; validation failures page |
| **Pipeline health** | freshness test pass rate + cost/query vs budget | daily | 08:00 | Shown ON the exec dashboard — ops transparency is a feature |

## Change policy

1. Formula changes require a decision-log entry + 1 full period of shadow reporting.
2. No KPI is removed, only deprecated (kept in the dictionary with an end date).
3. Every KPI here must regenerate from the warehouse with one command — if a number
   can't be reproduced, it doesn't ship.

*Next revisions arrive with each module build (M2–M5). This file is versioned in-repo.*