#!/usr/bin/env python3
"""Convert agent session transcripts into a compact JSON the demo web page embeds.
Bundles artifact files (reports) as base64 downloads so the single-file page can
serve them without a server. Output: sessions.js (window.SESSIONS).

Each session -> {name, goal, model, n_turns, tool_calls, artifacts, chat: [...],
                 downloads: [{name, b64, size}]}

Chat format (what the web chatbox renders):
  {who: 'user'|'agent'|'tool', text, tool?: {name, detail}}
  - user: the goal
  - agent: assistant text (thinking + final answers)
  - tool: one per tool call: name + compact detail of what it did
Tool detail is a SHORT human summary: sql for run_sql, table for profile,
last-expression result preview for run_python, key numbers for forecast,
artifact path for make_report.
"""
import base64
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_B64 = 2_600_000  # ~2.6 MB per file, keep total bundle sane


def shorten_sql(sql, n=140):
    s = " ".join((sql or "").split())
    return s if len(s) <= n else s[:n] + "…"


def tool_detail(name, args, result):
    if name == "run_sql":
        d = {"sql": shorten_sql(args.get("sql", ""))}
        if "error" in (result or {}):
            d["error"] = result["error"][:120]
        else:
            d["rows_preview"] = result.get("rows", [])[:3]
            d["columns"] = result.get("columns", [])[:8]
        return d
    if name == "profile_table":
        r = result or {}
        d = {"table": args.get("table")}
        if "rows" in r:
            d["rows"] = r["rows"]
            d["columns"] = list(r.get("columns", {}).keys())
            dup = {k: v for k, v in r.items() if k.startswith("duplicate_")}
            d["duplicates"] = dup or None
        else:
            d["error"] = r.get("error", "profile failed")[:120]
        return d
    if name == "run_python":
        d = {"code": " ".join((args.get("code") or "").split())[:220] + "…"}
        r = result or {}
        if "error" in r:
            d["error"] = r["error"][:160]
        else:
            out = (r.get("stdout") or "").strip()
            res = r.get("result")
            d["returned"] = out[-300:] if out else (str(res)[:300] if res is not None else "")
        return d
    if name == "forecast":
        r = result or {}
        d = {"model": r.get("model"), "n_history": r.get("n_history"),
             "mape": r.get("mape_fit_pct")}
        f = r.get("forecast") or []
        d["first3"] = f[:3]
        if "error" in r:
            d["error"] = r["error"][:120]
        return d
    if name == "make_report":
        r = result or {}
        return {"artifact": r.get("artifact"), "type": r.get("type"), "title": r.get("title")}
    return {"detail": "…"}



TITLES = {
    "s1-audit": ("Data-health audit (messy legacy export)",
                 "The agent profiles a deliberately-abused 20,488-row export and finds every defect with exact counts."),
    "s2-clean": ("Clean, validate, reconcile",
                 "Dedupe, normalize 4 date formats, fix casing chaos, write the clean table, reconcile against source with zero deltas."),
    "s3-forecast": ("Forecast + risk brief",
                    "Monthly history is thin, so the agent adapts to daily scale, backtests, and honestly reports the naive baseline won."),
    "s4-nyc311-ops": ("NYC 311 operations brief (22.5M rows)",
                      "Workload, complaint mix, agency resolution times (NYPD 53-min median vs HPD 4.7 days), and a 12-month forecast that beats naive."),
    "s5-rdw-fleet": ("Dutch fleet electrification (16.9M vehicles)",
                     "Fuel mix, APK cohorts, and the 77x EV gap between new and old registrations — the flow-vs-stock story."),
    "s6-census-econ": ("US census economics (5M weighted records)",
                       "Survey-weighted medians: who earns what in America, housing burden, and the education ladder."),
    "s7-chicago-crime": ("Chicago crime trend brief (8.6M rows)",
                         "A falling decade, narcotics as an enforcement signal, +226% motor-vehicle-theft spike, and the bankable summer curve."),
}

def main():
    out = {"sessions": []}
    total_b64 = 0
    for tdir in sorted(glob.glob(os.path.join(HERE, "sessions", "*"))):
        tp = os.path.join(tdir, "transcript.json")
        if not os.path.exists(tp):
            continue
        t = json.load(open(tp))
        name = t["session"]
        if name == "smoke":
            continue  # internal test only
        chat = [{"who": "user", "text": t["goal"]}]
        n_tool = 0
        artifacts = []
        failed_streak = 0
        for turn in t["turns"]:
            calls = turn.get("tool_calls") or []
            if calls:
                n_tool += len(calls)
                if turn.get("content"):
                    chat.append({"who": "agent", "text": turn["content"].strip(), "kind": "thinking"})
                for c in calls:
                    td = tool_detail(c["name"], c.get("args", {}), c.get("result"))
                    is_fail = "error" in (c.get("result") or {})
                    if is_fail:
                        failed_streak += 1
                        if failed_streak > 2:
                            continue  # skip repeats after 2 consecutive failures shown
                    else:
                        failed_streak = 0
                    chat.append({"who": "tool", "tool": c["name"], "detail": td})
                    if c["name"] == "make_report" and (c.get("result") or {}).get("artifact"):
                        artifacts.append(c["result"])
            else:
                if turn.get("content"):
                    chat.append({"who": "agent", "text": turn["content"].strip(), "kind": "final"})
        # bundle artifact files
        downloads = []
        for a in artifacts:
            rel = a.get("path") or a.get("artifact")
            if not rel:
                continue
            fp = os.path.join(tdir, rel)
            if os.path.exists(fp) and os.path.getsize(fp) < MAX_B64:
                b64 = base64.b64encode(open(fp, "rb").read()).decode()
                total_b64 += len(b64)
                downloads.append({"name": os.path.basename(rel),
                                  "b64": b64, "size": os.path.getsize(fp),
                                  "type": a.get("type"), "title": a.get("title")})
        out["sessions"].append({
            "name": name,
            "title": (TITLES.get(name, (name, ""))[0]),
            "blurb": (TITLES.get(name, ("", t["goal"][:120]))[1]),
            "goal_short": t["goal"][:120] + ("…" if len(t["goal"]) > 120 else ""),
            "model": t.get("model", "glm-5.3"),
            "n_turns": len(t["turns"]),
            "n_tools": n_tool,
            "chat": chat,
            "artifacts": [a.get("path") for a in artifacts],
            "downloads": downloads,
        })
    js = "window.SESSIONS = " + json.dumps(out, ensure_ascii=False) + ";\n"
    p = os.path.join(HERE, "sessions.js")
    open(p, "w").write(js)
    print(f"wrote {p} ({len(js)/1e6:.2f} MB, {len(out['sessions'])} sessions, b64 payload {total_b64/1e6:.2f} MB)")
    for s in out["sessions"]:
        print(" -", s["name"], "| turns", s["n_turns"], "| tools", s["n_tools"],
              "| artifacts", len(s["downloads"]))


if __name__ == "__main__":
    main()