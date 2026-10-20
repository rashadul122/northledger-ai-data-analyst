#!/usr/bin/env python3
"""
check_ai_report: the AI path's report scorecard (AI Analyst v2 phase G, 20 Oct 2026; plan/AI-ANALYST-V2-PLAN.md §6). For each file it
does what the page does after the visitor's acknowledgement: the engine's profile (with its row sample and shape) to the worker's
POST /plan, the engine run with that plan, the engine's results_for_ai to POST /report (the page's own body: objective, results,
context_queries), and it scores the report the AI wrote, deterministically (no AI judge):

  * structure: a title line, "## Executive summary", "## The headline: ...", "## What to do", "## Risks ..." present;
  * confidence: the headline section carries a "Confidence:" line and every confidence line is High, Medium, Low or "Described, not
    tested" (the worker calibrates them to the engine's grades);
  * honesty: the worker's guard's count of removed sentences (repaired), reported, never hidden;
  * visuals: the [VISUAL:id] markers placed, every one an id the results hold;
  * forecast: a yearly or quarterly series with the engine's forecast states its back-test as the results write it;
  * golden facts: the figures a senior analyst would state for the file (tools/fixtures/ai-report-golden.json, by file name), each a
    regular expression the report must match.

    ../agent-demo/venv/bin/python tools/check_ai_report.py --worker https://<worker>/ FILE.csv [FILE.csv ...] [--out DIR]

Each file is 2 AI calls (plan, report) on the worker's daily budget: run on purpose, never in verify.sh. Writes OUT/ai_report.json and
the reports' texts, prints one line per file, and exits 1 when a file misses a structural check or a golden fact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
ORIGIN = "https://northledger.halosyncs.com"
GOLDEN = os.path.join(HERE, "fixtures", "ai-report-golden.json")
CONF_RX = re.compile(r"^Confidence:\s*(High|Medium|Low|Described, not tested)\b", re.M)
ANY_CONF_RX = re.compile(r"^Confidence:\s*(\S[^(]*)", re.M)


def post(url: str, body: dict, timeout: int = 330) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": ORIGIN,
                                          # Cloudflare refuses Python's own user agent (error 1010) before the worker sees it
                                          "User-Agent": "NorthLedger-eval/1.0 (+https://northledger.halosyncs.com)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return dict(json.loads(exc.read().decode() or "{}"), status=exc.code)
        except Exception:  # noqa: BLE001
            return {"error": "http_%d" % exc.code}
    except Exception as exc:  # noqa: BLE001 - a timeout or a dropped connection is a result too
        return {"error": type(exc).__name__}


def section(text: str, head_rx: str) -> str:
    m = re.search(r"^##\s*" + head_rx + r".*?$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    return m.group(1) if m else ""


def score(text: str, results: dict, golden: list) -> dict:
    lines = [l for l in text.split("\n") if l.strip()]
    heads = [l.strip() for l in lines if l.startswith("## ")]
    want = {"executive summary": r"## Executive summary", "headline": r"## The headline\b", "what to do": r"## What to do",
            "risks": r"## Risks"}
    bare = [h for h in heads if re.match(r"## The headline\s*:?\s*$", h, re.I)]
    missing = [k for k, rx in want.items() if not any(re.match(rx, h, re.I) for h in heads)]
    title_ok = bool(lines) and not lines[0].startswith(("#", "-", "|", "["))
    head_sec = section(text, r"The headline\b")
    confs = ANY_CONF_RX.findall(text)
    bad_conf = [c.strip() for c in confs if not re.match(r"(High|Medium|Low|Described, not tested)\b", c.strip())]
    ids = {v.get("id") for v in results.get("visuals") or [] if isinstance(v, dict)}
    placed = re.findall(r"^\[VISUAL:([a-z][a-z0-9_]{0,23})\]\s*$", text, re.M)
    unknown = [p for p in placed if p not in ids]
    pf = results.get("period_forecast") if isinstance(results.get("period_forecast"), dict) else None
    fc_ok = None
    if pf:
        lab = str(pf.get("audit_label") or "")
        fc_ok = bool(lab) and lab in text
    hits, misses = [], []
    for g in golden:
        (hits if re.search(g["rx"], text, re.I | re.S) else misses).append(g["fact"])
    problems = (["missing section: " + m for m in missing] + ([] if title_ok else ["no title line"]) +
                (["the headline heading names no finding"] if bare else []) +
                ([] if CONF_RX.search(head_sec or "") else ["the headline has no confidence line"]) +
                ["a confidence level outside the four: " + b for b in bad_conf] +
                ["a visual the results do not hold: " + u for u in unknown] +
                (["the forecast's back-test is not stated"] if fc_ok is False else []) +
                ["golden fact missed: " + m for m in misses])
    return {"sections": heads, "title_ok": title_ok, "confidence_lines": len(confs), "visuals_available": sorted(i for i in ids if i),
            "visuals_placed": placed, "forecast_backtest_stated": fc_ok, "golden_hits": hits, "golden_misses": misses,
            "chars": len(text), "problems": problems}



def page_decisions(data: bytes, name: str):
    """The decisions the page sends after the visitor's acknowledgement (src/js/50-try.js keepAll): the scan's flagged columns, every one
    kept, a column the engine coded as it arrived (an email, a phone) coded. Without them a salary column stays withheld and the AI
    reads a payroll with no pay in it (20 Oct 2026)."""
    import nl_browser as NB
    scan = NB.run(data, name, "", None, None)
    flagged = ((scan.get("privacy") or {}).get("flagged") or []) if isinstance(scan, dict) else []
    flagged = [f for f in flagged if isinstance(f, dict) and f.get("column")]
    return flagged, {f["column"]: ("code" if "coded" in str(f.get("kind") or "") else "keep") for f in flagged}

def one(path: str, worker: str, golden: dict) -> dict:
    import nl_browser as NB
    name = os.path.basename(path)
    with open(path, "rb") as fh:
        data = fh.read()
    flagged, dec = page_decisions(data, name)
    prof = NB.profile_for_ai(data, name, flagged=flagged, decisions=dec)
    if not prof.get("ok"):
        return {"file": name, "status": "no_profile"}
    prof = {k: v for k, v in prof.items() if k != "ok"}
    t0 = time.time()
    a = post(worker.rstrip("/") + "/plan", {"objective": "", "profile": prof, "consent": "dataset",
                                            "profile_hash": hashlib.sha256(data).hexdigest()})
    if not isinstance(a.get("plan"), dict):
        return {"file": name, "status": "plan_failed", "why": a.get("error") or a.get("status")}
    rep = NB.run(data, name, "", dict(dec, __plan__=a["plan"]), None)
    if not rep.get("ok"):
        return {"file": name, "status": "engine_failed", "why": str(rep.get("error"))[:200]}
    res = NB.results_for_ai(rep)
    rn = a["plan"].get("row_noun")
    if isinstance(rn, str) and rn:
        res = dict(res, row_noun=rn)
    b = post(worker.rstrip("/") + "/report", {"objective": "", "results": res, "context_queries": rep.get("context_queries") or []})
    if not isinstance(b.get("report"), str):
        return {"file": name, "status": "report_failed", "why": b.get("error") or b.get("status"), "secs": round(time.time() - t0, 1)}
    sc = score(b["report"], res, golden.get(name) or [])
    return {"file": name, "status": "ok", "secs": round(time.time() - t0, 1), "repaired": b.get("repaired"), "model": b.get("model"),
            "sources": len(b.get("sources") or []), "score": sc, "report": b["report"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=os.path.join(SITE, "..", "runs", "ai-report"))
    a = ap.parse_args()
    sys.path[:0] = [os.path.join(SITE, "engine"), os.path.join(os.path.dirname(SITE), "northledger-core")]
    golden = json.load(open(GOLDEN, encoding="utf-8")) if os.path.exists(GOLDEN) else {}
    os.makedirs(a.out, exist_ok=True)
    res, bad = [], 0
    for f in a.files:
        r = one(f, a.worker, golden)
        res.append({k: v for k, v in r.items() if k != "report"})
        if r.get("report"):
            with open(os.path.join(a.out, os.path.basename(f) + ".report.txt"), "w", encoding="utf-8") as fh:
                fh.write(r["report"])
        probs = (r.get("score") or {}).get("problems") or ([r["status"]] if r["status"] != "ok" else [])
        bad += bool(probs)
        sc = r.get("score") or {}
        print("%-4s %-40s %s repaired=%s conf=%s visuals=%d/%d golden=%d/%d %s" % (
            "PASS" if not probs else "FAIL", os.path.basename(f)[:40], r["status"], r.get("repaired"), sc.get("confidence_lines"),
            len(sc.get("visuals_placed") or []), len(sc.get("visuals_available") or []), len(sc.get("golden_hits") or []),
            len(sc.get("golden_hits") or []) + len(sc.get("golden_misses") or []), "; ".join(probs)[:200]))
    with open(os.path.join(a.out, "ai_report.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("AI REPORT: %d of %d files with no problem" % (len(res) - bad, len(res)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
