"""The quarterly sales report from a CRM deals export (Track F of plan/DASHBOARD-DESIGN.md; CONTRACT-v2 section 5.18).

The visitor uploads the deals (opportunities) their CRM exports: Salesforce (Reports > Opportunities > Export), HubSpot
(Deals > Export), Pipedrive or Zoho (the deals list's export). The engine has already read, cleaned and checked the file;
this module only computes the deal metrics, from the rows the engine kept (downloads.clean_csv), never from anything else:

  * which column is which (`map_columns`): the CRM's own names read by the engine's rules (a header like "Close Date" or
    "Opportunity Owner", and what the values look like: dates, numbers, won/lost), the AI plan's reading of a column (its
    semantic type and its words) when the visitor used the AI, and the visitor's own choice in the report's card, which
    wins; a role nothing fits is left empty and the questions it answers are said to be unanswerable (`CANNOT`), never
    guessed;
  * the quarter against the one before (calendar quarters by default; the visitor may set the month the fiscal year
    starts): won revenue, the number of won deals and the average won deal, and the change split into the number of won
    deals and the average deal size by the one price-volume formula (`nl_scenarios.price_volume`, exact: the two parts add
    up to the change);
  * by segment: won revenue, won deals, win rate = won / (won + lost) of the deals closed in the quarter, the average won
    deal, and each segment's own deals/size split;
  * the sales cycle: days from created to close of the deals won in the quarter, median and mean, against the quarter
    before;
  * the pipeline on the export date: open value by stage, weighted by probability where the export has it, the age of the
    open deals, the stalled ones (past their expected close date, or no stage change for more than `stall_days`), and
    coverage of the next quarter's target when the visitor gives one;
  * the top 3 reps by won revenue (deals, win rate, average deal); names only when the visitor kept the owner column at
    the personal-data step, otherwise "Rep A", "Rep B", "Rep C";
  * what is working and what needs to change: the engine's own findings, ranked by the size of the move; each says what
    moved and by how much, and what the data shows about why (the deals/size split), and nothing more: every cause the
    data does not hold reads "driver to be confirmed".

Small cells (the engine's rule): no figure rests on fewer than 5 deals; such a figure is shown as "<5" and the notes say
so. Every number here is computed from the kept rows; every printed string is the engine's. Nothing is sent anywhere.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import nl_browser as NB
import nl_scenarios as NS

VERSION = "2026-10-17.1"
SMALL = 5                          # the engine's small-cell rule: a figure rests on 5 or more deals
STALL_DAYS = 30                    # an open deal with no stage change for longer is stalled (visitor's setting)
WIN_MOVE_PP = 5.0                  # a win-rate move worth a finding, in percentage points
CYCLE_MOVE = 0.10                  # a cycle-length move worth a finding (10% of the quarter before)
STALL_SHARE = 0.20                 # stalled open value worth a finding (20% of the open pipeline)
SEG_MOVE = 0.02                    # a segment revenue move worth a finding (2% of the quarter before's won revenue)
COVERAGE_OK = 3.0                  # pipeline coverage of a target the trade calls healthy (3x)
DRIVER = "driver to be confirmed"
MINUS = "−"

ROLES = ("id", "amount", "stage", "outcome", "segment", "owner", "created", "close", "probability", "stage_changed")
ROLE_LABEL = {
    "id": "Deal ID", "amount": "Amount", "stage": "Stage", "outcome": "Won / lost", "segment": "Segment",
    "owner": "Owner (rep)", "created": "Created date", "close": "Close date", "probability": "Probability",
    "stage_changed": "Last stage change"}
ROLE_KIND = {"id": "text", "amount": "number", "stage": "text", "outcome": "text", "segment": "text", "owner": "text",
             "created": "date", "close": "date", "probability": "number", "stage_changed": "date"}
REQUIRED = ("amount", "close")     # and won/lost, from an outcome or a stage column
# what the export should hold, for step 1's checklist and the report's "what this needs" (the page reads NEEDS)
NEEDS = [
    {"role": "id", "what": "Deal (opportunity) ID", "optional": True},
    {"role": "amount", "what": "Amount (the deal's value)", "optional": False},
    {"role": "stage", "what": "Stage (Prospecting ... Closed Won / Closed Lost)", "optional": False},
    {"role": "outcome", "what": "Won / lost (or closed-won / closed-lost stages)", "optional": False},
    {"role": "segment", "what": "Segment (e.g. Enterprise, Mid-Market, SMB)", "optional": True},
    {"role": "owner", "what": "Owner / rep", "optional": True},
    {"role": "created", "what": "Created date", "optional": True},
    {"role": "close", "what": "Close date (expected close for open deals)", "optional": False},
    {"role": "probability", "what": "Probability (%)", "optional": True},
    {"role": "stage_changed", "what": "Last stage change (or last activity) date", "optional": True},
]
# what cannot be answered without a role (said plainly, never guessed)
CANNOT = {
    "id": "No deal ID column: each row is counted as one deal, and repeated deals cannot be told apart.",
    "amount": "No amount column: no revenue, deal size, pipeline value or coverage can be computed.",
    "outcome": "No won/lost column or closed-won/closed-lost stages: won revenue, win rates and the pipeline cannot be "
               "told apart.",
    "stage": "No stage column: the open pipeline cannot be shown by stage.",
    "segment": "No segment column: revenue, deals and win rate by segment cannot be shown.",
    "owner": "No owner (rep) column: the top reps cannot be shown.",
    "created": "No created date: the sales cycle (days from created to close) and the age of open deals cannot be "
               "measured.",
    "close": "No close date: deals cannot be placed in a quarter, so no quarter can be compared.",
    "probability": "No probability column: the pipeline is shown unweighted.",
    "stage_changed": "No last-stage-change date: a deal is called stalled only when it is past its expected close date.",
}
OWNER_WITHHELD = ("The owner (rep) column was withheld at the personal-data step, so the top reps cannot be shown. To "
                  "see them, run the file again and choose Code (reps shown as Rep A, B, C) or Keep (names shown).")

_NAME_RULES = {
    "id": (r"\b(deal|opportunity|opp|record|lead)\s*(id|number|no|#|key)\b", r"^id$", r"\bdeal\s*id\b", r"\bid\b"),
    "amount": (r"\bamount\b", r"\bdeal (value|size|amount)\b", r"\bvalue\b", r"\brevenue\b", r"\b(acv|arr|tcv|mrr)\b",
               r"\bbooking", r"\bprice\b", r"\btotal\b"),
    "outcome": (r"\bis won\b", r"\bwon\b", r"\bstatus\b", r"\boutcome\b", r"\bresult\b"),
    "stage": (r"\b(deal |pipeline |sales )?stage\b", r"\bphase\b"),
    "segment": (r"\bsegment\b", r"\btier\b", r"\b(customer|account|company|client) (type|size|tier|class)\b",
                r"\bmarket\b", r"\bsize\b"),
    "owner": (r"\bowner\b", r"\b(sales )?rep\b", r"\bsales ?person\b", r"\baccount executive\b", r"\bassigned( to)?\b",
              r"\bae\b", r"\bseller\b", r"\bagent\b", r"\buser\b"),
    "created": (r"\bcreated?\b", r"\bcreation\b", r"\bopened\b", r"\bopen date\b", r"\bdate added\b", r"\badd time\b"),
    "close": (r"\bclose\b", r"\bclosed\b", r"\bclosing\b", r"\bwon (date|time)\b"),
    "probability": (r"\bprobab", r"\blikelihood\b", r"\bwin (%|chance|percent)", r"\bconfidence\b"),
    "stage_changed": (r"\bstage change", r"\blast stage\b", r"\blast activity\b", r"\blast (modified|updated)\b",
                      r"\bmodified\b", r"\bupdated\b", r"\bstage (changed|updated) (date|time|on)\b"),
}
_PLAN_TYPES = {"id": ("identifier", "code"), "amount": ("flow_amount", "level"), "probability": ("percentage",),
               "segment": ("category", "ordinal"), "stage": ("category", "ordinal"), "outcome": ("category", "boolean"),
               "owner": ("entity", "category"), "created": ("date",), "close": ("date",), "stage_changed": ("date",)}
_WON = re.compile(r"\bwon\b|\bwin\b|closed[\s_-]*won|closedwon", re.I)
_LOST = re.compile(r"\blost\b|\bloss\b|closed[\s_-]*lost|closedlost", re.I)
_TRUE = {"true", "yes", "y", "1", "t"}
_FALSE = {"false", "no", "n", "0", "f"}


# ----------------------------------------------------------------------------------------------- small helpers
def _words(s: Any) -> str:
    return " ".join(re.sub(r"[_\-./]+", " ", str(s or "")).lower().split())


def _amt(v: Any, cur: str) -> str:
    return NB._amt(v, cur)


def _chg(v: Any, cur: str) -> str:
    return NS._fmt_item(v, "change", cur)


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else ("%.1f%%" % v).replace("-", MINUS)


def _pct_chg(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    return ("+" if v > 0 else "") + ("%.1f%%" % v).replace("-", MINUS)


def _pp(v: Optional[float], long: bool = False) -> str:
    if v is None:
        return "n/a"
    s = ("+" if v > 0.05 else "") + ("%.1f" % v).replace("-", MINUS)
    if s in (MINUS + "0.0", "+0.0"):
        s = "0.0"
    return s + (" percentage points" if long else " pp")


def _days(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    r = round(float(v), 1)
    return ("%d" % r if r == int(r) else "%.1f" % r) + " days"


def _count(n: Any) -> str:
    return format(int(n), ",")


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return "%s %s" % (_count(n), one if n == 1 else (many or one + "s"))


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _r(v: Optional[float], k: int = 6) -> Optional[float]:
    return None if v is None else round(float(v), k)


# ----------------------------------------------------------------------------------------------- quarters
def quarter_of(d: dt.date, fs: int = 1) -> Tuple[int, int]:
    """(fiscal year, quarter) of a date, the fiscal year starting in month fs and named by the calendar year it ends in
    (fs = 1: the calendar year; fs = 4: FY2026 runs April 2025 to March 2026)."""
    m0 = (d.month - fs) % 12
    fy = d.year + (1 if fs != 1 and d.month >= fs else 0)
    return fy, m0 // 3 + 1


def quarter_start(fy: int, q: int, fs: int = 1) -> dt.date:
    m = fs + 3 * (q - 1)
    y = fy - (1 if fs != 1 else 0)
    while m > 12:
        m -= 12
        y += 1
    return dt.date(y, m, 1)


def quarter_end(fy: int, q: int, fs: int = 1) -> dt.date:
    n = next_quarter(fy, q)
    return quarter_start(n[0], n[1], fs) - dt.timedelta(days=1)


def next_quarter(fy: int, q: int) -> Tuple[int, int]:
    return (fy, q + 1) if q < 4 else (fy + 1, 1)


def prev_quarter(fy: int, q: int) -> Tuple[int, int]:
    return (fy, q - 1) if q > 1 else (fy - 1, 4)


def quarter_label(fy: int, q: int, fs: int = 1) -> str:
    return ("%d Q%d" if fs == 1 else "FY%d Q%d") % (fy, q)


def _parse_quarter(s: Any, fs: int) -> Optional[Tuple[int, int]]:
    m = re.match(r"^\s*(?:FY)?\s*(\d{4})\s*[- ]?\s*Q([1-4])\s*$", str(s or ""), re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ----------------------------------------------------------------------------------------------- reading the columns
def _kind_of(frame: Any, land: str) -> str:
    """number | date | text, as the kept rows hold the column (nothing else is read)."""
    import pandas as pd
    s = frame[land].astype(str).str.strip()
    s = s[(s != "") & (s.str.lower() != "nan")]
    if not len(s):
        return "empty"
    d = pd.to_datetime(s.str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
    if d.notna().mean() >= 0.9:
        return "date"
    n = NB._numbers_of(s)
    if n.notna().mean() >= 0.9:
        return "number"
    return "text"


def _levels(frame: Any, land: str, cap: int = 200) -> List[str]:
    s = frame[land].astype(str).str.strip()
    return [x for x in s[(s != "") & (s.str.lower() != "nan")].unique().tolist()[:cap]]


def _outcome_values(vals: List[str]) -> Dict[str, int]:
    out = {"won": 0, "lost": 0, "bool": 0}
    for v in vals:
        lv = v.lower()
        if _WON.search(lv):
            out["won"] += 1
        elif _LOST.search(lv):
            out["lost"] += 1
        elif lv in _TRUE or lv in _FALSE:
            out["bool"] += 1
    return out


def map_columns(frame: Any, header_of: Dict[str, str], plan_by: Optional[Dict[str, Dict[str, Any]]] = None,
                chosen: Optional[Dict[str, Any]] = None, allowed_owner: Optional[List[str]] = None
                ) -> Dict[str, Any]:
    """{role: {land, header, how}} for each role a column fits, plus {role: None} for the rest.

    how: "visitor" (the visitor chose it in the card; wins), "plan" (the AI plan's reading named it), "name" (the
    header reads as the role), "values" (the values do: won/lost, dates, numbers). A column serves one role, except a
    stage column whose values hold closed-won / closed-lost also gives the won/lost reading. `allowed_owner`: the
    flagged columns the visitor kept or coded (the only flagged columns that may be read, and only as the owner)."""
    plan_by = plan_by or {}
    chosen = chosen or {}
    cols = list(frame.columns)
    kinds = {c: _kind_of(frame, c) for c in cols}
    allowed_owner = list(allowed_owner or [])
    by_header = {}
    for c in cols:
        by_header[str(header_of.get(c, c))] = c
        by_header[c] = c
    out: Dict[str, Any] = {r: None for r in ROLES}
    taken: Dict[str, str] = {}

    def text_of(c: str) -> Tuple[str, str]:
        pe = plan_by.get(c) or {}
        return _words(header_of.get(c, c)), _words(" ".join(str(pe.get(k) or "") for k in ("label", "why")))

    def fits(role: str, c: str) -> bool:
        k = kinds.get(c)
        if c in allowed_owner and role != "owner":
            return False
        want = ROLE_KIND[role]
        if role == "probability":
            if k != "number":
                return False
            n = NB._numbers_of(frame[c].astype(str))
            n = n[n.notna()]
            return bool(len(n)) and float(n.min()) >= 0 and float(n.max()) <= 100
        if role in ("outcome", "stage", "segment", "owner", "id"):
            return k in ("text", "number") if role == "id" else (k == "text" or (role == "outcome" and k == "number"))
        return k == want

    # 1. the visitor's choices (a header or a landed name; "" or None for "no such column")
    for role in ROLES:
        if role not in chosen:
            continue
        v = chosen.get(role)
        if v in (None, "", "none"):
            out[role] = {"land": None, "header": None, "how": "visitor"}
            continue
        c = by_header.get(str(v))
        if c is not None and c not in taken and fits(role, c):
            out[role] = {"land": c, "header": str(header_of.get(c, c)), "how": "visitor"}
            taken[c] = role

    def score(role: str, c: str) -> Tuple[int, str]:
        h, p = text_of(c)
        pats = _NAME_RULES[role]
        s, how = 0, ""
        for i, pat in enumerate(pats):
            if re.search(pat, h):
                s = max(s, 6 - min(i, 3))
                how = "name"
                break
        pt = str((plan_by.get(c) or {}).get("semantic_type") or "")
        if p and any(re.search(pat, p) for pat in pats):
            s += 3
            how = how or "plan"
        if pt and pt in _PLAN_TYPES.get(role, ()):
            s += 1
            how = how or ("plan" if s >= 3 else "")
        return s, how

    def pick(role: str, extra: Optional[Dict[str, int]] = None, min_score: int = 3) -> None:
        if out[role] is not None:
            return
        best = None
        for i, c in enumerate(cols):
            if c in taken or not fits(role, c):
                continue
            s, how = score(role, c)
            if extra and c in extra:
                s += extra[c]
                how = how or "values"
            if s >= min_score and (best is None or s > best[0]):
                best = (s, i, c, how)
        if best is not None:
            c = best[2]
            out[role] = {"land": c, "header": str(header_of.get(c, c)), "how": best[3] or "name"}
            taken[c] = role

    # 2. dates: by name; two unnamed dates are not guessed
    for role in ("close", "created", "stage_changed"):
        pick(role)
    # 3. won/lost: a column whose values hold won and lost (an outcome or status), a boolean "is won", else the stage
    oc: Dict[str, int] = {}
    stage_vals: Dict[str, int] = {}
    for c in cols:
        if kinds.get(c) not in ("text", "number") or c in allowed_owner:
            continue
        vals = _levels(frame, c, 60)
        if not vals or len(vals) > 40:
            continue
        ov = _outcome_values(vals)
        if ov["won"] and ov["lost"] and len(vals) <= 4:
            oc[c] = 4                     # won / lost / open: an outcome (status) column
        elif ov["bool"] == len(vals) and re.search(r"\bwon\b", _words(header_of.get(c, c))):
            oc[c] = 4                     # "Is Won": true / false
        if ov["won"] and ov["lost"] and len(vals) > 2:
            stage_vals[c] = 3             # stages, among them closed-won and closed-lost
    pick("outcome", oc, min_score=4)
    pick("stage", stage_vals)
    if out["outcome"] is None and out["stage"] is not None and out["stage"]["land"] in stage_vals:
        out["outcome"] = {"land": out["stage"]["land"], "header": out["stage"]["header"], "how": "values",
                          "from_stage": True}
    # 4. the rest by name (and the plan's reading)
    pick("amount")
    pick("probability")
    pick("id")
    pick("segment")
    if allowed_owner:
        for c in allowed_owner:
            if c in cols and c not in taken and out["owner"] is None and score("owner", c)[0] >= 3:
                out["owner"] = {"land": c, "header": str(header_of.get(c, c)), "how": "name"}
                taken[c] = "owner"
    pick("owner")
    return {"roles": out, "kinds": {str(header_of.get(c, c)): kinds[c] for c in cols}}


# ----------------------------------------------------------------------------------------------- the deals
def _deals(frame: Any, roles: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """One dict per kept row: amount, outcome (won | lost | open | None), stage, segment, owner, created, close,
    probability (0 to 1), changed. `skipped`: counts of rows a part of the report could not read, by reason."""
    import pandas as pd
    n = len(frame)
    col = lambda r: (roles.get(r) or {}).get("land")  # noqa: E731

    def txt(r: str) -> List[Optional[str]]:
        c = col(r)
        if not c:
            return [None] * n
        s = frame[c].astype(str).str.strip()
        return [None if (v == "" or v.lower() == "nan") else v for v in s.tolist()]

    def dates(r: str) -> List[Optional[dt.date]]:
        c = col(r)
        if not c:
            return [None] * n
        d = pd.to_datetime(frame[c].astype(str).str.strip().str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
        return [None if pd.isna(x) else x.date() for x in d.tolist()]

    def nums(r: str) -> List[Optional[float]]:
        c = col(r)
        if not c:
            return [None] * n
        s = NB._numbers_of(frame[c].astype(str))
        return [None if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x) for x in s.tolist()]

    amount, prob = nums("amount"), nums("probability")
    if col("probability"):
        top = max([p for p in prob if p is not None] or [0.0])
        scale = 1.0 if top <= 1.0 else 100.0
        prob = [None if p is None else p / scale for p in prob]
    stage, seg, owner, ids = txt("stage"), txt("segment"), txt("owner"), txt("id")
    created, close, changed = dates("created"), dates("close"), dates("stage_changed")
    oc_land = col("outcome")
    oc_raw = txt("outcome")
    from_bool = False
    if oc_land:
        vals = [v.lower() for v in oc_raw if v]
        from_bool = bool(vals) and all(v in _TRUE or v in _FALSE for v in vals)
    out, skipped = [], {"no_outcome": 0, "no_amount": 0, "no_close": 0}
    for i in range(n):
        o = None
        v = oc_raw[i]
        if v is not None:
            lv = v.lower()
            if from_bool:
                o = "won" if lv in _TRUE else "closed_other"
            elif _WON.search(lv):
                o = "won"
            elif _LOST.search(lv):
                o = "lost"
            elif re.search(r"\bclosed?\b", lv):
                o = "closed_other"        # closed, neither won nor lost: left out of win rates, said in the notes
            else:
                o = "open"
        elif oc_land:
            skipped["no_outcome"] += 1
        if from_bool and o == "closed_other":
            # "Is Won" false: lost when the deal is closed (a stage saying closed, or a close date in the past is not
            # enough); with an "Is Closed"-less export a false is read with the stage when there is one
            st = (stage[i] or "").lower()
            o = "lost" if _LOST.search(st) or re.search(r"\bclosed?\b", st) else "open"
        if amount[i] is None:
            skipped["no_amount"] += 1
        if close[i] is None:
            skipped["no_close"] += 1
        out.append({"id": ids[i], "amount": amount[i], "outcome": o, "stage": stage[i], "segment": seg[i],
                    "owner": owner[i], "created": created[i], "close": close[i], "prob": prob[i],
                    "changed": changed[i]})
    return out, skipped


# ----------------------------------------------------------------------------------------------- the report
class Report:
    def __init__(self, deals: List[Dict[str, Any]], settings: Dict[str, Any], as_of: dt.date) -> None:
        self.deals = deals
        self.s = settings
        self.fs = int(settings.get("fiscal_start") or 1)
        self.cur = str(settings.get("currency") or "")
        self.as_of = as_of

    def q(self, d: Optional[dt.date]) -> Optional[Tuple[int, int]]:
        return quarter_of(d, self.fs) if d else None

    def closed_in(self, qk: Tuple[int, int], seg: Optional[str] = None, owner: Optional[str] = None
                  ) -> List[Dict[str, Any]]:
        return [d for d in self.deals if d["outcome"] in ("won", "lost") and d["close"] and self.q(d["close"]) == qk
                and (seg is None or d["segment"] == seg) and (owner is None or d["owner"] == owner)]

    @staticmethod
    def won_stats(closed: List[Dict[str, Any]]) -> Dict[str, Any]:
        won = [d for d in closed if d["outcome"] == "won" and d["amount"] is not None]
        lost = [d for d in closed if d["outcome"] == "lost"]
        R = math.fsum(d["amount"] for d in won)
        n = len(won)
        wr_n = len([d for d in closed if d["outcome"] == "won"]) + len(lost)
        wr = 100.0 * len([d for d in closed if d["outcome"] == "won"]) / wr_n if wr_n else None
        return {"revenue": R, "won": n, "lost": len(lost), "closed": wr_n, "avg": R / n if n else None, "win_rate": wr,
                "won_all": len([d for d in closed if d["outcome"] == "won"])}


def _cell(v: Optional[float], n: int, fmt: Any) -> str:
    """A figure's text, or "<5" when it rests on fewer than 5 deals (the engine's small-cell rule)."""
    if n < SMALL:
        return "<5"
    return "n/a" if v is None else fmt(v)


def build(frame: Any, header_of: Dict[str, str], as_of: Any, settings: Optional[Dict[str, Any]] = None,
          plan_by: Optional[Dict[str, Dict[str, Any]]] = None, private: Optional[Dict[str, str]] = None,
          viz: Any = None) -> Dict[str, Any]:
    """The quarterly sales report (CONTRACT-v2 5.18). frame: the kept rows (text cells, landed names), with the
    owner column only when the visitor kept or coded it; private: {landed: "keep" | "code" | "withhold"} of the
    flagged columns. Never raises."""
    try:
        return _build(frame, header_of, as_of, settings or {}, plan_by or {}, private or {}, viz)
    except Exception as exc:  # noqa: BLE001 - a plain refusal, never a traceback
        import os
        if os.environ.get("NL_BROWSER_STRICT"):
            raise
        return blank("the sales report could not be built for this file (%s)" % type(exc).__name__)


def blank(why: str) -> Dict[str, Any]:
    return {"version": VERSION, "ok": False, "why": why, "fit": {"ok": False, "why": why, "missing": []},
            "mapping": [], "cannot": [], "needs": NEEDS}


def _settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    s: Dict[str, Any] = {}
    try:
        fs = int(raw.get("fiscal_start") or 1)
    except (TypeError, ValueError):
        fs = 1
    s["fiscal_start"] = fs if 1 <= fs <= 12 else 1
    s["quarter"] = str(raw.get("quarter") or "")[:12]
    try:
        t = float(str(raw.get("target") or "").replace(",", "").replace("$", "").strip() or "nan")
        s["target"] = t if t > 0 and math.isfinite(t) else None
    except ValueError:
        s["target"] = None
    try:
        sd = int(raw.get("stall_days") or STALL_DAYS)
    except (TypeError, ValueError):
        sd = STALL_DAYS
    s["stall_days"] = min(365, max(7, sd))
    cur = str(raw.get("currency") or "").strip()[:4]
    s["currency"] = cur if re.match(r"^[$€£¥]?$|^[A-Z]{3}$", cur) else ""
    s["rep_names"] = raw.get("rep_names") is not False
    s["columns"] = {str(k): (None if v in (None, "") else str(v)[:120]) for k, v in dict(raw.get("columns") or {}).items()
                    if k in ROLES}
    return s


def _build(frame: Any, header_of: Dict[str, str], as_of: Any, raw: Dict[str, Any], plan_by: Dict[str, Any],
           private: Dict[str, str], viz: Any) -> Dict[str, Any]:
    st = _settings(raw)
    if frame is None or not len(frame):
        return blank("the engine kept no rows")
    try:
        AS_OF = dt.date.fromisoformat(str(as_of)[:10])
    except ValueError:
        AS_OF = dt.date.today()
    allowed_owner = [c for c, d in private.items() if d in ("keep", "code") and c in frame.columns]
    owner_withheld = any(d == "withhold" and re.search("|".join(_NAME_RULES["owner"]), _words(header_of.get(c, c)))
                         for c, d in private.items())
    mp = map_columns(frame, header_of, plan_by, st["columns"], allowed_owner)
    roles = mp["roles"]
    mapping = []
    for need in NEEDS:
        r = need["role"]
        m = roles.get(r)
        mapping.append({"role": r, "label": ROLE_LABEL[r], "what": need["what"], "optional": need["optional"],
                        "column": m["header"] if m else None, "how": (m or {}).get("how") or None,
                        "from_stage": bool(m and m.get("from_stage"))})
    cannot = []
    for r in ROLES:
        if roles.get(r) and roles[r].get("land"):
            continue
        if r == "owner" and owner_withheld:
            cannot.append({"role": r, "text": OWNER_WITHHELD})
        elif r == "stage" and roles.get("outcome") and roles["outcome"].get("land"):
            cannot.append({"role": r, "text": CANNOT[r]})
        else:
            cannot.append({"role": r, "text": CANNOT[r]})
    missing = [ROLE_LABEL[r] for r in REQUIRED if not (roles.get(r) and roles[r].get("land"))]
    if not (roles.get("outcome") and roles["outcome"].get("land")):
        missing.insert(1 if missing else 0, ROLE_LABEL["outcome"])
    rec: Dict[str, Any] = {"version": VERSION, "ok": False, "why": "", "as_of": AS_OF.isoformat(),
                           "settings": {k: v for k, v in st.items() if k != "columns"},
                           "chosen_columns": st["columns"], "mapping": mapping, "cannot": cannot, "needs": NEEDS,
                           "columns": [{"header": h, "kind": k} for h, k in mp["kinds"].items()],
                           "owner_decision": private.get((roles.get("owner") or {}).get("land") or "") or None,
                           "fit": {"ok": not missing, "missing": missing,
                                   "why": ("this file reads as a deals export: %s" % ", ".join(
                                       "%s = %s" % (m["label"], m["column"]) for m in mapping if m["column"]))
                                   if not missing else "needs %s" % ", ".join(missing)}}
    if missing:
        rec["why"] = "The sales report needs %s; this file has no column the engine could read as %s. Choose the " \
                     "columns in the card if the export names them differently." % (
                         ", ".join(missing), "it" if len(missing) == 1 else "them")
        return rec
    if not st["currency"]:
        u = str((plan_by.get(roles["amount"]["land"]) or {}).get("unit") or "").strip()
        st["currency"] = u if re.match(r"^[$\u20ac\u00a3\u00a5]$|^[A-Z]{3}$", u) else ""
        rec["settings"]["currency"] = st["currency"]
    deals, skipped = _deals(frame, roles)
    R = Report(deals, st, AS_OF)
    cur = R.cur
    closed = [d for d in deals if d["outcome"] in ("won", "lost") and d["close"]]
    if not closed:
        rec["why"] = "No deal in this file is closed won or closed lost with a close date, so no quarter can be reported."
        return rec
    # the quarters with closed deals; complete when the export date is after the quarter's last day
    qs = sorted({R.q(d["close"]) for d in closed})
    complete = [qk for qk in qs if quarter_end(qk[0], qk[1], R.fs) < AS_OF]
    rec["quarters"] = [{"label": quarter_label(qk[0], qk[1], R.fs), "complete": qk in complete} for qk in qs][::-1]
    asked = _parse_quarter(st["quarter"], R.fs) if st["quarter"] else None
    if asked and asked in qs:
        Q1 = asked
    else:
        Q1 = complete[-1] if complete else qs[-1]
    Q0 = prev_quarter(*Q1)
    L1, L0 = quarter_label(Q1[0], Q1[1], R.fs), quarter_label(Q0[0], Q0[1], R.fs)
    s1, e1 = quarter_start(Q1[0], Q1[1], R.fs), quarter_end(Q1[0], Q1[1], R.fs)
    s0, e0 = quarter_start(Q0[0], Q0[1], R.fs), quarter_end(Q0[0], Q0[1], R.fs)
    partial = Q1 not in complete
    rec["quarter"] = {"label": L1, "start": s1.isoformat(), "end": e1.isoformat(), "prev_label": L0,
                      "prev_start": s0.isoformat(), "prev_end": e0.isoformat(), "complete": not partial,
                      "fiscal_start": R.fs,
                      "note": ("%s is not over on the export date (%s): its figures are to date." % (L1, AS_OF.isoformat()))
                      if partial else ""}
    if asked and asked not in qs:
        rec["quarter"]["note"] = ("No deal closed in %s, so the report shows %s." % (st["quarter"], L1)
                                  + (" " + rec["quarter"]["note"] if rec["quarter"]["note"] else ""))
    c1, c0 = R.closed_in(Q1), R.closed_in(Q0)
    W1, W0 = R.won_stats(c1), R.won_stats(c0)
    rec["counts"] = {"rows": len(deals), "closed_q": len(c1), "closed_prev": len(c0),
                     "open": len([d for d in deals if d["outcome"] == "open"]),
                     "closed_other": len([d for d in deals if d["outcome"] == "closed_other"]),
                     "no_amount": skipped["no_amount"], "no_close": skipped["no_close"],
                     "no_outcome": skipped["no_outcome"]}
    if roles.get("id") and roles["id"].get("land"):
        ids = [d["id"] for d in deals if d["id"]]
        rec["counts"]["repeated_ids"] = len(ids) - len(set(ids))

    # ---- the headline: won revenue, deals, average deal; the change split into deals and size
    growth = _growth(W0, W1, cur, L0, L1)
    rec["growth"] = growth
    rec["kpis"] = _kpis(R, W0, W1, c0, c1, Q0, Q1, roles, cur)
    # ---- by segment
    rec["segments"] = _segments(R, Q0, Q1, roles, cur, L0, L1)
    # ---- the sales cycle
    rec["cycle"] = _cycle(R, Q0, Q1, roles, L0, L1)
    # ---- the pipeline on the export date
    rec["pipeline"] = _pipeline(R, Q1, roles, cur, st)
    # ---- the top reps
    rec["reps"] = _reps(R, Q1, roles, cur, private, st, owner_withheld, L1)
    # ---- what is working, what needs to change
    rec["findings"] = _findings(rec, cur, L0, L1)
    rec["summary"] = _summary(rec, cur, L0, L1)
    rec["notes"] = _notes(rec, roles, skipped, AS_OF)
    rec["charts"] = _charts(rec, viz, cur, L0, L1)
    rec["title"] = "Quarterly sales report: %s against %s" % (L1, L0)
    rec["ok"] = True
    return rec


def _growth(W0: Dict[str, Any], W1: Dict[str, Any], cur: str, L0: str, L1: str) -> Dict[str, Any]:
    R0, R1, n0, n1 = W0["revenue"], W1["revenue"], W0["won"], W1["won"]
    g: Dict[str, Any] = {"prev": _r(R0, 2), "cur": _r(R1, 2), "change": _r(R1 - R0, 2),
                         "pct": _r(100.0 * (R1 - R0) / R0, 4) if R0 > 0 else None, "deals_prev": n0, "deals_cur": n1,
                         "avg_prev": _r(W0["avg"], 2), "avg_cur": _r(W1["avg"], 2), "count_effect": None,
                         "size_effect": None, "adds_up": False, "suppressed": n0 < SMALL or n1 < SMALL, "text": "",
                         "lead": ""}
    if g["suppressed"]:
        g["text"] = ("Fewer than 5 deals were won in %s, so its revenue is not shown (the engine's small-cell rule)."
                     % (L0 if n0 < SMALL else L1))
        return g
    size, count = NS.price_volume(R0, n0, R1, n1)
    g["count_effect"], g["size_effect"] = _r(count, 2), _r(size, 2)
    g["adds_up"] = abs(size + count - (R1 - R0)) <= 1e-6 * max(1.0, abs(R1), abs(R0))
    up = R1 >= R0
    lead = "more deals" if abs(count) >= abs(size) else "bigger deals"
    if (count >= 0) != (size >= 0):
        lead = ("bigger deals" if size > 0 else "more deals") if up else ("smaller deals" if size < 0 else "fewer deals")
    elif not up:
        lead = "fewer deals" if abs(count) >= abs(size) else "smaller deals"
    g["lead"] = lead
    g["text"] = ("Won revenue %s from %s in %s to %s in %s (%s, %s). %s won deals in %s (%s in %s), at an average of "
                 "%s (%s in %s): the number of deals accounts for %s and the average deal size for %s, which add up "
                 "to the change. The %s came from %s." % (
                     "rose" if R1 > R0 else "fell" if R1 < R0 else "held", _amt(R0, cur), L0, _amt(R1, cur), L1,
                     _chg(R1 - R0, cur), _pct_chg(g["pct"]), _count(n1), L1, _count(n0), L0, _amt(W1["avg"], cur),
                     _amt(W0["avg"], cur), L0, _chg(count, cur), _chg(size, cur),
                     "growth" if up else "fall", lead))
    return g


def _kpis(R: Report, W0: Dict[str, Any], W1: Dict[str, Any], c0: List[Dict[str, Any]], c1: List[Dict[str, Any]],
          Q0: Tuple[int, int], Q1: Tuple[int, int], roles: Dict[str, Any], cur: str) -> List[Dict[str, Any]]:
    ks = []

    def k(kid: str, label: str, v1: Optional[float], v0: Optional[float], n1: int, n0: int, fmt: Any, chg: Any) -> None:
        sup = n1 < SMALL
        ks.append({"id": kid, "label": label, "value": None if sup else _r(v1, 4), "prev": None if n0 < SMALL else _r(v0, 4),
                   "text": _cell(v1, n1, fmt), "prev_text": _cell(v0, n0, fmt),
                   "change_text": chg(v1 - v0) if (v1 is not None and v0 is not None and n1 >= SMALL and n0 >= SMALL) else "",
                   "n": n1, "suppressed": sup})
    k("revenue", "Won revenue", W1["revenue"], W0["revenue"], W1["won"], W0["won"], lambda v: _amt(v, cur),
      lambda d: _chg(d, cur))
    k("deals", "Deals won", W1["won"], W0["won"], W1["won"], W0["won"], _count, lambda d: ("+" if d > 0 else "") + _count(d).replace("-", MINUS))
    k("avg", "Average won deal", W1["avg"], W0["avg"], W1["won"], W0["won"], lambda v: _amt(v, cur), lambda d: _chg(d, cur))
    k("win_rate", "Win rate", W1["win_rate"], W0["win_rate"], W1["closed"], W0["closed"], _pct, _pp)
    if roles.get("created") and roles["created"].get("land"):
        d1 = _cycle_days(R, c1)
        d0 = _cycle_days(R, c0)
        k("cycle", "Median sales cycle", _median(d1), _median(d0), len(d1), len(d0), _days,
          lambda d: ("+" if d > 0 else "") + _days(d).replace("-", MINUS))
    return ks


def _cycle_days(R: Report, closed: List[Dict[str, Any]]) -> List[float]:
    return [float((d["close"] - d["created"]).days) for d in closed
            if d["outcome"] == "won" and d["created"] and d["close"] and d["close"] >= d["created"]]


def _segments(R: Report, Q0: Tuple[int, int], Q1: Tuple[int, int], roles: Dict[str, Any], cur: str, L0: str,
              L1: str) -> Dict[str, Any]:
    if not (roles.get("segment") and roles["segment"].get("land")):
        return {"ok": False, "why": CANNOT["segment"], "rows": []}
    segs = sorted({d["segment"] for d in R.deals if d["segment"] and d["outcome"] in ("won", "lost")
                   and d["close"] and R.q(d["close"]) in (Q0, Q1)})
    if len(segs) > 12:
        return {"ok": False, "why": "The segment column has %d values in these two quarters; the report shows 12 at "
                                    "most. Choose a column with fewer values." % len(segs), "rows": []}
    rows = []
    for sg in segs:
        a, b = R.won_stats(R.closed_in(Q0, seg=sg)), R.won_stats(R.closed_in(Q1, seg=sg))
        ok = a["won"] >= SMALL and b["won"] >= SMALL
        size = count = None
        if ok:
            size, count = NS.price_volume(a["revenue"], a["won"], b["revenue"], b["won"])
        rows.append({"label": sg, "rev_prev": _r(a["revenue"], 2) if a["won"] >= SMALL else None,
                     "rev_cur": _r(b["revenue"], 2) if b["won"] >= SMALL else None,
                     "change": _r(b["revenue"] - a["revenue"], 2) if ok else None,
                     "won_prev": a["won"], "won_cur": b["won"], "closed_prev": a["closed"], "closed_cur": b["closed"],
                     "win_prev": _r(a["win_rate"], 4) if a["closed"] >= SMALL else None,
                     "win_cur": _r(b["win_rate"], 4) if b["closed"] >= SMALL else None,
                     "avg_prev": _r(a["avg"], 2) if a["won"] >= SMALL else None,
                     "avg_cur": _r(b["avg"], 2) if b["won"] >= SMALL else None,
                     "count_effect": _r(count, 2), "size_effect": _r(size, 2),
                     "texts": {"rev_prev": _cell(a["revenue"], a["won"], lambda v: _amt(v, cur)),
                               "rev_cur": _cell(b["revenue"], b["won"], lambda v: _amt(v, cur)),
                               "change": _chg(b["revenue"] - a["revenue"], cur) if ok else "",
                               "won_prev": _cell(a["won"], a["won"], _count), "won_cur": _cell(b["won"], b["won"], _count),
                               "win_prev": _cell(a["win_rate"], a["closed"], _pct), "win_cur": _cell(b["win_rate"], b["closed"], _pct),
                               "win_change": _pp(b["win_rate"] - a["win_rate"]) if (a["closed"] >= SMALL and b["closed"] >= SMALL
                                                                                    and a["win_rate"] is not None and b["win_rate"] is not None) else "",
                               "avg_prev": _cell(a["avg"], a["won"], lambda v: _amt(v, cur)),
                               "avg_cur": _cell(b["avg"], b["won"], lambda v: _amt(v, cur))}})
    rows.sort(key=lambda r: (-(r["rev_cur"] or 0), r["label"]))
    hidden = sum(1 for r in rows if "<5" in r["texts"].values())
    for r in rows:
        r["texts"]["closed_prev"] = _cell(r["closed_prev"], r["closed_prev"], _count)
        r["texts"]["closed_cur"] = _cell(r["closed_cur"], r["closed_cur"], _count)
    sh = roles["segment"]["header"]
    table = {"cols": [sh, "Revenue %s" % L0, "Revenue %s" % L1, "Change", "Won %s" % L0, "Won %s" % L1,
                      "Avg deal %s" % L0, "Avg deal %s" % L1],
             "rows": [[r["label"], r["texts"]["rev_prev"], r["texts"]["rev_cur"], r["texts"]["change"],
                       r["texts"]["won_prev"], r["texts"]["won_cur"], r["texts"]["avg_prev"], r["texts"]["avg_cur"]]
                      for r in rows]}
    win_table = {"cols": [sh, "Closed %s" % L0, "Win rate %s" % L0, "Closed %s" % L1, "Win rate %s" % L1, "Change"],
                 "rows": [[r["label"], r["texts"]["closed_prev"], r["texts"]["win_prev"], r["texts"]["closed_cur"],
                           r["texts"]["win_cur"], r["texts"]["win_change"]] for r in rows]}
    return {"ok": True, "column": sh, "rows": rows, "table": table, "win_table": win_table,
            "note": ("%s rest on fewer than 5 deals and are shown as “<5” (the engine's small-cell rule)."
                     % ("Some figures" if hidden else "")) if hidden else "",
            "why": ""}


def _cycle(R: Report, Q0: Tuple[int, int], Q1: Tuple[int, int], roles: Dict[str, Any], L0: str, L1: str
           ) -> Dict[str, Any]:
    if not (roles.get("created") and roles["created"].get("land")):
        return {"ok": False, "why": CANNOT["created"]}
    c0, c1 = R.closed_in(Q0), R.closed_in(Q1)
    d0, d1 = _cycle_days(R, c0), _cycle_days(R, c1)
    bad = len([d for d in c0 + c1 if d["outcome"] == "won" and d["created"] and d["close"] and d["close"] < d["created"]])
    out: Dict[str, Any] = {"ok": True, "n_prev": len(d0), "n_cur": len(d1), "bad_dates": bad,
                           "median_prev": _median(d0) if len(d0) >= SMALL else None,
                           "median_cur": _median(d1) if len(d1) >= SMALL else None,
                           "mean_prev": _r(math.fsum(d0) / len(d0), 2) if len(d0) >= SMALL else None,
                           "mean_cur": _r(math.fsum(d1) / len(d1), 2) if len(d1) >= SMALL else None, "segments": []}
    if roles.get("segment") and roles["segment"].get("land"):
        for sg in sorted({d["segment"] for d in c0 + c1 if d["segment"]}):
            a = _cycle_days(R, R.closed_in(Q0, seg=sg))
            b = _cycle_days(R, R.closed_in(Q1, seg=sg))
            out["segments"].append({"label": sg, "median_prev": _median(a) if len(a) >= SMALL else None,
                                    "median_cur": _median(b) if len(b) >= SMALL else None, "n_prev": len(a),
                                    "n_cur": len(b), "texts": {"prev": _cell(_median(a), len(a), _days),
                                                               "cur": _cell(_median(b), len(b), _days)}})
    mp, mc = out["median_prev"], out["median_cur"]
    if mp is not None and mc is not None:
        out["change"] = mc - mp
        out["text"] = ("The median sales cycle of won deals (days from created to close) was %s in %s against %s in "
                       "%s (%s); the mean was %s against %s." % (
                           _days(mc), L1, _days(mp), L0, ("+" if mc > mp else "") + _days(mc - mp).replace("-", MINUS),
                           _days(out["mean_cur"]), _days(out["mean_prev"])))
    else:
        out["change"] = None
        out["text"] = "Fewer than 5 won deals with both dates in a quarter: the cycle is not shown (the small-cell rule)."
    if bad:
        out["text"] += " %s with a close date before the created date %s left out." % (
            _plural(bad, "won deal"), "is" if bad == 1 else "are")
    return out


def _pipeline(R: Report, Q1: Tuple[int, int], roles: Dict[str, Any], cur: str, st: Dict[str, Any]) -> Dict[str, Any]:
    opened = [d for d in R.deals if d["outcome"] == "open"]
    have_amt = [d for d in opened if d["amount"] is not None]
    out: Dict[str, Any] = {"ok": bool(opened), "as_of": R.as_of.isoformat(), "n": len(opened),
                           "value": _r(math.fsum(d["amount"] for d in have_amt), 2) if len(have_amt) >= SMALL else None,
                           "weighted": None, "stages": [], "stalled": None, "age": None, "coverage": None}
    if not opened:
        out["why"] = "No deal in the file is open (neither won nor lost), so there is no pipeline on the export date."
        return out
    if len(have_amt) < SMALL:
        out["why"] = "Fewer than 5 open deals have an amount: the pipeline is not shown (the small-cell rule)."
        out["ok"] = False
        return out
    has_prob = bool(roles.get("probability") and roles["probability"].get("land"))
    if has_prob:
        wp = [d for d in have_amt if d["prob"] is not None]
        out["weighted"] = _r(math.fsum(d["amount"] * d["prob"] for d in wp), 2)
        out["weighted_n"] = len(wp)
    # by stage, in the order of their average probability (else by name)
    stages: Dict[str, List[Dict[str, Any]]] = {}
    for d in have_amt:
        stages.setdefault(d["stage"] or "(no stage)", []).append(d)

    def order(item: Tuple[str, List[Dict[str, Any]]]) -> Tuple[float, str]:
        ps = [d["prob"] for d in item[1] if d["prob"] is not None]
        return (math.fsum(ps) / len(ps) if ps else 0.0, item[0])
    for name, ds in sorted(stages.items(), key=order):
        v = math.fsum(d["amount"] for d in ds)
        w = math.fsum(d["amount"] * d["prob"] for d in ds if d["prob"] is not None) if has_prob else None
        ages = [float((R.as_of - d["created"]).days) for d in ds if d["created"]]
        n = len(ds)
        out["stages"].append({"label": name, "n": n, "value": _r(v, 2) if n >= SMALL else None,
                              "weighted": _r(w, 2) if (w is not None and n >= SMALL) else None,
                              "median_age": _median(ages) if len(ages) >= SMALL else None,
                              "texts": {"n": _cell(n, n, _count), "value": _cell(v, n, lambda x: _amt(x, cur)),
                                        "weighted": _cell(w, n, lambda x: _amt(x, cur)) if has_prob else "",
                                        "age": _cell(_median(ages), len(ages), _days) if ages else "n/a"}})
    ages = [float((R.as_of - d["created"]).days) for d in have_amt if d["created"]]
    if len(ages) >= SMALL:
        out["age"] = {"median": _median(ages), "mean": _r(math.fsum(ages) / len(ages), 2), "n": len(ages)}
    # stalled: past the expected close date, or (with the column) no stage change for longer than stall_days
    sd = st["stall_days"]
    past = [d for d in have_amt if d["close"] and d["close"] < R.as_of]
    quiet = [d for d in have_amt if d["changed"] and (R.as_of - d["changed"]).days > sd and d not in past]
    stalled = past + quiet
    sv = math.fsum(d["amount"] for d in stalled)
    out["stalled"] = {"n": len(stalled), "past_close": len(past), "no_change": len(quiet),
                      "value": _r(sv, 2) if len(stalled) >= SMALL else None,
                      "share": _r(100.0 * sv / out["value"], 4) if (len(stalled) >= SMALL and out["value"]) else None,
                      "days": sd, "by_change": bool(roles.get("stage_changed") and roles["stage_changed"].get("land"))}
    # the next quarter's pipeline and coverage of a target
    nq = next_quarter(*Q1)
    while quarter_end(nq[0], nq[1], R.fs) < R.as_of:
        nq = next_quarter(*nq)
    nlab = quarter_label(nq[0], nq[1], R.fs)
    inq = [d for d in have_amt if d["close"] and R.q(d["close"]) == nq]
    nv = math.fsum(d["amount"] for d in inq)
    nw = math.fsum(d["amount"] * d["prob"] for d in inq if d["prob"] is not None) if has_prob else None
    out["next"] = {"label": nlab, "n": len(inq), "value": _r(nv, 2) if len(inq) >= SMALL else None,
                   "weighted": _r(nw, 2) if (nw is not None and len(inq) >= SMALL) else None}
    if st.get("target") and len(inq) >= SMALL:
        t = float(st["target"])
        out["coverage"] = {"target": t, "label": nlab, "x": _r(nv / t, 4), "weighted_x": _r(nw / t, 4) if nw is not None else None}
    table = {"cols": ["Stage", "Open deals", "Value"] + (["Weighted value"] if has_prob else []) + ["Median age"],
             "rows": [[s["label"], s["texts"]["n"], s["texts"]["value"]] + ([s["texts"]["weighted"]] if has_prob else [])
                      + [s["texts"]["age"]] for s in out["stages"]]}
    out["table"] = table
    st_ = out["stalled"]
    cvv = out["coverage"]
    out["texts"] = {"value": _amt(out["value"], cur), "n": _count(len(opened)),
                    "weighted": _amt(out["weighted"], cur) if out["weighted"] is not None else "",
                    "stalled_share": _pct(st_["share"]) if st_["share"] is not None else "",
                    "stalled_n": _count(st_["n"]),
                    "coverage": ("%.1fx" % cvv["x"]) if cvv else "",
                    "coverage_weighted": ("%.1fx" % cvv["weighted_x"]) if (cvv and cvv["weighted_x"] is not None) else ""}
    out["text"] = ("On the export date (%s), %s were open, worth %s%s. %s %s past %s expected close date%s; together "
                   "%s (%s of the open value)." % (
                       R.as_of.isoformat(), _plural(len(opened), "deal"), _amt(out["value"], cur),
                       (" (%s weighted by probability)" % _amt(out["weighted"], cur)) if out["weighted"] is not None else "",
                       _plural(st_["past_close"], "deal"), "is" if st_["past_close"] == 1 else "are",
                       "its" if st_["past_close"] == 1 else "their",
                       (" and %s had no stage change for more than %d days" % (_plural(st_["no_change"], "other"), sd))
                       if st_["by_change"] else "",
                       _amt(st_["value"], cur) if st_["value"] is not None else "<5",
                       _pct(st_["share"]) if st_["share"] is not None else "n/a"))
    if out["coverage"]:
        cv = out["coverage"]
        out["text"] += (" Against the %s target of %s, the open deals expected to close in %s cover %.1fx%s." % (
            nlab, _amt(cv["target"], cur), nlab, cv["x"],
            (" (%.1fx weighted)" % cv["weighted_x"]) if cv["weighted_x"] is not None else ""))
    elif out["next"]["value"] is not None:
        out["text"] += (" %s expected to close in %s: %s%s. Give a target in the card to see coverage." % (
            _plural(out["next"]["n"], "open deal is", "open deals are"), nlab, _amt(out["next"]["value"], cur),
            (" (%s weighted)" % _amt(out["next"]["weighted"], cur)) if out["next"]["weighted"] is not None else ""))
    return out


def _reps(R: Report, Q1: Tuple[int, int], roles: Dict[str, Any], cur: str, private: Dict[str, str],
          st: Dict[str, Any], owner_withheld: bool, L1: str) -> Dict[str, Any]:
    o = roles.get("owner")
    if not (o and o.get("land")):
        return {"ok": False, "why": OWNER_WITHHELD if owner_withheld else CANNOT["owner"], "rows": []}
    land = o["land"]
    decision = private.get(land)
    names_ok = decision != "code" and st["rep_names"]
    if decision is None and NB._looks_like_names(sorted({d["owner"] for d in R.deals if d["owner"]})[:2000],
                                                 o["header"]):
        names_ok = False              # a column of people's names the scan did not flag is never printed
    reps = sorted({d["owner"] for d in R.deals if d["owner"]})
    stats = []
    for rp in reps:
        w = R.won_stats(R.closed_in(Q1, owner=rp))
        if w["won"] >= SMALL:
            stats.append((rp, w))
    stats.sort(key=lambda x: (-x[1]["revenue"], x[0]))
    total = math.fsum(d["amount"] for d in R.closed_in(Q1) if d["outcome"] == "won" and d["amount"] is not None)
    rows = []
    for i, (rp, w) in enumerate(stats[:3]):
        label = rp if names_ok else "Rep %s" % "ABC"[i]
        rows.append({"rank": i + 1, "label": label, "revenue": _r(w["revenue"], 2), "won": w["won"],
                     "win_rate": _r(w["win_rate"], 4) if w["closed"] >= SMALL else None, "avg": _r(w["avg"], 2),
                     "share": _r(100.0 * w["revenue"] / total, 4) if total else None,
                     "texts": {"revenue": _amt(w["revenue"], cur), "won": _count(w["won"]),
                               "win_rate": _cell(w["win_rate"], w["closed"], _pct), "avg": _amt(w["avg"], cur),
                               "share": _pct(100.0 * w["revenue"] / total) if total else "n/a"}})
    left = len([rp for rp in reps if 0 < R.won_stats(R.closed_in(Q1, owner=rp))["won"] < SMALL])
    return {"ok": bool(rows), "names": names_ok, "column": o["header"], "rows": rows, "ranked": len(stats),
            "left_out": left,
            "why": "" if rows else "No rep won 5 or more deals in %s, so none is ranked (the small-cell rule)." % L1,
            "note": ("Reps are shown by name because you kept the %s column." % o["header"]) if names_ok else
                    ("Reps are shown as Rep A, B and C (ranked by won revenue): %s." % (
                        "the owner column was coded at the personal-data step" if decision == "code" else
                        "names are hidden in this report")),
            "small_note": ("%s with fewer than 5 won deals in %s %s not ranked." % (
                _plural(left, "rep"), L1, "is" if left == 1 else "are")) if left else "",
            "table": {"cols": ["Rank", "Rep", "Won revenue", "Share", "Deals won", "Win rate", "Average deal"],
                      "rows": [[str(r["rank"]), r["label"], r["texts"]["revenue"], r["texts"]["share"], r["texts"]["won"],
                                r["texts"]["win_rate"], r["texts"]["avg"]] for r in rows]}}


# ----------------------------------------------------------------------------------------------- findings
def _findings(rec: Dict[str, Any], cur: str, L0: str, L1: str) -> Dict[str, Any]:
    """What is working and what needs to change: the engine's own findings, each scored by the size of its move (a
    revenue change as a share of the quarter before's won revenue; a win rate in points over 100; a cycle's change
    over its length, halved; stalled value over the open value, halved), ranked within its side. The text says what
    moved, by how much, and what the data shows about why; a cause the data does not hold is "driver to be
    confirmed"."""
    items = []
    R0 = (rec.get("growth") or {}).get("prev") or 0.0
    seg = rec.get("segments") or {}
    for r in seg.get("rows") or []:
        if r["change"] is None or not R0:
            continue
        if abs(r["change"]) < SEG_MOVE * R0:
            r = dict(r, change=None)          # too small a move to rank; its win rate may still be one
        c = r["change"]
        if c is None:
            items.extend(_win_item(r, L0, L1))
            continue
        size, count = r["size_effect"] or 0.0, r["count_effect"] or 0.0
        why = ("the data shows it came from %s: the average won deal went from %s to %s (deal size %s) and won deals "
               "from %s to %s (deal count %s); why the deals %s: %s" % (
                   ("bigger deals" if size > 0 else "smaller deals") if abs(size) >= abs(count) else
                   ("more deals" if count > 0 else "fewer deals"), r["texts"]["avg_prev"], r["texts"]["avg_cur"],
                   _chg(size, cur), r["texts"]["won_prev"], r["texts"]["won_cur"], _chg(count, cur),
                   ("changed size" if abs(size) >= abs(count) else "changed in number"), DRIVER))
        items.append({"side": "working" if c > 0 else "change", "kind": "segment_revenue", "subject": r["label"],
                      "score": abs(c) / R0,
                      "title": "%s won revenue %s (%s to %s)" % (r["label"], _chg(c, cur), r["texts"]["rev_prev"],
                                                                     r["texts"]["rev_cur"]),
                      "text": "%s won revenue %s from %s in %s to %s in %s (%s); %s." % (
                          r["label"], "rose" if c > 0 else "fell", r["texts"]["rev_prev"], L0, r["texts"]["rev_cur"], L1,
                          _chg(c, cur), why), "driver": DRIVER})
        items.extend(_win_item(r, L0, L1))
    cy = rec.get("cycle") or {}
    if cy.get("ok") and cy.get("change") is not None and cy.get("median_prev"):
        d = cy["change"]
        if abs(d) >= CYCLE_MOVE * cy["median_prev"]:
            items.append({"side": "change" if d > 0 else "working", "kind": "cycle", "subject": "sales cycle",
                          "score": 0.5 * abs(d) / cy["median_prev"],
                          "title": "Sales cycle %s %s" % ("longer by" if d > 0 else "shorter by", _days(abs(d))),
                          "text": "Won deals took a median of %s from created to close in %s, against %s in %s; why: "
                                  "%s." % (_days(cy["median_cur"]), L1, _days(cy["median_prev"]), L0, DRIVER),
                          "driver": DRIVER})
    pl = rec.get("pipeline") or {}
    stl = pl.get("stalled") or {}
    if stl.get("share") is not None and stl["share"] >= 100.0 * STALL_SHARE:
        items.append({"side": "change", "kind": "stalled", "subject": "pipeline",
                      "score": 0.5 * stl["share"] / 100.0,
                      "title": "%s of the open pipeline is stalled" % _pct(stl["share"]),
                      "text": "%s of the open pipeline's value (%s) sits in %s that %s past %s expected close date%s; "
                              "why they stalled: %s." % (
                                  _pct(stl["share"]), _amt(stl["value"], cur), _plural(stl["n"], "deal"),
                                  "is" if stl["n"] == 1 else "are", "its" if stl["n"] == 1 else "their",
                                  (" or had no stage change for more than %d days" % stl["days"]) if stl["by_change"] else "",
                                  DRIVER), "driver": DRIVER})
    cv = pl.get("coverage")
    if cv:
        x = cv["x"]
        items.append({"side": "working" if x >= COVERAGE_OK else "change", "kind": "coverage", "subject": "coverage",
                      "score": 0.3 * abs(x - COVERAGE_OK) / COVERAGE_OK,
                      "title": "Pipeline covers %.1fx of the %s target" % (x, cv["label"]),
                      "text": "The open deals expected to close in %s cover %.1fx the target of %s (3x is the usual "
                              "rule of thumb)." % (cv["label"], x, _amt(cv["target"], cur)), "driver": ""})
    out = {}
    for side in ("working", "change"):
        xs = sorted([i for i in items if i["side"] == side], key=lambda i: (-i["score"], i["title"]))[:4]
        for k, i in enumerate(xs):
            i["rank"] = k + 1
            i["score"] = _r(i["score"], 6)
        out[side] = xs
    return out


def _win_item(r: Dict[str, Any], L0: str, L1: str) -> List[Dict[str, Any]]:
    """A segment's win-rate move of WIN_MOVE_PP points or more, as a finding."""
    wp, wc = r["win_prev"], r["win_cur"]
    if wp is None or wc is None or abs(wc - wp) < WIN_MOVE_PP:
        return []
    d = wc - wp
    return [{"side": "working" if d > 0 else "change", "kind": "segment_win_rate", "subject": r["label"],
             "score": abs(d) / 100.0,
             "title": "%s win rate %s (%s to %s)" % (r["label"], _pp(d), r["texts"]["win_prev"], r["texts"]["win_cur"]),
             "text": "%s won %s of the deals it closed in %s, against %s in %s (%s; %s won of %s closed, against %s "
                     "of %s); why: %s." % (r["label"], _pct(wc), L1, _pct(wp), L0, _pp(d, True), _count(r["won_cur"]),
                                          _count(r["closed_cur"]), _count(r["won_prev"]), _count(r["closed_prev"]),
                                          DRIVER), "driver": DRIVER}]


def _summary(rec: Dict[str, Any], cur: str, L0: str, L1: str) -> Dict[str, Any]:
    """The one-page executive summary: the headline, then a line each for segments, the cycle, the pipeline and the
    top rep, then what is working and what needs to change (the top 2 each)."""
    g = rec["growth"]
    lines = []
    head = g["text"].split(". ")[0] + "." if g["text"] else ""
    if g.get("lead") and not g["suppressed"]:
        head = ("Won revenue was %s in %s, %s on %s (%s, %s). The %s came from %s: %s won deals (%s in %s) at an "
                "average of %s (%s); the average deal size accounts for %s and the number of deals for %s." % (
                    _amt(g["cur"], cur), L1, "up" if g["change"] > 0 else "down" if g["change"] < 0 else "level",
                    L0, _chg(g["change"], cur), _pct_chg(g["pct"]), "growth" if g["change"] >= 0 else "fall",
                    g["lead"], _count(g["deals_cur"]), _count(g["deals_prev"]), L0, _amt(g["avg_cur"], cur),
                    _amt(g["avg_prev"], cur), _chg(g["size_effect"], cur), _chg(g["count_effect"], cur)))
    seg = rec.get("segments") or {}
    if seg.get("ok") and seg["rows"]:
        moved = [r for r in seg["rows"] if r["change"] is not None]
        if moved:
            up = max(moved, key=lambda r: (r["change"], r["label"]))
            how = ""
            if up["size_effect"] is not None and up["count_effect"] is not None:
                how = (", from bigger deals (average %s to %s)" % (up["texts"]["avg_prev"], up["texts"]["avg_cur"])
                       if up["size_effect"] > 0 and abs(up["size_effect"]) >= abs(up["count_effect"]) else
                       ", from more deals (%s to %s won)" % (up["texts"]["won_prev"], up["texts"]["won_cur"])
                       if up["count_effect"] > 0 and abs(up["count_effect"]) > abs(up["size_effect"]) else "")
            lines.append("By segment, %s grew most (%s to %s, %s)%s%s." % (
                up["label"], up["texts"]["rev_prev"], up["texts"]["rev_cur"], up["texts"]["change"], how,
                "".join("; %s win rate %s to %s" % (r["label"], r["texts"]["win_prev"], r["texts"]["win_cur"])
                        for r in sorted(seg["rows"], key=lambda r: r["label"]) if r["texts"]["win_change"])))
    cy = rec.get("cycle") or {}
    if cy.get("ok") and cy.get("change") is not None:
        lines.append("Sales cycle: median %s in %s against %s in %s." % (_days(cy["median_cur"]), L1,
                                                                          _days(cy["median_prev"]), L0))
    pl = rec.get("pipeline") or {}
    if pl.get("ok") and pl.get("value") is not None:
        stl = pl.get("stalled") or {}
        lines.append("Pipeline on %s: %s open, %s%s; %s stalled (%s of its value)." % (
            pl["as_of"], _plural(pl["n"], "deal"), _amt(pl["value"], cur),
            (" (%s weighted)" % _amt(pl["weighted"], cur)) if pl.get("weighted") is not None else "",
            _plural(stl.get("n") or 0, "deal"), _pct(stl.get("share")) if stl.get("share") is not None else "n/a"))
    rp = rec.get("reps") or {}
    if rp.get("ok") and rp["rows"]:
        lines.append("Top reps by won revenue in %s: %s." % (L1, "; ".join(
            "%s %s (%s deals, win rate %s)" % (r["label"], r["texts"]["revenue"], r["texts"]["won"], r["texts"]["win_rate"])
            for r in rp["rows"])))
    f = rec.get("findings") or {}
    return {"headline": head, "lines": lines,
            "working": [i["title"] for i in (f.get("working") or [])[:3]],
            "change": [i["title"] for i in (f.get("change") or [])[:3]]}


def _notes(rec: Dict[str, Any], roles: Dict[str, Any], skipped: Dict[str, int], as_of: dt.date) -> List[str]:
    q = rec["quarter"]
    n = rec["counts"]
    out = ["Quarters are %s; %s runs %s to %s and %s runs %s to %s. A deal is in the quarter its close date falls in." % (
        "calendar quarters" if q["fiscal_start"] == 1 else "fiscal quarters, the year starting in %s" % dt.date(
            2000, q["fiscal_start"], 1).strftime("%B"), q["label"], q["start"], q["end"], q["prev_label"],
        q["prev_start"], q["prev_end"]),
        "Won revenue is the sum of the amounts of the deals won in the quarter. The win rate is won / (won + lost) of "
        "the deals closed in the quarter; open deals are not in it.",
        "The change in won revenue is split into the number of won deals (at the quarter before's average deal) and "
        "the average deal size (at this quarter's number of deals); the two parts add up to the change exactly.",
        "The pipeline is the open deals (neither won nor lost) on the export date, %s." % as_of.isoformat(),
        "No figure rests on fewer than 5 deals: such a figure is shown as “<5”."]
    if q.get("note"):
        out.insert(0, q["note"])
    if n.get("closed_other"):
        out.append("%s closed neither won nor lost (a closed stage without won or lost in it) %s left out of the win "
                   "rates." % (_plural(n["closed_other"], "deal"), "is" if n["closed_other"] == 1 else "are"))
    if n.get("no_amount"):
        out.append("%s no amount: %s counted in the win rates but not in revenue or pipeline value." % (
            _plural(n["no_amount"], "deal has", "deals have"), "it is" if n["no_amount"] == 1 else "they are"))
    if n.get("no_close"):
        out.append("%s no close date and cannot be placed in a quarter." % _plural(n["no_close"], "deal has",
                                                                                     "deals have"))
    if n.get("repeated_ids"):
        out.append("%s share a deal ID with another row; each row is counted as it is in the export." % _plural(
            n["repeated_ids"], "row"))
    return out


# ----------------------------------------------------------------------------------------------- charts
def _charts(rec: Dict[str, Any], viz: Any, cur: str, L0: str, L1: str) -> List[Dict[str, Any]]:
    """The report's charts as chart registry records (CONTRACT 5.9), drawn by the page's window.NLV like every other
    chart and rendered to PNG for the Word file: the deals/size waterfall, won revenue and win rate by segment (two
    quarters side by side), the median cycle by segment, and the open pipeline by stage."""
    if viz is None:
        try:
            import nl_viz as viz  # type: ignore[no-redef]
        except ImportError:
            return []
    out: List[Dict[str, Any]] = []
    q = rec["quarter"]
    months = [q["prev_start"][:7], q["end"][:7]]
    g = rec["growth"]
    if g.get("adds_up") and g["count_effect"] is not None:
        R0, R1, cnt, size = g["prev"], g["cur"], g["count_effect"], g["size_effect"]
        steps = [{"label": "Won revenue %s" % L0, "value": R0, "text": _amt(R0, cur), "kind": "total", "from": 0, "to": R0},
                 {"label": "Number of won deals", "value": cnt, "text": _chg(cnt, cur), "kind": "step", "from": R0,
                  "to": _r(R0 + cnt, 2)},
                 {"label": "Average deal size", "value": size, "text": _chg(size, cur), "kind": "step",
                  "from": _r(R0 + cnt, 2), "to": R1},
                 {"label": "Won revenue %s" % L1, "value": R1, "text": _amt(R1, cur), "kind": "total", "from": 0, "to": R1}]
        r = viz._record(
            "pvm_waterfall", "Won revenue, %s to %s: number of deals and deal size" % (L0, L1),
            "%s won deals in %s, %s in %s; the average won deal %s, then %s" % (
                _count(g["deals_prev"]), L0, _count(g["deals_cur"]), L1, _amt(g["avg_prev"], cur), _amt(g["avg_cur"], cur)),
            "drove", "The change in won revenue split into the number of won deals and the average deal size", [],
            None, None, {"label": "Won revenue", "unit": cur, "kind": "amount"},
            {"steps": steps, "basis": {"split": "price_volume_mix", "finding_id": None, "column": None,
                                       "prior": [q["prev_start"][:7], q["prev_end"][:7]],
                                       "latest": [q["start"][:7], q["end"][:7]]},
             "change": {"value": g["change"], "text": _chg(g["change"], cur)}},
            {"cols": ["Step", "Won revenue"], "rows": [[s["label"], s["text"]] for s in steps]},
            "Won revenue went from %s to %s (%s): the number of won deals accounts for %s and the average deal size "
            "for %s." % (_amt(R0, cur), _amt(R1, cur), _chg(g["change"], cur), _chg(cnt, cur), _chg(size, cur)),
            (0, ""), "The deals won in each quarter, by close date",
            {"columns": [], "rows": g["deals_prev"] + g["deals_cur"], "months": months,
             "op": "won revenue = deals x average deal; price-volume split"})
        out.append(r)
    seg = rec.get("segments") or {}
    rows = [x for x in seg.get("rows") or []]
    if seg.get("ok") and len(rows) >= 2:
        def gb(title: str, sub: str, vals: Any, texts: Any, chg: Any, mlabel: str, unit: str, kind: str,
               summary: str, tcol: str) -> Optional[Dict[str, Any]]:
            data_rows = []
            for x in rows:
                v = vals(x)
                if v[0] is None and v[1] is None:
                    continue
                data_rows.append({"label": x["label"][:80], "values": [_r(v[0] or 0, 4), _r(v[1] or 0, 4)],
                                  "texts": [t[:24] for t in texts(x)], "change_text": (chg(x) or "n/a")[:24]})
            if len(data_rows) < 2:
                return None
            return viz._record(
                "grouped_bar", title, sub, "other", title, [], None, None,
                {"label": mlabel, "unit": unit, "kind": kind},
                {"series": [L0, L1], "rows": data_rows, "other": False},
                {"cols": [seg["column"], "%s %s" % (tcol, L0), "%s %s" % (tcol, L1), "Change"],
                 "rows": [[d["label"]] + d["texts"] + [d["change_text"]] for d in data_rows]},
                summary, (sum(1 for d in data_rows if "<5" in d["texts"]), "A figure on fewer than 5 deals is shown as <5"
                          if any("<5" in d["texts"] for d in data_rows) else ""),
                "The deals closed in each quarter, by %s" % seg["column"],
                {"columns": [seg["column"]], "rows": rec["counts"]["closed_q"] + rec["counts"]["closed_prev"],
                 "months": months, "op": tcol.lower()})
        top = max(rows, key=lambda x: (abs(x["change"] or 0), x["label"]))
        r1 = gb("Won revenue by %s, %s against %s" % (seg["column"], L1, L0),
                "The amounts of the deals won in each quarter", lambda x: (x["rev_prev"], x["rev_cur"]),
                lambda x: (x["texts"]["rev_prev"], x["texts"]["rev_cur"]), lambda x: x["texts"]["change"],
                "Won revenue", cur, "amount",
                "%s moved most: %s to %s (%s)." % (top["label"], top["texts"]["rev_prev"], top["texts"]["rev_cur"],
                                                  top["texts"]["change"] or "n/a"), "Won revenue")
        if r1:
            out.append(r1)
        wr = [x for x in rows if x["texts"]["win_change"]]
        lo = min(wr, key=lambda x: (x["win_cur"] - x["win_prev"], x["label"])) if wr else None
        r2 = gb("Win rate by %s, %s against %s" % (seg["column"], L1, L0),
                "Won / (won + lost) of the deals closed in each quarter", lambda x: (x["win_prev"], x["win_cur"]),
                lambda x: (x["texts"]["win_prev"], x["texts"]["win_cur"]), lambda x: x["texts"]["win_change"],
                "Win rate", "%", "average",
                ("The largest fall is %s: %s to %s (%s)." % (lo["label"], lo["texts"]["win_prev"], lo["texts"]["win_cur"],
                                                              lo["texts"]["win_change"])) if lo and lo["win_cur"] < lo["win_prev"]
                else "Win rates by segment in the two quarters.", "Win rate")
        if r2:
            out.append(r2)
    cy = rec.get("cycle") or {}
    if cy.get("ok") and len(cy.get("segments") or []) >= 2 and cy.get("change") is not None and seg.get("ok"):
        crow = [x for x in cy["segments"] if x["median_prev"] is not None or x["median_cur"] is not None]
        if len(crow) >= 2:
            dr = [{"label": x["label"][:80], "values": [_r(x["median_prev"] or 0, 2), _r(x["median_cur"] or 0, 2)],
                   "texts": [x["texts"]["prev"][:24], x["texts"]["cur"][:24]],
                   "change_text": (("+" if x["median_cur"] > x["median_prev"] else "") + _days(x["median_cur"] - x["median_prev"]).replace("-", MINUS))[:24]
                   if (x["median_prev"] is not None and x["median_cur"] is not None) else "n/a"} for x in crow]
            out.append(viz._record(
                "grouped_bar", "Median sales cycle by %s, %s against %s" % (seg["column"], L1, L0),
                "Days from created to close of the deals won in each quarter", "other",
                "The sales cycle by segment", [], None, None, {"label": "Median days to close", "unit": "days",
                                                               "kind": "average"},
                {"series": [L0, L1], "rows": dr, "other": False},
                {"cols": [seg["column"], "Median days %s" % L0, "Median days %s" % L1, "Change"],
                 "rows": [[d["label"]] + d["texts"] + [d["change_text"]] for d in dr]},
                cy["text"].split("; the mean")[0] + ".", (0, ""), "The deals won in each quarter",
                {"columns": [seg["column"]], "rows": cy["n_prev"] + cy["n_cur"], "months": months,
                 "op": "median days from created to close"}))
    pl = rec.get("pipeline") or {}
    sts = [x for x in pl.get("stages") or [] if x["value"] is not None]
    if pl.get("ok") and len(sts) >= 2:
        dr = [{"label": x["label"][:80], "values": [_r(x["value"], 2)], "texts": [x["texts"]["value"][:24]]} for x in sts]
        out.append(viz._record(
            "bar_chart", "Open pipeline by stage on %s" % pl["as_of"],
            "The amounts of the open deals%s" % ("; weighted by probability: %s" % _amt(pl["weighted"], cur)
                                                 if pl.get("weighted") is not None else ""),
            "other", "The open pipeline by stage", [], None, None, {"label": "Open value", "unit": cur, "kind": "amount"},
            {"series": ["Open value"], "rows": dr, "other": False},
            {"cols": pl["table"]["cols"], "rows": pl["table"]["rows"]},
            pl["text"].split(". ")[0] + ".", (0, ""), "The open deals on the export date",
            {"columns": [], "rows": pl["n"], "months": None, "op": "open value by stage"}))
    for i, r in enumerate(out):
        r["id"] = "viz.8%d.%s" % (i, r["chart"])
        r["chosen_by"] = "engine"
        r["why"] = "Chosen by the engine: the quarterly sales report's own chart."
    return out
