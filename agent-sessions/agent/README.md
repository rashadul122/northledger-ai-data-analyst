# NorthLedger Agent — reproducible source

The AI data analyst demo on the portfolio site is real engineering, not a mockup. Everything here re-runs end to end.

## What it is

`agent.py` is a tool-calling agent loop (the same architecture as [Hermes Agent](https://github.com/NousResearch/hermes-agent)) pointed at a client database:

| Tool | What it does |
|---|---|
| `run_sql` | Read-only SELECT against the database (enforced: writes are rejected) |
| `profile_table` | Row counts, per-column nulls, distinct values, top values, duplicate checks |
| `run_python` | Full pandas/numpy analysis; stdout is captured and returned |
| `forecast` | OLS trend + monthly seasonality, 80% bands, seasonal-naive baseline — always both |
| `make_report` | Writes PDF / Excel / Word deliverables (validates: rejects empty bodies) |

The system prompt carries the rigor standard: profile before concluding, every number from an actual query, quarantine rather than drop, uncertainty mandatory, disclose limitations.

## Reproduce

```bash
python3 -m venv venv
venv/bin/pip install requests pandas numpy openpyxl python-docx fpdf2

# 1) Build the demo database (~60k real NYC collision rows + the messy legacy export)
venv/bin/python build_database.py

# 2) Run a session (needs OLLAMA_API_KEY for Ollama Cloud in ~/.hermes/.env)
venv/bin/python agent.py --session mysession --goal "Profile legacy_export and audit its data quality"

# 3) Rebuild the demo page from all sessions
venv/bin/python build_sessions_js.py
venv/bin/python build_page.py   # -> agent-demo.html
```

## The shipped sessions

| Session | Goal | Deliverable |
|---|---|---|
| s1-audit | Data-health audit of the messy legacy export | `reports/legacy-export-data-health-audit.pdf` |
| s2-clean | Clean, quarantine, reconcile against source | `reports/cleaning-validation.xlsx` |
| s3-forecast | 12-month forecast + risk brief | `reports/collision-forecast-and-risk-brief.docx` |

Each transcript is at `sessions/<name>/transcript.json` — the demo page renders them verbatim (including the agent's self-corrections).

## Data

- `collisions_raw`: real NYC Motor Vehicle Collisions (Socrata dataset `h9gi-nx95`), fetched at build time.
- `legacy_export`: the same data after synthetic Excel-export abuse — 4 date formats, 488 exact duplicates, casing chaos, padded/float-coerced IDs, null-like sentinels. The agent discovers all of it.

## Notes

- The LLM endpoint is Ollama Cloud (OpenAI-compatible `/v1/chat/completions`). Any tool-calling model works; swap via `AGENT_MODEL` env var.
- Report files carry a content-validation gate because a real failure taught us: a model once shipped a title-only PDF. The gate rejects `body_md < 200 chars`, forcing a retry. The s1 transcript shows the rejection and the corrected retry — kept visible on purpose.
- Read-only SQL is enforced at the tool layer, not by prompt. The agent's cleaning session writes through `run_python` into a new table only, per its goal.