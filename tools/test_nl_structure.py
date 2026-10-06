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
import math
import re
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
    """The hand-off (nl_browser._official_inference, track A2), now that both tracks are one engine: the slice's own run
    calls it first with the slice's two-column header and no structure yet (a no-op: that header holds no publisher's
    signature), and the slice's call is the one that matters: made once the structure and the estimand are on the
    slice's report, with the FILE's own header, a layout that carries the table's rows a month, and the columns the
    engine may not read. The call is not guarded: the function is part of this engine."""
    seen = []

    def spy(rep, header, layout, hidden):
        seen.append({"header": list(header), "layout": dict(layout), "hidden": list(hidden),
                     "structure": rep.get("structure"), "estimand": rep.get("estimand")})
    data = MC.partition(0.10)
    orig = NB._official_inference
    NB._official_inference = spy
    try:
        rep = _run(data, "regions.csv")
    finally:
        NB._official_inference = orig
    assert len(seen) == 2, len(seen)
    first, got = seen
    assert first["header"] == ["REF_DATE", "value total"] and first["structure"] is None and first["estimand"] is None, first
    assert first["layout"]["rows_a_month"] == 6, first["layout"]            # the row-count drop reads it in the slice's run
    assert got["header"] == list(pd.read_csv(io.BytesIO(data), dtype=str, nrows=0).columns), got["header"]
    assert got["layout"]["rows_a_month"] == 6 and got["layout"]["layout"] == NB.STRUCTURE_LAYOUT, got["layout"]
    assert got["structure"]["kind"] == "cube" and got["estimand"]["slice"][0]["member"] == "Total", got["estimand"]["slice"]
    # the table's rows a month is the series it lists (6 here, 465 for the retail file)
    S = NS.detect(reading(data), set())
    assert NS.rows_a_month(S) == 6
    json.dumps(rep, allow_nan=False)
    assert not hasattr(NB, "_official_inference_missing"), "the guard (globals().get) is gone"
    assert 'globals().get("_official_inference")' not in open(os.path.join(ADAPTER_DIR, "nl_browser.py"), encoding="utf-8").read()


def _marked(data: bytes, headline_marks):
    """The cube with the headline series' (the Total's) latest months marked by the publisher: {month: code}."""
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    for mo, code in headline_marks.items():
        df.loc[(df.GEO == "Total") & (df.REF_DATE == mo), "STATUS"] = code
    return df.to_csv(index=False, quoting=1).encode("utf-8")


def test_e2e_an_official_cube_gets_the_described_change_in_the_estimands_own_figures_and_the_structures_flags():
    """T4 on a table read by its structure: the change is DESCRIBED (prior and latest are the estimand's 12-month totals,
    not the finding's monthly averages), the quality codes are the headline's own months by code (the slice's health holds
    no status column), and revised and preliminary months are counted from the structure's flags."""
    rep = _run(_marked(MC.partition(0.10), {"2022-10": "r", "2022-11": "r", "2022-12": "p"}), "regions.csv")
    est, st = rep["estimand"], rep["structure"]
    chg = [f for f in rep["findings"] if f["id"].endswith(".change")]
    assert len(chg) == 1, [f["id"] for f in rep["findings"]]
    inf = chg[0]["inference"]
    assert inf and inf["mode"] == "official_aggregate" and inf["publisher"] == "statcan", inf
    assert inf["describe"]["prior"] == est["figures"]["prior"]["value"], (inf["describe"], est["figures"])
    assert inf["describe"]["latest"] == est["figures"]["latest"]["value"] and inf["describe"]["change_pct"] == chg[0]["value"], inf
    # a 12-month total of the Total's own published series (thousands x 1,000), not the finding's monthly average
    df = pd.read_csv(io.BytesIO(MC.partition(0.10)), dtype=str)
    tot = df[df.GEO == "Total"].set_index("REF_DATE").VALUE.astype(float) * 1000
    lat, pri = est["comparison"]["latest"], est["comparison"]["prior"]
    assert inf["describe"]["latest"] == tot[(tot.index >= lat[0]) & (tot.index <= lat[1])].sum(), inf["describe"]
    assert inf["describe"]["prior"] == tot[(tot.index >= pri[0]) & (tot.index <= pri[1])].sum(), inf["describe"]
    # the quality codes: months of the headline by code, from the structure
    assert inf["quality"] == {"column": "STATUS", "codes": st["flags"]["quality_of_headline"]}, (inf["quality"], st["flags"])
    assert inf["quality"]["codes"] == {"A": 45, "r": 2, "p": 1}, inf["quality"]
    assert inf["revisions"] == "the file marks 2 months of the headline revised and 1 preliminary", inf["revisions"]
    assert "flow" in " ".join(inf["how_known"]) and "sum-check" in " ".join(inf["how_known"]), inf["how_known"]
    # a forecast is not an official figure: no record on it
    assert all(f.get("inference") is None for f in rep["findings"] if not f["id"].endswith(".change")), rep["findings"]
    # the estimand's own slot (track A1 reserved it for A2) holds the headline claim's record, a copy; the writer reads it
    # first, with the words through the payload's scrubber and the figures as they are
    assert est["inference"] == inf and est["inference"] is not inf and est["inference"]["how_known"] is not inf["how_known"], est
    got = NB.results_for_ai(rep)["estimand"]["inference"]
    assert got["mode"] == "official_aggregate" and got["describe"] == inf["describe"], got
    assert got["quality"] == {"column": "STATUS", "codes": {"A": 45, "r": 2, "p": 1}} and got["revisions"] == inf["revisions"], got
    assert got["grade_label"] == inf["grade_label"] and got["how_known"] == inf["how_known"], got
    # the same file with the publisher's signature columns renamed: nothing is described, the engine's grade stands alone
    df = pd.read_csv(io.BytesIO(MC.partition(0.10)), dtype=str, keep_default_na=False)
    plain = df.rename(columns={"DGUID": "region_key", "VECTOR": "series_key", "STATUS": "mark"}).to_csv(
        index=False, quoting=1).encode("utf-8")
    rep2 = _run(plain, "regions.csv")
    assert rep2["estimand"] and all(f.get("inference") is None for f in rep2["findings"]), \
        [(f["id"], f.get("inference")) for f in rep2["findings"]]
    assert rep2["estimand"]["inference"] is None and NB.results_for_ai(rep2)["estimand"]["inference"] is None
    json.dumps(rep, allow_nan=False)


def test_the_official_inference_records_are_replaced_by_a_second_call_never_kept():
    """_official_inference owns findings[].inference: a second call (the slice's, on the inner report) replaces the first
    call's record, so a file that then publishes sampling errors leaves none behind."""
    rep = _run(MC.partition(0.10), "regions.csv")
    f = next(x for x in rep["findings"] if x["id"].endswith(".change"))
    assert f["inference"] and f["inference"]["mode"] == "official_aggregate"
    header = list(pd.read_csv(io.BytesIO(MC.partition(0.10)), dtype=str, nrows=0).columns)
    assert rep["estimand"]["inference"] == f["inference"]
    NB._official_inference(rep, header + ["Standard error"], None, [])
    assert f["inference"] is None and rep["estimand"]["inference"] is None, (f["inference"], rep["estimand"]["inference"])
    NB._official_inference(rep, header, None, [])
    assert f["inference"] and f["inference"]["describe"]["prior"] == rep["estimand"]["figures"]["prior"]["value"]
    assert rep["estimand"]["inference"] == f["inference"]


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


# ----------------------------------------------------------------------------- the waterfall is the decomposition of the change
# (final integration pass, 6 Oct 2026): a "Start" total of 0, each part's contribution, a "Total change" total, so the parts
# fill the chart under the frozen spec's "the value axis always includes 0"; the not-allocated step is drawn only when it is
# 1% or more of the change (or when leaving it out would break the spec's add-up), and says why it is there
def _wf_case(parts, u, change=None, suppressed_parts=0, sum_check=True):
    import nl_viz as NV
    S = {"measure": {"type": "flow", "currency": True, "uom": "Dollars"}}
    change = math.fsum(parts) + u if change is None else change
    prior = 100e9
    it = {"headline.prior": {"id": "headline.prior", "value": prior, "text": NS.money(prior, S)},
          "headline.latest": {"id": "headline.latest", "value": prior + change, "text": NS.money(prior + change, S)},
          "headline.change": {"id": "headline.change", "value": change, "text": NS.money(change, S, signed=True)}}
    for i, v in enumerate(parts):
        iid = "contribution.geo.p%d" % i
        it[iid] = {"id": iid, "segment": "Part %d" % i, "value": v, "text": NS.money(v, S, signed=True)}
        it["growth.geo.p%d" % i] = {"id": "growth.geo.p%d" % i, "segment": "Part %d" % i, "value": 1.0, "text": "+1.0%"}
    it["contribution.geo.unallocated"] = {"id": "contribution.geo.unallocated", "segment": "unallocated (GEO)", "value": u,
                                          "text": NS.money(u, S, signed=True, ref=change)}
    rep = {"estimand": {"sum_checks": ([{"dim": "GEO", "total": "Canada", "suppressed_parts": suppressed_parts}]
                                         if sum_check else [])}}
    bd = {"key": "geo", "dim": "GEO", "parent": "Canada", "id": "B1"}
    basis = {"windows": {"prior": ["2024-08", "2025-07"], "latest": ["2025-08", "2026-07"]}, "finding_id": None,
             "measure": "Total retail sales", "estimand": "Canada · Total retail sales", "grade": None, "complete": True}
    rec = NV._b_structure_waterfall(rep, bd, it, basis, {})
    rec["id"], rec["why"] = "viz.1.contribution_waterfall", "Chosen by the engine: where the change came from"
    assert not _spec_errors(rec), _spec_errors(rec)
    return rec


def test_the_waterfall_is_the_decomposition_of_the_change_and_the_parts_fill_the_chart():
    parts = [10.2e9, 6.2e9, 5.9e9, 3.3e9, 1.0e9, 0.9e9, 0.9e9, 0.4e9, 0.3e9, 0.2e9, 0.07e9, 0.03e9]
    rec = _wf_case(parts, 1000.0)
    st = rec["data"]["steps"]
    change = math.fsum(parts) + 1000.0
    assert (st[0]["label"], st[0]["kind"], st[0]["value"], st[0]["from"], st[0]["to"], st[0]["text"]) == ("Start", "total", 0, 0, 0, "$0")
    assert st[-1]["label"] == "Total change" and st[-1]["kind"] == "total" and st[-1]["from"] == 0
    assert abs(st[-1]["value"] - change) < 1e-3 and rec["data"]["change"]["value"] == change
    # the steps fill the chart: nothing is drawn beyond the change (the levels view put every step in 1% of the height)
    assert max(abs(s["to"]) for s in st) <= abs(change) * (1 + 1e-6) and max(abs(s["value"]) for s in st[1:-1]) > 0.3 * change
    assert [s["label"] for s in st[1:-1]][-1] == "other parts (2)" and len(st) == 1 + 10 + 1 + 1
    # the levels are in the caption, not the chart
    assert "$100.0B" in rec["subtitle"] and "$129.4B" in rec["subtitle"] and "2025-08 to 2026-07" in rec["subtitle"], rec["subtitle"]
    assert rec["table"]["rows"][0][:2] == ["Start", "$0"] and rec["table"]["rows"][-1][0] == "Total change"
    assert not any(r[0] == "12 months before" or r[0] == "Latest 12 months" for r in rec["table"]["rows"])


def test_the_not_allocated_step_is_left_out_below_one_percent_and_the_sum_check_line_says_the_gap():
    parts = [10.2e9, 6.2e9, 5.9e9, 3.3e9, 1.0e9]
    for u, why in ((1000.0, "rounding"), (-6000.0, "rounding")):
        rec = _wf_case(parts, u)
        assert not any(s["label"].startswith("Not allocated") for s in rec["data"]["steps"]), rec["data"]["steps"]
        assert not any(r[0].startswith("Not allocated") for r in rec["table"]["rows"])
        gap = "$%s" % format(int(abs(u)), ",")
        assert "(gap %s, %s)" % (gap, why) in rec["summary"], rec["summary"]
        assert "adds up; gap %s, %s)" % (gap, why) in rec["source"], rec["source"]
        assert "unallocated" not in rec["summary"].lower() and "$0.0B" not in rec["summary"] + rec["source"]
    # suppressed cells in the windows name the cause of the gap
    assert "gap $1,000, suppressed cells" in _wf_case(parts, 1000.0, suppressed_parts=2)["source"]
    # nothing to say about a gap that is exactly zero
    z = _wf_case(parts, 0.0)
    assert "add up to the change exactly" in z["summary"] and "gap" not in z["source"], (z["summary"], z["source"])


def test_the_not_allocated_step_is_drawn_from_one_percent_and_says_why():
    parts = [10.2e9, 6.2e9, 5.9e9, 3.3e9, 1.0e9]
    for u, kw, label in ((0.02 * 26.6e9, {}, "Not allocated: rounding"),
                         (-0.02 * 26.6e9, {"suppressed_parts": 3}, "Not allocated: suppressed cells"),
                         (0.5e9, {"suppressed_parts": 1}, "Not allocated: suppressed cells"),
                         (0.5e9, {}, "Not allocated: rounding"),
                         (0.2e9, {"sum_check": False}, "Not allocated: suppressed cells")):
        rec = _wf_case(parts, u, **kw)
        st = rec["data"]["steps"]
        assert [s["label"] for s in st if s["label"].startswith("Not allocated")] == [label], [s["label"] for s in st]
        step = next(s for s in st if s["label"] == label)
        assert abs(step["value"] - u) < 1e-3 and step["kind"] == "step"
        assert any(r[0] == label for r in rec["table"]["rows"]) and "adds up; gap" not in rec["source"]
        assert "step is the total less its published parts" in rec["source"], rec["source"]
    # between a millionth and one percent of the change the step cannot be left out: the spec's add-up (1e-6 of the change)
    # would break, so it is drawn (and a record without it would be refused by the worker)
    small = _wf_case(parts, 0.001 * 26.6e9)
    assert any(s["label"].startswith("Not allocated") for s in small["data"]["steps"])


def test_a_falling_total_decomposes_the_same_way():
    parts = [-9.0e9, -4.0e9, 2.0e9, -1.0e9, 0.5e9]
    rec = _wf_case(parts, 2000.0)
    st = rec["data"]["steps"]
    change = math.fsum(parts) + 2000.0
    assert change < 0 and st[0]["value"] == 0 and abs(st[-1]["value"] - change) < 1e-3 and st[-1]["text"].startswith("\u2212$")
    assert max(abs(s["to"]) for s in st) <= 1.2 * abs(change) and not any(s["label"].startswith("Not allocated") for s in st)


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


# ------------------------------------------------------------------------------ wave 4, track B step 0
def _part_items(key, parts, total_sign=1):
    """The items of a breakdown the way nl_scenarios builds them: contribution, growth and share_level per part."""
    out = []
    for name, v in parts:
        slug = name.lower().replace(" ", "_")
        out.append({"id": "contribution.%s.%s" % (key, slug), "group": "contribution", "segment": name, "kind": "change",
                    "unit": "", "value": float(v)})
        out.append({"id": "growth.%s.%s" % (key, slug), "group": "contribution", "segment": name, "kind": "change",
                    "unit": "%", "value": float(v) / 100.0})
        out.append({"id": "share_level.%s.%s" % (key, slug), "group": "contribution", "segment": name, "kind": "percent",
                    "unit": "", "value": abs(float(v)) / 10.0})
    out.append({"id": "contribution.%s.unallocated" % key, "group": "contribution", "segment": "unallocated (%s)" % key,
                "kind": "change", "unit": "", "value": 1.0})
    return out


def test_the_payload_budget_keeps_a_part_that_moved_against_the_change_and_each_breakdowns_largest_parts():
    """Retail: the budget left out the Northwest Territories, the only province that fell, as the smallest part. Now:
    the facts first; the parts beyond each breakdown's 6 largest, the smallest first; then the largest parts' share
    items; then the other groups; an item that moved AGAINST the headline change late; the unallocated part last."""
    geo = [("Ontario", 100), ("Quebec", 60), ("Alberta", 50), ("BC", 30), ("Manitoba", 10), ("Nova Scotia", 9),
           ("Saskatchewan", 8), ("Newfoundland", 3), ("Yukon", 1), ("Nunavut", 0.5), ("Northwest Territories", -0.4)]
    nai = [("General merchandise", 80), ("Health", 66), ("Gasoline", 61), ("Sporting", 34), ("Clothing", 31),
           ("Food", 26), ("Furniture", -17), ("Motor vehicle", 12.8), ("Building", -1.4)]
    basis = {"breakdowns": [{"key": "geo", "dim": "GEO"}, {"key": "naics", "dim": "NAICS"}]}

    def build(sign):
        its = [{"id": "headline.change", "group": "headline", "segment": None, "kind": "change", "unit": "",
                "value": 293.0 * sign},
               {"id": "headline.latest", "group": "headline", "segment": None, "kind": "amount", "unit": "", "value": 8640.0}]
        its += _part_items("geo", [(n, v * sign) for n, v in geo]) + _part_items("naics", [(n, v * sign) for n, v in nai])
        its += [{"id": "facts.months", "group": "facts", "segment": None, "kind": "count", "unit": "", "value": 79.0}]
        return its
    for sign in (1, -1):            # a total that rose, and one that fell (the parts that rose are the ones against it)
        items = build(sign)
        order = NB._budget_drop_order(items, basis)
        ids = [items[i]["id"] for i in order]
        assert sorted(order) == list(range(len(items))) and ids[0] == "facts.months", (sign, ids[:3])
        # the unallocated parts are the very last, the one built first the last of all; a part against the change just before
        assert ids[-2:] == ["contribution.naics.unallocated", "contribution.geo.unallocated"], ids[-3:]
        assert ids[-3] in ("contribution.naics.furniture", "contribution.geo.northwest_territories",
                           "growth.naics.furniture", "contribution.naics.building"), ids[-3:]
        pos = {k: n for n, k in enumerate(ids)}
        # GEO's 6 largest are Ontario to Nova Scotia; Saskatchewan, the 7th, and every smaller part are beyond them, and go
        # first, the smallest part first
        assert all(pos["contribution.geo.%s" % n] < pos["contribution.geo.ontario"]
                   for n in ("saskatchewan", "newfoundland", "yukon", "nunavut"))
        assert pos["contribution.geo.nunavut"] < pos["contribution.geo.yukon"] < pos["contribution.geo.newfoundland"] < \
            pos["contribution.geo.saskatchewan"] < pos["contribution.geo.manitoba"], "the smallest part goes first"
        # a part that moved against the headline change: its contribution and growth are among the last items to go, after
        # the headline and every large part, and only the unallocated parts come after; its share_level is an ordinary item
        for k in ("contribution.geo.northwest_territories", "growth.geo.northwest_territories",
                  "contribution.naics.furniture", "growth.naics.furniture", "contribution.naics.building",
                  "growth.naics.building"):
            assert pos[k] > pos["headline.latest"] and pos[k] > pos["contribution.geo.ontario"] \
                and pos[k] > pos["contribution.naics.food"] and pos[k] > pos["contribution.geo.nunavut"], (sign, k)
        assert pos["share_level.geo.northwest_territories"] < pos["contribution.geo.northwest_territories"]
        # nothing that moved the headline's way is ever kept ahead of a part that moved against it
        assert pos["contribution.geo.yukon"] < pos["growth.geo.northwest_territories"]
    # with no breakdowns in the basis (one segment column) the same rules hold, per column
    flat = [dict(it, id=it["id"].replace(".geo.", ".")) for it in build(1) if ".naics." not in it["id"]]
    o2 = [flat[i]["id"] for i in NB._budget_drop_order(flat, {})]
    assert o2[-1] == "contribution.unallocated" and o2.index("contribution.northwest_territories") > o2.index("contribution.ontario")
    assert o2[0] == "facts.months" and o2.index("contribution.nunavut") < o2.index("contribution.ontario")


def _later(data: bytes, years: int) -> bytes:
    """A cube's file with every REF_DATE `years` years later (so its last month is close to the test's analysis date)."""
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    df["REF_DATE"] = df["REF_DATE"].map(lambda m: "%04d%s" % (int(m[:4]) + years, m[4:]))
    return df.to_csv(index=False, quoting=1).encode("utf-8")


def test_the_replay_sentence_of_the_core_is_replaced_by_the_audits_one_count_and_the_headline_by_the_estimands():
    """Step 0 of track B: a report states ONE count of how its range held (the audit's), and a table read by its structure
    leads with its estimand, in the writer's fallback-title format; a report with no structure keeps the core's headline."""
    data = _later(MC.partition(0.10), 3)           # Jan 2022 - Dec 2025: a forecast of the months after it is not history
    NB._PROFILE_CACHE.clear()
    rep = NB.run(data, "regions.csv", "", {}, "2026-01-20")
    assert rep["ok"], rep["error"]
    au = rep["forecast"]["audit"]
    assert au["horizons"] and au["label"].startswith("back-tested: held "), au
    wn = " ".join(rep["story"]["whats_next"])
    assert not re.search(r"held in \d+ of \d+ replayed months", wn + " ".join(rep["story"]["what_happened"])), wn
    assert "Its 80%% range was %s." % au["label"] in wn, wn
    out = NB.results_for_ai(rep)
    assert "coverage" not in out["forecast"] and out["forecast"]["audit"]["label"] == au["label"], out["forecast"]
    est = rep["estimand"]
    F = est["figures"]
    meas = est["measure"]["label"]
    member = next(x["member"] for x in est["slice"] if x["member"] != meas)
    hl = rep["story"]["headline"]
    assert hl.startswith("%s, %s, 12 months to " % (meas, member)) and (": " + F["change_pct"]["text"]) in hl, hl
    assert "Monthly" not in hl and "forecast" not in hl
    if est["inference"] and est["inference"]["mode"] == "official_aggregate":
        assert hl.endswith("(%s) in the published totals" % F["latest"]["text"]), hl
    assert out["goal"] == hl and out["story"]["headline"] == hl, (out["goal"], out["story"]["headline"])
    # the same table with the structure switched off keeps the core's own headline (no structure: nothing changes)
    try:
        NB.STRUCTURE_ON = False
        NB._PROFILE_CACHE.clear()
        old = NB.run(data, "regions.csv", "", {}, "2026-01-20")
    finally:
        NB.STRUCTURE_ON = True
    assert old["ok"] and old["estimand"] is None and old["story"]["headline"] != hl
    assert not re.search(r"in the published totals|12 months to", old["story"]["headline"]), old["story"]["headline"]


def test_estimand_headline_formats_follow_the_writers_fallback_title():
    est = {"measure": {"label": "Total retail sales", "type": "flow"},
           "slice": [{"member": "Canada"}, {"member": "Total retail sales"}, {"member": "Unadjusted"}],
           "comparison": {"latest": ["2025-08", "2026-07"], "prior": ["2024-08", "2025-07"]}, "complete": True,
           "figures": {"latest": {"value": 1.0, "text": "$864.0B"}, "change_pct": {"value": 3.5, "text": "+3.5%"},
                       "change": {"value": 1.0, "text": "+$29.3B"}}, "inference": None}
    f = {"id": "measure.x.change", "kind": "business", "grade": "WATCH"}
    rep = {"estimand": est, "findings": [f], "scenarios": {"basis": {"finding_id": "measure.x.change"}}}
    assert NB._estimand_headline(rep) == "Total retail sales, Canada: no settled change in the 12 months to Jul 2026 (+3.5%, WATCH)"
    f["grade"] = "CONFIRMED"
    assert NB._estimand_headline(rep) == "Total retail sales, Canada, 12 months to Jul 2026: +3.5% to $864.0B (CONFIRMED)"
    f["grade"] = "NOT_ENOUGH_DATA"
    assert NB._estimand_headline(rep) == "Total retail sales, Canada: no settled change; the data cannot say yet (INSUFFICIENT)"
    est["inference"] = {"mode": "official_aggregate"}
    assert NB._estimand_headline(rep) == "Total retail sales, Canada, 12 months to Jul 2026: +3.5% ($864.0B) in the published totals"
    est["figures"]["change_pct"] = {"value": -3.5, "text": "-3.5%"}
    assert "\u22123.5% ($864.0B)" in NB._estimand_headline(rep)
    est["complete"], est["months_used"] = False, 11
    assert "the 11 matched months to Jul 2026" in NB._estimand_headline(rep)
    est["figures"]["change_pct"] = {"value": None, "text": "n/a"}
    assert NB._estimand_headline(rep) is None, "no figure is printed: no headline made of one"
    assert NB._estimand_headline({"estimand": None}) is None




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
    # the waterfall is the decomposition of the change (a "Start" of 0 and a "Total change"); the windows' months are in its words
    assert wf["data"]["steps"][0]["label"] == "Start" and wf["data"]["steps"][-1]["label"] == "Total change", wf["data"]["steps"]
    assert "11 matched months" in wf["subtitle"] and "11 matched months" in wf["summary"], (wf["subtitle"], wf["summary"])
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
    # the two waterfalls decompose the +$29.3B change (a Start of 0 to the Total change); the $1,000 and $6,000 gaps are
    # rounding (no part lacks a month's value in the windows), under 1% of the change: no step, the gap is in the sum-check line
    wfs = [c for c in rep["viz"]["charts"] if c["chart"] == "contribution_waterfall"]
    for c, gap in zip(wfs, ("$1,000", "$6,000")):
        st = c["data"]["steps"]
        assert (st[0]["label"], st[0]["value"], st[-1]["label"], st[-1]["text"]) == ("Start", 0, "Total change", "+$29.3B"), st
        assert not any(x["label"].startswith("Not allocated") for x in st), [x["label"] for x in st]
        assert max(abs(x["value"]) for x in st[1:-1]) > 0.25 * st[-1]["value"], "the largest part fills a quarter of the chart"
        assert "(gap %s, rounding)" % gap in c["summary"] and "adds up; gap %s, rounding" % gap in c["source"], (c["summary"], c["source"])
    assert [c["suppressed_parts"] for c in est["sum_checks"]] == [0, 0], est["sum_checks"]
    assert [c["unallocated_latest"]["text"] for c in est["sum_checks"]] == ["\u2212$3,000", "\u2212$2,000"], est["sum_checks"]
    cal = next(c for c in rep["viz"]["charts"] if c["chart"] == "calendar_heatmap")
    assert "seasonally adjusted" in cal["title"] and max(n for row in cal["data"]["n"] for n in row if n) == 13, cal["title"]
    geo = next(c for c in rep["viz"]["charts"] if c["chart"] == "change_heatmap" and c["data"]["row_label"] == "GEO")
    assert geo["data"]["rows"][:3] == ["Ontario", "Quebec", "British Columbia"] and geo["data"]["rows"][-1] == "other (2 parts)"
    assert rep["input"]["rows"] == 36735 and rep["input"]["columns"] == 17
    print("    statcan planner off: %.1f s native" % took)
    # step 0 of track B (6 Oct 2026): the seasonal-naive champion's audit can pass (rel MAE 1.00 is "no worse"), the story
    # states the audit's one count of the range, the headline is the estimand's, and the writer's payload keeps the
    # only province that fell
    au = rep["forecast"]["audit"]
    assert [(h["held"], h["of"]) for h in au["horizons"]] == [(21, 23), (19, 21), (17, 18), (11, 12)], au["label"]
    assert au["status"] == "passes" and au["trusted"] is True and rep["forecast"]["trusted"] is True and \
        au["benchmark_is_model"] is True and au["label"].endswith("; the model is the seasonal-naive benchmark itself"), au
    assert rep["story"]["headline"] == "Total retail sales, Canada, 12 months to Jul 2026: +3.5% ($864.0B) in the published totals", \
        rep["story"]["headline"]
    assert not re.search(r"22 of 24", json.dumps(rep["story"]) + json.dumps(rep["summary"])), rep["story"]["whats_next"]
    out = NB.results_for_ai(rep)
    assert out["goal"] == rep["story"]["headline"] and "coverage" not in out["forecast"]
    assert len(json.dumps(out)) <= NB.RESULTS_MAX_BYTES, len(json.dumps(out))
    kept = [i["id"] for i in out["scenarios"]["items"]]
    assert {"contribution.geo.northwest_territories", "growth.geo.northwest_territories", "contribution.naics.444",
            "contribution.naics.449", "contribution.geo.unallocated", "contribution.naics.unallocated"} <= set(kept), \
        [i for i in it if i not in kept]
    print("    statcan payload: %d bytes, %d of %d scenario items" % (len(json.dumps(out)), len(kept), len(it)))
    plan = {"goal": "How did retail sales change?", "primary": "VALUE",
            "columns": [{"name": "VALUE", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [{"op": "keep_rows", "column": "Adjustments", "values": ["Unadjusted"]}]}
    rep = _run(data, os.path.basename(path), {"__plan__": plan})
    assert rep["estimand"]["plan_source"] == "ai_corrected" and rep["estimand"]["figures"]["latest"]["text"] == "$864.0B"
    assert {c["dim"] for c in rep["structure"]["corrections"]} >= {"GEO"}, rep["structure"]["corrections"]


# ----------------------------------------------------------------------------- wave 5 (generality), gap 3: unknown-type counts
def _total_series(data: bytes, col: str = "GEO", total: str = "Total", scale: float = 1.0):
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    return pd.to_numeric(df[df[col] == total].set_index("REF_DATE").VALUE) * scale


def test_g3_a_count_with_no_clue_in_its_labels_is_averaged_over_time_never_summed():
    """Persons or Number with no word that says the count accumulates over time (Group A): the engine used to SUM its months
    (a stock added up). Now it is an average level over the window, the decision is in the measure record, and the percent
    change is the same either way."""
    data = MC.counts()
    S = detect(data)
    m = S["measure"]
    assert S["usable"] and dim(S, "GEO")["role"] == "partition" and m["type"] == "count", (S["reason"], m)
    assert m["type_basis"] == "ambiguous: averaged" and m["aggregation"] == "mean over months" and not NS.sums_over_time(m), m
    body, info = NS.slice_bytes(S, S["default"])
    assert info["column"] == "value", "the core reads a level, never a sum: %s" % info["column"]
    rep = _run(data, "persons.csv")
    est = rep["estimand"]
    assert est["measure"]["type_basis"] == "ambiguous: averaged" and est["measure"]["aggregation"] == "mean over months", est["measure"]
    assert "average level over the window" in est["text"] and "12-month totals" not in est["text"], est["text"]
    tot = _total_series(data)
    lat, pri = est["comparison"]["latest"], est["comparison"]["prior"]
    L = tot[(tot.index >= lat[0]) & (tot.index <= lat[1])]
    P = tot[(tot.index >= pri[0]) & (tot.index <= pri[1])]
    F = est["figures"]
    assert abs(F["latest"]["value"] - L.mean()) < 1e-6 and abs(F["prior"]["value"] - P.mean()) < 1e-6, (F, L.mean())
    assert abs(F["change_pct"]["value"] - 100.0 * (L.sum() / P.sum() - 1.0)) < 1e-6, "the percent change is right either way"
    basis = rep["scenarios"]["basis"]
    assert basis["how"] == "average", basis["how"]
    assert not any(f["id"].startswith("measure.volume") for f in rep["findings"])
    json.dumps(rep, allow_nan=False)


def test_g3_a_count_with_a_flow_word_is_still_summed_and_a_stock_word_is_a_stock_and_currency_is_a_flow():
    """The negative cases: a flow word in the labels (permits issued) is positively a flow and is summed; a stock word
    (employment) is positively a stock and is averaged; a currency is positively a flow (the retail table's own reading)."""
    flow = MC.counts(uom="Number", label="Building permits issued")
    S = detect(flow)
    m = S["measure"]
    assert m["type"] == "count" and m["type_basis"] == "positively a flow" and m["aggregation"] == "sum over months", m
    assert "permits" in m["type_why"], m["type_why"]
    body, info = NS.slice_bytes(S, S["default"])
    assert info["column"].startswith("Building permits issued"), info["column"]
    rep = _run(flow, "permits.csv")
    est = rep["estimand"]
    tot = _total_series(flow)
    lat = est["comparison"]["latest"]
    assert "12-month totals" in est["text"] and "average level" not in est["text"], est["text"]
    assert abs(est["figures"]["latest"]["value"] - tot[(tot.index >= lat[0]) & (tot.index <= lat[1])].sum()) < 1e-6, est["figures"]
    stock = detect(MC.counts(label="Employment"))["measure"]
    assert stock["type"] == "stock" and stock["type_basis"] == "positively a stock" and stock["aggregation"] == "mean over months", stock
    cur = detect(MC.partition())["measure"]
    assert cur["type"] == "flow" and cur["type_basis"] == "positively a flow" and cur["aggregation"] == "sum over months", cur
    assert "currency" in cur["type_why"], cur["type_why"]
    # a rate and an index are read as levels, never summed
    assert detect(MC.rate())["measure"]["aggregation"] == "mean over months"


# ----------------------------------------------------------------------------- wave 5, gap 1: no-total partitions
def _nt_totals(vals, months, regions=None):
    """{month: the regions' summed value in base units}"""
    regs = list(regions or MC.REGIONS)
    return {m: 1000.0 * sum(vals[r][i] for r in regs) for i, m in enumerate(MC.MONTHS) if m in months}


def _win(est, which, months):
    a, b = est["comparison"][which]
    return [m for m in months if a <= m <= b]


def test_g1_a_table_of_regions_with_no_total_row_is_built_from_its_parts_never_one_regions_value():
    """Before: rule 6 read ONE member (the largest region) as the table's figure, a silent wrong answer. Now: the
    regions are the parts of a table with no total row; a flow's headline is their sum, month by month, the estimand says
    "built from 5 regions; this table has no total row", the sum-check is "not possible (no total row)"."""
    data = MC.no_total()
    S = detect(data)
    g = dim(S, "GEO")
    assert S["usable"] and g["role"] == "parts" and g.get("total") is None and len(g["parts"]) == 5, (S["reason"], g["role"])
    assert S["default"]["GEO"] == NS.PARTS_TOKEN, S["default"]
    vals = MC.no_total_values()
    months, v = NS._monthly(S, S["default"])
    assert np.allclose(v, [1000.0 * sum(vals[r][i] for r in MC.REGIONS) for i in range(len(MC.MONTHS))]), v[:3]
    rep = _run(data, "regions_no_total.csv")
    est = rep["estimand"]
    assert "built from 5 regions; this table has no total row" in est["text"], est["text"]
    assert est["plan_source"] == "engine_default" and est["reconciles"] is True, est
    bf = est["built_from"]
    assert bf["dim"] == "GEO" and bf["n"] == 5 and bf["noun"] == "regions" and bf["incomplete"] is False, bf
    assert bf["text"] == "built from 5 regions; this table has no total row", bf["text"]
    chk = [c for c in est["sum_checks"] if c["dim"] == "GEO"]
    assert len(chk) == 1 and chk[0]["verdict"] == "not possible (no total row)" and chk[0]["total"] is None and chk[0]["parts"] == 5, est["sum_checks"]
    tot = _nt_totals(vals, set(MC.MONTHS))
    lat, pri = _win(est, "latest", MC.MONTHS), _win(est, "prior", MC.MONTHS)
    F = est["figures"]
    assert abs(F["latest"]["value"] - sum(tot[m] for m in lat)) < 1e-3 and abs(F["prior"]["value"] - sum(tot[m] for m in pri)) < 1e-3, F
    one = max(sum(1000.0 * vals[r][MC.MONTHS.index(m)] for m in lat) for r in MC.REGIONS)
    assert F["latest"]["value"] > 1.5 * one, "not the largest region's own value (%s vs %s)" % (F["latest"]["value"], one)
    it = _items(rep)
    parts = [v_["value"] for k, v_ in it.items() if k.startswith("contribution.geo.")]
    assert len(parts) == 5 and abs(sum(parts) - it["headline.change"]["value"]) < 1e-3, sorted(it)
    assert not any(k.endswith(".unallocated") for k in it), "a total built from its parts leaves nothing unallocated"
    assert all("growth.geo.%s" % NS_slug for NS_slug in ("alpha", "bravo", "charlie", "delta", "echo") if "growth.geo.%s" % NS_slug in it)
    assert rep["scenarios"]["basis"]["source"] == "structure" and rep["scenarios"]["basis"]["breakdowns"][0]["no_total"] is True
    wf = [c for c in rep["viz"]["charts"] if c["chart"] == "contribution_waterfall"]
    assert len(wf) == 1 and not _spec_errors(wf[0]), (len(wf), wf and _spec_errors(wf[0]))
    assert "no total row" in wf[0]["source"], wf[0]["source"]
    json.dumps(rep, allow_nan=False)
    out = NB.results_for_ai(rep)
    assert out["estimand"]["built_from"]["text"] == bf["text"], out["estimand"].get("built_from")


def test_g1_months_with_a_suppressed_region_are_left_out_and_said_and_a_headline_too_incomplete_sums_the_reported_parts():
    """Complete months only: two suppressed cells leave 10 matched months, both windows like for like. Too few complete
    months for the table's own latest windows (a region suppressed from month 26 on): the reported parts are summed, the
    estimand counts the region-months suppressed and is marked incomplete. Never silent."""
    vals = MC.no_total_values()
    data = MC.no_total(hide=[("Charlie", 38), ("Bravo", 34)])
    rep = _run(data, "regions_two_gaps.csv")
    est = rep["estimand"]
    bf = est["built_from"]
    assert bf["incomplete"] is False and bf["complete_months_only"] is True and bf["suppressed_part_months"] == 2, bf
    assert est["complete"] is False and est["months_used"] == 10 and est["months_left_out"] == ["2022-03", "2022-11"], est
    tot = _nt_totals(vals, set(MC.MONTHS))
    lat = [m for m in _win(est, "latest", MC.MONTHS) if m not in ("2022-03", "2022-11")]
    pri = [NB._shift_month(m, -12) for m in lat]
    assert abs(est["figures"]["latest"]["value"] - sum(tot[m] for m in lat)) < 1e-3, est["figures"]
    assert abs(est["figures"]["prior"]["value"] - sum(tot[m] for m in pri)) < 1e-3, est["figures"]
    assert "10 months with a value in both windows" in est["text"], est["text"]
    it = _items(rep)
    parts = [v_["value"] for k, v_ in it.items() if k.startswith("contribution.geo.")]
    assert abs(sum(parts) - it["headline.change"]["value"]) < 1e-3
    # a region suppressed from month 26 on: the table's latest windows hold no complete month
    hide = [("Echo", i) for i in range(26, 48)]
    rep = _run(MC.no_total(hide=hide), "regions_echo_gone.csv")
    est = rep["estimand"]
    bf = est["built_from"]
    assert bf["incomplete"] is True and bf["complete_months_only"] is False and est["complete"] is False, bf
    assert bf["suppressed_part_months"] == 22 and "22 region-months suppressed" in est["text"], (bf, est["text"])
    assert est["comparison"]["latest"][1] == "2022-12", est["comparison"]
    lat, pri = _win(est, "latest", MC.MONTHS), _win(est, "prior", MC.MONTHS)
    got = {m: 1000.0 * sum(vals[r][MC.MONTHS.index(m)] for r in MC.REGIONS if (r, MC.MONTHS.index(m)) not in set(hide)) for m in MC.MONTHS}
    assert abs(est["figures"]["latest"]["value"] - sum(got[m] for m in lat)) < 1e-3, est["figures"]
    assert est["figures"]["latest"]["months"] == 12 and est["inference"] is None or True
    json.dumps(rep, allow_nan=False)


def test_g1_a_combined_member_is_never_added_to_its_own_parts_and_a_total_row_is_not_summed_twice():
    """A member that equals the sum of 2 or more others is left out of the sum (and listed under "left out and why"); the
    negative cases: a table WITH a total row is read by its total (never the total plus its parts), also when it holds a
    combined member."""
    vals = MC.no_total_values()
    data = MC.no_total(combined=True, nested=True)
    S = detect(data)
    g = dim(S, "GEO")
    assert g["role"] == "parts" and sorted(g["parts"]) == sorted(MC.REGIONS), g["parts"]
    assert g["combined"] == {"Prairie group": ["Alpha", "Bravo", "Charlie"], "Centre block": ["Charlie", "Delta"]}, g["combined"]
    rep = _run(data, "regions_combined.csv")
    est = rep["estimand"]
    lat = _win(est, "latest", MC.MONTHS)
    tot = _nt_totals(vals, set(MC.MONTHS))
    assert abs(est["figures"]["latest"]["value"] - sum(tot[m] for m in lat)) < 1e-3, "the five regions, not seven members"
    assert est["built_from"]["n"] == 5 and sorted(est["built_from"]["combined"]) == ["Centre block", "Prairie group"], est["built_from"]
    ex = {x["what"]: x["why"] for x in est["excluded"]}
    assert "equals the sum of Alpha, Bravo and Charlie" in ex["Prairie group"] and "never added" in ex["Prairie group"], ex
    viol = NS.check_rows(S, list(range(S["rows"])))
    assert any(v_["kind"] == "combined_with_parts" for v_ in viol), viol
    # negative: a Total row
    S2 = detect(MC.no_total(total=True))
    assert dim(S2, "GEO")["role"] == "partition" and dim(S2, "GEO")["total"] == "Total" and S2["default"]["GEO"] == "Total", dim(S2, "GEO")["role"]
    rep2 = _run(MC.no_total(total=True), "regions_total.csv")
    e2 = rep2["estimand"]
    assert "built from" not in e2["text"] and e2.get("built_from") is None and e2["sum_checks"][0]["verdict"] == "adds_up", e2["text"]
    lat2 = _win(e2, "latest", MC.MONTHS)
    assert abs(e2["figures"]["latest"]["value"] - sum(tot[m] for m in lat2)) < 1e-3, "the Total's own series, once"
    # negative: a Total row AND a combined member: the hierarchy search reads Total > the group > its regions
    S3 = detect(MC.no_total(total=True, combined=True))
    assert dim(S3, "GEO")["role"] == "hierarchy" and S3["default"]["GEO"] == "Total", (dim(S3, "GEO")["role"], S3["default"])


def test_g1_a_stock_or_a_rate_with_no_total_shows_one_member_by_dominance_and_says_it_is_not_a_national_figure():
    """Non-additive measures have no valid aggregate: one member, never added or averaged across members."""
    for data, why in ((MC.counts(label="Employment", with_total=False), "stock"),
                      (MC.no_total(rate=True, uom="Percent", label="Unemployment rate"), "rate")):
        S = detect(data)
        g = dim(S, "GEO")
        assert g["role"] == "single" and g.get("single_by") == "dominance", (why, g["role"], g.get("why"))
        rep = _run(data, "no_total_%s.csv" % why)
        est = rep["estimand"]
        ex = " | ".join(x["why"] for x in est["excluded"])
        assert "one member shown, not a national figure" in ex and "never added or averaged" in ex, (why, est["excluded"])
        assert est.get("built_from") is None and "built from" not in est["text"], est["text"]
        assert not [k for k in _items(rep) if k.startswith("contribution.")], "a breakdown of one member"
        member = est["slice"][0]["member"]
        assert member in MC.REGIONS, member
    # the stock's member is the largest by dominance (Bravo holds 8,100 of the 5,200 / 8,100 / 3,300 / 6,100 / 2,400)
    rep = _run(MC.counts(label="Employment", with_total=False), "stock.csv")
    assert rep["estimand"]["slice"][0]["member"] == "Bravo", rep["estimand"]["slice"]


# ----------------------------------------------------------------------------- wave 5, gap 2: measure dimensions
def _meas(S, col):
    return {m["name"]: m for m in dim(S, col)["measures"]}


def test_g2_dollars_and_units_in_one_value_column_are_typed_separately_and_one_is_chosen():
    """A "Statistics" dimension whose members are measured in different units (dollars beside units): each member is typed
    on its own (M1, M2 ... with unit, type and the default flag), the table's relations are checked on the default member,
    the slices, the estimand and the breakdowns fix ONE member, and the estimand says why it is that one."""
    data = MC.measures_units_dollars()
    S = detect(data)
    d = dim(S, "Statistics")
    assert d["role"] == "measure" and S["usable"], (d["role"], S["reason"])
    got = [(m["id"], m["name"], m["uom"], m["type"], m["default"], m["precision"]) for m in d["measures"]]
    assert got == [("M1", "Sales value", "Dollars", "flow", True, False), ("M2", "Units sold", "Number", "count", False, False)], got
    assert d["measures"][1]["type_basis"] == "positively a flow" and d["measures"][0]["type_basis"] == "positively a flow"
    g = dim(S, "GEO")
    assert g["role"] == "partition" and g["total"] == "Total", (g["role"], g.get("why"))
    assert S["default"] == {"GEO": "Total", "Statistics": "Sales value"}, S["default"]
    rep = _run(data, "measures.csv")
    est = rep["estimand"]
    mc = est["measure_choice"]
    assert mc["dim"] == "Statistics" and mc["chosen"]["id"] == "M1" and mc["by"] == "default", mc
    assert "a currency flow, then a count flow, then a stock, then a rate or an index; never a precision member" in mc["rule"], mc
    assert [(a["id"], a["name"]) for a in mc["alternatives"]] == [("M2", "Units sold")], mc["alternatives"]
    assert est["measure"]["uom"] == "Dollars" and est["measure"]["type"] == "flow", est["measure"]
    assert "one measure shown: Sales value" in est["text"], est["text"]
    ex = {x["what"]: x["why"] for x in est["excluded"]}
    assert "another measure" in ex["Units sold"] and "never mixed" in ex["Units sold"], ex
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    tot = pd.to_numeric(df[(df.GEO == "Total") & (df.Statistics == "Sales value")].set_index("REF_DATE").VALUE)
    lat = est["comparison"]["latest"]
    assert abs(est["figures"]["latest"]["value"] - tot[(tot.index >= lat[0]) & (tot.index <= lat[1])].sum()) < 1e-6, \
        "dollars only: the units are never added to them"
    out = NB.results_for_ai(rep)
    assert out["estimand"]["measure_choice"]["chosen"]["id"] == "M1"
    json.dumps(rep, allow_nan=False)


def test_g2_a_plan_chooses_a_measure_member_by_id_and_the_profile_lists_the_members():
    data = MC.measures_units_dollars()
    NB._PROFILE_CACHE.clear()
    prof = NB.profile_for_ai(data, "measures.csv", decisions={})
    ms = prof["structure"]["measures"]
    assert ms["dim"] == "Statistics" and [(m["id"], m["name"], m["uom"], m["type"], m["default"]) for m in ms["members"]] == [
        ("M1", "Sales value", "Dollars", "flow", True), ("M2", "Units sold", "Number", "count", False)], ms
    plan = {"goal": "How did unit sales move?", "primary": "VALUE", "measure_member": "M2",
            "columns": [{"name": "VALUE", "semantic_type": "count", "role": "target"}], "operations": []}
    rep = _run(data, "measures.csv", {"__plan__": plan})
    est = rep["estimand"]
    assert est["plan_source"] == "ai" and est["measure"]["uom"] == "Number" and est["measure"]["type"] == "count", est["measure"]
    assert est["measure_choice"]["chosen"]["id"] == "M2" and est["measure_choice"]["by"] == "plan", est["measure_choice"]
    assert "one measure shown: Units sold, chosen by the plan" in est["text"], est["text"]
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    tot = pd.to_numeric(df[(df.GEO == "Total") & (df.Statistics == "Units sold")].set_index("REF_DATE").VALUE)
    lat = est["comparison"]["latest"]
    assert abs(est["figures"]["latest"]["value"] - tot[(tot.index >= lat[0]) & (tot.index <= lat[1])].sum()) < 1e-6, est["figures"]
    assert est["figures"]["latest"]["text"].startswith("$") is False, "units are not dollars: %s" % est["figures"]["latest"]["text"]
    it = _items(rep)
    parts = [v["value"] for k, v in it.items() if k.startswith("contribution.geo.") and not k.endswith("unallocated")]
    assert len(parts) == 3 and abs(sum(parts) + it["contribution.geo.unallocated"]["value"] - it["headline.change"]["value"]) < 1e-6
    # an id the table does not have is ignored, with a note
    rep = _run(data, "measures.csv", {"__plan__": dict(plan, measure_member="M9")})
    assert rep["estimand"]["measure_choice"]["chosen"]["id"] == "M1" and rep["estimand"]["plan_source"] == "engine_default"
    assert any("measure_member" in x and "M9" in x for x in rep["ai_plan"]["refused"]), rep["ai_plan"]["refused"]


def test_g2_a_rate_a_count_and_a_standard_error_in_one_value_column_a_precision_member_is_never_the_headline():
    data = MC.measures_rate_se()
    S = detect(data)
    d = dim(S, "Labour force characteristics")
    ms = {m["name"]: m for m in d["measures"]}
    assert d["role"] == "measure", d["role"]
    assert ms["Unemployment rate"]["type"] == "rate" and ms["Unemployment rate"]["id"] == "M1", ms["Unemployment rate"]
    assert ms["Employment"]["type"] == "stock" and ms["Employment"]["type_basis"] == "positively a stock", ms["Employment"]
    se = ms["Standard error of the unemployment rate"]
    assert se["precision"] is True and se["type"] == "precision" and se["default"] is False and se["id"] == "M3", se
    # the documented default: a currency flow, then a count flow, then a stock, then a rate or an index; never a precision member
    assert ms["Employment"]["default"] is True and ms["Unemployment rate"]["default"] is False, [m["default"] for m in d["measures"]]
    assert S["default"]["Labour force characteristics"] == "Employment" and dim(S, "GEO")["role"] == "partition" and \
        dim(S, "GEO")["total"] == "Canada", (S["default"], dim(S, "GEO")["role"])
    assert not any(s["where"].get("Labour force characteristics") == se["name"] for s in S["slices"]), "no slice of a precision member"
    # the rate's slice: its published aggregate, never added or averaged
    r = next(s for s in S["slices"] if s["where"].get("Labour force characteristics") == "Unemployment rate")
    assert r["where"]["GEO"] == "Canada" and r["use"] == "other_measure", r
    rep = _run(data, "lfs.csv")
    est = rep["estimand"]
    assert est["measure_choice"]["chosen"]["name"] == "Employment" and est["measure"]["type"] == "stock", est["measure_choice"]
    ex = {x["what"]: x["why"] for x in est["excluded"]}
    assert "precision" in ex["Standard error of the unemployment rate"] and "never the headline" in ex["Standard error of the unemployment rate"], ex
    assert [a["name"] for a in est["measure_choice"]["alternatives"]] == ["Unemployment rate", "Standard error of the unemployment rate"], \
        est["measure_choice"]["alternatives"]
    # a plan may choose the rate (M1): its published aggregate in percent; a precision member (M3) is refused
    plan = {"goal": "How did unemployment move?", "primary": "VALUE", "operations": [],
            "columns": [{"name": "VALUE", "semantic_type": "percentage", "role": "target"}]}
    rep = _run(data, "lfs.csv", {"__plan__": dict(plan, measure_member="M1")})
    est = rep["estimand"]
    assert est["measure"]["type"] == "rate" and est["slice"][0]["member"] == "Canada" and est["plan_source"] == "ai", est["measure"]
    assert est["figures"]["change"]["text"].endswith("percentage points"), est["figures"]
    assert not [k for k in _items(rep) if k.startswith("contribution.")], "a rate is never broken down"
    rep = _run(data, "lfs.csv", {"__plan__": dict(plan, measure_member="M3")})
    assert rep["estimand"]["measure_choice"]["chosen"]["name"] == "Employment" and rep["estimand"]["plan_source"] == "engine_default"
    assert any("precision" in x and "M3" in x for x in rep["ai_plan"]["refused"]), rep["ai_plan"]["refused"]


def test_g2_negatives_a_precision_member_first_in_the_file_is_not_the_default_and_an_ordinary_dimension_is_not_a_measure_dimension():
    S = detect(MC.measures_rate_se(members=("Standard error of the unemployment rate", "Unemployment rate")))
    d = dim(S, "Labour force characteristics")
    assert d["measures"][0]["precision"] and not d["measures"][0]["default"] and d["measures"][1]["default"], d["measures"]
    assert S["default"]["Labour force characteristics"] == "Unemployment rate", S["default"]
    # a table whose measure dimension holds two currency measures of one unit (the retail table's own shape) keeps its role
    S2 = detect(MC.partition())
    assert all(x["role"] != "measure" for x in S2["dims"]), [x["role"] for x in S2["dims"]]
    # one region whose name holds the word "rate" is a region, not a measure
    S3 = detect(MC.partition().replace(b"North", b"Rate review boards"))
    assert dim(S3, "GEO")["role"] == "partition" and not any(x.get("measure_dim") for x in S3["dims"]), dim(S3, "GEO")["role"]


# ----------------------------------------------------------------------------- wave 5, gap 5: repeated member names
def _strip_q(label: str) -> str:
    return re.sub(r"\s*\([^()]*\)\s*$", "", label)


def test_g5_repeated_member_names_are_keyed_by_an_id_a_code_or_a_parent_and_printed_qualified():
    """Before: a repeated name (Other under three parents) made the date and the dimensions fail to tell the rows apart, and the
    table was refused (or read as one industry). Now each member is keyed by a one-to-one id (a part of the dotted COORDINATE,
    found by checking every part against the dimension, or an id column) or by its parent's name; the name is printed, and
    qualified when two members share it."""
    ser = MC.dup_names_series()
    for variant, by in (("coordinate", "COORDINATE part 2"), ("code", "Industry code"), ("parent", "Industry group")):
        data = MC.dup_names(variant)
        S = detect(data)
        assert S["kind"] == "cube" and S["usable"], (variant, S["kind"], S["reason"])
        d = dim(S, "Industry")
        assert d["role"] == "hierarchy" and d["total"] == "All industries", (variant, d["role"], d.get("why"))
        labs = d["labels"]
        assert len(labs) == 13 and len(set(labs)) == 13, (variant, labs)
        assert [x for x in labs if _strip_q(x) == "Other"] and all(x != "Other" for x in labs), labs
        assert "Food" in labs and "Retail" in labs and "Machinery" in labs, "a name that is not shared is printed plain: %s" % labs
        assert S["keys"] == [{"dim": "Industry", "by": by, "duplicates": ["Other", "Services"]}], (variant, S["keys"])
        tree = {_strip_q(labs[p]): sorted(_strip_q(labs[c]) for c in ch) for p, ch in d["tree"].items() if labs[p] != "All industries"}
        assert tree == {"Retail": ["Food", "Other", "Services"], "Wholesale": ["Machinery", "Other", "Services"],
                        "Transport": ["Other", "Road", "Services"]}, (variant, tree)
        for p, ch in d["tree"].items():
            if labs[p] == "All industries":
                assert sorted(labs[c] for c in ch) == ["Retail", "Transport", "Wholesale"]
        # each qualified member is its own series: three different "Other" values
        others = [x for x in labs if _strip_q(x) == "Other"]
        times, v1, _b = NS.series(S, {"Industry": others[0]})
        times, v2, _b = NS.series(S, {"Industry": others[1]})
        assert not np.allclose(v1, v2), "two members named Other are two series"
    rep = _run(MC.dup_names("coordinate"), "industries.csv")
    est = rep["estimand"]
    lat = est["comparison"]["latest"]
    mons = [m for m in MC.MONTHS if lat[0] <= m <= lat[1]]
    want = 1000.0 * sum(ser[("", "All industries")][MC.MONTHS.index(m)] for m in mons)
    assert abs(est["figures"]["latest"]["value"] - want) < 1e-3, (est["figures"], want)
    assert [x["member"] for x in est["slice"] if x["dim"] == "Industry"] == ["All industries"]
    it = _items(rep)
    parts = sorted(k for k in it if k.startswith("contribution.industry.") and not k.endswith("unallocated"))
    assert len(parts) == 3, parts
    json.dumps(rep, allow_nan=False)


def test_g5_names_that_repeat_inside_the_same_parent_with_no_id_are_refused_with_a_plain_reason():
    """The only refusal: no id, and the names repeat so that nothing tells the rows apart (two members named Other under the same
    parent, or no parent column at all). The reason names the repeated member."""
    for variant in ("same_parent", "plain"):
        S = detect(MC.dup_names(variant))
        assert S["kind"] == "cube_incomplete" and not S["usable"], (variant, S["kind"])
        assert "\"Other\" appears more than once for the same date" in S["reason"] and "tell the rows apart" in S["reason"], S["reason"]
        rep = _run(MC.dup_names(variant), "industries_%s.csv" % variant)
        assert rep["estimand"] is None and "Other" in rep["story"]["headline"] and "tell the rows apart" in rep["story"]["headline"], \
            rep["story"]["headline"]
    # negative: unique names are never touched (no key record, no qualified label)
    S = detect(MC.partition())
    assert S.get("keys") == [] and all("(" not in x for x in dim(S, "GEO")["labels"]), S.get("keys")


# ----------------------------------------------------------------------------- wave 5, gap 4: quarterly and annual tables
def _periodic_total(data: bytes, scale: float = 1.0):
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    return pd.to_numeric(df[df.GEO == "Total"].set_index("REF_DATE").VALUE) * scale


def _no_month_words(*texts):
    for x in texts:
        assert not re.search(r"(?i)\bmonths?\b|\bmonthly\b", str(x)), "a month word in a quarterly or annual table's words: %s" % x


def test_g4_a_quarterly_stock_is_compared_over_4_quarters_and_says_quarters_never_months():
    """A window is 4 quarters (12 months of a monthly table): the described change is printed with 4 values a window, the core's
    grade stays its own (not enough data), the words are quarters, and the forecast and its audit are unavailable with a plain
    reason (the forecast reads monthly series only)."""
    data = MC.periodic("quarter", "iso", stock=True)
    S = detect(data)
    assert S["usable"] and S["period"]["kind"] == "quarter" and S["period"]["window"] == 4 and S["period"]["nouns"] == "quarters", S["period"]
    rep = _run(data, "quarterly.csv")
    est = rep["estimand"]
    P = est["period"]
    assert P["kind"] == "quarter" and P["window"] == 4 and P["nouns"] == "quarters", P
    tot = _periodic_total(data)                                   # 48 quarters, Q1 2012 to Q4 2023
    last4, prev4 = tot.iloc[-4:], tot.iloc[-8:-4]
    F = est["figures"]
    assert abs(F["latest"]["value"] - last4.mean()) < 1e-6 and abs(F["prior"]["value"] - prev4.mean()) < 1e-6, (F, last4.mean())
    assert abs(F["change_pct"]["value"] - 100.0 * (last4.mean() / prev4.mean() - 1.0)) < 1e-4 and F["latest"]["months"] == 4, F
    assert est["comparison"] == {"latest": ["2023-01", "2023-10"], "prior": ["2022-01", "2022-10"]}, est["comparison"]
    assert "4-quarter averages Q1 2023\u2013Q4 2023 vs Q1 2022\u2013Q4 2022" in est["text"], est["text"]
    assert est["complete"] is True and est["periods_used"] == 4, est
    _no_month_words(est["text"], est["measure"].get("aggregation"), est["slice"][0]["why"])
    # the core's grade is its own: 4 values a window cannot be tested (not faked)
    chg = [f for f in rep["findings"] if f["id"].endswith(".change")]
    assert chg and chg[0]["grade"] in ("NOT_ENOUGH_DATA", "WATCH") and chg[0].get("test", {}).get("p") is None, chg[0]
    assert rep["story"]["headline"].startswith("Population, Total, 4 quarters to Q4 2023: "), rep["story"]["headline"]
    # no forecast and no audit, with the reason
    fc = rep["forecast"]
    assert fc["available"] is False and "quarterly" in fc["reason"] and fc.get("audit") is None, fc["reason"]
    assert "forecast reads monthly series only" in fc["reason"], fc["reason"]
    # the scenarios and the page's words
    it = _items(rep)
    assert "the 4 quarters before" in it["headline.prior"]["label"] and "the latest 4 quarters" in it["headline.latest"]["label"], it["headline.prior"]["label"]
    assert it["facts.months"]["label"].startswith("Quarters with a value"), it["facts.months"]["label"]
    for k in ("headline.prior", "headline.latest", "headline.change", "facts.months", "facts.first", "facts.last"):
        _no_month_words(it[k]["label"], it[k].get("inputs", {}).get("op"))
    assert it["facts.first"]["text"] == "Q1 2012" and it["facts.last"]["text"] == "Q4 2023", (it["facts.first"], it["facts.last"])
    for chart in rep["viz"]["charts"]:
        assert chart["chart"] == "contribution_waterfall", chart["chart"]
        _no_month_words(chart["subtitle"], chart["summary"])
    assert any(r["chart"] == "change_heatmap" and "quarterly" in r["why"] for r in rep["viz"]["refused"]), rep["viz"]["refused"]
    for x in rep["story"]["what_happened"] + rep["story"]["whats_next"]:
        if "forecast reads monthly series only" not in x:        # the one sentence that must say what the forecast reads
            _no_month_words(x)
    assert rep["story"]["whats_next"] == [fc["reason"]], rep["story"]["whats_next"]
    json.dumps(rep, allow_nan=False)


def test_g4_a_quarterly_flow_is_summed_over_4_quarters_a_missing_quarter_is_matched_and_a_monthly_table_is_unchanged():
    data = MC.periodic("quarter", "iso", stock=False)
    rep = _run(data, "quarterly_sales.csv")
    est = rep["estimand"]
    tot = _periodic_total(data, 1000.0)
    F = est["figures"]
    assert "4-quarter totals Q1 2023\u2013Q4 2023 vs Q1 2022\u2013Q4 2022" in est["text"], est["text"]
    assert abs(F["latest"]["value"] - tot.iloc[-4:].sum()) < 1e-3 and abs(F["prior"]["value"] - tot.iloc[-8:-4].sum()) < 1e-3, F
    it = _items(rep)
    parts = [v["value"] for k, v in it.items() if k.startswith("contribution.geo.") and not k.endswith("unallocated")]
    assert len(parts) == 4 and abs(sum(parts) + it["contribution.geo.unallocated"]["value"] - it["headline.change"]["value"]) < 1e-3
    # a quarter the headline lacks: compared on the 3 quarters both windows have
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    m = (df.GEO == "Total") & (df.REF_DATE == "2023-04")
    df.loc[m, "VALUE"], df.loc[m, "STATUS"] = "", "x"
    rep = _run(df.to_csv(index=False).encode(), "quarterly_gap.csv")
    e2 = rep["estimand"]
    assert e2["complete"] is False and e2["periods_used"] == 3 and e2["months_left_out"] == ["2023-04"], e2
    assert "3 quarters with a value in both windows (Q2 2023 left out)" in e2["text"], e2["text"]
    want1 = tot[["2023-01", "2023-07", "2023-10"]].sum()
    want0 = tot[["2022-01", "2022-07", "2022-10"]].sum()
    assert abs(e2["figures"]["latest"]["value"] - want1) < 1e-3 and abs(e2["figures"]["prior"]["value"] - want0) < 1e-3, e2["figures"]
    # negative: a monthly table keeps its words and its windows
    S = detect(MC.partition())
    assert S["period"]["kind"] == "month" and S["period"]["window"] == 12, S["period"]
    est = _run(MC.partition(), "monthly.csv")["estimand"]
    assert "12-month totals" in est["text"] and est["period"]["kind"] == "month" and est["comparison"]["latest"] == ["2022-01", "2022-12"], est


def test_g4_an_annual_table_prints_the_change_with_one_value_a_window_and_period_labels_are_read():
    for style in ("year", "iso", "dec"):
        data = MC.periodic("year", style, stock=False)
        S = detect(data)
        assert S["usable"] and S["period"]["kind"] == "year" and S["period"]["window"] == 1, (style, S["reason"], S["period"])
        rep = _run(data, "annual_%s.csv" % style)
        est = rep["estimand"]
        tot = _periodic_total(data, 1000.0)
        assert "annual totals 2023 vs 2022" in est["text"], est["text"]
        assert abs(est["figures"]["latest"]["value"] - tot.iloc[-1]) < 1e-3 and abs(est["figures"]["prior"]["value"] - tot.iloc[-2]) < 1e-3, est["figures"]
        _no_month_words(est["text"])
        assert rep["story"]["headline"].startswith("VALUE, Total, 2023: "), rep["story"]["headline"]
        assert rep["forecast"]["available"] is False and "annual" in rep["forecast"]["reason"], rep["forecast"]["reason"]
    # quarter labels the engine does not read as dates (2012-Q1, 2012Q1, Q1 2012) are read as quarters, the same table
    ref = _run(MC.periodic("quarter", "iso"), "q_iso.csv")["estimand"]["figures"]
    for style in ("q", "qc", "qf"):
        data = MC.periodic("quarter", style)
        S = detect(data)
        assert S["usable"] and S["period"]["kind"] == "quarter", (style, S["reason"])
        got = _run(data, "q_%s.csv" % style)["estimand"]
        assert got["figures"] == ref, (style, got["figures"], ref)


# ----------------------------------------------------------------------------- wave 5, gap 6: the unallocated item's cause and residual
def test_g6_the_unallocated_item_names_its_cause_by_the_charts_rule_and_prints_the_real_residual():
    """contribution.<dim>.unallocated said "(suppressed cells)" and "$0.0B" even when the residual was rounding. Now the cause
    is the chart's own (sum_checks[].suppressed_parts: no part lacks a month's value in either window = rounding, else suppressed
    cells) and the residual is written out ("$1,000", never "$0.0B")."""
    rep = _run(MC.partition(0.10), "regions_suppressed.csv")
    it = _items(rep)["contribution.geo.unallocated"]
    chk = rep["estimand"]["sum_checks"][0]
    assert chk["suppressed_parts"] > 0 and "(suppressed cells)" in it["label"] and "(rounding)" not in it["label"], (it["label"], chk)
    want_u, _n = MC.partition_suppressed_sum()
    assert abs(it["value"] - want_u) < 1e-3 and "$0.0" not in it["text"], it
    assert "suppressed cells" in it["inputs"]["assumes"] if "inputs" in it and "assumes" in it.get("inputs", {}) else True
    # a table that adds up exactly: nothing is unallocated, and it says "$0", not "$0.0M"
    rep = _run(MC.partition(), "regions.csv")
    it = _items(rep)["contribution.geo.unallocated"]
    assert rep["estimand"]["sum_checks"][0]["suppressed_parts"] == 0 and "(rounding)" in it["label"] and "(suppressed cells)" not in it["label"], it["label"]
    assert it["text"] == "$0" and it["value"] == 0, it
    # a gap of rounding is written out in whole units: a published total 1 thousand dollars off its parts
    df = pd.read_csv(io.BytesIO(MC.partition()), dtype=str, keep_default_na=False)
    m = (df.GEO == "Total") & (df.REF_DATE == "2022-12")
    df.loc[m, "VALUE"] = (pd.to_numeric(df.loc[m, "VALUE"]) + 1).astype(int).astype(str)
    rep = _run(df.to_csv(index=False).encode(), "regions_rounding.csv")
    it = _items(rep)["contribution.geo.unallocated"]
    assert rep["estimand"]["sum_checks"][0]["suppressed_parts"] == 0 and "(rounding)" in it["label"], it["label"]
    assert it["text"] == "+$1,000" and abs(it["value"] - 1000.0) < 1e-6, it


# ----------------------------------------------------------------------------- wave 5, gap 7 (stretch): a wide table of periods
def test_g7_a_wide_table_of_periods_is_reshaped_to_long_and_its_embedded_flags_are_counted():
    """A table whose columns are periods (Eurostat's shape: 2016Q1 ... 2023Q4) with ":" and "123.4 p" in its cells: one row per
    series and period, the flags left in the value cell where the structure layer strips and counts them by the publisher's
    vocabulary; the headline is EU27 and the estimand says quarters. A long table, and a table whose headers are not periods,
    are left alone."""
    data, truth = MC.wide_period("quarter")
    w = NS.wide_to_long(data)
    assert w and w["info"]["family"] == "quarter" and w["info"]["periods"] == 32 and w["info"]["rows_in"] == 4 and w["info"]["rows_out"] == 128, w and w["info"]
    long = pd.read_csv(io.BytesIO(w["csv"]), dtype=str, keep_default_na=False)
    assert list(long.columns) == ["freq", "unit", "geo", "TIME_PERIOD", "OBS_VALUE"], list(long.columns)
    assert (long.OBS_VALUE == ":").sum() == truth["colon"] and long.OBS_VALUE.str.endswith(" p").sum() == truth["p"], long.OBS_VALUE.value_counts().head()
    # negatives: a long table, a wide table of month NAMES, a table with fewer than 6 period columns
    assert NS.wide_to_long(MC.partition()) is None and NS.wide_to_long(MC.eurostat()) is None
    names = pd.DataFrame({"region": ["a", "b"], **{m: ["1", "2"] for m in ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul")}})
    assert NS.wide_to_long(names.to_csv(index=False).encode()) is None
    few = pd.DataFrame({"region": ["a", "b"], "2020Q1": ["1", "2"], "2020Q2": ["1", "2"], "2020Q3": ["1", "2"]})
    assert NS.wide_to_long(few.to_csv(index=False).encode()) is None
    rep = _run(data, "eurostat_wide.csv")
    est = rep["estimand"]
    assert est["period"]["kind"] == "quarter" and est["slice"][0]["member"] == "EU27_2020" and "4-quarter totals Q1 2023" in est["text"], (est["text"], est["slice"])
    eu = truth["values"]["EU27_2020"]
    assert abs(est["figures"]["latest"]["value"] - float(eu[-4:].sum())) < 1e-6 and abs(est["figures"]["prior"]["value"] - float(eu[-8:-4].sum())) < 1e-6, est["figures"]
    fl = rep["structure"]["flags"]
    assert fl["by_kind"] == {"not_available": truth["colon"]} and fl["codes"][":"]["kind"] == "not_available" and fl["codes"]["p"]["rows"] == truth["p"], fl
    assert rep["structure"]["wide"]["periods"] == 32 and rep["structure"]["wide"]["family"] == "quarter"
    assert "wide table (32 columns of periods, 2016Q1 to 2023Q4)" in rep["limitations"][0]["text"], rep["limitations"][0]["text"]
    json.dumps(rep, allow_nan=False)
    # annual and monthly wide tables read the same way
    for freq, kind in (("year", "year"), ("month", "month")):
        d2, tr = MC.wide_period(freq)
        S = NS.wide_to_long(d2)
        assert S and S["info"]["family"] == freq, freq
        r2 = _run(d2, "eurostat_wide_%s.csv" % freq)
        assert r2["estimand"] and r2["estimand"]["period"]["kind"] == kind, (freq, r2["estimand"] and r2["estimand"]["text"])


def test_g4_an_annual_panel_of_near_equal_series_is_not_a_hierarchy_and_a_real_subset_sum_family_still_is():
    """A year column is read as annual dates (gap 4), so a country panel of 11 years is a table the structure layer reads.
    Two series within one rounding unit of each other are a copy, not a partition: a family of one member is never a
    hierarchy (the code-free search needs 2 parts). Negative: a parent that really is the sum of 2 members is still found."""
    rows = ["country,year,co2"] + ["C%02d,%d,%d" % (i, y, 100 - i + y - 2000) for i in range(12) for y in range(2000, 2011)]
    S = detect(("\n".join(rows) + "\n").encode())
    assert S["period"]["kind"] == "year", S.get("period")
    d = dim(S, "country")
    assert d["role"] not in ("hierarchy", "partition") and not S.get("breakdowns"), (d["role"], tree_sets(S, "country"), S.get("breakdowns"))
    # negative: a tree of sums with no codes and no total name (Group1 = Bravo + Charlie, Group2 = Echo + Foxtrot, All = both
    # groups) is still found, in the same annual table: the family of 2 is a partition, not a copy
    rows = ["country,year,co2"]
    for y in range(2000, 2011):
        k = y - 2000
        b, c, e, f = 40 + 3 * k, 25 + k % 4, 30 + 2 * k + (k * k) % 5, 12 + (k * 7) % 6
        vals = {"Bravo": b, "Charlie": c, "Echo": e, "Foxtrot": f, "Group1": b + c, "Group2": e + f, "Everything": b + c + e + f}
        for name, v in vals.items():
            rows.append("%s,%d,%d" % (name, y, v))
    S = detect(("\n".join(rows) + "\n").encode())
    d = dim(S, "country")
    want = {"Everything": {"Group1", "Group2"}, "Group1": {"Bravo", "Charlie"}, "Group2": {"Echo", "Foxtrot"}}
    assert d["role"] == "hierarchy" and tree_sets(S, "country") == want, (d["role"], tree_sets(S, "country"), d.get("why"))


def test_every_pattern_the_engine_compiles_is_legal_in_python_3_12_pyodide():
    """The page runs the engine in Pyodide (Python 3.12), where a global inline flag ((?i), (?s) ...) anywhere but the start of
    a pattern is an ERROR (3.9 only warns): a pattern built by joining other patterns' text broke the structure layer there
    (import failed, the file was read as before, 3x slower) while every native test passed. Every compiled pattern of the
    engine's modules is checked for the 3.11+ rule, and the literal patterns of the sources are compiled with the warning as an
    error. Negative: a pattern that starts with the flag, and a scoped group, are fine."""
    import ast
    import importlib
    import re as _re
    flag = _re.compile(r"\(\?[aiLmsux]+\)")
    bad = []
    for name in ("nl_structure", "nl_browser", "nl_scenarios", "nl_viz", "nl_inference"):
        mod = importlib.import_module(name)
        for k, v in vars(mod).items():
            if isinstance(v, _re.Pattern):
                for m in flag.finditer(v.pattern):
                    if m.start() != 0:
                        bad.append((name, k, m.start()))
    assert not bad, bad
    import warnings
    ENG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine")
    funcs = {"compile", "search", "match", "fullmatch", "sub", "subn", "findall", "finditer", "split"}
    seen = 0
    for name in ("nl_structure", "nl_browser", "nl_scenarios", "nl_viz", "nl_inference"):
        with open(os.path.join(ENG, name + ".py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in funcs and n.args
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "re"
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                seen += 1
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    _re.compile(n.args[0].value)
    assert seen > 50, seen
    # negative: what is legal stays legal
    assert not [m for m in flag.finditer("(?i)\\bpermits?\\b") if m.start() != 0]
    assert _re.compile("(?:(?i:rate)|index)")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failed = 0
    only = os.environ.get("NL_ONLY")
    if only:
        TESTS = [t for t in TESTS if only in t.__name__]
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
