#!/usr/bin/env python3
"""Read the QA driver's TESTOUT block from a rendered DOM dump and judge it.

    python tools/check_driver.py DUMP.html [--root SITE]

The expected figures are computed here from the JSON the page was built from
(demo-data.json for the v1 layout), formatted the way the page's JavaScript formats
them, so the test cannot drift from the data and no expected number is typed by hand.
Exit code 1 on any failure.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_name(ym):
    return "%s %s" % (MONTHS[int(ym[5:7]) - 1], ym[:4])


def fmt_b(v):                       # app.js fmtB: US$ millions -> "$773.9B"
    return "$" + format(v / 1000.0, ",.1f") + "B"


def fmt_pct(v, dp=1):               # app.js fmtPct: signed, fixed dp
    return ("+" if v >= 0 else "") + format(v, ".%df" % dp) + "%"


def months_between(a, b):
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7])) + 1


def read_testout(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r'<pre id="TESTOUT">(.*?)</pre>', raw, re.S)
    if not m:
        return None
    return json.loads(html.unescape(m.group(1)))


def judge(out, root):
    fails, notes = [], []
    if out is None:
        return ["TESTOUT not found in the rendered page: the page did not finish loading or the driver was not injected"], notes
    if out.get("THROWN"):
        fails.append("driver threw: %s" % out["THROWN"])
    if out.get("errors"):
        fails.append("JavaScript errors on the page: %s" % out["errors"])
    if out.get("emDash"):
        fails.append("an em dash appears in the page text (house style bans it)")
    if not out.get("hasMain"):
        fails.append("no <main> landmark")
    if out.get("svgUnlabelled"):
        fails.append("SVG without role=\"img\" and an accessible name in: %s" % ", ".join(out["svgUnlabelled"]))
    if out.get("hScroll"):
        fails.append("the page scrolls sideways at the test window width")
    notes.append("layout profile: %s; %d element(s) carry data-fact" % (out.get("profile"), out.get("factCount", 0)))

    if out.get("profile") == "v1":
        dd_path = os.path.join(root, "demo-data.json")
        D = json.load(open(dd_path))
        v = out.get("v1", {})
        last_b, mom, yoy = fmt_b(D["last_value"]), fmt_pct(D["mom_pct"], 2), fmt_pct(D["yoy_pct"], 2)
        ecom = format(D["ecom"]["latest_value"], ".1f") + "%"
        f0 = fmt_b(D["forecast"][0])
        n_fit = months_between("%d-01" % int(D["fit_start"]), D["last_date"])
        expect = [
            ("hero date", v.get("bcDate") == month_name(D["last_date"]), v.get("bcDate"), month_name(D["last_date"])),
            ("hero latest value", last_b in (v.get("bcP1") or ""), v.get("bcP1"), last_b),
            ("hero month-over-month", mom in (v.get("bcP1") or ""), v.get("bcP1"), mom),
            ("hero year-over-year", yoy in (v.get("bcP1") or ""), v.get("bcP1"), yoy),
            ("hero e-commerce share", ecom in (v.get("bcP2") or ""), v.get("bcP2"), ecom),
            ("hero next-month forecast", f0 in (v.get("bcP3") or ""), v.get("bcP3"), f0),
            ("first KPI tile", (v.get("kpiValues") or [None])[0] == last_b, (v.get("kpiValues") or [None])[0], last_b),
            ("memo what-changed", last_b in (v.get("memo1") or "") and yoy in (v.get("memo1") or ""), v.get("memo1"), last_b + " and " + yoy),
            ("memo band", all(x in (v.get("memo3") or "") for x in (f0, fmt_b(D["lo80"][0]), fmt_b(D["hi80"][0]))),
             v.get("memo3"), "%s, %s to %s" % (f0, fmt_b(D["lo80"][0]), fmt_b(D["hi80"][0]))),
            # v1 printed chart months + forecast months here (36 + 12 = 48); the model was fit on n_fit months
            ("model fit length", v.get("modelFit") == str(n_fit), v.get("modelFit"),
             "%d (months from %s-01 to %s, the fit window in demo-data.json)" % (n_fit, D["fit_start"], D["last_date"])),
            ("model series named", "RSAFS" in (v.get("modelDisc") or ""), v.get("modelDisc"), "mentions RSAFS"),
            ("chart paths", v.get("chartPaths") == 4, v.get("chartPaths"), 4),
            ("band shown then hidden then restored",
             (v.get("bandVisible"), v.get("bandHiddenAfterToggle"), v.get("bandRestored")) == (True, True, True),
             (v.get("bandVisible"), v.get("bandHiddenAfterToggle"), v.get("bandRestored")), (True, True, True)),
            ("baseline shown then hidden then restored",
             (v.get("naiveVisible"), v.get("naiveHiddenAfterToggle"), v.get("naiveRestored")) == (True, True, True),
             (v.get("naiveVisible"), v.get("naiveHiddenAfterToggle"), v.get("naiveRestored")), (True, True, True)),
            ("stat tiles", v.get("stats") == 4, v.get("stats"), 4),
            ("KPI tiles", v.get("kpis") == 4, v.get("kpis"), 4),
            ("legend items", v.get("legend") == 8, v.get("legend"), 8),
            ("offers", v.get("offers") == 3, v.get("offers"), 3),
            ("loop steps", v.get("loopSteps") == 6, v.get("loopSteps"), 6),
            ("architecture diagram", v.get("archSvg") is True, v.get("archSvg"), True),
            ("footer cites FRED", "FRED" in (v.get("footNote") or ""), v.get("footNote"), "mentions FRED"),
        ]
        for name, ok, got, want in expect:
            if not ok:
                fails.append("v1 %s: page shows %r, data says %r" % (name, got, want))
        notes.append("v1 figures compared with demo-data.json: %d assertion(s)" % len(expect))
    return fails, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dump")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    out = read_testout(a.dump)
    fails, notes = judge(out, a.root)
    print("CHECK driver   %s%s" % ("PASS" if not fails else "FAIL",
                                   "" if not fails else " (%d problem%s)" % (len(fails), "" if len(fails) == 1 else "s")))
    for n in notes:
        print("    note: " + n)
    for f in fails:
        print("    - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
