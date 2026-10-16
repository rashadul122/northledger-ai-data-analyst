# Corpus benchmark: baseline scorecard

Run on 14 October 2026 with `tools/run_corpus.py --score --keep-flagged` semantics (as of 2026-10-01, native engine,
NL_ENGINE_ROOT = a northledger-core copy), against the engine as it stood at the end of that day. 26 datasets,
121 dataset facts (the `expect` lists in `tools/corpus.json`) plus the generic checks every file gets.
The facts were computed from the files with pandas, not taken from the engine's output.

**Total: 275/311 checks (88.4%). Dataset facts: 89/121 (73.6%).**
Coverage (files with 3 or more insights): ledger 3/3, panel 6/6, official 7/8 (fx_daily is refused), survey 0/2, cross_section 3/5.

| dataset | kind | score | facts | failures (one line each) |
|---|---|---|---|---|
| ledger_rentals | ledger | 12/14 | 4/6 | drill inside East York names a vendor (Don Valley HVAC), not Rent (+211,611 of +233,150); never says East York's units only start in 2025-03/04 (a building added, not growth) |
| ledger_orders | ledger | 12/12 | 4/4 | none |
| cpi_us | official_stats | 12/12 | 5/5 | none |
| global_temp | official_stats | 10/11 | 3/4 | headline pools GCAG and GISTEMP over a window to 2026-07 although GISTEMP ends 2025-12 (-0.0906 against GISTEMP's own -0.0925) |
| brent_monthly | official_stats | 10/12 | 3/5 | never names the record month (132.72 in 2008-07); forecast headline says "114.2 price for 2026-10" |
| gdp_panel | panel | 10/13 | 3/6 | ranks 2022, not 2023 (the latest year, which has US, China, Germany, Japan): misses Germany passing Japan; fastest grower should be Guyana (+262% to +303%) |
| population_panel | panel | 13/13 | 5/5 | none |
| co2_panel | panel | 11/13 | 4/5 | 364 s for one run (budget 30 s); the 2023-2024 change prints as +395.0 Mt with no percent (World +1.3%) |
| stocks_long | panel | 12/12 | 5/5 | none |
| fx_daily | official_stats | 7/7 | 3/3 | none (refused plainly: over 200,000 rows) |
| seattle_weather | sensor | 10/11 | 3/4 | never says what the weather mostly is (rain 43.9%, sun 43.8%) |
| flights_monthly | official_stats | 11/11 | 4/4 | none |
| steak_survey | survey | 10/12 | 2/4 | only the strongest answer pair; never reports the main answer (Medium rare, 38.4%); 2 insights |
| candy_ranking | cross_section | 11/11 | 4/4 | none |
| diamonds | cross_section | 8/11 | 2/4 | 83 s (budget 30 s); names x, not carat, as price's partner; misses the cut paradox (Ideal cheapest because smallest) |
| reviews | reviews | 9/10 | 2/3 | never names the most-reviewed brand (Nintendo, 251 answers) |
| car_sales | official_stats | 11/11 | 4/4 | none |
| drug_sales | official_stats | 12/12 | 5/5 | none |
| disasters_panel | panel | 11/12 | 4/5 | ranks only the latest year; misses drought as the deadliest type over the record (11.7 million) |
| sp500_monthly | official_stats | 11/12 | 4/5 | names the 2007 peak but not the 2009-02 trough (735) |
| airline_safety | cross_section | 9/13 | 1/5 | no 1985-99 against 2000-14 comparison (fatalities -50.6%); no Malaysia Airlines / Aeroflot; nothing per seat-km |
| himalaya_expeditions | ledger | 12/14 | 4/6 | never names Everest (privacy scan withholds peak_name by default); no success rate (79.5% with oxygen, 45.8% without) |
| superbowl_ads | cross_section | 10/13 | 2/5 | time axis is the YouTube upload date, not the Super Bowl year; no Doritos (most viewed) or Bud Light/Budweiser (most ads) |
| sp500_financials | cross_section | 11/13 | 3/5 | ranks share Price (NVR), not market cap (Nvidia, Apple, Alphabet); no sector share (Semiconductors 14.9%) |
| flying_survey | survey | 10/13 | 3/5 | "The strongest link ... is Is it rude" (question not quoted); no answer shares (58.8% say reclining is not rude); 2 insights |
| energy_panel | panel | 10/13 | 3/5 | 231 s (budget 30 s); primary measure is gdp, not energy: ranks China, US, India on gdp |

## Findings worth acting on

1. **Surveys are the weakest kind (0/2 with 3 insights).** The loop only cross-tabulates answer pairs; it never says
   what most people answered, which is the first thing an analyst reports.
2. **Primary-measure choice on wide panels.** energy_panel (130 columns) leads on `gdp`; sp500_financials ranks share
   price instead of market cap; superbowl_ads uses the upload date as its time axis instead of `year`.
3. **Latest-period choice.** gdp_panel ranks 2022 although 2023 has every large economy; global_temp mixes a source that
   stopped in 2025-12 into a window to 2026-07.
4. **Speed.** co2_panel (364 s), energy_panel (231 s) and diamonds (83 s) blow the 30 s per-run budget; the analyst loop
   itself stays inside its 6 s on every file, so the time is in the engine before it.
5. **Privacy scan false positives on public data** (recorded with the file run as a visitor would, then kept):
   population_panel `value` (World Bank population counts), co2_panel and energy_panel `population`, `gdp`;
   sp500_financials `name`, `market_cap`, `ebitda`; airline_safety `avail_seat_km_per_week`; himalaya_expeditions
   `peak_name` (mountain names); flying_survey four etiquette questions (arm rests, window shade, reclining obligation);
   superbowl_ads ad titles and descriptions. No withheld column reached the analyst block on any file.
6. Fixed during the day, and now guarded by the benchmark: himalaya_expeditions summed the `year` column
   ("The total of year rose ... +111%") and compared a partial 2019; seattle_weather printed percent changes of
   Celsius temperatures ("-70.2% from that peak", a seasonal gap of "+234%"); several panels printed counts in the
   "(N aggregate rows ...)" note that no step's ledger held.

## How to reproduce

    NL_ENGINE_ROOT=<northledger-core copy> python tools/run_corpus.py --score --keep-flagged --json score.json --min 0.85

The whole corpus takes about 25 minutes natively, most of it co2_panel and energy_panel (each run twice: once as
withheld, once kept). This baseline was produced by a read-only harness calling the same scoring functions
(`score_dataset`, `print_scorecard`) over the cached `.corpus/` files, because the session's command guard refused to
launch `run_corpus.py` itself from a subagent (it downloads and writes); rerun the command above from a main session
to confirm the numbers.
