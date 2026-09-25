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
    the visitor's file (source_line) and write whole numbers without ".0".

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
import types
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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
# Added to a flagged column's kind when the engine coded its values as it landed the file.
CODED_ON_ARRIVAL = "coded as it arrived"

# THE REPORT CONTRACT: the keys run() returns, at each level. The page and the tests both
# check against this, so a change here is a change to the contract. Version 2 (engine/
# CONTRACT-v2.md, design §4.2) keeps every v1 key and adds the V2_* keys below.
CONTRACT_VERSION = 2
REPORT_KEYS = ("ok", "error", "engine", "input", "timings", "privacy", "health", "cleaning",
               "roles", "findings", "forecast", "story", "downloads",
               "contract_version", "primary_metric", "tests_run", "methods", "limitations",
               "reproducibility", "charts", "charts_suppressed", "llm", "summary")
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
        "privacy": {"flagged": []},
        "health": {"score": None, "issues": [], "score_min": None, "score_mean": None, "weakest": None,
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
                     "decision_edge": None},
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


class Scrubber:
    """Finds a withheld column's values inside text and replaces them with [withheld].

    Values are compared whole, after collapsing whitespace, trimming punctuation at the
    edges and lower-casing: every quoted span and every run of up to 8 words is looked up
    in a set, so the cost grows with the text, not with the number of values. Values that
    identify no one are skipped: under 3 characters, or purely numeric (a withheld salary
    column's 52,000 must not blank out a real figure that happens to be equal)."""

    MAX_WORDS = 8

    def __init__(self, values: Iterable[Any] = ()) -> None:
        self.values: Set[str] = set()
        self.words = 1
        for v in values:
            n = _norm(v)
            if len(n) < 3 or _NUMBER_LIKE.match(n):
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


def _withheld_values(db_path: str, table: str, columns: List[str]) -> List[str]:
    """Every distinct value of the withheld columns as landed, plus the ISO form of any
    that reads as a date (the profiler prints dates as YYYY-MM-DD)."""
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
            if vals:
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
               "ip": "IP address", "person_name": "person's name"}


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


def _forecast_block(r: Any, db_path: str, story_next: List[str]) -> Dict[str, Any]:
    from northledger import forecast as _fc
    from northledger._sqlite import connect_ro
    block = blank_report()["forecast"]
    series = list(getattr(r.measure, "series", []) or [])
    if not series:
        reasons = list(getattr(r.measure, "unmeasured", []) or [])
        block["reason"] = _plain(reasons[0]) if reasons else "No monthly series was found to forecast."
        return block
    s = next((x for x in series if x.slug in r.forecasts), series[0])
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
                 objective: str, reasons: Dict[str, int], rules: List[Any]) -> None:
        self.rep, self.audit, self.ana, self.th, self.cr = rep, audit, ana, th, cr
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
                s = next((x for x in series if x.slug in ana.forecasts), series[0])
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
        tested = [g for g in self.shown() if g.fact.test and g.fact.test.get("ran")]
        prim = [g for g in tested if (g.gate.signals or {}).get("family") == "primary"]
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
                   "method": "", "scale": fact.unit, "unit": None, "note": None}
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
               and f["id"] not in mon]
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
            self.add("catmonth.%s" % dim, "#8", "heatmap", "Rows by %s and month" % dim, "analyst",
                     "a tested change claim and a category column with 2-50 levels (which segments drive the "
                     "change is not computed in this release, so no reversal is flagged)",
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
                what = rows_word if slug == "monthly_rows" else \
                    (self.name_of(by_slug[slug]) if slug in by_slug else "")
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
        if fcf is not None and fid not in sm["monitoring"]:
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
                if fcf["grade"] == "CONFIRMED":
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
              reasons: Dict[str, int], rules: List[Any]) -> None:
    """The v2 blocks. A failure here leaves the v1 report whole and says so under limitations
    (with NL_BROWSER_STRICT set, as the tests set it, it stops the run instead)."""
    try:
        _V2(rep, audit, ana, th, cr, db_path, flagged, withheld, pub, as_of, objective, reasons, rules).build()
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

        # -- decide: every flagged column, the visitor's choice or withhold
        t0 = time.perf_counter()
        flagged = _apply_decisions(E, eng, res, decisions)
        rep["privacy"]["flagged"] = flagged
        withheld = [f["column"] for f in flagged if f["decision"] == "withhold"]
        scrub = Scrubber(_withheld_values(eng.db_path, table, withheld))
        timings["decide"] = time.perf_counter() - t0

        as_of_eff = as_of or _dt.date.today().isoformat()
        timer = _loop.StageTimer()
        audit = r = None
        refusal = ""
        with _pinned_clock(as_of):
            # -- the Data Health Audit: the engine's data-quality findings
            t0 = time.perf_counter()
            audit = _loop.run_loop(eng.db_path, table, objective, out_dir=os.path.join(tmp, "audit"),
                                   display_name=name, as_of=as_of_eff)
            t_audit = time.perf_counter() - t0
            # -- the business analysis: measures, forecast, story
            try:
                r = _loop.run_analyze(eng.db_path, table, objective,
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
        rep["health"] = {"score": _num(th.score),
                         "issues": [pub(_plain(x)) for x in (th.findings or [])
                                    if _issue_is_safe(x, wh)]}

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
            why = ("The time analysis is not shown: the date column the engine chose for it, %s, is "
                   "one you chose to withhold, and its months would appear in every figure. Choose "
                   "keep for %s to include it." % (r.roles.date, r.roles.date))
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
            rep["forecast"] = _forecast_block(r, eng.db_path, story["whats_next"])
        for k in ("what_happened", "why", "what_to_do", "whats_next", "cannot_answer"):
            story[k] = [pub(x) for x in story[k]]
        story["headline"] = pub(story["headline"])
        rep["forecast"]["reason"] = pub(rep["forecast"]["reason"])
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
        starts = _record_lines(data)
        n_in = int(cr.total_in)
        idx = set(int(i) for i in cr.clean.index) | set(int(i) for i in cr.quarantined.index)
        lines = starts if len(starts) == n_in and idx == set(range(n_in)) else None
        rep["downloads"] = {
            "clean_csv": _csv_text(cr.clean, withheld, None, lines),
            "quarantine_csv": _csv_text(cr.quarantined, withheld, reason_fix, lines),
            "ledger_json": _ledger_text(ledgers, scrub, rep["engine"]),
        }
        # -- contract v2: grades, tests, provenance, quality profile and chart data
        _build_v2(rep, audit, r if (r is not None and not date_withheld) else None, th, cr, eng.db_path,
                  flagged, withheld, pub, as_of_eff, objective, reasons, rules)
        timings["story"] = st.get("narrate", 0.0) + st.get("write", 0.0) + (time.perf_counter() - t_story)
        rep["ok"] = True
    except Refusal as exc:
        rep["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - a plain refusal, never a traceback
        msg = " ".join(str(exc).split())[:200]
        rep["error"] = ("The engine stopped on this file (%s%s). Nothing was sent anywhere."
                        % (type(exc).__name__, ": " + msg if msg else ""))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        rep["timings"] = [{"stage": s, "seconds": round(float(timings[s]), 3)} for s in STAGES]
        _ensure_v2(rep)
    return rep


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
