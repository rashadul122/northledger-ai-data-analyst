#!/usr/bin/env python3
"""After a deploy: does the REAL host answer the way dist/_headers and dist/_redirects say? Read-only HEAD and GET.

    python3 tools/check_live_headers.py https://northledger-site.<account>.workers.dev      (the preview, before the domain)
    python3 tools/check_live_headers.py https://northledger.halosyncs.com                   (after the domain is attached)
    python3 tools/check_live_headers.py http://127.0.0.1:PORT --local                       (against tools/serve_dist.mjs)

For every file in dist/ it asks the host for the final URL path and compares the headers _headers promises (resolved
the way Cloudflare merges blocks, with tools/check_dist.py's resolver), then checks the redirects, the 404, and that the
bytes of the pages and the engine worker arrive UNCHANGED (a zone feature such as Email Obfuscation or Rocket Loader
rewrites HTML; the sha256 of what arrives must equal dist/'s). It also prints INFO lines for what the plan could not
confirm: the Content-Type override, on-the-fly compression of wasm/html/csv, the noindex rule on the free hostname.
Exit 1 on any FAIL. Standard library only. It sends no body, no cookie and no credential, and never a POST.
"""
import argparse
import hashlib
import http.client
import json
import os
import ssl
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_dist as cd  # noqa: E402

UA = "northledger-header-check/1"


def fetch(base, path, method="HEAD", enc="identity"):
    u = urllib.parse.urlparse(base)
    port = u.port or (443 if u.scheme == "https" else 80)
    conn = (http.client.HTTPSConnection(u.hostname, port, timeout=60, context=ssl.create_default_context())
            if u.scheme == "https" else http.client.HTTPConnection(u.hostname, port, timeout=60))
    try:
        conn.request(method, path, headers={"User-Agent": UA, "Accept-Encoding": enc, "Accept": "*/*"})
        r = conn.getresponse()
        body = r.read() if method == "GET" else b""
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("base")
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--local", action="store_true", help="the stand-in server: skip the Cloudflare-only expectations")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    dist = os.path.abspath(a.dist)
    files = [f for f in cd.walk(dist) if f not in cd.SPECIAL]
    blocks, _p = cd.parse_headers(cd.read(dist, "_headers").decode("utf-8"))
    cfg = cd.jload("deploy/cloudflare.config.json")
    csp_name = "Content-Security-Policy-Report-Only" if cfg["headers"].get("csp_report_only") else "Content-Security-Policy"
    fails, n = [], 0

    def check(ok, what):
        nonlocal n
        n += 1
        if not ok:
            fails.append(what)
            print("FAIL " + what)

    # 1. every file: status and the headers _headers promises
    for rel in files:
        path = cd.served_path(rel)
        status, h, _b = fetch(base, urllib.parse.quote(path))
        want, _hit = cd.resolve(blocks, path)
        check(status == 200, "%s: status %s (want 200)" % (path, status))
        for k, (name, vals) in want.items():
            w = ", ".join(vals)
            got = h.get(k)
            if k == "content-type":
                check(got is not None and got.lower().replace(" ", "") == w.lower().replace(" ", ""), "%s: Content-Type is %r, _headers says %r" % (path, got, w))
            else:
                check(got == w, "%s: %s is %r, _headers says %r" % (path, name, (got or "")[:70], w[:70]))
        if rel.endswith(".html") and rel != "404.html":
            check(h.get(csp_name.lower()) is not None, "%s: no %s arrived" % (path, csp_name))
    # 2. redirects
    for src, dst, codes in [("/index.html", "/", (307, 308)), ("/case-study.html", "/case-study", (307, 308)), ("/agent-demo.html", "/agent-demo", (307, 308)),
                            ("/try", "/#try", (302,))]:
        status, h, _b = fetch(base, src)
        check(status in codes and urllib.parse.urlparse(h.get("location", "")).path + ("#" + urllib.parse.urlparse(h.get("location", "")).fragment if "#" in h.get("location", "") else "") == dst,
              "%s: answered %s -> %r, expected %s -> %s" % (src, status, h.get("location"), "/".join(map(str, codes)), dst))
    for al in cfg.get("aliases", []):
        pth = urllib.parse.urlparse(al).path.rstrip("/")
        if pth:
            for src, dst in [(pth, "/"), (pth + "/", "/"), (pth + "/case-study.html", "/case-study.html")]:
                status, h, _b = fetch(base, src)
                check(status == 301 and h.get("location", "").endswith(dst), "%s: answered %s -> %r, expected 301 -> %s" % (src, status, h.get("location"), dst))
    # 3. a real 404 with the site's own page
    status, h, body = fetch(base, "/zz-no-such-path-8213", "GET")
    check(status == 404, "unknown path answered %s, expected a real 404" % status)
    check(hashlib.sha256(body).hexdigest() == hashlib.sha256(cd.read(dist, "404.html")).hexdigest(), "the 404 body is not dist/404.html")
    # 4. nothing in front of the files rewrote them: the bytes that arrive are dist/'s
    for rel in ["index.html", "case-study.html", "agent-demo.html", "engine/worker.js", "engine/pack.json"]:
        status, h, body = fetch(base, cd.served_path(rel), "GET", "identity")
        want = hashlib.sha256(cd.read(dist, rel)).hexdigest()
        check(status == 200 and hashlib.sha256(body).hexdigest() == want,
              "%s: the bytes that arrive are not dist/%s (a proxy feature rewrote them? content-encoding %s)" % (cd.served_path(rel), rel, h.get("content-encoding")))
        check("set-cookie" not in h, "%s: sets a cookie" % cd.served_path(rel))
    # 5. INFO: what the plan could not confirm
    print("\nINFO (settle the plan's open items; not pass/fail):")
    pins = cd.jload(cfg["runtime"]["pins"])
    probes = [("/", "html"), ("/engine/sample-messy.csv", "csv"), ("/engine/pyodide/%s/pyodide.asm.wasm" % pins["tag"], "wasm"),
              ("/engine/pyodide/%s/pandas-2.2.3-cp312-cp312-pyodide_2024_0_wasm32.whl" % pins["tag"], "wheel (a zip)")]
    for pth, what in probes:
        status, h, _b = fetch(base, pth, "HEAD", "br, gzip")
        print("  %-8s %-42s status %s  content-type %-28s content-encoding %-6s content-length %-10s cf-cache-status %s" % (
            what, pth[-42:], status, (h.get("content-type") or "-")[:28], h.get("content-encoding") or "none", h.get("content-length") or "-", h.get("cf-cache-status") or "-"))
    status, h, _b = fetch(base, "/")
    print("  noindex rule (should be present on a *.workers.dev host only): x-robots-tag = %r" % h.get("x-robots-tag"))
    print("  server = %r   cf-ray present = %s" % (h.get("server"), "cf-ray" in h))
    print("\ncheck_live_headers: %s (%d checks, %d failed) against %s" % ("ALL PASS" if not fails else "FAILED", n, len(fails), base))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
