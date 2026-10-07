#!/usr/bin/env python3
"""
nl_browser: the NorthLedger engine's own end-to-end path on one file, in a visitor's browser.

    import nl_browser
    report = nl_browser.run(csv_bytes, "ledger.csv", "What changed this year?")
    text = nl_browser.run_json(csv_bytes, "ledger.csv", "", decisions={"notes": "keep"})

The same file runs in CPython (the tests) and in Pyodide (the site). It does no analysis of
its own. Every stage is the engine's own function, called in the engine's own order:

    engagement.land             read the file, scan it for personal data, code what is certain
    engagement.decide           the visitor's decision on each flagged column (default: withhold)
    loop.run_loop               the no-AI Data Health Audit: profile, clean, gate, narrate
    loop.run_analyze            profile, clean, measures, backtested forecast, gate, story
    clean.standard_rules        the rule set the cleaner used, so each repair is named

and then translates the engine's results into THE REPORT CONTRACT (see REPORT_KEYS below),
the one JSON shape the page renders. The translation adds no figure: every number in a text
field is one the engine wrote. Rounding and wording stay the engine's.

What this adapter adds, and why:
  * Limits: 25 MB and 200,000 rows. A bigger file is refused with a plain message; nothing
    is ever sampled or cut. So is a file with lines the reader would skip (more fields than
    the header), and one so full of repeated rows that the duplicate check would stall.
  * Withheld columns stay out of everything the page shows or offers to download. The engine
    itself only keeps a withheld column away from AI models; the audit still profiles it. So
    the adapter drops withheld columns from both CSV downloads, replaces any quarantine
    reason that quotes one of their values, and scrubs any of their values that reach a text
    field (a defence in depth: the engine prints none of them in the normal path). If the
    engine chose a withheld column as its analysis date, the time analysis is not shown at
    all, and the report says why.
  * A pinned analysis date (as_of), for the sample file only: the engine's profiler reads the
    clock itself, so a fixed sample would age into a "stale" report. With as_of given, the
    profiler's clock is set to that date for the run and restored afterwards. No engine file
    is changed. Without as_of, the engine runs on today's date.
  * Stubs for standard modules a browser build lacks (nl_stubs/), installed only when the
    real module is missing.
  * The forecast's measured-coverage check, run from the pack: the engine quotes the 80%
    ranges' measured coverage from benchmark/engine_benchmark.json only when that receipt
    measured its own decision code, a hash over every northledger/*.py but benchmark.py. The
    pack leaves some of those files out, so the browser cannot recompute it; the packer stamps
    it in nl_pack.json from the whole engine. When every packed module still matches its stamp,
    the adapter hands that id to the engine's own check (forecast.coverage_evidence) and puts
    the result where the engine keeps it. No figure is computed here, and a stale receipt is
    still never quoted.
  * What a visitor reads, without changing a figure: a file whose first row is a title (not
    column names) is refused with a plain message; text fields never show a phone number or an
    email address (the personal-data scan reads headings and value shapes and can miss a
    column), nor the engine's internal table name; a type conversion is described as one, not
    as a repair; set-aside dates that could be day/month or month/day are counted under that
    reason; a refused business analysis is said once. The downloads carry each row's line in
    the visitor's file (source_line, the same line in every download, after the AI plan's row
    filters too) and write whole numbers without ".0".

Nothing is sent anywhere. The file lives in a temporary folder for the run and is deleted,
with everything the engine wrote, before run() returns. run() never raises: a failure
comes back as {"ok": false, "error": "<plain sentence>"}.
"""
from __future__ import annotations

import contextlib
import csv
import datetime as _dt
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import types
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

MAX_BYTES = 25_000_000          # "25 MB", the same figure the page states and enforces (build.py TRY_MAX_BYTES)
MAX_ROWS = 200000
# The engine's exact-duplicate rule does work proportional to rows x duplicate rows
# (clean._rule_dedupe rebuilds set(dupes) once per row: reported to the engine's owner).
# Above this product a browser tab would stall for minutes, so the file is refused plainly.
DEDUPE_BUDGET = 60_000_000
ACCEPTED_SUFFIXES = (".csv", ".tsv", ".txt")
DECISIONS = ("withhold", "code", "keep")
STAGES = ("read", "profile", "decide", "clean", "analyze", "forecast", "story")
DEFAULT_OBJECTIVE = ("What is happening to volume and the main measures, and what is likely "
                     "to come next?")
WITHHELD_MARK = "[withheld]"
# The start of the headline when the engine refuses the business analysis (the page reads it).
GATE_TRIPPED = "The business analysis did not run"
# what the planner's profile says in place of the file's name (the page's /report payload says the same)
FILE_WORD = "[your file]"
# The report writer's one line about the flagged columns the visitor kept (option B, owner's decision, 29 Sep
# 2026: the page sends a kept column's values only after a ticked box that names it). Column names only, no value.
OPTED_IN = "The visitor chose to send these personal columns to the AI: %s."
# Added to a flagged column's kind when the engine coded its values as it landed the file.
CODED_ON_ARRIVAL = "coded as it arrived"

# THE REPORT CONTRACT: the keys run() returns, at each level. The page and the tests both
# check against this, so a change here is a change to the contract. Version 2 (engine/
# CONTRACT-v2.md, design §4.2) keeps every v1 key and adds the V2_* keys below.
CONTRACT_VERSION = 2
VIZ_VERSION = "2026-09-30.1"            # the chart registry's frozen interface (tools/fixtures/viz/spec.json; nl_viz)
REPORT_KEYS = ("ok", "error", "engine", "input", "timings", "privacy", "health", "cleaning",
               "roles", "findings", "forecast", "story", "downloads",
               "contract_version", "primary_metric", "tests_run", "methods", "limitations",
               "reproducibility", "charts", "charts_suppressed", "llm", "summary", "structure", "estimand")
GRADE = {"RECOMMEND": "CONFIRMED", "WATCH": "WATCH", "INSUFFICIENT": "NOT_ENOUGH_DATA"}
MANAGER_MAX_CHARTS = 6              # §5: at most six manager displays by default: the headline tiles and
                                    # the findings table count, so at most four are drawn as charts
KPI_MAX = 3                         # the first screen's tiles: at most three business claims
RANK_TOP = 5                        # §5 #7/#8: top 5 plus "other"
DIM_LEVELS = (2, 50)                # §5 #2b/#7: a dimension with 2-50 levels
SEASON_MIN_MONTHS = 24              # §5 #3
CORR_MIN_MEASURES = 3               # §5 #9
MISSING_SHARE = 0.01                # §5 #11: any column over 1% empty
CATMONTH_MAX = 3                    # category x month heatmaps drawn, in the engine's column order
# §5 #8's reason: the map counts rows and never flags a driver or a reversal; where the change sits is the contribution
# waterfall's job, when the chart registry built one (nl_viz.mend_catmonth_why swaps the clause), else none is charted
CATMONTH_WHY = "a tested change claim and a category column with 2-50 levels (%s)"
CATMONTH_NO_WATERFALL = ("it counts rows; no contribution waterfall was built for this file, so no driver or reversal "
                         "is flagged")
CATMONTH_WATERFALL = ("it counts rows; the contribution waterfall shows where the change sits, and this map flags no "
                      "driver or reversal")
SUBKEYS = {
    "engine": ("snapshot", "version"),
    "input": ("name", "bytes", "rows", "columns", "sha256"),
    "privacy": ("flagged",),
    "health": ("score", "issues"),
    "cleaning": ("rows_in", "rows_clean", "rows_quarantined", "fixes", "quarantine_reasons"),
    "roles": ("date", "measures", "dimensions", "excluded"),
    "forecast": ("available", "reason", "verdict", "champion", "baseline_won", "series",
                 "forecast", "backtest"),
    "story": ("headline", "what_happened", "why", "what_to_do", "whats_next", "cannot_answer"),
    "downloads": ("clean_csv", "quarantine_csv", "ledger_json"),
}
ITEM_KEYS = {
    "timings": ("stage", "seconds"),
    "privacy.flagged": ("column", "kind", "decision"),
    "cleaning.fixes": ("rule", "column", "count", "what"),
    "cleaning.quarantine_reasons": ("reason", "count"),
    "findings": ("id", "claim", "verdict", "why", "kind", "value"),
    "forecast.series": ("month", "actual"),
    "forecast.forecast": ("month", "value", "lo", "hi"),
    "forecast.backtest": ("mape", "mase", "coverage"),
}

HERE = os.path.dirname(os.path.abspath(__file__))


class Refusal(Exception):
    """A plain-language reason the file is not analysed. Shown to the visitor as is."""


# --------------------------------------------------------------------------- environment
def _install_stubs() -> None:
    """Standard modules a WebAssembly Python may lack. Only installed when really missing."""
    if HERE not in sys.path:
        sys.path.append(HERE)
    try:
        import resource  # noqa: F401
    except ImportError:
        from nl_stubs import resource as _stub
        sys.modules["resource"] = _stub


def _import_engine():
    """The packed engine sits beside this file in the browser; in the repository it is
    ../../northledger-core. Either way it is the engine's own code, unmodified."""
    try:
        import northledger  # noqa: F401
    except ImportError:
        repo = os.path.normpath(os.path.join(HERE, "..", "..", "northledger-core"))
        if os.path.isdir(os.path.join(repo, "northledger")) and repo not in sys.path:
            sys.path.insert(0, repo)
        import northledger  # noqa: F401
    if HERE not in sys.path:
        sys.path.append(HERE)
    import northledger
    return northledger


_COVERAGE_SEEDED = False


def _seed_forecast_coverage() -> None:
    """In the pack only (nl_pack.json beside this file): give the engine the decision-code id
    the packer computed from the whole engine, so its own coverage_evidence check can run.
    Skipped (the engine then says the measured coverage is not yet published) when there is no
    stamp or id, or any packed engine file differs from the stamp."""
    global _COVERAGE_SEEDED
    if _COVERAGE_SEEDED:
        return
    _COVERAGE_SEEDED = True
    stamp_path = os.path.join(HERE, "nl_pack.json")
    if not os.path.exists(stamp_path):
        return                                   # the source tree: the engine checks by itself
    with open(stamp_path, encoding="utf-8") as fh:
        stamp = json.load(fh)
    want = str(stamp.get("decision_code_snapshot") or "")
    files = stamp.get("files") or {}
    if not want or not files:
        return
    for arc, sha in files.items():
        p = os.path.join(HERE, *arc.split("/"))
        try:
            with open(p, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != sha:
                    return
        except OSError:
            return
    from northledger import forecast as _fc
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(_fc.__file__))),
                        "benchmark", "engine_benchmark.json")
    ev = None
    try:
        with open(path, encoding="utf-8") as fh:
            ev = _fc.coverage_evidence(json.load(fh), expected_snapshot=want)
    except (OSError, ValueError, TypeError, KeyError):
        ev = None
    _fc._EVIDENCE_CACHE["default"] = ev


def engine_info() -> Dict[str, str]:
    """Which engine produced the report: the packed snapshot id, or the source tree's own."""
    nl = _import_engine()
    stamp = os.path.join(HERE, "nl_pack.json")
    if os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as fh:
            info = json.load(fh)
        return {"snapshot": str(info["engine_snapshot"])[:12],
                "version": str(info.get("engine_version") or getattr(nl, "__version__", ""))}
    from northledger.loop import engine_snapshot
    return {"snapshot": engine_snapshot()["id"][:12], "version": str(getattr(nl, "__version__", ""))}


@contextlib.contextmanager
def _pinned_clock(as_of: Optional[str]):
    """Run the profiler as if today were `as_of` (the sample's fixed analysis date).

    health.score_table reads datetime.now() for its timeliness score and its "newest row"
    line; every other stage takes as_of as an argument. The module's own datetime name is
    swapped for a copy whose now()/today() return as_of, then put back."""
    if not as_of:
        yield
        return
    from northledger import health as _health
    fixed = _dt.datetime.combine(_dt.date.fromisoformat(as_of), _dt.time(12, 0))

    class _PinnedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 - mirrors datetime.now
            return fixed if tz is None else fixed.replace(tzinfo=tz)

        @classmethod
        def today(cls):
            return fixed

    class _PinnedDate(_dt.date):
        @classmethod
        def today(cls):
            return fixed.date()

    shim = types.ModuleType("datetime")
    shim.__dict__.update(_dt.__dict__)
    shim.datetime, shim.date = _PinnedDatetime, _PinnedDate
    before = _health._dt
    _health._dt = shim
    try:
        yield
    finally:
        _health._dt = before


# --------------------------------------------------------------------------- small helpers
def _num(v: Any) -> Optional[float]:
    """A JSON-safe number: a finite float, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


_REF_RE = re.compile(r"\s*\[fact: [^\]]*\]")


def _plain(text: Any) -> str:
    """Engine prose without its [fact: ...] citations (the ledger download keeps them)."""
    return _REF_RE.sub("", str(text or "")).strip()


# The design document's codes ("design M1-M2", "review of R1", "design §1.5") and the engine's
# internal verdict word, as they would reach a visitor in the v2 blocks. The page's grades are
# CONFIRMED / WATCH / NOT ENOUGH DATA, so "a recommendation" there means "confirmed".
_PLAN_CODE_RE = re.compile(r"\s*\((?:design|review of)\b[^()]*\)")
_VISITOR_WORDS = (("To turn this into a recommendation:", "To confirm it:"),
                  ("it becomes a recommendation", "it can be confirmed"))


# an engine finding id quoted in prose (review v2: "The like-for-like comparison is
# measure.volume.like_for_like.change_pct."): a reader sees the finding itself, not its id, and the
# id is one unbreakable token that pushed a phone layout sideways
_FINDING_ID = r"(?:measure|forecast|health|clean)\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+"
_ID_SENTENCE_RE = re.compile(r"The like-for-like comparison is %s\." % _FINDING_ID)
_ID_PAREN_RE = re.compile(r"\s*\(%s\)" % _FINDING_ID)


def _visitor(text: Any) -> str:
    """_plain, without plan codes and with the page's grade words (v2 blocks only; the v1 keys and
    the downloads keep the engine's own text)."""
    t = _PLAN_CODE_RE.sub("", _plain(text))
    t = _ID_SENTENCE_RE.sub("The like-for-like comparison is its own finding.", t)
    t = _ID_PAREN_RE.sub("", t)
    for a, b in _VISITOR_WORDS:
        t = t.replace(a, b)
    return t.strip()


def _movement(eff: Dict[str, Any], bar: Optional[float]) -> Optional[Dict[str, Any]]:
    """For a WATCH change claim: did it clearly move? "moved" when its 95% interval lies wholly on
    one side of zero but not wholly beyond the bar (changed, but not yet shown to be at least the
    bar: design §2.4); "cleared" when the whole interval is beyond the bar, so another check holds
    it back; "unclear" when the interval includes zero; None with no interval. A fall's bar is the
    rise's on the log scale, as the test uses it (a 5% bar is a fall of 1 - 1/1.05)."""
    ci = eff.get("ci") or [None, None]
    if eff.get("scale") != "fraction" or ci[0] is None or ci[1] is None:
        return None
    if not (ci[0] > 0 or ci[1] < 0):
        return {"kind": "unclear", "direction": None}
    rise = ci[0] > 0
    clear = False
    if bar is not None and bar > 0:
        clear = ci[0] >= bar if rise else ci[1] <= (1.0 / (1.0 + bar) - 1.0)
    return {"kind": "cleared" if clear else "moved", "direction": "rise" if rise else "fall"}


_GRADE_RANK = {"CONFIRMED": 0, "WATCH": 1, "NOT_ENOUGH_DATA": 3}


def _evidence_rank(f: Dict[str, Any]) -> int:
    """CONFIRMED 0; WATCH that clearly moved 1; other WATCH 2; NOT ENOUGH DATA 3."""
    r = _GRADE_RANK.get(f.get("grade"), 3)
    if f.get("grade") == "WATCH":
        mv = (f.get("watch") or {}).get("movement") or {}
        r = 1 if mv.get("kind") in ("moved", "cleared") else 2
    return r


def _materiality(f: Dict[str, Any]) -> float:
    """The size of a claim's change, for ordering: |effect| as a fraction; 0 when it has none."""
    e = f.get("effect") or {}
    v = e.get("estimate")
    return abs(float(v)) if e.get("scale") == "fraction" and v is not None else 0.0


def _count_word(n: int) -> str:
    """A small count in words, as the page's notes write it."""
    w = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    return w[n] if 0 <= n < len(w) else str(n)


def _pct_text(v: float) -> str:
    """A rate in percent as the trust sentences print it: two decimals under 1%, else one."""
    return ("%.2f%%" if abs(v) < 1.0 else "%.1f%%") % v


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    for s in items:
        s = _plain(s)
        if s and s not in out:
            out.append(s)
    return out


def _count_rows(data: bytes) -> int:
    """Records after the header, counted the way a CSV reader counts them (quoted line
    breaks stay inside their record; blank lines are skipped). Only the ASCII quote and
    line-break bytes matter, so latin-1 decoding is exact for this in any common encoding."""
    n = 0
    for rec in csv.reader(io.StringIO(data.decode("latin-1"), newline="")):
        if rec and any(c.strip() for c in rec):
            n += 1
    return max(0, n - 1)


def _clean_name(name: Any) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/")).strip() or "upload.csv"
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base)[:120] or "upload.csv"
    stem, ext = os.path.splitext(base)
    if ext.lower() not in ACCEPTED_SUFFIXES:
        if ext.lower() in (".xlsx", ".xlsm", ".xls", ".json", ".jsonl", ".pdf", ".zip"):
            raise Refusal("This demo reads CSV text files (.csv, .tsv or .txt). Save the sheet as "
                          "CSV and drop it here again.")
        base = (stem or "upload") + ".csv" if not ext else base + ".csv"
    return base


def _as_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    to_py = getattr(data, "to_py", None)          # a JS Uint8Array handed over by Pyodide
    if callable(to_py):
        return bytes(to_py())
    if isinstance(data, str):
        return data.encode("utf-8")
    raise Refusal("The file could not be read as bytes.")


def blank_report(name: str = "", data: bytes = b"") -> Dict[str, Any]:
    """The contract's shape with nothing measured: what a refusal returns. Every v2 key is
    present with its empty value (null, [] or 0 counts), so a page reads one shape."""
    sha = hashlib.sha256(data).hexdigest()
    return {
        "ok": False, "error": None,
        "engine": {"snapshot": "", "version": "", "semver": "", "decision_code_snapshot": "",
                   "environment": {"python": None, "numpy": None, "pandas": None, "sqlite": None,
                                   "pyodide": None},
                   "restated_since_previous": None, "benchmark": _blank_benchmark()},
        "input": {"name": name, "bytes": len(data), "rows": 0, "columns": 0, "sha256": sha},
        "timings": [],
        "privacy": {"flagged": [], "released": []},
        "health": {"score": None, "issues": [], "score_min": None, "score_mean": None, "weakest": None,
                   "csv_text_numbers": None, "explain": "",
                   "dimensions": [], "sample": {"method": "all", "n": 0, "seed": None}, "columns": [],
                   "missingness": {"matrix_columns": [], "by_month": [],
                                   "nullity_corr": {"columns": [], "matrix": [], "n": 0},
                                   "mcar": {"test": "little", "p": None,
                                            "conclusion": "not run in this release"}},
                   "accuracy": dict(ACCURACY_NOT_MEASURED)},
        "cleaning": {"rows_in": 0, "rows_clean": 0, "rows_quarantined": 0, "fixes": [],
                     "quarantine_reasons": [], "quarantine_by_month": [], "rules": []},
        "roles": {"date": None, "measures": [], "dimensions": [], "excluded": {}},
        "findings": [],
        "forecast": {"available": False, "reason": "", "verdict": None, "champion": None,
                     "baseline_won": None, "series": [], "forecast": [],
                     "backtest": {"mape": None, "mase": None, "coverage": None},
                     "band": None, "coverage": None, "baseline_test": None, "models": [],
                     "break": None, "interventions": [], "forecastability": None,
                     "decision_edge": None,
                     # P0-13 (wave 4): the shown forecast's rolling-origin back-test and its verdict, and the row
                     # count a table's layout or the calendar fixes, which is not forecast (CONTRACT 2.5, 5.11)
                     "audit": None, "trusted": None, "row_forecast_dropped": None},
        "story": {"headline": "", "what_happened": [], "why": [], "what_to_do": [],
                  "whats_next": [], "cannot_answer": []},
        "downloads": {"clean_csv": "", "quarantine_csv": "", "ledger_json": ""},
        "contract_version": CONTRACT_VERSION,
        "primary_metric": None,
        "tests_run": {"families": [], "claims_tested": 0},
        "methods": [],
        "limitations": [],
        "reproducibility": {"input_sha256": sha, "engine_snapshot": "", "decision_code_snapshot": "",
                            "environment": {}, "parameters": {}, "seeds": {}, "B": {},
                            "figures_reproduced": {"k": 0, "n": 0, "failed": 0}},
        "charts": [],
        "charts_suppressed": [],
        "llm": {"used": False, "model": None, "consent": False,
                "guard": {"passed": None, "rejected_numbers": [], "rejected_phrases": [],
                          "unbound_numbers": []}},
        # the manager's bottom line (fixer round, 24 Sep 2026): at most three sentences built from
        # the engine's facts (what moved and why, what to act on, the planning number), plain labels
        # for the claims, and the claims about the ledger's own line count, kept off the first screen
        "summary": {"lines": [], "labels": {}, "monitoring": []},
        # the scenario and contribution block (design B, plan/AI-INSIGHTS-DESIGN.md; engine/nl_scenarios.py):
        # the headline claim broken down by segment, price, volume and mix, per unit, run rate, sensitivity, gap
        # and the forecast added up, from the rows the engine kept, for the report writer to copy (CONTRACT §5.8)
        "scenarios": {"basis": None, "items": [], "refused": [], "note": ""},
        # the charts chosen from the data (the chart registry, engine/nl_viz.py; CONTRACT §5.9): nothing built yet
        "viz": {"version": VIZ_VERSION, "charts": [], "refused": [], "chosen_by": "none"},
        # a statistical table's structure (engine/nl_structure.py; CONTRACT §5.10) and what the headline is (§1): null
        # for a file that is not read by its structure
        "structure": None,
        "estimand": None,
    }


ACCURACY_NOT_MEASURED = {"measured": False, "audited_rows": 0, "errors": 0, "upper95": None,
                         "text": "not measured: no rows were checked against their source"}
BENCH_NOT_MEASURED = ("placebo_real", "power_at_bar_matched", "cross_env")
#: The false-confirm rate the engine aims at (design §1.5, "Target: X <= 1%"); the release check
#: certifies each no-change condition below a looser cap (benchmark.THRESHOLDS false_confirm_cap).
TARGET_FALSE_CONFIRM_PCT = 1.0
#: The one size of true change at which the benchmark receipt measures power (its "alt" cells).
POWER_SHIFT = 0.2
#: How far a file's own diagnostics may sit from the matched simulated condition before the page
#: calls it the "nearest" condition rather than one "like this file's": estimated momentum 0.1,
#: noise 5 points, the same months, and rows a month within x1.5 (1,000 or more counts as 1,000).
MATCH_CLOSE = {"phi": 0.1, "cv_pct": 5.0, "rows_ratio": 1.5}


def _blank_benchmark() -> Dict[str, Any]:
    return {"receipt": "northledger/benchmark_receipt.json", "snapshot": "", "available": False,
            "note": "", "matched_cell": None, "worst_cell": None, "forecast_coverage_80": None,
            "placebo_real": None, "power_at_bar_matched": None, "cross_env": None,
            "not_measured": list(BENCH_NOT_MEASURED),
            # the primary claim's own diagnostics beside the condition matched to them, how close
            # the match is, whether a rule holds the claim at WATCH, the release's certification of
            # its no-change conditions, and the measured power nearest the claim
            "file": None, "match": None, "routed": None, "certification": None, "power_matched": None,
            "target_pct": TARGET_FALSE_CONFIRM_PCT}


# --------------------------------------------------------------------------- the scrubber
_EDGE = " \t\r\n'\"`.,;:!?()[]{}<>"
_NUMBER_LIKE = re.compile(r"^[+-]?[$]?\s*[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\s*%?$")
_QUOTED = re.compile(r"'([^'\n]{1,400})'|\"([^\"\n]{1,400})\"")


def _norm(s: Any) -> str:
    return " ".join(str(s).split()).strip(_EDGE).lower()


# Common English words (a value that is one of them identifies no one): everyday words, and every word of six letters
# or more in the engine's and this adapter's own sentences (their string literals of four words or more), so a word
# the report itself writes is never taken for a withheld value. Only words of six letters or more are listed: a
# shorter single word is never looked for (Scrubber.SPECIFIC_MIN_CHARS). Names were taken out (a surname such as
# "Wilson" in "the Wilson interval" is not a common word), and so were technical tokens.
_COMMON_WORDS = frozenset("""
absent absolute absolutely absorbs accents accept accepted accepts access accident account accountant accounted
accumulates accurate across acting action actionable actions active activity actual actually actuals adapter
additive address adjusted admits adverse advertised advice advising affects affordable afternoon afterwards
against agency aggregate agreement aliases aligned allowed allowing allows almost alongside already alternative
although always amazing ambiguous amount amounts analysed analyses analysis analyst analyze annual annualised
anomalies another answer answers anybody anyone anything anyway anywhere apartment apology apostrophe appear
appears append appended applicable applied applies appreciate approve approved approximation argument arising
arithmetic around arrival arrived artefact article assembled assert asserted assigned associated association
assumed assumes assumptions attach attached attitude attractive attributes auditor audits august autumn
available average averaged averages awaiting awesome backed background balance barely baseline baselines baskets
beaten beautiful became because become becomes bedroom before behaviours behind belong belongs benchmark beside
besides better between beyond biased bigger biggest billed billing binary binned binomial birthday bisection
blanked blanket blanking blocks boolean borough borrowed bottle bottom bought boundary bounds branch breakage
breakdown breaks brilliant brings broken brother brought browser buckets budget building buildings builds
business businesses button buying cached calculation calendar calibrated calibration called caller callers
calling cancel canceled cancelled candidate candidates cannot capital capped captured cardinality carried
carrier carries carrying cartridge casing casual categories category caught caused caution cautioning cautious
caveat caveats census centre centred certain certification certified certifies certify chaining champion chance
change changed changes changing characters charted charts cheaper checkable checked checks children choice
choices choose chooses choosing chosen cinema citations claimed claims classified clause clauses cleaned cleaner
cleaning cleans cleared clearer clearly clears client climate closed closer cluster coarsest coefficient
coefficients collapse collapsed collapses collapsing collected collection collision column columns combining
comfort comfortable coming command commit common commonest compact company comparable compare compared compares
comparing comparison comparisons compatible compiled complain complaint complete completed completeness
composite composition computed computer computes condition conditional conditions confidence confident
configuration confirm confirmation confirmed confirming confirms confounded confounder confounders connected
connection consecutive consent conservation conservatism conservative consistency console constant construction
consultant contact contain content context contiguous continued contract contradict contradiction contribution
contributions control controls convention conventions conversion conversions converted cooking coordinate copies
correct corrected correction correlation cosmetic cotton counted counter counterfactual counting countries
country counts county couple course cousin covariance coverage covered covers create credit criteria critical
currencies currency current customer customers damaged damped dashed database dataset daughter decade decaying
december decibels decide decided decides deciding decimal decimals decision decisions declared decode decoded
decoding default defect defective defence defensive defensively defined definition degrees delete deleted
deletion deliberately deliver delivered delivery denominator density departure depend dependence dependent
depends deprecation derivation derive derived describe described describes description descriptive design
desktop detail details detected determinism deterministic deterministically development deviation deviations
diagnostics differ differed difference different differently differs digest digits dimension dimensions dinner
direction directions disagree disagreeing discovery distance distilled distinct distinctness distribution
divided dividing division doctor document dollar dollars dominance dominant dominate double doubled doubles
doubling download downloads downstream drifting drifts driven driver drivers drives dropped dropping duplicate
duplicated duplicates duplication during earlier earned easily echoes editor effect effective effects either
elaborate element eleven emails emitted employee encoding energies energy enforce engagement engine enjoyed
enjoying enough enrichment entered entering enters entirely entities entitled entity entries equals equilibrium
equivalent errors escapes especially established estimand estimate estimated estimating evaluate evaluated
evaluation evening events everybody everything everywhere evidence exactly example examples exceed exceedance
excellent except exception exchangeable exclude excluded executing executive exists exited expected expects
expensive expiry explain explained explaining explains explanation explicit explodes export exported expose
exposes expressed expression extend extended extends external extract extreme extremes fabulous facing factor
factors factory failed failing failure fallback fallen falling falsely falsification falsifies families family
famous fantastic faster fastest father feature february feedback female fewest fields figure figures filler
filter filtered filters finally finding findings finite fitted fixtures flagged flattening floating flower
folded folder follow followed follows footnote forecast forecasting forecasts foreign format formats formatted
formatting formed formula forward fourth fraction fractions freedom freehand freeze frequent friday friend
friendly friends fullest function functions further furthest future gaming garden gating general generated
generates generating generator gentle genuinely geography gorgeous graded grades granddaughter grandma grandson
greatest grouped groups growing growth guarantee guarded guessed guessing hallucinated halves handed handful
handled happened happening happens happily hardest harmonic hashes having header headers headings headline
headset health healthy heuristic hidden hiding higher highest history holding holiday honest honesty honour
honouring horizon horrible hospital however hundred husband hyphen hypotheses hypothesis identical identifier
identify images implausibly implements import imported imports impossible inactive include included includes
including inclusive incomplete inconsistent independence independent independently indexes indicator indicators
indices inference inferred inform information initials innovation innovations inputs insensitively inside
insight inspections installed instance instances instead instruction instructions intact intake integer integral
integration internal interpolate interpolated interval intervals invalid invariant invent invents inverse
inversion inverting inverts invoice irregular issued issues itself jacket january judged judges keeping kitchen
labelled labels ladies landed landing language larger largest latest layout lazily leading leaned learned
leather leaves leaving ledger ledgers legitimately length letter letters levels library licenses likelier
likelihood likely limitations limits linear linearly listed little longer looked looking loosening looser lovely
lowers lowest magnitude malformed manager manual mapping marginal marked marker market marking matched matches
matching material materiality matrix matter matters maximum meaning meaningless measure measured measurement
measurements measures mechanical mechanism median medium member members memory mention merely merged message
metadata method methods metric middle midnight million millions milliseconds minimum minimums minute minutes
mirror mirrored mismatch misread misreading missed misses missing mistaken mistyped mobile modelled models
modified module modules moment momentum monday monitor monitoring monotone monthly months morning mostly mother
movement moving multiplicative multiplicatively multiplied mutate naming nanosecond narrate narrated narrating
narrative narrator narrow narrower nearest nearly needed negative neither nephew nested nevermind newest nobody
nominal nonpositive normal normalisation normalised normalises normals nothing november nowhere number numbered
numbers numeric numerical object objective objects observable observation observational observed offered offers
office offset omission online opaque opened operation operations opposite optimal option optional orange ordered
ordering orders ordinary origin original origins others otherwise outcome outcomes outlier outnumber output
outputs outside overall overlapping overstate overwrite packed padded padding paired pairwise parameter
parametric parentheses parsed parsing partial particles particular passed passes passing pattern payload
payloads peaked peeking penalised penalties penalty pending people percent percentage percentages percentile
percentiles percents period periods permutation persistence persistent person personal phrase phrased phrases
picked picking pieces pinned pipeline placed placeholder placeholders places planner planning plausible pleased
plural plurals pocket pointed points policy polite population position positionally positions positive possible
precedence precisely precision predict predicted prediction predicts preferred prefix premium presence present
presentation preserving pretending pretty prevent previous prices primary printed printing prints priority
privacy private probabilities probability probable probably problem problems procedure process processes produce
produced produces product products profile profiled profiler profiles profiling project projects promise
promised promises proportion proposed provenance provide provided provider provisional public published pulled
punctuation purchase purchased purely purple purpose pushed qualifiers qualifies quality quantile quantiles
quantities quantity quarantine quarantined quarantines quarterly quarters queries question quickly quietly
quoted quotes quoting raises random ranged ranges ranked ranking rather rating ratings ratios reaches reaching
readable reader readers reading realised reality really reason reasons rebuilt receipt receipts received
receives recent recipe recognised recommend recommendation recommendations recommended recommending recommends
recompute recomputed reconcile reconciled record recorded records recovered recovering recovers redaction reduce
reduced reference referred refits refund refunded refusal refusals refuse refused refuses refusing region
regions register registered registers regression regular regularised reject rejected rejection rejections
rejects relationship relative release releases reliable relying remain remaining remains remote removed removes
renamed rendering renders repair repaired repairs repeat repeated repeating repeats replaced replacement
replaces replay replayed replays replicated replication replications report reported reporters reports
repository representable represented reproduce reproduced reproduces request require required requirement
requires reshaped residual resolution resolved resolves respect restated restatement restatements resting
restored restricted restricts result resulting results resumes retained retention return returned returning
returns reveal revenue reversal reversals review reviewable reviews rewrite rewritten robust rolling roughly
rounded rounding routed routing rumour runner running runtime salary sample sampled samples sampling sanity
saturday saying scaled scales scanned scanner scenario scenarios sceptical scheduled schema school scored scores
scoring screen script scrubber scrubs search searched searches season seasonal seasonality second secondary
seconds secret section sector secure segment segments selection seller semantic sensible sensitivity sentence
sentences sentinel separate separated separately separator separators sequence serialised series serves service
setting settle settled several severity shaped shapes shared shares sharing sharper shifted shifts shipped
shipping shocks shopping shortened shorter shortfall should shuffle shuffles signal signals signed significance
significant silent silently simple simplest simulate simulated single sister skipped sleeve slightly smaller
smallest smooth snapshot software someone something somewhat sorted source sourced sources spaces spacing sparse
sparser speaks special spelling spellings spends splits splitting spread spreads spring square squared squares
stable stages staleness stamped stamps standard stands started starting starts stated statement statements
states stating stationary statistic statistical statistically statistics status stayed steadier steadily steady
stepped sticky stopped stopping storage stored straddles straight stream street stress stretch strict stricter
string strings strong stronger strongest structural structured stylish subset substring successes suffix
suffixes summary summed summer sunday superb superseded supply support supported supports supposed surprise
survive survived survives suspect swapped sweater switch system tables tablet tailed target targeted targets
temperatures temporary terrible testable tested thankful thanks themes themselves theorem therefore things
thirds thousands threshold thresholds through throughout thursday ticket timeliness timestamps timezone timing
timings tipping together tokens tolerance tolerated tomorrow tonight totals touched toward towards tracked
tracking trailing trained training transactions transform transition translates translation travel treated
treats trends trials trimmed trimming triples tripped truncated trusted trusting tuesday tunable turned turning
twelve typical typically unambiguous unanswerable unanswered unchanged unclear uncomfortable unconditional
uncorrected undated undecodable undefined underestimate underneath understates undoing unexpected ungated
unhappy unidentified unique uniqueness unknown unless unmodelled unmodified unquoted unread unreadable
unrecorded unrelated unreproduced unrounded untested untouched untrimmed unverifiable upgrade upload usable
useful useless usually vacuous validation validity values vanished variance variant variants variation varies
verbatim verdict verdicts verification verified verify version versus vetted visible visitor volume waiting
wandering wanders warning wasted watched watching weakest wearing wednesday weekday weekdays weekend weekends
weighed weighs weighted weights whatever whenever wherever whether whitening whitespace whoever wholly widened
widening widens widest window windows winter wireless withheld withhold withholds within without wonderful
worded wording worked worker workers working worthless writer writes writing written yearly yellow yields
adjacent autocorrelation autocorrelations bootstrap demeaned ending estimator leaning margin overlap preliminary
publisher residuals revised revisions steeper taking
""".split())


def _specific(n: str, free_text: bool = False) -> bool:
    """A normalised value (_norm) specific enough to scrub from text: a free-text column's value only whole and 20
    characters or more; any other value 2 words or more, or one word of 6 characters or more that is not a common
    English word; never a number."""
    if not n or _NUMBER_LIKE.match(n):
        return False
    if free_text:
        return len(n) >= Scrubber.FREE_TEXT_MIN
    if len(n.split()) >= 2:
        return True
    return len(n) >= Scrubber.SPECIFIC_MIN_CHARS and n.strip(_EDGE) not in _COMMON_WORDS


class Scrubber:
    """Finds a withheld column's values inside text and replaces them with [withheld].

    Values are compared whole, after collapsing whitespace, trimming punctuation at the
    edges and lower-casing: every quoted span and every run of up to 8 words is looked up
    in a set, so the cost grows with the text, not with the number of values. Only a
    SPECIFIC value is looked for (_specific): a value that identifies no one would scrub
    ordinary words out of the engine's own sentences (the live baseline of 30 Sep 2026: one
    review whose whole text was "this" turned "a result at least this strong" into "a result
    at least [withheld] strong", 62 times in one PDF). A value of a free-text column (the
    `free_text` values) is looked for only when it is 20 characters or more (FREE_TEXT_MIN);
    any other value when it has at least 2 words or at least 6 characters, and is not a
    common English word (_COMMON_WORDS) or a number (a withheld salary column's 52,000 must
    not blank out a real figure that happens to be equal)."""

    MAX_WORDS = 8
    FREE_TEXT_MIN = 20
    SPECIFIC_MIN_CHARS = 6
    # the exact tokens of every flagged column's values (_decide_and_guard), all together and by landed column: never
    # scrubbed from text, read by the themes and the chart registry's labels
    flag_tokens: FrozenSet[str] = frozenset()
    flag_tokens_by: Dict[str, FrozenSet[str]] = {}

    def __init__(self, values: Iterable[Any] = (), free_text: Iterable[Any] = ()) -> None:
        self.values: Set[str] = set()
        self.words = 1
        for vals, free in ((values, False), (free_text, True)):
            for v in vals:
                n = _norm(v)
                if not _specific(n, free):
                    continue
                self.values.add(n)
                self.words = max(self.words, min(self.MAX_WORDS, len(n.split())))

    def __bool__(self) -> bool:
        return bool(self.values)

    def spans(self, text: str) -> List[Tuple[int, int]]:
        if not self.values or not text:
            return []
        out: List[Tuple[int, int]] = []
        for m in _QUOTED.finditer(text):
            g = 1 if m.group(1) is not None else 2
            if _norm(m.group(g)) in self.values:
                out.append((m.start(g), m.end(g)))
        toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        for i in range(len(toks)):
            for k in range(1, self.words + 1):
                if i + k > len(toks):
                    break
                a, b = toks[i][0], toks[i + k - 1][1]
                raw = text[a:b]
                if _norm(raw) in self.values:
                    lead = len(raw) - len(raw.lstrip(_EDGE))
                    trail = len(raw) - len(raw.rstrip(_EDGE))
                    out.append((a + lead, b - trail))
        return out

    def found(self, text: str) -> bool:
        return bool(self.spans(text))

    def clean(self, text: Any) -> Any:
        if not isinstance(text, str) or not self.values:
            return text
        spans = sorted(self.spans(text))
        if not spans:
            return text
        merged: List[List[int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        out, pos = [], 0
        for a, b in merged:
            out.append(text[pos:a])
            out.append(WITHHELD_MARK)
            pos = b
        out.append(text[pos:])
        return "".join(out)

    def clean_tree(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.clean(obj)
        if isinstance(obj, list):
            return [self.clean_tree(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.clean_tree(v) for k, v in obj.items()}
        return obj


def _withheld_values(db_path: str, table: str, columns: List[str], dates: bool = True) -> List[str]:
    """Every distinct value of the withheld columns as landed, plus (dates=True) the ISO form of any
    that reads as a date (the profiler prints dates as YYYY-MM-DD). A free-text column's values are read
    with dates=False: its short values are never looked for (Scrubber.FREE_TEXT_MIN), an ISO date among them."""
    if not columns:
        return []
    import pandas as pd
    from northledger._sqlite import connect_ro
    con = connect_ro(db_path)
    out: List[str] = []
    try:
        for c in columns:
            qc = '"%s"' % c.replace('"', '""')
            vals = [str(r[0]) for r in con.execute('SELECT DISTINCT %s FROM "%s" WHERE %s IS NOT NULL'
                                                    % (qc, table.replace('"', '""'), qc))]
            out.extend(vals)
            if vals and dates:
                with contextlib.suppress(Exception):
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        d = pd.to_datetime(pd.Series(vals), errors="coerce")
                    out.extend(x.date().isoformat() for x in d.dropna())
    finally:
        con.close()
    return out


def _issue_is_safe(line: str, withheld: Set[str]) -> bool:
    """A profile line about a withheld column may give counts and shapes ("notes: 12 values
    carry whitespace"), never a reading of its values. The whole-table lines that name a
    column describe its values ("Newest row in date_of_birth is 1994-12-16, 10,952 days old"),
    and a value can be worked back from an age, so such a line is left out."""
    for c in withheld:
        if line.startswith("%s: " % c):
            continue
        if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(c), line):
            return False
    return True


# Numbers stored as text (reviews of the evaluation preflight, 30 Sep 2026). The engine's health check marks a number
# column's validity down when its numbers are stored as text (northledger/health.py: validity x (1 - 0.5 x the share),
# and the line "amount: 3,288 of 3,288 numbers are stored as text; they will sort '10' before '9'."). Every file this
# page reads is CSV text (ACCEPTED_SUFFIXES), landed as text, so the line is true of every number column of every
# upload and says nothing about this one; and it is not true of the analysis, which reads those values as numbers
# (the cleaner converts them), so no figure here sorts '10' before '9'. The adapter leaves the line out of the issues
# it shows and sends (no engine file is changed). The core's score keeps the mark-down: when it lowers the weakest
# dimension or the mean, health.csv_text_numbers says so in plain words, and the page prints it in the Data health
# area (engine/CONTRACT-v2.md 2.2).
_TEXT_NUMBERS_RE = re.compile(r"^(?P<col>.+?): [\d,]+ of [\d,]+ numbers are stored as text; they will sort '10' before '9'\.?$")
TEXT_NUMBERS_WORDS = "the score counts numbers stored as text, which every CSV has"
# the same for the report writer (results_for_ai.health_issues, 200 characters at most): its health_score is the
# weakest dimension, so this line goes only when the mark-down lowers that one (validity)
TEXT_NUMBERS_AI = ("The health score counts numbers stored as text, which every CSV has: it is validity, the weakest "
                   "dimension here, marked down for them, although the engine reads them as numbers.")


def _is_text_numbers_line(line: Any) -> bool:
    """True for the engine's "N of N numbers are stored as text; they will sort '10' before '9'" line."""
    return isinstance(line, str) and bool(_TEXT_NUMBERS_RE.match(line.strip()))


def _text_numbers(th: Any, dims: Dict[str, Any], score_min: Any, score_mean: Any, withheld: Set[str],
                  pub: Any = None) -> Optional[Dict[str, Any]]:
    """health.csv_text_numbers: {columns, lowers {validity, score_min, score_mean}, note}, or None when no number
    column of the file is marked down for numbers stored as text. Whether the mark-down lowers a score is read by
    undoing it with the engine's own constant and rounding (health._NUM_AS_TEXT_PENALTY, health._pct) and the
    cleaner's validity cap as the engine applies it; the report prints no counterfactual number, only which score
    the mark-down lowers. The note names at most three columns (never a withheld one)."""
    from northledger import health as _health
    marked = [c for c in (getattr(th, "columns", None) or [])
              if getattr(c, "dtype_guess", "") == "numeric" and int(getattr(c, "n_numeric_as_text", 0) or 0) > 0]
    if not marked:
        return None
    pen = float(getattr(_health, "_NUM_AS_TEXT_PENALTY", 0.5))
    ids = set(id(c) for c in marked)
    vals = []
    for c in getattr(th, "columns", None) or []:
        v = getattr(c, "validity", None)
        if v is None:
            continue
        v = float(v)
        n_num = int((getattr(c, "class_counts", None) or {}).get("numeric", 0) or 0)
        if id(c) in ids and v > 0 and n_num > 0:
            f = 1.0 - pen * (int(c.n_numeric_as_text) / float(n_num))   # the factor health.py multiplied in
            v = min(1.0, v / f) if f > 0 else v
        vals.append(v)
    cur_v = dims.get("validity")
    lowers = {"validity": False, "score_min": False, "score_mean": False}
    if vals and cur_v is not None:
        undone = _health._pct(sum(vals) / len(vals))
        cap = (getattr(th, "validity_cap", None) or {}).get("cap")
        if cap is not None and float(cap) < undone:
            undone = float(cap)
        if undone > float(cur_v) + 1e-9:
            lowers["validity"] = True
            other = [float(v) for k, v in dims.items() if v is not None and k != "validity"]
            new_min = min(other + [undone])
            lowers["score_min"] = score_min is not None and new_min > float(score_min) + 1e-9
            new_mean = round((sum(other) + undone) / (len(other) + 1), 1)
            lowers["score_mean"] = score_mean is not None and new_mean > float(score_mean) + 1e-9
    names = [str(c.name) for c in marked if str(c.name) not in withheld]
    shown = [str(pub(n)) if pub is not None else n for n in names[:3]]
    where = ", ".join(shown[:-1]) + " and " + shown[-1] if len(shown) > 1 else (shown[0] if shown else "")
    if len(names) > 3:
        where = "%s and %s" % (", ".join(shown), _n_values(len(names) - 3, "more column"))
    note = ""
    if lowers["score_min"] or lowers["score_mean"]:
        note = "%s%s: %s for the numbers in %s%s, although the engine reads them as numbers." % (
            TEXT_NUMBERS_WORDS[0].upper(), TEXT_NUMBERS_WORDS[1:],
            "validity, the weakest dimension here, is marked down" if lowers["score_min"] else "validity is marked down",
            where or "the number columns", "" if lowers["score_min"] else ", which lowers the mean of the five")
    return {"columns": names, "lowers": lowers, "note": note}


# health.explain (final evaluation, 1 Oct 2026): the reviews PDF printed "Data health score 0.0" for a file whose other
# checks scored 99.6 to 100, because its newest row was 3.5 years old, and nothing said so. The score is the weakest
# dimension (CONTRACT-v2 §4.2); the explanation names it, says why in plain words, and gives the other checks' average.
# The core's score is unchanged. The writer's health issues start with it (HEALTH_EXPLAIN_AI) when the weakest dimension
# sits HEALTH_EXPLAIN_GAP points or more under the others' average, unless the numbers-stored-as-text line already says
# why (_text_numbers).
HEALTH_EXPLAIN_AI = "The health score is %s"
HEALTH_EXPLAIN_GAP = 10.0
_NEWEST_RE = re.compile(r"^Newest row in .+ is \d{4}-\d{2}-\d{2}, (\d+) days old as of \d{4}-\d{2}-\d{2}\.?$")
_FUTURE_RE = re.compile(r"^(\d+) of (\d+) dated rows are in the future")


def _score_words(v: float) -> str:
    """A score as a sentence gives it: one decimal, a whole number without its ".0" (0, 99.9, 100)."""
    return ("%.1f" % float(v)).rstrip("0").rstrip(".")


def _age_words(days: int) -> str:
    if days >= 365:
        y = _score_words(days / 365.25)
        return "1 year" if y == "1" else "%s years" % y
    if days >= 60:
        return "%d months" % int(round(days / 30.4375))
    return "%d days" % days


def _health_info(th: Any, ev: Dict[str, Any], idlike: bool) -> Dict[str, Any]:
    """What the engine's own checks found, for _health_explain: exact duplicate rows, the rows, an id-like column that
    repeats, the newest row's age in days and the future-dated rows (the engine's own lines, health._timeliness)."""
    dup = ev.get("duplicate_rows")
    info: Dict[str, Any] = {"duplicate_rows": int(float(dup["value"])) if dup else None,
                            "rows": int(getattr(th, "n_rows", 0) or 0), "id_like": bool(idlike), "newest": None,
                            "future": None}
    for line in getattr(th, "findings", None) or []:
        t = _plain(line)
        m = _NEWEST_RE.match(t)
        if m:
            info["newest"] = int(m.group(1))
        m = _FUTURE_RE.match(t)
        if m:
            info["future"] = (int(m.group(1)), int(m.group(2)))
    return info


def _health_explain(dims: List[Dict[str, Any]], score_min: Any, weakest: Any, info: Dict[str, Any],
                    tn: Optional[Dict[str, Any]] = None) -> str:
    """health.explain: the dimension that set the score and why, e.g. "0 because the newest row is 3.5 years old (the
    timeliness check); the other checks averaged 99.9."; "" with no score. No column is named."""
    appl = [d for d in dims if d.get("applicable") and d.get("score") is not None]
    if score_min is None or not appl:
        return ""
    if all(float(d["score"]) >= 100.0 for d in appl):
        return "%s: every check the engine ran scored 100." % _score_words(score_min)
    w = str(weakest or "")
    if w == "timeliness":
        parts = []
        age, fut = info.get("newest"), info.get("future")
        if age is not None and age > 30:
            parts.append("the newest row is %s old" % _age_words(int(age)))
        if fut and fut[0]:
            parts.append("%s of %s dated rows are dated after the analysis date" % (format(fut[0], ","), format(fut[1], ",")))
        why = " and ".join(parts) or "the dates are not recent"
    elif w == "completeness":
        why = "%s of the cells are empty or a placeholder" % _pct_text(100.0 - float(score_min))
    elif w == "uniqueness":
        k, n = info.get("duplicate_rows"), info.get("rows")
        why = ("%s rows (%s) repeat another row exactly" % (format(int(k), ","), _pct_text(100.0 * k / n)) if k and n
               else "a column named like a key repeats some of its values" if info.get("id_like")
               else "some rows repeat another row")
    elif w == "validity":
        # the numbers-stored-as-text mark-down (_text_numbers) when it is what lowers this score, else the values
        why = ("the check marks down numbers stored as text, which every CSV has, although the engine reads them as numbers"
               if tn and (tn.get("lowers") or {}).get("score_min") else "some values do not read as their column's main type")
    elif w == "consistency":
        why = "some columns write the same value with different case or spacing"
    else:
        why = "it is the lowest of the engine's checks"
    s = "%s because %s (the %s check)" % (_score_words(score_min), why, w or "lowest")
    others = [float(d["score"]) for d in appl if d.get("name") != w]
    if others:
        s += "; the other checks averaged %s" % _score_words(math.fsum(others) / len(others))
    return s + "."


def _health_gap(health: Dict[str, Any]) -> float:
    """How far the weakest dimension sits under the other checks' average (0 when there is nothing to compare)."""
    appl = [d for d in health.get("dimensions") or [] if isinstance(d, dict) and d.get("applicable")
            and isinstance(d.get("score"), (int, float))]
    w, lo = health.get("weakest"), health.get("score_min")
    others = [float(d["score"]) for d in appl if d.get("name") != w]
    return (math.fsum(others) / len(others) - float(lo)) if others and isinstance(lo, (int, float)) else 0.0


def _exact_duplicates(db_path: str, table: str) -> int:
    """Rows that repeat an earlier row exactly, as landed. The cleaner compares rows after
    trimming and re-casing, so it finds at least this many: a floor, used only for the
    DEDUPE_BUDGET guard."""
    from northledger._sqlite import connect_ro
    con = connect_ro(db_path)
    try:
        qt = '"%s"' % table.replace('"', '""')
        cols = ", ".join('"%s"' % r[1].replace('"', '""')
                         for r in con.execute("PRAGMA table_info(%s)" % qt))
        n = con.execute("SELECT COUNT(*) FROM %s" % qt).fetchone()[0]
        distinct = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT %s FROM %s)" % (cols, qt)).fetchone()[0]
        return int(n) - int(distinct)
    finally:
        con.close()


# --------------------------------------------------------------------------- what a visitor reads
_PHONE_RE = re.compile(r"(?<![\d-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def public_text(text: Any, table: str = "", name: str = "") -> Any:
    """A text field as the page shows it: the engine's internal table name replaced by the
    file's name, and any phone number or email address replaced by a label. It removes figures
    and never adds one."""
    if not isinstance(text, str):
        return text
    if table and name:
        text = text.replace(table, name)
    text = _EMAIL_RE.sub("[email address]", text)
    return _PHONE_RE.sub("[phone number]", text)


def _looks_like_title(data: bytes) -> bool:
    """True when the first row holds one or two cells and the rows after it hold many: a report
    title above the real header, which the reader would take as the column names."""
    head = data[:65536].decode("utf-8", errors="replace").lstrip("\ufeff")
    best = (0, 0)
    for delim in (",", ";", "\t", "|"):
        filled = []
        try:
            for rec in csv.reader(io.StringIO(head, newline=""), delimiter=delim):
                n = sum(1 for c in rec if c.strip())
                if n:
                    filled.append(n)
                if len(filled) >= 7:
                    break
        except csv.Error:
            continue
        if len(filled) >= 2 and max(filled[1:]) > best[1]:
            best = (filled[0], max(filled[1:]))
    first, width = best
    return width >= 3 and first <= max(1, width // 4)


def _record_lines(data: bytes) -> List[int]:
    """The 1-based line on which each data record starts, counted as a text editor counts
    lines (a quoted line break inside a value moves the next record down). Blank lines and the
    header are skipped, as the reader skips them; a line of empty cells (",,,") is a row."""
    reader = csv.reader(io.StringIO(data.decode("latin-1"), newline=""))
    out: List[int] = []
    while True:
        before = reader.line_num
        try:
            rec = next(reader)
        except StopIteration:
            break
        except csv.Error:
            return []
        if rec and (len(rec) > 1 or rec[0].strip()):     # a row of empty cells is still a row
            out.append(before + 1)
    return out[1:]


def _ambiguous_dates(cr: Any, rules: List[Any], renamed: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    """The engine's set-aside reasons, with the rows its date rule set aside because their day
    and month could be either way round counted under that reason (the engine's own row-level
    words), instead of under "value matches no known date format". A reason every row of which
    moved is recorded in `renamed` (old words: new words), so the story can say the same."""
    from northledger.clean import QUARANTINE_COL
    reasons = {k: int(v) for k, v in (cr.quarantine_reasons or {}).items()}
    q = cr.quarantined
    if q is None or QUARANTINE_COL not in getattr(q, "columns", []):
        return reasons
    msgs = [str(m) for m in q[QUARANTINE_COL].tolist()]
    for rule in rules:
        if rule.kind != "date" or not rule.column or rule.column == "*":
            continue
        key = rule.reason_key()
        lead = "column %r:" % rule.column
        n = sum(1 for m in msgs if m.startswith(lead) and "could be day/month or month/day" in m)
        if n and reasons.get(key, 0) >= n:
            new = ("%s: could be day/month or month/day, and this column holds both orders, so these "
                   "dates were not read by guessing" % rule.name)
            reasons[key] -= n
            if not reasons[key]:
                del reasons[key]
                if renamed is not None:
                    renamed[key] = new
            reasons[new] = n
    return reasons


# --------------------------------------------------------------------------- privacy
_REASON_KIND = None


_REASON_LABEL = {"name": "named like personal data", "free_text": "free text",
                 "weak": "some values look personal", "unconfirmed": "values shaped like personal data",
                 "business_place": "business contact details", "no_header": "no header row",
                 "auto_redact_off": "personal data", "people": "people's names"}
_KIND_LABEL = {"email": "email", "credit_card": "card number", "phone_na": "phone number",
               "phone_intl": "phone number", "sin_ssn": "SIN or SSN", "postal_ca": "postal code",
               "ip": "IP address", "person_name": "person's name", "street_address": "street address",
               "account_number": "account or card number", "id_number": "long ID number",
               "sensitive_category": "sensitive category (named by its header)"}


def _kind_of(col: str, kinds: str, res: Any) -> str:
    """A short label for why a column was flagged: the intake's own review reason (free
    text, named like personal data...), else the kind of value the scanner matched."""
    global _REASON_KIND
    if _REASON_KIND is None:
        from northledger import intake as _intake
        _REASON_KIND = {v: k for k, v in getattr(_intake, "_REVIEW_REASONS", {}).items()}
    key = _REASON_KIND.get((getattr(res, "review_reasons", None) or {}).get(col, ""), "")
    if key in _REASON_LABEL:
        return _REASON_LABEL[key]
    labels = []
    for k in (kinds or "").split(","):
        k = k.strip()
        if not k:
            continue
        lab = _KIND_LABEL.get(k) or (("named like " + k[len("named_"):].replace("_", " "))
                                     if k.startswith("named_") else k.replace("_", " "))
        if lab not in labels:
            labels.append(lab)
    return ", ".join(labels) or "possible personal data"


def _apply_decisions(E: Any, eng: Any, res: Any, decisions: Optional[Dict[str, Any]]
                     ) -> List[Dict[str, str]]:
    """Every flagged column gets a decision: the visitor's, else withhold. A column the
    scanner already coded at landing cannot be un-coded; asked to keep it, it stays coded."""
    import sqlite3
    wanted: Dict[str, str] = {}
    if decisions:
        colmap = dict(getattr(res, "column_map", {}) or {})
        for k, v in dict(decisions).items():
            d = str(v or "").strip().lower()
            if d not in DECISIONS:
                continue
            wanted[str(k)] = d
            if str(k) in colmap:
                wanted[colmap[str(k)]] = d
    con = sqlite3.connect(eng.db_path)
    try:
        rows = con.execute("SELECT column_name, kinds, decision FROM %s WHERE table_name = ? "
                           "ORDER BY rowid" % E.COLUMNS_TABLE, (res.table,)).fetchall()
    finally:
        con.close()
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for col, kinds, current in rows:
        if col in seen:
            continue
        seen.add(col)
        want = wanted.get(col, "withhold")
        if current == "coded":
            if want == "withhold":
                E.decide(eng, col, "withhold", table=res.table, note="withheld by the visitor")
                effective = "withhold"
            else:
                effective = "code"
        else:
            E.decide(eng, col, want, table=res.table,
                     note="decided by the visitor" if col in wanted else "withheld by default")
            effective = want
        kind = _kind_of(col, kinds or "", res)
        if current == "coded":
            kind += "; " + CODED_ON_ARRIVAL
        out.append({"column": col, "kind": kind, "decision": effective})
    return out


def _effective_decisions(flagged: List[Dict[str, str]], colmap: Dict[str, str], decisions: Any) -> Dict[str, str]:
    """{landed column: decision} as _apply_decisions would settle it for these flagged columns: the visitor's
    choice by the landed name or the file's own header, else withhold; a column coded as it arrived is never
    kept (keep reads as code)."""
    wanted: Dict[str, str] = {}
    for k, v in dict(decisions or {}).items():
        d = str(v or "").strip().lower()
        if str(k).startswith("__") or d not in DECISIONS:
            continue
        wanted[str(k)] = d
        if str(k) in colmap:
            wanted[colmap[str(k)]] = d
    out: Dict[str, str] = {}
    for f in flagged:
        want = wanted.get(f["column"], "withhold")
        if CODED_ON_ARRIVAL in str(f.get("kind") or ""):
            want = "withhold" if want == "withhold" else "code"
        out[f["column"]] = want
    return out


# --------------------------------------------------------------------------- the adapter's personal-column check
# The engine's scan flags a column by a name hint or by the shape of its values (emails, phone numbers, SINs,
# people's names whose first word is on its list of common given names). Reviews of 29 Sep 2026: a "member"
# column of people's names ("Marisol Fairweather") passed both, and so did lower-case names, initials, names in
# other scripts, "Fairweather, Marisol", and names under "technician", "assigned_to" or "nombre". This check runs
# AFTER the engine's scan and only ever ADDS a column to the visitor's choices, with the default every flagged
# column has (withhold); it never replaces or clears an engine flag. A missed column is the costly mistake (its
# values reach the planner and the report writer); a column flagged in error costs the visitor one click (Keep).
# A column joins when
#   most of its values are email addresses, phone numbers written with separators, account or card numbers
#   written in groups of four digits, or street addresses, or
#   its NAME says it holds people (_person_hint: a word for a person or a person's name, in English or another
#   common language, as the column's last word; a person's name ("customer_name", "nombre"); "created_by",
#   "assigned_to") AND most of its values could be a person's name (_person_value: 1 to 4 words of letters in
#   any script or case, with initials, particles and the comma form, and no word that says firm, role, tier,
#   software or a way to pay).
# A column of names under a heading this check does not know ("Stylist", "Crew") can still be missed, and the
# page says so.
PERSONAL_MIN_SHARE = 0.60
PERSONAL_ID_MAX_DISTINCT = 400          # wave 5d: a column of 9 to 19 digit numbers that names at most this many things ...
PERSONAL_ID_MAX_SHARE = 0.30            # ... on at least 3 cells in 10 each (not a column of measures) is a column of ID numbers
# words for a person, as a column's (last) word says it: in the file's own spelling, lower case, accents
# dropped and plurals folded as _header_tokens gives them
_PERSON_WORDS = frozenset((
    # English
    "member", "customer", "cust", "client", "person", "people", "patient", "employee", "contact", "owner",
    "tenant", "guest", "user", "username", "manager", "attendee", "instructor", "technician", "rep",
    "representative", "salesperson", "salesman", "saleswoman", "salesrep", "agent", "staff", "driver", "cashier",
    "buyer", "recipient", "payee", "spouse", "teacher", "student", "doctor", "nurse", "parent", "guardian",
    "beneficiary", "holder", "cardholder", "signer", "approver", "supervisor", "worker", "volunteer", "player",
    "participant", "applicant", "candidate", "author", "sender", "requester", "submitter", "assignee", "who",
    "resident", "occupant", "landlord", "subscriber", "donor", "borrower", "physician", "therapist", "consultant",
    "advisor", "adviser", "coach", "trainer", "caller", "reviewer", "inspector", "clerk", "homeowner",
    # wave 5f (C): the people who work a case or a sale, named by their job (a header "Analyst" over a column of names)
    "analyst", "officer", "specialist", "associate", "assistant", "executive", "director", "coordinator", "engineer",
    "planner", "underwriter", "adjuster", "auditor", "broker", "operator", "teller", "dentist", "surgeon",
    "clinician", "pharmacist", "lawyer", "attorney", "accountant", "controller", "foreman", "seller", "mentor",
    "tutor", "trainee", "intern", "handler", "caseworker", "colleague", "instructor", "bartender", "barista",
    # Spanish, Portuguese, Italian, French
    "cliente", "empleado", "miembro", "socio", "paciente", "usuario", "vendedor", "persona", "huesped",
    "funcionario", "utente", "dipendente", "membre", "employe", "utilisateur", "personne", "vendeur", "locataire",
    # German, Dutch, Nordic, Polish, Turkish
    "kunde", "kunden", "mitarbeiter", "mitglied", "benutzer", "klant", "medewerker", "gebruiker", "kund",
    "asiakas", "klient", "pracownik", "musteri", "calisan",
))
# words for a person's name: the column holds names wherever one stands, unless a thing comes right before it
# ("product_name", "store_name"; _THING_WORDS)
_NAME_WORDS = frozenset((
    "name", "firstname", "lastname", "fullname", "surname", "forename", "givenname", "familyname", "fname",
    "lname", "nickname", "nombre", "apellido", "nome", "cognome", "sobrenome", "nom", "prenom", "vorname",
    "nachname", "naam", "voornaam", "achternaam", "navn", "fornavn", "etternavn", "efternavn", "namn",
    "efternamn", "nimi", "imie", "nazwisko", "isim", "soyad", "soyadi", "adi", "kundenname", "benutzername",
    "mitarbeitername",
))
# words for a name or a person in scripts the engine's splitter drops, matched anywhere in the column's name
_PERSON_WORDS_OTHER_SCRIPTS = (
    "имя", "фамилия", "клиент", "сотрудник", "пользователь", "покупатель",      # Russian
    "اسم", "عميل", "موظف",                                                    # Arabic
    "姓名", "名字", "名前", "氏名", "客户", "顾客", "顧客", "会员", "會員", "员工", "員工",   # Chinese, Japanese
    "이름", "성명", "고객", "회원", "직원",                                         # Korean
    "नाम", "ग्राहक",                                                            # Hindi
)
# the part of a name: first or last name only (a single word is then the whole value)
_NAME_PART = frozenset(("first", "last", "given", "family", "middle", "maiden", "nick", "firstname", "lastname",
                        "surname", "fname", "lname", "forename", "givenname", "familyname", "vorname", "nachname",
                        "prenom", "apellido", "cognome", "sobrenome", "voornaam", "achternaam", "fornavn",
                        "etternavn", "efternavn", "efternamn", "nazwisko", "soyad", "soyadi"))
# a thing: a name-word right after one names that thing ("product_name"), and a person word as its modifier
# names what the person has ("customer_id", "member_since", "user_count", "owner_city")
_THING_WORDS = frozenset((
    "product", "item", "sku", "file", "filename", "category", "brand", "company", "business", "store", "shop",
    "branch", "city", "region", "country", "province", "state", "sheet", "table", "column", "field", "host",
    "domain", "campaign", "event", "project", "plan", "service", "model", "device", "course", "team",
    "department", "dept", "warehouse", "location", "site", "tag", "label", "menu", "channel", "list", "report",
    "stage", "vendor", "supplier", "part", "bank", "color", "colour", "variant", "style", "option", "attribute",
    "promo", "coupon", "discount", "app", "application", "role", "job", "position", "id", "no", "num", "nbr",
    "number", "ref", "code", "key", "uuid", "guid", "since", "segment", "tier", "group", "type", "status",
    "source", "count", "cnt", "lifetime", "ltv", "value", "class", "rank", "score", "date", "time", "at", "ts",
    "timestamp", "created", "updated", "portal", "kind", "qty", "quantity", "total", "sum", "avg", "mean", "min",
    "max", "rate", "ratio", "pct", "percent", "size", "level", "flag", "format", "email", "mail", "phone",
    "address", "age", "gender", "sex", "industry", "language", "note", "comment", "version",
))
# words after a person word that leave it the head ("salesperson assigned", "technician on duty", "customer 2")
_TRAILING = frozenset(("assigned", "responsible", "on", "duty", "in", "charge", "of", "record", "primary",
                       "secondary", "main", "lead", "current", "previous", "prev", "new", "old", "other", "alt",
                       "backup", "1", "2", "3", "4"))
# "<verb>_to" that names who something went to ("assigned_to", "sold_to", "bill_to")
_TO_VERBS = frozenset(("assigned", "reassigned", "allocated", "delegated", "escalated", "referred", "billed",
                       "bill", "ship", "shipped", "sold", "sent", "delivered", "paid", "issued", "addressed",
                       "attention", "attn", "reported", "forwarded", "transferred"))
# "<verb>_by" that names who did it ("created_by", "sold_by"): a verb ending in "ed", or one of these
_BY_VERBS = frozenset(("sold", "paid", "made", "done", "run", "taken", "won", "led", "held", "kept", "sent",
                       "seen", "met", "written", "driven", "given", "bought", "brought", "caught", "taught",
                       "built", "chosen", "drawn", "set"))
_PLACE_WORDS = frozenset(("store", "shop", "branch", "warehouse", "site", "office", "vendor", "supplier",
                          "company", "business", "merchant", "depot", "plant", "location", "facility", "hq",
                          "outlet", "dealer", "distributor", "manufacturer", "carrier", "courier"))
# words no person's name holds: a firm, a role, a tier, software or a way to pay ("Acme Corp", "Sales Team",
# "Gold Member", "Field Technician", "Google Chrome", "Card" under paid_by); matched on whole words, lower case
_NOT_NAME_WORDS = frozenset((
    "inc", "ltd", "llc", "llp", "plc", "gmbh", "corp", "corporation", "company", "holdings", "partners",
    "industries", "systems", "solutions", "services", "logistics", "traders", "trading", "enterprises",
    "technologies", "labs", "agency", "university", "college", "school", "hospital", "clinic", "foundation",
    "institute", "association", "department", "dept", "team", "office", "division", "store", "restaurant",
    "hotel", "centre", "center", "manager", "director", "executive", "officer", "assistant", "technician",
    "engineer", "analyst", "specialist", "coordinator", "supervisor", "representative", "admin",
    "administrator", "operations", "sales", "support", "success", "reception", "accounts", "payable",
    "receivable", "member", "tier", "level", "basic", "plus", "premium", "elite", "platinum", "vip", "standard",
    "chrome", "firefox", "safari", "browser", "android", "iphone", "windows", "linux", "software",
    "card", "cash", "cheque", "eft", "visa", "mastercard", "amex", "paypal", "transfer", "debit", "credit", "wire",
    "invoice", "ach",
))
# words beside a name that are not one of its words ("Maria de la Cruz", "Ludwig van Beethoven")
_PARTICLES = frozenset(("de", "del", "della", "der", "den", "di", "da", "das", "dos", "do", "du", "la", "le",
                        "les", "van", "von", "ter", "ten", "te", "zu", "af", "av", "y", "e", "bin", "binti", "bint",
                        "ibn", "al", "el", "abu", "ben", "bat"))
_EMAIL_VALUE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
# a phone number as people write one: groups joined by a space, dot or dash, or an area code in brackets
_PHONE_VALUE = re.compile(r"^(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{2,4}[ .-]\d{3,4}"
                          r"(?:\s*(?:x|ext\.?)\s*\d{1,5})?$", re.I)
# an account or card number written in groups: 4-4-4, 4-4-4-4 or 4-6-5 digits with one separator. No phone
# plan writes a number that way, so it is never read as a phone; it identifies an account or a card holder,
# so it is flagged as "account or card number" (withheld by default, like every flagged column)
_GROUPED_NUMBER = re.compile(r"^\d{4}([ .-])\d{4}\1\d{4}(?:\1\d{4})?$|^\d{4}([ .-])\d{6}\2\d{4,5}$")
# street types after the street's name ("12 Queen St W", "4500 Maple Avenue, Unit 3", "100 5th Ave")
_STREET_TYPES = frozenset(("street", "avenue", "ave", "road", "rd", "boulevard", "blvd", "drive", "lane", "court",
                           "crescent", "cres", "place", "terrace", "highway", "hwy", "parkway", "pkwy", "square",
                           "circle", "trail", "trl", "close"))
# street types that are also other words ("12 ct", "3 Way Switch", "10 Sq Ft", "Dr Pepper"): one counts only
# when nothing numeric follows it ("12 Oak Ct" and "12 Oak Ct, Unit 3", never "12 Oak Ct 24")
_STREET_TYPES_SHORT = frozenset(("st", "dr", "ct", "way", "ln", "pl", "sq", "cir", "terr", "ter"))
# street types before the street's name ("12 rue de Rivoli", "5 Calle Mayor")
_STREET_TYPES_FIRST = frozenset(("rue", "calle", "avenida", "rua", "via", "viale", "piazza", "plaza", "chemin"))
# words before a count or a size, never a street's name ("12 Pack Dr Pepper", "Large Eggs 6 Ct")
_NOT_A_STREET = frozenset(("pack", "pk", "ct", "count", "pc", "pcs", "piece", "pieces", "oz", "lb", "lbs", "kg",
                           "g", "ml", "l", "ft", "in", "inch", "x", "sq", "cu", "gauge", "amp", "volt", "watt",
                           "way", "set", "box", "case", "roll", "sheet", "ply"))
_HOUSE = re.compile(r"^\s*\d{1,6}[A-Za-z]?(?:-\d{1,6})?,?\s+(.*)$")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")


def _header_tokens(name: Any) -> Tuple[str, ...]:
    """A column name's words, split as the engine's scan splits them (case changes, letters and digits,
    everything else; plurals folded: "MemberName" and "members" both give "member"), but in any script, with
    accents dropped ("Prénom" gives "prenom", "Müşteri" "musteri")."""
    import unicodedata
    s = _CAMEL.sub(" ", str(name)).lower()
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return tuple(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
                 for t in re.split(r"[\W_]+", s) if t)


def _person_hint(toks: Tuple[str, ...], header: Any = "") -> Tuple[bool, bool]:
    """(the column's name says it holds people, and it holds one part of a name: a first or last name).
    Its last word (after "assigned", "on duty", a number) is a person ("sales_rep", "account_manager",
    "Salesperson Assigned"); or a name-word stands with no thing right before it ("customer_name", "Name",
    "nombre del cliente"; never "product_name"); or it is "<verb>ed_by" or "assigned_to"; or it holds a word
    for a name or a person in another script ("客户姓名"). Never "user_agent", "customer_id", "member_since"."""
    low = str(header or "").lower()
    if any(w in low for w in _PERSON_WORDS_OTHER_SCRIPTS):
        return True, False
    if not toks:
        return False, False
    ts = set(toks)
    part = bool(ts & (_NAME_PART - {"first", "last", "given", "family", "middle", "maiden", "nick"})) or (
        bool(ts & _NAME_WORDS) and bool(ts & _NAME_PART))
    for i, t in enumerate(toks):
        if t in _NAME_WORDS and (i == 0 or toks[i - 1] not in _THING_WORDS):
            return True, part
    if len(toks) >= 2 and toks[-1] == "by" and (toks[-2].endswith("ed") or toks[-2] in _BY_VERBS):
        return True, False
    if len(toks) >= 2 and toks[-1] == "to" and toks[-2] in _TO_VERBS:
        return True, False
    head = list(toks)
    while len(head) > 1 and head[-1] in _TRAILING:
        head.pop()
    if head[-1] in _PERSON_WORDS and not (head[-1] == "agent" and len(head) >= 2 and head[-2] == "user"):
        return True, part
    return False, False


def _name_word(w: str) -> bool:
    """A word of a person's name: letters in any script (marks inside are fine), an apostrophe, hyphen or dot
    inside or after ("O'Neil", "Mary-Jane", "J.")."""
    import unicodedata
    if not w or not w[0].isalpha():
        return False
    return all(ch.isalpha() or ch in "'’-." or unicodedata.category(ch).startswith("M") for ch in w[1:])


def _person_value(v: str) -> bool:
    """A value that could be a person's name, in any script or case: 1 to 4 words of letters, with initials
    ("J. Smith"), particles beside them ("Maria de la Cruz", "Anne van der Berg"), or the comma form
    ("Fairweather, Marisol"); at least one word of two or more letters ("J. K." is not a name); no word that
    says firm, role, tier, software or a way to pay (_NOT_NAME_WORDS: "Acme Corp", "Gold Member", "Sales Manager",
    "Card")."""
    s = " ".join(str(v).split())
    if s.count(",") == 1:
        a, b = (x.strip() for x in s.split(","))
        if not a or not b:
            return False
        s = a + " " + b
    words = s.split(" ")
    if not s or len(words) > 8:
        return False
    core, full = 0, False
    for w in words:
        lw = w.casefold().strip(".'’")
        if lw in _NOT_NAME_WORDS:
            return False
        if lw in _PARTICLES and core:
            continue                          # a particle after the first word ("de la", "van der")
        if not _name_word(w):
            return False
        core += 1
        full = full or sum(ch.isalpha() for ch in w) >= 2
    return 1 <= core <= 4 and full


# wave 5f (C): about three hundred given names that are nothing else (no "Mark", "Grace", "Jordan", "Victoria", "Austin", "Kelly"): a column of
# values that open with one and go on with a capitalised surname is a column of people's names under ANY header ("Analyst", "Stylist",
# "Hairdresser", "Pilot"), because a header list is never complete. Accents are folded; the test is on the first word only.
_GIVEN_NAMES = frozenset("""
james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony donald steven paul andrew joshua kenneth
kevin brian george timothy ronald jason edward jeffrey ryan jacob gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel
gregory alexander patrick frank raymond jack dennis jerry tyler aaron adam nathan henry zachary douglas peter kyle noah ethan jeremy walter
christian keith roger terry sean gerald carl harold dylan arthur lawrence jesse bryan billy bruce gabriel joe logan albert willie alan juan wayne
elijah randy roy vincent ralph eugene russell bobby mason philip louis
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen lisa nancy betty margaret sandra ashley kimberly emily donna michelle
carol amanda dorothy melissa deborah stephanie rebecca sharon laura cynthia kathleen amy angela shirley anna brenda pamela emma nicole helen
samantha katherine christine debra rachel carolyn janet catherine maria heather diane ruth julie olivia joyce virginia lauren christina joan
evelyn judith megan andrea cheryl hannah jacqueline martha gloria teresa ann sara madison frances kathryn janice jean abigail alice judy sophia
denise amber doris marilyn danielle beverly isabella theresa diana natalie brittany charlotte marie kayla alexis lori
carlos jose luis miguel pedro jorge manuel francisco antonio javier fernando ricardo alejandro diego sergio pablo andres rafael eduardo alberto
enrique mario oscar raul hector ignacio ana carmen isabel rosa lucia marta paula elena sofia cristina silvia pilar mercedes dolores beatriz
lucas mateo tomas santiago
hans klaus jurgen wolfgang dieter stefan andreas markus lars sven erik anders nils bjorn henrik johan karl ingrid astrid freya greta heidi anke
petra sabine ursula brigitte monika gisela helga ewa katarzyna agnieszka piotr tomasz marek pawel jan jakub luca marco giuseppe giovanni
francesco alessandro matteo lorenzo paolo stefano chiara giulia francesca valentina federica elisa pierre michel philippe alain jacques
nicolas francois stephane laurent olivier sophie camille isabelle nathalie sylvie veronique
mohamed mohammed ahmed ali omar hassan hussein ibrahim yusuf khalid samir tariq karim fatima aisha layla amina zainab priya rahul amit raj arjun
vikram anil sanjay rohan neha pooja anita sunita kavita deepak wei ming jun hiroshi takashi kenji yuki akira sakura soo hyun
""".split())


def _given_name_value(v: str) -> bool:
    """A value of two or three words that opens with a given name and could be a person's name (`_person_value`): "Maria Hollin", "Omar Nakamura"."""
    import unicodedata
    if not _person_value(v):
        return False
    words = [w for w in " ".join(str(v).replace(",", " ").split()).split(" ") if w]
    if not 2 <= len(words) <= 4 or not all(w[:1].isupper() for w in words if w.casefold() not in _PARTICLES):
        return False
    first = "".join(ch for ch in unicodedata.normalize("NFKD", words[0].casefold()) if not unicodedata.combining(ch)).strip(".'’")
    return first in _GIVEN_NAMES


def _street_value(v: str) -> bool:
    """A street address: a house number, then the street's name (1 to 4 words, the last holding a letter and
    not a pack or size word), then a street type ("12 Queen St W", "4500 Maple Avenue, Unit 3", "100 5th Ave",
    "1 Microsoft Way"), or a house number and a type that comes first ("12 rue de Rivoli"). Never "12 ct",
    "12 Ct Paper Towels", "10 Sq Ft Tile", "3 Way Switch" (no street's name between the number and the type)
    or "12 Pack Dr Pepper" (a pack word before it)."""
    m = _HOUSE.match(str(v))
    if not m:
        return False
    toks = re.findall(r"[^\s,#]+|[,#]", m.group(1))
    if len(toks) >= 2 and toks[0].lower() in _STREET_TYPES_FIRST and re.search(r"[^\W\d_]", toks[1]):
        return True
    for i in range(1, min(len(toks), 5)):
        if toks[i - 1] in (",", "#"):
            return False                      # the street's name ended with no type
        t = toks[i].lower().rstrip(".")
        if t not in _STREET_TYPES and t not in _STREET_TYPES_SHORT:
            continue
        last = toks[i - 1].lower().rstrip(".")
        if not re.search(r"[^\W\d_]", last) or last in _NOT_A_STREET:
            continue
        rest = toks[i + 1:]
        if t in _STREET_TYPES_SHORT and rest and rest[0][:1].isdigit():
            continue
        return True
    return False


# wave 5f (C): a column whose HEADER names a sensitive category (marital status, Indigenous identity, HIV status, ICD code, cause of death,
# religion, ethnicity, visible minority ...) is FLAGGED and WITHHELD BY DEFAULT in any table layout, with a one-click Keep on the consent
# card: the scan flags what looks like free text, a long ID or a person, so a five-member category column sat as an ordinary DIMENSION of
# an official cube and one of its members was printed in the estimand ("Canada · All industries · Non-Indigenous identity"). Wave 5e's
# SENSITIVE_HEADER only stopped a flagged column from being RELEASED. This vocabulary is the FLAGGING one, so it is strict: whole words
# or phrases of the header (accents folded, case ignored, a word boundary on both sides: never "sex" in Essex, "race" in Terrace,
# "union" in Reunion, "aids" in "Aids and appliances"), English with the French, Spanish and German names. A header is a hint, never
# evidence about values: a numeric column with many different values is a MEASURE under such a header (a "Disability benefit" amount) and
# is not flagged. Money words (income, salary, debt) are not in it: they name measures far more often than categories.
_SENSITIVE_PHRASES = (
    # race, ethnicity, Indigenous identity, visible minority
    r"ethnic(?:ity|ities)?(?: origin| group| background)?", r"racial|race|races", r"visible minorit(?:y|ies)", r"minorit[ey]s? visibles?",
    r"indigenous|aboriginal|first nations?|inuit|metis|autochtones?|indigenas?|indigene", r"ancestry",
    r"origine ethnique|ethnie|etnia|raza|rasse|herkunft",
    # religion and belief, politics, trade union
    r"religion|religious|religieuse?s?|faith|religious denomination|beliefs?|confession|konfession|creencias?",
    r"political (?:party|affiliation|view|views|opinion|opinions|belief|beliefs|leaning)|party affiliation|voting (?:intention|preference)",
    r"opinions? politiques?|partido politico|politische (?:partei|meinung|einstellung)|parti politique",
    r"trade unions?|union (?:membership|member|status)|syndicat|sindicato|gewerkschaft\w*",
    # sex, gender, orientation, marital and family status
    r"sex|gender(?: identity)?|sexe|sexo|geschlecht|sexual (?:orientation|preference|identity)|orientation sexuelle|orientacion sexual|"
    r"sexuelle orientierung|transgender",
    r"marital(?: status)?|marriage status|civil status|relationship status|etat (?:matrimonial|civil)|situation matrimoniale|"
    r"estado civil|familienstand|pregnan(?:t|cy)",
    # health, disability, cause of death
    r"diagnos(?:is|es|tic|tics)|diagnostic|diagnostico|diagnose|disease|diseases|illness|illnesses|maladie|enfermedad|krankheit|"
    r"disabilit(?:y|ies)|disabled|handicap|discapacidad|behinderung|incapacite|"
    r"(?:health|medical|mental health|medical history|health) (?:status|condition|conditions|history|record|records)|"
    r"etat de sante|estado de salud|gesundheitszustand|symptoms?|"
    r"icd(?:[- ]?\d{1,2})?|cause of death|cause de deces|causa de muerte|todesursache|mortality cause|"
    r"hiv|vih|sida|hiv status|aids (?:status|test|diagnosis|infection|case|cases)",
    # criminal record, immigration
    r"criminal (?:record|history|offence|offense|conviction)|convictions?|arrests?|casier judiciaire|antecedentes penales|vorstrafen?|"
    r"immigration status|statut d immigration|estatus migratorio|aufenthaltsstatus|asylum|refugee|"
    r"nationality|nationalite|nacionalidad|staatsangehorigkeit|citizenship|citoyennete|ciudadania",
    # genetic and biometric
    r"genetic|genetique|genetico|biometric\w*",
)
_SENSITIVE_RX = re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(_SENSITIVE_PHRASES))
_AIDS_UPPER = re.compile(r"(?<![A-Za-z0-9])AIDS(?![A-Za-z0-9])")


def _sensitive_header(header: Any) -> Optional[str]:
    """The sensitive-category phrase a column's HEADER holds (whole words, accents folded, case ignored), or None. `aids` counts only in capitals
    or beside a word that makes it a diagnosis ("AIDS status"), never as the word of "Aids and appliances"."""
    import unicodedata
    h = str(header or "")
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", h.lower()) if not unicodedata.combining(ch))
    folded = re.sub(r"[^a-z0-9]+", " ", folded.replace("'", " ")).strip()
    m = _SENSITIVE_RX.search(folded)
    if m:
        return m.group(0).strip()
    if _AIDS_UPPER.search(h):
        return "aids"
    return None


def _measure_like(values: Any) -> bool:
    """Whether a column's cells are a MEASURE and not a category: numbers, with more than a handful of different values. A category column
    coded 1, 2, 3 is not a measure; a column of amounts is."""
    import pandas as pd
    t = values.astype(object).where(values.notna(), "").astype(str).str.strip()
    t = t[t != ""]
    if len(t) < 2:
        return False
    num = pd.to_numeric(t.str.replace(",", "", regex=False), errors="coerce")
    return bool(float(num.notna().mean()) >= 0.95 and int(t.nunique()) > SENSITIVE_MEASURE_DISTINCT)


SENSITIVE_MEASURE_DISTINCT = 25          # wave 5f: a number column with more than this many different values is a measure under any header


def _personal_kind(header: Any, values: Any) -> Optional[str]:
    """The kind of personal data a column holds by this check (see above), or None: `email`, `phone_na`, `account_number`,
    `street_address`, `person_name` or (wave 5f) `sensitive_category`. `header` is the column's name as the file writes it, `values` its
    cells (any type)."""
    import numpy as np
    import pandas as pd
    nulls = _null_tokens()
    t = values.astype(object).where(values.notna(), "").astype(str).str.split().str.join(" ")
    t = t[~t.str.lower().isin(nulls)]
    if not len(t):
        return None
    if _sensitive_header(header) and not _measure_like(values):
        return "sensitive_category"
    vc = t.value_counts()
    vals = pd.Series([str(x) for x in vc.index], dtype=object)
    w = vc.to_numpy(dtype=float)
    total = float(w.sum())

    def share(mask: Any) -> float:
        return float(w[np.asarray(mask, dtype=bool)].sum()) / total if total else 0.0
    toks = _header_tokens(header)
    digits = vals.str.count(r"\d")
    grouped = vals.str.match(_GROUPED_NUMBER)
    if share(vals.str.match(_EMAIL_VALUE)) >= PERSONAL_MIN_SHARE:
        return "email"
    if share(vals.str.match(_PHONE_VALUE) & digits.between(10, 15) & ~grouped) >= PERSONAL_MIN_SHARE:
        return "phone_na"
    if share(grouped) >= PERSONAL_MIN_SHARE:
        return "account_number"
    # wave 5d: a column that names things (few different values, each on many rows: at most 400 and at most 30% of its cells) whose
    # members are numbers of nine to nineteen digits is a column of ID numbers (a customer or account number, a phone number written
    # with no separator): no category is called by 9 digits. A column of measures is never one: it has a different value on most rows
    if len(vals) <= PERSONAL_ID_MAX_DISTINCT and len(vals) <= PERSONAL_ID_MAX_SHARE * total and \
            share(vals.str.fullmatch(r"\d{9,19}")) >= PERSONAL_MIN_SHARE:
        return "id_number"
    if not set(toks) & _PLACE_WORDS and share([_street_value(x) for x in vals]) >= PERSONAL_MIN_SHARE:
        return "street_address"
    if _person_hint(toks, header)[0] and share([_person_value(x) for x in vals]) >= PERSONAL_MIN_SHARE:
        return "person_name"
    # wave 5f (C): under any other header, values that open with a given name and carry a surname (at least 3 different ones, 60% of the cells)
    if len(vals) >= 3 and not set(toks) & _PLACE_WORDS and share([_given_name_value(x) for x in vals]) >= PERSONAL_MIN_SHARE:
        return "person_name"
    return None


def _personal_columns(db_path: str, table: str, colmap: Dict[str, str], skip: Set[str]) -> List[Tuple[str, str]]:
    """[(landed column, kind)]: the columns this check adds to the visitor's choices (see above), in the
    file's order; `skip`: the columns the engine's scan already flagged. Kinds are the engine's own words
    (person_name, email, phone_na), street_address and account_number."""
    import pandas as pd
    from northledger import clean as _clean
    from northledger._sqlite import connect_ro
    con = connect_ro(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM %s" % _clean._quote_ident(table), con)
    finally:
        con.close()
    head = {str(v): str(k) for k, v in (colmap or {}).items()}
    out: List[Tuple[str, str]] = []
    for col in df.columns:
        if col in skip:
            continue
        kind = _personal_kind(head.get(col, col), df[col])
        if kind:
            out.append((str(col), kind))
    return out


# wave 5d: a long table the layout pass turns into one column per series NAMES each series from its text columns, so a personal column
# among them (an account owner, a contact phone) was written into the series names, past every scan: the file is landed AFTER it
# is reshaped, and the scan reads the reshaped table's headers as headers. The text columns that would name the series are checked
# first, by the engine's own scan (a name hint, a value shape) and by this check, and a personal one never names a series unless the
# visitor chose to keep it (_decide_and_guard lists it with the flagged columns, the default being withhold, and scrubs its values).
def _raw_personal_columns(df: Any, cols: Iterable[Any]) -> Dict[str, str]:
    """{column header: kind label} for the columns among `cols` of a table as the file holds it (not yet landed) that look personal: the
    engine's scan (a column named like personal data, values shaped like an email, a phone number, a national ID number ...) and this
    adapter's check (_personal_kind)."""
    found: Dict[str, str] = {}
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return found
    try:
        from northledger import intake as _intake
        for f in _intake.scan_pii(df[cols]):
            found.setdefault(str(f.column), _kind_label(str(f.kind)))
    except Exception:  # noqa: BLE001 - the scan is one of two checks; the adapter's own still runs
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
    for c in cols:
        if str(c) not in found:
            k = _personal_kind(str(c), df[c])
            if k:
                found[str(c)] = _kind_label(k)
    return found


def _kind_label(kind: str) -> str:
    """The words for one kind of personal data (as the consent step says it)."""
    k = str(kind).strip()
    if k.startswith("named_"):
        return _REASON_LABEL["name"] if _KIND_LABEL.get(k) is None else _KIND_LABEL[k]       # "named like personal data", as landing says it
    return _KIND_LABEL.get(k) or k.replace("_", " ")


# --------------------------------------------------------------------------- a withheld column drives no rule
_CODE_HEAD = "WITHHELD"


def _opaque_code(i: int) -> str:
    """The i-th code a withheld column's value becomes: WITHHELDA, WITHHELDB ... WITHHELDZ, WITHHELDAA ...
    Capital letters only, so no engine reader takes it for a number, a date, a yes/no or a placeholder, and
    the letter-case rule leaves it as it is."""
    s, n = "", int(i)
    while True:
        s = chr(65 + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return _CODE_HEAD + s


def _neutralize_withheld(db_path: str, table: str, columns: List[str]) -> List[str]:
    """A withheld column never drives the engine's cleaning (integration review, 29 Sep 2026: a withheld notes
    column that mostly held dates got the engine's notes_date rule, which set aside every row whose note was
    not a date, against the promise that the column is never used in the business analysis). The engine has
    no option to leave a column out of its rules: clean.standard_rules reads every column of the health
    profile, and the loop takes no rule set of its own. So a withheld column is landed as text no rule acts
    on, before the engine profiles or cleans anything: each distinct value, byte for byte as the file holds it
    (its case, its spaces and a placeholder such as N/A or a dash included), becomes its own opaque code of
    capital letters; only an empty cell stays empty. No date, number, yes/no, range, spelling, spacing or
    placeholder rule reads a value, so no rule sets a row aside or changes one because of what it holds. The
    column still counts where every column counts, as the file has it: the data-health check counts its empty
    cells, and both exact-duplicate checks (the health's count and the cleaning's) compare its codes, which
    are equal exactly where the file's values are, so it only keeps otherwise-identical rows apart.

    Final review, 29 Sep 2026 (tools/fixtures/review3/codes_probe.py): the codes were made after runs of
    spaces were collapsed and placeholders blanked, so rows that differed in the file only by spacing or a
    placeholder became duplicates: the health counted 137 exact duplicate rows where the file has 100, and the
    cleaning set aside 137. Byte-exact codes give 100 and 100 (codes off, the engine's own reading: 100 and 150;
    the 50 more are rows that become duplicates only once the engine's trim, case and placeholder rules rewrite
    the column, which a withheld column must never drive). Blanking placeholders was measured too and not
    kept: it moves both counts together (107 and 107 blanking N/A as written, 118 and 118 once trimmed), so it
    brings the pair no closer (every variant is 50 rows off in all), it leaves the health's count off the
    file's own, and it would need a placeholder rule that reads the values. Returns the codes, which the
    scrubber hides wherever they would show."""
    import sqlite3
    codes: List[str] = []
    if not columns:
        return codes
    qt = '"%s"' % str(table).replace('"', '""')
    con = sqlite3.connect(db_path)
    try:
        for c in columns:
            qc = '"%s"' % str(c).replace('"', '""')
            keys: Dict[str, str] = {}
            pairs = []
            for (v,) in con.execute("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL" % (qc, qt, qc)).fetchall():
                t = str(v)                    # byte for byte: "N/A", " call back" and "Call back" are three codes
                if t == "":
                    pairs.append((v, None))   # an empty cell stays empty
                    continue
                if t not in keys:
                    keys[t] = _opaque_code(len(keys))
                pairs.append((v, keys[t]))
            con.execute("DROP TABLE IF EXISTS temp._nl_codes")
            con.execute("CREATE TEMP TABLE _nl_codes (v PRIMARY KEY, t)")
            con.executemany("INSERT OR IGNORE INTO _nl_codes VALUES (?, ?)", pairs)
            con.execute("UPDATE %s SET %s = (SELECT t FROM _nl_codes WHERE _nl_codes.v = %s.%s) WHERE %s IS NOT NULL"
                        % (qt, qc, qt, qc, qc))
            codes.extend(keys.values())
        con.execute("DROP TABLE IF EXISTS temp._nl_codes")
        con.commit()
    finally:
        con.close()
    return codes


# --------------------------------------------------------------------------- a category is not free text
# WAVE 4 (plan/WAVE4-A-DESIGN.md section 2(9), with the lead's amendment AM1). The engine's scan calls any column with 20
# or more different wordy values "free text" (intake._is_free_text), so an official table's industry column (30 NAICS
# labels, each on 1,185 rows) was withheld and the table read as nonsense. The adapter releases such a column, and only
# when ALL of these hold: the scan's only reason is free text (no value looked like an email, a phone number or any other
# personal shape); it has at most 300 different values, at most 5% of its filled cells; every label repeats at least 5
# times; its values do not look like people's names (_looks_like_names) and its name does not say it holds people
# (_person_hint); and its name is not a sensitive category (AM1: health, religion, ethnicity and the like are sensitive
# even when categorical). A release deletes the column's row from the engagement's column register (runtime state, never
# engine code), so the engine reads it like any column, and is recorded in privacy.released. The page's consent step
# shows every released column ("Read as a category, not personal data: <column> (<n> labels)") and the visitor can still
# withhold it: a withhold or code decision for it keeps the flag.
RELEASE_MAX_DISTINCT = 300
RELEASE_MAX_DISTINCT_CODED = 3000       # wave 5f (G): a column whose labels CARRY CODES ([4411], 4411 Used car dealers: NAICS at 6 digits, HS) has 400 to 2,500 labels
_CODED_LABEL = re.compile(r"\[[0-9A-Za-z][0-9A-Za-z.\-]*\]\s*$|^\s*[0-9][0-9A-Za-z.\-]*\s+\S")
RELEASE_MAX_SHARE = 0.05
RELEASE_MAX_SHARE_CODED = 0.20          # wave 5g (F): a coded column (NAICS, HS) of a table with a few periods: a label is on one row a period, so 935 labels
                                        # over 8 quarters are 12% of the rows; every label repeating RELEASE_MIN_REPEAT times already limits the share to 20%
RELEASE_MIN_REPEAT = 5
SENSITIVE_HEADER = re.compile(
    r"(?i)diagnos|condition|disease|illness|medic|health|symptom|treatment|drug|religio|faith|ethnic|race|"
    r"nationality|citizenship|gender|sex\b|sexual|orientation|disab|pregnan|criminal|offen[cs]e|convict|union|"
    r"political|party|vote|salary|wage|income|debt|credit|immigra|visa|"
    # wave 5e: what the independent reviewer named (visible minority, Indigenous identity, cause of death, ICD codes, HIV, marital status)
    # and the words other languages use for them (minorit[eé] visible, autochtone, [eé]tat matrimonial, Familienstand, Behinderung ...)
    r"visible minorit|minorit[e\u00e9]s? visible|indigenous|aboriginal|first nations|inuit|m[e\u00e9]tis|autochton|ind[i\u00ed]gen|"
    r"cause of death|cause de d[e\u00e9]c[e\u00e8]s|causa de muerte|todesursache|\bicd\b|\bhiv\b|\baids\b|\bsida\b|marital|marriage|"
    r"civil status|[e\u00e9]tat (?:matrimonial|civil)|estado civil|familienstand|behinderung|discapacidad|handicap|"
    r"ethni|etnia|religi[o\u00f3]n|konfession|orientaci[o\u00f3]n sexual|geschlecht|g[e\u00e9]nero")
RELEASED_WORDS = "Read as a category, not personal data: %s (%s labels)"
RELEASED_MEASURE_WORDS = "Read as the table's measure, not personal data: %s"
RELEASE_NAME_SHARE = 0.30
_LABEL_GLUE = frozenset(("and", "of", "the", "for", "or", "with", "in", "on", "to", "at", "by", "from", "other", "all",
                         "total", "except", "excluding", "not", "nec", "n.e.c", "&"))


def _name_shaped(v: str) -> bool:
    """A label that reads like a person's name whatever list of given names is at hand ("Oskar Lucia Brennan Tanaka",
    "Marisol Fairweather"): 1 to 4 words of letters as _person_value reads them, each word capitalised (or every word in
    one case), and none of a category label's glue words ("Food and beverage retailers")."""
    if not _person_value(v):
        return False
    words = [w for w in " ".join(str(v).replace(",", " ").split()).split(" ") if w]
    if any(w.casefold().strip(".") in _LABEL_GLUE for w in words):
        return False
    caps = [w[:1].isupper() for w in words if w[:1].isalpha()]
    return bool(caps) and (all(caps) or not any(caps) or all(w.isupper() for w in words))


def _publisher_header(headers: Iterable[str]) -> Optional[str]:
    """The publisher whose signature columns the file's header holds (3 of them, or all of a shorter signature), from engine/flag_vocab.json
    and in any language it lists; None for any other file."""
    sigs, _codes = _guard_vocab()
    have = {_pnorm(h) for h in headers}
    for name, sig in sigs.items():
        s = [_pnorm(x) for x in sig]
        if sum(1 for x in s if x in have) >= min(3, len(s)):
            return name
    return None


def _release_value_column(E: Any, eng: Any, res: Any, decisions: Any, released: List[Dict[str, Any]]) -> None:
    """Wave 5e (P10). The documented value column of an official table (VALUE, OBS_VALUE, VALEUR; the file carries a publisher's
    signature) that parses as numbers is a MEASURE, not a national ID number: real tables hold nine-digit values (dollars in thousands,
    a population in persons) and the engine's scan reads one in ten of them as a SIN. It is released with the consent line "Read as
    the table's measure, not personal data: VALUE" (privacy.released, kind "measure"); the visitor can still withhold it (the refusal
    of wave 5d then stands). The long-ID rule and the scan stay for every other column."""
    import sqlite3
    colmap = dict(getattr(res, "column_map", {}) or {})
    # wave 5g (F): a publisher's signature, or three of the columns only a statistical publisher uses (the vocabulary the series guard reads, in
    # English, French, Spanish and German: a German table has MASSEINHEIT, SKALENFAKTOR and DEZIMALSTELLEN, and no signature of its own)
    if _publisher_header(colmap.keys()) is None and sum(1 for h in colmap.keys() if _pnorm(h) in _GUARD_META_SPECIFIC) < 3:
        return
    head = {str(v): str(k) for k, v in colmap.items()}
    chosen: Dict[str, str] = {}
    for k, v in dict(decisions or {}).items():
        if not str(k).startswith("__"):
            chosen[str(k)] = str(v or "").strip().lower()
            if str(k) in colmap:
                chosen[colmap[str(k)]] = str(v or "").strip().lower()
    con = sqlite3.connect(eng.db_path)
    try:
        rows = con.execute("SELECT column_name FROM %s WHERE table_name = ?" % E.COLUMNS_TABLE, (res.table,)).fetchall()
        flagged = {str(r[0]) for r in rows}
    finally:
        con.close()
    for col in sorted(flagged):
        header = head.get(col, col)
        if _pnorm(header) not in _PANEL_VALUE or chosen.get(col) in ("withhold", "code") or chosen.get(header) in ("withhold", "code"):
            continue
        con = sqlite3.connect(eng.db_path)
        try:
            vals = [str(r[0]) for r in con.execute('SELECT "%s" FROM "%s" WHERE "%s" IS NOT NULL' % (
                col.replace('"', '""'), str(res.table).replace('"', '""'), col.replace('"', '""')))]
        finally:
            con.close()
        vals = [v.strip() for v in vals if v.strip() != ""]
        if len(vals) < 6:
            continue
        ok = 0
        seen = set()
        for v in vals:
            t = v.replace(",", "")
            try:
                float(t)
                ok += 1
                seen.add(t)
            except ValueError:
                pass
        # all the cells are numbers (the publisher's flags and blanks are other columns' business), and they vary
        if ok < 0.95 * len(vals) or len(seen) < 2:
            continue
        con = sqlite3.connect(eng.db_path)
        try:
            con.execute("DELETE FROM %s WHERE table_name = ? AND column_name = ?" % E.COLUMNS_TABLE, (res.table, col))
            con.commit()
        finally:
            con.close()
        released.append({"column": col, "header": header, "kind": "measure", "distinct": len(seen), "rows": ok, "min_repeat": 1,
                         "text": RELEASED_MEASURE_WORDS % header,
                         "why": "the table's own value column (a publisher's layout names it), every cell a number: a count of nine or more "
                                "digits has the shape of an ID number and is not one here"})


def _release_categories(E: Any, eng: Any, res: Any, decisions: Any) -> List[Dict[str, Any]]:
    """The free-text flags the adapter lifts (see above), as privacy.released: [{column, header, distinct, rows, min_repeat,
    why}]. Runs before any decision, so a released column is never withheld or coded by default."""
    import sqlite3
    colmap = dict(getattr(res, "column_map", {}) or {})
    head = {str(v): str(k) for k, v in colmap.items()}
    chosen: Dict[str, str] = {}
    for k, v in dict(decisions or {}).items():
        if str(k).startswith("__"):
            continue
        d = str(v or "").strip().lower()
        chosen[str(k)] = d
        if str(k) in colmap:
            chosen[colmap[str(k)]] = d
    con = sqlite3.connect(eng.db_path)
    try:
        rows = con.execute("SELECT column_name, kinds FROM %s WHERE table_name = ? ORDER BY rowid" % E.COLUMNS_TABLE,
                           (res.table,)).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    nulls = _null_tokens()
    for col, kinds in rows:
        col = str(col)
        header = head.get(col, col)
        if chosen.get(col) in ("withhold", "code") or chosen.get(header) in ("withhold", "code"):
            continue
        if _kind_of(col, kinds or "", res) != _REASON_LABEL["free_text"]:
            continue
        if any(k.strip() and not k.strip().startswith("named_") for k in str(kinds or "").split(",")):
            continue                                  # a value shape the scan matched: never released
        if str(kinds or "").strip() or SENSITIVE_HEADER.search(header) or _person_hint(_header_tokens(header), header)[0]:
            continue                                  # its name was a hint (named_...), is sensitive (AM1) or says people
        con = sqlite3.connect(eng.db_path)
        try:
            qc = '"%s"' % col.replace('"', '""')
            vc = con.execute("SELECT %s, COUNT(*) FROM %s WHERE %s IS NOT NULL GROUP BY %s" % (
                qc, '"%s"' % str(res.table).replace('"', '""'), qc, qc)).fetchall()
        finally:
            con.close()
        counts: Dict[str, int] = {}
        for v, n in vc:
            t = " ".join(str(v).split())
            if t.lower() in nulls:
                continue
            counts[t] = counts.get(t, 0) + int(n)
        filled = sum(counts.values())
        coded = bool(counts) and sum(1 for x in counts if _CODED_LABEL.search(x)) >= 0.8 * len(counts)
        if not counts or len(counts) > (RELEASE_MAX_DISTINCT_CODED if coded else RELEASE_MAX_DISTINCT) or \
                len(counts) > (RELEASE_MAX_SHARE_CODED if coded else RELEASE_MAX_SHARE) * filled:
            continue
        least = min(counts.values())
        if least < RELEASE_MIN_REPEAT:
            continue
        if _looks_like_names(list(counts), header) or \
                sum(1 for x in counts if _name_shaped(x)) >= RELEASE_NAME_SHARE * len(counts):
            continue                                  # people's names, whatever their header (AM1: "Stylist")
        con = sqlite3.connect(eng.db_path)
        try:
            con.execute("DELETE FROM %s WHERE table_name = ? AND column_name = ?" % E.COLUMNS_TABLE, (res.table, col))
            con.commit()
        finally:
            con.close()
        out.append({"column": col, "header": header, "distinct": len(counts), "rows": filled, "min_repeat": least,
                    "text": RELEASED_WORDS % (header, format(len(counts), ",")),
                    "why": ("a category: %s labels, each on %s rows or more; the scan read it as free text only because "
                            "it holds 20 or more different values" % (format(len(counts), ","), format(least, ",")))})
    return out


def _raw_column_values(raw: Any, headers: Iterable[str]) -> Dict[str, List[str]]:
    """{header: the distinct values of that column of the file as the visitor sent it} (for the scrubber: never printed)."""
    import pandas as pd
    out: Dict[str, List[str]] = {}
    heads = [str(h) for h in headers]
    if not heads or not raw:
        return out
    try:
        df = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8-sig", keep_default_na=False, usecols=lambda c: str(c) in heads)
    except Exception:  # noqa: BLE001 - the values are looked for where they might appear; a file that does not read gives none
        return out
    for h in heads:
        if h in df.columns:
            out[h] = sorted({" ".join(str(v).split()) for v in df[h] if str(v).strip()})
    return out


def _release_slice_measure(E: Any, eng: Any, res: Any, header: str) -> None:
    """The measure column of a slice the structure layer wrote itself (the date and one number a month, in base units) is never a
    national ID number, whatever its digits: a figure of eleven digits (dollars in millions, a population) passes the scan's check digit
    now and then, and the slice then had no measure and the run no facts (wave 5e: found when "USD millions" was applied to the figures).
    The visitor decided on the file's own columns (privacy.flagged is theirs); this column is the engine's own."""
    import sqlite3
    landed = dict(getattr(res, "column_map", {}) or {}).get(header)
    if not landed:
        return
    con = sqlite3.connect(eng.db_path)
    try:
        con.execute("DELETE FROM %s WHERE table_name = ? AND column_name = ?" % E.COLUMNS_TABLE, (res.table, landed))
        con.commit()
    finally:
        con.close()


def _decide_and_guard(E: Any, eng: Any, res: Any, decisions: Any, aside: Optional[Dict[str, str]] = None, sent_bytes: Any = None,
                      kept: Optional[Dict[str, str]] = None, slice_measure: Optional[str] = None
                      ) -> Tuple[List[Dict[str, str]], List[str], "Scrubber", List[Dict[str, Any]]]:
    """The decide stage, the same for a run and for the planner's profile: a free-text flag on a plain category is
    lifted (_release_categories, unless the visitor withheld or coded it), the adapter's personal-column check adds
    what the engine's scan missed (a pending decision in the engine's own column register, as a column its scan
    flagged gets), every flagged column takes the visitor's decision or withhold, and each withheld column's values
    are read for the scrubber and then landed as codes no cleaning rule reads. Returns (privacy.flagged, the withheld
    columns, the scrubber, privacy.released)."""
    import sqlite3
    if slice_measure:
        _release_slice_measure(E, eng, res, slice_measure)
    released = _release_categories(E, eng, res, decisions)
    _release_value_column(E, eng, res, decisions, released)
    colmap = dict(getattr(res, "column_map", {}) or {})
    con = sqlite3.connect(eng.db_path)
    try:
        known = {str(r[0]) for r in con.execute("SELECT column_name FROM %s WHERE table_name = ?" % E.COLUMNS_TABLE,
                                                 (res.table,))}
    finally:
        con.close()
    added = _personal_columns(eng.db_path, res.table, colmap, known)
    if added:
        con = sqlite3.connect(eng.db_path)
        try:
            con.executemany("INSERT INTO %s VALUES (?,?,?,?,?,?,?)" % E.COLUMNS_TABLE,
                            [(res.table, c, k, "value", "pending", None, "flagged by the browser adapter's "
                              "personal-column check") for c, k in added])
            con.commit()
        finally:
            con.close()
    # the exact tokens of every flagged column's values (withheld, coded or kept; a column flagged as free text left
    # out: its words are the file's own vocabulary), read before a "code" decision pseudonymises a column in place and
    # before a withheld one is landed as codes. They never leave the adapter: the themes never show one, and no chart
    # prints a level that holds one (review of the chart registry, 30 Sep 2026: a staff member's name was the second
    # commonest theme word)
    con = sqlite3.connect(eng.db_path)
    try:
        now = [str(r[0]) for r in con.execute("SELECT DISTINCT column_name FROM %s WHERE table_name = ?"
                                              % E.COLUMNS_TABLE, (res.table,))]
    finally:
        con.close()
    raw = {c: _withheld_values(eng.db_path, res.table, [c], dates=False) for c in now}
    flagged = _apply_decisions(E, eng, res, decisions)
    withheld = [f["column"] for f in flagged if f["decision"] == "withhold"]
    # a free-text column's values are scrubbed only whole and long (Scrubber.FREE_TEXT_MIN): its short reviews
    # ("this", "good") are ordinary words
    free = [f["column"] for f in flagged if f["decision"] == "withhold"
            and _REASON_LABEL["free_text"] in str(f.get("kind") or "")]
    values = _withheld_values(eng.db_path, res.table, [c for c in withheld if c not in free])
    free_values = _withheld_values(eng.db_path, res.table, free, dates=False)
    by = {str(f["column"]): _value_tokens(raw.get(str(f["column"]), ())) for f in flagged
          if _REASON_LABEL["free_text"] not in str(f.get("kind") or "")}
    codes = _neutralize_withheld(eng.db_path, res.table, withheld)
    # wave 5d: the columns that looked personal before the table was reshaped (and so never named a series) are listed with the flagged
    # ones, withheld; their values go to the scrubber and the token filter like any withheld column's (they are not in the landed table)
    if aside:
        aside_values = _raw_column_values(sent_bytes, aside)
        for header, label in sorted(aside.items()):
            flagged.append({"column": _engine_slug(header), "kind": str(label), "decision": "withhold"})
            values = values + aside_values.get(header, [])
            by[_engine_slug(header)] = _value_tokens(aside_values.get(header, []))
    for header, label in sorted((kept or {}).items()):           # the visitor kept it: it names the series, and the report says it was flagged
        flagged.append({"column": _engine_slug(header), "kind": str(label), "decision": "keep"})
    sc = Scrubber(values + codes, free_values)
    sc.flag_tokens_by = by
    sc.flag_tokens = frozenset().union(*by.values()) if by else frozenset()
    return flagged, withheld, sc, released


# --------------------------------------------------------------------------- translation
_KIND_WHAT = {
    "null_like": "Placeholder values (such as N/A, NULL, a dash or a blank) were turned into true "
                 "empty values",
    "trim": "Leading, trailing and doubled spaces were removed",
    "numeric": "Read as numbers: every text value converted is counted, whether or not it needed a repair",
    "date": "Read as dates in one format: every text value converted is counted, whether or not it was "
            "written differently",
    "boolean": "Yes/no values written several ways were brought to one spelling",
    "dedupe": "Exact duplicate rows were set aside",
    "range": "Values outside the possible range were set aside",
    "not_future": "Dates in the future were set aside",
    "not_null": "Rows missing a required value were set aside",
    "allowed": "Values outside the allowed list were set aside",
    "series_end": "Rows dated after the series ends were set aside",
    "latest_per_key": "Earlier rows for the same key were set aside by design",
}
_SUBKEY_WHAT = {
    "comma_decimal": "Numbers written with a decimal comma were read as numbers",
    "epoch": "Dates stored as computer timestamps were converted to dates",
    # review v2 (24 Sep 2026): repairs the engine now makes on realistic exports
    "utc": "Timestamps written with a time zone (a Z or a plus-or-minus offset) were converted to UTC",
    "percent": "Percentages written with a % sign were read as fractions of one",
    "magnitude": "Numbers written with a size suffix (k for thousands, M for millions) were read at their full size",
    "unit_suffix": "Numbers written with the unit the column's name states (an h in an hours column) "
                   "were read without the unit",
}


def _fix_entries(clean_res: Any, rules: List[Any]) -> List[Dict[str, Any]]:
    by_name = {r.name: r for r in rules}

    def describe(key: str, flagged: bool) -> Tuple[str, Optional[str], str]:
        base, _, sub = key.partition(".")
        rule = by_name.get(key) or by_name.get(base)
        col = None
        if rule is not None and rule.column and rule.column != "*":
            col = rule.column
        if sub in _SUBKEY_WHAT and not flagged:
            what = _SUBKEY_WHAT[sub]
        elif rule is None:
            what = "Repaired by rule %s" % key
        elif rule.kind == "case" and str((rule.params or {}).get("mode", "")) == "dominant":
            # fixer round (24 Sep 2026): a grouping column's case and spacing variants take its most
            # common spelling ('PARKDALE WALK-UP' becomes 'Parkdale Walk-Up')
            what = ("Letter-case and spacing variants brought to the column's most common spelling, so one "
                    "name is one group: only the values that differed are counted")
        elif rule.kind == "case":
            mode = str((rule.params or {}).get("mode", ""))
            what = ("Letter case made the same across the column (%s): every value rewritten is counted, "
                    "not only the spellings that differed" % (
                        "written in capitals" if mode == "upper" else "written in title case"
                        if mode == "title" else "written in lower case"))
        else:
            what = _KIND_WHAT.get(rule.kind, rule.kind)
        if flagged:
            what = "Flagged and left in place: %s" % what[0].lower() + what[1:]
        return (rule.name if rule is not None else key), col, what

    out = []
    for key, n in sorted((clean_res.fixes_applied or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        if int(n) <= 0:
            continue
        name, col, what = describe(key, False)
        out.append({"rule": key, "column": col, "count": int(n), "what": what})
    for key, n in sorted((clean_res.flags or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        if int(n) <= 0:
            continue
        name, col, what = describe(key, True)
        out.append({"rule": key, "column": col, "count": int(n), "what": what})
    return out


_VERDICT_ORDER = {"RECOMMEND": 0, "WATCH": 1, "INSUFFICIENT": 2}
# within a verdict: what the business can act on, then the forecast, then data quality
_KIND_ORDER = {"business": 0, "forecast": 1, "data_quality": 2}


def _findings(gated: List[Any], kind_of) -> List[Dict[str, Any]]:
    out = []
    for g in gated:
        if getattr(g.fact, "role", "headline") != "headline":
            continue
        out.append({"id": g.fact.id, "claim": _plain(g.fact.claim), "verdict": g.gate.verdict,
                    "why": _plain(g.gate.reason), "kind": kind_of(g.fact),
                    "value": _num(g.fact.value)})
    return out


def _csv_text(df: Any, drop: List[str], reason_fix=None, lines: Optional[List[int]] = None) -> str:
    """A download: withheld columns left out, whole numbers written without ".0", and, when
    `lines` maps every landed row to its line in the visitor's file, a first column
    source_line."""
    if df is None or not len(getattr(df, "columns", [])):
        return ""
    import pandas as pd
    keep = [c for c in df.columns if c not in set(drop)]
    out = df[keep].copy()
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_float_dtype(s):
            v = s.dropna()
            if len(v) and bool(((v % 1) == 0).all()) and bool((v.abs() < 1e15).all()):
                out[c] = s.astype("Int64")
    if reason_fix is not None:
        from northledger.clean import QUARANTINE_COL
        if QUARANTINE_COL in out.columns:
            out[QUARANTINE_COL] = out[QUARANTINE_COL].map(reason_fix)
    if lines:
        out.insert(0, "source_line", [lines[int(i)] for i in out.index])
    return out.to_csv(index=False)


def _ledger_text(paths: Dict[str, str], scrub: Scrubber, engine: Dict[str, str]) -> str:
    doc: Dict[str, Any] = {"what": "NorthLedger evidence ledgers for this run: every figure, the "
                                   "query or refit that produced it, and its gate verdict",
                           "engine_snapshot": engine.get("snapshot", "")}
    for key, p in paths.items():
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                doc[key] = json.load(fh)

    def safe(o: Any) -> Any:
        if isinstance(o, float):
            return o if math.isfinite(o) else None
        if isinstance(o, list):
            return [safe(x) for x in o]
        if isinstance(o, dict):
            return {k: safe(v) for k, v in o.items()}
        return o
    return json.dumps(scrub.clean_tree(safe(doc)), indent=1, allow_nan=False)


def _period_forecast_reason(per: Dict[str, Any]) -> str:
    """Why a quarterly or an annual table has no forecast and no audit of one (the rows_a_month analogue)."""
    return ("No forecast is shown: the table is %s (one value a %s), and the forecast reads monthly series only, so it "
            "has no back-test either." % (per.get("adjective") or str(per.get("kind")) + "ly", per.get("noun") or "period"))


_MONTH_WORDS = re.compile(r"(?i)\bmonth|months\b")
_WINDOW_CUE = re.compile(r"12-month|12 months|the 12 before|the average month|[Mm]onthly total\b")


def _window_sub(text: str, noun: str, nouns: str, w: int) -> str:
    """The window phrases of the engine's sentences (a window is 12 months) in a quarterly or an annual table's own words:
    "the latest 12 months against the 12 before", "the 12 months before", "latest 12 months", "12-month", "12 months"."""
    one = w == 1
    text = re.sub(r"(?i)\bthe latest 12 months against the 12 before\b",
                  "the latest %s against the %s before" % (noun if one else "%d %s" % (w, nouns), noun if one else str(w)), text)
    text = re.sub(r"\bthe 12 months before\b", "the %s before" % (noun if one else "%d %s" % (w, nouns)), text)
    text = re.sub(r"\b(latest|last) 12 months\b", lambda m: "%s %s" % (m.group(1), noun if one else "%d %s" % (w, nouns)), text)
    text = re.sub(r"\bthe 12 before\b", "the %s before" % (noun if one else w), text)
    text = re.sub(r"\b12-month\b", "%s-%s" % (w, noun) if not one else "annual", text)
    text = re.sub(r"\b12 months\b", "%d %s" % (w, nouns) if not one else "a year", text)
    return text


def _window_phrases(text: Any, per: Optional[Dict[str, Any]]) -> Any:
    """Wave 5c. Window phrases only, for a quarterly or an annual table: "the latest 12 months against the 12 before", "the 12
    months before", "latest 12 months", "12-month", "12 months", and the two phrases the core's claims name a period's figure
    with: "the average month" (a mean) and "monthly total" (a sum). Any other text that holds the word "months" ("Months since
    signup", "48 months of history", "monthly") is left alone,
    and a monthly table's text is never touched. For the text the core wrote that the adapter copies into a record of its own
    (a claim, a chart's `supports` and `inputs.op`, an item's `assumes`, a label); `_period_text` is the broad pass over the
    report's own sentences."""
    if not isinstance(text, str) or not per or int(per.get("step") or 1) == 1 or per.get("cadence") or not _WINDOW_CUE.search(text):
        return text
    noun, nouns, w = str(per.get("noun")), str(per.get("nouns")), int(per.get("window") or 12)
    adj = str(per.get("adjective") or "monthly")
    text = re.sub(r"\bthe average month\b", "the average %s" % noun, _window_sub(text, noun, nouns, w))
    return re.sub(r"\b([Mm])onthly total\b", lambda m: (adj if m.group(1) == "m" else adj[:1].upper() + adj[1:]) + " total", text)


_WINDOW_KEYS = frozenset(("claim", "supports", "op", "assumes"))


def _window_rewrite(o: Any, per: Dict[str, Any], key: str = "") -> Any:
    """_window_phrases applied to the records that carry a claim, a chart's `supports` and `inputs.op`, an item's `assumes` and
    the summary's labels (never to a member's name, a label of the data, an id or a machine value)."""
    if isinstance(o, str):
        return _window_phrases(o, per) if key in _WINDOW_KEYS else o
    if isinstance(o, list):
        return [_window_rewrite(x, per, key) for x in o]
    if isinstance(o, dict):
        if key == "labels":
            return {k: (_window_phrases(v, per) if isinstance(v, str) else v) for k, v in o.items()}
        return {k: (_window_rewrite(v, per, str(k)) if k not in ("id", "slug", "estimand", "unit", "kind", "chart", "type") else v)
                for k, v in o.items()}
    return o


def _period_text(text: str, per: Dict[str, Any]) -> str:
    """The engine's own sentences count months ("the average month", "the latest 12 months against the 12 before", "48 months of
    history"); about a quarterly or an annual table they say quarters or years instead (wave 5, gap 4). The numbers are not
    changed: only the unit's name and the window's length."""
    if not isinstance(text, str) or not _MONTH_WORDS.search(text) or int(per.get("step") or 1) == 1 or per.get("cadence"):
        return text                           # (a weekly or a daily table: the core's sentences count the months it aggregated, and say so)
    noun, nouns, w = str(per.get("noun")), str(per.get("nouns")), int(per.get("window") or 12)
    cap = lambda x: x[:1].upper() + x[1:]
    adj = str(per.get("adjective") or "monthly")
    keep = "ZZKEEPZZ"
    text = text.replace("reads monthly series only", keep)             # the forecast's own reason is about monthly series
    # a sentence of the core's change test about a window it could not fill
    text = re.sub(r"only (\d+) of the latest 12 months and (\d+) of the 12 before hold a usable value; the test needs at least "
                  r"(\d+) in each; the file holds no values for [^.;]*",
                  lambda m: "the table holds one value a %s, so each window holds %d: the change test needs at least %s in each"
                  % (noun, w, m.group(3)), text)
    text = _window_sub(text, noun, nouns, w)
    for pat, rep_ in ((r"\bmonth-to-month\b", "%s-to-%s" % (noun, noun)), (r"\bMonth-to-month\b", "%s-to-%s" % (cap(noun), noun)),
                      (r"\bmonthly\b", adj), (r"\bMonthly\b", cap(adj)), (r"\bmonths\b", nouns), (r"\bMonths\b", cap(nouns)),
                      (r"\bmonth\b", noun), (r"\bMonth\b", cap(noun))):
        text = re.sub(pat, rep_, text)
    return text.replace(keep, "reads monthly series only")


_PERIOD_TEXT_KEYS = frozenset(("claim", "why", "text", "title", "subtitle", "summary", "reason", "label", "what", "headline",
                               "not_run_reason", "grade_label", "assumptions", "note", "why_shown", "what_happened",
                               "whats_next", "what_to_do", "cannot_answer", "needed_to_upgrade", "scale", "lines"))


def _period_rewrite(o: Any, per: Dict[str, Any], key: str = "") -> Any:
    """_period_text applied to the sentences of a report (never to a column's name, an id or a machine value)."""
    if isinstance(o, str):
        return _period_text(o, per) if key in _PERIOD_TEXT_KEYS or key == "" else o
    if isinstance(o, list):
        return [_period_rewrite(x, per, key) for x in o]
    if isinstance(o, dict):
        return {k: (_period_rewrite(v, per, str(k)) if k not in ("id", "slug", "estimand", "unit", "kind", "chart", "type") else v)
                for k, v in o.items()}
    return o


def _row_artifact(r: Any, db_path: str, row_s: Any, layout: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Why the monthly row count is no business series (P0-13, nl_inference.row_series_artifact), or None: its
    months all hold the same number of rows (a cube, a one-row-a-month slice), the file is a long table read one
    column per series, or each row is one date (a daily rate: the rows a month are the calendar's days)."""
    import nl_inference as _ni
    from northledger import forecast as _fc
    from northledger._sqlite import connect_ro
    con = connect_ro(db_path)
    try:
        counts = [_num(v) for m, v in _fc.read_series(con, row_s.sql) if m is not None]
        per_month: List[Tuple[int, int]] = []
        tname = getattr(getattr(r.measure, "table", None), "name", None)
        if tname:
            per_month = [(int(a), int(b)) for _m, a, b in con.execute(
                'SELECT _month, COUNT(*), COUNT(DISTINCT _d) FROM "%s" WHERE _month IS NOT NULL GROUP BY _month'
                % str(tname).replace('"', '""'))]
    except Exception:  # noqa: BLE001 - without the counts the row forecast stands as the engine made it
        return None
    finally:
        con.close()
    got = _ni.row_series_artifact(counts, per_month, layout)
    if got is not None:
        got.update(series=str(row_s.label), slug=str(row_s.slug))
    return got


def _forecast_block(r: Any, db_path: str, story_next: List[str],
                    layout: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The forecast block (v1 keys and P0-13's): the engine's first forecast series, never a row count that the
    table's layout or the calendar fixes (row_forecast_dropped: when no other series was forecast, the block is not
    available and says why); `audit` is nl_inference.forecast_audit of the series shown (or dropped), `trusted`
    its verdict. _V2 reads the same series (r._nl_forecast_slug)."""
    from northledger import forecast as _fc
    from northledger._sqlite import connect_ro
    import nl_inference as _ni
    block = blank_report()["forecast"]
    per = (layout or {}).get("period") if isinstance(layout, dict) else None
    if isinstance(per, dict) and int(per.get("step") or 1) != 1:
        # wave 5, gap 4: the forecast and its audit read monthly series; a quarterly or an annual table has none to give them
        block["reason"] = _period_forecast_reason(per)
        block["frequency"] = str(per.get("kind"))
        return block
    series = list(getattr(r.measure, "series", []) or [])
    if not series:
        reasons = list(getattr(r.measure, "unmeasured", []) or [])
        block["reason"] = _plain(reasons[0]) if reasons else "No monthly series was found to forecast."
        return block
    row_s = next((x for x in series if x.slug == "monthly_rows"), None)
    dropped = _row_artifact(r, db_path, row_s, layout) if row_s is not None else None
    block["row_forecast_dropped"] = dropped
    s = next((x for x in series if x.slug in r.forecasts and not (dropped and x.slug == "monthly_rows")), None) \
        or next((x for x in series if x.slug in r.forecasts), series[0])
    try:
        r._nl_forecast_slug = s.slug
    except AttributeError:  # pragma: no cover - the engine's result is a plain dataclass
        pass
    block["label"] = str(s.label)                 # the series' plain label, for the AI report writer
    con = connect_ro(db_path)
    try:
        rows = _fc.read_series(con, s.sql)
    finally:
        con.close()
    block["series"] = [{"month": str(m), "actual": _num(v)} for m, v in rows
                       if m is not None and _num(v) is not None]
    fr = r.forecasts.get(s.slug)
    verdicts = {g.fact.id: g.gate for g in r.gated}
    gate = verdicts.get("forecast.%s.next" % s.slug) or verdicts.get("forecast.%s.history_months" % s.slug)
    block["verdict"] = gate.verdict if gate is not None else None
    if fr is not None:
        # P0-13: the forecast shown, back-tested at rolling origins against seasonal naive (the core is unchanged)
        try:
            months, values = _fc.prepare_series(rows, fill=getattr(s, "fill", "zero") or "zero")
            block["audit"] = _ni.forecast_audit(months, values, fr)
            block["audit"]["series"] = str(s.label)
            block["trusted"] = bool(block["audit"]["trusted"])
            if dropped and s.slug == "monthly_rows":
                # kept for the record only: a forecast that is not shown is not trusted, whatever its back-test says
                block["audit"]["shown"] = False
                block["audit"]["trusted"] = block["trusted"] = False
                block["audit"]["grade_label"] = "the engine's grade (no forecast of this series is shown)"
        except Exception:  # noqa: BLE001 - the audit is an addition; the engine's forecast stands without it
            if os.environ.get("NL_BROWSER_STRICT"):
                raise
    if dropped and s.slug == "monthly_rows":
        block["reason"] = "No forecast of the rows a month is shown: %s." % dropped["reason"]
        return block
    if fr is None:
        line = next((x for x in story_next if s.label in x or s.slug.replace("_", " ") in x), "")
        block["reason"] = _plain(line) or (_plain(gate.reason) if gate is not None else
                                           "The series could not support a forecast.")
        return block
    champ = fr.models.get(fr.champion, {})
    block["champion"] = _fc.PLAIN_NAME.get(fr.champion, fr.champion)
    block["baseline_won"] = bool(fr.baseline_won)
    hits, n = fr.coverage.get("hits"), fr.coverage.get("n")
    block["backtest"] = {"mape": _num(champ.get("mape")), "mase": _num(champ.get("mase")),
                         "coverage": _num(100.0 * float(hits) / float(n)) if n else None}
    if gate is None or gate.verdict == "INSUFFICIENT":
        # The engine does not offer a forecast it could not check, not even as a guide.
        line = next((x for x in story_next if "not offered" in x or "withheld" in x), "")
        block["reason"] = _plain(line) or (_plain(gate.reason) if gate is not None else "")
        return block
    block["available"] = True
    block["reason"] = _plain(gate.reason)
    block["forecast"] = [{"month": str(p["month"]), "value": _num(p.get("point")),
                          "lo": _num(p.get("lo80")), "hi": _num(p.get("hi80"))} for p in fr.forward]
    return block


def _demote_row_forecast_lines(lines: List[str], dropped: Optional[Dict[str, Any]], labels: List[str]) -> List[str]:
    """The story's "what's next" with a dropped row forecast (P0-13) said in one line: the engine's lines from the
    row series' own first line up to the next forecast series' first line become "No forecast of ... is shown: why"."""
    if not dropped:
        return lines
    out: List[str] = []
    skip = False
    for x in lines:
        if x.startswith(dropped["series"]) or x.startswith("No forecast of %s" % dropped["series"]):
            if not skip:
                out.append("No forecast of %s is shown: %s." % (dropped["series"], dropped["reason"]))
            skip = True
            continue
        if skip and any(x.startswith(lb) for lb in labels if lb != dropped["series"]):
            skip = False
        if not skip:
            out.append(x)
    return out


# The core's own sentence about how its month-ahead range did on its replay ("Its month-ahead 80% range held in 22 of 24
# replayed months, each range built only from errors known at the time."), which disagrees with the audit's count of the
# range actually shown (21 of 23 at 1 month: a different replay). With an audit, only the audit's evidence is stated
# (wave 4, track B step 0).
_CORE_REPLAY_SENTENCE = re.compile(
    r"(?:Its|The) month-ahead \d+(?:\.\d+)?% range held in \d+ of \d+ replayed months?"
    r"(?:,? each range built only from errors known at the time)?\.")


def _audit_sentence(audit: Dict[str, Any]) -> str:
    """The audit's own evidence as one sentence ("Its 80% range was back-tested: held 21 of 23 at 1 month, ...")."""
    lab = str(audit.get("label") or "").strip().rstrip(".")
    if not lab:
        return ""
    if lab.startswith("back-tested: "):
        lab = "was " + lab
    return "Its 80%% range %s." % lab


def _one_forecast_evidence(rep: Dict[str, Any]) -> None:
    """With rep["forecast"]["audit"], the story's and the summary's sentences about the core's replay of its range
    ("held in 22 of 24 replayed months") become the audit's own ("back-tested: held 21 of 23 at 1 month, ..."), so a
    report states ONE count of how the range held. A sentence is rewritten in the block of the series the audit was
    run on (the block that starts with its label), or the only one there is; the rest of the line is kept."""
    fc = rep.get("forecast") if isinstance(rep.get("forecast"), dict) else {}
    au = fc.get("audit") if isinstance(fc.get("audit"), dict) else None
    new = _audit_sentence(au) if au and au.get("horizons") else ""
    if not new:
        return
    series = str(au.get("series") or fc.get("label") or "")
    story = rep.get("story") if isinstance(rep.get("story"), dict) else {}
    lines = [x for k in ("what_happened", "why", "whats_next", "what_to_do") for x in story.get(k) or []
             if isinstance(x, str)]
    lines += [str(x.get("text")) for x in (rep.get("summary") or {}).get("lines") or [] if isinstance(x, dict)]
    hits = sum(len(_CORE_REPLAY_SENTENCE.findall(x)) for x in lines)
    if not hits:
        return

    def fix(items: List[Any]) -> List[Any]:
        out, block = [], series
        for x in items:
            if not isinstance(x, str):
                out.append(x)
                continue
            m = re.match(r"^(monthly [^:]+?|[A-Za-z][^:]{0,80}?) for \d{4}-\d{2}:", x)
            if m:
                block = m.group(1)
            if hits == 1 or block == series or not series:
                x = _CORE_REPLAY_SENTENCE.sub(lambda _m: new, x, count=1)
            out.append(x)
        return out
    for k in ("what_happened", "why", "whats_next", "what_to_do"):
        if isinstance(story.get(k), list):
            story[k] = fix(story[k])
    for ln in (rep.get("summary") or {}).get("lines") or []:
        if isinstance(ln, dict) and isinstance(ln.get("text"), str):
            ln["text"] = _CORE_REPLAY_SENTENCE.sub(lambda _m: new, ln["text"], count=1)


_BIG_NUMBER = re.compile(r"(?<![\w.,$])(\d{1,3}(?:,\d{3}){2,})(?:\.\d+)?(?![\w,])")


def _estimand_units_in_text(text: str, S: Dict[str, Any], column: str) -> str:
    """text with each amount of a million or more printed in the estimand's own units ("$73.0B": nl_structure.money), as the
    headline is, never as raw base units ("73,046,640,000 total_retail_sales"); the measure's column name that follows an
    amount goes with it. A rate or an index is left as it is."""
    if not isinstance(text, str) or S["measure"].get("type") in ("rate", "index") or not _BIG_NUMBER.search(text):
        return text
    NS = _ns()
    slug = re.escape(column or "")

    def one(m: Any) -> str:
        v = float(m.group(0).replace(",", ""))
        return NS.money(v, S)
    out = _BIG_NUMBER.sub(one, text)
    return re.sub(r"(\$?\d[\d.]*[TBMK])\s+%s\b" % slug, r"\1", out) if slug else out


def _estimand_units(rep: Dict[str, Any], S: Dict[str, Any], column: str) -> None:
    """A table read by its structure: every amount the engine's sentences print in raw base units (the core writes
    "73,046,640,000 total_retail_sales") is printed in the estimand's units instead, in the story, the summary and the
    forecast's reason. The numbers themselves are not changed: only how they are written."""
    fix = lambda t: _estimand_units_in_text(t, S, column)
    st = rep.get("story") if isinstance(rep.get("story"), dict) else {}
    for k in ("what_happened", "why", "whats_next", "what_to_do", "cannot_answer"):
        if isinstance(st.get(k), list):
            st[k] = [fix(x) if isinstance(x, str) else x for x in st[k]]
    for ln in (rep.get("summary") or {}).get("lines") or []:
        if isinstance(ln, dict) and isinstance(ln.get("text"), str):
            ln["text"] = fix(ln["text"])
    fc = rep.get("forecast") if isinstance(rep.get("forecast"), dict) else {}
    if isinstance(fc.get("reason"), str):
        fc["reason"] = fix(fc["reason"])


def _estimand_window_text(est: Dict[str, Any]) -> str:
    """"12 months to Jul 2026" (the estimand's latest window); "the 11 matched months of 12 to Jul 2026" when a month the
    headline lacks in one window is left out of both; "Jul 2026" for one month."""
    w = (est.get("comparison") or {}).get("latest") or []
    if not (isinstance(w, list) and len(w) == 2 and all(isinstance(x, str) and len(x) >= 7 for x in w)):
        return ""
    per = est.get("period") if isinstance(est.get("period"), dict) else None
    if per and per.get("cadence"):
        # a weekly or a daily table (wave 5e): "52 weeks to 20 Feb 2023", "the 40 matched weeks to 20 Feb 2023"
        last = _ns()._plabel({"period": per}, w[1])
        used = int(est.get("periods_used") or per.get("window") or 1)
        if est.get("complete") is False and est.get("periods_used") and used < int(per.get("window") or 1):
            return "the %d matched %s of %d to %s" % (used, per["nouns"] if used != 1 else per["noun"], int(per.get("window") or 1), last)
        return "%d %s to %s" % (int(per.get("window") or 1), per["nouns"], last)
    if per and int(per.get("step") or 1) != 1:
        # a quarterly or an annual table: "4 quarters to Q4 2023", "2023", "the 3 matched quarters to Q4 2023"
        Sp = {"period": per}
        last = _ns()._plabel(Sp, w[1])
        used = int(est.get("periods_used") or per.get("window") or 1)
        if est.get("complete") is False and est.get("periods_used") and used < int(per.get("window") or 1):
            # wave 5e (P6): a headline on a subset of the periods says how many of the whole window it rests on
            return "the %d matched %s of %d to %s" % (used, per["nouns"] if used != 1 else per["noun"], int(per.get("window") or 1), last)
        n = int(per.get("window") or 1)
        return last if n == 1 else "%d %s to %s" % (n, per["nouns"], last)
    try:
        y1, m1, y2, m2 = int(w[0][:4]), int(w[0][5:7]), int(w[1][:4]), int(w[1][5:7])
    except ValueError:
        return ""
    n = (y2 - y1) * 12 + (m2 - m1) + 1
    if n < 1 or not 1 <= m2 <= 12:
        return ""
    end = "%s %d" % (_MON[m2 - 1], y2)
    if est.get("complete") is False and est.get("months_used") and int(est["months_used"]) < 12:
        return "the %d matched months of 12 to %s" % (int(est["months_used"]), end)
    return end if n == 1 else "%d months to %s" % (n, end)


def _proper_minus(t: str) -> str:
    return re.sub(r"^(\s*)-(?=[\d$\u20ac\u00a3\u00a5.])", "\\1\u2212", str(t))


def _estimand_headline(rep: Dict[str, Any]) -> Optional[str]:
    """The report's headline composed from its estimand (the core's "Monthly total_retail_sales forecast at
    73,046,640,000 total_retail_sales for 2026-08." says nothing of what the table's headline is), in the format of the
    report writer's own fallback title (insight-proxy report.js engineTitle):
        an official aggregate  "<measure>, <member>, 12 months to Jul 2026: +3.5% ($864.0B) in the published totals"
        CONFIRMED              "<subject>, <window>: +3.5% to $864.0B (CONFIRMED)"
        WATCH                  "<subject>: no settled change in the 12 months to Jul 2026 (+3.5%, WATCH)"
        INSUFFICIENT           "<subject>: no settled change; the data cannot say yet (INSUFFICIENT)"
    The subject is the estimand's measure label and its first slice member that is not the label; nothing is invented.
    None when there is no estimand, or its figures cannot be stated."""
    est = rep.get("estimand") if isinstance(rep.get("estimand"), dict) else None
    if not est:
        return None
    fig = est.get("figures") if isinstance(est.get("figures"), dict) else {}
    meas = est.get("measure") if isinstance(est.get("measure"), dict) else {}
    label = str(meas.get("label") or "").strip()
    member = next((str(x.get("member")) for x in est.get("slice") or [] if isinstance(x, dict) and x.get("member")
                   and str(x.get("member")) != label and str(x.get("member")) != "*"), "")
    subject = label + (", " + member if member else "") if label else member
    if not subject:
        return None
    sm = est.get("single_member") if isinstance(est.get("single_member"), dict) else None
    if sm:
        # one member of a table with no total member: never a national figure, said in the headline too
        subject += " (one member shown, not %s)" % ("a national figure" if sm.get("noun") == "national figure" else
                                                    "the sum of the bases" if sm.get("copies") else "the table's total")
    level = meas.get("type") in ("rate", "index")
    chg = (fig.get("change") if level else fig.get("change_pct")) or {}
    lvl = fig.get("latest") or {}
    chg_t = _proper_minus(str(chg.get("text") or "")) if chg.get("value") is not None else ""
    lvl_t = _proper_minus(str(lvl.get("text") or "")) if lvl.get("value") is not None else ""
    if not chg_t or chg_t == "n/a":
        return None
    win = _estimand_window_text(est)
    lead = subject + (", " + win if win else "") + ": "
    # wave 5g (E): a measure AVERAGED because nothing says it accumulates (a currency with no flow word, a count with no word) is stated as an
    # average level, never as a total: "+2.6% (average level $3.0K)", not "($3.0K) in the published totals". A rate or an index is a level by
    # its nature and keeps the words it has always had.
    avg = str(meas.get("type_basis") or "").startswith("ambiguous") and str(meas.get("aggregation") or "").startswith("mean")
    lvl_in = ("average level %s" if avg else "%s")
    inf = est.get("inference") if isinstance(est.get("inference"), dict) else {}
    sid = (rep.get("scenarios") or {}).get("basis") or {}
    fid = sid.get("finding_id") if isinstance(sid, dict) else None
    f = next((x for x in rep.get("findings") or [] if isinstance(x, dict) and x.get("id") == fid), None) \
        or next((x for x in rep.get("findings") or [] if isinstance(x, dict) and x.get("kind") == "business"
                 and str(x.get("id") or "").endswith(".change")), None)
    grade = str((f or {}).get("grade") or "")
    if inf.get("mode") == "official_aggregate":
        # wave 5e (P8): "in the published totals" only where every total behind the figure was verified; else the words of what it is
        tail = str((est.get("evidence") or {}).get("tail")) if isinstance(est.get("evidence"), dict) else \
            ("" if avg else " in the published totals")
        return lead + chg_t + (" (%s)" % (lvl_in % lvl_t) if lvl_t and lvl_t != "n/a" else "") + tail
    if grade == "CONFIRMED":
        return lead + chg_t + ((" to an average of %s" if avg else " to %s") % lvl_t if lvl_t and lvl_t != "n/a" and not level else "") + " (CONFIRMED)"
    if grade == "WATCH":
        return "%s: no settled change%s (%s, WATCH)" % (subject, " in the " + win if win and not win.startswith("the ")
                                                       else (" in " + win if win else ""), chg_t)
    if grade in ("NOT_ENOUGH_DATA", "INSUFFICIENT"):
        return subject + ": no settled change; the data cannot say yet (INSUFFICIENT)"
    if not grade and est.get("complete") is False and (est.get("months_used") or est.get("periods_used")):
        # a short table: no test of the change was run (the engine's own sentence says so), but the matched periods are compared and the
        # estimand prints that change: the headline says both, never one report that says "no change is tested" and a percent
        return lead + chg_t + (" (%s)" % (lvl_in % lvl_t) if lvl_t and lvl_t != "n/a" else "") + \
            " (the table is too short to test the change; only the matched periods are compared)"
    return None


# --------------------------------------------------------------------------- contract v2
# engine/CONTRACT-v2.md. Everything below reads what the engine already produced for this run
# (its gated facts and their signals, its forecast result, its cleaned table as downloaded, the
# benchmark receipt it ships) and arranges it. The only statistics are the descriptive chart
# data (counts, means, shares, bins, correlations) and the change test's studentised statistic
# from the engine's own estimate and standard error; the tests re-compute the chart data from
# the two CSV downloads.
def _ensure_v2(rep: Dict[str, Any]) -> None:
    """Every key of the blank report present at every level, whatever stopped the run."""
    blank = blank_report()
    for k, v in blank.items():
        if k not in rep:
            rep[k] = v
        elif isinstance(v, dict) and isinstance(rep[k], dict):
            for k2, v2 in v.items():
                rep[k].setdefault(k2, v2)
    rep["reproducibility"]["input_sha256"] = rep["input"]["sha256"]


def _wilson_pct(k: Any, n: Any) -> List[Optional[float]]:
    """95% Wilson score interval of k/n on the 0-100 scale (the engine's forecast.wilson)."""
    if not n:
        return [None, None]
    from northledger.forecast import wilson
    lo, hi = wilson(int(k), int(n))
    # the Wilson interval reaches 0 at k = 0 and 1 at k = n exactly; floating point stops short
    return [0.0 if int(k) == 0 else _num(100.0 * lo), 100.0 if int(k) == int(n) else _num(100.0 * hi)]


def _share(k: Any, n: Any) -> Dict[str, Any]:
    k, n = int(k or 0), int(n or 0)
    return {"k": k, "n": n, "pct": _num(100.0 * k / n) if n else None, "ci": _wilson_pct(k, n)}


def _download_frame(text: str, drop: Iterable[str]) -> Any:
    """The cleaned table exactly as the visitor downloads it (clean_csv), as text cells: the frame
    every chart is computed from, so a chart re-computes from the download to the last digit.
    `drop`: every flagged column (never charted) and the source_line column."""
    import pandas as pd
    if not text:
        return None
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    gone = set(drop) | {"source_line"}
    return df[[c for c in df.columns if c not in gone]]


def _months_of(s: Any) -> Any:
    import warnings
    import pandas as pd
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(s.where(s != ""), errors="coerce").dt.strftime("%Y-%m")


def _numbers_of(s: Any) -> Any:
    import pandas as pd
    return pd.to_numeric(s.where(s != ""), errors="coerce")


def _month_range(a: str, b: str) -> List[str]:
    out: List[str] = []
    if not a or not b:
        return out
    y, m = int(a[:4]), int(a[5:7])
    while "%04d-%02d" % (y, m) <= b:
        out.append("%04d-%02d" % (y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _shift_month(mo: str, k: int) -> str:
    i = int(mo[:4]) * 12 + int(mo[5:7]) - 1 + int(k)
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def _payload(fact: Any) -> Dict[str, Any]:
    try:
        p = json.loads(fact.query)
        return p if isinstance(p, dict) else {}
    except (TypeError, ValueError):
        return {}


def _claim_series(fact: Any, gated: Dict[str, Any]) -> Dict[str, Any]:
    """What a change claim's monthly series is, from the engine's own claim key and test
    diagnostics (review v2, 24 Sep 2026: a claim key is no longer always a column name):
      kind    'volume' (rows a month), 'volume_subset' (rows a month in a subset, e.g. like for
              like), 'total' (a monthly sum of a money or count column, maybe of one kind of row)
              or 'measure' (a monthly average of a column)
      column  the column whose values are counted, summed or averaged (None for rows)
      columns every column the claim reads besides the date
      unit    the words for one value of the series
      split   the engine's kind split for a total ({column, level, group}) or None"""
    key = str(getattr(fact, "claim_key", "") or "")
    t = getattr(fact, "test", None) or {}
    if key == "volume":
        return {"kind": "volume", "column": None, "columns": [], "unit": "rows", "split": None}
    if key.startswith("volume:"):
        cols: List[str] = []
        parent = gated.get(str(t.get("like_for_like_of") or ""))
        comp = ((getattr(parent.fact, "test", None) or {}).get("composition") if parent is not None else None) or {}
        if comp.get("column"):
            cols = [str(comp["column"])]
        return {"kind": "volume_subset", "column": None, "columns": cols, "unit": "rows", "split": None}
    parent = gated.get(str(t.get("like_for_like_of") or "")) if t.get("like_for_like_of") else None
    pt = (getattr(parent.fact, "test", None) or {}) if parent is not None else {}
    if key.startswith("total:") and pt.get("total_of"):
        # a like-for-like total, a claim of its own: its parent total's column and kind split, and
        # the category whose levels it keeps
        base = _claim_series(parent.fact, gated) if not t.get("total_of") else \
            _claim_series(types.SimpleNamespace(claim_key=key, test=dict(t, like_for_like_of=None)), gated)
        comp = pt.get("composition") or {}
        cols = base["columns"] + ([str(comp["column"])] if comp.get("column") and comp["column"] not in base["columns"] else [])
        return dict(base, columns=cols, unit=base["unit"] + ", like for like")
    if key.startswith("total:") and t.get("total_of"):
        col = str(t["total_of"])
        sp = t.get("kind_split") or None
        where = ""
        if sp:
            where = (", %s %s" % (sp["column"], sp["level"]) if sp.get("group") != "other"
                     else ", other %s values" % sp["column"])
        return {"kind": "total", "column": col, "columns": [col] + ([str(sp["column"])] if sp else []),
                "unit": "total %s a month%s" % (col, where), "split": sp}
    return {"kind": "measure", "column": key, "columns": [key], "unit": "mean of %s" % key, "split": None}


def _pair_counts(a: Any, b: Any) -> Dict[Tuple[str, str], int]:
    """Rows for each (a, b) pair of two aligned text series, in one grouped count."""
    import pandas as pd
    df = pd.DataFrame({"a": a.values, "b": b.values})
    return {(str(x), str(y)): int(n) for (x, y), n in df.groupby(["a", "b"]).size().items()}


def _nums(values: Iterable[Any]) -> List[Optional[float]]:
    return [_num(v) for v in values]


class _V2:
    """Builds the contract-v2 blocks for one run. `ana` is the business analysis when it is shown
    (None when it did not run or its date column is withheld)."""

    def __init__(self, rep: Dict[str, Any], audit: Any, ana: Any, th: Any, cr: Any, db_path: str,
                 flagged: List[Dict[str, str]], withheld: List[str], pub: Any, as_of: str,
                 objective: str, reasons: Dict[str, int], rules: List[Any],
                 plan_measure: Optional[Tuple[str, str]] = None, names: Optional[Dict[str, str]] = None) -> None:
        self.rep, self.audit, self.ana, self.th, self.cr = rep, audit, ana, th, cr
        # the plan's primary measure (its landed name, its semantic type) and each planned measure's name in words
        self.plan_measure = plan_measure if plan_measure and plan_measure[0] else None
        self.names = dict(names or {})
        self.db_path, self.pub, self.as_of, self.objective = db_path, pub, as_of, objective
        self.flagged = [f["column"] for f in flagged]
        self.withheld = set(withheld)
        self.reasons, self.rules = reasons, rules
        self.gated = {g.fact.id: g for g in list(audit.gated) + (list(ana.gated) if ana is not None else [])}
        self.frame = _download_frame(rep["downloads"]["clean_csv"], self.flagged)
        self.fr = None
        self.slug = None
        if ana is not None:
            series = list(getattr(ana.measure, "series", []) or [])
            if series:
                # the series _forecast_block shows (never a row count the layout or the calendar fixes, P0-13)
                want = getattr(ana, "_nl_forecast_slug", None)
                s = next((x for x in series if x.slug == want), None) or \
                    next((x for x in series if x.slug in ana.forecasts), series[0])
                self.slug, self.fr = s.slug, ana.forecasts.get(s.slug)
        w = (ana.measure.to_dict().get("window") if ana is not None else None) or None
        self.window = {"start": w["start"], "end": w["end"]} if w and w.get("start") else None
        self.wmonths = _month_range(self.window["start"], self.window["end"]) if self.window else []
        self.date = ana.roles.date if ana is not None and ana.roles.date not in self.flagged else None
        self.measures = [m for m in (ana.roles.measures if ana is not None else []) if m not in self.flagged]
        self.dims = [d for d in (ana.roles.dimensions if ana is not None else []) if d not in self.flagged]
        self.month = None
        if self.frame is not None and self.date and self.date in self.frame.columns:
            self.month = _months_of(self.frame[self.date])
        # why no chart can place rows by month, when none can
        self.no_month = ("the business analysis did not run" if ana is None else
                         "the date column is flagged as possible personal data, so no chart places rows by "
                         "its months" if ana.roles.date and ana.roles.date in self.flagged else
                         "the file has no date column the engine could read" if not ana.roles.date else
                         "no analysis window")
        self.charts: List[Dict[str, Any]] = []
        self.suppressed: List[Dict[str, str]] = []
        # money (review of the site, 24 Sep 2026: "82,201 (80% range 74,149.91 to 123,139.33)"): the
        # series the engine totals as money (its additive kind "money"), by forecast series and by
        # claim key, so the page prints them in whole units. A like-for-like claim of a money total
        # is money too.
        self.money_slugs: Set[str] = set()
        self.money_keys: Set[str] = set()
        for g in self.gated.values():
            t = getattr(g.fact, "test", None) or {}
            if t.get("additive") == "money":
                if t.get("series_slug"):
                    self.money_slugs.add(str(t["series_slug"]))
                self.money_keys.add(str(g.fact.claim_key or ""))
        for g in self.gated.values():
            t = getattr(g.fact, "test", None) or {}
            par = self.gated.get(str(t.get("like_for_like_of") or ""))
            if par is not None and str(par.fact.claim_key or "") in self.money_keys:
                self.money_keys.add(str(g.fact.claim_key or ""))
                if t.get("series_slug"):
                    self.money_slugs.add(str(t["series_slug"]))

    def value_scale(self, key: Optional[str] = None, slug: Optional[str] = None) -> Optional[str]:
        """'money' for a series the engine totals as money (the page prints it in whole units)."""
        return "money" if ((key is not None and key in self.money_keys)
                           or (slug is not None and slug in self.money_slugs)) else None

    # ---------------------------------------------------------------- engine and provenance
    def engine(self) -> None:
        import northledger
        from northledger import loop as _loop
        e = self.rep["engine"]
        env = _loop.environment()
        e["semver"] = str(getattr(northledger, "__version__", ""))
        e["decision_code_snapshot"] = _decision_code()
        e["environment"] = {k: env.get(k) for k in ("python", "numpy", "pandas", "sqlite", "pyodide")}
        e["restated_since_previous"] = None
        e["benchmark"] = self.benchmark()

    def benchmark(self) -> Dict[str, Any]:
        from northledger import forecast as _fc
        from northledger import gate as _gate
        b = _blank_benchmark()
        ev = _fc._EVIDENCE_CACHE.get("default") if "default" in _fc._EVIDENCE_CACHE else \
            _fc.default_coverage_evidence()
        if ev:
            b["forecast_coverage_80"] = {k: ({"lo": _num(ev[k]["lo"]), "hi": _num(ev[k]["hi"])}
                                             if ev.get(k) else None) for k in ("steady", "momentum")}
        rec, why = _gate.load_benchmark_receipt()
        if rec is None:
            b["note"] = why
            return b
        b["available"] = True
        b["snapshot"] = str((rec.get("change_path_snapshot") or {}).get("id") or "")[:12]
        gated_cells = [c for c in rec.get("cells") or [] if c.get("gated") and c.get("n")]
        if gated_cells:
            w = max(gated_cells, key=lambda c: (float(c["k"]) / float(c["n"]), int(c["n"]), c["name"]))
            b["worst_cell"] = _cell_out(w)
            b["worst_cell"]["above_target"] = bool(b["worst_cell"]["lower95"] is not None
                                                   and b["worst_cell"]["lower95"] > TARGET_FALSE_CONFIRM_PCT)
        b["certification"] = _certification(gated_cells)
        pm = self.primary_gated()
        t = (getattr(pm.fact, "test", None) or {}) if pm is not None else {}
        if pm is not None and t.get("n") and t.get("sigma_hat") is not None and t.get("phi_hat") is not None:
            # the engine's own claim type and rows a month (gate.claim_type: a monthly total or a
            # like-for-like count carries counting noise too, and records its rows a month apart)
            kind = _gate.claim_type(pm.fact)
            level, rows, eff = _claim_level(pm.fact)
            b["file"] = {"for_finding": pm.fact.id, "claim": kind, "months": int(t["n"]),
                         "cv_pct": _num(100.0 * float(t["sigma_hat"])), "phi": _num(t["phi_hat"]),
                         "rows_a_month": _num(rows), "effective_rows_a_month": _num(eff),
                         "amount_cv": _num(t.get("amount_cv")) if kind == "total" else None}
            routed = _gate.uncertified_condition(pm.fact)
            if routed is not None:
                # a monthly total is routed on its EFFECTIVE rows a month against the totals' own
                # line (gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS); a count on its rows against the counts'
                # (fixer round, 24 Sep 2026: "217 rows a month, under 200" printed the counts' rule)
                total = routed.get("effective_rows_a_month") is not None
                b["routed"] = {"for_finding": pm.fact.id, "condition": routed["condition"],
                               "kind": "total" if total else "count",
                               "rows_a_month": _num(routed["rows_a_month"]),
                               "min_rows_a_month": int(_gate.ROUTE_MIN_ROWS_A_MONTH),
                               "effective_rows_a_month": _num(routed.get("effective_rows_a_month")),
                               "min_effective_rows_a_month": _num(_gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS)
                               if total else None,
                               "amount_cv": _num(routed.get("amount_cv")) if total else None}
            # matched as the gate matches the cell it quotes: the claim's own type and seasonality,
            # a total on its effective rows a month
            seas = t.get("seasonal")
            cell = _gate.nearest_benchmark_cell(rec, float(t["n"]), float(t["sigma_hat"]), float(t["phi_hat"]), level,
                                                claim=kind, seasonal=None if seas is None else bool(seas))
            if cell is not None:
                b["matched_cell"] = dict(_cell_out(cell), for_finding=pm.fact.id)
                b["match"] = _match_kind(b["file"], b["matched_cell"])
                far = _gate.benchmark_far(t["phi_hat"], cell, [c for c in rec.get("cells") or [] if c.get("n")
                                                              and str(c.get("claim") or "volume") == kind]) \
                    if hasattr(_gate, "benchmark_far") else None
                if far:
                    # no measured condition is like this file: the page quotes no rate for it
                    b["match"] = "none"
                    b["file"]["unlike"] = self.pub(far)
            b["power_matched"] = self.power_for(pm.fact)
        if b["matched_cell"] is None:
            b["note"] = "no tested change claim to match a benchmark condition to"
        return b

    def full_receipt(self) -> Optional[Dict[str, Any]]:
        """The benchmark receipt beside the engine (benchmark/engine_benchmark.json: the full one
        in the source tree, the pack's cut of it in the browser), only when it measured this very
        decision code (as the forecast coverage it also carries); else None."""
        if hasattr(self, "_full_receipt"):
            return self._full_receipt
        from northledger import forecast as _fc
        rec = None
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(_fc.__file__))),
                            "benchmark", "engine_benchmark.json")
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            got = str(((rec.get("decision_code_snapshot") or {}).get("id")) or "")
            want = _decision_code()
            if not got or not want or not (got.startswith(want) or want.startswith(got)):
                rec = None
        except (OSError, ValueError, AttributeError):
            rec = None
        self._full_receipt = rec
        return rec

    def interval_coverage_for(self, fact: Any) -> Optional[Dict[str, Any]]:
        """T2 (wave 4): the measured coverage of a change claim's 95% interval in the benchmark condition nearest it,
        matched as the gate matches the cell it quotes (the claim's own type, months, noise, estimated momentum,
        seasonality and, for a count or a total, its (effective) rows a month), within the claim's momentum bucket
        (nl_inference.interval_coverage). None when no receipt measured this code or the claim has no diagnostics."""
        import nl_inference as _ni
        from northledger import gate as _gate
        t = getattr(fact, "test", None) or {}
        if not (t.get("n") and t.get("sigma_hat") is not None and t.get("phi_hat") is not None):
            return None
        rec = self.full_receipt()
        if rec is None:
            return None
        seas = t.get("seasonal")
        cov = _ni.interval_coverage(rec, _gate.claim_type(fact), float(t["n"]), float(t["sigma_hat"]),
                                    float(t["phi_hat"]), _claim_level(fact)[0], None if seas is None else bool(seas))
        if cov is None:
            return None
        return {k: (_num(v) if isinstance(v, float) else v) for k, v in cov.items()}

    def coverage_words(self) -> None:
        """T2 / AM5: the core's promise that an interval was "built to hold the true change 95% of the time" becomes
        what the benchmark measured for the condition nearest the claim, in the finding's why, its WATCH reason and
        the story's sentence about that interval ("labelled 95%; coverage not measured ..." without a receipt)."""
        import nl_inference as _ni
        from northledger import gate as _gate
        from northledger.narrate import story_number as SN
        story_tag = "It was " + _gate.interval_caution(types.SimpleNamespace(effect_ci_low=0.0, test={}),
                                                       with_level=False) + "."
        by_range: List[Tuple[str, str]] = []
        for f in self.rep["findings"]:
            g = self.gated.get(f["id"])
            eff = f.get("effect") or {}
            if g is None or eff.get("ci") is None or f.get("estimand") != "ratio_of_average_month":
                continue
            core = _gate.interval_caution(g.fact)
            if not core:
                continue
            ours = _ni.coverage_clause(eff.get("coverage"), eff.get("level"))
            for key in ("why",):
                if isinstance(f.get(key), str):
                    f[key] = f[key].replace(core, ours)
            if f.get("watch") and isinstance(f["watch"].get("reason"), str):
                f["watch"]["reason"] = f["watch"]["reason"].replace(core, ours)
            lo, hi = self.gated.get(f["id"] + ".ci_low"), self.gated.get(f["id"] + ".ci_high")
            if lo is not None and hi is not None:
                by_range.append(("from %s to %s" % (SN(lo.fact.value, lo.fact.unit, signed=True),
                                                    SN(hi.fact.value, hi.fact.unit, signed=True)),
                                 _ni.coverage_sentence(eff.get("coverage"), eff.get("level"))))
        st = self.rep["story"]
        for k in ("what_happened", "why", "whats_next"):
            lines = []
            for x in st.get(k) or []:
                if story_tag in x:
                    hit = [sent for rng, sent in by_range if rng in x]
                    x = x.replace(story_tag, hit[0] if len(set(hit)) == 1 else
                                  "It is a 95% interval by construction; its measured coverage is stated with the "
                                  "finding.")
                lines.append(x)
            st[k] = lines

    def power_for(self, fact: Any) -> Optional[Dict[str, Any]]:
        """The measured power nearest a tested change claim: the receipt's cells where a true
        change was planted (all at one size, POWER_SHIFT), matched to the claim's own months,
        noise, estimated momentum and, for a volume, rows a month, by the engine's own matching
        (gate.nearest_benchmark_cell). None when no receipt measured this code or the claim has no
        test diagnostics; {"routed": ...} when the claim is held at WATCH by rule, whatever its size."""
        from northledger import gate as _gate
        t = getattr(fact, "test", None) or {}
        if not (t.get("n") and t.get("sigma_hat") is not None and t.get("phi_hat") is not None):
            return None
        if _gate.uncertified_condition(fact) is not None:
            return {"routed": True, "cell": None}
        rec = self.full_receipt()
        if rec is None:
            return None
        cells = []
        for r in (rec.get("results") or {}).get("change") or []:
            c = r.get("cell") or {}
            if c.get("role") != "alt" or not r.get("n") or r.get("k_confirm") is None \
                    or abs(float(c.get("shift") or 0.0) - POWER_SHIFT) > 1e-9:
                continue
            cells.append({"claim": c.get("claim"), "name": c.get("name"), "months": c.get("months"), "cv": c.get("cv"),
                          "phi_hat_mean": r.get("phi_hat_mean"), "level": c.get("level"), "n": int(r["n"]),
                          "k": int(r["k_confirm"]), "shift": float(c["shift"]), "cp95": r.get("clopper_pearson95"),
                          "gated": bool(c.get("gated")), "amount_sd": c.get("amount_sd")})
        kind = _gate.claim_type(fact)
        cells = [c for c in cells if c["claim"] == kind] or cells
        if not cells:
            return None
        level = _claim_level(fact)[0]
        cell = _gate.nearest_benchmark_cell({"cells": cells}, float(t["n"]), float(t["sigma_hat"]),
                                            float(t["phi_hat"]), level)
        if cell is None:
            return None
        out = _cell_out(cell)
        out["shift_pct"] = _num(100.0 * cell["shift"])
        return {"routed": False, "cell": out}

    def primary_gated(self) -> Any:
        """The report's primary claim: the gate's primary family, unless the AI plan named a measure the engine tested
        and the gate's primary is not about it (the live baseline of 30 Sep 2026: the plan named the exchange rate and
        the report led with the row count). Then the engine's claim for that measure leads: a level's (or a rate's,
        a price's, a rating's) average month, an amount's or a count's first total in the engine's order (the one the
        scenarios break down), else its average. The row count never leads when the plan named a measure the engine
        tested. The gate's own grades and families are unchanged."""
        tested = [g for g in self.shown() if g.fact.test and g.fact.test.get("ran")]
        prim = [g for g in tested if (g.gate.signals or {}).get("family") == "primary"]
        if self.plan_measure:
            m, st = self.plan_measure

            def about(g: Any) -> bool:
                k = str(g.fact.claim_key or "")
                return not (g.fact.test or {}).get("like_for_like_of") and (
                    k == m or k == "total:" + m or k.startswith("total:%s:" % m))
            if not (prim and about(prim[0])):
                mine = [g for g in tested if about(g)]
                avg = [g for g in mine if str(g.fact.claim_key or "") == m]
                tot = [g for g in mine if g not in avg]
                pick = (tot + avg) if st in ("flow_amount", "count") else (avg + tot)
                if pick:
                    return pick[0]
        return (prim or tested or [None])[0]

    def shown(self) -> List[Any]:
        return [self.gated[f["id"]] for f in self.rep["findings"] if f["id"] in self.gated]

    # ---------------------------------------------------------------- what the file covers
    def composition_of(self, fact: Any) -> Optional[Dict[str, Any]]:
        """A change claim's composition, as the engine recorded it (review v2: a category whose
        levels start or stop inside the modelled months): the category, the months where levels
        start ('entered') or stop ('left'), the engine's own words, and the like-for-like
        comparison on the levels present throughout. None when the file's coverage did not change,
        or when the category is flagged as possible personal data (its levels are data values)."""
        comp = (getattr(fact, "test", None) or {}).get("composition") or {}
        col = str(comp.get("column") or "")
        if not col or col in self.flagged:
            return None
        steps = ([{"month": str(m), "kind": "entered", "level": self.pub(str(lv))} for lv, m in comp.get("entered") or []]
                 + [{"month": str(m), "kind": "left", "level": self.pub(str(lv))} for lv, m in comp.get("left") or []])
        steps.sort(key=lambda s: (s["month"], s["kind"], s["level"]))
        return {"column": col, "fact_id": str(comp.get("fact") or "measure.composition"),
                "words": self.pub(str(comp.get("words") or "")), "steps": steps,
                "levels_kept": len(comp.get("stable") or []), "like_for_like": self.like_for_like_of(fact)}

    def like_for_like_of(self, fact: Any) -> Optional[Dict[str, Any]]:
        """The like-for-like comparison of a change claim: a tested claim of its own (its test names
        this claim in like_for_like_of), else the engine's descriptive fact the composition names.
        Numbers are the engine's (a tested claim's estimate and interval; a descriptive fact's
        percent, as a fraction). None when the engine made none."""
        shown = {f["id"] for f in self.rep["findings"]}
        kids = [g for g in self.gated.values() if (getattr(g.fact, "test", None) or {}).get("like_for_like_of") == fact.id
                and g.fact.id in shown]
        if kids:
            k = kids[0].fact
            ci = [_num(k.effect_ci_low), _num(k.effect_ci_high)] if k.effect_ci_level is not None else None
            return {"finding_id": k.id, "fact_id": k.id, "claim": self.pub(k.claim),
                    "estimate": _num(k.effect_size), "ci": ci, "level": _num(k.effect_ci_level) if ci else None,
                    "grade": GRADE.get(kids[0].gate.verdict), "tested": True}
        comp = (getattr(fact, "test", None) or {}).get("composition") or {}
        g = self.gated.get(str(comp.get("like_for_like") or ""))
        if g is None or g.fact.value is None or str(g.fact.unit) != "pct":
            return None
        return {"finding_id": g.fact.id if g.fact.id in shown else None, "fact_id": g.fact.id,
                "claim": self.pub(g.fact.claim), "estimate": _num(float(g.fact.value) / 100.0), "ci": None,
                "level": None, "grade": GRADE.get(g.gate.verdict) if g.fact.id in shown else None,
                "tested": bool(getattr(g.fact, "test", None))}

    def stepped(self, g: Any, eff: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A WATCH change the engine explains by steps where what the file covers changed (its gate
        rule 'composition', with those steps explaining the series): {"kind": "stepped", direction,
        steps[{month, size, levels, fact_id}]}. Its as-filed interval is not a statement about the
        business (fixer round, 24 Sep 2026: "No clear movement: the interval includes zero" beside
        a +87.4% step the engine detected), so the page says it stepped and reads the business
        like for like. None otherwise, or when the category is withheld."""
        sig = g.gate.signals or {}
        t = getattr(g.fact, "test", None) or {}
        if sig.get("rule") != "composition" or not sig.get("composition_explains") \
                or eff.get("scale") != "fraction" or eff.get("estimate") is None:
            return None
        named = (t.get("composition_steps") or {}).get("named") or []
        if not named or self.composition_of(g.fact) is None:
            return None
        return {"kind": "stepped", "direction": "rise" if float(eff["estimate"]) >= 0 else "fall",
                "steps": [{"month": str(x["month"]), "size": _num(x.get("size")),
                           "levels": [[self.pub(str(lv)), str(verb)] for lv, verb in x.get("levels") or []],
                           "fact_id": x.get("fact")} for x in named]}

    def steps_of(self, f: Dict[str, Any], fact: Any, months: List[str]) -> List[Dict[str, Any]]:
        """The months a claim's chart marks, inside its months: where a level of the composition
        category starts or stops (the engine's composition record), and the one level shift the
        engine's step screen found (its test's step month), each with where the engine said it."""
        comp = f.get("composition") or {}
        out = [dict(s, source=comp.get("fact_id")) for s in (comp.get("steps") or [])]
        t = getattr(fact, "test", None) or {}
        st = t.get("step") or {}
        if t.get("screen_kind") == "step" and st.get("month") is not None:
            m = int(st["month"])
            mo = "%04d-%02d" % (m // 12, m % 12 + 1)
            if not any(s["month"] == mo for s in out):
                out.append({"month": mo, "kind": "step", "level": None, "source": fact.id})
        return sorted([s for s in out if s["month"] in months], key=lambda s: s["month"])

    def like_for_like_series(self, fact: Any, sql: str, kind: str) -> Optional[List[Optional[float]]]:
        """The like-for-like monthly series drawn beside a claim's own: a tested like-for-like
        claim's own series (its payload's SQL), else the claim's series SQL restricted to the
        levels present throughout, as the engine's descriptive like-for-like fact restricts its
        two sums (only for a count or a total, whose series SQL is one grouped subquery)."""
        shown = {f["id"] for f in self.rep["findings"]}
        from northledger._sqlite import connect_ro
        for g in self.gated.values():
            t = getattr(g.fact, "test", None) or {}
            if t.get("like_for_like_of") == fact.id and g.fact.id in shown:
                p = self.gated.get("%s.p" % g.fact.id)
                s = _payload(p.fact).get("series_sql") if p is not None else None
                if s:
                    con = connect_ro(self.db_path)
                    try:
                        return [_num(v) for _, v in con.execute(s)]
                    finally:
                        con.close()
        comp = (getattr(fact, "test", None) or {}).get("composition") or {}
        if kind not in ("total", "volume") or not comp.get("stable") or sql.count(" GROUP BY _month") != 1 \
                or self.ana is None:
            return None
        from northledger.measure import qi, ql
        at = self.ana.measure.table
        keep = "LOWER(TRIM(%s)) IN (%s)" % (qi(at.col(str(comp["column"]))),
                                            ", ".join(ql(str(v)) for v in comp["stable"]))
        con = connect_ro(self.db_path)
        try:
            return [_num(v) for _, v in con.execute(sql.replace(" GROUP BY _month", " AND %s GROUP BY _month" % keep, 1))]
        finally:
            con.close()

    def reproducibility(self) -> None:
        from northledger import loop as _loop
        rp = self.rep["reproducibility"]
        rp["engine_snapshot"] = _engine_full_snapshot()
        rp["decision_code_snapshot"] = self.rep["engine"]["decision_code_snapshot"]
        rp["environment"] = dict(self.rep["engine"]["environment"])
        seeds: Dict[str, Any] = {}
        bees: Dict[str, Any] = {}
        policy = None
        for fid, g in sorted(self.gated.items()):
            if g.fact.query_kind == "model":
                p = _payload(g.fact)
                # one recipe per claim: every fact of a change test shares its claim's seed and B,
                # and every fact of a forecast its series' seed
                if p.get("kind") == "northledger.trend_test/1" and p.get("claim"):
                    seeds[str(p["claim"])] = int(p["seed"])
                    bees[str(p["claim"])] = {"test": int(p.get("b") or 0), "interval": int(p.get("ci_b") or 0)}
                elif p.get("kind") == "northledger.forecast/1" and p.get("seed") is not None:
                    seeds[".".join(fid.split(".")[:2])] = int(p["seed"])
            if policy is None and isinstance((g.gate.signals or {}).get("policy"), dict) \
                    and "recommend_q" in g.gate.signals["policy"]:
                policy = dict(g.gate.signals["policy"])
        if policy is None:
            from dataclasses import asdict
            from northledger import gate as _gate
            policy = asdict(_gate.DEFAULT_POLICY)
        rp["seeds"], rp["B"] = seeds, bees
        rp["parameters"] = {"as_of": self.as_of, "objective": self.objective, "window": self.window,
                            "gate_policy": policy,
                            "forecast_config": dict(getattr(self.fr, "config", {}) or {}) or None}
        k = n = failed = 0
        for res in (self.audit, self.ana):
            v = getattr(res, "verification", None) or {}
            k += int(v.get("reproduced", 0) or 0)
            n += int(v.get("checked", 0) or 0)
            failed += int(v.get("failed", 0) or 0)
        rp["figures_reproduced"] = {"k": k, "n": n, "failed": failed}
        _ = _loop

    # ---------------------------------------------------------------- health and cleaning
    def health(self) -> None:
        from northledger import health as _health
        from northledger import measure as _ms
        th, h = self.th, self.rep["health"]
        dims = th.dimensions or {}
        ev = {e.get("key"): e for e in (th.sql_evidence or [])}
        out = []
        idlike = any(c.is_id_like and c.distinct_ratio < 1.0 for c in th.columns)
        for name in _health.DIMENSIONS:
            v = dims.get(name)
            d = {"name": name, "score": _num(v), "applicable": v is not None, "ci": [None, None],
                 "ci_method": "", "k": None, "n": None}
            if v is not None and name == "completeness" and "null_like_cells" in ev and th.n_rows:
                n = int(th.n_rows) * int(th.n_cols)
                k = n - int(ev["null_like_cells"]["value"])
                d.update(k=k, n=n, ci=_wilson_pct(k, n), ci_method="Wilson 95%, non-empty cells of all cells")
            elif v is not None and name == "uniqueness" and "duplicate_rows" in ev and th.n_rows and not idlike:
                n = int(th.n_rows)
                k = n - int(ev["duplicate_rows"]["value"])
                d.update(k=k, n=n, ci=_wilson_pct(k, n), ci_method="Wilson 95%, rows that repeat no other row")
            elif v is not None:
                d["ci_method"] = ("none: a mean of column ratios, not one proportion" if name != "timeliness"
                                  else "none: a staleness decay, not a proportion")
                if name == "uniqueness":
                    d["ci_method"] = "none: an id-like column's repeats lower it, so it is not one proportion"
            out.append(d)
        appl = [d for d in out if d["applicable"]]
        low = min(appl, key=lambda d: d["score"]) if appl else None
        h["dimensions"] = out
        h["score_min"] = low["score"] if low else None
        h["score_mean"] = _num(th.score)
        h["weakest"] = low["name"] if low else None
        h["score"] = h["score_min"]                         # §4.2: the v1 key now carries the min
        # the mark-down for numbers stored as text, which every CSV has: which score it lowers, in plain words
        h["csv_text_numbers"] = _text_numbers(th, dims, h["score_min"], h["score_mean"], set(self.withheld), self.pub)
        # what set the score, in plain words (final evaluation, 1 Oct 2026; _health_explain)
        h["explain"] = _health_explain(out, h["score_min"], h["weakest"], _health_info(th, ev, idlike), h["csv_text_numbers"])
        h["sample"] = {"method": "sampled" if th.sampled else "all", "n": int(th.n_rows), "seed": None}
        h["accuracy"] = dict(ACCURACY_NOT_MEASURED)
        claim_h = _ms.column_claim_health(th)
        by_rule = {r.name: r for r in self.rules}
        q_by_col: Dict[str, Dict[str, int]] = {}
        for key, n in self.reasons.items():
            rule = by_rule.get(str(key).split(":", 1)[0].strip())
            if rule is not None and rule.column and rule.column != "*":
                q_by_col.setdefault(rule.column, {})[rule.name] = q_by_col.get(rule.column, {}).get(rule.name, 0) + int(n)
        fx_by_col: Dict[str, Dict[str, int]] = {}
        for fx in self.rep["cleaning"]["fixes"]:
            if fx.get("column"):
                fx_by_col.setdefault(fx["column"], {})[fx["rule"]] = int(fx["count"])
        clean = self.cr.clean
        cols = []
        for ch in th.columns:
            name = ch.name
            wh = name in self.withheld
            counts = dict(ch.class_counts or {})
            nn = int(ch.n_non_null or 0)
            dom = max(counts, key=lambda k: (counts[k], k)) if counts and nn else None
            val = _share(counts[dom], nn) if dom else _share(0, 0)
            fmt = None
            if dom == "date" and ch.date_formats:
                fmt = max(ch.date_formats, key=lambda k: (ch.date_formats[k], k))
            val["dominant_format"] = fmt or dom
            uni = {"applicable": bool(ch.is_id_like), "k": None, "n": None, "pct": None, "ci": [None, None]}
            if ch.is_id_like:
                uni.update(_share(ch.n_distinct, nn))
                uni["applicable"] = True
            comp = _share(nn, ch.n_rows)
            parts = [("completeness", comp["pct"]), ("validity", val["pct"])]
            if uni["applicable"]:
                parts.append(("uniqueness", uni["pct"]))
            parts = [p for p in parts if p[1] is not None]
            numeric = dates = None
            if not wh and clean is not None and name in getattr(clean, "columns", []):
                import pandas as pd
                s = clean[name]
                if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
                    v = s.dropna().astype(float)
                    if len(v):
                        numeric = {"min": _num(v.min()), "median": _num(v.median()), "max": _num(v.max())}
                elif pd.api.types.is_datetime64_any_dtype(s):
                    v = s.dropna()
                    if len(v):
                        dates = {"min": v.min().date().isoformat(), "max": v.max().date().isoformat(),
                                 "order": ch.day_month_order or None}
            cols.append({
                "name": name, "type": ch.dtype_guess, "n": int(ch.n_rows),
                "flagged": name in self.flagged, "withheld": wh,
                "completeness": comp, "validity": val, "uniqueness": uni,
                "weakest": min(parts, key=lambda p: p[1])[0] if parts else None,
                "claim_health": _num(claim_h.get(name)),
                "distinct": int(ch.n_distinct or 0),
                "top_values": [] if wh else [[self.pub(str(v)), int(n)] for v, n in (ch.top_values or [])],
                "numeric": numeric, "dates": dates,
                "quarantined_by_rule": q_by_col.get(name, {}),
                "fixes_by_rule": fx_by_col.get(name, {}),
                "safe_for": [],
            })
        h["columns"] = cols
        h["missingness"] = self.missingness()

    def missingness(self) -> Dict[str, Any]:
        import numpy as np
        m = {"matrix_columns": [], "by_month": [], "nullity_corr": {"columns": [], "matrix": [], "n": 0},
             "mcar": {"test": "little", "p": None, "conclusion": "not run in this release"}}
        if self.frame is None or self.month is None or not self.wmonths:
            return m
        inwin = self.month.isin(self.wmonths)
        f = self.frame[inwin]
        mw = self.month[inwin]
        cols = [c for c in f.columns]
        m["matrix_columns"] = cols
        empty = {c: (f[c] == "") for c in cols}
        import pandas as pd
        per = pd.DataFrame(empty).groupby(mw.values).sum() if cols else None
        rows = mw.value_counts()
        by = []
        for mo in self.wmonths:
            have = per is not None and mo in per.index
            by.append({"month": mo, "rows": int(rows.get(mo, 0)),
                       "nulls": {c: int(per.at[mo, c]) if have else 0 for c in cols}})
        m["by_month"] = by
        some = [c for c in cols if bool(empty[c].any())]
        if len(some) >= 2 and len(f) >= 3:
            mat = []
            for a in some:
                row = []
                for b in some:
                    x, y = empty[a].to_numpy(float), empty[b].to_numpy(float)
                    row.append(_num(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else None)
                mat.append(row)
            m["nullity_corr"] = {"columns": some, "matrix": mat, "n": int(len(f))}
        return m

    def cleaning(self) -> None:
        c = self.rep["cleaning"]
        c["rules"] = [{"name": r.name, "kind": r.kind,
                       "column": r.column if r.column and r.column != "*" else None,
                       "reason": self.pub(r.reason_key().split(":", 1)[-1].strip())} for r in self.rules]
        qm = self.quarantine_months()
        by: Dict[Optional[str], int] = {}
        for mo in qm:
            by[mo] = by.get(mo, 0) + 1
        c["quarantine_by_month"] = [{"month": mo, "count": int(n)} for mo, n in
                                    sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))]

    def quarantine_months(self) -> List[Optional[str]]:
        """The month of every set-aside row as the engine's analysis read it (its quarantine table,
        written with the cleaner's own date reader), or [] when no analysis ran."""
        if self.ana is None or self.date is None:
            return []
        import sqlite3
        from northledger._sqlite import connect_ro
        at = self.ana.measure.table
        qname = getattr(at, "quarantine_name", None) or ("%s_quarantine" % at)
        con = connect_ro(self.db_path)
        try:
            return [r[0] for r in con.execute('SELECT _month FROM "%s"' % str(qname).replace('"', '""'))]
        except sqlite3.OperationalError:          # no such table: the analysis set nothing aside
            return []
        finally:
            con.close()

    # ---------------------------------------------------------------- findings
    def findings(self) -> None:
        from northledger import forecast as _fc
        from northledger import gate as _gate
        from northledger import stats as _st
        date = self.ana.roles.date if self.ana is not None else None
        offered = bool(self.rep["forecast"].get("available"))
        for f in self.rep["findings"]:
            g = self.gated.get(f["id"])
            fact, sig = g.fact, dict(g.gate.signals or {})
            t = dict(getattr(fact, "test", None) or {})
            change = bool(t)
            is_fc = fact.fact_kind == "forecast"
            f["grade"] = GRADE[f["verdict"]]
            f["role"] = sig.get("family") if sig.get("family") in ("primary", "secondary") else "secondary"
            # a like-for-like claim names the claim it restates on the levels present throughout
            f["parent_id"] = (str(t["like_for_like_of"]) if t.get("like_for_like_of") in self.gated else None)
            f["estimand"], f["unit"] = (("ratio_of_average_month", "month") if change else
                                        ("next_month_value", "month") if is_fc else (None, None))
            trace = [f["id"]]
            eff = {"estimate": _num(fact.value), "ci": None, "level": None, "ci_fcr": None, "fcr_level": None,
                   "method": "", "scale": fact.unit, "unit": None, "note": None, "coverage": None}
            f["inference"] = None                 # T4: an official aggregate's record (_official_inference)
            f["layout_artifact"] = False          # P0-13: a row count the layout or the calendar fixes
            if is_fc and f["id"].endswith(".next") and not fact.unit and \
                    self.value_scale(slug=f["id"][len("forecast."):-len(".next")]):
                # the forecast of a money total: whole units on the page
                eff["scale"] = "money"
            test = None
            no_ratio = change and _gate._no_ratio(fact)
            if no_ratio:
                # monthly averages at or below zero: the change is the difference in the measure's
                # own units (the fact's value, as the engine states it), never a percentage
                eff.update(estimate=_num(fact.value), scale="difference", unit=str(fact.unit or ""),
                           note="a difference in the measure's own units: its monthly averages are at or "
                                "below zero, so a percentage has no meaning for it")
            elif change:
                eff.update(estimate=_num(fact.effect_size), scale="fraction")
            if change:
                if fact.effect_ci_level is not None and not no_ratio:
                    eff.update(ci=[_num(fact.effect_ci_low), _num(fact.effect_ci_high)],
                               level=_num(fact.effect_ci_level), method=fact.effect_ci_method)
                    # T2: how often such an interval held the true change in the benchmark's nearest condition
                    eff["coverage"] = self.interval_coverage_for(fact)
                if fact.effect_ci_adj_level is not None and not no_ratio:
                    eff.update(ci_fcr=[_num(fact.effect_ci_adj_low), _num(fact.effect_ci_adj_high)],
                               fcr_level=_num(fact.effect_ci_adj_level))
                ran = bool(t.get("ran"))
                stat = None
                if ran and t.get("se") and t.get("delta_hat") is not None and t.get("direction"):
                    stat = _num((float(t["delta_hat"]) - _st.bar_log(float(t["bar"]), int(t["direction"])))
                                / float(t["se"]))
                test = {"name": "minimum-effect change test (one-sided, at least the bar)",
                        "method": _visitor(t.get("method", "")), "ran": ran,
                        "not_run_reason": self.pub(str(t.get("not_run"))) if t.get("not_run") else None,
                        "null": "min_effect", "bar": _num(t.get("bar", fact.min_effect)),
                        "n_months": t.get("n") if ran else t.get("months_observed"),
                        "n_rows": int(fact.rows_scanned), "phi_hat": _num(t.get("phi_hat")),
                        "B": t.get("b"), "statistic": stat,
                        "statistic_name": "t = (estimate - bar on the log scale) / SE, AR(1) GLS model of "
                                          "the monthly values" if stat is not None else None,
                        "df": t.get("df"), "p": _num(fact.p_value) if ran else None,
                        "p_mc_interval": _nums(t["mc_interval"]) if t.get("mc_interval") else None,
                        "p_point_null": _num(fact.p_nonzero), "q": _num(sig.get("q_value")),
                        "family": sig.get("family"), "family_size": sig.get("family_size"),
                        "fdr_method": sig.get("family_method"), "family_line": _num(sig.get("family_line"))}
                trace += [x for x in ("%s.p" % f["id"], "%s.p_nonzero" % f["id"], "%s.ci_low" % f["id"],
                                      "%s.ci_high" % f["id"]) if x in self.gated]
            elif is_fc and self.fr is not None and f["id"] == "forecast.%s.next" % self.slug:
                p0 = (self.fr.forward or [{}])[0]
                if offered and f["grade"] != "NOT_ENOUGH_DATA":
                    eff.update(ci=[_num(p0.get("lo80")), _num(p0.get("hi80"))],
                               level=_num(self.fr.config.get("band", 0.8)),
                               method=str(self.fr.config.get("band_method", "")))
                else:
                    # the engine does not offer this forecast: no point and no range reach a
                    # reader (the ledger keeps them)
                    eff.update(estimate=None, note="not offered: the engine could not check this forecast "
                                                   "well enough to offer it")
                bt = dict(self.fr.baseline_test or {})
                test = {"name": "Diebold-Mariano test against seasonal-naive (one-sided, HLN small-sample)",
                        "method": str(bt.get("method", "")), "ran": bt.get("p") is not None,
                        "not_run_reason": None, "null": "no_more_accurate_than_seasonal_naive", "bar": None,
                        "n_months": bt.get("n"), "n_rows": int(fact.rows_scanned), "phi_hat": None, "B": None,
                        "statistic": _num(bt.get("statistic")), "statistic_name": "DM (HLN-corrected)",
                        "df": (int(bt["n"]) - 1) if bt.get("n") else None, "p": _num(bt.get("p")),
                        "p_mc_interval": None, "p_point_null": None, "q": None, "family": None,
                        "family_size": None, "fdr_method": None, "family_line": None}
                trace += [x for x in sorted(self.gated) if x.startswith("forecast.%s." % self.slug) and x != f["id"]]
            elif is_fc and f["id"].endswith(".next") and f["grade"] != "NOT_ENOUGH_DATA":
                # another series' forecast the engine offers (review v2: the money total is charted,
                # the row count and the other totals are not): its point keeps the engine's own 80%
                # range beside it, from the facts the engine stated, never a bare point
                lo, hi = self.gated.get(f["id"] + ".lo80"), self.gated.get(f["id"] + ".hi80")
                fr_o = self.ana.forecasts.get(f["id"][len("forecast."):-len(".next")]) if self.ana is not None else None
                if lo is not None and hi is not None and lo.fact.value is not None and hi.fact.value is not None:
                    eff.update(ci=[_num(lo.fact.value), _num(hi.fact.value)],
                               level=_num((fr_o.config.get("band", 0.8)) if fr_o is not None else 0.8),
                               method=str(fr_o.config.get("band_method", "")) if fr_o is not None else "")
                    trace += [lo.fact.id, hi.fact.id]
            dropped = self.rep["forecast"].get("row_forecast_dropped") or {}
            if is_fc and dropped and f["id"].startswith("forecast.%s." % dropped.get("slug")):
                # P0-13: a row count the table's layout or the calendar fixes: no point and no range reach a reader
                f["layout_artifact"] = True
                eff.update(estimate=None, ci=None, level=None, method="",
                           note="not offered: %s" % self.pub(dropped.get("reason") or ""))
            if is_fc and f["id"].endswith(".next") and f["grade"] == "NOT_ENOUGH_DATA" and eff["estimate"] is not None:
                # any other series' forecast the engine does not offer: no point and no range either
                eff.update(estimate=None, ci=None, level=None, method="",
                           note="not offered: the engine could not check this forecast well enough to offer it")
            f["effect"], f["test"] = eff, test
            f["health"] = _num(fact.claim_health)
            f["checks"] = {"claim_health": _num(fact.claim_health),
                           "drift_screen": {"hit": bool(fact.trend_screen) if change else None,
                                            "reason": self.pub(_plain(fact.trend_screen)) if change else ""},
                           "tipping_point": {"value": None, "units": "", "plausible_range": [None, None],
                                             "source": "not_computed"},
                           "reversal": {"flagged": None, "segment": None}}
            settle = self.pub(_visitor(g.gate.needed_to_upgrade)) or None
            f["watch"] = ({"reason": f["why"],
                           "checks_failed": list(sig.get("checks_failed") or ([sig["rule"]] if sig.get("rule") else [])),
                           "settle": settle, "movement": (self.stepped(g, eff) or _movement(eff, _num(t.get("bar"))))
                           if change else None,
                           "routed": _gate.uncertified_condition(fact) is not None if change else False}
                          if f["grade"] == "WATCH" else None)
            fc_slug = f["id"][len("forecast."):-len(".next")] if (is_fc and f["id"].endswith(".next")) else None
            fc_res = (self.ana.forecasts.get(fc_slug) if (fc_slug and self.ana is not None) else None)
            if fc_res is not None:
                # every CONFIRMED forecast the engine offers, not only the one the charts draw
                f["error_rate"], f["error_rate_note"] = self.forecast_error_rate(g, fc_res)
            else:
                f["error_rate"], f["error_rate_note"] = self.error_rate(g, change)
            if f["error_rate"]:
                trace += list(f["error_rate"]["fact_ids"])
            f["drivers"] = []
            f["composition"] = self.composition_of(fact) if change else None
            if f["composition"] and f["composition"]["like_for_like"]:
                lf = f["composition"]["like_for_like"]
                trace += [x for x in (f["composition"]["fact_id"], lf["fact_id"]) if x in self.gated and x not in trace]
            f["posterior"] = {"shown": False, "p_exceeds_bar": None, "calibration_ref": None}
            pw = self.power_for(fact) if change else None
            rr = _gate.uncertified_condition(fact) if change else None
            f["power"] = {"at_bar": None, "bar": _num(t.get("bar")) if change else None, "months_to_80pct": None,
                          "nearest": (pw or {}).get("cell"), "routed": bool((pw or {}).get("routed")),
                          # the rule that holds it at WATCH, in its own terms (fixer round, 24 Sep 2026)
                          "routed_rule": ({"kind": "total" if rr.get("effective_rows_a_month") is not None else "count",
                                           "rows_a_month": _num(rr.get("rows_a_month")),
                                           "effective_rows_a_month": _num(rr.get("effective_rows_a_month")),
                                           "line": _num(_gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS
                                                        if rr.get("effective_rows_a_month") is not None
                                                        else _gate.ROUTE_MIN_ROWS_A_MONTH)} if rr else None),
                          "design": ("held at WATCH by rule whatever the size of the change, so no power applies"
                                     if (pw or {}).get("routed") else
                                     "measured at one size of true change (%d%%) in simulated conditions; the "
                                     "nearest one to this claim is quoted, not a power computed for this claim"
                                     % round(100 * POWER_SHIFT) if pw else
                                     "not measured for this claim")}
            f["needed_to_upgrade"] = settle or ""
            f["columns_read"] = ([date] + [c for c in _claim_series(fact, self.gated)["columns"] if c != date]) \
                if (change and date) else ([date] if is_fc and date else [])
            f["verified"] = fact.verified
            f["tested_times"] = None
            f["chart_ids"] = []
            f["trace"] = trace
        self.coverage_words()
        by_col: Dict[str, List[str]] = {}
        for f in self.rep["findings"]:
            for c in f["columns_read"]:
                by_col.setdefault(c, []).append(f["id"])
        for col in self.rep["health"]["columns"]:
            col["safe_for"] = by_col.get(col["name"], [])
        _ = _fc

    def error_rate(self, g: Any, change: bool) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        sig = g.gate.signals or {}
        if not change:
            return None, "quoted only beside a change claim"
        if g.gate.verdict != "RECOMMEND":
            return None, "quoted only beside a CONFIRMED change claim"
        ids = list(sig.get("benchmark_fact_ids") or [])
        if not ids:
            m = re.search(r"No measured false-confirm rate is quoted for it: ([^.]*)\.", g.gate.reason or "")
            return None, self.pub(m.group(1)) if m else "no measured rate was quoted"
        vals = {i.rsplit(".", 1)[-1]: self.gated[i].fact.value for i in ids if i in self.gated}
        raw = g.gate.reason or ""
        a = raw.find("Measured, not promised:")
        b = raw.find("[fact:", a)
        sentence = self.pub(_plain(raw[a:b if b > a else len(raw)])) if a >= 0 else ""
        return ({"sentence": sentence, "rate": _num(vals.get("rate")), "lower95": _num(vals.get("lo")),
                 "upper95": _num(vals.get("hi")),
                 "k": int(vals["k"]) if vals.get("k") is not None else None,
                 "n": int(vals["n"]) if vals.get("n") is not None else None,
                 "cell": sig.get("benchmark_cell"), "at_bar": sig.get("benchmark_at_bar"), "fact_ids": ids},
                None)

    def forecast_error_rate(self, g: Any, fr: Any = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """For a CONFIRMED forecast (it beats seasonal-naive), the benchmark's measured rate of false
        "beats seasonal-naive" advice (§3.4): the receipt's seasonal random-walk condition, where
        seasonal-naive is the best forecast possible, nearest this series' months of history,
        with its exact 95% Clopper-Pearson interval (stats.clopper_pearson)."""
        from northledger import stats as _st
        if g.gate.verdict != "RECOMMEND":
            return None, "quoted only beside a CONFIRMED forecast"
        rec = self.full_receipt()
        if rec is None or not self.rep["engine"]["benchmark"].get("forecast_coverage_80"):
            return None, "no benchmark receipt measured this engine's forecast code"
        cells = [r for r in (rec.get("results") or {}).get("forecast") or []
                 if (r.get("cell") or {}).get("process") == "seasonal_rw" and (r.get("cell") or {}).get("gated")
                 and r.get("n") and r.get("k_recommend") is not None]
        if not cells:
            return None, "the benchmark receipt has no condition where seasonal-naive cannot be beaten"
        months = int(((fr if fr is not None else self.fr).history_months) or 0) or 1
        best = min(cells, key=lambda r: (abs(math.log(float(months) / float(r["cell"]["months"]))), r["cell"]["months"]))
        k, n = int(best["k_recommend"]), int(best["n"])
        lo, hi = _st.clopper_pearson(k, n, 0.95)
        rate, lo, hi = 100.0 * k / n, 100.0 * lo, 100.0 * hi
        cm = int(best["cell"]["months"])
        where = ("%d months of history, like this series" % cm if cm == months else
                 "%d months of history, the simulated condition nearest this series' %d" % (cm, months))
        sentence = ("Measured, not promised: on simulated series where repeating last year's value for the "
                    "same month is the best forecast anyone can make (%s), the engine still said its forecast "
                    "beats that rule in %s of them (%d of %d; 95%% interval %s to %s). That is how often it "
                    "claims skill that is not there, not the chance that this forecast misses."
                    % (where, _pct_text(rate), k, n, _pct_text(lo), _pct_text(hi)))
        return ({"sentence": sentence, "rate": _num(rate), "lower95": _num(lo), "upper95": _num(hi), "k": k, "n": n,
                 "cell": str(best["cell"].get("name")), "at_bar": False, "fact_ids": [],
                 "source": "benchmark receipt (results.forecast, seasonal random walk), decision code %s"
                           % str((rec.get("decision_code_snapshot") or {}).get("id") or "")[:12],
                 "months": cm, "series_months": months},
                None)

    def blocks(self) -> None:
        """primary_metric, tests_run, methods, limitations."""
        rep = self.rep
        pm = self.primary_gated()
        rep["primary_metric"] = ({"finding_id": pm.fact.id, "claim_key": pm.fact.claim_key,
                                  "grade": GRADE[pm.gate.verdict]} if pm is not None else None)
        fams: Dict[str, Dict[str, Any]] = {}
        tested = 0
        for g in self.shown():
            sig = g.gate.signals or {}
            if sig.get("family"):
                fams.setdefault(sig["family"], {"name": sig["family"], "size": sig.get("family_size"),
                                                "fdr_method": sig.get("family_method"),
                                                "level": _num(sig.get("family_line"))})
                tested = max(tested, int(sig.get("claims_tested_in_run") or 0))
        rep["tests_run"] = {"families": [fams[k] for k in sorted(fams)], "claims_tested": tested}
        methods, lims = [], []
        change_ids = [f["id"] for f in rep["findings"] if f["test"] is not None and f["estimand"] == "ratio_of_average_month"]
        if change_ids:
            g0 = self.gated[change_ids[0]]
            methods.append({"id": "change_test", "name": _visitor(g0.fact.test.get("method", "")),
                            "assumptions": [self.pub(_plain(c)) for c in g0.fact.caveats[:1]],
                            "applies_to": change_ids, "desktop_only": False})
            methods.append({"id": "fdr", "name": "false-discovery control within each family of tested claims "
                                                 "(Benjamini-Hochberg), selection-adjusted bounds for confirmed ones "
                                                 "(Benjamini-Yekutieli)",
                            "assumptions": [], "applies_to": change_ids, "desktop_only": False})
            lims.append({"kind": "causal", "text": "The engine compares periods in observational data: a change it "
                                                   "reports is an association with the period, not a finding about its cause.",
                         "finding_ids": change_ids})
        fc_ids = [f["id"] for f in rep["findings"] if f["kind"] == "forecast"]
        if self.fr is not None:
            from northledger import forecast as _fc
            methods.append({"id": "forecast", "name": _fc.PLAIN_NAME.get(self.fr.champion, self.fr.champion),
                            "assumptions": [self.pub(_plain(n)) for n in (self.fr.notes or [])],
                            "applies_to": fc_ids, "desktop_only": False})
            methods.append({"id": "forecast_band", "name": "conformal rank ranges from replayed errors, widened when "
                                                           "those errors move together",
                            "assumptions": [], "applies_to": fc_ids, "desktop_only": False})
            if (self.fr.baseline_test or {}).get("method"):
                methods.append({"id": "baseline_test", "name": "Diebold-Mariano test against seasonal-naive, "
                                                               "nested model selection",
                                "assumptions": [], "applies_to": fc_ids, "desktop_only": False})
            for n in self.fr.notes or []:
                lims.append({"kind": "forecast", "text": self.pub(_plain(n)), "finding_ids": fc_ids})
        methods.append({"id": "cleaning", "name": "stated cleaning rules; every row kept or set aside with a reason",
                        "assumptions": [], "applies_to": [], "desktop_only": False})
        for line in rep["story"]["cannot_answer"]:
            lims.append({"kind": "data", "text": line, "finding_ids": []})
        lims.append({"kind": "statistical", "text": "Not measured in this release: tipping points for the set-aside "
                                                    "rows, which segments drive a change and reversals within them, "
                                                    "and model-based probabilities. Power is measured only at one "
                                                    "size of true change (%d%%) in simulated conditions: each tested "
                                                    "claim quotes the nearest one, not a power computed for itself."
                                                    % round(100 * POWER_SHIFT), "finding_ids": []})
        cert = rep["engine"]["benchmark"].get("certification")
        if cert and cert["failed"]:
            lims.append({"kind": "statistical",
                         "text": "The release check certifies each simulated no-change condition below a %s "
                                 "false-confirm cap; %d of %d conditions pass and %d do not, so in %s the engine "
                                 "can confirm a change that is not there more often than that."
                                 % (_pct_text(cert["cap_pct"]), cert["passed"], cert["cells"], len(cert["failed"]),
                                    "those conditions" if len(cert["failed"]) > 1 else "that condition"),
                         "finding_ids": []})
        lims.append({"kind": "external", "text": "Accuracy, whether the values match reality, is not measured: no "
                                                 "rows were checked against their source.", "finding_ids": []})
        rep["methods"], rep["limitations"] = methods, lims

    # ---------------------------------------------------------------- forecast
    def forecast(self) -> None:
        F, fr = self.rep["forecast"], self.fr
        if fr is None:
            return
        from northledger import forecast as _fc
        sig = {}
        g = self.gated.get("forecast.%s.next" % self.slug)
        if g is not None:
            sig = dict(g.gate.signals or {})
        p0 = (fr.forward or [{}])[0]
        pp = sig.get("band_coverage_p10_p90_h1") or [p0.get("coverage_p10"), p0.get("coverage_p90")]
        F["band"] = {"level": _num(fr.config.get("band")), "method": str(fr.config.get("band_method", "")),
                     "n_errors": p0.get("n_errors"), "max_level": _num(p0.get("max_level")),
                     "conditional_coverage_p10_p90": _nums(pp), "scope": sig.get("band_scope", "per_month"),
                     "dependence": str(fr.config.get("band_dependence", "")),
                     "widened_by_max": _num(sig.get("band_widened_by_max")),
                     "n_effective_h1": _num(p0.get("n_effective"))}
        cv = dict(fr.coverage or {})
        F["coverage"] = {"hits": cv.get("hits"), "n": cv.get("n"), "wilson": _nums([cv.get("wilson_lo"), cv.get("wilson_hi")]),
                         "binomial_p": _num(cv.get("binomial_p")),
                         "christoffersen_ind_p": _num(cv.get("christoffersen_ind_p")),
                         "christoffersen_cc_p": _num(cv.get("christoffersen_cc_p"))}
        bt = dict(fr.baseline_test or {})
        F["baseline_test"] = {"method": bt.get("method"), "stat": _num(bt.get("statistic")),
                              "df": (int(bt["n"]) - 1) if bt.get("n") else None, "n": bt.get("n"),
                              "p": _num(bt.get("p")), "gain": _num(bt.get("gain"))}
        rows = []
        for name, m in (fr.models or {}).items():
            champ = name == fr.champion
            rows.append({"name": name, "label": _fc.PLAIN_NAME.get(name, name), "mase": _num(m.get("mase")),
                         "mape": _num(m.get("mape")), "mape_suppressed": bool(m.get("mape_suppressed")),
                         "msis": _num(m.get("msis")), "skill_vs_sn": _num(m.get("skill_vs_sn")),
                         "mae": _num(m.get("mae")),
                         "coverage": ({"hits": cv.get("hits"), "n": cv.get("n"), "rate": _num(cv.get("rate"))}
                                      if champ else None),
                         "dm": ({"stat": F["baseline_test"]["stat"], "p": F["baseline_test"]["p"],
                                 "n": F["baseline_test"]["n"],
                                 "of": "the whole method, choosing its model at each replayed month, "
                                       "against seasonal-naive, one month ahead"} if champ else None),
                         "champion": champ, "baseline": name == "seasonal_naive",
                         "applicable": bool(m.get("applicable", True))})
        rows.sort(key=lambda r: (r["mase"] is None, r["mase"] if r["mase"] is not None else 0.0, r["name"]))
        F["models"] = rows
        F["break"] = None
        F["interventions"] = []
        F["forecastability"] = None
        F["decision_edge"] = None

    # ---------------------------------------------------------------- charts
    def add(self, cid: str, rule: str, ctype: str, title: str, view: str, why: str, data: Dict[str, Any],
            finding_ids: Iterable[str] = (), source: str = "", visible: bool = True) -> None:
        self.charts.append({"id": cid, "rule": rule, "type": ctype, "title": title, "view": view,
                            "default_visible": bool(visible), "finding_ids": list(finding_ids),
                            "why_shown": why, "source": source, "data": data})

    def skip(self, rule: str, ctype: str, why: str) -> None:
        self.suppressed.append({"rule": rule, "type": ctype, "why": why})

    def charts_all(self) -> None:
        rep = self.rep
        fnd = rep["findings"]
        # the first screen's tiles: at most KPI_MAX business claims (a forecast the engine offers
        # counts as one), strongest evidence first, then the larger change; data hygiene only when
        # the file has no business claim at all. A forecast that is not offered has no tile.
        mon = set(rep["summary"]["monitoring"])
        biz = [f for f in fnd if f["kind"] in ("business", "forecast") and f["effect"]["estimate"] is not None
               and f["id"] not in mon and not f.get("layout_artifact")]
        pool = biz or [f for f in fnd if f["kind"] == "data_quality"]
        # within one strength of evidence, the primary claim's own story first: the claim, its
        # like-for-like restatement and its forecast (fixer round, 24 Sep 2026)
        own = self.primary_group()
        # the primary claim leads, as its decision line does (plan §4.1; fixer round, 24 Sep 2026:
        # retail's total sales, held back by a step, had no tile while three averages did)
        heads = sorted(pool, key=lambda f: (f["id"] != (own or [None])[0], _evidence_rank(f), f["id"] not in own,
                                            own.index(f["id"]) if f["id"] in own else 0, f["role"] != "primary",
                                            -_materiality(f), fnd.index(f)))[:KPI_MAX]
        self.add("kpi", "#1", "kpi_tiles", "Headline findings", "manager",
                 "always: the strongest business claims, at most %d" % KPI_MAX,
                 {"tiles": [{"finding_id": f["id"], "claim": rep["summary"]["labels"].get(f["id"]) or f["claim"],
                             "value": f["effect"]["estimate"] if f["effect"]["scale"] == "difference" else f["value"],
                             "unit": f["effect"]["unit"] if f["effect"]["scale"] == "difference"
                             else self.gated[f["id"]].fact.unit, "grade": f["grade"],
                             # a claim the coverage steps explain: its as-filed interval spans the steps
                             # and says nothing about the business, so its tile prints none
                             "ci": None if _stepped(f) else (f["effect"] or {}).get("ci"),
                             "ci_level": None if _stepped(f) else (f["effect"] or {}).get("level"),
                             "scale": (f["effect"] or {}).get("scale"), "kind": f["kind"],
                             "movement": (f["watch"] or {}).get("movement")} for f in heads]},
                 [f["id"] for f in heads], "findings")
        self.trend_charts()
        self.forecast_charts()
        self.add("findings_table", "#13", "table", "Every finding and its grade", "manager",
                 "always: the compact table in the manager view, the full one in the analyst view",
                 {"rows": [{"finding_id": f["id"], "claim": f["claim"], "grade": f["grade"],
                            "effect": f["effect"]["estimate"], "ci": f["effect"]["ci"],
                            "ci_level": f["effect"]["level"], "scale": f["effect"]["scale"],
                            "unit": f["effect"]["unit"], "note": f["effect"]["note"],
                            "movement": (f["watch"] or {}).get("movement"),
                            "settle": f["needed_to_upgrade"] or None,
                            "p": (f["test"] or {}).get("p"), "q": (f["test"] or {}).get("q"),
                            "family": (f["test"] or {}).get("family")} for f in fnd],
                  "manager_columns": ["claim", "effect", "ci", "grade", "settle"],
                  "analyst_columns": ["claim", "effect", "ci", "grade", "settle", "p", "q", "family"]},
                 [f["id"] for f in fnd], "findings")
        self.cleaning_chart()
        self.season_charts()
        self.dimension_charts()
        self.missing_chart()
        self.corr_chart()
        b = rep["engine"]["benchmark"]
        self.add("benchmark", "#14", "benchmark_strip", "How often the engine confirms a change that is not there",
                 "manager", "always: the matched benchmark condition and the worst one, never a pooled rate",
                 {"available": b["available"], "matched_cell": b["matched_cell"], "worst_cell": b["worst_cell"],
                  "note": b["note"] or None, "file": b["file"], "match": b["match"], "routed": b["routed"],
                  "certification": b["certification"], "target_pct": b["target_pct"]},
                 [b["matched_cell"]["for_finding"]] if b["matched_cell"] else [], "benchmark receipt")
        self.skip("#2b", "driver_waterfall", "which segments drive a change is not computed in this release")
        self.cap_manager()
        by_f: Dict[str, List[str]] = {}
        for c in self.charts:
            for fid in c["finding_ids"]:
                by_f.setdefault(fid, []).append(c["id"])
        for f in fnd:
            f["chart_ids"] = by_f.get(f["id"], [])
        rep["charts"], rep["charts_suppressed"] = self.charts, self.suppressed

    def cap_manager(self) -> None:
        """§5: at most six charts in the manager view by default. The fixed ones (tiles, the
        findings table, the benchmark strip, the forecast and its replay) keep their places; trend
        charts fill what is left, the primary claim's first."""
        fixed = {"kpi", "findings_table", "benchmark"} | {c["id"] for c in self.charts
                                                          if c["id"].startswith(("fan.", "replay."))}
        left = MANAGER_MAX_CHARTS - sum(1 for c in self.charts if c["id"] in fixed and c["view"] == "manager")
        moved = []
        for c in self.charts:
            if c["view"] != "manager" or c["id"] in fixed:
                continue
            if left > 0:
                left -= 1
            else:
                c["view"] = "analyst"
                moved.append(c)
        if moved:
            # the note counts what it names (review of the site, 24 Sep 2026: "at most six charts"
            # beside four drawn ones): the six of §5 include the headline tiles and the findings
            # table, which are not drawn as charts, so the note counts the drawn ones and says which
            drawn = [c for c in self.charts if c["view"] == "manager" and c["id"] not in ("kpi", "findings_table")]
            names = {"fan": "the forecast", "replay": "its replay", "benchmark_strip": "the false-alarm benchmark"}
            words = [names[c["type"]] for c in drawn if c["type"] in names]
            rest = len(drawn) - len(words)
            if rest:
                words.append("%s trend chart%s" % (_count_word(rest), "" if rest == 1 else "s"))
            note = ("; moved to the analyst view: the manager view draws at most %s charts beside its headline "
                    "tiles and findings table, and holds %s already: %s"
                    % (_count_word(len(drawn)), _count_word(len(drawn)),
                       ", ".join(words[:-1]) + (" and " if len(words) > 1 else "") + words[-1]))
            for c in moved:
                c["why_shown"] += note

    def trend_charts(self) -> None:
        import numpy as np
        rep = self.rep
        tested = [f for f in rep["findings"] if f["estimand"] == "ratio_of_average_month"
                  and f["test"] is not None and f["test"]["ran"]]
        if not tested:
            why = ("the business analysis did not run" if self.ana is None else "no change claim was tested")
            self.skip("#2", "trend", why)
            self.skip("#10", "distribution", why)
            return
        # strongest evidence first, then the primary claim, then the larger change: a chart of a claim
        # with NOT ENOUGH DATA never takes a manager place ahead of a WATCH or CONFIRMED one
        order = sorted(tested, key=lambda f: (_evidence_rank(f), f["role"] != "primary", -_materiality(f), f["id"]))
        from northledger._sqlite import connect_ro
        con = connect_ro(self.db_path)
        try:
            for f in order:
                g = self.gated[f["id"]]
                key = g.fact.claim_key
                cs = _claim_series(g.fact, self.gated)
                pay = _payload(self.gated["%s.p" % f["id"]].fact) if "%s.p" % f["id"] in self.gated else {}
                sql = pay.get("series_sql")
                if not sql or self.month is None:
                    continue
                pts = [(str(m), _num(v)) for m, v in con.execute(sql)]
                months = [m for m, _ in pts]
                values = [v for _, v in pts]
                mfl = re.search(r"HAVING COUNT\([^)]*\) >= (\d+)", sql)
                floor = int(mfl.group(1)) if mfl else 0
                if key == "volume":
                    cnt = self.month.value_counts()
                    rows = [int(cnt.get(m, 0)) for m in months]
                elif cs["kind"] == "volume_subset":
                    # the series is itself the rows a month in the subset the engine counted
                    rows = [int(v or 0) for v in values]
                elif cs["kind"] == "total":
                    # the rows each monthly total sums: the engine's own series query, counting
                    # the values it summed instead of adding them
                    csql, nsub = re.subn(r"SUM\(([^()]*)\) AS v", r"COUNT(\1) AS v", sql, count=1)
                    got = {str(m): int(v or 0) for m, v in con.execute(csql)} if nsub else {}
                    rows = [int(got.get(m, 0)) for m in months]
                else:
                    s = _numbers_of(self.frame[key]) if key in self.frame.columns else None
                    got = s.notna().groupby(self.month.values).sum() if s is not None else {}
                    rows = [int(got.get(m, 0)) for m in months]
                la = str(pay.get("latest_start"))
                windows = {"prior": [_shift_month(la, -12), _shift_month(la, -1)], "latest": [la, _shift_month(la, 11)]}

                def wmean(a: str, b: str, series: Optional[List[Optional[float]]] = None) -> Optional[float]:
                    v = [x for m, x in zip(months, values if series is None else series)
                         if a <= m <= b and x is not None]
                    return _num(sum(v) / len(v)) if v else None
                data = {"months": months, "values": values, "rows": rows, "month_floor": floor,
                        "windows": windows,
                        "window_means": {"prior": wmean(*windows["prior"]), "latest": wmean(*windows["latest"])},
                        "effect": {"estimate": f["effect"]["estimate"], "ci": f["effect"]["ci"],
                                   "level": f["effect"]["level"]},
                        "unit": cs["unit"], "scale": self.value_scale(key=key),
                        "steps": self.steps_of(f, g.fact, months), "like_for_like": None}
                fids = [f["id"]]
                lf = (f.get("composition") or {}).get("like_for_like")
                lvals = self.like_for_like_series(g.fact, sql, "volume" if key == "volume" else cs["kind"]) if lf else None
                if lf and lvals is not None and len(lvals) == len(months):
                    # the like-for-like line beside the claim's own: the same months, only the levels
                    # present throughout (the engine's like-for-like comparison, its number quoted)
                    lw = {k: wmean(windows[k][0], windows[k][1], lvals) for k in ("prior", "latest")}
                    data["like_for_like"] = dict(lf, values=lvals, window_means=lw,
                                                 column=f["composition"]["column"],
                                                 levels_kept=f["composition"]["levels_kept"])
                    if lf.get("finding_id") and lf["finding_id"] not in fids:
                        fids.append(lf["finding_id"])
                self.add("trend.%s" % key, "#2", "trend_windows", f["claim"], "manager",
                         "a tested change claim: both window means with the effect interval, and every monthly point"
                         + "".join(w for ok, w in ((data["like_for_like"], "; the like-for-like line"),
                                                   (any(x["kind"] != "step" for x in data["steps"]),
                                                    "; the months where the file's coverage changed"),
                                                   (any(x["kind"] == "step" for x in data["steps"]),
                                                    "; the month the engine's step screen found a step")) if ok),
                         data, fids, "the claim's own monthly series (its test payload's SQL)")
                if cs["kind"] in ("volume", "volume_subset"):
                    continue
                pv = [x for m, x in zip(months, values) if windows["prior"][0] <= m <= windows["prior"][1] and x is not None]
                lv = [x for m, x in zip(months, values) if windows["latest"][0] <= m <= windows["latest"][1] and x is not None]
                allv = pv + lv
                if len(allv) < 2:
                    continue
                k = int(math.ceil(math.log2(len(allv)) + 1))
                lo, hi = min(allv), max(allv)
                edges = [float(x) for x in np.linspace(lo, hi, k + 1)] if hi > lo else [lo - 0.5, hi + 0.5]
                self.add("dist.%s" % key, "#10", "histogram_windows",
                         ("Monthly %s, before and after" % cs["unit"].replace(" a month", "s", 1)
                          if cs["kind"] == "total" else "Monthly averages of %s, before and after" % key), "analyst",
                         "a measure change finding: the monthly values of each window, binned (Sturges)",
                         {"edges": edges,
                          "prior": {"values": pv, "counts": [int(x) for x in np.histogram(pv, bins=edges)[0]]},
                          "latest": {"values": lv, "counts": [int(x) for x in np.histogram(lv, bins=edges)[0]]},
                          "scale": self.value_scale(key=key)},
                         [f["id"]], "the claim's monthly series")
        finally:
            con.close()
        if not any(c["id"].startswith("trend.") for c in self.charts):
            self.skip("#2", "trend", self.no_month if self.month is None else "no tested claim's series could be read")
        if not any(c["id"].startswith("dist.") for c in self.charts):
            self.skip("#10", "distribution", "no tested change claim on a measure")

    def forecast_charts(self) -> None:
        F, fr = self.rep["forecast"], self.fr
        if not F["available"] or fr is None:
            why = F["reason"] or "no forecast was offered"
            for rule, t in (("#4", "fan"), ("#5", "replay"), ("#6", "model_table")):
                self.skip(rule, t, why)
            return
        fid = "forecast.%s.next" % self.slug
        fids = [fid] if fid in {f["id"] for f in self.rep["findings"]} else []
        self.add("fan.%s" % self.slug, "#4", "fan", "Forecast with its 80% range", "manager",
                 "the forecast is graded %s: about 80%% of months land in ranges built this way, on average; "
                 "the range is for each month on its own" % GRADE.get(F["verdict"], F["verdict"]),
                 {"history": list(F["series"]),
                  "forward": [{"month": str(p["month"]), "h": int(p.get("h", i + 1)), "value": _num(p.get("point")),
                               "lo": _num(p.get("lo80")), "hi": _num(p.get("hi80")),
                               "widened_by": _num(p.get("widened_by"))} for i, p in enumerate(fr.forward)],
                  "level": _num(fr.config.get("band")), "scope": "per_month",
                  "scale": self.value_scale(slug=self.slug)}, fids, "the forecast result")
        rep_rows = [b for b in (fr.backtest or []) if int(b.get("h", 0)) == 1]
        self.add("replay.%s" % self.slug, "#5", "replay", "The forecast replayed one month ahead", "manager",
                 "shown whenever the forecast is: actuals against the range per replayed month, misses marked",
                 {"months": [{"month": str(b["month"]), "actual": _num(b.get("actual")), "point": _num(b.get("point")),
                              "lo": _num(b.get("lo80")), "hi": _num(b.get("hi80")), "in_band": bool(b.get("in_band"))}
                             for b in rep_rows],
                  "hits": (fr.coverage or {}).get("hits"), "n": (fr.coverage or {}).get("n"),
                  "scale": self.value_scale(slug=self.slug)}, fids,
                 "the forecast's replay")
        self.add("models.%s" % self.slug, "#6", "table", "Forecast methods compared", "analyst",
                 "with the forecast chart: every candidate's replay error, MASE first",
                 {"rows": F["models"], "columns": ["label", "mase", "mape", "msis", "coverage", "dm", "champion"]},
                 fids, "the forecast result")

    def cleaning_chart(self) -> None:
        if self.month is None:
            self.skip("#12", "before_after", self.no_month)
            return
        from northledger import clean as _clean
        kept = self.month.value_counts()
        undated_kept = int(self.month.isna().sum())
        q: Dict[str, int] = {}
        und_q = 0
        for mo in self.quarantine_months():
            if mo is None:
                und_q += 1
            else:
                q[mo] = q.get(mo, 0) + 1
        months = sorted(set(str(m) for m in kept.index) | set(q))
        dcol = next((c for c in self.th.columns if c.name == self.date), None)
        fmts = list(_clean._strptime_formats(getattr(dcol, "date_formats", None)))
        fmts += [f for f in _clean.DEFAULT_DATE_FORMATS if f not in fmts]
        k = [int(kept.get(m, 0)) for m in months]
        s = [int(q.get(m, 0)) for m in months]
        self.add("cleaning.before_after", "#12", "stacked_bars", "Rows kept and set aside, by month", "analyst",
                 "always computed: every row kept or set aside, by month (the analyst view's data-quality tab)",
                 {"months": months, "landed": [a + b for a, b in zip(k, s)], "kept": k, "set_aside": s,
                  "undated_kept": undated_kept, "undated_set_aside": und_q, "date_formats": fmts},
                 [f["id"] for f in self.rep["findings"] if f["id"].startswith("clean.")], "cleaned and set-aside rows")

    def season_charts(self) -> None:
        if self.month is None or len(self.wmonths) < SEASON_MIN_MONTHS:
            self.skip("#3", "month_year_heatmap", "fewer than %d months in the analysis window" % SEASON_MIN_MONTHS
                      if self.month is not None else self.no_month)
            return
        inwin = self.month.isin(self.wmonths)
        mw = self.month[inwin]
        years = sorted({m[:4] for m in self.wmonths})
        series = (["volume"] if self.ana.roles.grain == "events" else []) + list(self.measures)
        # a measure the engine totals per kind of row (review v2: rent near 2,059 and repairs near
        # 280 in one column) has no one monthly average: the engine refuses it, so no chart draws it
        split_of: Dict[str, Dict[str, Any]] = {}
        for g in self.shown():
            sp = (getattr(g.fact, "test", None) or {}).get("kind_split")
            col = (getattr(g.fact, "test", None) or {}).get("total_of")
            if sp and col:
                split_of[str(col)] = sp
        for key in [k for k in series if k in split_of]:
            self.skip("#3", "month_year_heatmap",
                      "no month-by-year average of %s: it holds two kinds of value (%s %s and the rest), so "
                      "the engine refuses one average across them and totals each kind instead"
                      % (key, split_of[key]["column"], split_of[key]["level"]))
        series = [k for k in series if k not in split_of]
        wset = set(self.wmonths)
        for key in series:
            vals, ns = [], []
            if key == "volume":
                cnt = {str(k): int(v) for k, v in mw.value_counts().items()}
                mean: Dict[str, Optional[float]] = {k: float(v) for k, v in cnt.items()}
            else:
                g = _numbers_of(self.frame.loc[inwin, key]).groupby(mw.values).agg(["mean", "count"])
                cnt = {str(k): int(r["count"]) for k, r in g.iterrows()}
                mean = {str(k): (_num(r["mean"]) if int(r["count"]) else None) for k, r in g.iterrows()}
            for y in years:
                rv, rn = [], []
                for mm in range(1, 13):
                    mo = "%s-%02d" % (y, mm)
                    if mo not in wset:
                        rv.append(None)
                        rn.append(None)
                        continue
                    n = cnt.get(mo, 0)
                    rv.append(mean.get(mo) if n else (0.0 if key == "volume" else None))
                    rn.append(n)
                vals.append(rv)
                ns.append(rn)
            fids = [f["id"] for f in self.rep["findings"] if self.gated[f["id"]].fact.claim_key == key]
            self.add("season.%s" % key, "#3", "heatmap", "%s by month and year" % ("Rows" if key == "volume" else
                                                                                   "Average %s" % key),
                     "analyst", "%d months in the analysis window (24 or more)" % len(self.wmonths),
                     {"years": years, "months": list(range(1, 13)), "values": vals, "n": ns,
                      "value": "rows" if key == "volume" else "mean"}, fids, "cleaned rows in the analysis window")

    def dimension_charts(self) -> None:
        if self.month is None or not self.dims:
            why = self.no_month if self.month is None else "no category column the engine could read"
            self.skip("#7", "ranked_bars", why)
            self.skip("#8", "category_month_heatmap", why)
            return
        from northledger import measure as _ms
        inwin = self.month.isin(self.wmonths)
        mw = self.month[inwin]
        trend = any(f["estimand"] == "ratio_of_average_month" for f in self.rep["findings"])
        drew = False
        heat = 0
        for dim in self.dims:
            if dim not in self.frame.columns:
                continue
            lab = self.frame.loc[inwin, dim]
            vc = lab[lab != ""].value_counts()
            if not (DIM_LEVELS[0] <= len(vc) <= DIM_LEVELS[1]):
                continue
            drew = True
            order = sorted(((str(k), int(v)) for k, v in vc.items()), key=lambda kv: (-kv[1], kv[0]))
            top = order[:RANK_TOP]
            total = int(len(lab))
            shares = {}
            pref = "measure.%s.share." % _ms.slug(dim)
            for fid, g in self.gated.items():
                if fid.startswith(pref):
                    m = re.search(r"is '(.*)' \(rank", g.fact.claim)
                    if m:
                        shares[m.group(1)] = (fid, float(g.fact.value))
            bars = []
            for k, v in top:
                share = 100.0 * v / total
                ef = shares.get(k)
                bars.append({"label": self.pub(k), "rows": v, "share_pct": _num(share),
                             "fact_id": ef[0] if ef and abs(ef[1] - share) <= 1e-9 * max(1.0, share) else None})
            self.add("ranked.%s" % dim, "#7", "ranked_bars", "Rows by %s" % dim, "analyst",
                     "%s has %d levels (2-50): the top %d plus other" % (dim, len(vc), RANK_TOP),
                     {"bars": bars, "other": int(sum(v for _, v in order[RANK_TOP:])),
                      "missing": int((lab == "").sum()), "total": total},
                     [b["fact_id"] for b in bars if b["fact_id"]], "cleaned rows in the analysis window")
            if not trend:
                continue
            if heat >= CATMONTH_MAX:
                self.skip("#8", "category_month_heatmap", "%s: only the first %d category columns the engine "
                          "reads get a category-by-month heatmap" % (dim, CATMONTH_MAX))
                continue
            heat += 1
            keys = [k for k, _ in top]
            cats = [self.pub(k) for k in keys] + ["other"]
            group = lab.where(lab.isin(keys), "\0other").where(lab != "", "\0missing")
            got = _pair_counts(group, mw)
            counts = [[got.get((k, mo), 0) for mo in self.wmonths] for k in keys + ["\0other"]]
            if bool((lab == "").any()):
                cats.append("(missing)")
                counts.append([got.get(("\0missing", mo), 0) for mo in self.wmonths])
            # the chart registry runs after this (nl_viz.build); when it builds a contribution waterfall, it rewords
            # this reason to point at it (nl_viz.mend_catmonth_why, 30 Sep 2026: the line said drivers were "not
            # computed in this release" beside the waterfall that computes them)
            self.add("catmonth.%s" % dim, "#8", "heatmap", "Rows by %s and month" % dim, "analyst",
                     CATMONTH_WHY % CATMONTH_NO_WATERFALL,
                     {"months": list(self.wmonths), "categories": cats, "counts": counts},
                     [f["id"] for f in self.rep["findings"] if f["estimand"] == "ratio_of_average_month"],
                     "cleaned rows in the analysis window")
        if not drew:
            self.skip("#7", "ranked_bars", "no category column with 2-50 levels")
        if not drew or not trend:
            self.skip("#8", "category_month_heatmap", "no tested change claim" if drew else
                      "no category column with 2-50 levels")

    def missing_chart(self) -> None:
        m = self.rep["health"]["missingness"]
        if not m["by_month"]:
            self.skip("#11", "missingness_heatmap", self.no_month)
            return
        cols = m["matrix_columns"]
        rows = [b["rows"] for b in m["by_month"]]
        total = sum(rows)
        nulls = [[b["nulls"][c] for b in m["by_month"]] for c in cols]
        worst = max((sum(r) / float(total) for r in nulls), default=0.0) if total else 0.0
        if worst <= MISSING_SHARE:
            self.skip("#11", "missingness_heatmap", "no column is over 1% empty in the analysis window")
            return
        self.add("missingness", "#11", "heatmap", "Empty cells by column and month", "analyst",
                 "a column is over 1% empty in the analysis window",
                 {"columns": cols, "months": [b["month"] for b in m["by_month"]], "rows": rows, "nulls": nulls},
                 [], "cleaned rows in the analysis window")

    def corr_chart(self) -> None:
        import numpy as np
        if self.month is None or len(self.measures) < CORR_MIN_MEASURES:
            self.skip("#9", "correlation_heatmap", "fewer than %d measures" % CORR_MIN_MEASURES)
            return
        inwin = self.month.isin(self.wmonths)
        num = {m: _numbers_of(self.frame.loc[inwin, m]) for m in self.measures}
        r, n = [], []
        for a in self.measures:
            rr, nn = [], []
            for b in self.measures:
                ok = num[a].notna() & num[b].notna()
                x, y = num[a][ok].to_numpy(float), num[b][ok].to_numpy(float)
                nn.append(int(ok.sum()))
                rr.append(_num(np.corrcoef(x, y)[0, 1]) if len(x) >= 3 and x.std() > 0 and y.std() > 0 else None)
            r.append(rr)
            n.append(nn)
        self.add("corr", "#9", "heatmap", "How the measures move together (rows)", "analyst",
                 "3 or more measures; off by default (descriptive only: it supports no decision, so it shows when the reader asks)",
                 {"measures": list(self.measures), "r": r, "n": n, "method": "Pearson, pairwise complete rows"},
                 [], "cleaned rows in the analysis window", visible=False)

    # ---------------------------------------------------------------- the manager's words
    _EVENT_NOUNS = ("invoice", "order", "transaction", "ticket", "booking", "receipt", "sale", "payment",
                    "visit", "appointment", "shipment", "call", "claim", "admission", "reservation", "subscription")

    def event_noun(self) -> Optional[str]:
        """When each row is one business event, named by an id column ('invoice_id', 'Order No'): the
        event's plural ('Invoices'); else None (a ledger's rows are lines, not events)."""
        if self.ana is None:
            return None
        cols = list((getattr(self.ana.roles, "excluded", None) or {}).keys()) + list(self.ana.roles.dimensions or [])
        for c in cols:
            toks = re.findall(r"[a-z]+", str(c).lower())
            if len(toks) >= 2 and toks[-1] in ("id", "no", "num", "number", "nbr", "ref") and toks[-2] in self._EVENT_NOUNS:
                return _cap(toks[-2] + "s")
        return None

    def money_total(self) -> bool:
        """The file has a money total the engine tested (its rows are ledger lines of amounts)."""
        return any(str(g.fact.claim_key or "").startswith("total:") and self.value_scale(key=str(g.fact.claim_key))
                   for g in self.gated.values() if (getattr(g.fact, "test", None) or {}).get("ran") is not None)

    def name_of(self, fact: Any) -> str:
        """A claim's measure in the reader's words: 'Rent', 'Amount other than rent', 'Net sales',
        'Amount in USD', 'Ledger lines' (a count of rows beside a money total), 'Average fee'."""
        t = getattr(fact, "test", None) or {}
        key = str(getattr(fact, "claim_key", "") or "")
        if key.startswith("total:"):
            col = str(t.get("total_of") or key.split(":")[1])
            if col in self.flagged:
                return "The total"
            cur, ks = t.get("currency") or {}, t.get("kind_split") or {}
            if cur.get("code"):
                return "%s in %s" % (_human(col), self.pub(str(cur["code"])))
            if ks.get("level") is not None and ks.get("column") not in self.flagged:
                lv = self.pub(str(ks["level"])).lower()
                return _cap(lv) if ks.get("group") == "level" else "%s other than %s" % (_human(col), lv)
            return _human(col)
        if key == "volume" or key.startswith("volume:"):
            return self.event_noun() or ("Ledger lines" if self.money_total() else "Rows")
        if key in self.names:
            return "Average %s" % self.names[key]
        return "Average %s" % _human(key).lower() if key else ""

    def primary_group(self) -> List[str]:
        """The primary claim, its like-for-like restatement and its series' forecast, in that order."""
        pm = self.primary_gated()
        if pm is None:
            return []
        out = [pm.fact.id]
        out += [f["id"] for f in self.rep["findings"] if f.get("parent_id") == pm.fact.id]
        slug = (getattr(pm.fact, "test", None) or {}).get("series_slug")
        if slug and "forecast.%s.next" % slug in self.gated:
            out.append("forecast.%s.next" % slug)
        return out

    def plain(self) -> None:
        """summary.labels (a plain label per business and forecast claim) and summary.monitoring
        (the claims about the ledger's own line count when the file has a money total: pipeline
        monitoring, kept off the first screen, the tiles and the bottom line)."""
        from northledger.vocab import plural_of
        sm = self.rep["summary"]
        # a count of ledger lines beside a money total is monitoring; a count of business events is not
        money = self.money_total() and not self.event_noun()
        rows_word = self.event_noun() or ("Ledger lines" if money else "Rows")
        labels: Dict[str, str] = {}
        monitoring: List[str] = []
        by_slug: Dict[str, Any] = {}
        for g in self.gated.values():
            t = getattr(g.fact, "test", None) or {}
            if t.get("series_slug") and not t.get("like_for_like_of"):
                by_slug.setdefault(str(t["series_slug"]), g.fact)
        for f in self.rep["findings"]:
            if f["kind"] not in ("business", "forecast"):
                continue
            fact = self.gated[f["id"]].fact
            t = getattr(fact, "test", None) or {}
            key = str(fact.claim_key or "")
            if f["kind"] == "forecast" and f["id"].endswith(".next"):
                slug = f["id"][len("forecast."):-len(".next")]
                month = fact.claim.rsplit(" for ", 1)[-1]
                # a forecast of a series no claim names (a structured table's one-row-a-month slice) is named from its
                # own slug, never left without a label ("The the forecast forecast for ...")
                what = rows_word if slug == "monthly_rows" else \
                    (self.name_of(by_slug[slug]) if slug in by_slug else _human(slug))
                if what:
                    labels[f["id"]] = "%s, forecast for %s" % (what, _mon(month))
                if money and slug == "monthly_rows":
                    monitoring.append(f["id"])
                continue
            if not t:
                continue
            name = self.name_of(fact)
            if not name:
                continue
            if key == "volume" or key.startswith("volume:"):
                labels[f["id"]] = "%s a month%s" % (name, ", like for like" if key.endswith("like_for_like") else "")
                if money:
                    monitoring.append(f["id"])
            elif key.startswith("total:"):
                sub = t.get("subset") or {}
                labels[f["id"]] = ("%s, like for like (the %d %s held throughout)"
                                   % (name, len(sub.get("levels") or []),
                                      plural_of(sub.get("column") or "").lower() if sub.get("column") not in self.flagged
                                      else "groups")
                                   if t.get("like_for_like_of") else "%s, monthly total" % name)
            else:
                labels[f["id"]] = "%s, the average month" % name
        sm["labels"], sm["monitoring"] = labels, monitoring

    def summary(self) -> None:
        """summary.lines: the manager's bottom line in at most three sentences, every number the
        engine's own fact printed the engine's way (narrate.story_number): what moved and why (from
        the composition record when the file's coverage changed), what to act on, and the planning
        number (fixer round, 24 Sep 2026: the bottom line was 7-11 'graded WATCH' clauses)."""
        from northledger.narrate import story_number as SN
        from northledger.vocab import plural_of
        sm, F = self.rep["summary"], {f["id"]: f for f in self.rep["findings"]}
        lines: List[Dict[str, Any]] = []
        pm = self.primary_gated()
        if pm is not None and pm.fact.id in F and pm.fact.unit == "pct":
            f, fact = F[pm.fact.id], pm.fact
            name = self.name_of(fact)
            v = float(fact.value)
            ids = [fact.id]
            up = "up" if v >= 0 else "down"
            mv = (f.get("watch") or {}).get("movement") or {}
            text = "%s is %s %s on the year before" % (name, up, SN(abs(v), "pct"))
            comp = f.get("composition") or {}
            lf = comp.get("like_for_like") or {}
            if mv.get("kind") == "stepped":
                came, went = [], []
                for st in sorted(mv["steps"], key=lambda x: -abs(float(x.get("size") or 0.0)))[:3]:
                    for lv, verb in st.get("levels") or []:
                        (came if verb == "first appears" else went).append((lv, _mon(st["month"])))
                bits = []
                if came:
                    bits.append(_listed(["'%s' first appears in the file in %s" % came[0]] +
                                        ["'%s' in %s" % x for x in came[1:]]))
                if went:
                    bits.append(_listed(["'%s' has no rows from %s" % went[0]] + ["'%s' from %s" % x for x in went[1:]]))
                text += ", but that is mostly a change in what the file covers" + \
                    ((": %s" % "; ".join(bits)) if bits else "")
            elif comp.get("words"):
                text += ", and part of that is a change in what the file covers"
            if lf.get("estimate") is not None and lf.get("fact_id") in self.gated:
                lfact = self.gated[lf["fact_id"]].fact
                text += (". The %d %s held throughout %s %s like for like (%s)"
                         % (int(comp.get("levels_kept") or 0), plural_of(comp.get("column") or "groups").lower(),
                            "grew" if float(lfact.value) >= 0 else "fell", SN(abs(float(lfact.value)), "pct"),
                            _grade_plain(lf.get("grade"))))
                ids.append(lf["fact_id"])
            elif f["grade"]:
                text += " (%s)" % _grade_plain(f["grade"])
            base = fact.id[:-len(".change")] if fact.id.endswith(".change") else ""
            fp = self.gated.get(base + ".from_peak") if base else None
            if fp is not None and fp.fact.value is not None and fp.fact.verified is not False:
                pmo = fp.fact.claim.split("(the 3 months to ", 1)[-1].split(")", 1)[0]
                # the same label the Analyst view gives it (review, 25 Sep 2026): the peak is chosen
                # after looking at the series, so the fall since it is never tested or graded
                text += ("; it peaked in the 3 months to %s and is %s %s since, a pattern found by looking, "
                         "not a tested change"
                         % (_mon(pmo), "down" if float(fp.fact.value) < 0 else "up", SN(abs(float(fp.fact.value)), "pct")))
                ids.append(fp.fact.id)
            # what was screened before the money was added up (the engine's currency and status screens)
            screens = []
            cur = (getattr(fact, "test", None) or {}).get("currency") or {}
            if cur.get("levels") and "measure.currency.count" in self.gated and cur.get("column") not in self.flagged:
                screens.append("amounts in different currencies (%s) are never added together"
                               % " and ".join(self.pub(str(x)) for x in cur["levels"]))
                ids.append("measure.currency.count")
            stx = self.gated.get("measure.status_excluded.rows")
            if stx is not None and stx.fact.value and (getattr(fact, "test", None) or {}).get("status_excluded"):
                who = stx.fact.claim.split("Rows whose ", 1)[-1].split(", over the 24", 1)[0]
                screens.append("%s rows whose %s are left out as not money received"
                               % (SN(float(stx.fact.value), "count"), self.pub(who)))
                ids.append(stx.fact.id)
            if screens:
                text += ". " + _cap("; ".join(screens))
            lines.append({"kind": "moved", "text": self.pub(text + "."), "finding_ids": ids})
        # what to act on: the confirmed business claims, or none
        conf = [f for f in self.rep["findings"] if f["kind"] == "business" and f["grade"] == "CONFIRMED"
                and f["id"] not in sm["monitoring"] and f["effect"]["estimate"] is not None]
        if conf:
            parts = []
            for f in conf[:3]:
                fact = self.gated[f["id"]].fact
                parts.append("%s (%s)" % (sm["labels"].get(f["id"]) or _plain(f["claim"]),
                                          SN(float(fact.value), fact.unit, signed=fact.unit == "pct")))
            lines.append({"kind": "act", "text": self.pub("Confirmed, so worth acting on: %s." % "; ".join(parts)),
                          "finding_ids": [f["id"] for f in conf[:3]]})
        elif any(f["kind"] == "business" and f["grade"] == "WATCH" for f in self.rep["findings"]):
            lines.append({"kind": "act", "text": "Nothing here is confirmed yet, so watch these changes rather than act on them.",
                          "finding_ids": []})
        # the planning number: the primary series' forecast
        slug = None
        if pm is not None:
            slug = (getattr(pm.fact, "test", None) or {}).get("series_slug")
        fid = "forecast.%s.next" % slug if slug else ("forecast.%s.next" % self.slug if self.slug else None)
        fcf = F.get(fid) if fid else None
        if fcf is not None and fcf.get("layout_artifact") and self.slug and fid != "forecast.%s.next" % self.slug:
            # P0-13: the planning number is the series the forecast block shows, never a row count the layout fixes
            fid = "forecast.%s.next" % self.slug
            fcf = F.get(fid)
        audit = self.rep["forecast"].get("audit") or {}
        shown = fid == "forecast.%s.next" % self.slug
        failed = audit.get("status") == "fails" and shown
        # P0-13: "plan on it" only for a forecast whose back-test passed (trusted); the engine's grade stands
        untested = shown and not audit.get("trusted")
        if fcf is not None and fid not in sm["monitoring"] and not fcf.get("layout_artifact"):
            fact = self.gated[fid].fact
            month = _mon(fact.claim.rsplit(" for ", 1)[-1])
            lo, hi = self.gated.get(fid + ".lo80"), self.gated.get(fid + ".hi80")
            what = sm["labels"].get(fid, "").split(", forecast for", 1)[0] or "The forecast"
            low = what[:1].lower() + what[1:]
            if fcf["effect"]["estimate"] is None or fcf["grade"] == "NOT_ENOUGH_DATA":
                lines.append({"kind": "plan", "text": self.pub("No planning number is offered for %s: the engine "
                                                               "could not check its forecast well enough."
                                                               % low if what != "The forecast" else
                                                               "No planning number is offered: the engine could "
                                                               "not check its forecast well enough."),
                              "finding_ids": [fid]})
            else:
                pt = SN(float(fact.value), fact.unit)
                rng = (" (80%% range %s to %s)" % (SN(float(lo.fact.value), lo.fact.unit), SN(float(hi.fact.value), hi.fact.unit))
                       if lo is not None and hi is not None and fcf["effect"]["ci"] else "")
                if failed:
                    # P0-13: the engine's grade stands, labelled; the back-test of the shown range failed
                    text = ("The %s forecast for %s is about %s%s; the engine graded it %s, but that grade is not "
                            "trusted: the back-test of its range failed, so it is no plan." % (
                                low, month, pt, rng, fcf["grade"].replace("_", " ")))
                elif fcf["grade"] == "CONFIRMED" and untested:
                    text = ("The %s forecast for %s is about %s%s; the engine graded it CONFIRMED, but %s, so use it "
                            "as a guide rather than a plan." % (
                                low, month, pt, rng, "its back-test neither passed nor failed the range"
                                if audit.get("horizons") else "it has too few months to be back-tested"))
                elif fcf["grade"] == "CONFIRMED":
                    text = "Plan on about %s for %s in %s%s." % (pt, low, month, rng)
                else:
                    text = ("The %s forecast for %s is about %s%s; it is not yet shown to beat a simple rule, "
                            "so use it as a guide rather than a plan." % (low, month, pt, rng))
                lines.append({"kind": "plan", "text": self.pub(text),
                              "finding_ids": [fid] + [x.fact.id for x in (lo, hi) if x is not None]})
        sm["lines"] = lines

    def build(self) -> None:
        self.engine()
        self.reproducibility()
        self.cleaning()
        self.health()
        self.forecast()
        self.findings()
        self.blocks()
        self.plain()
        self.charts_all()
        self.summary()


def _claim_level(fact: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(the level a benchmark cell is matched on, rows a month, effective rows a month) for a change
    claim, as gate.attach_measured_rates reads them: a count on its rows a month, a monthly total on
    its effective rows a month (rows / (1 + CV^2) of the row amounts), a measure on none."""
    from northledger import gate as _gate
    t = getattr(fact, "test", None) or {}
    kind = _gate.claim_type(fact)
    rv = t.get("rows_a_month", t.get("mean_month"))
    if kind not in ("volume", "total") or rv is None:
        return None, None, None
    rows = float(rv)
    if kind == "total":
        cv = float(t.get("amount_cv") or 0.0)
        eff = rows / (1.0 + cv * cv)
        return eff, rows, eff
    return rows, rows, None


_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _mon(ym: Any) -> str:
    """'2025-03' as 'Mar 2025' (anything else unchanged)."""
    m = re.match(r"^(\d{4})-(\d{2})$", str(ym or "").strip())
    return "%s %s" % (_MON[int(m.group(2)) - 1], m.group(1)) if m and 1 <= int(m.group(2)) <= 12 else str(ym or "")


def _human(col: Any) -> str:
    """A column name as words: 'net_sales' -> 'Net sales'."""
    return _cap(" ".join(str(col or "").replace("_", " ").split()).lower())


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _join(items: List[str]) -> str:
    q = ["'%s'" % x for x in items]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]


def _listed(parts: List[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _stepped(f: Dict[str, Any]) -> bool:
    return (((f.get("watch") or {}).get("movement") or {}).get("kind")) == "stepped"


# a forecast's grade in the page's own words (src/js/52-nl2-report.js FC_WORDS): a point is not "confirmed"
_FC_GRADE_WORDS = {"CONFIRMED": "usable for planning", "WATCH": "not yet shown usable",
                   "NOT_ENOUGH_DATA": "not enough data"}


def _grade_plain(g: Any) -> str:
    return {"CONFIRMED": "confirmed", "WATCH": "not yet conclusive",
            "NOT_ENOUGH_DATA": "too little data to judge"}.get(str(g or ""), "not graded")


def _cell_out(c: Dict[str, Any]) -> Dict[str, Any]:
    from northledger import gate as _gate
    k, n = int(c["k"]), int(c["n"])
    cp = list(c.get("cp95") or [None, None])
    shift = float(c.get("shift") or 0.0)
    level = c.get("level")
    # percent, as the engine prints a quoted rate (gate._cell_value): rate, its 95% Clopper-Pearson
    # interval and the condition's month-to-month noise. at_bar: the true change was exactly the
    # bar, not zero. routed: a count condition under the engine's rows-a-month line, where every
    # verdict is WATCH by rule, so its rate is zero by construction, not by measurement.
    return {"name": c.get("name"), "n": c.get("months"), "phi": _num(c.get("phi_hat_mean")),
            "cv_pct": _num(100.0 * float(c["cv"])) if c.get("cv") is not None else None,
            "level": level, "k": k, "N": n, "rate": _num(100.0 * k / n) if n else None,
            "lower95": _num(100.0 * cp[0]) if cp[0] is not None else None,
            "upper95": _num(100.0 * cp[1]) if cp[1] is not None else None,
            "shift_pct": _num(100.0 * shift), "at_bar": bool(shift > 0.0 and str(c.get("name") or "").startswith("null.")),
            "routed": bool(c.get("claim") == "volume" and level is not None
                           and float(level) < float(_gate.ROUTE_MIN_ROWS_A_MONTH)),
            "seasonal": bool(c.get("seasonal")), "white_sd": _num(c.get("white_sd")),
            # a monthly-total condition's effective rows a month (rows / (1 + CV^2) of its amounts)
            "effective_level": (_num(float(level) / (1.0 + math.expm1(float(c.get("amount_sd") or 0.0) ** 2)))
                                if c.get("claim") == "total" and level is not None else None)}


def _match_kind(file: Dict[str, Any], cell: Dict[str, Any]) -> str:
    """"close" when the file's own diagnostics sit within MATCH_CLOSE of the matched condition's,
    else "nearest" (the page then says "the nearest simulated condition", not "like this file's")."""
    try:
        close = (int(file["months"]) == int(cell["n"])
                 and abs(float(file["phi"]) - float(cell["phi"])) <= MATCH_CLOSE["phi"]
                 and abs(float(file["cv_pct"]) - float(cell["cv_pct"])) <= MATCH_CLOSE["cv_pct"])
        lv = file.get("effective_rows_a_month") if file.get("effective_rows_a_month") is not None \
            else file.get("rows_a_month")
        if close and lv is not None:
            a, b = min(float(lv), 1000.0), min(float(cell.get("effective_level") or cell["level"] or 1000), 1000.0)
            close = max(a, b) / max(min(a, b), 1.0) <= MATCH_CLOSE["rows_ratio"]
    except (TypeError, ValueError, KeyError):
        close = False
    return "close" if close else "nearest"


def _certification(cells: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The release's certification of the receipt's gated no-change conditions, by the benchmark's
    own judge (benchmark.judge_nulls_certify: H0 "rate >= cap" rejected per condition, Holm across
    them): how many pass, and which fail with their k of n."""
    if not cells:
        return None
    try:
        from northledger import benchmark as _bm
    except ImportError:             # a pack cut before the benchmark module was packed
        return None
    res = _bm.judge_nulls_certify([{"k_confirm": int(c["k"]), "n": int(c["n"])} for c in cells])
    failed = [_cell_out(c) for c, r in zip(cells, res) if not r["pass"]]
    return {"cells": len(cells), "passed": sum(1 for r in res if r["pass"]), "failed": failed,
            "cap_pct": _num(100.0 * float(_bm.THRESHOLDS["false_confirm_cap"])),
            "family_level": _num(_bm.THRESHOLDS["family_alpha"]), "form": res[0]["form"]}


def _engine_full_snapshot() -> str:
    stamp = os.path.join(HERE, "nl_pack.json")
    if os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as fh:
            return str(json.load(fh).get("engine_snapshot_full") or "")
    from northledger.loop import engine_snapshot
    return engine_snapshot()["id"]


def _decision_code() -> str:
    stamp = os.path.join(HERE, "nl_pack.json")
    if os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as fh:
            return str(json.load(fh).get("decision_code_snapshot") or "")
    from northledger.forecast import decision_snapshot
    return decision_snapshot()


def _build_v2(rep: Dict[str, Any], audit: Any, ana: Any, th: Any, cr: Any, db_path: str,
              flagged: List[Dict[str, str]], withheld: List[str], pub: Any, as_of: str, objective: str,
              reasons: Dict[str, int], rules: List[Any], plan_measure: Optional[Tuple[str, str]] = None,
              names: Optional[Dict[str, str]] = None) -> None:
    """The v2 blocks. A failure here leaves the v1 report whole and says so under limitations
    (with NL_BROWSER_STRICT set, as the tests set it, it stops the run instead)."""
    try:
        _V2(rep, audit, ana, th, cr, db_path, flagged, withheld, pub, as_of, objective, reasons, rules,
            plan_measure, names).build()
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        keep = {k: rep[k] for k in ("ok", "error", "input", "timings", "privacy", "roles", "story", "downloads")}
        v1 = {"engine": ("snapshot", "version"), "health": ("score", "issues"),
              "cleaning": ("rows_in", "rows_clean", "rows_quarantined", "fixes", "quarantine_reasons"),
              "forecast": SUBKEYS["forecast"]}
        blank = blank_report(rep["input"]["name"])
        for k, sub in v1.items():
            blank[k].update({x: rep[k][x] for x in sub})
        blank["health"]["score"] = rep["health"].get("score_mean", rep["health"]["score"]) \
            if rep["health"].get("score_mean") is not None else rep["health"]["score"]
        blank["findings"] = [{x: f[x] for x in ITEM_KEYS["findings"]} for f in rep["findings"]]
        blank.update(keep)
        blank["limitations"] = [{"kind": "external", "text": "The confidence details (grades, tests, chart data) "
                                 "could not be built for this file (%s); the findings above are the engine's own."
                                 % type(exc).__name__, "finding_ids": []}]
        rep.clear()
        rep.update(blank)


# --------------------------------------------------------------------------- run
# ----------------------------------------------------------------------------- long statistical tables
# Official statistics come as one long table (Statistics Canada, Eurostat, OECD, ECB, ...): a date,
# one VALUE column, one or more columns naming the series, and the agency's metadata columns (units,
# vector ids, coordinates, status flags, decimals). Read as it stands, every series lands in one
# column: 28 exchange rates in different units averaged together (a visitor's StatCan table 33-10-0036,
# 25 Sep 2026: "+10,847%", the codes COORDINATE and DECIMALS read as business measures, the currency
# names withheld as free text). Such a table is turned into one column per series before the engine
# reads it, so each series is analysed on its own with the engine's own methods; the metadata columns
# are set aside, and exact zeros that stand for closed days (weekend placeholders in a series that is
# otherwise always positive) become empty. The report says so, with the counts.
_PANEL_META = frozenset((
    "dguid", "uom", "uomid", "scalarfactor", "scalarid", "vector", "coordinate", "status", "symbol",
    "terminated", "decimals", "obsstatus", "obsflag", "obsconf", "unitmult", "unitmultiplier",
    "confstatus", "unitmeasure", "unit", "units", "flag", "flags", "footnote", "footnotes",
    "timeformat", "freq", "frequency", "lastupdate", "dataflow", "structure", "structureid", "action",
    # wave 5e (P9): the same columns of a French, Spanish or German table (equal to nl_structure._META; a test keeps the two equal)
    "vecteur", "coordonnee", "statut", "symbole", "termine", "decimales", "unitedemesure", "iddelunitedemesure",
    "facteurscalaire", "iddufacteurscalaire", "indicateur", "unidaddemedida", "factorescalar", "estado", "decimales",
    "einheit", "masseinheit", "maeinheit", "faktor", "skalierung", "dezimalstellen", "statusflag"))
_PANEL_DATE = ("refdate", "timeperiod", "date", "period", "time", "referenceperiod", "periodedereference", "periodo", "zeitraum", "fecha",
               "datum")
_PANEL_VALUE = ("value", "obsvalue", "valeur", "valor", "wert")
PANEL_MAX_SERIES = 60


def _pnorm(c: Any) -> str:
    """A header in lower-case letters and digits only, accents folded (the same function as nl_structure._norm: GEO and GÉO are one)."""
    t = unicodedata.normalize("NFKD", str(c))
    return re.sub(r"[^a-z0-9]", "", "".join(ch for ch in t if not unicodedata.combining(ch)).lower())


def _kept_by_visitor(decisions: Any) -> Set[str]:
    """The columns the visitor chose to keep (by the file's header or the landed name), for the passes that run before landing."""
    return {str(k) for k, v in dict(decisions or {}).items() if not str(k).startswith("__") and str(v or "").strip().lower() == "keep"}


def _reshape_long_panel(data: bytes, planned: bool = False, date_col: Optional[str] = None,
                        value_col: Optional[str] = None, keep: Optional[Set[str]] = None
                        ) -> Tuple[bytes, Optional[Dict[str, Any]]]:
    """A long statistical table as one column per series, or (data, None) when it is not one. An AI
    plan may name the date and value columns (a 'Year' of months, a 'Mean' of anomalies). A column that looks personal never names a
    series (wave 5d) unless the visitor kept it (`keep`: headers or landed names): it is listed in `personal_set_aside`."""
    import pandas as pd
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception:  # noqa: BLE001 - not our layout; the engine reads the file as it stands
        return data, None
    norm = {c: _pnorm(c) for c in df.columns}
    date = next((c for k in _PANEL_DATE for c in df.columns if norm[c] == k), None)
    value = next((c for k in _PANEL_VALUE for c in df.columns if norm[c] == k), None)
    if planned and date_col in df.columns:
        date = date_col
    if planned and value_col in df.columns and value_col != date:
        value = value_col
    meta = [c for c in df.columns if norm[c] in _PANEL_META and c not in (date, value)]
    # the rule needs three metadata columns to call a table long; an AI plan that says so is trusted
    if not date or not value or (len(meta) < 3 and not planned) or len(df) < 50:
        return data, None
    dims = [c for c in df.columns if c not in meta and c not in (date, value)]
    keep_set = set(keep or ())
    found = _raw_personal_columns(df, dims)
    personal = {c: k for c, k in found.items() if c not in keep_set and _engine_slug(c) not in keep_set}
    personal_kept = {c: k for c, k in found.items() if c not in personal}
    dims = [c for c in dims if c not in personal]
    varying = [c for c in dims if df[c].nunique() > 1]
    constant = [c for c in dims if c not in varying]
    if varying:
        key = df[varying[0]].str.strip()
        for c in varying[1:]:
            key = key + " | " + df[c].str.strip()
    else:
        # one series: named after its value column, never after the value of a column that holds one value throughout
        # (the live baseline of 30 Sep 2026: the U.S. dollar rate was "canada", from GEO, so the plan's primary VALUE
        # matched no claim and the report led with the row count)
        key = pd.Series([str(value)] * len(df), index=df.index)
    n_series = int(key.nunique())
    if n_series > PANEL_MAX_SERIES or pd.Series(list(zip(df[date], key))).duplicated().mean() > 0.01:
        return data, None
    dt = pd.to_datetime(df[date], errors="coerce")
    v = pd.to_numeric(df[value].str.replace(",", "", regex=False), errors="coerce")
    if dt.notna().mean() < 0.95 or v.notna().sum() < 50:
        return data, None
    # zeros that stand for closed days: a series that is otherwise always positive, whose zeros fall
    # on Saturdays and Sundays (at least 90% of them)
    zeroed = 0
    for lv in key.unique():
        m = key == lv
        z = m & (v == 0)
        nz = v[m & v.notna() & (v != 0)]
        if z.sum() and len(nz) and (nz > 0).all() and dt[z].dt.dayofweek.isin([5, 6]).mean() >= 0.9:
            v = v.mask(z)
            zeroed += int(z.sum())
    long = pd.DataFrame({"_d": dt, "_key": key, "_v": v}).dropna(subset=["_v"])
    # compare series where they exist: the active ones (a value in the file's last six steps, at least 90
    # days: a monthly series published six months late is still current) that run at least three years
    # set the start; discontinued, too recent or too sparse series are set aside and named
    span = long.groupby("_key")["_d"].agg(["min", "max", "count"])
    last = long["_d"].max()
    steps = pd.Series(sorted(long["_d"].unique())).diff().dropna()
    step = steps.median() if len(steps) else pd.Timedelta(days=1)
    active = span[span["max"] >= last - max(pd.Timedelta(days=90), 6 * step)]
    lasting = active[active["max"] - active["min"] >= pd.Timedelta(days=3 * 365)]
    if lasting.empty:
        return data, None
    start = lasting["min"].max()
    inwin = long[long["_d"] >= start]
    n_dates = inwin["_d"].nunique()
    cover = inwin.groupby("_key")["_d"].nunique() / float(max(n_dates, 1))
    kept = [k for k in lasting.index if cover.get(k, 0) >= 0.5]
    if not kept:
        return data, None
    dropped = {"discontinued": sorted(k for k in span.index if k not in active.index),
               "too recent": sorted(k for k in active.index if k not in lasting.index),
               "too sparse": sorted(k for k in lasting.index if k not in kept)}
    inwin = inwin[inwin["_key"].isin(kept)]
    wide = inwin.groupby([inwin["_d"].dt.strftime("%Y-%m-%d").rename("_date"), "_key"], sort=True)["_v"].mean().unstack("_key")
    wide = wide.dropna(how="all")
    first_seen = {k: i for i, k in enumerate(pd.unique(key))}
    counts = inwin.groupby("_key")["_v"].count()
    order = sorted(kept, key=lambda k: (-int(counts.get(k, 0)), first_seen.get(k, 0)))
    wide = wide[[c for c in order if c in wide.columns]].reset_index().rename(columns={"_date": str(date)})
    units = {}
    for c in meta:
        if norm[c] in ("uom", "unit", "units", "unitmeasure"):
            units = {str(k): str(u) for k, u in df.groupby(key)[c].agg(lambda x: x.mode().iat[0] if len(x.mode()) else "").items()}
            break
    out = wide.to_csv(index=False).encode("utf-8")
    lay = {
        "layout": "long statistical table", "rows_in": int(len(df)), "rows_out": int(len(wide)),
        "series_column": " | ".join(varying) if varying else None, "series": n_series, "kept": len(kept),
        "set_aside": {k: v for k, v in dropped.items() if v}, "start": start.strftime("%Y-%m-%d"),
        "order": [str(c) for c in order if c in wide.columns],
        "value_column": str(value), "date_column": str(date), "metadata_set_aside": [str(c) for c in meta],
        "constant_set_aside": [str(c) for c in constant], "zeros_as_empty": zeroed,
        "units": sorted(set(u for u in units.values() if u)),
    }
    if personal:                                  # wave 5d: only where there is one, so every other table's layout record is what it was
        lay["personal_set_aside"] = {str(c): k for c, k in personal.items()}
    if personal_kept:
        lay["personal_kept"] = {str(c): k for c, k in personal_kept.items()}
    return out, lay


def _slug(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _measure_label(pc: Dict[str, Any], header: Any) -> str:
    """A measure in words, from the AI plan's reading of its column: the plan's label when it gives one ("USD/CAD
    exchange rate"), else the column's own name ("value"), with the plan's unit when it names a real unit ("value
    (CAD per USD)"; a placeholder such as "currency" is no unit, see _unit_parts). Never a value from the file: a long
    table's one series is named after its value column (_reshape_long_panel)."""
    label = " ".join(str(pc.get("label") or "").split())[:80]
    name = label or " ".join(str(header or "").replace("_", " ").split()).lower()
    unit = " ".join(str(pc.get("unit") or "").split())
    pre, post = _unit_parts(unit)
    if (pre or post) and unit.lower() not in name.lower():
        name += " (%s)" % unit
    return name


def _measure_names(plan: Any, colmap: Dict[str, str], pub: Any) -> Dict[str, str]:
    """{landed name: the measure in words (_measure_label)} for each column the plan gives a label or a real unit."""
    out: Dict[str, str] = {}
    cols = plan.get("columns") if isinstance(plan, dict) else None
    for c in cols or []:
        if not isinstance(c, dict) or not c.get("name") or not (c.get("label") or c.get("unit")):
            continue
        land = colmap.get(str(c["name"]))
        if land:
            out[str(land)] = pub(_measure_label(c, c["name"]))
    return out


def _lead_series(lay: Dict[str, Any], objective: str) -> Tuple[str, str]:
    """The series a long table's report leads with, and why: the one the visitor's question names
    (every word of its name, apart from words all the series share), else the file's first."""
    order = lay.get("order") or []
    if not order:
        return "", ""
    if len(order) == 1:
        return order[0], "it is the file's only series"
    toks = [set(re.findall(r"[a-z0-9]+", str(k).lower().replace(".", ""))) for k in order]
    common = set.intersection(*toks) if toks else set()
    asked = set(re.findall(r"[a-z0-9]+", str(objective or "").lower().replace(".", "")))
    for k, t in zip(order, toks):
        core = t - common
        if core and core <= asked:
            return k, "your question names it"
    return order[0], "it comes first in the file; name another series in the question box to lead with it"


# ----------------------------------------------------------------------------- the AI plan
# Owner's design (25 Sep 2026): the AI reads a privacy-safe profile of the file, states the goal, says
# what each column is and directs the engine through a small set of generic operations; the engine
# computes every number. The plan arrives as JSON (the worker's /plan, DeepSeek) and is validated here:
# an operation that names a column the file lacks, or that is not in the menu, is refused and listed.
PLAN_OPS = ("set_aside", "keep_columns", "long_to_wide", "exclude_rows", "keep_rows", "exclude_blank", "date_from_year",
            "not_personal")
SEMANTIC_TYPES = ("flow_amount", "level", "percentage", "log_scale", "count", "rating", "category",
                  "ordinal", "duration", "date", "year", "boolean", "free_text", "identifier", "code",
                  "geography", "entity", "metadata", "other")


# The planner's profile reads the file the way the engine reads it (review M3, 29 Sep 2026: the profile said
# 39.7% of a money column was numeric where the engine read nearly all of it, and "no column reads as dates"
# where the data test passed). The page asks for the profile right after its first run of the engine on the
# same bytes, so that run leaves its reading here (the facts only, keyed by the file's sha256); any other
# caller gets a fresh landing and cleaning of the file by the engine itself.
_PROFILE_CACHE: Dict[str, Any] = {}
_PERSONAL_SHAPE = re.compile(r"@|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b")


def _span(d: Any) -> Dict[str, Any]:
    years, complete = _full_years(d)
    lo, hi = d.min(), d.max()
    return {"first": lo.strftime("%Y-%m"), "last": hi.strftime("%Y-%m"),
            "months": int((hi.year - lo.year) * 12 + hi.month - lo.month + 1), "dates": int(d.nunique()),
            "years": years, "complete": complete}


def _profile_facts(R: "_Reading", headers: List[str], hidden: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """What the planner's profile may say about each column, from the engine's reading: counts, how many
    values the engine read as numbers and as dates, the numbers' range, the commonest values, and the time
    span over the rows the engine kept (the rows the analyses read). Nothing here is sent anywhere by itself:
    profile_for_ai chooses what each column shows, by the visitor's privacy decisions. hidden: the landed names of
    the flagged columns the visitor did not keep, which the analyses leave out and so never read a series by
    (zeros_missing_if_level reads a level's zeros as the analyses would: on the rows the engine kept, in date order
    within each series, _series_groups)."""
    import numpy as np
    import pandas as pd
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    hidden = set(str(x) for x in hidden or ())
    # the file's date column (the one the engine reads the most dates in): the order a level's zeros are read in
    day_col, day_n = None, 0
    for h in headers:
        land = R.landed(h)
        if land is not None and R.kind(land) == "date":
            n_d = int(R.dates(land).notna().sum())
            if n_d > day_n:
                day_col, day_n = land, n_d
    days = R.dates(day_col) if day_col is not None else None
    kept = np.asarray(R.kept, bool)
    series: List[Any] = []                   # the rows' series (_series_groups), worked out once, when a level needs it
    for h in headers:
        land = R.landed(h)
        if land is None or land in seen:
            continue
        seen.add(land)
        txt = R.texts[land].astype(str).str.strip()
        fill = R.filled(land)
        f = txt[fill]
        n_f = int(fill.sum())
        kind = R.kind(land)
        nums, dts = R.numbers(land), R.dates(land)
        ok_n, ok_d = fill & nums.notna().to_numpy(), fill & dts.notna().to_numpy()
        info: Dict[str, Any] = {"header": str(h), "landed": land, "rows": R.n, "filled": n_f,
                                "distinct": int(f.nunique()), "kind": kind,
                                "numeric_share": round(float(ok_n.sum()) / n_f, 3) if n_f else 0.0,
                                "date_share": round(float(ok_d.sum()) / n_f, 3) if n_f else 0.0,
                                "percent_sign": bool(f.str.endswith("%").mean() > 0.5) if n_f else False,
                                "personal_shape": bool(f.head(500).str.contains(_PERSONAL_SHAPE).mean() > 0.05) if n_f else False}
        if ok_n.any():
            v = nums[ok_n]
            zeros = int((v == 0).sum())
            ev = None
            use = ok_n & kept
            if zeros and use.any() and _zero_gate(nums[use].to_numpy()):
                dd = days[use] if days is not None and land != day_col else None
                if dd is not None and not series:
                    # the series over the rows the engine kept, as the analyses read them (their frame is those rows)
                    series.append(_series_groups(
                        [(c, np.where(R.filled(c), R.texts[c].astype(str).str.strip().to_numpy(), "")[kept])
                         for c in dict.fromkeys(R.landed(x) for x in headers)
                         if c is not None and c != day_col and c not in hidden and R.kind(c) == "text"], days[kept]))
                grp = series[0][ok_n[kept]] if dd is not None and series and series[0] is not None else None
                _z, ev = _zero_shape(nums[use].to_numpy(), dd, grp)
            info.update({"min": float(v.min()), "median": float(v.median()), "max": float(v.max()),
                         "integers": bool((v % 1 == 0).all()), "zeros": zeros, "zeros_missing_if_level": bool(ev)})
        # short values (a median of 60 characters or fewer): a category's, never free text's (nl_viz.limits reads it for
        # a Pareto's category past the 300 values listed here)
        info["short"] = bool(n_f and kind != "number" and f.str.len().median() <= 60)
        if n_f and kind != "number" and info["distinct"] <= 300 and info["short"]:
            vc = f.value_counts()
            info["top_values"] = [str(x)[:60] for x in vc.head(12).index]
            info["groups"] = int((vc >= COMPARE_MIN_GROUP).sum())
            if 12 < info["distinct"]:
                info["values"] = sorted(str(x)[:60] for x in f.unique())
        if kind == "date":
            d = dts[np.asarray(R.kept, bool)].dropna()
            if len(d):
                info["time"] = _span(d)
        elif kind == "number" and info.get("integers") and _YEAR_NAME.search(str(h)) \
                and 1000 <= info.get("min", 0) and info.get("max", 0) <= 2999:
            y = nums[np.asarray(R.kept, bool)].dropna().astype(int)
            if len(y):
                yrs = sorted(set(int(v) for v in y.tolist()))
                info["time"] = {"first": "%04d-01" % yrs[0], "last": "%04d-12" % yrs[-1],
                                "months": 12 * (yrs[-1] - yrs[0] + 1), "dates": len(yrs), "years": yrs,
                                "complete": yrs, "year_column": True}
        out.append(info)
    return out


def _engine_profile_pass(data: bytes, name: str, decisions: Any = None, as_of: Optional[str] = None,
                         structure: bool = True, wide: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The engine lands the file, the decide stage runs as a run's does (_decide_and_guard: the adapter's
    personal-column check, the visitor's decisions, a withheld column landed as codes, a coded one coded), and
    the engine profiles and cleans it (its own code); its reading gives the profile's facts, so the planner
    reads each column as the run under these decisions will. Used when the run that last read these bytes ran
    under other decisions (the page's scan withholds every flagged column), and by tests and other callers."""
    tmp = None
    try:
        if not data or not data.strip():
            return {"ok": False, "error": "empty"}
        if _looks_like_title(data):
            return {"ok": False, "error": "the first row looks like a title"}
        _install_stubs()
        _import_engine()
        from northledger import clean as _clean
        from northledger import engagement as E
        from northledger import health as _health
        tmp = tempfile.mkdtemp(prefix="nl_profile_")
        src_dir = os.path.join(tmp, "incoming")
        os.makedirs(src_dir)
        src = os.path.join(src_dir, _clean_name(name))
        with open(src, "wb") as fh:
            fh.write(data)
        eng = E.open_engagement(os.path.join(tmp, "engagement"), create=True)
        res = E.land(eng, src)
        colmap = dict(getattr(res, "column_map", {}) or {})
        flagged, _withheld, _scrub, released = _decide_and_guard(E, eng, res, decisions)
        hidden = [str(f["column"]) for f in flagged if isinstance(f, dict) and f.get("decision") != "keep"]
        try:
            as_of = _dt.date.fromisoformat(str(as_of)[:10]).isoformat() if as_of else _dt.date.today().isoformat()
        except ValueError:
            as_of = _dt.date.today().isoformat()
        con = _health.connect_read_only(eng.db_path)
        try:
            th = _health.score_table(con, res.table)
            cr = _clean.clean_table(con, res.table, health=th, as_of=as_of)
        finally:
            con.close()
        R = _engine_reading(eng.db_path, res.table, _clean.standard_rules(th, as_of=as_of), cr, colmap)
        headers = list(R.land) or list(R.values.columns)
        facts = _profile_facts(R, headers, hidden)
        import nl_viz as _nv
        out = {"ok": True, "facts": facts, "rows": R.n, "flagged": flagged, "colmap": colmap, "released": released,
               "viz_stats": _nv.profile_stats(R, facts), "columns": int(res.n_cols)}
        if structure and STRUCTURE_ON:
            fail: Dict[str, Any] = {}
            S = _structure_detect(R, hidden, wide, fail=fail)
            out[_PROFILE_CACHE_STRUCTURE] = S
            if S is None and fail:
                # the structure layer could not run: a table of series is refused (the run reads this), any other file is read as before
                rec = _guard_failure(data, fail)
                if rec is not None:
                    out[_PROFILE_CACHE_ERROR] = rec
            elif S is not None and _refusable_verdict(S):
                rec = _verdict_failure(data, S)           # the layer answered "not a cube" for a table of series: refused, never averaged
                if rec is not None:
                    out[_PROFILE_CACHE_ERROR] = rec
            if S is not None:
                withheld = {str(f["column"]) for f in flagged if f.get("decision") == "withhold"}
                cleaning = {"rows_in": int(cr.total_in), "rows_clean": int(cr.rows_clean),
                            "rows_quarantined": int(cr.rows_quarantined)}
                out["file_health"] = _file_health(th.score, th.findings or [], S, withheld,
                                                  lambda t: public_text(_scrub.clean(t), res.table, name), cleaning)
        return out
    except Exception as exc:  # noqa: BLE001 - the profile is an aid; the page runs without it
        intake_error = getattr(sys.modules.get("northledger.intake"), "IntakeError", Refusal)
        if isinstance(exc, (Refusal, intake_error)):
            return {"ok": False, "error": "unreadable: %s" % type(exc).__name__}
        # wave 5e (P7): any other failure of the pass is a failure of the layer's reading (stage "profile"): a table of series is refused,
        # and a plan's run reads that record (never "the profile pass failed, so the rows are averaged"); a strict run raises for any
        # other file, as for every other stage
        rec = _guard_failure(data, _failure("profile", exc))
        out = {"ok": False, "error": "unreadable: %s" % type(exc).__name__}
        if rec is not None:
            out[_PROFILE_CACHE_ERROR] = rec
        return out
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _profile_privacy(facts: List[Dict[str, Any]], flagged: Any, decisions: Any) -> Dict[str, Tuple[str, str]]:
    """{column: (decision, why it was flagged)} for every column the engine's scan flagged, compared by the
    engine's landed name (review H1, 29 Sep 2026: a "Date Of Birth" header against the flag on date_of_birth
    let a withheld column through). flagged: {column: kind} from the scan (the page's worker), or {column:
    {"kind", "decision"}}, or the report's privacy.flagged list; decisions: {column: decision}. A flagged
    column with no decision is withheld, the engine's default; one the engine coded as it arrived cannot be
    kept, only coded or withheld. With no decisions at all (the page's first call, before the visitor has
    chosen) every flagged column is withheld: a profile sent as it stands never names one."""
    fl: Dict[str, Tuple[str, Optional[str]]] = {}
    if isinstance(flagged, list):
        flagged = {str(x.get("column")): {"kind": x.get("kind"), "decision": x.get("decision")}
                   for x in flagged if isinstance(x, dict) and x.get("column")}
    for k, v in dict(flagged or {}).items():
        if isinstance(v, dict):
            fl[str(k)] = (str(v.get("kind") or "possible personal data"), v.get("decision"))
        else:
            fl[str(k)] = (str(v or "possible personal data"), None)
    dec = {str(k): str(v or "").strip().lower() for k, v in dict(decisions or {}).items()}
    out: Dict[str, Tuple[str, str]] = {}
    for f in facts:
        keys = [f["header"], f["landed"], _engine_slug(f["header"])]
        hit = next((fl[k] for k in keys if k in fl), None)
        if hit is None:
            continue
        kind, d0 = hit
        want = next((dec[k] for k in keys if k in dec), None) or str(d0 or "").strip().lower() or "withhold"
        if want not in DECISIONS:
            want = "withhold"
        if want == "keep" and CODED_ON_ARRIVAL in kind:
            want = "code"
        out[f["header"]] = (want, kind)
    return out


def _same_choices(got: Dict[str, Any], decisions: Any) -> bool:
    """Whether a profile pass (or the run that left its reading) settled every flagged column as these
    decisions would: only then may its facts stand for them."""
    fl = got.get("flagged")
    if not isinstance(fl, list):
        return False
    colmap = dict(got.get("colmap") or {})
    # a released category the visitor now withholds or codes was read under other choices (_release_categories)
    for r in got.get("released") or []:
        for k, v in dict(decisions or {}).items():
            if str(k) in (r.get("column"), r.get("header"), colmap.get(str(k), "\0")) and \
                    str(v or "").strip().lower() in ("withhold", "code") and \
                    (str(k) == r.get("column") or str(k) == r.get("header") or colmap.get(str(k)) == r.get("column")):
                return False
    return _effective_decisions(fl, colmap, decisions) == {f["column"]: f["decision"] for f in fl}


def _merged_flags(got: Dict[str, Any], flagged: Any) -> Dict[str, Dict[str, Any]]:
    """{landed column: {"kind", "decision"}}: every column the engine flagged and the adapter's check added, as
    the pass settled it, plus any other column the caller flags (by the file's header, the landed name or
    its slug), which is then withheld unless the caller's decisions say otherwise."""
    out: Dict[str, Dict[str, Any]] = {str(f["column"]): {"kind": f.get("kind"), "decision": f.get("decision")}
                                      for f in got.get("flagged") or [] if isinstance(f, dict) and f.get("column")}
    colmap = dict(got.get("colmap") or {})
    if isinstance(flagged, list):
        flagged = {str(x.get("column")): {"kind": x.get("kind"), "decision": x.get("decision")}
                   for x in flagged if isinstance(x, dict) and x.get("column")}
    for k, v in dict(flagged or {}).items():
        k = str(k)
        if k in out or colmap.get(k) in out or _engine_slug(k) in out:
            continue
        out[k] = v if isinstance(v, dict) else {"kind": str(v or "possible personal data"), "decision": None}
    return out


def profile_for_ai(data: Any, name: str = "", max_cols: int = 120, flagged: Any = None,
                   decisions: Any = None, as_of: Optional[str] = None) -> Dict[str, Any]:
    """What the AI planner sees: names, how the engine reads each column (the shares it read as numbers and as
    dates), counts, ranges (a number column's min, median and max; profile.time: the date column's first and
    last month), for a number column with values of exactly 0 how many (zeros) and whether the analyses would
    read them as no value were the column typed a level (zeros_missing_if_level: _zero_shape's evidence rule on the rows
    the engine kept, in the file's date order within each series as the analyses read it (_series_groups), see
    PLACEHOLDER_ZERO_TYPES; typed an amount or a count, 0 stays a real 0)
    and, for a text or date column of at most 300 distinct short values (median 60 characters or
    fewer), its 12 commonest values and, when it has more than 12, every one of them (each cut at 60
    characters): the page's consent says exactly this (src/js/50-try.js AI_CONSENT). Never rows, and never the
    file's name (FILE_WORD stands in for it).
    The visitor's privacy decisions (flagged, decisions: see _profile_privacy) decide each flagged column:
      withheld (the default): absent. Never named, never counted in a reason, never the time column.
      coded: its name, its type (the numeric and date shares, whole numbers, a % sign), its counts and
             looks_personal: true, and nothing that holds a value (no top_values, values, examples, min,
             median or max). It is never the time column and never counted in analysis_limits: the
             analyses leave it out.
      kept: like any column, looks_personal: false.
    With no decisions every flagged column is withheld. The page asks once, after the visitor's choices
    (engine/worker.js "profile"), so a coded or kept column reaches the planner as chosen. The facts come from
    the engine's reading under these same decisions (the scan's run when it ran under them, else a profile
    pass): a withheld column no rule read, a coded column's codes, a kept column read like any other. Every
    column the engine flagged or the adapter's personal-column check added counts as flagged, whatever the
    caller passes in `flagged`."""
    data = _as_bytes(data)
    try:
        data = normalize_file(data)[0]              # wave 5f (H, E): the bytes the run reads (dates and numbers the core cannot read, rewritten)
    except Refusal as exc:
        return {"ok": False, "error": str(exc)}
    sha = hashlib.sha256(data).hexdigest()
    got = _PROFILE_CACHE.get("value") if _PROFILE_CACHE.get("sha") == sha else None
    if got is not None and not _same_choices(got, decisions):
        got = None
    if got is None:
        got = _engine_profile_pass(data, name, decisions, as_of)
        if got.get("ok"):
            _PROFILE_CACHE.clear()
            _PROFILE_CACHE.update(sha=sha, value=got)
    if not got.get("ok"):
        return {"ok": False, "error": str(got.get("error") or "unreadable")}
    facts = got["facts"]
    priv = _profile_privacy(facts, _merged_flags(got, flagged), decisions)
    shown = [f for f in facts if (priv.get(f["header"]) or ("",))[0] != "withhold"]
    cols = []
    for f in shown[:max_cols]:
        d, kind = priv.get(f["header"]) or (None, "")
        info: Dict[str, Any] = {"name": f["header"], "filled": f["filled"], "distinct": f["distinct"],
                                "numeric_share": f["numeric_share"]}
        if f["kind"] == "number" and "min" in f:
            info.update({k: f[k] for k in ("min", "median", "max", "integers")})
            info["percent_sign"] = f["percent_sign"]
            if f.get("zeros"):
                # how many values are exactly 0, and whether they have a missing value's pattern (PLACEHOLDER_ZERO_TYPES):
                # typed a level, such a column's 0 is read as no value by the analyses; an amount or a count keeps it
                info["zeros"] = int(f["zeros"])
                info["zeros_missing_if_level"] = bool(f.get("zeros_missing_if_level"))
        else:
            info["date_share"] = f["date_share"]
            looks = bool(f["personal_shape"]) and d != "keep"
            if not looks and "top_values" in f:
                info["top_values"] = list(f["top_values"])
                if "values" in f:
                    # every value of a category column (countries, regions, products) so the AI can tell the
                    # members from the aggregates (World, Asia, High-income countries) mixed in with them
                    info["values"] = list(f["values"])
            info["looks_personal"] = looks
        if f["rows"] > f["filled"]:
            info["blank"] = int(f["rows"] - f["filled"])
        if d == "code":
            info = {k: info[k] for k in ("name", "filled", "distinct", "blank", "numeric_share", "date_share")
                    if k in info}
            info["date_share"] = f["date_share"]
            if "integers" in f:
                info["integers"] = f["integers"]
            info["percent_sign"] = f["percent_sign"]
            info["looks_personal"] = True
            info["privacy_flag"] = str(kind)[:60]
        elif d == "keep":
            info["looks_personal"] = False
        cols.append(info)
    # a statistical table's structure (nl_structure.profile_block, CONTRACT §5.4): its dimensions' roles, the slice ids and
    # the breakdown ids the plan may name, at most 6,000 bytes; only the columns shown here, and only member strings that
    # a column's own values below already hold (a coded or personal-looking column has none)
    structure_block = None
    S_prof = got.get(_PROFILE_CACHE_STRUCTURE)
    if STRUCTURE_ON and S_prof is not None and S_prof.get("usable"):
        try:
            values_of = {c["name"]: list(c.get("values") or []) + list(c.get("top_values") or []) for c in cols
                         if not c.get("looks_personal") and ("values" in c or "top_values" in c)}
            structure_block = _ns().profile_block(S_prof, values_of, columns=[c["name"] for c in cols])
        except Exception:  # noqa: BLE001 - the profile is an aid; the planner still gets the columns
            if os.environ.get("NL_BROWSER_STRICT"):
                raise
            structure_block = None
    # never the file's name (final review, 29 Sep 2026: "private_mix.csv" reached the planner): it says what the
    # file is about and whose it is, as the /report payload's "[your file]" stands in for it (src/js/50-try.js)
    out = {"ok": True, "name": FILE_WORD, "rows": int(got.get("rows") or 0), "columns": cols,
           "columns_total": len(shown), "time": None, "analysis_limits": [],
           # the list of search terms the adapter builds the report's web searches from (engine/context_terms.json):
           # the planner's plan.context must use its terms (_context_queries); its version, so the two can tell
           "context_terms_version": _context_terms().get("version") or None}
    try:
        out["time"], out["analysis_limits"] = _time_and_limits(facts, priv, int(got.get("rows") or 0))
    except Exception:  # noqa: BLE001 - the profile is an aid; the planner still gets the columns
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        out["time"], out["analysis_limits"] = None, []
    # the charts the planner may choose (nl_viz.limits, CONTRACT §5.9): one {chart, ok, why} per menu chart, at most
    # 16, over the rows the engine kept; every flagged column (a kept one too) skipped before anything is chosen
    try:
        import nl_viz as _nv
        out["chart_limits"] = _nv.limits(facts, priv, out["time"], got.get("viz_stats"), int(got.get("rows") or 0))
    except Exception:  # noqa: BLE001 - the profile is an aid; the planner still gets the columns
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        out["chart_limits"] = []
    if structure_block is not None:
        out["structure"] = structure_block
    return out


_YEAR_NAME = re.compile(r"(?i)(?:^|[^a-z])(?:year|yr|fy)(?:$|[^a-z])|year")


def _cut160(s: str) -> str:
    return s if len(s) <= 160 else s[:157].rsplit(" ", 1)[0].rstrip(",;") + "..."


def _time_and_limits(facts: List[Dict[str, Any]], priv: Dict[str, Tuple[str, str]], rows: int
                     ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """profile.time and profile.analysis_limits, for the planner (owner's schema, 29 Sep 2026):
    time = {"column", "first": "YYYY-MM", "last": "YYYY-MM", "months" (the span, first to last month
    inclusive), "distinct_years"} over the rows the engine kept, for the column the engine reads as dates with
    the most dates (else a whole-year column named like a year), or None. analysis_limits = [{"analysis", "ok",
    "why"}] (at most 12, why at most 160 characters, one per analysis the planner can choose) from the minimums
    the analyses themselves enforce (TREND_MIN_YEARS and the rest), so the planner does not ask for a trend a
    23-month file cannot give. A withheld or coded column is left out BEFORE anything is chosen (review H5):
    never the time column, never counted, never named."""
    usable = [f for f in facts if (priv.get(f["header"]) or ("keep",))[0] == "keep"]
    dated = [f for f in usable if f.get("time") and not f["time"].get("year_column")]
    best = max(dated, key=lambda f: f["time"]["dates"]) if dated else next(
        (f for f in usable if f.get("time") and f["time"].get("year_column")), None)
    time = None
    complete: List[int] = []
    dates_n = 0
    if best is not None:
        tm = best["time"]
        complete, dates_n = list(tm["complete"]), int(tm["dates"])
        time = {"column": best["header"], "first": tm["first"], "last": tm["last"], "months": int(tm["months"]),
                "distinct_years": len(tm["years"])}
    numeric = [f for f in usable if f["kind"] == "number" and f is not best]
    span = ("the file spans %s months (%s to %s), %s" % (format(time["months"], ","), time["first"], time["last"],
                                                          _n_values(len(complete), "complete calendar year"))
            if time else "no column the engine reads as dates")
    lim: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, why: str) -> None:
        lim.append({"analysis": name, "ok": bool(ok), "why": _cut160(why)})

    add("trend", bool(time) and len(complete) >= TREND_MIN_YEARS,
        "needs %d or more complete years of values; %s" % (TREND_MIN_YEARS, span))
    add("extremes", bool(time) and len(complete) >= EXTREMES_MIN_YEARS,
        "needs %d or more complete years; %s" % (EXTREMES_MIN_YEARS, span))
    add("agreement", bool(time) and dates_n >= AGREEMENT_MIN_DATES and len(numeric) >= 2,
        "needs two numeric series with %d or more shared dates; the file has %s and %s" % (
            AGREEMENT_MIN_DATES, _n_values(dates_n, "distinct date") if time else "no date column",
            _n_values(len(numeric), "numeric column")))
    groups = sorted(((int(f.get("groups") or 0), f) for f in usable
                     if f["kind"] == "text" and f is not best and 2 <= f["distinct"] <= 300 and "top_values" in f),
                    key=lambda g: -g[0])
    series_ok = bool(time) and len(complete) >= RANK_MIN_YEARS and len(numeric) >= 2
    add("rank", (bool(groups) and bool(numeric)) or series_ok,
        "needs a column of entities (countries, products) and a measure, or two numeric series over %d complete "
        "years; %s" % (RANK_MIN_YEARS, ("%s has %s" % (groups[0][1]["header"], _n_values(groups[0][1]["distinct"], "value")))
                        if groups else ("%s" % span if time else "no column of a few hundred values or fewer")))
    add("share", len(numeric) >= 2, "needs two or more numeric parts of a whole; the file has %s" % _n_values(len(numeric), "numeric column"))
    add("compare", bool(groups) and groups[0][0] >= 2 and bool(numeric),
        "needs a column of groups with 2 or more groups of %d or more rows; %s" % (
            COMPARE_MIN_GROUP, ("%s has %d such groups" % (groups[0][1]["header"], groups[0][0])) if groups else "no such column"))
    add("relationship", len(numeric) >= 2 and rows >= RELATIONSHIP_MIN_ROWS,
        "needs two numeric columns and %d or more rows; the file has %s and %s" % (
            RELATIONSHIP_MIN_ROWS, _n_values(len(numeric), "numeric column"), _n_values(rows, "row")))
    most = max([int(f["filled"]) for f in numeric] or [0])
    add("distribution", most >= DISTRIBUTION_MIN_VALUES,
        "needs %d or more values in a numeric column; the fullest has %s" % (DISTRIBUTION_MIN_VALUES, format(most, ",")))
    # free text: words, not numbers, dates or a short list of codes (a column of a few hundred values has
    # top_values), and not the time column
    texts = [f for f in usable if f is not best and f["kind"] == "text" and not f["personal_shape"]
             and "top_values" not in f and f["filled"] >= THEMES_MIN_TEXTS]
    add("themes", bool(texts), "needs a free-text column with %d or more texts; %s" % (
        THEMES_MIN_TEXTS, ("%s has %s" % (texts[0]["header"], format(int(texts[0]["filled"]), ","))) if texts
        else "no free-text column"))
    add("predict", rows >= PREDICT_MIN_ROWS and len(numeric) >= 1,
        "needs %d or more complete rows and %d per model term; the file has %s, scored %s" % (
            PREDICT_MIN_ROWS, PREDICT_ROWS_PER_TERM, _n_values(rows, "row"),
            "forward in time by %s" % time["column"] if time else "in random folds (no usable date)"))
    return time, lim[:12]


def _from_js(v: Any) -> Any:
    """A value from the page's worker: JSON text, a Pyodide proxy or a plain value."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    if v is not None and hasattr(v, "to_py"):
        return v.to_py()
    return v


def profile_json(data: Any, name: str = "", flagged: Any = None, decisions: Any = None, as_of: Any = None) -> str:
    """profile_for_ai as JSON text; flagged: {column: kind} from the scan (the engine's privacy flags), or
    {column: {"kind", "decision"}}; decisions: {column: "withhold" | "code" | "keep"} (the visitor's). With no
    decision a flagged column is withheld: absent from the profile."""
    return json.dumps(profile_for_ai(data, name, flagged=_from_js(flagged), decisions=_from_js(decisions),
                                     as_of=str(as_of) if as_of else None),
                      allow_nan=False, default=str)


def plan_profile_json(data: Any, name: str = "", flagged: Any = None, decisions: Any = None, as_of: Any = None) -> str:
    """What engine/worker.js answers the page's "profile" message with, after the visitor's choices:
    {"profile": profile_for_ai(...), "landed": {the file's header: the engine's landed name}} for every column
    of the file. The page keeps "landed" to itself and never sends it: its second filter (T.planProfile and the
    re-plan's filters) matches each profiled column to its flag exactly, and knows every spelling of a withheld
    column's name ("Date Of Birth" and date_of_birth) to keep out of what it sends."""
    data = _as_bytes(data)
    prof = profile_for_ai(data, name, flagged=_from_js(flagged), decisions=_from_js(decisions),
                          as_of=str(as_of) if as_of else None)
    landed: Dict[str, str] = {}
    got = _PROFILE_CACHE.get("value") or {}
    if prof.get("ok") and _PROFILE_CACHE.get("sha") == hashlib.sha256(data).hexdigest():
        landed = {str(f["header"]): str(f["landed"]) for f in got.get("facts") or []}
    return json.dumps({"profile": prof, "landed": landed}, allow_nan=False, default=str)


# What leaves for an AI (the /plan feedback and the /report payload) or a share link never names a column the
# visitor withheld and never quotes a cell (reviewer 2 H4, 29 Sep 2026: a finding sent to /report read "could
# not be read as numbers (for example 'ask Marisol')").
WITHHELD_WORDS = "a column you withheld"
DATE_WITHHELD_LEAD = "The time analysis is not shown: the date column the engine chose for it"
_LABEL_RE = re.compile(r"\[(?:phone number|email address|withheld)\]")
_EXAMPLE_RE = re.compile(r"\s*\((?:for example|for instance|e\.g\.,?|such as)\b[^()]*\)", re.I)


class _NameScrub:
    """Every mention of a withheld column's name, in any spelling of it (date_of_birth, Date Of Birth,
    date-of-birth, DATE OF BIRTH), replaced by "a column you withheld". The page's own labels ([phone number],
    [email address], [withheld]) are left as they are."""

    def __init__(self, names: Iterable[Any]) -> None:
        pats = set()
        for n in names:
            toks = re.findall(r"[A-Za-z0-9]+", str(n or ""))
            if toks:
                pats.add(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9\n]{0,3}".join(re.escape(t) for t in toks) + r"(?![A-Za-z0-9])")
        self.rx = re.compile("|".join(sorted(pats, key=len, reverse=True)), re.I) if pats else None

    def names(self, text: Any) -> bool:
        return bool(self.rx is not None and isinstance(text, str) and self.rx.search(_LABEL_RE.sub(" ", text)))

    def __call__(self, text: Any, words: str = "") -> Any:
        """text with every mention replaced by `words` (default WITHHELD_WORDS, "a column you withheld")."""
        if not isinstance(text, str) or self.rx is None:
            return text
        if text.startswith(DATE_WITHHELD_LEAD) and not words:
            return DATE_WITHHELD_LEAD + " is " + WITHHELD_WORDS + "."
        parts, labels = _LABEL_RE.split(text), _LABEL_RE.findall(text)
        out = []
        for i, part in enumerate(parts):
            out.append(self.rx.sub(words or WITHHELD_WORDS, part))
            if i < len(labels):
                out.append(labels[i])
        return "".join(out)


def _withheld_names(rep: Dict[str, Any]) -> List[str]:
    """The withheld columns' names as the report knows them: the engine's landed names (privacy.flagged) and
    the file's own spellings of them in the plan and the data tests."""
    wh = [str(f.get("column")) for f in (rep.get("privacy") or {}).get("flagged") or []
          if isinstance(f, dict) and f.get("decision") == "withhold" and f.get("column")]
    if not wh:
        return []
    land = set(wh)
    names = list(wh)
    plan = rep.get("ai_plan") or {}
    cands = [c.get("name") for c in plan.get("columns") or [] if isinstance(c, dict)]
    for op in plan.get("operations") or []:
        if isinstance(op, dict):
            cands += [op.get("column")] + list(op.get("columns") or [])
    cands += [t.get("column") for t in (rep.get("contracts") or {}).get("tests") or [] if isinstance(t, dict)]
    for c in cands:
        if c and (_engine_slug(c) in land or str(c) in land):
            names.append(str(c))
    return names


def _scenarios_for_ai(sc: Any, safe: Any) -> Dict[str, Any]:
    """rep["scenarios"] for the report writer, 1:1: the same keys in the same order, every number and every
    item's text as they are, and every word (the claim, column and level names, labels, assumptions, refusals,
    the note) through `safe`, as every other text of results_for_ai."""
    if not isinstance(sc, dict):
        return {"basis": None, "items": [], "refused": [], "note": ""}

    def words(x: Any, n: int) -> Any:
        return safe(x, n) if isinstance(x, str) else x
    basis = sc.get("basis")
    if isinstance(basis, dict):
        b = dict(basis)
        for k, n in (("claim", 400), ("measure", 80), ("unit", 40), ("grade_words", 80)):
            b[k] = words(b.get(k), n)
        seg = b.get("segment")
        if isinstance(seg, dict):
            b["segment"] = {k: (words(seg.get(k), 80) if k == "column" else [words(x, 80) for x in seg.get(k) or []])
                            for k in ("column", "levels", "folded", "entered", "exited") if k in seg or k in
                            ("column", "levels", "folded")}
        basis = b
    items = []
    for it in sc.get("items") or []:
        if not isinstance(it, dict):
            continue
        d = dict(it)
        for k, n in (("segment", 80), ("label", 400), ("text", 80), ("assumes", 400), ("unit", 40)):
            d[k] = words(d.get(k), n)
        if "grade_words" in d:
            d["grade_words"] = words(d["grade_words"], 160)
        inp = d.get("inputs")
        if isinstance(inp, dict):
            d["inputs"] = {"columns": [words(c, 80) for c in inp.get("columns") or []],
                           "window": inp.get("window"), "op": words(inp.get("op"), 200)}
        items.append(d)
    return {"basis": basis, "items": items, "refused": [words(x, 300) for x in sc.get("refused") or []],
            "note": words(sc.get("note") or "", 600)}


def _primary_for_ai(rep: Dict[str, Any], safe: Any, scrub: Any) -> Optional[Dict[str, Any]]:
    """The report's primary claim for the writer, {id, claim, grade}, or None (integration pass, 30 Sep 2026: the
    PDF's key figures guessed it from the claims' words, and a file whose plan named amount could lead with its
    rows). It is the claim the scenarios break down (their basis: the total of the plan's primary column, see
    nl_scenarios._pick_basis), else the engine's primary claim (its gate's primary, set from the plan's primary
    column: the average when that column is a rate or a price, which the scenarios refuse to break down), else None.
    Its claim is the text its finding carries in `findings` (the same `safe`), its grade the report's grade word;
    never a claim about a withheld column (None then, as its finding is never sent)."""
    by_id = {f.get("id"): f for f in rep.get("findings") or [] if isinstance(f, dict) and f.get("id")}
    sc = rep.get("scenarios") if isinstance(rep.get("scenarios"), dict) else {}
    basis = sc.get("basis") if isinstance(sc.get("basis"), dict) else {}
    pm = rep.get("primary_metric") if isinstance(rep.get("primary_metric"), dict) else {}
    for fid in (basis.get("finding_id"), pm.get("finding_id")):
        f = by_id.get(fid) if fid else None
        if f is None or not f.get("claim"):
            continue
        if scrub.names(f.get("claim")) or scrub.names(f.get("why")):
            return None
        return {"id": str(fid)[:120], "claim": safe(f.get("claim"), 400),
                "grade": str(f.get("grade") or f.get("verdict") or "")[:20]}
    return None


def _estimand_for_ai(est: Any, safe: Any) -> Optional[Dict[str, Any]]:
    """rep["estimand"] for the report writer: every figure with its text, the slice and what was left out, its words made
    safe as every other text (never a withheld column's name)."""
    if not isinstance(est, dict):
        return None
    out = {"text": safe(est.get("text"), 400),
           "slice": [{"dim": safe(x.get("dim"), 120), "member": safe(x.get("member") if isinstance(x.get("member"), str)
                                                                      else json.dumps(x.get("member")), 120),
                      "why": safe(x.get("why"), 200)} for x in est.get("slice") or [] if isinstance(x, dict)][:8],
           "measure": {k: (est.get("measure") or {}).get(k) for k in ("label", "uom", "scale", "scale_applied", "type",
                                                                      "type_basis", "aggregation")},
           "comparison": est.get("comparison"),
           **({"period": {k: (est["period"] or {}).get(k) for k in ("kind", "noun", "nouns", "window")}}
              if isinstance(est.get("period"), dict) and int(est["period"].get("step") or 1) != 1 else {}),
           "figures": {k: dict({"value": (v or {}).get("value"), "text": safe((v or {}).get("text"), 40)},
                               # the periods a window figure adds up, only when they are not the whole window (matched months)
                               **({"months": v["months"]} if isinstance(v.get("months"), int) and not isinstance(v.get("months"), bool)
                                  and v["months"] != int(((est.get("period") or {}).get("window")) or 12) else {}))
                       for k, v in (est.get("figures") or {}).items() if isinstance(v, dict)},
           "sum_checks": [dict({"dim": safe(c.get("dim"), 120), "total": safe(c.get("total"), 120), "parts": c.get("parts"),
                                "verdict": c.get("verdict"),
                                "unallocated_latest": {"value": (c.get("unallocated_latest") or {}).get("value"),
                                                       "text": safe((c.get("unallocated_latest") or {}).get("text"), 40)}},
                               # how it adds up, for the report's estimand block (the worker keeps complete_cells and
                               # max_rel_residual; the page's own copy keeps the rest)
                               **{k: c[k] for k in ("complete_cells", "within_tolerance", "max_rel_residual",
                                                    "built_from_parts", "suppressed_part_months") if k in c},
                               **({"max_residual": {"value": (c.get("max_residual") or {}).get("value"),
                                                    "text": safe((c.get("max_residual") or {}).get("text"), 40)}}
                                  if isinstance(c.get("max_residual"), dict) else {}))
                          for c in est.get("sum_checks") or [] if isinstance(c, dict)][:8],
           "excluded": [{"what": safe(x.get("what"), 120), "why": safe(x.get("why"), 200)}
                        for x in est.get("excluded") or [] if isinstance(x, dict)][:8],
           "plan_source": est.get("plan_source"), "inference": _inference_for_ai(est.get("inference"), safe)}
    mc = est.get("measure_choice")
    if isinstance(mc, dict) and isinstance(mc.get("chosen"), dict):
        # a table whose members are different measures: the one shown, who chose it, the rule and the others left out
        out["measure_choice"] = {
            "dim": safe(mc.get("dim"), 120), "by": mc.get("by"), "rule": safe(mc.get("rule"), 200),
            "chosen": {k: safe(mc["chosen"].get(k), 80) for k in ("id", "name", "uom", "type", "type_basis")},
            "alternatives": [{"id": safe(a.get("id"), 8), "name": safe(a.get("name"), 80), "type": a.get("type"),
                              "precision": bool(a.get("precision"))}
                             for a in mc.get("alternatives") or [] if isinstance(a, dict)][:8]}
    bf = est.get("built_from")
    if isinstance(bf, dict) and bf.get("n"):
        # a table with no total row: the headline is the sum of its parts (nl_structure.parts_info), said in its own words
        out["built_from"] = {"dim": safe(bf.get("dim"), 120), "noun": safe(bf.get("noun"), 20), "n": bf.get("n"),
                             "text": safe(bf.get("text"), 160), "complete_months_only": bool(bf.get("complete_months_only")),
                             "incomplete": bool(bf.get("incomplete")),
                             "suppressed_part_months": bf.get("suppressed_part_months"),
                             "months_dropped": [str(x)[:7] for x in (bf.get("months_dropped") or [])][:12],
                             "combined": [safe(x, 120) for x in (bf.get("combined") or [])][:6]}
    sm = est.get("single_member")
    if isinstance(sm, dict) and sm.get("member"):
        # a table with no total member: one member is shown, said in the estimand's own words (nl_structure.estimand)
        out["single_member"] = {"dim": safe(sm.get("dim"), 120), "member": safe(sm.get("member"), 120),
                                "noun": safe(sm.get("noun"), 20), "statement": safe(sm.get("statement"), 240)}
    return out


TREND_VERDICTS = ("rising", "falling", "no_settled_direction", "not_graded")


def _trend_test_for_ai(t: Any, safe: Any) -> Optional[Dict[str, Any]]:
    """A trend analysis's test (nl_inference.trend_test) for the writer, in a compact record of about 220 bytes:
    {verdict, name, n, p, p_random_walk, size {nominal, simulated, claims, newey_west}}. `verdict` is one of "rising",
    "falling", "no_settled_direction" or "not_graded" (anything else reads "not_graded": a word the writer cannot grade
    on is never passed on as a finding); p, the random-walk screen's p and n are the test's own figures; `size` is what
    the test found in simulated series with no trend like this one (a share of them: "simulated" at the nominal level,
    "claims" the share that came out as a rising or falling verdict) and what the Newey-West range used before found.
    The slope, its range, the momentum and the simulation's cell are in the engine's own report (the page's trend card
    and the analyst view), not here. Never the series' name (a column)."""
    if not isinstance(t, dict) or not t.get("verdict"):
        return None
    v = str(t["verdict"])
    out: Dict[str, Any] = {"verdict": v if v in TREND_VERDICTS else "not_graded", "name": safe(t.get("name"), 90)}
    for k in ("n", "p", "p_random_walk"):
        x = t.get(k)
        if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
            out[k] = x
    z = t.get("size")
    if isinstance(z, dict):
        sz = {k: z[k] for k in ("nominal", "simulated", "claims", "newey_west")
              if isinstance(z.get(k), (int, float)) and not isinstance(z.get(k), bool) and math.isfinite(z[k])}
        if sz:
            out["size"] = sz
    return out


def _privacy_for_ai(pr: Any, safe: Any, scrub: Any) -> Optional[Dict[str, Any]]:
    """The categories the engine read as categories, not personal data (privacy.released), as the words the visitor was
    shown ("Read as a category, not personal data: <column> (30 labels)"), for the report's method section. A column the
    visitor withheld is never named (it is not among the released ones, and a text that names one is left out)."""
    rel = []
    for x in (pr or {}).get("released") or [] if isinstance(pr, dict) else []:
        if not isinstance(x, dict) or not x.get("text") or scrub.names(x.get("text")):
            continue
        rel.append({"text": safe(x["text"], 200), "distinct": x.get("distinct") if isinstance(x.get("distinct"), int) else None})
    return {"released": rel[:8]} if rel else None


def _inference_for_ai(inf: Any, safe: Any) -> Optional[Dict[str, Any]]:
    """An official aggregate's record (findings[].inference, estimand.inference) for the writer: every word through the
    same scrubber as the rest of the payload (the record counts a withheld column, never names it), figures as they are."""
    if not isinstance(inf, dict):
        return None
    out: Dict[str, Any] = {k: inf.get(k) for k in ("mode", "publisher", "describe") if k in inf}
    out["how_known"] = [safe(x, 300) for x in inf.get("how_known") or [] if isinstance(x, str)][:6]
    for k in ("revisions", "grade_label"):
        if isinstance(inf.get(k), str):
            out[k] = safe(inf[k], 300)
    q = inf.get("quality")
    out["quality"] = {"column": safe(q.get("column"), 120), "codes": q.get("codes")} if isinstance(q, dict) else None
    return out


def _structure_for_ai(st: Any, safe: Any) -> Optional[Dict[str, Any]]:
    """The structure's summary for the writer: the dimensions and their roles, the slice, the flags' counts."""
    if not isinstance(st, dict) or not st.get("kind"):
        return None
    # wave 5c: no_total_member / single_by (one member shown by dominance, wave 5b) and aggregate_by (how a rate's or an index's published
    # aggregate was found) are the keys contract section 5.12 ("For the worker") names; sum_check {verified: false} marks an aggregate that
    # is one by its name alone (no check could verify it). Each is sent only where the dimension has it.
    dims = [dict({k: (safe(d[k], 120) if isinstance(d.get(k), str) else d[k])
                  for k in ("column", "role", "members", "total", "parts", "nsa", "sa", "depths", "no_total_member", "single_by", "aggregate_by")
                  if k in d},
                 **({"sum_check": {"verified": False}} if isinstance(d.get("sum_check"), dict) and d["sum_check"].get("verified") is False else {}))
            for d in st.get("dims") or [] if isinstance(d, dict)][:8]
    fl = st.get("flags") if isinstance(st.get("flags"), dict) else None
    out = {"kind": st.get("kind"), "usable": st.get("usable"), "publisher": st.get("publisher"),
           "series": st.get("series"), "months": st.get("months"), "dims": dims,
           "slice": st.get("slice"), "reason": safe(st.get("reason") or "", 300),
           "flags": {"column": fl.get("column"), "by_kind": fl.get("by_kind"),
                     "quality_of_headline": fl.get("quality_of_headline")} if fl else None,
           "corrections": [{"dim": safe(c.get("dim"), 120), "kind": c.get("kind")} for c in st.get("corrections") or []
                           if isinstance(c, dict)][:6]}
    err = st.get("error")
    if st.get("kind") == "error" and isinstance(err, dict):
        # the structure layer could not run on a table of series (wave 5b): what stopped it, never a cell of the file
        out["error"] = {"stage": safe(err.get("stage"), 40), "type": safe(err.get("type"), 80), "message": safe(err.get("message"), 200)}
    return out


def results_for_ai(rep: Any) -> Dict[str, Any]:
    """The engine's own report distilled for the AI report writer (the /report route): every figure
    the writer may quote, nothing else. The writer never sees a row; it sees the goal, the graded
    claims with their values and intervals, the AI-named analyses with their tables, the story,
    the forecast, and the health and cleaning summary. Capped: the worker's body limit is small.
    A withheld column is never named (its lines are left out, or it reads "a column you withheld"), and no
    text quotes a cell as an example.
    """
    if isinstance(rep, str):
        try:
            rep = json.loads(rep)
        except ValueError:
            return {"ok": False, "error": "the report is not JSON"}
    if not isinstance(rep, dict):
        return {"ok": False, "error": "the report is not an object"}

    scrub = _NameScrub(_withheld_names(rep))

    def safe(x: Any, n: int) -> str:
        return _EXAMPLE_RE.sub("", scrub(str(x if x is not None else "")))[:n]

    def _num(x: Any) -> Any:
        try:
            f = float(x)
            return f if (f == f and abs(f) != float("inf")) else None
        except (TypeError, ValueError):
            return None

    def _cap(x: Any, n: int) -> Any:
        return x[:n] if isinstance(x, list) else x

    findings = []
    for f in rep.get("findings") or []:
        if not isinstance(f, dict) or len(findings) >= 20:
            continue
        if scrub.names(f.get("claim")) or scrub.names(f.get("why")):
            continue                                     # a claim about a withheld column: never sent
        d = {"verdict": str(f.get("verdict") or ""), "kind": str(f.get("kind") or ""),
             "claim": safe(f.get("claim"), 400)}
        for k in ("value", "interval", "p_value", "power"):
            v = _num(f.get(k))
            if v is not None:
                d[k] = v
        if f.get("why"):
            d["why"] = safe(f["why"], 240)
        if f.get("layout_artifact") is True:
            d["layout_artifact"] = True          # a row count the table's layout fixes: no number, no grade for the reader
            d.pop("value", None)
        cov = ((f.get("effect") or {}).get("coverage")) if isinstance(f.get("effect"), dict) else None
        if isinstance(cov, dict) and _num(cov.get("measured")) is not None:
            # T2: the interval's measured coverage (the numbers the why quotes), for the writer and its guard
            d["interval_coverage"] = {"measured": _num(cov.get("measured")), "lo": _num(cov.get("lo")),
                                      "hi": _num(cov.get("hi"))}
        findings.append(d)

    analyses = []
    charts = []
    tables = []
    links: List[Tuple[Optional[int], Optional[int]]] = []     # each analysis's chart and table in charts / tables
    for a in _cap((rep.get("ai_analyses") or {}).get("items") or [], 8):
        if not isinstance(a, dict):
            continue
        d: Dict[str, Any] = {"title": safe(a.get("title"), 160),
             "sentence": _cut_words(safe(a.get("sentence"), 4000), ANALYSIS_TEXT_MAX["sentence"]),
             "method": _cut_words(safe(a.get("method"), 4000), ANALYSIS_TEXT_MAX["method"])}
        tt = _trend_test_for_ai(a.get("test"), safe)
        if tt:
            d["test"] = tt          # T1: the test's own record (the method text above is cut where the worker cuts it)
        t = a.get("table") or {}
        rows = t.get("rows") or []
        tab = None
        if isinstance(rows, list) and rows:
            cols = [safe(c, 60) for c in (t.get("cols") or [])][:6]
            tab = {"cols": cols, "rows": [[safe(v, 80) for v in r][:len(cols)] for r in rows[:12]]}
        analyses.append(d)
        link_chart: Optional[int] = None
        # the chart and the numbers behind the report: the writer may place a chart marker
        # [CHART:n] (the n-th chart here) and a table marker [TABLE:n] beside the finding it
        # belongs to; the page and the shared viewer draw both from this list, engine-computed
        ch = a.get("chart")
        if isinstance(ch, dict) and len(charts) < 6:
            c2: Dict[str, Any] = {"kind": str(ch.get("kind") or "")[:10],
                  "title": safe(a.get("title"), 160)}
            for k in ("x_name", "y_name", "x_label", "unit"):
                if ch.get(k):
                    c2[k] = safe(ch[k], 60)
            if isinstance(ch.get("series"), list):
                # a line chart's series are its lines (at most LEGACY_LINES_MAX drawn); a bar chart's are its bars,
                # every one up to LEGACY_BARS_MAX (final evaluation, 1 Oct 2026: one cap of 4 for both sent 4 of the
                # FX histogram's 12 bins, and dropped the lowest department the text beside the chart named)
                cap = LEGACY_BARS_MAX if str(ch.get("kind") or "") == "bars" else LEGACY_LINES_MAX
                ser = []
                for s in ch["series"]:
                    if len(ser) >= cap:
                        break
                    if isinstance(s, dict) and isinstance(s.get("x"), list) and isinstance(s.get("y"), list):
                        n = min(len(s["x"]), len(s["y"]), 60)
                        ser.append({"name": safe(s.get("name") or s.get("label") or "", 80),
                                    "x": [float(v) if isinstance(v, (int, float)) else 0.0 for v in s["x"][:n]],
                                    "y": [float(v) if isinstance(v, (int, float)) else 0.0 for v in s["y"][:n]]})
                    elif isinstance(s, dict) and isinstance(s.get("value"), (int, float)):
                        ser.append({"label": safe(s.get("label") or "", 80), "value": float(s["value"])})
                if ser:
                    c2["series"] = ser
            if isinstance(ch.get("points"), list):
                pts = []
                for p in ch["points"][:LEGACY_POINTS_MAX]:
                    if isinstance(p, (list, tuple)) and len(p) >= 2 and all(isinstance(v, (int, float)) for v in p[:2]):
                        pts.append([float(p[0]), float(p[1])])
                if pts:
                    c2["points"] = pts
            if isinstance(ch.get("fits"), list):
                fits = []
                for f in ch["fits"][:4]:
                    if isinstance(f, dict) and all(isinstance(f.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")):
                        fits.append({"name": safe(f.get("name") or "", 80), "recent": bool(f.get("recent")),
                                     "x0": float(f["x0"]), "y0": float(f["y0"]), "x1": float(f["x1"]), "y1": float(f["y1"])})
                if fits:
                    c2["fits"] = fits
            if c2.get("series") or c2.get("points"):
                link_chart = len(charts)
                charts.append(c2)
        # the econometrics numbers the writer may quote from the analysis tables: every figure in a
        # table row is an engine figure, so it must be in the payload for the guard to accept. Sent once, in
        # "tables" under the analysis's own title (final review, 30 Sep 2026: each table went twice, as
        # analyses[].table and in tables[]; the worker reads tables[] for [TABLE:n] and for the figures it accepts,
        # and needs no analyses[].table)
        link_table: Optional[int] = None
        if tab is not None:
            link_table = len(tables)
            tables.append({"title": d["title"], "cols": tab["cols"], "rows": tab["rows"]})
        links.append((link_chart, link_table))

    # the charts chosen from the data (rep.viz, engine/nl_viz.py; CONTRACT §5.9), after the analyses' charts: each
    # record whole (the worker makes the model's card from it, never showing the model its data, and returns it
    # validated for the page, the PDF and the share viewer), at most AI_CHARTS_MAX charts in all, as it may leave the
    # adapter (nl_viz.for_sending: with a suppressed cell, no exact total a hidden figure could be worked back from; a
    # word that names a withheld column reads "[withheld column]", and a record that then breaks a cap is not sent)
    try:
        import nl_viz as _nv
        charts_max = _nv.AI_CHARTS_MAX
    except ImportError:
        _nv, charts_max = None, 10
    for rec in ((rep.get("viz") or {}).get("charts") or []) if _nv is not None else []:
        if len(charts) >= charts_max:
            break
        if not _nv.is_record(rec):
            continue
        sent = _nv.for_sending(rec, scrub)
        if sent is not None:
            charts.append(sent)

    # the scenario and contribution block (engine/nl_scenarios.py), 1:1: every key, every figure and every text as
    # the report holds them, its words made safe as every other text here; and ONE table, "What drove the change
    # in <measure>", a row per segment whose cells are those items' own texts, after the analyses' tables (their
    # [TABLE:n] places stay where they were)
    scenarios = _scenarios_for_ai(rep.get("scenarios"), safe)

    def drove_of(sc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            import nl_scenarios as _ns
            dr = _ns.table(sc)
        except ImportError:
            dr = None
        if dr is None:
            return None
        return {"title": safe(dr["title"], 160), "cols": [safe(c, 60) for c in dr["cols"]][:6],
                "rows": [[safe(v, 80) for v in r][:6] for r in dr["rows"][:12]]}
    n_tables = len(tables)                   # the analyses' tables; the "What drove the change" table follows them
    drove = drove_of(scenarios)
    if drove is not None:
        tables.append(drove)

    clean = rep.get("cleaning") or {}
    health = rep.get("health") or {}
    plan = rep.get("ai_plan") or {}
    applied = [safe(x, 200) for x in _cap(plan.get("applied") or [], 8)]
    # what the data tests found and what the engine did with those rows, in the Data tests card's own numbers,
    # so the writer quotes those and no other count for the same cells. The worker keeps 8 lines: the plan's
    # own steps first ("37 rows left" is a figure the writer may quote), the test lines fill what is left. A
    # withheld column's test is never sent. A note row (a level's placeholder zeros) goes with them.
    tested = []
    for t in (rep.get("contracts") or {}).get("tests") or []:
        if isinstance(t, dict) and (t.get("failed") or t.get("note")) and t.get("brief") and t.get("private") != "withhold":
            tested.append(safe("data test on %s (%s): %s" % (t.get("column"), t.get("semantic_type"), t.get("brief")), 200))
    applied = applied + tested[:max(0, 8 - len(applied))]
    story = dict(rep.get("story") or {})     # a copy: distilling the payload never changes the report itself
    # the writer needs a goal. With no AI plan (rules-only run, or the planner did not answer),
    # the engine's own headline stands in: it is the report's primary claim, checked by the engine.
    goal = safe(str(plan.get("goal") or (rep.get("objective") or "")).strip(), 600)
    if not goal and isinstance(story.get("headline"), str):
        goal = safe(story["headline"], 300)
    if not goal:
        goal = "What does this file say, and what should be done about it?"
    # the story distilled for the writer, from the engine's own story
    st = story
    for k in ("headline", "what_happened", "why", "what_to_do", "whats_next", "cannot_answer"):
        v = st.get(k)
        if isinstance(v, list):
            story[k] = [safe(x, 300) for x in _cap(v, 8)]
        elif v:
            story[k] = safe(v, 300)

    # the forecast as the proxy's validateResults reads it (insight-proxy/src/report.js): plain strings where
    # it takes strings, a real boolean for baseline_won (the string "True" read as false there, so the writer
    # was always told the baseline lost), each point's month as "date". Live bug, 29 Sep 2026.
    fc = {}
    f = rep.get("forecast") or {}
    if not f.get("available") and f.get("row_forecast_dropped"):
        # P0-13: no forecast of a row count the table's layout (or the calendar) fixes, and why: one line, never a number
        fc["row_forecast_dropped"] = True
        if f.get("reason"):
            fc["reason"] = safe(f["reason"], 200)
    if not f.get("available") and f.get("frequency"):
        # wave 5, gap 4: no forecast and no audit of a quarterly or an annual table, and why: one line, never a number
        fc["frequency"] = str(f["frequency"])[:20]
        if f.get("reason"):
            fc["reason"] = safe(f["reason"], 240)
    if f.get("available"):
        fc["available"] = True
        if f.get("label"):
            fc["series"] = safe(f["label"], 200)
        g = GRADE.get(str(f.get("verdict") or ""))
        if g:
            fc["verdict"] = _FC_GRADE_WORDS.get(g, _grade_plain(g))
        if f.get("champion"):
            fc["champion"] = str(f["champion"])[:120]
        cov = f.get("coverage") if isinstance(f.get("coverage"), dict) else {}
        hits, n = cov.get("hits"), cov.get("n")
        lvl = _num((f.get("band") or {}).get("level")) if isinstance(f.get("band"), dict) else None
        has_audit = isinstance(f.get("audit"), dict) and bool(f["audit"].get("horizons"))
        # with an audit the report states ONE count of how the range held: the audit's (wave 4, track B step 0)
        if isinstance(hits, (int, float)) and isinstance(n, (int, float)) and n and not has_audit:
            fc["coverage"] = ("%d of %d replayed months inside the %s range" % (
                int(hits), int(n), ("%s%%" % _fmt(100.0 * lvl if lvl <= 1 else lvl)) if lvl else "forecast"))[:120]
        if isinstance(f.get("baseline_won"), bool):
            fc["baseline_won"] = f["baseline_won"]
        if f.get("reason"):
            fc["reason"] = safe(f["reason"], 200)
        au = f.get("audit") if isinstance(f.get("audit"), dict) else None
        if au and au.get("horizons"):
            # P0-13: the back-test of the range shown, and whether the engine's grade is trusted
            fc["audit"] = {"label": safe(au.get("label"), 200), "status": str(au.get("status") or ""),
                           "trusted": bool(au.get("trusted")), "grade_label": safe(au.get("grade_label"), 120)}
        fwd = f.get("forecast") or []
        if isinstance(fwd, list) and fwd:
            # the writer must be able to QUOTE a forecast figure: the engine's full floats
            # (8973.866344820577) are not quotable prose, so the payload carries the same
            # display rounding the engine's own story uses (live finding, 28 Sep 2026: the
            # report shipped raw floats when the scenario rules asked for base/low/high).
            def _r1(x: Any) -> Any:
                v = _num(x)
                return None if v is None else (int(round(v)) if abs(v) >= 1000 else round(v, 1))
            fc["points"] = [{"date": str(x.get("month") or x.get("date") or "")[:20],
                             "value": _r1(x.get("value")), "lo": _r1(x.get("lo")), "hi": _r1(x.get("hi"))}
                            for x in fwd[:14] if isinstance(x, dict)]

    # the flagged columns the visitor kept (and agreed, on the page, to send): the writer is told once, in the
    # reading (the proxy keeps only the keys it knows, and the reading has room where the limitations may not),
    # by name only; a kept column is not withheld, so its name is never scrubbed
    kept = [str(f.get("column")) for f in (rep.get("privacy") or {}).get("flagged") or []
            if isinstance(f, dict) and f.get("decision") == "keep" and f.get("column")]
    # the health issues: never the engine's "numbers are stored as text" line (every upload is CSV text, see
    # _TEXT_NUMBERS_RE; left out here too for a report saved before it was); when that mark-down lowers the score the
    # writer is sent (health_score, the weakest dimension), the writer is told so first, in the page's words
    tn = health.get("csv_text_numbers") if isinstance(health.get("csv_text_numbers"), dict) else {}
    h_issues = [x for x in health.get("issues") or [] if not scrub.names(x) and not _is_text_numbers_line(x)]
    if tn.get("note") and (tn.get("lowers") or {}).get("score_min"):
        h_issues = [TEXT_NUMBERS_AI] + h_issues
    elif health.get("explain") and _health_gap(health) >= HEALTH_EXPLAIN_GAP:
        # what set a score far under the other checks, first (_health_explain: it names no column)
        h_issues = [_cut_words(HEALTH_EXPLAIN_AI % health["explain"], 200)] + h_issues
    # the steps of the plan that set rows aside (_row_drops): the notice of one that set aside PLAN_DROP_NOTICE_PCT or
    # more of the rows leads the limitations. The guard reads a limitation's figures as the engine's, so a reason that
    # carries a figure of the planner's own is left to the risks the plan named (sent as quality_risks)
    drops = [d for d in plan.get("row_drops") or [] if isinstance(d, dict) and isinstance(d.get("rows"), int)]

    def writer_notice(d: Dict[str, Any]) -> str:
        # within the worker's 240 characters: the count and share, the engine's check, then the plan's reason, cut at a
        # word (the whole reason is among the risks the plan named)
        if not d.get("notice"):
            return ""
        n = "The AI plan set aside %s rows (%s)" % (format(int(d["rows"]), ","), _pct_text(float(d.get("pct") or 0)))
        if d.get("check"):
            n += "; the engine checked: %s" % d["check"]
        reason = str(d.get("reason") or "").strip()
        n += (". Its reason: " + reason) if reason and not re.search(r"\d", reason) else \
            ". Its reason is among the risks the plan named." if reason else ". The plan gave no reason."
        return _cut_words(safe(n, 4 * LIMITATION_MAX), LIMITATION_MAX)
    drop_notes = [x for x in (writer_notice(d) for d in drops) if x]
    # the reading at most 800 characters (the worker's cap), cut at a word with an ellipsis, never mid-word
    reading = _cut_words(safe(plan.get("understanding") or "", 4000), 800)
    if kept:
        told = (OPTED_IN % ", ".join(kept))[:400]
        reading = (_cut_words(reading, max(0, 799 - len(told))).rstrip() + " " + told).strip()

    # the applied steps carry real figures (rows left after a filter, series count after a
    # reshape): the writer may quote them, so they must be in the payload as findings of the run
    out = {
        "ok": True,
        # what the headline is, first (a table read by its structure: CONTRACT §1, §5.10), then the structure's summary
        "estimand": _estimand_for_ai(rep.get("estimand"), safe),
        "structure": _structure_for_ai(rep.get("structure"), safe),
        "input": {"name": (rep.get("input") or {}).get("name"),
                  "rows": (rep.get("input") or {}).get("rows"),
                  "columns": (rep.get("input") or {}).get("columns")},
        "goal": goal,
        "reading": reading,
        "quality_risks": [safe(x, 240) for x in _cap(plan.get("quality_risks") or [], 6)],
        "plan_applied": applied,
        "plan_refused": [safe(x, 160) for x in _cap(plan.get("refused") or [], 6)],
        "findings": findings,
        "analyses": analyses,
        "charts": charts,
        "tables": tables,
        "analyses_refused": [safe(x, 160) for x in _cap((rep.get("ai_analyses") or {}).get("refused") or [], 6)],
        "story": story,
        "forecast": fc,
        "health_score": _num(health.get("score")),
        # what set the score (health.explain), for the PDF's health tile; the writer reads it in health_issues
        "health_explain": safe(health.get("explain") or "", 400),
        "health_issues": [safe(x, 200) for x in h_issues][:6],
        # each step of the plan that set rows aside, whole, for the PDF's data section and notice (the worker keeps only
        # the keys it knows: the writer reads the steps in plan_applied and the notice among the limitations)
        "plan_row_drops": [{"rows": int(d["rows"]), "of": int(d.get("of") or 0), "pct": float(d.get("pct") or 0),
                            "text": safe(d.get("text") or "", 1200), "notice": safe(d.get("notice") or "", 800)}
                           for d in drops[:8]],
        "cleaning": {"rows_in": clean.get("rows_in"), "rows_clean": clean.get("rows_clean"),
                     "rows_quarantined": clean.get("rows_quarantined"),
                     "fixes": [safe(x.get("what") or x, 160) for x in clean.get("fixes") or []
                               if isinstance(x, dict) and not scrub.names(x.get("what")) and not scrub.names(x.get("column"))][:6]},
        # cut to LIMITATION_MAX only after the file's name is swapped for FILE_WORD (final, below)
        "limitations": (drop_notes + [safe(x.get("text") or x, 4 * LIMITATION_MAX) for x in rep.get("limitations") or []
                                      if isinstance(x, dict) and not scrub.names(x.get("text"))])[:6],
        "scenarios": scenarios,
        # the claim the report leads with ({id, claim, grade} or null): the PDF's key figures read it exactly
        "primary": _primary_for_ai(rep, safe, scrub),
    }
    pv = _privacy_for_ai(rep.get("privacy"), safe, scrub)
    if pv:
        out["privacy"] = pv        # what was read as a category, for the report's method section (the worker drops it: the AI never reads it)
    # never the file's name (final review, 29 Sep 2026: the engine's sentences name it, "22 values in the amount
    # column of private_mix.csv could not be read", and every one went to /report): FILE_WORD stands in, as in
    # the planner's profile; the page puts the name back only in what it shows the visitor (src/js/50-try.js)
    fname = str((rep.get("input") or {}).get("name") or "")

    def final(o: Dict[str, Any]) -> Dict[str, Any]:
        o = _swap_text(o, fname, FILE_WORD) if fname else o
        # a name shorter than the placeholder makes a text longer: each analysis's sentence and method are cut again
        ana = [dict(a, **{k: _cut_words(a[k], n) for k, n in ANALYSIS_TEXT_MAX.items() if isinstance(a.get(k), str)})
               for a in o.get("analyses") or []]
        return dict(o, analyses=ana, limitations=[_cut_text(x, LIMITATION_MAX) for x in o.get("limitations") or []])
    return _within_budget(out, final, drove_of, n_tables, links)


# The analyses' own charts as results_for_ai sends them (final evaluation, 1 Oct 2026): a line chart's series are its
# lines, at most LEGACY_LINES_MAX; a bar chart's series are its bars, at most LEGACY_BARS_MAX (the analyses draw at most
# 12: a distribution's bins, a compare's groups, the themes' words); a scatter's points, a sample of LEGACY_POINTS_MAX.
# (insight-proxy/src/charts.js sanitizeLegacyChart keeps the same caps since the integration pass of 1 Oct 2026.)
LEGACY_LINES_MAX = 4
LEGACY_BARS_MAX = 24
LEGACY_POINTS_MAX = 120

# Each limitation the report writer receives, in characters: the worker's cap (insight-proxy/src/report.js
# validateResults, limitations 240). Cut after the file's name is swapped for FILE_WORD: cut before, a name shorter
# than the placeholder made the line longer than the cap (the contract test of 30 Sep 2026: "f3_eur.csv" is 10
# characters, "[your file]" 11, and two limitations arrived at 241).
LIMITATION_MAX = 240


def _cut_text(s: str, n: int) -> str:
    """s at most n characters, never cut inside the FILE_WORD placeholder (a cut through one ends before it)."""
    if len(s) <= n:
        return s
    j = s.find(FILE_WORD, max(0, n - len(FILE_WORD) + 1))
    return s[:j].rstrip() if 0 <= j < n else s[:n]


# An analysis's sentence and method, in characters: the worker's caps (insight-proxy/src/report.js validateResults:
# sentence 700, method 300), which cut a longer text where the count ends. The adapter sends each whole, or cut at a
# word with an ellipsis (_cut_words), so the worker never has to cut one (review of the live run, 30 Sep 2026: a
# method note reached the report as "...each of the last 4 predicted by a model trained only on the blocks befor").
ANALYSIS_TEXT_MAX = {"sentence": 700, "method": 300}


def _cut_words(s: str, n: int) -> str:
    """s at most n characters: whole, or cut after the last whole word that fits, with an ellipsis ("\u2026"); never
    inside a word or a figure, and never inside the FILE_WORD placeholder. Only a text with no space before the limit is
    cut where the count ends."""
    if not isinstance(s, str) or len(s) <= n:
        return s
    head = s[:n - 1]
    k = head.rfind(" ")
    if k > 0:
        head = head[:k]
    o = head.rfind("[")
    if o >= 0 and "]" not in head[o:] and s.startswith(FILE_WORD, o):
        head = head[:o]
    return head.rstrip(" ,;:(\u2212-") + "\u2026"


# The adapter's byte budget for results_for_ai (30 Sep 2026): the worker takes a /report body of at most 96,000 bytes
# (insight-proxy/src/report.js REPORT_MAX_BYTES), and that body holds these results with the visitor's question and
# the planner's searches. Past RESULTS_MAX_BYTES, scenario items are dropped from the tail of the worker's own
# priority (nl_scenarios._ordered): the facts first, then the per-segment detail beyond the TOP_SEGMENTS segments with
# the largest contributions, the smallest segment's last item first; only if that is not enough do the core's
# segments shrink (the sixth, then the fifth, ...) and, with no segment left, the groups go from the end of the order.
# Never an oversize payload (final review, 30 Sep 2026): with every scenario item gone and the payload still over, the
# analyses go from the last in the plan's order (each with its chart and its table), and analyses_refused says so
# first; past that (a payload the caps above cannot make), the charts, the tables and the findings from the end.
# The size is json.dumps's with its \u escapes, never less than the UTF-8 bytes the page sends.
RESULTS_MAX_BYTES = 90000
BUDGET_REFUSED = ("%s of the %s scenario items are left out to keep what the report writer receives under %s bytes: "
                  "the facts first, then the parts with the smallest contributions; a part that moved against the "
                  "change and the unallocated part are kept to the end")
BUDGET_ANALYSES = ("%s of the %s analyses are left out to keep what the report writer receives under %s bytes: the "
                   "last ones in the plan's order")


def _payload_bytes(x: Any) -> int:
    """The size of x as JSON (ASCII, \\u escapes: at least the UTF-8 bytes of the same JSON)."""
    return len(json.dumps(x, default=str))


BUDGET_TOP_PARTS = 6            # the parts of each breakdown with the largest |contribution|, kept to the end (the worker's top 6)
_BUDGET_PARTS_OF = ("contribution", "price_volume_mix")      # the groups that say where the change sits


def _sign(v: Any) -> int:
    return 0 if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v == 0 \
        else (1 if v > 0 else -1)


def _budget_drop_order(items: List[Dict[str, Any]], basis: Optional[Dict[str, Any]] = None) -> List[int]:
    """The scenario items' indices in the order the byte budget drops them (RESULTS_MAX_BYTES), first to go first.

    Wave 4, track B step 0 (6 Oct 2026): the retail report left out the Northwest Territories, the only province that
    fell, because it was the smallest part. The priority is now (1) the facts; (2) the items of the parts outside each
    breakdown's BUDGET_TOP_PARTS largest |contribution| that did not move against the headline change, the smallest
    part first and, inside a part, its last item first; (3) the items of the largest parts (and the share items of a
    part that moved against the change), the smallest first; (4) the other groups, the headline's last; (5) an item that
    moved AGAINST the headline change (a contribution, or the price, volume or mix effect, of the opposite sign: the
    reader must learn what pulled the other way); (6) the unallocated part (the total less its published parts), which
    is what makes a breakdown add up to the change, last. A breakdown is a table's own dimension (basis.breakdowns,
    the id's second word) or, with none, the one segment column."""
    import nl_scenarios as _ns
    per = set(_ns.PER_SEGMENT)
    groups = {g: i for i, g in enumerate(_ns.GROUPS)}
    idx = list(range(len(items)))
    keys = {str(b.get("key")) for b in (basis or {}).get("breakdowns") or [] if isinstance(b, dict) and b.get("key")}
    head = next((it for it in items if it.get("id") == "headline.change"), None)
    hs = _sign(head.get("value")) if head else 0

    def is_unalloc(i: int) -> bool:
        return str(items[i].get("id") or "").endswith(".unallocated")

    def bkey(i: int) -> str:
        parts = str(items[i].get("id") or "").split(".")
        return parts[1] if len(parts) > 2 and parts[1] in keys else ""

    def of_part(i: int) -> bool:
        s = items[i].get("segment")
        return items[i].get("group") in per and isinstance(s, str) and not is_unalloc(i)
    size: Dict[Tuple[str, str], float] = {}
    sign: Dict[Tuple[str, str], int] = {}
    for i in idx:
        it = items[i]
        if of_part(i) and it.get("group") == "contribution" and it.get("kind") == "change" and it.get("unit") != "%" \
                and isinstance(it.get("value"), (int, float)) and not isinstance(it.get("value"), bool):
            k = (bkey(i), it["segment"])
            size[k] = max(size.get(k, 0.0), abs(float(it["value"])))
            sign[k] = _sign(it["value"])
    rank: Dict[Tuple[str, str], int] = {}
    for b in sorted({k[0] for k in size}):
        for r, k in enumerate(sorted((k for k in size if k[0] == b), key=lambda k: (-size[k], k[1]))):
            rank[k] = r

    def part(i: int) -> Optional[Tuple[str, str]]:
        return (bkey(i), items[i]["segment"]) if of_part(i) else None

    def against(i: int) -> bool:
        """an item that moved the other way than the headline change: a contribution or a growth, an effect of the
        change's own sign (kind change), not a share of a level"""
        it = items[i]
        if not hs or it.get("kind") != "change" or it.get("group") not in _BUDGET_PARTS_OF or is_unalloc(i):
            return False
        return _sign(it.get("value")) == -hs

    def part_against(k: Optional[Tuple[str, str]]) -> bool:
        return k is not None and bool(hs) and sign.get(k, 0) == -hs
    facts = [i for i in reversed(idx) if items[i].get("group") == "facts"]
    keep_last = [i for i in idx if is_unalloc(i)]
    protected = [i for i in idx if against(i) and i not in keep_last]
    taken = set(facts) | set(keep_last) | set(protected)

    def smallest_first(cands: List[int]) -> List[int]:
        # the part with the smallest |contribution| first (a tie: the breakdown, then the name); inside a part its last
        # item first (the order the items were built in)
        return sorted(cands, key=lambda i: (size.get(part(i), 0.0), part(i) or ("", ""), -i))
    parts_i = [i for i in idx if of_part(i) and i not in taken]
    beyond = smallest_first([i for i in parts_i if rank.get(part(i), 0) >= BUDGET_TOP_PARTS and not part_against(part(i))])
    top = smallest_first([i for i in parts_i if i not in set(beyond)])
    taken |= set(beyond) | set(top)
    rest = sorted((i for i in idx if i not in taken), key=lambda i: (-groups.get(items[i].get("group"), 0), -i))
    prot = smallest_first(protected) if all(of_part(i) for i in protected) else \
        sorted(protected, key=lambda i: (-groups.get(items[i].get("group"), 0), -i))
    return facts + beyond + top + rest + prot + sorted(keep_last, reverse=True)


def _within_budget(out: Dict[str, Any], final: Any, drove_of: Any, n_tables: int,
                   links: Optional[List[Tuple[Optional[int], Optional[int]]]] = None) -> Dict[str, Any]:
    """final(out), at most RESULTS_MAX_BYTES: scenario items dropped (_budget_drop_order) until it fits, the "Where the
    change came from" table rebuilt from the items kept and scenarios.refused saying first how many were left out;
    then, if it is still over, the charts chosen from the data (rep.viz records) from the last; then the analyses
    from the last (with the chart and table each one has: links, their
    indices in charts and tables) and analyses_refused saying so first; then the charts, the tables and the findings
    from the end. Never returns a payload over the budget."""
    done = final(out)
    size = _payload_bytes(done)
    if size <= RESULTS_MAX_BYTES:
        return done
    sc = out.get("scenarios") if isinstance(out.get("scenarios"), dict) else None
    items = list((sc or {}).get("items") or [])
    if items:
        order = _budget_drop_order(items, sc.get("basis") if isinstance(sc.get("basis"), dict) else None)
        base_out = out

        def without(k: int) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
            """(out, final(out), its size) with the first k items of the drop order left out, the "Where the change
            came from" table rebuilt from the items kept."""
            gone = set(order[:k])
            kept = [it for i, it in enumerate(items) if i not in gone]
            sc2 = dict(sc, items=kept, refused=[BUDGET_REFUSED % (format(len(gone), ","), format(len(items), ","),
                                                                  format(RESULTS_MAX_BYTES, ","))]
                       + list(sc.get("refused") or []))
            o2 = dict(base_out, scenarios=sc2)
            dr = drove_of(sc2)
            o2["tables"] = list(base_out.get("tables") or [])[:n_tables] + ([dr] if dr is not None else [])
            d2 = final(o2)
            return o2, d2, _payload_bytes(d2)
        j = 0
        while j < len(order):
            while j < len(order) and size > RESULTS_MAX_BYTES:
                size -= _payload_bytes(items[order[j]]) + 2          # the item and its ", "
                j += 1
            out, done, size = without(j)
            if size <= RESULTS_MAX_BYTES:
                # no more than it needs: the estimate leaves out the table's rows, so a segment whose items all went
                # took its row too; the items dropped last come back while the payload still fits
                while j > 0:
                    o3, d3, s3 = without(j - 1)
                    if s3 > RESULTS_MAX_BYTES:
                        break
                    j, out, done, size = j - 1, o3, d3, s3
                return done
    # every scenario item is gone and it is still over: the charts chosen from the data (rep.viz), from the last,
    # before any analysis (CONTRACT §5.9; they follow the analyses' own charts, whose places the links keep)
    try:
        import nl_viz as _nv
        is_viz = _nv.is_record
    except ImportError:
        is_viz = None
    while is_viz is not None and size > RESULTS_MAX_BYTES and out.get("charts") and is_viz(list(out["charts"])[-1]):
        out = dict(out, charts=list(out["charts"])[:-1])
        done = final(out)
        size = _payload_bytes(done)
    if size <= RESULTS_MAX_BYTES:
        return done
    # and still over: the analyses from the last in the plan's order
    ana = list(out.get("analyses") or [])
    links = list(links or [])[:len(ana)]
    links += [(None, None)] * (len(ana) - len(links))
    n_ana = len(ana)
    while size > RESULTS_MAX_BYTES and ana:
        ana.pop()
        ch, tb = links.pop()
        charts = list(out.get("charts") or [])
        tables = list(out.get("tables") or [])
        if ch is not None and ch < len(charts):
            charts.pop(ch)
        if tb is not None and tb < len(tables):
            tables.pop(tb)
        note = BUDGET_ANALYSES % (format(n_ana - len(ana), ","), format(n_ana, ","), format(RESULTS_MAX_BYTES, ","))
        refused = [x for x in out.get("analyses_refused") or [] if not str(x).endswith("the last ones in the plan's order")]
        out = dict(out, analyses=ana, charts=charts, tables=tables, analyses_refused=[note] + refused)
        done = final(out)
        size = _payload_bytes(done)
    # past what the caps above allow: the charts, the tables and the findings, each from the end
    for key in ("charts", "tables", "findings"):
        while size > RESULTS_MAX_BYTES and out.get(key):
            out = dict(out, **{key: list(out[key])[:-1]})
            done = final(out)
            size = _payload_bytes(done)
    if size > RESULTS_MAX_BYTES:
        # never sent oversize, and never silently: the worker refuses a result that says ok false, and says why
        return {"ok": False, "error": "the results are %s bytes, over the report writer's %s-byte budget" % (
            format(size, ","), format(RESULTS_MAX_BYTES, ","))}
    return done


def _strings(x: Any) -> List[str]:
    """Every string value in x (not its keys), at any depth."""
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    return []


def _swap_text(x: Any, old: str, new: str) -> Any:
    """x with every `old` in every string (at any depth) read as `new`."""
    if isinstance(x, str):
        return x.replace(old, new)
    if isinstance(x, list):
        return [_swap_text(v, old, new) for v in x]
    if isinstance(x, dict):
        return {k: _swap_text(v, old, new) for k, v in x.items()}
    return x


def results_json(rep: Any) -> str:
    """results_for_ai as JSON text (what the page POSTs to the worker's /report)."""
    return json.dumps(results_for_ai(rep), allow_nan=False, default=str)


def _clean_plan_review(raw: Any) -> Optional[Dict[str, Any]]:
    """The visitor's review of the AI plan, kept as a record only: fixed keys, safe types, short."""
    if not isinstance(raw, dict):
        return None
    strs = lambda v: [str(x)[:80] for x in v[:20] if isinstance(x, str)] if isinstance(v, list) else []
    return {"approved": raw.get("approved") is True, "goal_edited": raw.get("goal_edited") is True,
            "ops_removed": strs(raw.get("ops_removed")), "analyses_removed": strs(raw.get("analyses_removed")),
            "at": str(raw.get("at") or "")[:40]}


def _validate_plan(plan: Any, columns: List[str], S: Optional[Dict[str, Any]] = None
                   ) -> Tuple[Dict[str, Any], List[str]]:
    """The plan cut down to what is valid, and the reasons for each part refused. With a statistical table's structure
    (nl_structure, the profile's structure block), the plan may name a slice id ("S1"), up to 4 breakdown ids ("B1") and
    a momentum slice: kept only when the structure holds them; with a slice id, a row filter on a structure dimension is
    ignored, with a note (the slice chooses the rows)."""
    refused: List[str] = []
    if not isinstance(plan, dict):
        return {}, ["the plan is not an object"]
    have = set(columns)
    # the goal and the reading cut at a word with an ellipsis (final evaluation, 1 Oct 2026: the plan card and the
    # PDF read "...every other column is a fixed code with one dist")
    out: Dict[str, Any] = {k: _cut_words(str(plan.get(k) or ""), 600) for k in ("goal", "understanding")}
    out["kind"] = str(plan.get("kind") or "")[:600]
    out["goal_candidates"] = [str(x)[:200] for x in (plan.get("goal_candidates") or [])[:3] if str(x).strip()]
    out["quality_risks"] = [str(x)[:240] for x in (plan.get("quality_risks") or [])[:6]]
    out["columns"] = []
    for c in (plan.get("columns") or [])[:200]:
        if isinstance(c, dict) and c.get("name") in have:
            t = str(c.get("semantic_type") or "other")
            out["columns"].append({"name": c["name"], "semantic_type": t if t in SEMANTIC_TYPES else "other",
                                   "role": str(c.get("role") or "")[:20], "unit": str(c.get("unit") or "")[:40],
                                   "why": str(c.get("why") or "")[:200]})
            if isinstance(c.get("label"), str) and c["label"].strip():    # the measure in words: "USD/CAD rate"
                out["columns"][-1]["label"] = " ".join(c["label"].split())[:80]
            if isinstance(c.get("values"), list):     # a category's profile values, for its contract test
                out["columns"][-1]["values"] = [str(x)[:60] for x in c["values"][:300]]
    ops = []
    for op in (plan.get("operations") or [])[:12]:
        if not isinstance(op, dict) or op.get("op") not in PLAN_OPS:
            refused.append("an operation outside the menu (%s)" % str((op or {}).get("op") if isinstance(op, dict) else op)[:40])
            continue
        names = [op.get("column")] if op.get("column") else list(op.get("columns") or [])
        missing = [n for n in names if n not in have]
        if missing:
            refused.append("%s names columns the file lacks: %s" % (op["op"], ", ".join(map(str, missing))[:120]))
            continue
        if op["op"] in ("exclude_rows", "keep_rows") and not (op.get("column") and op.get("values")):
            refused.append("%s needs a column and values" % op["op"])
            continue
        ops.append({k: op[k] for k in ("op", "column", "columns", "values") if k in op})
    out["operations"] = ops
    # analyses name series that may exist only after long_to_wide (GCAG from Source), so their names are
    # checked when they run, against the file as the engine read it
    ans = []
    for a in (plan.get("analyses") or [])[:12]:
        if not isinstance(a, dict) or a.get("type") not in ANALYSIS_TYPES:
            refused.append("an analysis outside the menu (%s)" % str((a or {}).get("type") if isinstance(a, dict) else a)[:40])
            continue
        if len(ans) >= ANALYSES_MAX:
            refused.append("more than %d analyses; the rest were not run" % ANALYSES_MAX)
            break
        cols = [str(c)[:120] for c in (a.get("columns") or []) if isinstance(c, (str, int, float))][:8]
        item = {"type": a["type"], "columns": cols, "why": str(a.get("why") or "")[:200]}
        if isinstance(a.get("by"), str) and a["by"] in have:
            item["by"] = a["by"]
        ans.append(item)
    out["analyses"] = ans
    # the charts the AI chose (plan.charts): read and cut here, checked by the engine when it builds them
    # (nl_viz.validate_directive); a refused chart is never a plan signal, so nothing goes to `refused`
    import nl_viz as _nv
    out["charts"] = _nv.plan_items(plan.get("charts"))
    prim = plan.get("primary")
    out["primary"] = prim if prim in have else ""
    if prim and prim not in have:
        refused.append("the headline measure %s is not a column" % str(prim)[:60])
    if S is not None and S.get("usable"):
        import nl_structure as _nst
        sids = {x["id"] for x in S.get("slices") or []}
        bids = {x["id"] for x in S.get("breakdowns") or []}
        sl = plan.get("slice")
        if isinstance(sl, str) and _nst.SLICE_ID.match(sl) and sl in sids:
            out["slice"] = sl
        elif sl not in (None, ""):
            refused.append("the slice %s is not one of the table's slices (%s)" % (str(sl)[:12], ", ".join(sorted(sids))))
        ms = plan.get("momentum_slice")
        if isinstance(ms, str) and _nst.SLICE_ID.match(ms) and ms in sids:
            out["momentum_slice"] = ms
        mm = plan.get("measure_member")
        mdim = next((d for d in S.get("dims") or [] if d.get("measure_dim")), None)
        if mm not in (None, ""):
            ids = {x["id"]: x for x in (mdim or {}).get("measures") or []}
            if mdim is None:
                refused.append("measure_member %s was ignored: the table has no dimension of measures" % str(mm)[:12])
            elif not (isinstance(mm, str) and _nst._MEASURE_ID.match(mm) and mm in ids):
                refused.append("measure_member %s is not one of the table's measures (%s)" % (
                    str(mm)[:12], ", ".join(sorted(ids))))
            elif ids[mm]["precision"]:
                refused.append("measure_member %s (%s) is the precision of another member (a standard error, a margin of error "
                               "or a confidence interval): never the headline, so the default measure was kept" % (
                                   mm, ids[mm]["name"][:60]))
            else:
                out["measure_member"] = mm
        bd = plan.get("breakdowns")
        if isinstance(bd, list):
            keep = [b for b in bd if isinstance(b, str) and _nst.BREAKDOWN_ID.match(b) and b in bids]
            out["breakdowns"] = list(dict.fromkeys(keep))[:_nst.BREAKDOWNS_MAX]
            if len(keep) < len(bd):
                refused.append("breakdown ids the table does not have were ignored")
        if out.get("slice"):
            dims = {d["column"] for d in S.get("dims") or []}
            kept_ops = []
            for op in out["operations"]:
                if op.get("op") in ("keep_rows", "exclude_rows", "exclude_blank") and op.get("column") in dims:
                    refused.append("%s on %s was ignored: the slice %s chooses the table's rows" % (
                        op["op"], op["column"], out["slice"]))
                    continue
                kept_ops.append(op)
            out["operations"] = kept_ops
    return out, refused


# ----------------------------------------------------------------------------- rows the AI plan sets aside
# The final evaluation (1 Oct 2026): the reviews plan set aside the whole "All Electronics" department, 6,694 of 33,878
# rows (19.8%), as "an umbrella department overlapping the specific ones", which was false (none of those reviews
# repeats a review of another department), and nothing in the report said how many rows went or why. Every plan step
# that sets rows aside (exclude_rows, keep_rows, exclude_blank, and date_from_year's rows before 1900) is now disclosed
# with its count, its share of the file's rows and the plan's own reason: the quality risk that names one of the step's
# values, else one that names its column with a word for setting rows aside (_drop_reason). The plan card shows it
# (ai_plan.row_drops), the report writer reads it (plan_applied carries the share; from PLAN_DROP_NOTICE_PCT the step's
# notice leads the limitations) and the PDF prints it (results.plan_row_drops). From PLAN_DROP_NOTICE_PCT of the rows
# the step is also a plan signal (kind "other"), so the one re-plan may keep the rows. When the reason says the rows
# repeat or overlap others (_OVERLAP_CLAIM), the engine checks it (_repeats_kept): how many set-aside rows equal a kept
# row on every column but the step's own and the key-like ones (an id: named like a key, its values nearly all
# different, as the engine's health reads one), each value trimmed.
PLAN_DROP_NOTICE_PCT = 10.0
_DROP_WORDS = re.compile(r"(?i)\b(?:exclud\w*|drop\w*|remov\w*|set aside|filter\w*|left out|omit\w*|discard\w*|"
                         r"blank\w*|empty|missing)\b")
_OVERLAP_CLAIM = re.compile(r"(?i)\b(?:duplicat\w*|overlap\w*|double[- ]count\w*|repeat\w*|counted twice|umbrella|"
                            r"already (?:counted|included|covered))")
_KEY_SUFFIXES = ("id", "_id", "key", "_key", "uuid", "_uuid", "pk", "_pk")      # northledger.health._ID_NAME_SUFFIXES
_KEY_DISTINCT = 0.90                                                             # northledger.health._ID_DISTINCT_THRESHOLD


def _drop_reason(plan: Dict[str, Any], op: Dict[str, Any]) -> str:
    """The plan's own words for a step that sets rows aside: the first quality risk that names one of the step's values,
    else the first that names its column beside a word for setting rows aside; "" when none does."""
    risks = [str(x) for x in plan.get("quality_risks") or [] if str(x).strip()]
    col = str(op.get("column") or "")
    vals = [str(v).strip() for v in (op.get("values") or [])[:50] if len(str(v).strip()) >= 2]
    for r in risks:
        if any(v.casefold() in r.casefold() for v in vals):
            return r
    if col:
        rx = re.compile(r"(?i)(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(col))
        for r in risks:
            if rx.search(r) and _DROP_WORDS.search(r):
                return r
    return ""


def _key_like(df: Any, col: str) -> bool:
    """A column named like a key whose filled values are nearly all different (the engine's id-like rule)."""
    if not str(col).strip().lower().replace(" ", "_").endswith(_KEY_SUFFIXES):
        return False
    s = df[col].str.strip()
    s = s[s != ""]
    return bool(len(s)) and s.nunique() / float(len(s)) > _KEY_DISTINCT


def _repeats_kept(gone: Any, kept: Any, skip: Iterable[str]) -> Tuple[Optional[int], List[str]]:
    """(how many rows of `gone` equal a row of `kept` on every column but `skip`, the columns compared); (None, []) when
    no column is left to compare. Exact after trimming; hashed first, each hit then compared value by value."""
    import pandas as pd
    cols = [c for c in gone.columns if c not in set(skip)]
    if not cols:
        return None, []
    if not len(gone) or not len(kept):
        return 0, cols
    g = gone[cols].apply(lambda s: s.str.strip())
    k = kept[cols].apply(lambda s: s.str.strip())
    hg = pd.util.hash_pandas_object(g, index=False).values
    hk = pd.util.hash_pandas_object(k, index=False).values
    hit = pd.Series(hg).isin(set(hk.tolist())).values
    if not hit.any():
        return 0, cols
    near = pd.Series(hk).isin(set(hg[hit].tolist())).values
    keys = set(k[near].itertuples(index=False, name=None))
    return sum(1 for t in g[hit].itertuples(index=False, name=None) if t in keys), cols


def _valued_rows(plan: Dict[str, Any], df: Any, drop: Any) -> int:
    """How many of the rows a step sets aside (`drop`, a mask over `df`) hold a usable value of the plan's primary
    measure, the only rows whose loss can change what the report measures (integration pass, 1 Oct 2026: the FX plan's
    exclude_blank on VALUE set aside 569 rows with no rate at all and cost a re-plan call). A usable value is filled (not
    blank and not one of the engine's placeholder words) and, in a column whose filled values the engine reads as
    numbers (clean.NUMERIC_DOMINANCE of them or more), a number by the engine's own reader; a 0 the analyses read as no
    value (the placeholder evidence of _zero_shape, for a primary the plan types a level) is not one. Every row counts
    when the plan names no primary measure (the headline then counts rows)."""
    import numpy as np
    import pandas as pd
    from northledger import clean as _clean
    drop = np.asarray(drop, dtype=bool)
    prim = str(plan.get("primary") or "")
    if not prim or prim not in df.columns:
        return int(drop.sum())
    s = df[prim]
    filled = _filled_text(s).to_numpy()
    n_f = int(filled.sum())
    if not n_f:
        return 0
    txt = s.astype(str).str.strip()
    nums = pd.to_numeric(txt.where(filled), errors="coerce").to_numpy(dtype=float)
    slow = filled & np.isnan(nums)
    raw = txt.to_numpy(dtype=object)
    dec = _clean._decimal_convention(raw[slow]) if slow.any() else "."
    units = _clean.column_units(prim)

    def engine_read(at: Any) -> None:
        nums[at] = [_clean._coerce_numeric_detail(v, True, dec, units)[0] for v in raw[at]]
    # a number column (the engine's own bar, estimated from at most 400 of the values the plain reader could not read)
    idx = np.flatnonzero(slow)
    probe = idx[np.linspace(0, len(idx) - 1, min(len(idx), 400)).astype(int)] if len(idx) else idx
    if len(probe):
        engine_read(probe)
    hit = float((~np.isnan(nums[probe])).mean()) if len(probe) else 0.0
    if (n_f - len(idx)) + hit * len(idx) < _clean.NUMERIC_DOMINANCE * n_f:
        return int((drop & filled).sum())
    rest = np.flatnonzero(slow & drop & np.isnan(nums))
    if len(rest):
        engine_read(rest)
    usable = filled & ~np.isnan(nums)
    types = {str(c.get("name")): str(c.get("semantic_type") or "") for c in plan.get("columns") or []
             if isinstance(c, dict)}
    if types.get(prim) in PLACEHOLDER_ZERO_TYPES and bool((usable & drop & (nums == 0)).any()):
        dcol = next((str(c.get("name")) for c in plan.get("columns") or [] if isinstance(c, dict)
                     and c.get("role") == "date" and str(c.get("name")) in df.columns and str(c.get("name")) != prim), None)
        dates = pd.to_datetime(df[dcol].where(_filled_text(df[dcol])), errors="coerce") if dcol else None
        _z, ev = _zero_shape(np.where(usable, nums, np.nan), dates)
        if ev:
            usable = usable & ~np.asarray(ev["mask"], dtype=bool)
    return int((usable & drop).sum())


def _drop_record(kind: str, op: Dict[str, Any], plan: Dict[str, Any], df: Any, drop: Any, n_in: int) -> Dict[str, Any]:
    """One step's record for _row_drops: {op, column, values, rows, of, valued, reason, repeats, compared}; `drop` is
    the mask of the rows it sets aside, over `df` (the file's rows the steps before it kept). `valued` (_valued_rows) is
    read for a step that sets aside PLAN_DROP_NOTICE_PCT of the file's rows or more (none under it can be a signal),
    else None."""
    col = str(op.get("column") or "")
    reason = _drop_reason(plan, op) if kind != "date_from_year" else "the engine reads dates from 1900 on"
    rows = int(drop.sum())
    valued = _valued_rows(plan, df, drop) if rows and n_in and 100.0 * rows / n_in >= PLAN_DROP_NOTICE_PCT else None
    rec = {"op": kind, "column": col, "values": [str(v) for v in (op.get("values") or [])[:500]],
           "rows": rows, "of": int(n_in), "valued": valued, "reason": reason, "repeats": None, "compared": []}
    if reason and _OVERLAP_CLAIM.search(reason) and rec["rows"]:
        typed = {str(c.get("name")) for c in plan.get("columns") or [] if isinstance(c, dict)
                 and c.get("semantic_type") == "identifier"}
        keys = [c for c in df.columns if c != col and (c in typed or _key_like(df, c))]
        n, cols = _repeats_kept(df[drop], df[~drop], [col] + keys)
        rec["repeats"], rec["compared"], rec["keys"] = n, cols, keys
    return rec


def _or_words(vals: List[str]) -> str:
    return vals[0] if len(vals) == 1 else "%s or %s" % (", ".join(vals[:-1]), vals[-1])


def _row_drops(drops: List[Dict[str, Any]], private: Any) -> List[Dict[str, Any]]:
    """ai_plan.row_drops: each step that set rows aside, {op, column, rows, of, pct, valued, reason, check, compared,
    text, notice}: `text` the full disclosure (the plan card, the PDF), `notice` the one line for the report's summary
    and the PDF's notice (from PLAN_DROP_NOTICE_PCT of the rows, else ""), `valued` how many of the rows hold a usable
    value of the plan's primary measure (_valued_rows; None under PLAN_DROP_NOTICE_PCT), which alone decides the plan
    signal (_plan_signals). A step's values are named only when they are 1 to 3 short values of a column the visitor
    did not withhold or code (the planner saw them in its profile)."""
    out = []
    for d in drops or []:
        n, of, col = int(d["rows"]), int(d["of"]), d["column"]
        if not n or not of:
            continue
        pct = 100.0 * n / of
        vals = d.get("values") or []
        named = not (private(col) if col else None) and 1 <= len(vals) <= 3 and all(0 < len(v) <= 40 for v in vals)
        if d["op"] == "exclude_blank":
            where = "where %s is blank" % col
        elif d["op"] == "date_from_year":
            where = "dated before 1900 in %s" % col
        elif d["op"] == "exclude_rows":
            where = ("where %s is %s" % (col, _or_words(vals))) if named else \
                "where %s is one of %s" % (col, _n_values(len(vals)))
        else:
            where = ("where %s is not %s" % (col, _or_words(vals))) if named else \
                "where %s is none of the %s kept" % (col, _n_values(len(vals)))
        k = d.get("repeats")
        check = "" if k is None else ("none of these rows duplicates a kept row" if k == 0 else
                                      "%s of these rows (%s) %s a kept row" % (format(k, ","), _pct_text(100.0 * k / n),
                                                                             "duplicates" if k == 1 else "duplicate"))
        but = [x for x in [col] + list(d.get("keys") or []) if x]
        reason = str(d.get("reason") or "").strip()
        rs = reason if reason.endswith((".", "!", "?")) else (reason + "." if reason else "")
        text = "Set aside %s rows (%s of the file's %s) %s. %s" % (
            format(n, ","), _pct_text(pct), format(of, ","), where,
            ("The plan's reason: " + rs) if rs else "The plan gave no reason.")
        if check:
            text += " The engine checked: %s (compared on every column but %s)." % (check, _listed(but))
        notice = ""
        if pct >= PLAN_DROP_NOTICE_PCT:
            notice = ("The AI plan set aside %s rows (%s): %s" % (format(n, ","), _pct_text(pct), rs)) if rs else \
                "The AI plan set aside %s rows (%s) and gave no reason." % (format(n, ","), _pct_text(pct))
            if check:
                notice += " The engine checked: %s." % check
        valued = d.get("valued")
        out.append({"op": d["op"], "column": col, "rows": n, "of": of, "pct": pct,
                    "valued": None if valued is None else int(valued), "reason": reason, "check": check,
                    "compared": list(d.get("compared") or []), "text": text, "notice": notice})
    return out


def _apply_plan(data: bytes, plan: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Run the plan's operations on the file. Returns the bytes, what was applied (and refused), and
    the long-table layout when long_to_wide ran."""
    import pandas as pd
    df = pd.read_csv(io.BytesIO(data), dtype=str, encoding="utf-8-sig", keep_default_na=False)
    n_in = len(df)
    applied, refused, decisions = [], [], {}
    drops: List[Dict[str, Any]] = []          # the steps that set rows aside (_row_drops)

    def share(k: int) -> str:
        return _pct_text(100.0 * k / n_in) if n_in else "0%"
    layout = None
    # row filters read the file's own columns, so they run before any column is set aside
    # the units of a long table, read before any column is set aside (a plan may set UOM aside first)
    ucol = next((c for c in df.columns if _pnorm(c) in ("uom", "unit", "units", "unitmeasure")), None)
    units_orig = sorted(set(v.strip() for v in df[ucol].unique() if str(v).strip())) if ucol else []
    rowf = ("exclude_rows", "keep_rows", "exclude_blank")
    ops = [o for o in plan.get("operations", []) if o["op"] in rowf] + \
          [o for o in plan.get("operations", []) if o["op"] not in rowf]
    for op in ops:
        kind = op["op"]
        try:
            if kind == "keep_columns":
                keep = [c for c in op.get("columns", []) if c in df.columns]
                if len(keep) < 2:
                    refused.append("keep_columns: fewer than two columns named, so every column was kept")
                    continue
                dropped = [c for c in df.columns if c not in keep]
                df = df[keep]
                applied.append("kept the %d columns the goal needs, set aside %d others" % (len(keep), len(dropped)))
            elif kind == "set_aside":
                cols = [c for c in op.get("columns", []) if c in df.columns]
                if cols:
                    df = df.drop(columns=cols)
                    applied.append("set aside %s" % ", ".join(cols))
            elif kind in ("exclude_rows", "keep_rows"):
                vals = set(str(v) for v in op["values"][:500])
                m = df[op["column"]].isin(vals)
                drop = m if kind == "exclude_rows" else ~m
                drops.append(_drop_record(kind, op, plan, df, drop, n_in))
                df = df[~drop]
                # each step's line says how many rows it set aside and what share of the file's rows that is
                # (final evaluation, 1 Oct 2026: see _row_drops)
                if kind == "exclude_rows":
                    applied.append("dropped %s rows (%s) where %s is one of %d values (%s rows left)" % (
                        format(int(drop.sum()), ","), share(int(drop.sum())), op["column"], len(vals), format(len(df), ",")))
                else:
                    applied.append("kept %s rows where %s is one of %d values and set aside the other %s (%s)" % (
                        format(len(df), ","), op["column"], len(vals), format(int(drop.sum()), ","), share(int(drop.sum()))))
            elif kind == "exclude_blank":
                c = op.get("column")
                if c not in df.columns:
                    refused.append("exclude_blank: %s is not a column now" % c)
                    continue
                blank = df[c].str.strip() == ""
                if not blank.any() or blank.all():
                    refused.append("exclude_blank: %s has %s blank" % (c, "no" if not blank.any() else "every value"))
                    continue
                drops.append(_drop_record(kind, op, plan, df, blank, n_in))
                df = df[~blank]
                applied.append("dropped %s rows (%s) where %s is blank (%s rows left)" % (
                    format(int(blank.sum()), ","), share(int(blank.sum())), c, format(len(df), ",")))
            elif kind == "date_from_year":
                c = op["column"]
                y = pd.to_numeric(df[c], errors="coerce")
                if y.between(1000, 2999).mean() < 0.95:
                    refused.append("date_from_year: %s does not hold years" % c)
                    continue
                old = y < 1900
                if old.any():
                    drops.append(_drop_record(kind, op, plan, df, old, n_in))
                    df, y = df[~old], y[~old]
                    applied.append("set aside %s rows (%s) before 1900 (the engine's dates start there)" % (
                        format(int(old.sum()), ","), share(int(old.sum()))))
                df.insert(0, "date", y.astype("Int64").astype(str) + "-12-31")
                df = df.drop(columns=[c])
                applied.append("read %s as the date (the year's last day)" % c)
            elif kind == "not_personal":
                for c in op.get("columns", []):
                    filled = df[c].str.strip()[df[c].str.strip() != ""] if c in df.columns else None
                    num = pd.to_numeric(filled.str.replace(",", "", regex=False), errors="coerce") if filled is not None else None
                    if num is not None and len(num) and num.notna().mean() >= 0.95:
                        decisions[c] = "keep"
                    else:
                        refused.append("not_personal: %s is not a numeric column, so its privacy check stands" % c)
                if decisions:
                    applied.append("kept numeric columns the privacy check had flagged: %s" % ", ".join(decisions))
            elif kind == "long_to_wide":
                buf = df.to_csv(index=False).encode("utf-8")
                roles = {str(c.get("role")): c["name"] for c in plan.get("columns", []) if c.get("name") in df.columns}
                dcol = op.get("column") if op.get("column") in df.columns and op.get("column") == roles.get("date") else roles.get("date")
                vcol = plan.get("primary") if plan.get("primary") in df.columns else roles.get("target")
                nb, lay = _reshape_long_panel(buf, planned=True, date_col=dcol, value_col=vcol)
                if lay:
                    if not lay.get("units") and len(units_orig) > 1:
                        lay["units"] = units_orig
                    layout = lay
                    df = pd.read_csv(io.BytesIO(nb), dtype=str, keep_default_na=False)
                    applied.append("one column per series (%d series)" % lay["kept"])
                else:
                    refused.append("long_to_wide: the file is not a long table")
        except Exception as exc:  # noqa: BLE001 - one failed step never stops the run
            refused.append("%s failed (%s)" % (kind, type(exc).__name__))
    # positions: each row's place among the file's data rows (the filters keep pandas' index), so a data
    # test can name the visitor's own line; a reshaped long table has no such place
    return df.to_csv(index=False).encode("utf-8"), {"applied": applied, "refused": refused, "decisions": decisions,
                                                    "rows_in": n_in, "drops": drops,
                                                    "positions": None if layout else [int(i) for i in df.index]}, layout


# ----------------------------------------------------------------------------- the engine's reading
# ONE parser (owner's decision after the review of 29 Sep 2026): the data tests, the AI's analyses and the
# planner's profile read every cell exactly as the engine's cleaner read it. The adapter used to re-parse the
# planned text with readers of its own; they missed the engine's decimal comma (a median of 2.29 against the
# engine's 1,260), its k suffix, its accounting negatives "($86.75)", guessed day/month orders the engine set
# aside, and counted rows the engine had set aside. Now the cleaner's own repair and conversion rules
# (clean.standard_rules, in the cleaner's own order, with its own parameters) are replayed on the landed table,
# so every row, kept or set aside, has the value the engine gave it; the analyses read only the rows it kept
# (the rows behind downloads.clean_csv). No engine file is changed: the replay calls the cleaner's own code.
_READ_RULE_KINDS = ("null_like", "trim", "case", "numeric", "date", "boolean")
_AMBIGUOUS_WORDS = "could be day/month or month/day"


def _engine_slug(header: Any) -> str:
    """A column's landed name by the engine's own rule (intake.normalise_columns, without its duplicate
    suffix): the fallback when the engine's column map is not at hand."""
    return re.sub(r"[^a-z0-9_]+", "_", str(header).strip().lower()).strip("_")


def _null_tokens() -> Set[str]:
    """The engine's placeholder words (N/A, NULL, a dash ...), lower-cased, with the empty string."""
    try:
        from northledger.clean import DEFAULT_NULL_TOKENS
        return set(str(t).strip().lower() for t in DEFAULT_NULL_TOKENS)
    except Exception:  # noqa: BLE001 - the engine is always importable where this runs; a safe floor
        return {"", "n/a", "na", "null", "none", "nan", "-"}


def _filled_text(s: Any) -> Any:
    """True where a cell holds a value: not blank and not one of the engine's placeholders ("N/A" is blank,
    as the engine's null_like rule reads it, never an unreadable value)."""
    t = s.astype(object).where(s.notna(), "").astype(str).str.strip()
    return ~t.str.lower().isin(_null_tokens())


class _Reading:
    """The engine's reading of the table it landed: `values` holds every cell as its cleaner read it (a float,
    a datetime or the cleaned text) for every landed row in order, `texts` the text it landed, `kept` which
    rows the cleaner kept, `aside` the cleaner's own message for each row it set aside, and `land` the map
    from the file's column names to the landed names (the engine's intake map)."""

    def __init__(self, values: Any, texts: Any, kept: Any, land: Dict[str, str], aside: Dict[int, str]) -> None:
        self.values, self.texts, self.kept, self.aside = values, texts, kept, aside
        self.land = {str(k): str(v) for k, v in (land or {}).items() if str(v) in values.columns}
        self.head = {v: k for k, v in self.land.items()}
        self.n = int(len(values))

    def landed(self, header: Any) -> Optional[str]:
        h = str(header)
        if h in self.land:
            return self.land[h]
        if h in self.values.columns:
            return h
        s = _engine_slug(h)
        return s if s in self.values.columns else None

    def header(self, landed: str) -> str:
        return self.head.get(landed, landed)

    def kind(self, landed: Optional[str]) -> str:
        """How the engine read a column: "number", "date" or "text"."""
        import pandas as pd
        if landed is None or landed not in self.values.columns:
            return "text"
        s = self.values[landed]
        if pd.api.types.is_bool_dtype(s):
            return "text"
        if pd.api.types.is_numeric_dtype(s):
            return "number"
        if pd.api.types.is_datetime64_any_dtype(s):
            return "date"
        return "text"

    def filled(self, landed: str) -> Any:
        return _filled_text(self.texts[landed]).to_numpy()

    def numbers(self, landed: str) -> Any:
        """The column as the engine's numbers (NaN where it holds none, and everywhere when the engine did not
        read the column as numbers)."""
        import numpy as np
        import pandas as pd
        if self.kind(landed) != "number":
            return pd.Series(np.nan, index=self.values.index, dtype=float)
        return pd.to_numeric(self.values[landed], errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)

    def dates(self, landed: str) -> Any:
        import pandas as pd
        if self.kind(landed) != "date":
            return pd.Series(pd.NaT, index=self.values.index, dtype="datetime64[ns]")
        return pd.to_datetime(self.values[landed], errors="coerce")

    def ambiguous(self, landed: str) -> Any:
        """Rows the engine set aside because this column's day and month could be read either way round."""
        import numpy as np
        lead = "column %r:" % landed
        m = np.zeros(self.n, dtype=bool)
        for pos, msg in self.aside.items():
            if 0 <= pos < self.n and msg.startswith(lead) and _AMBIGUOUS_WORDS in msg:
                m[pos] = True
        return m


def _engine_reading(db_path: str, table: str, rules: List[Any], cr: Any, colmap: Dict[str, str]) -> _Reading:
    """Replay the cleaner's repairs and conversions (the rules clean_table ran, in its order) on the landed
    table with the cleaner's own code, before any row is set aside: the value the engine gave every cell."""
    import numpy as np
    import pandas as pd
    from northledger import clean as _clean
    from northledger._sqlite import connect_ro
    con = connect_ro(db_path)
    try:
        raw = pd.read_sql_query("SELECT * FROM %s" % _clean._quote_ident(table), con)
    finally:
        con.close()
    state = _clean._Pass(raw)
    for rule in rules:
        if rule.kind in _READ_RULE_KINDS:
            _clean._DISPATCH[rule.kind](state, rule)
    texts = raw.astype(object).where(raw.notna(), "").astype(str)
    kept = np.zeros(len(raw), dtype=bool)
    kept[[int(i) for i in cr.clean.index if 0 <= int(i) < len(raw)]] = True
    q = cr.quarantined
    aside = ({int(i): str(v) for i, v in q[_clean.QUARANTINE_COL].items()}
             if q is not None and _clean.QUARANTINE_COL in getattr(q, "columns", []) else {})
    return _Reading(state.work, texts, kept, colmap, aside)


def _fallback_values(s: Any, kind: str) -> Any:
    """The engine's own value readers on one column of text, for a table the engine did not land row for row
    (a file its rules read reshaped): its number reader with the column's own decimal convention and unit
    suffixes, or its date reader with its default formats. Placeholders read as missing."""
    import numpy as np
    import pandas as pd
    from northledger import clean as _clean
    raw = s.astype(object).where(_filled_text(s), None)
    if kind == "number":
        vals = raw.to_numpy(dtype=object)
        dec = _clean._decimal_convention(vals)
        units = _clean.column_units(str(s.name))
        return pd.Series([_clean._coerce_numeric_detail(v, True, dec, units)[0] for v in vals],
                         index=s.index, dtype=float).replace([np.inf, -np.inf], np.nan)
    parsed, _bad, _amb = _clean._coerce_dates_detail(raw, _clean.DEFAULT_DATE_FORMATS)
    return parsed


def _pct_reading(nums: Any, texts: Any, filled: Any) -> Dict[str, Any]:
    """A percentage column on one scale (review H4/H7, 29 Sep 2026). A value written with a % sign is already a
    percent (the engine reads "12%" as 0.12, so it is 12 here), never multiplied again. The values written
    without one are fractions only when at least 95% of them lie between 0 and 1; otherwise they are percents.
    Returns {"percent": the column in percent, "scale": 1 | 100, "out": out-of-range mask (below 0 or above
    100 percent), "low"/"high": which side, "mixed": fractions and percents of the same quantity side by side,
    "n_frac"/"n_pct": their counts}. ONE reading for the data test and the analyses."""
    import numpy as np
    import pandas as pd
    v = np.asarray(pd.to_numeric(pd.Series(nums), errors="coerce"), float)
    ok = np.asarray(filled, bool) & ~np.isnan(v)
    sign = np.asarray(pd.Series(texts).astype(str).str.strip().str.endswith("%"), bool)
    bare = ok & ~sign
    bv = v[bare]
    frac_share = float(((bv >= 0) & (bv <= 1)).mean()) if len(bv) else 0.0
    scale = 1 if len(bv) and frac_share >= 0.95 else 100
    pct = np.where(sign, v * 100.0, v * (100.0 if scale == 1 else 1.0))
    pct = np.where(ok, pct, np.nan)
    low, high = ok & (pct < 0), ok & (pct > 100.0 + 1e-9)
    mixed, n_frac, n_pct = False, 0, 0
    if scale == 100:
        fr, pc = bv[(bv > 0) & (bv < 1)], bv[bv > 1]
        n_frac, n_pct = int(len(fr)), int(len(pc))
        if n_frac >= 3 and n_pct >= 3:
            f100 = 100.0 * fr
            lo_f, hi_f = np.percentile(f100, [10, 90])
            lo_p, hi_p = np.percentile(pc, [10, 90])
            # the same quantity on two scales: each group's middle sits inside the other's spread
            mixed = bool(lo_p <= np.median(f100) <= hi_p and lo_f <= np.median(pc) <= hi_f)
    return {"percent": pd.Series(pct, index=getattr(nums, "index", None)), "scale": scale, "out": low | high,
            "low": low, "high": high, "mixed": mixed, "n_frac": n_frac, "n_pct": n_pct}


# ----------------------------------------------------------------------------- data tests from the plan
# The dbt idea: the AI's reading of each column (its semantic_type) compiles to a test the engine's adapter
# runs on the file the plan produced. Every number here is counted by this code, none by the model.
#
# THE RULE (owner's decision, 29 Sep 2026, after an adversarial review blocked the cell-blanking rule): a
# data test never changes what the engine reads. The engine gets the planned file exactly as the plan left
# it, so its own cleaning, its exact-duplicate check, its health score and its set-aside file all work on
# the visitor's real data. A test only reports and signals:
#   * unreadable text in a typed column (a date, or a number: count, duration, percentage, money): the pass
#     share is the readable values over the non-blank ones. At or above MISREAD_BELOW the AI's reading
#     stands: the AI's analyses count those cells as missing and say so in each sentence over the column,
#     and the card says what the engine itself did with those rows (set aside, with its reason, or kept).
#     Below it the column is probably not what the AI read it as, and the planner is told the fact.
#   * an out-of-range value (a percentage below 0 or above its scale, a negative duration, a negative or
#     fractional count, a third value in a yes/no column) is evidence about the TYPE, never a cell to fix:
#     nothing changes; when more than OUT_OF_RANGE_SIGNAL of the values are out of range the planner is
#     told the fact, and at or below it the card notes it.
#   * a repeated identifier: nothing changes; the card counts the repeats and how many of them the engine's
#     own exact-duplicate check set aside. A column under MISREAD_BELOW unique once those exact duplicate
#     rows are left out is probably not an identifier, and the planner is told.
# The visitor can turn any test off (__contracts_off__). The download "values the data tests flagged" lists
# every failing cell with the visitor's own line, the test and what happened to it.
#
# MISREAD_BELOW = 0.80, why: the live run of 29 Sep 2026 had free text in 9% of a real date column
# ("pending carrier scan") and 8% of a real count ("about 21"). A rule that called any column with more
# than 5% failing a misread told the AI its correct readings were wrong. A misread usually fails most of a
# column; a real column with a messy fifth still reads four values in five.
MISREAD_BELOW = 0.80
OUT_OF_RANGE_SIGNAL = 0.05
FLAGGED_CELLS_MAX = 20000
CONTRACT_NOTE = ("Tests compiled from the AI's reading of each column. They never change what the engine reads: "
                 "each says what failed and what the engine itself did with those rows. A column where fewer "
                 "than %d%% of the values can be read (or are unique, for an identifier), or more than %d%% are "
                 "out of range, is probably not what the AI read it as: that is what the AI's one "
                 "self-correction is asked to fix." % (round(100 * MISREAD_BELOW), round(100 * OUT_OF_RANGE_SIGNAL)))
_CONTRACT_WORDS = {"percentage": "between 0 and 100 (or 0 and 1)", "count": "a whole number, 0 or more",
                   "duration": "0 or more", "year": "a whole year between 1000 and 2999",
                   "rating": "a whole number on the file's rating scale", "identifier": "unique: no value repeated",
                   "boolean": "at most 2 different values", "category": "one of the values the profile lists",
                   "date": "a date that can be read", "flow_amount": "a number that can be read",
                   "level": "a number that can be read"}
# what a column of each type is, for "so it may not be a date"
_CONTRACT_NOUN = {"percentage": "a percentage", "count": "a count", "duration": "a duration", "year": "a year",
                  "rating": "a rating", "identifier": "an identifier", "boolean": "a yes/no column",
                  "category": "a category with those values", "date": "a date", "flow_amount": "an amount",
                  "level": "a number"}
_NUMERIC_TESTS = ("percentage", "count", "duration", "year", "rating", "flow_amount", "level")
def _year_dates(nums: Any) -> Any:
    """A column the engine reads as whole numbers between 1000 and 2999, as dates (each year's last day, as
    date_from_year writes it): the one way a number is read as a date, for a plan that calls a year column
    its date. Anything else is NaT."""
    import numpy as np
    import pandas as pd
    v = pd.to_numeric(pd.Series(nums), errors="coerce").astype(float)
    ok = v.notna() & (np.mod(v.fillna(0.5), 1) == 0) & (v >= 1000) & (v <= 2999)
    out = pd.Series(pd.NaT, index=v.index, dtype="datetime64[ns]")
    if ok.any():
        out[ok] = pd.to_datetime(v[ok].astype(int).astype(str) + "-12-31", errors="coerce")
    return out


def _run_contracts(df: Any, plan: Dict[str, Any], off: Any = (), reading: Optional[_Reading] = None,
                   hidden: Optional[Dict[str, str]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Phase one, on the file the plan produced (the engine's input, unchanged), read the way the engine read
    it: `reading` is the engine's reading of these same rows (None when its rows are not these rows; the
    engine's own value readers then read the text). Returns (tests, aux): tests as the card lists them,
    counted but not yet decided, and aux {column: {"masks": {kind: boolean array over the rows}, "range":
    words for an out-of-range value, "read_as": "dates" | "numbers"}}. The kinds: unreadable (text the engine
    could not read as the type), ambiguous (dates the engine set aside because day and month could be either
    way round), out_of_range, repeated, unexpected. A blank cell or a placeholder (N/A, NULL, a dash: the
    engine's own list) is never tested and never fails; `off` lists the columns whose tests the visitor
    turned off. `hidden`: {column: "withhold" | "code"}: the engine reads a withheld or coded column's codes,
    never its values, so its number and date tests are not run (a test on codes would tell the planner a date
    is "probably not a date"); its identifier, yes/no and category tests count repeats and never show a value."""
    import numpy as np
    tests: List[Dict[str, Any]] = []
    aux: Dict[str, Any] = {}
    off = set(str(x) for x in (off or []))
    hidden = hidden or {}
    aligned = reading is not None and reading.n == len(df)
    for pc in plan.get("columns") or []:
        col, st = pc.get("name"), str(pc.get("semantic_type") or "")
        if col not in df.columns or st not in _CONTRACT_WORDS:
            continue
        if st == "category" and not (isinstance(pc.get("values"), list) and pc["values"]):
            continue
        s = df[col].astype(str).str.strip()
        filled = _filled_text(df[col]).to_numpy()
        t = {"column": col, "semantic_type": st, "test": _CONTRACT_WORDS[st], "checked": int(filled.sum()),
             "failed": 0, "examples": [], "action": "", "unreadable": 0, "ambiguous": 0, "out_of_range": 0,
             "repeated": 0, "unexpected": 0, "misread": False, "signal": False}
        if col in off:
            t.update(checked=0, action="turned off by you")
            tests.append(t)
            continue
        if hidden.get(col) and (st in _NUMERIC_TESTS or st == "date"):
            t.update(checked=0, action=("not tested: you withheld this column, so none of its values is read"
                                        if hidden[col] == "withhold" else
                                        "not tested: you chose to code this column, so the engine reads its codes, "
                                        "not its values"))
            tests.append(t)
            continue
        land = reading.landed(col) if aligned else None
        ekind = reading.kind(land) if land is not None else None
        none = np.zeros(len(s), dtype=bool)
        masks = {"unreadable": none, "ambiguous": none, "out_of_range": none, "repeated": none, "unexpected": none}
        rng, read_as = "", ""
        if st in _NUMERIC_TESTS:
            num = reading.numbers(land) if land is not None else _fallback_values(df[col], "number")
            nv = np.asarray(num, dtype=float)
            if land is not None and ekind != "number":
                # the engine reads the column as text (or dates): none of it counts as numbers. How many values its
                # own number reader would read tells the planner how far the column is from numbers
                t["would_read"] = int((filled & np.asarray(_fallback_values(df[col], "number").notna())).sum())
            ok = filled & ~np.isnan(nv)
            masks["unreadable"] = filled & ~ok
            read_as = "numbers"
            t["engine_read_as"] = {"number": "numbers", "date": "dates", "text": "text"}.get(ekind or "number")
            whole = ok & (np.mod(np.where(ok, nv, 0.0), 1) == 0)
            if st == "percentage":
                pr = _pct_reading(nv, df[col], filled)
                hi = pr["scale"]
                t["test"] = ("between 0 and 1 (fractions: at least 95% of the values written without a % sign lie "
                             "between 0 and 1)" if hi == 1 else "between 0 and 100")
                t["scale"] = hi
                masks["out_of_range"] = pr["out"]
                rng = _either(("below 0", pr["low"].any()), ("above %d" % hi, pr["high"].any()))
                if pr["mixed"]:
                    t["mixed_scale"] = {"fractions": pr["n_frac"], "percents": pr["n_pct"]}
            elif st == "count":
                neg, frac = ok & (nv < 0), ok & ~whole
                masks["out_of_range"] = neg | frac
                rng = _either(("below 0", neg.any()), ("fractional", frac.any()))
            elif st == "duration":
                masks["out_of_range"] = ok & (nv < 0)
                rng = "below 0"
            elif st == "year":
                masks["out_of_range"] = ok & ~(whole & (nv >= 1000) & (nv <= 2999))
                rng = "outside the whole years 1000 to 2999"
            elif st == "rating":
                v = nv[ok]
                if not len(v) or float(v.max()) > 10:
                    continue
                lo = 0 if float(v.min()) < 1 else 1
                hi = 5 if float((v <= 5).mean()) >= 0.9 else int(math.ceil(float(v.max())))
                t["test"] = "a whole number from %d to %d (a %d-%d scale, read from the file)" % (lo, hi, lo, hi)
                out_m, frac = ok & ((nv < lo) | (nv > hi)), ok & ~whole
                masks["out_of_range"] = out_m | frac
                rng = _either(("outside the %d to %d scale" % (lo, hi), out_m.any()), ("fractional", frac.any()))
        elif st == "identifier":
            masks["repeated"] = filled & s.duplicated(keep="first").to_numpy()
        elif st == "boolean":
            top = list(s[filled].value_counts().index[:2])
            masks["unexpected"] = filled & ~s.isin(top).to_numpy()
            rng = "beyond its two commonest values"
        elif st == "category":
            allowed = set(str(x).strip() for x in pc["values"])
            t["test"] = "one of the %d values the profile lists" % len(allowed)
            masks["unexpected"] = filled & ~s.isin(allowed).to_numpy()
            rng = "not among the %d values the profile lists" % len(allowed)
        else:  # date
            if land is not None and ekind == "text":
                t["would_read"] = int((filled & np.asarray(_fallback_values(df[col], "date").notna())).sum())
            if land is None:
                dates = _fallback_values(df[col], "date")
            elif ekind == "number":
                dates = _year_dates(reading.numbers(land))       # whole years stand for dates, as the analyses read them
            else:
                dates = reading.dates(land)
            ok = filled & np.asarray(dates.notna())
            amb = (filled & reading.ambiguous(land)) if land is not None else none
            masks["ambiguous"] = amb & ~ok
            masks["unreadable"] = filled & ~ok & ~amb
            read_as = "dates"
            t["engine_read_as"] = {"number": "years", "date": "dates", "text": "text"}.get(ekind or "date")
        bad = masks["unreadable"] | masks["ambiguous"] | masks["out_of_range"] | masks["repeated"] | masks["unexpected"]
        for k in masks:
            t[k] = int(masks[k].sum())
        t["failed"] = int(bad.sum())
        t["examples"] = [x[:40] for x in list(dict.fromkeys(s[bad]))[:3]]
        n = t["checked"]
        if t["unreadable"] and n and (n - t["unreadable"]) / float(n) < MISREAD_BELOW:
            t["misread"] = True             # the reading does not stand: nothing the analyses read is changed
        aux[col] = {"masks": masks, "range": rng, "read_as": read_as}
        tests.append(t)
    return tests, aux


def _either(*parts: Tuple[str, Any]) -> str:
    """'below 0', 'not whole numbers' or 'below 0 or not whole numbers': the parts that happened."""
    got = [w for w, on in parts if on]
    return " or ".join(got) if got else parts[0][0]


def _n_values(n: int, word: str = "value") -> str:
    return "%s %s%s" % (format(n, ","), word, "" if n == 1 else "s")


def _engine_part(rows: Any, engine: Optional[Dict[str, Any]], col: str, empties: bool = False) -> Tuple[str, str]:
    """What the engine did with these rows, in (the card's words, the AI payload's shorter words).
    engine: {"aside": {row: reason}, "empty": {column: set of kept rows whose value it left empty}} over the
    same rows as the tests, or None when its rows are not the tests' rows."""
    rows = [int(r) for r in rows]
    if engine is None:
        w = "the engine read this file reshaped, so what it did with these rows is not matched here"
        return w, w
    one = len(rows) == 1
    aside = [r for r in rows if r in engine["aside"]]
    kept = len(rows) - len(aside)
    reasons: Dict[str, int] = {}
    for r in aside:
        reasons[engine["aside"][r]] = reasons.get(engine["aside"][r], 0) + 1
    top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    cut = lambda k: k if len(k) <= 90 else k[:87].rstrip() + "..."
    why = (cut(top[0][0]) if len(top) == 1 else
           "; ".join("%s: %s" % (cut(k), format(v, ",")) for k, v in top) if len(top) <= 2 else
           "for %d different reasons" % len(top))
    emptied = ""
    if empties and kept:
        e = sum(1 for r in rows if r not in engine["aside"] and r in engine["empty"].get(col, ()))
        if e:
            emptied = (", leaving %s empty" % ("that value" if e == 1 else "those values" if e == kept
                                                else "%s of those values" % format(e, ",")))
    if not aside:
        w = "the engine kept %s%s" % ("its row" if one else "these rows", emptied)
        return w, w
    if not kept:
        return (("the engine set %s aside (%s; see the set-aside file)" % ("its row" if one else "these rows", why)),
                ("the engine set %s aside" % ("its row" if one else "these rows")))
    return (("the engine set %s of these rows aside (%s; see the set-aside file) and kept %s%s"
             % (format(len(aside), ","), why, format(kept, ","), emptied)),
            ("the engine set %s of these rows aside and kept %s%s" % (format(len(aside), ","), format(kept, ","), emptied)))


# words in an engine row message that tell which kind of rule set the row aside (clean.py _RULE_BLURBS)
_KIND_WORDS = {"date": ("date format", "ambiguous", "day and month"), "not_future": ("future",),
               "date_plausible": ("placeholder", "implausib"), "range": ("range",), "numeric": ("number",),
               "not_null": ("missing", "empty"), "allowed": ("allowed",), "boolean": ("yes/no",),
               "series_end": ("series",), "latest_per_key": ("superseded", "later row"), "dedupe": ("duplicate",)}


def _rule_reasons(messages: Dict[int, str], rules: List[Any], used: Any) -> Dict[int, str]:
    """Each set-aside row's reason as the engine's rule-level words ("order_date_date: value matches no
    known date format", as its cleaning summary counts them), not the row's own message ("column
    'order_date': 'TBD' matches no known date format (tried ...)"), so a card can count rows by reason and
    never quotes a value. A message no rule can be matched to is kept as it is."""
    known = [(str(getattr(ru, "column", "") or ""), str(getattr(ru, "kind", "")), ru.reason_key()) for ru in rules
             if ru.reason_key() in used]
    out: Dict[int, str] = {}
    memo: Dict[str, str] = {}
    for row, msg in messages.items():
        if msg in memo:
            out[row] = memo[msg]
            continue
        m = re.match(r"column '((?:[^'\\]|\\.)*)': ", msg)
        col = m.group(1) if m else ""
        cands = [(k, key) for c, k, key in known if (c == col if m else k == "dedupe") or key == msg]
        if len(cands) > 1:
            low = msg.lower()
            cands = [(k, key) for k, key in cands if key == msg or any(w in low for w in _KIND_WORDS.get(k, ()))] or cands
        memo[msg] = out[row] = cands[0][1] if len(cands) == 1 else msg
    return out


def _analysis_tail(rows: Any, engine: Optional[Dict[str, Any]], what: str = "count them as missing") -> str:
    """What the AI's analyses did with these values: they read only the rows the engine kept, so a value in a
    row the engine set aside is not read at all, and one in a kept row is counted as missing."""
    rows = [int(r) for r in rows]
    if engine is None:
        return "the AI's analyses %s" % what
    kept = [r for r in rows if r not in engine["aside"]]
    if len(kept) == len(rows):
        return "the AI's analyses %s" % what
    if not kept:
        return "the AI's analyses read only the rows the engine kept"
    return "the AI's analyses read only the rows the engine kept and count the %s kept ones as missing" % format(len(kept), ",")


def _finish_contracts(tests: List[Dict[str, Any]], aux: Dict[str, Any], engine: Optional[Dict[str, Any]],
                      dup_reason: str) -> None:
    """Phase two, after the engine ran: each test's action (what failed, and what the engine did with those
    rows, read from its own set-aside file and cleaned table), its short form for the AI report writer
    (t["brief"]) and, for a probable misread or a type the values contradict, the fact the planner gets
    (t["signal"], t["problem"]). dup_reason: the engine's set-aside reason for an exact duplicate row."""
    import numpy as np
    for t in tests:
        if t["action"]:                                  # turned off by you
            t.setdefault("brief", t["action"])
            continue
        a = aux.get(t["column"]) or {}
        m = a.get("masks") or {}
        n, col = int(t["checked"]), t["column"]
        if not t["failed"]:
            t["action"] = t["brief"] = "passed" if n else "nothing to test: every value is blank"
            continue
        z = np.zeros(len(next(iter(m.values()))) if m else 0, dtype=bool)
        rows = np.flatnonzero(m.get("unreadable", z) | m.get("ambiguous", z) | m.get("out_of_range", z)
                              | m.get("repeated", z) | m.get("unexpected", z))
        noun = _CONTRACT_NOUN.get(t["semantic_type"], "what the AI read it as")
        read_as = a.get("read_as") or "values"
        u, o, r = t["unreadable"], t["out_of_range"] + t["unexpected"], t["repeated"]
        amb = int(t.get("ambiguous") or 0)
        one_read = {"dates": "a date", "numbers": "a number"}.get(read_as, "a value")
        read_u = one_read if u == 1 else read_as
        eng, eng_short = _engine_part(rows, engine, col, empties=bool(u) and read_as == "numbers")
        if t["misread"]:
            ok = n - u
            as_ = t.get("engine_read_as")
            if as_ == "text":
                w = int(t.get("would_read") or 0)
                kind_w = "dates" if read_as == "dates" else "numbers"
                t["problem"] = ("only %s of %s values %s (%d%%), too few for the engine to read the column as %s: it "
                                "reads it as text" % (format(w, ","), format(n, ","),
                                                      "read as dates" if read_as == "dates" else "can be read as numbers",
                                                      int(math.floor(100.0 * w / n)), kind_w))
            elif as_ in ("numbers", "years") and read_as == "dates":
                t["problem"] = "the engine reads it as numbers, not dates: only %s of %s values are whole years (%d%%)" % (
                    format(ok, ","), format(n, ","), int(math.floor(100.0 * ok / n)))
            elif as_ == "dates" and read_as == "numbers":
                t["problem"] = "the engine reads it as dates, so none of its %s values count as numbers" % format(n, ",")
            else:
                t["problem"] = "only %s of %s values %s (%d%%)" % (
                    format(ok, ","), format(n, ","), "read as dates" if read_as == "dates" else "can be read as numbers",
                    int(math.floor(100.0 * ok / n)))       # 79.6% is "79%": below the line, never shown as 80%
            t["signal"] = True
            t["action"] = "probably not %s: %s; %s" % (noun, t["problem"], eng)
            t["brief"] = "probably not %s: %s; %s" % (noun, t["problem"], eng_short)
            continue
        tail = "the tests changed no value"
        if r:
            dup = 0
            if engine is not None:
                dup = sum(1 for i in np.flatnonzero(m["repeated"]) if engine["aside"].get(int(i)) == dup_reason)
            base = n - dup
            if base and (n - r) / float(base) < MISREAD_BELOW:
                t["signal"] = True
                t["problem"] = "only %s of %s values are unique (%d%%)%s" % (
                    format(n - r, ","), format(base, ","), int(math.floor(100.0 * (n - r) / base)),
                    (", leaving out %s exact duplicate rows" % format(dup, ",")) if dup else "")
                head = "probably not %s: %s" % (noun, t["problem"])
            else:
                head = "%s %s an earlier value (%s)" % (
                    _n_values(r), "repeats" if r == 1 else "repeat",
                    "not matched against the engine's duplicate check" if engine is None else
                    "none in exact duplicate rows" if not dup else
                    ("in an exact duplicate row" if r == 1 else "all in exact duplicate rows") if dup == r else
                    "%s of them in exact duplicate rows" % format(dup, ","))
        elif amb and not u and not o:
            # the engine's own reason, in its own words: these are dates, read neither way round
            head = "%s could be day/month or month/day, and the column holds both orders" % _n_values(amb)
            tail = _analysis_tail(np.flatnonzero(m["ambiguous"]), engine)
        elif (u or amb) and not o:
            head = "%s can't be read as %s" % (_n_values(u), read_u)
            if amb:
                head += " and %s could be day/month or month/day" % format(amb, ",")
            tail = _analysis_tail(np.flatnonzero(m["unreadable"] | m.get("ambiguous", z)), engine)
        elif u:
            head = "%s can't be read as %s and %s %s %s" % (_n_values(u), read_u, format(o, ","),
                                                           "is" if o == 1 else "are", a.get("range"))
            tail = "%s; the tests changed no value" % _analysis_tail(
                np.flatnonzero(m["unreadable"]), engine, "count the unreadable %s as missing" % ("one" if u == 1 else "ones"))
        else:
            head = "%s %s %s" % (_n_values(o), "is" if o == 1 else "are", a.get("range"))
        if o and not t["signal"] and o / float(n) > OUT_OF_RANGE_SIGNAL:
            t["signal"] = True
            t["problem"] = "%s of %s values are %s, so it may not be %s" % (
                format(o, ","), format(n, ","), a.get("range"),
                ("a 0-%d percentage" % t.get("scale", 100)) if t["semantic_type"] == "percentage" else noun)
            head = t["problem"] if not u else "%s (%s)" % (head, t["problem"].split(", so ", 1)[-1])
        mix = t.get("mixed_scale")
        if mix and not t["signal"]:
            t["signal"] = True
            t["problem"] = ("%s values are written as fractions (between 0 and 1) and %s as percents (above 1): the "
                            "column mixes two scales" % (format(mix["fractions"], ","), format(mix["percents"], ",")))
            head = "%s; %s" % (head, t["problem"])
        t["action"] = "%s: %s; %s" % (head, eng, tail)
        t["brief"] = "%s: %s; %s" % (head, eng_short, tail)
    for t in tests:
        # a mixed column with nothing else wrong still says so, and the planner is told
        mix = t.get("mixed_scale")
        if mix and t["action"] == "passed":
            t["signal"] = True
            t["problem"] = ("%s values are written as fractions (between 0 and 1) and %s as percents (above 1): the "
                            "column mixes two scales" % (format(mix["fractions"], ","), format(mix["percents"], ",")))
            t["action"] = t["brief"] = t["problem"] + "; the analyses read it on the 0-100 scale; the tests changed no value"


def _zero_note_rows(rep: Dict[str, Any], zeros: Dict[str, Dict[str, Any]], off: Any = ()) -> None:
    """The Data tests card's note row for each level whose 0 the AI's analyses read as "no value"
    (PLACEHOLDER_ZERO_TYPES), right after that column's own test: a note, never a failure, and nothing the engine
    reads is changed (its table, its duplicate check and every download keep the zeros). A column whose tests the
    visitor turned off gets none (its row says so); its analyses read the zeros as missing all the same, as
    turning a test off changes no analysis."""
    off = set(str(x) for x in (off or []))
    rows = []
    for col, zi in zeros.items():
        if col in off:
            continue
        z, n = int(zi["zeros"]), int(zi["values"])
        what = ("note: %s in %s %s not counted by the AI's analyses (%s); the engine's own reading is unchanged and "
                "the tests changed no value" % (_n_values(z, "zero value"), col, "is" if z == 1 else "are", _zero_why(zi)))
        rows.append({"column": col, "semantic_type": "level", "test": "0 marks no value (a level's zeros between "
                     "non-zero values, in the pattern of a missing value)", "checked": n, "failed": 0, "examples": [],
                     "action": what, "brief": what,
                     "unreadable": 0, "ambiguous": 0, "out_of_range": 0, "repeated": 0, "unexpected": 0,
                     "misread": False, "signal": False, "private": None, "note": True, "zeros": z})
    if not rows:
        return
    k = rep.get("contracts")
    if not isinstance(k, dict):
        k = rep["contracts"] = {"tests": [], "cells_flagged": 0, "line": "table_row", "note": CONTRACT_NOTE}
    tests = k.setdefault("tests", [])
    for r in rows:
        at = max([i for i, t in enumerate(tests) if isinstance(t, dict) and t.get("column") == r["column"]] or [len(tests) - 1])
        tests.insert(at + 1, r)


def _flagged_cells(tests: List[Dict[str, Any]], aux: Dict[str, Any], df: Any, lines: Optional[List[int]],
                   engine: Optional[Dict[str, Any]], hide: Dict[str, str], clean: Any) -> Tuple[str, int]:
    """The download "values the data tests flagged": one row per failing cell, in the visitor's line order.
    Columns: the line (source_line, as the other downloads number the visitor's file; table_row for a file
    read reshaped), the column, the value (a flagged column's values never leave: a withheld column's read
    "value withheld", a coded column's "value coded"), the test it failed and what happened (set aside by the
    engine, counted as missing by the analyses, kept). `hide`: {column: "withhold" | "code"} by the file's
    names. Returns (CSV text, cells flagged); at most FLAGGED_CELLS_MAX rows."""
    import numpy as np
    kinds = (("unreadable", "can't be read"), ("ambiguous", "day and month could be either way round"),
             ("out_of_range", "out of range"), ("repeated", "repeats an earlier value"),
             ("unexpected", "unexpected value"))
    order = {t["column"]: i for i, t in enumerate(tests)}
    cells = []
    for t in tests:
        if t["action"] == "turned off by you" or not t["failed"]:
            continue
        col = t["column"]
        m = (aux.get(col) or {}).get("masks") or {}
        emp = (engine or {}).get("empty", {}).get(col, ())
        for key, word in kinds:
            for pos in np.flatnonzero(m.get(key, np.zeros(0, dtype=bool))):
                pos = int(pos)
                what = [word]
                if engine is None:
                    what.append("not matched: the engine read this file reshaped")
                elif pos in engine["aside"]:
                    what.append("set aside by the engine (%s)" % engine["aside"][pos])
                elif key == "unreadable" and pos in emp:
                    what.append("kept by the engine, value left empty")
                else:
                    what.append("kept by the engine")
                if key in ("unreadable", "ambiguous"):
                    what.append("counted as missing by the analyses" if engine is None or pos not in engine["aside"]
                                else "not read by the analyses")
                cells.append((lines[pos] if lines else pos + 1, order.get(col, 0), col,
                              str(df[col].iat[pos]), t["test"], "; ".join(what)))
    cells.sort(key=lambda c: (c[0], c[1]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["source_line" if lines else "table_row", "column", "value", "test", "what happened"])
    for ln, _o, col, val, test, what in cells[:FLAGGED_CELLS_MAX]:
        d = hide.get(col)
        w.writerow([ln, col, "value withheld" if d == "withhold" else "value coded" if d else clean(val), test, what])
    return buf.getvalue(), len(cells)


# ----------------------------------------------------------------------------- the AI's analyses
# Owner's design, step 3 (25 Sep 2026): the plan also says WHAT to compute, from a fixed menu; this
# code computes every number. A long temperature record wants its trend, its extremes and whether two
# sources agree, not only "the latest 12 months against the 12 before". These are descriptive: they
# are not graded by the change gate, and every card says so and how it was computed.
ANALYSIS_TYPES = ("trend", "extremes", "agreement", "rank", "share", "compare", "relationship", "distribution", "themes",
                  "predict")
ANALYSES_MAX = 6
_LEVEL_TYPES = ("level", "percentage", "log_scale", "rating", "ordinal", "duration", "other")


def _fmt(v: Any, sig: int = 3) -> str:
    """A number for a sentence: 3 significant digits, thousands separated, a true minus sign."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if v != v:
        return "n/a"
    a = abs(v)
    if a >= 1000:
        s = format(round(v), ",")
    elif a == 0:
        s = "0"
    else:
        import math
        dp = max(0, sig - 1 - int(math.floor(math.log10(a))))
        s = ("%.*f" % (min(dp, 6), v))
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    return s.replace("-", "−")


# The unit the AI plan gives a column is words for a sentence, and not every such word is a unit: a planner
# that does not know the currency writes "currency" (live run, 29 Sep 2026: "933 currency", "a gap of 283
# currency", in the analyses and then in the AI report), "local currency units", "units", "index" or
# "value". A word that names no actual unit is dropped and the number stands plain; an ISO code inside one
# ("currency (CAD)") is the unit; a currency symbol goes before the number ($933, -$5); "%" follows it
# closed up (5%); any other unit follows it after a space (12 USD, 31.2 kg). A percentage column always
# reads "%" (its fractions are shown x100, see _analysis_frame), and a difference between two percentages
# is in percentage points (_diff_amt).
_NO_UNIT_WORDS = frozenset(("currency", "currencies", "currency unit", "currency units", "units of currency",
                            "local currency", "local currency unit", "local currency units", "lcu",
                            "unknown currency", "unknown", "money", "monetary", "monetary unit", "monetary units",
                            "amount", "amounts", "value", "values", "unit", "units", "index", "indices",
                            "index points", "index value", "number", "numbers", "count", "counts", "n/a", "na",
                            "none", "-"))
_ISO_RE = re.compile(r"\b([A-Z]{3})\b")
# ISO 4217 currency codes: a three-capital word is a currency only when it is one (review, 29 Sep 2026: "LCU",
# the World Bank's "local currency units", printed as if it were a currency code)
_ISO_CURRENCIES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF
CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG
HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH
UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL""".split())


def _unit_parts(unit: Any) -> Tuple[str, str]:
    """(before the number, after it) for a plan column's unit; ("", "") for none."""
    import unicodedata
    u = " ".join(str(unit or "").split())
    if not u:
        return "", ""
    low = " ".join(re.sub(r"[()\[\]]", " ", u.lower()).split()).strip(".")
    iso = next((m for m in _ISO_RE.finditer(u) if m.group(1) in _ISO_CURRENCIES), None)
    if iso and (u.strip("()[] ") == iso.group(1) or any(w in low for w in ("currency", "money", "monetary"))):
        return "", " " + iso.group(1)
    if low in _NO_UNIT_WORDS or re.sub(r"\s*[(\[]?\blcu\b[)\]]?\s*", " ", low).strip() in _NO_UNIT_WORDS | {"", "current"} \
            or re.search(r"\blcu\b", low):
        return "", ""
    if u == "%" or low in ("percent", "per cent", "percentage", "pct"):
        return "", "%"
    if len(u) <= 4 and any(unicodedata.category(ch) == "Sc" for ch in u):
        return u, ""
    return "", " " + u


def _amt(v: Any, unit: Any, tail: bool = True) -> str:
    """_fmt(v) with the column's unit written the way a reader expects (see _unit_parts). tail=False
    leaves out a unit that follows the number: "between 10 and 20 kg", "between $10 and $20"."""
    pre, post = _unit_parts(unit)
    s = _fmt(v)
    if s == "n/a":
        return s
    if pre:
        s = ("\u2212" + pre + s[1:]) if s.startswith("\u2212") else pre + s
    return s + (post if tail else "")


def _diff_amt(v: Any, st: str, unit: Any) -> str:
    """A difference or a rate of change of a column: a percentage's in percentage points (5% to 7% is 2
    percentage points, not 2%), anything else in the column's own unit."""
    if st == "percentage":
        s = _fmt(v)
        return s if s == "n/a" else s + " percentage points"
    return _amt(v, unit)


def _hac_slope(t: Any, y: Any) -> Tuple[float, float, int]:
    """OLS slope of y on t with a Newey-West standard error (Bartlett weights, lag 4(n/100)^(2/9)):
    yearly values of a climate or price series lean on the year before, and a plain OLS interval
    would be too narrow. Returns (slope, se, lag)."""
    import numpy as np
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    n = len(t)
    X = np.column_stack([np.ones(n), t - t.mean()])
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    e = y - X @ beta
    lag = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    xe = X * e[:, None]
    S = xe.T @ xe
    for l in range(1, lag + 1):
        w = 1.0 - l / (lag + 1.0)
        G = xe[l:].T @ xe[:-l]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    return float(beta[1]), float(np.sqrt(max(V[1, 1], 0.0))), lag


def _tcrit(df: int) -> float:
    """The two-sided 97.5% t critical value, from the core's own distribution code (forecast.py:
    t_cdf through the incomplete beta, checked in tests/test_forecast_baseline.py). No scipy: it
    does not exist in Pyodide, and a value that differed between the browser and a local run
    would make two runs of the same file disagree. Bisection on the monotone CDF, 1e-6 in x."""
    from northledger.forecast import t_cdf
    d = max(int(df), 1)
    if d == 1:
        return 12.706204736432095  # tan(pi * 0.975); the exact closed form
    lo, hi = 0.0, 12.71
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, d) < 0.975:
            lo = mid
        else:
            hi = mid
    return float(0.5 * (lo + hi))


# The analyses' date column (reviews H2 and M2, 29 Sep 2026): ONLY the layout's date, else the column the plan
# gives the date ROLE (or the "date" a date_from_year step made of it). A column merely typed date with another
# role (a driver, metadata, a refund date) is never the axis: a review file whose order dates were 30% "TBD"
# got a trend drawn over its refund dates, which run the other way. The axis is a column the engine reads as
# dates, or as whole years (a year column the plan calls its date). The analyses read only the rows the engine
# kept, so every date they use is one the engine read; a kept row whose date is blank drops out of a time
# analysis, and its sentence says how many did.
# The minimums the analyses enforce, shared with the profile's analysis_limits so the planner is told the
# same numbers the analyses refuse by.
TREND_MIN_YEARS = 8               # trend: a least-squares line needs 8 complete years
EXTREMES_MIN_YEARS = 10           # extremes: the highest and lowest of 10 or more complete years
AGREEMENT_MIN_DATES = 12          # agreement: 12 dates where both series have a value
RANK_MIN_YEARS = 2                # rank (series side by side): a first and a last complete year
COMPARE_MIN_GROUP = 5             # compare: a group needs 5 values, and two groups are needed
RANK_MIN_ROWS = COMPARE_MIN_GROUP  # rank: an entry whose figure adds up or averages several rows needs 5 of them
# Groups ranked by an average (rank on a level or a rating, compare): the IMDb-style weighted rating. Each group is
# ranked by (n * its average + m * the average of every row in the ranking) / (n + m): its own n rows weighed against
# m rows at the overall average, so an average resting on a few rows is pulled toward the overall one and a large
# group keeps nearly its own (evaluation preflight, 30 Sep 2026: with the 5-row minimum in place, brands with 5 to 8
# five-star reviews still topped a ranking of 5,587 reviews). m is the file's own (final review, 30 Sep 2026: an
# empirical-Bayes m, never a fixed 10): the pooled variance of the rows within a group over the variance of the
# groups' true averages, by the method of moments on the groups with SHRINK_MIN_ROWS rows or more (the variance of
# their averages less the average of each one's sampling variance), rounded to whole rows and held to SHRINK_M_MIN to
# SHRINK_M_MAX; no spread between the groups beyond chance is SHRINK_M_MAX (the most weight on the overall average),
# and fewer than SHRINK_MIN_GROUPS such groups, or no spread at all, is SHRINK_M, and the sentence says so. The
# sentence states m. The table shows each group's own average and rows beside the weighted one; a ranking of totals,
# and a panel's own figures, are not weighted.
# Why 5 groups (final review, 30 Sep 2026: with 2 groups the estimate was 50, "no more than chance", in a third of
# files whose groups truly differ): the variance of the groups' true averages is estimated with one fewer degree of
# freedom than there are groups, so its relative error is about sqrt(2 / (groups - 1)): 100% with 3 groups, 71% with
# 5. Simulated (100 rows a group, true m 25 and 11, 2,000 files each), the estimate lands within a factor of 2 of the
# true m in 25 to 29% of files with 2 groups, 37 to 41% with 3 and 54 to 60% with 5, and reads "no more than chance"
# in 24 to 36%, 11 to 19% and 2 to 6%; 8 groups do better still (66 to 72%) but would leave most small files, a few
# brands or stores, on the set value.
SHRINK_M = 10                     # m when it cannot be estimated
SHRINK_M_MIN, SHRINK_M_MAX = 5, 50
SHRINK_MIN_ROWS = 5               # the groups m is estimated on have this many rows ...
SHRINK_MIN_GROUPS = 5             # ... and there are this many of them, at least
RELATIONSHIP_MIN_ROWS = 10        # relationship: 10 rows with both values
DISTRIBUTION_MIN_VALUES = 10      # distribution: 10 values
THEMES_MIN_TEXTS = 20             # themes: 20 non-empty texts
PREDICT_MIN_ROWS = 30             # predict: 30 complete rows ...
PREDICT_ROWS_PER_TERM = 10        # ... and 10 rows per model term
PREDICT_BLOCKS = 5                # forward-chained: 5 blocks in date order, the last 4 scored
YEARS_MIN_SHARE = 0.95            # a number column is the year axis when 95% of its numbers are whole years
# Zero as a placeholder (evaluation preflight, 30 Sep 2026: StatCan's daily USD rate carries 0 on the weekends before
# April 2022, and the AI plan's path read those 550 zeros as exchange rates: a long-run trend of 0.0721 CAD a year
# where the rates themselves give 0.0104, and "18.6% of the values are exactly zero"). A column the plan types as a
# level (a rate, price, index, balance or ratio) may use 0 for "no value", and then the AI's analyses read its zeros as
# missing (each sentence counts them and says why, the Data tests card says so in a note row). The engine's own input
# is never changed. A flow or a count (sales, orders, amounts), where 0 is real, is never read this way.
# The evidence, never the share of zeros alone (final review, 30 Sep 2026: that rule deleted 0% policy rates held for
# seven years, paid-off balances and stockouts): the zeros are at least 1% of the numbers and at least 95% of the others
# are positive, AND in date order (within each series: _series_groups, the same reading for the planner's profile and
# the analyses) most of them (80%) sit alone or two or three together between non-zero values, never in a run of 5 or
# more (5 dated zeros in a row are a real period of zero), AND either they are concentrated on one or two days of the
# week that hold few of the other values (the FX weekends) or the series picks up where it left off after them (the
# values on either side within 10% of each other, for 80% of the runs and at least 3 separate runs: one zero, or two,
# is never enough, final review of 30 Sep 2026): the pattern of a day with no value. Random zeros between values that
# jump about (paid-off balances, stockouts) are real. Only the zeros the evidence describes are read as no value (the
# note's words hold for every one of them); a zero at the start or end of a series, on a row with no date, or outside
# the pattern stays 0, and the note counts those apart.
PLACEHOLDER_ZERO_TYPES = ("level",)
PLACEHOLDER_ZERO_MIN_SHARE = 0.01
PLACEHOLDER_POSITIVE_SHARE = 0.95
PLACEHOLDER_RUN_MAX = 3            # a placeholder sits alone or in a run of 2 or 3 between non-zero values
PLACEHOLDER_REAL_RUN = 5           # a run of 5 or more zeros in date order is a real period of zero
PLACEHOLDER_INTERLEAVED = 0.80     # the share of the zeros that must sit in such short runs
PLACEHOLDER_DAYS_SHARE = 0.80      # the zeros on their one or two commonest weekdays, at least
PLACEHOLDER_DAYS_OTHER = 0.50      # ... the other values on those weekdays, at most
PLACEHOLDER_DAYS_MIN = 5           # dated zeros needed to read a weekday pattern
PLACEHOLDER_RESUME = 0.10          # the values on either side of a run within 10% of each other
PLACEHOLDER_RESUME_SHARE = 0.80    # ... for this share of the runs
PLACEHOLDER_RESUME_RUNS = 3        # ... and for this many separate runs, at least
SERIES_KEY_SHARE = 0.95            # a text column names the series when, with the date, it tells 95% of the rows apart
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_TIME_ANALYSES = ("trend", "extremes", "agreement")
_NUMERIC_ANALYSES = ("trend", "extremes", "agreement", "rank", "share", "compare", "relationship", "distribution")


def _full_years(dates: Any) -> Tuple[List[int], List[int]]:
    """Calendar years with values, and the complete ones (review, 29 Sep 2026: a year with eleven months is not a
    complete calendar year). Values monthly or more often: all 12 months of the year hold a value; quarterly:
    all 4 quarters; yearly or sparser: every year with a value. Coverage of the calendar, never a count of
    rows: a growing business's early years hold fewer rows and are still complete."""
    import pandas as pd
    d = pd.Series(dates).dropna()
    if d.empty:
        return [], []
    n = d.dt.year.value_counts().sort_index()
    years = [int(y) for y in n.index]
    steps = d.drop_duplicates().sort_values().diff().dropna()
    step = steps.median() if len(steps) else None
    if step is None or step >= pd.Timedelta(days=300):
        return years, years
    periods, need = (d.dt.month, 12) if step < pd.Timedelta(days=60) else (d.dt.quarter, 4)
    held = periods.groupby(d.dt.year).nunique()
    return years, [y for y in years if int(held.get(y, 0)) == need]


def _zero_gate(values: Any) -> bool:
    """The first test of _zero_shape, alone (cheap): the zeros are at least PLACEHOLDER_ZERO_MIN_SHARE of the numbers
    and at least PLACEHOLDER_POSITIVE_SHARE of the others are positive."""
    import numpy as np
    a = np.asarray(values, dtype=float)
    nums = a[~np.isnan(a)]
    zeros = int((nums == 0).sum())
    other = nums[nums != 0]
    return bool(zeros > 0 and zeros >= PLACEHOLDER_ZERO_MIN_SHARE * len(nums) and len(other) > 0
                and float((other > 0).mean()) >= PLACEHOLDER_POSITIVE_SHARE)


def _series_groups(texts: List[Tuple[str, Any]], dates: Any) -> Optional[Any]:
    """The series a level's zeros are read within, in date order, the same way for the planner's profile
    (zeros_missing_if_level) and for the analyses (final review, 30 Sep 2026: the profile read a panel's rows across
    its entities, the analyses within each, so the hint said no where the analyses dropped the zeros). When the dates
    repeat (fewer than SERIES_KEY_SHARE of the dated rows have a date of their own), the text column that, with the
    date, tells SERIES_KEY_SHARE of the dated rows apart, the one with the fewest values (then the first listed): its
    values, one per row, as the groups. None when the dates alone tell the rows apart (one series) or no column does.
    texts: (name, the column's values as text, one per row) for each text column the reading may use."""
    import pandas as pd
    if dates is None:
        return None
    d = pd.to_datetime(pd.Series(list(dates)), errors="coerce")
    ok = d.notna().to_numpy()
    n = int(ok.sum())
    if n < 2 or float((~d[ok].duplicated(keep=False)).mean()) >= SERIES_KEY_SHARE:
        return None
    dd = d[ok].to_numpy()
    best: Optional[Tuple[Tuple[int, int], Any]] = None
    for pos, (_name, vals) in enumerate(texts):
        g = pd.Series(vals, dtype=object)
        if len(g) != len(d):
            continue
        g = g.where(g.notna(), "").astype(str)
        k = int(g[ok].nunique())
        if k < 2 or k > n // 2:
            continue
        pairs = pd.DataFrame({"d": dd, "g": g[ok].to_numpy()})
        if float((~pairs.duplicated(keep=False)).mean()) >= SERIES_KEY_SHARE and (best is None or (k, pos) < best[0]):
            best = ((k, pos), g.to_numpy())
    return best[1] if best is not None else None


def _zero_shape(values: Any, dates: Any = None, groups: Any = None) -> Tuple[int, Optional[Dict[str, Any]]]:
    """(how many of the numbers are exactly 0, the evidence that 0 is a placeholder for "no value", or None when it
    is a real 0): see PLACEHOLDER_ZERO_TYPES. values: the numbers read (NaN is not a number and is left out); dates:
    the rows' dates (the order the runs are read in, and the weekdays), or None for the rows' own order; groups: the
    rows' series (runs are read within each: _series_groups), or None. The evidence: {"kind": "weekdays" or
    "resumes", "days": the weekday names (weekdays), "zeros": how many zeros are read as no value, "mask": a boolean
    array over `values` marking exactly those, "kept": {"edge", "undated", "other"}: the other zeros, which stay 0}.
    Only the zeros the evidence describes are read as no value (final review, 30 Sep 2026: every zero of the column
    was, the ones the note's words did not fit too): weekdays, the zeros on those days in a short run between
    non-zero values; resumes, the zeros of a short run between non-zero values that pick up where they left off. A
    zero at the start or the end of a series (edge), on a row with no date (undated), or anywhere else (other) stays
    0 and is counted apart."""
    import numpy as np
    import pandas as pd
    a = np.asarray(values, dtype=float)
    ok = ~np.isnan(a)
    zeros = int((a[ok] == 0).sum())
    if not _zero_gate(a):
        return zeros, None
    df = pd.DataFrame({"v": a, "i": np.arange(len(a))})
    undated = 0
    if dates is not None:
        df["d"] = pd.to_datetime(pd.Series(list(dates)), errors="coerce").to_numpy()
        undated = int((ok & (a == 0) & df["d"].isna().to_numpy()).sum())
    df["g"] = ["" if (x is None or (isinstance(x, float) and x != x)) else str(x) for x in groups] \
        if groups is not None else ""
    df = df[ok]
    if dates is not None:
        df = df[df["d"].notna()]
    df = df.sort_values(["g", "d", "i"] if dates is not None else ["g", "i"], kind="mergesort")
    # each run of zeros: (its length, the value before, the value after, its rows' places in `values`)
    runs: List[Tuple[int, Optional[float], Optional[float], List[int]]] = []
    for _g, part in df.groupby("g", sort=True):
        v = part["v"].to_numpy(dtype=float)
        at = part["i"].to_numpy(dtype=int)
        i = 0
        while i < len(v):
            if v[i] != 0:
                i += 1
                continue
            j = i
            while j < len(v) and v[j] == 0:
                j += 1
            runs.append((j - i, float(v[i - 1]) if i > 0 else None, float(v[j]) if j < len(v) else None,
                         [int(x) for x in at[i:j]]))
            i = j
    if not runs or max(r[0] for r in runs) >= PLACEHOLDER_REAL_RUN:
        return zeros, None
    short = [r for r in runs if r[0] <= PLACEHOLDER_RUN_MAX and r[1] is not None and r[2] is not None
             and r[1] > 0 and r[2] > 0]
    if sum(r[0] for r in short) < PLACEHOLDER_INTERLEAVED * sum(r[0] for r in runs):
        return zeros, None
    edge = int(sum(r[0] for r in runs if r[1] is None or r[2] is None))
    drop: List[int] = []
    kind, days = "", []
    if dates is not None:
        dow = pd.Series(df["d"].dt.dayofweek.to_numpy(), index=df["i"].to_numpy())
        wz = df.loc[df["v"] == 0, "d"].dt.dayofweek.value_counts()
        wo = df.loc[df["v"] != 0, "d"].dt.dayofweek
        if int(wz.sum()) >= PLACEHOLDER_DAYS_MIN and len(wo):
            order = sorted(wz.index.tolist(), key=lambda k: (-int(wz[k]), k))
            for k in (1, 2):
                top = sorted(order[:k])
                on = int(sum(int(wz[d]) for d in top))
                if on >= PLACEHOLDER_DAYS_SHARE * int(wz.sum()) and float(wo.isin(top).mean()) <= PLACEHOLDER_DAYS_OTHER:
                    kind, days = "weekdays", [_WEEKDAYS[d] for d in top]
                    drop = [p for r in short for p in r[3] if int(dow[p]) in top]
                    break
    if not kind:
        resumed = [r for r in short if abs(r[2] - r[1]) <= PLACEHOLDER_RESUME * max(r[1], r[2])]
        if len(resumed) >= PLACEHOLDER_RESUME_RUNS and len(resumed) >= PLACEHOLDER_RESUME_SHARE * len(short):
            kind = "resumes"
            drop = [p for r in resumed for p in r[3]]
    if not kind or not drop:
        return zeros, None
    mask = np.zeros(len(a), dtype=bool)
    mask[drop] = True
    return zeros, {"kind": kind, "days": days, "zeros": len(drop), "mask": mask,
                   "kept": {"edge": edge, "undated": undated, "other": int(zeros - len(drop) - edge - undated)}}


def _zero_word(col: Any, unit: Any) -> str:
    """What a value of the column is, for "a rate of 0 is a placeholder": rate, price, index, balance or ratio when
    the column's name or unit says so (a unit "per" another, CAD per USD, is a rate), else value."""
    toks = set(re.split(r"[^a-z]+", ("%s %s" % (col, unit or "")).lower()))
    for w, forms in (("rate", ("rate", "rates")), ("price", ("price", "prices")), ("index", ("index", "indices", "indexes")),
                     ("balance", ("balance", "balances")), ("ratio", ("ratio", "ratios"))):
        if toks & set(forms):
            return w
    if "per" in set(re.split(r"[^a-z]+", str(unit or "").lower())) or "/" in str(unit or ""):
        return "rate"
    return "value"


def _zero_plural(w: str) -> str:
    return {"index": "index values", "value": "values"}.get(w, w + "s")


def _zero_why(zi: Dict[str, Any]) -> str:
    """The evidence that 0 marks "no value" in words, true of every zero read so (_zero_shape's mask): "they fall on
    weekends between non-zero rates, the pattern of a day with no value" or "they sit alone or two or three together
    between non-zero rates that pick up where they left off, the pattern of a missing value"; then the zeros that stay
    0, counted apart: "; 3 other zero values stay 0 (1 at the start or end of a series, 2 on rows with no date)"."""
    w = _zero_plural(str(zi.get("word") or "value"))
    ev = zi.get("evidence") or {}
    if ev.get("kind") == "weekdays":
        days = list(ev.get("days") or [])
        when = "weekends" if days == ["Saturday", "Sunday"] else _listed([d + "s" for d in days])
        text = "they fall on %s between non-zero %s, the pattern of a day with no value" % (when, w)
    else:
        text = ("they sit alone or two or three together between non-zero %s that pick up where they left off, the "
                "pattern of a missing value" % w)
    kept = ev.get("kept") or {}
    parts = [(int(kept.get("edge") or 0), "%s at the start or end of a series"),
             (int(kept.get("undated") or 0), "%s on %s with no date"),
             (int(kept.get("other") or 0), "%s outside that pattern")]
    total = sum(n for n, _f in parts)
    if total > 0:
        words = [(f % (format(n, ","), "a row" if n == 1 else "rows") if "%s on %s" in f else f % format(n, ","))
                 for n, f in parts if n > 0]
        text += "; %s other zero %s 0 (%s)" % (format(total, ","), "value stays" if total == 1 else "values stay",
                                              ", ".join(words))
    return text


def _zero_note(col: str, zi: Dict[str, Any]) -> str:
    """"550 zero values in VALUE are not counted: they fall on weekends between non-zero rates, the pattern of a day
    with no value": the count and the evidence, never a bare "a rate of 0 is a placeholder"; the zeros that stay 0
    are counted apart (_zero_why)."""
    n = int(zi["zeros"])
    return "%s in %s %s not counted: %s" % (_n_values(n, "zero value"), col, "is" if n == 1 else "are", _zero_why(zi))


def _date_candidates(plan: Dict[str, Any], layout: Optional[Dict[str, Any]], columns: Any) -> List[str]:
    """The one column the time analyses may read: the layout's date, else the plan's date ROLE, or "date"
    when a date_from_year step turned that column into "date". Never a column merely typed date."""
    if layout and layout.get("date_column"):
        return [layout["date_column"]]
    pcs = [c for c in plan.get("columns") or [] if isinstance(c, dict) and c.get("name")]
    conv = [o.get("column") for o in plan.get("operations") or [] if isinstance(o, dict) and o.get("op") == "date_from_year"]
    role = next((c["name"] for c in pcs if c.get("role") == "date"), None)
    if role and role in conv and role not in columns and "date" in columns:
        return ["date"]                                   # date_from_year turned the plan's column into "date"
    if role:
        return [role]
    return ["date"] if conv and "date" in columns else []


def _series_kind(s: Any) -> str:
    import pandas as pd
    if pd.api.types.is_bool_dtype(s):
        return "text"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    return "text"


def _texts_of(df: Any, col: str) -> Any:
    """A column's values as labels: text as the engine kept it, a whole number without ".0", a date as
    YYYY-MM-DD, a missing value as ""."""
    import pandas as pd
    s = df[col]
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime("%Y-%m-%d").fillna("")
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return s.map(lambda v: "" if v != v else (str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else repr(float(v))))
    return s.astype(object).where(s.notna(), "").astype(str).str.strip()


def _who(name: Any, decision: Optional[str]) -> str:
    """A flagged column in an analysis refusal: a withheld column is never named."""
    return "a column you withheld" if decision == "withhold" else "%s (coded as personal data)" % name


def _analysis_frame(ctx: Dict[str, Any], plan: Dict[str, Any], layout: Optional[Dict[str, Any]]):
    """The rows the engine kept, as it read them: (frame, date column, entity column, why there is no date
    column, info). The frame is the engine's cleaned table (the rows behind downloads.clean_csv) under the
    file's own column names, without every column the visitor withheld or coded; a percentage column is in
    percent on the scale its data test chose (_pct_reading); the date column is the engine's dates (or whole
    years read as each year's last day). info: {"kept", "aside", "undated" (kept rows with no date),
    "unread" {column: kept cells the engine could not read as numbers}, "kinds" {column: number|date|text},
    "mixed" {column: fractions and percents side by side}, "zeros" {column: {"zeros", "values", "word"}} (a level
    whose 0 is a placeholder: its zeros are missing in the frame, see PLACEHOLDER_ZERO_TYPES)}. ctx: {"reading",
    "clean", "hide" {landed: decision}, "pct" {column: _pct_reading}}."""
    import numpy as np
    import pandas as pd
    from northledger.clean import QUARANTINE_COL
    R, clean, hide = ctx["reading"], ctx["clean"], ctx.get("hide") or {}
    keep = [c for c in clean.columns if c != QUARANTINE_COL and c not in hide]
    df = clean[keep].copy()
    df.columns = [R.header(c) for c in keep]
    pos = np.asarray([int(i) for i in clean.index], dtype=int)
    info: Dict[str, Any] = {"kept": int(len(df)), "aside": int(R.n - len(df)), "undated": 0, "unread": {},
                            "kinds": {h: _series_kind(df[h]) for h in df.columns}, "mixed": {}}
    for c in keep:
        h = R.header(c)
        if info["kinds"][h] == "number":
            fill = R.filled(c)[pos] if len(pos) else np.zeros(0, dtype=bool)
            n_bad = int((fill & df[h].isna().to_numpy()).sum())
            if n_bad:
                info["unread"][h] = n_bad
    for h, pr in (ctx.get("pct") or {}).items():
        if h in df.columns and info["kinds"].get(h) == "number":
            df[h] = np.asarray(pr["percent"], float)[pos] if len(pos) else df[h]
            if pr.get("mixed"):
                info["mixed"][h] = (pr["n_frac"], pr["n_pct"])
    df.index = pd.RangeIndex(len(df))
    date, why = None, ""
    headers = list(R.land) or [R.header(c) for c in R.values.columns]
    cands = _date_candidates(plan, layout, headers)
    if not len(df):
        why = "the engine kept no rows"
    elif not cands:
        why = "the plan names no date column"
    else:
        c = cands[0]
        land = R.landed(c)
        if land is not None and land in hide:
            why = ("the plan's date column is a column you withheld" if hide[land] == "withhold"
                   else "the plan's date column %s is coded as personal data" % c)
        elif c not in df.columns:
            why = "the plan's date column %s is not in the file the engine read" % c
        elif not df[c].notna().any():
            why = "the plan's date column %s is blank" % c
        elif info["kinds"][c] == "date":
            date = c
        elif info["kinds"][c] == "number":
            yd = _year_dates(df[c])
            n_num = int(df[c].notna().sum())
            if n_num and yd.notna().sum() >= YEARS_MIN_SHARE * n_num:
                df[c] = yd.to_numpy()
                date = c
            else:
                why = "the engine reads %s as numbers, not dates or whole years" % c
        else:
            why = "the engine reads %s as text, not dates" % c
    if date:
        info["undated"] = int(pd.to_datetime(df[date], errors="coerce").isna().sum())
    roles = {str(c.get("role")): c["name"] for c in plan.get("columns", []) if c.get("name")}
    ent = None
    for r in ("entity", "geography", "segment"):
        c = roles.get(r)
        if c in df.columns and c != date and _texts_of(df, c).replace("", np.nan).nunique() > 1:
            ent = c
            break
    # a level whose 0 is a placeholder (PLACEHOLDER_ZERO_TYPES, on its evidence: read in date order within each
    # series, _series_groups, as the planner's profile reads it): the zeros the evidence describes read as missing
    # here, in the analyses' own copy of the rows, and no other; the engine's table and every download keep them
    info["zeros"] = {}
    days = df[date] if date else None
    series: List[Any] = []                   # the rows' series, worked out once, when a level needs it
    for h in list(df.columns):
        if h == date or info["kinds"].get(h) != "number" or h in info["mixed"]:
            continue
        st, unit = _col_type(plan, h, layout)
        if st not in PLACEHOLDER_ZERO_TYPES:
            continue
        v = pd.to_numeric(df[h], errors="coerce").astype(float)
        if not _zero_gate(v.to_numpy()):
            continue
        if not series:
            series.append(_series_groups([(c, _texts_of(df, c).to_numpy()) for c in df.columns
                                          if c != date and info["kinds"].get(c) == "text"], days))
        _zeros, ev = _zero_shape(v.to_numpy(), days, series[0])
        if ev:
            vv = v.to_numpy().copy()
            vv[ev["mask"]] = np.nan
            df[h] = vv
            info["zeros"][h] = {"zeros": int(ev["zeros"]), "values": int(v.notna().sum()), "word": _zero_word(h, unit),
                                "evidence": {k: x for k, x in ev.items() if k != "mask"}}
    return df, date, ent, why, info


def _resolve(names: Any, df: Any, layout: Optional[Dict[str, Any]], date: Optional[str], ent: Optional[str]) -> List[str]:
    """Plan names as the frame's numeric columns. After long_to_wide a series is a column named by its
    value (GCAG), and the old value column (Mean) stands for every series. A plan that names a
    dimension value (Export, Import) on a long table names every series of that kind at once: the
    series names carry the dimension (united_states_export), so a word matching many series
    resolves to all of them (capped), and the analysis ranks or trends them side by side."""
    cols = [c for c in df.columns if c not in (date, ent)]
    low = {str(c).lower(): c for c in cols}
    out: List[str] = []
    for n in (names or [])[:8]:
        n = str(n)
        if n in cols:
            out.append(n)
        elif n.lower() in low:
            out.append(low[n.lower()])
        elif layout and n in (layout.get("value_column"), layout.get("series_column")):
            out.extend(layout.get("order") or [])
        else:
            hit = [c for c in cols if n.lower() in str(c).lower() or str(c).lower() in n.lower()]
            if len(hit) == 1:
                out.append(hit[0])
            elif layout and len(hit) > 1 and len(hit) <= 12:
                # a dimension value the long table was reshaped on (Export, Import): every
                # series of that kind, in the layout's own order
                order = layout.get("order") or []
                out.extend([c for c in order if c in hit] or hit)
    seen, res = set(), []
    for c in out:
        if c not in seen and c in df.columns:
            seen.add(c)
            res.append(c)
    return res


def _col_type(plan: Dict[str, Any], col: str, layout: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(semantic type, unit) the plan gave a column; a reshaped series takes its value column's."""
    by = {c["name"]: c for c in plan.get("columns", []) if c.get("name")}
    c = by.get(col) or (by.get(layout.get("value_column")) if layout else None) or {}
    unit = str(c.get("unit") or "")
    if layout and col not in by and len(layout.get("units") or []) > 1:
        unit = ""                        # series in several units: one unit for all of them would be wrong
    st = str(c.get("semantic_type") or "other")
    if st == "percentage":
        unit = "%"                       # the frame holds it in percent (_pct_reading), so every value is in %
    return st, unit


def _how(st: str) -> str:
    """A flow or a count adds up over a year (its total); anything else is a level (its average)."""
    return "sum" if st in ("flow_amount", "count") else "mean"


def _period_values(df: Any, date: str, ent: Optional[str], col: str, how: str):
    """(values by complete calendar year, what a value is). how "sum": the year's total, every row of the year
    added (a flow or a count); "mean": the average of the year's rows (a level). Review H3 (29 Sep 2026): the
    total used to be a sum of each entity's yearly MEAN, so monthly sales by 3 regions rose "11,906 a year"
    where the yearly totals rose 142,875. With an entity column the year is over one steady set of entities,
    those with a value in the latest year and in 95% of the years since most of them began (so a trend is not
    the arrival of new reporters), and a year counts only when at least 90% of them report in it."""
    import pandas as pd
    word = "yearly total" if how == "sum" else "yearly average"
    s = pd.DataFrame({"d": pd.to_datetime(df[date], errors="coerce"), "v": _col_nums(df, col)})
    if ent:
        s["e"] = _texts_of(df, ent)
    s = s.dropna(subset=["d", "v"])
    if ent:
        s = s[s["e"] != ""]
    if s.empty:
        return pd.Series(dtype=float), "none"
    s["y"] = s["d"].dt.year
    if not ent:
        _all, full = _full_years(s["d"])
        g = s.groupby("y")["v"]
        agg = g.sum() if how == "sum" else g.mean()
        return agg[agg.index.isin(full)].astype(float), word
    pres = s.groupby(["y", "e"]).size().unstack("e")
    last = pres.index.max()
    now = pres.columns[pres.loc[last].notna()]
    if len(now) == 0:
        return pd.Series(dtype=float), "none"
    frac = pres[now].notna().mean(axis=1)
    start = frac[frac >= 0.9].index.min() if (frac >= 0.9).any() else pres.index.min()
    ww = pres.loc[pres.index >= start, now]
    steady = list(ww.columns[ww.notna().mean() >= 0.95])
    if not steady:
        return pd.Series(dtype=float), "none"
    t = s[s["e"].isin(steady) & (s["y"] >= start)]
    reporting = t.groupby("y")["e"].nunique()
    _all, full = _full_years(t["d"])
    ok = [y for y in full if reporting.get(y, 0) >= 0.9 * len(steady)]
    g = t.groupby("y")["v"]
    agg = g.sum() if how == "sum" else g.mean()
    what = "the %s over the %d %s entries that report every year from %d" % (word, len(steady), ent, int(start))
    return agg[agg.index.isin(ok)].astype(float), what


def _a_trend(df, date, ent, cols, plan, layout) -> Dict[str, Any]:
    """The long-run trend of each named series' complete calendar years (T1, wave 4: plan/WAVE4-A-DESIGN.md 3).
    The slope, its 95% range and the direction come from nl_inference.trend_test (Prais-Winsten AR(1) GLS, a
    parametric bootstrap under no trend, a random-walk screen); the Newey-West range it replaces (_hac_slope, kept
    as the size simulation's reference) found a trend in 22% to 50% of 9-year series that had none
    (tools/sim_trend_size.py). A direction is claimed ("rose", "fell") only on the test's verdict, and the method
    text quotes the simulated size of the condition nearest the lead series. `test` is the lead series' record,
    `tests` every series'."""
    import numpy as np
    import nl_inference as _ni
    rows, lines, sentences, fits, tests = [], [], [], [], []
    grains = []
    pword = "year"
    for col in cols[:4]:
        st, unit = _col_type(plan, col, layout)
        y, grain = _period_values(df, date, ent, col, _how(st))
        if len(y) < TREND_MIN_YEARS:
            continue
        grains.append(grain)
        yrs = np.asarray(y.index, float)
        rec = _ni.trend_test(yrs, y.values)
        rec["series"] = col
        tests.append(rec)
        b = float(rec["slope"]) if rec["slope"] is not None else float(np.polyfit(yrs, y.values, 1)[0])
        per = 10.0 if yrs.max() - yrs.min() >= 20 else 1.0
        pword = "decade" if per == 10.0 else "year"
        span_txt = "%d to %d" % (int(yrs.min()), int(yrs.max()))
        lo, hi = ((rec["ci"][0] * per, rec["ci"][1] * per) if rec["ci"][0] is not None else (None, None))
        rng = ("95%% range %s to %s" % (_fmt(lo), _fmt(hi))) if lo is not None else "no range"
        if rec["why"] == "exact_line":
            rng = "every year lies on the line"
        recent = None
        if yrs.max() - yrs.min() >= 60:
            m = yrs >= yrs.max() - 29
            rr = _ni.trend_test(yrs[m], y.values[m])
            if rr["slope"] is not None:
                recent = (rr, int(yrs[m].min()))
        what = ("its %s" % grain) if not ent else grain
        v = rec["verdict"]
        if v in ("rising", "falling"):
            text = ("%s rose by %s per %s over %s (%s; %s)" if v == "rising" else
                    "%s fell by %s per %s over %s (%s; %s)") % (
                col, _diff_amt(abs(b * per), st, unit), pword, span_txt, what, rng)
        elif v == "no_settled_direction" and rec["why"] == "constant":
            text = "%s is the same in every year over %s (%s)" % (col, span_txt, what)
        elif v == "no_settled_direction":
            # the range includes no change at all: no direction is claimed (review M8, 29 Sep 2026)
            text = ("%s shows no clear rise or fall over %s: %s moves by %s per %s, with a 95%% range of %s to %s, "
                    "which includes no change" % (col, span_txt, what, _diff_amt(b * per, st, unit), pword,
                                                  _fmt(lo), _fmt(hi)))
        else:
            why = ("a series that wanders with no trend at all (a random walk) moves this steadily in about %s of "
                   "100 cases like this one" % _fmt(100.0 * float(rec["p_random_walk"]), 2)
                   if rec["why"] == "random_walk" else
                   "the test's false-alarm rate on simulated series like this one is above the 7.5% it is held to"
                   if rec["why"] == "size_above_limit" else
                   "the test's false-alarm rate is not measured for series like this one"
                   if rec["why"] == "size_not_measured" else "no test applies to these values")
            text = ("%s moves by %s per %s over %s (%s; %s), but no direction is claimed: %s"
                    % (col, _diff_amt(b * per, st, unit), pword, span_txt, what, rng, why))
        if recent:
            rr, r0 = recent
            text += "; since %d the pace is %s per decade (%s to %s)" % (
                r0, _diff_amt(rr["slope"] * per, st, unit), _fmt(rr["ci"][0] * per), _fmt(rr["ci"][1] * per))
            if rr["ci"][0] is not None and hi is not None and rr["ci"][0] * per > hi:
                text += ", faster than the whole record"
        sentences.append(text + ".")
        rows.append([col, span_txt, str(len(y)), _fmt(b * per), ("%s to %s" % (_fmt(lo), _fmt(hi))) if lo is not None
                     else "", (_fmt(recent[0]["slope"] * per) if recent else "")])
        lines.append({"name": col, "x": [int(v) for v in yrs], "y": [float(v) for v in y.values]})
        c0 = float(np.mean(y.values)) - b * float(np.mean(yrs))
        fits.append({"name": col + " trend", "x0": int(yrs.min()), "x1": int(yrs.max()),
                     "y0": c0 + b * yrs.min(), "y1": c0 + b * yrs.max()})
        if recent:
            m = yrs >= recent[1]
            rb = float(recent[0]["slope"])
            c1 = float(np.mean(y.values[m])) - rb * float(np.mean(yrs[m]))
            fits.append({"name": col + " since %d" % recent[1], "x0": recent[1], "x1": int(yrs.max()),
                         "y0": c1 + rb * recent[1], "y1": c1 + rb * yrs.max(), "recent": True})
    if not sentences:
        return {"refused": "trend: no series with %d or more complete years of values" % TREND_MIN_YEARS}
    if len(cols) > 4:
        sentences.append("Lines are drawn for the first 4 of the %d series named." % len(cols))
    short = _short_labels([ln["name"] for ln in lines])
    for ln in lines:
        ln["name"] = short.get(ln["name"], ln["name"])
    for f in fits:
        base = next((k for k in short if f["name"].startswith(k)), None)
        if base:
            f["name"] = short[base] + f["name"][len(base):]
    return {"type": "trend", "title": "Long-run trend", "sentence": " ".join(sentences),
            "method": "A line through each complete calendar year's value (%s), fitted allowing for one year leaning on "
                      "the year before (Prais-Winsten); its 95%% range and its direction come from %s simulated series "
                      "with no trend and the same momentum, and a direction is claimed only when the line is steeper "
                      "than 95%% of them and than 95%% of random walks. %s"
                      % ("; ".join(dict.fromkeys(grains)), format(_ni.TREND_B, ","), _ni.size_words(tests[0])),
            "table": {"cols": ["Series", "Years", "Points", "Change per " + pword, "95% range", "Last 30 years"], "rows": rows},
            "chart": {"kind": "line", "x_label": "year", "series": lines, "fits": fits},
            "test": tests[0], "tests": tests}


def _a_extremes(df, date, ent, cols, plan, layout) -> Dict[str, Any]:
    sentences, rows, bars, grains = [], [], [], []
    for col in cols[:2]:
        st, unit = _col_type(plan, col, layout)
        y, grain = _period_values(df, date, ent, col, _how(st))
        if len(y) < EXTREMES_MIN_YEARS:
            continue
        grains.append(grain)
        top = y.sort_values(ascending=False).head(5)
        low = y.sort_values().head(3)
        recent10 = sum(1 for k in top.index if k >= y.index.max() - 9)
        sentences.append("Highest %s years (%s): %s; lowest: %s.%s" % (
            col, grain, ", ".join("%d (%s)" % (k, _amt(v, unit)) for k, v in top.items()),
            ", ".join("%d (%s)" % (k, _amt(v, unit)) for k, v in low.items()),
            ((" All 5 fall in the last 10 of %d years." % len(y)) if recent10 == 5 else
             (" %d of the 5 fall in the last 10 of %d years." % (recent10, len(y))) if recent10 >= 3 else "")))
        for k, v in top.items():
            rows.append([col, str(k), _fmt(v), "highest"])
        for k, v in low.items():
            rows.append([col, str(k), _fmt(v), "lowest"])
        if not bars:
            bars = [{"label": str(k), "value": float(v)} for k, v in top.items()]
    if not sentences:
        return {"refused": "extremes: no series with %d or more complete years" % EXTREMES_MIN_YEARS}
    return {"type": "extremes", "title": "Highest and lowest years", "sentence": " ".join(sentences),
            "method": "Each complete calendar year's value (%s), ranked." % "; ".join(dict.fromkeys(grains)),
            "table": {"cols": ["Series", "Year", "Value", "Rank"], "rows": rows},
            "chart": {"kind": "bars", "series": bars}}


def _a_agreement(df, date, ent, cols, plan, layout) -> Dict[str, Any]:
    import numpy as np
    import pandas as pd
    if len(cols) < 2:
        return {"refused": "agreement: needs two series"}
    a, b = cols[0], cols[1]
    d = pd.to_datetime(df[date], errors="coerce")
    s = pd.DataFrame({"d": d, "a": _col_nums(df, a), "b": _col_nums(df, b)}).dropna()
    if len(s) < AGREEMENT_MIN_DATES:
        return {"refused": "agreement: fewer than %d dates where both have a value" % AGREEMENT_MIN_DATES}
    diff = s["a"] - s["b"]
    r = float(np.corrcoef(s["a"], s["b"])[0, 1])
    i = int(diff.abs().values.argmax())
    sd = float(diff.std())
    steady = sd < 0.25 * abs(float(diff.mean())) if diff.mean() != 0 else False
    st, unit = _col_type(plan, a, layout)
    text = ("Over %s shared dates (%s to %s), %s runs %s %s than %s on average, and they move together "
            "(correlation %s); the widest gap was %s on %s." % (
                format(len(s), ","), s["d"].min().strftime("%Y-%m"), s["d"].max().strftime("%Y-%m"), a,
                _diff_amt(abs(diff.mean()), st, unit), "higher" if diff.mean() > 0 else "lower", b, _fmt(r),
                _diff_amt(float(diff.iloc[i]), st, unit), s["d"].iloc[i].strftime("%Y-%m")))
    if steady:
        text += " The gap is nearly constant (its spread is %s), the mark of two series measured from different baselines rather than disagreeing." % _fmt(sd)
    ya = s.groupby(s["d"].dt.year)[["a", "b"]].mean()
    return {"type": "agreement", "title": "Do %s and %s agree?" % (a, b), "sentence": text,
            "method": "Dates where both series have a value; difference = %s minus %s; Pearson correlation." % (a, b),
            "table": {"cols": ["Shared dates", "Mean difference", "Spread of difference", "Correlation", "Widest gap"],
                      "rows": [[format(len(s), ","), _fmt(diff.mean()), _fmt(sd), _fmt(r), "%s (%s)" % (_fmt(float(diff.iloc[i])), s["d"].iloc[i].strftime("%Y-%m"))]]},
            "chart": {"kind": "line", "x_label": "year", "series": [
                {"name": a, "x": [int(k) for k in ya.index], "y": [float(v) for v in ya["a"]]},
                {"name": b, "x": [int(k) for k in ya.index], "y": [float(v) for v in ya["b"]]}], "fits": []}}


def _short_labels(names: List[str]) -> Dict[str, str]:
    """Chart labels without the words every series shares (', daily average'); tables keep full names."""
    names = [str(n) for n in names]
    if len(names) < 2:
        return {n: n for n in names}
    rev = [n[::-1] for n in names]
    k = len(os.path.commonprefix(rev))
    suf = names[0][len(names[0]) - k:] if k else ""
    cut = suf.find(",") if "," in suf else (suf.find(" ") if suf.startswith(" ") else -1)
    suf = suf[cut:] if cut >= 0 else ""
    out = {}
    for n in names:
        short = n[:len(n) - len(suf)].strip(" ,") if suf and n.endswith(suf) else n
        out[n] = short or n
    return out


def _a_rank_series(df, date, cols, plan, layout) -> Dict[str, Any]:
    """Series side by side (currencies after long_to_wide): each one's change from its first complete
    year to its last, as a percentage of the first year's average (a level) or total (an amount)."""
    rows = []
    for col in cols[:60]:
        st, _u = _col_type(plan, col, layout)
        y, _g = _period_values(df, date, None, col, _how(st))
        y = y.dropna()
        if len(y) < RANK_MIN_YEARS or not y.iloc[0]:
            continue
        rows.append((col, int(y.index[0]), int(y.index[-1]), float(y.iloc[0]), float(y.iloc[-1]),
                     100.0 * (float(y.iloc[-1]) / float(y.iloc[0]) - 1.0) if y.iloc[0] > 0 else None))
    rows = [r for r in rows if r[5] is not None]
    if len(rows) < 2:
        return {"refused": "rank: fewer than two series with two complete years"}
    rows.sort(key=lambda r: -r[5])
    y0 = min(r[1] for r in rows)
    y1 = max(r[2] for r in rows)
    up = [r for r in rows if r[5] > 0]
    text = ("From %d to %d (yearly averages, or totals for an amount), %s rose most (%s%%) and %s fell most "
            "(%s%%); %d of %d series rose." % (y0, y1, rows[0][0], _fmt(rows[0][5]), rows[-1][0], _fmt(rows[-1][5]),
                                               len(up), len(rows)))
    if rows[-1][5] > 0:
        text = ("From %d to %d (yearly averages, or totals for an amount) every series rose: most %s (%s%%), least "
                "%s (%s%%)." % (y0, y1, rows[0][0], _fmt(rows[0][5]), rows[-1][0], _fmt(rows[-1][5])))
    shown = rows if len(rows) <= 12 else rows[:6] + rows[-6:]
    return {"type": "rank", "title": "Which series moved most", "sentence": text,
            "method": "Each series' first and last complete calendar year, averaged (a total for an amount); "
                      "change as a percentage of the first year. Series of different units are compared "
                      "only as percentages.",
            "table": {"cols": ["Series", "First year", "Last year", "First", "Last", "Change"],
                      "rows": [[r[0], str(r[1]), str(r[2]), _fmt(r[3]), _fmt(r[4]), _fmt(r[5]) + "%"] for r in rows]},
            "chart": {"kind": "bars", "unit": "%", "series": [{"label": _short_labels([x[0] for x in rows])[r[0]], "value": r[5]} for r in shown]}}


def _shrink_m(groups: Any, st: str = "") -> Tuple[int, Dict[str, Any]]:
    """(m for the weighted averages, how it was found: {"kind": "estimated", "high" (the estimate held to
    SHRINK_M_MAX), "low" (held to SHRINK_M_MIN), "chance" (the averages differ no more than chance makes them:
    SHRINK_M_MAX) or "fallback" (SHRINK_M: "why" "few", fewer than SHRINK_MIN_GROUPS groups of SHRINK_MIN_ROWS rows,
    or "flat", no spread at all), "raw": the estimate}): see SHRINK_M. groups: each group's values (decibels, st
    "log_scale", are read as the energies _center averages)."""
    import numpy as np
    use = []
    for v in groups:
        a = np.asarray(v, dtype=float)
        a = a[~np.isnan(a)]
        if st == "log_scale":
            a = 10.0 ** (a / 10.0)
        if len(a) >= SHRINK_MIN_ROWS:
            use.append(a)
    if len(use) < SHRINK_MIN_GROUPS:
        return SHRINK_M, {"kind": "fallback", "why": "few", "raw": None}
    dof = float(sum(len(a) - 1 for a in use))
    within = float(sum(float(((a - a.mean()) ** 2).sum()) for a in use)) / dof
    means = np.array([a.mean() for a in use])
    tau2 = float(means.var(ddof=1)) - float(np.mean([within / len(a) for a in use]))
    if tau2 <= 0:
        return (SHRINK_M, {"kind": "fallback", "why": "flat", "raw": None}) if within <= 0 else \
            (SHRINK_M_MAX, {"kind": "chance", "raw": None})
    raw = within / tau2
    m = int(math.floor(raw + 0.5))
    kind = "high" if m > SHRINK_M_MAX else "low" if m < SHRINK_M_MIN else "estimated"
    return max(SHRINK_M_MIN, min(SHRINK_M_MAX, m)), {"kind": kind, "raw": raw}


def _m_words(m: int, how: Dict[str, Any], what: str) -> str:
    """How m was found, for the sentence: "13 is estimated from how much the brand averages differ against how much
    rows differ within a brand"."""
    k = how.get("kind")
    spread = "how much the %s averages differ against how much rows differ within a %s" % (what, what)
    if k == "estimated":
        return "%d is estimated from %s" % (m, spread)
    if k in ("high", "low"):
        return "estimated at %s from %s, held to %d" % (_fmt(how.get("raw")), spread, m)
    if k == "chance":
        return "%d, the most: the %s averages differ no more than chance alone would make them" % (m, what)
    if how.get("why") == "flat":
        return "%d, a set value: the values do not vary at all, so it cannot be estimated" % m
    return "%d, a set value: fewer than %s %s groups have %d or more rows, too few to estimate it from" % (
        m, _count_word(SHRINK_MIN_GROUPS), what, SHRINK_MIN_ROWS)


def _weighted_average(mean: float, n: int, grand: float, st: str = "", m: float = SHRINK_M) -> float:
    """The IMDb-style weighted rating a group is ranked by: (n * mean + m * grand) / (n + m), m from _shrink_m.
    Decibels (st "log_scale") are weighed as the energies _center averages, and turned back into dB."""
    m = float(m)
    if st == "log_scale":
        e = (n * 10.0 ** (mean / 10.0) + m * 10.0 ** (grand / 10.0)) / (n + m)
        return float(10.0 * math.log10(e))
    return float((n * mean + m * grand) / (n + m))


def _one_row_each(s: Any, dated: bool) -> bool:
    """True when the rows hold one row per entry and date (with no date, one row per entry), at most 1% of them
    repeating a pair: a panel, whose each row is the entry's own figure, not one of several averaged or added."""
    if not len(s):
        return True
    return float(s.duplicated(subset=["e", "d"] if dated else ["e"]).mean()) <= 0.01


def _a_rank(df, date, ent, cols, plan, layout) -> Dict[str, Any]:
    """Entities ranked by the measure's yearly total (a flow or a count) or yearly average (a level) in the
    latest complete year most of them report (review, 29 Sep 2026: transactions were ranked by the single rows
    of their latest day, "all 1 region entries"); with no date, by the entity's total or average over the
    file. An entry whose figure is made of several rows needs RANK_MIN_ROWS of them (the entries below are left
    out and counted in the sentence); a panel's entry (one row per date) is its own figure (_one_row_each)."""
    import pandas as pd
    if not ent and date and len(cols) >= 2:
        return _a_rank_series(df, date, cols, plan, layout)
    if not ent or not cols:
        return {"refused": "rank: needs an entity column (country, product, region) and a measure"}
    col = cols[0]
    st, unit = _col_type(plan, col, layout)
    how = _how(st)
    word = "total" if how == "sum" else "average"
    s = pd.DataFrame({"e": _texts_of(df, ent), "v": _col_nums(df, col)})
    s = s[s["e"] != ""]
    if date:
        s["d"] = pd.to_datetime(df[date], errors="coerce")
    s = s.dropna()
    if s.empty:
        return {"refused": "rank: %s has no numbers" % col}
    latest = back = None
    if date:
        s["y"] = s["d"].dt.year
        _all, full = _full_years(s["d"])
        s = s[s["y"].isin(full)]
        if s.empty:
            return {"refused": "rank: no complete calendar year of %s" % col}
        g = s.groupby(["y", "e"])["v"]
        agg = (g.sum() if how == "sum" else g.mean()).unstack("e")
        per = agg.notna().sum(axis=1)
        good = per[per >= 0.8 * per.max()]
        latest = int(good.index.max())
        now = agg.loc[latest].dropna()
        back = latest - 10 if (latest - 10) in agg.index else None
        then = agg.loc[back].dropna() if back is not None else None
        label = "yearly %s %s" % (word, col)
    else:
        g = s.groupby("e")["v"]
        now, then = (g.sum() if how == "sum" else g.mean()), None
        label = "%s %s" % (word, col)
    # an entry whose figure adds up or averages several rows (transactions, reviews) is ranked only on
    # RANK_MIN_ROWS rows or more (evaluation preflight, 30 Sep 2026: ten brands "averaged" 5.0 stars on one review
    # each); a table with one row per entry and date (a panel: countries by year) ranks each entry's own figure
    small: List[Any] = []
    ranked = now
    one_each = _one_row_each(s, bool(date))
    if not one_each:
        n_now = (s[s["y"] == latest] if date else s).groupby("e").size()
        small = [k for k in now.index if int(n_now.get(k, 0)) < RANK_MIN_ROWS]
        ranked = now.drop(index=small)
        if then is not None:
            n_then = s[s["y"] == back].groupby("e").size()
            then = then[[k for k in then.index if int(n_then.get(k, 0)) >= RANK_MIN_ROWS]]
        if ranked.empty:
            return {"refused": "rank: no %s entry has %d or more rows%s, the fewest a ranked %s may rest on" % (
                ent, RANK_MIN_ROWS, (" in %d" % latest) if latest is not None else "", word)}
    # an average made of several rows is ranked by its weighted average (the IMDb-style weighted rating): its own rows
    # plus m rows at the average of every row in the ranking (the ranked year, or the file), m the file's own
    # (_shrink_m, on every entry's rows in that scope); a total, and a panel's own figure, rank as they are
    weigh = how != "sum" and not one_each
    wt = n_of = None
    grand, n_scope = 0.0, 0
    m, m_how = SHRINK_M, {"kind": "fallback", "raw": None}
    if weigh:
        scope = s[s["y"] == latest] if date else s
        n_of = scope.groupby("e").size()
        grand, n_scope = float(scope["v"].mean()), int(len(scope))
        m, m_how = _shrink_m([g["v"].to_numpy() for _k, g in scope.groupby("e", sort=True)])
        wt = pd.Series({k: _weighted_average(float(ranked[k]), int(n_of[k]), grand, "", m) for k in ranked.index},
                       dtype=float)
        keys = sorted(ranked.index, key=lambda k: (-float(wt[k]), -int(n_of[k]), str(k)))[:10]
        top = wt[keys]
    else:
        top = ranked.sort_values(ascending=False).head(10)
    total = float(now.sum()) if how == "sum" else 0.0
    rows, bars = [], []
    for k, val in top.items():
        chg = ""
        own = float(ranked[k])
        if then is not None and k in then.index and then[k]:
            chg = _fmt(100.0 * (own / then[k] - 1.0)) + "%"
        if weigh:
            rows.append([k, _fmt(val), _fmt(own), format(int(n_of[k]), ","), chg])
        else:
            rows.append([k, _fmt(val), (_fmt(100.0 * val / total) + "%") if total > 0 else "", chg])
        bars.append({"label": k, "value": float(val)})
    if weigh:
        head = ", ".join("%s (weighted %s: an average of %s on %s)" % (
            k, _amt(v, unit), _amt(ranked[k], unit), _n_values(int(n_of[k]), "row")) for k, v in top.head(3).items())
        text = ("In %d the highest %s by %s, each average weighted by its rows, were %s." % (latest, label, ent, head)
                if latest is not None else "The highest %s values by %s, each average weighted by its rows, were %s." % (
                    label, ent, head))
        text += (" Each %s's average is pulled toward the overall %s (the average of all %s%s) by the equivalent of "
                 "%d rows at that average (%s), so an average on a few rows counts for less than one on many." % (
                     ent, _amt(grand, unit), _n_values(n_scope, "row"), (" in %d" % latest) if latest is not None
                     else "", m, _m_words(m, m_how, ent)))
    else:
        head = ", ".join("%s (%s)" % (k, _amt(v, unit)) for k, v in top.head(3).items())
        share3 = 100.0 * float(top.head(3).sum()) / total if total > 0 else None
        tail = ("; together %s%% of the total over all %d %s entries" % (_fmt(share3), len(now), ent)) if share3 else ""
        text = ("In %d the largest %s by %s were %s%s." % (latest, label, ent, head, tail) if latest is not None
                else "The largest %s values by %s were %s%s." % (label, ent, head, tail))
    if then is not None and len(rows):
        ch = [(k, 100.0 * (now[k] / then[k] - 1.0)) for k in top.index if k in then.index and then[k] > 0]
        if ch:
            fast = max(ch, key=lambda x: x[1])
            text += " Over the 10 years before, the fastest riser among them was %s (%s%%)." % (fast[0], _fmt(fast[1]))
    if small:
        text += " %s %s %s with fewer than %d rows%s %s not ranked." % (
            format(len(small), ","), ent, "entry" if len(small) == 1 else "entries", RANK_MIN_ROWS,
            (" in %d" % latest) if latest is not None else "", "is" if len(small) == 1 else "are")
    mp = None
    if 5 <= len(ranked) <= 300:
        # every ranked entity's value that year (the weighted average when the ranking weighs them): the page draws
        # a world map when most of the names are countries
        mp = {"measure": col, "when": str(latest) if latest is not None else "",
              "values": {str(k): float(v) for k, v in (wt if weigh else ranked).items()}}
    min_rows = ("" if one_each else "; an entry resting on fewer than %d rows%s is not ranked" % (
        RANK_MIN_ROWS, " that year" if date else ""))
    weighs = ("; ranked by its average weighted by its rows (the IMDb-style weighted rating: its own rows plus %d rows "
              "at the average of all rows%s; %s)" % (m, " that year" if date else "", _m_words(m, m_how, ent))
              if weigh else "")
    tcols = [ent, "Weighted average", "Average %s" % col, "Rows", "Change over 10 years"] if weigh \
        else [ent, col, "Share of all", "Change over 10 years"]
    if rows and not any(r[-1] for r in rows):
        # no entry has a figure 10 years before: the column would be empty on every row (final review, 30 Sep 2026)
        tcols, rows = tcols[:-1], [r[:-1] for r in rows]
    return {"type": "rank", "title": "%s %s by %s%s" % ("Highest average" if weigh else "Largest", col, ent,
                                                      (" in %d" % latest) if latest is not None else ""),
            "sentence": text, "map": mp,
            "method": (("Each %s entry's %s for each complete calendar year; the latest year at least 80%% of the entries "
                        "report%s%s%s." % (ent, "yearly total (every row added)" if how == "sum" else "yearly average",
                                           ("; change against 10 years before (%d)" % back) if back is not None else "",
                                           weighs, min_rows))
                       if date else "Each %s entry's %s over the file%s%s." % (
                           ent, "total (every row added)" if how == "sum" else "average", weighs, min_rows)),
            "table": {"cols": tcols, "rows": rows},
            "chart": {"kind": "bars", "series": bars}}


def _a_share(df, date, ent, cols, plan, layout) -> Dict[str, Any]:
    import pandas as pd
    if len(cols) < 2:
        return {"refused": "share: needs two or more parts"}
    num = {c: _col_nums(df, c) for c in cols[:8]}
    f = pd.DataFrame(num)
    f["_d"] = pd.to_datetime(df[date], errors="coerce") if date else 0
    g = f.groupby("_d")[list(num)].sum(min_count=1).dropna(how="any")
    if g.empty:
        return {"refused": "share: no date where every part has a value"}
    last, first = g.index.max(), g.index.min()
    tl, tf = g.loc[last].sum(), g.loc[first].sum()
    if tl <= 0 or tf <= 0:
        return {"refused": "share: the parts do not add to a positive whole"}
    rows = [[c, _fmt(100.0 * g.loc[first, c] / tf) + "%", _fmt(100.0 * g.loc[last, c] / tl) + "%"] for c in num]
    lead = max(num, key=lambda c: g.loc[last, c])
    ly, fy = pd.Timestamp(last).strftime("%Y"), pd.Timestamp(first).strftime("%Y")
    text = "In %s, %s was the largest part (%s%% of the %d parts' total), against %s%% in %s." % (
        ly, lead, _fmt(100.0 * g.loc[last, lead] / tl), len(num), _fmt(100.0 * g.loc[first, lead] / tf), fy)
    return {"type": "share", "title": "What the total is made of", "sentence": text,
            "method": "Each part's sum at the first and latest date where every part has a value%s." % ((", over every %s kept" % ent) if ent else ""),
            "table": {"cols": ["Part", "Share " + fy, "Share " + ly], "rows": rows},
            "chart": {"kind": "bars", "series": [{"label": c, "value": float(100.0 * g.loc[last, c] / tl)} for c in num]}}


def _col_nums(df: Any, col: str):
    """The column's numbers as the engine read them (NaN where it read none; all NaN for a column the engine
    reads as text or dates)."""
    import numpy as np
    import pandas as pd
    s = df[col]
    if _series_kind(s) != "number":
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(s, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)


def _center(vals: Any, st: str) -> float:
    """The average that fits the scale: decibels (log_scale) are averaged as energy, 10^(dB/10), and
    turned back into dB; everything else is the plain mean."""
    import numpy as np
    v = np.asarray(vals, float)
    if st == "log_scale":
        return float(10.0 * np.log10(np.mean(np.power(10.0, v / 10.0))))
    return float(np.mean(v))


def _boot_ci(vals: Any, st: str, seed: int = 20260925, B: int = 999) -> Tuple[float, float]:
    import numpy as np
    v = np.asarray(vals, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    if st == "log_scale":
        e = np.power(10.0, v / 10.0)
        m = 10.0 * np.log10(e[idx].mean(axis=1))
    else:
        m = v[idx].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def _a_compare(df, date, ent, cols, plan, layout, by=None) -> Dict[str, Any]:
    import numpy as np
    import pandas as pd
    roles = {str(c.get("role")): c["name"] for c in plan.get("columns", []) if c.get("name")}
    seg = by if by in df.columns else next((roles.get(r) for r in ("segment", "entity", "geography")
                                             if roles.get(r) in df.columns), None)
    if not seg:
        return {"refused": "compare: needs a column of groups (the plan's 'by' or a segment column)"}
    col = cols[0]
    if col == seg:
        return {"refused": "compare: the measure and the groups are the same column"}
    st, unit = _col_type(plan, col, layout)
    s = pd.DataFrame({"g": _texts_of(df, seg), "v": _col_nums(df, col)}).dropna()
    s = s[s["g"] != ""]
    counts = s["g"].value_counts()
    keep = counts[counts >= COMPARE_MIN_GROUP].index[:12]
    if len(keep) < 2:
        return {"refused": "compare: fewer than two groups with %d or more values" % COMPARE_MIN_GROUP}
    rows, bars = [], []
    # the groups are ranked by their averages weighted by their rows (the IMDb-style weighted rating): each group's
    # own rows plus m rows at the average of every row compared (every group, shown or not), m the file's own
    # (_shrink_m, on every group's rows)
    grand = _center(s["v"].values, st)
    m, m_how = _shrink_m([g["v"].to_numpy() for _k, g in s.groupby("g", sort=True)], st)
    for g in keep:
        v = s.loc[s["g"] == g, "v"].values
        lo, hi = _boot_ci(v, st)
        c = _center(v, st)
        rows.append((g, len(v), c, lo, hi, float(np.median(v)), _weighted_average(c, len(v), grand, st, m)))
    rows.sort(key=lambda r: (-r[6], -r[1], r[0]))
    top, bot = rows[0], rows[-1]
    rng = np.random.default_rng(20260925)
    a = s.loc[s["g"] == top[0], "v"].values
    b = s.loc[s["g"] == bot[0], "v"].values
    d = [_center(a[rng.integers(0, len(a), len(a))], st) - _center(b[rng.integers(0, len(b), len(b))], st) for _ in range(999)]
    dlo, dhi = float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))
    avg = "energy average" if st == "log_scale" else "average"
    if m_how.get("kind") == "chance":
        # the groups' averages differ no more than chance makes them (_shrink_m): the top and the bottom are picked
        # out of every group compared, and a gap between the two ends is what chance makes, so no range is offered
        # as if it could exclude no gap (final review, 30 Sep 2026: "95% range 0.0111 to 0.396" beside "no more than
        # chance" in 92 of 300 files with no true difference)
        gap = ("a gap of %s between the top and the bottom of the %d groups, the two ends picked out of them, which "
               "chance alone can make this wide" % (_diff_amt(top[2] - bot[2], st, unit), len(rows)))
    else:
        gap = "a gap of %s (95%% range %s to %s)%s" % (
            _diff_amt(top[2] - bot[2], st, unit), _fmt(dlo), _fmt(dhi),
            "" if dlo > 0 else ", a range that includes no gap at all, so the two may not differ")
    text = ("By %s, with each group's %s %s weighted by its rows, %s ranks highest (%s, 95%% range %s to %s, %s rows; "
            "weighted %s) and %s lowest (%s, %s rows; weighted %s): %s." % (
                seg, avg, col, top[0], _amt(top[2], unit), _fmt(top[3]), _fmt(top[4]), format(top[1], ","),
                _amt(top[6], unit), bot[0], _amt(bot[2], unit), format(bot[1], ","), _amt(bot[6], unit), gap))
    text += (" Each group's %s is pulled toward the overall %s (the %s of all %s) by the equivalent of %d rows at "
             "that %s (%s)." % (avg, _amt(grand, unit), avg, _n_values(int(len(s)), "row"), m, avg,
                                _m_words(m, m_how, seg)))
    if st == "log_scale":
        text += " Decibels are averaged as energy (10^(dB/10)), not as plain numbers."
    few = int((counts < COMPARE_MIN_GROUP).sum())
    more = int((counts >= COMPARE_MIN_GROUP).sum()) - len(keep)
    if few:
        text += " %s with fewer than %d rows %s left out." % (_n_values(few, "group"), COMPARE_MIN_GROUP,
                                                             "is" if few == 1 else "are")
    if more:
        text += " %s more with %d or more rows %s not shown (the 12 with the most rows are)." % (
            _n_values(more, "group"), COMPARE_MIN_GROUP, "is" if more == 1 else "are")
    return {"type": "compare", "title": "%s by %s" % (col, seg), "sentence": text,
            "method": "Each group's %s with a 95%% bootstrap range (999 resamples), ranked by its %s weighted by its rows "
                      "(the IMDb-style weighted rating: its own rows plus %d rows at the %s of all rows; %s); groups "
                      "with fewer than %d values are left out. An association with the group, not its cause." % (
                          avg, avg, m, avg, _m_words(m, m_how, seg), COMPARE_MIN_GROUP),
            "table": {"cols": [seg, "Rows", avg.capitalize(), "95% range", "Median", "Weighted %s" % avg],
                      "rows": [[r[0], format(r[1], ","), _fmt(r[2]), "%s to %s" % (_fmt(r[3]), _fmt(r[4])), _fmt(r[5]),
                                _fmt(r[6])] for r in rows]},
            "chart": {"kind": "bars", "series": [{"label": r[0], "value": r[2]} for r in rows]}}


def _a_relationship(df, date, ent, cols, plan, layout, by=None) -> Dict[str, Any]:
    import numpy as np
    import pandas as pd
    if len(cols) < 2:
        return {"refused": "relationship: needs two numeric columns"}
    x, y = cols[0], cols[1]
    s = pd.DataFrame({"x": _col_nums(df, x), "y": _col_nums(df, y)}).dropna()
    n = len(s)
    if n < RELATIONSHIP_MIN_ROWS or s["x"].nunique() < 3 or s["y"].nunique() < 3:
        return {"refused": "relationship: fewer than %d rows with both values, or a column that barely varies"
                % RELATIONSHIP_MIN_ROWS}
    rho = float(s["x"].rank().corr(s["y"].rank()))
    z = np.arctanh(max(min(rho, 0.999999), -0.999999))
    se = 1.06 / np.sqrt(max(n - 3, 1))            # Fieller et al. for Spearman
    lo, hi = float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se))
    strength = "barely" if abs(rho) < 0.1 else "weakly" if abs(rho) < 0.3 else "moderately" if abs(rho) < 0.6 else "strongly"
    if lo <= 0 <= hi:
        text = ("Across %s rows, %s and %s show no clear link (Spearman %s, 95%% range %s to %s)."
                % (format(n, ","), x, y, _fmt(rho), _fmt(lo), _fmt(hi)))
    else:
        text = ("Across %s rows, higher %s goes with %s %s, %s (Spearman %s, 95%% range %s to %s). "
                "An association, not a cause." % (format(n, ","), x, "higher" if rho > 0 else "lower", y, strength,
                                                  _fmt(rho), _fmt(lo), _fmt(hi)))
    pts = s.sample(min(n, 400), random_state=1) if n > 400 else s
    return {"type": "relationship", "title": "%s and %s" % (x, y), "sentence": text,
            "method": "Spearman rank correlation (it reads any steady rise or fall, not only a straight line); "
                      "95% range by the Fisher transform with the Spearman correction.",
            "table": {"cols": ["Rows", "Spearman", "95% range"], "rows": [[format(n, ","), _fmt(rho), "%s to %s" % (_fmt(lo), _fmt(hi))]]},
            "chart": {"kind": "scatter", "x_name": x, "y_name": y,
                      "points": [[float(a), float(b)] for a, b in zip(pts["x"], pts["y"])]}}


def _a_distribution(df, date, ent, cols, plan, layout, by=None) -> Dict[str, Any]:
    import numpy as np
    col = cols[0]
    st, unit = _col_type(plan, col, layout)
    v = _col_nums(df, col).dropna().values
    if len(v) < DISTRIBUTION_MIN_VALUES:
        return {"refused": "distribution: fewer than %d values in %s" % (DISTRIBUTION_MIN_VALUES, col)}
    q = np.percentile(v, [10, 25, 50, 75, 90])
    text = ("%s: half the %s values lie between %s and %s (median %s); one in ten is below %s and one in ten above %s."
            % (col, format(len(v), ","), _amt(q[1], unit, tail=False), _amt(q[3], unit), _amt(q[2], unit, tail=False),
               _amt(q[0], unit, tail=False), _amt(q[4], unit, tail=False)))
    if st == "log_scale":
        text += " The energy average is %s, above the median because loud values dominate energy." % _amt(_center(v, st), unit)
    zeros = float((v == 0).mean())
    if zeros >= 0.05:
        text += " %s%% of the values are exactly zero." % _fmt(100 * zeros)
    edges = np.histogram_bin_edges(v, bins=min(12, max(5, int(np.sqrt(len(v))))))
    h, _e = np.histogram(v, bins=edges)
    return {"type": "distribution", "title": "How %s is spread" % col, "sentence": text,
            "method": "Percentiles of every value; the bars count values in equal-width bins.",
            "table": {"cols": ["Values", "10th", "25th", "Median", "75th", "90th"],
                      "rows": [[format(len(v), ",")] + [_fmt(x) for x in q]]},
            "chart": {"kind": "bars", "series": [{"label": "%s to %s" % (_fmt(edges[i]), _fmt(edges[i + 1])), "value": int(h[i])}
                                                 for i in range(len(h))]}}


_STOP = frozenset("""a an and are as at be been but by can could did do does for from had has have he her his i if in
into is it its just me my no not of on or our so than that the their them then there these they this to too us
was we were what when which who will with would you your very really also get got all any some more most much one
out up about after again am being both each few how im ive dont didnt its it's only other own same should such
""".split())


# The themes analysis's words (review of the chart registry, 30 Sep 2026). A word is letters in any script, read after
# Unicode NFC composition ("hôtel" and "café" stay whole; they were cut to "tel" and "caf"), 3 or more characters
# starting with a letter, with an apostrophe inside; common words (_STOP) are left out. A word that is a person's
# name (_theme_drop) stands as a break: it is never a theme, and no two-word phrase joins across it.
_THEME_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|'){2,}")
# the proper-noun rule: a word capitalised in PROPER_SHARE or more of its occurrences that are not a sentence's first
# word, in a file whose texts do not capitalise most words anyway (at most PROPER_BASELINE of all such occurrences;
# above that, as in titles written in Title Case, a capital says nothing and the rule is off)
PROPER_SHARE = 0.60
PROPER_BASELINE = 0.50
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_ANY_WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*")
# a list of given names (engine/first_names.txt, beside this file and in the pack: /usr/share/dict/propernames, public
# domain, lower-cased, without the ordinary words it holds; see its header)
FIRST_NAMES_FILE = "first_names.txt"
_FIRST_NAMES: Optional[FrozenSet[str]] = None


def _first_names() -> FrozenSet[str]:
    """engine/first_names.txt as a set of lower-case given names (empty when the file is missing)."""
    global _FIRST_NAMES
    if _FIRST_NAMES is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), FIRST_NAMES_FILE), encoding="utf-8") as fh:
                _FIRST_NAMES = frozenset(x.strip().lower() for x in fh if x.strip() and not x.startswith("#"))
        except OSError:
            _FIRST_NAMES = frozenset()
    return _FIRST_NAMES


def _nfc(s: Any) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", str(s if s is not None else ""))


def _value_tokens(values: Iterable[Any]) -> FrozenSet[str]:
    """Every token of the values, of any length: runs of letters or digits, lower case, after NFC ("Emily Jones" gives
    emily and jones, "emily.jones@mail.test" emily, jones, mail and test)."""
    out: Set[str] = set()
    for v in values:
        out.update(re.findall(r"[^\W_]+", _nfc(v).lower()))
    return frozenset(out)


def _theme_tokens(t: Any, drop: Any = frozenset()) -> List[Optional[str]]:
    """One text's theme words in order, lower case (_THEME_WORD, common words left out); a word in `drop` is None."""
    out: List[Optional[str]] = []
    for w in _THEME_WORD.findall(_nfc(t).lower()):
        if w in _STOP:
            continue
        out.append(None if w in drop else w)
    return out


def _theme_pairs(toks: List[Optional[str]]) -> Set[str]:
    """The two-word phrases of a text's theme words: two words side by side, never across a name."""
    return set(a + " " + b for a, b in zip(toks, toks[1:]) if a and b)


def _proper_nouns(texts: Iterable[Any]) -> FrozenSet[str]:
    """The words the texts write as a name (PROPER_SHARE, PROPER_BASELINE): counted on the words that are not a
    sentence's first; a sentence of 3 or more long words (4 or more letters) all capitalised is a title in Title Case
    and is not counted, nor is a word in capitals throughout ("GREAT", "USB")."""
    import collections
    cap: Dict[str, int] = collections.Counter()
    low: Dict[str, int] = collections.Counter()
    for t in texts:
        for sent in _SENTENCE_SPLIT.split(_nfc(t)):
            ws = _ANY_WORD.findall(sent)
            longs = [w for w in ws if len(w) >= 4]
            if len(longs) >= 3 and all(w[0].isupper() for w in longs):
                continue
            for w in ws[1:]:
                lw = w.lower()
                if len(w) < 3 or w.isupper() or lw in _STOP or lw.startswith(("i'", "i\u2019")):
                    continue
                if w[0].isupper():
                    cap[lw] += 1
                else:
                    low[lw] += 1
    nc, nl = sum(cap.values()), sum(low.values())
    if not nc or nc > PROPER_BASELINE * (nc + nl):
        return frozenset()
    return frozenset(w for w, c in cap.items() if c >= PROPER_SHARE * (c + low[w]))


def _theme_drop(texts: Iterable[Any], names: Any = frozenset()) -> FrozenSet[str]:
    """The words the themes never show, as people's names: a token of a flagged column's values (withheld, coded or
    kept; `names`, the exact set, any length), a given name (engine/first_names.txt), and a word the texts write as a
    name (_proper_nouns)."""
    return frozenset(names or ()) | _first_names() | _proper_nouns(texts)


def _theme_counts(texts: List[str], drop: Any) -> Tuple[Any, Any]:
    """(words, phrases): collections.Counter of the texts that use each theme word and two-word phrase at least once."""
    import collections
    words, pairs = collections.Counter(), collections.Counter()
    for t in texts:
        toks = _theme_tokens(t, drop)
        words.update(set(w for w in toks if w))
        pairs.update(_theme_pairs(toks))
    return words, pairs


def _a_themes(df, date, ent, cols, plan, layout, by=None, names=None) -> Dict[str, Any]:
    col = cols[0]
    texts = [t for t in _texts_of(df, col).tolist() if t]
    if len(texts) < THEMES_MIN_TEXTS:
        return {"refused": "themes: fewer than %d non-empty texts in %s" % (THEMES_MIN_TEXTS, col)}
    words, pairs = _theme_counts(texts, _theme_drop(texts, names))
    n = len(texts)
    # ties in the word's order (review of the chart registry, 30 Sep 2026: Counter.most_common kept the order the words
    # were first counted in, a set's, so two words used equally often swapped places with the string-hash seed)
    top = [(w, c) for w, c in sorted(words.items(), key=lambda kv: (-kv[1], kv[0])) if c >= 3][:10]
    top2 = [(w, c) for w, c in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0])) if c >= 3][:6]
    if not top:
        return {"refused": "themes: no word appears in 3 or more of the texts"}
    text = "Across %s texts in %s, the words most often used are %s" % (
        format(n, ","), col, ", ".join("'%s' (%s%%)" % (w, _fmt(100.0 * c / n)) for w, c in top[:6]))
    if top2:
        text += "; the most frequent phrases are %s" % ", ".join("'%s' (%s%%)" % (w, _fmt(100.0 * c / n)) for w, c in top2[:4])
    text += ". Counts of words, not a reading of meaning."
    return {"type": "themes", "title": "What the %s texts talk about" % col, "sentence": text,
            "method": "Share of texts that use each word or two-word phrase at least once; common words (the, and, "
                      "very) are left out. Names, emails and phone numbers are withheld before this step, and a word "
                      "that is a person's name is never a theme (a word of a flagged column's values, a given name, or "
                      "a word the texts capitalise mid-sentence).",
            "table": {"cols": ["Word or phrase", "Texts", "Share of texts"],
                      "rows": [[w, format(c, ","), _fmt(100.0 * c / n) + "%"] for w, c in top + top2]},
            "chart": {"kind": "bars", "unit": "%", "series": [{"label": w, "value": 100.0 * c / n} for w, c in top]}}


# The adapter's person-name test for a chart's category levels (review of the chart registry, 30 Sep 2026): a Pareto's
# bars and a crosstab's rows and columns are the column's own values, so a column of people's names that the
# personal-column check did not flag (its heading names no person: "stylist", "assigned") must not be charted. The
# column's values look like people's names when the personal-column check would say so from its heading and values
# (_person_hint and _person_value, PERSONAL_MIN_SHARE), or when PERSONAL_MIN_SHARE of its distinct values could be a
# person's name (_person_value) and NAMES_FIRST_SHARE of them begin with a given name (engine/first_names.txt).
NAMES_FIRST_SHARE = 0.30


def _looks_like_names(values: Iterable[Any], header: Any = "") -> bool:
    vals = list(dict.fromkeys(" ".join(str(v).split()) for v in values if str(v).strip()))
    if not vals:
        return False
    person = [v for v in vals if _person_value(v)]
    if len(person) < PERSONAL_MIN_SHARE * len(vals):
        return False
    if _person_hint(_header_tokens(header), header)[0]:
        return True
    given = _first_names()

    def first(v: str) -> str:
        if v.count(",") == 1:
            v = v.split(",")[1]
        w = v.split()
        return w[0].casefold().strip(".'\u2019") if w else ""
    return sum(1 for v in person if first(v) in given) >= NAMES_FIRST_SHARE * len(vals)


def _design(df: Any, cols: List[str]):
    """Columns the engine reads as numbers as they are; any other column as categories: one-hot columns of its
    8 commonest values (the rest together, the commonest left out as the base). Returns (matrix, groups, names,
    rows used)."""
    import numpy as np
    import pandas as pd
    parts, groups, names = [], [], []
    for c in cols:
        if _series_kind(df[c]) == "number":            # a column the engine reads as numbers; a missing one drops its row
            parts.append(_col_nums(df, c).rename(c))
            groups.append(c)
            names.append(c)
        else:
            v = _texts_of(df, c)
            top = v[v != ""].value_counts().index[:8]
            if len(top) < 2:
                continue
            miss = v == ""
            v = v.where(v.isin(top), "other")
            for lv in [x for x in list(top[1:]) + (["other"] if ((v == "other") & ~miss).any() else [])]:
                parts.append((v == lv).astype(float).where(~miss).rename("%s = %s" % (c, lv)))
                groups.append(c)
                names.append("%s = %s" % (c, lv))
    if not parts:
        return None, [], [], None
    X = pd.concat(parts, axis=1)
    ok = X.notna().all(axis=1)
    return X[ok].values.astype(float), groups, names, ok


def _oos(X: Any, y: Any, splits: List[Tuple[Any, Any]]) -> Tuple[float, float, float, int]:
    """Least squares scored out of sample over (training rows, scored rows) splits: (R squared against the
    baseline, mean absolute error, the baseline's mean absolute error, rows scored). The baseline predicts
    each scored row with the mean of its training rows, the forecast a person could make without a model."""
    import numpy as np
    pred = np.full(len(y), np.nan)
    base = np.full(len(y), np.nan)
    for tr, te in splits:
        A = np.column_stack([np.ones(len(tr)), X[tr]])
        beta = np.linalg.lstsq(A, y[tr], rcond=None)[0]
        pred[te] = np.column_stack([np.ones(len(te)), X[te]]) @ beta
        base[te] = y[tr].mean()
    sc = ~np.isnan(pred)
    sse, sst = float(((y[sc] - pred[sc]) ** 2).sum()), float(((y[sc] - base[sc]) ** 2).sum())
    return ((1.0 - sse / sst) if sst > 0 else 0.0, float(np.abs(y[sc] - pred[sc]).mean()),
            float(np.abs(y[sc] - base[sc]).mean()), int(sc.sum()))


def _forward_blocks(d: Any, k: int) -> Optional[List[Tuple[Any, Any]]]:
    """Forward-chaining splits over rows with dates d (numpy datetime64): the rows in date order cut into k
    blocks of about equal size, never splitting one date across two blocks; block j (j >= 1) is scored by a
    model trained on the blocks before it. None when fewer than 3 blocks can be scored."""
    import numpy as np
    order = np.argsort(d, kind="stable")
    ds = d[order]
    n = len(ds)
    starts = np.flatnonzero(np.r_[True, ds[1:] != ds[:-1]])          # first row of each distinct date
    block = np.minimum((starts * k) // max(n, 1), k - 1)
    ids = np.empty(n, dtype=int)
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else n
        ids[s:e] = block[i]
    got = sorted(set(ids.tolist()))
    splits = []
    for j in got[1:]:
        tr, te = order[ids < j], order[ids == j]
        if len(tr) and len(te):
            splits.append((tr, te))
    return splits if len(splits) >= 3 else None


def _a_predict(df, date, ent, cols, plan, layout, by=None) -> Dict[str, Any]:
    import numpy as np
    import pandas as pd
    target = cols[0]
    roles = {c["name"]: c for c in plan.get("columns", []) if c.get("name")}
    drivers = [c for c in cols[1:] if c in df.columns and c not in (target, date)]
    if not drivers:
        drivers = [c for c, m in roles.items() if m.get("role") in ("driver", "segment") and c in df.columns
                   and c not in (target, date)][:8]
    drivers = drivers[:8]
    if not drivers:
        return {"refused": "predict: no driver columns named (the plan's columns after the target)"}
    y_all = _col_nums(df, target)
    X, groups, names, ok = _design(df, drivers)
    if X is None:
        return {"refused": "predict: none of the drivers can enter a model"}
    n_all = len(df)
    miss_driver = int((~ok).sum())
    y = y_all[ok].values.astype(float)
    keep = ~np.isnan(y)
    miss_target = int((~keep).sum())
    X, y = X[keep], y[keep]
    d = None
    if date:
        d_all = pd.to_datetime(df[date], errors="coerce")[ok].values[keep]
        has = ~pd.isna(d_all)
        if has.sum() >= PREDICT_MIN_ROWS:
            d = d_all
    miss_date = 0
    if d is not None:
        has = ~pd.isna(d)
        miss_date = int((~has).sum())
        X, y, d = X[has], y[has], d[has]
    n, p = X.shape
    if n < PREDICT_MIN_ROWS or n < PREDICT_ROWS_PER_TERM * (p + 1):
        return {"refused": "predict: %d complete rows for %d model terms; at least %d rows a term are needed"
                % (n, p + 1, PREDICT_ROWS_PER_TERM)}
    splits = _forward_blocks(d, PREDICT_BLOCKS) if d is not None else None
    if splits is not None:
        design = ("forward-chaining: the rows sorted by %s and cut into %d blocks, each of the last %d predicted by "
                  "a model trained only on the blocks before it" % (date, PREDICT_BLOCKS, len(splits)))
        how = "trained on earlier dates and scored on the %d later blocks" % len(splits)
    else:
        folds = np.random.default_rng(20260925).permutation(n) % 5
        splits = [(np.flatnonzero(folds != f), np.flatnonzero(folds == f)) for f in range(5)]
        design = ("5 random folds, because %s" % ("the rows have too few distinct dates to score later rows by "
                                                  "earlier ones" if d is not None else "the file has no usable date"))
        how = "scored on held-out rows in 5 random folds"
    r2, mae, mae0, scored = _oos(X, y, splits)
    imp = []
    for g in dict.fromkeys(groups):
        m = np.array([gg != g for gg in groups])
        r2g = _oos(X[:, m], y, splits)[0] if m.any() else 0.0
        imp.append((g, r2 - r2g))
    imp.sort(key=lambda t: -t[1])
    A = np.column_stack([np.ones(n), X])
    beta = np.linalg.lstsq(A, y, rcond=None)[0][1:]
    st, unit = _col_type(plan, target, layout)
    if r2 < 0.05:
        text = ("A straight-line model of %s from %s, %s, does not predict them better than the mean of its "
                "training rows does (out-of-sample R squared %s): these columns say little about %s on their own."
                % (target, ", ".join(drivers), how, _fmt(r2), target))
    else:
        text = ("A straight-line model of %s from %s, %s, predicts them with R squared %s and a typical error of "
                "%s, against %s for the mean of its training rows. %s carries most of it (R squared falls by %s "
                "without it)." % (target, ", ".join(drivers), how, _fmt(r2), _diff_amt(mae, st, unit),
                                   _diff_amt(mae0, st, unit), imp[0][0], _fmt(imp[0][1])))
    text += " An association the model learned, not a cause."
    dropped = [(miss_driver, "a missing or unreadable driver"), (miss_target, "a missing or unreadable %s" % target),
               (miss_date, "no date")]
    drop_txt = "; ".join("%s for %s" % (format(k, ","), w) for k, w in dropped if k) or "none"
    coefs = {nm: b for nm, b in zip(names, beta)}
    rows = [[g, _fmt(dd) if dd > 0.005 else "adds nothing held-out", "; ".join("%s %s" % (nm.split(" = ", 1)[-1] if " = " in nm else "per unit", _fmt(coefs[nm]))
                                     for nm in names if (nm == g or nm.startswith(g + " = ")))] for g, dd in imp]
    rows.append(["Typical held-out error (MAE)", _fmt(mae), "against %s for the mean of the training rows" % _fmt(mae0)])
    rows.append(["Rows", "%s of %s used, %s scored" % (format(n, ","), format(n_all, ","), format(scored, ",")),
                 "dropped: %s" % drop_txt])
    return {"type": "predict", "title": "What predicts %s" % target, "sentence": text,
            "method": "Least squares on %s complete rows (%s of %s dropped: %s); categories as one column per value "
                      "(their commonest value is the base); scored out of sample by %s, against the mean of the "
                      "training rows; each driver by how much the out-of-sample R squared falls without it."
                      % (format(n, ","), format(n_all - n, ","), format(n_all, ","), drop_txt, design),
            "table": {"cols": ["Driver", "R squared lost without it", "Effect (per unit, or against the base value)"], "rows": rows},
            "chart": {"kind": "bars", "series": [{"label": g, "value": max(0.0, dd)} for g, dd in imp]},
            "_used": [target] + list(drivers) + ([date] if d is not None else [])}


_ANALYSIS_FN = {"trend": _a_trend, "extremes": _a_extremes, "agreement": _a_agreement, "rank": _a_rank, "share": _a_share,
                "compare": _a_compare, "relationship": _a_relationship, "distribution": _a_distribution,
                "themes": _a_themes, "predict": _a_predict}
_TAKES_BY = ("compare", "relationship", "distribution", "themes", "predict")


# how many named columns each analysis reads (the rest are named but unused), for the unreadable note
_USES = {"trend": 4, "extremes": 2, "agreement": 2, "compare": 1, "relationship": 2, "distribution": 1, "share": 8,
         "themes": 0}


def _unreadable_note(text: str, used: List[str], unread: Dict[str, int], undated: int = 0,
                     date: Optional[str] = None, zeros: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """The analysis sentence with the cells it could not count said at its end: "(127 unreadable values in
    units are not counted)", for a level whose 0 is a placeholder "(550 zero values in VALUE are not counted: a
    rate of 0 is a placeholder)", and, for an analysis over time, "(12 rows with no order_date are not counted)".
    The counts are over the rows the engine kept, the rows the analysis read, so the sentence says exactly
    what was left out of what it counted."""
    parts = [(c, int(unread[c])) for c in dict.fromkeys(used) if c != date and unread.get(c)]
    notes = []
    if parts:
        words = ["%s in %s" % (_n_values(n, "unreadable value"), c) if i == 0 else "%s in %s" % (format(n, ","), c)
                 for i, (c, n) in enumerate(parts)]
        one = len(parts) == 1 and parts[0][1] == 1
        notes.append("%s %s not counted" % (_listed(words), "is" if one else "are"))
    for c in dict.fromkeys(used):
        if c != date and (zeros or {}).get(c):
            notes.append(_zero_note(c, zeros[c]))
    if undated and date:
        notes.append("%s with no %s %s not counted" % (_n_values(int(undated), "row"), date,
                                                      "is" if int(undated) == 1 else "are"))
    if not notes:
        return text
    note = " (%s)" % "; ".join(notes)
    first = _first_sentence(text)
    if first.endswith(".") and text.startswith(first):
        return first[:-1] + note + "." + text[len(first):]
    return text.rstrip() + note


def _names_but(ctx: Dict[str, Any], col: str) -> FrozenSet[str]:
    """The tokens of every flagged column's values (ctx "names_by", by landed name) but those of `col` (a file's name)."""
    nb = ctx.get("names_by") or {}
    rd = ctx.get("reading")
    own = {col, _engine_slug(col)} | ({rd.landed(col)} if rd is not None and rd.landed(col) else set())
    return frozenset().union(*[v for k, v in nb.items() if k not in own]) if nb else frozenset()


def _gate_state(cr: Any, limit: float) -> Dict[str, Any]:
    """The engine's own gate, as the AI's analyses and the planner's feedback respect it (integration review,
    29 Sep 2026: the analyses ran on the rows the engine kept, with a caveat, after the engine had refused its
    own analysis because it set aside more than `limit` of the rows; the rest may not stand for the file).
    {"over", "pct", "limit" (both in percent), "aside", "rows"}.

    When the gate trips, the planner is not asked again (_plan_signals sends nothing): a plan cannot make an
    unreadable cell readable, so a re-plan is either a wasted call or a plan that sets the unreadable column
    aside and moves the analysis to another column (review cases f and g: the order date is "TBD" on 30% of
    rows, and the other date is a refund date that runs the wrong way), the very result the gate exists to
    prevent. The narrower signal kept until the final review (29 Sep 2026: "setting aside a column the
    analyses do not read may let them run") is gone too: with two side dates unreadable on the same rows it
    told the planner that one column alone held those rows back, which was false."""
    total = int(getattr(cr, "total_in", 0) or 0)
    rate = float(getattr(cr, "suspect_quarantine_rate", 0.0) or 0.0) if total else 0.0
    aside = int(getattr(cr, "rows_quarantined", 0) or 0)
    return {"over": bool(total) and rate > limit, "pct": round(100.0 * rate, 4),
            "limit": round(100.0 * limit, 2), "aside": aside, "rows": total}


def _run_analyses(ctx: Dict[str, Any], plan: Dict[str, Any], layout: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The plan's analyses, computed here on the rows the engine kept, as it read them (_analysis_frame). Never
    raises: a request that cannot run is refused and named. A column the visitor withheld or coded is never
    read: never an axis, a group, a driver or a measure, and a withheld one is never named (it is "a column you
    withheld"). ctx: see _analysis_frame, plus "private" (a function: a file's column name to "withhold",
    "code" or None). Leaves ctx["zeros"]: the frame's placeholder zeros ({column: {"zeros", "values", "word"}})."""
    out, refused = [], []
    asked = [a for a in (plan.get("analyses") or []) if isinstance(a, dict)][:ANALYSES_MAX]
    if not asked:
        return {"items": [], "refused": []}
    gate = ctx.get("gate")
    if gate is not None:
        # the engine refused its own business analysis: none of the plan's analyses is drawn from the rows it
        # kept either (_gate_state says why, and why the planner is then not asked again)
        kinds = list(dict.fromkeys(str(a.get("type") or "analysis")[:40] for a in asked))
        why = ("the engine set aside %s of the rows (%s of %s), over its %s limit, so no analysis is drawn from the rest"
               % (_pct_text(gate["pct"]), format(gate["aside"], ","), format(gate["rows"], ","), "%g%%" % gate["limit"])
               if gate.get("over") else "the engine's business analysis did not run, so no analysis is drawn from its rows")
        return {"items": [], "refused": ["%s: %s" % (", ".join(kinds), why)],
                "rows": {"kept": int(gate["rows"]) - int(gate["aside"]), "set_aside": int(gate["aside"])}, "date": None,
                "gate": gate,
                "note": "Not computed: the engine stopped its own business analysis because it set aside too many rows "
                        "for the rest to stand for the file, and the AI's analyses stop with it. The data tests and the "
                        "data-health findings still stand."}
    try:
        df, date, ent, no_date, info = _analysis_frame(ctx, plan, layout)
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return {"items": [], "refused": ["the analyses could not read the file (%s)" % type(exc).__name__]}
    ctx["zeros"] = dict(info.get("zeros") or {})          # for the Data tests card's note row (run())
    private = ctx.get("private") or (lambda _n: None)
    roles = {str(c.get("role")): c["name"] for c in plan.get("columns", []) if c.get("name")}
    ent_hidden = next((private(roles[r]) and roles[r] for r in ("entity", "geography") if roles.get(r) and private(roles[r])), None)
    for a in asked:
        t = a.get("type")
        if t not in _ANALYSIS_FN:
            refused.append("%s: not on the menu" % str(t)[:40])
            continue
        if not date and t in _TIME_ANALYSES:
            refused.append("%s: no usable date column (%s)" % (t, no_date))
            continue
        names = [str(c) for c in (a.get("columns") or [])]
        hidden = [(n, private(n)) for n in names if private(n)]
        names = [n for n in names if not private(n)]
        by = a.get("by")
        if by and private(by):
            refused.append("%s: %s, so it does not group the rows" % (t, _who(by, private(by))))
            continue
        if t in ("rank", "share") and not ent and ent_hidden:
            refused.append("%s: the plan's entity column is %s, so no entity is named" % (
                t, _who(ent_hidden, private(ent_hidden)) if private(ent_hidden) != "withhold" else "a column you withheld"))
            continue
        if hidden and not names:
            refused.append("%s: %s" % (t, "every column it names is one you withheld or coded" if len(hidden) > 1 else
                                       "it names only %s" % _who(*hidden[0])))
            continue
        # a driver may be the segment or entity column (review: predict refused the region it was asked to use)
        cols = _resolve(names, df, layout, date, None if t == "predict" else ent)
        if not cols:
            alt = _resolve(names, df, layout, date, None)
            if alt and t in _NUMERIC_ANALYSES and info["kinds"].get(alt[0]) != "number":
                refused.append("%s: the engine reads %s as %s, not numbers" % (
                    t, alt[0], "dates" if info["kinds"].get(alt[0]) == "date" else "text"))
            elif alt:
                refused.append("%s: %s groups the rows here (the plan's entity or segment), so it is not also the "
                               "measure" % (t, alt[0]))
            else:
                # a column the plan set aside, or one the table the engine read does not have, is said to be so
                aside = set()
                for op in plan.get("operations") or []:
                    if isinstance(op, dict) and op.get("op") == "set_aside":
                        aside |= set(str(c) for c in op.get("columns") or [])
                    elif isinstance(op, dict) and op.get("op") == "keep_columns" and len(op.get("columns") or []) >= 2:
                        aside |= set(str(c) for c in names if c not in (op.get("columns") or []))
                gone = [n for n in names if n in aside]
                refused.append("%s: %s" % (t, ("the plan set %s aside, so the engine did not read it" % ", ".join(gone[:3])) if gone
                                           else "the table the engine read has no column %s" % (", ".join(names[:4]) or "named")))
            continue
        need = {"relationship": 2, "agreement": 2, "share": 8}.get(t, 1)
        if t in _NUMERIC_ANALYSES or t == "predict":
            text_cols = [c for c in cols[:need if t != "trend" else 4] if info["kinds"].get(c) != "number"]
            if t == "predict":
                text_cols = [c for c in cols[:1] if info["kinds"].get(c) != "number"]
            if text_cols:
                c0 = text_cols[0]
                refused.append("%s: the engine reads %s as %s, not numbers" % (
                    t, c0, "dates" if info["kinds"].get(c0) == "date" else "text"))
                continue
        try:
            # the themes never show a person's name: the tokens of every flagged column's values but the column the
            # themes read themselves (a kept column's own words are its themes; ctx "names_by")
            res = (_ANALYSIS_FN[t](df, date, ent, cols, plan, layout, by=by, names=_names_but(ctx, cols[0])) if t == "themes"
                   else _ANALYSIS_FN[t](df, date, ent, cols, plan, layout, by=by) if t in _TAKES_BY
                   else _ANALYSIS_FN[t](df, date, ent, cols, plan, layout))
        except Exception as exc:  # noqa: BLE001 - one analysis never stops the report
            if os.environ.get("NL_BROWSER_STRICT"):
                raise
            res = {"refused": "%s failed (%s)" % (t, type(exc).__name__)}
        if res.get("refused"):
            refused.append(res["refused"])
            continue
        used = res.pop("_used", None)
        dated = bool(date) and t in _TIME_ANALYSES + ("rank", "share")      # these read the date when there is one
        if used is None:
            k = _USES.get(t, 60 if (t == "rank" and not ent and date and len(cols) >= 2) else 1)
            used = list(cols[:k]) + ([date] if dated else [])
        if dated:
            res["method"] = (res.get("method") or "").rstrip() + " Dates from the %s column." % date
        if t != "themes":
            res["sentence"] = _unreadable_note(res.get("sentence") or "", used, info["unread"],
                                               info["undated"] if dated else 0, date, info.get("zeros"))
            for c in dict.fromkeys(used):
                if c in info["mixed"]:
                    res["sentence"] = res["sentence"].rstrip() + (
                        " %s mixes two scales (%s values between 0 and 1, %s above 1); every value is read on the "
                        "0-100 scale." % (c, format(info["mixed"][c][0], ","), format(info["mixed"][c][1], ",")))
        res["columns"] = cols
        res["graded"] = False
        out.append(res)
    kept, aside = info["kept"], info["aside"]
    return {"items": out, "refused": refused, "rows": {"kept": kept, "set_aside": aside}, "date": date,
            "note": "Asked for by the AI plan, computed by the engine's adapter on the %s rows the engine kept%s, "
                    "with every value as the engine read it. These are descriptive: the change gate did not grade "
                    "them. A value the engine could not read is counted as missing, and each sentence says how "
                    "many. A column you withheld or coded is never used. Units are the AI's reading of the column; "
                    "the numbers are the file's." % (format(kept, ","), (" (the %s it set aside are not counted)" %
                                                                         format(aside, ",")) if aside else "")}


_SHORT_LINE = re.compile(r"^([\d,]+) months of history \(([^)]*)\): too short to compare the latest 12 months with the "
                         r"12 before, so no change is tested; the averages over the period are below\.")


_COVERS_LINE = re.compile(r"^The file covers ([\d,]+) months \(([^)]*)\); comparing the latest 12 months with the 12 before "
                          r"needs 24, so no change over time is tested\.$")
_MONTHS_HISTORY = re.compile(r"There are ([\d,]+) months of history; .*$")
_MONTHS_TOO_SHORT = re.compile(r"([\d,]+) months of history is too short to replay and check one\.")


def _mend_short_history_line(rep: Dict[str, Any], clean: Any = None, date_col: Optional[str] = None) -> None:
    """The engine's bottom line for a file whose change tests settled nothing says the history is "too short"
    whatever its length (review 25 Sep 2026: 1,758 months of temperatures read "too short"). From 24 months
    on, that is not why, so the line says what happened. For yearly rows (review M9, 29 Sep 2026: 30 yearly
    values read "30 months of history (1995-12 to 2024-12)") it says what the rows are, that the monthly test
    does not apply, and quotes the lead analysis; the engine's lines that count those rows as months say the
    same. The fix lives here, not in the engine's narrate.py, so the engine's decision code (and the benchmark
    receipt measured on it) is unchanged."""
    import pandas as pd
    st = rep.get("story") or {}
    h = st.get("headline")
    m = _SHORT_LINE.match(h) if isinstance(h, str) else None
    grain = _date_grain(clean, date_col) if clean is not None and date_col else ""
    if grain == "yearly":
        d = pd.to_datetime(clean[date_col], errors="coerce").dropna()
        if len(d):
            n, y0, y1 = int(d.nunique()), int(d.min().year), int(d.max().year)
            line = ("%s yearly values, %d to %d: the engine's monthly change test does not apply."
                    % (format(n, ","), y0, y1))
            if m:
                items = (rep.get("ai_analyses") or {}).get("items") or []
                st["headline"] = line + ((" From the AI plan's analyses: %s" % _first_sentence(items[0].get("sentence") or ""))
                                         if items else "")
            not_monthly = "%s yearly values are not a monthly series, so no monthly forecast is made." % format(n, ",")

            def mend(x: str) -> str:
                if _COVERS_LINE.match(x):
                    return ("The file holds %s yearly values (%d to %d); the engine's monthly change test compares "
                            "months, so it does not apply." % (format(n, ","), y0, y1))
                x = _MONTHS_HISTORY.sub("There are " + not_monthly, x)
                return _MONTHS_TOO_SHORT.sub(not_monthly, x)
            for k in ("cannot_answer", "what_happened", "why", "whats_next"):
                st[k] = [mend(x) for x in st.get(k) or []]
            fc = rep.get("forecast") or {}
            if isinstance(fc.get("reason"), str):
                fc["reason"] = mend(fc["reason"])
            return
    if m and int(m.group(1).replace(",", "")) >= 24:
        st["headline"] = ("%s months of history (%s): the latest 12 months against the 12 before settled no change "
                          "strong enough to act on; the averages over the period are below.%s"
                          % (m.group(1), m.group(2), h[m.end():]))


_NO_DATES_LINE = "No column holds dates, so nothing can be said about change over time and no forecast is possible."


def _mend_cannot_answer(rep: Dict[str, Any]) -> None:
    """The engine's lines that deny what the AI plan's analyses show (review, 29 Sep 2026: "No column holds
    dates, so nothing can be said about change over time" under a headline quoting a trend over the years):
    each says what the engine did and what the analyses read instead."""
    from northledger import narrate as _narrate
    ana = rep.get("ai_analyses") or {}
    items = ana.get("items") or []
    if not items:
        return
    date = ana.get("date")
    dated = date and any(a.get("type") in _TIME_ANALYSES + ("rank", "share") and
                         "Dates from the %s column." % date in (a.get("method") or "") for a in items)
    st = rep.get("story") or {}
    for k in ("cannot_answer", "what_happened", "why", "whats_next"):
        lines = []
        for x in st.get(k) or []:
            if x == _NO_DATES_LINE and dated:
                x = ("The engine reads no column as dates for its monthly tests, so it tested no monthly change and "
                     "made no forecast; the AI plan's analyses read the dates from %s." % date)
            elif x == _narrate.NO_FACTS_LINE:
                x = ("The engine's own tests produced no graded finding; the AI plan's analyses are descriptive "
                     "and were not graded.")
            lines.append(x)
        st[k] = lines


def _layout_notes(rep: Dict[str, Any], lay: Dict[str, Any]) -> None:
    """Say in the report how a long statistical table was read (limitations and cleaning)."""
    n = lay["series"]
    unit_note = (" The series come in %d units (%s), so they are never added or averaged together."
                 % (len(lay["units"]), ", ".join(lay["units"][:4])) if len(lay["units"]) > 1 else "")
    parts = [
        "This file is a long statistical table: %s rows, one row per date and series, all values in the "
        "column %s, %d series%s." % (format(lay["rows_in"], ","), lay["value_column"], n,
                                    (" named by %s" % lay["series_column"]) if lay["series_column"] else ""),
        "It was read as one column per series, one row per date with a value (%s dates from %s), so every "
        "series is analysed on its own.%s" % (format(lay["rows_out"], ","), lay["start"], unit_note),
    ]
    parts += ["Set aside as %s: %s." % (why, ", ".join(ks)) for why, ks in lay["set_aside"].items()]
    if lay["metadata_set_aside"]:
        parts.append("The agency's metadata columns (%s) were set aside: they describe the series, they are "
                     "not measures." % ", ".join(lay["metadata_set_aside"]))
    if lay["constant_set_aside"]:
        parts.append("Columns with one value throughout (%s) were set aside as well."
                     % ", ".join(lay["constant_set_aside"]))
    if lay.get("lead"):
        parts.append("The report leads with %s: %s." % (lay["lead"], lay["lead_why"]))
    rep["limitations"].insert(0, {"kind": "data", "finding_ids": [], "text": " ".join(parts)})
    fixes = rep.setdefault("cleaning", {}).setdefault("fixes", [])
    fixes.insert(0, {"rule": "long_to_wide", "column": lay["series_column"] or lay["value_column"],
                     "count": lay["rows_in"], "what": "A long table of %d series turned into one column per series, "
                     "one row per date (%s rows in, %s dates out; %d series compared from %s)"
                     % (n, format(lay["rows_in"], ","), format(lay["rows_out"], ","), lay["kept"], lay["start"])})
    if lay["zeros_as_empty"]:
        fixes.insert(1, {"rule": "closed_day_zeros", "column": lay["value_column"], "count": lay["zeros_as_empty"],
                         "what": "Exact zeros on Saturdays and Sundays in series that are otherwise always positive "
                                 "read as empty: they mark days with no value, not a value of zero"})
    rep.setdefault("input", {})["layout"] = lay


def _tidy_left_out_rows(rep: Dict[str, Any], tidy: Optional[Dict[str, Any]]) -> None:
    """The rows the adapter left out before the file was read (total rows, a month the file stops in the middle of) are rows SET ASIDE, not rows
    that were never there: they are counted in the input and the cleaning, listed with their reason among the quarantined rows and in the
    quarantine download (with their line in the visitor's file), and never lost."""
    lo = (tidy or {}).get("left_out_rows")
    if not lo:
        return
    import csv as _csv
    frame = lo["frame"]
    n = int(len(frame))
    rep["input"]["rows"] = int(rep["input"].get("rows") or 0) + n
    cl = rep.setdefault("cleaning", {})
    cl["rows_in"] = int(cl.get("rows_in") or 0) + n
    cl["rows_quarantined"] = int(cl.get("rows_quarantined") or 0) + n
    by: Dict[str, int] = {}
    for r in lo["reasons"]:
        by[r] = by.get(r, 0) + 1
    qr = cl.setdefault("quarantine_reasons", [])
    for r, c in by.items():
        qr.append({"reason": r, "count": c})
    qr.sort(key=lambda x: (-x["count"], x["reason"]))
    dl = rep.get("downloads") or {}
    from northledger.clean import QUARANTINE_COL
    text = str(dl.get("quarantine_csv") or "")
    if text.strip():
        header = next(_csv.reader(io.StringIO(text)))
    else:
        clean_head = next(_csv.reader(io.StringIO(str(dl.get("clean_csv") or ""))), [])
        header = [h for h in clean_head if h] + ([QUARANTINE_COL] if clean_head else [])
    if not header:
        return
    slug = {_engine_slug(c): c for c in frame.columns}
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    if not text.strip():
        w.writerow(header)
    for k in range(n):
        row = []
        for h in header:
            if h == "source_line":
                row.append("" if lo["lines"][k] is None else lo["lines"][k])
            elif h == QUARANTINE_COL:
                row.append(lo["reasons"][k])
            else:
                src = slug.get(h)
                row.append(str(frame.iloc[k][src]) if src is not None else "")
        w.writerow(row)
    dl["quarantine_csv"] = (text if text.endswith("\n") or not text else text + "\n") + buf.getvalue()


def _tidy_notes(rep: Dict[str, Any], tidy: Optional[Dict[str, Any]], date_notes: List[Dict[str, Any]],
                number_notes: Optional[List[Dict[str, Any]]] = None) -> None:
    """Say in the report what was done to a plain file before it was read (wave 5f): the dates rewritten, the total rows left out and the month
    the file stops in the middle of left out of the comparison. Limitations (kind "data") and cleaning fixes; the headline carries a word
    only where a total row was left out WITHOUT proof."""
    lim, fixes = [], []
    for n in date_notes or []:
        fixes.append({"rule": "dates_read", "column": n["column"], "count": n["rows"],
                      "what": "Dates written %s in the column %s were read as dates (they were rewritten as year-month-day before the file was read)"
                              % (n["format"], n["column"])})
    for n in number_notes or []:
        fixes.append({"rule": "numbers_read", "column": n["column"], "count": n["rows"],
                      "what": "Numbers written with spaces between the thousands or a comma for the decimals in the column %s were read as "
                              "numbers (708 219,6 is 708219.6)" % n["column"]})
    unproven = []
    for t in (tidy or {}).get("totals") or []:
        who = "%r in the column %s" % (t["member"], t["column"])
        if t.get("left_out") and t["status"] == "verified":
            lim.append("%d rows named %s equal the sum of the other rows of the same date (checked cell by cell), so counting them would count every "
                       "amount twice: they were left out of the figures." % (t["rows"], who))
            fixes.append({"rule": "total_rows_left_out", "column": t["column"], "count": t["rows"],
                          "what": "Rows named %r equal the sum of the other rows: left out of the figures, never counted twice" % t["member"]})
        elif t.get("left_out"):
            unproven.append(t)
            lim.append("%d rows named %s could not be checked against the other rows (%s). They were left out of the figures, as a total is; "
                       "if %r is a branch of its own, the figures are short by its amounts." % (t["rows"], who, t["why"], t["member"]))
            fixes.append({"rule": "total_rows_left_out", "column": t["column"], "count": t["rows"],
                          "what": "Rows named %r could not be checked against the other rows; left out as a total is" % t["member"]})
        elif t.get("nomination") == "exact":
            lim.append("%d rows named %s are not the sum of the other rows (%s), so they were counted as a member of their own."
                       % (t["rows"], who, t["why"]))
    part = (tidy or {}).get("partial")
    if part:
        months = ", ".join(_mon(m) for m in part["months"])
        n = sum(part["rows_by_month"].values())
        which = "ends on %s" % part["last_date"] if part["months"][0] == part["months"][-1] and part["months"][0] > part["first_date"][:7] \
            else "starts on %s and ends on %s" % (part["first_date"], part["last_date"])
        lim.append("The file %s, in the middle of %s (%d %s rows). The months are compared whole, so %s %s left out of the comparison."
                   % (which, months, n, part["cadence"], months, "is" if len(part["months"]) == 1 else "are"))
        fixes.append({"rule": "partial_month_left_out", "column": (tidy or {}).get("date_column") or "date", "count": n,
                      "what": "%d rows of %s, a month the file stops in the middle of, were left out of the comparison" % (n, months)})
    for text in reversed(lim):
        rep["limitations"].insert(0, {"kind": "data", "finding_ids": [], "text": text})
    if fixes:
        rep.setdefault("cleaning", {}).setdefault("fixes", [])[0:0] = fixes
    if unproven:
        st = rep.get("story") or {}
        if st.get("headline"):
            st["headline"] = "%s (rows named %s were left out of these figures: they could not be checked against the other rows)" % (
                st["headline"].rstrip("."), " and ".join(repr(t["member"]) for t in unproven[:2]))


# ----------------------------------------------------------------------------- the structure of a statistical table
# WAVE 4, track A1 (plan/WAVE4-A-DESIGN.md sections 1, 2 and 4; engine/nl_structure.py). An official table holds totals
# beside their parts, an adjusted copy beside the unadjusted one and components beside their parents: the engine added
# its 36,735 rows and averaged 465 series. The structure is read from the engine's own reading after the visitor's
# decisions (a withheld column is never read) and before the business analysis, at two levels:
#   1. the fast path: the structure the scan's run (or the planner's profile) found under these same choices, looked up
#      before the AI plan is applied (the plan's row choices are then checked against it: check_rows);
#   2. the hook between the audit (run_loop) and the business analysis (run_analyze): the reading built early, the
#      structure detected and cached with the profile's facts.
# A usable structure is analysed as ONE series, the slice the structure chooses (the headline: the root of every
# hierarchy, the unadjusted copy, a measure's total), by running the whole engine on that slice (_run_slice: date and
# one measure column in base units); the report carries the file's privacy, rows and health, the structure and the
# estimand. A table whose readable columns cannot tell its rows apart (a dimension withheld) is refused with a plain
# reason; a business file whose members simply add up (N/S/E/W) and a panel with no relation are read as before.
STRUCTURE_ON = True                        # the tests switch it off to prove a business file is read as before
STRUCTURE_BUDGET_S = 1.0                   # (no longer read: wave 5e, P3 -- no wall-clock decision; nl_structure.WALL_GUARD_S only refuses a table that cannot be read at all)
STRUCTURE_LAYOUT = "structured cube slice"
_PROFILE_CACHE_STRUCTURE = "structure"
_PROFILE_CACHE_ERROR = "structure_error"      # the refusal made when the structure layer could not run on a table of series


def _ns() -> Any:
    import nl_structure
    return nl_structure


# ----------------------------------------------------------------------------- the series-table guard (wave 5b)
# The structure layer is an aid for most files and a safeguard for one kind: a table of series with totals (an official
# table: Statistics Canada, Eurostat, the ONS). Read the old way, such a table has its totals and its parts averaged
# together, a figure that looks right and is wrong. The page's Pyodide once failed to import nl_structure (a regex flag
# Python 3.12 refuses and 3.9 only warns about) and the engine silently read the file the old way: retail took 57 s and
# the figure was an average over 36,735 rows. So: when the structure layer cannot run (it cannot be imported, detect
# throws, the slice cannot be run) on a file that LOOKS LIKE a table of series with totals, no business analysis is made;
# the "did not run" path says so and `structure` is {kind: "error", usable: false, error: {stage, type, message}}.
# Any other file keeps the old behaviour. `looks_like_series_table` is the one place that decides what "looks like" means,
# and it must not depend on nl_structure (it is what runs when nl_structure cannot).
SERIES_GUARD_REASON = ("This file looks like a table of series with totals, and the part of the engine that finds them could "
                       "not run. An average over its rows would count totals and parts together, so no figure is shown.")
_GUARD_ROWS = 50000                         # rows read to decide what a file looks like (a failure path only)
_GUARD_FLAG_CODES = 20                      # a flag column: at most 20 short codes ...
_GUARD_FLAG_LEN = 4                         # ... of at most 4 characters ...
_GUARD_BLANK_MEASURE = 0.90                 # ... one of which is a publisher's code or has a blank measure on 90% of its rows
_GUARD_DIM_MAX = 400                        # a dimension: 2 to 400 members
_GUARD_MIN_DATES = 6
# the publishers' signature columns are read from engine/flag_vocab.json (the structure layer reads the same file); this is only
# the stand-in for a file that cannot be read, and a test keeps it equal to the file
_GUARD_SIGNATURES = {"statcan": ["REF_DATE", "DGUID", "VECTOR", "COORDINATE", "STATUS"],
                     "eurostat": ["TIME_PERIOD", "OBS_VALUE", "OBS_FLAG"]}
_GUARD_CODES = frozenset((":", "x", "..", "...", "F", "E", "A", "B", "C", "D", "r", "p", "c", "e", "b", "u", "z", "[x]", "[c]",
                          "[z]", "[u]", "n/a", "-", "*"))
_GUARD_FLAG_NAMES = frozenset(("status", "flag", "flags", "obsstatus", "obsflag", "confstatus", "obsconf", "symbol", "statut", "symbole", "estado"))
# wave 5e (P7): the names that count towards "3 or more metadata-like columns" are the ones only a statistical publisher uses; generic words (unit,
# units, status, flag, action, frequency, symbol, structure, footnote) are columns of any business file (an inventory has Unit, Status and Action)
_GUARD_META_SPECIFIC = frozenset((
    "dguid", "uom", "uomid", "scalarfactor", "scalarid", "vector", "coordinate", "terminated", "decimals", "obsstatus", "obsflag", "obsconf",
    "unitmult", "unitmultiplier", "confstatus", "timeformat", "lastupdate", "dataflow", "structureid", "vecteur", "coordonnee", "termine",
    "decimales", "unitedemesure", "iddelunitedemesure", "facteurscalaire", "iddufacteurscalaire", "unidaddemedida", "factorescalar",
    "dezimalstellen", "skalierung", "maeinheit", "masseinheit"))
_GUARD_GRADE_LETTERS = frozenset("ABCDEF")      # a quality letter of a publisher is also the grade of a student: never a flag on its own
_GUARD_ID = re.compile(r"^[A-Za-z]{0,4}[\s_-]?\d[\d.\-_]*$")
_GUARD_UNIT_WORD = re.compile(r"(?i)\b(?:dollars?|euros?|pounds?|persons?|people|number|percent(?:age)?|index|units?|tonnes?|hours?|"
                              r"personnes?|pourcent(?:age)?|nombre|indice|unidades?|personas?|porcentaje|personen|prozent|anzahl|"
                              r"millions?|thousands?)\b|%")
_GUARD_DATE_NAMES = frozenset(_PANEL_DATE) | {"year", "yr", "fiscalyear", "fy", "calendaryear", "obstime", "refperiod"}
_GUARD_ISO = re.compile(r"^\d{4}(?:[-/.]\d{1,2}){0,2}$")
_GUARD_PERIOD = re.compile(r"^(?:\d{4}\s*[-_/ ]?\s*[QqHhSs][1-4]|[Qq][1-4]\s*[-_/ ]?\s*\d{4}|\d{4}\s*[-_ ]?[Mm]\d{1,2})$")


def _guard_vocab() -> Tuple[Dict[str, List[str]], FrozenSet[str]]:
    """({publisher: signature columns}, every flag code any publisher lists) from engine/flag_vocab.json, beside this file;
    the stand-ins above when it cannot be read (the guard must still work then)."""
    try:
        with open(os.path.join(HERE, "flag_vocab.json"), encoding="utf-8") as fh:
            pubs = (json.load(fh) or {}).get("publishers") or {}
        sigs = {str(k): [str(x) for x in (v or {}).get("signature") or []] for k, v in pubs.items() if (v or {}).get("signature")}
        codes = frozenset(str(c) for v in pubs.values() for c in ((v or {}).get("codes") or {}))
        return sigs or dict(_GUARD_SIGNATURES), codes or _GUARD_CODES
    except (OSError, ValueError, AttributeError):
        return dict(_GUARD_SIGNATURES), _GUARD_CODES


def _guard_is_dates(f: Any, name: str) -> bool:
    """Whether a column's filled cells are dates or periods (95% of them, at least 6 different)."""
    import pandas as pd
    import warnings
    if int(f.nunique()) < _GUARD_MIN_DATES:
        return False
    sample = list(f.drop_duplicates().head(400))
    hits = sum(1 for v in sample if _GUARD_PERIOD.match(v) or (_GUARD_ISO.match(v) and (len(v) > 4 or name in _GUARD_DATE_NAMES)))
    if hits >= 0.95 * len(sample):
        return True
    if all(re.fullmatch(r"[-+]?[\d,]*\.?\d+", v) for v in sample):
        return False                               # numbers are never read as dates by their look alone
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        got = pd.to_datetime(pd.Series(sample), errors="coerce")
    return bool(got.notna().mean() >= 0.95)


def _guard_long_shape(df: Any) -> Optional[Dict[str, Any]]:
    """A long statistical table by what its columns hold: a date column, a measure column, two or more dimensions and a
    flag column (a few short codes, with blanks, one a publisher's code or standing for a blank measure)."""
    import pandas as pd
    if len(df) < 24:
        return None
    _sigs, codes = _guard_vocab()
    text = {c: df[c].astype(str).str.strip() for c in df.columns}
    date, numbers, texts = None, [], []
    for c in df.columns:
        f = text[c][text[c] != ""]
        if len(f) < 24:
            continue
        nums = pd.to_numeric(f.str.replace(",", "", regex=False), errors="coerce")
        n = _pnorm(c)
        numeric = bool(nums.notna().mean() >= 0.95)
        if date is None and (not numeric or n in _GUARD_DATE_NAMES) and _guard_is_dates(f, n):
            date = c
        elif numeric:
            if int(nums.nunique()) >= 2:
                numbers.append((c, f))
        else:
            texts.append(c)
    if date is None or not numbers:
        return None
    hinted = [x for x in numbers if _pnorm(x[0]) in _PANEL_VALUE]
    measure = (hinted[0] if hinted else max(numbers, key=lambda x: int(x[1].nunique())))[0]
    blank_measure = pd.to_numeric(text[measure].str.replace(",", "", regex=False), errors="coerce").isna()
    flags: List[str] = []
    dims: List[str] = []
    for c in texts:
        f = text[c][text[c] != ""]
        members = [str(v) for v in f.unique()]
        if not 1 <= len(members) <= _GUARD_DIM_MAX:
            continue
        short = len(members) <= _GUARD_FLAG_CODES and all(len(m) <= _GUARD_FLAG_LEN for m in members)
        if short:
            # a flag marks a missing value: a code whose rows have a blank measure (learned from the file), or a column the
            # publishers name as a flag (STATUS, OBS_FLAG ...) that holds one of their codes; a "status" of open/done, a "returned"
            # of Y/blank or a grade of A to D is a business file's own column, not a flag
            # a code, not a word: a status such as "Void" or "Hold" whose rows have no value is a business file's own column; a publisher's code
            # is a symbol or a letter or two (x, F, .., p, [x])
            predicts = any(int((text[c] == m).sum()) >= 3 and float(blank_measure[text[c] == m].mean()) >= _GUARD_BLANK_MEASURE
                           and not (len(m) >= 3 and m.isalpha())
                           for m in members)
            # a column the publishers name a flag holds one of their codes: never counting the letters A to F, which are a student's grade
            # as much as a publisher's quality letter (wave 5e, P7)
            named_flag = _pnorm(c) in _GUARD_FLAG_NAMES and any(m in codes and m not in _GUARD_GRADE_LETTERS for m in members)
            if predicts or named_flag:
                flags.append(c)
                continue
        if len(members) >= 2 and _pnorm(c) not in _PANEL_META:
            dims.append(c)
    if len(dims) < 2:
        return None
    if not flags:
        # no flag column (a table without suppressed cells, or in a language whose flag names are not known): a series id (one value for
        # each series, a vector or a coordinate) and a constant column that holds a unit of measure say the same (wave 5e, P7)
        sid = _guard_series_id(df, dims, text)
        unit = next((c for c in texts if c not in dims and c != date and int(text[c][text[c] != ""].nunique()) == 1
                     and _GUARD_UNIT_WORD.search(str(text[c][text[c] != ""].iloc[0]))), None) if sid else None
        if not (sid and unit):
            return None
        return {"date": str(date), "measure": str(measure), "dimensions": [str(d) for d in dims[:8]], "flags": [], "series_id": str(sid)}
    return {"date": str(date), "measure": str(measure), "dimensions": [str(d) for d in dims[:8]], "flags": [str(x) for x in flags]}


def _guard_series_id(df: Any, dims: List[str], text: Dict[str, Any]) -> Optional[str]:
    """A column that names the series (VECTOR, VECTEUR, a coordinate): its labels are ids (letters and digits only) and each is one-to-one with
    the combination of the dimension columns, whatever the column is called."""
    import pandas as pd
    if not dims:
        return None
    key = text[dims[0]]
    for d in dims[1:]:
        key = key + "\x1f" + text[d]
    n_key = int(key[key != ""].nunique())
    for c in df.columns:
        if c in dims:
            continue
        f = text[c][text[c] != ""]
        members = f.unique()
        if len(members) != n_key or len(members) < 2 or len(members) > 20000:
            continue
        if not all(_GUARD_ID.match(str(m)) for m in members[:400]):
            continue
        pair = pd.Series(list(zip(key, text[c]))).nunique()
        if pair == n_key:
            return str(c)
    return None


def looks_like_series_table(data: bytes) -> Optional[Dict[str, Any]]:
    """None, or why the file looks like a table of series with totals (an official table whose rows hold totals beside their
    parts), from the file's own columns, with no help from nl_structure. It does, when ANY of these holds:
      publisher   the header holds a publisher's signature columns (engine/flag_vocab.json: 3 of them, or all of a shorter one);
      metadata    the header holds 3 or more columns only a statistical publisher uses (_GUARD_META_SPECIFIC: UOM, VECTOR, DGUID,
                  SCALAR_FACTOR, VECTEUR, FACTEUR SCALAIRE ...; never Unit, Status or Action, which any business file has);
      long format a date column, a measure column, 2 or more dimension columns and a flag column (a code that stands for a blank measure,
                  or a publisher's code in a column they name a flag; never a grade A to F), or a series id and a constant unit.
    {"by": "publisher" | "metadata", "publisher"?, "columns": [the signature or metadata names]} or {"by": "long format",
    "dimensions": n, "flags": n}."""
    try:
        import pandas as pd
        first = data[:8192].decode("utf-8-sig", "replace").split("\n", 1)[0]
        sep = max(",;\t|", key=lambda c: (first.count(c), c == ","))          # the delimiter of the header line (the engine's intake sniffs it too)
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig", encoding_errors="replace",
                         sep=sep, nrows=_GUARD_ROWS)
    except Exception:  # noqa: BLE001 - a file the reader cannot parse is refused by the engine's own intake
        return None
    header = [str(c) for c in df.columns]
    norm = [_pnorm(h) for h in header]
    sigs, _codes = _guard_vocab()
    best: Optional[Tuple[str, List[str]]] = None
    for name, sig in sigs.items():
        s = [_pnorm(x) for x in sig]
        hit = [h for h, n in zip(header, norm) if n in s]
        if len(set(n for n in norm if n in s)) >= min(3, len(s)) and (best is None or len(hit) > len(best[1])):
            best = (name, hit)
    if best is not None:
        return {"by": "publisher", "publisher": best[0], "columns": best[1][:8]}
    meta = [h for h, n in zip(header, norm) if n in _GUARD_META_SPECIFIC]
    if len(meta) >= 3:
        return {"by": "metadata", "columns": meta[:8]}
    shape = _guard_long_shape(df)
    if shape is not None:
        # counts, never the columns' names: a dimension of a table may be a column the visitor withholds
        return {"by": "long format", "dimensions": len(shape["dimensions"]), "flags": len(shape["flags"])}
    return None


def _failure(stage: str, exc: BaseException) -> Dict[str, Any]:
    """{stage, type, message trimmed to 200 characters} of an exception; `_exc` (kept for a strict run) is never serialised."""
    return {"stage": stage, "type": type(exc).__name__, "message": " ".join(str(exc).split())[:200], "_exc": exc}


def _structure_import_failure() -> Optional[Dict[str, Any]]:
    """The failure of importing nl_structure (None when it imports)."""
    try:
        _ns()
        return None
    except Exception as exc:  # noqa: BLE001 - what is asked is whether the structure layer can run at all
        return _failure("import", exc)


def _guard_failure(data: bytes, fail: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `structure` record of a refusal when the structure layer could not run on a file that looks like a table of series
    with totals; None for any other file (it is read as before: a strict run raises the exception that stopped the layer)."""
    exc = fail.get("_exc")
    why = looks_like_series_table(data)
    if why is None:
        if exc is not None and os.environ.get("NL_BROWSER_STRICT"):
            raise exc
        return None
    err = {k: v for k, v in fail.items() if k != "_exc"}
    return {"kind": "error", "usable": False, "reason": SERIES_GUARD_REASON, "error": err, "looks_like": why}


SLICE_GUARD_REASON = ("This file is a table of series with totals, and the part of the engine that reads it one series at a time "
                      "could not run. An average over its rows would count totals and parts together, so no figure is shown.")


def _slice_failure(data: bytes) -> Optional[Dict[str, Any]]:
    """The `structure` record of a refusal when the structure layer found a usable structure, its slice could not be run (fewer
    than 2 values, or the engine's run on the slice stopped) and the file looks like a table of series with totals: it is never
    read as a plain table instead. None for any other file (a business file the layer found some structure in is read as before)."""
    why = looks_like_series_table(data)
    if why is None:
        return None
    return {"kind": "error", "usable": False, "reason": SLICE_GUARD_REASON,
            "error": {"stage": "slice", "type": "SliceNotRun",
                      "message": "the headline slice could not be run (fewer than 2 values, or the engine's run on it stopped)"},
            "looks_like": why}


VERDICT_GUARD_REASON = ("This file looks like a table of series with totals, and the engine could not read it as one (%s). An average over "
                        "its rows would count totals and parts together, so no figure is shown.")


def _refusable_verdict(S: Optional[Dict[str, Any]]) -> bool:
    """The layer's answer is one a table of series is refused for: "not a cube", or (wave 5f, E) a cube in which it found no headline slice
    (every series of the default slice is empty): it must not fall back to the row-average path either."""
    return isinstance(S, dict) and (S.get("kind") == "not_cube" or (S.get("kind") == "cube" and not S.get("usable")
                                                                  and S.get("code") == "no_default_slice"))


def _verdict_failure(data: bytes, S: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The `structure` record of a refusal when the structure layer ran and answered "not a cube" for a file that looks like a table of series
    with totals (wave 5e, P7): one reference period (a census table), more dimensions, series or cells than the layer reads, a table too large
    or too slow to read, a layout it does not know. The layer's own plain reason is the refusal's. None for any other file (read as before)."""
    if not _refusable_verdict(S):
        return None
    if S.get("code") == "second_measure" and not S.get("official"):
        return None          # a file with two number columns and no publisher's mark is a business export (read as before), whatever else it looks like
    why = looks_like_series_table(data)
    if why is None:
        return None
    reason = " ".join(str(S.get("reason") or "it is not a table the engine reads").split())
    return {"kind": "error", "usable": False, "reason": VERDICT_GUARD_REASON % reason[:160],
            "error": {"stage": "verdict", "type": "not_cube", "message": reason[:200]}, "looks_like": why}


PANEL_GUARD_REASON = ("This file looks like a table of series with totals, and its %d series stand in no relation to one another and could not "
                      "be set side by side. An average over its rows would mix them, so no figure is shown.")


def _panel_failure(data: bytes, S: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The `structure` record of a refusal when the structure layer found a table of series with no relation between its members (kind
    "panel_no_relations": read side by side by the long-table layout), the layout did not apply, and the layer could not read it one member at a time
    either (wave 5g, A): the OLD path would average the rows of every series together. None for any other file (a table of one series, a file
    that does not look like a table of series: read as before)."""
    if not (isinstance(S, dict) and S.get("kind") == "panel_no_relations" and S.get("dims")):
        return None
    why = looks_like_series_table(data)
    if why is None:
        return None
    reason = PANEL_GUARD_REASON % int(S.get("series") or 0)
    return {"kind": "error", "usable": False, "reason": reason,
            "error": {"stage": "panel", "type": "panel_no_relations", "message": " ".join(str(S.get("reason") or "").split())[:200]},
            "looks_like": why}


def _reason_in_sentence(rec: Dict[str, Any]) -> str:
    """The refusal's reason as the story's headline carries it ("The business analysis did not run: <this>.")."""
    r = str(rec.get("reason") or SERIES_GUARD_REASON)
    return r[0].lower() + r[1:]


def _structure_cached(sha: str, decisions: Any) -> Optional[Dict[str, Any]]:
    """The structure the last reading of these bytes found, when it ran under these choices; else None."""
    got = _PROFILE_CACHE.get("value") if _PROFILE_CACHE.get("sha") == sha else None
    if not got or not got.get("ok") or not _same_choices(got, decisions):
        return None
    return got.get(_PROFILE_CACHE_STRUCTURE)


def _structure_cached_error(sha: str, decisions: Any) -> Optional[Dict[str, Any]]:
    """The refusal the last reading of these bytes made (the structure layer could not run on a table of series), under these choices."""
    got = _PROFILE_CACHE.get("value") if _PROFILE_CACHE.get("sha") == sha else None
    if not got or not got.get("ok") or not _same_choices(got, decisions):
        return None
    return got.get(_PROFILE_CACHE_ERROR)


def _structure_detect(reading: Any, hidden: Any, wide: Optional[Dict[str, Any]] = None,
                      fail: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The structure of the table, or None. A caller that passes `fail` is handed the failure (the file is refused when it looks
    like a table of series: _guard_failure); without it the structure is an aid and the file is read as before."""
    try:
        S = _ns().detect(reading, set(hidden or ()))
        if wide and S is not None:
            S["wide"] = dict(wide)                 # the file was a wide table of periods, reshaped to long before it was read
        return S
    except Exception as exc:  # noqa: BLE001 - the structure is an aid; the file is then read as before
        if fail is not None:
            fail.update(_failure("detect", exc))
            return None
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return None


def _file_health(score: Any, findings: Iterable[Any], S: Dict[str, Any], withheld: Set[str], pub: Any,
                 cleaning: Dict[str, Any]) -> Dict[str, Any]:
    """structure.file_health: the engine's health of the whole file, its issues without the findings on a table's
    metadata and flag columns (an empty SYMBOL column, the letter case of STATUS codes, a constant UOM): those columns
    describe the series, they are not data. The core's score is unchanged."""
    meta = {str(m.get("landed")) for m in S.get("metadata") or [] if m.get("class") in
            ("constant", "empty", "flag", "series_id", "alias", "unit", "other", "member_id", "parent")}
    kept, dropped = [], []
    for x in findings or []:
        if not _issue_is_safe(x, withheld) or _is_text_numbers_line(x):
            continue
        line = pub(_plain(x))
        col = line.split(":", 1)[0].strip() if ":" in line else ""
        (dropped if col in meta else kept).append(line)
    return {"score": _num(score), "issues": kept, "dropped": dropped,
            "rows_in": cleaning.get("rows_in"), "rows_clean": cleaning.get("rows_clean"),
            "rows_quarantined": cleaning.get("rows_quarantined"),
            "note": ("the health of the whole file; findings on the table's metadata and flag columns (%s) are left out: "
                     "they describe the series, they are not data" % ", ".join(sorted(
                         m["column"] for m in S.get("metadata") or [] if m.get("landed") in meta))[:300]) if meta else ""}


def _sent_header(data: Optional[bytes]) -> List[str]:
    """The header row of the file as the visitor sent it (a publisher's signature columns are read from it)."""
    if not data:
        return []
    try:
        import pandas as _pd_h
        return [str(c) for c in _pd_h.read_csv(io.BytesIO(data), dtype=str, nrows=0, encoding="utf-8-sig").columns]
    except Exception:  # noqa: BLE001 - no header, no publisher signature
        return []


def _hidden_names(flagged: Iterable[Dict[str, Any]], colmap: Dict[str, str]) -> List[str]:
    """The columns the engine may not read, by landed name and by the file's header (a withheld or coded column)."""
    inv = {str(v): str(k) for k, v in dict(colmap or {}).items()}
    out: List[str] = []
    for f in flagged or []:
        if isinstance(f, dict) and f.get("decision") != "keep" and f.get("column"):
            out.append(str(f["column"]))
            if inv.get(str(f["column"])):
                out.append(inv[str(f["column"])])
    return out


def _outer_of(got: Dict[str, Any], rep: Dict[str, Any], data: Optional[bytes] = None) -> Dict[str, Any]:
    """What the slice's report takes from the file (the fast path: the cached reading's)."""
    inp = dict(rep.get("input") or {})
    inp["rows"] = int(got.get("rows") or 0)
    inp["columns"] = int(got.get("columns") or len(got.get("colmap") or {}) or 0)
    return {"input": inp, "flagged": [dict(f) for f in got.get("flagged") or []],
            "released": [dict(x) for x in got.get("released") or []], "file_health": got.get("file_health"),
            "header": _sent_header(data), "hidden": _hidden_names(got.get("flagged") or [], got.get("colmap") or {})}


def _hook_cache(sent: bytes, reading: Any, flagged: List[Dict[str, Any]], released: List[Dict[str, Any]],
                colmap: Dict[str, str], S: Dict[str, Any], audit: Any, wh_list: List[str], rep: Dict[str, Any],
                name: str, pub_lite: Any) -> Dict[str, Any]:
    """Cache the reading's profile facts with the structure (the planner's profile and a plan's run read them next) and
    return what the slice's report takes from the file."""
    import nl_viz as _nv
    hidden = {str(f["column"]): str(f["decision"]) for f in flagged if f.get("decision") != "keep"}
    cr = audit.clean
    cleaning = {"rows_in": int(cr.total_in), "rows_clean": int(cr.rows_clean), "rows_quarantined": int(cr.rows_quarantined)}
    fh = _file_health(audit.health.score, audit.health.findings or [], S, set(wh_list), pub_lite, cleaning)
    value: Dict[str, Any] = {"ok": True, "rows": reading.n, "flagged": [dict(f) for f in flagged],
                             "released": [dict(x) for x in released], "colmap": dict(colmap or {}),
                             _PROFILE_CACHE_STRUCTURE: S, "file_health": fh, "columns": int(rep["input"].get("columns") or 0)}
    try:
        pfacts = _profile_facts(reading, list(reading.land) or list(reading.values.columns), hidden)
        value.update(facts=pfacts, viz_stats=_nv.profile_stats(reading, pfacts))
    except Exception:  # noqa: BLE001 - the profile then lands the file itself
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        value["ok"] = False
    _PROFILE_CACHE.clear()
    _PROFILE_CACHE.update(sha=hashlib.sha256(sent).hexdigest(), value=value)
    inp = dict(rep["input"])
    return {"input": inp, "flagged": value["flagged"], "released": value["released"], "file_health": fh,
            "header": _sent_header(sent), "hidden": _hidden_names(flagged, colmap)}


def _quick_structure(data: bytes, keep: Optional[Set[str]] = None, fail: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The structure layer's reading of a file from its text, BEFORE it is landed (dates as dates, numbers as numbers, a column that looks personal
    left out as it will be after landing), or None. A caller that passes `fail` is handed the failure of the layer (it could not be asked); the
    layer's verdict "not a cube" for a table of series is left in fail["verdict"] (wave 5e, P7)."""
    try:
        import numpy as np
        import pandas as pd
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        # wave 5d: a column that looks personal is a withheld column here too (it is, after landing): the table is judged without it,
        # so an account owner beside every region is not a dimension that displaces the regions
        kept = set(keep or ())
        meta_like = [c for c in df.columns if _pnorm(c) in _PANEL_META or _pnorm(c) in _PANEL_DATE or _pnorm(c) in _PANEL_VALUE]
        aside = [c for c in _raw_personal_columns(df, [c for c in df.columns if c not in meta_like])
                 if c not in kept and _engine_slug(c) not in kept]
        if aside:
            df = df.drop(columns=aside)
        land = {h: _engine_slug(h) for h in df.columns}
        if len(set(land.values())) < len(land):
            return None
        texts = df.rename(columns=land)
        values = texts.copy().astype(object)
        for c in values.columns:
            t = texts[c].str.strip()
            f = t[t != ""]
            if not len(f):
                continue
            num = pd.to_numeric(f.str.replace(",", "", regex=False), errors="coerce")
            if num.notna().mean() >= 0.95:
                values[c] = pd.to_numeric(t.str.replace(",", "", regex=False), errors="coerce")
            elif f.str.match(r"^\d{4}-\d{2}(?:-\d{2})?$").mean() >= 0.95:
                values[c] = pd.to_datetime(t.where(t != ""), errors="coerce")
        R = _Reading(values, texts, np.ones(len(df), bool), land, {})
        S = _ns().detect(R, ())
        if _refusable_verdict(S) and fail is not None:
            fail["verdict"] = S          # wave 5e (P7): the layer's answer "not a cube" for a table of series is a refusal, never a reshape
        return S
    except Exception as exc:  # noqa: BLE001 - the layout pass then reads it as before
        if fail is not None:
            fail.update(_failure("detect", exc))
            return None
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return None


def _long_has_structure(data: bytes, fail: Optional[Dict[str, Any]] = None, keep: Optional[Set[str]] = None) -> bool:
    """Whether a long table the layout pass would turn into one column per series holds totals beside their parts (or
    an adjusted copy): a quick reading of the file's text (dates as dates, numbers as numbers), only to decide not to
    reshape it; the structure itself is read after landing, from the engine's reading, without a withheld column. A caller
    that passes `fail` is handed the failure of the structure layer (it could not be asked), and the answer is False."""
    S = _quick_structure(data, keep, fail)
    if S is None:
        return False
    return bool(S.get("usable")) and any(d["role"] in ("partition", "hierarchy", "adjustment", "components",
                                                       "rate_aggregate", "parts")
                                         or (d["role"] == "single" and _ns().reads_one_member(S))
                                         for d in S.get("dims") or [])


def _private_of(got: Dict[str, Any]) -> Any:
    """private(name) for the fast path, from the cached reading's flags: "withhold" or "code" for a flagged column the
    visitor did not keep (by its landed name, its header or its slug), else None (as run()'s own private())."""
    hidden = {str(f["column"]): str(f["decision"]) for f in got.get("flagged") or [] if f.get("decision") != "keep"}
    colmap = dict(got.get("colmap") or {})

    def private(name: Any) -> Optional[str]:
        if name is None or not hidden:
            return None
        n = str(name)
        for k in (n, colmap.get(n), _engine_slug(n)):
            if k and k in hidden:
                return hidden[k]
        return None
    return private


def _structure_plan(S: Dict[str, Any], ai_plan: Dict[str, Any], positions: Any, row_steps: bool
                    ) -> Tuple[Dict[str, Any], str, str, List[Dict[str, Any]]]:
    """(the slice's where, its id, the plan's part in it, the corrections): the plan's slice id; else, when the plan set
    rows aside, the slice its kept rows form, or the default slice when they mix a total with its parts, both adjusted
    copies, a component with its parent or units (ai_corrected, never sent back as a plan signal); else the default."""
    NS = _ns()
    where, sid_, psrc, corr = _structure_plan_rows(S, ai_plan, positions, row_steps)
    mm = ai_plan.get("measure_member")
    mdim = next((d for d in S.get("dims") or [] if d.get("measure_dim")), None)
    if mm and mdim is not None:
        member = next((x for x in mdim["measures"] if x["id"] == mm and not x["precision"]), None)
        if member is not None:
            w2 = dict(where, **{mdim["column"]: member["name"]})
            if NS._series_exist(S, w2):
                same = next((x for x in S["slices"] if x["where"] == w2), None)
                return w2, (same["id"] if same else "plan"), ("ai" if psrc == "engine_default" else psrc), corr
            ai_plan.setdefault("refused", []).append(
                "measure_member %s (%s) has no series at the headline's other members, so the default measure was kept" % (
                    mm, member["name"][:60]))
    return where, sid_, psrc, corr


def _structure_plan_rows(S: Dict[str, Any], ai_plan: Dict[str, Any], positions: Any, row_steps: bool
                         ) -> Tuple[Dict[str, Any], str, str, List[Dict[str, Any]]]:
    NS = _ns()
    sid = str(ai_plan.get("slice") or "")
    s = NS.slice_by_id(S, sid) if sid else None
    if s is not None:
        return dict(s["where"]), s["id"], "ai", []
    if row_steps:
        viol = NS.check_rows(S, positions)
        if viol:
            return dict(S["default"]), "S1", "ai_corrected", viol
        where = NS.plan_where(S, positions)
        same = next((x for x in S["slices"] if x["where"] == where), None)
        return where, (same["id"] if same else "plan"), "ai", []
    return dict(S["default"]), "S1", "engine_default", []


def _inner_plan(ai_plan: Dict[str, Any], S: Dict[str, Any], where: Dict[str, Any], col: str, raw_context: Any
                ) -> Tuple[Dict[str, Any], List[str]]:
    """The AI plan as the slice's run reads it: the plan's goal and words, the date and the slice's measure column
    (typed by the structure: a flow or a count adds up, anything else is a level), the analyses that read only the
    measure and the date, and the charts; an analysis that groups or filters by a structure dimension is refused (its
    rows would mix totals and parts; the breakdowns answer it)."""
    NS = _ns()
    st = NS.slice_type(S, where)
    stype = {"flow": "flow_amount", "count": "count"}.get(st["type"], "level") if NS.sums_over_time(st) else "level"
    mh, dh = S["measure"]["column"], S["date"]["column"]
    pcs = {str(c.get("name")): c for c in ai_plan.get("columns") or [] if isinstance(c, dict)}
    pv = pcs.get(mh) or {}
    cols = [{"name": dh, "semantic_type": "date", "role": "date"},
            {"name": col, "semantic_type": stype, "role": "target", "unit": str(pv.get("unit") or "")}]
    if pv.get("label"):
        cols[1]["label"] = pv["label"]
    rename = {mh: col, col: col, dh: dh}
    analyses, refused = [], []
    dims = {d["column"] for d in S["dims"]}
    for a in ai_plan.get("analyses") or []:
        cs = [str(c) for c in a.get("columns") or []]
        if cs and all(c in rename for c in cs) and not a.get("by"):
            analyses.append(dict(a, columns=[rename[c] for c in cs]))
        else:
            used = [c for c in cs + ([a["by"]] if a.get("by") else []) if c in dims]
            refused.append("%s: %s" % (a.get("type"), (
                "it reads %s, a dimension of a table of series whose rows mix totals and parts; the structure's "
                "breakdowns answer it" % ", ".join(used[:2])) if used else
                "it reads columns the slice does not carry (the analysis runs on the headline series)"))
    charts = []
    for c in ai_plan.get("charts") or []:
        cs = [str(x) for x in c.get("columns") or []]
        charts.append(dict(c, columns=[rename.get(x, x) for x in cs]))
    out = {k: ai_plan.get(k) for k in ("goal", "understanding", "kind", "goal_candidates", "quality_risks")}
    out.update(columns=cols, operations=[], analyses=analyses, charts=charts, primary=col, context=raw_context)
    return out, refused


def _checked_sentence(S: Dict[str, Any]) -> str:
    """What was checked, at the evidence level the table reached (wave 5e, P8): "Each total was checked against its parts" only when every
    total in the table was; a total the table names that no cell could check, a rate's named aggregate and a member shown by dominance
    each say what they are. Nothing is said of a check that did not happen."""
    NS = _ns()
    out: List[str] = []
    nu = next((d for d in S["dims"] if NS._named_unverified(d)), None)
    if nu:
        out.append("%s is the named total of %s; it could not be checked against its parts: %s cannot be summed." % (
            nu["total"], nu["column"], "an index" if S["measure"].get("type") == "index" else "a rate"))
    totals = [d for d in S["dims"] if d["role"] in ("partition", "hierarchy") or (d["role"] == "rate_aggregate" and not NS._named_unverified(d))]
    named = [d for d in totals if d.get("evidence") == "named"]
    for d in named[:2]:
        out.append("%s is named as the total of %s; it could not be checked against its parts (%s)." % (
            d["total"], d["column"], NS._why_unchecked(d.get("sum_check") or {})))
    if totals and not named and not nu:
        out.append("Each total was checked against its parts.")
    elif len(totals) > len(named):
        out.append("The other totals were checked against their parts.")
    for d in [d for d in S["dims"] if d["role"] == "single"][:2]:
        out.append("%s: one member (%s) is shown; none was verified as the total of the others, so they are never added." % (
            d["column"], d["total"]))
    return " ".join(out)


def _structure_notes(rep: Dict[str, Any], S: Dict[str, Any], est: Dict[str, Any], info: Dict[str, Any]) -> None:
    """Say how the table was read: the limitations' first line and a cleaning step."""
    roles = "; ".join("%s: %s" % (d["column"], d["role"].replace("_", " ")) for d in S["dims"] if d["role"] != "constant")
    per = S.get("period") or {"nouns": "months", "adjective": "monthly"}
    checked = _checked_sentence(S)
    has_parts = any(d["role"] == "parts" for d in S["dims"])
    verified = any(d["role"] in ("partition", "hierarchy") and (d.get("evidence") or "verified") == "verified" for d in S["dims"])
    if has_parts:
        tail = ("The table has no total row: the headline is the sum of its parts" +
                (", month by month" if int(per.get("step") or 1) == 1 else "") +
                ("; the other totals were checked against their parts." if verified else "."))
    else:
        tail = checked
    text = ("This file is a statistical table: %s rows, %s series over %s %s (%s). Adding its rows would count the "
            "same value more than once, so the report reads one series, the headline the structure chooses: %s. "
            "%s" % (
                format(int(S.get("rows") or 0), ","), format(int(S.get("series") or 0), ","),
                format(int(S.get("months") or 0), ","), per["nouns"], roles, est.get("text") or "", tail)).rstrip()
    if S.get("wide"):
        w_ = S["wide"]
        text += (" The file was a wide table (%d columns of %s, %s to %s); it was reshaped to one row per series and %s, and a "
                 "flag written in a cell (a colon, a letter after the number) was read as the publisher's flag, not as part of "
                 "the value." % (int(w_.get("periods") or 0), "years" if w_.get("family") == "year" else "periods",
                                 w_.get("first"), w_.get("last"), per.get("noun") or "period"))
    rep["limitations"].insert(0, {"kind": "data", "finding_ids": [], "text": text})
    rep.setdefault("cleaning", {}).setdefault("fixes", []).insert(0, {
        "rule": "structure_slice", "column": S["measure"]["column"], "count": int(S.get("rows") or 0),
        "what": "A statistical table read as one series: %s rows to %s %s values of %s (base units, the file's "
                "scale applied)" % (format(int(S.get("rows") or 0), ","), format(int(info.get("rows") or 0), ","),
                                     per["adjective"], info.get("column"))})


def _run_slice(S: Dict[str, Any], where: Dict[str, Any], slice_id: str, plan_source: str,
               corrections: List[Dict[str, Any]], *, name: str, objective: str, as_of: Optional[str],
               ai_plan: Optional[Dict[str, Any]], raw_context: Any, outer: Dict[str, Any],
               timings: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """The engine's whole run on the slice (date and one measure column, base units), then the file's own figures put
    back: its rows, columns, size and hash, its privacy decisions and releases, its health (structure.file_health), the
    plan as applied to the file. None when the slice cannot be run (the caller reads the file as before)."""
    NS = _ns()
    body, info = NS.slice_bytes(S, where)
    if info["rows"] < 2:
        return None
    inner_dec: Dict[str, Any] = {"__structure_inner__": {"S": S, "where": where, "slice_id": slice_id,
                                                         "plan_source": plan_source, "corrections": corrections,
                                                         "column": info["column"], "info": info,
                                                         "momentum_slice": (ai_plan or {}).get("momentum_slice")}}
    refused_analyses: List[str] = []
    if ai_plan:
        inner_dec["__plan__"], refused_analyses = _inner_plan(ai_plan, S, where, info["column"], raw_context)
    rep = run(body, name, objective if objective != DEFAULT_OBJECTIVE else "", inner_dec, as_of)
    if not rep.get("ok"):
        return None
    inp = rep["input"]
    inp.update({k: outer["input"][k] for k in ("name", "bytes", "rows", "columns", "sha256") if k in outer["input"]})
    inp["layout"] = {"layout": STRUCTURE_LAYOUT, "slice": slice_id, "where": where, "column": info["column"],
                     "rows_in": outer["input"].get("rows"), "rows_out": info["rows"], "series": S.get("series")}
    rep["reproducibility"]["input_sha256"] = inp["sha256"]
    rep["privacy"] = {"flagged": list(outer.get("flagged") or []), "released": list(outer.get("released") or [])}
    st = rep.get("structure") or {}
    st["file_health"] = outer.get("file_health")
    rep["structure"] = st
    # T4, an official aggregate is described, not tested (track A2): the slice's own run called _official_inference once
    # with the slice's two-column header; this is the call that matters, with the structure and the estimand copied on
    # just above and the FILE's header (its publisher's signature columns) and withheld columns. It replaces the first
    # call's records (the function owns findings[].inference)
    _official_inference(rep, list(outer.get("header") or []), {"layout": STRUCTURE_LAYOUT, "structure_slice": True,
                                                              "rows_a_month": int(NS.rows_a_month(S))},
                        list(outer.get("hidden") or []))
    # the core's headline of a table's report is its forecast's ("Monthly total_retail_sales forecast at 73,046,640,000
    # total_retail_sales for 2026-08."): with an estimand, the headline says what the table's headline is (wave 4, B)
    hl = _estimand_headline(rep)
    if hl and isinstance(rep.get("story"), dict):
        rep["story"]["headline"] = hl
        # the bottom line's first sentence is the same claim (it read "Average total retail sales is up 3.5% on the year
        # before (too little data to judge)": the monthly average's own grade, not what the table's headline is)
        lines = (rep.get("summary") or {}).get("lines") or []
        if lines and isinstance(lines[0], dict) and lines[0].get("kind") == "moved":
            lines[0]["text"] = hl
    _estimand_units(rep, NS.local(S, where), _engine_slug(info["column"]))
    if ai_plan:
        inner_plan = rep.get("ai_plan") or {}
        keep = {k: inner_plan[k] for k in ("context_queries", "context_queries_dropped", "context") if k in inner_plan}
        rep["ai_plan"] = dict(ai_plan, **keep)
        rep["ai_plan"]["structure_slice"] = {"id": slice_id, "where": where, "plan_source": plan_source,
                                            "column": info["column"]}
        if refused_analyses:
            aa = rep.setdefault("ai_analyses", {"items": [], "refused": []})
            aa["refused"] = list(aa.get("refused") or []) + refused_analyses
        if plan_source == "ai_corrected":
            rep["plan_signals"] = []          # a correction is disclosed, never sent back to the planner
    per = S.get("period") or {}
    if int(per.get("step") or 1) != 1:
        # a quarterly or an annual table: the charts that draw a year as 12 months or a window as 12 months are not drawn,
        # and the engine's own sentences say quarters or years, not months (the numbers stand)
        drawn = [c for c in rep.get("charts") or [] if isinstance(c, dict) and c.get("type") in ("heatmap", "trend", "fan", "replay")]
        if drawn:
            rep["charts"] = [c for c in rep["charts"] if c not in drawn]
            sup = rep.setdefault("charts_suppressed", [])
            for c in drawn:
                sup.append({"rule": "period", "type": str(c.get("type")),
                            "why": "the chart reads monthly values; this table is %s" % per.get("adjective")})
        for k in ("story", "summary", "findings", "methods", "limitations", "charts", "forecast", "cleaning"):
            if k in rep:
                rep[k] = _period_rewrite(rep[k], per)
        # wave 5c: the claims, chart records and labels the adapter copied from the core's text before it was rewritten
        for k in ("scenarios", "viz", "charts", "summary"):
            if k in rep:
                rep[k] = _window_rewrite(rep[k], per)
    tm = {t["stage"]: float(t["seconds"]) for t in rep.get("timings") or []}
    for k, v in (timings or {}).items():
        tm[k] = tm.get(k, 0.0) + float(v or 0.0)
    rep["timings"] = [{"stage": s, "seconds": round(tm.get(s, 0.0), 3)} for s in STAGES]
    return rep


def _structure_inner_blocks(rep: Dict[str, Any], inner: Dict[str, Any]) -> None:
    """In the slice's own run: rep["structure"] and rep["estimand"], in the engine's own windows (the headline claim's
    chart), with S1's monthly values reconciled to the engine's charted series (1e-6)."""
    NS = _ns()
    S, where = inner["S"], inner["where"]
    S = NS.local(S, where)                  # the slice's own measure (a member of a measure dimension has its own unit)
    months, vals = NS._monthly(S, where)
    win = None
    chart = None
    for c in rep.get("charts") or []:
        if isinstance(c, dict) and c.get("type") == "trend_windows" and c.get("finding_ids"):
            f = next((x for x in rep.get("findings") or [] if x.get("id") == c["finding_ids"][0]), None)
            if f is not None and f.get("kind") == "business" and not str(f.get("id") or "").startswith("measure.volume"):
                chart = c
                break
    if NS._cad(S):
        chart = None                          # a weekly or a daily table: the core's windows are calendar months; the estimand's are whole weeks or days
    if chart is not None and (chart.get("data") or {}).get("windows"):
        w = chart["data"]["windows"]
        win = {"prior": list(w["prior"]), "latest": list(w["latest"])}
    if win is None:
        win = NS.windows(months, vals, S)
    why = (NS.slice_by_id(S, inner.get("slice_id") or "") or {}).get("why") or {}
    est = NS.estimand(S, where, win, inner.get("plan_source") or "engine_default", why)
    est["slice_id"] = inner.get("slice_id")
    rec = None
    if chart is not None:
        cm, cv = chart["data"].get("months") or [], chart["data"].get("values") or []
        mine = dict(zip(months, vals))
        bad = [m for m, v in zip(cm, cv) if (win["prior"][0] <= m <= win["latest"][1]) and v is not None and
               (m not in mine or not (abs(float(mine[m]) - float(v)) <= 1e-6 * max(1.0, abs(float(v)))))]
        rec = not bad
    est["reconciles"] = rec
    est["corrections"] = list(inner.get("corrections") or [])
    rep["estimand"] = est
    pub = NS.public(S)
    pub["corrections"] = list(inner.get("corrections") or [])
    pub["slice"] = {"id": inner.get("slice_id"), "where": where, "column": inner.get("column")}
    rep["structure"] = pub
    _structure_notes(rep, S, est, inner.get("info") or {})


def _official_inference(rep: Dict[str, Any], header: List[str], layout: Optional[Dict[str, Any]],
                        hidden: Iterable[str]) -> None:
    """T4 (wave 4, plan/WAVE4-A-DESIGN.md 3): an official aggregate is described, not tested. findings[].inference
    is set on the business change claims about the published measure (never a row count) when (1) the file's header
    holds a publisher's signature columns (nl_inference.publisher_of), (2) the headline is the root of every
    additive dimension: with track A1's structure, its slice member of each partition or hierarchy is that
    dimension's total; without it, the engine read no dimension of two or more values (the other text columns hold
    one value each), or a long table one column per series (each series analysed on its own), and (3) no column
    publishes sampling errors (a "Statistics" dimension with standard errors). The engine still grades the claim;
    the record says what that grade is a grade of. A1 calls this again on the inner report of a structured slice,
    after copying its structure onto it (the file's header and withheld columns, the structure's own flags and the
    estimand's own 12-month figures: with a structure, the status column is read from structure.flags, because the
    slice's health holds only the date and the measure, and the described change is the estimand's)."""
    import nl_inference as _ni
    for f in rep.get("findings") or []:
        f["inference"] = None                 # this function owns the field: a second call replaces the first's records
    est_rec = rep.get("estimand") if isinstance(rep.get("estimand"), dict) else None
    if est_rec is not None:
        est_rec["inference"] = None           # the estimand's own slot (track A1 reserved it): the headline claim's record
        if est_rec.get("single_member"):
            return                            # one member of a table with no total member is not a published total: not described as one
    st = rep.get("structure") if isinstance(rep.get("structure"), dict) else None
    pubr = _ni.publisher_of(header)
    if pubr is None and st and st.get("publisher"):
        pubr = {"key": str(st["publisher"]), "name": str(st["publisher"]), "columns": []}
    if pubr is None:
        return
    hide = set(str(h) for h in hidden)
    cols = [{"name": c.get("name"), "values": [v for v, _n in (c.get("top_values") or [])]}
            for c in (rep.get("health") or {}).get("columns") or []] + [{"name": h} for h in header]
    if _ni.publishes_errors(cols):
        return
    if st:
        sl = {str(x.get("dim")): str(x.get("member")) for x in ((rep.get("estimand") or {}).get("slice") or [])}
        for d in st.get("dims") or []:
            if d.get("role") in ("partition", "hierarchy") and sl.get(str(d.get("column"))) != str(d.get("total")):
                return
        tot_dims = [d for d in st.get("dims") or [] if d.get("role") in ("partition", "hierarchy")]
        unchecked = [d for d in tot_dims if (d.get("sum_check") or {}).get("verified") is False]
        why_total = ("the headline is the total of %s (sum-check)" % " and ".join(str(d.get("column")) for d in tot_dims)) \
            if tot_dims and not unchecked else \
            ("the headline is %s, named as the total of %s; no cell could check it against its parts" % (
                unchecked[0].get("total"), unchecked[0].get("column"))) if unchecked else \
            "the headline is one published series"
        for d in st.get("dims") or []:
            # wave 5c: the headline is a rate's or an index's aggregate by its name alone: no sum-check could verify it, and the
            # record says so (the headline is still named, so it is still described as a published total)
            if d.get("role") == "rate_aggregate" and (d.get("sum_check") or {}).get("verified") is False \
                    and sl.get(str(d.get("column"))) == str(d.get("total")):
                why_total = "the headline is %s, %s" % (d.get("total"), _ns().named_total_words(str((st.get("measure") or {}).get("type") or "rate")))
        bf = (rep.get("estimand") or {}).get("built_from") if isinstance(rep.get("estimand"), dict) else None
        if isinstance(bf, dict) and bf.get("n"):
            # a table with no total row: the headline is the sum of its published parts (nothing is sampled by the sum)
            why_total = "the headline is the sum of the table's %d published %s (it has no total row)" % (
                int(bf["n"]), str(bf.get("noun") or "members"))
    elif layout:
        why_total = "a long table read one column per series: the headline is one published series (%s)" % \
            str(layout.get("lead") or "")
    else:
        if (rep.get("roles") or {}).get("dimensions"):
            return
        ones = [c for c, why in ((rep.get("roles") or {}).get("excluded") or {}).items()
                if "fewer than two values" in str(why) and c not in hide]
        why_total = ("the headline is the one published series (%s %s one value throughout)"
                     % (" and ".join(ones), "holds" if len(ones) == 1 else "each hold")) if ones else \
            "the headline is the one published series"
    led: Dict[str, Any] = {}
    try:
        for x in (json.loads(rep["downloads"]["ledger_json"]).get("analysis_ledger") or []):
            led[str(x.get("id"))] = x
    except (ValueError, KeyError, TypeError, AttributeError):
        led = {}
    flag = next((c for c in (rep.get("health") or {}).get("columns") or []
                 if _pnorm(c.get("name")) in ("status", "obsstatus", "obsflag") and c.get("name") not in hide), None)
    quality, revisions = None, ""
    if flag is not None:
        codes = {str(v): int(n) for v, n in (flag.get("top_values") or [])}
        quality = {"column": str(flag["name"]), "codes": codes}
        rv, pr = codes.get("r", 0), codes.get("p", 0)
        revisions = ("the file marks %d value%s revised and %d preliminary" % (rv, "" if rv == 1 else "s", pr)
                     if rv or pr else "the file marks no value as revised or preliminary")
    elif st and (st.get("flags") or {}).get("quality_of_headline") is not None:
        # a table read by its structure (track A1): the slice's own health holds no status column; the structure's
        # flags give the headline's quality codes (months by code) and the publisher's revised/preliminary marks
        fl = st["flags"]
        codes = {str(k): int(v) for k, v in (fl.get("quality_of_headline") or {}).items()}
        kinds = {k: (v or {}).get("kind") for k, v in (fl.get("codes") or {}).items()}
        rv = sum(n for k, n in codes.items() if kinds.get(k) == "revised")
        pr = sum(n for k, n in codes.items() if kinds.get(k) == "preliminary")
        quality = {"column": str(fl.get("column") or ""), "codes": codes}
        revisions = ("the file marks %d month%s of the headline revised and %d preliminary" % (rv, "" if rv == 1 else "s", pr)
                     if rv or pr else "the file marks no month of the headline as revised or preliminary")
    else:
        revisions = "revisions are not stated in what the engine read (the file's status column is not among them)"
    m = (st or {}).get("measure") or {}
    est_fig = ((rep.get("estimand") or {}).get("figures") or {}) if st else {}
    for f in rep.get("findings") or []:
        if f.get("kind") != "business" or f.get("estimand") != "ratio_of_average_month" \
                or f["id"].startswith("measure.volume") or f.get("parent_id"):
            continue
        base = f["id"][:-len(".change")] if f["id"].endswith(".change") else f["id"].rsplit(".", 1)[0]
        total = ".total." in f["id"]
        prior = next((led[k]["value"] for k in (base + ".prior12_mean", base + ".prior12") if k in led), None)
        latest = next((led[k]["value"] for k in (base + ".last12_mean", base + ".last12") if k in led), None)
        if (est_fig.get("prior") or {}).get("value") is not None and (est_fig.get("latest") or {}).get("value") is not None:
            # the estimand's own 12-month figures (the finding's are monthly averages): the same percent, in the
            # headline's own words
            prior, latest, total = est_fig["prior"]["value"], est_fig["latest"]["value"], \
                str(m.get("aggregation") or "").startswith("sum")
        words = ("a %s in %s" % (m.get("type") or "measure", m.get("uom") or "its own units")) if m else \
            ("a total of the published values" if total else "a published level, averaged by month")
        f["inference"] = _ni.official_inference(
            pubr, hide, why_total, words,
            {"change_pct": _num(f.get("value")), "prior": _num(prior), "latest": _num(latest)},
            None if (isinstance(rep.get("estimand"), dict) and isinstance(rep["estimand"].get("period"), dict) and
                     int(rep["estimand"]["period"].get("step") or 1) != 1) else (f.get("test") or {}).get("n_months"),
            "sum" if total else "mean", quality, revisions,
            (rep.get("estimand") or {}).get("period") if isinstance(rep.get("estimand"), dict) else None)
        if est_rec is not None and est_rec["inference"] is None and f["inference"]:
            # the table's headline IS this claim: its record goes where the writer reads what the headline is
            # (results_for_ai sends the estimand first), a copy, so the two never share a list
            est_rec["inference"] = json.loads(json.dumps(f["inference"]))


# ----------------------------------------------------------------------------- wave 5f: dates, totals and partial months of a plain file
# Three things the adapter does to a plain (business) file BEFORE it is landed, none of them to a publisher's table (whose structure the
# layer reads, nl_structure) and none to a file the layer reads:
#   1. DATES WRITTEN IN A FORMAT THE CORE DOES NOT READ (31.12.2019, 12/31/2019, "Jan 2019", "2019 Jan", 2019M01, 20190131) are rewritten
#      as ISO dates (H). A day/month pair that could be read either way round (03/04/2019) is settled by the column's other rows (a first
#      or a second field above 12) or the file is refused with a plain reason: a guess would move every month.
#   2. TOTAL ROWS (T): a member NOMINATED by a total word in a category column (Total, All, Grand total, overall ...) is a total only if the
#      cells say so: its rows equal the sum of the other members' over the same other-dimension cells (exact, or within the file's own
#      rounding: nl_structure._sum_check, a check that could have failed). A verified total is left out of the figures (the file adds up
#      to the same figures without it); a name nobody can check is left out too when it is a bare total phrase, and said so; a name that
#      merely holds a total word (All Saints Church, Total Wine) is a member until the cells say otherwise.
#   3. A PARTIAL MONTH (D): a file with several dates a month ends in the middle of a month, and "12 months" would compare 26 days of the
#      last month with a whole month a year before: the rows of the last (or, when it enters the comparison, the first) month are left
#      out, and said so.
TIDY_ON = True
TIDY_MIN_ROWS = 8
TIDY_MAX_MEMBERS = 400            # a category column with more members is an id column, not a dimension a total can be a member of
TIDY_KEY_MAX = 60                 # the other-dimension cells of a check: columns with more members than this are not part of the cell's key
_DATE_HEADER = re.compile(r"(?i)(?:date|day|period|time|month|week|year|datum|fecha|jour|periode|periodo|mes|monat|dt)")
_MONTH_NAMES = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO_LIKE = re.compile(r"^\s*\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?(?:[ T].*)?$|^\s*\d{4}-?[Qq][1-4]\s*$|^\s*\d{4}\s*$")
_DATE_FORMATS = (
    # (label, regex that the text must match, strptime format, resolution: day / month, day-first-or-month-first group)
    ("day.month.year", r"^\d{1,2}\.\d{1,2}\.\d{4}$", "%d.%m.%Y", "day"),
    ("day.month.yy", r"^\d{1,2}\.\d{1,2}\.\d{2}$", "%d.%m.%y", "day"),
    ("day-month-year", r"^\d{1,2}-\d{1,2}-\d{4}$", "%d-%m-%Y", "day"),
    ("day-Mon-year", r"^\d{1,2}-%s\.?-\d{4}$" % _MONTH_NAMES, "%d-%b-%Y", "day"),
    ("day-Mon-yy", r"^\d{1,2}-%s\.?-\d{2}$" % _MONTH_NAMES, "%d-%b-%y", "day"),
    ("month/year", r"^\d{1,2}/\d{4}$", "%m/%Y", "month"),
    ("month-first slash", r"^\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$", "%m/%d/%Y", "slash"),
    ("month-first slash yy", r"^\d{1,2}/\d{1,2}/\d{2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$", "%m/%d/%y", "slash"),
    ("month day, year", r"^%s\.?\s+\d{1,2},?\s+\d{4}$" % _MONTH_NAMES, "%b %d %Y", "day"),
    ("day month year", r"^\d{1,2}\s+%s\.?,?\s+\d{4}$" % _MONTH_NAMES, "%d %b %Y", "day"),
    ("month year", r"^%s\.?[ -]\d{4}$" % _MONTH_NAMES, "%b %Y", "month"),
    ("year month", r"^\d{4}[ -]%s\.?$" % _MONTH_NAMES, "%Y %b", "month"),
    ("year M month", r"^\d{4}\s?M\d{2}$", "%YM%m", "month"),
    ("year month digits", r"^\d{4}(?:0[1-9]|1[0-2])$", "%Y%m", "month_digits"),
    ("year month day digits", r"^\d{4}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$", "%Y%m%d", "day_digits"),
)


def _month_fix(t: str) -> str:
    """'Sept 2019' -> 'Sep 2019'; a full month name -> its three letters (strptime reads %b)."""
    t = re.sub(r"(?i)\b(sep)t(?:ember)?\b", r"\1", t)
    m = re.match(r"(?i)^(.*?)\b(%s)\b(.*)$" % _MONTH_NAMES, t)
    if m:
        t = m.group(1) + m.group(2)[:3].capitalize() + m.group(3)
    return t.replace(".", "") if re.search(r"[A-Za-z]\.", t) else t


def _read_plain(data: bytes) -> Optional[Any]:
    import pandas as pd
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception:  # noqa: BLE001 - a file pandas cannot read is left to the engine's own reader
        return None
    if df.columns.has_duplicates or len(df.columns) < 2:
        return None
    return df


def _to_csv_bytes(df: Any) -> bytes:
    return df.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _dates_in_place(df: Any) -> List[Dict[str, Any]]:
    """Rewrite, in the frame, every column of dates written in a format the core does not read as ISO dates (H); [{column, format, rows}]. A column is
    rewritten only when at least 95% of its filled cells fit ONE format (never a text column that merely holds a date now and then). 03/04/2019
    could be 3 April or 4 March: the column's other rows settle it (a first field above 12 is a day, a second above 12 is a day), else the file is
    refused with a plain reason."""
    import pandas as pd
    notes: List[Dict[str, Any]] = []
    for c in list(df.columns):
        s = df[c].astype(str).str.strip()
        filled = s[s != ""]
        if len(filled) < 3:
            continue
        sample = filled.drop_duplicates()
        if len(sample) > 3000:
            sample = sample.sample(3000, random_state=0)
        if float(sample.map(lambda v: bool(_ISO_LIKE.match(v))).mean()) >= 0.95:
            continue                                           # the core reads these
        picks = []
        for label, rx, fmt, res in _DATE_FORMATS:
            if res in ("month_digits", "day_digits") and not _DATE_HEADER.search(str(c)):
                continue                                       # six or eight digits are a date only under a heading that says so
            if float(sample.map(lambda v, rx=rx: bool(re.match(rx, _month_fix(v), re.I) or re.match(rx, v, re.I))).mean()) >= 0.95:
                picks.append((label, rx, fmt, res))
        if not picks:
            continue
        label, rx, fmt, res = picks[0]
        if res == "slash":
            first = filled.str.extract(r"^(\d{1,2})/(\d{1,2})/")
            f, g = pd.to_numeric(first[0], errors="coerce"), pd.to_numeric(first[1], errors="coerce")
            if bool((f > 12).any()) and bool((g > 12).any()):
                continue                                       # both fields exceed 12 somewhere: not a date column at all
            if bool((f > 12).any()):
                fmt, label = fmt.replace("%m/%d", "%d/%m"), "day/month/year"
            elif bool((g > 12).any()):
                label = "month/day/year"
            else:
                raise Refusal("The dates in the column %s are written like %s, which could be day/month/year or month/day/year, and no "
                              "row settles it (no first or second number is above 12). Write them as 2019-04-03 (year-month-day), or "
                              "tell me which it is, and try again. Nothing was read." % (c, str(filled.iloc[0])[:20]))
        txt = filled.map(_month_fix) if res in ("month", "day") and "%b" in fmt else filled
        if "," in fmt or label == "month day, year":
            txt = txt.str.replace(",", "", regex=False)
        if "%H" not in fmt and re.search(r"\d:\d{2}", str(filled.iloc[0])):
            txt = txt.str.replace(r"\s+\d{1,2}:\d{2}(?::\d{2})?$", "", regex=True)
        try:
            parsed = pd.to_datetime(txt.str.replace(r"\s+", " ", regex=True), format=fmt, errors="coerce")
        except Exception:  # noqa: BLE001 - a format pandas refuses is not this column's
            continue
        if float(parsed.notna().mean()) < 0.95:
            continue
        iso = parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), None)
        out = df[c].copy()
        out.loc[filled.index] = [v if v is not None else df.at[i, c] for i, v in zip(filled.index, iso)]
        if not (out != df[c]).any():
            continue
        df[c] = out
        notes.append({"column": str(c), "format": label, "rows": int(parsed.notna().sum())})
    return notes


# numbers written with a space (or a non-breaking space, a narrow one, an apostrophe) between the thousands, with a decimal comma or point
# ("708 219,6", "1 234", "1'234.5"), or with a point between the thousands and a comma for the decimals ("1.234,5"): the core reads none of them
# as numbers. A decimal comma alone ("123,4") is read by the core and is left to it; "1,234" and "1.234" alone are ambiguous and are left alone.
_NUM_SPACED = re.compile(r"^[-+]?\d{1,3}(?:[ \u00a0\u202f\u2009']\d{3})+(?:[.,]\d+)?$")
_NUM_DOTTED = re.compile(r"^[-+]?\d{1,3}(?:\.\d{3})+,\d+$")
_NUM_PLAIN = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")


def _numbers_in_place(df: Any) -> List[Dict[str, Any]]:
    """Rewrite, in the frame, every column of numbers written with thousands separators the core does not read as plain numbers (see _NUM_SPACED):
    at least 95% of the filled cells are numbers in one of these forms and at least 5% are written with a separator. [{column, rows}]."""
    notes: List[Dict[str, Any]] = []
    for c in list(df.columns):
        s = df[c].astype(str).str.strip()
        filled = s[s != ""]
        if len(filled) < 6:
            continue
        sample = filled.drop_duplicates()
        if len(sample) > 3000:
            sample = sample.sample(3000, random_state=0)
        spaced = sample.map(lambda v: bool(_NUM_SPACED.match(v)))
        dotted = sample.map(lambda v: bool(_NUM_DOTTED.match(v)))
        plain = sample.map(lambda v: bool(_NUM_PLAIN.match(v)))
        if float((spaced | dotted | plain).mean()) < 0.95 or float((spaced | dotted).mean()) < 0.05:
            continue
        dot_dec = sample.map(lambda v: bool(_NUM_SPACED.match(v)) and bool(re.search(r"\.\d+$", v)))
        com_dec = sample.map(lambda v: bool(re.search(r",\d+$", v)) and (bool(_NUM_SPACED.match(v)) or bool(_NUM_DOTTED.match(v))))
        if bool(dot_dec.any()) and bool(com_dec.any()):
            continue                                           # point and comma both as the decimal mark in one column: not read

        def conv(v: str) -> str:
            t = v.strip()
            if _NUM_DOTTED.match(t):
                return t.replace(".", "").replace(",", ".")
            if _NUM_SPACED.match(t):
                t = re.sub(r"[ \u00a0\u202f\u2009']", "", t)
                return t.replace(",", ".")
            if _NUM_PLAIN.match(t) and "," in t and "." not in t:
                return t.replace(",", ".")                     # the rest of the column is in the same decimal-comma style
            return v
        out = df[c].map(lambda v: conv(str(v)) if str(v).strip() != "" else v)
        if (out != df[c]).any():
            df[c] = out
            notes.append({"column": str(c), "rows": int(len(filled))})
    return notes


def normalize_file(data: bytes) -> Tuple[bytes, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(the file, date notes, number notes): dates and numbers written in a format the core does not read, rewritten as ISO dates and plain numbers
    before anything reads the file (H, E). The same bytes when no column needs it (a head of the file is looked at first, so an ordinary file is
    parsed once)."""
    import pandas as pd
    try:
        head = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig", nrows=3000)
    except Exception:  # noqa: BLE001 - a file pandas cannot read is left to the engine's own reader
        return data, [], []
    if head.columns.has_duplicates or len(head.columns) < 2 or len(head) < 3:
        return data, [], []
    try:
        applies = bool(_dates_in_place(head.copy())) or bool(_numbers_in_place(head.copy()))
    except Refusal:
        applies = True                                         # the head is ambiguous: the whole column may settle it, or refuse
    if not applies:
        return data, [], []
    df = _read_plain(data)
    if df is None:
        return data, [], []
    dn = _dates_in_place(df)
    nn = _numbers_in_place(df)
    return (_to_csv_bytes(df), dn, nn) if (dn or nn) else (data, [], [])


def normalize_dates(data: bytes) -> Tuple[bytes, List[Dict[str, Any]]]:
    """The dates part of normalize_file (kept for callers and tests)."""
    out, dn, _nn = normalize_file(data)
    return out, dn


_TOTAL_GENERIC = frozenset((
    "branches", "branch", "stores", "store", "shops", "shop", "regions", "region", "locations", "location", "products", "product", "items",
    "item", "categories", "category", "customers", "channels", "departments", "department", "sites", "site", "areas", "area", "provinces",
    "states", "industries", "sectors", "groups", "segments", "offices", "countries", "cities", "brands", "services", "types", "ages",
    "sources", "outlets", "markets", "territories", "clients", "accounts", "sales", "periods", "months", "years", "classes", "lines"))


def _total_nomination(label: Any) -> Optional[str]:
    """How a member's NAME nominates it as the total of the others: "exact" (a bare total phrase: Total, Grand total, All, All regions, Total, all
    industries, Overall, Ensemble, Insgesamt ...) or "loose" (a total word inside a longer name: All Saints Church, Total Wine), else None. The
    rest of something ("All other branches") and an alternative total ("Total excluding X") are never nominated: they are what they say."""
    NS = _ns()
    if NS._is_rest(label) or NS._is_alt(label) or not NS._says_total(label):
        return None
    t = NS._fold_name(NS._outside_brackets(label))
    toks = [x for x in t.split() if x]
    drop = {"the", "of", "de", "des", "du", "la", "le", "les", "und", "and", "all", "tous", "toutes", "alle", "todos", "todas",
            "for", "across", "over", "pour", "para", "fur", "in"}
    core = [x for x in toks if x not in drop]
    head = {"total", "totals", "totale", "totaal", "grand", "overall", "aggregate", "combined", "sum", "ensemble", "insgesamt", "gesamt",
            "gesamtsumme", "general", "generale", "subtotal", "everything"}
    if toks and (set(core) <= head | _TOTAL_GENERIC and (not core or core[0] in head or toks[0] in ("all", "tous", "toutes", "alle", "todos", "todas"))):
        return "exact"
    # "Company total", "Branch total", "Chain total": a name that ENDS in a total word is a total (nobody calls a branch that); a name that
    # merely starts with one (Total Wine, Total Fitness) or holds "all" (All Saints Church) stays loose
    if 2 <= len(toks) <= 4 and toks[-1] in ("total", "totals", "totale", "totaal", "gesamt", "insgesamt", "overall", "combined"):
        return "exact"
    return "loose"


def _decimals_of(series: Any) -> int:
    """The most decimals any filled cell of a text column of numbers writes (at most 4)."""
    d = 0
    for v in series.drop_duplicates().head(2000):
        m = re.match(r"^[-+]?\d*\.(\d+)", str(v).strip().replace(",", ""))
        if m:
            d = max(d, len(m.group(1)))
    return min(d, 4)


ID_MIN_DIGITS = 9               # wave 5g (D): a column of whole numbers all of ONE width of at least 9 digits is a column of identifiers, not amounts


def _identifier_column(name: Any, values: Any, all_names: Iterable[Any]) -> bool:
    """Whether a number column is an identifier (a case number, an invoice number, an account), not an amount that adds up: by SHAPE, whole numbers
    that all have the same width of nine or more digits (no amount is written so evenly: sizes spread over several widths), or by the core's own
    reading of a column that is not a measure (a name ending in id, key, number, code ... with whole numbers, a map coordinate, a year; or whole
    numbers that are nearly all different), so that the checks of a total read the columns the core will sum and no others. Wave 5g, D (fuzz v2
    seed 832: a 12-digit case number vetoed a Revenue total that matched to the unit)."""
    import pandas as pd
    v = pd.to_numeric(values, errors="coerce").dropna()
    if len(v) < 4 or not bool((v == v.round()).all()):
        return False
    digits = v.abs().astype("int64").astype(str).str.len() if float(v.abs().max()) < 9e18 else None
    if digits is not None and int(digits.min()) >= ID_MIN_DIGITS and int(digits.nunique()) == 1:
        return True
    try:
        from northledger import measure as _m
        s = v.astype("int64") if float(v.abs().max()) < 9e18 else v
        if _m._label_number(str(name), s, [str(x) for x in all_names]) or _m._id_like(str(name), s, {}):
            return True
    except Exception:  # noqa: BLE001 - the shape rule above still stands without the core
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
    return False


def _number_columns(df: Any, exclude: Set[str]) -> Dict[str, Any]:
    import pandas as pd
    out = {}
    for c in df.columns:
        if c in exclude:
            continue
        t = df[c].astype(str).str.strip()
        f = t[t != ""]
        if len(f) < 4:
            continue
        num = pd.to_numeric(f.str.replace(",", "", regex=False), errors="coerce")
        if float(num.notna().mean()) >= 0.95 and int(num.nunique()) >= 2:
            out[c] = pd.to_numeric(t.str.replace(",", "", regex=False), errors="coerce")
    return out


def _date_values(df: Any, exclude: Set[str]) -> Tuple[Optional[str], Optional[Any]]:
    """(the date column, its dates as datetimes): the column whose cells are ISO dates with the most different dates."""
    import pandas as pd
    best, best_n, best_s = None, 0, None
    for c in df.columns:
        if c in exclude:
            continue
        t = df[c].astype(str).str.strip()
        f = t[t != ""]
        if len(f) < 4 or float(f.map(lambda v: bool(re.match(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?(?:[ T].*)?$", v))).mean()) < 0.95:
            continue
        # 2022-03-05, 2022-3-5, 2022/03/05 and 2022-03 (the first of the month): the core reads them all
        dd = t.str.extract(r"^\s*(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?")
        d = pd.to_datetime(pd.DataFrame({"year": pd.to_numeric(dd[0], errors="coerce"), "month": pd.to_numeric(dd[1], errors="coerce"),
                                         "day": pd.to_numeric(dd[2], errors="coerce").fillna(1)}), errors="coerce")
        n = int(d.dropna().nunique())
        if n > best_n:
            best, best_n, best_s = c, n, d
    return best, best_s


def ledger_tidy(data: bytes, keep: Optional[Set[str]] = None, S_probe: Any = None) -> Optional[Dict[str, Any]]:
    """What to do to a plain file before it is landed (T and D, above), or None when there is nothing to do. {drops: the rows to leave out (their
    index in the file), totals: [{column, member, rows, status, why}], partial: {...} | None}. Never raises on a file it cannot read."""
    if not TIDY_ON:
        return None
    import numpy as np
    import pandas as pd
    NS = _ns()
    df = _read_plain(data)
    if df is None or len(df) < TIDY_MIN_ROWS:
        return None
    if _publisher_header(df.columns) is not None or len([c for c in df.columns if _pnorm(c) in _PANEL_META]) >= 3:
        return None                                            # a publisher's table: its structure is read by nl_structure
    kept = set(keep or ())
    aside = {c for c in _raw_personal_columns(df, list(df.columns)) if c not in kept and _engine_slug(c) not in kept}
    date_col, dts = _date_values(df, aside)
    if date_col is None:
        return None
    nums = _number_columns(df, aside | {date_col})
    nums = {c: v for c, v in nums.items() if not _identifier_column(c, v, df.columns)}       # wave 5g (D): amounts only
    if not nums:
        return None
    # the category columns: text, at most TIDY_MAX_MEMBERS different members, not personal, not a number
    cats: Dict[str, Any] = {}
    for c in df.columns:
        if c in aside or c == date_col or c in nums:
            continue
        t = df[c].astype(str).str.strip()
        nun = int(t[t != ""].nunique())
        if 2 <= nun <= TIDY_MAX_MEMBERS:
            cats[c] = t
    drops: Set[int] = set()
    reasons: Dict[int, str] = {}
    totals: List[Dict[str, Any]] = []
    valid_date = dts.notna().to_numpy()
    date_key = dts.dt.strftime("%Y-%m-%d").fillna("")
    checks: List[Tuple[Any, ...]] = []
    for c, col in cats.items():
        members = [m for m in pd.unique(col[col != ""])]
        nominated = {m: _total_nomination(m) for m in members}
        nominated = {m: k for m, k in nominated.items() if k}
        if not nominated or len(members) - len(nominated) < 1:
            continue
        others_cols = [o for o in cats if o != c and int(cats[o][cats[o] != ""].nunique()) <= TIDY_KEY_MAX]
        ctx = pd.Series("", index=df.index)
        for o in others_cols:
            ctx = ctx + "\x1f" + cats[o]
        base = [m for m in members if m not in nominated]
        for m, kind in nominated.items():
            checks.append((c, col, m, kind, _total_columns(NS, df, col, m, base, nums, ctx, date_key, valid_date)))
    for c, col, m, kind, res in checks:
        status, why = _total_verdict(res)
        rows = np.flatnonzero((col == m).to_numpy())
        rec = {"column": str(c), "member": str(m), "rows": int(len(rows)), "nomination": kind, "status": status, "why": why}
        # a verified total is left out; a bare total phrase that no cell could verify, or that stands above the sum of the others (a total whose
        # parts are not all listed), is left out too and said so; a name that merely holds a total word is a member until the cells say otherwise
        if status == "verified" or (status in ("unresolved", "contradicted_bounding") and kind == "exact"):
            drops.update(int(i) for i in rows)
            why_row = ("left out of the figures: a row named %r that equals the sum of the other rows (a total)" % str(m)) \
                if status == "verified" else \
                ("left out of the figures: a row named %r that could not be checked against the other rows (treated as a total)" % str(m))
            for i in rows:
                reasons[int(i)] = why_row
            rec["left_out"] = True
        else:
            rec["left_out"] = False
        totals.append(rec)
    partial = _partial_months(df, dts, drops)
    if not drops and not partial and not [t for t in totals if t["nomination"] == "exact"]:
        return None
    if partial:
        drops.update(partial["rows"])
        month_of = dts.dt.strftime("%Y-%m")
        for i in partial["rows"]:
            reasons.setdefault(int(i), "left out of the comparison: the file stops in the middle of %s (or starts in it), so the months compared are whole"
                               % _mon(str(month_of.iloc[i])))
    return {"drops": sorted(drops), "totals": totals, "partial": partial, "date_column": date_col, "reasons": reasons}


def _total_columns(NS: Any, df: Any, col: Any, m: str, base: List[str], nums: Dict[str, Any], ctx: Any, date_key: Any,
                   valid_date: Any) -> Any:
    """The sum-check of member m of a category column against the `base` members over the same cells (the same date and the same members of
    every other dimension), for EVERY number column: {column: (nl_structure._sum_check's result, the cells)}, or (status, why) when no check can
    be made (too few rows, too many cells)."""
    import numpy as np
    import pandas as pd
    mem_list = base + [m]
    use = valid_date & col.isin(mem_list).to_numpy()
    if int(use.sum()) < TIDY_MIN_ROWS:
        return "unresolved", "too few rows"
    ci, cvals = pd.factorize(ctx[use])
    ti, tvals = pd.factorize(date_key[use])
    mi = np.array([mem_list.index(x) for x in col[use].to_numpy()])
    nm, nc, nt = len(mem_list), len(cvals), len(tvals)
    if nm * nc * nt > 4_000_000:
        return "unresolved", "too many cells"
    X = np.zeros((nm, nc, nt), dtype=bool)
    X[mi, ci, ti] = True
    results: Dict[str, Any] = {}
    for name, series in nums.items():
        v = series[use].to_numpy(dtype=float)
        ok = ~np.isnan(v)
        g = pd.DataFrame({"m": mi[ok], "c": ci[ok], "t": ti[ok], "v": v[ok]}).groupby(["m", "c", "t"], sort=False)["v"].sum()
        A = np.full((nm, nc, nt), np.nan)
        idx = g.index.to_frame(index=False).to_numpy()
        A[idx[:, 0], idx[:, 1], idx[:, 2]] = g.to_numpy()
        # half a unit of the last digit written, or of the zeros the figures end in (4731000: rounded to the thousand; wave 5g)
        tol = max(0.5 * 10.0 ** (-_decimals_of(df[name])), 0.5 * NS._round_unit(v[ok]) if _decimals_of(df[name]) == 0 else 0.0)
        nonneg = bool(np.nanmin(A) >= 0) if np.isfinite(A).any() else True
        results[name] = (NS._sum_check(A, X, nm - 1, list(range(nm - 1)), tol, nonneg), A)
    return results


def _total_verdict(res: Any) -> Tuple[str, str]:
    """(status, why) from a member's per-column sum-checks: "verified" (a check that could have FAILED passed for at least one number column and
    failed for none), "contradicted_bounding" (it is above their sum in the cells: a total whose parts are not all listed), "contradicted" (it
    is not their sum and is not above it: an ordinary member) or "unresolved" (no check could have failed)."""
    import numpy as np
    if not isinstance(res, dict):
        return res
    results = list(res.values())
    if any(c["status"] == "fail" for c, _a in results):
        bound = True
        for c, A in results:
            if c["status"] != "fail":
                continue
            rest, tot = A[:-1], A[-1]
            both = ~np.isnan(tot) & (~np.isnan(rest)).all(axis=0)
            bound = bound and bool(both.sum() >= 3 and float((tot[both] >= np.nansum(rest, axis=0)[both] - 1e-9).mean()) >= 0.95)
        return ("contradicted_bounding" if bound else "contradicted"), "it is not the sum of the other members"
    if any(c["status"] == "pass" for c, _a in results):
        return "verified", "it equals the sum of the other members in every cell that can be checked"
    return "unresolved", "no check on these figures could have failed"


def _check_total(NS: Any, df: Any, col: Any, m: str, base: List[str], nums: Dict[str, Any], ctx: Any, date_key: Any,
                 valid_date: Any) -> Tuple[str, str]:
    """Whether member m of a category column is the total of the `base` members over the same cells, for every number column (see
    `_total_columns` and `_total_verdict`): "verified", "contradicted_bounding", "contradicted" or "unresolved"."""
    return _total_verdict(_total_columns(NS, df, col, m, base, nums, ctx, date_key, valid_date))


def _partial_months(df: Any, dts: Any, already: Set[int]) -> Optional[Dict[str, Any]]:
    """The rows of a last month the file stops in the middle of (and of a first month, when it enters the comparison), for a file with several
    dates a month at a regular rhythm (weekly, daily, or weekdays only). None for a monthly file, an irregular one, or a file that ends on the
    last expected date of its month."""
    import numpy as np
    import pandas as pd
    ok = dts.notna().to_numpy()
    keep_rows = np.array([i not in already for i in range(len(df))]) & ok
    d = dts[keep_rows]
    if len(d) < 8:
        return None
    ds = sorted({x.strftime("%Y-%m-%d") for x in d})
    cad = _ns()._cadence(ds)
    if cad is None:
        return None
    # a REGULAR table: the same number of rows on most dates (one row a day for each series). A log of transactions on random dates has a date
    # with no row now and then, and a last date one day before a month end proves nothing about the month: the core's own rule (a last month under
    # half a typical one is left out) is all that is said of it
    per_date = d.dt.strftime("%Y-%m-%d").value_counts()
    if float((per_date == per_date.mode().iloc[0]).mean()) < 0.6:
        return None
    months = sorted({x[:7] for x in ds})
    if len(months) < 24:
        return None                       # two 12-month windows need 24 months: a shorter file is not compared, whole months or not
    days = [pd.Timestamp(x) for x in ds]
    wd_share = np.bincount([x.weekday() for x in days], minlength=7) / float(len(days))
    present = {w for w in range(7) if wd_share[w] >= 0.05}
    # ... and a COMPLETE one: it holds (nearly) every date its rhythm expects between its first and its last
    span = [days[0] + pd.Timedelta(days=k) for k in range((days[-1] - days[0]).days + 1)]
    expected_n = sum(1 for x in span if x.weekday() in present and (cad["cadence"] != "week" or x.weekday() == days[0].weekday()))
    if expected_n == 0 or len(days) < 0.95 * expected_n:
        return None

    def expected_after(ts: Any) -> bool:
        """Whether a date the file's own rhythm would hold falls after `ts` in its month."""
        end = ts + pd.offsets.MonthEnd(0)
        cur = ts + pd.Timedelta(days=1)
        while cur <= end:
            if cur.weekday() in present and (cad["cadence"] != "week" or cur.weekday() == ts.weekday()):
                return True
            cur += pd.Timedelta(days=1)
        return False

    def expected_before(ts: Any) -> bool:
        start = ts.replace(day=1)
        cur = ts - pd.Timedelta(days=1)
        while cur >= start:
            if cur.weekday() in present and (cad["cadence"] != "week" or cur.weekday() == ts.weekday()):
                return True
            cur -= pd.Timedelta(days=1)
        return False
    last_ts = max(days)
    first_ts = min(days)
    # a file whose months never reach a later (or an earlier) day of the month than this one has its own month end (a log that stops on the 28th
    # in every month): the last date is then the month's last, and the first its first
    max_day: Dict[str, int] = {}
    min_day: Dict[str, int] = {}
    for x in days:
        k = x.strftime("%Y-%m")
        max_day[k] = max(max_day.get(k, 0), x.day)
        min_day[k] = min(min_day.get(k, 99), x.day)
    last_m, first_m = last_ts.strftime("%Y-%m"), first_ts.strftime("%Y-%m")
    out_months: List[str] = []
    if expected_after(last_ts) and any(v > last_ts.day for k, v in max_day.items() if k != last_m):
        out_months.append(last_m)
    left = [m for m in months if m not in out_months]
    # the first month, when the comparison (the latest 24 whole months) reaches it
    if expected_before(first_ts) and len(left) <= 24 and any(v < first_ts.day for k, v in min_day.items() if k != first_m):
        out_months.append(first_m)
    if not out_months:
        return None
    month_of = dts.dt.strftime("%Y-%m").fillna("")
    rows = [int(i) for i in np.flatnonzero(month_of.isin(out_months).to_numpy()) if i not in already]
    if not rows:
        return None
    counts = {m: int(((month_of == m) & pd.Series(keep_rows, index=df.index)).sum()) for m in out_months}
    return {"months": out_months, "rows": rows, "rows_by_month": counts, "last_date": last_ts.strftime("%Y-%m-%d"),
            "first_date": first_ts.strftime("%Y-%m-%d"), "cadence": cad["cadence"]}


def run(csv_bytes: Any, name: str, objective: str = "", decisions: Optional[Dict[str, Any]] = None,
        as_of: Optional[str] = None) -> Dict[str, Any]:
    """The engine's end-to-end path on one file, as THE REPORT CONTRACT. Never raises.

    decisions: {column: "withhold" | "code" | "keep"} for flagged columns, by the landed
        name (as privacy.flagged lists it) or the file's own header. Default: withhold.
    as_of: YYYY-MM-DD analysis date. Leave it out for a visitor's file (today is used);
        the sample passes its fixed date so its report does not age.
    """
    t_start = time.perf_counter()
    try:
        data = _as_bytes(csv_bytes)
    except Refusal as exc:
        rep = blank_report(os.path.basename(str(name or "")))
        rep["error"] = str(exc)
        rep["timings"] = [{"stage": s, "seconds": 0.0} for s in STAGES]
        return rep
    rep = blank_report(os.path.basename(str(name or "")), data)
    timings: Dict[str, float] = {s: 0.0 for s in STAGES}
    tmp = None
    try:
        name = _clean_name(name)
        rep["input"]["name"] = name
        if not data or not data.strip():
            raise Refusal("That file is empty.")
        if len(data) > MAX_BYTES:
            raise Refusal("That file is larger than the 25 MB this in-browser demo reads. Nothing "
                          "was sampled or cut: send a smaller extract, or email me about the full "
                          "audit.")
        if as_of is not None:
            try:
                as_of = _dt.date.fromisoformat(str(as_of)[:10]).isoformat()
            except ValueError:
                raise Refusal("The analysis date must be written YYYY-MM-DD.")
        if data.count(b"\n") > MAX_ROWS and _count_rows(data) > MAX_ROWS:
            raise Refusal("That file has more than 200,000 rows, the most this in-browser demo "
                          "reads. Nothing was sampled or cut: send a smaller extract, or email me "
                          "about the full audit.")
        objective = " ".join(str(objective or "").split())[:300] or DEFAULT_OBJECTIVE
        if _looks_like_title(data):
            raise Refusal("The first row of this file looks like a title, not column names: it has "
                          "one or two cells and the rows below it have many. Delete the rows above "
                          "the column names (and any blank row under them), save as CSV and try "
                          "again. Nothing was read.")
        # wave 5f (H): dates written in a format the core does not read are rewritten as ISO dates before anything reads the file
        data, date_notes, number_notes = normalize_file(data)
        tidy_info: Optional[Dict[str, Any]] = None

        layout = None
        ai_plan = None
        goal_from_plan = False
        plan_review = None
        structure_inner = None                    # set only in the slice's own run (_run_slice)
        if isinstance(decisions, dict) and "__structure_inner__" in decisions:
            decisions = dict(decisions)
            structure_inner = decisions.pop("__structure_inner__")
        # a WIDE table of periods (2019Q1, 2020Q1 ... as columns; Eurostat's shape) is read as one row per series and period
        wide_info = None
        # wave 5b, fail closed: the structure layer could not run on a file that looks like a table of series with totals
        # (nl_structure cannot be imported, detect throws, the slice cannot be run): a refusal, never the old reading
        # (struct_error is the `structure` record; cube_refusal below carries the reason to the "did not run" path)
        struct_error: Optional[Dict[str, Any]] = None
        if structure_inner is None and STRUCTURE_ON:
            f_imp = _structure_import_failure()
            if f_imp is not None:
                struct_error = _guard_failure(data, f_imp)       # None: not a table of series, read as before (strict: raises)
            else:
                try:
                    w_ = _ns().wide_to_long(data, MAX_ROWS)
                except Exception as exc_w:  # noqa: BLE001 - the file is then read as it stands (a table of series: refused)
                    struct_error = _guard_failure(data, _failure("wide", exc_w))
                    w_ = None
                if w_ is not None:
                    data, wide_info = w_["csv"], w_["info"]
        if isinstance(decisions, dict) and "__plan_review__" in decisions:
            decisions = dict(decisions)
            plan_review = _clean_plan_review(decisions.pop("__plan_review__"))
        contracts_off: List[str] = []
        if isinstance(decisions, dict) and "__contracts_off__" in decisions:
            decisions = dict(decisions)
            raw_off = decisions.pop("__contracts_off__")
            contracts_off = [str(x) for x in raw_off][:200] if isinstance(raw_off, list) else []
        # the data tests (phase one): what they found, their cell masks, and the table they ran on, which is
        # the file the engine reads, unchanged (a test never changes what the engine reads)
        ctests: Optional[List[Dict[str, Any]]] = None
        caux: Dict[str, Any] = {}
        cdf = None
        sent = data                               # the file as the visitor sent it, for its line numbers
        sent_rows = None                          # (each planned row's place among the visitor's rows, their count)
        raw_context: Any = None                   # the plan's web searches: items of list terms (_context_queries)
        plan_drops: List[Dict[str, Any]] = []     # the plan's steps that set rows aside (_row_drops)
        # the structure of a statistical table, the fast path: the structure the scan's run or the planner's profile
        # found under these same choices (a plan's run that finds none runs the profile pass once, which caches it)
        S_pre = None
        cube_refusal = ""
        has_plan = isinstance(decisions, dict) and isinstance(decisions.get("__plan__"), dict)
        vis_dec = {k: v for k, v in dict(decisions or {}).items() if not str(k).startswith("__")}
        if structure_inner is None and STRUCTURE_ON and struct_error is None:
            sha_sent = hashlib.sha256(data).hexdigest()
            S_pre = _structure_cached(sha_sent, vis_dec)
            struct_error = _structure_cached_error(sha_sent, vis_dec)
            if S_pre is None and struct_error is None and has_plan:
                got = _engine_profile_pass(data, name, vis_dec, as_of, structure=True, wide=wide_info)
                if got.get("ok"):
                    _PROFILE_CACHE.clear()
                    _PROFILE_CACHE.update(sha=sha_sent, value=got)
                    S_pre = got.get(_PROFILE_CACHE_STRUCTURE)
                    struct_error = got.get(_PROFILE_CACHE_ERROR)
                else:
                    struct_error = got.get(_PROFILE_CACHE_ERROR)        # the pass itself failed (wave 5e, P7): a table of series is refused
            if S_pre is not None and struct_error is None and _refusable_verdict(S_pre):
                struct_error = _verdict_failure(sent, S_pre)
            if S_pre is not None and S_pre.get("kind") == "cube_incomplete":
                cube_refusal = S_pre.get("reason") or "the table's rows cannot be told apart"
            if S_pre is not None and S_pre.get("usable") and not has_plan:
                outer = _PROFILE_CACHE.get("value") or {}
                inner = _run_slice(S_pre, dict(S_pre["default"]), "S1", "engine_default", [], name=name,
                                   objective=objective, as_of=as_of, ai_plan=None, raw_context=None,
                                   outer=_outer_of(outer, rep, data), timings={})
                if inner is not None:
                    if date_notes or number_notes:
                        _tidy_notes(inner, None, date_notes, number_notes)
                    return inner
                struct_error = _slice_failure(data)                 # a table of series whose slice could not be run (else: read as before)
        if struct_error is not None:
            cube_refusal = _reason_in_sentence(struct_error)
            rep["structure"] = struct_error
        if isinstance(decisions, dict) and isinstance(decisions.get("__plan__"), dict):
            decisions = dict(decisions)
            raw_plan = decisions.pop("__plan__")
            raw_context = raw_plan.get("context")    # never its old free-text context_queries
            try:
                import pandas as _pd
                cols = list(_pd.read_csv(io.BytesIO(data), dtype=str, nrows=0, encoding="utf-8-sig").columns)
                S_use = S_pre if (S_pre is not None and S_pre.get("usable")) else None
                if S_use is None and S_pre is not None and S_pre.get("kind") == "panel_no_relations" and S_pre.get("official"):
                    # wave 5g (A): the layer left this official panel to the long-table layout. If the layout can be made from the date and measure
                    # columns the LAYER found (so, in any language), the plan's own reshape may make it; if it cannot, the panel is read one member
                    # at a time, as without a plan
                    lay_p = None
                    if any(isinstance(o, dict) and o.get("op") == "long_to_wide" for o in raw_plan.get("operations") or []):
                        try:
                            _nb_p, lay_p = _reshape_long_panel(data, planned=True, date_col=(S_pre.get("date") or {}).get("column"),
                                                               value_col=(S_pre.get("measure") or {}).get("column"))
                        except Exception:  # noqa: BLE001 - no layout then
                            lay_p = None
                    if lay_p is None and _ns().read_one_member_panel(S_pre):
                        S_use = S_pre
                ai_plan, plan_refused = _validate_plan(raw_plan, cols, S_use)
                if S_use is not None:
                    # a table read by its structure: the plan's rows are checked against it (check_rows) and the slice
                    # runs; a plan that reshapes the table is not needed (the structure reads its series)
                    ops = [o for o in ai_plan.get("operations") or [] if o.get("op") != "long_to_wide"]
                    if len(ops) < len(ai_plan.get("operations") or []):
                        plan_refused.append("long_to_wide: the table is read by its structure, one series at a time")
                    _d2, applied_s, _lay = _apply_plan(data, dict(ai_plan, operations=ops))
                    ai_plan["applied"] = applied_s["applied"]
                    ai_plan["refused"] = plan_refused + applied_s["refused"]
                    ai_plan["row_drops"] = _row_drops(list(applied_s.get("drops") or []),
                                                      _private_of((_PROFILE_CACHE.get("value") or {})))
                    ai_plan["context_queries"], ai_plan["context_queries_dropped"], ai_plan["context"] = \
                        _context_queries(raw_context)
                    where, sid, psrc, corr = _structure_plan(S_use, ai_plan, applied_s.get("positions"),
                                                             bool(applied_s.get("drops")))
                    if psrc == "ai_corrected":
                        ai_plan["refused"].append(
                            "the plan's rows mix a total with its parts (%s), so the engine's default slice was used"
                            % "; ".join("%s: %s" % (v["dim"], v["kind"].replace("_", " ")) for v in corr[:3]))
                    outer = _PROFILE_CACHE.get("value") or {}
                    inner = _run_slice(S_use, where, sid, psrc, corr, name=name, objective=objective, as_of=as_of,
                                       ai_plan=ai_plan, raw_context=raw_context, outer=_outer_of(outer, rep, data),
                                       timings={})
                    if inner is not None:
                        if plan_review:
                            inner.setdefault("ai_plan", {})["review"] = plan_review
                        if date_notes or number_notes:
                            _tidy_notes(inner, None, date_notes, number_notes)
                        return inner
                    struct_error = _slice_failure(data)              # a table of series whose slice could not be run (else: read as before)
                    if struct_error is not None:
                        cube_refusal = _reason_in_sentence(struct_error)
                        rep["structure"] = struct_error
                data, applied, layout = _apply_plan(data, ai_plan)
                if layout is None and S_use is None and S_pre is not None and S_pre.get("kind") == "panel_no_relations":
                    # wave 5g (A): the profile left this table of unrelated series to the layout and the plan did not make one: the old path would
                    # average its members together, so it is refused (without a plan the same table is read one member at a time)
                    struct_error = _panel_failure(sent, S_pre)
                    if struct_error is not None:
                        cube_refusal = _reason_in_sentence(struct_error)
                        rep["structure"] = struct_error
                sent_rows = (applied.get("positions"), applied.get("rows_in"))
                ai_plan["applied"] = applied["applied"]
                plan_drops = list(applied.get("drops") or [])
                if ai_plan.get("primary") and layout is not None:
                    ai_plan["refused"] = list(ai_plan.get("refused") or [])
                    ai_plan["primary"] = ""          # the value column became one column per series: lead with a series
                ai_plan["refused"] = plan_refused + applied["refused"]
                try:
                    # the table the data tests read, as text: the file the engine reads, unchanged. The tests
                    # themselves run after the engine, on its own reading of these rows (a test never changes
                    # what the engine reads)
                    cdf = _pd.read_csv(io.BytesIO(data), dtype=str, encoding="utf-8-sig", keep_default_na=False)
                except Exception:  # noqa: BLE001 - the tests are an aid; the file runs as the plan left it
                    cdf = None
                for c, d in applied["decisions"].items():
                    decisions.setdefault(c, d)
                if ai_plan.get("goal") and objective == DEFAULT_OBJECTIVE:
                    objective = ai_plan["goal"]
                    goal_from_plan = True
            except Exception as exc:  # noqa: BLE001 - a plan that cannot run leaves the rule-based path
                ai_plan = {"refused": ["the plan could not run (%s); the rule-based reading was used" % type(exc).__name__]}
        # wave 5f (T, D): a plain file the structure layer does not read: the total rows it holds are left out of its figures when the cells say
        # they are totals (a bare total phrase nobody can check too, said so), and a month the file stops in the middle of is not compared
        if layout is None and structure_inner is None and STRUCTURE_ON and struct_error is None and not cube_refusal:
            try:
                kept_t = _kept_by_visitor(decisions)
                t_ = ledger_tidy(data, kept_t)
                if t_ is not None:
                    fail_t: Dict[str, Any] = {}
                    S_t = _quick_structure(data, kept_t, fail_t)
                    if S_t is not None and S_t.get("usable"):
                        t_ = None                 # the layer reads this file (a total row beside its parts): its estimand is the answer
                if t_ is not None:
                    df_t = _read_plain(data)
                    sent_rows_before = sent_rows
                    drop_t = set(t_["drops"])
                    pos_kept = [i for i in range(len(df_t)) if i not in drop_t]
                    if drop_t:
                        if cdf is not None and len(cdf) == len(df_t):
                            cdf = cdf.iloc[pos_kept].reset_index(drop=True)      # the table the data tests read follows the rows the engine reads
                        data = _to_csv_bytes(df_t.iloc[pos_kept])
                        if sent_rows is None:
                            sent_rows = (pos_kept, len(df_t))
                        elif sent_rows[0] is not None and len(sent_rows[0]) == len(df_t):
                            sent_rows = ([sent_rows[0][i] for i in pos_kept], sent_rows[1])
                        else:
                            sent_rows = (None, sent_rows[1])
                    tidy_info = dict(t_, rows_in=int(len(df_t)), rows_out=int(len(pos_kept)))
                    if drop_t:
                        lines_t = _record_lines(sent)
                        if sent_rows_before is None:
                            map_t = list(range(len(df_t)))
                        else:
                            map_t = sent_rows_before[0] if (sent_rows_before[0] is not None and len(sent_rows_before[0]) == len(df_t)) else None
                        order_t = sorted(drop_t)
                        tidy_info["left_out_rows"] = {
                            "frame": df_t.iloc[order_t],
                            "lines": [lines_t[map_t[i]] if map_t is not None and 0 <= map_t[i] < len(lines_t) else None for i in order_t],
                            "reasons": [t_["reasons"].get(i, "left out of the figures") for i in order_t]}
            except Refusal:
                raise
            except Exception:  # noqa: BLE001 - the tidy is an aid; the file is read as it stands
                if os.environ.get("NL_BROWSER_STRICT"):
                    raise
                tidy_info = None
        reshaped_after = False                    # the rules read the planned file as a long table
        if layout is None and structure_inner is None:
            try:
                kept_cols = _kept_by_visitor(decisions)
                reshaped, layout = _reshape_long_panel(data, keep=kept_cols)
                if layout and STRUCTURE_ON and int(layout.get("series") or 0) >= 2:
                    f_long: Dict[str, Any] = {}
                    if _long_has_structure(data, fail=f_long, keep=kept_cols):
                        layout = None             # totals beside parts: the structure reads it (the hook below)
                    elif f_long and struct_error is None:
                        # the structure layer could not be asked (a failure), or answered "not a cube" for a table of series: refused
                        struct_error = _verdict_failure(sent, f_long["verdict"]) if f_long.get("verdict") is not None else \
                            _guard_failure(sent, f_long)
                        if struct_error is not None:
                            cube_refusal = _reason_in_sentence(struct_error)
                            rep["structure"] = struct_error
                if layout:
                    data = reshaped
                    reshaped_after = True
            except Exception:  # noqa: BLE001 - the layout pass is an aid; the file is read as it stands
                layout = None
        if structure_inner is not None:
            # the slice's own table holds one row a month, and the outer table's layout fixes its rows a month (465
            # a month): track A2's row-count drop (w4-inference, nl_inference.row_series_artifact) reads
            # layout["rows_a_month"]; every other use of `layout` below is a long table's, which this is not
            layout = {"layout": STRUCTURE_LAYOUT, "structure_slice": True,
                      "rows_a_month": int(_ns().rows_a_month(structure_inner["S"])),
                      "period": dict(structure_inner["S"].get("period") or {"step": 1})}

        def visitor_lines() -> Optional[List[int]]:
            """Each record of the file the engine read, as its line in the visitor's file (review M2, 29 Sep
            2026: the downloads numbered the plan's re-written file, so a row after a filtered one, a quoted
            line break or a blank line carried the wrong line). ONE mapping for every download. None when the
            file was read reshaped: a long table read as one column per series has no such line."""
            if layout is not None or structure_inner is not None or wide_info is not None:
                return None
            lines = _record_lines(sent)
            if sent_rows is None:
                return lines                      # no plan ran: the engine read the visitor's own bytes
            pos, n_sent = sent_rows
            return [lines[i] for i in pos] if pos is not None and len(lines) == n_sent else None

        _install_stubs()
        _import_engine()
        _seed_forecast_coverage()
        from northledger import clean as _clean
        from northledger import engagement as E
        from northledger import intake as _intake
        from northledger import loop as _loop
        rep["engine"] = engine_info()

        t0 = time.perf_counter()
        tmp = tempfile.mkdtemp(prefix="nl_browser_")
        src_dir = os.path.join(tmp, "incoming")
        os.makedirs(src_dir)
        src = os.path.join(src_dir, name)
        with open(src, "wb") as fh:
            fh.write(data)
        eng = E.open_engagement(os.path.join(tmp, "engagement"), create=True)

        # -- read: land the file (sniff, parse, scan for personal data, code what is certain)
        try:
            res = E.land(eng, src)
        except _intake.IntakeError as exc:
            raise Refusal(str(exc))
        timings["read"] = time.perf_counter() - t0
        if res.truncated or res.n_rows > MAX_ROWS:
            raise Refusal("That file has more than 200,000 rows, the most this in-browser demo "
                          "reads. Nothing was sampled or cut: send a smaller extract, or email me "
                          "about the full audit.")
        if _count_rows(data) > int(res.n_rows):
            # pandas' reader skips a line with more fields than the header, with only a
            # warning; the engine would then report on fewer rows than the file holds.
            raise Refusal("Some lines of this file have more fields than its header row, and the "
                          "reader would skip them without saying so. Fix those lines (often a comma "
                          "inside an unquoted value) and try again. Nothing was sampled or cut.")
        rep["input"]["rows"], rep["input"]["columns"] = int(res.n_rows), int(res.n_cols)
        table = res.table
        if int(res.n_rows) * _exact_duplicates(eng.db_path, table) > DEDUPE_BUDGET:
            raise Refusal("This file holds so many exact duplicate rows that the engine's duplicate "
                          "check would take minutes in a browser tab, so the demo stops here rather "
                          "than freeze it. Remove the repeated rows and try again, or email me about "
                          "the full audit.")

        # -- decide: every flagged column (the engine's scan, then the adapter's personal-column check), the
        # visitor's choice or withhold; a withheld column is then landed as codes no cleaning rule reads
        t0 = time.perf_counter()
        flagged, withheld, scrub, released = _decide_and_guard(E, eng, res, decisions,
                                                               aside=(layout or {}).get("personal_set_aside"), sent_bytes=sent,
                                                               kept=(layout or {}).get("personal_kept"),
                                                               slice_measure=(structure_inner or {}).get("column"))
        rep["privacy"]["flagged"] = flagged
        rep["privacy"]["released"] = released
        timings["decide"] = time.perf_counter() - t0

        as_of_eff = as_of or _dt.date.today().isoformat()
        timer = _loop.StageTimer()
        audit = r = None
        refusal = ""
        pol = None
        colmap = dict(getattr(res, "column_map", {}) or {})
        prim_head = ""                            # the column the report leads with, named as the engine read it
        with _pinned_clock(as_of):
            # -- the Data Health Audit: the engine's data-quality findings
            t0 = time.perf_counter()
            audit = _loop.run_loop(eng.db_path, table, objective, out_dir=os.path.join(tmp, "audit"),
                                   display_name=name, as_of=as_of_eff)
            t_audit = time.perf_counter() - t0
            # -- the structure hook: the reading built early (the audit's cleaning is the analysis's), the structure
            # detected and cached with the profile's facts; a usable one is analysed as its slice
            early_reading = None
            if structure_inner is None and STRUCTURE_ON and ai_plan is None and layout is None and not cube_refusal:
                t_s = time.perf_counter()
                f_read: Dict[str, Any] = {}
                try:
                    early_reading = _engine_reading(eng.db_path, table, _clean.standard_rules(audit.health, as_of=as_of_eff),
                                                    audit.clean, colmap)
                except Exception as exc_r:  # noqa: BLE001 - the reading is built again below (a table of series: refused here)
                    f_read = _failure("reading", exc_r)
                    early_reading = None
                S_hook = None
                hidden_early = [str(f["column"]) for f in flagged if f.get("decision") != "keep"]
                f_hook: Dict[str, Any] = dict(f_read)
                if early_reading is not None:
                    S_hook = _structure_detect(early_reading, hidden_early, wide_info, fail=f_hook)
                if S_hook is not None and S_hook.get("kind") == "panel_no_relations":
                    # wave 5g (A): the layer left an official table of unrelated members to the long-table layout, and the layout did not
                    # apply (layout is None here): it is read one member at a time, never averaged across its members by the old path; a
                    # table that looks like a table of series and cannot be read so is refused
                    if not _ns().read_one_member_panel(S_hook):
                        struct_error = _panel_failure(sent, S_hook)
                        if struct_error is not None:
                            cube_refusal = _reason_in_sentence(struct_error)
                            rep["structure"] = struct_error
                if S_hook is None and f_hook:
                    # the structure layer could not run: a table of series is refused here, any other file is read as before
                    struct_error = _guard_failure(sent, f_hook)
                    if struct_error is not None:
                        cube_refusal = _reason_in_sentence(struct_error)
                        rep["structure"] = struct_error
                elif S_hook is not None and _refusable_verdict(S_hook):
                    # wave 5e (P7): the layer ran and answered "not a cube": a table of series is refused with the layer's own reason
                    struct_error = _verdict_failure(sent, S_hook)
                    if struct_error is not None:
                        cube_refusal = _reason_in_sentence(struct_error)
                        rep["structure"] = struct_error
                if S_hook is not None:
                    outer_info = _hook_cache(sent, early_reading, flagged, released, colmap, S_hook, audit, wh_list=withheld,
                                             rep=rep, name=name, pub_lite=lambda t: public_text(scrub.clean(t), table, name))
                    if S_hook.get("usable"):
                        timings["analyze"] = t_audit
                        timings["profile"], timings["clean"] = 0.0, 0.0
                        timings["read"] = timings.get("read", 0.0)
                        tm_outer = dict(timings, analyze=t_audit + (time.perf_counter() - t_s))
                        inner = _run_slice(S_hook, dict(S_hook["default"]), "S1", "engine_default", [], name=name,
                                           objective=objective, as_of=as_of, ai_plan=None, raw_context=None,
                                           outer=outer_info, timings=tm_outer)
                        if inner is not None:
                            if date_notes or number_notes:
                                _tidy_notes(inner, None, date_notes, number_notes)
                            return inner
                        struct_error = _slice_failure(sent)         # a table of series whose slice could not be run (else: read as before)
                        if struct_error is not None:
                            cube_refusal = _reason_in_sentence(struct_error)
                    elif S_hook.get("kind") == "cube_incomplete":
                        cube_refusal = S_hook.get("reason") or "the table's rows cannot be told apart"
                    # a table refused for its structure, or one whose slice could not run, says so; a file read as
                    # before (a business export whose members add up, a panel with no relation) carries none
                    rep["structure"] = _ns().public(S_hook) if (S_hook.get("kind") == "cube_incomplete" or
                                                                S_hook.get("usable")) else None
                    if struct_error is not None:
                        rep["structure"] = struct_error                   # the slice could not be run: said as such
            # -- the business analysis: measures, forecast, story
            try:
                pol = None
                # the plan's primary column, as the engine's claim for that measure (the live baseline of 30 Sep
                # 2026: the plan named VALUE, the rules then read the file as a long table whose one series was named
                # otherwise, and the gate's primary matched nothing, so the report led with the row count). A long
                # table's value column is one column per series: the report leads with one of them (_lead_series).
                prim_head = str((ai_plan or {}).get("primary") or "")
                if structure_inner is not None and not prim_head:
                    prim_head = str(structure_inner.get("column") or "")      # the slice's measure leads
                if layout and (not prim_head or prim_head == layout.get("value_column")):
                    layout["lead"], layout["lead_why"] = _lead_series(layout, objective)
                    if goal_from_plan and layout["lead_why"] == "your question names it":
                        layout["lead_why"] = "the goal the AI plan set names it"
                    prim_head = layout["lead"]
                if prim_head:
                    import dataclasses as _dc
                    from northledger import gate as _gate
                    pol = _dc.replace(_gate.DEFAULT_POLICY,
                                      primary_metric=str(colmap.get(prim_head) or _slug(prim_head)))
                if cube_refusal:
                    # a table of series whose readable columns cannot tell its rows apart: adding its rows would mix
                    # totals and parts, so the business analysis is refused with the reason (nl_structure)
                    refusal = cube_refusal
                else:
                    r = _loop.run_analyze(eng.db_path, table, objective, policy=pol,
                                          out_dir=os.path.join(tmp, "analysis"), as_of=as_of_eff,
                                          display_name=name, timer=timer,
                                          landing=E.landing_stamp(eng.meta(), table))
            except E.NotReady as exc:
                refusal = str(exc)
        st = {s["stage"]: float(s["seconds"]) for s in timer.stages}
        timings["profile"] = st.get("profile", 0.0)
        timings["clean"] = st.get("clean", 0.0)
        timings["analyze"] = t_audit + st.get("measure", 0.0) + st.get("gate_verify", 0.0)
        timings["forecast"] = st.get("forecast", 0.0)
        t_story = time.perf_counter()

        th = r.health if r is not None else audit.health
        cr = r.clean if r is not None else audit.clean
        wh = set(withheld)

        # -- health
        renamed: Dict[str, str] = {}
        reasons = _ambiguous_dates(cr, _clean.standard_rules(th, as_of=as_of_eff), renamed)

        def pub(t: Any) -> Any:
            t = public_text(scrub.clean(t), table, name)
            for old, new in renamed.items():
                t = t.replace(old, new) if isinstance(t, str) else t
            return t
        # the engine's "numbers are stored as text" line is left out: every upload is CSV text (_TEXT_NUMBERS_RE)
        rep["health"] = {"score": _num(th.score),
                         "issues": [pub(_plain(x)) for x in (th.findings or [])
                                    if _issue_is_safe(x, wh) and not _is_text_numbers_line(x)]}

        # -- cleaning
        rules = _clean.standard_rules(th, as_of=as_of_eff)
        rep["cleaning"] = {
            "rows_in": int(cr.total_in), "rows_clean": int(cr.rows_clean),
            "rows_quarantined": int(cr.rows_quarantined),
            "fixes": [dict(f, what=pub(f["what"])) for f in _fix_entries(cr, rules)],
            "quarantine_reasons": [{"reason": pub(k), "count": int(v)} for k, v in
                                   sorted(reasons.items(),
                                          key=lambda kv: (-kv[1], kv[0]))],
        }

        # -- findings: data quality from the audit, then the business analysis
        date_withheld = r is not None and bool(r.roles.date) and r.roles.date in wh
        findings = _findings(audit.gated, lambda f: "data_quality")
        if r is not None and not date_withheld:
            findings += _findings(r.gated, lambda f: "forecast" if f.fact_kind == "forecast"
                                  else "business")
        for f in findings:
            f["claim"], f["why"] = pub(f["claim"]), pub(f["why"])
        if isinstance(layout, dict) and isinstance(layout.get("period"), dict) and int(layout["period"].get("step") or 1) != 1:
            findings = [f for f in findings if f.get("kind") != "forecast"]       # about months of history: none are held
        findings.sort(key=lambda f: (_VERDICT_ORDER.get(f["verdict"], 9), _KIND_ORDER.get(f["kind"], 9)))
        rep["findings"] = findings

        # -- roles, forecast and story
        story = rep["story"]
        audit_actions = list(audit.full_brief.what_to_do or [])
        if r is None:
            # the engine's advice to an analyst ("read the audit's quarantine reasons, correct the
            # rule, and re-run") is left to the page, which says what a visitor can do instead
            why_not = re.split(r";\s*read the audit's quarantine reasons", _plain(refusal))[0].rstrip(". ")
            story["headline"] = "%s: %s." % (GATE_TRIPPED, why_not)
            story["what_to_do"] = _dedupe(audit_actions)
            story["cannot_answer"] = [x for x in _dedupe(list(audit.full_brief.cannot_answer or []))
                                      if x != story["headline"]]
            rep["forecast"]["reason"] = ("The business analysis did not run, so there is no monthly "
                                         "series to forecast.")
        elif date_withheld:
            why = ("%s, %s, is one you chose to withhold, and its months would appear in every figure. "
                   "Choose keep for %s to include it." % (DATE_WITHHELD_LEAD, r.roles.date, r.roles.date))
            story["headline"] = why
            story["what_to_do"] = _dedupe(audit_actions)
            story["cannot_answer"] = [why]
            rep["forecast"]["reason"] = why
            rep["roles"] = {"date": None, "measures": [], "dimensions": [],
                            "excluded": {r.roles.date: "withheld by you"}}
        else:
            s = r.story
            from northledger import narrate as _narrate
            # An empty section carries the engine's own line for it, as its Markdown does.
            story["headline"] = _plain(s.bottom_line) or _narrate.NOTHING_HAPPENED_LINE
            story["what_happened"] = _dedupe(s.what_happened) or [_narrate.NOTHING_HAPPENED_LINE]
            story["why"] = _dedupe(s.why) or [_narrate.NO_WHY_LINE]
            story["whats_next"] = _dedupe(s.whats_next) or [_narrate.NO_FORECAST_LINE]
            story["what_to_do"] = (_dedupe(list(s.what_to_do) + audit_actions)
                                   or [_narrate.NO_STORY_ACTION_LINE])
            story["cannot_answer"] = _dedupe(list(s.not_measured or []) +
                                             list(r.full_brief.cannot_answer or []))
            rep["roles"] = {"date": r.roles.date or None, "measures": list(r.roles.measures),
                            "dimensions": list(r.roles.dimensions),
                            "excluded": dict(r.roles.excluded)}
            rep["forecast"] = _forecast_block(r, eng.db_path, story["whats_next"], layout)
            if rep["forecast"].get("frequency"):
                story["whats_next"] = [rep["forecast"]["reason"]]
            story["whats_next"] = _demote_row_forecast_lines(
                story["whats_next"], rep["forecast"].get("row_forecast_dropped"),
                [str(x.label) for x in (getattr(r.measure, "series", []) or [])]) or [_narrate.NO_FORECAST_LINE]
        for k in ("what_happened", "why", "what_to_do", "whats_next", "cannot_answer"):
            story[k] = [pub(x) for x in story[k]]
        story["headline"] = pub(story["headline"])
        rep["forecast"]["reason"] = pub(rep["forecast"]["reason"])
        if rep["forecast"].get("label"):
            rep["forecast"]["label"] = pub(rep["forecast"]["label"])
        for k in ("audit", "row_forecast_dropped"):          # P0-13: the series they name, scrubbed as the label is
            if isinstance(rep["forecast"].get(k), dict) and rep["forecast"][k].get("series"):
                rep["forecast"][k]["series"] = pub(rep["forecast"][k]["series"])
        _one_forecast_evidence(rep)       # with an audit, the story states the audit's count of the range, no other
        rep["roles"]["excluded"] = {k: pub(v) for k, v in rep["roles"]["excluded"].items()}

        # -- downloads: withheld columns never leave, not even in a quarantine reason
        def reason_fix(text: Any) -> Any:
            if not isinstance(text, str):
                return text
            for c in wh:
                if text.startswith("column %r:" % c):
                    return "column %r: value withheld" % c
            return scrub.clean(text)
        ledgers = {"audit_ledger": audit.artifacts.get("evidence_json", "")}
        if r is not None and not date_withheld:
            ledgers["analysis_ledger"] = r.artifacts.get("evidence_json", "")
        vlines = visitor_lines()
        n_in = int(cr.total_in)
        idx = set(int(i) for i in cr.clean.index) | set(int(i) for i in cr.quarantined.index)
        rows_match = idx == set(range(n_in))
        lines = vlines if vlines is not None and len(vlines) == n_in and rows_match else None
        rep["downloads"] = {
            "clean_csv": _csv_text(cr.clean, withheld, None, lines),
            "quarantine_csv": _csv_text(cr.quarantined, withheld, reason_fix, lines),
            "ledger_json": _ledger_text(ledgers, scrub, rep["engine"]),
        }
        # -- the engine's reading of every cell of the table it landed (ONE parser: see _engine_reading)
        reading = early_reading
        try:
            if reading is None:
                reading = _engine_reading(eng.db_path, table, rules, cr, dict(getattr(res, "column_map", {}) or {}))
        except Exception:  # noqa: BLE001 - without it the tests use the engine's value readers, the analyses do not run
            if os.environ.get("NL_BROWSER_STRICT"):
                raise
            reading = None
        # every flagged column the visitor did not keep, by the engine's landed name (review H1: a "Date Of Birth"
        # header against the landed date_of_birth let a withheld column through). Its values never reach a card,
        # a download, an AI or a share link, and a withheld one is never named where an AI reads.
        hidden_land = {str(f["column"]): str(f["decision"]) for f in flagged if f.get("decision") != "keep"}

        def private(name: Any) -> Optional[str]:
            if name is None or not hidden_land:
                return None
            n = str(name)
            for k in (n, reading.landed(n) if reading is not None else None, _engine_slug(n)):
                if k and k in hidden_land:
                    return hidden_land[k]
            return None
        names_withheld = [c for c, d in hidden_land.items() if d == "withhold"]
        if cdf is not None:
            names_withheld += [str(h) for h in cdf.columns if private(h) == "withhold"]
        ai_scrub = _NameScrub(names_withheld)
        # the profile the planner reads next may come from this same reading (the page asks for it after the
        # visitor's choices; when those are the choices this run made, every flagged column withheld as the
        # scan runs, the profile needs no second pass of the engine: _same_choices)
        if ai_plan is None and layout is None and reading is not None and structure_inner is None and \
                not (_PROFILE_CACHE.get("sha") == hashlib.sha256(sent).hexdigest() and
                     _PROFILE_CACHE_STRUCTURE in (_PROFILE_CACHE.get("value") or {})):
            try:
                _PROFILE_CACHE.clear()
                import nl_viz as _nv
                pfacts = _profile_facts(reading, list(reading.land) or list(reading.values.columns), hidden_land)
                _PROFILE_CACHE.update(sha=hashlib.sha256(sent).hexdigest(), value={
                    "ok": True, "facts": pfacts, "released": [dict(x) for x in released],
                    "rows": reading.n, "flagged": [dict(f) for f in flagged],
                    "colmap": dict(getattr(res, "column_map", {}) or {}),
                    "viz_stats": _nv.profile_stats(reading, pfacts)})
            except Exception:  # noqa: BLE001 - the profile then lands the file itself
                if os.environ.get("NL_BROWSER_STRICT"):
                    raise
        # -- the data tests: phase one on the engine's reading of the same rows (unless the engine read the file
        # reshaped, when the engine's own value readers read the text), phase two from the engine's outcome
        aligned = (reading is not None and cdf is not None and not reshaped_after and len(cdf) == n_in and rows_match
                   and reading.n == len(cdf))
        if ai_plan is not None and cdf is not None:
            try:
                ctests, caux = _run_contracts(cdf, ai_plan, contracts_off, reading if aligned else None,
                                              {str(h): private(h) for h in cdf.columns if private(h)})
                ctests = ctests or None
            except Exception:  # noqa: BLE001 - the tests are an aid; the report stands without them
                if os.environ.get("NL_BROWSER_STRICT"):
                    raise
                ctests, caux = None, {}
        if ctests:
            from northledger.clean import QUARANTINE_COL
            engine_rows = None
            if aligned:
                q = cr.quarantined
                raw = {int(i): str(v) for i, v in q[QUARANTINE_COL].items()} if QUARANTINE_COL in q.columns else {}
                keyed = _rule_reasons(raw, rules, set(str(k) for k in (cr.quarantine_reasons or {})))
                aside = {i: pub(reason_fix(k)) for i, k in keyed.items()}
                empty: Dict[str, Set[int]] = {}
                for tt in ctests:
                    col = tt["column"]
                    if tt.get("unreadable") and (caux.get(col) or {}).get("read_as") == "numbers":
                        land = reading.landed(col)
                        if land is not None and land in cr.clean.columns:
                            empty[col] = set(int(i) for i in cr.clean.index[cr.clean[land].isna()])
                engine_rows = {"aside": aside, "empty": empty}
            dup_reason = pub(reason_fix(next((ru.reason_key() for ru in rules if ru.kind == "dedupe"),
                                             "exact_duplicates: exact duplicate row")))
            _finish_contracts(ctests, caux, engine_rows, dup_reason)
            # a flagged column's values never show in the card or the download unless the visitor chose keep
            # (a coded column's raw values would undo the code: the downloads carry only its codes); every other
            # example is scrubbed as the download scrubs its values
            hide: Dict[str, str] = {}
            for tt in ctests:
                d = private(tt["column"])
                tt["private"] = d
                if d:
                    hide[tt["column"]] = d
                    tt["examples"] = ["value withheld" if d == "withhold" else "value coded"] if tt["failed"] else []
                else:
                    tt["examples"] = [scrub.clean(x) for x in tt["examples"]]
            tlines = vlines if vlines is not None and cdf is not None and len(vlines) == len(cdf) else None
            text, n_cells = _flagged_cells(ctests, caux, cdf, tlines, engine_rows, hide, scrub.clean)
            rep["contracts"] = {"tests": ctests, "cells_flagged": n_cells, "line": "source_line" if tlines else "table_row",
                                "note": CONTRACT_NOTE + (" The download lists the first %s of the %s flagged values."
                                                         % (format(FLAGGED_CELLS_MAX, ","), format(n_cells, ","))
                                                         if n_cells > FLAGGED_CELLS_MAX else "")}
            if n_cells:
                rep["downloads"]["contract_flagged_csv"] = text
        # -- contract v2: grades, tests, provenance, quality profile and chart data. The plan's primary column leads
        # (the engine's claim for that measure: _V2.primary_gated), and a measure the plan reads is named from its
        # column and the plan's label and unit (_measure_names), never from a value in the file
        plan_measure = None
        if ai_plan and prim_head:
            pcs = {str(c.get("name")): c for c in ai_plan.get("columns") or [] if isinstance(c, dict)}
            pc = pcs.get(prim_head) or pcs.get(str(ai_plan.get("primary") or "")) or {}
            plan_measure = (str(colmap.get(prim_head) or _slug(prim_head)), str(pc.get("semantic_type") or ""))
        _build_v2(rep, audit, r if (r is not None and not date_withheld) else None, th, cr, eng.db_path,
                  flagged, withheld, pub, as_of_eff, objective, reasons, rules, plan_measure,
                  _measure_names(ai_plan, colmap, pub))
        timings["story"] = st.get("narrate", 0.0) + st.get("write", 0.0) + (time.perf_counter() - t_story)
        if layout and structure_inner is None:
            _layout_notes(rep, layout)
        if tidy_info is not None or date_notes or number_notes:
            _tidy_left_out_rows(rep, tidy_info)
            _tidy_notes(rep, tidy_info, date_notes, number_notes)
        # T4: an official aggregate is described, not tested (the header as the visitor sent it)
        try:
            import pandas as _pd_h
            _hdr = [str(c) for c in _pd_h.read_csv(io.BytesIO(sent), dtype=str, nrows=0, encoding="utf-8-sig").columns]
        except Exception:  # noqa: BLE001 - no header, no publisher signature
            _hdr = []
        _official_inference(rep, _hdr, layout, [c for c, d in hidden_land.items()] + names_withheld)
        if ai_plan:
            if plan_review:
                ai_plan["review"] = plan_review
            rep["ai_plan"] = ai_plan
            # every step that set rows aside, with its count, share and the plan's reason (_row_drops)
            ai_plan["row_drops"] = _row_drops(plan_drops, private)
            # the web searches, built by the adapter from the plan's items of list terms (never free text, never
            # anything from the file): what the page sends (an explicit [] means no search); a dropped item is
            # counted by its reason
            ai_plan["context_queries"], ai_plan["context_queries_dropped"], ai_plan["context"] = \
                _context_queries(raw_context)
            # the engine's gate (it refused its own analysis): the analyses stop with it and the planner is not
            # asked again (_gate_state)
            gate = None
            if r is None:
                from northledger import gate as _gate
                limit = float(getattr(pol if pol is not None else _gate.DEFAULT_POLICY, "max_quarantine_rate", 0.20))
                gate = _gate_state(cr, limit)
            if ai_plan.get("analyses"):
                if reading is None:
                    rep["ai_analyses"] = {"items": [], "refused": ["the analyses could not read the engine's table"]}
                else:
                    # a percentage column on one scale, read once for the data test and the analyses
                    pct: Dict[str, Any] = {}
                    for pc in ai_plan.get("columns") or []:
                        nm = pc.get("name") if isinstance(pc, dict) else None
                        if not nm or pc.get("semantic_type") != "percentage" or private(nm):
                            continue
                        lands = [reading.landed(nm)]
                        if layout and nm == layout.get("value_column"):
                            lands = [reading.landed(x) for x in layout.get("order") or []]
                        for land in lands:
                            if land is not None and reading.kind(land) == "number":
                                pct[reading.header(land)] = _pct_reading(reading.numbers(land), reading.texts[land],
                                                                         reading.filled(land))
                    actx = {"reading": reading, "clean": cr.clean, "hide": hidden_land, "pct": pct, "private": private,
                            "gate": gate, "names_by": scrub.flag_tokens_by}
                    rep["ai_analyses"] = _run_analyses(actx, ai_plan, None if structure_inner is not None else layout)
                    _zero_note_rows(rep, actx.get("zeros") or {}, contracts_off)
            rep["plan_signals"] = _plan_signals(rep, ai_plan, ai_scrub, gate)
        if plan_review and not ai_plan and not plan_review["approved"]:
            rep["plan_review"] = plan_review
        _mend_short_history_line(rep, cr.clean if (r is not None and not date_withheld) else None,
                                 r.roles.date if (r is not None and not date_withheld) else None)
        if r is not None and not date_withheld:
            _true_headline(rep, r, pub, _date_grain(cr.clean, r.roles.date))
        # the question the report answers: the plan's goal, else the visitor's own (never the default question)
        goal = str((ai_plan or {}).get("goal") or (objective if objective != DEFAULT_OBJECTIVE else ""))
        if structure_inner is not None and r is not None:
            _structure_inner_blocks(rep, structure_inner)
        rep["scenarios"] = _scenarios(rep, r, cr, hidden_land, reading, ai_plan, pub, date_withheld, goal,
                                      structure_inner)
        # the charts chosen from the data (engine/nl_viz.py), after the scenarios they read; each is also a rule "V"
        # record in rep["charts"]
        rep["viz"] = _viz(rep, r if not date_withheld else None, reading, ai_plan, pub, goal, scrub.flag_tokens,
                          scrub.flag_tokens_by, structure_inner)
        _mend_cannot_answer(rep)
        rep["ok"] = True
    except Refusal as exc:
        rep["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - a plain refusal, never a traceback
        if os.environ.get("NL_BROWSER_TRACE"):
            import traceback as _tb
            _tb.print_exc()
        msg = " ".join(str(exc).split())[:200]
        rep["error"] = ("The engine stopped on this file (%s%s). Nothing was sent anywhere."
                        % (type(exc).__name__, ": " + msg if msg else ""))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        rep["timings"] = [{"stage": s, "seconds": round(float(timings[s]), 3)} for s in STAGES]
        _ensure_v2(rep)
    return rep


def _first_sentence(text: str) -> str:
    """The first sentence of an analysis sentence ("... 12,061). units rose ..." ends at the first full stop
    followed by a capital or a column name's start; a decimal point never ends one)."""
    m = re.search(r"\.\s+(?=[A-Z(a-z])", text or "")
    return (text[:m.start() + 1] if m else (text or "")).strip()


def _date_grain(clean: Any, col: Any) -> str:
    """How far apart the engine's dates are: "yearly" (a year or more between distinct dates, as a panel
    of countries by year), "quarterly", or "" (monthly or finer, or unknown)."""
    try:
        import pandas as pd
        if not col or col not in clean.columns:
            return ""
        d = pd.to_datetime(clean[col], errors="coerce").dropna().drop_duplicates().sort_values()
        step = d.diff().dropna().median() if len(d) > 2 else None
        if step is None or step != step:
            return ""
        return "yearly" if step >= pd.Timedelta(days=300) else "quarterly" if step >= pd.Timedelta(days=80) else ""
    except Exception:  # noqa: BLE001 - a missing grain only shortens the headline's reason
        return ""


def _true_headline(rep: Dict[str, Any], r: Any, pub: Any, grain: str = "") -> None:
    """The engine's monthly story has no bottom line when nothing it tested settled; its fallback then reads
    "No business measure could be computed from this file", which is false when a measure was computed
    (review of 29 Sep 2026: a 10-year file of monthly sales whose every monthly change was graded too little
    data to judge, and whose long-run trend the AI plan's analyses drew). The headline then says what
    happened to the monthly change test, and why, and quotes the lead analysis's own sentence. The engine's
    narrate.py is left as it is (its decision code is what the benchmark receipt measured)."""
    from northledger import narrate as _narrate
    st = rep.get("story") or {}
    fallback = (_narrate.NOTHING_HAPPENED_LINE, pub(_narrate.NOTHING_HAPPENED_LINE))
    if st.get("headline") not in fallback:
        return
    biz = [f for f in rep.get("findings") or [] if f.get("kind") == "business"]
    items = (rep.get("ai_analyses") or {}).get("items") or []
    if biz and all(f.get("verdict") == "INSUFFICIENT" for f in biz):
        # what was tested, counted as it was (review, 29 Sep 2026: "5 business measures" were 5 tests over the
        # row count and 2 columns)
        dcol = (rep.get("roles") or {}).get("date")
        reads = [[c for c in (f.get("columns_read") or []) if c != dcol] for f in biz]
        cols = list(dict.fromkeys(c for cs in reads for c in cs))
        what = _n_values(len(biz), "test")
        over = (["the row count"] if any(not cs for cs in reads) else []) + (
            ["%s (%s)" % (_n_values(len(cols), "column"), ", ".join(cols[:4]) + (", ..." if len(cols) > 4 else ""))]
            if cols else [])
        if over:
            what += " over " + " and ".join(over)
        why = ("The monthly change test (the latest 12 months against the 12 before) ran %s and settled none of "
               "them: each is graded too little data to judge%s." % (
                   what,
                   {"yearly": ", because the rows are yearly, so each 12 months hold one date",
                    "quarterly": ", because the rows are quarterly, so each 12 months hold four dates"}.get(grain, "")))
    elif biz:
        return                                  # something settled or is being watched: the engine says so itself
    else:
        reasons = [_plain(x) for x in list(getattr(getattr(r, "measure", None), "unmeasured", None) or [])]
        reasons += [x for x in st.get("cannot_answer") or [] if x not in fallback]
        if not (rep.get("roles") or {}).get("date"):
            reason = "the engine found no date column to count months by"
        elif grain:
            reason = "the rows are %s, and the test compares months" % grain
        elif reasons:
            reason = reasons[0].rstrip(". ")
            reason = reason[:1].lower() + reason[1:]
        else:
            reason = "no monthly measure could be formed from this file's rows"
        why = "The monthly change test did not run: %s." % reason
    head = why
    if items:
        # the analysis of the plan's primary column leads, when there is one (the headline follows the plan)
        prim = str((rep.get("ai_plan") or {}).get("primary") or "")
        lead = next((a for a in items if prim and prim in (a.get("columns") or [])), items[0])
        head += " From the AI plan's analyses: %s" % _first_sentence(lead.get("sentence") or "")
    st["headline"] = pub(head)
    if st.get("what_happened") in ([_narrate.NOTHING_HAPPENED_LINE], [pub(_narrate.NOTHING_HAPPENED_LINE)]):
        st["what_happened"] = [pub(why)]


def _plan_signals(rep: Dict[str, Any], plan: Dict[str, Any], scrub: Optional["_NameScrub"] = None,
                  gate: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """What the engine found wrong with the AI's plan, for the planner's one self-correction (max 20). The
    planner never hears of a column the visitor withheld: its tests are left out and its name never appears.
    Nothing at all when the engine's gate tripped (it set aside more than its limit of the rows): no data test,
    step or analysis signal, so the planner is not asked again (see _gate_state for why a new plan cannot help)."""
    out: List[Dict[str, Any]] = []
    if gate and gate.get("over"):
        return out
    safe = scrub if scrub is not None else (lambda x: x)
    # a test's fact is a signal only when it is evidence against the AI's reading (a probable misread, a type
    # the values contradict); a reading that stood is not, and telling the planner its correct reading was
    # wrong would send it to change what worked (live run, 29 Sep 2026)
    for t in (rep.get("contracts") or {}).get("tests") or []:
        if t.get("signal") and t.get("problem") and t.get("private") != "withhold":
            out.append({"kind": "contract_failed", "column": str(t.get("column")), "detail": safe(str(t.get("problem")))[:200]})
    for x in plan.get("refused") or []:
        out.append({"kind": "layout_refused" if str(x).startswith("long_to_wide") else "op_refused", "detail": safe(str(x))[:200]})
    # a step whose set-aside rows that hold a usable value of the plan's primary measure (row_drops[].valued) are
    # PLAN_DROP_NOTICE_PCT or more of the file's rows (final evaluation, 1 Oct 2026: a fifth of the reviews went on a
    # false "overlap"): the one re-plan may keep them; the engine's check of the plan's reason goes too. Rows with no
    # usable value cannot be analysed whatever the plan says (integration pass, 1 Oct 2026: the FX plan's 569 rows with
    # a blank VALUE cost a re-plan call), so they are disclosed (row_drops[].text, .notice) but never a signal.
    for d in plan.get("row_drops") or []:
        if not isinstance(d, dict):
            continue
        n, of = int(d.get("rows") or 0), int(d.get("of") or 0)
        v = n if d.get("valued") is None else int(d["valued"])
        if not of or 100.0 * v / of < PLAN_DROP_NOTICE_PCT:
            continue
        detail = "the plan's filter set aside %s of %s rows (%s)" % (format(n, ","), format(of, ","),
                                                                    _pct_text(100.0 * n / of))
        if v < n:
            detail += ", %s of them (%s of the rows) with a value in %s" % (
                format(v, ","), _pct_text(100.0 * v / of), str(plan.get("primary") or "the measure"))
        detail += "; keep them unless the goal needs them excluded"
        if d.get("check"):
            detail += "; " + str(d["check"])
        out.append({"kind": "other", "column": str(d.get("column") or ""), "detail": safe(detail)[:200]})
    ana = rep.get("ai_analyses") or {}
    # an analysis refusal is feedback only while the engine's own analysis ran (refused for another reason, no
    # new plan makes it run either)
    if not gate:
        for x in ana.get("refused") or []:
            out.append({"kind": "analysis_refused", "detail": safe(str(x))[:200]})
    biz = [f for f in rep.get("findings") or [] if f.get("kind") == "business"]
    if biz and all(f.get("grade") == "NOT_ENOUGH_DATA" for f in biz) and not ana.get("items"):
        out.append({"kind": "no_findings", "detail": "every business finding has too little data to judge"})
    return out[:20]


def _scenarios(rep: Dict[str, Any], r: Any, cr: Any, hidden: Dict[str, str], reading: Any, plan: Any, pub: Any,
               date_withheld: bool, goal: str = "", structure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """rep["scenarios"] (design B; engine/nl_scenarios.py): the engine's claims as its gate kept them, the rows its
    cleaner kept without any column the visitor withheld or coded (the frame the analyses read, by the landed
    names the claims use), and the plan's column roles and units. The engine's own files are not touched."""
    refused = ""
    if r is None:
        refused = "the engine's business analysis did not run, so there is no claim to break down"
    elif date_withheld:
        refused = "the date column is one you withheld, so no change over time is broken down"
    if refused:
        return {"basis": None, "items": [], "refused": [refused],
                "note": "No scenario or contribution figures for this file; the reasons are listed."}
    if structure is not None and rep.get("estimand"):
        try:
            import nl_scenarios as _nsc
            return _nsc.build_structure(rep, structure, plan if isinstance(plan, dict) else None)
        except Exception as exc:  # noqa: BLE001 - the report stands without the block, and says so
            if os.environ.get("NL_BROWSER_STRICT"):
                raise
            return {"basis": None, "items": [], "refused": ["the structure's breakdown could not be computed (%s)"
                                                            % type(exc).__name__],
                    "note": "No scenario or contribution figures for this file; the reasons are listed."}
    try:
        import nl_scenarios as _ns
        from northledger.clean import QUARANTINE_COL
        from northledger.measure import additive_kind
        clean = cr.clean
        frame = clean.drop(columns=[c for c in clean.columns if c == QUARANTINE_COL or c in (hidden or {})])
        claims: Dict[str, Dict[str, Any]] = {}
        for g in r.gated:
            fact = getattr(g, "fact", None)
            key = getattr(fact, "claim_key", None) if fact is not None else None
            if not key:
                continue
            t = getattr(fact, "test", None) or {}
            claims[str(fact.id)] = {"key": str(key), "total_of": t.get("total_of"), "kind_split": t.get("kind_split"),
                                    "currency": t.get("currency"), "status_excluded": t.get("status_excluded"),
                                    "like_for_like_of": t.get("like_for_like_of")}
        return _ns.build(rep, frame, r.roles.date or None, claims,
                         plan if isinstance(plan, dict) and plan.get("columns") else None,
                         reading.landed if reading is not None else None, pub, additive_kind, goal)
    except Exception as exc:  # noqa: BLE001 - the report stands without the block, and says so
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return {"basis": None, "items": [], "refused": ["the scenarios could not be computed for this file (%s)"
                                                        % type(exc).__name__],
                "note": "No scenario or contribution figures for this file; the reasons are listed."}


def _viz(rep: Dict[str, Any], r: Any, reading: Any, plan: Any, pub: Any, goal: str = "",
         names: Any = frozenset(), names_by: Any = None, structure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """rep["viz"] (engine/nl_viz.py, CONTRACT §5.9): the charts the AI plan chose, each checked and built from the rows
    the engine kept, or the engine's own picks; every refusal with its reason. The report stands without them. names:
    the exact tokens of every flagged column's values (Scrubber.flag_tokens); names_by: the same by landed column
    (Scrubber.flag_tokens_by), so the theme chart of a kept text leaves out every other column's (nl_viz.Ctx.names_but)."""
    try:
        import nl_viz as _nv
        return _nv.build(rep, {"r": r, "reading": reading, "plan": plan if isinstance(plan, dict) else None,
                               "pub": pub, "goal": goal, "names": names, "names_by": names_by or {},
                               "structure": structure})
    except Exception as exc:  # noqa: BLE001 - the report stands without the charts, and says so
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return {"version": VIZ_VERSION, "charts": [], "refused": [
            {"chart": "all", "columns": [], "why": "the charts could not be built for this file (%s)" % type(exc).__name__,
             "chosen_by": "engine"}], "chosen_by": "none"}


# ----------------------------------------------------------------------------- the report's web searches
# The AI-written report may cite public sources, found by up to 4 web searches the page sends to the report writer
# (rep.ai_plan.context_queries; an explicit [] means no search). A search is never free text: not the planner's and
# never anything from the visitor's file (final review, 30 Sep 2026: a block-list check of free-text searches let
# names through in a dozen ways, among them a client column typed as a category, accented and "ue" spellings, CJK
# names, two-letter surnames, cp1252 and "|" files and its own "ordinary words" rule). The plan names what it wants
# as items of fixed terms, plan.context = at most CONTEXT_MAX of {"indicator", "sector", "region", "years"}, each
# term from engine/context_terms.json (CONTEXT_TERMS_FILE: generic indicators, industries and places), and the
# adapter builds each search itself from the list's own spelling of those terms and the years: "[sector] indicator
# [region] [from] [to]". An item whose indicator is missing or any term is off its list (compared after _term_key),
# or whose years are not integers from 1900 to 2099 in order, is dropped whole; other keys in an item are ignored.
# The plan's old free-text context_queries is never read. A term that happens to equal a value in the file (a
# region column holding "Ontario") is still only a list term. Each dropped item is counted by its reason in
# ai_plan.context_queries_dropped; ai_plan.context holds the items kept, in the list's spelling.
CONTEXT_TERMS_FILE = "context_terms.json"
CONTEXT_MAX = 4                     # searches built from the plan's items, at most
CONTEXT_READ = 12                   # items read from the plan, at most
CONTEXT_YEARS = (1900, 2099)
CQ_NOT_ITEM = "not an item of list terms"
CQ_INDICATOR = "an indicator not on the list"
CQ_SECTOR = "a sector not on the list"
CQ_REGION = "a region not on the list"
CQ_YEARS = "years that are not a range from 1900 to 2099"
CQ_MORE = "more than 4 searches"
CQ_TWICE = "the same search twice"
CQ_NO_LIST = "the list of search terms could not be read"
_CONTEXT_TERMS: Dict[str, Any] = {}


def _term_key(s: Any) -> str:
    """A term as the list compares it: NFKD, combining marks (accents) dropped, case-folded, every run of characters
    that are not letters or digits one space, trimmed ("Côte d'Ivoire" and "cote d ivoire" are one term)."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(s))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[\W_]+", " ", t).strip()


def _context_terms() -> Dict[str, Any]:
    """engine/context_terms.json as the adapter reads it (beside this file, in the pack too): {"version", and for
    "indicator", "sector" and "region" a map from each term's _term_key to the list's own spelling}, or {} when the
    file is missing or malformed (then no search is built). Read once."""
    if "value" in _CONTEXT_TERMS:
        return _CONTEXT_TERMS["value"]
    got: Dict[str, Any] = {}
    try:
        with open(os.path.join(HERE, CONTEXT_TERMS_FILE), encoding="utf-8") as fh:
            raw = json.load(fh)
        maps = {}
        for field, key in (("indicator", "indicators"), ("sector", "sectors"), ("region", "regions")):
            terms = raw[key]
            if not isinstance(terms, list) or not terms or not all(
                    isinstance(t, str) and t.strip() and t.isascii() and not re.search(r"\d", t) for t in terms):
                raise ValueError(key)
            maps[field] = {_term_key(t): t for t in terms}
        got = dict(maps, version=str(raw.get("version") or ""))
    except Exception:  # noqa: BLE001 - no list, no search
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        got = {}
    _CONTEXT_TERMS["value"] = got
    return got


def _context_year(y: Any) -> Optional[int]:
    """A year of an item: an integer (never a bool) or a string of 4 digits, from 1900 to 2099; else None."""
    if isinstance(y, bool):
        return None
    if isinstance(y, int):
        v = y
    elif isinstance(y, str) and re.fullmatch(r"\d{4}", y.strip()):
        v = int(y.strip())
    else:
        return None
    return v if CONTEXT_YEARS[0] <= v <= CONTEXT_YEARS[1] else None


def _context_queries(raw: Any) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """(the searches built from plan.context, a reason for each item dropped, the items kept in the list's spelling):
    see CONTEXT_TERMS_FILE. Anything but a list of items is no search. Every word of a search is a list term or a
    year: nothing the plan wrote, and nothing from the file, can reach it."""
    items = raw[:CONTEXT_READ] if isinstance(raw, list) else []
    if not items:
        return [], [], []
    terms = _context_terms()
    if not terms:
        return [], [CQ_NO_LIST] * len(items), []
    queries: List[str] = []
    dropped: List[str] = []
    kept: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            dropped.append(CQ_NOT_ITEM)
            continue
        got: Dict[str, Any] = {}
        why = ""
        for field, reason in (("indicator", CQ_INDICATOR), ("sector", CQ_SECTOR), ("region", CQ_REGION)):
            v = it.get(field)
            if field != "indicator" and (v is None or (isinstance(v, str) and not v.strip())):
                continue                            # an optional term left out
            t = terms[field].get(_term_key(v)) if isinstance(v, str) else None
            if t is None:
                why = reason
                break
            got[field] = t
        if not why and it.get("years") not in (None, []):
            ys = it.get("years")
            yy = [_context_year(y) for y in ys] if isinstance(ys, list) and 1 <= len(ys) <= 2 else [None]
            if any(y is None for y in yy) or yy[0] > yy[-1]:
                why = CQ_YEARS
            else:
                got["years"] = [yy[0], yy[-1]]
        if why:
            dropped.append(why)
            continue
        words = [got.get("sector"), got["indicator"], got.get("region")]
        if "years" in got:
            words += [str(got["years"][0])] + ([str(got["years"][1])] if got["years"][1] != got["years"][0] else [])
        q = " ".join(w for w in words if w)
        if q in queries:
            dropped.append(CQ_TWICE)
        elif len(queries) >= CONTEXT_MAX:
            dropped.append(CQ_MORE)
        else:
            queries.append(q)
            kept.append(got)
    return queries, dropped, kept


def run_json(csv_bytes: Any, name: str, objective: str = "", decisions: Any = None,
             as_of: Optional[str] = None) -> str:
    """run(), as JSON text (what the page reads across the Python/JavaScript boundary).
    `decisions` may be a dict or a JSON string."""
    if isinstance(decisions, str):
        try:
            decisions = json.loads(decisions) if decisions.strip() else None
        except ValueError:
            decisions = None
    elif decisions is not None and hasattr(decisions, "to_py"):
        decisions = decisions.to_py()
    return json.dumps(run(csv_bytes, name, objective, decisions, as_of), allow_nan=False)


if __name__ == "__main__":
    # python engine/nl_browser.py FILE [OBJECTIVE] [--as-of YYYY-MM-DD]: print the report
    args = [a for a in sys.argv[1:]]
    pinned = None
    if "--as-of" in args:
        i = args.index("--as-of")
        pinned = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        sys.exit(2)
    with open(args[0], "rb") as fh:
        out = run(fh.read(), os.path.basename(args[0]), args[1] if len(args) > 1 else "", as_of=pinned)
    for k in ("clean_csv", "quarantine_csv", "ledger_json"):
        out["downloads"][k] = "<%d characters>" % len(out["downloads"][k])
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["ok"] else 1)
