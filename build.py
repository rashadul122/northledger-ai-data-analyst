#!/usr/bin/env python3
"""Build the NorthLedger Insights portfolio page (v2) into one offline file, index.html.

    python3 build.py              # build index.html, case-study.html (+ .md), data/site_build.json, downloads/
    python3 build.py --check      # rebuild in memory and fail if data/site_build.json would change
    python3 build.py --tests      # also run ../northledger-core/run_tests.sh and record the result
                                  #   in data/engine_tests.json (takes about a minute)

What it does
  1. Reads the data files in data/ (written by build_rentsafe.py, build_forecast.py, the engine
     runs and the replay sanitizer) plus site.config.json, the only file where a person types
     values (contact details, prices).
  2. Computes every derived figure the page shows (a share turned into a percentage, a count of
     sessions, a file size) and writes them, with the source of each, to data/site_build.json.
  3. Runs the build checks below; any failure stops the build.
  4. Renders src/sections/*.html. A figure is never typed in a section: it is written as
     {{f file:path|fmt}} (bound to data/<file>.json) or {{d key}} (bound to a derived figure in
     data/site_build.json). Both become <span data-fact=...> so tools/check_site.py can prove the
     text equals its JSON, in the built page and in the rendered page.
  5. Inlines the colour tokens, src/style.css, the JSON the browser needs and src/js/*.js into
     index.html, and renders case-study.html (styled like the page, both themes; the page links
     here) and case-study.md from src/case-study.template.md, the one source for both.

No network access. Nothing here publishes anything.
"""
from __future__ import annotations

import csv
import datetime as _dt
import decimal
import glob
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
DATA = os.path.join(ROOT, "data")
REPO = os.path.dirname(ROOT)
ENGINE = os.path.join(REPO, "northledger-core")
RAW = os.path.join(REPO, "data", "rentsafe")
ENGAGEMENT = os.path.join(REPO, "engagements", "rentsafe")
PBIP_DIR = os.path.join(ENGAGEMENT, "view-2023", "deliverables", "powerbi", "RentSafeTO Scorecard View")
BRIEF_PDF = os.path.join(ENGAGEMENT, "view-2023", "deliverables", "business-analysis-exec.pdf")
BRIEF_FULL_PDF = os.path.join(ENGAGEMENT, "view-2023", "deliverables", "business-analysis-full.pdf")
EVIDENCE_XLSX = os.path.join(ENGAGEMENT, "view-2023", "deliverables", "business-analysis-evidence.xlsx")
DL = os.path.join(ROOT, "downloads")
PBIP_ZIP = "downloads/rentsafe-scorecard-view-pbip.zip"
BRIEF_OUT = "downloads/rentsafe-engine-brief-exec.pdf"
BRIEF_FULL_OUT = "downloads/rentsafe-engine-brief-full.pdf"
EVIDENCE_OUT = "downloads/rentsafe-engine-evidence.xlsx"
DOWNLOAD_KEY = {PBIP_ZIP: "pbip_zip", BRIEF_OUT: "brief_pdf", BRIEF_FULL_OUT: "brief_full", EVIDENCE_OUT: "evidence_xlsx"}

sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_site  # noqa: E402  (shared formatting, path resolution and leak scanning)

PILLARS = ["envelope", "grounds", "entry", "systems", "stairs", "interiors", "waste", "paperwork"]
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
esc = html.escape


# ----------------------------------------------------------------------------- helpers
def load(name):
    with open(os.path.join(DATA, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def fmt(v, f):
    return check_site.fmt_value(v, f)


def pct(share, dp=1):                     # 0.9944 -> "99.4%"
    return format(share * 100.0, ",.%df" % dp) + "%"


def half_up(v, dp=1):
    """Decimal half-up of the stored value, binary noise removed first: the rounding the page's
    JavaScript uses for scorecard cells (U.half in src/js/00-core.js)."""
    q = decimal.Decimal(1).scaleb(-dp)
    return str(decimal.Decimal(format(v, ".12g")).quantize(q, rounding=decimal.ROUND_HALF_UP))


def human_bytes(n):
    if n >= 1_000_000:
        return format(n / 1_000_000.0, ",.1f") + " MB"
    return format(n / 1000.0, ",.0f") + " KB"


def utc_label(ts):
    return ts.replace("T", " ").replace("Z", " UTC")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def engine_snapshot():
    """Same recipe as northledger.loop.engine_snapshot: sha256 over northledger/*.py."""
    here = os.path.join(ENGINE, "northledger")
    if not os.path.isdir(here):
        return None
    h = hashlib.sha256()
    for f in sorted(x for x in os.listdir(here) if x.endswith(".py")):
        h.update(f.encode("utf-8") + b"\0")
        with open(os.path.join(here, f), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


class Checks:
    def __init__(self):
        self.items = []

    def __call__(self, name, ok, detail=""):
        self.items.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def failed(self):
        return [c for c in self.items if not c["ok"]]


# ----------------------------------------------------------------------------- colour tokens
# One place for the palette. The band hues passed the dataviz skill's palette validator
# (lightness band, chroma, colour-blind separation) in both themes; the heat-cell tints and all
# text colours are checked for contrast below, at every build.
TOKENS = {
    "light": {"bg": "#f7f6f3", "surface": "#ffffff", "ink": "#16232e", "body": "#33424e", "muted": "#56646f",
              "hairline": "#e2dfd6", "accent": "#0b7366", "accent-ink": "#0a5c52", "on-accent": "#ffffff",
              "grid": "#e8e5dd", "axis": "#b9b5aa", "series": "#24425c", "series-2": "#0b7366", "series-3": "#945400",
              "band-green": "#0f8f7a", "band-yellow": "#e0a000", "band-red": "#d4502c",
              "div-neg": "#2a78d6", "div-pos": "#b35c00", "focus": "#1f5fbf", "fan": "rgba(11,115,102,0.16)"},
    "dark": {"bg": "#0c141c", "surface": "#121d28", "ink": "#e9eef3", "body": "#c3ced8", "muted": "#9aabb8",
             "hairline": "#26364a", "accent": "#2bb5a3", "accent-ink": "#5fd6c5", "on-accent": "#062521",
             "grid": "#1f2d3b", "axis": "#3b4d60", "series": "#b9cce0", "series-2": "#2bb5a3", "series-3": "#e3a33b",
             "band-green": "#199e86", "band-yellow": "#bc8c00", "band-red": "#e2507a",
             "div-neg": "#3987e5", "div-pos": "#c8741c", "focus": "#8ab8ff", "fan": "rgba(43,181,163,0.20)"},
}
TINTS = {"light": (0.20, 0.34, 0.50), "dark": (0.24, 0.36, 0.48)}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a, b, t):
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(ra[i] * (1 - t) + rb[i] * t for i in range(3)))


def luminance(h):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def heat_tokens():
    out = {}
    for mode in ("light", "dark"):
        t = TOKENS[mode]
        out[mode] = {}
        for band in ("green", "yellow", "red"):
            for i, share in enumerate(TINTS[mode]):
                out[mode]["heat-%s-%d" % (band, i + 1)] = mix(t["surface"], t["band-" + band], share)
        for i, share in enumerate((0.18, 0.34, 0.50)):
            out[mode]["div-neg-%d" % (i + 1)] = mix(t["surface"], t["div-neg"], share)
            out[mode]["div-pos-%d" % (i + 1)] = mix(t["surface"], t["div-pos"], share)
    return out


def css_tokens():
    heat = heat_tokens()
    blocks_ = {}
    for mode in ("light", "dark"):
        vals = dict(TOKENS[mode])
        vals.update(heat[mode])
        blocks_[mode] = "\n".join("  --%s: %s;" % (k, v) for k, v in vals.items())
    return (":root {\n  color-scheme: light;\n%s\n}\n"
            "@media (prefers-color-scheme: dark) {\n  :root:not([data-theme=\"light\"]) {\n  color-scheme: dark;\n%s\n  }\n}\n"
            ":root[data-theme=\"dark\"] {\n  color-scheme: dark;\n%s\n}\n") % (blocks_["light"], blocks_["dark"], blocks_["dark"])


def contrast_report():
    """Every text colour against every surface it sits on, both themes (WCAG 2.x ratio)."""
    heat = heat_tokens()
    rows = []
    for mode in ("light", "dark"):
        t = TOKENS[mode]
        pairs = [("ink", "bg"), ("body", "bg"), ("muted", "bg"), ("ink", "surface"), ("body", "surface"),
                 ("muted", "surface"), ("accent-ink", "surface"), ("accent-ink", "bg"), ("on-accent", "accent"),
                 ("series-3", "surface"), ("series-3", "bg"), ("series", "surface"), ("focus", "surface")]
        for fg, bgk in pairs:
            rows.append({"mode": mode, "text": fg, "on": bgk, "ratio": round(contrast(t[fg], t[bgk]), 2)})
        for k, v in heat[mode].items():
            rows.append({"mode": mode, "text": "ink", "on": k, "ratio": round(contrast(t["ink"], v), 2)})
            rows.append({"mode": mode, "text": "body", "on": k, "ratio": round(contrast(t["body"], v), 2)})
    return rows


# ----------------------------------------------------------------------------- inputs
def read_inputs():
    I = {}
    for n in ("rentsafe_meta", "rentsafe_scorecard", "rentsafe_cube", "rentsafe_analysis_a_points_per_fix",
              "rentsafe_analysis_b_risk", "rentsafe_analysis_d_archetypes", "rentsafe_trend", "rentsafe_wards_map",
              "forecast_lab", "timings", "engine_scorecard", "pl300-status"):
        I[n] = load(n)
    I["fred_sources"] = json.load(open(os.path.join(DATA, "fred", "SOURCES.json"), encoding="utf-8"))
    I["config"] = json.load(open(os.path.join(ROOT, "site.config.json"), encoding="utf-8"))
    p = os.path.join(DATA, "engine_tests.json")
    I["engine_tests"] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    demo = open(os.path.join(ROOT, "agent-demo.html"), encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="demo-manifest">(.*?)</script>', demo, re.S)
    I["manifest"] = json.loads(m.group(1)) if m else None
    return I


def cube_rows(cube):
    F = cube["fields"]
    return [dict(zip(F, r)) for r in cube["rows"]]


def agg(rows):
    s = {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (int, float)):
                s[k] = s.get(k, 0) + v
    return s


# ----------------------------------------------------------------------------- engine tests
def run_engine_tests():
    script = os.path.join(ENGINE, "run_tests.sh")
    started = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    snap_before = engine_snapshot()
    p = subprocess.run(["bash", script], cwd=ENGINE, capture_output=True, text=True, timeout=1800)
    out = p.stdout + p.stderr
    m = re.search(r"ALL SUITES GREEN\D+(\d+) tests", out)
    suites = re.findall(r"^\s+(ok|FAIL)\s+(\S+)", out, re.M)
    rec = {
        "what": "One run of northledger-core/run_tests.sh, recorded by build.py --tests. Not a schedule.",
        "ran_at": started,
        "command": "cd northledger-core && ./run_tests.sh",
        "exit_code": p.returncode,
        "green": p.returncode == 0 and bool(m),
        "tests": int(m.group(1)) if m else None,
        "suites": len(suites),
        "suites_failed": [s for st, s in suites if st == "FAIL"],
        "engine_snapshot": {"kind": "sha256 of northledger/*.py (no git commit available)",
                            "id": snap_before, "unchanged_during_run": snap_before == engine_snapshot()},
        "summary_line": (re.findall(r"^.*(?:ALL SUITES GREEN|SUITE FAILURES).*$", out, re.M) or [""])[-1].strip(),
    }
    with open(os.path.join(DATA, "engine_tests.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return rec


# ----------------------------------------------------------------------------- downloads
def deterministic_zip(src_dir, arc_root):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fns in os.walk(src_dir):
            dns.sort()
            for fn in sorted(fns):
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
                zi = zipfile.ZipInfo(arc_root + "/" + rel, date_time=(2026, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                with open(full, "rb") as fh:
                    z.writestr(zi, fh.read())
    return buf.getvalue()


def leak_scan(blob, label):
    r = check_site.Result(label)
    for part, t in check_site._embedded_texts(blob):
        check_site._scan_text(t, "%s (%s)" % (label, part), r)
    return r.failures


def build_downloads(C, write):
    """Zip the engine's PBIP export and copy its exec brief next to the page, after a leak scan."""
    out = {}
    if os.path.isdir(PBIP_DIR):
        blob = deterministic_zip(PBIP_DIR, "RentSafeTO Scorecard View")
        bad = leak_scan(blob, PBIP_ZIP)
        C("PBIP download holds no local path or secret", not bad, "; ".join(bad[:3]))
        if write and not bad:
            os.makedirs(DL, exist_ok=True)
            open(os.path.join(ROOT, PBIP_ZIP), "wb").write(blob)
    for src, rel, what in ((BRIEF_PDF, BRIEF_OUT, "engine brief PDF"), (BRIEF_FULL_PDF, BRIEF_FULL_OUT, "engine full brief PDF"),
                           (EVIDENCE_XLSX, EVIDENCE_OUT, "engine evidence ledger")):
        if os.path.exists(src):
            blob = open(src, "rb").read()
            bad = leak_scan(blob, rel)
            C("%s holds no local path or secret" % what, not bad, "; ".join(bad[:3]))
            if write and not bad:
                os.makedirs(DL, exist_ok=True)
                open(os.path.join(ROOT, rel), "wb").write(blob)
    for rel in DOWNLOAD_KEY:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            out[rel] = {"bytes": os.path.getsize(p), "sha256": sha256_file(p)}
    C("all four downloads exist next to the page", len(out) == len(DOWNLOAD_KEY), ", ".join(out))
    return out


# ----------------------------------------------------------------------------- PBIP reading
def read_pbip():
    """Tables, relationships, measures and best-practice evidence from the exported TMDL."""
    info = {"present": os.path.isdir(PBIP_DIR), "tables": [], "relationships": [], "measures": [], "files": []}
    if not info["present"]:
        return info
    sm = os.path.join(PBIP_DIR, "RentSafeTO Scorecard View.SemanticModel", "definition")
    for p in sorted(glob.glob(os.path.join(sm, "tables", "*.tmdl"))):
        t = open(p, encoding="utf-8").read()
        name = re.search(r"^table\s+'?([^'\n]+?)'?\s*$", t, re.M).group(1)
        cols = re.findall(r"^\tcolumn\s+", t, re.M)
        ms = []
        for m in re.finditer(r"((?:^\t///[^\n]*\n)*)^\tmeasure\s+('[^']+'|\S+)\s*=\s*([^\n]+)\n((?:\t\t[^\n]*\n)*)", t, re.M):
            desc = " ".join(x.strip()[3:].strip() for x in m.group(1).splitlines() if x.strip())
            fs = re.search(r"formatString:\s*(\S+)", m.group(4))
            ms.append({"table": name, "name": m.group(2).strip("'"), "dax": m.group(3).strip(),
                       "description": desc, "format": fs.group(1) if fs else ""})
        info["tables"].append({"name": name, "columns": len(cols), "measures": len(ms),
                               "is_date": bool(re.search(r"dataCategory:\s*Time", t))})
        info["measures"].extend(ms)
    relp = os.path.join(sm, "relationships.tmdl")
    rel = open(relp, encoding="utf-8").read() if os.path.exists(relp) else ""
    for m in re.finditer(r"relationship\s+[^\n]+\n((?:\t[^\n]*\n?)*)", rel):
        body = m.group(1)
        fr = re.search(r"fromColumn:\s*(.+)", body)
        to = re.search(r"toColumn:\s*(.+)", body)
        info["relationships"].append({"from": fr.group(1).strip() if fr else "", "to": to.group(1).strip() if to else "",
                                      "both_directions": "bothDirections" in body})
    model = open(os.path.join(sm, "model.tmdl"), encoding="utf-8").read()
    info["discourage_implicit"] = "discourageImplicitMeasures" in model
    for dp, dns, fns in os.walk(PBIP_DIR):
        dns.sort()
        for fn in sorted(fns):
            full = os.path.join(dp, fn)
            info["files"].append({"path": os.path.relpath(full, PBIP_DIR).replace(os.sep, "/"), "bytes": os.path.getsize(full)})
    return info


def pbip_snippet(rel, max_lines=18):
    lines = open(os.path.join(PBIP_DIR, rel), encoding="utf-8", errors="replace").read().splitlines()
    return "\n".join(lines[:max_lines]) + ("\n..." if len(lines) > max_lines else "")


# ----------------------------------------------------------------------------- messy examples
NON_ITEM = {"_id", "RSN", "YEAR REGISTERED", "YEAR BUILT", "YEAR EVALUATED", "PROPERTY TYPE", "WARD", "WARDNAME",
            "SITE ADDRESS", "CONFIRMED STOREYS", "CONFIRMED UNITS", "EVALUATION COMPLETED ON",
            "CURRENT BUILDING EVAL SCORE", "PROACTIVE BUILDING SCORE", "CURRENT REACTIVE SCORE",
            "NO OF AREAS EVALUATED", "GRID", "LATITUDE", "LONGITUDE", "X", "Y"}


def messy_examples():
    """Six real cells from the raw City files, before and after, found by rule (not typed)."""
    post = os.path.join(RAW, "apartment-building-evaluations-2023-current.csv")
    pre = os.path.join(RAW, "pre-2023-apartment-building-evaluations.csv")
    if not (os.path.exists(post) and os.path.exists(pre)):
        return []
    ex = []
    with open(post, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    items = [c for c in rows[0].keys() if c not in NON_ITEM]
    for r in rows:
        y = r["YEAR EVALUATED"]
        if len(y.strip()) != 4:
            ex.append({"file": "evaluations 2023 to now", "column": "YEAR EVALUATED", "raw": '"%s"' % y,
                       "after": "evaluation year %s, taken from EVALUATION COMPLETED ON (%s); the label is flagged, not dropped"
                                % (r["EVALUATION COMPLETED ON"][:4], r["EVALUATION COMPLETED ON"]), "rule": "year_label"})
            break
    for r in rows:
        hit = next((c for c in items if r[c] != r[c].strip() and r[c].strip()), None)
        if hit:
            ex.append({"file": "evaluations 2023 to now", "column": hit, "raw": '"%s"' % r[hit],
                       "after": '"%s": whitespace trimmed, and the engine counted the repair' % r[hit].strip(), "rule": "trim"})
            break
    for r in rows:
        hit = next((c for c in items if r[c].strip() == "0"), None)
        if hit:
            ex.append({"file": "evaluations 2023 to now", "column": hit, "raw": '"%s"' % r[hit],
                       "after": "refused or blocked: kept as a separate flag in pillar cells (provisional); the City's own score counts it as zero points",
                       "rule": "zero_sentinel"})
            break
    for r in rows:
        hit = next((c for c in items if r[c].strip().upper() == "N/A"), None)
        if hit:
            ex.append({"file": "evaluations 2023 to now", "column": hit, "raw": '"%s"' % r[hit],
                       "after": "not applicable by design (the building has none): left out of the score, not counted as missing",
                       "rule": "not_applicable"})
            break
    for r in rows:
        if not r["LATITUDE"].strip() and r["X"].strip():
            ex.append({"file": "evaluations 2023 to now", "column": "LATITUDE / X", "raw": '"%s" / "%s"' % (r["LATITUDE"], r["X"]),
                       "after": "kept: the ward code places the building; projected X/Y are not converted", "rule": "no_lat_long"})
            break
    with open(pre, encoding="utf-8-sig", newline="") as f:
        prows = list(csv.DictReader(f))
    for r in prows:
        if "CREATED IN ERROR" in r.get("SITE_ADDRESS", ""):
            addr = r["SITE_ADDRESS"].strip()
            marker = addr[:addr.index("CREATED IN ERROR") + len("CREATED IN ERROR")] + (" **" if "ERROR **" in addr else "")
            ex.append({"file": "evaluations before 2023", "column": "SITE_ADDRESS", "raw": '"%s [street address withheld here]"' % marker,
                       "after": "quarantined with its reason, never silently dropped", "rule": "created_in_error"})
            break
    return ex[:6]


# ----------------------------------------------------------------------------- derived figures
def compute(I, C, downloads, pbip, examples, tests, contrast_rows):
    D, SRC_OF = {}, {}

    def d(key, value, source):
        D[key] = value
        SRC_OF[key] = source

    meta, sc, cube = I["rentsafe_meta"], I["rentsafe_scorecard"], I["rentsafe_cube"]
    A, B, Dd = I["rentsafe_analysis_a_points_per_fix"], I["rentsafe_analysis_b_risk"], I["rentsafe_analysis_d_archetypes"]
    FL, T, E = I["forecast_lab"], I["timings"], I["engine_scorecard"]
    k = meta["kpis"]

    # --- the cube re-adds to the published KPIs and to the scorecard
    rows = cube_rows(cube)
    tot = agg(rows)
    C("cube buildings = KPI buildings", tot["n"] == k["buildings"], "%s vs %s" % (tot["n"], k["buildings"]))
    C("cube units = KPI units", tot["units"] == k["units"])
    C("cube mean score = KPI mean", round(tot["score_sum"] / tot["n"], 2) == k["score_mean"])
    C("cube unit-weighted score = KPI", round(tot["score_usum"] / tot["units"], 2) == k["score_unit_weighted"])
    C("cube % green = KPI", round(100.0 * tot["green"] / tot["n"], 2) == k["pct_green"])
    C("cube refusals per 100 = KPI", round(100.0 * tot["refused_evals"] / tot["n"], 2) == k["refusal_evals_per_100"])
    bad = []
    for w in sc["wards"]:
        wr = agg([r for r in rows if r["ward"] == w["ward"]])
        # cells are stored at 6 dp and printed once at 1 dp; the cube's sums are 4 dp, so compare the
        # value within 1e-4 and, what a reader sees, the printed cell exactly
        m_ = wr["score_sum"] / wr["n"]
        if wr["n"] != w["n_buildings"] or abs(m_ - w["overall_mean"]) > 1e-4 or fmt(m_, "fixed:1") != fmt(w["overall_mean"], "fixed:1"):
            bad.append(w["ward"])
        for p in PILLARS:
            if wr[p + "_n"]:
                m_ = wr[p + "_sum"] / wr[p + "_n"]
                if abs(m_ - w[p + "_mean"]) > 1e-4 or fmt(m_, "fixed:1") != fmt(w[p + "_mean"], "fixed:1"):
                    bad.append("%s/%s" % (w["ward"], p))
    C("cube re-adds to every ward cell of the scorecard", not bad, ", ".join(bad[:5]))
    # published cells print half-up (data-fmt "half:1"), as the browser prints a recomputed one;
    # POST_CHECKS below confirms on the rendered table that each cell's text and band come from it
    avg = sum(w["overall_mean"] for w in sc["wards"]) / len(sc["wards"])
    C("City row = simple average of the ward cells", abs(avg - sc["city"]["overall_mean__avg_of_wards"]) < 1e-6)

    # --- engine reconciliation and the messy fixture
    fx = E["fixture"]
    lost = fx["rows"] - fx["after"]["clean"] - fx["after"]["quarantined"]
    C("messy fixture: rows in = clean + quarantined", lost == 0, "lost %d" % lost)
    d("fixture_lost", format(lost, ","), "engine_scorecard fixture.rows - after.clean - after.quarantined")
    d("fixture_quarantine_pct", format(fx["after"]["quarantine_rate_pct"], ".1f") + "%", "engine_scorecard fixture.after.quarantine_rate_pct")
    d("fixture_before_quarantine_pct", format(fx["before"]["quarantine_rate_pct"], ".1f") + "%", "engine_scorecard fixture.before.quarantine_rate_pct")
    bm = fx["after"]["business_measures"]
    d("fixture_rating_change", format(bm["star_rating_change"], "+.3f"), "engine_scorecard fixture.after.business_measures.star_rating_change")
    d("fixture_volume_change", format(bm["volume_change_pct"], "+.1f") + "%", "engine_scorecard fixture.after.business_measures.volume_change_pct")
    tc = fx["after"]["truth_check"]
    d("fixture_truth_volume", format(tc["truth_volume_change_pct"], "+.1f") + "%", "engine_scorecard fixture.after.truth_check")
    d("fixture_truth_rating", format(tc["truth_rating_change"], "+.3f"), "engine_scorecard fixture.after.truth_check")
    fc = fx["after"]["forecast"]
    d("fixture_band_hits", "%d of %d" % (fc["coverage"]["hits"], fc["coverage"]["n"]), "engine_scorecard fixture.after.forecast.coverage")
    d("fixture_mape_h1", format(fc["mape_h1"], ".1f") + "%", "engine_scorecard fixture.after.forecast.mape_h1")
    d("fixture_mape_all", format(fc["mape_all"], ".1f") + "%", "engine_scorecard fixture.after.forecast.mape_all")
    nlc = meta["meta"]["northledger_clean"]["files"]
    C("NorthLedger cleaning of the three City files reconciles", all(f["rows_in"] == f["clean"] + f["quarantined"] for f in nlc))
    d("nl_rows_in", format(sum(f["rows_in"] for f in nlc), ","), "rentsafe_meta meta.northledger_clean.files[].rows_in summed")
    d("nl_clean", format(sum(f["clean"] for f in nlc), ","), "rentsafe_meta meta.northledger_clean.files[].clean summed")
    d("nl_quarantined", format(sum(f["quarantined"] for f in nlc), ","), "rentsafe_meta meta.northledger_clean.files[].quarantined summed")
    C("build quarantine reconciles", all(v["rows_in"] == v["clean"] + v["quarantined"] for v in meta["health"]["reconciliation"].values()))

    # --- analysis A (hero brief and signal cards)
    ct = {t["zero_as"]: t for t in A["constrained_tests"]}
    d("a_zero_rows_share", pct(ct["zero"]["rows_with_a_0_exact_share"]), "analysis_a constrained_tests[zero].rows_with_a_0_exact_share")
    d("a_missing_rows_share", pct(ct["missing"]["rows_with_a_0_exact_share"]), "analysis_a constrained_tests[missing].rows_with_a_0_exact_share")
    d("a_zero_all_share", pct(ct["zero"]["exact_share"]), "analysis_a constrained_tests[zero].exact_share")
    d("a_zero_rows", format(ct["zero"]["rows_with_a_0"], ","), "analysis_a constrained_tests[zero].rows_with_a_0")
    d("a_exact", "%s of %s" % (format(ct["zero"]["exact_matches"], ","), format(ct["zero"]["rows"], ",")), "analysis_a constrained_tests[zero]")
    C("hero RECOMMEND on the zero rule follows its own rule (at least 99% exact on rows with a 0)",
      ct["zero"]["rows_with_a_0_exact_share"] >= 0.99 and A["constrained_winner"] == "zero")
    C("hero RECOMMEND on points per fix follows its own rule (the formula reproduces at least 99% of scores)",
      ct["zero"]["exact_share"] >= 0.99)
    items = sorted(A["points_per_fix"]["items"], key=lambda x: x["rank"])
    d("n_items", format(len(items), ","), "analysis_a points_per_fix.items")
    d("n_pillars", format(len([c for c in sc["columns"] if c["key"] in PILLARS]), ","), "rentsafe_scorecard columns that are pillars")
    d("a_top_item", items[0]["item"], "analysis_a points_per_fix.items rank 1")
    d("a_top_points", format(items[0]["expected_points_per_building"], ".2f"), "analysis_a points_per_fix.items rank 1")
    low3 = sorted(items, key=lambda x: x["avg_score_1_to_3"])[:3]
    d("a_low_items", "; ".join("%s %s" % (x["item"], format(x["avg_score_1_to_3"], ".2f")) for x in low3),
      "analysis_a points_per_fix.items, the three lowest avg_score_1_to_3")
    pvh = A["points_per_fix"]["paperwork_vs_high_risk"]
    d("a_paperwork_scored1", pct(pvh["paperwork_share_scored_1"]), "analysis_a paperwork_vs_high_risk.paperwork_share_scored_1")
    d("a_highrisk_scored1", pct(pvh["high_risk_share_scored_1"]), "analysis_a paperwork_vs_high_risk.high_risk_share_scored_1")
    un = A["unconstrained"]
    d("a_ratio_published", format(un["high_to_cosmetic_ratio_published"], ".0f"), "analysis_a unconstrained.high_to_cosmetic_ratio_published")
    d("a_ratio_recovered", format(un["high_to_cosmetic_ratio_recovered"], ".2f"), "analysis_a unconstrained.high_to_cosmetic_ratio_recovered")
    d("a_ols_test_r2", format(un["test_r2"], ".3f"), "analysis_a unconstrained.test_r2")
    d("a_ols_test_mae", format(un["test_mae_points"], ".2f"), "analysis_a unconstrained.test_mae_points")
    d("a_formula_test_mae", format(un["constrained_same_test_rows_mae_points"], ".2f"), "analysis_a unconstrained.constrained_same_test_rows_mae_points")
    d("a_unexplained", format(A["unexplained_rows"]["count"], ","), "analysis_a unexplained_rows.count")
    pvw = E["rentsafe"]["panel_vs_window"]
    d("panel_mean_change", format(pvw["mean_change_same_building"], "+.1f"), "engine_scorecard rentsafe.panel_vs_window.mean_change_same_building")
    d("panel_share_improved", pct(pvw["share_improved"]), "engine_scorecard rentsafe.panel_vs_window.share_improved")
    d("panel_share_jun_nov", pct(pvw["share_of_evaluations_jun_to_nov"]), "engine_scorecard rentsafe.panel_vs_window.share_of_evaluations_jun_to_nov")
    # the same change, split by where each pair started: the regression-to-the-middle evidence
    sb = pvw.get("by_start_band") or []
    C("the same-building change is split by starting band, and the bands hold every pair",
      len(sb) >= 2 and sum(b["n"] for b in sb) == pvw["pairs"], "%s bands, %s pairs of %s" % (len(sb), sum(b["n"] for b in sb), pvw["pairs"]))
    if sb:
        lo_b, hi_b = sb[0], sb[-1]
        for tag, b in (("lo", lo_b), ("hi", hi_b)):
            d("band_%s_name" % tag, b["band"], "engine_scorecard rentsafe.panel_vs_window.by_start_band[%s].band" % ("0" if tag == "lo" else "-1"))
            d("band_%s_change" % tag, format(b["mean_change"], "+.1f"), "same band, mean_change")
            d("band_%s_n" % tag, format(b["n"], ","), "same band, n")
            d("band_%s_improved" % tag, pct(b["share_improved"]), "same band, share_improved")

    # --- analysis B
    tm = B["test_metrics"]
    beats = tm["brier_model"] < tm["brier_persistence_band"] and tm["mae_score_model"] < tm["mae_score_persistence"]
    C("analysis B gate verdict follows from its metrics", (B["gate"]["champion"] == "model") == beats)
    d("b_coverage", pct(tm["interval80_model"]["coverage_test"]), "analysis_b test_metrics.interval80_model.coverage_test")
    d("b_coverage_ci", "%s to %s" % (pct(tm["interval80_model"]["wilson95"][0]), pct(tm["interval80_model"]["wilson95"][1])), "analysis_b interval80_model.wilson95")
    d("b_pos_rate", pct(B["pairs"]["test_positive_rate"]), "analysis_b pairs.test_positive_rate")
    d("b_champion", B["gate"]["champion"], "analysis_b gate.champion")
    nx_word = next_evaluation_claim(B, lambda ref, fm=None: ref)[1]
    C("hero 'What is next' badge follows analysis B's gate (RECOMMEND only when the model beat both baselines)",
      B["gate"]["champion"] in ("model", "persistence") and (nx_word == "RECOMMEND") == (B["gate"]["champion"] == "model"))

    # --- analysis D
    ar = Dd["archetypes"]
    shape = ar["shape_variant"]
    lag = min(shape["clusters"], key=lambda c: min(c["deviation_from_own_mean"].values()))
    d("d_lag_n", format(lag["n_buildings"], ","), "analysis_d shape_variant: the cluster with the deepest lagging pillar")
    d("d_lag_pillar", lag["lagging_pillar"], "same cluster, lagging_pillar")
    d("d_lag_gap", format(abs(lag["deviation_from_own_mean"][lag["lagging_pillar"]]), ".1f"), "same cluster, deviation_from_own_mean, as a distance below the building's own average")
    d("d_lag_green", format(lag["pct_green"], ".1f") + "%", "same cluster, pct_green")
    sil = {r["k"]: r["silhouette_on_1500_sample"] for r in shape["k_search"]}
    d("d_lag_silhouette", format(sil[shape["k_chosen"]], ".2f"), "analysis_d shape_variant.k_search at k_chosen")
    d("d_k_level", format(ar["k_chosen"], ","), "analysis_d archetypes.k_chosen")
    cc = Dd["control_chart"]
    d("d_flags", format(sum(1 for p in cc["points"] if p["flag"] and p["flag"] != "low n"), ","), "analysis_d control_chart.points with a flag other than low n")
    d("d_months", format(cc["eligible_months"], ","), "analysis_d control_chart.eligible_months (months with enough evaluations to be flagged)")
    C("the control chart's flags fall only on months it can judge", sum(1 for p in cc["points"] if p["flag"] and p["flag"] != "low n") <= cc["eligible_months"])
    d("d_rule_agreement", pct(shape["one_line_rule"]["agreement"]), "analysis_d shape_variant.one_line_rule.agreement")

    # --- forecast lab
    bt = FL["backtest"]
    C("forecast champion is the top of its own ranking", bt["ranking"][0] == bt["champion"])
    rs = bt.get("realtime_selection") or {}
    C("the Forecast Lab measures its selection effect (re-picked at each origin from errors known then)",
      rs.get("mape") is not None and FL["display"].get("realtime_selection_mape") == format(rs["mape"], ".2f") + "%",
      "realtime_selection %s, display %s" % (rs.get("mape"), FL["display"].get("realtime_selection_mape")))
    d("fl_band_nominal", format(bt["coverage"]["nominal"] * 100, ".0f") + "%", "forecast_lab backtest.coverage.nominal")

    # --- replay manifest
    M = I["manifest"]
    C("replay manifest present in agent-demo.html", M is not None)
    if M:
        s4 = [s for s in M["sessions"] if s["id"].startswith("s4")]
        C("the NYC 311 session is archived and not featured", all((not s["featured"]) and s["archived"] for s in s4))
        C("featured count matches the session list", M["counts"]["featured"] == sum(1 for s in M["sessions"] if s["featured"]))
        ev = M["evidence"]
        d("replay_sessions", format(M["counts"]["sessions"], ","), "agent-demo.html demo-manifest counts.sessions")
        d("replay_featured", format(M["counts"]["featured"], ","), "demo-manifest counts.featured")
        d("replay_archived", format(M["counts"]["archived"], ","), "demo-manifest counts.archived")
        d("replay_q_reproduced", format(ev["audit_queries_reproduced"], ","), "demo-manifest evidence.audit_queries_reproduced")
        d("replay_q_checked", format(ev["audit_queries_checked"], ","), "demo-manifest evidence.audit_queries_checked")
        d("replay_traced", format(ev["audit_numbers_traced"], ","), "demo-manifest evidence.audit_numbers_traced")
        d("replay_stated", format(ev["audit_numbers_stated"], ","), "demo-manifest evidence.audit_numbers_stated")
        d("replay_trace_pct", pct(ev["audit_traceability"]), "demo-manifest evidence.audit_traceability")
        d("replay_model", ", ".join(M["models"]), "demo-manifest models")
        d("s5_stated", format(M["s5_correction"]["stated"], ","), "demo-manifest s5_correction.stated")
        d("s5_correct", format(M["s5_correction"]["correct"], ","), "demo-manifest s5_correction.correct")
        d("s5_parts", " + ".join(format(v, ",") for v in M["s5_correction"]["parts"].values()), "demo-manifest s5_correction.parts")
        picks = [s for s in M["sessions"] if s["featured"] and not s["id"].startswith("s4")]
        chosen = []
        for want in ("s1-audit", "s3-forecast", "s5-rdw-fleet"):
            chosen += [s for s in picks if s["id"] == want]
        C("three featured replays found for the teaser", len(chosen) == 3)
        for i, s in enumerate(chosen[:3]):
            d("rp%d_title" % i, s["title"], "demo-manifest sessions[%s].title" % s["id"])
            d("rp%d_turns" % i, "%d turns, %d tool calls" % (s["turns"], s["tool_calls"]), "demo-manifest sessions[%s]" % s["id"])
            kinds = sorted({fl["name"].rsplit(".", 1)[-1].upper() for fl in s["report_files"]})
            d("rp%d_reports" % i, ", ".join(kinds), "demo-manifest sessions[%s].report_files" % s["id"])
            d("rp%d_db" % i, s["database"], "demo-manifest sessions[%s].database" % s["id"])
            d("rp%d_id" % i, s["id"], "demo-manifest sessions[%s].id (the replay link opens this session)" % s["id"])

    # --- engine tests receipt
    if tests and tests.get("tests"):
        d("tests_total", format(tests["tests"], ","), "data/engine_tests.json tests")
        d("tests_suites", format(tests["suites"], ","), "data/engine_tests.json suites")
        d("tests_ran_at", utc_label(tests["ran_at"]), "data/engine_tests.json ran_at")
        d("tests_state", "all green" if tests["green"] else "NOT green", "data/engine_tests.json green")
        d("tests_snapshot", (tests["engine_snapshot"]["id"] or "")[:12], "data/engine_tests.json engine_snapshot.id")
    else:
        for kk in ("tests_total", "tests_suites", "tests_ran_at", "tests_state", "tests_snapshot"):
            d(kk, "[unrun]", "data/engine_tests.json missing: run build.py --tests")
    snap = engine_snapshot()
    # the receipts must be for the engine code on disk: a test run or a timed run on older code
    # would put two different engines on one page
    C("the engine test receipt (engine_tests.json) is for the engine code on disk",
      bool(tests) and (tests.get("engine_snapshot") or {}).get("id") == snap,
      "tests on %s, engine now %s: run python3 build.py --tests" % (((tests or {}).get("engine_snapshot") or {}).get("id", "none")[:12], (snap or "none")[:12]))
    C("the timed run (timings.json) is for the engine code on disk", T["engine"]["id"] == snap,
      "timed on %s, engine now %s: re-run the recorded engagement" % (T["engine"]["id"][:12], (snap or "none")[:12]))
    d("engine_now", (snap or "[not found]")[:12], "sha256 of ../northledger-core/northledger/*.py at build time")
    d("engine_timed", T["engine"]["id"][:12], "timings.json engine.id")
    d("engine_same", "the same code" if snap == T["engine"]["id"] else "different code: the engine changed after the timed run",
      "comparison of the two ids above")

    # --- timings
    rc = T["runs"]["rentsafe_cli"]
    d("run_total", format(rc["total_seconds"], ".1f") + " s", "timings runs.rentsafe_cli.total_seconds (wall clock)")
    d("run_stage_sum", format(sum(s["seconds"] for s in rc["stages"]), ".1f") + " s", "timings runs.rentsafe_cli.stages[].seconds summed (the replay clock's total)")
    C("the stages of the recorded run fit inside its wall clock", sum(s["seconds"] for s in rc["stages"]) <= rc["total_seconds"] + 0.05)
    d("run_stage_count", format(len(rc["stages"]), ","), "timings runs.rentsafe_cli.stages")
    d("run_date", utc_label(rc["date"]), "timings runs.rentsafe_cli.date")
    d("run_peak", format(rc["peak_rss_mb"], ".0f") + " MB", "timings runs.rentsafe_cli.peak_rss_mb")
    mc = T["machine"]
    d("machine", "%s, %d CPU cores, %.0f GB memory, Python %s" % (mc["machine"], mc["cpu_count"], mc["memory_gb"], mc["python"]), "timings machine")
    for i, s in enumerate(rc["stages"]):
        d("st%d_sec" % i, format(s["seconds"], ".2f") + " s", "timings runs.rentsafe_cli.stages[%d].seconds" % i)
        d("st%d_rows" % i, format(s["rows"], ",") if s.get("rows") is not None else "", "timings stages[%d].rows" % i)
        d("st%d_mem" % i, (format(s["peak_rss_mb"], ".0f") + " MB") if s.get("peak_rss_mb") is not None else "", "timings stages[%d].peak_rss_mb" % i)
        d("st%d_exit" % i, "ok" if s.get("exit_code") == 0 else "exit %s" % s.get("exit_code"), "timings stages[%d].exit_code" % i)
    C("every stage of the timed RentSafeTO run exited cleanly", all(s.get("exit_code") == 0 for s in rc["stages"]))
    for i, st in enumerate(E["stages"]):
        for col in ("fixture_before", "fixture_after", "rentsafe"):
            s = st[col].get("seconds")
            d("es%d_%s_sec" % (i, col), (format(s, ".2f") + " s") if isinstance(s, (int, float)) else "",
              "engine_scorecard stages[%d].%s.seconds" % (i, col))

    # --- sources
    d("n_city_files", format(len(meta["meta"]["sources"]), ","), "rentsafe_meta meta.sources (the City files build_rentsafe.py reads)")
    for i, s in enumerate(meta["meta"]["sources"]):
        d("src%d_bytes" % i, human_bytes(s["bytes"]), "rentsafe_meta meta.sources[%d].bytes" % i)
        d("src%d_sha" % i, s["sha256"][:16], "rentsafe_meta meta.sources[%d].sha256, first 16" % i)
        d("src%d_at" % i, utc_label(s["retrieved_at"]), "rentsafe_meta meta.sources[%d].retrieved_at" % i)
    for i, key in enumerate(("RSAFSNA", "RSAFS")):
        s = I["fred_sources"]["series"][key]
        d("fred%d_bytes" % i, human_bytes(s["bytes"]), "data/fred/SOURCES.json series.%s.bytes" % key)
        d("fred%d_sha" % i, s["sha256"][:16], "data/fred/SOURCES.json series.%s.sha256, first 16" % key)
        d("fred%d_at" % i, utc_label(s["completed_at"]), "data/fred/SOURCES.json series.%s.completed_at" % key)
        d("fred%d_updated" % i, s["last_modified_header"], "data/fred/SOURCES.json series.%s.last_modified_header" % key)
        d("fred%d_label" % i, s["label"], "data/fred/SOURCES.json series.%s.label" % key)
        d("fred%d_agency" % i, s["source_agency"], "data/fred/SOURCES.json series.%s.source_agency" % key)
    for rel, v in downloads.items():
        key = DOWNLOAD_KEY[rel]
        d(key + "_size", human_bytes(v["bytes"]), "%s file size" % rel)
        d(key + "_sha", v["sha256"][:16], "%s sha256, first 16" % rel)

    # --- PBIP facts
    if pbip["present"]:
        d("pbip_tables", format(len(pbip["tables"]), ","), "TMDL tables in the exported PBIP")
        d("pbip_measures", format(len(pbip["measures"]), ","), "measures in the exported TMDL")
        d("pbip_measures_desc", format(sum(1 for m in pbip["measures"] if m["description"]), ","), "measures carrying a /// description")
        d("pbip_measures_fmt", format(sum(1 for m in pbip["measures"] if m["format"]), ","), "measures carrying a formatString")
        d("pbip_rels", format(len(pbip["relationships"]), ","), "relationships.tmdl")
        d("pbip_bidir", format(sum(1 for r in pbip["relationships"] if r["both_directions"]), ","), "relationships with bothDirections")
        d("pbip_files", format(len(pbip["files"]), ","), "files in the PBIP folder")
        bad311 = [m["name"] for m in pbip["measures"] if re.search(r"311|complaint|service request|agency", m["name"], re.I)]
        C("no NYC 311 measure names in the RentSafeTO export", not bad311, ", ".join(bad311))
        for i, fl in enumerate(pbip["files"]):
            d("pf%d_size" % i, human_bytes(fl["bytes"]) if fl["bytes"] >= 1000 else format(fl["bytes"], ",") + " B", "PBIP file size")

    # --- contrast
    worst = min(r["ratio"] for r in contrast_rows)
    C("every text colour reaches 4.5:1 on its surface in both themes", worst >= 4.5, "worst %.2f" % worst)
    d("contrast_worst", format(worst, ".2f") + ":1", "computed at build from the colour tokens (WCAG 2.x)")
    d("contrast_pairs", format(len(contrast_rows), ","), "text/surface pairs checked at build")

    # --- messy examples
    for i, e in enumerate(examples):
        d("ex%d_raw" % i, e["raw"], "raw City file, column %s" % e["column"])
        d("ex%d_after" % i, e["after"], "rule %s" % e["rule"])
        d("ex%d_col" % i, e["column"], "column name")
        d("ex%d_file" % i, e["file"], "file")
    d("n_examples", format(len(examples), ","), "messy examples found in the raw files")
    d("audit_ai_calls", "none" if T.get("ai_model_called") is False else "see timings.json", "timings.json ai_model_called")
    reg = meta["health"]["registration"]
    gap = (_dt.date.fromisoformat(meta["meta"]["data_as_of"][:10]) - _dt.date.fromisoformat(reg["ckan_last_refreshed"][:10])).days
    d("reg_fresh_gap", format(gap, ","), "days from rentsafe_meta health.registration.ckan_last_refreshed to meta.data_as_of")
    # the PBIP's own grain, read from the exported data, so the page can say how it differs from the report
    rows_, dates_, tot_ = 0, [], 0.0
    cpath = os.path.join(PBIP_DIR, "data", "clean.csv")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8", newline="") as fh:
            for r_ in csv.DictReader(fh):
                rows_ += 1
                dates_.append(r_["evaluation_date"][:10])
                tot_ += float(r_["current_score"])
    C("the PBIP export holds its evaluation rows", rows_ > 0, "PBIP data/clean.csv")
    if rows_:
        d("pbip_rows", format(rows_, ","), "rows in the exported PBIP data/clean.csv")
        d("pbip_first", min(dates_), "earliest evaluation_date in the exported PBIP data/clean.csv")
        d("pbip_last", max(dates_), "latest evaluation_date in the exported PBIP data/clean.csv")
        d("pbip_mean", format(tot_ / rows_, ".1f"), "mean current_score over the exported PBIP data/clean.csv")
    return D, SRC_OF


# ----------------------------------------------------------------------------- template engine
TAG = re.compile(r"\{\{\s*(\w+)\s+([^}]*?)\s*\}\}")


class Renderer:
    def __init__(self, sources, display, config):
        self.sources, self.display, self.config = sources, display, config
        self.blocks = {}
        self.errors = []

    def fact(self, ref, fm=None, cls=None):
        src, _, path = ref.partition(":")
        try:
            v = check_site.resolve(self.sources[src], path)
        except Exception:
            self.errors.append("no value at %s" % ref)
            return "[missing %s]" % esc(ref)
        if isinstance(v, (dict, list)) or v is None:
            self.errors.append("%s is not a single value" % ref)
            return "[missing]"
        if isinstance(v, bool):
            self.errors.append("%s is a boolean; bind a display string" % ref)
            return "[bool]"
        if isinstance(v, (int, float)) and not fm:
            self.errors.append("%s is a number and needs a format" % ref)
            return "[fmt]"
        text = fmt(v, fm) if isinstance(v, (int, float)) else v
        attrs = ' data-fact="%s"' % esc(ref)
        if fm:
            attrs += ' data-fmt="%s"' % esc(fm)
        if cls:
            attrs += ' class="%s"' % cls
        return "<span%s>%s</span>" % (attrs, esc(text))

    def disp(self, key, cls=None):
        if key not in self.display:
            self.errors.append("no derived figure %r" % key)
            return "[missing %s]" % esc(key)
        return self.fact("site_build:display." + key, cls=cls)

    def sub(self, text):
        def rep(m):
            kind, arg = m.group(1), m.group(2)
            if kind == "f":
                ref, _, fm = arg.partition("|")
                return self.fact(ref.strip(), fm.strip() or None)
            if kind == "d":
                return self.disp(arg)
            if kind == "r":
                if arg not in self.blocks:
                    self.errors.append("no block %r" % arg)
                    return ""
                return self.blocks[arg]
            if kind == "c":
                v = self.config
                for kk in arg.split("."):
                    v = v[kk]
                return esc(str(v))
            self.errors.append("unknown tag %s" % kind)
            return ""
        return TAG.sub(rep, text)


# ----------------------------------------------------------------------------- HTML blocks
def band_of(v):
    """Sign band and shade of a cell, from the value as printed (1 dp, half-up), so a cell that
    prints 85.0 is green even when its stored value is 84.96."""
    v = float(half_up(v, 1))
    if v >= 85:
        return "green", (3 if v >= 95 else 2 if v >= 90 else 1)
    if v >= 70:
        return "yellow", (3 if v >= 80 else 2 if v >= 75 else 1)
    return "red", (3 if v >= 60 else 2 if v >= 50 else 1)


GLYPH = {"green": "●", "yellow": "◐", "red": "○"}
BADGE_ICON = {
    "RECOMMEND": '<svg class="bi" aria-hidden="true" viewBox="0 0 16 16"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    "WATCH": '<svg class="bi" aria-hidden="true" viewBox="0 0 16 16"><path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>',
}


def badge(word, why):
    return '<span class="badge b-%s">%s%s</span><span class="why">%s</span>' % (word.lower(), BADGE_ICON[word], word, esc(why))


def engine_status(word):
    w = (word or "").lower()
    if w.startswith("works"):
        return '<span class="status s-built">%s</span>' % esc(word)
    if w.startswith("partial"):
        return '<span class="status s-partial">%s</span>' % esc(word)
    if w == "broken":
        return '<span class="status s-broken">%s</span>' % esc(word)
    return '<span class="status s-roadmap">%s</span>' % esc(word or "missing")


def next_evaluation_claim(Bk, f):
    """The hero's 'What is next' sentence and badge, built from analysis B's gate and metrics, so a
    rebuild that flips the gate or a metric changes the words with it."""
    x = Bk["test_metrics"]
    tm = "rentsafe_analysis_b_risk:test_metrics."
    champ = Bk["gate"]["champion"]
    closer = x["mae_score_model"] < x["mae_score_persistence"]
    flags_better = x["brier_model"] < x["brier_persistence_band"]
    mae = ("predicted the next score more closely" if closer else "missed the next score by more") + \
        " (average miss %s points, against %s for repeating the last score)" % (f(tm + "mae_score_model", "fixed:2"), f(tm + "mae_score_persistence", "fixed:2"))
    brier = ("better" if flags_better else "no better") + \
        " at flagging which buildings would fall below green (Brier score %s against %s for the band's past rate; lower is better)" % (
            f(tm + "brier_model", "fixed:4"), f(tm + "brier_persistence_band", "fixed:4"))
    joint = " and it was " if closer == flags_better else " but it was "
    if champ == "model":
        text = ("For the next evaluation, our model passed the rule fixed before the results were seen: it beat simple baselines on both measures. "
                "It %s,%s%s." % (mae, joint, brier))
        word, why = "RECOMMEND", "The model beat the simple baselines on both measures out of sample (Analysis B)."
    else:
        text = ("For the next evaluation, our model failed the rule fixed before the results were seen: to be offered, it had to beat simple "
                "baselines on both measures. It %s,%s%s, so this page makes no prediction for individual buildings. The last score plus the usual "
                "change for its band misses by %s points." % (mae, joint, brier, f(tm + "mae_score_persistence_plus_band_mean_change", "fixed:2")))
        word, why = "WATCH", "The model did not beat the simple baselines on both measures out of sample, so it is not offered as a forecast."
    return text, word, why


def blocks(I, R, D, pbip, examples):
    B = {}
    sc, meta, A = I["rentsafe_scorecard"], I["rentsafe_meta"], I["rentsafe_analysis_a_points_per_fix"]
    Bk, Dd, FL, T, E = I["rentsafe_analysis_b_risk"], I["rentsafe_analysis_d_archetypes"], I["forecast_lab"], I["timings"], I["engine_scorecard"]
    f, dd = R.fact, R.disp
    cols = [c for c in sc["columns"] if c["key"] in PILLARS]
    label_of = {c["key"]: c["label"] for c in cols}

    def lst(src, path, n):
        return "".join("<li>%s</li>" % f("%s:%s.%d" % (src, path, i)) for i in range(n))

    # the replay teaser links open the session each card describes (agent-demo.html#<session>)
    for i in range(3):
        B["rp%d_href" % i] = "agent-demo.html#" + esc(D.get("rp%d_id" % i, ""))

    # ---------------- the same-building change split by starting band (regression to the middle)
    pv = E["rentsafe"]["panel_vs_window"]
    sb = pv.get("by_start_band") or []
    if sb:
        lo_i, hi_i = 0, len(sb) - 1
        mid_ = sb[lo_i]["mean_change"] > 0 > sb[hi_i]["mean_change"]
        split = ("Split by each pair's first score: pairs that started %s moved %s points on average (%s pairs, %s higher), and pairs that started at %s moved %s (%s pairs, %s higher)" % (
            dd("band_lo_name"), dd("band_lo_change"), dd("band_lo_n"), dd("band_lo_improved"),
            dd("band_hi_name"), dd("band_hi_change"), dd("band_hi_n"), dd("band_hi_improved")))
        # the score scale's ceiling, read from the City's band table (the top of the green band)
        cap_i = max(range(len(I["rentsafe_scorecard"]["bands"])), key=lambda j: I["rentsafe_scorecard"]["bands"][j]["max"])
        split += ((". Low starters rising while high starters fall is the pattern regression toward the middle produces (the %s-point cap "
                   "also limits high starters), so part of the average may not be repair.") % f("rentsafe_scorecard:bands.%d.max" % cap_i, "int")
                  if mid_ else ".")
        B["band_split"] = split
        B["band_split_short"] = "By first score, pairs that started %s moved %s on average and those that started at %s moved %s%s" % (
            dd("band_lo_name"), dd("band_lo_change"), dd("band_hi_name"), dd("band_hi_change"),
            ", the pattern regression toward the middle produces, so part of the rise may not be repair." if mid_ else ".")
    else:
        B["band_split"] = B["band_split_short"] = ""

    # ---------------- hero brief card
    nx_text, nx_word, nx_why = next_evaluation_claim(Bk, f)
    B["_next_word"] = nx_word
    B["next_eval"] = nx_text
    B["brief"] = "".join([
        '<div class="bc-item"><h2>What changed</h2><p>Same building, next evaluation: the City\'s proactive score changed by %s points on average over %s repeat evaluations (pairs of evaluations of the same building), and %s of them scored higher. %s</p>%s</div>' % (
            dd("panel_mean_change"), f("engine_scorecard:rentsafe.panel_vs_window.pairs", "int"), dd("panel_share_improved"), B["band_split_short"],
            badge("WATCH", "A real average rise over thousands of pairs, but it cannot yet be split between repairs, the second round of a new scoring tool, and low scores drifting back toward the middle: watch, do not act.")),
        '<div class="bc-item"><h2>What it means</h2><p>An area the officer cannot enter costs full marks. Counting a refused item as zero reproduces %s of the %s affected City scores exactly; leaving it out reproduces %s.</p>%s</div>' % (
            dd("a_zero_rows_share"), dd("a_zero_rows"), dd("a_missing_rows_share"),
            badge("RECOMMEND", "Reproduces the City's own published scores (rule: nearly every affected score matches exactly, at least ninety-nine in a hundred).")),
        '<div class="bc-item"><h2>What to do</h2><p>Book access to every area before evaluation day, then work down the points-per-fix list. The top item, %s, is worth %s points per building on average.</p>%s</div>' % (
            dd("a_top_item"), dd("a_top_points"),
            badge("RECOMMEND", "Uses the City's own weights, which reproduce the published score on nearly every evaluation.")),
        '<div class="bc-item"><h2>What is next</h2><p>%s</p>%s</div>' % (nx_text, badge(nx_word, nx_why)),
    ])

    # ---------------- heatmap
    th = ['<th scope="col" class="c-ward"><button type="button" data-sort="ward">Ward</button></th>']
    for c in cols:
        th.append('<th scope="col" data-col="%s"><button type="button" data-sort="%s">%s</button></th>' % (c["key"], c["key"], esc(c["label"])))
    th += ['<th scope="col" class="c-ov" data-col="overall"><button type="button" data-sort="overall">Overall (City score)</button></th>',
           '<th scope="col"><button type="button" data-sort="green">Share green</button></th>',
           '<th scope="col"><button type="button" data-sort="refusals">Refusals per hundred</button></th>',
           '<th scope="col"><button type="button" data-sort="rank">Rank</button></th>']

    def cell(ref, v, col):
        band, shade = band_of(v)
        return ('<td class="hc h-%s-%d" data-col="%s" data-v="%s" data-band="%s"><span class="cv">%s</span><span class="gl" aria-hidden="true">%s</span>'
                '<span class="sr"> %s</span></td>') % (band, shade, col, v, band, f(ref, "half:1"), GLYPH[band], band)

    body = []
    for i, w in enumerate(sc["wards"]):
        r = ['<tr class="hw" data-ward="%s" data-district="%s">' % (w["ward"], esc(w["district"])),
             '<th scope="row" class="c-ward"><button type="button" class="ward-btn" aria-haspopup="dialog"><span class="wcode">%s</span> %s</button></th>' % (
                 f("rentsafe_scorecard:wards.%d.ward" % i), f("rentsafe_scorecard:wards.%d.ward_name" % i))]
        for c in cols:
            r.append(cell("rentsafe_scorecard:wards.%d.%s_mean" % (i, c["key"]), w[c["key"] + "_mean"], c["key"]))
        r.append(cell("rentsafe_scorecard:wards.%d.overall_mean" % i, w["overall_mean"], "overall"))
        r.append('<td class="num" data-col="green" data-v="%s">%s</td>' % (w["pct_green"], f("rentsafe_scorecard:wards.%d.pct_green" % i, "pct:0")))
        r.append('<td class="num" data-col="refusals" data-v="%s">%s</td>' % (w["refusal_evals_per_100"], f("rentsafe_scorecard:wards.%d.refusal_evals_per_100" % i, "fixed:1")))
        r.append('<td class="num" data-col="rank" data-v="%s">%s</td></tr>' % (w["rank"], f("rentsafe_scorecard:wards.%d.rank" % i, "int")))
        body.append("".join(r))
    foot = []
    for ref, g in [("districts.%d" % i, g) for i, g in enumerate(sc["districts"])] + [("city", sc["city"])]:
        is_city = ref == "city"
        r = ['<tr class="hg%s" data-group="%s">' % (" hcity" if is_city else "", esc(g["label"])),
             '<th scope="row" class="c-ward">%s<span class="gnote">%s</span></th>' % (
                 f("rentsafe_scorecard:%s.label" % ref), ("average of the %s ward cells" % f("rentsafe_scorecard:city.n_wards", "int")) if is_city else "average of its wards")]
        for c in cols:
            key = "%s_mean__avg_of_wards" % c["key"]
            r.append(cell("rentsafe_scorecard:%s.%s" % (ref, key), g[key], c["key"]))
        r.append(cell("rentsafe_scorecard:%s.overall_mean__avg_of_wards" % ref, g["overall_mean__avg_of_wards"], "overall"))
        r.append('<td class="num" data-col="green">%s</td>' % f("rentsafe_scorecard:%s.pct_green__avg_of_wards" % ref, "pct:0"))
        r.append('<td class="num" data-col="refusals">%s</td>' % f("rentsafe_scorecard:%s.refusal_evals_per_100__avg_of_wards" % ref, "fixed:1"))
        r.append('<td class="num" data-col="rank"><span class="muted">not ranked</span></td></tr>')
        foot.append("".join(r))
    B["heatmap_table"] = (
        '<table class="hm" id="hm"><caption class="sr">Toronto wards by building-health pillar: the average of each ward\'s buildings, '
        'latest City evaluation of each, with district and City rows. Each cell prints its score and its City sign band.</caption>'
        '<thead><tr>%s</tr></thead><tbody id="hm-body">%s</tbody><tbody class="hm-groups" id="hm-groups">%s</tbody></table>'
        % ("".join(th), "".join(body), "".join(foot)))
    B["heat_legend"] = "".join(
        '<span class="lg"><span class="sw h-%s-2" aria-hidden="true">%s</span>%s %s to %s</span>'
        % (b["band"], GLYPH[b["band"]], b["band"].capitalize(), f("rentsafe_scorecard:bands.%d.min" % i, "int"),
           f("rentsafe_scorecard:bands.%d.max" % i, "int")) for i, b in enumerate(sc["bands"]))
    k_ = "rentsafe_meta:kpis."
    B["city_note"] = ("The City row averages the %s ward cells, each ward counted once, so it differs from the building-level figures in the report above: "
                      "simple mean %s, unit-weighted %s, %s in the green band and %s evaluations with a refusal per hundred." % (
                          f("rentsafe_scorecard:city.n_wards", "int"), f(k_ + "score_mean", "fixed:1"), f(k_ + "score_unit_weighted", "fixed:1"),
                          f(k_ + "pct_green", "pct:1"), f(k_ + "refusal_evals_per_100", "fixed:2")))
    B["bands_url"] = esc(sc["bands_source"])
    B["heat_method"] = "".join("<li>%s</li>" % f("rentsafe_scorecard:method.%s" % kk) for kk in sc["method"])
    B["heat_limits"] = lst("rentsafe_scorecard", "limits", len(sc["limits"]))

    # ---------------- slicers (choices are the cube's own values)
    cube = I["rentsafe_cube"]
    F = cube["fields"]
    vals = {kk: sorted({r[F.index(kk)] for r in cube["rows"]}) for kk in ("ward", "property_type", "eval_year")}
    names = {w["ward"]: w["ward_name"] for w in sc["wards"]}
    dists = sorted(set(cube["district_of_ward"].values()))

    def sel(id_, label, opts, dim):
        o = '<option value="">All</option>' + "".join('<option value="%s">%s</option>' % (esc(v), esc(t)) for v, t in opts)
        return '<label class="slicer" for="%s"><span>%s</span><select id="%s" data-dim="%s">%s</select></label>' % (id_, label, id_, dim, o)
    B["slicers"] = "".join([
        sel("sl-district", "District", [(x, x) for x in dists], "district"),
        sel("sl-ward", "Ward", [(w, "%s %s" % (w, names.get(w, ""))) for w in vals["ward"]], "ward"),
        sel("sl-year", "Year of latest evaluation", [(y, y) for y in vals["eval_year"]], "eval_year"),
        sel("sl-ptype", "Property type", [(p, "TCHC" if p == "TCHC" else p.title()) for p in vals["property_type"]], "property_type"),
        sel("sl-band", "Highlight a sign band", [("green", "Green"), ("yellow", "Yellow"), ("red", "Red")], "band"),
    ])

    # ---------------- KPI cards (defaults bound to rentsafe_meta; the browser recomputes them for filters)
    k = "rentsafe_meta:kpis."
    B["kpis"] = "".join([
        '<div class="kpi" data-kpi="buildings"><div class="k-lab">Buildings</div><div class="k-val">%s</div><div class="k-sub">latest City evaluation of each</div></div>' % f(k + "buildings", "int"),
        '<div class="kpi" data-kpi="units"><div class="k-lab">Units covered</div><div class="k-val">%s</div><div class="k-sub">confirmed units in those buildings</div></div>' % f(k + "units", "int"),
        '<div class="kpi" data-kpi="score"><div class="k-lab">Average City score, unit-weighted</div><div class="k-val">%s</div><div class="k-sub">simple mean %s</div></div>' % (f(k + "score_unit_weighted", "fixed:1"), f(k + "score_mean", "fixed:1")),
        '<div class="kpi" data-kpi="green"><div class="k-lab">Green sign</div><div class="k-val">%s</div><div class="k-sub">yellow %s, red %s</div><div class="bandbar" aria-hidden="true"></div></div>' % (f(k + "pct_green", "pct:1"), f(k + "pct_yellow", "pct:1"), f(k + "pct_red", "pct:1")),
        '<div class="kpi" data-kpi="refusals"><div class="k-lab">Evaluations with a refused or blocked area, per hundred</div><div class="k-val">%s</div><div class="k-sub">an area the officer cannot enter scores zero</div></div>' % f(k + "refusal_evals_per_100", "fixed:2"),
        '<div class="kpi kpi-city" data-kpi="audit"><div class="k-lab">Audit zone, %s <span class="cw">citywide, not filtered</span></div><div class="k-val">%s</div><div class="k-sub">of %s evaluations that year at or below the cutoff score of %s (proactive score only)</div></div>' % (
            f(k + "audit_zone.year", "raw"), f(k + "audit_zone.evaluations_at_or_below_cutoff", "int"), f(k + "audit_zone.evaluations_that_year", "int"), f(k + "audit_zone.p2_5_cutoff_proactive", "fixed:0")),
        '<div class="kpi kpi-city" data-kpi="due"><div class="k-lab">Evaluation due within ninety days <span class="cw">citywide, not filtered</span></div><div class="k-val">%s</div><div class="k-sub">last evaluation plus two years; %s more are already past that mark</div></div>' % (
            f(k + "due_next_90_days_from_as_of.count", "int"), f(k + "due_next_90_days_from_as_of.overdue_count", "int")),
    ])
    B["audit_note"] = f(k + "audit_zone.note")
    B["due_note"] = f(k + "due_next_90_days_from_as_of.overdue_note")

    # ---------------- points per fix table
    items = A["points_per_fix"]["items"]
    order = sorted(range(len(items)), key=lambda i: items[i]["rank"])
    rows = []
    for i in order:
        it = items[i]
        p = "rentsafe_analysis_a_points_per_fix:points_per_fix.items.%d." % i
        rows.append('<tr data-pillar="%s"><td>%s</td><td>%s</td><td data-sort-value="%s">%s <span class="muted">(%s)</span></td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td></tr>' % (
            it["pillar"], f(p + "item"), esc(label_of[it["pillar"]]), esc(str(it["weight_pct"])), f(p + "tier"), f(p + "weight_pct", "fixed:1"),
            f(p + "avg_score_1_to_3", "fixed:2"), f(p + "share_below_3", "fixed:2"),
            f(p + "expected_points_per_building", "fixed:2"), f(p + "access_points_per_building", "fixed:2")))
    B["ppf_table"] = ('<table class="dt sortable" id="ppf"><caption class="sr">All items, ranked by expected points per building</caption><thead><tr>'
                      '<th scope="col"><button type="button">Item</button></th><th scope="col"><button type="button">Pillar</button></th>'
                      '<th scope="col"><button type="button" data-sort-num>Tier (weight)</button></th><th scope="col"><button type="button" data-num>Average score, of three</button></th>'
                      '<th scope="col"><button type="button" data-num>Share below full marks</button></th>'
                      '<th scope="col" aria-sort="descending"><button type="button" data-num>Expected points per building</button></th>'
                      '<th scope="col"><button type="button" data-num>Points withheld by refusals</button></th></tr></thead><tbody>%s</tbody></table>' % "".join(rows))
    B["ppf_basis"] = f("rentsafe_analysis_a_points_per_fix:points_per_fix.basis")

    # ---------------- analysis B tables
    tm = "rentsafe_analysis_b_risk:test_metrics."
    x = Bk["test_metrics"]
    B["b_metrics"] = (
        '<div class="tscroll" tabindex="0" role="region" aria-label="Test-set scores table; scrolls sideways on narrow screens">'
        '<table class="dt bm"><caption class="sr">Test-set scores, model against simple baselines</caption><thead><tr><th scope="col">Measure, test set</th><th scope="col">Model</th><th scope="col">Baseline</th><th scope="col">Better</th></tr></thead><tbody>'
        '<tr><th scope="row">Brier score for the next score falling below green (how far the predicted chances were from what happened; lower is better)</th><td class="num">%s</td><td class="num">%s<br><span class="muted">band\'s past rate</span></td><td>%s</td></tr>'
        '<tr><th scope="row">Precision-recall area (how well it ranks the buildings that did fall; higher is better)</th><td class="num">%s</td><td class="num">%s<br><span class="muted">band\'s past rate</span></td><td>%s</td></tr>'
        '<tr><th scope="row">Mean absolute error of the next score, points (lower is better)</th><td class="num">%s</td><td class="num">%s<br><span class="muted">repeat the last score</span></td><td>%s</td></tr>'
        '<tr><th scope="row">Mean absolute error of the next score, points (lower is better)</th><td class="num">%s</td><td class="num">%s<br><span class="muted">last score plus the band\'s usual change</span></td><td>%s</td></tr>'
        '</tbody></table></div>') % (
        f(tm + "brier_model", "fixed:4"), f(tm + "brier_persistence_band", "fixed:4"),
        "baseline" if x["brier_persistence_band"] < x["brier_model"] else "model",
        f(tm + "pr_auc_model", "fixed:3"), f(tm + "pr_auc_persistence_band", "fixed:3"),
        "model" if x["pr_auc_model"] > x["pr_auc_persistence_band"] else "baseline",
        f(tm + "mae_score_model", "fixed:2"), f(tm + "mae_score_persistence", "fixed:2"),
        "model" if x["mae_score_model"] < x["mae_score_persistence"] else "baseline",
        f(tm + "mae_score_model", "fixed:2"), f(tm + "mae_score_persistence_plus_band_mean_change", "fixed:2"),
        "model" if x["mae_score_model"] < x["mae_score_persistence_plus_band_mean_change"] else "baseline")
    tr = Bk["transition_counts_all_pairs"]
    head = "".join('<th scope="col">next %s</th>' % esc(c) for c in tr["cols_next_band"])
    body = "".join('<tr><th scope="row">last %s</th>%s</tr>' % (esc(r), "".join(
        '<td class="num">%s</td>' % f("rentsafe_analysis_b_risk:transition_counts_all_pairs.counts.%d.%d" % (i, j), "int")
        for j in range(len(tr["cols_next_band"])))) for i, r in enumerate(tr["rows_last_band"]))
    B["b_transitions"] = ('<table class="dt"><caption class="sr">Buildings by sign band at one evaluation and at the next</caption><thead><tr>'
                          '<th scope="col">Pairs of evaluations</th>%s</tr></thead><tbody>%s</tbody></table>' % (head, body))

    # ---------------- forecast lab
    models = FL["models"]
    idx = {m["id"]: i for i, m in enumerate(models)}
    rws = []
    for mid in FL["backtest"]["ranking"]:
        champ = mid == FL["backtest"]["champion"]
        rws.append('<tr%s><td>%s%s</td><td>%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td></tr>' % (
            ' class="champ"' if champ else "", f("forecast_lab:models.%d.name" % idx[mid]),
            ' <span class="tagc">champion</span>' if champ else "", f("forecast_lab:models.%d.kind" % idx[mid]),
            f("forecast_lab:backtest.scores.%s.overall.mape" % mid, "fixed:2"), f("forecast_lab:backtest.scores.%s.overall.mase" % mid, "fixed:2"),
            f("forecast_lab:backtest.scores.%s.overall.n" % mid, "int")))
    B["fl_table"] = ('<table class="dt"><caption class="sr">Out-of-sample accuracy of every method, best first</caption><thead><tr><th scope="col">Method</th><th scope="col">Kind</th>'
                     '<th scope="col">MAPE, percent</th><th scope="col">MASE</th><th scope="col">Forecasts scored</th></tr></thead><tbody>%s</tbody></table>' % "".join(rws))
    B["fl_method"] = lst("forecast_lab", "method", len(FL["method"]))
    B["fl_limits"] = lst("forecast_lab", "limits", len(FL["limits"]))
    B["fl_erratum"] = "".join("<p>%s</p>" % f("forecast_lab:erratum_text.%d" % i) for i in range(len(FL["erratum_text"])))
    B["fl_models"] = "".join('<option value="%s"%s>%s</option>' % (esc(m["id"]), " selected" if m["id"] == FL["backtest"]["champion"] else "", esc(m["name"])) for m in models)
    B["a_limits"] = lst("rentsafe_analysis_a_points_per_fix", "limits", len(A["limits"]))
    B["b_limits"] = lst("rentsafe_analysis_b_risk", "limits", len(Bk["limits"]))
    B["d_limits"] = lst("rentsafe_analysis_d_archetypes", "archetypes.limits", len(Dd["archetypes"]["limits"])) + lst(
        "rentsafe_analysis_d_archetypes", "control_chart.limits", len(Dd["control_chart"]["limits"]))
    B["trend_limits"] = lst("rentsafe_trend", "limits", len(I["rentsafe_trend"]["limits"]))

    # ---------------- data health page
    h = meta["health"]
    rr = []
    for i in range(len(meta["meta"]["northledger_clean"]["files"])):
        p = "rentsafe_meta:meta.northledger_clean.files.%d." % i
        rr.append('<tr><th scope="row">%s</th><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td></tr>' % (
            f(p + "source_file"), f(p + "rows_in", "int"), f(p + "clean", "int"), f(p + "quarantined", "int"),
            f(p + "health_score", "fixed:1"), f(p + "cells_changed_total", "int")))
    B["health_recon"] = ('<table class="dt"><caption class="sr">Rows in, clean and quarantined for each City file, as the NorthLedger engine cleaned them</caption><thead><tr><th scope="col">City file, engine step</th><th scope="col">Rows in</th><th scope="col">Clean</th>'
                         '<th scope="col">Quarantined</th><th scope="col">Health score</th><th scope="col">Cells repaired</th></tr></thead><tbody>%s</tbody></table>' % "".join(rr))
    B["health_quarantine"] = "".join('<li>%s: %s</li>' % (esc(r), f("rentsafe_meta:health.quarantine_reasons.%s" % r, "int"))
                                     for r in h["quarantine_reasons"] if "." not in r)
    names_ = {"post2023": "Current-method evaluations", "pre2023": "Previous-method evaluations"}
    B["health_build"] = "".join('<li>%s: %s rows in = %s clean + %s quarantined</li>' % (
        esc(names_.get(kk, kk)), f("rentsafe_meta:health.reconciliation.%s.rows_in" % kk, "int"),
        f("rentsafe_meta:health.reconciliation.%s.clean" % kk, "int"), f("rentsafe_meta:health.reconciliation.%s.quarantined" % kk, "int"))
        for kk in h["reconciliation"])
    B["health_flags"] = "".join([
        '<li>Year label malformed, current-method file: %s row, value %s</li>' % (f("rentsafe_meta:health.flags.year_label_malformed.post2023", "int"), f("rentsafe_meta:health.flags.malformed_year_label_values.0")),
        '<li>Year label differs from the completion year, current-method file: %s rows</li>' % f("rentsafe_meta:health.flags.year_label_differs_from_completion_year.post2023", "int"),
        '<li>No latitude or longitude, current-method file: %s rows, of which %s carry projected X/Y</li>' % (f("rentsafe_meta:health.flags.no_lat_long.post2023", "int"), f("rentsafe_meta:health.flags.no_lat_long_but_has_xy.post2023", "int")),
        '<li>Published scores that are not whole numbers: %s</li>' % f("rentsafe_meta:health.flags.non_integer_published_scores", "int"),
        '<li>The ward code inside GRID equals WARD on %s of %s rows, %s violations</li>' % (f("rentsafe_meta:health.grid_rule.grid_chars_2_3_equal_ward", "int"), f("rentsafe_meta:health.grid_rule.rows_checked", "int"), f("rentsafe_meta:health.grid_rule.violations", "int")),
    ])
    B["health_decisions"] = "".join('<li><strong>%s</strong> <span class="dstat">%s</span><br>%s</li>' % (
        esc(x["id"].replace("_", " ")), f("rentsafe_meta:health.decisions.%d.status" % i), f("rentsafe_meta:health.decisions.%d.decision" % i))
        for i, x in enumerate(h["decisions"]))
    B["grid_note"] = f("rentsafe_meta:health.grid_rule.note")
    B["reg_fresh"] = "The registration file was last refreshed by the City on %s, %s days before the data date (%s), against a stated cadence of %s." % (
        f("rentsafe_meta:health.registration.ckan_last_refreshed"), dd("reg_fresh_gap"), f("rentsafe_meta:meta.data_as_of"),
        f("rentsafe_meta:health.registration.stated_refresh_rate"))

    # ---------------- walkthrough run table
    rc = T["runs"]["rentsafe_cli"]
    rws = []
    for i, s in enumerate(rc["stages"]):
        rws.append('<tr data-i="%d"><td>%s</td><td>%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td><td>%s</td><td class="bar-cell"><span class="runbar"></span></td></tr>' % (
            i, f("timings:runs.rentsafe_cli.stages.%d.engagement" % i), f("timings:runs.rentsafe_cli.stages.%d.stage" % i),
            dd("st%d_rows" % i) if D.get("st%d_rows" % i) else "", dd("st%d_sec" % i),
            dd("st%d_mem" % i) if D.get("st%d_mem" % i) else "", dd("st%d_exit" % i)))
    B["run_table"] = ('<table class="dt runs" id="run-table"><caption class="sr">Every stage of the recorded run with its measured seconds</caption><thead><tr><th scope="col">Engagement</th><th scope="col">Stage</th><th scope="col">Rows</th>'
                      '<th scope="col">Seconds</th><th scope="col">Peak memory</th><th scope="col">Result</th><th scope="col"><span class="sr">Replay progress</span></th></tr></thead><tbody>%s</tbody></table>' % "".join(rws))

    # ---------------- engine stage table
    rws = []
    for i, st in enumerate(E["stages"]):
        tds = []
        for col in ("fixture_before", "fixture_after", "rentsafe"):
            xx = st[col]
            sec = dd("es%d_%s_sec" % (i, col)) if D.get("es%d_%s_sec" % (i, col)) else ""
            ev = ('<div class="ev">%s</div>' % f("engine_scorecard:stages.%d.%s.evidence" % (i, col))) if xx.get("evidence") else ""
            tds.append('<td>%s <span class="sec">%s</span>%s</td>' % (engine_status(xx.get("status")), sec, ev))
        rws.append('<tr><th scope="row">%s</th>%s</tr>' % (f("engine_scorecard:stages.%d.stage" % i), "".join(tds)))
    B["engine_table"] = ('<table class="dt eng"><caption class="sr">Each engine stage on three inputs, with its status and measured seconds</caption><thead><tr><th scope="col">Stage</th><th scope="col">Messy file, before the fixes</th>'
                         '<th scope="col">Messy file, after</th><th scope="col">RentSafeTO files</th></tr></thead><tbody>%s</tbody></table>' % "".join(rws))
    B["engine_not_yet"] = lst("engine_scorecard", "verdict.not_yet", len(E["verdict"]["not_yet"]))
    B["engine_limits"] = lst("engine_scorecard", "limits", len(E["limits"]))
    B["xcheck_diffs"] = lst("engine_scorecard", "rentsafe.forecast_crosscheck.explained_differences", len(E["rentsafe"]["forecast_crosscheck"]["explained_differences"]))

    # ---------------- examples
    B["examples"] = "".join(
        '<tr><td>%s<br><span class="muted">%s</span></td><td><code>%s</code></td><td>%s</td></tr>' % (
            dd("ex%d_col" % i), dd("ex%d_file" % i), dd("ex%d_raw" % i), dd("ex%d_after" % i)) for i in range(len(examples)))

    # ---------------- sources table
    src_rows = []
    for i, s in enumerate(meta["meta"]["sources"]):
        src_rows.append('<tr><td>%s<br><span class="muted">%s</span></td><td><a href="%s" rel="noopener">CKAN download</a></td><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>' % (
            f("rentsafe_meta:meta.sources.%d.dataset" % i), f("rentsafe_meta:meta.sources.%d.file" % i), esc(s["url"]),
            dd("src%d_at" % i), dd("src%d_bytes" % i), dd("src%d_sha" % i),
            "Open Government Licence, Toronto, assumed: the catalogue entry names none" if s["file"].endswith(".geojson") else "Open Government Licence, Toronto"))
    for i, key in enumerate(("RSAFSNA", "RSAFS")):
        s = I["fred_sources"]["series"][key]
        src_rows.append('<tr><td>%s, %s<br><span class="muted">%s</span></td><td><a href="%s" rel="noopener">FRED series page</a></td><td>%s<br><span class="muted">source file dated %s</span></td><td>%s</td><td><code>%s</code></td><td>FRED terms, with attribution</td></tr>' % (
            dd("fred%d_label" % i), esc(key), dd("fred%d_agency" % i), esc(s["page"]),
            dd("fred%d_at" % i), dd("fred%d_updated" % i), dd("fred%d_bytes" % i), dd("fred%d_sha" % i)))
    B["sources_table"] = ('<table class="dt src"><caption class="sr">Every downloaded input with its retrieval time, size and checksum</caption><thead><tr><th scope="col">Dataset and file</th><th scope="col">Where from</th><th scope="col">Retrieved</th>'
                          '<th scope="col">Size</th><th scope="col">Checksum, first characters</th><th scope="col">Licence</th></tr></thead><tbody>%s</tbody></table>' % "".join(src_rows))

    # ---------------- PBIP explorer and model
    if pbip["present"]:
        # the files to open in the explorer, named from the exported model itself (so a renamed
        # table cannot silently drop out): the project file, the relationships, the date table,
        # the forecast and every table in a relationship
        sm_ = "RentSafeTO Scorecard View.SemanticModel/definition/"
        rel_t = {r_[k].split(".")[0].strip("'") for r_ in pbip["relationships"] for k in ("from", "to")}
        show = {"RentSafeTO Scorecard View.pbip", sm_ + "relationships.tmdl"} | {
            sm_ + "tables/%s.tmdl" % t["name"] for t in pbip["tables"] if t["is_date"] or t["name"] in rel_t or t["name"] == "Forecast"}
        have = {fl["path"] for fl in pbip["files"]}
        R.errors.extend("PBIP explorer names a file the export does not have: %s" % x for x in sorted(show - have))
        tree, snippets, seen_dirs = [], [], set()
        for i, fl in enumerate(pbip["files"]):
            depth = fl["path"].count("/")
            name = fl["path"].rsplit("/", 1)[-1]
            parts = fl["path"].split("/")[:-1]
            for j in range(len(parts)):
                dkey = "/".join(parts[:j + 1])
                if dkey not in seen_dirs:
                    seen_dirs.add(dkey)
                    tree.append('<li class="d%d dir"><span class="pf-dir" data-fact-exempt="a folder name in the export">%s/</span></li>' % (min(j, 4), esc(parts[j])))
            if fl["path"] in show:
                sid = "pf-%d" % i
                tree.append('<li class="d%d"><button type="button" class="pf" aria-controls="%s" aria-expanded="false">%s</button> <span class="muted">%s</span></li>' % (min(depth, 4), sid, esc(name), dd("pf%d_size" % i)))
                snippets.append('<pre class="snip" id="%s" hidden data-fact-exempt="verbatim excerpt of the exported file"><code>%s</code></pre>' % (sid, esc(pbip_snippet(fl["path"]))))
            else:
                tree.append('<li class="d%d"><span class="pf-plain">%s</span> <span class="muted">%s</span></li>' % (min(depth, 4), esc(name), dd("pf%d_size" % i)))
        B["pbip_tree"] = '<ul class="ftree">%s</ul>' % "".join(tree)
        B["pbip_snippets"] = "".join(snippets)
        # one column that scales to the drawer's width: related tables first, so the relationship
        # is a short line between neighbours, then the standalone tables
        rel_names = [n_ for r_ in pbip["relationships"] for n_ in (r_["to"].split(".")[0].strip("'"), r_["from"].split(".")[0].strip("'"))]
        tbls = sorted(pbip["tables"], key=lambda t: (rel_names.index(t["name"]) if t["name"] in rel_names else len(rel_names), t["name"]))
        hgt = 20 + 86 * len(tbls)
        boxes, pos = [], {}
        for i, t in enumerate(tbls):
            x, y = 10, 10 + i * 86
            pos[t["name"]] = (x, y)
            boxes.append('<g><rect x="%d" y="%d" width="280" height="56" rx="8" class="mbox%s"/><text x="%d" y="%d" class="mt">%s</text><text x="%d" y="%d" class="ms">%s</text></g>' % (
                x, y, " mdate" if t["is_date"] else "", x + 12, y + 23, esc(t["name"]), x + 12, y + 43,
                esc("marked date table" if t["is_date"] else ("measures and columns" if t["measures"] else "columns"))))
        lines = []
        for r_ in pbip["relationships"]:
            a = r_["from"].split(".")[0].strip("'")
            b = r_["to"].split(".")[0].strip("'")
            if a in pos and b in pos:
                (x1, y1), (x2, y2) = pos[a], pos[b]
                lines.append('<path d="M%d %dL%d %d" class="mrel"/>' % (x1 + 140, y1 + 28, x2 + 140, y2 + 28))
                if abs(y2 - y1) == 86:
                    lines.append('<text x="%d" y="%d" class="ms">one to many</text>' % (x1 + 150, (y1 + y2) // 2 + 32))
        B["model_svg"] = ('<svg class="model" viewBox="0 0 300 %d" role="img" aria-labelledby="model-t"><title id="model-t">Tables in the exported Power BI model, with its one-to-many relationship: %s</title>%s%s</svg>'
                          % (hgt, esc(", ".join(t["name"] for t in tbls)), "".join(lines), "".join(boxes)))
        related = set()
        for r_ in pbip["relationships"]:
            related.add(r_["from"].split(".")[0].strip("'"))
            related.add(r_["to"].split(".")[0].strip("'"))
        facts_ = [t["name"] for t in tbls if t["name"] in related and not t["is_date"]]
        dates_t = [t["name"] for t in tbls if t["is_date"]]
        alone = [t["name"] for t in tbls if t["name"] not in related]
        B["_model_words"] = "one wide evaluation table (%s) related to a marked date table (%s), plus standalone tables (%s); not yet a star schema" % (
            esc(", ".join(facts_) or "none"), esc(", ".join(dates_t) or "none"), esc(", ".join(alone) or "none"))
        B["model_note"] = "It is %s." % B["_model_words"]
        meas = [m for m in pbip["measures"] if m["name"].startswith("Average") or m["name"] in ("Rows", "Quarantined Rows")][:6]
        B["engine_dax"] = "".join('<li><code>%s = %s</code><br><span class="muted">%s</span></li>' % (
            esc(m["name"]), esc(m["dax"]), esc(m["description"] or "no description in the export")) for m in meas)
    else:
        B["pbip_tree"] = B["pbip_snippets"] = B["model_svg"] = B["engine_dax"] = ""
        B["model_note"] = "No Power BI export was found at build."

    # ---------------- best practice panel (evidence, not ticks)
    ex = E["rentsafe"]["cli_run"]["exports"].get("view-2023", {})
    bp = []

    def bpi(ok, title, evidence):
        bp.append('<li class="%s"><span class="bpm">%s</span><div><strong>%s</strong><br>%s</div></li>' % (
            "ok" if ok else "no", "met" if ok else "not met", esc(title), evidence))
    if pbip["present"]:
        date_t = [t for t in pbip["tables"] if t["is_date"]]
        n_meas = len(pbip["measures"])
        bpi(bool(date_t), "Marked date table", "a table with dataCategory Time: %s" % esc(", ".join(t["name"] for t in date_t) or "none"))
        bpi(pbip.get("discourage_implicit"), "Explicit measures only", "model.tmdl switches implicit measures off" if pbip.get("discourage_implicit") else "implicit measures are not switched off")
        bpi(sum(1 for m in pbip["measures"] if m["description"]) == n_meas, "Every measure has a description", "%s of %s measures carry one" % (dd("pbip_measures_desc"), dd("pbip_measures")))
        bpi(sum(1 for m in pbip["measures"] if m["format"]) == n_meas, "Every measure has a format string", "%s of %s measures carry one" % (dd("pbip_measures_fmt"), dd("pbip_measures")))
        bpi(not any(r_["both_directions"] for r_ in pbip["relationships"]), "No bidirectional relationships", "%s of %s relationships filter both ways" % (dd("pbip_bidir"), dd("pbip_rels")))
        bpi(False, "Star schema", "not yet: the export is %s; a star schema (evaluation, item score, building, item, ward, district) is on the roadmap" % B["_model_words"])
    bpi(ex.get("pbip_lint") == "clean", "pbip_lint clean", "result: %s" % f("engine_scorecard:rentsafe.cli_run.exports.view-2023.pbip_lint"))
    bpi(bool(ex.get("tmdl_tables")), "Microsoft's TMDL parser loads the model", "%s tables and %s relationship loaded by %s" % (
        f("engine_scorecard:rentsafe.cli_run.exports.view-2023.tmdl_tables", "int"),
        f("engine_scorecard:rentsafe.cli_run.exports.view-2023.tmdl_relationships", "int"),
        f("engine_scorecard:rentsafe.cli_run.exports.view-2023.tmdl_tool")))
    bpi(False, "Opened and refreshed in Power BI Desktop", "not yet: Power BI has not evaluated the DAX or the Power Query steps")
    bpi(True, "Every visual has a text name and a table view", "each chart is an SVG with role img and a title, with a Table button beside it (verify.sh checks the SVGs)")
    bpi(True, "Text contrast checked", "worst text-on-surface pair %s across %s pairs in both themes, computed at build" % (dd("contrast_worst"), dd("contrast_pairs")))
    bpi(True, "Freshness stamp present", "the data date and the build time are written by the build, never typed")
    B["best_practice"] = '<ul class="bp">%s</ul>' % "".join(bp)

    # ---------------- PL-300 milestones (owner-edited file)
    pl = I["pl300-status"]
    B["pl_milestones"] = "".join('<li><span>%s</span> <span class="status s-roadmap">%s</span></li>' % (
        f("pl300-status:milestones.%d.name" % i), f("pl300-status:milestones.%d.state" % i)) for i in range(len(pl["milestones"])))
    B["ogl_url"] = esc(meta["meta"]["licence"]["url"])
    B["pl_url"] = esc(pl["data_source"]["url"])
    B["pl_reconcile"] = ("No reconcile result has been recorded by the owner yet." if not pl.get("reconcile")
                         else f("pl300-status:reconcile.summary"))

    # ---------------- offers (typed by the owner in site.config.json)
    offers = []
    for o in I["config"]["offers"]:
        offers.append('<article class="offer" data-offer="%s"><p class="tag">%s</p><h3>%s</h3><p>%s</p>'
                      '<p class="meta" data-fact-exempt="price and timing typed by the owner in site.config.json"><span class="price">%s</span> %s</p>%s</article>' % (
                          esc(o["id"]), esc(o["tag"]), esc(o["name"]), esc(o["pitch"]), esc(o["price"]), esc(o["timing"]),
                          ('<p class="noai">AI model calls in the audit runs recorded on this page: %s.</p>' % dd("audit_ai_calls")) if o.get("no_ai") else ""))
    B["offers"] = "".join(offers)

    # ---------------- contact (empty until the owner fills site.config.json)
    cfg = I["config"]
    # the same keys tools/check_site.py accepts: top-level contact_email / booking_url, or a "contact" block
    nested = cfg.get("contact") if isinstance(cfg.get("contact"), dict) else {}
    email = (cfg.get("contact_email") or nested.get("email") or "").strip()
    booking = (cfg.get("booking_url") or nested.get("booking_url") or "").strip()
    if email or booking:
        parts = []
        if email:
            body_ = "What do you want to know?%0AWhich tools hold your data?%0ARough row count?%0A"
            parts.append('<a class="btn btn-primary" href="mailto:%s?subject=Data%%20audit%%20enquiry&amp;body=%s">Email %s</a> '
                         '<button type="button" class="btn btn-ghost" data-copy="%s">Copy the address</button>' % (
                             esc(email), body_.replace(" ", "%20"), esc(email), esc(email)))
        if booking:
            parts.append('<a class="btn btn-ghost" href="%s" rel="noopener">Book a call</a>' % esc(booking))
        B["contact"] = '<div class="contact-live">%s</div>' % " ".join(parts)
    else:
        B["contact"] = ('<div class="contact-empty" role="status"><strong>Contact details coming.</strong> The owner has not added an email '
                        'address or a booking link yet, so this page does not offer one, and nothing here collects your details in the meantime.</div>')
    return B


# ----------------------------------------------------------------------------- DATA-SOURCES.md
SCALE_SOURCES = [   # (manifest scale_tables key, dataset, what it is, official page, direct download, licence)
    ("chicago_crimes", "Chicago Crimes 2001 to present", "every reported crime incident: date, block, type, arrest, beat, ward",
     "https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2",
     "https://data.cityofchicago.org/api/views/ijzp-q8t2/rows.csv?accessType=DOWNLOAD", "City of Chicago open-data terms, with attribution"),
    ("nyc_311", "NYC 311 Service Requests 2010 to present", "every 311 request: agency, complaint type, dates, borough, status",
     "https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9",
     "https://data.cityofnewyork.us/api/views/erm2-nwe9/rows.csv?accessType=DOWNLOAD", "NYC Open Data terms, with attribution"),
    ("pums_persons", "US Census ACS PUMS 2023 1-year, persons", "person-level microdata, survey-weighted (PWGTP)",
     "https://www.census.gov/programs-surveys/acs/microdata/documentation.html",
     "https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_pus.zip", "US Census Bureau public-use microdata"),
    ("pums_households", "US Census ACS PUMS 2023 1-year, households", "household-level microdata, survey-weighted (WGTP)",
     "https://www.census.gov/programs-surveys/acs/microdata/documentation.html",
     "https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_hus.zip", "US Census Bureau public-use microdata"),
    ("amazon_reviews_vg", "Amazon Reviews 2023, video games reviews", "rating, text, verified purchase, helpful votes, time",
     "https://amazon-reviews-2023.github.io/",
     "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Video_Games.jsonl.gz",
     "no licence declared by the publisher (McAuley Lab, UCSD); used only as test input"),
    ("amazon_meta_vg", "Amazon Reviews 2023, video games products", "product catalogue: price, brand, categories, ratings",
     "https://amazon-reviews-2023.github.io/",
     "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Video_Games.jsonl.gz",
     "no licence declared by the publisher (McAuley Lab, UCSD); used only as test input"),
    ("rdw_vehicles", "RDW registered vehicles (Netherlands)", "every registered vehicle: brand, model, colour, inspection dates",
     "https://opendata.rdw.nl/Voertuigen/Open-Data-RDW-Gekentekende-voertuigen/m9d7-ebf2",
     "https://opendata.rdw.nl/api/views/m9d7-ebf2/rows.csv?accessType=DOWNLOAD", "CC0"),
    ("rdw_fuel", "RDW registered vehicles, fuel records (Netherlands)", "fuel records that pair with the vehicle registry",
     "https://opendata.rdw.nl/Voertuigen/Open-Data-RDW-Gekentekende-voertuigen/m9d7-ebf2 (same RDW open-data portal)",
     "not recorded in this project", "CC0"),
]


def data_sources_md(I, built_at):
    """DATA-SOURCES.md, written from the same records the page shows (never typed)."""
    meta, fred, M = I["rentsafe_meta"], I["fred_sources"], I["manifest"]
    lic = meta["meta"]["licence"]
    out = ["# NorthLedger data sources", "",
           "Every dataset used on this site is public and traceable to its official source. Licences differ by",
           "dataset and are listed per row; one publisher (Amazon Reviews 2023) declares no licence, and that data",
           "is used only as test input for the AI-analyst replays. Nothing here is private or scraped. Each file was",
           "downloaded once, on purpose, and recorded; nothing on this site refreshes itself or runs on a schedule.", "",
           "This file is written by `build.py` from `data/rentsafe_meta.json`, `data/fred/SOURCES.json` and the",
           "replay manifest inside `agent-demo.html`, so its counts and checksums are the ones the page uses.",
           "Built %s." % utc_label(built_at), "",
           "## The main page: RentSafeTO report, scorecard and analyses", "",
           "City of Toronto open data from the CKAN portal. %s ([the licence](%s).) This is independent analysis;"
           " it is not produced by, affiliated with or endorsed by the City of Toronto or RentSafeTO." % (lic["required_attribution_text"], lic["url"]), "",
           "| Dataset | File | Retrieved (UTC) | Size | sha256, first 16 | Licence |", "|---|---|---|---|---|---|"]
    for s_ in meta["meta"]["sources"]:
        out.append("| %s | [%s](%s) | %s | %s | `%s` | %s |" % (
            s_["dataset"], s_["file"], s_["url"], utc_label(s_["retrieved_at"]), human_bytes(s_["bytes"]), s_["sha256"][:16],
            "Open Government Licence, Toronto, assumed: the catalogue entry names none" if s_["file"].endswith(".geojson")
            else "Open Government Licence, Toronto"))
    out += ["", "## The Forecast Lab", "",
            "One-off downloads by `tools/fetch_fred.py` (fredgraph.csv, no API key), recorded in `data/fred/SOURCES.json`.",
            "Nothing refreshes them. FRED's terms for redistributing derived charts were not re-read for this build.", "",
            "| Series | What it is | Source | Downloaded (UTC) | Source file dated | Months | Size | sha256, first 16 |",
            "|---|---|---|---|---|---|---|---|"]
    for key in ("RSAFSNA", "RSAFS"):
        s_ = fred["series"][key]
        out.append("| [%s](%s) | %s, %s, %s | %s | %s | %s | %s (%s to %s) | %s | `%s` |" % (
            key, s_["page"], s_["label"], s_["units"].lower(), s_["seasonal_adjustment"].lower(), s_["source_agency"],
            utc_label(s_["completed_at"]), s_["last_modified_header"], format(s_["rows"], ","), s_["first"]["month"],
            s_["last"]["month"], human_bytes(s_["bytes"]), s_["sha256"][:16]))
    if M:
        ev = M["evidence"]
        t = ev["scale_tables"]
        out += ["", "## The AI-analyst replays (agent-demo.html)", "",
                "Demo database: NYC Motor Vehicle Collisions from NYC Open Data"
                " (https://data.cityofnewyork.us/Public-Safety/Motor-Vehicle-Collisions-Crashes/qgea-i56i), %s crash records"
                " from %s to %s, with a synthetic legacy-export layer (%s rows of deliberate duplicates, mixed date formats and"
                " casing) standing in for a client's messy copy. Licence: NYC Open Data terms, with attribution." % (
                    format(ev["demo_rows"], ","), ev["demo_first_date"], ev["demo_last_date"], format(ev["legacy_rows"], ",")), "",
                "Scale-tier database, row counts as ingested (profiled %s; %s rows in all):" % (ev["scale_profile_date"], format(ev["scale_total_rows"], ",")), "",
                "| Dataset | Rows | What it is | Official page | Direct download | Licence |", "|---|---|---|---|---|---|"]
        for key, name, what, page, dl, lic_ in SCALE_SOURCES:
            if key in t:
                out.append("| %s | %s | %s | %s | %s | %s |" % (name, format(t[key], ","), what, page, dl, lic_))
        out += ["", "The NYC 311 session is archived on the replay page and kept apart from the owner's hand-built PL-300",
                "Power BI project, which uses the same dataset."]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- browser payload
def payload(I):
    sc, A, Bk, Dd, FL = (I["rentsafe_scorecard"], I["rentsafe_analysis_a_points_per_fix"], I["rentsafe_analysis_b_risk"],
                         I["rentsafe_analysis_d_archetypes"], I["forecast_lab"])
    decomp = [{"m": r["month"], "o": r["observed"], "s": r["seasonal_pct"], "c": r["census_seasonal_pct"], "cv": r["in_covid_window"]}
              for r in FL["decomposition"]["series"]]
    return {
        "cube": {"fields": I["rentsafe_cube"]["fields"], "rows": I["rentsafe_cube"]["rows"], "district_of_ward": I["rentsafe_cube"]["district_of_ward"]},
        "sc": {"wards": sc["wards"], "districts": sc["districts"], "city": sc["city"], "bands": sc["bands"],
               "columns": [{"key": c["key"], "label": c["label"], "items": c.get("items", []), "weight_total": c.get("weight_total")} for c in sc["columns"]]},
        "A": {"items": A["points_per_fix"]["items"], "by_ward": A["points_per_fix"]["by_ward"],
              "city_total": A["points_per_fix"]["city_total_fix_points_per_building"]},
        "B": {"rel_model": Bk["reliability_model"], "rel_persist": Bk["reliability_persistence"], "by_ward": Bk["by_ward_test"]},
        "D": {"clusters": Dd["archetypes"]["clusters"], "shape": Dd["archetypes"]["shape_variant"]["clusters"],
              "corr": Dd["archetypes"]["pillar_correlation"], "control": Dd["control_chart"], "city_profile": Dd["archetypes"]["city_mean_profile"]},
        "trend": {"city": I["rentsafe_trend"]["city_by_year"], "wards": I["rentsafe_trend"]["ward_by_year"]},
        "fl": {"models": FL["models"], "ranking": FL["backtest"]["ranking"], "champion": FL["backtest"]["champion"],
               "origins": FL["backtest"]["origins"], "by_h": {k: v["by_h"] for k, v in FL["backtest"]["scores"].items()},
               "forecast": FL["forecast"], "decomp": decomp, "factors": FL["decomposition"]["factors"], "covid": FL["covid_window"]},
        "run": [{"e": s["engagement"], "s": s["stage"], "t": s["seconds"]} for s in I["timings"]["runs"]["rentsafe_cli"]["stages"]],
        "map": I["rentsafe_wards_map"]["features"],
    }


# ----------------------------------------------------------------------------- main
# checks that run on the rendered pages, after the page states the total; each is recorded in
# site_build.json when it passes, so the stated count and the receipt's list always agree
POST_CHECKS = (
    "no internal plan code or Markdown backtick in reader-facing text (index.html, case-study.html)",
    "every scorecard cell prints its value rounded half-up and takes its band from that printed value",
    "every bound figure on index.html and case-study.html equals its JSON",
)


def build(write=True, run_tests=False):
    C = Checks()
    I = read_inputs()
    tests = run_engine_tests() if (run_tests and write) else I["engine_tests"]
    downloads = build_downloads(C, write)
    pbip = read_pbip()
    examples = messy_examples()
    C("six messy examples found in the raw City files", len(examples) == 6, "%d found" % len(examples))
    crow = contrast_report()
    D, SRC_OF = compute(I, C, downloads, pbip, examples, tests, crow)
    pl = I["pl300-status"]
    C("the PL-300 status file carries no measured numbers (no numeric values, no digits in status, milestones or reconcile)",
      not re.search(r"\d", json.dumps([pl.get("status"), pl.get("milestones"), pl.get("reconcile"), pl.get("method"), pl.get("route")]))
      and not re.search(r":\s*-?\d", json.dumps(pl)))
    C("site.config.json lists the three offers", len(I["config"].get("offers", [])) == 3)

    built_at = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    n_checks = len(C.items) + len(POST_CHECKS)     # + the checks on the rendered pages below
    D["build_checks_passed"] = format(n_checks, ",")
    SRC_OF["build_checks_passed"] = "count of build.py checks, every one of which must pass"
    D["built_at_label"] = utc_label(built_at)
    SRC_OF["built_at_label"] = "clock at build time"
    site_build = {
        "schema": "site_build/1",
        "what": "Figures the page shows that are derived from other files (a share turned into a percentage, a count of sessions, a file size). Written by build.py; each key names its source.",
        "built_at": built_at,
        "display": D,
        "derived_from": SRC_OF,
        "checks": C.items,
        "contrast": crow,
        "examples": examples,
    }
    if C.failed:
        for c in C.failed:
            print("BUILD CHECK FAILED: %s %s" % (c["check"], c["detail"]))
        return 1, site_build
    sources = check_site.load_sources(ROOT)
    sources["site_build"] = site_build
    R = Renderer(sources, D, I["config"])
    R.blocks = blocks(I, R, D, pbip, examples)
    content = "\n".join(R.sub(open(p, encoding="utf-8").read()) for p in sorted(glob.glob(os.path.join(SRC, "sections", "*.html"))))
    if R.errors:
        for e in sorted(set(R.errors)):
            print("TEMPLATE ERROR: " + e)
        return 1, site_build
    css = css_tokens() + open(os.path.join(SRC, "style.css"), encoding="utf-8").read()
    js = "\n".join(open(p, encoding="utf-8").read() for p in sorted(glob.glob(os.path.join(SRC, "js", "*.js"))))
    data_js = json.dumps(payload(I), separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    page = PAGE % (css, content, data_js, js)
    # the case study as a page styled like this one, from the same template as case-study.md
    tpl = os.path.join(SRC, "case-study.template.md")
    tpl_text = open(tpl, encoding="utf-8").read() if os.path.exists(tpl) else ""
    case_page = CASE_PAGE % (css, R.sub(md_to_html(tpl_text))) if tpl_text else None
    if R.errors:
        for e in sorted(set(R.errors)):
            print("TEMPLATE ERROR: " + e)
        return 1, site_build
    plan = {}
    for name_, pg in (("index.html", page), ("case-study.html", case_page or "")):
        hits = sorted({m.group(0) for m in PLAN_CODE.finditer(reader_text(pg))})
        pl_hits = sorted({m.group(0) for m in PLAN_CODE.finditer(" ".join(x for x in _strings(payload(I))))}) if name_ == "index.html" else []
        if hits or pl_hits:
            plan[name_] = hits + pl_hits
    if plan:
        print("BUILD CHECK FAILED: no internal plan code (E4, E10 ...) or Markdown backtick in reader-facing text %s" % plan)
        return 1, site_build
    site_build["checks"].append({"check": POST_CHECKS[0], "ok": True, "detail": ""})
    # every scorecard cell's printed text and band come from the half-up value
    cells = re.findall(r'<td class="hc h-(\w+)-\d" data-col="\w+" data-v="([^"]+)" data-band="(\w+)"><span class="cv"><span data-fact="[^"]+" data-fmt="([^"]+)">([^<]+)</span>',
                       R.blocks.get("heatmap_table", ""))
    off = ["%s prints %s (%s), band %s" % (v, text, fm, b) for cls, v, b, fm, text in cells
           if fm != "half:1" or text != half_up(float(v), 1) or b != cls or b != band_of(float(text))[0]]
    if not cells or off:
        print("BUILD CHECK FAILED: %s %s" % (POST_CHECKS[1], "; ".join(off[:5]) or "no scorecard cells found"))
        return 1, site_build
    site_build["checks"].append({"check": POST_CHECKS[1], "ok": True, "detail": "%d cells" % len(cells)})
    # the figure check on the built page, before anything is written
    site = check_site.Site(ROOT)
    site._html["index.html"] = page
    site._tree["index.html"] = check_site.parse_html(page)
    if case_page:
        site._html["case-study.html"] = case_page
        site._tree["case-study.html"] = check_site.parse_html(case_page)
    site.pages = lambda: [x for x in ("index.html", "case-study.html") if x in site._html]
    real_load = check_site.load_sources
    check_site.load_sources = lambda root: sources
    try:
        res = check_site.check_figures(site)
    finally:
        check_site.load_sources = real_load
    fails = [x for x in res.failures if x.startswith(("index.html", "case-study.html"))]
    if fails:
        for x in fails[:40]:
            print("FIGURE CHECK: " + x)
        return 1, site_build
    site_build["checks"].append({"check": POST_CHECKS[2], "ok": True, "detail": res.notes[-1] if res.notes else ""})
    # the page states how many checks passed; the receipt must list exactly that many
    if len(site_build["checks"]) != n_checks or not all(c["ok"] for c in site_build["checks"]):
        print("BUILD CHECK FAILED: the page states %d build checks but site_build.json would list %d" % (n_checks, len(site_build["checks"])))
        return 1, site_build
    if write:
        with open(os.path.join(DATA, "site_build.json"), "w", encoding="utf-8") as f:
            json.dump(site_build, f, indent=1, ensure_ascii=False)
            f.write("\n")
        with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
            f.write(page)
        with open(os.path.join(ROOT, "DATA-SOURCES.md"), "w", encoding="utf-8") as f:
            f.write(data_sources_md(I, built_at))
        if tpl_text:
            md = TAG.sub(lambda m: _plain(R, m), tpl_text)
            with open(os.path.join(ROOT, "case-study.md"), "w", encoding="utf-8") as f:
                f.write(md)
            with open(os.path.join(ROOT, "case-study.html"), "w", encoding="utf-8") as f:
                f.write(case_page)
        print("built index.html (%d KB), %d build checks passed, %d bound figures" % (
            len(page.encode("utf-8")) // 1024, n_checks, page.count("data-fact=\"")))
    return 0, site_build


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NorthLedger Insights: RentSafeTO Building Health</title>
<meta name="description" content="Rashadul Islam Roman, data analyst in Toronto. A RentSafeTO building-health scorecard built from City of Toronto open data, with checked findings, honest forecasts and the receipts behind every number.">
<script>(function(){var t=null;try{t=localStorage.getItem('nl-theme');}catch(e){}document.documentElement.setAttribute('data-theme',t==='dark'?'dark':'light');})();</script>
<style>
%s
</style>
</head>
<body>
%s
<script>window.NL = %s;</script>
<script>
%s
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------- case study page
def md_inline(t):
    """Inline Markdown of the case-study template: code, bold, italics, links. {{tags}} pass through."""
    t = esc(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![*\w])\*([^*\n]+?)\*(?![*\w])", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md_to_html(md):
    """The small Markdown subset the case-study template uses: #/## headings, paragraphs, '- '
    lists with indented continuation lines. Anything else is an error, not a guess."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>%s</p>" % md_inline(" ".join(para)))
            para.clear()
        if items:
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % md_inline(" ".join(x)) for x in items))
            items.clear()
    for line in md.splitlines():
        if not line.strip():
            flush()
            continue
        h = re.match(r"^(#{1,3}) (.+)$", line)
        if h:
            flush()
            n = len(h.group(1))
            out.append("<h%d>%s</h%d>" % (n, md_inline(h.group(2).strip()), n))
        elif line.startswith("- "):
            if para:
                flush()
            items.append([line[2:].strip()])
        elif items and line.startswith("  "):
            items[-1].append(line.strip())
        elif re.match(r"^\s*([*+]|\d+\.)\s", line) or line.startswith(("|", ">", "```")):
            raise ValueError("case-study template uses Markdown this build does not render: %r" % line[:40])
        else:
            para.append(line.strip())
    flush()
    return "\n".join(out)


CASE_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Case study: RentSafeTO Building Health | NorthLedger Insights</title>
<meta name="description" content="A RentSafeTO case study on City of Toronto open data: what the audit found and what it means for a building operator. Every figure is filled in from the site's data files.">
<script>(function(){var t=null;try{t=localStorage.getItem('nl-theme');}catch(e){}document.documentElement.setAttribute('data-theme',t==='dark'?'dark':'light');})();</script>
<style>
%s
.cs { max-width: 760px; margin: 0 auto; padding: 28px 24px 48px; }
.cs h1 { font-size: clamp(26px, 4vw, 36px); line-height: 1.15; margin: 8px 0 12px; color: var(--ink); }
.cs h2 { font-size: 20px; margin: 28px 0 8px; color: var(--ink); }
.cs p, .cs li { font-size: 16px; line-height: 1.6; color: var(--body); }
.cs ul { padding-left: 22px; }
.cs li { margin: 6px 0; }
.cs em { color: var(--muted); }
.cs-bar { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
@media (max-width: 480px) { .cs { padding: 18px 16px 40px; } }
</style>
</head>
<body>
<header class="topbar"><div class="wrap bar cs-bar"><a class="wordmark" href="index.html">NorthLedger Insights<small>Rashadul Islam Roman</small></a>
<button class="theme-btn" type="button" id="theme-btn" aria-label="Switch between light and dark theme"><span aria-hidden="true" class="ti"></span></button></div></header>
<main id="main"><article class="cs">
%s
<p><a href="index.html">Back to the interactive report</a> &middot; <a href="index.html#receipts">Receipts: sources, checksums and tests</a></p>
</article></main>
<script>
(function(){var b=document.getElementById('theme-btn');if(!b)return;b.addEventListener('click',function(){var r=document.documentElement;
var c=r.getAttribute('data-theme')||(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
var n=c==='dark'?'light':'dark';r.setAttribute('data-theme',n);try{localStorage.setItem('nl-theme',n);}catch(e){}});})();
</script>
</body>
</html>
"""


# internal plan codes (E4, E10 ...) and Markdown backticks mean nothing to a reader
PLAN_CODE = re.compile(r"\bE\d{1,2}\b|`")


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def reader_text(page):
    """What a reader sees: the body without scripts, styles or tags."""
    b = page.split("<body>", 1)[-1]
    b = re.sub(r"<(script|style)\b.*?</\1>", " ", b, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", b))


def _plain(R, m):
    """Case study: the same bound values as plain text (Markdown carries no data-fact)."""
    kind, arg = m.group(1), m.group(2)
    out = R.sub("{{%s %s}}" % (kind, arg))
    return html.unescape(re.sub(r"<[^>]+>", "", out))


def main(argv):
    if "--check" in argv:
        path = os.path.join(DATA, "site_build.json")
        old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
        rc, new = build(write=False)
        if rc:
            return rc

        def strip(x):
            if not x:
                return x
            y = {k: v for k, v in x.items() if k != "built_at"}
            y["display"] = {k: v for k, v in x["display"].items() if k != "built_at_label"}
            y["derived_from"] = {k: v for k, v in x["derived_from"].items() if k != "built_at_label"}
            return y
        if strip(old) != strip(new):
            print("data/site_build.json would change: run python3 build.py")
            return 1
        print("data/site_build.json reproduces from its inputs")
        return 0
    rc, _ = build(write=True, run_tests="--tests" in argv)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
