# Agent Sessions — the AI data analyst in operation

Eight real, unedited sessions of an AI agent with database access, replayable in the
browser: [agent-sessions/agent-demo.html](agent-demo.html) (self-contained, 8 reports
downloadable, charts and maps rendered inline).

## The engine

A tool-calling agent (the same architecture as [Hermes Agent](https://github.com/NousResearch/hermes-agent))
with **two interchangeable brains** — Ollama Cloud (glm-5.3) or **DeepSeek** (`deepseek-chat`)
via `AGENT_PROVIDER=deepseek` — and seven tools:

`run_sql` (read-only enforced) · `profile_table` · `run_python` · `forecast` (OLS trend
+ seasonality, 80% bands, seasonal-naive baseline always) · `web_search` (Google via Serper,
behind the portfolio's search proxy — a Cloudflare Worker holds the key, so it works with **no
key of your own**) · `web_read` (fetch +
read any URL — the browser's READ half; search + read = the full browse loop) · `make_chart`
(line/bar/grouped) · `make_map` (world choropleth, Natural Earth) · `make_report`
(PDF/Excel/Word; validates non-empty bodies; embeds charts)

## The sessions

| Session | Rows | The story it found |
|---|---|---|
| s1 Data-health audit | 20,488 messy | 100% of rows defective; duplicates inflate injuries +2.6%; "NULL" was a top borough |
| s2 Clean + reconcile | 20,488 → 14,104 | Zero deltas vs source; 6,384 quarantined, nothing dropped silently |
| s3 Forecast + risk | 60,000 | Naive baseline WON (4.95% vs 7.07% MAPE) — reported honestly |
| s4 NYC 311 operations | 22,542,090 | NYPD median closure 53 minutes vs HPD 41.8 days at P90; 1.48M timestamp defects |
| s5 Dutch fleet | 16,852,477 | 77× EV gap between new and old cohorts; flow-vs-stock story |
| s6 US census economics | 5,026,099 | Survey-weighted: $76.2K typical household; half of renters cost-burdened |
| s7 Chicago crime | 8,643,513 | Narcotics = enforcement signal (99.3% arrest); +226% vehicle-theft spike; July +33.2% |
| s8 Canada trade story (DeepSeek) | 1,818 | Quarter-century arc; **caught the UK gold-settlement artifact**; honest losing forecast |
| s9 Canada trade: VERIFIED edition | 1,818 | Same arc + **web-verified with cited sources** — gold anomaly confirmed (StatCan/Global Affairs/LBMA), every dip cited |
| s10 Mystery dataset bootstrap | 147 | **Unknown table, no hints**: profiled → hypothesized → browsed (3 searches, 4 pages read) → identified as NASA GISTEMP via fingerprint match → analyzed as climate data with cited causes |
| s11 Canada trade: CONTEXT edition | 1,818 | **The benchmark report**: Three-Question Test in action — the 2025-Q2 export break connected to the US tariff timeline (CUSMA review, 50% tariffs Aug 2026, C$27.6bn counter-tariffs), forecast benchmarked vs Bank of Canada & EDC, 7 pages read, 8 primary sources |

Scale tier: **74.8M rows** across 8 tables — see [big-data-profile.json](big-data-profile.json)
and [DATA-SOURCES.md](../DATA-SOURCES.md) at repo root (all public, licensed, traceable).

## Reproduce

```bash
cd agent
python3 -m venv venv && venv/bin/pip install requests pandas numpy openpyxl python-docx fpdf2 matplotlib
venv/bin/python build_database.py                                    # demo DB
AGENT_PROVIDER=deepseek venv/bin/python agent.py --db big_data.db \
  --session mysession --goal "Analyze can_trade and forecast exports"
```

DeepSeek needs `DEEPSEEK_API_KEY`; Ollama Cloud needs `OLLAMA_API_KEY` (in `~/.hermes/.env`).
`web_search` needs **no key at all**: it calls the portfolio's search proxy
(`https://northledger-insight-proxy.r-mdrashad97.workers.dev/search`), a Cloudflare Worker that
holds the Serper key and serves a capped number of searches a day (5 per visitor per minute,
300 a day for everyone). This is NorthLedger's own search path, separate from any other app's
Serper key. To use your own Serper key instead, set `SERPER_API_KEY` in this agent's
environment; to point elsewhere, set `SEARCH_PROXY_URL`.
