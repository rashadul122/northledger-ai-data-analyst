#!/usr/bin/env python3
"""Lint dist/ (the folder Cloudflare Workers Static Assets will serve). No browser, no network.

    python3 tools/check_dist.py [DIST]            (default: ./dist)  exit 1 on any FAIL
    python3 tools/check_dist.py --listing         also print every file with its size
    python3 tools/check_dist.py --js-crosscheck   also run tools/serve_dist.mjs --resolve (node) and require
                                                  its header resolution to equal this script's, path by path

Checks (each prints PASS or FAIL lines):
  listing   no file over 25 MiB, under 100,000 files, no symlinks, the required files present
  devfiles  nothing from the source tree: build.py, tools/, src/, tests, site.config.json, verify.sh,
            wrangler.jsonc, deploy*, dotfiles, agent-sessions/; only the two .py files the pages link
  links     every local link of every page resolves to a file in dist (and every #fragment to an id)
  runtime   dist/engine/pyodide/<tag>/ is exactly the pinned set (names, sizes, sha256) and the tag
            matches; the engine zip and the sample are the bytes engine/pack.json names; the packages
            the engine asks for are pinned; worker.js points at the folder; no jsdelivr outside the
            pinned runtime (where pyodide.js carries one setCdnUrl default, never fetched for these packages)
  origins   the only absolute address any page or the worker script can fetch is the AI proxy
            (site.config.json ai_proxy_url); every fetch( call is recognised
  redirects _redirects parses; counts and lengths are inside Cloudflare's limits
  headers   _headers parses (100 blocks, 2,000 characters a line); every CSP parses, uses only known
            directives, no wildcard, no http:, no unsafe-eval unless the config allows it for the engine
            worker, default-src 'none'; the page may connect to 'self' and the proxy only; the engine
            worker to 'self' only; the resolved headers of every served path carry the eight
            protections exactly once (blocks that set the same header are JOINED by Cloudflare, so a
            CSP or Cache-Control in /* would double up)
Standard library only; Python 3.9 or later.
"""
import argparse
import hashlib
import html.parser
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fetch_pyodide  # noqa: E402

LIMIT = 25 * 1024 * 1024
MAX_FILES = 100000
REQUIRED = ["index.html", "case-study.html", "agent-demo.html", "404.html", "_headers", "_redirects", "favicon.svg",
            "favicon-32.png", "engine/worker.js", "engine/northledger-browser.zip", "engine/pack.json",
            "engine/sample-messy.csv", "engine/world-110m.json"]
BAD_PARTS = {"tools", "src", "tests", "test", "deploy", "qa", "agent-sessions", ".git", ".work", ".cache",
             "node_modules", "__pycache__", "dist"}
BAD_NAMES = {"build.py", "site.config.json", "verify.sh", "deploy.sh", "wrangler.jsonc", "wrangler.toml", "package.json",
             "package-lock.json", ".gitignore", ".assetsignore", "test-driver.html", "demo-data.json", "README.md",
             "RUNBOOK.md", "AUDIT.md", "COST.md", "REQUIREMENTS.md", "DECISION-LOG.md", "INCIDENT-POSTMORTEM.md",
             "KPI-DICTIONARY.md", "DATA-MANAGEMENT.md", "audit-offer.md", "case-study.md"}
BAD_EXT = (".pyc", ".map", ".mjs", ".sh", ".toml", ".jsonc", ".env", ".log", ".bak", ".orig", ".swp")
ALLOWED_PY = {"build_forecast.py", "build_rentsafe.py"}          # linked from index.html as "script"
SPECIAL = {"_headers", "_redirects"}
KNOWN_DIRECTIVES = {"default-src", "script-src", "script-src-elem", "script-src-attr", "style-src", "style-src-elem",
                    "style-src-attr", "img-src", "font-src", "connect-src", "worker-src", "manifest-src", "media-src",
                    "object-src", "frame-src", "child-src", "frame-ancestors", "base-uri", "form-action", "sandbox",
                    "upgrade-insecure-requests", "report-uri", "report-to"}
KEYWORDS = {"'none'", "'self'", "'unsafe-inline'", "'unsafe-eval'", "'wasm-unsafe-eval'", "'strict-dynamic'", "'unsafe-hashes'"}
PROTECT = ["X-Content-Type-Options", "Referrer-Policy", "Strict-Transport-Security", "X-Frame-Options", "Permissions-Policy"]


class R:
    def __init__(self, name):
        self.name, self.fails, self.notes = name, [], []

    def fail(self, m):
        self.fails.append(m)

    def note(self, m):
        self.notes.append(m)

    def show(self):
        print("CHECK %-9s %s" % (self.name, "FAIL" if self.fails else "PASS"))
        for m in self.fails[:40]:
            print("    FAIL: " + m)
        if len(self.fails) > 40:
            print("    ... %d more" % (len(self.fails) - 40))
        for m in self.notes:
            print("    note: " + m)
        return not self.fails


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def jload(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return json.load(f)


def read(dist, rel, mode="rb"):
    with open(os.path.join(dist, *rel.split("/")), mode) as f:
        return f.read()


def walk(dist):
    out = []
    for dp, dn, fns in os.walk(dist):
        for fn in fns:
            p = os.path.join(dp, fn)
            out.append(os.path.relpath(p, dist).replace(os.sep, "/"))
    return sorted(out)


# ------------------------------------------------------------------------------------------ headers model
def parse_headers(text):
    """[(pattern, [(name, value) or ('!', name)], line_no)] and a list of problems."""
    blocks, problems, cur = [], [], None
    for i, raw in enumerate(text.split("\n"), 1):
        if len(raw) > 2000:
            problems.append("line %d is %d characters (limit 2,000)" % (i, len(raw)))
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] not in " \t":
            cur = (raw.strip(), [], i)
            blocks.append(cur)
            continue
        if cur is None:
            problems.append("line %d: a header before any URL line" % i)
            continue
        s = raw.strip()
        if s.startswith("!"):
            cur[1].append(("!", s[1:].strip()))
            continue
        name, sep, value = s.partition(":")
        if not sep or not re.match(r"^[A-Za-z0-9-]+$", name.strip()):
            problems.append("line %d: not a header line: %r" % (i, s[:60]))
            continue
        if not value.strip():
            problems.append("line %d: %s has no value" % (i, name.strip()))
            continue
        cur[1].append((name.strip(), value.strip()))
    return blocks, problems


def pattern_regex(pat):
    parts = re.split(r"(\*|:[A-Za-z]\w*)", pat)
    rx = ""
    for p in parts:
        if p == "*":
            rx += ".*"
        elif p.startswith(":") and len(p) > 1:
            rx += "[^/]+"
        else:
            rx += re.escape(p)
    return re.compile("^" + rx + "$")


def served_path(rel):
    """The URL path a file is finally served at under html_handling auto-trailing-slash."""
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[:-len("index.html")]
    if rel.endswith(".html"):
        return "/" + rel[:-5]
    return "/" + rel


def resolve(blocks, path):
    """Cloudflare's merge: every matching block in file order; the same header from several blocks is joined
    with ', '; '! Name' detaches what earlier blocks set. Returns ({lower-name: (Name, [values])}, [matching patterns])."""
    out, hit = {}, []
    for pat, hs, _ln in blocks:
        if pat.startswith(("http://", "https://")):
            continue
        if pattern_regex(pat).match(path):
            hit.append(pat)
            for n, v in hs:
                if n == "!":
                    out.pop(v.lower(), None)
                else:
                    out.setdefault(n.lower(), (n, []))[1].append(v)
    return out, hit


def parse_csp(value):
    d, probs = {}, []
    for part in [p.strip() for p in value.split(";") if p.strip()]:
        toks = part.split()
        name, srcs = toks[0].lower(), toks[1:]
        if name in d:
            probs.append("directive %s appears twice" % name)
        if name not in KNOWN_DIRECTIVES:
            probs.append("unknown directive %r" % name)
        for s in srcs:
            low = s.lower()
            if low in KEYWORDS or low.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-")):
                continue
            if low in ("data:", "blob:", "https:", "wss:"):
                continue
            if low == "http:" or low == "*" or low.startswith("http://"):
                probs.append("%s: %r is insecure or too broad" % (name, s))
            elif re.match(r"^https://\*\.", low) or low == "https://*":
                probs.append("%s: wildcard host %r" % (name, s))
            elif re.match(r"^https://[a-z0-9.-]+(:\d+)?(/\S*)?$", low):
                continue
            else:
                probs.append("%s: cannot read the source %r" % (name, s))
        d[name] = srcs
    return d, probs


# ------------------------------------------------------------------------------------------ html helpers
class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.refs = set(), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.add(a["name"])
        k = {"a": "href", "link": "href", "script": "src", "img": "src", "source": "src", "iframe": "src",
             "embed": "src", "object": "data"}.get(tag)
        if k and a.get(k):
            self.refs.append((tag, a[k]))


def load_page(dist, rel):
    p = Page()
    p.feed(read(dist, rel).decode("utf-8", "replace"))
    return p


# ------------------------------------------------------------------------------------------ the checks
def run(dist, show_listing, js_cross):
    cfg = jload("deploy/cloudflare.config.json")
    pins = jload(cfg["runtime"]["pins"])
    site_cfg = jload("site.config.json")
    mode = cfg["runtime"]["mode"]
    worker_origin = re.match(r"^(https?://[^/]+)", site_cfg.get("ai_proxy_url", "")).group(1)
    files = walk(dist)
    res = []

    # ---- listing
    r = R("listing")
    total = 0
    for rel in files:
        p = os.path.join(dist, *rel.split("/"))
        if os.path.islink(p):
            r.fail("%s is a symbolic link" % rel)
        s = os.path.getsize(p)
        total += s
        if s > LIMIT:
            r.fail("%s is %s bytes: over Cloudflare's 25 MiB per-file limit" % (rel, format(s, ",")))
        if show_listing:
            print("  %-70s %12s" % (rel, format(s, ",")))
    if len(files) > MAX_FILES:
        r.fail("%d files: over 100,000" % len(files))
    for rel in REQUIRED:
        if rel not in files:
            r.fail("required file missing: %s" % rel)
    big = max(((os.path.getsize(os.path.join(dist, *f.split("/"))), f) for f in files), default=(0, ""))
    r.note("%d files, %s bytes; largest %s (%s bytes, %.1f MiB of 25)" % (len(files), format(total, ","), big[1], format(big[0], ","), big[0] / 1048576.0))
    res.append(r)

    # ---- dev files
    r = R("devfiles")
    for rel in files:
        parts = rel.split("/")
        name = parts[-1]
        if name in SPECIAL:
            continue
        if any(p in BAD_PARTS for p in parts[:-1]):
            r.fail("%s: a source or dev folder is inside dist" % rel)
        elif name in BAD_NAMES:
            r.fail("%s: a dev file" % rel)
        elif name.startswith("."):
            r.fail("%s: a dotfile" % rel)
        elif name.endswith(BAD_EXT):
            r.fail("%s: a dev file type" % rel)
        elif name.endswith(".py") and name not in ALLOWED_PY:
            r.fail("%s: a Python file the pages do not link" % rel)
        elif name.endswith(".md") and name != "DATA-SOURCES.md":
            r.fail("%s: a Markdown file the pages do not link" % rel)
    r.note("allowed Python files (linked from index.html as 'script'): %s" % ", ".join(sorted(ALLOWED_PY)))
    res.append(r)

    # ---- links
    r = R("links")
    htmls = [f for f in files if f.endswith(".html") and f != "404.html"]
    idmap = {f: load_page(dist, f).ids for f in htmls}
    n = 0
    for page in htmls:
        base = os.path.dirname(page)
        for tag, ref in load_page(dist, page).refs:
            low = ref.strip().lower()
            if not low or low.startswith(("data:", "blob:", "mailto:", "tel:", "javascript:", "http://", "https://", "//")):
                continue
            path, _, frag = ref.strip().partition("#")
            path = path.split("?")[0]
            n += 1
            if not path:
                if frag and frag not in idmap[page]:
                    r.fail("%s: #%s has no element with that id" % (page, frag))
                continue
            if path.startswith("/"):
                r.fail("%s: root-absolute link %s" % (page, ref))
                continue
            target = os.path.normpath(os.path.join(base, path)).replace(os.sep, "/")
            if target not in files:
                r.fail("%s: <%s> %s -> %s is not in dist" % (page, tag, ref, target))
            elif frag and target in idmap and frag not in idmap[target]:
                r.fail("%s: %s has no element with id %r" % (page, target, frag))
    r.note("%d local links checked across %d pages" % (n, len(htmls)))
    res.append(r)

    # ---- runtime
    r = R("runtime")
    wjs = read(dist, "engine/worker.js").decode("utf-8")
    pack = json.loads(read(dist, "engine/pack.json").decode("utf-8"))
    z = read(dist, "engine/northledger-browser.zip")
    if sha256(z) != pack["zip"]["sha256"] or len(z) != pack["zip"]["bytes"]:
        r.fail("engine/northledger-browser.zip is not the file engine/pack.json describes (%s, %d bytes): a stale pack" % (sha256(z)[:12], len(z)))
    smp = read(dist, "engine/sample-messy.csv")
    if sha256(smp) != pack["sample"]["sha256"] or len(smp) != pack["sample"]["bytes"]:
        r.fail("engine/sample-messy.csv is not the sample engine/pack.json names")
    if mode == "self":
        rt = "engine/pyodide/" + pins["tag"] + "/"
        want = {n_: (h, s) for n_, h, s in fetch_pyodide.served_entries(pins)}
        have = {f[len(rt):]: f for f in files if f.startswith(rt)}
        for n_, (h, s) in want.items():
            if n_ not in have:
                r.fail("pinned runtime file missing: %s" % n_)
                continue
            b = read(dist, have[n_])
            if len(b) != s or sha256(b) != h:
                r.fail("%s is not the pinned file (size or sha256)" % n_)
        for extra in sorted(set(have) - set(want)):
            r.fail("unpinned file in the runtime folder: %s" % extra)
        other = [f for f in files if f.startswith("engine/pyodide/") and not f.startswith(rt)]
        if other:
            r.fail("another runtime folder is in dist: %s" % other[0])
        if fetch_pyodide.tag_of(pins["version"], [(n_, h) for n_, (h, _s) in want.items()]) != pins["tag"]:
            r.fail("pins: the tag is not the hash of the files")
        line = "var PYODIDE_URL = new URL('pyodide/%s/', self.location.href).href;" % pins["tag"]
        if wjs.count(line) != 1:
            r.fail("engine/worker.js does not point PYODIDE_URL at the pinned folder (expected: %s)" % line)
        have_pk = set(pins["served_lock"]["packages"])
        for p_ in pack["runtime"]["pyodide_packages"]:
            if p_.lower().replace("_", "-") not in have_pk:
                r.fail("the engine asks for %s, which the pinned runtime lacks" % p_)
        lock = json.loads(read(dist, rt + "pyodide-lock.json").decode("utf-8"))
        if sorted(lock["packages"]) != sorted(have_pk) or lock["info"]["version"] != pins["version"]:
            r.fail("the served lock does not list exactly the pinned packages")
        for k, pk in lock["packages"].items():
            if pk["file_name"] not in have:
                r.fail("the lock names %s, which is not served" % pk["file_name"])
        for f in files:
            if f.startswith(rt):
                continue
            if f.endswith((".html", ".js", ".json", ".md", ".py", ".csv", ".txt", "_headers")) or f == "_headers":
                t = read(dist, f).decode("utf-8", "replace")
                if re.search(r"jsdelivr", t, re.I):
                    r.fail("%s still mentions jsdelivr" % f)
        pj = read(dist, rt + "pyodide.js").decode("utf-8", "replace")
        if len(re.findall(r"cdn\.jsdelivr\.net", pj)) != 1:
            r.fail("pyodide.js: expected exactly one built-in jsdelivr default (setCdnUrl), found %d" % len(re.findall(r"cdn\.jsdelivr\.net", pj)))
        r.note("runtime %s: %d pinned files exactly; pyodide.js's one jsdelivr string is Pyodide's own setCdnUrl default (never fetched for these packages; the worker's CSP would block it)" % (pins["tag"], len(want)))
    else:
        r.note("runtime.mode is %s: nothing self-hosted, nothing checked" % mode)
    res.append(r)

    # ---- origins
    r = R("origins")
    pages = [f for f in files if f.endswith(".html")]
    for f in pages + ["engine/worker.js"]:
        t = read(dist, f).decode("utf-8", "replace")
        for m in re.finditer(r"""(fetch|new Worker|importScripts|new EventSource|new WebSocket|sendBeacon)\s*\(\s*(['"`])(https?:)?//""", t):
            r.fail("%s: %s( with an absolute address: %r" % (f, m.group(1), t[m.start():m.start() + 80]))
        for m in re.finditer(r"""<(script|img|iframe|embed|source|video|audio)\b[^>]*\bsrc=["'](https?:)?//""", t, re.I):
            r.fail("%s: <%s> loads an absolute address" % (f, m.group(1)))
        for m in re.finditer(r"""<link\b[^>]*\bhref=["'](https?:)?//[^>]*>""", t, re.I):
            if not re.search(r"""rel=["'](canonical|alternate|author|license|help)""", m.group(0), re.I):
                r.fail("%s: <link> loads an absolute address: %s" % (f, m.group(0)[:80]))
        if re.search(r"@import|url\(\s*['\"]?https?:", t) and f.endswith(".html"):
            r.fail("%s: CSS @import or url() with an absolute address" % f)
    idx = read(dist, "index.html").decode("utf-8", "replace")
    allowed_starts = ("String(CFG.ai_proxy_url)", "url,", "'engine/")
    seen = []
    for m in re.finditer(r"fetch\(([^;]{0,60})", idx):
        a = m.group(1)
        ok = a.startswith(allowed_starts)
        seen.append(a[:40])
        if not ok:
            r.fail("index.html: an unrecognised fetch( target: %r" % a)
    if not any(s.startswith("String(CFG.ai_proxy_url)") for s in seen):
        r.fail("index.html: no fetch to the AI proxy found (is the page's code what the CSP was written for?)")
    if worker_origin not in idx:
        r.fail("index.html does not carry the AI proxy address %s (site.config.json ai_proxy_url)" % worker_origin)
    for f in ("case-study.html", "agent-demo.html"):
        t = read(dist, f).decode("utf-8", "replace")
        if re.search(r"fetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|new Worker|importScripts", t):
            r.fail("%s: uses a network API, but its CSP says connect-src 'none'" % f)
    r.note("fetch targets in index.html: %d (proxy, same-origin files only); AI proxy origin %s" % (len(seen), worker_origin))
    res.append(r)

    # ---- redirects
    r = R("redirects")
    rtext = read(dist, "_redirects").decode("utf-8")
    static_n = dyn_n = 0
    for i, raw in enumerate(rtext.split("\n"), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if len(raw) > 1000:
            r.fail("line %d: over 1,000 characters" % i)
        parts = s.split()
        if len(parts) not in (2, 3):
            r.fail("line %d: want 'source destination [code]': %r" % (i, s[:60]))
            continue
        code = parts[2] if len(parts) == 3 else "302"
        if code not in ("200", "301", "302", "303", "307", "308"):
            r.fail("line %d: status %s" % (i, code))
        if not (parts[0].startswith("/")):
            r.fail("line %d: the source must start with /" % i)
        if not (parts[1].startswith("/") or parts[1].startswith("https://")):
            r.fail("line %d: the destination must start with / or https://" % i)
        if "*" in parts[0] or ":" in parts[0]:
            dyn_n += 1
        else:
            if dyn_n:
                r.fail("line %d: a static redirect after a dynamic one" % i)
            static_n += 1
    if static_n > 2000 or dyn_n > 100:
        r.fail("redirect limits: %d static (2,000), %d dynamic (100)" % (static_n, dyn_n))
    r.note("%d static, %d dynamic redirect(s)" % (static_n, dyn_n))
    res.append(r)

    # ---- headers
    r = R("headers")
    htext = read(dist, "_headers").decode("utf-8")
    blocks, probs = parse_headers(htext)
    for p in probs:
        r.fail(p)
    if len(blocks) > 100:
        r.fail("%d blocks: over 100" % len(blocks))
    star = [b for b in blocks if b[0] == "/*"]
    if len(star) != 1:
        r.fail("expected exactly one /* block, found %d" % len(star))
    else:
        names = [n_.lower() for n_, _v in star[0][1] if n_ != "!"]
        for bad in ("content-security-policy", "content-security-policy-report-only", "cache-control", "content-type"):
            if bad in names:
                r.fail("/* sets %s: every more specific block would be JOINED with it, giving two values" % bad)
        for want_h in PROTECT:
            if want_h.lower() not in names:
                r.fail("/* lacks %s" % want_h)
    csp_name = "Content-Security-Policy-Report-Only" if cfg["headers"].get("csp_report_only") else "Content-Security-Policy"
    unsafe_eval_ok = bool(cfg["headers"].get("unsafe_eval_for_engine_worker"))
    page_rules = {"/", "/case-study", "/agent-demo"}
    resolved_all = {}
    for rel in files:
        if rel in SPECIAL:
            continue
        path = served_path(rel)
        hs, hit = resolve(blocks, path)
        resolved_all[path] = {v[0]: ", ".join(v[1]) for v in hs.values()}
        for want_h in PROTECT:
            v = hs.get(want_h.lower())
            if not v or len(v[1]) != 1:
                r.fail("%s: %s resolves to %s" % (path, want_h, "nothing" if not v else "%d values" % len(v[1])))
        if rel.endswith(".html") and rel != "404.html":
            c = hs.get(csp_name.lower())
            if not c or len(c[1]) != 1:
                r.fail("%s: no single %s (blocks hit: %s)" % (path, csp_name, hit))
            cc = hs.get("cache-control")
            if not cc or len(cc[1]) != 1:
                r.fail("%s: no single Cache-Control" % path)
        if rel == "engine/worker.js":
            c = hs.get(csp_name.lower()) or hs.get("content-security-policy")
            if not c or len(c[1]) != 1:
                r.fail("%s: the engine worker has no single CSP of its own" % path)
        for k in ("content-security-policy", "content-security-policy-report-only", "cache-control", "content-type",
                  "strict-transport-security"):
            if k in hs and len(hs[k][1]) > 1:
                r.fail("%s: %s is set by %d blocks and would be joined: %s" % (path, k, len(hs[k][1]), hit))
        if path.startswith("/engine/pyodide/"):
            cc = hs.get("cache-control")
            if cc is None or "immutable" not in cc[1][0]:
                r.fail("%s: runtime files must be immutable-cached" % path)
    for pth in page_rules:
        if pth not in resolved_all:
            r.fail("no served page at %s" % pth)

    def check_csp(value, who, kind):
        d, ps = parse_csp(value)
        for p in ps:
            r.fail("%s: %s" % (who, p))
        if d.get("default-src") != ["'none'"] and kind != "favicon":
            r.fail("%s: default-src is not 'none'" % who)
        if "'unsafe-eval'" in " ".join(" ".join(v) for v in d.values()) and not (kind == "worker" and unsafe_eval_ok):
            r.fail("%s: 'unsafe-eval' is not allowed here" % who)
        if kind == "worker":
            want_script = ["'self'", "'wasm-unsafe-eval'"] + (["'unsafe-eval'"] if unsafe_eval_ok else []) + (["https://cdn.jsdelivr.net"] if mode != "self" else [])
            want_conn = ["'self'"] + (["https://cdn.jsdelivr.net"] if mode != "self" else [])
            if sorted(d.get("script-src", [])) != sorted(want_script):
                r.fail("%s: script-src is %s, expected %s" % (who, d.get("script-src"), want_script))
            if sorted(d.get("connect-src", [])) != sorted(want_conn):
                r.fail("%s: connect-src is %s, expected %s (the engine must never reach the AI proxy)" % (who, d.get("connect-src"), want_conn))
        elif kind == "page-net":
            if sorted(d.get("connect-src", [])) != sorted(["'self'", worker_origin]):
                r.fail("%s: connect-src is %s, expected 'self' and %s only" % (who, d.get("connect-src"), worker_origin))
            if sorted(d.get("script-src", [])) != ["'self'", "'unsafe-inline'"]:
                r.fail("%s: script-src is %s" % (who, d.get("script-src")))
            if d.get("worker-src") != ["'self'"]:
                r.fail("%s: worker-src must be 'self'" % who)
            for need in ("frame-ancestors", "base-uri", "form-action", "object-src", "frame-src"):
                if need not in d:
                    r.fail("%s: lacks %s" % (who, need))
        elif kind == "page-static":
            if d.get("connect-src") != ["'none'"]:
                r.fail("%s: connect-src must be 'none'" % who)
            if "worker-src" in d and d["worker-src"] != ["'none'"]:
                r.fail("%s: worker-src must be 'none' or absent" % who)
        for k, v in d.items():
            if any(s.startswith("https://") for s in v) and not (k == "connect-src" and kind == "page-net") and not (mode != "self" and kind == "worker"):
                r.fail("%s: %s names an outside address %s" % (who, k, [s for s in v if s.startswith("https://")]))
        return d

    kinds = {"/": "page-net", "/case-study": "page-static", "/agent-demo": "page-static", "/engine/worker.js": "worker", "/favicon.svg": "favicon"}
    for pth, kind in kinds.items():
        if pth in resolved_all:
            v = resolved_all[pth].get(csp_name) or resolved_all[pth].get("Content-Security-Policy")
            if v is None:
                r.fail("%s: no CSP" % pth)
            else:
                check_csp(v, pth, kind)
    r.note("%d blocks; CSP header %s; engine worker connect-src 'self' only; page connect-src 'self' + %s" % (len(blocks), csp_name, worker_origin))
    res.append(r)

    if js_cross:
        r = R("jsmatch")
        node = subprocess.run(["node", os.path.join(ROOT, "tools", "serve_dist.mjs"), "--resolve", dist], capture_output=True, text=True)
        if node.returncode != 0:
            r.fail("serve_dist.mjs --resolve failed: %s" % node.stderr[:200])
        else:
            theirs = json.loads(node.stdout)
            for pth, hs in resolved_all.items():
                t = {k.lower(): v for k, v in theirs.get(pth, {}).items()}
                mine = {k.lower(): v for k, v in hs.items()}
                if t != mine:
                    r.fail("%s: node and python resolve the headers differently" % pth)
            r.note("%d paths resolved identically by python and node" % len(resolved_all))
        res.append(r)
    ok = True
    for r in res:
        ok = r.show() and ok
    print("\ncheck_dist: %s (%d file(s) in %s)" % ("ALL PASS" if ok else "FAILED", len(files), dist))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Lint dist/ (no browser, no network).")
    ap.add_argument("dist", nargs="?", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--listing", action="store_true")
    ap.add_argument("--js-crosscheck", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(a.dist):
        print("no dist folder at %s (run tools/make_dist.py)" % a.dist)
        return 1
    return run(os.path.abspath(a.dist), a.listing, a.js_crosscheck)


if __name__ == "__main__":
    sys.exit(main())
