"""
nl_dashboard: the Dashboard tab (plan/DASHBOARD-DESIGN.md, Track A; CONTRACT-v2.md 5.14).

    import nl_dashboard
    rep["dashboard"] = nl_dashboard.build(rep, ctx)   # after rep["viz"]; ctx as nl_viz.build's, plus "recorded"

The AI designs, the engine computes. The design is the plan's `dashboard` block (the worker's POST /plan, checked
there first), a recorded design (the site's sample ledger only, matched by its sha256: RECORDED_FILE), or the engine's
own (default_design). Every element is checked here against the engine's reading of the rows it kept (nl_viz.Ctx):
an unknown column or chart, a personal column, a level summed over time, a figure in an AI string: refused with its
reason (record.refused, shown in the notes). The engine then writes:

  * kpis and charts: what each counts (column, aggregation, unit, rows), never a computed value the browser cannot
    recompute: every figure on the tab is recomputed in the browser from `cube` under the visitor's filters;
  * cube: counts and sums by month x the dashboard's dimensions (at most DIMS_MAX, each at most LEVELS_MAX levels, the
    rest folded into "other"), one sum and one filled count per measure. No flagged column (withheld, coded or kept)
    is a dimension, a measure or a label; a level that holds a flagged column's value is folded;
  * defaults: the unfiltered value of every KPI, computed here by the same rule (query), which the page checks;
  * notes: definitions, the data's range, rows kept and set aside, and "Looked wrong, please check".

Rules the browser keeps too (57-dashboard.js mirrors query()): flows (sums, counts) are added up; levels, rates and
balances are averaged, never summed over time; a figure resting on fewer than SMALL rows is shown as "<5" (after
every filter); a month with no rows is a gap, never filled, smoothed or interpolated.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import nl_browser as NB
import nl_viz as NV

VERSION = "2026-10-16.1"
SMALL = 5                     # no figure rests on fewer rows
KPI_MAX = 6
CHART_MAX = 6
QUESTION_MAX = 6
FILTER_CATS = 3               # category filters, besides the date range
DIMS_MAX = 5                  # the cube's dimensions
LEVELS_MAX = 12               # a dimension's levels: the 11 with the most rows and "other"
CELLS_MAX = 50000             # the cube's cells
WHERE_VALUES_MAX = 12
PINS_MAX = 6                  # charts the visitor pinned from "Ask your data" (decisions __pins__)
TEXT_MAX = {"audience": 160, "q": 160, "label": 60, "title": 90, "why": 200, "unit_word": 20}
ID_RX = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
YEAR_RX = re.compile(r"(?<!\d)(?:1[89]\d\d|20\d\d)(?!\d)")
UNIT_RX = re.compile(r"^[^\W\d_]+(?:[ /.-][^\W\d_]+)*$|^[$€£¥%]$|^[A-Z]{3}$")
AGGS = ("sum", "average", "count", "latest")
# the menu charts the dashboard recomputes under its filters, and what it draws for each
DRAWN = {"line_chart": "line", "area_chart": "area", "bar_chart": "bar", "grouped_bar": "grouped",
         "small_multiples": "multiples"}
OTHER = "other"
BLANK = "(blank)"
RECORDED_FILE = "dashboard_sample.json"     # the sample ledger's recorded AI design (beside this file, in the pack)

R_FIGURE = "it held a figure (only a 4-digit year may appear in an AI's words); the engine wrote its own"
R_PERSONAL = "a personal column is never shown"
R_MISSING = "a column the file does not have"
R_LEVEL_SUM = "%s is a level (a balance, rate or average), so it is averaged, never added up over time"
R_NO_DATE = "the file has no date column the engine reads"
R_MENU = "not on the chart menu"
R_ANSWERS = "it answers nothing on this dashboard (its tiles and charts were not found)"
R_ID = "its id is not a short lowercase name, so the engine gave it one"
R_TWICE = "the same id twice"
R_DIMS = "the dashboard can filter by at most %d columns at once" % DIMS_MAX
R_CELLS = "too many combinations of its columns to recompute in the browser"


# ----------------------------------------------------------------------------- small helpers
def _text(s: Any, n: int) -> str:
    return NV._cut(" ".join(str(s or "").split()), n)


def has_figure(s: Any) -> bool:
    """A digit outside a 4-digit year from 1800 to 2099 (the worker's rule on AI strings, kept here too)."""
    return bool(re.search(r"\d", YEAR_RX.sub("", str(s or ""))))


def _plural_noun(noun: str) -> str:
    n = noun.strip()
    return n if n.endswith("s") else n + "s"


def row_noun(rep: Dict[str, Any]) -> Tuple[str, str]:
    """(plural, singular) of what one row is, as the engine's own labels name it ("Ledger lines a month")."""
    lab = str(((rep.get("summary") or {}).get("labels") or {}).get("measure.volume.change_pct") or "")
    m = re.match(r"^(.*?) a (?:month|quarter|year|day|week)\b", lab)
    plural = (m.group(1) if m else "").strip() or "Rows"
    sing = plural[:-1] if plural.lower().endswith("s") and len(plural) > 3 else plural
    return plural, sing.lower()


def _month_word(ym: Optional[str]) -> str:
    return NV._mon(ym) if ym else ""


# ----------------------------------------------------------------------------- what the dashboard reads
class Read:
    """The engine's reading for the dashboard: nl_viz.Ctx (the kept rows without every flagged column), plus the
    report's own labels."""

    def __init__(self, rep: Dict[str, Any], ctx_in: Dict[str, Any]) -> None:
        self.rep = rep
        self.ctx = NV.Ctx(rep, dict(ctx_in, claims=NV.claims_of(ctx_in.get("r"))))
        self.refused: List[Dict[str, str]] = []

    def refuse(self, element: str, why: str) -> None:
        if why == R_PERSONAL:              # never echo what the AI called it (its label may name the column)
            w = element.split(" ")[0]
            element = "a " + w if w in ("tile", "chart") else "an element of the design"
        self.refused.append({"element": _text(element, 80), "why": _text(why, 240)})

    def column(self, name: Any, element: str, role: str) -> Optional[str]:
        """The landed name of a column the dashboard may use in a role (measure, category), else None (refused)."""
        c = self.ctx
        if name is None or not str(name).strip():
            return None
        if c.is_flagged(name):
            self.refuse(element, R_PERSONAL)
            return None
        land = c.landed(name)
        if land is None or land not in c.columns:
            self.refuse(element, R_MISSING)
            return None
        why = c.role_ok(land, role)
        if why:
            self.refuse(element, why)
            return None
        if role == "category" and NB._looks_like_names(c.levels_all(land), c.header(land)):
            self.refuse(element, "the values of %s look like people's names" % c.header(land))
            return None
        return land

    def words(self, s: Any, kind: str, element: str, ai: bool) -> Optional[str]:
        """An AI string as the page may print it, or None (refused): no figure, nothing the report's scrubber
        changes (a phone number, an email, a withheld value)."""
        t = _text(s, TEXT_MAX[kind])
        if not t:
            return None
        if ai and has_figure(t):
            self.refuse(element, R_FIGURE)
            return None
        if self.ctx.pub(t) != t:
            self.refuse(element, "it named something personal; the engine wrote its own")
            return None
        return t


# ----------------------------------------------------------------------------- the design
def _ident(raw: Any, prefix: str, i: int, seen: set, R: Read, element: str) -> str:
    s = str(raw or "")
    if not ID_RX.match(s):
        if s:
            R.refuse(element, R_ID)
        s = "%s%d" % (prefix, i + 1)
    while s in seen:
        s = s + "_"
    seen.add(s)
    return s


def _where(R: Read, raw: Any, element: str) -> Optional[Dict[str, Any]]:
    """{dim: landed column, values: [levels]} from an element's `where` {column, values}, or None."""
    if not isinstance(raw, dict):
        return None
    land = R.column(raw.get("column"), element, "category")
    if land is None:
        return None
    have = set(R.ctx.levels_all(land))
    vals = []
    for v in (raw.get("values") or [])[:WHERE_VALUES_MAX] if isinstance(raw.get("values"), list) else []:
        v = str(v).strip()
        if v in have and v not in vals:
            vals.append(v)
    if not vals:
        R.refuse(element, "none of its values is a value of %s in the rows kept" % R.ctx.header(land))
        return None
    if raw.get("exclude") is True:
        return {"dim": land, "values": vals, "exclude": True}
    return {"dim": land, "values": vals}


def _agg_for(R: Read, land: Optional[str], agg: Any, element: str, has_date: bool) -> str:
    c = R.ctx
    if land is None:
        return "count"
    flow = c.is_flow(land)
    a = str(agg or "")
    if a not in AGGS:
        a = "sum" if flow else "average"
    if a == "sum" and not flow:
        R.refuse(element, R_LEVEL_SUM % c.header(land))
        a = "average"
    if a == "latest" and not has_date:
        R.refuse(element, R_NO_DATE)
        a = "sum" if flow else "average"
    return a


def validate(R: Read, block: Dict[str, Any], ai: bool) -> Dict[str, Any]:
    """The design an AI (or the recorded design) proposed, every element checked; what is refused is in R.refused."""
    c = R.ctx
    has_date = c.date is not None
    seen: set = set()
    out: Dict[str, Any] = {"audience": "", "questions": [], "kpis": [], "charts": [], "filters": [], "layout": []}
    # what the worker already dropped (its planDashboard), with its reasons: shown in the notes like the engine's own
    for d in (block.get("dropped") or [])[:20] if isinstance(block.get("dropped"), list) else []:
        if isinstance(d, dict) and d.get("why"):
            el = str(d.get("element") or "an element of the design")
            R.refuse(el if not has_figure(el) and c.pub(el) == el else "an element of the design", str(d.get("why")))
    out["audience"] = R.words(block.get("audience"), "audience", "the audience", ai) or ""
    raw_k = block.get("kpis") if isinstance(block.get("kpis"), list) else []
    for i, k in enumerate(raw_k[:KPI_MAX + 4]):
        if not isinstance(k, dict):
            continue
        el = "tile %s" % _text(k.get("label") or k.get("id") or i + 1, 40)
        if len(out["kpis"]) >= KPI_MAX:
            R.refuse(el, "at most %d tiles" % KPI_MAX)
            continue
        kid = _ident(k.get("id"), "k", i, seen, R, el)
        m = k.get("measure")
        rows = m is None or str(m).strip().lower() in ("", "rows", "row", "count")
        land = None if rows else R.column(m, el, "measure")
        if not rows and land is None:
            continue
        agg = "count" if land is None else _agg_for(R, land, k.get("agg"), el, has_date)
        if land is not None and str(k.get("agg") or "") == "count":
            agg = "count"
        wh = _where(R, k.get("where"), el) if k.get("where") is not None else None
        if k.get("where") is not None and wh is None:
            continue
        out["kpis"].append({"id": kid, "measure": land, "agg": agg, "where": wh,
                            "label": R.words(k.get("label"), "label", el, ai) or "",
                            "unit_word": _unit(R, k.get("unit_word"), el, ai, land)})
    raw_c = block.get("charts") if isinstance(block.get("charts"), list) else []
    lim = NV.run_limits(c)
    for i, ch in enumerate(raw_c[:CHART_MAX + 4]):
        if not isinstance(ch, dict):
            continue
        name = str(ch.get("chart") or ch.get("kind") or "")
        el = "chart %s" % _text(ch.get("title") or name or i + 1, 60)
        if len(out["charts"]) >= CHART_MAX:
            R.refuse(el, "at most %d charts" % CHART_MAX)
            continue
        if name not in NV.REGISTRY:
            R.refuse(el, R_MENU)
            continue
        args = ch.get("args") if isinstance(ch.get("args"), list) else (ch.get("columns") or [])
        args = [str(a) for a in args if isinstance(a, (str, int, float))][:12]
        if any(c.is_flagged(a) for a in args):
            R.refuse(el, R_PERSONAL)
            continue
        chart, lands, why = NV.validate_directive(c, {"kind": name, "columns": args}, lim)
        if why:
            R.refuse(el, why)
            continue
        cid = _ident(ch.get("id"), "c", i, seen, R, el)
        item = {"id": cid, "chart": name, "title": R.words(ch.get("title"), "title", el, ai) or "",
                "why": R.words(ch.get("why"), "why", el, ai) or ""}
        if name in DRAWN:
            spec = _drawn_args(R, name, lands, el)
            if spec is None:
                continue
            item.update(spec)
            if ch.get("where") is not None:
                wh = _where(R, ch.get("where"), el)
                if wh is None:
                    continue
                item["where"] = wh
            else:
                item["where"] = None
            item["unit_word"] = _unit(R, ch.get("unit_word"), el, ai, item.get("measure"))
        else:
            try:
                rec = NV._built(c, name, lands)
            except NV.Refused as exc:
                R.refuse(el, str(exc))
                continue
            item.update({"draw": "static", "record": rec, "measure": None, "agg": None, "by": None, "where": None,
                         "unit_word": ""})
        out["charts"].append(item)
    raw_f = block.get("filters") if isinstance(block.get("filters"), list) else []
    for f in raw_f[:FILTER_CATS + 3]:
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind") or "category")
        el = "the filter on %s" % ("a column you withheld" if c.is_withheld(f.get("column") or "") else
                                    _text(f.get("column") or kind, 40))
        if kind == "date":
            if not has_date:
                R.refuse(el, R_NO_DATE)
            elif not any(x["kind"] == "date" for x in out["filters"]):
                out["filters"].append({"kind": "date", "dim": None})
            continue
        if sum(1 for x in out["filters"] if x["kind"] == "category") >= FILTER_CATS:
            R.refuse(el, "at most %d category filters" % FILTER_CATS)
            continue
        land = R.column(f.get("column"), el, "category")
        if land is not None and not any(x.get("dim") == land for x in out["filters"]):
            out["filters"].append({"kind": "category", "dim": land})
    ids = {k["id"] for k in out["kpis"]} | {x["id"] for x in out["charts"]}
    raw_q = block.get("questions") if isinstance(block.get("questions"), list) else []
    for q in raw_q[:QUESTION_MAX + 4]:
        if not isinstance(q, dict) or len(out["questions"]) >= QUESTION_MAX:
            continue
        el = "a question"
        t = R.words(q.get("q"), "q", el, ai)
        if not t:
            continue
        ab = [str(x) for x in (q.get("answered_by") or []) if str(x) in ids] if isinstance(q.get("answered_by"), list) else []
        if not ab:
            R.refuse("the question “%s”" % _text(t, 60), R_ANSWERS)
            continue
        out["questions"].append({"q": t, "answered_by": list(dict.fromkeys(ab))[:8]})
    out["layout"] = _layout(block.get("layout"), out)
    return out


def _unit(R: Read, u: Any, element: str, ai: bool, land: Optional[str]) -> str:
    s = " ".join(str(u or "").split())[:TEXT_MAX["unit_word"]]
    if s and UNIT_RX.match(s) and not (ai and has_figure(s)):
        return s
    if s:
        R.refuse(element, "its unit is not a unit word; the plan's unit was used")
    return R.ctx.unit(land) if land else ""


def _drawn_args(R: Read, name: str, lands: List[str], el: str) -> Optional[Dict[str, Any]]:
    """measure, aggregation and the category a recomputed chart splits by, from its menu arguments."""
    c = R.ctx
    has_date = c.date is not None
    measure, by = None, None
    if name == "line_chart":
        measure = lands[0] if lands else None
        by = lands[1] if len(lands) > 1 else None
        if measure is None:
            has, pm = c.primary_measure()
            measure = pm if has else None
    else:
        by = lands[0] if lands else None
        measure = lands[1] if len(lands) > 1 else None
        if measure is None and name == "area_chart":
            has, pm = c.primary_measure()
            measure = pm if (has and pm and c.is_flow(pm)) else None
    if name in ("line_chart", "area_chart", "grouped_bar", "small_multiples") and not has_date:
        R.refuse(el, R_NO_DATE)
        return None
    if name == "area_chart" and measure is not None and not c.is_flow(measure):
        R.refuse(el, "an area chart stacks parts of a total; %s is averaged" % c.header(measure))
        return None
    if by is not None and NB._looks_like_names(c.levels_all(by), c.header(by)):
        R.refuse(el, "the values of %s look like people's names" % c.header(by))
        return None
    agg = "count" if measure is None else ("sum" if c.is_flow(measure) else "average")
    return {"draw": DRAWN[name], "measure": measure, "agg": agg, "by": by}


def _layout(raw: Any, out: Dict[str, Any]) -> List[List[str]]:
    ids = [k["id"] for k in out["kpis"]] + [x["id"] for x in out["charts"]]
    rows: List[List[str]] = []
    used: set = set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, list):
            continue
        r = [str(x) for x in row if str(x) in ids and str(x) not in used][:4]
        used.update(r)
        if r:
            rows.append(r)
    kp = [i for i in ids if i not in used and any(k["id"] == i for k in out["kpis"])]
    if kp:
        rows.insert(0, kp)
    rest = [i for i in ids if i not in used and i not in kp]
    for j in range(0, len(rest), 2):
        rows.append(rest[j:j + 2])
    return rows


def default_design(R: Read) -> Dict[str, Any]:
    """The engine's own dashboard when no AI designed one: what each row counts and the headline measure, its line by
    month, and its split by the categories the engine reads, with the date range and up to 3 category filters."""
    c, rep = R.ctx, R.rep
    plural, _sing = row_noun(rep)
    labels = (rep.get("summary") or {}).get("labels") or {}
    has, pm = c.primary_measure()
    pm = pm if has else (c.measures[0] if c.measures else None)
    fid = str((rep.get("primary_metric") or {}).get("finding_id") or "")
    split = (c.claims.get(fid) or {}).get("kind_split") if fid else None
    out: Dict[str, Any] = {"audience": "Anyone who reads this file: the engine's own design from its columns, without the AI",
                           "questions": [], "kpis": [], "charts": [], "filters": [], "layout": []}
    k_rows = {"id": "rows", "measure": None, "agg": "count", "where": None, "label": plural, "unit_word": ""}
    out["kpis"].append(k_rows)
    where_lvl = where_other = None
    split_col = None
    if split and isinstance(split, dict) and pm and split.get("column") in c.columns and c.role_ok(split["column"], "category") == "":
        split_col = split["column"]
        lv = str(split.get("level") or "")
        levels = c.levels_all(split_col)
        if lv in levels:
            where_lvl = {"dim": split_col, "values": [lv]}
            where_other = {"dim": split_col, "values": [lv], "exclude": True}
    if pm:
        flow = c.is_flow(pm)
        lab = str(labels.get(fid) or "").split(",")[0].strip() if fid else ""
        if where_lvl:
            out["kpis"].append({"id": "main", "measure": pm, "agg": "sum" if flow else "average", "where": where_lvl,
                                "label": lab or c.header(pm), "unit_word": c.unit(pm)})
            olab = str(labels.get(fid.replace(".total.%s." % _slug(where_lvl["values"][0]), ".total.other.")) or "")
            out["kpis"].append({"id": "rest", "measure": pm, "agg": "sum" if flow else "average", "where": where_other,
                                "label": olab.split(",")[0].strip() or ("%s, other" % c.header(pm)), "unit_word": c.unit(pm)})
        else:
            out["kpis"].append({"id": "main", "measure": pm, "agg": "sum" if flow else "average", "where": None,
                                "label": c.header(pm), "unit_word": c.unit(pm)})
    for m in c.measures:
        if len(out["kpis"]) >= 4 or m == pm:
            continue
        out["kpis"].append({"id": "m_%s" % _slug(m)[:20], "measure": m, "agg": "sum" if c.is_flow(m) else "average",
                            "where": None, "label": c.header(m), "unit_word": c.unit(m)})
    dims = [d for d in c.dims if c.role_ok(d, "category") == "" and 2 <= len(c.levels_all(d)) <= 60
            and not NB._looks_like_names(c.levels_all(d), c.header(d))]
    if split_col and split_col in dims:
        dims = [split_col] + [d for d in dims if d != split_col]
    has_date = c.date is not None
    main_where = where_lvl
    if pm and has_date:
        out["charts"].append({"id": "trend", "chart": "line_chart", "draw": "line", "measure": pm,
                              "agg": "sum" if c.is_flow(pm) else "average", "by": None, "where": main_where,
                              "title": "", "why": NV.ENGINE_WHY + NV.REGISTRY["line_chart"]["what"],
                              "unit_word": c.unit(pm)})
    elif has_date:
        out["charts"].append({"id": "trend", "chart": "line_chart", "draw": "line", "measure": None, "agg": "count",
                              "by": None, "where": None, "title": "", "why": NV.ENGINE_WHY + NV.REGISTRY["line_chart"]["what"],
                              "unit_word": ""})
    for j, d in enumerate(dims[:2]):
        wh = None if d == split_col else main_where
        out["charts"].append({"id": "by_%s" % _slug(d)[:18], "chart": "bar_chart", "draw": "bar", "measure": pm,
                              "agg": "count" if pm is None else ("sum" if c.is_flow(pm) else "average"), "by": d,
                              "where": wh, "title": "", "why": NV.ENGINE_WHY + NV.REGISTRY["bar_chart"]["what"],
                              "unit_word": c.unit(pm) if pm else ""})
    if pm and has_date and c.is_flow(pm) and len(dims) > 2:
        d = dims[2]
        out["charts"].append({"id": "mix_%s" % _slug(d)[:18], "chart": "area_chart", "draw": "area", "measure": pm,
                              "agg": "sum", "by": d, "where": main_where, "title": "",
                              "why": NV.ENGINE_WHY + NV.REGISTRY["area_chart"]["what"], "unit_word": c.unit(pm)})
    if has_date:
        out["filters"].append({"kind": "date", "dim": None})
    for d in dims[:FILTER_CATS]:
        out["filters"].append({"kind": "category", "dim": d})
    # the questions the engine's design answers, in its own words
    for k in out["kpis"][1:2]:
        if any(x["id"] == "trend" for x in out["charts"]):
            out["questions"].append({"q": "How does %s move month by month?" % (k["label"] or "the headline"),
                                     "answered_by": [k["id"], "trend"]})
    bars = [x for x in out["charts"] if x["draw"] == "bar"]
    for b in bars:
        out["questions"].append({"q": "Which %s accounts for the most%s?" % (
            c.header(b["by"]), (" " + c.header(pm)) if pm else " rows"), "answered_by": [b["id"]]})
    if not out["questions"]:
        out["questions"].append({"q": "How many %s does the file hold?" % plural.lower(), "answered_by": ["rows"]})
    out["layout"] = _layout(None, out)
    return out


def _slug(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "x"


# ----------------------------------------------------------------------------- the cube
def make_cube(R: Read, design: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, str]]:
    """(cube, {landed dim: key}, {landed measure: key}). Elements that need a dimension the cube could not hold are
    marked by the caller (_prune)."""
    import pandas as pd
    c = R.ctx
    frame = c.frame
    n = len(frame)
    want: List[str] = []
    for f in design["filters"]:
        if f.get("dim"):
            want.append(f["dim"])
    for x in design["kpis"] + design["charts"]:
        for d in ([x.get("by")] if x.get("by") else []) + ([x["where"]["dim"]] if x.get("where") else []):
            if d not in want:
                want.append(d)
    dims = want[:DIMS_MAX]
    for d in want[DIMS_MAX:]:
        R.refuse("the column %s" % c.header(d), R_DIMS)
    meas: List[str] = []
    for x in design["kpis"] + design["charts"]:
        m = x.get("measure")
        if m and m not in meas:
            meas.append(m)
    mon = c.month if c.date is not None else pd.Series([None] * n, index=frame.index)
    months = sorted(str(m) for m in mon.dropna().unique())
    mi = mon.map({m: i for i, m in enumerate(months)}).fillna(-1).astype(int)
    cols: Dict[str, Any] = {"mi": mi.values}
    dim_rec = []
    while True:
        dim_rec, codes = [], {}
        for j, d in enumerate(dims):
            t = c.texts(d)
            by_rows = c.levels(d)
            keep: List[str] = []
            folded = hidden = 0
            for lv, _k in by_rows:
                if not NV._label_ok(c, lv):
                    hidden += 1
                elif len(keep) < LEVELS_MAX - 1:
                    keep.append(lv)
                else:
                    folded += 1
            if folded == 1 and not hidden and len(keep) == LEVELS_MAX - 1:
                keep.append(by_rows[-1][0])
                folded = 0
            levels = list(keep)
            has_blank = bool((t == "").any())
            if has_blank:
                levels.append(BLANK)
            if folded or hidden:
                levels.append(OTHER)
            idx = {lv: i for i, lv in enumerate(levels)}
            other_i = idx.get(OTHER, -1)
            code = t.map(lambda v, _i=idx, _o=other_i: _i[BLANK] if v == "" else _i.get(v, _o)).astype(int)
            codes["d%d" % j] = code.values
            dim_rec.append({"key": "d%d" % j, "column": c.header(d), "land": d, "levels": levels, "folded": folded,
                            "hidden": hidden})
        g = pd.DataFrame(dict(cols, **codes))
        keys = ["mi"] + list(codes)
        ncells = int(g.groupby(keys).ngroups) if n else 0
        if ncells <= CELLS_MAX or not dims:
            break
        gone = dims.pop()
        R.refuse("the column %s" % c.header(gone), R_CELLS)
    mrec = []
    for j, m in enumerate(meas):
        v = c.nums(m)
        g["s%d" % j] = v.fillna(0.0).values
        g["c%d" % j] = v.notna().astype(int).values
        mrec.append({"key": "m%d" % j, "column": c.header(m), "land": m, "flow": bool(c.is_flow(m)),
                     "unit": c.unit(m)})
    g["n"] = 1
    agg = {"n": "sum"}
    for j in range(len(meas)):
        agg["s%d" % j] = "sum"
        agg["c%d" % j] = "sum"
    cells: List[List[Any]] = []
    if n:
        G = g.groupby(keys, sort=True).agg(agg).reset_index()
        for row in G.itertuples(index=False):
            r = list(row)
            cell = [int(x) for x in r[:len(keys)]] + [int(r[len(keys)])]
            for j in range(len(meas)):
                s = float(r[len(keys) + 1 + 2 * j])
                cell += [_num(s), int(r[len(keys) + 2 + 2 * j])]
            cells.append(cell)
    cube = {"months": months, "dims": [{k: v for k, v in d.items() if k != "land"} for d in dim_rec],
            "measures": [{k: v for k, v in m.items() if k != "land"} for m in mrec], "cells": cells,
            "layout": "cell = [month index (-1: no date), a level index per dim, rows, then sum and filled count per measure]",
            "rows": int(n), "no_date_rows": int((mi < 0).sum()) if n else 0}
    return cube, {d["land"]: d["key"] for d in dim_rec}, {m["land"]: m["key"] for m in mrec}


def _num(v: float) -> float:
    if not math.isfinite(v):
        return 0.0
    r = round(v, 6)
    return int(r) if r == int(r) and abs(r) < 2 ** 53 else r


# ----------------------------------------------------------------------------- the browser's rule, in Python
def _cell_ok(cube: Dict[str, Any], cell: List[Any], filt: Dict[str, Any], where: Optional[Dict[str, Any]]) -> bool:
    months = cube["months"]
    mi = cell[0]
    full = filt.get("from") in (None, months[0] if months else None) and filt.get("to") in (None, months[-1] if months else None)
    if mi < 0:
        if not full:
            return False
    else:
        m = months[mi]
        if filt.get("from") and m < filt["from"]:
            return False
        if filt.get("to") and m > filt["to"]:
            return False
    dk = [d["key"] for d in cube["dims"]]
    for key, lv in (filt.get("cats") or {}).items():
        if key in dk and lv:
            j = dk.index(key)
            if cube["dims"][j]["levels"][cell[1 + j]] not in lv:
                return False
    if where:
        j = dk.index(where["key"])
        hit = cube["dims"][j]["levels"][cell[1 + j]] in where["values"]
        if hit == bool(where.get("exclude")):
            return False
    return True


def query_kpi(cube: Dict[str, Any], kpi: Dict[str, Any], filt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{value, n, suppressed, month}: a tile's figure under the filters (57-dashboard.js computes it the same way)."""
    filt = filt or {}
    nd = len(cube["dims"])
    keys = [m["key"] for m in cube["measures"]]
    cells = [x for x in cube["cells"] if _cell_ok(cube, x, filt, kpi.get("where"))]
    month = None
    if kpi["agg"] == "latest":
        ms = [x[0] for x in cells if x[0] >= 0]
        if ms:
            last = max(ms)
            month = cube["months"][last]
            cells = [x for x in cells if x[0] == last]
        else:
            cells = []
    rows = sum(x[1 + nd] for x in cells)
    if kpi["agg"] == "count" or kpi.get("mkey") is None:
        n, v = rows, float(rows)
    else:
        j = keys.index(kpi["mkey"])
        s = math.fsum(x[2 + nd + 2 * j] for x in cells)
        k = sum(x[3 + nd + 2 * j] for x in cells)
        flow = cube["measures"][j]["flow"]
        n = k
        if kpi["agg"] == "average" or (kpi["agg"] == "latest" and not flow):
            v = s / k if k else None
        else:
            v = s if k else None
    sup = n < SMALL
    return {"value": None if sup or v is None else _num(v), "n": int(n), "suppressed": bool(0 < n < SMALL),
            "month": month}


def query_series(cube: Dict[str, Any], ch: Dict[str, Any], filt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A recomputed chart's points: {months, series: [{level, points: [value or None]}], rows: [[n]], gaps: [months
    with no rows]} for time charts, {bars: [{level, value, n}]} for a bar chart. Suppressed and empty points are None
    (a gap): never filled."""
    filt = filt or {}
    nd = len(cube["dims"])
    keys = [m["key"] for m in cube["measures"]]
    cells = [x for x in cube["cells"] if _cell_ok(cube, x, filt, ch.get("where"))]
    j = keys.index(ch["mkey"]) if ch.get("mkey") else None
    bj = [d["key"] for d in cube["dims"]].index(ch["bkey"]) if ch.get("bkey") else None

    def val(cs: List[List[Any]]) -> Tuple[Optional[float], int]:
        rows = sum(x[1 + nd] for x in cs)
        if j is None:
            return (float(rows) if rows >= SMALL else None), rows
        s = math.fsum(x[2 + nd + 2 * j] for x in cs)
        k = sum(x[3 + nd + 2 * j] for x in cs)
        if k < SMALL:
            return None, k
        return (s if ch["agg"] == "sum" else s / k), k

    if ch["draw"] == "bar":
        lv = cube["dims"][bj]["levels"]
        bars = []
        for i, name in enumerate(lv):
            v, k = val([x for x in cells if x[1 + bj] == i])
            if k:
                bars.append({"level": name, "value": None if v is None else _num(v), "n": k})
        bars.sort(key=lambda b: (b["value"] is None, -(b["value"] or 0), b["level"]))
        return {"bars": bars}
    ms = sorted({x[0] for x in cells if x[0] >= 0})
    if not ms:
        return {"months": [], "series": [], "gaps": []}
    span = NB._month_range(cube["months"][ms[0]], cube["months"][ms[-1]])
    idx = {m: i for i, m in enumerate(cube["months"])}
    groups = [None] if bj is None else list(range(len(cube["dims"][bj]["levels"])))
    series = []
    for gi in groups:
        sub = cells if gi is None else [x for x in cells if x[1 + bj] == gi]
        if not sub:
            continue
        pts = []
        for m in span:
            i = idx.get(m)
            v, _k = val([x for x in sub if x[0] == i]) if i is not None else (None, 0)
            pts.append(None if v is None else _num(v))
        series.append({"level": None if gi is None else cube["dims"][bj]["levels"][gi], "points": pts})
    gaps = [m for m in span if m not in idx or not any(x[0] == idx[m] for x in cells)]
    return {"months": span, "series": series, "gaps": gaps}


# ----------------------------------------------------------------------------- the notes
AGG_WORDS = {"sum": "the total of", "average": "the average of", "count": "the number of", "latest": "the latest month's"}


def definition(R: Read, x: Dict[str, Any], plural: str, measures: Dict[str, Dict[str, Any]]) -> str:
    c = R.ctx
    m = x.get("measure")
    agg = x.get("agg") or ("count" if m is None else "sum")
    if m is None:
        what = "the number of %s" % plural.lower()
    elif agg == "latest":
        what = "%s %s of %s" % (AGG_WORDS["latest"], "total" if c.is_flow(m) else "average", c.header(m))
    elif agg == "count":
        what = "the number of %s" % plural.lower()
    else:
        what = "%s %s" % (AGG_WORDS[agg], c.header(m))
    wh = x.get("where")
    if wh:
        vals = ", ".join(wh["values"])
        what += " over the rows where %s is %s%s" % (c.header(wh["dim"]), "not " if wh.get("exclude") else "", vals)
    if x.get("by"):
        what += ", by %s" % c.header(x["by"])
    if m is not None and agg != "count":
        what += "; %s" % ("a flow: added up" if c.is_flow(m) else "a level: averaged, never added up over time")
        u = x.get("unit_word") or c.unit(m)
        if u:
            what += "; in %s" % u
    return what[0].upper() + what[1:] + "."


def _ranges(months: List[str]) -> List[str]:
    out, run = [], []
    for m in months:
        if run and NB._shift_month(run[-1], 1) == m:
            run.append(m)
        else:
            if run:
                out.append(run)
            run = [m]
    if run:
        out.append(run)
    return ["%s to %s" % (_month_word(r[0]), _month_word(r[-1])) if len(r) > 1 else _month_word(r[0]) for r in out]


def remarks(R: Read, cube: Dict[str, Any], plural: str) -> Dict[str, Any]:
    """The notes the tab and every export carry, in the engine's words."""
    c, rep = R.ctx, R.rep
    cl = rep.get("cleaning") or {}
    rows_in = int(cl.get("rows_in") or (rep.get("input") or {}).get("rows") or 0)
    kept = int(cube["rows"])
    reasons = [{"reason": str(q.get("reason") or "").split(": ", 1)[-1], "count": int(q.get("count") or 0)}
               for q in cl.get("quarantine_reasons") or [] if isinstance(q, dict)]
    for d in ((rep.get("ai_plan") or {}).get("row_drops") or []):
        if isinstance(d, dict) and d.get("rows"):
            reasons.append({"reason": "the plan set them aside: %s" % _text(d.get("why") or d.get("op") or "", 120),
                            "count": int(d.get("rows") or 0)})
    hidden = [str(f.get("column")) for f in c.flagged] + [c.header(str(f.get("column"))) for f in c.flagged]
    hid_rx = re.compile(r"\b(%s)\b" % "|".join(re.escape(h) for h in hidden if h), re.I) if hidden else None

    def clean_line(s: Any) -> Optional[str]:
        t = " ".join(str(s or "").split())
        if not t or (hid_rx is not None and hid_rx.search(t)):
            return None
        return _text(c.pub(t), 300)

    check: List[str] = []
    for s in ((rep.get("health") or {}).get("issues") or [])[:14]:
        t = clean_line(s)
        if t:
            check.append(t)
    for fx in (cl.get("fixes") or [])[:12]:
        if not isinstance(fx, dict):
            continue
        col = fx.get("column")
        t = clean_line("Cleaning: %s (%s values%s)." % (str(fx.get("what") or fx.get("rule") or "").rstrip("."),
                                                      NV._count(fx.get("count") or 0),
                                                      ", " + c.header(str(col)) if col else ""))
        if t:
            check.append(t)
    for q in reasons:
        if q["count"]:
            check.append("Set aside: %s %s, %s." % (NV._count(q["count"]), plural.lower() if q["count"] != 1 else
                                                     row_noun(rep)[1], q["reason"]))
    months = cube["months"]
    if months:
        span = NB._month_range(months[0], months[-1])
        have = set(months)
        gaps = [m for m in span if m not in have]
        if gaps:
            check.append("Months with no rows, left as gaps (never filled): %s." % "; ".join(_ranges(gaps)[:12]))
        nd = len(cube["dims"])
        by_m: Dict[int, int] = {}
        for x in cube["cells"]:
            if x[0] >= 0:
                by_m[x[0]] = by_m.get(x[0], 0) + x[1 + nd]
        thin = [months[i] for i, k in sorted(by_m.items()) if k < SMALL]
        if thin:
            check.append("Months with fewer than %d rows (their figures are shown as “<5”): %s." % (
                SMALL, "; ".join(_ranges(thin)[:12])))
    if cube.get("no_date_rows"):
        check.append("%s %s have no date the engine reads: they count only while the date range is the whole span." % (
            NV._count(cube["no_date_rows"]), plural.lower()))
    for d in cube["dims"]:
        if d.get("folded"):
            check.append("%s: its %s smallest values are counted together as “other”." % (
                d["column"], NV._count(d["folded"])))
        if d.get("hidden"):
            check.append("%s: %s a word with a column you withheld or coded, so %s counted in “other” and never "
                         "named." % (d["column"], "1 value shares" if d["hidden"] == 1 else
                                     "%s values share" % NV._count(d["hidden"]),
                                     "it is" if d["hidden"] == 1 else "they are"))
    for r_ in R.refused:
        check.append("Not on this dashboard: %s, because %s." % (r_["element"], r_["why"].rstrip(".")))
    return {"data_range": [months[0], months[-1]] if months else None,
            "data_range_text": ("%s to %s" % (_month_word(months[0]), _month_word(months[-1]))) if months else
            "the file has no date the engine reads",
            "rows": {"in": rows_in, "kept": kept, "set_aside": max(0, rows_in - kept) if rows_in else 0,
                     "reasons": reasons},
            "check": check[:60]}


# ----------------------------------------------------------------------------- the record
def blank(why: str = "") -> Dict[str, Any]:
    return {"version": VERSION, "ok": False, "why": why, "chosen_by": "none"}


def recorded_design(rep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The recorded AI design of the site's sample ledger, when the file is that sample byte for byte (its sha256),
    else None."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), RECORDED_FILE), encoding="utf-8") as fh:
            fx = json.load(fh)
    except Exception:  # noqa: BLE001 - no fixture, no recorded design
        return None
    sha = str((rep.get("input") or {}).get("sha256") or "")
    if not sha or sha != str(fx.get("input_sha256") or ""):
        return None
    d = fx.get("dashboard")
    return dict(d, _recorded=str(fx.get("recorded") or "")) if isinstance(d, dict) else None


def build(rep: Dict[str, Any], ctx_in: Dict[str, Any]) -> Dict[str, Any]:
    """rep["dashboard"]: see the module's docstring and CONTRACT-v2.md 5.14. Never raises (a refusal instead)."""
    st = rep.get("structure") if isinstance(rep.get("structure"), dict) else None
    if st is not None and (st.get("kind") == "cube_incomplete" or (st.get("kind") == "cube" and st.get("usable"))):
        return blank("the file is a table of published series with totals beside their parts: a dashboard that adds "
                     "its rows up would count them twice")
    if not (rep.get("downloads") or {}).get("clean_csv"):
        return blank("the engine kept no rows")
    R = Read(rep, ctx_in)
    c = R.ctx
    if c.frame is None or not len(c.frame):
        return blank("the engine kept no rows")
    plan = ctx_in.get("plan") if isinstance(ctx_in.get("plan"), dict) else None
    block = plan.get("dashboard") if plan and isinstance(plan.get("dashboard"), dict) else None
    chosen = "ai" if block is not None else "engine"
    recorded = ""
    if block is None:
        rd = ctx_in.get("recorded")
        if rd is None:
            rd = recorded_design(rep)
        if isinstance(rd, dict):
            block, chosen, recorded = rd, "recorded", str(rd.get("_recorded") or "")
    if block is not None:
        design = validate(R, block, ai=True)
        if not design["kpis"] and not design["charts"]:
            R.refuse("the AI's design", "none of its tiles or charts could be built, so the engine designed this one")
            design, chosen = default_design(R), "engine"
    else:
        design = default_design(R)
    pins = [x for x in (ctx_in.get("pins") or []) if isinstance(x, dict)][:PINS_MAX]
    if pins:
        seen = {k["id"] for k in design["kpis"]} | {x["id"] for x in design["charts"]}
        got = validate(R, {"charts": [dict(p, id=None) for p in pins]}, ai=True)["charts"]
        for i, ch in enumerate(got):
            pid = "pin%d" % (i + 1)
            while pid in seen:
                pid += "_"
            seen.add(pid)
            design["charts"].append(dict(ch, id=pid, pinned=True))
            design["layout"].append([pid])
    cube, dkey, mkey = make_cube(R, design)
    plural, sing = row_noun(rep)
    measures = {m["column"]: m for m in cube["measures"]}

    def keyed(x: Dict[str, Any]) -> Any:
        y = dict(x)
        if y.get("by") and y["by"] not in dkey:
            return None
        if y.get("where") and y["where"]["dim"] not in dkey:
            return None
        y["mkey"] = mkey.get(y["measure"]) if y.get("measure") else None
        y["bkey"] = dkey.get(y["by"]) if y.get("by") else None
        if y.get("where"):
            y["where"] = dict(y["where"], key=dkey[y["where"]["dim"]], column=c.header(y["where"]["dim"]))
            y["where"].pop("dim", None)
            lv = next(d["levels"] for d in cube["dims"] if d["key"] == y["where"]["key"])
            gone = [v for v in y["where"]["values"] if v not in lv]
            y["where"]["values"] = [v for v in y["where"]["values"] if v in lv]
            if gone:
                R.refuse("%s %s" % ("tile" if "draw" not in x else "chart", x.get("label") or x.get("title") or x["id"]),
                         "a value it filters on is counted in \u201cother\u201d on this dashboard")
                return False
        y["definition"] = definition(R, x, plural, measures)
        y["measure"] = c.header(x["measure"]) if x.get("measure") else None
        y["by"] = c.header(x["by"]) if x.get("by") else None
        return y

    kpis, charts = [], []
    for k in design["kpis"]:
        y = keyed(k)
        if y is False:
            continue
        if y is None:
            R.refuse("tile %s" % (k.get("label") or k["id"]), R_DIMS)
            continue
        if not y.get("label"):
            y["label"] = (AGG_WORDS.get(y["agg"], "") + " " + (y["measure"] or plural.lower())).strip().capitalize()
        kpis.append(y)
    for ch in design["charts"]:
        rec = ch.get("record")
        y = keyed(dict(ch, record=None))
        if y is False:
            continue
        if y is None:
            R.refuse("chart %s" % (ch.get("title") or ch["chart"]), R_DIMS)
            continue
        if rec is not None:
            y["record"] = rec
        else:
            y.pop("record", None)
        if not y.get("title"):
            y["title"] = _title(c, y, plural)
        charts.append(y)
    filters = []
    for f in design["filters"]:
        if f["kind"] == "date":
            filters.append({"kind": "date", "column": c.header(c.date)})
        elif f["dim"] in dkey:
            filters.append({"kind": "category", "key": dkey[f["dim"]], "column": c.header(f["dim"])})
    ids = {k["id"] for k in kpis} | {x["id"] for x in charts}
    questions = [dict(q, answered_by=[i for i in q["answered_by"] if i in ids]) for q in design["questions"]]
    questions = [q for q in questions if q["answered_by"]]
    layout = [[i for i in row if i in ids] for row in design["layout"]]
    layout = [r for r in layout if r]
    defaults = {"kpis": {k["id"]: query_kpi(cube, k) for k in kpis}}
    dims_used = [c.header(d) for d in c.dims]
    grain = {"text": "One row is one %s: %s." % (sing, _grain_words(c)), "noun": plural, "by": "engine"}
    columns = _column_meanings(c)
    out = {"version": VERSION, "ok": True, "chosen_by": chosen,
           "recorded": recorded if chosen == "recorded" else "",
           "audience": design["audience"], "questions": questions, "grain": grain, "columns": columns, "kpis": kpis, "charts": charts,
           "filters": filters, "layout": layout, "cube": cube, "defaults": defaults,
           "remarks": remarks(R, cube, plural), "refused": list(R.refused), "suppress_below": SMALL,
           "rules": ["Flows are added up; levels, rates and balances are averaged, never added up over time.",
                     "A figure that rests on fewer than %d rows is shown as “<5”, after every filter." % SMALL,
                     "A month with no rows is a gap: nothing is filled, smoothed or interpolated.",
                     "Columns you withheld or coded are never a filter, a label or a group."],
           "title": "%s dashboard" % (NV._cut(str((rep.get("input") or {}).get("name") or "Data"), 60))}
    del dims_used
    return out


def _column_meanings(c: Any) -> List[Dict[str, str]]:
    """What the engine reads each column it kept as (never a flagged column): the step-1 card's list."""
    out = []
    if c.date:
        out.append({"column": c.header(c.date), "reads_as": "the date (months)", "unit": ""})
    for m in c.measures:
        out.append({"column": c.header(m), "reads_as": "a number: a flow, added up" if c.is_flow(m) else
                    "a number: a level, averaged (never added up over time)", "unit": c.unit(m)})
    for d in c.dims:
        n = len(c.levels_all(d))
        out.append({"column": c.header(d), "reads_as": "a category of %s values" % NV._count(n), "unit": ""})
    return out[:40]


def _grain_words(c: Any) -> str:
    parts = []
    if c.measures:
        parts.append(" and ".join("a %s" % c.header(m) for m in c.measures[:3]))
    if c.date:
        parts.append("on a %s" % c.header(c.date))
    dims = [c.header(d) for d in c.dims if c.role_ok(d, "category") == ""][:4]
    if dims:
        parts.append("for a " + ", a ".join(dims))
    return " ".join(parts) or "a record of the file"


def _title(c: Any, y: Dict[str, Any], plural: str) -> str:
    what = y["measure"] or plural
    if y.get("where"):
        what += " (%s %s)" % ("not" if y["where"].get("exclude") else "", ", ".join(y["where"]["values"][:3]))
        what = what.replace("( ", "(")
    if y["draw"] == "line":
        return NV._cut("%s by month%s" % (what, (", by " + y["by"]) if y.get("by") else ""), 90)
    if y["draw"] == "area":
        return NV._cut("%s by month, split by %s" % (what, y["by"]), 90)
    if y["draw"] == "bar":
        return NV._cut("%s by %s" % (what, y["by"]), 90)
    if y["draw"] == "grouped":
        return NV._cut("%s by %s: the latest 12 months against the 12 before" % (what, y["by"]), 90)
    if y["draw"] == "multiples":
        return NV._cut("%s by month, one panel for each %s" % (what, y["by"]), 90)
    rec = y.get("record") or {}
    return NV._cut(str(rec.get("title") or y["chart"].replace("_", " ")), 90)


def for_ai(d: Any) -> Optional[Dict[str, Any]]:
    """results_for_ai's summary of the dashboard: its design, never its cube (the rows' aggregates stay in the page)."""
    if not isinstance(d, dict) or not d.get("ok"):
        return None
    return {"chosen_by": d.get("chosen_by"),
            "kpis": [{"id": k["id"], "label": k.get("label"), "agg": k.get("agg"), "measure": k.get("measure")}
                     for k in d.get("kpis") or []][:KPI_MAX],
            "charts": [{"id": x["id"], "chart": x.get("chart"), "title": x.get("title")} for x in d.get("charts") or []][:CHART_MAX],
            "refused": len(d.get("refused") or [])}
