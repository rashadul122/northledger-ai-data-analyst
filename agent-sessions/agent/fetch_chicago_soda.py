#!/usr/bin/env python3
"""Fetch all 8,643,513 Chicago crime rows via the official Socrata API (50k pages),
writing one clean CSV. Avoids the flaky bulk-export stream entirely.
Same official source: data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2
"""
import os
import sys
import time

import requests

OUT = os.path.expanduser("~/data-analytics-portfolio/big-data/chicago-crimes-soda.csv")
ENDPOINT = "https://data.cityofchicago.org/resource/ijzp-q8t2.csv"
PAGE = 50_000
TOTAL = 8_643_513

def main():
    s = requests.Session()
    wrote = 0
    offset = 0
    with open(OUT, "w") as f:
        while offset < TOTAL:
            for attempt in range(6):
                try:
                    r = s.get(ENDPOINT, params={"$limit": PAGE, "$offset": offset, "$order": ":id"},
                              timeout=120)
                    if r.status_code == 200 and r.text.strip():
                        break
                    print(f"  offset {offset}: HTTP {r.status_code}, retry {attempt+1}", flush=True)
                except requests.RequestException as e:
                    print(f"  offset {offset}: {type(e).__name__}, retry {attempt+1}", flush=True)
                time.sleep(3 * (attempt + 1))
            else:
                print(f"FATAL: could not fetch offset {offset}")
                sys.exit(1)
            lines = r.text.splitlines()
            if offset == 0:
                f.write(lines[0] + "\n")  # header once
                body = lines[1:]
            else:
                body = lines[1:]  # skip repeated header
            f.write("\n".join(body) + "\n")
            wrote += len(body)
            if wrote % 500_000 < PAGE:
                print(f"  {wrote:,} rows", flush=True)
            offset += PAGE
            time.sleep(0.7)  # be polite to the API
    print(f"DONE: {wrote:,} rows written to {OUT}")
    if wrote < TOTAL - 5_000:  # small slack for deleted rows since the count snapshot
        print(f"WARNING: expected ~{TOTAL:,}, got {wrote:,}")

if __name__ == "__main__":
    main()