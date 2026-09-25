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
    """The engine's own path run directly (not through the adapter), and everything it wrote."""
    tmp = tempfile.mkdtemp(prefix="nl_direct_")
    try:
        src = os.path.join(tmp, name)
        with open(src, "wb") as fh:
            fh.write(data)
        eng = E.open_engagement(os.path.join(tmp, "e"), create=True)
        E.land(eng, src)
        for tc in E.intake_state(eng.db_path).pending:
            E.decide(eng, tc.split(".", 1)[1], "withhold")
        table = eng.meta()["landings"][-1]["table"]
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
    # 1. no text field carries a number the engine did not write
    for t in _texts(rep):
        extra = _numbers(t) - universe
        assert not extra, "numbers %s in %r are not in anything the engine wrote" % (sorted(extra), t[:160])
    # 2. the story's own sections: every number is a fact's value, the way the engine prints
    #    it, or part of a fact's label (a month, "12 months"); "80" names the 80% range
    s = rep["story"]
    strict = [s["headline"]] + s["what_happened"] + s["why"] + s["whats_next"]
    for t in strict:
        extra = _numbers(t) - fact_forms - claim_nums - {_canon("80")}
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
    before = set(os.listdir(tempfile.gettempdir()))
    NB.run(_email_file(), "orders_with_email.csv", "", None, "2026-01-10")
    NB.run(b"", "e.csv", "")
    after = set(os.listdir(tempfile.gettempdir()))
    left = [n for n in after - before if n.startswith("nl_browser_")]
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
        assert re.fullmatch(r"#\d+b?", c["rule"]), c["rule"]
        assert c["view"] in ("manager", "analyst") and isinstance(c["default_visible"], bool), c["id"]
        assert c["why_shown"].strip() and isinstance(c["data"], dict) and c["data"], c["id"]
        assert set(c["finding_ids"]) <= ids | _ledger_ids(rep), (c["id"], c["finding_ids"])
        manager += int(c["view"] == "manager" and c["default_visible"])
    assert manager <= 6, "manager view shows %d charts by default" % manager
    assert "findings_table" in seen and "benchmark" in seen, sorted(seen)
    # §5 "Suppression": every rule is either drawn or says in one line why it is absent
    accounted = {c["rule"] for c in rep["charts"]} | {x["rule"] for x in rep["charts_suppressed"]}
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
    assert "#2b" in rules, rep["charts_suppressed"]
    _, rep = _v2("no_date.csv")
    ids = {c["id"] for c in rep["charts"]}
    assert not any(i.startswith(("trend.", "season.", "fan.", "replay.", "catmonth.")) for i in ids), ids
    rules = {s["rule"] for s in rep["charts_suppressed"]}
    assert {"#2", "#3", "#4", "#5"} <= rules, rep["charts_suppressed"]


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
        data_charts = {i for i in drawn if not i.startswith(("kpi", "findings_table", "benchmark", "models."))}
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
        drawn = [c for c in rep["charts"] if c["view"] == "manager" and c["id"] not in ("kpi", "findings_table")]
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


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

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
