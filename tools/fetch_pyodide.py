#!/usr/bin/env python3
"""Fetch, verify and (when asked) pin the Pyodide runtime that dist/ self-hosts.

The binaries are NEVER committed. What is committed is deploy/pyodide-pins.json: the Pyodide
version, every file's name, size and sha256, the sha256 of the official pyodide-lock.json, and of
the trimmed lock this site serves in its place. This script downloads what is missing into a
cache folder (default .cache/pyodide/<version>/, git-ignored), checks every byte against the pins
and builds the trimmed lock deterministically, so a corrupted or substituted download fails here
and never reaches dist/.

    python3 tools/fetch_pyodide.py            fetch what is missing, verify everything (network)
    python3 tools/fetch_pyodide.py --check    verify the cache only (no network), exit 1 if wrong
    python3 tools/fetch_pyodide.py --pin --from DIR
                                              (maintainers) write deploy/pyodide-pins.json from
                                              a folder that holds the official files, after
                                              verifying every wheel against the official lock

Sources (both read-only, no account): the primary is cdn.jsdelivr.net/pyodide/v<ver>/full/; the
mirror (the same npm release) is unpkg.com/pyodide@<ver>/, used for the core files only. The
build machine contacts them; a visitor's browser never does after the move.

Which packages: engine/pack.json "runtime.pyodide_packages" plus their dependencies in the
official lock (numpy, pandas, python-dateutil, six, pytz, sqlite3 for 0.27.7: 11 files, about
23.8 MB raw). openpyxl is not in 0.27.7's lock at all; the page refuses spreadsheets for that
reason (src/js/50-try.js).
Standard library only; Python 3.9 or later.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(ROOT, "deploy", "pyodide-pins.json")
PACK = os.path.join(ROOT, "engine", "pack.json")
CORE = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip"]
LOCK = "pyodide-lock.json"
UA = "northledger-site-build/1 (+https://northledger.halosyncs.com)"
LIMIT = 25 * 1024 * 1024


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def closure(packages, wanted):
    """Names in `packages` (the official lock's table) reachable from `wanted` through depends."""
    seen = []

    def walk(n):
        k = n.lower().replace("_", "-")
        if k in seen:
            return
        if k not in packages:
            raise SystemExit("package %r is not in the official lock (Pyodide has no such package)" % n)
        seen.append(k)
        for d in packages[k].get("depends", []):
            walk(d)

    for w in wanted:
        walk(w)
    return sorted(seen)


def trimmed_lock(official_bytes, names):
    """The lock this site serves: the same entries, only for `names`. Deterministic bytes."""
    lock = json.loads(official_bytes.decode("utf-8"))
    out = {"info": lock["info"], "packages": {k: lock["packages"][k] for k in names}}
    return json.dumps(out, separators=(",", ":"), sort_keys=True).encode("utf-8")


def tag_of(version, entries):
    """The folder name: the version and ten hex digits of the sha256 over every served file's name
    and hash. A changed byte anywhere is a new folder, so 'immutable' caching can never serve a mix."""
    body = "\n".join("%s %s" % (n, h) for n, h in sorted(entries)).encode("utf-8")
    return "%s-%s" % (version, sha256(body)[:10])


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    return data


def load_pins():
    with open(PINS, encoding="utf-8") as f:
        return json.load(f)


def cache_dir(pins, override=None):
    base = override or os.environ.get("NL_PYODIDE_CACHE") or os.path.join(ROOT, ".cache", "pyodide")
    return os.path.join(base, pins["version"])


def served_entries(pins):
    """(name, sha256, bytes) of every file dist/ serves: four core files, six wheels, the trimmed lock."""
    out = [(f["name"], f["sha256"], f["bytes"]) for f in pins["files"]]
    out.append((LOCK, pins["served_lock"]["sha256"], pins["served_lock"]["bytes"]))
    return out


def verify(pins, d, quiet=False):
    """Check every served file in cache folder `d` against the pins. Returns a list of problems."""
    problems = []
    official_path = os.path.join(d, "official", LOCK)
    served_path = os.path.join(d, LOCK)
    for name, want, size in served_entries(pins):
        path = served_path if name == LOCK else os.path.join(d, name)
        if not os.path.exists(path):
            problems.append("missing %s" % name)
            continue
        b = open(path, "rb").read()
        if len(b) != size:
            problems.append("%s: %d bytes, pinned %d" % (name, len(b), size))
        if sha256(b) != want:
            problems.append("%s: sha256 %s, pinned %s" % (name, sha256(b)[:16], want[:16]))
        if len(b) > LIMIT:
            problems.append("%s is over 25 MiB" % name)
    if os.path.exists(official_path):
        b = open(official_path, "rb").read()
        if sha256(b) != pins["official_lock"]["sha256"]:
            problems.append("official lock: sha256 differs from the pin")
    if not problems and tag_of(pins["version"], [(n, h) for n, h, _ in served_entries(pins)]) != pins["tag"]:
        problems.append("pins: tag does not match the files' hashes (re-pin)")
    if not quiet:
        for n, h, s in served_entries(pins):
            print("  %-60s %10s  %s" % (n, format(s, ","), h[:16]))
    return problems


def fetch(pins, d):
    os.makedirs(os.path.join(d, "official"), exist_ok=True)
    pri, mir = pins["sources"]["primary"], pins["sources"]["mirror"]

    def get_checked(name, want_sha, mirror_ok):
        path = os.path.join(d, name)
        if os.path.exists(path) and sha256(open(path, "rb").read()) == want_sha:
            return
        last = None
        for base in ([pri] + ([mir] if mirror_ok else [])):
            try:
                data = http_get(base + name)
            except Exception as e:  # network, 404 ...
                last = "%s: %s" % (base + name, e)
                continue
            if sha256(data) == want_sha:
                with open(path, "wb") as f:
                    f.write(data)
                print("  fetched %s (%s bytes) from %s" % (name, format(len(data), ","), base.split("/")[2]))
                return
            last = "%s: sha256 %s is not the pinned %s" % (base + name, sha256(data)[:16], want_sha[:16])
        raise SystemExit("could not get %s: %s" % (name, last))

    for f in pins["files"]:
        get_checked(f["name"], f["sha256"], f["kind"] == "core")
    # the official lock (kept only to prove the trimmed one derives from it)
    op = os.path.join(d, "official", LOCK)
    if not (os.path.exists(op) and sha256(open(op, "rb").read()) == pins["official_lock"]["sha256"]):
        data = None
        for base in (pri, mir):
            try:
                data = http_get(base + LOCK)
                if sha256(data) == pins["official_lock"]["sha256"]:
                    break
                data = None
            except Exception:
                data = None
        if data is None:
            raise SystemExit("could not get the official %s matching its pin" % LOCK)
        with open(op, "wb") as f:
            f.write(data)
    derived = trimmed_lock(open(op, "rb").read(), pins["served_lock"]["packages"])
    if sha256(derived) != pins["served_lock"]["sha256"]:
        raise SystemExit("the trimmed lock derived from the official one is not the pinned bytes (Python's json changed?)")
    with open(os.path.join(d, LOCK), "wb") as f:
        f.write(derived)


def pin(src):
    names = {n: os.path.join(src, n) for n in CORE + [LOCK]}
    for n, p in names.items():
        if not os.path.exists(p):
            raise SystemExit("%s is not in %s" % (n, src))
    official = open(names[LOCK], "rb").read()
    lock = json.loads(official.decode("utf-8"))
    pack = json.load(open(PACK, encoding="utf-8"))
    wanted = pack["runtime"]["pyodide_packages"]
    keep = closure(lock["packages"], wanted)
    files = []
    for n in CORE:
        b = open(names[n], "rb").read()
        files.append({"name": n, "kind": "core", "bytes": len(b), "sha256": sha256(b)})
    for k in keep:
        pk = lock["packages"][k]
        p = os.path.join(src, pk["file_name"])
        if not os.path.exists(p):
            raise SystemExit("%s is not in %s" % (pk["file_name"], src))
        b = open(p, "rb").read()
        if sha256(b) != pk["sha256"]:
            raise SystemExit("%s does not match the official lock's sha256" % pk["file_name"])
        files.append({"name": pk["file_name"], "kind": "package", "package": k, "version": pk["version"],
                      "bytes": len(b), "sha256": sha256(b)})
    served = trimmed_lock(official, keep)
    version = lock["info"]["version"]
    pins = {
        "_about": ("What dist/engine/pyodide/<tag>/ is made of. tools/fetch_pyodide.py downloads each file, "
                   "refuses any whose size or sha256 differs, and derives the served lock from the official one. "
                   "The binaries are not in git. Re-pin (tools/fetch_pyodide.py --pin --from DIR) only to upgrade Pyodide."),
        "version": version,
        "python": lock["info"]["python"],
        "platform": lock["info"]["platform"],
        "abi_version": lock["info"]["abi_version"],
        "sources": {"primary": "https://cdn.jsdelivr.net/pyodide/v%s/full/" % version,
                    "mirror": "https://unpkg.com/pyodide@%s/" % version,
                    "_checked": "the four core files and the official lock have the same sha256 on both"},
        "engine_packages": wanted,
        "official_lock": {"file": LOCK, "bytes": len(official), "sha256": sha256(official), "packages_in_lock": len(lock["packages"])},
        "files": files,
        "served_lock": {"file": LOCK, "bytes": len(served), "sha256": sha256(served), "packages": keep},
    }
    pins["tag"] = tag_of(version, [(f["name"], f["sha256"]) for f in files] + [(LOCK, sha256(served))])
    with open(PINS, "w", encoding="utf-8") as f:
        json.dump(pins, f, indent=2)
        f.write("\n")
    total = sum(f["bytes"] for f in files) + len(served)
    print("pinned %s: %d files, %s bytes, tag %s" % (version, len(files) + 1, format(total, ","), pins["tag"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="verify the cache only; no network")
    ap.add_argument("--pin", action="store_true", help="(maintainers) write the pins from --from DIR")
    ap.add_argument("--from", dest="src", help="folder with the official files, for --pin")
    ap.add_argument("--cache", help="cache base folder (default .cache/pyodide or $NL_PYODIDE_CACHE)")
    a = ap.parse_args()
    if a.pin:
        if not a.src:
            raise SystemExit("--pin needs --from DIR")
        pin(os.path.abspath(a.src))
        return 0
    pins = load_pins()
    d = cache_dir(pins, a.cache)
    if not a.check:
        os.makedirs(d, exist_ok=True)
        fetch(pins, d)
    problems = verify(pins, d)
    if problems:
        print("PYODIDE CACHE NOT OK:\n  " + "\n  ".join(problems))
        return 1
    print("pyodide %s: %d files verified against deploy/pyodide-pins.json (tag %s)" % (pins["version"], len(served_entries(pins)), pins["tag"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
