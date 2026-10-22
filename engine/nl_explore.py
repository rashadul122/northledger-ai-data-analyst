"""
nl_explore: the analysis of a table that is NOT a time series (universal track, 17 Oct 2026).

A visitor's Statistics Canada Census Profile (14 regions x 1,624 measures, no date) came back as a blank report: every chart the
engine knew needed a date, and the only charts it could draw ADDED the value column across rows that measure different things
(a dollar average, a head count and a percentage in one column). This module reads such a table the way an analyst would:

  * indicator layout: one column names WHAT is measured (CHARACTERISTIC_NAME, Indicator Name, Series, Measure), another names WHERE
    or WHO (GEO_NAME, Country, Region), and together they tell the rows apart. A value column is then never added or ranked
    across indicators: each indicator is compared across the places, one at a time.
  * value families: numeric columns that are one quantity in parallel forms, by their headers: a TOTAL beside MEN+ and WOMEN+
    (or MALE/FEMALE), and the LOW_CI / HI_CI bounds of each (a 95% confidence interval the publisher printed).
  * the total member: a place whose values are the sum of the others (Canada = the 13 provinces and territories), checked on the
    values; it is a reference line in every comparison, never a bar beside its own parts.
  * per indicator, the comparable form: a percentage of its group when the table carries one (RATE beside COUNT), so a large
    province is not "highest" for being large; the value itself for averages, medians and dollars.
  * which indicators to show: those that are averages, medians, rates or shares, near the top of the table's own hierarchy (the
    label's indentation), one per topic first, with the widest spread across places.

Every figure printed is a value from the file or a ratio of two of them; nothing is modelled. Deterministic: pandas only.

    import nl_explore as NX
    lay = NX.detect(frame, header_of, raw_frame)      # None when the table is not an indicator table
    out = NX.build(lay)                               # {"records": [...viz records...], "sentences": [...], "headline": str}
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "2026-10-17.1"

ENTITY_MAX = 400                 # a "where" column has at most this many members
INDICATOR_MIN = 10               # a "what" column names at least this many indicators
DUP_MAX = 0.02                   # rows that repeat (where, what) at most this share
COVER_MIN = 0.50                 # the table fills at least half of where x what
TOTAL_TOL = 0.03                 # a total member equals the sum of the others within 3% ...
TOTAL_SHARE = 0.60               # ... on at least 60% of the indicators where every member has a value
CHARTS_MAX = 6                   # indicator charts at most
GAP_CHARTS_MAX = 2               # men / women (two-group) charts at most
BARS_MAX = 20                    # places shown in one chart (the renderer's cap)

_ROLE_TOTAL = {"total", "all", "both", "persons", "overall"}
_ROLE_GROUPS = {"men": "Men+", "man": "Men+", "male": "Male", "males": "Male", "women": "Women+", "woman": "Women+",
                "female": "Female", "females": "Female", "boys": "Boys", "girls": "Girls"}
_ROLE_LO = {"low", "lo", "lower", "lcl", "lb", "min"}
_ROLE_HI = {"hi", "high", "upper", "ucl", "ub", "max"}
_CI_WORDS = {"ci", "cl", "interval", "bound", "bounds", "limit", "limits"}
_AVG_RX = re.compile(r"\b(average|mean|median|per capita|rate|ratio|index|prevalence|percent|percentage|share|proportion)\b|\(\$\)|\(%\)|%",
                     re.I)
_MONEY_RX = re.compile(r"\(\$\)|\$|\bincome\b|\bdollars?\b|\bcost\b|\bwage|\bsalar|\bearnings\b|\bprice\b|\brent\b", re.I)
_PCT_RX = re.compile(r"\(%\)|%|\bpercent|\bpercentage\b|\brate\b|\bprevalence\b|\bshare\b|\bproportion\b", re.I)


# --------------------------------------------------------------------------------------------- reading the layout
def _is_num(s: Any) -> Any:
    import pandas as pd
    return pd.to_numeric(s, errors="coerce")


def _text_cols(frame: Any) -> List[str]:
    out = []
    for c in frame.columns:
        s = frame[c]
        if s.dtype == object:
            t = s.dropna().astype(str).str.strip()
            t = t[t != ""]
            if len(t) and _is_num(t).notna().mean() < 0.9:
                out.append(c)
    return out


def _digit_share(values: Sequence[str]) -> float:
    txt = "".join(str(v) for v in values[:200])
    return sum(ch.isdigit() for ch in txt) / max(1, len(txt))


def _tokens(header: str) -> List[str]:
    h = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(header))
    return [t for t in re.split(r"[^A-Za-z0-9]+", h.lower()) if t]


def value_families(num_cols: List[str], header_of: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """{family: {"total": col, "groups": {label: col}, "lo": {role: col}, "hi": {role: col}}} from the headers alone:
    C1_COUNT_TOTAL / C2_COUNT_MEN+ / C4_COUNT_LOW_CI_TOTAL ... give family "count". A column with no role word is its own
    family's total."""
    fams: Dict[str, Dict[str, Any]] = {}
    for c in num_cols:
        toks = _tokens(header_of.get(c, c))
        if toks and re.fullmatch(r"c\d+", toks[0]):
            toks = toks[1:]                               # the publisher's column number (C1_, C12_)
        bound = "lo" if any(t in _ROLE_LO for t in toks) else "hi" if any(t in _ROLE_HI for t in toks) else ""
        if bound and not any(t in _CI_WORDS for t in toks) and not any(t in ("low", "high", "lower", "upper") for t in toks):
            bound = ""
        role = next((_ROLE_GROUPS[t] for t in toks if t in _ROLE_GROUPS), "")
        rest = [t for t in toks if t not in _ROLE_TOTAL and t not in _ROLE_GROUPS and t not in _ROLE_LO and t not in _ROLE_HI
                and t not in _CI_WORDS]
        fam = " ".join(rest) or "value"
        f = fams.setdefault(fam, {"total": None, "groups": {}, "lo": {}, "hi": {}})
        key = role or "total"
        if bound:
            f[bound].setdefault(key, c)
        elif role:
            f["groups"].setdefault(role, c)
        elif f["total"] is None:
            f["total"] = c
    return {k: v for k, v in fams.items() if v["total"] is not None}


def _measures_rows(frame: Any, col: str, ent: str) -> bool:
    """A value column measures the rows: not the engine's row number (source_line, or any column that counts 1, 2, 3 down the file),
    and not an attribute of the member repeated on all its rows (a region's response rate, its code)."""
    import pandas as pd
    if str(col).lower() in ("source_line", "row", "row_number", "rownum", "index"):
        return False
    v = _is_num(frame[col])
    if v.notna().all() and v.is_monotonic_increasing and v.is_unique:
        return False
    per = pd.DataFrame({"e": frame[ent].astype(str), "v": v}).dropna().groupby("e")["v"].nunique()
    return bool(len(per) and float((per > 1).mean()) >= 0.5)


def detect(frame: Any, header_of: Dict[str, str], raw_frame: Any = None) -> Optional[Dict[str, Any]]:
    """The indicator layout of `frame` (the rows the engine kept, landed column names), or None. raw_frame: the file as written
    (text, the same rows in the same order before cleaning), for the indentation of the labels; optional."""
    import numpy as np
    import pandas as pd
    if frame is None or len(frame) < 20:
        return None
    n = len(frame)
    texts = _text_cols(frame)
    nums = [c for c in frame.columns if c not in texts and _is_num(frame[c]).notna().mean() >= 0.5]
    distinct = {c: int(frame[c].nunique(dropna=True)) for c in frame.columns}
    ent_c = [c for c in texts if 2 <= distinct[c] <= ENTITY_MAX]
    ind_c = [c for c in frame.columns if distinct[c] >= INDICATOR_MIN and distinct[c] < n and c not in nums[:0]]
    best = None
    for e in ent_c:
        for i in ind_c:
            if i == e or distinct[i] <= distinct[e]:
                continue
            k = frame[[e, i]].astype(str)
            dup = 1.0 - len(k.drop_duplicates()) / n
            cover = n / float(distinct[e] * distinct[i])
            if dup > DUP_MAX or cover < COVER_MIN:
                continue
            # the "where" column: readable (letters, few digits), then more members
            score = (round(1 - _digit_share(list(frame[e].astype(str).unique())), 1), distinct[e], -distinct[i])
            if best is None or score > best[0]:
                best = (score, e, i)
    if best is None:
        return None
    _s, ent, ind = best
    # the indicator's printed label: a text column with one label per indicator (CHARACTERISTIC_NAME for CHARACTERISTIC_ID)
    label = ind
    if ind not in texts:
        for c in texts:
            if c in (ent, ind):
                continue
            g = frame[[ind, c]].dropna().astype(str).drop_duplicates()
            if len(g) and g[ind].is_unique and distinct[c] >= 0.5 * distinct[ind]:
                label = c
                break
    if label == ind and ind not in texts:
        return None                                       # numbered indicators with nothing that says what they are
    vals = [c for c in nums if c not in (ent, ind) and distinct[c] > 2 and _measures_rows(frame, c, ent)]
    fams = value_families(vals, header_of)
    if not fams:
        return None
    # the primary family: the richest (groups and intervals beside its total), then the fullest, then in file order (COUNT before RATE)
    order = sorted(fams, key=lambda f: (-(len(fams[f]["groups"]) + len(fams[f]["lo"])),
                                        -int(_is_num(frame[fams[f]["total"]]).notna().sum()), vals.index(fams[f]["total"])))
    prim = order[0]
    v = _is_num(frame[fams[prim]["total"]])
    per = pd.DataFrame({"i": frame[ind].astype(str), "v": v.abs()}).dropna().groupby("i")["v"].median()
    per = per[per > 0]
    mixed = bool(len(per) >= INDICATOR_MIN and float(per.quantile(0.9)) > 50 * max(1e-9, float(per.quantile(0.1))))
    labs = frame[label].astype(str)
    cue = float(labs.apply(lambda s: bool(_AVG_RX.search(s))).mean())
    if not mixed and cue < 0.10:
        return None                                       # one quantity measured many ways: not an indicator table
    lay = {"entity": ent, "indicator": ind, "label": label, "families": fams, "primary": prim, "rows": n,
           "n_entities": distinct[ent], "n_indicators": distinct[ind], "mixed_scale": mixed, "header_of": header_of,
           "frame": frame}
    lay["flags"] = flag_columns(frame, [c for f in fams.values() for c in [f["total"]] + list(f["groups"].values()) +
                                        list(f["lo"].values()) + list(f["hi"].values())], header_of)
    lay["depth"], lay["topic"] = _hierarchy(frame, ind, label, raw_frame, header_of)
    lay["total_member"] = _total_member(lay)
    return lay


def _hierarchy(frame: Any, ind: str, label: str, raw: Any, header_of: Dict[str, str]) -> Tuple[Dict[str, int], Dict[str, str]]:
    """{indicator: depth} from the leading spaces of its label as the file wrote it, and {indicator: its topic} (the nearest label
    of depth 0 before it, in file order). With no raw text, every depth is 0 and every topic is ''."""
    depth: Dict[str, int] = {}
    topic: Dict[str, str] = {}
    keys = frame[ind].astype(str).tolist()
    raw_labels: Optional[List[str]] = None
    if raw is not None:
        h = header_of.get(label, label)
        if h in raw.columns and len(raw) == len(frame):
            raw_labels = raw[h].astype(str).tolist()
        elif h in raw.columns and "source_line" in frame.columns:
            # the kept rows carry the file's line number (header on line 1): the raw label of each kept row
            idx = _is_num(frame["source_line"]) - 2
            col = raw[h].astype(str).tolist()
            raw_labels = [col[int(i)] if i == i and 0 <= int(i) < len(col) else "" for i in idx]
    cur = ""
    for k, lab in zip(keys, raw_labels or [""] * len(keys)):
        if k in depth:
            continue
        sp = len(lab) - len(lab.lstrip(" ")) if raw_labels else 0
        d = sp // 2
        depth[k] = d
        if d == 0:
            cur = _topic_name(lab)
        topic[k] = cur
    return depth, topic


def _topic_name(lab: str) -> str:
    s = " ".join(str(lab).split())
    s = re.sub(r"^Total\s*-\s*", "", s)
    s = re.sub(r"\s*-\s*\d+% sample data$", "", s)
    return s[:90]


def _total_member(lay: Dict[str, Any]) -> Optional[str]:
    """The member whose value is the sum of all the others on most indicators (Canada), checked on the primary family."""
    import pandas as pd
    f = lay["frame"]
    col = lay["families"][lay["primary"]]["total"]
    t = pd.DataFrame({"e": f[lay["entity"]].astype(str), "i": f[lay["indicator"]].astype(str), "v": _is_num(f[col])})
    wide = t.pivot_table(index="i", columns="e", values="v", aggfunc="first").dropna()
    if wide.shape[1] < 3 or len(wide) < INDICATOR_MIN:
        return None
    tot = wide.sum(axis=1)
    best, share = None, 0.0
    for m in wide.columns:
        others = tot - wide[m]
        ok = ((wide[m] - others).abs() <= TOTAL_TOL * others.abs().clip(lower=1)) & (others.abs() > 0)
        s = float(ok.mean())
        if s > share:
            best, share = m, s
    return best if share >= TOTAL_SHARE else None


# --------------------------------------------------------------------------------------------- one indicator's figures
def _fmt_num(v: float, kind: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if kind == "pct":
        return ("%.1f%%" % v).replace(".0%", "%")
    if kind == "money":
        return "$" + format(int(round(v)), ",") if abs(v) >= 100 else "$%.2f" % v
    if abs(v) >= 100 or float(v).is_integer():
        return format(int(round(v)), ",")
    return ("%.2f" % v).rstrip("0").rstrip(".")


def flag_columns(frame: Any, value_cols: List[str], header_of: Dict[str, str]) -> Dict[str, str]:
    """{value column: its flag column}: a column right after a value column, named like a flag (SYMBOL, FLAG, STATUS, NOTE,
    OBS_STATUS), holding a few short codes (\"...\", x, F, E). A filled flag means the publisher did not stand behind that value."""
    cols = list(frame.columns)
    out: Dict[str, str] = {}
    for v in value_cols:
        if v not in cols:
            continue
        i = cols.index(v)
        if i + 1 >= len(cols):
            continue
        f = cols[i + 1]
        h = str(header_of.get(f, f)).lower()
        if not re.match(r"(symbol|flag|status|obs_status|qual|note)", re.sub(r"[^a-z_]", "", h)):
            continue
        s = frame[f].dropna().astype(str).str.strip()
        s = s[s != ""]
        if s.nunique() <= 12 and (not len(s) or s.str.len().max() <= 4):
            out[v] = f
    return out


def _vals_of(rows: Any, col: str, flags: Dict[str, str]) -> List[Any]:
    v = _is_num(rows[col])
    f = flags.get(col)
    if f is not None and f in rows.columns:
        bad = rows[f].fillna("").astype(str).str.strip() != ""
        v = v.mask(bad)
    return v.tolist()


def _indicator_values(lay: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """The comparable figures of one indicator: {label, kind (pct|money|number), family, by member: value, lo, hi, groups}. Cached on
    the layout (choose reads every indicator, more than once)."""
    memo = lay.setdefault("_iv", {})
    if key not in memo:
        memo[key] = _indicator_values_of(lay, key)
    return memo[key]


def _rows_by_indicator(lay: Dict[str, Any]) -> Dict[str, Any]:
    if "_groups" not in lay:
        f = lay["frame"]
        lay["_groups"] = {str(k): g for k, g in f.groupby(f[lay["indicator"]].astype(str), sort=False)}
    return lay["_groups"]


def _indicator_values_of(lay: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    rows = _rows_by_indicator(lay).get(key)
    if rows is None:
        return None
    if not len(rows):
        return None
    label = " ".join(str(rows[lay["label"]].iloc[0]).split())
    fams = lay["families"]
    ent = rows[lay["entity"]].astype(str).tolist()
    # the comparable family: a percentage beside the count when they differ on this indicator (a share of the group), else
    # the primary family's value itself (an average, a median, a dollar figure)
    use, kind = lay["primary"], "number"
    for fam, spec in fams.items():
        if fam == lay["primary"]:
            continue
        a = _is_num(rows[spec["total"]])
        b = _is_num(rows[fams[lay["primary"]]["total"]])
        ok = a.notna() & b.notna()
        if ok.sum() >= 2 and float((a[ok] - b[ok]).abs().max()) > 1e-9 and float(a[ok].max()) <= 100.0001 and \
                float(a[ok].min()) >= 0 and re.search(r"rate|pct|percent|share", fam):
            use, kind = fam, "pct"
            break
    if kind != "pct":
        kind = "pct" if _PCT_RX.search(label) and not _MONEY_RX.search(label) else "money" if _MONEY_RX.search(label) and \
            re.search(r"average|median|\(\$\)|\$", label, re.I) else "number"
    spec = fams[use]
    fl = lay.get("flags") or {}
    val = dict(zip(ent, _vals_of(rows, spec["total"], fl)))
    lo = dict(zip(ent, _vals_of(rows, spec["lo"]["total"], fl))) if spec["lo"].get("total") else {}
    hi = dict(zip(ent, _vals_of(rows, spec["hi"]["total"], fl))) if spec["hi"].get("total") else {}
    groups = {g: dict(zip(ent, _vals_of(rows, c, fl))) for g, c in spec["groups"].items()}
    glo = {g: dict(zip(ent, _vals_of(rows, c, fl))) for g, c in spec["lo"].items() if g != "total"}
    ghi = {g: dict(zip(ent, _vals_of(rows, c, fl))) for g, c in spec["hi"].items() if g != "total"}
    return {"key": key, "label": label, "kind": kind, "family": use, "value": val, "lo": lo, "hi": hi, "groups": groups,
            "glo": glo, "ghi": ghi, "depth": lay["depth"].get(key, 0), "topic": lay["topic"].get(key, "")}


def _ok(v: Any) -> bool:
    return isinstance(v, (int, float)) and not math.isnan(float(v))


def _members(lay: Dict[str, Any], iv: Dict[str, Any]) -> List[str]:
    tm = lay.get("total_member")
    return [m for m, v in iv["value"].items() if m != tm and _ok(v)]


def _score(lay: Dict[str, Any], iv: Dict[str, Any]) -> float:
    ms = _members(lay, iv)
    if len(ms) < max(2, int(0.8 * (lay["n_entities"] - (1 if lay.get("total_member") else 0)))):
        return -1.0
    xs = [float(iv["value"][m]) for m in ms]
    mean = sum(xs) / len(xs)
    if mean == 0:
        return -1.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))
    cv = sd / abs(mean)
    s = min(1.0, 2.0 * cv)                                   # spread counts, but a big spread is not what makes a measure matter
    if _AVG_RX.search(iv["label"]) or iv["kind"] == "pct":
        s += 3.0
    if iv["kind"] == "money":
        s += 1.0
    s += max(0.0, 1.5 - 0.5 * iv["depth"])
    s -= min(2.5, max(0.0, (len(iv["label"]) - 55) / 25.0))  # "... provided by local government, First Nation or Indian band": a detail
    if re.match(r"^total\b", iv["label"], re.I) or iv["kind"] == "number" and iv["family"] == lay["primary"]:
        s -= 2.5                                           # a raw head count compares the members' sizes, not the members
    if iv["kind"] == "pct" and (min(xs) >= 99.5 or max(xs) <= 0.5):
        s -= 3.0                                           # 100% everywhere: nothing to compare
    return s


def _yearless(s: str) -> str:
    return " ".join(re.sub(r"\b(19|20)\d\d\b", "", str(s).lower()).split())


def _year_of(s: str) -> int:
    ys = [int(y) for y in re.findall(r"\b((?:19|20)\d\d)\b", str(s))]
    return max(ys) if ys else 0


def choose(lay: Dict[str, Any], limit: int = CHARTS_MAX) -> List[Dict[str, Any]]:
    """The indicators worth a chart: scored, one per topic first (a topic and a label count once whatever year they name: the latest
    year's is kept), then the next best."""
    f = lay["frame"]
    keys = list(dict.fromkeys(f[lay["indicator"]].astype(str).tolist()))
    topic_rank: Dict[str, int] = {}
    for k in keys:
        topic_rank.setdefault(_yearless(lay["topic"].get(k, "")), len(topic_rank))
    best: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}
    for k in keys:
        iv = _indicator_values(lay, k)
        if iv is None:
            continue
        sc = _score(lay, iv)
        if sc <= 0:
            continue
        # a publisher lists its main topics first: the topic's place in the file, not the indicator's
        sc += 3.0 * (1.0 - topic_rank.get(_yearless(iv["topic"]), 0) / float(max(1, len(topic_rank))))
        if re.search(r"\btotal\b", iv["label"], re.I) and not re.match(r"^total\b", iv["label"], re.I):
            sc += 0.5                                          # "Average total income": the general measure of its topic
        lk = _yearless(iv["label"]) + "|" + _yearless(iv["topic"])
        prev = best.get(lk)
        y = _year_of(iv["label"] + " " + iv["topic"])
        if prev is None or y > _year_of(prev[2]["label"] + " " + prev[2]["topic"]) or \
                (y == _year_of(prev[2]["label"] + " " + prev[2]["topic"]) and sc > prev[0]):
            best[lk] = (sc, keys.index(k), iv)
    scored = sorted(best.values(), key=lambda t: (-t[0], t[1]))
    out: List[Dict[str, Any]] = []
    topics, labels = set(), set()
    per_kind: Dict[str, int] = {}
    for strict in (True, False):
        for sc, _i, iv in scored:
            # first pass: one per subject (the topic's first two words: every "Income statistics for ..." is one subject) and at
            # most half the charts in one unit; second pass: one per topic
            tk = " ".join(_yearless(iv["topic"]).split()[:2]) if strict else _yearless(iv["topic"])
            lk = _yearless(iv["label"])
            if iv in out or (tk in topics and tk) or lk in labels:
                continue
            if strict and per_kind.get(iv["kind"], 0) >= (limit + 1) // 2:
                continue
            out.append(iv)
            topics.add(tk)
            labels.add(lk)
            per_kind[iv["kind"]] = per_kind.get(iv["kind"], 0) + 1
            if len(out) >= limit:
                return out
    for sc, _i, iv in scored:
        if len(out) >= limit:
            break
        if iv not in out and _yearless(iv["label"]) not in labels:
            out.append(iv)
            labels.add(_yearless(iv["label"]))
    return out


# --------------------------------------------------------------------------------------------- records and sentences
def _record(chart: str, title: str, subtitle: str, measure: Dict[str, Any], data: Dict[str, Any], table: Dict[str, Any],
            summary: str, inputs: Dict[str, Any], source: str) -> Dict[str, Any]:
    import nl_viz as _nv
    return _nv._record(chart, title, subtitle, "other", title, [], None, None, measure, data, table, summary, (0, ""),
                       source, inputs)


def _ref_text(lay: Dict[str, Any], iv: Dict[str, Any]) -> str:
    tm = lay.get("total_member")
    if tm and _ok(iv["value"].get(tm)):
        return "%s as a whole: %s" % (tm, _fmt_num(float(iv["value"][tm]), iv["kind"]))
    return ""


def _short(label: str, n: int = 90) -> str:
    s = re.sub(r"\s*-\s*\d+% sample data$", "", label)
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _titled(iv: Dict[str, Any], n: int = 90) -> str:
    """The indicator's label, with its topic in front when the label alone does not say what it counts ("French" is "Knowledge of
    official languages: French")."""
    lab = _short(iv["label"], n)
    top = iv.get("topic") or ""
    if top and len(iv["label"]) < 40 and top.lower() not in iv["label"].lower() and \
            not top.lower().startswith(iv["label"].lower()):
        top = _topic_short(top, 70)
        return _short("%s: %s" % (top, iv["label"]), n)
    return lab


def indicator_record(lay: Dict[str, Any], iv: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    ent_h = humane(lay["header_of"].get(lay["entity"], lay["entity"]))
    ms = sorted(_members(lay, iv), key=lambda m: -float(iv["value"][m]))[:BARS_MAX]
    fmt = lambda v: _fmt_num(float(v), iv["kind"])          # noqa: E731
    ref = _ref_text(lay, iv)
    tm = lay.get("total_member")
    ref_v = {"label": tm, "value": float(iv["value"][tm]), "text": _fmt_num(float(iv["value"][tm]), iv["kind"])} \
        if tm and _ok(iv["value"].get(tm)) else None
    title = _titled(iv)
    unit = "%" if iv["kind"] == "pct" else "$" if iv["kind"] == "money" else ""
    with_ci = all(_ok(iv["lo"].get(m)) and _ok(iv["hi"].get(m)) and float(iv["lo"][m]) <= float(iv["value"][m]) <=
                  float(iv["hi"][m]) for m in ms) and bool(iv["lo"])
    top, bot = ms[0], ms[-1]
    sent = "%s: highest in %s (%s), lowest in %s (%s)%s." % (
        _titled(iv, 140), top, fmt(iv["value"][top]), bot, fmt(iv["value"][bot]), "; " + ref if ref else "")
    sub = "Each %s, highest first%s%s" % (ent_h, "; " + ref if ref else "",
                                           "; the line is its 95% confidence interval" if with_ci else "")
    inputs = {"columns": [lay["header_of"].get(c, c) for c in (lay["entity"], lay["label"],
                                                                 lay["families"][iv["family"]]["total"])],
              "rows": len(ms), "months": None, "op": "one indicator's value per %s, as the file states it" % ent_h}
    src = "The rows of this indicator in the file, one per %s; no figure added across indicators" % ent_h
    if with_ci:
        rows = [{"label": m, "n": 1, "center": float(iv["value"][m]), "lo": float(iv["lo"][m]), "hi": float(iv["hi"][m]),
                 "median": float(iv["value"][m]),
                 "texts": {"n": "1", "center": fmt(iv["value"][m]), "lo": fmt(iv["lo"][m]), "hi": fmt(iv["hi"][m]),
                           "median": fmt(iv["value"][m])}} for m in ms]
        data = {"rows": rows, "interval": True}
        if ref_v is not None:
            data["ref"] = ref_v
        rec = _record("group_ranges", title, sub, {"label": title, "unit": unit, "kind": "average"}, data,
                      {"cols": [ent_h, "Value", "95% interval"],
                       "rows": [[r["label"], r["texts"]["center"], "%s to %s" % (r["texts"]["lo"], r["texts"]["hi"])]
                                for r in rows]}, sent, inputs, src)
    else:
        rows = [{"label": m, "values": [float(iv["value"][m])], "texts": [fmt(iv["value"][m])]} for m in ms]
        rec = _record("bar_chart", title, sub, {"label": title, "unit": unit, "kind": "average" if iv["kind"] != "number"
                                                else "amount"},
                      dict({"series": [title[:40]], "rows": rows, "other": False}, **({"ref": ref_v} if ref_v else {})),
                      {"cols": [ent_h, title[:60]], "rows": [[r["label"], r["texts"][0]] for r in rows]}, sent, inputs, src)
    return rec, sent


def gap_record(lay: Dict[str, Any], iv: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], List[str]]]:
    """Two groups (Men+ / Women+) side by side for one indicator, with the second as a % of the first; None without two groups."""
    gs = list(iv["groups"])
    if len(gs) < 2:
        return None
    a, b = gs[0], gs[1]
    ent_h = humane(lay["header_of"].get(lay["entity"], lay["entity"]))
    ms = [m for m in iv["value"] if _ok(iv["groups"][a].get(m)) and _ok(iv["groups"][b].get(m)) and float(iv["groups"][a][m]) != 0]
    if len(ms) < 3:
        return None
    ratio = {m: 100.0 * float(iv["groups"][b][m]) / float(iv["groups"][a][m]) for m in ms}
    tm = lay.get("total_member")
    places = [m for m in ms if m != tm]
    places.sort(key=lambda m: -abs(math.log(max(ratio[m], 1e-9) / 100.0)))
    fmt = lambda v: _fmt_num(float(v), iv["kind"])          # noqa: E731
    show = sorted(ms, key=lambda m: (m != tm, ratio[m]))[:BARS_MAX]
    rows = [{"label": m, "values": [float(iv["groups"][a][m]), float(iv["groups"][b][m])],
             "texts": [fmt(iv["groups"][a][m]), fmt(iv["groups"][b][m])],
             "change_text": "%.0f%%" % ratio[m]} for m in show]
    title = "%s vs %s: %s" % (a, b, _titled(iv, 80))
    sents = []
    rel = lambda m: "%s is %.0f%% of %s's" % (b, ratio[m], a)   # noqa: E731
    w, nrw = places[0], places[-1]
    s = "%s: %s%s; the furthest from equal is %s (%s), the closest %s (%s)." % (
        _titled(iv, 110), rel(tm) if tm in ratio else "", (" in %s" % tm) if tm in ratio else "", w, rel(w), nrw, rel(nrw))
    above = [m for m in places if ratio[m] > 100]
    below = [m for m in places if ratio[m] < 100]
    if above and len(above) <= len(places) / 3:
        s += " %s is higher than %s only in %s." % (b, a, _join(above))
    elif below and len(below) <= len(places) / 3:
        s += " %s is lower than %s only in %s." % (b, a, _join(below))
    sents.append(s)
    # a gap the confidence intervals cannot tell apart is said so
    lo_a, hi_a, lo_b, hi_b = (iv["glo"].get(a, {}), iv["ghi"].get(a, {}), iv["glo"].get(b, {}), iv["ghi"].get(b, {}))
    if lo_a and lo_b:
        unclear = [m for m in places if all(_ok(d.get(m)) for d in (lo_a, hi_a, lo_b, hi_b)) and
                   max(float(lo_a[m]), float(lo_b[m])) <= min(float(hi_a[m]), float(hi_b[m]))]
        if unclear:
            sents.append("In %s the two 95%% intervals overlap, so the data cannot say which group is higher there." %
                         _join(unclear))
    rec = _record("grouped_bar", title, "%s and %s side by side for each %s%s, then by %s as a share of %s" % (
        a, b, ent_h, (", %s first" % tm) if tm in ratio else "", b, a), {"label": _short(iv["label"], 60), "unit": "%" if iv["kind"] == "pct" else "$" if iv["kind"] == "money" else "",
                             "kind": "average"},
        {"series": [a, b], "rows": rows, "other": False, "order": "given"},
        {"cols": [ent_h, a, b, "%s as %% of %s" % (b, a)],
         "rows": [[r["label"], r["texts"][0], r["texts"][1], "%.0f%%" % ratio[r["label"]]] for r in rows]},
        " ".join(sents), {"columns": [lay["header_of"].get(lay["families"][iv["family"]]["groups"][g], g) for g in (a, b)],
                          "rows": len(rows), "months": None, "op": "two groups' values per %s, and their ratio" % ent_h},
        "The rows of this indicator in the file, one per %s" % ent_h)
    return rec, sents


def _join(xs: List[str]) -> str:
    xs = list(xs)
    if len(xs) <= 1:
        return "".join(xs)
    if len(xs) > 4:
        return ", ".join(xs[:4]) + " and %d more" % (len(xs) - 4)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def humane(header: str) -> str:
    """A column's name as words: GEO_NAME is "place", CHARACTERISTIC_NAME "characteristic", Country Name "country"."""
    toks = [t for t in _tokens(header) if t not in ("name", "names", "label", "desc", "description", "title", "txt", "text")]
    words = " ".join(toks) or str(header).lower()
    return {"geo": "place", "geography": "place", "geo level": "level", "ref area": "place", "area": "area"}.get(words, words)


def _plural_of(w: str) -> str:
    return w if w.endswith("s") else (w[:-1] + "ies") if w.endswith("y") and w[-2:-1] not in "aeiou" else w + "s"


def _topic_short(t: str, n: int = 60) -> str:
    t = re.sub(r"\s+(for|of|in) (the )?(population|private households|employed|labour|persons|people).*$", "", t)
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0] + "…"


def _gap_candidates(lay: Dict[str, Any], chosen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Indicators for the two-group charts: from a wider pool than the charts above (the best 30), those with two groups, the widest
    gap at the total member first (a dollar or a rate gap counts more than a count's), one per subject."""
    pool = choose(lay, 30)
    tm = lay.get("total_member")
    out = []
    for iv in pool:
        gs = list(iv["groups"])
        if len(gs) < 2:
            continue
        a, b = iv["groups"][gs[0]], iv["groups"][gs[1]]
        ref = tm if tm and _ok(a.get(tm)) and _ok(b.get(tm)) else None
        pairs = [(float(a[m]), float(b[m])) for m in ([ref] if ref else list(a)) if _ok(a.get(m)) and _ok(b.get(m)) and float(a[m]) > 0]
        if not pairs or any(y <= 0 for _x, y in pairs):
            continue
        mag = sum(abs(math.log(y / x)) for x, y in pairs) / len(pairs)
        out.append((mag * (1.5 if iv["kind"] == "money" else 1.0), iv))
    out.sort(key=lambda t: -t[0])
    seen, res = set(), []
    for _m, iv in out:
        k = " ".join(_yearless(iv["topic"]).split()[:2])
        if k in seen:
            continue
        seen.add(k)
        res.append(iv)
    return res


PROFILE_LIST_MAX = 60
PROFILE_BYTES_MAX = 9000


def label_key(label: str, topic: str = "") -> str:
    """How a plan's indicator is matched back to the table: its label and topic, spaces folded, case ignored."""
    return " ".join(str(label).split()).casefold() + "||" + " ".join(str(topic).split()).casefold()


def profile_block(lay: Dict[str, Any]) -> Dict[str, Any]:
    """What the AI planner sees of an indicator table: the where/what columns, the total member, and the best PROFILE_LIST_MAX
    indicators (the engine's own ranking) as {id I1.., label, topic, unit}, at most PROFILE_BYTES_MAX bytes. Never a value."""
    cands = choose(lay, PROFILE_LIST_MAX)
    items = []
    for i, iv in enumerate(cands):
        items.append({"id": "I%d" % (i + 1), "label": _short(iv["label"], 120), "topic": _topic_short(iv["topic"], 80),
                      "unit": {"pct": "percent", "money": "dollars", "number": "number"}[iv["kind"]],
                      "two_groups": list(iv["groups"])[:2] if len(iv["groups"]) >= 2 else []})
    import json as _json
    while items and len(_json.dumps(items)) > PROFILE_BYTES_MAX:
        items.pop()
    ent_h = lay["header_of"].get(lay["entity"], lay["entity"])
    lab_h = lay["header_of"].get(lay["label"], lay["label"])
    return {"kind": "indicator_table", "entity_column": ent_h, "indicator_column": lab_h, "members": lay["n_entities"],
            "indicators_total": lay["n_indicators"], "total_member": lay.get("total_member"), "list": items}


def preferred(lay: Dict[str, Any], keys: Sequence[str]) -> List[Dict[str, Any]]:
    """The indicators a plan named (label_key strings), in its order, as _indicator_values; an unknown one is skipped."""
    want = [str(k) for k in keys or []][:CHARTS_MAX]
    if not want:
        return []
    by: Dict[str, Dict[str, Any]] = {}
    for k in dict.fromkeys(lay["frame"][lay["indicator"]].astype(str).tolist()):
        iv = _indicator_values(lay, k)
        if iv is None:
            continue
        by.setdefault(label_key(_short(iv["label"], 120), _topic_short(iv["topic"], 80)), iv)
        by.setdefault(label_key(_short(iv["label"], 120)), iv)
    out = []
    for w in want:
        lab, _s, top = w.partition("||")
        iv = by.get(label_key(lab, top)) or by.get(label_key(lab))
        if iv is not None and iv not in out and _score(lay, iv) > -1:
            out.append(iv)
    return out


def build(lay: Dict[str, Any], prefer: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The explore block: viz records (indicator comparisons, then two-group gaps), the sentences that read them, a headline and a
    plain description of the table. prefer: the indicators the AI plan chose (label_key strings), charted first."""
    first = preferred(lay, prefer or [])
    chosen = first + [iv for iv in choose(lay) if iv not in first and _yearless(iv["label"]) not in
                      {_yearless(x["label"]) for x in first}]
    chosen = chosen[:CHARTS_MAX]
    records, sentences = [], []
    for iv in chosen:
        rec, sent = indicator_record(lay, iv)
        records.append(rec)
        sentences.append(sent)
    gaps = 0
    for iv in _gap_candidates(lay, chosen):
        if gaps >= GAP_CHARTS_MAX:
            break
        g = gap_record(lay, iv)
        if g is None:
            continue
        records.insert(1 + gaps, g[0])
        sentences.extend(g[1])
        gaps += 1
    ent_h = lay["header_of"].get(lay["entity"], lay["entity"])
    lab_h = lay["header_of"].get(lay["label"], lay["label"])
    ent_w, lab_w = humane(ent_h), humane(lab_h)
    tm = lay.get("total_member")
    topics = [t for t in dict.fromkeys(_topic_short(x) for x in lay["topic"].values() if x)]
    topics = list(dict.fromkeys(_yearless(t) and t for t in topics))
    seen_y, uniq = set(), []
    for tp in topics:
        if _yearless(tp) not in seen_y:
            seen_y.add(_yearless(tp))
            uniq.append(tp)
    about = ("Each row is one %s (%s) for one %s (%s): %s %s for %s %s%s. Different %s are in different units in the same value "
             "columns, so nothing is added or ranked across them; each is compared across the %s on its own." % (
                 lab_w, lab_h, ent_w, ent_h, format(lay["n_indicators"], ","), _plural_of(lab_w), lay["n_entities"],
                 _plural_of(ent_w), (", %s being the total of the others" % tm) if tm else "", _plural_of(lab_w), _plural_of(ent_w)))
    if uniq:
        shown = uniq[:4]
        about += " %d topics, among them %s." % (len(uniq), ", ".join(shown[:-1]) + " and " + shown[-1] if len(shown) > 1 else shown[0])
    head = sentences[0] if sentences else about
    ex = [iv for iv in chosen[:3]]
    nxt = ["Ask in step 4, naming a %s: for example \u201cWhich %s has the highest %s?\u201d" % (
        lab_w, ent_w, _short(ex[0]["label"], 70).lower())] if ex else []
    if tm and ex:
        nxt.append("Or compare a %s with %s as a whole: \u201c%s in %s\u201d." % (ent_w, tm, _short(ex[-1]["label"], 60),
                                                                         next((m for m in lay["frame"][lay["entity"]].astype(str).unique()
                                                                               if m != tm), tm)))
    return {"version": VERSION, "kind": "indicator_table", "records": records, "sentences": sentences, "headline": head,
            "about": about, "entity": ent_w, "indicator": lab_w, "entity_column": ent_h, "indicator_column": lab_h,
            "total_member": tm,
            "indicators": [{"label": iv["label"], "kind": iv["kind"], "topic": iv["topic"]} for iv in chosen],
            "chosen_by": "ai" if first else "engine", "next": nxt,
            "n_indicators": lay["n_indicators"], "n_entities": lay["n_entities"]}


# ============================================================================================ a table of rows (no date, no indicators)
# The other table a visitor brings with no date: one row per thing (a state, a college major, a penguin, a wine), with measures and
# perhaps a few categories. The analyst's first moves: rank the things by the measures that matter, compare the categories, see which
# measures move together, and how a key measure is spread. Every figure is the file's value, a mean, a count or a Pearson r.

_CUE = re.compile(r"(rate|per[_ ]|_per\b|median|mean|average|avg|price|income|earning|salary|wage|score|quality|total|count|share|"
                  r"percent|pct|index|crime|death|population|gdp|life|mass|length|height|weight|value|revenue|sales|cost|spend|"
                  r"amount|unemploy|poverty|rating|rank)", re.I)
_STRONG = re.compile(r"(median|mean|average|avg|rate|_per_|per[_ ]capita|per_?100k|share|percent|pct|price|income|earning|salary|wage|"
                     r"score|quality|index|rating|ratio|gdp|expectancy|_rank$)", re.I)
_ORDINAL = re.compile(r"(quality|rating|score|grade|stars|level|satisf)", re.I)
_ID_WORD = re.compile(r"(^|_|\b)(id|code|fips|zip|zipcode|postal|dbn|key|uid|uuid|isbn|number|no)($|_|\b)", re.I)
RANK_SHOW = 10                     # the highest and the lowest shown in a ranking of more than 2 x RANK_SHOW rows
GROUP_MAX = 12                     # levels of a category compared in one chart
BOOT_B, BOOT_SEED = 999, 20261017


def _human(h: str) -> str:
    s = re.sub(r"[_]+", " ", str(h)).strip()
    return s[:1].upper() + s[1:] if s else str(h)


def detect_table(frame: Any, header_of: Dict[str, str], date_col: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A table of things: {label (a column naming each row, or None), measures, categories, n}; None with no measure."""
    import pandas as pd
    if frame is None or len(frame) < 5:
        return None
    n = len(frame)
    texts = _text_cols(frame)
    measures, cats, label = [], [], None
    for c in frame.columns:
        if c == date_col or str(c).lower() == "source_line":
            continue
        h = str(header_of.get(c, c))
        if c in texts:
            d = int(frame[c].nunique(dropna=True))
            filled = int(frame[c].notna().sum())
            vals = frame[c].dropna().astype(str)
            if d >= 0.9 * max(1, filled) and filled >= 0.8 * n and vals.str.contains(r"[A-Za-z]").mean() > 0.8 and \
                    vals.str.len().median() <= 80:
                if label is None or _digit_share(list(vals.unique())) < _digit_share(list(frame[label].dropna().astype(str).unique())):
                    label = c
            elif 2 <= d <= 30 and filled >= 0.5 * n:
                cats.append(c)
            continue
        v = _is_num(frame[c])
        filled = int(v.notna().sum())
        d = int(v.nunique())
        if filled < max(5, 0.3 * n) or d < 3:
            continue
        if _ID_WORD.search(h) or (v.notna().all() and v.is_monotonic_increasing and v.is_unique):
            continue
        if re.search(r"(^|_|\b)(year|yr)($|_|\b)", h, re.I) and v.between(1800, 2100).mean() > 0.95:
            continue                                      # a year is a label of the row, not a quantity
        measures.append(c)
        if _ORDINAL.search(h) and 3 <= d <= 10 and (v.dropna() == v.dropna().round()).all():
            cats.append(c)                                # a rating of a few whole steps (wine quality 3 to 8): also a grouping
    if not measures:
        return None
    return {"kind": "table", "label": label, "measures": measures, "categories": cats, "n": n, "frame": frame,
            "header_of": header_of}


def _measure_score(t: Dict[str, Any], c: str, primary: Optional[str]) -> float:
    if primary and c == primary:
        return 100.0
    f = t["frame"]
    v = _is_num(f[c]).dropna()
    h = str(t["header_of"].get(c, c))
    s = 3.0 if _STRONG.search(h) else 1.0 if _CUE.search(h) else 0.0
    if re.search(r"(median|income|earning|salary|wage|price|gdp|quality|score|rating)", h, re.I):
        s += 1.0                                          # the outcome a reader looks for first (earnings, a price, a score)
    s += float(len(v)) / t["n"]
    m = float(v.mean()) if len(v) else 0.0
    s += min(1.0, float(v.std()) / abs(m)) if m else 0.0
    if re.search(r"(low|high|lower|upper)[_ ]?(ci|bound)|_se$|stderr|margin", h, re.I):
        s -= 3.0
    return s


def _boot(v: Any) -> Tuple[float, float]:
    import numpy as np
    rng = np.random.default_rng(BOOT_SEED)
    a = np.asarray(v, dtype=float)
    if len(a) < 2:
        return float(a.mean()), float(a.mean())
    means = a[rng.integers(0, len(a), size=(BOOT_B, len(a)))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _nfmt(v: float) -> str:
    if v is None or v != v:
        return "-"
    a = abs(v)
    if a >= 1e12:
        return "%.2fT" % (v / 1e12)
    if a >= 1e9:
        return "%.2fB" % (v / 1e9)
    if a >= 1e6:
        return "%.2fM" % (v / 1e6)
    if a >= 1000:
        return format(int(round(v)), ",")
    if a >= 100:
        return "%.0f" % v
    if a >= 1:
        return ("%.2f" % v).rstrip("0").rstrip(".")
    return ("%.3f" % v).rstrip("0").rstrip(".") or "0"


def _ranking(t: Dict[str, Any], c: str) -> Optional[Tuple[Dict[str, Any], str]]:
    import pandas as pd
    f, lab = t["frame"], t["label"]
    if lab is None:
        return None
    s = pd.DataFrame({"l": f[lab].astype(str), "v": _is_num(f[c])}).dropna().drop_duplicates("l")
    if len(s) < 3:
        return None
    s = s.sort_values("v", ascending=False)
    mh, lh = _human(t["header_of"].get(c, c)), _human(t["header_of"].get(lab, lab))
    cut = len(s) > 2 * RANK_SHOW
    show = pd.concat([s.head(RANK_SHOW), s.tail(RANK_SHOW)]) if cut else s
    rows = [{"label": str(r.l)[:80], "values": [float(r.v)], "texts": [_nfmt(float(r.v))]} for r in show.itertuples()]
    med = float(s["v"].median())
    top, bot = s.iloc[0], s.iloc[-1]
    sent = "%s is highest for %s (%s) and lowest for %s (%s); the median of the %s %s is %s." % (
        mh, top.l, _nfmt(float(top.v)), bot.l, _nfmt(float(bot.v)), format(len(s), ","), _plural_of(lh.lower()), _nfmt(med))
    sub = ("The %d highest and the %d lowest of %s %s, highest first" % (RANK_SHOW, RANK_SHOW, format(len(s), ","), _plural_of(lh.lower()))
           if cut else "Every %s, highest first" % lh.lower())
    data = {"series": [mh[:40]], "rows": rows, "other": False, "ref": {"label": "median", "value": med, "text": _nfmt(med)}}
    rec = _record("bar_chart", "%s by %s" % (mh, lh.lower()), sub, {"label": mh, "unit": "", "kind": "amount"}, data,
                  {"cols": [lh, mh], "rows": [[r["label"], r["texts"][0]] for r in rows]}, sent,
                  {"columns": [t["header_of"].get(lab, lab), t["header_of"].get(c, c)], "rows": int(len(s)), "months": None,
                   "op": "each row's value as the file states it, sorted"},
                  "The rows of the file, one per %s" % lh.lower())
    return rec, sent


def _groups(t: Dict[str, Any], cat: str, c: str) -> Optional[Tuple[Dict[str, Any], str]]:
    import pandas as pd
    f = t["frame"]
    s = pd.DataFrame({"g": f[cat].astype(str), "v": _is_num(f[c])}).dropna()
    s = s[s["g"].str.strip() != ""]
    cnt = s["g"].value_counts()
    keep = [g for g, k in cnt.items() if k >= 5][:GROUP_MAX]
    if len(keep) < 2:
        return None
    rows = []
    for g in keep:
        v = s.loc[s["g"] == g, "v"].to_numpy(dtype=float)
        lo, hi = _boot(v)
        m = float(v.mean())
        shown = ("%s %s" % (_human(t["header_of"].get(cat, cat)).lower(), g)) if re.fullmatch(r"-?\d+(\.0+)?", str(g)) else str(g)
        rows.append({"label": shown[:80], "n": int(len(v)), "center": m, "lo": min(lo, m), "hi": max(hi, m),
                     "median": float(pd.Series(v).median()),
                     "texts": {"n": format(len(v), ","), "center": _nfmt(m), "lo": _nfmt(min(lo, m)), "hi": _nfmt(max(hi, m)),
                               "median": _nfmt(float(pd.Series(v).median()))}})
    rows.sort(key=lambda r: -r["center"])
    mh, ch = _human(t["header_of"].get(c, c)), _human(t["header_of"].get(cat, cat))
    a, b = rows[0], rows[-1]
    clear = a["lo"] > b["hi"]
    sent = "Average %s is highest for %s (%s, 95%% range %s to %s) and lowest for %s (%s)%s." % (
        mh.lower(), a["label"], a["texts"]["center"], a["texts"]["lo"], a["texts"]["hi"], b["label"], b["texts"]["center"],
        "; the two ranges do not overlap" if clear else "; their ranges overlap, so the data cannot rank them for sure")
    rec = _record("group_ranges", "Average %s by %s" % (mh.lower(), ch.lower()),
                  "Each %s's average, its 95%% bootstrap range (%d resamples) and its median" % (ch.lower(), BOOT_B),
                  {"label": mh, "unit": "", "kind": "average"}, {"rows": rows},
                  {"cols": [ch, "Rows", "Average", "95% range", "Median"],
                   "rows": [[r["label"], r["texts"]["n"], r["texts"]["center"], "%s to %s" % (r["texts"]["lo"], r["texts"]["hi"]),
                             r["texts"]["median"]] for r in rows]}, sent,
                  {"columns": [t["header_of"].get(c, c), t["header_of"].get(cat, cat)], "rows": int(len(s)), "months": None,
                   "op": "mean per group, 95% bootstrap range"}, "The rows of the file, by %s" % ch.lower())
    return rec, sent


def _correlation(t: Dict[str, Any], ms: List[str], key: Optional[str] = None) -> Optional[Tuple[Dict[str, Any], str]]:
    import nl_viz as _nv
    import pandas as pd
    if len(ms) < 3:
        return None
    ms = ms[:8]
    f = t["frame"]
    df = pd.DataFrame({c: _is_num(f[c]) for c in ms})
    names = [_human(t["header_of"].get(c, c))[:40] for c in ms]
    cells, best = [], None
    for i, a in enumerate(ms):
        row = []
        for j, b in enumerate(ms):
            both = df[[a, b]].dropna()
            n = len(both)
            if i == j:
                row.append((1.0, "1", 0, n) if n >= 5 else (None, "", 0, 0))
                continue
            if n < 5 or both[a].std() == 0 or both[b].std() == 0:
                row.append((None, "", 0, 0))
                continue
            r = float(both[a].corr(both[b]))
            row.append(_nv._cell(r, n, _nv._signed_r(_nv._r6(r))))
            if i < j and (best is None or abs(r) > abs(best[0])):
                best = (r, i, j, n)
        cells.append(row)
    if best is None:
        return None
    data, _supp = _nv._grid(names, names, cells, "diverging", _nv._signed_r, "measure", "measure", corr=True)
    if key in ms:
        # the strongest link WITH the key measure is what a reader asks first (alcohol and quality, not two sulfur measures)
        k = ms.index(key)
        kb = [(float(data["values"][k][j2]), j2) for j2 in range(len(ms)) if j2 != k and data["values"][k][j2] is not None]
        if kb:
            rk, jk = max(kb, key=lambda x: abs(x[0]))
            nk = int(df[[ms[k], ms[jk]]].dropna().shape[0])
            best = (rk, k, jk, nk)
    r, i, j, n = best
    sent = "%s and %s move together most closely (r %s over %s rows): %s. An association, not a cause." % (
        names[i], names[j].lower(), _nv._signed_r(_nv._r6(r)), format(n, ","),
        "when one is higher the other tends to be higher too" if r > 0 else "when one is higher the other tends to be lower")
    rec = _record("correlation_heatmap", "How the measures move together",
                  "Pearson r of each pair of measures, on the rows holding both values",
                  {"label": "Pearson r", "unit": "", "kind": "correlation"}, data,
                  _nv._grid_table("measure", names, names, data["text"]), sent,
                  {"columns": names, "rows": int(t["n"]), "months": None, "op": "Pearson r of each pair"},
                  "The rows of the file")
    return rec, sent


def _histogram(t: Dict[str, Any], c: str) -> Optional[Tuple[Dict[str, Any], str]]:
    import numpy as np
    v = _is_num(t["frame"][c]).dropna().to_numpy(dtype=float)
    if len(v) < 20 or len(np.unique(v)) < 5:
        return None
    edges = np.histogram_bin_edges(v, bins="auto")
    if len(edges) > 13:
        edges = np.linspace(v.min(), v.max(), 11)
    cnt, edges = np.histogram(v, bins=edges)
    mh = _human(t["header_of"].get(c, c))
    rows = [{"label": "%s to %s" % (_nfmt(float(edges[k])), _nfmt(float(edges[k + 1]))), "values": [float(cnt[k])],
             "texts": [format(int(cnt[k]), ",")]} for k in range(len(cnt))]
    q1, med, q3 = (float(x) for x in np.percentile(v, [25, 50, 75]))
    sent = "Half of the %s values of %s lie between %s and %s (median %s); the lowest is %s and the highest %s." % (
        format(len(v), ","), mh.lower(), _nfmt(q1), _nfmt(q3), _nfmt(med), _nfmt(float(v.min())), _nfmt(float(v.max())))
    rec = _record("bar_chart", "How %s is spread" % mh.lower(), "Rows in each band of %s, lowest band first" % mh.lower(),
                  {"label": "rows", "unit": "", "kind": "count"}, {"series": ["Rows"], "rows": rows[:20], "other": False, "order": "given"},
                  {"cols": [mh, "Rows"], "rows": [[r["label"], r["texts"][0]] for r in rows[:20]]}, sent,
                  {"columns": [t["header_of"].get(c, c)], "rows": int(len(v)), "months": None, "op": "rows counted per band"},
                  "The rows of the file")
    return rec, sent


def _counts(t: Dict[str, Any], cat: str) -> Optional[Tuple[Dict[str, Any], str]]:
    f = t["frame"]
    vc = f[cat].dropna().astype(str).value_counts()
    if len(vc) < 2:
        return None
    ch = _human(t["header_of"].get(cat, cat))
    rows = [{"label": str(k)[:80], "values": [float(v)], "texts": [format(int(v), ",")]} for k, v in vc.head(20).items()]
    tot = int(vc.sum())
    sent = "%s: %s is the most common (%s of %s rows, %.0f%%)." % (ch, vc.index[0], format(int(vc.iloc[0]), ","), format(tot, ","),
                                                                   100.0 * vc.iloc[0] / tot)
    rec = _record("bar_chart", "Rows by %s" % ch.lower(), "How many rows each %s holds, the most first" % ch.lower(),
                  {"label": "rows", "unit": "", "kind": "count"}, {"series": ["Rows"], "rows": rows, "other": False},
                  {"cols": [ch, "Rows"], "rows": [[r["label"], r["texts"][0]] for r in rows]}, sent,
                  {"columns": [t["header_of"].get(cat, cat)], "rows": tot, "months": None, "op": "rows counted"}, "The rows of the file")
    return rec, sent


def build_table(t: Dict[str, Any], primary: Optional[str] = None, limit: int = 8) -> Dict[str, Any]:
    """The explore block of a table of things: rankings, group comparisons, the measures' correlations and a spread."""
    ms = sorted(t["measures"], key=lambda c: -_measure_score(t, c, primary))
    cats = sorted(t["categories"], key=lambda c: (int(t["frame"][c].nunique()) > GROUP_MAX, int(t["frame"][c].nunique())))
    recs: List[Tuple[Dict[str, Any], str]] = []
    key = ms[0]
    if cats and key == cats[0]:
        import pandas as pd
        kv = _is_num(t["frame"][key])
        def link(m: str) -> float:
            b = pd.DataFrame({"a": kv, "b": _is_num(t["frame"][m])}).dropna()
            return abs(float(b["a"].corr(b["b"]))) if len(b) >= 5 and b["b"].std() > 0 else 0.0
        others = sorted([m for m in ms if m != key], key=lambda m: -link(m))
    else:
        others = [m for m in ms if not cats or m != cats[0]]
    for fn, args in ([(_ranking, (t, m)) for m in ms[:2]] + [(_groups, (t, cats[0], m)) for m in others[:2] if cats] +
                     [(_correlation, (t, ms, key)), (_histogram, (t, key))] + [(_counts, (t, c)) for c in cats[:1] if c != key] +
                     [(_groups, (t, cats[1], key)) for _x in [0] if len(cats) > 1]):
        if len(recs) >= limit:
            break
        try:
            got = fn(*args)
        except Exception:  # noqa: BLE001 - one chart that cannot be built never stops the others
            got = None
        if got is not None:
            recs.append(got)
    lab_w = humane(t["header_of"].get(t["label"], t["label"])) if t["label"] else "record"
    # rows that are US places (a state a row): the lead measure on the map, before the rankings
    if t["label"]:
        try:
            import nl_geo
            import pandas as pd
            fr = t["frame"]
            sv = pd.DataFrame({"l": fr[t["label"]].astype(str).str.strip(), "v": _is_num(fr[key])}).dropna().drop_duplicates("l")
            mh0 = _human(t["header_of"].get(key, key))
            geo = nl_geo.map_record(list(sv["l"]), None, [""], {r.l: [float(r.v)] for r in sv.itertuples()}, mh0,
                                    "%" if re.search(r"(?i)percent|%|\brate\b|share", mh0) and float(sv["v"].max()) <= 100 else "",
                                    False, lab_w, fmt=_nfmt)
            if geo is not None:
                recs.insert(0, (geo, geo.get("summary") or ""))
                recs = recs[:limit]
        except Exception:  # noqa: BLE001 - the table stands without its map
            pass
    about = "Each row is one %s: %s rows, %d measure%s%s. No column holds dates, so this report compares the rows rather than " \
            "following a change over time." % (
                lab_w, format(t["n"], ","), len(ms), "" if len(ms) == 1 else "s",
                (" and %d categor%s (%s)" % (len(cats), "y" if len(cats) == 1 else "ies",
                                             ", ".join(_human(t["header_of"].get(c, c)).lower() for c in cats[:4]))) if cats else "")
    sentences = [s for r, s in recs if s and r.get("kind") != "geo"]
    return {"version": VERSION, "kind": "table", "records": [r for r, _s in recs], "sentences": sentences,
            "headline": sentences[0] if sentences else about, "about": about, "entity": lab_w, "indicator": "measure",
            "entity_column": t["header_of"].get(t["label"], t["label"]) if t["label"] else None, "indicator_column": None,
            "total_member": None, "measures": [t["header_of"].get(c, c) for c in ms[:8]], "chosen_by": "ai" if primary else "engine"}


# ============================================================================================ a panel: things x years
# Countries by year (OWID), states by year (deaths by cause), with a Year column and no dates: what changed, for the whole and for the
# leaders, and who leads now. A member that is the total of the others (World, United States) or a named group of them (Africa,
# High-income countries) is never ranked beside its members: it is the line the members are read against.

_YEAR_HEAD = re.compile(r"(^|_|\b)(year|yr|fiscal_?year|ref_?year|time)($|_|\b)", re.I)
_GROUP_WORDS = re.compile(r"\b(world|income|countries|region|union|oecd|members|total|all|area|excl|incl|asia|africa|europe|america|"
                          r"oceania|international|ussr|g20|g7|opec|eu-?27|non-oecd|kp annex|small states|dividend|ida|ibrd|"
                          r"classified)\b|\((?:[^)]*\b(?:excl|incl|ida|ibrd|countries|income|members|total|hipc|fcv)\b)", re.I)
# The World Bank's aggregate codes (its API's region "Aggregates"; WLD World, HIC High income, IBT IDA & IBRD total, ...): with the
# file's code column beside the names, a member is a group exactly when its code is one of these, never by its name ("IBRD only"
# reads like no group word; "South Africa" reads like one)
WB_AGGREGATE_CODES = frozenset(
    "AFE AFW ARB CEB CSS EAP EAR EAS ECA ECS EMU EUU FCS FCV HIC HPC IBD IBT IDA IDB IDX INX LAC LCN LDC LIC LMC LMY LTE MEA MIC MNA "
    "NAC OED OSS PRE PSS PST SAS SSA SSF SST TEA TEC TLA TMN TSA TSS UMC WLD".split())
LINES_MAX_YEARS = 30
PANEL_LINES = 5


def _region_lists() -> Tuple[frozenset, frozenset]:
    """(the world regions, the countries) of context_terms.json: its "regions" list in the blocks its "region_groups" counts
    (35 world regions, then 202 countries and territories, ...)."""
    import json as _json
    import os as _os
    try:
        with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "context_terms.json"), encoding="utf-8") as fh:
            d = _json.load(fh)
        regs, counts = list(d.get("regions") or []), dict(d.get("region_groups") or {})
        n_w = int(counts.get("world regions") or 0)
        n_c = int(counts.get("countries and territories") or 0)
        return (frozenset(str(x).casefold() for x in regs[:n_w]), frozenset(str(x).casefold() for x in regs[n_w:n_w + n_c]))
    except Exception:  # noqa: BLE001
        return frozenset(), frozenset()


def _code_column(frame: Any, ent: str, texts: Sequence[str]) -> Optional[str]:
    """A column of short upper-case codes (ISO 3166 ABW, the World Bank's WLD) one-to-one with the entity's names, or None."""
    names = frame[ent].astype(str)
    for c in texts:
        if c == ent:
            continue
        v = frame[c].astype(str)
        if not v.str.fullmatch(r"[A-Z][A-Z0-9]{1,2}").mean() >= 0.98:
            continue
        pair = pd_unique_pairs(names, v)
        if pair is not None and pair[0] == pair[1] == pair[2]:
            return c
    return None


def pd_unique_pairs(a: Any, b: Any) -> Optional[Tuple[int, int, int]]:
    """(distinct a, distinct b, distinct (a, b) pairs): equal three ways when a and b name the same things one to one."""
    try:
        import pandas as pd
        return int(a.nunique()), int(b.nunique()), int(len(pd.DataFrame({"a": a, "b": b}).drop_duplicates()))
    except Exception:  # noqa: BLE001
        return None


def _groups_of(members: Sequence[str], code_of: Optional[Dict[str, str]] = None) -> set:
    """The members that are groups of others (World, High income, Sub-Saharan Africa), never ranked beside the countries. By the World
    Bank's own codes when the file carries them; else a known world region is a group, a known country never is (South Africa, Puerto
    Rico (US)), and any other name is a group when it reads like one."""
    if code_of and "WLD" in set(code_of.values()):
        return {m for m in members if code_of.get(m) in WB_AGGREGATE_CODES}
    world, countries = _region_lists()
    out = set()
    for m in members:
        k = str(m).casefold().strip()
        if k in world:
            out.add(m)
        elif k in countries or re.sub(r"\s*\([^)]*\)\s*$", "", k) in countries:
            continue
        elif _GROUP_WORDS.search(str(m)):
            out.add(m)
    return out


def detect_panel(frame: Any, header_of: Dict[str, str], name_hint: str = "") -> Optional[Dict[str, Any]]:
    """{year, entity, dim (a second grouping such as a cause, filtered to its total level), measures, aggregates, total} or None."""
    import pandas as pd
    if frame is None or len(frame) < 20:
        return None
    texts = _text_cols(frame)
    year = None
    for c in frame.columns:
        h = str(header_of.get(c, c))
        if not _YEAR_HEAD.search(h):
            continue
        y = _is_num(frame[c])
        if y.notna().mean() >= 0.95 and y.dropna().between(1500, 2100).mean() >= 0.95 and (y.dropna() == y.dropna().round()).all() \
                and y.nunique() >= 5:
            year = c
            break
    if year is None:
        return None
    n = len(frame)
    ents = [c for c in texts if 2 <= frame[c].nunique() <= 2000]
    best = None
    for e in ents:
        k = frame[[e, year]].astype(str)
        dup = 1.0 - len(k.drop_duplicates()) / n
        dim = None
        if dup > DUP_MAX:
            for d in texts:
                if d in (e,) or not 2 <= frame[d].nunique() <= 60:
                    continue
                if 1.0 - len(frame[[e, year, d]].astype(str).drop_duplicates()) / n <= DUP_MAX:
                    dim = d
                    break
            if dim is None:
                continue
        score = (dim is None, round(1 - _digit_share(list(frame[e].astype(str).unique())), 1), int(frame[e].nunique()))
        if best is None or score > best[0]:
            best = (score, e, dim)
    if best is None:
        return None
    _s, ent, dim = best
    hint = set(re.findall(r"[a-z0-9]+", str(name_hint).lower()))
    measures = []
    for c in frame.columns:
        if c in (year, ent, dim) or c in texts or str(c).lower() == "source_line":
            continue
        h = str(header_of.get(c, c))
        v = _is_num(frame[c])
        if v.notna().sum() < 5 or v.nunique() < 5 or _ID_WORD.search(h) or _YEAR_HEAD.search(h):
            continue
        measures.append(c)
    if not measures:
        return None
    f = frame
    if dim is not None:
        # the second grouping's total level (All causes): the level whose values are the sum of the others, else the commonest
        lv = _dim_total(f, ent, year, dim, measures[0])
        f = f[f[dim].astype(str) == lv] if lv is not None else f
    members = [str(m) for m in f[ent].dropna().astype(str).unique()]
    code_c = _code_column(f, ent, texts)
    code_of = dict(zip(f[ent].astype(str), f[code_c].astype(str))) if code_c else None
    aggs = _groups_of(members, code_of)
    total = _panel_total(f, ent, year, measures[0], members)
    if total:
        aggs.add(total)
    if not total:
        total = next((m for m in members if m.casefold() == "world"), None)
    # a column of place codes beside the names (a Census CBSA code, a FIPS code): one code per member, for the map
    codes = None
    for c in f.columns:
        if c in (year, ent, dim) or c in measures:
            continue
        sub = f[[ent, c]].astype(str).drop_duplicates()
        if len(sub) and sub[c].str.fullmatch(r"\d{1,5}(\.0)?").mean() >= 0.95 and sub.groupby(ent)[c].nunique().max() == 1:
            codes = dict(zip(sub[ent], sub[c]))
            break
    return {"kind": "panel", "year": year, "entity": ent, "dim": dim, "dim_level": (lv if dim is not None else None), "codes": codes,
            "measures": measures, "aggregates": aggs, "total": total, "frame": f, "full": frame, "header_of": header_of,
            "hint": hint, "n": len(f)}


def _dim_total(f: Any, ent: str, year: str, dim: str, m: str) -> Optional[str]:
    import pandas as pd
    t = pd.DataFrame({"e": f[ent].astype(str), "y": f[year].astype(str), "d": f[dim].astype(str), "v": _is_num(f[m])}).dropna()
    wide = t.pivot_table(index=["e", "y"], columns="d", values="v", aggfunc="first").dropna()
    if wide.shape[1] < 3 or len(wide) < 5:
        return None
    tot = wide.sum(axis=1)
    for lv in wide.columns:
        others = tot - wide[lv]
        if float(((wide[lv] - others).abs() <= TOTAL_TOL * others.abs().clip(lower=1)).mean()) >= TOTAL_SHARE:
            return str(lv)
    lv = [str(x) for x in wide.columns if re.search(r"\b(all|total)\b", str(x), re.I)]
    return lv[0] if lv else None


def _panel_total(f: Any, ent: str, year: str, m: str, members: List[str]) -> Optional[str]:
    import pandas as pd
    t = pd.DataFrame({"e": f[ent].astype(str), "y": f[year].astype(str), "v": _is_num(f[m])}).dropna()
    wide = t.pivot_table(index="y", columns="e", values="v", aggfunc="first")
    if wide.shape[1] < 3:
        return None
    best, share = None, 0.0
    for c in wide.columns:
        others = wide.drop(columns=[c]).sum(axis=1, min_count=2)
        ok = ((wide[c] - others).abs() <= TOTAL_TOL * others.abs().clip(lower=1)) & wide[c].notna()
        s = float(ok.sum()) / max(1, int(wide[c].notna().sum()))
        if s > share:
            best, share = c, s
    return str(best) if share >= TOTAL_SHARE else None


def _panel_key(p: Dict[str, Any], primary: Optional[str]) -> str:
    if primary in p["measures"]:
        return primary
    t = {"frame": p["frame"], "header_of": p["header_of"], "n": p["n"]}

    def sc(c: str) -> float:
        h = str(p["header_of"].get(c, c)).lower()
        bonus = 6.0 if re.sub(r"[^a-z0-9]", "", h) in p["hint"] else 0.0       # owid-co2-panel.csv: the column named co2
        filled = float(_is_num(p["frame"][c]).notna().mean())
        return _measure_score(t, c, None) + bonus + filled - (2.0 if re.search(r"(share|per|_pct|cumulative|change)", h) else 0.0)
    return max(p["measures"], key=sc)


def _series(p: Dict[str, Any], m: str) -> Any:
    import pandas as pd
    f = p["frame"]
    t = pd.DataFrame({"e": f[p["entity"]].astype(str), "y": _is_num(f[p["year"]]).astype("Int64"), "v": _is_num(f[m])}).dropna()
    return t.pivot_table(index="y", columns="e", values="v", aggfunc="first").sort_index()


def build_panel(p: Dict[str, Any], primary: Optional[str] = None, limit: int = 8) -> Dict[str, Any]:
    import math as _m
    key = _panel_key(p, primary)
    mh = _human(p["header_of"].get(key, key))
    eh = humane(p["header_of"].get(p["entity"], p["entity"]))
    wide = _series(p, key)
    wide = wide.loc[wide.notna().sum(axis=1) > 0]
    years = [int(y) for y in wide.index]
    recs, sents = [], []
    members = [c for c in wide.columns if c not in p["aggregates"]]
    last = None
    for y in reversed(years):                                  # the latest year most members report
        if wide.loc[y, members].notna().sum() >= 0.5 * max(1, wide[members].notna().sum(axis=1).max()):
            last = y
            break
    show_years = [y for y in years if last is None or y <= last][-LINES_MAX_YEARS:]
    # 1. the whole (or the leaders) over time
    lines = []
    if p["total"] and p["total"] in wide.columns:
        lines.append(p["total"])
    elif len(members) >= 10:
        # no row is the whole (FRED's metro areas, no national line): the median member each year is the reference line, a figure of
        # the file itself, never a total that is not there (18 Oct 2026)
        rep_n = int(wide[members].notna().sum(axis=1).max())
        med_name = "Median of %s %s" % (format(rep_n, ","), _plural_of(eh))
        wide = wide.assign(**{med_name: wide[members].median(axis=1, skipna=True).where(
            wide[members].notna().sum(axis=1) >= 0.5 * rep_n)})
        p["median_line"] = med_name
        lines.append(med_name)
    if last is not None:
        lead = wide.loc[last, members].dropna().sort_values(ascending=False)
        lines += [m for m in lead.index[:PANEL_LINES - len(lines)]]
    if lines and len(show_years) >= 2:
        series = [{"label": str(m)[:80], "values": [None if _m.isnan(float(wide.loc[y, m])) else float(wide.loc[y, m])
                                                     for y in show_years]} for m in lines]
        tab = [[str(y)] + [_nfmt(float(wide.loc[y, m])) if not _m.isnan(float(wide.loc[y, m])) else "" for m in lines] for y in show_years]
        ref = lines[0]
        # the sentence spans every year the line's first member reports (the chart keeps its last LINES_MAX_YEARS)
        have = [y for y in years if (last is None or y <= last) and not _m.isnan(float(wide.loc[y, ref]))]
        a, b = (have[0], have[-1]) if len(have) >= 2 else (show_years[0], show_years[-1])
        va, vb = wide.loc[a, ref], wide.loc[b, ref]
        if not (_m.isnan(float(va)) or _m.isnan(float(vb))) and float(va) != 0:
            who = ("%s %s" % (ref, mh[:1].lower() + mh[1:])) if ref == p["total"] else \
                ("The median %s's %s" % (eh, mh[:1].lower() + mh[1:])) if ref == p.get("median_line") else ("%s for %s" % (mh, ref))
            ratio = float(vb) / float(va)
            how = ("%+.0f%%" % (100.0 * (ratio - 1.0))) if -1.0 < ratio - 1.0 <= 9.99 or float(va) < 0 else \
                "%s times as much" % format(round(ratio), ",")                 # 9.31 to 38,599 is "4,146 times as much", never +414671%
            if p.get("rate"):
                how = "%+.1f percentage points" % (float(vb) - float(va))      # a rate's change, never a percent of a percent
            sent = "%s went from %s in %d to %s in %d (%s)." % (who, _nfmt(float(va)), a, _nfmt(float(vb)), b, how)
        else:
            sent = "%s over %d to %d, for %s." % (mh, a, b, ", ".join(lines))
        recs.append(_record("line_chart", "%s, %d to %d" % (mh, show_years[0], show_years[-1]),
                            ("%s and the %d highest %s in %d" % (lines[0], len(lines) - 1, _plural_of(eh), last)) if (p["total"] in lines or p.get("median_line") in lines)
                            else "The %d highest %s in %d" % (len(lines), _plural_of(eh), last),
                            {"label": mh, "unit": "", "kind": "amount"},
                            {"x": [str(y) for y in show_years], "grain": "year", "x_label": "Year", "series": series, "forecast": None},
                            {"cols": ["Year"] + [str(m)[:40] for m in lines], "rows": tab}, sent,
                            {"columns": [p["header_of"].get(c, c) for c in (p["year"], p["entity"], key)], "rows": int(p["n"]),
                             "months": None, "op": "each year's value as the file states it"}, "The rows of the file, by year"))
        sents.append(sent)
    # 2. who leads now: the latest year's ranking, groups and totals left out
    if last is not None:
        s = wide.loc[last, members].dropna().sort_values(ascending=False)
        if len(s) >= 3:
            cut = len(s) > 2 * RANK_SHOW
            show = list(s.index[:RANK_SHOW]) + (list(s.index[-RANK_SHOW:]) if cut else list(s.index[RANK_SHOW:]))
            rows = [{"label": str(m)[:80], "values": [float(s[m])], "texts": [_nfmt(float(s[m]))]} for m in show]
            sent = "In %d, %s was highest for %s (%s) and lowest for %s (%s) of %s %s%s." % (
                last, mh.lower(), s.index[0], _nfmt(float(s.iloc[0])), s.index[-1], _nfmt(float(s.iloc[-1])), format(len(s), ","), _plural_of(eh),
                (" (groups such as %s are left out of the ranking)" % _join(sorted(p["aggregates"])[:3])) if p["aggregates"] else "")
            recs.append(_record("bar_chart", "%s by %s, %d" % (mh, eh, last),
                                ("The %d highest and the %d lowest of %s %s in %d" % (RANK_SHOW, RANK_SHOW, format(len(s), ","), _plural_of(eh), last))
                                if cut else "Every %s in %d, highest first" % (eh, last),
                                {"label": mh, "unit": "", "kind": "amount"},
                                {"series": [mh[:40]], "rows": rows, "other": False},
                                {"cols": [eh.capitalize(), mh], "rows": [[r["label"], r["texts"][0]] for r in rows]}, sent,
                                {"columns": [p["header_of"].get(p["entity"]), p["header_of"].get(key)], "rows": int(len(s)), "months": None,
                                 "op": "the latest year's value per member, groups left out"}, "The rows of %d" % last))
            sents.append(sent)
    # 3. who changed most: the first year every leader reports to the latest
    if last is not None and len(show_years) >= 2:
        a = show_years[0]
        both = wide.loc[[a, last], members].T.dropna()
        both = both[both[a] != 0] if not p.get("rate") else both
        if len(both) >= 3:
            rate = bool(p.get("rate"))
            ch = ((both[last] - both[a]) if rate else ((both[last] - both[a]) / both[a].abs() * 100.0)).sort_values(ascending=False)
            unit_t = (lambda x: "%+.1f pp" % x) if rate else (lambda x: "%+.0f%%" % x)
            pick = list(ch.index[:6]) + [m for m in ch.index[-4:] if m not in ch.index[:6]]
            rows = [{"label": str(m)[:80], "values": [float(ch[m])], "texts": [unit_t(float(ch[m]))]} for m in pick]
            hi_c, lo_c = float(ch.iloc[0]), float(ch.iloc[-1])
            if hi_c < 0:
                how = "fell for every %s: least for %s (%s) and most for %s (%s)" % (eh, ch.index[0], unit_t(hi_c), ch.index[-1], unit_t(lo_c))
            elif lo_c > 0:
                how = "rose for every %s: most for %s (%s) and least for %s (%s)" % (eh, ch.index[0], unit_t(hi_c), ch.index[-1], unit_t(lo_c))
            else:
                how = "rose most for %s (%s) and fell most for %s (%s)" % (ch.index[0], unit_t(hi_c), ch.index[-1], unit_t(lo_c))
            sent = "From %d to %d, %s %s." % (a, last, mh.lower(), how)
            recs.append(_record("bar_chart", "Change in %s, %d to %d" % (mh.lower(), a, last),
                                "The largest and the smallest changes of the %s %s with a value in both years" % (format(len(ch), ","), _plural_of(eh)),
                                {"label": "change", "unit": "pp" if rate else "%", "kind": "amount" if rate else "change_pct"},
                                {"series": ["Change"], "rows": rows, "other": False},
                                {"cols": [eh.capitalize(), "Change"], "rows": [[r["label"], r["texts"][0]] for r in rows]}, sent,
                                {"columns": [p["header_of"].get(key)], "rows": int(len(ch)), "months": None,
                                 "op": "latest - first, in percentage points" if rate else "(latest - first) / first, in %"},
                                "The rows of %d and %d" % (a, last)))
            sents.append(sent)
    # 4. the measures' links across members in the latest year
    if last is not None and len(p["measures"]) >= 3:
        f = p["frame"]
        latest = f[_is_num(f[p["year"]]) == last]
        latest = latest[~latest[p["entity"]].astype(str).isin(p["aggregates"])]
        ms = [key] + [m for m in sorted(p["measures"], key=lambda c: -float(_is_num(latest[c]).notna().mean())) if m != key][:7]
        t = {"frame": latest, "header_of": p["header_of"], "n": len(latest)}
        got = _correlation(t, ms, key) if len(latest) >= 5 else None
        if got is not None:
            recs.append(got[0])
            sents.append(got[1].replace("rows", "%s in %d" % (_plural_of(eh), last), 1))
    # 5. where each member is (US metro areas, states): a map, one year at a time on one legend, leading the charts
    try:
        import nl_geo
        span_years = [y for y in years if last is None or y <= last]
        mvals = {m: [None if _m.isnan(float(wide.loc[y, m])) else float(wide.loc[y, m]) for y in span_years] for m in members}
        geo = nl_geo.map_record(members, p.get("codes"), [str(y) for y in span_years], mvals, mh, "%" if "%" in mh else "",
                                bool(p.get("rate")), eh, fmt=_nfmt)
        if geo is not None:
            recs.insert(0, geo)
    except Exception:  # noqa: BLE001 - the panel stands without its map
        pass
    about = ("Each row is one %s in one year%s: %s rows, %d to %d, %d measure%s%s. A %s that is the total or a group of others (%s) is "
             "read as the line the others are compared with, never ranked beside them." % (
                 eh, (" and one %s (the rows of its total, %s, are used)" % (_human(p["header_of"].get(p["dim"])).lower(), p["dim_level"]))
                 if p["dim"] else "", format(p["n"], ","), years[0], years[-1], len(p["measures"]), "" if len(p["measures"]) == 1 else "s",
                 "" if not last else "; the latest year most of them report is %d" % last, eh,
                 _join(sorted(p["aggregates"])[:4]) or "none here"))
    return {"version": VERSION, "kind": "panel", "records": recs[:limit], "sentences": sents, "headline": sents[0] if sents else about,
            "about": about, "entity": eh, "indicator": "measure", "entity_column": p["header_of"].get(p["entity"]),
            "indicator_column": None, "total_member": p["total"], "measures": [p["header_of"].get(key)], "chosen_by": "ai" if primary else "engine"}


# ============================================================================================ a survey (one row per respondent)
# Answers as words (Very favorably, Yes, 18-29), a question spread over several columns (one per film, one per character), a rank
# per option. The analyst's first moves: what share chose each answer, in the scale's own order (never alphabetical); which option
# ranks best on average (a rank: lower is better); who answered (the demographic bands, in their order).

_POS = re.compile(r"(favorabl|agree|satisf|likely|good|excellent|great|positive|approve|support|happy|useful|important|yes\b|always|"
                  r"often|love|like\b)", re.I)
_NEG = re.compile(r"(unfavorabl|disagree|dissatisf|unlikely|poor|bad|terrible|negative|disapprove|oppose|unhappy|useless|"
                  r"unimportant|\bno\b|never|rarely|hate|dislike)", re.I)
_NEU = re.compile(r"(neither|neutral|unsure|not sure|don.?t know|undecided|indifferent|sometimes|mixed)", re.I)
_STRONG_W = re.compile(r"\b(very|strongly|extremely|completely|totally|highly)\b", re.I)
_WEAK_W = re.compile(r"\b(somewhat|slightly|mostly|fairly|rather|moderately)\b", re.I)
SURVEY_MIN_ANSWERS = 30
ANSWERS_MAX = 12


def answer_order(levels: Sequence[str]) -> Optional[List[str]]:
    """The answers of a scale in its own order, most positive first, or None when they are not a scale (fewer than two read as
    positive or negative). An answer the lexicon cannot place (Unfamiliar) goes last."""
    scored, rest = [], []
    for lv in levels:
        s = str(lv)
        neg = bool(_NEG.search(s))
        pos = bool(_POS.search(s)) and not neg
        k = 2 if _STRONG_W.search(s) else 1
        if pos:
            scored.append((k, s))
        elif neg:
            scored.append((-k, s))
        elif _NEU.search(s):
            scored.append((0, s))
        else:
            rest.append(s)
    if sum(1 for v, _s in scored if v != 0) < 2:
        return None
    return [s for _v, s in sorted(scored, key=lambda x: -x[0])] + sorted(rest)


def _band_key(s: str) -> Tuple[float, str]:
    """An ordered band ("18-29", "$25,000 - $49,999", "> 60") by its first number; words after numbers."""
    m = re.search(r"\d[\d,]*(\.\d+)?", str(s))
    return (float(m.group(0).replace(",", "")) if m else float("inf"), str(s))


def _question(header: str) -> Tuple[str, str]:
    """(the question, the option) of a merged header "Question: option"; the option is '' for a one-column question."""
    h = str(header)
    if " | " in h:
        q, _s, o = h.partition(" | ")
        return q.strip(), o.strip()
    return h.strip(), ""


def detect_survey(frame: Any, header_of: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """A survey: at least SURVEY_MIN_ANSWERS rows and three or more columns of a few word answers (2 to ANSWERS_MAX), or a group of
    rank columns; None otherwise."""
    if frame is None or len(frame) < SURVEY_MIN_ANSWERS:
        return None
    answers, ranks, ticks = [], [], []
    for c in frame.columns:
        if str(c).lower() == "source_line":
            continue
        s = frame[c].dropna().astype(str).str.strip()
        s = s[s != ""]
        if len(s) < 0.2 * len(frame):
            continue
        d = int(s.nunique())
        num = _is_num(s)
        if num.notna().mean() >= 0.95 and 3 <= d <= 12 and (num == num.round()).all() and num.min() >= 1 and num.max() <= d:
            ranks.append(c)                                   # 1..k with every value a whole step: a rank or a rating
        elif num.notna().mean() < 0.5 and 2 <= d <= ANSWERS_MAX:
            answers.append(c)
        elif num.notna().mean() < 0.5 and d == 1 and " | " in str(header_of.get(c, c)):
            ticks.append(c)                                   # "tick all that apply": the option's own name, or blank
    if len(answers) + len(ranks) + len(ticks) < 3:
        return None
    # a survey, not a table with a few categories (penguins: species, island, sex): questions over several options (a merged
    # "question | option" header), or two or more questions answered on a scale (agree ... disagree, favorably ... unfavorably)
    merged = sum(1 for c in frame.columns if " | " in str(header_of.get(c, c)))
    scaled = sum(1 for c in answers if answer_order(list(frame[c].dropna().astype(str).str.strip().unique())) is not None)
    if merged < 2 and scaled < 2:
        return None
    groups: Dict[str, List[str]] = {}
    for c in answers + ranks + ticks:
        q, _o = _question(header_of.get(c, c))
        groups.setdefault(q, []).append(c)
    return {"kind": "survey", "answers": answers, "ranks": ranks, "ticks": ticks, "groups": groups, "frame": frame, "header_of": header_of,
            "n": len(frame)}


def _rank_record(sv: Dict[str, Any], q: str, cols: List[str]) -> Optional[Tuple[Dict[str, Any], str]]:
    f = sv["frame"]
    rows = []
    for c in cols:
        v = _is_num(f[c]).dropna()
        if len(v) < 5:
            return None
        _q, o = _question(sv["header_of"].get(c, c))
        lo, hi = _boot(v.to_numpy(dtype=float))
        rows.append({"label": (o or str(c))[:80], "n": int(len(v)), "center": float(v.mean()), "lo": min(lo, float(v.mean())),
                     "hi": max(hi, float(v.mean())), "median": float(v.median()),
                     "texts": {"n": format(len(v), ","), "center": "%.2f" % v.mean(), "lo": "%.2f" % min(lo, float(v.mean())),
                               "hi": "%.2f" % max(hi, float(v.mean())), "median": "%.0f" % v.median()}})
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["center"])
    a, b = rows[0], rows[-1]
    sent = "%s: %s ranks best on average (%s, where 1 is best) and %s worst (%s), over %s answers." % (
        _short(q, 90), a["label"], a["texts"]["center"], b["label"], b["texts"]["center"], a["texts"]["n"])
    rec = _record("group_ranges", _short(q, 100), "Average rank of each option, best (lowest) first, with its 95% bootstrap range",
                  {"label": "average rank", "unit": "", "kind": "average"}, {"rows": rows[:12]},
                  {"cols": ["Option", "Answers", "Average rank", "95% range", "Median"],
                   "rows": [[r["label"], r["texts"]["n"], r["texts"]["center"], "%s to %s" % (r["texts"]["lo"], r["texts"]["hi"]),
                             r["texts"]["median"]] for r in rows[:12]]}, sent,
                  {"columns": [sv["header_of"].get(c, c) for c in cols][:12], "rows": int(sv["n"]), "months": None,
                   "op": "mean rank per option; lower is better"}, "The answers in the file")
    return rec, sent


def _scale_record(sv: Dict[str, Any], q: str, cols: List[str]) -> Optional[Tuple[Dict[str, Any], str]]:
    """Several options answered on one scale (each character's favourability): a heatmap of the share of each answer, in the
    scale's order, options by the share of the most positive answers."""
    import nl_viz as _nv
    f = sv["frame"]
    levels: List[str] = []
    for c in cols:
        for v in f[c].dropna().astype(str).str.strip().unique():
            if v and v not in levels:
                levels.append(v)
    order = answer_order(levels)
    if order is None or len(order) > 8:
        return None
    pos = [lv for lv in order if _POS.search(lv) and not _NEG.search(lv)]
    rows_lab, cells, share_pos = [], [], {}
    for c in cols:
        s = f[c].dropna().astype(str).str.strip()
        s = s[s != ""]
        if len(s) < 5:
            continue
        _q, o = _question(sv["header_of"].get(c, c))
        lab = (o or str(c))[:40]
        vc = s.value_counts()
        row = []
        for lv in order:
            k = int(vc.get(lv, 0))
            p = 100.0 * k / len(s)
            row.append(_nv._cell(p, len(s), "%.0f%%" % p))
        rows_lab.append(lab)
        cells.append(row)
        share_pos[lab] = sum(100.0 * int(vc.get(lv, 0)) / len(s) for lv in pos)
    if len(rows_lab) < 2:
        return None
    idx = sorted(range(len(rows_lab)), key=lambda i: -share_pos[rows_lab[i]])[:12]
    rows_lab = [rows_lab[i] for i in idx]
    cells = [cells[i] for i in idx]
    cols_lab = [lv[:24] for lv in order]
    data, _supp = _nv._grid(rows_lab, cols_lab, cells, "sequential", lambda v: "%.0f%%" % v, "option", "answer")
    best, worst = rows_lab[0], rows_lab[-1]
    sent = "%s: %s gets the most positive answers (%.0f%% %s) and %s the fewest (%.0f%%)." % (
        _short(q, 90), best, share_pos[best], " or ".join(x.lower() for x in pos[:2]), worst, share_pos[worst])
    rec = _record("crosstab_heatmap", _short(q, 100), "Share of each answer for each option, the scale in its own order",
                  {"label": "share of answers", "unit": "%", "kind": "share_pct"}, data,
                  _nv._grid_table("option", rows_lab, cols_lab, data["text"]), sent,
                  {"columns": [sv["header_of"].get(c, c) for c in cols][:12], "rows": int(sv["n"]), "months": None,
                   "op": "share of each answer per option"}, "The answers in the file")
    return rec, sent


def _tick_record(sv: Dict[str, Any], q: str, cols: List[str]) -> Optional[Tuple[Dict[str, Any], str]]:
    """A "tick all that apply" question: the share of respondents who ticked each option, the most first."""
    f = sv["frame"]
    n = int(sv["n"])
    rows = []
    for c in cols:
        k = int((f[c].fillna("").astype(str).str.strip() != "").sum())
        _q, o = _question(sv["header_of"].get(c, c))
        rows.append({"label": (o or str(c))[:80], "values": [100.0 * k / n], "texts": ["%.0f%%" % (100.0 * k / n)]})
    rows.sort(key=lambda r: -r["values"][0])
    if len(rows) < 2:
        return None
    sent = "%s: %s was ticked most (%s of %s respondents) and %s least (%s)." % (
        _short(q, 90), rows[0]["label"], rows[0]["texts"][0], format(n, ","), rows[-1]["label"], rows[-1]["texts"][0])
    rec = _record("bar_chart", _short(q, 100), "Share of all %s respondents who ticked each option" % format(n, ","),
                  {"label": "share", "unit": "%", "kind": "share_pct"}, {"series": ["Ticked"], "rows": rows[:20], "other": False},
                  {"cols": ["Option", "Ticked"], "rows": [[r["label"], r["texts"][0]] for r in rows[:20]]}, sent,
                  {"columns": [sv["header_of"].get(c, c) for c in cols][:12], "rows": n, "months": None, "op": "share ticked"},
                  "The answers in the file")
    return rec, sent


def _answer_record(sv: Dict[str, Any], c: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """One question's answers, in the scale's order or the bands' order, as shares of those who answered."""
    s = sv["frame"][c].dropna().astype(str).str.strip()
    s = s[s != ""]
    if len(s) < 5:
        return None
    vc = s.value_counts()
    order = answer_order(list(vc.index)) or (sorted(vc.index, key=_band_key) if sum(1 for x in vc.index if re.search(r"\d", x)) >=
                                             0.6 * len(vc) else list(vc.index))
    q, _o = _question(sv["header_of"].get(c, c))
    rows = [{"label": str(lv)[:80], "values": [100.0 * vc.get(lv, 0) / len(s)], "texts": ["%.0f%%" % (100.0 * vc.get(lv, 0) / len(s))]}
            for lv in order[:20]]
    top = vc.index[0]
    sent = "%s: %s (%.0f%% of %s answers)." % (_short(q, 100), top, 100.0 * vc.iloc[0] / len(s), format(len(s), ","))
    rec = _record("bar_chart", _short(q, 100), "Share of the %s who answered, %s" % (
        format(len(s), ","), "in the scale's order" if answer_order(list(vc.index)) else "in order"),
        {"label": "share", "unit": "%", "kind": "share_pct"}, {"series": ["Share"], "rows": rows, "other": False, "order": "given"},
        {"cols": ["Answer", "Share"], "rows": [[r["label"], r["texts"][0]] for r in rows]}, sent,
        {"columns": [sv["header_of"].get(c, c)], "rows": int(len(s)), "months": None, "op": "share of each answer"}, "The answers in the file")
    return rec, sent


def build_survey(sv: Dict[str, Any], limit: int = 8) -> Dict[str, Any]:
    recs: List[Tuple[Dict[str, Any], str]] = []
    multi = [(q, cs) for q, cs in sv["groups"].items() if len(cs) >= 2]
    single = [cs[0] for q, cs in sv["groups"].items() if len(cs) == 1]
    for q, cs in multi:
        if len(recs) >= limit:
            break
        got = _rank_record(sv, q, cs) if all(c in sv["ranks"] for c in cs) else             _tick_record(sv, q, cs) if all(c in sv["ticks"] for c in cs) else _scale_record(sv, q, cs)
        if got is not None:
            recs.append(got)
    # the single questions: the ones on a scale or yes/no first, then the bands (who answered)
    single.sort(key=lambda c: (answer_order(list(sv["frame"][c].dropna().astype(str).unique())) is None, list(sv["frame"].columns).index(c)))
    for c in single:
        if len(recs) >= limit:
            break
        got = _answer_record(sv, c)
        if got is not None:
            recs.append(got)
    about = ("Each row is one respondent: %s answers to %d questions (%d asked about several options at once). Answers on a scale "
             "are read in the scale's own order; a rank is read as 1 the best." % (format(sv["n"], ","), len(sv["groups"]), len(multi)))
    sents = [s for _r, s in recs]
    return {"version": VERSION, "kind": "survey", "records": [r for r, _s in recs], "sentences": sents,
            "headline": sents[0] if sents else about, "about": about, "entity": "respondent", "indicator": "question",
            "entity_column": None, "indicator_column": None, "total_member": None, "measures": [], "chosen_by": "engine"}


# ============================================================================================ Ask (step 4) on an indicator table
# A question on a Census Profile ("Which province has the highest average total income?") names a MEASURE and perhaps a place. The
# engine's own Ask added the value column across all 1,624 measures and read CENSUS_YEAR as a date. Here the question is matched to
# the measure it names, and one read-only query lists that measure for each place (or the places it names), largest first.

_STOP = frozenset("""the a an of in on for and or to by with what which who whose how much many is are was were be been does do did
the highest lowest largest smallest most least top bottom show me list give tell compare between than per each every all any
province provinces territory territories place places country countries region regions state states city cities value values
number figure data table file rate there their this that these those it its from as at about""".split())


def ask_index(lay: Dict[str, Any]) -> Dict[str, Any]:
    """What Ask needs of an indicator table, by the file's own headers (the Ask table, kept_rows, is named by them)."""
    h = lambda c: str(lay["header_of"].get(c, c)) if c else None          # noqa: E731
    fam = lay["families"][lay["primary"]]
    rate = next((s for f, s in lay["families"].items() if f != lay["primary"] and re.search(r"rate|pct|percent|share", f)), None)
    items = []
    topics: Dict[str, int] = {}
    for k in dict.fromkeys(lay["frame"][lay["indicator"]].astype(str).tolist()):
        iv = _indicator_values(lay, k)
        if iv is None:
            continue
        r = topics.setdefault(_yearless(iv["topic"]), len(topics))
        items.append({"key": k, "label": iv["label"], "topic": iv["topic"], "kind": iv["kind"], "family": iv["family"], "trank": r})
    for it in items:
        it["trank"] = it["trank"] / float(max(1, len(topics)))
    members = [str(m) for m in lay["frame"][lay["entity"]].dropna().astype(str).unique()]
    return {"kind": "indicator_table", "entity": h(lay["entity"]), "indicator": h(lay["indicator"]), "label": h(lay["label"]),
            "value": h(fam["total"]), "rate": h(rate["total"]) if rate else None,
            "groups": {g: h(c) for g, c in fam["groups"].items()},
            "rate_groups": {g: h(c) for g, c in (rate or {}).get("groups", {}).items()},
            "numeric_key": lay["indicator"] != lay["label"], "items": items, "members": members, "total": lay.get("total_member")}


def _words(s: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9$%]+", str(s).lower()) if len(w) >= 3 and w not in _STOP]


def ask_steps(ix: Dict[str, Any], question: str) -> List[Dict[str, Any]]:
    """The read-only `sql` step that answers a question on an indicator table, or one that lists the measures closest to it."""
    q = str(question or "")
    ql = q.lower()
    named = [m for m in ix["members"] if m.lower() in ql]
    qw = set(_words(q)) - set(w for m in named for w in _words(m))
    best, best_s = None, 0.0
    for it in ix["items"]:
        lw = _words(it["label"])
        if not lw:
            continue
        hit = sum(1 for w in set(lw) if w in qw or w.rstrip("s") in qw or (w + "s") in qw)
        if not hit:
            continue
        # the most question words first; then the measure that is about little else, the file's main topics, and never a "Total -"
        # line (100% everywhere)
        s = hit + 0.3 * hit / float(len(set(lw))) + 1.0 * (1.0 - it.get("trank", 0.0)) + \
            0.2 * sum(1 for w in set(_words(it["topic"])) if w in qw)
        if " ".join(lw) in " ".join(_words(q)):
            s += 1.0
        if re.match(r"^\s*total\s*-", it["label"], re.I):
            s -= 2.0
        if best is None or s > best_s + 1e-9:
            best, best_s = it, s
    qi = lambda c: '"%s"' % str(c).replace('"', '""')                     # noqa: E731
    lit = lambda v: "'%s'" % str(v).replace("'", "''")                     # noqa: E731
    if best is None:
        like = " OR ".join("%s LIKE %s" % (qi(ix["label"]), lit("%" + w + "%")) for w in sorted(qw)[:4]) or "1"
        return [{"op": "sql", "args": {"sql": "SELECT DISTINCT %s FROM kept_rows WHERE %s LIMIT 40" % (qi(ix["label"]), like)},
                 "why": "no measure of the table matches the question's words: the measures closest to them, to choose from"}]
    use_rate = best["kind"] == "pct" and ix.get("rate")
    col = ix["rate"] if use_rate else ix["value"]
    groups = ix["rate_groups"] if use_rate else ix["groups"]
    want_g = [g for g in groups if re.search(r"\b(men|man|male|women|woman|female|gender|sex|gap)\b", ql)]
    sel = ["%s AS place" % qi(ix["entity"]), "%s AS value" % qi(col)] + ["%s AS %s" % (qi(groups[g]), qi(g)) for g in want_g]
    key_col = ix["indicator"] if ix["numeric_key"] else ix["label"]
    key_val = best["key"]
    try:
        key_sql = str(int(float(key_val))) if ix["numeric_key"] else lit(key_val)
    except ValueError:
        key_sql = lit(key_val)
    where = ["%s = %s" % (qi(key_col), key_sql)]
    if named:
        where.append("%s IN (%s)" % (qi(ix["entity"]), ", ".join(lit(m) for m in named + ([ix["total"]] if ix.get("total") and
                                                                                             ix["total"] not in named else []))))
    sql = "SELECT %s FROM kept_rows WHERE %s ORDER BY value DESC" % (", ".join(sel), " AND ".join(where))
    ix["last_ask"] = {"label": best["label"], "kind": best["kind"], "named": named}
    return [{"op": "sql", "args": {"sql": sql},
             "why": "the measure the question names (%s), for %s, largest first" % (
                 _short(best["label"], 120), "the places it names" if named else "every place")}]



# ============================================================================================ the answer to a question (step 4)
def _num_or_none(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def answer_of(question: str, steps: List[Dict[str, Any]], ix: Optional[Dict[str, Any]] = None,
              rate_cols: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """The direct answer a reader looks for first: one sentence written from the first query that ran (its result table), the
    table itself (at most 20 rows) and the query. None when no query ran. Every figure is a cell of that result."""
    st = next((s for s in steps or [] if isinstance(s, dict) and s.get("op") == "sql" and s.get("ok")), None)
    if st is None:
        return None
    tb = st.get("table") or {}
    cols = [str(c) for c in tb.get("columns") or []]
    rows = [list(r) for r in tb.get("rows") or [] if isinstance(r, (list, tuple))]
    last = (ix or {}).get("last_ask") or {}
    kind = str(last.get("kind") or "")
    def fmt(v: Any) -> str:
        f = _num_or_none(v)
        if f is None:
            return str(v)
        return _fmt_num(f, kind) if kind in ("pct", "money") else _nfmt(f)
    # a column of dates is a period: shown at its own grain (2010-01-01 ... every 1 January is 2010; every 1st of a month is 2010-03)
    date_c = set()
    for i in range(len(cols)):
        vals = [str(r[i]) for r in rows if i < len(r) and r[i] is not None]
        if vals and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]00:00:00)?", v) for v in vals):
            date_c.add(i)
            cut = 4 if all(v[5:10] == "01-01" for v in vals) else 7 if all(v[8:10] == "01" for v in vals) else 10
            for r in rows:
                if i < len(r) and r[i] is not None:
                    r[i] = str(r[i])[:cut]
    # a period by its name (period, yr, year, month_label ...), or a column of dates that comes first (the row's own date); a date
    # column further right is one more fact about the row (last_month_below_now), shown at its grain but never the row's period
    name_per = {i for i, c in enumerate(cols) if re.fullmatch(r"(period|yr|year|years|quarter|month|date|time_period|obs_date|observation_date|"
                                                            r"year_month|month_label|year_label|fiscal_year|d|dt)", c.strip().lower())}
    per_c = name_per | ({0} & date_c)
    shown = [[fmt(c) if _num_or_none(c) is not None and i not in per_c else str(c) for i, c in enumerate(r)] for r in rows[:20]]
    # the column headers in words: the measure's name for "value", the table's word for "place"
    if last.get("label"):
        cols = [_short(str(last["label"]), 50) if c == "value" else c for c in cols]
    # a bar per row for a single measure (its share of the largest), when every value is 0 or more
    bars = None
    if len(cols) >= 2 and rows:
        v1 = [_num_or_none(r[1]) if len(r) > 1 else None for r in rows[:20]]
        if all(x is not None and x >= 0 for x in v1) and max(v1) > 0:
            bars = [round(x / max(v1), 4) for x in v1]
    if not rows:
        text = "No rows match this question in the file."
    else:
        # a period column (1960, 2025-Q3) is a label, never a figure: never "1,960"
        per_i = set(per_c)
        num_i = [i for i in range(len(cols)) if i not in per_i and
                 all(_num_or_none(r[i]) is not None for r in rows if i < len(r) and r[i] is not None)]
        lab_i = [i for i in range(len(cols)) if i not in num_i]
        subject = last.get("label") or (cols[num_i[0]] if num_i else "")
        pinned = re.findall(r"\"[^\"]+\"\s*=\s*'((?:[^']|'')+)'", str((st.get("args") or {}).get("sql") or ""))
        if pinned and subject:
            subject = "%s (%s)" % (subject, ", ".join(x.replace("''", "'") for x in pinned[:2]))      # Population (China)
        sql_t = str((st.get("args") or {}).get("sql") or "")
        # the outer query's ORDER BY (quoted or bare): the column the visitor's answer is ordered by, and which way
        obs = re.findall(r'ORDER\s+BY\s+(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_]*))\s*(ASC|DESC)?', sql_t, re.I)
        o_col, o_dir = None, ""
        if obs:
            q_, b_, d_ = obs[-1]
            o_name = (q_ or b_).replace('""', '"')
            o_col = next((i for i, c in enumerate(cols) if c.lower() == o_name.lower()), None)
            if o_col is None:
                # ordered by the source column the query shows under another name ("X" AS inflation)
                for src_q, src_b, al_q, al_b in re.findall(
                        r'(?:"((?:[^"]|"")+)"|\b([A-Za-z_][A-Za-z0-9_]*))\s+AS\s+(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_]*))', sql_t, re.I):
                    if (src_q or src_b).replace('""', '"').lower() == o_name.lower():
                        o_col = next((i for i, c in enumerate(cols) if c.lower() == (al_q or al_b).replace('""', '"').lower()), None)
                        if o_col is not None:
                            break
            o_dir = (d_ or "ASC").upper()
        ranked = lab_i and num_i and not (set(lab_i) & per_i) and (
            bool(re.search(r"\bLIMIT\s+\d+\s*$", sql_t, re.I)) or (o_col is not None and o_col in num_i))
        figs = [i for i in num_i if cols[i] != "row_count" and not cols[i].lower().startswith("average_")] or num_i
        n_i = cols.index("row_count") if "row_count" in cols else None
        kv = lab_i and num_i and re.fullmatch(r"(metric|measure|statistic|stat|item|key|label|name|indicator|what)",
                                              cols[lab_i[0]].strip().lower())
        if kv:
            # a table of named figures (metric, value): each named in words, the first five
            li, vi = lab_i[0], (o_col if o_col in num_i else num_i[0])
            parts_kv = ["%s %s" % (str(r[li]).replace("_", " "), fmt(r[vi])) for r in rows[:5] if _num_or_none(r[vi]) is not None]
            text = ("; ".join(parts_kv) + ".") if parts_kv else "%s rows; see the table." % format(len(rows), ",")
            text = text[:1].upper() + text[1:]
        elif len(rows) == 1 and per_i and figs:
            # one period, one figure: "Population in 1990: 5.28B (the total of its 217 rows)."
            r0 = rows[0]
            pi = sorted(per_i)[0]
            n0 = _num_or_none(r0[n_i]) if n_i is not None else None
            text = "%s %s: %s%s." % (("In" if str(subject) in cols and len(figs) > 1 else _short(str(subject), 120) + " in"), r0[pi], "; ".join(
                ("%s %s" % (cols[i].replace("_", " "), fmt(r0[i])) if len(figs) > 1 else fmt(r0[i])) for i in figs if r0[i] is not None),
                (" (the %s of its %s rows)" % ("average" if re.search(r"(?i)\bAVG\(", sql_t) or "average" in cols[figs[0]].lower() else "total",
                                              format(int(n0), ","))) if n0 and n0 > 1 else "")
        elif len(rows) == 1 and str(subject) in cols:
            # one row of named figures: each in words ("latest reading 2.78; avg last 12m 2.92"), never "latest_reading: latest_reading"
            text = "; ".join("%s %s" % (cols[i].replace("_", " "), fmt(rows[0][i]) if i not in per_c else rows[0][i])
                             for i in range(len(cols)) if rows[0][i] is not None and cols[i] != "row_count") + "."
            text = text[:1].upper() + text[1:]
        elif len(rows) == 1:
            text = "%s: %s." % (_short(str(subject), 120) if subject else "The answer",
                                "; ".join("%s %s" % (cols[i], fmt(rows[0][i]) if i not in per_i else rows[0][i])
                                          for i in range(len(cols)) if rows[0][i] is not None and cols[i] != "row_count"))
        elif per_i and figs and set(lab_i) <= per_i and o_col in num_i and re.search(r"\bLIMIT\s+\d+\s*$", sql_t, re.I):
            # the dates of one series ranked (when was it highest): the top readings and when, by the column the query orders by
            pi, vi = sorted(per_i)[0], o_col
            if not last.get("label"):
                subject = cols[vi].replace("_", " ")
            top = ["%s in %s" % (fmt(r[vi]), r[pi]) for r in rows[:3] if _num_or_none(r[vi]) is not None]
            low = o_dir == "ASC"
            text = "%s, %s: %s%s." % (_short(str(subject), 120), "lowest" if low else "highest", top[0],
                                      (", then " + " and ".join(top[1:])) if len(top) > 1 else "") if top else "%s rows; see the table." % len(rows)
        elif per_i and figs and set(lab_i) <= per_i:
            # a figure by period: where it started, where it ended, its peak
            pi, vi = sorted(per_i)[0], figs[0]
            pts = [(r[pi], _num_or_none(r[vi])) for r in rows if _num_or_none(r[vi]) is not None]
            # the years the question names frame the sentence ("from 2008 to 2026"), when the rows hold them: a query that returned
            # every year otherwise answered "from 2001" (18 Oct 2026, a visitor's Ask on FRED's metro unemployment)
            asked = sorted({int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(question or ""))})
            if len(asked) >= 2:
                yr = lambda p: int(str(p)[:4]) if re.match(r"^\d{4}", str(p)) else None  # noqa: E731
                inside = [x for x in pts if yr(x[0]) is not None and asked[0] <= yr(x[0]) <= asked[-1]]
                if len(inside) >= 2 and yr(inside[0][0]) == asked[0] and yr(inside[-1][0]) == asked[-1]:
                    pts = inside
            # the step's table keeps its first rows (nl_analyst.SQL_TABLE_ROWS); its "rows" number counts them all. A cut list's last
            # row is not where the series ends, and its peak may lie past the cut (18 Oct 2026: Detroit "to 4.8 in 2024-08")
            n_all = ((st.get("numbers") or {}).get("rows") or {}).get("value")
            cut = isinstance(n_all, (int, float)) and n_all == n_all and int(n_all) > len(rows)
            if len(pts) >= 2 and cut:
                (p0, v0), (p1, v1) = pts[0], pts[-1]
                text = "%s was %s in %s and %s in %s, the last of the first %s rows the query returned (of %s); ask for yearly " \
                       "averages to see every year." % (_short(str(subject), 120), fmt(v0), p0, fmt(v1), p1, format(len(rows), ","),
                                                        format(int(n_all), ","))
            elif len(pts) >= 2:
                (p0, v0), (p1, v1) = pts[0], pts[-1]
                hi = max(pts, key=lambda x: x[1])
                base_c = re.sub(r"^(?:average|total)_", "", cols[vi].lower())
                # a rate selected under another name ("Unemployment (%...)" AS spain_unemployment) is still a rate
                for src_q, src_b, al_q, al_b in re.findall(r'(?:"((?:[^"]|"")+)"|\b([A-Za-z_][A-Za-z0-9_]*))\s+AS\s+(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_]*))',
                                                           sql_t, re.I):
                    if (al_q or al_b).lower() == cols[vi].lower():
                        base_c = (src_q or src_b).lower()
                if any(re.sub(r"[^a-z0-9]", "", base_c) == re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in (rate_cols or set())):
                    ch = " (%+.1f percentage points)" % (v1 - v0)          # a rate's change, never a percent of a percent
                else:
                    ch = (" (%+.0f%%)" % (100.0 * (v1 - v0) / abs(v0))) if v0 else ""
                text = "%s went from %s in %s to %s in %s%s%s." % (
                    _short(str(subject), 120), fmt(v0), p0, fmt(v1), p1, ch,
                    ("; it peaked at %s in %s" % (fmt(hi[1]), hi[0])) if hi[0] not in (p0, p1) else "")
            else:
                text = "%s rows; see the table." % format(len(rows), ",")
        elif ranked:
            # a ranking query (ORDER BY ... LIMIT): the members in its order, first three named
            li, vi = lab_i[0], num_i[0]
            if o_col is not None and o_col in num_i:
                vi = o_col                                    # the column the ranking is ordered by (net score, gross margin; not a count)
                if not last.get("label"):
                    subject = cols[vi].replace("_", " ")
            top = ["%s (%s)" % (r[li], fmt(r[vi])) for r in rows[:3] if _num_or_none(r[vi]) is not None]
            low = o_col == vi and o_dir == "ASC"
            # a ranking of one period says which (21 Oct 2026, live: "price: Rafael (277)" was 2026-Q1 alone, a single day)
            one_p = re.search(r"strftime\([^)]*\)[^=\n]*=\s*'(\d{4}(?:-Q[1-4]|-\d{2})?)'", sql_t)
            if one_p and one_p.group(1) not in str(subject):
                subject = "%s in %s" % (subject, one_p.group(1))
            text = "%s%s: %s%s." % (_short(str(subject), 120), ", lowest first" if low else "", top[0],
                                    (", then " + " and ".join(top[1:])) if len(top) > 1 else "") \
                if top else "%s rows; see the table." % format(len(rows), ",")
        elif lab_i and num_i:
            li, vi = lab_i[0], num_i[0]
            # a row named by two labels (a year and an outcome type) is named by both (21 Oct 2026, live: "dogs: highest in 2024 (3,460),
            # lowest in 2024 (1)", the 1 a rare outcome of 2024, not 2024 as a whole)
            lbl = (lambda r: ", ".join(str(r[i]) for i in lab_i)) if len(lab_i) > 1 else (lambda r: r[li])  # noqa: E731
            vals = [(lbl(r), _num_or_none(r[vi])) for r in rows if _num_or_none(r[vi]) is not None]
            tot = (ix or {}).get("total")
            members = [(lb, v) for lb, v in vals if str(lb) != str(tot)]
            lim = re.search(r"\bLIMIT\s+(\d+)\s*$", sql_t, re.I)
            if lim and len(rows) >= int(lim.group(1)) and members:
                # the first rows of a longer list (the top readings of 2020, LIMIT 20): its last row is not the lowest of anything;
                # the readings in the query's own order, with their period (18 Oct 2026, "lowest in Monroe, MI (25.4)")
                pc = sorted(per_i)[0] if per_i else None
                nm = [i for i in lab_i if i != pc] or [li]       # the period goes after the value, as before
                top = ["%s (%s%s)" % (", ".join(str(r[i]) for i in nm), fmt(r[vi]), (", %s" % r[pc]) if pc is not None else "") for r in rows[:3]
                       if _num_or_none(r[vi]) is not None]
                low = o_col == vi and o_dir == "ASC"
                text = "%s, %s: %s%s." % (_short(str(subject), 120), "lowest" if low else "highest", top[0],
                                          (", then " + " and ".join(top[1:])) if len(top) > 1 else "")
            elif len(members) == 1:
                tv = next((v for lb, v in vals if str(lb) == str(tot)), None)
                text = "%s: %s %s%s." % (_short(str(subject), 120), members[0][0], fmt(members[0][1]),
                                          ("; %s as a whole: %s" % (tot, fmt(tv))) if tv is not None else "")
            elif members:
                hi = max(members, key=lambda x: x[1])
                lo = min(members, key=lambda x: x[1])
                text = "%s: highest in %s (%s), lowest in %s (%s)" % (
                    _short(str(subject), 120), hi[0], fmt(hi[1]), lo[0], fmt(lo[1]))
                tv = next((v for lb, v in vals if str(lb) == str(tot)), None)
                text += ("; %s as a whole: %s." % (tot, fmt(tv))) if tv is not None else "."
                if len(members) == 2 and hi[1] != lo[1]:
                    text = "%s: %s %s, %s %s." % (_short(str(subject), 120), hi[0], fmt(hi[1]), lo[0], fmt(lo[1])) + \
                        ((" %s as a whole: %s." % (tot, fmt(tv))) if tv is not None else "")
            else:
                text = "%s rows; see the table." % format(len(rows), ",")
        else:
            text = "%s rows match; see the table." % format(len(rows), ",")
    # a short alias the query gave a column ("CORESTICK..." AS v): the answer names the column itself
    sql_a = str((st.get("args") or {}).get("sql") or "")
    for src_q, src_b, al_q, al_b in re.findall(r'(?:"((?:[^"]|"")+)"|\b([A-Za-z_][A-Za-z0-9_]*))\s+AS\s+(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_]*))',
                                               sql_a, re.I):
        al, src = (al_q or al_b), (src_q or src_b).replace('""', '"')
        if len(al) <= 3 and al in cols and src and len(src) > len(al) and not re.fullmatch(r"(?i)[a-z]{1,3}", src):
            text = re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(al), src, text)
            cols = [src if c == al else c for c in cols]
    return {"question": str(question)[:300], "text": text, "columns": cols[:12], "rows": [r[:12] for r in shown], "bars": bars,
            "rows_total": len(rows), "sql": str((st.get("args") or {}).get("sql") or "")[:4000], "step": st.get("id")}
