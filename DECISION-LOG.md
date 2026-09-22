# NorthLedger — Architecture Decision Log (ADR-style)

One decision per entry. Decisions are never deleted — superseded entries stay with pointers.

## ADR-001 — Warehouse: BigQuery (sandbox) over local Postgres
**Date:** 2026-09-22 · **Status:** Accepted
**Context:** The flagship needs a governed warehouse; budget is $0/mo. Local Postgres gives
full control but no cloud-credential story; BigQuery sandbox gives 1 TiB queries/mo + 10 GiB
storage free, native partitioning, and the exact "BigQuery idioms" Google postings name.
**Decision:** BigQuery sandbox as the warehouse of record; local Parquet mirrors of every
table (sandbox has 60-day table expiry — the mirror is the durability layer, one-command rebuild).
**Consequences:** Query cost-awareness becomes a habit early (documented $ per query);
the rebuild path is tested monthly.

## ADR-002 — Orchestration: Airflow over Dagster
**Date:** 2026-09-22 · **Status:** Accepted (confirms Rev-2 correction)
**Context:** Dagster is cleaner to author; Airflow appears 231 vs 23 UK postings in the
2026 evidence sweep and is the name interviewers say.
**Decision:** Airflow for scheduling; Dagster remains reading knowledge only.
**Consequences:** More boilerplate, better hiring signal; the pipeline DAG is a portfolio
artifact itself.

## ADR-003 — SQLite for the scale-tier demo DB; BigQuery remains the flagship target
**Date:** 2026-09-22 · **Status:** Accepted
**Context:** The AI-agent demo needed a zero-setup database a client can run locally;
57.8M rows ingested from 7 public datasets on a laptop with 16 GB RAM.
**Decision:** SQLite (WAL, chunked ingestion, busy_timeout) for the demo/scale tier;
the flagship still targets BigQuery per ADR-001. The same SQL discipline transfers.
**Consequences:** Demo runs anywhere with `python`; one honest caveat to state in
interviews — SQLite lacks columnar scan speed, so we demonstrate patterns, not BigQuery scale.
Production lessons already banked: journal-mode trade-offs, concurrent-reader locking,
verify-before-delete, chunked commits (see agent/README "Notes").

## ADR-004 — Synthetic transactions: PaySim + self-built generator, disclosed everywhere
**Date:** 2026-09-22 · **Status:** Accepted
**Context:** Real bank transaction data is not public; the evaluation rigor is the skill
being demonstrated (blueprint honesty rule).
**Decision:** PaySim + a seeded synthetic generator scaled to 10M+ rows; disclosed in
README, dashboards, and model cards. Public REAL data carries the scale story instead
(57.8M rows across 7 datasets).
**Consequences:** Nobody can accuse the portfolio of hiding synthetic data; the generator
is itself a code artifact.

## ADR-005 — Agent report tool validates non-empty content
**Date:** 2026-09-22 · **Status:** Accepted
**Context:** A model once shipped a title-only PDF (empty make_report call accepted silently).
**Decision:** make_report rejects kind/body_md < 200 chars with a descriptive error; the
agent retries with full content. The rejection+retry stays visible in the demo transcript
on purpose.
**Consequences:** Every shipped artifact has real content; the guardrail became a demo
feature.