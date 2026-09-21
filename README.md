# NorthLedger Insights

**Portfolio of Rashadul Islam Roman** — data analytics, automation, and AI-agent engineering. Toronto.

- **Portfolio site:** https://rashadul122.github.io/northledger-insights/
- **AI-agent demo:** https://rashadul122.github.io/northledger-insights/agent-demo.html

## What this is

Three layers, built to be consumed in 30 seconds, 3 minutes, or a full review:

1. **The site (`index.html`)** — the dual-door portfolio: employers see the flagship plan and a live federal-data forecast (real FRED series, honest 80% bands, seasonal-naive baseline); business owners see the productized service ladder (Data Health Audit → Automated Insights Build → Insights Retainer).
2. **The AI-agent demo (`agent-demo.html`)** — a self-contained chatbox replaying **three real sessions** of an AI data analyst with full database access. It profiles a deliberately messy 20,488-row legacy export, cleans and reconciles it with zero deltas against source, and forecasts 12 months with honest uncertainty. Every report (PDF / Excel / Word) was generated *by the agent, in-session*, and downloads from the page.
3. **The source (`agent/`)** — the actual engineering: a tool-calling agent loop (SQL, profiling, Python, forecasting, report generation) against a real 60,000-row NYC collision database, plus the reproducible build pipeline for the demo page.

## The case study in 60 seconds

**Problem.** Small and mid-size businesses sit on messy data — exports, spreadsheets, five disconnected tools — and decisions run on instinct because reporting is a chore. Large-scale public data (8.6M Chicago crime records, 22.5M NYC 311 requests, ACS census microdata) has the same shape: too big for Excel, too messy for quick answers.

**Approach.** Build the loop once, prove it three ways: a rigorous profile-first audit standard (every number from an actual query, quarantine over silent drops), an honest forecasting standard (80% bands, naive-baseline comparison, backtests), and an AI agent that runs both on any database it's pointed at — producing client-ready PDF/Excel/Word reports, not dashboards nobody reads.

**Results (from the shipped sessions).**
- Audit: 100% of legacy rows defective; duplicates inflate injury counts +2.6%; "NULL" ranked as the #1 borough until cleaning — all found by the agent with exact counts.
- Cleaning: 20,488 messy rows → 14,104 clean + 6,384 quarantined (sum exact, nothing dropped silently); reconciliation against the source system showed zero deltas.
- Forecast: rolling backtest run; the naive baseline won (MAPE 4.95% vs 7.07%) — reported honestly, with the model kept as the scenario path.

**Stack.** Python, pandas/numpy, SQLite (same pattern scales to Postgres/BigQuery), Ollama Cloud (GLM-4-class model), fpdf2/openpyxl/python-docx, single-file HTML with zero dependencies.

## Repo layout

```
index.html                  <- portfolio site (start here)
agent-demo.html             <- AI-agent demo: 3 real sessions + embedded reports
Senior-Data-Analytics-Portfolio-Blueprint.pdf   <- 34-page evidence-backed plan
reports/                    <- the agent's generated deliverables
  legacy-export-data-health-audit.pdf
  cleaning-validation.xlsx
  collision-forecast-and-risk-brief.docx
agent/                      <- reproducible source
  agent.py                  <- tool-calling agent (run_sql, profile_table, run_python, forecast, make_report)
  build_database.py         <- builds the demo DB (real NYC data + synthetic legacy mess)
  build_sessions_js.py      <- transcripts -> embeddable sessions.js
  build_page.py             <- assembles the single-file demo page
  README.md                 <- how to re-run everything
```

## Reproduce it

```bash
cd agent
python3 -m venv venv && venv/bin/pip install requests pandas numpy openpyxl python-docx fpdf2
venv/bin/python build_database.py                     # ~60k real NYC rows + messy legacy table
venv/bin/python agent.py --session demo --goal "..."  # run your own session
```

Requires an `OLLAMA_API_KEY` (Ollama Cloud) in `~/.hermes/.env` or the environment.

## Honesty notes

- The demo database is public NYC Open Data (motor vehicle collisions); the "legacy export" mess is synthetic but modeled on real Excel-export abuse (duplicates, mixed formats, casing chaos). Disclosed on the demo page as well.
- The forecast demo on the site uses live FRED federal data (series RSAFS, ECOMPCTSA) — every number regenerates from the build script.
- The scale-tier datasets (Chicago Crimes 8.6M rows, NYC 311 22.5M rows, ACS PUMS) are not in this repo (GitHub's 100 MB file limit); ingestion scripts live in `agent/`.
- AI-assisted workflow disclosure: this portfolio and its pipeline were built with Hermes Agent (GLM). All analysis claims are backed by queries and files you can verify above.

## Contact

- GitHub: https://github.com/rashadul122
- The site's "Book a data audit" door is the client entry point.