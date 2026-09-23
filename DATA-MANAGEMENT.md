# NorthLedger data management process

How data is handled in this project, mapped to two standard frameworks so a reviewer can trace each
step. The row counts, dates and checksums are not repeated here; they are kept in
[DATA-SOURCES.md](DATA-SOURCES.md) and on the page, where the build writes them from the data.

## The frameworks

**1. Google Data Analytics, 6 steps:** Ask, Prepare, Process, Analyze, Share, Act.

**2. CRISP-DM, 6 phases, iterative:** Business Understanding, Data Understanding, Data Preparation,
Modeling, Evaluation, Deployment.

| # | Step (Google / CRISP-DM) | What we do | Where it shows on this site |
|---|---|---|---|
| 1 | **Ask** / Business Understanding | Define the question and the decision it serves. | Each analysis card opens with its question (for example: which fixes are worth the most City points?) |
| 2 | **Prepare** / Data Understanding | Source public data, record the download (address, time, size, checksum), read the licence, profile before concluding. | Receipts on the page; `DATA-SOURCES.md`; the Data health page of the report |
| 3 | **Process** / Data Preparation | Clean with tested rules: trim, normalise dates, fix labels; quarantine with a reason, never silently drop; reconcile rows in = clean + quarantined. | The NorthLedger engine's audit and clean of the three City files, then the build's own domain rules, both reconciled on the Data health page |
| 4 | **Analyze** / Modeling | SQL and pandas for facts, simple models against simple baselines, every number traceable to code. | Analyses A to D and the Forecast Lab |
| 5 | **Share** / Evaluation | Out-of-sample tests, rules fixed before the results are seen, limits written next to every finding. | Each card's Limits; the gates (RECOMMEND, WATCH, INSUFFICIENT) |
| 6 | **Act** / Deployment | Decisions, one-command rebuilds, a Power BI project export. | `build.py`, `verify.sh`, the downloads |

## The rigour rules

- Profile before concluding; never assume what a column means.
- Every number traces to code and data; cite row counts.
- Quarantine, never silently drop. Reconcile against the source, or explain the difference.
- Uncertainty is mandatory: bands, baselines and backtests, never accuracy theatre.
- Synthetic or sampled data is disclosed, and every report has a limits section.

## Reproducibility

Every figure on the page is rebuilt from the recorded downloads with the commands in the README
(`build_rentsafe.py`, `build_forecast.py`, then `build.py` and `verify.sh`). The raw files stay
re-downloadable from the official sources listed in `DATA-SOURCES.md`.
