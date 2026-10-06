#!/usr/bin/env python3
"""
Tests for engine/nl_browser.py, the adapter that runs the NorthLedger engine in the browser.

    python tools/test_nl_browser.py                      # the adapter in engine/
    NL_BROWSER_DIR=/some/copy python tools/test_nl_browser.py   # a copy (used to prove red)

Runs the adapter natively in CPython, on the synthetic sample and on adversarial files, and
checks:
  * THE REPORT CONTRACT: exact keys at every level, types, JSON without NaN, ok/error
  * refusals: empty, header only, over 25 MB, over 200,000 rows, a spreadsheet, garbage;
    each ok=false with a plain error, never an exception, and never a silent sample
  * a flagged column (an email column, a salary column, free text) is withheld from both
    downloads, from the ledger, from every claim and from every quarantine reason
  * every number in the story and the findings is one the engine computed: story lines
    against the facts' own values (formatted the engine's way), and every other text field
    against what the engine itself wrote when run directly on the same file
  * the packed zip matches the engine source, holds every module the runs imported, and
    gives the same report as the source tree when run on its own (with `resource` missing)

Self-running like the engine's tests: prints PASS/FAIL per test, exits 1 on any failure.
Python 3.9, pandas and numpy (the engine's own environment); nothing is fetched.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# A failure while building the contract-v2 blocks stops the run under test (in the browser the
# adapter keeps the v1 report and says so instead).
os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ADAPTER_DIR = os.path.abspath(os.environ.get("NL_BROWSER_DIR") or os.path.join(SITE, "engine"))
# NL_ENGINE_ROOT: a frozen copy of northledger-core to run against (while the engine is being edited)
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
SAMPLE = os.path.join(SITE, "engine", "sample-messy.csv")
SAMPLE_AS_OF = "2026-09-15"

sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ADAPTER_DIR)
import nl_browser as NB  # noqa: E402

from northledger import engagement as E  # noqa: E402
from northledger import loop as L  # noqa: E402
from northledger.narrate import story_number  # noqa: E402

# The contract, written out here independently of the adapter's own constants.
CONTRACT = {
    "": ("ok", "error", "engine", "input", "timings", "privacy", "health", "cleaning", "roles",
         "findings", "forecast", "story", "downloads"),
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
ITEMS = {
    ("timings",): ("stage", "seconds"),
    ("privacy", "flagged"): ("column", "kind", "decision"),
    ("cleaning", "fixes"): ("rule", "column", "count", "what"),
    ("cleaning", "quarantine_reasons"): ("reason", "count"),
    ("findings",): ("id", "claim", "verdict", "why", "kind", "value"),
    ("forecast", "series"): ("month", "actual"),
    ("forecast", "forecast"): ("month", "value", "lo", "hi"),
}
STAGES = ["read", "profile", "decide", "clean", "analyze", "forecast", "story"]
VERDICTS = ("RECOMMEND", "WATCH", "INSUFFICIENT")
MONTH = re.compile(r"^\d{4}-\d{2}$")

_CACHE = {}


def _run(data: bytes, name: str, objective: str = "", decisions=None, as_of=None):
    key = (data, name, objective, json.dumps(decisions, sort_keys=True), as_of)
    if key not in _CACHE:
        _CACHE[key] = NB.run(data, name, objective, decisions, as_of)
    return _CACHE[key]


def _sample_bytes() -> bytes:
    with open(SAMPLE, "rb") as fh:
        return fh.read()


def _csv(rows, header, encoding="utf-8") -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode(encoding)


# ------------------------------------------------------------------------ adversarial files
def _email_file() -> bytes:
    """A small shop's orders with a customer email column (every address made up, on the
    reserved example.com domain) and a free-text note that names nobody."""
    import random
    rng = random.Random(7)
    rows = []
    for i in range(900):
        y, m = 2023 + i // 300, (i // 25) % 12 + 1
        rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)),
                     "buyer%04d@example.com" % rng.randint(1, 400),
                     rng.choice(["Tees", "Mugs", "Posters", "Stickers"]),
                     "%.2f" % rng.uniform(5, 80),
                     rng.choice(["gift wrap please", "leave at door", "", "second order this month"])])
    return _csv(rows, ["order_date", "customer_email", "product", "total", "delivery_note"])


def _salary_file() -> bytes:
    """A payroll-like log: a salary column (flagged by its name) holding a few text values
    that the numeric rule quarantines, with the value quoted in the reason."""
    rows = []
    for i in range(240):
        sal = "%d" % (48000 + (i % 17) * 1250)
        if i in (5, 77, 150):
            sal = "pending-review-%d" % (i * 13)
        rows.append(["2024-%02d-%02d" % (i % 12 + 1, i % 27 + 1), "Store %d" % (i % 4 + 1), sal,
                     "%d" % (30 + i % 11)])
    return _csv(rows, ["week_of", "branch", "salary", "hours"])


def _dob_file() -> bytes:
    """A clinic's visits: date_of_birth comes first and is as complete as visit_date."""
    rows = []
    for i in range(700):
        rows.append(["19%02d-%02d-%02d" % (40 + i % 55, i % 12 + 1, i % 28 + 1),
                     "20%02d-%02d-%02d" % (22 + (i // 250), (i // 21) % 12 + 1, i % 28 + 1),
                     ["Physio", "Checkup", "Vaccine"][i % 3], "%d" % (40 + (i * 7) % 90)])
    return _csv(rows, ["date_of_birth", "visit_date", "service", "fee"])


def _latin1_file() -> bytes:
    rows = [["2024-%02d-%02d" % (i % 12 + 1, i % 28 + 1), ["Café Nord", "Crêperie Sud", "Épicerie"][i % 3],
             "%d" % (10 + i % 40)] for i in range(120)]
    return _csv(rows, ["jour", "boutique", "ventes"], encoding="latin-1")


ADVERSARIAL = {
    "one_column.csv": lambda: _csv([["%d" % (i * 3 % 17)] for i in range(50)], ["amount"]),
    "no_date.csv": lambda: _csv([[["North", "South", "East"][i % 3], "%d" % (i % 23), "%.1f" % (i * 1.5)]
                                 for i in range(300)], ["region", "units", "revenue"]),
    "latin1.csv": _latin1_file,
    "twelve_rows.csv": lambda: _csv([["2025-%02d-15" % (i + 1), "%d" % (100 + i * 7)] for i in range(12)],
                                    ["month", "sales"]),
    "orders_with_email.csv": _email_file,
    "payroll.csv": _salary_file,
    "visits.csv": _dob_file,
}


# ------------------------------------------------------------------------ contract checks
def _assert_contract(rep):
    """The v1 contract: every v1 key present, with its v1 type. Contract v2 (CONTRACT-v2.md) adds
    keys at most levels, so v1 keys are checked as a subset; _assert_contract_v2 checks the rest."""
    assert isinstance(rep, dict), type(rep)
    for path, keys in CONTRACT.items():
        obj = rep if not path else rep[path]
        assert isinstance(obj, dict), (path, type(obj))
        assert set(keys) <= set(obj.keys()), \
            "%s lacks v1 keys %s" % (path or "<top>", sorted(set(keys) - set(obj.keys())))
    assert set(rep["forecast"]["backtest"]) == {"mape", "mase", "coverage"}, rep["forecast"]["backtest"]
    for path, keys in ITEMS.items():
        obj = rep
        for p in path:
            obj = obj[p]
        assert isinstance(obj, list), (path, type(obj))
        for it in obj:
            assert set(keys) <= set(it), "%s item lacks v1 keys %s" % (".".join(path), sorted(set(keys) - set(it)))
    assert isinstance(rep["ok"], bool)
    assert (rep["error"] is None) == rep["ok"], (rep["ok"], rep["error"])
    if not rep["ok"]:
        assert isinstance(rep["error"], str) and rep["error"].strip(), rep["error"]
        assert "Traceback" not in rep["error"]
    assert [t["stage"] for t in rep["timings"]] == STAGES, rep["timings"]
    assert all(isinstance(t["seconds"], float) and t["seconds"] >= 0 for t in rep["timings"])
    i = rep["input"]
    assert isinstance(i["name"], str) and isinstance(i["bytes"], int) and isinstance(i["rows"], int)
    assert isinstance(i["columns"], int) and re.fullmatch(r"[0-9a-f]{64}", i["sha256"]), i
    for f in rep["privacy"]["flagged"]:
        assert f["decision"] in ("withhold", "code", "keep") and f["kind"] and f["column"], f
    h = rep["health"]
    assert h["score"] is None or (isinstance(h["score"], float) and 0 <= h["score"] <= 100), h["score"]
    assert all(isinstance(x, str) for x in h["issues"])
    c = rep["cleaning"]
    for k in ("rows_in", "rows_clean", "rows_quarantined"):
        assert isinstance(c[k], int), (k, c[k])
    if rep["ok"]:
        assert c["rows_in"] == c["rows_clean"] + c["rows_quarantined"], c
        assert sum(q["count"] for q in c["quarantine_reasons"]) == c["rows_quarantined"], c
        assert re.fullmatch(r"[0-9a-f]{12}", rep["engine"]["snapshot"]), rep["engine"]
        assert rep["engine"]["version"], rep["engine"]
        assert rep["story"]["headline"].strip(), rep["story"]
    for fx in c["fixes"]:
        assert isinstance(fx["count"], int) and fx["count"] > 0 and fx["what"].strip() and fx["rule"], fx
        assert fx["column"] is None or isinstance(fx["column"], str), fx
    r = rep["roles"]
    assert r["date"] is None or isinstance(r["date"], str)
    assert isinstance(r["measures"], list) and isinstance(r["dimensions"], list) and isinstance(r["excluded"], dict)
    for f in rep["findings"]:
        assert f["verdict"] in VERDICTS, f
        assert f["value"] is None or (isinstance(f["value"], float) and math.isfinite(f["value"])), f
        assert f["claim"].strip() and f["why"].strip() and f["id"] and f["kind"], f
    fc = rep["forecast"]
    assert isinstance(fc["available"], bool) and isinstance(fc["reason"], str)
    if not fc["available"]:
        assert fc["forecast"] == [], fc["forecast"][:1]
        if rep["ok"]:
            assert fc["reason"].strip(), "an unavailable forecast must say why"
    else:
        assert fc["forecast"] and fc["verdict"] in ("RECOMMEND", "WATCH") and fc["champion"], fc
    for p in fc["series"]:
        assert MONTH.match(p["month"]) and isinstance(p["actual"], float), p
    for p in fc["forecast"]:
        assert MONTH.match(p["month"]) and isinstance(p["value"], float), p
        assert p["lo"] is None or isinstance(p["lo"], float)
        assert p["hi"] is None or isinstance(p["hi"], float)
    s = rep["story"]
    assert isinstance(s["headline"], str)
    for k in ("what_happened", "why", "what_to_do", "whats_next", "cannot_answer"):
        assert isinstance(s[k], list) and all(isinstance(x, str) and x.strip() for x in s[k]), (k, s[k])
        assert not any("[fact:" in x for x in s[k]), (k, "citations left in")
    for k in CONTRACT["downloads"]:
        assert isinstance(rep["downloads"][k], str), k
    if rep["downloads"]["ledger_json"]:
        json.loads(rep["downloads"]["ledger_json"])
    json.dumps(rep, allow_nan=False)            # the page parses it with JSON.parse


def _texts(rep):
    """Every text field the page shows (not the downloads)."""
    out = [rep["story"]["headline"], rep["forecast"]["reason"]]
    for k in ("what_happened", "why", "what_to_do", "whats_next", "cannot_answer"):
        out += rep["story"][k]
    for f in rep["findings"]:
        out += [f["claim"], f["why"]]
    out += rep["health"]["issues"]
    out += [x["what"] for x in rep["cleaning"]["fixes"]]
    out += [x["reason"] for x in rep["cleaning"]["quarantine_reasons"]]
    out += [str(v) for v in rep["roles"]["excluded"].values()]
    return [t for t in out if t]


def _strip(rep):
    """A report without what legitimately differs between two runs: the timings, and each
    fact's computed_at clock stamp inside the ledger download."""
    out = json.loads(json.dumps({k: v for k, v in rep.items() if k != "timings"}))
    led = out["downloads"]["ledger_json"]
    if led:
        doc = json.loads(led)
        for key in ("audit_ledger", "analysis_ledger"):
            for f in doc.get(key, []):
                f.pop("computed_at", None)
        out["downloads"]["ledger_json"] = doc
    return out


# ------------------------------------------------------------------------ numbers
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _canon(tok: str) -> str:
    t = tok.replace(",", "").rstrip(".")
    try:
        v = float(t)
    except ValueError:
        return t
    return repr(round(v, 6))


def _numbers(text: str):
    return {_canon(m.group(0)) for m in _NUM.finditer(text or "")}


def _fact_forms(f) -> set:
    """How the engine prints a fact's value, in the story and in the brief."""
    out = set()
    v, unit = f.get("value"), f.get("unit", "")
    if v is None:
        return out
    for signed in (False, True):
        out |= _numbers(story_number(v, unit, signed=signed))
    try:
        fv = float(v)
        out |= _numbers(format(int(fv), ",") if fv.is_integer() else "%.4g" % fv)
        out |= _numbers("%.1f" % fv) | _numbers("%.2f" % fv) | _numbers("%.0f" % fv)
        out |= _numbers(str(int(round(fv))))
    except (TypeError, ValueError):
        pass
    return out


def _engine_direct(data: bytes, name: str, objective: str, as_of, withhold_all=True):
    """The engine's own path run directly (not through the adapter), and everything it wrote. The file is landed
    as the adapter lands it: every flagged column withheld, and a withheld column landed as codes no cleaning
    rule reads (nl_browser._neutralize_withheld: the engine then reads the same table the adapter's run read)."""
    tmp = tempfile.mkdtemp(prefix="nl_direct_")
    try:
        src = os.path.join(tmp, name)
        with open(src, "wb") as fh:
            fh.write(data)
        eng = E.open_engagement(os.path.join(tmp, "e"), create=True)
        E.land(eng, src)
        held = [tc.split(".", 1)[1] for tc in E.intake_state(eng.db_path).pending]
        for c in held:
            E.decide(eng, c, "withhold")
        table = eng.meta()["landings"][-1]["table"]
        NB._neutralize_withheld(eng.db_path, table, held)
        with NB._pinned_clock(as_of):
            a = L.run_loop(eng.db_path, table, objective or NB.DEFAULT_OBJECTIVE,
                           out_dir=os.path.join(tmp, "a"), display_name=name,
                           as_of=as_of or __import__("datetime").date.today().isoformat())
            try:
                r = L.run_analyze(eng.db_path, table, objective or NB.DEFAULT_OBJECTIVE,
                                  out_dir=os.path.join(tmp, "b"), display_name=name,
                                  as_of=as_of or __import__("datetime").date.today().isoformat())
            except E.NotReady:
                r = None
        texts, facts = [], []
        for res in (a, r):
            if res is None:
                continue
            texts.append(res.full_brief.to_markdown())
            texts.append(res.brief.to_exec_markdown())
            texts += list(res.health.findings)
            for g in res.gated:
                facts.append(dict(g.to_dict()))
        if r is not None:
            texts.append(r.story.to_markdown())
            texts += list(r.measure.unmeasured)
        for g in facts:
            texts += [g.get("claim", ""), g.get("gate_reason", ""), g.get("needed_to_upgrade", "")]
            texts += list(g.get("caveats") or [])
        return texts, facts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_numbers(rep, data, name, objective="", as_of=None):
    texts, facts = _engine_direct(data, name, objective, as_of)
    universe = set()
    for t in texts:
        universe |= _numbers(t)
    fact_forms = set()
    claim_nums = set()
    for f in facts:
        fact_forms |= _fact_forms(f)
        claim_nums |= _numbers(f.get("claim", ""))
        for c in f.get("caveats") or []:            # the story quotes a fact's caveats
            claim_nums |= _numbers(c)
    universe |= fact_forms
    # wave 4 (T2, CONTRACT 5.11): an interval's measured coverage, from the benchmark receipt the engine ships, replaces
    # the core's "built to hold" (each finding's effect.coverage; tools/test_nl_inference.py checks it against the
    # receipt itself)
    receipt = set()
    for f in rep["findings"]:
        cov = (f.get("effect") or {}).get("coverage") or {}
        for k in ("measured", "lo", "hi"):
            if cov.get(k) is not None:
                receipt |= _numbers("%.1f" % (100.0 * cov[k]))
        if cov:
            receipt |= _numbers("95")
    universe |= receipt
    # 1. no text field carries a number the engine did not write
    for t in _texts(rep):
        extra = _numbers(t) - universe
        assert not extra, "numbers %s in %r are not in anything the engine wrote" % (sorted(extra), t[:160])
    # 2. the story's own sections: every number is a fact's value, the way the engine prints
    #    it, or part of a fact's label (a month, "12 months"); "80" names the 80% range
    s = rep["story"]
    strict = [s["headline"]] + s["what_happened"] + s["why"] + s["whats_next"]
    for t in strict:
        extra = _numbers(t) - fact_forms - claim_nums - receipt - {_canon("80")}
        assert not extra, "story numbers %s in %r are not fact values" % (sorted(extra), t[:160])
    # 3. a finding's claim is the fact's own claim, and its value is the fact's value
    by_id = {f["id"]: f for f in facts}
    for f in rep["findings"]:
        src = by_id.get(f["id"])
        assert src is not None, "finding %s is not an engine fact" % f["id"]
        assert f["claim"] == NB._plain(src["claim"]), (f["claim"], src["claim"])
        assert f["verdict"] == src["verdict"], (f["id"], f["verdict"], src["verdict"])
        if f["value"] is not None:
            assert abs(f["value"] - float(src["value"])) < 1e-9 * max(1.0, abs(f["value"])), f


# ------------------------------------------------------------------------ privacy
def _all_output_text(rep) -> str:
    d = dict(rep)
    return json.dumps(d, ensure_ascii=False)


def _landed_values(data: bytes, name: str, column: str):
    """The column's values exactly as the engine landed them (pseudonyms, if it coded them)."""
    tmp = tempfile.mkdtemp(prefix="nl_landed_")
    try:
        src = os.path.join(tmp, name)
        with open(src, "wb") as fh:
            fh.write(data)
        eng = E.open_engagement(os.path.join(tmp, "e"), create=True)
        res = E.land(eng, src)
        import sqlite3
        con = sqlite3.connect(eng.db_path)
        vals = [r[0] for r in con.execute('SELECT DISTINCT "%s" FROM "%s" WHERE "%s" IS NOT NULL'
                                          % (column, res.table, column))]
        con.close()
        return [str(v) for v in vals]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _csv_header(text: str):
    return next(csv.reader(io.StringIO(text))) if text else []


def _assert_withheld(rep, column: str, values):
    """`column` and every one of `values` are absent from everything the page shows or offers."""
    flagged = {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}
    assert flagged.get(column) == "withhold", (column, flagged)
    for k in ("clean_csv", "quarantine_csv"):
        assert column not in _csv_header(rep["downloads"][k]), (k, _csv_header(rep["downloads"][k]))
    blob = _all_output_text(rep)
    vals = [v for v in values if len(v.strip()) >= 4]
    leaked = [v for v in vals if v in blob]
    assert not leaked, "withheld %s values leaked: %s" % (column, leaked[:5])
    assert column not in rep["roles"]["dimensions"] and column not in rep["roles"]["measures"]


# ======================================================================== tests
def test_sample_is_synthetic_deterministic_and_the_right_size():
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_sample_messy.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = list(csv.reader(io.StringIO(_sample_bytes().decode("utf-8"))))[1:]
    assert 3000 <= len(rows) <= 5000, len(rows)
    blob = _sample_bytes().decode("utf-8")
    assert "@" not in blob and not re.search(r"\(?\d{3}\)?[ -]\d{3}-\d{4}", blob), "personal-looking data"


def test_contract_shape_on_the_sample():
    rep = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    assert rep["ok"], rep["error"]
    _assert_contract(rep)
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    assert rep["engine"]["snapshot"] == pack["engine_snapshot"] == L.engine_snapshot()["id"][:12], \
        (rep["engine"], pack["engine_snapshot"])
    assert rep["input"]["rows"] == rep["cleaning"]["rows_in"], (rep["input"], rep["cleaning"]["rows_in"])


def test_sample_shows_what_the_demo_promises():
    rep = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    assert rep["ok"], rep["error"]
    c = rep["cleaning"]
    assert c["rows_quarantined"] > 0 and len(c["fixes"]) >= 4, c
    assert any("duplicate" in q["reason"] for q in c["quarantine_reasons"]), c["quarantine_reasons"]
    v = [f["verdict"] for f in rep["findings"]]
    assert "RECOMMEND" in v and "WATCH" in v, v
    assert any(f["kind"] == "data_quality" for f in rep["findings"])
    assert any(f["kind"] == "business" for f in rep["findings"])
    fc = rep["forecast"]
    assert fc["available"] and len(fc["series"]) >= 30 and len(fc["forecast"]) >= 1, \
        (fc["available"], fc["reason"], len(fc["series"]))
    assert fc["backtest"]["mape"] is not None and fc["backtest"]["coverage"] is not None, fc["backtest"]
    assert rep["roles"]["date"] and rep["roles"]["measures"], rep["roles"]
    assert [f["column"] for f in rep["privacy"]["flagged"]] == ["notes"], rep["privacy"]
    assert "Notes" not in _csv_header(rep["downloads"]["clean_csv"])


def test_sample_report_is_repeatable_with_a_pinned_date():
    a = NB.run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    b = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    for rep in (a, b):
        rep = dict(rep)
    assert _strip(a) == _strip(b), "two runs of the sample on the same analysis date differ"
    assert any("2026-09-15" in i for i in a["health"]["issues"]), \
        "the profiler did not use the pinned analysis date"


def test_numbers_in_story_and_findings_come_from_the_engine():
    for data, name, as_of in ((_sample_bytes(), "sample-messy.csv", SAMPLE_AS_OF),
                              (_email_file(), "orders_with_email.csv", "2026-01-10"),
                              (_dob_file(), "visits.csv", "2024-12-10")):
        rep = _run(data, name, "", None, as_of)
        assert rep["ok"], (name, rep["error"])
        _check_numbers(rep, data, name, "", as_of)


def test_refusals_are_plain_and_never_sample():
    cases = {
        "empty": (b"", "empty.csv", "empty"),
        "blank": (b"   \n\n", "blank.csv", "empty"),
        "header only": (b"date,amount,category\n", "header.csv", "zero rows"),
        "spreadsheet": (b"PK\x03\x04 not really a workbook", "book.xlsx", "CSV"),
        "too big": (b"a,b\n" + b"1,2\n" * ((25 * 1024 * 1024) // 4 + 10), "big.csv", "25 MB"),
        "too many rows": (b"n\n" + b"".join(b"%d\n" % i for i in range(200001)), "rows.csv", "200,000"),
        "lines the reader skips": (b"a,b,c\n1,2,3\n4,5,6,7\n8,9,10\n", "ragged.csv", "more fields"),
    }
    for label, (data, name, needle) in cases.items():
        rep = NB.run(data, name, "")
        _assert_contract(rep)
        assert rep["ok"] is False, (label, "should be refused")
        assert needle.lower() in rep["error"].lower(), (label, rep["error"])
        assert rep["findings"] == [] and rep["downloads"]["clean_csv"] == "", label
    # exactly at the limit: read, not refused (distinct rows, so no duplicate guard)
    ok = NB.run(b"n,v\n" + b"".join(b"%d,%d\n" % (i, i % 97) for i in range(200000)), "rows.csv", "")
    _assert_contract(ok)
    assert ok["ok"] and ok["input"]["rows"] == 200000, (ok["error"], ok["input"])
    # a file that is nearly all repeated rows is refused before the engine's duplicate check
    many = NB.run(b"n\n" + b"1\n" * 20000, "dupes.csv", "")
    _assert_contract(many)
    assert many["ok"] is False and "duplicate" in many["error"], many["error"]
    few = NB.run(b"n\n" + b"1\n" * 300, "dupes.csv", "")
    assert few["ok"], few["error"]


def test_never_raises_on_garbage():
    weird = [
        (None, "x.csv", None, None),
        (b"\x00\xff\xfe\x00" * 500, "binary.csv", "", None),
        (b'a,b\n1,"unterminated\n2,3\n', "ragged.csv", "", None),
        (b"a;b;c\n1;2\n3;4;5;6\n", "semi.csv", "", "not-a-dict"),
        (_sample_bytes(), "sample.csv", "x" * 5000, {"notes": "delete-it"}),
        (_sample_bytes(), "sample.csv", "", None, ),
    ]
    for args in weird:
        data, name, objective, decisions = (list(args) + [None] * 4)[:4]
        rep = NB.run(data, name, objective, decisions if isinstance(decisions, dict) else None)
        _assert_contract(rep)
    rep = NB.run(_sample_bytes(), "s.csv", "", None, as_of="15/09/2026")
    _assert_contract(rep)
    assert rep["ok"] is False and "YYYY-MM-DD" in rep["error"], rep["error"]
    text = NB.run_json(_sample_bytes(), "s.csv", "", "{not json", SAMPLE_AS_OF)
    assert json.loads(text)["ok"] is True


def test_adversarial_files_run_or_refuse_plainly():
    for name, make in ADVERSARIAL.items():
        data = make()
        rep = _run(data, name, "", None, None)
        _assert_contract(rep)
        assert rep["ok"], (name, rep["error"])
        assert rep["input"]["rows"] > 0 and rep["cleaning"]["rows_in"] == rep["input"]["rows"], (name, rep["input"])
        if name in ("one_column.csv", "no_date.csv"):
            assert rep["roles"]["date"] is None and not rep["forecast"]["available"], (name, rep["roles"])
            assert rep["forecast"]["reason"], name
        if name == "twelve_rows.csv":
            assert not rep["forecast"]["available"] and rep["forecast"]["reason"], rep["forecast"]
        if name == "latin1.csv":
            assert "Café Nord" in rep["downloads"]["clean_csv"], rep["downloads"]["clean_csv"][:200]
            assert "Ã" not in rep["downloads"]["clean_csv"], "latin-1 text was mis-decoded"


def test_email_column_is_withheld_everywhere_by_default():
    data = _email_file()
    rep = _run(data, "orders_with_email.csv", "", None, "2026-01-10")
    assert rep["ok"], rep["error"]
    raw = sorted(set(re.findall(r"buyer\d{4}@example\.com", data.decode())))
    coded = _landed_values(data, "orders_with_email.csv", "customer_email")
    assert raw and coded and not set(raw) & set(coded), "the engine did not code the emails at landing"
    _assert_withheld(rep, "customer_email", raw + coded)
    notes = [v for v in ("gift wrap please", "leave at door", "second order this month")]
    _assert_withheld(rep, "delivery_note", notes)
    kinds = {f["column"]: f["kind"] for f in rep["privacy"]["flagged"]}
    assert "email" in kinds["customer_email"], kinds


def test_visitor_decisions_are_respected():
    data = _email_file()
    rep = _run(data, "orders_with_email.csv", "", {"customer_email": "keep", "delivery_note": "keep"},
               "2026-01-10")
    assert rep["ok"], rep["error"]
    dec = {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}
    # coded at landing: it cannot be un-coded, so "keep" leaves it coded
    assert dec == {"customer_email": "code", "delivery_note": "keep"}, dec
    hdr = _csv_header(rep["downloads"]["clean_csv"])
    assert "customer_email" in hdr and "delivery_note" in hdr, hdr
    raw = set(re.findall(r"buyer\d{4}@example\.com", data.decode()))
    assert not any(v in rep["downloads"]["clean_csv"] for v in raw), "raw emails reached the download"
    assert "leave at door" in rep["downloads"]["clean_csv"]
    rep2 = _run(data, "orders_with_email.csv", "", {"Customer_Email": "withhold", "delivery_note": "CODE"},
                "2026-01-10")
    dec2 = {f["column"]: f["decision"] for f in rep2["privacy"]["flagged"]}
    assert dec2["delivery_note"] == "code" and dec2["customer_email"] == "withhold", dec2
    assert "leave at door" not in _all_output_text(rep2)


def test_withheld_values_never_reach_a_quarantine_reason():
    data = _salary_file()
    rep = _run(data, "payroll.csv", "", None, "2025-01-15")
    assert rep["ok"], rep["error"]
    bad = ["pending-review-65", "pending-review-1001", "pending-review-1950"]
    salaries = _landed_values(data, "payroll.csv", "salary")
    _assert_withheld(rep, "salary", bad)
    q = rep["downloads"]["quarantine_csv"]
    assert "salary" not in _csv_header(q)
    assert not any(s in q for s in salaries if not s.isdigit()), "a withheld salary value is in a quarantine reason"


def test_a_withheld_date_column_never_becomes_the_story():
    data = _dob_file()
    rep = _run(data, "visits.csv", "", None, "2024-12-10")
    assert rep["ok"], rep["error"]
    dobs = _landed_values(data, "visits.csv", "date_of_birth")
    _assert_withheld(rep, "date_of_birth", dobs)
    assert rep["roles"]["date"] != "date_of_birth", rep["roles"]
    # an age in days gives the birth date back as surely as the date itself
    assert not any("date_of_birth" in i and "days old" in i for i in rep["health"]["issues"]), \
        rep["health"]["issues"]
    for p in rep["forecast"]["series"]:
        assert p["month"] >= "2020-01", "the series runs over birth months: %s" % p["month"]


def test_scrubber_finds_whole_values_only():
    s = NB.Scrubber(["Maple Plumbing Ltd", "x@example.com", "52000", "ok", "Leaf cleanup"])
    assert s.clean("vendor 'Maple Plumbing Ltd' is late") == "vendor '[withheld]' is late"
    assert s.clean("mail x@example.com, then") == "mail [withheld], then"
    assert s.clean("LEAF  CLEANUP.") == "[withheld]."
    assert s.clean("a total of 52000 and ok") == "a total of 52000 and ok"   # numbers, short values kept
    assert s.clean("Maple Plumbing") == "Maple Plumbing"                     # a part is not the value
    assert not NB.Scrubber([]) and s


def test_row_counter_counts_records_not_lines():
    data = b'a,b\n1,"two\nlines"\n\n3,4\n'
    assert NB._count_rows(data) == 2


def test_pack_matches_the_engine_and_holds_every_module_used():
    r = subprocess.run([sys.executable, os.path.join(HERE, "pack_engine.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, os.path.join(HERE, "pack_engine.py"), "--imports"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    packed = {f["path"] for f in pack["files"]}
    with zipfile.ZipFile(os.path.join(SITE, "engine", pack["zip"]["file"])) as z:
        assert set(z.namelist()) == packed | {"nl_pack.json"}, sorted(set(z.namelist()) ^ packed)
        stamp = json.loads(z.read("nl_pack.json"))
    assert stamp["engine_snapshot"] == L.engine_snapshot()["id"][:12]
    used = {m for m in sys.modules if m == "northledger" or m.startswith("northledger.")}
    need = {"northledger/__init__.py" if m == "northledger" else "northledger/%s.py" % m.split(".", 1)[1]
            for m in used}
    missing = need - packed
    assert not missing, "the runs above imported engine modules the pack leaves out: %s" % sorted(missing)
    assert not any(p.endswith(("report.py", "analyst.py", "tmdl_check.py", "powerbi.py")) for p in packed)


def test_pack_carries_the_benchmark_receipt_the_packed_gate_reads():
    """R1's gate quotes a measured false-confirm rate beside a confirmed change, read from
    northledger/benchmark_receipt.json beside gate.py. The zip must carry that file byte for
    byte, and the gate unpacked from the zip alone must accept it (its change-path snapshot is
    computed over the packed modules), or the browser says "no benchmark receipt ships with
    this engine" where the native engine quotes a rate."""
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    engine_dir = os.path.dirname(os.path.abspath(L.__file__))
    with open(os.path.join(engine_dir, "benchmark_receipt.json"), "rb") as fh:
        want = fh.read()
    tmp = tempfile.mkdtemp(prefix="nl_rcpt_")
    try:
        with zipfile.ZipFile(os.path.join(SITE, "engine", pack["zip"]["file"])) as z:
            assert "northledger/benchmark_receipt.json" in z.namelist(), \
                "the pack leaves out northledger/benchmark_receipt.json"
            assert z.read("northledger/benchmark_receipt.json") == want, \
                "the packed receipt differs from the engine's"
            z.extractall(tmp)
        code = ("import sys\n"
                "sys.path[:] = [p for p in sys.path if 'northledger-core' not in p and 'portfolio-website' not in p]\n"
                "sys.path.insert(0, %r)\n"
                "from northledger import gate\n"
                "rec, why = gate.load_benchmark_receipt()\n"
                "assert gate.__file__.startswith(%r), gate.__file__\n"
                "print('OK' if rec else 'NONE: ' + why)\n" % (tmp, tmp))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp)
        assert p.returncode == 0 and p.stdout.strip() == "OK", p.stdout + p.stderr[-800:]
    finally:
        shutil.rmtree(tmp, True)


def _zip_run_text(edit=None) -> str:
    """The sample run from the packed zip alone (source tree hidden, as in the browser), after
    edit(tmp) changes the unpacked copy; the report as JSON text."""
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="nl_cov_")
    try:
        with zipfile.ZipFile(os.path.join(SITE, "engine", pack["zip"]["file"])) as z:
            z.extractall(tmp)
        if edit:
            edit(tmp)
        out = os.path.join(tmp, "report.json")
        code = ("import sys, json\n"
                "sys.path[:] = [p for p in sys.path if 'northledger-core' not in p and 'portfolio-website' not in p]\n"
                "sys.path.insert(0, %r)\n"
                "import nl_browser\n"
                "rep = nl_browser.run(open(%r, 'rb').read(), 'sample-messy.csv', '', None, %r)\n"
                "json.dump(rep, open(%r, 'w'))\n" % (tmp, SAMPLE, SAMPLE_AS_OF, out))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp)
        assert r.returncode == 0, r.stderr[-1500:]
        with open(out, encoding="utf-8") as fh:
            return fh.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_NOT_PUBLISHED = "measured coverage not yet published"
_MEASURED = re.compile(r"\(measured: [^)]*\)")


def test_packed_forecast_quotes_measured_coverage_only_for_its_own_code():
    """The forecast caveat quotes the 80% ranges' measured coverage from the packed receipt
    exactly as the source tree does, and not when the stamp's decision-code id does not match
    the receipt (a stale receipt) or a packed engine file differs from its stamp."""
    src = json.dumps(_run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF))
    want = _MEASURED.findall(src)
    assert want, "the source tree run quotes no measured coverage; nothing to compare"
    assert _MEASURED.findall(_zip_run_text()) == want

    def stale(tmp):
        p = os.path.join(tmp, "nl_pack.json")
        st = json.load(open(p, encoding="utf-8"))
        st["decision_code_snapshot"] = "0" * 64
        json.dump(st, open(p, "w", encoding="utf-8"))

    def tampered(tmp):
        with open(os.path.join(tmp, "northledger", "vocab.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# edited after packing\n")

    for edit in (stale, tampered):
        txt = _zip_run_text(edit)
        assert not _MEASURED.findall(txt) and _NOT_PUBLISHED in txt, edit.__name__


def test_the_packed_zip_alone_gives_the_same_report():
    """Unzip into an empty folder, hide the source tree and the real `resource` module, and run
    the sample: the report must equal the source tree's (timings aside)."""
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="nl_zip_")
    try:
        with zipfile.ZipFile(os.path.join(SITE, "engine", pack["zip"]["file"])) as z:
            z.extractall(tmp)
        out = os.path.join(tmp, "report.json")
        code = (
            "import sys, json\n"
            "sys.path[:] = [p for p in sys.path if 'northledger-core' not in p and 'portfolio-website' not in p]\n"
            "sys.path.insert(0, %r)\n"
            "sys.modules['resource'] = None\n"            # as in a browser build without it
            "import nl_browser\n"
            "rep = nl_browser.run(open(%r, 'rb').read(), 'sample-messy.csv', '', None, %r)\n"
            "import northledger\n"
            "assert northledger.__file__.startswith(%r), northledger.__file__\n"
            "assert getattr(sys.modules['resource'], 'IS_STUB', False)\n"
            "json.dump(rep, open(%r, 'w'))\n" % (tmp, SAMPLE, SAMPLE_AS_OF, tmp, out))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp)
        assert r.returncode == 0, r.stderr[-1500:]
        with open(out, encoding="utf-8") as fh:
            zrep = json.load(fh)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    src = json.loads(json.dumps(_run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)))
    assert _strip(zrep) == _strip(src), "the packed engine and the source tree disagree"


def test_no_temporary_files_are_left_behind():
    # in a temp folder of this test's own (final review, 29 Sep 2026: another process's nl_browser_ folder in the
    # shared temp folder failed this test); the adapter's folders (nl_browser_ for a run, nl_profile_ for a
    # profile pass) are made with tempfile's default folder, which this test points at its own
    own = tempfile.mkdtemp(prefix="nl_test_tmp_")
    saved = tempfile.tempdir
    try:
        tempfile.tempdir = own
        NB.run(_email_file(), "orders_with_email.csv", "", None, "2026-01-10")
        NB.run(b"", "e.csv", "")
        NB.profile_for_ai(_email_file(), "orders_with_email.csv", decisions={"customer_email": "code"})
        left = sorted(os.listdir(own))
    finally:
        tempfile.tempdir = saved
        shutil.rmtree(own, ignore_errors=True)
    assert not left, left


# ------------------------------------------------------------------------ what a visitor reads
_PHONE = re.compile(r"(?<!\d)\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")


def _tidy_file() -> bytes:
    """A clean export: ISO dates, plain decimals, one spelling per category."""
    rows = [["2024-%02d-%02d" % (i % 12 + 1, i % 28 + 1), ["North", "South", "East"][i % 3],
             "%.2f" % (20 + (i * 7) % 90), "%d" % (1 + i % 9)] for i in range(600)]
    return _csv(rows, ["order_date", "region", "amount", "units"])


def _mixed_dates_file(tbd_every: int = 0) -> bytes:
    """A rent roll: date_paid mixes day-first (25/11/2025) and month-first (11/25/2025) dates, so
    03/11/2025 cannot be read without guessing; one note spans two lines and a blank line follows
    the header. With tbd_every, that many rows in one have 'TBD' as the amount paid."""
    rows = []
    for i in range(300):
        d, m = i % 28 + 1, i % 12 + 1
        paid = ("%02d/%02d/2025" % (d, m) if i % 3 == 0 else "%02d/%02d/2025" % (m, d) if i % 3 == 1
                else "2025-%02d-%02d" % (m, d))
        amount = "TBD" if tbd_every and i % tbd_every == 0 else "%d" % (1500 + (i % 7) * 50)
        rows.append(["Pape Ave %d" % (i % 3 + 1), "%d" % (100 + i % 12), "2025-%02d-01" % m, amount, paid,
                     "called twice\nno answer" if i == 7 else ""])
    text = _csv(rows, ["building", "unit", "rent_due", "amount_paid", "date_paid", "notes"]).decode("utf-8")
    head, rest = text.split("\n", 1)
    return (head + "\n\n" + rest).encode("utf-8")


def test_type_conversions_are_not_reported_as_repairs():
    rep = _run(_tidy_file(), "tidy.csv", "", None, "2025-01-20")
    assert rep["ok"], rep["error"]
    whats = [f["what"] for f in rep["cleaning"]["fixes"]]
    assert whats, rep["cleaning"]
    for w in whats:
        assert "currency signs" not in w and "different formats" not in w, w
    assert any(w.startswith("Read as") for w in whats), whats


def test_a_title_row_above_the_header_is_refused_plainly():
    body = _csv([["2025-%02d-01" % (i % 12 + 1), "Unit %d" % (i % 9 + 1), "%d" % (1500 + i % 5 * 25)] for i in range(80)],
                ["rent_due", "unit", "amount"])
    data = b"Rent Roll - 118 Dundas St W - printed Sept 2025\n\n" + body
    rep = NB.run(data, "rent-roll-with-title.csv", "")
    _assert_contract(rep)
    assert rep["ok"] is False and "title" in rep["error"] and "column names" in rep["error"], rep["error"]
    assert NB.run(body, "rent-roll.csv", "")["ok"], "the same file without its title was refused"


def test_phone_numbers_and_emails_never_reach_a_text_field():
    t = NB.public_text("The most common ref value is '416-555-1000', with 8.8% of rows; mail jo@example.com.",
                       "client_x", "x.csv")
    assert not _PHONE.search(t) and "@" not in t and "8.8%" in t, t
    rows = [["2025-%02d-%02d" % (i % 12 + 1, i % 28 + 1), "%d" % (40 + i % 30), "416-555-%04d" % (1000 + i % 6)]
            for i in range(400)]
    data = _csv(rows, ["visit", "fee", "ref"])
    rep = _run(data, "visits-ref.csv", "", None, "2025-12-20")
    assert rep["ok"], rep["error"]
    for text in _texts(rep):
        assert not _PHONE.search(text), text


def test_dates_that_could_be_either_order_are_named_as_such():
    rep = _run(_mixed_dates_file(), "rent-roll.csv", "", None, "2025-12-20")
    assert rep["ok"], rep["error"]
    reasons = {q["reason"]: q["count"] for q in rep["cleaning"]["quarantine_reasons"]}
    amb = [k for k in reasons if "could be day/month or month/day" in k]
    assert amb, reasons
    assert not any("date_paid" in k and "matches no known date format" in k for k in reasons), reasons
    q = rep["downloads"]["quarantine_csv"]
    n_amb = sum(1 for r in csv.DictReader(io.StringIO(q)) if "could be day/month" in r["_quarantine_reason"])
    assert sum(reasons[k] for k in amb) == n_amb, (reasons, n_amb)
    for text in _texts(rep):                    # the story says it the same way as the table
        assert "date_paid_date: value matches no known date format" not in text, text


def test_a_tripped_cleaning_gate_is_said_once_with_no_internal_names():
    rep = _run(_mixed_dates_file(tbd_every=3), "rent-roll.csv", "", None, "2025-12-20")
    assert rep["ok"], rep["error"]
    s = rep["story"]
    assert s["headline"].startswith(NB.GATE_TRIPPED), s["headline"]
    assert s["headline"] not in s["cannot_answer"], "the headline is repeated under What we cannot answer"
    assert rep["forecast"]["reason"] != s["headline"] and len(rep["forecast"]["reason"]) < 90, rep["forecast"]["reason"]
    for text in _texts(rep):
        assert "client_" not in text, text


def test_downloads_carry_the_source_line_and_whole_numbers():
    data = _mixed_dates_file()
    rep = _run(data, "rent-roll.csv", "", None, "2025-12-20")
    assert rep["ok"], rep["error"]
    lines = data.decode("utf-8").split("\n")
    for key in ("clean_csv", "quarantine_csv"):
        rows = list(csv.DictReader(io.StringIO(rep["downloads"][key])))
        assert rows and "source_line" in rows[0], (key, list(rows[0]) if rows else None)
        for r in rows:
            n = int(r["source_line"])
            src = next(csv.reader(io.StringIO("\n".join(lines[n - 1:]))))
            if key == "quarantine_csv":
                assert src[0] == r["building"] and src[4] == r["date_paid"], (n, src, r)
            else:
                assert src[1] == r["unit"], (n, src, r)
                assert not r["unit"].endswith(".0") and not r["amount_paid"].endswith(".0"), r


def test_a_row_of_empty_cells_keeps_the_source_lines():
    lines = _mixed_dates_file().decode("utf-8").split("\n")
    data = "\n".join(lines[:12] + [",,,,,"] + lines[12:]).encode("utf-8")
    rep = _run(data, "rent-roll-gap.csv", "", None, "2025-12-20")
    assert rep["ok"], rep["error"]
    rows = list(csv.DictReader(io.StringIO(rep["downloads"]["quarantine_csv"])))
    assert rows and "source_line" in rows[0], "no source_line once a row of empty cells is in the file"
    text = data.decode("utf-8").split("\n")
    for r in rows:
        src = next(csv.reader(io.StringIO("\n".join(text[int(r["source_line"]) - 1:]))))
        assert src[0] == r["building"] and src[4] == r["date_paid"], (r["source_line"], src, r)


def test_columns_coded_on_arrival_say_so():
    rep = _run(_email_file(), "orders_with_email.csv", "", None, "2026-01-10")
    kinds = {f["column"]: f["kind"] for f in rep["privacy"]["flagged"]}
    assert NB.CODED_ON_ARRIVAL in kinds["customer_email"], kinds
    assert NB.CODED_ON_ARRIVAL not in kinds["delivery_note"], kinds


def test_business_findings_come_first_within_each_verdict():
    rep = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    kinds = {"business": 0, "forecast": 1, "data_quality": 2}
    key = [(VERDICTS.index(f["verdict"]), kinds.get(f["kind"], 3)) for f in rep["findings"]]
    assert key == sorted(key), [(f["verdict"], f["kind"]) for f in rep["findings"]][:12]
    assert rep["findings"][0]["kind"] != "data_quality" or not any(
        f["kind"] != "data_quality" and f["verdict"] == rep["findings"][0]["verdict"] for f in rep["findings"])


def test_pack_states_the_engine_thresholds_the_page_quotes():
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    from northledger.forecast import ForecastPolicy, min_history_months
    from northledger.gate import GatePolicy
    assert pack.get("thresholds") == {"forecast_min_history_months": ForecastPolicy().min_history_months,
                                      "forecast_min_checkable_months": min_history_months(),
                                      "forecast_replay_months": ForecastPolicy().min_holdout,
                                      "recommend_min_rows": GatePolicy().min_rows_recommend}, pack.get("thresholds")


def _monthly_sales(months: int, lift_at=(), seed: int = 7) -> bytes:
    """`months` whole months of about 300 sales a month, ending Aug 2026 (the month before
    V2_AS_OF), flat apart from a 18% lift in the months at `lift_at` (counted from the start)."""
    import random as _random
    rng = _random.Random(seed)
    rows = []
    for k in range(months):
        idx = 2026 * 12 + 7 - (months - 1) + k           # month index, Aug 2026 last
        y, m = idx // 12, idx % 12 + 1
        lift = 1.18 if k in lift_at else 1.0
        for _ in range(int(round(300 * math.exp(rng.gauss(0, 0.02))))):
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), rng.choice(["King St", "Queen St"]),
                         "%.2f" % (24.0 * lift * rng.uniform(0.7, 1.3))])
    return _csv(rows, ["sold_on", "shop", "net_sales"])


def test_v2_the_page_promises_the_months_a_forecast_really_needs():
    """Review, 25 Sep 2026: the page said a forecast needs about 36 months, and a 38-month file got
    none (only 5 months could be replayed). The page now quotes forecast.min_history_months(): the
    training months, the errors a first range needs and the 12 replayed months. One month short of
    it the engine declines for the replay; at it, the replay is long enough."""
    from northledger.forecast import min_history_months
    need = min_history_months()
    short = _run(_monthly_sales(need - 1), "short.csv", "", None, V2_AS_OF)
    enough = _run(_monthly_sales(need), "enough.csv", "", None, V2_AS_OF)
    f_short = _find(short, "forecast.total_net_sales.next")
    f_enough = _find(enough, "forecast.total_net_sales.next")
    assert f_short["grade"] == "NOT_ENOUGH_DATA" and "replayed over" in (f_short.get("why") or ""), f_short.get("why")
    assert "replayed over" not in (f_enough.get("why") or "") and "months of history" not in (f_enough.get("why") or ""), \
        f_enough.get("why")
    with open(os.path.join(SITE, "index.html"), encoding="utf-8") as fh:
        page = re.sub(r"<[^>]+>", "", fh.read())
    m = re.search(r"a forecast needs at least ([\d,]+) months of monthly history", page)
    assert m and int(m.group(1).replace(",", "")) == need, (m and m.group(0), need)


def test_v2_a_peak_in_the_bottom_line_says_it_was_found_by_looking():
    """Review, 25 Sep 2026: a flat cafe file's Manager bottom line said 'it peaked in the 3 months
    to Jan 2026 and is down 10.6% since' with no label, while the Analyst view called the same
    figure a description found by looking. The Manager line carries the same qualifier."""
    rep = _run(_monthly_sales(38, lift_at=range(26, 30)), "peak.csv", "", None, V2_AS_OF)
    moved = [l for l in rep["summary"]["lines"] if l["kind"] == "moved"]
    assert moved, rep["summary"]["lines"]
    t = moved[0]["text"]
    assert "measure.net_sales.total.from_peak" in moved[0]["finding_ids"] and "peaked in the 3 months to" in t, t
    assert "found by looking, not a tested change" in t, t


# ======================================================================== contract v2
# engine/CONTRACT-v2.md, written out here independently of the adapter's own constants.
V2_TOP = ("contract_version", "primary_metric", "tests_run", "methods", "limitations",
          "reproducibility", "charts", "charts_suppressed", "llm")
V2_ENGINE = ("semver", "decision_code_snapshot", "environment", "restated_since_previous", "benchmark")
V2_BENCH = ("receipt", "snapshot", "available", "note", "matched_cell", "worst_cell",
            "forecast_coverage_80", "placebo_real", "power_at_bar_matched", "cross_env", "not_measured")
V2_HEALTH = ("score_min", "score_mean", "weakest", "dimensions", "sample", "columns", "missingness",
             "accuracy")
V2_HEALTH_COL = ("name", "type", "n", "flagged", "withheld", "completeness", "validity", "uniqueness",
                 "weakest", "claim_health", "distinct", "top_values", "numeric", "dates",
                 "quarantined_by_rule", "fixes_by_rule", "safe_for")
V2_CLEANING = ("quarantine_by_month", "rules")
V2_FINDING = ("grade", "role", "parent_id", "estimand", "unit", "effect", "test", "health", "checks",
              "watch", "error_rate", "error_rate_note", "drivers", "composition", "posterior", "power",
              "needed_to_upgrade", "columns_read", "verified", "tested_times", "chart_ids", "trace")
V2_EFFECT = ("estimate", "ci", "level", "ci_fcr", "fcr_level", "method", "scale")
V2_TEST = ("name", "method", "ran", "not_run_reason", "null", "bar", "n_months", "n_rows", "phi_hat", "B",
           "statistic", "statistic_name", "df", "p", "p_mc_interval", "p_point_null", "q", "family",
           "family_size", "fdr_method", "family_line")
V2_FORECAST = ("band", "coverage", "baseline_test", "models", "break", "interventions",
               "forecastability", "decision_edge")
V2_MODEL = ("name", "label", "mase", "mape", "mape_suppressed", "msis", "skill_vs_sn", "mae", "coverage",
            "dm", "champion", "baseline", "applicable")
V2_REPRO = ("input_sha256", "engine_snapshot", "decision_code_snapshot", "environment", "parameters",
            "seeds", "B", "figures_reproduced")
V2_CHART = ("id", "rule", "type", "title", "view", "default_visible", "finding_ids", "why_shown", "source",
            "data")
GRADE_OF = {"RECOMMEND": "CONFIRMED", "WATCH": "WATCH", "INSUFFICIENT": "NOT_ENOUGH_DATA"}
SECTION5_RULES = ("#1", "#2", "#2b", "#3", "#4", "#5", "#6", "#7", "#8", "#9", "#10", "#11", "#12", "#13", "#14")
V2_AS_OF = "2026-09-15"


def _three_measures_file() -> bytes:
    """About 15,400 sales rows over 48 months: price rises 30% in the latest 12 months (the engine
    confirms it, so a CONFIRMED claim with its selection-adjusted bound and its measured
    false-confirm rate is in the report), three measures (the correlation rule fires), a
    discount column about 6% empty (the missingness rule fires) and a channel column about 3%
    empty (so empty cells in two columns can be correlated), two small dimensions. In one
    month (2024-06) discount is almost all empty, under the engine's rows-a-month floor, so its
    monthly average is a gap; and 40 rows fall in 2026-09, the month of the analysis date, which
    the engine leaves out of its window as incomplete (so the window is not simply every row)."""
    import random as _random
    rng = _random.Random(11)
    rows = []
    for k in range(49):
        y, m = 2022 + (k + 8) // 12, (k + 8) % 12 + 1
        base = 320 * (1.0 + 0.12 * math.sin(2 * math.pi * m / 12.0))
        n = int(round(base * math.exp(rng.gauss(0, 0.04)))) if k < 48 else 40
        for i in range(n):
            price = rng.uniform(20, 60) * (1.3 if k >= 36 else 1.0)
            qty = max(1, int(round(rng.gauss(3 + price / 30.0, 1))))
            disc = "" if rng.random() < 0.06 or (k == 21 and i >= 3) else \
                "%.2f" % (price * qty * rng.uniform(0, 0.15))
            channel = "" if rng.random() < 0.03 else rng.choice(["web", "store", "phone"])
            day = "2026-09-%02d" % rng.randint(1, 14) if k == 48 else "%04d-%02d-%02d" % (y, m, rng.randint(1, 28))
            rows.append([day, rng.choice(["North", "South", "East", "West"]), channel, "%.2f" % price, "%d" % qty, disc])
    return _csv(rows, ["sold_on", "region", "channel", "price", "qty", "discount"])


def _margins_file() -> bytes:
    """36 months of about 150-190 sales rows a month (review of the site, 24 Sep): net_margin's
    monthly averages are below zero (-4.1 before, -2.6 in the latest 12 months), so its change has
    no percentage; fee falls a true 3% with little noise (its interval sits wholly below zero but
    inside the 5% bar: moved, size not yet shown); wait_minutes does not move; volume rises 25% on
    fewer than 200 rows a month (held at WATCH by rule). 36 months is too short for the forecast
    to be offered."""
    import random as _random
    rng = _random.Random(29)
    rows = []
    for k in range(36):
        y, m = 2023 + (k + 8) // 12, (k + 8) % 12 + 1
        n = int(round(150 * (1.25 if k >= 24 else 1.0) * math.exp(rng.gauss(0, 0.05))))
        for i in range(n):
            margin = rng.gauss(-2.6 if k >= 24 else -4.1, 1.2)
            fee = rng.gauss(80.0 * (0.97 if k >= 24 else 1.0), 1.5)
            wait = rng.gauss(20.0, 3.0)
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), rng.choice(["North", "South", "East"]),
                         "%.3f" % margin, "%.2f" % fee, "%.1f" % wait])
    return _csv(rows, ["sold_on", "region", "net_margin", "fee", "wait_minutes"])


# The sample plus seven adversarial files: no date, too short, flagged columns, a withheld date
# column, a confirmed change with three measures, a cleaning gate that trips, and negative
# averages with an unoffered forecast.
V2_FILES = {
    "sample-messy.csv": (_sample_bytes, SAMPLE_AS_OF),
    "no_date.csv": (ADVERSARIAL["no_date.csv"], V2_AS_OF),
    "twelve_rows.csv": (ADVERSARIAL["twelve_rows.csv"], V2_AS_OF),
    "orders_with_email.csv": (_email_file, "2026-01-10"),
    "visits.csv": (_dob_file, "2024-12-10"),
    "three_measures.csv": (_three_measures_file, V2_AS_OF),
    "rent-roll-tbd.csv": (lambda: _mixed_dates_file(tbd_every=3), "2025-12-20"),
    "margins-36m.csv": (_margins_file, V2_AS_OF),
}


def _v2(name):
    make, as_of = V2_FILES[name]
    data = make()
    return data, _run(data, name, "", None, as_of)


def _keys(obj, keys, where):
    assert isinstance(obj, dict), (where, type(obj))
    missing = [k for k in keys if k not in obj]
    assert not missing, "%s lacks %s" % (where, missing)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num_or_none(v, where):
    assert v is None or _is_num(v), (where, v)


def _pair(v, where):
    assert isinstance(v, list) and len(v) == 2, (where, v)
    for x in v:
        _num_or_none(x, where)


def _assert_contract_v2(rep, name=""):
    _assert_contract(rep)
    _keys(rep, V2_TOP, name + " top")
    assert rep["contract_version"] == 2, rep["contract_version"]
    _keys(rep["engine"], V2_ENGINE, "engine")
    _keys(rep["engine"]["benchmark"], V2_BENCH, "engine.benchmark")
    env = rep["engine"]["environment"]
    _keys(env, ("python", "numpy", "pandas", "sqlite", "pyodide"), "engine.environment")
    _keys(rep["health"], V2_HEALTH, "health")
    _keys(rep["cleaning"], V2_CLEANING, "cleaning")
    _keys(rep["forecast"], V2_FORECAST, "forecast")
    _keys(rep["reproducibility"], V2_REPRO, "reproducibility")
    _keys(rep["llm"], ("used", "model", "consent", "guard"), "llm")
    assert rep["llm"]["used"] is False
    assert isinstance(rep["charts"], list) and isinstance(rep["charts_suppressed"], list)
    for s in rep["charts_suppressed"]:
        _keys(s, ("rule", "type", "why"), "charts_suppressed[]")
        assert s["why"].strip(), s
    json.dumps(rep, allow_nan=False)
    if not rep["ok"]:
        assert rep["findings"] == [] and rep["charts"] == [], name
        return
    # -- engine and provenance
    rp = rep["reproducibility"]
    assert rp["input_sha256"] == rep["input"]["sha256"], rp["input_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", rp["engine_snapshot"]) and rp["engine_snapshot"].startswith(
        rep["engine"]["snapshot"]), rp["engine_snapshot"]
    fr = rp["figures_reproduced"]
    assert isinstance(fr["k"], int) and isinstance(fr["n"], int) and 0 <= fr["k"] <= fr["n"] and fr["n"] > 0, fr
    assert isinstance(rp["seeds"], dict) and isinstance(rp["B"], dict)
    b = rep["engine"]["benchmark"]
    assert isinstance(b["available"], bool) and isinstance(b["not_measured"], list)
    for k in ("placebo_real", "power_at_bar_matched", "cross_env"):
        assert b[k] is None and k in b["not_measured"], (k, b[k])
    # -- health: score is the minimum dimension, as §4.2 says
    h = rep["health"]
    dims = h["dimensions"]
    assert [d["name"] for d in dims] == ["completeness", "validity", "uniqueness", "consistency", "timeliness"], dims
    appl = [d["score"] for d in dims if d["applicable"]]
    assert appl and h["score"] == h["score_min"] == min(appl), (h["score"], h["score_min"], appl)
    assert h["weakest"] == next(d["name"] for d in dims if d["applicable"] and d["score"] == h["score_min"])
    for d in dims:
        _pair(d["ci"], "dimension ci")
        if d["ci"][0] is not None:
            assert d["ci"][0] <= d["score"] + 0.05 and d["score"] - 0.05 <= d["ci"][1], d
    assert h["accuracy"]["measured"] is False and h["accuracy"]["upper95"] is None
    for c in h["columns"]:
        _keys(c, V2_HEALTH_COL, "health.columns[]")
        for dim in ("completeness", "validity"):
            x = c[dim]
            if x["n"]:
                assert 0 <= x["k"] <= x["n"] and abs(x["pct"] - 100.0 * x["k"] / x["n"]) < 1e-9, (c["name"], dim, x)
                assert x["ci"][0] <= x["pct"] <= x["ci"][1], (c["name"], dim, x)
        if c["withheld"]:
            assert not c["top_values"] and c["numeric"] is None and c["dates"] is None, c
    # -- findings
    ids = {f["id"] for f in rep["findings"]}
    chart_ids = {c["id"] for c in rep["charts"]}
    for f in rep["findings"]:
        _keys(f, V2_FINDING, "finding %s" % f["id"])
        assert f["grade"] == GRADE_OF[f["verdict"]], (f["id"], f["grade"], f["verdict"])
        _keys(f["effect"], V2_EFFECT, "effect")
        _num_or_none(f["effect"]["estimate"], "effect.estimate")
        for k in ("ci", "ci_fcr"):
            assert f["effect"][k] is None or len(f["effect"][k]) == 2, (f["id"], k)
        if f["test"] is not None:
            _keys(f["test"], V2_TEST, "test %s" % f["id"])
            for k in ("p", "q", "p_point_null", "statistic"):
                _num_or_none(f["test"][k], "test." + k)
            if f["test"]["p"] is not None:
                assert 0 <= f["test"]["p"] <= 1, f["test"]
        if f["grade"] == "WATCH" and f["kind"] != "data_quality":
            assert f["watch"] and f["watch"]["reason"].strip(), (f["id"], f["watch"])
        if f["grade"] != "WATCH":
            assert f["watch"] is None, (f["id"], f["watch"])
        assert (f["error_rate"] is None) != (f["error_rate_note"] is None), (f["id"], f["error_rate"], f["error_rate_note"])
        assert f["posterior"]["shown"] is False and f["drivers"] == []
        assert set(f["chart_ids"]) <= chart_ids, (f["id"], f["chart_ids"])
        assert isinstance(f["trace"], list) and all(isinstance(x, str) for x in f["trace"])
    # -- forecast
    F = rep["forecast"]
    if F["available"]:
        assert F["models"] and sum(1 for m in F["models"] if m["champion"]) == 1, F["models"]
        mases = [m["mase"] for m in F["models"] if m["mase"] is not None]
        assert mases == sorted(mases), "models are not ordered by MASE"
        for m in F["models"]:
            _keys(m, V2_MODEL, "forecast.models[]")
            if not m["champion"]:
                assert m["coverage"] is None and m["dm"] is None, m
        assert F["band"]["level"] == 0.8 and F["coverage"]["n"] >= 1, (F["band"], F["coverage"])
    # -- charts
    seen = set()
    manager = 0
    for c in rep["charts"]:
        _keys(c, V2_CHART, "chart")
        assert c["id"] not in seen, c["id"]
        seen.add(c["id"])
        if c["rule"] == "V":
            # the charts chosen from the data (CONTRACT §5.9): drawn in the page's viz area, not among section 3's
            # six manager records; tools/test_nl_viz.py checks them
            assert c["type"] == "viz" and c["data"].get("id") == c["id"], c["id"]
            assert set(c["finding_ids"]) <= ids, (c["id"], c["finding_ids"])
            continue
        assert re.fullmatch(r"#\d+b?", c["rule"]), c["rule"]
        assert c["view"] in ("manager", "analyst") and isinstance(c["default_visible"], bool), c["id"]
        assert c["why_shown"].strip() and isinstance(c["data"], dict) and c["data"], c["id"]
        assert set(c["finding_ids"]) <= ids | _ledger_ids(rep), (c["id"], c["finding_ids"])
        manager += int(c["view"] == "manager" and c["default_visible"])
    assert manager <= 6, "manager view shows %d charts by default" % manager
    assert "findings_table" in seen and "benchmark" in seen, sorted(seen)
    # §5 "Suppression": every rule is either drawn or says in one line why it is absent
    accounted = {c["rule"] for c in rep["charts"]} | {x["rule"] for x in rep["charts_suppressed"]}
    # #2b (the driver waterfall) is drawn by the chart registry: a contribution_waterfall record (rule V) replaces its
    # suppressed line (nl_viz.drop_driver_line), and then no line says the drivers are not computed
    wfall = any(c["rule"] == "V" and (c["data"] or {}).get("chart") == "contribution_waterfall" for c in rep["charts"])
    if wfall:
        assert not any(x["rule"] == "#2b" for x in rep["charts_suppressed"]), (name, rep["charts_suppressed"])
        accounted.add("#2b")
    missing = set(SECTION5_RULES) - accounted
    assert not missing, "%s: rules neither drawn nor suppressed: %s" % (name, sorted(missing))


def _ledger_ids(rep):
    led = rep["downloads"]["ledger_json"]
    if not led:
        return set()
    doc = json.loads(led)
    return {f["id"] for k in ("audit_ledger", "analysis_ledger") for f in doc.get(k, [])}


def _ledger(rep):
    doc = json.loads(rep["downloads"]["ledger_json"])
    return {f["id"]: f for k in ("audit_ledger", "analysis_ledger") for f in doc.get(k, [])}


def test_v2_contract_shape_on_the_sample_and_six_adversarial_files():
    for name in V2_FILES:
        data, rep = _v2(name)
        assert rep["ok"], (name, rep["error"])
        _assert_contract_v2(rep, name)
    for data, name in ((b"", "empty.csv"), (b"a,b\n1,2\n4,5,6,7\n", "ragged.csv")):
        rep = NB.run(data, name, "")
        assert not rep["ok"]
        _assert_contract_v2(rep, name)


def test_v2_expected_charts_fire_and_absent_ones_say_why():
    _, rep = _v2("three_measures.csv")
    ids = {c["id"] for c in rep["charts"]}
    for want in ("kpi", "trend.price", "dist.price", "season.volume", "ranked.region", "catmonth.region",
                 "missingness", "cleaning.before_after", "corr", "findings_table", "benchmark"):
        assert want in ids, (want, sorted(ids))
    corr = next(c for c in rep["charts"] if c["id"] == "corr")
    assert corr["default_visible"] is False and corr["view"] == "analyst", corr
    # the file keeps exercising the two paths a chart can get wrong without anyone noticing: a
    # measure month under the engine's rows-a-month floor, and kept rows outside the window
    disc = next(c for c in rep["charts"] if c["id"] == "trend.discount")["data"]
    assert disc["month_floor"] > 0 and None in disc["values"], (disc["month_floor"], disc["values"][:12])
    ba = next(c for c in rep["charts"] if c["id"] == "cleaning.before_after")["data"]
    win = rep["reproducibility"]["parameters"]["window"]
    assert any(m > win["end"] and k for m, k in zip(ba["months"], ba["kept"])), (win, ba["months"][-3:])
    rules = {s["rule"] for s in rep["charts_suppressed"]}
    # #2b, the driver waterfall: the chart registry draws it here (a contribution_waterfall of region, rule V), so no
    # line says the drivers are not computed (integration pass, 30 Sep 2026: the line sat beside the waterfall)
    wf = [c for c in rep["charts"] if c["rule"] == "V" and c["data"]["chart"] == "contribution_waterfall"]
    assert wf and "#2b" not in rules, (len(wf), rep["charts_suppressed"])
    _, rep = _v2("no_date.csv")
    ids = {c["id"] for c in rep["charts"]}
    assert not any(i.startswith(("trend.", "season.", "fan.", "replay.", "catmonth.")) for i in ids), ids
    rules = {s["rule"] for s in rep["charts_suppressed"]}
    # no date, so no waterfall: #2b keeps its line, word for word
    assert {"#2", "#2b", "#3", "#4", "#5"} <= rules, rep["charts_suppressed"]
    assert [s["why"] for s in rep["charts_suppressed"] if s["rule"] == "#2b"] == \
        ["which segments drive a change is not computed in this release"], rep["charts_suppressed"]
    assert not any(c["rule"] == "V" and c["data"]["chart"] == "contribution_waterfall" for c in rep["charts"])


def _chart_strings(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _chart_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _chart_strings(v, out)
    elif isinstance(obj, str):
        out.append(obj)
    return out


def test_v2_flagged_columns_never_appear_in_chart_data():
    cases = [("orders_with_email.csv", None), ("visits.csv", None), ("sample-messy.csv", None),
             ("orders_with_email.csv", {"customer_email": "keep", "delivery_note": "keep"}),
             ("visits.csv", {"date_of_birth": "keep"})]      # a flagged date column the visitor kept
    for name, decisions in cases:
        make, as_of = V2_FILES[name]
        data = make()
        rep = _run(data, name, "", decisions, as_of)
        assert rep["ok"], (name, rep["error"])
        flagged = {f["column"] for f in rep["privacy"]["flagged"]}
        assert flagged, name
        assert rep["charts"], name
        for c in rep["charts"]:
            strings = _chart_strings(c["data"], [])
            hit = [s for s in strings if s in flagged]
            assert not hit, (name, c["id"], hit)
            assert not any(col in c["id"] for col in flagged), (name, c["id"])
        assert not set(rep["health"]["missingness"]["matrix_columns"]) & flagged, name
        _assert_contract_v2(rep, name)
        withheld = [f["column"] for f in rep["privacy"]["flagged"] if f["decision"] == "withhold"]
        blob = json.dumps(rep["charts"])
        for col in withheld:
            vals = [v for v in _landed_values(data, name, col) if len(v.strip()) >= 4]
            leaked = [v for v in vals if v in blob]
            assert not leaked, (name, col, leaked[:3])


# -- independent re-computation of the chart data from the two CSV downloads
def _frames(rep):
    import pandas as pd
    clean = pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]), dtype=str, keep_default_na=False)
    quar = pd.read_csv(io.StringIO(rep["downloads"]["quarantine_csv"]), dtype=str, keep_default_na=False) \
        if rep["downloads"]["quarantine_csv"] else None
    return clean, quar


def _months_of(series):
    import pandas as pd
    return pd.to_datetime(series.where(series != ""), errors="coerce").dt.strftime("%Y-%m")


def _month_range(a, b):
    y, m = int(a[:4]), int(a[5:7])
    out = []
    while "%04d-%02d" % (y, m) <= b:
        out.append("%04d-%02d" % (y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _close(a, b, where, rel=1e-9):
    if a is None or b is None:
        assert a is None and b is None, (where, a, b)
        return
    assert abs(float(a) - float(b)) <= rel * max(1.0, abs(float(a)), abs(float(b))), (where, a, b)


def _close_list(a, b, where):
    assert len(a) == len(b), (where, len(a), len(b))
    for i, (x, y) in enumerate(zip(a, b)):
        if isinstance(x, list):
            _close_list(x, y, "%s[%d]" % (where, i))
        else:
            _close(x, y, "%s[%d]" % (where, i))


def _recompute_charts(rep):
    """Every chart whose data comes from the cleaned table, re-computed here from the downloads
    by the contract's definitions (CONTRACT-v2.md §3), compared with the report's."""
    import numpy as np
    import pandas as pd
    clean, quar = _frames(rep)
    roles = rep["roles"]
    date = roles["date"]
    win = rep["reproducibility"]["parameters"]["window"]
    charts = {c["id"]: c for c in rep["charts"]}
    checked = []
    month = _months_of(clean[date]) if date else None
    wmonths = _month_range(win["start"], win["end"]) if win else []
    inwin = month.isin(wmonths) if date else None
    num = {m: pd.to_numeric(clean[m].where(clean[m] != ""), errors="coerce") for m in roles["measures"]}
    findings = {f["id"]: f for f in rep["findings"]}
    for cid, c in charts.items():
        d = c["data"]
        if cid.startswith("trend."):
            key = cid.split(".", 1)[1]
            months = _month_range(d["months"][0], d["months"][-1])
            assert d["months"] == months, cid
            src = _ledger(rep)[c["finding_ids"][0]]
            t = src.get("test") or {}
            if key == "volume":
                cnt = month.value_counts()
                vals = [float(cnt.get(m, 0)) for m in months]
                rows = [int(cnt.get(m, 0)) for m in months]
            elif key.startswith("volume:"):
                # a count of the rows in a subset: like for like, the levels present in every month
                comp = _ledger(rep)[t["like_for_like_of"]]["test"]["composition"]
                keep = clean[comp["column"]].astype(str).str.strip().str.lower().isin(comp["stable"])
                cnt = month[keep].value_counts()
                vals = [float(cnt.get(m, 0)) for m in months]
                rows = [int(cnt.get(m, 0)) for m in months]
                assert d["unit"] == "rows", (cid, d["unit"])
            elif key.startswith("total:"):
                # a monthly total of one column, of one kind of row when the engine split it
                tt = _ledger(rep)[t["like_for_like_of"]]["test"] if t.get("like_for_like_of") else t
                s = num[tt["total_of"]]
                keep = s.notna()
                if t.get("like_for_like_of"):
                    # a like-for-like total: the parent's total on the levels present throughout
                    cp = tt["composition"]
                    keep &= clean[cp["column"]].astype(str).str.strip().str.lower().isin(cp["stable"])
                t = dict(t, total_of=tt["total_of"], kind_split=tt.get("kind_split"))
                sp = t.get("kind_split")
                if sp:
                    lv = clean[sp["column"]].astype(str)
                    keep &= (lv == sp["level"]) if sp["group"] != "other" else (lv != sp["level"])
                rows = [int((keep & (month == m)).sum()) for m in months]
                vals = [float(s[keep & (month == m)].sum()) for m in months]
                assert d["unit"].startswith("total %s a month" % t["total_of"]), (cid, d["unit"])
            else:
                s = num[key]
                rows = [int(s[(month == m)].notna().sum()) for m in months]
                floor = max(1, int(d["month_floor"] or 0))
                vals = [float(s[(month == m)].mean()) if r >= floor else None for m, r in zip(months, rows)]
            _close_list(d["values"], vals, cid + ".values")
            assert d["rows"] == rows, (cid, d["rows"][:5], rows[:5])
            # the like-for-like line: the same series on the levels present in every modelled month
            # (the claim's composition record), its window means' ratio the engine's like-for-like change
            comp = (src.get("test") or {}).get("composition") or {}
            if d.get("like_for_like") is not None:
                lf = d["like_for_like"]
                keep_c = clean[comp["column"]].astype(str).str.strip().str.lower().isin(comp["stable"])
                if key == "volume":
                    lvals = [float((keep_c & (month == m)).sum()) for m in months]
                else:
                    assert key.startswith("total:"), (cid, "a like-for-like line on a claim that is not a count or a total")
                    lvals = [float(s[keep & keep_c & (month == m)].sum()) for m in months]
                _close_list(lf["values"], lvals, cid + ".like_for_like")
                lp = [v for m, v in zip(months, lvals) if d["windows"]["prior"][0] <= m <= d["windows"]["prior"][1]]
                ll = [v for m, v in zip(months, lvals) if d["windows"]["latest"][0] <= m <= d["windows"]["latest"][1]]
                _close(lf["window_means"]["prior"], sum(lp) / len(lp), cid + " lf prior")
                _close(lf["window_means"]["latest"], sum(ll) / len(ll), cid + " lf latest")
                _close((sum(ll) / len(ll)) / (sum(lp) / len(lp)) - 1.0, lf["estimate"], cid + " lf estimate", rel=1e-7)
            want_steps = [(str(mo), k) for k in ("entered", "left") for _, mo in comp.get(k) or [] if str(mo) in months]
            if t.get("screen_kind") == "step" and (t.get("step") or {}).get("month") is not None:
                mi = int(t["step"]["month"])
                sm = "%04d-%02d" % (mi // 12, mi % 12 + 1)
                if sm in months and sm not in [x[0] for x in want_steps]:
                    want_steps.append((sm, "step"))
            assert sorted((x["month"], x["kind"]) for x in d["steps"]) == sorted(want_steps), (cid, d["steps"], want_steps)
            pa, pb = d["windows"]["prior"]
            la, lb = d["windows"]["latest"]
            pv = [v for m, v in zip(months, vals) if pa <= m <= pb and v is not None]
            lv = [v for m, v in zip(months, vals) if la <= m <= lb and v is not None]
            _close(d["window_means"]["prior"], sum(pv) / len(pv), cid + ".prior")
            _close(d["window_means"]["latest"], sum(lv) / len(lv), cid + ".latest")
            fid = c["finding_ids"][0]
            _close(100.0 * (sum(lv) / len(lv)) / (sum(pv) / len(pv)) - 100.0, findings[fid]["value"],
                   cid + " headline", rel=1e-7)
            checked.append(cid)
            dist = charts.get("dist." + key)
            if dist is not None:
                allv = pv + lv
                k = int(math.ceil(math.log2(len(allv)) + 1))
                lo, hi = min(allv), max(allv)
                edges = list(np.linspace(lo, hi, k + 1)) if hi > lo else [lo - 0.5, hi + 0.5]
                _close_list(dist["data"]["edges"], edges, "dist edges")
                _close_list(dist["data"]["prior"]["values"], pv, "dist prior")
                _close_list(dist["data"]["latest"]["values"], lv, "dist latest")
                assert dist["data"]["prior"]["counts"] == [int(x) for x in np.histogram(pv, bins=edges)[0]]
                assert dist["data"]["latest"]["counts"] == [int(x) for x in np.histogram(lv, bins=edges)[0]]
                checked.append(dist["id"])
        elif cid.startswith("season."):
            key = cid.split(".", 1)[1]
            years = sorted({m[:4] for m in wmonths})
            assert d["years"] == years, (cid, d["years"])
            vals, ns = [], []
            for y in years:
                rv, rn = [], []
                for mm in range(1, 13):
                    m = "%s-%02d" % (y, mm)
                    if m not in wmonths:
                        rv.append(None)
                        rn.append(None)
                    elif key == "volume":
                        n = int((month == m).sum())
                        rv.append(float(n))
                        rn.append(n)
                    else:
                        s = num[key][month == m]
                        n = int(s.notna().sum())
                        rv.append(float(s.mean()) if n else None)
                        rn.append(n)
                vals.append(rv)
                ns.append(rn)
            _close_list(d["values"], vals, cid)
            assert d["n"] == ns, cid
            checked.append(cid)
        elif cid.startswith("ranked."):
            dim = cid.split(".", 1)[1]
            lab = clean.loc[inwin, dim]
            total = int(len(lab))
            vc = lab[lab != ""].value_counts()
            order = sorted(vc.items(), key=lambda kv: (-kv[1], kv[0]))
            top = order[:5]
            assert [b["label"] for b in d["bars"]] == [NB.public_text(k) for k, _ in top], (cid, d["bars"])
            assert [b["rows"] for b in d["bars"]] == [int(v) for _, v in top], cid
            for bar, (_, v) in zip(d["bars"], top):
                _close(bar["share_pct"], 100.0 * v / total, cid + " share")
            assert d["other"] == int(sum(v for _, v in order[5:])) and d["missing"] == int((lab == "").sum()), cid
            assert d["total"] == total, cid
            led = _ledger(rep)
            for bar in d["bars"]:
                if bar["fact_id"]:
                    _close(bar["share_pct"], led[bar["fact_id"]]["value"], cid + " vs engine fact", rel=1e-9)
            checked.append(cid)
        elif cid.startswith("catmonth."):
            dim = cid.split(".", 1)[1]
            lab = clean.loc[inwin, dim]
            vc = lab[lab != ""].value_counts()
            top = [k for k, _ in sorted(vc.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
            cats = [NB.public_text(k) for k in top] + ["other"]
            if (lab == "").any():
                cats.append("(missing)")
            assert d["categories"] == cats and d["months"] == wmonths, (cid, d["categories"])
            mw = month[inwin]
            want = []
            for k in top:
                want.append([int(((lab == k) & (mw == m)).sum()) for m in wmonths])
            other = (lab != "") & ~lab.isin(top)
            want.append([int((other & (mw == m)).sum()) for m in wmonths])
            if (lab == "").any():
                want.append([int(((lab == "") & (mw == m)).sum()) for m in wmonths])
            assert d["counts"] == want, cid
            checked.append(cid)
        elif cid == "missingness":
            cols = d["columns"]
            assert d["months"] == wmonths, cid
            assert set(cols) <= set(clean.columns) - {"source_line"}, cols
            mw = month[inwin]
            assert d["rows"] == [int((mw == m).sum()) for m in wmonths], cid
            want = [[int(((clean.loc[inwin, col] == "") & (mw == m)).sum()) for m in wmonths] for col in cols]
            assert d["nulls"] == want, cid
            nc = rep["health"]["missingness"]["nullity_corr"]
            some = [col for col in cols if (clean.loc[inwin, col] == "").any()]
            assert nc["columns"] == (some if len(some) >= 2 else []), (nc["columns"], some)
            if nc["columns"]:
                assert nc["n"] == int(inwin.sum()), (nc["n"], int(inwin.sum()))
                for i, a in enumerate(some):
                    for j, b in enumerate(some):
                        x = (clean.loc[inwin, a] == "").to_numpy(float)
                        y = (clean.loc[inwin, b] == "").to_numpy(float)
                        want_r = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else None
                        _close(nc["matrix"][i][j], want_r, "nullity corr %s %s" % (a, b))
            checked.append(cid)
        elif cid == "cleaning.before_after":
            from northledger import clean as _cl
            kept = month.value_counts()
            q = {}
            und_q = 0
            if quar is not None and len(quar) and date in quar.columns:
                raw = quar[date].map(lambda v: v if v != "" else None)
                parsed = _cl._coerce_dates(raw, list(d["date_formats"]))
                for x in parsed:
                    if pd.isna(x):
                        und_q += 1
                    else:
                        q[x.strftime("%Y-%m")] = q.get(x.strftime("%Y-%m"), 0) + 1
            months = sorted(set(kept.index.dropna()) | set(q))
            assert d["months"] == months, (cid, d["months"][:4], months[:4])
            assert d["kept"] == [int(kept.get(m, 0)) for m in months], cid
            assert d["set_aside"] == [int(q.get(m, 0)) for m in months], cid
            assert d["landed"] == [a + b for a, b in zip(d["kept"], d["set_aside"])], cid
            assert d["undated_set_aside"] == und_q, (cid, d["undated_set_aside"], und_q)
            assert sum(d["kept"]) + d["undated_kept"] == rep["cleaning"]["rows_clean"], cid
            assert sum(d["set_aside"]) + d["undated_set_aside"] == rep["cleaning"]["rows_quarantined"], cid
            checked.append(cid)
        elif cid == "corr":
            ms = d["measures"]
            for i, a in enumerate(ms):
                for j, b in enumerate(ms):
                    x, y = num[a][inwin], num[b][inwin]
                    ok = x.notna() & y.notna()
                    assert d["n"][i][j] == int(ok.sum()), (a, b)
                    r = float(np.corrcoef(x[ok].astype(float), y[ok].astype(float))[0, 1])
                    _close(d["r"][i][j], r, "corr %s %s" % (a, b), rel=1e-9)
            checked.append(cid)
        elif cid.startswith("fan."):
            F = rep["forecast"]
            assert d["history"] == F["series"] and len(d["forward"]) == len(F["forecast"]), cid
            for p, q_ in zip(d["forward"], F["forecast"]):
                assert (p["month"], p["value"], p["lo"], p["hi"]) == (q_["month"], q_["value"], q_["lo"], q_["hi"])
            checked.append(cid)
        elif cid.startswith("replay."):
            F = rep["forecast"]
            actual = {p["month"]: p["actual"] for p in F["series"]}
            assert d["hits"] == F["coverage"]["hits"] and d["n"] == F["coverage"]["n"] == len(d["months"]), cid
            assert sum(1 for p in d["months"] if p["in_band"]) == d["hits"], cid
            for p in d["months"]:
                _close(p["actual"], actual[p["month"]], cid)
                assert p["in_band"] == (p["lo"] <= p["actual"] <= p["hi"]), p
            checked.append(cid)
    return checked


def test_v2_chart_data_equals_recomputation_from_the_downloads():
    done = set()
    for name in ("sample-messy.csv", "three_measures.csv", "orders_with_email.csv", "rent-roll-tbd.csv"):
        _, rep = _v2(name)
        assert rep["ok"], (name, rep["error"])
        checked = _recompute_charts(rep)
        done |= {c.split(".")[0] for c in checked}
        drawn = {c["id"] for c in rep["charts"]}
        # the viz records (rule V) are re-computed from the downloads by tools/test_nl_viz.py
        data_charts = {i for i in drawn if not i.startswith(("kpi", "findings_table", "benchmark", "models.", "viz."))}
        assert data_charts <= set(checked), (name, sorted(data_charts - set(checked)))
    for kind in ("trend", "dist", "season", "ranked", "catmonth", "missingness", "cleaning", "corr", "fan", "replay"):
        assert kind in done, "no file exercised the %s chart" % kind


def test_v2_finding_numbers_are_the_engines():
    for name in ("sample-messy.csv", "three_measures.csv"):
        _, rep = _v2(name)
        led = _ledger(rep)
        for f in rep["findings"]:
            src = led[f["id"]]
            assert f["id"] in f["trace"], (f["id"], f["trace"])
            if src.get("test") and src["test"].get("ran"):
                t = src["test"]
                e = f["effect"]
                _close(e["estimate"], src["effect_size"], f["id"])
                _close_list(e["ci"], [src["effect_ci_low"], src["effect_ci_high"]], f["id"] + " ci")
                _close(e["level"], src["effect_ci_level"], f["id"])
                if src["effect_ci_adj_level"] is not None:
                    _close_list(e["ci_fcr"], [src["effect_ci_adj_low"], src["effect_ci_adj_high"]], f["id"])
                    _close(e["fcr_level"], src["effect_ci_adj_level"], f["id"])
                else:
                    assert e["ci_fcr"] is None and e["fcr_level"] is None, f["id"]
                T = f["test"]
                _close(T["p"], src["p_value"], f["id"] + " p")
                _close(T["p_point_null"], src["p_nonzero"], f["id"] + " p0")
                _close(T["B"], t["b"], f["id"] + " B")
                _close(T["n_months"], t["n"], f["id"])
                assert T["n_rows"] == src["rows_scanned"], f["id"]
                from northledger import stats as _st
                _close(T["statistic"], (t["delta_hat"] - _st.bar_log(t["bar"], t["direction"])) / t["se"], f["id"])
                _close_list(T["p_mc_interval"], t["mc_interval"], f["id"])
                _close(f["health"], src["claim_health"], f["id"])
                assert f["needed_to_upgrade"] == NB._visitor(src["needed_to_upgrade"]), f["id"]
            if f["grade"] == "CONFIRMED" and f["error_rate"] is not None and f["kind"] != "forecast":
                er = f["error_rate"]
                for key, suf in (("rate", "rate"), ("lower95", "lo"), ("upper95", "hi"), ("k", "k"), ("n", "n")):
                    _close(er[key], led["%s.bench.%s" % (f["id"], suf)]["value"], f["id"] + " " + key)
                assert er["sentence"] in f["why"], (er["sentence"][:80], f["why"][-300:])
            if f["kind"] == "forecast" and f["test"] is not None:
                slug = f["id"].split(".")[1]
                _close(f["test"]["p"], led["forecast.%s.baseline_test.p" % slug]["value"], f["id"])
                _close(rep["forecast"]["coverage"]["hits"], led["forecast.%s.coverage.hits" % slug]["value"], f["id"])


def test_v2_confirmed_claims_carry_their_bound_and_measured_rate():
    _, rep = _v2("three_measures.csv")
    conf = [f for f in rep["findings"] if f["grade"] == "CONFIRMED" and f["estimand"] == "ratio_of_average_month"]
    assert conf, [(f["id"], f["grade"]) for f in rep["findings"]]
    for f in conf:
        e = f["effect"]
        assert e["ci_fcr"] is not None and e["ci_fcr"][0] is not None and 0 < e["fcr_level"] < 1, e
        assert e["ci_fcr"][0] >= 0.05 - 1e-12, "a confirmed rise's adjusted bound is below the bar: %s" % e
        er = f["error_rate"]
        assert er and er["sentence"].startswith("Measured, not promised") and er["n"] > 0, er
        assert er["lower95"] <= er["rate"] <= er["upper95"], er
        assert f["watch"] is None and f["test"]["q"] is not None and f["test"]["q"] <= f["test"]["family_line"], f["test"]
    pm = rep["primary_metric"]
    assert pm and pm["finding_id"] in {f["id"] for f in rep["findings"]}, pm


def test_v2_watch_says_why_and_what_would_settle_it():
    # review v2 (24 Sep 2026): the sample's volume rise is two properties bought part-way, so the
    # engine holds it at WATCH for composition and points at the like-for-like count; the money
    # (the rent total) is the primary claim, read from its own columns
    _, rep = _v2("sample-messy.csv")
    vol = next(f for f in rep["findings"] if f["id"] == "measure.volume.change_pct")
    assert vol["grade"] == "WATCH" and vol["watch"]["checks_failed"], vol["watch"]
    assert "composition" in vol["watch"]["checks_failed"], vol["watch"]
    assert vol["watch"]["settle"] == vol["needed_to_upgrade"] and vol["watch"]["settle"].strip()
    assert "like-for-like" in vol["watch"]["settle"], vol["watch"]["settle"]
    assert vol["error_rate"] is None and vol["error_rate_note"], vol
    assert vol["test"]["q"] is not None and vol["test"]["family"] == "secondary", vol["test"]
    assert "trend.volume" in vol["chart_ids"], vol["chart_ids"]
    pm = rep["primary_metric"]
    assert pm["claim_key"] == "total:amount:rent", pm
    prim = next(f for f in rep["findings"] if f["id"] == pm["finding_id"])
    assert prim["test"]["family"] == "primary" and prim["grade"] == "WATCH", prim["test"]
    assert prim["watch"]["settle"] == prim["needed_to_upgrade"] and prim["watch"]["settle"].strip()
    # held back by what the file covers (two buildings bought part-way); the trend screen, when it also
    # fires, is named beside it (the engine names a stepped total's composition, not only "drift")
    assert "composition" in prim["watch"]["checks_failed"], prim["watch"]
    assert (not prim["checks"]["drift_screen"]["hit"]) or "drift_screen" in prim["watch"]["checks_failed"], prim["watch"]
    # a claim key is not a column: the columns read are the summed column and the kind it splits on
    assert prim["columns_read"] == [rep["roles"]["date"], "amount", "category"], prim["columns_read"]
    lfl = next(f for f in rep["findings"] if f["id"] == "measure.volume.like_for_like.change_pct")
    assert lfl["columns_read"] == [rep["roles"]["date"], "property"], lfl["columns_read"]
    assert "trend.total:amount:rent" in prim["chart_ids"], prim["chart_ids"]
    tr = next(c for c in rep["charts"] if c["id"] == "trend.total:amount:rent")["data"]
    assert tr["unit"] == "total amount a month, category RENT" and sum(tr["rows"]) > 0, (tr["unit"], tr["rows"][:6])
    # no chart averages a measure the engine refuses to average across its two kinds of row
    assert "season.amount" not in {c["id"] for c in rep["charts"]}, [c["id"] for c in rep["charts"]]
    assert any(s["rule"] == "#3" and "amount" in s["why"] for s in rep["charts_suppressed"]), rep["charts_suppressed"]


# ----------------------------------------------------------------- review of the site, 24 Sep 2026
def _find(rep, fid):
    return next(f for f in rep["findings"] if f["id"] == fid)


def _chart(rep, cid):
    return next(c for c in rep["charts"] if c["id"] == cid)


def _full_receipt():
    """The benchmark receipt beside the engine under test, and whether it measured this code."""
    from northledger import forecast as _fc
    with open(os.path.join(ENGINE_ROOT, "benchmark", "engine_benchmark.json"), encoding="utf-8") as fh:
        rec = json.load(fh)
    got = str((rec.get("decision_code_snapshot") or {}).get("id") or "")
    want = _fc.decision_snapshot()
    return rec, bool(got) and (got.startswith(want) or want.startswith(got))


def test_v2_a_measure_with_negative_averages_is_a_difference_never_a_percentage():
    # small_shop.csv: net_margin -4.075 -> -2.587 read "+36.5%" in the tables and "+148.9%" on its tile
    _, rep = _v2("margins-36m.csv")
    led = _ledger(rep)
    fid = "measure.net_margin.change"
    f = _find(rep, fid)
    src = led[fid]
    assert (src.get("test") or {}).get("no_ratio"), "the file no longer plants a measure with no ratio"
    e = f["effect"]
    assert e["scale"] == "difference" and e["ci"] is None and e["ci_fcr"] is None, e
    _close(e["estimate"], src["value"], fid)
    assert e["unit"] == src["unit"], (e["unit"], src["unit"])
    for t in _chart(rep, "kpi")["data"]["tiles"]:
        if t["finding_id"] == fid:
            assert t["scale"] == "difference", t
            _close(t["value"], src["value"], "tile")
    row = next(r for r in _chart(rep, "findings_table")["data"]["rows"] if r["finding_id"] == fid)
    assert row["scale"] == "difference" and row["ci"] is None, row
    _close(row["effect"], src["value"], "table row")


def test_v2_a_forecast_the_engine_does_not_offer_shows_no_point_or_range():
    # clinic_visits.csv: "not offered", yet a tile and both tables printed "755 [701 to 1,092]"
    _, rep = _v2("margins-36m.csv")
    assert not rep["forecast"]["available"], "the 36-month file's forecast is offered now"
    fc = [f for f in rep["findings"] if f["kind"] == "forecast" and f["id"].endswith(".next")]
    assert fc, [f["id"] for f in rep["findings"]]
    for f in fc:
        e = f["effect"]
        assert e["estimate"] is None and e["ci"] is None and e["level"] is None, (f["id"], e)
        assert e["note"] and "not offered" in e["note"], e
        assert f["id"] not in _chart(rep, "kpi")["finding_ids"], f["id"]
        row = next(r for r in _chart(rep, "findings_table")["data"]["rows"] if r["finding_id"] == f["id"])
        assert row["effect"] is None and row["ci"] is None, row
    # and in every file: no forecast graded NOT ENOUGH DATA carries a point, a range or a tile
    for name in V2_FILES:
        _, rep = _v2(name)
        for f in rep["findings"]:
            if f["kind"] == "forecast" and f["id"].endswith(".next") and f["grade"] == "NOT_ENOUGH_DATA":
                assert f["effect"]["estimate"] is None and f["effect"]["ci"] is None, (name, f["id"], f["effect"])
                assert f["id"] not in _chart(rep, "kpi")["finding_ids"], (name, f["id"])


def test_v2_a_confirmed_forecast_quotes_the_measured_false_beats_rate():
    # fixer round (24 Sep 2026): the sample's forecasts are no longer CONFIRMED (their skill test now
    # compares with a same-month-last-year rule scaled by the building purchase), so the confirmed
    # forecast of three_measures.csv carries this check
    _, rep = _v2("three_measures.csv")
    f = _find(rep, "forecast.monthly_rows.next")
    assert f["grade"] == "CONFIRMED", f["grade"]
    rec, fresh = _full_receipt()
    if not fresh:
        assert f["error_rate"] is None and f["error_rate_note"], f
        return
    er = f["error_rate"]
    assert er is not None, f["error_rate_note"]
    months = int(_ledger(rep)["forecast.monthly_rows.history_months"]["value"]) if \
        "forecast.monthly_rows.history_months" in _ledger(rep) else None
    cells = [r for r in rec["results"]["forecast"] if r["cell"]["process"] == "seasonal_rw" and r["cell"]["gated"]]
    assert cells, "the receipt has no seasonal random-walk condition"
    m = er["series_months"]
    if months is not None:
        assert m == months, (m, months)
    best = min(cells, key=lambda r: (abs(math.log(m / r["cell"]["months"])), r["cell"]["months"]))
    assert er["cell"] == best["cell"]["name"] and er["k"] == best["k_recommend"] and er["n"] == best["n"], er
    from northledger import benchmark as _bm   # the receipt's own exact interval (bisection), not the adapter's
    lo, hi = _bm.clopper_pearson(er["k"], er["n"])
    _close(er["rate"], 100.0 * er["k"] / er["n"], "rate")
    assert abs(er["lower95"] - 100 * lo) < 1e-6 and abs(er["upper95"] - 100 * hi) < 1e-6, (er, lo, hi)
    assert ("%d of %d" % (er["k"], er["n"])) in er["sentence"] and "not the chance" in er["sentence"], er["sentence"]
    assert f["error_rate_note"] is None
    # every CONFIRMED forecast the engine offers carries its own measured rate, not only the one charted
    for g in rep["findings"]:
        if g["kind"] == "forecast" and g["id"].endswith(".next") and g["grade"] == "CONFIRMED":
            assert g["error_rate"] is not None and g["error_rate"]["n"] > 0, (g["id"], g["error_rate_note"])
            # and its point keeps the engine's own 80% range beside it, charted or not
            led = _ledger(rep)
            _close_list(g["effect"]["ci"], [led[g["id"] + ".lo80"]["value"], led[g["id"] + ".hi80"]["value"]], g["id"])
            assert g["effect"]["ci"][0] <= g["effect"]["estimate"] <= g["effect"]["ci"][1], g["effect"]


def test_v2_benchmark_names_bar_cells_routed_cells_and_the_release_check():
    from northledger import benchmark as _bm
    from northledger import gate as _gate
    _, rep = _v2("sample-messy.csv")
    b = rep["engine"]["benchmark"]
    rec, why = _gate.load_benchmark_receipt()
    if rec is None:
        assert not b["available"] and b["note"], b
        return
    gated = [c for c in rec["cells"] if c.get("gated") and c.get("n")]
    worst = max(gated, key=lambda c: (c["k"] / c["n"], c["n"], c["name"]))
    W = b["worst_cell"]
    assert W["name"] == worst["name"] and W["at_bar"] == (float(worst.get("shift") or 0) > 0), W
    assert W["above_target"] == (100 * worst["cp95"][0] > 1.0), W
    res = _bm.judge_nulls_certify([{"k_confirm": c["k"], "n": c["n"]} for c in gated])
    cert = b["certification"]
    assert cert["cells"] == len(gated) and cert["passed"] == sum(r["pass"] for r in res), cert
    assert [z["name"] for z in cert["failed"]] == [c["name"] for c, r in zip(gated, res) if not r["pass"]], cert
    for z in [b["matched_cell"], W] + cert["failed"]:
        c = next(x for x in rec["cells"] if x["name"] == z["name"])
        assert z["routed"] == (c.get("claim") == "volume" and float(c.get("level") or 1000) < _gate.ROUTE_MIN_ROWS_A_MONTH), z
        assert z["at_bar"] == (float(c.get("shift") or 0) > 0), z
    # the sample's primary claim is a monthly TOTAL under the totals' line (fixer round, 24 Sep 2026: it was
    # described with the counts' rows-a-month rule): held at WATCH by rule on its EFFECTIVE rows a month,
    # rows / (1 + CV^2) of its amounts, against gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS; no rate and no power
    pm = rep["primary_metric"]["finding_id"]
    t = _ledger(rep)[pm]["test"]
    kind = _gate.claim_type(type("F", (), {"claim_key": _ledger(rep)[pm]["claim_key"]})())
    assert kind == "total", kind
    eff = t["rows_a_month"] / (1.0 + float(t.get("amount_cv") or 0.0) ** 2)
    assert eff < _gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS, eff
    R = b["routed"]
    assert R and R["for_finding"] == pm and R["kind"] == "total", R
    _close(R["rows_a_month"], t["rows_a_month"], "rows a month")
    _close(R["effective_rows_a_month"], eff, "effective rows a month")
    _close(R["min_effective_rows_a_month"], _gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS, "the totals' line")
    assert R["effective_rows_a_month"] < R["min_effective_rows_a_month"], R
    assert _find(rep, pm)["power"]["routed"] is True and _find(rep, pm)["power"]["nearest"] is None
    rr = _find(rep, pm)["power"]["routed_rule"]
    assert rr["kind"] == "total" and abs(rr["effective_rows_a_month"] - eff) < 1e-6 and rr["line"] == _gate.TOTAL_ROUTE_MIN_EFFECTIVE_ROWS, rr
    # the matched condition is the one the gate itself would quote for this claim: its own claim type,
    # its effective rows a month (a total) and seasonality
    want = _gate.nearest_benchmark_cell(rec, t["n"], t["sigma_hat"], t["phi_hat"], eff,
                                        claim=kind, seasonal=bool(t["seasonal"]) if t.get("seasonal") is not None else None)
    assert b["matched_cell"]["name"] == want["name"], (b["matched_cell"]["name"], want["name"])
    # the file's own diagnostics sit beside the matched condition, and "close" is earned; a condition far
    # off the file's momentum is no condition like it ("none"), and the file record says why
    F, M = b["file"], b["matched_cell"]
    _close(F["phi"], t["phi_hat"], "phi")
    _close(F["cv_pct"], 100 * t["sigma_hat"], "cv")
    _close(F["rows_a_month"], t["rows_a_month"], "rows a month")
    _close(F["effective_rows_a_month"], eff, "effective rows a month")
    far = _gate.benchmark_far(t["phi_hat"], want, [c for c in rec["cells"] if c.get("n") and c.get("claim") == kind])
    if far:
        assert b["match"] == "none" and F["unlike"], (b["match"], F)
    else:
        lv = M["effective_level"] if M["effective_level"] is not None else M["level"]
        close = F["months"] == M["n"] and abs(F["phi"] - M["phi"]) <= 0.1 and abs(F["cv_pct"] - M["cv_pct"]) <= 5.0 and \
            max(min(eff, 1000.0), min(lv, 1000.0)) / max(min(min(eff, 1000.0), min(lv, 1000.0)), 1.0) <= 1.5
        assert b["match"] == ("close" if close else "nearest"), (b["match"], F, M)


def test_v2_power_is_quoted_from_the_nearest_measured_condition():
    from northledger import gate as _gate
    _, rep = _v2("three_measures.csv")
    rec, fresh = _full_receipt()
    led = _ledger(rep)
    tested = [f for f in rep["findings"] if f["test"] and f["test"]["ran"] and f["estimand"] == "ratio_of_average_month"]
    assert tested
    for f in tested:
        p, t = f["power"], led[f["id"]]["test"]
        if not fresh:
            assert p["nearest"] is None, p
            continue
        if _gate.uncertified_condition(type("F", (), {"claim_key": led[f["id"]]["claim_key"], "test": t})()) is not None:
            assert p["routed"] and p["nearest"] is None, p
            continue
        kind = _gate.claim_type(type("F", (), {"claim_key": led[f["id"]]["claim_key"]})())
        alts = [{"name": r["cell"]["name"], "months": r["cell"]["months"], "cv": r["cell"]["cv"],
                 "phi_hat_mean": r["phi_hat_mean"], "level": r["cell"]["level"], "n": r["n"], "k": r["k_confirm"],
                 "claim": r["cell"]["claim"], "amount_sd": r["cell"].get("amount_sd")}
                for r in rec["results"]["change"] if r["cell"]["role"] == "alt" and r["cell"]["shift"] == 0.2
                and r["cell"]["claim"] == kind]
        # a count on its rows a month; a monthly total on its EFFECTIVE rows a month, rows / (1 + CV^2) of its
        # amounts, as the gate matches it (fixer round, 24 Sep 2026: totals were matched on nothing)
        level = t.get("rows_a_month", t["mean_month"]) if kind == "volume" else \
            (t["rows_a_month"] / (1.0 + float(t.get("amount_cv") or 0.0) ** 2) if kind == "total" else None)
        want = _gate.nearest_benchmark_cell({"cells": alts}, t["n"], t["sigma_hat"], t["phi_hat"], level)
        z = p["nearest"]
        assert z and z["name"] == want["name"] and z["k"] == want["k"] and z["N"] == want["n"], (f["id"], z, want["name"])
        _close(z["rate"], 100.0 * want["k"] / want["n"], f["id"])
        assert z["shift_pct"] == 20.0 and not p["routed"], z


def test_v2_watch_says_whether_the_claim_moved():
    # C2: a CSAT fall of -9.0% to -4.2% read exactly like the claims with no movement at all
    _, rep = _v2("margins-36m.csv")
    seen = set()
    for f in rep["findings"]:
        if f["grade"] != "WATCH" or not f["test"] or f["effect"]["scale"] != "fraction":
            continue
        lo, hi = f["effect"]["ci"]
        mv = f["watch"]["movement"]
        bar = f["test"]["bar"]
        if lo <= 0 <= hi:
            want = "unclear"
        elif (lo >= bar) or (hi <= 1 / (1 + bar) - 1):
            want = "cleared"
        else:
            want = "moved"
        assert mv and mv["kind"] == want, (f["id"], f["effect"]["ci"], mv)
        if want != "unclear":
            assert mv["direction"] == ("rise" if lo > 0 else "fall"), (f["id"], mv)
        seen.add(want)
    assert {"moved", "unclear"} <= seen, seen
    fee = _find(rep, "measure.fee.change")
    assert fee["watch"]["movement"] == {"kind": "moved", "direction": "fall"}, fee["watch"]
    # the stronger evidence leads: tiles and manager charts put moved claims ahead of unclear ones
    rank = {"CONFIRMED": 0, "WATCH": 1, "NOT_ENOUGH_DATA": 3}

    def strength(fid):
        g = _find(rep, fid)
        r = rank[g["grade"]]
        if g["grade"] == "WATCH":
            r = 1 if ((g["watch"] or {}).get("movement") or {}).get("kind") in ("moved", "cleared") else 2
        return r
    tiles = [t["finding_id"] for t in _chart(rep, "kpi")["data"]["tiles"]]
    assert [strength(x) for x in tiles] == sorted(strength(x) for x in tiles), tiles
    trends = [c["finding_ids"][0] for c in rep["charts"] if c["id"].startswith("trend.")]
    assert [strength(x) for x in trends] == sorted(strength(x) for x in trends), trends


def test_v2_first_screen_holds_three_business_tiles_and_no_weaker_chart_first():
    for name in V2_FILES:
        _, rep = _v2(name)
        if not rep["ok"] or not rep["charts"]:
            continue
        tiles = _chart(rep, "kpi")["data"]["tiles"]
        assert len(tiles) <= 3, (name, len(tiles))
        biz = [f for f in rep["findings"] if f["kind"] in ("business", "forecast") and f["effect"]["estimate"] is not None]
        if biz:
            assert all(t["kind"] in ("business", "forecast") for t in tiles), (name, tiles)
        order = {"CONFIRMED": 0, "WATCH": 1, "NOT_ENOUGH_DATA": 2}
        mgr = [_find(rep, c["finding_ids"][0])["grade"] for c in rep["charts"]
               if c["view"] == "manager" and c["id"].startswith("trend.")]
        assert [order[g] for g in mgr] == sorted(order[g] for g in mgr), (name, mgr)


_PLAN_CODES = re.compile(r"\b(?:R1|S2|M1-M2|M\d{1,2})\b|design §|§\d|\(design\b|To turn this into a recommendation")


def test_v2_visitor_text_carries_no_plan_codes():
    for name in V2_FILES:
        _, rep = _v2(name)
        texts = []
        for f in rep["findings"]:
            texts += [(f["test"] or {}).get("method") or "", (f["watch"] or {}).get("settle") or "",
                      f["needed_to_upgrade"] or "", f["power"]["design"] or "", f["error_rate_note"] or ""]
        texts += [c["why_shown"] for c in rep["charts"]] + [s["why"] for s in rep["charts_suppressed"]]
        texts += [m["name"] for m in rep["methods"]] + [l["text"] for l in rep["limitations"] if l["kind"] != "data"]
        bad = [t for t in texts if _PLAN_CODES.search(t)]
        assert not bad, (name, bad[:3])


def test_v2_money_totals_and_their_forecasts_are_marked_money():
    # review of the site (24 Sep 2026): "82,201 (80% range 74,149.91 to 123,139.33)" beside "13,717.58"
    # on the first screen; the page prints money in whole units when the report says it is money
    _, rep = _v2("sample-messy.csv")
    led = _ledger(rep)
    money_slugs = {x["test"]["series_slug"] for x in led.values()
                   if (x.get("test") or {}).get("additive") == "money" and x["test"].get("series_slug")}
    money_keys = {x["claim_key"] for x in led.values() if (x.get("test") or {}).get("additive") == "money"}
    assert money_slugs and money_keys, "the sample has no money total"
    fcs = [f for f in rep["findings"] if f["kind"] == "forecast" and f["id"].endswith(".next")]
    assert any(f["effect"]["scale"] == "money" for f in fcs), [(f["id"], f["effect"]["scale"]) for f in fcs]
    for f in fcs:
        slug = f["id"][len("forecast."):-len(".next")]
        want = "money" if slug in money_slugs else led[f["id"]]["unit"]
        assert f["effect"]["scale"] == want, (f["id"], f["effect"]["scale"], want)
    tiles = next(c for c in rep["charts"] if c["id"] == "kpi")["data"]["tiles"]
    for t in tiles:
        assert t["scale"] == next(f for f in rep["findings"] if f["id"] == t["finding_id"])["effect"]["scale"], t
    for c in rep["charts"]:
        if c["id"].startswith(("trend.", "dist.")):
            key = c["id"].split(".", 1)[1]
            parent = [x for x in led.values() if x.get("claim_key") == key]
            lf_of = (parent[0].get("test") or {}).get("like_for_like_of") if parent else None
            is_money = key in money_keys or (lf_of is not None and led[lf_of]["claim_key"] in money_keys)
            assert c["data"]["scale"] == ("money" if is_money else None), (c["id"], c["data"]["scale"])
        if c["id"].startswith(("fan.", "replay.")):
            assert c["data"]["scale"] == ("money" if c["id"].split(".", 1)[1] in money_slugs else None), c["id"]
    assert next(c for c in rep["charts"] if c["id"] == "trend.volume")["data"]["scale"] is None
    # a file with no money column: nothing is marked money
    _, rep3 = _v2("three_measures.csv")
    assert not any(f["effect"]["scale"] == "money" for f in rep3["findings"])
    assert not any((c["data"].get("scale") == "money") for c in rep3["charts"]), \
        [c["id"] for c in rep3["charts"] if c["data"].get("scale") == "money"]


def test_v2_the_sample_story_is_charted_like_for_like_with_its_coverage_steps():
    # review of the site (24 Sep 2026): the sample's planted story (East York Low-Rise bought in
    # 2025-03 steps the rent total up) reached the page in words only; the primary claim's chart now
    # carries the like-for-like line and the months where the file's coverage changed, and the
    # claim carries the engine's composition record and its like-for-like comparison
    _, rep = _v2("sample-messy.csv")
    led = _ledger(rep)
    F = {f["id"]: f for f in rep["findings"]}
    pm = rep["primary_metric"]
    f = F[pm["finding_id"]]
    t = led[f["id"]]["test"]
    comp, rec = f["composition"], t["composition"]
    assert comp and comp["column"] == rec["column"] and comp["words"] == rec["words"], (comp, rec)
    want = sorted([(str(m), "entered", str(lv)) for lv, m in rec.get("entered") or []]
                  + [(str(m), "left", str(lv)) for lv, m in rec.get("left") or []])
    assert [(s["month"], s["kind"], s["level"]) for s in comp["steps"]] == want, (comp["steps"], want)
    assert ("2025-03", "entered", "East York Low-Rise") in want, want
    assert comp["levels_kept"] == len(rec["stable"])
    lf = comp["like_for_like"]
    assert lf is not None, "the primary claim has no like-for-like comparison"
    if lf["tested"]:
        src = led[lf["fact_id"]]
        _close(lf["estimate"], src["effect_size"], "lf estimate")
        _close_list(lf["ci"], [src["effect_ci_low"], src["effect_ci_high"]], "lf ci")
        assert lf["grade"] == F[lf["finding_id"]]["grade"] and F[lf["finding_id"]]["parent_id"] == f["id"], lf
    else:
        assert lf["fact_id"] == rec["like_for_like"] and lf["ci"] is None, lf
        _close(lf["estimate"], led[rec["like_for_like"]]["value"] / 100.0, "lf estimate")
    ch = next(c for c in rep["charts"] if c["id"] == "trend." + pm["claim_key"])
    assert ch["view"] == "manager" and ch["data"]["like_for_like"] is not None, ch["view"]
    assert [s["month"] for s in ch["data"]["steps"]] == [s["month"] for s in comp["steps"]], ch["data"]["steps"]
    for k in ("estimate", "ci", "grade", "tested", "fact_id"):
        assert ch["data"]["like_for_like"][k] == lf[k], k
    if lf["finding_id"]:
        assert ch["finding_ids"] == [f["id"], lf["finding_id"]], ch["finding_ids"]
    # the volume's like-for-like claim names its parent
    vol_lf = F.get("measure.volume.like_for_like.change_pct")
    assert vol_lf is not None and vol_lf["parent_id"] == "measure.volume.change_pct", vol_lf and vol_lf["parent_id"]
    assert all(x["parent_id"] is None for x in rep["findings"] if not (led[x["id"]].get("test") or {}).get("like_for_like_of"))


def test_v2_the_manager_cap_note_counts_what_it_names():
    # review of the site (24 Sep 2026): "moved to the analyst view: the manager view shows at most six
    # charts" beside four drawn ones (the six of §5 count the tiles and the findings table)
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    names = {"fan": "the forecast", "replay": "its replay", "benchmark_strip": "the false-alarm benchmark"}
    seen = 0
    for name in V2_FILES:
        _, rep = _v2(name)
        drawn = [c for c in rep["charts"] if c["view"] == "manager" and c["id"] not in ("kpi", "findings_table")
                 and c["rule"] != "V"]          # the viz records are drawn in their own area (CONTRACT §5.9)
        for c in rep["charts"]:
            if "moved to the analyst view" not in c["why_shown"]:
                continue
            seen += 1
            assert "six charts" not in c["why_shown"], c["why_shown"]
            m = re.search(r"draws at most (\w+) charts beside its headline tiles and findings table, and holds (\w+) "
                          r"already: (.*)$", c["why_shown"])
            assert m and words.index(m.group(1)) == len(drawn) == words.index(m.group(2)), (name, len(drawn), c["why_shown"])
            listed = m.group(3)
            for d in drawn:
                if d["type"] in names:
                    assert names[d["type"]] in listed, (d["id"], listed)
            trends = len([d for d in drawn if d["type"] not in names])
            if trends:
                assert "%s trend chart" % words[trends] in listed, (trends, listed)
    assert seen, "no file moved a chart to the analyst view"


# ======================================================================== fixer round, 24 Sep 2026
# A senior manager's walk: the bottom line was 7-11 "graded WATCH" clauses; the ledger's line count was a
# first-screen planning number; a monthly total was described by the counts' rows-a-month rule; a step the
# engine had named sat beside "no clear movement"; a file far from every simulated condition was quoted a
# matched rate; a subscription export added USD to CAD. Each test failed on the pre-change adapter.
_MON_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}\b")


def test_v2_the_bottom_line_is_three_sentences_of_the_engines_own_numbers():
    from northledger.narrate import story_number
    _, rep = _v2("sample-messy.csv")
    sm = rep["summary"]
    lines = sm["lines"]
    assert 1 <= len(lines) <= 3, lines
    first = lines[0]["text"]
    assert "East York Low-Rise" in first and "like for like" in first and "Mar 2025" in first, first
    led = _ledger(rep)
    for l in lines:
        assert "graded" not in l["text"] and "sample-messy.csv" not in l["text"], l["text"]
        allowed = set()
        for fid in l["finding_ids"]:
            v, u = led[fid]["value"], led[fid]["unit"]
            allowed |= {story_number(v, u), story_number(abs(v), u)}
        allowed.add("%d%%" % round(100 * float(((rep["forecast"] or {}).get("band") or {}).get("level") or 0.8)))
        comp = _find(rep, rep["primary_metric"]["finding_id"])["composition"] or {}
        allowed.add(str(comp.get("levels_kept")))          # "the 3 properties held throughout"
        for num in re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?%?", _MON_RE.sub("", l["text"])):
            assert num in allowed, (num, sorted(allowed), l["text"])


def test_v2_the_ledgers_line_count_is_monitoring_beside_a_money_total():
    _, rep = _v2("sample-messy.csv")
    mon = rep["summary"]["monitoring"]
    assert {"measure.volume.change_pct", "forecast.monthly_rows.next"} <= set(mon), mon
    tiles = [t["finding_id"] for t in _chart(rep, "kpi")["data"]["tiles"]]
    assert tiles and tiles[0] == rep["primary_metric"]["finding_id"] and not set(tiles) & set(mon), tiles
    assert rep["summary"]["labels"][rep["primary_metric"]["finding_id"]] == "Rent, monthly total", rep["summary"]["labels"]
    # a file with no money total counts its rows as the business: nothing is monitoring there
    _, rep2 = _v2("margins-36m.csv")
    assert "measure.volume.change_pct" not in rep2["summary"]["monitoring"] or \
        any(str(f["id"]).startswith("measure.") and ".total" in f["id"] for f in rep2["findings"]), rep2["summary"]


def test_v2_a_claim_the_coverage_steps_explain_reads_as_stepped():
    _, rep = _v2("sample-messy.csv")
    pm = _find(rep, rep["primary_metric"]["finding_id"])
    mv = pm["watch"]["movement"]
    assert mv["kind"] == "stepped" and mv["direction"] == "rise", mv
    assert mv["steps"][0]["month"] == "2025-03" and mv["steps"][0]["levels"][0][0] == "East York Low-Rise", mv
    led = _ledger(rep)
    _close(mv["steps"][0]["size"], led[mv["steps"][0]["fact_id"]]["value"] / 100.0, "the step's size")


def _random_walk_revenue() -> bytes:
    """36 months of about 500 sales a month whose level wanders like a random walk (momentum near 1):
    no simulated condition of the benchmark is like it."""
    import random as _random
    rng = _random.Random(41)
    rows, lvl = [], 0.0
    for k in range(37):
        y, m = 2023 + (k + 7) // 12, (k + 7) % 12 + 1
        lvl += rng.gauss(0.0, 0.08)
        for i in range(int(round(500 * math.exp(rng.gauss(0, 0.02))))):
            day = "%04d-%02d-%02d" % (y, m, rng.randint(1, 28)) if k < 36 else "2026-09-%02d" % rng.randint(1, 10)
            rows.append([day, rng.choice(["North", "South"]), "%.2f" % (60.0 * math.exp(lvl) * rng.uniform(0.7, 1.3))])
    return _csv(rows, ["sold_on", "region", "revenue"])


def test_v2_a_file_unlike_every_measured_condition_is_quoted_no_rate():
    from northledger import gate as _gate
    rep = _run(_random_walk_revenue(), "wander.csv", "", None, V2_AS_OF)
    b = rep["engine"]["benchmark"]
    rec, why = _gate.load_benchmark_receipt()
    if rec is None:
        assert not b["available"], b
        return
    F = b["file"]
    assert F and F["claim"] == "total" and F["effective_rows_a_month"] is not None, F
    assert F["phi"] > 0.93, F                     # the fixture wanders like a random walk
    assert b["match"] == "none" and "momentum" in F["unlike"], (b["match"], F)


def _two_currencies() -> bytes:
    """36 months of invoices in USD and CAD, a few refunded or failed, with customer ids."""
    import random as _random
    rng = _random.Random(7)
    rows = []
    for k in range(37):
        y, m = 2023 + (k + 7) // 12, (k + 7) % 12 + 1
        for cur, n in (("USD", 220), ("CAD", 80)):
            for i in range(n):
                st = "paid" if rng.random() > 0.05 else rng.choice(["refunded", "failed"])
                day = "%04d-%02d-%02d" % (y, m, rng.randint(1, 28)) if k < 36 else "2026-09-%02d" % rng.randint(1, 10)
                rows.append(["INV-%06d" % len(rows), "C%04d" % rng.randint(1, 900), day,
                             "%.2f" % rng.choice([19.0, 49.0, 129.0]), cur, st])
    return _csv(rows, ["invoice_id", "customer_id", "invoice_date", "amount", "currency", "status"])


def test_v2_currencies_and_refunds_are_screened_before_money_is_added():
    rep = _run(_two_currencies(), "invoices.csv", "", None, V2_AS_OF)
    ids = {f["id"] for f in rep["findings"]}
    assert "measure.amount.total.change" not in ids, "USD and CAD were added together"
    assert {"measure.amount.total.usd.change", "measure.amount.total.cad.change"} <= ids, sorted(i for i in ids if "total" in i)
    wh = " ".join(rep["story"]["what_happened"])
    assert "never added across currencies" in wh and "not money received" in wh, wh
    assert any("Churn and retention are not measured" in x for x in rep["story"]["cannot_answer"]), rep["story"]["cannot_answer"]
    assert rep["summary"]["labels"].get("measure.amount.total.usd.change") == "Amount in USD, monthly total", rep["summary"]["labels"]


def _long_table(weekend_zeros=True):
    """A small StatCan-layout table: 3 live series in two units, one discontinued, metadata columns."""
    import datetime as dt
    rows = ['"REF_DATE","GEO","DGUID","Type of currency","UOM","UOM_ID","SCALAR_FACTOR","SCALAR_ID","VECTOR",'
            '"COORDINATE","VALUE","STATUS","SYMBOL","TERMINATED","DECIMALS"']
    series = [("Old index, daily", "Index, 1992=100", 100.0, "2015-01-01", "2019-12-31"),
              ("U.S. dollar, daily average", "Dollars", 1.30, "2020-01-01", "2026-08-31"),
              ("European euro, daily average", "Dollars", 1.45, "2020-01-01", "2026-08-31"),
              ("Japanese yen, daily average", "Dollars", 0.0095, "2020-01-01", "2026-08-31")]
    for i, (name, uom, base, a, b) in enumerate(series):
        d, end, k = dt.date.fromisoformat(a), dt.date.fromisoformat(b), 0
        while d <= end:
            wk = d.weekday() >= 5
            v = 0.0 if wk else base * (1 + 0.0002 * k + 0.01 * ((k % 7) - 3) / 3)
            if not wk or weekend_zeros:
                rows.append('"%s","Canada","2021A000011124","%s","%s","81","units","0","v%d","1.%d","%.8f","","","","4"'
                            % (d.isoformat(), name, uom, 1000 + i, i + 1, v))
            d += dt.timedelta(days=1)
            k += 1
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_a_long_statistical_table_is_read_one_series_per_column():
    # a visitor's StatCan table 33-10-0036 (25 Sep 2026): read as it stood, 28 exchange rates in two units
    # were averaged together, COORDINATE and DECIMALS became measures and the currency names were withheld
    rep = NB.run(_long_table(), "fx.csv", "", None, "2026-09-15")
    assert rep["ok"], rep["error"]
    lay = rep["input"].get("layout") or {}
    assert lay.get("series") == 4 and lay.get("kept") == 3, lay
    assert lay.get("set_aside") == {"discontinued": ["Old index, daily"]}, lay
    assert {"COORDINATE", "DECIMALS", "VECTOR", "UOM"} <= set(lay.get("metadata_set_aside") or []), lay
    assert lay.get("zeros_as_empty", 0) > 0, "weekend placeholder zeros must be read as empty"
    measures = rep["roles"]["measures"]
    assert not any(m in ("coordinate", "decimals", "value") for m in measures), measures
    assert any("dollar" in m for m in measures) and any("yen" in m for m in measures), measures
    assert rep["privacy"]["flagged"] == [], rep["privacy"]
    assert rep["primary_metric"] and rep["primary_metric"]["claim_key"] != "volume", rep["primary_metric"]
    assert any(l["kind"] == "data" and "long statistical table" in l["text"] for l in rep["limitations"])
    assert any(f["rule"] == "closed_day_zeros" for f in rep["cleaning"]["fixes"]), rep["cleaning"]["fixes"]


def test_the_question_picks_the_series_a_long_table_leads_with():
    rep = NB.run(_long_table(), "fx.csv", "How has the Japanese yen moved?", None, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert rep["primary_metric"]["claim_key"] == "japanese_yen_daily_average", rep["primary_metric"]
    assert rep["input"]["layout"]["lead_why"] == "your question names it"


def test_an_ordinary_file_is_not_reshaped():
    rep = NB.run(open(SAMPLE, "rb").read(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    assert rep["ok"] and "layout" not in rep["input"], rep["input"]


def _panel_csv():
    rows = ["country,year,iso_code,population,co2"]
    for c, iso in (("Canada", "CAN"), ("Mexico", "MEX"), ("World", "")):
        for y in range(1850, 2025):
            rows.append("%s,%d,%s,%d,%.2f" % (c, y, iso, 1000000 + y, (y - 1800) * (3 if c == "World" else 1)))
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_an_ai_plan_is_validated_applied_and_reported():
    plan = {"goal": "How have CO2 emissions changed?", "understanding": "Countries by year.",
            "goal_candidates": ["Which country emits most?"],
            "operations": [{"op": "exclude_rows", "column": "country", "values": ["World"]},
                           {"op": "set_aside", "columns": ["iso_code"]},
                           {"op": "date_from_year", "column": "year"},
                           {"op": "not_personal", "columns": ["population"]},
                           {"op": "drop_everything"},
                           {"op": "set_aside", "columns": ["no_such_column"]}],
            "primary": "co2", "quality_risks": ["aggregates mixed with countries"]}
    rep = NB.run(_panel_csv(), "co2.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    ap = rep["ai_plan"]
    assert ap["goal"] == "How have CO2 emissions changed?"
    assert any("dropped" in a and "country" in a for a in ap["applied"]), ap["applied"]
    assert any("before 1900" in a for a in ap["applied"]), ap["applied"]
    assert any("drop_everything" in r for r in ap["refused"]) and any("no_such_column" in r for r in ap["refused"]), ap["refused"]
    assert rep["roles"]["date"] == "date", rep["roles"]
    assert "iso_code" not in rep["roles"]["measures"] and "year" not in rep["roles"]["measures"], rep["roles"]
    assert all(f["column"] != "population" or f["decision"] != "withhold" for f in rep["privacy"]["flagged"]), rep["privacy"]


def test_the_visitors_plan_review_is_kept_as_a_clean_record():
    plan = {"goal": "How have CO2 emissions changed?", "operations": [{"op": "date_from_year", "column": "year"}], "primary": "co2"}
    rv = {"approved": True, "goal_edited": True, "ops_removed": ["set_aside"], "analyses_removed": [],
          "at": "2026-09-29T00:00:00Z", "evil": {"x": 1}}
    rep = NB.run(_panel_csv(), "co2.csv", "", {"__plan__": plan, "__plan_review__": rv}, "2026-09-15")
    assert rep["ok"], rep["error"]
    r = rep["ai_plan"]["review"]
    assert set(r) == {"approved", "goal_edited", "ops_removed", "analyses_removed", "at"} and r["approved"] is True and r["ops_removed"] == ["set_aside"], r
    rep = NB.run(_panel_csv(), "co2.csv", "", {"__plan_review__": {"approved": False, "at": "2026-09-29T00:00:00Z"}}, "2026-09-15")
    assert rep["ok"] and rep["plan_review"]["approved"] is False and "ai_plan" not in rep, rep.get("plan_review")


def test_the_profile_for_the_planner_holds_no_rows():
    prof = NB.profile_for_ai(_panel_csv(), "co2.csv", flagged={"population": "some values look personal"}, decisions={})
    assert prof["ok"] and prof["rows"] == 525
    names = [c["name"] for c in prof["columns"]]
    # a flagged column the visitor left at withhold (the engine's default): never named, never counted
    assert names == ["country", "year", "iso_code", "co2"] and prof["columns_total"] == 4, names
    assert "population" not in json.dumps(prof)
    # before the visitor has chosen (the page's first call, flags only) it is withheld too: a profile sent as
    # it stands never names it
    first = NB.profile_for_ai(_panel_csv(), "co2.csv", flagged={"population": "some values look personal"})
    assert "population" not in json.dumps(first) and first["columns_total"] == 4, [c["name"] for c in first["columns"]]
    coded = NB.profile_for_ai(_panel_csv(), "co2.csv", flagged={"population": "some values look personal"},
                              decisions={"population": "code"})
    pop = [c for c in coded["columns"] if c["name"] == "population"][0]
    assert pop["looks_personal"] is True and pop.get("privacy_flag"), pop
    assert not set(pop) & {"top_values", "values", "examples", "min", "median", "max"}, pop
    text = json.dumps(coded)
    assert "10018" not in text and "10020" not in text, "a coded column's value reached the profile"
    kept = NB.profile_for_ai(_panel_csv(), "co2.csv", flagged={"population": "some values look personal"},
                             decisions={"population": "keep"})
    pop = [c for c in kept["columns"] if c["name"] == "population"][0]
    assert pop["looks_personal"] is False and "privacy_flag" not in pop and pop["min"] == 1001850.0, pop
    # the time span and what the analyses need (owner's schema, 29 Sep 2026): the year column stands in for a date
    assert prof["time"] == {"column": "year", "first": "1850-01", "last": "2024-12", "months": 2100, "distinct_years": 175}, prof["time"]
    assert [x["analysis"] for x in prof["analysis_limits"]][:3] == ["trend", "extremes", "agreement"], prof["analysis_limits"]
    assert all(x["ok"] is True for x in prof["analysis_limits"] if x["analysis"] in ("trend", "extremes")), prof["analysis_limits"]


def _two_source_csv():
    """Two sources of one monthly quantity in long form, named Source/Year/Mean (not the agency names the
    rule knows); B runs 0.1 above A and stops six months earlier; both warm 0.02 a year."""
    rows = ["Source,Year,Mean"]
    for src, off, end in (("A", 0.0, 2026), ("B", 0.1, 2025)):
        for y in range(1950, end + 1):
            for m in range(1, 13):
                if src == "A" and y == 2026 and m > 6:
                    break
                wob = 0.05 * (((y * 7 + m * 3) % 11) - 5) / 5
                rows.append("%s,%d-%02d,%.4f" % (src, y, m, off + 0.02 * (y - 1950) + wob))
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_the_ai_names_the_date_and_value_of_a_long_table_the_rule_does_not_know():
    plan = {"goal": "How has Mean changed, and do A and B agree?", "operations": [{"op": "long_to_wide"}],
            "columns": [{"name": "Source", "role": "segment"}, {"name": "Year", "role": "date"},
                        {"name": "Mean", "role": "target", "semantic_type": "level", "unit": "C"}],
            "primary": "Mean",
            "analyses": [{"type": "trend", "columns": ["Mean"]}, {"type": "extremes", "columns": ["A"]},
                         {"type": "agreement", "columns": ["A", "B"]}, {"type": "rank", "columns": ["Mean"]},
                         {"type": "crystal_ball", "columns": ["A"]}]}
    rep = NB.run(_two_source_csv(), "temps.csv", "", {"__plan__": plan}, "2026-07-15")
    assert rep["ok"], rep["error"]
    ap = rep["ai_plan"]
    assert ap["applied"] == ["one column per series (2 series)"], ap
    assert any("crystal_ball" in r for r in ap["refused"]), ap["refused"]
    assert rep["input"]["layout"]["kept"] == 2, "B, six months behind, is current, not discontinued"
    assert "too short" not in json.dumps(rep["story"]), "76 years of months is not a short file"
    A = rep["ai_analyses"]
    kinds = [a["type"] for a in A["items"]]
    assert kinds == ["trend", "extremes", "agreement"], (kinds, A["refused"])
    assert any(r.startswith("rank:") for r in A["refused"]), "rank needs an entity column"
    tr = A["items"][0]
    per_decade = float(tr["table"]["rows"][0][3].replace("\u2212", "-"))
    assert abs(per_decade - 0.2) < 0.01, tr["table"]       # 0.02 a year, as built
    ag = A["items"][2]
    assert "0.1 C lower" in ag["sentence"] and "nearly constant" in ag["sentence"], ag["sentence"]
    assert all(not a["graded"] for a in A["items"])
    assert "not grade" in A["note"]


def test_a_panel_analysis_sums_a_steady_set_of_entities_after_the_aggregates_go():
    plan = {"goal": "Which country emits most?",
            "operations": [{"op": "set_aside", "columns": ["iso_code"]}, {"op": "exclude_blank", "column": "iso_code"},
                           {"op": "date_from_year", "column": "year"}, {"op": "not_personal", "columns": ["population"]}],
            "columns": [{"name": "country", "role": "entity"}, {"name": "year", "role": "date"},
                        {"name": "co2", "role": "target", "semantic_type": "flow_amount", "unit": "Mt"}],
            "primary": "co2",
            "analyses": [{"type": "rank", "columns": ["country", "co2"]}, {"type": "extremes", "columns": ["co2"]}]}
    rep = NB.run(_panel_csv(), "co2.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert any("iso_code is blank" in a for a in rep["ai_plan"]["applied"]), "the row filter ran before the column went"
    rank, ext = rep["ai_analyses"]["items"]
    labels = [b["label"] for b in rank["chart"]["series"]]
    assert "World" not in labels and labels[:2] == ["Canada", "Mexico"], labels
    # 2024: Canada and Mexico each (2024 - 1800) = 224, summed = 448
    assert ext["table"]["rows"][0][1:3] == ["2024", "448"], ext["table"]["rows"][0]
    assert "the yearly total over the 2 country entries" in ext["sentence"], ext["sentence"]


def test_the_profile_lists_every_value_of_a_category_column_unless_it_is_flagged():
    rows = ["region,amount"] + ["R%02d,%d" % (i % 20, i) for i in range(200)]
    data = ("\n".join(rows) + "\n").encode()
    prof = NB.profile_for_ai(data, "r.csv")
    reg = prof["columns"][0]
    assert reg["values"] == ["R%02d" % i for i in range(20)], reg
    prof2 = NB.profile_for_ai(data, "r.csv", flagged={"region": "some values look personal"})
    assert "values" not in prof2["columns"][0]


def test_series_side_by_side_are_ranked_by_their_change_with_no_unit_across_units():
    plan = {"goal": "Which currencies moved most?", "operations": [{"op": "long_to_wide"}],
            "columns": [{"name": "REF_DATE", "role": "date"}, {"name": "VALUE", "role": "target", "semantic_type": "level",
                                                             "unit": "CAD / unit (CERI: index 1992=100)"}],
            "analyses": [{"type": "rank", "columns": ["Type of currency", "VALUE"]}, {"type": "trend", "columns": ["VALUE"]}]}
    rep = NB.run(_long_table(), "fx.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    items = {a["type"]: a for a in rep["ai_analyses"]["items"]}
    rk = items["rank"]
    assert rk["title"] == "Which series moved most" and len(rk["table"]["rows"]) == 3, rk
    changes = [float(r[5].rstrip("%").replace("\u2212", "-")) for r in rk["table"]["rows"]]
    assert changes == sorted(changes, reverse=True), changes
    assert "trend" not in items and any("8 or more complete years" in r for r in rep["ai_analyses"]["refused"]), \
        "six complete years are too few for a yearly trend, and the refusal says so"
    lay = rep["input"]["layout"]
    assert NB._col_type(rep["ai_plan"], lay["order"][0], lay)[1] == "", "two units: no single unit is printed"


def _survey_csv():
    """Sites with noise readings in dB, a rating, free-text comments and a person's email per row."""
    rows = ["site,noise_db,rating,comment,contact"]
    words = ["delivery was late", "friendly staff", "late delivery again", "staff were friendly and quick",
             "noisy street outside", "quick service"]
    for i in range(120):
        site = "North" if i % 2 else "South"
        db = (60.0 if i % 4 < 2 else 70.0) if site == "North" else 55.0
        rows.append('%s,%.1f,%d,"%s",person%d@example.com' % (site, db, 1 + i % 5, words[i % 6], i))
    return ("\n".join(rows) + "\n").encode()


def test_decibels_average_as_energy_and_text_themes_count_words_but_withheld_columns_stay_out():
    plan = {"goal": "Where is it loudest, and what do people say?",
            "columns": [{"name": "site", "role": "segment"}, {"name": "noise_db", "semantic_type": "log_scale", "unit": "dB"},
                        {"name": "comment", "semantic_type": "free_text"}, {"name": "rating", "semantic_type": "rating"}],
            "analyses": [{"type": "compare", "columns": ["noise_db"], "by": "site"},
                         {"type": "themes", "columns": ["comment"]}, {"type": "themes", "columns": ["contact"]},
                         {"type": "relationship", "columns": ["rating", "noise_db"]}, {"type": "distribution", "columns": ["rating"]}]}
    rep = NB.run(_survey_csv(), "survey.csv", "", {"__plan__": plan, "comment": "keep"}, "2026-09-15")
    assert rep["ok"], rep["error"]
    A = rep["ai_analyses"]
    items = {a["type"]: a for a in A["items"]}
    cmp_ = items["compare"]
    north = [r for r in cmp_["table"]["rows"] if r[0] == "North"][0]
    # half 60 dB, half 70 dB: 10*log10((10^6 + 10^7) / 2) = 67.4 dB, not the plain mean 65
    assert north[2] == "67.4", north
    assert "as energy" in cmp_["sentence"]
    th = items["themes"]
    assert "'delivery'" in th["sentence"] and "@" not in json.dumps(th), th["sentence"]
    # a withheld column is never named in a refusal (it reaches the planner): "a column you withheld"
    assert any(r == "themes: it names only a column you withheld" for r in A["refused"]), A["refused"]
    assert "contact" not in json.dumps(A["refused"]) + json.dumps(rep["plan_signals"]), A["refused"]
    assert "relationship" in items and "distribution" in items, A


def test_a_ranking_with_no_date_column_ranks_every_entity_and_carries_map_values():
    rows = ["country,co2"] + ["C%02d,%d" % (i, 100 - i) for i in range(30)]
    plan = {"goal": "Who emits most?", "columns": [{"name": "country", "role": "entity", "semantic_type": "geography"},
                                                   {"name": "co2", "semantic_type": "flow_amount"}],
            "analyses": [{"type": "rank", "columns": ["country", "co2"]}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "c.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    A = rep["ai_analyses"]
    assert not A["refused"], A["refused"]          # was "co2 has no numbers": an empty date column dropped every row
    rk = A["items"][0]
    assert [b["label"] for b in rk["chart"]["series"]][:3] == ["C00", "C01", "C02"], rk["chart"]
    assert len(rk["map"]["values"]) == 30 and rk["map"]["values"]["C05"] == 95.0, rk["map"]


# ---- data tests compiled from the plan's column types (contracts). THE RULE (29 Sep 2026, after an
# adversarial review blocked the cell-blanking rule): a data test never changes what the engine reads; it
# reports what failed and what the engine itself did with those rows, and signals the planner only when
# the values are evidence against the AI's reading.
def _contract_csv(n, price, extra=None):
    """n rows of date,price,units; price(i) gives the price text. extra: {column: fn(i)} appended columns."""
    extra = extra or {}
    rows = [",".join(["date", "price", "units"] + list(extra))]
    for i in range(n):
        rows.append(",".join([(datetime.date(2025, 1, 1) + datetime.timedelta(days=i)).isoformat(), str(price(i)), str(10 + i % 7)]
                             + [str(f(i)) for f in extra.values()]))
    return ("\n".join(rows) + "\n").encode()


def _contract_plan(*cols):
    return {"goal": "How is price moving?", "columns": [{"name": n, "semantic_type": t} for n, t in cols], "primary": "units"}


def _by_col(rep):
    return {t["column"]: t for t in rep["contracts"]["tests"]}


def _flagged_rows(rep):
    """The "values the data tests flagged" download as dicts (source_line, column, value, test, what happened)."""
    return list(csv.DictReader(io.StringIO(rep["downloads"].get("contract_flagged_csv", ""))))


def _engine_view(rep):
    """What the engine made of the file: counts, downloads, health and findings (the data tests' own card and
    the AI's analyses left out). Two runs that read the same bytes give the same view."""
    return json.dumps({"cleaning": rep["cleaning"], "health": rep["health"], "findings": rep["findings"],
                       "clean": rep["downloads"]["clean_csv"], "quarantine": rep["downloads"]["quarantine_csv"],
                       "roles": rep["roles"], "story": rep["story"]["what_happened"]}, sort_keys=True)


FIXTURES = os.path.join(HERE, "fixtures", "review")
FIXTURES2 = os.path.join(HERE, "fixtures", "review2")          # the second review's cases (29 Sep 2026)
PRIVACY = os.path.join(HERE, "fixtures", "privacy")            # synthetic files with personal columns
REVIEW3 = os.path.join(HERE, "fixtures", "review3")            # the final review's probes (29 Sep 2026)


def _clean_df(rep):
    """downloads.clean_csv as a table: the rows the engine kept, as it read them (what the analyses read)."""
    import pandas as pd
    return pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]))


def _median_of(rep, col, scale=1.0):
    """The engine's median of a kept column, printed the way the analyses print it."""
    import numpy as np
    v = _clean_df(rep)[col].dropna().astype(float) * scale
    return len(v), NB._fmt(float(np.median(v)))


def _fx(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


def _case(csv_name, plan_name, dec_name=None, off=None):
    """A review case (tools/fixtures/review): the file, the hand-written plan and the visitor's choices."""
    d = json.loads(_fx(dec_name)) if dec_name else {}
    d["__plan__"] = json.loads(_fx(plan_name))
    if off is not None:
        d["__contracts_off__"] = off
    rep = _run(_fx(csv_name), csv_name, "", d, "2026-09-29")
    assert rep["ok"], rep["error"]
    return rep


def test_contracts_never_change_what_the_engine_reads():
    # the blanking rule handed the engine a changed file: its duplicate check and health then ran on data the
    # visitor never sent (review B1). With every test on or every test off, the engine's view is identical.
    for csv_name, plan_name, cols in (("run2_replan.csv", "run2_plan.json", ["order_date", "units", "revenue", "discount_pct"]),
                                      ("b_duplicate_ids.csv", "b_plan.json", ["order_id", "order_date", "customer_id", "amount"]),
                                      ("a_negative_percent.csv", "a_plan.json", ["month", "margin_pct", "delay_days", "orders"])):
        on = _case(csv_name, plan_name)
        off = _case(csv_name, plan_name, off=cols)
        assert any(t["failed"] for t in on["contracts"]["tests"]), csv_name
        assert all(t["action"] == "turned off by you" for t in off["contracts"]["tests"]), off["contracts"]["tests"]
        assert _engine_view(on) == _engine_view(off), "%s: a data test changed what the engine read" % csv_name
        assert "contract_flagged_csv" not in off["downloads"]


def test_contracts_out_of_range_under_5_percent_is_a_note_not_a_signal():
    csv_ = _contract_csv(100, lambda i: 140 if i in (7, 60) else 20 + i % 50)
    rep = NB.run(csv_, "p.csv", "", {"__plan__": _contract_plan(("price", "percentage"))}, "2026-09-15")
    assert rep["ok"], rep["error"]
    t = _by_col(rep)["price"]
    assert t["failed"] == 2 and t["out_of_range"] == 2 and t["unreadable"] == 0 and t["checked"] == 100, t
    assert not t["signal"] and not t["misread"] and t["examples"] == ["140"] and "between 0 and 100" in t["test"], t
    assert t["action"].startswith("2 values are above 100: the engine ") and t["action"].endswith("the tests changed no value"), t["action"]
    assert rep["plan_signals"] == [], rep["plan_signals"]
    assert rep["input"]["rows"] == 100 and ",140," in rep["downloads"]["clean_csv"] + rep["downloads"]["quarantine_csv"]
    rows = _flagged_rows(rep)
    assert [(r["source_line"], r["column"], r["value"]) for r in rows] == [("9", "price", "140"), ("62", "price", "140")], rows
    assert all(r["test"] == "between 0 and 100" and r["what happened"].startswith("out of range; ") for r in rows), rows
    c = rep["contracts"]
    assert c["cells_flagged"] == 2 and c["line"] == "source_line" and "never change what the engine reads" in c["note"], c
    assert "before any analysis" not in c["note"] and "blanked" not in json.dumps(c) and "contract_blanked_csv" not in rep["downloads"]


def test_contracts_out_of_range_over_5_percent_is_a_signal_stating_the_fact():
    csv_ = _contract_csv(100, lambda i: 140 if i % 10 < 3 else 20 + i % 50)
    rep = NB.run(csv_, "p.csv", "", {"__plan__": _contract_plan(("price", "percentage"))}, "2026-09-15")
    assert rep["ok"], rep["error"]
    t = _by_col(rep)["price"]
    assert t["signal"] and not t["misread"] and t["problem"] == "30 of 100 values are above 100, so it may not be a 0-100 percentage", t
    sig = [x for x in rep["plan_signals"] if x["kind"] == "contract_failed"]
    assert sig == [{"kind": "contract_failed", "column": "price", "detail": t["problem"]}], sig
    assert rep["downloads"]["clean_csv"].count(",140,") + rep["downloads"]["quarantine_csv"].count(",140,") == 30, "a value was changed"


def test_contracts_unreadable_text_misreads_below_80_percent_readable():
    for bad, misread in ((20, False), (21, True)):
        csv_ = _contract_csv(100, lambda i, b=bad: "n/a %d" % i if i < b else 20 + i % 50)
        rep = NB.run(csv_, "p.csv", "", {"__plan__": _contract_plan(("price", "count"))}, "2026-09-15")
        assert rep["ok"], rep["error"]
        t = _by_col(rep)["price"]
        assert t["unreadable"] == bad and t["misread"] is misread and t["signal"] is misread, (bad, t)
        if misread:     # 79%, never rounded up to the line
            assert t["problem"] == "only 79 of 100 values can be read as numbers (79%)", t["problem"]
            assert t["action"].startswith("probably not a count: only 79 of 100"), t["action"]
        else:
            assert t["action"].startswith("20 values can't be read as numbers: the engine "), t["action"]
            assert t["action"].endswith("the AI's analyses count them as missing"), t["action"]
    assert NB.MISREAD_BELOW == 0.80 and NB.OUT_OF_RANGE_SIGNAL == 0.05


def test_contracts_identifier_count_year_and_rating_each_report_their_own_values():
    extra = {"id": lambda i: 5 if i in (3, 40) else 1000 + i,
             "cnt": lambda i: -4 if i == 9 else i,
             "yr": lambda i: 3050 if i == 15 else 2000 + i % 20,
             "stars": lambda i: 7 if i == 21 else 1 + i % 5}
    plan = _contract_plan(("id", "identifier"), ("cnt", "count"), ("yr", "year"), ("stars", "rating"))
    rep = NB.run(_contract_csv(200, lambda i: 20 + i % 50, extra), "p.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    t = _by_col(rep)
    assert t["id"]["repeated"] == 1 and t["id"]["examples"] == ["5"], t["id"]      # the repeat after the first
    assert t["id"]["action"].startswith("1 value repeats an earlier value (none in exact duplicate rows): the engine "), t["id"]["action"]
    assert t["cnt"]["out_of_range"] == 1 and t["cnt"]["examples"] == ["-4"] and t["cnt"]["action"].startswith("1 value is below 0: "), t["cnt"]
    assert t["yr"]["out_of_range"] == 1 and t["yr"]["examples"] == ["3050"] and t["yr"]["action"].startswith("1 value is outside the whole years 1000 to 2999: "), t["yr"]
    assert t["stars"]["out_of_range"] == 1 and t["stars"]["examples"] == ["7"] and "1 to 5" in t["stars"]["test"], t["stars"]
    got = {(r["source_line"], r["column"], r["value"]) for r in _flagged_rows(rep)}
    assert got == {("42", "id", "5"), ("11", "cnt", "-4"), ("17", "yr", "3050"), ("23", "stars", "7")}, got
    assert rep["contracts"]["cells_flagged"] == 4 and rep["input"]["rows"] == 200 and rep["plan_signals"] == [], rep["plan_signals"]


def test_contracts_a_test_the_visitor_turned_off_is_marked_and_lists_nothing():
    csv_ = _contract_csv(100, lambda i: 140 if i in (7, 60) else 20 + i % 50)
    dec = {"__plan__": _contract_plan(("price", "percentage"), ("units", "count")), "__contracts_off__": ["price"]}
    rep = NB.run(csv_, "p.csv", "", dec, "2026-09-15")
    assert rep["ok"], rep["error"]
    t = _by_col(rep)
    assert t["price"]["action"] == "turned off by you" and t["price"]["failed"] == 0 and t["units"]["action"] == "passed", t
    assert "contract_flagged_csv" not in rep["downloads"] and rep["contracts"]["cells_flagged"] == 0


def test_contracts_the_download_names_the_visitors_own_line_after_a_row_filter():
    # the plan drops rows before the tests run; a flagged cell still carries its line in the file sent
    rows = ["region,date,price,units"]
    for i in range(60):
        rows.append("%s,%s,%s,%d" % ("World" if i < 10 else "Canada", (datetime.date(2025, 1, 1) + datetime.timedelta(days=i)).isoformat(),
                                    "140" if i == 30 else str(20 + i % 50), 10 + i % 7))
    plan = dict(_contract_plan(("price", "percentage")), operations=[{"op": "exclude_rows", "column": "region", "values": ["World"]}])
    rep = NB.run(("\n".join(rows) + "\n").encode(), "p.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert rep["input"]["rows"] == 50, rep["input"]
    assert [(r["source_line"], r["column"], r["value"]) for r in _flagged_rows(rep)] == [("32", "price", "140")], _flagged_rows(rep)
    # the engine's own downloads number the same row the same way (review M2)
    both = list(csv.DictReader(io.StringIO(rep["downloads"]["clean_csv"]))) + list(csv.DictReader(io.StringIO(rep["downloads"]["quarantine_csv"])))
    assert [r["source_line"] for r in both if r["price"] == "140"] == ["32"], [r for r in both if r["price"] == "140"]


def test_contracts_a_flagged_columns_values_never_reach_the_card_or_the_download():
    rows = [["order_date", "customer_email", "total"]]
    for i in range(200):
        rows.append(["2025-%02d-%02d" % (1 + i % 12, 1 + i % 28), "buyer%04d@example.com" % (5 if i in (60, 120, 180) else i),
                     "%.2f" % (10 + i % 70)])
    plan = {"goal": "What do customers spend?", "primary": "total",
            "columns": [{"name": "customer_email", "semantic_type": "identifier"}, {"name": "total", "semantic_type": "duration"}]}
    for dec, word in (({}, "value withheld"), ({"customer_email": "code"}, "value coded")):
        rep = NB.run(_csv(rows[1:], rows[0]), "orders.csv", "", dict(dec, __plan__=plan), "2026-09-15")
        assert rep["ok"], rep["error"]
        t = _by_col(rep)["customer_email"]
        assert t["repeated"] == 3 and t["examples"] == [word], t             # 3 repeats of buyer0005 after the first
        dl = rep["downloads"]["contract_flagged_csv"]
        assert "@example.com" not in dl and "buyer" not in dl and dl.count(word) == 3, dl
        assert "@example.com" not in json.dumps(rep["contracts"]), rep["contracts"]


# ---- the review cases of 29 Sep 2026 (tools/fixtures/review), each against the rule above
def test_review_b_duplicate_ids_the_engine_quarantines_the_duplicates_and_uniqueness_drops():
    rep = _case("b_duplicate_ids.csv", "b_plan.json")
    assert rep["cleaning"]["rows_quarantined"] == 24, rep["cleaning"]
    assert rep["cleaning"]["quarantine_reasons"] == [{"reason": "exact_duplicates: exact duplicate row", "count": 24}], rep["cleaning"]
    uniq = next(d for d in rep["health"]["dimensions"] if d["name"] == "uniqueness")
    assert uniq["score"] < 100 and uniq["k"] == 200 and uniq["n"] == 224, uniq
    t = _by_col(rep)
    assert t["order_id"]["repeated"] == 24 and not t["order_id"]["signal"], t["order_id"]
    assert t["order_id"]["action"] == ("24 values repeat an earlier value (all in exact duplicate rows): the engine set these rows aside "
                                       "(exact_duplicates: exact duplicate row; see the set-aside file); the tests changed no value"), t["order_id"]["action"]
    assert t["customer_id"]["signal"], t["customer_id"]
    assert t["customer_id"]["problem"] == "only 58 of 200 values are unique (29%), leaving out 24 exact duplicate rows", t["customer_id"]
    assert [s["column"] for s in rep["plan_signals"] if s["kind"] == "contract_failed"] == ["customer_id"], rep["plan_signals"]


def test_review_h_repeat_buyers_codes_stay_intact():
    rep = _case("h_repeat_buyers.csv", "h_plan.json", "h_dec.json")
    assert rep["cleaning"]["rows_clean"] == 200 and rep["cleaning"]["rows_quarantined"] == 0, rep["cleaning"]
    clean = list(csv.DictReader(io.StringIO(rep["downloads"]["clean_csv"])))
    codes = [r["customer_email"] for r in clean]
    assert all(codes) and not any("@" in c for c in codes), codes[:5]                 # every row keeps its code
    assert len(set(codes)) == 172, len(set(codes))                                     # the repeat buyers keep theirs
    t = _by_col(rep)["customer_email"]
    assert t["repeated"] == 28 and not t["signal"] and "none in exact duplicate rows" in t["action"] and "the engine kept these rows" in t["action"], t
    rows = _flagged_rows(rep)
    assert len(rows) == 28 and all(r["value"] == "value coded" for r in rows), rows[:3]      # coded, not withheld
    assert rep["plan_signals"] == [], rep["plan_signals"]


def test_review_a_negative_percent_values_are_kept_and_the_type_is_questioned():
    rep = _case("a_negative_percent.csv", "a_plan.json")
    # the engine set aside the 35 rows with a negative delay (29% of 120, over its 20% limit) and refused its own
    # analysis, so the AI's analyses refuse too (integration review, 29 Sep 2026; before, they read the 85 kept
    # rows with a caveat). delay_days is a column the analyses read, so the gate is no re-plan signal
    A = rep["ai_analyses"]
    assert A["items"] == [] and A["rows"] == {"kept": 85, "set_aside": 35} and A["gate"]["over"], A
    assert A["refused"] and A["refused"][0].endswith(": the engine set aside 29.2% of the rows (35 of 120), over its 20% limit, so no "
                                                     "analysis is drawn from the rest"), A["refused"]
    # the gate tripped, so the planner is not asked again (final review, 29 Sep 2026): no data-test signal either,
    # though the card still says what the tests found
    assert rep["plan_signals"] == [], rep["plan_signals"]
    t = _by_col(rep)
    assert t["margin_pct"]["problem"] == "17 of 120 values are below 0, so it may not be a 0-100 percentage", t["margin_pct"]
    assert t["delay_days"]["problem"] == "35 of 120 values are below 0, so it may not be a duration", t["delay_days"]
    assert t["margin_pct"]["out_of_range"] == 17 and t["margin_pct"]["action"].startswith(t["margin_pct"]["problem"] + ": the engine "), \
        t["margin_pct"]["action"]


_F_GATE = ("trend, extremes: the engine set aside 30.0% of the rows (180 of 600), over its 20% limit, so no analysis is "
           "drawn from the rest")


def test_review_g_a_sparse_date_never_moves_the_axis_to_the_refund_date():
    # the engine set aside 30% of the rows (order_date is "TBD" on them) and refused its own analysis: the AI's
    # analyses refuse too (integration review, 29 Sep 2026: they had run on the 420 kept rows with a caveat), and
    # the refund date, which runs the other way, is never read. No signal at all (final review, 29 Sep 2026: the
    # order date's data test was still sent, and a re-plan answered it by setting the order date aside and reading
    # the refund date): no new plan brings those rows back
    rep = _case("g_sparse_date_wrong_axis.csv", "f_plan.json")
    A = rep["ai_analyses"]
    assert rep["story"]["headline"].startswith(NB.GATE_TRIPPED), rep["story"]["headline"]
    assert A["items"] == [] and A["refused"] == [_F_GATE] and A["date"] is None and "refund_date" not in json.dumps(A), A
    assert A["gate"] == {"over": True, "pct": 30.0, "limit": 20.0, "aside": 180, "rows": 600}, A["gate"]
    assert rep["plan_signals"] == [], rep["plan_signals"]
    assert _by_col(rep)["order_date"]["problem"] == "only 420 of 600 values read as dates (70%)"     # the card still says it
    assert NB.results_for_ai(rep)["analyses_refused"] == [_F_GATE]


def test_review_f_a_sparse_date_reports_what_the_engine_set_aside():
    rep = _case("f_sparse_date.csv", "f_plan.json")
    t = _by_col(rep)["order_date"]
    assert t["misread"] and t["unreadable"] == 180, t
    assert t["action"] == ("probably not a date: only 420 of 600 values read as dates (70%); the engine set these rows aside "
                           "(order_date_date: value matches no known date format; see the set-aside file)"), t["action"]
    assert rep["cleaning"]["rows_quarantined"] == 180, rep["cleaning"]
    assert rep["ai_analyses"]["rows"] == {"kept": 420, "set_aside": 180}, rep["ai_analyses"]["rows"]
    assert rep["ai_analyses"]["items"] == [] and rep["ai_analyses"]["refused"] == [_F_GATE], rep["ai_analyses"]
    assert rep["plan_signals"] == [], rep["plan_signals"]         # the gate tripped: the planner is not asked again
    rows = _flagged_rows(rep)
    assert len(rows) == 180 and all(r["what happened"].startswith("can't be read; set aside by the engine (order_date_date") for r in rows), rows[:2]


def test_review_c_every_download_numbers_a_row_by_the_visitors_own_line():
    # a filtered TOTAL row (line 3), a quoted line break (lines 4-5) and a blank line (6): before, the clean
    # and set-aside downloads numbered the plan's re-written file, so the row on line 7 read "5" (review M2)
    data = _fx("c_source_lines.csv")
    rep = _case("c_source_lines.csv", "c_plan.json")
    flagged = [(r["source_line"], r["column"], r["value"]) for r in _flagged_rows(rep)]
    assert flagged == [("7", "shipped", "pending carrier scan"), ("37", "shipped", "2024-02-30"), ("38", "shipped", "2024-02-31"),
                       ("39", "shipped", "2024-02-32"), ("40", "shipped", "about 21"), ("40", "units", "about 21"),
                       ("41", "units", "-4")], flagged
    text = data.decode("utf-8").splitlines()
    clean = list(csv.DictReader(io.StringIO(rep["downloads"]["clean_csv"])))
    quar = list(csv.DictReader(io.StringIO(rep["downloads"]["quarantine_csv"])))
    assert [r["source_line"] for r in clean[:3]] == ["2", "4", "8"], clean[:3]
    assert [r["source_line"] for r in quar] == ["7", "37", "38", "39", "40", "41"], quar
    for r in clean + quar:
        rec = next(csv.reader(io.StringIO("\n".join(text[int(r["source_line"]) - 1:]))))
        assert rec[0] == r["shipped"] and rec[1] == r["site"], (r["source_line"], rec, r)
    for ln, col, val in flagged:
        rec = next(csv.reader(io.StringIO("\n".join(text[int(ln) - 1:]))))
        assert val == rec[{"shipped": 0, "units": 3}[col]], (ln, rec)
    assert "TOTAL" not in rep["downloads"]["clean_csv"] + rep["downloads"]["quarantine_csv"]


def test_review_run2_counts_agree_on_the_card_the_engine_and_the_ai_facts():
    # the live bug: the card said units failed on 108 and the AI report said 99 could not be read. Both are
    # true: the engine set aside the 127 rows with an unreadable date first (9 of them held bad units too)
    # and left the other 99 unreadable units empty. The card now says exactly that.
    rep = _case("run2_replan.csv", "run2_plan.json")
    t = _by_col(rep)
    assert t["order_date"]["unreadable"] == 127 and t["units"]["unreadable"] == 108, t
    assert t["order_date"]["action"] == ("127 values can't be read as dates: the engine set these rows aside (order_date_date: value "
                                         "matches no known date format; see the set-aside file); the AI's analyses read only "
                                         "the rows the engine kept")
    assert t["units"]["action"] == ("108 values can't be read as numbers: the engine set 9 of these rows aside (order_date_date: value "
                                    "matches no known date format; see the set-aside file) and kept 99, leaving those values empty; "
                                    "the AI's analyses read only the rows the engine kept and count the 99 kept ones as missing"), t["units"]["action"]
    # the engine's own words carry the same numbers
    assert rep["cleaning"]["quarantine_reasons"] == [{"reason": "order_date_date: value matches no known date format", "count": 127}]
    assert any("108 text values" in x and x.startswith("units:") for x in rep["health"]["issues"]), rep["health"]["issues"]
    assert any(f["claim"].startswith("99 value(s) in the units column") for f in rep["findings"]), [f["claim"][:60] for f in rep["findings"]]
    ai = NB.results_for_ai(rep)
    assert ai["plan_applied"][0] == "kept the 6 columns the goal needs, set aside 1 others", ai["plan_applied"]
    assert ("data test on order_date (date): 127 values can't be read as dates: the engine set these rows aside; the AI's analyses "
            "read only the rows the engine kept") in ai["plan_applied"], ai["plan_applied"]
    assert ("data test on units (count): 108 values can't be read as numbers: the engine set 9 of these rows aside and kept 99, "
            "leaving those values empty; the AI's analyses read only the rows the engine kept and count the 99 kept ones as "
            "missing")[:200] in ai["plan_applied"], ai["plan_applied"]
    blob = json.dumps(rep["ai_analyses"]) + json.dumps(ai) + json.dumps(rep["contracts"])
    assert not re.search(r"\d currency", blob) and "currency units" not in blob, re.search(r".{60}\d currency.{20}", blob)
    pr = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "predict")
    # the sentence counts what the analysis read: the 99 kept rows with no units (the 127 undated rows were set aside)
    assert "(99 unreadable values in units are not counted)" in pr["sentence"] and "order_date are not" not in pr["sentence"], pr["sentence"]
    assert "(99 unreadable values in units are not counted)" in json.dumps(ai["analyses"]), ai["analyses"]
    assert rep["roles"]["date"] == "order_date" and not [s for s in rep["plan_signals"] if s["kind"] == "contract_failed"]


def test_review_run3_control_the_headline_is_true():
    # 10 years x 3 regions of monthly sales: every monthly change is graded too little data to judge, so the
    # engine's story has no bottom line, and its fallback said no measure could be computed, above a trend
    rep = _case("run3_control.csv", "run3_plan.json")
    h = rep["story"]["headline"]
    assert "No business measure could be computed" not in h + json.dumps(rep["story"]), h
    # what was tested, counted as it was: 5 tests over the row count and 2 columns, not "5 business measures"
    assert h.startswith("The monthly change test (the latest 12 months against the 12 before) ran 5 tests over the row "
                        "count and 2 columns (sales, units) and settled none of them: each is graded too little data to "
                        "judge."), h
    tr = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "trend")
    assert h.endswith("From the AI plan's analyses: " + NB._first_sentence(tr["sentence"])), h
    # review H3: the yearly TOTAL of every row (142,875 a year), not a sum of each region's yearly mean (11,906)
    assert tr["sentence"].startswith("sales rose by 142,542 per year over 2016 to 2025 (the yearly total over the 3 region "
                                     "entries") and "Dates from the month column." in tr["method"], tr
    assert all(f["verdict"] == "INSUFFICIENT" for f in rep["findings"] if f["kind"] == "business")


def test_a_yearly_files_headline_says_why_the_monthly_test_settled_nothing():
    rows = ["country,year,co2"] + ["%s,%d,%.2f" % (c, y, (y - 1980) * k) for c, k in (("Canada", 2), ("Mexico", 1)) for y in range(1990, 2025)]
    plan = {"goal": "How have emissions changed?", "operations": [{"op": "date_from_year", "column": "year"}], "primary": "co2",
            "columns": [{"name": "country", "role": "entity"}, {"name": "year", "role": "date", "semantic_type": "year"},
                        {"name": "co2", "role": "target", "semantic_type": "flow_amount", "unit": "Mt"}],
            "analyses": [{"type": "trend", "columns": ["co2"]}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "co2.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    h = rep["story"]["headline"]
    assert h.startswith("The monthly change test (the latest 12 months against the 12 before) ran 2 tests over the row count "
                        "and 1 column (co2) and settled none of them: each is graded too little data to judge, because the "
                        "rows are yearly, so each 12 months hold one date. From the AI plan's analyses: co2 rose by 30 Mt per "
                        "decade over 1990 to 2024 (the yearly total over the 2 country entries"), h
    tr = rep["ai_analyses"]["items"][0]
    assert tr["method"].endswith("Dates from the date column."), tr["method"]        # date_from_year made "date" of year


def test_the_analyses_date_is_only_the_plans_date_role():
    # review M2 (29 Sep 2026): a column typed date with another role (a driver, metadata) is never the axis; only
    # the plan's date role is, read from the rows the engine kept
    rows = ["order_date,ship_date,refund_date,noted,amount"]
    for i in range(400):
        d = datetime.date(2012, 1, 1) + datetime.timedelta(days=i * 11)
        rows.append("%s,%s,%s,%s,%d" % ("" if i % 7 == 0 else d.isoformat(), (d + datetime.timedelta(days=2)).isoformat(),
                                       (d + datetime.timedelta(days=40)).isoformat(), d.isoformat(), 100 + i))
    data = ("\n".join(rows) + "\n").encode()
    cols = [{"name": "order_date", "semantic_type": "date", "role": "date"}, {"name": "refund_date", "semantic_type": "date", "role": "metadata"},
            {"name": "ship_date", "semantic_type": "date", "role": "driver"}, {"name": "amount", "semantic_type": "flow_amount", "role": "target"}]
    ana = [{"type": "trend", "columns": ["amount"]}, {"type": "predict", "columns": ["amount", "ship_date"]}]
    rep = NB.run(data, "o.csv", "", {"__plan__": {"columns": cols, "analyses": ana}}, "2026-09-15")
    assert rep["ok"], rep["error"]
    A = rep["ai_analyses"]
    tr = next(a for a in A["items"] if a["type"] == "trend")
    assert A["date"] == "order_date" and tr["method"].endswith("Dates from the order_date column."), A
    # the kept rows with a blank order date drop out of the time analysis, and its sentence says how many
    blank = sum(1 for i in range(400) if i % 7 == 0)
    assert ("(%d rows with no order_date are not counted)" % blank) in tr["sentence"], tr["sentence"]
    no_role = [dict(c, role="driver") if c["name"] == "order_date" else c for c in cols]
    rep2 = NB.run(data, "o.csv", "", {"__plan__": {"columns": no_role, "analyses": ana[:1]}}, "2026-09-15")
    assert rep2["ai_analyses"]["refused"] == ["trend: no usable date column (the plan names no date column)"], rep2["ai_analyses"]
    assert rep2["ai_analyses"]["date"] is None


def test_the_analyses_count_unreadable_cells_as_missing_and_say_how_many():
    # 12 years of orders with free text in 9% of order_date and 8% of units. The engine sets aside the rows whose
    # date it cannot read (so the analyses never read them) and keeps the rows with an unreadable unit, leaving
    # the unit empty: each sentence counts what it read
    rows = []
    for i in range(2400):
        d = datetime.date(2014, 1, 1) + datetime.timedelta(days=(i * 365 * 12) // 2400)
        units = 1 + (i * 37) % 60
        rows.append([("pending carrier scan %d" % i) if i % 11 == 3 else d.isoformat(), ["North", "South"][i % 2],
                     ("about %d" % units) if i % 13 == 5 else str(units), "%.2f" % (units * 20 * (1 + 0.0004 * (d - datetime.date(2014, 1, 1)).days))])
    plan = {"goal": "How did revenue change?", "primary": "revenue",
            "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"}, {"name": "region", "semantic_type": "category", "role": "segment"},
                        {"name": "units", "semantic_type": "count", "role": "driver", "unit": "units"},
                        {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "currency (CAD)"}],
            "analyses": [{"type": "trend", "columns": ["revenue"]}, {"type": "extremes", "columns": ["units"]},
                         {"type": "distribution", "columns": ["revenue"]}]}
    rep = NB.run(_csv(rows, ["order_date", "region", "units", "revenue"]), "orders.csv", "", {"__plan__": plan}, "2026-09-29")
    assert rep["ok"], rep["error"]
    n_date, n_units = sum(1 for i in range(2400) if i % 11 == 3), sum(1 for i in range(2400) if i % 13 == 5)
    A = rep["ai_analyses"]
    assert not A["refused"], A["refused"]
    tr, ex, dist = A["items"]
    kept_units = sum(1 for i in range(2400) if i % 13 == 5 and i % 11 != 3)      # an unreadable unit on a row the engine kept
    assert A["rows"] == {"kept": 2400 - n_date, "set_aside": n_date}, A["rows"]
    assert "unreadable" not in tr["sentence"] and "order_date are" not in tr["sentence"], tr["sentence"]
    assert re.search(r"revenue rose by [\d,.]+ CAD per year", tr["sentence"]), tr["sentence"]
    assert ("(%s unreadable values in units are not counted)" % kept_units) in ex["sentence"], (kept_units, n_units, ex["sentence"])
    assert "unreadable" not in dist["sentence"], dist["sentence"]          # revenue has no unreadable value
    n, med = _median_of(rep, "revenue")
    assert "half the %s values" % format(n, ",") in dist["sentence"] and "(median %s" % med in dist["sentence"], dist["sentence"]
    assert tr["method"].endswith("Dates from the order_date column.") and "Dates from" not in dist["method"]


def test_money_units_are_written_where_a_reader_expects_them():
    assert NB._amt(933.4, "currency") == "933" and NB._amt(933.4, "Currency units") == "933"
    assert NB._amt(933.4, "local currency units") == "933" and NB._amt(31.2, "units") == "31.2" and NB._amt(7, "Index") == "7"
    assert NB._amt(933.4, "value") == "933" and NB._amt(933.4, "AMOUNT") == "933"
    assert NB._amt(933.4, "currency (CAD)") == "933 CAD" and NB._amt(933.4, "local currency units (BDT)") == "933 BDT"
    assert NB._amt(933.4, "$") == "$933" and NB._amt(-5, "€") == "−€5" and NB._amt(1234.5, "US$") == "US$1,234"
    assert NB._amt(933.4, "USD") == "933 USD" and NB._amt(31.2, "kg") == "31.2 kg" and NB._amt(5, "%") == "5%"
    assert NB._amt(10, "$", tail=False) == "$10" and NB._amt(10, "kg", tail=False) == "10" and NB._amt(float("nan"), "$") == "n/a"
    assert NB._amt(12, "") == "12"
    assert NB._diff_amt(0.648, "percentage", "%") == "0.648 percentage points" and NB._diff_amt(3, "flow_amount", "$") == "$3"


def test_a_fraction_percentage_prints_times_100_on_the_scale_its_test_chose():
    # a visit id keeps each row unique (the same rows repeated are exact duplicates, which the engine sets aside)
    rows = [["V%03d" % i, "2025-%02d-01" % (1 + i % 12), ["A", "B"][i % 2], "%.3f" % (0.05 + (i % 20) / 100.0)] for i in range(120)]
    plan = {"goal": "What is the conversion rate?", "primary": "rate",
            "columns": [{"name": "rate", "semantic_type": "percentage", "role": "target", "unit": "share"},
                        {"name": "grp", "semantic_type": "category", "role": "segment"}],
            "analyses": [{"type": "distribution", "columns": ["rate"]}, {"type": "compare", "columns": ["rate"], "by": "grp"}]}
    rep = NB.run(_csv(rows, ["visit", "month", "grp", "rate"]), "conv.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert _by_col(rep)["rate"]["test"].startswith("between 0 and 1"), _by_col(rep)["rate"]
    dist, cmp_ = rep["ai_analyses"]["items"]
    assert "(median 14.5)" in dist["sentence"] and "%" in dist["sentence"] and "0.145" not in dist["sentence"], dist["sentence"]
    assert "percentage points" in cmp_["sentence"] and re.search(r"\(\d[\d.]*%, 95% range", cmp_["sentence"]), cmp_["sentence"]


def test_results_for_ai_keeps_the_plans_steps_and_caps_the_test_lines():
    steps = ["dropped 3 rows where site is one of 1 values (37 rows left)", "set aside notes", "kept the 4 columns the goal needs, set aside 2 others"]
    tests = [{"column": "c%d" % i, "semantic_type": "count", "failed": 2, "brief": "2 values are below 0: the engine kept these rows; the tests changed no value"}
             for i in range(9)] + [{"column": "ok", "semantic_type": "count", "failed": 0, "brief": "passed"}]
    rep = {"ok": True, "ai_plan": {"goal": "g", "applied": steps}, "contracts": {"tests": tests}, "findings": []}
    ai = NB.results_for_ai(rep)
    assert ai["plan_applied"][:3] == steps and len(ai["plan_applied"]) == 8, ai["plan_applied"]
    assert all(x.startswith("data test on c") for x in ai["plan_applied"][3:]), ai["plan_applied"]
    rep["ai_plan"]["applied"] = ["step %d (%d rows left)" % (i, 100 - i) for i in range(8)]
    assert NB.results_for_ai(rep)["plan_applied"] == rep["ai_plan"]["applied"]


def _proxy_validate(results):
    """insight-proxy/src/report.js validateResults, run by Node on the payload, when both are here; else None."""
    report_js = os.path.normpath(os.path.join(SITE, "..", "insight-proxy", "src", "report.js"))
    node = shutil.which("node")
    if not node or not os.path.exists(report_js):
        return None
    script = ("import { validateResults } from %s;\n"
              "let s = ''; process.stdin.on('data', (d) => { s += d; });\n"
              "process.stdin.on('end', () => { const r = validateResults({ objective: 'o', results: JSON.parse(s) });"
              " process.stdout.write(JSON.stringify(r)); });\n") % json.dumps("file://" + report_js)
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "v.mjs")
        with open(p, "w") as fh:
            fh.write(script)
        out = subprocess.run([node, p], input=json.dumps(results), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-600:]
    return json.loads(out.stdout)


def test_results_for_ai_forecast_reaches_the_proxy_as_the_page_shows_it():
    # live bug, 29 Sep 2026: baseline_won went as the string "True" (the proxy reads only true or 'true', so the
    # writer was told the baseline lost), every point lost its month, series was a Python list's repr, verdict
    # the internal code, and coverage was missing
    rep = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    f = rep["forecast"]
    assert f["available"] and f["baseline_won"] is True, {k: f.get(k) for k in ("available", "baseline_won")}
    fc = NB.results_for_ai(rep)["forecast"]
    assert fc["baseline_won"] is True and isinstance(fc["baseline_won"], bool), fc
    # the file's name reads "[your file]" in everything /report receives (final review, 29 Sep 2026)
    assert fc["series"] == f["label"].replace(rep["input"]["name"], "[your file]") and fc["series"].startswith("monthly total amount") \
        and "['" not in fc["series"] and fc["series"].endswith(" in [your file]"), fc["series"]
    assert fc["verdict"] == {"RECOMMEND": "usable for planning", "WATCH": "not yet shown usable"}[f["verdict"]], fc["verdict"]
    # wave 4, track B step 0: ONE count of how the range held, the audit's; the core's replay count is no longer sent beside it
    assert f["audit"]["horizons"] and "coverage" not in fc and fc["audit"]["label"] == f["audit"]["label"], fc
    assert [p["date"] for p in fc["points"]] == [p["month"] for p in f["forecast"][:14]], fc["points"][:2]
    assert all(set(p) == {"date", "value", "lo", "hi"} and isinstance(p["value"], (int, float)) for p in fc["points"]), fc["points"][:2]
    for k in ("series", "verdict", "champion"):
        assert isinstance(fc[k], str) and len(fc[k]) <= (120 if k in ("champion", "coverage") else 200), (k, fc[k])
    got = _proxy_validate(NB.results_for_ai(rep))
    if got is not None:
        v = got["value"]["forecast"]
        assert got["ok"] and v["baseline_won"] is True and v["series"] == fc["series"] and v["verdict"] == fc["verdict"], v
        assert [p["date"] for p in v["points"]] == [p["date"] for p in fc["points"]], v["points"][:2]
        assert all(isinstance(p.get("value"), (int, float)) for p in v["points"]), v["points"][:2]


def test_the_profile_tells_the_planner_the_time_span_and_what_the_analyses_need():
    prof = NB.profile_for_ai(_fx("run2_replan.csv"), "run2_replan.csv")
    assert prof["time"] == {"column": "order_date", "first": "2024-01", "last": "2025-11", "months": 23, "distinct_years": 2}, prof["time"]
    lim = {x["analysis"]: x for x in prof["analysis_limits"]}
    assert len(prof["analysis_limits"]) <= 12 and all(set(x) == {"analysis", "ok", "why"} and len(x["why"]) <= 160 for x in prof["analysis_limits"])
    assert not lim["trend"]["ok"] and lim["trend"]["why"].startswith("needs %d or more complete years of values; the file spans 23 months "
                                                                     "(2024-01 to 2025-11)" % NB.TREND_MIN_YEARS), lim["trend"]
    assert not lim["extremes"]["ok"] and ("%d or more" % NB.EXTREMES_MIN_YEARS) in lim["extremes"]["why"], lim["extremes"]
    assert lim["compare"]["ok"] and lim["predict"]["ok"] and lim["distribution"]["ok"], lim
    assert "forward in time by order_date" in lim["predict"]["why"], lim["predict"]
    assert not lim["themes"]["ok"] and "order_date" not in lim["themes"]["why"], lim["themes"]      # a date is not free text
    prof3 = NB.profile_for_ai(_fx("run3_control.csv"), "run3_control.csv")
    assert prof3["time"] == {"column": "month", "first": "2016-01", "last": "2025-12", "months": 120, "distinct_years": 10}, prof3["time"]
    lim3 = {x["analysis"]: x for x in prof3["analysis_limits"]}
    assert lim3["trend"]["ok"] and lim3["extremes"]["ok"] and lim3["agreement"]["ok"], lim3
    # a flagged date column's span is never shown
    prof4 = NB.profile_for_ai(_fx("run2_replan.csv"), "run2_replan.csv", flagged={"order_date": "date of birth"})
    assert prof4["time"] is None and not {x["analysis"]: x for x in prof4["analysis_limits"]}["trend"]["ok"], prof4["time"]
    assert "2024-01" not in json.dumps(prof4["analysis_limits"]), prof4["analysis_limits"]
    # a year column stands in when no column holds dates
    prof5 = NB.profile_for_ai(_panel_csv(), "co2.csv")
    assert prof5["time"] == {"column": "year", "first": "1850-01", "last": "2024-12", "months": 2100, "distinct_years": 175}, prof5["time"]
    json.dumps(prof, allow_nan=False)


def test_signals_a_misread_date_column_says_how_few_values_read_as_dates():
    rows = ["when,units"] + ["%s,%d" % ("2025-01-%02d" % (1 + i % 28) if i % 5 == 0 else "note %d" % i, i) for i in range(1400)]
    rep = NB.run(("\n".join(rows) + "\n").encode(), "d.csv", "", {"__plan__": _contract_plan(("when", "date"))}, "2026-09-15")
    assert rep["ok"], rep["error"]
    sig = [x for x in rep["plan_signals"] if x["kind"] == "contract_failed"]
    # the engine reads a column of 20% dates as text; the planner hears how many values its date reader reads
    detail = "only 280 of 1,400 values read as dates (20%), too few for the engine to read the column as dates: it reads it as text"
    assert sig == [{"kind": "contract_failed", "column": "when", "detail": detail}], sig
    assert _by_col(rep)["when"]["action"].startswith("probably not a date: " + detail + "; the engine ")


def test_signals_a_reading_that_stood_sends_no_contract_signal():
    # 10% unreadable: the reading stands, nothing changes, and the planner is not told it was wrong
    csv_ = _contract_csv(100, lambda i: "about %d" % i if i % 10 == 0 else 20 + i % 50)
    rep = NB.run(csv_, "p.csv", "", {"__plan__": _contract_plan(("price", "count"))}, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert _by_col(rep)["price"]["unreadable"] == 10 and not _by_col(rep)["price"]["signal"]
    assert not [x for x in rep["plan_signals"] if x["kind"] == "contract_failed"], rep["plan_signals"]



def test_predict_scores_later_rows_by_earlier_ones_and_names_the_driver_that_matters():
    # review of 29 Sep 2026: random folds let a model learn from rows after the ones it is scored on; with a
    # date the rows are scored forward in time, and the table carries the error against the baseline's
    import random
    rnd = random.Random(7)
    rows = ["sold_on,price,area,rooms,region,noise"]
    for i in range(300):
        area = rnd.uniform(40, 200)
        region = ["North", "South", "East"][i % 3]
        price = 3.0 * area + (40 if region == "North" else 0) + rnd.gauss(0, 20)
        day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i * 3)
        rows.append("%s,%.1f,%.1f,%d,%s,%.3f" % (day.isoformat(), price, area, 1 + i % 5, region, rnd.random()))
    cols = [{"name": "sold_on", "semantic_type": "date", "role": "date"}, {"name": "price", "role": "target", "unit": "k"}]
    analyses = [{"type": "predict", "columns": ["price", "area", "rooms", "region", "noise"]},
                {"type": "predict", "columns": ["noise", "rooms"]}]
    rep = NB.run(("\n".join(rows) + "\n").encode(), "homes.csv", "", {"__plan__": {"goal": "What sets the price?", "columns": cols,
                                                                                  "analyses": analyses}}, "2026-09-15")
    assert rep["ok"], rep["error"]
    good, weak = rep["ai_analyses"]["items"]
    assert good["table"]["rows"][0][0] == "area", good["table"]["rows"]
    r2 = float(good["sentence"].split("R squared ")[1].split(" ")[0])
    assert 0.9 < r2 < 1.0, good["sentence"]                 # 3 per unit of area over a range of 160 against noise of 20
    assert "per unit 3" in good["table"]["rows"][0][2], good["table"]["rows"][0]
    assert "trained on earlier dates and scored on the 4 later blocks" in good["sentence"], good["sentence"]
    assert ("forward-chaining: the rows sorted by sold_on and cut into 5 blocks, each of the last 4 predicted by a model "
            "trained only on the blocks before it") in good["method"], good["method"]
    mae_row = next(r for r in good["table"]["rows"] if r[0] == "Typical held-out error (MAE)")
    mae, mae0 = float(mae_row[1]), float(mae_row[2].split("against ")[1].split(" ")[0].replace(",", ""))
    assert mae_row[2].endswith("for the mean of the training rows") and mae < mae0 / 3, mae_row
    assert next(r for r in good["table"]["rows"] if r[0] == "Rows")[1:] == ["300 of 300 used, 240 scored", "dropped: none"], good["table"]["rows"]
    assert "does not predict them better than the mean of its training rows" in weak["sentence"], weak["sentence"]
    # no date: 5 random folds, and the method says why
    rows2 = [r.split(",", 1)[1] for r in rows]
    rep2 = NB.run(("\n".join(rows2) + "\n").encode(), "homes2.csv", "", {"__plan__": {"goal": "What sets the price?", "columns": cols[1:],
                                                                                    "analyses": analyses[:1]}}, "2026-09-15")
    assert rep2["ok"], rep2["error"]
    g2 = rep2["ai_analyses"]["items"][0]
    assert "5 random folds" in g2["sentence"] and "5 random folds, because the file has no usable date" in g2["method"], g2["method"]
    # a driver that is missing on some rows: the rows dropped are counted in the table
    rows3 = [r if i % 10 else ",".join(r.split(",")[:2] + [""] + r.split(",")[3:]) for i, r in enumerate(rows)]
    rows3[0] = rows[0]
    rep3 = NB.run(("\n".join(rows3) + "\n").encode(), "homes3.csv", "", {"__plan__": {"goal": "g", "columns": cols, "analyses": analyses[:1]}}, "2026-09-15")
    g3 = rep3["ai_analyses"]["items"][0]
    assert next(r for r in g3["table"]["rows"] if r[0] == "Rows")[2] == "dropped: 30 for a missing or unreadable driver", g3["table"]["rows"]


def test_signals_an_off_menu_operation_is_op_refused():
    plan = dict(_contract_plan(("price", "count")), operations=[{"op": "drop_everything"}])
    rep = NB.run(_contract_csv(100, lambda i: 20 + i % 50), "p.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert any(x["kind"] == "op_refused" for x in rep["plan_signals"]), rep["plan_signals"]


def test_signals_a_clean_plan_on_a_clean_file_has_none():
    rep = NB.run(_contract_csv(100, lambda i: 20 + i % 50), "p.csv", "", {"__plan__": _contract_plan(("price", "percentage"))}, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert rep["plan_signals"] == [], rep["plan_signals"]


# ---- the third review of 29 Sep 2026: ONE parser, the privacy promise, and the numbers (tools/fixtures/review2,
# tools/fixtures/privacy: the reviewers' own case files). Each test below failed on the adapter they reviewed.
def _fx2(name, where=None):
    with open(os.path.join(where or FIXTURES2, name), "rb") as fh:
        return fh.read()


def _case2(csv_name, plan_name, dec_name=None, off=None, where=None):
    d = json.loads(_fx2(dec_name)) if dec_name else {}
    d["__plan__"] = json.loads(_fx2(plan_name))
    if off is not None:
        d["__contracts_off__"] = off
    rep = _run(_fx2(csv_name, where), csv_name, "", d, "2026-09-29")
    assert rep["ok"], rep["error"]
    return rep


def _items(rep, kind):
    return [a for a in (rep.get("ai_analyses") or {}).get("items") or [] if a["type"] == kind]


def test_one_parser_every_analysis_counts_the_rows_the_engine_kept_as_it_read_them():
    # the analyses used to re-read the planned text: a decimal comma read 2.29 where the engine read 1,260 (n11),
    # the k suffix went unread (n12), "($86.75)" was text (n1), and rows the engine set aside were counted (b, n3).
    # Every distribution now counts exactly the values of downloads.clean_csv, and its median is theirs.
    cases = [("n1_money.csv", "n1_plan.json", FIXTURES2), ("n3_id_repeats.csv", "n3_plan.json", FIXTURES2),
             ("n3b_line_items.csv", "n3_plan.json", FIXTURES2), ("n9_date88.csv", "n9_plan.json", FIXTURES2),
             ("n10_nan_edges.csv", "n10_plan.json", FIXTURES2), ("n11_eu_decimal.csv", "n11_plan.json", FIXTURES2),
             ("n12_k_suffix.csv", "n12_plan.json", FIXTURES2), ("n6_pct_fraction.csv", "n6_plan.json", FIXTURES2),
             ("n5_count_decimals.csv", "n5_plan.json", FIXTURES2), ("b_duplicate_ids.csv", "b_plan.json", FIXTURES),
             ("a_negative_percent.csv", "a_plan.json", FIXTURES), ("run2_replan.csv", "run2_plan.json", FIXTURES)]
    seen = 0
    for csv_name, plan_name, where in cases:
        d = {"__plan__": json.loads(_fx2(plan_name, where))}
        rep = _run(_fx2(csv_name, where), csv_name, "", d, "2026-09-29")
        assert rep["ok"], (csv_name, rep["error"])
        if (rep.get("ai_analyses") or {}).get("gate"):
            # the engine's gate stopped its own analysis (a: 35 of 120 rows set aside): nothing is computed
            assert csv_name == "a_negative_percent.csv" and rep["ai_analyses"]["items"] == [], csv_name
            continue
        pct = {c["name"] for c in d["__plan__"]["columns"] if c.get("semantic_type") == "percentage"}
        for a in _items(rep, "distribution"):
            col = a["columns"][0]
            scale = 100.0 if col in pct and _clean_df(rep)[col].dropna().abs().max() <= 1.0 else 1.0
            n, med = _median_of(rep, col, scale)
            assert ("half the %s values" % format(n, ",")) in a["sentence"] and \
                re.search(r"\(median \D{0,3}%s\b" % re.escape(med), a["sentence"]), (csv_name, col, n, med, a["sentence"])
            seen += 1
        for a in _items(rep, "compare"):
            kept = _clean_df(rep)
            by = a["title"].split(" by ", 1)[1]
            counts = kept.dropna(subset=[a["columns"][0]]).groupby(by).size()
            for row in a["table"]["rows"]:
                assert int(row[1].replace(",", "")) == int(counts[row[0]]), (csv_name, row, dict(counts))
        assert (rep.get("ai_analyses") or {}).get("rows", {}).get("kept") == rep["cleaning"]["rows_clean"], csv_name
    assert seen >= 11, seen
    n11 = _case2("n11_eu_decimal.csv", "n11_plan.json")
    assert "(median 1,260)" in _items(n11, "distribution")[0]["sentence"], _items(n11, "distribution")[0]["sentence"]


def test_one_parser_the_replay_is_the_engines_own_cleaned_table():
    # the adapter's reading of every cell is the cleaner's own rules replayed: on the rows the engine kept it is
    # its cleaned table, value for value, in every column
    import pandas as pd
    got = []
    real = NB._engine_reading

    def spy(db_path, table, rules, cr, colmap):
        R = real(db_path, table, rules, cr, colmap)
        got.append((R, cr))
        return R
    NB._engine_reading = spy
    try:
        for csv_name, plan_name in (("n1_money.csv", "n1_plan.json"), ("n11_eu_decimal.csv", "n11_plan.json"),
                                    ("n12_k_suffix.csv", "n12_plan.json"), ("n2_mixed_dates.csv", "n2_plan.json"),
                                    ("n10_nan_edges.csv", "n10_plan.json")):
            got.clear()
            rep = NB.run(_fx2(csv_name), csv_name, "", {"__plan__": json.loads(_fx2(plan_name))}, "2026-09-29")
            assert rep["ok"] and got, (csv_name, rep["error"])
            R, cr = got[-1]
            for c in cr.clean.columns:
                if c == "_quarantine_reason":
                    continue
                a, b = R.values.loc[cr.clean.index, c], cr.clean[c]
                same = (a.isna() & b.isna()) | (a.astype(object) == b.astype(object))
                assert bool(same.all()), (csv_name, c, pd.DataFrame({"replay": a[~same], "engine": b[~same]}).head())
            assert int(R.kept.sum()) == rep["cleaning"]["rows_clean"] and R.n == rep["cleaning"]["rows_in"], csv_name
    finally:
        NB._engine_reading = real


def test_one_parser_the_data_tests_read_each_cell_as_the_engine_did_and_na_is_blank():
    n12 = _case2("n12_k_suffix.csv", "n12_plan.json")
    t = _by_col(n12)["revenue"]
    assert t["failed"] == 0 and t["action"] == "passed", t                   # "8.1k" is 8,100 to the engine
    n1 = _case2("n1_money.csv", "n1_plan.json")
    t = _by_col(n1)["amount"]
    data = _fx2("n1_money.csv").decode()
    raw = [r[2] for r in csv.reader(io.StringIO(data))][1:]
    junk = sum(1 for v in raw if v and not re.search(r"\d", v) and v.strip().upper() != "N/A")
    # "($86.75)" is a number (the engine set those rows aside for their range); N/A is a blank, never unreadable
    assert t["unreadable"] == junk and t["checked"] == len(raw) - raw.count("N/A"), (t, junk)
    assert "N/A" not in json.dumps(_flagged_rows(n1)), "a placeholder was listed as an unreadable value"
    rows = ["month,region,sales"] + ["2024-%02d-01,%s,%s" % (1 + i % 12, ["North", "South"][i % 2], "N/A" if i % 10 < 3 else 1000 + i)
                                     for i in range(300)]
    plan = {"goal": "sales", "primary": "sales", "columns": [{"name": "month", "semantic_type": "date", "role": "date"},
            {"name": "region", "semantic_type": "category", "role": "segment"}, {"name": "sales", "semantic_type": "flow_amount", "role": "target"}],
            "analyses": [{"type": "compare", "columns": ["sales"], "by": "region"}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "na.csv", "", {"__plan__": plan}, "2026-09-15")
    t = _by_col(rep)["sales"]
    assert t["action"] == "passed" and t["checked"] == 210 and t["unreadable"] == 0, t      # review H6: 90 N/A are blanks
    assert not rep["plan_signals"], rep["plan_signals"]


def test_one_parser_dates_the_engine_would_not_guess_are_reported_not_passed():
    # n2: 80 of 216 dates could be day/month or month/day in a column holding both orders; the engine set them
    # aside. The date test used to say "passed" (its own reader guessed them) and the profile saw no dates at all
    rep = _case2("n2_mixed_dates.csv", "n2_plan.json")
    t = _by_col(rep)["date"]
    assert t.get("ambiguous") == 80 and t["failed"] == 80 and t["unreadable"] == 0 and not t["misread"], t
    assert t["action"].startswith("80 values could be day/month or month/day, and the column holds both orders: the engine "
                                  "set these rows aside") and t["action"].endswith("the AI's analyses read only the rows the engine kept"), t
    assert rep["ai_analyses"]["rows"] == {"kept": 136, "set_aside": 80}, rep["ai_analyses"]["rows"]
    prof = NB.profile_for_ai(_fx2("n2_mixed_dates.csv"), "n2_mixed_dates.csv")
    assert prof["time"]["column"] == "date" and prof["time"]["first"] == "2016-01", prof["time"]
    n1 = NB.profile_for_ai(_fx2("n1_money.csv"), "n1_money.csv")
    amount = [c for c in n1["columns"] if c["name"] == "amount"][0]
    # review M3: the profile said 39.7% of amount was numeric; the engine reads all but the text notes
    assert amount["numeric_share"] > 0.85 and "median" in amount, amount
    lim = {x["analysis"]: x for x in n1["analysis_limits"]}
    assert lim["distribution"]["ok"] and lim["compare"]["ok"], lim


def test_turning_the_data_tests_off_changes_no_analysis():
    # review, 29 Sep 2026: with a test off, its column's unreadable cells were no longer blanked, so the date
    # axis and the predict design changed with a switch that promises to change nothing
    for csv_name, plan_name in (("n9_date88.csv", "n9_plan.json"), ("n1_money.csv", "n1_plan.json")):
        plan = json.loads(_fx2(plan_name))
        on = _case2(csv_name, plan_name)
        off = _case2(csv_name, plan_name, off=[c["name"] for c in plan["columns"]])
        assert all(t["action"] == "turned off by you" for t in off["contracts"]["tests"])
        strip = lambda A: json.dumps({k: A.get(k) for k in ("items", "refused", "date", "rows")}, sort_keys=True)
        assert strip(on["ai_analyses"]) == strip(off["ai_analyses"]), csv_name
        assert on["ai_analyses"].get("date") == "order_date", on["ai_analyses"].get("date")


_A_PLAN = {"goal": "How is revenue moving by region?", "kind": "transactions", "understanding": "Order lines.", "primary": "revenue",
           "columns": [["order_no", "identifier", "key"], ["order_date", "date", "date"], ["region", "category", "segment"],
                       ["customer_name", "identifier", "entity"], ["customer_email", "identifier", "key"], ["phone", "count", "metadata"],
                       ["date_of_birth", "date", "driver"], ["units", "count", "driver"], ["revenue", "flow_amount", "target"],
                       ["discount_pct", "percentage", "driver"]],
           "analyses": [["compare", ["revenue"], "region"], ["compare", ["revenue"], "customer_name"], ["distribution", ["revenue"], None],
                        ["predict", ["revenue", "units", "discount_pct", "region", "customer_email", "date_of_birth"], None],
                        ["trend", ["revenue"], None], ["rank", ["revenue"], None], ["themes", ["customer_name"], None],
                        ["distribution", ["phone"], None]]}
_PERSONAL_RX = re.compile(r"Zelda|Quintus|Marisol|Octavia|Bartholomew|Ingrid|Thaddeus|Philippa|Lysander|Wilhelmina|Vanterpool|"
                          r"Okonkwo|Fairweather|Kowalczyk|Abernathy|Delacroix|examplemail|416-555|\b19[4-9]\d-\d\d")


def _titled(name):
    return " ".join(w.capitalize() for w in name.split("_"))


def _a_plan(titled):
    nm = _titled if titled else (lambda x: x)
    return {"goal": _A_PLAN["goal"], "kind": _A_PLAN["kind"], "understanding": _A_PLAN["understanding"],
            "primary": nm(_A_PLAN["primary"]),
            "columns": [{"name": nm(c), "semantic_type": t, "role": r} for c, t, r in _A_PLAN["columns"]],
            "analyses": [dict({"type": t, "columns": [nm(c) for c in cs]}, **({"by": nm(b)} if b else {}))
                         for t, cs, b in _A_PLAN["analyses"]]}


def _page_flow(data, name, plan, decisions):
    """The page's own flow: a first run with every flagged column withheld (the scan), the planner's profile
    with the scan's flags and the visitor's decisions, the run with the plan, the planner's feedback and the
    report writer's payload (whose charts and tables are what a share link carries)."""
    rep0 = NB.run(data, name, "", {}, "2026-09-15")
    fm = {f["column"]: f["kind"] for f in rep0["privacy"]["flagged"]}
    try:
        prof = json.loads(NB.profile_json(data, name, json.dumps(fm), json.dumps(decisions)))
    except TypeError:                     # an adapter before the visitor's decisions reached the profile
        prof = json.loads(NB.profile_json(data, name, json.dumps(fm)))
    rep = NB.run(data, name, "", dict(decisions, __plan__=json.loads(json.dumps(plan))), "2026-09-15")
    assert rep["ok"], rep["error"]
    res = json.loads(NB.results_json(json.dumps(rep)))
    return rep0, prof, rep, res


def _names_in(text, names):
    """Whole-word mentions of any spelling of these column names (the page's [phone number] labels aside)."""
    text = re.sub(r"\[(?:phone number|email address|withheld)\]", " ", text)
    hits = []
    for n in names:
        toks = re.findall(r"[A-Za-z0-9]+", n)
        if re.search(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]{0,3}".join(map(re.escape, toks)) + r"(?![A-Za-z0-9])", text, re.I):
            hits.append(n)
    return hits


def test_privacy_no_ai_or_share_payload_carries_a_personal_value_or_a_withheld_name():
    # name, email, phone and birth-date columns, snake_case and titled ("Date Of Birth"), all withheld and all
    # coded: the profile, the planner's feedback and the report writer's payload (with the charts and tables a
    # share link carries) hold no personal value; a withheld column is never named; a coded one is named with
    # no value and is never an axis, a group, a driver or a measure
    base = _fx2("a_personal.csv", PRIVACY)
    lines = base.decode("utf-8").split("\n", 1)
    personal = ["customer_name", "customer_email", "phone", "date_of_birth"]
    for titled in (False, True):
        data = (",".join(_titled(h) for h in lines[0].split(",")) + "\n" + lines[1]).encode() if titled else base
        heads = [_titled(c) if titled else c for c in personal]
        for how in ("withhold", "code"):
            dec = {h: how for h in heads} if how == "code" else {}
            rep0, prof, rep, res = _page_flow(data, "orders.csv", _a_plan(titled), dec)
            assert sorted(f["column"] for f in rep["privacy"]["flagged"]) == sorted(personal), rep["privacy"]["flagged"]
            assert all(f["decision"] == how for f in rep["privacy"]["flagged"]), rep["privacy"]["flagged"]
            ai = {"profile": prof, "signals": rep["plan_signals"], "report": res,
                  "share": {"charts": res["charts"], "tables": res["tables"]}}
            for where, obj in ai.items():
                blob = json.dumps(obj)
                assert not _PERSONAL_RX.search(blob), (titled, how, where, _PERSONAL_RX.search(blob).group(0),
                                                       blob[max(0, _PERSONAL_RX.search(blob).start() - 120):_PERSONAL_RX.search(blob).end() + 40])
                if how == "withhold":
                    assert not _names_in(blob, personal + heads), (titled, where, _names_in(blob, personal + heads))
            cols = {c["name"]: c for c in prof["columns"]}
            for h in heads:
                if how == "withhold":
                    assert h not in cols, (h, cols.get(h))
                else:
                    c = cols[h]
                    assert c["looks_personal"] is True and c.get("privacy_flag"), c
                    assert not set(c) & {"top_values", "values", "examples", "min", "median", "max"}, c
            assert prof["time"] and prof["time"]["column"] not in heads, prof["time"]
            A = rep["ai_analyses"]
            used = [c for a in A["items"] for c in a["columns"]] + [A.get("date")]
            assert not set(used) & set(heads), (used, heads)
            assert not _names_in(json.dumps([a["sentence"] + a["method"] for a in A["items"]]), personal + heads), A["items"]
            word = "value withheld" if how == "withhold" else "value coded"
            mine = [r for r in _flagged_rows(rep) if r["column"] in heads]
            assert mine and all(r["value"] == word for r in mine), mine[:3]          # the visitor's own download
            for t in rep["contracts"]["tests"]:
                if t["column"] in heads and t["failed"]:
                    assert t["examples"] == [word], t


def test_privacy_a_titled_withheld_birth_date_never_reaches_the_analyses_or_the_time_span():
    # review H1: the plan's date role was "Date Of Birth", the flag was on date_of_birth: the withheld birth
    # years drew the trend (n7c: "Fee fell ... over 1950 to 1999") and became the profile's time span
    for csv_name in ("n7b_withheld_date_titled.csv", "n7c_titled_dob.csv"):
        rep = _case2(csv_name, "n7b_plan.json", "n7b_dec.json")
        A = rep["ai_analyses"]
        assert not _items(rep, "trend") and not _items(rep, "extremes") and A.get("date") is None, A
        assert "trend: no usable date column (the plan's date column is a column you withheld)" in A["refused"], A["refused"]
        blob = json.dumps(A) + json.dumps(rep["plan_signals"]) + json.dumps(NB.results_for_ai(rep))
        assert not re.search(r"\b19[4-9]\d\b", blob), re.search(r".{80}\b19[4-9]\d\b.{20}", blob)
        assert not _names_in(blob, ["date_of_birth", "Date Of Birth"]), _names_in(blob, ["date_of_birth"])
        rep0 = NB.run(_fx2(csv_name), csv_name, "", {}, "2026-09-15")
        fm = {f["column"]: f["kind"] for f in rep0["privacy"]["flagged"]}
        prof = NB.profile_for_ai(_fx2(csv_name), csv_name, flagged=fm, decisions=json.loads(_fx2("n7b_dec.json")))
        assert prof["time"]["column"] == "Visit Date", prof["time"]                  # review H5: flagged first, then chosen
        assert "Date Of Birth" not in json.dumps(prof) and "date_of_birth" not in json.dumps(prof)
        first = NB.profile_for_ai(_fx2(csv_name), csv_name, flagged=fm)            # the page's first call: flags only
        assert first["time"]["column"] == "Visit Date" and not re.search(r"\b19[4-9]\d", json.dumps(first)), first["time"]
        assert "Date Of Birth" not in json.dumps(first) and "date_of_birth" not in json.dumps(first)


def test_privacy_a_coded_birth_date_is_never_the_axis_and_its_years_never_leave():
    rep = NB.run(_fx2("c_dob_axis.csv", PRIVACY), "c.csv", "", {"date_of_birth": "code", "__plan__": json.loads(json.dumps({
        "goal": "How do visits move?", "primary": "visits",
        "columns": [{"name": "date_of_birth", "semantic_type": "date", "role": "date"}, {"name": "visits", "semantic_type": "count", "role": "target"},
                    {"name": "spend", "semantic_type": "flow_amount", "role": "driver"}, {"name": "tier", "semantic_type": "category", "role": "segment"}],
        "analyses": [{"type": "trend", "columns": ["visits"]}, {"type": "agreement", "columns": ["visits", "spend"]},
                     {"type": "predict", "columns": ["spend", "visits", "tier"]}]}))}, "2026-09-15")
    assert rep["ok"], rep["error"]
    A = rep["ai_analyses"]
    assert [a["type"] for a in A["items"]] == ["predict"] and A.get("date") is None, A
    assert "5 random folds" in A["items"][0]["method"] and "date_of_birth" not in A["items"][0]["method"], A["items"][0]["method"]
    t = _by_col(rep)["date_of_birth"]
    assert t["action"].startswith("not tested: you chose to code this column") and not t["signal"], t
    res = NB.results_for_ai(rep)
    assert not re.search(r"\b19[4-9]\d\b", json.dumps(res)), re.search(r".{60}\b19[4-9]\d\b", json.dumps(res))


def test_privacy_a_quoted_cell_never_reaches_the_report_writer():
    # reviewer 2 H4: "could not be read as numbers (for example 'ask Marisol')" went to /report
    rep = NB.run(_fx2("a_personal.csv", PRIVACY), "orders.csv", "", {"__plan__": _a_plan(False)}, "2026-09-15")
    assert any("for example" in f["claim"] and "Marisol" in f["claim"] for f in rep["findings"]), "the engine's own words quote it"
    res = NB.results_for_ai(rep)
    blob = json.dumps(res)
    assert "Marisol" not in blob and "for example" not in blob, re.search(r".{80}(Marisol|for example).{40}", blob)
    assert any("could not be read as numbers and were left empty" in f["claim"] for f in res["findings"]), res["findings"]


def test_a_panel_trend_is_the_yearly_total_of_every_row_and_a_level_is_the_yearly_average():
    import numpy as np
    # review H3: run3's sales "rose 11,906 a year" (a sum of each region's yearly MEAN); the yearly totals rose 142,875
    rep = _case("run3_control.csv", "run3_plan.json")
    kept = _clean_df(rep)
    y = kept.groupby(kept["month"].astype(str).str[:4])["sales"].sum()
    slope = np.polyfit(np.asarray(y.index, float), y.values, 1)[0]
    tr = _items(rep, "trend")[0]
    # wave 4 (T1): the slope is the trend test's (Prais-Winsten), on the same yearly totals; it is within 1% of their
    # least-squares slope (142,875) and nowhere near the sum of yearly means (11,906)
    import nl_inference as NI
    rec = NI.trend_test(np.asarray(y.index, float), y.values)
    assert tr["test"]["slope"] == rec["slope"] and abs(rec["slope"] / slope - 1.0) < 0.01, (rec["slope"], slope)
    assert tr["sentence"].startswith("sales rose by %s per year" % NB._fmt(rec["slope"])), (NB._fmt(rec["slope"]), tr["sentence"])
    ex = _items(rep, "extremes")[0]
    assert ex["table"]["rows"][0][1:3] == [y.idxmax(), NB._fmt(y.max())], (ex["table"]["rows"][0], y.max())
    # a level (GDP per country) is the yearly average over the countries, and the sentence says so
    rep = _case2("n4_year_panel.csv", "n4y_plan.json")
    tr, ex = _items(rep, "trend")[0], _items(rep, "extremes")[0]
    assert "(the yearly average over the 6 country entries" in tr["sentence"], tr["sentence"]
    kept = _clean_df(rep)
    m = kept.groupby(kept["date"].astype(str).str[:4])["gdp_bn"].mean()
    assert ex["table"]["rows"][0][1:3] == [m.idxmax(), NB._fmt(m.max())], (ex["table"]["rows"][0], m.max())


def test_a_percentage_is_read_on_one_scale_and_a_percent_sign_is_never_multiplied_again():
    # reviewer 1 H4 / reviewer 2 H7: 239 fractions and one stray 1.5 read on the 0-100 scale ("median 0.139%")
    rep = _case2("n6b_pct_stray.csv", "n6_plan.json")
    dist = _items(rep, "distribution")[0]
    assert "(median 13.9)" in dist["sentence"] and "%" in dist["sentence"], dist["sentence"]
    t = _by_col(rep)["conversion_rate"]
    assert t["scale"] == 1 and t["out_of_range"] == 1 and t["examples"] == ["1.5"] and not t["signal"], t
    # rates written with a % sign under 1%: read as written (0.3% to 0.9%), never x100 again
    rows = ["id,month,region,rate"] + ["%d,2024-%02d-01,%s,%.2f%%" % (i, 1 + i % 12, ["N", "S"][i % 2], 0.3 + (i % 7) / 10) for i in range(120)]
    plan = {"goal": "rate", "primary": "rate", "columns": [{"name": "month", "semantic_type": "date", "role": "date"},
            {"name": "region", "semantic_type": "category", "role": "segment"}, {"name": "rate", "semantic_type": "percentage", "role": "target", "unit": "%"}],
            "analyses": [{"type": "distribution", "columns": ["rate"]}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "pct.csv", "", {"__plan__": plan}, "2026-09-15")
    assert "(median 0.6)" in rep["ai_analyses"]["items"][0]["sentence"], rep["ai_analyses"]["items"][0]["sentence"]
    # fractions and percents of the same quantity side by side: flagged as mixed, and the planner is told
    rows = ["id,month,region,rate"] + ["%d,2024-%02d-01,%s,%s" % (i, 1 + i % 12, ["N", "S"][i % 2],
                                       ("%.3f" % (0.1 + (i % 9) / 100)) if i % 2 else ("%.1f" % (10 + i % 9))) for i in range(120)]
    rep = NB.run(("\n".join(rows) + "\n").encode(), "mix.csv", "", {"__plan__": plan}, "2026-09-15")
    t = _by_col(rep)["rate"]
    assert t["mixed_scale"] == {"fractions": 60, "percents": 60} and t["signal"], t
    assert any(s["column"] == "rate" and "mixes two scales" in s["detail"] for s in rep["plan_signals"]), rep["plan_signals"]
    assert "mixes two scales" in rep["ai_analyses"]["items"][0]["sentence"], rep["ai_analyses"]["items"][0]["sentence"]


def test_a_ranking_of_transactions_ranks_each_entitys_yearly_total():
    # review: transactions were ranked by the single rows of their latest day ("all 1 region entries")
    rep = _case2("n1_money.csv", "n1r_plan.json")
    rk = _items(rep, "rank")[0]
    kept = _clean_df(rep)
    tot = kept[kept["order_date"].astype(str).str[:4] == "2024"].groupby("region")["amount"].sum().sort_values(ascending=False)
    head = ", ".join("%s (%s)" % (k, NB._amt(v, "USD")) for k, v in tot.head(3).items())
    assert rk["sentence"].startswith("In 2024 the largest yearly total amount by region were %s; together 100%% of the total "
                                     "over all 3 region entries" % head), (head, rk["sentence"])


def test_predict_uses_the_segment_it_is_asked_to_use():
    rep = _case2("n1_money.csv", "n1_plan.json")
    pr = _items(rep, "predict")
    assert pr and pr[0]["sentence"].startswith("A straight-line model of amount from region"), rep["ai_analyses"]["refused"]
    # an analysis that needs a column the plan set aside says so
    plan = json.loads(_fx2("n1_plan.json"))
    plan["operations"] = [{"op": "set_aside", "columns": ["region"]}]
    plan["analyses"] = [{"type": "distribution", "columns": ["region"]}]
    rep = NB.run(_fx2("n1_money.csv"), "n1_money.csv", "", {"__plan__": plan}, "2026-09-29")
    assert rep["ai_analyses"]["refused"] == ["distribution: the plan set region aside, so the engine did not read it"], rep["ai_analyses"]


def test_the_headline_claims_no_direction_the_range_does_not_show_and_yearly_rows_are_not_months():
    import random
    rnd = random.Random(11)
    rows = ["year,sales"] + ["%d,%.1f" % (y, 1000 + 0.2 * (y - 1995) + rnd.gauss(0, 40)) for y in range(1995, 2025)]
    plan = {"goal": "How have sales moved?", "primary": "sales", "operations": [{"op": "date_from_year", "column": "year"}],
            "columns": [{"name": "year", "semantic_type": "year", "role": "date"}, {"name": "sales", "semantic_type": "flow_amount", "role": "target"}],
            "analyses": [{"type": "trend", "columns": ["sales"]}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "sales.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    tr = _items(rep, "trend")[0]
    # review M8: the 95% range spans zero, so no rise or fall is claimed
    assert tr["sentence"].startswith("sales shows no clear rise or fall over 1995 to 2024") and " rose " not in tr["sentence"], tr["sentence"]
    h = rep["story"]["headline"]
    # review M9: 30 yearly values are not "30 months of history"
    assert h == ("30 yearly values, 1995 to 2024: the engine's monthly change test does not apply. From the AI plan's "
                 "analyses: " + NB._first_sentence(tr["sentence"])), h
    blob = json.dumps(rep["story"]) + rep["forecast"]["reason"]
    assert "30 months" not in blob and "30 yearly values are not a monthly series" in blob, blob
    # a trend over the years is never denied by the engine's "nothing can be said about change over time"
    n4 = _case2("n4_year_panel.csv", "n4_plan.json")
    assert _items(n4, "trend") and "nothing can be said about change over time" not in json.dumps(n4["story"]), n4["story"]["cannot_answer"]


def test_analysis_limits_offer_only_what_the_planner_can_choose_and_a_complete_year_has_12_months():
    rows = ["month,sales"]
    for y in range(2017, 2025):
        for m in range(1, 13):
            if not (y == 2020 and m == 3):                          # 2020 has 11 months: not a complete year
                rows.append("%d-%02d-01,%d" % (y, m, 1000 + 10 * (y - 2017) + m))
    data = ("\n".join(rows) + "\n").encode()
    prof = NB.profile_for_ai(data, "m.csv")
    lim = {x["analysis"]: x for x in prof["analysis_limits"]}
    assert set(lim) <= set(NB.ANALYSIS_TYPES) and "rank_series" not in lim, sorted(lim)
    assert not lim["trend"]["ok"] and "7 complete calendar years" in lim["trend"]["why"], lim["trend"]
    plan = {"goal": "sales", "primary": "sales", "columns": [{"name": "month", "semantic_type": "date", "role": "date"},
            {"name": "sales", "semantic_type": "flow_amount", "role": "target"}], "analyses": [{"type": "trend", "columns": ["sales"]}]}
    rep = NB.run(data, "m.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ai_analyses"]["refused"] == ["trend: no series with 8 or more complete years of values"], rep["ai_analyses"]


def test_local_currency_units_are_not_a_unit():
    for u in ("LCU", "current LCU", "Local currency units (LCU)", "constant 2015 LCU"):
        assert NB._amt(933.4, u) == "933", (u, NB._amt(933.4, u))
    assert NB._amt(933.4, "CAD") == "933 CAD" and NB._amt(933.4, "local currency units (BDT)") == "933 BDT"


# ---- the integration of 29 Sep 2026: the profile after the choices, a withheld column drives no rule, the
# engine's gate stops the AI's analyses, and the adapter's personal-column check (each failed on the adapter
# the two builders left)
def _notes_file():
    """200 orders whose free-text notes mostly hold a date: the engine's own rules give notes a date rule when
    it reads them. Rows 7 and 8 differ only in their note; row 9 repeats row 8 exactly."""
    rows = ["order_date,notes,amount"]
    for i in range(200):
        d = datetime.date(2024, 1, 1) + datetime.timedelta(days=2 * i)
        note = (d + datetime.timedelta(days=3)).isoformat() if i % 5 else "  call back   Tuesday "
        if i % 11 == 0:
            note = ""
        rows.append("%s,%s,%d" % (d.isoformat(), note, 10 + i % 17))
    rows[8] = rows[7].split(",")[0] + ",left at door," + rows[7].split(",")[2]
    rows.insert(10, rows[9])
    return ("\n".join(rows) + "\n").encode()


def test_integration_a_withheld_column_never_sets_a_row_aside_or_changes_one():
    data = _notes_file()
    kept = NB.run(data, "orders.csv", "", {"notes": "keep"}, "2026-09-15")
    assert kept["ok"], kept["error"]
    # kept, the engine's own rules read the notes: rows whose note is not a date are set aside (the proof the
    # file exercises the rule), and its spaces are fixed
    assert any(q["reason"].startswith("notes_date:") for q in kept["cleaning"]["quarantine_reasons"]), kept["cleaning"]
    for dec in ({}, {"notes": "withhold"}):
        rep = NB.run(data, "orders.csv", "", dec, "2026-09-15")
        assert rep["ok"], rep["error"]
        assert [f["decision"] for f in rep["privacy"]["flagged"] if f["column"] == "notes"] == ["withhold"], rep["privacy"]
        c = rep["cleaning"]
        # only the exact duplicate row goes: no rule read the withheld notes, and the two rows that differ only in
        # their note both stay (each note is its own code, so the duplicate check keeps them apart, as in the file)
        assert c["quarantine_reasons"] == [{"reason": "exact_duplicates: exact duplicate row", "count": 1}], c["quarantine_reasons"]
        assert c["rows_in"] == 201 and c["rows_clean"] == 200, c
        assert not [f for f in c["fixes"] if f["column"] == "notes"], c["fixes"]
        assert "notes" not in (rep.get("quality") or {}).get("fixes_by_rule", {}), rep.get("quality")
        # the data-health check still counts its blanks (and names it, never a value)
        assert any(x.startswith("notes:") and "40" in x for x in rep["health"]["issues"]) or \
            any(x.startswith("notes:") for x in rep["health"]["issues"]), rep["health"]["issues"]
        blob = json.dumps(rep)
        assert "call back" not in blob and not re.search(r"WITHHELD[A-Z]+", blob), re.search(r".{60}WITHHELD[A-Z]+.{20}", blob)
        assert "notes" not in _csv_header(rep["downloads"]["clean_csv"])


def test_integration_the_planner_profile_is_made_after_the_choices():
    rows = ["order_date,customer_email,notes,staff_name,spend"]
    staff = ["Dana Whitfield", "Marco Bellini", "Ines Duarte", "Tom Kaczmarek"]
    for k in range(120):
        d = datetime.date(2024, 1, 1) + datetime.timedelta(days=3 * k)
        rows.append("%s,buyer%02d@example.org,%s,%s,%.2f" % (d.isoformat(), k % 30, "call 416-555-%04d" % k if k % 3 == 0 else "gift",
                                                             staff[k % 4], 5 + (k * 7) % 90))
    data = ("\n".join(rows) + "\n").encode()
    rep0 = NB.run(data, "o.csv", "", {}, "2026-09-15")                     # the scan: every flagged column withheld
    fm = {f["column"]: f["kind"] for f in rep0["privacy"]["flagged"]}
    assert set(fm) == {"customer_email", "notes", "staff_name"}, fm
    passes = []
    real = NB._engine_profile_pass

    def counted(*a, **k):
        passes.append(a[2] if len(a) > 2 else k.get("decisions"))
        return real(*a, **k)
    NB._engine_profile_pass = counted
    try:
        # the default choices reuse the scan's reading: no second pass of the engine
        first = json.loads(NB.plan_profile_json(data, "o.csv", json.dumps(fm), json.dumps({}), "2026-09-15"))
        assert passes == [] and [c["name"] for c in first["profile"]["columns"]] == ["order_date", "spend"], (passes, first)
        chosen = {"customer_email": "code", "notes": "withhold", "staff_name": "keep"}
        got = json.loads(NB.plan_profile_json(data, "o.csv", json.dumps(fm), json.dumps(chosen), "2026-09-15"))
        assert len(passes) == 1, passes                                      # other choices: one profile pass
    finally:
        NB._engine_profile_pass = real
    prof = got["profile"]
    cols = {c["name"]: c for c in prof["columns"]}
    assert list(cols) == ["order_date", "customer_email", "staff_name", "spend"], list(cols)
    assert "notes" not in json.dumps(prof) and "416-555" not in json.dumps(prof) and "example.org" not in json.dumps(prof)
    ce = cols["customer_email"]
    assert set(ce) <= {"name", "filled", "distinct", "blank", "numeric_share", "date_share", "integers", "percent_sign",
                       "looks_personal", "privacy_flag"} and ce["looks_personal"] is True and ce["privacy_flag"], ce
    sn = cols["staff_name"]
    assert sn["looks_personal"] is False and "privacy_flag" not in sn and sorted(sn["top_values"]) == sorted(staff), sn
    assert cols["spend"] == {c["name"]: c for c in first["profile"]["columns"]}["spend"], (cols["spend"], first)
    assert prof["time"]["column"] == "order_date", prof["time"]
    assert got["landed"] == {"order_date": "order_date", "customer_email": "customer_email", "notes": "notes",
                             "staff_name": "staff_name", "spend": "spend"}, got["landed"]
    # a coded column is profiled as the engine reads it, as its codes: the facts are the run's under these choices
    assert ce["numeric_share"] == 0.0 and ce["distinct"] == 30, ce


def _people_file():
    """Columns of people the engine's scan misses (a member, a person, a user: names whose first word is not on
    its list of given names; street addresses under a neutral heading), beside look-alikes that are not people."""
    first = ["Marisol", "Quintus", "Octavia", "Thaddeus", "Philippa", "Lysander", "Wilhelmina", "Ingrid"]
    last = ["Fairweather", "Vanterpool", "Abernathy", "Delacroix", "Kowalczyk", "Okonkwo-Reyes"]
    cities = ["New York", "Los Angeles", "San Diego", "Buenos Aires"]
    products = ["Blue Widget", "Red Chair", "Oak Table", "Steel Lamp"]
    rows = []
    for i in range(160):
        nm = "%s %s" % (first[i % 8], last[i % 6])
        rows.append([nm, "%s %s" % (first[(i + 3) % 8], last[(i + 1) % 6]), "%s %s" % (first[(i + 5) % 8], last[i % 6]),
                     "%d %s Street" % (10 + i, ["Queen", "King", "Maple", "Birch"][i % 4]),
                     "SKU-%05d" % (i % 40), products[i % 4], str(3 + i % 9), "Gold" if i % 3 else "Silver",
                     "Firefox/%d.0" % (100 + i % 30), cities[i % 4],
                     (datetime.date(2020, 1, 1) + datetime.timedelta(days=i)).isoformat(), "%.2f" % (20 + i % 50)])
    return _csv(rows, ["member", "person", "user", "residence", "product_name", "product_name_2", "customer_count", "member_tier",
                       "user_agent", "city", "member_since", "amount"])


def test_integration_the_adapter_flags_people_the_engine_missed_and_only_them():
    data = _people_file()
    rep = NB.run(data, "people.csv", "", {}, "2026-09-15")
    assert rep["ok"], rep["error"]
    fl = {f["column"]: f for f in rep["privacy"]["flagged"]}
    assert {"member", "person", "user", "residence"} <= set(fl), fl
    assert fl["member"]["kind"] == "person's name" and fl["residence"]["kind"] == "street address", fl
    assert all(f["decision"] == "withhold" for f in fl.values()), fl                      # the same default
    for not_people in ("product_name", "product_name_2", "customer_count", "member_tier", "user_agent", "city", "member_since", "amount"):
        assert not_people not in fl, (not_people, fl.get(not_people))
    blob = json.dumps({"results": NB.results_for_ai(rep), "signals": rep.get("plan_signals"), "story": rep["story"]})
    assert not _PERSONAL_RX.search(blob), _PERSONAL_RX.search(blob).group(0)
    hdr = _csv_header(rep["downloads"]["clean_csv"])
    assert not {"member", "person", "user", "residence"} & set(hdr), hdr
    # the visitor's choice applies to a column the adapter flagged as to one the engine flagged
    coded = NB.run(data, "people.csv", "", {"member": "code", "person": "keep"}, "2026-09-15")
    clean = list(csv.DictReader(io.StringIO(coded["downloads"]["clean_csv"])))
    assert all(r["member"].startswith("rdc_") for r in clean) and len({r["member"] for r in clean}) == 24, clean[:2]
    assert clean[0]["person"] == "Thaddeus Vanterpool" and "user" not in clean[0], clean[0]
    prof = NB.profile_for_ai(data, "people.csv", flagged={f: v["kind"] for f, v in fl.items()}, decisions={"member": "code", "person": "keep"})
    pc = {c["name"]: c for c in prof["columns"]}
    assert "user" not in pc and "residence" not in pc and pc["member"]["looks_personal"] is True and "top_values" not in pc["member"], pc.get("member")
    assert pc["person"]["looks_personal"] is False and pc["person"]["top_values"], pc["person"]


def test_integration_the_personal_column_check_reads_names_and_shapes_both_ways():
    H = lambda h: NB._person_hint(NB._header_tokens(h), h)[0]
    assert NB._person_hint(NB._header_tokens("member")) == (True, False)
    assert NB._person_hint(NB._header_tokens("First Name")) == (True, True)
    for h in ("agent_name", "customerName", "technician", "attendee", "salesperson", "Sales Rep", "agent", "driver", "staff",
              "assigned_to", "assignee", "created_by", "sold_by", "author", "who", "owner", "Account Manager", "Salesperson Assigned",
              "customer_2", "nombre", "Nombre del cliente", "kunde", "cliente", "Prénom", "Müşteri", "имя", "客户姓名", "이름"):
        assert H(h), h
    for h in ("product_name", "customer_id", "user_agent", "member_since", "customer_count", "store_name", "amount", "owner_city",
              "member_tier", "customer_type_name", "Stylist", "vendor", "Guest Count"):
        assert not H(h), h
    for v in ("Marisol Fairweather", "marisol fairweather", "MARISOL FAIRWEATHER", "Quintus", "J. Smith", "Fairweather, Marisol",
              "Mary-Jane O'Neil", "Anne B. Okonkwo-Reyes", "Maria de la Cruz", "Anne van der Berg", "张伟", "محمد علي", "Иван Петров",
              "Nguyễn Văn An", "Seán Ó Briain"):
        assert NB._person_value(v), v
    for v in ("SKU-00012", "AB 1234", "ACME CORP", "Mozilla/5.0", "J. K.", "Gold Member", "Sales Manager", "Google Chrome",
              "Northwind Traders", "Sales Team", "2024-01-05", "Card", "EFT", "Cheque"):
        assert not NB._person_value(v), v
    assert NB._EMAIL_VALUE.match("zelda.v@examplemail.test") and not NB._EMAIL_VALUE.match("not an email")
    assert NB._PHONE_VALUE.match("(416) 555-0199") and NB._PHONE_VALUE.match("+44 20 7946 0958")
    assert not NB._PHONE_VALUE.match("2024-01-05") and not NB._PHONE_VALUE.match("4165550199")
    for v in ("1234-5678-9012", "1234 5678 9012 3456", "3782 822463 10005"):
        assert NB._GROUPED_NUMBER.match(v), v                       # an account or card number, never a phone
    assert not NB._GROUPED_NUMBER.match("416-555-0199") and not NB._GROUPED_NUMBER.match("123-456-7890")
    for v in ("12 Queen St W", "4500 Maple Avenue, Unit 3", "100 5th Ave", "1 Microsoft Way", "12 Oak Ct", "12 Oak Ct, Unit 3",
              "45 St Clair Ave E", "221B Baker Street", "12 rue de Rivoli"):
        assert NB._street_value(v), v
    for v in ("Queen Street", "12 units", "12 ct", "12 Ct Paper Towels", "10 Sq Ft Tile", "3 Way Switch", "2 Way Radio",
              "12 Pack Dr Pepper", "12 Large Eggs 6 Ct", "12 Oak Ct 24"):
        assert not NB._street_value(v), v


def test_final_review_the_engines_gate_sends_no_signal_and_no_replan():
    # the narrow re-plan signal the gate once sent ("setting aside follow_up may let them run") is gone (final
    # review, 29 Sep 2026: with two side dates unreadable on the same rows it said one column alone held them back,
    # which was false), and so is every other signal once the gate trips
    import random as _random
    rnd = _random.Random(3)
    rows = ["order_date,follow_up,amount"]
    for i in range(400):
        d = datetime.date(2016, 1, 1) + datetime.timedelta(days=9 * i)
        fu = (d + datetime.timedelta(days=30)).isoformat() if rnd.random() > 0.35 else rnd.choice(["asap", "next week", "tbd", "later"])
        rows.append("%s,%s,%.2f" % (d.isoformat(), fu, 150 + rnd.random() * 20))
    plan = {"goal": "How has order value moved?", "primary": "amount",
            "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                        {"name": "follow_up", "semantic_type": "date", "role": "metadata"},
                        {"name": "amount", "semantic_type": "percentage", "role": "target"}],
            "operations": [{"op": "keep_rows", "column": "nope", "values": ["x"]}],
            "analyses": [{"type": "trend", "columns": ["amount"]}, {"type": "distribution", "columns": ["amount"]}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "fu.csv", "", {"__plan__": plan}, "2026-09-29")
    assert rep["ok"] and rep["story"]["headline"].startswith(NB.GATE_TRIPPED), rep["story"]["headline"]
    A = rep["ai_analyses"]
    assert A["items"] == [] and A["refused"] == ["trend, distribution: the engine set aside 35.2% of the rows (141 of 400), over its "
                                                 "20% limit, so no analysis is drawn from the rest"], A
    assert A["gate"] == {"over": True, "pct": 35.25, "limit": 20.0, "aside": 141, "rows": 400}, A["gate"]
    # a misread (amount as a 0-100 percentage) and a refused step were found, and still nothing is sent
    assert rep["ai_plan"]["refused"] and _by_col(rep)["amount"]["signal"], (rep["ai_plan"]["refused"], _by_col(rep)["amount"])
    assert rep["plan_signals"] == [], rep["plan_signals"]
    # the two side dates of the review (ship and delivery "pending" on the same rows) and the review's f and g
    for csv_name, plan_name, where in (("two_side_dates.csv", "two_side_plan.json", REVIEW3), ("f_sparse_date.csv", "f_plan.json", FIXTURES),
                                       ("g_sparse_date_wrong_axis.csv", "f_plan.json", FIXTURES)):
        r = _run(_fx2(csv_name, where), csv_name, "", {"__plan__": json.loads(_fx2(plan_name, where))}, "2026-09-15")
        assert r["ok"] and r["ai_analyses"]["gate"]["over"] and "replan" not in r["ai_analyses"]["gate"], (csv_name, r["ai_analyses"])
        assert r["plan_signals"] == [], (csv_name, r["plan_signals"])
    # the same file with a clean follow-up: the gate holds, and the plan's misread is a signal as before
    ok_rows = [rows[0]] + [",".join([r.split(",")[0], r.split(",")[0], r.split(",")[2]]) for r in rows[1:]]
    rep2 = NB.run(("\n".join(ok_rows) + "\n").encode(), "fu.csv", "", {"__plan__": plan}, "2026-09-29")
    assert "gate" not in (rep2.get("ai_analyses") or {}) and [x["kind"] for x in rep2["plan_signals"]][:1] == ["contract_failed"], rep2["plan_signals"]


def _review3_module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location("review3_" + name, os.path.join(REVIEW3, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_final_review_the_personal_column_check_misses_none_of_the_reviewers_cases():
    # tools/fixtures/review3/detector_probe.py: before this fix 28 personal cases were missed (lower-case names,
    # initials, other scripts, "Fairweather, Marisol", one person, "technician", "assigned_to", "nombre" ...) and
    # the adapter added 10 false positives ("12 ct", "3 Way Switch", "Gold Member", "Sales Manager" ...)
    D = _review3_module("detector_probe")
    from northledger import engagement as _E
    missed, added = [], []
    for key, (_h, _make, want) in D.CASES.items():
        _hdr, eng, add = D.run_case(key, NB, _E)
        if want == "personal" and eng == "-" and add == "-":
            missed.append(key)
        if key in D.ADAPTER_MUST_NOT_ADD and add != "-":
            added.append((key, add))
        if want == "limit":
            # the page's example of what the scan can miss (src/js/50-try.js AI_CONSENT) must stay a miss
            assert eng == "-" and add == "-", (key, eng, add)
    assert not missed, missed
    assert not added, added
    assert D.run_case("account_4_4_4", NB, _E)[2] == "account_number"
    assert NB._KIND_LABEL["account_number"] == "account or card number"


def test_final_review_withheld_codes_keep_the_files_duplicates():
    # tools/fixtures/review3/codes_probe.py: the codes were made after spaces were collapsed and placeholders
    # blanked, so the health counted 137 exact duplicate rows where the file has 100, and 137 rows were set
    # aside. Each value byte for byte is its own code: the health's count is the file's own, and the column only
    # keeps otherwise-identical rows apart
    C = _review3_module("codes_probe")
    got = C.probe(NB)
    off, held, kept = got["codes off"], got["withheld"], got["kept"]
    assert off[:2] == (100, 150) and kept[:2] == (100, 150), (off, kept)
    assert held[:2] == (100, 100), held
    assert held[0] == off[0]
    # its empty cells are still counted; its values are not read (no mixed-content or spelling line)
    assert held[2] == ["notes: 19.9% null-like (269 of 1,350 values)."], held[2]


def test_final_review_no_ai_payload_holds_the_files_name():
    # the planner's profile said "private_mix.csv", and the engine's sentences in /report said it on every finding
    # ("22 values in the amount column of private_mix.csv ..."): "[your file]" stands in for both
    data = _email_file()
    rep = NB.run(data, "acme-payroll-march.csv", "", None, "2026-01-10")
    assert rep["ok"] and "acme-payroll-march.csv" in json.dumps(rep["findings"]), "the engine's own words no longer name the file"
    res = NB.results_for_ai(rep)
    assert "acme-payroll" not in json.dumps(res) and res["input"]["name"] == "[your file]", json.dumps(res)[:300]
    assert "[your file]" in json.dumps(res["findings"]) and "acme-payroll" not in NB.results_json(rep)
    for dec in (None, {"customer_email": "code"}):
        prof = NB.profile_for_ai(data, "acme-payroll-march.csv", decisions=dec)
        assert prof["ok"] and prof["name"] == "[your file]" == NB.FILE_WORD, prof.get("name")
        assert "acme-payroll" not in json.dumps(prof), json.dumps(prof)[:200]
    pj = NB.plan_profile_json(data, "acme-payroll-march.csv", "{}", "{}", "2026-01-10")
    assert "acme-payroll" not in pj and json.loads(pj)["profile"]["name"] == "[your file]", pj[:200]


def test_contract_a_limitation_is_cut_after_the_files_name_is_swapped():
    # the cross-repo contract test of 30 Sep 2026: each limitation was cut to 240 characters (the worker's cap) BEFORE
    # "f3_eur.csv" (10 characters) was swapped for "[your file]" (11), so two arrived at 241. Cut after the swap: never
    # over 240, never through the placeholder, and a line within the cap stays whole
    rep = NB.run(_email_file(), "f3_eur.csv", "", None, "2026-01-10")
    assert rep["ok"], rep.get("error")
    n, name = 240, "f3_eur.csv"
    texts = ["z" * (n - len(name)) + name,                 # 240 before the swap, 241 after: the cut ends before the placeholder
             "A short line about " + name + ".",            # within the cap: whole, the placeholder in place
             "w" * 300 + " " + name,                        # a long line: cut at 240, the name past the cut
             name + " " + "v" * 300]                        # the placeholder first, then cut at 240
    rep["limitations"] = [{"kind": "data", "finding_ids": [], "text": t} for t in texts]
    got = NB.results_for_ai(rep)["limitations"]
    want = ["z" * (n - len(name)), "A short line about [your file].", "w" * n, ("[your file] " + "v" * 300)[:n]]
    assert got == want, [(len(x), x[-24:]) for x in got]
    assert all(len(x) <= n and "f3_eur" not in x and x.count("[") == x.count("[your file]") for x in got), got


# ---- option B (owner's decision, 29 Sep 2026): the AI may read a flagged column's real values once the visitor has
# agreed on the page (src/js/50-try.js: a ticked box names the kept columns). The adapter's part: a kept flagged column
# is profiled and analysed like any column, a withheld one stays out, and the report writer is told once, in one line
# that names the columns and holds no value, which personal columns the visitor chose to send.
_OPTIN_STAFF = ["Dana Whitfield", "Marco Bellini", "Ines Duarte", "Tom Kaczmarek"]


def _optin_file():
    rows = ["order_date,region,customer_email,notes,staff_name,spend"]
    for k in range(160):
        d = datetime.date(2024, 1, 1) + datetime.timedelta(days=3 * k)
        s = (k // 2) % 4
        rows.append("%s,%s,buyer%02d@example.org,%s,%s,%.2f" % (d.isoformat(), ["North", "South", "East"][k % 3], k % 30,
                                                                  "call 416-555-%04d" % k if k % 3 == 0 else "gift",
                                                                  _OPTIN_STAFF[s], 20 + 15 * s + (k * 7) % 13))
    return ("\n".join(rows) + "\n").encode()


def test_optin_a_kept_column_is_profiled_and_analysed_like_any_column_and_the_writer_is_told():
    data = _optin_file()
    rep0 = NB.run(data, "o.csv", "", {}, "2026-09-15")
    fm = {f["column"]: f["kind"] for f in rep0["privacy"]["flagged"]}
    assert set(fm) == {"customer_email", "notes", "staff_name"}, fm
    chosen = {"customer_email": "code", "notes": "withhold", "staff_name": "keep"}
    got = json.loads(NB.plan_profile_json(data, "o.csv", json.dumps(fm), json.dumps(chosen), "2026-09-15"))
    prof = got["profile"]
    cols = {c["name"]: c for c in prof["columns"]}
    sn, rg = cols["staff_name"], cols["region"]
    # the profile: the kept column goes like any text column of its shape (the same fields), its values in it
    assert sn["looks_personal"] is False and "privacy_flag" not in sn and sorted(sn["top_values"]) == sorted(_OPTIN_STAFF), sn
    assert set(sn) == set(rg), (sorted(sn), sorted(rg))
    assert "notes" not in cols and "416-555" not in json.dumps(prof) and "example.org" not in json.dumps(prof), prof
    assert cols["customer_email"]["looks_personal"] is True and "top_values" not in cols["customer_email"], cols["customer_email"]
    # the analyses: the plan may group by the kept column, and the engine computes it; one by the withheld notes is refused
    plan = {"goal": "Who sells most?",
            "columns": [{"name": "spend", "semantic_type": "flow_amount", "role": "target"}, {"name": "staff_name", "role": "segment"}],
            "analyses": [{"type": "compare", "columns": ["spend"], "by": "staff_name"}, {"type": "compare", "columns": ["spend"], "by": "notes"}]}
    d = dict(chosen)
    d["__plan__"] = plan
    rep = NB.run(data, "o.csv", "", d, "2026-09-15")
    assert rep["ok"], rep["error"]
    assert {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]} == chosen, rep["privacy"]["flagged"]
    items = rep["ai_analyses"]["items"]
    by_staff = [a for a in items if {r[0] for r in (a.get("table") or {}).get("rows") or [] if r} >= set(_OPTIN_STAFF)]
    assert by_staff, json.dumps(items)[:600]
    ana = json.dumps(rep["ai_analyses"])
    assert not re.search(r"(?<![A-Za-z0-9_])notes(?![A-Za-z0-9_])", ana) and "416-555" not in ana, rep["ai_analyses"]["refused"]
    # the writer: the kept values, and one line naming the columns the visitor chose to send (no value in it)
    res = NB.results_for_ai(rep)
    blob = json.dumps(res)
    assert all(s in blob for s in _OPTIN_STAFF), blob[:400]
    line = "The visitor chose to send these personal columns to the AI: staff_name."
    assert blob.count(line) == 1 and line in res["reading"], res["reading"]
    assert "example.org" not in blob and "416-555" not in blob and not re.search(r"(?<![A-Za-z0-9_])notes(?![A-Za-z0-9_])", blob), blob[:400]
    json.loads(NB.results_json(rep))                                   # still JSON with no NaN
    # nothing kept (the default): no such line, and the reading is the plan's own
    base = NB.run(data, "o.csv", "", {"__plan__": dict(plan, understanding="Orders by member of staff.")}, "2026-09-15")
    bres = NB.results_for_ai(base)
    assert "chose to send" not in json.dumps(bres) and bres["reading"] == "Orders by member of staff.", bres["reading"]
    assert not any(s in json.dumps(bres) for s in _OPTIN_STAFF), "a withheld column's values reached the writer"


# ------------------------------------------------------------------ scenarios and contributions (design B)
# plan/AI-INSIGHTS-DESIGN.md section B, 29 Sep 2026: rep["scenarios"], computed by the adapter from the rows the
# engine kept, in the engine's own windows, reconciled with the claim's charted monthly values, for the report
# writer to copy. The ship2 figures below were recomputed independently of the adapter (design B).
SCEN = os.path.join(HERE, "fixtures", "scenarios")
SHIP2_NAME = "ship2_privacy_orders.csv"
SHIP2_AS_OF = "2026-09-29"
SHIP2_FIXTURE = os.path.join(SCEN, "scenarios-ship2.json")      # the frozen interface the worker is built on
NOT_RECONCILED = "the breakdown could not be reconciled with the engine's own monthly totals, so it is not shown"
SC_DERIVED = ("headline", "contribution", "price_volume_mix", "per_unit", "run_rate", "sensitivity", "gap")


def _ship2_bytes() -> bytes:
    with open(os.path.join(SCEN, SHIP2_NAME), "rb") as fh:
        return fh.read()


def _ship2():
    rep = _run(_ship2_bytes(), SHIP2_NAME, "", None, SHIP2_AS_OF)
    assert rep["ok"], rep["error"]
    return rep


def _sc(rep):
    sc = rep.get("scenarios")
    assert isinstance(sc, dict) and set(sc) == {"basis", "items", "refused", "note"}, \
        "the report has no scenarios block of the contract's shape: %r" % (sc if sc is None else sorted(sc),)
    return sc


def _sc_items(rep):
    return {it["id"]: it for it in _sc(rep)["items"]}


def _sc_close(a, b, what, rel=1e-9):
    assert a is not None and b is not None and abs(a - b) <= max(1e-6, rel * abs(b)), (what, a, b)


def _mrange(a, b):
    out, (y, m) = [], (int(a[:4]), int(a[5:7]))
    while "%04d-%02d" % (y, m) <= b:
        out.append("%04d-%02d" % (y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _sc_expected(rep, keep=None):
    """{item id: value} for every item of the derived groups and the facts, recomputed by this test from
    downloads.clean_csv alone (its own arithmetic: nothing of the adapter's but the block's choices of claim,
    windows, segment levels and units column, which other tests check). keep: the claim's own rows (a kind split)."""
    import pandas as pd
    sc = _sc(rep)
    b = sc["basis"]
    items = _sc_items(rep)
    date = rep["roles"]["date"]
    df = pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]), dtype=str, keep_default_na=False)
    dd = pd.to_datetime(df[date].where(df[date] != ""), errors="coerce")
    df["_m"] = dd.dt.strftime("%Y-%m")
    exp = {"facts.months": float(df["_m"].dropna().nunique()),
           "facts.dates": float(dd.dropna().dt.strftime("%Y-%m-%d").nunique()),
           "facts.first": dd.dropna().min().strftime("%Y-%m-%d"), "facts.last": dd.dropna().max().strftime("%Y-%m-%d")}
    if keep is not None:
        df = keep(df)
    measure = None if b["how"] == "count" else b["measure"]
    if measure:
        df = df[df[measure] != ""]
    pm, lm = _mrange(*b["windows"]["prior"]), _mrange(*b["windows"]["latest"])
    P, L = df[df["_m"].isin(pm)], df[df["_m"].isin(lm)]
    tot = (lambda x: float(len(x))) if measure is None else (lambda x: math.fsum(float(v) for v in x[measure]))
    R0, R1 = tot(P), tot(L)
    exp.update({"headline.prior": R0, "headline.latest": R1, "headline.change": R1 - R0,
                "run_rate.year": R1, "run_rate.month": R1 / 12.0, "sensitivity.one_pct": R1 / 100.0})
    if R0 > 0:
        exp["headline.change_pct"] = 100.0 * (R1 - R0) / R0
    seg = b["segment"]["column"]
    units = None
    if "per_unit.prior" in items:
        units = [c for c in items["per_unit.prior"]["inputs"]["columns"] if c not in (date, measure)][0]
    st = {}
    raw = {}
    if seg:
        lv = set(b["segment"]["levels"])
        lab = lambda x: x[seg].where(x[seg].isin(lv), "other")
        for g in sorted(set(lab(P)) | set(lab(L))):
            gp, gl = P[lab(P) == g], L[lab(L) == g]
            st[g] = {"r0": tot(gp), "r1": tot(gl)}
            if units:
                st[g]["u0"] = math.fsum(float(v) for v in gp[units])
                st[g]["u1"] = math.fsum(float(v) for v in gl[units])
        # every level's own latest total, a folded one too (the largest is chosen among all of them)
        for g in sorted(set(L[seg]) - {""}):
            gl = L[L[seg] == g]
            raw[g] = {"r1": tot(gl), "u1": math.fsum(float(v) for v in gl[units]) if units else 0.0}
    slug = {}
    for it in sc["items"]:
        if it["group"] == "contribution" and it["id"].endswith(".change"):
            slug[it["segment"]] = it["id"].split(".")[1]
    chg = R1 - R0
    # shares only when the total moved by 1% of its level and every segment moved the way the total did
    shares = bool(st) and chg != 0 and abs(chg) >= 0.01 * abs(R0) and \
        not any((v["r1"] - v["r0"]) * chg < 0 or abs(100.0 * (v["r1"] - v["r0"]) / chg) > 100 + 1e-9 for v in st.values())
    for g, v in st.items():
        s, c = slug[g], v["r1"] - v["r0"]
        exp["contribution.%s.prior" % s], exp["contribution.%s.latest" % s] = v["r0"], v["r1"]
        exp["contribution.%s.change" % s] = c
        if shares:
            exp["contribution.%s.share" % s] = 100.0 * c / chg
        if v["r0"] > 0:
            exp["contribution.%s.pct" % s] = 100.0 * c / v["r0"]
    if units:
        U0 = math.fsum(float(v) for v in P[units])
        U1 = math.fsum(float(v) for v in L[units])
        P0, P1 = R0 / U0, R1 / U1
        exp["per_unit.prior"], exp["per_unit.latest"] = P0, P1
        if P0 > 0:
            exp["per_unit.change_pct"] = 100.0 * (P1 - P0) / P0
        exp["price_volume_mix.volume"] = (U1 - U0) * P0
        if st and all(v["u0"] > 0 and (v["u1"] > 0 or v["r1"] == 0) for v in st.values()):
            exp["price_volume_mix.price"] = math.fsum(v["u1"] * (v["r1"] / v["u1"] - v["r0"] / v["u0"])
                                                      for v in st.values() if v["u1"] > 0)
            exp["price_volume_mix.mix"] = math.fsum((v["u1"] - U1 * v["u0"] / U0) * v["r0"] / v["u0"] for v in st.values())
        else:
            exp["price_volume_mix.price"] = R1 - U1 * P0
        for g, v in st.items():
            if v["u0"] > 0:
                exp["per_unit.%s.prior" % slug[g]] = v["r0"] / v["u0"]
            if v["u1"] > 0:
                exp["per_unit.%s.latest" % slug[g]] = v["r1"] / v["u1"]
            if v["u0"] > 0 and v["u1"] > 0 and v["r0"] > 0:
                p0, p1 = v["r0"] / v["u0"], v["r1"] / v["u1"]
                exp["per_unit.%s.change_pct" % slug[g]] = 100.0 * (p1 - p0) / p0
    exited = set(b["segment"].get("exited") or [])
    tops = sorted((g for g in raw if raw[g]["r1"] > 0), key=lambda g: (-raw[g]["r1"], g))
    real = [g for g in st if g != "other" and g not in exited and (not tops or g != tops[0])]
    if tops and real and R1 > 0:
        t = raw[tops[0]]
        for g in real:
            exp["gap.%s.amount" % slug[g]] = t["r1"] - st[g]["r1"]
            exp["gap.%s.points" % slug[g]] = 100.0 * (t["r1"] - st[g]["r1"]) / R1
            if units and st[g]["u1"] > 0 and t["u1"] > 0:
                pt, pg = t["r1"] / t["u1"], st[g]["r1"] / st[g]["u1"]
                if pt > pg:
                    exp["gap.%s.per_unit" % slug[g]] = st[g]["u1"] * (pt - pg)
    return exp


def _sc_check_recomputed(rep, keep=None, where=""):
    exp = _sc_expected(rep, keep)
    got = {k: v for k, v in _sc_items(rep).items() if v["group"] in SC_DERIVED + ("facts",)}
    assert set(got) == set(exp), (where, sorted(set(got) ^ set(exp)))
    for k, v in exp.items():
        if isinstance(v, str):
            assert got[k]["value"] == v, (where, k, got[k]["value"], v)
        else:
            _sc_close(got[k]["value"], v, (where, k))
        assert got[k]["text"] == NB_SC()._fmt_item(got[k]["value"], got[k]["kind"], got[k]["unit"]), (where, k)


def NB_SC():
    import nl_scenarios
    return nl_scenarios


def test_scenarios_ship2_holds_the_figures_recomputed_independently():
    rep = _ship2()
    sc = _sc(rep)
    b = sc["basis"]
    assert b and b["finding_id"] == "measure.revenue.total.change" and b["measure"] == "revenue" and b["how"] == "total", b
    assert b["windows"] == {"prior": ["2024-01", "2024-12"], "latest": ["2025-01", "2025-12"]}, b["windows"]
    assert b["reconciles"] is True and b["grade"] == "WATCH" and b["grade_words"] == "not yet conclusive", b
    assert b["segment"] == {"column": "region", "levels": ["East", "North", "South", "West"], "folded": [],
                            "entered": [], "exited": []}, b["segment"]
    assert b["rows"] == {"prior": 151, "latest": 149, "units_missing": 0}, b["rows"]
    t = {k: v["text"] for k, v in _sc_items(rep).items()}
    assert (t["headline.prior"], t["headline.latest"], t["headline.change"]) == ("128,256", "152,214", "+23,958"), t
    ch = [(it["segment"], it["text"]) for it in sc["items"] if it["group"] == "contribution" and it["id"].endswith(".change")]
    assert ch == [("East", "+16,220"), ("North", "+7,116"), ("West", "+330"), ("South", "+292")], ch
    assert t["contribution.east.share"] == "67.7%", t["contribution.east.share"]
    assert (t["price_volume_mix.price"], t["price_volume_mix.volume"], t["price_volume_mix.mix"]) == \
        ("+20,527", "+2,051", "+1,380"), [t.get("price_volume_mix." + k) for k in ("price", "volume", "mix")]
    assert (t["per_unit.prior"], t["per_unit.latest"]) == ("41", "47.9"), (t["per_unit.prior"], t["per_unit.latest"])
    # review, 30 Sep 2026: "revenue per unit 41 to 47.9 (+16.8%)" lost its "+16.8%" to the worker's guard: no item held it
    pc = _sc_items(rep)["per_unit.change_pct"]
    assert pc["text"] == "+16.8%" and (pc["kind"], pc["unit"], pc["group"], pc["grade"], pc["parent_grade"]) == \
        ("change", "%", "per_unit", None, "WATCH"), pc
    assert t["per_unit.east.change_pct"] == "+15.4%", t["per_unit.east.change_pct"]
    assert t["sensitivity.one_pct"] == "1,522", t["sensitivity.one_pct"]
    assert _sc_items(rep)["per_unit.prior"]["inputs"]["columns"][-1] == "units"


def test_scenarios_ship2_is_the_frozen_interface_fixture():
    # the worker (insight-proxy) is built on this exact block: a change to it is a change to that interface
    with open(SHIP2_FIXTURE, encoding="utf-8") as fh:
        want = json.load(fh)
    got = json.loads(json.dumps(_sc(_ship2()), allow_nan=False))
    assert got == want, "rep['scenarios'] for %s differs from %s" % (SHIP2_NAME, os.path.basename(SHIP2_FIXTURE))


def test_scenarios_every_item_recomputes_from_the_clean_download():
    _sc_check_recomputed(_ship2(), where="ship2")
    rep = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    b = _sc(rep)["basis"]
    assert b["finding_id"] == "measure.amount.total.rent.change" and b["segment"]["column"] == "property", b
    _sc_check_recomputed(rep, keep=lambda df: df[df["category"] == "RENT"], where="sample: rent only")
    _, rep = _v2("three_measures.csv")
    assert _sc(rep)["basis"]["how"] == "count" and _sc(rep)["basis"]["measure"] == "rows", _sc(rep)["basis"]
    _sc_check_recomputed(rep, where="three_measures: row count")
    rep = _case("run3_control.csv", "run3_plan.json")
    _sc_check_recomputed(rep, where="run3 with its plan")


def test_scenarios_contributions_and_price_volume_mix_add_up_to_the_change():
    for rep in (_ship2(), _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF), _v2("three_measures.csv")[1]):
        it = _sc_items(rep)
        chg = it["headline.change"]["value"]
        parts = [v["value"] for k, v in it.items() if v["group"] == "contribution" and k.endswith(".change")]
        assert parts, rep["input"]["name"]
        _sc_close(math.fsum(parts), chg, ("contributions", rep["input"]["name"]))
        pvm = [v["value"] for v in it.values() if v["group"] == "price_volume_mix"]
        if pvm:
            _sc_close(math.fsum(pvm), chg, ("price + volume + mix", rep["input"]["name"]))
    assert len([v for v in _sc_items(_ship2()).values() if v["group"] == "price_volume_mix"]) == 3


def test_scenarios_reconcile_with_the_claims_charted_months_or_the_whole_block_is_refused():
    import copy
    import nl_scenarios as NS
    rep = _ship2()
    b = _sc(rep)["basis"]
    ch = next(c for c in rep["charts"] if c["type"] == "trend_windows" and c["finding_ids"][0] == b["finding_id"])
    assert b["windows"] == ch["data"]["windows"], (b["windows"], ch["data"]["windows"])
    by = dict(zip(ch["data"]["months"], ch["data"]["values"]))
    it = _sc_items(rep)
    _sc_close(it["headline.prior"]["value"], math.fsum(by[m] for m in _mrange(*b["windows"]["prior"])), "prior window")
    _sc_close(it["headline.latest"]["value"], math.fsum(by[m] for m in _mrange(*b["windows"]["latest"])), "latest window")
    _sc_close(it["run_rate.month"]["value"], ch["data"]["window_means"]["latest"], "the average month")
    # the same inputs the run hands the block, with one charted month moved: the whole block is refused
    seen = {}
    orig = NS.build

    def spy(*a, **k):
        seen["a"], seen["k"] = a, k
        return orig(*a, **k)
    NS.build = spy
    try:
        NB.run(_ship2_bytes(), SHIP2_NAME, "", None, SHIP2_AS_OF)
    finally:
        NS.build = orig
    assert "a" in seen, "run() does not build the block with nl_scenarios.build"
    args = list(seen["a"])

    def moved(delta):
        r2 = copy.deepcopy(args[0])
        c2 = next(c for c in r2["charts"] if c["type"] == "trend_windows" and c["finding_ids"][0] == b["finding_id"])
        c2["data"]["values"][c2["data"]["months"].index("2025-03")] += delta
        return NS.build(r2, *args[1:], **seen["k"])
    got = moved(1.0)
    assert got["items"] == [] and NOT_RECONCILED in got["refused"] and got["basis"]["reconciles"] is False, got
    assert moved(1e-9)["basis"]["reconciles"] is True          # within 1e-6 of the value: the same total


def test_scenarios_only_the_headline_carries_the_claims_grade_and_a_derived_figure_names_it_as_its_parent():
    # final review, 30 Sep 2026: every derived item carried the claim's grade as its own, so a region that fell 1.63%
    # inside a CONFIRMED rise read CONFIRMED. The headline (the claim itself) keeps it; a derived item has grade null,
    # the claim's grade as parent_grade and grade_words saying it is not graded itself
    f9c = _run(_r4("f9c_confirmed_total.csv"), "f9c_confirmed_total.csv", "", {"__plan__": R4_SALES_PLAN}, R4_AS_OF)
    cases = [_ship2(), _case("run3_control.csv", "run3_plan.json"), _v2("three_measures.csv")[1], f9c]
    grades = set()
    for rep in cases:
        sc = _sc(rep)
        f = next(x for x in rep["findings"] if x["id"] == sc["basis"]["finding_id"])
        assert sc["basis"]["grade"] == f["grade"], (sc["basis"], f["grade"])
        grades.add(f["grade"])
        for it in sc["items"]:
            g = it["group"]
            if g == "facts":
                assert (it["grade"], it["parent_grade"], it["grade_words"]) == (None, None, "a fact about the rows, not graded"), it
            elif g == "forecast":
                assert it["grade"] == GRADE_OF[rep["forecast"]["verdict"]] == "CONFIRMED" and it["parent_grade"] is None, it
                assert it["grade_words"] == "the engine's forecast, graded CONFIRMED (usable for planning)", it
            elif g == "headline":
                assert (it["grade"], it["parent_grade"]) == (f["grade"], None), (rep["input"]["name"], it)
                assert it["grade_words"] == "the claim itself, graded %s" % f["grade"], it
            else:
                assert (it["grade"], it["parent_grade"]) == (None, f["grade"]), (rep["input"]["name"], it["id"], it["grade"])
                assert it["grade_words"] == "part of a change graded %s; not graded itself" % f["grade"], it
    assert grades == {"WATCH", "NOT_ENOUGH_DATA", "CONFIRMED"}, grades
    # f9c: North and East rose 50% and made the total's CONFIRMED rise; South fell 1.63%, and that is not CONFIRMED
    it = _sc_items(f9c)
    assert f9c["scenarios"]["basis"]["grade"] == "CONFIRMED" and it["headline.change"]["grade"] == "CONFIRMED"
    south = it["contribution.south.pct"]
    assert south["text"] == "\u22121.63%" and south["grade"] is None and south["parent_grade"] == "CONFIRMED", south
    assert not any(v["grade"] == "CONFIRMED" for v in it.values() if v["segment"] is not None), "a segment reads CONFIRMED"
    # the writer receives the same fields 1:1, and the worker keeps every item (a null grade is no grade)
    out = NB.results_for_ai(f9c)
    assert out["scenarios"] == _sc(f9c), "results_for_ai changed the scenarios block"
    v = _proxy_validate(out)
    if v is not None:
        assert v["ok"] and len(v["value"]["scenarios"]["items"]) == len(it), v.get("detail")
        vs = {x["id"]: x for x in v["value"]["scenarios"]["items"]}
        assert vs["contribution.south.pct"]["grade"] is None and vs["headline.change"]["grade"] == "CONFIRMED"


def _orders_with_people(with_region: bool) -> bytes:
    """26 months of orders sold by four named people (a person's name column: flagged by the adapter's check),
    with revenue and units, and a region column when asked."""
    import random as _random
    rng = _random.Random(20260929)
    reps = ["Marisol Fairweather", "Tobias Quennell", "Ingrid Halvorsen", "Desmond Achebe"]
    rows = []
    for k in range(26):
        y, m = 2024 + k // 12, k % 12 + 1
        for i in range(24):
            u = rng.randint(1, 30)
            row = ["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), rng.choice(reps)]
            if with_region:
                row.append(rng.choice(["North", "South", "East", "West"]))
            rows.append(row + ["%.2f" % (u * rng.uniform(30, 50) * (1.1 if k >= 14 else 1.0)), "%d" % u])
    return _csv(rows, ["order_date", "sales_rep"] + (["region"] if with_region else []) + ["revenue", "units"])


def test_scenarios_privacy_a_withheld_coded_or_kept_flagged_segment_gives_no_contributions():
    names = ["Marisol Fairweather", "Tobias Quennell", "Ingrid Halvorsen", "Desmond Achebe"]
    words = [w for n in names for w in n.split()]
    for with_region in (False, True):
        data = _orders_with_people(with_region)
        for dec in ("withhold", "code", "keep"):
            rep = _run(data, "orders_people.csv", "", {"sales_rep": dec}, "2026-03-15")
            assert rep["ok"], rep["error"]
            fl = {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}
            assert fl.get("sales_rep") == dec, (dec, rep["privacy"]["flagged"])
            sc = _sc(rep)
            assert sc["basis"] and sc["basis"]["reconciles"], sc
            seg = sc["basis"]["segment"]["column"]
            assert seg != "sales_rep" and seg == ("region" if with_region else None), (with_region, dec, seg)
            contrib = [it for it in sc["items"] if it["group"] == "contribution"]
            assert bool(contrib) == with_region, (with_region, dec, len(contrib))
            assert all("sales_rep" not in (it["inputs"]["columns"] or []) for it in sc["items"])
            if not with_region:
                assert any(x.startswith("no segment column qualifies") for x in sc["refused"]), sc["refused"]
            out = NB.results_for_ai(rep)
            text = json.dumps(sc) + json.dumps(out.get("scenarios")) + json.dumps(out.get("tables"))
            assert not any(w in text for w in words), (with_region, dec, [w for w in words if w in text])


def _scenarios_in_a_fresh_python(seed: str) -> str:
    """rep["scenarios"] of ship2 as JSON text, from a fresh Python with its own string-hash seed (a set or dict
    order that leaks into the block differs between two such runs; two runs in one process share a seed)."""
    code = ("import sys, json\n"
            "sys.path.insert(0, %r)\nsys.path.insert(0, %r)\n"
            "import nl_browser\n"
            "rep = nl_browser.run(open(%r, 'rb').read(), %r, '', None, %r)\n"
            "sys.stdout.write(json.dumps(rep['scenarios'], allow_nan=False, sort_keys=True))\n"
            % (ENGINE_ROOT, ADAPTER_DIR, os.path.join(SCEN, SHIP2_NAME), SHIP2_NAME, SHIP2_AS_OF))
    env = dict(os.environ, PYTHONHASHSEED=seed)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-1200:]
    return r.stdout


def test_scenarios_are_finite_and_the_same_on_every_run():
    a = NB.run(_ship2_bytes(), SHIP2_NAME, "", None, SHIP2_AS_OF)
    ta = json.dumps(_sc(a), allow_nan=False, sort_keys=True)
    assert _scenarios_in_a_fresh_python("1") == ta == _scenarios_in_a_fresh_python("2"), "two runs of one file differ"
    for rep in (a, _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF), _v2("three_measures.csv")[1]):
        for it in _sc(rep)["items"]:
            assert it["kind"] in ("amount", "change", "count", "percent", "points", "per_unit", "date"), it
            if it["kind"] == "date":
                assert re.match(r"^\d{4}-\d{2}-\d{2}$", it["value"]), it
            else:
                assert _is_num(it["value"]), it
            assert it["text"] and "nan" not in it["text"].lower() and "inf" not in it["text"].lower(), it
            assert "—" not in it["label"] + it["text"] and "–" not in it["label"], it
        json.loads(NB.run_json(_ship2_bytes(), SHIP2_NAME, "", None, SHIP2_AS_OF))


def test_scenarios_add_up_a_forecast_only_when_it_is_usable_for_planning():
    _, rep = _v2("three_measures.csv")
    fc = rep["forecast"]
    assert fc["available"] and GRADE_OF[fc["verdict"]] == "CONFIRMED", fc["verdict"]
    it = _sc_items(rep)
    pts = fc["forecast"]
    for h in (3, 6, 12):
        if len(pts) < h:
            assert not any(k.startswith("forecast.%d." % h) for k in it), (h, len(pts))
            continue
        for part, key in (("base", "value"), ("low", "lo"), ("high", "hi")):
            x = it["forecast.%d.%s" % (h, part)]
            _sc_close(x["value"], math.fsum(p[key] for p in pts[:h]), (h, part))
            assert x["grade"] == "CONFIRMED" and x["group"] == "forecast", x
        for part in ("low", "high"):
            lab = it["forecast.%d.%s" % (h, part)]["label"]
            # the wording first, so a reader that cuts a label at 160 characters (the proxy) keeps it
            assert lab.startswith(part.capitalize() + ": the months' own 80% ranges added up, at least as wide as an "
                                  "80% range for the total, the next " + str(h) + " months added up"), lab
    assert "forecast.3.base" in it, sorted(it)
    rep = _run(_sample_bytes(), "sample-messy.csv", "", None, SAMPLE_AS_OF)
    assert rep["forecast"]["available"] and GRADE_OF[rep["forecast"]["verdict"]] != "CONFIRMED"
    assert not any(v["group"] == "forecast" for v in _sc(rep)["items"]), "a forecast not usable for planning was added up"
    assert any("not yet shown usable" in x for x in _sc(rep)["refused"]), _sc(rep)["refused"]


def test_scenarios_run_rate_is_the_latest_12_months_never_three_months_times_four():
    it = _sc_items(_ship2())
    rr = sorted(k for k, v in it.items() if v["group"] == "run_rate")
    assert rr == ["run_rate.month", "run_rate.year"], rr
    assert not any("annual" in k or "annual" in v["label"].lower() or "3 months" in v["label"] for k, v in it.items()
                   if v["group"] != "forecast"), [k for k in it if "annual" in k]
    _sc_close(it["run_rate.year"]["value"], it["headline.latest"]["value"], "run rate a year")
    _sc_close(it["run_rate.month"]["value"], it["headline.latest"]["value"] / 12.0, "run rate a month")
    _sc_close(it["sensitivity.one_pct"]["value"], it["headline.latest"]["value"] / 100.0, "each 1%")


def _levels_file(levels: int, sparse: str = "") -> bytes:
    """26 months of orders over `levels` regions; `sparse` names a region with only 3 rows in 2024."""
    import random as _random
    rng = _random.Random(7)
    regs = ["R%02d" % i for i in range(levels)] if not sparse else ["North", "South", "East", "West"]
    rows = []
    for k in range(26):
        y, m = 2024 + k // 12, k % 12 + 1
        for i in range(30):
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), rng.choice(regs), "%.2f" % rng.uniform(50, 150)])
        if sparse and (y == 2025 or (y == 2024 and m <= 3)):
            rows.append(["%04d-%02d-15" % (y, m), sparse, "%.2f" % rng.uniform(50, 150)])
    return _csv(rows, ["order_date", "region", "revenue"])


def test_scenarios_sparse_levels_fold_into_other_and_more_than_12_levels_give_no_breakdown():
    rep = _run(_levels_file(4, sparse="Remote"), "sparse.csv", "", None, "2026-03-15")
    sc = _sc(rep)
    assert sc["basis"]["segment"] == {"column": "region", "levels": ["East", "North", "South", "West"],
                                      "folded": ["Remote"], "entered": [], "exited": []}, sc["basis"]["segment"]
    segs = [v["segment"] for v in sc["items"] if v["group"] == "contribution" and v["id"].endswith(".change")]
    assert sorted(segs) == ["East", "North", "South", "West", "other"], segs
    assert "Remote" not in json.dumps(sc["items"]), "a folded level has an item of its own"
    _sc_check_recomputed(rep, where="folded")
    assert not any(v["group"] == "gap" and v["segment"] == "other" for v in sc["items"]), "a gap to or from other"
    rep = _run(_levels_file(13), "many.csv", "", None, "2026-03-15")
    sc = _sc(rep)
    assert sc["basis"]["reconciles"] and sc["basis"]["segment"]["column"] is None, sc["basis"]["segment"]
    assert not any(v["group"] in ("contribution", "gap") for v in sc["items"]), [v["id"] for v in sc["items"]]
    assert any(x.startswith("no segment column qualifies") for x in sc["refused"]), sc["refused"]


def test_scenarios_say_why_there_are_none_with_no_dates_or_when_the_gate_trips():
    _, rep = _v2("no_date.csv")
    sc = _sc(rep)
    assert sc["basis"] is None and sc["items"] == [] and sc["note"], sc
    assert any("no column holds dates" in x for x in sc["refused"]), sc["refused"]
    _, rep = _v2("rent-roll-tbd.csv")
    assert rep["story"]["headline"].startswith(NB.GATE_TRIPPED), rep["story"]["headline"]
    sc = _sc(rep)
    assert sc["basis"] is None and sc["items"] == [] and any("did not run" in x for x in sc["refused"]), sc
    rep = _run(b"", "empty.csv")
    assert not rep["ok"] and _sc(rep) == {"basis": None, "items": [], "refused": [], "note": ""}, rep.get("scenarios")


def test_results_for_ai_carries_the_scenarios_one_to_one_and_one_table_the_proxy_keeps():
    rep = _ship2()
    out = NB.results_for_ai(rep)
    json.dumps(out, allow_nan=False)
    assert out["scenarios"] == _sc(rep), "results_for_ai changed the scenarios block"
    tab = out["tables"][-1]
    assert tab["title"] == "Where the change in total revenue came from", tab["title"]
    assert tab["cols"] == ["region", "12 months before", "Latest 12 months", "Change", "Share of the change",
                           "Own change"], tab["cols"]
    it = _sc_items(rep)
    want = []
    for v in _sc(rep)["items"]:
        if v["group"] == "contribution" and v["id"].endswith(".change"):
            s = v["id"][:-len(".change")]
            want.append([v["segment"]] + [it[s + "." + k]["text"] if s + "." + k in it else ""
                                          for k in ("prior", "latest", "change", "share", "pct")])
    assert tab["rows"] == want and len(want) <= 12, (tab["rows"], want)
    assert len([t for t in out["tables"] if t["title"].startswith("Where the change")]) == 1
    # a label that names a withheld column reads "a column you withheld" (safe() on labels), figures unchanged
    rep2 = json.loads(json.dumps(rep))
    rep2["scenarios"]["items"][0]["label"] = "customer_name: " + rep2["scenarios"]["items"][0]["label"]
    o2 = NB.results_for_ai(rep2)["scenarios"]["items"][0]
    assert o2["label"].startswith(NB.WITHHELD_WORDS) and o2["value"] == rep2["scenarios"]["items"][0]["value"], o2
    # the file's name never reaches the writer: the forecast label carries it on a usable forecast
    _, rep3 = _v2("three_measures.csv")
    o3 = NB.results_for_ai(rep3)["scenarios"]
    assert "three_measures.csv" not in json.dumps(o3) and "[your file]" in json.dumps(o3), o3["items"][:1]
    # what the proxy's own validator keeps (insight-proxy/src/report.js), when it is here: the table always; the
    # block itself once the worker's validateResults carries it
    got = _proxy_validate(out)
    if got is not None:
        assert got["ok"], got
        assert any(t["title"] == tab["title"] and t["rows"] == tab["rows"] for t in got["value"]["tables"]), \
            "the proxy dropped the Where the change came from table"
        if "scenarios" in got["value"]:
            gi = {x["id"]: x for x in got["value"]["scenarios"]["items"]}
            assert not [k for k in it if k not in gi], ("the proxy dropped scenario items", [k for k in it if k not in gi])
            assert all(gi[k]["text"] == v["text"] and gi[k]["value"] == v["value"] for k, v in it.items()), \
                "the proxy changed a scenario's figure"


# ------------------------------------------------------------------ the report's web searches: built from a fixed list
# (final review, 30 Sep 2026: the block-list check of free-text searches let names through in a dozen ways; a search is
# now built by the adapter from engine/context_terms.json terms and years only, _context_queries)
SHIP2_PLAN = {"goal": "How did order revenue develop over 2024 to 2025?", "understanding": "Orders.",
              "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                          {"name": "region", "semantic_type": "category", "role": "segment"},
                          {"name": "revenue", "semantic_type": "flow_amount", "role": "target"},
                          {"name": "units", "semantic_type": "count", "role": "driver"}],
              "operations": [], "analyses": [], "primary": "revenue"}
CTX_TERMS = os.path.join(SITE, "engine", "context_terms.json")
CQ_REASONS = (NB.CQ_NOT_ITEM, NB.CQ_INDICATOR, NB.CQ_SECTOR, NB.CQ_REGION, NB.CQ_YEARS, NB.CQ_MORE, NB.CQ_TWICE)


def _terms() -> dict:
    with open(CTX_TERMS, encoding="utf-8") as fh:
        return json.load(fh)


def _built_from_terms(q: str, it: dict, t: dict) -> bool:
    """q is exactly the search the list's terms and the years of item `it` build, and each term is on its list."""
    ys = [str(y) for y in dict.fromkeys(it.get("years") or [])]
    words = [it.get("sector"), it.get("indicator"), it.get("region")] + ys
    return (q == " ".join(w for w in words if w) and it.get("indicator") in t["indicators"]
            and it.get("sector", t["sectors"][0]) in t["sectors"] and it.get("region", t["regions"][0]) in t["regions"]
            and all(1900 <= int(y) <= 2099 for y in ys))


def test_context_terms_list_is_ascii_generic_versioned_and_packed_byte_for_byte():
    with open(CTX_TERMS, "rb") as fh:
        raw = fh.read()
    assert all(b < 128 for b in raw), "engine/context_terms.json is not ASCII"
    t = json.loads(raw)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", t["version"]), t["version"]
    assert t["plan_field"]["name"] == "context" and t["plan_field"]["max_items"] == NB.CONTEXT_MAX == 4
    assert 25 <= len(t["indicators"]) <= 32 and 75 <= len(t["sectors"]) <= 90, (len(t["indicators"]), len(t["sectors"]))
    for key in ("indicators", "sectors", "regions"):
        lst = t[key]
        keys = [NB._term_key(x) for x in lst]
        assert all(isinstance(x, str) and x == x.strip() and x.isascii() and not re.search(r"\d", x) for x in lst), key
        assert all(keys) and len(set(keys)) == len(keys), (key, [x for x, k in zip(lst, keys) if keys.count(k) > 1])
    # the groups, counted in the list's order: every country, Canada's 13 provinces and territories, the US states and
    # the District of Columbia (Georgia shares the country's entry), at most 50 cities
    g = t["region_groups"]
    assert sum(g.values()) == len(t["regions"]), (g, len(t["regions"]))
    assert g["countries and territories"] >= 195 and g["Canadian provinces and territories"] == 13 and \
        g["US states and the District of Columbia"] == 50 and g["major cities"] <= 50, g
    for must, key in ((("consumer price inflation", "retail sales", "consumer spending", "interest rates", "exchange rate",
                        "unemployment rate", "housing starts", "rental vacancy rate", "e-commerce sales",
                        "industrial production", "commodity prices", "wage growth"), "indicators"),
                      (("video games", "consumer electronics", "restaurants", "grocery retail", "rental housing",
                        "real estate", "banking", "insurance", "software", "logistics", "construction", "healthcare",
                        "education", "tourism", "automotive", "apparel", "e-commerce", "telecommunications", "energy",
                        "agriculture"), "sectors"),
                      (("World", "European Union", "Canada", "United States", "Ontario", "Quebec", "Nunavut", "Georgia",
                        "California", "District of Columbia", "Toronto", "Cote d'Ivoire", "Japan", "Brazil"), "regions")):
        assert not [m for m in must if m not in t[key]], (key, [m for m in must if m not in t[key]])
    brands = ("nintendo", "apple", "amazon", "google", "walmart", "tesla", "uber", "netflix", "starbucks", "microsoft")
    assert not [s for s in t["sectors"] + t["indicators"] if any(b in s.lower().split() for b in brands)]
    # packed beside the adapter byte for byte, and read from the zip alone as from the source tree
    with open(os.path.join(SITE, "engine", "pack.json"), encoding="utf-8") as fh:
        pack = json.load(fh)
    entry = [f for f in pack["files"] if f["path"] == "context_terms.json"]
    assert len(entry) == 1 and entry[0]["sha256"] == hashlib.sha256(raw).hexdigest(), entry
    tmp = tempfile.mkdtemp(prefix="nl_terms_")
    try:
        with zipfile.ZipFile(os.path.join(SITE, "engine", pack["zip"]["file"])) as z:
            assert z.read("context_terms.json") == raw, "the packed list differs from engine/context_terms.json"
            z.extractall(tmp)
        code = ("import sys, json\n"
                "sys.path[:] = [p for p in sys.path if 'northledger-core' not in p and 'portfolio-website' not in p]\n"
                "sys.path.insert(0, %r)\n"
                "import nl_browser as NB\n"
                "print(json.dumps([NB._context_terms().get('version'), NB._context_queries("
                "[{'indicator': 'exchange rate', 'region': 'Canada', 'years': [2024, 2025]}])[0]]))\n" % tmp)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp)
        assert p.returncode == 0, p.stderr[-800:]
        assert json.loads(p.stdout) == [t["version"], ["exchange rate Canada 2024 2025"]], p.stdout
    finally:
        shutil.rmtree(tmp, True)
    # the planner is told which list the adapter reads
    with open(SAMPLE, "rb") as fh:
        prof = NB.profile_for_ai(fh.read(), "sample-messy.csv")
    assert prof["context_terms_version"] == t["version"], prof.get("context_terms_version")


def test_context_searches_are_built_only_from_list_terms_and_years():
    t = _terms()
    q, why, kept = NB._context_queries([
        {"indicator": "consumer price inflation", "region": "Canada", "years": [2024, 2025]},
        {"sector": "Video Games", "indicator": "SALES growth", "years": [2023]},       # case and spacing: the list's own
        {"indicator": "retail sales", "region": "Côte d’Ivoire", "years": ["2024", 2024],
         "query": "Acme Holdings market share", "why": "Zoe Muller"},              # other keys are never read
        {"indicator": "Acme Holdings market share"},                                   # not an indicator
        {"indicator": "retail sales", "sector": "Nintendo"},                           # a brand is no sector
        {"indicator": "retail sales", "region": "Zoe Muller"},                         # a name is no region
        {"indicator": "retail sales", "years": [1850, 2025]},
        {"indicator": "retail sales", "years": [2026, 2025]},
        {"indicator": "retail sales", "years": [True]},
        "retail sales Canada 2025",                                                    # free text is no item
        {"sector": "video games"},                                                     # no indicator
        {"indicator": "consumer price inflation", "region": "canada", "years": [2024, 2025]}])   # built already
    assert q == ["consumer price inflation Canada 2024 2025", "video games sales growth 2023",
                 "retail sales Cote d'Ivoire 2024"], q
    assert why == [NB.CQ_INDICATOR, NB.CQ_SECTOR, NB.CQ_REGION, NB.CQ_YEARS, NB.CQ_YEARS, NB.CQ_YEARS, NB.CQ_NOT_ITEM,
                   NB.CQ_INDICATOR, NB.CQ_TWICE], why
    assert kept == [{"indicator": "consumer price inflation", "region": "Canada", "years": [2024, 2025]},
                    {"sector": "video games", "indicator": "sales growth", "years": [2023, 2023]},
                    {"indicator": "retail sales", "region": "Cote d'Ivoire", "years": [2024, 2024]}], kept
    assert all(_built_from_terms(a, b, t) for a, b in zip(q, kept))
    # at most 4 searches; an empty optional term is left out; years null or [] is no year
    q, why, _k = NB._context_queries([{"indicator": x, "sector": "", "region": None, "years": []} for x in t["indicators"][:6]])
    assert q == t["indicators"][:4] and why == [NB.CQ_MORE] * 2, (q, why)
    for raw in ([], None, "not a list", {"indicator": "retail sales"}, 7):
        assert NB._context_queries(raw) == ([], [], []), raw
    assert NB._context_queries([7, None, "  "]) == ([], [NB.CQ_NOT_ITEM] * 3, [])
    # at most CONTEXT_READ items are read
    assert len(NB._context_queries([{"indicator": "x"}] * 40)[1]) == NB.CONTEXT_READ


def test_context_searches_on_ship2_fx_and_reviews_and_the_old_free_text_is_never_read():
    t = _terms()
    # ship2: the customers' names and emails are flagged (kept, withheld or coded); the plan names them, and a region
    # value of the file that is no list term, in every field, and asks for free-text searches the old way
    ctx = [{"sector": "e-commerce", "indicator": "sales growth", "region": "United States", "years": [2024, 2025]},
           {"indicator": "Florian Kettleby"},
           {"indicator": "consumer spending", "region": "Florian Kettleby", "years": [2025]},
           {"indicator": "retail sales", "sector": "seraphina.quillfeather32@mailbox.test"},
           {"indicator": "retail sales", "region": "West", "years": [2024, 2025]},
           {"indicator": "consumer spending", "region": "United States", "years": [2024, 2025]}]
    plan = dict(SHIP2_PLAN, context=ctx, context_queries=["Florian Kettleby order history", "US retail sales 2025"])
    want = ["e-commerce sales growth United States 2024 2025", "consumer spending United States 2024 2025"]
    for dec in ("keep", "withhold", "code"):
        rep = _run(_ship2_bytes(), SHIP2_NAME, "", {"__plan__": plan, "customer_name": dec, "email": dec}, SHIP2_AS_OF)
        assert rep["ok"], rep["error"]
        ap = rep["ai_plan"]
        assert ap["context_queries"] == want, (dec, ap["context_queries"])
        assert ap["context_queries_dropped"] == [NB.CQ_INDICATOR, NB.CQ_REGION, NB.CQ_SECTOR, NB.CQ_REGION], ap
        assert all(_built_from_terms(q, it, t) for q, it in zip(ap["context_queries"], ap["context"]))
        txt = json.dumps(ap["context_queries"] + ap["context_queries_dropped"] + ap["context"]).lower()
        assert not any(w in txt for w in ("kettleby", "florian", "seraphina", "mailbox", "west", "order history")), txt
    # fx: the Bank of Canada's rate; reviews: a brand file, where a brand is never a sector
    with open(EVAL_FX, "rb") as fh:
        fx = fh.read()
    rep = _run(fx, "fx_usd_cad.csv", "", {"__plan__": dict(EVAL_FX_PLAN, context=[
        {"indicator": "exchange rate", "region": "Canada", "years": [2017, 2025]},
        {"indicator": "interest rates", "region": "United States", "years": [2025]}])}, "2026-09-29")
    assert rep["ai_plan"]["context_queries"] == ["exchange rate Canada 2017 2025", "interest rates United States 2025"], \
        rep["ai_plan"]["context_queries"]
    data, _counts = _brand_reviews()
    rep = _run(data, "brand_ratings.csv", "", {"__plan__": dict(BRAND_PLAN, context=[
        {"sector": "video games", "indicator": "sales growth", "years": [2022]},
        {"sector": "consumer electronics", "indicator": "market size", "region": "World", "years": [2022]},
        {"sector": "G-STORY", "indicator": "sales growth"}, {"indicator": "Elgato market share"}])}, "2026-09-30")
    ap = rep["ai_plan"]
    assert ap["context_queries"] == ["video games sales growth 2022", "consumer electronics market size World 2022"], ap
    assert ap["context_queries_dropped"] == [NB.CQ_SECTOR, NB.CQ_INDICATOR], ap["context_queries_dropped"]
    # no list at all, or none of its items, means no search: an explicit []; a run with no plan has no ai_plan
    for cx in ([], None, "not a list", [7, None, "  "]):
        p = dict(SHIP2_PLAN, context_queries=["US retail sales 2025"])
        if cx is not None:
            p["context"] = cx
        rep = _run(_ship2_bytes(), SHIP2_NAME, "", {"__plan__": p}, SHIP2_AS_OF)
        assert rep["ai_plan"]["context_queries"] == [], (cx, rep["ai_plan"].get("context_queries"))
    rep = _run(_ship2_bytes(), SHIP2_NAME, "", None, SHIP2_AS_OF)
    assert "ai_plan" not in rep, "a run with no plan gains an ai_plan"


def test_context_searches_read_no_value_of_a_latin1_semicolon_file():
    import random as _random
    rng = _random.Random(12)
    names = ["Zoë Müller", "René Faßbender", "Anaïs Lefèvre", "Jörg Brandstätter"]
    rows = ["%04d-%02d-%02d;%s;%s;%s" % (2024 + k // 12, k % 12 + 1, rng.randint(1, 28), rng.choice(names),
                                        rng.choice(["Nord", "Süd"]), ("%.2f" % rng.uniform(20, 90)).replace(".", ","))
            for k in range(26) for _ in range(15)]
    data = ("date;client;region;amount\n" + "\n".join(rows) + "\n").encode("latin-1")
    plan = {"goal": "g", "columns": [], "operations": [], "primary": "",
            "context_queries": ["Zoë Müller spending"],
            "context": [{"indicator": "consumer spending", "region": "Zoë Müller"},
                        {"indicator": "consumer spending", "region": "Zoe Mueller"},
                        {"indicator": "retail sales", "region": "Germany", "years": [2025]}]}
    rep = _run(data, "clients.csv", "", {"__plan__": plan}, "2026-03-15")
    assert rep["ok"], rep["error"]
    assert "client" in {f["column"] for f in rep["privacy"]["flagged"]}, rep["privacy"]["flagged"]
    ap = rep["ai_plan"]
    assert ap["context_queries"] == ["retail sales Germany 2025"], ap.get("context_queries")
    assert ap["context_queries_dropped"] == [NB.CQ_REGION, NB.CQ_REGION], ap.get("context_queries_dropped")


def test_review5_no_value_from_any_file_column_reaches_a_web_search():
    # the final review's attack files (tools/fixtures/review5/attack_files.py, all made up): every string the review
    # sent past the block list, and every name itself, in every field of the plan's items and in its old free-text
    # list; a value of the file that is a list term (a region column holding Ontario) is fine, as a list term
    sys.path.insert(0, os.path.join(HERE, "fixtures", "review5"))
    import attack_files as AF
    t = _terms()
    reached, planted, built_n = [], 0, 0
    for case in AF.cases():
        vals = sorted({NB._term_key(v) for v in AF.file_values(case.data)} - {""})
        items = []
        for s in case.strings:
            items += [{"indicator": s}, {"indicator": "retail sales", "sector": s}, {"indicator": "retail sales", "region": s},
                      {"indicator": "retail sales", "region": "Canada", "years": [2025], "query": s, "note": s},
                      {"indicator": "retail sales", "years": [s]}]
        planted += len(items)
        built = []
        for i in range(0, len(items), NB.CONTEXT_READ):
            q, why, kept = NB._context_queries(items[i:i + NB.CONTEXT_READ])
            built += list(zip(q, kept))
            assert len(q) + len(why) == len(items[i:i + NB.CONTEXT_READ])
        # the run on the file itself: the same builder, whatever the file holds and whatever the visitor chose
        plan = {"goal": "g", "columns": case.columns, "operations": [], "analyses": [], "primary": "",
                "context_queries": list(case.strings[:12]),
                "context": [{"indicator": "retail sales", "region": s} for s in case.strings[:3]] +
                           [{"indicator": "retail sales", "region": "Ontario", "years": [2024, 2025]}]}
        rep = NB.run(case.data, case.name, "", dict(case.decisions, __plan__=plan), AF.AS_OF)
        assert rep["ok"], (case.name, rep["error"])
        ap = rep["ai_plan"]
        assert (ap["context_queries"], ap["context"]) == NB._context_queries(plan["context"])[::2], (case.name, ap)
        assert "retail sales Ontario 2024 2025" in ap["context_queries"], (case.name, ap["context_queries"])
        built += list(zip(ap["context_queries"], ap["context"]))
        built_n += len(built)
        for q, it in built:
            assert _built_from_terms(q, it, t), (case.name, q, it)
            used = [NB._term_key(it[f]) for f in ("sector", "indicator", "region") if it.get(f)]
            for v in vals:
                if (" %s " % v) in (" %s " % NB._term_key(q)) and not any(v in u for u in used):
                    reached.append((case.name, v, q))
    assert not reached, reached[:10]
    assert planted == 5 * sum(len(c.strings) for c in AF.cases()) >= 800 and built_n > 0, (planted, built_n)


# ------------------------------------------------------------------ the evaluation preflight (30 Sep 2026)
# The packed engine on real data (.work/eval): the AI plan's path read StatCan's weekend zeros as exchange rates, a
# brand ranking rested on single reviews, and the scenarios block had no per-unit change for "+16.8%".
EVAL_FX = os.path.join(HERE, "fixtures", "eval", "fx_usd_cad.csv")     # StatCan 33-10-0036-01 (make_fx.py)
EVAL_FX_PLAN = {"goal": "How has the Canadian dollar price of one U.S. dollar moved?", "kind": "time_series",
                "understanding": "The Bank of Canada daily U.S. dollar rate.", "primary": "VALUE",
                "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date", "unit": ""},
                            {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"},
                            {"name": "STATUS", "semantic_type": "metadata", "role": "metadata", "unit": ""}],
                "operations": [{"op": "set_aside", "columns": ["STATUS"]}, {"op": "exclude_blank", "column": "VALUE"}],
                "analyses": [{"type": "trend", "columns": ["VALUE"]}, {"type": "distribution", "columns": ["VALUE"]}]}
# the note states the evidence, never a bare "a rate of 0 is a placeholder" (final review, 30 Sep 2026)
ZERO_NOTE_FX = ("(550 zero values in VALUE are not counted: they fall on weekends between non-zero rates, the pattern "
                "of a day with no value)")


def _ols_yearly(pairs):
    """This test's own trend: the OLS slope of each complete calendar year's (all 12 months hold a value) mean
    on the year, from (YYYY-MM-DD, value) pairs."""
    vals, months = {}, {}
    for d, v in pairs:
        vals.setdefault(int(d[:4]), []).append(v)
        months.setdefault(int(d[:4]), set()).add(d[5:7])
    years = sorted(y for y in vals if len(months[y]) == 12)
    ys = [math.fsum(vals[y]) / len(vals[y]) for y in years]
    mx, my = math.fsum(years) / len(years), math.fsum(ys) / len(ys)
    return math.fsum((x - mx) * (y - my) for x, y in zip(years, ys)) / math.fsum((x - mx) ** 2 for x in years)


def test_eval_a_rates_placeholder_zeros_are_not_counted_and_its_trend_is_the_rates_own():
    import pandas as pd
    with open(EVAL_FX, "rb") as fh:
        data = fh.read()
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    rates = [(r["REF_DATE"], float(r["VALUE"])) for r in rows if r["VALUE"] and float(r["VALUE"]) != 0]
    zeros = [r for r in rows if r["VALUE"] and float(r["VALUE"]) == 0]
    assert (len(rows), len(rates), len(zeros)) == (3526, 2407, 550), (len(rows), len(rates), len(zeros))
    want = _ols_yearly(rates)                                                   # the rates' own trend
    as_rates = _ols_yearly(rates + [(r["REF_DATE"], 0.0) for r in zeros])       # the defect: zeros read as rates
    assert abs(want - 0.0104) < 0.0001 and abs(as_rates - 0.0721) < 0.0001, (want, as_rates)
    rep = _run(data, "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    assert rep["ok"], rep["error"]
    A = {a["type"]: a for a in rep["ai_analyses"]["items"]}
    assert set(A) == {"trend", "distribution"}, (sorted(A), rep["ai_analyses"]["refused"])
    tr, dist = A["trend"], A["distribution"]
    slope = float(tr["table"]["rows"][0][3].replace("−", "-"))
    # wave 4 (T1): the slope is the trend test's (Prais-Winsten) on the rates' own yearly averages: this test's yearly
    # averages give the same record, near the rates' least-squares 0.0104 and far from the zeros-as-rates 0.0721
    import nl_inference as NI
    vals = {}
    for d, v in rates:
        vals.setdefault(int(d[:4]), []).append(v)
    yrs = [y for y in sorted(vals) if len({d[5:7] for d, _v in rates if int(d[:4]) == y}) == 12]
    rec = NI.trend_test(yrs, [math.fsum(vals[y]) / len(vals[y]) for y in yrs])
    assert abs(tr["test"]["slope"] - rec["slope"]) < 1e-12 and abs(rec["slope"] - want) < 0.002, (tr["test"], rec, want)
    assert tr["table"]["cols"][3] == "Change per year" and abs(slope - rec["slope"]) <= 0.00005 + 1e-12, (tr["table"], rec)
    assert abs(slope - as_rates) > 0.05, slope
    assert tr["table"]["rows"][0][1:3] == ["2017 to 2025", "9"], tr["table"]["rows"][0]
    assert ZERO_NOTE_FX in tr["sentence"] and ZERO_NOTE_FX in dist["sentence"], (tr["sentence"], dist["sentence"])
    assert ("%s CAD per USD per year" % NB._fmt(rec["slope"])) in tr["sentence"] and \
        "shows no clear rise or fall" in tr["sentence"], tr["sentence"]
    assert dist["table"]["rows"][0][0] == "2,407" and "exactly zero" not in dist["sentence"], dist
    q = pd.Series([v for _d, v in rates]).quantile([0.1, 0.25, 0.5, 0.75, 0.9], interpolation="linear").tolist()
    got = [float(x) for x in dist["table"]["rows"][0][1:]]
    assert all(abs(g - w) <= 0.005 + 1e-12 for g, w in zip(got, q)), (got, q)
    # the Data tests card: a note row right after VALUE's own test, never a failure and never a signal
    tests = rep["contracts"]["tests"]
    i = [t["column"] for t in tests].index("VALUE")
    own, note = tests[i], tests[i + 1]
    assert own["action"] == "passed" and not own.get("note"), own
    assert note["column"] == "VALUE" and note["note"] is True and note["failed"] == 0 and not note["signal"], note
    assert (note["zeros"], note["checked"]) == (550, 2957), note
    assert note["action"] == ("note: 550 zero values in VALUE are not counted by the AI's analyses (they fall on weekends "
                              "between non-zero rates, the pattern of a day with no value); the engine's own reading is "
                              "unchanged and the tests changed no value"), note
    assert "placeholder" not in note["action"], note
    # (nor is the plan's own step on VALUE, which sets aside the 569 rows with no rate: rows with no usable value of the
    # measure are disclosed, never a signal; test_integ_fx_live_plan_replay_...)
    assert not any(s.get("column") == "VALUE" for s in rep["plan_signals"]), rep["plan_signals"]
    # the engine's own input is unchanged: it read the plan's 2,957 rows, zeros and all
    assert rep["cleaning"]["rows_in"] == 2957 and rep["input"]["rows"] == 2957, (rep["cleaning"]["rows_in"], rep["input"]["rows"])
    clean = pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]), dtype=str, keep_default_na=False)
    vcol = next(c for c in clean.columns if c.lower() == "value")
    assert int((pd.to_numeric(clean[vcol], errors="coerce") == 0).sum()) == 550, "the engine's table lost its zeros"
    # the report writer reads the note among the data-test lines; the planner's profile counts the zeros
    out = NB.results_for_ai(rep)
    assert any("550 zero values in VALUE are not counted" in x for x in out["plan_applied"]), out["plan_applied"]
    prof = NB.profile_for_ai(data, "fx_usd_cad.csv")
    pv = next(c for c in prof["columns"] if c["name"] == "VALUE")
    assert pv["zeros"] == 550 and pv["zeros_missing_if_level"] is True, pv
    json.loads(NB.run_json(data, "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29"))


# ------------------------------------------------------------------ the live baseline evaluation (30 Sep 2026)
# The live page on the evaluation's two files (.work/eval/out/baseline-2026-10-01/SCORECARD-draft.md): the FX report led
# with the row count although the AI plan named VALUE, the rate was named "canada" after the constant GEO column, a
# level had no outlook at all, a one-word review scrubbed "this" out of the engine's sentences, keeping the review text
# switched the breakdown away from the departments the question asked about, and a method note was cut mid-word.
EVAL_REVIEWS = os.path.join(HERE, "fixtures", "eval", "reviews_synthetic.csv")    # synthetic (make_reviews.py)
EVAL_FX_CONSTANTS = (("GEO", "Canada"), ("Type of currency", "U.S. dollar, daily average"), ("UOM", "Dollars"),
                     ("SCALAR_FACTOR", "units"))


def _fx_long() -> bytes:
    """The FX fixture in the published table's layout: its REF_DATE, VALUE and STATUS, with the four columns that hold
    one value throughout in StatCan table 33-10-0036-01's U.S. dollar series (GEO, Type of currency, UOM,
    SCALAR_FACTOR). Three of them are metadata, so the rules read the file as a long statistical table, as the live
    page did."""
    with open(EVAL_FX, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["REF_DATE"] + [k for k, _v in EVAL_FX_CONSTANTS] + ["VALUE", "STATUS"])
    for r in rows:
        w.writerow([r["REF_DATE"]] + [v for _k, v in EVAL_FX_CONSTANTS] + [r["VALUE"], r["STATUS"]])
    return buf.getvalue().encode("utf-8")


# the live run's plan (quant-fx/plan-response-2.json), cut to the fixture's columns: it keeps the constant columns
# and names no reshape step, so the rules read the planned file as a long table
EVAL_FX_LONG_PLAN = {
    "goal": "How has the Canadian dollar moved against the U.S. dollar since 2017, and what should a Canadian importer "
            "expect next?", "kind": "time_series_panel", "primary": "VALUE",
    "understanding": "The Bank of Canada daily U.S. dollar rate, one row per date.",
    "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date", "unit": ""},
                {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"},
                {"name": "STATUS", "semantic_type": "code", "role": "metadata", "unit": ""},
                {"name": "GEO", "semantic_type": "geography", "role": "geography", "unit": ""},
                {"name": "Type of currency", "semantic_type": "category", "role": "metadata", "unit": ""},
                {"name": "UOM", "semantic_type": "metadata", "role": "metadata", "unit": ""},
                {"name": "SCALAR_FACTOR", "semantic_type": "metadata", "role": "metadata", "unit": ""}],
    "operations": [{"op": "keep_columns", "columns": ["REF_DATE", "VALUE", "STATUS", "GEO", "Type of currency", "UOM",
                                                      "SCALAR_FACTOR"]},
                   {"op": "exclude_blank", "column": "VALUE"}],
    "analyses": [{"type": "trend", "columns": ["VALUE"]}, {"type": "distribution", "columns": ["VALUE"]}]}


def _fx_rates():
    """The fixture's rates, this test's own reading: (YYYY-MM-DD, rate) for every row with a VALUE that is not 0
    (the 550 weekend zeros mark days with no rate: test_eval_a_rates_placeholder_zeros_are_not_counted_...)."""
    with open(EVAL_FX, encoding="utf-8") as fh:
        return [(r["REF_DATE"], float(r["VALUE"])) for r in csv.DictReader(fh) if r["VALUE"] and float(r["VALUE"]) != 0]


def _monthly_means(pairs):
    by = {}
    for d, v in pairs:
        by.setdefault(d[:7], []).append(v)
    return {m: math.fsum(vs) / len(vs) for m, vs in by.items()}


def _kpi_tiles(rep):
    return next(c for c in rep["charts"] if c["id"] == "kpi")["data"]["tiles"]


def test_eval_b_the_primary_follows_the_plan_on_the_live_fx_layout():
    # live baseline: the plan named VALUE (the USD/CAD rate); results_for_ai's primary, the bottom line, the tiles and
    # the PDF title were the row count ("Rows is up 0.4%"), because the rules then read the file as a long table
    rep = _run(_fx_long(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_LONG_PLAN}, "2026-09-29")
    assert rep["ok"], rep["error"]
    assert (rep["input"].get("layout") or {}).get("layout") == "long statistical table", rep["input"].get("layout")
    pm = rep["primary_metric"]
    assert pm["finding_id"] == "measure.value.change" and pm["claim_key"] == "value", pm
    f = next(x for x in rep["findings"] if x["id"] == "measure.value.change")
    # the engine's claim for VALUE, recomputed here: the average month of the latest 12 months against the 12 before
    mm = _monthly_means(_fx_rates())
    prior = math.fsum(mm["2024-%02d" % k] for k in range(9, 13)) + math.fsum(mm["2025-%02d" % k] for k in range(1, 9))
    latest = math.fsum(mm["2025-%02d" % k] for k in range(9, 13)) + math.fsum(mm["2026-%02d" % k] for k in range(1, 9))
    assert abs(prior / 12 - 1.396492) < 5e-7 and abs(latest / 12 - 1.386262) < 5e-7, (prior / 12, latest / 12)
    assert abs(f["value"] - 100.0 * (latest / prior - 1)) < 1e-9 and round(f["value"], 2) == -0.73, f["value"]
    # the bottom line, the tiles and what the report writer leads with: that claim, never the row count
    line = rep["summary"]["lines"][0]
    assert line["kind"] == "moved" and line["finding_ids"][0] == "measure.value.change", line
    assert line["text"].startswith("Average value (CAD per USD) is down 0.7% on the year before"), line["text"]
    assert _kpi_tiles(rep)[0]["finding_id"] == "measure.value.change", _kpi_tiles(rep)
    out = NB.results_for_ai(rep)
    assert out["primary"] and out["primary"]["id"] == "measure.value.change", out["primary"]
    assert out["primary"]["grade"] == f["grade"] == "WATCH", (out["primary"], f["grade"])
    # the same file without the reshape (the fixture's own three columns) leads with the same claim
    flat = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    assert flat["primary_metric"]["finding_id"] == "measure.value.change", flat["primary_metric"]
    # an amount the engine splits by currency (f3: total:amount:eur, ...): the plan named amount, so its first total
    # leads (the one the scenarios break down), never the row count the gate's own family chose
    f3 = _run(_r4("f3_eur.csv"), "f3_eur.csv", "", {"__plan__": R4_EUR_PLAN}, R4_AS_OF)
    assert f3["primary_metric"]["finding_id"] == _sc(f3)["basis"]["finding_id"] == "measure.amount.total.eur.change", \
        (f3["primary_metric"], _sc(f3)["basis"]["finding_id"])
    assert _kpi_tiles(f3)[0]["finding_id"] == "measure.amount.total.eur.change", _kpi_tiles(f3)
    assert f3["summary"]["lines"][0]["finding_ids"][0] == "measure.amount.total.eur.change", f3["summary"]["lines"][0]
    # the adapter's own headline, when the engine's story has none, quotes the analysis of the plan's primary column
    from northledger import narrate as _narrate
    none = _narrate.NOTHING_HAPPENED_LINE
    fake = {"story": {"headline": none, "what_happened": [none], "cannot_answer": []}, "roles": {"date": "ref_date"},
            "findings": [{"kind": "business", "verdict": "INSUFFICIENT", "columns_read": ["value"]}],
            "ai_plan": {"primary": "VALUE"},
            "ai_analyses": {"items": [{"sentence": "count rose by 3 a year. More.", "columns": ["count"]},
                                      {"sentence": "VALUE rose by 0.0104 CAD per USD per year. More.", "columns": ["VALUE"]}]}}
    NB._true_headline(fake, None, lambda x: x)
    assert fake["story"]["headline"].endswith("From the AI plan's analyses: VALUE rose by 0.0104 CAD per USD per year."), \
        fake["story"]["headline"]


def test_eval_b_a_measure_is_named_from_its_column_and_the_plans_words_never_a_constant():
    # live baseline: "Average canada moved from 1.396 ..." and "Measures canada": the U.S. dollar rate was named after
    # GEO, a column that holds "Canada" on every row
    rep = _run(_fx_long(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_LONG_PLAN}, "2026-09-29")
    assert rep["roles"]["measures"] == ["value"] and rep["input"]["layout"]["order"] == ["VALUE"], \
        (rep["roles"], rep["input"]["layout"]["order"])
    assert rep["input"]["layout"]["lead"] == "VALUE" and rep["input"]["layout"]["lead_why"] == "it is the file's only series"
    blob = json.dumps({k: v for k, v in rep.items() if k != "ai_plan"})
    assert not re.search(r"(?i)\bcanada\b", blob), re.search(r"(?i).{80}\bcanada\b.{40}", blob).group(0)
    assert rep["summary"]["labels"]["measure.value.change"] == "Average value (CAD per USD), the average month", \
        rep["summary"]["labels"]
    assert [a["columns"] for a in rep["ai_analyses"]["items"]] == [["VALUE"], ["VALUE"]], rep["ai_analyses"]["items"]
    assert all(a["sentence"].startswith("VALUE") for a in rep["ai_analyses"]["items"]), \
        [a["sentence"][:40] for a in rep["ai_analyses"]["items"]]
    # the rules alone (no AI plan) name it after its column too
    rules = _run(_fx_long(), "fx_usd_cad.csv", "", None, "2026-09-29")
    assert rules["roles"]["measures"] == ["value"] and rules["primary_metric"]["claim_key"] == "value", \
        (rules["roles"], rules["primary_metric"])
    assert not re.search(r"(?i)\bcanada\b", json.dumps(rules)), "a constant column's value names the measure"
    # the plan's own words for the measure, when it gives them, with its unit; a placeholder unit is no unit
    worded = json.loads(json.dumps(EVAL_FX_LONG_PLAN))
    worded["columns"][1].update(label="USD/CAD exchange rate")
    rep = _run(_fx_long(), "fx_usd_cad.csv", "", {"__plan__": worded}, "2026-09-29")
    assert rep["ai_plan"]["columns"][1]["label"] == "USD/CAD exchange rate", rep["ai_plan"]["columns"][1]
    assert rep["summary"]["labels"]["measure.value.change"] == \
        "Average USD/CAD exchange rate (CAD per USD), the average month", rep["summary"]["labels"]
    assert rep["summary"]["lines"][0]["text"].startswith("Average USD/CAD exchange rate (CAD per USD) is down 0.7%")
    assert NB._measure_label({"unit": "currency"}, "VALUE") == "value" and \
        NB._measure_label({"unit": "CAD"}, "Net_Sales") == "net sales (CAD)", "a placeholder unit is printed"


def _history_want(pairs, lag):
    """This test's own historical range: every month m whose month m - lag also holds a value, the change of the
    monthly average across the two; the 10th, 50th and 90th percentiles (linear interpolation, pandas' default), the
    share that rose, and the count."""
    import pandas as pd
    mm = _monthly_means(pairs)

    def back(m):
        y, k = int(m[:4]), int(m[5:7]) - lag
        while k < 1:
            y, k = y - 1, k + 12
        return "%04d-%02d" % (y, k)
    ch = [mm[m] - mm[back(m)] for m in sorted(mm) if back(m) in mm]
    q = pd.Series(ch).quantile([0.1, 0.5, 0.9], interpolation="linear").tolist()
    last = max(mm)
    gap = lambda m: (int(last[:4]) * 12 + int(last[5:7])) - (int(m[:4]) * 12 + int(m[5:7]))   # noqa: E731
    nov = [mm[m] - mm[back(m)] for m in sorted(mm) if back(m) in mm and gap(m) % lag == 0]
    return {"p10": q[0], "p50": q[1], "p90": q[2], "rose": float(sum(1 for x in ch if x > 0)), "changes": ch,
            "nonoverlap": nov,
            "windows": float(len(ch)), "first": min(mm), "last": max(mm)}


def test_eval_d_a_level_gets_its_historical_range_as_history_not_a_forecast():
    # the engine forecasts counts and totals only: the FX report's only outlook was "20 rows for 2026-09". A level
    # (a rate, a price, an index) with 3 years or more of history now gets the range of its past 12-month and 3-month
    # moves, recomputed here from the fixture's rates, as facts about the past (no grade, group history_range)
    import numpy as np
    import nl_scenarios as NS
    pairs = _fx_rates()
    flat = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    long_ = _run(_fx_long(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_LONG_PLAN}, "2026-09-29")
    for rep, where in ((flat, "the fixture's columns"), (long_, "the published layout")):
        sc = _sc(rep)
        H = {it["id"]: it for it in sc["items"] if it["group"] == "history_range"}
        # wave 4 (T3): per lag the windows, their effective count, the 10th/50th/90th percentiles (13 and 43
        # independent windows here, so not the extremes), the windows that rose (a count), and the non-overlapping
        # changes (n, min, median, max)
        assert len(H) == 20, (where, sorted(H), sc["refused"])
        import nl_inference as NI
        for lag in NS.HISTORY_LAGS:
            want = _history_want(pairs, lag)
            want.update({"n_eff": float(round(NI.n_eff_overlapping(want["changes"], lag))),
                         "nonoverlap.n": float(len(want["nonoverlap"])),
                         "nonoverlap.min": min(want["nonoverlap"]), "nonoverlap.max": max(want["nonoverlap"]),
                         "nonoverlap.median": float(np.percentile(want["nonoverlap"], 50))})
            b = "history_range.m%d" % lag
            for k in ("p10", "p50", "p90", "rose", "windows", "n_eff", "nonoverlap.n", "nonoverlap.min",
                      "nonoverlap.median", "nonoverlap.max"):
                it = H["%s.%s" % (b, k)]
                w = NI.sig2(want[k]) if it["kind"] == "change" else want[k]       # 2 significant figures
                assert abs(it["value"] - w) <= 1e-6, (where, it["id"], it["value"], w)
                assert it["grade"] is None and it["parent_grade"] is None, it
                assert it["grade_words"] == "a fact about the file's past, not graded: history, not a forecast", it
                assert it["text"] == NS._fmt_item(it["value"], it["kind"], it["unit"],
                                                  sig=2 if it["kind"] == "change" else None), it
                assert "history" in it["label"] and "not a forecast" in it["label"], it["label"]
                assert it["inputs"]["window"] == "history" and it["inputs"]["columns"] == ["ref_date", "value"], it
            assert (H[b + ".p10"]["kind"], H[b + ".p10"]["unit"]) == ("change", "CAD per USD"), H[b + ".p10"]
            assert (H[b + ".rose"]["kind"], H[b + ".rose"]["unit"]) == ("count", ""), H[b + ".rose"]
            assert want["windows"] == {12: 104.0, 3: 113.0}[lag] and (want["first"], want["last"]) == ("2017-01", "2026-08")
            assert H[b + ".n_eff"]["value"] == {12: 13.0, 3: 43.0}[lag], H[b + ".n_eff"]
            t = {k: H["%s.%s" % (b, k)]["text"] for k in ("p10", "p50", "p90", "nonoverlap.min", "nonoverlap.max",
                                                          "nonoverlap.median")}
            every = "one window a year, each ending in Aug" if lag == 12 else \
                "one window every 3 months, the latest ending in Aug 2026"
            assert H[b + ".windows"]["label"] == (
                "In the %d past %d-month windows (Jan 2017 to Aug 2026; they overlap, one ending each month, so they are "
                "worth about %d independent ones), the change in the monthly average of value ran from %s (1 in 10 "
                "lower) to %s (1 in 10 higher); the middle was %s, and it rose in %d of the %d. Taking %s, the %d "
                "changes ran from %s to %s, with a middle of %s. This is history, not a forecast."
                % (want["windows"], lag, want["n_eff"], t["p10"], t["p90"], t["p50"], want["rose"], want["windows"],
                   every, want["nonoverlap.n"], t["nonoverlap.min"], t["nonoverlap.max"], t["nonoverlap.median"])), \
                H[b + ".windows"]["label"]
        assert NS.HISTORY_NOTE in sc["note"], sc["note"]
    # the fixture's own path keeps the engine's zeros (its downloads do): the range counts them as no rate, and says so
    zero = [it["assumes"] for it in _sc(flat)["items"] if it["group"] == "history_range"]
    assert set(zero) == {"550 zero values in VALUE are not counted: they fall on weekends between non-zero rates, the "
                         "pattern of a day with no value"}, set(zero)
    # the report writer receives them 1:1, in the block's order (before the facts)
    out = NB.results_for_ai(flat)
    assert [it["id"] for it in out["scenarios"]["items"] if it["group"] == "history_range"] == \
        [it["id"] for it in _sc(flat)["items"] if it["group"] == "history_range"]
    # never a flow, a count or a rating: the ticket file's cost (an amount) and the reviews (a row count) have none
    for rep in (_run(_r4("f3_eur.csv"), "f3_eur.csv", "", {"__plan__": R4_EUR_PLAN}, R4_AS_OF),
                _run(open(EVAL_REVIEWS, "rb").read(), "reviews_synthetic.csv", "", None, "2026-09-30")):
        assert not [it for it in _sc(rep)["items"] if it["group"] == "history_range"], _sc(rep)["items"][:2]
    # too short: a level with 24 months of history is refused, and the refusal says why
    short = "\n".join(["REF_DATE,VALUE"] + ["2024-%02d-15,%.4f" % (m, 1.3 + m / 100) for m in range(1, 13)]
                      + ["2025-%02d-15,%.4f" % (m, 1.4 + m / 100) for m in range(1, 13)]).encode() + b"\n"
    plan = {"goal": "How has the rate moved?", "primary": "VALUE",
            "columns": [{"name": "REF_DATE", "semantic_type": "date", "role": "date"},
                        {"name": "VALUE", "semantic_type": "level", "role": "target", "unit": "CAD per USD"}]}
    sc = _sc(_run(short, "short.csv", "", {"__plan__": plan}, "2026-01-15"))
    assert not [it for it in sc["items"] if it["group"] == "history_range"], sc["items"]
    assert "no historical range of the monthly average of value: it has 24 months with a value, fewer than the 36 " \
           "(3 years) it needs" in sc["refused"], sc["refused"]


def test_eval_e_the_scrubber_never_scrubs_a_common_word_and_still_scrubs_a_name():
    # live baseline: with review_text withheld (the default), one review whose whole text was "this" turned every "this"
    # in the engine's own sentences into "[withheld]" (62 in one PDF: "a result at least [withheld] strong")
    data = open(EVAL_REVIEWS, "rb").read()
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    short = [r["review_text"] for r in rows if len(r["review_text"]) < 12]
    assert sorted(short) == sorted(["this", "good", "free", "yes", "Excellent", "Great game"]), short
    rep = _run(data, "reviews_synthetic.csv", "Which departments stand out?", None, "2026-09-30")
    assert rep["ok"], rep["error"]
    assert rep["privacy"]["flagged"] == [{"column": "review_text", "kind": "free text", "decision": "withhold"}]
    blob = json.dumps({k: v for k, v in rep.items() if k != "downloads"})
    assert NB.WITHHELD_MARK not in blob, re.search(r".{60}\[withheld\].{30}", blob).group(0)
    assert "a result at least this strong" in blob, "the engine's own sentence is not there to check"
    assert NB.WITHHELD_MARK not in json.dumps(NB.results_for_ai(rep))
    # the withheld column itself never leaves: not its name where an AI reads, not a review longer than a few words
    _assert_withheld(rep, "review_text", [r["review_text"] for r in rows if len(r["review_text"]) >= 20])
    # the rule: a free-text value only whole and 20 characters or more; any other value 2 words or more, or one word
    # of 6 characters or more that is not a common English word; never a number
    s = NB.Scrubber(["Marisol Fairweather", "Fairweather", "Zelda", "Excellent", "Pending", "52,000", "x@example.com",
                     "1994-12-16"], free_text=["this", "good", "Great game", "yes",
                                               "It stopped working after two weeks of use."])
    assert s.clean("a result at least this strong; certify a good one; yes") == \
        "a result at least this strong; certify a good one; yes"
    assert s.clean("Excellent value, Pending, 52,000 and a Great game") == "Excellent value, Pending, 52,000 and a Great game"
    assert s.clean("ask Marisol Fairweather today") == "ask [withheld] today"
    assert s.clean("the Fairweather account") == "the [withheld] account"
    assert s.clean("mail x@example.com on 1994-12-16") == "mail [withheld] on [withheld]"
    assert s.clean("quoted 'It stopped working after two weeks of use.' here") == "quoted '[withheld]' here"
    assert s.clean("Zelda said") == "Zelda said", "a single word under 6 characters identifies no one"
    assert not NB._specific("this") and not NB._specific("average") and not NB._specific("52,000") and \
        NB._specific("fairweather") and NB._specific("leaf cleanup") and not NB._specific("great game", True) and \
        NB._specific("it stopped working after two weeks", True)


def test_eval_c_the_scenarios_segment_follows_the_goal():
    # live baseline: the question asked which departments stand out; keeping review_text switched the breakdown from
    # department (45 items) to verified_purchase (21 items), the first of the engine's dimensions
    data = open(EVAL_REVIEWS, "rb").read()
    ask = "What do customers praise and complain about, and which departments stand out?"
    for dec in (None, {"review_text": "keep"}):
        rep = _run(data, "reviews_synthetic.csv", ask, dec, "2026-09-30")
        assert rep["roles"]["dimensions"][:2] == ["verified_purchase", "department"], rep["roles"]
        assert _sc(rep)["basis"]["segment"]["column"] == "department", (dec, _sc(rep)["basis"]["segment"])
        _sc_check_recomputed(rep, where="reviews by department (%s)" % (dec or "defaults"))
    # a question that names no column: the existing order (the first dimension that qualifies)
    rep = _run(data, "reviews_synthetic.csv", "", {"review_text": "keep"}, "2026-09-30")
    assert _sc(rep)["basis"]["segment"]["column"] == "verified_purchase", _sc(rep)["basis"]["segment"]
    # with a plan: the goal's column first, then the plan's segment roles (verified_purchase is listed first)
    plan = {"goal": "Which department grows fastest?", "kind": "transactions",
            "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                        {"name": "verified_purchase", "semantic_type": "category", "role": "segment"},
                        {"name": "department", "semantic_type": "category", "role": "segment"},
                        {"name": "rating", "semantic_type": "rating", "role": "driver"}],
            "operations": [], "analyses": []}
    rep = _run(data, "reviews_synthetic.csv", "", {"__plan__": plan}, "2026-09-30")
    assert _sc(rep)["basis"]["segment"]["column"] == "department", _sc(rep)["basis"]["segment"]
    rep = _run(data, "reviews_synthetic.csv", "", {"__plan__": dict(plan, goal="How are reviews moving?")}, "2026-09-30")
    assert _sc(rep)["basis"]["segment"]["column"] == "verified_purchase", _sc(rep)["basis"]["segment"]
    import nl_scenarios as NS
    assert NS._goal_first(["a_b", "region", "store"], "Which stores and regions lead?", {}) == ["store", "region", "a_b"]
    assert NS._goal_first(["category", "store"], "Which categories?", {}) == ["category", "store"]
    assert NS._goal_first(["verified_purchase", "department"], "verified purchases or not?", {}) == \
        ["verified_purchase", "department"]


def test_eval_f_price_volume_mix_states_its_base_not_an_assumption():
    # review of the live run: "on the assumption that price, volume and mix add up to the change"; they add up by
    # construction (the block reconciles them), so the item states the base they are measured against
    import nl_scenarios as NS
    assert NS.PVM_ASSUMES == "measured against the 12 months before; the three parts add up exactly to the change"
    assert NS.PV_ASSUMES == "measured against the 12 months before; the two parts add up exactly to the change"
    it = _sc_items(_ship2())
    assert {it["price_volume_mix." + k]["assumes"] for k in ("price", "volume", "mix")} == {NS.PVM_ASSUMES}
    assert not any("assumption" in (x.get("assumes") or "") for x in it.values())


def test_eval_g_an_analysis_method_is_cut_at_a_word_never_mid_word():
    # review of the live run: a method note reached the report as "...trained only on the blocks befor" (the payload's
    # 300-character cap, which is the worker's own)
    rep = NB.run(_fx2("a_personal.csv", PRIVACY), "orders.csv", "", {"__plan__": _a_plan(False)}, "2026-09-15")
    pr = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "predict")
    assert len(pr["method"]) > 300, len(pr["method"])
    out = NB.results_for_ai(rep)
    got = next(a for a in out["analyses"] if a["title"] == pr["title"])["method"]
    assert len(got) <= 300 and got.endswith("…"), (len(got), got[-40:])
    head = got[:-1]
    assert pr["method"].startswith(head) and pr["method"][len(head)] == " ", (head[-30:], pr["method"][len(head):][:20])
    v = _proxy_validate(out)
    assert v is None or next(a for a in v["value"]["analyses"] if a["title"] == pr["title"])["method"] == got, v
    # a text that fits is sent whole; the placeholder for the file's name is never cut
    assert NB.ANALYSIS_TEXT_MAX == {"sentence": 700, "method": 300} and NB._cut_words("short", 300) == "short"
    assert NB._cut_words("the trend of %s is up" % NB.FILE_WORD, 16) == "the trend of…"


def _daily_file():
    """Ten years of daily rows: sales (about 6% of days sell nothing: a real 0), price (a level with 3 zeros in
    3,653 days: under 1%) and balance (a level that is often negative, with zeros)."""
    import random as _random
    rng = _random.Random(20260930)
    rows, d = [], datetime.date(2016, 1, 1)
    while d <= datetime.date(2025, 12, 31):
        k = (d - datetime.date(2016, 1, 1)).days
        sales = 0.0 if rng.random() < 0.06 else round(rng.uniform(50, 150) * (1 + k / 3653.0), 2)
        price = 0.0 if k in (100, 1000, 2000) else round(20 + k / 365.0 + rng.uniform(-1, 1), 3)
        balance = 0.0 if rng.random() < 0.05 else round(rng.uniform(-500, 800), 2)
        rows.append([d.isoformat(), "%.2f" % sales, "%.3f" % price, "%.2f" % balance])
        d += datetime.timedelta(days=1)
    return _csv(rows, ["day", "sales", "price", "balance"])


def _daily_plan(sales_type):
    return {"goal": "How are sales moving?", "primary": "sales",
            "columns": [{"name": "day", "semantic_type": "date", "role": "date"},
                        {"name": "sales", "semantic_type": sales_type, "role": "target", "unit": "USD"},
                        {"name": "price", "semantic_type": "level", "role": "driver", "unit": "USD"},
                        {"name": "balance", "semantic_type": "level", "role": "driver", "unit": "USD"}],
            "operations": [],
            "analyses": [{"type": "distribution", "columns": ["sales"]}, {"type": "trend", "columns": ["sales"]},
                         {"type": "distribution", "columns": ["price"]}, {"type": "distribution", "columns": ["balance"]}]}


def test_eval_a_flows_real_zeros_are_counted_and_the_placeholder_rule_reads_only_a_level():
    data = _daily_file()
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    n, z = len(rows), sum(1 for r in rows if float(r["sales"]) == 0)
    assert n == 3653 and 0.05 * n < z < 0.08 * n, (n, z)
    for st in ("flow_amount", "count"):
        rep = _run(data, "daily.csv", "", {"__plan__": _daily_plan(st)}, "2026-09-15")
        assert rep["ok"], rep["error"]
        items = rep["ai_analyses"]["items"]
        assert [a["type"] for a in items] == ["distribution", "trend", "distribution", "distribution"], rep["ai_analyses"]
        sales = items[0]
        # a day that sold nothing is a real 0: every value counts, and the sentence says how many are zero
        assert sales["table"]["rows"][0][0] == format(n, ","), (st, sales["table"])
        assert "%s%% of the values are exactly zero" % NB._fmt(100.0 * z / n) in sales["sentence"], sales["sentence"]
        blob = json.dumps(rep["ai_analyses"]) + json.dumps(rep.get("contracts"))
        assert "placeholder" not in blob, (st, [a["sentence"] for a in items])
        assert not any(t.get("note") for t in rep["contracts"]["tests"]), rep["contracts"]["tests"]
        # the yearly totals count the zero days as 0: the last year's total is the rows' own sum
        tr = items[1]
        last = math.fsum(float(r["sales"]) for r in rows if r["day"].startswith("2025"))
        yr = [ln for ln in tr["chart"]["series"] if ln["name"] == "sales"][0]
        assert abs(yr["y"][-1] - last) <= 1e-6 * last, (yr["y"][-1], last)
    # a level under 1% zeros (price) or with negative values (balance) keeps its zeros too
    for a in items[2:]:
        assert a["table"]["rows"][0][0] == format(n, ","), a["table"]
    # typed a level, the same sales keep their zeros too (final review, 30 Sep 2026): days with nothing sold fall at
    # random, not on given weekdays, and the sales either side of them jump about rather than pick up where they left
    # off, so nothing says 0 stands for "no value" (the share of zeros alone deleted real zeros)
    rep = _run(data, "daily.csv", "", {"__plan__": _daily_plan("level")}, "2026-09-15")
    sales = rep["ai_analyses"]["items"][0]
    assert sales["table"]["rows"][0][0] == format(n, ","), sales["table"]
    assert "not counted" not in sales["sentence"] and not any(t.get("note") for t in rep["contracts"]["tests"]), \
        sales["sentence"]
    assert [a["table"]["rows"][0][0] for a in rep["ai_analyses"]["items"][2:]] == [format(n, ",")] * 2


def _reviews_file():
    """Two full years of reviews: two large brands, a mid one, eight brands with 1 to 4 five-star reviews in 2022,
    and one brand with 5 reviews in 2022 (exactly the minimum)."""
    import random as _random
    rng = _random.Random(30)
    rows = []

    def day(y):
        return datetime.date(y, 1, 1) + datetime.timedelta(days=rng.randint(0, 364))
    for y in (2021, 2022):
        for _ in range(600):
            rows.append([day(y).isoformat(), "BigCo", str(rng.choice([1, 3, 4, 4, 5, 5]))])
            rows.append([day(y).isoformat(), "Gigant", str(rng.choice([2, 3, 4, 5, 5]))])
        for _ in range(40):
            rows.append([day(y).isoformat(), "MidCo", str(rng.choice([4, 4, 5, 5, 5]))])
    for i in range(8):
        for _ in range(1 + i % 4):
            rows.append([day(2022).isoformat(), "Tiny%d" % i, "5"])
    for _ in range(5):
        rows.append([day(2022).isoformat(), "JustFive", "5"])
    rng.shuffle(rows)
    return _csv(rows, ["review_date", "brand", "rating"])


def test_eval_a_ranking_of_groups_rests_on_5_rows_each_and_counts_the_rest():
    import pandas as pd
    data = _reviews_file()
    plan = {"goal": "Which brands rate best?", "primary": "rating",
            "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                        {"name": "brand", "semantic_type": "entity", "role": "entity"},
                        {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"}],
            "operations": [],
            "analyses": [{"type": "rank", "columns": ["brand", "rating"]}, {"type": "compare", "columns": ["rating"], "by": "brand"}]}
    rep = _run(data, "reviews.csv", "", {"__plan__": plan}, "2026-09-15")
    assert rep["ok"], rep["error"]
    A = {a["type"]: a for a in rep["ai_analyses"]["items"]}
    assert set(A) == {"rank", "compare"}, rep["ai_analyses"]
    df = pd.read_csv(io.BytesIO(data), dtype=str)
    n22 = df[df["review_date"].str[:4] == "2022"].groupby("brand").size()
    rk = A["rank"]
    named = [r[0] for r in rk["table"]["rows"]]
    assert named and all(int(n22[b]) >= NB.RANK_MIN_ROWS for b in named), [(b, int(n22[b])) for b in named]
    assert not any(b.startswith("Tiny") for b in named) and "JustFive" in named, named
    assert NB.RANK_MIN_ROWS == NB.COMPARE_MIN_GROUP == 5
    assert "8 brand entries with fewer than 5 rows in 2022 are not ranked." in rk["sentence"], rk["sentence"]
    assert "an entry resting on fewer than 5 rows that year is not ranked" in rk["method"], rk["method"]
    assert not any(k.startswith("Tiny") for k in (rk.get("map") or {}).get("values") or {}), rk["map"]
    cmp_ = A["compare"]
    assert not any(r[0].startswith("Tiny") for r in cmp_["table"]["rows"]), cmp_["table"]["rows"]
    assert "8 groups with fewer than 5 rows are left out." in cmp_["sentence"], cmp_["sentence"]
    # with no date: an entry's figure over the file rests on 5 rows too
    plan2 = json.loads(json.dumps(plan))
    plan2["columns"][0] = {"name": "review_date", "semantic_type": "metadata", "role": "metadata"}
    plan2["analyses"] = plan2["analyses"][:1]
    rep2 = _run(data, "reviews.csv", "", {"__plan__": plan2}, "2026-09-15")
    rk2 = rep2["ai_analyses"]["items"][0]
    assert not any(r[0].startswith("Tiny") for r in rk2["table"]["rows"]), rk2["table"]["rows"]
    assert "8 brand entries with fewer than 5 rows are not ranked." in rk2["sentence"], rk2["sentence"]
    # a panel (one row per entry and year) ranks each entry's own figure: the test above of a country panel, and here
    rows = ["country,year,co2"] + ["C%02d,%d,%d" % (i, y, 100 - i + y - 2000) for i in range(12) for y in range(2000, 2011)]
    plan3 = {"goal": "Who emits most?", "operations": [{"op": "date_from_year", "column": "year"}],
             "columns": [{"name": "country", "role": "entity"}, {"name": "year", "role": "date"},
                         {"name": "co2", "role": "target", "semantic_type": "flow_amount"}],
             "analyses": [{"type": "rank", "columns": ["country", "co2"]}]}
    rep3 = _run(("\n".join(rows) + "\n").encode(), "panel.csv", "", {"__plan__": plan3}, "2026-09-15")
    rk3 = rep3["ai_analyses"]["items"][0]
    assert [r[0] for r in rk3["table"]["rows"]][:3] == ["C00", "C01", "C02"] and "not ranked" not in rk3["sentence"], rk3


def _segments_file(levels=12):
    """26 months of orders over `levels` regions with revenue and units; the later regions grow faster."""
    import random as _random
    rng = _random.Random(11)
    regs = ["R%02d" % i for i in range(levels)]
    rows = []
    for k in range(26):
        y, m = 2024 + k // 12, k % 12 + 1
        for i, rg in enumerate(regs):
            for _ in range(8):
                u = rng.randint(1, 20)
                p = rng.uniform(30, 50) * (1 + (0.02 * i if k >= 14 else 0.0))
                rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), rg, "%.2f" % (u * p), "%d" % u])
    return _csv(rows, ["order_date", "region", "revenue", "units"])


def _proxy_cap(items, max_items):
    """insight-proxy/src/report.js capScenarioItems(items, max_items), run by Node, when both are here; else None."""
    report_js = os.path.normpath(os.path.join(SITE, "..", "insight-proxy", "src", "report.js"))
    node = shutil.which("node")
    if not node or not os.path.exists(report_js):
        return None
    script = ("import { capScenarioItems } from %s;\n"
              "let s = ''; process.stdin.on('data', (d) => { s += d; });\n"
              "process.stdin.on('end', () => { const a = JSON.parse(s);"
              " process.stdout.write(JSON.stringify(capScenarioItems(a.items, a.max).map((x) => x.id))); });\n"
              ) % json.dumps("file://" + report_js)
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "c.mjs")
        with open(p, "w") as fh:
            fh.write(script)
        out = subprocess.run([node, p], input=json.dumps({"items": items, "max": max_items}), capture_output=True,
                             text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-600:]
    return json.loads(out.stdout)


def test_scenarios_come_in_the_order_the_workers_cap_keeps_them():
    # 30 Sep 2026: the worker keeps up to 160 items (capScenarioItems): the core is every group with the per-segment
    # groups (contribution, per_unit, gap) cut to the 6 segments with the largest |contribution|; the rest follow by
    # that rank. The adapter's order is the same, so a reader that keeps the first N keeps what the cap keeps.
    import nl_scenarios as NS
    assert NS.TOP_SEGMENTS == 6 and NS.PER_SEGMENT == ("contribution", "per_unit", "gap")
    rep = _run(_segments_file(12), "twelve.csv", "", None, "2026-03-15")
    assert rep["ok"], rep["error"]
    sc = _sc(rep)
    b = sc["basis"]
    assert b["reconciles"] and b["segment"]["column"] == "region" and len(b["segment"]["levels"]) == 12, b
    items = sc["items"]
    _sc_check_recomputed(rep, where="twelve regions")
    # R00 and R01 fell while the total rose: no share of the change is given (12 items fewer than with shares)
    assert NS.MIXED_SHARES in sc["refused"] and not any(it["id"].endswith(".share") for it in items), sc["refused"]
    assert len(items) == 134, (len(items), {g: sum(1 for it in items if it["group"] == g) for g in NS.GROUPS})
    # the segments ranked by the size of their contribution to the change, independently of the adapter
    size = {it["segment"]: abs(it["value"]) for it in items if it["group"] == "contribution" and it["id"].endswith(".change")}
    rank = sorted(size, key=lambda s: (-size[s], s))
    top = set(rank[:6])
    ids = [it["id"] for it in items]
    by = {it["id"]: it for it in items}
    order = {g: i for i, g in enumerate(NS.GROUPS)}
    beyond = lambda i: by[i]["group"] in NS.PER_SEGMENT and by[i]["segment"] is not None and by[i]["segment"] not in top
    core = [i for i in ids if not beyond(i)]
    assert ids[:len(core)] == core, "the core does not come first"
    assert [order[by[i]["group"]] for i in core] == sorted(order[by[i]["group"]] for i in core), [by[i]["group"] for i in core]
    for g in NS.GROUPS:
        if g not in ("forecast", "history_range"):          # a total: no usable forecast here, and no level's history
            assert any(by[i]["group"] == g for i in core), g
    for seg in top:
        for g in ("contribution", "per_unit"):
            assert any(by[i]["group"] == g and by[i]["segment"] == seg for i in core), (g, seg)
    rest = ids[len(core):]
    key = [(rank.index(by[i]["segment"]), order[by[i]["group"]]) for i in rest]
    assert rest and key == sorted(key), "the rest is not segment by segment in the rank of their contributions"
    assert len(core) == 74 and len(rest) == 60, (len(core), len(rest))
    # the worker's own cap keeps a prefix of this order at every size it can be cut to (the core always fits in 160)
    for n in (len(core), len(core) + 10, 120, 130, len(ids)):
        got = _proxy_cap(items, n)
        if got is None:
            break
        assert got == ids[:n], (n, [i for i in got if i not in ids[:n]][:4])
    got = _proxy_validate(NB.results_for_ai(rep))
    if got is not None:
        assert [x["id"] for x in got["value"]["scenarios"]["items"]] == ids, "the proxy did not keep every item in order"
    # with 4 segments (ship2) every segment is in the core: every item comes in the order of the groups
    ids2 = [it for it in _sc(_ship2())["items"]]
    g2 = [order[it["group"]] for it in ids2]
    assert g2 == sorted(g2) and len(ids2) == 58, [it["id"] for it in ids2]


# a 12-segment file whose report writer's payload is over the budget: an AI plan with six analyses, twelve sales
# territories with names of 30 to 33 characters (a longer name reads as free text and is withheld), revenue and units
_TERRITORIES = ["Northern", "Southern", "Eastern", "Western", "Central", "Coastal", "Inland", "Mountain", "Valley", "River",
                "Lake", "Prairie"]
TERRITORY_PLAN = {
    "goal": "Which sales territories drove the change in revenue, and what should the business do about it?",
    "primary": "revenue",
    "understanding": "Orders with a date, the sales territory, the revenue in Canadian dollars and the units sold.",
    "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                {"name": "sales_territory", "semantic_type": "category", "role": "segment"},
                {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "CAD"},
                {"name": "units", "semantic_type": "count", "role": "driver", "unit": "units"}],
    "operations": [],
    "analyses": [{"type": "compare", "columns": ["revenue"], "by": "sales_territory"},
                 {"type": "rank", "columns": ["sales_territory", "revenue"]},
                 {"type": "relationship", "columns": ["units", "revenue"]},
                 {"type": "distribution", "columns": ["revenue"]},
                 {"type": "compare", "columns": ["units"], "by": "sales_territory"},
                 {"type": "predict", "columns": ["revenue", "units", "sales_territory"]}]}


def _territories_file():
    import random as _random
    rng = _random.Random(11)
    rows = []
    for k in range(26):
        y, m = 2024 + k // 12, k % 12 + 1
        for i, w in enumerate(_TERRITORIES):
            for _ in range(8):
                u = rng.randint(1, 20)
                p = rng.uniform(30, 50) * (1 + (0.02 * i if k >= 14 else 0.0))
                rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), "%s wholesale and retail territory" % w,
                             "%.2f" % (u * p), "%d" % u])
    return _csv(rows, ["order_date", "sales_territory", "revenue", "units"])


def test_results_for_ai_keeps_under_its_byte_budget_dropping_scenario_items_from_the_tail():
    # 30 Sep 2026: the worker takes a /report body of at most 96,000 bytes; past 90,000 bytes of results the adapter
    # drops scenario items from the tail of the cap's order: the facts, then the detail beyond the top 6 segments
    import nl_scenarios as NS
    assert NB.RESULTS_MAX_BYTES == 90000
    rep = _run(_territories_file(), "territories.csv", "", {"__plan__": TERRITORY_PLAN}, "2026-03-15")
    assert rep["ok"], rep["error"]
    sc = _sc(rep)
    assert sc["basis"]["segment"]["column"] == "sales_territory" and len(sc["basis"]["segment"]["levels"]) == 12, sc["basis"]
    assert len(sc["items"]) == 134 and not rep["privacy"]["flagged"], (len(sc["items"]), rep["privacy"]["flagged"])
    old = NB.RESULTS_MAX_BYTES
    NB.RESULTS_MAX_BYTES = 10 ** 9
    try:
        whole = NB.results_for_ai(rep)
    finally:
        NB.RESULTS_MAX_BYTES = old
    assert len(json.dumps(whole)) > 90000, "the file no longer exceeds the budget, so this test tests nothing: %d" % len(json.dumps(whole))
    got = NB.results_for_ai(rep)
    body = json.dumps(got, allow_nan=False)
    assert len(body.encode("utf-8")) <= 90000 and len(NB.results_json(rep)) <= 90000, len(body)
    ids = [it["id"] for it in whole["scenarios"]["items"]]
    by = {it["id"]: it for it in whole["scenarios"]["items"]}
    kept = [it["id"] for it in got["scenarios"]["items"]]
    assert kept == [i for i in ids if i in set(kept)], "the kept items changed order"
    dropped = [i for i in ids if i not in set(kept)]
    facts = [i for i in ids if by[i]["group"] == "facts"]
    assert dropped and set(facts) <= set(dropped), ("the facts go first", dropped)
    size = {by[i]["segment"]: abs(by[i]["value"]) for i in ids if by[i]["group"] == "contribution" and i.endswith(".change")}
    rank = sorted(size, key=lambda s: (-size[s], s))
    tail = [i for i in dropped if i not in facts]
    # wave 4, track B step 0: what goes after the facts is the detail of the parts beyond the 6 largest, and never an item
    # that moved against the headline change (a part that fell while the total rose)
    head = by["headline.change"]["value"]
    against = [i for i in ids if by[i]["group"] == "contribution" and by[i]["kind"] == "change" and by[i]["value"] * head < 0]
    assert against, "this file has no part that moved against the change, so the test tests nothing"
    assert not set(against) & set(dropped), ("an item that moved against the headline change was dropped", sorted(set(against) & set(dropped)))
    assert tail and all(by[i]["group"] in NS.PER_SEGMENT and rank.index(by[i]["segment"]) >= 6 for i in tail), tail
    # the parts left out are the smallest of those beyond the 6 largest: a part kept beyond them is larger than any whole part dropped
    gone = {by[i]["segment"] for i in tail}
    whole_gone = {s_ for s_ in gone if all(i in set(dropped) for i in ids if by[i]["segment"] == s_ and by[i]["group"] in NS.PER_SEGMENT)}
    against_segs = {by[i]["segment"] for i in against}
    beyond_kept = {by[i]["segment"] for i in kept if by[i]["group"] in NS.PER_SEGMENT and by[i]["segment"] in size
                   and rank.index(by[i]["segment"]) >= 6 and by[i]["segment"] not in against_segs}
    assert not [s_ for s_ in whole_gone for k_ in beyond_kept if size[k_] < size[s_]], (whole_gone, beyond_kept)
    # no more than it needs: with the item dropped last put back, the payload is over the budget
    order = NB._budget_drop_order(whole["scenarios"]["items"], whole["scenarios"]["basis"])
    pos = {whole["scenarios"]["items"][k]["id"]: n for n, k in enumerate(order)}
    last = max(dropped, key=lambda i: pos[i])
    back = dict(got, scenarios=dict(got["scenarios"], items=[it for it in whole["scenarios"]["items"] if it["id"] in set(kept) | {last}]))
    assert len(json.dumps(back)) > 90000, "the budget dropped more than it needed"
    # the writer is told first, and the "Where the change came from" table is rebuilt from the items kept
    assert got["scenarios"]["refused"][0] == NB.BUDGET_REFUSED % (len(dropped), "134", "90,000"), got["scenarios"]["refused"][:1]
    assert got["scenarios"]["refused"][1:] == whole["scenarios"]["refused"]
    drove = got["tables"][-1]
    assert drove["title"] == whole["tables"][-1]["title"] and drove["title"].startswith("Where the change"), drove["title"]
    segs = []
    for it in got["scenarios"]["items"]:
        if it["group"] == "contribution" and it["segment"] not in segs:
            segs.append(it["segment"])
    assert [r[0] for r in drove["rows"]] == segs[:12] and "Share of the change" not in drove["cols"], drove
    for k in whole:
        if k not in ("scenarios", "tables"):
            assert got[k] == whole[k], k
    assert got["tables"][:-1] == whole["tables"][:-1] and got["scenarios"]["basis"] == whole["scenarios"]["basis"]
    # the worker takes it whole: every kept item passes its checks, within its 160-item cap, and the /report body fits
    v = _proxy_validate(got)
    if v is not None:
        assert v["ok"] and [x["id"] for x in v["value"]["scenarios"]["items"]] == kept, v.get("detail")
    req = json.dumps({"objective": TERRITORY_PLAN["goal"], "results": got, "context_queries": ["x" * 120] * 3})
    assert len(req.encode("utf-8")) <= 96000, len(req)
    # a payload under the budget is untouched: the twelve-region file keeps all 134 items, as the report holds them
    rep2 = _run(_segments_file(12), "twelve.csv", "", None, "2026-03-15")
    r2 = NB.results_for_ai(rep2)
    assert len(json.dumps(r2)) <= 90000 and [it["id"] for it in r2["scenarios"]["items"]] == [it["id"] for it in rep2["scenarios"]["items"]]
    assert not any(str(x).startswith(NB.BUDGET_REFUSED.split("%s", 2)[1].strip()[:20]) for x in r2["scenarios"]["refused"])


# ---- groups ranked by an average: the IMDb-style weighted rating (evaluation preflight, 30 Sep 2026)
EVAL_BRANDS = os.path.join(HERE, "fixtures", "eval", "brand_ratings_2022.csv")   # make_brand_ratings.py: counts only
BRAND_PLAN = {"goal": "Which brands do customers rate best?", "primary": "rating",
              "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                          {"name": "brand", "semantic_type": "entity", "role": "entity"},
                          {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"},
                          {"name": "review_no", "semantic_type": "identifier", "role": "key"}],
              "operations": [],
              "analyses": [{"type": "rank", "columns": ["brand", "rating"]}, {"type": "compare", "columns": ["rating"], "by": "brand"}]}


def _brand_reviews():
    """(a reviews file standing for the fixture's counts, {brand: [1-star .. 5-star counts]}): one row per review,
    dated the 15th of each month of 2022 in turn (so a brand with more than 12 reviews has several on one day, as
    reviews do: not a panel of one row per brand and date), numbered so that no row repeats another."""
    with open(EVAL_BRANDS, encoding="utf-8", newline="") as fh:
        counts = {r["brand"]: [int(r["stars_%d" % s]) for s in range(1, 6)] for r in csv.DictReader(fh)}
    rows, k = [], 0
    for b in sorted(counts):
        for star, n in enumerate(counts[b], 1):
            for _ in range(n):
                rows.append(["2022-%02d-15" % (k % 12 + 1), b, str(star), "R%05d" % k])
                k += 1
    return _csv(rows, ["review_date", "brand", "rating", "review_no"]), counts


def test_eval_a_ranking_of_averages_weighs_each_brand_by_its_rows():
    # the preflight's reviews (5,581 in 2022): with the 5-row minimum, the ranking by the plain average was topped by
    # brands with 5 to 8 five-star reviews; weighted, each brand's own rows count plus m at the average of all rows,
    # m the file's own (final review, 30 Sep 2026: an empirical-Bayes m, where it was a fixed 10)
    data, counts = _brand_reviews()
    rep = _run(data, "brand_ratings.csv", "", {"__plan__": BRAND_PLAN}, "2026-09-30")
    assert rep["ok"], rep["error"]
    assert not rep["privacy"]["flagged"] and rep["cleaning"]["rows_quarantined"] == 0, (rep["privacy"]["flagged"], rep["cleaning"])
    A = {a["type"]: a for a in rep["ai_analyses"]["items"]}
    assert set(A) == {"rank", "compare"}, rep["ai_analyses"]
    n = {b: sum(c) for b, c in counts.items()}
    tot = {b: sum(s * k for s, k in zip(range(1, 6), c)) for b, c in counts.items()}
    mean = {b: tot[b] / n[b] for b in counts}
    N = sum(n.values())
    grand = sum(tot.values()) / N
    # m, this test's own: the pooled variance of the ratings within a brand over the variance of the brands' true
    # averages (the variance of their averages less the average of each one's sampling variance), on the brands with
    # 5 or more reviews, rounded to whole reviews and held to 5 to 50
    big = [b for b in counts if n[b] >= 5]
    within = math.fsum(k * (star - mean[b]) ** 2 for b in big for star, k in zip(range(1, 6), counts[b])) / \
        math.fsum(n[b] - 1 for b in big)
    mu = math.fsum(mean[b] for b in big) / len(big)
    tau2 = math.fsum((mean[b] - mu) ** 2 for b in big) / (len(big) - 1) - math.fsum(within / n[b] for b in big) / len(big)
    m = min(50, max(5, int(math.floor(within / tau2 + 0.5))))
    assert N == 5581 and tau2 > 0 and m == 13 and (NB.SHRINK_M_MIN, NB.SHRINK_M_MAX) == (5, 50), (N, tau2, m)
    ranked = [b for b in counts if n[b] >= 5]
    w = {b: (n[b] * mean[b] + m * grand) / (n[b] + m) for b in counts}
    want = sorted(ranked, key=lambda b: (-w[b], -n[b], b))[:10]
    # the ranking this replaces: by the plain average, 5.0-star brands with 5 to 8 reviews on top
    plain = sorted(ranked, key=lambda b: (-mean[b], -n[b], b))[:8]
    assert all(mean[b] == 5.0 and 5 <= n[b] <= 8 for b in plain), [(b, mean[b], n[b]) for b in plain]
    rk = A["rank"]
    assert rk["title"] == "Highest average rating by brand in 2022", rk["title"]
    # no brand has a figure 10 years before 2022: the "Change over 10 years" column is left out, not empty on every row
    assert rk["table"]["cols"] == ["brand", "Weighted average", "Average rating", "Rows"], rk["table"]["cols"]
    rows = rk["table"]["rows"]
    assert [r[0] for r in rows] == want, ([r[0] for r in rows], want)
    for r in rows:
        b = r[0]
        assert r[1:4] == [NB._fmt(w[b]), NB._fmt(mean[b]), format(n[b], ",")], (r, w[b], mean[b], n[b])
    # the top of the ranking is no longer a 5.0-star brand on 5 to 8 reviews: the first places go to brands with more
    # reviews (G-STORY, 4.92 on 12; Elgato, 4.91 on 11; KIWI design, 4.42 on 53), the 5.0-star brands on 5 or 6 reviews
    # leave the top 10, and those on 7 or 8 stay below them (what m = 13 does on this file)
    assert not any(mean[b] == 5.0 and n[b] <= 8 for b in want[:2]) and all(n[b] > 8 for b in want[:2]), \
        [(b, mean[b], n[b]) for b in want[:2]]
    assert not any(mean[b] == 5.0 and n[b] <= 6 for b in want), [(b, mean[b], n[b]) for b in want]
    assert want[:2] == ["G-STORY", "Elgato"] and plain[:2] != want[:2], (plain[:2], want[:2])
    assert [x["label"] for x in rk["chart"]["series"]] == want and \
        all(abs(x["value"] - w[x["label"]]) < 1e-9 for x in rk["chart"]["series"]), rk["chart"]["series"][:2]
    assert set(rk["map"]["values"]) == set(ranked) and all(abs(rk["map"]["values"][b] - w[b]) < 1e-9 for b in ranked)
    st = rk["sentence"]
    assert st.startswith("In 2022 the highest yearly average rating by brand, each average weighted by its rows, were "
                         "%s (weighted %s stars: an average of %s stars on %s rows)" % (want[0], NB._fmt(w[want[0]]),
                                                                                       NB._fmt(mean[want[0]]), format(n[want[0]], ","))), st
    assert ("Each brand's average is pulled toward the overall %s stars (the average of all %s rows in 2022) by the "
            "equivalent of %d rows at that average (%d is estimated from how much the brand averages differ against how "
            "much rows differ within a brand), so an average on a few rows counts for less than one on many."
            % (NB._fmt(grand), format(N, ","), m, m)) in st, st
    assert want[2] == "KIWI design", want[:3]
    small = sum(1 for b in counts if n[b] < 5)
    assert "%s brand entries with fewer than 5 rows in 2022 are not ranked." % format(small, ",") in st, st
    assert "IMDb-style weighted rating" in rk["method"] and "an entry resting on fewer than 5 rows that year is not ranked" in rk["method"]
    # compare: the 12 brands with the most rows, ranked by the same weighted average; each one's own average and rows
    cp = A["compare"]
    assert cp["table"]["cols"] == ["brand", "Rows", "Average", "95% range", "Median", "Weighted average"], cp["table"]["cols"]
    got = [r[0] for r in cp["table"]["rows"]]
    cut = sorted(n.values(), reverse=True)[12]
    assert len(got) == 12 and all(n[b] >= cut for b in got), [(b, n[b]) for b in got]
    assert got == sorted(got, key=lambda b: (-w[b], -n[b], b)), [(b, w[b]) for b in got]
    for r in cp["table"]["rows"]:
        assert r[1] == format(n[r[0]], ",") and r[2] == NB._fmt(mean[r[0]]) and r[5] == NB._fmt(w[r[0]]), r
    assert ("Each group's average is pulled toward the overall %s stars (the average of all %s rows) by the equivalent "
            "of %d rows at that average (%d is estimated from how much the brand averages differ against how much rows "
            "differ within a brand)." % (NB._fmt(grand), format(N, ","), m, m)) in cp["sentence"], cp["sentence"]
    assert cp["sentence"].startswith("By brand, with each group's average rating weighted by its rows, %s ranks highest"
                                     % got[0]), cp["sentence"]
    # a ranking of totals is not weighted (the transactions test), nor is a panel's own figure: one row per entry a year
    rows = ["country,year,rate"] + ["C%02d,%d,%.2f" % (i, y, 5.0 - 0.3 * i + 0.01 * (y - 2000)) for i in range(12)
                                    for y in range(2000, 2011)]
    plan = {"goal": "Which country has the highest rate?", "operations": [{"op": "date_from_year", "column": "year"}],
            "columns": [{"name": "country", "role": "entity"}, {"name": "year", "role": "date"},
                        {"name": "rate", "role": "target", "semantic_type": "level"}],
            "analyses": [{"type": "rank", "columns": ["country", "rate"]}]}
    pn = _run(("\n".join(rows) + "\n").encode(), "panel_rate.csv", "", {"__plan__": plan}, "2026-09-15")["ai_analyses"]["items"][0]
    assert pn["table"]["cols"] == ["country", "rate", "Share of all", "Change over 10 years"], pn["table"]["cols"]
    assert [r[0] for r in pn["table"]["rows"]][:3] == ["C00", "C01", "C02"] and pn["table"]["rows"][0][1] == "5.1", pn["table"]["rows"][0]
    assert "weighs" not in pn["sentence"] and "weighted" not in pn["method"], pn["sentence"]


# ---- numbers stored as text, which every CSV has (evaluation preflight, 30 Sep 2026)
def test_eval_numbers_stored_as_text_are_no_issue_and_the_data_health_area_says_the_score_counts_them():
    from northledger import health as H
    cases = [("sample-messy.csv", _sample_bytes(), SAMPLE_AS_OF, ["amount"],
              {"validity": True, "score_min": False, "score_mean": True}),
             ("twelve.csv", _segments_file(12), "2026-03-15", ["revenue", "units"],
              {"validity": True, "score_min": True, "score_mean": True})]
    for name, data, as_of, cols, want in cases:
        rep = _run(data, name, "", None, as_of)
        h = rep["health"]
        # the engine writes the line for every number column of a CSV; the adapter shows and sends none of it
        texts, _ = _engine_direct(data, name, "", as_of)
        eng = sorted(set(t.split(":", 1)[0] for t in texts if NB._is_text_numbers_line(t)))
        assert eng == sorted(cols), (name, eng)
        assert not any("numbers are stored as text" in x for x in h["issues"]), (name, h["issues"])
        rfa = NB.results_for_ai(rep)
        assert not any("stored as text; they will sort" in x for x in rfa["health_issues"]), rfa["health_issues"]
        tn = h["csv_text_numbers"]
        assert tn["columns"] == cols and tn["lowers"] == want, (name, tn)
        # the oracle: the core's own scores with its mark-down undone (health._NUM_AS_TEXT_PENALTY = 0)
        old = H._NUM_AS_TEXT_PENALTY
        H._NUM_AS_TEXT_PENALTY = 0.0
        try:
            und = NB.run(data, name, "", None, as_of)
        finally:
            H._NUM_AS_TEXT_PENALTY = old
        d1 = {d["name"]: d["score"] for d in h["dimensions"]}
        d0 = {d["name"]: d["score"] for d in und["health"]["dimensions"]}
        seen = {"validity": d0["validity"] > d1["validity"], "score_min": und["health"]["score_min"] > h["score_min"],
                "score_mean": und["health"]["score_mean"] > h["score_mean"]}
        assert seen == want, (name, seen, d0, d1)
        assert {k: v for k, v in d0.items() if k != "validity"} == {k: v for k, v in d1.items() if k != "validity"}
        assert und["health"]["csv_text_numbers"]["note"] == "" and not any(und["health"]["csv_text_numbers"]["lowers"].values())
        # the core's score is shown as it is; the Data health area says in plain words that it counts them
        note = tn["note"]
        assert note.startswith("The score counts numbers stored as text, which every CSV has: ") and \
            note.endswith(", although the engine reads them as numbers."), note
        assert ("the weakest dimension here" in note) == want["score_min"], note
        assert ("which lowers the mean of the five" in note) == (not want["score_min"]), note
        assert all(c in note for c in cols), note
        # the writer is told only when the score it is sent (health_score, the weakest dimension) is lowered
        assert (rfa["health_issues"][:1] == [NB.TEXT_NUMBERS_AI]) == want["score_min"], rfa["health_issues"][:2]
        assert rfa["health_score"] == h["score"] and len(NB.TEXT_NUMBERS_AI) <= 200
        v = _proxy_validate(rfa)
        if v is not None:
            assert v["ok"] and v["value"]["health_issues"] == rfa["health_issues"], v.get("detail")
    # a file with no number column: nothing marked down, no note
    rows = ["day,shop,note"] + ["2025-%02d-%02d,S%d,fine" % (1 + i % 12, 1 + i % 28, i % 3) for i in range(120)]
    rep = _run(("\n".join(rows) + "\n").encode(), "words.csv", "", None, "2026-09-15")
    assert rep["health"]["csv_text_numbers"] is None, rep["health"]["csv_text_numbers"]


# ------------------------------------------------------------------ the final site review (30 Sep 2026)
# The reviewer's synthetic files (tools/fixtures/review4: every value made up, emails on example.com, phones in the
# 555 range): a currency split, placeholder zeros, grades on derived items, the search check, new and closed stores,
# the basis, shares of a net change, the empirical-Bayes weight and the byte budget.
R4 = os.path.join(HERE, "fixtures", "review4")
R4_AS_OF = "2026-01-15"
R4_SALES_PLAN = {"goal": "How are sales moving by region?", "kind": "transactions", "primary": "revenue",
                 "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                             {"name": "region", "semantic_type": "category", "role": "segment"},
                             {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "CAD"},
                             {"name": "units", "semantic_type": "count", "role": "driver", "unit": ""}],
                 "operations": [], "analyses": []}


def _r4(name: str) -> bytes:
    with open(os.path.join(R4, name), "rb") as fh:
        return fh.read()


def _r4_frame(name: str):
    import pandas as pd
    return pd.read_csv(os.path.join(R4, name), dtype=str, keep_default_na=False)


def _ana(rep, typ, col):
    """The AI analysis of type `typ` whose title names `col`."""
    got = [a for a in rep["ai_analyses"]["items"] if a["type"] == typ and col in a["title"]]
    assert len(got) == 1, (typ, col, [a["title"] for a in rep["ai_analyses"]["items"]], rep["ai_analyses"]["refused"])
    return got[0]


# f3: EUR, USD and GBP amounts in one column, the plan's unit for it "USD", its primary the amount
R4_EUR_PLAN = {"goal": "How are invoiced amounts moving?", "kind": "transactions", "primary": "amount",
               "columns": [{"name": "invoice_date", "semantic_type": "date", "role": "date"},
                           {"name": "currency", "semantic_type": "code", "role": "metadata"},
                           {"name": "country", "semantic_type": "geography", "role": "segment"},
                           {"name": "amount", "semantic_type": "flow_amount", "role": "target", "unit": "USD"},
                           {"name": "units", "semantic_type": "count", "role": "driver", "unit": ""}],
               "operations": [], "analyses": []}


def test_review4_a_claim_in_one_currency_is_in_that_currency_whatever_unit_the_plan_gives():
    # f3: EUR, USD and GBP amounts in one column, the plan's unit for it "USD". The EUR total read "196,081 USD"
    plan = R4_EUR_PLAN
    df = _r4_frame("f3_eur.csv")
    for prim in ("amount", ""):
        rep = _run(_r4("f3_eur.csv"), "f3_eur.csv", "", {"__plan__": dict(plan, primary=prim)}, R4_AS_OF)
        assert rep["ok"], rep["error"]
        sc = _sc(rep)
        b = sc["basis"]
        assert (b["finding_id"], b["measure"], b["unit"]) == ("measure.amount.total.eur.change", "amount", "EUR"), (prim, b)
        money = [it for it in sc["items"] if it["unit"] not in ("%", "points", "")]
        assert len(money) > 20 and all(it["unit"] == "EUR" and it["text"].endswith(" EUR") for it in money), \
            (prim, [(it["id"], it["text"]) for it in money if it["unit"] != "EUR"][:3])
        out = NB.results_for_ai(rep)
        assert "USD" not in json.dumps(sc["items"]) + json.dumps(out["tables"][-1]), prim
        # the figures are the EUR rows' own, recomputed from the clean download
        _sc_check_recomputed(rep, keep=lambda d: d[d["currency"].str.strip().str.upper() == "EUR"], where="f3 EUR")
        eur = df[(df["currency"] == "EUR") & (df["invoice_date"].str[:7] >= b["windows"]["latest"][0])
                 & (df["invoice_date"].str[:7] <= b["windows"]["latest"][1])]
        _sc_close(_sc_items(rep)["headline.latest"]["value"], math.fsum(float(x) for x in eur["amount"]), "EUR latest")


def test_results_for_ai_sends_the_primary_claim_the_report_leads_with():
    # integration pass, 30 Sep 2026: the PDF's key figures guessed the primary claim from the claims' words; the
    # adapter now sends it, {id, claim, grade}, its claim the same text as its finding's
    def check(rep, want_id, words, what):
        assert rep["ok"], rep["error"]
        out = NB.results_for_ai(rep)
        p = out["primary"]
        f = next(x for x in rep["findings"] if x["id"] == want_id)
        assert p == {"id": want_id, "claim": f["claim"].replace(rep["input"]["name"], NB.FILE_WORD)[:400],
                     "grade": f["grade"]}, (what, p)
        assert p["claim"] in [x["claim"] for x in out["findings"]] and words in p["claim"], (what, p["claim"])
        assert "row volume" not in p["claim"], what
        v = _proxy_validate(out)
        assert v is None or v["ok"], (what, v and v.get("detail"))
        return out
    # FX rates: the plan's primary is a rate (a level): no breakdown, and the primary is the average's own claim
    with open(EVAL_FX, "rb") as fh:
        fx = _run(fh.read(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    out = check(fx, "measure.value.change", "average month of value", "FX")
    assert out["scenarios"]["basis"] is None, out["scenarios"]["basis"]
    # f3 EUR: the plan's primary is the amount: the EUR total the scenarios break down, never the row count the engine's
    # own gate chose as its primary here (since the live baseline of 30 Sep 2026 the report's primary_metric is that
    # total too: _V2.primary_gated, test_eval_b_the_primary_follows_the_plan_on_the_live_fx_layout)
    f3 = _run(_r4("f3_eur.csv"), "f3_eur.csv", "", {"__plan__": R4_EUR_PLAN}, R4_AS_OF)
    assert f3["primary_metric"]["finding_id"] == "measure.amount.total.eur.change", f3["primary_metric"]
    out = check(f3, "measure.amount.total.eur.change", "monthly total of amount in EUR", "f3 EUR")
    assert out["primary"]["id"] == out["scenarios"]["basis"]["finding_id"], out["scenarios"]["basis"]
    # f9c: the total the scenarios break down (CONFIRMED), not the engine's average of the same measure
    f9c = _run(_r4("f9c_confirmed_total.csv"), "f9c_confirmed_total.csv", "", {"__plan__": R4_SALES_PLAN}, R4_AS_OF)
    assert check(f9c, "measure.revenue.total.change", "monthly total of revenue", "f9c")["primary"]["grade"] == "CONFIRMED"
    # no claim to lead with: null; a claim about a withheld column: null (its finding is never sent)
    none = dict(fx, primary_metric=None, scenarios={"basis": None, "items": [], "refused": [], "note": ""})
    assert NB.results_for_ai(none)["primary"] is None
    wh = json.loads(json.dumps(f3))
    wh["privacy"] = dict(wh.get("privacy") or {}, flagged=list((wh.get("privacy") or {}).get("flagged") or [])
                         + [{"column": "amount", "decision": "withhold"}])
    got = NB.results_for_ai(wh)
    assert got["primary"] is None and not any("amount" in x["claim"] for x in got["findings"]), got["primary"]


def _loans_file(entity_rows: bool = True) -> bytes:
    """Eight years of month-end balances for 30 loan accounts (all made up): each is paid down to 0 over 20 to 50
    months and stays at 0 once paid off (runs of paid-off months), some never finish."""
    import random as _random
    rng = _random.Random(47)
    rows = []
    accts = []
    for a in range(30):
        start = rng.uniform(5000, 50000)
        accts.append(("L%03d" % a, start, rng.randint(20, 150), rng.randint(0, 20)))
    for k in range(96):
        y, m = 2018 + k // 12, k % 12 + 1
        for name, start, months, delay in accts:
            t = k - delay
            bal = start if t < 0 else max(0.0, start * (1 - t / months))
            rows.append(["%04d-%02d-28" % (y, m), name, "%.2f" % bal])
    return _csv(rows, ["month_end", "account", "balance"])


def test_review4_placeholder_zeros_need_the_pattern_of_a_missing_value_and_real_zeros_stay():
    # the share of zeros alone read 7 years of a 0% policy rate, paid-off balances and stockouts as "no value"
    import pandas as pd
    import nl_scenarios as NS    # noqa: F401  (the engine directory is on the path)
    plan = {"goal": "How have interest rates and the exchange rate moved?", "kind": "time_series", "primary": "policy_rate",
            "columns": [{"name": "date", "semantic_type": "date", "role": "date"},
                        {"name": "policy_rate", "semantic_type": "level", "role": "target", "unit": "%"},
                        {"name": "mortgage_rate", "semantic_type": "level", "role": "driver", "unit": "%"},
                        {"name": "usd_cad", "semantic_type": "level", "role": "driver", "unit": "CAD per USD"}],
            "operations": [], "analyses": [{"type": "distribution", "columns": ["policy_rate"]},
                                           {"type": "distribution", "columns": ["usd_cad"]}]}
    data = _r4("f6_rates.csv")
    df = _r4_frame("f6_rates.csv")
    pol = df["policy_rate"].astype(float)
    assert int((pol == 0).sum()) == 336 and len(pol) == 1200
    rep = _run(data, "f6_rates.csv", "", {"__plan__": plan}, "2025-01-15")
    assert rep["ok"], rep["error"]
    # the policy rate at 0% from 2009 to 2015: 336 dated zeros in a row, a real period of zero, every value counted
    p = _ana(rep, "distribution", "policy_rate")
    assert p["table"]["rows"][0][0] == "1,200" and p["table"]["rows"][0][3] == NB._fmt(float(pol.median())) == "1.97", p["table"]
    assert "not counted" not in p["sentence"] and "exactly zero" in p["sentence"], p["sentence"]
    # the exchange rate's zeros sit alone between rates that pick up where they left off: the pattern of a missing value
    fx = _ana(rep, "distribution", "usd_cad")
    zf = int((df["usd_cad"].astype(float) == 0).sum())
    assert zf == 149 and fx["table"]["rows"][0][0] == format(1200 - zf, ","), fx["table"]
    assert ("(149 zero values in usd_cad are not counted: they sit alone or two or three together between non-zero "
            "rates that pick up where they left off, the pattern of a missing value)") in fx["sentence"], fx["sentence"]
    notes = [t for t in rep["contracts"]["tests"] if t.get("note")]
    assert [t["column"] for t in notes] == ["usd_cad"] and "placeholder" not in notes[0]["action"], notes
    prof = {c["name"]: c for c in NB.profile_for_ai(data, "f6_rates.csv")["columns"]}
    assert prof["policy_rate"]["zeros"] == 336 and prof["policy_rate"]["zeros_missing_if_level"] is False, prof["policy_rate"]
    assert prof["usd_cad"]["zeros"] == 149 and prof["usd_cad"]["zeros_missing_if_level"] is True, prof["usd_cad"]
    # the reviewer's balances and stock: 8% of accounts paid off, 5% of items out of stock, at random between values
    # that jump about: real zeros, every value counted
    bplan = {"goal": "How are balances and stock levels spread?", "kind": "snapshots", "primary": "account_balance",
             "columns": [{"name": "snapshot_date", "semantic_type": "date", "role": "date"},
                         {"name": "product", "semantic_type": "category", "role": "segment"},
                         {"name": "account_balance", "semantic_type": "level", "role": "target", "unit": "CAD"},
                         {"name": "units_on_hand", "semantic_type": "level", "role": "driver", "unit": "units"}],
             "operations": [], "analyses": [{"type": "distribution", "columns": ["account_balance"]},
                                            {"type": "distribution", "columns": ["units_on_hand"]}]}
    rep = _run(_r4("bal.csv"), "bal.csv", "", {"__plan__": bplan}, R4_AS_OF)
    bdf = _r4_frame("bal.csv")
    assert int((bdf["account_balance"].astype(float) == 0).sum()) > 200 and int((bdf["units_on_hand"].astype(float) == 0).sum()) > 100
    for col in ("account_balance", "units_on_hand"):
        a = _ana(rep, "distribution", col)
        assert a["table"]["rows"][0][0] == "3,000" and "not counted" not in a["sentence"], (col, a["sentence"])
    assert not any(t.get("note") for t in rep["contracts"]["tests"]), [t["column"] for t in rep["contracts"]["tests"] if t.get("note")]
    # a loan book: each account paid down to 0 and held there (runs of 5 or more paid-off months), read within each
    # account (the plan's entity) and across accounts (no entity): real zeros either way
    data = _loans_file()
    bal = pd.read_csv(io.BytesIO(data))["balance"]
    z = int((bal == 0).sum())
    assert 300 < z < len(bal) - 300, z
    for ent_role in ("entity", "metadata"):
        lplan = {"goal": "How are loan balances spread?", "kind": "snapshots", "primary": "balance",
                 "columns": [{"name": "month_end", "semantic_type": "date", "role": "date"},
                             {"name": "account", "semantic_type": "identifier" if ent_role == "metadata" else "entity",
                              "role": ent_role},
                             {"name": "balance", "semantic_type": "level", "role": "target", "unit": "CAD"}],
                 "operations": [], "analyses": [{"type": "distribution", "columns": ["balance"]}]}
        rep = _run(data, "loans.csv", "", {"__plan__": lplan}, R4_AS_OF)
        a = _ana(rep, "distribution", "balance")
        assert a["table"]["rows"][0][0] == format(len(bal), ",") and "not counted" not in a["sentence"], (ent_role, a["sentence"])
    # the rule reads only a level: the FX rates typed a flow keep their zeros (and the level's are the weekends)
    flow = json.loads(json.dumps(EVAL_FX_PLAN))
    flow["columns"][1]["semantic_type"] = "flow_amount"
    with open(EVAL_FX, "rb") as fh:
        fxd = fh.read()
    rep = _run(fxd, "fx_usd_cad.csv", "", {"__plan__": flow}, "2026-09-29")
    d = _ana(rep, "distribution", "VALUE")
    assert d["table"]["rows"][0][0] == "2,957" and "not counted" not in d["sentence"], d["table"]


def test_review4_new_and_closed_segments_are_their_own_rows_and_the_largest_is_chosen_among_all_levels():
    import nl_scenarios as NS
    plan = {"goal": "How are store sales moving?", "kind": "transactions", "primary": "sales",
            "columns": [{"name": "sale_date", "semantic_type": "date", "role": "date"},
                        {"name": "store", "semantic_type": "category", "role": "segment"},
                        {"name": "sales", "semantic_type": "flow_amount", "role": "target", "unit": "CAD"},
                        {"name": "qty", "semantic_type": "count", "role": "driver", "unit": ""}],
            "operations": [], "analyses": []}
    rep = _run(_r4("f2_newstore.csv"), "f2_newstore.csv", "", {"__plan__": plan}, R4_AS_OF)
    assert rep["ok"], rep["error"]
    sc = _sc(rep)
    b = sc["basis"]
    # Uptown opened in the latest 12 months, the Mall closed before them: each its own row, never "other"
    assert b["segment"] == {"column": "store", "levels": ["Airport", "Downtown", "Harbour", "Mall (closed)", "Uptown (new)"],
                            "folded": [], "entered": ["Uptown (new)"], "exited": ["Mall (closed)"]}, b["segment"]
    it = _sc_items(rep)
    assert not any(v["segment"] == "other" for v in it.values()), "a level of one window was folded into other"
    df = _r4_frame("f2_newstore.csv")
    win = lambda w: df[(df["sale_date"].str[:7] >= b["windows"][w][0]) & (df["sale_date"].str[:7] <= b["windows"][w][1])]
    tot = lambda d, s: math.fsum(float(x) for x in d.loc[d["store"] == s, "sales"])
    up, mall = it["contribution.uptown_new.change"], it["contribution.mall_closed.change"]
    _sc_close(up["value"], tot(win("latest"), "Uptown (new)"), "Uptown entered")
    _sc_close(mall["value"], -tot(win("prior"), "Mall (closed)"), "the Mall exited")
    assert it["contribution.uptown_new.prior"]["value"] == 0 and "contribution.uptown_new.pct" not in it
    assert it["contribution.mall_closed.pct"]["text"] == "−100%" and it["contribution.mall_closed.latest"]["value"] == 0
    assert up["label"] == "Uptown (new) (new in the latest 12 months): its contribution to the change in total sales", up["label"]
    assert mall["label"].startswith("Mall (closed) (not in the latest 12 months): "), mall["label"]
    # Harbour and Downtown fell while the total rose: no share of the change, the amounts stay
    assert not any(k.endswith(".share") for k in it) and NS.MIXED_SHARES in sc["refused"], sc["refused"]
    # the largest store in the latest 12 months is the new one; the gap never reaches from or to a closed store
    gaps = [v for v in it.values() if v["group"] == "gap"]
    assert gaps and all("Uptown (new)'s" in v["label"] and "the largest store by total sales in the latest 12 months"
                        in v["label"] for v in gaps), [v["label"] for v in gaps][:2]
    assert it["gap.airport.amount"]["label"] == ("Airport: how far its total sales in the latest 12 months is below Uptown "
                                                 "(new)'s, the largest store by total sales in the latest 12 months")
    assert {v["segment"] for v in gaps} == {"Airport", "Downtown", "Harbour"}, {v["segment"] for v in gaps}
    _sc_close(it["gap.airport.amount"]["value"], tot(win("latest"), "Uptown (new)") - tot(win("latest"), "Airport"), "gap")
    _sc_check_recomputed(rep, where="f2 new and closed stores")
    # no mix without a price per unit before for the new store, and no claim that price, volume and mix add up
    assert "price_volume_mix.mix" not in it and it["price_volume_mix.price"]["assumes"] == NS.PV_ASSUMES == \
        "measured against the 12 months before; the two parts add up exactly to the change", it["price_volume_mix.price"]
    assert any(x.startswith("the mix effect needs units above zero for every store value in both windows (Uptown (new) is "
                            "new in the latest 12 months)") for x in sc["refused"]), sc["refused"]
    assert "The figures are shown rounded; they add up before rounding." in sc["note"], sc["note"]
    # the writer's table: named rows, the share column left out as no row has one
    tab = NB.results_for_ai(rep)["tables"][-1]
    assert tab["title"] == "Where the change in total sales came from", tab["title"]
    assert tab["cols"] == ["store", "12 months before", "Latest 12 months", "Change", "Own change"], tab["cols"]
    assert tab["rows"][0][0] == "Uptown (new) (new in the latest 12 months)" and \
        "Mall (closed) (not in the latest 12 months)" in [r[0] for r in tab["rows"]], tab["rows"]
    # a small level in both windows still folds into other, and the largest may be one of them: Enterprise, 3 orders the
    # year before and 4 large ones in the latest 12 months
    import random as _random
    rng = _random.Random(9)
    rows = []
    for k in range(26):
        y, m = 2024 + k // 12, k % 12 + 1
        for i in range(30):
            rows.append(["%04d-%02d-%02d" % (y, m, rng.randint(1, 28)), rng.choice(["North", "South", "East", "West"]),
                         "%.2f" % rng.uniform(50, 150)])
    for d, v in (("2024-04-10", 900), ("2024-06-10", 900), ("2024-08-10", 900), ("2025-04-10", 20000), ("2025-06-10", 20000),
                 ("2025-08-10", 20000), ("2025-10-10", 20000)):
        rows.append([d, "Enterprise", "%.2f" % v])
    rep = _run(_csv(rows, ["order_date", "region", "revenue"]), "enterprise.csv", "", None, "2026-03-15")
    sc = _sc(rep)
    assert sc["basis"]["segment"]["folded"] == ["Enterprise"] and "Enterprise" not in sc["basis"]["segment"]["levels"], sc["basis"]
    gaps = [v for v in sc["items"] if v["group"] == "gap"]
    assert {v["segment"] for v in gaps} == {"North", "South", "East", "West"} and all(
        "Enterprise's" in v["label"] and "the largest region by total revenue in the latest 12 months" in v["label"]
        for v in gaps), [v["label"] for v in gaps][:1]
    _sc_check_recomputed(rep, where="the largest is a folded level")


def test_review4_the_basis_is_the_plans_primary_and_an_average_is_never_read_as_a_row_count():
    import nl_scenarios as NS

    def plan(prim, tick="count", bal="level"):
        return {"goal": "How is support work moving?", "kind": "transactions", "primary": prim,
                "columns": [{"name": "logged_on", "semantic_type": "date", "role": "date"},
                            {"name": "team", "semantic_type": "category", "role": "segment"},
                            {"name": "tickets", "semantic_type": tick, "role": "driver", "unit": "tickets"},
                            {"name": "hours", "semantic_type": "flow_amount", "role": "driver", "unit": "hours"},
                            {"name": "cost", "semantic_type": "flow_amount", "role": "target", "unit": "CAD"},
                            {"name": "account_balance", "semantic_type": bal, "role": "driver", "unit": "CAD"}],
                "operations": [], "analyses": []}
    data = _r4("f4_counts.csv")
    # the plan's primary is cost: its total is broken down (it was the ticket count, the first total in the engine's order)
    rep = _run(data, "f4_counts.csv", "", {"__plan__": plan("cost")}, R4_AS_OF)
    b = _sc(rep)["basis"]
    assert (b["finding_id"], b["unit"]) == ("measure.cost.total.change", "CAD"), b
    assert rep["primary_metric"]["finding_id"] == "measure.cost.change", rep["primary_metric"]
    _sc_check_recomputed(rep, where="f4 cost")
    # the plan's primary is a balance (a level): nothing adds up, and the block says so rather than count rows
    rep = _run(data, "f4_counts.csv", "", {"__plan__": plan("account_balance")}, R4_AS_OF)
    sc = _sc(rep)
    assert sc["basis"] is None and sc["refused"][0] == NS.AVERAGE_REFUSED == (
        "the headline is an average, so it has no parts that add up; see the headline finding"), sc["refused"]
    assert not any(it["group"] in NB_SC().PER_SEGMENT + ("headline",) for it in sc["items"]), [it["id"] for it in sc["items"]]
    # the FX rates (a level): refused, where the rows were counted and broken down before
    with open(EVAL_FX, "rb") as fh:
        rep = _run(fh.read(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    sc = _sc(rep)
    assert sc["basis"] is None and NS.AVERAGE_REFUSED in sc["refused"], (sc["basis"], sc["refused"])
    # with no plan, the engine's own primary claim, as before (the ticket file's cost total)
    rep = _run(data, "f4_counts.csv", "", None, R4_AS_OF)
    assert _sc(rep)["basis"]["finding_id"] == rep["primary_metric"]["finding_id"] == "measure.cost.total.change"


def test_review4_shares_of_a_net_change_are_left_out_when_segments_moved_in_opposite_directions():
    import nl_scenarios as NS
    plan = {"goal": "How are sales moving by channel?", "kind": "transactions", "primary": "revenue",
            "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                        {"name": "channel", "semantic_type": "category", "role": "segment"},
                        {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "USD"},
                        {"name": "units", "semantic_type": "count", "role": "driver", "unit": "units"}],
            "operations": [], "analyses": []}
    rep = _run(_r4("f1_refunds.csv"), "f1_refunds.csv", "", {"__plan__": plan}, R4_AS_OF)
    sc = _sc(rep)
    it = _sc_items(rep)
    ch = {v["segment"]: v["value"] for v in it.values() if v["group"] == "contribution" and v["id"].endswith(".change")}
    total = it["headline.change"]["value"]
    # refunds took the marketplace from +33,496 to -20,325: its "share" of a -10,672 change read 504%, the web's -182%
    assert total < 0 and any(c > 0 for c in ch.values()) and any(c < 0 for c in ch.values()), (total, ch)
    assert not any(k.endswith(".share") for k in it) and NS.MIXED_SHARES in sc["refused"], sc["refused"]
    assert NS.MIXED_SHARES == ("shares of the change are not given: segments moved in opposite directions, so shares of "
                               "the net change would exceed 100%")
    _sc_close(math.fsum(ch.values()), total, "the amounts still add up")
    _sc_check_recomputed(rep, where="f1 refunds")
    assert "Share of the change" not in NB.results_for_ai(rep)["tables"][-1]["cols"]


def test_review4_results_for_ai_sends_each_table_once_and_never_goes_over_its_budget():
    rep = _run(_territories_file(), "territories.csv", "", {"__plan__": TERRITORY_PLAN}, "2026-03-15")
    old = NB.RESULTS_MAX_BYTES
    NB.RESULTS_MAX_BYTES = 10 ** 9
    try:
        whole = NB.results_for_ai(rep)
    finally:
        NB.RESULTS_MAX_BYTES = old
    # each analysis's table goes once, in tables under the analysis's own title (the worker reads [TABLE:n] and the
    # figures it accepts from tables; analyses[].table was the same table a second time)
    assert not any("table" in a for a in whole["analyses"]), [a["title"] for a in whole["analyses"] if "table" in a]
    with_table = [a for a in rep["ai_analyses"]["items"] if (a.get("table") or {}).get("rows")]
    assert [t["title"] for t in whole["tables"][:len(with_table)]] == [a["title"] for a in with_table], whole["tables"]
    assert len(whole["tables"]) == len(with_table) + 1 and whole["tables"][-1]["title"].startswith("Where the change")
    for a, t in zip(with_table, whole["tables"]):
        assert t["rows"] == [[str(v)[:80] for v in r][:len(t["cols"])] for r in a["table"]["rows"][:12]], t["title"]
    v = _proxy_validate(whole)
    if v is not None:
        assert v["ok"] and [t["title"] for t in v["value"]["tables"]] == [t["title"] for t in whole["tables"]], v.get("detail")
    # a budget the scenario items alone cannot meet: every item goes, then the charts chosen from the data (rep.viz,
    # CONTRACT §5.9) from the last, then the analyses from the last (each with its chart and its table), and
    # analyses_refused says so first
    import nl_viz as NV
    assert any(NV.is_record(c) for c in whole["charts"]), "the file no longer has a viz record, so this tests less"
    budget = _payload_bytes_of(dict(whole, scenarios=dict(whole["scenarios"], items=[]),
                                    charts=[c for c in whole["charts"] if not NV.is_record(c)])) - 9000
    NB.RESULTS_MAX_BYTES = budget
    try:
        got = NB.results_for_ai(rep)
        NB.RESULTS_MAX_BYTES = 800
        tiny = NB.results_for_ai(rep)
    finally:
        NB.RESULTS_MAX_BYTES = old
    assert len(json.dumps(got)) <= budget and got["scenarios"]["items"] == [], len(json.dumps(got))
    assert not any(NV.is_record(c) for c in got["charts"]), "a viz record outlived an analysis"
    titles = [a["title"] for a in whole["analyses"]]
    k = len(got["analyses"])
    assert 0 < k < len(titles) and [a["title"] for a in got["analyses"]] == titles[:k], [a["title"] for a in got["analyses"]]
    assert got["analyses_refused"][0] == NB.BUDGET_ANALYSES % (len(titles) - k, len(titles), format(budget, ",")), got["analyses_refused"]
    assert got["analyses_refused"][1:] == whole["analyses_refused"], got["analyses_refused"]
    gone = set(titles[k:])
    assert not any(t["title"] in gone for t in got["tables"]) and not any(c["title"] in gone for c in got["charts"]), gone
    assert [t["title"] for t in got["tables"]] == [t["title"] for t in whole["tables"][:-1] if t["title"] not in gone]
    # nothing can fit: never an oversize payload, never silently: ok false with the reason
    assert tiny["ok"] is False and "over the report writer's 800-byte budget" in tiny["error"], tiny


# ------------------------------------------------------------------ final review 5 (30 Sep 2026): zeros, m and compare
def _level_plan(date: str, col: str, unit: str, extra=None) -> dict:
    return {"goal": "How has %s moved?" % col, "primary": col, "operations": [],
            "columns": [{"name": date, "semantic_type": "date", "role": "date"}] + list(extra or []) +
                       [{"name": col, "semantic_type": "level", "role": "target", "unit": unit}],
            "analyses": [{"type": "distribution", "columns": [col]}]}


def test_review5_placeholder_zeros_need_three_runs_and_only_the_zeros_the_note_describes_leave():
    import random as _random
    d0 = datetime.date(2025, 1, 1)
    # a price at 19.99 with one, two or three zeros between days that pick up where they left off (over 1% of the
    # days each time): one zero, or two, is never enough; three separate runs are
    for zs, gone in (((44,), 0), ((20, 60), 0), ((20, 44, 70), 3)):
        rows = [[(d0 + datetime.timedelta(k)).isoformat(), "0" if k in zs else "19.99"] for k in range(90)]
        data = _csv(rows, ["day", "price"])
        rep = _run(data, "price.csv", "", {"__plan__": _level_plan("day", "price", "USD")}, "2025-06-30")
        d = _ana(rep, "distribution", "price")
        assert d["table"]["rows"][0][0] == str(90 - gone), (zs, d["table"])
        assert ("not counted" in d["sentence"]) == bool(gone), (zs, d["sentence"])
        prof = {c["name"]: c for c in NB.profile_for_ai(data, "price.csv")["columns"]}
        assert prof["price"]["zeros"] == len(zs) and prof["price"]["zeros_missing_if_level"] is bool(gone), prof["price"]
    # a daily rate: 5 zeros alone between rates that pick up where they left off, one on the last day (the edge of the
    # series) and one on a row with no date: only the 5 are not counted; the other 2 stay 0, and the sentence and the
    # Data tests note count them apart (it read every zero as missing, with words that fit only the 5)
    rng = _random.Random(7)
    rows = []
    for k in range(120):
        v = 0.0 if k in (10, 30, 50, 70, 90, 119) else round(1.30 + rng.uniform(-0.004, 0.004), 4)
        rows.append([(d0 + datetime.timedelta(k)).isoformat(), "%.4f" % v])
    rows.append(["", "0"])
    data = _csv(rows, ["day", "rate"])
    rep = _run(data, "rates.csv", "", {"__plan__": _level_plan("day", "rate", "CAD per USD")}, "2025-06-30")
    assert rep["ok"] and rep["cleaning"]["rows_clean"] == 121, (rep["error"], rep["cleaning"])
    d = _ana(rep, "distribution", "rate")
    words = ("5 zero values in rate are not counted: they sit alone or two or three together between non-zero rates "
             "that pick up where they left off, the pattern of a missing value; 2 other zero values stay 0 (1 at the "
             "start or end of a series, 1 on a row with no date)")
    assert d["table"]["rows"][0][0] == "116" and "(%s)." % words in d["sentence"], (d["table"], d["sentence"])
    note = [t for t in rep["contracts"]["tests"] if t.get("note")]
    assert len(note) == 1 and note[0]["zeros"] == 5 and "(%s)" % words.split(": ", 1)[1] in note[0]["action"], note
    # the frame drops exactly the 5: its zeros left are the edge's and the undated row's
    r = NB._zero_shape([1.3, 0, 1.3, 1.31, 0, 1.3, 1.3, 0, 1.31, 1.3, 0], ["2025-01-%02d" % i for i in range(1, 11)] + [None])
    assert r[0] == 4 and r[1]["zeros"] == 3 - 0 and list(r[1]["mask"].nonzero()[0]) == [1, 4, 7], r
    assert r[1]["kept"] == {"edge": 0, "undated": 1, "other": 0}, r[1]["kept"]


def test_review5_the_profile_reads_a_panels_zeros_within_each_series_as_the_analyses_do():
    # two desks' rates, one row a desk a day, both desks at 0 on the same 3 days: each desk's zeros sit between its own
    # rates, which pick up where they left off. Read across the desks in date order (the profile's old reading) they
    # are no pattern (a run of two zeros between 50 and 1.3); read within each desk (the analyses') they are. The
    # profile and the analyses now read the same series, whatever role the plan gives desk
    import random as _random
    rng = _random.Random(3)
    rows = []
    d0 = datetime.date(2025, 1, 1)
    for k in range(90):
        for s, base in (("North", 1.30), ("South", 50.0)):
            z = k in (10, 40, 70)
            rows.append([(d0 + datetime.timedelta(k)).isoformat(), s,
                         "0" if z else "%.3f" % (base * (1 + rng.uniform(-0.004, 0.004)))])
    data = _csv(rows, ["day", "desk", "rate"])
    vals = [float(r[2]) for r in rows]
    assert NB._zero_shape(vals, [r[0] for r in rows])[1] is None, "read across the desks the zeros show a pattern"
    for role in ("entity", "segment", "metadata"):
        plan = _level_plan("day", "rate", "CAD per USD", [{"name": "desk", "semantic_type": "category", "role": role}])
        rep = _run(data, "desks.csv", "", {"__plan__": plan}, "2025-06-30")
        d = _ana(rep, "distribution", "rate")
        assert d["table"]["rows"][0][0] == "174" and "(6 zero values in rate are not counted:" in d["sentence"], (role, d)
    prof = {c["name"]: c for c in NB.profile_for_ai(data, "desks.csv")["columns"]}
    assert prof["rate"]["zeros"] == 6 and prof["rate"]["zeros_missing_if_level"] is True, prof["rate"]
    # the series is the column that, with the date, tells the rows apart; none when the dates alone do
    g = NB._series_groups([("note", ["x"] * len(rows)), ("desk", [r[1] for r in rows])], [r[0] for r in rows])
    assert list(g[:4]) == ["North", "South", "North", "South"], g[:4]
    assert NB._series_groups([("desk", ["A"] * 5)], ["2025-01-0%d" % i for i in range(1, 6)]) is None


def test_review5_m_needs_five_groups_and_compare_offers_no_range_for_a_gap_chance_can_make():
    import numpy as np
    import pandas as pd
    plan = {"columns": [{"name": "rating", "semantic_type": "rating", "role": "target"},
                        {"name": "store", "semantic_type": "category", "role": "segment"}]}
    # fewer than 5 groups of 5 rows: m is the set value 10, and the sentence says so; 5 groups are estimated
    rng = np.random.default_rng(4)
    assert NB.SHRINK_MIN_GROUPS == 5 and NB._shrink_m([rng.normal(i, 1, 40) for i in range(4)]) == \
        (10, {"kind": "fallback", "why": "few", "raw": None})
    assert NB._shrink_m([rng.normal(i, 1, 40) for i in range(5)])[1]["kind"] != "fallback"
    df = pd.DataFrame({"store": np.repeat(["A", "B", "C", "D"], 50), "rating": rng.normal(3.5, 1.0, 200)})
    out = NB._a_compare(df, None, None, ["rating"], plan, None, by="store")
    assert ("by the equivalent of 10 rows at that average (10, a set value: fewer than five store groups have 5 or "
            "more rows, too few to estimate it from).") in out["sentence"], out["sentence"]
    # 12 stores with no true difference: when the averages differ no more than chance makes them, the gap between the
    # top and the bottom is said to be the two ends picked out of 12, and no range is offered for it (it read "a gap of
    # 0.199 (95% range 0.0111 to 0.396)" beside "no more than chance" in 92 of 300 such files)
    chance = 0
    for seed in range(40):
        rng = np.random.default_rng(seed)
        df = pd.DataFrame({"store": np.repeat(["S%02d" % i for i in range(12)], 200), "rating": rng.normal(3.5, 1.0, 2400)})
        t = NB._a_compare(df, None, None, ["rating"], plan, None, by="store")["sentence"]
        gap = t[t.index(": a gap of"):t.index(". Each group")]
        if "no more than chance" in t:
            chance += 1
            assert gap.endswith("between the top and the bottom of the 12 groups, the two ends picked out of them, "
                                "which chance alone can make this wide") and "range" not in gap, t
        else:
            assert "(95% range " in gap, t
    assert chance >= 5, chance


# ------------------------------------------------------------------ the final evaluation (1 Oct 2026)
# The live page on the evaluation's two files (.work/eval/out/final-2026-10-01/SCORECARD-draft.md): results_for_ai cut
# every chart's series to its first 4 (the FX histogram sent 4 of its 12 bins, 598 of 2,407 rates; the reviews "rating
# by department" dropped Computers, the lowest department, which the text beside it names); the AI plan set aside the
# whole "All Electronics" department (6,694 rows, 19.8%) as "an umbrella department overlapping the specific ones",
# which was false, and nothing said so; the health score read 0.0 with nothing saying why (the newest row was 3.5 years
# old); the plan's reading was cut mid-word ("one dist"). The tests use the StatCan FX fixture and the SYNTHETIC reviews
# file (tools/fixtures/eval), never the research-licensed review rows.
FINAL5_UMBRELLA = ("'All Electronics' reads as an umbrella department overlapping the specific ones, so it was excluded; "
                   "if it held unique reviews they are not covered.")
FINAL5_PLAN = {
    "goal": "What do customers praise and complain about, and how do the departments compare?", "kind": "text_corpus",
    "understanding": "Product reviews, one row per review, with a 1-5 star rating, a department and a brand.",
    "primary": "rating",
    "columns": [{"name": "review_date", "semantic_type": "date", "role": "date"},
                {"name": "rating", "semantic_type": "rating", "role": "target", "unit": "stars"},
                {"name": "verified_purchase", "semantic_type": "boolean", "role": "segment"},
                {"name": "helpful_votes", "semantic_type": "count", "role": "driver"},
                {"name": "department", "semantic_type": "category", "role": "segment"},
                {"name": "brand", "semantic_type": "entity", "role": "entity"},
                {"name": "review_title", "semantic_type": "free_text", "role": "metadata"},
                {"name": "review_text", "semantic_type": "free_text", "role": "driver"}],
    "operations": [{"op": "exclude_rows", "column": "department", "values": ["All Electronics"]},
                   {"op": "set_aside", "columns": ["review_title"]}],
    "analyses": [{"type": "compare", "columns": ["rating"], "by": "department"},
                 {"type": "themes", "columns": ["review_text"]}],
    "quality_risks": [FINAL5_UMBRELLA, "Ratings are skewed high, so averages sit near the ceiling."]}


def _final5_rows():
    with open(EVAL_REVIEWS, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _final5_reviews(plan=None, dec=None):
    d = dict(dec if dec is not None else {"review_text": "keep"}, __plan__=plan if plan is not None else FINAL5_PLAN)
    rep = _run(open(EVAL_REVIEWS, "rb").read(), "reviews_synthetic.csv", "", d, "2026-09-30")
    assert rep["ok"], rep["error"]
    return rep


def test_final5_results_for_ai_sends_every_bar_and_caps_only_the_lines():
    # FX: the distribution's 12 bins, every one of them, adding up to the 2,407 rates (the live payload sent 4)
    rep = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    assert rep["ok"], rep["error"]
    dist = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "distribution")
    bins = dist["chart"]["series"]
    assert len(bins) == 12 and sum(b["value"] for b in bins) == 2407, (len(bins), sum(b["value"] for b in bins))
    out = NB.results_for_ai(rep)
    sent = next(c for c in out["charts"] if c.get("kind") == "bars" and c["title"] == dist["title"])
    assert [(s["label"], s["value"]) for s in sent["series"]] == [(b["label"], float(b["value"])) for b in bins], sent
    line = next(c for c in out["charts"] if c.get("kind") == "line")
    assert len(line["series"]) == 1 and len(line["series"][0]["x"]) == 9, line
    # the synthetic reviews, all six departments (no row dropped): the compare analysis's bars keep every group, the
    # lowest among them, whom its sentence names
    rows = _final5_rows()
    depts = sorted(set(r["department"] for r in rows))
    assert len(depts) == 6, depts
    rep = _final5_reviews(dict(FINAL5_PLAN, operations=[{"op": "set_aside", "columns": ["review_title"]}]))
    cmp_ = next(a for a in rep["ai_analyses"]["items"] if a["type"] == "compare")
    labels = [s["label"] for s in cmp_["chart"]["series"]]
    low = cmp_["table"]["rows"][-1][0]
    assert sorted(labels) == depts and labels[-1] == low and (" and %s lowest (" % low) in cmp_["sentence"], (labels, low)
    sent = next(c for c in NB.results_for_ai(rep)["charts"] if c.get("kind") == "bars" and c["title"] == cmp_["title"])
    assert [s["label"] for s in sent["series"]] == labels, [s["label"] for s in sent["series"]]
    # the caps: a line chart's series are lines (at most 4 drawn); a bar chart's series are its bars (at most
    # LEGACY_BARS_MAX, the analyses' own caps are 12); a scatter's points stay a sample of 120
    many = {"ok": True, "ai_plan": {"goal": "g"}, "findings": [], "ai_analyses": {"items": [
        {"title": "lines", "sentence": "s", "method": "m",
         "chart": {"kind": "line", "series": [{"name": "s%d" % i, "x": [1, 2], "y": [1.0, 2.0]} for i in range(6)]}},
        {"title": "bars", "sentence": "s", "method": "m",
         "chart": {"kind": "bars", "series": [{"label": "b%d" % i, "value": float(i)} for i in range(30)]}},
        {"title": "points", "sentence": "s", "method": "m",
         "chart": {"kind": "scatter", "points": [[float(i), float(i)] for i in range(400)]}}]}}
    ch = {c["title"]: c for c in NB.results_for_ai(many)["charts"]}
    assert NB.LEGACY_LINES_MAX == 4 and NB.LEGACY_BARS_MAX == 24 and NB.LEGACY_POINTS_MAX == 120
    assert len(ch["lines"]["series"]) == 4 and len(ch["bars"]["series"]) == 24 and len(ch["points"]["points"]) == 120, \
        {k: len(v.get("series") or v.get("points")) for k, v in ch.items()}


def _final5_repeats(rows, col, gone_value, skip=()):
    """This test's own count: the rows whose `col` is gone_value that equal another row on every column but `col`
    and `skip` (values trimmed)."""
    cols = [c for c in rows[0] if c != col and c not in skip]
    keys = {tuple(r[c].strip() for c in cols) for r in rows if r[col] != gone_value}
    return sum(1 for r in rows if r[col] == gone_value and tuple(r[c].strip() for c in cols) in keys)


def test_final5_a_plan_step_that_sets_rows_aside_is_disclosed_with_its_reason_and_checked():
    rows = _final5_rows()
    n = len(rows)
    gone = sum(1 for r in rows if r["department"] == "All Electronics")
    pct = 100.0 * gone / n
    assert 10 <= pct < 25, pct
    share = "%.1f%%" % pct
    reps = _final5_repeats(rows, "department", "All Electronics")
    check = "none of these rows duplicates a kept row" if not reps else "%s of these rows duplicate a kept row" % reps
    rep = _final5_reviews()
    ap = rep["ai_plan"]
    # the step's own line says how many and what share
    assert ap["applied"][0] == "dropped %s rows (%s) where department is one of 1 values (%s rows left)" % (
        format(gone, ","), share, format(n - gone, ",")), ap["applied"]
    drops = ap["row_drops"]
    assert [(d["op"], d["column"], d["rows"], d["of"]) for d in drops] == [("exclude_rows", "department", gone, n)], drops
    d = drops[0]
    assert abs(d["pct"] - pct) < 1e-9 and d["reason"] == FINAL5_UMBRELLA and d["check"] == check, d
    # the reason claims an overlap, so the engine checked it: every column but the step's own compared
    assert d["compared"] == ["review_date", "rating", "verified_purchase", "helpful_votes", "brand", "review_title",
                             "review_text"], d["compared"]
    assert d["text"] == ("Set aside %s rows (%s of the file's %s) where department is All Electronics. The plan's "
                         "reason: %s The engine checked: %s (compared on every column but department)."
                         % (format(gone, ","), share, format(n, ","), FINAL5_UMBRELLA, check)), d["text"]
    assert d["notice"] == "The AI plan set aside %s rows (%s): %s The engine checked: %s." % (
        format(gone, ","), share, FINAL5_UMBRELLA, check), d["notice"]
    # from 10% of the rows the step is a plan signal ("other", which the worker accepts): the one re-plan may keep them
    sig = [s for s in rep["plan_signals"] if s["kind"] == "other"]
    assert sig == [{"kind": "other", "column": "department",
                    "detail": "the plan's filter set aside %s of %s rows (%s); keep them unless the goal needs them "
                              "excluded; %s" % (format(gone, ","), format(n, ","), share, check)}], rep["plan_signals"]
    # the report writer's facts: the step's line with its share, the notice first among the limitations, and the
    # whole disclosure for the PDF (results.plan_row_drops)
    out = NB.results_for_ai(rep)
    assert out["plan_applied"][0] == ap["applied"][0], out["plan_applied"]
    # the writer's limitation, at most 240 characters (the worker's cap): the count, the engine's check, then the
    # plan's reason, cut at a word
    lim = NB._cut_words("The AI plan set aside %s rows (%s); the engine checked: %s. Its reason: %s" % (
        format(gone, ","), share, check, FINAL5_UMBRELLA), NB.LIMITATION_MAX)
    assert out["limitations"][0] == lim and len(lim) <= 240, out["limitations"][:1]
    assert out["plan_row_drops"] == [{"rows": gone, "of": n, "pct": d["pct"], "text": d["text"], "notice": d["notice"]}], \
        out["plan_row_drops"]
    v = _proxy_validate(out)
    assert v is None or (v["value"]["limitations"][0] == lim and v["value"]["plan_applied"][0] == ap["applied"][0]), v
    # a step under 10% is disclosed but neither a signal nor a limitation; a reason that claims no overlap is not
    # checked; a step with no reason says so
    small = dict(FINAL5_PLAN, operations=[{"op": "exclude_rows", "column": "department", "values": ["Software"]}],
                 quality_risks=["The Software department is out of scope for this question, so it was excluded."])
    rep2 = _final5_reviews(small)
    sw = sum(1 for r in rows if r["department"] == "Software")
    d2 = rep2["ai_plan"]["row_drops"][0]
    assert (d2["rows"], d2["check"], d2["compared"], d2["notice"]) == (sw, "", [], ""), d2
    assert d2["reason"] == "The Software department is out of scope for this question, so it was excluded.", d2
    assert not [s for s in rep2["plan_signals"] if s["kind"] == "other"] and \
        not any("The AI plan set aside" in x for x in NB.results_for_ai(rep2)["limitations"]), rep2["plan_signals"]
    rep3 = _final5_reviews(dict(FINAL5_PLAN, quality_risks=[]))
    d3 = rep3["ai_plan"]["row_drops"][0]
    assert d3["reason"] == "" and d3["check"] == "" and d3["notice"] == (
        "The AI plan set aside %s rows (%s) and gave no reason." % (format(gone, ","), share)), d3
    assert "The plan gave no reason." in d3["text"], d3["text"]


def test_final5_the_overlap_check_counts_real_repeats_and_skips_an_id_column():
    # an "All" group whose rows repeat other groups' rows (a true umbrella), each row with its own id: the id column
    # is left out of the comparison (named like a key, its values distinct), the group's own column too
    base = [["R%04d" % i, "2025-%02d-%02d" % (1 + i % 12, 1 + i % 28), ["North", "South", "East"][i % 3],
             str(1 + (i * 7) % 5), "item %d" % (i % 37)] for i in range(240)]
    copies = [["R%04d" % (500 + j)] + base[j * 7][1:2] + ["All"] + base[j * 7][3:] for j in range(30)]
    own = [["R%04d" % (700 + j), "2025-06-%02d" % (1 + j), "All", "3", "unique %d" % j] for j in range(20)]
    data = _csv(base + copies + own, ["review_id", "day", "region", "stars", "text"])
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    want = _final5_repeats(rows, "region", "All", skip=("review_id",))
    assert want == 30, want
    plan = {"goal": "How do regions rate?", "kind": "survey",
            "columns": [{"name": "day", "semantic_type": "date", "role": "date"},
                        {"name": "region", "semantic_type": "category", "role": "segment"},
                        {"name": "stars", "semantic_type": "rating", "role": "target"}],
            "operations": [{"op": "exclude_rows", "column": "region", "values": ["All"]}],
            "quality_risks": ["The All region repeats rows of the other regions, so it was excluded."]}
    rep = NB.run(data, "regions.csv", "", {"__plan__": plan}, "2026-01-15")
    assert rep["ok"], rep["error"]
    d = rep["ai_plan"]["row_drops"][0]
    assert (d["rows"], d["of"]) == (50, 290) and d["compared"] == ["day", "stars", "text"], d
    assert d["check"] == "30 of these rows (60.0%) duplicate a kept row", d["check"]
    assert d["text"].endswith("The engine checked: 30 of these rows (60.0%) duplicate a kept row (compared on every "
                              "column but region and review_id)."), d["text"]


def test_final5_the_health_score_says_what_set_it():
    rep = _final5_reviews()
    h = rep["health"]
    dims = {x["name"]: x for x in h["dimensions"] if x["applicable"]}
    assert h["weakest"] == "timeliness" and h["score_min"] == 0.0, (h["weakest"], h["score_min"])
    others = [x["score"] for k, x in dims.items() if k != "timeliness"]
    avg = ("%.1f" % (math.fsum(others) / len(others))).rstrip("0").rstrip(".")
    age = (datetime.date(2026, 9, 30) - datetime.date(2023, 3, 31)).days
    assert age == 1279, age
    assert h["explain"] == ("0 because the newest row is 3.5 years old (the timeliness check); the other checks "
                            "averaged %s." % avg), h["explain"]
    out = NB.results_for_ai(rep)
    assert out["health_explain"] == h["explain"] and out["health_issues"][0] == "The health score is " + h["explain"], \
        out["health_issues"][:2]
    # a fresh file: the weakest dimension says why too, and the writer's issues do not repeat it when it is close
    fx = _run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", "", {"__plan__": EVAL_FX_PLAN}, "2026-09-29")
    hx = fx["health"]
    assert hx["explain"].startswith(("%.1f" % hx["score_min"]).rstrip("0").rstrip(".") + " because ") and \
        ("(the %s check)" % hx["weakest"]) in hx["explain"], hx["explain"]
    # the core's score is unchanged: the minimum and the mean are the engine's own
    assert h["score"] == h["score_min"] and h["score_mean"] == round(math.fsum(x["score"] for x in dims.values()) / len(dims), 1)
    # every dimension has its words, and a score of 100 on every check says so
    for name, why in (("completeness", "of the cells are empty or a placeholder"), ("uniqueness", "repeat another row"),
                      ("validity", "do not read as their column's main type"),
                      ("consistency", "write the same value with different case or spacing")):
        dd = [dict(x, score=(62.5 if x["name"] == name else 100.0), applicable=True) for x in h["dimensions"]]
        got = NB._health_explain(dd, 62.5, name, {"duplicate_rows": 12, "rows": 400, "id_like": False,
                                                  "newest": None, "future": None})
        assert got.startswith("62.5 because ") and why in got and "the other checks averaged 100." in got, (name, got)
    assert NB._health_explain([dict(x, score=100.0, applicable=True) for x in h["dimensions"]], 100.0, "completeness",
                              {}) == "100: every check the engine ran scored 100."


def test_final5_the_plans_reading_is_cut_at_a_word_never_mid_word():
    words = ("The Bank of Canada daily U.S. dollar rate, one row per calendar day, with blank values on holidays and "
             "weekend zeros before April 2022. ") * 6
    long_ = (words + "every other column is a fixed code with one distinct value.").strip()
    assert len(long_) > 700
    rep = _final5_reviews(dict(FINAL5_PLAN, understanding=long_, goal=("How do departments compare? " * 25).strip()))
    u, g = rep["ai_plan"]["understanding"], rep["ai_plan"]["goal"]
    for got, whole, n in ((u, long_, 600), (g, ("How do departments compare? " * 25).strip(), 600)):
        assert len(got) <= n and got.endswith("…"), (len(got), got[-30:])
        head = got[:-1]
        assert whole.startswith(head) and whole[len(head)] in " ,.;", (head[-20:], whole[len(head):][:10])
    # the reading the report writer receives, with the kept columns' sentence after it, is cut at a word too
    saved = {"ok": True, "findings": [], "ai_plan": {"goal": "g", "understanding": long_[:800]},
             "privacy": {"flagged": [{"column": "review_text", "kind": "free text", "decision": "keep"}]}}
    r = NB.results_for_ai(saved)["reading"]
    told = NB.OPTED_IN % "review_text"
    assert r.endswith(" " + told) and len(r) <= 800, (len(r), r[-120:])
    head = r[:-len(told) - 1]
    assert head.endswith("…") and long_.startswith(head[:-1]) and long_[len(head) - 1] in " ,.;", head[-40:]


# ------------------------------------------------------------------ the integration pass (1 Oct 2026)
# The plan-drop signal (kind "other") asked the AI for a new plan on the FX file only because its plan set aside the
# 569 rows whose VALUE is blank: rows with no usable value of the measure cannot be analysed whatever the plan says, so
# the re-plan call bought nothing and cost the visitor its latency. Every step that sets rows aside is still disclosed
# (the plan card, the PDF, the writer); only the rows that hold a usable value of the plan's primary measure count
# toward the signal (row_drops[].valued), against the same threshold (PLAN_DROP_NOTICE_PCT).
FX_LIVE_PLANS = os.path.join(HERE, "fixtures", "eval", "fx_live_plans_2026-10-01.json")
# StatCan table 33-10-0036-01's U.S. dollar series as published: the columns around REF_DATE, VALUE and STATUS hold one
# value throughout (checked against .work/eval/data/quant_fx_usd.csv, the file the live run read)
FX_PUBLISHED = (("GEO", "Canada"), ("DGUID", "2021A000011124"), ("Type of currency", "U.S. dollar, daily average"),
                ("UOM", "Dollars"), ("UOM_ID", "81"), ("SCALAR_FACTOR", "units"), ("SCALAR_ID", "0"),
                ("VECTOR", "v111666248"), ("COORDINATE", "1.25"))
# the live reviews run's file and plan: research-licensed rows that never enter the repository, read in place when
# this machine has them (.work is git-ignored); the test says so and checks nothing when they are absent
LIVE_REVIEWS = os.path.join(SITE, ".work", "eval", "data", "qual_amazon_vg_reviews.csv")
LIVE_REVIEWS_PLAN = os.path.join(SITE, ".work", "eval", "out", "final-2026-10-01", "qual-reviews", "requests",
                                 "01_plan-response.json")


def _fx_published() -> bytes:
    """The FX fixture rebuilt to the published file's 15 columns and quoting (every field quoted), row for row: the
    file the live FX run read."""
    with open(EVAL_FX, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_ALL)
    head = ["REF_DATE"] + [k for k, _v in FX_PUBLISHED] + ["VALUE", "STATUS", "SYMBOL", "TERMINATED", "DECIMALS"]
    w.writerow(head)
    for r in rows:
        w.writerow([r["REF_DATE"]] + [v for _k, v in FX_PUBLISHED] + [r["VALUE"], r["STATUS"], "", "", "4"])
    return buf.getvalue().encode("utf-8")


def test_integ_fx_live_plan_replay_discloses_the_blank_rows_and_asks_for_no_new_plan():
    data = _fx_published()
    live = os.path.join(SITE, ".work", "eval", "data", "quant_fx_usd.csv")
    if os.path.exists(live):
        with open(live, "rb") as fh:
            assert fh.read() == data, "_fx_published no longer rebuilds the live run's file"
    with open(FX_LIVE_PLANS, encoding="utf-8") as fh:
        plans = json.load(fh)
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    blank = sum(1 for r in rows if not r["VALUE"].strip())
    assert (len(rows), blank) == (3526, 569), (len(rows), blank)
    share = "%.1f%%" % (100.0 * blank / len(rows))
    for key in ("plan_1", "plan_2"):
        rep = _run(data, "quant_fx_usd.csv", "", {"__plan__": plans[key]}, "2026-09-29")
        assert rep["ok"], rep["error"]
        ap = rep["ai_plan"]
        # disclosed as before: the step's line with its share, its item with the count, the share and the plan's
        # reason, the summary's notice, the writer's limitation and the PDF's list
        assert "dropped 569 rows (%s) where VALUE is blank (2,957 rows left)" % share in ap["applied"], ap["applied"]
        d, = ap["row_drops"]
        assert (d["op"], d["column"], d["rows"], d["of"], d["valued"]) == ("exclude_blank", "VALUE", 569, 3526, 0), d
        assert d["text"].startswith("Set aside 569 rows (%s of the file's 3,526) where VALUE is blank. " % share), d["text"]
        assert d["notice"].startswith("The AI plan set aside 569 rows (%s)" % share), d["notice"]
        out = NB.results_for_ai(rep)
        assert out["plan_row_drops"] == [{"rows": 569, "of": 3526, "pct": d["pct"], "text": d["text"],
                                          "notice": d["notice"]}], out["plan_row_drops"]
        assert out["limitations"][0].startswith("The AI plan set aside 569 rows (%s)" % share), out["limitations"][:1]
        # but no row of them holds a rate, so the step is no signal: the plan the report ran asks for nothing, and the
        # first plan only for its refused analysis (the live run's own re-plan reason)
        assert not [s for s in rep["plan_signals"] if s["kind"] == "other"], rep["plan_signals"]
    assert rep["plan_signals"] == [], rep["plan_signals"]
    first = _run(data, "quant_fx_usd.csv", "", {"__plan__": plans["plan_1"]}, "2026-09-29")
    assert [s["kind"] for s in first["plan_signals"]] == ["analysis_refused"], first["plan_signals"]


def test_integ_reviews_live_plan_replay_still_signals_the_all_electronics_drop():
    if not (os.path.exists(LIVE_REVIEWS) and os.path.exists(LIVE_REVIEWS_PLAN)):
        print("    (no check: the live reviews file is not on this machine; it is research-licensed and never in "
              "the repository)")
        return
    with open(LIVE_REVIEWS_PLAN, encoding="utf-8") as fh:
        plan = json.load(fh)["plan"]
    with open(LIVE_REVIEWS, "rb") as fh:
        data = fh.read()
    rep = NB.run(data, "qual_amazon_vg_reviews.csv", "", {"review_text": "keep", "__plan__": plan}, "2026-09-30")
    assert rep["ok"], rep["error"]
    drops = rep["ai_plan"]["row_drops"]
    d = drops[0]
    assert (d["op"], d["column"], d["rows"], d["of"], d["valued"]) == ("exclude_rows", "department", 6694, 33878, 6694), d
    assert d["text"].startswith("Set aside 6,694 rows (19.8% of the file's 33,878) where department is All "
                                "Electronics. "), d["text"]
    # every one of those reviews holds a star rating, the plan's measure: the step is still the one signal
    sig = [s for s in rep["plan_signals"] if s["kind"] == "other"]
    assert sig == [{"kind": "other", "column": "department",
                    "detail": "the plan's filter set aside 6,694 of 33,878 rows (19.8%); keep them unless the goal "
                              "needs them excluded; none of these rows duplicates a kept row"}], rep["plan_signals"]
    # the plan's small step (the blank brands, 0.23%) is disclosed and never read for a signal
    assert [(x["op"], x["column"], x["valued"]) for x in drops[1:]] == [("exclude_blank", "brand", None)], drops[1:]


def _integ_sales(online_filled: int, blank_elsewhere: int = 0, money: bool = True):
    """400 days of revenue by region: 300 rows across four regions, then 100 "Online" rows of which the first
    `online_filled` hold a revenue; `blank_elsewhere` of the regional rows have a blank revenue. Revenue is written
    with a thousands comma ("1,234.50") when `money`, which the plain number reader cannot read and the engine's can."""
    rows = []
    day = datetime.date(2025, 1, 1)
    for i in range(400):
        online = i % 4 == 3
        region = "Online" if online else ["North", "South", "East"][i % 4]
        v = 1000 + (i * 37) % 900 + 0.5
        filled = (sum(1 for j in range(i) if j % 4 == 3) < online_filled) if online else \
            (sum(1 for j in range(i) if j % 4 != 3) >= blank_elsewhere)
        cell = (format(v, ",.2f") if money else "%.2f" % v) if filled else ""
        rows.append([(day + datetime.timedelta(days=i)).isoformat(), region, cell])
    return _csv(rows, ["day", "region", "revenue"])


INTEG_PLAN = {"goal": "How has revenue moved by region?", "kind": "time_series", "primary": "revenue",
              "understanding": "Daily revenue by region.",
              "columns": [{"name": "day", "semantic_type": "date", "role": "date"},
                          {"name": "region", "semantic_type": "category", "role": "segment"},
                          {"name": "revenue", "semantic_type": "amount", "role": "target", "unit": "EUR"}],
              "operations": [{"op": "exclude_rows", "column": "region", "values": ["Online"]}],
              "quality_risks": ["The Online region is a separate channel, so it was excluded."]}


def test_integ_a_filter_counts_toward_the_signal_only_the_rows_that_hold_a_value():
    def run(data, plan):
        rep = NB.run(data, "sales.csv", "", {"__plan__": plan}, "2026-03-01")
        assert rep["ok"], rep["error"]
        d, = rep["ai_plan"]["row_drops"]
        return rep, d, [s for s in rep["plan_signals"] if s["kind"] == "other"]
    # the filter sets aside 100 of 400 rows (25%), 50 of them with a revenue (12.5% of the file): a signal, which
    # says how many of the rows hold a value
    rep, d, sig = run(_integ_sales(50), INTEG_PLAN)
    assert (d["rows"], d["of"], d["valued"]) == (100, 400, 50), d
    assert d["notice"] == "The AI plan set aside 100 rows (25.0%): The Online region is a separate channel, so it was " \
                          "excluded.", d["notice"]
    assert sig == [{"kind": "other", "column": "region",
                    "detail": "the plan's filter set aside 100 of 400 rows (25.0%), 50 of them (12.5% of the rows) "
                              "with a value in revenue; keep them unless the goal needs them excluded"}], sig
    # the same step when only 20 of them hold a revenue (5% of the file): disclosed alike, never a signal
    rep, d, sig = run(_integ_sales(20), INTEG_PLAN)
    assert (d["rows"], d["valued"]) == (100, 20) and d["notice"].startswith("The AI plan set aside 100 rows (25.0%)"), d
    assert not sig and NB.results_for_ai(rep)["limitations"][0].startswith("The AI plan set aside 100 rows (25.0%)"), \
        rep["plan_signals"]
    # every Online row holds a revenue, written plainly: all 100 count
    rep, d, sig = run(_integ_sales(100, money=False), INTEG_PLAN)
    assert d["valued"] == 100 and sig and sig[0]["detail"].startswith(
        "the plan's filter set aside 100 of 400 rows (25.0%); keep them"), sig
    # a plan that names no measure counts rows, so every row it sets aside counts
    rep, d, sig = run(_integ_sales(20), dict(INTEG_PLAN, primary=""))
    assert d["valued"] == 100 and len(sig) == 1, (d, sig)
    # the rows whose revenue is blank (a fifth of the file): disclosed, never a signal
    blank_plan = dict(INTEG_PLAN, operations=[{"op": "exclude_blank", "column": "revenue"}],
                      quality_risks=["revenue is blank on some days, so those rows were excluded."])
    rep, d, sig = run(_integ_sales(100, blank_elsewhere=80), blank_plan)
    assert (d["op"], d["rows"], d["valued"]) == ("exclude_blank", 80, 0) and d["notice"] and not sig, (d, sig)
    # zeros the analyses read as no value (the FX file's 550 weekend zeros, typed a level) are no usable value either
    zero_plan = dict(EVAL_FX_PLAN, operations=[{"op": "exclude_rows", "column": "VALUE", "values": ["0.0000"]}])
    with open(EVAL_FX, "rb") as fh:
        rep = NB.run(fh.read(), "fx_usd_cad.csv", "", {"__plan__": zero_plan}, "2026-09-29")
    assert rep["ok"], rep["error"]
    d, = rep["ai_plan"]["row_drops"]
    assert (d["rows"], d["valued"]) == (550, 0) and not [s for s in rep["plan_signals"] if s["kind"] == "other"], \
        (d, rep["plan_signals"])
    # a real 0 (a level the plan does not type, here an amount) is a value
    rep = NB.run(open(EVAL_FX, "rb").read(), "fx_usd_cad.csv", "",
                 {"__plan__": dict(zero_plan, columns=[dict(c, semantic_type="amount") if c["name"] == "VALUE" else c
                                                        for c in zero_plan["columns"]])}, "2026-09-29")
    d, = rep["ai_plan"]["row_drops"]
    assert d["valued"] == 550 and [s["kind"] for s in rep["plan_signals"] if s["kind"] == "other"] == ["other"], d


def _payload_bytes_of(x) -> int:
    return len(json.dumps(x, default=str))

TESTS =[v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    print("adapter under test: %s" % ("engine/ (the site)" if not os.environ.get("NL_BROWSER_DIR")
                                      else "a copy (NL_BROWSER_DIR)"))
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print("  PASS  %s" % fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  %s: %s: %s" % (fn.__name__, type(exc).__name__, str(exc)[:400]))
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    if failed:
        sys.exit(1)
    print("ALL TESTS PASSED")
