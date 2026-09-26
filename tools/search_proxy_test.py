#!/usr/bin/env python3
"""Search-proxy test runner + persistent test log (NorthLedger insight-proxy POST /search).

Usage:
  python3 search_proxy_test.py --n 10            # 10 timed searches, appended to the log
  python3 search_proxy_test.py --report          # print the log's efficiency report

The log is data/search_proxy_log.jsonl (one JSON record per request). Every request
is timed end to end (the same wall clock a distant user's agent feels), the reply
shape is validated against the agent tool contract, and failures keep their error
code. Nothing in the log holds the Serper key.
"""
import argparse
import datetime as dt
import json
import os
import statistics
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "search_proxy_log.jsonl")
URL = os.environ.get(
    "SEARCH_PROXY_URL",
    "https://northledger-insight-proxy.r-mdrashad97.workers.dev/search",
)
# The same client a distant user's agent.py uses (requests). Cloudflare's bot filter
# blocks the bare Python-urllib signature (error 1010), so the runner sends the exact
# headers agent.py sends, which the worker serves.
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) data-analyst-agent/1.0"

QUERIES = [
    "NASA GISTEMP global temperature dataset",
    "Bank of Canada policy rate decision September 2026",
    "StatCan Canadian merchandise exports Q2 2025",
    "CUSMA review 2026 tariff timeline Canada",
    "NYC 311 service requests annual report",
    "Netherlands RDW vehicle fleet electrification share",
    "US Census ACS PUMS household income methodology",
    "LBMA gold price settlement history",
    "Chicago crime data portal annual trends",
    "UN comtrade Canada imports classification",
    "Toronto RentSafeTO apartment building evaluations",
    "OECD leading indicators Canada August 2026",
]


def one_request(q, num, timeout=40):
    """One timed POST /search through the same library agent.py uses. Returns the
    record for the log (never the key)."""
    t0 = time.perf_counter()
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "q": q, "num": num}
    try:
        r = requests.post(
            URL,
            headers={"Content-Type": "application/json", "User-Agent": UA},
            json={"q": q, "num": num},
            timeout=timeout,
        )
        code = r.status_code
        try:
            payload = r.json()
        except ValueError:
            payload = {"error": f"non-json: {r.text[:60]}"}
    except requests.RequestException as e:
        code = 0
        payload = {"error": f"network: {type(e).__name__}"}
    rec["seconds"] = round(time.perf_counter() - t0, 3)
    rec["status"] = code
    if code == 200:
        results = payload.get("results", [])
        rec["ok"] = True
        rec["n_results"] = len(results)
        rec["bytes"] = len(json.dumps(payload))
        rec["answer_box"] = "answer_box" in payload
        rec["knowledge_graph"] = "knowledge_graph" in payload
        rec["shape_valid"] = (
            isinstance(payload.get("query"), str)
            and isinstance(results, list)
            and all(
                isinstance(x.get("title"), str)
                and isinstance(x.get("link"), str)
                and x.get("link", "").startswith("http")
                and isinstance(x.get("snippet"), str)
                for x in results
            )
        )
        if not rec["shape_valid"]:
            rec["note"] = "reply failed the agent tool shape contract"
    else:
        rec["ok"] = False
        rec["error"] = str(payload.get("error", code))[:80]
    return rec


def run_tests(n, num=5):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    recs = []
    for i in range(n):
        q = QUERIES[i % len(QUERIES)]
        r = one_request(q, num)
        recs.append(r)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        mark = "OK " if r["ok"] else "ERR"
        print(f"[{i+1:2d}/{n}] {mark} {r['seconds']:6.2f}s  {r['status']}  {r['q'][:48]}")
    print(f"\nAppended {len(recs)} records to {LOG}")
    report()


def load_log():
    recs = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return recs


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    frac = k - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 3)


def report():
    recs = load_log()
    if not recs:
        print("No records in the log yet. Run with --n first.")
        return
    ok = [r for r in recs if r.get("ok")]
    err = [r for r in recs if not r.get("ok")]
    s_all = sorted(r["seconds"] for r in recs)
    s_ok = sorted(r["seconds"] for r in ok)
    days = {}
    for r in recs:
        days.setdefault(r["ts"][:10], [0, 0])
        days[r["ts"][:10]][0] += 1
        days[r["ts"][:10]][1] += 1 if r.get("ok") else 0
    print("=" * 62)
    print(f"SEARCH-PROXY TEST LOG  ({len(recs)} requests, log: data/search_proxy_log.jsonl)")
    print(f"endpoint: {URL}")
    print("=" * 62)
    print(f"success rate       : {len(ok)}/{len(recs)} ({100*len(ok)/len(recs):.1f}%)")
    if ok:
        print(f"latency ALL (s)    : median {statistics.median(s_ok):.2f}  "
              f"mean {statistics.mean(s_ok):.2f}  p90 {pct(s_ok, 0.9):.2f}  "
              f"p99 {pct(s_ok, 0.99):.2f}  max {max(s_ok):.2f}")
        print(f"latency OK only    : min {min(s_ok):.2f}")
        shapes = sum(1 for r in ok if r.get("shape_valid"))
        print(f"tool-shape valid   : {shapes}/{len(ok)}")
        print(f"avg results/query  : {sum(r.get('n_results',0) for r in ok)/len(ok):.1f}")
        ab = sum(1 for r in ok if r.get("answer_box"))
        kg = sum(1 for r in ok if r.get("knowledge_graph"))
        print(f"answer_box seen    : {ab}/{len(ok)}   knowledge_graph seen: {kg}/{len(ok)}")
        avg_bytes = sum(r.get("bytes", 0) for r in ok) / len(ok)
        print(f"avg reply size     : {avg_bytes/1024:.1f} KB")
    if err:
        print(f"failures           : {len(err)}")
        bycode = {}
        for r in err:
            bycode[r.get("error", "?")] = bycode.get(r.get("error", "?"), 0) + 1
        for code, n in sorted(bycode.items(), key=lambda kv: -kv[1]):
            print(f"  {code}: {n}")
    print(f"per-day            : " + "  ".join(f"{d}:{n}/{t}" for d, (t, n) in sorted(days.items())))
    print(f"budget             : searches spent per UTC day are capped at 300 by the worker")
    print("=" * 62)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--num", type=int, default=5)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    else:
        run_tests(a.n, a.num)
