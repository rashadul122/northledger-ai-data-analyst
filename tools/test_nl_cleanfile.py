#!/usr/bin/env python3
"""Tests for "Clean my file" (engine/nl_cleanfile.py through nl_browser.cleanfile_json, CONTRACT §5.14).

    ../agent-demo/venv/bin/python tools/test_nl_cleanfile.py

The cleaned copy is a VIEW of the engine's own cleaning (one cleaner), so these tests hold it to:
  * the report: the log's rule table and set-aside table are the report's `cleaning` block, number for number
  * pandas, computed here independently from the sample file: rows in/out, duplicates merged, dates parsed,
    the flagged rows and why, and nothing filled in
  * its own promises: a flagged value is the file's value unchanged, every count in the log equals the actual
    difference between the file and the cleaned copy, and cleaning the cleaned copy again changes nothing
  * privacy: a withheld column is never in the copy and never named in the log
  * edits: the same cleaner again, refused edits said, a private column never takes a rule
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, os.path.join(SITE, "engine"))
import pandas as pd  # noqa: E402
import nl_browser as NB  # noqa: E402
from northledger import vocab as V  # noqa: E402

SAMPLE = os.path.join(SITE, "engine", "sample-messy.csv")
AS_OF = "2026-09-15"
_C: dict = {}


def _sample() -> bytes:
    with open(SAMPLE, "rb") as fh:
        return fh.read()


def _clean(data: bytes, name: str, decisions=None, rules=None):
    key = (data, name, json.dumps(decisions, sort_keys=True), json.dumps(rules, sort_keys=True))
    if key not in _C:
        rep = NB.run(data, name, "", decisions, AS_OF)
        out = json.loads(NB.cleanfile_json(json.dumps({"sha256": rep["input"]["sha256"], "rules": rules})))
        _C[key] = (rep, out)
    return _C[key]


def _rows(text: str):
    r = list(csv.reader(io.StringIO(text)))
    return r[0], r[1:]


def _raw_frame() -> pd.DataFrame:
    return pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)


def _log_table(log: str, heading: str):
    sec = log.split("## " + heading, 1)[1].split("\n## ", 1)[0]
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in sec.splitlines()
            if ln.startswith("|") and not ln.startswith("|---")][1:]


def _int(s: str) -> int:
    return int(re.sub(r"[^0-9]", "", s))


# ------------------------------------------------------------------ against pandas
def test_sample_against_pandas():
    rep, out = _clean(_sample(), "sample-messy.csv")
    assert out["ok"], out["error"]
    df = _raw_frame()
    head, rows = _rows(out["csv"])
    # the withheld Notes column is left out; the order of the rest is the file's; the two new columns last
    assert head == [c for c in df.columns if c != "Notes"] + ["nl_flag", "nl_missing"], head
    # duplicates: the file's own exact duplicate rows (pandas), merged into the first copy
    dups = int(df.duplicated().sum())
    assert dups == 55 and out["summary"]["merged"] == dups, (dups, out["summary"]["merged"])
    assert len(rows) == len(df) - dups == out["summary"]["rows_out"]
    # flagged: amounts written TBD (kept as written), negative amounts, dates after the analysis date, and the
    # one impossible date (30 Feb), each kept and flagged
    amt = df["Amount"].str.strip()
    num = pd.to_numeric(amt.str.replace(r"[$,]", "", regex=True), errors="coerce")
    tbd = int((amt == "TBD").sum())
    neg = int((num < 0).sum())
    d = df["Date"].str.strip()
    iso = pd.to_datetime(d, format="%Y-%m-%d", errors="coerce")
    later = int((iso > pd.Timestamp(AS_OF)).sum())
    impossible = int(d.isin(["2025-02-30"]).sum())
    assert (tbd, neg, later, impossible) == (17, 6, 3, 1), (tbd, neg, later, impossible)
    flagged = [r for r in rows if r[-2]]
    assert len(flagged) == tbd + neg + later + impossible == out["summary"]["flagged_rows"], len(flagged)
    assert sum(1 for r in flagged if "'TBD' could not be read" in r[-2] and r[4] == "TBD") == tbd
    assert sum(1 for r in flagged if "outside the possible range" in r[-2] and "sets this row aside" in r[-2]) == neg
    assert sum(1 for r in flagged if "in the future" in r[-2]) == later
    assert sum(1 for r in flagged if "2025-02-30" in r[-2] and r[0] == "2025-02-30") == impossible
    # dates: every unflagged row's date is ISO and is the file's date read day-first (the column's proved order)
    raw_by_line = {i + 2: df.iloc[i] for i in range(len(df))}
    good = [r for r in rows if not r[-2]]
    assert all(re.match(r"^\d{4}-\d{2}-\d{2}$", r[0]) for r in good)
    want = set()
    for v in d:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%b %d, %Y"):
            t = pd.to_datetime(v, format=fmt, errors="coerce")
            if not pd.isna(t):
                want.add(t.strftime("%Y-%m-%d"))
                break
    assert set(r[0] for r in good) <= want
    assert len(raw_by_line) == len(df)
    # numbers plain: no currency sign or thousands separator in an unflagged amount
    assert all(re.match(r"^-?\d+(\.\d+)?$", r[4]) or r[4] == "" for r in good)


def test_nothing_filled_and_flagged_unchanged():
    rep, out = _clean(_sample(), "sample-messy.csv")
    df = _raw_frame()
    head, rows = _rows(out["csv"])
    lines = NB._record_lines(_sample())
    # map each kept row to its file row: the cleaned copy is in file order with the merged rows left out
    merged = set(a for a, _b in out["summary"]["merged_lines"])
    file_rows = [i for i in range(len(df)) if lines[i] not in merged]
    assert len(file_rows) == len(rows)
    cols = head[:-2]
    for r, i in zip(rows, file_rows):
        src = df.iloc[i]
        for j, c in enumerate(cols):
            v, rv = r[j], src[c]
            if V.is_null_like(rv):
                assert v == "", ("filled", c, rv, v)          # a missing value stays missing
            else:
                assert v != "", ("emptied", c, rv)            # a value is never dropped
        miss = [c for j, c in enumerate(cols) if r[j] == ""]
        assert r[-1] == "; ".join(miss), (r[-1], miss)
        # a doubtful cell (named in nl_flag) is byte for byte the file's
        for fl in [x for x in r[-2].split(" | ") if x]:
            col = fl.split(":", 1)[0]
            if col in cols and "kept as written" in fl:
                assert r[cols.index(col)] == src[col], (col, r[cols.index(col)], src[col])


def test_flagged_row_other_cells_standardised():
    """In a row the analysis sets aside, every cell but the doubtful one is cleaned like any row; the doubtful cell is
    the file's text byte for byte."""
    rep, out = _clean(_sample(), "sample-messy.csv")
    df = _raw_frame()
    head, rows = _rows(out["csv"])
    lines = NB._record_lines(_sample())
    merged = set(a for a, _b in out["summary"]["merged_lines"])
    src_of = [i for i in range(len(df)) if lines[i] not in merged]
    aside = [(r, df.iloc[i]) for r, i in zip(rows, src_of) if "sets this row aside" in r[-2]]
    assert len(aside) == 10, len(aside)
    cat_vocab = set(r[head.index("Category")] for r in rows if not r[-2])
    for r, src in aside:
        bad = r[-2].split(":", 1)[0]
        assert r[head.index(bad)] == src[bad]                                    # byte-identical
        for c in ("Date", "Amount"):
            if c != bad:
                v = r[head.index(c)]
                assert re.match(r"^\d{4}-\d{2}-\d{2}$" if c == "Date" else r"^-?\d+(\.\d+)?$", v), (c, v)
        assert r[head.index("Category")] in cat_vocab, r                       # the same spelling as every row
        assert r[head.index("Paid?")] in ("Y", "N")
        assert r[head.index("Property")] == r[head.index("Property")].strip()
    # one of them was written ' Leslieville Fourplex ' / '17/10/2025' in the file and is cleaned now
    assert any(src["Date"].strip() != r[0] for r, src in aside)


def test_replay_matches_the_cleaner():
    """The reading rules replayed for set-aside rows give, on every row the cleaner kept, exactly its values."""
    from nl_cleanfile import _read_work, _cell
    _clean(_sample(), "sample-messy.csv")
    rep = NB.run(_sample(), "sample-messy.csv", "", None, AS_OF)
    L = NB._LAST_CLEAN
    work, clean = _read_work(L["raw"], L["rules"]), L["cr"].clean
    for c in clean.columns:
        a = [_cell(v) for v in work.loc[clean.index, c]]
        b = [_cell(v) for v in clean[c]]
        assert a == b, (c, next((x, y) for x, y in zip(a, b) if x != y))
    assert rep["ok"]


def test_log_counts_equal_report_and_diffs():
    rep, out = _clean(_sample(), "sample-messy.csv")
    log = out["log"]
    # the rule table IS the report's cleaning record
    fixes = _log_table(log, "Rules applied")
    want = [(f["rule"], f["count"]) for f in rep["cleaning"]["fixes"]]
    got = [(t[0].strip("`"), _int(t[2])) for t in fixes[:len(want)]]
    assert got == want, (got, want)
    aside = [t for t in fixes[len(want):] if len(t) == 3 and t[0] != "Reason"]
    assert [(t[0], _int(t[1])) for t in aside] == [(q["reason"], q["count"]) for q in rep["cleaning"]["quarantine_reasons"]]
    head, rows = _rows(out["csv"])
    # before / after
    ba = _log_table(log, "Before and after")
    assert [_int(x) for x in ba[0][1:]] == [rep["cleaning"]["rows_in"], len(rows)]
    assert [_int(x) for x in ba[1][1:]] == [rep["input"]["columns"], len(head)]
    # duplicates merged = rows in - rows out = the engine's exact-duplicate count
    m = re.search(r"Rows merged: \*\*([\d,]+)\*\*", log)
    dup = next(q["count"] for q in rep["cleaning"]["quarantine_reasons"] if q["reason"].startswith("exact_duplicates"))
    assert _int(m.group(1)) == rep["cleaning"]["rows_in"] - len(rows) == dup
    # flagged rows and missing cells, counted in the CSV
    m = re.search(r"\*\*([\d,]+)\*\* rows? carry a flag", log)
    assert _int(m.group(1)) == sum(1 for r in rows if r[-2])
    miss = {t[0].strip("`"): _int(t[1]) for t in _log_table(log, "Missing values")}
    for j, h in enumerate(head[:-2]):
        assert miss[h] == sum(1 for r in rows if r[j] == ""), h
    fl = {t[0]: t[1] for t in _log_table(log, "Flagged rows")}
    kept = next(v for k, v in fl.items() if k.startswith("rows the analysis sets aside"))
    assert _int(kept) == sum(1 for r in rows if "sets this row aside" in r[-2])
    for k, v in fl.items():
        m = re.match(r"`(.+)`: (the value a set-aside|could not be read)", k)
        if m:
            what = "sets this row aside" if m.group(2).startswith("the value") else "could not be read"
            assert _int(v) == sum(1 for r in rows for x in r[-2].split(" | ") if x.startswith(m.group(1) + ":") and what in x), k
    assert "The original file was not changed" in log


def test_idempotent():
    rep, out = _clean(_sample(), "sample-messy.csv")
    head, rows = _rows(out["csv"])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head[:-2])
    w.writerows(r[:-2] for r in rows)
    rep2, out2 = _clean(buf.getvalue().encode("utf-8"), "sample-messy-cleaned.csv")
    assert out2["ok"], out2["error"]
    head2, rows2 = _rows(out2["csv"])
    assert head2 == head and out2["summary"]["merged"] == 0
    assert [r[:-1] for r in rows2] == [r[:-1] for r in rows], next(
        (a, b) for a, b in zip(rows2, rows) if a[:-1] != b[:-1])
    assert [r[-1] for r in rows2] == [r[-1] for r in rows]


def test_privacy_withheld_never_named_or_copied():
    rep, out = _clean(_sample(), "sample-messy.csv")
    df = _raw_frame()
    notes = set(v for v in df["Notes"] if len(v) >= 6)
    assert notes
    assert "Notes" not in out["csv"].splitlines()[0] and not any(n in out["csv"] for n in notes)
    assert not re.search(r"\bnotes\b", out["log"], re.I), "the log names the withheld column"
    assert "a withheld column" in out["log"]
    # coded: the column's codes, never its values, and no rule
    rep2, out2 = _clean(_sample(), "sample-messy.csv", {"notes": "code"})
    head, rows = _rows(out2["csv"])
    assert "Notes" in head and not any(n in out2["csv"] for n in notes)
    assert not re.search(r"`notes`", out2["log"])


def test_edits_run_the_same_cleaner():
    rules = {"rules": [{"name": "amount_range", "on": False}, {"name": "category_case", "mode": "dominant"},
                       {"name": "date_date", "order": "nonsense"}],
             "allowed": {"paid": ["Y"], "notes": ["x"]}, "duplicates": {"on": True, "key": ["date", "unit", "notes"]}}
    rep, out = _clean(_sample(), "sample-messy.csv", None, rules)
    assert out["ok"] and out["edited"], out["error"]
    assert any("day/month order" in n for n in out["notes"]) and any("withheld" in n for n in out["notes"])
    assert "You edited the cleaning rules" in out["log"]
    head, rows = _rows(out["csv"])
    # range off: the negatives are no longer flagged; allowed Y only: every N row is kept and flagged
    assert not any("outside the possible range" in r[-2] for r in rows)
    n_rows = [r for r in rows if r[head.index("Paid?")] in ("N", "n", "No", "no", "FALSE")]
    # (a row another rule set aside first carries that rule's reason: one reason per row, as the engine records it)
    assert n_rows and all(r[-2] for r in n_rows) and sum(1 for r in n_rows if "is not one of" in r[-2]) >= len(n_rows) - 5
    # dominant spelling instead of capitals
    assert "Rent" in set(r[head.index("Category")] for r in rows)
    # the duplicate key holds no private column; the card says so
    assert out["card"]["duplicates"]["key"] == ["date", "unit"]
    # the report's own block is untouched
    assert rep["cleaning"]["quarantine_reasons"][0]["count"] == 55


def test_unchanged_edits_are_not_edits():
    rep, out = _clean(_sample(), "sample-messy.csv")
    same = {"rules": [dict(r) for c in out["card"]["columns"] for r in c["rules"]] + out["card"]["general"],
            "allowed": {}, "duplicates": {"on": True, "key": []}}
    rep2, out2 = _clean(_sample(), "sample-messy.csv", None, same)
    assert out2["ok"] and not out2["edited"] and out2["csv"] == out["csv"]


def test_stale_run_refused():
    _clean(_sample(), "sample-messy.csv")
    o = json.loads(NB.cleanfile_json(json.dumps({"sha256": "0" * 64})))
    assert not o["ok"] and "run the file again" in o["error"]


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
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
