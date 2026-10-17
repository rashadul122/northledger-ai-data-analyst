"""nl_pack: the monthly reporting pack (Track E of plan/DASHBOARD-DESIGN.md; CONTRACT-v2 §5.18).

Pack assembly only. The files are read by the engine's own run (nl_browser.input_json / run_json: profile, cleaning,
personal-data choices); this module reads the rows each run KEPT (nl_browser.input_frames) and assembles the pack:

  * where the actual and the plan figures are: a scenario column (Actual / Budget / Plan / Forecast members), paired
    columns ("revenue" beside "revenue_budget", "nps" beside "nps_target"), or a file that is all plan (its scenario
    column names only plans, every measure is named as a plan, or its file name says so); a long table's line column
    ("line", "metric", "kpi", "account" ...) when a file holds one value per line;
  * the reporting month: the latest month with actual figures, stepped back one when a file of daily rows stops before
    that month's end and the analysis date is not past it (the month is not complete);
  * per metric line: this month, last month, the change (amount and percent), the plan, the variance (amount and
    percent), and year to date actual against year to date plan (flows only: a level, a stock or a rate is never summed
    over months; a rate's change is in percentage points); gaps stay gaps: a month with no value is not filled, and a
    line without one of the months has no figure for it, with the reason;
  * the watch list: every line whose |change %| or |variance %| (or the amounts, the visitor's choice) is over ONE
    threshold;
  * a cause only where the engine's own decomposition attributes the move from the data: the month's change by a
    grouping column (nl_analyst op_change, the same step Ask your data runs) or the variance by a grouping column both
    the actual and the plan files carry; the cause itself is always "driver to be confirmed";
  * notes (definitions, sources, month, threshold, assumptions, what looked wrong), the inputs checklist for next month
    and a recipe (JSON) that makes the same pack from next month's files.

Every figure is computed here from the kept rows; the page writes the Excel workbook (src/js/56-xlsx.js, formulas with
these figures as cached values) and the Word cover note (src/js/46-report-docx.js) from this record and adds none.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

VERSION = 1
KIND = "monthly-pack"
THRESHOLD_DEFAULT = 0.10               # 10% (basis percent); an amount when the basis is "amount"
LINES_MAX = 40                         # metric lines in a pack at most (the first ones, in file order)
HEADLINE_MAX = 5
DIMS_MAX = 4                           # grouping columns tried for where a change sits
DIM_LEVELS = (2, 12)
ATTRIB_SHARE = 0.5                     # a member is named when it carries at least half the change, in its direction
LINE_WORDS = ("line", "lines", "metric", "metrics", "kpi", "kpis", "measure", "indicator", "account", "accounts",
              "item", "gl", "description", "line_item")
LINE_WORDS_WEAK = ("category", "type", "class")
# a level (an average or a count at a date, never summed over months) the engine's words do not name
LEVEL_EXTRA = ("active", "customers", "subscribers", "members", "users", "headcount", "employees", "fte", "staff",
               "nps", "csat", "satisfaction", "score", "rating", "index", "backlog", "open", "inventory", "stock",
               "balance", "cash", "outstanding", "accounts")
FLOW_FIRST = ("new", "added", "orders", "tickets", "sales", "revenue", "cost", "costs", "spend", "units", "hires",
              "leavers", "signups", "calls", "visits", "sessions", "income", "expense", "expenses", "amount")
RATE_EXTRA = ("pct", "percent", "rate", "ratio", "margin", "share", "churn", "conversion", "yield", "utilisation",
              "utilization")
REV_RX = re.compile(r"^(revenue|sales|net_sales|turnover|income|gross_sales)$")
COST_RX = re.compile(r"^(cost|costs|cogs|cost_of_sales|cost_of_goods|cost_of_goods_sold)$")
CAUSE = "driver to be confirmed"


def _nb():
    import nl_browser as nb
    return nb


def _slug(s: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(s or "").lower())).strip("_")


def _label(stem: str) -> str:
    toks = [("%" if t in ("pct", "percent") else t) for t in _slug(stem).split("_") if t]
    t = " ".join(toks) or str(stem)
    t = re.sub(r"\bnps\b", "NPS", t)
    t = re.sub(r"\bkpi\b", "KPI", t)
    return t[:1].upper() + t[1:]


def _toks(s: Any) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(s or "").lower()) if t]


def _month(d: Any) -> Optional[str]:
    try:
        return d.strftime("%Y-%m")
    except (AttributeError, ValueError):
        return None


def _shift(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) + k
    return "%04d-%02d" % (t // 12, t % 12 + 1)


def _month_end(ym: str) -> _dt.date:
    nx = _shift(ym, 1)
    return _dt.date(int(nx[:4]), int(nx[5:7]), 1) - _dt.timedelta(days=1)


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def month_label(ym: Optional[str]) -> str:
    if not ym:
        return ""
    return "%s %s" % (MONTHS[int(ym[5:7]) - 1], ym[:4])


def _fin(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(v: Optional[float], d: int = 6) -> Optional[float]:
    return None if v is None else round(v, d)


# ----------------------------------------------------------------------------- reading each file
def kind_of(stem: str, values: Any) -> str:
    """flow (summed over months), stock (a count at a date: summed across groups, never over months), level (an average
    or a score), or rate (a percent: changes in points). The engine's own words first (nl_analyst), then the pack's."""
    import nl_analyst as na
    toks = set(_toks(stem))
    k = na._measure_kind_words(stem, values)
    if k == "rate" or toks & set(RATE_EXTRA):
        return "rate"
    if k in ("stock", "level"):
        return k
    if toks & set(FLOW_FIRST) and not toks & {"active", "open", "outstanding", "balance", "backlog"}:
        return "flow"
    if toks & set(LEVEL_EXTRA):
        return "stock" if toks & {"headcount", "employees", "fte", "staff", "active", "customers", "subscribers",
                                  "members", "users", "inventory", "stock", "backlog", "open", "outstanding"} else "level"
    return k


def _no_pct(stem: str, kind: str) -> bool:
    """A change of this line is said in points or units, never in percent of itself (a rate, a score)."""
    return kind == "rate" or bool(set(_toks(stem)) & {"nps", "score", "rating", "index", "csat", "satisfaction"})


def _line_column(df: Any, kinds: Dict[str, str], skip: List[str], n_values: int) -> Optional[str]:
    texts = [c for c, k in kinds.items() if k == "text" and c not in skip]
    for words in (LINE_WORDS, LINE_WORDS_WEAK if n_values == 1 else ()):
        for c in texts:
            toks = _toks(c)
            if toks and set(toks) & set(words) and set(toks) <= set(words) | {"name", "names", "pl", "p", "l", "of"}:
                n = df[c].astype(str).str.strip().replace("", None).dropna().nunique()
                if 1 <= n <= LINES_MAX:
                    return c
    return None


def _dims(df: Any, kinds: Dict[str, str], skip: List[str]) -> List[str]:
    out = []
    for c, k in kinds.items():
        if k != "text" or c in skip:
            continue
        s = df[c].astype(str).str.strip()
        n = s[s != ""].nunique()
        if DIM_LEVELS[0] <= n <= DIM_LEVELS[1] and n < 0.5 * max(1, len(df)):
            out.append(c)
    return out[:DIMS_MAX]


def read_input(inp: Dict[str, Any], recipe_in: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One file as pack records: [{line, stem, scen, month, value, n, date}] plus how it was read (its mapping)."""
    import pandas as pd
    nb = _nb()
    df, kinds, s = inp.get("df"), dict(inp.get("kinds") or {}), inp.get("summary") or {}
    name = str(inp.get("name") or "")
    out: Dict[str, Any] = {"id": inp.get("id"), "name": name, "role": inp.get("role"), "records": [], "mapping": None,
                           "why": "", "dims": [], "frame": None}
    if df is None or not len(df):
        out["why"] = "the engine kept no rows of it"
        return out
    if inp.get("role") in ("deals", "tasks", "list", "other"):
        out["why"] = "it is read as %s, not as monthly figures" % str(inp.get("role")).replace("_", " ")
        return out
    dates = [c for c, k in kinds.items() if k == "date"]
    date = (s.get("months") or {}).get("column") if (s.get("months") or {}).get("column") in dates else (dates[0] if dates else None)
    if not date:
        out["why"] = "it has no date column, and a monthly pack needs months"
        return out
    nums = [c for c, k in kinds.items() if k == "number"]
    if not nums:
        out["why"] = "it has no measures"
        return out
    deb = [c for c in nums if set(_toks(c)) & {"debit", "debits", "dr"}]
    cre = [c for c in nums if set(_toks(c)) & {"credit", "credits", "cr"}]
    if len(deb) == 1 and len(cre) == 1:
        # a trial balance's lines: debit minus credit for each account (the net: a credit balance is negative), one
        # figure per account and month
        df = df.copy()
        df["net"] = pd.to_numeric(df[deb[0]], errors="coerce").fillna(0) - pd.to_numeric(df[cre[0]], errors="coerce").fillna(0)
        df.loc[df[deb[0]].isna() & df[cre[0]].isna(), "net"] = float("nan")
        nums = [c for c in nums if c not in (deb[0], cre[0])] + ["net"]
        kinds = dict(kinds, net="number")
    sc = nb.scenario_column(df, kinds)
    default_scen = "plan" if inp.get("role") == "plan" else "actual"
    cols = []                                         # (column, stem, scenario or None: from the scenario column)
    for c in nums:
        stem, scen = nb.header_scenario(c)
        cols.append((c, stem or _slug(c), scen))
    stems = sorted({st for _c, st, _s in cols})
    line_col = _line_column(df, kinds, [date] + ([sc["column"]] if sc else []), len(stems))
    skip = [date] + ([sc["column"]] if sc else []) + ([line_col] if line_col else [])
    dims = _dims(df, kinds, skip)
    mapping = {"pattern": name_pattern(name), "name": name, "role": inp.get("role"), "date": date,
               "scenario": sc, "line_column": line_col, "values": [], "dims": dims}
    month = df[date].map(_month)
    recs = []
    for c, stem, scen in cols:
        v = pd.to_numeric(df[c], errors="coerce")
        if sc:
            member = df[sc["column"]].astype(str).str.strip()
            reads = {m["value"]: m["reads_as"] for m in sc["members"]}
            scen_s = member.map(lambda x: reads.get(x)) if scen is None else pd.Series(scen, index=df.index)
        else:
            scen_s = pd.Series(scen or default_scen, index=df.index)
        lines = df[line_col].astype(str).str.strip() if line_col else pd.Series(stem, index=df.index)
        if line_col and len(stems) > 1:
            lines = lines + " " + _label(stem).lower()
        frame = pd.DataFrame({"line": lines, "scen": scen_s, "month": month, "value": v, "date": df[date]})
        frame = frame.dropna(subset=["month", "scen"])
        frame = frame[frame["line"].astype(str) != ""]
        recs.append((c, stem, frame))
        reads_as = ("from %s" % sc["column"]) if sc and scen is None else (scen or default_scen)
        mapping["values"].append({"column": c, "stem": stem, "reads_as": reads_as})
    out.update(records=recs, mapping=mapping, dims=dims, frame=df, date=date, scen_col=sc, line_col=line_col)
    if recipe_in:
        miss = [x.get("column") for x in recipe_in.get("values") or [] if x.get("column") not in df.columns]
        if miss:
            out["recipe_missing"] = miss
    return out


def nb_scen_from_column(col: str) -> bool:
    """A measure whose scenario comes from the file's scenario column (its header names none)."""
    return _nb().header_scenario(col)[1] is None


def name_pattern(name: str) -> str:
    """A file name with its digits as wildcards: next month's export of the same report matches it."""
    return re.sub(r"\d+", "*", str(name or ""))


def _match(pattern: str, name: str) -> bool:
    rx = "^" + re.escape(str(pattern or "")).replace(r"\*", r"\d+") + "$"
    return bool(re.match(rx, str(name or ""), re.I))


# ----------------------------------------------------------------------------- months
def _aggregate(frame: Any, kind: str) -> Tuple[Dict[str, float], Dict[str, int], str]:
    """{month: value} for one line and scenario: a flow summed; a stock taken at the latest date in the month (summed
    across the rows of that date); a level or a rate averaged over the month's rows. Months with no value are absent."""
    f = frame.dropna(subset=["value"])
    vals: Dict[str, float] = {}
    cnt: Dict[str, int] = {}
    if f.empty:
        return vals, cnt, ""
    for m, g in f.groupby("month"):
        if kind == "flow":
            v = float(g["value"].sum())
        elif kind == "stock":
            last = g["date"].max()
            v = float(g.loc[g["date"] == last, "value"].sum())
        else:
            v = float(g["value"].mean())
        vals[str(m)] = v
        cnt[str(m)] = int(len(g))
    multi = max(cnt.values()) > 1
    how = {"flow": "the sum of the month's rows",
           "stock": "the value at the month's latest date" + (", added across the rows of that date" if multi else ""),
           "level": "the average of the month's rows" if multi else "the month's value",
           "rate": "the average of the month's rows (an unweighted average of rates)" if multi else "the month's value"}[kind]
    return vals, cnt, how


# ----------------------------------------------------------------------------- the pack
def build(inputs: List[Dict[str, Any]], settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    st = dict(settings or {})
    basis = "amount" if st.get("basis") == "amount" else "percent"
    # the threshold in the visitor's unit: percent (10 = 10%) on the percent basis, the lines' own units on the amount basis
    thr_in = _fin(st.get("threshold"))
    if thr_in is None or thr_in < 0:
        thr_in = THRESHOLD_DEFAULT * 100 if basis == "percent" else 0.0
    thr = thr_in / 100.0 if basis == "percent" else thr_in
    compare = "forecast" if st.get("compare") == "forecast" else "plan"
    try:
        fy = int(st.get("fiscal_start") or 1)
    except (TypeError, ValueError):
        fy = 1
    fy = fy if 1 <= fy <= 12 else 1
    as_of = str(st.get("as_of") or "")[:10]
    try:
        as_of_d = _dt.date.fromisoformat(as_of) if as_of else _dt.date.today()
    except ValueError:
        as_of_d = _dt.date.today()
    readers = str(st.get("readers") or "").strip()[:200]
    want = [w for w in (_slug(x) for x in (st.get("metrics") if isinstance(st.get("metrics"), list)
                                            else str(st.get("metrics") or "").split(","))) if w]
    kinds_set = st.get("kinds") if isinstance(st.get("kinds"), dict) else {}
    recipe = st.get("recipe") if isinstance(st.get("recipe"), dict) else {}
    out: Dict[str, Any] = {"ok": False, "error": "", "kind": KIND, "version": VERSION, "readers": readers,
                           "threshold": thr, "threshold_input": thr_in, "basis": basis, "compare": compare, "fiscal_start": fy, "as_of": as_of_d.isoformat(),
                           "lines": [], "watch": [], "headline": [], "inputs_used": [], "refused": [],
                           "notes": {"definitions": [], "assumptions": [], "wrong": [], "sources": []}, "checklist": [],
                           "recipe": None, "month": None, "prev_month": None, "ytd_months": []}

    # 1. read every file
    read = []
    for inp in inputs:
        rin = next((x for x in recipe.get("inputs") or [] if isinstance(x, dict) and _match(x.get("pattern"), inp.get("name"))), None)
        r = read_input(inp, rin)
        r["summary"] = inp.get("summary") or {}
        r["analyst"] = inp.get("analyst") or {}
        r["kinds"] = inp.get("kinds") or {}
        if r["records"]:
            read.append(r)
            if r.get("recipe_missing"):
                out["notes"]["wrong"].append("Last time's recipe read %s in %s; this file has no such column." % (
                    ", ".join(r["recipe_missing"]), r["name"]))
        else:
            out["refused"].append({"input": r["name"], "why": r["why"]})
    if not read:
        out["error"] = "none of this run's files has monthly figures the pack can read (a date column and measures)"
        return out

    # 2. lines: (key) -> {actual: {...}, plan: {...}, forecast: {...}}; the first file read wins a line's scenario
    lines: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in read:
        for col, stem, frame in r["records"]:
            for (line, scen), g in frame.groupby(["line", "scen"], sort=False):
                key = _slug(line)
                if not key:
                    continue
                L = lines.get(key)
                if L is None:
                    if len(order) >= LINES_MAX:
                        continue
                    L = lines[key] = {"key": key, "label": _label(line) if r["line_col"] is None else str(line),
                                      "stem": stem, "src": {}}
                    order.append(key)
                if scen in L["src"]:
                    if L["src"][scen]["input"] != r["name"]:
                        out["notes"]["wrong"].append("%s (%s) is in %s and in %s: the pack reads %s." % (
                            L["label"], scen, L["src"][scen]["input"], r["name"], L["src"][scen]["input"]))
                    continue
                where = None
                if r["scen_col"] is not None and nb_scen_from_column(col):
                    mem = sorted({str(x) for x in r["frame"].loc[g.index, r["scen_col"]["column"]].astype(str)})
                    where = {r["scen_col"]["column"]: mem}
                if r["line_col"]:
                    where = dict(where or {}, **{r["line_col"]: [str(line)]})
                L["src"][scen] = {"input": r["name"], "input_id": r["id"], "column": col, "frame": g, "where": where,
                                  "read": r}
    # the kind of each line (from its actual values, else its plan's); the visitor's setting wins
    for key in order:
        L = lines[key]
        base = L["src"].get("actual") or L["src"].get(compare) or next(iter(L["src"].values()))
        k = kind_of(L["stem"] if not base["read"]["line_col"] else L["label"], base["frame"]["value"])
        if kinds_set.get(key) in ("flow", "stock", "level", "rate"):
            k, L["kind_by"] = kinds_set[key], "visitor"
        else:
            L["kind_by"] = "engine"
        L["kind"] = k
        for scen, src in L["src"].items():
            src["vals"], src["n"], src["how"] = _aggregate(src["frame"], k)

    # 3. the reporting month: the latest month with actual figures, if complete
    act_months = sorted({m for L in lines.values() if "actual" in L["src"] for m in L["src"]["actual"]["vals"]})
    if not act_months:
        out["error"] = "no file has actual figures by month (only plans)"
        return out
    month = str(st.get("month") or "")
    month_why = ""
    if month and re.match(r"^\d{4}-\d{2}$", month) and month in act_months:
        month_why = "you chose it"
    else:
        month = act_months[-1]
        month_why = "the latest month with actual figures"
        for r in read:
            s = r["summary"].get("months") or {}
            if s.get("grain") in ("day", "week") and s.get("last") == month and any(
                    src["read"] is r for L in lines.values() for sc, src in L["src"].items() if sc == "actual"):
                last = _dt.date.fromisoformat(s["last_date"])
                end = _month_end(month)
                if last < end and as_of_d <= end and len(act_months) > 1:
                    month_why = ("%s is not complete: %s runs to %s and the analysis date is %s, so the pack reports "
                                 "the month before" % (month_label(month), r["name"], last.isoformat(), as_of_d.isoformat()))
                    month = act_months[-2]
                    break
    prev = _shift(month, -1)
    fy_start = "%s-%02d" % (month[:4] if int(month[5:7]) >= fy else str(int(month[:4]) - 1), fy)
    ytd = []
    m = fy_start
    while m <= month:
        ytd.append(m)
        m = _shift(m, 1)
    out.update(month=month, prev_month=prev, month_label=month_label(month), prev_label=month_label(prev),
               month_why=month_why, ytd_months=ytd, ytd_label="%s to %s" % (month_label(ytd[0]), month_label(month)))

    # 4. figures per line
    rows = []
    for key in order:
        L = lines[key]
        a = L["src"].get("actual")
        p = L["src"].get(compare)
        if a is None:
            out["notes"]["wrong"].append("%s has a %s but no actual figures; it is not in the pack." % (L["label"], compare))
            continue
        if want and not any(w in key or key in w for w in want):
            continue
        k = L["kind"]
        A, P = a["vals"], (p["vals"] if p else {})
        cur, prv = A.get(month), A.get(prev)
        scale = 100.0 if k == "rate" and all(abs(v) <= 1.5 for v in A.values()) else 1.0
        row: Dict[str, Any] = {"key": key, "label": L["label"], "kind": k, "kind_by": L["kind_by"],
                               "points": _no_pct(L["stem"], k), "scale": scale,
                               "cur": _r(cur), "prev": _r(prv), "plan": _r(P.get(month)) if p else None,
                               "actual": {"input": a["input"], "column": a["column"], "how": a["how"], "where": a["where"]},
                               "planned": ({"input": p["input"], "column": p["column"], "how": p["how"], "where": p["where"]} if p else None),
                               "series": {mm: _r(A[mm]) for mm in sorted(A) if _shift(month, -12) <= mm <= month},
                               "gaps": [], "derived": None}
        _changes(row)
        # gaps in the year: months with no actual value between the first and the reporting month (left empty)
        first = min(A) if A else month
        mm = max(first, _shift(month, -12))
        while mm <= month:
            if mm not in A:
                row["gaps"].append(mm)
            mm = _shift(mm, 1)
        if row["gaps"]:
            out["notes"]["wrong"].append("%s has no value for %s in %s: left empty, not filled." % (
                L["label"], ", ".join(month_label(g) for g in row["gaps"]), a["input"]))
        if p is None:
            out["notes"]["wrong"].append("%s has no %s in any file: its plan columns are empty." % (L["label"], compare))
        _ytd(row, k, A, P if p else None, ytd)
        rows.append(row)

    # derived lines: gross profit and gross margin from revenue and cost (when both are flows of the same files)
    rev = next((x for x in rows if REV_RX.match(x["key"]) and x["kind"] == "flow"), None)
    cost = next((x for x in rows if COST_RX.match(x["key"]) and x["kind"] == "flow"), None)
    if rev and cost and rev["actual"]["input"] == cost["actual"]["input"] and len(rows) < LINES_MAX:
        rows = _derived(rows, rev, cost, lines, compare, month, prev, ytd)

    if not rows:
        out["error"] = "no metric line has actual figures%s" % (" among the metrics you chose" if want else "")
        return out

    # 5. the watch list, and where each flagged move sits
    for row in rows:
        why = []
        for k_, pk, word in (("mom", "mom_pct", "the month's change"), ("var", "var_pct", "the variance to %s" % compare)):
            v = row.get(pk) if basis == "percent" else row.get(k_)
            if v is not None and abs(v) > thr + 1e-12:
                why.append(word)
        row["watch"] = bool(why)
        row["watch_why"] = why
        row["driver"] = None
        row["cause"] = CAUSE
        if why:
            row["driver"] = _where(row, lines, read, month, prev, compare)
    out["watch"] = [r["key"] for r in rows if r["watch"]]
    rank = sorted(rows, key=lambda r: -max(abs(r.get("mom_pct") or 0), abs(r.get("var_pct") or 0)))
    out["headline"] = [r["key"] for r in rank if r["watch"]][:HEADLINE_MAX]
    for r in rank:
        if len(out["headline"]) >= 3:
            break
        if r["key"] not in out["headline"] and (r.get("mom_pct") is not None or r.get("var_pct") is not None):
            out["headline"].append(r["key"])
    out["lines"] = rows

    # 6. notes, checklist, recipe
    _notes(out, rows, read, lines, compare, thr, basis, fy)
    out["checklist"] = _checklist(read, rows, month)
    out["recipe"] = {"kind": KIND, "version": VERSION, "settings": {
        "readers": readers, "metrics": [r["key"] for r in rows if not r.get("derived")] if want else [],
        "threshold": thr_in, "basis": basis, "compare": compare, "fiscal_start": fy, "month": "",
        "kinds": {r["key"]: r["kind"] for r in rows if r.get("kind_by") == "visitor"}},
        "inputs": [dict(r["mapping"], role=r["role"]) for r in read]}
    out["inputs_used"] = [{"id": r["id"], "name": r["name"], "role": r["role"],
                           "lines": sorted({_slug(l) for _c, _s, f in r["records"] for l in f["line"].unique()})[:LINES_MAX]}
                          for r in read]
    out["ok"] = True
    return out


def _changes(row: Dict[str, Any]) -> None:
    cur, prv, plan, scale = row["cur"], row["prev"], row["plan"], row["scale"]
    row["mom"] = _r((cur - prv) * scale) if cur is not None and prv is not None else None
    row["mom_pct"] = _r((cur - prv) / abs(prv)) if cur is not None and prv not in (None, 0) else None
    row["var"] = _r((cur - plan) * scale) if cur is not None and plan is not None else None
    row["var_pct"] = _r((cur - plan) / abs(plan)) if cur is not None and plan not in (None, 0) else None


def _ytd(row: Dict[str, Any], k: str, A: Dict[str, float], P: Optional[Dict[str, float]], ytd: List[str]) -> None:
    row.update(ytd_act=None, ytd_plan=None, ytd_var=None, ytd_var_pct=None, ytd_why="")
    if k != "flow":
        row["ytd_why"] = {"stock": "a count at a date is not added up over months",
                          "level": "an average or a score is not added up over months",
                          "rate": "a rate is not added up over months"}[k]
        return
    miss = [m for m in ytd if m not in A]
    if miss:
        row["ytd_why"] = "no actual figure for %s" % ", ".join(month_label(m) for m in miss[:3])
        return
    row["ytd_act"] = _r(sum(A[m] for m in ytd))
    if P is None:
        row["ytd_why"] = "no plan"
        return
    pm = [m for m in ytd if m not in P]
    if pm:
        row["ytd_why"] = "no plan figure for %s" % ", ".join(month_label(m) for m in pm[:3])
        return
    row["ytd_plan"] = _r(sum(P[m] for m in ytd))
    row["ytd_var"] = _r(row["ytd_act"] - row["ytd_plan"])
    row["ytd_var_pct"] = _r(row["ytd_var"] / abs(row["ytd_plan"])) if row["ytd_plan"] else None


def _derived(rows, rev, cost, lines, compare, month, prev, ytd):
    """Gross profit (revenue - cost, a flow) and gross margin ((revenue - cost) / revenue, a rate recomputed from the
    sums, never averaged), placed after cost; the workbook writes both as formulas of the revenue and cost rows."""
    A_r, A_c = lines[rev["key"]]["src"]["actual"]["vals"], lines[cost["key"]]["src"]["actual"]["vals"]
    pr, pc = lines[rev["key"]]["src"].get(compare), lines[cost["key"]]["src"].get(compare)
    P_r, P_c = (pr["vals"] if pr else {}), (pc["vals"] if pc else {})
    gpA = {m: A_r[m] - A_c[m] for m in A_r if m in A_c}
    gpP = {m: P_r[m] - P_c[m] for m in P_r if m in P_c}
    gmA = {m: gpA[m] / A_r[m] for m in gpA if A_r[m]}
    gmP = {m: gpP[m] / P_r[m] for m in gpP if P_r[m]}
    src = {"input": rev["actual"]["input"], "column": "%s - %s" % (rev["actual"]["column"], cost["actual"]["column"]),
           "how": "revenue minus cost", "where": None}
    gp = {"key": "gross_profit", "label": "Gross profit", "kind": "flow", "kind_by": "engine", "points": False, "scale": 1.0,
          "cur": _r(gpA.get(month)), "prev": _r(gpA.get(prev)), "plan": _r(gpP.get(month)) if gpP else None,
          "actual": src, "planned": dict(src) if gpP else None, "gaps": [],
          "series": {m: _r(gpA[m]) for m in sorted(gpA) if _shift(month, -12) <= m <= month},
          "derived": {"op": "minus", "of": [rev["key"], cost["key"]]}}
    _changes(gp)
    _ytd(gp, "flow", gpA, gpP or None, ytd)
    src_m = dict(src, column="(%s - %s) / %s" % (rev["actual"]["column"], cost["actual"]["column"], rev["actual"]["column"]),
                 how="gross profit divided by revenue, from the month's sums (never an average of rates)")
    gm = {"key": "gross_margin", "label": "Gross margin %", "kind": "rate", "kind_by": "engine", "points": True, "scale": 100.0,
          "cur": _r(gmA.get(month), 12), "prev": _r(gmA.get(prev), 12), "plan": _r(gmP.get(month), 12) if gmP else None,
          "actual": src_m, "planned": dict(src_m) if gmP else None, "gaps": [],
          "series": {m: _r(gmA[m]) for m in sorted(gmA) if _shift(month, -12) <= m <= month},
          "derived": {"op": "ratio", "of": ["gross_profit", rev["key"]]}}
    _changes(gm)
    gm.update(ytd_act=None, ytd_plan=None, ytd_var=None, ytd_var_pct=None, ytd_why="")
    if gp["ytd_act"] is not None and rev.get("ytd_act"):
        gm["ytd_act"] = _r(gp["ytd_act"] / rev["ytd_act"], 12)
        if gp.get("ytd_plan") is not None and rev.get("ytd_plan"):
            gm["ytd_plan"] = _r(gp["ytd_plan"] / rev["ytd_plan"], 12)
            gm["ytd_var"] = _r((gm["ytd_act"] - gm["ytd_plan"]) * 100.0)
            gm["ytd_var_pct"] = _r((gm["ytd_act"] - gm["ytd_plan"]) / abs(gm["ytd_plan"])) if gm["ytd_plan"] else None
    else:
        gm["ytd_why"] = gp["ytd_why"] or "no year to date revenue"
    i = rows.index(cost) + 1
    return rows[:i] + [gp, gm] + rows[i:]


# ----------------------------------------------------------------------------- where a flagged move sits
def _where(row, lines, read, month, prev, compare) -> Optional[Dict[str, Any]]:
    """The engine's own decomposition, when it attributes the move: the month's change by a grouping column (nl_analyst
    op_change, as Ask your data runs it), else the variance by a grouping column both the actual and the plan files
    carry. A member is named when it carries at least half the move in its direction. Else None (driver to be
    confirmed, and nothing else said)."""
    if row.get("derived") or row["kind"] not in ("flow", "stock"):
        return None
    L = lines.get(row["key"])
    a = L and L["src"].get("actual")
    if not a:
        return None
    tries = []
    for why in row.get("watch_why") or []:
        tries.append("var" if why.startswith("the variance") else "mom")
    for t in ("mom", "var"):
        if t not in tries:
            tries.append(t)
    for t in tries:
        best = None
        if t == "mom" and row.get("mom"):
            best = _where_change(row, a, month)
        elif t == "var" and row.get("var") and L["src"].get(compare):
            best = _where_variance(row, a, L["src"][compare], month)
        if best is not None:
            return best
    return None


def _fit(share: float) -> float:
    """How well one member explains a move: 1 when it carries all of it, less when it carries part, or far more than
    all of it (the other members pulling the other way)."""
    s = abs(share)
    return min(s, 1.0 / s) if s else 0.0


def _where_change(row, a, month) -> Optional[Dict[str, Any]]:
    import nl_analyst as na
    r = a["read"]
    fr = r.get("analyst") or {}
    df, kinds, hint = fr.get("df"), fr.get("kinds"), fr.get("hint") or {}
    if df is None or a["column"] not in getattr(df, "columns", []):
        df, kinds, hint = r["frame"], r["kinds"], {"date": r["date"]}
    where = None
    if a.get("where"):
        where = {c: v[0] for c, v in a["where"].items() if len(v) == 1}
        if any(len(v) != 1 for v in a["where"].values()):
            return None
    best = None
    for dim in r["dims"]:
        if dim not in getattr(df, "columns", []) or (where and dim in where):
            continue
        args = {"measure": a["column"], "by": dim, "grain": "month", "period": month}
        if where:
            args["where"] = where
        try:
            res = na.run_steps(df, kinds, [{"op": "change", "args": args, "why": "monthly pack: where the change sits"}], hint)
        except Exception:  # noqa: BLE001 - no attribution, the cause stays to be confirmed
            continue
        st = (res.get("steps") or [{}])[0]
        if not st.get("ok"):
            continue
        n = st.get("numbers") or {}
        tot, top, td = (n.get("delta") or {}).get("value"), (n.get("top") or {}).get("text"), (n.get("top_delta") or {}).get("value")
        if tot is None or td is None or not top or tot == 0:
            continue
        # the engine's totals are the same month pair as the pack's: else the step read other windows
        if (n.get("cur_label") or {}).get("text") != month:
            continue
        if td * tot > 0 and abs(td) >= ATTRIB_SHARE * abs(tot):
            cand = {"of": "change", "by": dim, "member": str(top), "amount": _r(td), "total": _r(tot),
                    "share": _r(td / tot), "method": "the engine's change-by-group step (nl_analyst change)"}
            if best is None or _fit(td / tot) > _fit(best["share"]):
                best = cand
    return best


def _where_variance(row, a, p, month) -> Optional[Dict[str, Any]]:
    ra, rp = a["read"], p["read"]
    shared = [d for d in ra["dims"] if d in rp["dims"]]
    best = None
    for dim in shared:
        fa = a["frame"].assign(_d=ra["frame"].loc[a["frame"].index, dim].astype(str).str.strip())
        fp = p["frame"].assign(_d=rp["frame"].loc[p["frame"].index, dim].astype(str).str.strip())
        fa, fp = fa[fa["month"] == month], fp[fp["month"] == month]
        if fa.empty or fp.empty:
            continue
        ga, gp = fa.groupby("_d")["value"].sum(), fp.groupby("_d")["value"].sum()
        if set(ga.index) != set(gp.index):
            continue
        var = (ga - gp)
        tot = float(var.sum())
        if tot == 0:
            continue
        top = var.idxmin() if tot < 0 else var.idxmax()
        td = float(var[top])
        if td * tot > 0 and abs(td) >= ATTRIB_SHARE * abs(tot):
            cand = {"of": "variance", "by": dim, "member": str(top), "amount": _r(td), "total": _r(tot),
                    "share": _r(td / tot), "method": "the variance of each %s (actual minus plan, the month's sums)" % dim}
            if best is None or _fit(td / tot) > _fit(best["share"]):
                best = cand
    return best


# ----------------------------------------------------------------------------- notes, checklist
def _where_words(w: Optional[Dict[str, Any]]) -> str:
    if not w:
        return ""
    return " where %s" % " and ".join("%s is %s" % (c, " or ".join(v)) for c, v in w.items())


def _notes(out, rows, read, lines, compare, thr, basis, fy) -> None:
    N = out["notes"]
    for r in rows:
        a, p = r["actual"], r.get("planned")
        if r.get("derived"):
            d = "%s: %s (actual)%s." % (r["label"], a["how"], ("; plan: the same from the %s figures" % compare) if p else "")
        else:
            d = "%s: %s of %s in %s%s" % (r["label"], a["how"], a["column"], a["input"], _where_words(a["where"]))
            d += ("; plan: %s of %s in %s%s." % (p["how"], p["column"], p["input"], _where_words(p["where"]))) if p else "; no plan."
        kind_word = {"flow": "a flow (added up over months)", "stock": "a count at a date (never added up over months)",
                     "level": "a level (never added up over months)", "rate": "a rate (its changes in percentage points)"}[r["kind"]]
        N["definitions"].append({"line": r["label"], "text": d, "kind": kind_word,
                                 "kind_by": "you" if r.get("kind_by") == "visitor" else "the engine"})
    N["sources"] = [{"input": r["name"], "role": r["role"], "rows_kept": int((r["summary"] or {}).get("rows_kept") or 0),
                     "rows_set_aside": int((r["summary"] or {}).get("rows_set_aside") or 0),
                     "months": (r["summary"] or {}).get("months")} for r in read]
    for s in N["sources"]:
        if s["rows_set_aside"]:
            N["wrong"].append("%s: the engine set aside %d %s (step 2 says why); they are not in any figure." % (
                s["input"], s["rows_set_aside"], "row" if s["rows_set_aside"] == 1 else "rows"))
    for x in out["refused"]:
        N["wrong"].append("%s was not read for the pack: %s." % (x["input"], x["why"]))
    A = N["assumptions"]
    A.append("Reporting month: %s (%s)." % (out["month_label"], out["month_why"]))
    A.append("Plan: the %s figures%s." % (compare, "" if compare == "plan" else " (the reforecast), not the budget"))
    A.append("Watch list: a line is flagged when %s is more than %s (one threshold for every line, in the Watch list "
             "sheet's threshold cell)." % ("its change on last month or its variance to plan, in percent," if basis == "percent"
                                          else "its change on last month or its variance to plan, in the line's own unit,",
                                          ("%g%%" % round(thr * 100, 4)) if basis == "percent" else "%g" % thr))
    A.append("Percent changes are divided by the size of the base (|last month| or |plan|), so a negative base keeps the "
             "sign of the move. A rate's change is in percentage points; its percent column is the relative change, "
             "used only for the flag.")
    A.append("Year to date: %s, a calendar month count from the fiscal year's first month (%s); flows only." % (
        out["ytd_label"], MONTHS[fy - 1]))
    A.append("Gaps are left as gaps: a month with no value is empty, never filled or smoothed.")
    A.append("Where a move sits is said only when the engine's own decomposition finds it in the data; the cause is "
             "always \"%s\"." % CAUSE)


def _checklist(read, rows, month) -> List[Dict[str, Any]]:
    nxt = _shift(month, 1)
    order = {"actuals": 0, "actuals_vs_plan": 1, "plan": 2, "kpi": 3}
    out = []
    for r in sorted(read, key=lambda r: order.get(r["role"], 9)):
        m = r["mapping"]
        used = sorted({v["column"] for v in m["values"]})
        out.append({"file": r["name"], "pattern": m["pattern"], "role": r["role"],
                    "date": m["date"], "scenario": (m["scenario"] or {}).get("column"),
                    "scenario_members": [x["value"] for x in (m["scenario"] or {}).get("members") or []],
                    "line_column": m["line_column"], "measures": used, "groups": m["dims"],
                    "next": "the same export with %s's rows (%s) added" % (month_label(nxt), nxt)})
    return out
