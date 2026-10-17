"""Ask with SQL (Track B, plan/DASHBOARD-DESIGN.md; CONTRACT §5.13): a question about the visitor's rows, answered as
SQL run in the browser, then explained by the engine's own decomposition of the asked move.

The "warehouse" is the rows the engine kept, as the analyst loop reads them (nl_browser._analysis_frame: no withheld or
coded column is in that frame), landed in an in-memory SQLite table named kept_rows. Nothing here sends a row anywhere.

  schema(state)                    the table and its kept columns (name, type, role, unit, kind): what the worker's
                                   POST /sql may see. Never a value.
  check_sql(sql, cols, hidden)     the static check (one read-only SELECT/WITH, no PRAGMA/ATTACH/..., only kept_rows and
                                   its columns). The worker runs the same rules (src/sql.js checkSql) before a query
                                   leaves it; this one runs again here, and the SQLite authorizer is the real fence.
  run_sql(state, sql)              runs a query under the authorizer: reads of kept_rows only, withheld/coded columns
                                   denied, functions from an allowlist, nothing that writes; PRAGMA query_only; a row
                                   cap (ROW_CAP returned) and a time cap (progress handler). Small cells: a result row
                                   that aggregates fewer than SMALL_CELL source rows is hidden when the engine can count
                                   them (the query reads kept_rows directly in its outer SELECT), and the answer says
                                   plainly when it cannot.
  explain(state, question)         the engine's own explanation of the asked move: the measure (a ratio such as gross
                                   margin = (revenue - cost) / revenue, recomputed from sums; never an average of row
                                   ratios), the period (the latest quarter/month/year in the slice, against the one
                                   before and the same one a year earlier), the slice (values of grouping columns
                                   named in the question, e.g. region = EMEA), and the change split by each grouping
                                   column into rate and mix effects (a flow: each group's contribution). Every figure
                                   is computed here; the sentences are filled from those figures.
  answer(state, question, sql, chart_spec)   all of it for the page: the SQL to run (the AI's, or the engine's own
                                   when there is none or it is refused), its result, the explanation and the chart spec
                                   (the AI's, checked against the chart menu and the kept columns, or the engine's).

state is nl_browser._LAST_ANALYST: {"df", "kinds", "hint", "shape", "hidden"}.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

TABLE = "kept_rows"
ROW_CAP = 5000              # result rows returned at most
TIME_CAP_S = 3.0            # a query running longer is interrupted
SMALL_CELL = 5              # a result row over fewer source rows is hidden; a group with fewer rows is pooled
MAX_SQL_CHARS = 4000
MAX_GROUPS = 50             # a grouping column with more distinct values is not split (an id, a free text)
TOP_DRIVERS = 5

# --------------------------------------------------------------------------------------------- the static check
BANNED = frozenset("""pragma attach detach insert update delete create drop alter vacuum reindex analyze truncate begin
commit rollback savepoint release grant revoke trigger load_extension readfile writefile edit fts3_tokenizer""".split())
FUNCTIONS = frozenset("""count sum avg min max total group_concat abs round coalesce ifnull nullif iif lower upper length
substr substring trim ltrim rtrim replace instr strftime date time datetime julianday unixepoch printf format typeof
sign row_number rank dense_rank percent_rank cume_dist ntile lag lead first_value last_value nth_value like glob
cast hex quote char unicode likely unlikely""".split())
KEYWORDS = frozenset("""select with recursive as from where group by having order asc desc limit offset and or not in is
null like glob between case when then else end distinct all union intersect except join inner left right full outer cross
natural on using exists cast over partition window rows range groups unbounded preceding following current row filter
nulls first last collate escape values integer int real text numeric blob float double varchar char boolean date true
false within materialized exclude ties others no""".split())
_TOKEN = re.compile(r"""
 (?P<ws>\s+)
|(?P<lc>--[^\n]*)
|(?P<bc>/\*.*?\*/)
|(?P<str>'(?:[^']|'')*')
|(?P<qid>"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])
|(?P<num>\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)
|(?P<word>[A-Za-z_][A-Za-z0-9_]*)
|(?P<op>\|\||<=|>=|<>|!=|==|<<|>>|[-+*/%<>=(),.;~&|])
""", re.S | re.X)


def _tokens(sql: str) -> Tuple[Optional[List[Tuple[str, str]]], str]:
    """(kind, text) for every significant token, or (None, reason) for text the check cannot read."""
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(sql):
        m = _TOKEN.match(sql, i)
        if not m:
            ch = sql[i]
            if ch in "'\"`[":
                return None, "a quote or bracket is never closed"
            if ch == "/" and sql.startswith("/*", i):
                return None, "a comment is never closed"
            if ch in "?:@$":
                return None, "parameters are not allowed"
            return None, "the character %r is not allowed" % ch
        kind = m.lastgroup
        if kind not in ("ws", "lc", "bc"):
            out.append((kind, m.group(0)))
        i = m.end()
    return out, ""


def _ident(kind: str, text: str) -> str:
    if kind == "qid":
        t = text[1:-1]
        return t.replace('""', '"') if text[0] == '"' else t
    return text


def check_sql(sql: Any, columns: List[str], hidden: Optional[List[str]] = None) -> Dict[str, Any]:
    """{"ok": True, "sql": the query without a trailing semicolon} or {"ok": False, "reason"}: one read-only
    SELECT/WITH over kept_rows and its listed columns. The worker's checkSql applies the same rules."""
    if not isinstance(sql, str) or not sql.strip():
        return {"ok": False, "reason": "no SQL"}
    if len(sql) > MAX_SQL_CHARS:
        return {"ok": False, "reason": "longer than %d characters" % MAX_SQL_CHARS}
    toks, why = _tokens(sql)
    if toks is None:
        return {"ok": False, "reason": why}
    while toks and toks[-1] == ("op", ";"):
        toks = toks[:-1]
    if not toks:
        return {"ok": False, "reason": "no SQL"}
    if any(t == ("op", ";") for t in toks):
        return {"ok": False, "reason": "more than one statement"}
    if toks[0][0] != "word" or toks[0][1].lower() not in ("select", "with"):
        return {"ok": False, "reason": "only a SELECT or WITH query is allowed"}
    cols = {c.lower(): c for c in columns}
    hid = {h.lower(): h for h in (hidden or [])}
    low = [(k, t.lower()) for k, t in toks]
    # names the query itself defines: CTE names (name AS ( / name (cols) AS (), AS aliases, table aliases
    defined = set()
    for i, (k, t) in enumerate(low):
        if k in ("word", "qid"):
            nm = _ident(k, toks[i][1]).lower()
            nxt = low[i + 1] if i + 1 < len(low) else ("", "")
            nx2 = low[i + 2] if i + 2 < len(low) else ("", "")
            if nxt == ("word", "as") and nx2 == ("op", "("):
                defined.add(nm)
            if i > 0 and low[i - 1] == ("word", "as"):
                defined.add(nm)
            if nxt == ("op", "(") and k == "word" and t not in FUNCTIONS and t not in KEYWORDS:
                # name(col, ...) AS ( : a CTE with its column list
                depth, j = 0, i + 1
                while j < len(low):
                    if low[j] == ("op", "("):
                        depth += 1
                    elif low[j] == ("op", ")"):
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                if j + 2 < len(low) and low[j + 1] == ("word", "as") and low[j + 2] == ("op", "("):
                    defined.add(nm)
                    for q in range(i + 2, j):
                        if low[q][0] in ("word", "qid"):
                            defined.add(_ident(low[q][0], toks[q][1]).lower())
    # table aliases: FROM/JOIN <table or cte> [AS] alias
    for i, (k, t) in enumerate(low):
        if (k, t) in (("word", "from"), ("word", "join")):
            j = i + 1
            if j < len(low) and low[j][0] in ("word", "qid"):
                j += 1
                if j < len(low) and low[j] == ("word", "as"):
                    j += 1
                if j < len(low) and low[j][0] in ("word", "qid") and low[j][1] not in KEYWORDS:
                    defined.add(_ident(low[j][0], toks[j][1]).lower())
    for i, (k, t) in enumerate(low):
        if k == "word" and t in BANNED:
            return {"ok": False, "reason": "%s is not allowed: the query may only read" % toks[i][1].upper()}
        if k == "word" and t == "replace" and not (i + 1 < len(low) and low[i + 1] == ("op", "(")):
            return {"ok": False, "reason": "REPLACE is not allowed: the query may only read"}
        if k in ("word", "qid"):
            nm = _ident(k, toks[i][1]).lower()
            if nm in hid:
                return {"ok": False, "reason": "%s is a column you withheld or coded; SQL cannot read it" % hid[nm]}
            prev = low[i - 1] if i else ("", "")
            nxt = low[i + 1] if i + 1 < len(low) else ("", "")
            if prev in (("word", "from"), ("word", "join")) or (prev == ("op", ",") and _in_from(low, i)):
                if nm != TABLE and nm not in defined:
                    return {"ok": False, "reason": "only the table %s can be read (not %s)" % (TABLE, _ident(k, toks[i][1]))}
                continue
            if k == "word" and nxt == ("op", "(") and t not in KEYWORDS and t not in FUNCTIONS and nm not in defined:
                return {"ok": False, "reason": "the function %s is not allowed" % toks[i][1]}
            if k == "word" and (t in KEYWORDS or t in FUNCTIONS):
                continue
            if nm in cols or nm == TABLE or nm in defined:
                continue
            if k == "qid" and toks[i][1][0] == '"':
                return {"ok": False, "reason": "%s is not a column of %s (write text values in single quotes)" % (toks[i][1], TABLE)}
            return {"ok": False, "reason": "%s is not a column of %s" % (_ident(k, toks[i][1]), TABLE)}
    text = sql.strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    return {"ok": True, "sql": text}


def _in_from(low: List[Tuple[str, str]], i: int) -> bool:
    """Whether token i (after a comma) is in a FROM list at its own depth: the nearest FROM before it at the same depth
    with no WHERE/GROUP/ORDER/... between."""
    depth = 0
    for j in range(i - 1, -1, -1):
        k, t = low[j]
        if (k, t) == ("op", ")"):
            depth += 1
        elif (k, t) == ("op", "("):
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and k == "word":
            if t in ("from", "join"):
                return True
            if t in ("select", "where", "group", "order", "having", "limit", "on", "using", "window", "union", "values"):
                return False
    return False


# --------------------------------------------------------------------------------------------- the table
def _cols(state: Dict[str, Any]) -> List[str]:
    df = state.get("df")
    return [str(c) for c in df.columns] if df is not None else []


def schema(state: Dict[str, Any]) -> Dict[str, Any]:
    """What POST /sql may see: the table's name and its kept columns (name, type, role, unit, kind). Never a value."""
    df = state.get("df")
    if df is None:
        return {"ok": False, "reason": "no file has been analysed", "table": TABLE, "columns": []}
    kinds = state.get("kinds") or {}
    shape = state.get("shape") or {}
    mkind = {str(m.get("column")): str(m.get("kind") or "") for m in shape.get("measures") or [] if isinstance(m, dict)}
    dims = set(str(d) for d in shape.get("dimensions") or [])
    date = _date_col(state)
    out = []
    for c in _cols(state):
        k = kinds.get(c) or "text"
        typ = {"number": "number", "date": "date"}.get(k, "text")
        if c == date:
            role = "date"
        elif typ == "number":
            role = "measure"
        elif c in dims:
            role = "dimension"
        elif typ == "date":
            role = "date"
        else:
            role = "identifier" if _distinct(df[c]) > MAX_GROUPS else "dimension"
        rec = {"name": c, "type": typ, "role": role, "unit": ""}
        if role == "measure":
            rec["kind"] = mkind.get(c) or "flow"
        out.append(rec)
    return {"ok": True, "table": TABLE, "rows": int(len(df)), "columns": out,
            "dialect": "SQLite; dates are text YYYY-MM-DD (strftime works on them)"}


def _distinct(s: Any) -> int:
    try:
        return int(s.dropna().astype(str).nunique())
    except Exception:  # noqa: BLE001
        return 0


def _date_col(state: Dict[str, Any]) -> Optional[str]:
    hint = state.get("hint") or {}
    shape = state.get("shape") or {}
    kinds = state.get("kinds") or {}
    for c in (shape.get("date"), hint.get("date")):
        if c and c in kinds:
            return str(c)
    for c, k in kinds.items():
        if k == "date":
            return str(c)
    return None


def _connection(state: Dict[str, Any]) -> sqlite3.Connection:
    """The kept rows in an in-memory SQLite table, made once per frame and read-only from then on."""
    import pandas as pd
    df = state["df"]
    key = id(df)
    con = state.get("_sql_con")
    if con is not None and state.get("_sql_key") == key:
        return con
    kinds = state.get("kinds") or {}
    con = sqlite3.connect(":memory:", check_same_thread=False)
    cols = _cols(state)
    q = lambda c: '"%s"' % c.replace('"', '""')  # noqa: E731
    ddl = ", ".join("%s %s" % (q(c), {"number": "REAL"}.get(kinds.get(c), "TEXT")) for c in cols)
    con.execute("CREATE TABLE %s (%s)" % (TABLE, ddl))
    data = []
    conv = []
    for c in cols:
        k = kinds.get(c)
        s = df[c]
        if k == "date":
            d = pd.to_datetime(s, errors="coerce")
            has_time = bool(((d.dt.hour != 0) | (d.dt.minute != 0) | (d.dt.second != 0)).fillna(False).any())
            fmt = "%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d"
            conv.append([None if pd.isna(x) else x.strftime(fmt) for x in d])
        elif k == "number":
            v = pd.to_numeric(s, errors="coerce")
            conv.append([None if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x) for x in v.tolist()])
        else:
            conv.append([None if (x is None or (isinstance(x, float) and math.isnan(x))) else str(x) for x in s.tolist()])
    data = list(zip(*conv)) if conv else []
    if cols:
        con.executemany("INSERT INTO %s VALUES (%s)" % (TABLE, ", ".join("?" * len(cols))), data)
    con.commit()
    con.execute("PRAGMA query_only = 1")
    old = state.get("_sql_con")
    if old is not None:
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
    state["_sql_con"], state["_sql_key"] = con, key
    return con


# --------------------------------------------------------------------------------------------- running a query
_AGG = frozenset("count sum avg min max total group_concat".split())


def _outer_count(sql: str) -> Optional[str]:
    """The query with COUNT(*) AS "nl_rows_behind" added to its outer SELECT, when that SELECT reads kept_rows
    directly (no join, no subquery, no compound): then each result row's count is the source rows it aggregates.
    None when the engine cannot count them that way."""
    toks, _ = _tokens(sql)
    if not toks:
        return None
    low = [(k, t.lower()) for k, t in toks]
    depth = 0
    sel, frm, after_from = [], [], []
    spans = []
    pos = 0
    # token start offsets in the original text, to insert at the outer FROM
    for k, t in toks:
        j = sql.find(t, pos)
        spans.append(j)
        pos = j + len(t)
    for i, (k, t) in enumerate(low):
        if (k, t) == ("op", "("):
            depth += 1
        elif (k, t) == ("op", ")"):
            depth -= 1
        elif depth == 0 and k == "word":
            if t == "select":
                sel.append(i)
            elif t == "from":
                frm.append(i)
            elif t in ("union", "intersect", "except", "join"):
                return None
    if len(sel) != 1 or len(frm) != 1 or frm[0] < sel[0]:
        return None
    f = frm[0]
    if f + 1 >= len(low) or _ident(low[f + 1][0], toks[f + 1][1]).lower() != TABLE:
        return None
    # nothing but an optional alias between the table and WHERE/GROUP/ORDER/LIMIT (a comma would be a join)
    j = f + 2
    while j < len(low) and not (low[j][0] == "word" and low[j][1] in ("where", "group", "order", "limit", "having", "window")):
        if low[j] == ("op", ","):
            return None
        j += 1
    if low[sel[0] + 1] == ("word", "distinct"):
        return None
    at = spans[f]
    return sql[:at] + ', COUNT(*) AS "nl_rows_behind" ' + sql[at:]


def _plain_cell(v: Any) -> Any:
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "(binary)"
    return v


def run_sql(state: Dict[str, Any], sql: str) -> Dict[str, Any]:
    """Run one checked query under the authorizer. {"ok", "columns", "rows", "row_count", "truncated", "small_cells"
    {"state": applied|not_feasible|raw_rows, "hidden", "note"}, "seconds"} or {"ok": False, "error"}."""
    if state.get("df") is None:
        return {"ok": False, "error": "no file has been analysed"}
    hidden = [str(h) for h in state.get("hidden") or []]
    chk = check_sql(sql, _cols(state), hidden)
    if not chk["ok"]:
        return {"ok": False, "error": "refused: " + chk["reason"]}
    sql = chk["sql"]
    con = _connection(state)
    hid = {h.lower() for h in hidden}
    funcs: List[str] = []
    denied: List[str] = []
    A = sqlite3

    def fence(action, a1, a2, db, src):
        if action == getattr(A, "SQLITE_SELECT", 21):
            return A.SQLITE_OK
        if action == getattr(A, "SQLITE_READ", 20):
            if str(a1 or "").lower() != TABLE:
                denied.append("the table %s" % a1)
                return A.SQLITE_DENY
            if a2 and str(a2).lower() in hid:
                denied.append("the withheld column %s" % a2)
                return A.SQLITE_DENY
            return A.SQLITE_OK
        if action == getattr(A, "SQLITE_FUNCTION", 31):
            name = str(a2 or "").lower()
            funcs.append(name)
            if name in FUNCTIONS:
                return A.SQLITE_OK
            denied.append("the function %s" % name)
            return A.SQLITE_DENY
        if action == getattr(A, "SQLITE_RECURSIVE", 33):
            return A.SQLITE_OK
        denied.append("an action that is not a read (code %s)" % action)
        return A.SQLITE_DENY

    deadline = [0.0]

    def clock():
        return 1 if time.monotonic() > deadline[0] else 0

    def go(text: str) -> Tuple[List[str], List[Any], bool]:
        con.set_authorizer(fence)
        con.set_progress_handler(clock, 2000)
        deadline[0] = time.monotonic() + TIME_CAP_S
        try:
            cur = con.execute(text)
            cols = [d[0] for d in (cur.description or [])]
            got = cur.fetchmany(ROW_CAP + 1)
            return cols, got, len(got) > ROW_CAP
        finally:
            con.set_progress_handler(None, 0)
            con.set_authorizer(None)

    t0 = time.monotonic()
    small = {"state": "raw_rows", "hidden": 0, "note": ""}
    try:
        cols, got, more = go(sql)
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "interrupted" in msg:
            return {"ok": False, "error": "stopped: the query ran past the %g-second limit" % TIME_CAP_S}
        if denied:
            return {"ok": False, "error": "refused by the engine's fence: the query tried to use %s" % denied[0]}
        m = re.search(r"no such column: (\S+)", msg)
        if m and m.group(1).split(".")[-1].lower() in hid:
            return {"ok": False, "error": "%s is a column you withheld or coded; SQL cannot read it" % m.group(1)}
        return {"ok": False, "error": "SQLite could not run it: %s" % msg}
    except sqlite3.DatabaseError as exc:
        if denied:
            return {"ok": False, "error": "refused by the engine's fence: the query tried to use %s" % denied[0]}
        return {"ok": False, "error": "SQLite could not run it: %s" % exc}
    aggregate = any(f in _AGG for f in funcs)
    rows = [list(r) for r in got[:ROW_CAP]]
    if aggregate:
        counted = _outer_count(sql)
        ok_count = False
        if counted:
            try:
                funcs.clear()
                c2, g2, _m2 = go(counted)
                if c2 and c2[-1] == "nl_rows_behind" and len(g2) == len(got):
                    keep, gone = [], 0
                    for r in g2[:ROW_CAP]:
                        n = r[-1]
                        if isinstance(n, (int, float)) and n < SMALL_CELL:
                            gone += 1
                        else:
                            keep.append(list(r[:-1]))
                    rows = keep
                    ok_count = True
                    small = {"state": "applied", "hidden": gone,
                             "note": ("%d result %s rested on fewer than %d of your rows and %s hidden." %
                                      (gone, "row" if gone == 1 else "rows", SMALL_CELL, "is" if gone == 1 else "are"))
                             if gone else "Every result row rests on %d or more of your rows." % SMALL_CELL}
            except sqlite3.DatabaseError:
                ok_count = False
        if not ok_count:
            small = {"state": "not_feasible", "hidden": 0,
                     "note": "The engine could not count the rows behind each result row of this query (it reads "
                             "through a subquery, a join or a compound), so groups of fewer than %d rows are not "
                             "hidden here; read small groups with care." % SMALL_CELL}
    else:
        small["note"] = "Rows as kept, not aggregates; at most %s are returned." % format(ROW_CAP, ",")
    out = {"ok": True, "sql": sql, "columns": cols, "rows": [[_plain_cell(v) for v in r] for r in rows],
           "row_count": len(rows), "truncated": bool(more), "row_cap": ROW_CAP, "small_cells": small,
           "seconds": round(time.monotonic() - t0, 3)}
    if more:
        out["note"] = "The result is cut at %s rows." % format(ROW_CAP, ",")
    return out


# --------------------------------------------------------------------------------------------- reading the question
_REV = re.compile(r"(^|_| )(revenue|sales|net_sales|turnover|income|amount|gross_sales)( |_|$)", re.I)
_COST = re.compile(r"(^|_| )(cost|costs|cogs|cost_of_sales|cost_of_goods|expense|expenses)( |_|$)", re.I)
_UNITS = re.compile(r"(^|_| )(units|quantity|qty|volume)( |_|$)", re.I)


def _words(name: str) -> str:
    return re.sub(r"[_\s]+", " ", str(name)).strip().lower()


def _mentions(q: str, name: str) -> bool:
    w = _words(name)
    return bool(w) and re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(w), q) is not None


def read_question(state: Dict[str, Any], question: str) -> Dict[str, Any]:
    """The asked move, read by the engine's rules: {measure, grain, slice, notes}."""
    import pandas as pd
    df = state["df"]
    kinds = state.get("kinds") or {}
    q = " " + re.sub(r"\s+", " ", str(question or "").lower()) + " "
    nums = [c for c in _cols(state) if kinds.get(c) == "number"]
    shape = state.get("shape") or {}
    mkind = {str(m.get("column")): str(m.get("kind") or "flow") for m in shape.get("measures") or [] if isinstance(m, dict)}
    notes: List[str] = []
    rev = next((c for c in nums if _REV.search(_words(c).replace(" ", "_")) or _REV.search(_words(c))), None)
    cost = next((c for c in nums if _COST.search(_words(c).replace(" ", "_")) or _COST.search(_words(c))), None)
    units = next((c for c in nums if _UNITS.search(_words(c))), None)
    named = [c for c in nums if _mentions(q, c)]
    measure: Optional[Dict[str, Any]] = None
    if "margin" in q and rev and cost:
        measure = {"kind": "ratio", "label": "gross margin", "num": [rev, cost], "num_sign": [1, -1], "den": rev,
                   "pct": True,
                   "formula": "gross margin = (%s - %s) / %s, recomputed from the summed %s and %s" % (rev, cost, rev, rev, cost)}
    elif "margin" in q:
        notes.append("The question asks about a margin, but the engine found no revenue and cost columns to make one.")
    elif re.search(r"\bprofit\b", q) and rev and cost:
        measure = {"kind": "flow", "label": "gross profit", "num": [rev, cost], "num_sign": [1, -1], "den": None,
                   "pct": False, "formula": "gross profit = %s - %s, summed" % (rev, cost)}
    elif re.search(r"\b(price|per unit|selling price|asp)\b", q) and rev and units:
        measure = {"kind": "ratio", "label": "average price per unit", "num": [rev], "num_sign": [1], "den": units,
                   "pct": False, "formula": "average price = %s / %s, recomputed from sums" % (rev, units)}
    else:
        m = re.search(r"([a-z0-9_ ]+?) per ([a-z0-9_ ]+?)\b", q)
        pair = None
        if m and len(named) >= 2:
            a = next((c for c in named if _mentions(" " + m.group(1) + " ", c)), None)
            b = next((c for c in named if _mentions(" " + m.group(2) + " ", c)), None)
            if a and b and a != b:
                pair = (a, b)
        if pair:
            measure = {"kind": "ratio", "label": "%s per %s" % (_words(pair[0]), _words(pair[1])), "num": [pair[0]],
                       "num_sign": [1], "den": pair[1], "pct": False,
                       "formula": "%s / %s, recomputed from sums" % pair}
        else:
            col = named[0] if named else (shape.get("primary") if shape.get("primary") in nums else (nums[0] if nums else None))
            if col:
                if mkind.get(col, "flow") == "flow":
                    measure = {"kind": "flow", "label": "total %s" % _words(col), "num": [col], "num_sign": [1],
                               "den": None, "pct": False, "formula": "total %s, summed (a flow)" % col}
                else:
                    measure = {"kind": "ratio", "label": "average %s" % _words(col), "num": [col], "num_sign": [1],
                               "den": "__rows__", "pct": False,
                               "formula": "average %s = the sum of %s / its rows (a %s is averaged, never summed)"
                                          % (col, col, mkind.get(col))}
                if not named:
                    notes.append("No measure was named in the question, so the engine read %s." % col)
    if re.search(r"\bquarter|\bq[1-4]\b|quarterly", q):
        grain = "Q"
    elif re.search(r"\bmonth|monthly\b", q):
        grain = "M"
    elif re.search(r"\byear\b|annual|yearly", q) and not re.search(r"(last|a) year (ago|earlier)", q):
        grain = "Y"
    else:
        grain = "Q"
        notes.append("No period was named, so the engine compares quarters.")
    # the slice: values of a grouping column the question names (EMEA, Online); the longest match in each column
    sl: Dict[str, str] = {}
    date = _date_col(state)
    for c in _cols(state):
        if kinds.get(c) != "text" or c == date:
            continue
        vals = df[c].dropna().astype(str)
        if vals.nunique() > 200:
            continue
        best = None
        for v in vals.unique():
            v2 = v.strip()
            if len(v2) < 2:
                continue
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(v2.lower()), q):
                if best is None or len(v2) > len(best):
                    best = v2
        if best is not None:
            sl[c] = best
    return {"measure": measure, "grain": grain, "slice": sl, "notes": notes}


# --------------------------------------------------------------------------------------------- the decomposition
GRAIN_WORDS = {"Q": ("quarter", "quarters"), "M": ("month", "months"), "Y": ("year", "years")}
YEAR_BACK = {"Q": 4, "M": 12, "Y": 1}


def _pname(p: Any, grain: str) -> str:
    s = str(p)
    if grain == "Q":
        return s[:4] + " Q" + s[-1]
    return s


def _frame_for(state: Dict[str, Any], rq: Dict[str, Any]):
    import numpy as np
    import pandas as pd
    df = state["df"]
    date = _date_col(state)
    m = rq["measure"]
    sub = df
    for c, v in rq["slice"].items():
        sub = sub[sub[c].astype(str).str.strip() == v]
    d = pd.to_datetime(sub[date], errors="coerce")
    sub = sub.assign(__p=d.dt.to_period(rq["grain"]))
    sub = sub[sub["__p"].notna()]
    num = np.zeros(len(sub))
    ok = np.ones(len(sub), dtype=bool)
    for col, sg in zip(m["num"], m["num_sign"]):
        v = pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=float)
        ok &= ~np.isnan(v)
        num = num + sg * np.nan_to_num(v)
    if m["den"] == "__rows__":
        den = np.ones(len(sub))
    elif m["den"]:
        v = pd.to_numeric(sub[m["den"]], errors="coerce").to_numpy(dtype=float)
        ok &= ~np.isnan(v)
        den = np.nan_to_num(v)
    else:
        den = None
    sub = sub.assign(__n=num)
    if den is not None:
        sub = sub.assign(__d=den)
    dropped = int((~ok).sum())
    return sub[ok], date, dropped


def _value(n: float, d: Optional[float], m: Dict[str, Any]) -> Optional[float]:
    if m["kind"] == "flow":
        return n
    if not d:
        return None
    v = n / d
    return v * 100.0 if m["pct"] else v


def _fmt_num(v: float, dec: Optional[int] = None) -> str:
    if v is None:
        return "n/a"
    if dec is None:
        dec = 0 if abs(v) >= 100 else (1 if abs(v) >= 10 else 2)
    s = format(abs(v), ",.%df" % dec)
    return ("-" if v < 0 and s.strip("0.,") else "") + s


def _fmt_val(v: Optional[float], m: Dict[str, Any]) -> str:
    if v is None:
        return "n/a"
    return ("%.1f%%" % v) if m["pct"] else _fmt_num(v)


def _fmt_chg(v: float, m: Dict[str, Any], signed: bool = True) -> str:
    if m["pct"]:
        s = "%.1f points" % abs(v)
        if s.startswith("0.0"):
            return "0.0 points"
        return (("+" if v > 0 else "-") + s) if signed else s
    if m["kind"] == "flow":
        s = _fmt_num(abs(v))
        return (("+" if v > 0 else "-") + s) if signed and s.strip("0.,") else s
    s = _fmt_num(abs(v), 3 if abs(v) < 10 else None)
    return (("+" if v > 0 else "-") + s) if signed and s.strip("0.,") else s


def _decompose(sub: Any, dim: str, p0: Any, p1: Any, m: Dict[str, Any]) -> Dict[str, Any]:
    """The change of the measure between periods p0 and p1, split by the groups of dim. A ratio M = sum(N)/sum(D):
    with w = D_g/D (each group's share of the denominator) and r = N_g/D_g (its own ratio), the change is
    sum(w1 r1 - w0 r0) = rate + mix, rate_g = (w0+w1)/2 (r1-r0), mix_g = (w1-w0) ((r0+r1)/2 - (R0+R1)/2): exact, so the
    groups' effects add up to the whole change. A flow: each group's contribution N1_g - N0_g. A group with fewer than
    SMALL_CELL rows in either period is pooled into one "fewer than 5 rows" group (the sums stay exact)."""
    g = sub[sub["__p"].isin([p0, p1])]
    lab = g[dim].astype(str).str.strip().where(g[dim].notna(), "(blank)")
    g = g.assign(__g=lab.to_numpy())
    agg = {"__n": "sum"}
    if "__d" in g:
        agg["__d"] = "sum"
    t = g.groupby(["__g", "__p"]).agg(agg)
    cnt = g.groupby(["__g", "__p"]).size()
    groups = sorted(set(g["__g"]))
    small = [x for x in groups if min(int(cnt.get((x, p0), 0)), int(cnt.get((x, p1), 0))) < SMALL_CELL]
    pooled = "groups with fewer than %d rows" % SMALL_CELL

    def get(x, p, col):
        if x == pooled:
            return float(sum(t[col].get((y, p), 0.0) for y in small))
        return float(t[col].get((x, p), 0.0))

    def n_rows(x, p):
        if x == pooled:
            return int(sum(int(cnt.get((y, p), 0)) for y in small))
        return int(cnt.get((x, p), 0))

    keys = [x for x in groups if x not in small] + ([pooled] if small else [])
    scale = 100.0 if m["pct"] else 1.0
    segs = []
    if m["kind"] == "flow":
        for x in keys:
            n0, n1 = get(x, p0, "__n"), get(x, p1, "__n")
            segs.append({"group": x, "rows": [n_rows(x, p0), n_rows(x, p1)], "value": [n0, n1], "effect": n1 - n0,
                         "contribution": n1 - n0, "pooled": x == pooled})
        tot = sum(s["effect"] for s in segs)
        return {"dimension": dim, "kind": "flow", "total": tot, "segments": segs, "pooled": small}
    N0 = sum(get(x, p0, "__n") for x in keys)
    D0 = sum(get(x, p0, "__d") for x in keys)
    N1 = sum(get(x, p1, "__n") for x in keys)
    D1 = sum(get(x, p1, "__d") for x in keys)
    if D0 <= 0 or D1 <= 0:
        return {"dimension": dim, "kind": "ratio", "error": "the denominator is not positive in one of the periods"}
    R0, R1 = N0 / D0, N1 / D1
    Rbar = (R0 + R1) / 2
    residual_bits = 0.0
    for x in keys:
        n0, d0, n1, d1 = get(x, p0, "__n"), get(x, p0, "__d"), get(x, p1, "__n"), get(x, p1, "__d")
        w0, w1 = d0 / D0, d1 / D1
        if d0 > 0 and d1 > 0:
            r0, r1 = n0 / d0, n1 / d1
        elif d1 > 0:
            r1 = n1 / d1
            r0 = r1
        elif d0 > 0:
            r0 = n0 / d0
            r1 = r0
        else:
            r0 = r1 = 0.0
            residual_bits += (n1 / D1 - n0 / D0)
        rate = (w0 + w1) / 2 * (r1 - r0)
        mix = (w1 - w0) * ((r0 + r1) / 2 - Rbar)
        segs.append({"group": x, "rows": [n_rows(x, p0), n_rows(x, p1)], "share": [w0, w1],
                     "ratio": [r0 * scale if d0 > 0 else None, r1 * scale if d1 > 0 else None],
                     "rate": rate * scale, "mix": mix * scale, "effect": (rate + mix) * scale, "pooled": x == pooled})
    rate_t = sum(s["rate"] for s in segs)
    mix_t = sum(s["mix"] for s in segs)
    total = (R1 - R0) * scale
    return {"dimension": dim, "kind": "ratio", "total": total, "rate": rate_t, "mix": mix_t,
            "residual": total - rate_t - mix_t, "segments": segs, "pooled": small,
            "overall": [R0 * scale, R1 * scale]}


def _dims(state: Dict[str, Any], sub: Any, rq: Dict[str, Any], date: Optional[str]) -> List[str]:
    kinds = state.get("kinds") or {}
    out = []
    for c in _cols(state):
        if kinds.get(c) != "text" or c == date or c in rq["slice"]:
            continue
        k = _distinct(sub[c])
        if 2 <= k <= MAX_GROUPS and k < max(3, 0.5 * len(sub)):
            out.append(c)
    return out


def explain(state: Dict[str, Any], question: str) -> Dict[str, Any]:
    """The engine's own explanation of the asked move (see the module's docstring). Every figure is computed here."""
    if state.get("df") is None:
        return {"ok": False, "why": "no file has been analysed"}
    rq = read_question(state, question)
    date = _date_col(state)
    m = rq["measure"]
    base = {"question": str(question or "")[:300], "slice": rq["slice"], "grain": rq["grain"], "notes": list(rq["notes"])}
    if m is None:
        return dict(base, ok=False, why=(rq["notes"] or ["The engine found no measure to read in this file."])[0])
    base["measure"] = {k: m[k] for k in ("kind", "label", "num", "num_sign", "den", "pct", "formula")}
    if not date:
        return dict(base, ok=False, why="The file has no date column the engine kept, so periods cannot be compared.")
    sub, date, dropped = _frame_for(state, rq)
    if dropped:
        base["notes"].append("%s kept %s had no readable %s and %s left out of these sums." %
                             (format(dropped, ","), "row" if dropped == 1 else "rows", " or ".join(
                                 [c for c in m["num"]] + ([m["den"]] if m["den"] and m["den"] != "__rows__" else [])),
                              "is" if dropped == 1 else "are"))
    if not len(sub):
        return dict(base, ok=False, why="No kept row matches %s." % _slice_words(rq["slice"]))
    import pandas as pd
    grain = rq["grain"]
    per = sorted(sub["__p"].unique())
    p1 = per[-1]
    p0 = p1 - 1
    py = p1 - YEAR_BACK[grain]
    have = set(per)
    # the period is partial when the FILE's data stop before it ends (a slice with no order on the last day is not)
    last_day = pd.to_datetime(state["df"][date], errors="coerce").max()
    partial = bool(last_day is not None and not pd.isna(last_day) and last_day.normalize() < p1.end_time.normalize())
    tot = sub.groupby("__p").agg(n=("__n", "sum"), rows=("__n", "size"), **({"d": ("__d", "sum")} if "__d" in sub else {}))

    def val(p):
        if p not in have:
            return None
        r = tot.loc[p]
        return _value(float(r["n"]), float(r["d"]) if "d" in r else None, m)

    v1, v0, vy = val(p1), val(p0), val(py)
    period = {"grain": grain, "latest": _pname(p1, grain), "previous": _pname(p0, grain) if p0 in have else None,
              "year_ago": _pname(py, grain) if py in have else None, "partial": partial,
              "latest_last_day": None if pd.isna(last_day) else last_day.strftime("%Y-%m-%d"),
              "rows": {"latest": int(tot.loc[p1]["rows"]), "previous": int(tot.loc[p0]["rows"]) if p0 in have else 0,
                       "year_ago": int(tot.loc[py]["rows"]) if py in have else 0}}
    series = [{"period": _pname(p, grain), "value": val(p), "rows": int(tot.loc[p]["rows"])} for p in per]
    out = dict(base, ok=True, period=period, values={"latest": v1, "previous": v0, "year_ago": vy}, series=series)
    if p0 not in have:
        out["why"] = "The slice has no rows in the %s before %s, so there is no change to explain." % (GRAIN_WORDS[grain][0], period["latest"])
        out["text"] = [out["why"]]
        out["ok"] = False
        return out
    dims = _dims(state, sub[sub["__p"].isin([p0, p1])], rq, date)
    comps = {}
    for key, pp in (("vs_previous", p0), ("vs_year_ago", py)):
        if pp not in have:
            continue
        comps[key] = {"from": _pname(pp, grain), "to": period["latest"],
                      "change": (v1 - val(pp)) if (v1 is not None and val(pp) is not None) else None,
                      "by": [d for d in (_decompose(sub, dim, pp, p1, m) for dim in dims) if "error" not in d]}
    out["comparisons"] = comps
    out["drivers"] = _drivers(comps.get("vs_previous"), m, v1, _slice_words(rq["slice"]))
    out["text"] = _sentences(out, m)
    return out


def _slice_words(sl: Dict[str, str]) -> str:
    return " and ".join("%s (%s)" % (v, _words(c)) for c, v in sl.items()) if sl else "the whole file"


def _drivers(comp: Optional[Dict[str, Any]], m: Dict[str, Any], overall: Optional[float] = None,
             where: str = "the slice") -> List[Dict[str, Any]]:
    """The largest single effects in the direction of the change, across every split (each split is a view of the
    same change: the drivers name where it shows most, never a sum across splits)."""
    if not comp or comp.get("change") is None:
        return []
    sign = -1 if comp["change"] < 0 else 1
    cands = []
    for b in comp["by"]:
        for s in b["segments"]:
            if s.get("pooled"):
                continue
            if m["kind"] == "flow":
                parts = [("contribution", s["contribution"])]
            else:
                parts = [("rate", s["rate"]), ("mix", s["mix"])]
            for kind, eff in parts:
                if eff * sign > 0:
                    cands.append({"dimension": b["dimension"], "group": s["group"], "effect_kind": kind, "effect": eff,
                                  "share": s.get("share"), "ratio": s.get("ratio"), "value": s.get("value"),
                                  "rows": s["rows"]})
    cands.sort(key=lambda x: -abs(x["effect"]))
    out = cands[:TOP_DRIVERS]
    for d in out:
        d["text"] = _driver_text(d, m, overall, where)
    return out


def _driver_text(d: Dict[str, Any], m: Dict[str, Any], overall: Optional[float] = None, where: str = "the slice") -> str:
    g, dim = d["group"], _words(d["dimension"])
    if d["effect_kind"] == "contribution":
        v0, v1 = d["value"]
        return "%s (%s): %s to %s, %s." % (g, dim, _fmt_num(v0), _fmt_num(v1), _fmt_chg(d["effect"], m))
    w0, w1 = d["share"]
    r0, r1 = d["ratio"]
    den = "rows" if m["den"] == "__rows__" else _words(m["den"])
    if d["effect_kind"] == "mix":
        own = r1 if r1 is not None else r0
        return ("%s (%s), mix: its share of %s went from %.1f%% to %.1f%%, and its own %s (%s) is %s the %s of %s; "
                "%s." % (g, dim, den, w0 * 100, w1 * 100, m["label"], _fmt_val(own, m),
                         "below" if overall is not None and own is not None and own < overall else "above",
                         _fmt_val(overall, m), where, _fmt_chg(d["effect"], m)))
    return ("%s (%s), rate: its own %s went from %s to %s; %s." %
            (g, dim, m["label"], _fmt_val(r0, m), _fmt_val(r1, m), _fmt_chg(d["effect"], m)))


def _sentences(o: Dict[str, Any], m: Dict[str, Any]) -> List[str]:
    P = o["period"]
    v = o["values"]
    sl = _slice_words(o["slice"])
    lab = m["label"][0].upper() + m["label"][1:]
    lines = []
    c0 = v["latest"] - v["previous"] if v["latest"] is not None and v["previous"] is not None else None
    if c0 is None:
        lines.append("%s in %s was %s in %s." % (lab, sl, _fmt_val(v["latest"], m), P["latest"]))
    else:
        word = "down" if c0 < 0 else ("up" if c0 > 0 else "unchanged,")
        s = "%s in %s was %s in %s, %s %s from %s in %s" % (lab, sl, _fmt_val(v["latest"], m), P["latest"], word,
                                                          _fmt_chg(c0, m, signed=False), _fmt_val(v["previous"], m), P["previous"])
        if v["year_ago"] is not None:
            cy = v["latest"] - v["year_ago"]
            s += ", and %s %s from %s in %s" % ("down" if cy < 0 else "up", _fmt_chg(cy, m, signed=False),
                                                 _fmt_val(v["year_ago"], m), P["year_ago"])
        lines.append(s + ".")
    rows = P["rows"]
    lines.append("How it is computed: %s; from %s kept rows in %s and %s in %s." %
                 (m["formula"] + ("" if m["kind"] == "flow" else ", never an average of row-level ratios"),
                  format(rows["latest"], ","), P["latest"], format(rows["previous"], ","), P["previous"]))
    if P["partial"]:
        lines.append("%s is not complete in the data: its last kept row is dated %s, so it is a part-%s against a whole one."
                     % (P["latest"], P["latest_last_day"], GRAIN_WORDS[P["grain"]][0]))
    comp = (o.get("comparisons") or {}).get("vs_previous")
    if comp and comp["by"]:
        lines.append("Each split below adds up to the whole change on its own (they are views of the same move, not "
                     "to be added together):")
        for b in comp["by"]:
            if m["kind"] == "flow":
                top = sorted(b["segments"], key=lambda s: -abs(s["effect"]))[:2]
                lines.append("By %s: %s." % (_words(b["dimension"]), "; ".join(
                    "%s %s" % (s["group"], _fmt_chg(s["effect"], m)) for s in top)))
            else:
                big = max(b["segments"], key=lambda s: abs(s["effect"]))
                lines.append("By %s: rate effect %s, mix effect %s; the largest single group is %s (%s)." %
                             (_words(b["dimension"]), _fmt_chg(b["rate"], m), _fmt_chg(b["mix"], m), big["group"],
                              _fmt_chg(big["effect"], m)))
    if o.get("drivers"):
        lines.append("The largest drivers, as the engine computed them:")
        lines.extend("%d. %s" % (i + 1, d["text"]) for i, d in enumerate(o["drivers"]))
    yo = (o.get("comparisons") or {}).get("vs_year_ago")
    if yo and yo["by"] and yo.get("change") is not None and m["kind"] != "flow":
        b = max(yo["by"], key=lambda b: max(abs(b["rate"]), abs(b["mix"])))
        lines.append("Against %s (%s): by %s, rate effect %s and mix effect %s." %
                     (yo["from"], _fmt_chg(yo["change"], m), _words(b["dimension"]), _fmt_chg(b["rate"], m),
                      _fmt_chg(b["mix"], m)))
    pooled = sorted({d for b in (comp or {}).get("by", []) for d in b.get("pooled") or []})
    if pooled:
        lines.append("Groups with fewer than %d rows in either period are pooled, never named (%d pooled)." %
                     (SMALL_CELL, len(pooled)))
    return lines


# --------------------------------------------------------------------------------------------- the engine's own SQL
def _q(c: str) -> str:
    return '"%s"' % str(c).replace('"', '""')


def _lit(v: str) -> str:
    return "'%s'" % str(v).replace("'", "''")


def engine_sql(state: Dict[str, Any], question: str) -> Optional[str]:
    """The SQL the engine writes for the asked move when the AI writes none: the measure by period in the slice."""
    rq = read_question(state, question)
    m = rq["measure"]
    date = _date_col(state)
    if not m or not date:
        return None
    d = _q(date)
    pe = {"Q": "strftime('%%Y', %s) || '-Q' || ((CAST(strftime('%%m', %s) AS INTEGER) + 2) / 3)" % (d, d),
          "M": "strftime('%%Y-%%m', %s)" % d, "Y": "strftime('%%Y', %s)" % d}[rq["grain"]]
    sel = ["%s AS period" % pe]
    for c in dict.fromkeys(m["num"] + ([m["den"]] if m["den"] and m["den"] != "__rows__" else [])):
        sel.append("SUM(%s) AS %s" % (_q(c), _q(c)))
    num = " ".join(("%s SUM(%s)" % ("+" if sg > 0 else "-", _q(c))) for c, sg in zip(m["num"], m["num_sign"])).lstrip("+ ")
    name = re.sub(r"[^a-z0-9]+", "_", m["label"].lower()).strip("_")
    if m["kind"] == "ratio":
        den = "COUNT(*)" if m["den"] == "__rows__" else "SUM(%s)" % _q(m["den"])
        sel.append("ROUND(%s(%s) / %s, 4) AS %s" % ("100.0 * " if m["pct"] else "1.0 * ", num, den,
                                                   _q(name + ("_pct" if m["pct"] else ""))))
    elif len(m["num"]) > 1:
        sel.append("%s AS %s" % (num, _q(name)))
    sel.append("COUNT(*) AS row_count")
    where = " AND ".join("%s = %s" % (_q(c), _lit(v)) for c, v in rq["slice"].items())
    return ("SELECT " + ",\n       ".join(sel) + "\nFROM " + TABLE + ("\nWHERE " + where if where else "") +
            "\nGROUP BY period\nORDER BY period")


# --------------------------------------------------------------------------------------------- the chart spec
def _menu() -> Dict[str, Any]:
    try:
        import nl_viz
        return {k: v for k, v in nl_viz.REGISTRY.items()}
    except Exception:  # noqa: BLE001
        return {}


_TITLE_OK_DIGITS = re.compile(r"\b(19|20)\d\d\b|\bQ[1-4]\b")


def check_chart(spec: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    """{"ok": True, "spec"} or {"ok": False, "reason"}: a chart from the menu, its arguments kept columns."""
    if not isinstance(spec, dict):
        return {"ok": False, "reason": "no chart spec"}
    menu = _menu()
    chart = spec.get("chart")
    if not isinstance(chart, str) or chart not in menu:
        return {"ok": False, "reason": "%s is not on the chart menu" % chart}
    args = spec.get("args") if isinstance(spec.get("args"), list) else []
    cols = set(_cols(state))
    hid = {h.lower() for h in state.get("hidden") or []}
    allowed = len(menu[chart].get("args") or []) + (12 if menu[chart].get("repeat") else 0)
    if len(args) > allowed:
        return {"ok": False, "reason": "%s takes at most %d arguments" % (chart, allowed)}
    for a in args:
        if not isinstance(a, str) or a not in cols or a.lower() in hid:
            return {"ok": False, "reason": "%r is not a kept column" % (a,)}
    title = spec.get("title") if isinstance(spec.get("title"), str) else ""
    title = re.sub(r"\s+", " ", title).strip()[:120]
    if re.search(r"\d", _TITLE_OK_DIGITS.sub("", title)):
        title = ""                      # a title never carries a figure
    return {"ok": True, "spec": {"chart": chart, "args": list(args), "title": title}}


def default_chart(state: Dict[str, Any], ex: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The engine's chart for the explained move: the split with the largest effect, as a bar of the measure's
    numerator (the dashboard track recomputes the ratio from measure_def)."""
    if not ex.get("ok"):
        return None
    m = ex["measure"]
    drv = ex.get("drivers") or []
    dim = drv[0]["dimension"] if drv else None
    menu = _menu()
    where = " in " + ", ".join(ex["slice"].values()) if ex.get("slice") else ""
    if dim and "bar_chart" in menu:
        # the bar draws the measure's base column (the menu's bar_chart takes one column); the pin's measure_def lets
        # the dashboard recompute the ratio itself, so the title names what is drawn
        return {"chart": "bar_chart", "args": [dim, m["num"][0]],
                "title": "%s by %s%s" % (_words(m["num"][0]).capitalize(), _words(dim), where)}
    if "line_chart" in menu:
        return {"chart": "line_chart", "args": [m["num"][0]], "title": m["label"][0].upper() + m["label"][1:]}
    return None


# --------------------------------------------------------------------------------------------- all of it
def answer(state: Dict[str, Any], question: str, sql: Any = "", chart_spec: Any = None, why: Any = "") -> Dict[str, Any]:
    """The SQL panel's record: {question, sql, sql_by: ai|engine, why, ai_refused, query, explain, chart, pin}."""
    question = re.sub(r"\s+", " ", str(question or "")).strip()[:300]
    out: Dict[str, Any] = {"version": 1, "question": question, "table": TABLE}
    if state.get("df") is None:
        out.update(ok=False, error="no file has been analysed")
        return out
    ai_sql = sql if isinstance(sql, str) and sql.strip() else ""
    ai_refused = ""
    query = None
    if ai_sql:
        query = run_sql(state, ai_sql)
        if query.get("ok"):
            out["sql"], out["sql_by"] = query["sql"], "ai"
            w = str(why or "")[:300]
            out["why"] = "" if re.search(r"\d", w) else w
        else:
            ai_refused = query.get("error") or "refused"
            query = None
    if query is None:
        es = engine_sql(state, question)
        if es:
            query = run_sql(state, es)
            out["sql"], out["sql_by"] = es, "engine"
            out["why"] = "The engine's own query: the asked measure by period in the asked slice."
        else:
            query = {"ok": False, "error": "the engine found no measure and date to write a query for"}
            out["sql"], out["sql_by"] = "", "engine"
    out["ai_refused"] = ai_refused
    out["query"] = query
    ex = explain(state, question)
    out["explain"] = ex
    chart = None
    chart_refused = ""
    if chart_spec is not None:
        c = check_chart(chart_spec, state)
        if c["ok"]:
            chart = c["spec"]
            chart["by"] = "ai"
        else:
            chart_refused = c["reason"]
    if chart is None:
        chart = default_chart(state, ex)
        if chart:
            chart["by"] = "engine"
    if chart and not chart.get("title"):
        d = default_chart(state, ex)
        chart["title"] = (d or {}).get("title") or ("Chart for: " + question)[:120]
    out["chart"] = chart
    out["chart_refused"] = chart_refused
    if chart:
        out["pin"] = {"chart": chart["chart"], "args": chart["args"], "title": chart["title"],
                      "why": "Answers: " + question, "source": "ask-sql", "question": question,
                      "sql": out.get("sql") or "", "filters": [{"column": c, "value": v} for c, v in (ex.get("slice") or {}).items()],
                      "measure_def": ex.get("measure") if ex.get("ok") else None,
                      "period": ex.get("period") if ex.get("ok") else None}
    out["ok"] = bool(query.get("ok") or ex.get("ok"))
    return out
