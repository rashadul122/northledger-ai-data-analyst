#!/usr/bin/env python3
"""Honesty and shipping checks for the portfolio site. Standard library only.

    python tools/check_site.py                       # every check on the site root
    python tools/check_site.py banned links          # some checks
    python tools/check_site.py --dom index.html=/tmp/index.dom.html   # add a rendered DOM

Checks
  banned    The words "live", "real-time", "every morning" and "daily" may not appear in page
            copy unless a scheduled rebuild exists AND has run: a .github/workflows file with a
            `schedule:` cron, plus data/refresh_stamp.json written by that job. Even then
            "real-time" is never allowed, and "daily" / "every morning" only for a daily cron.
            Quoted third-party text can be exempted with data-honesty-exempt="<reason>".
            The same check fails an em dash in a page's editorial text (the site's style has
            none), and reads the replay page's session data (window.SESSIONS): card titles,
            blurbs, archive notes and editor's notes are editorial and are held to both rules;
            the recorded prompts and answers are quoted as recorded and are not.
  figures   Every element carrying data-fact="<json>:<path>" must show exactly the value at
            that path in data/<json>.json (or demo-data.json for "demo-data"). A string value
            is compared as is; a number needs data-fmt (see FORMATS). Inside an element marked
            data-fact-scope, any digit outside a data-fact element is a hand-typed figure and
            fails (data-fact-exempt="<reason>" opts a node out). index.html must bind at least
            one figure.
  shipped   No local filesystem paths (/Users/<name>, /home/<name>, /var/folders, /private/tmp,
            C:\\Users, file://), no ".env", and no token-shaped secrets in anything the site
            serves, including files embedded as base64 (PDF streams and zip members are opened),
            served zips (the demo's packed engine) and served source files (.py and the like).
  links     Every href/src resolves when the site is served from a GitHub Pages project
            subpath: no root-absolute links, nothing that climbs above the site root, the
            target file exists, #fragments exist, .md links only with a .nojekyll file,
            mailto: addresses are real. External links are listed, not fetched.
  contact   site.config.json must hold a real contact route (email, booking_url or
            form_endpoint) and index.html must link to it.
  collab    The research collaboration route: a "collaboration" block in site.config.json, a
            <section id="collaborate">, a button to it in the header, the hero (below the doors)
            and the Book/About area, and one primary "Invite me to a project" mailto to the
            configured contact_email with the subject "Research collaboration invitation" and a
            short prefilled body (project, role, timeline, data, links).
  pl300     The PL-300 card links the public repository and its data release exactly as encoded
            here (PL300_REPO, PL300_RELEASE_TAG), and nothing else on GitHub outside that
            repository; its status and milestones stay digit-free and keep "AI-built,
            owner-directed". It states the state the repository itself records (PL300_STATE,
            README of 25 Sep 2026): the dev profile refreshed and reconciled in the Power BI
            service, the full profile, RLS and report pages not yet run. Sentences the repository
            now contradicts (PL300_STALE: "not yet run in Power BI", "the workspace is not set up",
            the Microsoft account and Fabric trial as the next step) may not come back.
            Addresses are checked as written, not fetched.
  stray     Nothing that is not part of the site sits in its folder unlisted in .gitignore (the
            publish step is `git add .`): no qa/ output folder, no PDF at the root, no test-*.html
            page other than the verify harness, no retired demo-data.json.
  requests  The site makes no request to another origin while someone reads it: no external
            script, stylesheet, image, frame or media in any page (built or rendered), and no
            outside address in any script or style it serves. Two exceptions, each encoded here
            and nowhere else: (1) the "Try it on your own file" demo's worker, engine/worker.js,
            may load Pyodide from https://cdn.jsdelivr.net/pyodide/ (and only from there); the
            page starts that worker only after a visitor starts the demo; (2) index.html may
            carry the owner's AI proxy address exactly as site.config.json "ai_proxy_url" states
            it, and only while that key is non-empty (the page calls it only after the visitor
            ticks consent). Links a reader clicks (a href) and form actions are navigation, not
            requests; the "links" check reads them.
  promises  Sentences an earlier page made that are false now may not come back, in any page
            (its text and its inline scripts) or in README.md: while the requests check allows
            the demo's outside origin, nothing may say "no third-party requests" or that the site
            "works offline" or has "no dependencies"; and the demo may not say a withheld column
            is dropped "before any analysis", that it is left out of "the story", or that coding a
            column keeps "counts" working (the data-health check still counts a withheld column's
            blanks and the story's data-health lines name it, and a coded column is left out of
            the analysis).
The banned-word check also reads the Markdown files a static host serves from the root
(README.md is exempt: it documents the rule and quotes the words).

Exit code 1 when any check fails. Nothing here writes to the site.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import decimal
import fnmatch
import glob
import html
import io
import json
import os
import posixpath
import re
import sys
import zipfile
import zlib
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = ("banned", "figures", "shipped", "links", "contact", "collab", "pl300", "stray", "requests", "promises")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
        "track", "wbr"}
SKIP_TEXT = {"script", "style", "template"}
SKIP_DIRS = {"qa", "tools", "tests", ".git", "node_modules", "__pycache__"}
TEXT_ATTRS = ("title", "alt", "aria-label", "placeholder", "aria-description")


# ----------------------------------------------------------------------------- tiny DOM
class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "line")

    def __init__(self, tag, attrs=None, parent=None, line=0):
        self.tag, self.attrs, self.children, self.parent, self.line = tag, attrs or {}, [], parent, line

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def text(self, skip=lambda n: False):
        out = []
        for c in self.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in SKIP_TEXT and not skip(c):
                out.append(c.text(skip))
        return "".join(out)

    def ancestors(self):
        n = self
        while n is not None:
            yield n
            n = n.parent


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur, self.getpos()[0])
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        n = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur, self.getpos()[0])
        self.cur.children.append(n)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(text: str) -> Node:
    b = _Builder()
    b.feed(text)
    b.close()
    return b.root


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def snippet(s: str, i: int, j: int, pad: int = 45) -> str:
    return norm_ws(s[max(0, i - pad): j + pad])


# ----------------------------------------------------------------------------- site model
class Site:
    def __init__(self, root: str, doms=None):
        self.root = os.path.abspath(root)
        self.doms = dict(doms or {})           # page name -> rendered DOM path
        self._html = {}
        self._tree = {}

    def pages(self):
        """Shipped HTML pages: *.html in the root, except test harnesses."""
        out = []
        for p in sorted(glob.glob(os.path.join(self.root, "*.html"))):
            b = os.path.basename(p)
            if b.startswith("test-") or b.startswith("test_"):
                continue
            out.append(b)
        return out

    def read(self, rel):
        if rel not in self._html:
            with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as f:
                self._html[rel] = f.read()
        return self._html[rel]

    def tree(self, rel):
        if rel not in self._tree:
            self._tree[rel] = parse_html(self.read(rel))
        return self._tree[rel]

    def views(self, page):
        """(label, tree) for the built HTML and, when given, its rendered DOM."""
        out = [("built", self.tree(page))]
        if page in self.doms and os.path.exists(self.doms[page]):
            with open(self.doms[page], encoding="utf-8", errors="replace") as f:
                out.append(("rendered", parse_html(f.read())))
        return out

    def _walk(self):
        for dp, dns, fns in os.walk(self.root):
            rel_dir = os.path.relpath(dp, self.root)
            parts = [] if rel_dir == "." else rel_dir.split(os.sep)
            if parts and (parts[0] in SKIP_DIRS or parts[0].startswith(".")):
                dns[:] = []
                continue
            dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in sorted(fns):
                if fn.startswith("test-") or fn.startswith("."):
                    continue
                yield os.path.normpath(os.path.join(rel_dir, fn))

    def served_files(self):
        """Every text file a static host would serve that the text checks should read."""
        exts = (".html", ".htm", ".json", ".csv", ".md", ".txt", ".js", ".css", ".xml", ".svg", ".webmanifest")
        return sorted(rel for rel in self._walk() if rel.lower().endswith(exts))

    def served_documents(self):
        """PDF, Office and zip files a static host would serve (opened and scanned like embedded
        files; a zip's members are each read, so the demo's packed engine is scanned too)."""
        return sorted(rel for rel in self._walk() if rel.lower().endswith((".pdf", ".docx", ".xlsx", ".pptx", ".zip")))

    def served_code(self):
        """Source files a static host would serve as plain downloads (the demo's engine adapter and
        its stubs). Only the shipped-paths check reads them: code is not page text."""
        return sorted(rel for rel in self._walk() if rel.lower().endswith((".py", ".pyi", ".mjs", ".cjs", ".toml")))


class Result:
    def __init__(self, name):
        self.name, self.failures, self.notes = name, [], []

    def fail(self, msg):
        self.failures.append(msg)

    def note(self, msg):
        self.notes.append(msg)

    @property
    def ok(self):
        return not self.failures


# ----------------------------------------------------------------------------- banned words
BANNED = {
    "live": re.compile(r"\blive\b", re.I),
    "real-time": re.compile(r"\breal[\s-]?time\b", re.I),
    "every morning": re.compile(r"\bevery\s+morning\b", re.I),
    "daily": re.compile(r"\bdaily\b", re.I),
}


def schedule_status(root: str):
    """(has_schedule, is_daily, has_run_stamp, detail). A schedule counts only when a workflow
    declares a cron AND data/refresh_stamp.json records a completed run of it."""
    crons = []
    for wf in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.y*ml"))):
        txt = open(wf, encoding="utf-8", errors="replace").read()
        if re.search(r"^\s*schedule\s*:", txt, re.M):
            crons += re.findall(r"cron\s*:\s*['\"]([^'\"]+)['\"]", txt)
    stamp_path = os.path.join(root, "data", "refresh_stamp.json")
    stamp = None
    if os.path.exists(stamp_path):
        try:
            stamp = json.load(open(stamp_path))
        except ValueError:
            stamp = None
    has_run = bool(stamp and stamp.get("run_id") and stamp.get("completed_at") and stamp.get("workflow"))
    daily = any(len(c.split()) == 5 and c.split()[2] == "*" and c.split()[4] == "*" and c.split()[3] == "*"
                for c in crons)
    return bool(crons), daily, has_run, {"crons": crons, "stamp": stamp}


def _exempt_reason(n: Node):
    for a in n.ancestors():
        if "data-honesty-exempt" in a.attrs:
            return a.attrs["data-honesty-exempt"].strip() or ""
    return None


EM_DASH = re.compile("\u2014")
EDITORIAL_SESSION_FIELDS = ("title", "blurb", "archive_reason")


def _session_texts(page_html: str):
    """(where, text) for the editorial parts of window.SESSIONS on a replay page: what the page
    writes about a session, not what the agent or the user said in it."""
    m = re.search(r"window\.SESSIONS\s*=\s*", page_html)
    if not m:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html, m.end())
    except ValueError:
        return [("window.SESSIONS", "")]
    out = []
    for s in (data.get("sessions") or []) if isinstance(data, dict) else []:
        name = s.get("name", "?")
        for k in EDITORIAL_SESSION_FIELDS:
            if isinstance(s.get(k), str):
                out.append(("session %s %s" % (name, k), s[k]))
        for i, c in enumerate(s.get("chat") or []):
            if isinstance(c, dict) and c.get("who") == "note" and isinstance(c.get("text"), str):
                out.append(("session %s editor's note %d" % (name, i + 1), c["text"]))
    return out


def check_banned(site: Site) -> Result:
    r = Result("banned")
    has_sched, daily, has_run, detail = schedule_status(site.root)
    allowed = set()
    if has_sched and has_run:
        allowed.add("live")
        if daily:
            allowed |= {"daily", "every morning"}
        r.note("scheduled rebuild found (%s) with a run stamp; allowed: %s"
               % (", ".join(detail["crons"]), ", ".join(sorted(allowed))))
    else:
        r.note("no scheduled rebuild with a recorded run: every banned word is blocked")
    for page in site.pages():
        for label, tree in site.views(page):
            for n in tree.iter():
                if "data-honesty-exempt" in n.attrs and not n.attrs["data-honesty-exempt"].strip():
                    r.fail("%s (%s) line %d: <%s data-honesty-exempt> needs a reason" % (page, label, n.line, n.tag))
            chunks = []

            def skip(n):
                return _exempt_reason(n) not in (None, "")

            for n in tree.iter():
                if n.tag in SKIP_TEXT or skip(n):
                    continue
                own = "".join(c for c in n.children if isinstance(c, str))
                if n.tag not in SKIP_TEXT and own.strip() and not any(a.tag in SKIP_TEXT for a in n.ancestors()):
                    chunks.append(("text <%s> line %d" % (n.tag, n.line), own))
                for a in TEXT_ATTRS:
                    if a in n.attrs:
                        chunks.append(("%s attribute of <%s> line %d" % (a, n.tag, n.line), n.attrs[a]))
                if n.tag == "meta" and n.attrs.get("content") and (
                        n.attrs.get("name", "").lower() in ("description", "twitter:title", "twitter:description")
                        or n.attrs.get("property", "").lower().startswith("og:")):
                    chunks.append(("meta %s" % (n.attrs.get("name") or n.attrs.get("property")), n.attrs["content"]))
            if label == "built":
                chunks += _session_texts(site.read(page))
            seen = set()
            for where, s in chunks:
                for m in EM_DASH.finditer(s):
                    key = ("em dash", snippet(s, m.start(), m.end()))
                    if key not in seen:
                        seen.add(key)
                        r.fail('%s (%s) %s: em dash in editorial text "...%s..."' % (page, label, where, key[1]))
                for word, rx in BANNED.items():
                    if word in allowed:
                        continue
                    for m in rx.finditer(s):
                        key = (word, snippet(s, m.start(), m.end()))
                        if key in seen:
                            continue
                        seen.add(key)
                        r.fail('%s (%s) %s: "%s" in "...%s..."' % (page, label, where, m.group(0), key[1]))
    # Markdown served from the root is read by visitors too
    for md in sorted(glob.glob(os.path.join(site.root, "*.md"))):
        name = os.path.basename(md)
        if name in MD_EXEMPT:
            r.note("%s not scanned: %s" % (name, MD_EXEMPT[name]))
            continue
        text = open(md, encoding="utf-8", errors="replace").read()
        for word, rx in BANNED.items():
            if word in allowed:
                continue
            for m in rx.finditer(text):
                r.fail('%s line %d: "%s" in "...%s..."' % (name, text.count("\n", 0, m.start()) + 1, m.group(0),
                                                          snippet(text, m.start(), m.end())))
    return r


MD_EXEMPT = {"README.md": "it documents the banned-word rule, so it quotes the words"}


# ----------------------------------------------------------------------------- stray files
STRAY_ALLOWED_TEST_PAGES = {"test-driver.html"}      # verify.sh injects it into a copy of the page


def _gitignore(root):
    pats = []
    p = os.path.join(root, ".gitignore")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line.lstrip("/"))
    return pats


def _ignored(name, is_dir, pats):
    for pat in pats:
        if pat.endswith("/"):
            if is_dir and fnmatch.fnmatch(name, pat[:-1]):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


def check_stray(site: Site) -> Result:
    r = Result("stray")
    pats = _gitignore(site.root)
    found = 0
    for name in sorted(os.listdir(site.root)):
        if name.startswith("."):
            continue
        full = os.path.join(site.root, name)
        is_dir = os.path.isdir(full)
        why = None
        if is_dir and name == "qa":
            why = "verify.sh output (screenshots, a print PDF), not part of the site"
        elif not is_dir and name.lower().endswith(".pdf"):
            why = "a PDF at the site root; downloads the page offers live in downloads/"
        elif (not is_dir and name.startswith(("test-", "test_")) and name.endswith(".html")
              and name not in STRAY_ALLOWED_TEST_PAGES):
            why = "a test page, not part of the site"
        elif not is_dir and name == "demo-data.json":
            why = "the retired v1 page's data"
        if why is None:
            continue
        found += 1
        if _ignored(name, is_dir, pats):
            r.note("%s%s is listed in .gitignore (%s)" % (name, "/" if is_dir else "", why))
        else:
            r.fail("%s%s would be published with the site: %s. Move it out of the folder or list it in .gitignore"
                   % (name, "/" if is_dir else "", why))
    r.note("%d stray candidate(s) at the site root" % found)
    return r


# ----------------------------------------------------------------------------- figures
def fmt_value(v, fmt):
    if fmt in (None, "", "raw"):
        return str(v)
    name, _, arg = fmt.partition(":")
    dp = int(arg) if arg else 0
    if name == "int":
        return format(int(round(v)), ",")
    if name == "fixed":
        return format(v, ",.%df" % dp)
    if name == "half":                      # decimal half-up, binary noise removed first (the page's U.half)
        q = decimal.Decimal(1).scaleb(-dp)
        return format(decimal.Decimal(format(v, ".12g")).quantize(q, rounding=decimal.ROUND_HALF_UP), ",")
    if name == "pct":
        return format(v, ",.%df" % dp) + "%"
    if name == "spct":
        s = format(v, ",.%df" % dp) + "%"
        return "+" + s if v >= 0 else s
    if name == "usd_b":                     # value in US$ millions
        return "$" + format(v / 1000.0, ",.%df" % dp) + "B"
    if name == "usd_m":
        return "$" + format(v, ",.%df" % dp) + "M"
    if name == "usd":
        return "$" + format(v, ",.%df" % dp)
    raise ValueError("unknown data-fmt %r" % fmt)


FORMATS = "raw, int, fixed:N, half:N (decimal half-up), pct:N, spct:N (signed), usd:N, usd_m:N, usd_b:N (value in US$ millions)"


def load_sources(root: str):
    src = {}
    for p in sorted(glob.glob(os.path.join(root, "data", "*.json"))):
        try:
            src[os.path.splitext(os.path.basename(p))[0]] = json.load(open(p))
        except ValueError as e:
            src[os.path.splitext(os.path.basename(p))[0]] = e
    dd = os.path.join(root, "demo-data.json")
    if os.path.exists(dd):
        try:
            src["demo-data"] = json.load(open(dd))
        except ValueError as e:
            src["demo-data"] = e
    return src


def resolve(obj, path: str):
    cur = obj
    for tok in [t for t in path.split(".") if t != ""]:
        if isinstance(cur, list):
            cur = cur[int(tok)]
        elif isinstance(cur, dict):
            cur = cur[tok]
        else:
            raise KeyError(tok)
    return cur


def check_figures(site: Site) -> Result:
    r = Result("figures")
    sources = load_sources(site.root)
    total = 0
    for page in site.pages():
        page_bound = 0
        for label, tree in site.views(page):
            for n in tree.iter():
                if "data-fact" not in n.attrs:
                    continue
                total += 1
                page_bound += 1
                ref = n.attrs["data-fact"]
                shown = norm_ws(n.text())
                where = "%s (%s) line %d data-fact=%r" % (page, label, n.line, ref)
                src, _, path = ref.partition(":")
                if src not in sources:
                    r.fail("%s: no data/%s.json" % (where, src))
                    continue
                if isinstance(sources[src], Exception):
                    r.fail("%s: data/%s.json does not parse" % (where, src))
                    continue
                try:
                    v = resolve(sources[src], path)
                except (KeyError, IndexError, ValueError):
                    r.fail("%s: path %r not found in %s" % (where, path, src))
                    continue
                if isinstance(v, (dict, list)) or v is None:
                    r.fail("%s: %r is not a single value" % (where, path))
                    continue
                fmt = n.attrs.get("data-fmt")
                if isinstance(v, (int, float)) and not isinstance(v, bool) and not fmt:
                    if shown != str(v):
                        r.fail("%s: a number needs data-fmt (%s); shows %r, value %r" % (where, FORMATS, shown, v))
                    continue
                try:
                    want = fmt_value(v, fmt) if not isinstance(v, str) else v
                except (ValueError, TypeError) as e:
                    r.fail("%s: %s" % (where, e))
                    continue
                if shown == "" and label == "built":
                    r.fail("%s: empty in the built HTML (reads blank with JavaScript off); expected %r" % (where, want))
                elif shown != norm_ws(want):
                    r.fail("%s: shows %r but the JSON says %r" % (where, shown, want))
            # hand-typed digits inside a figure scope
            for n in tree.iter():
                if "data-fact-scope" not in n.attrs:
                    continue
                for m in n.iter():
                    if m.tag in SKIP_TEXT:
                        continue
                    own = "".join(c for c in m.children if isinstance(c, str))
                    if not re.search(r"\d", own):
                        continue
                    anc = list(m.ancestors())
                    if any("data-fact" in a.attrs for a in anc) or any(a.tag in SKIP_TEXT for a in anc):
                        continue
                    ex = [a for a in anc if "data-fact-exempt" in a.attrs]
                    if ex and ex[0].attrs["data-fact-exempt"].strip():
                        continue
                    r.fail("%s (%s) line %d: figure %r inside data-fact-scope is not bound to JSON"
                           % (page, label, m.line, norm_ws(own)[:80]))
        if page == "index.html" and page_bound == 0:
            r.fail("index.html binds no figure to JSON: mark each figure with data-fact=\"<json>:<path>\" "
                   "(see tools/check_site.py) so this check can prove it equals its source")
    r.note("%d bound figure(s) checked against %d JSON source(s)" % (total, len(sources)))
    return r


# ----------------------------------------------------------------------------- shipped content
PATH_PATTERNS = [
    ("home directory path", re.compile(r"/Users/[A-Za-z0-9._-]+")),
    ("home directory path", re.compile(r"/home/[a-z_][a-z0-9_-]*/")),
    ("Windows user path", re.compile(r"[A-Za-z]:\\\\?Users\\\\?", re.I)),
    ("temp path", re.compile(r"/private/(?:var|tmp)/|/var/folders/")),
    ("file:// URL", re.compile(r"file://", re.I)),
    (".env file", re.compile(r"(?<![A-Za-z0-9_$.])\.env(?![A-Za-z0-9_])")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
B64_RX = [re.compile(r'"b64"\s*:\s*"([A-Za-z0-9+/=\\n]{64,})"'),
          re.compile(r"data:[^;,\"'\s]*;base64,([A-Za-z0-9+/=]{64,})")]


def _scan_text(text: str, where: str, r: Result):
    for label, rx in PATH_PATTERNS:
        for m in rx.finditer(text):
            r.fail('%s: %s "%s" in "...%s..."' % (where, label, m.group(0), snippet(text, m.start(), m.end(), 30)))


def _embedded_texts(blob: bytes):
    """Readable text inside an embedded file: zip members, PDF streams (inflated), or raw."""
    out = []
    if blob[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for info in z.infolist():
                    out.append(("zip member %s" % info.filename, z.read(info).decode("utf-8", "replace")))
        except zipfile.BadZipFile:
            out.append(("raw", blob.decode("latin-1")))
    elif blob[:5] == b"%PDF-":
        out.append(("pdf raw", blob.decode("latin-1")))
        for i, m in enumerate(re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", blob, re.S)):
            try:
                out.append(("pdf stream %d" % i, zlib.decompress(m.group(1)).decode("latin-1")))
            except zlib.error:
                pass
    else:
        out.append(("raw", blob.decode("latin-1")))
    return out


def check_shipped(site: Site) -> Result:
    r = Result("shipped")
    files = site.served_files()
    embedded = 0
    for rel in files:
        text = open(os.path.join(site.root, rel), encoding="utf-8", errors="replace").read()
        _scan_text(text, rel, r)
        for rx in B64_RX:
            for k, m in enumerate(rx.finditer(text)):
                raw = m.group(1).replace("\\n", "")
                try:
                    blob = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False)
                except (binascii.Error, ValueError):
                    continue
                embedded += 1
                for part, t in _embedded_texts(blob):
                    _scan_text(t, "%s embedded file #%d (%s)" % (rel, k + 1, part), r)
    code = site.served_code()
    for rel in code:
        _scan_text(open(os.path.join(site.root, rel), encoding="utf-8", errors="replace").read(), rel, r)
    docs = site.served_documents()
    for rel in docs:
        blob = open(os.path.join(site.root, rel), "rb").read()
        for part, t in _embedded_texts(blob):
            _scan_text(t, "%s (%s)" % (rel, part), r)
    r.note("%d served file(s), %d served code file(s), %d served document(s) or zip(s) and %d embedded file(s) scanned"
           % (len(files), len(code), len(docs), embedded))
    return r


# ----------------------------------------------------------------------------- links
LINK_ATTRS = {"a": "href", "link": "href", "area": "href", "script": "src", "img": "src", "iframe": "src",
              "source": "src", "video": "src", "audio": "src", "embed": "src", "form": "action"}
PLACEHOLDER = re.compile(r"example\.(?:com|org|net)|\byour[-_.@]|@your|\bname@|changeme|\btodo\b|\btbd\b|xxx|"
                         r"placeholder|localhost|127\.0\.0\.1|\btest@", re.I)
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _ids(tree: Node):
    out = set()
    for n in tree.iter():
        if n.attrs.get("id"):
            out.add(n.attrs["id"])
        if n.tag == "a" and n.attrs.get("name"):
            out.add(n.attrs["name"])
    return out


def check_links(site: Site, project: str = "northledger-ai-data-analyst") -> Result:
    r = Result("links")
    base = "/%s/" % project
    external = set()
    checked = 0
    nojekyll = os.path.exists(os.path.join(site.root, ".nojekyll"))
    id_cache = {}

    def ids_of(rel):
        if rel not in id_cache:
            ids = set()
            for _label, t in site.views(rel):
                ids |= _ids(t)
            id_cache[rel] = ids
        return id_cache[rel]

    for page in site.pages():
        for label, tree in site.views(page):
            for n in tree.iter():
                attr = LINK_ATTRS.get(n.tag)
                if not attr or attr not in n.attrs:
                    continue
                url = html.unescape(n.attrs[attr]).strip()
                where = "%s (%s) line %d <%s %s=%r>" % (page, label, n.line, n.tag, attr, url[:120])
                checked += 1
                low = url.lower()
                if url == "":
                    if n.tag == "a":
                        r.fail("%s: empty link" % where)
                    continue
                if low.startswith("data:") or low.startswith("blob:"):
                    continue
                if low.startswith("javascript:"):
                    r.fail("%s: javascript: link" % where)
                    continue
                if low.startswith("mailto:"):
                    addr = url[7:].split("?")[0]
                    if not EMAIL.match(addr) or PLACEHOLDER.search(addr):
                        r.fail("%s: mailto address %r is not a real address" % (where, addr))
                    continue
                if low.startswith("tel:"):
                    continue
                if re.match(r"^[a-z][a-z0-9+.-]*:", low) or low.startswith("//"):
                    if not (low.startswith("https://") or low.startswith("http://") or low.startswith("//")):
                        r.fail("%s: unsupported scheme" % where)
                    elif PLACEHOLDER.search(url):
                        r.fail("%s: placeholder URL" % where)
                    else:
                        external.add(url)
                    continue
                path, _, frag = url.partition("#")
                path = path.split("?")[0]
                if path == "":
                    if frag and frag not in ids_of(page):
                        r.fail("%s: no element with id %r on this page" % (where, frag))
                    continue
                if path.startswith("/"):
                    r.fail("%s: root-absolute link breaks under a project URL (%s...)" % (where, base))
                    continue
                served = posixpath.normpath(posixpath.join(base, posixpath.dirname(page), path))
                if not (served + "/").startswith(base):
                    r.fail("%s: climbs above the site root; served from %s it resolves to %s" % (where, base, served))
                    continue
                rel = served[len(base):]
                local = os.path.join(site.root, *rel.split("/"))
                if os.path.isdir(local):
                    local = os.path.join(local, "index.html")
                    rel = posixpath.join(rel, "index.html")
                if not os.path.exists(local):
                    r.fail("%s: %s does not exist" % (where, rel))
                    continue
                if rel.lower().endswith(".md") and not nojekyll:
                    r.fail("%s: links a Markdown file; without a .nojekyll file GitHub Pages converts it and "
                           "this URL can 404 (add .nojekyll or link a rendered page)" % where)
                if frag and rel.lower().endswith((".html", ".htm")) and frag not in ids_of(rel):
                    r.fail("%s: %s has no element with id %r" % (where, rel, frag))
    r.note("%d link(s) checked as served from %s; %d external link(s) not fetched" % (checked, base, len(external)))
    return r


# ----------------------------------------------------------------------------- contact
def _get(d, *paths):
    for p in paths:
        cur = d
        ok = True
        for k in p.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and isinstance(cur, str) and cur.strip():
            return cur.strip()
    return None


def check_contact(site: Site) -> Result:
    r = Result("contact")
    p = os.path.join(site.root, "site.config.json")
    if not os.path.exists(p):
        r.fail("site.config.json not found. Add a contact route the page can use: \"contact\": {\"email\": ..., "
               "\"booking_url\": ..., \"form_endpoint\": ...}. The owner creates any booking or form account; "
               "this check will not pass on a placeholder.")
        return r
    try:
        cfg = json.load(open(p))
    except ValueError as e:
        r.fail("site.config.json does not parse: %s" % e)
        return r
    email = _get(cfg, "contact.email", "contact_email", "email")
    booking = _get(cfg, "contact.booking_url", "booking_url", "contact.booking")
    form = _get(cfg, "contact.form_endpoint", "form_endpoint", "contact.form_action")
    routes = []
    if email:
        if EMAIL.match(email) and not PLACEHOLDER.search(email):
            routes.append(("email", email, "mailto:" + email))
        else:
            r.fail("site.config.json contact email %r is not a real address" % email)
    for kind, url in (("booking_url", booking), ("form_endpoint", form)):
        if url:
            if url.startswith("https://") and not PLACEHOLDER.search(url):
                routes.append((kind, url, url))
            else:
                r.fail("site.config.json %s %r is not a real https URL" % (kind, url))
    if not routes:
        if not r.failures:
            r.fail("site.config.json has no contact route: fill contact_email and/or booking_url (https) in "
                   "site.config.json, then run build.py (the owner creates any booking account)")
        return r
    if "index.html" not in site.pages():
        r.fail("index.html not found")
        return r
    targets = set()
    for _label, tree in site.views("index.html"):
        for n in tree.iter():
            for a in ("href", "action"):
                if n.attrs.get(a):
                    targets.add(html.unescape(n.attrs[a]).strip())
    linked = [k for k, _v, t in routes if any(x == t or x.split("?")[0] == t for x in targets)]
    if not linked:
        r.fail("site.config.json has a contact route (%s) but index.html links none of them"
               % ", ".join(k for k, _v, _t in routes))
    else:
        r.note("contact route(s) configured and linked: %s" % ", ".join(linked))
    return r


# ----------------------------------------------------------------------------- research collaboration
# The invitation's subject line, and the places a reader finds the button (header nav, the hero right
# below the doors, the Book/About area), encoded here and nowhere else.
COLLAB_SECTION = "collaborate"
COLLAB_SUBJECT = "Research collaboration invitation"
COLLAB_PLACES = (("header", "topbar"), ("hero", "top"), ("book or about", ("book", "about")))
COLLAB_BODY_WORDS = ("project", "role", "timeline", "data", "links")


def _query(url):
    from urllib.parse import parse_qs, unquote, urlsplit
    q = urlsplit(url).query
    return unquote(url[7:].split("?")[0]), {k: v[0] for k, v in parse_qs(q, keep_blank_values=True).items()}


def check_collab(site: Site) -> Result:
    r = Result("collab")
    try:
        cfg = json.load(open(os.path.join(site.root, "site.config.json"), encoding="utf-8"))
    except (OSError, ValueError) as e:
        r.fail("site.config.json cannot be read: %s" % e)
        return r
    block = cfg.get("collaboration") if isinstance(cfg.get("collaboration"), dict) else None
    # the invitation goes to collaboration.email (the owner's university address) when set, else contact_email
    email = ((block or {}).get("email") or "").strip() or _get(cfg, "contact.email", "contact_email", "email")
    if not block:
        r.fail('site.config.json has no "collaboration" block (the invitation\'s subject and prompts live there)')
    elif (block.get("subject") or "").strip() != COLLAB_SUBJECT:
        r.fail("site.config.json collaboration.subject is %r, not %r" % (block.get("subject"), COLLAB_SUBJECT))
    if not email or not EMAIL.match(email):
        r.fail("site.config.json has no collaboration.email or contact_email for the invitation to go to")
    if "index.html" not in site.pages():
        r.fail("index.html not found")
        return r
    for label, tree in site.views("index.html"):
        by_id = {n.attrs["id"]: n for n in tree.iter() if n.attrs.get("id")}
        sec = by_id.get(COLLAB_SECTION)
        if sec is None or sec.tag != "section":
            r.fail("index.html (%s): no <section id=%r> for research collaboration" % (label, COLLAB_SECTION))
            continue
        # the button in each place: a link to the section whose own words say research collaboration
        for place, ids in COLLAB_PLACES:
            ids = ids if isinstance(ids, tuple) else (ids,)
            hits = [a for i in ids if i in by_id for a in by_id[i].iter()
                    if a.tag == "a" and a.attrs.get("href") == "#" + COLLAB_SECTION]
            named = [a for a in hits if re.search(r"research collaboration|collaborat", norm_ws(a.text()), re.I)]
            if not named:
                r.fail("index.html (%s): no research collaboration button in the %s (a link to #%s inside #%s)"
                       % (label, place, COLLAB_SECTION, " or #".join(ids)))
        # the invitation: one mailto, to the configured address, with the subject and a short prefilled body
        mails = [a for a in sec.iter() if a.tag == "a" and html.unescape(a.attrs.get("href", "")).lower().startswith("mailto:")]
        invite = [a for a in mails if re.search(r"invite me to a project", norm_ws(a.text()), re.I)]
        if not invite:
            r.fail('index.html (%s): the collaboration section has no "Invite me to a project" email button' % label)
            continue
        for a in mails:
            addr, q = _query(html.unescape(a.attrs["href"]))
            where = "index.html (%s) line %d" % (label, a.line)
            if email and addr != email:
                r.fail("%s: the invitation goes to %r, not to site.config.json collaboration.email / contact_email %r" % (where, addr, email))
            if q.get("subject") != COLLAB_SUBJECT:
                r.fail("%s: the invitation's subject is %r, not %r" % (where, q.get("subject"), COLLAB_SUBJECT))
            body = (q.get("body") or "").lower()
            missing = [w for w in COLLAB_BODY_WORDS if w not in body]
            if missing:
                r.fail("%s: the prefilled body does not ask for %s" % (where, ", ".join(missing)))
            if len(body) > 400:
                r.fail("%s: the prefilled body is %d characters; keep it short" % (where, len(body)))
        if "btn-primary" not in invite[0].attrs.get("class", ""):
            r.fail("index.html (%s): the invitation button is not the section's primary button" % label)
    if not r.failures:
        r.note("collaboration buttons in the header, hero and book/about area; invitation to %s, subject %r" % (email, COLLAB_SUBJECT))
    return r


# ----------------------------------------------------------------------------- PL-300 card
PL300_REPO = "https://github.com/rashadul122/pl300-nyc311"
PL300_RELEASE_TAG = "data-v1"
PL300_RELEASE = PL300_REPO + "/releases/tag/" + PL300_RELEASE_TAG
# What the public repository records (README "Status (25 Sep 2026)" and docs/evidence/refresh-3-dev.md
# at commit 4da5ffbc): the dev profile refreshed and reconciled in the Power BI service; the full
# profile, RLS and the report pages are still [unrun]. The card must say both halves.
PL300_STATE = ("dev profile", "reconciled", "power bi service", "full profile", "rls", "report pages", "not yet run")
# Sentences the card made before that run, which the repository now contradicts.
PL300_STALE = (r"not yet run in power bi\b", r"workspace is not set up", r"microsoft work account",
               r"no reconcile result has been recorded")


def check_pl300(site: Site) -> Result:
    r = Result("pl300")
    try:
        pl = json.load(open(os.path.join(site.root, "data", "pl300-status.json"), encoding="utf-8"))
    except (OSError, ValueError) as e:
        r.fail("data/pl300-status.json cannot be read: %s" % e)
        return r
    repo = ((pl.get("repo") or {}).get("url") or "").strip()
    rel = pl.get("release") or {}
    if repo != PL300_REPO:
        r.fail("pl300-status.json repo.url is %r, not the public project %r" % (repo, PL300_REPO))
    if (rel.get("tag") or "") != PL300_RELEASE_TAG or (rel.get("url") or "").strip() != PL300_RELEASE:
        r.fail("pl300-status.json release is %r / %r, not %r / %r" % (rel.get("tag"), rel.get("url"), PL300_RELEASE_TAG, PL300_RELEASE))
    # the state words stay digit-free, say what has run and what has not
    words = [pl.get("status") or ""] + [m.get("state", "") + " " + m.get("name", "") for m in pl.get("milestones") or []]
    if any(re.search(r"\d", w) for w in words):
        r.fail("pl300-status.json status or milestones carry a digit: %s" % [w for w in words if re.search(r"\d", w)][:3])
    st = (pl.get("status") or "").lower()
    missing = [w for w in PL300_STATE if w not in st]
    if missing:
        r.fail("pl300-status.json status %r does not state what the repository records (missing %s)" % (pl.get("status"), missing))
    if "ai-built, owner-directed" not in (pl.get("authorship") or "").lower():
        r.fail('pl300-status.json authorship does not say "AI-built, owner-directed"')
    nxt = (pl.get("next_step") or "").lower()
    if not ("full profile" in nxt and "rls" in nxt):
        r.fail("pl300-status.json next_step %r does not name the remaining runs (full profile, RLS)" % pl.get("next_step"))
    stale_json = " ".join(str(pl.get(k) or "") for k in ("status", "route", "method", "next_step")) + " " + \
        " ".join(m.get("state", "") + " " + m.get("name", "") for m in pl.get("milestones") or [])
    for pat in PL300_STALE:
        if re.search(pat, stale_json, re.I):
            r.fail("pl300-status.json still says %r, which the repository now contradicts" % pat)
    if "index.html" not in site.pages():
        r.fail("index.html not found")
        return r
    for label, tree in site.views("index.html"):
        card = next((n for n in tree.iter() if n.attrs.get("id") == "pl300"), None)
        if card is None:
            r.fail("index.html (%s): no #pl300 section" % label)
            continue
        hrefs = {html.unescape(a.attrs.get("href", "")).strip() for a in card.iter() if a.tag == "a"}
        for want, what in ((PL300_REPO, "the public repository"), (PL300_RELEASE, "the %s release" % PL300_RELEASE_TAG)):
            if want not in hrefs:
                r.fail("index.html (%s): the PL-300 card does not link %s (%s)" % (label, what, want))
        bad = sorted(h for h in hrefs if "github.com" in h and not (h == PL300_REPO or h.startswith(PL300_REPO + "/")))
        if bad:
            r.fail("index.html (%s): the PL-300 card links another GitHub address: %s" % (label, ", ".join(bad)))
        text = norm_ws(card.text()).lower()
        for must in ("ai-built, owner-directed",) + PL300_STATE:
            if must not in text:
                r.fail("index.html (%s): the PL-300 card does not say %r" % (label, must))
        for pat in PL300_STALE:
            m = re.search(pat, text, re.I)
            if m:
                r.fail("index.html (%s): the PL-300 card still says %r, which the repository now contradicts" % (label, m.group(0)))
    if not r.failures:
        r.note("the PL-300 card links %s and %s (addresses checked here, not fetched)" % (PL300_REPO, PL300_RELEASE))
    return r


# ----------------------------------------------------------------------------- runtime requests
# The one outside origin the site may load at runtime, and the only file allowed to load it.
DEMO_WORKER = "engine/worker.js"
DEMO_RUNTIME_PREFIX = "https://cdn.jsdelivr.net/pyodide/"
# XML namespace names look like URLs but are never fetched
NAMESPACES = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xhtml", "http://www.w3.org/1999/xlink",
              "http://www.w3.org/XML/1998/namespace", "http://www.w3.org/2000/xmlns/")
ABS_URL = re.compile(r"(?i)\b(?:https?|wss?)://[^\s'\"`)<>\\,;]+")
PROTO_REL = re.compile(r"""['"`(]\s*(//[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+[^\s'"`)<>\\]*)""")
CSS_URL = re.compile(r"""(?i)(?:url\(\s*['"]?|@import\s+['"])\s*((?:[a-z][a-z0-9+.-]*:)?//[^'")\s]+)""")
# attributes that make the browser fetch something as the page loads (a href and form action
# are navigation, started by the reader)
REQUEST_ATTRS = {"script": ("src",), "link": ("href",), "img": ("src", "srcset"), "iframe": ("src",),
                 "source": ("src", "srcset"), "video": ("src", "poster"), "audio": ("src",), "embed": ("src",),
                 "object": ("data",), "track": ("src",), "image": ("href", "xlink:href"), "use": ("href", "xlink:href"),
                 "input": ("src",), "frame": ("src",)}


def _proxy_url(root):
    try:
        cfg = json.load(open(os.path.join(root, "site.config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    v = cfg.get("ai_proxy_url") if isinstance(cfg, dict) else ""
    return v.strip() if isinstance(v, str) else ""


def _urls_in_code(text):
    """Every outside address written in script or style text: absolute (http, https, ws, wss),
    protocol-relative inside a string or url(), and CSS url()/@import targets."""
    out = [m.group(0).rstrip(".") for m in ABS_URL.finditer(text)]
    out += [m.group(1) for m in PROTO_REL.finditer(text)]
    out += [m.group(1) for m in CSS_URL.finditer(text) if not ABS_URL.match(m.group(1))]
    return out


def _is_outside(url):
    low = url.strip().lower()
    return bool(re.match(r"^(?:https?:|wss?:)?//", low)) and not any(low.startswith(ns.lower()) for ns in NAMESPACES)


def check_requests(site: Site) -> Result:
    r = Result("requests")
    proxy = _proxy_url(site.root)
    seen = set()
    used = {"runtime": 0, "proxy": 0}

    def judge(where, rel, url, in_script):
        if not _is_outside(url):
            return
        if rel == DEMO_WORKER and url.startswith(DEMO_RUNTIME_PREFIX):
            used["runtime"] += 1
            return
        if rel == "index.html" and in_script and proxy and url == proxy:
            used["proxy"] += 1
            return
        why = ""
        if url.startswith(DEMO_RUNTIME_PREFIX) or "cdn.jsdelivr.net" in url:
            why = " (Pyodide from cdn.jsdelivr.net/pyodide/ is allowed in %s only)" % DEMO_WORKER
        elif proxy and url == proxy:
            why = " (the AI proxy is called from index.html, not from %s)" % rel
        elif not proxy and "workers.dev" in url:
            why = " (site.config.json ai_proxy_url is empty, so no proxy address may ship)"
        key = (where, url)
        if key not in seen:
            seen.add(key)
            r.fail("%s: request to another origin %r%s" % (where, url[:120], why))

    for rel in site.served_files():
        low = rel.lower()
        if not low.endswith((".js", ".css")):
            continue
        text = open(os.path.join(site.root, rel), encoding="utf-8", errors="replace").read()
        for u in _urls_in_code(text):
            judge(rel.replace(os.sep, "/"), rel.replace(os.sep, "/"), u, low.endswith(".js"))
    for page in site.pages():
        for label, tree in site.views(page):
            for n in tree.iter():
                where = "%s (%s) line %d <%s>" % (page, label, n.line, n.tag)
                for attr in REQUEST_ATTRS.get(n.tag, ()):
                    v = html.unescape(n.attrs.get(attr, "")).strip()
                    if n.tag == "link" and n.attrs.get("rel", "").lower() in ("canonical", "alternate", "author", "license"):
                        continue
                    for part in ([x.strip().split(" ")[0] for x in v.split(",")] if attr == "srcset" else [v]):
                        if part:
                            judge(where + " " + attr, page, part, False)
                if "style" in n.attrs:
                    for u in _urls_in_code(n.attrs["style"]):
                        judge(where + " style attribute", page, u, False)
                if n.tag in ("script", "style") and "src" not in n.attrs:
                    code = "".join(c for c in n.children if isinstance(c, str))
                    for u in _urls_in_code(code):
                        judge(where, page, u, n.tag == "script")
    r.note("allowed outside origin: %s in %s only (%d reference(s))" % (DEMO_RUNTIME_PREFIX, DEMO_WORKER, used["runtime"]))
    r.note("AI proxy: %s" % (("%s, in index.html only (%d reference(s))" % (proxy, used["proxy"])) if proxy
                             else "not configured (site.config.json ai_proxy_url is empty), so none may ship"))
    return r


# ----------------------------------------------------------------------------- promises
# (phrase, why it is false, whether it depends on the demo's outside origin being allowed)
FALSE_PROMISES = (
    (r"no third[- ]party requests", "the Try-it demo fetches Pyodide from cdn.jsdelivr.net (the requests check allows it)", True),
    (r"works offline", "the Try-it demo needs cdn.jsdelivr.net and the packed engine", True),
    (r"no dependencies", "the Try-it demo depends on Pyodide from cdn.jsdelivr.net", True),
    (r"before any analysis", "a withheld column is still profiled by the data-health check (counts, never values)", False),
    (r"dropped before analysis", "a withheld column is still profiled by the data-health check (counts, never values)", False),
    (r"counts still work", "a coded column is left out of the analysis like a withheld one", False),
    (r"left out of the business analysis, the story", "the story's data-health lines name a withheld column "
     "(its blanks and spellings, never a value); only the AI payload leaves out every line that names it", False),
)


def check_promises(site: Site) -> Result:
    r = Result("promises")
    demo = os.path.exists(os.path.join(site.root, *DEMO_WORKER.split("/")))
    rules = [(re.compile(p, re.I), why) for p, why, needs_demo in FALSE_PROMISES if demo or not needs_demo]
    sources = [(page, site.read(page)) for page in site.pages()]
    for label, path in sorted((site.doms or {}).items()):
        try:
            sources.append(("%s (rendered)" % label, open(path, encoding="utf-8", errors="replace").read()))
        except OSError:
            pass
    readme = os.path.join(site.root, "README.md")
    if os.path.exists(readme):
        sources.append(("README.md", open(readme, encoding="utf-8", errors="replace").read()))
    for where, raw in sources:
        text = " ".join(html.unescape(raw).split())
        for rx, why in rules:
            for m in rx.finditer(text):
                r.fail('%s: "...%s..." is no longer true: %s' % (where, snippet(text, m.start(), m.end()), why))
    r.note("demo worker %s: %s" % (DEMO_WORKER, "present, so the outside-request promises are checked" if demo else "absent"))
    return r


# ----------------------------------------------------------------------------- main
RUNNERS = {"banned": check_banned, "figures": check_figures, "shipped": check_shipped, "links": check_links,
           "contact": check_contact, "collab": check_collab, "pl300": check_pl300, "stray": check_stray, "requests": check_requests, "promises": check_promises}


def run(root, checks=CHECKS, doms=None):
    site = Site(root, doms)
    return [RUNNERS[c](site) for c in checks]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Honesty and shipping checks for the portfolio site.")
    ap.add_argument("checks", nargs="*", default=list(CHECKS), help="subset of: " + ", ".join(CHECKS))
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--dom", action="append", default=[], metavar="PAGE=PATH",
                    help="rendered DOM of a page (e.g. from Chrome --dump-dom)")
    ap.add_argument("--json", help="also write the results here")
    ap.add_argument("--max", type=int, default=25, help="failures printed per check")
    a = ap.parse_args(argv)
    bad = [c for c in a.checks if c not in RUNNERS]
    if bad:
        ap.error("unknown check(s): %s" % ", ".join(bad))
    doms = dict(d.split("=", 1) for d in a.dom)
    results = run(a.root, a.checks, doms)
    for res in results:
        print("CHECK %-8s %s%s" % (res.name, "PASS" if res.ok else "FAIL",
                                   "" if res.ok else " (%d problem%s)" % (len(res.failures), "" if len(res.failures) == 1 else "s")))
        for nte in res.notes:
            print("    note: " + nte)
        for f in res.failures[: a.max]:
            print("    - " + f)
        if len(res.failures) > a.max:
            print("    ... and %d more" % (len(res.failures) - a.max))
    if a.json:
        with open(a.json, "w") as f:
            json.dump([{"check": x.name, "ok": x.ok, "failures": x.failures, "notes": x.notes} for x in results], f, indent=1)
    return 0 if all(x.ok for x in results) else 1


if __name__ == "__main__":
    sys.exit(main())
