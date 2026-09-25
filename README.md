# NorthLedger Insights: portfolio website

A static site: `index.html` (one file with its data inside it) and `agent-demo.html` (replays of
recorded AI-analyst sessions), with the data behind them in `data/`. The page makes no outside
request while someone reads it. The one exception starts when a visitor starts the "Try it on
your own file" demo: its worker (`engine/worker.js`) fetches Pyodide from cdn.jsdelivr.net and the
packed engine (`engine/northledger-browser.zip`) from this site, so the demo does not work
offline. With `ai_proxy_url` set, the page also sends the findings and the story to the owner's
AI proxy, only after the visitor ticks consent. `tools/check_site.py` encodes both exceptions.

Nothing on this site runs on a schedule, fetches data when someone visits, or updates itself.
Every dataset is a snapshot someone downloaded on purpose, and each download is recorded with
its URL, time, size and sha256. If a scheduled rebuild is ever set up (see "Freshness words"
below), the page may say so, and not before.

## Status

- **`index.html` is the v2 page**, assembled by `build.py` from `src/` and `data/`:
  - the RentSafeTO report, scorecard and Analyses A, B and D (`build_rentsafe.py` reads the City of
    Toronto files in `../data/rentsafe` and writes `data/rentsafe_*.json`);
  - the Forecast Lab, Analysis C (`build_forecast.py` models FRED RSAFSNA, U.S. retail trade and food
    services, not seasonally adjusted, with six methods, a rolling-origin backtest and bands sized
    from past out-of-sample errors; everything it shows is in `data/forecast_lab.json`);
  - the NorthLedger engine run on the City's files (`data/timings.json`, `data/engine_scorecard.json`,
    written by `../engagements/rentsafe/build_engine_scorecard.py`), with its exec brief, full brief,
    evidence ledger and Power BI project copied into `downloads/` by `build.py` after a leak scan.
- **`agent-demo.html`** is rebuilt by `tools/sanitize_agent_demo.py` from the replay page in
  `../agent-demo/` (see below). `DATA-SOURCES.md`, `case-study.html` (the page the site links to,
  styled like it) and `case-study.md` are written by `build.py` from `src/case-study.template.md`.
- **Still needed from the owner:** a contact route in `site.config.json` (`contact_email` and/or
  `booking_url`; until then the page says "Contact details coming" and `verify.sh`'s contact check
  fails on purpose), then pushing the repository and turning on GitHub Pages.

## Rebuild

Run from this folder. `PY` is the project's Python 3.9 environment (numpy and pandas; no scipy,
scikit-learn or statsmodels).

```bash
PY=../agent-demo/venv/bin/python

$PY build_rentsafe.py                # City files -> data/rentsafe_*.json
$PY build_rentsafe.py --check        # rebuild into a temp folder and run the invariant checks
                                     #   (published folder: add --input-dir <folder of the 4 City files>)
$PY tools/fetch_fred.py              # download RSAFSNA and RSAFS (fredgraph.csv, no API key)
                                     #   -> data/fred/<ID>.csv + data/fred/SOURCES.json
                                     #   a bad or shorter download keeps the last good copy
$PY build_forecast.py                # -> data/forecast_lab.json (no network; about 3 s)
$PY build_forecast.py --check        # recompute and compare with the file on disk
(cd ../engagements/rentsafe && ../../agent-demo/venv/bin/python build_engine_scorecard.py)
                                     # -> data/timings.json, data/engine_scorecard.json
$PY tools/sanitize_agent_demo.py     # ../agent-demo/agent-demo.html -> agent-demo.html
$PY tools/sanitize_agent_demo.py --check
python3 build.py                     # inline src/ + data into index.html; DATA-SOURCES.md,
                                     #   case-study.html + .md, data/site_build.json, downloads/
python3 build.py --tests             # also re-record data/engine_tests.json (the build fails when
                                     #   that receipt or timings.json is for older engine code)
./verify.sh                          # the full battery (see below)
```

`tools/fetch_fred.py --offline` re-describes the files already on disk without fetching.

## What `verify.sh` checks

It reads the site and writes only to a scratch folder (`VERIFY_TMP`, default a fresh temp folder)
and the QA folder (`QA_OUT`, default `$TMPDIR/northledger-qa`, outside the site so nothing it
writes can be published). `SKIP_VISUAL=1` skips the screenshots.

| Step | Passes when |
|---|---|
| forecast | `data/forecast_lab.json` reproduces exactly from `data/fred/*.csv` |
| replays | `agent-demo.html` is exactly what the sanitizer builds (skipped if `../agent-demo` is absent) |
| render | headless Chrome renders a copy of `index.html` with `test-driver.html` injected; no JS errors, a `<main>` landmark, every non-decorative SVG has `role="img"` and a name, no sideways scroll, no em dash |
| honesty | `tools/check_site.py`: banned freshness words (pages and served Markdown) and em dashes in editorial page text, including the replay page's card text and editor's notes (quoted transcripts are exempt), figures equal their JSON, no local paths or environment (dot-env) file names in anything served (embedded and served PDF/DOCX/XLSX included), links resolve under a GitHub Pages project path, a real contact route is configured and linked, and no stray file (a `qa/` folder, a PDF at the root, a test page) sits in the folder unlisted in `.gitignore` |
| sweep | no TODO/lorem, no em dash, wordmark present |
| ui | `tools/check_ui.js` (Node and Playwright, driving headless Chrome on the local file, no server): filters and Esc, the ward drill under filters, shared filter links, sorting, focus after removing a chip, the drawer, chart label collisions, phone layouts at 320 to 390 px, touch-target sizes, dark-theme legends, accessible names, the Model drawer fitting its width, the PBIP preview opening in view on a phone, the single-ward rank, the small-cell rule in the risk chart, and on the replay page: switching sessions plays only the chosen one, and each replay link opens the session it names. Set `PLAYWRIGHT_MODULE` to the Playwright package folder; without it the step is skipped |
| visual | full-page and narrow screenshots plus a print PDF land in `QA_OUT` (a 420 px Chrome window is not a phone; the ui step covers real phone widths) |
| print | `tools/check_print.py` reads that print PDF page by page with the poppler tools (`pdftotext`, `pdftoppm`; skipped without them): the hero is on page 1, no page but the last is nearly empty, and no page ends on a chart's caption with the chart on the next |

## Contracts for page authors

- **Bind every figure to its JSON.** `<span data-fact="forecast_lab:display.champion_mape">1.27%</span>`
  means "this text must equal `display.champion_mape` in `data/forecast_lab.json`". The part before
  the colon is a file in `data/` without `.json` (or `demo-data`); the path uses dots, with numbers
  for list positions. A string value is compared as it is. A number needs `data-fmt`: `int`,
  `fixed:N`, `pct:N`, `spct:N` (signed), `usd:N`, `usd_m:N`, `usd_b:N` (value in US$ millions shown
  as billions). Prefer the ready-made strings under `display` in `forecast_lab.json`.
- **Write the numbers into the built HTML.** A bound element that is empty until JavaScript runs
  fails, because the page must read with JavaScript off.
- **`data-fact-scope`** on a section makes any digit inside it that is not in a `data-fact`
  element a failure. `data-fact-exempt="<reason>"` opts out a node that holds a constant, not data.
- **`data-honesty-exempt="<reason>"`** excludes quoted third-party text (for example the agent
  transcripts) from the banned-word scan. A blank reason fails.
- **Contact.** `site.config.json` needs `contact_email` and/or `booking_url` (https), not a
  placeholder, and `index.html` must link it (`build.py` renders both). The owner creates any
  booking account.
- **Links.** Relative links only (`index.html`, never `../index.html` or `/index.html`), because
  the planned address is a project path (`https://rashadul122.github.io/northledger-ai-data-analyst/`).
- **Replay teaser.** `agent-demo.html` carries `<script type="application/json" id="demo-manifest">`
  with the session list, `featured` flags (the NYC 311 session is archived and `featured: false`),
  counts and the audit numbers. A teaser should read its counts from there.

## Freshness words

"live", "real-time", "every morning" and "daily" fail the check unless a GitHub Actions workflow
with a `schedule:` cron exists **and** `data/refresh_stamp.json` records a completed run of it
(`workflow`, `run_id`, `completed_at`). Even then "real-time" always fails, and "daily" or
"every morning" pass only for a daily cron. Nothing here schedules anything.

## The Forecast Lab in brief

- Series: RSAFSNA, monthly, 1992 onward; RSAFS (seasonally adjusted) is used for the headline
  month-over-month figure, the Census seasonal factors comparison and the v1 correction.
- Methods, all on log levels and refit at every origin: naive, seasonal naive, drift, seasonal
  naive with drift, damped Holt on the deseasonalised log, log-linear trend plus month effects
  (anchored on its last residuals).
- March 2020 to June 2021 is treated as missing whenever a model estimates anything; no scored
  forecast starts from, looks back into or lands in that window.
- Backtest: 24 origins by horizons 1 to 12; MAE, MAPE and MASE per horizon. The champion is the
  lowest out-of-sample MASE, and the page says so plainly when a simple baseline wins.
- 80% bands: 10th and 90th percentiles of the champion's past log errors at each horizon. Their
  coverage in the backtest is reported as k of n with a Wilson interval, with the caveat that
  overlapping forecasts are not independent.
- Limits (also in the JSON): revised history rather than first releases, no trading-day or
  holiday effects, nominal dollars, one economic period, not investment advice.

Tests: `../northledger-core/tests/test_site_forecast_lab.py` and `test_site_checks.py` run with
the engine's `./run_tests.sh`.

## The replay page

`tools/sanitize_agent_demo.py` rebuilds `agent-demo.html` from `../agent-demo/agent-demo.html`
(written by `../agent-demo/build_page.py`, which it never modifies). It redacts local file paths
and the directory listing that exposed the environment (dot-env) file, adds editor's notes marked as added after the
session (early empty-query failures, a misread start date, an unsupported "so trust the model",
and the Dutch fleet total the self-audit found wrong, recomputed from the transcript), archives the
NYC 311 session away from the showcase, fixes the back link, regenerates the counts from the
database profile, the demo database and the audit reports, and states that the sessions call a
remote model and that the Python tool is not sandboxed. It also patches the replay script: a new
session (card, Replay, or `agent-demo.html#<session>` link) first stops the one playing, and the
page plays the session its URL names (the first one otherwise). Card titles and blurbs carry no em
dash; the recorded prompts and answers stay as recorded.

## Deploy to GitHub Pages (owner only; not done)

Nothing has been published. `.gitignore` keeps the owner's planning PDF, the retired
`demo-data.json` and any `qa/` output out of the repository; `verify.sh`'s stray check fails if
such a file appears unlisted. `.nojekyll` is in place so the Markdown files are served as is.

```bash
gh auth login
git init -b main && git add . && git commit -m "NorthLedger Insights portfolio site"
gh repo create rashadul122/northledger-ai-data-analyst --public --source=. --push
# then enable Pages (Settings > Pages > deploy from branch main, root)
```

## Data and licences

- City of Toronto open data (Open Government Licence, Toronto) for the report, scorecard and
  analyses; the site is independent analysis, not produced by, affiliated with or endorsed by the
  City of Toronto or RentSafeTO.
- FRED series RSAFSNA and RSAFS (U.S. Census Bureau via the Federal Reserve Bank of St. Louis):
  https://fred.stlouisfed.org/series/RSAFSNA and https://fred.stlouisfed.org/series/RSAFS.
  FRED's terms for redistributing derived charts have not been re-read for this build [unverified].
- Every dataset, with its download record and licence: `DATA-SOURCES.md` (written by `build.py`).
  The handling process: `DATA-MANAGEMENT.md`.
- This site and its tooling were built with AI assistance (Hermes Agent with GLM, and Claude).

## Agent Sessions — the AI data analyst at scale

Eight real, unedited sessions of a tool-calling AI data analyst (DeepSeek / Ollama brains)
against a 74.8M-row public-data tier — profile-first auditing, quarantine-not-drop cleaning,
honest forecasts that lose to baselines when they should, charts and world maps, and reports
written in-session: [agent-sessions/](agent-sessions/) · [replay the sessions](agent-sessions/agent-demo.html)

