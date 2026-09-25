#!/usr/bin/env python3
"""Build the demo database: REAL NYC Motor Vehicle Collisions + a deliberately-mangled
'legacy export' table (the client's messy copy). Two tables in one SQLite DB:
  - collisions_raw      : as fetched from Socrata (real nulls, real-world formatting)
  - legacy_export      : same data after classic Excel-export abuse (dupes, 4 date formats,
                         casing chaos, mixed typed numbers, trailing spaces, column rot)
Run:  ~/data-analytics-portfolio/agent-demo/venv/bin/python build_database.py
"""
import os, sqlite3, random, json, sys

import requests

random.seed(42)
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "client_data.db")

SODA = "https://data.cityofnewyork.us/resource/h9gi-nx95.json"
COLS = ["collision_id", "crash_date", "crash_time", "borough", "zip_code",
        "latitude", "longitude", "on_street_name", "cross_street_name",
        "number_of_persons_injured", "number_of_persons_killed",
        "contributing_factor_vehicle_1", "vehicle_type_code1", "vehicle_type_code2"]

def fetch_rows(total=60000, page=20000):
    rows, offset = [], 0
    while len(rows) < total:
        r = requests.get(SODA, params={
            "$select": ",".join(COLS), "$limit": page, "$offset": offset,
            "$order": "crash_date DESC"}, timeout=60)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        offset += page
        print(f"fetched {len(rows)}", flush=True)
        if len(batch) < page:
            break
    return rows[:total]

# ---------- date-format chaos for the legacy table ----------
MONTHS = {m: i for i, m in enumerate(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
def fmt_date_chaos(d):  # d like '2023-09-15T00:00:00.000'
    y, m, day = d[0:4], int(d[5:7]), int(d[8:10])
    style = random.random()
    if style < 0.30: return f"{m:02d}/{day:02d}/{y}"          # 09/15/2023
    if style < 0.55: return d                                 # ISO full
    if style < 0.80: return f"{day:02d}-{'JanFebMarAprMayJunJulAugSepOctNovDec'[(m-1)*3:(m-1)*3+3]}-{y[2:]}"  # 15-Sep-23
    return f"{y}-{m:02d}-{day:02d}"                           # 2023-09-15

def fmt_time_chaos(t):  # t like '14:30'
    try:
        hh, mm = int(t.split(":")[0]), int(t.split(":")[1])
    except Exception:
        return t
    style = random.random()
    if style < 0.5: return t
    if style < 0.75:
        ampm = "AM" if hh < 12 else "PM"
        h12 = hh if 1 <= hh <= 12 else (hh - 12 if hh > 12 else 12)
        return f"{h12}:{mm:02d} {ampm}"
    return f"{hh:02d}{mm:02d}"  # 1430

def borough_chaos(b):
    if not b: return b
    style = random.random()
    if style < 0.35: return b.upper()
    if style < 0.60: return b.lower()
    if style < 0.85: return " " + b + " "
    return b.upper() + "  "

def num_chaos(v, nulls=True):
    if v in (None, ""):
        return random.choice(["", "N/A", "0"]) if nulls else ""
    style = random.random()
    if style < 0.6: return str(v)
    if style < 0.8: return f"{float(v):.1f}" if str(v).replace('.','').isdigit() else str(v)
    if style < 0.9: return " " + str(v) + " "
    return str(v) + ".0" if not str(v).endswith(".0") else str(v)

def veh_chaos(v):
    if not v: return v
    style = random.random()
    if style < 0.3: return v.upper()
    if style < 0.55: return v.lower()
    if style < 0.7 and v.lower().startswith("sedan"): return "4 dr sedan"
    return v

def factor_chaos(v):
    if not v: return v
    return v.upper() if random.random() < 0.3 else v

def main():
    rows = fetch_rows(60000)
    print("total fetched:", len(rows))
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # ---------- table 1: collisions_raw (as-fetched) ----------
    cur.execute("""CREATE TABLE collisions_raw (
        collision_id INTEGER, crash_date TEXT, crash_time TEXT, borough TEXT,
        zip_code TEXT, latitude TEXT, longitude TEXT, on_street_name TEXT,
        cross_street_name TEXT, number_of_persons_injured TEXT,
        number_of_persons_killed TEXT, contributing_factor_vehicle_1 TEXT,
        vehicle_type_code1 TEXT, vehicle_type_code2 TEXT)""")
    clean_rows = [tuple(r.get(c) for c in COLS) for r in rows]
    cur.executemany("INSERT INTO collisions_raw VALUES (" + ",".join("?"*14) + ")", clean_rows)

    # ---------- table 2: legacy_export (the client's abused copy) ----------
    cur.execute("""CREATE TABLE "legacy_export" (
        "Collision ID" TEXT, "CRASH DATE" TEXT, "Crash Time" TEXT, "Boro" TEXT,
        "Zip " TEXT, "LATITUDE" TEXT, "LONGITUDE " TEXT, "On Street" TEXT,
        "number of persons injured" TEXT, "number of persons killed" TEXT,
        "CONTRIBUTING FACTOR" TEXT, "Vehicle Type 1" TEXT, "Vehicle Type 2" TEXT)""")
    sample = rows[:20000]
    messy = []
    for r in sample:
        messy.append((
            num_chaos(r.get("collision_id")),
            fmt_date_chaos(r["crash_date"]) if r.get("crash_date") else "",
            fmt_time_chaos(r["crash_time"]) if r.get("crash_time") else "",
            borough_chaos(r.get("borough")),
            num_chaos(r.get("zip_code")),
            r.get("latitude") or "",
            r.get("longitude") or "",
            r.get("on_street_name") or "",
            num_chaos(r.get("number_of_persons_injured")),
            num_chaos(r.get("number_of_persons_killed")),
            factor_chaos(r.get("contributing_factor_vehicle_1")),
            veh_chaos(r.get("vehicle_type_code1")),
            veh_chaos(r.get("vehicle_type_code2")),
        ))
    # exact duplicates: append copies of ~2.5% of rows
    dups = [row for row in messy if random.random() < 0.025]
    messy.extend(dups)
    random.shuffle(messy)
    cur.executemany("INSERT INTO \"legacy_export\" VALUES (" + ",".join("?"*13) + ")", messy)

    con.commit()
    for t in ("collisions_raw", "legacy_export"):
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(t, n, "rows")
    con.close()
    print("DB at", DB, f"({os.path.getsize(DB)/1e6:.1f} MB)")

if __name__ == "__main__":
    main()