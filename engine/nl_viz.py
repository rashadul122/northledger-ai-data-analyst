"""
nl_viz: charts chosen from the data (the chart registry, Batch 1; plan/CHART-REGISTRY-DESIGN.md, CONTRACT-v2.md 5.9).

    import nl_viz
    rep["viz"] = nl_viz.build(rep, ctx)          # after rep["scenarios"]; appends rule "V" records to rep["charts"]
    profile["chart_limits"] = nl_viz.limits(...) # one {chart, ok, why} per menu chart, for the planner

The frozen interface is tools/fixtures/viz/spec.json (version 2026-09-30.1): the menu, the record's schema and
each kind's invariants. The rules this module keeps:
  * The engine computes every value and writes every printed string: cell texts, step texts, legend lines, labels,
    table cells, the summary, and every colour tier. A renderer formats nothing but axis ticks.
  * The AI only selects charts (plan.charts, at most 8, each with a why). Each choice is checked in order (the menu
    name, the columns, privacy, their roles, the chart's limit, its conditions), then built and reconciled with the
    engine's own overlapping figures (the scenarios items, the trend and season charts, the compare and themes
    analyses, the corr chart), or refused with a reason in rep.viz.refused. A refusal is never a re-plan signal.
  * With no AI chart built, the engine picks up to 4 itself (auto_rank), chosen_by "engine".
  * Privacy: no flagged column (withheld, coded, or kept by the visitor) in any role or label; no printed figure
    rests on fewer than 5 rows (a heatmap cell reads "<5"; a bar, row or step is folded into "other").
  * Byte caps (the owner's decision, 30 Sep 2026): CHART_BYTES 6,000 a record, except a heatmap, HEATMAP_BYTES
    12,000. A grid over its cap is trimmed (a calendar's oldest year first) and its subtitle says so.
  * Deterministic (fixed seeds, explicit sort keys, math.fsum) and finite: no NaN reaches a record.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

import nl_browser as NB
import nl_scenarios as NS

VERSION = "2026-09-30.1"

# ----------------------------------------------------------------------------- caps (spec.json "caps")
VIZ_MAX = 8                 # charts a report, and items kept from plan.charts
VIZ_AUTO = 4                # the engine's own picks when no AI chart is built
HEAT_MAX = 3                # heatmaps a report
SMALL_CELL = 5              # no printed figure rests on fewer rows
AI_CHARTS_MAX = 10          # charts in results_for_ai (the analyses' and these together)
CHART_BYTES = 6000          # one record as JSON.stringify writes it, in UTF-8 bytes
HEATMAP_BYTES = 12000       # ... a heatmap's (the owner's decision on the spec's byte-cap conflict, 30 Sep 2026)
CHART_LIMITS_MAX = 16
CHART_LIMIT_WHY_MAX = 160
PLAN_CHARTS_READ = 12
PLAN_WHY_MAX = 200
KIND_MAX_CHARS = 10
HEAT_ROWS_MAX = 12
HEAT_COLS_MAX = 24
CELL_TEXT_MAX = 12
WATERFALL_PARTS_MAX = 12
PARETO_BARS_MAX = 20
DOT_ROWS_MAX = 12
SLOPE_ROWS_MAX = 12
LABEL_MAX = 80
TABLE_COLS_MAX = 25
TABLE_ROWS_MAX = 30
ANCHORS_MAX = 24
RECONCILE_TOL = 1e-6
CARD_TABLE_ROWS = 12        # a card's table (cards_for_ai)

# the data conditions the menu states
CALENDAR_MIN_MONTHS = 24    # a calendar needs 24 months over 2 calendar years
CALENDAR_YEARS = 12         # the latest 12 years shown
CHANGE_MIN_MONTHS = 13      # a change heatmap needs the same month a year before
CHANGE_COLS = 24            # the latest 24 such months
SEGMENT_LEVELS = (2, 12)    # a segment's levels over the two windows
CROSS_ROWS = 12             # a crosstab's rows (the 11 with the most rows and "other")
CROSS_COLS = 24             # ... its columns (the 23 with the most rows and "other")
RATING_LEVELS = (2, 10)     # a rating column's whole-number levels
PARETO_MIN_LEVELS = 8
PARETO_MAX_LEVELS = 5000    # a column the plan types as a category or an entity is a Pareto's up to 5,000 levels
PARETO_TYPES = ("category", "entity", "geography")
CORR_MEASURES = (3, 12)
CORR_MIN_ROWS = 10
THEME_WORDS = 12
THEME_MIN_TEXTS = NB.THEMES_MIN_TEXTS
COMPARE_SEED, COMPARE_B = 20260925, 999

OTHER = "other"
MINUS = "−"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ENGINE_WHY = "Chosen by the engine: "

# ----------------------------------------------------------------------------- the menu (spec.json "menu")
MEASURE_TYPES = ("flow_amount", "count", "level", "percentage", "log_scale", "rating", "ordinal", "duration")
TOTAL_TYPES = ("flow_amount", "count")
AVERAGE_TYPES = ("level", "percentage", "log_scale", "rating", "ordinal", "duration")
SEGMENT_TYPES = ("category", "geography", "ordinal", "boolean")
CATEGORY_TYPES = ("category", "geography", "entity", "ordinal", "boolean")
RATING_TYPES = ("rating", "ordinal")
NOT_A_CATEGORY_ROLE = ("key", "metadata", "set_aside", "date", "identifier")

REGISTRY: Dict[str, Dict[str, Any]] = {
    "contribution_waterfall": {
        "kind": "waterfall", "section": "drove", "args": [("segment", False), ("total", True)],
        "args_text": "[category, total?]",
        "what": "each segment's contribution to the headline's change, from the prior total to the latest",
        "limit_needs": "needs a total over two 12-month windows and a category of 2 to 12 levels (2 with 5 rows a "
                       "window)"},
    "pvm_waterfall": {
        "kind": "waterfall", "section": "drove", "args": [("total", False), ("units", False), ("segment", True)],
        "args_text": "[total, units, category?]", "what": "the headline's change split into price, volume and mix",
        "limit_needs": "needs a money total over two 12-month windows and a units column filled on every row"},
    "calendar_heatmap": {
        "kind": "heatmap", "section": "headline", "section_if_not_primary": "other", "args": [("measure", True)],
        "args_text": "[measure?]",
        "what": "month (across) by year (down) of the measure, on the engine's date column",
        "limit_needs": "needs 24 or more months over 2 or more calendar years"},
    "change_heatmap": {
        "kind": "heatmap", "section": "drove", "args": [("category", False), ("measure", True)],
        "args_text": "[category, measure?]",
        "what": "segment (down) by month (across) of the year-on-year % change of the measure",
        "limit_needs": "needs half the category's level-months to hold 5 or more rows, over 13 or more months"},
    "crosstab_heatmap": {
        "kind": "heatmap", "section": "other",
        "args": [("category", False), ("category_or_rating", False), ("measure", True)],
        "args_text": "[category, category, measure?]",
        "what": "two categories against each other: rows counted, or the measure's total (a flow) or average (a "
                "level)",
        "limit_needs": "needs two category columns of 2 to 24 levels"},
    "theme_rating_heatmap": {
        "kind": "heatmap", "section": "other", "args": [("text", False), ("rating", False)],
        "args_text": "[text, rating]",
        "what": "the themes analysis's words (down) by rating (across): the share of each rating's texts that use "
                "the word",
        "limit_needs": "needs a free-text column with 20 or more texts and a rating column of 2 to 10 levels"},
    "correlation_heatmap": {
        "kind": "heatmap", "section": "other", "args": [("measure", False)], "repeat": CORR_MEASURES,
        "args_text": "[measure, measure, measure, ...] (3 to 12; [] for the engine's own measures)",
        "what": "Pearson r of each pair of measures on pairwise complete rows",
        "limit_needs": "needs 3 to 12 numeric columns and 10 or more rows"},
    "group_ranges": {
        "kind": "dot_range", "section": "other", "args": [("measure", False), ("category", False)],
        "args_text": "[measure, category]",
        "what": "each group's average with its 95% bootstrap range and its median: the compare analysis as a dot "
                "plot",
        "limit_needs": "needs a measure and a category with 2 or more groups of 5 or more values"},
    "pareto": {
        "kind": "pareto", "section": "other", "args": [("category", False), ("total", True)],
        "args_text": "[category, total?]",
        "what": "the levels ranked by their total (or rows): the top 20, the running share of the whole, and k80",
        "limit_needs": "needs a category of 8 or more levels (products, brands, stores) and a total"},
    "slope": {
        "kind": "slope", "section": "scenarios", "args": [("segment", False), ("total", True)],
        "args_text": "[category, total?]", "what": "each segment's prior and latest total, joined by a line",
        "limit_needs": "needs a total over two 12-month windows and a category of 2 to 12 levels"},
}
MENU = tuple(REGISTRY)

# auto_rank (spec.json "auto_rank"): 0-9 by plan.kind, plus bonuses
RANK_KINDS = ("time_series_panel", "event_log", "transactions", "survey", "cross_section", "text_corpus",
              "sensor_readings", "geographic_table", "ledger", "other")
RANK_BASE = {
    "contribution_waterfall": (6, 6, 9, 2, 3, 1, 3, 6, 9, 5),
    "pvm_waterfall": (4, 3, 8, 1, 2, 1, 1, 2, 6, 4),
    "calendar_heatmap": (8, 7, 7, 3, 2, 3, 8, 4, 7, 5),
    "change_heatmap": (7, 5, 6, 2, 2, 1, 5, 6, 5, 4),
    "crosstab_heatmap": (3, 6, 5, 8, 7, 5, 2, 4, 4, 5),
    "theme_rating_heatmap": (1, 2, 1, 8, 2, 9, 1, 1, 1, 2),
    "correlation_heatmap": (5, 2, 3, 5, 7, 2, 7, 5, 2, 4),
    "group_ranges": (4, 5, 4, 8, 8, 6, 6, 5, 3, 5),
    "pareto": (3, 6, 7, 3, 5, 4, 2, 5, 7, 5),
    "slope": (6, 4, 6, 2, 2, 1, 3, 6, 5, 4),
}
RANK_ANALYSIS = {"compare": "group_ranges", "themes": "theme_rating_heatmap", "relationship": "correlation_heatmap",
                 "rank": "pareto", "share": "pareto", "trend": "calendar_heatmap", "extremes": "calendar_heatmap"}
GOAL_KEYWORDS = {
    "contribution_waterfall": ("drove", "driver", "contribut", "why", "bridge", "grew", "fell"),
    "pvm_waterfall": ("price", "volume", "mix", "per unit"),
    "calendar_heatmap": ("season", "month", "calendar", "monthly"),
    "change_heatmap": ("year on year", "year-over-year", "yoy", "by month"),
    "crosstab_heatmap": ("versus", "cross", "breakdown", "split by"),
    "theme_rating_heatmap": ("review", "rating", "complain", "theme", "words"),
    "correlation_heatmap": ("relationship", "correlat", "related", "together"),
    "group_ranges": ("compare", "best", "worst", "differ", "group"),
    "pareto": ("top", "concentrat", "80/20", "pareto", "biggest", "largest"),
    "slope": ("before", "after", "shift", "gap", "overtook"),
}

# the reasons a chart is refused (spec.json plan_directive.refusal_reasons; the limit's why and a few specific
# conditions are added where they say more)
R_MENU = "not on the chart menu"
R_ARGS = "the wrong number of columns for %s"
R_MISSING = "a column the file does not have"
R_PERSONAL = "a personal column is never charted"
R_HALF = "fewer than half its cells rest on 5 or more rows"
R_RECON = "could not be reconciled with the engine's own figures"
R_HEAT = "at most 3 heatmaps"
R_MAX = "at most 8 charts"
R_TWICE = "the same chart twice"
R_SIZE = "too large to draw"


class Refused(Exception):
    """A chart that cannot be drawn from this file, with the reason in the engine's words."""


def blank() -> Dict[str, Any]:
    """rep["viz"] with nothing built (what blank_report carries)."""
    return {"version": VERSION, "charts": [], "refused": [], "chosen_by": "none"}


# ----------------------------------------------------------------------------- numbers and texts
def _r6(x: Any) -> float:
    return round(float(x), 6)


def _tidy(x: Any) -> Any:
    """x with every float finite and a whole-number float written as an integer (100, never 100.0), as
    JSON.stringify writes it (spec caps_notes.CHART_BYTES)."""
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError("a figure that is not finite")
        return int(x) if x.is_integer() and abs(x) < 2 ** 53 else x
    if isinstance(x, dict):
        return {k: _tidy(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_tidy(v) for v in x]
    try:                                                     # a numpy scalar
        return _tidy(x.item())
    except AttributeError:
        return x


def record_bytes(rec: Dict[str, Any]) -> int:
    """A record's size as JSON.stringify writes it, in UTF-8 bytes."""
    import json
    return len(json.dumps(_tidy(rec), separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def bytes_cap(kind: str) -> int:
    return HEATMAP_BYTES if kind == "heatmap" else CHART_BYTES


def _count(n: Any) -> str:
    return format(int(n), ",")


def _fmt(v: Any) -> str:
    return NB._fmt(v)


def _chg(v: Any, unit: str = "") -> str:
    """A change: "+16,220", "−0.648%" (unit "%")."""
    return NS._fmt_item(v, "change", unit)


def _pct_share(v: Any) -> str:
    """A running share: "4.5%", "100.0%" (two decimals under 1%)."""
    return NB._pct_text(float(v)).replace("-", MINUS)


_COMPACT = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k"))


def _compact(v: Any) -> str:
    """A large figure in at most 12 characters: 4 significant digits and T, B, M or k ("1.235B", "456.7M",
    "−12.35k")."""
    v = float(v)
    a = abs(v)
    for i, (d, suf) in enumerate(_COMPACT):
        if a >= d:
            s = NB._fmt(v / d, 4)
            if i and abs(float(s.replace("−", "-").replace(",", ""))) >= 1000:
                d, suf = _COMPACT[i - 1]                   # 999.97M reads 1,000M: one unit up
                s = NB._fmt(v / d, 4)
            return s + suf
    return NB._fmt(v, 4)


def _cell_fmt(base: Callable[[Any], str], values: Sequence[Any]) -> Callable[[Any], str]:
    """A heatmap's cell format: `base` when every value's text fits a cell (CELL_TEXT_MAX), else the compact form for
    every cell and the legend (review of the chart registry, 30 Sep 2026: a calendar of totals in the billions was
    refused "a figure too long to print in a cell")."""
    return base if all(len(base(v)) <= CELL_TEXT_MAX for v in values if v is not None) else _compact


def _signed_r(v: float) -> str:
    """A correlation: "+0.453", "−0.12", "0"."""
    s = _fmt(v)
    return s if v <= 0 else "+" + s


def _cut(s: Any, n: int) -> str:
    """s at most n characters, cut after a whole word (an ellipsis marks the cut)."""
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else NB._cut_words(s, n)


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return "%s %s" % (_count(n), one if n == 1 else (many or one + "s"))


def _pctl(xs: Sequence[float], p: float) -> float:
    """numpy's 'linear' percentile."""
    s = sorted(float(x) for x in xs)
    pos = (len(s) - 1) * p / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _close(a: float, b: float, scale: float = 0.0) -> bool:
    return abs(float(a) - float(b)) <= RECONCILE_TOL * max(1.0, abs(float(b)), abs(float(scale)))


def _mon(ym: str) -> str:
    return "%s %s" % (MONTHS[int(ym[5:7]) - 1], ym[:4])


# ----------------------------------------------------------------------------- tiers and legends (spec.json "tiers")
def _seq_tiers(values: List[List[Optional[float]]]) -> Tuple[List[List[int]], Optional[Tuple[float, ...]]]:
    """Sequential: tertiles of the shown values; tier 1 when value <= q1, 2 when <= q2, else 3."""
    shown = [v for row in values for v in row if v is not None]
    if not shown:
        return [[0 for _v in row] for row in values], None
    q1, q2 = _pctl(shown, 100 / 3.0), _pctl(shown, 200 / 3.0)
    tier = [[0 if v is None else (1 if v <= q1 else 2 if v <= q2 else 3) for v in row] for row in values]
    return tier, (min(shown), q1, q2, max(shown))


def _div_tiers(values: List[List[Optional[float]]]) -> Tuple[List[List[int]], Optional[Tuple[float, ...]]]:
    """Diverging: tertiles of |value| over the shown non-zero cells, one scale for both signs; 0 is tier 0. The bounds:
    (q1, q2, the largest |value|, the largest fall, the largest rise)."""
    mags = [abs(v) for row in values for v in row if v is not None and v != 0]
    if not mags:
        return [[0 for _v in row] for row in values], None
    q1, q2 = _pctl(mags, 100 / 3.0), _pctl(mags, 200 / 3.0)
    falls = [-v for row in values for v in row if v is not None and v < 0]
    rises = [v for row in values for v in row if v is not None and v > 0]

    def t(v: Optional[float]) -> int:
        if v is None or v == 0:
            return 0
        k = 1 if abs(v) <= q1 else 2 if abs(v) <= q2 else 3
        return k if v > 0 else -k
    return [[t(v) for v in row] for row in values], (q1, q2, max(mags), max(falls) if falls else 0.0,
                                                     max(rises) if rises else 0.0)


def _corr_tier(r: float) -> int:
    """Correlation: fixed cut points of |r| (below 0.3, 0.3 to below 0.6, 0.6 and over)."""
    if r == 0:
        return 0
    k = 1 if abs(r) < 0.3 else 2 if abs(r) < 0.6 else 3
    return k if r > 0 else -k


def _used(tier: List[List[int]]) -> List[int]:
    return sorted({t for row in tier for t in row if t})


def _seq_legend(tier: List[List[int]], bounds: Optional[Tuple[float, ...]], fmt: Callable[[float], str]
                ) -> List[Dict[str, Any]]:
    if bounds is None:
        return []
    lo, q1, q2, hi = bounds
    txt = {1: "%s to %s" % (fmt(lo), fmt(q1)), 2: "%s to %s" % (fmt(q1), fmt(q2)), 3: "%s to %s" % (fmt(q2), fmt(hi))}
    return [{"tier": t, "text": txt[t][:40]} for t in _used(tier)]


def _div_legend(tier: List[List[int]], bounds: Optional[Tuple[float, ...]], fmt: Callable[[float], str],
                zero: str) -> List[Dict[str, Any]]:
    """Each tier used, its range on its own side of 0: the falls run down to the largest fall and the rises up to the
    largest rise (review of the chart registry, 30 Sep 2026: both ends read the largest |value|, so the falls' last
    tier said -2.73% where the largest fall was -2.2%)."""
    if bounds is None:
        return []
    q1, q2, _mx, fall, rise = bounds
    inner, outer = {1: None, 2: q1, 3: q2}, {1: q1, 2: q2}

    def far(k: int, m: float) -> float:
        return min(outer.get(k, m), m)
    txt = {}
    for k in (1, 2, 3):
        txt[-k] = "%s to %s" % (fmt(-far(k, fall)), zero if inner[k] is None else fmt(-inner[k]))
        txt[k] = "%s to %s" % (zero if inner[k] is None else fmt(inner[k]), fmt(far(k, rise)))
    return [{"tier": t, "text": txt[t][:40]} for t in _used(tier)]


_CORR_LEGEND = {-3: "−1 to −0.6", -2: "−0.6 to −0.3", -1: "−0.3 to 0", 1: "0 to +0.3",
                2: "+0.3 to +0.6", 3: "+0.6 to +1"}


def _grid_table(row_label: str, rows: List[str], cols: List[str], text: List[List[str]]) -> Dict[str, Any]:
    return {"cols": [row_label] + list(cols), "rows": [[rows[i]] + list(text[i]) for i in range(len(rows))]}


# ----------------------------------------------------------------------------- the rows a chart reads
class Ctx:
    """What every chart reads: the rows the engine kept exactly as the visitor downloads them (downloads.clean_csv,
    text cells, the frame the engine's own charts are computed from), without EVERY flagged column (withheld, coded
    or kept), the engine's date column and each row's month, the plan's column entries by landed name, the engine's
    claims, its charts, and the AI's analyses. One exception, option B (owner, 1 Oct 2026: the AI may read the personal
    columns the visitor keeps): a column the scan flagged as free text, which the visitor kept and agreed to send to
    the AI (the page's ticked box; an AI plan runs only after it), may be the theme chart's text, and nothing else in
    any chart (kept_text)."""

    def __init__(self, rep: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        self.rep = rep
        self.reading = ctx.get("reading")
        plan = ctx.get("plan")
        self.plan = plan if isinstance(plan, dict) else None
        self.pub: Callable[[Any], Any] = ctx.get("pub") or (lambda x: x)
        self.goal = str(ctx.get("goal") or "")
        self.flagged = [f for f in (rep.get("privacy") or {}).get("flagged") or [] if isinstance(f, dict)]
        self.flag_land = {str(f.get("column")) for f in self.flagged if f.get("column")}
        self.withheld_land = {str(f.get("column")) for f in self.flagged if f.get("decision") == "withhold"}
        self.frame = NB._download_frame((rep.get("downloads") or {}).get("clean_csv") or "", self.flag_land)
        cols = list(self.frame.columns) if self.frame is not None else []
        self.columns = cols
        roles = rep.get("roles") or {}
        d = roles.get("date")
        self.date = d if d and d in cols and d not in self.flag_land else None
        self.month = NB._months_of(self.frame[self.date]) if self.date else None
        self.dims = [str(x) for x in roles.get("dimensions") or [] if x in cols and x not in self.flag_land]
        self.measures = [str(x) for x in roles.get("measures") or [] if x in cols and x not in self.flag_land]
        self.plan_by = NS._plan_entries(self.plan, self.reading.landed if self.reading is not None else None) \
            if self.plan else {}
        try:
            from northledger.measure import additive_kind
            self.additive: Callable[[Any], str] = additive_kind
        except ImportError:                                       # the engine is always there in a run
            self.additive = lambda _n: ""
        self.charts = {str(c.get("id")): c for c in rep.get("charts") or [] if isinstance(c, dict)}
        self.findings = {str(f.get("id")): f for f in rep.get("findings") or [] if isinstance(f, dict)}
        self.analyses = [a for a in (rep.get("ai_analyses") or {}).get("items") or [] if isinstance(a, dict)]
        self.sc = rep.get("scenarios") if isinstance(rep.get("scenarios"), dict) else {}
        self.claims = dict(ctx.get("claims") or {})
        # the exact tokens of every flagged column's values (withheld, coded or kept: Scrubber.flag_tokens); a level
        # that holds one is never printed (_label_ok) and no theme word is one
        self.names = frozenset(ctx.get("names") or ())
        # the same tokens by landed column (Scrubber.flag_tokens_by): a kept text's own words are its themes, so the
        # theme chart reads every OTHER flagged column's tokens (names_but), as the themes analysis does
        self.names_by = {str(k): frozenset(v) for k, v in dict(ctx.get("names_by") or {}).items()}
        # option B: the flagged columns the scan read as free text that the visitor kept, when an AI plan ran (the
        # page runs one only after the box naming them is ticked); read from the download only for the theme chart
        self.kept_free = {str(f.get("column")) for f in self.flagged if f.get("decision") == "keep"
                          and str(f.get("kind") or "") == "free text"} if self.plan else set()
        self._kept_frame: Any = None
        # the words of a withheld column's name: a theme word that is one is left out, so a record never names it
        self.withheld_words = frozenset(w for c in self.withheld_land for src in (c, self.header(c))
                                        for w in NB._theme_tokens(src) if w)
        self._num: Dict[str, Any] = {}
        self._txt: Dict[str, Any] = {}
        self.lim_detail: Dict[str, Any] = {}      # run_limits: the columns the chart limits read and count (limits)

    # -- names
    def header(self, land: str) -> str:
        """A landed column as the visitor's file names it."""
        return self.reading.header(land) if self.reading is not None else land

    def landed(self, name: Any) -> Optional[str]:
        n = str(name)
        if self.reading is not None:
            got = self.reading.landed(n)
            if got is not None:
                return got
        if n in self.columns:
            return n
        s = NB._engine_slug(n)
        return s if s in self.columns else None

    def is_flagged(self, name: Any) -> bool:
        n = str(name)
        land = self.landed(n)
        return any(k in self.flag_land for k in (n, land, NB._engine_slug(n)) if k)

    def is_withheld(self, name: Any) -> bool:
        n = str(name)
        land = self.landed(n)
        return any(k in self.withheld_land for k in (n, land, NB._engine_slug(n)) if k)

    # -- values
    def nums(self, land: str) -> Any:
        if land not in self._num:
            self._num[land] = NB._numbers_of(self.frame[land])
        return self._num[land]

    def texts(self, land: str) -> Any:
        if land not in self._txt:
            src = self.frame if land in self.frame.columns else self.kept_frame()
            self._txt[land] = src[land].astype(str).str.strip()
        return self._txt[land]

    # -- option B: a kept free-text column, for the theme chart's text only
    def kept_frame(self) -> Any:
        """The download with the kept free-text columns (kept_free) too, row for row the frame; None without one."""
        if self._kept_frame is None and self.kept_free:
            self._kept_frame = NB._download_frame((self.rep.get("downloads") or {}).get("clean_csv") or "",
                                                  self.flag_land - self.kept_free)
        return self._kept_frame

    def kept_text(self, name: Any) -> Optional[str]:
        """The landed name of `name` when it is a kept free-text column the theme chart may read (kept_free, in the
        download, its values not people's names), else None."""
        if not self.kept_free:
            return None
        n = str(name)
        keys = [n, NB._engine_slug(n)] + ([self.reading.landed(n)] if self.reading is not None else [])
        land = next((k for k in keys if k and k in self.kept_free), None)
        kf = self.kept_frame()
        if land is None or kf is None or land not in kf.columns or len(kf) != len(self.frame):
            return None
        t = self.texts(land)
        if NB._looks_like_names(t[t != ""].unique().tolist()[:2000], self.header(land)):
            return None
        return land

    def names_but(self, land: str) -> FrozenSet[str]:
        """The tokens of every flagged column's values but those of `land` (the file's name for it or its landed one):
        what the themes analysis leaves out when it reads `land` (nl_browser._names_but)."""
        if not self.names_by:
            return self.names
        own = {land, self.header(land), NB._engine_slug(self.header(land))}
        return frozenset().union(*[v for k, v in self.names_by.items() if k not in own])

    def ptype(self, land: str) -> str:
        return str((self.plan_by.get(land) or {}).get("semantic_type") or "")

    def prole(self, land: str) -> str:
        return str((self.plan_by.get(land) or {}).get("role") or "")

    def unit(self, land: Optional[str]) -> str:
        if not land:
            return ""
        return "%" if self.ptype(land) == "percentage" else str((self.plan_by.get(land) or {}).get("unit") or "")[:20]

    def numeric(self, land: str) -> bool:
        """The engine reads the column as numbers: every filled cell of the kept rows is one."""
        if land not in self.columns or land == self.date:
            return False
        t = self.texts(land)
        filled = t != ""
        return bool(filled.any()) and bool(self.nums(land)[filled].notna().all())

    def is_flow(self, land: Optional[str]) -> bool:
        """A flow or a count is added up; anything else is averaged (the plan's type, else the engine's own
        additive kind from the column's name)."""
        if land is None:
            return True                                       # rows are counted
        st = self.ptype(land)
        if st:
            return st in TOTAL_TYPES
        return self.additive(land) in ("money", "units")

    def levels_all(self, land: str) -> List[str]:
        """The distinct filled values of a column over the kept rows."""
        t = self.texts(land)
        return [str(x) for x in t[t != ""].unique()]

    def levels(self, land: str, measure: Optional[str] = None) -> List[Tuple[str, int]]:
        """(level, rows) of a category over the kept rows, blanks left out, largest first (WAVE 4: segments by size, not
        by how many rows they have): with a measure, by the sum of |measure| over the latest 12 months for a flow or a
        count, or rows x |mean| for a level; then by rows; then by name. Without one (a chart that counts rows), by rows."""
        t = self.texts(land)
        filled = t != ""
        vc = t[filled].value_counts()
        size: Dict[str, float] = {}
        if measure is not None and measure in self.columns and measure != land:
            v = self.nums(measure)
            ok = filled & v.notna()
            if self.is_flow(measure):
                if self.month is not None:
                    ms = sorted(m for m in self.month.dropna().unique())
                    if ms:
                        ok = ok & (self.month >= NB._shift_month(ms[-1], -11)) & self.month.notna()
                size = {str(k): float(x) for k, x in v[ok].abs().groupby(t[ok]).sum().items()}
            else:
                g = v[ok].groupby(t[ok])
                size = {str(k): float(n) * abs(float(mu)) for k, n, mu in zip(g.count().index, g.count().values,
                                                                              g.mean().values)}
        return sorted(((str(k), int(v)) for k, v in vc.items()), key=lambda kv: (-size.get(kv[0], 0.0), -kv[1], kv[0]))

    def rating_levels(self, land: str) -> Optional[List[float]]:
        """The whole-number levels of a rating column (2 to 10 of them), in their order, or None."""
        if not self.numeric(land):
            return None
        v = self.nums(land).dropna()
        if not len(v) or not bool((v % 1 == 0).all()):
            return None
        lv = sorted(set(float(x) for x in v.tolist()))
        return lv if RATING_LEVELS[0] <= len(lv) <= RATING_LEVELS[1] else None

    def free_text(self, land: str) -> bool:
        """Free text: the plan types it free_text, or (no plan entry) its values are too many or too long for a
        category (the profile's rule: more than 300 distinct values, or a median of more than 60 characters)."""
        if land in self.kept_free:
            if not (self.plan_by.get(land) or {}).get("semantic_type") in (None, "", "free_text"):
                return False
        elif land not in self.columns or self.numeric(land) or land == self.date:
            return False
        st = self.ptype(land)
        if st:
            return st == "free_text"
        t = self.texts(land)
        f = t[t != ""]
        return len(f) >= THEME_MIN_TEXTS and (int(f.nunique()) > 300 or float(f.str.len().median()) > 60)

    # -- roles (spec.json "roles")
    def role_ok(self, land: str, role: str) -> str:
        """"" when the column can play the role, else why not (in words that name only an unflagged column)."""
        h = self.header(land)
        st, pr = self.ptype(land), self.prole(land)
        if role in ("measure", "total", "units"):
            if not self.numeric(land) or (st and st not in MEASURE_TYPES):
                return "%s is not a number column the engine reads" % h if not self.numeric(land) else \
                    "%s is not a measure (the plan reads it as %s)" % (h, st)
            if role == "total" and not self.is_flow(land):
                return "%s is not a total (a flow or a count); it is averaged" % h
            if role == "units" and not ((st == "count") if st else self.additive(land) == "units"):
                return "%s is not a units column (a count)" % h
            return ""
        if role in ("segment", "category", "category_or_rating"):
            if role == "category_or_rating" and self.rating_levels(land) is not None and \
                    (not st or st in RATING_TYPES):
                return ""
            if self.numeric(land) or land == self.date:
                return "%s is not a category column" % h
            types = SEGMENT_TYPES if role == "segment" else CATEGORY_TYPES
            if st or pr:
                if st in ("identifier", "code", "free_text", "metadata", "date", "year") or pr in NOT_A_CATEGORY_ROLE:
                    return "%s is not a category (the plan reads it as %s)" % (h, st or pr)
                if st and st not in types and pr not in ("segment", "geography"):
                    return "%s is not a category (the plan reads it as %s)" % (h, st)
                return ""
            if land in self.dims:
                return ""
            n = len(self.levels_all(land))
            if n > 300:
                return ("%s has %s values; past 300, a column is a category only when the plan types it a category or an "
                        "entity" % (h, _count(n)))
            return "%s is not a category the engine reads" % h
        if role == "text":
            return "" if self.free_text(land) else "%s is not a free-text column" % h
        if role == "rating":
            if st and st not in RATING_TYPES:
                return "%s is not a rating (the plan reads it as %s)" % (h, st)
            return "" if self.rating_levels(land) is not None else \
                "%s is not a rating of 2 to 10 whole-number levels" % h
        return "%s cannot play that part" % h

    # -- the engine's own figures
    def primary_measure(self) -> Tuple[bool, Optional[str]]:
        """(is there one, the primary claim's measure: its column, None for rows)."""
        key = str((self.rep.get("primary_metric") or {}).get("claim_key") or "")
        if not key:
            return False, None
        if key == "volume":
            return True, None
        if key.startswith("total:"):
            m = key.split(":")[1]
            return (True, m) if m in self.columns else (False, None)
        return (True, key) if key in self.columns else (False, None)

    def plan_primary(self) -> Optional[str]:
        name = str((self.plan or {}).get("primary") or "")
        return self.landed(name) if name else None


def claims_of(r: Any) -> Dict[str, Dict[str, Any]]:
    """{finding id: the claim's key and conditions}, from the engine's gated claims (as nl_browser._scenarios)."""
    out: Dict[str, Dict[str, Any]] = {}
    for g in getattr(r, "gated", None) or []:
        fact = getattr(g, "fact", None)
        key = getattr(fact, "claim_key", None) if fact is not None else None
        if not key:
            continue
        t = getattr(fact, "test", None) or {}
        out[str(fact.id)] = {"key": str(key), "total_of": t.get("total_of"), "kind_split": t.get("kind_split"),
                             "currency": t.get("currency"), "status_excluded": t.get("status_excluded"),
                             "like_for_like_of": t.get("like_for_like_of")}
    return out


# ----------------------------------------------------------------------------- the planner's limits
def profile_stats(R: Any, facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the limits need beyond the profile's facts, from the engine's reading over the rows it kept: for the
    column read as dates with the most dates, each short text column's rows by level and month. Never leaves the
    adapter (the limits say only counts and the names of columns the planner may see)."""
    import numpy as np
    out: Dict[str, Any] = {"day": None, "months": {}, "cols": {}}
    if R is None:
        return out
    kept = np.asarray(R.kept, bool)
    day, n_day = None, 0
    for f in facts:
        if f.get("kind") == "date":
            n = int(R.dates(f["landed"])[kept].notna().sum())
            if n > n_day:
                day, n_day = f["landed"], n
    if day is None:
        return out
    mon = R.dates(day)[kept].dt.strftime("%Y-%m")
    out["day"] = day
    out["months"] = {str(k): int(v) for k, v in mon.value_counts().items()}
    for f in facts:
        land = f["landed"]
        if land == day or f.get("kind") != "text" or "top_values" not in f or not 2 <= int(f.get("distinct") or 0) <= 300:
            continue
        t = R.texts[land][kept].astype(str).str.strip()
        ok = (t != "") & mon.notna()
        g = {}
        for (lv, mo), n in t[ok].groupby([t[ok], mon[ok]]).size().items():
            g.setdefault(str(lv), {})[str(mo)] = int(n)
        out["cols"][land] = g
    return out


def _windows_of(last: str) -> Tuple[List[str], List[str]]:
    """(the 12 months before, the latest 12 months) ending with the month `last`."""
    lm = [NB._shift_month(last, -k) for k in range(11, -1, -1)]
    return [NB._shift_month(m, -12) for m in lm], lm


def limits(facts: List[Dict[str, Any]], priv: Dict[str, Any], time: Optional[Dict[str, Any]], stats: Any,
           rows: int, plan_by: Optional[Dict[str, Dict[str, Any]]] = None, additive: Any = None,
           detail: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """profile.chart_limits: [{chart, ok, why}] in menu order (at most CHART_LIMITS_MAX; why at most 160 code
    points: the chart's limit_needs, "; ", what this file has), over the rows the engine kept. Every flagged column
    (withheld, coded or kept) is skipped before anything is chosen: never named, never counted. ok true is necessary,
    not sufficient: the build may still refuse. plan_by (the plan's entries by landed name, in a run) lets the plan's
    types count (a column typed free_text is text, one typed rating a rating). detail (a run's own dict, never the
    profile's) receives the columns the limits read ("usable": each fact by landed name) and the ones they count as
    categories ("cats", landed names in order), so a refusal can say why a column the AI named is not one
    (_pareto_refusal)."""
    if additive is None:
        try:
            from northledger.measure import additive_kind as additive
        except ImportError:
            additive = lambda _n: ""  # noqa: E731
    plan_by = plan_by or {}
    stats = stats if isinstance(stats, dict) else {}
    usable = [f for f in facts if f["header"] not in priv]
    tcol = time.get("column") if isinstance(time, dict) else None
    tfact = next((f for f in usable if f["header"] == tcol), None)
    tland = tfact["landed"] if tfact is not None else None
    if tfact is None or (tfact.get("time") or {}).get("year_column") or tfact.get("kind") != "date":
        time, tcol, tland = None, None, None    # no date column (a year column has no months), or a flagged one
    months = int(time.get("months") or 0) if isinstance(time, dict) else 0
    years = int(time.get("distinct_years") or 0) if isinstance(time, dict) else 0
    mstats = (stats.get("cols") or {}) if (tland and stats.get("day") == tland) else {}
    all_months = NB._month_range(time["first"], time["last"]) if isinstance(time, dict) else []

    def st(f: Dict[str, Any]) -> str:
        return str((plan_by.get(f["landed"]) or {}).get("semantic_type") or "")

    def is_rating(f: Dict[str, Any]) -> bool:
        return f.get("kind") == "number" and bool(f.get("integers")) and \
            RATING_LEVELS[0] <= int(f.get("distinct") or 0) <= RATING_LEVELS[1] and (not st(f) or st(f) in RATING_TYPES)
    numeric = [f for f in usable if f.get("kind") == "number" and f["landed"] != tland and
               (not st(f) or st(f) in MEASURE_TYPES)]
    cats = [f for f in usable if f.get("kind") == "text" and f["landed"] != tland and "top_values" in f
            and int(f.get("distinct") or 0) >= 2 and not f.get("personal_shape")
            and st(f) not in ("identifier", "code", "free_text", "metadata", "date", "year")]
    texts = [f for f in usable if f.get("kind") == "text" and f["landed"] != tland and not f.get("personal_shape")
             and int(f.get("filled") or 0) >= THEME_MIN_TEXTS
             and (st(f) == "free_text" if st(f) else "top_values" not in f)]
    # option B (Ctx.kept_text): a column the scan flagged as free text that the visitor kept may be the theme chart's
    # text (the profile names a kept column already); listed after the unflagged ones, never counted for another chart
    texts += [f for f in facts if (priv.get(f["header"]) or ("", ""))[0] == "keep" and
              (priv.get(f["header"]) or ("", ""))[1] == "free text" and f.get("kind") == "text" and f["landed"] != tland
              and int(f.get("filled") or 0) >= THEME_MIN_TEXTS and st(f) in ("", "free_text")]
    ratings = [f for f in usable if is_rating(f)]
    if detail is not None:
        detail.update(usable={f["landed"]: f for f in usable}, cats=[f["landed"] for f in cats])
    span = ("the file spans %s months (%s to %s)" % (_count(months), time["first"], time["last"])) if tcol \
        else "no column the engine reads as dates"
    out: List[Dict[str, Any]] = []

    def add(chart: str, ok: bool, has: str) -> None:
        why = REGISTRY[chart]["limit_needs"] + "; " + has
        out.append({"chart": chart, "ok": bool(ok), "why": NB._cut160(why)})

    # the category a waterfall or a slope can use: 2 to 12 levels over the two windows, 2 with 5 rows in each
    seg_has, seg_ok = "no category column of 2 to 12 levels", False
    if tcol and months >= 24:
        pm, lm = _windows_of(time["last"])
        best = None
        for f in cats:
            lv = mstats.get(f["landed"]) or {}
            n = {g: (sum(c.get(m, 0) for m in pm), sum(c.get(m, 0) for m in lm)) for g, c in lv.items()}
            present = [g for g, (a, b) in n.items() if a or b]
            both = [g for g, (a, b) in n.items() if a >= SMALL_CELL and b >= SMALL_CELL]
            good = SEGMENT_LEVELS[0] <= len(present) <= SEGMENT_LEVELS[1] and len(both) >= 2
            if good:
                best = (f, len(present))
                break
            if best is None and present:
                seg_has = ("%s has %s" % (f["header"], _plural(len(present), "level")) if len(present) > 12 or
                           len(present) < 2 else "%s has %s with 5 rows a window" % (f["header"], _plural(len(both), "level")))
        if best is not None:
            seg_ok = True
            seg_has = "%s months, %s has %s" % (_count(months), best[0]["header"], _plural(best[1], "level"))
    else:
        seg_has = span
    add("contribution_waterfall", seg_ok, seg_has)

    # price, volume and mix: a money total and a units column filled on every row, none negative
    money = [f for f in numeric if (st(f) == "flow_amount" if st(f) else additive(f["landed"]) == "money")]
    units = [f for f in numeric if (st(f) == "count" if st(f) else additive(f["landed"]) == "units")
             and int(f.get("filled") or 0) >= int(rows or 0) and float(f.get("min", 0) or 0) >= 0]
    pvm_ok = bool(tcol) and months >= 24 and bool(money) and bool(units)
    if not tcol or months < 24:
        pvm_has = span
    elif not money:
        pvm_has = "no money total (a column named like revenue, sales or amount)"
    elif not units:
        pvm_has = "no units column filled on every row"
    else:
        pvm_has = "%s and %s qualify" % (money[0]["header"], units[0]["header"])
    add("pvm_waterfall", pvm_ok, pvm_has)

    add("calendar_heatmap", bool(tcol) and months >= CALENDAR_MIN_MONTHS and years >= 2, span)

    # the change heatmap: half the category's level-months with 5 or more rows, over 13 or more months
    ch_ok, ch_has = False, span if not tcol or months < CHANGE_MIN_MONTHS else "no category column of 2 to 12 levels"
    if tcol and months >= CHANGE_MIN_MONTHS:
        first = None
        for f in cats:
            lv = mstats.get(f["landed"]) or {}
            if not lv:
                continue
            top = sorted(lv, key=lambda g: (-sum(lv[g].values()), g))
            keys = top[:CROSS_ROWS - 1] + ([OTHER] if len(top) >= CROSS_ROWS else [])
            cnt = {g: lv[g] for g in top[:CROSS_ROWS - 1]}
            if len(top) >= CROSS_ROWS:
                o: Dict[str, int] = {}
                for g in top[CROSS_ROWS - 1:]:
                    for m, n in lv[g].items():
                        o[m] = o.get(m, 0) + n
                cnt[OTHER] = o
            total = len(keys) * len(all_months)
            good = sum(1 for g in keys for m in all_months if cnt[g].get(m, 0) >= SMALL_CELL)
            if len(keys) >= 2 and total and 2 * good >= total:
                ch_ok, ch_has = True, "%s has %s of %s" % (f["header"], _count(good), _count(total))
                break
            if first is None and len(keys) >= 2:
                first = "%s has %s of %s" % (f["header"], _count(good), _count(total))
        if not ch_ok and first:
            ch_has = first
    add("change_heatmap", ch_ok, ch_has)

    pool = cats + [f for f in ratings if f not in cats]
    names = [f["header"] for f in pool]
    add("crosstab_heatmap", len(pool) >= 2,
        "the file has %s" % ("none" if not pool else "one (%s)" % names[0] if len(pool) == 1 else
                             "%s (%s)" % (_count(len(pool)), ", ".join(names[:4]) + (", ..." if len(pool) > 4 else ""))))
    th_ok = bool(texts) and bool(ratings)
    th_has = ("no free-text column" if not texts else "no rating column of 2 to 10 whole-number levels" if not ratings
              else "%s and %s qualify" % (texts[0]["header"], ratings[0]["header"]))
    add("theme_rating_heatmap", th_ok, th_has)
    nn = [f["header"] for f in numeric]
    add("correlation_heatmap", CORR_MEASURES[0] <= len(numeric) and int(rows or 0) >= CORR_MIN_ROWS,
        "the file has %s%s" % (_count(len(nn)), (" (%s)" % ", ".join(nn[:4]) + ("" if len(nn) <= 4 else ", ...")) if nn else ""))
    grp = sorted(((int(f.get("groups") or 0), i, f) for i, f in enumerate(cats)), key=lambda x: (-x[0], x[1]))
    gr_ok = bool(numeric) and bool(grp) and grp[0][0] >= 2
    gr_has = ("no number column" if not numeric else "no category column" if not grp else
              "%s has %s" % (grp[0][2]["header"], _plural(grp[0][0], "such group")))
    add("group_ranges", gr_ok, gr_has)
    big = sorted(((int(f.get("distinct") or 0), i, f) for i, f in enumerate(cats)), key=lambda x: (-x[0], x[1]))
    # past the 300 values the profile lists, a column of short values counts as a Pareto's category up to 5,000 levels
    # when the plan types it a category, an entity or a place (review of the chart registry, 30 Sep 2026: a brand column
    # of 3,401 values typed entity was refused); in the planner's profile, before any plan, when the plan may type it so
    wide = [f for f in usable if f.get("kind") == "text" and f["landed"] != tland and not f.get("personal_shape")
            and "top_values" not in f and f.get("short") and PARETO_MIN_LEVELS <= int(f.get("distinct") or 0) <= PARETO_MAX_LEVELS
            and (st(f) in PARETO_TYPES or (not plan_by and not st(f)))]
    if detail is not None:
        detail["wide"] = [f["landed"] for f in wide]
    pbig = [x for x in big if x[0] <= PARETO_MAX_LEVELS]
    if wide and (not pbig or pbig[0][0] < PARETO_MIN_LEVELS):
        w0 = max(wide, key=lambda f: int(f.get("distinct") or 0))
        add("pareto", True, "%s has %s levels%s" % (w0["header"], _count(int(w0.get("distinct") or 0)),
                                                    "" if plan_by else ", a category when the plan types it one"))
    else:
        add("pareto", bool(big) and big[0][0] >= PARETO_MIN_LEVELS,
            "no category column" if not big else "%s has %s" % (big[0][2]["header"], _count(big[0][0])))
    sl_has = seg_has
    if seg_ok:
        sl_has = seg_has.split(", ", 1)[1] if ", " in seg_has else seg_has
    add("slope", seg_ok, sl_has)
    return out[:CHART_LIMITS_MAX]


# ----------------------------------------------------------------------------- the record
def _record(chart: str, title: str, subtitle: str, section: str, supports: str, anchors: List[str],
            grade: Optional[str], parent_grade: Optional[str], measure: Dict[str, Any], data: Dict[str, Any],
            table: Dict[str, Any], summary: str, suppressed: Tuple[int, str], source: str,
            inputs: Dict[str, Any]) -> Dict[str, Any]:
    """A record in the spec's key order; id, chosen_by and why are set when it takes its place."""
    anc = list(dict.fromkeys(a for a in anchors if a))[:ANCHORS_MAX]
    return {"id": "", "chart": chart, "kind": REGISTRY[chart]["kind"], "title": _cut(title, 120),
            "subtitle": _cut(subtitle, 160), "section": section, "supports": _cut(supports, 200), "anchors": anc,
            "grade": grade, "parent_grade": parent_grade if grade is None else None, "chosen_by": "engine", "why": "",
            "measure": {"label": _cut(measure.get("label") or "rows", 80), "unit": str(measure.get("unit") or "")[:20],
                        "kind": measure.get("kind") or "count"},
            "data": data, "table": {"cols": [_cut(c, 80) for c in table["cols"]][:TABLE_COLS_MAX],
                                    "rows": [[str(x)[:80] for x in r][:TABLE_COLS_MAX] for r in table["rows"]][:TABLE_ROWS_MAX]},
            "summary": _cut(summary, 400) or _cut(title, 400), "suppressed": {"cells": int(suppressed[0]),
                                                                                "why": _cut(suppressed[1], 200)},
            "source": _cut(source, 200), "inputs": {"columns": [str(c)[:120] for c in inputs.get("columns") or [] if c][:12],
                                                    "rows": inputs.get("rows"), "months": inputs.get("months"),
                                                    "op": _cut(inputs.get("op") or "", 120)}}


def _label_tokens(s: Any) -> FrozenSet[str]:
    """The tokens of a level that could name someone: 2 or more characters with a letter among them (a single letter
    or a number alone identifies no one)."""
    return frozenset(t for t in NB._value_tokens([s]) if len(t) >= 2 and re.search(r"[^\W\d_]", t))


def _label_ok(ctx: Ctx, s: str, level: Optional[str] = None, names: Optional[FrozenSet[str]] = None) -> bool:
    """A level may be printed: 1 to 80 characters, the report's scrubber leaves it as it is (no phone number, email
    address or withheld value in it), and no token of it is a token of a flagged column's values (withheld, coded or
    kept: the exact set, ctx.names; review of the chart registry, 30 Sep 2026: the scrubber looks only for a value of 6
    or more characters, so a first name went through). `level`: the value itself when s decorates it ("East (new)")."""
    if not (0 < len(s) <= LABEL_MAX and ctx.pub(s) == s):
        return False
    nm = ctx.names if names is None else names       # names: the theme chart's own set (Ctx.names_but)
    return not (nm and _label_tokens(s if level is None else level) & nm)


def _names_check(ctx: Ctx, land: str) -> None:
    """A Pareto's or a crosstab's levels are the column's own values: a column whose values look like people's names
    (nl_browser._looks_like_names, the adapter's person-name test) is refused."""
    if land in ctx.columns and NB._looks_like_names(ctx.levels_all(land), ctx.header(land)):
        raise Refused("the values of %s look like people's names" % ctx.header(land))


# ----------------------------------------------------------------------------- from the scenarios block
def _basis(ctx: Ctx) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    sc = ctx.sc or {}
    b = sc.get("basis")
    if not isinstance(b, dict) or not b.get("reconciles") or not sc.get("items"):
        why = next((str(x) for x in sc.get("refused") or []), "")
        raise Refused("the scenarios block has no breakdown of the headline%s" % ((": " + why) if why else ""))
    return b, {str(it.get("id")): it for it in sc.get("items") or [] if isinstance(it, dict)}


def _what(items: Dict[str, Dict[str, Any]], basis: Dict[str, Any]) -> str:
    """The headline measure in the engine's words: "total revenue", "rows"."""
    m = re.match(r"^Change in (.*), the latest 12 months against the 12 before$",
                 str((items.get("headline.change") or {}).get("label") or ""))
    return m.group(1) if m else ("rows" if basis.get("how") == "count" else "total %s" % basis.get("measure"))


def _check_basis_cols(ctx: Ctx, basis: Dict[str, Any], seg: Optional[str], total: Optional[str],
                      need_seg: bool = True) -> None:
    col = (basis.get("segment") or {}).get("column")
    if need_seg and not col:
        raise Refused("the scenarios block found no category to break the change down by")
    if seg is not None and seg != col:
        raise Refused("the scenarios block broke the change down by %s, not %s" % (
            ctx.header(col) if col else "no category", ctx.header(seg)))
    if total is not None:
        if basis.get("how") == "count":
            raise Refused("the headline is a count of rows, so it has no total column")
        if total != basis.get("measure"):
            raise Refused("the headline is the total of %s, not of %s" % (ctx.header(str(basis.get("measure"))),
                                                                         ctx.header(total)))


def _level_rows(ctx: Ctx, basis: Dict[str, Any]) -> Dict[str, Tuple[int, int]]:
    """{level as the scenarios name it (a shown level, or "other"): (the claim's rows in the 12 months before, in
    the latest 12 months)}, recounted from the kept rows by the claim's own conditions (nl_scenarios._claim_mask);
    they must add up to the block's own row counts."""
    import pandas as pd
    seg = (basis.get("segment") or {}).get("column")
    fid = str(basis.get("finding_id") or "")
    claim = ctx.claims.get(fid) or {}
    measure = None if basis.get("how") == "count" else str(basis.get("measure"))
    need = [c for c in [seg, measure] + [str((claim.get(k) or {}).get("column")) for k in
                                         ("kind_split", "currency", "status_excluded") if claim.get(k)] if c]
    if ctx.month is None or any(c not in ctx.columns for c in need):
        raise Refused(R_RECON)
    typed = pd.DataFrame(index=ctx.frame.index)
    for c in dict.fromkeys(need):
        typed[c] = ctx.nums(c) if c == measure else ctx.texts(c).where(ctx.texts(c) != "", None)
    mask, why = NS._claim_mask(typed, claim, measure)
    if why:
        raise Refused(R_RECON)
    pm = NB._month_range(*basis["windows"]["prior"])
    lm = NB._month_range(*basis["windows"]["latest"])
    shown = set(str(x) for x in (basis.get("segment") or {}).get("levels") or [])
    lab = typed[seg].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v).strip())
    grp = lab.where(lab.isin(shown), OTHER)
    out: Dict[str, Tuple[int, int]] = {}
    for w, months in ((0, pm), (1, lm)):
        sel = mask & ctx.month.isin(months)
        for g, n in grp[sel].value_counts().items():
            a, b = out.get(str(g), (0, 0))
            out[str(g)] = (a + int(n), b) if w == 0 else (a, b + int(n))
    rows = basis.get("rows") or {}
    if sum(a for a, _b in out.values()) != int(rows.get("prior") or 0) or \
            sum(b for _a, b in out.values()) != int(rows.get("latest") or 0):
        raise Refused(R_RECON)
    return out


def _segment_parts(ctx: Ctx, basis: Dict[str, Any], items: Dict[str, Dict[str, Any]], cap: int,
                   per_window: bool = False) -> Tuple[List[Dict[str, Any]], Tuple[int, str]]:
    """The segments of the contribution group as parts: [{g, name, status, slug, c (change), a (prior), b (latest),
    pct_text, n0, n1}], every part that rests on fewer than 5 rows folded into "other" (a level in both windows with
    fewer than 5 rows in either, one that entered with fewer than 5 in the latest window, one that left with fewer
    than 5 before, and "other" itself when it rests on fewer than 5 rows: in all, for a waterfall, which prints its
    change; in EACH window it has rows in, per_window, for a slope, which prints both of its totals; review of the chart
    registry, 30 Sep 2026: a slope printed the two totals of an 'other' made of 2 rows before and 3 after); then at most
    `cap` parts, the smallest folded first. Returns (parts in the scenarios' order, (the levels on fewer than 5 rows,
    the record's suppressed.why))."""
    seg = basis["segment"]
    entered = [str(x) for x in seg.get("entered") or []]
    exited = [str(x) for x in seg.get("exited") or []]
    _names_check(ctx, str(seg["column"]))
    rows = _level_rows(ctx, basis)
    parts: List[Dict[str, Any]] = []
    for it in ctx.sc.get("items") or []:
        if it.get("group") != "contribution" or not str(it.get("id")).endswith(".change"):
            continue
        g = str(it.get("segment"))
        slug = str(it["id"])[:-len(".change")]
        pr, la, pc = items.get(slug + ".prior"), items.get(slug + ".latest"), items.get(slug + ".pct")
        if pr is None or la is None:
            raise Refused(R_RECON)
        n0, n1 = rows.get(g, (0, 0))
        status = "other" if g == OTHER else "entered" if g in entered else "exited" if g in exited else "both"
        name = OTHER if g == OTHER else "%s (new)" % g if status == "entered" else "%s (left)" % g if status == "exited" \
            else g
        parts.append({"g": g, "name": name, "status": status, "slug": slug, "c": float(it["value"]),
                      "a": float(pr["value"]), "b": float(la["value"]), "text": str(it.get("text") or ""),
                      "a_text": str(pr.get("text") or ""), "b_text": str(la.get("text") or ""),
                      "pct_text": str((pc or {}).get("text") or ""), "n0": n0, "n1": n1})
    if not parts:
        raise Refused("the scenarios block has no contribution of a segment")
    folded: List[str] = []
    extra = {"other": 0, "cap": 0}

    def small(p: Dict[str, Any]) -> bool:
        if p["status"] == "both":
            return min(p["n0"], p["n1"]) < SMALL_CELL
        if p["status"] == "entered":
            return p["n1"] < SMALL_CELL
        if p["status"] == "exited":
            return p["n0"] < SMALL_CELL
        return False

    def fold(p: Dict[str, Any]) -> None:
        o = next((q for q in parts if q["status"] == "other"), None)
        if o is None:
            o = {"g": OTHER, "name": OTHER, "status": "other", "slug": None, "c": 0.0, "a": 0.0, "b": 0.0,
                 "text": "", "a_text": "", "b_text": "", "pct_text": "", "n0": 0, "n1": 0, "made": True}
            parts.append(o)
        for k in ("c", "a", "b", "n0", "n1"):
            o[k] += p[k]
        o["made"] = True
        folded.append(p["g"])
        parts.remove(p)
    for p in [p for p in parts if small(p)]:
        fold(p)

    def other_small() -> bool:
        o = next((q for q in parts if q["status"] == "other"), None)
        if o is None:
            return False
        if per_window:
            return 0 < o["n0"] < SMALL_CELL or 0 < o["n1"] < SMALL_CELL
        return o["n0"] + o["n1"] < SMALL_CELL
    n_small = len(folded)
    while other_small() or len(parts) > cap:
        cand = sorted((p for p in parts if p["status"] == "both"), key=lambda p: (abs(p["c"]), p["name"]))
        cand = cand or sorted((p for p in parts if p["status"] in ("entered", "exited")),
                              key=lambda p: (abs(p["c"]), p["name"]))
        if not cand:
            raise Refused("every part of the change rests on fewer than 5 rows")
        extra["other" if other_small() else "cap"] += 1
        fold(cand[0])
    unit = str(basis.get("unit") or "")
    for p in parts:
        if p.get("made"):
            p["text"] = _chg(_r6(p["c"]), unit)
            p["a_text"] = NS._fmt_item(_r6(p["a"]), "count" if basis.get("how") == "count" else "amount", unit)
            p["b_text"] = NS._fmt_item(_r6(p["b"]), "count" if basis.get("how") == "count" else "amount", unit)
            p["pct_text"] = _chg(_r6(100.0 * (p["b"] - p["a"]) / p["a"]), "%") if p["a"] > 0 else ""
    for p in parts:
        if not _label_ok(ctx, p["name"], None if p["g"] == OTHER else p["g"]):
            raise Refused("a level too long to print or that reads as personal data")
    n = len([x for x in seg.get("folded") or [] if x != "(blank)"]) + n_small
    why = ""
    if n:
        why = "%s with fewer than 5 rows in a window %s counted only in 'other'" % (
            _plural(n, "level"), "is" if n == 1 else "are")
    if extra["other"]:
        why += "%s%s %s folded into 'other' so that it rests on 5 or more rows%s" % (
            "; " if why else "", _plural(extra["other"], "more level" if n else "level", "more levels" if n else "levels"),
            "is" if extra["other"] == 1 else "are", " in each window" if per_window else "")
    if extra["cap"]:
        why += "%s%s %s folded into 'other' to keep at most %d parts" % (
            "; " if why else "", _plural(extra["cap"], "level"), "is" if extra["cap"] == 1 else "are", cap)
    return parts, (n, why)


def _waterfall_data(prior: Tuple[str, float, str], parts: List[Tuple[str, float, str]],
                    latest: Tuple[str, float, str], change: Tuple[float, str], basis: Dict[str, Any]) -> Dict[str, Any]:
    steps = [{"label": prior[0], "value": _r6(prior[1]), "text": prior[2], "kind": "total", "from": 0.0,
              "to": _r6(prior[1])}]
    run = _r6(prior[1])
    for label, value, text in parts:
        to = _r6(run + _r6(value))
        steps.append({"label": label, "value": _r6(value), "text": text[:24], "kind": "step", "from": run, "to": to})
        run = to
    steps.append({"label": latest[0], "value": _r6(latest[1]), "text": latest[2], "kind": "total", "from": 0.0,
                  "to": _r6(latest[1])})
    return {"steps": steps, "basis": basis, "change": {"value": _r6(change[0]), "text": change[1][:24]}}


def _check_waterfall(data: Dict[str, Any]) -> None:
    """The steps add up (math.fsum) to the change, and the last running total is the latest total, to 1e-6."""
    st = data["steps"]
    prior, latest, change = st[0]["value"], st[-1]["value"], data["change"]["value"]
    tol = RECONCILE_TOL * max(1.0, abs(prior), abs(latest), abs(change)) + 1e-6 * len(st)
    mid = st[1:-1]
    if abs(math.fsum(s["value"] for s in mid) - change) > tol or abs(prior + change - latest) > tol \
            or abs(mid[-1]["to"] - latest) > tol:
        raise Refused(R_RECON)


def _wf_common(ctx: Ctx, basis: Dict[str, Any], items: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    what = _what(items, basis)
    hp, hl, hc = items.get("headline.prior"), items.get("headline.latest"), items.get("headline.change")
    if not hp or not hl or not hc:
        raise Refused(R_RECON)
    measure = None if basis.get("how") == "count" else str(basis.get("measure"))
    return {"what": what, "prior": ("12 months before", float(hp["value"]), str(hp["text"])),
            "latest": ("Latest 12 months", float(hl["value"]), str(hl["text"])),
            "change": (float(hc["value"]), str(hc["text"])), "measure": measure,
            "mlabel": ctx.header(measure) if measure else "rows",
            "months": [basis["windows"]["prior"][0], basis["windows"]["latest"][1]],
            "rows": int((basis.get("rows") or {}).get("prior") or 0) + int((basis.get("rows") or {}).get("latest") or 0),
            "fid": str(basis.get("finding_id") or ""), "grade": basis.get("grade") or None,
            "claim": str((ctx.findings.get(str(basis.get("finding_id"))) or {}).get("claim") or basis.get("claim") or "")}


def _b_contribution(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    basis, items = _basis(ctx)
    _check_basis_cols(ctx, basis, cols[0] if cols else None, cols[1] if cols and len(cols) > 1 else None)
    w = _wf_common(ctx, basis, items)
    seg = basis["segment"]["column"]
    sh = ctx.header(seg)
    parts, supp = _segment_parts(ctx, basis, items, WATERFALL_PARTS_MAX)
    # the invariant's order: levels in both windows by the size of their contribution, then "other", then the levels
    # that entered or left
    order = {"both": 0, "other": 1, "entered": 2, "exited": 2}
    parts.sort(key=lambda p: (order[p["status"]], -abs(p["c"]) if p["status"] != "other" else 0, p["name"]))
    bs = {"split": "segment", "finding_id": w["fid"] or None, "column": sh[:120],
          "prior": list(basis["windows"]["prior"]), "latest": list(basis["windows"]["latest"])}
    data = _waterfall_data(w["prior"], [(p["name"], p["c"], p["text"]) for p in parts], w["latest"], w["change"], bs)
    _check_waterfall(data)
    unit = str(basis.get("unit") or "")
    col_head = NB._cap(w["what"]) + (" (%s)" % unit if unit and unit != "%" else "")
    big = sorted((p for p in parts if p["status"] != "other"), key=lambda p: (-abs(p["c"]), p["name"]))[:2]
    if not big:
        raise Refused("every level of %s rests on fewer than 5 rows in a window" % sh)
    named = " and ".join("%s %s" % (p["name"], p["text"]) for p in big)
    lead = ("The largest contributions: %s" % named) if len(big) > 1 else ("%s contributed %s" % (big[0]["name"],
                                                                                                big[0]["text"]))
    # 'other' is a part too (review of the chart registry, 30 Sep 2026: the summary named two stores' +244 and -58 as
    # "the largest contributions" beside an 'other' of +47,366): when it is the largest, the summary says so first
    oth = next((p for p in parts if p["status"] == "other"), None)
    if oth is not None and abs(oth["c"]) > abs(big[0]["c"]):
        most = abs(oth["c"]) > 0.5 * math.fsum(abs(p["c"]) for p in parts)
        lead = "%s of the change sits in the smaller levels of %s combined ('other', %s); of the levels shown, %s" % (
            "Most" if most else "The largest part", sh, oth["text"],
            ("the largest contributions are %s" % named) if len(big) > 1 else "%s contributed %s" % (
                big[0]["name"], big[0]["text"]))
    summary = ("%s went from %s in the 12 months before to %s in the latest 12 months, a change of %s (part of a "
               "change graded %s; not graded itself). %s. Where the change sits, not what caused it." % (
                   NB._cap(w["what"]), w["prior"][2], w["latest"][2], w["change"][1], w["grade"] or "by the engine",
                   lead))
    if len(summary) > 400:
        summary = ("%s went from %s to %s, a change of %s. Where the change sits, not what caused it." % (
            NB._cap(w["what"]), w["prior"][2], w["latest"][2], w["change"][1]))
    anchors = (["finding:" + w["fid"]] if w["fid"] in ctx.findings else []) + ["scenario:headline.prior"] + \
        ["scenario:%s.change" % p["slug"] for p in parts if p["slug"] and not p.get("made")] + \
        ["scenario:headline.latest", "scenario:headline.change"]
    return _record(
        "contribution_waterfall", "Where the change in %s came from, by %s" % (w["mlabel"], sh),
        "%s, %s to %s against %s to %s: each %s's contribution to the change" % (
            NB._cap(w["what"]), basis["windows"]["prior"][0], basis["windows"]["prior"][1],
            basis["windows"]["latest"][0], basis["windows"]["latest"][1], sh),
        "drove", w["claim"], anchors, None, w["grade"],
        {"label": w["mlabel"], "unit": unit, "kind": "count" if w["measure"] is None else "amount"}, data,
        {"cols": ["Step", col_head], "rows": [[s["label"], s["text"]] for s in data["steps"]]}, summary, supp,
        "Scenario items on the rows the engine kept (%s to %s); their monthly totals match the engine's own charted "
        "series" % tuple(w["months"]),
        {"columns": ([w["mlabel"]] if w["measure"] else []) + [sh], "rows": w["rows"], "months": w["months"],
         "op": ("%s added up by %s in each 12-month window" % (w["mlabel"], sh)) if w["measure"]
         else "rows counted by %s in each 12-month window" % sh})


def _b_pvm(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    basis, items = _basis(ctx)
    if basis.get("how") == "count":
        raise Refused("the headline is a count of rows, not a money total")
    price, volume, mix = (items.get("price_volume_mix.%s" % k) for k in ("price", "volume", "mix"))
    if price is None or volume is None:
        why = next((x for x in ctx.sc.get("refused") or [] if "per unit" in x or "price" in x), "")
        raise Refused("the scenarios block has no price and volume split%s" % ((": " + why) if why else ""))
    units = str(((price.get("inputs") or {}).get("columns") or [""])[-1])
    if cols:
        _check_basis_cols(ctx, basis, None, cols[0], need_seg=False)
        if cols[1] != units:
            raise Refused("the scenarios block measured units in %s, not %s" % (ctx.header(units), ctx.header(cols[1])))
        if len(cols) > 2:
            _check_basis_cols(ctx, basis, cols[2], None)
    w = _wf_common(ctx, basis, items)
    seg = (basis.get("segment") or {}).get("column")
    parts = [("Price", price), ("Volume", volume)] + ([("Mix", mix)] if mix is not None else [])
    bs = {"split": "price_volume_mix", "finding_id": w["fid"] or None,
          "column": ctx.header(seg)[:120] if seg and mix is not None else None,
          "prior": list(basis["windows"]["prior"]), "latest": list(basis["windows"]["latest"])}
    data = _waterfall_data(w["prior"], [(k, float(it["value"]), str(it["text"])) for k, it in parts], w["latest"],
                           w["change"], bs)
    _check_waterfall(data)
    unit = str(basis.get("unit") or "")
    uh = ctx.header(units)
    mixed = mix is not None
    if mixed:
        summary = ("Of the %s change in %s, price (the change in %s per unit, at the latest units) adds %s, volume %s "
                   "and the shift of %s between %s values %s; they add up to the change." % (
                       w["change"][1], w["what"], w["mlabel"], price["text"], volume["text"], uh, ctx.header(seg),
                       mix["text"]))
    else:
        summary = ("Of the %s change in %s, price (the change in %s per unit, at the latest units) adds %s and volume "
                   "%s; they add up to the change." % (w["change"][1], w["what"], w["mlabel"], price["text"],
                                                      volume["text"]))
    return _record(
        "pvm_waterfall", "%s in the change in %s" % ("Price, volume and mix" if mixed else "Price and volume",
                                                    w["mlabel"]),
        "%s, %s to %s against %s to %s: the change in %s per unit, in %s%s" % (
            NB._cap(w["what"]), basis["windows"]["prior"][0], basis["windows"]["prior"][1], basis["windows"]["latest"][0],
            basis["windows"]["latest"][1], w["mlabel"], uh,
            (", and in the %s mix" % ctx.header(seg)) if mixed else ""),
        "drove", w["claim"],
        (["finding:" + w["fid"]] if w["fid"] in ctx.findings else []) + ["scenario:headline.prior"] +
        ["scenario:price_volume_mix.%s" % k.lower() for k, _it in parts] + ["scenario:headline.latest",
                                                                           "scenario:headline.change"],
        None, w["grade"], {"label": w["mlabel"], "unit": unit, "kind": "amount"}, data,
        {"cols": ["Step", NB._cap(w["what"]) + (" (%s)" % unit if unit and unit != "%" else "")],
         "rows": [[s["label"], s["text"]] for s in data["steps"]]}, summary, (0, ""),
        "Scenario items (price, volume%s) on the rows the engine kept; units on every row of both windows" % (
            " and mix" if mixed else ""),
        {"columns": [w["mlabel"], uh] + ([ctx.header(seg)] if mixed else []), "rows": w["rows"], "months": w["months"],
         "op": "price at the latest units, volume at the prior price per unit%s" % (
             ", mix at each %s's prior price" % ctx.header(seg) if mixed else "")})


def _b_slope(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    basis, items = _basis(ctx)
    _check_basis_cols(ctx, basis, cols[0] if cols else None, cols[1] if cols and len(cols) > 1 else None)
    w = _wf_common(ctx, basis, items)
    seg = basis["segment"]["column"]
    sh = ctx.header(seg)
    parts, supp = _segment_parts(ctx, basis, items, SLOPE_ROWS_MAX, per_window=True)
    parts.sort(key=lambda p: (p["status"] == "other", -p["b"], p["name"]))
    rows = []
    for p in parts:
        change = "new" if p["status"] == "entered" else "left" if p["status"] == "exited" else \
            (p["pct_text"] or p["text"])
        rows.append({"label": p["name"], "a": _r6(p["a"]), "b": _r6(p["b"]), "a_text": p["a_text"][:24],
                     "b_text": p["b_text"][:24], "change_text": change[:24]})
    tol = RECONCILE_TOL * max(1.0, abs(w["prior"][1]), abs(w["latest"][1])) + 1e-6 * len(rows)
    if abs(math.fsum(r["a"] for r in rows) - w["prior"][1]) > tol or abs(math.fsum(r["b"] for r in rows) - w["latest"][1]) > tol:
        raise Refused(R_RECON)
    top = rows[0]
    rest = rows[1:3]
    summary = ("%s: %s in the 12 months before, %s in the latest 12 months (%s)." % (
        top["label"], top["a_text"], top["b_text"], top["change_text"]))
    if rest:
        summary += " " + "; ".join("%s: %s to %s (%s)" % (r["label"], r["a_text"], r["b_text"], r["change_text"])
                                   for r in rest) + "."
    summary += " Part of a change graded %s; not graded itself." % (w["grade"] or "by the engine")
    anchors = (["finding:" + w["fid"]] if w["fid"] in ctx.findings else []) + \
        ["scenario:%s.%s" % (p["slug"], k) for p in parts if p["slug"] and not p.get("made")
         for k in ("prior", "latest", "pct") if "%s.%s" % (p["slug"], k) in items]
    unit = str(basis.get("unit") or "")
    return _record(
        "slope", "%s by %s, the 12 months before and the latest 12 months" % (NB._cap(w["mlabel"]), sh),
        "Each %s's %s in %s to %s and %s to %s, and its own change" % (
            sh, w["what"], basis["windows"]["prior"][0], basis["windows"]["prior"][1], basis["windows"]["latest"][0],
            basis["windows"]["latest"][1]),
        "scenarios", w["claim"], anchors, None, w["grade"],
        {"label": w["mlabel"], "unit": unit, "kind": "count" if w["measure"] is None else "amount"},
        {"rows": rows, "a_label": "12 months before", "b_label": "Latest 12 months"},
        {"cols": [sh, "12 months before", "Latest 12 months", "Own change"],
         "rows": [[r["label"], r["a_text"], r["b_text"], r["change_text"]] for r in rows]}, summary, supp,
        "Scenario items (each %s's two totals and its own change) on the rows the engine kept" % sh,
        {"columns": ([w["mlabel"]] if w["measure"] else []) + [sh], "rows": w["rows"], "months": w["months"],
         "op": ("%s added up by %s in each 12-month window" % (w["mlabel"], sh)) if w["measure"]
         else "rows counted by %s in each 12-month window" % sh})


# ----------------------------------------------------------------------------- heatmaps
def _cell(value: Optional[float], n: int, text: str) -> Tuple[Optional[float], str, int, Optional[int]]:
    """(value, text, tier placeholder, n) of a cell: shown (n >= 5), suppressed ('<5', value and n null) or empty
    ('' and n 0)."""
    if n <= 0 or value is None:
        return None, "", 0, 0
    if n < SMALL_CELL:
        return None, "<5", 0, None
    return _r6(value), text, 0, int(n)


def _grid(rows: List[str], cols: List[str], cells: List[List[Tuple[Optional[float], str, int, Optional[int]]]],
          scale: str, fmt: Callable[[float], str], row_label: str, col_label: str, corr: bool = False
          ) -> Tuple[Dict[str, Any], int]:
    """A heatmap's data from its cells, tiers and legend computed on what is shown; (data, suppressed cells)."""
    values = [[c[0] for c in row] for row in cells]
    text = [[c[1] for c in row] for row in cells]
    n = [[c[3] for c in row] for row in cells]
    if corr:
        tier = [[0 if (v is None or (i == j)) else _corr_tier(v) for j, v in enumerate(row)] for i, row in enumerate(values)]
        legend = [{"tier": t, "text": _CORR_LEGEND[t]} for t in _used(tier)]
    elif scale == "sequential":
        tier, b = _seq_tiers(values)
        legend = _seq_legend(tier, b, fmt)
    else:
        tier, b = _div_tiers(values)
        legend = _div_legend(tier, b, fmt, fmt(0.0))
    for row in text:
        for t in row:
            if len(t) > CELL_TEXT_MAX:
                raise Refused("a figure too long to print in a cell")
    supp = sum(1 for row in text for t in row if t == "<5")
    return {"rows": rows, "cols": cols, "values": values, "text": text, "tier": tier, "n": n, "scale": scale,
            "legend": legend, "row_label": row_label[:80], "col_label": col_label[:80]}, supp


def _shown(data: Dict[str, Any]) -> int:
    return sum(1 for row in data["values"] for v in row if v is not None)


def _placeholder_zeros(ctx: Ctx, land: str, v: Any) -> Tuple[Any, int, str]:
    """A level's zeros that mark "no value" (the analyses' own evidence, nl_browser._zero_shape), made missing:
    (values, how many, the note), as the scenarios' historical range reads them."""
    import numpy as np
    import pandas as pd
    st = ctx.ptype(land)
    if not (st in NB.PLACEHOLDER_ZERO_TYPES or not st) or ctx.is_flow(land):
        return v, 0, ""
    a = v.to_numpy(dtype=float)
    if not NB._zero_gate(a):
        return v, 0, ""
    _z, ev = NB._zero_shape(a, ctx.frame[ctx.date] if ctx.date else None, None)
    if not ev:
        return v, 0, ""
    b = a.copy()
    b[ev["mask"]] = np.nan
    return pd.Series(b, index=v.index), int(ev["zeros"]), "%s zero values read as no value" % _count(int(ev["zeros"]))


def _reconcile_months(ctx: Ctx, land: Optional[str], flow: bool, got: Dict[str, Tuple[float, int]]
                      ) -> Optional[str]:
    """Each month's total (or average) against the engine's own monthly series where they overlap, to 1e-6: the
    claim's trend chart (trend.total:<m>, trend.<m> or trend.volume), else its season chart. The finding id the
    matched chart belongs to ("" when none), or Refused when a month differs or nothing overlaps."""
    if land is None:
        keys = ["trend.volume"]
    elif flow:
        keys = ["trend.total:%s" % land]
    else:
        keys = ["trend.%s" % land]
    for k in keys:
        ch = ctx.charts.get(k)
        if not ch:
            continue
        d = ch.get("data") or {}
        over = 0
        for m, v in zip(d.get("months") or [], d.get("values") or []):
            if v is None or m not in got:
                continue
            over += 1
            if not _close(got[m][0], float(v)):
                raise Refused(R_RECON)
        if over:
            return str((ch.get("finding_ids") or [""])[0])
    ch = ctx.charts.get("season.%s" % ("volume" if land is None else land))
    if ch:
        d = ch.get("data") or {}
        over = 0
        for i, y in enumerate(d.get("years") or []):
            for j in range(12):
                m = "%s-%02d" % (y, j + 1)
                v, n = d["values"][i][j], d["n"][i][j]
                if v is None or not n or m not in got:
                    continue
                over += 1
                want = float(v) if (land is None or not flow) else float(v) * int(n)
                if not _close(got[m][0], want):
                    raise Refused(R_RECON)
        if over:
            return str((ch.get("finding_ids") or [""])[0]) if ch.get("finding_ids") else ""
    raise Refused(R_RECON)


def _monthly(ctx: Ctx, land: Optional[str], flow: bool, rows: Optional[Any] = None
             ) -> Tuple[Dict[str, Tuple[float, int]], int, str]:
    """{month: (total or average, rows)} over the kept rows (optionally a mask), the rows read, the zeros note."""
    import pandas as pd
    mon = ctx.month
    sel = mon.notna() if rows is None else (mon.notna() & rows)
    note = ""
    if land is None:
        g = mon[sel].value_counts()
        return {str(m): (float(n), int(n)) for m, n in g.items()}, int(sel.sum()), note
    v = ctx.nums(land)
    if not flow:
        v, _nz, note = _placeholder_zeros(ctx, land, v)
    ok = sel & v.notna()
    out: Dict[str, Tuple[float, int]] = {}
    for m, s in v[ok].groupby(mon[ok], sort=True):
        vals = [float(x) for x in s.tolist()]
        out[str(m)] = (math.fsum(vals) if flow else math.fsum(vals) / len(vals), len(vals))
    return out, int(ok.sum()), note


def _b_calendar(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    if ctx.month is None:
        raise Refused("no date column the engine reads")
    land = cols[0] if cols else None
    if cols is None:
        _has, land = ctx.primary_measure()
    flow = ctx.is_flow(land)
    got, n_rows, note = _monthly(ctx, land, flow)
    if not got:
        raise Refused("no dated rows")
    ms = sorted(got)
    span = NB._month_range(ms[0], ms[-1])
    years_all = sorted({m[:4] for m in span})
    if len(span) < CALENDAR_MIN_MONTHS or len(years_all) < 2:
        raise Refused(REGISTRY["calendar_heatmap"]["limit_needs"] + "; the file spans %s months" % _count(len(span)))
    fid = _reconcile_months(ctx, land, flow, got)
    mh = ctx.header(land) if land else "rows"
    has, pm = ctx.primary_measure()
    section = "headline" if has and pm == land else "other"
    unit = ctx.unit(land)
    first = max(0, len(years_all) - CALENDAR_YEARS)

    cfmt = _cell_fmt(_fmt, [v for v, n in got.values() if n >= SMALL_CELL]) if flow else _fmt

    def build(k: int) -> Dict[str, Any]:
        years = years_all[k:]
        cells = []
        for y in years:
            row = []
            for j in range(12):
                m = "%s-%02d" % (y, j + 1)
                if flow:
                    v, n = got.get(m, (None, 0))
                    row.append(_cell(v, n, cfmt(v) if v is not None else ""))
                else:
                    p = NB._shift_month(m, -1)
                    if m not in got or p not in got or got[p][0] <= 0:
                        row.append((None, "", 0, 0))
                        continue
                    v = 100.0 * (got[m][0] / got[p][0] - 1.0)
                    n = min(got[m][1], got[p][1])
                    row.append(_cell(v, n, _chg(_r6(v), "%")))
            cells.append(row)
        scale = "sequential" if flow else "diverging"
        fmt = cfmt if flow else (lambda v: _chg(_r6(v), "%"))
        data, supp = _grid(years, list(MONTHS), cells, scale, fmt, "Year", "Month")
        shown = [(data["values"][i][j], i, j) for i in range(len(years)) for j in range(12)
                 if data["values"][i][j] is not None]
        if not shown:
            raise Refused("no month rests on 5 or more rows")
        trimmed = k > 0
        # why fewer years than the file has are shown (review of the chart registry, 30 Sep 2026: a 30-year file
        # trimmed to its latest 12 by the calendar's own rule said "the chart's size limit"): the size limit when the
        # byte cap took years beyond the rule's, else the rule (CALENDAR_YEARS)
        by_size = k > first
        yr = "%s and %s" % (years[0], years[-1]) if len(years) == 2 else "%s to %s" % (years[0], years[-1])
        if flow:
            hi, lo = max(shown), min(shown)
            summary = "The highest month is %s %s (%s) and the lowest %s %s (%s)." % (
                MONTHS[hi[2]], years[hi[1]], data["text"][hi[1]][hi[2]], MONTHS[lo[2]], years[lo[1]],
                data["text"][lo[1]][lo[2]])
            full = [i for i in range(len(years)) if all(data["values"][i][j] is not None for j in range(12))]
            if len(full) >= 2 and full[-1] == full[-2] + 1:
                a, b = full[-2], full[-1]
                up = sum(1 for j in range(12) if data["values"][b][j] > data["values"][a][j])
                summary += " %s is above %s in %s of the 12 months." % (years[b], years[a], _count(up))
            title = "%s by month, %s" % ("Rows" if land is None else NB._cap(mh), yr)
            sub = "%s in each month; the shade and the glyph mark the lowest, middle and highest third of the months" % (
                "Rows" if land is None else "Total %s" % mh)
            kind, op = ("count" if land is None else "amount"), ("rows counted by calendar month" if land is None
                                                                 else "%s added up by calendar month" % mh)
        else:
            up, dn = max(shown), min(shown)
            summary = "The largest rise on the month before is %s %s (%s) and the largest fall %s %s (%s)." % (
                MONTHS[up[2]], years[up[1]], data["text"][up[1]][up[2]], MONTHS[dn[2]], years[dn[1]],
                data["text"][dn[1]][dn[2]])
            title = "%s month on month, %s" % (NB._cap(mh), yr)
            sub = "Change in the month's average %s from the month before, in percent%s" % (
                mh, ("; %s" % note) if note else "")
            kind, op = "change_pct", "%s averaged by calendar month, percent change from the month before" % mh
        if supp:
            summary += " %s %s on fewer than 5 rows and %s not shown." % (
                _plural(supp, "month"), "rests" if supp == 1 else "rest", "is" if supp == 1 else "are")
        if trimmed:
            sub += "; %s to %s shown: %s" % (years[0], years[-1], "the chart's size limit" if by_size else
                                             "the latest %d years" % CALENDAR_YEARS)
        f = ctx.findings.get(fid) if fid else None
        dm = sorted(m for m in got if m[:4] >= years[0])
        rec = _record(
            "calendar_heatmap", title, sub, section, str((f or {}).get("claim") or "%s by month" % NB._cap(mh)),
            ["finding:" + fid] if f else [], None, None,
            {"label": mh, "unit": "%" if not flow else unit, "kind": kind}, data,
            _grid_table("Year", years, list(MONTHS), data["text"]), summary,
            (supp, "%s %s on fewer than 5 rows" % (_plural(supp, "month"), "rests" if supp == 1 else "rest")
             if supp else ""),
            "The rows the engine kept, by %s month; each month's %s equals the engine's own charted series" % (
                ctx.header(ctx.date), "total" if flow else "average"),
            {"columns": ([mh] if land else []) + [ctx.header(ctx.date)],
             "rows": int(sum(got[m][1] for m in dm)) if flow else int(sum(got[m][1] for m in dm)),
             "months": [dm[0], dm[-1]], "op": op})
        return rec
    return _fit(lambda k: build(first + k), len(years_all) - first - 2)


def _fit(make: Callable[[int], Dict[str, Any]], steps: int) -> Dict[str, Any]:
    """The record, trimmed one step at a time (make(k): k steps trimmed) until it fits its byte cap, or refused."""
    for k in range(0, max(0, steps) + 1):
        rec = make(k)
        if record_bytes(rec) <= bytes_cap(rec["kind"]):
            return rec
    raise Refused(R_SIZE)


def _top_levels(ctx: Ctx, land: str, keep: int, measure: Optional[str] = None) -> Tuple[List[str], Any]:
    """(the rows' labels: the `keep`-1 largest levels (by the chart's measure: Ctx.levels) and "other", or every level
    when there are at most `keep`; each row's group, None for a blank)."""
    lv = ctx.levels(land, measure)
    names = [g for g, _n in lv]
    top = names if len(names) <= keep else names[:keep - 1]
    t = ctx.texts(land)
    grp = t.where(t != "", None)
    grp = grp.where(grp.isna() | grp.isin(top), OTHER)
    labels = top + ([OTHER] if len(names) > len(top) else [])
    if OTHER in names and len(names) > len(top):
        raise Refused("%s already has a level named other" % ctx.header(land))
    return labels, grp


def _b_change(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    if ctx.month is None:
        raise Refused("no date column the engine reads")
    if cols:
        seg, land = cols[0], (cols[1] if len(cols) > 1 else None)
    else:
        seg = (((ctx.sc or {}).get("basis") or {}).get("segment") or {}).get("column") or next(iter(_cat_cands(ctx)), None)
        _h, land = ctx.primary_measure()
        if seg is None:
            raise Refused("no category column")
    flow = ctx.is_flow(land)
    _names_check(ctx, seg)
    labels, grp = _top_levels(ctx, seg, CROSS_ROWS, land)
    if not SEGMENT_LEVELS[0] <= len(labels) <= SEGMENT_LEVELS[1]:
        raise Refused("%s has %s" % (ctx.header(seg), _plural(len(labels), "level")))
    for g in labels:
        if not _label_ok(ctx, g):
            raise Refused("%s has a level too long to print or that reads as personal data" % ctx.header(seg))
    allm, _n, note = _monthly(ctx, land, flow)
    if not allm:
        raise Refused("no dated rows")
    ms = sorted(allm)
    span = NB._month_range(ms[0], ms[-1])
    if len(span) < CHANGE_MIN_MONTHS:
        raise Refused(REGISTRY["change_heatmap"]["limit_needs"] + "; the file spans %s months" % _count(len(span)))
    by: Dict[str, Dict[str, Tuple[float, int]]] = {}
    sums: Dict[str, List[float]] = {}
    for g in labels:
        got, _r, _nt = _monthly(ctx, land, flow, grp == g)
        by[g] = got
    # the rows of a blank level are in no row of the grid, but in the engine's series: they count in the check
    blank_got, _r, _nt = _monthly(ctx, land, flow, grp.isna())
    for m in span:
        parts = [by[g].get(m) for g in labels] + [blank_got.get(m)]
        parts = [p for p in parts if p is not None]
        if not parts:
            continue
        if flow:
            sums[m] = [math.fsum(p[0] for p in parts), sum(p[1] for p in parts)]
        else:
            n = sum(p[1] for p in parts)
            sums[m] = [math.fsum(p[0] * p[1] for p in parts) / n, n]
    fid = _reconcile_months(ctx, land, flow, {m: (v[0], int(v[1])) for m, v in sums.items()})
    cols_all = [m for m in span if NB._shift_month(m, -12) >= span[0]][-CHANGE_COLS:]
    mh = ctx.header(land) if land else "rows"
    sh = ctx.header(seg)
    basis = (ctx.sc or {}).get("basis") or {}
    derived = bool(basis) and ((basis.get("how") == "count" and land is None) or
                               (basis.get("how") == "total" and basis.get("measure") == land))

    def build(k: int) -> Dict[str, Any]:
        cm = cols_all[k:]
        cells = []
        for g in labels:
            row = []
            for m in cm:
                p = NB._shift_month(m, -12)
                a, b = by[g].get(p), by[g].get(m)
                if a is None or b is None or a[1] == 0 or b[1] == 0:
                    row.append((None, "", 0, 0))
                    continue
                n = min(a[1], b[1])
                if n < SMALL_CELL:
                    row.append((None, "<5", 0, None))
                    continue
                if a[0] <= 0:
                    row.append((None, "", 0, 0))
                    continue
                v = 100.0 * (b[0] / a[0] - 1.0)
                row.append(_cell(v, n, _chg(_r6(v), "%")))
            cells.append(row)
        data, supp = _grid(labels, cm, cells, "diverging", lambda v: _chg(_r6(v), "%"), sh, "Month")
        total = len(labels) * len(cm)
        if 2 * _shown(data) < total:
            raise Refused(R_HALF)
        shown = [(data["values"][i][j], i, j) for i in range(len(labels)) for j in range(len(cm))
                 if data["values"][i][j] is not None]
        up, dn = max(shown), min(shown)
        summary = "The largest rise on the year before is %s in %s (%s) and the largest fall %s in %s (%s)." % (
            labels[up[1]], cm[up[2]], data["text"][up[1]][up[2]], labels[dn[1]], cm[dn[2]], data["text"][dn[1]][dn[2]])
        if supp:
            summary += " %s %s on fewer than 5 rows in one of the two years and %s not shown." % (
                _plural(supp, "cell"), "rests" if supp == 1 else "rest", "is" if supp == 1 else "are")
        sub = "Each %s's %s in the month against the same month a year before, in percent%s" % (
            sh, ("total %s" % mh if flow and land else "rows" if land is None else "average %s" % mh),
            ("; %s" % note) if note else "")
        if k:
            sub += "; %s to %s shown: the chart's size limit" % (cm[0], cm[-1])
        f = ctx.findings.get(fid) if fid else None
        grade = (ctx.findings.get(str(basis.get("finding_id"))) or {}).get("grade") if derived else None
        return _record(
            "change_heatmap", "%s by %s and month, change on the year before" % (NB._cap(mh), sh), sub, "drove",
            str((f or {}).get("claim") or "%s by %s" % (NB._cap(mh), sh)),
            (["finding:" + str(basis.get("finding_id"))] if derived and str(basis.get("finding_id")) in ctx.findings
             else []) + (["finding:" + fid] if f else []), None, grade,
            {"label": mh, "unit": "%", "kind": "change_pct"}, data, _grid_table(sh, labels, cm, data["text"]),
            summary, (supp, "%s %s on fewer than 5 rows in one of the two years" % (
                _plural(supp, "cell"), "rests" if supp == 1 else "rest") if supp else ""),
            "The rows the engine kept, by %s and %s month; each month's %s over every %s equals the engine's own "
            "charted series" % (sh, ctx.header(ctx.date), "total" if flow else "average", sh),
            {"columns": ([mh] if land else []) + [sh, ctx.header(ctx.date)],
             "rows": int(sum(v[1] for m, v in sums.items() if NB._shift_month(cm[0], -12) <= m <= cm[-1])),
             "months": [NB._shift_month(cm[0], -12), cm[-1]],
             "op": "%s by %s and month, percent change on the same month a year before" % (
                 "rows counted" if land is None else "%s %s" % (mh, "added up" if flow else "averaged"), sh)})
    return _fit(build, len(cols_all) - 1)


def _b_crosstab(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    if cols:
        a, b = cols[0], cols[1]
        land = cols[2] if len(cols) > 2 else None
    else:
        cands = _cat_cands(ctx)
        rat = [c for c in ctx.columns if c not in cands and c not in ctx.flag_land and ctx.rating_levels(c) is not None
               and not ctx.ptype(c)]
        pool = cands + rat
        if len(pool) < 2:
            raise Refused("needs two category columns")
        a, b, land = pool[0], pool[1], None
    if a == b:
        raise Refused("the two columns are the same")
    flow = ctx.is_flow(land)
    rl = ctx.rating_levels(b) if (not ctx.ptype(b) or ctx.ptype(b) in RATING_TYPES) else None
    # a crosstab's rows and columns are the columns' own values: never people's names (review of the chart registry)
    _names_check(ctx, a)
    if rl is None:
        _names_check(ctx, b)
    ra, ga = _top_levels(ctx, a, CROSS_ROWS, land)
    if rl is not None:
        rb = [_fmt(x) for x in rl]
        nb = ctx.nums(b)
        gb = nb.map(lambda x: None if x != x else _fmt(float(x)))
    else:
        rb, gb = _top_levels(ctx, b, CROSS_COLS, land)
    if len(ra) < 2 or len(rb) < 2:
        raise Refused("each column needs 2 or more levels")
    for g in ra + rb:
        if not _label_ok(ctx, g):
            raise Refused("a level too long to print or that reads as personal data")
    base = ga.notna() & gb.notna()
    if land is not None:
        v = ctx.nums(land)
        if not flow:
            v, _nz, _nt = _placeholder_zeros(ctx, land, v)
        base = base & v.notna()
    else:
        v = None
    n_rows = int(base.sum())
    if not n_rows:
        raise Refused("no row holds both columns")
    import pandas as pd
    df = pd.DataFrame({"a": ga[base], "b": gb[base]})
    if v is not None:
        df["v"] = v[base].astype(float)
    cnt = df.groupby(["a", "b"]).size()
    tot = df.groupby(["a", "b"])["v"].apply(lambda s: math.fsum(float(x) for x in s)) if v is not None else None
    whole = math.fsum(float(x) for x in df["v"]) if v is not None else float(n_rows)
    mh = ctx.header(land) if land else "rows"
    ah, bh = ctx.header(a), ctx.header(b)
    # every cell's figure could be a total over several levels (the rows folded into 'other'): the widest is the
    # whole's, so the format is chosen once from it and the cells
    cfmt = _cell_fmt(_count if v is None else _fmt, [whole] + ([float(x) for x in cnt.values] if v is None else
                                      [float(x) for x in tot.values] if flow else []))

    def build(k: int) -> Dict[str, Any]:
        rows = list(ra)
        fold = []
        if k:
            real = [r for r in rows if r != OTHER]
            fold = real[len(real) - k:]
            rows = real[:len(real) - k] + [OTHER]
        cells = []
        check = []
        for r in rows:
            srcs = [r] if r != OTHER else [x for x in ra if x == OTHER] + fold
            row = []
            for c in rb:
                n = int(sum(int(cnt.get((s, c), 0)) for s in srcs))
                if v is None:
                    val = float(n)
                elif flow:
                    val = math.fsum(float(tot.get((s, c), 0.0)) for s in srcs)
                else:
                    val = math.fsum(float(tot.get((s, c), 0.0)) for s in srcs) / n if n else None
                check.append((val if (v is None or flow) else (val or 0.0) * n))
                txt = cfmt(val) if (v is None or flow) else (_fmt(val) if val is not None else "")
                row.append(_cell(val, n, txt))
            cells.append(row)
        if abs(math.fsum(check) - whole) > RECONCILE_TOL * max(1.0, abs(whole)) + 1e-6 * len(check):
            raise Refused(R_RECON)
        data, supp = _grid(rows, rb, cells, "sequential", cfmt if (v is None or flow) else _fmt, ah, bh)
        total = len(rows) * len(rb)
        if 2 * _shown(data) < total:
            raise Refused(R_HALF)
        shown = [(data["values"][i][j], i, j) for i in range(len(rows)) for j in range(len(rb))
                 if data["values"][i][j] is not None]
        hi = max(shown)
        what = "rows" if v is None else ("total %s" % mh if flow else "average %s" % mh)
        summary = "The largest cell is %s %s with %s %s (%s)." % (ah, rows[hi[1]], bh, rb[hi[2]],
                                                                 data["text"][hi[1]][hi[2]])
        if supp:
            summary += " %s %s on fewer than 5 rows and %s not shown." % (
                _plural(supp, "cell"), "rests" if supp == 1 else "rest", "is" if supp == 1 else "are")
        sub = "%s by %s%s and %s%s; cells under 5 rows are not shown" % (
            NB._cap(what), ah, " (the %d with the most rows and all others)" % (len(rows) - 1) if OTHER in rows else "",
            bh, " (the %d with the most rows and all others)" % (len(rb) - 1) if OTHER in rb else "")
        if k:
            sub += "; the last %s folded into other: the chart's size limit" % _plural(k, "row")
        return _record(
            "crosstab_heatmap", "%s by %s and %s" % (NB._cap(what), ah, bh), sub, "other",
            "How %s splits by %s and %s" % (what, ah, bh), [], None, None,
            {"label": mh, "unit": ctx.unit(land) if v is not None else "",
             "kind": "count" if v is None else ("amount" if flow else "average")}, data,
            _grid_table(ah, rows, rb, data["text"]), summary,
            (supp, "%s %s on fewer than 5 rows and %s not shown" % (_plural(supp, "cell"), "rests" if supp == 1 else
                                                                   "rest", "is" if supp == 1 else "are") if supp else ""),
            "The rows the engine kept; the cells, the suppressed ones included, add up to the %s of the rows read" % (
                "count" if v is None else "total" if flow else "sum"),
            {"columns": [ah, bh] + ([mh] if land else []), "rows": n_rows, "months": _span_months(ctx, base),
             "op": "%s by %s (%s) and %s" % ("rows counted" if v is None else "%s %s" % (mh, "added up" if flow
                                                                                    else "averaged"),
                                               ah, "top %d, the rest as other" % (len(rows) - 1) if OTHER in rows
                                               else "every level", bh)})
    real = [r for r in ra if r != OTHER]
    return _fit(build, max(0, len(real) - 1))


def _span_months(ctx: Ctx, mask: Any) -> Optional[List[str]]:
    if ctx.month is None:
        return None
    m = ctx.month[mask].dropna()
    return [str(m.min()), str(m.max())] if len(m) else None


def _b_theme(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    """The themes analysis's words by rating. Built only when the AI plan asks for it by name (review of the chart
    registry, 30 Sep 2026: the engine's own pick charted the words of short review titles, staff names among them). No
    word is a person's name (nl_browser._theme_drop: a token of a flagged column's values, a given name, a word the
    texts capitalise mid-sentence), none is a word of a withheld column's name (its row alone is left out), and words
    are read in any script (NFC). A text with no rating is counted in 'all' only, and the subtitle says how many. When a
    rating column is suppressed (fewer than 5 texts), or fewer than 5 texts have no rating, 'all' is printed in whole
    percents and without its n, so the hidden texts' words cannot be worked back from it and the shown cells
    (best effort: no secondary suppression; CONTRACT 5.9)."""
    if not cols:
        raise Refused("the engine never picks this chart itself: the AI plan must ask for it by name")
    tcol, rcol = cols[0], cols[1]
    import pandas as pd
    th, rh = ctx.header(tcol), ctx.header(rcol)
    df = pd.DataFrame({th: ctx.texts(tcol)})
    # every flagged column's tokens but the text's own (a kept text's words are its themes: Ctx.names_but), as the
    # plan's themes analysis of the same column reads them; for an unflagged text, every flagged column's
    names = ctx.names_but(tcol)
    res = NB._a_themes(df, None, None, [th], {"columns": []}, None, names=names)
    if res.get("refused"):
        raise Refused(str(res["refused"]))
    words = [(r[0], int(str(r[1]).replace(",", "")), r[2]) for r in res["table"]["rows"]]
    # the plan's own themes analysis of the same column, when it ran: its word counts are these
    ana_i = next((i for i, a in enumerate(ctx.analyses) if a.get("type") == "themes" and a.get("columns")
                  and ctx.landed(a["columns"][0]) == tcol), None)
    if ana_i is not None:
        theirs = [(r[0], int(str(r[1]).replace(",", ""))) for r in (ctx.analyses[ana_i].get("table") or {}).get("rows") or []]
        if theirs != [(w, c) for w, c, _t in words]:
            raise Refused("the themes analysis of %s counted other words than this chart reads" % th)
    words = [w for w in words if _label_ok(ctx, w[0], names=names) and not (set(w[0].split()) & ctx.withheld_words)][:THEME_WORDS]
    if not words:
        raise Refused("the themes analysis found no word used in 3 or more texts")
    levels = ctx.rating_levels(rcol)
    txt = NB._texts_of(df, th)
    rv = ctx.nums(rcol)
    has = txt != ""
    n_all = int(has.sum())
    drop = NB._theme_drop([t for t in txt.tolist() if t], names)
    toks = txt[has].map(lambda t: NB._theme_tokens(t, drop))
    wsets = toks.map(lambda tk: set(w for w in tk if w))
    psets = toks.map(NB._theme_pairs)
    rl = rv[has]
    rated = rl.isin(levels)
    n_unrated = int((~rated).sum())
    cols_lab = [_fmt(x) for x in levels]
    base = {lv: int((rl == lv).sum()) for lv in levels}
    if sum(base.values()) + n_unrated != n_all:
        raise Refused(R_RECON)
    hidden = any(0 < n < SMALL_CELL for n in base.values()) or 0 < n_unrated < SMALL_CELL

    def uses(w: str) -> Any:
        return (psets if " " in w else wsets).map(lambda s: w in s)

    def all_cell(c_all: int, t_all: str) -> Tuple[Optional[float], str, int, Optional[int]]:
        if not hidden:
            return _cell(100.0 * c_all / n_all, n_all, t_all)
        whole = float(math.floor(100.0 * c_all / n_all + 0.5))
        return whole, _fmt(whole) + "%", 0, None

    def build(k: int) -> Dict[str, Any]:
        ws = words[:len(words) - k]
        cells = []
        for w, c_all, t_all in ws:
            u = uses(w)
            row = []
            got = 0
            for lv in levels:
                n = base[lv]
                hits = int((u & (rl == lv)).sum())
                got += hits
                share = 100.0 * hits / n if n else None
                row.append(_cell(share, n, (_fmt(share) + "%") if share is not None else ""))
            if got + int((u & ~rated).sum()) != c_all:
                raise Refused(R_RECON)
            row.append(all_cell(c_all, t_all))
            cells.append(row)
        labels = [w for w, _c, _t in ws]
        data, supp = _grid(labels, cols_lab + ["all"], cells, "sequential", lambda v: _fmt(v) + "%", "word", rh)
        top = ws[0]
        shown = [(data["values"][0][j], j) for j in range(len(levels)) if data["values"][0][j] is not None]
        summary = "'%s' is the commonest word, in %s of all texts." % (top[0], data["text"][0][-1])
        if len(shown) >= 2:
            hi, lo = max(shown), min(shown)
            summary += " It is used most in the %s %s texts (%s) and least in the %s %s texts (%s)." % (
                rh, cols_lab[hi[1]], data["text"][0][hi[1]], rh, cols_lab[lo[1]], data["text"][0][lo[1]])
        n_r = sum(1 for lv in levels if 0 < base[lv] < SMALL_CELL)
        if supp:
            summary += " %s %s fewer than 5 texts and %s not shown." % (
                _plural(n_r, "rating"), "has" if n_r == 1 else "have", "is" if n_r == 1 else "are")
        if n_unrated:
            summary += " %s %s no %s and %s counted only in 'all'." % (
                _plural(n_unrated, "text"), "has" if n_unrated == 1 else "have", rh, "is" if n_unrated == 1 else "are")
        extra = []
        if n_unrated:
            extra.append("%s with no %s %s in 'all' only" % (_plural(n_unrated, "text"), rh,
                                                           "is" if n_unrated == 1 else "are"))
        if hidden:
            extra.append("'all' in whole percents")
        if k:
            extra.append("the last %s left out: the chart's size limit" % _plural(k, "word"))
        sub = "The share of each %s's texts that use the word; the %s used in 3 or more texts; 'all' is the themes " \
              "analysis" % (rh, _plural(len(ws), "word or phrase", "words and phrases"))
        if extra and len(sub) + sum(2 + len(x) for x in extra) > 160:
            sub = "The share of each %s's texts that use the word; 'all' is the themes analysis" % rh
        sub = "; ".join([sub] + extra)
        return _record(
            "theme_rating_heatmap", "What the %s texts say, by %s" % (th, rh), sub, "other",
            "What the %s texts talk about" % th, ["analysis:%d" % (ana_i + 1)] if ana_i is not None and ana_i < 8 else [],
            None, None, {"label": "texts using the word", "unit": "%", "kind": "share_pct"}, data,
            _grid_table("word", labels, cols_lab + ["all"], data["text"]), summary,
            (supp, "%s %s fewer than 5 texts" % (_plural(n_r, "rating"), "has" if n_r == 1 else "have") if supp else ""),
            "The themes analysis's words, counted again by %s on the same texts; each word's texts add up to its "
            "count in the analysis" % rh,
            {"columns": [th, rh], "rows": n_all, "months": _span_months(ctx, txt != ""),
             "op": "share of each %s's texts that use the word at least once" % rh})
    return _fit(build, len(words) - 1)


def _b_correlation(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    ch = ctx.charts.get("corr")
    if not ch:
        raise Refused("the engine's correlation chart needs 3 or more measures; this file has fewer")
    d = ch.get("data") or {}
    ms = [str(m) for m in d.get("measures") or []]
    if cols:
        miss = [c for c in cols if c not in ms]
        if miss:
            raise Refused("%s %s not among the engine's measures, so its r cannot be checked" % (
                ", ".join(ctx.header(c) for c in miss[:3]), "is" if len(miss) == 1 else "are"))
        pick = list(dict.fromkeys(cols))
    else:
        pick = ms[:CORR_MEASURES[1]]
    if not CORR_MEASURES[0] <= len(pick) <= CORR_MEASURES[1]:
        raise Refused("needs 3 to 12 measures")
    if any(p in ctx.flag_land for p in pick):
        raise Refused(R_PERSONAL)
    idx = [ms.index(p) for p in pick]
    heads = [ctx.header(p) for p in pick]
    for h in heads:
        if not _label_ok(ctx, h):
            raise Refused("a measure name too long to print")

    def build(k: int) -> Dict[str, Any]:
        sel = idx[:len(idx) - k]
        names = heads[:len(sel)]
        cells = []
        for a in sel:
            row = []
            for b in sel:
                r, n = d["r"][a][b], int(d["n"][a][b] or 0)
                if a == b:
                    row.append((1.0, "1", 0, n if n >= SMALL_CELL else None) if n >= SMALL_CELL else
                               ((None, "<5", 0, None) if n else (None, "", 0, 0)))
                    continue
                if r is None:
                    row.append((None, "", 0, 0) if n >= SMALL_CELL or not n else (None, "<5", 0, None))
                    continue
                row.append(_cell(float(r), n, _signed_r(_r6(float(r)))))
            cells.append(row)
        data, supp = _grid(names, names, cells, "diverging", _signed_r, "measure", "measure", corr=True)
        for i, a in enumerate(sel):
            for j, b in enumerate(sel):
                v = data["values"][i][j]
                if v is not None and i != j and abs(v - float(d["r"][a][b])) > RECONCILE_TOL:
                    raise Refused(R_RECON)
        off = [(abs(data["values"][i][j]), i, j) for i in range(len(sel)) for j in range(i + 1, len(sel))
               if data["values"][i][j] is not None]
        if not off:
            raise Refused("no pair of measures rests on 5 or more rows")
        s = max(off)
        summary = "The strongest link is between %s and %s (r %s)." % (names[s[1]], names[s[2]], data["text"][s[1]][s[2]])
        if supp:
            summary += " %s %s on fewer than 5 rows and %s not shown." % (
                _plural(supp, "cell"), "rests" if supp == 1 else "rest", "is" if supp == 1 else "are")
        summary += " An association, not a cause."
        win = (ctx.rep.get("reproducibility") or {}).get("parameters", {}).get("window") or {}
        sub = "Pearson r of each pair of measures, on the rows holding both values%s" % (
            " in the analysis window (%s to %s)" % (win.get("start"), win.get("end")) if win.get("start") else "")
        if k:
            sub += "; the last %s left out: the chart's size limit" % _plural(k, "measure")
        return _record(
            "correlation_heatmap", "How the measures move together", sub, "other", "How the measures move together",
            ["chart:corr"], None, None, {"label": "Pearson r", "unit": "", "kind": "correlation"}, data,
            _grid_table("measure", names, names, data["text"]), summary,
            (supp, "%s %s on fewer than 5 rows holding both values" % (_plural(supp, "cell"), "rests" if supp == 1
                                                                       else "rest") if supp else ""),
            "The engine's correlation chart (%s)" % str(d.get("method") or "Pearson, pairwise complete rows"),
            {"columns": names, "rows": int(max((d["n"][a][a] or 0) for a in sel)),
             "months": [win["start"], win["end"]] if win.get("start") else None,
             "op": "Pearson r of each pair, on the rows holding both values"})
    return _fit(build, len(idx) - CORR_MEASURES[0])


def _cat_cands(ctx: Ctx) -> List[str]:
    """The category columns in the engine's order of preference: the plan's segment and geography roles, its
    category types, then the engine's dimensions; never a flagged column."""
    order: List[str] = []
    for land, pc in ctx.plan_by.items():
        if str(pc.get("role") or "") in ("segment", "geography"):
            order.append(land)
    for land, pc in ctx.plan_by.items():
        if str(pc.get("semantic_type") or "") in CATEGORY_TYPES:
            order.append(land)
    order += ctx.dims
    out = []
    for c in order:
        if c in out or c not in ctx.columns or c in ctx.flag_land or c == ctx.date:
            continue
        if not ctx.role_ok(c, "category"):
            out.append(c)
    return out


# ----------------------------------------------------------------------------- group ranges and pareto
def _b_group_ranges(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    import numpy as np
    import pandas as pd
    if cols:
        land, seg = cols[0], cols[1]
    else:
        _h, land = ctx.primary_measure()
        if land is None or not ctx.numeric(land):
            land = next((m for m in ctx.measures if ctx.numeric(m)), None)
        cands = _cat_cands(ctx)
        basis_seg = (((ctx.sc or {}).get("basis") or {}).get("segment") or {}).get("column")
        seg = basis_seg if basis_seg in cands else next(iter(cands), None)
        if land is None or seg is None:
            raise Refused("needs a measure and a category")
    mh, sh = ctx.header(land), ctx.header(seg)
    _names_check(ctx, seg)
    st = ctx.ptype(land) or ("flow_amount" if ctx.is_flow(land) else "level")
    s = pd.DataFrame({"g": ctx.texts(seg), "v": ctx.nums(land)}).dropna()
    s = s[s["g"] != ""]
    counts = s["g"].value_counts()
    order = sorted(((str(k), int(v)) for k, v in counts.items()), key=lambda kv: (-kv[1], kv[0]))
    keep = [g for g, n in order if n >= SMALL_CELL][:DOT_ROWS_MAX]
    if len(keep) < 2:
        raise Refused("fewer than two groups with 5 or more values")
    for g in keep:
        if not _label_ok(ctx, g):
            raise Refused("%s has a level too long to print or that reads as personal data" % sh)
    grand = NB._center(s["v"].values, st)
    m, _how = NB._shrink_m([grp["v"].to_numpy() for _k, grp in s.groupby("g", sort=True)], st)
    rows = []
    for g in keep:
        v = s.loc[s["g"] == g, "v"].to_numpy(dtype=float)
        lo, hi = NB._boot_ci(v, st, COMPARE_SEED, COMPARE_B)
        c = NB._center(v, st)
        rows.append((g, len(v), c, lo, hi, float(np.median(v)), NB._weighted_average(c, len(v), grand, st, m)))
    rows.sort(key=lambda r: (-r[6], -r[1], r[0]))
    # the compare analysis, run on the same rows by its own function: its table must be these figures
    plan = {"columns": [dict(pc, name=ctx.header(k)) for k, pc in ctx.plan_by.items()]}
    cmp_ = NB._a_compare(pd.DataFrame({mh: ctx.nums(land), sh: ctx.texts(seg)}), None, None, [mh], plan, None, by=sh)
    theirs = [(r[0], r[1], r[2], r[3], r[4]) for r in (cmp_.get("table") or {}).get("rows") or []]
    mine = [(r[0], _count(r[1]), _fmt(r[2]), "%s to %s" % (_fmt(r[3]), _fmt(r[4])), _fmt(r[5])) for r in rows]
    if theirs != mine:
        raise Refused(R_RECON)
    # the plan's own compare analysis of the same measure and groups, when it ran: to its printed digits
    ana_i = None
    for i, a in enumerate(ctx.analyses):
        if a.get("type") == "compare" and a.get("columns") and ctx.landed(a["columns"][0]) == land and \
                str(a.get("title") or "") == "%s by %s" % (mh, sh):
            ana_i = i
            got = [tuple(r[:5]) for r in (a.get("table") or {}).get("rows") or []]
            if got != mine:
                raise Refused(R_RECON)
            break
    few = int((counts < SMALL_CELL).sum())
    more = int((counts >= SMALL_CELL).sum()) - len(keep)
    if any(not r[3] <= r[2] <= r[4] for r in rows):
        raise Refused("a group's 95% range does not hold its average")
    data = {"rows": [{"label": r[0], "n": int(r[1]), "center": _r6(r[2]), "lo": _r6(r[3]),
                      "hi": _r6(r[4]), "median": _r6(r[5]),
                      "texts": {"n": _count(r[1]), "center": _fmt(r[2]), "lo": _fmt(r[3]), "hi": _fmt(r[4]),
                                "median": _fmt(r[5])}} for r in rows]}
    t0, t1 = data["rows"][0], data["rows"][-1]
    avg = "energy average" if st == "log_scale" else "average"
    # the figure printed is each group's own average; the order (the dots', the table's, the compare analysis's) is by
    # that average weighted by the group's rows (review of the chart registry, 30 Sep 2026: the summary called the
    # own average "its weighted average")
    summary = ("Ranked by each %s's %s weighted by its rows, %s is highest (its own %s %s, 95%% range %s to %s) and %s "
               "lowest (%s, %s to %s). An association with the %s, not its cause." % (
                   sh, avg, t0["label"], avg, t0["texts"]["center"], t0["texts"]["lo"], t0["texts"]["hi"], t1["label"],
                   t1["texts"]["center"], t1["texts"]["lo"], t1["texts"]["hi"], sh))
    notes = []
    if few:
        notes.append("%s with fewer than 5 values %s left out" % (_plural(few, "group"), "is" if few == 1 else "are"))
    if more:
        notes.append("%s more with 5 or more values %s not shown" % (_plural(more, "group"), "is" if more == 1 else "are"))
    return _record(
        "group_ranges", "%s %s by %s, with 95%% ranges" % (NB._cap(avg), mh, sh),
        "Each %s's %s %s, its 95%% bootstrap range (999 resamples) and its median; ordered by the %s weighted by its "
        "rows" % (sh, avg, mh, avg), "other", str(cmp_.get("title") or "%s by %s" % (mh, sh)),
        ["analysis:%d" % (ana_i + 1)] if ana_i is not None and ana_i < 8 else [], None, None,
        {"label": mh, "unit": ctx.unit(land), "kind": "average"}, data,
        {"cols": [sh, "Rows", NB._cap(avg), "95% range", "Median"],
         "rows": [[r["label"], r["texts"]["n"], r["texts"]["center"], "%s to %s" % (r["texts"]["lo"], r["texts"]["hi"]),
                   r["texts"]["median"]] for r in data["rows"]]},
        summary, (few, "; ".join(notes)),
        "The compare analysis on the rows the engine kept: the same seed and resamples, so the figures match its table",
        {"columns": [mh, sh], "rows": int(len(s)), "months": _span_months(ctx, ctx.nums(land).notna() & (ctx.texts(seg) != "")),
         "op": "each %s's %s %s per row, 95%% bootstrap range, median" % (sh, avg, mh)})


def _b_pareto(ctx: Ctx, cols: Optional[List[str]]) -> Dict[str, Any]:
    import pandas as pd
    if cols:
        seg, land = cols[0], (cols[1] if len(cols) > 1 else None)
    else:
        # the engine's own pick reads only a column the AI plan types as a category, an entity or a place (review of
        # the chart registry, 30 Sep 2026: with no plan it charted the values of a short text column, review titles
        # that named staff); any other column only when the plan asks for this chart by name
        big = [c for c in _cat_cands(ctx) if ctx.ptype(c) in PARETO_TYPES and len(ctx.levels(c)) >= PARETO_MIN_LEVELS]
        if not big:
            raise Refused("no column the plan types as a category of 8 or more levels")
        seg = big[0]
        _h, pm = ctx.primary_measure()
        land = pm if pm is not None and ctx.is_flow(pm) else None
    sh = ctx.header(seg)
    _names_check(ctx, seg)
    mh = ctx.header(land) if land else "rows"
    lab = ctx.texts(seg)
    ok = lab != ""
    if land is not None:
        v = ctx.nums(land)
        ok = ok & v.notna()
    df = pd.DataFrame({"g": lab[ok]})
    df["v"] = ctx.nums(land)[ok].astype(float) if land is not None else 1.0
    tot = {str(g): math.fsum(float(x) for x in s) for g, s in df.groupby("g", sort=True)["v"]}
    n_of = {str(g): int(n) for g, n in df["g"].value_counts().items()}
    if len(tot) < PARETO_MIN_LEVELS:
        raise Refused("%s has %s; a Pareto needs 8 or more" % (sh, _plural(len(tot), "level")))
    if len(tot) > PARETO_MAX_LEVELS:
        raise Refused("%s has %s levels; a Pareto reads at most %s" % (sh, _count(len(tot)), _count(PARETO_MAX_LEVELS)))
    if any(x < 0 for x in tot.values()):
        raise Refused("a level's total of %s is below zero, so shares of the whole do not add up" % mh)
    whole = math.fsum(tot.values())
    if whole <= 0:
        raise Refused("the whole is not above zero")
    allr = sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))
    k80, run = len(allr), 0.0
    for i, (_g, x) in enumerate(allr):
        run += x
        if 100.0 * run / whole >= 80.0 - 1e-9:
            k80 = i + 1
            break
    ranked = [(g, x) for g, x in allr if n_of[g] >= SMALL_CELL]
    small = sum(1 for g in tot if n_of[g] < SMALL_CELL)
    if not ranked:
        raise Refused("no level of %s rests on 5 or more rows" % sh)
    # the engine's ranked chart of the same column counts rows in its window; where it counts the same rows, its
    # bars are these levels and counts, in this order
    rk = ctx.charts.get("ranked.%s" % seg)
    if rk and land is None and int((rk.get("data") or {}).get("total") or -1) == int(len(df)):
        mine = [(g, int(x)) for g, x in allr[:len((rk.get("data") or {}).get("bars") or [])]]
        theirs = [(str(b.get("label")), int(b.get("rows") or 0)) for b in (rk.get("data") or {}).get("bars") or []]
        if mine != theirs:
            raise Refused("the ranking of %s differs from the engine's own ranked chart of the same rows" % sh)
    txt = _count if land is None else _fmt
    n_rows = int(len(df))

    def build(k: int) -> Dict[str, Any]:
        bars_src = list(ranked[:PARETO_BARS_MAX - k] if PARETO_BARS_MAX - k > 0 else ranked[:1])
        # 'other' rests on 5 or more rows (review of the chart registry, 30 Sep 2026: an 'other' of one level of 2 rows
        # printed that level's total): the smallest bars join it until it does
        merged = 0
        while bars_src and 0 < n_rows - sum(n_of[g] for g, _x in bars_src) < SMALL_CELL:
            bars_src.pop()
            merged += 1
        if not bars_src:
            raise Refused("no level of %s can be shown with 'other' resting on 5 or more rows" % sh)
        for g, _x in bars_src:
            if not _label_ok(ctx, g):
                raise Refused("%s has a level too long to print or that reads as personal data" % sh)
        bars, acc = [], 0.0
        for g, x in bars_src:
            acc += x
            cp = 100.0 * acc / whole
            bars.append({"label": g, "value": _r6(x), "text": txt(x)[:24], "cum_pct": _r6(min(cp, 100.0)),
                         "cum_text": _pct_share(cp)[:12]})
        n_other = len(tot) - len(bars)
        o_val = whole - acc
        other = None
        if n_other:
            other = {"label": OTHER, "value": _r6(max(o_val, 0.0)), "text": txt(max(o_val, 0.0))[:24], "cum_pct": 100.0,
                     "cum_text": _pct_share(100.0), "n_entities": int(n_other)}
        if abs(math.fsum([b["value"] for b in bars] + ([other["value"]] if other else [])) - whole) > \
                RECONCILE_TOL * max(1.0, whole) + 1e-6 * (len(bars) + 1):
            raise Refused(R_RECON)
        small_big = ("a level of %s with fewer than 5 rows is among the largest, so the bars' running share cannot "
                     "show where 80%% of %s is reached" % (sh, "the rows" if land is None else "the total " + mh))
        if k80 <= len(bars) and (bars[k80 - 1]["cum_pct"] < 80 or (k80 > 1 and bars[k80 - 2]["cum_pct"] >= 80)):
            raise Refused(small_big)                 # a level under 5 rows is among the largest: k80 would mislead
        if k80 > len(bars) and bars[-1]["cum_pct"] >= 80:
            raise Refused(small_big)
        what = "the rows" if land is None else "the total %s" % mh
        data = {"bars": bars, "other": other, "total": {"value": _r6(whole), "text": txt(whole)[:24]},
                "k80": {"k": int(k80), "of": int(len(tot)),
                        "text": ("%s of the %s levels of %s account for 80%% of %s" % (
                            _count(k80), _count(len(tot)), sh, what))[:120]}}
        summary = ("The top %s of %s levels of %s hold %s of %s (%s); %s leads with %s (%s). %s." % (
            _count(len(bars)), _count(len(tot)), sh, bars[-1]["cum_text"], what, data["total"]["text"],
            bars[0]["label"], bars[0]["text"], bars[0]["cum_text"], NB._cap(data["k80"]["text"])))
        if small:
            summary += " %s with fewer than 5 rows %s counted only in 'other'." % (
                _plural(small, "level"), "is" if small == 1 else "are")
        if merged:
            summary += " %s more %s in 'other' so that it rests on 5 or more rows." % (
                _count(merged), "level is" if merged == 1 else "levels are")
        sub = "%s by %s, largest first, with the running share of all %s" % (
            "Rows" if land is None else "Total %s" % mh, sh, data["total"]["text"])
        if k:
            sub += "; the top %s shown: the chart's size limit" % _count(len(bars))
        rows = [[b["label"], b["text"], b["cum_text"]] for b in bars]
        if other:
            rows.append(["other (%s)" % _plural(n_other, "level"), other["text"], other["cum_text"]])
        rows.append(["Total", data["total"]["text"], ""])
        return _record(
            "pareto", "%s by %s: the top %s and the long tail" % ("Rows" if land is None else NB._cap(mh), sh,
                                                                   _count(len(bars))), sub, "other",
            "How concentrated %s is across %s" % (what, sh), ["chart:ranked.%s" % seg] if rk else [], None, None,
            {"label": mh, "unit": ctx.unit(land) if land else "", "kind": "count" if land is None else "amount"}, data,
            {"cols": [sh, "Rows" if land is None else NB._cap(mh), "Running share"], "rows": rows}, summary,
            (small, "; ".join(x for x in (
                "%s with fewer than 5 rows %s counted only in 'other'" % (_plural(small, "level"), "is" if small == 1
                                                                         else "are") if small else "",
                "%s more %s in 'other' so that it rests on 5 or more rows" % (
                    _count(merged), "level is" if merged == 1 else "levels are") if merged else "") if x)),
            "The rows the engine kept, by %s" % sh,
            {"columns": [sh] + ([mh] if land else []), "rows": int(len(df)), "months": _span_months(ctx, ok),
             "op": "%s by %s; the top %s with 5 or more rows, the rest as other" % (
                 "rows counted" if land is None else "%s added up" % mh, sh, _count(len(bars)))})
    return _fit(build, max(0, len(ranked[:PARETO_BARS_MAX]) - 1))


BUILD: Dict[str, Callable[[Ctx, Optional[List[str]]], Dict[str, Any]]] = {
    "contribution_waterfall": _b_contribution, "pvm_waterfall": _b_pvm, "calendar_heatmap": _b_calendar,
    "change_heatmap": _b_change, "crosstab_heatmap": _b_crosstab, "theme_rating_heatmap": _b_theme,
    "correlation_heatmap": _b_correlation, "group_ranges": _b_group_ranges, "pareto": _b_pareto, "slope": _b_slope}


# ----------------------------------------------------------------------------- the plan's charts[]
def plan_items(raw: Any) -> List[Dict[str, Any]]:
    """plan.charts as _validate_plan keeps it: the first 12 items read, each {kind, columns, why} cut to its caps
    (kind 40 characters, at most 12 columns of 120, why 200 at a word). Nothing is refused here: the engine checks
    every item when it builds (validate_directive), and a refusal is never a plan signal."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for it in raw[:PLAN_CHARTS_READ]:
        if not isinstance(it, dict):
            out.append({"kind": "", "columns": [], "why": ""})
            continue
        cols = it.get("columns")
        cols = [str(c)[:120] for c in cols[:12] if isinstance(c, (str, int, float)) and not isinstance(c, bool)] \
            if isinstance(cols, list) else []
        out.append({"kind": str(it.get("kind") or "")[:40], "columns": cols, "why": _cut(it.get("why") or "", PLAN_WHY_MAX)})
    return out


def _args_fit(chart: str, n: int) -> bool:
    m = REGISTRY[chart]
    if m.get("repeat"):
        return n == 0 or m["repeat"][0] <= n <= m["repeat"][1]
    req = sum(1 for _r, opt in m["args"] if not opt)
    return req <= n <= len(m["args"])


def _roles(chart: str, n: int) -> List[str]:
    m = REGISTRY[chart]
    if m.get("repeat"):
        return [m["args"][0][0]] * n
    return [r for r, _o in m["args"]][:n]


def validate_directive(ctx: Ctx, item: Dict[str, Any], lim: Dict[str, Dict[str, Any]]) -> Tuple[str, List[str], str]:
    """(the menu name, the columns as landed names, "") for an item that may be built, or (name, [], why) for one
    refused: the menu name, the number of columns, privacy (a flagged column is never charted, and a refusal never
    names a withheld one), each column in the file, each one's role, then the chart's limit."""
    chart = str(item.get("kind") or "")
    cols = list(item.get("columns") or [])
    if chart not in REGISTRY:
        return chart, [], R_MENU
    if not _args_fit(chart, len(cols)):
        return chart, [], R_ARGS % REGISTRY[chart]["args_text"]
    # option B (Ctx.kept_text): the theme chart's text may be a kept free-text column; every other flagged column, and
    # a flagged column in any other part or chart, is refused
    kept = ctx.kept_text(cols[0]) if chart == "theme_rating_heatmap" and cols else None
    if any(ctx.is_flagged(c) for c in (cols[1:] if kept else cols)):
        return chart, [], R_PERSONAL
    lands = []
    for i, c in enumerate(cols):
        land = kept if (i == 0 and kept) else ctx.landed(c)
        if land is None or (land not in ctx.columns and not (i == 0 and kept)):
            return chart, [], R_MISSING
        lands.append(land)
    for land, role in zip(lands, _roles(chart, len(lands))):
        why = ctx.role_ok(land, role)
        if why:
            return chart, [], why
    if len(set(lands)) != len(lands):
        return chart, [], "the same column twice"
    lm = lim.get(chart) or {}
    # the chart limits never count a flagged column: a kept text's chart is checked by its build (20 texts or more)
    if lm and not lm.get("ok") and not kept:
        why = str(lm.get("why") or "")
        if chart == "pareto":
            why = _pareto_refusal(ctx, lands[0], why)
        return chart, [], why
    return chart, lands, ""


def _pareto_refusal(ctx: Ctx, land: str, why: str) -> str:
    """The Pareto's refusal for the category column the AI named. The limit is the file's (its why names the largest
    category the limits count, or none); when that is another column, the refusal says what keeps THIS one out, then
    what the Pareto needs and the largest category there is (pre-deploy pass, 30 Sep 2026: a Pareto of brand, 3,401
    values, was refused with "department has 6"). The limits count a text column as a category when it has 2 to 300
    values of at most 60 characters (the profile lists its levels: nl_browser._profile_facts), never a personal or
    flagged one. The limit's own why when it names this column, or when the column is not among those it read."""
    d = ctx.lim_detail or {}
    use, cats = d.get("usable") or {}, list(d.get("cats") or [])
    f = use.get(land)
    if f is None:
        return why
    big = sorted(((int(use[c].get("distinct") or 0), i, c) for i, c in enumerate(cats) if c in use),
                 key=lambda x: (-x[0], x[1]))
    if big and big[0][2] == land:
        return why
    h, n = str(f.get("header") or ctx.header(land)), int(f.get("distinct") or 0)
    if land in cats:
        col = "%s has %s" % (h, _plural(n, "level"))
    elif "top_values" not in f and n > PARETO_MAX_LEVELS:
        col = "%s has %s values; a Pareto reads at most %s" % (h, _count(n), _count(PARETO_MAX_LEVELS))
    elif "top_values" not in f and n > 300 and f.get("short"):
        col = ("%s has %s values; past 300, the chart limits count a category only when the plan types it a category "
               "or an entity" % (h, _count(n)))
    elif "top_values" not in f and n > 300:
        col = "%s has %s values; the chart limits count a category of 300 values at most" % (h, _count(n))
    elif "top_values" not in f and f.get("kind") == "text" and n > 0:
        col = "%s has values too long to count as a category's levels (the middle one is over 60 characters)" % h
    else:
        col = "the chart limits do not count %s as a category" % h
    best = ("the largest, %s, has %s" % (use[big[0][2]]["header"], _plural(big[0][0], "level")) if big
            else "no column counts as one")
    return "%s, and the Pareto needs a category of 8 or more levels (%s)" % (col, best)


def _score(ctx: Ctx, chart: str) -> int:
    kind = str((ctx.plan or {}).get("kind") or "other")
    k = RANK_KINDS.index(kind) if kind in RANK_KINDS else RANK_KINDS.index("other")
    s = RANK_BASE[chart][k]
    prim = ctx.plan_primary()
    if prim:
        uses = {"contribution_waterfall": "measure", "pvm_waterfall": "measure", "slope": "measure",
                "calendar_heatmap": "measure", "change_heatmap": "measure", "group_ranges": "measure",
                "pareto": "measure"}
        basis = (ctx.sc or {}).get("basis") or {}
        has, pm = ctx.primary_measure()
        m = basis.get("measure") if chart in ("contribution_waterfall", "pvm_waterfall", "slope") else pm
        if chart in uses and m == prim:
            s += 3
    ran = {str(a.get("type") or "") for a in ctx.analyses} | \
        {str(a.get("type") or "") for a in (ctx.plan or {}).get("analyses") or [] if isinstance(a, dict)}
    if any(RANK_ANALYSIS.get(t) == chart for t in ran):
        s += 2
    g = ctx.goal.lower()
    s += min(2, sum(1 for w in GOAL_KEYWORDS[chart] if w in g))
    return s


def _place(viz: Dict[str, Any], rec: Dict[str, Any], chosen_by: str, why: str) -> None:
    rec["id"] = "viz.%d.%s" % (len(viz["charts"]) + 1, rec["chart"])
    rec["chosen_by"] = chosen_by
    rec["why"] = _cut(why, PLAN_WHY_MAX)
    viz["charts"].append(_tidy(rec))


def _built(ctx: Ctx, chart: str, lands: Optional[List[str]]) -> Dict[str, Any]:
    """BUILD[chart], a record or Refused; an unexpected failure refuses this chart only and says what failed (with
    NL_BROWSER_STRICT set, as the tests set it, it stops the run instead, so a defect cannot hide as a refusal)."""
    import os
    try:
        return BUILD[chart](ctx, lands)
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - one chart never stops the report
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        raise Refused("the chart could not be built for this file (%s)" % type(exc).__name__)


# --------------------------------------------------------------------------- a long statistical table's charts
# A visitor's StatCan debt table (8 Oct 2026; nl_browser._reshape_long_panel reads it one column per series, one row a month)
# drew one chart, a correlation of levels that all rise: every other chart above needs a category column or 5 rows a month,
# and such a table has neither. Its series are published figures, one value a month, so these charts read them directly,
# each from the kinds every reader already draws (heatmap, waterfall, slope) and the same checks:
#  (1) change_heatmap: each series (rows, the file's order) by calendar year (columns), the % change of the year's published
#      months against the SAME months a year earlier; a cell rests on its matched months (n), 5 or more or "<5";
#  (2) contribution_waterfall: the lead series' change over the headline's two windows, split by the identity the agency's
#      own labels state ("A. Federal debt (accumulated deficit), (B - E)", "B. Net debt, (C - D)"), drawn only when that
#      identity holds in every month of the file (to the rounding of its figures, else the gap is its own step) and the
#      two windows' averages are the headline finding's own;
#  (3) slope: each series' first 12 months against its latest 12 (the averages of their published months): the story
#      over the whole file, which the headline (the latest 12 months against the 12 before) does not tell.
SERIES_ROWS_MAX = 12
# in each such chart's "why": the report writer's proxy (insight-proxy src/report.js SERIES_CHART_WHY, addUnplacedViz) adds a chart
# carrying it that the writer left out, so the shared report shows it as the page does. Change both together
SERIES_CHART_WHY = "a published series' own chart"
_FORMULA = re.compile(r"^\s*([A-Z])\.\s+.*\(\s*([A-Z])\s*([+-])\s*([A-Z])\s*\)\s*$")
_LETTER = re.compile(r"^\s*([A-Z])\.\s+")


def _series_lands(ctx: Ctx, lay: Dict[str, Any]) -> List[str]:
    """The long table's series as landed columns, in the file's order (the layout's file_order), numeric and not flagged."""
    by_header = {ctx.header(c): c for c in ctx.measures}
    out = []
    for h in lay.get("file_order") or lay.get("order") or []:
        land = by_header.get(str(h))
        if land and land in ctx.columns and land not in ctx.flag_land:
            out.append(land)
    return out


def _avg_by_month(ctx: Ctx, land: str) -> Dict[str, float]:
    """{YYYY-MM: the published value} of one series (one row a month in a long table read one column per series)."""
    v = ctx.nums(land)
    out: Dict[str, float] = {}
    for m, x in zip(ctx.month, v):
        if m and x == x and x is not None:
            out[str(m)] = float(x)
    return out


def _pct_text(v: float) -> str:
    return ("%+.1f%%" % v).replace("-", "−")


def _amount_text(v: float) -> str:
    s = format(int(round(v)), ",")
    return s.replace("-", "−") if v < 0 else s


def _b_series_change_heatmap(ctx: Ctx, lay: Dict[str, Any]) -> Dict[str, Any]:
    lands = _series_lands(ctx, lay)
    series = []
    for land in lands:
        bym = _avg_by_month(ctx, land)
        if len(bym) >= 24 and len(set(bym.values())) > 1:
            series.append((land, bym))
        if len(series) >= SERIES_ROWS_MAX:
            break
    if len(series) < 2:
        raise Refused("needs two or more series with 24 or more published months")
    years = sorted({m[:4] for _l, b in series for m in b})
    cols = [y for y in years[1:]][-HEAT_COLS_MAX:]
    if not cols:
        raise Refused("needs two or more calendar years")
    cells, rows = [], []
    for land, bym in series:
        row = []
        for y in cols:
            prev = str(int(y) - 1)
            months = [m for m in bym if m.startswith(y) and (prev + m[4:]) in bym]
            a = math.fsum(bym[prev + m[4:]] for m in months)
            b = math.fsum(bym[m] for m in months)
            if not months or a <= 0:
                row.append(_cell(None, 0, ""))
                continue
            pct = 100.0 * (b / a - 1.0)
            row.append(_cell(pct, len(months), _pct_text(pct)[:CELL_TEXT_MAX]))
        cells.append(row)
        rows.append(_short_label(ctx.header(land)))
    rows = _unique_labels(rows)
    data, supp = _grid(rows, cols, cells, "diverging", _pct_text, "Series", "Year")
    if _shown(data) == 0:
        raise Refused("no year has 5 or more months published in both it and the year before")
    shown = [(data["values"][i][j], rows[i], cols[j]) for i in range(len(rows)) for j in range(len(cols))
             if data["values"][i][j] is not None]
    hi = max(shown, key=lambda x: x[0])
    lo = min(shown, key=lambda x: x[0])
    summary = ("The largest rise: %s in %s (%s on the same months of %d); the largest fall: %s in %s (%s). Each cell compares a "
               "year's published months with the same months a year earlier; the figures are the agency's, not graded."
               % (hi[1], hi[2], _pct_text(hi[0]), int(hi[2]) - 1, lo[1], lo[2], _pct_text(lo[0])))
    return _record(
        "change_heatmap", "Year-on-year change of each series",
        "Each series' published months in a year against the same months a year earlier (%s to %s)" % (cols[0], cols[-1]),
        "other", "How each published series moved, year by year", [], None, None,
        {"label": "change on the same months a year earlier", "unit": "%", "kind": "change_pct"}, data,
        _grid_table("Series", rows, cols, data["text"]), summary,
        (supp, "A year with fewer than 5 months published in both it and the year before is not shown." if supp else ""),
        "The table's published series, one value a month, as the engine read them",
        {"columns": [ctx.header(l) for l, _b in series], "rows": len(ctx.frame), "months": None,
         "op": "each year's published months against the same months a year earlier"})


def _identity(ctx: Ctx, lands: List[str]) -> Optional[Tuple[str, List[Tuple[int, str]]]]:
    """(the lead series, [(sign, leaf series)]): the lead's formula in the agency's labels, expanded through the formulas of
    its terms ("A ... (B - E)" with "B ... (C - D)": A = C - D - E), or None when its label states none."""
    by_letter: Dict[str, str] = {}
    formula: Dict[str, Tuple[str, int, str]] = {}
    for land in lands:
        h = ctx.header(land)
        m = _LETTER.match(h)
        if m:
            by_letter.setdefault(m.group(1), land)
        f = _FORMULA.match(h)
        if f:
            formula[f.group(1)] = (f.group(2), 1 if f.group(3) == "+" else -1, f.group(4))
    lead = next((l for l in lands if _FORMULA.match(ctx.header(l))), None)
    if lead is None:
        return None

    def expand(letter: str, sign: int, depth: int) -> Optional[List[Tuple[int, str]]]:
        if depth > 6 or letter not in by_letter:
            return None
        if letter not in formula:
            return [(sign, by_letter[letter])]
        x, s2, y = formula[letter]
        a = expand(x, sign, depth + 1)
        b = expand(y, sign * s2, depth + 1)
        return None if a is None or b is None else a + b
    root = _LETTER.match(ctx.header(lead)).group(1)
    x, s2, y = formula[root]
    left, right = expand(x, 1, 1), expand(y, s2, 1)
    if left is None or right is None:
        return None
    return lead, left + right


def _b_series_bridge(ctx: Ctx, lay: Dict[str, Any]) -> Dict[str, Any]:
    lands = _series_lands(ctx, lay)
    got = _identity(ctx, lands)
    if got is None:
        raise Refused("no series' label states how it is made from the others (such as \"(B - E)\")")
    lead, terms = got
    if len(terms) > WATERFALL_PARTS_MAX:
        raise Refused("the identity has more terms than a waterfall shows")
    lm = _avg_by_month(ctx, lead)
    tm = [(s, l, _avg_by_month(ctx, l)) for s, l in terms]
    common = [m for m in lm if all(m in b for _s, _l, b in tm)]
    if len(common) < 24:
        raise Refused("the identity's series share fewer than 24 published months")
    # the identity in every shared month, to the rounding of the published figures (half a unit a term, at the table's decimals)
    gap = max(abs(lm[m] - math.fsum(s * b[m] for s, _l, b in tm)) for m in common)
    if gap > 0.5 * (len(terms) + 1):
        raise Refused("the identity its labels state does not hold in the published figures")
    end = max(lm)
    ey, emo = int(end[:4]), int(end[5:])
    def shift(y: int, mo: int, k: int) -> str:
        t = y * 12 + (mo - 1) - k
        return "%04d-%02d" % (t // 12, t % 12 + 1)
    latest_w = (shift(ey, emo, 11), end)
    prior_w = (shift(ey, emo, 23), shift(ey, emo, 12))
    inw = lambda m, w: w[0] <= m <= w[1]
    pm = [m for m in common if inw(m, prior_w)]
    lmn = [m for m in common if inw(m, latest_w)]
    if len(pm) < SMALL_CELL or len(lmn) < SMALL_CELL or set(pm) != {m for m in lm if inw(m, prior_w)} or \
            set(lmn) != {m for m in lm if inw(m, latest_w)}:
        raise Refused(R_RECON)
    avg = lambda b, ms: math.fsum(b[m] for m in ms) / len(ms)
    prior, latest = avg(lm, pm), avg(lm, lmn)
    # the totals are the headline's own (the finding's described averages), else the chart is not the headline's
    fid = next((f for f, x in ctx.findings.items() if f.endswith(".change") and
                ((x.get("inference") or {}).get("describe") or {}).get("prior") is not None and
                str((x.get("claim") or "")).endswith("latest 12 months against the 12 before") and
                ctx.header(lead) in str(ctx.pub(x.get("claim") or "")) + str(x.get("claim") or "")), None)
    if fid is None:
        fid = next((f for f in ctx.findings if f == "measure.%s.change" % lead), None)
    if fid is not None:
        dsc = (ctx.findings[fid].get("inference") or {}).get("describe") or {}
        for want, have in ((dsc.get("prior"), prior), (dsc.get("latest"), latest)):
            if want is not None and abs(float(want) - have) > 1e-6 * max(1.0, abs(have)):
                raise Refused(R_RECON)
    change = latest - prior
    parts = []
    for s, l, b in tm:
        d = s * (avg(b, lmn) - avg(b, pm))
        h = ctx.header(l)
        label = _short_label(("less " if s < 0 else "") + h)
        parts.append((label, d, _amount_text(d)))
    resid = change - math.fsum(p[1] for p in parts)
    if abs(resid) > RECONCILE_TOL * max(1.0, abs(change)):
        parts.append((UNALLOCATED_ROUNDING, resid, _amount_text(resid)))
    basis = {"split": "segment", "finding_id": fid, "column": None, "prior": list(prior_w), "latest": list(latest_w)}
    data = _waterfall_data((WATERFALL_START, 0.0, "0"), parts, (WATERFALL_TOTAL, change, _amount_text(change)),
                           (change, _amount_text(change)), basis)
    _check_waterfall(data)
    unit = ", ".join(lay.get("units") or [])[:20]
    lh = ctx.header(lead)
    big = max(parts, key=lambda p: abs(p[1]))
    summary = ("%s changed by %s between the average month of %s to %s and of %s to %s. By the identity its label states, "
               "the largest part is %s (%s). Exact arithmetic on the agency's figures; the change itself is graded %s."
               % (lh, _amount_text(change), prior_w[0], prior_w[1], latest_w[0], latest_w[1], big[0], big[2],
                  (ctx.findings.get(fid) or {}).get("grade") or "by the engine"))
    return _record(
        "contribution_waterfall", "What moved %s" % _short_label(lh, 60),
        "The change in its average month, %s to %s against %s to %s, split by the identity in the agency's labels"
        % (latest_w[0], latest_w[1], prior_w[0], prior_w[1]),
        "drove", str((ctx.findings.get(fid) or {}).get("claim") or lh), ["finding:" + fid] if fid else [], None,
        (ctx.findings.get(fid) or {}).get("grade"),
        {"label": lh[:80], "unit": unit, "kind": "amount"}, data,
        {"cols": ["Step", "Change"], "rows": [[s["label"], s["text"]] for s in data["steps"]]}, summary, (0, ""),
        "The table's published series and the identity its labels state (it holds in every published month: largest gap %s)"
        % _amount_text(gap),
        {"columns": [lh] + [ctx.header(l) for _s, l in terms], "rows": len(pm) + len(lmn),
         "months": [prior_w[0], latest_w[1]], "op": "each term's change in its average month, signed as the identity adds it"})


def _b_series_slope(ctx: Ctx, lay: Dict[str, Any]) -> Dict[str, Any]:
    lands = _series_lands(ctx, lay)
    rows, start, stop = [], None, None
    for land in lands:
        bym = _avg_by_month(ctx, land)
        if len(bym) < 24:
            continue
        ms = sorted(bym)
        f0 = ms[0]
        y0, m0 = int(f0[:4]), int(f0[5:])
        w0 = (f0, "%04d-%02d" % ((y0 * 12 + m0 + 10) // 12, (y0 * 12 + m0 + 10) % 12 + 1))
        e = ms[-1]
        ye, me = int(e[:4]), int(e[5:])
        w1 = ("%04d-%02d" % ((ye * 12 + me - 12) // 12, (ye * 12 + me - 12) % 12 + 1), e)
        a_ms = [m for m in ms if w0[0] <= m <= w0[1]]
        b_ms = [m for m in ms if w1[0] <= m <= w1[1]]
        if len(a_ms) < SMALL_CELL or len(b_ms) < SMALL_CELL:
            continue
        if start is None:
            start, stop = w0, w1
        elif (w0, w1) != (start, stop):
            continue                                      # one pair of windows for every row of the chart
        a = math.fsum(bym[m] for m in a_ms) / len(a_ms)
        b = math.fsum(bym[m] for m in b_ms) / len(b_ms)
        if a <= 0:
            continue
        rows.append({"label": _short_label(ctx.header(land)), "a": _r6(a), "b": _r6(b), "a_text": _amount_text(a),
                     "b_text": _amount_text(b), "change_text": _pct_text(100.0 * (b / a - 1.0))[:24]})
        if len(rows) >= SLOPE_ROWS_MAX:
            break
    if len(rows) < 2:
        raise Refused("needs two or more series published over the same span of 24 or more months")
    labels = _unique_labels([r["label"] for r in rows])
    for r, l in zip(rows, labels):
        r["label"] = l
    al, bl = "%s to %s" % start, "%s to %s" % stop
    top = rows[0]
    summary = ("%s: %s on average in %s, %s in %s (%s). Averages of each window's published months, the agency's own figures; "
               "the whole-file change is history, not a graded claim." % (top["label"], top["a_text"], al, top["b_text"], bl,
                                                                          top["change_text"]))
    unit = ", ".join(lay.get("units") or [])[:20]
    return _record(
        "slope", "Where each series started and where it is now",
        "The average published month of %s against %s" % (al, bl), "other",
        "The change over the whole file, which the headline's latest year against the year before does not show", [],
        None, None, {"label": "average published month", "unit": unit, "kind": "average"},
        {"rows": rows, "a_label": al[:40], "b_label": bl[:40]},
        {"cols": ["Series", al, bl, "Change"], "rows": [[r["label"], r["a_text"], r["b_text"], r["change_text"]] for r in rows]},
        summary, (0, ""), "The table's published series, one value a month, as the engine read them",
        {"columns": [r["label"] for r in rows], "rows": len(ctx.frame), "months": [start[0], stop[1]],
         "op": "the average of each window's published months"})


def _series_panel(rep: Dict[str, Any], ctx: Ctx, viz: Dict[str, Any], heat: int) -> int:
    """A long statistical table's own charts, placed first (see above); each refused with its reason. Returns the heatmaps."""
    lay = (rep.get("input") or {}).get("layout") or {}
    if lay.get("layout") != "long statistical table" or int(lay.get("kept") or 0) < 2 or ctx.month is None:
        return heat
    made = []
    for chart, fn in (("contribution_waterfall", _b_series_bridge), ("change_heatmap", _b_series_change_heatmap),
                      ("slope", _b_series_slope)):
        try:
            made.append(fn(ctx, lay))
        except Refused as exc:
            viz["refused"].append({"chart": chart, "columns": [], "why": _cut(str(exc), 300), "chosen_by": "engine"})
    if not made:
        return heat
    others = viz["charts"]
    viz["charts"] = []
    for rec in made:
        heat += int(rec["kind"] == "heatmap")
        _place(viz, rec, "engine", ENGINE_WHY + SERIES_CHART_WHY + ": " + REGISTRY[rec["chart"]]["what"])
    for rec in others:
        if len(viz["charts"]) >= VIZ_MAX:
            break
        if rec["chart"] in {r["chart"] for r in made}:
            continue
        if rec["kind"] == "heatmap" and heat >= HEAT_MAX:
            continue
        heat += int(rec["kind"] == "heatmap")
        _place(viz, rec, rec.get("chosen_by") or "engine", rec.get("why") or "")
    return heat


# --------------------------------------------------------------------------- a statistical table's charts
# WAVE 4, track A1 (plan/WAVE4-A-DESIGN.md section 2(10)). A table read by its structure (nl_structure) is charted from
# its published parts, never its raw rows: (1) a contribution waterfall per breakdown (the headline's verified parts,
# the largest first, the smaller ones folded into "other parts", and the UNALLOCATED step: the total less its published
# parts, the suppressed share), its table carrying each part's own change; (2) a year-on-year change heatmap of each
# breakdown's parts on the unadjusted series (a province by month), the smallest parts folded into "other"; (3) the
# month-on-month calendar of the seasonally adjusted slice (S2), refused when the table publishes no adjusted series
# (the unadjusted one shows its season, not momentum). The records keep the frozen spec's shape (spec.json: a step and
# `basis` take no other key, a shown cell needs n >= 5): the record's source says it is the structure's, a part's own
# change is in the waterfall's table, and a heatmap cell's n is the number of sum-checked PUBLISHED PARTS its figure adds
# up from (nl_structure.support: a province's retail total adds up from its 9 industries), 5 or more or the cell reads
# "<5". A chart over a table's raw rows (a table whose structure could not be used) is refused: its rows mix totals and
# parts.
R_CUBE_ROWS = "rows of a table of series mix totals and parts"
R_CUBE_HEAT = ("a crosstab of a table of series would add rows that mix totals and parts; the structure's breakdowns "
               "show where the change sits")
R_NO_SA = ("the table publishes no seasonally adjusted series: the month-on-month change of an unadjusted series shows "
           "its season, not momentum")
R_RATE_CAL = "a rate or an index changes in points, not percent: its published aggregate is charted by its trend"
R_PERIOD = "the chart reads monthly values; this table is %s (one value a %s), so it is not drawn"
STRUCTURE_WATERFALL_PARTS = 10          # the largest parts shown; the rest folded into "other parts", then unallocated
# the step the total less its published parts makes, in the words a reader needs: not "unallocated", a word of
# accounting, but what it is (wave 4, track B). Final integration pass (6 Oct 2026): the waterfall shows the DECOMPOSITION OF
# THE CHANGE (a "Start" total of 0, each part's contribution, a "Total change" total), so the parts fill the chart (the
# frozen spec's 0 on the axis holds by construction); the step is drawn only when it is 1% or more of the change (the share
# the worker names it from, G20), and says why it is there: the suppressed cells of the table, or the rounding of its
# published figures. Below that it is left out, and the figure's own sum-check line says "adds up; gap ..., rounding".
UNALLOCATED_STEP = "Not allocated: suppressed cells"
UNALLOCATED_ROUNDING = "Not allocated: rounding"
UNALLOCATED_SHOW_SHARE = 0.01
WATERFALL_START = "Start"
WATERFALL_TOTAL = "Total change"


def _b_structure_waterfall(rep: Dict[str, Any], bd: Dict[str, Any], items: Dict[str, Dict[str, Any]],
                           basis: Dict[str, Any], findings: Dict[str, Any]) -> Dict[str, Any]:
    key = bd["key"]
    pre = "contribution.%s." % key
    parts = [it for iid, it in items.items() if iid.startswith(pre) and not iid.endswith(".unallocated")]
    if len(parts) < 2:
        raise Refused("the breakdown has fewer than two parts")
    unal = items.get(pre + "unallocated")
    no_total = bool(bd.get("no_total"))                 # a table with no total row: its headline IS the sum of these parts
    hp, hl, hc = items.get("headline.prior"), items.get("headline.latest"), items.get("headline.change")
    if not hp or not hl or not hc or (unal is None and not no_total):
        raise Refused(R_RECON)
    parts.sort(key=lambda it: (-abs(float(it["value"])), str(it["segment"])))
    shown, rest = parts[:STRUCTURE_WATERFALL_PARTS], parts[STRUCTURE_WATERFALL_PARTS:]
    growth = {str(it["segment"]): it for iid, it in items.items() if iid.startswith("growth.%s." % key)}
    wlabels = _unique_labels([str(it["segment"]) for it in shown])
    change = float(hc["value"])
    u = float(unal["value"]) if unal is not None else 0.0
    # the not-allocated step: drawn when it is 1% or more of the change, or whenever leaving it out would break the
    # frozen spec's add-up (the steps add up to the change to 1e-6 of it); below both, left out and said in the sum-check
    # line. Its cause: suppressed cells when a part of this dimension lacks a month's value in either window (the sum-check's
    # `suppressed_parts`), else the rounding of the published figures (a table in thousands adds up to within a few units).
    n_mid = len(shown) + (1 if rest else 0)
    tol = RECONCILE_TOL * max(1.0, abs(change)) + 1e-6 * (n_mid + 2)
    omit = u == 0 or (abs(u) < UNALLOCATED_SHOW_SHARE * abs(change) and abs(u) <= 0.5 * tol)
    chk = next((c for c in (rep.get("estimand") or {}).get("sum_checks") or []
                if isinstance(c, dict) and c.get("dim") == bd["dim"] and c.get("total") == bd["parent"]), None)
    rounding = chk is not None and chk.get("suppressed_parts") == 0
    cause_words = "rounding" if rounding else "suppressed cells"
    step_label = UNALLOCATED_ROUNDING if rounding else UNALLOCATED_STEP
    zero_text = _money_like(0.0, str(hc["text"]), signed=False)
    gap_text = _money_like(abs(u), str(hc["text"]), signed=False, ref=change, exact_small=True)
    steps = [(wlabels[i], float(it["value"]), str(it["text"])) for i, it in enumerate(shown)]
    if rest:
        v = math.fsum(float(it["value"]) for it in rest)
        steps.append(("other parts (%d)" % len(rest), v, _money_like(v, str(hc["text"]))))
    if not omit:
        steps.append((step_label, u, str(unal["text"])))
    bs = {"split": "segment", "finding_id": basis.get("finding_id"), "column": str(bd["dim"])[:120],
          "prior": list(basis["windows"]["prior"]), "latest": list(basis["windows"]["latest"])}
    full = basis.get("complete") is not False
    k_used = int(basis.get("months_used") or 12)
    data = _waterfall_data((WATERFALL_START, 0.0, zero_text), steps, (WATERFALL_TOTAL, change, str(hc["text"])),
                           (change, str(hc["text"])), bs)
    _check_waterfall(data)
    measure = str(basis.get("measure") or "the total")
    what = str(basis.get("estimand") or measure).split(";")[0]
    grade = basis.get("grade")
    big = shown[:2]
    lead = " and ".join("%s %s" % (it["segment"], it["text"]) for it in big)
    pw, lw = basis["windows"]["prior"], basis["windows"]["latest"]
    import nl_structure as _N
    P = basis.get("period") or {"noun": "month", "nouns": "months", "window": 12, "step": 1}
    Sp = {"period": P}
    words = _N.period_words(Sp, None if full else k_used)
    if int(P.get("step") or 1) == 1:
        levels = ("went from %s in the 12 months before to %s in the latest 12 months" if full else
                  "went from %%s in the %d matched months before to %%s in the same months of the latest 12" % k_used) % (
                      hp["text"], hl["text"])
    else:
        levels = "went from %s in %s to %s in %s" % (hp["text"], words["prior"], hl["text"], words["latest"])
    closing = ("The parts add up to the change%s." % (" exactly" if u == 0 else " (gap %s, %s)" % (gap_text, cause_words))) \
        if omit else ("Not allocated (the total less its published parts, %s): %s." % (cause_words, unal["text"]))

    def summary_of(w: str, dim: str, ld: str) -> str:
        return "%s %s, a change of %s (descriptive arithmetic on published totals). The largest contributions by %s: " \
               "%s. %s Where the change sits, not what caused it." % (w, levels, hc["text"], dim, ld, closing)
    sdim = _short_dim(str(bd["dim"]))
    summary = summary_of(_cut(what, 120), str(bd["dim"]), lead)
    for w_, d_, l_ in ((_cut(what, 120), sdim, lead), (_cut(measure, 60), sdim, lead),
                       (_cut(measure, 60), sdim, "%s %s" % (shown[0]["segment"], shown[0]["text"]))):
        if len(summary) <= 400:
            break
        summary = summary_of(w_, d_, l_)
    rows = [[WATERFALL_START, zero_text, ""]]
    for i, it in enumerate(shown):
        g = growth.get(str(it["segment"]))
        rows.append([wlabels[i], str(it["text"]), str(g["text"]) if g else "n/a"])
    if rest:
        rows.append([steps[len(shown)][0], steps[len(shown)][2], ""])
    if not omit:
        rows.append([step_label, str(unal["text"]), ""])
    rows.append([WATERFALL_TOTAL, str(hc["text"]), ""])
    fid = str(basis.get("finding_id") or "")
    anchors = (["finding:" + fid] if fid in findings else []) + ["scenario:headline.prior"] + \
        ["scenario:" + str(it["id"]) for it in shown] + (["scenario:" + str(unal["id"])] if unal is not None else []) + \
        ["scenario:headline.latest", "scenario:headline.change"]
    sub_levels = "%s (%s to %s) to %s (%s to %s)" % (hp["text"], _N.pkey(Sp, pw[0]), _N.pkey(Sp, pw[1]), hl["text"],
                                                      _N.pkey(Sp, lw[0]), _N.pkey(Sp, lw[1])) if full else \
        "%s to %s (%d matched %s of each window)" % (hp["text"], hl["text"], k_used, P["nouns"] if k_used != 1 else P["noun"])
    return _record(
        "contribution_waterfall", "Where the change in %s came from, by %s" % (_cut(measure, 60), bd["dim"]),
        "%s went from %s; each published part's contribution to the %s change" % (
            _cut(measure, 40), sub_levels, hc["text"]),
        "drove", str((findings.get(fid) or {}).get("claim") or what), anchors, None, grade,
        {"label": measure, "unit": "", "kind": "amount"}, data,
        {"cols": ["Step", "Contribution", "Own change"], "rows": rows}, summary,
        (0, "%d smaller parts folded into 'other parts'" % len(rest) if rest else ""),
        ("structure: the parts of the table by %s (it has no total row: the headline is built by adding them)" % bd["dim"]
         if no_total else
         "structure: the published parts of %s by %s (each total checked against its parts: adds up%s)" % (
             bd["parent"], bd["dim"], "" if u == 0 else "; gap %s, %s" % (gap_text, cause_words))) if omit else
        ("structure: the published parts of %s by %s (each total checked against its parts); the not-allocated step is "
         "the total less its published parts (%s)" % (bd["parent"], bd["dim"], cause_words)),
        {"columns": [measure, str(bd["dim"])], "rows": None, "months": [basis["windows"]["prior"][0],
                                                                         basis["windows"]["latest"][1]],
         "op": "each part's %s total in each window, from the table's own series" % (
             "annual" if int(P.get("window") or 12) == 1 else "%d-%s" % (int(P.get("window") or 12), P["noun"]))})


def _short_dim(dim: str, n: int = 30) -> str:
    """A dimension's name in a sentence: its own abbreviation in brackets when the name is long ("... (NAICS)" is "NAICS")."""
    if len(dim) <= n:
        return dim
    m = re.search(r"\(([^()]{2,12})\)\s*$", dim)
    return m.group(1) if m else _cut(dim, n)


def _money_like(v: float, like: str, signed: bool = True, ref: Optional[float] = None, exact_small: bool = False) -> str:
    """A figure written as the headline's change is ("+$1.2B"): its currency sign and suffix. ref: the figure whose scale
    it is written in; exact_small: a gap that would read as zero there is written out ("$1,000")."""
    import nl_structure as NST
    S = {"measure": {"type": "flow", "currency": like.lstrip("+" + MINUS).startswith("$"), "uom": "Dollars"}}
    return NST.money(v, S, signed=signed, ref=ref, exact_small=exact_small)


def _short_label(s: Any, n: int = LABEL_MAX) -> str:
    """A member's label in at most n characters with its trailing code kept ("... leather goods retailers [458]")."""
    s = " ".join(str(s or "").split())
    if len(s) <= n:
        return s
    m = re.search(r"\s*(\[[^\[\]]{1,12}\])\s*$", s)
    tail = (" " + m.group(1)) if m else ""
    head = s[:len(s) - len(m.group(0))] if m else s
    return head[:max(1, n - len(tail) - 1)].rstrip() + "\u2026" + tail


def _unique_labels(names: List[str]) -> List[str]:
    out, seen = [], set()
    for x in names:
        lab = _short_label(x)
        k = 2
        while lab in seen:
            lab = _short_label(x, LABEL_MAX - 4) + " (%d)" % k
            k += 1
        seen.add(lab)
        out.append(lab)
    return out


def _b_structure_yoy(rep: Dict[str, Any], S: Dict[str, Any], where: Dict[str, Any], bd: Dict[str, Any],
                     basis: Dict[str, Any], findings: Dict[str, Any]) -> Dict[str, Any]:
    """A breakdown's parts by month: each part's published series against the same month a year before, in percent. The
    headline's other dimensions are held at the slice (an unadjusted slice makes the same month a year before a
    like-for-like comparison); the 11 largest parts are rows and the rest one "other" row (a month where all of them
    have a value). A cell's n is the number of published parts its figure adds up from (nl_structure.support)."""
    import numpy as np
    import nl_structure as NST
    per = (basis.get("period") or {})
    if int(per.get("step") or 1) != 1:
        raise Refused(R_PERIOD % ({"quarter": "quarterly", "half-year": "half-yearly", "year": "annual"}.get(per.get("kind"), per.get("kind")),
                                  per.get("noun")))
    if (rep.get("estimand") or {}).get("reconciles") is False:       # None: the engine charted no series to compare with
        raise Refused(R_RECON)
    dim = str(bd["dim"])
    months, _tot = NST._monthly(S, where)
    if not months:
        raise Refused("no dated rows")
    pos = {m: i for i, m in enumerate(months)}
    span = NB._month_range(months[0], months[-1])
    if len(span) < CHANGE_MIN_MONTHS:
        raise Refused(REGISTRY["change_heatmap"]["limit_needs"] + "; the table spans %s months" % _count(len(span)))
    win = basis["windows"]
    parts = []
    for p in bd["parts"]:
        w = dict(where, **{dim: p})
        _m, v = NST._monthly(S, w)
        lat, _k = NST.window_figure(S, _m, v, win["latest"])
        parts.append((str(p), v, NST.support(S, w)[1], abs(float(lat or 0.0))))
    parts.sort(key=lambda t: (-t[3], t[0]))
    if len(parts) < 2 or len(parts) > 400:
        raise Refused("%s has %s" % (dim, _plural(len(parts), "part")))
    shown, folded = (parts[:CROSS_ROWS - 1], parts[CROSS_ROWS - 1:]) if len(parts) > CROSS_ROWS else (parts, [])
    names = [t[0] for t in shown]
    series = [(t[1], t[2]) for t in shown]
    if folded:
        allv = np.vstack([t[1] for t in folded])
        ok = ~np.isnan(allv).any(axis=0)
        series.append((np.where(ok, np.nansum(allv, axis=0), np.nan), np.sum(np.vstack([t[2] for t in folded]), axis=0)))
        names.append("other (%d parts)" % len(folded))
    labels = _unique_labels(names)
    cols_all = [m for m in span if NB._shift_month(m, -12) >= span[0]][-CHANGE_COLS:]
    measure = str(basis.get("measure") or "the total")
    flow = NST.sums_over_time(S["measure"])
    adj = next((d for d in S["dims"] if d["role"] == "adjustment"), None)
    nsa_note = "; unadjusted, so the same month a year before is a like-for-like comparison" \
        if adj is not None and where.get(adj["column"]) == adj.get("nsa") else ""
    fid = str(basis.get("finding_id") or "")
    fold_txt = ("; %s folded into 'other': %s" % (_plural(len(folded), "smaller part"), ", ".join(
        _short_label(t[0], 24) for t in folded[:6]) + (", ..." if len(folded) > 6 else ""))) if folded else ""

    def build(k: int) -> Dict[str, Any]:
        cm = cols_all[k:]
        cells = []
        for v, sup in series:
            row = []
            for m in cm:
                q = NB._shift_month(m, -12)
                if m not in pos or q not in pos:
                    row.append((None, "", 0, 0))
                    continue
                a, b = float(v[pos[q]]), float(v[pos[m]])
                if a != a or b != b or a <= 0:
                    row.append((None, "", 0, 0))
                    continue
                x = 100.0 * (b / a - 1.0)
                row.append(_cell(x, int(min(sup[pos[m]], sup[pos[q]])), _chg(_r6(x), "%")))
            cells.append(row)
        data, supp = _grid(labels, cm, cells, "diverging", lambda x: _chg(_r6(x), "%"), dim, "Month")
        if 2 * _shown(data) < len(labels) * len(cm):
            raise Refused("fewer than half its cells rest on 5 or more published parts (each figure's n is the sum-checked "
                          "parts it adds up from; a leaf of the table has none)")
        shown_c = [(data["values"][i][j], i, j) for i in range(len(labels)) for j in range(len(cm))
                   if data["values"][i][j] is not None]
        up, dn = max(shown_c), min(shown_c)
        summary = "The largest rise on the year before is %s in %s (%s) and the largest fall %s in %s (%s)." % (
            labels[up[1]], cm[up[2]], data["text"][up[1]][up[2]], labels[dn[1]], cm[dn[2]], data["text"][dn[1]][dn[2]])
        if supp:
            summary += " %s %s on fewer than 5 published parts and %s not shown." % (
                _plural(supp, "cell"), "rests" if supp == 1 else "rest", "is" if supp == 1 else "are")
        what = measure if measure.lower().startswith("total") else ("total %s" % measure if flow else "average %s" % measure)
        sub = "Each %s's %s in the month against the same month a year before, in percent%s%s" % (
            dim, what, nsa_note, fold_txt)
        if k:
            sub += "; %s to %s shown: the chart's size limit" % (cm[0], cm[-1])
        anchors = (["finding:" + fid] if fid in findings else []) + ["scenario:headline.latest"]
        return _record(
            "change_heatmap", "%s by %s and month, change on the year before" % (NB._cap(measure), dim), sub, "drove",
            str((findings.get(fid) or {}).get("claim") or "%s by %s" % (NB._cap(measure), dim)), anchors, None,
            basis.get("grade"), {"label": measure, "unit": "%", "kind": "change_pct"}, data,
            _grid_table(dim, labels, cm, data["text"]), summary,
            (supp, "%s on fewer than 5 published parts" % _plural(supp, "cell") if supp else ""),
            "structure: each part of %s by month (the published series; each figure's n is the sum-checked parts it adds "
            "up from)" % bd["parent"],
            {"columns": [measure, dim, str(S["date"]["column"])], "rows": None,
             "months": [NB._shift_month(cm[0], -12), cm[-1]],
             "op": "published %s by %s and month, percent change on the same month a year before" % (measure, dim)})
    return _fit(build, len(cols_all) - 1)


def _momentum_where(S: Dict[str, Any], where: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The slice's seasonally adjusted copy: its adjustment dimension at the adjusted member (or the slice itself when it
    already is that), None when the table has no adjusted series for this slice."""
    import nl_structure as NST
    adj = next((d for d in S["dims"] if d["role"] == "adjustment"), None)
    if adj is None or not adj.get("sa"):
        return None
    w = dict(where)
    if w.get(adj["column"]) not in (adj.get("nsa"), adj.get("sa")):
        return None
    w[adj["column"]] = adj["sa"]
    return w if NST._series_exist(S, w) else None


def _b_structure_mom(rep: Dict[str, Any], S: Dict[str, Any], where: Dict[str, Any], basis: Dict[str, Any],
                     findings: Dict[str, Any], momentum: Optional[str] = None) -> Dict[str, Any]:
    """The month-on-month calendar of the seasonally adjusted slice (S2, the momentum slice): month across, year down, the
    change from the month before in percent. The unadjusted series is never used for momentum."""
    import nl_structure as NST
    per = (basis.get("period") or {})
    if int(per.get("step") or 1) != 1:
        raise Refused(R_PERIOD % ({"quarter": "quarterly", "half-year": "half-yearly", "year": "annual"}.get(per.get("kind"), per.get("kind")),
                                  per.get("noun")))
    if S["measure"]["type"] in ("rate", "index"):
        raise Refused(R_RATE_CAL)
    w2 = _momentum_where(S, where)
    plan_sl = NST.slice_by_id(S, momentum) if momentum else None        # the plan's momentum_slice id, when it is adjusted
    adj0 = next((d for d in S["dims"] if d["role"] == "adjustment"), None)
    if plan_sl is not None and adj0 is not None and plan_sl["where"].get(adj0["column"]) == adj0.get("sa") and \
            NST._series_exist(S, plan_sl["where"]):
        w2 = dict(plan_sl["where"])
    if w2 is None:
        raise Refused(R_NO_SA)
    months, v = NST._monthly(S, w2)
    sm, sup = NST.support(S, w2)
    if not months:
        raise Refused("no dated rows")
    pos = {m: i for i, m in enumerate(months)}
    span = NB._month_range(months[0], months[-1])
    years_all = sorted({m[:4] for m in span})
    if len(span) < CALENDAR_MIN_MONTHS or len(years_all) < 2:
        raise Refused(REGISTRY["calendar_heatmap"]["limit_needs"] + "; the table spans %s months" % _count(len(span)))
    measure = str(basis.get("measure") or "the total")
    first = max(0, len(years_all) - CALENDAR_YEARS)
    adj = next(d for d in S["dims"] if d["role"] == "adjustment")
    adds = {d["column"]: d.get("sa_adds_up") for d in S["dims"] if d["role"] in ("partition", "hierarchy")}
    sa_txt = "; its parts add up" if adds and all(x is True for x in adds.values()) else ""

    def build(k: int) -> Dict[str, Any]:
        years = years_all[first + k:]
        cells = []
        for y in years:
            row = []
            for j in range(12):
                m = "%s-%02d" % (y, j + 1)
                q = NB._shift_month(m, -1)
                if m not in pos or q not in pos:
                    row.append((None, "", 0, 0))
                    continue
                a, b = float(v[pos[q]]), float(v[pos[m]])
                if a != a or b != b or a <= 0:
                    row.append((None, "", 0, 0))
                    continue
                x = 100.0 * (b / a - 1.0)
                row.append(_cell(x, int(min(sup[pos[m]], sup[pos[q]])), _chg(_r6(x), "%")))
            cells.append(row)
        data, supp = _grid(years, list(MONTHS), cells, "diverging", lambda x: _chg(_r6(x), "%"), "Year", "Month")
        shown = [(data["values"][i][j], i, j) for i in range(len(years)) for j in range(12)
                 if data["values"][i][j] is not None]
        if not shown:
            raise Refused("no month rests on 5 or more published parts")
        up, dn = max(shown), min(shown)
        yr = "%s and %s" % (years[0], years[-1]) if len(years) == 2 else "%s to %s" % (years[0], years[-1])
        summary = ("The largest rise on the month before is %s %s (%s) and the largest fall %s %s (%s), in the seasonally "
                   "adjusted series." % (MONTHS[up[2]], years[up[1]], data["text"][up[1]][up[2]], MONTHS[dn[2]],
                                         years[dn[1]], data["text"][dn[1]][dn[2]]))
        if supp:
            summary += " %s %s on fewer than 5 published parts and %s not shown." % (
                _plural(supp, "month"), "rests" if supp == 1 else "rest", "is" if supp == 1 else "are")
        sub = "Change in the seasonally adjusted %s from the month before, in percent%s" % (measure, sa_txt)
        if k or first:
            sub += "; %s to %s shown: %s" % (years[0], years[-1], "the chart's size limit" if k else
                                             "the latest %d years" % CALENDAR_YEARS)
        dm = [m for m in months if m[:4] >= years[0]]
        return _record(
            "calendar_heatmap", "%s month on month, seasonally adjusted, %s" % (NB._cap(measure), yr), sub, "other",
            "month-on-month momentum of the seasonally adjusted %s" % measure, [], None, basis.get("grade"),
            {"label": measure, "unit": "%", "kind": "change_pct"}, data, _grid_table("Year", years, list(MONTHS), data["text"]),
            summary, (supp, "%s on fewer than 5 published parts" % _plural(supp, "month") if supp else ""),
            "structure: the seasonally adjusted slice (%s) by month; each figure's n is the sum-checked parts it adds up "
            "from" % adj["sa"],
            {"columns": [measure, str(S["date"]["column"])], "rows": None, "months": [dm[0], dm[-1]],
             "op": "the seasonally adjusted %s by calendar month, percent change from the month before" % measure})
    return _fit(lambda k: build(k), len(years_all) - first - 2)


def _build_structure(rep: Dict[str, Any], ctx_in: Dict[str, Any], viz: Dict[str, Any]) -> Dict[str, Any]:
    """The charts of a table read by its structure: a waterfall and a year-on-year heatmap per breakdown and the
    seasonally adjusted month-on-month calendar; a plan's chart of a structure dimension is the structure's own when one
    matches, else refused with its reason."""
    sc = rep.get("scenarios") or {}
    basis = sc.get("basis") or {}
    items = {str(it.get("id")): it for it in sc.get("items") or [] if isinstance(it, dict)}
    findings = {str(f.get("id")): f for f in rep.get("findings") or [] if isinstance(f, dict)}
    st_in = ctx_in.get("structure") or {}
    S = st_in.get("S") or {}
    where = dict(st_in.get("where") or {})
    if S:
        import nl_structure as _NST
        S = _NST.local(S, where)               # the slice's own measure (a member of a measure dimension)
    waterfalls: Dict[str, Dict[str, Any]] = {}
    heats: Dict[str, Dict[str, Any]] = {}
    calendar: Optional[Dict[str, Any]] = None
    if basis.get("source") == "structure":
        for bd in basis.get("breakdowns") or []:
            dim = str(bd["dim"])
            try:
                waterfalls[dim] = _b_structure_waterfall(rep, bd, items, basis, findings)
            except Refused as exc:
                viz["refused"].append({"chart": "contribution_waterfall", "columns": [dim],
                                       "why": _cut(str(exc), 300), "chosen_by": "engine"})
            full = next((b for b in S.get("breakdowns") or [] if b.get("id") == bd.get("id")), None)
            if full is None:
                continue
            try:
                heats[dim] = _b_structure_yoy(rep, S, where, full, basis, findings)
            except Refused as exc:
                viz["refused"].append({"chart": "change_heatmap", "columns": [dim], "why": _cut(str(exc), 300),
                                       "chosen_by": "engine"})
        try:
            calendar = _b_structure_mom(rep, S, where, basis, findings, st_in.get("momentum_slice"))
        except Refused as exc:
            viz["refused"].append({"chart": "calendar_heatmap", "columns": [], "why": _cut(str(exc), 300),
                                   "chosen_by": "engine"})
    dims = {d["column"] for d in S.get("dims") or []}
    chosen_ai: Dict[Tuple[str, str], str] = {}
    plan = ctx_in.get("plan") if isinstance(ctx_in.get("plan"), dict) else {}
    for it in (plan.get("charts") or [])[:PLAN_CHARTS_READ]:
        chart = str(it.get("kind") or "")
        cols = [str(c) for c in it.get("columns") or []]
        hit = [c for c in cols if c in dims]
        ref = {"chart": chart[:40], "columns": cols[:12], "why": "", "chosen_by": "ai"}
        if chart == "contribution_waterfall" and hit and hit[0] in waterfalls:
            chosen_ai[(chart, hit[0])] = str(it.get("why") or "")
            continue
        if chart == "change_heatmap" and hit and hit[0] in heats:
            chosen_ai[(chart, hit[0])] = str(it.get("why") or "")
            continue
        if chart == "calendar_heatmap" and calendar is not None and not hit:
            chosen_ai[(chart, "")] = str(it.get("why") or "")
            continue
        if chart == "crosstab_heatmap":
            ref["why"] = R_CUBE_HEAT
        elif chart in ("change_heatmap", "calendar_heatmap") and not hit:
            ref["why"] = next((str(r["why"]) for r in viz["refused"] if r.get("chart") == chart),
                              "the chart reads the table's rows, which mix totals and parts; the structure's own "
                              "charts show the headline's parts")
        elif hit:
            ref["why"] = "%s: %s" % (R_CUBE_ROWS, "the structure's breakdowns show %s instead" % hit[0])
        else:
            ref["why"] = "the chart reads the table's rows, which mix totals and parts; the headline series is charted " \
                         "by the engine's own trend chart"
        viz["refused"].append(ref)
    order = [("contribution_waterfall", d, r) for d, r in waterfalls.items()] + \
            [("change_heatmap", d, r) for d, r in heats.items()] + \
            ([("calendar_heatmap", "", calendar)] if calendar is not None else [])
    heat = 0
    for chart, dim, rec in order:
        if len(viz["charts"]) >= VIZ_MAX:
            viz["refused"].append({"chart": chart, "columns": [dim] if dim else [], "why": R_MAX, "chosen_by": "engine"})
            continue
        if rec["kind"] == "heatmap" and heat >= HEAT_MAX:
            viz["refused"].append({"chart": chart, "columns": [dim] if dim else [], "why": R_HEAT, "chosen_by": "engine"})
            continue
        heat += int(rec["kind"] == "heatmap")
        if (chart, dim) in chosen_ai:
            _place(viz, rec, "ai", str(chosen_ai[(chart, dim)]) or "the plan asked for this chart of the table's parts")
        else:
            _place(viz, rec, "engine", ENGINE_WHY + {
                "contribution_waterfall": "where the change in the headline came from, by the table's own published "
                                          "parts of %s" % dim,
                "change_heatmap": "how each published part of %s moved against the year before" % dim,
                "calendar_heatmap": "the month-on-month momentum of the seasonally adjusted series"}[chart])
    viz["chosen_by"] = "ai" if any(c.get("chosen_by") == "ai" for c in viz["charts"]) else \
        ("engine" if viz["charts"] else "none")
    rep["charts"] = list(rep.get("charts") or []) + v2_records(viz)
    drop_driver_line(rep, viz)
    return viz


def build(rep: Dict[str, Any], ctx_in: Dict[str, Any]) -> Dict[str, Any]:
    """rep["viz"] = {version, charts, refused, chosen_by}, and each record appended to rep["charts"] as a rule "V"
    record. ctx_in: {"r" (the engine's analysis, for its claims), "reading", "plan" (the validated AI plan), "pub" (the
    report's scrubber), "goal", "structure" (a table read by its structure: its slice's run)}."""
    viz = blank()
    if ctx_in.get("structure") is not None:
        return _build_structure(rep, ctx_in, viz)
    st = rep.get("structure") if isinstance(rep.get("structure"), dict) else None
    if st is not None and (st.get("kind") == "cube_incomplete" or (st.get("kind") == "cube" and st.get("usable"))):
        # a table of series read row by row (its structure could not be used): no chart averages or totals its rows
        viz["refused"].append({"chart": "all", "columns": [], "why": R_CUBE_ROWS, "chosen_by": "engine"})
        return viz
    if not (rep.get("downloads") or {}).get("clean_csv"):
        return viz
    ctx = Ctx(rep, dict(ctx_in, claims=claims_of(ctx_in.get("r"))))
    if ctx.frame is None or not len(ctx.frame):
        viz["refused"].append({"chart": "all", "columns": [], "why": "the engine kept no rows", "chosen_by": "engine"})
        return viz
    lim = run_limits(ctx)
    items = list((ctx.plan or {}).get("charts") or [])
    seen = set()
    heat = 0
    for it in items[:PLAN_CHARTS_READ]:
        chart, lands, why = validate_directive(ctx, it, lim)
        shown_cols = [("a column you withheld" if ctx.is_withheld(c) else str(c))[:120] for c in it.get("columns") or []]
        ref = {"chart": chart[:40], "columns": shown_cols, "why": "", "chosen_by": "ai"}
        key = (chart, tuple(lands) if lands else tuple(str(c) for c in it.get("columns") or []))
        if key in seen:
            why = R_TWICE
        seen.add(key)
        if not why:
            if len(viz["charts"]) >= VIZ_MAX:
                why = R_MAX
            elif REGISTRY[chart]["kind"] == "heatmap" and heat >= HEAT_MAX:
                why = R_HEAT
        if not why:
            try:
                rec = _built(ctx, chart, lands)
                heat += int(rec["kind"] == "heatmap")
                w = str(ctx.pub(it.get("why") or "")).strip()
                if w.startswith(ENGINE_WHY):
                    w = w[len(ENGINE_WHY):]
                _place(viz, rec, "ai", w)
                continue
            except Refused as exc:
                why = str(exc)
        ref["why"] = _cut(why, 300)
        viz["refused"].append(ref)
    if viz["charts"]:
        viz["chosen_by"] = "ai"
    else:
        ranked = sorted(MENU, key=lambda c: (-_score(ctx, c), MENU.index(c)))
        for chart in ranked:
            if len(viz["charts"]) >= VIZ_AUTO:
                break
            if not (lim.get(chart) or {}).get("ok"):
                continue
            if REGISTRY[chart]["kind"] == "heatmap" and heat >= HEAT_MAX:
                continue
            try:
                rec = _built(ctx, chart, None)
            except Refused:
                continue
            heat += int(rec["kind"] == "heatmap")
            _place(viz, rec, "engine", ENGINE_WHY + REGISTRY[chart]["what"])
        viz["chosen_by"] = "engine" if viz["charts"] else "none"
    # a long statistical table's own charts (one value a month per series: the charts above need categories), placed first
    chosen = viz["chosen_by"]
    heat = _series_panel(rep, ctx, viz, heat)
    if viz["charts"] and chosen == "none":
        viz["chosen_by"] = "engine"
    rep["charts"] = list(rep.get("charts") or []) + v2_records(viz)
    drop_driver_line(rep, viz)
    mend_catmonth_why(rep, viz)
    return viz


def _has_waterfall(viz: Dict[str, Any]) -> bool:
    return any(isinstance(c, dict) and c.get("chart") == "contribution_waterfall" for c in viz.get("charts") or [])


def drop_driver_line(rep: Dict[str, Any], viz: Dict[str, Any]) -> None:
    """§5's rule #2b (the driver waterfall) is suppressed with "which segments drive a change is not computed in this
    release" before the registry runs; a contribution_waterfall record computes exactly that, so the line goes when
    one was built (integration pass, 30 Sep 2026: the report listed it beside the waterfall). The record, a rule "V"
    entry in rep["charts"], then accounts for #2b."""
    if _has_waterfall(viz):
        rep["charts_suppressed"] = [s for s in rep.get("charts_suppressed") or []
                                    if not (isinstance(s, dict) and s.get("rule") == "#2b")]


def mend_catmonth_why(rep: Dict[str, Any], viz: Dict[str, Any]) -> None:
    """§5's rule #8 (the category-by-month heatmap) is made before the registry runs, its reason saying no
    contribution waterfall was built (nl_browser.CATMONTH_NO_WATERFALL); when one was, the clause says the waterfall
    shows where the change sits (pre-deploy pass, 30 Sep 2026: the reason said drivers were "not computed in this
    release" beside the waterfall that computes them). Only that clause changes; the map still flags no reversal."""
    if not _has_waterfall(viz):
        return
    was, now = NB.CATMONTH_WHY % NB.CATMONTH_NO_WATERFALL, NB.CATMONTH_WHY % NB.CATMONTH_WATERFALL
    for c in rep.get("charts") or []:
        if isinstance(c, dict) and str(c.get("id") or "").startswith("catmonth.") and c.get("why_shown") == was:
            c["why_shown"] = now


def run_limits(ctx: Ctx) -> Dict[str, Dict[str, Any]]:
    """The chart limits over this run's rows (the profile's rule on the engine's reading of the file it read, with
    the plan's types): {chart: {ok, why}}."""
    R = ctx.reading
    if R is None:
        return {c: {"ok": False, "why": REGISTRY[c]["limit_needs"] + "; the engine's reading is not available"}
                for c in MENU}
    headers = list(R.land) or [R.header(c) for c in R.values.columns]
    hidden = {str(f.get("column")): str(f.get("decision")) for f in ctx.flagged if f.get("decision") != "keep"}
    facts = NB._profile_facts(R, headers, hidden)
    priv = NB._profile_privacy(facts, ctx.flagged, None)
    # a kept column is flagged too: never counted, never named
    for f in facts:
        if f["landed"] in ctx.flag_land and f["header"] not in priv:
            priv[f["header"]] = ("keep", "flagged")
    rows = int(R.n)
    time, _al = NB._time_and_limits(facts, {k: v for k, v in priv.items() if v[0] != "keep"}, rows)
    if time and any(f["header"] == time.get("column") and f["landed"] in ctx.flag_land for f in facts):
        time = None
    stats = profile_stats(R, facts)
    detail: Dict[str, Any] = {}
    lst = limits(facts, priv, time, stats, rows, ctx.plan_by, ctx.additive, detail)
    ctx.lim_detail = detail
    return {x["chart"]: x for x in lst}


def v2_records(viz: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each record as a rep["charts"] entry: {id, rule "V", type "viz", title, view "manager", default_visible true,
    finding_ids (the anchors' finding ids), why_shown (its why), source, data: the record}. Drawn in the page's viz
    area; not counted among section 3's six manager records."""
    out = []
    for rec in viz.get("charts") or []:
        out.append({"id": rec["id"], "rule": "V", "type": "viz", "title": rec["title"], "view": "manager",
                    "default_visible": True,
                    "finding_ids": [a[len("finding:"):] for a in rec.get("anchors") or [] if a.startswith("finding:")],
                    "why_shown": rec.get("why") or ("chosen by the AI plan" if rec.get("chosen_by") == "ai"
                                                    else "chosen by the engine"),
                    "source": rec.get("source") or "", "data": rec})
    return out


def cards_for_ai(viz: Any, start: int = 1) -> List[Dict[str, Any]]:
    """A card per chart for the report writer, never its data: {n, id, chart, kind, title, section, supports,
    anchors, grade, parent_grade, summary, table (at most 12 rows)}. (The worker makes the same card from the whole
    record it receives in results_for_ai.charts; the data stay there for the page, the PDF and the share viewer.)"""
    recs = viz.get("charts") if isinstance(viz, dict) else viz
    out = []
    for i, r in enumerate(recs or []):
        if not isinstance(r, dict):
            continue
        t = r.get("table") or {}
        out.append({"n": start + i, "id": r.get("id"), "chart": r.get("chart"), "kind": r.get("kind"),
                    "title": r.get("title"), "section": r.get("section"), "supports": r.get("supports"),
                    "anchors": list(r.get("anchors") or []), "grade": r.get("grade"),
                    "parent_grade": r.get("parent_grade"), "summary": r.get("summary"),
                    "table": {"cols": list(t.get("cols") or []), "rows": [list(x) for x in (t.get("rows") or [])[:CARD_TABLE_ROWS]]}})
    return out


WITHHELD_COLUMN = "[withheld column]"
# the fields that are names, not words (never rewritten): the record's own keys and codes
_SEND_KEEP = frozenset(("id", "chart", "kind", "section", "grade", "parent_grade", "chosen_by", "anchors", "scale",
                        "split", "finding_id", "prior", "latest", "months", "unit"))
# each printed string's cap, by its key (a string over it after a rewrite: the record is not sent)
_SEND_CAPS = {"title": 120, "subtitle": 160, "summary": 400, "supports": 200, "source": 200, "why": 200, "op": 120,
              "text": 40, "cum_text": 12, "a_text": 24, "b_text": 24, "change_text": 24}


def for_sending(rec: Dict[str, Any], scrub: Any = None) -> Optional[Dict[str, Any]]:
    """A viz record as it leaves the adapter (results_for_ai; the page's share body applies the same rule to a saved
    record, src/js/50-try.js shareSafe), or None when it cannot be sent. A copy:
      * with a suppressed cell, no exact total a hidden figure could be worked back from: inputs.rows is null (the
        rows read, less the shown cells' rows, gave a suppressed cell's; review of the chart registry, 30 Sep 2026).
        The theme heatmap's 'all' column comes from the engine in whole percents and without n in that case. Best
        effort: no secondary suppression (CONTRACT 5.9);
      * every word that names a withheld column reads "[withheld column]" (scrub: nl_browser._NameScrub), where the
        record used to be left out whole (the same review: a withheld column named "phone" dropped a themes chart
        whose word "phone" was the subjects' own). A string then over its cap, or a record over its byte cap, is not
        sent."""
    import copy
    r = copy.deepcopy(rec)
    if int((r.get("suppressed") or {}).get("cells") or 0) > 0 and isinstance(r.get("inputs"), dict):
        r["inputs"]["rows"] = None
    rx = getattr(scrub, "rx", None) if scrub is not None else None
    if rx is None:
        return r
    bad = []

    def walk(x: Any, key: str = "") -> Any:
        if isinstance(x, str):
            if key in _SEND_KEEP or not scrub.names(x):
                return x
            y = scrub(x, WITHHELD_COLUMN)
            if len(y) > _SEND_CAPS.get(key, LABEL_MAX):
                bad.append(key)
            return y
        if isinstance(x, list):
            return x if key in _SEND_KEEP else [walk(v, key) for v in x]
        if isinstance(x, dict):
            return {k: walk(v, k) for k, v in x.items()}
        return x
    r = walk(r)
    if bad or record_bytes(r) > bytes_cap(str(r.get("kind") or "")):
        return None
    return r


def is_record(c: Any) -> bool:
    """A viz record in a charts list (results_for_ai): its kind is a draw kind and it names its menu chart."""
    return isinstance(c, dict) and c.get("kind") in ("waterfall", "heatmap", "dot_range", "pareto", "slope", "table") \
        and isinstance(c.get("chart"), str) and isinstance(c.get("data"), dict)
