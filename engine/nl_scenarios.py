"""
nl_scenarios: the report's scenario and contribution block (rep["scenarios"], design B of
plan/AI-INSIGHTS-DESIGN.md), computed by the browser adapter from the rows the engine kept.

    import nl_scenarios
    rep["scenarios"] = nl_scenarios.build(rep, frame, date_col, claims, plan, landed, pub, additive)

The AI report writer may quote a figure only when the payload holds it (the engine never lets a model
supply a number). The writer needed figures the engine does not state: which segment the change came
from and its share of it, price against volume against mix, revenue per unit, the run rate, what each
1% is worth, the gap to the largest segment, and what the forecast adds up to. This block states them,
each with its own text, so the writer copies and never computes.

The rules (the design's, restated where the code keeps them):
  * One claim is broken down (_pick_basis, final review 30 Sep 2026): the total of the plan's primary
    column (its first total in the engine's order: one currency, one kind of row), else the engine's primary
    claim when it is a total or a row count, or the total of the measure its average is about; with no
    primary to follow, the first total, else the row count. When the primary is a level or an average (a
    rate, a price, a rating) the block is refused, never read as a row count: "the headline is an average,
    so it has no parts that add up; see the headline finding".
  * The engine's own comparison windows (the latest 12 months against the 12 before, read from the
    claim's chart), and the rows the engine kept (the frame the analyses read, without any column the
    visitor withheld or coded).
  * The monthly totals of those rows MUST equal the claim's charted monthly values (1e-6, relative to
    the value, at least 1e-6 absolute) in every month of both windows, or the whole block is refused:
    "the breakdown could not be reconciled with the engine's own monthly totals". A figure that does not
    reconcile is not shown.
  * The unit: a claim in one currency (the engine's currency split) is in that currency's code, whatever
    unit the plan gave the column; else the plan's unit for the column.
  * A segment column is a category, geography or segment column in the frame that is not in
    privacy.flagged at all (a kept flagged column's consent covered the AI report, not a breakdown), with
    2 to 12 levels over the two windows, at least 2 of them in both windows. A level present in only one
    window is its own row, "new in the latest 12 months" (entered) or "not in the latest 12 months"
    (exited); only a level in both windows with fewer than 5 of the claim's rows in either folds into
    "other", as does a blank.
  * The headline items carry the claim's grade (they are the claim); every derived item (a contribution, a
    price, volume or mix part, a figure per unit, the run rate, the sensitivity, a gap) has grade null, the
    claim's grade as parent_grade and grade_words "part of a change graded WATCH; not graded itself"; a
    forecast item carries the forecast's grade; a fact carries null.
  * Shares of the change only when every segment moved the way the total did: segments that moved in
    opposite directions would take shares of the net change over 100%, so none is given (the amounts are).
  * Forecast items only when the engine's forecast is usable for planning (CONFIRMED): the base, low and
    high for 3, 6 and 12 months are sums of its points and of their own 80% ranges, labelled as such
    (a sum of monthly ranges is at least as wide as an 80% range for the total).
  * The segment the goal names comes first (_goal_first): the plan's goal, else the visitor's question, names
    "departments", so department segments the claim before the plan's other segment roles and the engine's
    dimensions (live baseline, 30 Sep 2026).
  * The historical range of a level (_history_range, group history_range): the engine forecasts counts and totals
    only, so for a rate, a price or an index with 3 years or more of monthly averages the block states the 10th,
    50th and 90th percentiles of its past 12-month and 3-month changes, the share of those windows that rose and
    their count: facts about the file's past, no grade, every label saying it is history, not a forecast.
  * No 3-month annualised run rate (three months times four is noise, not a rate).
  * Deterministic (math.fsum, sorted by (-|contribution|, name)), finite or absent (never NaN), and
    guarded against division by zero: a share of the change needs the total to move by at least 1% of
    its level, a segment's own change a prior total above zero, a figure per unit units above zero.
  * Figures per unit come for both windows and as a change in percent ("+16.8%"), overall and per segment.
  * The order is the worker's cap (_ordered; insight-proxy/src/report.js capScenarioItems keeps up to 160 items):
    the core first, every group in order with the per-segment groups (contribution, per_unit, gap) cut to the 6
    segments with the largest |contribution|; then the other segments' items, segment by segment in that rank. A
    reader that keeps the first N items keeps exactly what the worker's cap keeps; the page's own byte budget
    (nl_browser.results_for_ai) drops from the tail.

Every item's text comes from ONE function, _fmt_item, which uses the adapter's own number formats
(_fmt, _amt, _pct_text), so the writer's copy and the page's copy are the same characters.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import nl_browser as NB

GROUPS = ("headline", "contribution", "price_volume_mix", "per_unit", "run_rate", "sensitivity", "gap",
          "forecast", "history_range", "facts")
KINDS = ("amount", "change", "count", "percent", "points", "per_unit", "date")
SEGMENT_MAX_LEVELS = 12          # a segment column has at most 12 levels over the two windows
# the segments whose per-segment items (PER_SEGMENT) come in the core (_ordered): the worker's SCENARIO_TOP_SEGMENTS
# (insight-proxy/src/report.js capScenarioItems), so the adapter's order and the worker's cap keep the same items
TOP_SEGMENTS = 6
PER_SEGMENT = ("contribution", "per_unit", "gap")
SPARSE_ROWS = 5                  # a level with fewer rows than this in either window folds into "other"
OTHER = "other"
RECONCILE_TOL = 1e-6             # relative to the charted value, and at least this much absolute
SHARE_MIN_MOVE = 0.01            # shares of the change only when the total moved by 1% of its prior level
HORIZONS = (3, 6, 12)
SEGMENT_ROLES = ("segment", "geography")
SEGMENT_TYPES = ("category", "geography")
NOT_A_SEGMENT_ROLE = ("key", "entity", "metadata", "set_aside", "target", "date")
NOT_A_SEGMENT_TYPE = ("identifier", "code", "free_text", "entity", "date", "year")

NOT_RECONCILED = "the breakdown could not be reconciled with the engine's own monthly totals, so it is not shown"
AVERAGE_REFUSED = "the headline is an average, so it has no parts that add up; see the headline finding"
MIXED_SHARES = ("shares of the change are not given: segments moved in opposite directions, so shares of the net "
                "change would exceed 100%")
# the base the parts are measured against; they add up to the change by construction (reconciled, _adds_up), so that is
# stated as what it is, never as an assumption (review of the live run, 30 Sep 2026)
PVM_ASSUMES = "measured against the 12 months before; the three parts add up exactly to the change"
# no mix item: the two parts alone add up
PV_ASSUMES = "measured against the 12 months before; the two parts add up exactly to the change"
ENTERED = "new in the latest 12 months"                       # a level with rows only in the latest window
EXITED = "not in the latest 12 months"                        # a level with rows only in the window before
# a column the plan types as one of these is read as an average (nl_browser._how), never a total
AVERAGE_TYPES = ("level", "percentage", "log_scale", "rating", "ordinal", "duration")
# the block's own working columns beside the engine's (a landed name is a slug, so it never starts with a space)
WIN, MON, SEG = " window", " month", " segment"
NOTE = ("Descriptive arithmetic on the rows the engine kept, in the engine's own comparison windows (the "
        "latest 12 months against the 12 before); the monthly totals of these rows match the engine's own "
        "charted series. The headline figures carry the claim's grade; a figure derived from them is not graded "
        "itself and names the grade of the change it is part of. A contribution says where the change sits, not "
        "what caused it, and a what-if is arithmetic, not a forecast. The figures are shown rounded; they add up "
        "before rounding.")
NOTE_REFUSED = "No scenario or contribution figures for this file; the reasons are listed."
# the historical range of a level (a rate, a price, an index): the engine forecasts counts and totals only, so for a
# level the block states how much its monthly average moved in the file's own past windows, as history (_history_range)
HISTORY_TYPES = ("level", "percentage")
HISTORY_MIN_MONTHS = 36          # three years of monthly averages, at least
HISTORY_LAGS = (12, 3)           # the 12-month and the 3-month changes
HISTORY_NEFF_MIN = 10           # fewer independent windows than this: the extremes, not the 10th and 90th percentiles
HISTORY_NOTE = ("The historical range items (group history_range) say how much the monthly average moved in the "
                "file's own past windows: facts about the past, not a forecast and not graded. The windows overlap, so "
                "each range says how many independent windows they are worth (n_eff), and the non-overlapping changes "
                "are stated beside them; figures are given to 2 significant figures.")


def blank() -> Dict[str, Any]:
    """The block with nothing computed (what blank_report carries)."""
    return {"basis": None, "items": [], "refused": [], "note": ""}


# --------------------------------------------------------------------------- the one text function
def _sig_text(v: float, sig: int) -> str:
    """v to `sig` significant figures, trailing zeros kept (0.040, not 0.04), thousands separated, true minus."""
    if v == 0:
        return "0"
    a = abs(v)
    e = int(math.floor(math.log10(a)))
    dp = max(0, sig - 1 - e)
    r = round(v, dp) if dp else float(round(v, sig - 1 - e))
    s = ("%.*f" % (dp, r)) if dp else format(int(r), ",")
    return s.replace("-", "\u2212")


def _fmt_item(value: Any, kind: str, unit: str = "", sig: Optional[int] = None) -> str:
    """An item's text, from its value, kind and unit, in the adapter's own formats:
    amount / per_unit  _amt (3 significant digits, whole units from 1,000, the unit where a reader expects it)
    change             _amt with a "+" before a rise (a fall carries the true minus sign); unit "%" for a
                       change in percent ("+18.7%")
    count              a whole number with thousands separated
    percent            _pct_text (a share: "67.7%", two decimals under 1%)
    points             _fmt and "percentage points"
    date               the date as the engine wrote it (YYYY-MM-DD)"""
    if kind == "date":
        return str(value or "")
    v = NB._num(value)
    if v is None:
        return "n/a"
    if kind == "count":
        return format(int(round(v)), ",").replace("-", "−")
    if kind == "percent":
        return NB._pct_text(v).replace("-", "−")
    if kind == "points":
        s = _sig_text(v, sig) if sig else NB._fmt(v)
        return s if s == "n/a" else s + " percentage points"
    if sig and kind in ("change", "amount", "per_unit"):
        pre, post = NB._unit_parts(unit)
        s = _sig_text(v, sig)
        if pre:
            s = ("\u2212" + pre + s[1:]) if s.startswith("\u2212") else pre + s
        s += post
    else:
        s = NB._amt(v, unit)
    if kind == "change" and v > 0 and s != "n/a" and not s.startswith("−"):
        return "+" + s
    return s


def _close(a: float, b: float) -> bool:
    """a equals b to RECONCILE_TOL, relative to b and at least that much absolute."""
    return abs(a - b) <= RECONCILE_TOL * max(1.0, abs(b))


def _adds_up(parts: List[float], whole: float, scale: float) -> bool:
    """fsum(parts) equals whole to RECONCILE_TOL, relative to the larger of the whole and the totals it is the
    difference of (a change of 10 between two totals of a million carries the totals' rounding, not its own)."""
    return abs(math.fsum(parts) - whole) <= RECONCILE_TOL * max(1.0, abs(whole), abs(scale))


# --------------------------------------------------------------------------- the claim broken down
def _claim_charts(rep: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{finding id: its trend_windows chart} (a chart's first finding id is its claim)."""
    out: Dict[str, Dict[str, Any]] = {}
    for c in rep.get("charts") or []:
        if isinstance(c, dict) and c.get("type") == "trend_windows" and c.get("finding_ids"):
            out.setdefault(str(c["finding_ids"][0]), c)
    return out


def _breakable(claim: Dict[str, Any]) -> bool:
    """A row count, or a plain total (maybe of one kind of row, one currency, without a status the engine
    leaves out); never a like-for-like restatement (its rows are a subset chosen over the whole span)."""
    key = str(claim.get("key") or "")
    if key == "volume":
        return True
    return key.startswith("total:") and bool(claim.get("total_of")) and not claim.get("like_for_like_of")


def _plan_primary(plan: Optional[Dict[str, Any]], landed: Optional[Callable[[Any], Any]]) -> Tuple[str, str]:
    """(the plan's primary column under its landed name, its semantic type), or ("", "") with none."""
    name = str((plan or {}).get("primary") or "").strip()
    if not name:
        return "", ""
    land = None
    if landed is not None:
        try:
            land = landed(name)
        except Exception:  # noqa: BLE001 - an unmapped name falls back to its own spelling
            land = None
    st = ""
    for pc in (plan or {}).get("columns") or []:
        if isinstance(pc, dict) and str(pc.get("name") or "") == name:
            st = str(pc.get("semantic_type") or "")
            break
    return str(land or name), st


def _pick_basis(rep: Dict[str, Any], claims: Dict[str, Dict[str, Any]], charts: Dict[str, Dict[str, Any]],
                plan: Optional[Dict[str, Any]] = None, landed: Optional[Callable[[Any], Any]] = None
                ) -> Tuple[Optional[Dict[str, Any]], str]:
    """(the business finding to break down, why none), in the engine's own finding order (final review, 30 Sep
    2026: a plan whose primary was cost broke down the ticket count, and an FX file's rates were read as a row
    count):
      1. the plan's primary column: its first total (one currency, one kind of row); a primary the plan types as
         a level or an average (AVERAGE_TYPES) has none, and the block is refused (AVERAGE_REFUSED);
      2. the engine's primary claim: itself when it is a total or a row count; when it is an average, the total
         of the same measure, else the block is refused (an average never falls back to a row count);
      3. with no primary to follow: the first total, else the row count."""
    biz = [f for f in rep.get("findings") or [] if isinstance(f, dict) and f.get("kind") == "business"
           and f.get("id") in claims and f.get("id") in charts]
    if not biz:
        return None, "no business claim with a monthly series to break down"

    def totals_of(measure: str) -> List[Dict[str, Any]]:
        return [f for f in biz if str(claims[f["id"]].get("key") or "").startswith("total:")
                and str(claims[f["id"]].get("total_of") or "") == measure and _breakable(claims[f["id"]])]
    pcol, ptype = _plan_primary(plan, landed)
    if pcol:
        tot = totals_of(pcol)
        if tot:
            return tot[0], ""
        if ptype in AVERAGE_TYPES:
            return None, AVERAGE_REFUSED
    prim = (rep.get("primary_metric") or {}).get("finding_id")
    first = [f for f in biz if f["id"] == prim]
    if first:
        c = claims[first[0]["id"]]
        if _breakable(c):
            return first[0], ""
        key = str(c.get("key") or "")
        if key and not key.startswith("total:") and key != "volume":      # the engine's average of `key`
            tot = totals_of(key)
            return (tot[0], "") if tot else (None, AVERAGE_REFUSED)
    totals = [f for f in biz if str(claims[f["id"]].get("key") or "").startswith("total:")]
    volume = [f for f in biz if claims[f["id"]].get("key") == "volume"]
    for f in totals + volume:
        if _breakable(claims[f["id"]]):
            return f, ""
    return None, ("contributions and scenarios are computed only for a total or a row count; the business "
                  "claims here are averages or like-for-like restatements, which do not add up")


def _claim_mask(frame: Any, claim: Dict[str, Any], measure: Optional[str]) -> Tuple[Any, str]:
    """(the rows the claim sums or counts, why they cannot be chosen): the engine's own conditions
    (measure.py: `col IS NOT NULL`, a kind split `c = level` or `(c IS NULL OR c <> level)`, a currency
    `UPPER(TRIM(c)) = code`, a status left out `(c IS NULL OR LOWER(TRIM(c)) NOT IN (...))`)."""
    import pandas as pd
    mask = pd.Series(True, index=frame.index)
    if measure is not None:
        if measure not in frame.columns:
            return mask, "the claim's measure is not among the rows the engine kept"
        mask &= frame[measure].notna()
    sp = claim.get("kind_split")
    if sp:
        col = sp.get("column")
        if col not in frame.columns:
            return mask, "the claim's rows are chosen by a column that is not shown"
        s = frame[col]
        eq = s.notna() & (s.astype(str) == str(sp.get("level")))
        mask &= (~eq) if sp.get("group") == "other" else eq
    cur = claim.get("currency")
    if cur:
        col = cur.get("column")
        if col not in frame.columns:
            return mask, "the claim's rows are chosen by a column that is not shown"
        s = frame[col]
        mask &= s.notna() & (s.astype(str).str.strip().str.upper() == str(cur.get("code") or "").upper())
    stx = claim.get("status_excluded")
    if stx:
        col = stx.get("column")
        if col not in frame.columns:
            return mask, "the claim's rows are chosen by a column that is not shown"
        s = frame[col]
        vals = set(str(v).strip().lower() for v in stx.get("values") or [])
        mask &= ~(s.notna() & s.astype(str).str.strip().str.lower().isin(vals))
    return mask, ""


def _what(claim: Dict[str, Any], measure: Optional[str]) -> str:
    """The claim's measure in words, as the engine names it: "total revenue", "total amount where category is
    'RENT'", "total amount for the other category values", "total amount in USD", "rows"."""
    if measure is None:
        return "rows"
    words = ""
    sp = claim.get("kind_split")
    cur = claim.get("currency")
    if cur:
        words = " in %s" % cur.get("code")
    elif sp:
        words = (" where %s is '%s'" % (sp.get("column"), str(sp.get("level"))[:40]) if sp.get("group") != "other"
                 else " for the other %s values" % sp.get("column"))
    return "total %s%s" % (measure, words)


# --------------------------------------------------------------------------- the segment column
def _plan_entries(plan: Optional[Dict[str, Any]], landed: Optional[Callable[[Any], Any]]) -> Dict[str, Dict[str, Any]]:
    """{landed name: the plan's column entry}."""
    out: Dict[str, Dict[str, Any]] = {}
    for pc in (plan or {}).get("columns") or []:
        if not isinstance(pc, dict) or not pc.get("name"):
            continue
        name = str(pc["name"])
        land = None
        if landed is not None:
            try:
                land = landed(name)
            except Exception:  # noqa: BLE001 - an unmapped name falls back to its own spelling
                land = None
        out.setdefault(str(land or name), pc)
    return out


def _segment_candidates(rep: Dict[str, Any], frame: Any, plan_by: Dict[str, Dict[str, Any]], busy: set
                        ) -> List[str]:
    """The columns that may segment the claim, in order: the plan's segment and geography roles, then its
    category and geography types, then the engine's dimensions. Never a flagged column (whatever the
    visitor decided), a column the plan calls a key, entity, identifier, code or free text, a number column,
    or a column the claim already reads."""
    flagged = {str(f.get("column")) for f in (rep.get("privacy") or {}).get("flagged") or [] if isinstance(f, dict)}
    dims = [str(d) for d in (rep.get("roles") or {}).get("dimensions") or []]
    order: List[str] = []
    for land, pc in plan_by.items():
        if str(pc.get("role") or "") in SEGMENT_ROLES:
            order.append(land)
    for land, pc in plan_by.items():
        if str(pc.get("semantic_type") or "") in SEGMENT_TYPES:
            order.append(land)
    order += dims
    out: List[str] = []
    for c in order:
        if c in out or c in busy or c in flagged or c not in frame.columns:
            continue
        pc = plan_by.get(c)
        if pc is not None:
            role, st = str(pc.get("role") or ""), str(pc.get("semantic_type") or "")
            if st in NOT_A_SEGMENT_TYPE or role in NOT_A_SEGMENT_ROLE:
                continue
            if role not in SEGMENT_ROLES and st not in SEGMENT_TYPES:
                continue
        elif c not in dims:
            continue
        s = frame[c]
        if str(s.dtype) not in ("object", "category", "string", "bool"):
            continue
        out.append(c)
    return out


def _stem(w: str) -> str:
    """A word without its plural ending: departments -> department, categories -> category, boxes -> box."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(text: Any) -> List[str]:
    """The words of a question or a column name (net_sales, NetSales and "net sales" are the same two), stemmed."""
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text or ""))
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", t.lower())]


def _goal_first(cands: List[str], goal: str, plan_by: Dict[str, Dict[str, Any]]) -> List[str]:
    """The segment candidates with the ones the goal names first, in the order the goal names them (the live baseline
    of 30 Sep 2026: the question asked which departments stand out, and keeping the review text switched the breakdown
    to verified_purchase). A column is named when every word of its name (its plan name, else its landed name) is in
    the goal, singular or plural ("departments" names department). Then the rest in their own order: the plan's
    segment roles, its category and geography types, the engine's dimensions (_segment_candidates)."""
    gw = _words(goal)
    if not gw or not cands:
        return list(cands)
    named = []
    for c in cands:
        cw = [w for w in _words((plan_by.get(c) or {}).get("name") or c) if w]
        if cw and all(w in gw for w in cw):
            named.append((min(gw.index(w) for w in cw), cands.index(c), c))
    first = [c for _p, _i, c in sorted(named)]
    return first + [c for c in cands if c not in first]


def _segment_levels(frame: Any, col: str, rows: Any, pub: Optional[Callable[[Any], Any]]) -> Dict[str, Any]:
    """The column's levels over the claim's rows in the windows: {"series" (each row's level as shown, or None
    when the column cannot segment), "raw" (each row's own level, None for a blank), "levels" (shown, in name
    order), "folded", "entered", "exited", "why"}. A level with rows in only one window is shown as its own row
    (final review, 30 Sep 2026: a store that opened and one that closed were folded into "other", which then
    took 97.6% of the change): ENTERED when its rows are all in the latest window, EXITED when all in the one
    before. A level in both windows with fewer than SPARSE_ROWS rows in either folds into "other", as does a
    blank. The column needs at least 2 levels in both windows with SPARSE_ROWS rows each; a level that reads as
    personal data under the report's scrubber rules the column out."""
    out: Dict[str, Any] = {"series": None, "raw": None, "levels": [], "folded": [], "entered": [], "exited": [],
                           "why": ""}
    s = frame.loc[rows.index, col]
    lab = s.map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v).strip())
    lab = lab.where(lab.notna() & (lab != ""), None)
    names = sorted(set(x for x in lab.dropna().tolist()))
    if len(names) < 2 or len(names) > SEGMENT_MAX_LEVELS:
        out["why"] = "has %s levels" % ("fewer than 2" if len(names) < 2 else "more than %d" % SEGMENT_MAX_LEVELS)
        return out
    if any(len(x) > 80 for x in names):
        out["why"] = "has values too long to be categories"
        return out
    if pub is not None and any(pub(x) != x for x in names):
        out["why"] = "has a value that reads as personal data"
        return out
    win = rows[WIN]
    folded: List[str] = []
    entered: List[str] = []
    exited: List[str] = []
    both: List[str] = []
    for x in names:
        n0 = int(((lab == x) & (win == "prior")).sum())
        n1 = int(((lab == x) & (win == "latest")).sum())
        if n0 == 0:
            entered.append(x)
        elif n1 == 0:
            exited.append(x)
        elif n0 < SPARSE_ROWS or n1 < SPARSE_ROWS:
            folded.append(x)
        else:
            both.append(x)
    if len(both) < 2:
        out["why"] = "has fewer than 2 levels with %d rows in each window" % SPARSE_ROWS
        return out
    shown = [x for x in names if x not in folded]
    if OTHER in shown:
        out["why"] = "already has a level named other"
        return out
    if int(lab.isna().sum()):
        folded.append("(blank)")
    out.update(series=lab.where(lab.isin(shown), OTHER), raw=lab, levels=shown, folded=folded, entered=entered,
               exited=exited)
    return out


def _seg_name(g: str, entered: List[str], exited: List[str]) -> str:
    """A level as its rows and labels name it: "Uptown (new in the latest 12 months)" for a level with rows only in
    the latest window, "Mall (not in the latest 12 months)" for one with rows only in the window before."""
    if g in entered:
        return "%s (%s)" % (g, ENTERED)
    if g in exited:
        return "%s (%s)" % (g, EXITED)
    return g


# --------------------------------------------------------------------------- the block
def build(rep: Dict[str, Any], frame: Any, date_col: Optional[str], claims: Dict[str, Dict[str, Any]],
          plan: Optional[Dict[str, Any]] = None, landed: Optional[Callable[[Any], Any]] = None,
          pub: Optional[Callable[[Any], Any]] = None, additive: Optional[Callable[[Any], str]] = None,
          goal: str = "") -> Dict[str, Any]:
    """rep["scenarios"]. rep: the report so far (findings, charts, primary_metric, privacy, roles, forecast);
    frame: the rows the engine kept under its landed names, without the columns the visitor withheld or coded;
    date_col: the engine's date column; claims: {finding id: {key, total_of, kind_split, currency,
    status_excluded, like_for_like_of}} from the engine's gated claims; plan: the AI plan (its column roles and
    units), or None; landed: the file's header -> the landed name; pub: the report's text scrubber; additive:
    the engine's measure.additive_kind ('money', 'units' or ''); goal: the question the report answers (the plan's
    goal, else the visitor's question): a column it names segments the claim first (_goal_first)."""
    import pandas as pd
    out = blank()
    refused: List[str] = out["refused"]
    if frame is None or date_col is None or date_col not in getattr(frame, "columns", []):
        refused.append("no column holds dates, so there are no comparison windows to break a change down in")
        out["note"] = NOTE_REFUSED
        return out
    months = pd.to_datetime(frame[date_col], errors="coerce").dt.strftime("%Y-%m")
    items: List[Dict[str, Any]] = []
    fc_items, fc_refused = _forecast_items(rep)
    charts = _claim_charts(rep)
    f, why = _pick_basis(rep, claims, charts, plan, landed)
    if f is None:
        refused.append(why)
    else:
        basis, b_items, b_refused = _breakdown(rep, frame, date_col, months, f, claims[f["id"]], charts[f["id"]],
                                               plan, landed, pub, additive, goal)
        out["basis"] = basis
        refused.extend(b_refused)
        if not basis["reconciles"]:
            out["items"] = []
            out["note"] = NOTE_REFUSED
            return out
        items.extend(b_items)
    items.extend(fc_items)
    refused.extend(fc_refused)
    h_items, h_refused = _history_range(rep, frame, date_col, months, claims, plan, landed, additive, pub)
    items.extend(h_items)
    refused.extend(h_refused)
    items.extend(_facts(frame, date_col, months))
    out["items"] = _ordered(items)
    out["note"] = (NOTE if out["basis"] is not None else NOTE_REFUSED) + (" " + HISTORY_NOTE if h_items else "")
    return out


def segment_rank(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """{segment: its rank}, as the worker's capScenarioItems ranks them: a segment with a contribution before one
    without, then by the size of its contribution to the change (|value| of its contribution item of kind change
    whose unit is not "%"), largest first, then by where it first appears among the items."""
    first: Dict[str, int] = {}
    size: Dict[str, float] = {}
    for it in items:
        s = it.get("segment")
        if not isinstance(s, str):
            continue
        first.setdefault(s, len(first))
        v = it.get("value")
        if it["group"] == "contribution" and it.get("kind") == "change" and it.get("unit") != "%" \
                and isinstance(v, (int, float)) and not isinstance(v, bool):
            size[s] = max(size.get(s, 0.0), abs(float(v)))
    ranked = sorted(first, key=lambda s: (s not in size, -size.get(s, 0.0), first[s]))
    return {s: i for i, s in enumerate(ranked)}


def _ordered(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The items in the order the worker's cap keeps them (insight-proxy/src/report.js capScenarioItems: up to 160
    items, by this priority), so a reader that keeps the first N keeps exactly what the cap keeps: the core first,
    every item of every group in the order of GROUPS (the headline, contributions, price, volume and mix, figures
    per unit, the run rate, the sensitivity, gaps, the forecast, the facts) with the per-segment groups
    (PER_SEGMENT: contribution, per_unit, gap) cut to the TOP_SEGMENTS segments with the largest |contribution|
    (segment_rank); then the rest, segment by segment in that rank, each segment's items in the order of GROUPS.
    Stable: within a group each part keeps its own order (segments by (-|contribution|, name))."""
    order = {g: i for i, g in enumerate(GROUPS)}
    rank = segment_rank(items)

    def extra(it: Dict[str, Any]) -> bool:
        s = it.get("segment")
        return it["group"] in PER_SEGMENT and isinstance(s, str) and rank[s] >= TOP_SEGMENTS
    core = sorted((it for it in items if not extra(it)), key=lambda it: order[it["group"]])
    rest = sorted((it for it in items if extra(it)), key=lambda it: (rank[it["segment"]], order[it["group"]]))
    return core + rest


def _grade_words(group: str, grade: Optional[str], parent: Optional[str]) -> Optional[str]:
    """An item's grade in words: the claim itself ("the claim itself, graded WATCH"), a figure derived from it
    ("part of a change graded CONFIRMED; not graded itself"), the forecast ("the engine's forecast, graded
    CONFIRMED (usable for planning)") or a fact ("a fact about the rows, not graded")."""
    if group == "facts":
        return "a fact about the rows, not graded"
    if group == "history_range":
        return "a fact about the file's past, not graded: history, not a forecast"
    if group == "forecast":
        return "the engine's forecast, graded %s (%s)" % (grade, NB._FC_GRADE_WORDS.get(str(grade), "not graded")) \
            if grade else "the engine's forecast, not graded"
    if group == "headline":
        return "the claim itself, graded %s" % grade if grade else "the claim itself, not graded"
    return "part of a change graded %s; not graded itself" % parent if parent else "not graded"


def _item(iid: str, group: str, label: str, value: Any, kind: str, unit: str, grade: Optional[str],
          columns: List[str], window: Optional[str], op: str, segment: Optional[str] = None,
          assumes: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One item. `grade` is the grade of what the item comes from: the claim's for the headline and every
    figure derived from it, the forecast's for a forecast item, None for a fact. Only the headline (the claim
    itself) and a forecast item carry it as their own grade; a derived item has grade None and names it as
    parent_grade (final review, 30 Sep 2026: South's -1.63% read CONFIRMED because the total's rise was)."""
    v = value if kind == "date" else NB._num(value)
    if v is None or v == "":
        return None
    if kind == "count":
        v = float(round(v))
    elif kind != "date":
        v = round(v, 6)                   # a millionth: inside the 1e-6 the block reconciles to, and short JSON
    own = grade if group in ("headline", "forecast") else None
    parent = grade if group not in ("headline", "forecast", "facts") else None
    return {"id": iid, "group": group, "segment": segment, "label": label, "value": v,
            "text": _fmt_item(v, kind, unit), "kind": kind, "unit": unit, "grade": own, "parent_grade": parent,
            "grade_words": _grade_words(group, own, parent), "assumes": assumes,
            "inputs": {"columns": [c for c in columns if c], "window": window, "op": op}}


def _slugs(names: List[str]) -> Dict[str, str]:
    """A short id part for each level, unique ('east', 'north_1' when two levels slug alike)."""
    out: Dict[str, str] = {}
    used: set = set()
    for n in names:
        base = re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_")[:24] or "level"
        s, k = base, 1
        while s in used:
            k += 1
            s = "%s_%d" % (base, k)
        used.add(s)
        out[n] = s
    return out


def _breakdown(rep: Dict[str, Any], frame: Any, date_col: str, months: Any, f: Dict[str, Any],
               claim: Dict[str, Any], chart: Dict[str, Any], plan: Optional[Dict[str, Any]],
               landed: Optional[Callable[[Any], Any]], pub: Optional[Callable[[Any], Any]],
               additive: Optional[Callable[[Any], str]], goal: str = ""
               ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """(basis, items, refused) for the claim `f`."""
    import pandas as pd
    refused: List[str] = []
    data = chart.get("data") or {}
    win = data.get("windows") or {}
    prior_w, latest_w = list(win.get("prior") or []), list(win.get("latest") or [])
    key = str(claim.get("key") or "")
    measure = None if key == "volume" else str(claim.get("total_of"))
    grade = f.get("grade")
    plan_by = _plan_entries(plan, landed)
    unit = ""
    if measure is not None:
        pc = plan_by.get(measure) or {}
        code = str((claim.get("currency") or {}).get("code") or "").strip().upper()
        # a claim in one currency is in that currency, whatever unit the plan gave the column (final review, 30
        # Sep 2026: the EUR total read "196,081 USD" when the plan's unit for the amount column was USD)
        unit = code if code else "%" if pc.get("semantic_type") == "percentage" else str(pc.get("unit") or "")
    basis = {"finding_id": f["id"], "claim": f.get("claim") or "", "grade": grade,
             "grade_words": NB._grade_plain(grade), "measure": measure or "rows",
             "how": "count" if measure is None else "total", "unit": unit,
             "windows": {"prior": prior_w, "latest": latest_w},
             "rows": {"prior": 0, "latest": 0, "units_missing": None},
             "segment": {"column": None, "levels": [], "folded": [], "entered": [], "exited": []},
             "reconciles": False}
    if len(prior_w) != 2 or len(latest_w) != 2:
        refused.append(NOT_RECONCILED)
        return basis, [], refused
    pm, lm = NB._month_range(prior_w[0], prior_w[1]), NB._month_range(latest_w[0], latest_w[1])
    if len(pm) != 12 or len(lm) != 12 or NB._shift_month(pm[-1], 1) != lm[0]:
        refused.append(NOT_RECONCILED)
        return basis, [], refused
    mask, why = _claim_mask(frame, claim, measure)
    if why:
        refused.append(why)
        return basis, [], refused
    w = pd.Series(None, index=frame.index, dtype=object)
    w[months.isin(pm)] = "prior"
    w[months.isin(lm)] = "latest"
    sel = mask & w.notna()
    rows = frame.loc[sel].copy()
    rows[WIN] = w[sel]
    rows[MON] = months[sel]
    val = (lambda df: [1.0] * len(df)) if measure is None else (lambda df: [float(x) for x in df[measure]])

    def total(df: Any) -> float:
        return math.fsum(val(df))

    # -- reconcile: every month of both windows against the claim's charted value
    cm = [str(x) for x in data.get("months") or []]
    cv = list(data.get("values") or [])
    chart_of = {m: cv[i] for i, m in enumerate(cm) if i < len(cv)}
    ok = all(m in chart_of for m in pm + lm)
    by_month: Dict[str, float] = {}
    if ok:
        for m, g in rows.groupby(MON, sort=True):
            by_month[str(m)] = total(g)
        for m in pm + lm:
            exp = NB._num(chart_of.get(m))
            got_v = by_month.get(m, 0.0)
            if exp is None:
                if got_v != 0.0:
                    ok = False
                    break
                continue
            if not _close(got_v, exp):
                ok = False
                break
    P, L = rows[rows[WIN] == "prior"], rows[rows[WIN] == "latest"]
    basis["rows"]["prior"], basis["rows"]["latest"] = int(len(P)), int(len(L))
    if not ok:
        refused.append(NOT_RECONCILED)
        return basis, [], refused
    basis["reconciles"] = True
    R0, R1 = total(P), total(L)
    change = R1 - R0
    what = _what(claim, measure)
    cols = [c for c in (date_col, measure) if c]
    amount_kind = "count" if measure is None else "amount"
    items: List[Dict[str, Any]] = []

    def add(it: Optional[Dict[str, Any]]) -> None:
        if it is not None:
            items.append(it)

    # -- headline
    add(_item("headline.prior", "headline", "%s, the 12 months before" % NB._cap(what), R0, amount_kind, unit, grade,
              cols, "prior", "count" if measure is None else "sum"))
    add(_item("headline.latest", "headline", "%s, the latest 12 months" % NB._cap(what), R1, amount_kind, unit, grade,
              cols, "latest", "count" if measure is None else "sum"))
    add(_item("headline.change", "headline", "Change in %s, the latest 12 months against the 12 before" % what,
              change, "change", unit, grade, cols, "both", "latest - prior"))
    if R0 > 0:
        add(_item("headline.change_pct", "headline", "Change in %s, in percent of the 12 months before" % what,
                  100.0 * change / R0, "change", "%", grade, cols, "both", "percent change"))

    # -- the units column, for figures per unit and price, volume and mix
    units_col, u_why = _units_column(rep, frame, measure, plan_by, additive)
    if measure is not None and u_why:
        refused.append(u_why)
    if units_col is not None:
        miss = int(rows[units_col].isna().sum())
        basis["rows"]["units_missing"] = miss
        neg = bool((pd.to_numeric(rows[units_col], errors="coerce") < 0).any())
        if miss:
            refused.append("figures per unit, and price against volume against mix, need units on every row: %s "
                           "of the claim's rows in the two windows have none" % format(miss, ","))
            units_col = None
        elif neg:
            refused.append("figures per unit need units of zero or more; some rows hold negative units")
            units_col = None

    # -- the segment column
    busy = set(c for c in (date_col, measure, units_col) if c)
    for k in ("kind_split", "currency", "status_excluded"):
        if claim.get(k):
            busy.add(str(claim[k].get("column")))
    seg_col, lv = None, None
    cands = _goal_first(_segment_candidates(rep, frame, plan_by, busy), goal, plan_by)
    for c in cands:
        got = _segment_levels(frame, c, rows, pub)
        if got["series"] is not None:
            seg_col, lv = c, got
            break
    seg = lv["series"] if lv else None
    entered, exited = (list(lv["entered"]), list(lv["exited"])) if lv else ([], [])
    if seg_col is None:
        refused.append("no segment column qualifies: a segment is a category, geography or segment column that is "
                       "not flagged as personal data and has 2 to %d levels, at least 2 of them with %d of the "
                       "claim's rows in both windows" % (SEGMENT_MAX_LEVELS, SPARSE_ROWS))
    else:
        basis["segment"] = {"column": seg_col, "levels": list(lv["levels"]), "folded": list(lv["folded"]),
                            "entered": entered, "exited": exited}

    def name(g: str) -> str:
        return _seg_name(g, entered, exited)
    per_unit_ok = units_col is not None and measure is not None and additive is not None \
        and additive(measure) == "money"
    U0 = math.fsum(float(x) for x in P[units_col]) if per_unit_ok else 0.0
    U1 = math.fsum(float(x) for x in L[units_col]) if per_unit_ok else 0.0
    if per_unit_ok and (U0 <= 0 or U1 <= 0):
        refused.append("figures per unit need units above zero in both windows")
        per_unit_ok = False

    # -- contributions per segment (a level present in only one window is its own row: entered or exited)
    groups: List[str] = []
    stats: Dict[str, Dict[str, float]] = {}
    raw: Dict[str, Dict[str, float]] = {}
    if seg is not None:
        rows = rows.assign(**{SEG: seg})
        P, L = rows[rows[WIN] == "prior"], rows[rows[WIN] == "latest"]
        names = list(lv["levels"]) + ([OTHER] if (rows[SEG] == OTHER).any() else [])
        for g in names:
            gp, gl = P[P[SEG] == g], L[L[SEG] == g]
            st = {"r0": total(gp), "r1": total(gl), "n0": float(len(gp)), "n1": float(len(gl))}
            if per_unit_ok:
                st["u0"] = math.fsum(float(x) for x in gp[units_col])
                st["u1"] = math.fsum(float(x) for x in gl[units_col])
            stats[g] = st
        # every level's own latest total, the folded ones too: the largest level is chosen among all of them
        lab_l = lv["raw"].loc[L.index]
        for g in sorted(set(x for x in lab_l.dropna().tolist())):
            gl = L[lab_l == g]
            raw[g] = {"r1": total(gl), "u1": math.fsum(float(x) for x in gl[units_col]) if per_unit_ok else 0.0}
        groups = sorted(names, key=lambda g: (-abs(stats[g]["r1"] - stats[g]["r0"]), g))
        if not _adds_up([stats[g]["r1"] - stats[g]["r0"] for g in groups], change, max(abs(R0), abs(R1))):
            refused.append("the contributions do not add up to the change, so none is shown")
            groups = []
    slug = _slugs(groups)
    seg_cols = cols + ([seg_col] if seg_col else [])
    shares = bool(groups) and change != 0.0 and abs(change) >= SHARE_MIN_MOVE * abs(R0)
    if groups and not shares:
        refused.append("shares of the change are not given: the total moved by less than 1% of its level")
    elif shares:
        # a share of the net change is a share only when every part moved the way the total did (final review,
        # 30 Sep 2026: refunds took one channel to -161% and the shares read 504% and -236%)
        cs = [stats[g]["r1"] - stats[g]["r0"] for g in groups]
        if any(c * change < 0 for c in cs) or any(abs(100.0 * c / change) > 100.0 + 1e-9 for c in cs):
            refused.append(MIXED_SHARES)
            shares = False
    for g in groups:
        st, sid = stats[g], "contribution.%s" % slug[g]
        c = st["r1"] - st["r0"]
        add(_item(sid + ".prior", "contribution", "%s: %s, the 12 months before" % (name(g), what), st["r0"],
                  amount_kind, unit, grade, seg_cols, "prior", "count" if measure is None else "sum", g))
        add(_item(sid + ".latest", "contribution", "%s: %s, the latest 12 months" % (name(g), what), st["r1"],
                  amount_kind, unit, grade, seg_cols, "latest", "count" if measure is None else "sum", g))
        add(_item(sid + ".change", "contribution", "%s: its contribution to the change in %s" % (name(g), what), c,
                  "change", unit, grade, seg_cols, "both", "latest - prior", g))
        if shares:
            add(_item(sid + ".share", "contribution", "%s: its share of the change in %s" % (name(g), what),
                      100.0 * c / change, "percent", "%", grade, seg_cols, "both", "share of the change", g))
        if st["r0"] > 0:
            add(_item(sid + ".pct", "contribution", "%s: its own change in %s, in percent" % (name(g), what),
                      100.0 * c / st["r0"], "change", "%", grade, seg_cols, "both", "percent change", g))

    # -- price, volume and mix: a money total and its units; mix needs a segment
    u_cols = seg_cols + ([units_col] if per_unit_ok else [])
    if per_unit_ok:
        P0, P1 = R0 / U0, R1 / U1
        volume = (U1 - U0) * P0
        pvm_ok = True
        if groups:
            bad = [g for g in groups if stats[g]["u0"] <= 0 or (stats[g]["u1"] <= 0 and stats[g]["r1"] != 0.0)]
            if not bad:
                price = math.fsum(stats[g]["u1"] * (stats[g]["r1"] / stats[g]["u1"] - stats[g]["r0"] / stats[g]["u0"])
                                  for g in groups if stats[g]["u1"] > 0)
                mix = math.fsum((stats[g]["u1"] - U1 * stats[g]["u0"] / U0) * (stats[g]["r0"] / stats[g]["u0"])
                                for g in groups)
            else:
                pvm_ok = False              # no prior price for it, or money with no units: the split would not add up
                new = [g for g in bad if g in entered]
                refused.append("the mix effect needs units above zero for every %s value in both windows%s" % (
                    seg_col, (" (%s %s %s)" % (NB._listed(new), "is" if len(new) == 1 else "are", ENTERED))
                    if new else ""))
        if not groups or not pvm_ok:
            price, mix = R1 - U1 * P0, None
        parts = [price, volume] + ([mix] if mix is not None else [])
        pvm_words = "price, volume and mix" if mix is not None else "price and volume"
        assumes = PVM_ASSUMES if mix is not None else PV_ASSUMES
        if not _adds_up(parts, change, max(abs(R0), abs(R1))):
            refused.append("%s do not add up to the change, so they are not shown" % pvm_words)
        else:
            add(_item("price_volume_mix.price", "price_volume_mix",
                      "Price effect: the change in %s per unit, at the latest 12 months' units" % measure, price,
                      "change", unit, grade, u_cols, "both", "price effect", None, assumes))
            add(_item("price_volume_mix.volume", "price_volume_mix",
                      "Volume effect: the change in %s, at the %s per unit of the 12 months before" % (units_col, measure),
                      volume, "change", unit, grade, u_cols, "both", "volume effect", None, assumes))
            if mix is not None:
                add(_item("price_volume_mix.mix", "price_volume_mix",
                          "Mix effect: the shift of %s between %s values, each at its own %s per unit of the 12 months "
                          "before" % (units_col, seg_col, measure), mix, "change", unit, grade, u_cols, "both",
                          "mix effect", None, assumes))
        # -- per unit, overall and per segment: both windows and the change in percent (review, 30 Sep 2026: the
        # writer's "revenue per unit 41 to 47.9 (+16.8%)" lost its "+16.8%" to the worker's guard, as no item held it)
        pu = "%s per unit (%s / %s)" % (measure, measure, units_col)
        add(_item("per_unit.prior", "per_unit", "%s, the 12 months before" % NB._cap(pu), P0, "per_unit", unit, grade,
                  cols + [units_col], "prior", "per unit"))
        add(_item("per_unit.latest", "per_unit", "%s, the latest 12 months" % NB._cap(pu), P1, "per_unit", unit, grade,
                  cols + [units_col], "latest", "per unit"))
        if P0 > 0:
            add(_item("per_unit.change_pct", "per_unit", "Change in %s, in percent of the 12 months before" % pu,
                      100.0 * (P1 / P0 - 1.0), "change", "%", grade, cols + [units_col], "both", "percent change"))
        for g in groups:
            st = stats[g]
            for wk, r, u in (("prior", st["r0"], st["u0"]), ("latest", st["r1"], st["u1"])):
                if u > 0:
                    add(_item("per_unit.%s.%s" % (slug[g], wk), "per_unit", "%s: %s, %s" % (
                        name(g), pu, "the 12 months before" if wk == "prior" else "the latest 12 months"), r / u,
                        "per_unit", unit, grade, u_cols, wk, "per unit", g))
            if st["u0"] > 0 and st["u1"] > 0 and st["r0"] > 0:
                add(_item("per_unit.%s.change_pct" % slug[g], "per_unit", "%s: its own change in %s, in percent" % (
                    name(g), pu), 100.0 * ((st["r1"] / st["u1"]) / (st["r0"] / st["u0"]) - 1.0), "change", "%", grade,
                    u_cols, "both", "percent change", g))

    # -- run rate: the latest 12 months, and their average month (never three months times four)
    add(_item("run_rate.year", "run_rate", "Run rate: %s over the latest 12 months" % what, R1, amount_kind, unit,
              grade, cols, "latest", "count" if measure is None else "sum", None,
              "the latest 12 months repeat unchanged"))
    add(_item("run_rate.month", "run_rate", "Run rate: %s in the average month of the latest 12 months" % what,
              R1 / 12.0, "amount", unit, grade, cols, "latest", "latest / 12", None,
              "the latest 12 months repeat unchanged"))

    # -- sensitivity: what each 1% of the measure is worth over a year at the latest level
    add(_item("sensitivity.one_pct", "sensitivity", "Each 1%% of %s, over a year at the latest 12 months' level" % what,
              R1 / 100.0, "amount", unit, grade, cols, "latest", "latest / 100", None,
              "a change of 1% held for a whole year, at the latest 12 months' level"))

    # -- the gap to the largest level in the latest 12 months, chosen among ALL the levels (a folded one and a new
    # one too); an item for each shown level in the latest window, never for "other" or a level that left
    top = sorted((g for g in raw if raw[g]["r1"] > 0), key=lambda g: (-raw[g]["r1"], g))[:1] if groups else []
    real = [g for g in groups if g != OTHER and g not in exited and (not top or g != top[0])]
    if top and real and R1 > 0:
        t, tr = top[0], raw[top[0]]
        largest = "the largest %s by %s in the latest 12 months" % (seg_col, what)
        for g in real:
            st, sid = stats[g], "gap.%s" % slug[g]
            gap = tr["r1"] - st["r1"]
            add(_item(sid + ".amount", "gap", "%s: how far its %s in the latest 12 months is below %s's, %s"
                      % (name(g), what, t, largest), gap, amount_kind, unit, grade, seg_cols, "latest",
                      "gap to the largest", g))
            add(_item(sid + ".points", "gap", "%s: how far its share of %s in the latest 12 months is below %s's, %s"
                      % (name(g), what, t, largest), 100.0 * gap / R1, "points", "points", grade, seg_cols, "latest",
                      "gap in share of the total", g))
            if per_unit_ok and st.get("u1", 0.0) > 0 and tr["u1"] > 0:
                p_top, p_g = tr["r1"] / tr["u1"], st["r1"] / st["u1"]
                if p_top > p_g:
                    add(_item(sid + ".per_unit", "gap", "What if %s's units of the latest 12 months had sold at %s's %s "
                              "per unit (%s): the extra %s" % (name(g), t, measure, largest, measure),
                              st["u1"] * (p_top - p_g), "change", unit, grade, u_cols, "latest", "what-if", g,
                              "its units unchanged, each at the largest's %s per unit" % measure))
    return basis, items, refused


def _units_column(rep: Dict[str, Any], frame: Any, measure: Optional[str], plan_by: Dict[str, Dict[str, Any]],
                  additive: Optional[Callable[[Any], str]]) -> Tuple[Optional[str], str]:
    """(the units a money total is sold in, why none): one of the engine's measures that it adds up as units
    (measure.additive_kind), not flagged; the plan's count columns first; a quantity word (units, qty,
    quantity, items, pieces) decides between several; more than one left means none (a guess is not a
    figure)."""
    if measure is None or additive is None or additive(measure) != "money":
        return None, ""
    flagged = {str(f.get("column")) for f in (rep.get("privacy") or {}).get("flagged") or [] if isinstance(f, dict)}
    ms = [str(m) for m in (rep.get("roles") or {}).get("measures") or []]
    cands = [m for m in ms if m != measure and m not in flagged and m in frame.columns and additive(m) == "units"]
    if not cands:
        return None, ""
    planned = [m for m in cands if str((plan_by.get(m) or {}).get("semantic_type") or "") == "count"]
    pool = planned or cands
    if len(pool) > 1:
        qty = [m for m in pool if set(re.split(r"[^a-z]+", m.lower())) & {"units", "qty", "quantity", "quantities",
                                                                         "items", "pieces"}]
        pool = qty if len(qty) == 1 else pool
    if len(pool) != 1:
        return None, ("figures per unit are not given: more than one column could be the units %s is sold in"
                      % measure)
    return pool[0], ""


def _forecast_items(rep: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The forecast added up over 3, 6 and 12 months, only when the engine's forecast is usable for planning:
    base (the points), low and high (their own 80% ranges, month by month, added up)."""
    fc = rep.get("forecast") or {}
    if not fc.get("available"):
        return [], []
    grade = NB.GRADE.get(str(fc.get("verdict") or ""))
    if grade != "CONFIRMED":
        return [], ["the forecast is %s, so it is not added up into scenarios" % NB._FC_GRADE_WORDS.get(
            grade, "not graded")]
    pts = [p for p in fc.get("forecast") or [] if isinstance(p, dict)]
    band = fc.get("band") if isinstance(fc.get("band"), dict) else {}
    lvl = NB._num(band.get("level"))
    if lvl is None or not pts:
        return [], ["the forecast carries no range level, so it is not added up"]
    lvl_pct = NB._fmt(100.0 * lvl if lvl <= 1 else lvl) + "%"
    series = str(fc.get("label") or "the forecast series")
    out: List[Dict[str, Any]] = []
    for h in HORIZONS:
        if len(pts) < h:
            continue
        head = pts[:h]
        vals = [NB._num(p.get("value")) for p in head]
        los = [NB._num(p.get("lo")) for p in head]
        his = [NB._num(p.get("hi")) for p in head]
        if any(v is None for v in vals + los + his):
            continue
        span = "%s to %s" % (NB._mon(head[0].get("month")), NB._mon(head[-1].get("month")))
        ranges = ("the months' own %s ranges added up, at least as wide as an %s range for the total"
                  % (lvl_pct, lvl_pct))
        # the words that say what the sum is come first, the series and its months last (a reader that cuts a
        # label short keeps what the figure is)
        for part, xs, words, assumes in (
                ("base", vals, "Base: the forecast", "the engine's forecast for each month"),
                ("low", los, "Low: " + ranges, "each month at the low end of its own %s range" % lvl_pct),
                ("high", his, "High: " + ranges, "each month at the high end of its own %s range" % lvl_pct)):
            it = _item("forecast.%d.%s" % (h, part), "forecast", "%s, the next %d months added up (%s, %s)"
                       % (words, h, series, span), math.fsum(xs), "amount", "", grade, [], None,
                       "sum of the next %d months' %s" % (h, {"base": "points", "low": "lows", "high": "highs"}[part]),
                       None, assumes)
            if it is not None:
                out.append(it)
    return out, []


def _history_measure(rep: Dict[str, Any], frame: Any, claims: Dict[str, Dict[str, Any]],
                     plan: Optional[Dict[str, Any]], landed: Optional[Callable[[Any], Any]],
                     additive: Optional[Callable[[Any], str]]) -> Tuple[Optional[str], str]:
    """(the level whose past moves the block states, its semantic type), or (None, ""): the plan's primary column when
    the plan reads it as a level or a percentage (HISTORY_TYPES: a rate, a price, an index); with no primary in the
    plan, the engine's primary claim when it is the average of a measure the engine does not add up (not money or
    units). Never a flow, a count or a rating, and only a measure the engine made its average-month claim about."""
    pcol, ptype = _plan_primary(plan, landed)
    if pcol:
        if ptype not in HISTORY_TYPES:
            return None, ""
        m = pcol
    else:
        c = claims.get(str((rep.get("primary_metric") or {}).get("finding_id") or "")) or {}
        m, ptype = str(c.get("key") or ""), ""
        if not m or m == "volume" or m.startswith(("total:", "volume:")) or c.get("like_for_like_of") \
                or (additive is not None and additive(m) in ("money", "units")):
            return None, ""
    keys = {str(c.get("key") or "") for c in claims.values() if not c.get("like_for_like_of")}
    return (m, ptype) if m in keys and m in getattr(frame, "columns", []) else (None, "")


def _history_range(rep: Dict[str, Any], frame: Any, date_col: str, months: Any, claims: Dict[str, Dict[str, Any]],
                   plan: Optional[Dict[str, Any]], landed: Optional[Callable[[Any], Any]],
                   additive: Optional[Callable[[Any], str]], pub: Optional[Callable[[Any], Any]] = None
                   ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(items, refused): the historical range of a level (review of the live baseline, 30 Sep 2026: the engine forecasts
    counts and totals only, so the report on an exchange rate had no outlook at all, and the writer had nothing honest
    to say about what an importer should expect). For the level _history_measure picks, with 3 years or more of monthly
    averages (HISTORY_MIN_MONTHS), for each lag in HISTORY_LAGS (12 and 3 months): every past window of that length, one
    ending each month (they overlap), whose first and last month both hold a value; the change of the monthly average
    across each; the 10th, 50th and 90th percentiles of those changes (linear interpolation) and the share of the
    windows in which it rose, with the window count. The monthly average is the mean of the month's values in the rows
    the engine kept; a level's zeros that mark "no value" (the analyses' own evidence, nl_browser._zero_shape: weekend
    placeholders between non-zero rates) are not counted, and the items say so. Facts about the file's past: no grade,
    group "history_range", every label saying it is history, not a forecast.

    T3 (wave 4, plan/WAVE4-A-DESIGN.md 3): the windows overlap, so each lag also states how many independent windows
    they are worth (n_eff, nl_inference.n_eff_overlapping), counts the windows that rose ("68 of the 104", never a
    percentage), uses the lowest and highest change instead of the 10th and 90th percentiles under HISTORY_NEFF_MIN
    independent windows, and adds the non-overlapping changes (the one ending in the latest month and every lag months
    before it): their count, lowest, middle and highest. Change figures are given to 2 significant figures."""
    import numpy as np
    import pandas as pd
    m, st = _history_measure(rep, frame, claims, plan, landed, additive)
    if m is None:
        return [], []
    plan_by = _plan_entries(plan, landed)
    pc = plan_by.get(m) or {}
    unit = str(pc.get("unit") or "")
    kind = "points" if st == "percentage" else "change"
    head = str(pc.get("name") or m)
    what = "the monthly average of %s" % (" ".join(str(pc.get("label") or "").split())[:80]
                                           or " ".join(head.replace("_", " ").split()).lower())
    if pub is not None:                       # the plan's own words go through the report's scrubber
        what = str(pub(what))
    v = pd.to_numeric(frame[m], errors="coerce").astype(float)
    mon = months                              # each row's month (build: the same index as the frame)
    note = None
    if st in NB.PLACEHOLDER_ZERO_TYPES or not st:
        if NB._zero_gate(v.to_numpy()):
            _z, ev = NB._zero_shape(v.to_numpy(), frame[date_col], None)
            if ev:
                vv = v.to_numpy().copy()
                vv[ev["mask"]] = np.nan
                v = pd.Series(vv, index=v.index)
                zi = {"zeros": int(ev["zeros"]), "word": NB._zero_word(head, unit),
                      "evidence": {k: x for k, x in ev.items() if k != "mask"}}
                note = NB._zero_note(head, zi)
                if len(note) > 240:
                    note = note.split(":", 1)[0] + " (they mark a day with no value)"
    ok = v.notna() & mon.notna()
    avg: Dict[str, float] = {}
    for mo, g in v[ok].groupby(mon[ok], sort=True):
        vals = [float(x) for x in g.tolist()]
        avg[str(mo)] = math.fsum(vals) / len(vals)
    ms = sorted(avg)
    if len(ms) < HISTORY_MIN_MONTHS or len(NB._month_range(ms[0], ms[-1])) < HISTORY_MIN_MONTHS:
        return [], ["no historical range of %s: it has %d months with a value, fewer than the %d (3 years) it needs"
                    % (what, len(ms), HISTORY_MIN_MONTHS)]
    span = "%s to %s" % (NB._mon(ms[0]), NB._mon(ms[-1]))
    cols = [c for c in (date_col, m) if c]
    items: List[Dict[str, Any]] = []
    import nl_inference as _ni
    last_ix = int(ms[-1][:4]) * 12 + int(ms[-1][5:7]) - 1
    for lag in HISTORY_LAGS:
        ends = [mo for mo in ms if NB._shift_month(mo, -lag) in avg]
        ch = [avg[mo] - avg[NB._shift_month(mo, -lag)] for mo in ends]
        if len(ch) < 2:
            continue
        n = len(ch)
        arr = np.asarray(ch, dtype=float)
        # T3 (wave 4): overlapping windows share months, so n of them are worth about n_eff independent ones; with
        # fewer than HISTORY_NEFF_MIN the 10th and 90th percentiles are not estimable and the extremes are stated
        neff = _ni.n_eff_overlapping(ch, lag)
        m_ind = max(1, int(round(neff)))
        wide = neff >= HISTORY_NEFF_MIN
        lo_k, hi_k = ("p10", "p90") if wide else ("min", "max")
        lo_v, hi_v = ((float(x) for x in np.percentile(arr, [10, 90])) if wide else (float(arr.min()), float(arr.max())))
        p50 = float(np.percentile(arr, 50))
        rose_k = sum(1 for x in ch if x > 0)
        # the non-overlapping alternative: the change ending in the latest month, and every lag months before it
        nov = [avg[mo] - avg[NB._shift_month(mo, -lag)] for mo in ends
               if (last_ix - (int(mo[:4]) * 12 + int(mo[5:7]) - 1)) % lag == 0]
        base = "history_range.m%d" % lag
        wins = "%d past %d-month windows" % (n, lag)
        tail = "%s; history, not a forecast" % span
        v2 = {k: _ni.sig2(x) for k, x in ((lo_k, lo_v), ("p50", p50), (hi_k, hi_v))}
        txt = {k: _fmt_item(x, kind, unit, sig=2) for k, x in v2.items()}
        of = "%s: the %%s of its changes over the %s%%s, %s" % (NB._cap(what), wins, tail)
        every = "one window a year, each ending in %s" % NB._mon(ms[-1])[:3] if lag == 12 else \
            "one window every %d months, the latest ending in %s" % (lag, NB._mon(ms[-1]))
        nv = {}
        if len(nov) >= 2:
            na = np.asarray(nov, dtype=float)
            nv = {"min": _ni.sig2(float(na.min())), "median": _ni.sig2(float(np.percentile(na, 50))),
                  "max": _ni.sig2(float(na.max()))}
        nvt = {k: _fmt_item(x, kind, unit, sig=2) for k, x in nv.items()}
        # one sentence within the writer's 400-character label cap (the longer one that also stated the non-overlapping
        # changes reached the report writer cut mid-sentence, "the 9 changes ran from"; they are items of their own:
        # .nonoverlap.n, .min, .median, .max)
        sentence = ("In the %s (%s; they overlap, one ending each month, so they are worth about %d independent "
                    "ones), the change in %s ran from %s (%s) to %s (%s); the middle was %s, and it rose in %d of the "
                    "%d. This is history, not a forecast." % (
                        wins, span, m_ind, what, txt[lo_k], "1 in 10 lower" if wide else "the lowest",
                        txt[hi_k], "1 in 10 higher" if wide else "the highest", txt["p50"], rose_k, n))
        lo_words = ("10th percentile", " (1 in 10 was lower)") if wide else ("lowest", "")
        hi_words = ("90th percentile", " (1 in 10 was higher)") if wide else ("highest", "")
        got = [
            _item(base + ".windows", "history_range", sentence, float(n), "count", "", None, cols, "history",
                  "count of %d-month windows" % lag, assumes=note),
            _item(base + ".n_eff", "history_range",
                  "About how many independent windows the %s are worth (they overlap: n / (1 + 2 x the changes' "
                  "autocorrelations up to lag %d)), %s" % (wins, lag - 1, tail), float(m_ind), "count", "", None,
                  cols, "history", "effective count of %d-month windows" % lag, assumes=note),
            _item(base + "." + lo_k, "history_range", of % lo_words, v2[lo_k], kind, unit, None, cols, "history",
                  "%s of %d-month changes" % (lo_k, lag), assumes=note),
            _item(base + ".p50", "history_range", of % ("middle (median)", ""), v2["p50"], kind, unit, None, cols,
                  "history", "p50 of %d-month changes" % lag, assumes=note),
            _item(base + "." + hi_k, "history_range", of % hi_words, v2[hi_k], kind, unit, None, cols, "history",
                  "%s of %d-month changes" % (hi_k, lag), assumes=note),
            _item(base + ".rose", "history_range",
                  "Of the %s (about %d independent), the windows in which %s rose, %s" % (wins, m_ind, what, tail),
                  float(rose_k), "count", "", None, cols, "history", "count of %d-month windows that rose" % lag,
                  assumes=note),
        ]
        if nv:
            nlab = "the %d non-overlapping %d-month changes (%s)" % (len(nov), lag, every)
            got += [
                _item(base + ".nonoverlap.n", "history_range", "The number of %s, %s" % (nlab, tail), float(len(nov)),
                      "count", "", None, cols, "history", "count of non-overlapping %d-month changes" % lag,
                      assumes=note),
                _item(base + ".nonoverlap.min", "history_range", "%s: the lowest of %s, %s" % (NB._cap(what), nlab,
                                                                                               tail),
                      nv["min"], kind, unit, None, cols, "history", "min of non-overlapping %d-month changes" % lag,
                      assumes=note),
                _item(base + ".nonoverlap.median", "history_range", "%s: the middle of %s, %s" % (NB._cap(what), nlab,
                                                                                                  tail),
                      nv["median"], kind, unit, None, cols, "history",
                      "median of non-overlapping %d-month changes" % lag, assumes=note),
                _item(base + ".nonoverlap.max", "history_range", "%s: the highest of %s, %s" % (NB._cap(what), nlab,
                                                                                                tail),
                      nv["max"], kind, unit, None, cols, "history", "max of non-overlapping %d-month changes" % lag,
                      assumes=note),
            ]
        for x in got:
            if x is not None and x["kind"] in ("change", "points"):
                x["text"] = _fmt_item(x["value"], x["kind"], x["unit"], sig=2)      # 2 significant figures (T3)
        items.extend(x for x in got if x is not None)
    return items, []


def _facts(frame: Any, date_col: str, months: Any) -> List[Dict[str, Any]]:
    """What the dated rows the engine kept span: months, distinct dates, the first and the last date."""
    import pandas as pd
    d = pd.to_datetime(frame[date_col], errors="coerce").dropna()
    if not len(d):
        return []
    days = d.dt.strftime("%Y-%m-%d")
    out = [
        _item("facts.months", "facts", "Months with a dated row the engine kept", float(months.dropna().nunique()),
              "count", "", None, [date_col], None, "count distinct months"),
        _item("facts.dates", "facts", "Distinct dates in the rows the engine kept", float(days.nunique()), "count", "",
              None, [date_col], None, "count distinct dates"),
        _item("facts.first", "facts", "The first date in the rows the engine kept", str(days.min()), "date", "", None,
              [date_col], None, "min"),
        _item("facts.last", "facts", "The last date in the rows the engine kept", str(days.max()), "date", "", None,
              [date_col], None, "max"),
    ]
    return [x for x in out if x is not None]


# --------------------------------------------------------------------------- the writer's table
def table(sc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """ONE table for the report writer, "Where the change in <measure> came from": a row per segment (at most 12),
    each cell the text of the item it shows; a level with rows in only one window is named so ("Uptown (new in
    the latest 12 months)"), and a column no row fills (the shares, when none is given) is left out. None when
    there are no contributions."""
    basis = (sc or {}).get("basis") or {}
    if basis.get("source") == "structure":
        return _structure_table(sc)
    segb = basis.get("segment") or {}
    col = segb.get("column")
    entered = [str(x) for x in segb.get("entered") or []]
    exited = [str(x) for x in segb.get("exited") or []]
    items = [it for it in (sc or {}).get("items") or [] if it.get("group") == "contribution"]
    if not col or not items:
        return None
    measure = basis.get("measure") or ""
    what = "rows" if basis.get("how") == "count" else "total %s" % measure
    head = next((it for it in (sc or {}).get("items") or [] if it.get("id") == "headline.change"), None)
    if head is not None:
        m = re.match(r"^Change in (.*), the latest 12 months against the 12 before$", head.get("label") or "")
        what = m.group(1) if m else what
    rows: List[List[str]] = []
    seen: List[str] = []
    by: Dict[Tuple[str, str], str] = {}
    for it in items:
        g = str(it.get("segment") or "")
        if g not in seen:
            seen.append(g)
        by[(g, str(it["id"]).rsplit(".", 1)[-1])] = it.get("text") or ""
    keys = ("prior", "latest", "change", "share", "pct")
    for g in seen[:12]:
        rows.append([_seg_name(g, entered, exited)] + [by.get((g, k), "") for k in keys])
    cols = [str(col), "12 months before", "Latest 12 months", "Change", "Share of the change", "Own change"]
    use = [0] + [j for j in range(1, len(cols)) if any(r[j] for r in rows)]
    return {"title": "Where the change in %s came from" % what, "cols": [cols[j] for j in use],
            "rows": [[r[j] for j in use] for r in rows]}


# --------------------------------------------------------------------------- a statistical table's breakdown
# WAVE 4, track A1 (plan/WAVE4-A-DESIGN.md section 4, "Scenario items"). A table read by its structure (nl_structure)
# is broken down by the structure, never by its raw rows: the headline is the slice's own series (Canada, all retail,
# total sales, unadjusted), and each verified partition of an additive dimension at the headline's root gives a
# breakdown (B1: the 13 provinces of Canada; B2: the 9 store types at the first level of NAICS), its parts at the
# headline's other members. Items: headline.{prior, latest, change, change_pct}; per part contribution.<dim>.<member>
# (its contribution to the change), growth.<dim>.<member> (its own change, when its 24 months are all published),
# share_level.<dim>.<member> (its share of the latest window) and share_change.<dim>.<member> (only when every part moved
# the way the total did); and contribution.<dim>.unallocated, the total less its parts with a value (the publisher's
# suppressed cells), so the parts and the unallocated add up to the change exactly. The slice's monthly values must equal
# the engine's charted series (the estimand's reconciliation) or the whole block is refused. Texts are the estimand's
# (nl_structure.money, "$864.0B", "+$10.2B"; nl_structure.pct, "+3.2%").
STRUCTURE_GRADE_WORDS = "descriptive arithmetic on published totals"
STRUCTURE_NOTE = ("Descriptive arithmetic on the table's own published series, in the engine's comparison windows (the "
                  "latest 12 months against the 12 before): the headline is the series the table's structure chooses, and "
                  "each breakdown's parts are that total's verified parts (each total checked against its parts). The "
                  "unallocated part is the total less the parts the publisher shows (its suppressed cells); the parts and "
                  "the unallocated add up to the change exactly. A contribution says where the change sits, not what "
                  "caused it.")
RATE_REFUSED = ("a %s is never added or averaged across members: each member's own %s is published, and the headline "
                "is the published aggregate, so there are no contributions")


def dim_key(header: str) -> str:
    """A dimension's short id part: an acronym in brackets ("NAICS" of "North American ... (NAICS)"), else its slug."""
    m = re.search(r"\(([A-Za-z][A-Za-z0-9]{1,11})\)\s*$", str(header))
    if m:
        return m.group(1).lower()
    return re.sub(r"[^a-z0-9]+", "_", str(header).lower()).strip("_")[:24] or "dim"


def member_keys(labels: List[str]) -> Dict[str, str]:
    """Each member's id part: its code when it carries one ("455", "44_45", "459a"), else its slug; unique."""
    import nl_structure as NST
    out: Dict[str, str] = {}
    used: set = set()
    for lb in labels:
        c = NST._label_code(lb)
        base = re.sub(r"[^a-z0-9]+", "_", (c or lb).lower()).strip("_")[:24] or "member"
        s, k = base, 1
        while s in used:
            k += 1
            s = "%s_%d" % (base, k)
        used.add(s)
        out[lb] = s
    return out


def build_structure(rep: Dict[str, Any], inner: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """rep["scenarios"] for a table read by its structure (basis.source "structure")."""
    import nl_structure as NST
    S, where = inner["S"], inner["where"]
    est = rep.get("estimand") or {}
    out = blank()
    refused: List[str] = out["refused"]
    win = {"prior": list(est["comparison"]["prior"]), "latest": list(est["comparison"]["latest"])}
    meas = NST.slice_type(S, where)
    S_local = dict(S, measure=dict(S["measure"], **meas))
    prim = (rep.get("primary_metric") or {}).get("finding_id")
    head = next((f for f in rep.get("findings") or [] if f.get("id") == prim and f.get("kind") == "business"), None) or \
        next((f for f in rep.get("findings") or [] if f.get("kind") == "business"
              and not str(f.get("id") or "").startswith("measure.volume")), None)
    grade = (head or {}).get("grade")
    official = bool(S.get("official"))
    col = str(inner.get("column") or S["measure"]["column"])
    basis = {"source": "structure", "finding_id": (head or {}).get("id"), "claim": (head or {}).get("claim"),
             "grade": grade, "grade_words": "the claim itself, graded %s" % grade if grade else None,
             "measure": col, "how": "total" if NST.sums_over_time(meas) else "average", "unit": meas.get("uom") or "",
             "windows": win, "slice": dict(where), "slice_id": inner.get("slice_id"), "breakdowns": [],
             "estimand": est.get("text"), "reconciles": est.get("reconciles") is not False}
    out["basis"] = basis
    if est.get("reconciles") is False:
        out["items"] = []
        refused.append(NOT_RECONCILED)
        out["note"] = NOTE_REFUSED
        return out
    items: List[Dict[str, Any]] = []
    fig = est.get("figures") or {}
    complete = est.get("complete") is not False
    Sp = {"period": est.get("period")} if est.get("period") else {}              # wave 5, gap 4: a quarterly or annual table's words
    P = est.get("period") or {"kind": "month", "noun": "month", "nouns": "months", "window": 12, "step": 1}
    k_used = int(est.get("periods_used") or est.get("months_used") or P["window"])
    # a flow's window lacking a period in either window is compared on the periods both have (nl_structure.matched_months)
    pw = NST.period_words(Sp, None if complete else k_used)
    ext, pri_w, lat_w = pw["against"], pw["prior"], pw["latest"]
    basis["complete"], basis["months_used"] = complete, k_used
    if int(P.get("step") or 1) != 1:
        basis["period"] = dict(P)                        # a quarterly or an annual table; a monthly table's basis is unchanged
    what = (est.get("text") or col).split(";")[0]

    def item(iid: str, group: str, label: str, value: Any, kind: str, text: str, grade_own: Optional[str],
             segment: Optional[str] = None, unit: str = "", window: Optional[str] = None, op: str = "",
             assumes: Optional[str] = None) -> Optional[Dict[str, Any]]:
        it = _item(iid, group, label, value, kind, unit, grade_own, [col], window, op, segment, assumes)
        if it is None:
            return None
        it["text"] = text
        if group not in ("headline", "forecast", "facts") and official:
            it["grade_words"] = "%s; part of a change graded %s, not graded itself" % (STRUCTURE_GRADE_WORDS, grade) \
                if grade else STRUCTURE_GRADE_WORDS
        return it
    for key, label, kind, unit, win_k, op in (
            ("prior", "%s, %s (%s to %s)" % (what, pri_w, NST.pkey(Sp, win["prior"][0]), NST.pkey(Sp, win["prior"][1])), "amount", "",
             "prior", ("sum of the slice's %s" if NST.sums_over_time(meas) else "mean of the slice's %s") % P["nouns"]),
            ("latest", "%s, %s (%s to %s)" % (what, lat_w, NST.pkey(Sp, win["latest"][0]), NST.pkey(Sp, win["latest"][1])), "amount", "",
             "latest", ("sum of the slice's %s" if NST.sums_over_time(meas) else "mean of the slice's %s") % P["nouns"]),
            ("change", "Change in %s, %s" % (what, ext), "change", "", "both", "latest less prior"),
            ("change_pct", "Change in %s in percent, %s" % (what, ext), "change", "%", "both", "latest / prior - 1")):
        f = fig.get(key) or {}
        it = item("headline.%s" % key, "headline", label, f.get("value"), kind, f.get("text") or "", grade,
                  unit=unit, window=win_k, op=op)
        if it is not None:
            items.append(it)
    if meas["type"] in ("rate", "index"):
        refused.append(RATE_REFUSED % (meas["type"], meas["type"]))
    else:
        chosen = [b for b in (plan or {}).get("breakdowns") or []] if plan else []
        bds = [b for b in S.get("breakdowns") or [] if not chosen or b["id"] in chosen][:NST.BREAKDOWNS_MAX]
        for bd in bds:
            res = NST.breakdown(S_local, bd, where, win)
            if res is None:
                refused.append("the breakdown by %s could not be formed in the comparison windows" % bd["dim"])
                continue
            if not res["reconciles"]:
                refused.append("the breakdown by %s does not add up to the change, so it is not shown" % bd["dim"])
                continue
            dk = dim_key(bd["dim"])
            mk = member_keys(bd["parts"])
            basis["breakdowns"].append({"id": bd["id"], "dim": bd["dim"], "key": dk, "parent": bd["parent"],
                                        **({"no_total": True} if bd.get("no_total") else {}),
                                        "parts": len(bd["parts"]), "depth": bd.get("depth", 1),
                                        "shares_given": res["shares_given"],
                                        "unallocated": {k: NST._r(v) for k, v in res["unallocated"].items()}})
            ref = res["latest"]
            for p in res["parts"]:
                m, seg = mk[p["member"]], p["member"]
                for iid, label, value, kind, text, unit, op in (
                        ("contribution.%s.%s" % (dk, m), "%s: contribution to the change in %s, %s" % (seg, what, ext),
                         p["contribution"], "change", NST.money(p["contribution"], S_local, signed=True, ref=None), "",
                         "the part's latest window less its window before"),
                        ("growth.%s.%s" % (dk, m), "%s: its own change in percent, %s" % (seg, ext),
                         p["growth_pct"], "change", NST.pct(p["growth_pct"]), "%", "latest / prior - 1"),
                        ("share_level.%s.%s" % (dk, m), "%s: share of %s in %s" % (seg, what, lat_w),
                         p["share_level_pct"], "percent", NST.pct(p["share_level_pct"], signed=False), "",
                         "the part's latest window / the total's"),
                        ("share_change.%s.%s" % (dk, m), "%s: share of the change in %s" % (seg, what),
                         p["share_change_pct"], "percent", NST.pct(p["share_change_pct"], signed=False), "",
                         "the part's contribution / the change")):
                    it = item(iid, "contribution", label, value, kind, text, grade, segment=seg, unit=unit,
                              window="both", op=op)
                    if it is not None:
                        items.append(it)
            u = res["unallocated"]
            # a table with no total row has nothing unallocated: its headline IS the sum of the parts
            # the cause of a gap, by the chart's own rule (the sum-check's suppressed_parts): suppressed cells when a part
            # lacks a month's value in either window, else the rounding of the published figures; the real residual is
            # printed (a gap too small for the headline's scale is written out, "$1,000", never "$0.0B")
            cause = "rounding" if res["suppressed_parts"] == 0 else "suppressed cells"
            it = None if bd.get("no_total") else item(
                "contribution.%s.unallocated" % dk, "contribution",
                "Unallocated within %s: %s less its published parts (%s), its change, %s"
                % (bd["dim"], bd["parent"], cause, ext), u["contribution"], "change",
                NST.money(u["contribution"], S_local, signed=True, ref=ref, exact_small=True), grade,
                segment="unallocated (%s)" % bd["dim"],
                window="both", op="the change less the parts' contributions",
                assumes="the total less the parts the publisher shows (%s): %s in the latest 12 months, %s before"
                        % (cause, NST.money(u["latest"], S_local, ref=ref, exact_small=True),
                           NST.money(u["prior"], S_local, ref=ref, exact_small=True)))
            if it is not None:
                items.append(it)
            if not res["shares_given"]:
                refused.append(MIXED_SHARES + " (%s)" % bd["dim"])
        if not S.get("breakdowns"):
            refused.append("no dimension of the table has a verified total at the headline's members, so there is no "
                           "breakdown")
    fc_items, fc_refused = _forecast_items(rep)
    items.extend(fc_items)
    refused.extend(fc_refused)
    months, vals = NST._monthly(S_local, where)
    have = [m for m, v in zip(months, vals) if v == v]
    if have:
        for it in (item("facts.months", "facts", "%s with a value in the headline series" % P["nouns"].capitalize(),
                        float(len(have)), "count", format(len(have), ","), None, op="count %s" % P["nouns"]),
                   item("facts.first", "facts", "The first %s of the headline series" % P["noun"], have[0], "date",
                        NST.pkey(Sp, have[0]), None, op="min"),
                   item("facts.last", "facts", "The last %s of the headline series" % P["noun"], have[-1], "date",
                        NST.pkey(Sp, have[-1]), None, op="max")):
            if it is not None:
                items.append(it)
    out["items"] = _ordered(items)
    out["note"] = STRUCTURE_NOTE.replace("the latest 12 months against the 12 before", pw["against"] if complete else
                                         NST.period_words(Sp)["against"])
    return out


def _structure_table(sc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The writer's table for a structure breakdown: the first breakdown's parts, each cell an item's own text."""
    basis = (sc or {}).get("basis") or {}
    bds = basis.get("breakdowns") or []
    if not bds:
        return None
    bd = bds[0]
    pre = ".%s." % bd["key"]
    by: Dict[Tuple[str, str], str] = {}
    seen: List[str] = []
    for it in (sc or {}).get("items") or []:
        iid = str(it.get("id") or "")
        grp = iid.split(".", 1)[0]
        if pre not in iid or grp not in ("contribution", "growth", "share_change", "share_level"):
            continue
        g = str(it.get("segment") or "")
        if g not in seen:
            seen.append(g)
        by[(g, grp)] = it.get("text") or ""
    rows = [[g] + [by.get((g, k), "") for k in ("contribution", "share_change", "growth", "share_level")]
            for g in seen[:12]]
    cols = [str(bd["dim"]), "Contribution to the change", "Share of the change", "Own change", "Share of the latest year"]
    use = [0] + [j for j in range(1, len(cols)) if any(r[j] for r in rows)]
    return {"title": _structure_table_title(basis), "cols": [cols[j] for j in use], "rows": [[r[j] for j in use] for r in rows]}


def _structure_table_title(basis: Dict[str, Any]) -> str:
    """"Where the change in Total retail sales (Canada · Retail trade [44-45] · Unadjusted) came from": the measure, then the
    rest of the slice the estimand names (the slice's other members, never the measure twice). The estimand's own words
    first read "Where the change in Canada · Retail trade [44-45] · Total retail sales · Unadjusted came from"."""
    measure = str(basis.get("measure") or "").strip()
    head = str(basis.get("estimand") or "").split(";")[0]
    rest = [x.strip() for x in head.split(" \u00b7 ") if x.strip() and x.strip() != measure]
    if not measure:
        return "Where the change in %s came from" % (head[:120] or "the total")
    return "Where the change in %s%s came from" % (measure[:60], (" (%s)" % " \u00b7 ".join(rest))[:110] if rest else "")
