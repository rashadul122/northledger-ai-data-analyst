#!/usr/bin/env python3
"""NorthLedger Agent — an AI data analyst with full access to a client database.

Architecture (mirrors Hermes Agent's own loop, minus the kitchen sink):
  - OpenAI-style tool-calling loop against Ollama Cloud (glm-5.3)
  - TOOLS: run_sql (read-only enforced), profile_table, run_python (data work),
           forecast (OLS trend+seasonality with honest bands + seasonal-naive baseline),
           make_report (PDF/Excel/Word), finish (structured conclusion)
  - System prompt carries the rigor rules from the portfolio blueprint:
    no number without a query, cite row counts, disclose assumptions, honest baselines.

Usage:
  venv/bin/python agent.py --db client_data.db --session demo1
  venv/bin/python agent.py --db client_data.db --session demo2 --goal "..."
Transcript + artifacts land in sessions/<name>/ .
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sqlite3
import sys
import traceback

import requests

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- config
def load_env():
    env = {}
    p = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

ENV = load_env()
API_KEY = ENV.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_API_KEY", "")
BASE_URL = (ENV.get("OLLAMA_BASE_URL") or "https://ollama.com/v1").rstrip("/")
MODEL = os.environ.get("AGENT_MODEL", "glm-5.3")
MAX_TURNS = 40
MAX_TOOL_ROUNDS = 30

# ---------------------------------------------------------------- tools
DB_PATH = None
SESSION_DIR = None
ARTIFACTS = []  # [{"type": "pdf"|"xlsx"|"docx", "path": ..., "desc": ...}]


def _con():
    return sqlite3.connect(DB_PATH)


def tool_run_sql(sql):
    """Read-only SELECT against the client database. Returns rows as JSON (cap 100)."""
    s = (sql or "").strip().rstrip(";")
    if not s:
        return {"error": ("the 'sql' parameter was EMPTY. Re-issue the call with the full SQL text "
                          "in the 'sql' parameter, e.g. {\"sql\": \"SELECT COUNT(*) FROM nyc_311\"}. "
                          "Multi-statement strings are not allowed; one SELECT per call.")}
    if not re.match(r"(?is)^(select|with|pragma\s+table_info|explain\s+query\s+plan)\b", s):
        return {"error": "read-only: only SELECT/WITH/PRAGMA table_info allowed"}
    bad = re.search(r"(?i)\b(insert|update|delete|drop|alter|attach|create)\b", s)
    if bad:
        return {"error": f"read-only guard: forbidden keyword {bad.group(0)!r}"}
    con = _con()
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(100)]
        n_more = max(0, len(rows) == 100 and 100 or 0)
        return {"columns": cols, "rows": rows, "rowcap": 100,
                "note": "showing first 100 rows; use COUNT(*) for exact totals"}
    except sqlite3.Error as e:
        return {"error": f"SQL error: {e}"}
    finally:
        con.close()


def tool_profile_table(table):
    """Full column profile: type mix, nulls, distinct counts, sample values, duplicates."""
    con = _con()
    try:
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if table not in tabs:
            return {"tables": tabs, "error": f"table {table!r} not found"}
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]
        out = {"table": table, "rows": con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], "columns": {}}
        for c in cols:
            info = {}
            q = f'SELECT "{c}", COUNT(*) n FROM "{table}" GROUP BY 1 ORDER BY n DESC'
            grp = con.execute(q).fetchall()
            nulls = next((n for v, n in grp if v in (None, "", "N/A")), 0)
            info["null_like"] = nulls
            info["distinct"] = len(grp)
            info["top_values"] = [{"value": str(v)[:40], "count": n} for v, n in grp[:6]]
            out["columns"][c] = info
        # duplicate check on collision_id-ish col
        idc = next((c for c in cols if "collision" in c.lower() and "id" in c.lower()), None)
        if idc:
            d = con.execute(f'SELECT COUNT(*) FROM (SELECT "{idc}" FROM "{table}" GROUP BY "{idc}" HAVING COUNT(*)>1)').fetchone()[0]
            out["duplicate_" + idc] = d
        return out
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        con.close()


def tool_run_python(code):
    """Run analysis code with pandas/numpy against the client DB. DB_PATH + pd are in scope.
    print() output IS captured and returned in 'stdout'; the last expression's value in 'result'."""
    import ast
    import pandas as pd, numpy as np
    g = {"pd": pd, "np": np, "DB_PATH": DB_PATH, "SESSION_DIR": SESSION_DIR}
    buf = io.StringIO()

    class _Capture:
        def write(self, s):
            buf.write(s)
        def flush(self):
            pass
    g["print"] = lambda *a, **k: print(*a, **k, file=buf, flush=True) if False else buf.write(" ".join(str(x) for x in a) + "\n")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"error": f"syntax: {e}"}
    last = tree.body[-1] if tree.body else None
    last_expr = ast.Expression(last.value) if isinstance(last, ast.Expr) else None
    if last_expr is not None:
        to_exec = ast.Module(tree.body[:-1], type_ignores=[])
    else:
        to_exec = tree
    try:
        exec(compile(to_exec, "<agent>", "exec"), g)
        val = None
        if last_expr is not None:
            val = eval(compile(last_expr, "<agent>", "eval"), g)
        return {"result": _jsonable(val), "stdout": buf.getvalue()[:4000]}
    except Exception:
        return {"error": traceback.format_exc(limit=4)}


def tool_forecast(series_json, periods=12, freq="MS"):
    """Honest forecast: OLS trend + seasonal dummies; 80% band from residual sigma (sqrt-h);
    ALWAYS compared against the seasonal-naive baseline (last-season value) and reports both."""
    import numpy as np
    data = json.loads(series_json)  # [[date_str, value], ...]
    dates = [d[0] for d in data]
    vals = np.array([float(d[1]) for d in data], dtype=float)
    n = len(vals)
    if n < 25:
        return {"error": f"need >=25 points, got {n}"}
    # parse dates -> periods since start
    def pnum(s):
        s = s[:7]
        y, m = int(s[:4]), int(s[5:7])
        return (y * 12 + m)
    t0 = pnum(dates[0])
    t = np.array([pnum(d) - t0 for d in dates], dtype=float)
    mo = np.array([int(d[5:7]) for d in dates])
    X = np.column_stack([np.ones(n), t] + [(mo == m).astype(float) for m in range(2, 13)])
    beta, *_ = np.linalg.lstsq(X, vals, rcond=None)
    pred = X @ beta
    resid = vals - pred
    sigma = float(resid.std(ddof=X.shape[1]))
    mape = float((abs(resid) / np.abs(vals)).mean() * 100)
    fut_t, out = [], []
    last_t = t[-1]
    cur_y, cur_m = int(dates[-1][:4]), int(dates[-1][5:7])
    for h in range(1, periods + 1):
        cur_m += 1
        if cur_m == 13:
            cur_m = 1; cur_y += 1
        tt = last_t + h
        row = [1.0, tt] + [1.0 if cur_m == m else 0.0 for m in range(2, 13)]
        mu = float(np.dot(row, beta))
        band = 1.2816 * sigma * (h ** 0.5)
        naive = vals[n - 12 + (h - 1) % 12] if n >= 12 + ((h - 1) % 12) + 1 else None
        out.append({"date": f"{cur_y:04d}-{cur_m:02d}", "forecast": round(mu, 2),
                    "lo80": round(mu - band, 2), "hi80": round(mu + band, 2),
                    "seasonal_naive": round(float(naive), 2) if naive is not None else None})
    return {"model": "OLS trend + monthly seasonality", "n_history": n,
            "mape_fit_pct": round(mape, 2), "sigma": round(sigma, 2), "forecast": out}


def tool_make_report(kind, title, summary_md, body_md):
    """Generate a client-ready report file: pdf | xlsx | docx. Returns artifact path.
    body_md: markdown-ish (## sections, bullets '- ', bold **x**, tables via | a | b |)."""
    import pandas as pd
    kind = (kind or "").strip().lower()
    if kind not in ("pdf", "xlsx", "docx"):
        return {"error": "kind must be exactly one of: pdf, xlsx, docx (got %r)" % kind}
    if not title or not str(title).strip():
        return {"error": "title is required"}
    if not (body_md or "").strip() or len((body_md or "").strip()) < 200:
        return {"error": ("body_md is required and must contain the FULL report content "
                          "(sections, tables, numbers) — got %d chars. Re-issue the call with the "
                          "complete report body; at minimum: findings with exact counts, a table, "
                          "and a limitations section." % len((body_md or "").strip()))}
    if not (summary_md or "").strip():
        summary_md = _strip_md(body_md).split(".")[0][:300] + "."
    os.makedirs(os.path.join(SESSION_DIR, "artifacts"), exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(SESSION_DIR, "artifacts", f"{ts}-{slug}.{kind}")
    if kind == "pdf":
        from fpdf import FPDF
        pdf = FPDF()
        pdf.set_auto_page_break(True, 18)
        pdf.add_page()
        pdf.set_font("helvetica", "B", 17)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, "NorthLedger Insights - automated analysis", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_text_color(20, 20, 20)
        pdf.set_font("helvetica", "", 10.5)
        for line in (summary_md or "").splitlines():
            if line.strip():
                pdf.multi_cell(0, 6, _pdf_clean(line), new_x="LMARGIN", new_y="NEXT")
        _render_md_pdf(pdf, body_md)
        pdf.output(path)
    elif kind == "xlsx":
        wb = _xlsx_from_md(title, summary_md, body_md)
        wb.save(path)
    elif kind == "docx":
        from docx import Document
        doc = Document()
        doc.add_heading(title, 0)
        doc.add_paragraph("NorthLedger Insights - automated analysis")
        for line in (summary_md or "").splitlines():
            if line.strip():
                doc.add_paragraph(_pdf_clean(line))
        _render_md_docx(doc, body_md)
        doc.save(path)
    else:
        return {"error": "kind must be pdf, xlsx, or docx"}
    ARTIFACTS.append({"type": kind, "path": os.path.relpath(path, SESSION_DIR), "desc": title})
    return {"artifact": os.path.relpath(path, SESSION_DIR), "type": kind, "title": title}


def _render_md_pdf(pdf, body_md):
    lines = body_md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("## "):
            pdf.ln(2); pdf.set_font("helvetica", "B", 12.5)
            pdf.multi_cell(0, 7, _pdf_clean(ln[3:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("helvetica", "", 10.5)
        elif ln.startswith("### "):
            pdf.ln(1); pdf.set_font("helvetica", "B", 11)
            pdf.multi_cell(0, 6.5, _pdf_clean(ln[4:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("helvetica", "", 10.5)
        elif ln.startswith("- "):
            pdf.set_font("helvetica", "", 10.5)
            pdf.multi_cell(0, 5.6, "  - " + _pdf_clean(_strip_md(ln[2:])), new_x="LMARGIN", new_y="NEXT")
        elif ln.startswith("|"):
            # collect table block
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", c or "-") for c in row):
                    tbl.append(row)
                i += 1
            i -= 1
            pdf.ln(1)
            _pdf_table(pdf, tbl)
            pdf.ln(1)
        elif ln.strip():
            pdf.set_font("helvetica", "", 10.5)
            pdf.multi_cell(0, 5.8, _pdf_clean(_strip_md(ln)), new_x="LMARGIN", new_y="NEXT")
        i += 1


def _pdf_table(pdf, tbl):
    if not tbl:
        return
    ncol = max(len(r) for r in tbl)
    widths = [190 / ncol] * ncol
    pdf.set_font("helvetica", "B", 9.5)
    first = True
    for r in tbl:
        for j, c in enumerate(r):
            if j >= ncol: break
            pdf.cell(widths[j], 6, _pdf_clean(_strip_md(str(c)))[:38], border=1,
                     new_x="RIGHT", new_y="TOP")
        pdf.ln(6)
        if first:
            pdf.set_font("helvetica", "", 9.5)
            first = False


def _xlsx_from_md(title, summary_md, body_md):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Report"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=15)
    ws["A2"] = "NorthLedger Insights - automated analysis"
    ws["A2"].font = Font(italic=True, color="777777")
    r = 4
    for line in (summary_md or "").splitlines():
        if line.strip():
            ws.cell(r, 1, _strip_md(line)).alignment = openpyxl.styles.Alignment(wrap_text=True)
            r += 1
    lines = body_md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("#"):
            ws.cell(r, 1, _strip_md(ln.lstrip("# "))).font = Font(bold=True, size=12)
            r += 2
        elif ln.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", c or "-") for c in row):
                    tbl.append(row)
                i += 1
            i -= 1
            for row in tbl:
                for j, c in enumerate(row):
                    cell = ws.cell(r, j + 1, _strip_md(c))
                    if r == ws.max_row or tbl.index(row) == 0:
                        pass
                r += 1
            r += 1
        elif ln.strip():
            ws.cell(r, 1, _strip_md(ln))
            r += 1
        i += 1
    ws.column_dimensions["A"].width = 70
    return wb


def _render_md_docx(doc, body_md):
    lines = body_md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("## "):
            doc.add_heading(_strip_md(ln[3:]), 1)
        elif ln.startswith("### "):
            doc.add_heading(_strip_md(ln[4:]), 2)
        elif ln.startswith("- "):
            doc.add_paragraph(_strip_md(ln[2:]), style="List Bullet")
        elif ln.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", c or "-") for c in row):
                    tbl.append(row)
                i += 1
            i -= 1
            if tbl:
                t = doc.add_table(rows=len(tbl), cols=len(tbl[0]))
                t.style = "Light Grid Accent 1"
                for ri, row in enumerate(tbl):
                    for ci, c in enumerate(row[:len(tbl[0])]):
                        t.cell(ri, ci).text = _strip_md(c)
        elif ln.strip():
            doc.add_paragraph(_strip_md(ln))
        i += 1


def _strip_md(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    return s


def _pdf_clean(s):
    # fpdf2 (helvetica) has no glyph coverage for smart punctuation/symbols
    repl = {
        "\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...",
        "\u2265": ">=", "\u2264": "<=", "\u2260": "!=", "\u00b1": "+/-",
        "\u2192": "->", "\u2190": "<-", "\u00d7": "x", "\u00f7": "/",
        "\u2248": "~", "\u2261": "=", "\u2211": "sum", "\u221a": "sqrt",
        "\u2022": "-", "\u25cf": "-", "\u25b8": ">", "\u00a0": " ",
        "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2070": "0",
        "\u00b2": "^2", "\u00b3": "^3", "\u00bd": " 1/2", "\u00bc": " 1/4",
        "\u20ac": "EUR ", "\u00a3": "GBP ", "\u00b0": " deg",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    # drop anything outside latin-1 printable (helvetica core font has no
    # Greek/CJK/emoji glyphs — even letters crash fpdf2)
    out = []
    for ch in s:
        o = ord(ch)
        if 32 <= o <= 126 or o in (10,):
            out.append(ch)
        elif o in (233, 246, 252, 228, 252):  # common latin-1 accented chars helvetica does have
            out.append(ch)
        else:
            out.append("?")
    return _strip_md("".join(out))


def _jsonable(v):
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
    except Exception:
        pass
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)[:2000]


TOOLS = [
    {"name": "run_sql", "description": "Run a read-only SELECT query against the client database (SQLite). Use COUNT(*) for totals; caps at 100 rows returned.",
     "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "profile_table", "description": "Profile a table: row count, per-column null-like counts, distinct values, top values, duplicate-ID check.",
     "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}},
    {"name": "run_python", "description": "Run Python analysis code. In scope: pd (pandas), np (numpy), DB_PATH (the SQLite file). Return the last expression's value.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "forecast", "description": "Honest monthly forecast (OLS trend + seasonality, 80% bands, seasonal-naive baseline comparison). Input: JSON array of [date 'YYYY-MM-DD', value] pairs, ordered oldest first.",
     "parameters": {"type": "object", "properties": {"series_json": {"type": "string"}, "periods": {"type": "integer", "default": 12}}, "required": ["series_json"]}},
    {"name": "make_report", "description": "Generate a report file (pdf/xlsx/docx) from markdown-ish content. Use for the final deliverable.",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["pdf", "xlsx", "docx"]},
        "title": {"type": "string"}, "summary_md": {"type": "string"}, "body_md": {"type": "string"}},
        "required": ["kind", "title", "summary_md", "body_md"]}},
]

SYSTEM_PROMPT = """You are the NorthLedger Agent: an AI data analyst with full read access to a client's database.

Mission: take the user's question about their data, investigate rigorously, and deliver a decision-ready answer with honest numbers.

Rules (from the NorthLedger rigor standard):
1. ALWAYS look at the data before speaking. Profile tables first; never assume column meanings.
2. Every number you state must come from a query you actually ran (cite row counts).
3. Data is messy: expect mixed date formats, duplicates, casing chaos, null-like values ('', 'N/A', padded spaces). Clean explicitly and say what you did.
4. Use run_sql for facts, run_python for heavier analysis, forecast for predictions. Compare forecasts to the seasonal-naive baseline and state which wins.
5. Uncertainty is mandatory: give bands or ranges for any forward-looking number. Never claim precision the data does not support.
6. When done, call make_report to produce the deliverable file(s) the user asked for, then give a final answer structured as: What changed / What it means / What to do (numbers first, plain language, one short paragraph each).
7. Disclose limitations honestly in the report (small samples, unmodeled factors, data quality gaps).
8. Budget discipline: you have plenty of tool calls, but do NOT explore endlessly. Aim to finish
   the investigation within ~15 rounds and RESERVE the final rounds for make_report. The requested
   deliverable file is a hard requirement — a session that ends without it is a failed session.
9. Never create files in the working directory; every artifact goes through make_report. If you
   must store intermediate state, keep it in memory (Python variables), never on disk.

Keep tool calls purposeful. Finish with a concise, human answer - never just 'done'."""

# ---------------------------------------------------------------- loop
def chat(messages, tools=TOOLS):
    import time as _time
    last_err = None
    for attempt in range(5):
        try:
            r = requests.post(f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages, "tools": tools, "tool_choice": "auto"},
                timeout=300)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 500 and attempt < 4:
                wait = 8 * (attempt + 1)
                print(f"LLM 500 (attempt {attempt+1}), retrying in {wait}s...", flush=True)
                _time.sleep(wait)
                continue
            raise RuntimeError(f"LLM {r.status_code}: {r.text[:300]}")
        except requests.RequestException as e:
            last_err = e
            if attempt < 4:
                _time.sleep(8 * (attempt + 1))
                continue
            raise RuntimeError(f"LLM request failed after retries: {e}")
    raise RuntimeError(f"LLM failed after retries: {last_err}")


def dispatch(name, args):
    name = name.strip()
    if name == "run_sql":
        return tool_run_sql(args.get("sql", ""))
    if name == "profile_table":
        return tool_profile_table(args.get("table", ""))
    if name == "run_python":
        return tool_run_python(args.get("code", ""))
    if name == "forecast":
        return tool_forecast(args.get("series_json", "[]"), int(args.get("periods", 12)))
    if name == "make_report":
        return tool_make_report(args.get("kind", "pdf"), args.get("title", "Report"),
                                args.get("summary_md", ""), args.get("body_md", ""))
    return {"error": f"unknown tool {name}"}


def run_session(db_path, session, goal, transcript_path):
    global DB_PATH, SESSION_DIR
    DB_PATH = db_path
    SESSION_DIR = os.path.dirname(transcript_path)
    os.makedirs(SESSION_DIR, exist_ok=True)
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": goal},
    ]
    transcript = {"session": session, "goal": goal, "model": MODEL, "turns": []}
    tool_rounds = 0
    for turn in range(MAX_TURNS):
        resp = chat(msgs)
        msg = resp["choices"][0]["message"]
        entry = {"role": "assistant", "content": msg.get("content") or "",
                 "tool_calls": []}
        calls = msg.get("tool_calls") or []
        if calls:
            tool_rounds += 1
            if tool_rounds > MAX_TOOL_ROUNDS:
                msgs.append({"role": "user", "content": "(tool budget exhausted; wrap up now with your final answer)"})
                entry["content"] += "\n[tool budget exhausted]"
                transcript["turns"].append(entry)
                continue
        msgs.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": calls if calls else None})
        # normalize for later messages
        if calls:
            msgs[-1]["tool_calls"] = calls
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            # tolerate alternate parameter names the model may use
            if name == "run_sql" and "sql" not in args:
                for alt in ("query", "statement", "sql_query", "q"):
                    if alt in args and args[alt]:
                        args["sql"] = args[alt]
                        break
            result = dispatch(name, args)
            entry["tool_calls"].append({"name": name, "args": args, "result": result})
            msgs.append({"role": "tool", "tool_call_id": c.get("id", "x"),
                         "content": json.dumps(result, default=str)[:8000]})
        transcript["turns"].append(entry)
        json.dump(transcript, open(transcript_path, "w"), indent=1, default=str)
        if not calls:
            break
    return transcript


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "client_data.db"))
    ap.add_argument("--session", default="demo1")
    ap.add_argument("--goal", required=True)
    a = ap.parse_args()
    sdir = os.path.join(HERE, "sessions", a.session)
    os.makedirs(sdir, exist_ok=True)
    tp = os.path.join(sdir, "transcript.json")
    t = run_session(a.db, a.session, a.goal, tp)
    print("SESSION DONE:", a.session, "| turns:", len(t["turns"]), "| artifacts:", [x["path"] for x in ARTIFACTS])
    print("transcript:", tp)

if __name__ == "__main__":
    main()