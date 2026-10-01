#!/usr/bin/env python3
"""
Tests for engine/nl_structure.py, the semantic layer of a statistical table (WAVE 4, track A1;
plan/WAVE4-A-DESIGN.md section 6, tests 1-16), and for its hooks in engine/nl_browser.py.

    python tools/test_nl_structure.py                 # the unit tests and the end-to-end tests
    NL_STRUCTURE_ACCEPTANCE=1 python tools/test_nl_structure.py    # also the StatCan acceptance (the dev file)

Every unit-test cube is synthetic (tools/fixtures/structure/make_cubes.py). The detection tests read a cube the way
the engine reads it (dates as dates, numbers as numbers, text as text) without landing it, so they run in
milliseconds; the end-to-end tests run the adapter itself. The StatCan acceptance reads the git-ignored dev file
(.work/eval/data/statcan/retail_sales_provinces_2020_2026.csv, beside this checkout or the main one) with the planner
off and checks the design's numbers.

Self-running like the adapter's tests: prints PASS/FAIL per test, exits 1 on any failure.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ADAPTER_DIR = os.path.abspath(os.environ.get("NL_BROWSER_DIR") or os.path.join(SITE, "engine"))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ADAPTER_DIR)
sys.path.insert(0, os.path.join(HERE, "fixtures", "structure"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import make_cubes as MC  # noqa: E402
import nl_browser as NB  # noqa: E402
import nl_structure as NS  # noqa: E402

AS_OF = "2026-09-30"
STATCAN_REL = os.path.join(".work", "eval", "data", "statcan", "retail_sales_provinces_2020_2026.csv")


def _dev_file(rel: str):
    for root in (SITE, os.path.join(SITE, "..", "portfolio-website")):
        p = os.path.normpath(os.path.join(root, rel))
        if os.path.exists(p):
            return p
    return None


# ----------------------------------------------------------------------------- a reading without landing
class _Spy(dict):
    """A column store that records every column read (test 10: a withheld column is never read)."""

    def __init__(self, frame):
        super().__init__({c: frame[c] for c in frame.columns})
        self.columns = list(frame.columns)
        self.index = frame.index
        self.read = set()

    def __getitem__(self, k):
        self.read.add(k)
        return dict.__getitem__(self, k)


def reading(data: bytes, spy: bool = False):
    """The table as the engine would read it: every column landed by the engine's slug, a column of dates read as
    dates, a column of numbers as numbers, the rest as text; every row kept."""
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    land = {h: NB._engine_slug(h) for h in df.columns}
    texts = df.rename(columns=land)
    values = texts.copy().astype(object)
    for c in values.columns:
        t = texts[c].str.strip()
        f = t[t != ""]
        if not len(f):
            continue
        num = pd.to_numeric(f.str.replace(",", "", regex=False), errors="coerce")
        if num.notna().mean() >= 0.95:
            values[c] = pd.to_numeric(t.str.replace(",", "", regex=False), errors="coerce")
            continue
        if f.str.match(r"^\d{4}-\d{2}(?:-\d{2})?$").mean() >= 0.95:
            values[c] = pd.to_datetime(t.where(t != ""), errors="coerce")
    R = NB._Reading(values, texts, np.ones(len(df), bool), land, {})
    if spy:
        R.values = _Spy(values)
        R.texts = _Spy(texts)
    return R


def detect(data: bytes, hidden=(), **kw):
    return NS.detect(reading(data), hidden, **kw)


def dim(S, col):
    return next(d for d in S["dims"] if d["column"] == col)


def tree_sets(S, col):
    """{parent label: set of children labels} of a dimension's verified tree, labels without codes."""
    d = dim(S, col)
    lab = d["labels"]
    return {NS_strip(lab[p]): {NS_strip(lab[c]) for c in ch} for p, ch in (d.get("tree") or {}).items()}


def NS_strip(s):
    return MC.strip_code(s)


# ----------------------------------------------------------------------------- 1-9, 12, 15, 16: detection
def test_01_a_balanced_total_and_five_regions_is_a_partition_with_nothing_unallocated():
    S = detect(MC.partition())
    assert S["kind"] == "cube" and S["usable"], (S["kind"], S["reason"])
    g = dim(S, "GEO")
    assert g["role"] == "partition" and g["total"] == "Total" and len(g["parts"]) == 5, g
    assert S["default"]["GEO"] == "Total" and S["measure"]["factor"] == 1000.0 and S["measure"]["type"] == "flow"
    m, v = NS._monthly(S, S["default"])
    win = NS.windows(m, v)
    b = NS.breakdown(S, S["breakdowns"][0], S["default"], win)
    assert b["reconciles"] and b["unallocated"]["latest"] == 0 and b["unallocated"]["prior"] == 0, b["unallocated"]
    assert abs(sum(p["contribution"] for p in b["parts"]) - b["change"]) < 1e-6


def test_02_suppressed_region_cells_pass_and_their_sum_is_the_unallocated_part():
    S = detect(MC.partition(0.10))
    g = dim(S, "GEO")
    assert g["role"] == "partition" and g["sum_check"]["incomplete"] > 0, g.get("sum_check")
    want, n_hidden = MC.partition_suppressed_sum()
    m, v = NS._monthly(S, S["default"])
    win = NS.windows(m, v)
    b = NS.breakdown(S, S["breakdowns"][0], S["default"], win)
    assert b["reconciles"]
    assert abs(b["unallocated"]["contribution"] - want) < 1e-6, (b["unallocated"], want)
    assert S["flags"]["by_kind"].get("suppressed") == n_hidden, (S["flags"]["by_kind"], n_hidden)
    assert S["flags"]["codes"]["x"]["missing"] is True


def test_03_a_coded_three_level_hierarchy_with_a_component_and_an_excluding_member():
    S = detect(MC.hierarchy(codes=True))
    d = dim(S, "Industry")
    assert d["role"] == "hierarchy" and d["by"] == "codes", (d["role"], d.get("by"))
    assert tree_sets(S, "Industry") == {k: set(map(MC.strip_code, v)) for k, v in
                                        {MC.strip_code(p): ch for p, ch in MC._TREE.items()}.items()}, tree_sets(S, "Industry")
    assert d["components"] == {MC.COMPONENT[0]: MC.COMPONENT[1]}, d["components"]
    assert MC.EXCLUDING[0] in d["alternatives"], d["alternatives"]
    assert S["default"]["Industry"] == "Total retail [1-3]"
    alt = [s for s in S["slices"] if s["use"] == "alternative"]
    assert alt and alt[0]["where"]["Industry"] == MC.EXCLUDING[0], S["slices"]


def test_04_the_same_tree_without_codes_and_in_a_shuffled_order_within_budget():
    t0 = time.perf_counter()
    S = detect(MC.hierarchy(codes=False, shuffle=True))
    took = time.perf_counter() - t0
    d = dim(S, "Industry")
    assert d["role"] == "hierarchy" and d["by"] == "subset sums", (d["role"], d.get("by"), d.get("why"))
    want = {MC.strip_code(p): set(map(MC.strip_code, ch)) for p, ch in MC._TREE.items()}
    assert tree_sets(S, "Industry") == want, tree_sets(S, "Industry")
    assert MC.strip_code(MC.COMPONENT[0]) in d["components"], d["components"]
    assert MC.strip_code(MC.EXCLUDING[0]) in d["alternatives"], d["alternatives"]
    assert took < NS.BUDGET_S, took


def test_05_an_adjusted_pair_with_neutral_labels_is_found_by_behaviour():
    S = detect(MC.adjusted())
    t = dim(S, "Type")
    assert t["role"] == "adjustment" and t["nsa"] == "A" and t["sa"] == "B", (t["role"], t.get("nsa"), t.get("why"))
    assert S["default"]["Type"] == "A"
    s2 = NS.slice_by_id(S, "S2")
    assert s2 is not None and s2["use"] == "momentum" and s2["where"]["Type"] == "B", S["slices"]
    g = dim(S, "GEO")
    assert g["role"] == "partition" and g["total"] == "All regions", g["role"]
    assert g.get("sa_adds_up") is False, g.get("sa_adds_up")


def test_06_a_rate_is_read_as_its_published_aggregate_never_summed_or_averaged():
    S = detect(MC.rate())
    assert S["measure"]["type"] == "rate", S["measure"]
    g = dim(S, "GEO")
    assert g["role"] == "rate_aggregate" and g["total"] == "Canada", (g["role"], g.get("why"))
    assert S["default"]["GEO"] == "Canada" and S["breakdowns"] == [], S["breakdowns"]
    # the slice is Canada's own published rate, month by month
    times, val, _b = NS.series(S, S["default"])
    df = pd.read_csv(io.BytesIO(MC.rate()), dtype=str)
    can = pd.to_numeric(df[df.GEO == "Canada"].VALUE).to_numpy()
    assert np.allclose(val, can), (val[:3], can[:3])
    viol = NS.check_rows(S, list(range(S["rows"])))
    assert any(v["kind"] == "rate_members" for v in viol), viol


def test_07_an_index_on_two_bases_is_never_averaged_across_bases():
    S = detect(MC.index_two_bases())
    assert S["measure"]["type"] == "index", S["measure"]
    b = dim(S, "Base")
    assert b["role"] == "measure" and b.get("mixed_units"), (b["role"], b.get("unit_of"))
    s1 = S["default"]
    assert isinstance(s1["Base"], str), s1
    others = [s for s in S["slices"] if s["use"] == "other_measure"]
    assert others and others[0]["where"]["Base"] != s1["Base"], S["slices"]
    sel = NS._select(S, s1)
    assert len({int(S["_SM"][s, S["dims"].index(b)]) for s in sel}) == 1


def test_08_a_dimension_of_dollars_and_units_is_a_measure_dimension_with_separate_slices():
    S = detect(MC.mixed_units())
    st = dim(S, "Statistics")
    assert st["role"] == "measure" and st["unit_of"] == {"Sales value": "Dollars", "Units sold": "Number"}, st
    assert S["default"]["Statistics"] == "Sales value", S["default"]
    assert any(s["use"] == "other_measure" and s["where"]["Statistics"] == "Units sold" for s in S["slices"]), S["slices"]
    viol = NS.check_rows(S, list(range(S["rows"])))
    assert any(v["kind"] == "mixed_units" for v in viol), viol


def test_09_a_business_export_with_all_and_total_rows_is_sliced_all_by_total():
    S = detect(MC.business_export())
    assert not S["official"] and S["usable"], (S["official"], S["reason"])
    assert dim(S, "region")["role"] == "partition" and dim(S, "region")["total"] == "All"
    assert dim(S, "product")["role"] == "partition" and dim(S, "product")["total"] == "Total"
    assert S["default"] == {"region": "All", "product": "Total"}, S["default"]
    assert S["measure"]["type"] == "flow"


def test_11_a_business_file_with_no_total_is_not_sliced():
    S = detect(MC.business_export(total_rows=False))
    assert not S["usable"], (S["kind"], [d["role"] for d in S["dims"]])
    assert all(d["role"] == "flat_additive" for d in S["dims"]), [d["role"] for d in S["dims"]]


def test_12_embedded_eurostat_flags_and_ons_x_are_read_as_flags():
    S = detect(MC.eurostat())
    assert S["usable"] and S["measure"]["embedded_flags"], (S["reason"], S["measure"])
    assert S["publisher"] == "eurostat"
    g = dim(S, "geo")
    assert g["role"] == "partition" and g["total"] == "EU27_2020", (g["role"], g.get("why"))
    fl = S["flags"]
    assert fl["codes"][":"]["kind"] == "not_available" and fl["codes"][":"]["missing"], fl["codes"]
    assert fl["codes"]["p"]["kind"] == "provisional" and not fl["codes"]["p"]["missing"], fl["codes"]
    # the embedded "p" is stripped: the value is read
    times, val, _b = NS.series(S, {"geo": "DE"})
    assert not np.isnan(val[3]), val[:6]
    S2 = detect(MC.ons())
    assert S2["usable"] and dim(S2, "region")["total"] == "Great Britain", S2["reason"]
    assert S2["flags"]["codes"]["[x]"]["kind"] == "not_available", S2["flags"]
    assert S2["measure"]["factor"] == 1000.0


def test_15_the_structure_hash_is_the_same_on_every_run():
    a = detect(MC.hierarchy(codes=False, shuffle=True))["hash"]
    b = detect(MC.hierarchy(codes=False, shuffle=True))["hash"]
    c = detect(MC.partition())["hash"]
    assert a == b and a != c, (a, b, c)


def test_16_a_forty_thousand_row_cube_is_detected_under_0_3_seconds():
    data = MC.big()
    R = reading(data)
    assert R.n >= 40000, R.n
    NS.detect(R, ())                       # warm
    t0 = time.perf_counter()
    S = NS.detect(R, ())
    took = time.perf_counter() - t0
    assert S["usable"] and dim(S, "GEO")["role"] == "partition" and dim(S, "Industry")["role"] == "partition"
    assert took < 0.3, took


def test_10a_a_withheld_dimension_makes_the_cube_incomplete_and_is_never_read():
    data = MC.health_region()
    R = reading(data, spy=True)
    S = NS.detect(R, {"health_region"})
    assert S["kind"] == "cube_incomplete" and not S["usable"], (S["kind"], S["reason"])
    assert "health_region" not in R.texts.read and "health_region" not in R.values.read, (R.texts.read, R.values.read)
    assert "health_region" not in json.dumps(NS.public(S))


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print("  PASS  %s" % fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print("  FAIL  %s: %s: %s" % (fn.__name__, type(exc).__name__, str(exc)[:600]))
            if os.environ.get("NL_TRACE"):
                traceback.print_exc()
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    if failed:
        sys.exit(1)
    print("ALL TESTS PASSED")
