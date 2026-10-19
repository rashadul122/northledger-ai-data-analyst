#!/usr/bin/env python3
"""
check_ai_dashboard: the AI path's scorecard (19 Oct 2026). check_universal runs every corpus file WITHOUT the AI; a visitor's file
goes through the AI's plan, and the AI's dashboard design is where the live misses were (a yearly FRED series averaged over 87
years, its line a table). For each file of ../corpus/universal this asks a worker's POST /plan for a plan, exactly as the page does
after the visitor's acknowledgement (the engine's profile, its sample of rows and its shape; consent "dataset"), runs the engine
with that plan (nl_browser.run, decisions {"__plan__": plan}), and scores the dashboard the AI designed:

  * chosen_by: "ai" (the AI's design stood) or "engine" (none of it could be built);
  * refused elements, tiles with no figure, charts with nothing to draw (nl_dashboard.signals: what the self-correction is told);
  * a published series (shape.rows_are) whose design has no latest / change / peak / low tile.

    ../agent-demo/venv/bin/python tools/check_ai_dashboard.py --worker https://<worker>/ [--only NAME] [--out DIR] [--replan]

--replan sends the signals back once (/plan with feedback, as the page's one self-correction does) and scores the revised design
too. Each file is one or two AI calls on the worker's daily budget: run it on purpose, never in verify.sh. Writes OUT/ai_dashboard.json
and prints one line per file; exit 1 when a file's final design still carries a signal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
CORPUS = os.path.join(os.path.dirname(SITE), "corpus", "universal")
ORIGIN = "https://northledger.halosyncs.com"


def post(url: str, body: dict, timeout: int = 200) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": ORIGIN})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return dict(json.loads(exc.read().decode() or "{}"), status=exc.code)
        except Exception:  # noqa: BLE001
            return {"error": "http_%d" % exc.code}


def score(rep: dict, shape: dict) -> dict:
    import nl_dashboard as ND
    d = rep.get("dashboard") or {}
    sig = ND.signals(d) if d.get("chosen_by") == "ai" else []
    if d.get("ok") and d.get("chosen_by") != "ai":
        sig = [{"kind": "dashboard", "detail": "the AI's design could not be built; the engine designed this one"}]
    series = (shape or {}).get("rows_are") == "published figures"
    return {"chosen_by": d.get("chosen_by"), "ok": bool(d.get("ok")), "series": series,
            "tiles": [k.get("agg") for k in d.get("kpis") or []], "charts": [c.get("draw") for c in d.get("charts") or []],
            "signals": [s["detail"] for s in sig]}


def one(entry: dict, worker: str, replan: bool) -> dict:
    import nl_browser as NB
    with open(os.path.join(CORPUS, entry["file"]), "rb") as fh:
        data = fh.read()
    prof = NB.profile_for_ai(data, entry["file"], decisions=None)
    if not prof.get("ok"):
        return {"file": entry["file"], "status": "no_profile"}
    prof = {k: v for k, v in prof.items() if k != "ok"}
    body = {"objective": "", "profile": prof, "consent": "dataset", "profile_hash": hashlib.sha256(data).hexdigest()}
    t0 = time.time()
    a = post(worker.rstrip("/") + "/plan", body)
    if not isinstance(a.get("plan"), dict):
        return {"file": entry["file"], "status": "plan_failed", "why": a.get("error") or a.get("status")}
    plan = a["plan"]
    rep = NB.run(data, entry["file"], "", {"__plan__": plan}, None)
    first = score(rep, prof.get("shape"))
    out = {"file": entry["file"], "status": "ok", "first": first, "secs": round(time.time() - t0, 1)}
    if replan and first["signals"]:
        sigs = [s for s in rep.get("plan_signals") or [] if isinstance(s, dict)][:20]
        prev = {k: plan.get(k) for k in ("goal", "kind", "primary", "columns", "operations", "analyses", "charts", "dashboard")}
        b = post(worker.rstrip("/") + "/plan", dict(body, profile_hash=None, feedback={"attempt": 1, "previous_plan": prev, "signals": sigs}))
        if isinstance(b.get("plan"), dict):
            rep2 = NB.run(data, entry["file"], "", {"__plan__": b["plan"]}, None)
            out["revised"] = score(rep2, prof.get("shape"))
        else:
            out["revised"] = {"error": b.get("error") or b.get("status")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True, help="the worker's base URL (a staging or local worker with the 19 Oct menu)")
    ap.add_argument("--only")
    ap.add_argument("--out", default=os.path.join(SITE, "..", "runs", "ai-dashboard"))
    ap.add_argument("--replan", action="store_true")
    a = ap.parse_args()
    sys.path[:0] = [os.path.join(SITE, "engine"), os.path.join(os.path.dirname(SITE), "northledger-core")]
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    files = man if isinstance(man, list) else man.get("files", [])
    res, bad = [], 0
    for e in files:
        if a.only and a.only not in e["file"]:
            continue
        r = one(e, a.worker, a.replan)
        res.append(r)
        final = r.get("revised") if isinstance(r.get("revised"), dict) and "signals" in r["revised"] else r.get("first") or {}
        left = len(final.get("signals") or []) if r["status"] == "ok" else -1
        bad += left != 0
        print("%-4s %-48s %s first %d signal(s)%s" % ("PASS" if left == 0 else "FAIL", e["file"][:48], r["status"],
                                                      len((r.get("first") or {}).get("signals") or []),
                                                      (", revised %d" % len(r["revised"].get("signals") or [])) if "revised" in r and "signals" in r["revised"] else ""))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ai_dashboard.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("AI DASHBOARD: %d of %d files with no signal left" % (len(res) - bad, len(res)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
