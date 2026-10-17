#!/usr/bin/env python3
"""Build a tracker from messy files (Track G, plan/DASHBOARD-DESIGN.md; CONTRACT-v2 section 5.18).

    build(inputs, kind=None, questions=None, today=None, plan_columns=None, dup_key=None) -> record

ONE cleaner. The tracker's rows ARE the "Clean my file" view's rows (engine/nl_cleanfile.view(..., detail=True)): the
engine's cleaner, its exact-duplicate merge and its flags, with every flagged row kept, its safe cells cleaned and only
its doubtful cell as written (owner, 16 Oct 2026). This module adds only what a tracker needs on top of that view:

  * kind templates (tasks, requests, inventory, shifts, vendors, other): the columns a tracker of that kind keeps,
    the header words that name each, the status words, and the formula columns (days open, stock value, hours,
    vendor spend);
  * the kind suggested from the files' headers (and the AI plan's semantic types when it ran), editable;
  * several files combined by matching columns (a column's role, else its name), each row keeping its source file and
    line; rows that are the same record in two files merged into the first file's copy, by the cleaner's own
    duplicate rule taken across files (equal in every column both files hold); a column no file maps keeps its own
    name, at the end; NO source column is dropped (a withheld one never arrives: the view leaves it out);
  * status words mapped to the template's (done, complete, closed -> Done ...), an unknown word kept as written,
    flagged and added to the dropdown list; one spelling of a category across files;
  * the workbook as a spec for the page's one .xlsx writer (src/js/56-xlsx.js, window.NLX.workbook): Data (an Excel
    table, typed columns, status and category dropdowns from Lists, formula columns as calculated columns, the source
    file and line of every row), Summary (COUNTIFS / SUMIFS / LARGE answering the visitor's questions: formulas, not
    pasted numbers), Lists, Instructions, Changes (row, column, before, after, rule), Merged duplicates (kept row,
    merged row, rule), Unplaced (rows that are not records: the header written again, a note line; a file that could
    not be read in its own shape) and Flags (every doubtful cell, every blank, every check that failed).

Never invents a row or a value: a blank stays blank and is flagged; a doubtful value stays as written and is flagged.
Every formula's cached value is computed here from the same rows (a viewer that does not recalculate shows the same
number Excel will); tools/test_nl_tracker.py evaluates the formulas independently and checks them against pandas.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "1"
TABLE = "Tracker"
SOURCE_COL = "Source file"
LINE_COL = "Line in source"
CHECK_COL = "Check"
EXTRA_ROWS = 1000             # dropdowns reach this many rows below the data, for rows a visitor adds
MAX_FLAG_TEXT = 300
MAX_QUESTIONS = 8

# ------------------------------------------------------------------ the header words that name each column role
ROLE_WORDS: Dict[str, List[str]] = {
    "id": ["id", "task id", "ticket", "ticket id", "ticket no", "ref", "reference", "request id", "case id", "job id",
           "job no", "no", "number", "item id", "sku", "item code", "part no", "part number", "product code", "code",
           "invoice", "invoice no", "invoice number", "shift id"],
    "title": ["task", "title", "summary", "subject", "description", "request", "job", "issue", "item", "item name",
              "product", "product name", "details", "what"],
    "status": ["status", "state", "stage", "progress", "paid", "paid?", "payment status"],
    "category": ["category", "type", "kind", "area", "group", "class", "department", "role", "position"],
    "priority": ["priority", "urgency", "severity", "importance"],
    "owner": ["owner", "assigned to", "assignee", "team", "responsible", "who", "handled by", "assigned"],
    "requester": ["requester", "requested by", "reported by", "raised by", "customer", "client", "tenant"],
    "opened": ["opened", "date opened", "date logged", "logged", "created", "created at", "date created", "raised",
               "date raised", "received", "date received", "reported", "date reported", "start date", "submitted"],
    "due": ["due", "due date", "deadline", "target", "target date", "due by"],
    "closed": ["closed", "date closed", "completed", "date completed", "completion date", "resolved", "date resolved",
               "done date", "finished", "closed at", "completed on"],
    "location": ["location", "site", "bin", "warehouse", "shelf", "store", "building"],
    "qty": ["qty", "quantity", "on hand", "stock", "in stock", "count", "units", "qty on hand"],
    "unit_cost": ["unit cost", "cost each", "unit price", "price", "cost", "cost per unit"],
    "reorder": ["reorder level", "reorder point", "reorder at", "min", "minimum", "min stock", "par"],
    "person": ["staff", "employee", "person", "worker", "name", "staff member"],
    "date": ["date", "shift date", "day", "work date", "invoice date", "bill date", "date of invoice"],
    "start": ["start", "start time", "clock in", "time in", "in", "from", "begins"],
    "end": ["end", "end time", "clock out", "time out", "out", "to", "ends"],
    "vendor": ["vendor", "supplier", "payee", "company", "contractor", "vendor name", "supplier name"],
    "amount": ["amount", "total", "spend", "value", "invoice amount", "amount due", "net", "gross"],
}

# ------------------------------------------------------------------ the kind templates
_TASK_STATUS = {
    "canon": ["Open", "In progress", "Waiting", "Done", "Cancelled"],
    "closed": ["Done", "Cancelled"],
    "done": "Done",
    "words": {
        "Open": ["open", "new", "to do", "todo", "not started", "pending", "logged", "raised", "submitted", "received",
                 "backlog", "reopened"],
        "In progress": ["in progress", "wip", "doing", "started", "working", "ongoing", "active", "assigned", "in hand",
                        "underway", "under way", "in-progress"],
        "Waiting": ["waiting", "on hold", "hold", "blocked", "paused", "awaiting", "deferred", "parked", "waiting on"],
        "Done": ["done", "complete", "completed", "closed", "resolved", "finished", "fixed"],
        "Cancelled": ["cancelled", "canceled", "dropped", "won't do", "wont do", "rejected", "void", "abandoned",
                      "withdrawn"],
    },
}
KINDS: Dict[str, Dict[str, Any]] = {
    "tasks": {
        "label": "Tasks", "noun": "task", "title_role": "title",
        "columns": [("id", "ID"), ("title", "Title"), ("status", "Status"), ("category", "Category"),
                    ("priority", "Priority"), ("owner", "Owner"), ("opened", "Opened"), ("due", "Due"),
                    ("closed", "Closed")],
        "core": ["id", "title", "status", "category", "owner", "opened", "due", "closed"],
        "status": _TASK_STATUS, "formulas": ["days_open", "is_open", "overdue", "open_key"],
        "words": ["task", "todo", "to do", "job", "action"],
        "questions": ["How many tasks are open?", "How many are overdue?", "Which open tasks are oldest?",
                      "How many tasks are in each category, and how many of them are open?"],
    },
    "requests": {
        "label": "Requests", "noun": "request", "title_role": "title",
        "columns": [("id", "ID"), ("title", "Request"), ("status", "Status"), ("category", "Category"),
                    ("priority", "Priority"), ("requester", "Requester"), ("owner", "Assigned to"),
                    ("opened", "Opened"), ("due", "Due"), ("closed", "Closed")],
        "core": ["id", "title", "status", "category", "owner", "opened", "due", "closed"],
        "status": _TASK_STATUS, "formulas": ["days_open", "is_open", "overdue", "open_key"],
        "words": ["request", "ticket", "case", "issue", "complaint", "enquiry", "inquiry", "work order"],
        "questions": ["How many requests are open?", "How many are overdue?", "Which open requests are oldest?",
                      "How long does a request take to close, on average?"],
    },
    "inventory": {
        "label": "Inventory", "noun": "item", "title_role": "title",
        "columns": [("id", "SKU"), ("title", "Item"), ("category", "Category"), ("location", "Location"),
                    ("status", "Status"), ("qty", "Qty"), ("unit_cost", "Unit cost"), ("reorder", "Reorder level")],
        "core": ["id", "title", "category", "location", "qty", "unit_cost"],
        "status": {"canon": ["In stock", "Low", "Out of stock", "On order", "Discontinued"],
                   "closed": ["Discontinued"], "done": None,
                   "words": {"In stock": ["in stock", "available", "ok", "instock"], "Low": ["low", "low stock"],
                             "Out of stock": ["out of stock", "out", "none", "oos", "sold out"],
                             "On order": ["on order", "ordered", "backorder", "back order", "backordered"],
                             "Discontinued": ["discontinued", "retired", "obsolete"]}},
        "formulas": ["stock_value", "reorder_flag"],
        "words": ["sku", "stock", "qty", "quantity", "inventory", "item", "part", "bin", "reorder"],
        "questions": ["What is the stock worth in total?", "Which items are at or below their reorder level?",
                      "How many items are in each category?"],
    },
    "shifts": {
        "label": "Shifts", "noun": "shift", "title_role": "person",
        "columns": [("id", "Shift ID"), ("date", "Date"), ("person", "Person"), ("category", "Role"),
                    ("location", "Location"), ("start", "Start"), ("end", "End"), ("status", "Status")],
        "core": ["date", "person", "category", "start", "end", "status"],
        "status": {"canon": ["Scheduled", "Worked", "Absent", "Cancelled", "Swapped"], "closed": ["Cancelled"],
                   "done": None,
                   "words": {"Scheduled": ["scheduled", "planned", "rostered", "booked"],
                             "Worked": ["worked", "done", "completed", "present", "attended"],
                             "Absent": ["absent", "no show", "no-show", "sick", "missed"],
                             "Cancelled": ["cancelled", "canceled", "dropped"],
                             "Swapped": ["swapped", "covered", "swap"]}},
        "formulas": ["hours"],
        "words": ["shift", "rota", "roster", "clock", "start time", "end time", "hours"],
        "questions": ["How many hours were worked in total?", "How many hours by role?", "How many shifts in each status?"],
    },
    "vendors": {
        "label": "Vendors", "noun": "bill", "title_role": "vendor",
        "columns": [("vendor", "Vendor"), ("id", "Invoice"), ("category", "Category"), ("date", "Invoice date"),
                    ("due", "Due"), ("amount", "Amount"), ("status", "Status")],
        "core": ["vendor", "id", "category", "date", "amount", "status"],
        "status": {"canon": ["Unpaid", "Paid", "Disputed", "Cancelled"], "closed": ["Paid", "Cancelled"],
                   "done": "Paid",
                   "words": {"Unpaid": ["unpaid", "open", "due", "outstanding", "pending", "not paid", "no", "n"],
                             "Paid": ["paid", "settled", "cleared", "yes", "y"],
                             "Disputed": ["disputed", "query", "queried", "on hold"],
                             "Cancelled": ["cancelled", "canceled", "void", "credited"]}},
        "formulas": ["vendor_total"],
        "words": ["vendor", "supplier", "invoice", "payee", "bill", "spend"],
        "questions": ["How much have we spent with each vendor?", "How much is unpaid?", "How much by category?"],
    },
    "other": {
        "label": "Something else", "noun": "row", "title_role": "title",
        "columns": [("id", "ID"), ("title", "Name"), ("status", "Status"), ("category", "Category"),
                    ("owner", "Owner"), ("date", "Date")],
        "core": [],
        "status": None, "formulas": [],
        "words": [],
        "questions": ["How many rows are in each status?", "How many rows are in each category?"],
    },
}
ORDER = ["tasks", "requests", "inventory", "shifts", "vendors", "other"]
DATE_ROLES = ("opened", "due", "closed", "date")
NUMBER_ROLES = ("qty", "unit_cost", "reorder", "amount")
TIME_ROLES = ("start", "end")

FORMULA_COLS = {
    "days_open": "Days open", "is_open": "Is open", "overdue": "Overdue", "open_key": "Oldest-open sort key",
    "stock_value": "Stock value", "reorder_flag": "Reorder", "hours": "Hours", "vendor_total": "Vendor total",
}


# ------------------------------------------------------------------ small helpers
def _norm(h: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9?]+", " ", str(h).lower()).split())


def _words(h: Any) -> List[str]:
    return _norm(h).replace("?", "").split()


def _sref(col: str) -> str:
    """A structured reference to this row's cell of a table column (Excel's file form of [@Col])."""
    return "%s[[#This Row],[%s]]" % (TABLE, re.sub(r"([\[\]#'])", r"'\1", col))


def _col(i: int) -> str:
    s, i = "", i + 1
    while i > 0:
        m = (i - 1) % 26
        s = chr(65 + m) + s
        i = (i - 1) // 26
    return s


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def _xstr(s: str) -> str:
    return '"' + str(s).replace('"', '""') + '"'


_ISO_D = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _as_date(v: str) -> Optional[_dt.datetime]:
    if _ISO_D.match(v):
        return _dt.datetime.strptime(v, "%Y-%m-%d")
    if _ISO_DT.match(v):
        return _dt.datetime.strptime(v, "%Y-%m-%d %H:%M:%S" if v.count(":") == 2 else "%Y-%m-%d %H:%M")
    return None


def _as_number(v: str) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _as_time(v: str) -> Optional[float]:
    """HH:MM[:SS] as a fraction of a day (Excel's time), or None."""
    m = _TIME.match(v)
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or se > 59:
        return None
    return (h * 3600 + mi * 60 + se) / 86400.0


def _serial(d: _dt.datetime) -> float:
    return (d - _dt.datetime(1899, 12, 30)).total_seconds() / 86400.0


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return "%s %s" % ("{:,}".format(n), one if n == 1 else (many or one + "s"))


# ------------------------------------------------------------------ roles: which column of each file is which
def _match_role(head: str, roles: Sequence[str], taken: set) -> Optional[str]:
    """The role a header names: an exact header word list first, then a header that holds a role's words."""
    n = _norm(head).replace("?", "").strip()
    for r in roles:
        if r not in taken and n in [_norm(w).replace("?", "").strip() for w in ROLE_WORDS.get(r, [])]:
            return r
    hw = set(_words(head))
    best, best_len = None, 0
    for r in roles:
        if r in taken:
            continue
        for w in ROLE_WORDS.get(r, []):
            ww = _words(w)
            if len(ww) >= 1 and set(ww) <= hw and len(ww) > best_len and (len(ww) > 1 or len(hw) <= 3):
                best, best_len = r, len(ww)
    return best


def map_columns(heads: Sequence[str], kinds: Sequence[str], kind: str) -> Dict[str, str]:
    """{header: role} for one file under a kind's template; each role at most once; a column the template does not
    name has no role (it keeps its own name). `kinds`: the view's column kinds (date, number, text, code)."""
    T = KINDS[kind]
    roles = [r for r, _l in T["columns"]]
    taken: set = set()
    out: Dict[str, str] = {}
    # strongest first: exact names, then contained words; a date role only for a column the cleaner reads as dates,
    # a number role only for numbers
    for exact in (True, False):
        for h, k in zip(heads, kinds):
            if h in out or k == "code":
                continue
            r = _match_role(h, roles, taken) if not exact else next(
                (x for x in roles if x not in taken and _norm(h).replace("?", "").strip() in
                 [_norm(w).replace("?", "").strip() for w in ROLE_WORDS.get(x, [])]), None)
            if r is None:
                continue
            if r in DATE_ROLES and k not in ("date", "text"):
                continue
            if r in NUMBER_ROLES and k not in ("number", "text"):
                continue
            out[h] = r
            taken.add(r)
    return out


def suggest(inputs: Sequence[Dict[str, Any]], plan_columns: Any = None) -> Dict[str, Any]:
    """The kind of tracker the files look like: {kind, why, scores}. From the headers (each role a template finds, and
    the words a kind is named by) and, when the AI plan ran, its semantic types (dates, amounts, counts)."""
    heads: List[str] = []
    kinds: List[str] = []
    names: List[str] = []
    for inp in inputs:
        d = (inp.get("view") or {}).get("detail") or {}
        heads += list(d.get("heads") or [])
        kinds += list(d.get("kinds") or [])
        names.append(str(inp.get("name") or ""))
    words = " ".join(_norm(h) for h in heads + names)
    sem = [str((c or {}).get("semantic_type") or "") for c in (plan_columns or []) if isinstance(c, dict)]
    scores: Dict[str, float] = {}
    why: Dict[str, List[str]] = {}
    for k in ORDER[:-1]:
        T = KINDS[k]
        m = map_columns(heads, kinds, k)
        found = [r for r in T["core"] if r in m.values()]
        s = float(len(found))
        hits = [w for w in T["words"] if re.search(r"\b%s\b" % re.escape(w), words)]
        s += 1.5 * len(hits)
        if k in ("tasks", "requests") and {"opened", "status"} <= set(m.values()):
            s += 1.0
        if k == "vendors" and "flow_amount" in sem:
            s += 1.0
        if k == "inventory" and "count" in sem:
            s += 0.5
        scores[k] = s
        why[k] = (["columns it would fill: %s" % ", ".join(dict(T["columns"])[r] for r in found)] if found else []) + \
                 (["the words %s in your headers or file names" % ", ".join("“%s”" % w for w in hits)] if hits else [])
    best = max(ORDER[:-1], key=lambda k: (scores[k], -ORDER.index(k)))
    if scores[best] < 3:
        return {"kind": "other", "why": "no tracker kind fits well (the best, %s, finds too few of its columns); "
                "the tracker keeps your columns as they are" % KINDS[best]["label"].lower(), "scores": scores}
    return {"kind": best, "why": "; ".join(why[best]) or "its columns", "scores": scores}


# ------------------------------------------------------------------ the rows
class _Row:
    __slots__ = ("file", "fi", "line", "vals", "flags", "doubtful", "data_row", "sources", "written", "fcol")

    def __init__(self, file: str, fi: int, line: int, vals: Dict[str, str], flags: List[str], doubtful: List[str]):
        self.file, self.fi, self.line, self.vals, self.flags, self.doubtful = file, fi, line, vals, flags, doubtful
        self.data_row = 0
        self.sources: List[Tuple[str, int]] = []
        self.written: Dict[str, str] = {}
        self.fcol: Dict[str, str] = {}            # a flag's text -> the column it is about

    def flag(self, text: str, col: str = "") -> None:
        if text not in self.flags:
            self.flags.append(text)
        if col:
            self.fcol[text] = col


def _status_map(kind: str) -> Dict[str, str]:
    S = KINDS[kind]["status"]
    if not S:
        return {}
    out = {}
    for canon, ws in S["words"].items():
        out[_norm(canon)] = canon
        for w in ws:
            out[_norm(w)] = canon
    return out


def _is_header_row(vals: Sequence[str], heads: Sequence[str]) -> bool:
    filled = [(v, h) for v, h in zip(vals, heads) if str(v).strip()]
    return len(filled) >= 2 and all(_norm(v) == _norm(h) for v, h in filled)


def build(inputs: Sequence[Dict[str, Any]], kind: Optional[str] = None, questions: Any = None,
          today: Optional[str] = None, plan_columns: Any = None, dup_key: Any = None) -> Dict[str, Any]:
    """The tracker for these inputs. inputs: [{name, view}] in order (the first file's copy is kept when a row is in
    two files), each view nl_cleanfile.view(..., detail=True), or {name, error} for a file that could not be cleaned.
    kind: one of KINDS (None: the suggestion). questions: the visitor's, one per line or a list. today: YYYY-MM-DD for
    the formulas' cached values (Excel recalculates TODAY() on opening). dup_key: [tracker column labels] that make two
    rows the same record (None: the cleaner's rule, equal in every column both files hold). Never raises."""
    try:
        return _build(inputs, kind, questions, today, plan_columns, dup_key)
    except Exception as exc:  # noqa: BLE001 - the page shows the reason; nothing half-made is offered
        return {"ok": False, "error": "The tracker could not be built (%s)." % (str(exc)[:200] or type(exc).__name__),
                "workbook": None}


def _questions(q: Any, kind: str) -> List[str]:
    if isinstance(q, str):
        q = [x for x in re.split(r"[\r\n]+", q)]
    out = [" ".join(str(x).split())[:200] for x in (q or []) if isinstance(x, str) and str(x).strip()]
    return out[:MAX_QUESTIONS]


def _build(inputs: Sequence[Dict[str, Any]], kind: Optional[str], questions: Any, today: Optional[str],
           plan_columns: Any, dup_key: Any) -> Dict[str, Any]:
    good = [x for x in inputs if isinstance(x, dict) and (x.get("view") or {}).get("ok") and (x.get("view") or {}).get("detail")]
    if not good:
        return {"ok": False, "error": "No file this tracker can read: run a file first.", "workbook": None}
    sug = suggest(good, plan_columns)
    kind = kind if kind in KINDS else sug["kind"]
    T = KINDS[kind]
    label_of = dict(T["columns"])
    try:
        td = _dt.datetime.strptime(str(today)[:10], "%Y-%m-%d") if today else None
    except ValueError:
        td = None
    td = td or _dt.datetime.combine(_dt.date.today(), _dt.time())
    asked = _questions(questions, kind)
    notes: List[str] = []

    # -- columns: the template's (core always, the others when a file maps them), then every other column by name
    per_file: List[Dict[str, Any]] = []
    roles_seen: set = set()
    extra_cols: List[str] = []
    extra_key: Dict[str, str] = {}
    sources_of: Dict[str, List[Tuple[str, str]]] = {}
    for inp in good:
        d = inp["view"]["detail"]
        heads, kinds = list(d["heads"]), list(d["kinds"])
        m = map_columns(heads, kinds, kind)
        roles_seen |= set(m.values())
        col_of: Dict[str, str] = {}
        for h in heads:
            if h in m:
                col_of[h] = label_of[m[h]]
            else:
                key = _norm(h)
                if key not in extra_key:
                    name = h
                    while name in label_of.values() or name in (SOURCE_COL, LINE_COL, CHECK_COL) or \
                            name in FORMULA_COLS.values() or name in extra_cols:
                        name = name + " (from file)"
                    extra_key[key] = name
                    extra_cols.append(name)
                col_of[h] = extra_key[key]
            sources_of.setdefault(col_of[h], []).append((str(inp["name"]), h))
        per_file.append({"name": str(inp["name"]), "view": inp["view"], "detail": d, "col_of": col_of, "map": m,
                         "kinds": dict(zip(heads, kinds)), "head_of": {v: k for k, v in col_of.items()}})
    tcols = [(r, l) for r, l in T["columns"] if r in T["core"] or r in roles_seen]
    data_cols = [l for _r, l in tcols] + extra_cols
    role_col = {r: l for r, l in tcols}
    ctype: Dict[str, str] = {}
    for r, l in tcols:
        ctype[l] = "date" if r in DATE_ROLES else "number" if r in NUMBER_ROLES else "time" if r in TIME_ROLES else "text"
    for f in per_file:
        for h, c in f["col_of"].items():
            if c in extra_cols:
                k = f["kinds"].get(h, "text")
                k = {"code": "text"}.get(k, k)
                if ctype.get(c, k) != k:
                    k = "text"
                ctype[c] = k
            elif ctype.get(c) == "time" and f["kinds"].get(h) == "date":
                ctype[c] = "datetime"

    # -- rows, with the template's status words and one spelling of a category across files
    smap = _status_map(kind)
    S = T["status"]
    rows: List[_Row] = []
    unplaced: List[Dict[str, Any]] = []
    changes: List[Dict[str, Any]] = []
    merged: List[Dict[str, Any]] = []
    cell_change: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
    for fi, f in enumerate(per_file):
        d = f["detail"]
        heads = list(d["heads"])
        for ch in d.get("changes") or []:
            cell_change[(fi, int(ch["line"]), ch["column"])] = ch
        for rr in d["rows"]:
            vals = list(rr["values"])
            filled = [(h, v) for h, v in zip(heads, vals) if str(v).strip()]
            why = None
            if _is_header_row(vals, heads):
                why = "the column names written again (two lists pasted together): not a record"
            elif len(filled) == 0:
                why = "an empty row: not a record"
            elif len(filled) == 1:
                why = "only one cell filled (%s): a note or a section line, not a record" % filled[0][0]
            written = list(rr.get("written") or vals)
            if why:
                unplaced.append({"file": f["name"], "line": rr["line"], "why": why,
                                 "text": "; ".join("%s: %s" % (h, v) for h, v in zip(heads, written) if str(v).strip())})
                continue
            row = _Row(f["name"], fi, int(rr["line"]), {}, [], list(rr.get("doubtful") or []))
            for h, v, w in zip(heads, vals, written):
                row.vals[f["col_of"][h]] = v
                row.written[f["col_of"][h]] = w
            for fl in rr.get("flags") or []:
                h = next((x for x in sorted(heads, key=len, reverse=True) if fl.startswith(x + ":")), None)
                row.flag(fl, f["col_of"].get(h, "") if h else "")
            row.doubtful = [f["col_of"].get(h, h) for h in row.doubtful]
            row.sources = [(f["name"], row.line)]
            rows.append(row)
        for mg in d.get("merged") or []:
            merged.append({"rule": "within %s: %s" % (f["name"], mg["rule"]), "kept_file": f["name"],
                           "kept_line": int(mg["into"]), "file": f["name"], "line": int(mg["line"]),
                           "values": "; ".join("%s: %s" % (h, v) for h, v in zip(heads, mg["values"]) if str(v).strip()),
                           "differed": ""})
    for f in [x for x in inputs if isinstance(x, dict) and x not in good]:
        unplaced.append({"file": str(f.get("name") or "a file"), "line": None, "text": "",
                         "why": "the whole file: %s" % (str(f.get("error") or (f.get("view") or {}).get("error") or
                                                            "it could not be cleaned in its own shape"))[:300]})

    st_col = role_col.get("status")
    unknown_status: Dict[str, int] = {}
    if st_col and S:
        for row in rows:
            v = row.vals.get(st_col, "")
            if not v.strip() or st_col in row.doubtful:
                continue
            c = smap.get(_norm(v))
            if c is None:
                unknown_status[v] = unknown_status.get(v, 0) + 1
                row.flag("%s: %s is not one of the tracker's status words; kept as written and added to the "
                         "dropdown list" % (st_col, "“%s”" % v), st_col)
            elif c != v:
                row.vals[st_col] = c
                _note_change(changes, cell_change, row, st_col, per_file[row.fi]["head_of"].get(st_col), v, c,
                             "tracker_status_words")
    cat_col = role_col.get("category")
    cats: Dict[str, int] = {}
    if cat_col:
        groups: Dict[str, Dict[str, int]] = {}
        order: List[str] = []
        for row in rows:
            v = row.vals.get(cat_col, "")
            if v.strip() and cat_col not in row.doubtful:
                g = groups.setdefault(v.lower(), {})
                if v.lower() not in order:
                    order.append(v.lower())
                g[v] = g.get(v, 0) + 1
        one = {k: max(g.items(), key=lambda kv: (kv[1], -list(g).index(kv[0])))[0] for k, g in groups.items()}
        for row in rows:
            v = row.vals.get(cat_col, "")
            if v.strip() and cat_col not in row.doubtful and one[v.lower()] != v:
                row.vals[cat_col] = one[v.lower()]
                _note_change(changes, cell_change, row, cat_col, per_file[row.fi]["head_of"].get(cat_col), v,
                             one[v.lower()], "tracker_one_spelling")
        for row in rows:
            v = row.vals.get(cat_col, "")
            if v.strip():
                cats[v] = cats.get(v, 0) + 1

    # -- the same record in two files: merged into the first file's copy (the cleaner's rule, across files)
    key_cols = [c for c in (dup_key or []) if c in data_cols] if isinstance(dup_key, list) else []
    id_col = role_col.get("id")
    cols_of_file = [set(f["col_of"].values()) for f in per_file]
    kept: List[_Row] = []
    first_by_key: Dict[Tuple[str, ...], _Row] = {}
    by_id: Dict[str, List[_Row]] = {}
    for row in rows:
        target = None
        if key_cols:
            k = tuple(row.vals.get(c, "").strip().lower() for c in key_cols)
            if any(k):
                target = first_by_key.get(k)
                if target is None:
                    first_by_key[k] = row
        else:
            idv = row.vals.get(id_col, "").strip().lower() if id_col else ""
            for other in (by_id.get(idv, []) if idv else kept):
                if other.fi == row.fi:
                    continue
                shared = cols_of_file[other.fi] & cols_of_file[row.fi]
                only_mine = cols_of_file[row.fi] - cols_of_file[other.fi]
                if all(other.vals.get(c, "") == row.vals.get(c, "") for c in shared) and \
                        not any(row.vals.get(c, "").strip() for c in only_mine):
                    target = other
                    break
        if target is not None:
            differed = [c for c in data_cols if c in cols_of_file[row.fi] and c in cols_of_file[target.fi]
                        and target.vals.get(c, "") != row.vals.get(c, "")]
            merged.append({"rule": ("across files: the same %s" % ", ".join(key_cols)) if key_cols else
                           "across files: equal in every column both files hold, once cleaned",
                           "kept_file": target.file, "kept_line": target.line, "file": row.file, "line": row.line,
                           "values": "; ".join("%s: %s" % (h, row.written.get(c, "")) for h, c in
                                               per_file[row.fi]["col_of"].items() if row.written.get(c, "").strip()),
                           "differed": ", ".join(differed), "_target": target})
            target.sources.append((row.file, row.line))
            for fl in row.flags:
                target.flag(fl, row.fcol.get(fl, ""))
            continue
        kept.append(row)
        if id_col and row.vals.get(id_col, "").strip():
            by_id.setdefault(row.vals[id_col].strip().lower(), []).append(row)
    rows = kept
    if not key_cols and id_col:
        for idv, rs in by_id.items():
            if len(rs) < 2:
                continue
            for r in rs:
                others = ["%s line %s" % (o.file, o.line) for o in rs if o is not r]
                diff = sorted(set(c for o in rs if o is not r for c in data_cols
                                  if o.vals.get(c, "") != r.vals.get(c, "") and c in cols_of_file[o.fi] & cols_of_file[r.fi]))
                r.flag("possible duplicate: the same %s as %s; not merged (differs in %s)"
                       % (id_col, ", ".join(others), ", ".join(diff) or "a column only one file holds"), id_col)

    # -- checks the tracker makes (never fixes): stated in Flags
    open_set = set(S["canon"]) - set(S["closed"]) if S else set()
    cl, op, du = role_col.get("closed"), role_col.get("opened"), role_col.get("due")
    for row in rows:
        stv = row.vals.get(st_col, "") if st_col else ""
        if cl and S and S.get("done") and stv == S["done"] and not row.vals.get(cl, "").strip():
            row.flag("%s is %s but %s is blank; left blank" % (st_col, S["done"], cl), cl)
        if cl and stv in open_set and row.vals.get(cl, "").strip():
            row.flag("%s has a date but %s is %s" % (cl, st_col, stv), cl)
        if cl and op:
            a, b = _as_date(row.vals.get(op, "")), _as_date(row.vals.get(cl, ""))
            if a and b and b < a:
                row.flag("%s (%s) is before %s (%s); both kept as written" % (cl, row.vals[cl], op, row.vals[op]), cl)
        if du and op:
            a, b = _as_date(row.vals.get(op, "")), _as_date(row.vals.get(du, ""))
            if a and b and b < a:
                row.flag("%s (%s) is before %s (%s); both kept as written" % (du, row.vals[du], op, row.vals[op]), du)

    for i, row in enumerate(rows):
        row.data_row = i + 2

    # -- blanks: every blank cell of a column the row's file holds, except one blank by design (Closed while open)
    flags: List[Dict[str, Any]] = []
    for row in rows:
        mine = cols_of_file[row.fi]
        for fl in row.flags:
            col = row.fcol.get(fl, "")
            flags.append({"file": row.file, "line": row.line, "row": row.data_row, "column": col,
                          "value": row.written.get(col, "") if col else "", "why": fl[:MAX_FLAG_TEXT]})
        blanks = []
        for c in data_cols:
            if c not in mine or row.vals.get(c, "").strip():
                continue
            if c == cl and (row.vals.get(st_col, "") in open_set or not row.vals.get(st_col, "").strip()):
                continue
            blanks.append(c)
        if blanks:
            flags.append({"file": row.file, "line": row.line, "row": row.data_row, "column": "; ".join(blanks),
                          "value": "", "why": "blank, left empty: nothing was filled in"})
        row.flags.append("blank: %s" % ", ".join(blanks)) if blanks else None

    # -- changes: the tracker's (on a row placed in Data) and the cleaner's (every other changed cell of a placed
    # row), one line per cell
    placed = set(id(r) for r in rows)
    changes = [c for c in changes if id(c["_row"]) in placed]
    for row in rows:
        f = per_file[row.fi]
        for c in data_cols:
            h = f["head_of"].get(c)
            ch = cell_change.get((row.fi, row.line, h)) if h is not None else None
            if ch is None or ch.get("_done"):
                continue
            changes.append({"file": row.file, "line": row.line, "_row": row, "column": c,
                            "before": ch["before"], "after": row.vals.get(c, ""), "rule": ch["rule"]})
    for c in changes:
        c["row"] = c.pop("_row").data_row
    changes = [c for c in changes if c["before"] != c["after"]]
    changes.sort(key=lambda c: (c["row"], data_cols.index(c["column"]) if c["column"] in data_cols else 99))
    for m in merged:
        t = m.pop("_target", None)
        m["kept_row"] = t.data_row if t is not None else next(
            (r.data_row for r in rows if r.file == m["kept_file"] and r.line == m["kept_line"]), None)

    wb, answers = _workbook(kind, T, tcols, data_cols, extra_cols, ctype, role_col, rows, per_file, sources_of, unplaced,
                            changes, merged, flags, unknown_status, cats, asked, td, key_cols, sug, notes)
    files = []
    for f in per_file:
        S_ = f["view"].get("summary") or {}
        files.append({"name": f["name"], "rows_in": int(S_.get("rows_in", 0)),
                      "merged_within": int(S_.get("merged", 0)),
                      "placed": sum(1 for r in rows if r.file == f["name"]),
                      "merged_across": sum(1 for m in merged if m["file"] == f["name"] and m["rule"].startswith("across")),
                      "unplaced": sum(1 for u in unplaced if u["file"] == f["name"] and u["line"] is not None),
                      "withheld_left_out": int(S_.get("withheld_left_out", 0))})
    summary = {"rows": len(rows), "files": files, "changes": len(changes),
               "merged_within": sum(1 for m in merged if m["rule"].startswith("within")),
               "merged_across": sum(1 for m in merged if m["rule"].startswith("across")),
               "unplaced": len(unplaced), "flags": len(flags), "flagged_rows": sum(1 for r in rows if r.flags),
               "columns": data_cols, "unmapped": extra_cols, "formula_columns": [FORMULA_COLS[k] for k in T["formulas"] if _formula_ok(k, role_col)],
               "unknown_status": unknown_status}
    return {"ok": True, "error": None, "version": VERSION, "kind": kind, "kind_label": T["label"], "suggest": sug,
            "kinds": [{"id": k, "label": KINDS[k]["label"]} for k in ORDER],
            "mapping": [{"column": c, "role": next((r for r, l in tcols if l == c), None),
                         "sources": [{"file": a, "header": b} for a, b in sources_of.get(c, [])]} for c in data_cols],
            "questions": answers, "summary": summary, "notes": notes, "workbook": wb, "today": td.strftime("%Y-%m-%d"),
            "dup_key": key_cols}


def _note_change(changes: List[Dict[str, Any]], cell_change: Dict[Tuple[int, int, str], Dict[str, Any]], row: _Row,
                 col: str, head: Optional[str], before: str, after: str, rule: str) -> None:
    """A tracker change to a cell; when the cleaner changed the same cell first, ONE line: the file's text before,
    the tracker's value after, both rules."""
    ch = cell_change.get((row.fi, row.line, head)) if head is not None else None
    if ch is not None and not ch.get("_done"):
        ch["_done"] = True
        before, rule = ch["before"], "%s, then %s" % (ch["rule"], rule)
    changes.append({"file": row.file, "line": row.line, "_row": row, "column": col, "before": before, "after": after,
                    "rule": rule})


def _formula_ok(k: str, role_col: Dict[str, str]) -> bool:
    need = {"days_open": ["opened"], "is_open": ["status"], "overdue": ["status", "due"], "open_key": ["opened", "status"],
            "stock_value": ["qty", "unit_cost"], "reorder_flag": ["qty", "reorder"], "hours": ["start", "end"],
            "vendor_total": ["vendor", "amount"]}[k]
    return all(r in role_col for r in need)


# ------------------------------------------------------------------ the workbook (a spec for window.NLX.workbook)
def _B(v: Any) -> Dict[str, Any]:
    return {"v": v, "b": True}


def _workbook(kind: str, T: Dict[str, Any], tcols, data_cols: List[str], extra_cols: List[str], ctype: Dict[str, str],
              role_col: Dict[str, str], rows: List[_Row], per_file, sources_of, unplaced, changes, merged, flags,
              unknown_status: Dict[str, int], cats: Dict[str, int], asked: List[str], td: _dt.datetime,
              key_cols: List[str], sug: Dict[str, Any], notes: List[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    S = T["status"]
    fcols = [k for k in T["formulas"] if _formula_ok(k, role_col)]
    flabels = [FORMULA_COLS[k] for k in fcols]
    tmpl = [l for _r, l in tcols]
    heads = tmpl + flabels + extra_cols + [SOURCE_COL, LINE_COL, CHECK_COL]
    ix = {h: i for i, h in enumerate(heads)}
    L = {h: _col(i) for h, i in ix.items()}
    n = len(rows)
    last = n + 1 if n else 2
    today_serial = _serial(td)

    # Lists: the status words (the template's, then any word the files use that it does not know), the statuses that
    # count as closed, the categories the files use
    st_list = (list(S["canon"]) + sorted(unknown_status, key=lambda v: (-unknown_status[v], v))) if S and role_col.get("status") else []
    closed_list = list(S["closed"]) if S else []
    cat_list = sorted(cats, key=lambda v: (-cats[v], v)) if role_col.get("category") else []
    lists_cols: List[Tuple[str, List[str]]] = []
    if st_list:
        lists_cols.append(("Status", st_list))
        lists_cols.append(("Statuses that count as closed", closed_list))
    if role_col.get("category"):
        lists_cols.append((role_col["category"], cat_list or [""]))
    lists_at: Dict[str, Tuple[int, int, int]] = {}
    lists_rows: List[List[Any]] = [[_B(h) for h, _v in lists_cols]]
    for j, (h, vals) in enumerate(lists_cols):
        lists_at[h] = (j, 2, 1 + max(1, len(vals)))
        for i, v in enumerate(vals):
            while len(lists_rows) <= i + 1:
                lists_rows.append([])
            r = lists_rows[i + 1]
            while len(r) < j:
                r.append(None)
            r.append(v)
    closed_ref = None
    if st_list:
        c0, r0, r1 = lists_at["Statuses that count as closed"]
        closed_ref = "Lists!$%s$%d:$%s$%d" % (_col(c0), r0, _col(c0), r1)

    # -- Data: one row per record, typed; formula columns with their cached values
    sc, cl, op, du = role_col.get("status"), role_col.get("closed"), role_col.get("opened"), role_col.get("due")
    data_rows: List[List[Any]] = [[_B(h) for h in heads]]
    calc: Dict[str, str] = {}
    fdefs: Dict[str, Tuple[str, str]] = {}
    if "days_open" in fcols:
        O, C = _sref(op), _sref(cl) if cl else '""'
        fdefs["days_open"] = ('IF(ISNUMBER(%s),IF(ISNUMBER(%s),%s-%s,IF(%s="",TODAY()-%s,"")),"")' % (O, C, C, O, C, O)
                              if cl else 'IF(ISNUMBER(%s),TODAY()-%s,"")' % (O, O), "num")
    if "is_open" in fcols:
        fdefs["is_open"] = ('IF(%s="","",IF(COUNTIF(%s,%s)>0,"No","Yes"))' % (_sref(sc), closed_ref, _sref(sc)), None)
    if "overdue" in fcols:
        IO, D = _sref(FORMULA_COLS["is_open"]), _sref(du)
        fdefs["overdue"] = ('IF(AND(%s="Yes",ISNUMBER(%s)),IF(%s<TODAY(),"Yes","No"),"")' % (IO, D, D), None)
    if "open_key" in fcols:
        IO, DO = _sref(FORMULA_COLS["is_open"]), _sref(FORMULA_COLS["days_open"])
        fdefs["open_key"] = ('IF(AND(%s="Yes",ISNUMBER(%s)),%s+ROW()/1000000,"")' % (IO, DO, DO), "num")
    if "stock_value" in fcols:
        Q, U = _sref(role_col["qty"]), _sref(role_col["unit_cost"])
        fdefs["stock_value"] = ('IF(AND(ISNUMBER(%s),ISNUMBER(%s)),%s*%s,"")' % (Q, U, Q, U), "num2")
    if "reorder_flag" in fcols:
        Q, R = _sref(role_col["qty"]), _sref(role_col["reorder"])
        fdefs["reorder_flag"] = ('IF(AND(ISNUMBER(%s),ISNUMBER(%s)),IF(%s<=%s,"Yes","No"),"")' % (Q, R, Q, R), None)
    if "hours" in fcols:
        A_, B_ = _sref(role_col["start"]), _sref(role_col["end"])
        fdefs["hours"] = ('IF(AND(ISNUMBER(%s),ISNUMBER(%s)),ROUND(MOD(%s-%s,1)*24,2),"")' % (A_, B_, B_, A_), "num2")
    if "vendor_total" in fcols:
        Vn, Am = role_col["vendor"], role_col["amount"]
        fdefs["vendor_total"] = ('IF(%s="","",SUMIFS(%s[%s],%s[%s],%s))' % (_sref(Vn), TABLE, Am, TABLE, Vn, _sref(Vn)), "num2")
    for k in fcols:
        calc[FORMULA_COLS[k]] = fdefs[k][0]

    # the cached values, computed here from the same rows by the same rule as each formula
    num_of: Dict[Tuple[int, str], Optional[float]] = {}

    def cellnum(row: _Row, c: str) -> Optional[float]:
        key = (id(row), c)
        if key in num_of:
            return num_of[key]
        v = row.vals.get(c, "")
        t = ctype.get(c, "text")
        x: Optional[float] = None
        if c in row.doubtful or not v.strip():
            x = None
        elif t in ("date", "datetime"):
            d = _as_date(v)
            x = _serial(d) if d else None
        elif t == "time":
            x = _as_time(v)
            if x is None:
                d = _as_date(v)
                x = _serial(d) if d else None
        elif t == "number":
            x = _as_number(v)
        num_of[key] = x
        return x

    vendor_sum: Dict[str, float] = {}
    if "vendor_total" in fcols:
        for row in rows:
            a = cellnum(row, role_col["amount"])
            if a is not None:
                vendor_sum[row.vals.get(role_col["vendor"], "")] = vendor_sum.get(row.vals.get(role_col["vendor"], ""), 0.0) + a
    cache: Dict[Tuple[int, str], Any] = {}
    for row in rows:
        c: Dict[str, Any] = {}
        if "days_open" in fcols:
            o = cellnum(row, op)
            x = cellnum(row, cl) if cl else None
            cv = row.vals.get(cl, "") if cl else ""
            c["days_open"] = "" if o is None else (x - o if x is not None else (today_serial - o if cv == "" else ""))
        if "is_open" in fcols:
            s = row.vals.get(sc, "")
            c["is_open"] = "" if s == "" else ("No" if s in closed_list else "Yes")
        if "overdue" in fcols:
            d = cellnum(row, du)
            c["overdue"] = ("Yes" if d < today_serial else "No") if c["is_open"] == "Yes" and d is not None else ""
        if "open_key" in fcols:
            c["open_key"] = (c["days_open"] + row.data_row / 1000000.0) if c["is_open"] == "Yes" and isinstance(c["days_open"], float) else ""
        if "stock_value" in fcols:
            q, u = cellnum(row, role_col["qty"]), cellnum(row, role_col["unit_cost"])
            c["stock_value"] = q * u if q is not None and u is not None else ""
        if "reorder_flag" in fcols:
            q, r = cellnum(row, role_col["qty"]), cellnum(row, role_col["reorder"])
            c["reorder_flag"] = ("Yes" if q <= r else "No") if q is not None and r is not None else ""
        if "hours" in fcols:
            a, b = cellnum(row, role_col["start"]), cellnum(row, role_col["end"])
            c["hours"] = round(((b - a) % 1) * 24, 2) if a is not None and b is not None else ""
        if "vendor_total" in fcols:
            vn = row.vals.get(role_col["vendor"], "")
            c["vendor_total"] = "" if vn == "" else vendor_sum.get(vn, 0.0)
        for k, v in c.items():
            cache[(row.data_row, k)] = v
        out: List[Any] = []
        for h in heads:
            if h in tmpl or h in extra_cols:
                v = row.vals.get(h, "")
                t = ctype.get(h, "text")
                if v == "" or h in row.doubtful:
                    out.append(v if v != "" else None)
                elif t in ("date", "datetime", "time") and _as_date(v):
                    out.append({"v": v, "fmt": "date" if _ISO_D.match(v) else "datetime"})
                elif t == "time" and _as_time(v) is not None:
                    out.append({"v": _as_time(v), "fmt": "hh:mm"})
                elif t == "number" and _as_number(v) is not None:
                    out.append(_as_number(v))
                else:
                    out.append(v)
            elif h == SOURCE_COL:
                out.append(row.file + ("" if len(row.sources) < 2 else " (also in %s)" % ", ".join(
                    sorted(set("%s line %s" % s for s in row.sources[1:])))))
            elif h == LINE_COL:
                out.append(row.line)
            elif h == CHECK_COL:
                out.append(" | ".join(row.flags)[:MAX_FLAG_TEXT * 3] or None)
            else:
                k = next(k for k in fcols if FORMULA_COLS[k] == h)
                v = c[k]
                cell = {"f": fdefs[k][0], "v": v}
                if fdefs[k][1] and v != "":
                    cell["fmt"] = fdefs[k][1]
                out.append(cell)
        data_rows.append(out)
    if not rows:
        data_rows.append([None] * len(heads))
    widths = []
    for h in heads:
        w = max([len(h)] + [len(str(r.vals.get(h, ""))) for r in rows[:300]]) + 2
        widths.append(max(8, min(w, 48 if h != CHECK_COL else 70)))
    validations = []
    if sc and st_list:
        c0, r0, r1 = lists_at["Status"]
        validations.append({"range": "%s2:%s%d" % (L[sc], L[sc], last + EXTRA_ROWS), "list": {"sheet": "Lists", "col": c0, "r0": r0, "r1": r1}})
    if role_col.get("category") and cat_list:
        c0, r0, r1 = lists_at[role_col["category"]]
        cc = role_col["category"]
        validations.append({"range": "%s2:%s%d" % (L[cc], L[cc], last + EXTRA_ROWS), "list": {"sheet": "Lists", "col": c0, "r0": r0, "r1": r1}})
    data_sheet = {"name": "Data", "freeze": 1, "widths": widths, "rows": data_rows, "validations": validations,
                  "tables": [{"name": TABLE, "ref": "A1:%s%d" % (_col(len(heads) - 1), last), "calc": calc}]}

    # -- Summary: formulas over whole Data columns (a row a visitor adds is counted), each with its cached value
    D = lambda h: "Data!$%s:$%s" % (L[h], L[h])                                    # noqa: E731
    ttl = role_col.get(T["title_role"]) or (tmpl[0] if tmpl else heads[0])
    sm: List[List[Any]] = []
    answers: List[Dict[str, Any]] = []
    vals = lambda h: [r.vals.get(h, "") for r in rows]                              # noqa: E731
    count_rows = sum(1 for r in rows if r.vals.get(ttl, "").strip())

    def put(label: str, f: str, v: Any, fmt: Optional[str] = None, how: str = "") -> str:
        cell = {"f": f, "v": v}
        if fmt and v != "":
            cell["fmt"] = fmt
        sm.append([label, cell, how])
        return "B%d" % len(sm)

    sm.append([_B("%s tracker: summary" % T["label"]), None, None])
    sm.append(["Every number here is a formula over the Data sheet; add or edit a row there and these update. "
               "Today's date comes from TODAY(), so day counts move each day.", None, None])
    sm.append(["Today", {"f": "TODAY()", "v": today_serial, "fmt": "date"}, "TODAY()"])
    sm.append([None, None, None])
    q_at = len(sm)
    sm.append([_B("Your questions"), _B("Answer"), _B("How it is worked out")])
    for _ in asked or T["questions"]:
        sm.append([None, None, None])
    sm.append([None, None, None])
    sm.append([_B("Totals"), None, None])
    cells: Dict[str, str] = {}
    cells["rows"] = put("%s in the tracker (rows with a %s)" % (T["noun"].capitalize() + "s", ttl.lower()),
                        'COUNTIFS(%s,"<>")-1' % D(ttl), count_rows, "int",
                        "rows with a %s, not counting the header" % ttl.lower())
    io_ = FORMULA_COLS["is_open"]
    if "is_open" in fcols:
        n_open = sum(1 for r in rows if cache[(r.data_row, "is_open")] == "Yes")
        cells["open"] = put("Open (status not in the closed list)", 'COUNTIFS(%s,"Yes")' % D(io_), n_open, "int",
                            "%s is Yes: a status not listed under “Statuses that count as closed” on Lists" % io_)
    if "overdue" in fcols:
        n_over = sum(1 for r in rows if cache[(r.data_row, "overdue")] == "Yes")
        cells["overdue"] = put("Overdue (open, due before today)", 'COUNTIFS(%s,"Yes")' % D(FORMULA_COLS["overdue"]), n_over,
                               "int", "Overdue is Yes: open and %s before today" % du)
    if sc and S and S.get("done"):
        n_done = sum(1 for v in vals(sc) if v == S["done"])
        cells["done"] = put("%s" % S["done"], 'COUNTIFS(%s,%s)' % (D(sc), _xstr(S["done"])), n_done, "int",
                            "%s is %s" % (sc, S["done"]))
    if "days_open" in fcols and "is_open" in fcols:
        xs = [cache[(r.data_row, "days_open")] for r in rows if cache[(r.data_row, "is_open")] == "No"
              and isinstance(cache[(r.data_row, "days_open")], float)]
        cells["avg_closed"] = put("Average days from opened to closed (closed items)",
                                  'IFERROR(AVERAGEIFS(%s,%s,"No"),"")' % (D(FORMULA_COLS["days_open"]), D(io_)),
                                  (sum(xs) / len(xs)) if xs else "", "num",
                                  "the mean of Days open over rows that are not open; blank when none")
        xo = [cache[(r.data_row, "days_open")] for r in rows if cache[(r.data_row, "is_open")] == "Yes"
              and isinstance(cache[(r.data_row, "days_open")], float)]
        cells["avg_open"] = put("Average days open so far (open items)",
                                'IFERROR(AVERAGEIFS(%s,%s,"Yes"),"")' % (D(FORMULA_COLS["days_open"]), D(io_)),
                                (sum(xo) / len(xo)) if xo else "", "num", "the mean of Days open over open rows")
    if "stock_value" in fcols:
        tot = sum(cache[(r.data_row, "stock_value")] for r in rows if isinstance(cache[(r.data_row, "stock_value")], float))
        cells["stock_value"] = put("Stock value (Qty x Unit cost, summed)", "SUM(%s)" % D(FORMULA_COLS["stock_value"]),
                                   tot, "num2", "the sum of Stock value")
    if "reorder_flag" in fcols:
        cells["reorder"] = put("Items at or below their reorder level", 'COUNTIFS(%s,"Yes")' % D(FORMULA_COLS["reorder_flag"]),
                               sum(1 for r in rows if cache[(r.data_row, "reorder_flag")] == "Yes"), "int", "Reorder is Yes")
    if "hours" in fcols:
        hs = sum(cache[(r.data_row, "hours")] for r in rows if isinstance(cache[(r.data_row, "hours")], float))
        cells["hours"] = put("Hours (summed)", "SUM(%s)" % D(FORMULA_COLS["hours"]), hs, "num2", "the sum of Hours")
    if role_col.get("amount"):
        am = role_col["amount"]
        tot = sum(x for x in (_as_number(r.vals.get(am, "")) if am not in r.doubtful else None for r in rows) if x is not None)
        cells["amount"] = put("%s (summed)" % am, "SUM(%s)" % D(am), tot, "num2", "the sum of %s" % am)
        if sc and S and S.get("done"):
            unpaid = sum(x for x, s in ((_as_number(r.vals.get(am, "")) if am not in r.doubtful else None, r.vals.get(sc, ""))
                                         for r in rows) if x is not None and s not in S["closed"] and s != "")
            cells["unpaid"] = put("%s not yet %s (open statuses)" % (am, S["done"].lower()),
                                  'SUMPRODUCT(SUMIFS(%s,%s,Lists!$A$2:$A$%d))-SUMPRODUCT(SUMIFS(%s,%s,%s))'
                                  % (D(am), D(sc), lists_at["Status"][2], D(am), D(sc), closed_ref),
                                  unpaid, "num2", "the sum of %s over the open statuses on Lists" % am)
    groups: List[Tuple[str, str, List[str]]] = []
    if sc and st_list:
        groups.append(("status", sc, st_list))
    if role_col.get("category"):
        groups.append(("category", role_col["category"], cat_list))
    if role_col.get("owner"):
        ow = role_col["owner"]
        seen: Dict[str, int] = {}
        for v in vals(ow):
            if v.strip():
                seen[v] = seen.get(v, 0) + 1
        groups.append(("owner", ow, sorted(seen, key=lambda v: (-seen[v], v))))
    if role_col.get("vendor") and kind == "vendors":
        vv: Dict[str, int] = {}
        for v in vals(role_col["vendor"]):
            if v.strip():
                vv[v] = vv.get(v, 0) + 1
        groups.append(("vendor", role_col["vendor"], sorted(vv, key=lambda v: (-vv[v], v))))
    group_at: Dict[str, int] = {}
    for gk, gc, members in groups:
        sm.append([None, None, None])
        extra = []
        if "is_open" in fcols:
            extra.append(("Open", io_, "Yes"))
        if "overdue" in fcols:
            extra.append(("Overdue", FORMULA_COLS["overdue"], "Yes"))
        sums = []
        if kind == "vendors" and role_col.get("amount"):
            sums.append(role_col["amount"])
        if kind == "inventory" and "stock_value" in fcols:
            sums.append(FORMULA_COLS["stock_value"])
        if kind == "shifts" and "hours" in fcols:
            sums.append(FORMULA_COLS["hours"])
        hrow = [_B("By %s" % gc.lower()), _B("Rows")] + [_B(e[0]) for e in extra] + [_B(s) for s in sums]
        sm.append(hrow)
        group_at[gk] = len(sm)
        for mval in list(members) + [""]:
            label = mval if mval != "" else "(blank)"
            cond = '%s,%s' % (D(gc), _xstr(mval)) if mval != "" else '%s,"<>",%s,""' % (D(ttl), D(gc))
            mrows = [r for r in rows if r.vals.get(gc, "") == mval and (mval != "" or r.vals.get(ttl, "").strip())]
            line: List[Any] = [label, {"f": "COUNTIFS(%s)" % cond, "v": len(mrows), "fmt": "int"}]
            for _lab, ec, ev in extra:
                key = next(k for k in fcols if FORMULA_COLS[k] == ec)
                line.append({"f": "COUNTIFS(%s,%s,%s)" % (cond, D(ec), _xstr(ev)),
                             "v": sum(1 for r in mrows if cache[(r.data_row, key)] == ev), "fmt": "int"})
            for s in sums:
                if s in FORMULA_COLS.values():
                    key = next(k for k in fcols if FORMULA_COLS[k] == s)
                    tot = sum(cache[(r.data_row, key)] for r in mrows if isinstance(cache[(r.data_row, key)], float))
                else:
                    tot = sum(x for x in (_as_number(r.vals.get(s, "")) if s not in r.doubtful else None for r in mrows) if x is not None)
                line.append({"f": "SUMIFS(%s,%s)" % (D(s), cond), "v": tot, "fmt": "num2"})
            if mval == "" and not mrows:
                continue
            sm.append(line)
    oldest_at = None
    if "open_key" in fcols:
        sm.append([None, None, None])
        sm.append([_B("Oldest open %ss" % T["noun"]), _B("Days open"), _B(ttl)] + ([_B(role_col["id"])] if role_col.get("id") else []))
        oldest_at = len(sm) + 1
        keys = sorted([cache[(r.data_row, "open_key")] for r in rows if isinstance(cache[(r.data_row, "open_key")], float)], reverse=True)
        by_key = {cache[(r.data_row, "open_key")]: r for r in rows if isinstance(cache[(r.data_row, "open_key")], float)}
        K = D(FORMULA_COLS["open_key"])
        for k in range(1, 6):
            kv = keys[k - 1] if k <= len(keys) else None
            r = by_key.get(kv) if kv is not None else None
            line = [k, {"f": 'IFERROR(INT(LARGE(%s,%d)),"")' % (K, k), "v": float(int(kv)) if kv is not None else "", "fmt": "int"},
                    {"f": 'IFERROR(INDEX(%s,MATCH(LARGE(%s,%d),%s,0)),"")' % (D(ttl), K, k, K), "v": r.vals.get(ttl, "") if r else ""}]
            if role_col.get("id"):
                line.append({"f": 'IFERROR(INDEX(%s,MATCH(LARGE(%s,%d),%s,0)),"")' % (D(role_col["id"]), K, k, K),
                             "v": r.vals.get(role_col["id"], "") if r else ""})
            sm.append(line)

    # the visitor's questions (or the kind's), each answered by a formula cell above, or said plainly when it cannot be
    for i, q in enumerate(asked or T["questions"]):
        a = _answer(q, cells, group_at, oldest_at, role_col, fcols, T, sm)
        answers.append(dict(a, question=q))
        r = q_at + 1 + i
        if a["cell"]:
            ref = a["cell"]
            src = sm[int(re.sub(r"\D", "", ref)) - 1]
            val = src[1 if ref.startswith("B") else 2]
            v = val.get("v") if isinstance(val, dict) else val
            sm[r - 1] = [q, {"f": ref, "v": v, **({"fmt": val["fmt"]} if isinstance(val, dict) and val.get("fmt") else {})}, a["how"]]
        else:
            sm[r - 1] = [q, a["text"], a["how"]]
    summary_sheet = {"name": "Summary", "widths": [52, 16, 16, 16, 16], "rows": sm}

    # -- the record sheets
    def fl(f, ln):
        return f if ln is None else "%s line %s" % (f, ln)
    ch_rows = [[_B("Source file"), _B("Line in source"), _B("Data row"), _B("Column"), _B("Before (as written)"),
                _B("After (in the tracker)"), _B("Rule")]]
    for c in changes:
        ch_rows.append([c["file"], c["line"], c["row"], c["column"], c["before"], c["after"], c["rule"]])
    mg_rows = [[_B("Rule"), _B("Kept: file and line"), _B("Kept: Data row"), _B("Merged: file and line"),
                _B("Merged row (as written)"), _B("Differed in")]]
    for m in merged:
        mg_rows.append([m["rule"], fl(m["kept_file"], m["kept_line"]), m.get("kept_row"), fl(m["file"], m["line"]),
                        m["values"], m["differed"] or None])
    un_rows = [[_B("Source file"), _B("Line in source"), _B("What it holds"), _B("Why it was not placed")]]
    for u in unplaced:
        un_rows.append([u["file"], u["line"], u["text"] or None, u["why"]])
    fg_rows = [[_B("Source file"), _B("Line in source"), _B("Data row"), _B("Column"), _B("Value (as written)"), _B("Why")]]
    for f in flags:
        fg_rows.append([f["file"], f["line"], f["row"], f["column"] or None, f["value"] or None, f["why"]])
    if len(ch_rows) == 1:
        ch_rows.append(["No cell was changed.", None, None, None, None, None, None])
    if len(mg_rows) == 1:
        mg_rows.append(["No duplicates were merged.", None, None, None, None, None])
    if len(un_rows) == 1:
        un_rows.append(["Every row was placed.", None, None, None])
    if len(fg_rows) == 1:
        fg_rows.append(["Nothing was flagged.", None, None, None, None, None])

    # -- Instructions
    ins: List[List[Any]] = []
    A = lambda *x: ins.append(list(x))                                              # noqa: E731
    A(_B("How this tracker works"))
    A("A %s tracker made in your browser by NorthLedger from %s. Nothing was uploaded. Rows were cleaned by the "
      "engine's cleaner (the same one as “Clean my file”); nothing was invented and no blank was filled in."
      % (T["label"].lower(), ", ".join(f["name"] for f in per_file)))
    A("Kind: %s (%s). You can change the kind on the page and download again." % (T["label"], sug.get("why") or "chosen by you"))
    A(None)
    A(_B("To add a row"))
    A("Type in the first empty row right under the table on Data. The table grows by itself, %s and the dropdowns "
      "reach the new row." % (("the formula columns (%s) fill in," % ", ".join(flabels)) if flabels else "its formatting follows,"))
    if sc and st_list:
        A("Status and %s have dropdowns. Their lists are on the Lists sheet: to add a value, insert a cell inside "
          "the list (above its last value) and type it, so the dropdown's range grows with it." % (role_col.get("category") or "category").lower())
        A("“Statuses that count as closed” on Lists decides what is open: a status not in that list counts as open.")
    A(None)
    A(_B("Columns"), _B("What it holds"), _B("From your files"), _B("Type"))
    role_words = {"id": "the record's own reference", "title": "what the %s is" % T["noun"], "status": "where it stands (dropdown)",
                  "category": "its group (dropdown)", "priority": "how urgent", "owner": "who handles it",
                  "requester": "who asked for it", "opened": "the date it was opened", "due": "the date it is due",
                  "closed": "the date it was closed", "location": "where it is", "qty": "how many are on hand",
                  "unit_cost": "the cost of one", "reorder": "the level at which to reorder", "person": "who works the shift",
                  "date": "the date", "start": "the start time", "end": "the end time", "vendor": "who is paid",
                  "amount": "the amount"}
    for r, l in tcols:
        src = "; ".join("%s: %s" % (a, b) for a, b in sources_of.get(l, [])) or "none of your files has it: added empty by the tracker"
        A(l, role_words.get(r, ""), src, ctype.get(l, "text"))
    fwords = {"days_open": "days from Opened to Closed; while Closed is blank, days from Opened to today",
              "is_open": "Yes when the status is not in the closed list on Lists",
              "overdue": "Yes when open and Due is before today",
              "open_key": "Days open plus a tiny row number, for the Summary's oldest open list (ties never collide)",
              "stock_value": "Qty x Unit cost", "reorder_flag": "Yes when Qty is at or below the reorder level",
              "hours": "End minus Start in hours (a shift past midnight counts the hours to the end)",
              "vendor_total": "every Amount for this vendor, summed"}
    for k in fcols:
        A(FORMULA_COLS[k], fwords[k], "a formula (a calculated column of the table)", "formula: =" + calc[FORMULA_COLS[k]])
    for c in extra_cols:
        A(c, "kept from your file as it is (the tracker's template has no column for it)",
          "; ".join("%s: %s" % (a, b) for a, b in sources_of.get(c, [])), ctype.get(c, "text"))
    A(SOURCE_COL, "the file the row came from (and any file it was also in, merged)", "", "text")
    A(LINE_COL, "the row's line in that file (line 1 is the header)", "", "number")
    A(CHECK_COL, "every flag on the row, in words (the Flags sheet lists them one per line)", "", "text")
    A(None)
    A(_B("Rows"), _B("In the file"), _B("Placed in Data"), _B("Merged (same file)"), _B("Merged (other file)"), _B("Unplaced"))
    for f in per_file:
        S_ = f["view"].get("summary") or {}
        A(f["name"], int(S_.get("rows_in", 0)), sum(1 for r in rows if r.file == f["name"]),
          sum(1 for m in merged if m["file"] == f["name"] and m["rule"].startswith("within")),
          sum(1 for m in merged if m["file"] == f["name"] and m["rule"].startswith("across")),
          sum(1 for u in unplaced if u["file"] == f["name"] and u["line"] is not None))
        if int(S_.get("withheld_left_out", 0)):
            A("  %s left out of %s, as you chose on the personal-data step; it is in no sheet."
              % (_plural(int(S_["withheld_left_out"]), "withheld column"), f["name"]))
    A(None)
    A(_B("What was flagged, changed and merged"))
    A("Changes: %s, each with its line, its value before and after and the rule." % _plural(len(changes), "cell"))
    A("Merged duplicates: %s merged into the first copy (%s). Rule within a file: the cleaner's (%s). Rule across "
      "files: %s." % (_plural(len(merged), "row"), "kept row and merged row side by side",
                      (per_file[0]["detail"].get("duplicate_rule") or "rows equal in every column"),
                      ("the same " + ", ".join(key_cols)) if key_cols else
                      "equal in every column both files hold, once cleaned; the same ID with any difference is flagged, not merged"))
    A("Unplaced: %s that are not records (listed with why); nothing from them is in Data." % _plural(len(unplaced), "row"))
    A("Flags: %s on %s: a value the cleaner could not read (kept as written), a blank (left empty), a check that "
      "failed (a closed date before the opened date, a status without its date), a possible duplicate."
      % (_plural(len(flags), "flag"), _plural(sum(1 for r in rows if r.flags), "row")))
    A("A blank cell is flagged when its column is one the row's file holds; Closed is not flagged while the status is open.")
    if asked:
        A(None)
        A(_B("Your questions"))
        for q in asked:
            A(q)
        A("Each is answered on Summary by a formula, or Summary says what the tracker would need to answer it.")
    ins_sheet = {"name": "Instructions", "widths": [34, 70, 48, 20, 16, 12], "rows": ins}

    lists_sheet = {"name": "Lists", "widths": [max(14, max([len(h) for h, _v in lists_cols] + [10]) + 2)] * max(1, len(lists_cols)),
                   "rows": lists_rows if lists_cols else [[_B("No dropdown lists: this tracker has no status or category column.")]]}
    sheets = [data_sheet, summary_sheet, lists_sheet, ins_sheet,
              {"name": "Changes", "freeze": 1, "widths": [30, 10, 10, 18, 30, 30, 34], "rows": ch_rows},
              {"name": "Merged duplicates", "freeze": 1, "widths": [52, 34, 10, 34, 80, 24], "rows": mg_rows},
              {"name": "Unplaced", "freeze": 1, "widths": [30, 10, 60, 70], "rows": un_rows},
              {"name": "Flags", "freeze": 1, "widths": [30, 10, 10, 22, 26, 90], "rows": fg_rows}]
    title = "%s tracker" % T["label"]
    return {"title": title, "sheets": sheets}, answers


# ------------------------------------------------------------------ the visitor's questions
_Q_RULES: List[Tuple[str, str]] = [
    (r"overdue|late|past due|behind", "overdue"),
    (r"oldest|longest|waiting the longest|been open the longest", "oldest"),
    (r"average|how long|typical|turnaround|take to (close|finish|complete)", "avg_closed"),
    (r"categor|type|kind of", "category"),
    (r"status|stage|state", "status"),
    (r"who|owner|team|assign|person|staff", "owner"),
    (r"vendor|supplier|payee", "vendor"),
    (r"unpaid|outstanding|owe", "unpaid"),
    (r"worth|value|stock value", "stock_value"),
    (r"reorder|low stock|run(ning)? out|below", "reorder"),
    (r"hours", "hours"),
    (r"spend|spent|total amount|cost", "amount"),
    (r"open|outstanding|pending|how many", "open"),
    (r"done|closed|completed|finished", "done"),
]


def _answer(q: str, cells: Dict[str, str], group_at: Dict[str, int], oldest_at: Optional[int],
            role_col: Dict[str, str], fcols: List[str], T: Dict[str, Any], sm: List[List[Any]]) -> Dict[str, Any]:
    ql = q.lower()
    for pat, what in _Q_RULES:
        if not re.search(pat, ql):
            continue
        if what in cells:
            how = sm[int(cells[what][1:]) - 1][2]
            return {"cell": cells[what], "text": None, "how": "the %s line under Totals: %s" % (sm[int(cells[what][1:]) - 1][0], how)}
        if what in group_at:
            return {"cell": None, "text": "See “%s” below (row %d)" % (sm[group_at[what] - 1][0]["v"], group_at[what]),
                    "how": "a COUNTIFS for each value"}
        if what == "oldest" and oldest_at:
            return {"cell": "C%d" % oldest_at, "text": None,
                    "how": "the open row with the most days open (the list of five is below, row %d)" % oldest_at}
        if what == "owner" and "owner" not in role_col:
            return {"cell": None, "text": "Not answered: none of your files has an owner or team column",
                    "how": "add an Owner column to answer it"}
        if what == "overdue" and "due" not in role_col:
            return {"cell": None, "text": "Not answered: none of your files has a due date", "how": "add a Due column"}
    return {"cell": None, "text": "Not answered by a formula: this tracker counts %s" % ", ".join(
        [x for x in ("rows", "open and overdue rows" if "overdue" in fcols else "", "rows by status" if "status" in group_at else "",
                     "rows by %s" % role_col["category"].lower() if "category" in group_at else "",
                     "days open" if "days_open" in fcols else "") if x]),
            "how": "read the Data sheet, or ask it differently"}
