#!/usr/bin/env python3
"""Make the agent replay page safe and honest to publish.

    python tools/sanitize_agent_demo.py            # ../agent-demo/agent-demo.html -> agent-demo.html
    python tools/sanitize_agent_demo.py --check    # rebuild in memory, compare with agent-demo.html

The input is the page that agent-demo/build_page.py writes. That page is not touched. The
output is portfolio-website/agent-demo.html, rebuilt from it every time (so the steps are
reproducible and idempotent). What changes:

  * local file paths and the directory listing that showed ".env" are redacted from the
    recorded sessions (they stay "as recorded" otherwise);
  * editor's notes, marked as such, are added where a replay says something the record does
    not support: the early empty-query failures, a date the agent misread, "so trust the
    model" (in-sample fit compared with a holdout error), and the Dutch fleet total that the
    self-audit found wrong (corrected from the query result itself);
  * the NYC 311 session is moved to an archive below the showcase and flagged
    featured: false in the page's manifest, because the owner's hand-built PL-300 model uses
    the same dataset;
  * the hero, facts and data-source copy are regenerated from evidence files (database
    profile, the demo database itself, the audit's replay and trace reports), so no count is
    typed by hand, and freshness words like "live" are gone;
  * the back link works from a GitHub Pages project URL (index.html, not ../index.html);
  * the replay script is patched so that starting a session (a card, Replay, or a link naming a
    session) first stops the one that is playing, and the page plays the session named in its
    URL hash (agent-demo.html#s5-rdw-fleet; the first session otherwise), with a static anchor
    per session so those links resolve;
  * editorial text (card titles and blurbs) carries no em dash; quoted transcripts stay as recorded;
  * a JSON manifest (script#demo-manifest) records sessions, featured flags, counts,
    redactions and sources for any teaser that needs them.

Exits non-zero, changing nothing, if an expected part of the input page is missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import tempfile

SITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(SITE)
AGENT = os.path.join(REPO, "agent-demo")
AUDIT = os.path.join(REPO, "audit")
SRC = os.path.join(AGENT, "agent-demo.html")
OUT = os.path.join(SITE, "agent-demo.html")
MARK = "<!-- built by tools/sanitize_agent_demo.py; edit that script, not this page -->"
# Card copy that reads as a freshness claim but describes data granularity.
CARD_WORDING = {"adapts to daily scale": "switches to day-level data",
                # the AI-agent session is not the Data Health Audit the site sells (that runs no model)
                "Data-health audit (messy legacy export)": "AI-agent data-quality session (messy legacy export)"}
# card text written for the page (not the recorded prompt): the site's style has no em dash
EDITORIAL_FIELDS = ("title", "blurb")
ARCHIVE = {"s4-nyc311-ops": "Archived, not part of the showcase: it uses the same NYC 311 dataset as "
                            "Rashadul's hand-built PL-300 Power BI model, so the two are kept apart."}

PATH_RX = [re.compile(r"(?:/Users|/home)/[^\s\"'<>\\]+"), re.compile(r"/private/(?:var|tmp)/[^\s\"'<>\\]+"),
           re.compile(r"(?<![\w.])/tmp/[^\s\"'<>\\]+"),
           re.compile(r"/var/folders/[^\s\"'<>\\]+"), re.compile(r"[A-Za-z]:\\\\?Users\\\\?[^\s\"'<>]+")]
ENV_RX = re.compile(r"(?<![A-Za-z0-9_$.])\.env(?![A-Za-z0-9_])")
LIST_RX = re.compile(r"\[[^\[\]]*\]")


class Missing(Exception):
    pass


# ----------------------------------------------------------------------------- helpers
def fmt_m(n):
    return "%.1fM" % (n / 1e6)


def fmt_k(n):
    return "%dK" % round(n / 1e3)


def esc(s):
    return html.escape(s, quote=True)


def sub_once(text, pattern, repl, what, flags=re.S):
    new, n = re.subn(pattern, lambda m: repl, text, count=1, flags=flags)
    if n != 1:
        raise Missing("expected part of the page not found: %s" % what)
    return new


def replace_once(text, old, new, what):
    if text.count(old) != 1:
        raise Missing("expected exactly one %s, found %d" % (what, text.count(old)))
    return text.replace(old, new)


# ----------------------------------------------------------------------------- evidence
def evidence(agent_dir=AGENT, audit_dir=AUDIT):
    prof_path = os.path.join(agent_dir, "big-data-profile.json")
    prof = json.load(open(prof_path))
    tables = {t["table"]: t["rows"] for t in prof["datasets"]}
    built = dt.datetime.fromtimestamp(os.path.getmtime(prof_path)).date()
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(agent_dir, "client_data.db"), uri=True)
    try:
        demo_rows = con.execute("SELECT COUNT(*) FROM collisions_raw").fetchone()[0]
        legacy_rows = con.execute("SELECT COUNT(*) FROM legacy_export").fetchone()[0]
        dmin, dmax = con.execute("SELECT MIN(substr(crash_date,1,10)), MAX(substr(crash_date,1,10)) "
                                 "FROM collisions_raw").fetchone()
        demo_tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        con.close()
    replay = json.load(open(os.path.join(audit_dir, "replay-report.json")))["totals"]
    trace = json.load(open(os.path.join(audit_dir, "trace-report.json")))["totals"]
    return {
        "scale_tables": tables, "scale_total_rows": sum(tables.values()), "scale_profile_date": built.isoformat(),
        "demo_rows": demo_rows, "legacy_rows": legacy_rows, "demo_first_date": dmin, "demo_last_date": dmax,
        "demo_tables": demo_tables,
        "audit_queries_checked": replay["checked"], "audit_queries_reproduced": replay["reproduced"],
        "audit_numbers_stated": trace["stated"], "audit_numbers_traced": trace["traced"],
        "audit_traceability": trace["traceability"],
    }


def s5_correction(agent_dir=AGENT):
    """The self-audit's one confirmed defect, recomputed from the recorded transcript: the
    report's 'total any-electric' against the query rows its BEV and PHEV figures came from."""
    p = os.path.join(agent_dir, "sessions", "s5-rdw-fleet", "transcript.json")
    if not os.path.exists(p):
        return None
    t = json.load(open(p))
    body, results = None, []
    for turn in t.get("turns", []):
        for tc in turn.get("tool_calls") or []:
            res = tc.get("result") or {}
            if tc.get("name") == "run_sql" and res.get("columns") == ["grp", "n"]:
                results.append({r[0]: r[1] for r in res.get("rows", [])})
            if tc.get("name") == "make_report" and (res or {}).get("artifact"):
                body = json.dumps(tc.get("args"))
    if not body:
        return None
    m = re.search(r"BEV ([\d,]+).*?PHEV ([\d,]+).*?total any-electric ([\d,]+)", body)
    if not m:
        return None
    bev, phev, stated = (int(x.replace(",", "")) for x in m.groups())
    for rows in results:
        if rows.get("BEV_pure_electric") == bev and rows.get("PHEV_Elek_plus_Benzine_or_Diesel") == phev:
            parts = {k: v for k, v in rows.items() if k != "No_electricity"}
            total = sum(parts.values())
            if total != stated:
                return {"stated": stated, "correct": total, "parts": parts, "gap": total - stated}
    return None


# ----------------------------------------------------------------------------- sessions
def redact(obj, counts):
    if isinstance(obj, str):
        s = obj

        def listing(m):
            if ENV_RX.search(m.group(0)):
                counts["listings"] += 1
                return "[directory listing redacted]"
            return m.group(0)

        s = LIST_RX.sub(listing, s)
        for rx in PATH_RX:
            s, n = rx.subn("[local path redacted]", s)
            counts["paths"] += n
        s, n = ENV_RX.subn("[redacted]", s)
        counts["env"] += n
        return s
    if isinstance(obj, list):
        return [redact(x, counts) for x in obj]
    if isinstance(obj, dict):
        return {k: redact(v, counts) for k, v in obj.items()}
    return obj


def note(text):
    return {"who": "note", "text": text}


def session_database(s, ev):
    sql = " ".join(json.dumps(c.get("detail", {})) for c in s["chat"] if c.get("who") == "tool")
    big = [t for t in ev["scale_tables"] if re.search(r"\b%s\b" % re.escape(t), sql)]
    small = [t for t in ev["demo_tables"] if re.search(r"\b%s\b" % re.escape(t), sql)]
    if big and not small:
        return "big_data.db (scale tier)"
    if small and not big:
        return "client_data.db (demo database)"
    return "client_data.db and big_data.db" if (big and small) else "the demo databases"


def add_notes(s, ev, fix5):
    chat = s["chat"]
    out = []
    # 1) a run of empty-query failures at the start
    fails = 0
    for c in chat:
        if c.get("who") != "tool":
            continue
        d = c.get("detail") or {}
        if c.get("tool") == "run_sql" and not d.get("sql") and d.get("error"):
            fails += 1
            continue
        break
    inserted_fail_note = False
    for i, c in enumerate(chat):
        out.append(c)
        if c.get("who") == "user" and fails >= 2 and not inserted_fail_note:
            out.append(note("Editor's note: the first %d SQL calls below fail because the agent sent an empty "
                            "query (the tool's schema declared no parameters at the time). The agent works "
                            "out the parameter name and recovers; the failures are left in as recorded." % fails))
            inserted_fail_note = True
        if c.get("who") == "agent":
            m = re.search(r"starts at (\d{4}-\d\d-\d\d)", c.get("text", ""))
            if m and ev.get("demo_first_date") and m.group(1) != ev["demo_first_date"] and "collisions_raw" in json.dumps(chat):
                out.append(note("Editor's note: the data starts on %s, not %s. The agent corrects this two "
                                "steps later." % (ev["demo_first_date"], m.group(1))))
            if "so trust the model" in c.get("text", ""):
                m2 = re.search(r"fit MAPE \*\*([\d.]+%)\*\* vs naive holdout MAPE \*\*([\d.]+%)\*\*", c["text"])
                nums = (" (%s against %s)" % m2.groups()) if m2 else ""
                out.append(note("Editor's note: \"so trust the model\" is not supported. It compares the model's "
                                "in-sample fit error with the baseline's error on held-out months%s; those are "
                                "different tests. The Forecast Lab on the main page compares methods on held-out "
                                "months only." % nums))
    if s["name"].startswith("s5") and fix5:
        parts = " + ".join(format(v, ",") for v in fix5["parts"].values())
        out.append(note("Correction (found by the self-audit): the downloadable report states a total of %s "
                        "electric cars. The query it came from returned %s = %s, so the report understates the "
                        "total by %s. The share it quotes rounds the same either way."
                        % (format(fix5["stated"], ","), parts, format(fix5["correct"], ","), format(fix5["gap"], ","))))
    s["chat"] = out
    return s


# ----------------------------------------------------------------------------- page copy
def hero_html(ev, man):
    t = ev["scale_tables"]
    census = t.get("pums_persons", 0) + t.get("pums_households", 0)
    return (
        '<p class="hero-sub">This is an AI agent pointed at a real, public, multi-dataset database. The demo '
        'sessions use a %s-row NYC collision table (plus a deliberately messy %s-row copy of it). The scale '
        'tier holds %s rows in %d tables: %s NYC 311 requests, %s Dutch vehicles with %s fuel records, %s '
        'Chicago crimes, %s census person and household records, and %s Amazon video-game reviews with %s '
        'product records (row counts from the database profile written on %s). The agent profiles, cleans, '
        'analyses, forecasts and writes the report files itself. Each replay shows the recorded session, with '
        'tool calls summarised, local file paths redacted and editor\'s notes added and marked.</p>'
        % (format(ev["demo_rows"], ","), format(ev["legacy_rows"], ","), fmt_m(ev["scale_total_rows"]), len(t),
           fmt_m(t.get("nyc_311", 0)), fmt_m(t.get("rdw_vehicles", 0)), fmt_m(t.get("rdw_fuel", 0)),
           fmt_m(t.get("chicago_crimes", 0)), fmt_m(census), fmt_m(t.get("amazon_reviews_vg", 0)),
           fmt_k(t.get("amazon_meta_vg", 0)), ev["scale_profile_date"]))


def facts_html(ev, man):
    return (
        '<div class="hero-facts">\n'
        '    <div class="hf"><b>%s rows</b><span>scale-tier database, %d tables</span></div>\n'
        '    <div class="hf"><b>%d tools used</b><span>%s, across these sessions</span></div>\n'
        '    <div class="hf"><b>%s</b><span>remote model (Ollama Cloud); SQL behind a text check, Python not sandboxed</span></div>\n'
        '    <div class="hf"><b>%d report files</b><span>PDF, Excel and Word, open them at the end of each replay</span></div>\n'
        '  </div>\n</div>'
        % (fmt_m(ev["scale_total_rows"]), len(ev["scale_tables"]), len(man["tools"]),
           " · ".join(man["tool_labels"]), esc(", ".join(man["models"])), man["counts"]["report_files"]))


def audit_html(ev, fix5):
    pct = "%.1f%%" % (100 * ev["audit_traceability"])
    tail = (" One arithmetic error was confirmed: the Dutch fleet report's electric-car total, corrected in "
            "that replay." if fix5 else "")
    return (
        '\n<div class="wrap readme" id="how-to-read">\n'
        '  <h2 class="sec">How to read these replays</h2>\n'
        '  <p>A self-audit re-ran every recorded SQL query: %d of %d reproduce (same query, same data). %s of %s '
        'numbers stated in the answers (%s) match a tool output exactly; the rest were not checked one by one, '
        'so no error rate is claimed for them.%s Reproducible is not the same as correct: a query can run '
        'cleanly and still answer a different question.</p>\n'
        '  <p>These sessions call a remote model. The database stays on the analyst\'s machine, but query '
        'results, including sample rows, are sent to the model to reason over. For NorthLedger\'s Data Health '
        'Audit, by contrast, the audit runs recorded on the main page made no AI model calls.</p>\n'
        '</div>\n'
        % (ev["audit_queries_reproduced"], ev["audit_queries_checked"], format(ev["audit_numbers_traced"], ","),
           format(ev["audit_numbers_stated"], ","), pct, tail))


def sources_html(ev):
    t = ev["scale_tables"]
    census = t.get("pums_persons", 0) + t.get("pums_households", 0)
    cells = [
        ("https://data.cityofnewyork.us/Public-Safety/Motor-Vehicle-Collisions-Crashes/qgea-i56i",
         "NYC Motor Vehicle Collisions",
         "NYC Open Data. The demo database: %s crash records from %s to %s, via the Socrata API."
         % (format(ev["demo_rows"], ","), ev["demo_first_date"], ev["demo_last_date"])),
        ("https://www.census.gov/programs-surveys/acs/microdata/documentation.html", "US Census ACS PUMS 2023",
         "Person and household microdata (%s records, survey-weighted) for demographics and economics."
         % fmt_m(census)),
        ("https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2", "Chicago Crimes 2001-present",
         "%s reported incidents: event-stream analysis at scale." % fmt_m(t.get("chicago_crimes", 0))),
        ("https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9",
         "NYC 311 Service Requests",
         "%s requests: operations and resolution-time analysis at scale." % fmt_m(t.get("nyc_311", 0))),
        ("https://amazon-reviews-2023.github.io/", "Amazon Reviews 2023 (UCSD)",
         "The video-games category: %s reviews and %s product records."
         % (fmt_m(t.get("amazon_reviews_vg", 0)), fmt_k(t.get("amazon_meta_vg", 0)))),
        ("https://opendata.rdw.nl/Voertuigen/Open-Data-RDW-Gekentekende-voertuigen/m9d7-ebf2", "RDW Dutch Vehicle Registry",
         "%s registered vehicles and %s fuel records (CC-0): the electrification and fleet-age story."
         % (fmt_m(t.get("rdw_vehicles", 0)), fmt_m(t.get("rdw_fuel", 0)))),
    ]
    rows = "\n".join('    <div class="how-cell"><b><a href="%s" style="color:var(--ink)">%s</a></b><span>%s</span></div>'
                     % (u, esc(n), esc(d)) for u, n, d in cells)
    return (
        '<h2 class="sec" style="margin-top:44px">Where the data comes from</h2>\n'
        '  <p class="foot-note" style="margin-top:10px">All public data, each traceable to its official source. '
        'Licences differ by dataset and are listed in the full manifest, with direct download links, in '
        '<a href="DATA-SOURCES.md" style="color:var(--accent-ink)">DATA-SOURCES.md</a>.</p>\n'
        '  <div class="how-grid">\n%s\n  </div>\n  ' % rows)


CONNECTS = ('<div class="how-cell"><b>Connects</b><span>The SQL tool accepts only statements that start with SELECT '
            'or WITH, on SQLite here. That is a text check on an ordinary connection, not a read-only one, so it is a '
            'guard rather than a guarantee. The Python tool is not sandboxed yet: it runs with '
            'the file access of the machine it is on, which is why these replays redact local paths. The database '
            'stays on the machine; query results, including sample rows, are sent to the remote model to reason '
            'over.</span></div>')
ANALYSES = ('<div class="how-cell"><b>Analyses on command</b><span>SQL for facts, Python for modelling. Its forecast '
            'tool prints a seasonal-naive baseline beside the model, but its error is measured in-sample and its band '
            'widens by a square-root rule; the Forecast Lab on the main page replaces both with backtested '
            'errors.</span></div>')
HONESTY = ('<p class="foot-note">Honesty notes: the demo database is public NYC Open Data (motor vehicle collisions) '
           'with a synthetic legacy-export layer (duplicates, mixed formats) standing in for a client\'s messy copy. '
           'The agent\'s reasoning loop follows Hermes Agent (Nous Research): profile first, cite row counts, compare '
           'against baselines, disclose limits. This page was assembled with AI assistance (Hermes Agent with GLM, '
           'and Claude).</p>')
EXTRA_CSS = """
/* added by tools/sanitize_agent_demo.py */
h2.sec { color: var(--ink); font-size: 26px; line-height: 1.15; letter-spacing: -0.02em; font-weight: 800; margin: 0 0 10px; }
.readme { padding: 0 24px 36px; }
/* .hero, .how and footer set "padding: N 0", which wipes .wrap's side padding: text touched the edge on phones */
.hero.wrap, .how.wrap, footer.wrap { padding-left: 24px; padding-right: 24px; }
@media (max-width: 600px) { .wrap, .hero.wrap, .how.wrap, footer.wrap, .readme { padding-left: 16px; padding-right: 16px; } }
.readme p { max-width: 78ch; font-size: 14.5px; }
.msg.note { max-width: 92%; }
.msg.note .bubble { background: var(--code-bg); border-left: 3px solid #b3423a; color: var(--ink); font-size: 13.4px; border-radius: 8px; }
.archive-label { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 700; color: var(--muted); margin: 10px 2px 0; }
.session-card.archived { opacity: 0.78; border-style: dashed; }
.session-card .sc-archive { display: block; font-size: 11.5px; color: var(--muted); margin-top: 6px; line-height: 1.35; }
.session-card:focus-visible, .btn-replay:focus-visible, a:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.session-anchors { height: 0; overflow: hidden; }
.session-anchors span { display: block; height: 0; scroll-margin-top: 12px; }
/* light by default, dark only when chosen (the same saved choice as the main page: localStorage nl-theme) */
:root { color-scheme: light; }
:root[data-theme="dark"] { color-scheme: dark; }
.theme-btn { flex: none; width: 40px; height: 40px; border-radius: 999px; border: 1px solid var(--hairline); background: var(--surface); cursor: pointer; display: grid; place-items: center; }
.theme-btn .ti { width: 16px; height: 16px; border-radius: 50%; background: linear-gradient(90deg, var(--ink) 50%, transparent 50%); border: 2px solid var(--ink); }
.theme-btn:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
/* on phones the tagline wrapped to four lines and spilled out of the header once the toggle joined it;
   the hero below says the same thing */
@media (max-width: 600px) { .hd .live { display: none; } .hd .theme-btn { margin-left: auto; } }
"""
# runs in <head>, before the page paints: light unless the visitor chose dark on either page
THEME_BOOT = ("<script>(function(){var t=null;try{t=localStorage.getItem('nl-theme');}catch(e){}"
              "document.documentElement.setAttribute('data-theme',t==='dark'?'dark':'light');})();</script>")
THEME_BTN = ('<button type="button" class="theme-btn" id="theme-toggle" aria-label="Switch between light and dark theme">'
             '<span class="ti" aria-hidden="true"></span></button>')
THEME_JS = ("<script>(function(){var b=document.getElementById('theme-toggle'),r=document.documentElement;if(!b)return;"
            "b.addEventListener('click',function(){var n=r.getAttribute('data-theme')==='dark'?'light':'dark';"
            "r.setAttribute('data-theme',n);try{localStorage.setItem('nl-theme',n);}catch(e){}});})();</script>")


def theme_css(text):
    """The upstream page follows the OS dark setting with no way to override it; scope its dark rules to
    data-theme="dark" so the page opens light and the toggle decides."""
    text, n = re.subn(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}\s*\}",
                      lambda m: ':root[data-theme="dark"] {' + m.group(1) + "}", text, count=1, flags=re.S)
    if n != 1:
        raise Missing("expected part of the page not found: dark palette")
    text = replace_once(text, "@media (prefers-color-scheme: dark) { .chat-head .agent-dot { color: #062521; } }",
                        ':root[data-theme="dark"] .chat-head .agent-dot { color: #062521; }', "dark agent dot")
    text = replace_once(text, "@media (prefers-color-scheme: dark) { .dl-btn { color: #062521 !important; } }",
                        ':root[data-theme="dark"] .dl-btn { color: #062521 !important; }', "dark download button")
    if "prefers-color-scheme: dark" in text:
        raise SystemExit("sanitize: a dark-mode rule still follows the OS setting")
    return text
ARCHIVE_JS = """<script>
/* added by tools/sanitize_agent_demo.py: archive divider for sessions kept out of the showcase */
(function () {
  var S = window.SESSIONS, list = document.getElementById("session-list");
  if (!S || !list) return;
  var cards = list.querySelectorAll(".session-card");
  S.sessions.forEach(function (s, i) {
    if (!s.archived || !cards[i]) return;
    cards[i].classList.add("archived");
    if (!list.querySelector(".archive-label")) {
      var h = document.createElement("div");
      h.className = "archive-label";
      h.textContent = "Archive";
      list.insertBefore(h, cards[i]);
    }
    var n = document.createElement("span");
    n.className = "sc-archive";
    n.textContent = s.archive_reason || "";
    cards[i].appendChild(n);
  });
})();
</script>"""


def patch_app(text):
    old_tool = "      } else {\n        el = toolHtml(m);\n      }"
    new_tool = ("      } else if (m.who === \"note\") {\n"
                "        el = '<div class=\"msg note\"><div class=\"who\">Added after the session</div>' +\n"
                "          '<div class=\"bubble\">' + md(m.text) + \"</div></div>\";\n"
                "      } else {\n        el = toolHtml(m);\n      }")
    text = replace_once(text, old_tool, new_tool, "tool branch in the replay script")
    old_show = "  function show(s, idx) {\n    var feed = $(\"chat-feed\");"
    new_show = ("  function show(s, idx) {\n"
                "    // one session at a time: stop the one that is playing before this one starts\n"
                "    if (show._cancel) show._cancel();\n"
                "    var sub = $(\"chat-sub\");\n"
                "    if (sub) sub.textContent = \"replaying a recorded session · \" + (s.database || \"demo database\");\n"
                "    var feed = $(\"chat-feed\");")
    text = replace_once(text, old_show, new_show, "show() in the replay script")
    old_dl = "<div class='dl-note'>Generated by the agent during this session — click to download.</div>"
    new_dl = "<div class='dl-note'>Generated by the agent during this session. Click to download.</div>"
    text = replace_once(text, old_dl, new_dl, "download note")
    old_join = '") — " + esc(d.title)'
    text = replace_once(text, old_join, '"): " + esc(d.title)', "report tool line")
    old_click = ('      el.classList.add("active");\n      show(s, i);\n    });')
    new_click = ('      el.classList.add("active");\n'
                 '      // the address names the session, so it can be shared\n'
                 '      if (window.history && history.replaceState) { try { history.replaceState(null, "", "#" + s.name); } catch (e) { /* some browsers refuse it for a page opened from disk */ } }\n'
                 '      show(s, i);\n    });')
    text = replace_once(text, old_click, new_click, "session card click handler")
    old_start = "  show(S.sessions[0], 0);\n})();"
    new_start = ("  // play the session the address names (agent-demo.html#s5-rdw-fleet), else the first one\n"
                 "  function fromHash() {\n"
                 "    var h = \"\", k = 0;\n"
                 "    try { h = decodeURIComponent((location.hash || \"\").slice(1)); } catch (e) { h = \"\"; }\n"
                 "    S.sessions.forEach(function (s, j) { if (s.name === h) k = j; });\n"
                 "    return k;\n"
                 "  }\n"
                 "  function play(k) {\n"
                 "    cards.forEach(function (c, j) { c.classList.toggle(\"active\", j === k); });\n"
                 "    show(S.sessions[k], k);\n"
                 "  }\n"
                 "  window.addEventListener(\"hashchange\", function () { play(fromHash()); });\n"
                 "  play(fromHash());\n})();")
    text = replace_once(text, old_start, new_start, "the replay start")
    return text


def anchors_html(sessions):
    """One empty anchor per session just above the player, so agent-demo.html#<session> resolves
    (and scrolls to the player); the patched script plays the session the hash names."""
    return ('<div class="session-anchors" aria-hidden="true">%s</div>'
            % "".join('<span id="%s"></span>' % esc(s["name"]) for s in sessions))


# ----------------------------------------------------------------------------- build
def build(src_html, ev, fix5):
    if MARK in src_html:
        raise Missing("the input is already a sanitized page; pass the builder output (agent-demo/agent-demo.html)")
    start = src_html.index("window.SESSIONS = ") + len("window.SESSIONS = ")
    end = src_html.index("</script>", start)
    data = json.loads(src_html[start:end].strip().rstrip(";"))

    counts_all = {}
    featured, archived = [], []
    for s in data["sessions"]:
        c = {"paths": 0, "listings": 0, "env": 0}
        s["chat"] = redact(s["chat"], c)
        for k in ("title", "blurb", "goal_short"):
            if k in s:
                s[k] = redact(s[k], c)
                for a, b in CARD_WORDING.items():
                    s[k] = s[k].replace(a, b)
                if k in EDITORIAL_FIELDS:
                    s[k] = re.sub(r"\s*\u2014\s*", ": ", s[k])
        counts_all[s["name"]] = c
        s["database"] = session_database(s, ev)
        add_notes(s, ev, fix5)
        if s["name"] in ARCHIVE:
            s["archived"], s["archive_reason"], s["featured"] = True, ARCHIVE[s["name"]], False
            archived.append(s)
        else:
            s["archived"], s["featured"] = False, True
            featured.append(s)
    data["sessions"] = featured + archived
    tools = sorted({c.get("tool") for s in data["sessions"] for c in s["chat"] if c.get("who") == "tool"} - {None})
    labels = {"run_sql": "SQL", "profile_table": "profile", "run_python": "Python", "forecast": "forecast",
              "make_report": "reports"}
    order = ["run_sql", "profile_table", "run_python", "forecast", "make_report"]
    tools = [t for t in order if t in tools] + [t for t in tools if t not in order]
    man = {
        "schema": "agent_demo_manifest/1",
        "built_by": "tools/sanitize_agent_demo.py",
        "source_page": "agent-demo/agent-demo.html",
        "source_sha256": hashlib.sha256(src_html.encode("utf-8")).hexdigest(),
        "sessions": [{"id": s["name"], "title": s.get("title"), "model": s.get("model"), "database": s["database"],
                      "featured": s["featured"], "archived": s["archived"], "tool_calls": s.get("n_tools"),
                      "turns": s.get("n_turns"),
                      "report_files": [{"name": d["name"], "bytes": d.get("size")} for d in s.get("downloads", [])],
                      "editor_notes": sum(1 for c in s["chat"] if c.get("who") == "note"),
                      "redactions": counts_all[s["name"]]} for s in data["sessions"]],
        "counts": {"sessions": len(data["sessions"]), "featured": len(featured), "archived": len(archived),
                   "report_files": sum(len(s.get("downloads", [])) for s in data["sessions"])},
        "models": sorted({s.get("model") for s in data["sessions"] if s.get("model")}),
        "tools": tools, "tool_labels": [labels.get(t, t) for t in tools],
        "evidence": ev,
        "s5_correction": fix5,
        "sources": ["agent-demo/big-data-profile.json", "agent-demo/client_data.db", "audit/replay-report.json",
                    "audit/trace-report.json", "agent-demo/sessions/s5-rdw-fleet/transcript.json"],
    }

    out = src_html[:start] + json.dumps(data, ensure_ascii=False) + ";\n" + src_html[end:]
    out = replace_once(out, "<!doctype html>", "<!doctype html>\n" + MARK, "doctype")
    out = sub_once(out, r'<meta name="description" content="[^"]*">',
                   '<meta name="description" content="An AI data analyst run against a real, public, multi-dataset '
                   'database: it profiles messy data, runs SQL and Python, forecasts, and writes PDF, Excel and Word '
                   'reports. Recorded sessions, local paths redacted.">', "meta description")
    out = replace_once(out, '<a class="home" href="../index.html">', '<a class="home" href="index.html">', "back link")
    out = sub_once(out, r'<div class="live"><span class="dot"></span>[^<]*</div>',
                   '<div class="live">Recorded sessions · public data · real report files</div>', "header strip")
    out = sub_once(out, r'<p class="hero-sub">.*?</p>', hero_html(ev, man), "hero paragraph")
    out = sub_once(out, r'<div class="hero-facts">(?:\s*<div class="hf">.*?</div>)+\s*</div>\s*</div>',
                   facts_html(ev, man), "hero facts")
    out = replace_once(out, '<section class="demo">', audit_html(ev, fix5) + '\n' + anchors_html(data["sessions"])
                       + '\n<section class="demo">', "demo section")
    out = sub_once(out, r'<div class="sub">connected to [^<]*</div>',
                   '<div class="sub" id="chat-sub">replaying a recorded session</div>', "chat header")
    out = replace_once(out, '<div id="chat-feed"></div>',
                       '<div id="chat-feed" data-honesty-exempt="quoted agent transcript, shown as recorded"></div>',
                       "chat feed")
    out = replace_once(out, '<h1 style="font-size:26px">How the agent works</h1>',
                       '<h2 class="sec">How the agent works</h2>', "how heading")
    out = sub_once(out, r'<div class="how-cell"><b>Connects</b><span>.*?</span></div>', CONNECTS, "Connects cell")
    out = sub_once(out, r'<div class="how-cell"><b>Analyses on command</b><span>.*?</span></div>', ANALYSES,
                   "Analyses cell")
    out = sub_once(out, r'<h1 style="font-size:26px; margin-top:44px">Where the data comes from</h1>.*?'
                        r'(?=<p class="foot-note">Honesty notes:)', sources_html(ev), "data sources block")
    out = sub_once(out, r'<p class="foot-note">Honesty notes:.*?</p>', HONESTY, "honesty notes")
    out = replace_once(out, "</style>", EXTRA_CSS + "</style>", "style end")
    out = theme_css(out)
    out = replace_once(out, "</head>", THEME_BOOT + "\n</head>", "head end")
    out, n = re.subn(r'(<div class="live">.*?</div>)', lambda m: m.group(1) + "\n    " + THEME_BTN, out, count=1, flags=re.S)
    if n != 1:
        raise Missing("expected part of the page not found: theme button")
    out = replace_once(out, "</header>", "</header>\n<main id=\"main\">", "header end")
    out = replace_once(out, '<footer class="wrap">', '</main>\n<footer class="wrap">', "footer start")
    out = patch_app(out)
    manifest = ('<script type="application/json" id="demo-manifest">%s</script>\n'
                % json.dumps(man, ensure_ascii=False, sort_keys=True).replace("</", "<\\/"))
    out = replace_once(out, "</body>", ARCHIVE_JS + "\n" + THEME_JS + "\n" + manifest + "</body>", "body end")
    return out, man


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sanitize the agent replay page for publishing.")
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--agent-dir", default=AGENT)
    ap.add_argument("--audit-dir", default=AUDIT)
    ap.add_argument("--check", action="store_true", help="rebuild in memory and compare with --out")
    a = ap.parse_args(argv)
    try:
        src_html = open(a.src, encoding="utf-8").read()
        ev = evidence(a.agent_dir, a.audit_dir)
        fix5 = s5_correction(a.agent_dir)
        out, man = build(src_html, ev, fix5)
    except (Missing, OSError, ValueError, KeyError) as e:
        print("SANITIZE: FAIL - %s: %s" % (type(e).__name__, e))
        return 1
    if a.check:
        cur = open(a.out, encoding="utf-8").read() if os.path.exists(a.out) else ""
        if cur != out:
            print("SANITIZE CHECK: FAIL - %s is not what the sanitizer builds from %s" % (
                os.path.basename(a.out), os.path.relpath(a.src, REPO)))
            return 1
        print("SANITIZE CHECK: PASS - %s rebuilds identically" % os.path.basename(a.out))
        return 0
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(a.out)), prefix=".agent-demo.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(out)
    os.chmod(tmp, 0o644)
    os.replace(tmp, a.out)
    red = {k: v for s in man["sessions"] for k, v in [(s["id"], s["redactions"])] if any(v.values())}
    print("wrote %s: %d sessions (%d featured, %d archived), %d editor's notes, redactions %s" % (
        a.out, man["counts"]["sessions"], man["counts"]["featured"], man["counts"]["archived"],
        sum(s["editor_notes"] for s in man["sessions"]), json.dumps(red)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
