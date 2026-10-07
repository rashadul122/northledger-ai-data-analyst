#!/usr/bin/env python3
"""Mutation tests for the deployment tools: tools/check_dist.py must FAIL on each deliberately broken dist/,
and tools/make_dist.py's exact-text rewrites must refuse a page whose sentence changed.

    python3 tools/test_dist_tools.py            (needs dist/ built: python3 tools/make_dist.py)
    NL_TEST_TMP=/some/scratch python3 tools/test_dist_tools.py

No browser, no network. It copies dist/ with hard links (fast) and rewrites a file only by unlinking it first,
so the real dist/ is never touched.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_dist  # noqa: E402
import make_dist  # noqa: E402

DIST = os.path.join(ROOT, "dist")
TMP = os.environ.get("NL_TEST_TMP") or tempfile.gettempdir()


def lint(d):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = check_dist.run(d, False, False)
    return rc, buf.getvalue()


class Mut(unittest.TestCase):
    def setUp(self):
        if not os.path.isdir(DIST):
            self.skipTest("dist/ is not built (python3 tools/make_dist.py)")
        self.d = tempfile.mkdtemp(prefix="nl-dist-mut-", dir=TMP)
        shutil.rmtree(self.d)
        shutil.copytree(DIST, self.d, copy_function=os.link)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def p(self, rel):
        return os.path.join(self.d, *rel.split("/"))

    def edit(self, rel, fn, binary=False):
        path = self.p(rel)
        with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
            data = f.read()
        os.unlink(path)                       # never write through a hard link
        with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8", "newline": "\n"})) as f:
            f.write(fn(data))

    def add(self, rel, data):
        os.makedirs(os.path.dirname(self.p(rel)), exist_ok=True)
        with open(self.p(rel), "wb") as f:
            f.write(data)

    def expect_fail(self, needle):
        rc, out = lint(self.d)
        self.assertEqual(rc, 1, "the linter passed a broken dist")
        self.assertIn(needle, out)

    def test_baseline_passes(self):
        rc, out = lint(self.d)
        self.assertEqual(rc, 0, out)

    def test_dev_file(self):
        self.add("build.py", b"x"); self.expect_fail("a dev file")

    def test_src_folder(self):
        self.add("src/a.js", b"x"); self.expect_fail("a source or dev folder")

    def test_site_config(self):
        self.add("site.config.json", b"{}"); self.expect_fail("site.config.json")

    def test_unlinked_python(self):
        self.add("engine/nl_browser.py", b"x"); self.expect_fail("a Python file the pages do not link")

    def test_over_25_mib(self):
        self.add("data/big.bin", b"\0" * (25 * 1024 * 1024 + 1)); self.expect_fail("over Cloudflare's 25 MiB")

    def test_missing_runtime_file(self):
        rt = os.path.join(self.d, "engine", "pyodide")
        tag = os.listdir(rt)[0]
        os.unlink(os.path.join(rt, tag, "six-1.16.0-py2.py3-none-any.whl")); self.expect_fail("pinned runtime file missing")

    def test_flipped_byte_in_wasm(self):
        rt = os.path.join(self.d, "engine", "pyodide")
        tag = os.listdir(rt)[0]
        self.edit("engine/pyodide/%s/pyodide.asm.wasm" % tag, lambda b: b[:100] + bytes([b[100] ^ 1]) + b[101:], binary=True)
        self.expect_fail("is not the pinned file")

    def test_extra_runtime_file(self):
        rt = os.path.join(self.d, "engine", "pyodide")
        tag = os.listdir(rt)[0]
        self.add("engine/pyodide/%s/extra.js" % tag, b"x"); self.expect_fail("unpinned file in the runtime folder")

    def test_stale_engine_zip(self):
        self.edit("engine/northledger-browser.zip", lambda b: b + b"\0", binary=True); self.expect_fail("a stale pack")

    def test_jsdelivr_left_in_page(self):
        self.edit("index.html", lambda t: t.replace("</body>", "<!-- cdn.jsdelivr.net --></body>", 1)); self.expect_fail("still mentions jsdelivr")

    def test_worker_points_elsewhere(self):
        self.edit("engine/worker.js", lambda t: t.replace("new URL('pyodide/", "new URL('pyodid/", 1)); self.expect_fail("does not point PYODIDE_URL")

    def test_outside_fetch_in_page(self):
        self.edit("index.html", lambda t: t.replace("</body>", "<script>fetch('https://evil.example/x')</script></body>", 1)); self.expect_fail("with an absolute address")

    def test_third_party_script_tag(self):
        self.edit("case-study.html", lambda t: t.replace("</head>", '<script src="https://cdn.example/x.js"></script></head>', 1)); self.expect_fail("loads an absolute address")

    def test_broken_link(self):
        self.edit("index.html", lambda t: t.replace("</body>", '<a href="missing-page.html">x</a></body>', 1)); self.expect_fail("is not in dist")

    def test_csp_outside_origin(self):
        self.edit("_headers", lambda t: t.replace("connect-src 'self' https://", "connect-src 'self' https://evil.example https://", 1)); self.expect_fail("connect-src")

    def test_unsafe_eval_on_page(self):
        self.edit("_headers", lambda t: t.replace("script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:", "script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:", 1))
        self.expect_fail("'unsafe-eval' is not allowed")

    def test_engine_worker_may_reach_proxy(self):
        self.edit("_headers", lambda t: t.replace("connect-src 'self'; base-uri 'none'", "connect-src 'self' https://northledger-insight-proxy.r-mdrashad97.workers.dev; base-uri 'none'", 1))
        self.expect_fail("the engine must never reach the AI proxy")

    def test_csp_in_star_block(self):
        self.edit("_headers", lambda t: t.replace("/*\n  X-Content", "/*\n  Content-Security-Policy: default-src 'none'\n  X-Content", 1)); self.expect_fail("every more specific block would be JOINED")

    def test_hsts_removed(self):
        self.edit("_headers", lambda t: "\n".join(l for l in t.split("\n") if "Strict-Transport-Security" not in l)); self.expect_fail("Strict-Transport-Security")

    def test_wildcard_source(self):
        self.edit("_headers", lambda t: t.replace("font-src 'none'", "font-src *", 1)); self.expect_fail("insecure or too broad")

    def test_csp_dropped_from_page(self):
        self.edit("_headers", lambda t: t.replace("\n/\n  Content-Security-Policy", "\n/\n  X-Not-Csp", 1)); self.expect_fail("no CSP")

    def test_line_too_long(self):
        self.edit("_headers", lambda t: t + "/x\n  X-Long: " + "a" * 2100 + "\n"); self.expect_fail("limit 2,000")

    def test_static_redirect_after_dynamic(self):
        self.edit("_redirects", lambda t: t + "/late /somewhere 301\n"); self.expect_fail("a static redirect after a dynamic one")

    def test_redirect_bad_code(self):
        self.edit("_redirects", lambda t: t + "/a/* /:splat 418\n"); self.expect_fail("status 418")


class Rewrites(unittest.TestCase):
    def test_transform_refuses_changed_wording(self):
        cfg = make_dist.jread("deploy/cloudflare.config.json")
        pins = make_dist.jread(cfg["runtime"]["pins"])
        table = make_dist.transforms(cfg, pins)
        with self.assertRaises(SystemExit):
            make_dist.apply_transforms("index.html", "a page whose sentences changed", table)

    def test_transform_applies_once(self):
        cfg = make_dist.jread("deploy/cloudflare.config.json")
        pins = make_dist.jread(cfg["runtime"]["pins"])
        table = make_dist.transforms(cfg, pins)
        with open(os.path.join(ROOT, "engine", "worker.js"), encoding="utf-8") as f:
            out = make_dist.apply_transforms("engine/worker.js", f.read(), table)
        self.assertIn("pyodide/%s/" % pins["tag"], out)
        self.assertNotIn("jsdelivr", out)

    def test_clean_target_refuses_foreign_folder(self):
        d = tempfile.mkdtemp(prefix="nl-foreign-", dir=TMP)
        try:
            with open(os.path.join(d, "precious.txt"), "w") as f:
                f.write("do not delete")
            with self.assertRaises(SystemExit):
                make_dist.clean_target(d)
            self.assertTrue(os.path.exists(os.path.join(d, "precious.txt")))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Stub(unittest.TestCase):
    def test_stub_redirects_keep_path_query_and_hash(self):
        import json
        import subprocess
        cfg = make_dist.jread("deploy/cloudflare.config.json")
        body = make_dist.stub_pages(cfg)["index.html"]
        self.assertEqual(body, make_dist.stub_pages(cfg)["404.html"])
        import re
        js = re.search(r"<script>(.*?)</script>", body, re.S).group(1)
        new = cfg["site_url"].rstrip("/")
        cases = [(("/northledger-ai-data-analyst/", "", ""), new + "/"),
                 (("/northledger-ai-data-analyst/case-study.html", "?a=1", "#receipts"), new + "/case-study.html?a=1#receipts"),
                 (("/northledger-ai-data-analyst", "", "#try"), new + "/#try")]
        for (pathname, search, hash_), want in cases:
            prog = "var out;var location={pathname:%s,search:%s,hash:%s,replace:function(u){out=u}};%s;process.stdout.write(out)" % (
                json.dumps(pathname), json.dumps(search), json.dumps(hash_), js)
            try:
                got = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30).stdout
            except FileNotFoundError:
                self.skipTest("node not found")
            self.assertEqual(got, want)
        self.assertIn(new + "/", re.search(r'<noscript>(.*?)</noscript>', body, re.S).group(1))
        with open(os.path.join(ROOT, "deploy", "gh-pages-stub", "index.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), body, "deploy/gh-pages-stub is stale: python3 tools/make_dist.py --stub-only")


if __name__ == "__main__":
    unittest.main(verbosity=1)
