#!/usr/bin/env python3
"""
Tests for engine/nl_dashboard.py, the Dashboard tab's engine side (plan/DASHBOARD-DESIGN.md Track A; CONTRACT-v2.md 5.14).

    python tools/test_nl_dashboard.py                      # the adapter in engine/
    NL_BROWSER_DIR=/some/copy python tools/test_nl_dashboard.py

Runs the adapter natively on the site's sample ledger and on synthetic files built here, and checks:
  * the AI's dashboard block is checked element by element: an unknown column or chart, a personal column, a figure in
    an AI string, a question that answers nothing: each refused with its reason; a 4-digit year is allowed;
  * levels are never summed (a balance asked for as a sum is averaged, and the refusal says why);
  * the engine's default design without the AI (tiles, a line by month, bars, the date and category filters);
  * the cube's sums equal the rows the engine kept (plain pandas over downloads.clean_csv) and the engine's own
    line chart of the sample's rent, month by month;
  * suppression after filtering ("<5": no value under 5 rows), and gaps left as gaps (no month filled);
  * no withheld column anywhere in the record (its header, its values), and its refusals never name it;
  * the sample's recorded design (by sha256 only; a changed byte gets the engine's own design);
  * results_for_ai carries the design, never the cube.
Self-running (PASS/FAIL per test, exit 1 on any failure). Python 3.9, pandas and numpy; nothing is fetched.
"""
from __future__ import annotations

import io
import json
import math
import os
import random
import sys

os.environ.setdefault("NL_BROWSER_STRICT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ADAPTER_DIR = os.path.abspath(os.environ.get("NL_BROWSER_DIR") or os.path.join(SITE, "engine"))
ENGINE_ROOT = os.path.abspath(os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core"))
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, ADAPTER_DIR)
import nl_browser as NB  # noqa: E402
import nl_dashboard as ND  # noqa: E402
import pandas as pd  # noqa: E402

SAMPLE = os.path.join(ADAPTER_DIR, "sample-messy.csv")
AS_OF = "2026-09-15"
_MEMO = {}


def sample_rep():
    if "sample" not in _MEMO:
        with open(SAMPLE, "rb") as fh:
            _MEMO["sample"] = NB.run(fh.read(), "sample-messy.csv", "", None, AS_OF)
    return _MEMO["sample"]


def synth_csv(gap: str = "2024-05", thin: str = "2024-09") -> bytes:
    """24+ months of a store ledger: sales (a flow), balance (a level), an email column (personal: withheld by
    default), a month with no rows (`gap`) and a month with 3 rows (`thin`)."""
    rnd = random.Random(7)
    rows = ["date,store,region,sales,balance,email"]
    stores = ["Alder", "Birch", "Cedar", "Maple", "Spruce"]
    regions = {"Alder": "North", "Birch": "North", "Cedar": "South", "Maple": "South", "Spruce": "West"}
    for y in (2023, 2024, 2025):
        for m in range(1, 13):
            ym = "%d-%02d" % (y, m)
            if ym == gap:
                continue
            n = 3 if ym == thin else 30
            for i in range(n):
                s = stores[i % 5]
                rows.append("%s-%02d,%s,%s,%.2f,%.2f,%s" % (ym, 1 + i % 27, s, regions[s], 100 + rnd.random() * 400,
                                                         1000 + rnd.random() * 50, "p%d.%s@mail.example.com" % (i, s.lower())))
    return ("\n".join(rows) + "\n").encode()


def synth_rep(block=None, extra_cols=None):
    plan = None
    if block is not None:
        plan = {"goal": "How are sales moving by store?", "kind": "transactions", "understanding": "",
                "columns": [{"name": "sales", "semantic_type": "flow_amount", "role": "target", "unit": "CAD"},
                            {"name": "balance", "semantic_type": "level", "role": "driver", "unit": "CAD"},
                            {"name": "store", "semantic_type": "category", "role": "segment"},
                            {"name": "region", "semantic_type": "category", "role": "segment"},
                            {"name": "date", "semantic_type": "date", "role": "date"}] + (extra_cols or []),
                "operations": [], "analyses": [], "primary": "sales", "dashboard": block}
    dec = {"__plan__": plan} if plan else None
    return NB.run(synth_csv(), "stores.csv", "", dec, "2026-01-15")


def frame_of(rep):
    return pd.read_csv(io.StringIO(rep["downloads"]["clean_csv"]), dtype=str, keep_default_na=False)


# ----------------------------------------------------------------------------- tests
def test_validation_refusals():
    block = {"audience": "Store managers", "questions": [
        {"q": "How are sales moving?", "answered_by": ["s", "line"]},
        {"q": "What about the moon?", "answered_by": ["nothing_here"]}],
        "kpis": [{"id": "s", "measure": "sales", "agg": "sum", "label": "Sales", "unit_word": "CAD"},
                 {"id": "ghost", "measure": "profit", "agg": "sum", "label": "Profit"},
                 {"id": "mail", "measure": "email", "agg": "count", "label": "Emails"},
                 {"id": "top", "measure": "sales", "agg": "sum", "label": "Top 5 sales"}],
        "charts": [{"id": "line", "chart": "line_chart", "args": ["sales"], "title": "Sales in 2025 and before"},
                   {"id": "pie", "chart": "pie_chart", "args": ["store"], "title": "A pie"},
                   {"id": "bars", "chart": "bar_chart", "args": ["store", "sales"], "title": "Sales up 12% by store"},
                   {"id": "who", "chart": "bar_chart", "args": ["email", "sales"], "title": "By person"}],
        "filters": [{"kind": "date"}, {"column": "store", "kind": "category"}, {"column": "email", "kind": "category"}],
        "layout": [["s", "line"], ["bars", "unknown"]]}
    d = synth_rep(block)["dashboard"]
    assert d["ok"] and d["chosen_by"] == "ai", d.get("why")
    why = {r["element"]: r["why"] for r in d["refused"]}
    text = json.dumps(d["refused"])
    assert any("does not have" in w for w in why.values()), why            # profit
    assert any("not on the chart menu" in w for w in why.values()), why    # pie_chart
    assert any("personal column" in w for w in why.values()), why          # email
    assert any("held a figure" in w for w in why.values()), why            # "Top 5", "12%"
    assert any("answers nothing" in w for w in why.values()), why
    assert "email" not in text.lower(), text
    ids = [k["id"] for k in d["kpis"]]
    assert "s" in ids and "ghost" not in ids and "mail" not in ids, ids
    titles = {c["id"]: c["title"] for c in d["charts"]}
    assert titles.get("line") == "Sales in 2025 and before", titles      # a year is allowed
    assert "bars" in titles and "12" not in titles["bars"], titles       # the engine wrote its own title
    assert set(titles) == {"line", "bars"}, titles
    assert [q["q"] for q in d["questions"]] == ["How are sales moving?"], d["questions"]
    assert all(i in ids + list(titles) for row in d["layout"] for i in row), d["layout"]
    assert [f["column"] for f in d["filters"]] == ["date", "store"], d["filters"]


def test_levels_never_summed():
    block = {"kpis": [{"id": "bal", "measure": "balance", "agg": "sum", "label": "Balance"},
                      {"id": "sales", "measure": "sales", "agg": "average", "label": "Average sale"}],
             "charts": [{"id": "bl", "chart": "line_chart", "args": ["balance"], "title": "Balance by month"},
                        {"id": "ba", "chart": "area_chart", "args": ["store", "balance"], "title": "Balance stacked"}]}
    d = synth_rep(block)["dashboard"]
    k = {x["id"]: x for x in d["kpis"]}
    assert k["bal"]["agg"] == "average", k["bal"]
    assert k["sales"]["agg"] == "average", k["sales"]
    assert any("never added up over time" in r["why"] for r in d["refused"]), d["refused"]
    c = {x["id"]: x for x in d["charts"]}
    assert c["bl"]["agg"] == "average", c["bl"]
    assert "ba" not in c, c                      # an area chart stacks a total; a level is refused
    m = next(x for x in d["cube"]["measures"] if x["column"] == "balance")
    assert m["flow"] is False
    # the browser's rule: the balance tile is an average of the rows, never their sum
    f = frame_of(synth_rep(block))
    v = pd.to_numeric(f["balance"], errors="coerce")
    assert abs(d["defaults"]["kpis"]["bal"]["value"] - v.mean()) < 1e-6, (d["defaults"]["kpis"]["bal"], v.mean())
    assert "a level: averaged" in k["bal"]["definition"], k["bal"]["definition"]


def test_default_design():
    rep = synth_rep(None)
    d = rep["dashboard"]
    assert d["ok"] and d["chosen_by"] == "engine", d
    assert d["kpis"][0]["agg"] == "count", d["kpis"][0]
    draws = [c["draw"] for c in d["charts"]]
    assert "line" in draws and "bar" in draws, draws
    assert d["filters"][0]["kind"] == "date" and 1 <= len(d["filters"]) <= 4, d["filters"]
    assert d["questions"] and all(q["answered_by"] for q in d["questions"]), d["questions"]
    assert "One row is one" in d["grain"]["text"], d["grain"]
    for k in d["kpis"]:
        assert k["definition"].endswith("."), k


def _kpi_total(d, where_level):
    cube = d["cube"]
    return ND.query_kpi(cube, {"agg": "sum", "mkey": "m0", "where": where_level})


def test_cube_equals_engine():
    rep = sample_rep()
    d = rep["dashboard"]
    f = frame_of(rep)
    amt = pd.to_numeric(f["amount"].where(f["amount"] != ""), errors="coerce")
    rent = f["category"] == "RENT"
    k = d["defaults"]["kpis"]
    assert abs(k["rent"]["value"] - amt[rent].sum()) < 1e-6, (k["rent"], amt[rent].sum())
    assert abs(k["spend"]["value"] - amt[~rent].sum()) < 1e-6, (k["spend"], amt[~rent].sum())
    assert k["lines"]["value"] == len(f) == d["cube"]["rows"], (k["lines"], len(f))
    assert k["unpaid"]["value"] == int((f["paid"] == "N").sum()), k["unpaid"]
    # the cube's cells add up to every kept row, and every filled amount
    nd = len(d["cube"]["dims"])
    assert sum(c[1 + nd] for c in d["cube"]["cells"]) == len(f)
    assert abs(sum(c[2 + nd] for c in d["cube"]["cells"]) - amt.sum()) < 1e-6
    # month by month, the rent line equals the engine's own line chart (its table's printed figures)
    ch = next(c for c in d["charts"] if c["id"] == "rent_trend")
    s = ND.query_series(d["cube"], ch)
    by = dict(zip(s["months"], s["series"][0]["points"]))
    eng = next(c for c in rep["viz"]["charts"] if c["chart"] == "line_chart")
    n = 0
    for row in eng["table"]["rows"]:
        if not row[1]:
            continue
        mon = pd.to_datetime(row[0], format="%b %Y").strftime("%Y-%m")
        assert format(round(by[mon]), ",") == row[1], (mon, by[mon], row[1])
        n += 1
    assert n >= 12, n


def test_suppression_after_filter():
    rep = synth_rep(None)
    d = rep["dashboard"]
    cube = d["cube"]
    kpi = {"agg": "sum", "mkey": next(m["key"] for m in cube["measures"] if m["column"] == "sales"), "where": None}
    full = ND.query_kpi(cube, kpi)
    assert full["value"] is not None and not full["suppressed"]
    thin = ND.query_kpi(cube, kpi, {"from": "2024-09", "to": "2024-09"})     # 3 rows that month
    assert thin["value"] is None and thin["suppressed"] and thin["n"] == 3, thin
    store = next(x for x in cube["dims"] if x["column"] == "store")
    one = ND.query_kpi(cube, kpi, {"from": "2024-09", "to": "2024-09", "cats": {store["key"]: ["Alder"]}})
    assert one["value"] is None and one["n"] < 5, one
    ok = ND.query_kpi(cube, kpi, {"from": "2024-08", "to": "2024-08", "cats": {store["key"]: ["Alder"]}})
    assert ok["value"] is not None and ok["n"] == 6, ok                     # 6 rows of Alder that month
    rows = ND.query_kpi(cube, {"agg": "count", "mkey": None, "where": None}, {"from": "2024-09", "to": "2024-09"})
    assert rows["value"] is None and rows["suppressed"], rows


def test_gaps_not_filled():
    rep = synth_rep(None)
    d = rep["dashboard"]
    ch = next(c for c in d["charts"] if c["draw"] == "line")
    s = ND.query_series(d["cube"], ch)
    i = s["months"].index("2024-05")
    assert s["series"][0]["points"][i] is None, s["series"][0]["points"][i - 1:i + 2]
    assert "2024-05" in s["gaps"], s["gaps"]
    j = s["months"].index("2024-09")
    assert s["series"][0]["points"][j] is None                              # 3 rows: suppressed, a gap too
    assert s["series"][0]["points"][i - 1] is not None and s["series"][0]["points"][i + 1] is not None
    assert "2024-05" not in d["cube"]["months"]
    chk = " ".join(x["text"] for x in d["remarks"]["needs_check"])
    assert "May 2024" in chk and "left as gaps" in chk, chk
    assert "Sep 2024" in chk and "fewer than 5 rows" in chk, chk


def test_no_withheld_column_anywhere():
    for block in (None, {"kpis": [{"id": "e", "measure": "email", "agg": "count", "label": "Mail"}],
                         "charts": [{"id": "c", "chart": "bar_chart", "args": ["email"], "title": "By address"},
                                    {"id": "s", "chart": "bar_chart", "args": ["store", "sales"], "title": "Sales by store"}],
                         "filters": [{"column": "email", "kind": "category"}]}):
        rep = synth_rep(block)
        assert any(f.get("decision") == "withhold" for f in rep["privacy"]["flagged"]), rep["privacy"]
        d = rep["dashboard"]
        blob = json.dumps(d).lower()
        assert "email" not in blob, [x for x in d["refused"]]
        assert "@mail.example.com" not in blob and "p0.alder" not in blob
        for dim in d["cube"]["dims"]:
            assert dim["column"] in ("store", "region"), dim
    # the sample: the withheld Notes column's values never appear
    d = sample_rep()["dashboard"]
    blob = json.dumps(d)
    for v in ("Mice treatment", "Fridge not cooling", "Stove element replaced"):
        assert v not in blob, v
    assert all(x["column"] != "Notes" for x in d["cube"]["dims"])


def test_recorded_design():
    d = sample_rep()["dashboard"]
    assert d["chosen_by"] == "recorded" and "recorded AI design" in d["recorded"], (d["chosen_by"], d["recorded"])
    assert [k["id"] for k in d["kpis"]] == ["rent", "spend", "unpaid", "lines"], d["kpis"]
    assert [c["draw"] for c in d["charts"]] == ["line", "grouped", "bar", "grouped"], d["charts"]
    assert not d["refused"], d["refused"]
    assert [f["column"] for f in d["filters"]] == ["Date", "Property", "Category", "Paid?"], d["filters"]
    with open(SAMPLE, "rb") as fh:
        b = bytearray(fh.read())
    b[-2:-1] = b" " if b[-2:-1] != b" " else b"x"
    d2 = NB.run(bytes(b), "sample-messy.csv", "", None, AS_OF)["dashboard"]
    assert d2["chosen_by"] == "engine", d2["chosen_by"]
    with open(os.path.join(ADAPTER_DIR, ND.RECORDED_FILE), encoding="utf-8") as fh:
        fx = json.load(fh)
    assert fx["input_sha256"] == sample_rep()["input"]["sha256"]
    for s in [fx["dashboard"]["audience"]] + [q["q"] for q in fx["dashboard"]["questions"]] + \
            [k["label"] for k in fx["dashboard"]["kpis"]] + [c["title"] for c in fx["dashboard"]["charts"]]:
        assert not ND.has_figure(s), s


def test_notes_put_what_needs_a_check_first():
    r = sample_rep()["dashboard"]["remarks"]
    need = [x["text"] for x in r["needs_check"]]
    assert need[0].startswith("55 ledger lines repeat another row exactly"), need
    assert any("dated in the future (the latest 2027-11-11)" in t for t in need), need
    assert any(t.startswith("1 ledger line has a Date that is no real date") for t in need), need
    assert any(t.startswith("17 Amount values were words, not numbers") for t in need), need
    assert any("never named (privacy" in t for t in need), need
    assert {x["rule"] for x in r["needs_check"] if x["where"] == "set_aside"} >= {"exact_duplicates", "amount_range", "date_not_future", "date_date"}
    assert len(r["fixed"]) == 7 and all(t.endswith(")." ) for t in r["fixed"]), r["fixed"]
    assert len(r["detail"]) >= 10 and not any("Cleaning:" in t for t in r["detail"])
    assert not any("null-like" in t for t in need)                       # the raw health lines are folded, not first


def test_area_dominated_by_other_is_drawn_as_bars():
    with open(SAMPLE, "rb") as fh:
        b = bytearray(fh.read())
    b[-2:-1] = b"x"                                                       # not the sample: the engine's own design
    d = NB.run(bytes(b), "sample-messy.csv", "", None, AS_OF)["dashboard"]
    assert d["chosen_by"] == "engine"
    assert all(c["draw"] != "area" for c in d["charts"]), [(c["id"], c["draw"]) for c in d["charts"]]
    assert any("stacked area" in t and "would hold" in t and "grouped bar" in t for t in d["remarks"]["design"]), d["remarks"]["design"]
    # an area whose named groups hold most of the total stays an area (the synthetic stores: 5 stores, full months)
    rep = synth_rep({"charts": [{"id": "a", "chart": "area_chart", "args": ["store", "sales"], "title": "Sales by store"}]})
    a = next(c for c in rep["dashboard"]["charts"] if c["id"] == "a")
    sh = ND.area_other_share(ND.Read(rep, {"r": None, "reading": None, "plan": None}), {"measure": "sales", "by": "store", "where": None})
    assert a["draw"] == "area" and sh is not None and sh <= ND.AREA_OTHER_MAX, (a["draw"], sh)


def test_results_for_ai():
    rep = sample_rep()
    out = NB.results_for_ai(rep)
    ds = out.get("dashboard")
    assert ds and ds["chosen_by"] == "recorded" and len(ds["kpis"]) == 4, ds
    assert "cube" not in json.dumps(ds), ds
    assert "dashboard" not in (rep.get("ai_plan") or {})


def test_has_figure():
    assert not ND.has_figure("Rent in 2025 against 2024")
    assert ND.has_figure("Top 5 units") and ND.has_figure("Up 12%") and ND.has_figure("Q3 rent")
    assert ND.has_figure("In 1066")


def test_deterministic():
    a = json.dumps(synth_rep(None)["dashboard"], sort_keys=True)
    b = json.dumps(synth_rep(None)["dashboard"], sort_keys=True)
    assert a == b
    for c in synth_rep(None)["dashboard"]["cube"]["cells"]:
        assert all(isinstance(x, (int, float)) and math.isfinite(x) for x in c), c


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            import traceback
            traceback.print_exc()
            print("FAIL", t.__name__, type(exc).__name__, str(exc)[:600])
    print("%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
    sys.exit(1 if bad else 0)
