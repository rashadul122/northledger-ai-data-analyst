"""
nl_geo: places on a map (18 Oct 2026). A table whose members are US places (metro and micro areas by their Census CBSA code or
their name, US states by name, postal abbreviation or FIPS code) gets a map: each place where it is, coloured by the measure, a
period at a time, on one legend for every period so the colours compare across years.

    import nl_geo
    rec = nl_geo.map_record(members, codes, years, values, measure, unit, rate, entity_noun)

geo_us.json (beside this file, in the pack) holds the places already projected for the drawer's 975 x 610 map (d3 geoAlbersUsa,
scale 1300, translate [487.5, 305], the us-atlas states-albers layout): metro and micro areas {code: [name, x, y, type]} and
states {fips: [name, abbreviation, x, y]}. tools/geo/make_geo.mjs makes it from the Census 2024 Gazetteer and us-atlas 3.0.1.
The drawer is src/js/54-nl-geo.js (the page) and the share viewer's copy (insight-proxy/src/share.js).
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "2026-10-18.1"
PLACED_MIN = 0.6          # a map when at least this share of the members are placed, and at least PLACED_MIN_N of them
PLACED_MIN_N = 8
ITEMS_MAX = 1000
PERIODS_MAX = 60
_GEO: Dict[str, Any] = {}


def _geo() -> Dict[str, Any]:
    if not _GEO:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "geo_us.json"), encoding="utf-8") as fh:
                _GEO.update(json.load(fh))
        except (OSError, ValueError):
            _GEO.update({"cbsa": {}, "states": {}})
        # name keys: "abilene, tx"; and each principal city with its state ("poughkeepsie|ny" for "Kiryas Joel-Poughkeepsie-Newburgh, NY")
        full, city = {}, {}
        for code, v in (_GEO.get("cbsa") or {}).items():
            name = str(v[0])
            full.setdefault(_key(name), code)
            m = re.match(r"^(.*),\s*([A-Z]{2}(?:-[A-Z]{2})*)$", name)
            if m:
                for c in m.group(1).split("-"):
                    for st in m.group(2).split("-"):
                        city.setdefault("%s|%s" % (_key(c), st.lower()), []).append(code)
        _GEO["_full"], _GEO["_city"] = full, city
        st_key = {}
        for fips, v in (_GEO.get("states") or {}).items():
            st_key[_key(v[0])] = fips
            if v[1]:
                st_key[v[1].lower()] = fips
            st_key[fips] = fips
            st_key[str(int(fips))] = fips
        _GEO["_state"] = st_key
    return _GEO


def _key(s: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.'’]", "", str(s or "").strip().lower()))


def _metro(name: str, code: Optional[str]) -> Optional[Tuple[str, float, float]]:
    """(code, x, y) of a metro or micro area: by its CBSA code, else its full name, else one of its principal cities with its state."""
    g = _geo()
    cb = g.get("cbsa") or {}
    c = re.sub(r"\.0$", "", str(code or "").strip())
    if c in cb:
        return c, cb[c][1], cb[c][2]
    k = _key(name)
    k = re.sub(r"\s+(metro|micro|metropolitan|micropolitan)( statistical)? area$", "", k)
    if k in g["_full"]:
        c = g["_full"][k]
        return c, cb[c][1], cb[c][2]
    m = re.match(r"^(.*),\s*([a-z]{2}(?:-[a-z]{2})*)$", k)
    if m:
        hits: List[str] = []
        for part in m.group(1).split("-"):
            for st in m.group(2).split("-"):
                hits += g["_city"].get("%s|%s" % (part.strip(), st), [])
        hits = list(dict.fromkeys(hits))
        if hits:
            # the metro (type 1) before a micro area, then the first named
            hits.sort(key=lambda h: (cb[h][3] != 1, 0))
            return hits[0], cb[hits[0]][1], cb[hits[0]][2]
    return None


def _state(name: str) -> Optional[str]:
    k = _key(name)
    return _geo()["_state"].get(k) or _geo()["_state"].get(re.sub(r"^state of ", "", k))


def place(members: Sequence[str], codes: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """{"mode": "points" | "areas", "at": {member: (x, y) or fips}, "missing": [...]} when most members are US places, else None.
    States are areas (each state filled); metro and micro areas are points."""
    ms = [str(m) for m in members if str(m).strip()]
    if len(ms) < PLACED_MIN_N:
        return None
    st = {m: _state(m) for m in ms}
    n_st = sum(1 for v in st.values() if v)
    if n_st >= PLACED_MIN * len(ms) and n_st >= PLACED_MIN_N:
        return {"mode": "areas", "at": {m: v for m, v in st.items() if v}, "missing": [m for m, v in st.items() if not v]}
    at: Dict[str, Any] = {}
    for m in ms:
        hit = _metro(m, (codes or {}).get(m))
        if hit:
            at[m] = (hit[1], hit[2])
    if len(at) >= PLACED_MIN * len(ms) and len(at) >= PLACED_MIN_N:
        return {"mode": "points", "at": at, "missing": [m for m in ms if m not in at]}
    return None


def _q(vals: List[float], f: float) -> float:
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(f * (len(s) - 1)))))
    return s[i]


def _nice(v: float) -> float:
    a = abs(v)
    if a == 0:
        return 0.0
    d = 10 ** (math.floor(math.log10(a)) - 1)
    return float("%.6g" % (round(v / d) * d))


def direction(label: str) -> str:
    """Which end of the scale is worse, from the measure's own words: unemployment, poverty, deaths, crime ... higher is worse;
    income, employment, life expectancy ... higher is better; anything else is neutral (one colour, darker is higher)."""
    t = str(label or "").lower()
    if re.search(r"unemploy|poverty|death|mortalit|crime|homicide|fatal|injur|accident|inflation|debt|default|delinquen|vacan|"
                 r"evict|overdose|emission|pollut|co2|smoke|obes|infection|cases|uninsured|homeless|loss|losses|cost of", t):
        return "higher_worse"
    if re.search(r"income|wage|salary|earning|employment rate|participation|life expect|literacy|graduat|gdp|growth|"
                 r"insured\b|access|surviv|satisf", t):
        return "higher_better"
    return "neutral"


def map_record(members: Sequence[str], codes: Optional[Dict[str, str]], periods: Sequence[str],
               values: Dict[str, List[Optional[float]]], measure: str, unit: str, rate: bool, entity: str,
               fmt: Any = None) -> Optional[Dict[str, Any]]:
    """The map's record (kind "geo", chart "geo_map") or None when the members are not US places.

    values: {member: [value per period]} (None where the member has none). The legend's five classes are cut once, over every
    period's values, so a colour means the same in 2002 and in 2026; the drawer shows the latest period first."""
    import nl_viz as _nv
    pl = place(members, codes)
    if pl is None or not periods:
        return None
    periods = [str(p) for p in periods][-PERIODS_MAX:]
    k0 = len(periods)
    items = []
    for m, at in pl["at"].items():
        vs = list(values.get(m) or [])[-k0:]
        vs = [None if (v is None or (isinstance(v, float) and math.isnan(v))) else
              (round(float(v), 2) if abs(float(v)) < 1000 else float(round(float(v)))) for v in vs]
        if not any(v is not None for v in vs):
            continue
        it = {"label": str(m)[:80], "v": vs}
        if pl["mode"] == "points":
            it["x"], it["y"] = float(at[0]), float(at[1])
        else:
            it["id"] = str(at)
        items.append(it)
    if len(items) < PLACED_MIN_N:
        return None
    items = items[:ITEMS_MAX]
    allv = [v for it in items for v in it["v"] if v is not None]
    cuts = [_nice(_q(allv, f)) for f in (0.2, 0.4, 0.6, 0.8)]
    cuts = sorted(dict.fromkeys(cuts))
    if len(cuts) < 2:
        return None
    last = len(periods) - 1
    lv = sorted(((it["v"][last], it["label"]) for it in items if it["v"][last] is not None), reverse=True)
    f = fmt or (lambda v: ("%.1f" % v) if abs(v) < 100 else format(round(v), ","))
    noun = entity.lower()
    plural = noun if noun.endswith("s") else (noun[:-1] + "ies" if noun.endswith("y") and not noun.endswith("ey") else noun + "s")
    when = ("In %s, " % periods[last]) if periods[last] else ""
    summary = ("%s%s was highest in %s (%s%s) and lowest in %s (%s%s), of %d %s on the map." % (
        when, (measure[:1].lower() if when else measure[:1].upper()) + measure[1:], lv[0][1], f(lv[0][0]), unit if unit == "%" else "",
        lv[-1][1], f(lv[-1][0]), unit if unit == "%" else "", len(lv), plural)) if len(lv) >= 2 else ""
    data = {"base": "us", "mode": pl["mode"], "periods": periods, "at": last, "items": items, "cuts": cuts,
            "lo": min(allv), "hi": max(allv), "dir": direction(measure), "unit": unit[:12], "label": measure[:80],
            "missing": [str(m)[:80] for m in pl["missing"]][:30], "n_missing": len(pl["missing"])}
    top = lv[:10] + (lv[-5:] if len(lv) > 15 else [])
    table = {"cols": [entity.capitalize()[:40], ("%s, %s" % (measure[:50], periods[last])) if periods[last] else measure[:60]],
             "rows": [[lab, f(v) + (unit if unit == "%" else "")] for v, lab in top]}
    title = "%s by %s, on the map%s" % (measure, noun, (", %s to %s" % (periods[0], periods[-1])) if len(periods) > 1 else
                                      (", %s" % periods[0]) if periods[0] else "")
    darker = "worse" if data["dir"] == "higher_worse" else "better" if data["dir"] == "higher_better" else "higher"
    sub = ("Each dot is a %s where it is; darker is %s." % (noun, darker) if pl["mode"] == "points" else
           "Each %s is shaded by its value; darker is %s." % (noun, darker)) + \
        (" Move the year to recolour the map: the legend stays the same for every year." if len(periods) > 1 else "")
    rec = _nv._record("geo_map", title, sub, "other", title, [], None, None,
                      {"label": measure, "unit": "%" if unit == "%" else "", "kind": "amount"}, data, table, summary, (0, ""),
                      "The rows of the file, by %s and year" % noun,
                      {"columns": [entity, measure], "rows": sum(1 for it in items for v in it["v"] if v is not None), "months": None,
                       "op": "each %s's value a year, placed by its %s" % (noun, "Census CBSA code or name" if pl["mode"] == "points" else
                                                                         "state name or code")})
    return rec
