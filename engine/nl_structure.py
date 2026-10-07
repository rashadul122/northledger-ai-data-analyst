"""
nl_structure: the semantic layer of a statistical table (WAVE 4, track A1; plan/WAVE4-A-DESIGN.md sections 1, 2, 4).

    import nl_structure as NS
    S = NS.detect(reading, hidden, budget_s=1.0)      # the engine's own reading, never a withheld column
    if S["usable"]:
        body, info = NS.slice_bytes(S, S["default"])   # date + one measure column, in base units

An official table (a StatCan cube: 465 series x 79 months, one row each) holds totals beside their parts, a
seasonally adjusted copy beside the unadjusted one, a component beside its parent and published flags beside the
values. Adding its rows up counts the same dollar three times. This module reads what the table IS, by behaviour,
from the values the engine read (names are only hints):

  * cube_roles: the date, the measure (the number that moves over time), the metadata (constant, empty, a series
    id, an alias of one dimension, a flag that predicts a blank value), and the dimensions (the text columns left).
    The date and the dimensions must tell the rows apart (at most 1% repeats), else the table is incomplete (a
    dimension withheld) and no business analysis runs on it. A series id or an alias never stands in for a
    dimension: a withheld NAICS column is never rebuilt from VECTOR.
  * relations: for each dimension, which member is the total of which others, checked on the values (a sum-check
    with a rounding tolerance), flat (Canada = the 13 provinces), by codes (NAICS [44-45] > [441] > [4411]), or by
    a subset-sum search when there are no codes; alternatives ("excluding ...") and components (cannabis inside
    miscellaneous) are never parts. A rate or an index is never summed or averaged across members: the published
    aggregate is read.
  * adjustment_pair: a seasonally adjusted member beside an unadjusted one, by behaviour (equal calendar-year sums,
    three times less seasonal), whatever they are called.
  * measure_type: flow, stock, rate, index or count, from the unit; the scale (thousands) applied, so every figure is
    in base units.
  * default_slice, breakdowns, check_rows (a plan's rows that mix a total with its parts), the profile block for the
    planner, the estimand and the structure-backed scenario items.

Deterministic, numpy and pandas only, bounded by a time budget (past it a dimension is left unresolved and read by
rule 6: one member in an official table, added across in a business export). No engine file is changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

VERSION = "2026-10-01.1"
HERE = os.path.dirname(os.path.abspath(__file__))
FLAG_VOCAB_FILE = "flag_vocab.json"

# wave 5e (P3): NO wall-clock decision. Every search below is bounded by a COUNT (parents tried, subsets tested, families verified,
# leftover members searched), so the answer is a function of the file and the same on a slow and a fast machine. The one clock left
# is a guard against a table that cannot be read at all in a reasonable time: when it trips, `detect` stops and the whole table is
# unresolved (kind "not_cube", reason "took too long"; a table that looks like a series table is then refused); it never changes a figure.
WALL_GUARD_S = 40.0
BUDGET_S = WALL_GUARD_S         # kept under its old name for callers that pass budget_s
PAIRS_MAX = 400_000             # (left, right) pairs a subset-sum search examines (a count; 2 x 2^11 subset sums are searched in about 2,000)
MATCHES_MAX = 200               # fingerprint matches one subset-sum search verifies at most
VERIFY_MAX = 40                 # families one subset-sum search verifies on every cell at most
FP_GROUPS_MAX = 4               # availability patterns a subset-sum fingerprint is tried on (parts that report in different periods)
LEFT_MAX = 60                   # leftover members searched for an alternative total or a component (a count)
COPY_PAIR_MEMBERS = 40          # members a copy-by-shape test compares pairwise (the 40 largest)
MAX_SERIES = 20000
MAX_CELLS = 2_000_000           # series x dates held in memory (16 MB of floats): a larger table is not read as a cube
MAX_TENSOR = 4_000_000          # a dimension's member x context x date block for its sum-checks
MAX_DIMS = 8
MAX_MEMBERS = 400               # the dimension whose RELATIONS are searched has at most 400 members; a larger one is a series key (wave 5e, P11)
MAX_KEY_MEMBERS = 50000         # a column of more members than this is not a dimension (an id column)
DUP_MAX = 0.01                  # date x dimensions repeat on at most 1% of the rows
PASS_SHARE = 0.95               # a sum-check passes on 95% of its complete cells ...
MIN_COMPLETE = 6                # ... with at least 6 complete cells ...
MIN_MONTHS = 3                  # ... across at least 3 months
STRONG_INFO = 100.0             # wave 5d: ... or, when the total is at least 100 rounding tolerances (a match is no coincidence), at least 3 cells
POWER_K = 10.0                  # wave 5e, P1: a cell supports a total only when the larger of the total and its parts' sum is at least 10 rounding
                                # tolerances: then a 10% error in the total is more than one tolerance and the check could have failed
EXACT_MIN = 16                  # ... or the residual is EXACTLY nothing on 16 cells where something is counted (and the total varies): small counts
EXACT_REL = 1e-9                # exact: within float noise of the larger of the total and the parts' sum
FAIL_MIN = 3                    # a check is contradicted (not just unresolved) when 3 complete cells are off by more than the tolerance and under 95% hold
BOUND_SHARE = 0.99              # a member bounds another in 99% of the cells
CANDIDATES_MAX = 22             # the subset-sum search: at most 22 candidates (2^11 subsets a half)
PARENTS_MAX = 30
GEO_MIN_PARTS = 3               # wave 5e, P1: a geographic dimension with no total row is added only with at least 3 parts (two are a pair)
FLAG_MAX_CODES = 20
FLAG_CODE_LEN = 4
FLAG_BLANK_SHARE = 0.90         # a code whose rows have a blank measure this often stands for a missing value
ADJ_YEAR_TOL = 0.03             # (kept for the record: wave 5d's copy test; wave 5e reads a copy by SHAPE, see COPY_*) calendar-year sums within 3%
COPY_CORR = 0.95                # wave 5e, P5: two members are one quantity twice when the changes of their smoothed (12-period moving average) logs
                                # correlate at least 0.95 ...
COPY_RATIO_SD = 0.15            # ... and the log of their smoothed ratio wanders at most 0.15 (the ratio is stable; a basis drifts, a copy does not jump)
COPY_MIN_POINTS = 12            # smoothed points a copy-by-shape test needs (a table of 24 months, 16 quarters; 8 years of an annual table: COPY_MIN_ANNUAL)
COPY_MIN_ANNUAL = 8
ADJ_SEASONAL_RATIO = 3.0        # an adjusted pair is a copy where one is three times more seasonal than the other
ADJ_MAX_MEMBERS = 4
ADJ_MIN_PERIODS = {1: 24, 3: 16}      # wave 5d: periods an adjusted pair is looked for in, by the table's step (months: 2 years, quarters: 4)
PROFILE_CAP = 6000              # the profile's structure block, bytes
PROFILE_VALUE_MAX = 60          # a member string as the profile lists it (nl_browser._profile_facts cuts at 60)
SLICES_MAX = 12
BREAKDOWNS_MAX = 4              # a plan names at most 4 breakdowns
SLICE_ID = re.compile(r"^S\d{1,2}$")
BREAKDOWN_ID = re.compile(r"^B\d{1,2}$")
RECONCILE_TOL = 1e-6
FLOAT_NOISE = 1e-12             # wave 5c: a residual this small beside the figure it is part of is float arithmetic, not data

ROLES = ("partition", "hierarchy", "adjustment", "measure", "components", "flat_additive", "single", "rate_aggregate",
         "constant", "parts")
PARTS_TOKEN = "(sum of the parts)"           # a slice's member of a dimension that has NO total row: its parts, added up
_GEO_WORDS = re.compile(r"(?i)\b(?:geo|geography|geographies|region|regions|province|provinces|state|states|country|"
                        r"countries|area|areas|territor(?:y|ies)|district|districts|nation|county|counties|"
                        r"municipalit(?:y|ies)|city|cities|zone|zones|nuts\d?|g[\u00e9e]ographie|g[\u00e9e]o|r[\u00e9e]gions?|"
                        r"territoires?|pays|provinces?|[\u00e9e]tats?|villes?|gebiete?|bundesl\u00e4nder|bundesland|l\u00e4nder|land|"
                        r"kanton|pa[i\u00ed]s|pa[i\u00ed]ses|regi[\u00f3o]n(?:es)?|provincias?|estados?|ciudad(?:es)?)\b")
KINDS = ("cube", "cube_incomplete", "panel_no_relations", "not_cube")

# names (normalised: lower case, letters and digits only) that are hints, never proof
_META = frozenset((
    "dguid", "uom", "uomid", "scalarfactor", "scalarid", "vector", "coordinate", "status", "symbol",
    "terminated", "decimals", "obsstatus", "obsflag", "obsconf", "unitmult", "unitmultiplier",
    "confstatus", "unitmeasure", "unit", "units", "flag", "flags", "footnote", "footnotes",
    "timeformat", "freq", "frequency", "lastupdate", "dataflow", "structure", "structureid", "action",
    # wave 5e (P9): the same columns of a French, Spanish or German table; the ids and coordinates are also found by behaviour
    "vecteur", "coordonnee", "statut", "symbole", "termine", "decimales", "unitedemesure", "iddelunitedemesure",
    "facteurscalaire", "iddufacteurscalaire", "indicateur", "unidaddemedida", "factorescalar", "estado", "decimales",
    "einheit", "masseinheit", "maeinheit", "faktor", "skalierung", "dezimalstellen", "statusflag"))
_UNIT_META = ("uom", "unit", "units", "unitmeasure", "unitedemesure", "unite", "unidaddemedida", "einheit", "masseinheit", "maeinheit")
_SCALE_WORDS = ("scalarfactor", "unitmult", "unitmultiplier", "multiplier", "facteurscalaire", "factorescalar", "skalierung", "faktor")
_SCALE_IDS = ("scalarid", "iddufacteurscalaire")
_DECIMALS = ("decimals", "decimales", "dezimalstellen")
_FLAG_NAMES = ("status", "flag", "flags", "obsstatus", "obsflag", "confstatus", "obsconf", "symbol", "statut", "symbole", "estado")
_DATE_NAMES = ("refdate", "timeperiod", "date", "period", "time", "referenceperiod", "periodedereference", "periodo", "zeitraum", "fecha",
               "datum")
_VALUE_NAMES = ("value", "obsvalue", "valeur", "valor", "wert")
_TOTAL_HINT = re.compile(r"(?i)(?:^|\b)(?:total|all|overall|grand|aggregate|combined|ensemble|tous|toutes|insgesamt|gesamt|alle|todos|todas)"
                         r"(?:\b|$)|^\s*(?:_T|TOTAL|_Z)\s*$")
# wave 5d: "excl." / "excl" / "w/o" / "net of" / "not including" / "minus" say it too (a member named "Total excl. Seasonal shops" was read as a
# total beside "Total, all industries": the abbreviation's full stop meant `ex\.` could never match before a letter)
_ALT_STRONG = (r"excluding|excludes?|excluded|excl(?:uding)?\.?|except(?:ing)?|ex\.|not including|net of|minus|sauf|excluant|hors|"
               r"[\u00e0a] l'exclusion|excepto|ohne|au(?:ss|\u00df)er|abz\u00fcglich")
_ALT_WEAK = r"less|without|w/o|other than|sans|moins|sin|menos|autres que|andere als"
_ALT_HINT = re.compile(r"(?i)(?<![A-Za-z])(?:%s|%s)(?![A-Za-z])|(?<![A-Za-z])ex-(?=[A-Za-z])" % (_ALT_STRONG, _ALT_WEAK))
_ALT_STRONG_RE = re.compile(r"(?i)(?<![A-Za-z])(?:%s)(?![A-Za-z])|(?<![A-Za-z])ex-(?=[A-Za-z])" % _ALT_STRONG)
# the rest of something ("All other provinces", "Rest of Canada", "Autres provinces"): never a whole, in any branch
_REST_NAME = re.compile(r"(?i)\b(?:other|others|rest of|remaining|remainder|autres?|reste|sonstige[nr]?|\u00fcbrige[nr]?|restliche[nr]?|otros|otras|resto)\b")
_STOCK_WORDS = re.compile(r"(?i)\b(?:inventor(?:y|ies)|outstanding|balances?|holdings?|assets?|debts?|stocks?)\b")
_POP_WORDS = re.compile(r"(?i)\b(?:employment|employed|population|labour force|labor force|persons employed)\b")
_CURRENCY = re.compile(r"(?i)\b(?:dollars?|euros?|pounds?|yen|yuan|francs?|krona|kronor|krone|rupees?|pesos?|reais|"
                       r"real|rand|won|currency|canadian dollars|us dollars)\b|[$€£¥]|(?<![A-Za-z])(?:CAD|USD|EUR|GBP|"
                       r"JPY|CNY|CHF|AUD|NZD|SEK|NOK|DKK|INR|MXN|BRL|ZAR|KRW)(?![A-Za-z])")        # MIO_EUR, MEUR, CP_MEUR
_RATE_WORDS = re.compile(r"(?i)\b(?:percent(?:age)?|rate|ratio|per\s+(?:cent|\d[\d,]*|capita|hour|person))\b|%")
_INDEX_WORDS = re.compile(r"(?i)\bindex\b|\b(?:19|20)\d\d\s*=\s*100\b")
_INDEX_BASE = re.compile(r"((?:19|20)\d\d(?:\s*[-/]\s*\d{2,4})?)\s*=\s*100")
# G3 (wave 5): a measure is summed over time only when it is POSITIVELY a flow (a currency, or a word in the labels that
# says the count accumulates over a period); a count with no such word is ambiguous (a stock added up is the harm) and is
# averaged: the percent change is the same either way, and a total is never misstated.
_FLOW_WORDS = re.compile(
    r"(?i)\b(?:sales?|receipts?|revenues?|turnover|income|permits?|births?|deaths?|visits?|visitors?|trips?|arrivals?|"
    r"departures?|nights?|starts?|completions?|shipments?|orders?|bookings?|transactions?|claims?|admissions?|discharges?|"
    r"exports?|imports?|production|output|spending|expenditures?|purchases?|payments?|deliver(?:y|ies|ed)|accidents?|"
    r"collisions?|crimes?|offen[cs]es?|bankruptcies|insolvenc(?:y|ies)|layoffs?|hires?|separations?|immigrants?|"
    r"emigrants?|passengers?|downloads?|tickets?|registrations?|openings|closures|launches|sold)\b")
_STOCK_LEVEL_WORDS = re.compile(
    r"(?i)\b(?:inventor(?:y|ies)|outstanding|balances?|holdings?|assets?|liabilit(?:y|ies)|debts?|stocks?|population|"
    r"populations|employment|employed|unemployed|labou?r force|residents?|households?|dwellings|vacanc(?:y|ies)|"
    r"subscribers?|headcount|members(?:hip)?|accounts|beds|backlog|unfilled)\b")
# a price or an average in a currency is a level, not an amount that accumulates (member labels of a measure dimension)
_LEVEL_PRICE = re.compile(r"(?i)\b(?:prices?|average|mean|median|per\s+(?:capita|unit|hour|person|household)|"
                          r"unit (?:cost|value)|wages? rate|rates?)\b")
# standard errors, margins of error and confidence intervals: the precision of another member, never a measure of its own
_PRECISION = re.compile(
    r"(?i)\b(?:standard errors?|std\.? ?err(?:or)?s?|sampling errors?|margins? of error|confidence (?:intervals?|limits?|bounds?)|"
    r"coefficients? of variation|relative standard errors?|(?:lower|upper) (?:confidence )?(?:bound|limit|ci)|"
    r"95% (?:ci|confidence)|moe|rse|cv)\b|\bse\b(?=\s*(?:of|\(|$))|\bCI\b")
_COUNT_UNITS = re.compile(r"(?i)\b(?:persons?|people|number|units?|count|households?|businesses|establishments|"
                          r"jobs|vehicles|dwellings|permits|births|deaths)\b")
_SCALE_FACTOR = {"units": 1.0, "unit": 1.0, "ones": 1.0, "tens": 10.0, "hundreds": 100.0, "thousand": 1e3,
                 "thousands": 1e3, "millions": 1e6, "million": 1e6, "billions": 1e9, "billion": 1e9,
                 "trillions": 1e12, "trillion": 1e12,
                 # wave 5e (P9): the words of French, Spanish and German tables (accents folded by _scale_word). "milliards" are
                 # billions in French (10^9), "milliarden" in German; "billion" is a million million in German and Spanish ("billones"):
                 # those are left out on purpose, and an unknown word in a scale column refuses the table
                 "unites": 1.0, "unite": 1.0, "unidades": 1.0, "unidad": 1.0, "einheiten": 1.0, "einheit": 1.0,
                 "dizaines": 10.0, "centaines": 100.0, "milliers": 1e3, "millier": 1e3, "mille": 1e3, "miles": 1e3, "tausend": 1e3,
                 "tausende": 1e3, "mil": 1e3, "millones": 1e6, "millon": 1e6, "mio": 1e6, "mill": 1e6,
                 "milliards": 1e9, "milliard": 1e9, "milliarden": 1e9, "milliarde": 1e9, "mrd": 1e9, "mil millones": 1e9}
_BRACKET_CODE = re.compile(r"\[([0-9A-Za-z][0-9A-Za-z.\-]*)\]\s*$")
_LEAD_CODE = re.compile(r"^\s*([0-9][0-9A-Za-z]*(?:\.[0-9A-Za-z]+)*)\s+\S")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_EMBED = re.compile(r"^\s*(?P<num>[-+]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)?\s*"
                    r"(?P<flag>\[[A-Za-z]{1,2}\]|[A-Za-z]{1,2}|:|\.{2,3}|\*)?\s*$")
_SENSITIVE_PREFIX = "S"


# wave 5, gap 4: the table's period. A window is 12 months of a monthly table, 4 quarters of a quarterly one, 1 year of an annual one
# (every window is a 12-month span of month keys: a quarter or a year is keyed by the month it starts in, so a 12-month span holds
# exactly 4 quarters or 1 year). Period labels the engine does not read as dates (2012-Q1, 2012Q1, Q1 2012, 2012) are read here.
PERIOD_KINDS = {1: ("month", "months", 12), 3: ("quarter", "quarters", 4), 6: ("half-year", "half-years", 2),
                12: ("year", "years", 1)}
_YEAR_NAMES = frozenset(("year", "yr", "fiscalyear", "fy", "refdate", "timeperiod", "period", "time", "date", "referenceperiod",
                         "reference", "calendaryear", "obstime", "timeperiodcode", "refperiod"))
_Q_A = re.compile(r"^(\d{4})\s*[-_/ ]?\s*[Qq]\s*([1-4])$")
_Q_B = re.compile(r"^[Qq]\s*([1-4])\s*[-_/ ]?\s*(\d{4})$")
_H_A = re.compile(r"^(\d{4})\s*[-_/ ]?\s*[HhSs]\s*([12])$")
_Y_ONLY = re.compile(r"^(\d{4})$")


def _parse_periods(t: Any, allow_year: bool) -> Tuple[Any, str, float]:
    """(each label as the first day of its period, the family "quarter" | "half-year" | "year", the share of the filled labels
    read) for a column of text: 2012-Q1, 2012Q1, Q1 2012, 2012-H2, 2012 (a bare year only when the header may be a date)."""
    import pandas as pd
    f = t[t != ""]
    best = (None, "", 0.0)
    fams = [("quarter", [(_Q_A, "yq"), (_Q_B, "qy")]), ("half-year", [(_H_A, "yh")])]
    if allow_year:
        fams.append(("year", [(_Y_ONLY, "y")]))
    for fam, rxs in fams:
        out = pd.Series(pd.NaT, index=t.index, dtype="datetime64[ns]")
        ok = pd.Series(False, index=t.index)
        for rx, kind in rxs:
            m = f.str.extract(rx)
            if m.empty:
                continue
            good = m.notna().all(axis=1)
            if not good.any():
                continue
            mm = m[good]
            if kind in ("yq", "yh"):
                y, k = mm[0].astype(int), mm[1].astype(int)
            elif kind == "qy":
                k, y = mm[0].astype(int), mm[1].astype(int)
            else:
                y, k = mm[0].astype(int), pd.Series(1, index=mm.index)
            if fam == "year":
                good2 = (y >= 1800) & (y <= 2200)
                month = pd.Series(1, index=y.index)
            elif fam == "quarter":
                good2 = (y >= 1800) & (y <= 2200)
                month = (k - 1) * 3 + 1
            else:
                good2 = (y >= 1800) & (y <= 2200)
                month = (k - 1) * 6 + 1
            idx = y.index[good2.to_numpy()]
            out.loc[idx] = pd.to_datetime(pd.DataFrame({"year": y[idx], "month": month[idx], "day": 1}))
            ok.loc[idx] = True
        share = float(ok[f.index].mean()) if len(f) else 0.0
        if share > best[2]:
            best = (out, fam, share)
    return best


def _period_labels(R: Any, cols: List[str], head: Dict[str, str]) -> Optional[Tuple[str, Any, str]]:
    """The date column when the engine read none: a column of period labels (a quarter, a half-year or a year), at least 95% of
    its filled cells read, 6 or more different periods. (column, dates, family) or None."""
    best = None
    for c in cols:
        if R.kind(c) == "date":
            continue
        name = _norm(head[c])
        t = R.texts[c].astype(str).str.strip()
        if int((t != "").sum()) < 6:
            continue
        out, fam, share = _parse_periods(t, allow_year=name in _YEAR_NAMES)
        if out is None or share < 0.95 or int(out.nunique()) < 6:
            continue
        if best is None or (name in _YEAR_NAMES and not best[3]):
            best = (c, out, fam, name in _YEAR_NAMES)
    return None if best is None else (best[0], best[1], best[2])


def _period_of(times: List[str], family: str = "") -> Dict[str, Any]:
    """The table's period from its dates: one value a period (not a daily or weekly table) and a steady gap of 3, 6 or 12
    months between periods (80% of the gaps) is quarterly, half-yearly or annual; anything else is read by month, as before."""
    import numpy as np
    months = sorted({t[:7] for t in times})
    step = 1
    if len(months) >= 4 and len(months) == len(times):
        idx = np.array([int(m[:4]) * 12 + int(m[5:7]) - 1 for m in months])
        d = np.diff(idx)
        vals, counts = np.unique(d, return_counts=True)
        top = int(vals[counts.argmax()])
        if top in (3, 6, 12) and int(counts.max()) >= 0.8 * len(d):
            step = top
    noun, nouns, window = PERIOD_KINDS[step][0], PERIOD_KINDS[step][1], PERIOD_KINDS[step][2]
    phase = (int(months[0][5:7]) - 1) % step if months else 0
    return {"kind": noun, "noun": noun, "nouns": nouns, "step": step, "phase": phase, "per_year": 12 // step,
            "window": window, "adjective": {1: "monthly", 3: "quarterly", 6: "half-yearly", 12: "annual"}[step]}


def _plabel(S: Dict[str, Any], key: str) -> str:
    """A period key (the month it starts in) as the table's period: Jul 2026, Q3 2026, H2 2026, 2026."""
    st = (S.get("period") or {}).get("step", 1)
    try:
        y, m = int(key[:4]), int(key[5:7])
    except ValueError:
        return str(key)
    if st == 3:
        return "Q%d %d" % ((m - 1) // 3 + 1, y)
    if st == 6:
        return "H%d %d" % ((m - 1) // 6 + 1, y)
    if st == 12:
        return "%d" % y
    return _mon(key)


class _TooLarge(Exception):
    """A dimension's sum-check block would not fit the memory budget: the dimension is left unresolved (rule 6)."""


class _WallGuard(Exception):
    """The wall-clock guard tripped: the table could not be read in WALL_GUARD_S seconds. The whole of `detect` stops and the table is
    unresolved; no figure is ever chosen by how far a search got (wave 5e, P3)."""


class _Timer:
    """The wall-clock guard. `check()` raises `_WallGuard` past the budget; nothing else in this module reads the clock."""

    def __init__(self, budget: float) -> None:
        self.t0 = time.perf_counter()
        self.budget = float(budget)

    def left(self) -> float:
        return self.budget - (time.perf_counter() - self.t0)

    def over(self) -> bool:
        return self.left() <= 0

    def check(self) -> None:
        if self.over():
            raise _WallGuard()


def _norm(c: Any) -> str:
    """A header or a name in lower-case letters and digits only, accents folded (GÉO is geo, UNITÉ DE MESURE unitedemesure): the same
    function everywhere a header is compared with a vocabulary (wave 5e, P9)."""
    t = unicodedata.normalize("NFKD", str(c))
    return re.sub(r"[^a-z0-9]", "", "".join(ch for ch in t if not unicodedata.combining(ch)).lower())


_VOCAB: Dict[str, Any] = {}


def flag_vocab() -> Dict[str, Any]:
    """engine/flag_vocab.json, read once (packed beside the adapter)."""
    if not _VOCAB:
        try:
            with open(os.path.join(HERE, FLAG_VOCAB_FILE), encoding="utf-8") as fh:
                _VOCAB.update(json.load(fh))
        except (OSError, ValueError):
            _VOCAB.update({"version": None, "publishers": {}})
    return _VOCAB


def _publisher(headers: Sequence[str]) -> Optional[str]:
    """A publisher whose signature columns the file carries (3 of them, or all of a shorter signature)."""
    have = {_norm(h) for h in headers}
    best, hits = None, 0
    for name, p in (flag_vocab().get("publishers") or {}).items():
        sig = [_norm(x) for x in p.get("signature") or []]
        if not sig:
            continue
        k = sum(1 for x in sig if x in have)
        if k >= min(3, len(sig)) and k > hits:
            best, hits = name, k
    return best


def _code_kind(code: str, publisher: Optional[str]) -> Optional[str]:
    pubs = flag_vocab().get("publishers") or {}
    order = ([publisher] if publisher else []) + [p for p in ("statcan", "eurostat", "ons", "generic") if p != publisher]
    for p in order:
        codes = (pubs.get(p) or {}).get("codes") or {}
        if code in codes:
            return str(codes[code])
    return None


# --------------------------------------------------------------------------------------------- small helpers
def _factorize(values: Any) -> Tuple[Any, List[str]]:
    import pandas as pd
    codes, uniq = pd.factorize(values, sort=False)
    return codes, [str(u) for u in uniq]


def _label_code(label: str) -> Optional[str]:
    m = _BRACKET_CODE.search(label)
    if m:
        return m.group(1)
    m = _LEAD_CODE.match(label)
    if m and not re.fullmatch(r"(?:19|20)\d\d", m.group(1)):
        return m.group(1)
    return None


def _contains(parent: str, child: str) -> bool:
    """A code that contains another: a prefix (441 > 4411, 459 > 459A, 01.1 > 01.1.1) or a range (44-45 > 441)."""
    if parent == child:
        return False
    m = _RANGE.match(parent)
    if m:
        a, b = m.group(1), m.group(2)
        if len(a) != len(b):
            return False
        head = child.replace(".", "")[:len(a)]
        return head.isdigit() and len(child.replace(".", "")) >= len(a) and a <= head <= b and not _RANGE.match(child)
    if "." in parent:
        return child.startswith(parent + ".")
    return child.startswith(parent) and len(child) > len(parent) and "-" not in child


def _spec(code: str) -> int:
    """How specific a code is (a range counts as its endpoints' length)."""
    m = _RANGE.match(code)
    return len(m.group(1)) if m else len(code.replace(".", "")) + code.count(".")


def _fmt_count(n: int) -> str:
    return format(int(n), ",")


def _join_names(xs: Sequence[str], limit: int = 6) -> str:
    """"Alpha, Bravo and Charlie"; past `limit` names, "Alpha, Bravo, ... and 3 more"."""
    xs = [str(x) for x in xs]
    if len(xs) > limit:
        return ", ".join(xs[:limit - 1]) + " and %d more" % (len(xs) - limit + 1)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


# --------------------------------------------------------------------------------------------- the measure's values
def _measure_values(R: Any, land: str) -> Tuple[Any, Any, bool]:
    """(values as floats, embedded flag codes or None, whether any flag was embedded). The engine's numbers where it
    read one; a cell it could not read is parsed for a publisher's embedded flag ("123.4 p", ":", "[x]")."""
    import numpy as np
    import pandas as pd
    nums = R.numbers(land).to_numpy(dtype=float, copy=True) if R.kind(land) == "number" else \
        np.full(R.n, np.nan)
    txt = R.texts[land].astype(str).str.strip()
    todo = np.isnan(nums) & (txt != "").to_numpy()
    flags = np.array([""] * R.n, dtype=object)
    embedded = False
    if todo.any():
        sub = txt[todo]
        m = sub.str.extract(_EMBED)
        num = m["num"]
        fl = m["flag"].fillna("")
        ok = num.notna()
        vals = pd.to_numeric(num.where(ok).str.replace(",", "", regex=False), errors="coerce")
        idx = np.flatnonzero(todo)
        nums[idx[ok.to_numpy()]] = vals[ok].to_numpy(dtype=float)
        has_flag = (fl != "").to_numpy()
        if has_flag.any():
            embedded = True
            flags[idx[has_flag]] = fl[has_flag].to_numpy()
    return nums, (flags if embedded else None), embedded


def _embedded_share(R: Any, land: str) -> float:
    """The share of a text column's filled cells that read as a number with an embedded flag, or a bare flag."""
    txt = R.texts[land].astype(str).str.strip()
    f = txt[txt != ""]
    if not len(f):
        return 0.0
    m = f.head(5000).str.extract(_EMBED)
    ok = m["num"].notna() | m["flag"].notna()
    return float(ok.mean()) if float(m["num"].notna().mean()) >= 0.5 else 0.0


# --------------------------------------------------------------------------------------------- 0. a wide table of periods
# wave 5, G7 (stretch): a WIDE table whose columns are periods (2019-01, 2019Q1, 2019: the shape Eurostat publishes) is reshaped to
# one row per series and period before anything else reads it; a cell's embedded flag (":" , "123.4 p") stays in the value cell,
# where the structure layer strips it and counts it by the publisher's vocabulary (engine/flag_vocab.json).
_WIDE_MONTH = re.compile(r"^(\d{4})\s*[-_/M]\s*(0?[1-9]|1[0-2])$")
WIDE_MIN_PERIODS = 6
WIDE_MIN_YEARS = 5


def _header_period(h: str) -> Optional[Tuple[str, str]]:
    """(the family "month" | "quarter" | "half-year" | "year", the header as one period label) for a column header that is a period,
    else None: 2019-01 and 2019M01 are months, 2019Q1 and 2019-Q1 quarters, 2019H1 and 2019-S2 half-years, 2019 a year."""
    t = str(h).strip()
    m = _WIDE_MONTH.match(t)
    if m:
        return "month", "%s-%02d" % (m.group(1), int(m.group(2)))
    if _Q_A.match(t) or _Q_B.match(t):
        return "quarter", t
    if _H_A.match(t):
        return "half-year", t
    if _Y_ONLY.match(t) and 1800 <= int(t) <= 2200:
        return "year", t
    return None


def wide_to_long(data: bytes, max_rows: int = 200000) -> Optional[Dict[str, Any]]:
    """The long form of a wide table of periods: {"csv": bytes, "info": {family, periods, first, last, id_columns, rows_in,
    rows_out}}, or None when the table is not one (fewer than 6 period columns, 5 years; period columns of two families; no
    column that names the series; most period cells empty; or the long table would pass max_rows). The id columns keep their
    headers (a "geo\\TIME_PERIOD" is "geo"); the period and the value are columns TIME_PERIOD and OBS_VALUE, the cell's own
    text, flags in it; an empty cell is no observation and is left out."""
    import csv
    import io
    import pandas as pd
    # the header row first: a long table (no run of period headers) is left at once, before any row is read
    try:
        head = next(csv.reader(io.StringIO(data[:262144].decode("utf-8-sig", errors="ignore"))), [])
    except Exception:  # noqa: BLE001
        return None
    kinds0 = [_header_period(c) for c in head]
    if sum(1 for k in kinds0 if k) < WIDE_MIN_YEARS:
        return None
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception:  # noqa: BLE001 - not a table this can read
        return None
    cols = [str(c) for c in df.columns]
    kinds = [_header_period(c) for c in cols]
    fams = {k[0] for k in kinds if k}
    if len(fams) != 1:
        return None
    fam = next(iter(fams))
    pcols = [c for c, k in zip(cols, kinds) if k]
    ids = [c for c, k in zip(cols, kinds) if not k]
    if len(pcols) < (WIDE_MIN_YEARS if fam == "year" else WIDE_MIN_PERIODS) or not ids or len(ids) > len(pcols):
        return None
    if len(set(pcols)) != len(pcols) or len(df) * len(pcols) > 4 * max_rows:
        return None
    label = {c: k[1] for c, k in zip(cols, kinds) if k}
    body = df[pcols].apply(lambda s: s.str.strip())
    if float((body != "").to_numpy().mean()) < 0.5:
        return None
    long = df[ids].copy()
    long.columns = [re.split(r"\\", c)[0] if "\\" in c else c for c in ids]
    if len(set(long.columns)) != len(long.columns):
        return None
    frames = []
    for c in pcols:
        keep = body[c] != ""
        f = long[keep].copy()
        f["TIME_PERIOD"] = label[c]
        f["OBS_VALUE"] = body.loc[keep, c]
        frames.append(f)
    out = pd.concat(frames, ignore_index=True)
    if len(out) < 2 or len(out) > max_rows:
        return None
    buf = io.StringIO()
    out.to_csv(buf, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    return {"csv": buf.getvalue().encode("utf-8"),
            "info": {"family": fam, "periods": len(pcols), "first": label[pcols[0]], "last": label[pcols[-1]],
                     "id_columns": list(long.columns), "rows_in": int(len(df)), "rows_out": int(len(out))}}


# --------------------------------------------------------------------------------------------- 1. cube roles
def _empty(kind: str, reason: str, **kw: Any) -> Dict[str, Any]:
    S = {"kind": kind, "usable": False, "reason": reason, "version": VERSION, "dims": [], "metadata": [],
         "slices": [], "breakdowns": [], "default": None, "flags": None, "publisher": None, "official": False}
    S.update(kw)
    return S


def detect(R: Any, hidden: Iterable[str] = (), budget_s: Optional[float] = None, headers: Optional[Sequence[str]] = None
           ) -> Dict[str, Any]:
    """The structure of the table the engine read (R: nl_browser._Reading), from its landed, non-hidden columns only.
    Returns S: {kind, usable, reason, date, measure, metadata, dims, flags, slices, breakdowns, default, hash, ...}
    plus private arrays (keys starting with "_") the slices are cut from. Never raises on a table it cannot read: it
    returns kind "not_cube" with the reason."""
    tm = _Timer(WALL_GUARD_S if budget_s is None else max(float(budget_s), WALL_GUARD_S))
    try:
        return _detect(R, set(str(h) for h in hidden or ()), tm, headers)
    except MemoryError:
        return _empty("not_cube", "the table is too large to read as a cube", resolved=False)
    except _WallGuard:
        return _empty("not_cube", "the table took too long to read, so no structure was found in it", resolved=False)


def _detect(R: Any, hidden: Set[str], tm: _Timer, headers: Optional[Sequence[str]]) -> Dict[str, Any]:
    import numpy as np
    import pandas as pd
    order = [str(c) for c in (R.land.values() if getattr(R, "land", None) else [])]
    order = list(dict.fromkeys(order + [str(c) for c in R.values.columns]))
    cols = [c for c in order if c not in hidden]
    head = {c: R.header(c) for c in cols}
    publisher = _publisher([head[c] for c in cols] + [c for c in hidden])
    meta_named = [c for c in cols if _norm(head[c]) in _META]
    official = bool(publisher) or len(meta_named) >= 3
    # wave 5d: the column that holds the table's figures (VALUE, OBS_VALUE) is withheld (the scan flagged some of its values as
    # personal: a count of nine or more digits has the shape of a national ID number). No other number column may stand in for
    # it (a unit id or a vector coordinate read as the measure printed a constant figure, 0% change): an official table whose
    # value column is withheld is not read, and the reason is said. The names are hints; the refusal is the safe side of one.
    withheld_value = [R.header(h) for h in sorted(hidden) if _norm(R.header(h)) in _VALUE_NAMES]
    if withheld_value and official:
        return _empty("cube_incomplete",
                      "the column that holds the table's figures (%s) is withheld as possibly personal, so no figure can be read: "
                      "choose Keep for it on the consent card if it holds numbers only" % withheld_value[0],
                      publisher=publisher, official=official)
    # -- the date: the column with the most dates the engine reads
    date, n_d = None, 0
    for c in cols:
        if R.kind(c) == "date":
            k = int(R.dates(c).notna().sum())
            if k > n_d or (k == n_d and date is not None and _norm(head[c]) in _DATE_NAMES):
                date, n_d = c, k
    date_family = ""
    dts = None
    if date is None or n_d < 6:
        pl = _period_labels(R, cols, head)                  # 2012-Q1, Q1 2012, 2012: periods the engine does not read as dates
        if pl is None:
            return _empty("not_cube", "no column holds dates", publisher=publisher, official=official)
        date, dts, date_family = pl
    if dts is None:
        dts = R.dates(date)
    dated = dts.notna().to_numpy()
    # -- the measure: the number that moves, with the most distinct values (VALUE / OBS_VALUE a hint)
    cands = []
    for c in cols:
        if c == date:
            continue
        kind = R.kind(c)
        if kind == "number":
            v = R.numbers(c)[dated]
            nd = int(v.nunique())
            if nd >= 2:
                cands.append((c, nd / max(1, int(v.notna().sum())), nd))
        elif _norm(head[c]) in _VALUE_NAMES and _embedded_share(R, c) >= 0.8:
            cands.append((c, 1.0, 999999))
    if not cands:
        return _empty("not_cube", "no number column moves over time", publisher=publisher, official=official)
    hinted = [x for x in cands if _norm(head[x[0]]) in _VALUE_NAMES]
    measure = (hinted[0] if hinted else max(cands, key=lambda x: (x[1], x[2])))[0]
    vals, emb_flags, embedded = _measure_values(R, measure)
    # -- metadata and dimension candidates, by behaviour
    n_rows = int(dated.sum())
    rows = np.flatnonzero(dated)
    metadata: List[Dict[str, Any]] = []
    cat: Dict[str, Any] = {}               # landed -> (codes over dated rows, labels)
    other_numbers: List[str] = []
    for c in cols:
        if c in (date, measure):
            continue
        t = R.texts[c].astype(str).str.strip().to_numpy()[rows]
        filled = t != ""
        nf = int(filled.sum())
        if nf == 0:
            metadata.append({"column": head[c], "landed": c, "class": "empty"})
            continue
        uniq = pd.unique(t[filled])
        if len(uniq) == 1 and nf == n_rows:
            metadata.append({"column": head[c], "landed": c, "class": "constant", "value": str(uniq[0])[:80]})
            continue
        if len(uniq) == 1:
            metadata.append({"column": head[c], "landed": c, "class": "constant", "value": str(uniq[0])[:80],
                             "blank_rows": int(n_rows - nf)})
            continue
        if R.kind(c) == "number" and _norm(head[c]) not in _META and len(uniq) > MAX_MEMBERS:
            other_numbers.append(c)
            continue
        codes, labels = _factorize(np.where(filled, t, ""))
        cat[c] = (codes, labels, filled)
    # flags by strong evidence: a few short codes, with blanks, or a code whose rows have a blank measure
    blank_measure = np.isnan(vals[rows])
    flags: List[str] = []
    for c, (codes, labels, filled) in list(cat.items()):
        nonblank = [lb for lb in labels if lb != ""]
        if len(nonblank) > FLAG_MAX_CODES:
            continue
        short = all(len(lb) <= FLAG_CODE_LEN for lb in nonblank)
        named = _norm(head[c]) in _FLAG_NAMES
        if not (short or named):
            continue
        empties = 1.0 - float(filled.mean())
        predicts = False
        for i, lb in enumerate(labels):
            if lb == "":
                continue
            m = codes == i
            if int(m.sum()) >= 3 and float(blank_measure[m].mean()) >= FLAG_BLANK_SHARE:
                predicts = True
                break
        if predicts or (empties >= 0.05 and (short or named)) or (named and short):
            flags.append(c)
            del cat[c]
    # dimension candidates: 2-400 levels; a metadata-named column (VECTOR, COORDINATE, DGUID, UOM) never a dimension
    dims0 = []
    for c, (codes, labels, filled) in cat.items():
        nl = len([lb for lb in labels if lb != ""])
        if _norm(head[c]) in _META:
            continue
        if 2 <= nl <= MAX_KEY_MEMBERS:
            dims0.append(c)
    # repeated member names (wave 5, gap 5): a name that stands for two members is keyed by a one-to-one id (an id column, or
    # the one part of a dotted COORDINATE that tells that dimension's members apart), else by its parent's name
    keys, base_names = _key_members(cat, dims0, head, metadata, n_rows)
    # aliases: 1:1 with a dimension (DGUID with GEO, UOM_ID with UOM); the metadata-named one is the alias
    alias_of: Dict[str, str] = {}
    for c in cat:
        if c in dims0:
            continue
        for d in dims0:
            if _one_to_one(cat[c][0], cat[d][0]):
                alias_of[c] = d
                break
    for a, b in [(a, b) for i, a in enumerate(dims0) for b in dims0[i + 1:]]:
        if a in alias_of or b in alias_of:
            continue
        if _one_to_one(cat[a][0], cat[b][0]):
            # neither is metadata-named: the one whose labels are words is the dimension and the other an alias; a series id
            # (v100000, 1.1.1, in any language: VECTEUR, COORDONNEE) is never the dimension (wave 5e, P9). With both or neither
            # an id, the one with the more readable labels
            ia, ib = _id_like(cat[a][1]), _id_like(cat[b][1])
            la = sum(len(x) for x in cat[a][1]) / max(1, len(cat[a][1]))
            lb_ = sum(len(x) for x in cat[b][1]) / max(1, len(cat[b][1]))
            keep_a = (not ia) if ia != ib else la >= lb_
            alias_of[b if keep_a else a] = a if keep_a else b
    dims = [d for d in dims0 if d not in alias_of]
    # series ids: a column that is 1:1 with the dimensions' key (VECTOR, COORDINATE), or any metadata-named column left
    if dims:
        key_codes = _combine([cat[d][0] for d in dims])
    else:
        key_codes = np.zeros(n_rows, dtype=np.int64)
    for c in cat:
        if c in dims or c in alias_of:
            continue
        cls = "series_id" if _one_to_one(cat[c][0], key_codes) else ("unit" if _norm(head[c]) in _UNIT_META else
                                                                      "series_id" if _norm(head[c]) in _META else "other")
        metadata.append({"column": head[c], "landed": c, "class": cls, "distinct": len(cat[c][1])})
    for c, d in alias_of.items():
        metadata.append({"column": head[c], "landed": c, "class": "alias", "alias_of": head[d]})
    for c in flags:
        metadata.append({"column": head[c], "landed": c, "class": "flag"})
    base = {"publisher": publisher, "official": official,
            "date": {"column": head[date], "landed": date}, "metadata": metadata}
    if len(dims) > MAX_DIMS:
        return _empty("not_cube", "more than %d dimensions" % MAX_DIMS, **base)
    # a second number that moves within a series: a business export with two measures is read by the engine's own rules
    others_varying = [m["column"] for m in metadata if m["class"] == "other"] + [head[c] for c in other_numbers]
    for c, _s, _n in cands:
        if c != measure and c not in alias_of and R.kind(c) == "number" and c not in [m["landed"] for m in metadata]:
            others_varying.append(head[c])
    if others_varying and not official:
        return _empty("not_cube", "more than one measure column (%s)" % ", ".join(sorted(set(others_varying))[:3]), **base)
    # -- the cube: date x dimensions tell the rows apart
    tcode, tlabels = _factorize(dts[dated].dt.strftime("%Y-%m-%d").to_numpy())
    if not dims:
        dup = 1.0 - len(pd.unique(tcode)) / max(1, n_rows)
        kind = "panel_no_relations" if dup <= DUP_MAX else ("cube_incomplete" if official else "not_cube")
        return _empty(kind, "one series" if kind == "panel_no_relations" else
                      "the dates repeat and no column the engine may read tells the repeats apart", **base)
    s_codes, s_index = _series_index([cat[d][0] for d in dims])
    pair = s_codes.astype(np.int64) * (len(tlabels) + 1) + tcode
    n_pairs = len(pd.unique(pair))
    dup = 1.0 - n_pairs / max(1, n_rows)
    if dup > DUP_MAX:
        rep_txt = _repeat_phrase(pair, [(head[d], cat[d][0], cat[d][1]) for d in dims],
                                 {head[k]: v for k, v in base_names.items() if k in head})
        series_code = [h for h in sorted(hidden) if _norm(h) in ("coordinate", "vector", "dguid")]
        why = ("the date and the columns the engine may read (%s) do not tell the rows apart: %s%s of %s rows repeat "
               "a date and a series, so a column that names the series is withheld or set aside%s"
               % (", ".join(head[d] for d in dims), rep_txt, _fmt_count(n_rows - n_pairs), _fmt_count(n_rows),
                  ("; %s is withheld: a series code may tell them apart (keep it on the consent card to read the table)"
                   % series_code[0]) if rep_txt and series_code else ""))
        return _empty("cube_incomplete" if official else "not_cube", why, **base)
    n_series = int(s_index.shape[0])
    if n_series > MAX_SERIES:
        return _empty("not_cube", "more than %s series" % _fmt_count(MAX_SERIES), **base)
    if n_series * len(tlabels) > MAX_CELLS:
        return _empty("not_cube", "more than %s series-dates to hold" % _fmt_count(MAX_CELLS), **base)
    # -- the tensor: V[series, time] in base units, E (a row exists), F (its flag code)
    torder = np.argsort(np.array(tlabels))
    trank = np.empty_like(torder)
    trank[torder] = np.arange(len(torder))
    times = [tlabels[i] for i in torder]
    tix = trank[tcode]
    scale_row, scale_info = _scale(R, cat, metadata, rows, head, official)
    if scale_info.get("scale_unknown"):
        return _empty("cube_incomplete" if official else "not_cube",
                      "a scale column (%s) holds a word the engine does not read, so no figure is shown at a scale it might get wrong"
                      % "; ".join(scale_info["scale_unknown"][:3]), **base)
    v = vals[rows] * scale_row
    # wave 5d: half a unit of the last published digit of EACH series, in base units. A table whose members have different
    # scale factors (dollars in millions beside units sold) or different DECIMALS has no one unit: a sum-check's rounding
    # tolerance is the one of the series it reads, not of the table's first scale
    half_unit = np.zeros(n_series)
    np.maximum.at(half_unit, s_codes, 0.5 * 10.0 ** (-_row_decimals(cat, head, rows, _decimals(R, measure, metadata)))
                  * np.abs(scale_row))
    scale_series = np.zeros(n_series)
    np.maximum.at(scale_series, s_codes, np.abs(scale_row))
    V = np.full((n_series, len(times)), np.nan)
    E = np.zeros((n_series, len(times)), dtype=bool)
    V[s_codes, tix] = v
    E[s_codes, tix] = True
    # rows that repeat a series and a date (at most 1%): the first is kept
    seen = np.zeros((n_series, len(times)), dtype=bool)
    first = np.ones(n_rows, dtype=bool)
    if dup > 0:
        _u, first_idx = np.unique(pair, return_index=True)
        first[:] = False
        first[first_idx] = True
        V[:] = np.nan
        E[:] = False
        V[s_codes[first], tix[first]] = v[first]
        E[s_codes[first], tix[first]] = True
    del seen
    flag_col = flags[0] if flags else None
    F = None
    fl_labels: List[str] = []
    if flag_col is not None or embedded:
        if flag_col is not None:
            fc, fl_labels, _f = cat.get(flag_col) or _factor_text(R, flag_col, rows)
        else:
            fc, fl_labels = _factorize(emb_flags[rows])
        F = np.full((n_series, len(times)), -1, dtype=np.int16)
        F[s_codes[first], tix[first]] = fc[first]
    months = sorted({t[:7] for t in times})
    monthly = len(months) == len(times)
    dimrecs = []
    for j, d in enumerate(dims):
        codes, labels, _f = cat[d]
        dimrecs.append({"column": head[d], "landed": d, "labels": labels, "role": None, "members": len(labels)})
    unit_info = _units(R, cat, metadata, alias_of, dims, rows, head, s_index, s_codes)
    S: Dict[str, Any] = {
        "kind": "cube", "usable": False, "reason": "", "version": VERSION, "publisher": publisher,
        "official": official, "date": {"column": head[date], "landed": date, "labels": date_family or None},
        "period": _period_of(times, date_family),
        "measure": {"column": head[measure], "landed": measure, "embedded_flags": embedded,
                    "decimals": _decimals(R, measure, metadata), **scale_info, **unit_info["measure"]},
        "metadata": metadata, "dims": dimrecs, "series": n_series, "times": len(times), "months": len(months),
        "monthly": monthly, "rows": int(R.n), "rows_read": n_rows, "duplicates": int(n_rows - n_pairs),
        "slices": [], "breakdowns": [], "default": None, "flags": None, "corrections": [], "keys": keys,
        "_V": V, "_E": E, "_F": F, "_flag_labels": fl_labels, "_flag_column": head[flag_col] if flag_col else
        ("%s (embedded)" % head[measure] if embedded else None),
        "_SM": s_index, "_times": times, "_months": [t[:7] for t in times],
        "_row_series": _row_map(R.n, rows, s_codes), "_row_time": _row_map(R.n, rows, tix),
        "_member_unit": unit_info["member_unit"], "_half_unit": half_unit, "_scale_series": scale_series,
    }
    _measure_type(S)
    # -- the dimension that names what is measured, first: the table's measure is its default member's, and the other
    # dimensions' relations are read on that member's cells (wave 5, gap 2)
    _measure_dims(S)
    # -- relations, dimension by dimension (adjustment pairs first: the other sum-checks run on the unadjusted member)
    for j in range(len(dims)):
        tm.check()
        try:
            _adjustment(S, j)
        except _TooLarge:
            pass
    for j in range(len(dims)):
        rec = S["dims"][j]
        if rec["role"]:
            continue
        if len(rec["labels"]) == 1:
            rec["role"] = "constant"
            continue
        tm.check()
        if rec.get("mixed_units"):
            _mixed_measure(S, j)                       # a unit that varies with this dimension but is not a measure: typed anyway
            continue
        try:
            if S["measure"]["type"] in ("rate", "index"):
                _rate_aggregate(S, j, tm)
            else:
                _relations(S, j, tm)
        except _TooLarge:
            rec["role"] = "unresolved"
            rec["why"] = "too many members and dates to check within the memory budget"
    for rec in S["dims"]:
        if rec["role"] in (None, "unresolved"):
            _rule6(S, rec)
    _flags(S)
    _slices(S)
    S["usable"] = _usable(S) and bool(S["slices"])
    if not S["usable"] and not S["reason"]:
        S["reason"] = "every dimension adds up across its members (no total, part or adjusted copy to keep apart)"
    S["hash"] = structure_hash(S)
    S["detect_seconds"] = round(time.perf_counter() - tm.t0, 4)
    return S


_COORD = re.compile(r"^\d+(?:\.\d+)+$")
KEY_UNIQUE_SHARE = 0.5          # a name column is a NAME column (not a grouping) when this share of its names has one member


_ID_LIKE = re.compile(r"^[A-Za-z]{0,4}[\s_-]?\d[\d.\-_]*$")


def _id_like(labels: Sequence[str]) -> bool:
    """Whether a column's labels are ids (v100000, 1.1.1, C104, 2.15): every filled label has a digit and little else. A column of words
    is never one, whatever its name."""
    xs = [str(x) for x in labels if str(x) != ""]
    return bool(xs) and all(_ID_LIKE.match(x) for x in xs)


def _functional(b: Any, a: Any) -> bool:
    """Whether every value of b has exactly one value of a (b determines a)."""
    import numpy as np
    import pandas as pd
    pairs = len(pd.unique(b.astype(np.int64) * (int(a.max()) + 2) + a))
    return pairs == len(pd.unique(b))


def _key_members(cat: Dict[str, Any], dims0: List[str], head: Dict[str, str], metadata: List[Dict[str, Any]], n_rows: int
                 ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, str]]]:
    """Members named by a repeated name are keyed by what tells them apart. For each dimension whose name column has a finer
    key beside it (an id: at least as many distinct values as the names, each of which belongs to ONE name, and most names
    belong to one id) the members are the id's values, printed by their name and qualified by the id when two share a
    name ("Other (id 5)"). The ids are looked for among the other readable columns, and among the parts of a dotted
    COORDINATE (each part checked against the dimension, never assumed by position). With no id, a parent column (its values are
    members' names, and the pair name and parent is finer than the name) qualifies the name by its parent's: "Other
    (Retail)". Mutates cat, dims0 and metadata; returns the records of what keyed what and each qualified label's base name."""
    import numpy as np
    import pandas as pd
    keys: List[Dict[str, Any]] = []
    base: Dict[str, Dict[str, str]] = {}
    parts: List[Tuple[str, int, Any, List[str]]] = []                 # (column, part number, per-label codes, part values)
    for c, (codes, labels, _f) in list(cat.items()):
        nb = [lb for lb in labels if lb != ""]
        if len(nb) >= 2 and all(_COORD.match(lb) for lb in nb):
            n_parts = {lb.count(".") for lb in nb}
            if len(n_parts) == 1:
                split = [lb.split(".") if lb != "" else [""] * (n_parts.copy().pop() + 1) for lb in labels]
                for k in range(n_parts.copy().pop() + 1):
                    vals = [x[k] for x in split]
                    parts.append((c, k + 1, np.array(pd.factorize(np.array(vals, dtype=object))[0]), vals))
    for a in list(dims0):
        if a not in cat or a not in dims0:
            continue
        ca, la, fa = cat[a]
        na = len([x for x in la if x != ""])
        done = False
        # 1. an id column (another readable dimension candidate that is finer and belongs to this name)
        for b in list(dims0):
            if b == a or b not in cat or done:
                continue
            cb, lb_, fb = cat[b]
            nb_ = len([x for x in lb_ if x != ""])
            if nb_ <= na or not _functional(cb, ca):
                continue
            # the names that belong to one id only
            per_name = pd.Series(cb).groupby(ca).nunique()
            if float((per_name == 1).mean()) < KEY_UNIQUE_SHARE:
                continue
            _fold(cat, dims0, a, b, cb, lb_, ca, la, base, keys, head, "id", None)
            metadata.append({"column": head[b], "landed": b, "class": "member_id", "id_of": head[a]})
            done = True
        if done:
            continue
        # 2. a part of a dotted COORDINATE
        for (c, k, pc, pv) in parts:
            if c not in cat:
                continue
            cc = cat[c][0]
            row_part = pc[cc]
            nb_ = len(pd.unique(row_part))
            if nb_ <= na or not _functional(row_part, ca):
                continue
            per_name = pd.Series(row_part).groupby(ca).nunique()
            if float((per_name == 1).mean()) < KEY_UNIQUE_SHARE:
                continue
            labs = [None] * nb_
            for code, val in zip(pc, pv):
                if labs[int(code)] is None:
                    labs[int(code)] = val
            _fold(cat, dims0, a, None, row_part, labs, ca, la, base, keys, head, "%s part %d" % (head[c], k), "id")
            done = True
            break
        if done:
            continue
        # 3. a parent column: its values are names of this dimension's members
        for b in list(dims0):
            if b == a or b not in cat or done:
                continue
            cb, lb_, fb = cat[b]
            vals_b = {x for x in lb_ if x != ""}
            if not vals_b or not vals_b <= set(la):
                continue
            pairs = pd.factorize(ca.astype(np.int64) * (int(cb.max()) + 2) + cb)[0]
            if len(pd.unique(pairs)) <= na:
                continue
            per_name = pd.Series(pairs).groupby(ca).nunique()
            if float((per_name == 1).mean()) < KEY_UNIQUE_SHARE:
                continue
            labs = [None] * (int(pairs.max()) + 1)
            first = {}
            for i, pcode in enumerate(pairs):
                first.setdefault(int(pcode), i)
            lab_pair = {pc_: (la[int(ca[i])], lb_[int(cb[i])]) for pc_, i in first.items()}
            _fold(cat, dims0, a, b, pairs, [lab_pair[i] for i in range(len(lab_pair))], ca, la, base, keys, head, "parent", None)
            metadata.append({"column": head[b], "landed": b, "class": "parent", "parent_of": head[a]})
            done = True
    return keys, base


def _fold(cat: Dict[str, Any], dims0: List[str], a: str, b: Optional[str], new_codes: Any, id_labels: List[Any], ca: Any,
          la: List[str], base: Dict[str, Dict[str, str]], keys: List[Dict[str, Any]], head: Dict[str, str], by: str,
          kind: Optional[str]) -> None:
    """Replace dimension a's members by the keyed ones: label = the name, qualified by the id (or the parent) when two members
    share the name; the id column b leaves the dimension candidates."""
    import numpy as np
    first: Dict[int, int] = {}
    for i, code in enumerate(new_codes):
        first.setdefault(int(code), i)
    n = len(id_labels)
    names, quals = [], []
    for m in range(n):
        i = first[m]
        names.append(la[int(ca[i])])
        quals.append(id_labels[m] if not isinstance(id_labels[m], tuple) else id_labels[m][1])
    count: Dict[str, int] = {}
    for nm in names:
        count[nm] = count.get(nm, 0) + 1
    labels: List[str] = []
    for m in range(n):
        nm = names[m]
        if nm != "" and count[nm] > 1:
            q = quals[m]
            if by == "parent":
                lab = "%s (%s)" % (nm, q or "no parent")
            elif kind == "id":
                lab = "%s (id %s)" % (nm, q)               # a part of a dotted coordinate
            else:
                lab = "%s (%s)" % (nm, q)                  # an id column's own code
        else:
            lab = nm
        labels.append(lab)
    # a label that still repeats (a parent's name that repeats too) keeps its place: the duplicates are then true duplicates
    base[a] = {labels[m]: names[m] for m in range(n)}
    filled = cat[a][2]
    cat[a] = (np.asarray(new_codes, dtype=np.int64), labels, filled)
    if b is not None:
        cat.pop(b, None)
        if b in dims0:
            dims0.remove(b)
    dup_names = sorted(nm for nm, k in count.items() if k > 1 and nm != "")
    keys.append({"dim": head[a], "by": head[b] if b is not None else by, "duplicates": dup_names})


def _repeat_phrase(pair: Any, dims: List[Tuple[str, Any, List[str]]], base: Dict[str, Dict[str, str]]) -> str:
    """Why the rows repeat, in the reader's words: the member name that appears more than once for the same date
    ('"Other" appears more than once for the same date (up to 3 times)'), else nothing."""
    import numpy as np
    import pandas as pd
    s = pd.Series(pair)
    dup = s.duplicated(keep=False).to_numpy()
    if not dup.any():
        return ""
    worst = int(s[dup].value_counts().max())
    best = None
    for head_name, codes, labels in dims:
        sub = pd.Series(np.asarray(codes)[dup])
        top = sub.value_counts()
        if not len(top):
            continue
        lab = labels[int(top.index[0])]
        if lab == "":
            continue
        k = len(top)
        if best is None or k < best[0]:
            best = (k, head_name, lab)
    if best is None:
        return ""
    name = (base.get(best[1]) or {}).get(best[2], best[2])
    return '"%s" appears more than once for the same date (up to %d times); ' % (name, worst)


def _factor_text(R: Any, c: str, rows: Any) -> Tuple[Any, List[str], Any]:
    import numpy as np
    t = R.texts[c].astype(str).str.strip().to_numpy()[rows]
    codes, labels = _factorize(t)
    return codes, labels, t != ""


def _row_map(n: int, rows: Any, vals: Any) -> Any:
    import numpy as np
    out = np.full(int(n), -1, dtype=np.int32)
    out[rows] = vals
    return out


def _one_to_one(a: Any, b: Any) -> bool:
    import numpy as np
    import pandas as pd
    na, nb = len(pd.unique(a)), len(pd.unique(b))
    if na != nb or na < 2:
        return False
    return len(pd.unique(a.astype(np.int64) * (int(b.max()) + 2) + b)) == na


def _combine(code_lists: List[Any]) -> Any:
    import numpy as np
    import pandas as pd
    key = np.zeros(len(code_lists[0]), dtype=np.int64)
    for c in code_lists:
        key = key * (int(c.max()) + 2) + c
        key, _u = pd.factorize(key)
        key = key.astype(np.int64)
    return key


def _series_index(code_lists: List[Any]) -> Tuple[Any, Any]:
    """(each row's series, the members of each series as [n_series, n_dims])."""
    import numpy as np
    key = _combine(code_lists)
    n = int(key.max()) + 1 if len(key) else 0
    sm = np.zeros((n, len(code_lists)), dtype=np.int32)
    first = np.full(n, -1, dtype=np.int64)
    idx = np.arange(len(key))
    first[key[::-1]] = idx[::-1]
    for j, c in enumerate(code_lists):
        sm[:, j] = c[first]
    return key.astype(np.int32), sm


def _decimals(R: Any, measure: str, metadata: List[Dict[str, Any]]) -> int:
    for m in metadata:
        if _norm(m["column"]) in _DECIMALS and m["class"] == "constant":
            try:
                return max(0, min(9, int(float(m["value"]))))
            except ValueError:
                pass
    t = R.texts[measure].astype(str).str.strip()
    t = t[t.str.contains(r"\.\d", regex=True)].head(5000)
    if not len(t):
        return 0
    return int(min(9, t.str.extract(r"\.(\d+)")[0].str.len().max() or 0))


def _row_decimals(cat: Dict[str, Any], head: Dict[str, str], rows: Any, base: int) -> Any:
    """Each row's number of decimals: the DECIMALS column's own value where it varies (one for dollars, another for counts), else
    the table's (`base`, from a constant DECIMALS or the measure's text)."""
    import numpy as np
    for c, (codes, labels, _f) in cat.items():
        if _norm(head.get(c, c)) in _DECIMALS:
            per = []
            for lb in labels:
                try:
                    per.append(max(0, min(9, int(float(lb)))))
                except ValueError:
                    per.append(base)
            return np.array(per, dtype=float)[codes]
    return np.full(len(rows), float(base))


def _fold(v: Any) -> str:
    """A cell's words in lower case with accents folded and spaces kept (MILLIERS, millones, Tausend)."""
    t = unicodedata.normalize("NFKD", str(v or ""))
    return " ".join("".join(ch for ch in t if not unicodedata.combining(ch)).lower().split())


def _scale(R: Any, cat: Dict[str, Any], metadata: List[Dict[str, Any]], rows: Any, head: Dict[str, str], official: bool = False
           ) -> Tuple[Any, Dict[str, Any]]:
    """Each row's scale factor (SCALAR_FACTOR "thousands", SCALAR_ID 3, UNIT_MULT 6, FACTEUR SCALAIRE "milliers"), and what was read. A column
    that is a scale by its name, or by its words (every value a scale word, one of them not "units"), that holds a word the engine does
    not read is reported as `unknown`: the table is then refused, never read at a scale of 1 (wave 5e, P9)."""
    import numpy as np
    n = len(rows)
    unknown: List[str] = []
    for m in metadata:
        nm = _norm(m["column"])
        if m["class"] == "constant" and (nm in _SCALE_WORDS or nm in _SCALE_IDS):
            f = _scale_word(m["value"], nm in _SCALE_IDS or nm in ("unitmult", "unitmultiplier"))
            if f:
                return np.full(n, f), {"scale": str(m["value"]), "factor": f, "scale_column": m["column"]}
            unknown.append("%s = %s" % (m["column"], str(m["value"])[:30]))
    for c, (codes, labels, _f) in cat.items():
        nm = _norm(head.get(c, c))
        if nm in _SCALE_WORDS or nm in _SCALE_IDS:
            fs = [_scale_word(lb, nm in _SCALE_IDS or nm in ("unitmult", "unitmultiplier")) for lb in labels]
            for lb, f in zip(labels, fs):
                if f is None and lb != "":
                    unknown.append("%s = %s" % (head.get(c, c), lb[:30]))
            arr = np.array([f or 1.0 for f in fs])[codes]
            return arr, {"scale": "varies by series", "factor": None, "scale_column": head.get(c, c), "scale_unknown": unknown}
    # by behaviour: a constant column (or a few short words) that holds scale words and nothing else
    for m in (metadata if official else []):
        if m["class"] == "constant" and _fold(m.get("value")) in _SCALE_FACTOR and _fold(m.get("value")) not in ("unit", "units", "ones"):
            f = _SCALE_FACTOR[_fold(m["value"])]
            return np.full(n, f), {"scale": str(m["value"]), "factor": f, "scale_column": m["column"]}
    return np.ones(n), {"scale": "units", "factor": 1.0, "scale_column": None, "scale_unknown": unknown}


def _scale_word(v: Any, exponent: bool) -> Optional[float]:
    s = _fold(v)
    if s in _SCALE_FACTOR:
        return _SCALE_FACTOR[s]
    try:
        x = float(s)
    except ValueError:
        return None
    if exponent and 0 <= x <= 12 and x == int(x):
        return float(10 ** int(x))
    return None


def _units(R: Any, cat: Dict[str, Any], metadata: List[Dict[str, Any]], alias_of: Dict[str, str], dims: List[str],
           rows: Any, head: Dict[str, str], s_index: Any, s_codes: Any) -> Dict[str, Any]:
    """The unit of measure: a constant (Dollars), or one that changes with one dimension (a measure dimension: dollars
    beside units), recorded per member."""
    import numpy as np
    out: Dict[str, Any] = {"measure": {"uom": None}, "member_unit": {}}
    for m in metadata:
        if m["class"] == "constant" and _norm(m["column"]) in _UNIT_META:
            out["measure"]["uom"] = m["value"]
            return out
    ucol = next((c for c in cat if _norm(head.get(c, c)) in _UNIT_META), None)
    if ucol is None:
        return out
    codes, labels, _f = cat[ucol]
    for j, d in enumerate(dims):
        if d == ucol:
            continue
        dc = cat[d][0]
        pair = dc.astype(np.int64) * (len(labels) + 1) + codes
        import pandas as pd
        if len(pd.unique(pair)) == len(pd.unique(dc)):          # the unit is a function of this dimension
            per = {}
            for mi in range(len(cat[d][1])):
                k = codes[dc == mi]
                if len(k):
                    per[mi] = labels[int(k[0])]
            if len(set(per.values())) > 1:
                out["member_unit"] = {j: per}
                out["measure"]["uom"] = "varies by %s" % head[d]
                return out
    out["measure"]["uom"] = "varies"
    return out


def sums_over_time(m: Dict[str, Any]) -> bool:
    """Whether a measure's months are added up: only when it is positively a flow (G3); anything else is averaged."""
    return str((m or {}).get("aggregation") or "").startswith("sum")


def _measure_type(S: Dict[str, Any]) -> None:
    """flow | stock | rate | index | count | unknown, from the unit and the labels, and the DECISION behind it (type_basis:
    "positively a flow", "positively a stock", "ambiguous: averaged"); how months are added up."""
    m = S["measure"]
    uom = str(m.get("uom") or "")
    # the words that may say what is counted: the measure's column, the members' labels, and the value of a column that
    # holds one value throughout ("Characteristics: Building permits issued")
    consts = [str(x.get("value") or "") for x in S.get("metadata") or []
              if x.get("class") == "constant" and _norm(x.get("column")) not in _META]
    labels = " ".join([m["column"]] + consts + [lb for d in S["dims"] for lb in d["labels"][:60]])
    if S.get("_member_unit"):
        uom = " ".join(sorted(set(v for per in S["_member_unit"].values() for v in per.values())))
    m.update(_classify(uom, labels, m["column"], says=" ".join([m["column"]] + consts)))
    hint = _label_hint(S)
    if hint:
        m["label_hint"] = hint[:60]
    m["currency"] = bool(_CURRENCY.search(uom))
    if m["type"] == "index":
        bases = sorted(set(_INDEX_BASE.findall(uom)))
        m["index_bases"] = bases
    for j, per in (S.get("_member_unit") or {}).items():
        S["dims"][j]["mixed_units"] = True
        S["dims"][j]["unit_of"] = {S["dims"][j]["labels"][mi]: u for mi, u in per.items()}


# a constant column that says WHAT is measured (its header or its words), against one that says how it was taken (a basis, a data type)
_MEASURE_HEADER = re.compile(r"(?i)\b(?:characteristics?|statistics?|indicators?|measures?|variables?|series|concepts?|items?|estimates?|"
                             r"products?|commodit(?:y|ies)|industr(?:y|ies)|trade|sector|activity|caract[\u00e9e]ristiques?|indicateurs?|"
                             r"mesures?|variables?|concepts?|[\u00e9e]l[\u00e9e]ments?|merkmale?|indikatoren?|kennzahlen?|kriterien|"
                             r"indicadores?|medidas?|variables?|conceptos?)\b")
_BASIS_WORDS = re.compile(r"(?i)\b(?:seasonally|unadjusted|adjusted|current (?:prices|dollars)|constant (?:prices|dollars)|chained|nominal|real|"
                          r"annual rate|calendar|trend|data type|basis|valeurs? (?:brutes|ajust[\u00e9e]es)|d[\u00e9e]saisonnalis[\u00e9e]|"
                          r"saisonbereinigt|desestacionalizad)")
_MEASURE_WORDS = re.compile("|".join(x.pattern.replace("(?i)", "", 1) if x.pattern.startswith("(?i)") else x.pattern
                                     for x in (_FLOW_WORDS, _STOCK_LEVEL_WORDS, _RATE_WORDS, _INDEX_WORDS, _LEVEL_PRICE)), re.I)


def _label_hint(S: Dict[str, Any]) -> str:
    """The words that name what the table's one measure is (wave 5e, P12): a constant column whose HEADER says what is measured
    (Labour force characteristics, Statistics, Indicator) or whose value holds a measure word (permits, sales, employed, rate, index), and
    never one that says how it was taken ("Seasonally adjusted": a basis); never a constant that says neither (an analyst's name, a
    note): the measure column's own name stands then. The first such column in the file."""
    best = ("", -1)
    for x in S.get("metadata") or []:
        if x.get("class") != "constant" or _norm(x.get("column")) in _META:
            continue
        v = str(x.get("value") or "").strip()
        if not v or re.fullmatch(r"[\d.,\s-]*", v):
            continue
        score = (2 if _MEASURE_HEADER.search(str(x.get("column") or "")) else 0) + (1 if _MEASURE_WORDS.search(v) else 0) \
            - (3 if _BASIS_WORDS.search(v) else 0)
        if score > 0 and score > best[1]:
            best = (v, score)
    return best[0]


def _classify(uom: str, bag: str, column: str, member: str = "", says: str = "") -> Dict[str, Any]:
    """One measure's type and the decision behind it: {type, type_basis, type_why, aggregation}. `bag` is the text whose
    words may say what is counted (the measure's column and the table's member labels); `member` a measure dimension's
    member label, whose own words decide first. A currency is a flow (a stock under inventories, balances, assets, debt),
    unless the member is a price or an average (a level). A count (persons, a number) is a flow only when a word in the
    labels says it accumulates over a period (permits issued, births, visits ...), a stock when a word says it is a level
    (employment, population ...), and otherwise AMBIGUOUS: averaged. A rate or an index is a level, never summed."""
    u = str(uom or "")
    own = str(member or "")
    out: Dict[str, Any] = {"type": "unknown", "type_basis": "ambiguous: averaged", "type_why": "", "aggregation": "mean over months"}

    def done(t: str, basis: str, why: str, sums: bool) -> Dict[str, Any]:
        out.update(type=t, type_basis=basis, type_why=why, aggregation="sum over months" if sums else "mean over months")
        return out
    if own and _PRECISION.search(own):
        return done("precision", "precision: never the headline, never summed",
                    "%s is the precision (standard error, margin of error or confidence interval) of another member" % own[:60], False)
    if _INDEX_WORDS.search(u) or (own and _INDEX_WORDS.search(own)):
        return done("index", "an index: a level, never summed", "the unit names an index (%s)" % (u or own)[:40], False)
    if _RATE_WORDS.search(u) or (own and not u and _RATE_WORDS.search(own)):
        return done("rate", "a rate: a level, never summed", "the unit names a rate or a percentage (%s)" % (u or own)[:40], False)
    if _CURRENCY.search(u):
        stock = _STOCK_WORDS.search(bag)
        if stock:
            return done("stock", "positively a stock", "currency, but the labels say %s" % stock.group(0).lower(), False)
        lvl = own or says
        if lvl and _LEVEL_PRICE.search(lvl):
            # wave 5e: an average, a median, a price or a rate in a currency is a level, whether a measure dimension's member says so
            # or the measure's own name and the table's constant labels do ("Average weekly earnings"): never summed over months
            return done("unknown", "ambiguous: averaged", "%s is a price or an average in a currency, a level" % lvl[:60], False)
        return done("flow", "positively a flow", "a currency (%s)" % u[:30], True)
    # an index or a rate named in the labels (not in a unit): never summed, whatever else the labels say
    if _INDEX_WORDS.search(bag):
        return done("index", "an index: a level, never summed", "the labels name an index", False)
    if _RATE_WORDS.search(bag):
        return done("rate", "a rate: a level, never summed", "the labels name a rate or a percentage", False)
    stock_w = _STOCK_LEVEL_WORDS.search(own or "") or _STOCK_LEVEL_WORDS.search(bag)
    flow_w = _FLOW_WORDS.search(own or "") or _FLOW_WORDS.search(u) or _FLOW_WORDS.search(bag)
    count_unit = bool(_COUNT_UNITS.search(u))
    if not u:
        try:
            from northledger.measure import additive_kind
            ak = additive_kind(column)
        except ImportError:
            ak = ""
        if ak == "money":
            return done("flow", "positively a flow", "the measure's name says an amount of money (%s)" % column[:30], True)
        count_unit = ak == "units"
        if not count_unit and not (own or flow_w or stock_w):
            return out
    if stock_w and flow_w:
        return done("count" if count_unit else "unknown", "ambiguous: averaged",
                    "the labels hold a flow word (%s) and a stock word (%s)" % (flow_w.group(0).lower(), stock_w.group(0).lower()), False)
    if stock_w:
        return done("stock", "positively a stock", "the labels say %s, a level" % stock_w.group(0).lower(), False)
    if flow_w:
        return done("count" if count_unit else "flow", "positively a flow",
                    "the labels say %s, which accumulates over a period" % flow_w.group(0).lower(), True)
    if count_unit:
        return done("count", "ambiguous: averaged",
                    "a count (%s) with no word in the labels that says it accumulates over time" % (u or "units")[:30], False)
    return out


def _unit_type(uom: str, labels: str, column: str, member: str = "") -> str:
    """flow | stock | rate | index | count | precision | unknown for one unit of measure."""
    return str(_classify(uom, labels, column, member)["type"])


MEASURE_RULE = ("a currency flow, then a count flow, then a stock, then a rate or an index; never a precision member")
_MEASURE_ID = re.compile(r"^M\d{1,2}$")
# the patterns are joined without their own leading (?i): a global inline flag anywhere but the start of a pattern is an error
# in Python 3.11+ (the page's Pyodide is 3.12; native 3.9 only warns), and the flag is passed instead
_MEMBER_CUE = re.compile("|".join(x.pattern.replace("(?i)", "", 1) if x.pattern.startswith("(?i)") else x.pattern
                                  for x in (_PRECISION, _RATE_WORDS, _INDEX_WORDS, _STOCK_LEVEL_WORDS, _FLOW_WORDS)), re.I)


def _member_types(S: Dict[str, Any], j: int) -> List[Dict[str, Any]]:
    """Each member of dimension j typed on its own: its unit (the table's unit, or the one its rows carry when the unit
    column varies with the dimension) and the words of its label decide (nl_structure._classify)."""
    d = S["dims"][j]
    unit_of = d.get("unit_of") or {}
    m = S["measure"]
    consts = [str(x.get("value") or "") for x in S.get("metadata") or []
              if x.get("class") == "constant" and _norm(x.get("column")) not in _META]
    bag = " ".join([m["column"]] + consts)
    base = str(m.get("uom") or "") if not unit_of else ""
    out = []
    for lb in d["labels"]:
        u = str(unit_of.get(lb) or base)
        out.append(dict(_classify(u, bag, m["column"], lb), uom=u, currency=bool(_CURRENCY.search(u))))
    return out


def _is_measure_dim(S: Dict[str, Any], j: int) -> Optional[List[Dict[str, Any]]]:
    """The members' types when dimension j names what is measured (its members' units differ, or their types differ and
    most labels say so, or a member is the precision of another), else None (wave 5, gap 2)."""
    d = S["dims"][j]
    if d.get("role") or len(d["labels"]) < 2 or len(d["labels"]) > 40:
        return None
    types = _member_types(S, j)
    if d.get("mixed_units"):
        return types
    kinds = {c["type"] for c in types}
    prec = any(c["type"] == "precision" for c in types)
    cued = sum(1 for lb in d["labels"] if _MEMBER_CUE.search(lb))
    if (len(kinds) >= 2 or prec) and cued >= 0.5 * len(d["labels"]) and any(c["type"] != "precision" for c in types):
        return types
    return None


def _measure_dims(S: Dict[str, Any]) -> None:
    """The dimension that names what is measured (dollars beside units, a rate beside counts beside standard errors in one
    value column): each member typed separately (M1, M2 ...), a default chosen by the documented rule, the relations of the
    other dimensions then read on that member. The first such dimension only."""
    for j, d in enumerate(S["dims"]):
        types = _is_measure_dim(S, j)
        if types is not None:
            _measure_dim(S, j, types)
            return


def _rank(c: Dict[str, Any], label: str) -> Tuple[int, int]:
    """The default order: a currency flow (0), a count flow or any other positive flow (1), a stock (2), an ambiguous
    count or unknown (3), a rate or an index (4); a precision member never (9). A total's name breaks a tie."""
    t = c["type"]
    r = 0 if t == "flow" and c.get("currency") else 1 if (t in ("flow", "count") and sums_over_time(c)) else \
        2 if t == "stock" else 4 if t in ("rate", "index") else 9 if t == "precision" else 3
    return r, 0 if _says_total(label) else 1


def _measure_dim(S: Dict[str, Any], j: int, types: List[Dict[str, Any]]) -> None:
    """Dimension j is a measure dimension: `measures` lists its members (id M1, M2 ... in file order, unit, type and the
    decision behind it, precision, default); the default is the first by MEASURE_RULE, never a precision member. The table's
    measure becomes the default's; the other dimensions' sum-checks read the default member's cells."""
    rec = S["dims"][j]
    labels = rec["labels"]
    order = sorted(range(len(labels)), key=lambda mi: (_rank(types[mi], labels[mi]), mi))
    best = order[0]
    if types[best]["type"] == "precision":
        rec["role"] = "unresolved"
        rec["why"] = "every member is the precision of another (standard errors, margins of error): nothing to report"
        return
    unit_of = {labels[mi]: types[mi]["uom"] for mi in range(len(labels))} if rec.get("mixed_units") else {}
    meas = []
    for mi, lb in enumerate(labels):
        c = types[mi]
        meas.append({"id": "M%d" % (mi + 1), "index": mi, "name": lb, "uom": c["uom"], "type": c["type"],
                     "type_basis": c["type_basis"], "type_why": c["type_why"], "aggregation": c["aggregation"],
                     "currency": bool(c.get("currency")), "precision": c["type"] == "precision", "default": mi == best})
    rec.update(role="measure", measure_dim=True, measures=meas, total=labels[best], total_index=best, default_index=best,
               components={}, alternatives={}, unit_of=unit_of or rec.get("unit_of") or {},
               why="its members are different measures (%s): one at a time, never added or averaged together; the default is "
                   "the first of %s" % (", ".join(sorted({"%s (%s)" % (m["name"], m["type"]) for m in meas}))[:200], MEASURE_RULE))
    if rec.get("unit_of"):
        rec["mixed_units"] = True
    c = types[best]
    S["measure"].update({k: c[k] for k in ("type", "type_basis", "type_why", "aggregation")}, uom=c["uom"],
                        currency=bool(c.get("currency")), units_vary_by=rec["column"])
    if c["type"] == "index":
        S["measure"]["index_bases"] = sorted(set(_INDEX_BASE.findall(c["uom"])))


def _mixed_measure(S: Dict[str, Any], j: int) -> None:
    """A dimension whose members are measured in different units: a measure dimension (kept for the callers of old)."""
    _measure_dim(S, j, _member_types(S, j))


def slice_type(S: Dict[str, Any], where: Dict[str, Any]) -> Dict[str, Any]:
    """The measure of one slice: its unit and type (a measure dimension's member decides them)."""
    for d in S["dims"]:
        if d.get("measure_dim") and isinstance(where.get(d["column"]), str):
            mm = next((x for x in d["measures"] if x["name"] == where[d["column"]]), None)
            if mm is not None:
                return {"uom": mm["uom"], "type": mm["type"], "currency": mm["currency"], "aggregation": mm["aggregation"],
                        "type_basis": mm["type_basis"], "type_why": mm["type_why"]}
        elif d.get("mixed_units") and isinstance(where.get(d["column"]), str):
            u = str((d.get("unit_of") or {}).get(where[d["column"]]) or "")
            c = _classify(u, " ".join([S["measure"]["column"], where[d["column"]]]), S["measure"]["column"], where[d["column"]])
            return dict(c, uom=u, currency=bool(_CURRENCY.search(u)))
    m = S["measure"]
    return {"uom": m.get("uom"), "type": m["type"], "currency": m.get("currency"), "aggregation": m["aggregation"],
            "type_basis": m.get("type_basis"), "type_why": m.get("type_why")}


def local(S: Dict[str, Any], where: Dict[str, Any]) -> Dict[str, Any]:
    """The structure with the measure of ONE slice: a slice of another member of a measure dimension has its own unit and
    type (dollars, units, a rate), whatever the table's default measure is."""
    st = slice_type(S, where)
    m = S["measure"]
    if all(st.get(k) == m.get(k) for k in ("uom", "type", "aggregation", "type_basis")):
        return S
    return dict(S, measure=dict(m, **st))


PANEL_MAX_SERIES = 60           # nl_browser's long-table layout reads at most this many series side by side


def reads_one_member(S: Dict[str, Any]) -> bool:
    """Whether an official table with a dimension it could not read (role "single") is read one member at a time and says so, instead of
    side by side by the long-table layout: a dimension of places (a national figure), or the members of a FLOW picked by dominance (wave
    5e: a flow's members are never a side-by-side panel; a currency's or an index's are)."""
    return bool(S.get("official")) and any(
        d.get("role") == "single" and (d.get("noun") == "national figure" or (
            d.get("single_by") == "dominance" and sums_over_time(S["measure"]))) for d in S.get("dims") or [])


def _usable(S: Dict[str, Any]) -> bool:
    """Slice the table when adding its rows would be wrong: a relation between members (a verified total, an adjusted
    copy, components, a rate's published aggregate). A panel with no relation (currencies in two units, an official
    table whose members only differ) is read side by side by the long-table layout, as before, when it has at most 60
    series (kind "panel_no_relations"); past that, the layout cannot, and the table is read one member at a time."""
    rel = [d for d in S["dims"] if d["role"] in ("partition", "hierarchy", "adjustment", "components", "rate_aggregate", "parts")
           or (d["role"] == "measure" and not d.get("mixed_units"))]
    if rel:
        return True
    if any(d["role"] in ("single", "measure") for d in S["dims"]):
        # an official table whose geography has no total row is read one member at a time, and says it is not a national
        # figure (wave 5, gap 1); any other table of at most 60 series (currencies, say) is read side by side by the layout
        geo_single = reads_one_member(S)
        if int(S.get("series") or 0) <= PANEL_MAX_SERIES and not geo_single:
            S["kind"] = "panel_no_relations"
            S["reason"] = ("no member of the table is a total, a part or an adjusted copy of another: its %d series are "
                           "read side by side" % int(S.get("series") or 0))
            return False
        return True
    return False


# --------------------------------------------------------------------------------------------- 2. relations
def _dim_mask(S: Dict[str, Any], j: int, restrict: bool = True, adjusted: bool = False) -> Any:
    """The series dimension j's sum-checks read: the other dimensions' default measure (their relations are read on the default
    measure's cells) and, with `restrict`, the unadjusted copy (or with `adjusted`, the adjusted one)."""
    import numpy as np
    SM = S["_SM"]
    mask = np.ones(SM.shape[0], dtype=bool)
    for k, d in enumerate(S["dims"]):
        if k != j and d.get("measure_dim") and d.get("default_index") is not None:
            mask &= SM[:, k] == d["default_index"]          # the other dimensions' relations are read on the default measure
    if restrict:
        for k, d in enumerate(S["dims"]):
            if k != j and d.get("role") == "adjustment" and d.get("nsa_index") is not None:
                mask &= SM[:, k] == d["sa_index" if adjusted else "nsa_index"]
    return mask


def _dim_tensor(S: Dict[str, Any], j: int, restrict: bool = True, adjusted: bool = False) -> Tuple[Any, Any, Any]:
    """A[member, context, time] (base units, NaN where blank or absent), X (a row exists), and each series' context,
    on the reference cells: another dimension's adjusted copy left out (its parts may be adjusted apart); with
    `adjusted`, the adjusted copy's cells only (the record of whether the adjusted parts add up)."""
    import numpy as np
    SM, V, E = S["_SM"], S["_V"], S["_E"]
    mask = _dim_mask(S, j, restrict, adjusted)
    sm = SM[mask]
    others = [k for k in range(SM.shape[1]) if k != j]
    if others:
        ctx = _combine([sm[:, k].astype(np.int64) for k in others])
    else:
        ctx = np.zeros(sm.shape[0], dtype=np.int64)
    M = len(S["dims"][j]["labels"])
    C = int(ctx.max()) + 1 if len(ctx) else 0
    T = V.shape[1]
    if M * C * T > MAX_TENSOR:
        raise _TooLarge()
    A = np.full((M, C, T), np.nan)
    X = np.zeros((M, C, T), dtype=bool)
    A[sm[:, j], ctx] = V[mask]
    X[sm[:, j], ctx] = E[mask]
    return A, X, ctx


_SCALE_WORD = {1.0: "units", 10.0: "tens", 100.0: "hundreds", 1e3: "thousands", 1e6: "millions", 1e9: "billions", 1e12: "trillions"}


def slice_scale(S: Dict[str, Any], where: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    """(the scale as the file words it, its factor) of ONE slice: the table's when every series has one scale, else the slice's own
    (the dollars of a table of dollars in millions beside units sold are in millions, the units are not); (the words, None) when
    the slice's series do not share one."""
    m = S["measure"]
    if m.get("factor") is not None:
        return m.get("scale"), m.get("factor")
    sc, sel = S.get("_scale_series"), _select(S, where)
    if sc is None or sel is None or not len(sel):
        return m.get("scale"), None
    import numpy as np
    u = np.unique(sc[sel])
    if len(u) == 1 and float(u[0]) > 0:
        f = float(u[0])
        return _SCALE_WORD.get(f, "x%g" % f), f
    return m.get("scale"), None


def _tol_unit(S: Dict[str, Any], j: Optional[int] = None, restrict: bool = True, adjusted: bool = False) -> float:
    """Half a unit of the last published digit, in base units (0.5 thousand dollars for a table in thousands). Of dimension j's
    sum-check (wave 5d): the largest half unit among the series that check reads, each series with its own scale factor and
    its own DECIMALS (a table of dollars in millions beside units sold has no one unit). One scale and one DECIMALS give the
    table's, as before."""
    h = S.get("_half_unit")
    if j is not None and h is not None:
        sel = h[_dim_mask(S, j, restrict, adjusted)]
        if len(sel) and float(sel.max()) > 0:
            return float(sel.max())
    f = S["measure"].get("factor") or 1.0
    return 0.5 * (10.0 ** -int(S["measure"].get("decimals") or 0)) * float(f)


def _sum_check(A: Any, X: Any, t: int, parts: Sequence[int], tol_unit: float, nonneg: bool,
               months: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Whether member t is the sum of `parts`, and whether the check COULD HAVE FAILED (wave 5e, P1 and P2).

    On the cells where t has a value and every part has a row, the residual r = t - (the parts with a value) is compared with the
    rounding tolerance of the n+1 values (half a unit of the last published digit each). Only a COMPLETE cell (every part has a
    value) is tested, and a cell SUPPORTS the relation only when it is informative: the larger of |t| and the parts' sum is at least
    POWER_K = 10 tolerances, so that a 10% error in the total would have been seen. A check has one of three results:

      pass        at least 6 informative cells in 3 months are within tolerance (3 when the total is at least 100 tolerances), and
                  they are at least 95% of the informative cells within tolerance plus the cells that are off by more than it; or the
                  residual is EXACTLY nothing (float noise) on 16 cells where something is counted, 3 of them different totals
                  (a count of 0 to 3 has no rounding tolerance worth the name, and 16 exact matches are no coincidence);
      fail        the relation is contradicted: 3 or more complete cells off by more than the tolerance and under 95% hold, or a
                  non-negative flow's parts exceed the total in a cell that is incomplete;
      unresolved  neither: too few informative cells, because the table's magnitudes are as small as its rounding (the tolerance of
                  2.5 counts for four parts is as big as a table of 0 to 2 events a month, which no residual can contradict), or too
                  few complete cells. An unresolved check is NOT a pass and is NOT a fail: nothing is learned from it.

    A cell that is off by more than the tolerance counts against the relation whatever its size."""
    import numpy as np
    P = list(parts)
    out: Dict[str, Any] = {"pass": False, "status": "unresolved", "complete": 0, "within": 0, "months": 0, "incomplete": 0,
                           "share": 0.0, "negative_unallocated": False, "max_rel_residual": None, "max_residual": None,
                           "info": 0.0, "informative": 0, "support": 0, "violated": 0, "exact": 0, "by": None}
    if not P:
        return out
    tgt = A[t]
    have_t = ~np.isnan(tgt)
    pa = A[P]
    rows = X[P].all(axis=0)
    present = ~np.isnan(pa)
    testable = have_t & rows
    complete = testable & present.all(axis=0)
    incomplete = testable & ~present.all(axis=0)
    s = np.where(present, pa, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore"):
        r = tgt - s
        tol = np.maximum(tol_unit * (len(P) + 1), 1e-6 * np.abs(tgt))
        within = (np.abs(r) <= tol) & complete
        scale = np.maximum(np.abs(tgt), np.abs(s))
        informative = complete & (scale >= POWER_K * tol)
        support = within & informative
        violated = complete & ~within
        neg = bool(((r < -tol) & incomplete).any()) if nonneg else False
    nc, n_inf = int(complete.sum()), int(informative.sum())
    n_sup, n_vio = int(support.sum()), int(violated.sum())
    months_c = int(support.any(axis=0).sum())
    share = float(n_sup) / (n_sup + n_vio) if (n_sup + n_vio) else 0.0
    rel = np.abs(r[complete]) / np.maximum(np.abs(tgt[complete]), 1e-300) if nc else np.array([])
    # the cells a pass needs depend on how much a match tells. A total many times the rounding tolerance that equals the sum of its
    # parts to the digit is no coincidence in 3 cells; under heavy suppression a table may hold only 5 complete cells
    info = float(np.median(np.abs(tgt[informative]) / np.maximum(tol[informative], 1e-300))) if n_inf else 0.0
    need = MIN_COMPLETE if info < STRONG_INFO else MIN_MONTHS
    by_tol = n_sup >= need and months_c >= MIN_MONTHS and share >= PASS_SHARE and not neg
    # the exact route: nothing left over, to float noise, wherever something is counted
    with np.errstate(invalid="ignore"):
        exact_tol = EXACT_REL * np.maximum(scale, 1e-300)
        ex_ok = complete & (np.abs(r) <= exact_tol) & (scale > 0)
        ex_off = complete & (np.abs(r) > exact_tol)
    n_ex, n_exoff = int(ex_ok.sum()), int(ex_off.sum())
    distinct = int(len(np.unique(np.round(tgt[ex_ok], 9)))) if n_ex else 0
    months_x = int(ex_ok.any(axis=0).sum())
    by_exact = (n_ex >= EXACT_MIN and n_ex >= PASS_SHARE * (n_ex + n_exoff) and distinct >= 3 and months_x >= MIN_MONTHS and not neg)
    ok_pass = bool(by_tol or by_exact)
    if ok_pass:
        status = "pass"
    elif neg or (n_vio >= FAIL_MIN and share < PASS_SHARE):
        status = "fail"
    else:
        status = "unresolved"
    out.update({"pass": ok_pass, "status": status, "complete": nc, "within": int(within.sum()), "months": months_c if by_tol or not by_exact
                else months_x, "incomplete": int(incomplete.sum()),
                "share": round(share if by_tol or not by_exact else float(n_ex) / max(1, n_ex + n_exoff), 4),
                "negative_unallocated": neg, "max_rel_residual": float(rel.max()) if len(rel) else None,
                "max_residual": float(np.abs(r[complete]).max()) if nc else None, "info": round(info, 1), "informative": n_inf,
                "support": n_sup, "violated": n_vio, "exact": n_ex, "by": "tolerance" if by_tol else ("exact" if by_exact else None)})
    if nc == 0 and int(incomplete.sum()) > 0 and not neg:
        out["bound_only"] = True
    return out


def _bounds(A: Any, x: int, y: int, tol: float) -> bool:
    """Member x bounds member y (y <= x + tol) in 99% of the cells where both have a value, and there are such cells."""
    import numpy as np
    both = ~np.isnan(A[x]) & ~np.isnan(A[y])
    n = int(both.sum())
    if n < MIN_COMPLETE:
        return False
    return float((A[y][both] <= A[x][both] + tol + 1e-9 * np.abs(A[x][both])).mean()) >= BOUND_SHARE


def _bounded_by(A: Any, x: int, tol: float) -> Any:
    """For every member at once: x bounds it in 99% of the cells where both have a value (6 or more such cells)."""
    import numpy as np
    ax = A[x]
    both = ~np.isnan(A) & ~np.isnan(ax)[None, :, :]
    n = both.reshape(A.shape[0], -1).sum(axis=1)
    with np.errstate(invalid="ignore"):
        under = (A <= ax[None, :, :] + tol + 1e-9 * np.abs(ax)[None, :, :]) & both
    k = under.reshape(A.shape[0], -1).sum(axis=1)
    out = (n >= MIN_COMPLETE) & (k >= BOUND_SHARE * np.maximum(n, 1))
    out[x] = False
    return out


def _dominance(A: Any) -> Any:
    """Each member's share of its cells where it is at least every other member."""
    import numpy as np
    with np.errstate(all="ignore"):
        mx = np.nanmax(np.where(np.isnan(A), -np.inf, A), axis=0)
    out = np.zeros(A.shape[0])
    for m in range(A.shape[0]):
        have = ~np.isnan(A[m])
        n = int(have.sum())
        if n:
            out[m] = float((A[m][have] >= mx[have] - 1e-9 * np.abs(mx[have])).mean())
    return out


def _nonneg(S: Dict[str, Any]) -> bool:
    """A flow, a count or a stock that is never negative in the file: its parts never exceed its total."""
    import numpy as np
    if S["measure"]["type"] not in ("flow", "count", "stock", "unknown"):
        return False
    V = S["_V"]
    for k, d in enumerate(S["dims"]):
        if d.get("measure_dim") and d.get("default_index") is not None:
            V = V[S["_SM"][:, k] == d["default_index"]]
    have = ~np.isnan(V)
    return bool(not have.any() or float(V[have].min()) >= 0)


def _is_geographic(column: str) -> bool:
    """Whether a dimension's header says its members are places (geo, region, province, state, territory ...: wave 5e adds the words
    the French, German and Spanish tables use). A header is a hint; it is the only reading of what the members ARE that the cells
    cannot give, and the evidence that a no-total dimension is a set of disjoint parts (_parts_only)."""
    return bool(_GEO_WORDS.search(str(column)))


def _relations(S: Dict[str, Any], j: int, tm: _Timer) -> None:
    """The dimension's role: flat partition, a coded hierarchy, a hierarchy found by subset sums, components and
    alternatives; else unresolved (rule 6 reads it).

    Wave 5e: a total is VERIFIED by a sum-check that could have failed (`_sum_check`), or it is NAMED (it says total, no check
    contradicts it, none could verify it: evidence "named", said so in every sentence that follows). A check that could not decide is
    neither: a candidate total it leaves undecided keeps the dimension from being read as a set of parts."""
    import numpy as np
    rec = S["dims"][j]
    labels = rec["labels"]
    A, X, _ctx = _dim_tensor(S, j)
    tol_u = _tol_unit(S, j)
    nonneg = _nonneg(S)
    dom = _dominance(A)
    M = len(labels)
    # the members whose NAMES nominate them as alternative totals ("excluding ...", "less ...", "without ...", "other than ..."): they
    # stay candidates for parts until the sums decide (P4)
    nominated = {m for m in range(M) if _is_alt(labels[m])}
    hint = [m for m in range(M) if (_says_total(labels[m]) or (_label_code(labels[m]) or "").count("-") == 1
                                    and _RANGE.match(_label_code(labels[m]) or "")) and m not in nominated]
    order = list(np.argsort(-dom, kind="stable"))
    # wave 5d: a whole country's name in a geographic dimension (Canada beside its provinces) is a total's name too: tried as the
    # total, and never added to the parts when no check could verify it (wave 5e: not when the column holds countries)
    whole = [m for m in range(M) if m not in hint and m not in nominated and _agg_name_tier(labels[m]) == 1
             and _is_geographic(rec["column"]) and not _COUNTRY_COLUMN.search(rec["column"])]
    # 1. FLAT: the top 3 by dominance and any name-hinted member, against every other member. Two readings of the members whose names
    # nominate them as alternatives: left out of the parts, or parts; the sums decide which (or neither)
    undecided: List[int] = []
    for t in list(dict.fromkeys([int(x) for x in order[:3]] + hint + whole)):
        variants = [[m for m in range(M) if m != t and m not in nominated]]
        if nominated - {t}:
            variants.append([m for m in range(M) if m != t])
        done: List[Tuple[List[int], Dict[str, Any]]] = []
        for P in variants:
            if len(P) < 1:
                continue
            chk = _sum_check(A, X, t, P, tol_u, nonneg)
            done.append((P, chk))
            if chk["pass"]:
                _set_partition(S, j, rec, A, tol_u, nonneg, nominated, t, P, chk, "verified")
                return
        if not done:
            continue
        P0, chk0 = done[0]
        if chk0["status"] == "unresolved":
            if t in hint:
                # named as the total, nothing contradicts it, nothing could check it (no complete cell, or magnitudes as small as the
                # rounding): the member is shown as the named total and never said to add up
                _set_partition(S, j, rec, A, tol_u, nonneg, nominated, t, P0, chk0, "named")
                return
            undecided.append(t)
    rec["undecided_totals"] = [labels[t] for t in undecided]
    if M > MAX_MEMBERS:
        # a series key with more members than the relation search reads (NAICS at 6 digits, HS codes): the flat check above (linear) is
        # all that is searched, and the reason says so (wave 5e, P11); one member is shown, or the table is refused by its caller
        rec["role"] = "unresolved"
        rec["why"] = "more than %d members in %s; totals were not searched beyond the flat check" % (MAX_MEMBERS, rec["column"])
        return
    # 2. HIERARCHY FROM CODES
    codes = {m: _label_code(labels[m]) for m in range(M)}
    if sum(1 for c in codes.values() if c) >= max(3, int(0.5 * M)):
        if _coded_hierarchy(S, rec, A, X, codes, tol_u, nonneg, nominated, dom):
            return
        if nominated and _coded_hierarchy(S, rec, A, X, codes, tol_u, nonneg, set(), dom):
            return
    # 3. HIERARCHY WITHOUT CODES
    if _codefree_hierarchy(S, rec, A, X, tol_u, nonneg, nominated, dom, tm):
        return
    if nominated and _codefree_hierarchy(S, rec, A, X, tol_u, nonneg, set(), dom, tm):
        return
    # 4. components: one member bounds every other (total sales and e-commerce sales), under a total's name or in a
    # dimension that names measures; a plain dimension of an official table (13 provinces, none a total) is not read so: the
    # largest province bounds the others without being their parent (wave 5, gap 1)
    top = int(order[0])
    others = [m for m in range(M) if m != top]
    if others and (top in hint or _measure_dim_name(rec["column"], labels)) and \
            all(_bounds(A, top, m, tol_u) for m in others):
        name = rec["column"]
        role = "measure" if _measure_dim_name(name, labels) else "components"
        rec.update(role=role, total=labels[top], total_index=top, components={labels[m]: labels[top] for m in others},
                   alternatives={}, why="%s bounds every other member everywhere and is not their sum" % labels[top])
        return
    # 5. NO TOTAL ROW (wave 5, gap 1): no member is the total of the others and none stands as their parent. A combined
    # member (one that equals the sum of 2 or more others) is left out; the rest are the parts, and a flow's headline is
    # their sum (wave 5e: only where the dimension is positively a set of disjoint parts). Any other measure has no valid aggregate:
    # one member is read (rule 6)
    if _parts_only(S, rec, A, X, tol_u, nonneg, nominated, dom, hint + whole, tm):
        return
    # an official table whose largest member bounds the others and whose measure cannot be added (a stock, a rate): one
    # member shown, as rule 6 reads it
    rec["role"] = "unresolved"
    rec["why"] = rec.pop("twin_why", None) or "no member is the sum of others (sum-checks failed)"


def _set_partition(S: Dict[str, Any], j: int, rec: Dict[str, Any], A: Any, tol_u: float, nonneg: bool, nominated: Set[int], t: int,
                   P: List[int], chk: Dict[str, Any], evidence: str) -> None:
    """Record member t as the total of P: evidence "verified" (a sum-check that could have failed passed) or "named" (it says total
    and nothing could check it)."""
    labels = rec["labels"]
    rec.update(role="partition", total=labels[t], total_index=t, parts=[labels[p] for p in P],
               part_index=P, tree={t: P}, depth={t: 0, **{p: 1 for p in P}},
               sum_check=dict(_public_check(chk), verified=(evidence == "verified")), evidence=evidence,
               components={}, alternatives={})
    _alternatives(S, rec, A, tol_u, nominated)
    _sa_record(S, j, rec, tol_u, nonneg)


def _parts_only(S: Dict[str, Any], rec: Dict[str, Any], A: Any, X: Any, tol_u: float, nonneg: bool, nominated: Set[int],
                dom: Any, hint: List[int], tm: _Timer) -> bool:
    """The dimension of an official table whose members are all parts: no total row, no hierarchy found. Wave 5e (P1): members are
    ADDED only with positive evidence that they are disjoint parts. "No relation was found" is never that evidence. It takes ALL of:

      * the measure is a flow (a stock, a rate, an index or an ambiguous count has no valid sum across members);
      * the dimension is geographic (its header names places) and has at least 3 members that are parts: two members are a pair
        (a total and a part, a copy and its original), three or more that stand in no relation are a set of regions;
      * no member that could be the total was left undecided: each of the three most dominant members is the sum of the others
        decidedly NOT (a check with the power to fail, which failed): a total hidden by small magnitudes or heavy suppression is
        never added to its parts;
      * the search for combined members (a member that equals the sum of 2 or more others) was complete: every parent searched, every
        candidate covered, the search never cut off ("unresolved" is not "none found", P2); the combined members, a member identical
        to an earlier one, and a member inside a coded parent that is also listed (459993 inside 459) are left out;
      * no two members of what remains are one quantity twice, by SHAPE (P5): the same movement under a stable level ratio
        (a seasonally adjusted copy, current and chained dollars), whatever their annual totals;
      * no member that says it leaves something out ("excluding ...") and is not a verified combined member remains.

    Else the dimension is read one member at a time (rule 6), and says so."""
    import numpy as np
    if not S["official"] or not sums_over_time(S["measure"]) or hint:
        return False
    labels = rec["labels"]
    M = len(labels)
    if M < 2 or _measure_dim_name(rec["column"], labels):
        return False
    cand = list(range(M))
    if not _is_geographic(rec["column"]):
        rec["twin_why"] = ("%s is not a dimension of places and no member is a total of the others: nothing shows that its members are "
                           "disjoint parts, so one member is shown and the members are never added" % rec["column"])
        return False
    # a total that could not be told from a part: every top candidate must be decidedly not the sum of the others
    for t in sorted(cand, key=lambda m: (-float(dom[m]), m))[:3]:
        chk = _sum_check(A, X, t, [m for m in cand if m != t], tol_u, nonneg)
        if chk["status"] != "fail":
            rec["twin_why"] = ("%s may be the total of the other members (%s), so it is never one of their parts" % (
                labels[t], "equals their sum in every cell that can be checked (%d)" % chk["complete"] if chk["pass"] else
                "the check could not decide: the table's magnitudes are as small as its rounding, or too few cells are complete"))
            return False
    sizes = np.array([float(np.nanmean(A[m])) if (~np.isnan(A[m])).any() else 0.0 for m in range(M)])
    order = [m for m in np.argsort(-dom, kind="stable").tolist() if m in cand]
    order = sorted(order, key=lambda m: (-sizes[m], m))
    combined: Dict[int, List[int]] = {}
    dup: Dict[int, int] = {}
    unresolved = ""
    if len(order) > PARENTS_MAX:
        unresolved = "more than %d members: combined members were not searched for among all of them" % PARENTS_MAX
    for p in order[:PARENTS_MAX]:
        if p in dup or p in combined:
            continue
        bnd = _bounded_by(A, p, tol_u)
        cs = [m for m in cand if m != p and bnd[m] and m not in dup]
        cs = sorted(cs, key=lambda m: (-sizes[m], m))
        if len(cs) > CANDIDATES_MAX:
            unresolved = unresolved or "%s bounds more than %d members: a combined member could be among them" % (labels[p], CANDIDATES_MAX)
            cs = cs[:CANDIDATES_MAX]
        if not cs:
            continue
        sol = _subset_partition(A, X, p, cs, tol_u, nonneg)
        if sol["status"] == "unresolved":
            unresolved = unresolved or "%s could not be searched for a combined member (%s)" % (labels[p], sol.get("why"))
            continue
        if sol["status"] == "none":
            continue
        fam = sol["fam"]
        if len(fam) == 1:
            dup[max(p, fam[0])] = min(p, fam[0])
        else:
            combined[p] = fam
    if unresolved:
        rec["twin_why"] = "no member is a total of the others, but the search for combined members did not finish: " + unresolved
        return False
    removed = set(combined) | set(dup)
    keep = [m for m in cand if m not in removed]
    # a member inside a coded parent that is also listed (the parent is bigger everywhere): never added to it
    codes = {m: _label_code(labels[m]) for m in keep}
    nested: Dict[int, int] = {}
    for m in keep:
        c = codes[m]
        if not c:
            continue
        anc = [o for o in keep if o != m and codes[o] and _contains(codes[o], c) and _bounds(A, o, m, tol_u)]
        if anc:
            nested[m] = max(anc, key=lambda o: _spec(codes[o]))
    keep = [m for m in keep if m not in nested]
    if len(keep) < GEO_MIN_PARTS:
        rec["twin_why"] = ("%s holds only %d members that are parts: two are a pair (a total and a part, a copy and its original), "
                           "not a set of regions, so one member is shown" % (rec["column"], len(keep)))
        return False
    leaving = [m for m in keep if _is_strong_alt(labels[m])]
    if leaving:
        rec["twin_why"] = ("%s says it leaves something out and is not a sum of the other members: it may contain them, so the "
                           "members are never added" % labels[leaving[0]])
        return False
    copy = _copies_by_shape(S, A, keep)
    if copy is not None:
        a, b = copy
        rec["twin_why"] = ("%s and %s move together under a steady ratio: one quantity twice (an adjusted copy, another price basis), "
                           "so they are never added" % (labels[a], labels[b]) if a is not None else
                           "the members' movements could not be compared (the table is too short to tell a copy from a part), so "
                           "they are never added")
        return False
    rec.update(role="parts", total=None, total_index=None, parts=[labels[m] for m in keep], part_index=keep,
               combined={labels[p]: [labels[x] for x in sorted(f)] for p, f in sorted(combined.items())},
               duplicates={labels[d]: labels[k] for d, k in sorted(dup.items())},
               nested={labels[m]: labels[o] for m, o in sorted(nested.items())},
               alternatives={}, components={}, evidence="built",
               sum_check=None, noun="regions",
               why="no member is a total of the others: the table has no total row, so a flow's headline is the sum of its "
                   "%d parts" % len(keep))
    return True


def _sa_record(S: Dict[str, Any], j: int, rec: Dict[str, Any], tol_u: float, nonneg: bool) -> None:
    """Whether the adjusted copies of a partition's parts add up to the adjusted total (StatCan's retail SA does; parts
    adjusted one by one do not): recorded, never used to choose the headline."""
    if not any(d.get("role") == "adjustment" for k, d in enumerate(S["dims"]) if k != j):
        return
    A, X, _c = _dim_tensor(S, j, adjusted=True)
    t, P = rec["total_index"], list(rec.get("part_index") or [])
    chk = _sum_check(A, X, t, P, tol_u, nonneg)
    if chk["complete"] >= MIN_COMPLETE:
        rec["sa_adds_up"] = bool(chk["pass"])
        rec["sa_check"] = _public_check(chk)


def _measure_dim_name(name: str, labels: List[str]) -> bool:
    try:
        from northledger.measure import additive_kind
    except ImportError:                                        # the engine is always there in a run
        def additive_kind(_n: Any) -> str:
            return ""
    words = re.findall(r"[a-z]+", name.lower())
    if set(words) & {"sales", "measure", "measures", "indicator", "indicators", "statistics", "variable", "concept",
                     "estimates", "series"}:
        return True
    return sum(1 for lb in labels if additive_kind(lb)) >= max(1, len(labels) // 2)


def _public_check(chk: Dict[str, Any]) -> Dict[str, Any]:
    return {k: chk.get(k) for k in ("complete", "within", "months", "incomplete", "share", "max_rel_residual",
                                    "max_residual", "informative", "by")}


def _diff_members(A: Any, V: Any, order: List[int], ok_fp: Any, fp: Any, diff: Any, tol_u: float) -> Optional[List[int]]:
    """One or two of the members `order` (rows of V, [member, cell]) that sum to `diff` (the total less a leftover member), verified on
    every cell as `_close_cells` does; None when no member or pair does. A single member is looked for over every member at once; a
    pair by a SORTED LOOKUP on a 3-cell fingerprint (O(M log M), not O(M^2)): for each first member, the second is searched in the
    members' sorted values where the fingerprint says it must be, and only those matches are verified (at most 50)."""
    import numpy as np
    d = diff.reshape(-1)
    live = ~np.isnan(d)
    if int(live.sum()) < MIN_COMPLETE:
        return None
    tol1 = np.maximum(tol_u * 2, 1e-6 * np.abs(d))
    with np.errstate(invalid="ignore"):
        both = live[None, :] & ~np.isnan(V)
        near = both & (np.abs(V - d[None, :]) <= tol1[None, :])
    n = both.sum(axis=1)
    hit = np.flatnonzero((n >= MIN_COMPLETE) & (near.sum(axis=1) >= PASS_SHARE * np.maximum(n, 1)))
    if len(hit):
        return [order[int(hit[0])]]
    if fp is None or not len(ok_fp):
        return None
    tgt = d[fp]
    if np.isnan(tgt).any():
        return None
    tol3 = np.maximum(tol_u * 3, 1e-6 * np.abs(tgt))
    cand = ok_fp                                                   # rows of V whose fingerprint cells all hold a value
    f0 = V[cand, fp[0]]
    srt = np.argsort(f0, kind="stable")
    f0s = f0[srt]
    tried = 0
    for ia, a in enumerate(cand):
        need0 = tgt[0] - V[a, fp[0]]
        lo = np.searchsorted(f0s, need0 - tol3[0], "left")
        hi = np.searchsorted(f0s, need0 + tol3[0], "right")
        for kk in range(lo, hi):
            b = cand[srt[kk]]
            if b <= a:
                continue
            if np.all(np.abs(V[a, fp] + V[b, fp] - tgt) <= tol3):
                tried += 1
                if tried > 50:
                    return None
                if _close_cells(diff, (V[a] + V[b]).reshape(diff.shape), tol_u * 3):
                    return [order[int(a)], order[int(b)]]
    return None


def _alternatives(S: Dict[str, Any], rec: Dict[str, Any], A: Any, tol_u: float, nominated: Set[int]) -> None:
    """Members left out of the tree: an alternative total (the total less one or two tree members, or named "excluding ..." and bounded
    by the total), else a component of the smallest verified member that bounds it everywhere. Bounded by counts (wave 5e, P11):
    at most LEFT_MAX leftover members are searched, each by a lookup that is linear in the members, never quadratic."""
    import numpy as np
    labels = rec["labels"]
    placed = set(rec.get("depth") or {})
    left = [m for m in range(len(labels)) if m not in placed]
    if not left:
        return
    t = rec.get("total_index")
    tree_members = sorted(placed)
    cand = [m for m in tree_members if m != t]
    V = fp = ok_fp = None
    for n_done, x in enumerate(left):
        if n_done >= LEFT_MAX:
            break                                   # past the count budget a leftover is left as it is: a member with no relation
        found = False
        if t is not None and _bounds(A, t, x, tol_u):
            diff = A[t] - A[x]
            if V is None and cand:
                V = np.stack([A[a].reshape(-1) for a in cand])
                cnt = (~np.isnan(V)).sum(axis=0)
                best = np.argsort(-cnt, kind="stable")[:3]
                fp = np.sort(best) if len(best) == 3 else None
                ok_fp = np.flatnonzero(~np.isnan(V[:, fp]).any(axis=1)) if fp is not None else np.array([], dtype=int)
            hit = _diff_members(A, V, cand, ok_fp, fp, diff, tol_u) if V is not None else None
            if hit:
                names = [labels[a] for a in hit]
                rec["alternatives"][labels[x]] = "%s less %s" % (labels[t], " and ".join(names))
                found = True
            elif x in nominated:
                rec["alternatives"][labels[x]] = labels[t]
                found = True
        if found:
            continue
        bounders = [m for m in tree_members if _bounds(A, m, x, tol_u)]
        cx = _label_code(labels[x])
        if cx:
            # a coded leftover: only its code-ancestor's own subtree (459993 under 459: 459, 459A or 459B)
            codes = {m: _label_code(labels[m]) for m in tree_members}
            anc = [m for m in tree_members if codes[m] and _contains(codes[m], cx)]
            if anc:
                near = max(anc, key=lambda m: _spec(codes[m]))
                sub = _subtree(rec, near)
                bounders = [m for m in bounders if m in sub]
        if bounders:
            sizes = {m: float(np.nanmean(A[m])) for m in bounders}
            before = [m for m in bounders if m < x and m != t]
            if cx and before:
                # the publisher's own outline: the nearest member before it (459B lists Cannabis retailers right after it)
                parent = max(before)
            else:
                parent = min(bounders, key=lambda m: (sizes[m], m))
            rec["components"][labels[x]] = labels[parent]


def _subtree(rec: Dict[str, Any], root: int) -> Set[int]:
    tree = rec.get("tree") or {}
    out, q = {root}, [root]
    while q:
        p = q.pop()
        for c in tree.get(p, []):
            if c not in out:
                out.add(c)
                q.append(c)
    return out


def _close_cells(a: Any, b: Any, tol: float) -> bool:
    import numpy as np
    both = ~np.isnan(a) & ~np.isnan(b)
    n = int(both.sum())
    if n < MIN_COMPLETE:
        return False
    return float((np.abs(a[both] - b[both]) <= np.maximum(tol, 1e-6 * np.abs(a[both]))).mean()) >= PASS_SHARE


def _coded_hierarchy(S: Dict[str, Any], rec: Dict[str, Any], A: Any, X: Any, codes: Dict[int, Optional[str]],
                     tol_u: float, nonneg: bool, alts_label: Set[int], dom: Any) -> bool:
    """A hierarchy from the members' codes: each member's parent is the most specific code that contains it; each
    family is verified by its sum-check, and a failing family is repaired by dropping its deepest-coded members (459 =
    459A + 459B, without 459993); a member dropped becomes a component of the smallest verified member that bounds it."""
    labels = rec["labels"]
    M = len(labels)
    coded = [m for m in range(M) if codes[m] and m not in alts_label]
    parent: Dict[int, Optional[int]] = {}
    for m in coded:
        best, bs = None, -1
        for p in coded:
            if p != m and _contains(codes[p], codes[m]) and _spec(codes[p]) > bs:
                best, bs = p, _spec(codes[p])
        parent[m] = best
    roots = [m for m in coded if parent[m] is None]
    uncoded = [m for m in range(M) if not codes[m] and m not in alts_label]
    root = None
    if len(roots) == 1:
        root = roots[0]
    elif uncoded:
        cand = sorted(uncoded, key=lambda m: (-dom[m], m))
        top = cand[0]
        if _says_total(labels[top]) or dom[top] >= 0.99:
            root = top
            for r in roots:
                parent[r] = top
            parent[top] = None
    if root is None:
        return False
    children: Dict[int, List[int]] = {}
    for m, p in parent.items():
        if p is not None:
            children.setdefault(p, []).append(m)
    tree: Dict[int, List[int]] = {}
    checks: Dict[int, Dict[str, Any]] = {}
    dropped: List[int] = []
    for p in sorted(children, key=lambda x: _spec(codes[x]) if codes[x] else -1):
        fam = sorted(children[p])
        chk = _sum_check(A, X, p, fam, tol_u, nonneg)
        while not chk["pass"] and len(fam) > 1:
            deepest = max(_spec(codes[m]) for m in fam)
            keep = [m for m in fam if _spec(codes[m]) < deepest]
            if not keep or keep == fam:
                break
            dropped += [m for m in fam if m not in keep]
            fam = keep
            chk = _sum_check(A, X, p, fam, tol_u, nonneg)
        if chk["pass"]:
            tree[p] = fam
            checks[p] = _public_check(chk)
        else:
            checks[p] = dict(_public_check(chk), failed=True)
    if root not in tree:
        return False
    # the verified tree from the root down
    depth = {root: 0}
    queue = [root]
    while queue:
        p = queue.pop(0)
        for c in tree.get(p, []):
            if c not in depth:
                depth[c] = depth[p] + 1
                queue.append(c)
    tree = {p: ch for p, ch in tree.items() if p in depth}
    rec.update(role="hierarchy" if any(depth[p] >= 1 for p in tree) else "partition", total=labels[root],
               total_index=root, tree=tree, depth=depth, parts=[labels[c] for c in tree[root]], part_index=tree[root],
               sum_check=dict(checks[root], verified=True), family_checks={labels[p]: checks[p] for p in tree}, components={},
               alternatives={}, by="codes", evidence="verified")
    _alternatives(S, rec, A, tol_u, alts_label)
    return True


def _codefree_hierarchy(S: Dict[str, Any], rec: Dict[str, Any], A: Any, X: Any, tol_u: float, nonneg: bool,
                        alts_label: Set[int], dom: Any, tm: Optional[_Timer] = None) -> bool:
    """The hierarchy found by subset sums: for each parent (the most dominant first, at most 30), the members it bounds
    (at most 22, the largest), a meet-in-the-middle subset sum on a 3-cell fingerprint, each match verified on every
    cell; the coarsest verified partition gives its children, and each child is searched in turn. Bounded by counts only."""
    import numpy as np
    labels = rec["labels"]
    M = len(labels)
    order = [int(x) for x in np.argsort(-dom, kind="stable") if int(x) not in alts_label]
    if not order:
        return False
    root = order[0]
    tree: Dict[int, List[int]] = {}
    checks: Dict[int, Dict[str, Any]] = {}
    assigned = {root}
    queue = [root]
    done = 0
    sizes = np.array([float(np.nanmean(A[m])) if (~np.isnan(A[m])).any() else 0.0 for m in range(M)])
    while queue and done < PARENTS_MAX:
        p = queue.pop(0)
        done += 1
        bnd = _bounded_by(A, p, tol_u)
        cands = [m for m in range(M) if m not in assigned and m not in alts_label and bnd[m]]
        if not cands:
            continue
        cands = sorted(cands, key=lambda m: (-sizes[m], m))[:CANDIDATES_MAX]
        sol = _subset_partition(A, X, p, cands, tol_u, nonneg, min_parts=2)
        if sol["status"] != "found":
            continue
        fam, chk = sol["fam"], sol["chk"]
        tree[p] = fam
        checks[p] = _public_check(chk)
        for c in fam:
            assigned.add(c)
        queue.extend(sorted(fam, key=lambda m: (-sizes[m], m)))
    if root not in tree:
        return False
    depth = {root: 0}
    q = [root]
    while q:
        p = q.pop(0)
        for c in tree.get(p, []):
            if c not in depth:
                depth[c] = depth[p] + 1
                q.append(c)
    trial = dict(rec)
    trial.update(role="hierarchy" if any(depth[p] >= 1 for p in tree) else "partition", total=labels[root],
                 total_index=root, tree=tree, depth=depth, parts=[labels[c] for c in tree[root]], part_index=tree[root],
                 sum_check=dict(checks[root], verified=True), family_checks={labels[p]: checks[p] for p in tree}, components={},
                 alternatives={}, by="subset sums", evidence="verified")
    _alternatives(S, trial, A, tol_u, alts_label)
    # a root that leaves several members unexplained is a SUBTOTAL, not the table's total (13 provinces and a Prairies
    # group, no Canada row): a member left over is explained when it is an alternative total or a component of one tree
    # member, and a component found only by bounding (no code says so) is weak evidence, so only a few are taken
    # (wave 5, gap 1). A table that fails this has no total row: its parts are added up (_parts_only)
    # wave 5d: a "component" that is an exact copy of the member it sits under (a single-child industry equal to its parent) is no
    # weak evidence of anything: only the components found by bounding alone are counted against the limit
    weak = [x for x, parent in (trial.get("components") or {}).items()
            if x in labels and parent in labels and not _close_cells(A[labels.index(x)], A[labels.index(parent)], tol_u * 2)]
    if len(weak) > max(1, M // 6):
        return False
    # wave 5d: a member left over is a component of the ROOT itself only when the root says it is the total. Found by bounding
    # alone, with no member below the root that could hold it, it is as likely a sibling the root leaves out: a combined member
    # (Inland provinces = two of the three regions) is a subtotal, and the third region a part of the table, not a component
    # of it. An unnamed root with such a leftover is no total: the dimension is read by its parts (_parts_only)
    root_label = labels[root]
    if not _says_total(root_label) and _agg_name_tier(root_label) != 2:
        explained = set(trial.get("depth") or {}) | {labels.index(x) for x in (trial.get("alternatives") or {}) if x in labels} | \
            {labels.index(x) for x in (trial.get("components") or {}) if x in labels} | set(alts_label)
        # a copy of a member the root explains (a single-child industry equal to its parent) is explained too
        for x in range(M):
            if x not in explained and any(y != x and _close_cells(A[x], A[y], tol_u * 2) for y in list(explained)):
                explained.add(x)
        # ... and it explains every member: each is under it, an alternative total, or a component of a member below it. A member
        # the root leaves unexplained (a region that is neither inside it nor a copy of it) makes the root a subtotal; and in a
        # geographic dimension nothing is a component by bounding: a region smaller than another is a region, not a part of it
        if (any(parent == root_label for parent in (trial.get("components") or {}).values()) or len(explained) < M
                or (_is_geographic(rec["column"]) and trial.get("components"))):
            return False
    rec.update(trial)
    return True


def _fingerprint_groups(A: Any, p: int, cands: List[int]) -> Tuple[List[Tuple[List[int], List[int]]], bool]:
    """Where to read a parent's fingerprint (wave 5e, P2): groups of 3 cells where the PARENT has a value, and the candidates that have
    one in all three. Parts that report in different periods (one stops in month 21, another starts in month 29) are never all present
    in one cell, so a single group of cells where everything is present does not exist; each group covers the candidates present in
    its three cells, and the next group the candidates not covered yet (at most FP_GROUPS_MAX). (the groups: [(cells, candidates)],
    whether every candidate was covered by some group)."""
    import numpy as np
    pa = A[p].reshape(-1)
    live = np.flatnonzero(~np.isnan(pa))
    if len(live) < 3:
        return [], False
    pres = np.stack([~np.isnan(A[c].reshape(-1))[live] for c in cands])             # k x live
    uncovered = set(range(len(cands)))
    groups: List[Tuple[List[int], List[int]]] = []
    while uncovered and len(groups) < FP_GROUPS_MAX:
        cur = np.array(sorted(uncovered))
        chosen: List[int] = []
        have = np.ones(len(cands), dtype=bool)
        have[:] = False
        have[cur] = True
        for _ in range(3):
            score = pres[have].sum(axis=0).astype(float)
            score[chosen] = -1.0
            # ties go to the cell the earliest, middle and latest of the parent's: a spread, not three neighbours
            order = np.argsort(-score, kind="stable")
            pick = int(order[0]) if score[order[0]] > 0 else -1
            if pick < 0:
                break
            if chosen and len(chosen) < 3:
                top = [int(x) for x in order if score[int(x)] == score[pick]]
                pick = top[len(top) // 2] if len(chosen) == 1 else top[-1]
            chosen.append(pick)
            have = have & pres[:, pick]
        if len(chosen) < 3 or not have.any():
            break
        members = sorted(int(i) for i in np.flatnonzero(pres[:, chosen].all(axis=1)))
        groups.append(([int(live[c]) for c in chosen], members))
        uncovered -= set(members)
    return groups, not uncovered


def _subset_partition(A: Any, X: Any, p: int, cands: List[int], tol_u: float, nonneg: bool, min_parts: int = 1
                      ) -> Dict[str, Any]:
    """The coarsest verified subset of `cands` that sums to the parent: {"status": "found", "fam", "chk"}, {"status": "none"} (every
    candidate was searched and none sums to it) or {"status": "unresolved", "why"} (the search could not run or could not decide:
    fewer than 3 cells where the parent has a value, a candidate that no 3 cells cover, a match that no check had the power to
    verify, a search cut off by its COUNT budget). "unresolved" is never "none found" (wave 5e, P2). A hierarchy needs
    min_parts=2: a parent that equals ONE member (within a rounding unit) is a copy of it, not a partition, and a table of
    near-equal series is not a hierarchy; the no-total search takes a single member as the copy it is (a duplicate).
    Bounded by counts only (PAIRS_MAX, MATCHES_MAX, VERIFY_MAX, FP_GROUPS_MAX): the result is the same on any machine."""
    import numpy as np
    if not cands:
        return {"status": "none"}
    groups, covered_all = _fingerprint_groups(A, p, cands)
    if not groups:
        return {"status": "unresolved", "why": "the parent has too few cells where its parts can be read together"}
    pflat = A[p].reshape(-1)
    seen_fam = set()
    fams: List[List[int]] = []
    cut = False
    pairs_done = 0
    for cells, members in groups:
        cs = [cands[i] for i in members]
        k = len(cs)
        pick = np.array(cells)
        tgt = pflat[pick]
        vals = np.stack([A[c].reshape(-1)[pick] for c in cs])          # k x 3
        tol = np.maximum(tol_u * (k + 1), 1e-6 * np.abs(tgt))
        h = k // 2
        left, right = vals[:h], vals[h:]

        def sums(v: Any) -> Tuple[Any, Any]:
            s_ = np.zeros((1, 3))
            masks = np.zeros(1, dtype=np.int64)
            for i in range(v.shape[0]):
                s_ = np.concatenate([s_, s_ + v[i]])
                masks = np.concatenate([masks, masks | (1 << i)])
            return s_, masks
        sl, ml = sums(left)
        sr, mr = sums(right)
        o = np.argsort(sr[:, 0], kind="stable")
        sr, mr = sr[o], mr[o]
        need = tgt[0] - sl[:, 0]
        lo = np.searchsorted(sr[:, 0], need - tol[0], "left")
        hi = np.searchsorted(sr[:, 0], need + tol[0], "right")
        for i in np.flatnonzero(hi > lo):
            for jj in range(lo[i], hi[i]):
                pairs_done += 1
                tot = sl[i] + sr[jj]
                if np.all(np.abs(tot - tgt) <= tol):
                    members_ = [cs[b] for b in range(h) if ml[i] >> b & 1] + [cs[h + b] for b in range(k - h) if mr[jj] >> b & 1]
                    key = tuple(sorted(members_))
                    if members_ and key not in seen_fam:
                        seen_fam.add(key)
                        fams.append(sorted(members_))
            if len(fams) > MATCHES_MAX or pairs_done > PAIRS_MAX:
                cut = True
                break
        if cut:
            break
    best = None
    undecided = False
    for n, fam in enumerate(sorted(fams, key=lambda f: (len(f), f))):
        if len(fam) < min_parts:
            continue
        if n >= VERIFY_MAX:
            cut = True
            break
        chk = _sum_check(A, X, p, fam, tol_u, nonneg)
        if chk["pass"]:
            best = (fam, chk)
            break
        if chk["status"] == "unresolved":
            undecided = True
    if best is not None:
        return {"status": "found", "fam": best[0], "chk": best[1]}
    if cut or undecided or not covered_all:
        return {"status": "unresolved", "why": "the search was cut off by its count budget" if cut else
                ("a match could not be checked (the table's magnitudes are as small as its rounding)" if undecided else
                 "a part is blank where the others are present, so it could not be searched with them")}
    return {"status": "none"}


# wave 5b: a rate or an index has a published aggregate only when something other than its place in the file says so. The names
# below are hints (a total's words, a whole country's name as the table of its provinces or states lists it); with no such name
# the member must be shown to be the others' weighted average (_unnamed_aggregate), else the table has NO aggregate and one
# member is shown by dominance, never as the national figure.
_AGG_TOTAL_WORD = re.compile(r"(?i)(?:^|\b)(?:total|all|overall|grand|aggregate|combined|national|nationwide|ensemble|tous|toutes|insgesamt|gesamt|"
                             r"alle|todos|todas)(?:\b|$)|"
                             r"^\s*(?:_T|TOTAL|_Z)\s*$")
_AGG_WHOLE = re.compile(r"(?i)^\s*(?:canada|united states(?: of america)?|u\.?s\.?a?\.?|united kingdom|u\.?k\.?|great britain|"
                        r"australia|new zealand|euro(?:pean)? (?:area|union|zone)|eu\s?-?\s?\d{2}(?:_\d{4})?|oecd|world)"
                        r"\s*(?:\(\s*\d+\s+(?:countries|member states)\s*\))?\s*$")
AGG_MIN_MEMBERS = 4             # an unnamed aggregate needs at least 3 parts beside it to be told from a member
AGG_MAX_MEMBERS = 40            # ... and at most this many members (the fit is run for each)
AGG_MIN_FIT_CELLS = 12          # complete cells (every member has a value) the weighted-average fit is made on
AGG_FIT_ROWS = 600              # at most this many cells are fitted (evenly spaced)
AGG_TRIES = 5                   # the candidates fitted: the 5 whose relation to the others' mean is the most stable (a true aggregate is among them)
AGG_PEERS = 8                   # the typical member's own fit is the median of at most this many members' fits
AGG_FIT_UNITS = 1.0             # the fit's RMS residual: at most 1 unit of the last published digit ...
AGG_PEER_UNITS = 3.0            # ... while a typical member's own fit is at least 3 units off (else the table cannot tell) ...
AGG_PEER_RATIO = 0.25           # ... and the member's fit is at most a quarter of a typical member's
AGG_MIN_WEIGHT = 0.01           # wave 5d: ... and every other member carries at least 1% of its weight (else it averages a part of the table) ...
AGG_SMALL_TABLE = 12            # ... in a table of at most this many members; in a larger one ...
AGG_SUPPORT_SHARE = 0.5         # ... the members carrying 95% of the weight are at least this share of them


_BRACKETS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_COUNTRY_COLUMN = re.compile(r"(?i)\b(?:country|countries|nation|nations|pays|land|l\u00e4nder|pa[i\u00ed]s|pa[i\u00ed]ses)\b")


def _outside_brackets(label: Any) -> str:
    """A label without its bracketed words: they define a member ("... (except convenience retailers) [44511]") and say nothing."""
    return _BRACKETS.sub(" ", str(label))


def _is_rest(label: Any) -> bool:
    """Whether a name says the rest of something ("All other provinces", "Rest of Canada", "Autres provinces"): never a whole."""
    return bool(_REST_NAME.search(_outside_brackets(label)))


def _agg_name_tier(label: str) -> int:
    """2 when the name says total (total, all, overall, national, _T ...), 1 when it is a whole country's name as the table of
    its provinces or states lists it (Canada, United States, Great Britain, Euro area ...), 0 otherwise; an alternative
    ("excluding ...") and the rest of something ("All other provinces") are never an aggregate (wave 5e: in every branch)."""
    if _is_alt(label) or _is_rest(label):
        return 0
    if _AGG_TOTAL_WORD.search(label):
        return 2
    return 1 if _AGG_WHOLE.match(label) else 0


def _is_alt(label: str) -> bool:
    """Whether a member's name NOMINATES it as an alternative total ("Total excl. Seasonal shops", "Retail trade excluding gasoline"):
    never the total by its words (words inside brackets define a member and say nothing). A nomination only: "Less than 15 years" is a
    part, and what decides is the sum-check (wave 5e, P4)."""
    return bool(_ALT_HINT.search(_outside_brackets(label)))


def _is_strong_alt(label: str) -> bool:
    """A nomination by a word that says it leaves something out (excluding, except, net of ...); "less", "without", "other than" are
    ordinary words of a part's name and are weak."""
    return bool(_ALT_STRONG_RE.search(_outside_brackets(label)))


def _says_total(label: str) -> bool:
    """A total's words ("Total", "All", "Overall" ...) in a name that does not say it leaves something out (wave 5d: "Total except
    Footwear and Grocery" is an alternative, and was read as the table's total by its first word) and is not the rest of something
    (wave 5e: "All other provinces")."""
    return bool(_TOTAL_HINT.search(label)) and not _is_alt(label) and not _is_rest(label)


def _coded_totals(labels: Sequence[str]) -> Set[int]:
    """The members whose code is a RANGE that holds the codes of at least two other members ("Full range [11-41]" beside [11], [21], [31]
    and [41]): the way a publisher marks a total whatever the member is called."""
    codes = [_label_code(lb) for lb in labels]
    out: Set[int] = set()
    for m, c in enumerate(codes):
        if c and _RANGE.match(c) and sum(1 for x, cx in enumerate(codes) if x != m and cx and _contains(c, cx)) >= 2:
            out.add(m)
    return out


def _named_unverified(d: Dict[str, Any]) -> bool:
    """A dimension whose published aggregate is one by its name alone (wave 5c): no range or fit could verify it."""
    return d.get("role") == "rate_aggregate" and (d.get("sum_check") or {}).get("verified") is False


def named_total_words(mtype: str) -> str:
    """What the engine says of a member of a rate or an index that is the table's aggregate by its name alone (wave 5c)."""
    return "the named total; not verifiable by a sum-check (%s cannot be summed)" % ("an index" if mtype == "index" else "a rate")


def _nnls(X: Any, y: Any) -> Any:
    """Non-negative least squares (Lawson and Hanson, active set), numpy only: min ||X w - y|| with w >= 0."""
    import numpy as np
    n = X.shape[1]
    P = np.zeros(n, dtype=bool)
    w = np.zeros(n)
    g = X.T @ (y - X @ w)
    it, cap = 0, 6 * n + 10
    while (~P).any() and g[~P].max() > 1e-10 and it < cap:
        P[np.flatnonzero(~P)[int(np.argmax(g[~P]))]] = True
        while it < cap:
            it += 1
            z = np.zeros(n)
            z[P] = np.linalg.lstsq(X[:, P], y, rcond=None)[0]
            if z[P].min() > 0:
                w = z
                break
            neg = P & (z <= 0)
            a = float(np.min(w[neg] / np.maximum(w[neg] - z[neg], 1e-300)))
            w = w + a * (z - w)
            P &= w > 1e-12
        g = X.T @ (y - X @ w)
    return w


def _convex_fit(y: Any, X: Any) -> Tuple[float, Any]:
    """(the RMS residual, the weights) of y reproduced as a weighted average of the columns of X (weights >= 0 adding to 1, fixed
    over the cells)."""
    import numpy as np
    lam = 1e3 * max(1.0, float(np.abs(y).max()))
    with np.errstate(all="ignore"):               # some BLAS builds (macOS Accelerate) raise spurious floating-point flags in matmul
        w = _nnls(np.vstack([X, lam * np.ones((1, X.shape[1]))]), np.r_[y, lam])
        rms = float(np.sqrt(np.mean((X @ w - y) ** 2)))
    return (rms if np.isfinite(rms) else float("inf")), w      # a fit that did not converge is no evidence


def _weights_cover(w: Any) -> bool:
    """Whether an aggregate's weights rest on its members all (see _unnamed_aggregate): every weight at least AGG_MIN_WEIGHT when
    there are at most AGG_SMALL_TABLE members, else the heaviest members that carry 95% of the weight are at least
    AGG_SUPPORT_SHARE of them."""
    import numpy as np
    w = np.asarray(w, dtype=float)
    if len(w) <= AGG_SMALL_TABLE:
        return bool(float(w.min()) >= AGG_MIN_WEIGHT)
    cum = np.cumsum(np.sort(w)[::-1]) / max(float(w.sum()), 1e-12)
    return bool((int(np.searchsorted(cum, 0.95)) + 1) >= AGG_SUPPORT_SHARE * len(w))


def _convex_rms(y: Any, X: Any) -> float:
    """The RMS residual of y reproduced as a weighted average of the columns of X (weights >= 0 adding to 1, fixed over the cells)."""
    return _convex_fit(y, X)[0]


def _unnamed_aggregate(S: Dict[str, Any], A: Any, stats: Dict[int, Tuple[float, int]], labels: Sequence[str],
                       unit: Optional[float] = None) -> Optional[Tuple[int, Dict[str, Any]]]:
    """A member with no name that says it is the aggregate of a rate or an index is one only when ALL of these hold, whatever
    its place in the file (first in the file is no evidence): it lies STRICTLY inside the others' min-max in at least 99% of its
    cells (`stats`); it has the table's full coverage (a value wherever any member has one, at least every other member's); and the
    others reproduce it: on the cells where every member has a value, a weighted average of the others (weights fixed, at
    least 0, adding to 1) matches it to within one unit of the last published digit, a typical member's own fit being at least
    3 units off and its own a quarter of that (an aggregate is an exact weighted average of its parts to the digit published, a
    member in the middle of the range is not). With fewer than 3 parts, over 40 members, or fewer than 12 complete cells there is
    no evidence to tell them apart. The 5 candidates whose relation to the others' mean is the most stable over the cells are
    fitted (a true aggregate is among them), against at most 8 peers, on at most 600 cells: a COUNT budget (wave 5e, P3: never a clock),
    so the answer is the same on any machine. (index, evidence) or None."""
    import numpy as np
    M, C, T = A.shape
    if not AGG_MIN_MEMBERS <= M <= AGG_MAX_MEMBERS:
        return None
    anyv = ~np.isnan(A).all(axis=0)
    if not anyv.any():
        return None
    cover = np.array([float((~np.isnan(A[m]))[anyv].mean()) for m in range(M)])
    complete = ~np.isnan(A).any(axis=0)
    if int(complete.sum()) < AGG_MIN_FIT_CELLS:
        return None
    cells = np.flatnonzero(complete.reshape(-1))
    if len(cells) > AGG_FIT_ROWS:
        cells = cells[np.linspace(0, len(cells) - 1, AGG_FIT_ROWS).astype(int)]
    B = A.reshape(M, -1)[:, cells]
    if unit is None:
        unit = (10.0 ** -int(S["measure"].get("decimals") or 0)) * float(S["measure"].get("factor") or 1.0)
    cands = []
    alt = {x for x in range(M) if _is_alt(labels[x])}      # wave 5d: an alternative total is never one of the aggregate's parts
    for m in range(M):
        share_strict, n = stats.get(m, (0.0, 0))
        if n < MIN_COMPLETE or share_strict < BOUND_SHARE or cover[m] < 0.99 or cover[m] < float(np.delete(cover, m).max()):
            continue
        if m in alt:
            continue                                  # a member that says it leaves something out is an alternative, never the aggregate
        rest = [x for x in range(M) if x != m and x not in alt]
        d = B[m] - B[rest].mean(axis=0)
        cands.append((float(d.std()), m))             # the stability of its relation to the others' mean over the cells
    found = []
    for _sd, m in sorted(cands)[:AGG_TRIES]:
        rest = [x for x in range(M) if x != m and x not in alt]
        rm, wts = _convex_fit(B[m], B[rest].T)
        if rm > AGG_FIT_UNITS * unit:
            continue
        # wave 5d: an aggregate is the weighted average of ALL its members. A candidate the others reproduce without some of
        # them (a combined member: the average of two or three of the regions) is a subtotal of a part of the table, never its
        # aggregate: read as the national figure it dropped the other regions. In a table of at most 12 members every member
        # carries at least 1% of the weight; in a larger one a small member cannot be told from a zero, so the members that
        # carry 95% of the weight must be at least half of them
        if not _weights_cover(wts):
            continue
        step = max(1, len(rest) // AGG_PEERS)
        peers = []
        for i in rest[::step][:AGG_PEERS]:
            peers.append(_convex_rms(B[i], B[[x for x in rest if x != i]].T))
        med = float(np.median(peers))
        if med < AGG_PEER_UNITS * unit or rm > AGG_PEER_RATIO * med:
            continue
        found.append((rm, m, med))
    if len(found) != 1:
        return None                               # none, or two that cannot be told apart: no aggregate
    rm, m, med = found[0]
    return m, {"fit_rms": round(rm, 6), "typical_member_fit_rms": round(med, 6), "unit": unit, "fit_cells": int(len(cells)),
               "coverage": round(float(cover[m]), 4)}


def _rate_aggregate(S: Dict[str, Any], j: int, tm: Optional[_Timer] = None) -> None:
    """A rate or an index is never summed or averaged across members (AM4). Its published aggregate is a member that lies inside
    the range of the others in 99% of its cells AND (a) carries a total's name, or a whole country's (Canada), or (b) is shown to
    be the others' weighted average (`_unnamed_aggregate`). First in the file is no evidence (it read one province as the
    national figure when it lay in the others' range). With none, the table has no aggregate: rule 6 reads one member, by
    dominance, and the estimand says it is not a national figure."""
    import numpy as np
    rec = S["dims"][j]
    labels = rec["labels"]
    A, X, _c = _dim_tensor(S, j)
    M = len(labels)
    stats: Dict[int, Tuple[float, int]] = {}
    named: List[Tuple[int, int, float, int]] = []          # a name AND inside the others' range in 99% of its cells (the range verifies it)
    named_any: List[Tuple[int, int, float, int]] = []      # a name, whatever the range says (wave 5c: the name is the evidence)
    coded_tot = _coded_totals(labels)
    with np.errstate(all="ignore"):
        for m in range(M):
            rest = [x for x in range(M) if x != m]
            if not rest:
                continue
            tier = 2 if (m in coded_tot and not _is_alt(labels[m])) else _agg_name_tier(labels[m])
            if tier == 1 and (not _is_geographic(rec["column"]) or _COUNTRY_COLUMN.search(rec["column"])):
                tier = 0            # a whole country is the aggregate of its provinces, never of other countries (wave 5e)
            lo = np.nanmin(np.where(np.isnan(A[rest]), np.inf, A[rest]), axis=0)
            hi = np.nanmax(np.where(np.isnan(A[rest]), -np.inf, A[rest]), axis=0)
            have = ~np.isnan(A[m]) & np.isfinite(lo) & np.isfinite(hi)
            n = int(have.sum())
            if n < MIN_COMPLETE:
                if tier:
                    named_any.append((tier, m, 0.0, n))
                continue
            inside = float(((A[m][have] >= lo[have] - 1e-12) & (A[m][have] <= hi[have] + 1e-12)).mean())
            stats[m] = (float(((A[m][have] > lo[have]) & (A[m][have] < hi[have])).mean()), n)
            if tier:
                named_any.append((tier, m, inside, n))
                if inside >= BOUND_SHARE:
                    named.append((tier, m, inside, n))
    pick, by, evidence = None, "", {}
    if named:
        top = max(t for t, _m, _i, _n in named)
        pool = [x for x in named if x[0] == top]
        if len(pool) == 1:                        # two whole countries' names (a table of countries), or two members that say "total"
            pick, by = pool[0], "name"            # (wave 5d: "Total, all industries" and a "Total excl. ..."), do not say which is the total
    if pick is None:
        # wave 5c: a member with a total's name (total, all, overall, national, _T) or a whole country's name in a geographic
        # dimension is the aggregate even when no check can verify it: an index or a rate cannot be summed, so there is no
        # sum-check, two bases or one other member leave no range to lie in, and a weighted-average fit has too little to fit. The
        # name is then the only evidence, and the table says so (sum_check.verified false). Exactly one such name, and never a
        # member that says it is the rest ("All other provinces").
        cands = [x for x in named_any if (x[0] == 2 or _is_geographic(rec["column"])) and not _is_rest(labels[x[1]])]
        if cands:
            top = max(t for t, _m, _i, _n in cands)
            pool = [x for x in cands if x[0] == top]
            if len(pool) == 1:
                pick, by = pool[0], "name"
                evidence = {"verified": False}
    if pick is None:
        try:
            got = _unnamed_aggregate(S, A, stats, labels, unit=2.0 * _tol_unit(S, j))
        except (np.linalg.LinAlgError, ValueError, FloatingPointError):      # a fit that cannot be made is no evidence
            got = None
        if got is not None:
            pick, by, evidence = (2, got[0], stats[got[0]][0], stats[got[0]][1]), "range and fit", got[1]
    if pick is None:
        rec["role"] = "unresolved"
        rec["why"] = "a rate or an index is never added up; no member is a published aggregate"
        return
    _s, m, inside, n = pick
    unverified = evidence.get("verified") is False
    rec.update(role="rate_aggregate", total=labels[m], total_index=m, aggregate_by=by,
               parts=[labels[x] for x in range(M) if x != m], part_index=[x for x in range(M) if x != m],
               components={}, alternatives={},
               sum_check=dict({"inside_range_share": round(inside, 4), "cells": n}, **evidence),
               why=("%s %s is never added or averaged across members: %s is %s" % (
                        "an" if S["measure"]["type"] == "index" else "a", S["measure"]["type"], labels[m],
                        named_total_words(S["measure"]["type"]))) if unverified else
               ("a %s is never added or averaged across members: %s lies inside the others' range in %s%% of %s "
                "cells and is read as the published aggregate%s" % (
                    S["measure"]["type"], labels[m], round(100 * inside, 1), _fmt_count(n),
                    "" if by == "name" else " (no member is named as a total: it has full coverage and the others' "
                                            "weighted average reproduces it to the last published digit)")))


def _adjustment(S: Dict[str, Any], j: int) -> None:
    """A seasonally adjusted copy beside the unadjusted one, by behaviour: the two series have the same SHAPE (wave 5e, P5: the changes of
    their smoothed logs correlate and their ratio is steady, whatever the gap between their levels) and one is three times as seasonal
    as the other (the variance of its month means, detrended). Labels are hints."""
    import numpy as np
    rec = S["dims"][j]
    labels = rec["labels"]
    M = len(labels)
    step = (S.get("period") or {}).get("step", 1)
    # wave 5d: a QUARTERLY table too (four seasons a year, at least 4 years); a monthly one needs 2 years, as before
    if not (2 <= M <= ADJ_MAX_MEMBERS) or not S.get("monthly") or step not in ADJ_MIN_PERIODS or S["months"] < ADJ_MIN_PERIODS[step]:
        return
    if _is_geographic(rec["column"]):
        return                                  # a dimension of places is never an adjustment: two regions that move together are two regions
    A, X, _c = _dim_tensor(S, j, restrict=False)
    months = S["_months"]
    best = None
    for a in range(M):
        for b in range(a + 1, M):
            copy = _pair_is_copy(A[a], A[b], step) or _year_agree(A[a], A[b], months, S["measure"]["type"], step)
            if not copy:
                continue
            ratio = _seasonal_ratio(A[a], A[b], months, S["measure"]["type"], step)
            if ratio is not None and (ratio >= ADJ_SEASONAL_RATIO or ratio <= 1.0 / ADJ_SEASONAL_RATIO):
                nsa, sa = (a, b) if ratio >= 1 else (b, a)
                strength = ratio if ratio >= 1 else 1.0 / ratio
                if best is None or strength > best[2]:
                    best = (nsa, sa, strength)
    if best is None:
        return
    nsa, sa, strength = best
    rec.update(role="adjustment", nsa=labels[nsa], sa=labels[sa], nsa_index=nsa, sa_index=sa,
               total=labels[nsa], total_index=nsa, components={}, alternatives={},
               why="the two series move together under a steady ratio; %s is %s times as seasonal as %s" % (
                   labels[nsa], round(strength, 1), labels[sa]))
    for m in range(M):
        if m not in (nsa, sa):
            rec["alternatives"][labels[m]] = labels[nsa]


def _seasonal_strength(y: Any, months: Sequence[str], step: int = 1) -> Optional[float]:
    """The variance of a series' seasonal means (its detrended values by month of the year, or by quarter of the year for a
    quarterly table, wave 5d), or None when the series is too short or leaves a season empty."""
    import numpy as np
    per = 12 // step                                 # seasons a year: 12 months, 4 quarters
    ok = ~np.isnan(y)
    if ok.sum() < 2 * per:
        return None
    v = y.copy()
    if np.all(v[ok] > 0):
        v = np.log(v)
    else:
        mu = np.nanmean(np.abs(v)) or 1.0
        v = v / mu
    # a centred 2 x per moving average as the trend
    h = per // 2
    k = np.r_[0.5, np.ones(per - 1), 0.5] / float(per)
    tr = np.full_like(v, np.nan)
    for i in range(h, len(v) - h):
        w = v[i - h:i + h + 1]
        if not np.isnan(w).any():
            tr[i] = float(np.dot(w, k))
    res = v - tr
    slot = np.array([((int(m[5:7]) - 1) // step) % per for m in months])
    means = []
    for mm in range(per):
        x = res[(slot == mm) & ~np.isnan(res)]
        if len(x):
            means.append(float(x.mean()))
    if len(means) < per:
        return None
    return float(np.var(means))


def _year_gaps(a: Any, b: Any, months: Sequence[str], mtype: str, step: int = 1) -> List[float]:
    """Each complete calendar year's relative gap between two members' totals (a mean for a level), in every context (up to 60). A year is
    complete when it holds every period of it (12 months, 4 quarters) with a value in both."""
    import numpy as np
    years = sorted({m[:4] for m in months})
    mon = np.array(months)
    per = 12 // step
    agree = []
    for c in range(min(a.shape[0], 60)):
        ya, yb = a[c], b[c]
        if np.isnan(ya).all() or np.isnan(yb).all():
            continue
        for y in years:
            sel = np.array([m.startswith(y) for m in mon])
            if sel.sum() == per and not np.isnan(ya[sel]).any() and not np.isnan(yb[sel]).any():
                fa, fb = (ya[sel].sum(), yb[sel].sum()) if mtype in ("flow", "count", "unknown") else (ya[sel].mean(), yb[sel].mean())
                if fb != 0:
                    agree.append(abs(fa / fb - 1.0))
    return agree


def _year_agree(a: Any, b: Any, months: Sequence[str], mtype: str, step: int = 1) -> bool:
    """The wave 5d test, kept as one more way to see a copy (never the only one; wave 5e, P5): calendar-year totals within 3% in at least
    two years, the largest gap within 6% (a statistical office benchmarks its adjusted series to the unadjusted annual totals). It errs
    towards calling two members copies, which only ever shows one member, never adds two."""
    import numpy as np
    agree = _year_gaps(a, b, months, mtype, step)
    return len(agree) >= 2 and float(np.median(agree)) <= ADJ_YEAR_TOL and float(np.max(agree)) <= 2 * ADJ_YEAR_TOL


def _seasonal_ratio(a: Any, b: Any, months: Sequence[str], mtype: str, step: int = 1) -> Optional[float]:
    """The median, over the contexts (up to 60), of the ratio of a's seasonal strength to b's; None when no context can say."""
    import numpy as np
    ratios = []
    for c in range(min(a.shape[0], 60)):
        ya, yb = a[c], b[c]
        if np.isnan(ya).all() or np.isnan(yb).all():
            continue
        sa_, sb_ = _seasonal_strength(ya, months, step), _seasonal_strength(yb, months, step)
        if sa_ is not None and sb_ is not None and sb_ > 0 and sa_ > 0:
            ratios.append(sa_ / sb_)
    return float(np.median(ratios)) if ratios else None


def _smooth_log(y: Any, per: int) -> Any:
    """The log of a series' centred moving average over one season (12 months, 4 quarters, 2 half-years; the series itself for a year),
    for a gap of at most 3 periods filled by a straight line first (a smoothing input only, never a figure). NaN where the window is
    incomplete or the level is not positive."""
    import numpy as np
    v = np.array(y, dtype=float)
    n = len(v)
    ok = ~np.isnan(v)
    if ok.sum() >= 2:
        idx = np.flatnonzero(ok)
        for a, b in zip(idx[:-1], idx[1:]):
            if 1 < b - a <= 4:
                v[a + 1:b] = np.interp(np.arange(a + 1, b), [a, b], [v[a], v[b]])
    if per > 1:
        k = np.r_[0.5, np.ones(per - 1), 0.5] / float(per) if per % 2 == 0 else np.ones(per) / float(per)
        h = len(k) // 2
        sm = np.full(n, np.nan)
        for i in range(h, n - h):
            w = v[i - h:i + h + 1]
            if not np.isnan(w).any():
                sm[i] = float(np.dot(w, k))
        v = sm
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(v > 0, np.log(np.where(v > 0, v, 1.0)), np.nan)


def _shape_copy(la: Any, lb: Any, need: int) -> Optional[bool]:
    """Whether two smoothed-log series are one quantity twice: the changes of the two correlate at least COPY_CORR and the log of
    their ratio wanders at most COPY_RATIO_SD. None when the test cannot run (fewer than `need` points where both are read, or a
    series that does not move)."""
    import numpy as np
    ok = np.isfinite(la) & np.isfinite(lb)
    if int(ok.sum()) < need:
        return None
    da, db = np.diff(la), np.diff(lb)
    pair = ok[:-1] & ok[1:]
    da, db = da[pair], db[pair]
    if len(da) < need - 1 or float(da.std()) < 1e-9 or float(db.std()) < 1e-9:
        return None
    corr = float(np.corrcoef(da, db)[0, 1])
    sd = float(np.std((la - lb)[ok]))
    return bool(corr >= COPY_CORR and sd <= COPY_RATIO_SD)


def _pair_is_copy(a: Any, b: Any, step: int) -> bool:
    """Whether members a and b (arrays [context, time]) are one quantity twice by shape in most of the contexts that can say."""
    per = 12 // max(1, step)
    need = COPY_MIN_ANNUAL if per == 1 else COPY_MIN_POINTS
    tested = copies = 0
    for c in range(min(a.shape[0], 60)):
        got = _shape_copy(_smooth_log(a[c], per), _smooth_log(b[c], per), need)
        if got is None:
            continue
        tested += 1
        copies += 1 if got else 0
    return bool(tested and 2 * copies >= tested)


def _copies_by_shape(S: Dict[str, Any], A: Any, members: Sequence[int]) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """Two of `members` that are one quantity twice by SHAPE (an adjusted copy, another price basis: the same movement under a steady
    ratio, whatever the gap between their levels): (a, b). (None, None) when no pair could be compared (the table is too short or too
    blank to tell a copy from a part), which is no evidence either way; None when the pairs were compared and none is a copy. Parts of
    a total are almost never so alike for 24 periods, and when they are, one member shown is the right answer. At most the
    COPY_PAIR_MEMBERS largest members are compared (a count)."""
    import numpy as np
    step = int((S.get("period") or {}).get("step") or 1)
    if step not in (1, 3, 6, 12):
        return (None, None)
    per = 12 // step
    need = COPY_MIN_ANNUAL if per == 1 else COPY_MIN_POINTS
    size = {m: (float(np.nanmean(A[m])) if (~np.isnan(A[m])).any() else 0.0) for m in members}
    mem = sorted(members, key=lambda m: (-size[m], m))[:COPY_PAIR_MEMBERS]
    C = min(A.shape[1], 60)
    sm = {m: [_smooth_log(A[m][c], per) for c in range(C)] for m in mem}
    any_tested = False
    months, mtype = S.get("_months") or [], S["measure"]["type"]
    for i, a in enumerate(mem):
        for b in mem[i + 1:]:
            if step in (1, 3) and months and _year_agree(A[a], A[b], months, mtype, step):
                return (a, b)
            tested = copies = 0
            for c in range(C):
                got = _shape_copy(sm[a][c], sm[b][c], need)
                if got is None:
                    continue
                tested += 1
                copies += 1 if got else 0
            if tested:
                any_tested = True
                if 2 * copies >= tested:
                    return (a, b)
    return None if any_tested else (None, None)


_UNADJUSTED = re.compile(r"(?i)\b(?:unadjusted|not seasonally adjusted|non[- ]?seasonally adjusted|raw|original|actual|brut(?:es?)?|"
                         r"non d[\u00e9e]saisonnalis[\u00e9e]e?s?|nicht saisonbereinigt|sin desestacionalizar)\b")


def _rule6(S: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """No relation verified: an official table is read one member at a time (the member that says total, else a whole country's name
    in a dimension of places, else the one with the most cells that dominates, and then the table says it is not a total); a business
    export adds its members up as the engine always has (the file is a ledger: wave 5e keeps that reading, and a member that says
    total is never added to the rows it totals, see `_relations`)."""
    import numpy as np
    labels = rec["labels"]
    j = S["dims"].index(rec)
    if S["official"] or S["measure"]["type"] in ("rate", "index"):
        coded = _coded_totals(labels)
        hint = [m for m in range(len(labels)) if _says_total(labels[m]) or m in coded]
        by = "name"
        whole = [m for m in range(len(labels)) if _agg_name_tier(labels[m]) == 1 and _is_geographic(rec["column"])
                 and not _COUNTRY_COLUMN.search(rec["column"])]
        if hint:
            m = hint[0]
        elif len(whole) == 1:
            # a whole country's name among its provinces (wave 5e: tried before dominance; "one member shown" says it is unverified)
            m = whole[0]
        else:
            by = "dominance"
            SM, E = S["_SM"], S["_E"]
            cover = np.bincount(SM[:, j], weights=E.sum(axis=1), minlength=len(labels))
            try:
                A, X, _c = _dim_tensor(S, j)
                dom = _dominance(A)
            except _TooLarge:
                dom = np.zeros(len(labels))
            # the contract's default prefers the unadjusted copy (S1: "the unadjusted series"), whichever is larger
            m = int(sorted(range(len(labels)), key=lambda x: (0 if _UNADJUSTED.search(labels[x]) else 1, -cover[x], -dom[x], x))[0])
        prior = rec.get("why")
        rec.update(role="single", total=labels[m], total_index=m, components={}, alternatives={}, single_by=by,
                   evidence="named" if by == "name" else "single",
                   noun="national figure" if _is_geographic(rec["column"]) else "total",
                   why=(prior + "; " if prior else "") + "read one member at a time (an official table is never "
                                                       "added across a dimension it could not verify)")
        tiers = [_agg_name_tier(lb) for lb in labels]
        if (not _is_geographic(rec["column"]) or _COUNTRY_COLUMN.search(rec["column"])):
            tiers = [0 if t == 1 else t for t in tiers]       # a country's name is no whole in a table of countries
        if by == "dominance" and not any(_says_total(lb) or t == 2 for lb, t in zip(labels, tiers)) and tiers.count(1) != 1:
            # no member is named as a total (one whole country's name, Canada, among provinces would be; two of them are a table of
            # countries): the one shown is not the table's figure
            rec["no_total_member"] = True
    else:
        rec.update(role="flat_additive", total=None, total_index=None, components={}, alternatives={}, evidence="ledger",
                   why=rec.get("why") or "no member is a total of the others; the members are added up")


# --------------------------------------------------------------------------------------------- 3. flags
def _flags(S: Dict[str, Any]) -> None:
    """structure.flags: the publisher's codes counted over the file; a code whose rows have a blank measure stands for a
    missing value (suppressed, not available, too unreliable), learned from the file; the headline's own quality codes
    are added by default_slice."""
    import numpy as np
    F = S.get("_F")
    if F is None:
        return
    labels = S["_flag_labels"]
    E, V = S["_E"], S["_V"]
    pub = S.get("publisher")
    codes: Dict[str, Dict[str, Any]] = {}
    by_kind: Dict[str, int] = {}
    unrec: List[str] = []
    for i, lb in enumerate(labels):
        m = (F == i) & E
        n = int(m.sum())
        if not n or lb == "":
            continue
        blank = float(np.isnan(V[m]).mean())
        kind = _code_kind(lb, pub)
        if kind is None:
            kind = "unrecognised flag"
            unrec.append(lb)
        missing = blank >= FLAG_BLANK_SHARE
        codes[lb] = {"kind": kind, "rows": n, "blank_measure": round(blank, 4), "missing": missing}
        if missing:
            by_kind[kind] = by_kind.get(kind, 0) + n
    blank_unflagged = int((E & np.isnan(V) & ((F < 0) | np.isin(F, [i for i, lb in enumerate(labels) if lb == ""]))).sum())
    S["flags"] = {"column": S["_flag_column"], "publisher": pub, "by_kind": by_kind, "codes": codes,
                  "unrecognised": unrec, "blank_without_flag": blank_unflagged, "quality_of_headline": {}}


# --------------------------------------------------------------------------------------------- 4. slices and breakdowns
def _slices(S: Dict[str, Any]) -> None:
    """S1 the headline (one member per dimension: the root of a partition or hierarchy, the unadjusted copy, a measure
    dimension's total-named or dominant member, rule 6's single member; a flat-additive dimension is added up), S2 the
    momentum slice (S1 adjusted), then components, alternatives and other measures. Breakdowns: each verified additive
    dimension's root children, the other dimensions at S1 (a hierarchy at depth 1)."""
    where: Dict[str, Any] = {}
    why: Dict[str, str] = {}
    for d in S["dims"]:
        r = d["role"]
        if r == "constant":
            where[d["column"]] = d["labels"][0]
            why[d["column"]] = "the only member"
        elif r == "flat_additive":
            where[d["column"]] = "*"
            why[d["column"]] = "every member, added up (no member is a total of the others)"
        elif r == "parts":
            where[d["column"]] = PARTS_TOKEN
            why[d["column"]] = ("no total row: the headline is the sum of the %d %s, month by month" % (
                len(d.get("parts") or []), d.get("noun") or "members"))
        elif d.get("total") is not None:
            where[d["column"]] = d["total"]
            why[d["column"]] = {
                "partition": "the total of the %d other members (sum-check)" % len(d.get("parts") or []),
                "hierarchy": "the root of the hierarchy (sum-checked family by family)",
                "adjustment": "the unadjusted series: full calendar years, additive",
                "measure": ("the engine's default measure: the first of %s" % MEASURE_RULE) if d.get("measure_dim") else
                "the total measure; the others are components or other measures",
                "components": "it bounds the other members, which are its components",
                "rate_aggregate": named_total_words(S["measure"]["type"]) if _named_unverified(d) else
                "the published aggregate (a %s is never added across members)" % S["measure"]["type"],
                "single": "read one member at a time (no relation verified)",
            }.get(r, "the default member")
    slices = []
    if not _series_exist(S, where):
        S["usable"] = False
        S["reason"] = "the default slice has no series in the table"
        S["slices"] = []
        return
    _parts_mode(S, where)
    slices.append({"id": "S1", "default": True, "where": dict(where), "use": "headline", "why": why})
    adj = next((d for d in S["dims"] if d["role"] == "adjustment"), None)
    if adj is not None:
        w2 = dict(where, **{adj["column"]: adj["sa"]})
        if _series_exist(S, w2):
            slices.append({"id": "S2", "where": w2, "use": "momentum",
                           "why": {adj["column"]: "the seasonally adjusted copy: month-on-month momentum"}})
    for d in S["dims"]:
        base = where.get(d["column"])
        for comp, parent in (d.get("components") or {}).items():
            if parent == base and len(slices) < SLICES_MAX:
                w = dict(where, **{d["column"]: comp})
                if _series_exist(S, w):
                    slices.append({"id": "S%d" % (len(slices) + 1), "where": w, "use": "component",
                                   "why": {d["column"]: "a component of %s" % parent}})
        for alt, of in (d.get("alternatives") or {}).items():
            if len(slices) < SLICES_MAX and d["role"] != "adjustment":
                w = dict(where, **{d["column"]: alt})
                if _series_exist(S, w):
                    slices.append({"id": "S%d" % (len(slices) + 1), "where": w, "use": "alternative",
                                   "why": {d["column"]: "an alternative total (%s)" % of}})
        if d["role"] == "measure" and d.get("measure_dim"):
            for mm in d["measures"]:
                if mm["name"] != base and not mm["precision"] and len(slices) < SLICES_MAX:
                    w = dict(where, **{d["column"]: mm["name"]})
                    if _series_exist(S, w):
                        slices.append({"id": "S%d" % (len(slices) + 1), "where": w, "use": "other_measure",
                                       "measure_id": mm["id"],
                                       "why": {d["column"]: "another measure (%s, %s)" % (mm["type"], mm["uom"] or "its own units")}})
        elif d["role"] == "measure" and d.get("mixed_units"):
            for lb in d["labels"]:
                if lb != base and len(slices) < SLICES_MAX and lb not in (d.get("components") or {}):
                    w = dict(where, **{d["column"]: lb})
                    if _series_exist(S, w):
                        slices.append({"id": "S%d" % (len(slices) + 1), "where": w, "use": "other_measure",
                                       "why": {d["column"]: "another measure (%s)" % (d.get("unit_of") or {}).get(lb, "")}})
    S["slices"] = slices
    S["default"] = slices[0]["where"]
    bds = []
    if S["measure"]["type"] not in ("rate", "index"):
        for d in S["dims"]:
            if d["role"] == "parts" and where.get(d["column"]) == PARTS_TOKEN:
                bd = {"id": "B%d" % (len(bds) + 1), "dim": d["column"], "parent": None, "no_total": True,
                      "parts": list(d["parts"]), "depth": 1}
                if all(_series_exist(S, dict(where, **{d["column"]: p})) for p in bd["parts"]):
                    bds.append(bd)
                continue
            if d["role"] not in ("partition", "hierarchy") or where.get(d["column"]) != d.get("total"):
                continue
            parts = list(d.get("part_index") or [])
            if not parts:
                continue
            bd = {"id": "B%d" % (len(bds) + 1), "dim": d["column"], "parent": d["total"],
                  "parts": [d["labels"][p] for p in parts], "depth": 1}
            # every part's series at S1's other members
            if all(_series_exist(S, dict(where, **{d["column"]: p})) for p in bd["parts"]):
                bds.append(bd)
    S["breakdowns"] = bds
    # the headline's own quality codes
    if S.get("flags") is not None:
        sel = _select(S, where)
        F, E = S["_F"], S["_E"]
        labels = S["_flag_labels"]
        q: Dict[str, int] = {}
        if sel is not None:
            for s in sel:
                for t in range(F.shape[1]):
                    if E[s, t]:
                        lb = labels[F[s, t]] if F[s, t] >= 0 else ""
                        q[lb] = q.get(lb, 0) + 1
        S["flags"]["quality_of_headline"] = dict(sorted(q.items(), key=lambda kv: (-kv[1], kv[0])))


def _members(S: Dict[str, Any], where: Dict[str, Any]) -> Optional[List[Optional[List[int]]]]:
    """Each dimension's member indices for a slice (None: every member), or None when a member is unknown."""
    out: List[Optional[List[int]]] = []
    for d in S["dims"]:
        w = where.get(d["column"], "*")
        if w == "*" or w is None:
            out.append(None)
            continue
        if w == PARTS_TOKEN and d.get("role") == "parts":
            out.append(list(d["part_index"]))
            continue
        ws = w if isinstance(w, (list, tuple)) else [w]
        idx = []
        for x in ws:
            if x not in d["labels"]:
                return None
            idx.append(d["labels"].index(x))
        out.append(idx)
    return out


def _select(S: Dict[str, Any], where: Dict[str, Any]) -> Optional[Any]:
    import numpy as np
    mem = _members(S, where)
    if mem is None:
        return None
    SM = S["_SM"]
    mask = np.ones(SM.shape[0], dtype=bool)
    for j, idx in enumerate(mem):
        if idx is not None:
            mask &= np.isin(SM[:, j], idx)
    return np.flatnonzero(mask)


def _series_exist(S: Dict[str, Any], where: Dict[str, Any]) -> bool:
    sel = _select(S, where)
    return sel is not None and len(sel) > 0 and bool(S["_E"][sel].any())


def _parts_cells(S: Dict[str, Any], where: Dict[str, Any], sel: Any) -> Tuple[Any, Any]:
    """(the values, which of them count as present) of a slice's series. For the parts of a dimension with no total row (wave 5d): where
    a part is blank in a month and a COMBINED member that equals the sum of a family of the parts has a value that month (a region
    split into sub-regions, a group of regions), the combined member's published value stands in for the family that month, so a
    figure the cells give is never left incomplete: the family's first part carries the value, the others carry 0 and count as present."""
    import numpy as np
    V = S["_V"][sel]
    have = ~np.isnan(V)
    ds = [d for d in S["dims"] if d.get("role") == "parts" and where.get(d["column"]) == PARTS_TOKEN and d.get("combined")]
    if not ds or have.all():
        return V, have
    d = ds[0]
    j = S["dims"].index(d)
    labels = d["labels"]
    SM = S["_SM"]
    others = [k for k in range(SM.shape[1]) if k != j]
    cache = S.setdefault("_row_of", {})
    if j not in cache:
        cache[j] = {(tuple(int(x) for x in SM[r, others]), int(SM[r, j])): r for r in range(SM.shape[0])}
    row_of = cache[j]
    pos_of = {int(r): i for i, r in enumerate(sel)}
    W, H = V.copy(), have.copy()
    kept = set(int(x) for x in d.get("part_index") or [])
    for comb, fam in (d.get("combined") or {}).items():
        if comb not in labels or any(f not in labels for f in fam):
            continue
        fam_idx = [labels.index(f) for f in fam]
        if not set(fam_idx) <= kept:
            continue
        for ctx in {tuple(int(x) for x in SM[r, others]) for r in sel}:
            leaf = [pos_of.get(row_of.get((ctx, m), -1)) for m in fam_idx]
            c_row = row_of.get((ctx, labels.index(comb)))
            if c_row is None or any(x is None for x in leaf):
                continue
            C = S["_V"][c_row]
            L = V[leaf]
            miss = np.isnan(L).any(axis=0) & ~np.isnan(C)
            if miss.any():
                W[leaf[0], miss] = C[miss]
                for x in leaf[1:]:
                    W[x, miss] = 0.0
                for x in leaf:
                    H[x, miss] = True
    return W, H


def series(S: Dict[str, Any], where: Dict[str, Any]) -> Tuple[List[str], Any, Any]:
    """(times, the slice's values in base units (NaN where no member has a value), how many member cells were blank)."""
    import numpy as np
    sel = _select(S, where)
    T = len(S["_times"])
    if sel is None or not len(sel):
        return list(S["_times"]), np.full(T, np.nan), np.zeros(T, dtype=int)
    V, have = _parts_cells(S, where, sel)
    E = S["_E"][sel]
    val = np.where(have.any(axis=0), np.where(have, V, 0.0).sum(axis=0), np.nan)
    if _needs_complete(S, where):
        # a dimension with no total row: its parts are summed in the months where EVERY part has a value (a month with a
        # suppressed part would understate the sum), else the reported parts are summed (the estimand says so)
        val = np.where(have.all(axis=0), val, np.nan)
    blank = (E & ~have).sum(axis=0)
    return list(S["_times"]), val, blank


def _needs_complete(S: Dict[str, Any], where: Dict[str, Any]) -> bool:
    """Whether a slice sums a dimension's parts in complete months only: it names that dimension's parts (the token or a
    list of 2 or more of its members) and the table's reading found enough complete months (parts_mode)."""
    for d in S["dims"]:
        if d.get("role") != "parts" or not d.get("complete_only"):
            continue
        w = where.get(d["column"])
        if w == PARTS_TOKEN or (isinstance(w, (list, tuple)) and len(w) >= 2):
            return True
    return False


def _parts_mode(S: Dict[str, Any], where: Dict[str, Any]) -> None:
    """For each dimension with no total row: complete months only when the table's own latest windows (anchored at the last
    month any part has a value) hold at least MIN_MATCHED months complete in both, and the last complete month is within 3
    months of the last month with any value; else the reported parts are summed in every month and the estimand says how many
    region-months are suppressed (the headline is then incomplete). Never silent."""
    import numpy as np
    pd_ = [d for d in S["dims"] if d.get("role") == "parts" and where.get(d["column"]) == PARTS_TOKEN]
    if not pd_:
        return
    for d in pd_:
        d["complete_only"] = False
    months, rep_vals = _monthly(S, where)                   # every part with a value, summed
    win = windows(months, rep_vals)
    for d in pd_:
        d["complete_only"] = True
    ok = False
    if win is not None:
        cm, cv = _monthly(S, where)                         # complete months only
        have = [m for m, v in zip(cm, cv) if v == v]
        anchor = max(m for m, v in zip(months, rep_vals) if v == v)
        if have and max(have) >= _mshift(anchor, -3):
            lat, pri = _prange(S, win["latest"][0], win["latest"][1]), _prange(S, win["prior"][0], win["prior"][1])
            hs = set(have)
            pairs = [(a, b) for a, b in zip(pri, lat) if a in hs and b in hs]
            ok = len(pairs) >= _min_matched(S)
    for d in pd_:
        d["complete_only"] = bool(ok)
    S["parts_mode"] = {"dims": [d["column"] for d in pd_], "complete_only": bool(ok)}


def slice_by_id(S: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    return next((s for s in S.get("slices") or [] if s["id"] == sid), None)


def measure_label(S: Dict[str, Any], where: Dict[str, Any]) -> str:
    """The slice's measure column as the engine reads it: a currency flow "<label> total" (the core adds up and forecasts
    a column whose head word is a money word), a count "<label> count", a stock, rate or index "value" (a level)."""
    st = slice_type(S, where)
    t = st["type"] if sums_over_time(st) else "level"
    label = ""
    for d in S["dims"]:
        if d["role"] in ("measure", "components") and isinstance(where.get(d["column"]), str):
            label = where[d["column"]]
    if not label and not st.get("currency"):
        label = str(S["measure"].get("label_hint") or "")        # a count's name comes from the table's one constant label
    label = re.sub(r"\s+", " ", re.sub(r"[^\w\s\-]", " ", label)).strip()
    try:
        from northledger.measure import additive_kind
    except ImportError:                                        # the engine is always there in a run
        def additive_kind(_n: Any) -> str:
            return ""
    if t == "flow":
        if label and additive_kind(label) == "money":
            return label[:60]
        return ("%s total" % (label or "value"))[:60]
    if t == "count":
        if label and additive_kind(label) == "units":
            return label[:60]
        return ("%s count" % (label or "value"))[:60]
    return "value"


def slice_bytes(S: Dict[str, Any], where: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any]]:
    """The slice as a CSV the engine reads: the date and one measure column in base units (the scale applied), one row
    per date with a value."""
    import numpy as np
    times, val, blank = series(S, where)
    col = measure_label(S, where)
    dh = S["date"]["column"]
    lines = ['"%s","%s"' % (dh.replace('"', '""'), col.replace('"', '""'))]
    integral = bool(np.all(np.isnan(val) | (np.abs(val - np.round(val)) < 1e-9)))
    for t, v in zip(times, val):
        if v != v:
            continue
        lines.append("%s,%s" % (t, ("%d" % int(round(v))) if integral and abs(v) < 9e15 else repr(float(v))))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    return body, {"column": col, "date": dh, "rows": len(lines) - 1, "blank_months": [t for t, v in zip(times, val)
                                                                                      if v != v]}


def rows_a_month(S: Dict[str, Any]) -> int:
    """The table's own rows a month (StatCan retail: 465, one per series): the count the layout fixes, which is no
    activity. A table whose months do not all list the same series gives the commonest count."""
    import numpy as np
    E = S["_E"]
    months = np.asarray([t[:7] for t in S["_times"]])
    per: List[int] = []
    for m in sorted(set(months.tolist())):
        per.append(int(E[:, months == m].any(axis=1).sum()))
    if not per:
        return 0
    return int(max(set(per), key=lambda k: (per.count(k), k)))


# --------------------------------------------------------------------------------------------- 6. a plan's rows
def check_rows(S: Dict[str, Any], positions: Optional[Sequence[int]]) -> List[Dict[str, Any]]:
    """The violations of a plan's kept rows (their places among the landed rows): a total kept with its parts, both
    adjusted copies, a component with its parent, an alternative with its total, members of a measure dimension in
    different units, or several members of a rate or an index (never averaged). Empty when the rows form a slice."""
    import numpy as np
    if positions is None:
        return [{"dim": None, "kind": "unmapped", "members": []}]
    rs = S["_row_series"]
    pos = np.asarray([int(p) for p in positions if 0 <= int(p) < len(rs)], dtype=np.int64)
    ser = rs[pos] if len(pos) else np.array([], dtype=np.int32)
    ser = np.unique(ser[ser >= 0])
    if not len(ser):
        return [{"dim": None, "kind": "no_rows", "members": []}]
    SM = S["_SM"]
    out: List[Dict[str, Any]] = []
    for j, d in enumerate(S["dims"]):
        kept = sorted(set(int(x) for x in SM[ser, j]))
        names = [d["labels"][m] for m in kept]
        role = d["role"]
        if len(kept) < 2:
            continue
        if role in ("partition", "hierarchy"):
            anc = _ancestors(d)
            pairs = [(d["labels"][a], d["labels"][m]) for m in kept for a in anc.get(m, []) if a in kept]
            if pairs:
                out.append({"dim": d["column"], "kind": "total_with_parts",
                            "members": sorted(set(x for p in pairs for x in p))[:12]})
        if role == "parts":
            cmb = d.get("combined") or {}
            hit = [(c, f) for c, f in cmb.items() if c in names and any(x in names for x in f)]
            if hit:
                out.append({"dim": d["column"], "kind": "combined_with_parts",
                            "members": sorted(set([c for c, _f in hit] + [x for _c, f in hit for x in f if x in names]))[:12]})
        if role == "adjustment" and d.get("nsa_index") in kept and d.get("sa_index") in kept:
            out.append({"dim": d["column"], "kind": "two_adjustments", "members": [d["nsa"], d["sa"]]})
        comps = d.get("components") or {}
        cp = [(c, p) for c, p in comps.items() if c in names and p in names]
        if cp:
            out.append({"dim": d["column"], "kind": "component_with_parent", "members": sorted(set(x for p in cp for x in p))})
        alts = d.get("alternatives") or {}
        ap = [a for a in alts if a in names and (d.get("total") in names)]
        if ap and role != "adjustment":
            out.append({"dim": d["column"], "kind": "alt_with_total", "members": ap + [d["total"]]})
        if d.get("measure_dim"):
            if len(names) > 1:
                out.append({"dim": d["column"], "kind": "mixed_units" if d.get("mixed_units") else "mixed_measures",
                            "members": names[:12]})
        elif d.get("mixed_units"):
            units = {(d.get("unit_of") or {}).get(n) for n in names}
            if len(units) > 1:
                out.append({"dim": d["column"], "kind": "mixed_units", "members": names[:12]})
        if role in ("rate_aggregate",) or (S["measure"]["type"] in ("rate", "index") and role != "constant"):
            out.append({"dim": d["column"], "kind": "rate_members", "members": names[:12]})
        if role == "single":
            out.append({"dim": d["column"], "kind": "unverified_members", "members": names[:12]})
    return out


def _ancestors(d: Dict[str, Any]) -> Dict[int, List[int]]:
    par: Dict[int, int] = {}
    for p, ch in (d.get("tree") or {}).items():
        for c in ch:
            par[int(c)] = int(p)
    out: Dict[int, List[int]] = {}
    for m in par:
        a, seen = [], set()
        x = m
        while x in par and x not in seen:
            seen.add(x)
            x = par[x]
            a.append(x)
        out[m] = a
    return out


def plan_where(S: Dict[str, Any], positions: Sequence[int]) -> Dict[str, Any]:
    """The slice a plan's kept rows form (when check_rows found nothing): each dimension's kept members."""
    import numpy as np
    rs = S["_row_series"]
    pos = np.asarray([int(p) for p in positions if 0 <= int(p) < len(rs)], dtype=np.int64)
    ser = np.unique(rs[pos][rs[pos] >= 0])
    w: Dict[str, Any] = {}
    for j, d in enumerate(S["dims"]):
        kept = sorted(set(int(x) for x in S["_SM"][ser, j]))
        if len(kept) == len(d["labels"]) and d["role"] == "flat_additive":
            w[d["column"]] = "*"
        elif d["role"] == "parts" and sorted(kept) == sorted(d["part_index"]):
            w[d["column"]] = PARTS_TOKEN
        elif len(kept) == 1:
            w[d["column"]] = d["labels"][kept[0]]
        else:
            w[d["column"]] = [d["labels"][m] for m in kept]
    return w


# --------------------------------------------------------------------------------------------- figures
def windows(months: Sequence[str], values: Any) -> Optional[Dict[str, List[str]]]:
    """The core's comparison windows: the latest 12 months ending at the last month with a value, and the 12 before."""
    import numpy as np
    have = [m for m, v in zip(months, values) if v == v]
    if not have:
        return None
    last = max(have)
    y, mo = int(last[:4]), int(last[5:7])

    def shift(k: int) -> str:
        i = y * 12 + (mo - 1) + k
        return "%04d-%02d" % (i // 12, i % 12 + 1)
    return {"latest": [shift(-11), last], "prior": [shift(-23), shift(-12)]}


def _monthly(S: Dict[str, Any], where: Dict[str, Any]) -> Tuple[List[str], Any]:
    """The slice by month: a flow or a count added up over a month's dates, a level averaged."""
    import numpy as np
    times, val, _b = series(S, where)
    months = sorted({t[:7] for t in times})
    out = np.full(len(months), np.nan)
    pos = {m: i for i, m in enumerate(months)}
    acc: Dict[int, List[float]] = {}
    for t, v in zip(times, val):
        if v == v:
            acc.setdefault(pos[t[:7]], []).append(float(v))
    flow = sums_over_time(S["measure"])
    for i, xs in acc.items():
        out[i] = math.fsum(xs) if flow else math.fsum(xs) / len(xs)
    return months, out


def support(S: Dict[str, Any], where: Dict[str, Any]) -> Tuple[List[str], Any]:
    """(months, how many published parts the slice's figure adds up from in each month): the best of the dimensions whose
    total the slice is (a province's industry total adds up from its 9 industries, Canada's seasonally adjusted total
    from its 13 provinces), counting the parts that have a value that month. Zero for a figure no sum-check
    decomposes. A published figure rests on its table row alone; this is how many sum-checked parts stand behind it."""
    import numpy as np
    months = sorted({t[:7] for t in S["_times"]})
    best = np.zeros(len(months), dtype=int)
    for d in S["dims"]:
        if d["role"] not in ("partition", "hierarchy") or where.get(d["column"]) != d.get("total"):
            continue
        bd = next((b for b in S.get("breakdowns") or [] if b["dim"] == d["column"]), None)
        if not bd:
            continue
        cnt = np.zeros(len(months), dtype=int)
        for p in bd["parts"]:
            _m, v = _monthly(S, dict(where, **{d["column"]: p}))
            cnt += (~np.isnan(v)).astype(int)
        best = np.maximum(best, cnt)
    return months, best


def window_figure(S: Dict[str, Any], months: Sequence[str], vals: Any, w: Sequence[str]) -> Tuple[Optional[float], int]:
    """A window's figure (a flow's 12-month total, a level's 12-month mean) and how many of its months hold a value."""
    xs = [float(v) for m, v in zip(months, vals) if w[0] <= m <= w[1] and v == v]
    if not xs:
        return None, 0
    if sums_over_time(S["measure"]):
        return math.fsum(xs), len(xs)
    return math.fsum(xs) / len(xs), len(xs)


MIN_MATCHED = 6                 # months with a value in both windows a flow's comparison needs


def _wlen(S: Dict[str, Any]) -> int:
    """Periods in a window: 12 months, 4 quarters, 2 half-years or 1 year."""
    return int((S.get("period") or {}).get("window") or 12)


def _min_matched(S: Dict[str, Any]) -> int:
    """Periods with a value in both windows a flow's comparison needs: half of a window (6 months, 2 quarters, 1 year)."""
    return max(1, _wlen(S) // 2) if _wlen(S) != 12 else MIN_MATCHED


def _prange(S: Dict[str, Any], a: str, b: str) -> List[str]:
    """The periods from a to b (month keys): every month of a monthly table, the quarters or years a 12-month span holds
    otherwise (a period is keyed by the month it starts in, at the table's phase)."""
    st = (S.get("period") or {}).get("step", 1)
    if st == 1:
        return _mrange(a, b)
    ph = (S.get("period") or {}).get("phase", 0)
    return [m for m in _mrange(a, b) if (int(m[5:7]) - 1) % st == ph]


def _comparison(S: Dict[str, Any], win: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """A window's endpoints: its two month keys for a monthly table, its first and last PERIOD for another (the quarters or
    the year a 12-month span holds), so a range of the endpoints holds exactly the periods compared."""
    if (S.get("period") or {}).get("step", 1) == 1:
        return {"latest": list(win["latest"]), "prior": list(win["prior"])}
    out = {}
    for k in ("latest", "prior"):
        ps = _prange(S, win[k][0], win[k][1])
        out[k] = [ps[0], ps[-1]] if ps else list(win[k])
    return out


def pkey(S: Dict[str, Any], key: str) -> str:
    """A period key in words: the month key itself for a monthly table (2022-11), Q4 2022 / 2022 otherwise."""
    return key if (S.get("period") or {}).get("step", 1) == 1 else _plabel(S, key)


def period_words(S: Dict[str, Any], used: Optional[int] = None) -> Dict[str, str]:
    """The words of a comparison in the table's own period: "the latest 12 months against the 12 before", "the 4 quarters
    before", "the latest year" ...; with `used` (a flow whose windows are matched on the periods both have) "the 3 matched
    quarters before". A monthly table's words are the ones the structure's reports have always used."""
    p = S.get("period") or {"noun": "month", "nouns": "months", "window": 12}
    w, noun, nouns = int(p["window"]), str(p["noun"]), str(p["nouns"])
    span = noun if w == 1 else "%d %s" % (w, nouns)
    latest = "the latest %s" % span
    before = "the %s before" % noun if w == 1 else "the %d before" % w
    if used is None or used == w:
        return {"latest": latest, "prior": "the %s before" % span, "against": "%s against %s" % (latest, before),
                "noun": noun, "nouns": nouns}
    nn = nouns if used != 1 else noun
    return {"latest": "the %d matched latest %s" % (used, nn), "prior": "the %d matched %s before" % (used, nn),
            "against": "the %d %s with a value in both %s and %s" % (used, nn, latest, before), "noun": noun, "nouns": nouns}


def _mshift(ym: str, k: int) -> str:
    i = int(ym[:4]) * 12 + (int(ym[5:7]) - 1) + k
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def _mrange(a: str, b: str) -> List[str]:
    out, m = [], a
    while m <= b and len(out) < 600:
        out.append(m)
        m = _mshift(m, 1)
    return out


def matched_months(S: Dict[str, Any], months: Sequence[str], tot: Any, win: Dict[str, List[str]]
                   ) -> Tuple[List[str], List[str], bool]:
    """(the latest window's months, the months a year before, whether both are the whole 12): a flow's or a count's
    window figure adds up its months, so a month the headline lacks in either window would make the change compare 11
    months with 12; the comparison then uses the months with a value in both windows (the same calendar month in each:
    like for like). A level's window figure is the mean of the months it has, so its windows stay whole."""
    lat = _prange(S, win["latest"][0], win["latest"][1])
    pri = _prange(S, win["prior"][0], win["prior"][1])
    if not sums_over_time(S["measure"]) or len(lat) != len(pri):
        return lat, pri, True
    have = {m for m, v in zip(months, tot) if v == v}
    pairs = [(a, b) for a, b in zip(pri, lat) if a in have and b in have]
    full = len(pairs) == len(lat) == len(pri) == _wlen(S)
    return [b for _a, b in pairs], [a for a, _b in pairs], full


def sum_at(S: Dict[str, Any], months: Sequence[str], vals: Any, at: Sequence[str]) -> Tuple[Optional[float], int]:
    """A figure over the given months: a flow's or a count's sum, a level's mean, and how many of them hold a value."""
    want = set(at)
    xs = [float(v) for m, v in zip(months, vals) if m in want and v == v]
    if not xs:
        return None, 0
    if sums_over_time(S["measure"]):
        return math.fsum(xs), len(xs)
    return math.fsum(xs) / len(xs), len(xs)


_SUFFIX = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def money(v: Optional[float], S: Dict[str, Any], signed: bool = False, ref: Optional[float] = None,
          exact_small: bool = False) -> str:
    """A figure in the measure's unit: "$834.7B", "+$29.3B", "−$1.2M", "12.4M" (a count), "5.2%" (a rate).
    exact_small (a gap, never a headline figure): an amount that would read as zero in the scale of `ref` is written out
    in whole units, "$1,000" and "−$3,000", not "$0.0B" (final integration pass, 6 Oct 2026)."""
    if v is None or v != v:
        return "n/a"
    t = S["measure"]["type"]
    cur = "$" if S["measure"].get("currency") and re.search(r"(?i)dollar|\$|CAD|USD", str(S["measure"].get("uom") or
                                                                                          "")) else ""
    if S["measure"].get("currency") and not cur:
        cur = ""
    x = abs(float(v))
    r = abs(float(ref)) if ref is not None else x
    if t in ("rate", "index"):
        body = "%.4g" % x + ("%" if re.search(r"(?i)percent|%", str(S["measure"].get("uom") or "")) else "")
    else:
        body = None
        for lim, suf in _SUFFIX:
            if r >= lim:
                body = "%.1f%s" % (x / lim, suf)
                break
        if body is None:
            body = "%.0f" % x if x >= 1 else "%.3g" % x
        elif exact_small and x > 0 and not re.search(r"[1-9]", body):
            body = format(int(round(x)), ",") if x >= 1 else "%.3g" % x
        if exact_small and x == 0:
            body = "0"                                      # a residual that is exactly nothing is "$0", not "$0.0B"
    if not re.search(r"[1-9]", body):
        v = 0.0                               # rounds to zero: no sign ("$0.0B", never "−$0.0B")
    sign = ("+" if v > 0 else "−" if v < 0 else "") if signed else ("−" if v < 0 else "")
    unit = ""
    if not cur and S["measure"].get("currency"):
        unit = " " + str(S["measure"].get("uom") or "")
    return sign + cur + body + unit


def _percent_rate(S: Dict[str, Any]) -> bool:
    return S["measure"]["type"] == "rate" and bool(re.search(r"(?i)percent|%", str(S["measure"].get("uom") or "")))


def points(v: Optional[float]) -> str:
    """A change of a percentage: "+0.617 percentage points" (never "%", which would read as a relative change)."""
    if v is None or v != v:
        return "n/a"
    body = "%.3g" % abs(v)
    if not re.search(r"[1-9]", body):
        return "0 percentage points"
    return ("+" if v > 0 else "\u2212") + body + " percentage points"


def pct(v: Optional[float], signed: bool = True) -> str:
    if v is None or v != v:
        return "n/a"
    s = "%.1f%%" % abs(v)
    if signed:
        return ("+" if v > 0 else "−" if v < 0 else "") + s
    return ("−" if v < 0 else "") + s


def noise_zero(v: float, *figures: Optional[float]) -> float:
    """A residual that is float noise is exactly 0 (and never -0.0): it is at most a millionth of a millionth of the largest of
    the figures it is part of (a count averaged over a window leaves -3.41e-13 beside a total of 5e4), and at most 1e-12 itself
    when the figures are small. A real residual, however small beside its total (the publisher's rounding, $1,000 of $864B), is
    kept as it is: it is millions of times the noise."""
    scale = max([1.0] + [abs(float(f)) for f in figures if f is not None])
    return 0.0 if abs(v) <= FLOAT_NOISE * scale else v


def breakdown(S: Dict[str, Any], bd: Dict[str, Any], where: Dict[str, Any], win: Dict[str, List[str]]
              ) -> Optional[Dict[str, Any]]:
    """One breakdown in the headline's windows: per part its two window figures, its contribution to the change, its own
    growth (when its 24 months are all published), its share of the latest level, and its share of the change (only when
    every part moved the way the total did); the UNALLOCATED part (the total less the parts with a value: the suppressed
    share) per window. The parts and the unallocated add up to the change exactly (math.fsum)."""
    import numpy as np
    months, tot = _monthly(S, where)
    lat_m, pri_m, _full = matched_months(S, months, tot, win)
    T0, n0 = sum_at(S, months, tot, pri_m)
    T1, n1 = sum_at(S, months, tot, lat_m)
    flow = sums_over_time(S["measure"])
    need = _min_matched(S) if flow else _wlen(S)
    if T0 is None or T1 is None or n0 < need or n1 < need or (not flow and (n0 < _wlen(S) or n1 < _wlen(S))):
        return None
    change = T1 - T0
    parts = []
    sums0, sums1 = [], []
    for p in bd["parts"]:
        m2, pv = _monthly(S, dict(where, **{bd["dim"]: p}))
        a, na = sum_at(S, m2, pv, pri_m)
        b, nb = sum_at(S, m2, pv, lat_m)
        if not flow:
            # a stock's window mean: its months' values over the 12 months (a blank month is unallocated)
            a = math.fsum(float(v) for m, v in zip(m2, pv) if win["prior"][0] <= m <= win["prior"][1] and v == v) / _wlen(S)
            b = math.fsum(float(v) for m, v in zip(m2, pv) if win["latest"][0] <= m <= win["latest"][1] and v == v) / _wlen(S)
        a = a or 0.0
        b = b or 0.0
        sums0.append(a)
        sums1.append(b)
        complete = na == len(pri_m) and nb == len(lat_m)
        parts.append({"member": p, "prior": a, "latest": b, "contribution": b - a, "complete": complete,
                      "growth_pct": (100.0 * (b / a - 1.0)) if complete and a > 0 else None,
                      "share_level_pct": 100.0 * b / T1 if T1 else None})
    u0 = noise_zero(T0 - math.fsum(sums0), T0, T1)
    u1 = noise_zero(T1 - math.fsum(sums1), T0, T1)
    contribs = [p["contribution"] for p in parts]
    u_contrib = noise_zero(change - math.fsum(contribs), change, T0, T1)
    same = all((c >= 0) == (change >= 0) for c in contribs if abs(c) > 0) and abs(change) >= 0.01 * abs(T0)
    for p in parts:
        p["share_change_pct"] = (100.0 * p["contribution"] / change) if same and change else None
    parts.sort(key=lambda p: (-abs(p["contribution"]), p["member"]))
    ok = abs(math.fsum(contribs + [u_contrib]) - change) <= RECONCILE_TOL * max(1.0, abs(change), abs(T1))
    return {"id": bd["id"], "dim": bd["dim"], "parent": bd["parent"], "depth": bd.get("depth", 1),
            "prior": T0, "latest": T1, "change": change, "parts": parts,
            "unallocated": {"prior": u0, "latest": u1, "contribution": u_contrib},
            "suppressed_parts": sum(1 for p in parts if not p["complete"]),
            "shares_given": bool(same), "reconciles": bool(ok), "months": len(lat_m)}


def _label_of(S: Dict[str, Any], where: Dict[str, Any]) -> List[str]:
    out = []
    for d in S["dims"]:
        w = where.get(d["column"])
        if d["role"] == "constant":
            continue
        if d["role"] == "parts" and w == PARTS_TOKEN:
            out.append("the sum of %d %s" % (len(d.get("parts") or []), d.get("noun") or "members"))
        elif w == "*":
            out.append("all %s" % d["column"])
        elif isinstance(w, list):
            out.append("%d %s members" % (len(w), d["column"]))
        elif w is not None:
            out.append(str(w))
    return out


def measure_choice(S: Dict[str, Any], where: Dict[str, Any], plan_source: str = "engine_default") -> Optional[Dict[str, Any]]:
    """estimand.measure_choice for a table whose members are different measures: the one measure shown, whether the engine's
    default order chose it or the plan did, the rule, and the others (left out and why). None for any other table."""
    for d in S["dims"]:
        w = where.get(d["column"])
        if not (d.get("measure_dim") and isinstance(w, str)):
            continue
        ch = next((m for m in d["measures"] if m["name"] == w), None)
        if ch is None:
            return None
        alts = [{"id": m["id"], "name": m["name"], "uom": m["uom"], "type": m["type"], "precision": m["precision"],
                 "why": ("the precision of another member: never the headline, never summed" if m["precision"] else
                         "another measure: one measure is shown, never mixed with this one")}
                for m in d["measures"] if m["id"] != ch["id"]]
        rec = lambda m: {k: m[k] for k in ("id", "name", "uom", "type", "type_basis")}
        return {"dim": d["column"], "chosen": rec(ch), "by": "plan" if plan_source == "ai" else "default",
                "rule": MEASURE_RULE, "alternatives": alts}
    return None


def parts_info(S: Dict[str, Any], where: Dict[str, Any], win: Dict[str, List[str]]) -> Optional[Dict[str, Any]]:
    """estimand.built_from for a slice that sums the parts of a dimension that has no total row: how many parts, whether only
    the months where every part has a value were summed, which months a suppressed part left out, how many part-months are
    suppressed in the two comparison windows (a part with no value that month), and whether the sum is incomplete (the
    reported parts were summed, some being suppressed). None for any other slice."""
    import numpy as np
    ds = [d for d in S["dims"] if d.get("role") == "parts" and where.get(d["column"]) == PARTS_TOKEN]
    if not ds or win is None:
        return None
    d = ds[0]
    n = len(d["parts"])
    noun = d.get("noun") or "members"
    sel = _select(S, where)
    if sel is None or not len(sel):
        return None
    times = S["_times"]
    _V, H = _parts_cells(S, where, sel)                     # a combined member that stands in for a blank part counts the part as present
    at: Dict[str, List[int]] = {}
    for i, t in enumerate(times):
        at.setdefault(t[:7], []).append(i)
    has = {m: H[:, idx].any(axis=1) for m, idx in at.items() if m >= win["prior"][0]}     # series with a value
    wmonths = set(_prange(S, win["latest"][0], win["latest"][1])) | set(_prange(S, win["prior"][0], win["prior"][1]))
    suppressed = sum(int((~h).sum()) for m, h in has.items() if m in wmonths and h.any())
    dropped = sorted(m for m, h in has.items() if h.any() and not h.all())
    last = max((m for m, h in has.items() if h.any()), default=None)
    complete_only = bool(d.get("complete_only"))
    return {"dim": d["column"], "noun": noun, "n": n, "text": "built from %d %s; this table has no total row" % (n, noun),
            "complete_months_only": complete_only, "incomplete": bool((not complete_only) and suppressed > 0),
            "suppressed_part_months": int(suppressed), "months_dropped": dropped if complete_only else [],
            "latest_month_in_table": last, "combined": sorted((d.get("combined") or {}).keys()),
            "duplicates": sorted((d.get("duplicates") or {}).keys()), "dims": [x["column"] for x in ds]}


def estimand(S: Dict[str, Any], where: Dict[str, Any], win: Dict[str, List[str]], plan_source: str = "engine_default",
             why: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """rep["estimand"]: what the headline is (the slice, the measure, the unit and scale, the windows), its figures in
    base units with their texts, each sum-check behind the slice, and what was left out and why."""
    months, vals = _monthly(S, where)
    lat_m, pri_m, complete = matched_months(S, months, vals, win)
    T0, n0 = sum_at(S, months, vals, pri_m)
    T1, n1 = sum_at(S, months, vals, lat_m)
    m = S["measure"]
    flow = sums_over_time(m)
    if flow and len(lat_m) < _min_matched(S):
        T0 = T1 = None                                   # too few months in both windows to compare like for like
    left_out = sorted(set(_prange(S, win["latest"][0], win["latest"][1])) - set(lat_m))
    P = S.get("period") or {"step": 1, "noun": "month", "nouns": "months", "window": 12, "adjective": "monthly", "kind": "month"}
    cmp_ = _comparison(S, win)
    ambiguous = str(m.get("type_basis") or "").startswith("ambiguous")
    w_ = int(P["window"])
    unit_w = "%d-%s" % (w_, P["noun"]) if w_ > 1 else "annual"
    agg = ("%s totals" % unit_w if complete else "totals of the %d %s with a value in both windows (%s left out)" % (
        len(lat_m), P["nouns"] if len(lat_m) != 1 else P["noun"],
        ", ".join(_mon(x) if P["step"] == 1 else _plabel(S, x) for x in left_out[:3]) + (", ..." if len(left_out) > 3 else ""))) \
        if flow else \
        (("average level over the window (%s averages)" % unit_w if w_ > 1 else "average level over the window (annual values)")
         if ambiguous else ("%s averages" % unit_w if w_ > 1 else "annual values"))
    scale_txt = ""
    sc_word, sc_factor = slice_scale(S, where)
    if sc_factor and sc_factor != 1:
        scale_txt = " (file in %s ×%s)" % (sc_word, format(int(sc_factor), ","))
    unit = (str(m.get("uom") or "") or m["type"]).lower()
    choice = measure_choice(S, where, plan_source)
    built = parts_info(S, where, win)
    built_txt = ""
    if choice:
        built_txt = "one measure shown: %s, chosen %s; " % (
            choice["chosen"]["name"], "by the plan" if choice["by"] == "plan" else "by the engine's default order (%s)" % MEASURE_RULE)
    if built:
        bits = [built["text"]]
        cells = "region-months" if built["noun"] == "regions" else "member-months"
        if built["incomplete"]:
            bits.append("%s %s suppressed in the two windows, so this sum of the reported parts is incomplete" % (
                _fmt_count(built["suppressed_part_months"]), cells))
        elif built["complete_months_only"] and built["months_dropped"]:
            bits.append("complete months only: the %s months where a part is suppressed are left out" % _fmt_count(
                len(built["months_dropped"])))
        built_txt += "; ".join(bits) + "; "
    single_member = None
    for d in S["dims"]:
        # a dimension with no total member (a table of provinces with no Canada row; a rate, an index or a stock is never
        # added or averaged across them): one member is shown, and the estimand says so, with its name
        if d["role"] == "single" and d.get("no_total_member") and isinstance(where.get(d["column"]), str) \
                and where.get(d["column"]) == d.get("total"):
            what = "a national figure" if d.get("noun") == "national figure" else "the table's total"
            single_member = {"dim": d["column"], "member": d["total"], "noun": d.get("noun") or "total",
                             "statement": "one member shown: %s; this table has no total member, so this is not %s" % (d["total"], what)}
            built_txt += single_member["statement"] + "; "
    for d in S["dims"]:
        # wave 5c: a rate's or an index's aggregate that is one by its name alone (no sum-check can verify it): the estimand says so,
        # first, so that a long text cut at its cap loses the windows' words and not this
        if _named_unverified(d) and where.get(d["column"]) == d.get("total"):
            built_txt = "%s: %s; " % (d["total"], named_total_words(m["type"])) + built_txt
    def span(w: List[str]) -> str:
        a, b = (_mon(w[0]), _mon(w[1])) if P["step"] == 1 else (_plabel(S, w[0]), _plabel(S, w[1]))
        return a if a == b else "%s–%s" % (a, b)
    text = "%s; %s%s%s; %s %s vs %s" % (
        " · ".join(_label_of(S, where)) or "the whole table", built_txt, unit, scale_txt, agg,
        span(cmp_["latest"]), span(cmp_["prior"]))
    why = why or {}
    sl = []
    for d in S["dims"]:
        w = where.get(d["column"])
        if d["role"] == "constant":
            continue
        sl.append({"dim": d["column"], "member": ("the sum of %d %s" % (len(d.get("parts") or []), d.get("noun") or "members")
                                                  if d["role"] == "parts" and w == PARTS_TOKEN else w),
                   "role": d["role"], "why": why.get(d["column"]) or d.get("why") or ""})
    change = (T1 - T0) if T0 is not None and T1 is not None else None
    chg_pct = (100.0 * (T1 / T0 - 1.0)) if T0 and T1 is not None and T0 > 0 else None
    figures = {"prior": {"value": _r(T0), "text": money(T0, S), "months": n0},
               "latest": {"value": _r(T1), "text": money(T1, S), "months": n1},
               "change": {"value": _r(change), "text": points(change) if _percent_rate(S) else money(change, S, signed=True)},
               "change_pct": {"value": _r(chg_pct, 6), "text": pct(chg_pct)}}
    checks = []
    built = parts_info(S, where, win)
    for d in S["dims"]:
        if d["role"] == "parts" and where.get(d["column"]) == PARTS_TOKEN:
            bd = next((b for b in S.get("breakdowns") or [] if b["dim"] == d["column"]), None)
            res = breakdown(S, bd, where, win) if bd else None
            checks.append({"dim": d["column"], "total": None, "parts": len(d.get("parts") or []), "by": "parts",
                           "complete_cells": None, "within_tolerance": None, "max_rel_residual": None,
                           "max_residual": {"value": None, "text": "n/a"},
                           "suppressed_parts": res["suppressed_parts"] if res else None,
                           "unallocated_latest": {"value": 0.0, "text": "none: the headline is the sum of its parts"},
                           "unallocated_prior": {"value": 0.0, "text": "none: the headline is the sum of its parts"},
                           "verdict": "not possible (no total row)", "built_from_parts": True,
                           "suppressed_part_months": (built or {}).get("suppressed_part_months")})
            continue
        if d["role"] not in ("partition", "hierarchy") or where.get(d["column"]) != d.get("total"):
            continue
        bd = next((b for b in S.get("breakdowns") or [] if b["dim"] == d["column"]), None)
        res = breakdown(S, bd, where, win) if bd else None
        sc = d.get("sum_check") or {}
        mx = sc.get("max_rel_residual")
        checks.append({"dim": d["column"], "total": d["total"], "parts": len(d.get("parts") or []),
                       "by": d.get("by") or "flat", "complete_cells": sc.get("complete"),
                       "within_tolerance": sc.get("within"), "max_rel_residual": mx,
                       "max_residual": {"value": _r(sc.get("max_residual")),
                                        "text": money(sc.get("max_residual"), S) if sc.get("max_residual") is not None
                                        else "n/a"},
                       "suppressed_parts": res["suppressed_parts"] if res else None,
                       "unallocated_latest": {"value": _r(res["unallocated"]["latest"]) if res else None,
                                              "text": money(res["unallocated"]["latest"], S, ref=T1, exact_small=True)
                                              if res else "n/a"},
                       "unallocated_prior": {"value": _r(res["unallocated"]["prior"]) if res else None,
                                             "text": money(res["unallocated"]["prior"], S, ref=T1, exact_small=True)
                                             if res else "n/a"},
                       "verdict": "adds_up"})
    excluded = []
    for d in S["dims"]:
        w = where.get(d["column"])
        if d["role"] == "adjustment" and w == d.get("nsa"):
            excluded.append({"what": d["sa"], "dim": d["column"],
                             "why": "the seasonally adjusted copy of the same series: adding it would count every "
                                    "dollar twice; it is read for month-on-month momentum only"})
        for comp, parent in (d.get("components") or {}).items():
            if parent == w or (d["role"] in ("measure", "components") and w == d.get("total")):
                excluded.append({"what": comp, "dim": d["column"], "why": "a component of %s, already inside it" % parent})
        for alt, of in (d.get("alternatives") or {}).items():
            if d["role"] != "adjustment":
                excluded.append({"what": alt, "dim": d["column"], "why": "an alternative total (%s)" % of})
        if d.get("measure_dim") and isinstance(w, str):
            for mm in d["measures"]:
                if mm["name"] == w:
                    continue
                excluded.append({"what": mm["name"], "dim": d["column"], "why": (
                    "the precision of another member (a standard error, a margin of error or a confidence interval): never the "
                    "headline, never summed" if mm["precision"] else
                    "another measure (%s, %s): one measure is shown, never mixed with %s" % (mm["type"], mm["uom"] or "its own units", w))})
        if d["role"] == "parts" and w == PARTS_TOKEN:
            for cmb, fam in (d.get("combined") or {}).items():
                excluded.append({"what": cmb, "dim": d["column"],
                                 "why": "equals the sum of %s (each also a member): left out of the sum, never added to "
                                        "its own parts" % _join_names(fam)})
            for x, keep in (d.get("duplicates") or {}).items():
                excluded.append({"what": x, "dim": d["column"],
                                 "why": "the same series as %s: left out, never counted twice" % keep})
            for x, parent in (d.get("nested") or {}).items():
                excluded.append({"what": x, "dim": d["column"],
                                 "why": "a part of %s, which is also a member: left out, never added to it" % parent})
            for alt in (d.get("alternatives") or {}):
                excluded.append({"what": alt, "dim": d["column"],
                                 "why": "an alternative total (it says it leaves something out): never added to the parts"})
        if d["role"] in ("partition", "hierarchy") and w == d.get("total"):
            n = len(d["labels"]) - 1
            excluded.append({"what": "%d other members" % n, "dim": d["column"],
                             "why": "parts of %s (sum-checked); shown as a breakdown, never added to it" % d["total"]})
        if d["role"] == "rate_aggregate" and w == d.get("total"):
            excluded.append({"what": "%d other members" % (len(d["labels"]) - 1), "dim": d["column"],
                             "why": "each member's own %s; never added or averaged across members%s" % (
                                 S["measure"]["type"], ("; %s is %s" % (d["total"], named_total_words(m["type"])))
                                 if _named_unverified(d) else "")})
        if d["role"] == "single":
            if d.get("single_by") == "dominance":
                excluded.append({"what": "%d other members" % (len(d["labels"]) - 1), "dim": d["column"],
                                 "why": "one member shown, not a %s: this table has no total %s, and a %s is never added "
                                        "or averaged across members" % (d.get("noun") or "total",
                                                                        "member" if d.get("no_total_member") else "row", m["type"]
                                                                        if m["type"] != "count" else "count")})
            else:
                excluded.append({"what": "%d other members" % (len(d["labels"]) - 1), "dim": d["column"],
                                 "why": "no total was verified, so members are never added across this dimension"})
    out = {"text": text, "slice": sl,
            "measure": {"label": _measure_name(S, where), "uom": m.get("uom"), "scale": sc_word,
                        "scale_applied": sc_factor, "type": m["type"], "type_basis": m.get("type_basis"),
                        "type_why": m.get("type_why"),
                        "aggregation": str(m["aggregation"]).replace("over months", "over %s" % P["nouns"])},
            "comparison": cmp_, "period": {k: P[k] for k in ("kind", "noun", "nouns", "step", "window", "adjective")},
            "figures": figures, "sum_checks": checks, "excluded": excluded, "plan_source": plan_source,
            "complete": bool(complete) and not (built or {}).get("incomplete"), "months_used": len(lat_m) if flow else n1,
            "periods_used": len(lat_m) if flow else n1,
            "months_left_out": [] if complete else left_out, "inference": None, "built_from": built,
            "measure_choice": choice}
    if single_member is not None:
        out["single_member"] = single_member           # only when it applies, so every other estimand keeps its keys
    return out


def _measure_name(S: Dict[str, Any], where: Dict[str, Any]) -> str:
    """What the headline measures, in the reader's words: the slice's member of a measure dimension ("Total retail
    sales"), else the measure column's name ("VALUE")."""
    for d in S["dims"]:
        if d["role"] in ("measure", "components") and isinstance(where.get(d["column"]), str):
            return str(where[d["column"]])
    # a table's one constant label ("Population") names a count or a stock; a currency flow keeps the measure column's name, as
    # it always has (the report's headline of a table with no label dimension reads "VALUE, Total, ...")
    return str((S["measure"].get("label_hint") if not S["measure"].get("currency") else "") or S["measure"]["column"])


def _r(v: Optional[float], nd: int = 6) -> Optional[float]:
    if v is None or v != v or abs(v) == float("inf"):
        return None
    return round(float(v), nd)


def _mon(ym: str) -> str:
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    try:
        return "%s %s" % (names[int(ym[5:7]) - 1], ym[:4])
    except (ValueError, IndexError):
        return str(ym)


# --------------------------------------------------------------------------------------------- public blocks
def public(S: Dict[str, Any]) -> Dict[str, Any]:
    """rep["structure"]: what the table is, in plain JSON (no arrays)."""
    if S is None:
        return None
    dims = []
    for d in S.get("dims") or []:
        x: Dict[str, Any] = {"column": d["column"], "role": d["role"], "members": len(d["labels"])}
        for k in ("total", "nsa", "sa", "by", "why", "single_by", "noun", "no_total_member", "aggregate_by"):
            if d.get(k) is not None:
                x[k] = d[k]
        if d.get("parts") is not None and d["role"] in ("partition", "hierarchy", "rate_aggregate", "parts"):
            x["parts"] = len(d["parts"])
        if d["role"] == "parts":
            x["no_total"] = True
            for k in ("combined", "duplicates", "nested"):
                if d.get(k):
                    x[k] = dict(d[k])
            x["complete_only"] = bool(d.get("complete_only"))
        if d["role"] == "hierarchy":
            depth = d.get("depth") or {}
            levels: Dict[int, int] = {}
            for m, dp in depth.items():
                if dp >= 1:
                    levels[dp] = levels.get(dp, 0) + 1
            x["depths"] = [levels[k] for k in sorted(levels)]
        if d.get("components"):
            x["component_of"] = dict(d["components"])
        if d.get("alternatives"):
            x["alternatives"] = dict(d["alternatives"])
        if d.get("sum_check"):
            x["sum_check"] = dict(d["sum_check"])
        if d.get("sa_adds_up") is not None:
            x["sa_adds_up"] = d["sa_adds_up"]
        if d.get("unit_of"):
            x["unit_of"] = dict(d["unit_of"])
        if d.get("measure_dim"):
            x["measures"] = [{k: m[k] for k in ("id", "name", "uom", "type", "type_basis", "precision", "default")}
                             for m in d["measures"]]
        dims.append(x)
    out = {"kind": S["kind"], "usable": bool(S.get("usable")), "reason": S.get("reason") or "",
           "version": S.get("version"), "publisher": S.get("publisher"), "official": bool(S.get("official")),
           "series": S.get("series"), "months": S.get("months"), "rows": S.get("rows"),
           "date": (S.get("date") or {}).get("column"), "period": dict(S["period"]) if S.get("period") else None,
           "measure": ({k: (str(v).replace("over months", "over %s" % (S.get("period") or {}).get("nouns", "months"))
                            if k == "aggregation" else v) for k, v in (S.get("measure") or {}).items() if k != "landed"}
                       if S.get("measure") else None),
           "metadata": [_public_meta(m) for m in S.get("metadata") or []],
           "flag_column": (S.get("flags") or {}).get("column"), "dims": dims,
           "slices": [{k: v for k, v in s.items()} for s in S.get("slices") or []],
           "breakdowns": [dict(b) for b in S.get("breakdowns") or []],
           "flags": S.get("flags"), "corrections": list(S.get("corrections") or []), "keys": list(S.get("keys") or []),
           "wide": dict(S["wide"]) if S.get("wide") else None,
           "hash": S.get("hash"), "detect_seconds": S.get("detect_seconds")}
    return out


def _public_meta(m: Dict[str, Any]) -> Dict[str, Any]:
    """A metadata column as the report carries it. The VALUE of a constant column is echoed only for the metadata the layer understands
    (UOM, SCALAR_FACTOR, DECIMALS ... : a unit, a scale word, a code); for any other column that holds one value (an analyst's name, a note
    the scan did not flag) the column's name and class are listed and its value is not (wave 5e, P12: the scan's blind spot is not
    printed)."""
    out = {k: v for k, v in m.items() if k != "landed"}
    if "value" in out and _norm(m.get("column")) not in _META:
        out.pop("value")
        out["value_not_shown"] = True
    return out


def structure_hash(S: Dict[str, Any]) -> str:
    """A hash of what was detected (roles, totals, trees, slices): equal across runs on the same table."""
    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {str(k): walk(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0])) if not str(k).startswith("_")
                    and k not in ("hash", "detect_seconds")}
        if isinstance(x, (list, tuple)):
            return [walk(v) for v in x]
        if isinstance(x, float):
            return round(x, 9)
        if hasattr(x, "item"):
            return walk(x.item())
        return x
    keep = {k: S.get(k) for k in ("kind", "publisher", "official", "date", "measure", "metadata", "series", "months",
                                  "slices", "breakdowns")}
    keep["dims"] = [{k: v for k, v in d.items() if k not in ("labels",)} for d in S.get("dims") or []]
    return hashlib.sha256(json.dumps(walk(keep), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def profile_block(S: Dict[str, Any], values_of: Dict[str, Sequence[str]], cap: int = PROFILE_CAP,
                  columns: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
    """profile.structure for the planner (at most `cap` bytes): only the dimensions the profile shows, and only member
    strings that column's own profile values hold (cut at 60 characters, as the profile cuts them). Trimmed in order:
    the rules, the drill-downs, the member lists; else dropped (None)."""
    if not S or not S.get("usable"):
        return None
    cols_all = set(str(c) for c in columns) if columns is not None else None

    def mem(col: str, x: Any) -> Optional[str]:
        if x is None:
            return None
        s = str(x)[:PROFILE_VALUE_MAX]
        return s if s in set(values_of.get(col) or ()) else None
    dims = []
    for d in S["dims"]:
        col = d["column"]
        if col not in values_of or d["role"] == "constant":
            continue
        # the worker's vocabulary of roles (insight-proxy src/plan.js STRUCTURE_ROLES): a table's published aggregate of a
        # rate is "aggregate"; a dimension with no total row is a "partition" with no total and `no_total` set
        x: Dict[str, Any] = {"column": col, "role": {"rate_aggregate": "aggregate", "parts": "partition"}.get(d["role"], d["role"]),
                             "members": len(d["labels"])}
        if d["role"] == "parts":
            x["no_total"] = True
            x["parts"] = len(d.get("parts") or [])
        if d.get("total") is not None and mem(col, d["total"]):
            x["total"] = mem(col, d["total"])
        if d["role"] in ("partition", "hierarchy"):
            x["parts"] = len(d.get("parts") or [])
        if d["role"] == "hierarchy":
            depth = d.get("depth") or {}
            levels: Dict[int, int] = {}
            for _m, dp in depth.items():
                if dp >= 1:
                    levels[dp] = levels.get(dp, 0) + 1
            x["depths"] = [levels[k] for k in sorted(levels)]
        if d["role"] == "adjustment":
            if mem(col, d["nsa"]):
                x["nsa"] = mem(col, d["nsa"])
            if mem(col, d["sa"]):
                x["sa"] = mem(col, d["sa"])
        comps = {mem(col, a): mem(col, b) for a, b in (d.get("components") or {}).items() if mem(col, a) and mem(col, b)}
        if comps:
            x["component_of"] = comps
        if d.get("components") and len(comps) < len(d["components"]):
            x["components"] = len(d["components"])
        alts = [mem(col, a) for a in (d.get("alternatives") or {}) if mem(col, a)]
        if alts and d["role"] != "adjustment":
            x["alternatives"] = alts
        dims.append(x)
    shown = {x["column"] for x in dims}
    slices = []
    for s in S.get("slices") or []:
        w = {}
        ok = True
        for col, v in s["where"].items():
            if col not in shown:
                continue
            if v == "*":
                w[col] = "*"
            elif v == PARTS_TOKEN:
                continue                       # a dimension with no total row: its parts, added up (no member to name)
            elif isinstance(v, list):
                ok = False
            else:
                mv = mem(col, v)
                if mv is None:
                    ok = False
                else:
                    w[col] = mv
        if ok:
            slices.append({"id": s["id"], "use": s["use"], "where": w, **({"default": True} if s.get("default") else {})})
    bds = [{"id": b["id"], "dim": b["dim"], "parts": len(b["parts"]), "depth": b.get("depth", 1)}
           for b in S.get("breakdowns") or [] if b["dim"] in shown]
    rules = []
    for d in S["dims"]:
        if d["column"] not in shown:
            continue
        tot = mem(d["column"], d.get("total"))
        if d["role"] == "partition" and tot:
            rules.append("never add rows across %s: %s is the sum of the other %d members" % (d["column"], tot,
                                                                                          len(d.get("parts") or [])))
        elif d["role"] == "parts":
            rules.append("%s has no total row: its %d parts are added up (never with a combined member); do not add rows "
                         "across it yourself" % (d["column"], len(d.get("parts") or [])))
        elif d["role"] == "hierarchy" and tot:
            rules.append("never add rows across %s: it holds nested levels under %s" % (d["column"], tot))
        elif d["role"] == "adjustment":
            rules.append("never add the two adjustments of %s: they are copies of the same series" % d["column"])
        elif d["role"] in ("measure", "components"):
            rules.append("never add the members of %s: they are different measures or components" % d["column"])
        elif d["role"] == "rate_aggregate":
            rules.append("never add or average members of %s: a %s is published per member" % (d["column"],
                                                                                              S["measure"]["type"]))
        elif d["role"] == "single":
            rules.append("never add members of %s: no total was verified" % d["column"])
    rules.append("choose one slice id for the headline; the default slice is the engine's choice")
    m = S["measure"]
    measures = None
    for d in S["dims"]:
        if d.get("measure_dim") and d["column"] in shown:
            mem_ok = [x for x in d["measures"] if mem(d["column"], x["name"])]
            if len(mem_ok) == len(d["measures"]):
                measures = {"dim": d["column"], "members": [
                    {"id": x["id"], "name": mem(d["column"], x["name"]), "uom": x["uom"] or None, "type": x["type"],
                     "default": bool(x["default"]), "precision": bool(x["precision"])} for x in d["measures"]]}
                rules.append("a measure dimension: show one member at a time (name its id as measure_member), never add or "
                             "average its members; a precision member is never the headline")
    out = {"kind": "cube", "publisher": S.get("publisher"), "series": S.get("series"), "months": S.get("months"),
           "date": S["date"]["column"],
           "measure": {"column": m["column"], "type": m["type"], "uom": m.get("uom"), "scale": m.get("scale")},
           "metadata": [x["column"] for x in S.get("metadata") or [] if cols_all is None or x["column"] in cols_all][:20],
           "flag_column": (S.get("flags") or {}).get("column"),
           "dims": dims, "slices": slices, "breakdowns": bds, "rules": rules}
    if measures:
        out["measures"] = measures
    if S.get("period") and int(S["period"].get("step") or 1) != 1:
        out["period"] = {"kind": S["period"]["kind"], "window": S["period"]["window"]}
    if cols_all is not None and out["flag_column"] not in cols_all:
        out["flag_column"] = None

    def size(o: Any) -> int:
        return len(json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if size(out) > cap:
        out["rules"] = out["rules"][-1:]
    if size(out) > cap:
        out["slices"] = [s for s in out["slices"] if s["use"] in ("headline", "momentum")]
        out["breakdowns"] = [b for b in out["breakdowns"] if b.get("depth", 1) == 1]
    if size(out) > cap:
        for x in out["dims"]:
            x.pop("component_of", None)
            x.pop("alternatives", None)
        out["metadata"] = []
    if size(out) > cap:
        return None
    return out


def summary_for_ai(S: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The structure summary results_for_ai sends after the estimand: the dimensions and their roles, the slices used,
    the unallocated share; no arrays, no member lists beyond a total."""
    if not S:
        return None
    dims = [{k: d[k] for k in ("column", "role", "members", "total", "parts", "nsa", "sa", "depths") if k in d}
            for d in S.get("dims") or []]
    return {"kind": S.get("kind"), "publisher": S.get("publisher"), "series": S.get("series"),
            "months": S.get("months"), "dims": dims,
            "flags": {k: (S.get("flags") or {}).get(k) for k in ("column", "by_kind", "quality_of_headline")}
            if S.get("flags") else None,
            "corrections": list(S.get("corrections") or [])[:6]}
