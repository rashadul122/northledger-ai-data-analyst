"""Ask your data, with SQL (Track B, plan/DASHBOARD-DESIGN.md; CONTRACT §5.16): the SQL fence and the engine's own plan
for a question. It extends the analyst loop's "Ask your data" (engine/nl_analyst.py; docs/ai-analyst/PROTOCOL.md): the
SQL and the driver split are typed steps of that loop (`sql`, `rate_mix`), so their numbers live in its ledger and its
insight, story and figure machinery apply.

The "warehouse" is the rows the engine kept, as the analyst loop reads them (nl_browser._analysis_frame: no withheld or
coded column is in that frame; nl_analyst.op_sql also leaves out the free-text columns), landed in an in-memory SQLite
table named kept_rows. Nothing here sends a row anywhere.

  check_sql(sql, cols, hidden)   the static check (one read-only SELECT/WITH; no PRAGMA/ATTACH/DETACH/INSERT/UPDATE/
                                 DELETE/CREATE/DROP/ALTER/REPLACE/VACUUM/load_extension; only kept_rows and its columns).
                                 The worker's /analyst runs the same rules (src/analyst.js checkSql) on a planner's `sql`
                                 step; nl_analyst._check_step runs this one again; the SQLite authorizer is the real fence.
  run_sql(state, sql)            runs a query under the authorizer: reads of kept_rows only, withheld/coded columns denied,
                                 functions from an allowlist, nothing that writes; PRAGMA query_only; a row cap (ROW_CAP
                                 returned) and a time cap (progress handler). Small cells: a result row aggregating fewer
                                 than SMALL_CELL source rows is hidden when the engine can count them (the outer SELECT
                                 reads kept_rows directly), and the answer says plainly when it cannot.
  read_question(state, q)        the asked move by the engine's rules: the measure (gross margin = (revenue - cost) /
                                 revenue; a price; a named flow or level), the period grain, the slice (values of grouping
                                 columns the question names, e.g. EMEA).
  engine_sql(state, q)           the query the engine writes for that move: the measure by period in the slice.
  plan_steps(state, q)           the steps the engine plans when no AI planner does: `sql`, then `rate_mix` (a ratio) or
                                 the loop's own `change`/`mix` (a flow/an average) by each grouping column.

state: {"df", "kinds", "shape", "hidden"} (nl_browser._LAST_ANALYST, or nl_analyst.op_sql's own).
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
    key = state.get("key", id(df))
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
    if not named:
        # a measure named in other words ("drivers in fatal collisions per billion miles" for "Number of drivers involved in fatal
        # collisions per billion miles"): the number column sharing the most content words with the question, two at least
        stop = {"the", "of", "in", "and", "or", "per", "for", "who", "were", "was", "which", "what", "has", "have", "had", "most",
                "least", "highest", "lowest", "with", "by", "a", "an", "are", "is", "do", "does", "than", "that", "this"}
        qw = {w[:6] for w in re.findall(r"[a-z]+", q) if len(w) > 2 and w not in stop}
        best, sc = None, 1
        for c in nums:
            cw = {w[:6] for w in re.findall(r"[a-z]+", _words(c)) if len(w) > 2 and w not in stop}
            k = len(qw & cw) - 0.05 * len(cw - qw)
            if k > sc:
                best, sc = c, k
        if best is not None:
            named = [best]
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
    elif (re.search(r"\byears?\b|annual|yearly", q) and not re.search(r"(last|a) year (ago|earlier)", q)) or \
            re.search(r"\b(?:in|for|during)\s+(?:19|20)\d\d\b", q):
        grain = "Y"
    else:
        grain = _natural_grain(state)
        notes.append("No period was named, so the engine compares %s." % {"Y": "years", "M": "months", "Q": "quarters"}[grain])
    # the slice: values of a grouping column the question names (EMEA, Online); the longest match in each column
    sl: Dict[str, str] = {}
    date = _date_col(state)
    for c in _cols(state):
        if kinds.get(c) != "text" or c == date:
            continue
        vals = df[c].dropna().astype(str)
        if vals.nunique() > SLICE_VALUES_MAX:
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
    # a ranking (which country, the largest, the top 10, the lowest): the members of a grouping column the question did not
    # pin, ordered by the measure in one period
    rank = None
    if _RANK.search(q) and not _PERIOD_RANK.search(q):
        hint = state.get("hint") or {}
        pool = [str(hint.get("entity"))] if hint.get("entity") else []
        pool += [str(d) for d in (shape.get("dimensions") or [])]
        pool += [c for c in _cols(state) if kinds.get(c) == "text" and c != date]
        named_dim = [c for c in dict.fromkeys(pool) if c in df.columns and c not in sl and _mentions_dim(q, c)]
        by = named_dim[0] if named_dim else next((c for c in dict.fromkeys(pool) if c in df.columns and c not in sl), None)
        # no members: a ranking of the dates of one series (one reading a date), else none (the period table answers)
        rank = None if (by is None and not _one_row_each(state, [], "D")) else {"by": by, "desc": not _LOW.search(q),
                "n": int(next((m.group(1) for m in [re.search(r"\b(?:top|bottom|first|last)\s+(\d{1,2})\b", q)] if m), 10))}
    elif re.search(r"\bwhen\b.*\b(high|low|peak|bottom|most|least|max|min)", q) or re.search(r"\b(peak|peaked|record high|record low)\b", q):
        rank = {"by": None, "desc": not re.search(r"\b(low|lowest|least|min|minimum|bottom|record low|trough)\b", q), "n": 10}
    # "compared with 2019", "2019 vs 2025": the years named, beside the latest one
    years = sorted(set(re.findall(r"\b((?:19|20)\d\d)\b", q)))
    compare = years if years and re.search(r"\b(compare[sd]?|comparison|versus|vs\.?|than|against|since|before|now|today|latest)\b", q) \
        and not re.search(r"\bsince\s+(?:19|20)\d\d\b", q) else []
    return {"measure": measure, "grain": grain, "slice": sl, "notes": notes, "period": _read_period(q, grain), "rank": rank,
            "compare": compare}


_RANK = re.compile(r"\bwhich\s+[a-z ]{0,30}?\b(has|had|have|is|was|were|are|grew|rose|fell|leads?|led)\b|\b(largest|biggest|"
                   r"highest|most|top|smallest|lowest|least|fewest|bottom|rank|ranking|ranked|leading|leaders?)\b")
_LOW = re.compile(r"\b(smallest|lowest|least|fewest|bottom)\b")
# "which month had the most sales", "the top 3 quarters": a question about periods, answered by the period table, never a ranking of members
_PERIOD_RANK = re.compile(r"\b(?:which|what|top\s+\d{1,2}|best|worst|busiest|slowest)\s+(?:months?|quarters?|years?|weeks?|days?|periods?|dates?)\b")
SLICE_VALUES_MAX = 5000


def _mentions_dim(q: str, c: str) -> bool:
    """The question names the grouping by its header or its singular (country for Country Name, state for State)."""
    for w in re.findall(r"[a-z]+", _words(c)):
        if len(w) >= 4 and w not in ("name", "code", "label", "type") and re.search(r"\b%s(?:s|es|ies)?\b" % re.escape(w.rstrip("s")), q):
            return True
    return False


def _natural_grain(state: Dict[str, Any]) -> str:
    """The data's own grain when the question names none: years for a table of one date a year (every date the 1st of January),
    months for one a month, else quarters."""
    import pandas as pd
    date = _date_col(state)
    df = state.get("df")
    if date is None or df is None or date not in df.columns:
        return "Q"
    d = pd.to_datetime(df[date], errors="coerce").dropna()
    if len(d) < 2:
        return "Q"
    if bool(((d.dt.month == 1) & (d.dt.day == 1)).all()):
        return "Y"
    if bool((d.dt.day == 1).all()) and not d.duplicated().any():
        return "M"                                       # one reading a month (a FRED series): its months, never quarters of three
    return "Q"


_MONTHS = {m: i + 1 for i, m in enumerate(("january february march april may june july august september october november "
                                             "december").split())}


def _read_period(q: str, grain: str) -> Optional[str]:
    """A named period in the question, as nl_analyst's `period` argument (2026-Q3, 2026-09, 2026, since:2024[-03]);
    None for "this/last/latest quarter" and the like: the latest complete period, the steps' default."""
    m = re.search(r"\bsince\s+(?:([a-z]+)\s+)?((?:19|20)\d\d)\b", q)
    if m:
        mo = _MONTHS.get((m.group(1) or "").lower())
        return "since:%s" % m.group(2) + ("-%02d" % mo if mo else "")
    m = re.search(r"\bq([1-4])\s*(?:of\s+)?((?:19|20)\d\d)\b", q) or re.search(r"\b((?:19|20)\d\d)\s*-?\s*q([1-4])\b", q)
    if m:
        a, b = m.group(1), m.group(2)
        y, n = (b, a) if len(a) == 1 else (a, b)
        return "%s-Q%s" % (y, n)
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d\d)\b", q)
    if m:
        return "%s-%02d" % (m.group(2), _MONTHS[m.group(1)])
    m = re.search(r"\b(?:in|for|during)\s+((?:19|20)\d\d)\b", q)
    if m and grain == "Y":
        return m.group(1)
    return None


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
    rk0 = rq.get("rank")
    if m and not date and rk0 and rk0.get("by") and len(m["num"]) == 1 and m["den"] in (None, "__rows__"):
        # a table with no dates (a state a row): the members ranked by the measure, their own rows when each is one row
        df = state["df"]
        by_c = rk0["by"]
        conds = ["%s = %s" % (_q(c), _lit(v)) for c, v in rq["slice"].items()]
        where = ("\nWHERE " + " AND ".join(conds)) if conds else ""
        n = max(1, min(20, int(rk0["n"])))
        if by_c in df.columns and not df[by_c].astype(str).duplicated().any():
            return "SELECT %s,\n       %s\nFROM %s%s\nORDER BY %s %s\nLIMIT %d" % (_q(by_c), _q(m["num"][0]), TABLE, where, _q(m["num"][0]),
                                                                          "DESC" if rk0["desc"] else "ASC", n)
        agg = "ROUND(AVG(%s), 4)" % _q(m["num"][0]) if m["den"] == "__rows__" else "SUM(%s)" % _q(m["num"][0])
        return ("SELECT %s,\n       %s AS %s,\n       COUNT(*) AS row_count\nFROM %s%s\nGROUP BY %s\nORDER BY 2 %s\nLIMIT %d"
                % (_q(by_c), agg, _q(m["num"][0]), TABLE, where, _q(by_c), "DESC" if rk0["desc"] else "ASC", n))
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
    conds = ["%s = %s" % (_q(c), _lit(v)) for c, v in rq["slice"].items()]
    single = len(m["num"]) == 1 and m["den"] in (None, "__rows__")
    v = _q(m["num"][0])
    if single and rq.get("compare"):
        # the years the question names beside the latest: each year's average (a level, a rate) or total (a flow), one row a year
        latest = _latest_period(state, "Y")
        ys = sorted(set(rq["compare"]) | ({latest} if latest else set()))
        agg = "ROUND(AVG(%s), 4)" % v if m["den"] == "__rows__" else "SUM(%s)" % v
        name_y = ("average_" if m["den"] == "__rows__" else "total_") + re.sub(r"[^a-z0-9]+", "_", _words(m["num"][0])).strip("_")
        return ("SELECT strftime('%%Y', %s) AS period,\n       %s AS %s,\n       COUNT(*) AS row_count\nFROM %s\nWHERE %s\n"
                "GROUP BY period\nORDER BY period" % (d, agg, _q(name_y), TABLE, " AND ".join(conds + [
                    "strftime('%%Y', %s) IN (%s)" % (d, ", ".join(_lit(y) for y in ys))])))
    if single and not rq["slice"] and _one_row_each(state, [], "D"):
        # one series, one reading a date (a FRED download): its own rows, never sums of three months hidden as small cells
        fmt_d = {"Y": "%Y", "M": "%Y-%m"}.get(_natural_grain(state), "%Y-%m-%d")
        per = _period_cond(d, rq.get("period"))
        rk = rq.get("rank")
        sel_s = "SELECT strftime('%s', %s) AS period,\n       %s\nFROM %s%s" % (fmt_d, d, v, TABLE,
                                                                             ("\nWHERE " + per) if per else "")
        if rk and rk.get("by") is None:
            return sel_s + "\nORDER BY %s %s\nLIMIT %d" % (v, "DESC" if rk["desc"] else "ASC", max(1, min(20, int(rk["n"]))))
        if rq["grain"] == "Y" and not per:
            agg = "ROUND(AVG(%s), 4)" % v if m["den"] == "__rows__" else "SUM(%s)" % v
            return ("SELECT strftime('%%Y', %s) AS period,\n       %s AS %s,\n       COUNT(*) AS row_count\nFROM %s\nGROUP BY period\n"
                    "ORDER BY period" % (d, agg, v, TABLE))
        return sel_s + "\nORDER BY period"
    per = _period_cond(d, rq.get("period"))
    rk = rq.get("rank")
    if rk and rk.get("by") is not None:
        # one period, the members ranked: the named period, else the latest one in the file
        if per is None or str(rq.get("period") or "").startswith("since:"):
            latest = _latest_period(state, rq["grain"])
            per = ("%s = %s" % (pe, _lit(latest))) if latest else per
        if per:
            conds.append(per)
        val = sel[1:-1]
        if m["kind"] == "ratio" and m["den"] == "__rows__":
            # one value a member in the period: the member's own figure (its average when the period holds several rows)
            val = ["ROUND(AVG(%s), 4) AS %s" % (_q(m["num"][0]), _q(m["num"][0]))]
        if m["kind"] == "ratio" and m["den"] != "__rows__":
            order = _q(name + ("_pct" if m["pct"] else ""))         # the ratio, recomputed per member
        elif len(m["num"]) > 1:
            order = _q(name)                                         # gross profit: revenue less cost
        else:
            order = _q(m["num"][0])
        by = _q(rk["by"])
        if len(m["num"]) == 1 and m["den"] in (None, "__rows__") and _one_row_each(state, [rk["by"]], rq["grain"]):
            # one row a member a period (a country's population in 2025): the rows themselves, in order, never an aggregate of one
            return ("SELECT %s,\n       %s\nFROM %s%s\nORDER BY %s %s\nLIMIT %d" % (
                by, _q(m["num"][0]), TABLE, ("\nWHERE " + " AND ".join(conds)) if conds else "", _q(m["num"][0]),
                "DESC" if rk["desc"] else "ASC", max(1, min(20, int(rk["n"])))))
        return ("SELECT " + ",\n       ".join(["%s" % by] + val + ["COUNT(*) AS row_count"]) + "\nFROM " + TABLE +
                ("\nWHERE " + " AND ".join(conds) if conds else "") + "\nGROUP BY %s\nORDER BY %s %s\nLIMIT %d" % (
                    by, order, "DESC" if rk["desc"] else "ASC", max(1, min(20, int(rk["n"])))))
    if per:
        conds.append(per)
    where = " AND ".join(conds)
    if rq["slice"] and len(m["num"]) == 1 and m["den"] in (None, "__rows__") and \
            _one_row_each(state, list(rq["slice"]), rq["grain"], rq["slice"]):
        # one row a period in the asked slice (China's population each year): its rows, never an aggregate of one row
        return ("SELECT %s AS period,\n       %s\nFROM %s\nWHERE %s\nORDER BY period" % (pe, _q(m["num"][0]), TABLE, where))
    return ("SELECT " + ",\n       ".join(sel) + "\nFROM " + TABLE + ("\nWHERE " + where if where else "") +
            "\nGROUP BY period\nORDER BY period")


def _one_row_each(state: Dict[str, Any], keys: List[str], grain: str, where: Optional[Dict[str, str]] = None) -> bool:
    """Whether the kept rows hold at most one row for each value of keys in each period of the grain (in the slice where)."""
    import pandas as pd
    df = state.get("df")
    date = _date_col(state)
    if df is None or date is None or any(k not in df.columns for k in keys):
        return False
    try:
        f = df
        for c, v in (where or {}).items():
            f = f[f[c].astype(str).str.strip() == str(v)]
        if not len(f):
            return False
        d = pd.to_datetime(f[date], errors="coerce")
        per = d.dt.year.astype(str) if grain == "Y" else (d.dt.to_period("Q").astype(str) if grain == "Q" else
                                                           d.dt.to_period("M").astype(str) if grain == "M" else d.astype(str))
        g = pd.DataFrame({**{k: f[k].astype(str) for k in keys}, "_p": per})
        return int(g.groupby(list(keys) + ["_p"]).size().max()) <= 1 if keys else not per.duplicated().any()
    except Exception:  # noqa: BLE001
        return False


def _period_cond(d: str, period: Optional[str]) -> Optional[str]:
    """The asked period as a condition on the date column d (quoted): 2025, 2025-Q3, 2025-09, since:2000[-03]."""
    if not period:
        return None
    m = re.fullmatch(r"since:(\d{4})(?:-(\d{2}))?", period)
    if m:
        return "%s >= %s" % (d, _lit("%s-%s-01" % (m.group(1), m.group(2) or "01")))
    if re.fullmatch(r"\d{4}", period):
        return "strftime('%%Y', %s) = %s" % (d, _lit(period))
    m = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if m:
        return "strftime('%%Y', %s) = %s AND ((CAST(strftime('%%m', %s) AS INTEGER) + 2) / 3) = %s" % (d, _lit(m.group(1)), d, m.group(2))
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return "strftime('%%Y-%%m', %s) = %s" % (d, _lit(period))
    return None


def _latest_period(state: Dict[str, Any], grain: str) -> Optional[str]:
    """The latest period of the file at the grain, as the SQL's period expression writes it (2025, 2025-Q4, 2025-12)."""
    import pandas as pd
    date = _date_col(state)
    df = state.get("df")
    if date is None or df is None:
        return None
    d = pd.to_datetime(df[date], errors="coerce").dropna()
    if not len(d):
        return None
    x = d.max()
    return {"Y": "%d" % x.year, "M": "%d-%02d" % (x.year, x.month), "Q": "%d-Q%d" % (x.year, (x.month + 2) // 3)}[grain]


# --------------------------------------------------------------------------------------------- the engine's own plan
GRAIN_OF = {"Q": "quarter", "M": "month", "Y": "year"}


def plan_steps(state: Dict[str, Any], question: str, dims: Optional[List[str]] = None, max_steps: int = 4) -> List[Dict[str, Any]]:
    """The analyst steps the engine plans for a question when no AI planner does (the AI is off, or its round failed):
    one `sql` step (engine_sql: the asked measure by period in the asked slice), then the move split by the shape's
    grouping columns: `rate_mix` for a ratio (gross margin, a price per unit, an average), the loop's own `change`
    with by for a flow. Steps in nl_analyst's typed form {op, args, why}; run_steps checks them like any planner's."""
    if state.get("df") is None:
        return []
    rq = read_question(state, question)
    m = rq["measure"]
    out: List[Dict[str, Any]] = []
    sql = engine_sql(state, question)
    if sql:
        out.append({"op": "sql", "args": {"sql": sql}, "why": "the asked measure by period in the asked slice, as SQL"})
    if not m or rq.get("rank") or rq.get("compare"):
        return out                      # a ranking is answered by its query: the members in order
    where = {c: v for c, v in list(rq["slice"].items())[:2]}
    when = {"grain": GRAIN_OF[rq["grain"]]}
    if rq.get("period"):
        when["period"] = rq["period"]
        if rq["period"].startswith("since:"):
            when.pop("grain")
    shape = state.get("shape") or {}
    pool = [d for d in (dims if dims is not None else (shape.get("dimensions") or [])) if d not in rq["slice"]]
    for by in pool[: max(0, max_steps - len(out))]:
        if m["kind"] == "ratio" and m["den"] == "__rows__":
            # an average: the loop's own mix op splits it by rows
            out.append({"op": "mix", "args": dict({"measure": m["num"][0], "by": by}, **when),
                        "why": "the change of the average split into inside the groups and between them, by " + by})
        elif m["kind"] == "ratio":
            args: Dict[str, Any] = dict({"measure": m["num"][0], "denominator": m["den"], "by": by}, **when)
            if len(m["num"]) > 1:
                args["less"] = m["num"][1]
            if where:
                args["where"] = dict(where)
            out.append({"op": "rate_mix", "args": args, "why": "the change of the ratio split into rate and mix by " + by})
        else:
            args = dict({"measure": m["num"][0], "by": by}, **when)
            if where:
                args["where"] = dict(where)
            out.append({"op": "change", "args": args, "why": "each group's part of the change, by " + by})
    return out[:max_steps]
