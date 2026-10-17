#!/usr/bin/env python3
"""Clean my file: the engine's own cleaning, as a cleaned copy of the visitor's file and a log (track C, 16 Oct 2026).

    view(last, edits=None, plan_columns=None, recount=None) -> {ok, error, csv, log, summary, card, notes, edited}

ONE cleaner. The report's cleaning (northledger.clean, the rules clean.standard_rules derives, the counts in the report's
`cleaning` block, its set-aside rows and its exact-duplicate rule) is what this module shows, as a second VIEW of the
same result (owner, 16 Oct 2026: extend the existing feature, never a second cleaner):

  * the engine's analysis copy (`downloads.clean_csv`) drops the rows it set aside and empties a cell it could not
    read; this view KEEPS every such row, cleans its safe cells by the same rules as every row (the cleaner's reading
    rules replayed, _read_work), keeps ONLY the doubtful cell (the column the engine's reason names, or the value it
    could not read) byte for byte as the file wrote it, and names that column and why in `nl_flag` (owner, 16 Oct
    2026); an uncertain value is never changed and a missing one never filled
  * exact duplicates (the engine's `exact_duplicates` rule) are merged into the first copy, and the log lists them
  * every other value is the engine's cleaned value: its trim, placeholder, case, yes/no, number and date rules
    (dates ISO under the column's one day/month order, vocab.day_month_order; numbers plain)
  * `nl_missing` lists the columns left blank in the row
  * the log's rule table IS the report's `cleaning` block (same rules, same words, same counts)

Edits (the "Cleaning rules" card): the visitor may switch a rule off, change a date column's day/month order, a range,
a letter-case mode or the "not after" date, restrict a column to allowed values, or set the duplicate key; the same
cleaner (clean.clean_frame) then runs again on the same landed table, in this tab, with no AI call, and the log says
the rules were edited (the report above keeps the engine's own).

Privacy (CONTRACT 5.6 and the page's promise for Withhold, "its values are never shown, downloaded or used"): a
withheld column is left out, as in every download; a coded column keeps its codes; neither drives or takes an edit,
and the log calls them "a withheld column" / "a coded column", never by name.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from northledger import clean as C
from northledger import vocab as V

VERSION = "2"
FLAG_COL = "nl_flag"
MISSING_COL = "nl_missing"
SEP = " | "                  # between the reasons in nl_flag
MISS_SEP = "; "              # between the column names in nl_missing
MAX_LISTED = 40              # merged lines named in the log
MAX_ALLOWED = 300
CASES = ("dominant", "upper", "lower", "title")
_DUP_RE = re.compile(r"^exact duplicate of the row kept at input position (\d+)")
_DM_RE = re.compile(C._DAY_MONTH_RE)
_TRIED_RE = re.compile(r" \(tried [^)]*\)")
_COL_RE = re.compile(r"^column '((?:[^'\\]|\\.)*)':")
_READ_KINDS = ("null_like", "trim", "case", "numeric", "date", "boolean")

AI_WORDS = {"date": "a date", "year": "a year", "flow_amount": "an amount (a flow)", "level": "a level",
            "percentage": "a percentage", "log_scale": "a number on a log scale", "count": "a count",
            "rating": "a rating", "duration": "a duration", "boolean": "yes/no", "category": "a category",
            "ordinal": "an ordered category", "geography": "a place", "entity": "a name of a thing",
            "code": "a code", "identifier": "an identifier", "free_text": "free text", "metadata": "metadata",
            "other": "something else"}


# ------------------------------------------------------------------ small helpers
def _plain_number(x: float) -> str:
    if x == 0:
        return "0"
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    s = repr(float(x))
    if "e" in s or "E" in s:
        s = ("%.15f" % x).rstrip("0").rstrip(".")
    return s


def _iso(t: Any) -> str:
    t = pd.Timestamp(t)
    if t.hour == 0 and t.minute == 0 and t.second == 0 and t.microsecond == 0:
        return t.strftime("%Y-%m-%d")
    if t.microsecond == 0:
        return t.strftime("%Y-%m-%d %H:%M:%S") if t.second else t.strftime("%Y-%m-%d %H:%M")
    return t.isoformat(sep=" ")


def _cell(v: Any) -> str:
    """One cleaned cell as text: a date ISO, a number plain, a missing value blank."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(v)
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp) or hasattr(v, "to_pydatetime"):
        return _iso(v)
    if isinstance(v, float):
        return _plain_number(v)
    if isinstance(v, int):
        return str(v)
    if hasattr(v, "item"):
        return _cell(v.item())
    return str(v)


def _raw(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def _short(v: Any, n: int = 40) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def _quote(v: Any) -> str:
    return "'" + _short(v).replace("'", "\u2019") + "'"


def _n(n: Any) -> str:
    return "{:,}".format(int(n))


def _md(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _slug(h: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(h).strip().lower()).strip("_")


def date_order(values: Sequence[Any]) -> Dict[str, Any]:
    """The column's day/month order by the engine's evidence rule (vocab.day_month_order), with its evidence."""
    first = second = 0
    for v in values:
        m = _DM_RE.match(v) if isinstance(v, str) else None
        if m:
            a, b = int(m.group(1)), int(m.group(3))
            if a > 12 and b <= 12:
                first += 1
            elif b > 12 and a <= 12:
                second += 1
    return {"order": V.day_month_order(first, second), "day_first": first, "month_first": second}


def _formats_for(order: str, formats: Sequence[str]) -> List[str]:
    """The date rule's formats under one day/month order: each wrong-order format becomes its twin, as the engine
    does once a column's order is proved (clean._formats_for_day_month_order)."""
    drop = C._MONTH_FIRST_FORMATS if order == "dmy" else C._DAY_FIRST_FORMATS
    out: List[str] = []
    for f in formats:
        g = C._ORDER_TWIN[f] if f in drop else f
        if g not in out:
            out.append(g)
    return out


# ------------------------------------------------------------------ the rules card and the visitor's edits
def _col_rules(rules: List[Any], col: str) -> List[Any]:
    return [r for r in rules if r.column == col]


def _contains(rules: List[Any], col: str, raw: pd.Series, acted: set) -> Tuple[str, Dict[str, Any]]:
    """What the engine's rules say the column should contain, in words, and the facts behind them. `acted`: the
    rules that changed or flagged anything (a yes/no rule acts only on a column that is mostly yes/no values)."""
    kinds = {r.kind: r for r in _col_rules(rules, col)}
    if "boolean" in kinds and kinds["boolean"].name in acted:
        words = "yes/no, in the one pair and spelling the column mostly uses"
        if "allowed" in kinds:
            vals = [str(x) for x in (kinds["allowed"].params or {}).get("values", [])]
            words += "; allowed: %s" % ", ".join(vals[:12])
        return words, {}
    info: Dict[str, Any] = {}
    if "date" in kinds:
        o = date_order([v for v in raw.tolist() if isinstance(v, str)])
        info.update(o)
        forced = (kinds["date"].params or {}).get("order")
        ow = {"dmy": "day first", "mdy": "month first"}.get(
            forced or o["order"], "both orders appear, so a date that could be either is set aside"
            if o["order"] == "mixed" else "not proved by any value")
        words = "a date, written YYYY-MM-DD; day and month: %s" % ow
        if forced:
            words += " (set by you)"
        elif o["order"] == "dmy":
            words += " (%s value%s with a day above 12)" % (_n(o["day_first"]), "" if o["day_first"] == 1 else "s")
        elif o["order"] == "mdy":
            words += " (%s value%s with a day above 12 in second place)" % (
                _n(o["month_first"]), "" if o["month_first"] == 1 else "s")
        if "not_future" in kinds:
            words += "; not after %s" % ((kinds["not_future"].params or {}).get("as_of") or "today")
        return words, info
    if "numeric" in kinds:
        words = "a number, written plainly (no currency sign or thousands separator)"
        if "range" in kinds:
            p = kinds["range"].params or {}
            if p.get("min") is not None:
                words += "; at least %s" % _plain_number(float(p["min"]))
            if p.get("max") is not None:
                words += "; at most %s" % _plain_number(float(p["max"]))
        return words, info
    if "case" in kinds or "boolean" in kinds or "allowed" in kinds:
        mode = (kinds["case"].params or {}).get("mode", "") if "case" in kinds else ""
        words = {"dominant": "one of a set of values, each in its most common spelling",
                 "upper": "a code or category, written in capitals", "title": "a category, written in title case",
                 "lower": "a category, written in lower case"}.get(mode, "text, spaces trimmed")
        if "allowed" in kinds:
            vals = [str(x) for x in (kinds["allowed"].params or {}).get("values", [])]
            words += "; allowed: %s%s" % (", ".join(vals[:12]), " ..." if len(vals) > 12 else "")
        return words, info
    return "text, spaces trimmed only", info


def _editable(r: Any) -> Dict[str, Any]:
    p = r.params or {}
    e: Dict[str, Any] = {"name": r.name, "kind": r.kind, "severity": r.severity, "on": True}
    if r.kind == "date":
        e["order"] = p.get("order", "auto")
    elif r.kind == "range":
        e["min"], e["max"] = p.get("min"), p.get("max")
    elif r.kind == "case":
        e["mode"] = p.get("mode", "upper")
    elif r.kind == "not_future":
        e["as_of"] = p.get("as_of")
    elif r.kind == "allowed":
        e["values"] = [str(x) for x in (p.get("values") or [])]
    return e


def _acted(cr: Any) -> set:
    out = set()
    for d in (getattr(cr, "fixes_applied", None) or {}, getattr(cr, "flags", None) or {}):
        for k, v in d.items():
            if int(v or 0) > 0:
                out.add(str(k).partition(".")[0])
    return out


def card(last: Dict[str, Any], rules: List[Any], plan_columns: Any = None, cr: Any = None) -> Dict[str, Any]:
    """The "Cleaning rules" card: per column, what it should contain (the engine's rules in words, and the AI plan's
    reading of it when the AI is on) and its rules; and the duplicate rule."""
    raw: pd.DataFrame = last["raw"]
    header_of, private = last["header_of"], last["private"]
    plan: Dict[str, Dict[str, Any]] = {}
    for c in plan_columns or []:
        if isinstance(c, dict) and c.get("name"):
            plan[_slug(c["name"])] = c
    cols = []
    for col in raw.columns:
        col = str(col)
        head = header_of.get(col, col)
        if col in private:
            cols.append({"column": col, "header": head, "private": private[col], "contains": None, "rules": []})
            continue
        words, info = _contains(rules, col, raw[col], _acted(cr if cr is not None else last["cr"]))
        pc = plan.get(_slug(head)) or plan.get(col)
        st = str((pc or {}).get("semantic_type") or "")
        cols.append({"column": col, "header": head, "private": None, "contains": words, "found": info,
                     "ai_type": st or None, "ai_words": AI_WORDS.get(st) if st else None,
                     "rules": [_editable(r) for r in _col_rules(rules, col)]})
    dd = next((r for r in rules if r.kind == "dedupe"), None)
    subset = [str(k) for k in ((dd.params or {}).get("subset") or [])] if dd is not None else []
    general = [_editable(r) for r in rules if r.column in (None, "", "*") and r.kind in ("null_like", "trim")]
    return {"columns": cols, "general": general,
            "duplicates": {"on": dd is not None, "key": subset,
                           "words": ("rows with the same %s" % ", ".join(header_of.get(k, k) for k in subset)) if subset
                           else "rows equal in every column once the other rules have run"}}


def _private_note(kind: str) -> str:
    return ("A %s column takes no rule; change it on the personal-data step"
            % ("withheld" if kind == "withhold" else "coded"))


def apply_edits(last: Dict[str, Any], edits: Any) -> Tuple[List[Any], List[str], bool]:
    """The engine's rules with the visitor's edits; every edit is checked, and one refused is said. (rules, notes, changed)"""
    rules = copy.deepcopy(last["rules"])
    notes: List[str] = []
    if not isinstance(edits, dict):
        return rules, notes, False
    private = last["private"]
    cols = set(str(c) for c in last["raw"].columns)
    changed = False
    by_name = {r.name: r for r in rules}
    drop: List[str] = []
    for e in edits.get("rules") or []:
        if not isinstance(e, dict) or e.get("name") not in by_name:
            continue
        r = by_name[e["name"]]
        if r.column in private:
            notes.append(_private_note(private[r.column]))
            continue
        p = dict(r.params or {})
        if e.get("on") is False:
            drop.append(r.name)
            changed = True
            continue
        if r.kind == "date" and "order" in e and (e.get("order") or "auto") != p.get("order", "auto"):
            o = e.get("order") or "auto"
            if o in ("dmy", "mdy"):
                p["order"] = o
                p["formats"] = _formats_for(o, p.get("formats") or C.DEFAULT_DATE_FORMATS)
                changed = True
            elif o == "auto":
                orig = next((x for x in last["rules"] if x.name == r.name), None)
                p = dict(orig.params or {}) if orig is not None else p
                p.pop("order", None)
                changed = True
            else:
                notes.append("%s: day/month order %r unknown; kept the engine's" % (r.name, o))
        elif r.kind == "range":
            for k in ("min", "max"):
                if k not in e:
                    continue
                v = e.get(k)
                nv: Optional[float] = None
                if v not in (None, ""):
                    try:
                        nv = float(v)
                        if not math.isfinite(nv):
                            raise ValueError
                    except (TypeError, ValueError):
                        notes.append("%s: %s %r is not a number; kept the engine's" % (r.name, k, v))
                        continue
                if nv != (None if p.get(k) is None else float(p[k])):
                    p[k] = nv
                    changed = True
            if p.get("min") is None and p.get("max") is None:
                drop.append(r.name)
                continue
        elif r.kind == "case" and e.get("mode") not in (None, p.get("mode")):
            if e["mode"] in CASES:
                p["mode"] = e["mode"]
                changed = True
            else:
                notes.append("%s: letter case %r unknown; kept the engine's" % (r.name, e["mode"]))
        elif r.kind == "not_future" and e.get("as_of") not in (None, "") and e.get("as_of") != p.get("as_of"):
            try:
                p["as_of"] = pd.Timestamp(str(e["as_of"])[:10]).strftime("%Y-%m-%d")
                changed = True
            except Exception:  # noqa: BLE001
                notes.append("%s: %r is not a date; kept the engine's" % (r.name, e.get("as_of")))
        r.params = p
    rules = [r for r in rules if r.name not in drop]
    # allowed values: the engine's own "allowed" rule, added before its duplicate check; a value outside the list is
    # set aside by the cleaner and, in this view, kept and flagged
    allowed = edits.get("allowed")
    if isinstance(allowed, dict):
        for col, vals in allowed.items():
            col = str(col)
            if col not in cols:
                continue
            if col in private:
                notes.append(_private_note(private[col]))
                continue
            vals = [str(x).strip() for x in vals if str(x).strip()][:MAX_ALLOWED] if isinstance(vals, list) else []
            name = "%s_allowed" % _slug(col)
            old = next((r for r in rules if r.name == name), None)
            old_vals = [str(x) for x in (old.params or {}).get("values") or []] if old is not None else []
            if old_vals == vals:
                continue
            if old is not None:
                rules.remove(old)
            if vals:
                at = next((i for i, r in enumerate(rules) if r.kind == "dedupe"), len(rules))
                rules.insert(at, C.Rule(name, col, "allowed", {"values": vals, "case_insensitive": True}, "quarantine"))
            changed = True
    d = edits.get("duplicates")
    if isinstance(d, dict):
        dd = next((r for r in rules if r.kind == "dedupe"), None)
        key = [str(k) for k in (d.get("key") or []) if str(k) in cols]
        if any(k in private for k in key):
            notes.append("A withheld or coded column cannot be part of the duplicate key: it never drives a rule")
            key = [k for k in key if k not in private]
        if d.get("on") is False:
            if dd is not None:
                rules.remove(dd)
                changed = True
        else:
            old = [str(k) for k in ((dd.params or {}).get("subset") or [])] if dd is not None else None
            if old != key:
                if dd is not None:
                    rules.remove(dd)
                rules.append(C.Rule("exact_duplicates", "", "dedupe",
                                    dict({"keep": "first"}, **({"subset": key} if key else {})), "quarantine"))
                changed = True
    return rules, notes, changed


def _read_work(raw: pd.DataFrame, rules: List[Any]) -> pd.DataFrame:
    """The cleaner's reading rules, in its order, on every row, nothing set aside: a rule that would set a row aside
    for an unreadable value only flags it here (the value is then empty, and the view keeps the file's text)."""
    rr = []
    for r in rules:
        if r.kind not in _READ_KINDS:
            continue
        r2 = copy.deepcopy(r)
        if r2.kind in ("numeric", "date", "boolean") and r2.severity == "quarantine":
            r2.severity = "flag"
        rr.append(r2)
    return C.clean_frame(raw.copy(), rr).clean


# ------------------------------------------------------------------ the view
def view(last: Dict[str, Any], edits: Any = None, plan_columns: Any = None,
         recount: Optional[Callable[[Any, List[Any]], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The cleaned copy and the log for the last run's cleaning (`last`: nl_browser._LAST_CLEAN). Never raises."""
    try:
        return _view(last, edits, plan_columns, recount)
    except Exception as exc:  # noqa: BLE001 - the page shows the reason; nothing half-made is offered
        return {"ok": False, "error": "The file could not be cleaned (%s)." % (str(exc)[:200] or type(exc).__name__),
                "csv": "", "log": "", "summary": None, "card": None}


def _view(last: Dict[str, Any], edits: Any, plan_columns: Any, recount: Any) -> Dict[str, Any]:
    raw: pd.DataFrame = last["raw"]
    lines = last.get("lines")
    if lines is None or len(lines) != len(raw):
        return {"ok": False, "error": "This file was read reshaped, so a cleaned copy in your file's own shape cannot be "
                "made here. The cleaned CSV under \"Take it with you\" holds the engine's cleaned table.",
                "csv": "", "log": "", "summary": None, "card": None}
    rules, notes, edited = apply_edits(last, edits)
    if edited:
        cr = C.clean_frame(raw.copy(), rules)
        cleaning = recount(cr, rules) if recount else None
    else:
        cr, rules, cleaning = last["cr"], last["rules"], last["cleaning"]
    header_of, private = last["header_of"], last["private"]
    reason_fix = last.get("reason_fix") or (lambda t: t)
    cols = [str(c) for c in raw.columns]
    out_cols = [c for c in cols if private.get(c) != "withhold"]
    clean, quar = cr.clean, cr.quarantined
    n = len(raw)

    # the rule that reads each column (a number, a date, a yes/no); the one that can empty a cell it cannot read
    reader_of: Dict[str, Any] = {}
    for r in rules:
        if r.column and r.column != "*" and r.kind in ("numeric", "date", "boolean") and r.column not in reader_of:
            reader_of[r.column] = r

    merged_into: Dict[int, int] = {}
    kept_aside: Dict[int, Tuple[Optional[str], str]] = {}     # row -> (the column the reason names, the reason)
    if len(quar):
        for pos, msg in zip(quar.index.tolist(), quar[C.QUARANTINE_COL].tolist()):
            m = _DUP_RE.match(str(msg))
            if m:
                merged_into[int(pos)] = int(m.group(1))
            else:
                # the engine's words, without the list of formats it tried (that list follows the order the formats
                # were seen in, so the same value would carry a different flag in a file cleaned twice)
                text = _TRIED_RE.sub("", str(reason_fix(msg)))
                mc = _COL_RE.match(text)
                kept_aside[int(pos)] = (mc.group(1), text[mc.end():].strip()) if mc and mc.group(1) in cols else (None, text)
    clean_pos = set(int(i) for i in clean.index)
    # a set-aside row's other cells: the cleaner's reading rules (trim, placeholders, case, numbers, dates, yes/no)
    # replayed on the same landed table, every row kept (the values the cleaner held for that row before a rejection
    # rule set it aside); the report's own clean rows are taken from its result, never from this replay
    work = _read_work(raw, rules) if kept_aside else None

    rows_out: List[Tuple[int, List[str], List[str]]] = []
    restored: Dict[str, int] = {}
    doubtful: Dict[str, int] = {}
    for i in range(n):
        if i in merged_into:
            continue
        flags: List[str] = []
        vals: List[str] = []
        aside = kept_aside.get(i)
        if aside is None and i not in clean_pos:
            continue
        frame = work if aside is not None else clean
        for c in out_cols:
            rv = _raw(raw.at[i, c])
            if private.get(c) == "code":
                vals.append(_cell(clean.at[i, c]) if aside is None else rv)
                continue
            if aside is not None and aside[0] == c:
                # the cell the engine's reason names: exactly as the file wrote it
                vals.append(rv)
                doubtful[c] = doubtful.get(c, 0) + 1
                flags.append("%s: %s; the analysis sets this row aside, the value kept as written"
                             % (header_of.get(c, c), aside[1]))
                continue
            v = _cell(frame.at[i, c])
            if v == "" and rv.strip() and not V.is_null_like(rv):
                # a value the cleaner could not read (it empties it, in a column that is not the row's key): kept
                rule = reader_of.get(c)
                v = rv
                restored[c] = restored.get(c, 0) + 1
                flags.append("%s: %s could not be read%s, kept as written"
                             % (header_of.get(c, c), _quote(rv), " (rule %s)" % rule.name if rule is not None else ""))
            vals.append(v)
        if aside is not None and aside[0] is None:
            flags.append("the analysis sets this row aside: %s" % aside[1])
        rows_out.append((i, vals, flags))

    # a duplicate key: a kept row whose merged copies differed elsewhere says so
    dd = next((r for r in rules if r.kind == "dedupe"), None)
    subset = [str(k) for k in ((dd.params or {}).get("subset") or [])] if dd is not None else []
    if subset and merged_into:
        by_row = {i: flags for i, _vals, flags in rows_out}
        diff: Dict[int, set] = {}
        for r, k in merged_into.items():
            if k not in by_row:
                continue
            for c in out_cols:
                if c in subset:
                    continue
                if " ".join(_raw(raw.at[r, c]).split()) != " ".join(_raw(raw.at[k, c]).split()):
                    diff.setdefault(k, set()).add("a coded column" if private.get(c) == "code" else header_of.get(c, c))
        for k, cs in diff.items():
            nm = sum(1 for v in merged_into.values() if v == k)
            by_row[k].append("duplicates: %s row%s with the same %s merged here differed in %s"
                             % (nm, "" if nm == 1 else "s", ", ".join(header_of.get(x, x) for x in subset),
                                ", ".join(sorted(cs))))

    # a looser duplicate rule, flagged and never merged: equal in every column this file shows, letter case and
    # spacing ignored, where the exact rule kept the rows apart (a withheld or coded column differs, or a spelling)
    possible = 0
    if dd is not None and not subset:
        shown = [j for j, c in enumerate(out_cols) if private.get(c) is None]
        groups: Dict[Tuple[str, ...], List[int]] = {}
        for k, (i, vals, _flags) in enumerate(rows_out):
            if i in kept_aside:
                continue
            key = tuple(" ".join(vals[j].split()).lower() for j in shown)
            if all(x == "" for x in key):
                continue
            groups.setdefault(key, []).append(k)
        why = ("a withheld or coded column, or letter case and spacing" if any(private.get(c) for c in cols)
               else "letter case and spacing")
        for ks in groups.values():
            if len(ks) < 2:
                continue
            for k in ks:
                possible += 1
                rows_out[k][2].append("possible duplicate: equal to another row except in %s; not merged" % why)

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    heads = [header_of.get(c, c) for c in out_cols]
    w.writerow(heads + [FLAG_COL, MISSING_COL])
    missing: Dict[str, int] = {h: 0 for h in heads}
    flagged = 0
    for _i, vals, flags in rows_out:
        miss = [h for h, v in zip(heads, vals) if v == ""]
        for h in miss:
            missing[h] += 1
        if flags:
            flagged += 1
        w.writerow(vals + [SEP.join(flags), MISS_SEP.join(miss)])

    summary = {"rows_in": n, "rows_out": len(rows_out), "merged": len(merged_into), "kept_set_aside": len(kept_aside),
               "cells_restored": {header_of.get(c, c): k for c, k in restored.items()},
               "cells_doubtful": {header_of.get(c, c): k for c, k in doubtful.items()},
               "possible_duplicates": possible, "flagged_rows": flagged,
               "columns_in": len(cols), "columns_out": len(out_cols) + 2,
               "withheld_left_out": sum(1 for c in cols if private.get(c) == "withhold"),
               "coded": sum(1 for c in cols if private.get(c) == "code"),
               "missing_cells": missing,
               "merged_lines": [[lines[r], lines[k] if 0 <= k < len(lines) else None] for r, k in sorted(merged_into.items())],
               "edited": bool(edited),
               "engine_rows_clean": int(cr.rows_clean), "engine_rows_set_aside": int(cr.rows_quarantined)}
    cd = card(last, rules, plan_columns, cr)
    log = _log(last, summary, cleaning, cd, notes)
    return {"ok": True, "error": None, "version": VERSION, "csv": buf.getvalue(), "log": log, "summary": summary,
            "card": cd, "notes": notes, "edited": bool(edited), "cleaning": cleaning}


# ------------------------------------------------------------------ the log
def _log(last: Dict[str, Any], S: Dict[str, Any], cleaning: Optional[Dict[str, Any]], cd: Dict[str, Any],
         notes: List[str]) -> str:
    private, header_of = last["private"], last["header_of"]

    def colname(c: Any) -> str:
        if c is None:
            return "across the file"
        c = str(c)
        if private.get(c) == "withhold":
            return "a withheld column"
        if private.get(c) == "code":
            return "a coded column"
        h = header_of.get(c, c)
        return "`%s`" % _md(c) + (" (%s)" % _md(h) if h != c else "")

    L: List[str] = []
    L.append("# Cleaning log: %s" % _md(last.get("name") or "your file"))
    L.append("")
    L.append("Made in your browser by NorthLedger's cleaner, the same one behind the report on the page. **The original "
             "file was not changed**: the cleaned CSV and this log are new files. Missing values were never filled in. "
             "Where the engine's analysis sets a row aside or empties a value it could not read, this cleaned copy keeps "
             "the row, cleans its other cells by the same rules as every row, keeps the doubtful value exactly as "
             "written, and names that column and why in `nl_flag`.")
    if S.get("edited"):
        L.append("")
        L.append("**You edited the cleaning rules**: the counts here are the cleaner's under your rules; the report on "
                 "the page keeps the engine's own rules.")
    if last.get("planned"):
        L.append("")
        L.append("The AI plan's row steps ran before cleaning, so the rows here are the file as the plan left it.")
    L.append("")
    L.append("## Before and after")
    L.append("")
    L.append("| | Before | After |")
    L.append("|---|---:|---:|")
    L.append("| Rows | %s | %s |" % (_n(S["rows_in"]), _n(S["rows_out"])))
    L.append("| Columns | %s | %s |" % (_n(S["columns_in"]), _n(S["columns_out"])))
    L.append("")
    bits = ["`nl_flag` and `nl_missing` added"]
    if S["withheld_left_out"]:
        bits.append("%s withheld column%s left out, as you chose on the personal-data step" % (
            _n(S["withheld_left_out"]), "" if S["withheld_left_out"] == 1 else "s"))
    if S["coded"]:
        bits.append("%s coded column%s written as codes" % (_n(S["coded"]), "" if S["coded"] == 1 else "s"))
    L.append("Columns: %s. Rows: %s duplicate%s merged into the first copy; %s row%s the engine's analysis sets "
             "aside kept here (doubtful value as written, flagged)." % ("; ".join(bits), _n(S["merged"]), "" if S["merged"] == 1 else "s",
                                                _n(S["kept_set_aside"]), "" if S["kept_set_aside"] == 1 else "s"))
    L.append("")
    L.append("## Rules applied, and the cells or rows each affected")
    L.append("")
    if cleaning:
        L.append("The cleaner's own record: the same rules, words and counts as \"What the cleaning did\" in the "
                 "report%s." % (" (here under your edited rules)" if S.get("edited") else ""))
        L.append("")
        L.append("| Rule | Column | Cells or rows | What it did |")
        L.append("|---|---|---:|---|")
        for f in cleaning.get("fixes") or []:
            L.append("| `%s` | %s | %s | %s |" % (_md(f["rule"]), colname(f.get("column")), _n(f["count"]), _md(f["what"])))
        L.append("")
        L.append("Rows the engine's analysis sets aside (the report's \"What was set aside, and why\"): %s of %s." % (
            _n(cleaning.get("rows_quarantined", 0)), _n(cleaning.get("rows_in", 0))))
        if cleaning.get("quarantine_reasons"):
            L.append("")
            L.append("| Reason | Rows | In this cleaned copy |")
            L.append("|---|---:|---|")
            for q in cleaning["quarantine_reasons"]:
                dup = str(q["reason"]).startswith("exact_duplicates")
                L.append("| %s | %s | %s |" % (_md(q["reason"]), _n(q["count"]),
                                              "merged into the first copy" if dup else "row kept and cleaned; the named value kept as written, flagged"))
    L.append("")
    L.append("## Duplicates")
    L.append("")
    dd = cd["duplicates"]
    if not dd["on"]:
        L.append("Rule: none (you switched the duplicate check off). Rows merged: **0**.")
    else:
        L.append("Rule: %s are duplicates; the first copy is kept and the others are merged into it. Rows merged: "
                 "**%s**." % (dd["words"], _n(S["merged"])))
        if S["merged_lines"]:
            shown = S["merged_lines"][:MAX_LISTED]
            L.append("")
            L.append("Line in your file -> the line it was merged into: " +
                     ", ".join("%s -> %s" % (a, b) for a, b in shown) +
                     (" (and %s more)" % _n(len(S["merged_lines"]) - len(shown))
                      if len(S["merged_lines"]) > len(shown) else "") + ".")
        if not dd["key"]:
            L.append("")
            L.append("A looser rule was checked too (equal in every column of this file, letter case and spacing ignored, "
                     "while a withheld or coded column kept them apart): "
                     "%s row%s matched another row that way; flagged, not merged." % (
                         _n(S["possible_duplicates"]), "" if S["possible_duplicates"] == 1 else "s"))
    L.append("")
    L.append("## Flagged rows")
    L.append("")
    L.append("**%s** row%s carry a flag in `nl_flag`. Only the cell a flag names was left as written; the row's other cells were cleaned like every row." % (
        _n(S["flagged_rows"]), "" if S["flagged_rows"] == 1 else "s"))
    L.append("")
    L.append("| Why | Count |")
    L.append("|---|---:|")
    L.append("| rows the analysis sets aside: kept, every other cell cleaned | %s rows |" % _n(S["kept_set_aside"]))
    for c, k in sorted(S.get("cells_doubtful", {}).items()):
        L.append("| `%s`: the value a set-aside row's reason names, kept as written | %s cells |" % (_md(c), _n(k)))
    for c, k in sorted(S["cells_restored"].items()):
        L.append("| `%s`: could not be read (emptied in the analysis), kept as written | %s cells |" % (_md(c), _n(k)))
    if S["possible_duplicates"]:
        L.append("| possible duplicate under the looser rule, not merged | %s rows |" % _n(S["possible_duplicates"]))
    L.append("")
    L.append("## Missing values")
    L.append("")
    L.append("Blank cells are left blank and listed in `nl_missing`; nothing was inferred or filled. A placeholder such "
             "as N/A or a dash counts as blank (the engine's one list of words for missing).")
    L.append("")
    L.append("| Column | Blank cells |")
    L.append("|---|---:|")
    for h, k in S["missing_cells"].items():
        L.append("| `%s` | %s |" % (_md(h), _n(k)))
    L.append("")
    L.append("## What each column should contain")
    L.append("")
    L.append("| Column | Should contain (the cleaner's rules) | The AI plan read it as |")
    L.append("|---|---|---|")
    for c in cd["columns"]:
        if c["private"] == "withhold":
            L.append("| a withheld column | left out of this file; no rule reads it | |")
        elif c["private"] == "code":
            L.append("| a coded column | codes, one per value; no rule reads it | |")
        else:
            L.append("| `%s` | %s | %s |" % (_md(c["header"]), _md(c["contains"]), _md(c.get("ai_words") or "")))
    if notes:
        L.append("")
        L.append("## Edits not applied")
        L.append("")
        for t in notes:
            L.append("- %s" % _md(t))
    L.append("")
    L.append("## About this file")
    L.append("")
    L.append("- UTF-8, comma-separated; one row per row of your file, in its order; your columns in their order.")
    L.append("- `nl_flag` holds the reasons a row is unsure, separated by \" | \"; `nl_missing` lists the blank columns, "
             "separated by \"; \".")
    L.append("")
    return "\n".join(L)
