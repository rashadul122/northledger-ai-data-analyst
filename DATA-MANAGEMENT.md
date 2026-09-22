# NorthLedger Data Management Process

How every dataset in this project is handled — mapped to the two industry-standard
frameworks, so anyone reviewing can trace each step.

## The frameworks

**1. Google Data Analytics — 6 steps** (the Google Data Analytics Certificate's spine):
Ask → Prepare → Process → Analyze → Share → Act

**2. CRISP-DM — 8 phases** (the classic KDD/industry standard for data mining):
1. Business Understanding · 2. Data Understanding · 3. Data Preparation ·
4. Modeling · 5. Evaluation · 6. Deployment · (plus the two continuous loops:
7. Planning/monitoring throughout, 8. Review/next-cycle)

This project runs the same discipline, in this order, every time:

| # | Step (Google / CRISP-DM) | What we actually do | Where it happened today |
|---|---|---|---|
| 1 | **Ask** / Business Understanding | Define the question and the decision it serves. | Session goals ("what's broken and what does it cost", "forecast the next 12 months") |
| 2 | **Prepare** / Data Understanding | Source public data, verify licenses, profile before concluding. | DATA-SOURCES.md (9 links, license + row counts); `profile_table` tool; Socrata/API bulk downloads verified live |
| 3 | **Process** / Data Preparation | Clean: dedupe, normalize formats (4 date formats → ISO), fix casing, quarantine (never silently drop), type-correct. | s2-clean session: 20,488 messy rows → 14,104 clean + 6,384 quarantined, zero deltas vs source |
| 4 | **Analyze** / Modeling | SQL for facts, Python for modeling, forecasts with baselines. Every number from a real query, row counts cited. | Scale-tier sessions: weighted census estimates, fleet age/EV shift, 311 resolution-time medians |
| 5 | **Report** / Share (Evaluation) | Deliverables: PDF/Excel/Word, numbers-first, limitations mandatory. Forecast honesty: 80% bands, seasonal-naive comparison, backtests. | make_report tool (validated: rejects empty bodies); s1 PDF audit, s2 xlsx reconciliation, s3 docx brief |
| 6 | **Act** / Deployment | Decisions, one-command re-runs, monitoring. | "What to do" blocks in every report; ingest scripts re-runnable; scale DB re-buildable from DATA-SOURCES.md |

## The rigor rules (from the blueprint's Section 5)

- Profile before concluding; never assume column meanings.
- Every number traces to a query; cite row counts.
- Quarantine, never silently drop. Reconcile against source (zero deltas or explain them).
- Uncertainty mandatory: bands, baselines, backtests. Never "99.8% accuracy" theater.
- Synthetic/sampled data disclosed; limitations section in every report.

## Reproducibility

```bash
# any dataset, from download to queryable table:
venv/bin/python ingest_big_data.py --only chicago   # | nyc311 | pums-persons | pums-households | amazon-reviews | amazon-meta | rdw
```

Raw files are deleted only after their row count verifies against the official
total (documented in DATA-SOURCES.md); every dataset remains re-downloadable
from its official source. Chunked ingestion (50k rows/batch) means no file is
ever loaded whole — the 57.8M-row tier was built on a laptop with 16 GB RAM.