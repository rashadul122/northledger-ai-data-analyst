#!/usr/bin/env python3
"""Big-data tier ingestion: chunked profiling + loading of multi-GB public datasets
into the agent's SQLite scale database. NEVER loads a full file into RAM.

Handles:
  - Chicago Crimes (8.6M rows, event stream)
  - NYC 311 requests (22.5M rows, event stream)
  - ACS PUMS 2023 person + household records (survey microdata, weighted)

Outputs big_data.db (SQLite) + a profile.json the agent (and the demo page) can cite.

Run:  venv/bin/python ingest_big_data.py [--only chicago|nyc311|pums-persons|pums-households]
"""
import csv
import io
import json
import os
import re
import sqlite3
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.expanduser("~/data-analytics-portfolio/big-data")
DB = os.path.join(HERE, "big_data.db")
PROFILE = os.path.join(HERE, "big-data-profile.json")
CHUNK = 50_000

def connect():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    return con

def csv_cols(path, n=2):
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        return [h.strip() for h in next(r)]

def clean_col(h):
    return re.sub(r"[^a-z0-9]+", "_", h.lower().strip().strip('"')).strip("_")

def ingest_csv_stream(path, table, drop_cols=(), as_int=()):
    """Stream a CSV into SQLite in chunks; returns (rows, cols).
    Commits per chunk + journal_mode=DELETE so the journal never balloons."""
    con = connect()
    con.execute("PRAGMA wal_autocheckpoint=200")  # checkpoint WAL every 200 pages
    cols = csv_cols(path)
    keep = [i for i, c in enumerate(cols) if clean_col(c) not in drop_cols]
    names = [clean_col(cols[i]) for i in keep]
    types = ["INTEGER" if n in as_int else "TEXT" for n in names]
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute('CREATE TABLE "%s" (%s)' % (table, ", ".join(f'"{n}" {t}' for n, t in zip(names, types))))
    ins = 'INSERT INTO "%s" VALUES (%s)' % (table, ",".join("?" * len(names)))
    total = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        next(r)  # header
        buf = []
        def flush():
            nonlocal buf, total
            con.executemany(ins, buf)
            con.commit()  # per-chunk commit: journal stays small
            total += len(buf)
            buf = []
            if total % 500_000 < CHUNK:
                print(f"  {table}: {total:,} rows", flush=True)
        for row in r:
            buf.append(tuple(row[i] if i < len(row) else None for i in keep))
            if len(buf) >= CHUNK:
                flush()
        if buf:
            flush()
    con.commit()
    con.close()
    return total, names

def profile_table(con, table):
    cur = con.execute(f'SELECT COUNT(*) FROM "{table}"')
    n = cur.fetchone()[0]
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    return {"table": table, "rows": n, "columns": cols}

def ingest_pums_zip(zpath, table_prefix):
    """PUMS zips contain one CSV per state (or a single US file). Load every CSV inside."""
    con = connect()
    zf = zipfile.ZipFile(zpath)
    csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    total = 0
    names = None
    for n in csvs:
        with zf.open(n) as rawf:
            txt = io.TextIOWrapper(rawf, encoding="latin-1")
            r = csv.reader(txt)
            header = [clean_col(h) for h in next(r)]
            if names is None:
                names = header
                con.execute(f'DROP TABLE IF EXISTS "{table_prefix}"')
                con.execute('CREATE TABLE "%s" (%s)' % (
                    table_prefix, ", ".join(f'"{h}" TEXT' for h in header)))
                ins = 'INSERT INTO "%s" VALUES (%s)' % (table_prefix, ",".join("?" * len(header)))
            buf = []
            for row in r:
                buf.append(row)
                if len(buf) >= CHUNK:
                    con.executemany(ins, buf)
                    total += len(buf)
                    buf = []
                    if total % 1_000_000 < CHUNK:
                        print(f"  {table_prefix}: {total:,} rows", flush=True)
            if buf:
                con.executemany(ins, buf)
                total += len(buf)
    con.commit()
    con.close()
    return total, names or []

def ingest_jsonl_gz(path, table, fields):
    """Stream a .jsonl.gz (one JSON object per line) into SQLite; keeps only `fields`.
    Returns (rows, kept_fields)."""
    import gzip
    import json as _json
    con = connect()
    con.execute("PRAGMA wal_autocheckpoint=200")  # checkpoint WAL every 200 pages
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute('CREATE TABLE "%s" (%s)' % (table, ", ".join(f'"{n}" TEXT' for n in fields)))
    ins = 'INSERT INTO "%s" VALUES (%s)' % (table, ",".join("?" * len(fields)))
    total = 0
    buf = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = _json.loads(line)
            except _json.JSONDecodeError:
                continue  # malformed line: skip, count below via row check
            buf.append(tuple(str(o.get(k, "")) if o.get(k) is not None else "" for k in fields))
            if len(buf) >= CHUNK:
                con.executemany(ins, buf); con.commit()
                total += len(buf); buf = []
                if total % 1_000_000 < CHUNK:
                    print(f"  {table}: {total:,} rows", flush=True)
    if buf:
        con.executemany(ins, buf); con.commit(); total += len(buf)
    con.close()
    return total, list(fields)


# RDW: keep the analytics columns; skip ~30 verbose technical ones that triple the size
RDW_KEEP = ["Kenteken", "Voertuigsoort", "Merk", "Handelsbenaming", "Eerste Kleur",
            "Brandstof Omschrijving", "Aantal Zitplaatsen", "Vervaldatum APK",
            "Datum Eerste Toelating", "Datum Eerste Tenaamstelling", "Catalogusprijs",
            "Massa Ledig Voertuig", "Inrichting", "Aantal Cilinders", "Cilinderinhoud"]


def ingest_rdw(path, table):
    """Column-filtered ingest of the RDW registry: keeps RDW_KEEP only."""
    header = open(path, encoding="latin-1", errors="replace").readline().split(",")
    keep_idx = []
    names = []
    for i, h in enumerate(header):
        h = h.strip().strip('"')
        if h in RDW_KEEP:
            keep_idx.append(i)
            names.append(clean_col(h))
    if not keep_idx:
        raise RuntimeError(f"none of RDW_KEEP found in header: {header[:8]}...")
    con = connect()
    con.execute("PRAGMA wal_autocheckpoint=200")  # checkpoint WAL every 200 pages
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute('CREATE TABLE "%s" (%s)' % (table, ", ".join(f'"{n}" TEXT' for n in names)))
    ins = 'INSERT INTO "%s" VALUES (%s)' % (table, ",".join("?" * len(names)))
    total = 0
    import csv as _csv
    with open(path, newline="", encoding="latin-1", errors="replace") as f:
        r = _csv.reader(f)
        next(r)
        buf = []
        def flush():
            nonlocal buf, total
            con.executemany(ins, buf)
            con.commit()
            total += len(buf)
            buf = []
            if total % 1_000_000 < CHUNK:
                print(f"  {table}: {total:,} rows", flush=True)
        for row in r:
            buf.append(tuple(row[i] if i < len(row) else None for i in keep_idx))
            if len(buf) >= CHUNK:
                flush()
        if buf:
            flush()
    con.close()
    return total, names


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    out = {"generated": os.path.basename(DB), "datasets": []}
    con = connect()

    jobs = {
        "chicago": ("chicago-crimes.csv", "chicago_crimes"),
        "chicago-soda": ("chicago-crimes-soda.csv", "chicago_crimes"),
        "nyc311": ("nyc-311.csv", "nyc_311"),
        "pums-persons": ("acs-pums-2023-persons.zip", "pums_persons"),
        "pums-households": ("acs-pums-2023-households.zip", "pums_households"),
        "amazon-reviews": ("~/Downloads/Video_Games.jsonl.gz", "amazon_reviews_vg"),
        "amazon-meta": ("~/Downloads/meta_Video_Games.jsonl.gz", "amazon_meta_vg"),
        "rdw": ("~/Downloads/Open_Data_RDW__Gekentekende_voertuigen.csv", "rdw_vehicles"),
    }
    for key, (fname, table) in jobs.items():
        path = fname if fname.startswith("~") else os.path.join(DATA, fname)
        path = os.path.expanduser(path)
        if only and key != only:
            continue
        if not os.path.exists(path):
            print(f"skip {key}: {fname} not found")
            continue
        size_mb = os.path.getsize(path) / 1e6
        print(f"== {key}: {fname} ({size_mb:,.0f} MB) -> table {table}", flush=True)
        if fname.endswith(".zip"):
            total, names = ingest_pums_zip(path, table)
        elif fname.endswith(".jsonl.gz"):
            fields = ("rating", "title", "text", "asin", "parent_asin", "user_id",
                      "timestamp", "helpful_vote", "verified_purchase") \
                     if "meta" not in key else \
                     ("parent_asin", "title", "price", "average_rating", "rating_number",
                      "categories", "store", "main_category")
            total, names = ingest_jsonl_gz(path, table, fields)
        elif key == "rdw":
            total, names = ingest_rdw(path, table)
        else:
            total, names = ingest_csv_stream(path, table)
        info = profile_table(con, table)
        info["source_file"] = fname
        info["file_mb"] = round(size_mb, 0)
        out["datasets"].append(info)
        print(f"   -> {total:,} rows, {len(names)} columns", flush=True)

    con.close()
    # merge with any existing profile (accumulate across runs)
    prev = {}
    if os.path.exists(PROFILE):
        try:
            prev = json.load(open(PROFILE))
        except json.JSONDecodeError:
            prev = {}
    prev_tables = {d["table"]: d for d in prev.get("datasets", [])}
    for d in out["datasets"]:
        prev_tables[d["table"]] = d
    out["datasets"] = list(prev_tables.values())
    json.dump(out, open(PROFILE, "w"), indent=1)
    print("profile:", PROFILE)
    print(json.dumps(out, indent=1)[:1500])

if __name__ == "__main__":
    main()