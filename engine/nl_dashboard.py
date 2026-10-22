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

VERSION = "2026-10-19.1"
SMALL = 1                     # no figure is hidden for its row count (owner, 19 Oct 2026, AI Analyst v2 phase A): each figure carries its rows
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
AGGS = ("sum", "average", "count", "latest", "change", "peak", "low")
# the tiles that read a measure period by period at the data's own step (period_values): the latest period's value, its change
# from the period before (or from a year earlier: vs "year"), and the highest and lowest period with when
SERIES_AGGS = ("latest", "change", "peak", "low")
# the menu charts the dashboard recomputes under its filters, and what it draws for each
DRAWN = {"line_chart": "line", "area_chart": "area", "bar_chart": "bar", "grouped_bar": "grouped",
         "small_multiples": "multiples"}
# charts only the dashboard computes (not on nl_viz's menu): a measure's change from one period to the next, and its average
# (a level) or total (a flow) by decade or by year, in time order
OWN_CHARTS = {"change_chart": "change", "period_chart": "periods"}
GRAIN_WORD = {"month": "month", "quarter": "quarter", "year": "year"}
_LY = {"month": "monthly", "quarter": "quarterly", "year": "yearly"}
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


def native_grain(months: List[str]) -> str:
    """The step the data are recorded at, from its months ("YYYY-MM", sorted, distinct): "year" when every row falls in the same
    month of its year (FRED's annual series are dated 1 January), "quarter" when every row falls in the same month of its
    quarter, else "month". A chart never shows a finer step than this: a yearly series by month is eleven gaps a year."""
    if len(months) < 2:
        return "month"
    idx = [int(m[:4]) * 12 + int(m[5:7]) - 1 for m in months]
    steps = [b - a for a, b in zip(idx, idx[1:])]
    if all(s % 12 == 0 for s in steps) and len({m[5:7] for m in months}) == 1:
        return "year"
    if all(s % 3 == 0 for s in steps) and len({(int(m[5:7]) - 1) % 3 for m in months}) == 1:
        return "quarter"
    return "month"


OBS_UNIQUE = 0.98             # the share of rows that must be alone on their date (and category) for a table of published figures
OBS_LEVELS_MAX = 1000         # a category with more values does not count towards a row's key (a free-text column would make every row unique);
                              # a panel's entity (200 countries, 400 metros) does


def observation_table(c: Any) -> bool:
    """True when each row is already a published figure, not a record about someone: one row per date (per date and category in a
    long table: country x year) and no column of the file flagged as personal. The small-cell rule (no figure on fewer than SMALL
    rows) protects the people behind a count; a yearly series has one row a year and no person behind it, and the rule hid every
    value of it (a visitor's FRED federal-debt-to-GDP file showed "<5" for all 87 years, 19 Oct 2026). Any personal column, kept,
    coded or withheld, or rows that share a date, keep the rule."""
    import pandas as pd
    if c.date is None or c.flagged or c.frame is None or len(c.frame) < 2:
        return False
    if not regular_dates(c.frame[c.date]):
        return False
    keys = {"_d": c.frame[c.date].astype(str).values}
    for j, d in enumerate(c.dims):
        if c.role_ok(d, "category") == "" and len(c.levels_all(d)) <= OBS_LEVELS_MAX:
            keys["_k%d" % j] = c.texts(d).astype(str).values
    sizes = pd.DataFrame(keys).groupby(list(keys), sort=False).size()
    return float(sizes[sizes == 1].sum()) / float(len(c.frame)) >= OBS_UNIQUE


REGULAR_SHARE = 0.80          # the share of steps between dates that must be the series' own step


def regular_dates(col: Any) -> bool:
    """True when the dates are a published series' calendar: no time of day (a timestamp is an event: a visit, a call, a sale), and
    the steps between the distinct dates are one regular step (a day, allowing weekends and holidays; a week; a month; a quarter; a
    year) for REGULAR_SHARE of them. An event log is irregular, so it keeps the small-cell rule even when no two rows share a date
    (review of the 19 Oct change: a log of rare events, one a day, read as a table of published figures would print single events)."""
    import pandas as pd
    t = pd.to_datetime(pd.Series(col).astype(str), errors="coerce")
    t = t.dropna()
    if len(t) < 3:
        return False
    if ((t.dt.hour != 0) | (t.dt.minute != 0) | (t.dt.second != 0)).any():
        return False
    days = sorted(set(t.dt.normalize()))
    if len(days) < 3:
        return False
    steps = [(b - a).days for a, b in zip(days, days[1:])]

    def kind(d: int) -> str:
        return ("day" if d <= 4 else "week" if d == 7 else "month" if 28 <= d <= 31 else "quarter" if 89 <= d <= 92
                else "year" if 365 <= d <= 366 else "other")
    counts: Dict[str, int] = {}
    for d in steps:
        k = kind(d)
        counts[k] = counts.get(k, 0) + 1
    best = max((v for k, v in counts.items() if k != "other"), default=0)
    return best >= REGULAR_SHARE * len(steps)


def _period(ym: str, grain: str) -> str:
    """A month's period at a grain: "2025-01", "2025-Q1" or "2025" (57-dashboard.js periodOf)."""
    if grain == "quarter":
        return "%s-Q%d" % (ym[:4], (int(ym[5:7]) - 1) // 3 + 1)
    return ym[:4] if grain == "year" else ym


def period_word(p: Optional[str]) -> str:
    """A period as the page prints it: "Jan 2025", "Q1 2025", "2025" (57-dashboard.js periodWord)."""
    p = str(p or "")
    if re.fullmatch(r"\d{4}-\d{2}", p):
        return _month_word(p)
    if re.fullmatch(r"\d{4}-Q\d", p):
        return "%s %s" % (p[5:], p[:4])
    return p


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
        # one unit word per measure (measure_units), for every figure the dashboard writes: its tiles and charts, the
        # engine's own chart records it carries, the notes and both exports
        self.units = measure_units(self.ctx, ctx_in.get("reading"))
        plan_unit = self.ctx.unit
        self.ctx.unit = lambda land: (self.units[land] if land and land in self.units else plan_unit(land))
        # a code-like column's name in words (the plan's label, else the FRED list's: nl_browser._column_names), for every
        # word the dashboard prints; the cube and the filters keep the file's header as their key
        self.plain = {str(k): str(v) for k, v in dict(ctx_in.get("plain_names") or {}).items() if k and v}
        for land in self.ctx.columns:                     # a FRED percentage the plan gives no unit: "%" (nl_browser._plain_units)
            pu = dict(ctx_in.get("plain_units") or {})
            u = pu.get(str(land)) or pu.get(self.ctx.header(land))
            if u and land not in self.units and not plan_unit(land):
                self.units[land] = u
        self.grain = native_grain(sorted(str(m) for m in self.ctx.month.dropna().unique())) \
            if self.ctx.date is not None and self.ctx.month is not None else "month"
        self.obs = observation_table(self.ctx)
        self.small = SMALL

    def name(self, land: Optional[str]) -> str:
        """The column's name as the dashboard prints it."""
        if not land:
            return ""
        h = self.ctx.header(land)
        return self.plain.get(h) or self.plain.get(str(land)) or h

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
CURRENCY_SIGNS = "$€£¥"
_SIGN_RX = re.compile(r"[$€£¥]")
_ISO_WORD_RX = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")


def measure_units(c: Any, reading: Any) -> Dict[str, str]:
    """{landed column: unit word} for the number columns whose own cells name their currency: one sign ($ € £ ¥) or
    one ISO 4217 code ("CAD 1,200"), the same in every cell that has one. The file's own word wins over the plan's
    (the cleaner strips the sign from the kept rows; the engine's reading still holds the landed text). A column
    whose cells name two currencies, or none, keeps the plan's unit; a percentage keeps "%"."""
    out: Dict[str, str] = {}
    texts = getattr(reading, "texts", None)
    if texts is None or not hasattr(texts, "columns"):
        return out
    for land in c.columns:
        if land == c.date or land not in texts.columns or c.ptype(land) == "percentage" or not c.numeric(land):
            continue
        seen: Dict[str, int] = {}
        for t in texts[land].astype(str).head(20000):
            if not t or not any(ch.isdigit() for ch in t):
                continue
            for sg in set(_SIGN_RX.findall(t)):
                seen[sg] = seen.get(sg, 0) + 1
            for iso in set(m.group(1) for m in _ISO_WORD_RX.finditer(t) if m.group(1) in NB._ISO_CURRENCIES):
                seen[iso] = seen.get(iso, 0) + 1
        if len(seen) == 1:
            out[land] = next(iter(seen))
    return out


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
    # "dog" or "Dogs" for the file's "Dog" (20 Oct 2026, live: Austin's "Dog outcomes" tile was refused): the case, then a plural s
    low = {}
    for h in have:
        low.setdefault(h.lower(), h)
    vals = []
    for v in (raw.get("values") or [])[:WHERE_VALUES_MAX] if isinstance(raw.get("values"), list) else []:
        v = str(v).strip()
        if v not in have:
            v = low.get(v.lower()) or (low.get(v.lower()[:-1]) if v.lower().endswith("s") else None) or v
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
    if a in SERIES_AGGS and not has_date:
        # a latest or a peak with no dates is no figure at all: the tile goes (20 Oct 2026, live: OWID's "Latest global emissions"
        # showed every row's co2 added up, the world and its regions counted twice, under its own label)
        R.refuse(element, R_NO_DATE)
        return ""
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
        # a count named by an identifier is a count of the rows, one line a row, when every row has one (UCI live, 21 Oct 2026:
        # "Invoice lines", measure Invoice, agg count, was refused as "Invoice is not a measure"); with blanks in it the count of the
        # rows would overstate it ("Customer lines" read 191,994 with thousands of lines that have no customer), so the tile goes
        if not rows and str(k.get("agg") or "") == "count" and c.landed(m) in c.columns:
            # a count tile counts rows, whatever column it names; it stands only when that column is filled on every row, else the
            # count of rows would overstate it (live: "Customer lines" read 191,994, thousands of lines having no customer)
            blank = int((c.texts(c.landed(m)) == "").sum())
            if blank:
                R.refuse(el, "it counts %s, and %s rows have none, so the count of rows would overstate it" % (
                    c.header(c.landed(m)), format(blank, ",")))
                continue
            rows = True
        land = None if rows else R.column(m, el, "measure")
        if not rows and land is None:
            continue
        agg = str(k.get("agg")) if land is None and has_date and str(k.get("agg") or "") in ("peak", "low", "change") \
            else "count" if land is None else _agg_for(R, land, k.get("agg"), el, has_date)
        if not agg:
            continue
        if land is not None and str(k.get("agg") or "") == "count":
            agg = "count"
        share = land is not None and not c.numeric(land) and c.flag_share(land) is not None
        if share:
            agg = "average"                          # a true/false column: the share of rows that are true, in %
        wh = _where(R, k.get("where"), el) if k.get("where") is not None else None
        if k.get("where") is not None and wh is None:
            continue
        item = {"id": kid, "measure": land, "agg": agg, "where": wh,
                "label": R.words(k.get("label"), "label", el, ai) or "",
                "unit_word": "%" if share else _unit(R, k.get("unit_word"), el, ai, land)}
        # a tile named for the busiest (highest, peak, record) period that adds up every row means the peak period (20 Oct 2026,
        # live: Chicago's "Busiest month" showed 119,887, all the rows)
        if agg in ("count", "sum") and has_date and re.search(r"(?i)\b(busiest|highest|peak|record|best|top)\s+(month|quarter|year|week|day|period)\b",
                                                             item["label"]):
            agg = item["agg"] = "peak"
            R.refuse(el, "its label names the busiest period, so it reads the highest period, not the whole range")
        if agg == "change":
            item["vs"] = "year" if str(k.get("vs") or "") == "year" and R.grain != "year" else "previous"
        # a file of records: the highest or lowest is a month's (a quarter's, a year's) total, never one row (20 Oct 2026, live: the
        # Fremont Bridge's hourly counts had a tile "Busiest single reading" of 1,187,146 cyclists, a month's total)
        if agg in ("peak", "low") and land is not None and not R.obs and \
                re.search(r"(?i)\b(single|reading|readings|hour|hourly|minute|day|daily|observation)\b", item["label"]):
            R.refuse(el, "its label named a single reading; the tile is the %s %s of a period" % (
                "highest" if agg == "peak" else "lowest", "total" if c.is_flow(land) else "average"))
            item["label"] = "%s %s" % ("Highest" if agg == "peak" else "Lowest", c.header(land))
        # a period's figure of a flow is its total: a label calling it an average says what the number is not (20 Oct 2026, live:
        # football's "Latest month's average home goals" showed 1,334 goals, the month's total)
        if agg in ("latest", "peak", "low") and land is not None and c.is_flow(land) and \
                re.search(r"(?i)\b(average|avg|mean|per)\b", item["label"]):
            R.refuse(el, "its label named an average; the tile is the %s total of %s" % (
                {"latest": "latest period's", "peak": "highest period's", "low": "lowest period's"}[agg], c.header(land)))
            item["label"] = "%s %s" % ({"latest": "Latest total", "peak": "Highest total", "low": "Lowest total"}[agg], c.header(land))
        out["kpis"].append(item)
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
        if name in OWN_CHARTS:
            if not has_date:
                R.refuse(el, R_NO_DATE)
                continue
            args = ch.get("args") if isinstance(ch.get("args"), list) else (ch.get("columns") or [])
            m = args[0] if args else ch.get("measure")
            if m is None:
                has, pm = c.primary_measure()
                land = pm if has else None
            else:
                land = R.column(m, el, "measure")
            if land is None:
                if m is None:
                    R.refuse(el, "the file has no measure to chart")
                continue
            wh = None
            if ch.get("where") is not None:
                wh = _where(R, ch.get("where"), el)
                if wh is None:
                    continue
            out["charts"].append({"id": _ident(ch.get("id"), "c", i, seen, R, el), "chart": name, "draw": OWN_CHARTS[name],
                                  "measure": land, "agg": "sum" if c.is_flow(land) else "average", "by": None, "where": wh,
                                  "title": R.words(ch.get("title"), "title", el, ai) or "",
                                  "why": R.words(ch.get("why"), "why", el, ai) or "",
                                  "unit_word": _unit(R, ch.get("unit_word"), el, ai, land)})
            continue
        if name not in NV.REGISTRY:
            R.refuse(el, R_MENU)
            continue
        args = ch.get("args") if isinstance(ch.get("args"), list) else (ch.get("columns") or [])
        args = [str(a) for a in args if isinstance(a, (str, int, float))][:12]
        if name in ("line_chart", "area_chart", "grouped_bar", "small_multiples") and c.date is not None:
            # the time axis is the file's own date: naming it is "over time", never a measure (20 Oct 2026: NYC 311's and Chicago's
            # "requests per month" passed the date and were refused, so two daily files had no trend at all)
            # and a second column of dates (Austin's monthyear beside its datetime, 21 Oct 2026: "Outcomes by month and year" was
            # refused) is the same time axis, read at the file's own date
            second = {k for k, v in ((R.rep.get("roles") or {}).get("excluded") or {}).items() if "second date" in str(v)}
            args = [a for a in args if c.landed(a) != c.date and (c.landed(a) or a) not in second and not _month_labels(c, c.landed(a))]
        if any(c.is_flagged(a) for a in args):
            R.refuse(el, R_PERSONAL)
            continue
        # a line or area over time split by a category alone counts the rows of each (20 Oct 2026, live: Austin's "outcome mix over
        # time" and NYC's "monthly requests by agency" named only the category and were refused as "not a number column")
        cat_spec = None
        if name in ("line_chart", "area_chart") and c.date is not None and len(args) == 1:
            lc = c.landed(args[0])
            if lc in c.columns and lc != c.date and not c.numeric(lc) and c.role_ok(lc, "category") == "" \
                    and 2 <= len(c.levels_all(lc)) <= 60:
                cat_spec = {"draw": DRAWN[name], "measure": None, "agg": "count", "by": lc}
        if cat_spec is not None:
            lands = [cat_spec["by"]]
        else:
            chart, lands, why = NV.validate_directive(c, {"kind": name, "columns": args}, lim)
            if why:
                R.refuse(el, why)
                continue
        cid = _ident(ch.get("id"), "c", i, seen, R, el)
        item = {"id": cid, "chart": name, "title": R.words(ch.get("title"), "title", el, ai) or "",
                "why": R.words(ch.get("why"), "why", el, ai) or ""}
        if name in DRAWN:
            spec = _drawn_args(R, name, lands, el) if cat_spec is None else cat_spec
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
            # what it counts, for its definition: the record's own measure and how the chart aggregates it
            sm = _static_measure(c, name, lands, rec)
            item.update({"draw": "static", "record": rec, "measure": None, "agg": None, "by": None, "where": None,
                         "unit_word": c.unit(sm) if sm else "", "static": {"chart": name, "lands": list(lands), "measure": sm,
                                                                       "label": _rec_measure_label(rec)}})
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
    # a question may be answered by the AI's own visuals, built after this check (nl_browser._dashboard_visuals prunes the ids of a
    # visual it then refuses; 20 Oct 2026: penguins' questions about its visuals were refused as answering nothing)
    vis_ids = {str(v.get("id")) for v in block.get("visuals") or [] if isinstance(v, dict) and isinstance(v.get("id"), str)}
    for q in raw_q[:QUESTION_MAX + 4]:
        if not isinstance(q, dict) or len(out["questions"]) >= QUESTION_MAX:
            continue
        el = "a question"
        t = R.words(q.get("q"), "q", el, ai)
        if not t:
            continue
        ab = [str(x) for x in (q.get("answered_by") or []) if str(x) in ids or str(x) in vis_ids] \
            if isinstance(q.get("answered_by"), list) else []
        if not ab:
            R.refuse("the question “%s”" % _text(t, 60), R_ANSWERS)
            continue
        out["questions"].append({"q": t, "answered_by": list(dict.fromkeys(ab))[:8]})
    out["layout"] = _layout(block.get("layout"), out)
    return out


def _unit(R: Read, u: Any, element: str, ai: bool, land: Optional[str]) -> str:
    """The unit word of an element. A measure has ONE unit word on the whole dashboard, its tiles, charts, notes and
    exports (review of the AI-designed dashboard's workbook, 17 Oct 2026: tiles in "$" and the charts' definitions "in
    CAD" for the same Amount): the file's own currency sign, else the plan's unit, else the first unit word the design
    gave that measure (measure_units). A different word an element asks for is replaced, and the notes say so."""
    s = " ".join(str(u or "").split())[:TEXT_MAX["unit_word"]]
    ok = bool(s) and bool(UNIT_RX.match(s)) and not (ai and has_figure(s))
    if s and not ok:
        R.refuse(element, "its unit is not a unit word; the plan's unit was used")
    if land is None:
        return s if ok else ""
    have = R.ctx.unit(land)
    if not have and ok:
        R.units[land] = have = s                          # the first word the design gave this measure
    if ok and s != have:
        R.refuse(element, "its unit word \u201c%s\u201d was replaced by \u201c%s\u201d: one unit word for %s on the whole "
                          "dashboard" % (s, have or "none", R.ctx.header(land)))
    return have


_MONTH_LABEL = re.compile(r"^(?:(?:0?[1-9]|1[0-2])[-/](?:19|20)\d{2}|(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])|"
                          r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (?:19|20)\d{2})$", re.I)


def _month_labels(c: Any, land: Optional[str]) -> bool:
    """A column of month labels ("05-2025", "2025-05", "May 2025") beside the file's date: the same time axis, never a category."""
    if not land or land not in c.columns or land == c.date or c.numeric(land):
        return False                                  # a number column (a balance of 1234.56) is never a month label
    t = c.texts(land)
    t = t[t != ""]
    return bool(len(t)) and float(t.head(2000).str.match(_MONTH_LABEL).mean()) >= 0.95


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
    agg = "count" if measure is None else ("sum" if c.is_flow(measure) else "average")
    return {"draw": DRAWN[name], "measure": measure, "agg": agg, "by": by}


def _layout(raw: Any, out: Dict[str, Any]) -> List[List[str]]:
    ids = [k["id"] for k in out["kpis"]] + [x["id"] for x in out["charts"]]
    tiles = {k["id"] for k in out["kpis"]}
    rows: List[List[str]] = []
    used: set = set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, list):
            continue
        r = [str(x) for x in row if str(x) in ids and str(x) not in used]
        r = r[:KPI_MAX] if r and all(x in tiles for x in r) else r[:4]       # a row of tiles holds them all; charts go 4 to a row
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


PERIOD_CHART_MIN = {"year": 20, "quarter": 12, "month": 36}   # periods of history before a by-decade / by-year chart says anything


def series_design(R: Read, pm: str) -> Dict[str, Any]:
    """The engine's own dashboard for a table of published figures (observation_table): where the headline measure stands now,
    how it moved from the period before (and from a year earlier, for a monthly or quarterly series), its highest and lowest
    period, its line, its change period by period and its average (a level) or total (a flow) by decade or by year; up to two
    further measures each on their own line (their units differ); a long table's categories as a split and as filters."""
    c = R.ctx
    g = GRAIN_WORD[R.grain]
    nm = R.name(pm)
    flow = c.is_flow(pm)
    base = "sum" if flow else "average"
    u = c.unit(pm)
    months = sorted(str(m) for m in c.month.dropna().unique())
    periods = len({_period(m, R.grain) for m in months})
    out: Dict[str, Any] = {"audience": "Anyone who follows %s: the engine's own design for a published series, without the AI" % nm,
                           "questions": [], "kpis": [], "charts": [], "filters": [], "layout": []}
    k = out["kpis"]
    k.append({"id": "now", "measure": pm, "agg": "latest", "where": None, "label": "Latest", "unit_word": u})
    k.append({"id": "chg", "measure": pm, "agg": "change", "vs": "previous", "where": None,
              "label": "Change from the %s before" % g, "unit_word": u})
    if R.grain != "year":
        k.append({"id": "yoy", "measure": pm, "agg": "change", "vs": "year", "where": None,
                  "label": "Change from a year earlier", "unit_word": u})
    k.append({"id": "peak", "measure": pm, "agg": "peak", "where": None, "label": "Highest %s" % g, "unit_word": u})
    k.append({"id": "low", "measure": pm, "agg": "low", "where": None, "label": "Lowest %s" % g, "unit_word": u})
    ch = out["charts"]
    ch.append({"id": "trend", "chart": "line_chart", "draw": "line", "measure": pm, "agg": base, "by": None, "where": None,
               "title": "", "why": "Chosen by the engine: the whole series in one line, so its peaks and turning points show",
               "unit_word": u})
    ch.append({"id": "change", "chart": "change_chart", "draw": "change", "measure": pm, "agg": base, "by": None, "where": None,
               "title": "", "why": "Chosen by the engine: how much it moved each %s, up or down" % g, "unit_word": u})
    if periods >= PERIOD_CHART_MIN[R.grain]:
        ch.append({"id": "eras", "chart": "period_chart", "draw": "periods", "measure": pm, "agg": base, "by": None, "where": None,
                   "title": "", "why": "Chosen by the engine: the %s %s era by era, in time order" % (
                       "total of" if flow else "average of", nm), "unit_word": u})
    for m in [x for x in c.measures if x != pm][:2]:
        ch.append({"id": "m_%s" % _slug(m)[:20], "chart": "line_chart", "draw": "line", "measure": m,
                   "agg": "sum" if c.is_flow(m) else "average", "by": None, "where": None, "title": "",
                   "why": "Chosen by the engine: a further measure of the file, on its own scale", "unit_word": c.unit(m)})
    dims = [d for d in c.dims if c.role_ok(d, "category") == "" and 2 <= len(c.levels_all(d)) <= 60
            and not NB._looks_like_names(c.levels_all(d), c.header(d))]
    for d in dims[:1]:
        ch.append({"id": "by_%s" % _slug(d)[:18], "chart": "bar_chart", "draw": "bar", "measure": pm, "agg": base, "by": d,
                   "where": None, "title": "", "why": NV.ENGINE_WHY + NV.REGISTRY["bar_chart"]["what"], "unit_word": u})
    out["filters"].append({"kind": "date", "dim": None})
    for d in dims[:FILTER_CATS]:
        out["filters"].append({"kind": "category", "dim": d})
    q = out["questions"]
    q.append({"q": "Where does %s stand now, and which way is it moving?" % nm,
              "answered_by": [x["id"] for x in k if x["agg"] in ("latest", "change")] + ["trend"]})
    q.append({"q": "When was it highest, and when lowest?", "answered_by": ["peak", "low", "trend"]})
    q.append({"q": "How much did it move %s by %s?" % (g, g), "answered_by": ["change"]})
    if any(x["id"] == "eras" for x in ch):
        q.append({"q": "How does one era compare with another?", "answered_by": ["eras"]})
    lay = [[x["id"] for x in k]]
    lay.append(["trend"])
    rest = [x["id"] for x in ch if x["id"] != "trend"]
    for j in range(0, len(rest), 2):
        lay.append(rest[j:j + 2])
    out["layout"] = _layout(lay, out)
    return out


_MONEY_RX = re.compile(r"(?i)salar|wage|\bpay|revenue|sales|amount|price|cost|income|spend|earning|budget|fee|usd|cad|eur|gbp|\$")


def _is_money(R: Read, m: Optional[str]) -> bool:
    if not m:
        return False
    c = R.ctx
    u = str(c.unit(m) or "")
    return bool(_MONEY_RX.search(c.header(m)) or _MONEY_RX.search(R.name(m)) or u in ("$", "€", "£", "¥") or re.fullmatch(r"[A-Z]{3}", u))


def lead_measure(R: Read) -> Optional[str]:
    """The measure the engine's own design leads with when the report's headline is a count of rows: a money column first (its
    header or its unit says so), then a flow, then the first measure (19 Oct 2026: Chicago's payroll led with Typical Hours)."""
    c = R.ctx
    ms = list(c.measures)
    if not ms:
        return None
    # a money total (revenue, sales) leads before a money level (a price): what the business took, not what one unit cost (21 Oct 2026:
    # a retail file's tiles led with the average price, the derived Revenue third)
    money = sorted([m for m in ms if _is_money(R, m)], key=lambda m: not c.is_flow(m))
    if money:
        return money[0]
    flows = [m for m in ms if c.is_flow(m)]
    return (flows or ms)[0]


def _indicator_design(R: Read, out: Dict[str, Any], k_rows: Dict[str, Any], pm: Optional[str],
                      dims: List[str]) -> Optional[Dict[str, Any]]:
    """The engine's design for an indicator table (rep.explore, engine/nl_explore.py: a census profile, each row one of 1,624
    characteristics for one of 14 places): every row measures a different thing, so nothing is added across rows. Each chosen
    characteristic is compared across the places, the total member (Canada) a bar of its own. 20 Oct 2026, live: the plain design added
    C1_COUNT_TOTAL over every characteristic and every place, Canada included, into 6.4 billion. None for any other table."""
    c, ex = R.ctx, R.rep.get("explore")
    if not pm or not isinstance(ex, dict) or ex.get("kind") != "indicator_table":
        return None
    land_of = {c.header(x): x for x in c.columns}
    ent, ind = land_of.get(str(ex.get("entity_column") or "")), land_of.get(str(ex.get("indicator_column") or ""))
    if ent is None or ind is None or c.is_flagged(ent) or c.is_flagged(ind):
        return None
    levels = {}
    for v in c.frame[ind].astype(str):
        levels.setdefault(v.strip(), v)
    out["kpis"] = [k_rows]
    for j, iv in enumerate([x for x in ex.get("indicators") or [] if isinstance(x, dict)][:3]):
        lv = levels.get(str(iv.get("label") or "").strip())
        if lv is None:
            continue
        # one filter a chart: the total member (Canada) stays a bar of its own, beside the places it totals; no bar is added
        wh = {"dim": ind, "values": [lv]}
        out["charts"].append({"id": "ind_%d" % j, "chart": "bar_chart", "draw": "bar", "measure": pm, "agg": "sum", "by": ent,
                              "where": wh, "title": "%s by %s" % (lv.strip(), str(ex.get("entity") or c.header(ent))),
                              "why": NV.ENGINE_WHY + NV.REGISTRY["bar_chart"]["what"], "unit_word": c.unit(pm)})
        out["questions"].append({"q": "Which %s has the highest %s?" % (str(ex.get("entity") or c.header(ent)), _lower_first(lv.strip())),
                                 "answered_by": ["ind_%d" % j]})
    if not out["charts"]:
        return None
    R.refuse("the totals of %s" % c.header(pm), "each row is a different %s, so adding %s across rows mixes unlike things; each "
             "chart shows one %s across the %s" % (str(ex.get("indicator") or "measure"), c.header(pm),
                                                   str(ex.get("indicator") or "measure"), _plural_word(str(ex.get("entity") or "rows"))))
    # a code a place (DGUID beside GEO_NAME) filters nothing the place names do not
    n_ent = len(c.levels_all(ent))
    out["filters"] = [{"kind": "category", "dim": d} for d in dims if d not in (ind, ent) and len(c.levels_all(d)) != n_ent][:FILTER_CATS]
    out["layout"] = _layout(None, out)
    return out


def _lower_first(w: str) -> str:
    """"Average value" -> "average value" mid-sentence; "GDP" and "CO2" stay."""
    return w[:1].lower() + w[1:] if w[1:2].islower() else w


def _plural_word(w: str) -> str:
    return w if w.endswith("s") else (w[:-1] + "ies" if w.endswith("y") and w[-2:-1] not in "aeiou" else w + "s")


def default_design(R: Read) -> Dict[str, Any]:
    """The engine's own dashboard when no AI designed one: what each row counts and the headline measure, its line by
    month, and its split by the categories the engine reads, with the date range and up to 3 category filters."""
    c, rep = R.ctx, R.rep
    plural, _sing = row_noun(rep)
    labels = (rep.get("summary") or {}).get("labels") or {}
    has, pm = c.primary_measure()
    pm = (pm if has else None) or lead_measure(R)
    if R.obs and pm and c.date is not None:
        return series_design(R, pm)
    fid = str((rep.get("primary_metric") or {}).get("finding_id") or "")
    lead = lead_measure(R)
    if lead and pm != lead and _is_money(R, lead) and (not _is_money(R, pm) or (c.is_flow(lead) and not c.is_flow(pm))):
        pm, fid = lead, ""                     # money leads; the headline's split and labels belong to its own measure
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
    one = _indicator_design(R, out, k_rows, pm, dims)
    if one is not None:
        return one
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


AREA_OTHER_MAX = 0.40          # a stacked area whose "other" would hold more of the total is drawn as bars instead
AREA_PARTS = 5                 # the parts a stacked area names (57-dashboard.js), the rest are "other"
PMAX = 30                      # periods a time chart shows (57-dashboard.js PMAX): a longer span by quarter or year


def area_other_share(R: Read, x: Dict[str, Any]) -> Optional[float]:
    """The share of the total a stacked area would draw as "other" over the whole span, by the page's rule
    (57-dashboard.js NLD.record): its 5 largest groups are named, a group with a period on 1 to 4 filled values goes
    to "other", and a value that shares a word with a flagged column is "other" too. None when it cannot be read."""
    import pandas as pd
    c = R.ctx
    m, by = x.get("measure"), x.get("by")
    if not m or not by or c.month is None:
        return None
    v = c.nums(m)
    keep = v.notna() & c.month.notna()
    wh = x.get("where")
    if wh:
        hit = c.texts(wh["dim"]).isin(wh["values"])
        keep &= ~hit if wh.get("exclude") else hit
    if not keep.any():
        return None
    mon = c.month[keep]
    span = NB._month_range(min(mon), max(mon))
    grain = "month" if len(span) <= PMAX else ("quarter" if -(-len(span) // 3) <= PMAX else "year")
    per = mon.map(lambda ym: ym if grain == "month" else (ym[:4] + "-Q%d" % ((int(ym[5:7]) - 1) // 3 + 1) if grain == "quarter" else ym[:4]))
    lv = c.texts(by)[keep]
    vals = v[keep]
    tot = vals.abs().groupby(lv).sum().sort_values(ascending=False)
    total = float(tot.sum())
    if total <= 0:
        return None
    named = 0.0
    shown = 0
    for level, t in tot.items():
        if shown >= AREA_PARTS:
            break
        if level == "" or not NV._label_ok(c, str(level)):
            continue
        k = pd.Series(1, index=per[lv == level].index).groupby(per[lv == level]).sum()
        shown += 1
        if ((k > 0) & (k < R.small)).any():
            continue
        named += float(t)
    return 1.0 - named / total


def _area_check(R: Read, design: Dict[str, Any]) -> None:
    """A stacked area whose "other" would hold more than AREA_OTHER_MAX of the total says little: it is drawn as a
    grouped bar (the latest 12 months against the 12 before) when the file spans 24 months, else a bar chart, and the
    notes say why."""
    c = R.ctx
    n_months = len(set(c.month.dropna())) if c.month is not None else 0
    for x in design["charts"]:
        if x.get("draw") != "area":
            continue
        sh = area_other_share(R, x)
        if sh is None or sh <= AREA_OTHER_MAX:
            continue
        to = "grouped_bar" if n_months >= 24 else "bar_chart"
        R.refuse("the stacked area “%s”" % (x.get("title") or "%s by %s" % (c.header(x["measure"]), c.header(x["by"]))),
                 "“other” would hold %d%% of its total (groups with a period on fewer than 5 rows count there), so it is "
                 "drawn as a %s" % (round(sh * 100), "grouped bar of the latest 12 months against the 12 before" if to == "grouped_bar" else "bar chart"))
        x.update({"chart": to, "draw": DRAWN[to], "title": ""})   # the engine writes the bars' own title


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
            # the values a tile or chart filters on keep their own level first, however few rows they hold (20 Oct 2026: a
            # census profile's characteristic, 14 rows of 22,736, was folded into "other" and its chart refused)
            asked = [v for x in design["kpis"] + design["charts"] if x.get("where") and x["where"].get("dim") == d
                     for v in x["where"].get("values") or []]
            by_rows = c.levels(d)
            first = [(lv, k) for lv, k in by_rows if lv in asked]
            by_rows = first + [(lv, k) for lv, k in by_rows if lv not in asked]
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
        fs = c.flag_share(m) if not c.numeric(m) else None
        v = fs if fs is not None else c.nums(m)
        # a measure shown in "%" is on the percent scale, read as the engine reads a percentage everywhere (_pct_reading: bare
        # values 95% of them between 0 and 1 are fractions). 20 Oct 2026, live: college majors' unemployment rate 0.0697 was tiled
        # "0.07%" and the share of women 0.52 "0.52%"
        if fs is None and (c.unit(m) or R.units.get(m) or "") == "%":
            t = c.texts(m)
            v = NB._pct_reading(v, t, (t != "").values)["percent"].reindex(v.index)
        g["s%d" % j] = v.fillna(0.0).values
        g["c%d" % j] = v.notna().astype(int).values
        mrec.append({"key": "m%d" % j, "column": c.header(m), "land": m, "flow": bool(c.is_flow(m)) and fs is None,
                     "unit": "%" if fs is not None else c.unit(m)})
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
            "rows": int(n), "no_date_rows": int((mi < 0).sum()) if n else 0,
            # the data's own step (native_grain): no chart or tile reads a finer one; and the fewest rows a figure may rest on
            "grain": R.grain, "small": R.small, "series": bool(R.obs)}
    # the last day the data reach, and whether its month is whole (20 Oct 2026, phase D: a tile's variance never compares a month
    # still filling, data to the 15th, with a whole one): whole when the last date is within 3 days of the month's end, or the
    # dates are month starts (a monthly series dated on the 1st)
    cube["last_day"], cube["last_month_full"] = None, True
    if c.date is not None and months:
        try:
            t = pd.to_datetime(frame[c.date], errors="coerce").dropna()
            if len(t):
                last = t.max()
                cube["last_day"] = last.strftime("%Y-%m-%d")
                firsts = bool((t.dt.day == 1).mean() >= 0.95)
                cube["last_month_full"] = bool(firsts or last.day >= last.days_in_month - 3)
        except Exception:  # noqa: BLE001 - unknown: treated as whole, as before
            pass
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
    small = int(cube.get("small") or SMALL)
    cells = [x for x in cube["cells"] if _cell_ok(cube, x, filt, kpi.get("where"))]
    month = None
    # a rows tile's highest or lowest period reads the rows period by period (57-dashboard.js NLD.kpi does the same)
    if kpi["agg"] in SERIES_AGGS and (kpi.get("mkey") is not None or kpi["agg"] in ("peak", "low", "change")):
        return _series_kpi(cube, kpi, cells)
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
    sup = n < small
    return {"value": None if sup or v is None else _num(v), "n": int(n), "suppressed": bool(0 < n < small),
            "month": month}


def period_values(cube: Dict[str, Any], cells: List[List[Any]], j: Optional[int], flow: bool,
                  grain: Optional[str] = None) -> List[Tuple[str, Optional[float], int]]:
    """[(period, value or None, rows)] in time order at the data's own step: a flow's total, a level's average, or the rows
    (j None). A period on fewer rows than the cube's small-cell floor has no value (57-dashboard.js periodValues)."""
    nd = len(cube["dims"])
    grain = grain or cube.get("grain") or "month"
    small = int(cube.get("small") or SMALL)
    by: Dict[str, List[Any]] = {}
    for x in cells:
        if x[0] < 0:
            continue
        p = _period(cube["months"][x[0]], grain)
        acc = by.setdefault(p, [[], 0, 0])
        if j is None:
            acc[1] += x[1 + nd]
        else:
            acc[0].append(x[2 + nd + 2 * j])
            acc[1] += x[3 + nd + 2 * j]
        acc[2] += x[1 + nd]
    out: List[Tuple[str, Optional[float], int]] = []
    for p in sorted(by):
        parts, k, rows = by[p]
        if k < small:
            out.append((p, None, k))
        elif j is None:
            out.append((p, float(k), k))
        else:
            s = math.fsum(parts)
            out.append((p, s if flow else s / k, k))
    return out


PMAX = 30                      # periods a time chart shows (57-dashboard.js PMAX): a longer span by quarter or year
_GRANK = {"month": 0, "quarter": 1, "year": 2}


def tile_grain(cube: Dict[str, Any], cells: List[List[Any]]) -> str:
    """The step a series tile reads: a table of published figures, its own (one figure a period); a file of records, the step its
    charts show over these cells (57-dashboard.js grainFor: up to PMAX months by month, then by quarter, then by year), never finer
    than its own (19 Oct 2026: Amazon reviews' "vs the year before" compared September 2023's 3 reviews with August's)."""
    native = cube.get("grain") or "month"
    # a table of published figures is told by the engine's reading (cube.series: observation_table), never by the row floor, which
    # is 1 for every file since phase A (20 Oct 2026: that made every records file's tiles read month by month again)
    if cube.get("series"):
        return native
    ms = [cube["months"][x[0]] for x in cells if x[0] >= 0]
    if not ms:
        return native
    a, b = min(ms), max(ms)
    n = (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1
    span = "month" if n <= PMAX else "quarter" if -(-n // 3) <= PMAX else "year"
    return span if _GRANK[span] > _GRANK[native] else native


def _year_back(p: str, grain: str) -> str:
    """The same period a year earlier."""
    if grain == "quarter":
        return "%04d-%s" % (int(p[:4]) - 1, p[5:])
    if grain == "month":
        return "%04d-%s" % (int(p[:4]) - 1, p[5:])
    return "%04d" % (int(p[:4]) - 1)


def _partial_last(cube: Dict[str, Any], pv: List[Any], grain: str) -> bool:
    """The last period of a records file read by month is the data's last month and that month is not whole (cube.last_month_full)."""
    return bool(grain == "month" and not cube.get("series") and cube.get("last_month_full") is False and pv
                and cube.get("months") and pv[-1][0] == cube["months"][-1])


def _series_kpi(cube: Dict[str, Any], kpi: Dict[str, Any], cells: List[List[Any]]) -> Dict[str, Any]:
    """A tile that reads its measure period by period (SERIES_AGGS): {value, n, suppressed, month, period, base, base_period, pct}.
    latest: the last period with a value; change: that value less the period before's (vs "year": the same period a year earlier),
    with pct its relative change; peak / low: the highest / lowest period in the range (the latest one on a tie)."""
    keys = [m["key"] for m in cube["measures"]]
    j = keys.index(kpi["mkey"]) if kpi.get("mkey") is not None else None      # None: the rows, counted
    flow = bool(cube["measures"][j]["flow"]) if j is not None else True
    grain = tile_grain(cube, cells)
    pv = period_values(cube, cells, j, flow, grain)
    if _partial_last(cube, pv, grain) and (kpi["agg"] in ("change", "peak", "low") or flow):
        pv = pv[:-1]                    # a month still filling (data to the 9th) is neither compared nor a low (20 Oct 2026)
    have = [(p, v, k) for p, v, k in pv if v is not None]
    none = {"value": None, "n": int(sum(k for _p, _v, k in pv)), "suppressed": bool(pv) and not have, "month": None,
            "period": None, "base": None, "base_period": None, "pct": None}
    if not have:
        return none
    agg = kpi["agg"]
    if agg in ("peak", "low"):
        best = None
        for p, v, k in have:
            if best is None or (v >= best[1] if agg == "peak" else v <= best[1]):
                best = (p, v, k)
        p, v, k = best
        return dict(none, value=_num(v), n=int(k), suppressed=False, period=p, month=_first_month(cube, p, grain))
    p, v, k = pv[-1]
    if v is None:                       # the last period rests on too few rows: shown as "<5", never an earlier period instead
        return dict(none, n=int(k), suppressed=True, period=p, month=_first_month(cube, p, grain))
    held = _held_months(cube, cells, p, grain)
    if agg == "latest":
        return dict(none, value=_num(v), n=int(k), suppressed=False, period=p, month=_first_month(cube, p, grain), held=held)
    want = _year_back(p, grain) if kpi.get("vs") == "year" else None
    prev = None
    for q, w, kq in reversed(pv[:pv.index((p, v, k))]):
        if want is not None and q != want:
            if q < want:
                break
            continue
        prev = (q, w, kq)
        break
    if prev is None or prev[1] is None:
        return dict(none, n=int(k), period=p, month=_first_month(cube, p, grain))
    d = v - prev[1]
    pct = (d / abs(prev[1]) * 100.0) if prev[1] else None
    return dict(none, value=_num(d), n=int(k + prev[2]), suppressed=False, period=p, month=_first_month(cube, p, grain),
                base=_num(prev[1]), base_period=prev[0], pct=None if pct is None else _num(round(pct, 6)), held=held)


def _held_months(cube: Dict[str, Any], cells: List[List[Any]], p: str, grain: str) -> Optional[int]:
    """How many of a quarter's or a year's months the data reach in the latest period, when they do not all (else None): the
    tile says so, a part of a year beside a whole one (57-dashboard.js heldMonths)."""
    if grain == "month" or int(cube.get("small") or SMALL) == 1:
        return None
    last = max((cube["months"][x[0]] for x in cells if x[0] >= 0), default=None)
    if not last:
        return None
    full = 3 if grain == "quarter" else 12
    held = (int(last[5:7]) - 1) % full + 1
    return held if held < full else None


def _first_month(cube: Dict[str, Any], p: str, grain: str) -> Optional[str]:
    """The first month of the cube that falls in a period (the tile's month, as "latest" always gave it)."""
    for m in cube["months"]:
        if _period(m, grain) == p:
            return m
    return None


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

    small = int(cube.get("small") or SMALL)

    def val(cs: List[List[Any]]) -> Tuple[Optional[float], int]:
        rows = sum(x[1 + nd] for x in cs)
        if j is None:
            return (float(rows) if rows >= small else None), rows
        s = math.fsum(x[2 + nd + 2 * j] for x in cs)
        k = sum(x[3 + nd + 2 * j] for x in cs)
        if k < small:
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
AGG_WORDS = {"sum": "the total of", "average": "the average of", "count": "the number of", "latest": "the latest month's",
             "change": "the change in", "peak": "the highest", "low": "the lowest"}


STATIC_DIMS = ("category", "category_or_rating", "segment", "text", "rating")


def _static_measure(c: Any, name: str, lands: List[str], rec: Dict[str, Any]) -> Optional[str]:
    """The landed measure an engine chart record reads: the column named in a measure's place, else the record's own
    measure (a waterfall, slope or Pareto on the headline's total) when it is one of the file's columns."""
    for land, role in zip(lands, NV._roles(name, len(lands))):
        if role in ("measure", "total"):
            return land
    lab = _rec_measure_label(rec)
    if lab:
        for land in c.measures + c.columns:
            if lab in (land, c.header(land)) or lab.lower() in (land.lower(), c.header(land).lower()):
                return land
    return None


def _rec_measure_label(rec: Any) -> str:
    m = rec.get("measure") if isinstance(rec, dict) else None
    return str(m.get("label") or "") if isinstance(m, dict) else ""


def static_definition(R: Read, st: Dict[str, Any], plural: str) -> str:
    """The definition of an engine chart record on the dashboard (review of the AI-designed dashboard's workbook, 17 Oct
    2026: "Charges by property and category" was defined as "The number of ledger lines." while its cells are dollar
    totals): its own measure and aggregation, what it is split by, the measure's one unit word, and that the filters do
    not change it."""
    c = R.ctx
    name, lands, m = st["chart"], st["lands"], st.get("measure")
    dims = [l for l, r in zip(lands, NV._roles(name, len(lands))) if r in STATIC_DIMS]
    what = str(NV.REGISTRY.get(name, {}).get("what") or "").strip()
    if name == "correlation_heatmap":
        head = "the correlation of each pair of %s" % _and([c.header(l) for l in lands] or ["the file's measures"])
    elif name == "theme_rating_heatmap":
        head = "the share of each rating's texts that use each word"
    else:
        if m is None:
            head = "the total of %s" % st["label"] if st.get("label") else "the number of %s" % plural.lower()
        elif name == "group_ranges" or not c.is_flow(m):
            head = "the average of %s" % c.header(m)
        else:
            head = "the total of %s" % c.header(m)
        if name == "change_heatmap":
            head = "the year-on-year change of " + head
        if dims:
            head += ", by %s" % _and([c.header(l) for l in dims])
        if name in ("calendar_heatmap", "change_heatmap"):
            head += (" and " if dims else ", by ") + "month"
        if m is not None:
            head += "; %s" % ("a flow: added up" if c.is_flow(m) else "a level: averaged, never added up over time")
            u = c.unit(m)
            if u:
                head += "; in %s" % u
    out = head[0].upper() + head[1:] + "."
    if what:
        out += " The engine's own chart (%s), over every row it kept: the filters do not change it." % what.rstrip(".")
    return out


def _and(xs: List[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def definition(R: Read, x: Dict[str, Any], plural: str, measures: Dict[str, Dict[str, Any]]) -> str:
    c = R.ctx
    if x.get("draw") == "static" and isinstance(x.get("static"), dict) and x["static"].get("from_report"):
        say = str(x["static"].get("say") or "")
        return NV._cut(("The report's own chart: %s " % say.rstrip(".") + ".") if say else "The report's own chart.", 300) + \
            " Over every row the engine kept: the filters do not change it."
    if x.get("draw") == "static" and isinstance(x.get("static"), dict):
        return static_definition(R, x["static"], plural)
    m = x.get("measure")
    agg = x.get("agg") or ("count" if m is None else "sum")
    g = GRAIN_WORD[R.grain]
    one = "total" if m is not None and c.is_flow(m) else "average"
    if m is None:
        what = "the number of %s" % plural.lower()
    elif agg == "latest":
        if R.obs:
            what = "the latest %s's value of %s" % (g, R.name(m))
        else:
            what = "the latest %s's %s of %s" % (g, one, R.name(m))
    elif agg == "change":
        what = "the change in %s from %s to the latest %s" % (
            R.name(m), "the same %s a year earlier" % g if x.get("vs") == "year" else "the %s before" % g, g)
    elif agg in ("peak", "low"):
        what = "the %s %s %s of %s in the range shown, and when" % ("highest" if agg == "peak" else "lowest", _LY[g],
                                                                   "value" if R.obs else one, R.name(m))
    elif agg == "count":
        what = "the number of %s" % plural.lower()
    elif x.get("draw") == "change":
        what = "the change in %s from each %s to the next" % (R.name(m), g)
    elif x.get("draw") == "periods":
        what = "the %s of %s by decade (or by year, for a short range), in time order" % (one, R.name(m))
    else:
        what = "%s %s" % (AGG_WORDS[agg], R.name(m))
    wh = x.get("where")
    if wh:
        vals = ", ".join(wh["values"])
        what += " over the rows where %s is %s%s" % (R.name(wh["dim"]), "not " if wh.get("exclude") else "", vals)
    if x.get("by"):
        what += ", by %s" % R.name(x["by"])
    if m is not None and agg != "count" and not (R.obs and (agg in SERIES_AGGS or x.get("draw") == "change")):
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

    sing = row_noun(rep)[1]

    def rows_w(n: int) -> str:
        return "%s %s" % (NV._count(n), sing if n == 1 else plural.lower())

    # (a) needs your check: what a person must look at, in plain words with counts, from the engine's own records
    need: List[Dict[str, Any]] = []
    rules = {str(r.get("name")): r for r in cl.get("rules") or [] if isinstance(r, dict)}
    issues = [str(x) for x in (rep.get("health") or {}).get("issues") or []]
    latest_future = next((m.group(1) for x in issues for m in [re.search(r"latest (\d{4}-\d{2}-\d{2})", x)] if m), "")
    for q in cl.get("quarantine_reasons") or []:
        if not isinstance(q, dict) or not q.get("count"):
            continue
        n = int(q["count"])
        name = str(q.get("reason") or "").split(": ", 1)[0]
        rr = rules.get(name) or {}
        kind, col = str(rr.get("kind") or ""), rr.get("column")
        h = c.header(str(col)) if col else ""
        if col and hid_rx is not None and hid_rx.search(h):
            h = "a column you withheld"
        hv, ar, rp = ("has", "is", "repeats") if n == 1 else ("have", "are", "repeat")
        words = {
            "dedupe": "%s %s another row exactly: the repeats were set aside, one copy of each counts" % (rows_w(n), rp),
            "range": "%s %s %s %s outside the range it can take (a negative or impossible value): set aside, not corrected" % (rows_w(n), hv, "an" if h[:1].lower() in "aeiou" else "a", h),
            "not_future": "%s %s dated in the future%s: set aside" % (rows_w(n), ar, " (the latest %s)" % latest_future if latest_future else ""),
            "date": "%s %s %s %s that is no real date (for example a day that does not exist): set aside" % (rows_w(n), hv, "an" if h[:1].lower() in "aeiou" else "a", h),
            "date_plausible": "%s %s a placeholder or impossibly early %s: set aside" % (rows_w(n), hv, h),
            "series_end": "%s %s dated after the series ends: set aside" % (rows_w(n), ar),
            "numeric": "%s %s %s %s that is not a number: set aside" % (rows_w(n), hv, "an" if h[:1].lower() in "aeiou" else "a", h),
            "boolean": "%s %s %s %s that is not a yes or no: set aside" % (rows_w(n), hv, "an" if h[:1].lower() in "aeiou" else "a", h),
        }.get(kind) or "%s set aside: %s%s" % (rows_w(n), str(q.get("reason") or "").split(": ", 1)[-1], " (%s)" % h if h else "")
        need.append({"text": words[0].upper() + words[1:] + ".", "rule": name, "column": h or None, "count": n, "where": "set_aside"})
    for x in issues:
        m = re.match(r"^([\w.]+): mixed content: [\d,]+ date-like, [\d,]+ numeric and ([\d,]+) text values", x)
        if m and m.group(1) in c.measures:
            n = int(m.group(2).replace(",", ""))
            if n:
                h = c.header(m.group(1))
                need.append({"text": "%s %s values were words, not numbers (a placeholder or a note): the engine left "
                                     "them blank and guessed nothing." % (NV._count(n), h),
                             "rule": "%s_numeric" % m.group(1), "column": h, "count": n, "where": "blank"})
    months = cube["months"]
    grain = cube.get("grain") or "month"
    small = int(cube.get("small") or SMALL)
    if months:
        span = NB._month_range(months[0], months[-1])
        if grain == "month":
            have = set(months)
            gaps = [m for m in span if m not in have]
            if gaps:
                need.append({"text": "Months with no rows, left as gaps on every chart (never filled): %s." % "; ".join(_ranges(gaps)[:12]),
                             "rule": "", "column": None, "count": len(gaps), "where": "months"})
        else:                               # a yearly or quarterly series: a gap is a missing year or quarter, never the months between
            have_p = {_period(m, grain) for m in months}
            span_p = list(dict.fromkeys(_period(m, grain) for m in span))
            gaps_p = [p for p in span_p if p not in have_p]
            if gaps_p:
                need.append({"text": "%ss with no rows, left as gaps on every chart (never filled): %s." % (
                    grain.capitalize(), ", ".join(period_word(p) for p in gaps_p[:24])), "rule": "", "column": None,
                    "count": len(gaps_p), "where": "months"})
        nd = len(cube["dims"])
        by_m: Dict[int, int] = {}
        for x in cube["cells"]:
            if x[0] >= 0:
                by_m[x[0]] = by_m.get(x[0], 0) + x[1 + nd]
        thin = [months[i] for i, k in sorted(by_m.items()) if k < small]
        if thin and small > 1 and grain == "month":
            need.append({"text": "Months with fewer than %d rows (their figures show as “<5”): %s." % (SMALL, "; ".join(_ranges(thin)[:12])),
                         "rule": "", "column": None, "count": len(thin), "where": "months"})
    if cube.get("no_date_rows"):
        need.append({"text": "%s have no date the engine reads: they count only while the date range is the whole span." % (
            rows_w(int(cube["no_date_rows"]))[0].upper() + rows_w(int(cube["no_date_rows"]))[1:]), "rule": "", "column": None,
            "count": int(cube["no_date_rows"]), "where": "other"})
    for d in cube["dims"]:
        if d.get("hidden"):
            need.append({"text": "%s: %s a word with a column you withheld or coded, so %s counted in “other” and never named "
                                 "(privacy: a value that could point to a person is never printed)." % (
                d["column"], "1 value shares" if d["hidden"] == 1 else "%s values share" % NV._count(d["hidden"]),
                "it is" if d["hidden"] == 1 else "they are"), "rule": "", "column": d["column"], "count": int(d["hidden"]), "where": "other"})
        if d.get("folded"):
            need.append({"text": "%s: its %s smallest values are counted together as “other”." % (d["column"], NV._count(d["folded"])),
                         "rule": "", "column": d["column"], "count": int(d["folded"]), "where": "other"})
    # (b) fixed automatically: the cleaning rules with their counts
    fixed: List[str] = []
    for fx in (cl.get("fixes") or [])[:16]:
        if not isinstance(fx, dict):
            continue
        col = fx.get("column")
        t = clean_line("%s (%s values%s)." % (str(fx.get("what") or fx.get("rule") or "").rstrip("."),
                                             NV._count(fx.get("count") or 0), ", " + c.header(str(col)) if col else ""))
        if t:
            fixed.append(t)
    # (c) data quality detail: the health check's own lines
    detail = [t for t in (clean_line(x) for x in issues[:20]) if t]
    # about the design: what the engine refused or changed in it
    design = ["%s: %s." % (r_["element"][0].upper() + r_["element"][1:], r_["why"].rstrip(".")) for r_ in R.refused]
    return {"data_range": [months[0], months[-1]] if months else None,
            "data_range_text": ("%s to %s" % (period_word(_period(months[0], grain)), period_word(_period(months[-1], grain))))
            if months else
            "the file has no date the engine reads",
            "rows": {"in": rows_in, "kept": kept, "set_aside": max(0, rows_in - kept) if rows_in else 0,
                     "reasons": reasons},
            "needs_check": need[:30], "fixed": fixed, "detail": detail, "design": design[:30]}


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
    if chosen == "engine":                    # an AI's or the recorded design is its own: the engine adds no chart to it
        _add_report_charts(R, design)
    _area_check(R, design)
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
            y["where"] = dict(y["where"], key=dkey[y["where"]["dim"]], column=R.name(y["where"]["dim"]))
            y["where"].pop("dim", None)
            lv = next(d["levels"] for d in cube["dims"] if d["key"] == y["where"]["key"])
            gone = [v for v in y["where"]["values"] if v not in lv]
            y["where"]["values"] = [v for v in y["where"]["values"] if v in lv]
            if gone:
                R.refuse("%s %s" % ("tile" if "draw" not in x else "chart", x.get("label") or x.get("title") or x["id"]),
                         "a value it filters on is counted in \u201cother\u201d on this dashboard")
                return False
        y["definition"] = definition(R, x, plural, measures)
        y.pop("static", None)
        y["measure"] = R.name(x["measure"]) if x.get("measure") else None
        y["by"] = R.name(x["by"]) if x.get("by") else None
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
            y["title"] = _title(c, y, plural, R.grain)
        charts.append(y)
    filters = []
    for f in design["filters"]:
        if f["kind"] == "date":
            filters.append({"kind": "date", "column": c.header(c.date), "grain": R.grain,
                            "label": {"year": "Years", "quarter": "Quarters", "month": "Months"}[R.grain]})
        elif f["dim"] in dkey:
            filters.append({"kind": "category", "key": dkey[f["dim"]], "column": c.header(f["dim"]), "label": R.name(f["dim"])})
    ids = {k["id"] for k in kpis} | {x["id"] for x in charts}
    if block is not None:                    # the AI's visuals are drawn after the build (nl_browser._dashboard_visuals prunes them)
        ids |= {str(v.get("id")) for v in block.get("visuals") or [] if isinstance(v, dict) and isinstance(v.get("id"), str)}
    questions = [dict(q, answered_by=[i for i in q["answered_by"] if i in ids]) for q in design["questions"]]
    questions = [q for q in questions if q["answered_by"]]
    layout = [[i for i in row if i in ids] for row in design["layout"]]
    layout = [r for r in layout if r]
    defaults = {"kpis": {k["id"]: query_kpi(cube, k) for k in kpis}}
    # a peak, low or latest tile reads the charts' step over the whole range (a year for 11 years of records): a label naming another
    # step names the one it reads (20 Oct 2026, live: Chicago's "Busiest month" showed 72,885 "in 2014", a year)
    if not R.obs:
        for k in kpis:
            per = str((defaults["kpis"].get(k["id"]) or {}).get("period") or "")
            if k["agg"] not in ("peak", "low", "latest") or not per:
                continue
            step = "year" if len(per) == 4 else "quarter" if "Q" in per else "month"
            k["label"] = re.sub(r"(?i)\b(month|quarter|year)\b", lambda m: m.group(0) if m.group(0).lower() == step else
                                (step if m.group(0).islower() else step.capitalize()), k["label"])
    dims_used = [c.header(d) for d in c.dims]
    if R.obs:
        ms = [R.name(m) for m in c.measures[:3]]
        dims = [R.name(d) for d in c.dims if c.role_ok(d, "category") == ""][:2]
        gtext = "One row is one %s's published figure%s of %s%s." % (
            GRAIN_WORD[R.grain], "s" if len(ms) > 1 else "", _and(ms) or "the series",
            (", for one " + _and(dims)) if dims else "")
    else:
        gtext = "One row is one %s: %s." % (sing, _grain_words(c))
    grain = {"text": gtext, "noun": plural, "by": "engine"}
    columns = _column_meanings(c, R.grain)
    out = {"version": VERSION, "ok": True, "chosen_by": chosen,
           "recorded": recorded if chosen == "recorded" else "",
           "audience": design["audience"], "questions": questions, "grain": grain, "columns": columns, "kpis": kpis, "charts": charts,
           "filters": filters, "layout": layout, "cube": cube, "defaults": defaults, "influencers": _influencers_or_none(R, design, dkey),
           "remarks": remarks(R, cube, plural), "refused": list(R.refused), "suppress_below": R.small,
           "observations": bool(R.obs),
           "rules": ["Flows are added up; levels, rates and balances are averaged, never added up over time.",
                     ("A figure that rests on fewer than %d rows is shown as “<5”, after every filter." % SMALL) if R.small > 1 else
                     "No figure is hidden for resting on few rows: each tile says how many rows or values it rests on, so read a "
                     "small one with care.",
                     "A %s with no rows is a gap: nothing is filled, smoothed or interpolated." % GRAIN_WORD[R.grain],
                     "Columns you withheld or coded are never a filter, a label or a group."],
           "title": _dash_title(R, rep)}
    del dims_used
    return out


INFL_MIN_ROWS = 30              # a level's rows before its average is compared (and the rest's)
INFL_MAX = 6
INFL_PER_COLUMN = 3


def key_influencers(R: Read, design: Dict[str, Any], dkey: Dict[str, str]) -> Dict[str, Any]:
    """Power BI's key influencers, kept honest (AI Analyst v2 phase D, 20 Oct 2026): for the dashboard's lead measure (its first tile's
    measure, else the engine's lead) and each category column the dashboard filters or splits by, the levels whose average differs
    from the average of every other row, by Welch's 95% interval on the difference; only a level on INFL_MIN_ROWS rows or more, and
    only an interval that excludes zero. Over every row the engine kept (not filtered). An association, never a cause: a level may
    differ because of something else that comes with it."""
    import math
    import pandas as pd
    c = R.ctx
    frame = c.frame
    m = next((k.get("measure") for k in design.get("kpis") or [] if k.get("measure") and k.get("measure") in c.measures), None) \
        or lead_measure(R)
    if not m or frame is None or m not in frame.columns or not dkey:
        return {"measure": None, "items": []}
    v = pd.to_numeric(frame[m], errors="coerce")
    items: List[Dict[str, Any]] = []
    cands: List[Dict[str, Any]] = []
    for land in dkey:
        if land not in frame.columns:
            continue
        g = frame[land].astype(str)
        ok = v.notna() & frame[land].notna()
        vv, gg = v[ok], g[ok]
        if len(vv) < 2 * INFL_MIN_ROWS:
            continue
        two = gg.nunique() == 2              # a two-level column: its levels mirror each other, so one finding, not two
        c0 = float(vv.mean())                    # centred first: sums of squares of large values (salaries) lose their digits
        w = vv - c0
        st = w.groupby(gg).agg(["count", "sum"])
        st["sq"] = (w * w).groupby(gg).sum()
        N, S, Q = float(len(w)), float(w.sum()), float((w * w).sum())
        for lev, r_ in st.iterrows():            # the rest's mean and variance from the totals: one pass, any number of levels
            n1, s1, q1 = float(r_["count"]), float(r_["sum"]), float(r_["sq"])
            n2, s2, q2 = N - n1, S - s1, Q - q1
            if n1 < INFL_MIN_ROWS or n2 < INFL_MIN_ROWS:
                continue
            if two and any(z_["column"] == c.header(land) for z_ in cands):
                break
            a, b = s1 / n1, s2 / n2
            va, vb = max(0.0, (q1 - n1 * a * a) / (n1 - 1)), max(0.0, (q2 - n2 * b * b) / (n2 - 1))
            a, b = a + c0, b + c0
            se = math.sqrt(va / n1 + vb / n2)
            if not (se > 0) or not math.isfinite(se):
                continue
            cands.append({"column": c.header(land), "level": str(lev)[:60], "mean": a, "rest_mean": b, "d": a - b, "se": se,
                          "rows": int(n1), "rest_rows": int(n2)})
    # every comparison made counts (Bonferroni): with dozens of groups, a 95% interval each would list noise as an influencer
    from statistics import NormalDist
    m_tests = max(1, len(cands))
    z = NormalDist().inv_cdf(1 - 0.025 / m_tests)
    for k in cands:
        lo, hi = k["d"] - z * k["se"], k["d"] + z * k["se"]
        if lo <= 0 <= hi:
            continue
        items.append({"column": k["column"], "level": k["level"], "mean": _num(k["mean"]), "rest_mean": _num(k["rest_mean"]),
                      "diff": _num(k["d"]), "lo": _num(lo), "hi": _num(hi), "ratio": _num(k["mean"] / k["rest_mean"]) if k["rest_mean"] else None,
                      "rows": k["rows"], "rest_rows": k["rest_rows"]})
    items.sort(key=lambda z_: -abs(z_["diff"]))
    per: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for z_ in items:                      # at most INFL_PER_COLUMN a column: a thousand job titles do not crowd out every other column
        if per.get(z_["column"], 0) < INFL_PER_COLUMN:
            per[z_["column"]] = per.get(z_["column"], 0) + 1
            kept.append(z_)
    items = kept
    return {"measure": c.header(m),          # its unit is the cube's for that measure (57-dashboard.js reads D.cube.measures)
            "items": items[:INFL_MAX], "rows_min": INFL_MIN_ROWS, "tests": m_tests,
            "note": "Over every row the engine kept, not filtered. An association, not a cause: a group may differ because of something "
                    "else that comes with it."}


def _influencers_or_none(R: Read, design: Dict[str, Any], dkey: Dict[str, str]) -> Optional[Dict[str, Any]]:
    try:
        return key_influencers(R, design, dkey)
    except Exception:  # noqa: BLE001 - the dashboard stands without its influencers
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return None


REPORT_CHARTS_MIN = 2           # a dashboard with fewer charts that follow the filters also carries the report's own


def _add_report_charts(R: Read, design: Dict[str, Any]) -> None:
    """A table of things with no date (states, schools, wines) left the dashboard with tiles and no chart (the universal corpus, 19
    Oct 2026), while its report drew a ranking, a spread and how the measures move together. When the design holds fewer than
    REPORT_CHARTS_MIN charts that follow the filters, the report's own chart records (rep.viz, under the same privacy rules; the
    map is the tab's own, so never twice) are added as engine charts, marked not filtered."""
    import copy
    live = [x for x in design["charts"] if x.get("draw") != "static"]
    if len(live) >= REPORT_CHARTS_MIN:
        return
    seen = {x["id"] for x in design["kpis"]} | {x["id"] for x in design["charts"]}
    titles = {str(x.get("title") or "") for x in design["charts"]}
    added: List[str] = []
    for rec in ((R.rep.get("viz") or {}).get("charts") or []):
        if len(design["charts"]) >= CHART_MAX:
            break
        if not isinstance(rec, dict) or rec.get("kind") in (None, "geo", "table") or str(rec.get("title") or "") in titles:
            continue
        cid = "r%d" % (len(added) + 1)
        while cid in seen:
            cid += "_"
        seen.add(cid)
        titles.add(str(rec.get("title") or ""))
        design["charts"].append({"id": cid, "chart": str(rec.get("chart") or rec.get("kind")), "draw": "static",
                                 "record": copy.deepcopy(rec), "measure": None, "agg": None, "by": None, "where": None,
                                 "title": NV._cut(str(rec.get("title") or ""), 90), "why": NV._cut(str(rec.get("why") or ""), 200),
                                 "unit_word": "", "static": {"from_report": True, "say": " ".join(
                                     str(rec.get("summary") or rec.get("subtitle") or "").split())[:240]}})
        added.append(cid)
    for j in range(0, len(added), 2):
        design["layout"].append(added[j:j + 2])
    if added and not design["questions"]:
        design["questions"].append({"q": "Which stand out, and how do the measures relate?", "answered_by": added[:4]})


def _dash_title(R: Read, rep: Dict[str, Any]) -> str:
    """The tab's title: the headline measure's name in words when the file names it by a code, else the file's name."""
    c = R.ctx
    has, pm = c.primary_measure()
    pm = pm if has else (c.measures[0] if c.measures else None)
    if pm and R.name(pm) != c.header(pm):
        return NV._cut(R.name(pm), 90)
    return "%s dashboard" % NV._cut(str((rep.get("input") or {}).get("name") or "Data"), 60)


def _column_meanings(c: Any, grain: str = "month") -> List[Dict[str, str]]:
    """What the engine reads each column it kept as (never a flagged column): the step-1 card's list."""
    out = []
    if c.date:
        out.append({"column": c.header(c.date), "reads_as": "the date (%ss)" % GRAIN_WORD.get(grain, "month"), "unit": ""})
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


def _title(c: Any, y: Dict[str, Any], plural: str, grain: str = "month") -> str:
    what = y["measure"] or plural
    step = "by %s" % GRAIN_WORD.get(grain, "month")
    if y.get("where"):
        what += " (%s %s)" % ("not" if y["where"].get("exclude") else "", ", ".join(y["where"]["values"][:3]))
        what = what.replace("( ", "(")
    if y["draw"] == "line":
        return NV._cut("%s %s%s" % (what, step, (", by " + y["by"]) if y.get("by") else ""), 90)
    if y["draw"] == "change":
        return NV._cut("%s: change from the %s before" % (what, GRAIN_WORD.get(grain, "month")), 90)
    if y["draw"] == "periods":
        return NV._cut("%s by decade" % what if grain == "year" else "%s by year" % what, 90)
    if y["draw"] == "area":
        return NV._cut("%s %s, split by %s" % (what, step, y["by"]), 90)
    if y["draw"] == "bar":
        return NV._cut("%s by %s" % (what, y["by"]), 90)
    if y["draw"] == "grouped":
        return NV._cut("%s by %s: the latest 12 months against the 12 before" % (what, y["by"]), 90)
    if y["draw"] == "multiples":
        return NV._cut("%s %s, one panel for each %s" % (what, step, y["by"]), 90)
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


def _fmt(v: float, unit: str) -> str:
    """A tile's figure as the page prints it (57-dashboard.js fmt): 0 decimals from 1,000, 1 from 10, else 2; a sign before, % after,
    a word after."""
    a = abs(v)
    dp = 0 if a >= 1000 else 1 if a >= 10 else 2
    s = ("{:,.%df}" % dp).format(a)
    if dp and "." in s:
        s = s.rstrip("0").rstrip(".")
    neg = "-" if v < 0 else ""
    if unit in ("$", "€", "£", "¥"):
        return neg + unit + s
    if unit == "%":
        return neg + s + "%"
    return neg + s + (" " + unit if unit else "")


def key_numbers(d: Any) -> List[Dict[str, Any]]:
    """The dashboard's tiles as the report writer reads them (AI Analyst v2 phase G, 20 Oct 2026: a salary report never stated the
    average salary, a reviews report never the average rating): each tile's label and its figure in the page's words, the rows it
    rests on, and the period a latest, change, peak or low tile reads. Every figure is the engine's (D.defaults)."""
    if not isinstance(d, dict) or not d.get("ok"):
        return []
    units = {m.get("key"): m.get("unit") or "" for m in (d.get("cube") or {}).get("measures") or []}
    out: List[Dict[str, Any]] = []
    for k in d.get("kpis") or []:
        v = ((d.get("defaults") or {}).get("kpis") or {}).get(k.get("id")) or {}
        if v.get("value") is None:
            continue
        unit = "" if k.get("agg") == "count" else (k.get("unit_word") or units.get(k.get("mkey")) or "")
        if re.fullmatch(r"(?i)currency( units?)?|units?", unit or ""):
            unit = ""                       # a placeholder, never printed as if it named the currency
        val = float(v["value"])
        if k.get("agg") == "change":
            fig = ("+" if val > 0 else "") + (_fmt(val, "" if unit == "%" else unit) + (" pp" if unit == "%" else ""))
            when = " (%s against %s)" % (period_word(v.get("period")), period_word(v.get("base_period"))) if v.get("base_period") else ""
        else:
            fig = _fmt(val, unit)
            when = " in %s" % period_word(v.get("period")) if k.get("agg") in ("latest", "peak", "low") and v.get("period") else ""
        n = int(v.get("n") or 0)
        rows = "" if d.get("observations") or k.get("agg") in ("latest", "change", "peak", "low", "count") else \
            ", on %s %s" % ("{:,}".format(n), "rows" if k.get("agg") == "count" else "values")
        out.append({"label": str(k.get("label") or "")[:60], "text": "%s: %s%s%s" % (str(k.get("label") or "")[:60], fig, when, rows),
                    "agg": k.get("agg")})
    return out[:KPI_MAX]


# ----------------------------------------------------------------------------- the AI's look at its own dashboard
SIGNALS_MAX = 8


def signals(d: Any) -> List[Dict[str, str]]:
    """What went wrong with an AI-designed dashboard, for the planner's one self-correction (nl_browser._plan_signals'
    feedback, kind "dashboard"), in the engine's words and never a figure: elements the engine refused, tiles that show no
    figure, charts with nothing to draw, and a design that reads a table of published figures as records (no latest, change,
    peak or low tile). Nothing for the engine's or the recorded design."""
    if not isinstance(d, dict) or not d.get("ok") or d.get("chosen_by") != "ai":
        return []
    out: List[Dict[str, str]] = []

    def add(t: str) -> None:
        if len(out) < SIGNALS_MAX:
            out.append({"kind": "dashboard", "detail": _text(t, 200)})
    for r in d.get("refused") or []:
        add("%s was refused: %s" % (r.get("element"), r.get("why")))
    cube = d.get("cube") or {}
    for k in d.get("kpis") or []:
        v = ((d.get("defaults") or {}).get("kpis") or {}).get(k["id"]) or {}
        if v.get("value") is None:
            add("tile %s shows no figure (%s)" % (k.get("label") or k["id"],
                                                  "it rests on too few rows" if v.get("suppressed") else "no rows for it"))
    for x in d.get("charts") or []:
        if x.get("draw") in ("line", "area", "multiples", "bar"):
            try:
                q = query_series(cube, x)
            except Exception:  # noqa: BLE001 - a chart the check cannot read is not a signal
                continue
            pts = [p for s in q.get("series") or [] for p in s.get("points") or []] if "series" in q else \
                [b.get("value") for b in q.get("bars") or []]
            if not any(p is not None for p in pts):
                add("chart %s has nothing to draw: every point rests on too few rows or is empty" % (x.get("title") or x["id"]))
    if d.get("observations") and not any(k.get("agg") in SERIES_AGGS for k in d.get("kpis") or []):
        add("each row is a published figure (one a %s): lead with latest, change, peak and low tiles, and a change_chart, not totals "
            "or averages over the whole range" % GRAIN_WORD.get(cube.get("grain") or "month", "month"))
    if not d.get("charts") and not d.get("visuals"):
        add("the dashboard has no chart")
    return out
