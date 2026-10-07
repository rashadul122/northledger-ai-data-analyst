#!/usr/bin/env python3
"""Assemble dist/, the folder Cloudflare Workers Static Assets serves (wrangler.jsonc, assets.directory).

    python3 tools/make_dist.py                 build dist/ (fetches and verifies Pyodide first)
    python3 tools/make_dist.py --out DIR       build somewhere else
    python3 tools/make_dist.py --stub          also (re)write deploy/gh-pages-stub/ from the config
    python3 tools/make_dist.py --stub-only     only the stub

It reads the BUILT site (index.html, case-study.html, agent-demo.html, engine/...), never the sources,
and it needs no sibling repository: it does not run build.py or pack_engine.py and it modifies nothing
outside dist/ (and deploy/gh-pages-stub/ with --stub).

What goes in: the pages, every local file they link (the link graph, so a link on a page can never 404
on the new host), the demo's runtime files (engine/worker.js, the packed engine, pack.json, the sample
CSV, the map outline), the self-hosted Pyodide (deploy/pyodide-pins.json, verified by
tools/fetch_pyodide.py), a 404.html, and the generated _headers and _redirects.
What stays out: build.py, tools/, src/, tests, verify.sh, site.config.json, agent-sessions/, every
dev file (tools/check_dist.py fails the build if one is in dist/). Any file over 25 MiB fails.

Deployment-only rewrites (exact text, each asserted: a changed source sentence fails the build instead
of shipping silently): with runtime.mode "self" the demo's Pyodide address becomes this site's own
copy, and the three sentences that said "cdn.jsdelivr.net" say "this site". They touch dist/ only.
Standard library only; Python 3.9 or later.
"""
import argparse
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fetch_pyodide  # noqa: E402

PAGES = ["index.html", "case-study.html", "agent-demo.html"]
# files the page's JavaScript fetches or starts by relative name (not links, so the graph cannot see them)
RUNTIME_FILES = ["engine/worker.js", "engine/northledger-browser.zip", "engine/pack.json",
                 "engine/sample-messy.csv", "engine/world-110m.json"]
ALWAYS = ["favicon.svg", "favicon-32.png"]
LIMIT = 25 * 1024 * 1024
LINK_ATTRS = {"a": "href", "link": "href", "script": "src", "img": "src", "source": "src", "iframe": "src",
              "embed": "src", "object": "data", "video": "src", "audio": "src"}


def jread(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


class _Refs(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        k = LINK_ATTRS.get(tag)
        if k and a.get(k):
            self.refs.append(a[k])


def local_refs(page_text):
    p = _Refs()
    p.feed(page_text)
    out = []
    for r in p.refs:
        low = r.strip().lower()
        if not low or low.startswith(("#", "data:", "blob:", "mailto:", "tel:", "javascript:", "http://", "https://", "//")):
            continue
        out.append(r.strip().split("#")[0].split("?")[0])
    return [r for r in out if r]


def asset_list():
    """Every file dist/ must hold, found from the built pages' links plus the runtime files."""
    want, queue = [], list(PAGES)
    seen = set()
    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        path = os.path.join(ROOT, *rel.split("/"))
        if not os.path.isfile(path):
            raise SystemExit("a page links %s, which is not in the repository" % rel)
        want.append(rel)
        if rel.endswith(".html"):
            base = os.path.dirname(rel)
            with open(path, encoding="utf-8") as f:
                for r in local_refs(f.read()):
                    queue.append(os.path.normpath(os.path.join(base, r)).replace(os.sep, "/"))
    for rel in RUNTIME_FILES + ALWAYS:
        if rel not in seen:
            if not os.path.isfile(os.path.join(ROOT, *rel.split("/"))):
                raise SystemExit("%s is not in the repository" % rel)
            want.append(rel)
    return sorted(set(want))


def transforms(cfg, pins):
    """(file, old, new, expected count) for the deployment-only rewrites. Exact text on purpose."""
    if cfg["runtime"]["mode"] != "self":
        return []
    d = cfg["runtime"]["dir"].strip("/")                      # engine/pyodide
    rel_from_engine = d[len("engine/"):] if d.startswith("engine/") else d
    ver = pins["version"]
    return [
        ("index.html",
         "The first run fetches the engine's Python runtime (Pyodide) from cdn.jsdelivr.net, the only other address this demo contacts, and your file never goes with it.",
         "The first run fetches the engine's Python runtime (Pyodide) from this site, and your file never goes with it.", 1),
        ("index.html",
         "The one exception starts only when you start the Try-it demo: it fetches the engine's Python runtime (Pyodide) from cdn.jsdelivr.net, and the engine itself from this site, and, only if you choose",
         "Starting the Try-it demo fetches the engine's Python runtime (Pyodide) and the engine itself from this site; only if you choose", 1),
        ("index.html",
         "It comes from cdn.jsdelivr.net on the first run. Check the connection (or an ad or script blocker) and try again.",
         "It comes from this site on the first run. Check the connection (or an ad or script blocker) and try again.", 1),
        ("engine/worker.js",
         "var PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v%s/full/';" % ver,
         "var PYODIDE_URL = new URL('%s/%s/', self.location.href).href;" % (rel_from_engine, pins["tag"]), 1),
        ("engine/worker.js",
         "from cdn.jsdelivr.net, the one outside origin the site\n   allows at runtime (tools/check_site.py, \"requests\" check, allows it in this file only), then",
         "from this site's own pinned copy (the folder named in\n   PYODIDE_URL below, written by tools/make_dist.py), then", 1),
    ]


def apply_transforms(rel, text, table):
    for f, old, new, n in table:
        if f != rel:
            continue
        got = text.count(old)
        if got != n:
            raise SystemExit("make_dist: expected %d occurrence(s) of this text in %s, found %d (the page's wording changed; "
                             "update tools/make_dist.py transforms()):\n  %s" % (n, rel, got, old[:140]))
        text = text.replace(old, new)
    return text


def origin_of(url):
    m = re.match(r"^(https?://[^/]+)", url or "")
    if not m:
        raise SystemExit("site.config.json ai_proxy_url is not an https address: %r" % url)
    return m.group(1)


def headers_text(cfg, pins, worker_origin):
    h = cfg["headers"]
    with open(os.path.join(ROOT, "deploy", "_headers.tmpl"), encoding="utf-8") as f:
        t = f.read()
    hsts = "max-age=%d" % h["hsts_max_age"] + ("; includeSubDomains" if h.get("hsts_include_subdomains") else "")
    mode = cfg["runtime"]["mode"]
    script_extra = " https://cdn.jsdelivr.net" if mode != "self" else ""
    script_extra += " 'unsafe-eval'" if h.get("unsafe_eval_for_engine_worker") else ""
    connect_extra = " https://cdn.jsdelivr.net" if mode != "self" else ""
    host = worker_origin.split("//", 1)[1]
    parts = host.split(".")
    if len(parts) == 4 and parts[2:] == ["workers", "dev"]:
        noindex = "https://%s.%s.workers.dev/*" % (cfg["worker"]["name"], parts[1])
    else:
        noindex = None
    runtime_block = ("%s/*\n  Cache-Control: public, max-age=31536000, immutable\n" % ("/" + cfg["runtime"]["dir"].strip("/") + "/" + pins["tag"])
                     if mode == "self" else "# runtime.mode is jsdelivr: nothing self-hosted, nothing to cache here.\n")
    out = (t.replace("{{HSTS}}", hsts)
            .replace("{{CSP_HEADER}}", "Content-Security-Policy-Report-Only" if h.get("csp_report_only") else "Content-Security-Policy")
            .replace("{{PAGE_CONNECT}}", "'self' " + worker_origin)
            .replace("{{WORKER_SCRIPT_EXTRA}}", script_extra)
            .replace("{{WORKER_CONNECT_EXTRA}}", connect_extra)
            .replace("{{RUNTIME_BLOCK}}", runtime_block.rstrip("\n")))
    if noindex:
        out = out.replace("{{NOINDEX_HOST}}", noindex)
    else:
        out = out[:out.index("# ---- keep the free workers.dev name")].rstrip("\n") + "\n"
    if "{{" in out:
        raise SystemExit("deploy/_headers.tmpl has an unfilled placeholder")
    return out


def redirects_text(cfg):
    lines = ["# NorthLedger _redirects. GENERATED by tools/make_dist.py from deploy/cloudflare.config.json (aliases).",
             "# The old github.io project path keeps working on the new host (the github.io page itself redirects with",
             "# deploy/gh-pages-stub/). Order matters: the first match wins; static before dynamic."]
    static, dynamic = [], []
    for a in cfg.get("aliases", []):
        m = re.match(r"^https?://[^/]+(/[^?#]*)?$", a)
        path = (m.group(1) or "").rstrip("/") if m else ""
        if path:
            static.append("%-40s %-9s 301" % (path, "/"))
            dynamic.append("%-40s %-9s 301" % (path + "/*", "/:splat"))
    static.append("%-40s %-9s 302" % ("/try", "/#try"))
    return "\n".join(lines + static + dynamic) + "\n"


def stub_pages(cfg):
    new = cfg["site_url"].rstrip("/")
    host = new.split("//", 1)[1]
    alias = (cfg.get("aliases") or [""])[0]
    m = re.match(r"^https?://[^/]+(/[^?#]*)?$", alias)
    prefix = (m.group(1) or "").rstrip("/") if m else ""
    pat = "^" + prefix.replace("/", "\\/")
    body = ('<!doctype html><meta charset="utf-8"><title>NorthLedger Insights has moved</title>\n'
            '<link rel="canonical" href="%(new)s/">\n'
            '<script>var p=location.pathname.replace(/%(pat)s/,\'\');\n'
            'location.replace(\'%(new)s\'+(p||\'/\')+location.search+location.hash);</script>\n'
            '<noscript><meta http-equiv="refresh" content="0;url=%(new)s/"></noscript>\n'
            '<p>NorthLedger Insights has moved to <a href="%(new)s/">%(host)s</a>.</p>\n') % {"new": new, "pat": pat, "host": host}
    return {"index.html": body, "404.html": body}


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)


def clean_target(out):
    if os.path.exists(out):
        names = os.listdir(out)
        if names:
            hp = os.path.join(out, "_headers")
            ours = os.path.isfile(hp) and open(hp, encoding="utf-8").read(80).startswith("# NorthLedger _headers")
            if not ours:
                raise SystemExit("make_dist: %s exists, is not empty and is not a previous dist/ (no NorthLedger _headers); "
                                 "refusing to delete it" % out)
            shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)


def main():
    ap = argparse.ArgumentParser(description="Assemble dist/ for Cloudflare Workers Static Assets.")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--stub", action="store_true", help="also write deploy/gh-pages-stub/")
    ap.add_argument("--stub-only", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="use the Pyodide cache as it is (verify only, no network)")
    a = ap.parse_args()
    cfg = jread("deploy/cloudflare.config.json")
    if a.stub or a.stub_only:
        for name, body in stub_pages(cfg).items():
            write(os.path.join(ROOT, "deploy", "gh-pages-stub", name), body)
        print("wrote deploy/gh-pages-stub/ (redirects the old github.io address to %s)" % cfg["site_url"])
        if a.stub_only:
            return 0
    pins = jread(cfg["runtime"]["pins"])
    site_cfg = jread("site.config.json")
    worker_origin = origin_of(site_cfg.get("ai_proxy_url", ""))
    out = os.path.abspath(a.out)
    files = asset_list()
    table = transforms(cfg, pins)
    # the engine's packages must be inside the pinned runtime
    pack = jread("engine/pack.json")
    need = [n.lower().replace("_", "-") for n in pack["runtime"]["pyodide_packages"]]
    if cfg["runtime"]["mode"] == "self":
        have = set(pins["served_lock"]["packages"])
        missing = [n for n in need if n not in have]
        if missing:
            raise SystemExit("engine/pack.json needs %s, which deploy/pyodide-pins.json does not pin; re-pin with tools/fetch_pyodide.py --pin" % missing)
        if not a.no_fetch:
            rc = subprocess.call([sys.executable, os.path.join(ROOT, "tools", "fetch_pyodide.py")])
            if rc:
                raise SystemExit("fetch_pyodide failed; nothing was built")
        d = fetch_pyodide.cache_dir(pins)
        problems = fetch_pyodide.verify(pins, d, quiet=True)
        if problems:
            raise SystemExit("the Pyodide cache is not the pinned set (run tools/fetch_pyodide.py):\n  " + "\n  ".join(problems))
    clean_target(out)
    for rel in files:
        src = os.path.join(ROOT, *rel.split("/"))
        dst = os.path.join(out, *rel.split("/"))
        if any(f == rel for f, _o, _n, _c in table):
            with open(src, encoding="utf-8") as f:
                text = f.read()
            write(dst, apply_transforms(rel, text, table))
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    if cfg["runtime"]["mode"] == "self":
        d = fetch_pyodide.cache_dir(pins)
        dest = os.path.join(out, *cfg["runtime"]["dir"].strip("/").split("/"), pins["tag"])
        os.makedirs(dest, exist_ok=True)
        for name, _h, _s in fetch_pyodide.served_entries(pins):
            shutil.copyfile(os.path.join(d, name), os.path.join(dest, name))
    shutil.copyfile(os.path.join(ROOT, "deploy", "404.html"), os.path.join(out, "404.html"))
    write(os.path.join(out, "_headers"), headers_text(cfg, pins, worker_origin))
    write(os.path.join(out, "_redirects"), redirects_text(cfg))
    # size report and the hard limits
    rows, total, bad = [], 0, []
    for dp, _dn, fns in os.walk(out):
        for fn in fns:
            p = os.path.join(dp, fn)
            s = os.path.getsize(p)
            total += s
            rows.append((os.path.relpath(p, out), s))
            if s > LIMIT:
                bad.append("%s is %s bytes, over Cloudflare's 25 MiB per-file limit" % (os.path.relpath(p, out), format(s, ",")))
    for rel, s in sorted(rows):
        print("  %-62s %12s" % (rel, format(s, ",")))
    print("dist: %d files, %s bytes -> %s" % (len(rows), format(total, ","), out))
    if bad:
        raise SystemExit("\n".join(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
