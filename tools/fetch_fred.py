#!/usr/bin/env python3
"""Fetch FRED series as keyless fredgraph.csv files, keeping a last-good copy.

    python tools/fetch_fred.py              # RSAFSNA and RSAFS (the defaults)
    python tools/fetch_fred.py --offline    # do not fetch; re-record what is on disk

Each series lands at data/fred/<ID>.csv. A download replaces that file only when it
parses and looks like the series (right header, monthly dates, numeric values, no
shrink in history). Otherwise the previous file stays and the failure is recorded.
Every attempt is written to data/fred/SOURCES.json: URL, request time (UTC), HTTP
status, bytes, sha256, rows, first and last observation, and the outcome.

No API key is used. Nothing here runs on a schedule: it runs when someone runs it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRED_DIR = os.path.join(ROOT, "data", "fred")
SOURCES = os.path.join(FRED_DIR, "SOURCES.json")
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s"

# Source metadata, copied from each series' FRED page (the URL is recorded beside it).
# These are labels, not figures: every number comes from the downloaded file.
SERIES = {
    "RSAFSNA": {
        "title": "Advance Retail Sales: Retail Trade and Food Services",
        "label": "U.S. retail trade and food services",
        "units": "Millions of Dollars",
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "frequency": "Monthly",
        "page": "https://fred.stlouisfed.org/series/RSAFSNA",
        "source_agency": "U.S. Census Bureau, via FRED (Federal Reserve Bank of St. Louis)",
    },
    "RSAFS": {
        "title": "Advance Retail Sales: Retail Trade and Food Services",
        "label": "U.S. retail trade and food services, seasonally adjusted",
        "units": "Millions of Dollars",
        "seasonal_adjustment": "Seasonally Adjusted",
        "frequency": "Monthly",
        "page": "https://fred.stlouisfed.org/series/RSAFS",
        "source_agency": "U.S. Census Bureau, via FRED (Federal Reserve Bank of St. Louis)",
    },
}
DEFAULT = ["RSAFSNA", "RSAFS"]


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def parse(series_id: str, raw: bytes) -> list[tuple[str, float]]:
    """Parse a fredgraph.csv body; raise ValueError when it is not the series we asked for."""
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty file")
    header = [h.strip() for h in rows[0]]
    if len(header) != 2 or header[0].lower() not in ("observation_date", "date") or header[1] != series_id:
        raise ValueError("unexpected header %r" % (header,))
    out = []
    for r in rows[1:]:
        if not r:
            continue
        if len(r) != 2:
            raise ValueError("bad row %r" % (r,))
        d, v = r[0].strip(), r[1].strip()
        dt.date.fromisoformat(d)  # raises on a bad date
        if v in ("", "."):
            continue  # FRED marks a missing value with "."
        out.append((d[:7], float(v)))
    if len(out) < 24:
        raise ValueError("only %d observations" % len(out))
    months = [m for m, _ in out]
    if months != sorted(months) or len(set(months)) != len(months):
        raise ValueError("dates are not strictly increasing months")
    return out


def load(series_id: str, fred_dir: str = FRED_DIR) -> list[tuple[str, float]]:
    path = os.path.join(fred_dir, series_id + ".csv")
    with open(path, "rb") as f:
        return parse(series_id, f.read())


def describe(series_id: str, raw: bytes) -> dict:
    obs = parse(series_id, raw)
    return {
        "bytes": len(raw),
        "sha256": sha256(raw),
        "rows": len(obs),
        "first": {"month": obs[0][0], "value": obs[0][1]},
        "last": {"month": obs[-1][0], "value": obs[-1][1]},
    }


def fetch_one(series_id: str, fred_dir: str, opener=None, timeout: int = 30) -> dict:
    """Fetch one series; replace the cached file only if the new body validates."""
    url = URL % series_id
    path = os.path.join(fred_dir, series_id + ".csv")
    rec = {"series": series_id, "url": url, "requested_at": now_utc()}
    rec.update(SERIES.get(series_id, {}))
    old = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            old = f.read()
    try:
        # Default urllib User-Agent on purpose: FRED's CDN stalls requests carrying an
        # unfamiliar custom agent string (observed 2026-09-23: timeout vs HTTP 200 in 0.1 s).
        req = urllib.request.Request(url)
        op = opener or urllib.request.urlopen
        with op(req, timeout=timeout) as resp:
            body = resp.read()
            rec["http_status"] = getattr(resp, "status", None)
            rec["last_modified_header"] = resp.headers.get("Last-Modified") if hasattr(resp, "headers") else None
        rec["completed_at"] = now_utc()
        info = describe(series_id, body)          # raises ValueError if the body is not the series
        prev_rows = None
        if old is not None:
            try:
                prev_rows = len(parse(series_id, old))
            except ValueError:
                prev_rows = None                   # a broken cache never blocks a good download
        if prev_rows is not None and info["rows"] < prev_rows:
            raise ValueError("new file has fewer observations (%d) than the cached one (%d)"
                             % (info["rows"], prev_rows))
        fd, tmp = tempfile.mkstemp(dir=fred_dir, prefix="." + series_id + ".")
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.chmod(tmp, 0o644)                       # mkstemp creates 0600; the site serves these
        os.replace(tmp, path)
        rec.update(info)
        rec["outcome"] = "downloaded" if old is None else ("unchanged" if sha256(old) == info["sha256"] else "updated")
    except (urllib.error.URLError, OSError, ValueError) as e:
        rec["completed_at"] = now_utc()
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        if old is not None:
            rec.update(describe(series_id, old))
            rec["outcome"] = "fetch_failed_kept_last_good"
        else:
            rec["outcome"] = "fetch_failed_no_copy"
    return rec


def record_offline(series_id: str, fred_dir: str, previous: dict | None) -> dict:
    path = os.path.join(fred_dir, series_id + ".csv")
    with open(path, "rb") as f:
        raw = f.read()
    rec = dict(previous or {"series": series_id, "url": URL % series_id})
    rec.update(SERIES.get(series_id, {}))
    rec.update(describe(series_id, raw))
    rec["checked_offline_at"] = now_utc()
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("series", nargs="*", default=DEFAULT)
    ap.add_argument("--offline", action="store_true", help="do not fetch; re-describe the cached files")
    ap.add_argument("--dir", default=FRED_DIR)
    a = ap.parse_args(argv)
    os.makedirs(a.dir, exist_ok=True)
    src_path = os.path.join(a.dir, "SOURCES.json")
    book = {}
    if os.path.exists(src_path):
        with open(src_path) as f:
            book = json.load(f)
    book.setdefault("note", "FRED fredgraph.csv downloads (no API key). Written by tools/fetch_fred.py. "
                            "Nothing refreshes these files on a schedule.")
    book.setdefault("series", {})
    book.setdefault("history", [])
    bad = 0
    for sid in a.series:
        if a.offline:
            rec = record_offline(sid, a.dir, book["series"].get(sid))
        else:
            rec = fetch_one(sid, a.dir)
            book["history"].append({k: rec.get(k) for k in
                                    ("series", "url", "requested_at", "completed_at", "http_status",
                                     "bytes", "sha256", "rows", "outcome", "error") if k in rec})
            if rec["outcome"].startswith("fetch_failed"):
                bad += 1
        book["series"][sid] = rec
        print("%-8s %-28s rows=%s last=%s bytes=%s sha256=%s" % (
            sid, rec.get("outcome", "offline"), rec.get("rows"), (rec.get("last") or {}).get("month"),
            rec.get("bytes"), (rec.get("sha256") or "")[:16]))
    fd, tmp = tempfile.mkstemp(dir=a.dir, prefix=".SOURCES.")
    with os.fdopen(fd, "w") as f:
        json.dump(book, f, indent=1)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, src_path)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
