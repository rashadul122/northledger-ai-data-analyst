# NorthLedger Data Sources

Every dataset in this project is public, licensed for reuse, and traceable to its
official source. Nothing here is private or scraped. If you want the raw data
yourself, click through and download it — the same files this project ingests.

Last verified: September 21, 2026.

## Demo-tier database (client_data.db — the AI-agent demo)

| Dataset | What it is | Official source page | Download |
|---|---|---|---|
| NYC Motor Vehicle Collisions | ~60,000 recent crash records (demo subset of the full dataset): date, borough, injuries, contributing factors, vehicle types. Socrata API, refreshed continuously by NYC Open Data. | https://data.cityofnewyork.us/Public-Safety/Motor-Vehicle-Collisions-Crashes/qgea-i56i | `Export → CSV` on the source page |

The "legacy export" mess in the demo is synthetic: the same crash data after
deliberate Excel-export abuse (duplicates, mixed date formats, casing chaos) —
built by `agent/build_database.py` with a fixed seed, disclosed on the demo page.

## Scale-tier database (big_data.db — the multi-GB datasets)

| Dataset | Rows | What it is | Official source page | Direct download (the file we ingest) |
|---|---|---|---|---|
| **Chicago Crimes 2001–present** | 8,643,513 | Every reported crime incident: date, block, type, arrest, domestic, beat, ward, coordinates. | https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2 | https://data.cityofchicago.org/api/views/ijzp-q8t2/rows.csv?accessType=DOWNLOAD |
| **NYC 311 Service Requests 2010–present** | 22,542,090 | Every 311 request: agency, complaint type, dates, borough, status, resolution. | https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9 | https://data.cityofnewyork.us/api/views/erm2-nwe9/rows.csv?accessType=DOWNLOAD |
| **ACS PUMS 2023 1-Year — persons** | 3,405,809 | US census person-level microdata (287 vars: income, education, occupation, commute, age...). Survey-weighted (PWGTP). | https://www.census.gov/programs-surveys/acs/microdata/documentation.html | https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_pus.zip |
| **ACS PUMS 2023 1-Year — households** | 1,620,290 | US census household-level microdata (241 vars: income, rent, housing burden, vehicles...). Survey-weighted (WGTP). | same as above | https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_hus.zip |
| **Amazon Reviews 2023 — Video Games** | ~20,000,000 | Product reviews: rating, text, verified purchase, helpful votes, timestamps. Research corpus, McAuley Lab UCSD. | https://amazon-reviews-2023.github.io/ | https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Video_Games.jsonl.gz |
| **Amazon Reviews 2023 — Video Games metadata** | ~800,000 | Product catalog: price, brand, features, categories, ratings — pairs with reviews. | https://amazon-reviews-2023.github.io/ | https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Video_Games.jsonl.gz |
| **RDW Gekentekende voertuigen** (Dutch vehicle registry) | 16,852,477 | Every registered vehicle in the Netherlands: brand, model, fuel type, color, APK (inspection) dates, registration. CC-0. | https://opendata.rdw.nl/Voertuigen/Open-Data-RDW-Gekentekende-voertuigen/m9d7-ebf2 | https://opendata.rdw.nl/api/views/m9d7-ebf2/rows.csv?accessType=DOWNLOAD |

## Site demo (index.html — the forecast chart)

| Series | What it is | Source |
|---|---|---|
| US Advance Retail Sales (RSAFS) | Monthly retail sales, US$ millions. Fetched live at build time. | https://fred.stlouisfed.org/series/RSAFS |
| E-commerce % of retail (ECOMPCTSA) | Quarterly share of e-commerce in total retail. | https://fred.stlouisfed.org/series/ECOMPCTSA |

## Licenses

- NYC Open Data, Chicago Data Portal, RDW: open government data (RDW is CC-0;
  NYC/Chicago portals publish under their open-data terms — free use with attribution).
- ACS PUMS: US Census Bureau — public use microdata, free to use.
- Amazon Reviews 2023: released by McAuley Lab (UCSD) for research use.
- FRED: Federal Reserve Bank of St. Louis — free with attribution.

## Re-ingest any dataset

```bash
cd agent
venv/bin/python ingest_big_data.py --only chicago   # or nyc311 | pums-persons | pums-households
# (amazon + rdw loaders: see ingest_big_data.py jobs table)
```

Each ingest is chunked (50k rows per batch) — no dataset is ever loaded into RAM
whole. Row counts are verified against the official totals before any raw file
is removed; every raw file remains re-downloadable from the links above.