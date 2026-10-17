"""The analyst loop: rounds of small, typed analysis steps over the rows the engine kept (CONTRACT §5.10).

Why typed steps and not model-written pandas: a code string from a model would run in the visitor's tab with every
column in reach (a withheld column, a coded one's values) and could print any number it liked; Pyodide is not a
sandbox. A step here is data, {op, args}: the executor resolves every column against the analysis frame (which never
holds a withheld or coded column, nl_browser._analysis_frame), computes with fixed code, and records every number it
produces in the step's ledger. A sentence is a template whose slots ({A2.share}) are filled from that ledger only, so
no figure on screen can come from anywhere else. The same ops are what an AI planner may ask for (`run_steps`); with
no planner the engine's own policy (`explore`) picks them, round by round, from what the previous round found:

  round 1  the shape of the file and the headline (the latest complete period against the one before, or the spread
           of the main measure when the file has no time axis)
  round 2  which dimension explains it (the share of the change each one's groups carry, or eta squared)
  round 3  the drill: the top group, broken down by the next dimension (a panel: who leads and who moved most)
  round 4  mix or rate (did the average move inside the groups, or did the rows move between them), concentration
           (top shares and the Herfindahl-Hirschman index), the season (month-of-year share of the movement around
           the trend), or the measures that move together
  round 5  robustness: volatility (recent moves against before), does the headline hold without its largest
           contributor, and the rows far from the rest

Then the story (build_story): one template per kind of data (official statistics, panel, survey, ledger,
cross-section), a headline, up to three findings, drivers, checks to make and caveats, every line filled from the
ledgers. A planner may add up to three rounds of four steps (docs/ai-analyst/PROTOCOL.md): step_schema() is the JSON
schema of a step, generated from OPS and the ops' signatures; run_steps() checks each step against it and runs it.

Everything here is descriptive: computed from the kept rows, not tested by the engine's gate, and labelled so. A
panel's aggregate rows (World, regions, income groups) are found and left out of every sum and ranking, and the
sentence says so; a rate's change is in percentage points, never in percent.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

ANALYST_VERSION = 1
MAX_ROUNDS = 5
MAX_STEPS = 18  # the engine's own rounds (raised from 14 for yoy, seasonality, volatility, concentration)
TIME_BUDGET_S = (
    6.0  # natively; the browser is slower, so a round never starts after this
)
MAX_DIMS = 6  # dimensions a driver round compares
MAX_MEASURES = 8  # measures a relationship round compares
DIM_MIN_LEVELS, DIM_MAX_LEVELS = 2, 60
MIN_GROUP_ROWS = 5  # a group smaller than this is not ranked on its own
WHOLE_TYPES_MAX = 25  # a panel of at most this many entries is a breakdown by type: its whole-record totals are said
WHOLE_MIN_PERIODS = 10  # ... over a record of at least this many years
OUTLIER_Z = 3.5  # robust z (median and MAD) beyond which a row is far from the rest
# OPS (every op a step may name) is defined with the executor's table, _OP_FN, below.

# ----------------------------------------------------------------------------- vocabulary
_RATE_WORDS = (
    "rate",
    "ratio",
    "percent",
    "pct",
    "share",
    "inflation",
    "yield",
    "margin",
    "unemployment",
    "%",
    "per_capita",
    "per capita",
    "growth",
)
_LEVEL_WORDS = (
    "price",
    "index",
    "temp",
    "temperature",
    "mean",
    "average",
    "avg",
    "score",
    "rating",
    "stars",
    "rank",
    "age",
    "wind",
    "precipitation",
    "carat",
    "depth",
    "table",
    "close",
    "open",
    "high",
    "low",
    "anomaly",
    "level",
)
_STOCK_WORDS = (
    "population",
    "balance",
    "inventory",
    "stock",
    "headcount",
    "employees",
    "assets",
    "debt",
)
_FLOW_WORDS = (
    "revenue",
    "sales",
    "amount",
    "total",
    "value",
    "gdp",
    "units",
    "quantity",
    "qty",
    "passengers",
    "count",
    "cost",
    "profit",
    "income",
    "spend",
    "expense",
    "emissions",
    "co2",
    "deaths",
    "cases",
    "orders",
    "volume",
    "consumption",
    "production",
    "payment",
    "fee",
    "rent",
)
_ID_WORDS = (
    "id",
    "code",
    "uuid",
    "key",
    "number",
    "no",
    "zip",
    "postal",
    "phone",
    "iso",
)
_YEAR_NAMES = ("year", "yr", "fiscal_year", "fy", "anno", "jahr", "annee", "time")
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        )
    )
}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})

# A panel's rows that add up other rows. Matched whole (after lower-casing and squeezing spaces), never as a part of
# a name: "ida" is inside "Trinidad", "asia" inside "Malaysia".
_AGGREGATE_NAMES = frozenset(
    x.lower()
    for x in (
        "World",
        "Africa",
        "Asia",
        "Europe",
        "North America",
        "South America",
        "Oceania",
        "Antarctica",
        "Americas",
        "European Union",
        "European Union (27)",
        "European Union (28)",
        "Euro area",
        "OECD members",
        "OECD",
        "Non-OECD",
        "High income",
        "Low income",
        "Middle income",
        "Upper middle income",
        "Lower middle income",
        "Low & middle income",
        "High-income countries",
        "Low-income countries",
        "Upper-middle-income countries",
        "Lower-middle-income countries",
        "Arab World",
        "East Asia & Pacific",
        "Europe & Central Asia",
        "Latin America & Caribbean",
        "Middle East & North Africa",
        "South Asia",
        "Sub-Saharan Africa",
        "North America",
        "Caribbean small states",
        "Central Europe and the Baltics",
        "Early-demographic dividend",
        "Late-demographic dividend",
        "Post-demographic dividend",
        "Pre-demographic dividend",
        "Fragile and conflict affected situations",
        "Heavily indebted poor countries (HIPC)",
        "IBRD only",
        "IDA & IBRD total",
        "IDA blend",
        "IDA only",
        "IDA total",
        "Least developed countries: UN classification",
        "Other small states",
        "Pacific island small states",
        "Small states",
        "Not classified",
        "International transport",
        "International aviation",
        "International shipping",
        "Kuwaiti Oil Fires",
        "Ryder Cup",
        "Asia (excl. China and India)",
        "Europe (excl. EU-27)",
        "Europe (excl. EU-28)",
        "North America (excl. USA)",
        "Least developed countries",
        "Middle East (GCP)",
        "Total",
        "All",
        "Grand total",
    )
)
_AGGREGATE_SUFFIX = re.compile(
    r"\((?:GCP|Shift|EI|BP|Ember|EIA|excl\.[^)]*|IDA & IBRD[^)]*|excluding high income|"
    r"IDA & IBRD countries)\)\s*$",
    re.I,
)
# World Bank aggregate codes (an ISO code column marks the countries; these three-letter codes are groups)
_AGGREGATE_CODES = frozenset(
    (
        "AFE",
        "AFW",
        "ARB",
        "CEB",
        "CSS",
        "EAP",
        "EAR",
        "EAS",
        "ECA",
        "ECS",
        "EMU",
        "EUU",
        "FCS",
        "HIC",
        "HPC",
        "IBD",
        "IBT",
        "IDA",
        "IDB",
        "IDX",
        "INX",
        "LAC",
        "LCN",
        "LDC",
        "LIC",
        "LMC",
        "LMY",
        "LTE",
        "MEA",
        "MIC",
        "MNA",
        "NAC",
        "OED",
        "OSS",
        "PRE",
        "PSS",
        "PST",
        "SAS",
        "SSA",
        "SSF",
        "SST",
        "TEA",
        "TEC",
        "TLA",
        "TMN",
        "TSA",
        "TSS",
        "UMC",
        "WLD",
    )
)


def _has(name: str, words) -> bool:
    n = re.sub(r"[^a-z0-9%]+", "_", str(name).lower()).strip("_")
    parts = set(n.split("_"))
    return any((w in parts) if w.isalnum() and len(w) <= 4 else (w in n) for w in words)


# ----------------------------------------------------------------------------- formatting (the only way numbers print)
def fmt_num(v: float, kind: str = "plain", signed: bool = False) -> str:
    """How a ledger number prints. kind: plain | pct (a fraction shown as %) | pp (percentage points) | share
    (a fraction shown as %) | int | r (a correlation)."""
    if (
        v is None
        or not isinstance(v, (int, float))
        or v != v
        or v in (float("inf"), float("-inf"))
    ):
        return "not computable"
    sign = "+" if signed and v > 0 else ("-" if v < 0 else "")
    a = abs(float(v))
    if kind == "pct" and a >= 10:
        return (
            sign + "{:,.0f}-fold".format(a + 1)
            if v > 0
            else sign + "%.1f%%" % (a * 100)
        )
    if kind in ("pct", "share"):
        x = a * 100.0
        s = ("%.0f" % x) if x >= 100 else ("%.1f" % x)
        return (sign if s.strip("0.") else "") + s + "%"  # never "-0.0%"
    if kind == "pp":
        s = "%.1f" % a
        return (sign if s.strip("0.") else "") + s + " percentage points"
    if kind == "r":
        return sign + ("%.2f" % a)
    if kind == "int":
        return sign + "{:,}".format(int(round(a)))
    for div, word in ((1e12, " trillion"), (1e9, " billion"), (1e6, " million")):
        if a >= div:
            return (
                sign + ("%.3g" % (a / div)).rstrip(".") + word
                if a / div < 100
                else sign + "{:,.0f}".format(a / div) + word
            )
    if a >= 1000:
        return sign + "{:,.0f}".format(a)
    if a >= 100:
        return sign + "%.1f" % a
    if a >= 1:
        return sign + "%.2f" % a
    if a == 0:
        return "0"
    return sign + ("%.3g" % a)


class Step:
    """One executed step: what was asked, why, what came back, and every number it produced."""

    def __init__(self, sid: str, rnd: int, op: str, args: Dict[str, Any], why: str):
        self.id, self.round, self.op, self.args, self.why = sid, rnd, op, args, why
        self.ok, self.refused = True, ""
        self.numbers: Dict[str, Dict[str, Any]] = {}
        self.table: Dict[str, Any] = {"columns": [], "rows": []}
        # the data of the chart behind the step (never serialised as is): _figure builds the registry record from it
        self.chart: Optional[Dict[str, Any]] = None

    def num(
        self, key: str, value: Any, kind: str = "plain", signed: bool = False
    ) -> str:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = float("nan")
        text = fmt_num(v, kind, signed)
        self.numbers[key] = {
            "value": (v if v == v and abs(v) != float("inf") else None),
            "text": text,
            "kind": kind,
        }
        return text

    def word(self, key: str, text: Any) -> str:
        """A non-numeric slot (a group's name, a column's name): never a number the step did not compute."""
        self.numbers[key] = {"value": None, "text": str(text), "kind": "word"}
        return str(text)

    def refuse(self, why: str) -> "Step":
        self.ok, self.refused = False, why
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "round": self.round,
            "op": self.op,
            "args": self.args,
            "why": self.why,
            "ok": self.ok,
            "refused": self.refused,
            "numbers": self.numbers,
            "table": self.table,
        }


def fill(template: str, steps: Dict[str, "Step"]) -> Optional[str]:
    """A sentence from a template: every {A3.key} slot from that step's ledger. None when a slot is missing, so a
    sentence with a figure nobody computed is never made."""
    bad = []

    def sub(m):
        sid, key = m.group(1), m.group(2)
        st = steps.get(sid)
        if st is None or key not in st.numbers:
            bad.append(m.group(0))
            return ""
        return st.numbers[key]["text"]

    out = re.sub(r"\{([A-Z]\d+)\.([a-z0-9_]+)\}", sub, template)
    return None if bad else out


# ----------------------------------------------------------------------------- the file's shape
def _num(s):
    import pandas as pd

    return pd.to_numeric(s, errors="coerce")


def _text(s):
    return s.astype(str).where(s.notna(), "").str.strip()


def _group_texts(df, by: str):
    """A group column as text; a 0/1 attribute reads "with <column>" / "without <column>"."""
    import pandas as pd

    v = _num(df[by])
    if v.notna().mean() >= 0.95 and _is_binary(v):
        return pd.Series(
            ["with " + by if x == 1 else "without " + by if x == 0 else "" for x in v],
            index=df.index,
        )
    t = _text(df[by])
    if set(t.str.lower().unique()) - {""} <= {"true", "false"} and (t != "").any():
        # a TRUE/FALSE text column reads the same way as a 0/1 one
        return (
            t.str.lower()
            .map({"true": "with " + by, "false": "without " + by})
            .fillna("")
        )
    return t


def _is_binary(v) -> bool:
    u = set(v.dropna().unique().tolist())
    return 0 < len(u) <= 2 and u <= {0, 1, 0.0, 1.0}


def _idlike(name: str, s, n: int) -> bool:
    v = s.dropna()
    if not len(v):
        return False
    uniq = v.nunique() / max(1, len(v))
    integral = (
        bool(((v - v.round()).abs() < 1e-9).all()) if v.dtype.kind in "fi" else False
    )
    low = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if (
        _has(name, _ID_WORDS) or low.endswith(("id", "code", "number", "uuid"))
    ) and uniq > 0.5:
        return True
    if v.dtype.kind == "O":
        return uniq > 0.98 and n > 20
    if not (uniq > 0.98 and n > 20 and integral):
        return False
    # unique whole numbers are an id only when they run close together (1, 2, 3 ... or 1001, 1002 ...); a
    # population or a price in whole units is unique too, but spread over many orders of magnitude
    return float(v.max() - v.min()) <= 3 * len(v)


_META_TIME = re.compile(
    r"(publish|upload|creat|updat|modif|scrap|retriev|download|fetch|ingest|import|timestamp|last_?seen)", re.I
)

def detect_date(df, kinds: Dict[str, str]) -> Tuple[Optional[str], Any, str, str]:
    """(column, a datetime Series for every row, grain, how it was read). Dates the engine read are used as they
    are; else a whole-year number column; else a year column with a month column beside it; else a text column of
    unambiguous dates (ISO 2024-05 / 2024-05-01, or with a month's name: Jan 1 2000). Never a dd/mm guess.
    """
    import pandas as pd

    # a record's own timestamp (when it was published, uploaded, scraped) is not what the rows are about when the
    # file also has a year column: the Super Bowl ads are dated by the game's year, not by their YouTube upload
    has_year = any(
        kinds.get(c) == "number"
        and _has(c, _YEAR_NAMES)
        and _num(df[c]).dropna().between(1000, 2999).mean() >= 0.95
        for c in df.columns
    )
    for c in df.columns:
        if kinds.get(c) == "date":
            if has_year and _META_TIME.search(str(c)):
                continue
            d = pd.to_datetime(df[c], errors="coerce")
            if d.notna().mean() >= 0.5:
                return c, d, _grain(d), "the engine's dates"
    years = []
    for c in df.columns:
        if kinds.get(c) != "number":
            continue
        v = _num(df[c])
        nn = v.dropna()
        if len(nn) < 3 or not ((nn - nn.round()).abs() < 1e-9).all():
            continue
        if nn.between(1000, 2999).mean() >= 0.95 and (
            _has(c, _YEAR_NAMES) or nn.nunique() <= 400
        ):
            years.append((0 if _has(c, _YEAR_NAMES) else 1, c))
    years.sort()
    for _p, c in years:
        y = _num(df[c])
        for m in df.columns:
            if m == c or not _has(m, ("month", "mon", "mo", "period")):
                continue
            mm = _month_numbers(df[m])
            if mm.notna().mean() >= 0.95:
                d = pd.to_datetime(dict(year=y, month=mm, day=1), errors="coerce")
                return c, d, _grain(d), "%s with the month in %s" % (c, m)
        d = pd.to_datetime(y.astype("Int64").astype(str) + "-12-31", errors="coerce")
        return c, d, "year", "whole years"
    iso = re.compile(r"^\d{4}-\d{1,2}(-\d{1,2})?([ T].*)?$")
    named = re.compile(r"[A-Za-z]{3,9}")
    for c in df.columns:
        if kinds.get(c) != "text":
            continue
        t = _text(df[c])
        t = t[t != ""]
        if len(t) < 3:
            continue
        sample = t.head(400)
        if sample.str.match(iso).mean() >= 0.95:
            d = pd.to_datetime(
                _text(df[c]).where(_text(df[c]) != ""), errors="coerce", format="mixed"
            )
        elif (
            sample.apply(
                lambda x: bool(named.search(x)) and any(k in x.lower() for k in _MONTHS)
            ).mean()
            >= 0.95
        ):
            d = pd.to_datetime(
                _text(df[c]).where(_text(df[c]) != ""), errors="coerce", format="mixed"
            )
        else:
            continue
        if d.notna().sum() >= 0.95 * len(t):
            return c, d, _grain(d), "dates written as text"
    return None, None, "", ""


def _month_numbers(s):
    import pandas as pd

    v = _num(s)
    if v.notna().mean() >= 0.95 and v.dropna().between(1, 12).all():
        return v
    t = _text(s).str.lower()
    return pd.to_numeric(
        t.map(lambda x: _MONTHS.get(x[:3]) if len(x) >= 3 else None), errors="coerce"
    )


def _grain(d) -> str:
    u = d.dropna().drop_duplicates().sort_values()
    if len(u) < 3:
        return ""
    step = u.diff().dropna().dt.days.median()
    return (
        "year"
        if step >= 300
        else (
            "quarter"
            if step >= 80
            else "month" if step >= 25 else "week" if step >= 6 else "day"
        )
    )


def _measure_kind(name: str, s) -> str:
    if str(name).endswith(" average"):
        return "level"
    if str(name).endswith(" count"):
        return "flow"
    return _measure_kind_words(name, s)


def _measure_kind_words(name: str, s) -> str:
    """rate (changes in points), level (an average makes sense, a sum does not), stock (adds across entities, not
    across time), or flow (adds up)."""
    if _has(name, _RATE_WORDS):
        return "rate"
    v = s.dropna()
    if (
        len(v)
        and v.between(0, 1).mean() > 0.98
        and v.nunique() > 2
        and _has(name, ("percent", "share", "prob"))
    ):
        return "rate"
    if _has(name, _STOCK_WORDS):
        return "stock"
    if _has(name, _LEVEL_WORDS):
        return "level"
    if _has(name, _FLOW_WORDS):
        return "flow"
    if len(v) and (v < 0).mean() > 0.05:
        return "level"  # a measure that runs both sides of zero (an anomaly, a balance change) averages
    return "flow"


# a level on an interval scale (a temperature, an anomaly, a score): its changes are differences, never percents
# (a temperature 2.5% higher means nothing; one at 0 degrees has no percent at all)
_NO_PCT_WORDS = (
    "temp",
    "temperature",
    "celsius",
    "fahrenheit",
    "degrees",
    "anomaly",
    "score",
    "rating",
    "stars",
    "elevation",
    "altitude",
    "latitude",
    "longitude",
)
# a level whose value is not comparable across entries (share prices, index points): an entry's own change is,
# but a ranking of the levels themselves says nothing
_UNRANKED_LEVEL_WORDS = ("price", "close", "open", "high", "low", "index", "adj_close")


def _no_negatives(s) -> bool:
    """A measure a percent change may be read on: (nearly) no value below zero. Zeros are allowed (a country with no emissions
    in 1850 does not make the world's change a difference); a measure that crosses zero is read as a difference."""
    v = _num(s).dropna()
    return bool(len(v)) and (v >= 0).mean() > 0.99 and (v > 0).mean() > 0.5


def _pct_ok(sh: "Shape", measure: str) -> bool:
    """Whether a change of this measure may be read in percent (the values must be positive too, checked apart)."""
    k = sh.mkind.get(measure, "flow")
    if k == "rate":
        return False
    return not (k == "level" and _has(measure, _NO_PCT_WORDS))


def detect_aggregates(
    df, ent: str, date: Optional[str], measure: Optional[str]
) -> Tuple[List[str], str]:
    """The entity values that add up other rows, and how they were found."""
    names = _text(df[ent])
    vals = sorted(set(names) - {""})
    hit, how = set(), []
    for v in vals:
        k = re.sub(r"\s+", " ", v.lower()).strip()
        if k in _AGGREGATE_NAMES or _AGGREGATE_SUFFIX.search(v):
            hit.add(v)
    if hit:
        how.append("by name")
    # an ISO code column: the countries carry three capital letters; a blank, an OWID_ code or a World Bank group
    # code marks a group
    for c in df.columns:
        if c in (ent, date) or not _has(c, ("code", "iso", "iso_code", "iso3")):
            continue
        codes = _text(df[c])
        ok = codes.str.fullmatch(r"[A-Z]{3}")
        if ok.mean() < 0.5:
            continue
        per = codes.groupby(names).first()
        more = {
            e
            for e, cd in per.items()
            if e and (not re.fullmatch(r"[A-Z]{3}", cd or "") or cd in _AGGREGATE_CODES)
        }
        if more - hit:
            hit |= more
            how.append("by their %s" % c)
        break
    # a value that is the sum of the others in most years ("World" in a file that names it otherwise)
    if measure and date and len(vals) >= 5:
        import pandas as pd

        t = pd.DataFrame({"e": names, "d": df[date], "v": _num(df[measure])}).dropna()
        t = t[~t["e"].isin(hit) & (t["e"] != "")]
        if len(t):
            piv = t.pivot_table(index="d", columns="e", values="v", aggfunc="sum")
            if piv.shape[1] >= 5 and piv.shape[0] >= 3:
                tot = piv.sum(axis=1)
                for e in piv.columns:
                    rest = tot - piv[e]
                    both = piv[e].notna() & (rest > 0)
                    if (
                        both.sum() >= 3
                        and (
                            (piv[e][both] - rest[both]).abs() <= 0.1 * rest[both]
                        ).mean()
                        >= 0.8
                    ):
                        hit.add(e)
                        how.append("%s adds up the others" % e)
    return sorted(hit), "; ".join(how)


class Shape:
    def __init__(self):
        self.kind = "cross_section"
        self.date: Optional[str] = None
        self.dates = None
        self.grain = ""
        self.date_how = ""
        self.entity: Optional[str] = None
        self.measures: List[str] = []
        self.mkind: Dict[str, str] = {}
        self.dims: List[str] = []
        self.free: List[str] = []          # free-text columns no step reads (shape_of)
        self.primary: Optional[str] = None
        self.aggregates: List[str] = []
        self.agg_how = ""
        self.repeat = 1.0  # rows per (entity, date)
        self.label: Optional[str] = (
            None  # a column naming each row (a brand, a product) with no time axis
        )
        # wide period columns (incidents_85_99, incidents_00_14): stem -> [(start year, label, column)], oldest first
        self.periods: Dict[str, List[Tuple[int, str, str]]] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "date": self.date,
            "date_read_as": self.date_how,
            "grain": self.grain or None,
            "entity": self.entity,
            "measures": [{"column": m, "kind": self.mkind[m]} for m in self.measures],
            "dimensions": self.dims,
            "primary": self.primary,
            "label": self.label,
            **(
                {"periods": {k: [c for _y, _l, c in v] for k, v in self.periods.items()}}
                if self.periods
                else {}
            ),
            "aggregates": (
                {
                    "column": self.entity,
                    "values": self.aggregates[:40],
                    "count": len(self.aggregates),
                    "how": self.agg_how,
                }
                if self.aggregates
                else None
            ),
        }


def _rating_histograms(df, kinds: Dict[str, str]) -> None:
    """Columns that count answers on a scale (stars_1 ... stars_5, score_1 ... score_10): adds "<prefix> average"
    (the count-weighted average of the scale) and "<prefix> count" (the answers on the row), in place, and leaves
    the scale columns out of the measures."""
    import pandas as pd

    groups: Dict[str, Dict[int, str]] = {}
    for c in df.columns:
        m = re.match(r"^(.*?)[ _-]?(\d{1,2})$", str(c))
        if m and kinds.get(c) == "number" and m.group(1):
            groups.setdefault(m.group(1), {})[int(m.group(2))] = c
    for prefix, cols in groups.items():
        ks = sorted(cols)
        if not (3 <= len(ks) <= 11) or ks != list(range(ks[0], ks[0] + len(ks))):
            continue
        vals = pd.DataFrame({k: _num(df[cols[k]]) for k in ks})
        if not (
            (vals.fillna(0) >= 0).all().all()
            and ((vals.fillna(0) % 1) == 0).all().all()
        ):
            continue
        tot = vals.sum(axis=1)
        avg = (vals * pd.Series(ks, index=ks)).sum(axis=1) / tot.where(tot > 0)
        name_a, name_c = "%s average" % prefix, "%s count" % prefix
        df[name_a] = avg
        df[name_c] = tot
        kinds[name_a] = kinds[name_c] = "number"
        for k in ks:
            kinds[cols[k]] = "scale"  # not a measure, not a dimension
        df.attrs.setdefault("derived", []).append(
            {"average": name_a, "count": name_c, "from": [cols[k] for k in ks]}
        )


def shape_of(df, kinds: Dict[str, str], hint: Optional[Dict[str, Any]] = None) -> Shape:
    """What the file is: its time axis, measures, dimensions, entity and kind. hint: {"date", "primary",
    "entity", "free_text"} from an AI plan (each used only when it names a column of the frame). A free-text column
    (review titles, comments) is never a dimension, an entity or a label: its values are people's own words, which
    can name people, so no step lists them, and the AI planner never sees them."""
    hint = hint or {}
    free = {str(c) for c in hint.get("free_text") or [] if isinstance(c, str)}
    sh = Shape()
    sh.free = sorted(free & set(map(str, df.columns)))
    n = len(df)
    sh.date, sh.dates, sh.grain, sh.date_how = None, None, "", ""
    if hint.get("date") in df.columns:
        sh.date, sh.dates, sh.grain, sh.date_how = detect_date(
            df[[hint["date"]]], kinds
        )
        if sh.date is not None and sh.date_how == "whole years":
            pass
    if sh.date is None:
        sh.date, sh.dates, sh.grain, sh.date_how = detect_date(df, kinds)
    _rating_histograms(df, kinds)
    month_col = None
    label_cands: List[str] = []
    if sh.date_how.startswith(str(sh.date) + " with the month in "):
        month_col = sh.date_how.split(" with the month in ", 1)[1]
    for c in df.columns:
        if c in (sh.date, month_col) or c in free:
            continue
        k = kinds.get(c)
        if k == "number":
            v = _num(df[c])
            if v.notna().sum() < max(3, 0.2 * n):
                continue
            if _is_binary(v):
                sh.dims.append(
                    c
                )  # a 0/1 attribute splits the rows; it is not a measure
            elif (
                _has(c, _YEAR_NAMES)
                and v.dropna().between(1000, 2999).mean() >= 0.95
                and ((v.dropna() - v.dropna().round()).abs() < 1e-9).all()
            ):
                continue  # a year beside the date (or a second year column) labels rows; summing it means nothing
            elif not _idlike(c, v, n):
                sh.measures.append(c)
        elif k == "text":
            t = _text(df[c])
            lv = t[t != ""].nunique()
            if DIM_MIN_LEVELS <= lv <= DIM_MAX_LEVELS and (t != "").mean() >= 0.5:
                sh.dims.append(c)
            elif (
                lv > DIM_MAX_LEVELS
                and sh.date is not None
                and sh.entity is None
                and lv <= max(400, 0.5 * n)
            ):
                per = t[t != ""].value_counts()
                at_once = df[t != ""].groupby(sh.dates[t != ""]).size()
                if per.median() >= 3 and len(at_once) and at_once.median() >= 2:
                    sh.entity = (
                        c  # repeats over time, several at each date (countries by year)
                    )
            elif (
                lv > DIM_MAX_LEVELS
                and sh.date is None
                and (t != "").mean() >= 0.9
            ):
                label_cands.append(c)
    if label_cands:
        # of the columns that name each row, the one a reader knows: words (Apple Inc.) over codes (AAPL)
        def readable(c):
            t = _text(df[c])
            t = t[t != ""].head(500)
            words = float(t.str.contains(r"[a-z]").mean()) + float(t.str.contains(" ").mean())
            # a name is (nearly) one per row; a column with repeats is a group, however wordy
            unique = t.nunique() >= 0.9 * max(1, len(t))
            return (not unique, -round(words, 1), label_cands.index(c))

        sh.label = sorted(label_cands, key=readable)[0]
    if hint.get("entity") in df.columns and kinds.get(hint["entity"]) == "text" and hint["entity"] not in free:
        sh.entity = hint["entity"]
        if sh.entity in sh.dims:
            sh.dims.remove(sh.entity)
    if sh.entity is None and sh.date is not None:
        # a dimension that repeats every date with one row each is the panel's entity (5 stocks by month)
        for c in sh.dims:
            t = _text(df[c])
            if not ((t != "").sum()):
                continue
            pairs = df.groupby([t, sh.dates]).size()
            per_date = df[t != ""].groupby(sh.dates[t != ""]).size()
            if (
                len(pairs)
                and (pairs == 1).mean() >= 0.95
                and t[t != ""].nunique()
                >= 3  # two parallel series (two sources of one measure) are not a panel
                and len(per_date)
                and per_date.median() >= 2
            ):
                sh.entity = c
                sh.dims.remove(c)
                break
    for m in sh.measures:
        sh.mkind[m] = _measure_kind(m, _num(df[m]))
    # the engine's own reading of a published table's series (nl_browser._analyst_series_kinds): a balance is never
    # added over months, a rate changes in points; it outranks the guess from the header's words
    for m, k in (hint.get("mkind") or {}).items():
        if m in sh.mkind and k in ("rate", "level", "stock", "flow"):
            sh.mkind[m] = k
    if sh.date is None:
        sh.periods = _period_columns(sh.measures)
    sh.primary = _pick_primary(sh, df, hint.get("primary"), hint.get("topic"))
    if sh.entity and sh.date is not None:
        t = _text(df[sh.entity])
        pairs = df.groupby([t, sh.dates]).size()
        sh.repeat = float(pairs.mean()) if len(pairs) else 1.0
        sh.kind = "panel"
        sh.aggregates, sh.agg_how = detect_aggregates(
            df.assign(**{"__d": sh.dates}), sh.entity, "__d", sh.primary
        )
    elif sh.date is not None:
        per = sh.dates.dropna().value_counts()
        sh.kind = "ledger" if len(per) and per.median() > 1.5 else "time_series"
        if (
            sh.kind == "time_series"
            and sh.grain in ("day", "week")
            and sh.dims
            and len(per) >= 3
        ):
            # transactions on scattered days (orders, payments) are a ledger even with one a day: a series has a
            # value on (nearly) every day of its span, a ledger on the days something happened
            span = (per.index.max() - per.index.min()).days + 1
            if span > 0 and len(per) / span < 0.6:
                sh.kind = "ledger"
        if sh.kind == "ledger":
            # two parallel series (two sources of one measure, one row each a date) are a time series
            for c in sh.dims:
                t = _text(df[c])
                if (
                    t[t != ""].nunique() == 2
                    and (df.groupby([t, sh.dates]).size() == 1).mean() >= 0.95
                    and per.median() <= 2
                    and (
                        df[t != ""].groupby(sh.dates[t != ""])[c].nunique() == 2
                    ).mean()
                    >= 0.8  # both, most dates
                ):
                    sh.kind = "time_series"
                    break
    elif _looks_survey(df, kinds, sh):
        sh.kind = "survey"
    sh.dims = sorted(sh.dims, key=lambda c: _text(df[c]).nunique())[:12]
    return sh


def _looks_survey(df, kinds, sh) -> bool:
    texts = [c for c in df.columns if kinds.get(c) == "text"]
    if len(texts) < 4:
        return False
    low_card = sum(1 for c in texts if 2 <= _text(df[c]).nunique() <= 12)
    return low_card >= 0.6 * len(texts) and len(sh.measures) <= 2


_DERIVED = re.compile(r"(change|pct|percent|per_capita|per_gdp|share|ratio|intensity|growth|_rank$|index)", re.I)


def _pick_primary(sh: Shape, df, hint: Optional[str], topic: Optional[List[str]] = None) -> Optional[str]:
    if hint in sh.measures:
        return hint
    for d in df.attrs.get("derived", []):
        if d["average"] in sh.measures:
            return d["average"]
    if not sh.measures:
        return None
    if sh.periods:
        # a table of counts by period (incidents_85_99 ... fatalities_00_14): the latest period of the stem with the
        # largest total, never the size column beside it (avail_seat_km_per_week)
        stem = _period_order(sh, df)[0]
        return sh.periods[stem][-1][2]
    # a wide file named for its subject (owid-energy-data): the measure that is the subject itself, not the gdp or
    # population beside it nor a share, change or per-head figure derived from it; the best-filled of those
    words = [w for w in (topic or []) if isinstance(w, str) and len(w) >= 3]
    if words:
        def toks(m):
            return re.sub(r"[^a-z0-9]+", "_", m.lower()).split("_")

        on = [m for m in sh.measures if any(w in toks(m) or w.rstrip("s") in toks(m) for w in words)]
        base = [m for m in on if not _DERIVED.search(re.sub(r"[^a-z0-9]+", "_", m.lower()))]
        pick = base or on
        if pick:
            return sorted(pick, key=lambda m: (-int(_num(df[m]).notna().sum()), sh.measures.index(m)))[0]
    order = (
        "revenue",
        "market_cap",
        "sales",
        "co2",
        "gdp",
        "passengers",
        "amount",
        "value",
        "total",
        "price",
        "population",
        "profit",
        "units",
        "quantity",
        "count",
        "winpercent",
        "rating",
        "stars",
        "score",
        "index",
        "mean",
        "close",
        "temp_max",
        "temperature",
        "inflation",
    )
    low = {m: re.sub(r"[^a-z0-9]+", "_", m.lower()) for m in sh.measures}
    for w in order:
        exact = [m for m in sh.measures if low[m] == w]
        if exact:
            return exact[0]
    for w in order:
        part = [m for m in sh.measures if w in low[m].split("_")]
        if part:
            return part[0]
    filled = sorted(
        sh.measures,
        key=lambda m: (-int(_num(df[m]).notna().sum()), sh.measures.index(m)),
    )
    return filled[0]


# a number column that ends in a year or a span of years: incidents_85_99, fatalities_00_14, pop_2019, sales 2020-21
_PERIOD_TAIL = re.compile(
    r"^(?P<stem>.*?[a-z].*?)[ _-]+(?P<a>(?:19|20)\d{2}|\d{2})(?:[ _-]+(?:to[ _-]+)?(?P<b>(?:19|20)\d{2}|\d{2}))?$",
    re.I,
)
# stems whose numbered columns are bands or ranks, not years (age_18_24, male_25_34, top_10)
_NOT_PERIOD = re.compile(
    r"(^|_)(age|aged|ages|male|female|men|women|boys|girls|grade|size|top|bottom|rank|q|quarter|week|day|hour|month|"
    r"score|stars|level|tier|band|bin|bucket|pct|percentile)(_|$)",
    re.I,
)


def _period_columns(measures: List[str]) -> Dict[str, List[Tuple[int, str, str]]]:
    """Measures that repeat one quantity by period in the column name. Four-digit years count on their own;
    two-digit ones (85_99) only when two or more quantities share the same periods, so a run of numbered columns
    (q_10, q_20) is not read as years."""
    found: Dict[str, List[Tuple[int, str, str, bool]]] = {}
    for c in measures:
        m = _PERIOD_TAIL.match(str(c).strip())
        if not m:
            continue
        stem = m.group("stem").strip(" _-")
        if not stem or _NOT_PERIOD.search(re.sub(r"[^a-z0-9]+", "_", stem.lower())):
            continue
        a, b = m.group("a"), m.group("b")
        ya = int(a) if len(a) == 4 else (2000 + int(a) if int(a) <= 30 else 1900 + int(a))
        short = len(a) == 2
        if b:
            yb = int(b) if len(b) == 4 else (ya // 100) * 100 + int(b)
            if yb < ya:
                yb += 100
            if not (0 < yb - ya <= 50):
                continue
            label = "%d-%s" % (ya, ("%02d" % (yb % 100)) if yb // 100 == ya // 100 else str(yb))
            short = short or len(b) == 2
        else:
            label = str(ya)
        found.setdefault(stem, []).append((ya, label, c, short))
    out: Dict[str, List[Tuple[int, str, str]]] = {}
    sets: Dict[Tuple[str, ...], int] = {}
    for stem, v in found.items():
        if len(v) >= 2 and len({x[0] for x in v}) == len(v):
            key = tuple(sorted(x[1] for x in v))
            sets[key] = sets.get(key, 0) + 1
    for stem, v in found.items():
        if len(v) < 2 or len({x[0] for x in v}) != len(v):
            continue
        if any(x[3] for x in v) and sets.get(tuple(sorted(x[1] for x in v)), 0) < 2:
            continue
        out[stem] = [(y, l, c) for y, l, c, _s in sorted(v)]
    return out


def _period_order(sh: "Shape", df) -> List[str]:
    """The period stems, the one with the largest latest-period total first (fatalities before incidents)."""

    def tot(stem):
        return float(_num(df[sh.periods[stem][-1][2]]).abs().sum())

    stems = list(sh.periods)
    return sorted(stems, key=lambda k: (-tot(k), stems.index(k)))


# a column that measures how large each row is (seat-km flown, people, hours worked): counts are read against it
_EXPOSURE_WORDS = (
    "km",
    "kms",
    "miles",
    "seat",
    "seats",
    "population",
    "pop",
    "members",
    "employees",
    "staff",
    "headcount",
    "hours",
    "passengers",
    "users",
    "customers",
    "subscribers",
    "exposure",
    "capacity",
    "enrollment",
    "enrolment",
    "students",
    "households",
    "beds",
    "flights",
    "trips",
    "visits",
)


def _exposure_col(sh: "Shape", df, skip: List[str]) -> Optional[str]:
    """A size column a count is read against: named for size, all positive, spread at least tenfold."""
    for c in sh.measures:
        if c in skip or not _has(c, _EXPOSURE_WORDS) or _DERIVED.search(c.lower()):
            continue
        v = _num(df[c]).dropna()
        if len(v) >= 5 and (v > 0).mean() >= 0.95 and v[v > 0].max() >= 10 * v[v > 0].min():
            return c
    return None


def _readable(name: str) -> str:
    return re.sub(r"[_]+", " ", str(name)).strip()


# ----------------------------------------------------------------------------- the executor
class Ctx:
    def __init__(self, df, sh: Shape):
        import pandas as pd

        self.df = df
        self.sh = sh
        keep = pd.Series(True, index=df.index)
        if sh.entity and sh.aggregates:
            keep = ~_text(df[sh.entity]).isin(sh.aggregates)
        self.base = keep  # rows every sum and ranking reads (the aggregates set apart)
        self.steps: Dict[str, Step] = {}
        self.order: List[str] = []
        self.refused: List[Dict[str, str]] = []

    def new(self, rnd: int, op: str, args: Dict[str, Any], why: str) -> Step:
        st = Step("A%d" % (len(self.order) + 1), rnd, op, args, why)
        self.steps[st.id] = st
        self.order.append(st.id)
        return st

    def col(self, name: Any, want: str = "any") -> Optional[str]:
        """A column the step may read: only the frame's own (a withheld or coded column is never in it)."""
        if not isinstance(name, str):
            return None
        hit = (
            name
            if name in self.df.columns
            else next((c for c in self.df.columns if c.lower() == name.lower()), None)
        )
        if hit is None or hit in getattr(self.sh, "free", ()):
            return None                          # a free-text column is read by no step (shape_of)
        if want == "measure" and hit not in self.sh.measures:
            return None
        if want == "dim" and hit not in self.sh.dims + (
            [self.sh.entity] if self.sh.entity else []
        ):
            return None
        return hit


def _agg_for(sh: Shape, m: str) -> str:
    k = sh.mkind.get(m, "flow")
    if k == "flow":
        return "sum"
    if k == "stock":
        return "stock"
    return "mean"


def _period_key(d, grain: str):
    if grain == "year":
        return d.dt.year
    if grain == "quarter":
        return d.dt.year * 10 + d.dt.quarter
    return d.dt.year * 100 + d.dt.month


def _period_label(k: int, grain: str) -> str:
    k = int(k)
    if grain == "year":
        return str(k)
    if grain == "quarter":
        return "%d Q%d" % (k // 10, k % 10)
    return "%d-%02d" % (k // 100, k % 100)


def _aggregate(frame, value: str, agg: str, keys: List[str], ent: Optional[str]):
    """Sum, mean or a stock's total (each entity's average in the period, then added across entities)."""
    if agg == "sum":
        return frame.groupby(keys)[value].sum(min_count=1)
    if agg == "mean":
        return frame.groupby(keys)[value].mean()
    if ent and ent in frame.columns:
        per = frame.groupby(keys + [ent])[value].mean()
        return per.groupby(level=list(range(len(keys)))).sum(min_count=1)
    return frame.groupby(keys)[value].mean()


def _windows(ctx: Ctx, measure: Optional[str] = None):
    """(unit, previous keys, latest keys, previous label, latest label) over complete periods.
    Yearly data: the latest year with at least 90% of the best-covered year's rows against the year before.
    Finer data, a flow or a ledger: the latest 12 complete months against the 12 before when all 24 are in the file;
    else the latest complete calendar year against the one before. A level or a rate with one value a month (an index,
    a price, a rate): the latest month against the same month a year earlier (the year-on-year reading official
    statistics use), else against the month before. A month only partly in the file is never the latest.
    """
    sh, d = ctx.sh, ctx.sh.dates
    ok = d.notna() & ctx.base
    if measure is not None and measure in ctx.df.columns:
        ok &= _num(
            ctx.df[measure]
        ).notna()  # the windows of this measure: a year with no value of it is not complete
    if ok.sum() < 4:
        return None
    lab = lambda k: "%d-%02d" % (k // 12, k % 12 + 1)  # noqa: E731
    if sh.grain == "year":
        y = d[ok].dt.year
        cnt = y.value_counts().sort_index()
        if sh.kind == "ledger":
            # a ledger's rows are events and their number varies year to year (8 ads one year, 18 the next): a year
            # is short only against the typical year, not against the busiest
            full = cnt[cnt >= 0.5 * cnt.median()].index
        else:
            full = cnt[cnt >= 0.9 * cnt.max()].index
        if len(full) < 2:
            return None
        last = int(max(full))
        # a panel's latest year can lack a few small entries (World Bank: 2023 without some islands) and still hold
        # nearly all of the total: it is complete when the entries reported in it held 95% of the year before's
        if sh.entity and measure is not None and measure in ctx.df.columns:
            import pandas as pd

            f = pd.DataFrame(
                {"y": y, "e": _text(ctx.df[sh.entity])[ok], "v": _num(ctx.df[measure])[ok]}
            )
            for cand in sorted((int(k) for k in cnt.index if k > last), reverse=True):
                if cnt.loc[cand] < 0.5 * cnt.max() or (cand - 1) not in cnt.index:
                    continue
                before = f[f["y"] == cand - 1].groupby("e")["v"].sum().clip(lower=0)
                there = set(f.loc[f["y"] == cand, "e"])
                tot = float(before.sum())
                if tot > 0 and float(before[before.index.isin(there)].sum()) >= 0.95 * tot:
                    last = cand
                    break
        prev = last - 1 if (last - 1) in cnt.index else int(sorted(full)[-2])
        return ("year", [prev], [last], str(prev), str(last))
    m = d[ok].dt.year * 12 + d[ok].dt.month - 1
    cnt = m.value_counts().sort_index()
    last = int(cnt.index.max())
    if len(cnt) >= 3 and cnt.loc[last] < 0.5 * cnt.median():
        last -= 1  # a month only partly in the file
    # a tail where one of several parallel series has stopped (GISTEMP ends 2025-12, GCAG runs on): the latest
    # month is the last one every series still reports, so a window never compares a different mix of sources
    typical = float(cnt.median())
    if typical >= 2:
        while last in cnt.index and cnt.loc[last] < 0.75 * typical and last - 24 > int(cnt.index.min()):
            last -= 1
    have = set(int(x) for x in cnt.index)
    kind = sh.mkind.get(measure or sh.primary or "", "flow")
    one_a_month = float(cnt.median()) <= 1.5 and not sh.entity
    if kind in ("level", "rate") and one_a_month:
        if (last - 12) in have:
            return ("month", [last - 12], [last], lab(last - 12), lab(last))
        if (last - 1) in have:
            return ("month", [last - 1], [last], lab(last - 1), lab(last))
        return None
    if all((last - i) in have for i in range(24)):
        cur = list(range(last - 11, last + 1))
        prev = list(range(last - 23, last - 11))
        return (
            "month",
            prev,
            cur,
            "the 12 months to %s" % lab(prev[-1]),
            "the 12 months to %s" % lab(cur[-1]),
        )
    years = sorted(
        {
            k // 12
            for k in have
            if all((k // 12) * 12 + i in have for i in range(12))
            and (k // 12) * 12 + 11 <= last
        }
    )
    if len(years) >= 2 and years[-1] - 1 in years:
        y = years[-1]
        return ("year", [y - 1], [y], str(y - 1), str(y))
    if sh.kind == "ledger":
        # a ledger records events, and a month with none (an off season) is not missing: a year is complete when
        # the file's dates run from before it starts to after it ends (the last month itself complete)
        first_k = int(cnt.index.min())
        inside = [
            y
            for y in range(first_k // 12, last // 12 + 1)
            if y * 12 >= first_k and y * 12 + 11 <= last
        ]
        if len(inside) >= 2:
            y = inside[-1]
            return ("year", [y - 1], [y], str(y - 1), str(y))
    if (last - 12) in have:
        return ("month", [last - 12], [last], lab(last - 12), lab(last))
    if (last - 1) in have:
        return ("month", [last - 1], [last], lab(last - 1), lab(last))
    return None


def _in(ctx: Ctx, keys: List[int], unit: str):
    d = ctx.sh.dates
    if unit == "year":
        return d.dt.year.isin(keys)
    return (d.dt.year * 12 + d.dt.month - 1).isin(keys)


def op_change(
    ctx: Ctx,
    st: Step,
    measure: str,
    by: Optional[str] = None,
    where: Optional[Dict[str, str]] = None,
) -> Step:
    """The measure in the latest complete window against the one before, and each group's part of the change."""
    import pandas as pd

    sh = ctx.sh
    if sh.date is None:
        return st.refuse("the file has no time axis")
    w = _windows(ctx, measure)
    if w is None:
        return st.refuse("fewer than two complete periods")
    unit, prev, cur, lp, lc = w
    agg = _agg_for(sh, measure)
    kind = sh.mkind.get(measure, "flow")
    base = ctx.base.copy()
    if where:
        st.word(
            "scope",
            "Among rows where %s, "
            % " and ".join("%s is %s" % (c, v) for c, v in where.items()),
        )
        for c, v in where.items():
            base &= _group_texts(ctx.df, c) == str(v)
    f = pd.DataFrame({"v": _num(ctx.df[measure])})
    f["w"] = None
    f.loc[base & _in(ctx, prev, unit), "w"] = "prev"
    f.loc[base & _in(ctx, cur, unit), "w"] = "cur"
    if sh.entity:
        f["e"] = _text(ctx.df[sh.entity])
    f = f.dropna(subset=["v", "w"])
    if f.empty or f["w"].nunique() < 2:
        return st.refuse("no values in one of the two windows")
    ent = "e" if sh.entity else None

    def total(frame):
        if agg == "stock" and ent:
            # each entity's average in the window, added across entities
            return frame.groupby("e")["v"].mean().sum()
        return frame["v"].sum() if agg == "sum" else frame["v"].mean()

    a, b = total(f[f["w"] == "prev"]), total(f[f["w"] == "cur"])
    st.word("measure", measure)
    st.word("prev_label", lp)
    st.word("cur_label", lc)
    single = int((f["w"] == "prev").sum()) == 1 and int((f["w"] == "cur").sum()) == 1
    st.word(
        "agg_word",
        (
            "value"
            if single and agg == "mean"
            else {"sum": "total", "stock": "total", "mean": "average"}[agg]
        ),
    )
    st.num("prev", a)
    st.num("cur", b)
    st.num("delta", b - a, "plain", signed=True)
    rate = kind == "rate"
    if rate:
        st.num(
            "change", (b - a) * (100 if _frac_scale(f["v"]) else 1), "pp", signed=True
        )
    elif (
        a
        and a > 0
        and _pct_ok(sh, measure)
        and _no_negatives(ctx.df[measure])
    ):
        st.num("change", (b - a) / abs(a), "pct", signed=True)
    else:
        st.num(
            "change", b - a, "plain", signed=True
        )  # a measure that crosses zero: a difference, never a %
    # a panel's own World row (a publisher's grand total, left out of the sum of entries with every other aggregate): its change
    # beside the entries' total, since the World row also counts what no entry holds (co2_panel: international shipping and
    # aviation; 38,094 to 38,599 Mt, +1.3%, against the countries' 37,003 to 37,398)
    if sh.entity and sh.aggregates and not by and not where and agg in ("sum", "stock"):
        wname = next((x for x in sh.aggregates if str(x).strip().lower() == "world"), None)
        if wname is not None:
            wm = _text(ctx.df[sh.entity]) == wname
            wv = _num(ctx.df[measure])
            wa = wv[wm & _in(ctx, prev, unit)].dropna()
            wb = wv[wm & _in(ctx, cur, unit)].dropna()
            if len(wa) and len(wb):
                wa_, wb_ = (wa.sum(), wb.sum()) if agg == "sum" else (wa.mean(), wb.mean())
                if wa_ > 0 and _pct_ok(sh, measure) and _no_negatives(ctx.df[measure]):
                    st.word("world_name", wname)
                    st.num("world_prev", wa_)
                    st.num("world_cur", wb_)
                    st.num("world_change", (wb_ - wa_) / abs(wa_), "pct", signed=True)
                    st.word("world_direction", "rose" if wb_ > wa_ else "fell" if wb_ < wa_ else "held level")
    st.numbers["direction"] = {
        "value": None,
        "text": "rose" if b > a else "fell" if b < a else "held level",
        "kind": "word",
    }
    st.table = {"columns": ["window", "value"], "rows": [[lp, _r(a)], [lc, _r(b)]]}
    if by:
        st.word("by", by)
        g = _group_texts(ctx.df, by).loc[f.index]
        f = f.assign(g=g)
        blank = f[f["g"] == ""]
        if agg == "sum" and len(blank) and b != a:
            # rows with no value in the group column carry part of the change too, and no group stands for them
            bd = float(
                blank.loc[blank["w"] == "cur", "v"].sum()
                - blank.loc[blank["w"] == "prev", "v"].sum()
            )
            st.num("blank_share", bd / (b - a), "share")
        f = f[f["g"] != ""]
        if agg == "mean":
            # an average: each group's own change, and its rows' share in the latest window
            pa = f[f["w"] == "prev"].groupby("g")["v"].agg(["mean", "size"])
            pb = f[f["w"] == "cur"].groupby("g")["v"].agg(["mean", "size"])
            t = pa.join(pb, lsuffix="_p", rsuffix="_c", how="inner")
            t = t[(t["size_p"] >= MIN_GROUP_ROWS) & (t["size_c"] >= MIN_GROUP_ROWS)]
            if t.empty:
                return st
            t["delta"] = t["mean_c"] - t["mean_p"]
            t = t.sort_values("delta", key=lambda s: -s.abs())
            st.table = {
                "columns": [by, lp, lc, "change", "rows (latest)"],
                "rows": [
                    [k, _r(r.mean_p), _r(r.mean_c), _r(r.delta), int(r.size_c)]
                    for k, r in t.head(12).iterrows()
                ],
            }
            top = t.index[0]
            st.word("top", top)
            st.num("top_delta", t.loc[top, "delta"], "plain", signed=True)
            st.num("groups", len(t), "int")
            same = int((t["delta"] * (b - a) > 0).sum())
            st.num("same_way", same, "int")
            st.numbers["explained"] = {
                "value": float(same / len(t)),
                "text": "",
                "kind": "score",
            }
            _chart_bars(
                st,
                [(k, [float(r.mean_p), float(r.mean_c)], int(min(r.size_p, r.size_c)),
                  fmt_num(float(r.delta), "plain", signed=True)) for k, r in t.iterrows()],
                [lp, lc], measure, "average", "plain", "Average %s by %s, %s against %s" % (measure, by, lc, lp),
                [measure, by, sh.date], by,
            )
            return st
        if agg == "stock":
            per = (
                f.groupby(["g", "w", "e"])["v"]
                .mean()
                .groupby(["g", "w"])
                .sum()
                .unstack("w")
            )
        else:
            per = f.groupby(["g", "w"])["v"].sum().unstack("w")
        per = per.reindex(columns=["prev", "cur"]).fillna(0.0)
        per["delta"] = per["cur"] - per["prev"]
        tot = float(b - a)
        if not tot:
            return st
        per["part"] = per["delta"] / tot
        per = per.sort_values("delta", key=lambda s: -(s * math.copysign(1, tot)))
        st.table = {
            "columns": [by, lp, lc, "change", "share of the change"],
            "rows": [
                [k, _r(r.prev), _r(r.cur), _r(r.delta), _r(r.part, 4)]
                for k, r in per.head(12).iterrows()
            ],
        }
        top = per.index[0]
        st.word("top", top)
        st.num("top_delta", per.loc[top, "delta"], "plain", signed=True)
        st.num("top_share", per.loc[top, "part"], "share")
        st.num("groups", len(per), "int")
        k3 = per["part"].head(3).sum()
        st.num("top3_share", k3, "share")
        rows_in = f.groupby(["g", "w"]).size().unstack("w").reindex(columns=["prev", "cur"]).fillna(0)
        _chart_bars(
            st,
            [(k, [float(r.prev), float(r.cur)], int(min([c for c in rows_in.loc[k] if c > 0] or [0])) if k in rows_in.index else 0,
              fmt_num(float(r.delta), "plain", signed=True)) for k, r in per.iterrows()],
            [lp, lc], measure, "amount", "plain", "%s by %s, %s against %s" % (measure, by, lc, lp),
            [measure, by, sh.date], by,
        )
        if sh.dates is not None:
            # a group that only started partway through (a building bought, a store opened) carries a change that
            # is an addition, not growth of what was already there: say when it first appears
            gall = _group_texts(ctx.df, by)
            seen = sh.dates[ctx.base & (gall == top)].dropna()
            first_all = sh.dates[ctx.base].dropna()
            if len(seen) and len(first_all):
                t0, tf, tl = seen.min(), first_all.min(), first_all.max()
                if (t0 - tf).days > 180 and (tl - t0).days < 2 * 366:
                    st.word("top_first", t0.strftime("%Y-%m"))
        against = per[per["delta"] * tot < 0]
        if len(against):
            st.word(
                "against",
                against.sort_values(
                    "delta", key=lambda s: s * math.copysign(1, tot)
                ).index[0],
            )
            st.num(
                "against_delta",
                against["delta"].loc[st.numbers["against"]["text"]],
                "plain",
                signed=True,
            )
        # how differently the change is split from the groups' size before (dissimilarity index, 0 to 1): a
        # dimension whose groups all grew in proportion explains nothing, however big its largest group is
        base_share = (
            per["prev"] / per["prev"].sum() if per["prev"].sum() else per["prev"] * 0
        )
        st.numbers["explained"] = {
            "value": float(min(1.0, 0.5 * (per["part"] - base_share).abs().sum()))
            * (
                1
                - min(
                    1.0,
                    max(0.0, (st.numbers.get("blank_share") or {}).get("value") or 0.0),
                )
            ),
            "text": "",
            "kind": "score",
        }
    return st


def _frac_scale(v) -> bool:
    x = v.dropna()
    return bool(len(x)) and x.between(-1, 1).mean() > 0.98


def _r(v, d: int = 6):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) == float("inf"):
        return None
    return round(f, d)


# ---------------------------------------------------------------- the chart behind a finding
# Each insight carries the chart that shows it: a record of the chart registry (tools/fixtures/viz/spec.json, the
# line_chart and bar_chart kinds), built from the numbers its step computed, so the page draws it with the same code,
# colours and table view as every other chart. A point or bar resting on fewer than SMALL_CELL rows is never drawn
# (a gap whose table cell reads '<5'); a chart that would hold fewer than two points or bars is not made.
SMALL_CELL = 5
_MKIND = {"sum": "amount", "stock": "amount", "mean": "average", "count": "count", "share": "share_pct"}
_GRAIN_X = {"year": "Year", "quarter": "Quarter", "month": "Month"}


def _x_key(k, grain: str) -> str:
    k = int(k)
    if grain == "year":
        return str(k)
    if grain == "quarter":
        return "%d-Q%d" % (k // 10, k % 10)
    return "%d-%02d" % (k // 100, k % 100)


def _chart_line(st: "Step", keys, values, counts, grain: str, measure: str, agg: str, title: str, columns) -> None:
    keys, values, counts = list(keys)[-30:], list(values)[-30:], list(counts)[-30:]
    if len(keys) < 2:
        return
    st.chart = {
        "type": "line", "x": [_x_key(k, grain) for k in keys], "grain": grain,
        "values": [float(v) for v in values], "counts": [int(c) for c in counts],
        "measure": measure, "mkind": _MKIND.get(agg, "amount"), "fmt": "plain",
        "title": _words(title), "columns": columns,
    }


def _chart_bars(st: "Step", rows, series, measure: str, mkind: str, fmt: str, title: str, columns, by: str) -> None:
    """rows: (label, [values per series], rows under the smallest value, change text or None), any order."""
    keep = [r for r in rows if r[2] >= SMALL_CELL and all(v == v for v in r[1])][:40]
    keep.sort(key=lambda r: -r[1][-1])
    keep = keep[:12]
    if len(keep) < 2:
        return
    st.chart = {
        "type": "bars", "series": list(series), "rows": keep, "measure": measure, "mkind": mkind, "fmt": fmt,
        "title": _words(title), "columns": columns, "by": by,
    }


def _words(s: str) -> str:
    """A column name as a title reads it: underscores as spaces, the first letter capital (view_count: View count)."""
    t = " ".join(str(s).replace("_", " ").split())
    return t[:1].upper() + t[1:] if t else t


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def _figure(st: "Step", n: int, about: str) -> Optional[Dict[str, Any]]:
    """The registry record of a step's chart (numbered n, 1 to 99), or None when the step stashed none."""
    c = st.chart
    if not c or not st.ok:
        return None
    vals_all = c["values"] if c["type"] == "line" else [v for r in c["rows"] for v in r[1]]
    fmt = "int" if c["fmt"] == "plain" and vals_all and all(float(v).is_integer() for v in vals_all) else c["fmt"]
    cols = [_clip(x, 120) for x in c["columns"] if x][:12]
    common = {
        "section": "other", "supports": _clip(about, 200), "anchors": [], "grade": None, "parent_grade": None,
        "chosen_by": "engine", "why": "Chosen by the engine: the chart behind this finding.",
        "measure": {"label": _clip(c["measure"], 80), "unit": "", "kind": c["mkind"]},
        "summary": _clip(about, 399), "source": "The rows the engine kept",
        "inputs": {"columns": cols, "rows": None, "months": None, "op": _clip(st.op.replace("_", " "), 120)},
    }
    if c["type"] == "line":
        vals, texts, supp = [], [], 0
        for v, k in zip(c["values"], c["counts"]):
            if k < SMALL_CELL:
                vals.append(None)
                texts.append("<5" if k else "")
                supp += 1 if k else 0
            else:
                vals.append(v)
                texts.append(fmt_num(v, fmt)[:80])
        if sum(v is not None for v in vals) < 2:
            return None
        lab = _clip(c["measure"], 80)
        rec = dict(
            id="viz.%d.line_chart" % n, chart="line_chart", kind="lines", title=_clip(c["title"], 120),
            subtitle=_clip("%s to %s, one point a %s" % (c["x"][0], c["x"][-1], c["grain"]), 160),
            data={"x": c["x"], "grain": c["grain"], "x_label": _GRAIN_X[c["grain"]],
                  "series": [{"label": lab, "values": vals}], "forecast": None},
            table={"cols": [_GRAIN_X[c["grain"]], lab], "rows": [[x, t] for x, t in zip(c["x"], texts)]},
            suppressed={"cells": supp, "why": "a point on fewer than %d rows is not shown" % SMALL_CELL if supp else ""},
            **common,
        )
        return rec
    rows, ns = [], len(c["series"])
    scale = 100.0 if c["mkind"] == "share_pct" else 1.0  # a share is drawn in percent, printed from its fraction
    for label, values, _k, change in c["rows"]:
        r = {"label": _clip(label, 80), "values": [round(float(v) * scale, 6) for v in values],
             "texts": [fmt_num(v, fmt)[:24] for v in values]}
        if ns == 2:
            r["change_text"] = (fmt_num(values[1] - values[0], fmt, signed=True) if fmt == "int" else change
                                or fmt_num(values[1] - values[0], fmt, signed=True))[:24] or "0"
        rows.append(r)
    tcols = [_clip(c["by"], 80)] + [_clip(x, 80) for x in c["series"]] + (["change"] if ns == 2 else [])
    trows = [[r["label"]] + r["texts"] + ([r["change_text"]] if ns == 2 else []) for r in rows]
    return dict(
        id="viz.%d.%s" % (n, "grouped_bar" if ns == 2 else "bar_chart"),
        chart="grouped_bar" if ns == 2 else "bar_chart", kind="hbar", title=_clip(c["title"], 120),
        subtitle=_clip("%s for each %s, the largest first" % (c["series"][-1], c["by"]), 160),
        data={"series": [_clip(x, 40) for x in c["series"]], "rows": rows, "other": False},
        table={"cols": tcols, "rows": trows}, suppressed={"cells": 0, "why": ""}, **common,
    )


OWN_WINDOW_MAX = 4      # parallel series read over their own latest 12 months, at most


def _parallel_dim(ctx: Ctx, measure: str) -> Optional[str]:
    """A dimension whose members are parallel series of one measure (two sources of one temperature record): 2 to OWN_WINDOW_MAX
    members, one row per member and month, each with at least 24 months. None when there is none (or the data is not monthly)."""
    import pandas as pd

    sh = ctx.sh
    if sh.date is None or sh.grain != "month" or sh.entity:
        return None
    v = _num(ctx.df[measure])
    k = sh.dates.dt.year * 12 + sh.dates.dt.month - 1
    for d in sh.dims:
        t = _text(ctx.df[d])
        ok = (t != "") & v.notna() & k.notna() & ctx.base
        n = t[ok].nunique()
        if not 2 <= n <= OWN_WINDOW_MAX:
            continue
        f = pd.DataFrame({"t": t[ok], "k": k[ok]})
        if f.duplicated().mean() > 0.05 or f.groupby("t")["k"].nunique().min() < 24:
            continue
        return d
    return None


def op_own_windows(ctx: Ctx, st: Step, measure: str, by: str) -> Step:
    """Parallel series that end at different months (GISTEMP stops at 2025-12, GCAG runs to 2026-07): each read over ITS OWN
    latest 12 months against the 12 before, so a series is never compared over months it did not report. Refused when every
    series ends in the same month (the shared window already reads each over its own months)."""
    import pandas as pd

    sh = ctx.sh
    agg = _agg_for(sh, measure)
    kind = sh.mkind.get(measure, "flow")
    t = _text(ctx.df[by])
    v = _num(ctx.df[measure])
    k = sh.dates.dt.year * 12 + sh.dates.dt.month - 1
    ok = (t != "") & v.notna() & k.notna() & ctx.base
    f = pd.DataFrame({"t": t[ok], "k": k[ok].astype(int), "v": v[ok]})
    lab = lambda x: "%d-%02d" % (x // 12, x % 12 + 1)  # noqa: E731
    rows = []
    for name, g in f.groupby("t", sort=False):
        s_ = g.groupby("k")["v"].mean()
        last = int(s_.index.max())
        if not all((last - i) in s_.index for i in range(24)):
            continue
        cur = s_.loc[last - 11:last]
        prev = s_.loc[last - 23:last - 12]
        a, b = (prev.sum(), cur.sum()) if agg == "sum" else (prev.mean(), cur.mean())
        rows.append((str(name), last, float(a), float(b)))
    if len(rows) < 2:
        return st.refuse("fewer than two series with 24 months of their own")
    if len({r[1] for r in rows}) == 1:
        return st.refuse("every series ends in the same month")
    rows.sort(key=lambda r: (-r[1], r[0]))
    pos = (v.dropna() > 0).mean() > 0.99
    st.word("by", by)
    st.word("measure", measure)
    st.word("verb", "totalled" if agg == "sum" else "averaged")
    st.num("series", len(rows), "int")
    for i, (name, last, a, b) in enumerate(rows, 1):
        st.word("s%d" % i, name)
        st.word("s%d_end" % i, lab(last))
        st.num("s%d_prev" % i, a)
        st.num("s%d_cur" % i, b)
        if kind == "rate":
            st.num("s%d_delta" % i, (b - a) * (100 if _frac_scale(f["v"]) else 1), "pp", signed=True)
        elif a > 0 and pos and _pct_ok(sh, measure):
            st.num("s%d_delta" % i, (b - a) / abs(a), "pct", signed=True)
        else:
            st.num("s%d_delta" % i, b - a, "plain", signed=True)
    st.table = {"columns": [by, "latest month", "12 months before", "latest 12 months", "change"],
                "rows": [[n, lab(l_), _r(a), _r(b), _r(b - a, 4)] for n, l_, a, b in rows]}
    return st


def op_breakdown(
    ctx: Ctx, st: Step, measure: str, by: str, where: Optional[Dict[str, str]] = None
) -> Step:
    """The measure by group (total for a flow, average otherwise); for an average, eta squared: the share of the
    measure's variation the groups account for."""
    import pandas as pd
    import numpy as np

    base = ctx.base.copy()
    if where:
        st.word(
            "scope",
            "Among rows where %s, "
            % " and ".join("%s is %s" % (c, v) for c, v in where.items()),
        )
        for c, v in where.items():
            base &= _group_texts(ctx.df, c) == str(v)
    f = pd.DataFrame({"v": _num(ctx.df[measure]), "g": _group_texts(ctx.df, by)})[base]
    f = f[(f["g"] != "") & f["v"].notna()]
    if f.empty:
        return st.refuse("no rows to break down")
    agg = _agg_for(ctx.sh, measure)
    g = f.groupby("g")["v"].agg(["sum", "mean", "size"])
    g = (
        g[g["size"] >= MIN_GROUP_ROWS]
        if (g["size"] >= MIN_GROUP_ROWS).sum() >= 2
        else g
    )
    if len(g) < 2:
        return st.refuse("fewer than two groups with %d rows" % MIN_GROUP_ROWS)
    st.word("measure", measure)
    st.word("by", by)
    st.num("groups", len(g), "int")
    if agg == "sum":
        g = g.sort_values("sum", ascending=False)
        tot = float(g["sum"].sum())
        top = g.index[0]
        st.word("top", top)
        st.num("top_value", g.loc[top, "sum"])
        st.num("top_share", g.loc[top, "sum"] / tot if tot else float("nan"), "share")
        st.table = {
            "columns": [by, "total", "share", "rows"],
            "rows": [
                [
                    k,
                    _r(r["sum"]),
                    _r(r["sum"] / tot if tot else None, 4),
                    int(r["size"]),
                ]
                for k, r in g.head(12).iterrows()
            ],
        }
        shares = (g["sum"] / tot).clip(lower=0) if tot else g["sum"] * 0
        st.num("hhi", float((shares**2).sum()), "r")
        st.numbers["explained"] = {
            "value": float(shares.max()) if len(shares) else 0.0,
            "text": "",
            "kind": "score",
        }
        _chart_bars(st, [(k, [float(r["sum"])], int(r["size"]), None) for k, r in g.iterrows()], ["total"],
                    measure, "amount", "plain", "%s by %s" % (measure, by), [measure, by], by)
        return st
    g = g.sort_values("mean", ascending=False)
    grand = f["v"].mean()
    sst = float(((f["v"] - grand) ** 2).sum())
    ssb = float((g["size"] * (g["mean"] - grand) ** 2).sum())
    eta = ssb / sst if sst > 0 else 0.0
    hi, lo = g.index[0], g.index[-1]
    st.word("top", hi)
    st.word("bottom", lo)
    st.num("top_value", g.loc[hi, "mean"])
    st.num("bottom_value", g.loc[lo, "mean"])
    st.num("overall", grand)
    st.num("eta2", eta, "share")
    st.num("top_rows", g.loc[hi, "size"], "int")
    st.num("bottom_rows", g.loc[lo, "size"], "int")
    # a 95% interval for the gap between the two ends (Welch); the ends are picked, so it is printed as a range only
    a = f.loc[f["g"] == hi, "v"]
    b = f.loc[f["g"] == lo, "v"]
    gap = a.mean() - b.mean()
    se = (
        math.sqrt(a.var(ddof=1) / max(1, len(a)) + b.var(ddof=1) / max(1, len(b)))
        if len(a) > 1 and len(b) > 1
        else float("nan")
    )
    st.num("gap", gap)
    st.num("gap_lo", gap - 1.96 * se)
    st.num("gap_hi", gap + 1.96 * se)
    st.table = {
        "columns": [by, "average", "rows"],
        "rows": [[k, _r(r["mean"]), int(r["size"])] for k, r in g.head(12).iterrows()],
    }
    st.numbers["explained"] = {"value": float(eta), "text": "", "kind": "score"}
    _chart_bars(st, [(k, [float(r["mean"])], int(r["size"]), None) for k, r in g.iterrows()], ["average"],
                measure, "average", "plain", "Average %s by %s" % (measure, by), [measure, by], by)
    return st


def op_trend(ctx: Ctx, st: Step, measure: str) -> Step:
    """The measure per period over the whole file: the straight-line change per period (OLS, 95% interval), the
    compound growth from the first complete period to the last for a positive measure, the peak and the trough.
    """
    import numpy as np
    import pandas as pd

    sh = ctx.sh
    if sh.date is None:
        return st.refuse("the file has no time axis")
    grain = (
        "year"
        if sh.grain == "year" or sh.dates.dt.year.nunique() >= 8
        else ("quarter" if sh.grain == "quarter" else "month")
    )
    agg = _agg_for(sh, measure)
    f = pd.DataFrame({"v": _num(ctx.df[measure]), "p": _period_key(sh.dates, grain)})[
        ctx.base
    ]
    if sh.entity:
        f["e"] = _text(ctx.df[sh.entity])[ctx.base]
    f = f.dropna(subset=["v", "p"])
    if f.empty:
        return st.refuse("no dated values")
    cnt = f.groupby("p").size()
    keep = list(
        cnt.index
    )  # the first or last period only partly in the file (a year to date) is left out
    if len(keep) > 2 and cnt.iloc[-1] < 0.9 * cnt.iloc[:-1].tail(12).median():
        keep = keep[:-1]
    if len(keep) > 2 and cnt.loc[keep[0]] < 0.9 * cnt.loc[keep[1:]].head(12).median():
        keep = keep[1:]
    f = f[f["p"].isin(keep)]
    s = _aggregate(f, "v", agg, ["p"], "e" if sh.entity else None).dropna().sort_index()
    if len(s) < 4:
        return st.refuse("fewer than four complete periods")
    x = np.arange(len(s), dtype=float)
    y = s.to_numpy(dtype=float)
    slope, icpt = np.polyfit(x, y, 1)
    res = y - (slope * x + icpt)
    se = (
        math.sqrt(
            float((res**2).sum()) / (len(s) - 2) / float(((x - x.mean()) ** 2).sum())
        )
        if len(s) > 2
        else float("nan")
    )
    unit = {"year": "year", "quarter": "quarter", "month": "month"}[grain]
    st.word("measure", measure)
    st.word("unit", unit)
    st.word("first", _period_label(s.index[0], grain))
    st.word("last", _period_label(s.index[-1], grain))
    st.num("periods", len(s), "int")
    st.num("slope", slope, signed=True)
    st.num("slope_lo", slope - 1.96 * se, signed=True)
    st.num("slope_hi", slope + 1.96 * se, signed=True)
    st.num("first_value", y[0])
    st.num("last_value", y[-1])
    pk, tr = int(np.argmax(y)), int(np.argmin(y))
    st.word("peak_at", _period_label(s.index[pk], grain))
    st.num("peak", y[pk])
    st.word("trough_at", _period_label(s.index[tr], grain))
    st.num("trough", y[tr])
    if y[pk] > 0 and (y > 0).all() and _pct_ok(sh, measure):
        st.num("from_peak", (y[-1] - y[pk]) / abs(y[pk]), "pct", signed=True)
    else:
        st.num(
            "from_peak", y[-1] - y[pk], "plain", signed=True
        )  # a difference: a level near zero has no percent
    clear = (slope - 1.96 * se) > 0 or (slope + 1.96 * se) < 0
    st.numbers["clear"] = {"value": 1.0 if clear else 0.0, "text": "", "kind": "score"}
    # the monthly record high and low, beneath a yearly trend too (a price's 2009 trough is a month, not a year's
    # average): a story only when the years move the series more than the seasons do (a price through a crash, not
    # a temperature through its summers), over three full years or more; the months only partly in the file are left
    # out as the periods above are
    story = 0.0
    if sh.grain in ("month", "day", "hour", "week") or grain == "month":
        mf = pd.DataFrame({"v": _num(ctx.df[measure]), "p": _period_key(sh.dates, "month")})[ctx.base]
        if sh.entity:
            mf["e"] = _text(ctx.df[sh.entity])[ctx.base]
        mf = mf.dropna(subset=["v", "p"])
        mc = mf.groupby("p").size()
        mk = list(mc.index)
        if len(mk) > 2 and mc.iloc[-1] < 0.9 * mc.iloc[:-1].tail(12).median():
            mk = mk[:-1]
        if len(mk) > 2 and mc.loc[mk[0]] < 0.9 * mc.loc[mk[1:]].head(12).median():
            mk = mk[1:]
        ms = _aggregate(mf[mf["p"].isin(mk)], "v", agg, ["p"], "e" if sh.entity else None).dropna().sort_index()
        if len(ms) >= 36:
            yrs = pd.Series(ms.to_numpy(dtype=float), index=[int(k) // 100 for k in ms.index])
            size = yrs.groupby(level=0).size()
            full = size[size >= 10].index
            means = yrs[yrs.index.isin(full)].groupby(level=0).mean()
            spans = yrs[yrs.index.isin(full)].groupby(level=0).agg(lambda v: v.max() - v.min())
            if len(means) >= 3 and float(means.max() - means.min()) > 2 * float(spans.median()):
                story = 1.0
                hi, lo = int(np.argmax(ms.to_numpy())), int(np.argmin(ms.to_numpy()))
                st.word("rec_hi_at", _period_label(ms.index[hi], "month"))
                st.num("rec_hi", float(ms.iloc[hi]))
                st.word("rec_lo_at", _period_label(ms.index[lo], "month"))
                st.num("rec_lo", float(ms.iloc[lo]))
    st.numbers["extremes"] = {"value": story, "text": "", "kind": "score"}
    if (
        y[0] > 0
        and y[-1] > 0
        and grain == "year"
        and len(s) >= 3
        and _pct_ok(sh, measure)
    ):
        st.num("cagr", (y[-1] / y[0]) ** (1.0 / (len(s) - 1)) - 1, "pct", signed=True)
    st.table = {
        "columns": [unit, measure],
        "rows": [[_period_label(k, grain), _r(v)] for k, v in s.items()],
    }
    # the chart: the same series, at a coarser grain when it has more periods than a line chart holds (30)
    cs, cc, cg = s, cnt, grain
    for g2 in ("quarter", "year"):
        if len(cs) <= 30 or cg == "year" or (cg == "quarter" and g2 == "quarter"):
            continue
        f2 = pd.DataFrame({"v": _num(ctx.df[measure]), "p": _period_key(sh.dates, g2)})[ctx.base]
        if sh.entity:
            f2["e"] = _text(ctx.df[sh.entity])[ctx.base]
        f2 = f2.dropna(subset=["v", "p"])
        c2 = f2.groupby("p").size()
        k2 = list(c2.index)
        if len(k2) > 2 and c2.iloc[-1] < 0.9 * c2.iloc[:-1].tail(12).median():
            k2 = k2[:-1]
        if len(k2) > 2 and c2.loc[k2[0]] < 0.9 * c2.loc[k2[1:]].head(12).median():
            k2 = k2[1:]
        cs = _aggregate(f2[f2["p"].isin(k2)], "v", agg, ["p"], "e" if sh.entity else None).dropna().sort_index()
        cc, cg = c2, g2
    _chart_line(st, cs.index, cs.to_numpy(dtype=float), [int(cc.get(k, 0)) for k in cs.index], cg, measure, agg,
                "%s by %s" % (measure, cg), [measure, sh.date])
    return st


def op_correlate(ctx: Ctx, st: Step, measures: List[str]) -> Step:
    """The pairs of measures that move together most (Spearman's rank correlation over the rows holding both, with
    a 95% interval by Fisher's z), leaving out pairs that are near copies of each other (|rho| >= 0.98: a size and its own dimensions).
    """
    import numpy as np
    import pandas as pd

    ms = [m for m in measures if m in ctx.sh.measures][:MAX_MEASURES]
    if len(ms) < 2:
        return st.refuse("fewer than two measures")
    f = pd.DataFrame({m: _num(ctx.df[m]) for m in ms})[ctx.base]
    ranks = f.rank()
    best = []
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            both = f[[ms[i], ms[j]]].dropna()
            n = len(both)
            if n < 10:
                continue
            r = float(ranks.loc[both.index, ms[i]].corr(ranks.loc[both.index, ms[j]]))
            a_, b_ = re.sub(r"[^a-z0-9]", "", ms[i].lower()), re.sub(
                r"[^a-z0-9]", "", ms[j].lower()
            )
            if a_ and b_ and (a_ in b_ or b_ in a_):
                continue  # a measure and its own variant (co2, co2_per_capita) move together by construction
            if r != r or abs(r) >= 0.98:
                continue
            z, se = math.atanh(max(-0.999999, min(0.999999, r))), 1 / math.sqrt(
                max(1, n - 3)
            )
            best.append(
                (
                    abs(r),
                    r,
                    ms[i],
                    ms[j],
                    n,
                    math.tanh(z - 1.96 * se),
                    math.tanh(z + 1.96 * se),
                )
            )
    if not best:
        return st.refuse("no pair of measures shares 10 rows")
    best.sort(reverse=True)
    prim = ctx.sh.primary
    mine = [b for b in best if prim in (b[2], b[3]) and b[0] >= 0.3]
    if mine:
        # what moves with the measure the report is about comes first; of near ties (carat and its own x, y, z), the
        # column a reader can name
        top = mine[0][0]

        def named(b):
            other = b[3] if b[2] == prim else b[2]
            return (len(re.sub(r"[^a-z]", "", other.lower())) >= 3, b[0])

        pick = max([b for b in mine if b[0] >= top - 0.01], key=named)
        best.remove(pick)
        if pick[3] == prim:
            pick = (pick[0], pick[1], pick[3], pick[2]) + tuple(pick[4:])
        best.insert(0, pick)
    _a, r, x, y, n, lo, hi = best[0]
    st.word("x", x)
    st.word("y", y)
    st.num("rho", r, "r", signed=True)
    st.num("lo", lo, "r", signed=True)
    st.num("hi", hi, "r", signed=True)
    st.num("n", n, "int")
    st.word(
        "strength",
        (
            "strongly"
            if abs(r) >= 0.7
            else (
                "moderately"
                if abs(r) >= 0.4
                else "weakly" if abs(r) >= 0.1 else "barely"
            )
        ),
    )
    st.word(
        "sign_word", "together" if r > 0 or abs(r) < 0.1 else "in opposite directions"
    )
    st.numbers["explained"] = {"value": abs(r), "text": "", "kind": "score"}
    st.table = {
        "columns": [
            "measure",
            "measure",
            "rank correlation",
            "95% low",
            "95% high",
            "rows",
        ],
        "rows": [
            [b[2], b[3], _r(b[1], 3), _r(b[5], 3), _r(b[6], 3), b[4]] for b in best[:8]
        ],
    }
    return st


# a column that names a person: counting its most common value would single someone out
_PERSON_COL = re.compile(
    r"(^|[^a-z])(customer|client|user|member|person|contact|employee|patient|buyer|seller|owner|e-?mail|"
    r"first_?name|last_?name|full_?name|surname|phone)"
)


# an outcome column's success class: the values that begin with one of these words (himalaya_expeditions: "Success (main peak)" and
# "Success (subpeak)" of 15 termination reasons)
_SUCCESS_WORD = re.compile(r"(?i)^\s*(success(?:ful)?|succeeded|completed?|won|passed|approved|accepted|converted)\b")
OUTCOME_MIN_ROWS = 30      # rows each group of an outcome-rate comparison needs


def op_outcome_rate(ctx: Ctx, st: Step) -> Step:
    """Rows are attempts and one column says how each ended: the share that succeeded (its values that begin with a success word)
    in each group of a two-valued column (with or without oxygen), the pair with the widest gap. Descriptive: the groups differ in
    more than the grouping, so the gap is an association, said so."""
    import pandas as pd

    sh = ctx.sh
    best = None
    for oc in sh.dims:
        t = _text(ctx.df[oc])
        filled = t != ""
        if filled.sum() < 2 * OUTCOME_MIN_ROWS or not 3 <= t[filled].nunique() <= 60:
            continue
        ok = t[filled].map(lambda x: bool(_SUCCESS_WORD.match(x)))
        share = float(ok.mean())
        if not 0.05 <= share <= 0.95:
            continue
        win = t.map(lambda x: bool(_SUCCESS_WORD.match(x)))
        for by in sh.dims:
            if by == oc:
                continue
            g = _group_texts(ctx.df, by)
            use = filled & (g != "") & ctx.base
            if g[use].nunique() != 2:
                continue
            f = pd.DataFrame({"g": g[use], "w": win[use]})
            r = f.groupby("g")["w"].agg(["mean", "size"])
            if int(r["size"].min()) < OUTCOME_MIN_ROWS:
                continue
            r = r.sort_values("mean", ascending=False)
            gap = float(r["mean"].iloc[0] - r["mean"].iloc[1])
            if best is None or gap > best[0]:
                best = (gap, oc, by, r, sorted(set(t[filled][ok])))
    if best is None or best[0] < 0.05:
        return st.refuse("no outcome column with a success class and a two-valued grouping")
    gap, oc, by, r, wins = best
    st.word("outcome", oc)
    st.word("success_word", _SUCCESS_WORD.match(wins[0]).group(1))
    st.word("by", by)
    st.word("a_name", r.index[0])
    st.num("a_rate", r["mean"].iloc[0], "share")
    st.num("a_n", int(r["size"].iloc[0]), "int")
    st.word("b_name", r.index[1])
    st.num("b_rate", r["mean"].iloc[1], "share")
    st.num("b_n", int(r["size"].iloc[1]), "int")
    st.num("gap", gap * 100, "pp", signed=True)
    st.table = {"columns": [by, "success rate", "rows"],
                "rows": [[k, _r(v.mean, 4), int(v.size)] for k, v in r.iterrows()]}
    return st


def op_frequency(ctx: Ctx, st: Step) -> Step:
    """What the rows are mostly about: the most common value of the detailed name column that repeats most (a peak
    in a file of expeditions, a brand in a file of products) and of a coarse group (3 to 20 levels), each with its
    share; rows are weighted by their answer count when the file counts answers (reviews)."""
    import pandas as pd

    sh, df = ctx.sh, ctx.df
    w = next((d["count"] for d in df.attrs.get("derived", [])), None)
    weight = _num(df[w]).fillna(0) if w else pd.Series(1.0, index=df.index)
    unit = "answers" if w else "rows"
    skip = {sh.date, sh.entity}
    fine, coarse = [], []
    for c in df.columns:
        if c in skip or ctx.col(c) is None or c in sh.measures:
            continue
        if _num(df[c]).notna().mean() >= 0.9:
            continue  # numbers (a scale's counts, a year) are not categories to count
        if _PERSON_COL.search(str(c).lower()):
            continue  # people (a customer's name, an email) are identifiers, never a breakdown
        t = _group_texts(df, c)[ctx.base]
        t = t[t != ""]
        if len(t) < 10 or t.head(200).str.contains("@").mean() > 0.2:
            continue
        k = int(t.nunique())
        if k < 3:
            continue
        g = weight[t.index].groupby(t).sum().sort_values(ascending=False)
        tot = float(g.sum())
        if tot <= 0:
            continue
        share = float(g.iloc[0]) / tot
        if share < 1.25 / k:
            continue  # every value about as common as the next (a month column, a balanced design): no "most common"
        readable = float(t.head(500).str.contains(r"[a-z]").mean())
        if k <= 20:
            coarse.append((share, c, g, tot))
        elif k <= 2000 and k <= 0.5 * len(t) and readable >= 0.5:
            fine.append((share, c, g, tot))
    if not fine and not coarse:
        return st.refuse("no column repeats a value often enough to count")
    st.word("unit", unit)
    for key, pool in (("a", fine), ("b", coarse)):
        if not pool:
            continue
        share, c, g, tot = max(pool, key=lambda x: (x[0], -list(df.columns).index(x[1])))
        st.word(key + "_col", c)
        st.word(key + "_top", g.index[0])
        st.num(key + "_top_n", float(g.iloc[0]), "int")
        st.num(key + "_n", tot, "int")
        st.num(key + "_share", share, "share")
        if len(g) >= 2:
            st.word(key + "_second", g.index[1])
            st.num(key + "_second_share", float(g.iloc[1]) / tot, "share")
        st.table.setdefault("columns", [])
        st.table[key] = {"columns": [c, unit], "rows": [[i, _r(v)] for i, v in g.head(10).items()]}
    st.table["columns"], st.table["rows"] = [], []
    pool = coarse or fine
    if pool:
        _s, c, g, _t = max(pool, key=lambda x: (x[0], -list(df.columns).index(x[1])))
        t = _group_texts(df, c)[ctx.base]
        n_rows = t[t != ""].value_counts()
        _chart_bars(
            st, [(k, [float(v)], int(n_rows.get(k, 0)), None) for k, v in g.head(40).items()], [unit],
            unit, "count", "int", "%s by %s" % (unit.capitalize(), c), [c], c,
        )
    return st


def op_confound(ctx: Ctx, st: Step, measure: str, by: str, against: str) -> Step:
    """Whether the groups' averages of a measure just follow their averages of a second measure it moves with (cut and
    price, with stone size behind both): the rank correlation of the two across the groups, the group lowest on the
    measure and its value of the second, and the measure per unit of the second by group (price per carat)."""
    import pandas as pd

    f = pd.DataFrame(
        {"g": _group_texts(ctx.df, by), "m": _num(ctx.df[measure]), "c": _num(ctx.df[against])}
    )[ctx.base]
    f = f[(f["g"] != "")].dropna()
    g = f.groupby("g").agg(m=("m", "mean"), c=("c", "mean"), n=("m", "size"), ms=("m", "sum"), cs=("c", "sum"))
    g = g[g["n"] >= 5]
    if len(g) < 4:
        return st.refuse("fewer than four groups of five rows")
    rho = float(g["m"].rank().corr(g["c"].rank()))
    if rho != rho:
        return st.refuse("the averages do not vary")
    lo, hi = g["m"].idxmin(), g["m"].idxmax()
    st.word("measure", measure)
    st.word("by", by)
    st.word("against", against)
    st.num("rho", rho, "r", signed=True)
    st.num("groups", len(g), "int")
    st.word("low", lo)
    st.num("low_value", g.loc[lo, "m"])
    st.num("low_against", g.loc[lo, "c"])
    st.word("high", hi)
    st.num("high_value", g.loc[hi, "m"])
    st.num("high_against", g.loc[hi, "c"])
    per = g["ms"] / g["cs"].where(g["cs"] > 0)
    if per.notna().sum() >= 4 and (g["c"] > 0).all():
        st.word("per_top", per.idxmax())
        st.num("per_top_value", float(per.max()))
        st.num("per_low_value", float(per.loc[lo]))
    st.numbers["explained"] = {"value": abs(rho), "text": "", "kind": "score"}
    st.table = {
        "columns": [by, "average " + measure, "average " + against, measure + " per " + against, "rows"],
        "rows": [[k, _r(r.m), _r(r.c), _r(per.loc[k]), int(r.n)] for k, r in g.sort_values("m").iterrows()],
    }
    return st


def op_mix(ctx: Ctx, st: Step, measure: str, by: str) -> Step:
    """An average's change split into the change inside the groups (rate) and the rows moving between groups (mix):
    within = sum of w_prev * (mean_cur - mean_prev); mix = the rest (exact, so the parts add up).
    """
    import pandas as pd

    if _agg_for(ctx.sh, measure) != "mean":
        return st.refuse("a mix splits the change of an average; this measure adds up")
    w = _windows(ctx, measure) if ctx.sh.date is not None else None
    if w is None:
        return st.refuse("needs two complete periods")
    unit, prev, cur, lp, lc = w
    f = pd.DataFrame({"v": _num(ctx.df[measure]), "g": _group_texts(ctx.df, by)})
    f["w"] = None
    f.loc[ctx.base & _in(ctx, prev, unit), "w"] = "prev"
    f.loc[ctx.base & _in(ctx, cur, unit), "w"] = "cur"
    f = f.dropna(subset=["v", "w"])
    f = f[f["g"] != ""]
    pa = f[f["w"] == "prev"].groupby("g")["v"].agg(["mean", "size"])
    pb = f[f["w"] == "cur"].groupby("g")["v"].agg(["mean", "size"])
    t = pa.join(pb, lsuffix="_p", rsuffix="_c", how="inner")
    if len(t) < 2:
        return st.refuse("fewer than two groups in both windows")
    wp = t["size_p"] / t["size_p"].sum()
    wc = t["size_c"] / t["size_c"].sum()
    if float((wc - wp).abs().sum()) < 0.05:
        return st.refuse(
            "the groups' shares of the rows barely moved, so there is no mix to split out"
        )
    total = float((wc * t["mean_c"]).sum() - (wp * t["mean_p"]).sum())
    within = float((wp * (t["mean_c"] - t["mean_p"])).sum())
    mix = total - within
    st.word("measure", measure)
    st.word("by", by)
    st.num("total", total, signed=True)
    st.num("within", within, signed=True)
    st.num("mix", mix, signed=True)
    st.num(
        "mix_share",
        (
            abs(mix) / (abs(within) + abs(mix))
            if (abs(within) + abs(mix))
            else float("nan")
        ),
        "share",
    )
    st.numbers["explained"] = {
        "value": (
            abs(mix) / (abs(within) + abs(mix)) if (abs(within) + abs(mix)) else 0.0
        ),
        "text": "",
        "kind": "score",
    }
    st.table = {
        "columns": [
            by,
            "share of rows " + lp,
            "share of rows " + lc,
            "average " + lp,
            "average " + lc,
        ],
        "rows": [
            [k, _r(wp[k], 4), _r(wc[k], 4), _r(r.mean_p), _r(r.mean_c)]
            for k, r in t.iterrows()
        ][:12],
    }
    return st


def op_outliers(ctx: Ctx, st: Step, measure: str) -> Step:
    """Values far from the rest: robust z = 0.6745 * (x - median) / MAD beyond 3.5 (Iglewicz and Hoaglin). A dated
    file is read on its changes from one date to the next within each series (a rising level is not an outlier; a
    jump is), a file with no time axis on its values."""
    import pandas as pd

    sh = ctx.sh
    v = _num(ctx.df[measure])
    on_changes = sh.date is not None and sh.kind != "ledger"
    logged = False
    if on_changes:
        f = pd.DataFrame({"v": v, "d": sh.dates})
        if sh.entity:
            f["e"] = _text(ctx.df[sh.entity])
        f = f[ctx.base].dropna(subset=["v", "d"])
        if sh.entity:
            f = f.sort_values(["e", "d"])
            # entries of very different size (a country's GDP): their changes compare only as shares of their level
            # (a series that starts at zero, emissions before industry, compares from its first positive value on)
            rel = bool((f["v"] > 0).mean() >= 0.9) and _pct_ok(sh, measure)
            if rel:
                before = f.groupby("e")["v"].shift(1)
                # a base under 1% of the entry's own median (a first token value, a unit slip) makes any change a
                # thousand-fold: such a step is not compared at all
                floor = 0.01 * f.groupby("e")["v"].transform("median")
                f["x"] = (f["v"] - before) / before.where((before > 0) & (before >= floor))
            else:
                f["x"] = f.groupby("e")["v"].diff()
            if rel:
                st.word("rel", "relative ")
        else:
            if f["d"].duplicated().mean() > 0.05:
                how = "sum" if _agg_for(sh, measure) == "sum" else "mean"
                f = f.groupby("d", as_index=False)["v"].agg(how)
            f = f.sort_values("d")
            # a positive series that grows several-fold (a price index over a century): its later changes are
            # bigger only because the level is, so they compare as shares of the level
            rel = (
                bool(len(f))
                and bool((f["v"] > 0).all())
                and _pct_ok(sh, measure)
                and float(f["v"].max()) >= 2 * float(f["v"].min())
            )
            f["x"] = f["v"].pct_change() if rel else f["v"].diff()
            if rel:
                st.word("rel", "relative ")
        x = f["x"].dropna()
    else:
        f = None
        x = v[ctx.base].dropna()
        # a positive, right-skewed measure (prices, amounts) is judged on its logarithm, where it is symmetric
        if len(x) >= 20 and (x > 0).all() and float(x.skew()) > 1:
            import numpy as np

            x = np.log(x)
            logged = True
    if len(x) < 20:
        return st.refuse("fewer than 20 values")
    med = x.median()
    mad = (x - med).abs().median()
    if not mad:
        return st.refuse("more than half the values are the same")
    z = 0.6745 * (x - med) / mad
    far = z[z.abs() > OUTLIER_Z]
    st.word("measure", measure)
    st.word(
        "what",
        (
            (
                st.numbers.get("rel", {}).get("text", "")
                + "changes from one date to the next in"
            )
            if on_changes
            else ("values of" if not logged else "values (judged on a log scale) of")
        ),
    )
    st.num("n", len(x), "int")
    st.num("far", len(far), "int")
    st.num("far_share", len(far) / len(x), "share")
    if len(far):
        top = far.abs().sort_values(ascending=False).index[:3]
        kind = "pct" if "rel" in st.numbers else "plain"
        if logged:
            import numpy as np

            st.num("max", float(np.exp(x.loc[top[0]])), kind)
            st.num("median", float(np.exp(med)), kind)
        else:
            st.num("max", x.loc[top[0]], kind, signed=on_changes)
            st.num("median", med, kind, signed=on_changes)
        if on_changes:
            d0 = f.loc[top[0], "d"]
            where = [str(f.loc[top[0], "e"])] if sh.entity else []
            where.append(str(pd.Timestamp(d0).date()))
            st.word("max_where", " ".join(where))
        else:
            # the row is named by its label (a company, a product) when the file has one, else by its first group
            multi = [d for d in sh.dims if not _is_binary(_num(ctx.df[d]))]
            lab = sh.label or (
                max(multi, key=lambda d: _group_texts(ctx.df, d).nunique()) if multi else (sh.dims[0] if sh.dims else None)
            )
            if lab:
                st.word("max_where", str(_group_texts(ctx.df, lab).loc[top[0]]))
        st.word("side", "largest" if float(z.loc[top[0]]) > 0 else "smallest")
        st.table = {
            "columns": ["row", measure, "robust z"],
            "rows": [[str(i), _r(x.loc[i]), _r(z.loc[i], 2)] for i in top],
        }
    return st


def op_panel_change(ctx: Ctx, st: Step, measure: str) -> Step:
    """A panel of levels (prices, rates): each entry's average in the latest window against the one before; never
    an average across entries, which mixes a $500 stock with a $30 one."""
    import pandas as pd

    sh = ctx.sh
    if not sh.entity or sh.date is None:
        return st.refuse("not a panel")
    w = _windows(ctx, measure)
    if w is None:
        return st.refuse("fewer than two complete periods")
    unit, prev, cur, lp, lc = w
    f = pd.DataFrame({"v": _num(ctx.df[measure]), "e": _text(ctx.df[sh.entity])})
    f["w"] = None
    f.loc[ctx.base & _in(ctx, prev, unit), "w"] = "prev"
    f.loc[ctx.base & _in(ctx, cur, unit), "w"] = "cur"
    f = f.dropna(subset=["v", "w"])
    t = (
        f.groupby(["e", "w"])["v"]
        .mean()
        .unstack("w")
        .reindex(columns=["prev", "cur"])
        .dropna()
    )
    if len(t) < 2:
        return st.refuse("fewer than two entries in both windows")
    rate = sh.mkind.get(measure) == "rate"
    pos = (
        bool((t["prev"] > 0).all())
        and _pct_ok(sh, measure)
        and (_num(ctx.df[measure]).dropna() > 0).mean() > 0.99
    )
    t["chg"] = (t["cur"] - t["prev"]) / t["prev"] if pos else t["cur"] - t["prev"]
    t = t.sort_values("chg", ascending=False)
    kind = "pct" if pos else ("pp" if rate else "plain")
    st.word("measure", measure)
    st.word("entity", sh.entity)
    st.word("prev_label", lp)
    st.word("cur_label", lc)
    st.num("n", len(t), "int")
    st.num("up", int((t["chg"] > 0).sum()), "int")
    st.num("median", float(t["chg"].median()), kind, signed=True)
    st.word("best", t.index[0])
    st.num("best_change", t["chg"].iloc[0], kind, signed=True)
    st.word("worst", t.index[-1])
    st.num("worst_change", t["chg"].iloc[-1], kind, signed=True)
    st.table = {
        "columns": [sh.entity, lp, lc, "change"],
        "rows": [
            [k, _r(r.prev), _r(r.cur), _r(r.chg, 4)] for k, r in t.head(12).iterrows()
        ],
    }
    return st


def op_rank_entities(ctx: Ctx, st: Step, measure: str, years: int = 10) -> Step:
    """A panel: who leads in the latest complete period, their share of the total (aggregates left out), and who
    moved most over `years` periods."""
    import pandas as pd

    sh = ctx.sh
    if not sh.entity or sh.date is None:
        return st.refuse("not a panel")
    w = _windows(ctx, measure)
    if w is None:
        return st.refuse("fewer than two complete periods")
    unit, _prev, cur, _lp, lc = w
    e = _text(ctx.df[sh.entity])
    f = pd.DataFrame({"e": e, "v": _num(ctx.df[measure]), "y": sh.dates.dt.year})[
        ctx.base
    ].dropna(subset=["v", "y"])
    f = f[f["e"] != ""]
    last_y = int(max(cur)) if unit == "year" else int(f["y"].max())
    agg = (
        "sum"
        if _agg_for(sh, measure) in ("sum", "stock") and sh.repeat > 1.5
        else "mean"
    )
    now = (
        f[f["y"] == last_y]
        .groupby("e")["v"]
        .agg(agg)
        .dropna()
        .sort_values(ascending=False)
    )
    if len(now) < 3:
        return st.refuse(
            "fewer than three entries in the latest period"
        )  # a ranking of two says nothing
    additive = _agg_for(sh, measure) in ("sum", "stock")
    st.word("measure", measure)
    st.word("year", str(last_y))
    st.num("entries", len(now), "int")
    for i, (k, v) in enumerate(now.head(3).items(), 1):
        st.word("top%d" % i, k)
        st.num("top%d_value" % i, v)
    if additive and now.sum() > 0:
        st.num("top1_share", now.iloc[0] / now.sum(), "share")
        st.num("top5_share", now.head(5).sum() / now.sum(), "share")
    # totals by type over the whole record (disasters_panel, 16 Oct 2026: the deadliest type over 1900-2017, Drought with 11.7
    # million deaths, was said only by an outlier line about one year): a flow that adds up across time, broken down into a few
    # types (WHOLE_TYPES_MAX entries at most: a panel of 200 countries is not a breakdown by type, and its cumulative sum is not the
    # question), over WHOLE_MIN_PERIODS periods or more, says which type holds the most of the whole record and its share
    if sh.mkind.get(measure, "flow") == "flow":
        tot = f.groupby("e")["v"].sum().sort_values(ascending=False)
        # never negative (a net flow's "share of the total" could pass 100%)
        if (3 <= len(tot) <= WHOLE_TYPES_MAX and f["y"].nunique() >= WHOLE_MIN_PERIODS and bool((f["v"] >= 0).all())
                and tot.iloc[0] > 0):
            st.word("whole_first", str(int(f["y"].min())))
            st.word("whole_last", str(int(f["y"].max())))
            st.word("whole_top1", tot.index[0])
            st.num("whole_top1_value", tot.iloc[0])
            st.num("whole_top1_share", tot.iloc[0] / tot.sum(), "share")
    then_y = last_y - years
    then = f[f["y"] == then_y].groupby("e")["v"].agg(agg)
    both = pd.concat([then.rename("then"), now.rename("now")], axis=1).dropna()
    both = both[both["then"] > 0] if (both["then"] > 0).sum() >= 3 else both
    if len(both) >= 3:
        rel = _pct_ok(sh, measure) and bool((both["then"] > 0).all())
        both["growth"] = (
            (both["now"] - both["then"]) / both["then"].abs()
            if rel
            else both["now"] - both["then"]
        )
        gk = "pct" if rel else ("pp" if sh.mkind.get(measure) == "rate" else "plain")
        both = both.sort_values("growth", ascending=False)
        big = (
            both[both["now"] >= both["now"].quantile(0.5)] if len(both) >= 10 else both
        )
        st.word("then_year", str(then_y))
        st.word(
            "movers_scope",
            (
                "among the larger half of entries"
                if len(both) >= 10
                else "among the entries with a figure in both years"
            ),
        )
        st.word("riser", big.index[0])
        st.num("riser_growth", big["growth"].iloc[0], gk, signed=True)
        st.word("faller", big.index[-1])
        st.num("faller_growth", big["growth"].iloc[-1], gk, signed=True)
        # the fastest of ALL entries, when the larger-half rule left it out (gdp_panel: Guyana +303% from 2013, 175th of 233 by
        # size): stated only for an entry with a real base (at least a twentieth of the median entry's) and a figure in every
        # period of the window, so a near-zero base or a gap never reads as growth
        if len(both) >= 10 and rel and both.index[0] != big.index[0]:
            floor = 0.05 * float(both["then"].median())
            for k0, r0 in both.iterrows():
                if k0 == big.index[0]:
                    break
                yrs = f[(f["e"] == k0) & (f["y"] >= then_y) & (f["y"] <= last_y)]["y"].nunique()
                if float(r0["then"]) >= floor and yrs >= last_y - then_y + 1:
                    st.word("riser_all", k0)
                    st.num("riser_all_growth", r0["growth"], gk, signed=True)
                    st.num("movers_all", len(both), "int")
                    break
    # a ranking of levels that do not compare across entries (share prices, index points) is not stated
    st.word(
        "comparable",
        (
            ""
            if sh.mkind.get(measure) == "level" and _has(measure, _UNRANKED_LEVEL_WORDS)
            else "yes"
        ),
    )
    st.table = {
        "columns": [sh.entity, measure + " " + str(last_y)],
        "rows": [[k, _r(v)] for k, v in now.head(12).items()],
    }
    st.numbers["explained"] = {"value": 0.5, "text": "", "kind": "score"}
    return st


def op_rank_labels(ctx: Ctx, st: Step, measure: str) -> Step:
    """Labelled rows (brands, products) ranked by a measure with small samples pulled toward the overall average:
    (n * own + M * overall) / (n + M), n the entry's weight (its answer count when the file counts answers, else its
    rows) and M the median weight, so 5 stars on 3 reviews does not outrank 4.8 on 3,000.
    """
    import pandas as pd

    sh = ctx.sh
    lab = sh.label
    if lab is None:
        return st.refuse("the file has no column naming each row")
    cnt_col = next(
        (
            d["count"]
            for d in ctx.df.attrs.get("derived", [])
            if d["average"] == measure
        ),
        None,
    )
    f = pd.DataFrame(
        {
            "e": _text(ctx.df[lab]),
            "v": _num(ctx.df[measure]),
            "w": _num(ctx.df[cnt_col]) if cnt_col else 1.0,
        }
    )[ctx.base]
    f = f[(f["e"] != "") & f["v"].notna() & (f["w"] > 0)]
    if f["e"].nunique() < 5:
        return st.refuse("fewer than five labelled entries")
    f["vw"] = f["v"] * f["w"]
    g = f.groupby("e").agg(vw=("vw", "sum"), w=("w", "sum"))
    g["own"] = g["vw"] / g["w"]
    overall = float(g["vw"].sum() / g["w"].sum())
    M = (
        min(50.0, max(5.0, float(g["w"].median()))) if float(g["w"].max()) > 1 else 0.0
    )  # held to 5 to 50 (CONTRACT §5.3)
    g["shrunk"] = (g["w"] * g["own"] + M * overall) / (g["w"] + M)
    st.word("plain", "" if M else "yes")
    g = g.sort_values("shrunk", ascending=False)
    unit = "answers" if cnt_col else "rows"
    st.word("measure", measure)
    st.word("unit", unit)
    st.num("overall", overall)
    st.num("m", M, "int")
    for i, (k, r) in enumerate(g.head(3).iterrows(), 1):
        st.word("top%d" % i, k)
        st.num("top%d_shrunk" % i, r.shrunk)
        st.num("top%d_raw" % i, r.own)
        st.num("top%d_n" % i, r.w, "int")
    if cnt_col and len(g) >= 5:
        # the entry with the most answers is part of the story too: the best-known brand, whatever its average
        big = g["w"].idxmax()
        st.word("most", big)
        st.num("most_n", float(g.loc[big, "w"]), "int")
        st.num("most_raw", float(g.loc[big, "own"]))
    raw = g.sort_values("own", ascending=False)
    if raw.index[0] not in list(g.index[:3]):
        st.word("raw_top", raw.index[0])
        st.num("raw_top_value", raw["own"].iloc[0])
        st.num("raw_top_n", raw["w"].iloc[0], "int")
        st.word(
            "raw_top_unit", unit[:-1] if int(round(raw["w"].iloc[0])) == 1 else unit
        )
    st.table = {
        "columns": [lab, "pulled-toward-average", "own average", unit],
        "rows": [
            [k, _r(r.shrunk), _r(r.own), _r(r.w)] for k, r in g.head(12).iterrows()
        ],
    }
    return st


def op_periods(ctx: Ctx, st: Step) -> Step:
    """A table of counts by period in its columns (incidents_85_99, incidents_00_14): each quantity's total in the
    first and the latest period over the rows that report both, the entry that leads each, and, when a column says
    how large each row is (seat-km flown), the latest period read as a rate per unit of size with small entries
    pulled toward the overall rate: (count + R * E0) / (size + E0), R the overall rate and E0 the median size.
    """
    import pandas as pd

    sh, df = ctx.sh, ctx.df
    if not sh.periods:
        return st.refuse("no columns repeat one quantity by period")
    stems = _period_order(sh, df)[:3]
    lab = sh.label
    if lab is None:
        # a short table (56 airlines) keeps its name column among the groups: one row each still names the rows
        for d in sh.dims:
            t = _text(df[d])
            if (t != "").mean() >= 0.9 and t[t != ""].nunique() >= 0.9 * max(1, int((t != "").sum())) >= 5:
                lab = d
                break
    names = _text(df[lab]) if lab else None
    first_l, last_l = sh.periods[stems[0]][0][1], sh.periods[stems[0]][-1][1]
    st.word("first", first_l)
    st.word("last", last_l)
    rows = []
    n_both = None
    for i, stem in enumerate(stems, 1):
        ps = sh.periods[stem]
        ca, cb = ps[0][2], ps[-1][2]
        a, b = _num(df[ca])[ctx.base], _num(df[cb])[ctx.base]
        both = a.notna() & b.notna()
        if both.sum() < 3:
            continue
        ta, tb = float(a[both].sum()), float(b[both].sum())
        whole = bool(((a[both] % 1) == 0).all() and ((b[both] % 1) == 0).all())
        k = "s%d" % i
        st.word(k, _readable(stem))
        st.num(k + "_a", ta, "int" if whole else "plain")
        st.num(k + "_b", tb, "int" if whole else "plain")
        st.word(k + "_dir", "rose" if tb > ta else "fell" if tb < ta else "held")
        if ta > 0 and tb >= 0 and _pct_ok(sh, cb):
            st.num(k + "_change", tb / ta - 1.0, "pct", signed=True)
        else:
            st.num(k + "_change", tb - ta, signed=True)
        n_both = int(both.sum()) if n_both is None else max(n_both, int(both.sum()))
        if names is not None:
            for side, col, ser in (("a", ca, a), ("b", cb, b)):
                g = ser[both].groupby(names[ctx.base][both]).sum()
                g = g[g.index != ""]
                if len(g) >= 3 and float(g.max()) > 0:
                    st.word("%s_lead_%s" % (k, side), g.idxmax())
                    st.num("%s_lead_%s_value" % (k, side), float(g.max()), "int" if whole else "plain")
        rows.append([_readable(stem), first_l, _r(ta), last_l, _r(tb), _r(tb - ta)])
    if not rows:
        return st.refuse("too few rows report both periods")
    st.num("n", n_both or 0, "int")
    st.word("unit", (lab + "s") if lab and re.fullmatch(r"[a-z]+", lab) and not lab.endswith("s") else "rows")
    st.table = {
        "columns": ["quantity", "first period", "total", "latest period", "total", "change"],
        "rows": rows,
    }
    # the latest period read against size
    skip = [c for v in sh.periods.values() for _y, _l, c in v]
    ex = _exposure_col(sh, df, skip)
    if ex is not None and names is not None:
        cb = sh.periods[stems[0]][-1][2]
        f = pd.DataFrame({"e": names, "c": _num(df[cb]), "x": _num(df[ex])})[ctx.base]
        f = f[(f["e"] != "") & f["c"].notna() & (f["x"] > 0)]
        g = f.groupby("e").agg(c=("c", "sum"), x=("x", "sum"))
        if len(g) >= 5 and float(g["c"].sum()) > 0:
            med = float(g["x"].median())
            R = float(g["c"].sum() / g["x"].sum())
            # the unit that puts the overall rate at 1 or more: 40 per billion seat-km, not 0.04 per million
            scale = next((x for x in (1.0, 1e3, 1e6, 1e9, 1e12) if R * x >= 1), 1e12)
            word = {1e3: "thousand", 1e6: "million", 1e9: "billion", 1e12: "trillion"}.get(scale, "")
            g["rate"] = g["c"] / g["x"] * scale
            g["shrunk"] = (g["c"] + R * med) / (g["x"] + med) * scale
            g = g.sort_values("shrunk", ascending=False)
            top = g.index[0]
            st.word("exposure", _readable(ex))
            st.word("per", ("per %s " % word if word else "per ") + _readable(ex))
            st.num("spread", float(g["x"].max() / g["x"].min()), "int")
            st.num("overall_rate", R * scale)
            st.word("rate_top", top)
            st.word("rate_top_s", top + ("'" if top.endswith("s") else "'s"))
            st.num("rate_top_value", g["rate"].iloc[0])
            st.num("rate_top_count", g["c"].iloc[0], "int" if (g["c"] % 1 == 0).all() else "plain")
            st.num("rate_top_shrunk", g["shrunk"].iloc[0])
            st.table["rate"] = {
                "columns": [lab, "rate (pulled toward overall)", "own rate", "count", ex],
                "rows": [[k2, _r(r.shrunk), _r(r.rate), _r(r.c), _r(r.x)] for k2, r in g.head(12).iterrows()],
            }
    return st


def _chi2_sf(x: float, k: int) -> float:
    """Upper tail of a chi-square with k degrees of freedom (Wilson-Hilferty; good to about 0.01 for k >= 2)."""
    if k <= 0:
        return 1.0
    z = ((x / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))
    return 0.5 * math.erfc(z / math.sqrt(2))


def op_crosstab(ctx: Ctx, st: Step) -> Step:
    """A survey: every pair of answer columns (2 to 12 answers each), cross-tabulated; the pairs with the strongest
    bias-corrected Cramer's V (Bergsma 2013), each with its chi-square p-value, and the cell furthest above what
    independence predicts."""
    import pandas as pd

    cols = [
        c
        for c in ctx.df.columns
        if c in ctx.sh.dims and 2 <= _text(ctx.df[c]).replace("", None).nunique() <= 12
    ][:16]
    res = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = _group_texts(ctx.df, cols[i]), _group_texts(ctx.df, cols[j])
            ok = ctx.base & (a != "") & (b != "")
            n = int(ok.sum())
            if n < 30:
                continue
            # skip logic: a question asked only after one answer to another links them by construction
            skip = False
            for x, y in ((a, b), (b, a)):
                asked = ctx.base & (y != "")
                share = x[asked & (x != "")].value_counts(normalize=True)
                if (
                    len(share)
                    and share.iloc[0] >= 0.98
                    and (ctx.base & (x != "")).sum() > 1.1 * asked.sum()
                ):
                    skip = True
            if skip:
                continue
            t = pd.crosstab(a[ok], b[ok])
            # an answer given by fewer than 5 respondents (a stray second header row, a typo) is left out: one row
            # in a category of its own lines up perfectly with every other question
            t = t.loc[t.sum(1) >= MIN_GROUP_ROWS, t.sum(0) >= MIN_GROUP_ROWS]
            n = int(t.values.sum())
            if t.shape[0] < 2 or t.shape[1] < 2 or n < 30:
                continue
            exp = t.sum(1).values[:, None] * t.sum(0).values[None, :] / n
            chi2 = float(((t.values - exp) ** 2 / exp).sum())
            r, k = t.shape
            phi2 = max(0.0, chi2 / n - (k - 1) * (r - 1) / (n - 1))
            rc, kc = r - (r - 1) ** 2 / (n - 1), k - (k - 1) ** 2 / (n - 1)
            v = math.sqrt(phi2 / max(1e-12, min(kc - 1, rc - 1)))
            lift = t.values / exp
            ii, jj = divmod(int(lift.argmax()), k)
            res.append(
                (
                    v,
                    cols[i],
                    cols[j],
                    n,
                    _chi2_sf(chi2, (r - 1) * (k - 1)),
                    str(t.index[ii]),
                    str(t.columns[jj]),
                    float(t.values[ii, jj] / t.values[ii].sum()),
                    float(t.values[:, jj].sum() / n),
                    int(t.values[ii].sum()),
                )
            )
    ctx.pairs = list(res)  # every pair scored, for op_answers: which questions the rest of the survey hangs on
    if not res:
        return st.refuse("no two answer columns share 30 respondents")
    res.sort(reverse=True)
    v, a, b, n, p, ra, cb, share, base, ra_n = res[0]
    st.word("a", _qtext(a))
    st.word("b", _qtext(b))
    st.num("v", v, "r")
    st.num("n", n, "int")
    st.num("respondents", int(ctx.base.sum()), "int")
    st.num("pairs", len(res), "int")
    st.num("ra_n", ra_n, "int")
    # the strongest of many pairs: its p-value is read against 0.01 shared out over every pair compared (Bonferroni)
    st.word(
        "p_word",
        (
            "chance alone would rarely make a link this strong, even among this many pairs"
            if p * len(res) < 0.01
            else "a link this weak can come from chance among this many pairs"
        ),
    )
    st.word("ra", ra)
    st.word("cb", cb)
    st.num("share", share, "share")
    st.num("base", base, "share")
    st.word(
        "detail",
        'respondents who answered %s to "%s" answered %s to "%s" %s of the time, against %s of all who answered both'
        % (ra, a, cb, b, st.numbers["share"]["text"], st.numbers["base"]["text"]),
    )
    if len(res) > 1:
        st.word("a2", _qtext(res[1][1]))
        st.word("b2", _qtext(res[1][2]))
        st.num("v2", res[1][0], "r")
    st.numbers["explained"] = {"value": v, "text": "", "kind": "score"}
    st.table = {
        "columns": ["answer", "answer", "Cramer's V", "respondents", "p"],
        "rows": [[x[1], x[2], _r(x[0], 3), x[3], _r(x[4], 4)] for x in res[:8]],
    }
    return st


def _qtext(c: str, cap: int = 160) -> str:
    """A survey question as a sentence quotes it: markup a form tool left in the header (<br>) read as a space, runs of
    space as one, cut at a word past `cap` characters."""
    t = " ".join(re.sub(r"<[^>]*>", " ", str(c)).split())
    if len(t) > cap:
        t = t[:cap].rsplit(" ", 1)[0] + "..."
    return t


# a question about who answered, not what they think: ordered after the survey's own questions
_DEMOGRAPHIC = re.compile(
    r"\b(age|gender|sex|income|education|degree|region|location|state|country|zip|postcode|ethnicity|race|"
    r"household|marital|occupation|employment)\b",
    re.I,
)


def op_answers(ctx: Ctx, st: Step) -> Step:
    """A survey: each question's most common answer among those who answered it (answers given by fewer than 5
    respondents left out of the ranking, never out of the base). The questions are ordered by how strongly their
    answers go with the rest (the sum of the crosstab's Cramer's V over every pair scored, when the crosstab ran),
    so the survey's subject leads and a screening question trails; then the question with the widest agreement and
    the most divided one."""
    cols = [
        c
        for c in ctx.df.columns
        if c in ctx.sh.dims and 2 <= _text(ctx.df[c]).replace("", None).nunique() <= 12
    ][:16]
    rows = []
    for c in cols:
        a = _group_texts(ctx.df, c)
        ok = ctx.base & (a != "")
        n = int(ok.sum())
        if n < 30:
            continue
        vc = a[ok].value_counts()
        vc = vc[vc >= MIN_GROUP_ROWS]
        if len(vc) < 2:
            continue
        rows.append(
            (
                c,
                n,
                str(vc.index[0]),
                float(vc.iloc[0] / n),
                str(vc.index[1]),
                float(vc.iloc[1] / n),
                len(vc),
            )
        )
    if not rows:
        return st.refuse("no question has two answers given by 5 or more of 30 respondents")
    weight = {c: 0.0 for c, *_ in rows}
    for v, x, y, *_ in getattr(ctx, "pairs", None) or []:
        for c in (x, y):
            if c in weight:
                weight[c] += float(v)
    topic = [w for w in getattr(ctx, "topic", None) or [] if isinstance(w, str)]

    def on_topic(c: str) -> bool:
        low = c.lower()
        return any(re.search(r"\b" + re.escape(w[:-1] if w.endswith("s") else w), low) for w in topic)

    # the questions naming the file's subject first, demographics (who answered) last, then how strongly each
    # question's answers go with the rest
    order = sorted(
        rows,
        key=lambda r: (
            not on_topic(r[0]),
            bool(_DEMOGRAPHIC.search(r[0])),
            -weight[r[0]],
            -r[1],
            cols.index(r[0]),
        ),
    )
    for i, (c, n, t1, s1, t2, s2, k) in enumerate(order[:3], 1):
        st.word("q%d" % i, _qtext(c))
        st.word("q%d_top" % i, t1)
        st.num("q%d_share" % i, s1, "share")
        st.num("q%d_n" % i, n, "int")
        st.word("q%d_second" % i, t2)
        st.num("q%d_second_share" % i, s2, "share")
    st.num("questions", len(rows), "int")
    opinion = [r for r in rows if not _DEMOGRAPHIC.search(r[0])]
    if len(opinion) >= 3:
        agree = max(opinion, key=lambda r: (r[3], r[1]))
        split = min(opinion, key=lambda r: (r[3] * r[6] ** 0.5, -r[1]))
        if agree[0] != split[0]:
            st.word("agree_q", _qtext(agree[0]))
            st.word("agree_top", agree[2])
            st.num("agree_share", agree[3], "share")
            st.word("split_q", _qtext(split[0]))
            st.word("split_top", split[2])
            st.num("split_share", split[3], "share")
            st.word("split_second", split[4])
            st.num("split_second_share", split[5], "share")
    st.table = {
        "columns": ["question", "most common answer", "share", "next answer", "share", "answered"],
        "rows": [[r[0], r[2], _r(r[3], 4), r[4], _r(r[5], 4), r[1]] for r in order],
    }
    return st


_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _monthly(ctx: Ctx, measure: str):
    """The measure month by month over complete months, as a Series keyed by year * 12 + month - 1 (consecutive
    months are consecutive keys): a flow's total, a stock's sum of each entity's average, otherwise the average. A
    first or last month only partly in the file (fewer rows than 90% of its neighbours' median) is left out.
    """
    import pandas as pd

    sh = ctx.sh
    d = sh.dates
    f = pd.DataFrame(
        {"v": _num(ctx.df[measure]), "p": d.dt.year * 12 + d.dt.month - 1}
    )[ctx.base]
    if sh.entity:
        f["e"] = _text(ctx.df[sh.entity])[ctx.base]
    f = f.dropna(subset=["v", "p"])
    if f.empty:
        return f["v"]
    f["p"] = f["p"].astype(int)
    cnt = f.groupby("p").size().sort_index()
    keep = list(cnt.index)
    if len(keep) > 2 and cnt.iloc[-1] < 0.9 * cnt.iloc[:-1].tail(12).median():
        keep = keep[:-1]
    if len(keep) > 2 and cnt.loc[keep[0]] < 0.9 * cnt.loc[keep[1:]].head(12).median():
        keep = keep[1:]
    f = f[f["p"].isin(keep)]
    return (
        _aggregate(f, "v", _agg_for(sh, measure), ["p"], "e" if sh.entity else None)
        .dropna()
        .sort_index()
    )


def _last_run(s):
    """The longest stretch of consecutive keys that ends at the last key."""
    if not len(s):
        return s
    keys = list(s.index)
    i = len(keys) - 1
    while i > 0 and keys[i] - keys[i - 1] == 1:
        i -= 1
    return s.iloc[i:]


def _longest_run(s):
    """The longest stretch of consecutive keys (the latest of equals): a series with a month missing (an unpublished
    release) is read on its longest unbroken part, never across the gap."""
    if not len(s):
        return s
    keys = list(s.index)
    best, start = (0, 1), 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] - keys[i - 1] != 1:
            if i - start >= best[1] - best[0]:
                best = (start, i)
            start = i
    return s.iloc[best[0] : best[1]]


def _mlab(k: int) -> str:
    return "%d-%02d" % (k // 12, k % 12 + 1)


def _change_kind(ctx: Ctx, measure: str, values) -> Tuple[str, bool, float]:
    """How a change of this measure prints: (kind, relative, scale). A rate changes in points, a positive measure
    in percent, anything that crosses zero as a plain difference."""
    if ctx.sh.mkind.get(measure) == "rate":
        return "pp", False, (100.0 if _frac_scale(values) else 1.0)
    if (
        (values > 0).all()
        and _pct_ok(ctx.sh, measure)
        and (_num(ctx.df[measure]).dropna() > 0).mean() > 0.99
    ):
        return "pct", True, 1.0
    return "plain", False, 1.0


def op_yoy(ctx: Ctx, st: Step, measure: str) -> Step:
    """Year on year: the latest complete month against the same month a year earlier, and the latest three months
    against the same three a year earlier (official statistics read a monthly flow this way: it takes the season
    out without any model)."""
    sh = ctx.sh
    if sh.date is None or sh.grain not in ("month", "week", "day"):
        return st.refuse("needs dates finer than a year")
    s = _monthly(ctx, measure)
    if len(s) < 13:
        return st.refuse("fewer than 13 complete months")
    last = int(s.index[-1])
    if (last - 12) not in s.index:
        return st.refuse("the same month a year earlier is not in the file")
    kind, rel, scale = _change_kind(ctx, measure, s)
    a, b = float(s.loc[last - 12]), float(s.loc[last])
    st.word("measure", measure)
    st.word("cur_label", _mlab(last))
    st.word("prev_label", _mlab(last - 12))
    st.num("prev", a)
    st.num("cur", b)
    st.num("change", (b - a) / abs(a) if rel else (b - a) * scale, kind, signed=True)
    st.word("direction", "rose" if b > a else "fell" if b < a else "held level")
    rows = [[_mlab(last - 12), _r(a)], [_mlab(last), _r(b)]]
    q_now = [last - i for i in range(3)]
    q_then = [k - 12 for k in q_now]
    if all(k in s.index for k in q_now + q_then):
        agg = _agg_for(sh, measure)
        qa = float(
            s.loc[q_then].sum()
            if agg == "sum" and kind != "pp"
            else s.loc[q_then].mean()
        )
        qb = float(
            s.loc[q_now].sum() if agg == "sum" and kind != "pp" else s.loc[q_now].mean()
        )
        st.word("q_from", _mlab(q_now[-1]))
        st.num(
            "q_change",
            (qb - qa) / abs(qa) if rel and qa else (qb - qa) * scale,
            kind,
            signed=True,
        )
        same = (qb - qa) * (b - a) > 0
        st.word("q_word", "the same way" if same else "the other way")
    st.table = {"columns": ["month", measure], "rows": rows}
    return st


def op_seasonality(ctx: Ctx, st: Step, measure: str) -> Step:
    """How much of the month-to-month movement is the time of year: the measure month by month (the latest run of
    consecutive months, at most ten years), its trend taken out with a centred 12-month moving average (2x12), then
    the share of what is left that the month of the year accounts for (eta squared of the month-of-year means). A
    positive measure is read on its logarithm, so a season that scales with the level counts once.
    """
    import numpy as np
    import pandas as pd

    sh = ctx.sh
    if sh.date is None or sh.grain not in ("month", "week", "day"):
        return st.refuse("needs dates finer than a year")
    s = _longest_run(_monthly(ctx, measure).tail(180)).tail(120)
    if len(s) < 36:
        return st.refuse("fewer than 36 consecutive complete months")
    y = s.to_numpy(dtype=float)
    logged = (
        bool((y > 0).all())
        and _pct_ok(sh, measure)
        and (_num(ctx.df[measure]).dropna() > 0).mean() > 0.99
    )
    x = np.log(y) if logged else y
    w = np.r_[0.5, np.ones(11), 0.5] / 12.0
    ma = np.convolve(x, w, mode="valid")  # centred: ma[i] belongs to month i + 6
    det = x[6 : 6 + len(ma)] - ma
    keys = np.asarray(s.index[6 : 6 + len(ma)], dtype=int)
    moy = keys % 12
    if len(det) < 24 or len(set(moy.tolist())) < 12:
        return st.refuse("too few months once the trend is taken out")
    g = pd.Series(det).groupby(moy).mean()
    grand = float(det.mean())
    sst = float(((det - grand) ** 2).sum())
    if sst <= 0:
        return st.refuse("no movement around the trend")
    ssb = float(sum(((moy == m).sum()) * (g[m] - grand) ** 2 for m in g.index))
    # omega squared, not eta squared: twelve month-of-year means fitted to noise alone take about 11 / (n - 1) of
    # its variance (10% over ten years), so the raw share overstates a season; omega squared takes that out
    n_, k_ = len(det), len(g)
    msw = (sst - ssb) / max(1, n_ - k_)
    eta = max(0.0, min(1.0, (ssb - (k_ - 1) * msw) / (sst + msw)))
    hi, lo = int(g.idxmax()), int(g.idxmin())
    st.word("measure", measure)
    st.num("months", len(s), "int")
    st.word("first", _mlab(int(s.index[0])))
    st.word("last", _mlab(int(s.index[-1])))
    st.num("strength", eta, "share")
    st.word("peak", _MONTH_NAMES[hi])
    st.word("trough", _MONTH_NAMES[lo])
    if logged:
        st.num("gap", math.exp(g[hi] - g[lo]) - 1, "pct")
        st.word("gap_how", "above")
    else:
        st.num("gap", g[hi] - g[lo])
        st.word("gap_how", "more than")
    st.word(
        "strength_word", "strong" if eta >= 0.5 else "clear" if eta >= 0.2 else "weak"
    )
    st.numbers["explained"] = {"value": float(eta), "text": "", "kind": "score"}
    st.table = {
        "columns": [
            "month of year",
            "against the trend" + (" (log)" if logged else ""),
        ],
        "rows": [[_MONTH_NAMES[m], _r(g[m])] for m in sorted(g.index)],
    }
    return st


def op_volatility(ctx: Ctx, st: Step, measure: str) -> Step:
    """How much the measure moves from one period to the next, recently against before: the standard deviation of
    its period-on-period changes (relative for a positive measure, in points for a rate) over the latest 12 months
    (or 10 years for yearly data) against the periods before them."""
    import numpy as np
    import pandas as pd

    sh = ctx.sh
    if sh.date is None:
        return st.refuse("the file has no time axis")
    if sh.grain in ("month", "week", "day"):
        s, unit, recent = _monthly(ctx, measure), "month", 12
        lab = _mlab
    else:
        f = pd.DataFrame({"v": _num(ctx.df[measure]), "p": sh.dates.dt.year})[ctx.base]
        if sh.entity:
            f["e"] = _text(ctx.df[sh.entity])[ctx.base]
        f = f.dropna(subset=["v", "p"])
        cnt = f.groupby("p").size()
        full = cnt[cnt >= 0.9 * cnt.max()].index
        f = f[f["p"].isin(full)]
        s = (
            _aggregate(f, "v", _agg_for(sh, measure), ["p"], "e" if sh.entity else None)
            .dropna()
            .sort_index()
        )
        s.index = s.index.astype(int)
        s, unit, recent = s, "year", 10
        lab = str
    if len(s) < 3 * recent + 1:
        return st.refuse("fewer than %d complete periods" % (3 * recent + 1))
    kind, rel, scale = _change_kind(ctx, measure, s)
    ch = s.pct_change() if rel else s.diff() * scale
    ch = ch[
        pd.Series(s.index, index=s.index).diff() == 1
    ].dropna()  # never across a missing period
    ch = ch[np.isfinite(ch)]
    now, before = ch.tail(recent), ch.iloc[:-recent]
    if len(before) < 2 * recent:
        return st.refuse("too few changes before the recent window")
    sd_now, sd_before = float(now.std(ddof=1)), float(before.std(ddof=1))
    if not sd_before or sd_before != sd_before or sd_now != sd_now:
        return st.refuse("the measure does not move")
    if rel and max(sd_now, sd_before) > 1:
        return st.refuse(
            "its changes run past 100% a period, too large to compare as percents"
        )
    ratio = sd_now / sd_before
    k = "share" if rel else kind
    st.word("measure", measure)
    st.word("unit", unit)
    st.word(
        "how",
        (
            "relative changes"
            if rel
            else "changes in points" if kind == "pp" else "changes"
        ),
    )
    st.num("n_recent", len(now), "int")
    st.num("n_before", len(before), "int")
    st.word("recent_from", lab(int(now.index[0])))
    st.word("last", lab(int(now.index[-1])))
    st.word("before_from", lab(int(before.index[0])))
    st.num("sd_recent", sd_now, k)
    st.num("sd_before", sd_before, k)
    st.num("ratio", ratio, "r")
    st.word(
        "compare",
        (
            "larger than"
            if ratio >= 1.5
            else "smaller than" if ratio <= 1 / 1.5 else "about as large as"
        ),
    )
    big = now.abs().idxmax()
    st.word("big_at", lab(int(big)))
    st.num("big", now.loc[big], "pct" if rel else kind, signed=True)
    st.numbers["explained"] = {
        "value": float(min(1.0, abs(math.log(ratio)) / math.log(3))),
        "text": "",
        "kind": "score",
    }
    st.table = {
        "columns": ["window", "periods", "standard deviation of changes"],
        "rows": [
            [
                "%s to %s" % (lab(int(before.index[0])), lab(int(before.index[-1]))),
                len(before),
                _r(sd_before),
            ],
            [
                "%s to %s" % (lab(int(now.index[0])), lab(int(now.index[-1]))),
                len(now),
                _r(sd_now),
            ],
        ],
    }
    return st


def op_concentration(
    ctx: Ctx, st: Step, measure: str, by: Optional[str] = None
) -> Step:
    """How much of the total the largest groups hold: the top group's, the top three's and the top five's shares, the
    Herfindahl-Hirschman index (sum of squared shares, 0 to 10,000) and its effective number of groups (1 / the index
    as a fraction), in the latest complete window; with a time axis, the top five's share then and now (a panel: ten
    periods earlier when the file has them, else the window before). Needs a measure that adds up; a panel's
    aggregate rows are left out."""
    import pandas as pd

    sh = ctx.sh
    agg = _agg_for(sh, measure)
    if agg not in ("sum", "stock"):
        return st.refuse(
            "concentration needs a measure that adds up (a total), not an average or a rate"
        )
    g = by or sh.entity
    if g is None:
        return st.refuse("needs a group column")
    f = pd.DataFrame({"v": _num(ctx.df[measure]), "g": _group_texts(ctx.df, g)})
    if sh.entity:
        f["e"] = _text(ctx.df[sh.entity])
    f = f[ctx.base & (f["g"] != "")].dropna(subset=["v"])

    def shares(frame):
        if agg == "stock" and "e" in frame:
            t = frame.groupby(["g", "e"])["v"].mean().groupby("g").sum()
        else:
            t = frame.groupby("g")["v"].sum()
        t = t.clip(lower=0)
        tot = float(t.sum())
        return (t / tot).sort_values(ascending=False) if tot > 0 else None

    then_lab = None
    if sh.date is not None:
        w = _windows(ctx, measure)
        if w is None:
            return st.refuse("fewer than two complete periods")
        unit, prev, cur, lp, lc = w
        now = shares(f[_in(ctx, cur, unit).loc[f.index]])
        now_rows = f[_in(ctx, cur, unit).loc[f.index]].groupby("g").size()
        then_keys, then_lab = prev, lp
        if (
            sh.kind == "panel"
            and unit == "year"
            and (cur[0] - 10) in set(sh.dates.dt.year.dropna().astype(int))
        ):
            then_keys, then_lab = [cur[0] - 10], str(cur[0] - 10)
        then = shares(f[_in(ctx, then_keys, unit).loc[f.index]])
    else:
        lc = ""
        now, then = shares(f), None
        now_rows = f.groupby("g").size()
    if now is None or len(now) < 3:
        return st.refuse("fewer than three groups with a positive total")
    hhi = float((now**2).sum())
    st.word("measure", measure)
    st.word("by", g)
    st.word("cur_label", lc)
    st.num("groups", len(now), "int")
    st.word("top1", now.index[0])
    st.num("top1_share", now.iloc[0], "share")
    if len(now) >= 2:
        st.word("top2", now.index[1])
        st.num("top2_share", now.iloc[1], "share")
    st.num("top3_share", now.head(3).sum(), "share")
    if len(now) >= 6:
        st.num("top5_share", now.head(5).sum(), "share")
    st.num("hhi", hhi * 10000, "int")
    st.num("effective", 1 / hhi if hhi else float("nan"))
    _chart_bars(
        st, [(k, [float(v)], int(now_rows.get(k, 0)), None) for k, v in now.head(40).items()], ["share"],
        "share of " + measure, "share_pct", "share",
        "Share of %s by %s%s" % (measure, g, ", " + lc if lc else ""), [measure, g], g,
    )
    # the index can never fall below 1 / groups (four equal groups score 2,500), so the words read the index
    # normalised to that floor: 0 for equal groups, 1 for one group holding everything
    n_g = len(now)
    norm = (hhi - 1.0 / n_g) / (1 - 1.0 / n_g) if n_g > 1 else 1.0
    st.num("hhi_norm", norm, "r")
    st.word(
        "level_word",
        (
            "highly concentrated"
            if norm >= 0.25
            else "moderately concentrated" if norm >= 0.15 else "not concentrated"
        ),
    )
    k = 5 if len(now) >= 6 else 3
    if (
        then is not None and len(then) >= 3 and len(now) > k
    ):  # the top k of k groups is everything
        a, b = float(then.head(k).sum()), float(now.head(k).sum())
        st.word("then_label", then_lab)
        st.word("top_k", "five" if k == 5 else "three")
        st.num("topk_then", a, "share")
        st.num("topk_now", b, "share")
        st.num("topk_change", (b - a) * 100, "pp", signed=True)
        st.word(
            "trend_word",
            (
                "more concentrated"
                if b - a >= 0.01
                else "less concentrated" if a - b >= 0.01 else "about as concentrated"
            ),
        )
        # the largest groups then, not now: who the top was is part of the story
        st.word("top1_then", then.index[0])
    if sh.kind in ("ledger", "cross_section"):
        # rows that are events (ads, orders, expeditions): which group has the most of them over the whole file,
        # apart from which holds the most of the measure (Bud Light ran the most ads; Doritos drew the most views)
        rc = _group_texts(ctx.df, g)[ctx.base]
        rc = rc[rc != ""].value_counts()
        if len(rc) >= 3 and int(rc.iloc[0]) >= 2 and int(rc.iloc[0]) > int(rc.iloc[1]):
            st.word("rows_top", rc.index[0])
            st.num("rows_top_n", int(rc.iloc[0]), "int")
            st.num("rows_n", int(rc.sum()), "int")
            st.num("rows_top_share", float(rc.iloc[0]) / float(rc.sum()), "share")
    st.numbers["explained"] = {"value": float(now.iloc[0]), "text": "", "kind": "score"}
    st.table = {
        "columns": [g, "share" + (" " + lc if lc else "")],
        "rows": [[k, _r(v, 4)] for k, v in now.head(12).items()],
    }
    return st


_OP_FN = {
    "change": op_change,
    "panel_change": op_panel_change,
    "breakdown": op_breakdown,
    "trend": op_trend,
    "yoy": op_yoy,
    "seasonality": op_seasonality,
    "volatility": op_volatility,
    "correlate": op_correlate,
    "mix": op_mix,
    "outliers": op_outliers,
    "rank_entities": op_rank_entities,
    "rank_labels": op_rank_labels,
    "concentration": op_concentration,
    "crosstab": op_crosstab,
    "answers": op_answers,
    "periods": op_periods,
    "confound": op_confound,
    "frequency": op_frequency,
}
OPS = tuple(_OP_FN)

# What a planner is told each op does (the system prompt of docs/ai-analyst/analyst-endpoint.mjs carries these).
OP_DOCS = {
    "change": "the measure in the latest complete window against the one before; with by, each group's part of the change",
    "panel_change": "a panel of levels (prices, rates): each entity's own change between the two latest windows",
    "breakdown": "the measure by group: totals and shares for a flow, averages with eta squared for a level",
    "trend": "the straight-line change per period over the whole file with a 95% range, peak and compound growth",
    "yoy": "monthly data: the latest complete month against the same month a year earlier, and the latest three months",
    "seasonality": "monthly data: the share of the movement around the trend that the month of the year accounts for",
    "volatility": "how much the measure moves period to period recently against before",
    "correlate": "the pairs of measures that move together most (rank correlation with a 95% range)",
    "mix": "an average's change split into the change inside the groups and the rows moving between them",
    "outliers": "values (or, with dates, changes) far from the rest by a robust z beyond 3.5",
    "rank_entities": "a panel: who leads in the latest period, their share, and who moved most over the years",
    "rank_labels": "labelled rows (brands, products) ranked with small samples pulled toward the overall average",
    "concentration": "how much of a total the largest groups hold: top shares and the Herfindahl-Hirschman index",
    "crosstab": "a survey: the pairs of answer columns most strongly linked (Cramer's V) and the cell furthest above chance",
    "answers": "a survey: each question's most common answer and its share, the survey's central questions first",
    "periods": "columns that repeat a count by period (x_85_99, x_00_14): totals per period, who leads each, and the "
    "latest read as a rate against a size column when there is one",
    "frequency": "what the rows are mostly about: the most common value of a detailed name column and of a coarse "
    "group, with their shares (weighted by answers when the file counts them)",
    "confound": "whether the groups' averages of a measure just follow their averages of a second measure (price by "
    "cut following stone size): the rank correlation across groups and the measure per unit of the second",
}

# Every argument an op may take, by name: its JSON schema and the role a column it names must have in the shape.
# step_schema() reads each op's own signature, so an op with an argument missing here fails at import.
# What the file must have for an op to run at all (step_schema(shape) leaves an op out when it is missing):
# date = a time axis, entity = a panel, label = a column naming each row, answers = two or more group columns,
# periods = counts by period in the columns, rows = a file whose rows are events or items (ledger, table, series).
OP_NEEDS = {
    "change": "date",
    "panel_change": "entity",
    "trend": "date",
    "yoy": "date",
    "seasonality": "date",
    "volatility": "date",
    "mix": "date",
    "rank_entities": "entity",
    "rank_labels": "label",
    "crosstab": "answers",
    "answers": "answers",
    "periods": "periods",
    "frequency": "rows",
}


def _shape_has(shape: Dict[str, Any], need: str) -> bool:
    if need == "date":
        return bool(shape.get("date"))
    if need == "entity":
        return bool(shape.get("entity")) and bool(shape.get("date"))
    if need == "label":
        return bool(shape.get("label"))
    if need == "answers":
        return len(shape.get("dimensions") or []) >= 2
    if need == "periods":
        return bool(shape.get("periods"))
    if need == "rows":
        return shape.get("kind") in ("ledger", "cross_section", "time_series")
    return True


_ARG_SPEC: Dict[str, Dict[str, Any]] = {
    "measure": {
        "type": "string",
        "maxLength": 200,
        "x-role": "measure",
        "description": "a number column of the analysis (one of shape.measures)",
    },
    "measures": {
        "type": "array",
        "minItems": 2,
        "maxItems": MAX_MEASURES,
        "items": {"type": "string", "maxLength": 200, "x-role": "measure"},
        "description": "two or more number columns of the analysis",
    },
    "by": {
        "type": "string",
        "maxLength": 200,
        "x-role": "dimension",
        "description": "a group column (one of shape.dimensions, or shape.entity)",
    },
    "where": {
        "type": "object",
        "maxProperties": 2,
        "x-role": "filter",
        "propertyNames": {"type": "string", "maxLength": 200, "x-role": "dimension"},
        "additionalProperties": {"type": "string", "maxLength": 80},
        "description": "keep only the rows where each group column holds the given value",
    },
    "against": {
        "type": "string",
        "maxLength": 200,
        "x-role": "measure",
        "description": "the second number column the group averages are read against",
    },
    "years": {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
        "description": "how many periods back the movers are measured from",
    },
}
MAX_AI_STEPS = 4  # steps a planner may ask for in one round
MAX_AI_ROUNDS = 3  # planner rounds after the engine's own
MAX_TOTAL_STEPS = MAX_STEPS + MAX_AI_ROUNDS * MAX_AI_STEPS
AI_ROUND_BUDGET_S = 3.0  # natively; a planner's step never starts after this


def _op_args(op: str) -> List[Tuple[str, bool]]:
    """(argument, required) for each argument of the op, read from its signature (after ctx and st)."""
    import inspect

    out = []
    for p in list(inspect.signature(_OP_FN[op]).parameters.values())[2:]:
        if p.name not in _ARG_SPEC:
            raise RuntimeError(
                "op %s takes %s, which _ARG_SPEC does not describe" % (op, p.name)
            )
        out.append((p.name, p.default is inspect.Parameter.empty))
    return out


def step_schema(shape: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The JSON schema of one step a planner may ask for, {op, args, why}, generated from OPS and each op's own
    signature. With a shape (the analyst block's `shape`), every column argument is narrowed to the columns of that
    role (measures; dimensions and the entity), and an op that cannot name any is left out: the planner is offered
    only steps that can run on this file. The executor (`run_steps`) checks every step against the same rules.
    """
    import copy

    meas = dims = None
    if isinstance(shape, dict):
        meas = [
            m.get("column") for m in shape.get("measures") or [] if isinstance(m, dict)
        ]
        dims = list(shape.get("dimensions") or []) + (
            [shape["entity"]] if shape.get("entity") else []
        )

    def narrow(spec):
        s = copy.deepcopy(spec)
        role = s.pop("x-role", None)
        if role == "measure" and meas is not None:
            s["enum"] = list(meas)
        if role == "dimension" and dims is not None:
            s["enum"] = list(dims)
        for k in ("items", "propertyNames"):
            if k in s:
                s[k] = narrow(s[k])
        if role:
            s["x-role"] = role
        return s

    variants = []
    for op in OPS:
        props, req, ok = {}, [], True
        for name, required in _op_args(op):
            s = narrow(_ARG_SPEC[name])
            empty = (
                s.get("enum") == []
                or (s.get("items") or {}).get("enum") == []
                or (s.get("propertyNames") or {}).get("enum") == []
            )
            if empty and required:
                ok = False
            if empty:
                continue
            props[name] = s
            if required:
                req.append(name)
        if meas is not None and op == "correlate" and len(meas) < 2:
            ok = False
        if isinstance(shape, dict) and not _shape_has(shape, OP_NEEDS.get(op, "")):
            ok = False
        if not ok:
            continue
        variants.append(
            {
                "type": "object",
                "description": OP_DOCS[op],
                "x-needs": OP_NEEDS.get(op, ""),
                "properties": {
                    "op": {"const": op},
                    "args": {
                        "type": "object",
                        "properties": props,
                        "required": req,
                        "additionalProperties": False,
                    },
                    "why": {"type": "string", "maxLength": 200},
                },
                "required": ["op", "args"],
                "additionalProperties": False,
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "northledger:analyst-step:v%d" % ANALYST_VERSION,
        "title": "One analyst step a planner may ask for",
        "x-max-steps-per-round": MAX_AI_STEPS,
        "x-max-rounds": MAX_AI_ROUNDS,
        "oneOf": variants,
    }


def _check_step(raw: Any, ctx: Ctx) -> Tuple[Optional[str], Dict[str, Any], str, str]:
    """(op, args with every column resolved against the frame, why, refusal). A refusal is non-empty when anything in
    the step falls outside step_schema(): an unknown op, field or argument, a wrong type, a column the frame does not
    hold in that role. Nothing of a refused step runs."""
    if not isinstance(raw, dict):
        return None, {}, "", "a step must be an object {op, args, why}"
    op = raw.get("op")
    if not isinstance(op, str) or op not in _OP_FN:
        return None, {}, "", "not one of the analyst's steps (%s)" % ", ".join(OPS)
    extra = sorted(set(raw) - {"op", "args", "why"})
    if extra:
        return (
            op,
            {},
            "",
            "a step holds only op, args and why (not %s)"
            % ", ".join(str(x)[:40] for x in extra[:3]),
        )
    why = raw.get("why", "")
    if not isinstance(why, str):
        return op, {}, "", "why must be text"
    # the planner's reason is shown as its words, never as a figure: a reason with a digit is dropped, not the step
    why = "" if re.search(r"\d", why) else why[:200]
    args = raw.get("args", {})
    if not isinstance(args, dict):
        return op, {}, why, "args must be an object"
    spec = dict(_op_args(op))
    unknown = sorted(set(args) - set(spec))
    if unknown:
        return (
            op,
            {},
            why,
            "%s takes no argument %s"
            % (op, ", ".join(str(x)[:40] for x in unknown[:3])),
        )
    missing = [k for k, req in spec.items() if req and k not in args]
    if missing:
        return op, {}, why, "%s needs %s" % (op, ", ".join(missing))
    out: Dict[str, Any] = {}
    for k, v in args.items():
        s = _ARG_SPEC[k]
        if v is None and not spec[k]:
            continue
        if s["type"] == "string":
            if not isinstance(v, str) or len(v) > s["maxLength"]:
                return op, {}, why, "%s must be a column's name" % k
            role = "measure" if s.get("x-role") == "measure" else "dim"
            c = ctx.col(v, role)
            if c is None:
                return (
                    op,
                    {},
                    why,
                    (
                        "the measure is not a number column the analysis can read"
                        if role == "measure"
                        else "the group column is not one the analysis can read"
                    ),
                )
            out[k] = c
        elif s["type"] == "array":
            if not isinstance(v, list) or not (
                s["minItems"] <= len(v) <= s["maxItems"]
            ):
                return (
                    op,
                    {},
                    why,
                    "%s must list %d to %d columns" % (k, s["minItems"], s["maxItems"]),
                )
            cs = [ctx.col(x, "measure") if isinstance(x, str) else None for x in v]
            if None in cs:
                return (
                    op,
                    {},
                    why,
                    "every measure must be a number column the analysis can read",
                )
            out[k] = cs
        elif s["type"] == "object":
            if not isinstance(v, dict) or not (1 <= len(v) <= s["maxProperties"]):
                return (
                    op,
                    {},
                    why,
                    "%s must name one or two group columns and a value each" % k,
                )
            w = {}
            for kk, vv in v.items():
                kc = ctx.col(kk, "dim")
                if kc is None:
                    return (
                        op,
                        {},
                        why,
                        "a filter column is not one the analysis can read",
                    )
                if not isinstance(vv, str) or len(vv) > 80:
                    return op, {}, why, "a filter value must be short text"
                # the value must be one the column holds: a filter names a group, never a figure of its own
                if vv not in set(_group_texts(ctx.df, kc).unique()) - {""}:
                    return (
                        op,
                        {},
                        why,
                        "a filter value is not one of the column's values",
                    )
                w[kc] = vv
            out[k] = w
        elif s["type"] == "integer":
            if (
                not isinstance(v, int)
                or isinstance(v, bool)
                or not (s["minimum"] <= v <= s["maximum"])
            ):
                return (
                    op,
                    {},
                    why,
                    "%s must be a whole number from %d to %d"
                    % (k, s["minimum"], s["maximum"]),
                )
            out[k] = v
    return op, out, why, ""


# A plain sentence for a step a planner asked for: the first template whose slots the step's ledger holds.
_OP_SENTENCES: Dict[str, List[str]] = {
    "change": [
        "{X.scope}by {X.by}, {X.top} carried {X.top_share} of the change in {X.measure} ({X.top_delta}) from "
        "{X.prev_label} to {X.cur_label}.",
        "{X.scope}the {X.agg_word} of {X.measure} {X.direction} from {X.prev} in {X.prev_label} to {X.cur} in "
        "{X.cur_label} ({X.change}).",
        "By {X.by}, {X.top} carried {X.top_share} of the change in {X.measure} ({X.top_delta}) from {X.prev_label} "
        "to {X.cur_label}.",
        "By {X.by}, the largest move in the average {X.measure} was in {X.top} ({X.top_delta}); {X.same_way} of "
        "{X.groups} groups moved the same way as the whole.",
        "The {X.agg_word} of {X.measure} {X.direction} from {X.prev} in {X.prev_label} to {X.cur} in {X.cur_label} "
        "({X.change}).",
    ],
    "panel_change": [
        "From {X.prev_label} to {X.cur_label}, {X.up} of {X.n} {X.entity} entries rose in {X.measure} (median change "
        "{X.median}); the highest change was {X.best} ({X.best_change}) and the lowest {X.worst} ({X.worst_change}).",
    ],
    "breakdown": [
        "{X.scope}{X.by} accounts for {X.eta2} of the variation in {X.measure}: {X.top} averages {X.top_value} "
        "({X.top_rows} rows) against {X.bottom_value} for {X.bottom} ({X.bottom_rows} rows).",
        "{X.scope}{X.top} holds {X.top_share} of the total {X.measure} ({X.top_value}) across {X.groups} {X.by} groups.",
        "{X.by} accounts for {X.eta2} of the variation in {X.measure}: {X.top} averages {X.top_value} ({X.top_rows} "
        "rows) against {X.bottom_value} for {X.bottom} ({X.bottom_rows} rows).",
        "{X.top} holds {X.top_share} of the total {X.measure} ({X.top_value}) across {X.groups} {X.by} groups.",
    ],
    "trend": [
        "Over {X.periods} {X.unit}s ({X.first} to {X.last}) {X.measure} moved by {X.slope} a {X.unit} on a straight "
        "line (95% range {X.slope_lo} to {X.slope_hi}).",
    ],
    "yoy": [
        "{X.measure} {X.direction} from {X.prev} in {X.prev_label} to {X.cur} in {X.cur_label} ({X.change} on the "
        "same month a year earlier).",
    ],
    "seasonality": [
        "Over {X.months} months ({X.first} to {X.last}) the month of the year accounts for {X.strength} of the "
        "movement in {X.measure} around its trend; {X.peak} runs highest and {X.trough} lowest.",
    ],
    "volatility": [
        "Over its last {X.n_recent} {X.unit}-to-{X.unit} changes ({X.recent_from} to {X.last}) {X.measure} moved by a typical {X.sd_recent} "
        "a {X.unit}, {X.compare} the {X.sd_before} of the {X.n_before} changes before.",
    ],
    "correlate": [
        "{X.x} and {X.y} move {X.strength} {X.sign_word} (rank correlation {X.rho}, 95% range {X.lo} to {X.hi}, "
        "{X.n} rows); that is association, not cause.",
    ],
    "mix": [
        "Of the {X.total} change in the average {X.measure}, {X.within} came from changes inside the {X.by} groups and "
        "{X.mix} from rows moving between them.",
    ],
    "outliers": [
        "{X.far} of {X.n} {X.what} {X.measure} ({X.far_share}) sit far from the rest (robust z beyond 3.5); the largest "
        "is {X.max} against a median of {X.median}.",
        "{X.far} of {X.n} {X.what} {X.measure} sit far from the rest (robust z beyond 3.5).",
    ],
    "rank_entities": [
        "In {X.year}, {X.top1} leads on {X.measure} ({X.top1_value}), then {X.top2} ({X.top2_value}) and {X.top3} "
        "({X.top3_value}).",
    ],
    "rank_labels": [
        "Ranked by {X.measure}, {X.top1} leads ({X.top1_shrunk}; {X.top1_raw} on {X.top1_n} {X.unit}), then {X.top2} "
        "({X.top2_shrunk}) and {X.top3} ({X.top3_shrunk}).",
    ],
    "concentration": [
        "In {X.cur_label}, the largest {X.by}, {X.top1}, holds {X.top1_share} of the total {X.measure} and the top "
        "three {X.top3_share} across {X.groups} groups (Herfindahl-Hirschman index {X.hhi}, the same as {X.effective} equal-sized groups: {X.level_word}).",
        "The largest {X.by}, {X.top1}, holds {X.top1_share} of the total {X.measure} and the top three {X.top3_share} "
        "across {X.groups} groups (Herfindahl-Hirschman index {X.hhi}, the same as {X.effective} equal-sized groups: {X.level_word}).",
    ],
    "crosstab": [
        "The strongest link between answers is {X.a} with {X.b} (Cramer's V {X.v}, {X.n} respondents answered both; "
        "{X.p_word}): {X.detail}.",
    ],
}


def _sentence(st: Step, steps: Dict[str, Step]) -> Optional[Tuple[str, str]]:
    if not st.ok:
        return None
    for t in _OP_SENTENCES.get(st.op, []):
        tmpl = t.replace("{X.", "{%s." % st.id)
        text = fill(tmpl, steps)
        if text:
            return tmpl, text
    return None


def ledger_for_ai(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What a planner sees of the steps so far (docs/ai-analyst/PROTOCOL.md `steps_so_far`): each step's op, args,
    why, whether it ran (or why not) and its printed figures. Never a table, never a row, never a raw value.
    """
    out = []
    for s in (block or {}).get("steps") or []:
        out.append(
            {
                "id": s.get("id"),
                "round": s.get("round"),
                "op": s.get("op"),
                "args": s.get("args"),
                "why": s.get("why", ""),
                "ok": bool(s.get("ok")),
                "refused": s.get("refused", ""),
                "figures": {
                    k: v.get("text")
                    for k, v in (s.get("numbers") or {}).items()
                    if v.get("kind") != "score" and v.get("text")
                },
            }
        )
    return out


# ----------------------------------------------------------------------------- the story
# The story is the analysis told in the order an analyst tells it, one template per kind of data: a headline, up to
# three findings, what drives it, what to check next and what to keep in mind. Every line is a template filled from
# the step ledgers by fill() (a line whose slot no step computed is not made), and every line names the insights and
# steps it rests on. "actions" are checks and questions, never advice and never a figure the engine did not compute.
STORY_TEMPLATES = {
    "time_series": "official statistics: the year-on-year change, the long-run trend, volatility and the season",
    "panel": "a panel: who leads, who moved, and how concentrated the total is",
    "survey": "a survey: the strongest links between answers, and the base each rests on",
    "ledger": "a ledger: the change, who drove it, mix and concentration",
    "cross_section": "a cross-section: what explains the measure, and the rankings",
}
_DESCRIPTIVE = "Descriptive: computed from the rows the engine kept and not tested by the engine's gate."


def _key_numbers(ins: List[Dict[str, Any]], steps: Dict[str, "Step"], cap: int = 4) -> List[Dict[str, Any]]:
    """The few numbers a reader takes away, as tiles {value, label, note, insight, step}: each value is a step's
    printed ledger text and each label is built from the step's own words, so a tile states nothing its step did not
    compute. One tile per step, in the insights' order (the headline first); a step of a kind with no tile is skipped."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    split_shown = False  # one "of the change came from" tile: a drill inside the top group repeats it
    head_shown = False  # one "latest against before" tile: a scoped re-run of the change repeats it

    def val(st, k):
        v = (st.numbers.get(k) or {}).get("value")
        return float(v) if isinstance(v, (int, float)) and v == v else 0.0

    def t(st, k):
        v = st.numbers.get(k)
        return v["text"] if v and v.get("text") not in (None, "", "not computable") else None

    for i in ins:
        for sid in i["steps"]:
            st = steps.get(sid)
            if st is None or not st.ok or sid in seen:
                continue
            tile = None
            if st.op == "change" and not st.args.get("by") and t(st, "cur") and t(st, "measure") and not head_shown:
                head_shown = True
                tile = (t(st, "cur"), "%s, %s" % (_words(t(st, "measure")), t(st, "cur_label")),
                        "%s against %s" % (t(st, "change"), t(st, "prev_label")) if t(st, "change") else "")
            elif st.op == "change" and t(st, "top") and t(st, "top_share") and not split_shown:
                split_shown = True
                tile = (t(st, "top_share"), "of the change came from %s" % t(st, "top"),
                        "%s, by %s" % (t(st, "top_delta"), _words(t(st, "by") or "")) if t(st, "top_delta") else "")
            elif st.op == "trend" and t(st, "rec_hi") and t(st, "rec_lo"):
                tile = (t(st, "rec_hi"), "Record high, %s" % t(st, "rec_hi_at"),
                        "low %s in %s" % (t(st, "rec_lo"), t(st, "rec_lo_at")))
            elif st.op == "trend" and t(st, "peak") and abs(val(st, "from_peak")) > 1e-9:  # a peak that is the last
                # value says no more than the headline
                tile = (t(st, "peak"), "Peak, %s" % t(st, "peak_at"),
                        "the last value is %s from it" % t(st, "from_peak") if t(st, "from_peak") else "")
            elif st.op == "concentration" and t(st, "top1_share"):
                tile = (t(st, "top1_share"), "%s share of %s" % (_poss(t(st, "top1")), _words(t(st, "measure") or "the total").lower()),
                        ("%s %s groups" % (t(st, "groups"), _words(t(st, "by") or "").lower())) if t(st, "groups") else "")
            elif st.op == "breakdown" and t(st, "top_share"):
                tile = (t(st, "top_share"), "%s share of %s" % (_poss(t(st, "top")), _words(t(st, "measure")).lower()), "")
            elif st.op == "breakdown" and t(st, "top_value") and t(st, "top"):
                tile = (t(st, "top_value"), "Highest average %s: %s" % (_words(t(st, "measure")).lower(), t(st, "top")),
                        "lowest %s (%s)" % (t(st, "bottom"), t(st, "bottom_value")) if t(st, "bottom") else "")
            elif st.op == "frequency" and max(val(st, "b_share"), val(st, "a_share")) >= 0.1:  # a top value under a
                # tenth of the rows is not a number to take away
                k = "b" if val(st, "b_share") >= 0.1 else "a"
                tile = (t(st, k + "_share"), "of %s: %s" % (unit_word(t(st, "unit")), t(st, k + "_top")),
                        "the most common %s" % _words(t(st, k + "_col")).lower())
            elif st.op == "panel_change" and t(st, "up") and t(st, "n"):
                tile = ("%s of %s" % (t(st, "up"), t(st, "n")),
                        "%s entries rose in %s" % (t(st, "entity") or "the", _words(t(st, "measure") or "").lower()),
                        "median change %s" % t(st, "median") if t(st, "median") else "")
            elif st.op == "answers" and t(st, "q1_share") and t(st, "q1_top"):
                tile = (t(st, "q1_share"), "Answered %s to \u201c%s\u201d" % (t(st, "q1_top"), _clip(t(st, "q1"), 48)),
                        "of %s who answered" % t(st, "q1_n") if t(st, "q1_n") else "")
            elif st.op == "yoy" and t(st, "cur") and t(st, "measure"):
                tile = (t(st, "cur"), "%s, %s" % (_words(t(st, "measure")), t(st, "cur_label")),
                        "%s on %s" % (t(st, "change"), t(st, "prev_label")) if t(st, "change") else "")
            if tile and tile[0]:
                seen.add(sid)
                out.append({"value": tile[0], "label": _clip(tile[1], 80), "note": _clip(tile[2] or "", 80),
                            "insight": i["id"], "step": sid})
            if len(out) >= cap:
                return out
    return out


def _poss(x: Optional[str]) -> str:
    x = str(x or "")
    return x + ("'" if x.endswith("s") else "'s")


def unit_word(u: Optional[str]) -> str:
    return u or "rows"


def build_story(
    sh: Shape, ins: List[Dict[str, Any]], steps: Dict[str, Step]
) -> Optional[Dict[str, Any]]:
    """story {kind, template, headline, findings[<=3], drivers[], actions[], caveats[]}; each line {text, template,
    insights[], steps[]}. Built only from the insights and the step ledgers (fill); None when there is no insight.
    """
    if not ins:
        return None
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for i in ins:
        by_kind.setdefault(i["kind"], []).append(i)

    def first(*kinds):
        for k in kinds:
            if by_kind.get(k):
                return by_kind[k][0]
        return None

    def step_of(i, op=None):
        if i is None:
            return None
        for sid in reversed(i["steps"]):
            st = steps.get(sid)
            if st is not None and (op is None or st.op == op):
                return st
        return None

    def ref(i):
        return {
            "text": i["text"],
            "template": i["template"],
            "insights": [i["id"]],
            "steps": list(i["steps"]),
        }

    def line(tmpl, sts, from_ins=()):
        """A new line from letters ({H.cur}) bound to steps; None when a slot is missing or a step is absent."""
        if any(s is None for s in sts.values()):
            return None
        t = _ids(tmpl, **sts)
        text = fill(t, steps)
        if not text:
            return None
        return {
            "text": text,
            "template": t,
            "insights": [i["id"] for i in from_ins if i],
            "steps": sorted({s.id for s in sts.values()}, key=lambda x: int(x[1:])),
        }

    def has(st, *keys):
        return st is not None and all(k in st.numbers for k in keys)

    used: List[str] = []
    findings: List[Dict[str, Any]] = []
    drivers: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    caveats: List[Dict[str, Any]] = []

    def add(lst, item, cap=None):
        if item is None or (cap is not None and len(lst) >= cap):
            return
        if any(
            x["text"] == item["text"] for x in findings + drivers + actions + caveats
        ):
            return
        lst.append(item)
        used.extend(item["insights"])

    def add_ins(lst, i, cap=None):
        if i is not None and i["id"] not in used:
            add(lst, ref(i), cap)

    kind = sh.kind
    headline = None
    hd = first("headline")
    H = step_of(hd)
    rob = [i for i in by_kind.get("robustness", []) if step_of(i, "change") is not None]
    B = step_of(rob[0]) if rob else None
    out_i = next(
        (
            i
            for i in by_kind.get("robustness", [])
            if step_of(i, "outliers") is not None
        ),
        None,
    )
    O = step_of(out_i)

    if kind == "time_series":
        yy, tr, vo, se = (
            first("yoy"),
            first("trend"),
            first("volatility"),
            first("seasonality"),
        )
        # the headline is the latest twelve months (or, for a monthly level, the month a year earlier); a single
        # month's year-on-year change is the first finding: one month of a seasonal, noisy flow is a weak headline
        headline = ref(hd) if hd else ref(yy) if yy else ref(tr) if tr else None
        if headline:
            used.extend(headline["insights"])
        v_, s_ = step_of(vo), step_of(se)
        loud = v_ is not None and v_.numbers["compare"]["text"] != "about as large as"
        seasonal = s_ is not None and (s_.numbers["strength"]["value"] or 0) >= 0.2
        for i in [yy, tr] + ([vo, se] if loud or not seasonal else [se, vo]) + [out_i]:
            add_ins(findings, i, 3)
        for i in by_kind.get("driver", []) + by_kind.get("drill", []):
            add_ins(drivers, i)
        Y, V, T, S = step_of(yy), step_of(vo), step_of(tr), step_of(se)
        lab = Y if has(Y, "cur_label") else H
        if has(lab, "cur_label"):
            add(
                actions,
                line(
                    "Check the release notes before reading the latest change: are the latest figures "
                    "({L.cur_label}) final, or provisional and open to revision?",
                    {"L": lab},
                    [yy or hd],
                ),
            )
        if has(V, "compare") and V.numbers["compare"]["text"] == "larger than":
            add(
                actions,
                line(
                    "Recent moves are larger than before ({V.sd_recent} against {V.sd_before} a {V.unit}): "
                    "what happened in {V.big_at}, the largest of them ({V.big})?",
                    {"V": V},
                    [vo],
                ),
            )
        if has(O, "max", "max_where"):
            add(
                actions,
                line(
                    "Check the largest jump, {O.max} at {O.max_where}, against the publisher's notes: a real "
                    "move, or a break in the series (a change of method, source or instrument)?",
                    {"O": O},
                    [out_i],
                ),
            )
        if has(S, "strength") and (S.numbers["strength"]["value"] or 0) >= 0.2:
            add(
                actions,
                line(
                    "Compare like months: is the latest figure of {S.measure} usual for its month of the year, given "
                    "that {S.peak} runs highest?",
                    {"S": S},
                    [se],
                ),
            )
        if has(T, "clear") and not T.numbers["clear"]["value"]:
            add(
                caveats,
                line(
                    "No straight-line trend is clear over {T.periods} {T.unit}s, so the latest change alone "
                    "does not show a turn.",
                    {"T": T},
                    [tr],
                ),
            )
        m = sh.primary or ""
        if sh.mkind.get(m) == "rate" and has(H, "measure"):
            add(
                caveats,
                line(
                    "{H.measure} is a rate: its changes are in percentage points, not percent.",
                    {"H": H},
                    [hd],
                ),
            )
    elif kind == "panel":
        R = next(
            (
                s
                for s in (step_of(i, "rank_entities") for i in by_kind.get("drill", []))
                if s is not None
            ),
            None,
        )
        rk_i = first("drill")
        tr_i = first("trend")
        K = step_of(first("concentration"))
        headline = (
            ref(hd) if hd else (ref(rk_i) if rk_i else ref(tr_i) if tr_i else None)
        )
        if headline:
            used.extend(headline["insights"])
        if has(R, "top1", "top2", "top3") and R.numbers["comparable"]["text"]:
            tm = "Leaders: in {R.year}, {R.top1} ({R.top1_value}), {R.top2} ({R.top2_value}) and {R.top3} ({R.top3_value})"
            if has(R, "top5_share"):
                tm += "; the top five hold {R.top5_share} of the total across {R.entries} entries"
            add(findings, line(tm + ".", {"R": R}, [rk_i]))
        if has(R, "riser", "faller"):
            add(
                findings,
                line(
                    "Movers: since {R.then_year}, {R.riser} grew most ({R.riser_growth}) and {R.faller} least "
                    "({R.faller_growth}), {R.movers_scope}.",
                    {"R": R},
                    [rk_i],
                ),
            )
        elif has(H, "best", "worst") and H.op == "panel_change":
            add(
                findings,
                line(
                    "Movers: from {H.prev_label} to {H.cur_label}, {H.best} moved most ({H.best_change}) and "
                    "{H.worst} least ({H.worst_change}).",
                    {"H": H},
                    [hd],
                ),
            )
        if K is not None:
            add_ins(findings, first("concentration"), 3)
        for i in (first("trend"), out_i):
            add_ins(findings, i, 3)
        for i in by_kind.get("driver", []):
            add_ins(drivers, i)
        if has(R, "riser", "riser_growth", "then_year"):
            add(
                actions,
                line(
                    "Check whether {R.riser}'s {R.riser_growth} since {R.then_year} is real growth, or a change in "
                    "how it is measured (coverage, definitions, a split or a merger).",
                    {"R": R},
                    [rk_i],
                ),
            )
        if (
            has(K, "trend_word", "topk_change")
            and K.numbers["trend_word"]["text"] != "about as concentrated"
        ):
            add(
                actions,
                line(
                    "Which entries account for the top {K.top_k}'s {K.topk_change} change in share since "
                    "{K.then_label}?",
                    {"K": K},
                    [first("concentration")],
                ),
            )
        agg_st = next((s for s in (H, R) if has(s, "aggregates_n")), None)
        if agg_st is not None and agg_st.numbers["aggregates_n"]["value"] == 1:
            add(
                actions,
                line(
                    "Check that {A.aggregate_example}, set apart as an aggregate, is the only row that adds "
                    "up others.",
                    {"A": agg_st},
                    [hd],
                ),
            )
            add(
                caveats,
                line(
                    "The aggregate entry {A.aggregate_example} is left out of every total and ranking.",
                    {"A": agg_st},
                    [hd],
                ),
            )
        elif agg_st is not None:
            add(
                actions,
                line(
                    "Check that the {A.aggregates_n} entries set apart as aggregates (such as "
                    "{A.aggregate_example}) are the only rows that add up others.",
                    {"A": agg_st},
                    [hd],
                ),
            )
            add(
                caveats,
                line(
                    "{A.aggregates_n} aggregate entries such as {A.aggregate_example} are left out of every "
                    "total and ranking.",
                    {"A": agg_st},
                    [hd],
                ),
            )
        if (
            has(R, "measure") and _agg_for(sh, sh.primary or "") == "sum"
        ):  # a flow's total; a stock (population) is its own base
            add(
                caveats,
                line(
                    "The ranking compares totals of {R.measure}; a per-head or per-unit figure could order "
                    "the entries differently.",
                    {"R": R},
                    [rk_i],
                ),
            )
        if has(R, "measure") and not R.numbers["comparable"]["text"]:
            add(
                caveats,
                line(
                    "{R.measure} is a level that does not compare across entries, so entries are compared "
                    "on their own changes, not ranked on their levels.",
                    {"R": R},
                    [rk_i],
                ),
            )
    elif kind == "survey":
        rel = by_kind.get("relationship", [])
        C = next(
            (s for s in (step_of(i, "crosstab") for i in rel) if s is not None), None
        )
        headline = ref(rel[0]) if rel else (ref(hd) if hd else None)
        if headline:
            used.extend(headline["insights"])
        if has(C, "a2", "b2", "v2"):
            add(
                findings,
                line(
                    "Next strongest: {C.a2} with {C.b2} (Cramer's V {C.v2}).",
                    {"C": C},
                    rel[:1],
                ),
            )
        if has(C, "respondents", "n", "ra_n"):
            add(
                findings,
                line(
                    "Base sizes: {C.respondents} respondents in all; {C.n} answered both questions, and the "
                    '{C.share} figure rests on the {C.ra_n} who answered {C.ra} to "{C.a}".',
                    {"C": C},
                    rel[:1],
                ),
            )
        for i in by_kind.get("driver", []) + by_kind.get("headline", []):
            add_ins(findings, i, 3)
        if (
            has(C, "a", "b", "n", "respondents")
            and C.numbers["n"]["value"] < C.numbers["respondents"]["value"]
        ):
            add(
                actions,
                line(
                    '{C.n} of {C.respondents} respondents answered both: were "{C.a}" and "{C.b}" asked of '
                    "everyone, or only after an earlier answer?",
                    {"C": C},
                    rel[:1],
                ),
            )
        if has(C, "ra", "ra_n"):
            add(
                actions,
                line(
                    'Is a base of {C.ra_n} respondents who answered {C.ra} to "{C.a}" large enough to read that group '
                    "on its own?",
                    {"C": C},
                    rel[:1],
                ),
            )
        add(
            actions,
            line(
                "Do the respondents match the population the survey speaks for, or does it need "
                "weights?",
                {},
                (),
            ),
        )
        if has(C, "pairs"):
            add(
                caveats,
                line(
                    "{C.pairs} pairs of questions were compared; the strongest of many can look strong by "
                    "chance.",
                    {"C": C},
                    rel[:1],
                ),
            )
        add(caveats, line("A link between answers is association, not cause.", {}, ()))
    elif kind == "ledger":
        headline = ref(hd) if hd else ref(first("trend")) if first("trend") else None
        if headline:
            used.extend(headline["insights"])
        drv = first("driver")
        for i in (
            drv,
            first("mix"),
            first("concentration"),
            first("yoy"),
            first("seasonality"),
            first("trend"),
        ):
            add_ins(findings, i, 3)
        for i in by_kind.get("driver", []) + by_kind.get("drill", []):
            add_ins(drivers, i)
        S = step_of(drv, "change")
        if has(S, "top", "top_share") and has(H, "prev_label", "cur_label"):
            add(
                actions,
                line(
                    "Between {H.prev_label} and {H.cur_label}, what changed in {S.top_name}, which carried "
                    "{S.top_share} of the change?",
                    {"S": S, "H": H},
                    [drv, hd],
                ),
            )
        if has(S, "against", "against_delta"):
            add(
                actions,
                line(
                    "Why did {S.against} move the other way ({S.against_delta})?",
                    {"S": S},
                    [drv],
                ),
            )
        if has(B, "without", "holds") and not B.numbers["holds"]["text"]:
            add(
                actions,
                line(
                    "The headline rests on {B.without}: check those rows for one-offs, duplicates or "
                    "re-coding before reading it as a trend.",
                    {"B": B},
                    rob[:1],
                ),
            )
            add(
                caveats,
                line(
                    "Leaving out {B.without}, the change reverses ({B.change}).",
                    {"B": B},
                    rob[:1],
                ),
            )
        elif has(B, "without", "change"):
            add(
                caveats,
                line(
                    "The direction holds when {B.without} is left out ({B.change} on the rest).",
                    {"B": B},
                    rob[:1],
                ),
            )
        if has(O, "far", "measure"):
            add(
                actions,
                line(
                    "Check the {O.far} rows of {O.measure} far from the rest for entry errors or one-off "
                    "items.",
                    {"O": O},
                    [out_i],
                ),
            )
        X = step_of(first("mix"))
        if has(X, "mix_share") and (X.numbers["mix_share"]["value"] or 0) >= 0.3:
            add(
                caveats,
                line(
                    "{X.mix_share} of the movement in the average is mix: rows moving between {X.by} groups, "
                    "not changes inside them.",
                    {"X": X},
                    [first("mix")],
                ),
            )
    else:  # cross_section (and anything without a time axis)
        drv = first("driver")
        rel = first("relationship")
        S = step_of(drv, "breakdown")
        # a grouping that explains little is a finding, not the headline, when a measure moves with this one
        weak = has(S, "eta2") and (S.numbers["eta2"]["value"] or 0) < 0.05
        if weak and rel:
            headline = ref(rel)
        else:
            headline = (
                ref(drv) if drv else (ref(hd) if hd else ref(rel) if rel else None)
            )
        if headline:
            used.extend(headline["insights"])
        for i in (hd, drv, first("drill"), rel, out_i):
            add_ins(findings, i, 3)
        for i in by_kind.get("driver", []) + by_kind.get("drill", []):
            add_ins(drivers, i)
        if weak:
            S = None
        R = step_of(hd, "rank_labels")
        if has(R, "top1", "top1_n", "unit") and not R.numbers["plain"]["text"]:
            add(
                actions,
                line(
                    "{R.top1} leads on {R.top1_n} {R.unit}: does the lead hold as more {R.unit} come in, "
                    "and against the entries just below it?",
                    {"R": R},
                    [hd],
                ),
            )
        if has(S, "top_name", "bottom_name", "top_rows", "bottom_rows", "eta2"):
            add(
                actions,
                line(
                    "Does the gap between {S.top_name} ({S.top_rows} rows) and {S.bottom_name} ({S.bottom_rows} rows) "
                    "hold within the other groupings and in a fresh sample?",
                    {"S": S},
                    [drv],
                ),
            )
        C = step_of(rel, "correlate")
        if has(C, "x", "y"):
            add(
                actions,
                line(
                    "Is the link between {C.x} and {C.y} there within each group, or only across them?",
                    {"C": C},
                    [rel],
                ),
            )
        if has(O, "far", "measure"):
            add(
                actions,
                line(
                    "Check the {O.far} values of {O.measure} far from the rest for entry errors before "
                    "ranking on it.",
                    {"O": O},
                    [out_i],
                ),
            )
        R = step_of(hd, "rank_labels")
        if has(R, "m", "unit") and not R.numbers["plain"]["text"]:
            add(
                caveats,
                line(
                    "Rankings pull small samples toward the overall {R.overall} by the weight of {R.m} "
                    "{R.unit}, so an average on a few {R.unit} counts for less.",
                    {"R": R},
                    [hd],
                ),
            )
        if has(S, "eta2"):
            add(
                caveats,
                line(
                    "{S.by} accounts for {S.eta2} of the variation: how much the groups differ, not why.",
                    {"S": S},
                    [drv],
                ),
            )
        if rel:
            add(
                caveats,
                line(
                    "Measures that move together show association, not cause.", {}, ()
                ),
            )
    if headline is None:
        return None
    add(caveats, line(_DESCRIPTIVE, {}, ()))
    return {
        "kind": kind,
        "template": STORY_TEMPLATES.get(kind, STORY_TEMPLATES["cross_section"]),
        "headline": headline,
        "findings": findings[:3],
        "drivers": drivers[:3],
        "actions": actions[:4],
        "caveats": caveats[:4],
        "key_numbers": _key_numbers(ins, steps),
    }


def _ids(tmpl: str, **steps: "Step") -> str:
    """A template written with letters ({H.cur}) turned into one naming the steps' ids ({A1.cur})."""
    for letter, st in steps.items():
        tmpl = tmpl.replace("{%s." % letter, "{%s." % st.id)
    return tmpl


def explore(
    df,
    kinds: Dict[str, str],
    hint: Optional[Dict[str, Any]] = None,
    budget_s: float = TIME_BUDGET_S,
) -> Dict[str, Any]:
    """Five rounds at most, each chosen from what the last one found, then the story (build_story). Returns the
    `analyst` block."""
    t0 = time.perf_counter()
    sh = shape_of(df, kinds, hint)
    ctx = Ctx(df, sh)
    ctx.topic = list((hint or {}).get("topic") or [])
    ins: List[Dict[str, Any]] = []
    out = {
        "version": ANALYST_VERSION,
        "chosen_by": "engine",
        "shape": sh.to_dict(),
        "steps": [],
        "insights": ins,
        "story": None,
        "refused": ctx.refused,
        "rounds": 0,
        "cut_short": None,
        "note": "Descriptive: computed from the rows the engine kept, not tested by the engine's gate.",
    }
    m = sh.primary
    if not len(df) or (m is None and sh.kind != "survey"):
        ctx.refused.append(
            {
                "op": "all",
                "why": (
                    "no measure to analyse" if len(df) else "the engine kept no rows"
                ),
            }
        )
        return out

    drawn: set = set()

    def say(kind, tmpl, sids):
        text = fill(tmpl, ctx.steps)
        if text:
            it = {
                "id": "I%d" % (len(ins) + 1),
                "kind": kind,
                "template": tmpl,
                "text": text,
                "steps": sids,
            }
            # the chart that shows it: the first of its steps with one not drawn already (one chart, one finding)
            for sid in sids:
                st = ctx.steps.get(sid)
                if st is None or sid in drawn or len(ins) >= 49:
                    continue
                fig = _figure(st, 50 + len(ins), text)  # 50 to 98: never the id of a chart rep.viz chose (1 to 49)
                if fig is not None:
                    drawn.add(sid)
                    it["figure"] = fig
                    break
            ins.append(it)

    def over():
        """True once the step cap is reached (deterministic) or, as a safety net only, the time budget runs out;
        the time cut is recorded so a report shortened by a slow device says so instead of differing silently."""
        if len(ctx.order) >= MAX_STEPS:
            return True
        if time.perf_counter() - t0 > budget_s:
            if not out.get("cut_short"):
                out["cut_short"] = "time"
                ctx.refused.append(
                    {"op": "rest", "why": "the time budget ran out; the later rounds were not run on this device"}
                )
            return True
        return False

    def agg_note(st: Step) -> str:
        """The aggregate rows set apart, as slots of the step that left them out."""
        if not sh.aggregates:
            return ""
        st.num("aggregates_n", len(sh.aggregates), "int")
        st.word("aggregate_example", sh.aggregates[0])
        if len(sh.aggregates) == 1:
            return (
                " (the aggregate entry {%s.aggregate_example} is left out of every total)"
                % st.id
            )
        return (
            " ({%s.aggregates_n} aggregate entries such as {%s.aggregate_example} are left out of every total)"
            % (st.id, st.id)
        )

    monthly = sh.date is not None and sh.grain in ("month", "week", "day")
    # ---- round 1: the headline
    rnd = 1
    head = None
    if sh.date is not None and sh.kind == "panel" and _agg_for(sh, m) == "mean":
        pc = op_panel_change(
            ctx,
            ctx.new(
                rnd,
                "panel_change",
                {"measure": m},
                "a panel of levels: each entry's own change, never an average across entries",
            ),
            m,
        )
        if pc.ok:
            say(
                "headline",
                _ids(
                    "From {H.prev_label} to {H.cur_label}, {H.up} of {H.n} {H.entity} entries rose in "
                    "{H.measure} (median change {H.median}); the highest change was {H.best} "
                    "({H.best_change}) and the lowest {H.worst} ({H.worst_change}).",
                    H=pc,
                ),
                [pc.id],
            )
    elif sh.date is not None:
        head = op_change(
            ctx,
            ctx.new(
                rnd,
                "change",
                {"measure": m},
                "the headline: the latest complete period against the one before",
            ),
            m,
        )
        if head.ok:
            say(
                "headline",
                _ids(
                    "The {H.agg_word} of {H.measure} {H.direction} from {H.prev} in {H.prev_label} to "
                    "{H.cur} in {H.cur_label} ({H.change})",
                    H=head,
                )
                + agg_note(head)
                # the total row named as what it is (16 Oct 2026: "the file's own grand-total row" left the reader to guess which row)
                + (_ids("; {H.world_name} (the file's own total row, which also counts what no entry holds) {H.world_direction} "
                        "{H.world_change} ({H.world_prev} to {H.world_cur})", H=head)
                   if "world_cur" in head.numbers else "")
                + ".",
                [head.id],
            )
        # parallel series that stop at different months (two sources of one record): each over its own latest 12 months
        pdim = _parallel_dim(ctx, m) if head.ok and not over() else None
        if pdim is not None:
            ow = op_own_windows(
                ctx,
                ctx.new(rnd, "own_windows", {"measure": m, "by": pdim},
                        "parallel series that end at different months: each over its own latest 12 months"),
                m,
                pdim,
            )
            if ow.ok:
                parts = ["{O.s%d} to {O.s%d_end} {O.verb} {O.s%d_cur} against {O.s%d_prev} the 12 months before ({O.s%d_delta})"
                         % (i, i, i, i, i) for i in range(1, int(ow.numbers["series"]["value"]) + 1)]
                say("headline", _ids("Each {O.by} over its own latest 12 months: " + "; ".join(parts) + ".", O=ow), [ow.id])
        # a monthly flow compared 12 months to 12: the latest month against the same month a year earlier too
        if (
            monthly
            and head.ok
            and _agg_for(sh, m) != "mean"
            and head.numbers["cur_label"]["text"].startswith("the 12 months")
            and not over()
        ):
            yy = op_yoy(
                ctx,
                ctx.new(
                    rnd,
                    "yoy",
                    {"measure": m},
                    "the latest month against the same month a year earlier",
                ),
                m,
            )
            if yy.ok:
                tm = (
                    "In {Y.cur_label}, {Y.measure} {Y.direction} to {Y.cur} from {Y.prev} in {Y.prev_label} "
                    "({Y.change} year on year)"
                )
                if "q_change" in yy.numbers:
                    tm += "; the three months from {Y.q_from} moved {Y.q_word} ({Y.q_change} on a year earlier)"
                say("yoy", _ids(tm + ".", Y=yy), [yy.id])
        tr = op_trend(
            ctx,
            ctx.new(rnd, "trend", {"measure": m}, "the long run behind the headline"),
            m,
        )
        if tr.ok:
            if tr.numbers["clear"]["value"]:
                tmpl = (
                    "Over {T.periods} {T.unit}s ({T.first} to {T.last}) {T.measure} moved by {T.slope} a {T.unit} "
                    "on a straight line (95% range {T.slope_lo} to {T.slope_hi})"
                )
            else:
                tmpl = (
                    "Over {T.periods} {T.unit}s ({T.first} to {T.last}) {T.measure} shows no clear straight-line "
                    "trend (95% range {T.slope_lo} to {T.slope_hi} a {T.unit})"
                )
            if tr.numbers["unit"]["text"] != "year":
                tmpl += "."  # a month's peak in a few years of monthly data is the season, not a story
            elif tr.numbers["peak_at"]["text"] == tr.numbers["last"]["text"]:
                tmpl += "; its last value, {T.peak} in {T.peak_at}, is the highest."
            else:
                tmpl += "; it peaked at {T.peak} in {T.peak_at} and the last value is {T.from_peak} from that peak."
            if "cagr" in tr.numbers:
                tmpl = (
                    tmpl[:-1]
                    + ", a compound {T.cagr} a year from {T.first_value} to {T.last_value}."
                )
            if tr.numbers["extremes"]["value"]:
                tmpl += " Its monthly record high was {T.rec_hi} in {T.rec_hi_at} and its low {T.rec_lo} in {T.rec_lo_at}."
            say("trend", _ids(tmpl, T=tr), [tr.id])
    else:
        if sh.periods:
            pe = op_periods(
                ctx,
                ctx.new(
                    rnd,
                    "periods",
                    {},
                    "the same counts by period in the columns: what changed, and who leads each",
                ),
            )
            if pe.ok:
                tm = "Across {P.n} {P.unit} from {P.first} to {P.last}, {P.s1} {P.s1_dir} from {P.s1_a} to {P.s1_b} ({P.s1_change})"
                for k in ("s2", "s3"):
                    if k + "_change" in pe.numbers:
                        tm += "; {P.%s} {P.%s_dir} from {P.%s_a} to {P.%s_b} ({P.%s_change})" % ((k,) * 5)
                say("headline", _ids(tm + ".", P=pe), [pe.id])
                parts = []
                for k in ("s1", "s2", "s3"):
                    for side, per in (("b", "last"), ("a", "first")):
                        if "%s_lead_%s" % (k, side) in pe.numbers:
                            parts.append(
                                "{P.%s_lead_%s} had the most {P.%s} in {P.%s} ({P.%s_lead_%s_value})"
                                % (k, side, k, per, k, side)
                            )
                if parts:
                    say("drill", _ids("; ".join(parts[:4]) + ".", P=pe), [pe.id])
                if "rate_top" in pe.numbers:
                    say(
                        "drill",
                        _ids(
                            "Read against size ({P.unit} differ {P.spread}-fold in {P.exposure}), {P.s1} in {P.last} "
                            "run {P.overall_rate} {P.per} overall; the highest rate is {P.rate_top_s}, {P.rate_top_value} "
                            "({P.rate_top_count} {P.s1}; {P.rate_top_shrunk} once its size is weighed).",
                            P=pe,
                        ),
                        [pe.id],
                    )
        if (
            not sh.periods
            and sh.label is not None
            and m is not None
            and not (sh.mkind.get(m) == "level" and _has(m, _UNRANKED_LEVEL_WORDS))
        ):
            rl = op_rank_labels(
                ctx,
                ctx.new(
                    rnd,
                    "rank_labels",
                    {"measure": m},
                    "each %s ranked, small samples pulled toward the average"
                    % sh.label,
                ),
                m,
            )
            if rl.ok and rl.numbers["plain"]["text"]:
                say(
                    "headline",
                    _ids(
                        "Ranked by {R.measure} (one row each, overall average {R.overall}), {R.top1} "
                        "leads with {R.top1_raw}, then {R.top2} ({R.top2_raw}) and {R.top3} "
                        "({R.top3_raw}).",
                        R=rl,
                    ),
                    [rl.id],
                )
            elif rl.ok:
                tm = (
                    "Ranked by {R.measure} with small samples pulled toward the overall {R.overall} (the weight of "
                    "{R.m} {R.unit} at that average), {R.top1} leads ({R.top1_shrunk}; {R.top1_raw} on {R.top1_n} "
                    "{R.unit}), then {R.top2} ({R.top2_shrunk}) and {R.top3} ({R.top3_shrunk})"
                )
                if "most" in rl.numbers:
                    tm += "; the most-answered, {R.most} ({R.most_n} {R.unit}), averages {R.most_raw}"
                if "raw_top" in rl.numbers:
                    tm += (
                        "; the highest raw average, {R.raw_top} ({R.raw_top_value} on {R.raw_top_n} {R.raw_top_unit}), "
                        "drops out"
                    )
                say("headline", _ids(tm + ".", R=rl), [rl.id])
        if sh.kind == "survey":
            ct = op_crosstab(
                ctx,
                ctx.new(rnd, "crosstab", {}, "a survey: which answers go together?"),
            )
            if ct.ok:
                say(
                    "relationship",
                    _ids(
                        "The strongest link between answers is between \"{C.a}\" and \"{C.b}\" (Cramer's V {C.v}, "
                        "{C.n} respondents answered both; {C.p_word}): {C.detail}.",
                        C=ct,
                    ),
                    [ct.id],
                )
                if "a2" in ct.numbers:
                    say(
                        "relationship",
                        _ids("Next: \"{C.a2}\" with \"{C.b2}\" (Cramer's V {C.v2}).", C=ct),
                        [ct.id],
                    )
            an = op_answers(
                ctx,
                ctx.new(rnd, "answers", {}, "a survey: what did most people answer?"),
            )
            if an.ok:
                tm = 'Most common answers: to "{A.q1}", {A.q1_top} ({A.q1_share} of {A.q1_n} who answered)'
                if "q2" in an.numbers:
                    tm += '; to "{A.q2}", {A.q2_top} ({A.q2_share})'
                if "q3" in an.numbers:
                    tm += '; to "{A.q3}", {A.q3_top} ({A.q3_share})'
                say("headline", _ids(tm + ".", A=an), [an.id])
                if "agree_q" in an.numbers:
                    say(
                        "distribution",
                        _ids(
                            'Widest agreement: {A.agree_share} answered {A.agree_top} to "{A.agree_q}". Most '
                            'divided: "{A.split_q}", where {A.split_top} ({A.split_share}) leads '
                            "{A.split_second} ({A.split_second_share}).",
                            A=an,
                        ),
                        [an.id],
                    )
            if m is None:
                out["rounds"] = 1
                out["steps"] = [ctx.steps[x].to_dict() for x in ctx.order]
                out["story"] = build_story(sh, ins, ctx.steps)
                out["seconds"] = round(time.perf_counter() - t0, 3)
                return out

    # ---- round 2: which dimension explains it
    rnd = 2
    best: Optional[Tuple[float, str, Step]] = None
    dims = list(sh.dims[:MAX_DIMS])
    few = [d for d in dims if _group_texts(df, d).replace("", None).nunique() <= 20]
    dims = few or dims
    for d in dims:
        if over():
            break
        if sh.date is not None and head is not None and head.ok:
            st = op_change(
                ctx,
                ctx.new(
                    rnd,
                    "change",
                    {"measure": m, "by": d},
                    "does %s carry the change?" % d,
                ),
                m,
                by=d,
            )
        else:
            st = op_breakdown(
                ctx,
                ctx.new(
                    rnd,
                    "breakdown",
                    {"measure": m, "by": d},
                    "does %s explain %s?" % (d, m),
                ),
                m,
                d,
            )
        sc = (st.numbers.get("explained") or {}).get("value")
        if st.ok and sc is not None and (best is None or sc > best[0]):
            best = (sc, d, st)
    if (
        best is not None
        and best[2].op == "breakdown"
        and "eta2" in best[2].numbers
        and (best[2].numbers["eta2"]["value"] or 0) < 0.05
    ):
        st = best[2]
        say(
            "driver",
            _ids(
                "No single grouping accounts for much of {S.measure}: the strongest, {S.by}, accounts for "
                "{S.eta2} of its variation.",
                S=st,
            ),
            [st.id],
        )
        best = None
    if best is not None:
        _sc, d, st = best
        st.word("by_word", d)
        if "top" in st.numbers:
            t1 = st.numbers["top"]["text"]
            st.word(
                "top_name",
                "the rows " + t1 if t1.startswith(("with ", "without ")) else t1,
            )
        if "bottom" in st.numbers:
            b1 = st.numbers["bottom"]["text"]
            st.word(
                "bottom_name",
                "the rows " + b1 if b1.startswith(("with ", "without ")) else b1,
            )
        if st.op == "change" and "top_share" in st.numbers:
            tm = "By {S.by_word}, {S.top} carried {S.top_share} of the change ({S.top_delta})"
            if (
                st.numbers["groups"]["value"] or 0
            ) > 3:  # with three groups or fewer, the top three are all
                tm += "; the top three of {S.groups} groups carried {S.top3_share}"
            if "against" in st.numbers:
                tm += ", while {S.against} moved the other way ({S.against_delta})"
            tm += "."
            if "top_first" in st.numbers:
                tm += (
                    " {S.top} first appears in {S.top_first}, so much of that is a group added, not growth of what "
                    "was already there."
                )
            say("driver", _ids(tm, S=st), [st.id])
        elif st.op == "change" and "top_delta" in st.numbers:
            say(
                "driver",
                _ids(
                    "By {S.by_word}, the largest move was in {S.top} ({S.top_delta}); {S.same_way} of "
                    "{S.groups} groups moved the same way as the whole.",
                    S=st,
                ),
                [st.id],
            )
        elif st.op == "breakdown" and "eta2" in st.numbers:
            say(
                "driver",
                _ids(
                    "{S.by} accounts for {S.eta2} of the variation in {S.measure}: {S.top} averages "
                    "{S.top_value} ({S.top_rows} rows) against {S.bottom_value} for {S.bottom} "
                    "({S.bottom_rows} rows); the gap's 95% range is {S.gap_lo} to {S.gap_hi}.",
                    S=st,
                ),
                [st.id],
            )
        elif st.op == "breakdown":
            say(
                "driver",
                _ids(
                    "{S.top} holds {S.top_share} of the total {S.measure} ({S.top_value}) across {S.groups} "
                    "{S.by} groups.",
                    S=st,
                ),
                [st.id],
            )
    # ---- round 3: the drill
    rnd = 3
    if not over() and sh.kind == "panel":
        rk = op_rank_entities(
            ctx,
            ctx.new(
                rnd, "rank_entities", {"measure": m}, "a panel: who leads and who moved"
            ),
            m,
        )
        if rk.ok:
            parts = []
            if rk.numbers["comparable"]["text"]:
                tm = (
                    "In {R.year}, {R.top1} leads on {R.measure} ({R.top1_value}), then {R.top2} ({R.top2_value}) and "
                    "{R.top3} ({R.top3_value})"
                )
                if "top5_share" in rk.numbers:
                    tm += "; the top five hold {R.top5_share} of the total across {R.entries} entries"
                parts.append(tm)
            if "whole_top1" in rk.numbers:
                parts.append(
                    "Over the whole record ({R.whole_first} to {R.whole_last}), {R.whole_top1} accounts for the most "
                    "{R.measure} ({R.whole_top1_value}, {R.whole_top1_share} of the total)"
                )
            if "riser" in rk.numbers:
                parts.append(
                    "Since {R.then_year}, {R.riser} grew most ({R.riser_growth}) and {R.faller} least "
                    "({R.faller_growth}), {R.movers_scope}"
                )
            if "riser_all" in rk.numbers:
                parts.append("Of all {R.movers_all} entries with a figure in both years, {R.riser_all} grew fastest ({R.riser_all_growth})")
            if parts:
                say("drill", _ids(". ".join(parts), R=rk) + agg_note(rk) + ".", [rk.id])
        if not over() and _agg_for(sh, m) in ("sum", "stock"):
            cc = op_concentration(
                ctx,
                ctx.new(
                    rnd,
                    "concentration",
                    {"measure": m},
                    "a panel: how much of the total the largest entries hold",
                ),
                m,
            )
            if cc.ok:
                tm = (
                    "In {K.cur_label}, the largest three {K.by} entries hold {K.top3_share} of the total {K.measure} "
                    "(Herfindahl-Hirschman index {K.hhi}, the same as {K.effective} equal-sized groups: {K.level_word})"
                )
                if "topk_then" in cc.numbers:
                    tm += (
                        "; the top {K.top_k} held {K.topk_then} in {K.then_label} and {K.topk_now} now "
                        "({K.topk_change}, {K.trend_word})"
                    )
                say("concentration", _ids(tm + ".", K=cc), [cc.id])
    elif not over() and best is not None and len(dims) >= 2:
        _sc, d, st = best
        top = st.numbers.get("top", {}).get("text")
        nxt = [x for x in dims if x != d]
        sub_best = None
        for d2 in nxt[:3]:
            if over():
                break
            if st.op == "change":
                s2 = op_change(
                    ctx,
                    ctx.new(
                        rnd,
                        "change",
                        {"measure": m, "by": d2, "where": {d: top}},
                        "inside %s, which %s carried it?" % (top, d2),
                    ),
                    m,
                    by=d2,
                    where={d: top},
                )
            else:
                s2 = op_breakdown(
                    ctx,
                    ctx.new(
                        rnd,
                        "breakdown",
                        {"measure": m, "by": d2, "where": {d: top}},
                        "inside %s, does %s matter?" % (top, d2),
                    ),
                    m,
                    d2,
                    where={d: top},
                )
            sc2 = (s2.numbers.get("explained") or {}).get("value")
            if sc2 is not None:
                # a column mostly blank inside the group (a vendor on one bill in ten) explains the change by its
                # blanks: what it says is weighed by the share of the change it actually names
                blank = (s2.numbers.get("blank_share") or {}).get("value") or 0.0
                sc2 = sc2 * (1.0 - min(1.0, max(0.0, blank)))
            if s2.ok and sc2 is not None and (sub_best is None or sc2 > sub_best[0]):
                sub_best = (sc2, d2, s2)
        if sub_best is not None:
            _x, d2, s2 = sub_best
            s2.word("parent", top)
            s2.word(
                "parent_phrase",
                (
                    top
                    if top.startswith(("with ", "without "))
                    else "where %s is %s" % (d, top)
                ),
            )
            s2.word("by_word", d2)
            if "top" in s2.numbers:
                t2 = s2.numbers["top"]["text"]
                s2.word(
                    "top_phrase",
                    (
                        t2
                        if t2.startswith(("with ", "without "))
                        else "%s (%s)" % (t2, d2)
                    ),
                )
            if s2.op == "change" and "top_share" in s2.numbers:
                if (s2.numbers.get("blank_share", {}).get("value") or 0) >= 0.5:
                    tm = (
                        "Among rows {S.parent_phrase}, {S.blank_share} of the change is in rows with no {S.by_word}; "
                        "of the rest, {S.top} carried the most ({S.top_delta})."
                    )
                elif (s2.numbers["top_share"]["value"] or 0) >= 0.25:
                    tm = "Among rows {S.parent_phrase}, {S.top_phrase} carried {S.top_share} of its change ({S.top_delta})."
                else:
                    # the largest of many small parts is not a driver: say the change is spread
                    tm = (
                        "Among rows {S.parent_phrase}, the change is spread across {S.groups} {S.by_word} groups: the "
                        "largest part, {S.top}'s, is {S.top_share} ({S.top_delta})."
                    )
                say("drill", _ids(tm, S=s2), [st.id, s2.id])
            elif s2.op == "breakdown" and "eta2" in s2.numbers:
                say(
                    "drill",
                    _ids(
                        "Among rows {S.parent_phrase}, {S.by_word} accounts for {S.eta2} of the variation: "
                        "{S.top} averages {S.top_value} against {S.bottom_value} for {S.bottom}.",
                        S=s2,
                    ),
                    [st.id, s2.id],
                )
            elif s2.op == "breakdown":
                say(
                    "drill",
                    _ids(
                        "Among rows {S.parent_phrase}, {S.top} holds {S.top_share} of the total.",
                        S=s2,
                    ),
                    [st.id, s2.id],
                )
    # ---- round 4: mix, concentration, season, relationships
    rnd = 4
    if (
        not over()
        and best is not None
        and _agg_for(sh, m) == "mean"
        and sh.kind == "ledger"  # parallel series are not segments rows move between
        and head is not None
        and head.ok
    ):
        mx = op_mix(
            ctx,
            ctx.new(
                rnd,
                "mix",
                {"measure": m, "by": best[1]},
                "rate or mix: inside the groups, or between them?",
            ),
            m,
            best[1],
        )
        if mx.ok:
            say(
                "mix",
                _ids(
                    "Of the {X.total} change in the average {X.measure}, {X.within} came from changes inside "
                    "the {X.by} groups and {X.mix} from rows moving between them ({X.mix_share} of the "
                    "movement is mix).",
                    X=mx,
                ),
                [mx.id],
            )
    cdim = None
    lv_all = None
    if sh.kind == "ledger" and sh.dims:
        cdim = best[1] if best is not None else sh.dims[0]
        lv_all = {d: _group_texts(df, d).replace("", None).nunique() for d in sh.dims}
        if lv_all.get(cdim, 0) < 3:
            # a two-way split (with or without animals) has no concentration to speak of: the most detailed group a
            # reader would name instead (3 to 20 levels)
            fit = [d for d in sh.dims if 3 <= lv_all[d] <= 20]
            cdim = max(fit, key=lambda d: (lv_all[d], -sh.dims.index(d))) if fit else None
    elif sh.kind == "cross_section":
        # a table with no time axis: the total's split across the group a reader would name (3 to 20 levels, the
        # most detailed of those)
        lv = {d: _group_texts(df, d).replace("", None).nunique() for d in sh.dims}
        fit = [d for d in sh.dims if 3 <= lv[d] <= 20]
        cdim = max(fit, key=lambda d: (lv[d], -sh.dims.index(d))) if fit else None
        if cdim is None:
            # a group column too detailed to be a driver (120 industries) still splits a total: rows repeat in
            # it (two or more a group on average), unlike a label
            for c in df.columns:
                if kinds.get(c) != "text" or c in (sh.label, sh.entity) or c in sh.dims:
                    continue
                t = _text(df[c])
                k = t[t != ""].nunique()
                if 3 <= k <= 200 and k <= 0.5 * max(1, (t != "").sum()):
                    cdim = c
                    break
    if (
        not over()
        and cdim is not None
        and _agg_for(sh, m) in ("sum", "stock")
    ):
        cc = op_concentration(
            ctx,
            ctx.new(
                rnd,
                "concentration",
                {"measure": m, "by": cdim},
                "how much of the total do the largest %s groups hold?" % cdim,
            ),
            m,
            cdim,
        )
        if cc.ok:
            if (cc.numbers["groups"]["value"] or 0) > 3:
                tm = (
                    "In {K.cur_label}, {K.top1} holds {K.top1_share} of the total {K.measure}, then {K.top2} ({K.top2_share}), "
                    "and the top three of {K.groups} {K.by} groups {K.top3_share} (Herfindahl-Hirschman index {K.hhi}, the same as "
                    "{K.effective} equal-sized groups: {K.level_word})"
                )
            else:
                tm = (
                    "In {K.cur_label}, {K.top1} holds {K.top1_share} of the total {K.measure} across {K.groups} "
                    "{K.by} groups (Herfindahl-Hirschman index {K.hhi}, the same as {K.effective} equal-sized "
                    "groups: {K.level_word})"
                )
            if "topk_then" in cc.numbers:
                tm += "; the top {K.top_k} held {K.topk_then} in {K.then_label} ({K.topk_change}, {K.trend_word})"
            if "rows_top" in cc.numbers:
                tm += ". Across all {K.rows_n} rows, {K.rows_top} appears most often ({K.rows_top_n}, {K.rows_top_share})"
            if not cc.numbers["cur_label"]["text"]:
                tm = tm.replace("In {K.cur_label}, {K.top1}", "{K.top1}", 1)
            say("concentration", _ids(tm + ".", K=cc), [cc.id])
    if not over() and sh.kind in ("ledger", "cross_section", "time_series"):
        fq = op_frequency(
            ctx, ctx.new(rnd, "frequency", {}, "what are the rows mostly about?")
        )
        if fq.ok:
            parts = []
            if "a_top" in fq.numbers:
                parts.append("{Q.a_top} is the most common {Q.a_col} ({Q.a_top_n} of {Q.a_n} {Q.unit}, {Q.a_share})")
            if "b_top" in fq.numbers:
                tb = "{Q.b_top} the most common {Q.b_col} ({Q.b_share})"
                if "b_second" in fq.numbers:
                    tb += ", then {Q.b_second} ({Q.b_second_share})"
                parts.append(tb)
            say("distribution", _ids("; ".join(parts) + ".", Q=fq), [fq.id])
    if not over() and sh.kind in ("ledger", "cross_section"):
        oc = op_outcome_rate(ctx, ctx.new(rnd, "outcome_rate", {}, "rows are attempts: how often each kind succeeded"))
        if oc.ok:
            say("relationship", _ids(
                "Success rate (a {O.outcome} that begins \"{O.success_word}\"): {O.a_rate} of {O.a_n} rows {O.a_name} against {O.b_rate} of "
                "{O.b_n} {O.b_name}, a gap of {O.gap}; that is association, not cause.", O=oc), [oc.id])
    if not over() and monthly and sh.kind in ("time_series", "ledger"):
        se = op_seasonality(
            ctx,
            ctx.new(
                rnd,
                "seasonality",
                {"measure": m},
                "is part of the movement the time of year?",
            ),
            m,
        )
        if se.ok:
            if (se.numbers["strength"]["value"] or 0) >= 0.2:
                tm = (
                    "Over {S.months} months ({S.first} to {S.last}) the month of the year accounts for {S.strength} of "
                    "the movement in {S.measure} around its trend ({S.strength_word}): {S.peak} runs highest and "
                    "{S.trough} lowest, {S.peak} {S.gap} above {S.trough} once the trend is taken out."
                )
            else:
                tm = (
                    "Over {S.months} months ({S.first} to {S.last}) the month of the year accounts for {S.strength} "
                    "of the movement in {S.measure} around its trend once chance is allowed for: no season to speak of."
                )
            say("seasonality", _ids(tm, S=se), [se.id])
    if not over() and len(sh.measures) >= 2:
        cr = op_correlate(
            ctx,
            ctx.new(
                rnd,
                "correlate",
                {"measures": sh.measures[:MAX_MEASURES]},
                "which measures move together?",
            ),
            sh.measures,
        )
        if cr.ok and abs(cr.numbers["rho"]["value"] or 0) >= 0.3:
            say(
                "relationship",
                _ids(
                    "{C.x} and {C.y} move {C.strength} {C.sign_word} (rank correlation {C.rho}, 95% "
                    "range {C.lo} to {C.hi}, {C.n} rows); that is association, not cause.",
                    C=cr,
                ),
                [cr.id],
            )
            # a table with no time axis whose measure moves strongly with another: do the groups' averages differ
            # only because their rows differ in that other measure (Ideal diamonds are cheapest, being smallest)?
            other = cr.numbers["y"]["text"] if cr.numbers["x"]["text"] == m else None
            if (
                sh.date is None
                and other is not None
                and (cr.numbers["rho"]["value"] or 0) >= 0.7
                and not over()
            ):
                cands = [d for d in sh.dims if 4 <= _group_texts(df, d).replace("", None).nunique() <= 20]
                best_cf = None
                for d in cands[:3]:
                    if over():
                        break
                    cf = op_confound(
                        ctx,
                        ctx.new(
                            rnd,
                            "confound",
                            {"measure": m, "by": d, "against": other},
                            "do the %s groups differ in %s only through %s?" % (d, m, other),
                        ),
                        m,
                        d,
                        other,
                    )
                    if cf.ok and (best_cf is None or (cf.numbers["rho"]["value"] or 0) > (best_cf.numbers["rho"]["value"] or 0)):
                        best_cf = cf
                if best_cf is not None and (best_cf.numbers["rho"]["value"] or 0) >= 0.7:
                    tm = (
                        "By {F.by}, the average {F.measure} follows the average {F.against} (rank correlation across "
                        "the {F.groups} groups {F.rho}): {F.low} averages the lowest {F.measure} ({F.low_value}) and has "
                        "smaller {F.against} ({F.low_against} against {F.high_against} for {F.high})"
                    )
                    if "per_top" in best_cf.numbers:
                        tm += "; per unit of {F.against}, {F.low} runs {F.per_low_value} and {F.per_top} the most ({F.per_top_value})"
                    say("drill", _ids(tm + ".", F=best_cf), [cr.id, best_cf.id])
    # ---- round 5: robustness
    rnd = 5
    if not over() and sh.kind == "time_series":
        vo = op_volatility(
            ctx,
            ctx.new(
                rnd,
                "volatility",
                {"measure": m},
                "are the recent moves larger or smaller than usual?",
            ),
            m,
        )
        if vo.ok:
            say(
                "volatility",
                _ids(
                    "Over its last {V.n_recent} {V.unit}-to-{V.unit} changes ({V.recent_from} to {V.last}) {V.measure} "
                    "moved by a typical {V.sd_recent} a {V.unit} (the standard deviation of its {V.how}"
                    "), {V.compare} the {V.sd_before} of the {V.n_before} changes before; the "
                    "largest recent move was {V.big} in {V.big_at}.",
                    V=vo,
                ),
                [vo.id],
            )
    if not over():
        o = op_outliers(
            ctx,
            ctx.new(
                rnd,
                "outliers",
                {"measure": m},
                "rows far from the rest could drive the headline",
            ),
            m,
        )
        if o.ok and o.numbers["far"]["value"]:
            tm = (
                "{O.far} of {O.n} {O.what} {O.measure} ({O.far_share}) sit far from the rest (robust z beyond 3.5); "
                "the furthest out is the {O.side}, {O.max}"
            )
            if "max_where" in o.numbers:
                tm += " ({O.max_where})"
            say(
                "robustness", _ids(tm + " against a median of {O.median}.", O=o), [o.id]
            )
    if (
        not over()
        and best is not None
        and best[2].op == "change"
        and "top" in best[2].numbers
        and head is not None
        and head.ok
    ):
        d, top = best[1], best[2].numbers["top"]["text"]
        gt = _group_texts(df, d)
        others = [v for v in gt.unique() if v and v != top]
        if others:
            keep_all = ctx.base
            ctx.base = keep_all & (gt != top)
            rb = op_change(
                ctx,
                ctx.new(
                    rnd,
                    "change",
                    {"measure": m, "where_not": {d: top}},
                    "does the headline hold without %s?" % top,
                ),
                m,
            )
            ctx.base = keep_all
            if rb.ok:
                rb.word(
                    "without",
                    "the rows " + top if top.startswith(("with ", "without ")) else top,
                )
                same = (rb.numbers["delta"]["value"] or 0) * (
                    head.numbers["delta"]["value"] or 0
                ) > 0
                rb.word("holds", "yes" if same else "")
                say(
                    "robustness",
                    _ids(
                        (
                            "Leaving out {B.without}, {B.measure} still {B.direction} ({B.change})."
                            if same
                            else "Leaving out {B.without}, {B.measure} {B.direction} instead ({B.change}): the "
                            "headline rests on it."
                        ),
                        B=rb,
                    ),
                    [rb.id],
                )
    out["rounds"] = max([ctx.steps[s].round for s in ctx.order] or [0])
    out["steps"] = [ctx.steps[s].to_dict() for s in ctx.order]
    out["story"] = build_story(sh, ins, ctx.steps)
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


def run_steps(
    df,
    kinds: Dict[str, str],
    steps: Any,
    hint: Optional[Dict[str, Any]] = None,
    prior: Optional[List[Dict[str, Any]]] = None,
    budget_s: float = AI_ROUND_BUDGET_S,
) -> Dict[str, Any]:
    """One planner round (docs/ai-analyst/PROTOCOL.md): steps an AI planner asked for, [{op, args, why}], each checked
    against step_schema() and run with the same code as the engine's own. A step outside the schema (an op not in
    OPS, a field or argument the op does not take, a wrong type, a column the frame does not hold in that role: a
    withheld or coded one, or no such column) is refused with its reason and nothing of it runs; so is any step past
    MAX_AI_STEPS in the round, past MAX_TOTAL_STEPS in the block, or after the round's time budget. Each step that
    runs gets a plain sentence filled from its own ledger. Returns {shape, steps, insights, refused, round, seconds};
    step ids and the round continue after `prior` (the block's steps so far)."""
    t0 = time.perf_counter()
    sh = shape_of(df, kinds, hint)
    ctx = Ctx(df, sh)
    for p in prior or []:
        ctx.order.append(str(p.get("id")))
    rnd = 1 + max([int(p.get("round") or 0) for p in prior or []] or [0])
    done: List[Step] = []
    ins: List[Dict[str, Any]] = []
    raw_steps = steps if isinstance(steps, list) else []
    if not isinstance(steps, list):
        ctx.refused.append(
            {"op": "all", "why": "steps must be a list of {op, args, why}"}
        )
    for n, raw in enumerate(raw_steps):
        op, args, why, bad = _check_step(raw, ctx)
        if not bad and n >= MAX_AI_STEPS:
            bad = "more than %d steps in one round" % MAX_AI_STEPS
        if not bad and len(ctx.order) >= MAX_TOTAL_STEPS:
            bad = (
                "the analysis already holds %d steps, the most it runs"
                % MAX_TOTAL_STEPS
            )
        if not bad and time.perf_counter() - t0 > budget_s:
            bad = "the round ran out of time"
        if op is None:
            ctx.refused.append(
                {
                    "op": str(raw.get("op") if isinstance(raw, dict) else raw)[:40],
                    "why": bad,
                }
            )
            continue
        st = ctx.new(rnd, op, args if not bad else {}, why)
        if bad:
            st.refuse(bad)
            done.append(st)
            continue
        try:
            _OP_FN[op](ctx, st, **args)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a step that cannot run is refused, never a traceback
            st.refuse("the step could not run (%s)" % type(exc).__name__)
        if not st.ok:
            st.numbers, st.table = {}, {"columns": [], "rows": []}
        done.append(st)
        s = _sentence(st, ctx.steps)
        if s:
            it = {
                "id": "P%d.%d" % (rnd, len(ins) + 1),
                "kind": "planned",
                "template": s[0],
                "text": s[1],
                "steps": [st.id],
            }
            # its chart, numbered 70 and up by round (4 a round): never an id the engine's own insights (50 on) or
            # rep.viz (1 to 49) hold, while the engine's rounds stay under 20 insights
            n = 70 + 4 * max(0, rnd - 6) + len(ins)
            fig = _figure(st, n, s[1]) if n <= 98 else None
            if fig is not None:
                it["figure"] = fig
            ins.append(it)
    return {
        "shape": sh.to_dict(),
        "steps": [s.to_dict() for s in done],
        "insights": ins,
        "refused": ctx.refused,
        "round": rnd,
        "seconds": round(time.perf_counter() - t0, 3),
    }
