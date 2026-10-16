#!/usr/bin/env python3
"""Run the engine on the real-dataset corpus (tools/corpus.json) and print one line per file; score it with --score.

    python tools/run_corpus.py                 # every dataset
    python tools/run_corpus.py cpi_us gdp_panel
    python tools/run_corpus.py --pin           # record the sha256 of downloads that have none yet
    python tools/run_corpus.py --json out.json # also write the full summary (the scorecard with --score)
    python tools/run_corpus.py --score --keep-flagged --min 0.7 --json score.json

Remote files are downloaded into .corpus/ (gitignored) and checked against their sha256 in the
manifest; a mismatch stops that file with a plain error instead of testing changed bytes. The
engine runs natively (NL_ENGINE_ROOT: a copy of northledger-core, as for tools/test_nl_browser.py).
Each line: id, kind, ok, rows kept, the headline, how many findings, charts and analyst steps, and
the seconds the run took. Exit 1 when any file fails to download or the engine raises.

--score turns the corpus into a benchmark. Every dataset's "expect" list in the manifest holds what a
good senior analyst must surface from that file, as machine-checkable assertions against the report
(the facts were computed from the data itself with pandas, not copied from the engine's output). On
top of those, GENERIC checks run on every file: the report is finite JSON (no NaN or inf), the engine
says something when it accepted the file, every number in an insight is a printed value of the steps
it cites, no doubled words, no em dashes, the run fits its time budget (RUN_BUDGET) and the analyst
loop its own (ANALYST_BUDGET), and no column the privacy scan withheld reaches the analyst block.
A check that does not apply (no insights to read, nothing withheld) is skipped, not counted.
The scorecard prints passed/total per dataset with each failure's reason, the total, and the
coverage per kind (how many ledger, panel, official, survey and cross-section files get 3 or more
insights). --min (a fraction, or a percent above 1) makes the run exit 2 when the score is lower.

Assertion keys (one main key per assertion, plus "why" for the scorecard):
  ok / error_contains                         the engine accepted (or refused) the file, and why
  "shape.<field>": value (or [values])        a field of analyst.shape, compared case-insensitively
  insight_kind (+ contains / contains_any)    an insight of that kind, optionally saying these words
  insight_mentions_any / insight_mentions_all words some insight / the insights together must say
                                              ("case": true makes _any case-sensitive)
  insight_mentions_none                       words no insight may say
  insight_order: [a, b, c] (+ contains_any)   one insight (that says one of contains_any) names these in order
  aggregates_excluded: [names]                never in an insight, nor a row of a ranking step's table
  no_text_matches: regex                      no insight or headline matches (case-insensitive)
  max_seconds: n                              the scored run took at most n seconds
  no_percent_change_for_zero_crossing: [cols] no %-change of these measures reaches an insight
  ledger_value: x (+ op, key, tol | rel_tol)  a step computed x and an insight citing it prints it
  ledger_word: w (+ op, key)                  a step's word value is w and an insight citing it prints it
  min_insights: n / insight_kinds_include: [] how much the analyst says, and of which kinds
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
MANIFEST = os.path.join(HERE, "corpus.json")
CACHE = os.path.join(SITE, ".corpus")
ENGINE_ROOT = os.path.abspath(
    os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core")
)
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, os.path.join(SITE, "engine"))

RUN_BUDGET = 30.0  # seconds for one engine run on one file, natively
ANALYST_BUDGET = 6.5  # the contract's 6 s for the analyst loop (CONTRACT-v2 5.10), plus scheduling slack
KIND_GROUPS = {
    "ledger": "ledger",
    "panel": "panel",
    "official_stats": "official",
    "survey": "survey",
    "cross_section": "cross_section",
}
_NUM = re.compile(
    r"[-+]?\d(?:[\d,]*\d)?(?:\.\d+)?"
)  # a trailing comma is punctuation, not the number
_CONSTANTS = {
    "95",
    "3.5",
    "12",
}  # wording constants: 95% ranges, robust z beyond 3.5, 12 months
_REPEAT = re.compile(r"\b([A-Za-z]+)\s+\1\b", re.I)
_EM_DASH = chr(0x2014)
_AGG_NOTE = re.compile(
    r"\((?:[^()]|\([^()]*\))*aggregate(?:[^()]|\([^()]*\))*\)", re.I
)  # "(95 aggregate entries such as ASEAN (Ember) are left out ...)", one level of nesting
_RANK_OPS = {"rank_entities", "panel_change", "change", "breakdown", "rank_labels"}


def fetch(ds: dict) -> bytes:
    if ds.get("path"):
        with open(os.path.join(SITE, ds["path"]), "rb") as f:
            return f.read()
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, ds["id"] + ".csv")
    if not os.path.exists(dest):
        req = urllib.request.Request(
            ds["url"], headers={"User-Agent": "northledger-corpus/1"}
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(dest + ".part", "wb") as f:
            f.write(data)
        os.replace(dest + ".part", dest)
    with open(dest, "rb") as f:
        data = f.read()
    want = ds.get("sha256")
    got = hashlib.sha256(data).hexdigest()
    if want and want != got:
        raise RuntimeError(
            "sha256 changed upstream (%s, manifest %s): re-pin on purpose with --pin after a look"
            % (got[:12], want[:12])
        )
    ds["_sha256"] = got
    return data


def summarise(ds: dict, rep: dict, secs: float) -> dict:
    an = rep.get("analyst") or {}
    return {
        "id": ds["id"],
        "kind": ds["kind"],
        "ok": bool(rep.get("ok")),
        "error": rep.get("error") or "",
        "rows": (an.get("rows") or {}).get("kept"),
        "headline": ((rep.get("story") or {}).get("headline") or "")[:160],
        "findings": len(rep.get("findings") or []),
        "charts": len((rep.get("viz") or {}).get("charts") or []),
        "analyst_steps": len(an.get("steps") or []),
        "analyst_insights": [
            i.get("text", "")[:200] for i in (an.get("insights") or [])
        ],
        "seconds": round(secs, 2),
    }


# ----------------------------------------------------------------------------------------------- scoring


def _analyst(rep: dict) -> dict:
    return (rep or {}).get("analyst") or {}


def _insights(rep: dict) -> list:
    return list(_analyst(rep).get("insights") or [])


def _steps(rep: dict) -> dict:
    return {s.get("id"): s for s in _analyst(rep).get("steps") or []}


def _strings(x) -> list:
    """Every string inside a nested report block (the story a visitor reads)."""
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, (list, tuple)):
        return [s for v in x for s in _strings(v)]
    return []


def _visible_texts(rep: dict) -> list:
    return [i.get("text") or "" for i in _insights(rep)] + _strings(
        rep.get("story") or {}
    )


def _cites(rep: dict, sid: str) -> list:
    """Texts of the insights that cite step sid."""
    return [
        i.get("text") or "" for i in _insights(rep) if sid in (i.get("steps") or [])
    ]


def _low(s) -> str:
    return str(s).lower()


def _label(a: dict) -> str:
    s = json.dumps({k: v for k, v in a.items() if k != "why"}, ensure_ascii=False)
    return s if len(s) <= 110 else s[:107] + "..."


def _close(v: float, want: float, a: dict) -> bool:
    if "tol" in a:
        return abs(v - want) <= float(a["tol"])
    return abs(v - want) <= float(a.get("rel_tol", 0.01)) * max(abs(want), 1e-12)


def _ledger_hits(rep: dict, a: dict, match) -> tuple:
    """(surfaced hit, any hit, nearest description) over the step numbers that pass the op/key filters."""
    found_any, near = None, None
    for sid, st in _steps(rep).items():
        if a.get("op") and st.get("op") != a["op"]:
            continue
        for key, n in (st.get("numbers") or {}).items():
            if a.get("key") and key != a["key"]:
                continue
            ok, dist = match(n)
            if dist is not None and (near is None or dist < near[0]):
                near = (dist, "%s.%s=%s" % (sid, key, n.get("text")))
            if not ok:
                continue
            found_any = "%s.%s=%s" % (sid, key, n.get("text"))
            if any(n.get("text") and n["text"] in t for t in _cites(rep, sid)):
                return found_any, found_any, near
    return None, found_any, near


def check_one(a: dict, res: dict) -> tuple:
    """(ok, detail) for one assertion against one run."""
    rep = res.get("rep") or {}
    if res.get("raised"):
        return False, "engine raised: " + res["raised"]
    an, ins = _analyst(rep), _insights(rep)
    texts = [i.get("text") or "" for i in ins]
    alltext = "\n".join(texts)

    if "ok" in a:
        got = bool(rep.get("ok"))
        return got == bool(a["ok"]), "ok=%s (%s)" % (got, (rep.get("error") or "")[:80])
    if "error_contains" in a:
        err = rep.get("error") or ""
        return _low(a["error_contains"]) in _low(err), "error: %s" % err[:100]
    for k in a:
        if k.startswith("shape."):
            field = k[len("shape.") :]
            got = (an.get("shape") or {}).get(field)
            wants = a[k] if isinstance(a[k], list) else [a[k]]
            return any(_low(got) == _low(w) for w in wants), "shape.%s=%s" % (
                field,
                got,
            )
    if "insight_kind" in a:
        kinds = [i.get("kind") for i in ins]
        cand = [i.get("text") or "" for i in ins if i.get("kind") == a["insight_kind"]]
        if not cand:
            return False, "no %s insight (kinds: %s)" % (
                a["insight_kind"],
                ", ".join(map(str, kinds)) or "none",
            )
        ok = [
            t
            for t in cand
            if all(_low(w) in _low(t) for w in a.get("contains") or [])
            and (
                not a.get("contains_any")
                or any(_low(w) in _low(t) for w in a["contains_any"])
            )
        ]
        return bool(ok), "%s insight: %s" % (a["insight_kind"], cand[0][:120])
    if "insight_mentions_any" in a:
        if a.get("case"):  # case-sensitive: a month's name, not the word "may"
            hit = [w for w in a["insight_mentions_any"] if w in alltext]
        else:
            hit = [w for w in a["insight_mentions_any"] if _low(w) in _low(alltext)]
        return bool(hit), (
            ("says %s" % hit[0])
            if hit
            else "none of %s in %d insights" % (a["insight_mentions_any"], len(ins))
        )
    if "insight_mentions_all" in a:
        miss = [w for w in a["insight_mentions_all"] if _low(w) not in _low(alltext)]
        return not miss, ("missing %s" % miss) if miss else "all said"
    if "insight_mentions_none" in a:
        hit = [w for w in a["insight_mentions_none"] if _low(w) in _low(alltext)]
        return not hit, ("says %s" % hit) if hit else "none said"
    if "insight_order" in a:
        names = [_low(n) for n in a["insight_order"]]
        for t in texts:
            if a.get("contains_any") and not any(
                _low(w) in _low(t) for w in a["contains_any"]
            ):
                continue  # the ranking must be of this measure, not another column's
            lt, pos, ok = _low(t), -1, True
            for n in names:
                p = lt.find(n, pos + 1)
                if p < 0:
                    ok = False
                    break
                pos = p
            if ok:
                return True, "in order"
        lead = [t for t in texts if any(n in _low(t) for n in names)]
        return False, "not in this order; nearest: %s" % (
            (lead[0][:120]) if lead else "no insight names them"
        )
    if "aggregates_excluded" in a:
        found = set()
        for nm in a["aggregates_excluded"]:
            pat = re.compile(r"(?<![\w&])" + re.escape(nm) + r"(?![\w&(])")
            # the parenthesis saying the aggregates are left out names them on purpose
            if any(pat.search(_AGG_NOTE.sub("", t)) for t in texts):
                found.add(nm)
            for st in _steps(rep).values():
                if st.get("op") not in _RANK_OPS:
                    continue
                for row in (st.get("table") or {}).get("rows") or []:
                    if row and str(row[0]) == nm:
                        found.add(nm)
        return not found, (
            ("counted as an entry: %s" % sorted(found)) if found else "left out"
        )
    if "no_text_matches" in a:
        pat = re.compile(a["no_text_matches"], re.I)
        hits = [
            t
            for t in texts + [(rep.get("story") or {}).get("headline") or ""]
            if pat.search(t)
        ]
        return not hits, ("matches: %s" % hits[0][:120]) if hits else "clean"
    if "max_seconds" in a:
        return res["secs"] <= float(a["max_seconds"]), "%.1fs" % res["secs"]
    if "no_percent_change_for_zero_crossing" in a:
        cols = {_low(c) for c in a["no_percent_change_for_zero_crossing"]}
        bad = []
        for sid, st in _steps(rep).items():
            m = (st.get("args") or {}).get("measure") or (
                (st.get("numbers") or {}).get("measure") or {}
            ).get("text")
            if _low(m) not in cols:
                continue
            for key, n in (st.get("numbers") or {}).items():
                if n.get("kind") == "pct" and any(
                    n.get("text") and n["text"] in t for t in _cites(rep, sid)
                ):
                    bad.append("%s %s.%s=%s" % (st.get("op"), sid, key, n["text"]))
        return not bad, (
            ("a %%-change of a zero-crossing measure: %s" % bad[0])
            if bad
            else "differences only"
        )
    if "ledger_value" in a:
        want = float(a["ledger_value"])

        def m(n):
            v = n.get("value")
            if not isinstance(v, (int, float)) or n.get("kind") == "word":
                return False, None
            return _close(float(v), want, a), abs(float(v) - want)

        hit, anyhit, near = _ledger_hits(rep, a, m)
        if hit:
            return True, hit
        if anyhit:
            return False, "computed (%s) but no insight prints it" % anyhit
        return False, "nearest %s, want %s" % (
            near[1] if near else "nothing",
            a["ledger_value"],
        )
    if "ledger_word" in a:
        want = _low(a["ledger_word"])

        def m(n):
            return _low(n.get("text")) == want, None

        hit, anyhit, _ = _ledger_hits(rep, a, m)
        if hit:
            return True, hit
        if anyhit:
            return False, "computed (%s) but no insight prints it" % anyhit
        got = [
            "%s.%s=%s"
            % (
                sid,
                a.get("key"),
                (st.get("numbers") or {}).get(a.get("key"), {}).get("text"),
            )
            for sid, st in _steps(rep).items()
            if a.get("key") in (st.get("numbers") or {})
            and (not a.get("op") or st.get("op") == a["op"])
        ]
        return False, ("got %s" % ", ".join(got[:3])) if got else "no step printed it"
    if "min_insights" in a:
        return len(ins) >= int(a["min_insights"]), "%d insights" % len(ins)
    if "insight_kinds_include" in a:
        kinds = {i.get("kind") for i in ins}
        miss = [k for k in a["insight_kinds_include"] if k not in kinds]
        return not miss, (
            ("missing %s (has %s)" % (miss, sorted(map(str, kinds))))
            if miss
            else "all kinds present"
        )
    return False, "unknown assertion " + _label(a)


def _finite(x) -> bool:
    try:
        json.dumps(x, allow_nan=False, default=str)
        return True
    except ValueError:
        return False


def _key(s) -> str:
    """A column name as the privacy scan prints it: 'Market Cap' and 'market_cap' are one column."""
    return re.sub(r"[^a-z0-9]+", "_", _low(s)).strip("_")


def _withheld_leaks(rep: dict, cols: list) -> list:
    """Withheld column names found in the analyst block: its shape, any step's args or word values."""
    an = _analyst(rep)
    want = {_key(c) for c in cols}
    leaks = set()
    sh = an.get("shape") or {}
    for v in _strings(
        {k: sh.get(k) for k in ("date", "entity", "primary", "label", "dimensions")}
    ):
        if _key(v) in want:
            leaks.add(v)
    for m in sh.get("measures") or []:
        if _key((m or {}).get("column")) in want:
            leaks.add(m["column"])
    for st in an.get("steps") or []:
        for v in _strings(st.get("args") or {}):
            if _key(v) in want:
                leaks.add(v)
        for n in (st.get("numbers") or {}).values():
            if n.get("kind") == "word" and _key(n.get("text")) in want:
                leaks.add(n["text"])
    return sorted(leaks)


def generic_checks(ds: dict, res: dict) -> list:
    """[(name, ok or None when it does not apply, detail)] for the checks every file gets."""
    rep = res.get("rep") or {}
    if res.get("raised"):
        return [("generic:engine_did_not_raise", False, res["raised"])]
    out = []
    reps = [rep] + ([res["rep_withheld"]] if res.get("rep_withheld") is not rep else [])
    out.append(
        (
            "generic:finite_json",
            all(_finite(r) for r in reps),
            "no NaN or inf in the report",
        )
    )
    ins = _insights(rep)
    out.append(
        (
            "generic:says_something",
            (len(ins) >= 1) if rep.get("ok") else None,
            "%d insights" % len(ins),
        )
    )
    if ins:
        steps, bad = _steps(rep), []
        for i in ins:
            allowed = set(_CONSTANTS)
            for sid in i.get("steps") or []:
                for n in ((steps.get(sid) or {}).get("numbers") or {}).values():
                    allowed |= set(_NUM.findall(n.get("text") or ""))
            bad += [
                "%s:%s" % (i.get("id"), x)
                for x in _NUM.findall(i.get("text") or "")
                if x not in allowed
            ]
        out.append(
            (
                "generic:insight_numbers_in_ledger",
                not bad,
                (
                    ("not in its steps' ledger: %s" % ", ".join(bad[:4]))
                    if bad
                    else "every number is a ledger value"
                ),
            )
        )
    else:
        out.append(("generic:insight_numbers_in_ledger", None, "no insights"))
    vis = _visible_texts(rep)
    rep_words = sorted({m.group(0) for t in vis for m in _REPEAT.finditer(t)})
    out.append(
        (
            "generic:no_repeated_words",
            not rep_words,
            ("doubled: %s" % rep_words[:3]) if rep_words else "clean",
        )
    )
    dash = [t for t in vis if _EM_DASH in t]
    out.append(
        (
            "generic:no_em_dash",
            not dash,
            ("em dash in: %s" % dash[0][:90]) if dash else "clean",
        )
    )
    out.append(
        (
            "generic:run_budget",
            res["secs"] <= RUN_BUDGET,
            "%.1fs (budget %.0fs)" % (res["secs"], RUN_BUDGET),
        )
    )
    asec = _analyst(rep).get("seconds")
    out.append(
        (
            "generic:analyst_budget",
            (float(asec) <= ANALYST_BUDGET) if isinstance(asec, (int, float)) else None,
            "analyst loop %ss (budget %.1fs)" % (asec, ANALYST_BUDGET),
        )
    )
    flagged = res.get("flagged") or []
    if flagged:
        leaks = _withheld_leaks(res.get("rep_withheld") or {}, flagged)
        out.append(
            (
                "generic:withheld_columns_absent",
                not leaks,
                (
                    ("withheld but in the analyst block: %s" % leaks)
                    if leaks
                    else "withheld %s stay out" % flagged
                ),
            )
        )
    else:
        out.append(("generic:withheld_columns_absent", None, "nothing withheld"))
    return out


def score_dataset(ds: dict, res: dict) -> dict:
    results = []
    for name, ok, detail in generic_checks(ds, res):
        results.append(
            {"check": name, "ok": ok, "skipped": ok is None, "detail": detail}
        )
    for a in ds.get("expect") or []:
        try:
            ok, detail = check_one(a, res)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a broken assertion is a failure, not a crash
            ok, detail = False, "assertion error %s: %s" % (type(exc).__name__, exc)
        results.append(
            {
                "check": _label(a),
                "ok": bool(ok),
                "skipped": False,
                "detail": detail,
                "why": a.get("why", ""),
            }
        )
    counted = [r for r in results if not r["skipped"]]
    return {
        "id": ds["id"],
        "kind": ds["kind"],
        "passed": sum(1 for r in counted if r["ok"]),
        "total": len(counted),
        "expect_passed": sum(
            1 for r in counted if r["ok"] and not r["check"].startswith("generic:")
        ),
        "expect_total": sum(
            1 for r in counted if not r["check"].startswith("generic:")
        ),
        "insights": len(_insights(res.get("rep") or {})),
        "seconds": round(res["secs"], 2),
        "flagged": res.get("flagged") or [],
        "results": results,
    }


def print_scorecard(cards: list) -> dict:
    print(
        "\nSCORECARD  (generic checks + each dataset's expect list; skipped checks not counted)"
    )
    print(
        "%-20s %-14s %9s %8s %5s %8s"
        % ("dataset", "kind", "score", "expect", "ins", "secs")
    )
    for c in cards:
        print(
            "%-20s %-14s %4d/%-4d %4d/%-3d %5d %7.1fs"
            % (
                c["id"],
                c["kind"],
                c["passed"],
                c["total"],
                c["expect_passed"],
                c["expect_total"],
                c["insights"],
                c["seconds"],
            )
        )
        for r in c["results"]:
            if not r["skipped"] and not r["ok"]:
                print(
                    "    FAIL %s\n         %s%s"
                    % (
                        r["check"],
                        r["detail"],
                        ("  [" + r["why"] + "]") if r.get("why") else "",
                    )
                )
    p, t = sum(c["passed"] for c in cards), sum(c["total"] for c in cards)
    ep, et = sum(c["expect_passed"] for c in cards), sum(
        c["expect_total"] for c in cards
    )
    cov = {}
    for g in ("ledger", "panel", "official", "survey", "cross_section"):
        cs = [c for c in cards if KIND_GROUPS.get(c["kind"]) == g]
        cov[g] = {
            "with_3_insights": sum(1 for c in cs if c["insights"] >= 3),
            "datasets": len(cs),
        }
    total = {
        "passed": p,
        "total": t,
        "score": round(p / t, 4) if t else 0.0,
        "expect_passed": ep,
        "expect_total": et,
        "expect_score": round(ep / et, 4) if et else 0.0,
    }
    print(
        "\nTOTAL %d/%d (%.1f%%); dataset facts %d/%d (%.1f%%)"
        % (p, t, 100 * total["score"], ep, et, 100 * total["expect_score"])
    )
    print(
        "coverage (files with >=3 insights): "
        + ", ".join(
            "%s %d/%d" % (g, v["with_3_insights"], v["datasets"])
            for g, v in cov.items()
        )
    )
    return {"total": total, "coverage": cov}


# ----------------------------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--pin", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--as-of", default="2026-10-01")
    ap.add_argument(
        "--keep-flagged",
        action="store_true",
        help="re-run keeping every column the privacy scan flagged",
    )
    ap.add_argument(
        "--score",
        action="store_true",
        help="evaluate every dataset's expect list and the generic checks",
    )
    ap.add_argument(
        "--min",
        type=float,
        default=None,
        help="with --score: exit 2 when the score is below this (0-1, or a percent)",
    )
    a = ap.parse_args()
    with open(MANIFEST) as f:
        man = json.load(f)
    import nl_browser

    out, cards, bad, pinned = [], [], 0, False
    for ds in man["datasets"]:
        if a.ids and ds["id"] not in a.ids:
            continue
        try:
            data = fetch(ds)
        except Exception as exc:  # noqa: BLE001
            print("%-18s %-14s FETCH FAIL %s" % (ds["id"], ds["kind"], exc))
            bad += 1
            if a.score:
                cards.append(
                    score_dataset(ds, {"raised": "fetch failed: %s" % exc, "secs": 0.0})
                )
            continue
        if a.pin and ds.get("url") and not ds.get("sha256"):
            ds["sha256"] = ds["_sha256"]
            pinned = True
        t = time.perf_counter()
        res = {"flagged": []}
        try:
            nm = os.path.basename(ds.get("path") or ds["id"] + ".csv")
            rep = nl_browser.run(data, nm, as_of=a.as_of)
            res["rep_withheld"] = rep
            flagged = [
                f["column"]
                for f in (rep.get("privacy") or {}).get("flagged") or []
                if f.get("decision") != "keep"  # amounts kept by default are not withheld
            ]
            res["flagged"] = flagged
            if flagged and a.keep_flagged:
                # as a visitor who reads the scan and keeps the columns: the corpus is public data
                t = (
                    time.perf_counter()
                )  # the scored run is the second one; time it alone
                rep = nl_browser.run(
                    data, nm, decisions={c: "keep" for c in flagged}, as_of=a.as_of
                )
            if flagged:
                print(
                    "%-18s privacy scan flagged %s%s"
                    % (
                        ds["id"],
                        ", ".join(flagged),
                        " (kept for this run)" if a.keep_flagged else " (withheld)",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - run() must never raise
            print(
                "%-18s %-14s RAISED %s: %s"
                % (ds["id"], ds["kind"], type(exc).__name__, exc)
            )
            bad += 1
            if a.score:
                res.update(
                    raised="%s: %s" % (type(exc).__name__, exc),
                    secs=time.perf_counter() - t,
                )
                cards.append(score_dataset(ds, res))
            continue
        secs = time.perf_counter() - t
        res.update(rep=rep, secs=secs)
        s = summarise(ds, rep, secs)
        out.append(s)
        print(
            "%-18s %-14s %-5s rows=%-7s find=%-3s viz=%-2s steps=%-2s %6.1fs  %s"
            % (
                s["id"],
                s["kind"],
                "ok" if s["ok"] else "REFUSED",
                s["rows"],
                s["findings"],
                s["charts"],
                s["analyst_steps"],
                s["seconds"],
                s["headline"] or s["error"][:120],
            )
        )
        for ins in s["analyst_insights"]:
            print("    - " + ins)
        if a.score:
            cards.append(score_dataset(ds, res))
        sys.stdout.flush()
    if pinned:
        for ds in man["datasets"]:
            ds.pop("_sha256", None)
        with open(MANIFEST, "w") as f:
            json.dump(man, f, indent=2, ensure_ascii=False)
            f.write("\n")
    rc = 1 if bad else 0
    if a.score:
        agg = print_scorecard(cards)
        if a.json:
            with open(a.json, "w") as f:
                json.dump(
                    {
                        "as_of": a.as_of,
                        "keep_flagged": a.keep_flagged,
                        **agg,
                        "datasets": cards,
                        "runs": out,
                    },
                    f,
                    indent=1,
                    default=str,
                )
        if a.min is not None:
            want = a.min / 100.0 if a.min > 1 else a.min
            if agg["total"]["score"] < want:
                print(
                    "score %.1f%% is below --min %.1f%%"
                    % (100 * agg["total"]["score"], 100 * want)
                )
                rc = rc or 2
    elif a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
