#!/usr/bin/env python3
"""Check tools/fixtures/viz/spec.json, the chart registry's frozen interface (engine/CONTRACT-v2.md 5.9).

It checks that the file is strict JSON (no NaN, no duplicate keys), that the spec holds the caps, menu, draw kinds,
colours and plan rules the three builders read, and that every example record passes the record schema and its
kind's invariants (a record's byte size against HEATMAP_BYTES for a heatmap, CHART_BYTES for every other kind). Then
it breaks copies of the examples on purpose and confirms each break is caught, and keeps one it must not refuse (a
heatmap between the two caps), so a pass means something. No dependencies; when the jsonschema package is installed it also checks the schema as draft
2020-12 and re-validates every example with it.

    python3 tools/fixtures/viz/validate_spec.py [path/to/spec.json]      # exit 0 when every check passes
"""
import copy
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MENU_NAMES = ["contribution_waterfall", "pvm_waterfall", "calendar_heatmap", "change_heatmap", "crosstab_heatmap",
              "theme_rating_heatmap", "correlation_heatmap", "group_ranges", "pareto", "slope"]
DRAW = ["waterfall", "heatmap", "dot_range", "pareto", "slope", "table"]
CAPS = {"VIZ_MAX": 8, "VIZ_AUTO": 4, "HEAT_MAX": 3, "SMALL_CELL": 5, "AI_CHARTS_MAX": 10, "CHART_BYTES": 6000,
        "HEATMAP_BYTES": 12000}
# the breaks only the schema catches, so the jsonschema cross-check must catch them too (a byte cap is not a schema rule)
SCHEMA_BREAKS = ("an unknown draw kind", "a missing summary", "an unknown key")


def load(path):
    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key in %s" % keys)
        return dict(pairs)

    def no_const(c):
        raise ValueError("%s is not JSON" % c)
    with open(path, encoding="utf-8") as fh:
        return json.loads(fh.read(), object_pairs_hook=no_dupes, parse_constant=no_const)


# ---------------------------------------------------------------- the JSON Schema subset spec.json uses
def is_type(x, t):
    if t == "null":
        return x is None
    if t == "boolean":
        return isinstance(x, bool)
    if t == "integer":
        return isinstance(x, int) and not isinstance(x, bool) or isinstance(x, float) and x.is_integer()
    if t == "number":
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    return isinstance(x, {"string": str, "array": list, "object": dict}[t])


def validate(x, sch, root, path="$"):
    errs = []
    if "$ref" in sch:
        node = root
        for part in sch["$ref"].lstrip("#/").split("/"):
            node = node[part]
        errs += validate(x, node, root, path)
    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(is_type(x, t) for t in types):
            return errs + ["%s: %r is not %s" % (path, x if not isinstance(x, (dict, list)) else type(x).__name__, types)]
    if "enum" in sch and x not in sch["enum"]:
        errs.append("%s: %r not in %s" % (path, x, sch["enum"]))
    if "const" in sch and x != sch["const"]:
        errs.append("%s: %r is not %r" % (path, x, sch["const"]))
    if isinstance(x, str):
        if len(x) > sch.get("maxLength", math.inf) or len(x) < sch.get("minLength", 0):
            errs.append("%s: length %d outside [%s, %s]" % (path, len(x), sch.get("minLength", 0), sch.get("maxLength")))
        if "pattern" in sch and not re.search(sch["pattern"], x):
            errs.append("%s: %r does not match %s" % (path, x, sch["pattern"]))
    if is_type(x, "number"):
        if x < sch.get("minimum", -math.inf) or x > sch.get("maximum", math.inf):
            errs.append("%s: %r outside [%s, %s]" % (path, x, sch.get("minimum"), sch.get("maximum")))
    if isinstance(x, list):
        if len(x) > sch.get("maxItems", math.inf) or len(x) < sch.get("minItems", 0):
            errs.append("%s: %d items outside [%s, %s]" % (path, len(x), sch.get("minItems", 0), sch.get("maxItems")))
        if "items" in sch:
            for i, v in enumerate(x):
                errs += validate(v, sch["items"], root, "%s[%d]" % (path, i))
    if isinstance(x, dict):
        for k in sch.get("required", []):
            if k not in x:
                errs.append("%s: missing %s" % (path, k))
        props = sch.get("properties", {})
        for k, v in x.items():
            if k in props:
                errs += validate(v, props[k], root, "%s.%s" % (path, k))
            elif sch.get("additionalProperties") is False:
                errs.append("%s: unknown key %s" % (path, k))
    if "anyOf" in sch and all(validate(x, s, root, path) for s in sch["anyOf"]):
        errs.append("%s: matches none of anyOf" % path)
    for s in sch.get("allOf", []):
        errs += validate(x, s, root, path)
    if "if" in sch and not validate(x, sch["if"], root, path):
        errs += validate(x, sch.get("then", {}), root, path)
    return errs


# ---------------------------------------------------------------- JSON.stringify's size of a record
def js_num(v):
    """A number as JSON.stringify writes it (Number.prototype.toString)."""
    if isinstance(v, int):
        return str(v)
    r = repr(v)
    if v.is_integer() and abs(v) < 1e21:
        if abs(v) < 2 ** 53 or "e" not in r:
            return str(int(v))
        mant, exp = r.split("e")                    # 1.2345678901234568e+20 -> 123456789012345680000
        return ("-" if v < 0 else "") + mant.lstrip("-").replace(".", "").ljust(int(exp) + 1, "0")
    if "e" not in r:
        return r
    mant, exp = r.split("e")
    e = int(exp)
    if -7 < e < 21:
        return format(float(r), "f").rstrip("0").rstrip(".") if e < 0 else str(int(float(r)))
    return mant + "e" + ("+" if e > 0 else "-") + str(abs(e))


def js_bytes(x):
    def enc(v):
        if isinstance(v, dict):
            return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + enc(w) for k, w in v.items()) + "}"
        if isinstance(v, list):
            return "[" + ",".join(enc(w) for w in v) + "]"
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        return js_num(v)
    return len(enc(x).encode("utf-8"))


# ---------------------------------------------------------------- the invariants of a record
def close(a, b, tol):
    return abs(a - b) <= tol


def check_record(r, spec):
    E, caps, menu = [], spec["caps"], spec["menu"]
    small, tol_rel = caps["SMALL_CELL"], caps["RECONCILE_TOL"]
    kind, chart, d = r["kind"], r["chart"], r["data"]
    if kind != "table":
        if chart not in menu:
            E.append("chart %s is not on the menu" % chart)
        elif menu[chart]["kind"] != kind:
            E.append("%s draws as %s, not %s" % (chart, menu[chart]["kind"], kind))
        elif r["section"] not in {menu[chart]["section"], menu[chart].get("section_if_not_primary")}:
            E.append("section %s is not %s's" % (r["section"], chart))
    else:
        if d != {} or not r["table"]["cols"]:
            E.append("a table record has data {} and a table with columns")
        if chart in menu and not r.get("degraded"):
            E.append("a menu chart shown as a table says why (degraded)")
    if (r["chosen_by"] == "engine") != r["why"].startswith("Chosen by the engine: "):
        E.append("an engine pick's why, and only an engine pick's, starts 'Chosen by the engine: '")
    if r["grade"] is not None and r["parent_grade"] is not None:
        E.append("grade and parent_grade are never both set")
    if len(set(r["anchors"])) != len(r["anchors"]):
        E.append("anchors repeat")
    for i, row in enumerate(r["table"]["rows"]):
        if len(row) != len(r["table"]["cols"]):
            E.append("table row %d has %d cells for %d columns" % (i, len(row), len(r["table"]["cols"])))
    # the byte cap by kind: HEATMAP_BYTES for a heatmap, CHART_BYTES for every other kind (the owner's decision)
    cap = "HEATMAP_BYTES" if kind == "heatmap" else "CHART_BYTES"
    size = js_bytes(r)
    if size > caps[cap]:
        E.append("%d bytes, over %s" % (size, cap))

    if kind == "waterfall":
        st = d["steps"]
        mid = st[1:-1]
        if st[0]["kind"] != "total" or st[-1]["kind"] != "total" or any(s["kind"] != "step" for s in mid):
            E.append("waterfall: totals first and last, steps between")
        if len(mid) > caps["WATERFALL_PARTS_MAX"]:
            E.append("waterfall: more than %d parts" % caps["WATERFALL_PARTS_MAX"])
        prior, latest, change = st[0]["value"], st[-1]["value"], d["change"]["value"]
        tol = tol_rel * max(1.0, abs(prior), abs(latest), abs(change)) + 1e-6 * len(st)
        for t in (st[0], st[-1]):
            if t["from"] != 0 or not close(t["to"], t["value"], tol):
                E.append("waterfall: a total runs from 0 to its value")
        run = st[0]["to"]
        for s in mid:
            if not close(s["from"], run, tol) or not close(s["to"], s["from"] + s["value"], tol):
                E.append("waterfall: step %r does not continue the running total" % s["label"])
            run = s["to"]
        if not close(math.fsum(s["value"] for s in mid), change, tol) or not close(prior + change, latest, tol) \
                or not close(run, latest, tol):
            E.append("waterfall: the steps do not add up to the change and the latest total")
    elif kind == "heatmap":
        R, C = len(d["rows"]), len(d["cols"])
        for key in ("values", "text", "tier", "n"):
            if len(d[key]) != R or any(len(row) != C for row in d[key]):
                E.append("heatmap: %s is not %d x %d" % (key, R, C))
                return E
        seq = d["scale"] == "sequential"
        supp, used, by_tier = 0, set(), {}
        for i in range(R):
            for j in range(C):
                v, t, k, n = d["values"][i][j], d["text"][i][j], d["tier"][i][j], d["n"][i][j]
                if v is None:
                    if t == "<5" and k == 0 and n is None:
                        supp += 1
                    elif not (t == "" and k == 0 and n == 0):
                        E.append("heatmap [%d][%d]: no value, so '<5' (n null) or '' (n 0), tier 0" % (i, j))
                    continue
                # the theme heatmap's 'all' column when a rating (or the texts with no rating) is hidden: whole percents
                # and no n, so the hidden texts cannot be worked back from it (review of the chart registry, 30 Sep 2026)
                whole_all = chart == "theme_rating_heatmap" and j == C - 1 and d["cols"][-1] == "all" and n is None \
                    and float(v).is_integer() and t == "%d%%" % int(v)
                if not t or (n is None and not whole_all) or (n is not None and n < small):
                    E.append("heatmap [%d][%d]: a shown cell has its text and %d or more rows" % (i, j, small))
                if k == 0:
                    diag = chart == "correlation_heatmap" and i == j
                    if not (diag or (not seq and v == 0)):
                        E.append("heatmap [%d][%d]: tier 0 with a value" % (i, j))
                    continue
                if seq and k < 0 or not seq and (k > 0) != (v > 0):
                    E.append("heatmap [%d][%d]: tier %d does not fit value %r on a %s scale" % (i, j, k, v, d["scale"]))
                used.add(k)
                by_tier.setdefault(k, []).append(abs(v) if not seq else v)
        order = sorted(by_tier, key=abs)
        for sign in (1, -1):
            ts = [t for t in order if (t > 0) == (sign > 0)]
            for a, b in zip(ts, ts[1:]):
                if max(by_tier[a]) > min(by_tier[b]):
                    E.append("heatmap: tier %d holds a value above tier %d's" % (a, b))
        if supp != r["suppressed"]["cells"]:
            E.append("heatmap: suppressed.cells is %d, the grid has %d '<5' cells" % (r["suppressed"]["cells"], supp))
        lt = [x["tier"] for x in d["legend"]]
        if lt != sorted(used) or (seq and any(t < 0 for t in lt)):
            E.append("heatmap: the legend lists %s, the cells use %s" % (lt, sorted(used)))
        if len(r["table"]["rows"]) != R or len(r["table"]["cols"]) != C + 1:
            E.append("heatmap: the table view is the grid (a row label and one column per col)")
    elif kind == "dot_range":
        for x in d["rows"]:
            if not x["lo"] <= x["center"] <= x["hi"]:
                E.append("dot_range %r: lo <= center <= hi" % x["label"])
    elif kind == "pareto":
        bars, other, total = d["bars"], d["other"], d["total"]["value"]
        tol = tol_rel * max(1.0, total) + 1e-6 * (len(bars) + 1)
        if any(a["value"] < b["value"] for a, b in zip(bars, bars[1:])):
            E.append("pareto: bars are not largest first")
        run = 0.0
        for b in bars + ([other] if other else []):
            run += b["value"]
            if not close(b["cum_pct"], 100.0 * run / total, 1e-4):
                E.append("pareto %r: cum_pct is not the running share" % b["label"])
        if not close(run, total, tol) or (other and not close(other["cum_pct"], 100.0, 1e-4)):
            E.append("pareto: the bars and other do not add up to the total")
        k = d["k80"]
        if k["of"] != len(bars) + (other["n_entities"] if other else 0) or k["k"] > k["of"]:
            E.append("pareto: k80.of counts every level, and k80.k is at most that")
        if k["k"] <= len(bars):
            if bars[k["k"] - 1]["cum_pct"] < 80 or (k["k"] > 1 and bars[k["k"] - 2]["cum_pct"] >= 80):
                E.append("pareto: bar k80.k is the first to reach 80%")
        elif bars[-1]["cum_pct"] >= 80:
            E.append("pareto: k80.k lies beyond the bars only when the bars stay under 80%")
    elif kind == "slope" and not d["rows"]:
        E.append("slope: no rows")
    return E


# ---------------------------------------------------------------- the spec itself
def luminance(h):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check_spec(spec):
    E = []
    for k, v in CAPS.items():
        if spec["caps"].get(k) != v:
            E.append("caps.%s is %r, not %r" % (k, spec["caps"].get(k), v))
    if list(spec["menu"]) != MENU_NAMES:
        E.append("the menu is not the Batch 1 list, in order")
    for name, m in spec["menu"].items():
        if m["kind"] not in DRAW[:-1] or m["section"] not in spec["sections"] or not m["conditions"] or not m["privacy"]:
            E.append("menu %s: kind, section, conditions and privacy" % name)
        if any(a["role"] not in spec["roles"] for a in m["args"]) or not m["args_text"].startswith("["):
            E.append("menu %s: args roles and args_text" % name)
        if len(m["limit_needs"]) > 100:
            E.append("menu %s: limit_needs leaves under 60 of the 160 characters for what the file has" % name)
    if [k for k in spec["draw_kinds"] if k in DRAW] != DRAW or any(len(k) > spec["caps"]["KIND_MAX_CHARS"] for k in DRAW):
        E.append("draw_kinds: waterfall, heatmap, dot_range, pareto, slope, table, each at most 10 characters")
    for mode in ("light", "dark"):
        p = spec["colors"][mode]
        hexes = re.findall(r"#[0-9a-fA-F]{6}\b", json.dumps(p))
        if len(hexes) < 30:
            E.append("colors.%s: fewer hex values than the palette holds" % mode)
        fills = [p[g][t] for g in ("sequential", "falls", "rises") for t in ("1", "2", "3")]
        if min(contrast(p["cell_text"], f) for f in fills) < 4.5:
            E.append("colors.%s: cell text under 4.5:1 on a tier" % mode)
        if contrast(p["suppressed"]["text"], p["suppressed"]["fill"]) < 4.5:
            E.append("colors.%s: suppressed text under 4.5:1" % mode)
        marks = [p["waterfall"][k] for k in ("total", "rise", "fall")] + [p["slope"]["rise"], p["slope"]["fall"],
                                                                         p["pareto"]["cumulative"], p["dot_range"]["dot"]]
        if min(contrast(c, p["frame"]["surface"]) for c in marks) < 3:
            E.append("colors.%s: a mark under 3:1 on the surface" % mode)
    light = spec["colors"]["light"]
    design = {"sequential": ["#cfe9e4", "#add9d2", "#87c7bc"], "falls": ["#d9e7f8", "#b7d1f1", "#94bcea"],
              "rises": ["#f1e2d1", "#e5c8a8", "#d9ae80"]}
    for g, want in design.items():
        if [light[g][t] for t in ("1", "2", "3")] != want:
            E.append("colors.light.%s is not the design's %s" % (g, want))
    if [light["waterfall"][k] for k in ("total", "rise", "fall")] != ["#24425c", "#0b7366", "#945400"]:
        E.append("colors.light.waterfall is not the design's")
    # the plan directive's and the limits' examples
    pd_ = spec["plan_directive"]
    ex = pd_["example"]["plan_charts"]
    if len(ex) > pd_["max"] or pd_["max"] != spec["caps"]["VIZ_MAX"]:
        E.append("plan_directive: at most VIZ_MAX charts")
    for it in ex:
        m = spec["menu"].get(it["kind"])
        req = [a for a in (m or {}).get("args", []) if not a.get("optional")]
        if m is None or not (len(req) <= len(it["columns"]) <= len(m["args"])) or len(it["why"]) > 200:
            E.append("plan_directive example %s: a menu name, its args, a why of at most 200" % it["kind"])
    lim = spec["chart_limits"]["example_ship2"]
    if len(lim) > spec["caps"]["CHART_LIMITS_MAX"] or [x["chart"] for x in lim] != MENU_NAMES \
            or any(len(x["why"]) > spec["caps"]["CHART_LIMIT_WHY_MAX"] or not isinstance(x["ok"], bool) for x in lim):
        E.append("chart_limits example: one per menu chart, in order, ok a boolean, why at most 160")
    if any(not x["why"].startswith(spec["menu"][x["chart"]]["limit_needs"] + "; ") for x in lim if x["chart"] in spec["menu"]):
        E.append("chart_limits example: each why is its chart's limit_needs, '; ', then what the file has")
    lw = {x["chart"]: x for x in lim}
    if any(lw[x["chart"]]["ok"] or x["why"] != lw[x["chart"]]["why"] for x in pd_["example"]["refused"]):
        E.append("plan_directive example: a chart whose limit is not ok is refused with the limit's why")
    # the examples cover every draw kind and every edge case asked for
    recs = {k: v["record"] for k, v in spec["examples"].items()}
    for k in DRAW:
        if recs.get(k, {}).get("kind") != k:
            E.append("examples: no %s example" % k)
    heat = [r for r in recs.values() if r["kind"] == "heatmap"]
    falls = [r for r in recs.values() if r["kind"] == "waterfall"]
    labels = [s for r in recs.values() for s in json.dumps(r["data"], ensure_ascii=False).split('"')]
    edge = {
        "an all-empty heatmap": any(all(v is None for row in r["data"]["values"] for v in row) for r in heat),
        "a 1-row heatmap": any(len(r["data"]["rows"]) == 1 for r in heat),
        "suppressed cells": any(r["suppressed"]["cells"] > 0 and any(v is not None for row in r["data"]["values"]
                                                                     for v in row) for r in heat),
        "a diverging heatmap": any(r["data"]["scale"] == "diverging" and r["data"]["legend"] for r in heat),
        "negative totals in a waterfall": any(r["data"]["steps"][0]["value"] < 0 and r["data"]["steps"][-1]["value"] < 0
                                              for r in falls),
        "12 long segment labels": any(len(r["data"]["steps"]) == 14 and min(len(s["label"]) for s in
                                                                            r["data"]["steps"][1:-1]) >= 40 for r in falls),
        "a non-Latin label": any(any(ord(ch) > 0x24F and ch not in "−○◐●" for ch in s) for s in labels),
    }
    E += ["examples: no case with %s" % k for k, ok in edge.items() if not ok]
    # the ship2 figures the brief names
    wf = {s["label"]: s["text"] for s in recs["waterfall"]["data"]["steps"]}
    pvm = {s["label"]: s["text"] for s in recs["edge_waterfall_pvm"]["data"]["steps"]}
    want = {"12 months before": "128,256", "Latest 12 months": "152,214", "East": "+16,220", "North": "+7,116",
            "West": "+330", "South": "+292"}
    if any(wf.get(k) != v for k, v in want.items()) or recs["waterfall"]["data"]["change"]["text"] != "+23,958" \
            or [pvm.get(k) for k in ("Price", "Volume", "Mix")] != ["+20,527", "+2,051", "+1,380"]:
        E.append("examples: the ship2 figures are not 128,256 / 152,214 / +23,958, the regions' and price/volume/mix")
    return E


def breaks(spec):
    """Copies of the examples broken on purpose: each must be caught, or the check is too weak."""
    ex = {k: v["record"] for k, v in spec["examples"].items()}

    def mut(name, f):
        r = copy.deepcopy(ex[name])
        f(r)
        return r
    out = [
        ("a waterfall that does not add up", mut("waterfall", lambda r: r["data"]["change"].update(value=23958.9))),
        ("a step off the running total", mut("waterfall", lambda r: r["data"]["steps"][2].update({"from": 144000}))),
        ("a shown cell on 3 rows", mut("heatmap", lambda r: r["data"]["n"][0].__setitem__(0, 3))),
        ("a cell text over 12 characters", mut("heatmap", lambda r: r["data"]["text"][0].__setitem__(0, "8,592 orders!"))),
        ("a tier against its value", mut("edge_heatmap_diverging", lambda r: r["data"]["tier"][0].__setitem__(1, 2))),
        ("a miscounted suppression", mut("edge_heatmap_suppressed", lambda r: r["suppressed"].update(cells=4))),
        ("a ragged grid", mut("heatmap", lambda r: r["data"]["values"][1].pop())),
        ("a pareto out of order", mut("pareto", lambda r: r["data"]["bars"].reverse())),
        ("a wrong k80", mut("pareto", lambda r: r["data"]["k80"].update(k=12))),
        ("a dot outside its range", mut("dot_range", lambda r: r["data"]["rows"][0].update(center=2000))),
        ("an unknown draw kind", mut("slope", lambda r: r.update(kind="sankey"))),
        ("a missing summary", mut("slope", lambda r: r.update(summary=""))),
        ("an unknown key", mut("table", lambda r: r.update(colour="red"))),
        ("a record over CHART_BYTES", mut("edge_waterfall_12_long_labels", over_chart_bytes)),
        ("a heatmap over HEATMAP_BYTES", mut("edge_heatmap_diverging", over_heatmap_bytes)),
    ]
    return out


def keeps(spec):
    """Copies that must pass: a heatmap between the two caps is kept (HEATMAP_BYTES), so the byte check goes by kind."""
    r = copy.deepcopy(spec["examples"]["edge_heatmap_diverging"]["record"])
    d, k = r["data"], 6
    for key in ("values", "text", "tier", "n"):
        d[key] = [row + row[:k] for row in d[key]]
    extra = [c + " again" for c in d["cols"][:k]]
    d["cols"] = d["cols"] + extra
    r["table"]["cols"] = r["table"]["cols"] + extra
    r["table"]["rows"] = [row + row[1:1 + k] for row in r["table"]["rows"]]
    return [("a heatmap between CHART_BYTES and HEATMAP_BYTES (10 x 18)", r)]


def over_chart_bytes(r):
    """A waterfall over CHART_BYTES (and under HEATMAP_BYTES) that is otherwise valid: 80-character labels (the
    12 long ones in a script of 3 UTF-8 bytes a character), the same labels in its table, and source and subtitle at
    their caps. Only the byte cap catches it."""
    for i, s in enumerate(r["data"]["steps"]):
        s["label"] = (s["label"] + " " + "\u6771" * 80)[:80]
        r["table"]["rows"][i][0] = s["label"]
    r.update(source="s" * 200, subtitle="t" * 160)


def over_heatmap_bytes(r):
    """A heatmap over HEATMAP_BYTES that is otherwise valid: row labels of 79 characters in a script of 3 UTF-8 bytes
    a character (the same in its table), column labels of 75, and source, subtitle, why and summary at their caps (the
    why still an engine pick's). Only the byte cap catches it."""
    r["data"]["rows"] = ["\u6771" * 78 + str(i) for i in range(len(r["data"]["rows"]))]
    for i, row in enumerate(r["table"]["rows"]):
        row[0] = r["data"]["rows"][i]
    r["data"]["cols"] = [c + " " + "m" * 70 for c in r["data"]["cols"]]
    r["table"]["cols"] = [r["table"]["cols"][0]] + r["data"]["cols"]
    r.update(source="s" * 200, subtitle="t" * 160, why=("Chosen by the engine: " + "w" * 200)[:200],
             summary=(r["summary"] + " " + "More words on the months. " * 20)[:399].strip())


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "spec.json")
    spec = load(path)
    schema = spec["schema"]
    fails = ["spec: " + e for e in check_spec(spec)]
    for name, ex in spec["examples"].items():
        errs = validate(ex["record"], schema, schema)
        errs += [] if errs else check_record(ex["record"], spec)
        fails += ["examples.%s: %s" % (name, e) for e in errs]
        print("%-34s %-9s %5d bytes  %s" % (name, ex["record"]["kind"], js_bytes(ex["record"]), "ok" if not errs else "FAIL"))
    for what, r in breaks(spec):
        caught = validate(r, schema, schema) or check_record(r, spec)
        if not caught:
            fails.append("the validator missed %s" % what)
    for what, r in keeps(spec):
        size = js_bytes(r)
        errs = validate(r, schema, schema) or check_record(r, spec)
        if errs or not spec["caps"]["CHART_BYTES"] < size <= spec["caps"]["HEATMAP_BYTES"]:
            fails.append("the validator refused %s (%d bytes): %s" % (what, size, errs[:2]))
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema:
        jsonschema.Draft202012Validator.check_schema(schema)
        v = jsonschema.Draft202012Validator(schema)
        for name, ex in spec["examples"].items():
            fails += ["jsonschema examples.%s: %s" % (name, e.message) for e in v.iter_errors(ex["record"])]
        for what, r in breaks(spec):
            if what in SCHEMA_BREAKS and not list(v.iter_errors(r)):
                fails.append("jsonschema missed %s" % what)
    print("breaks caught: %d of %d; kept: %d of %d; jsonschema cross-check: %s" % (
        len(breaks(spec)) - sum(1 for f in fails if f.startswith("the validator missed")), len(breaks(spec)),
        len(keeps(spec)) - sum(1 for f in fails if f.startswith("the validator refused")), len(keeps(spec)),
        "yes (draft 2020-12)" if jsonschema else "not installed, skipped"))
    for f in fails:
        print("FAIL", f)
    print("ALL PASS" if not fails else "%d FAILED" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
