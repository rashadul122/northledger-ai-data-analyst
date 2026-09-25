#!/usr/bin/env python3
"""Pack the NorthLedger engine for the in-browser demo (Pyodide).

    python tools/pack_engine.py            # write engine/northledger-browser.zip and engine/pack.json
    python tools/pack_engine.py --check    # exit 1 if either no longer matches the engine source
    python tools/pack_engine.py --imports  # list every import the packed code makes, and where it
                                           # comes from in Pyodide

The zip holds exactly what the browser needs and nothing else:
  northledger/<module>.py   the engine modules the adapter's path uses, copied byte for byte
  northledger/<data file>   data the packed modules read from beside themselves (ENGINE_DATA:
                            the slim benchmark receipt), copied byte for byte
  benchmark/engine_benchmark.json  the forecast part of the benchmark receipt (no local paths),
                            where the engine reads it
  nl_browser.py             the adapter (engine/nl_browser.py)
  nl_stubs/                 stand-ins for standard modules a WebAssembly Python may lack
  nl_pack.json              the engine snapshot the zip was cut from, read by the adapter

The engine snapshot is the id build.py and northledger.loop.engine_snapshot() use: sha256 over
every northledger/*.py (name, NUL, bytes, NUL, in sorted order), first 12 hex characters. It
names the WHOLE engine the packed modules were copied from, so a report made in the browser
carries the same id as the site's other receipts. Any edit to any engine file changes it, and
--check then fails until the pack is rebuilt.

The zip is deterministic (fixed member order, timestamps and permissions), so --check compares
bytes. Standard library only. Nothing is fetched and nothing is installed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
import sys
import sysconfig
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_SRC = os.path.normpath(os.path.join(SITE, "..", "northledger-core", "northledger"))
OUT_DIR = os.path.join(SITE, "engine")
ZIP_NAME = "northledger-browser.zip"
MANIFEST = os.path.join(OUT_DIR, "pack.json")

# The engine modules nl_browser's path imports: land -> decide -> run_loop -> run_analyze.
# Deliberately NOT packed (the adapter never reaches them): analyst (the AI analyst, needs
# requests), enrich (web slots), evaluate, extract_audit, report (PDF/Word/Excel writers),
# powerbi, pbi, pbip_lint, tmdl_check (a subprocess), __main__ (the CLI).
ENGINE_MODULES = ("__init__", "_sqlite", "benchmark", "clean", "engagement", "facts", "forecast", "gate",
                  "health", "intake", "loop", "measure", "narrate", "stats", "vocab")
# benchmark: the adapter judges the shipped receipt's no-change conditions with the benchmark's
# own certification (judge_nulls_certify), so the page states the release check's result.
# Data files the packed modules read from beside themselves, copied byte for byte:
# benchmark_receipt.json is the slim benchmark receipt gate.load_benchmark_receipt() reads
# (gate.RECEIPT_FILE) to quote the measured false-confirm rate beside a confirmed change.
# Without it the browser engine says "no benchmark receipt ships with this engine" where the
# native engine quotes a measured rate, so the two reports would differ.
ENGINE_DATA = ("benchmark_receipt.json",)
# The forecast part of the benchmark receipt, packed at the path the engine reads it from
# (forecast.py default_coverage_evidence: ../benchmark/engine_benchmark.json beside northledger/).
# The forecast caveat quotes the 80% ranges' measured coverage from it only when it measured this
# very decision code. The browser cannot recompute that code's hash (it spans modules the pack
# leaves out), so the pack stamps it (nl_pack.json "decision_code_snapshot", computed here from
# the whole engine) and the adapter hands it to the engine's own check.
# The full receipt is not shipped as it is: it records the machine and the engine's local folder
# (engine_root), which no served file may carry. The pack cuts it to what forecast.coverage_evidence
# reads (decision_code_snapshot and results.forecast, unchanged), plus where it came from.
ENGINE_ROOT = os.path.dirname(ENGINE_SRC)
ENGINE_EXTRA = {"benchmark/engine_benchmark.json": os.path.join(ENGINE_ROOT, "benchmark", "engine_benchmark.json")}


POWER_CELL_KEYS = ("name", "claim", "role", "gated", "months", "cv", "phi", "level", "shift", "seasonal",
                   "white_sd")


def cut_receipt(raw: bytes) -> bytes:
    """The shipped benchmark/engine_benchmark.json: the forecast cells and the decision-code id of
    the full receipt, byte-stable (sorted keys), with the full receipt's sha256."""
    full = json.loads(raw.decode("utf-8"))
    cut = {"spec": full.get("spec"), "ran_at": full.get("ran_at"), "size": full.get("size"),
           "master_seed": full.get("master_seed"),
           "decision_code_snapshot": full.get("decision_code_snapshot"),
           "results": {"forecast": (full.get("results") or {}).get("forecast") or [],
                       # the power cells (a true change planted), with only what the adapter's
                       # power quote reads (engine/nl_browser.py _V2.power_for), values unchanged
                       "change": [{"cell": {k: (r.get("cell") or {}).get(k) for k in POWER_CELL_KEYS},
                                   "n": r.get("n"), "k_confirm": r.get("k_confirm"),
                                   "phi_hat_mean": r.get("phi_hat_mean"),
                                   "clopper_pearson95": r.get("clopper_pearson95")}
                                  for r in (full.get("results") or {}).get("change") or []
                                  if (r.get("cell") or {}).get("role") == "alt"]},
           "cut": {"by": "portfolio-website/tools/pack_engine.py",
                   "from": "northledger-core/benchmark/engine_benchmark.json",
                   "from_sha256": _sha(raw),
                   "kept": "decision_code_snapshot and results.forecast, unchanged: what "
                           "northledger.forecast.coverage_evidence reads; and, of results.change, "
                           "the power cells' names, conditions, k of n, mean estimated momentum and "
                           "Clopper-Pearson interval: what the adapter's power quote reads"}}
    return (json.dumps(cut, indent=1, sort_keys=True) + "\n").encode("utf-8")
ADAPTER_FILES = {"nl_browser.py": "nl_browser.py",
                 "nl_stubs/__init__.py": os.path.join("nl_stubs", "__init__.py"),
                 "nl_stubs/resource.py": os.path.join("nl_stubs", "resource.py")}
SAMPLE = {"file": "sample-messy.csv", "as_of": "2026-09-15",
          "generator": "tools/make_sample_messy.py"}

# What a Pyodide page must load before running the adapter, and what it provides.
PYODIDE_PACKAGES = ("numpy", "pandas", "sqlite3")         # pyodide.loadPackage([...])
PYODIDE_UNVENDORED = {"sqlite3": "part of the standard library, shipped separately: "
                                 "loadPackage('sqlite3')",
                      "ssl": "loadPackage('ssl')", "lzma": "loadPackage('lzma')"}
STUBBED = {"resource": "nl_stubs/resource.py, installed only if the real module is missing"}
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)



def adapter_limits():
    """MAX_BYTES and MAX_ROWS as engine/nl_browser.py states them, so pack.json (and build.py's
    check against the page's limits) can never quote a different figure from the one enforced."""
    with open(os.path.join(OUT_DIR, "nl_browser.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    got = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("MAX_BYTES", "MAX_ROWS"):
            got[node.targets[0].id.lower()] = ast.literal_eval(node.value)
    return {"max_bytes": got["max_bytes"], "max_rows": got["max_rows"]}


def adapter_contract() -> dict:
    """The report contract version engine/nl_browser.py returns (CONTRACT_VERSION) and the file
    that defines it, so a page can check what it is about to read before it runs the engine."""
    with open(os.path.join(OUT_DIR, "nl_browser.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "CONTRACT_VERSION":
            return {"version": ast.literal_eval(node.value), "spec": "engine/CONTRACT-v2.md"}
    return {"version": 1, "spec": "engine/nl_browser.py REPORT_KEYS"}

def engine_thresholds(src: str = ENGINE_SRC) -> dict:
    """The two engine bars the page states up front, read from the engine's own policy classes
    (the defaults its runs use): months of history a forecast needs, and rows a finding must
    rest on before it can be a RECOMMEND."""
    def field(module: str, cls: str, name: str):
        with open(os.path.join(src, module), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for st in node.body:
                    if isinstance(st, ast.AnnAssign) and getattr(st.target, "id", "") == name:
                        return ast.literal_eval(st.value)
        raise SystemExit("pack: %s.%s not found in northledger/%s" % (cls, name, module))
    def config(key: str):
        """A value of forecast.DEFAULT_CONFIG, the settings every run uses."""
        with open(os.path.join(src, "forecast.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
                tgt = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
                if getattr(tgt, "id", "") == "DEFAULT_CONFIG":
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and k.value == key:
                            return ast.literal_eval(v)
        raise SystemExit("pack: DEFAULT_CONFIG[%r] not found in northledger/forecast.py" % key)

    def conformal_min(level: float) -> int:
        """forecast.conformal_min_errors: the fewest errors a range at `level` can be drawn from,
        2/(1 - level) - 1 rounded up (9 for 80%). tools/test_nl_browser.py checks the sum below
        against forecast.min_history_months() itself."""
        return int(-(-(2.0 / (1.0 - level) - 1.0 - 1e-9) // 1))
    # The months a forecast can be checked on at all (forecast.min_history_months): the months
    # a model learns from before its first replayed forecast, the errors a first range needs,
    # and the replayed months the gate requires (review, 25 Sep 2026: the page quoted only the
    # 36-month floor, and a 38-month file got no forecast because only 5 months could be replayed).
    replay = int(config("min_holdout"))
    checkable = int(config("min_train")) + max(int(config("min_band_errors")), conformal_min(float(config("band")))) + replay
    return {"forecast_min_history_months": field("forecast.py", "ForecastPolicy", "min_history_months"),
            "forecast_min_checkable_months": max(checkable, field("forecast.py", "ForecastPolicy", "min_history_months")),
            "forecast_replay_months": field("forecast.py", "ForecastPolicy", "min_holdout"),
            "recommend_min_rows": field("gate.py", "GatePolicy", "min_rows_recommend")}


def engine_snapshot(src: str = ENGINE_SRC) -> str:
    """Same recipe as northledger.loop.engine_snapshot and build.py."""
    h = hashlib.sha256()
    for f in sorted(f for f in os.listdir(src) if f.endswith(".py")):
        h.update(f.encode("utf-8") + b"\0")
        with open(os.path.join(src, f), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def decision_snapshot(src: str = ENGINE_SRC) -> str:
    """Same recipe as northledger.forecast.decision_snapshot (and benchmark.engine_snapshots):
    sha256 of northledger/*.py leaving out benchmark.py, the id a benchmark receipt stamps as
    decision_code_snapshot."""
    h = hashlib.sha256()
    for f in sorted(f for f in os.listdir(src) if f.endswith(".py") and f != "benchmark.py"):
        h.update(f.encode("utf-8") + b"\0")
        with open(os.path.join(src, f), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def engine_version(src: str = ENGINE_SRC) -> str:
    with open(os.path.join(src, "__init__.py"), encoding="utf-8") as fh:
        for node in ast.parse(fh.read()).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__version__"
                                                    for t in node.targets):
                return str(ast.literal_eval(node.value))
    return ""


def members() -> list:
    """(path in zip, source path) for every packed file, in zip order."""
    out = [("northledger/%s.py" % m, os.path.join(ENGINE_SRC, "%s.py" % m)) for m in ENGINE_MODULES]
    out += [("northledger/%s" % f, os.path.join(ENGINE_SRC, f)) for f in ENGINE_DATA]
    out += sorted(ENGINE_EXTRA.items())
    out += [(arc, os.path.join(OUT_DIR, rel)) for arc, rel in sorted(ADAPTER_FILES.items())]
    return out


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build() -> tuple:
    """(zip bytes, manifest dict), computed from the sources on disk."""
    snap = engine_snapshot()
    files = []
    blobs = []
    for arc, src in members():
        with open(src, "rb") as fh:
            b = fh.read()
        if arc in ENGINE_EXTRA:
            b = cut_receipt(b)
        blobs.append((arc, b))
        source = ("northledger-core/northledger/%s" % os.path.basename(src) if arc.startswith("northledger/")
                  else "northledger-core/%s" % arc if arc in ENGINE_EXTRA
                  else "portfolio-website/engine/%s" % arc)
        files.append({"path": arc, "sha256": _sha(b), "bytes": len(b), "source": source})
    stamp = {"engine_snapshot": snap[:12], "engine_snapshot_full": snap,
             "decision_code_snapshot": decision_snapshot(),
             "engine_version": engine_version(),
             "snapshot_recipe": "sha256 of northledger/*.py (no git commit available)",
             "files": {f["path"]: f["sha256"] for f in files}}
    stamp_bytes = (json.dumps(stamp, indent=1, sort_keys=True) + "\n").encode("utf-8")
    blobs.append(("nl_pack.json", stamp_bytes))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for arc, b in blobs:
            info = zipfile.ZipInfo(arc, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            info.create_system = 3
            z.writestr(info, b)
    zbytes = buf.getvalue()
    sample_path = os.path.join(OUT_DIR, SAMPLE["file"])
    sample = dict(SAMPLE)
    if os.path.exists(sample_path):
        with open(sample_path, "rb") as fh:
            sb = fh.read()
        sample.update({"sha256": _sha(sb), "bytes": len(sb)})
    manifest = {
        "what": "The NorthLedger engine packed for the in-browser demo: the modules the adapter's "
                "path uses, copied unmodified, plus the adapter and its stubs.",
        "engine_snapshot": snap[:12],
        "engine_snapshot_full": snap,
        "engine_version": engine_version(),
        "decision_code_snapshot": decision_snapshot(),
        "snapshot_recipe": "sha256 of northledger/*.py (no git commit available); the same id "
                           "build.py and northledger.loop.engine_snapshot() compute",
        "zip": {"file": ZIP_NAME, "sha256": _sha(zbytes), "bytes": len(zbytes)},
        "files": files,
        "not_packed": sorted(f[:-3] for f in os.listdir(ENGINE_SRC)
                             if f.endswith(".py") and f[:-3] not in ENGINE_MODULES),
        "runtime": {"pyodide_packages": list(PYODIDE_PACKAGES),
                    "entry": "import nl_browser; nl_browser.run_json(data, name, objective, "
                             "decisions_json, as_of)",
                    "stubs": dict(STUBBED)},
        "sample": sample,
        "limits": adapter_limits(),
        "contract": adapter_contract(),
        "thresholds": engine_thresholds(),
        "rebuild": "python3 tools/pack_engine.py (and --check to verify)",
    }
    return zbytes, manifest


# --------------------------------------------------------------------------- imports
def _stdlib_dirs() -> list:
    paths = sysconfig.get_paths()
    return [os.path.realpath(p) for p in (paths.get("stdlib"), paths.get("platstdlib")) if p]


def classify(module: str) -> str:
    top = module.split(".")[0]
    if top in STUBBED:
        return "stub if missing: %s" % STUBBED[top]
    if top in PYODIDE_UNVENDORED:
        return "Pyodide standard library, %s" % PYODIDE_UNVENDORED[top]
    if top in ("numpy", "pandas"):
        return "Pyodide package: loadPackage('%s')" % top
    if top in sys.builtin_module_names:
        return "Pyodide standard library (built in)"
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return "NOT AVAILABLE"
    origin = os.path.realpath(spec.origin or "")
    if spec.origin in ("built-in", "frozen") or (
            any(origin.startswith(d) for d in _stdlib_dirs()) and "site-packages" not in origin):
        return "Pyodide standard library"
    return "third-party (not in Pyodide's default set)"


def imports_report() -> list:
    """Every import in every packed file: (file, line, module, where, source)."""
    packed = {m for m in ENGINE_MODULES}
    rows = []
    for arc, src in members():
        if not arc.endswith(".py"):     # a packed data file imports nothing
            continue
        with open(src, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        parents = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[ch] = node

        def where(n):
            p = parents.get(n)
            while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                p = parents.get(p)
            return "module level" if p is None else "inside %s()" % p.name

        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                if node.level:          # relative: from . import x / from .x import y
                    if node.module:
                        mods = ["northledger." + node.module]
                    else:
                        mods = ["northledger." + a.name for a in node.names]
                else:
                    base = node.module or ""
                    if base == "nl_stubs":
                        mods = ["nl_stubs." + a.name for a in node.names]
                    else:
                        mods = [base]
            for m in mods:
                if m.startswith("northledger"):
                    sub = m.split(".")[1] if "." in m else "__init__"
                    src_kind = ("engine, packed" if sub in packed else
                                "engine, NOT packed: never reached by the adapter")
                elif m.startswith("nl_stubs"):
                    src_kind = "adapter stub, packed"
                else:
                    src_kind = classify(m)
                rows.append((arc, node.lineno, m, where(node), src_kind))
    return rows


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the pack no longer matches the source")
    ap.add_argument("--imports", action="store_true", help="list every import and its Pyodide source")
    a = ap.parse_args(argv)
    zpath = os.path.join(OUT_DIR, ZIP_NAME)

    if a.imports:
        rows = imports_report()
        bad = [r for r in rows if r[4] in ("NOT AVAILABLE", "third-party (not in Pyodide's default set)")
               or ("NOT packed" in r[4] and r[3] == "module level")]
        seen = {}
        for arc, line, m, wh, kind in rows:
            seen.setdefault((m.split(".")[0] if not m.startswith(("northledger", "nl_stubs")) else m, kind), []
                            ).append("%s:%d (%s)" % (arc, line, wh))
        for (m, kind), where_ in sorted(seen.items()):
            print("%-28s %s" % (m, kind))
            for w in where_[:6]:
                print("%-28s   %s" % ("", w))
            if len(where_) > 6:
                print("%-28s   ... %d more" % ("", len(where_) - 6))
        if bad:
            print("\nIMPORT PROBLEMS: %d" % len(bad))
            for r in bad:
                print("  %s:%d %s (%s): %s" % r)
            return 1
        print("\nEvery import is Pyodide standard library, a Pyodide package, a packed engine module "
              "or a packed stub; imports of unpacked engine modules sit only inside functions the "
              "adapter never calls.")
        return 0

    zbytes, manifest = build()
    if a.check:
        problems = []
        if not os.path.exists(zpath):
            problems.append("%s is missing" % ZIP_NAME)
        else:
            with open(zpath, "rb") as fh:
                on_disk = fh.read()
            if on_disk != zbytes:
                problems.append("%s differs from a fresh pack of the current source" % ZIP_NAME)
                try:
                    with zipfile.ZipFile(io.BytesIO(on_disk)) as z:
                        old = {n: _sha(z.read(n)) for n in z.namelist()}
                    for f in manifest["files"]:
                        if old.get(f["path"]) != f["sha256"]:
                            problems.append("  %s changed since the pack (source %s)" % (f["path"], f["source"]))
                    if "nl_pack.json" in old:
                        with zipfile.ZipFile(io.BytesIO(on_disk)) as z:
                            was = json.loads(z.read("nl_pack.json")).get("engine_snapshot")
                        if was != manifest["engine_snapshot"]:
                            problems.append("  engine snapshot was %s, the engine is now %s"
                                            % (was, manifest["engine_snapshot"]))
                except zipfile.BadZipFile:
                    problems.append("  the zip on disk is not a readable zip")
        try:
            with open(MANIFEST, encoding="utf-8") as fh:
                if json.load(fh) != manifest:
                    problems.append("pack.json differs from a fresh pack of the current source")
        except (FileNotFoundError, ValueError):
            problems.append("pack.json is missing or unreadable")
        if problems:
            print("pack FAILS its check:")
            for p in problems:
                print("  " + p)
            print("Rebuild with: python3 tools/pack_engine.py")
            return 1
        print("pack OK: %s and pack.json match engine snapshot %s (%d files)"
              % (ZIP_NAME, manifest["engine_snapshot"], len(manifest["files"])))
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(zpath, "wb") as fh:
        fh.write(zbytes)
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")
    print("wrote engine/%s (%d bytes, %d files) and engine/pack.json; engine snapshot %s"
          % (ZIP_NAME, len(zbytes), len(manifest["files"]) + 1, manifest["engine_snapshot"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
