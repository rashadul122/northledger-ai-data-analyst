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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

VERSION = "2026-10-01.1"
HERE = os.path.dirname(os.path.abspath(__file__))
FLAG_VOCAB_FILE = "flag_vocab.json"

BUDGET_S = 1.0                  # detect, timer-guarded
CODEFREE_BUDGET_S = 0.5         # the subset-sum search, inside that
MAX_SERIES = 20000
MAX_DIMS = 8
MAX_MEMBERS = 400               # a dimension has 2 to 400 members
DUP_MAX = 0.01                  # date x dimensions repeat on at most 1% of the rows
PASS_SHARE = 0.95               # a sum-check passes on 95% of its complete cells ...
MIN_COMPLETE = 6                # ... with at least 6 complete cells ...
MIN_MONTHS = 3                  # ... across at least 3 months
BOUND_SHARE = 0.99              # a member bounds another in 99% of the cells
CANDIDATES_MAX = 22             # the subset-sum search: at most 22 candidates (2^11 subsets a half)
PARENTS_MAX = 30
FLAG_MAX_CODES = 20
FLAG_CODE_LEN = 4
FLAG_BLANK_SHARE = 0.90         # a code whose rows have a blank measure this often stands for a missing value
ADJ_YEAR_TOL = 0.03             # an adjusted pair: calendar-year sums within 3% ...
ADJ_SEASONAL_RATIO = 3.0        # ... and one three times more seasonal than the other
ADJ_MAX_MEMBERS = 4
PROFILE_CAP = 6000              # the profile's structure block, bytes
PROFILE_VALUE_MAX = 60          # a member string as the profile lists it (nl_browser._profile_facts cuts at 60)
SLICES_MAX = 12
BREAKDOWNS_MAX = 4              # a plan names at most 4 breakdowns
SLICE_ID = re.compile(r"^S\d{1,2}$")
BREAKDOWN_ID = re.compile(r"^B\d{1,2}$")
RECONCILE_TOL = 1e-6

ROLES = ("partition", "hierarchy", "adjustment", "measure", "components", "flat_additive", "single", "rate_aggregate",
         "constant")
KINDS = ("cube", "cube_incomplete", "panel_no_relations", "not_cube")

# names (normalised: lower case, letters and digits only) that are hints, never proof
_META = frozenset((
    "dguid", "uom", "uomid", "scalarfactor", "scalarid", "vector", "coordinate", "status", "symbol",
    "terminated", "decimals", "obsstatus", "obsflag", "obsconf", "unitmult", "unitmultiplier",
    "confstatus", "unitmeasure", "unit", "units", "flag", "flags", "footnote", "footnotes",
    "timeformat", "freq", "frequency", "lastupdate", "dataflow", "structure", "structureid", "action"))
_UNIT_META = ("uom", "unit", "units", "unitmeasure")
_SCALE_WORDS = ("scalarfactor", "unitmult", "unitmultiplier", "multiplier")
_SCALE_IDS = ("scalarid",)
_DECIMALS = ("decimals",)
_FLAG_NAMES = ("status", "flag", "flags", "obsstatus", "obsflag", "confstatus", "obsconf", "symbol")
_DATE_NAMES = ("refdate", "timeperiod", "date", "period", "time", "referenceperiod")
_VALUE_NAMES = ("value", "obsvalue")
_TOTAL_HINT = re.compile(r"(?i)(?:^|\b)(?:total|all|overall|grand|aggregate|combined)(?:\b|$)|^\s*(?:_T|TOTAL|_Z)\s*$")
_ALT_HINT = re.compile(r"(?i)\b(?:excluding|except|ex\.|less|without|other than)\b")
_STOCK_WORDS = re.compile(r"(?i)\b(?:inventor(?:y|ies)|outstanding|balances?|holdings?|assets?|debts?|stocks?)\b")
_POP_WORDS = re.compile(r"(?i)\b(?:employment|employed|population|labour force|labor force|persons employed)\b")
_CURRENCY = re.compile(r"(?i)\b(?:dollars?|euros?|pounds?|yen|yuan|francs?|krona|kronor|krone|rupees?|pesos?|reais|"
                       r"real|rand|won|currency|canadian dollars|us dollars)\b|[$€£¥]|\b(?:CAD|USD|EUR|GBP|"
                       r"JPY|CNY|CHF|AUD|NZD|SEK|NOK|DKK|INR|MXN|BRL|ZAR|KRW)\b")
_RATE_WORDS = re.compile(r"(?i)\b(?:percent(?:age)?|rate|ratio|per\s+(?:cent|\d[\d,]*|capita|hour|person))\b|%")
_INDEX_WORDS = re.compile(r"(?i)\bindex\b|\b(?:19|20)\d\d\s*=\s*100\b")
_INDEX_BASE = re.compile(r"((?:19|20)\d\d(?:\s*[-/]\s*\d{2,4})?)\s*=\s*100")
_COUNT_UNITS = re.compile(r"(?i)\b(?:persons?|people|number|units?|count|households?|businesses|establishments|"
                          r"jobs|vehicles|dwellings|permits|births|deaths)\b")
_SCALE_FACTOR = {"units": 1.0, "unit": 1.0, "ones": 1.0, "tens": 10.0, "hundreds": 100.0, "thousand": 1e3,
                 "thousands": 1e3, "millions": 1e6, "million": 1e6, "billions": 1e9, "billion": 1e9,
                 "trillions": 1e12, "trillion": 1e12}
_BRACKET_CODE = re.compile(r"\[([0-9A-Za-z][0-9A-Za-z.\-]*)\]\s*$")
_LEAD_CODE = re.compile(r"^\s*([0-9][0-9A-Za-z]*(?:\.[0-9A-Za-z]+)*)\s+\S")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_EMBED = re.compile(r"^\s*(?P<num>[-+]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)?\s*"
                    r"(?P<flag>\[[A-Za-z]{1,2}\]|[A-Za-z]{1,2}|:|\.{2,3}|\*)?\s*$")
_SENSITIVE_PREFIX = "S"


class _Timer:
    def __init__(self, budget: float) -> None:
        self.t0 = time.perf_counter()
        self.budget = float(budget)

    def left(self) -> float:
        return self.budget - (time.perf_counter() - self.t0)

    def over(self) -> bool:
        return self.left() <= 0


def _norm(c: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).lower())


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


# --------------------------------------------------------------------------------------------- 1. cube roles
def _empty(kind: str, reason: str, **kw: Any) -> Dict[str, Any]:
    S = {"kind": kind, "usable": False, "reason": reason, "version": VERSION, "dims": [], "metadata": [],
         "slices": [], "breakdowns": [], "default": None, "flags": None, "publisher": None, "official": False}
    S.update(kw)
    return S


def detect(R: Any, hidden: Iterable[str] = (), budget_s: float = BUDGET_S, headers: Optional[Sequence[str]] = None
           ) -> Dict[str, Any]:
    """The structure of the table the engine read (R: nl_browser._Reading), from its landed, non-hidden columns only.
    Returns S: {kind, usable, reason, date, measure, metadata, dims, flags, slices, breakdowns, default, hash, ...}
    plus private arrays (keys starting with "_") the slices are cut from. Never raises on a table it cannot read: it
    returns kind "not_cube" with the reason."""
    tm = _Timer(budget_s)
    try:
        return _detect(R, set(str(h) for h in hidden or ()), tm, headers)
    except MemoryError:
        return _empty("not_cube", "the table is too large to read as a cube")


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
    # -- the date: the column with the most dates the engine reads
    date, n_d = None, 0
    for c in cols:
        if R.kind(c) == "date":
            k = int(R.dates(c).notna().sum())
            if k > n_d or (k == n_d and date is not None and _norm(head[c]) in _DATE_NAMES):
                date, n_d = c, k
    if date is None or n_d < 6:
        return _empty("not_cube", "no column holds dates", publisher=publisher, official=official)
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
        if 2 <= nl <= MAX_MEMBERS:
            dims0.append(c)
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
            # neither is metadata-named: the one with the more readable labels is the dimension
            la = sum(len(x) for x in cat[a][1]) / max(1, len(cat[a][1]))
            lb_ = sum(len(x) for x in cat[b][1]) / max(1, len(cat[b][1]))
            alias_of[b if la >= lb_ else a] = a if la >= lb_ else b
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
        why = ("the date and the columns the engine may read (%s) do not tell the rows apart: %s of %s rows repeat "
               "a date and a series, so a column that names the series is withheld or set aside"
               % (", ".join(head[d] for d in dims), _fmt_count(n_rows - n_pairs), _fmt_count(n_rows)))
        return _empty("cube_incomplete" if official else "not_cube", why, **base)
    n_series = int(s_index.shape[0])
    if n_series > MAX_SERIES:
        return _empty("not_cube", "more than %s series" % _fmt_count(MAX_SERIES), **base)
    # -- the tensor: V[series, time] in base units, E (a row exists), F (its flag code)
    torder = np.argsort(np.array(tlabels))
    trank = np.empty_like(torder)
    trank[torder] = np.arange(len(torder))
    times = [tlabels[i] for i in torder]
    tix = trank[tcode]
    scale_row, scale_info = _scale(R, cat, metadata, rows, head)
    v = vals[rows] * scale_row
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
        "official": official, "date": {"column": head[date], "landed": date},
        "measure": {"column": head[measure], "landed": measure, "embedded_flags": embedded,
                    "decimals": _decimals(R, measure, metadata), **scale_info, **unit_info["measure"]},
        "metadata": metadata, "dims": dimrecs, "series": n_series, "times": len(times), "months": len(months),
        "monthly": monthly, "rows": int(R.n), "rows_read": n_rows, "duplicates": int(n_rows - n_pairs),
        "slices": [], "breakdowns": [], "default": None, "flags": None, "corrections": [],
        "_V": V, "_E": E, "_F": F, "_flag_labels": fl_labels, "_flag_column": head[flag_col] if flag_col else
        ("%s (embedded)" % head[measure] if embedded else None),
        "_SM": s_index, "_times": times, "_months": [t[:7] for t in times],
        "_row_series": _row_map(R.n, rows, s_codes), "_row_time": _row_map(R.n, rows, tix),
        "_member_unit": unit_info["member_unit"],
    }
    _measure_type(S)
    # -- relations, dimension by dimension (adjustment pairs first: the other sum-checks run on the unadjusted member)
    for j in range(len(dims)):
        if tm.over():
            break
        _adjustment(S, j)
    for j in range(len(dims)):
        rec = S["dims"][j]
        if rec["role"]:
            continue
        if len(rec["labels"]) == 1:
            rec["role"] = "constant"
            continue
        if tm.over():
            rec["role"] = "unresolved"
            rec["why"] = "the time budget ran out"
            continue
        if rec.get("mixed_units"):
            _mixed_measure(S, j)
            continue
        if S["measure"]["type"] in ("rate", "index"):
            _rate_aggregate(S, j)
        else:
            _relations(S, j, tm)
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


def _scale(R: Any, cat: Dict[str, Any], metadata: List[Dict[str, Any]], rows: Any, head: Dict[str, str]
           ) -> Tuple[Any, Dict[str, Any]]:
    """Each row's scale factor (SCALAR_FACTOR "thousands", SCALAR_ID 3, UNIT_MULT 6), and what was read."""
    import numpy as np
    n = len(rows)
    for m in metadata:
        nm = _norm(m["column"])
        if m["class"] == "constant" and (nm in _SCALE_WORDS or nm in _SCALE_IDS):
            f = _scale_word(m["value"], nm in _SCALE_IDS or nm in ("unitmult", "unitmultiplier"))
            if f:
                return np.full(n, f), {"scale": str(m["value"]), "factor": f, "scale_column": m["column"]}
    for c, (codes, labels, _f) in cat.items():
        nm = _norm(head.get(c, c))
        if nm in _SCALE_WORDS or nm in _SCALE_IDS:
            fs = [_scale_word(lb, nm in _SCALE_IDS or nm in ("unitmult", "unitmultiplier")) or 1.0 for lb in labels]
            arr = np.array(fs)[codes]
            return arr, {"scale": "varies by series", "factor": None, "scale_column": head.get(c, c)}
    return np.ones(n), {"scale": "units", "factor": 1.0, "scale_column": None}


def _scale_word(v: Any, exponent: bool) -> Optional[float]:
    s = str(v or "").strip().lower()
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


def _measure_type(S: Dict[str, Any]) -> None:
    """flow | stock | rate | index | count | unknown, from the unit and the labels; how months are added up."""
    m = S["measure"]
    uom = str(m.get("uom") or "")
    labels = " ".join([m["column"]] + [lb for d in S["dims"] for lb in d["labels"][:60]])
    if S.get("_member_unit"):
        uom = " ".join(sorted(set(v for per in S["_member_unit"].values() for v in per.values())))
    t = _unit_type(uom, labels, m["column"])
    m["type"] = t
    m["aggregation"] = "sum over months" if t in ("flow", "count") else "mean over months"
    m["currency"] = bool(_CURRENCY.search(uom))
    if t == "index":
        bases = sorted(set(_INDEX_BASE.findall(uom)))
        m["index_bases"] = bases
    for j, per in (S.get("_member_unit") or {}).items():
        S["dims"][j]["mixed_units"] = True
        S["dims"][j]["unit_of"] = {S["dims"][j]["labels"][mi]: u for mi, u in per.items()}


def _unit_type(uom: str, labels: str, column: str) -> str:
    """flow | stock | rate | index | count | unknown for one unit of measure."""
    if _INDEX_WORDS.search(uom):
        return "index"
    if _RATE_WORDS.search(uom):
        return "rate"
    if _CURRENCY.search(uom):
        return "stock" if _STOCK_WORDS.search(labels) else "flow"
    if _COUNT_UNITS.search(uom):
        return "stock" if _POP_WORDS.search(labels) else "count"
    if not uom:
        try:
            from northledger.measure import additive_kind
            ak = additive_kind(column)
        except ImportError:
            ak = ""
        return "flow" if ak == "money" else "count" if ak == "units" else "unknown"
    return "unknown"


def _mixed_measure(S: Dict[str, Any], j: int) -> None:
    """A dimension whose members are measured in different units (dollars beside units, an index on two bases): each
    slice fixes one member, never mixed. The default member: one named as a total, else one in a currency, else the
    first in the file."""
    rec = S["dims"][j]
    labels = rec["labels"]
    unit_of = rec.get("unit_of") or {}
    hint = [m for m in range(len(labels)) if _TOTAL_HINT.search(labels[m])]
    cur = [m for m in range(len(labels)) if _CURRENCY.search(str(unit_of.get(labels[m]) or ""))]
    m = (hint or cur or [0])[0]
    rec.update(role="measure", total=labels[m], total_index=m, components={}, alternatives={},
               why="its members are measured in different units (%s): one at a time, never added or averaged together"
                   % ", ".join(sorted(set(str(u) for u in unit_of.values()))[:4]))
    u = str(unit_of.get(labels[m]) or "")
    t = _unit_type(u, " ".join([S["measure"]["column"], labels[m]]), S["measure"]["column"])
    S["measure"].update(uom=u, type=t, aggregation="sum over months" if t in ("flow", "count") else "mean over months",
                        currency=bool(_CURRENCY.search(u)), units_vary_by=rec["column"])
    if t == "index":
        S["measure"]["index_bases"] = sorted(set(_INDEX_BASE.findall(u)))


def slice_type(S: Dict[str, Any], where: Dict[str, Any]) -> Dict[str, Any]:
    """The measure of one slice: its unit and type (a measure dimension's member decides them)."""
    for d in S["dims"]:
        if d.get("mixed_units") and isinstance(where.get(d["column"]), str):
            u = str((d.get("unit_of") or {}).get(where[d["column"]]) or "")
            t = _unit_type(u, " ".join([S["measure"]["column"], where[d["column"]]]), S["measure"]["column"])
            return {"uom": u, "type": t, "currency": bool(_CURRENCY.search(u)),
                    "aggregation": "sum over months" if t in ("flow", "count") else "mean over months"}
    m = S["measure"]
    return {"uom": m.get("uom"), "type": m["type"], "currency": m.get("currency"), "aggregation": m["aggregation"]}


PANEL_MAX_SERIES = 60           # nl_browser's long-table layout reads at most this many series side by side


def _usable(S: Dict[str, Any]) -> bool:
    """Slice the table when adding its rows would be wrong: a relation between members (a verified total, an adjusted
    copy, components, a rate's published aggregate). A panel with no relation (currencies in two units, an official
    table whose members only differ) is read side by side by the long-table layout, as before, when it has at most 60
    series (kind "panel_no_relations"); past that, the layout cannot, and the table is read one member at a time."""
    rel = [d for d in S["dims"] if d["role"] in ("partition", "hierarchy", "adjustment", "components", "rate_aggregate")
           or (d["role"] == "measure" and not d.get("mixed_units"))]
    if rel:
        return True
    if any(d["role"] in ("single", "measure") for d in S["dims"]):
        if int(S.get("series") or 0) <= PANEL_MAX_SERIES:
            S["kind"] = "panel_no_relations"
            S["reason"] = ("no member of the table is a total, a part or an adjusted copy of another: its %d series are "
                           "read side by side" % int(S.get("series") or 0))
            return False
        return True
    return False


# --------------------------------------------------------------------------------------------- 2. relations
def _dim_tensor(S: Dict[str, Any], j: int, restrict: bool = True, adjusted: bool = False) -> Tuple[Any, Any, Any]:
    """A[member, context, time] (base units, NaN where blank or absent), X (a row exists), and each series' context,
    on the reference cells: another dimension's adjusted copy left out (its parts may be adjusted apart); with
    `adjusted`, the adjusted copy's cells only (the record of whether the adjusted parts add up)."""
    import numpy as np
    SM, V, E = S["_SM"], S["_V"], S["_E"]
    mask = np.ones(SM.shape[0], dtype=bool)
    if restrict:
        for k, d in enumerate(S["dims"]):
            if k != j and d.get("role") == "adjustment" and d.get("nsa_index") is not None:
                mask &= SM[:, k] == d["sa_index" if adjusted else "nsa_index"]
    sm = SM[mask]
    others = [k for k in range(SM.shape[1]) if k != j]
    if others:
        ctx = _combine([sm[:, k].astype(np.int64) for k in others])
    else:
        ctx = np.zeros(sm.shape[0], dtype=np.int64)
    M = len(S["dims"][j]["labels"])
    C = int(ctx.max()) + 1 if len(ctx) else 0
    T = V.shape[1]
    A = np.full((M, C, T), np.nan)
    X = np.zeros((M, C, T), dtype=bool)
    A[sm[:, j], ctx] = V[mask]
    X[sm[:, j], ctx] = E[mask]
    return A, X, ctx


def _tol_unit(S: Dict[str, Any]) -> float:
    """Half a unit of the last published digit, in base units (0.5 thousand dollars for a table in thousands)."""
    f = S["measure"].get("factor") or 1.0
    return 0.5 * (10.0 ** -int(S["measure"].get("decimals") or 0)) * float(f)


def _sum_check(A: Any, X: Any, t: int, parts: Sequence[int], tol_unit: float, nonneg: bool,
               months: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Whether member t is the sum of `parts`: on the cells where t has a value and every part has a row, the residual
    t - (the parts with a value) is within the tolerance on 95% of the complete cells (6 or more, over 3 or more
    months); a non-negative flow's parts with a value never exceed it by more than the tolerance (an incomplete cell's
    residual is the unallocated, suppressed share)."""
    import numpy as np
    P = list(parts)
    tgt = A[t]
    have_t = ~np.isnan(tgt)
    if not P:
        return {"pass": False, "complete": 0}
    pa = A[P]
    rows = X[P].all(axis=0)
    present = ~np.isnan(pa)
    testable = have_t & rows
    complete = testable & present.all(axis=0)
    incomplete = testable & ~present.all(axis=0)
    s = np.where(present, pa, 0.0).sum(axis=0)
    r = tgt - s
    tol = np.maximum(tol_unit * (len(P) + 1), 1e-6 * np.abs(tgt))
    nc = int(complete.sum())
    ok = (np.abs(r) <= tol) & complete
    months_c = int(complete.any(axis=0).sum())
    share = float(ok.sum()) / nc if nc else 0.0
    neg = bool(((r < -tol) & incomplete).any()) if nonneg else False
    rel = np.abs(r[complete]) / np.maximum(np.abs(tgt[complete]), 1e-300) if nc else np.array([])
    out = {"pass": nc >= MIN_COMPLETE and months_c >= MIN_MONTHS and share >= PASS_SHARE and not neg,
           "complete": nc, "within": int(ok.sum()), "months": months_c, "incomplete": int(incomplete.sum()),
           "share": round(share, 4), "negative_unallocated": neg,
           "max_rel_residual": float(rel.max()) if len(rel) else None}
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
    have = ~np.isnan(V)
    return bool(not have.any() or float(V[have].min()) >= 0)


def _relations(S: Dict[str, Any], j: int, tm: _Timer) -> None:
    """The dimension's role: flat partition, a coded hierarchy, a hierarchy found by subset sums, components and
    alternatives; else unresolved (rule 6 reads it)."""
    import numpy as np
    rec = S["dims"][j]
    labels = rec["labels"]
    A, X, _ctx = _dim_tensor(S, j)
    tol_u = _tol_unit(S)
    nonneg = _nonneg(S)
    dom = _dominance(A)
    M = len(labels)
    # "excluding" outside brackets names an alternative total ("Retail trade excluding gasoline"); inside them it defines
    # a member ("Supermarkets and other grocery retailers (except convenience retailers) [44511]")
    alts_label = {m for m in range(M) if _ALT_HINT.search(re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", labels[m]))}
    hint = [m for m in range(M) if _TOTAL_HINT.search(labels[m]) or (_label_code(labels[m]) or "").count("-") == 1
            and _RANGE.match(_label_code(labels[m]) or "")]
    order = list(np.argsort(-dom, kind="stable"))
    # 1. FLAT: the top 3 by dominance and any name-hinted member, against every other member (alternatives left out)
    tried = []
    for t in list(dict.fromkeys([int(x) for x in order[:3]] + hint)):
        P = [m for m in range(M) if m != t and m not in alts_label]
        if len(P) < 1:
            continue
        chk = _sum_check(A, X, t, P, tol_u, nonneg)
        tried.append((t, chk))
        if chk["pass"] or (chk.get("bound_only") and t in hint):
            rec.update(role="partition", total=labels[t], total_index=t, parts=[labels[p] for p in P],
                       part_index=P, tree={t: P}, depth={t: 0, **{p: 1 for p in P}},
                       sum_check=_public_check(chk), components={}, alternatives={})
            _alternatives(S, rec, A, tol_u, alts_label)
            _sa_record(S, j, rec, tol_u, nonneg)
            return
    # 2. HIERARCHY FROM CODES
    codes = {m: _label_code(labels[m]) for m in range(M)}
    if sum(1 for c in codes.values() if c) >= max(3, int(0.5 * M)):
        if _coded_hierarchy(S, rec, A, X, codes, tol_u, nonneg, alts_label, dom):
            return
    # 3. HIERARCHY WITHOUT CODES
    if not tm.over() and _codefree_hierarchy(S, rec, A, X, tol_u, nonneg, alts_label, dom, tm):
        return
    # 4. components: one member bounds every other (total sales and e-commerce sales)
    top = int(order[0])
    others = [m for m in range(M) if m != top]
    if others and (S["official"] or top in hint or _measure_dim_name(rec["column"], labels)) and \
            all(_bounds(A, top, m, tol_u) for m in others):
        name = rec["column"]
        role = "measure" if _measure_dim_name(name, labels) else "components"
        rec.update(role=role, total=labels[top], total_index=top, components={labels[m]: labels[top] for m in others},
                   alternatives={}, why="%s bounds every other member everywhere and is not their sum" % labels[top])
        return
    rec["role"] = "unresolved"
    rec["why"] = "no member is the sum of others (sum-checks failed)"


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
    return {k: chk.get(k) for k in ("complete", "within", "months", "incomplete", "share", "max_rel_residual")}


def _alternatives(S: Dict[str, Any], rec: Dict[str, Any], A: Any, tol_u: float, alts_label: Set[int]) -> None:
    """Members left out of the tree: an alternative total (named "excluding ...", or a total less one or two members),
    else a component of the smallest verified member that bounds it everywhere."""
    import numpy as np
    labels = rec["labels"]
    placed = set(rec.get("depth") or {})
    left = [m for m in range(len(labels)) if m not in placed]
    if not left:
        return
    t = rec.get("total_index")
    tree_members = sorted(placed)
    for x in left:
        if x in alts_label and t is not None and _bounds(A, t, x, tol_u):
            rec["alternatives"][labels[x]] = labels[t]
            continue
        found = False
        if t is not None and _bounds(A, t, x, tol_u):
            diff = A[t] - A[x]
            cand = [m for m in tree_members if m != t]
            for a in cand:
                if _close_cells(diff, A[a], tol_u * 2):
                    rec["alternatives"][labels[x]] = "%s less %s" % (labels[t], labels[a])
                    found = True
                    break
            if not found:
                for i, a in enumerate(cand):
                    for b in cand[i + 1:]:
                        if _close_cells(diff, A[a] + A[b], tol_u * 3):
                            rec["alternatives"][labels[x]] = "%s less %s and %s" % (labels[t], labels[a], labels[b])
                            found = True
                            break
                    if found:
                        break
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
        if _TOTAL_HINT.search(labels[top]) or dom[top] >= 0.99:
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
               sum_check=checks[root], family_checks={labels[p]: checks[p] for p in tree}, components={},
               alternatives={}, by="codes")
    _alternatives(S, rec, A, tol_u, alts_label)
    return True


def _codefree_hierarchy(S: Dict[str, Any], rec: Dict[str, Any], A: Any, X: Any, tol_u: float, nonneg: bool,
                        alts_label: Set[int], dom: Any, tm: _Timer) -> bool:
    """The hierarchy found by subset sums: for each parent (the most dominant first, at most 30), the members it bounds
    (at most 22, the largest), a meet-in-the-middle subset sum on a 3-cell fingerprint, each match verified on every
    cell; the coarsest verified partition gives its children, and each child is searched in turn."""
    import numpy as np
    t_end = time.perf_counter() + min(CODEFREE_BUDGET_S, max(0.0, tm.left()))
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
    while queue and done < PARENTS_MAX and time.perf_counter() < t_end:
        p = queue.pop(0)
        done += 1
        cands = [m for m in range(M) if m not in assigned and m not in alts_label and _bounds(A, p, m, tol_u)]
        if not cands:
            continue
        cands = sorted(cands, key=lambda m: (-sizes[m], m))[:CANDIDATES_MAX]
        sol = _subset_partition(A, X, p, cands, tol_u, nonneg, t_end)
        if sol is None:
            continue
        fam, chk = sol
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
    rec.update(role="hierarchy" if any(depth[p] >= 1 for p in tree) else "partition", total=labels[root],
               total_index=root, tree=tree, depth=depth, parts=[labels[c] for c in tree[root]], part_index=tree[root],
               sum_check=checks[root], family_checks={labels[p]: checks[p] for p in tree}, components={},
               alternatives={}, by="subset sums")
    _alternatives(S, rec, A, tol_u, alts_label)
    return True


def _subset_partition(A: Any, X: Any, p: int, cands: List[int], tol_u: float, nonneg: bool, t_end: float
                      ) -> Optional[Tuple[List[int], Dict[str, Any]]]:
    import numpy as np
    k = len(cands)
    # the fingerprint: 3 cells where the parent and every candidate have a value, spread over the cells
    have = ~np.isnan(A[p])
    for c in cands:
        have &= ~np.isnan(A[c])
    idx = np.flatnonzero(have.ravel())
    if len(idx) < 3:
        return None
    pick = idx[[0, len(idx) // 2, len(idx) - 1]]
    tgt = A[p].ravel()[pick]
    vals = np.stack([A[c].ravel()[pick] for c in cands])          # k x 3
    tol = np.maximum(tol_u * (k + 1), 1e-6 * np.abs(tgt))
    h = k // 2
    left, right = vals[:h], vals[h:]

    def sums(v: Any) -> Tuple[Any, Any]:
        s = np.zeros((1, 3))
        masks = np.zeros(1, dtype=np.int64)
        for i in range(v.shape[0]):
            s = np.concatenate([s, s + v[i]])
            masks = np.concatenate([masks, masks | (1 << i)])
        return s, masks
    sl, ml = sums(left)
    sr, mr = sums(right)
    o = np.argsort(sr[:, 0], kind="stable")
    sr, mr = sr[o], mr[o]
    need = tgt[0] - sl[:, 0]
    lo = np.searchsorted(sr[:, 0], need - tol[0], "left")
    hi = np.searchsorted(sr[:, 0], need + tol[0], "right")
    found: List[List[int]] = []
    for i in np.flatnonzero(hi > lo):
        for jj in range(lo[i], hi[i]):
            tot = sl[i] + sr[jj]
            if np.all(np.abs(tot - tgt) <= tol):
                members = [cands[b] for b in range(h) if ml[i] >> b & 1] + \
                          [cands[h + b] for b in range(k - h) if mr[jj] >> b & 1]
                if members:
                    found.append(sorted(members))
        if len(found) > 200 or time.perf_counter() > t_end:
            break
    best = None
    # the coarsest verified partition (a member named "excluding ..." never takes part: _relations leaves it out)
    for fam in sorted(found, key=lambda f: (len(f), f)):
        chk = _sum_check(A, X, p, fam, tol_u, nonneg)
        if chk["pass"]:
            best = (fam, chk)
            break
        if time.perf_counter() > t_end:
            break
    return best


def _rate_aggregate(S: Dict[str, Any], j: int) -> None:
    """A rate or an index is never summed or averaged across members (AM4): the published aggregate is the member that
    lies inside the range of the others in 99% of the cells and carries a total's name, or comes first in the file."""
    import numpy as np
    rec = S["dims"][j]
    labels = rec["labels"]
    A, X, _c = _dim_tensor(S, j)
    M = len(labels)
    with np.errstate(all="ignore"):
        best = None
        for m in range(M):
            rest = [x for x in range(M) if x != m]
            if not rest:
                continue
            lo = np.nanmin(np.where(np.isnan(A[rest]), np.inf, A[rest]), axis=0)
            hi = np.nanmax(np.where(np.isnan(A[rest]), -np.inf, A[rest]), axis=0)
            have = ~np.isnan(A[m]) & np.isfinite(lo) & np.isfinite(hi)
            n = int(have.sum())
            if n < MIN_COMPLETE:
                continue
            inside = float(((A[m][have] >= lo[have] - 1e-12) & (A[m][have] <= hi[have] + 1e-12)).mean())
            if inside < BOUND_SHARE:
                continue
            score = (2 if _TOTAL_HINT.search(labels[m]) else 1 if m == 0 else 0)
            if score and (best is None or score > best[0]):
                best = (score, m, inside, n)
    if best is None:
        rec["role"] = "unresolved"
        rec["why"] = "a rate or an index is never added up; no member is a published aggregate"
        return
    _s, m, inside, n = best
    rec.update(role="rate_aggregate", total=labels[m], total_index=m,
               parts=[labels[x] for x in range(M) if x != m], part_index=[x for x in range(M) if x != m],
               components={}, alternatives={},
               sum_check={"inside_range_share": round(inside, 4), "cells": n},
               why="a %s is never added or averaged across members: %s lies inside the others' range in %s%% of %s "
                   "cells and is read as the published aggregate" % (S["measure"]["type"], labels[m],
                                                                     round(100 * inside, 1), _fmt_count(n)))


def _adjustment(S: Dict[str, Any], j: int) -> None:
    """A seasonally adjusted copy beside the unadjusted one, by behaviour: their calendar-year totals agree within 3%
    and one is three times as seasonal as the other (the variance of its month means, detrended). Labels are hints."""
    import numpy as np
    rec = S["dims"][j]
    labels = rec["labels"]
    M = len(labels)
    if not (2 <= M <= ADJ_MAX_MEMBERS) or not S.get("monthly") or S["months"] < 24:
        return
    A, X, _c = _dim_tensor(S, j, restrict=False)
    months = S["_months"]
    best = None
    for a in range(M):
        for b in range(a + 1, M):
            year_ok, ratio = _pair_behaviour(A[a], A[b], months, S["measure"]["type"])
            if year_ok is None:
                continue
            if year_ok and ratio is not None and (ratio >= ADJ_SEASONAL_RATIO or ratio <= 1.0 / ADJ_SEASONAL_RATIO):
                nsa, sa = (a, b) if ratio >= 1 else (b, a)
                strength = ratio if ratio >= 1 else 1.0 / ratio
                if best is None or strength > best[2]:
                    best = (nsa, sa, strength)
    if best is None:
        return
    nsa, sa, strength = best
    rec.update(role="adjustment", nsa=labels[nsa], sa=labels[sa], nsa_index=nsa, sa_index=sa,
               total=labels[nsa], total_index=nsa, components={}, alternatives={},
               why="calendar-year totals agree within 3%%; %s is %s times as seasonal as %s" % (
                   labels[nsa], round(strength, 1), labels[sa]))
    for m in range(M):
        if m not in (nsa, sa):
            rec["alternatives"][labels[m]] = labels[nsa]


def _seasonal_strength(y: Any, months: Sequence[str]) -> Optional[float]:
    import numpy as np
    ok = ~np.isnan(y)
    if ok.sum() < 24:
        return None
    v = y.copy()
    if np.all(v[ok] > 0):
        v = np.log(v)
    else:
        mu = np.nanmean(np.abs(v)) or 1.0
        v = v / mu
    # a centred 2x12 moving average as the trend
    k = np.r_[0.5, np.ones(11), 0.5] / 12.0
    tr = np.full_like(v, np.nan)
    for i in range(6, len(v) - 6):
        w = v[i - 6:i + 7]
        if not np.isnan(w).any():
            tr[i] = float(np.dot(w, k))
    res = v - tr
    mon = np.array([int(m[5:7]) for m in months])
    means = []
    for mm in range(1, 13):
        x = res[(mon == mm) & ~np.isnan(res)]
        if len(x):
            means.append(float(x.mean()))
    if len(means) < 12:
        return None
    return float(np.var(means))


def _pair_behaviour(a: Any, b: Any, months: Sequence[str], mtype: str) -> Tuple[Optional[bool], Optional[float]]:
    """(calendar-year totals agree within 3% in every context, the median ratio of a's seasonal strength to b's)."""
    import numpy as np
    years = sorted({m[:4] for m in months})
    mon = np.array(months)
    agree, ratios, n = [], [], 0
    for c in range(a.shape[0]):
        ya, yb = a[c], b[c]
        if np.isnan(ya).all() or np.isnan(yb).all():
            continue
        for y in years:
            sel = np.array([m.startswith(y) for m in mon])
            if sel.sum() == 12 and not np.isnan(ya[sel]).any() and not np.isnan(yb[sel]).any():
                fa, fb = (ya[sel].sum(), yb[sel].sum()) if mtype in ("flow", "count", "unknown") else \
                    (ya[sel].mean(), yb[sel].mean())
                if fb != 0:
                    agree.append(abs(fa / fb - 1.0))
        sa_, sb_ = _seasonal_strength(ya, months), _seasonal_strength(yb, months)
        if sa_ is not None and sb_ is not None and sb_ > 0 and sa_ > 0:
            ratios.append(sa_ / sb_)
        n += 1
        if n >= 60:
            break
    if not agree or not ratios:
        return None, None
    return bool(float(np.median(agree)) <= ADJ_YEAR_TOL), float(np.median(ratios))


def _rule6(S: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """No relation found: an official table is read one member at a time (the total-named member, else the one with the
    most cells that dominates); a business export adds its members up as the engine always has."""
    import numpy as np
    labels = rec["labels"]
    j = S["dims"].index(rec)
    if S["official"] or S["measure"]["type"] in ("rate", "index"):
        hint = [m for m in range(len(labels)) if _TOTAL_HINT.search(labels[m])]
        if hint:
            m = hint[0]
        else:
            SM, E = S["_SM"], S["_E"]
            cover = np.array([int(E[SM[:, j] == mm].sum()) for mm in range(len(labels))])
            A, X, _c = _dim_tensor(S, j)
            dom = _dominance(A)
            m = int(sorted(range(len(labels)), key=lambda x: (-cover[x], -dom[x], x))[0])
        prior = rec.get("why")
        rec.update(role="single", total=labels[m], total_index=m, components={}, alternatives={},
                   why=(prior + "; " if prior else "") + "read one member at a time (an official table is never "
                                                       "added across a dimension it could not verify)")
    else:
        rec.update(role="flat_additive", total=None, total_index=None, components={}, alternatives={},
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
        elif d.get("total") is not None:
            where[d["column"]] = d["total"]
            why[d["column"]] = {
                "partition": "the total of the %d other members (sum-check)" % len(d.get("parts") or []),
                "hierarchy": "the root of the hierarchy (sum-checked family by family)",
                "adjustment": "the unadjusted series: full calendar years, additive",
                "measure": "the total measure; the others are components or other measures",
                "components": "it bounds the other members, which are its components",
                "rate_aggregate": "the published aggregate (a %s is never added across members)" % S["measure"]["type"],
                "single": "read one member at a time (no relation verified)",
            }.get(r, "the default member")
    slices = []
    if not _series_exist(S, where):
        S["usable"] = False
        S["reason"] = "the default slice has no series in the table"
        S["slices"] = []
        return
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
        if d["role"] == "measure" and d.get("mixed_units"):
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


def series(S: Dict[str, Any], where: Dict[str, Any]) -> Tuple[List[str], Any, Any]:
    """(times, the slice's values in base units (NaN where no member has a value), how many member cells were blank)."""
    import numpy as np
    sel = _select(S, where)
    T = len(S["_times"])
    if sel is None or not len(sel):
        return list(S["_times"]), np.full(T, np.nan), np.zeros(T, dtype=int)
    V = S["_V"][sel]
    E = S["_E"][sel]
    have = ~np.isnan(V)
    val = np.where(have.any(axis=0), np.where(have, V, 0.0).sum(axis=0), np.nan)
    blank = (E & ~have).sum(axis=0)
    return list(S["_times"]), val, blank


def slice_by_id(S: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    return next((s for s in S.get("slices") or [] if s["id"] == sid), None)


def measure_label(S: Dict[str, Any], where: Dict[str, Any]) -> str:
    """The slice's measure column as the engine reads it: a currency flow "<label> total" (the core adds up and forecasts
    a column whose head word is a money word), a count "<label> count", a stock, rate or index "value" (a level)."""
    t = S["measure"]["type"]
    label = ""
    for d in S["dims"]:
        if d["role"] in ("measure", "components") and isinstance(where.get(d["column"]), str):
            label = where[d["column"]]
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
        if d.get("mixed_units"):
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
    flow = S["measure"]["type"] in ("flow", "count")
    for i, xs in acc.items():
        out[i] = math.fsum(xs) if flow else math.fsum(xs) / len(xs)
    return months, out


def window_figure(S: Dict[str, Any], months: Sequence[str], vals: Any, w: Sequence[str]) -> Tuple[Optional[float], int]:
    """A window's figure (a flow's 12-month total, a level's 12-month mean) and how many of its months hold a value."""
    xs = [float(v) for m, v in zip(months, vals) if w[0] <= m <= w[1] and v == v]
    if not xs:
        return None, 0
    if S["measure"]["type"] in ("flow", "count"):
        return math.fsum(xs), len(xs)
    return math.fsum(xs) / len(xs), len(xs)


_SUFFIX = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def money(v: Optional[float], S: Dict[str, Any], signed: bool = False, ref: Optional[float] = None) -> str:
    """A figure in the measure's unit: "$834.7B", "+$29.3B", "−$1.2M", "12.4M" (a count), "5.2%" (a rate)."""
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
        body = "%.4g" % x
    else:
        body = None
        for lim, suf in _SUFFIX:
            if r >= lim:
                body = "%.1f%s" % (x / lim, suf)
                break
        if body is None:
            body = "%.0f" % x if x >= 1 else "%.3g" % x
    if not re.search(r"[1-9]", body):
        v = 0.0                               # rounds to zero: no sign ("$0.0B", never "−$0.0B")
    sign = ("+" if v > 0 else "−" if v < 0 else "") if signed else ("−" if v < 0 else "")
    unit = ""
    if not cur and S["measure"].get("currency"):
        unit = " " + str(S["measure"].get("uom") or "")
    return sign + cur + body + unit


def pct(v: Optional[float], signed: bool = True) -> str:
    if v is None or v != v:
        return "n/a"
    s = "%.1f%%" % abs(v)
    if signed:
        return ("+" if v > 0 else "−" if v < 0 else "") + s
    return ("−" if v < 0 else "") + s


def breakdown(S: Dict[str, Any], bd: Dict[str, Any], where: Dict[str, Any], win: Dict[str, List[str]]
              ) -> Optional[Dict[str, Any]]:
    """One breakdown in the headline's windows: per part its two window figures, its contribution to the change, its own
    growth (when its 24 months are all published), its share of the latest level, and its share of the change (only when
    every part moved the way the total did); the UNALLOCATED part (the total less the parts with a value: the suppressed
    share) per window. The parts and the unallocated add up to the change exactly (math.fsum)."""
    import numpy as np
    months, tot = _monthly(S, where)
    T0, n0 = window_figure(S, months, tot, win["prior"])
    T1, n1 = window_figure(S, months, tot, win["latest"])
    if T0 is None or T1 is None or n0 < 12 or n1 < 12:
        return None
    flow = S["measure"]["type"] in ("flow", "count")
    change = T1 - T0
    parts = []
    sums0, sums1 = [], []
    for p in bd["parts"]:
        m2, pv = _monthly(S, dict(where, **{bd["dim"]: p}))
        a, na = window_figure(S, m2, pv, win["prior"])
        b, nb = window_figure(S, m2, pv, win["latest"])
        if not flow:
            # a stock's window mean: its months' values over the 12 months (a blank month is unallocated)
            a = math.fsum(float(v) for m, v in zip(m2, pv) if win["prior"][0] <= m <= win["prior"][1] and v == v) / 12.0
            b = math.fsum(float(v) for m, v in zip(m2, pv) if win["latest"][0] <= m <= win["latest"][1] and v == v) / 12.0
            na_ok, nb_ok = na, nb
        a = a or 0.0
        b = b or 0.0
        sums0.append(a)
        sums1.append(b)
        complete = na == 12 and nb == 12
        parts.append({"member": p, "prior": a, "latest": b, "contribution": b - a, "complete": complete,
                      "growth_pct": (100.0 * (b / a - 1.0)) if complete and a > 0 else None,
                      "share_level_pct": 100.0 * b / T1 if T1 else None})
    u0 = T0 - math.fsum(sums0)
    u1 = T1 - math.fsum(sums1)
    contribs = [p["contribution"] for p in parts]
    u_contrib = change - math.fsum(contribs)
    same = all((c >= 0) == (change >= 0) for c in contribs if abs(c) > 0) and abs(change) >= 0.01 * abs(T0)
    for p in parts:
        p["share_change_pct"] = (100.0 * p["contribution"] / change) if same and change else None
    parts.sort(key=lambda p: (-abs(p["contribution"]), p["member"]))
    ok = abs(math.fsum(contribs + [u_contrib]) - change) <= RECONCILE_TOL * max(1.0, abs(change), abs(T1))
    return {"id": bd["id"], "dim": bd["dim"], "parent": bd["parent"], "depth": bd.get("depth", 1),
            "prior": T0, "latest": T1, "change": change, "parts": parts,
            "unallocated": {"prior": u0, "latest": u1, "contribution": u_contrib},
            "shares_given": bool(same), "reconciles": bool(ok)}


def _label_of(S: Dict[str, Any], where: Dict[str, Any]) -> List[str]:
    out = []
    for d in S["dims"]:
        w = where.get(d["column"])
        if d["role"] == "constant":
            continue
        if w == "*":
            out.append("all %s" % d["column"])
        elif isinstance(w, list):
            out.append("%d %s members" % (len(w), d["column"]))
        elif w is not None:
            out.append(str(w))
    return out


def estimand(S: Dict[str, Any], where: Dict[str, Any], win: Dict[str, List[str]], plan_source: str = "engine_default",
             why: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """rep["estimand"]: what the headline is (the slice, the measure, the unit and scale, the windows), its figures in
    base units with their texts, each sum-check behind the slice, and what was left out and why."""
    months, vals = _monthly(S, where)
    T0, n0 = window_figure(S, months, vals, win["prior"])
    T1, n1 = window_figure(S, months, vals, win["latest"])
    m = S["measure"]
    agg = "12-month totals" if m["type"] in ("flow", "count") else "12-month averages"
    scale_txt = ""
    if m.get("factor") and m["factor"] != 1:
        scale_txt = " (file in %s ×%s)" % (m.get("scale"), format(int(m["factor"]), ","))
    unit = (str(m.get("uom") or "") or m["type"]).lower()
    text = "%s; %s%s; %s %s–%s vs %s–%s" % (
        " · ".join(_label_of(S, where)) or "the whole table", unit, scale_txt, agg,
        _mon(win["latest"][0]), _mon(win["latest"][1]), _mon(win["prior"][0]), _mon(win["prior"][1]))
    why = why or {}
    sl = []
    for d in S["dims"]:
        w = where.get(d["column"])
        if d["role"] == "constant":
            continue
        sl.append({"dim": d["column"], "member": w, "role": d["role"],
                   "why": why.get(d["column"]) or d.get("why") or ""})
    change = (T1 - T0) if T0 is not None and T1 is not None else None
    chg_pct = (100.0 * (T1 / T0 - 1.0)) if T0 and T1 is not None and T0 > 0 else None
    figures = {"prior": {"value": _r(T0), "text": money(T0, S), "months": n0},
               "latest": {"value": _r(T1), "text": money(T1, S), "months": n1},
               "change": {"value": _r(change), "text": money(change, S, signed=True)},
               "change_pct": {"value": _r(chg_pct, 6), "text": pct(chg_pct)}}
    checks = []
    for d in S["dims"]:
        if d["role"] not in ("partition", "hierarchy") or where.get(d["column"]) != d.get("total"):
            continue
        bd = next((b for b in S.get("breakdowns") or [] if b["dim"] == d["column"]), None)
        res = breakdown(S, bd, where, win) if bd else None
        sc = d.get("sum_check") or {}
        mx = sc.get("max_rel_residual")
        checks.append({"dim": d["column"], "total": d["total"], "parts": len(d.get("parts") or []),
                       "by": d.get("by") or "flat", "complete_cells": sc.get("complete"),
                       "within_tolerance": sc.get("within"), "max_rel_residual": mx,
                       "unallocated_latest": {"value": _r(res["unallocated"]["latest"]) if res else None,
                                              "text": money(res["unallocated"]["latest"], S, ref=T1) if res else "n/a"},
                       "unallocated_prior": {"value": _r(res["unallocated"]["prior"]) if res else None,
                                             "text": money(res["unallocated"]["prior"], S, ref=T1) if res else "n/a"},
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
        if d["role"] in ("partition", "hierarchy") and w == d.get("total"):
            n = len(d["labels"]) - 1
            excluded.append({"what": "%d other members" % n, "dim": d["column"],
                             "why": "parts of %s (sum-checked); shown as a breakdown, never added to it" % d["total"]})
        if d["role"] == "rate_aggregate" and w == d.get("total"):
            excluded.append({"what": "%d other members" % (len(d["labels"]) - 1), "dim": d["column"],
                             "why": "each member's own %s; never added or averaged across members" % S["measure"]["type"]})
        if d["role"] == "single":
            excluded.append({"what": "%d other members" % (len(d["labels"]) - 1), "dim": d["column"],
                             "why": "no total was verified, so members are never added across this dimension"})
    return {"text": text, "slice": sl,
            "measure": {"label": m["column"], "uom": m.get("uom"), "scale": m.get("scale"),
                        "scale_applied": m.get("factor"), "type": m["type"],
                        "aggregation": m["aggregation"]},
            "comparison": {"latest": list(win["latest"]), "prior": list(win["prior"])},
            "figures": figures, "sum_checks": checks, "excluded": excluded, "plan_source": plan_source,
            "inference": None}


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
        for k in ("total", "nsa", "sa", "by", "why"):
            if d.get(k) is not None:
                x[k] = d[k]
        if d.get("parts") is not None and d["role"] in ("partition", "hierarchy", "rate_aggregate"):
            x["parts"] = len(d["parts"])
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
        dims.append(x)
    out = {"kind": S["kind"], "usable": bool(S.get("usable")), "reason": S.get("reason") or "",
           "version": S.get("version"), "publisher": S.get("publisher"), "official": bool(S.get("official")),
           "series": S.get("series"), "months": S.get("months"), "rows": S.get("rows"),
           "date": (S.get("date") or {}).get("column"),
           "measure": {k: v for k, v in (S.get("measure") or {}).items() if k != "landed"} if S.get("measure") else None,
           "metadata": [{k: v for k, v in m.items() if k != "landed"} for m in S.get("metadata") or []],
           "flag_column": (S.get("flags") or {}).get("column"), "dims": dims,
           "slices": [{k: v for k, v in s.items()} for s in S.get("slices") or []],
           "breakdowns": [dict(b) for b in S.get("breakdowns") or []],
           "flags": S.get("flags"), "corrections": list(S.get("corrections") or []),
           "hash": S.get("hash"), "detect_seconds": S.get("detect_seconds")}
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
        if col not in values_of:
            continue
        x: Dict[str, Any] = {"column": col, "role": d["role"], "members": len(d["labels"])}
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
    out = {"kind": "cube", "publisher": S.get("publisher"), "series": S.get("series"), "months": S.get("months"),
           "date": S["date"]["column"],
           "measure": {"column": m["column"], "type": m["type"], "uom": m.get("uom"), "scale": m.get("scale")},
           "metadata": [x["column"] for x in S.get("metadata") or [] if cols_all is None or x["column"] in cols_all][:20],
           "flag_column": (S.get("flags") or {}).get("column"),
           "dims": dims, "slices": slices, "breakdowns": bds, "rules": rules}
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
