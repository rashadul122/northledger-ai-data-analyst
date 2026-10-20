"""
nl_vega: the visuals the AI designs for the Dashboard tab (AI Analyst v2, phase C, 20 Oct 2026; plan/AI-ANALYST-V2-PLAN.md §4).

The AI writes each visual as {id, title, why, sql, spec}: a read-only query over the rows the engine kept (engine/nl_sql.py,
the table kept_rows) and a Vega-Lite spec that draws the query's result. The engine runs the query (nl_analyst's `sql` op, the
same fence as the analyst rounds) and checks the spec against a small grammar: the marks, channels and properties below, every
field a column of the query's result, no data of its own (no data, url, datasets: the data is always the engine's table), no
expression, transform, selection or signal (the query does that work). A visual that fails is refused with its reason, which
goes back to the AI with the dashboard's other signals. The AI never types a number: its words follow the dashboard's rule
(only a 4-digit year), and the figures are the query's result.

    validate_spec(spec, columns) -> (clean spec or None, why)
    build(raw_visuals, run_sql) -> {"visuals": [...], "refused": [{element, why}]}
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

VISUALS_MAX = 4
SPEC_BYTES_MAX = 6000
SQL_MAX = 1500
ROWS_MAX = 200                 # the rows a visual draws (nl_analyst.SQL_TABLE_ROWS: a step's table keeps 200)
LAYERS_MAX = 3
TOOLTIP_MAX = 6
TEXT_MAX = {"title": 90, "why": 200, "axis": 60}
ID_RX = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
YEAR_RX = re.compile(r"(?<!\d)(?:1[89]\d\d|20\d\d)(?!\d)")

MARKS = ("bar", "line", "area", "point", "circle", "square", "tick", "rect", "rule", "arc", "boxplot", "text", "trail")
MARK_PROPS = {"type": str, "point": bool, "tooltip": bool, "interpolate": str, "opacity": (int, float), "strokeWidth": (int, float),
              "size": (int, float), "innerRadius": (int, float), "outerRadius": (int, float), "cornerRadius": (int, float),
              "filled": bool, "orient": str, "extent": (str, int, float), "binSpacing": (int, float), "thickness": (int, float)}
INTERPOLATE = ("linear", "monotone", "step", "step-after", "step-before", "basis", "cardinal")
CHANNELS = ("x", "y", "x2", "y2", "color", "size", "shape", "opacity", "theta", "radius", "column", "row", "facet",
            "tooltip", "detail", "order", "text", "strokeDash", "xOffset", "yOffset")
TYPES = ("quantitative", "nominal", "ordinal", "temporal")
AGGREGATES = ("sum", "mean", "average", "median", "min", "max", "count", "distinct", "q1", "q3", "stdev")
TIMEUNITS = ("year", "quarter", "month", "yearquarter", "yearmonth", "yearmonthdate", "date", "day", "week", "yearweek")
SORT_WORDS = ("ascending", "descending", "x", "y", "-x", "-y", "color", "-color", "theta", "-theta")
STACK = (None, True, False, "zero", "normalize", "center")
SCALE_TYPES = ("linear", "log", "sqrt", "symlog", "pow", "time", "utc", "band", "point", "ordinal")
FORMAT_RX = re.compile(r"^[0-9a-zA-Z%$,.~+\- ():/]{0,24}$")      # a d3 number or time format: no braces, no quotes
ORIENTS = ("left", "right", "top", "bottom", "none", "top-left", "top-right", "bottom-left", "bottom-right")
SPEC_KEYS = ("mark", "encoding", "layer", "description", "$schema", "title", "width", "height", "resolve")
FORBIDDEN = ("data", "url", "datasets", "transform", "params", "selection", "signals", "expr", "config", "usermeta",
             "projection", "repeat", "concat", "hconcat", "vconcat", "spec", "autosize", "background", "padding")


def has_figure(s: Any) -> bool:
    return bool(re.search(r"\d", YEAR_RX.sub("", str(s or ""))))


def _words(v: Any, n: int) -> str:
    t = re.sub(r"\s+", " ", v).strip()[:n] if isinstance(v, str) else ""
    return "" if has_figure(t) else t


class Bad(Exception):
    pass


def _num(v: Any, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
        raise Bad("a number out of range")
    return v


def _mark(m: Any) -> Any:
    if isinstance(m, str):
        if m not in MARKS:
            raise Bad("the mark %s is not one the engine draws" % m[:30])
        return m
    if not isinstance(m, dict) or m.get("type") not in MARKS:
        raise Bad("the mark is not one the engine draws")
    out: Dict[str, Any] = {}
    for k, v in m.items():
        t = MARK_PROPS.get(k)
        if t is None:
            continue                                  # a style the theme owns (colour, font): dropped, not refused
        if (t is bool) != isinstance(v, bool) or not isinstance(v, t):
            raise Bad("the mark's %s has the wrong type" % k)
        if k == "interpolate" and v not in INTERPOLATE:
            raise Bad("the mark's interpolate is not one the engine knows")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            _num(v, 0, 400)
        out[k] = v
    return out


def _sort(v: Any, cols: List[str]) -> Any:
    if v is None or v in SORT_WORDS:
        return v
    if isinstance(v, list) and len(v) <= 60 and all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v):
        return [str(x)[:60] if isinstance(x, str) else x for x in v]
    if isinstance(v, dict):
        out: Dict[str, Any] = {}
        if v.get("field") is not None:
            if v["field"] not in cols:
                raise Bad("it sorts by a column the query does not return: %s" % str(v["field"])[:60])
            out["field"] = v["field"]
        if v.get("encoding") is not None:
            if v["encoding"] not in CHANNELS:
                raise Bad("it sorts by a channel the engine does not know")
            out["encoding"] = v["encoding"]
        if v.get("op") is not None:
            if v["op"] not in AGGREGATES:
                raise Bad("its sort's op is not one the engine knows")
            out["op"] = v["op"]
        if v.get("order") in ("ascending", "descending"):
            out["order"] = v["order"]
        return out
    raise Bad("its sort is not one the engine knows")


def _guide(v: Any, what: str) -> Any:
    """An axis or a legend: its title, format, label angle, orientation; null hides it."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise Bad("its %s is not an object" % what)
    out: Dict[str, Any] = {}
    if "title" in v:
        out["title"] = None if v["title"] is None else _words(v["title"], TEXT_MAX["axis"])
    if isinstance(v.get("format"), str):
        if not FORMAT_RX.match(v["format"]):
            raise Bad("its %s's format is not a number or date format" % what)
        out["format"] = v["format"]
    if v.get("formatType") in ("number", "time", "utc"):
        out["formatType"] = v["formatType"]
    if "labelAngle" in v:
        out["labelAngle"] = _num(v["labelAngle"], -90, 90)
    if v.get("orient") in ORIENTS:
        out["orient"] = v["orient"]
    if isinstance(v.get("grid"), bool):
        out["grid"] = v["grid"]
    if isinstance(v.get("labelLimit"), (int, float)) and not isinstance(v.get("labelLimit"), bool):
        out["labelLimit"] = _num(v["labelLimit"], 0, 400)
    return out


def _fielddef(d: Any, cols: List[str], ch: str) -> Dict[str, Any]:
    if not isinstance(d, dict):
        raise Bad("its %s channel is not an object" % ch)
    out: Dict[str, Any] = {}
    if "value" in d and "field" not in d:
        v = d["value"]
        if isinstance(v, str):
            if has_figure(v):
                raise Bad("its %s channel holds a figure" % ch)
            out["value"] = v[:40]
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and ch in ("size", "opacity", "x", "y", "strokeDash"):
            out["value"] = v                           # a constant size, opacity or position: a style, never a data figure
        else:
            raise Bad("its %s channel's value is not a word" % ch)
        return out
    agg = d.get("aggregate")
    f = d.get("field")
    if f is None and agg != "count":
        raise Bad("its %s channel names no column" % ch)
    if f is not None:
        if not isinstance(f, str) or f not in cols:
            raise Bad("its %s channel reads a column the query does not return: %s" % (ch, str(f)[:60]))
        out["field"] = f
    t = d.get("type")
    if t is not None:
        if t not in TYPES:
            raise Bad("its %s channel's type is not one Vega-Lite knows" % ch)
        out["type"] = t
    if agg is not None:
        if agg not in AGGREGATES:
            raise Bad("its %s channel's aggregate is not one the engine knows" % ch)
        out["aggregate"] = "mean" if agg == "average" else agg
    if "bin" in d:
        b = d["bin"]
        if isinstance(b, bool):
            out["bin"] = b
        elif isinstance(b, dict) and set(b) <= {"maxbins"}:
            out["bin"] = {"maxbins": int(_num(b.get("maxbins", 10), 2, 60))}
        else:
            raise Bad("its %s channel's bin is not true or {maxbins}" % ch)
    if d.get("timeUnit") is not None:
        if d["timeUnit"] not in TIMEUNITS:
            raise Bad("its %s channel's timeUnit is not one the engine knows" % ch)
        out["timeUnit"] = d["timeUnit"]
    if "sort" in d:
        out["sort"] = _sort(d["sort"], cols)
    if "stack" in d:
        if d["stack"] not in STACK:
            raise Bad("its %s channel's stack is not one Vega-Lite knows" % ch)
        out["stack"] = d["stack"]
    if "title" in d:
        out["title"] = None if d["title"] is None else _words(d["title"], TEXT_MAX["axis"])
    if isinstance(d.get("format"), str):
        if not FORMAT_RX.match(d["format"]):
            raise Bad("its %s channel's format is not a number or date format" % ch)
        out["format"] = d["format"]
    if "scale" in d:
        s = d["scale"]
        if s is None:
            out["scale"] = None
        elif isinstance(s, dict):
            sc: Dict[str, Any] = {}
            if isinstance(s.get("zero"), bool):
                sc["zero"] = s["zero"]
            if s.get("type") in SCALE_TYPES:
                sc["type"] = s["type"]
            if isinstance(s.get("nice"), bool):
                sc["nice"] = s["nice"]
            if isinstance(s.get("reverse"), bool):
                sc["reverse"] = s["reverse"]
            if s.get("scheme") in ("category10", "tableau10", "blues", "greens", "reds", "oranges", "purples",
                                   "redblue", "blueorange", "viridis", "tealblues"):
                sc["scheme"] = s["scheme"]
            out["scale"] = sc
        else:
            raise Bad("its %s channel's scale is not an object" % ch)
    if "axis" in d:
        out["axis"] = _guide(d["axis"], "axis")
    if "legend" in d:
        out["legend"] = _guide(d["legend"], "legend")
    if isinstance(d.get("columns"), int) and ch == "facet":
        out["columns"] = int(_num(d["columns"], 1, 6))
    if "type" not in out and "aggregate" not in out:
        raise Bad("its %s channel has no type (quantitative, nominal, ordinal or temporal)" % ch)
    return out


def _encoding(e: Any, cols: List[str]) -> Dict[str, Any]:
    if not isinstance(e, dict) or not e:
        raise Bad("it has no encoding")
    out: Dict[str, Any] = {}
    for ch, d in e.items():
        if ch not in CHANNELS:
            raise Bad("the channel %s is not one the engine draws" % str(ch)[:30])
        if ch == "tooltip" and isinstance(d, list):
            out[ch] = [_fielddef(x, cols, ch) for x in d[:TOOLTIP_MAX]]
        elif ch == "tooltip" and d is None:
            out[ch] = None
        else:
            out[ch] = _fielddef(d, cols, ch)
    return out


def _forbidden(v: Any, depth: int = 0) -> Optional[str]:
    """A key anywhere in the spec that would bring in data or code of its own, or a string that reaches outside the page."""
    if depth > 8:
        return "it is nested too deeply"
    if isinstance(v, dict):
        for k, x in v.items():
            if k in FORBIDDEN:
                return "it carries %s (the data is the engine's query result; the query does any filtering)" % k
            w = _forbidden(x, depth + 1)
            if w:
                return w
    elif isinstance(v, list):
        for x in v:
            w = _forbidden(x, depth + 1)
            if w:
                return w
    elif isinstance(v, str) and re.match(r"(?i)\s*(https?:|data:|javascript:|//)", v):
        return "it names an address outside the page"
    return None


def validate_spec(spec: Any, columns: List[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """The spec as the engine draws it (only the grammar above, the data left out), or None with the reason."""
    if not isinstance(spec, dict):
        return None, "its spec is not an object"
    try:
        size = len(json.dumps(spec))
    except (TypeError, ValueError):
        return None, "its spec is not plain JSON"
    if size > SPEC_BYTES_MAX:
        return None, "its spec is longer than %d characters" % SPEC_BYTES_MAX
    w = _forbidden(spec)
    if w:
        return None, w
    cols = [str(c) for c in columns]
    try:
        out: Dict[str, Any] = {}
        if "layer" in spec:
            lay = spec["layer"]
            if not isinstance(lay, list) or not (1 <= len(lay) <= LAYERS_MAX):
                raise Bad("its layers are not a list of 1 to %d" % LAYERS_MAX)
            out["layer"] = []
            for L in lay:
                if not isinstance(L, dict) or "layer" in L:
                    raise Bad("a layer is not one mark with its encoding")
                out["layer"].append({"mark": _mark(L.get("mark")), "encoding": _encoding(L.get("encoding"), cols)})
            if isinstance(spec.get("encoding"), dict):
                out["encoding"] = _encoding(spec["encoding"], cols)
        else:
            out["mark"] = _mark(spec.get("mark"))
            out["encoding"] = _encoding(spec.get("encoding"), cols)
        if isinstance(spec.get("resolve"), dict):
            r = spec["resolve"]
            res: Dict[str, Any] = {}
            for part in ("scale", "axis", "legend"):
                if isinstance(r.get(part), dict):
                    res[part] = {k: v for k, v in r[part].items() if k in CHANNELS and v in ("shared", "independent")}
            out["resolve"] = res
        if isinstance(spec.get("description"), str):
            d = _words(spec["description"], 200)
            if d:
                out["description"] = d
        return out, ""
    except Bad as exc:
        return None, str(exc)


def _columns_named(spec: Dict[str, Any]) -> List[str]:
    """Every column the clean spec reads."""
    out: List[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if isinstance(v.get("field"), str):
                out.append(v["field"])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(spec)
    return out


def build(raw: Any, run_sql: Callable[[str], Dict[str, Any]], taken: Any = ()) -> Dict[str, Any]:
    """The AI's visuals, each run and checked: {"visuals": [{id, title, why, sql, spec, columns, rows, rows_total,
    truncated}], "refused": [{element, why}]}. run_sql(sql) -> {"ok", "columns", "rows", "rows_total"} or {"ok": False,
    "error"} (nl_browser: nl_analyst's sql op on the analysis frame). `taken`: ids the dashboard's tiles and charts hold."""
    visuals: List[Dict[str, Any]] = []
    refused: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return {"visuals": visuals, "refused": refused}
    seen = set(str(t) for t in taken or ())
    for i, v in enumerate(raw[:VISUALS_MAX + 2]):
        if not isinstance(v, dict):
            continue
        title = _words(v.get("title"), TEXT_MAX["title"])
        el = "visual " + (title or str(v.get("id") or i + 1))[:60]
        if len(visuals) >= VISUALS_MAX:
            refused.append({"element": el, "why": "at most %d visuals" % VISUALS_MAX})
            continue
        vid = v.get("id") if isinstance(v.get("id"), str) and ID_RX.match(v["id"]) else "v%d" % (i + 1)
        while vid in seen:
            vid += "_"
        sql = v.get("sql")
        if not isinstance(sql, str) or not sql.strip() or len(sql) > SQL_MAX:
            refused.append({"element": el, "why": "its query is missing or longer than %d characters" % SQL_MAX})
            continue
        if not title:
            refused.append({"element": el, "why": "it has no title in plain words (only a 4-digit year may appear)"})
            continue
        bad = _forbidden(v.get("spec")) if isinstance(v.get("spec"), dict) else "its spec is not an object"
        if bad:                                       # no query runs for a spec that would bring its own data or code
            refused.append({"element": el, "why": "its spec was refused: %s" % bad})
            continue
        res = run_sql(sql)
        if not res.get("ok"):
            refused.append({"element": el, "why": "its query did not run: %s" % str(res.get("error") or "")[:160]})
            continue
        cols = [str(c) for c in res.get("columns") or []]
        rows = list(res.get("rows") or [])
        if not rows:
            refused.append({"element": el, "why": "its query returned no rows"})
            continue
        total = int(res.get("rows_total") or len(rows))
        if total > ROWS_MAX:
            # a chart of the first rows of a longer result is a chart of an arbitrary part (20 Oct 2026: a histogram of salaries
            # drew 200 of 5,000 people as the whole): the query must group or bin to at most ROWS_MAX rows
            refused.append({"element": el, "why": "its query returned %d rows and a visual draws at most %d: group, bin or rank in the "
                                                  "query (GROUP BY, a band such as CAST(x / 10000 AS INTEGER) * 10000, LIMIT after "
                                                  "ORDER BY)" % (total, ROWS_MAX)})
            continue
        spec, why = validate_spec(v.get("spec"), cols)
        if spec is None:
            refused.append({"element": el, "why": "its spec was refused: %s" % why})
            continue
        named = _columns_named(spec)
        if not named:
            refused.append({"element": el, "why": "its spec reads none of the query's columns"})
            continue
        seen.add(vid)
        visuals.append({"id": vid, "title": title, "why": _words(v.get("why"), TEXT_MAX["why"]), "sql": sql.strip(),
                        "spec": spec, "columns": cols, "rows": [list(r) if isinstance(r, (list, tuple)) else r for r in rows[:ROWS_MAX]],
                        "rows_total": total, "truncated": False})
    return {"visuals": visuals, "refused": refused}
