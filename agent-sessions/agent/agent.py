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
AGENT_PROVIDER = os.environ.get("AGENT_PROVIDER", "ollama").strip().lower()
if AGENT_PROVIDER == "deepseek":
    API_KEY = ENV.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    BASE_URL = "https://api.deepseek.com/v1"
    MODEL = os.environ.get("AGENT_MODEL", "deepseek-chat")
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
    # parse dates -> periods since start (accepts 'YYYY-MM' strings or YYYY ints)
    def pnum(s):
        s = str(s)
        s = s[:7]
        y, m = int(s[:4]), int(s[5:7] or 1)
        return (y * 12 + m)
    t_all = [pnum(d) for d in dates]
    t_mid = sorted(t_all)[len(t_all) // 2]  # center time to avoid overflow on long spans
    t = np.array([x - t_mid for x in t_all], dtype=float)
    months = [int(str(d)[5:7]) if len(str(d)) >= 7 and str(d)[5:7].isdigit() else None for d in dates]
    annual = all(m is None for m in months)
    mo = np.array([m or 1 for m in months])
    if annual:
        X = np.column_stack([np.ones(n), t])
    else:
        X = np.column_stack([np.ones(n), t] + [(mo == m).astype(float) for m in range(2, 13)])
    beta, *_ = np.linalg.lstsq(X, vals, rcond=None)
    pred = X @ beta
    resid = vals - pred
    sigma = float(resid.std(ddof=X.shape[1]))
    mape = float((abs(resid) / np.abs(vals)).mean() * 100)
    fut_t, out = [], []
    last_t = t[-1]
    _last = str(dates[-1])
    cur_y, cur_m = int(_last[:4]), int(_last[5:7] or 1)
    for h in range(1, periods + 1):
        if annual:
            cur_y += 1
            row = [1.0, last_t + 12 * h]
            mu = float(np.dot(row, beta))
            band = 1.2816 * sigma * (h ** 0.5)
            naive = vals[n - 1] if n >= 1 else None
            out.append({"date": str(cur_y), "forecast": round(mu, 2),
                        "lo80": round(mu - band, 2), "hi80": round(mu + band, 2),
                        "seasonal_naive": round(float(naive), 2) if naive is not None else None})
        else:
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
    return {"model": ("OLS trend (annual series)" if annual else "OLS trend + monthly seasonality"), "n_history": n,
            "mape_fit_pct": round(mape, 2), "sigma": round(sigma, 2), "forecast": out}


def _save_chart_png(fig_or_ax, title):
    """Save a matplotlib fig to the session artifacts dir; return path."""
    import matplotlib
    matplotlib.use("Agg")
    os.makedirs(os.path.join(SESSION_DIR, "artifacts"), exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "chart").lower()).strip("-")[:50]
    path = os.path.join(SESSION_DIR, "artifacts", f"{ts}-{slug}.png")
    fig = fig_or_ax
    fig.savefig(path, dpi=140, bbox_inches="tight")
    matplotlib.pyplot.close(fig)
    return path



def tool_web_search(query, num=5):
    """Search the web via the portfolio's search proxy (Cloudflare Worker holding the Serper key,
    northledger-insight-proxy POST /search), so a distant user running this agent needs no key of
    their own. A local SERPER_API_KEY (env or ~/StyleCast/env) is tried first and wins when it
    works; on any failure the proxy is the fallback. Returns top organic results with
    title/link/snippet (+ answer box + knowledge graph when present) for context gathering and
    citations."""
    import requests as _rq
    num = int(min(max(num, 1), 10))

    def _direct(key):
        r = _rq.post("https://google.serper.dev/search",
                     headers={"X-API-KEY": key, "Content-Type": "application/json"},
                     json={"q": query, "num": num}, timeout=30)
        if r.status_code != 200:
            return {"failed": f"serper {r.status_code}: {r.text[:150]}"}
        return _shape(r.json())

    def _shape(d):
        out = []
        for item in (d.get("organic") or [])[:num]:
            out.append({"title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": (item.get("snippet") or "")[:300],
                        "date": item.get("date", "")})
        result = {"query": query, "results": out}
        if d.get("answerBox"):
            ab = d["answerBox"]
            result["answer_box"] = {"title": ab.get("title", ""),
                                    "answer": ab.get("answer") or ab.get("snippet", ""),
                                    "link": ab.get("link", "")}
        if d.get("knowledgeGraph"):
            kg = d["knowledgeGraph"]
            result["knowledge_graph"] = {"title": kg.get("title", ""),
                                          "type": kg.get("type", ""),
                                          "description": (kg.get("description") or "")[:300]}
        return result

    key = ENV.get("SERPER_API_KEY") or os.environ.get("SERPER_API_KEY", "")
    if not key:
        # fallback: StyleCast env file
        try:
            for line in open(os.path.expanduser("~/StyleCast/env")):
                if line.startswith("SERPER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
        except OSError:
            pass

    proxy_url = (os.environ.get("SEARCH_PROXY_URL")
                 or ENV.get("SEARCH_PROXY_URL")
                 or "https://northledger-insight-proxy.r-mdrashad97.workers.dev/search")

    direct_error = None
    if key:
        try:
            out = _direct(key)
            if "failed" not in out:
                return out
            direct_error = out["failed"]
        except _rq.RequestException as e:
            direct_error = f"direct search request failed: {e}"

    try:
        r = _rq.post(proxy_url, headers={"Content-Type": "application/json"},
                     json={"q": query, "num": num}, timeout=30)
        if r.status_code == 200:
            return r.json()          # the proxy already answers this tool's own shape
        proxy_error = f"search proxy {r.status_code}: {r.text[:150]}"
    except _rq.RequestException as e:
        proxy_error = f"search proxy request failed: {e}"

    if direct_error:
        return {"error": f"{direct_error} | proxy fallback failed: {proxy_error}"}
    return {"error": proxy_error}



def tool_web_read(url):
    """Fetch a URL and return readable text (title + content, scripts/styles stripped).
    The READ half of the browser: call after web_search to actually read a source page.
    Use for dataset identification, methodology notes, and citation verification."""
    import html as _html
    import requests as _rq
    if not url.startswith("http"):
        return {"error": "url must start with http(s)://"}
    try:
        r = _rq.get(url, timeout=25, headers={"User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) data-analyst-agent/1.0"})
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code} fetching {url}"}
        page = r.text
    except _rq.RequestException as e:
        return {"error": f"fetch failed: {e}"}
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    title = _html.unescape(m.group(1).strip()) if m else url
    # strip scripts/styles, then tags
    body = re.sub(r"(?is)<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", " ", page)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = _html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    return {"url": url, "title": title[:200], "content": body[:6000],
            "note": "first ~6000 chars of readable text; cite as title - url"}


def tool_make_chart(spec_json):
    """Generate a chart PNG from a spec: {"type": "line"|"bar"|"grouped_bar",
    "title": str, "x": [...], "series": {"name": [...]}, "ylabel": str}
    Saves PNG; use make_report to embed it (pass artifact path)."""
    import json as _json
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        spec = _json.loads(spec_json) if isinstance(spec_json, str) else spec_json
    except _json.JSONDecodeError as e:
        return {"error": f"bad chart spec JSON: {e}"}
    t = spec.get("type", "line")
    title = spec.get("title", "Chart")
    x = [str(v) for v in spec.get("x", [])]
    series = spec.get("series", {})
    # sanitize: numeric-coerce; None/NaN -> 0 so matplotlib never gets None
    def _num(v):
        try:
            import math
            f = float(v)
            return f if math.isfinite(f) else 0.0
        except (TypeError, ValueError):
            return 0.0
    series = {name: [_num(v) for v in ys] for name, ys in series.items()}
    # align lengths: trim x and all series to the shortest
    if series:
        n_min = min(min(len(ys) for ys in series.values()), len(x))
        if len(x) != n_min or any(len(ys) != n_min for ys in series.values()):
            x = x[:n_min]
            series = {name: ys[:n_min] for name, ys in series.items()}
    if not x or not series:
        return {"error": "chart spec needs 'x' (list) and 'series' ({name: values})"}
    aligned_note = ""
    if series and len(x) != len(next(iter(series.values()))):
        pass  # handled above; kept for safety
    fig, ax = plt.subplots(figsize=(10, 5.2))
    if t == "line":
        for name, ys in series.items():
            ax.plot(range(len(x)), ys, marker="o", markersize=3, linewidth=1.8, label=name)
    elif t == "bar":
        for name, ys in series.items():
            ax.bar(range(len(x)), ys, label=name)
    elif t == "grouped_bar":
        import numpy as _np
        n = len(series)
        w = 0.8 / max(n, 1)
        for i, (name, ys) in enumerate(series.items()):
            ax.bar([j + (i - n / 2 + 0.5) * w for j in range(len(x))], ys, width=w, label=name)
    else:
        return {"error": f"chart type must be line/bar/grouped_bar, got {t!r}"}
    ax.set_xticks(range(len(x)))
    ax.set_xticklabels(x, rotation=60 if len(x) > 8 else 0, fontsize=8)
    ax.set_ylabel(spec.get("ylabel", ""))
    ax.set_title(title, fontsize=13)
    ax.grid(alpha=0.25)
    if len(series) > 1:
        ax.legend(fontsize=9)
    fig.tight_layout()
    path = _save_chart_png(fig, title)
    return {"artifact": os.path.relpath(path, SESSION_DIR), "type": "png",
            "title": title, "note": "PNG saved; embed it via make_report body_md image line: ![](artifact-path)"}


def tool_make_map(spec_json):
    """Choropleth WORLD MAP PNG. spec: {"title": str, "values": {"Country": number}, "label": str}.
    Aliases: US/USA->United States of America, UK->United Kingdom, European Union->members."""
    import json as _json
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        spec = _json.loads(spec_json) if isinstance(spec_json, str) else spec_json
    except _json.JSONDecodeError as e:
        return {"error": f"bad map spec JSON: {e}"}
    values = spec.get("values", {})
    if not values:
        return {"error": "map spec needs 'values': {country: number}"}
    alias = {"United States": "United States of America", "USA": "United States of America",
             "US": "United States of America", "UK": "United Kingdom"}
    eu_members = ["Germany", "France", "Italy", "Spain", "Netherlands", "Poland", "Sweden",
                  "Belgium", "Austria", "Ireland", "Denmark", "Finland", "Portugal", "Greece"]
    geo = _json.load(open(os.path.join(HERE, "assets", "world.geojson")))

    def rings_of(geom):
        """Yield [x,y] rings for Polygon/MultiPolygon, robustly."""
        t = geom["type"]
        if t == "Polygon":
            for ring in geom["coordinates"]:
                yield ring
        elif t == "MultiPolygon":
            for poly in geom["coordinates"]:
                for ring in poly:
                    yield ring

    fig, ax = plt.subplots(figsize=(12.5, 6.4))
    vals = [v for v in values.values() if isinstance(v, (int, float))]
    vmin, vmax = (min(vals), max(vals)) if vals else (0, 1)
    rng = (vmax - vmin) or 1
    matched = set()
    values = {k: v for k, v in values.items() if isinstance(v, (int, float))}
    for f in geo["features"]:
        nm = f["properties"].get("NAME", "")
        geom = f.get("geometry")
        if not geom:
            continue
        v = None
        for src_name, val in values.items():
            tgt = alias.get(src_name, src_name)
            if nm == tgt:
                v = val; matched.add(src_name); break
            if src_name == "European Union" and nm in eu_members:
                v = val; matched.add("European Union"); break
        for ring in rings_of(f["geometry"]):
            try:
                xs, ys = zip(*ring)
            except ValueError:
                continue
            if v is None:
                ax.add_patch(plt.Polygon(list(zip(xs, ys)), closed=True,
                            facecolor="#e8e8e8", edgecolor="#999999", linewidth=0.4))
            else:
                frac = (v - vmin) / rng
                ax.add_patch(plt.Polygon(list(zip(xs, ys)), closed=True,
                            facecolor=plt.cm.YlOrRd(0.15 + 0.8 * frac),
                            edgecolor="#555555", linewidth=0.4))
    unmatched = set(values) - matched
    ax.set_xlim(-170, 180); ax.set_ylim(-58, 84)
    ax.axis("off")
    ax.set_title(spec.get("title", "World Map"), fontsize=13)
    sm = plt.cm.ScalarMappable(cmap="YlOrRd", norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.55)
    cbar.set_label(spec.get("label", "value"))
    fig.tight_layout()
    path = _save_chart_png(fig, spec.get("title", "world-map"))
    out = {"artifact": os.path.relpath(path, SESSION_DIR), "type": "png",
           "title": spec.get("title", "World Map")}
    if unmatched:
        out["warning"] = "unmatched countries (no map match): " + ", ".join(sorted(unmatched))
    return out


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
        elif re.match(r"^!\[\]\((.+?)\)\s*$", ln.strip()):
            m = re.match(r"^!\[\]\((.+?)\)\s*$", ln.strip())
            img = os.path.join(SESSION_DIR, m.group(1))
            if os.path.exists(img):
                if pdf.get_y() > 230:
                    pdf.add_page()
                w = 160
                h = w * 0.55
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(img) as im:
                        w0, h0 = im.size
                    h = w * h0 / max(w0, 1)
                    if h > 150: w = w * 150 / h; h = 150
                except Exception:
                    pass
                pdf.image(img, x=(190 - w) / 2, w=w, h=h)
                pdf.ln(4)
            else:
                pdf.set_font("helvetica", "i", 9)
                pdf.multi_cell(0, 5, f"[chart: {m.group(1)}]", new_x="LMARGIN", new_y="NEXT")
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
    {"name": "make_report", "description": "Generate a report file (pdf/xlsx/docx) from markdown-ish content. Use for the final deliverable. Embed chart/map PNGs with a line like: ![](artifacts/xxx.png)",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["pdf", "xlsx", "docx"]},
        "title": {"type": "string"}, "summary_md": {"type": "string"}, "body_md": {"type": "string"}},
        "required": ["kind", "title", "summary_md", "body_md"]}},
    {"name": "web_search", "description": "Search the web (Google via Serper) for external context, explanations, news, or verification. Use when the data alone cannot explain a finding, when you need current events (post-training-cutoff), or to verify/attribute a real-world cause. ALWAYS cite: title + link. Every web-sourced claim in the report must carry its source link.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "num": {"type": "integer", "default": 5}}, "required": ["query"]}},
    {"name": "web_read", "description": "Open a URL and read its readable text (the READ half of the browser). Use right after web_search: pick the most promising result link and read the page for full context, methodology, or verification. Cite as title - url.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "make_chart", "description": "Generate a chart PNG. spec_json: {\"type\": \"line\"|\"bar\"|\"grouped_bar\", \"title\": str, \"x\": [...labels], \"series\": {\"SeriesName\": [values aligned with x]}, \"ylabel\": str}. Returns artifact path; embed into a report with an image line in body_md.",
     "parameters": {"type": "object", "properties": {"spec_json": {"type": "string"}}, "required": ["spec_json"]}},
    {"name": "make_map", "description": "Generate a choropleth WORLD MAP PNG. spec_json: {\"title\": str, \"values\": {\"Country\": number}, \"label\": str}. Aliases handled (US/USA->United States, UK->United Kingdom, European Union->its members). Returns artifact path for report embedding.",
     "parameters": {"type": "object", "properties": {"spec_json": {"type": "string"}}, "required": ["spec_json"]}},
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
8. UNKNOWN-DATASET BOOTSTRAP: if the goal references a table you do not know (user-supplied
   data), start by (a) profile_table + a sample of rows to learn columns/content, then
   (b) ONE web_search to identify the dataset/domain ("what is this data: <key column names,
   distinct values, date range>"), then plan with that context. Users bring ANY dataset -
   sales, IoT, health, logistics - your job is to recognize it, understand it, then analyze.
9. Web search discipline: web_search + web_read together are your BROWSER. The browse loop:
   SEARCH -> pick the best 1-2 links -> web_read them -> if still unclear, refine and search
   again (max ~4 searches + ~4 reads per topic). READ BEFORE YOU CITE: never cite from a
   snippet alone - web_read the page you are citing. Source hierarchy: prefer primary/
   official sources (statistical agencies, central banks, IMF, government releases, peer-
   reviewed/technical papers) over news aggregators and NEVER cite social-media posts as
   report sources. Every externally-sourced claim gets an inline citation "[Source: title -
   link]"; claims you could not verify stay labeled as inference. Data-sourced facts need no
   citation - they come from the queries you ran.
   THE THREE-QUESTION TEST - before finalizing any domain report, you MUST have searched for:
   (a) WHAT IS HAPPENING NOW in this domain (current events, policy changes, scheduled
       reviews/disputes that affect the data - e.g. for trade data: tariff actions, trade-
       agreement reviews; for crime data: policy changes; for fleet data: regulation)?
   (b) AUTHORITATIVE OUTLOOK: what do official bodies forecast or warn about for this
       domain's next 1-3 years, to qualify your own forecast against?
   (c) METHODOLOGY: any known data-quality notes about this dataset/series?
   Findings from (a) and (b) go into a mandatory report section "Current Context and
   Outlook", cited. Your forecast must be explicitly compared with the official outlook.
10. Budget discipline: you have plenty of tool calls, but do NOT explore endlessly. Aim to finish
   the investigation within ~15 rounds and RESERVE the final rounds for make_report. The requested
   deliverable file is a hard requirement — a session that ends without it is a failed session.
11. Never create files in the working directory; every artifact goes through make_report. If you
   must store intermediate state, keep it in memory (Python variables), never on disk.

Keep tool calls purposeful. Finish with a concise, human answer - never just 'done'."""

# ---------------------------------------------------------------- loop
def chat(messages, tools=TOOLS):
    import time as _time
    last_err = None
    for attempt in range(5):
        try:
            tools_payload = tools
            if AGENT_PROVIDER == "deepseek":
                tools_payload = [{"type": "function", "function": t} for t in tools]
            r = requests.post(f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages, "tools": tools_payload, "tool_choice": "auto"},
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
    if name == "web_search":
        return tool_web_search(args.get("query", ""), int(args.get("num", 5)))
    if name == "web_read":
        return tool_web_read(args.get("url", ""))
    if name == "make_chart":
        return tool_make_chart(args.get("spec_json", "{}"))
    if name == "make_map":
        return tool_make_map(args.get("spec_json", "{}"))
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