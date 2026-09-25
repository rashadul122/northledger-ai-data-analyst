# NorthLedger data sources

Every dataset used on this site is public and traceable to its official source. Licences differ by
dataset and are listed per row; one publisher (Amazon Reviews 2023) declares no licence, and that data
is used only as test input for the AI-analyst replays. Nothing here is private or scraped. Each file was
downloaded once, on purpose, and recorded; nothing on this site refreshes itself or runs on a schedule.

This file is written by `build.py` from `data/rentsafe_meta.json`, `data/fred/SOURCES.json` and the
replay manifest inside `agent-demo.html`, so its counts and checksums are the ones the page uses.
Built 2026-09-25 08:14:25 UTC.

## The main page: RentSafeTO report, scorecard and analyses

City of Toronto open data from the CKAN portal. Contains information licensed under the Open Government Licence – Toronto. ([the licence](https://open.toronto.ca/open-data-licence/).) This is independent analysis; it is not produced by, affiliated with or endorsed by the City of Toronto or RentSafeTO.

| Dataset | File | Retrieved (UTC) | Size | sha256, first 16 | Licence |
|---|---|---|---|---|---|
| Apartment Building Evaluation | [apartment-building-evaluations-2023-current.csv](https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/4ef82789-e038-44ef-a478-a8f3590c3eb1/resource/7fa98ab2-7412-43cd-9270-cb44dd75b573/download/apartment-building-evaluations-2023-current.csv) | 2026-09-23 06:40:59 UTC | 1.8 MB | `db9feec11327ca41` | Open Government Licence, Toronto |
| Apartment Building Evaluation | [pre-2023-apartment-building-evaluations.csv](https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/4ef82789-e038-44ef-a478-a8f3590c3eb1/resource/979fb513-5186-41e9-bb23-7b5cc6b89915/download/pre-2023-apartment-building-evaluations.csv) | 2026-09-23 06:40:59 UTC | 2.8 MB | `9ddfb28db7d35453` | Open Government Licence, Toronto |
| Apartment Building Registration | [apartment-building-registration-data.csv](https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/2b98b3f3-4f3a-42a4-a4e9-b44d3026595a/resource/97b8b7a4-baca-49c7-915d-335322dbcf95/download/apartment-building-registration-data.csv) | 2026-09-23 06:40:59 UTC | 1.6 MB | `9ca50a07cd567d68` | Open Government Licence, Toronto |
| City Wards | [city-wards-data-4326.geojson](https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/5e7a8234-f805-43ac-820f-03d7c360b588/resource/737b29e0-8329-4260-b6af-21555ab24f28/download/city-wards-data-4326.geojson) | 2026-09-23 06:41:00 UTC | 1.1 MB | `a35851f39c83e492` | Open Government Licence, Toronto, assumed: the catalogue entry names none |

## The Forecast Lab

One-off downloads by `tools/fetch_fred.py` (fredgraph.csv, no API key), recorded in `data/fred/SOURCES.json`.
Nothing refreshes them. FRED's terms for redistributing derived charts were not re-read for this build.

| Series | What it is | Source | Downloaded (UTC) | Source file dated | Months | Size | sha256, first 16 |
|---|---|---|---|---|---|---|---|
| [RSAFSNA](https://fred.stlouisfed.org/series/RSAFSNA) | U.S. retail trade and food services, millions of dollars, not seasonally adjusted | U.S. Census Bureau, via FRED (Federal Reserve Bank of St. Louis) | 2026-09-23 06:47:06 UTC | Wed, 16 Sep 2026 12:39:52 GMT | 416 (1992-01 to 2026-08) | 8 KB | `2bbe15d2fc830b6b` |
| [RSAFS](https://fred.stlouisfed.org/series/RSAFS) | U.S. retail trade and food services, seasonally adjusted, millions of dollars, seasonally adjusted | U.S. Census Bureau, via FRED (Federal Reserve Bank of St. Louis) | 2026-09-23 06:47:06 UTC | Wed, 16 Sep 2026 12:39:53 GMT | 416 (1992-01 to 2026-08) | 8 KB | `c57685eed78fe9d2` |

## The AI-analyst replays (agent-demo.html)

Demo database: NYC Motor Vehicle Collisions from NYC Open Data (https://data.cityofnewyork.us/Public-Safety/Motor-Vehicle-Collisions-Crashes/qgea-i56i), 60,000 crash records from 2025-09-25 to 2026-06-11, with a synthetic legacy-export layer (20,488 rows of deliberate duplicates, mixed date formats and casing) standing in for a client's messy copy. Licence: NYC Open Data terms, with attribution.

Scale-tier database, row counts as ingested (profiled 2026-09-22; 74,809,573 rows in all):

| Dataset | Rows | What it is | Official page | Direct download | Licence |
|---|---|---|---|---|---|
| Chicago Crimes 2001 to present | 8,643,513 | every reported crime incident: date, block, type, arrest, beat, ward | https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2 | https://data.cityofchicago.org/api/views/ijzp-q8t2/rows.csv?accessType=DOWNLOAD | City of Chicago open-data terms, with attribution |
| NYC 311 Service Requests 2010 to present | 22,542,090 | every 311 request: agency, complaint type, dates, borough, status | https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9 | https://data.cityofnewyork.us/api/views/erm2-nwe9/rows.csv?accessType=DOWNLOAD | NYC Open Data terms, with attribution |
| US Census ACS PUMS 2023 1-year, persons | 3,405,809 | person-level microdata, survey-weighted (PWGTP) | https://www.census.gov/programs-surveys/acs/microdata/documentation.html | https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_pus.zip | US Census Bureau public-use microdata |
| US Census ACS PUMS 2023 1-year, households | 1,620,290 | household-level microdata, survey-weighted (WGTP) | https://www.census.gov/programs-surveys/acs/microdata/documentation.html | https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_hus.zip | US Census Bureau public-use microdata |
| Amazon Reviews 2023, video games reviews | 4,624,615 | rating, text, verified purchase, helpful votes, time | https://amazon-reviews-2023.github.io/ | https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Video_Games.jsonl.gz | no licence declared by the publisher (McAuley Lab, UCSD); used only as test input |
| Amazon Reviews 2023, video games products | 137,269 | product catalogue: price, brand, categories, ratings | https://amazon-reviews-2023.github.io/ | https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Video_Games.jsonl.gz | no licence declared by the publisher (McAuley Lab, UCSD); used only as test input |
| RDW registered vehicles (Netherlands) | 16,852,477 | every registered vehicle: brand, model, colour, inspection dates | https://opendata.rdw.nl/Voertuigen/Open-Data-RDW-Gekentekende-voertuigen/m9d7-ebf2 | https://opendata.rdw.nl/api/views/m9d7-ebf2/rows.csv?accessType=DOWNLOAD | CC0 |
| RDW registered vehicles, fuel records (Netherlands) | 16,983,510 | fuel records that pair with the vehicle registry | https://opendata.rdw.nl/Voertuigen/Open-Data-RDW-Gekentekende-voertuigen/m9d7-ebf2 (same RDW open-data portal) | not recorded in this project | CC0 |

The NYC 311 session is archived on the replay page and kept apart from the PL-300 Power BI
project (AI-built, owner-directed), which uses the same dataset.
