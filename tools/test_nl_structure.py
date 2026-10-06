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

sys.path.insert(0, os.path.join(HERE, "fixtures", "viz"))
import validate_spec as VS  # noqa: E402

SPEC = VS.load(os.path.join(HERE, "fixtures", "viz", "spec.json"))
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


# ----------------------------------------------------------------------------- end to end: the adapter's run
def _run(data: bytes, name: str, decisions=None, objective: str = ""):
    NB._PROFILE_CACHE.clear()
    rep = NB.run(data, name, objective, decisions or {}, AS_OF)
    assert rep["ok"], rep["error"]
    return rep


def _items(rep):
    return {it["id"]: it for it in rep["scenarios"]["items"]}


def test_e2e_a_cube_is_read_as_its_headline_slice_with_the_estimand_and_reconciled_parts():
    rep = _run(MC.partition(0.10), "regions.csv")
    est = rep["estimand"]
    assert est and est["plan_source"] == "engine_default" and est["reconciles"] is True, est
    assert rep["input"]["rows"] == 6 * len(MC.MONTHS) and rep["input"]["layout"]["layout"] == NB.STRUCTURE_LAYOUT
    assert rep["structure"]["kind"] == "cube" and rep["structure"]["dims"][0]["role"] == "partition"
    # the headline figures are the Total's own published series, in base units (thousands x 1,000)
    df = pd.read_csv(io.BytesIO(MC.partition(0.10)), dtype=str)
    tot = df[df.GEO == "Total"].set_index("REF_DATE").VALUE.astype(float) * 1000
    lat, pri = est["comparison"]["latest"], est["comparison"]["prior"]
    want = (tot[(tot.index >= lat[0]) & (tot.index <= lat[1])].sum(), tot[(tot.index >= pri[0]) & (tot.index <= pri[1])].sum())
    assert (est["figures"]["latest"]["value"], est["figures"]["prior"]["value"]) == want, (est["figures"], want)
    it = _items(rep)
    parts = [v["value"] for k, v in it.items() if k.startswith("contribution.geo.") and k != "contribution.geo.unallocated"]
    assert len(parts) == 5 and abs(sum(parts) + it["contribution.geo.unallocated"]["value"] - it["headline.change"]["value"]) \
        <= 1e-6 * abs(est["figures"]["latest"]["value"]), it.keys()
    want_u, _n = MC.partition_suppressed_sum()
    assert abs(it["contribution.geo.unallocated"]["value"] - want_u) < 1e-3, (it["contribution.geo.unallocated"], want_u)
    assert rep["scenarios"]["basis"]["source"] == "structure"
    # the row count is never the headline and nothing reads the table's raw rows
    assert not any(f["id"].startswith("measure.volume") for f in rep["findings"]), [f["id"] for f in rep["findings"]]
    out = NB.results_for_ai(rep)
    assert list(out)[:3] == ["ok", "estimand", "structure"], list(out)[:4]
    json.dumps(rep, allow_nan=False)


def test_e2e_the_slice_run_hands_track_a2_the_header_the_row_layout_and_the_hidden_columns():
    """The guarded call (nl_browser._official_inference, track A2 on w4-inference): made once the structure and the
    estimand are on the slice's report, with the file's own header, a layout that carries the table's rows a month, and
    the columns the engine may not read. Without the function (this branch alone) nothing is called."""
    seen = []

    def spy(rep, header, layout, hidden):
        seen.append({"header": list(header), "layout": dict(layout), "hidden": list(hidden),
                     "structure": rep.get("structure"), "estimand": rep.get("estimand")})
    data = MC.partition(0.10)
    orig = getattr(NB, "_official_inference", None)          # present once w4-inference is merged: put it back
    NB._official_inference = spy
    try:
        rep = _run(data, "regions.csv")
    finally:
        if orig is None:
            del NB._official_inference
        else:
            NB._official_inference = orig
    assert len(seen) == 1, len(seen)
    got = seen[0]
    assert got["header"] == list(pd.read_csv(io.BytesIO(data), dtype=str, nrows=0).columns), got["header"]
    assert got["layout"]["rows_a_month"] == 6 and got["layout"]["layout"] == NB.STRUCTURE_LAYOUT, got["layout"]
    assert got["structure"]["kind"] == "cube" and got["estimand"]["slice"][0]["member"] == "Total", got["estimand"]["slice"]
    # the table's rows a month is the series it lists (6 here, 465 for the retail file)
    S = NS.detect(reading(data), set())
    assert NS.rows_a_month(S) == 6
    json.dumps(rep, allow_nan=False)


def _spec_errors(rec):
    """validate_spec's schema and invariants for one chart record, with the owner's byte caps."""
    return VS.validate(rec, SPEC["schema"], SPEC["schema"]) or VS.check_record(rec, SPEC)


def _charts(rep):
    return {c["id"]: c for c in rep["viz"]["charts"]}


def test_viz_structure_heatmaps_recompute_from_the_cube_and_every_record_is_in_the_frozen_spec():
    """A year-on-year heatmap of each breakdown's published parts (the smallest folded into "other"), every cell
    recomputed from the CSV; the calendar is refused when the table has no adjusted series; no chart reads raw rows."""
    data = MC.big()
    rep = _run(data, "big.csv")
    ch = [c for c in rep["viz"]["charts"] if c["chart"] == "change_heatmap"]
    assert [c["data"]["row_label"] for c in ch] == ["GEO", "Industry"], [c["data"]["row_label"] for c in ch]
    assert not [c for c in rep["viz"]["charts"] if c["chart"] == "calendar_heatmap"]
    assert any(r["chart"] == "calendar_heatmap" and "seasonally adjusted" in r["why"] for r in rep["viz"]["refused"]), \
        rep["viz"]["refused"]
    for c in rep["viz"]["charts"]:
        assert not _spec_errors(c), (c["id"], _spec_errors(c))
        assert c["source"].startswith("structure:") and c["inputs"]["rows"] is None, c["source"]
    df = pd.read_csv(io.BytesIO(data), dtype=str)
    df["v"] = pd.to_numeric(df.VALUE) * 1000.0
    for c in ch:
        dim = c["data"]["row_label"]
        other = "Industry" if dim == "GEO" else "GEO"
        keep = df[df[other] == ("All industries" if other == "Industry" else "Canada")]
        wide = keep.pivot_table(index="REF_DATE", columns=dim, values="v", aggfunc="sum")
        rows, cols = c["data"]["rows"], c["data"]["cols"]
        assert len(rows) == 12 and rows[-1] == "other (2 parts)" if dim == "GEO" else len(rows) == 12, rows
        assert not any(r in ("Canada", "All industries") for r in rows), "a total is never a row"
        size = wide.loc[wide.index >= sorted(wide.index)[-12]].abs().sum().drop(["Canada", "All industries"], errors="ignore")
        assert rows[:11] == list(size.sort_values(ascending=False, kind="stable").index[:11]), (rows, size.head(12))
        parts = [x for x in wide.columns if x not in ("Canada", "All industries")]
        folded = [x for x in parts if x not in rows]
        for i, r in enumerate(rows):
            s = wide[folded].sum(axis=1) if r.startswith("other (") else wide[r]
            for j, m in enumerate(cols):
                q = NB._shift_month(m, -12)
                want = 100.0 * (s[m] / s[q] - 1.0)
                got = c["data"]["values"][i][j]
                assert got is not None and abs(got - want) < 1e-4, (dim, r, m, got, want)
                assert c["data"]["n"][i][j] >= 5
        assert "folded into 'other'" in c["subtitle"] and not c["data"]["text"][0][0] == ""
    # the waterfalls' step labels are unique and at most 80 characters, a member's code kept
    for c in rep["viz"]["charts"]:
        if c["chart"] == "contribution_waterfall":
            labs = [s["label"] for s in c["data"]["steps"]]
            assert len(set(labs)) == len(labs) and all(len(x) <= 80 for x in labs), labs
    import nl_viz as NV
    lab = NV._short_label("Clothing, clothing accessories, shoes, jewellery, luggage and leather goods retailers [458]")
    assert lab.endswith(" [458]") and len(lab) <= 80 and lab.startswith("Clothing, clothing"), lab
    assert NV._short_label("Ontario") == "Ontario"


def test_viz_structure_calendar_is_the_adjusted_slice_month_on_month_and_the_heatmaps_say_when_parts_are_too_few():
    data = MC.adjusted_additive()
    rep = _run(data, "adjusted.csv")
    cal = [c for c in rep["viz"]["charts"] if c["chart"] == "calendar_heatmap"]
    assert len(cal) == 1 and cal[0]["section"] == "other" and cal[0]["measure"]["kind"] == "change_pct", cal
    c = cal[0]
    assert not _spec_errors(c), _spec_errors(c)
    assert "seasonally adjusted" in c["title"] and "adjusted" in c["subtitle"]
    df = pd.read_csv(io.BytesIO(data), dtype=str)
    sa = df[(df.GEO == "All regions") & (df.Adjustments == "Seasonally adjusted")].set_index("REF_DATE").VALUE.astype(float)
    for i, y in enumerate(c["data"]["rows"]):
        for j in range(12):
            m = "%s-%02d" % (y, j + 1)
            p = NB._shift_month(m, -1)
            got = c["data"]["values"][i][j]
            if m in sa.index and p in sa.index:
                want = 100.0 * (sa[m] / sa[p] - 1.0)
                assert got is not None and abs(got - want) < 1e-4, (m, got, want)
                assert c["data"]["n"][i][j] == 6, c["data"]["n"][i][j]          # the 6 regions it adds up from
            else:
                assert got is None and c["data"]["text"][i][j] == ""
    # a region is a leaf of this table: its year-on-year cells rest on 1 published figure, so the heatmap says so
    assert any(r["chart"] == "change_heatmap" and "published parts" in r["why"] for r in rep["viz"]["refused"]), \
        rep["viz"]["refused"]
    # the headline is the unadjusted series, the momentum slice is the adjusted one, and the table never mixes them
    assert rep["estimand"]["slice"][-1]["member"] == "Unadjusted"
    assert rep["scenarios"]["basis"]["slice"]["Adjustments"] == "Unadjusted"


def test_e2e_a_plan_names_a_slice_and_breakdowns_by_id_and_an_unknown_id_or_a_row_filter_is_ignored():
    data = MC.adjusted_additive()
    NB._PROFILE_CACHE.clear()
    prof = NB.profile_for_ai(data, "adj.csv", decisions={})
    st = prof["structure"]
    ids = {s["id"]: s for s in st["slices"]}
    assert set(ids) >= {"S1", "S2"} and ids["S2"]["use"] == "momentum", ids
    assert [b["id"] for b in st["breakdowns"]] == ["B1"], st["breakdowns"]
    plan = {"goal": "How did sales move?", "primary": "VALUE", "slice": "S2", "momentum_slice": "S2",
            "breakdowns": ["B1", "B9", "x"],
            "columns": [{"name": "VALUE", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [{"op": "keep_rows", "column": "GEO", "values": ["North"]}]}
    rep = _run(data, "adj.csv", {"__plan__": plan})
    est = rep["estimand"]
    assert est["slice_id"] == "S2" and est["plan_source"] == "ai", (est["slice_id"], est["plan_source"])
    assert est["slice"][-1]["member"] == "Seasonally adjusted" and est["slice"][0]["member"] == "All regions", est["slice"]
    refused = " | ".join(rep["ai_plan"]["refused"])
    assert "breakdown ids the table does not have were ignored" in refused, refused
    assert "keep_rows on GEO was ignored" in refused, refused
    assert [b["id"] for b in rep["scenarios"]["basis"]["breakdowns"]] == ["B1"]
    cal = [c for c in rep["viz"]["charts"] if c["chart"] == "calendar_heatmap"]
    assert len(cal) == 1 and cal[0]["data"]["rows"][0] == "2019"
    json.dumps(rep, allow_nan=False)
    # an id the table does not have is ignored and the default slice read
    rep = _run(data, "adj.csv", {"__plan__": dict(plan, slice="S7", momentum_slice="S9", operations=[])})
    assert rep["estimand"]["slice_id"] == "S1" and rep["estimand"]["plan_source"] == "engine_default"
    assert any("not one of the table's slices" in x for x in rep["ai_plan"]["refused"]), rep["ai_plan"]["refused"]


def test_the_payload_budget_drops_the_unallocated_part_last_and_the_forecast_label_never_stutters():
    rep = _run(MC.partition(0.10), "regions.csv")
    items = rep["scenarios"]["items"]
    order = NB._budget_drop_order(items)
    ids = [items[i]["id"] for i in order]
    assert ids[-1] == "contribution.geo.unallocated" and "contribution.geo.unallocated" not in ids[:-1], ids[-4:]
    assert sorted(order) == list(range(len(items))), "every item has a place in the drop order"
    # a forecast of a series no claim names (a one-row-a-month slice) has a label of its own, not "The the forecast forecast"
    text = " ".join(l["text"] for l in rep["summary"]["lines"])
    assert "The the forecast" not in text and "forecast forecast" not in text, text
    fl = [v for k, v in rep["summary"]["labels"].items() if k.startswith("forecast.")]
    assert fl and all(", forecast for " in x for x in fl), rep["summary"]["labels"]


def test_e2e_a_headline_month_missing_from_a_window_is_compared_on_the_months_both_windows_have():
    """A flow's window figure adds its months up: a month the headline lacks in one window must not make the change
    compare 11 months with 12. The comparison uses the months with a value in both windows and says so."""
    df = pd.read_csv(io.BytesIO(MC.partition(0.0)), dtype=str, keep_default_na=False)
    m = (df.GEO == "Total") & (df.REF_DATE == "2022-06")
    df.loc[m, "VALUE"], df.loc[m, "STATUS"] = "", "x"
    rep = _run(df.to_csv(index=False).encode(), "gap.csv")
    est = rep["estimand"]
    assert est["complete"] is False and est["months_used"] == 11 and est["months_left_out"] == ["2022-06"], est
    assert "11 months with a value in both windows (Jun 2022 left out)" in est["text"], est["text"]
    tot = df[df.GEO == "Total"].set_index("REF_DATE").VALUE
    tot = pd.to_numeric(tot.where(tot != "")) * 1000.0
    lat = [x for x in est["comparison"]["latest"]]
    months = [mo for mo in tot.index if lat[0] <= mo <= lat[1] and mo != "2022-06"]
    want1 = sum(tot[mo] for mo in months)
    want0 = sum(tot[NB._shift_month(mo, -12)] for mo in months)
    F = est["figures"]
    assert abs(F["latest"]["value"] - want1) < 1e-3 and abs(F["prior"]["value"] - want0) < 1e-3, (F, want1, want0)
    assert abs(F["change_pct"]["value"] - 100.0 * (want1 / want0 - 1.0)) < 1e-4 and F["latest"]["months"] == 11
    it = _items(rep)
    assert "11 matched" in it["headline.latest"]["label"], it["headline.latest"]["label"]
    parts = [v["value"] for k, v in it.items() if k.startswith("contribution.geo.") and not k.endswith("unallocated")]
    assert len(parts) == 5 and abs(sum(parts) + it["contribution.geo.unallocated"]["value"] - it["headline.change"]["value"]) < 1e-3
    wf = next(c for c in rep["viz"]["charts"] if c["chart"] == "contribution_waterfall")
    assert wf["data"]["steps"][0]["label"] == "11 matched months before", wf["data"]["steps"][0]["label"]
    # fewer than 6 months with a value in both windows: no figure is printed (a total of 5 months is no year)
    d = df.copy()
    m = (d.GEO == "Total") & d.REF_DATE.isin(["2022-01", "2022-02", "2022-03", "2022-04", "2022-05", "2022-06", "2022-07"])
    d.loc[m, "VALUE"], d.loc[m, "STATUS"] = "", "x"
    rep = _run(d.to_csv(index=False).encode(), "gap2.csv")
    F = rep["estimand"]["figures"]
    assert rep["estimand"]["complete"] is False and F["change_pct"]["value"] is None and F["prior"]["value"] is None, F
    assert not [k for k in _items(rep) if k.startswith("contribution.")], "a breakdown of a change that is not stated"
    json.dumps(rep, allow_nan=False)


def test_e2e_a_rate_table_reads_its_published_aggregate_and_never_adds_or_averages_members():
    NB._PROFILE_CACHE.clear()
    rep = json.loads(NB.run_json(MC.rate(), "rates.csv", "", None, AS_OF))
    assert rep["ok"] and rep["estimand"], rep.get("error")
    assert rep["estimand"]["slice"][0]["member"] == "Canada" and rep["estimand"]["measure"]["type"] == "rate"
    df = pd.read_csv(io.BytesIO(MC.rate()), dtype=str)
    can = df[df.GEO == "Canada"].set_index("REF_DATE").VALUE.astype(float)
    lat = rep["estimand"]["comparison"]["latest"]
    want = can[(can.index >= lat[0]) & (can.index <= lat[1])].mean()
    assert abs(rep["estimand"]["figures"]["latest"]["value"] - want) < 1e-6, (rep["estimand"]["figures"], want)
    assert rep["estimand"]["figures"]["change"]["text"].endswith("percentage points"), rep["estimand"]["figures"]
    assert not [i for i in rep["scenarios"]["items"] if i["id"].startswith("contribution.")], "a rate was broken down"
    assert any("never added or averaged" in x for x in rep["scenarios"]["refused"]), rep["scenarios"]["refused"]


def test_e2e_10_a_withheld_dimension_refuses_the_business_analysis_with_a_reason():
    rep = _run(MC.health_region(), "health.csv")
    json.dumps(rep, allow_nan=False)
    fl = {f["column"]: f["decision"] for f in rep["privacy"]["flagged"]}
    assert fl.get("health_region") == "withhold" and not rep["privacy"]["released"], rep["privacy"]
    assert rep["structure"]["kind"] == "cube_incomplete", rep.get("structure")
    assert rep["story"]["headline"].startswith(NB.GATE_TRIPPED) and "tell the rows apart" in rep["story"]["headline"], \
        rep["story"]["headline"]
    assert not [f for f in rep["findings"] if f["kind"] == "business"], [f["id"] for f in rep["findings"]]
    assert rep["estimand"] is None and rep["scenarios"]["items"] == []
    assert "Health region number" not in json.dumps(NB.results_for_ai(rep))


def test_e2e_11_a_business_file_with_no_total_is_read_as_before():
    data = MC.business_export(total_rows=False)
    rep = _run(data, "sales.csv")
    try:
        NB.STRUCTURE_ON = False
        old = _run(data, "sales.csv")
    finally:
        NB.STRUCTURE_ON = True
    assert rep["estimand"] is None and old["estimand"] is None
    pick = lambda r: (r["story"], [(f["id"], f["value"], f["grade"]) for f in r["findings"]], r["scenarios"]["items"],
                      [c["id"] for c in r["charts"]], r["downloads"]["clean_csv"])
    assert pick(rep) == pick(old)


def test_e2e_13_a_category_is_released_names_and_free_text_stay_flagged_and_the_visitor_can_withhold():
    rep = _run(MC.category_long(), "segments.csv")
    rel = rep["privacy"]["released"]
    assert [r["column"] for r in rel] == ["industry_segment"] and rel[0]["distinct"] == 30, rel
    assert rel[0]["text"] == "Read as a category, not personal data: Industry segment (30 labels)", rel[0]
    assert not rep["privacy"]["flagged"], rep["privacy"]
    rep = _run(MC.category_long(header="Stylist", names=True), "stylists.csv")
    assert not rep["privacy"]["released"] and [f["column"] for f in rep["privacy"]["flagged"]] == ["stylist"], rep["privacy"]
    rep = _run(MC.category_long(rows=5000, labels=5000), "notes.csv")
    assert not rep["privacy"]["released"] and [f["column"] for f in rep["privacy"]["flagged"]] == ["industry_segment"]
    # AM1: a sensitive header is never released, categorical or not
    rep = _run(MC.category_long(header="Health condition"), "conditions.csv")
    assert not rep["privacy"]["released"] and [f["column"] for f in rep["privacy"]["flagged"]] == ["health_condition"]
    # the visitor may still withhold a released column: it is then flagged and withheld, its values in no download
    rep = _run(MC.category_long(), "segments.csv", {"Industry segment": "withhold"})
    assert not rep["privacy"]["released"] and rep["privacy"]["flagged"] == [
        {"column": "industry_segment", "kind": "free text", "decision": "withhold"}], rep["privacy"]
    assert "Segment 07" not in rep["downloads"]["clean_csv"]


def test_e2e_14_a_plan_that_keeps_one_adjustment_but_mixes_regions_is_corrected():
    data = MC.adjusted()
    plan = {"goal": "How did sales change?", "columns": [{"name": "VALUE", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [{"op": "keep_rows", "column": "Type", "values": ["A"]}], "primary": "VALUE",
            "analyses": [{"type": "compare", "columns": ["VALUE"], "by": "GEO", "why": "regions"}]}
    rep = _run(data, "adjusted.csv", {"__plan__": plan})
    json.dumps(rep, allow_nan=False)
    est = rep["estimand"]
    assert est["plan_source"] == "ai_corrected", est["plan_source"]
    kinds = {(c["dim"], c["kind"]) for c in rep["structure"]["corrections"]}
    assert ("GEO", "total_with_parts") in kinds, kinds
    assert rep["plan_signals"] == [] and rep["ai_plan"]["structure_slice"]["plan_source"] == "ai_corrected"
    assert any("default slice" in x for x in rep["ai_plan"]["refused"]), rep["ai_plan"]["refused"]
    assert any(x.startswith("compare: it reads GEO") for x in rep["ai_analyses"]["refused"]), rep["ai_analyses"]
    # the same plan naming the slice id: the AI's choice, the row filter on a structure dimension ignored
    rep = _run(data, "adjusted.csv", {"__plan__": dict(plan, slice="S2")})
    assert rep["estimand"]["plan_source"] == "ai" and rep["estimand"]["slice_id"] == "S2", rep["estimand"]["slice_id"]
    assert any("keep_rows on Type was ignored" in x for x in rep["ai_plan"]["refused"]), rep["ai_plan"]["refused"]


def test_e2e_the_profile_carries_the_structure_block_within_its_cap_and_only_profile_values():
    data = MC.hierarchy()
    NB._PROFILE_CACHE.clear()
    prof = NB.profile_for_ai(data, "h.csv", decisions={})
    st = prof.get("structure")
    assert st and st["kind"] == "cube" and len(json.dumps(st, separators=(",", ":")).encode()) <= NS.PROFILE_CAP, st
    vals = {c["name"]: set(c.get("values") or c.get("top_values") or []) for c in prof["columns"]}
    for s in st["slices"]:
        assert NS.SLICE_ID.match(s["id"])
        for col, m in s["where"].items():
            assert m == "*" or m in vals[col], (col, m)
    assert any(d["role"] == "hierarchy" for d in st["dims"]) and st["slices"][0]["default"] is True


def test_acceptance_statcan_retail_planner_off_and_an_unadjusted_only_plan():
    path = _dev_file(STATCAN_REL)
    if path is None:
        print("    SKIP: the StatCan dev file is not beside this checkout (%s)" % STATCAN_REL)
        return
    data = open(path, "rb").read()
    t0 = time.perf_counter()
    rep = _run(data, os.path.basename(path), {})
    took = time.perf_counter() - t0
    json.dumps(rep, allow_nan=False)
    est = rep["estimand"]
    F = est["figures"]
    assert (F["prior"]["text"], F["latest"]["text"], F["change_pct"]["text"]) == ("$834.7B", "$864.0B", "+3.5%"), F
    assert F["change"]["text"] == "+$29.3B" and est["reconciles"] is True and est["plan_source"] == "engine_default"
    it = _items(rep)
    geo = sorted(((k, v) for k, v in it.items() if k.startswith("contribution.geo.") and not k.endswith("unallocated")),
                 key=lambda kv: -kv[1]["value"])
    assert [(k.split(".")[-1], v["text"]) for k, v in geo[:3]] == [("ontario", "+$10.2B"), ("quebec", "+$6.2B"),
                                                                   ("alberta", "+$5.9B")], geo[:3]
    assert abs(it["contribution.geo.unallocated"]["value"]) < 0.05e9
    for c in est["sum_checks"]:
        assert abs(c["unallocated_latest"]["value"]) < 0.05e9, c
    nai = sorted(((k, v) for k, v in it.items() if k.startswith("contribution.naics.") and not k.endswith("unallocated")),
                 key=lambda kv: -kv[1]["value"])
    assert [(k.split(".")[-1], v["text"]) for k, v in nai[:3]] == [("455", "+$8.1B"), ("456", "+$6.6B"),
                                                                   ("457", "+$6.1B")], nai[:3]
    assert len(nai) == 9 and abs(sum(v["value"] for _k, v in nai) + it["contribution.naics.unallocated"]["value"]
                                 - F["change"]["value"]) < 1.0
    rel = [r["header"] for r in rep["privacy"]["released"]]
    assert rel == ["North American Industry Classification System (NAICS)"], rep["privacy"]
    assert [f["column"] for f in rep["privacy"]["flagged"]] == ["coordinate"], rep["privacy"]
    assert rep["structure"]["flags"]["by_kind"] == {"suppressed": 5430, "not_available": 533, "too_unreliable": 162}
    assert not any("status" in f["id"] for f in rep["findings"]), [f["id"] for f in rep["findings"]]
    assert not any("status" in x.lower() for x in rep["health"]["issues"] + rep["structure"]["file_health"]["issues"])
    assert not any(f["id"].startswith("forecast.monthly_rows") for f in rep["findings"]), "a row forecast"
    assert not any(c.get("chart") == "group_ranges" for c in rep["viz"]["charts"])
    kinds = [c["chart"] for c in rep["viz"]["charts"]]
    assert kinds.count("contribution_waterfall") == 2 and kinds.count("change_heatmap") == 2 and \
        kinds.count("calendar_heatmap") == 1, kinds
    for c in rep["viz"]["charts"]:
        assert not _spec_errors(c), (c["id"], _spec_errors(c))
    cal = next(c for c in rep["viz"]["charts"] if c["chart"] == "calendar_heatmap")
    assert "seasonally adjusted" in cal["title"] and max(n for row in cal["data"]["n"] for n in row if n) == 13, cal["title"]
    geo = next(c for c in rep["viz"]["charts"] if c["chart"] == "change_heatmap" and c["data"]["row_label"] == "GEO")
    assert geo["data"]["rows"][:3] == ["Ontario", "Quebec", "British Columbia"] and geo["data"]["rows"][-1] == "other (2 parts)"
    assert rep["input"]["rows"] == 36735 and rep["input"]["columns"] == 17
    print("    statcan planner off: %.1f s native" % took)
    plan = {"goal": "How did retail sales change?", "primary": "VALUE",
            "columns": [{"name": "VALUE", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [{"op": "keep_rows", "column": "Adjustments", "values": ["Unadjusted"]}]}
    rep = _run(data, os.path.basename(path), {"__plan__": plan})
    assert rep["estimand"]["plan_source"] == "ai_corrected" and rep["estimand"]["figures"]["latest"]["text"] == "$864.0B"
    assert {c["dim"] for c in rep["structure"]["corrections"]} >= {"GEO"}, rep["structure"]["corrections"]


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
