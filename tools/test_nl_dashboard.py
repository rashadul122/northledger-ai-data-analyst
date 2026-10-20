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
    # phase A (owner, 19 Oct 2026): a figure on few rows is shown, with its row count
    thin = ND.query_kpi(cube, kpi, {"from": "2024-09", "to": "2024-09"})     # 3 rows that month
    assert thin["value"] is not None and not thin["suppressed"] and thin["n"] == 3, thin
    store = next(x for x in cube["dims"] if x["column"] == "store")
    ok = ND.query_kpi(cube, kpi, {"from": "2024-08", "to": "2024-08", "cats": {store["key"]: ["Alder"]}})
    assert ok["value"] is not None and ok["n"] == 6, ok                     # 6 rows of Alder that month
    rows = ND.query_kpi(cube, {"agg": "count", "mkey": None, "where": None}, {"from": "2024-09", "to": "2024-09"})
    assert rows["value"] == 3 and not rows["suppressed"], rows


def test_gaps_not_filled():
    rep = synth_rep(None)
    d = rep["dashboard"]
    ch = next(c for c in d["charts"] if c["draw"] == "line")
    s = ND.query_series(d["cube"], ch)
    i = s["months"].index("2024-05")
    assert s["series"][0]["points"][i] is None, s["series"][0]["points"][i - 1:i + 2]
    assert "2024-05" in s["gaps"], s["gaps"]
    j = s["months"].index("2024-09")
    assert s["series"][0]["points"][j] is not None                          # 3 rows: shown (phase A)
    assert s["series"][0]["points"][i - 1] is not None and s["series"][0]["points"][i + 1] is not None
    assert "2024-05" not in d["cube"]["months"]
    chk = " ".join(x["text"] for x in d["remarks"]["needs_check"])
    assert "May 2024" in chk and "left as gaps" in chk, chk
    assert "fewer than 5 rows" not in chk, chk


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


def test_static_record_definition():
    """An engine chart record on the dashboard is defined by its own measure and aggregation (17 Oct 2026: a crosstab of
    dollar totals read "The number of ledger lines.")."""
    block = {"kpis": [{"id": "s", "measure": "sales", "agg": "sum", "label": "Sales"}],
             "charts": [{"id": "wf", "chart": "contribution_waterfall", "args": ["store", "sales"], "title": "Where the change came from"},
                        {"id": "cal", "chart": "calendar_heatmap", "args": ["sales"], "title": "Sales by month and year"}]}
    d = synth_rep(block)["dashboard"]
    st = {c["id"]: c for c in d["charts"] if c.get("draw") == "static"}
    assert set(st) == {"wf", "cal"}, (list(st), d["refused"])
    for c in st.values():
        df = c["definition"]
        assert df.startswith("The total of sales") and "in CAD" in df and "number of" not in df, df
        assert "filters do not change it" in df and "static" not in c, (df, sorted(c))
    assert "by store" in st["wf"]["definition"] and "by month" in st["cal"]["definition"], st


def test_one_unit_word_per_measure():
    """One unit word per measure everywhere (17 Oct 2026: tiles in "$", chart definitions "in CAD" for one Amount): the
    plan's unit over a different word the design gave one element, which the notes record; the file's own sign over
    the plan's."""
    block = {"kpis": [{"id": "s", "measure": "sales", "agg": "sum", "label": "Sales", "unit_word": "$"}],
             "charts": [{"id": "line", "chart": "line_chart", "args": ["sales"], "title": "Sales by month"}]}
    d = synth_rep(block)["dashboard"]
    units = {x["id"]: x["unit_word"] for x in d["kpis"] + d["charts"]}
    assert units == {"s": "CAD", "line": "CAD"}, units
    assert [m["unit"] for m in d["cube"]["measures"]] == ["CAD"], d["cube"]["measures"]
    assert any("one unit word for sales" in r["why"] for r in d["refused"]), d["refused"]
    # the sample ledger writes its own sign ("$2,150.00"): every Amount figure is in "$", the cube's unit too
    s = sample_rep()["dashboard"]
    assert {x["unit_word"] for x in s["kpis"] + s["charts"] if x.get("measure")} == {"$"}, s["kpis"]
    assert all(" CAD" not in (x.get("definition") or "") for x in s["kpis"] + s["charts"])
    assert [m["unit"] for m in s["cube"]["measures"]] == ["$"], s["cube"]["measures"]


# ----------------------------------------------------------------------------- published series (19 Oct 2026)
# A visitor's FRED download (GFDGDPA188S, federal debt as a share of GDP, one row a year since 1939) showed every figure as "<5" and
# its line as a table: the dashboard read months only, and held each year's single row to the five-row rule meant for records.
def yearly_csv(code: str = "GFDGDPA188S", first: int = 1939, last: int = 2025) -> bytes:
    rnd = random.Random(11)
    v, rows = 50.0, ["observation_date,%s" % code]
    for y in range(first, last + 1):
        v = max(20.0, v + rnd.uniform(-6, 7))
        rows.append("%d-01-01,%.5f" % (y, v))
    return ("\n".join(rows) + "\n").encode()


def series_values(data: bytes) -> list:
    lines = data.decode().strip().split("\n")[1:]
    return [(ln.split(",")[0][:4], float(ln.split(",")[1])) for ln in lines]


def test_yearly_series_reads_years_and_shows_every_value():
    data = yearly_csv()
    d = NB.run(data, "GFDGDPA188S.csv", "", None, "2026-10-19")["dashboard"]
    assert d["ok"] and d["observations"] is True and d["suppress_below"] == 1, (d.get("why"), d.get("observations"))
    cube = d["cube"]
    assert cube["grain"] == "year" and cube["small"] == 1, (cube["grain"], cube["small"])
    assert d["title"].startswith("US gross federal debt"), d["title"]           # the FRED list names the code
    vals = series_values(data)
    ys = [v for _y, v in vals]
    k = {x["id"]: d["defaults"]["kpis"][x["id"]] for x in d["kpis"]}
    assert set(k) == {"now", "chg", "peak", "low"}, list(k)
    assert abs(k["now"]["value"] - ys[-1]) < 1e-4 and k["now"]["period"] == "2025", k["now"]
    assert abs(k["chg"]["value"] - (ys[-1] - ys[-2])) < 1e-4 and k["chg"]["base_period"] == "2024", k["chg"]
    hi = max(range(len(ys)), key=lambda i: (ys[i], i))
    lo = min(range(len(ys)), key=lambda i: (ys[i], -i))
    assert k["peak"]["period"] == vals[hi][0] and abs(k["peak"]["value"] - ys[hi]) < 1e-4, (k["peak"], vals[hi])
    assert k["low"]["period"] == vals[lo][0] and abs(k["low"]["value"] - ys[lo]) < 1e-4, (k["low"], vals[lo])
    assert not any(x["suppressed"] for x in k.values())
    assert all(x["unit_word"] == "%" for x in d["kpis"]), [x["unit_word"] for x in d["kpis"]]
    draws = [c["draw"] for c in d["charts"]]
    assert draws[:3] == ["line", "change", "periods"], draws
    assert "by year" in d["charts"][0]["title"] and "month" not in d["charts"][0]["title"], d["charts"][0]["title"]
    chk = json.dumps(d["remarks"]["needs_check"])
    assert "Months with" not in chk and "<5" not in chk, chk                    # no 946 empty months, no thin months
    assert d["remarks"]["data_range_text"] == "1939 to 2025", d["remarks"]["data_range_text"]
    assert d["filters"][0]["grain"] == "year" and d["filters"][0]["label"] == "Years", d["filters"]
    assert not any("<5" in r for r in d["rules"]), d["rules"]


def test_a_missing_year_is_a_gap_in_years():
    data = yearly_csv(first=1990, last=2020).decode().split("\n")
    data = "\n".join(x for x in data if not x.startswith("2005-")).encode()
    d = NB.run(data, "GFDGDPA188S.csv", "", None, "2026-10-19")["dashboard"]
    chk = " ".join(x["text"] for x in d["remarks"]["needs_check"])
    assert "Years with no rows" in chk and "2005" in chk and "Months" not in chk, chk


def test_quarterly_series_reads_quarters():
    rows, v = ["observation_date,GFDEGDQ188S"], 60.0
    for y in range(2000, 2026):
        for q in (1, 4, 7, 10):
            v += 0.4
            rows.append("%d-%02d-01,%.3f" % (y, q, v))
    d = NB.run(("\n".join(rows) + "\n").encode(), "debt-q.csv", "", None, "2026-10-19")["dashboard"]
    assert d["cube"]["grain"] == "quarter" and d["observations"], d["cube"]["grain"]
    k = d["defaults"]["kpis"]
    assert k["now"]["period"] == "2025-Q4" and k["chg"]["base_period"] == "2025-Q3", (k["now"], k["chg"])
    assert k["yoy"]["base_period"] == "2024-Q4" and abs(k["yoy"]["value"] - 1.6) < 1e-6, k["yoy"]


def test_records_are_read_as_records_and_nothing_is_hidden():
    # phase A (owner, 19 Oct 2026): the small-cell rule is off everywhere; a file of records is still read as records (its design)
    d = synth_rep(None)["dashboard"]
    assert d["observations"] is False and d["cube"]["small"] == 1 and d["suppress_below"] == 1, d.get("observations")
    rows = ["date,patient_email,visits"]
    for i in range(60):
        rows.append("2024-%02d-%02d,p%d@mail.example.com,%d" % (1 + i // 28, 1 + i % 28, i, 1 + i % 3))
    d2 = NB.run(("\n".join(rows) + "\n").encode(), "visits.csv", "", None, "2026-01-15")["dashboard"]
    assert d2.get("ok") is False or d2["observations"] is False, d2.get("observations")


def test_an_event_log_is_read_as_records():
    # one row a date, no personal column, but irregular dates (rare events): records, not published figures
    rnd, rows, day = random.Random(9), ["date,ward,cost"], 0
    import datetime as _dt
    for i in range(80):
        day += rnd.choice([1, 2, 5, 9, 13, 20, 33])
        rows.append("%s,%s,%.2f" % ((_dt.date(2020, 1, 1) + _dt.timedelta(days=day)).isoformat(), rnd.choice(["A", "B", "C"]), rnd.random() * 900))
    d = NB.run(("\n".join(rows) + "\n").encode(), "events.csv", "", None, "2026-01-15")["dashboard"]
    assert d["ok"] and d["observations"] is False, d.get("observations")
    # timestamps are events too, even one a day
    rows2 = ["when,amount"] + ["2024-%02d-%02dT%02d:15:00,%d" % (1 + i // 28, 1 + i % 28, 8 + i % 9, 10 + i) for i in range(84)]
    d2 = NB.run(("\n".join(rows2) + "\n").encode(), "calls.csv", "", None, "2026-01-15")["dashboard"]
    assert d2.get("ok") is False or d2["observations"] is False, d2.get("observations")
    assert ND.regular_dates(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])   # weekdays
    assert ND.regular_dates(["%d-01-01" % y for y in range(1990, 2000)])
    assert not ND.regular_dates(["2024-01-01", "2024-01-09", "2024-02-20", "2024-02-21", "2024-05-30"])


def test_a_long_table_of_published_figures():
    # one row per year and region, no personal column: every region's single row a year is shown
    rows, rnd = ["year,region,rate"], random.Random(3)
    for y in range(2000, 2025):
        for r in ("North", "South", "East", "West"):
            rows.append("%d-01-01,%s,%.2f" % (y, r, 3 + rnd.random() * 5))
    d = NB.run(("\n".join(rows) + "\n").encode(), "rates.csv", "", None, "2026-01-15")["dashboard"]
    assert d["observations"] and d["cube"]["small"] == 1 and d["cube"]["grain"] == "year", (d.get("observations"), d["cube"]["grain"])
    reg = next(x for x in d["cube"]["dims"] if x["column"] == "region")
    k = next(x for x in d["kpis"] if x["agg"] == "latest")
    one = ND.query_kpi(d["cube"], k, {"cats": {reg["key"]: ["North"]}})
    assert one["value"] is not None and not one["suppressed"] and one["n"] == 1, one


def test_browser_computes_the_same_series_tiles():
    """57-dashboard.js NLD.kpi equals nl_dashboard.query_kpi on every tile, filtered and not (Node)."""
    import subprocess
    import tempfile
    d = NB.run(yearly_csv(), "GFDGDPA188S.csv", "", None, "2026-10-19")["dashboard"]
    filts = [{}, {"from": "1950-01", "to": "1990-01"}, {"from": "2010-01"}]
    want = [[ND.query_kpi(d["cube"], k, f) for k in d["kpis"]] for f in filts]
    js = ("global.window={NLU:{}};global.document={readyState:'complete',addEventListener(){}};require(process.argv[1]);"
          "const D=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));const F=JSON.parse(process.argv[3]);"
          "process.stdout.write(JSON.stringify(F.map(f=>D.kpis.map(k=>window.NLD.kpi(D,k,f)))));")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(d, fh)
    try:
        r = subprocess.run(["node", "-e", js, os.path.join(SITE, "src", "js", "57-dashboard.js"), fh.name, json.dumps(filts)],
                           capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(fh.name)
    assert r.returncode == 0, r.stderr[-800:]
    got = json.loads(r.stdout)
    for wf, gf in zip(want, got):
        for w, g in zip(wf, gf):
            for key in ("period", "base_period", "suppressed"):
                assert w.get(key) == g.get(key), (key, w, g)
            for key in ("value", "base"):
                if w.get(key) is None:
                    assert g.get(key) is None, (key, w, g)
                else:
                    assert abs(w[key] - g[key]) < 1e-6, (key, w, g)


def test_ai_design_with_the_series_menu():
    plan = {"goal": "Where does federal debt stand?", "kind": "series", "understanding": "",
            "columns": [{"name": "GFDGDPA188S", "semantic_type": "percentage", "role": "target", "unit": "%",
                         "label": "Gross federal debt (% of GDP)"},
                        {"name": "observation_date", "semantic_type": "date", "role": "date"}],
            "operations": [], "analyses": [], "primary": "GFDGDPA188S",
            "dashboard": {"audience": "Budget watchers",
                          "kpis": [{"id": "now", "measure": "GFDGDPA188S", "agg": "latest", "label": "Debt now"},
                                   {"id": "yoy", "measure": "GFDGDPA188S", "agg": "change", "vs": "year", "label": "Change"},
                                   {"id": "hi", "measure": "GFDGDPA188S", "agg": "peak", "label": "Record"}],
                          "charts": [{"id": "ch", "chart": "change_chart", "args": ["GFDGDPA188S"], "title": "Year on year"},
                                     {"id": "dec", "chart": "period_chart", "args": [], "title": "Decade averages"}],
                          "filters": [{"kind": "date"}],
                          "questions": [{"q": "Is debt at a record?", "answered_by": ["now", "hi"]}]}}
    d = NB.run(yearly_csv(), "GFDGDPA188S.csv", "", {"__plan__": plan}, "2026-10-19")["dashboard"]
    assert d["chosen_by"] == "ai" and not d["refused"], d["refused"]
    assert [k["agg"] for k in d["kpis"]] == ["latest", "change", "peak"], d["kpis"]
    assert d["kpis"][1]["vs"] == "previous"                    # a yearly series: a year earlier is the year before
    assert [c["draw"] for c in d["charts"]] == ["change", "periods"], d["charts"]
    assert d["title"] == "Gross federal debt (% of GDP)" and d["kpis"][0]["measure"] == "Gross federal debt (% of GDP)", d["title"]


def test_a_weak_ai_design_goes_back_as_signals():
    # the live miss: an AI design that averaged a yearly series and charted it by month goes back to the planner
    plan = {"goal": "Debt", "kind": "series", "understanding": "",
            "columns": [{"name": "GFDGDPA188S", "semantic_type": "percentage", "role": "target", "unit": "%"},
                        {"name": "observation_date", "semantic_type": "date", "role": "date"}],
            "operations": [], "analyses": [], "primary": "GFDGDPA188S",
            "dashboard": {"kpis": [{"id": "avg", "measure": "GFDGDPA188S", "agg": "average", "label": "Average"},
                                   {"id": "ghost", "measure": "profit", "agg": "sum", "label": "Profit"}],
                          "charts": [{"id": "l", "chart": "line_chart", "args": ["GFDGDPA188S"], "title": "Debt"}]}}
    rep = NB.run(yearly_csv(), "GFDGDPA188S.csv", "", {"__plan__": plan}, "2026-10-19")
    sig = [x for x in rep.get("plan_signals") or [] if x.get("kind") == "dashboard"]
    text = " ".join(x["detail"] for x in sig)
    assert "published figure" in text and "latest, change, peak and low" in text, sig
    assert "refused" in text, sig                                       # the tile on a column the file does not have
    assert not any(ch.isdigit() for x in sig for ch in x["detail"].replace("GFDGDPA188S", "")), sig
    # the engine's own design sends nothing
    assert ND.signals(NB.run(yearly_csv(), "GFDGDPA188S.csv", "", None, "2026-10-19")["dashboard"]) == []


def test_undated_table_gets_the_reports_charts():
    rows, rnd = ["state,score,rate"], random.Random(5)
    for st in ("Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia Hawaii Idaho "
               "Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland").split():
        rows.append("%s,%.1f,%.2f" % (st, 40 + rnd.random() * 50, rnd.random() * 9))
    d = NB.run(("\n".join(rows) + "\n").encode(), "states.csv", "", None, "2026-01-15")["dashboard"]
    assert d["ok"], d.get("why")
    st = [c for c in d["charts"] if c["draw"] == "static"]
    assert len(d["charts"]) >= 2 and st, [c["draw"] for c in d["charts"]]
    assert all(c.get("record") and c.get("definition", "").startswith("The report's own chart") for c in st), st[0].get("definition")


# ----------------------------------------------------------------------------- the live test of 19 Oct 2026
def test_a_column_cut_that_merges_rows_is_refused():
    # Chicago's employees: the plan kept 6 of 8 columns without the name, 27,453 of 32,022 rows became "duplicates"
    rows = ["Name,Job,Dept,Salary"] + ["P%d,CLERK,WATER,%d" % (i, 50000 + (i % 3)) for i in range(300)]
    plan = {"goal": "pay", "kind": "other", "understanding": "", "primary": "Salary", "analyses": [],
            "columns": [{"name": "Salary", "semantic_type": "flow_amount", "role": "target"}],
            "operations": [{"op": "keep_columns", "columns": ["Job", "Dept", "Salary"]}]}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "pay.csv", "", {"__plan__": plan}, "2026-10-19")
    assert rep["ok"], rep.get("error")
    ref = " ".join((rep.get("ai_plan") or {}).get("refused") or [])
    assert "look identical" in ref and rep["cleaning"]["rows_clean"] == 300, (ref, rep["cleaning"]["rows_clean"])


def test_a_ratio_above_100_and_an_average_rating_are_not_misreads():
    plan = {"goal": "debt", "kind": "series", "understanding": "", "primary": "GFDGDPA188S", "operations": [], "analyses": [],
            "columns": [{"name": "GFDGDPA188S", "semantic_type": "percentage", "role": "target"},
                        {"name": "observation_date", "semantic_type": "date", "role": "date"}]}
    rep = NB.run(yearly_csv(), "GFDGDPA188S.csv", "", {"__plan__": plan}, "2026-10-19")
    data = yearly_csv().decode().split("\n")
    t = [x for x in (rep.get("contracts") or {}).get("tests") or [] if x.get("column") == "GFDGDPA188S"]
    over = sum(1 for ln in data[1:] if ln and float(ln.split(",")[1]) > 100)
    if over:
        assert t and not t[0].get("signal"), t
    rows = ["product,avg_rating,reviews"] + ["P%d,%.1f,%d" % (i, 3 + (i % 20) / 10.0, 10 + i) for i in range(80)]
    plan2 = {"goal": "ratings", "kind": "other", "understanding": "", "primary": "avg_rating", "operations": [], "analyses": [],
             "columns": [{"name": "avg_rating", "semantic_type": "rating", "role": "target"}]}
    rep2 = NB.run(("\n".join(rows) + "\n").encode(), "r.csv", "", {"__plan__": plan2}, "2026-10-19")
    t2 = [x for x in (rep2.get("contracts") or {}).get("tests") or [] if x.get("column") == "avg_rating"]
    assert t2 and not t2[0].get("signal") and "average of ratings" in t2[0].get("test", ""), t2


def test_a_yearly_series_reads_its_years_in_the_report():
    rep = NB.run(yearly_csv(), "GFDGDPA188S.csv", "", None, "2026-10-19")
    blob = json.dumps({k: rep.get(k) for k in ("story", "summary", "scenarios")})
    assert "one ending each month" not in blob and "independent" not in blob, blob[blob.find("independent") - 200:][:300]
    assert "the latest 12 months against the 12 before" not in blob


def test_the_row_sample_reads_the_engines_kept_rows_only():
    rows = ["order_date,status,amount", "awaiting Zelda Vanterpool,open,10"] + \
        ["2024-%02d-%02d,open,%d" % (1 + i // 28, 1 + i % 28, 10 + i) for i in range(90)]
    p = NB.profile_for_ai(("\n".join(rows) + "\n").encode(), "o.csv")
    sm = p.get("sample") or {}
    assert sm.get("rows") and "Zelda" not in json.dumps(sm), sm


def test_the_engines_design_leads_with_money_over_hours():
    rows = ["Dept,Typical Hours,Annual Salary"] + ["D%d,%d,%d" % (i % 4, 30 + i % 10, 50000 + 37 * i) for i in range(200)]
    # the salary is flagged by its name; the visitor's acknowledgement keeps it (the page sends keep for every flagged column)
    d = NB.run(("\n".join(rows) + "\n").encode(), "pay.csv", "", {"annual_salary": "keep"}, "2026-10-19")["dashboard"]
    meas = [k.get("measure") for k in d["kpis"] if k.get("measure")]
    assert meas and meas[0] == "Annual Salary", meas


def test_a_small_first_to_last_change_is_said_to_be_within_the_usual_swing():
    """57-dashboard.js NLD.insights: noise is not called a fall (Node)."""
    import subprocess
    import tempfile
    rnd, rows, v = random.Random(4), ["observation_date,IDX"], 4.0
    for y in range(2000, 2024):
        v = 4.0 + rnd.uniform(-0.2, 0.2)
        rows.append("%d-01-01,%.3f" % (y, v))
    d = NB.run(("\n".join(rows) + "\n").encode(), "idx.csv", "", None, "2026-10-19")["dashboard"]
    js = ("global.window={NLU:{}};global.document={readyState:'complete',addEventListener(){}};require(process.argv[1]);"
          "const D=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));process.stdout.write(JSON.stringify(window.NLD.insights(D,{})));")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(d, fh)
    try:
        r = subprocess.run(["node", "-e", js, os.path.join(SITE, "src", "js", "57-dashboard.js"), fh.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(fh.name)
    said = [t for t in json.loads(r.stdout) if t.startswith("From ")]
    assert said and ("within its normal swing" in said[0] or " rose " in said[0] or " fell " in said[0]), said
    first, last = float(rows[1].split(",")[1]), float(rows[-1].split(",")[1])
    if abs(last - first) < 0.15:
        assert "within its normal swing" in said[0], said


def test_the_ais_visuals_are_run_by_the_engine_and_their_specs_checked():
    """engine/nl_vega.py (AI Analyst v2 phase C): the AI's query runs on the analysis frame, its spec draws only the result's
    columns; data, transforms and addresses of its own are refused, and every refusal is a signal for the self-correction."""
    import nl_vega as NV
    rows = ["order_date,region,channel,revenue,units"] + ["2024-%02d-%02d,%s,%s,%d,%d" % (1 + i % 12, 1 + i % 28, "NESW"[i % 4], "AB"[(i // 4) % 2], 100 + i, 1 + i % 7)
                                                          for i in range(400)]
    ok_spec = {"mark": "bar", "encoding": {"x": {"field": "region", "type": "nominal", "sort": "-y"},
                                          "y": {"field": "revenue", "type": "quantitative", "axis": {"format": "$,.0f"}},
                                          "color": {"field": "channel", "type": "nominal"}}}
    plan = {"goal": "revenue", "dashboard": {"kpis": [{"id": "rev", "measure": "revenue", "agg": "sum", "label": "Revenue"}], "visuals": [
        {"id": "mix", "title": "Revenue by region and channel", "why": "where it comes from",
         "sql": "SELECT region, channel, SUM(revenue) revenue FROM kept_rows GROUP BY region, channel", "spec": ok_spec},
        {"id": "pulled", "title": "Pulled in", "sql": "SELECT region FROM kept_rows", "spec": {"data": {"url": "https://x.example/a.csv"}, "mark": "bar",
                                                                                              "encoding": {"x": {"field": "region", "type": "nominal"}}}},
        {"id": "wrong", "title": "Units", "sql": "SELECT region, SUM(units) AS u FROM kept_rows GROUP BY 1",
         "spec": {"mark": "bar", "encoding": {"x": {"field": "units", "type": "quantitative"}}}},
        {"id": "nope", "title": "Nothing", "sql": "SELECT region FROM kept_rows WHERE region = 'Q'", "spec": ok_spec},
        {"id": "raw", "title": "Every order", "sql": "SELECT revenue FROM kept_rows",
         "spec": {"mark": "bar", "encoding": {"x": {"field": "revenue", "type": "quantitative", "bin": True}, "y": {"aggregate": "count"}}}}]}}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "o.csv", "", {"__plan__": plan}, "2026-10-20")
    d = rep["dashboard"]
    assert [v["id"] for v in d.get("visuals") or []] == ["mix"], d.get("visuals")
    v = d["visuals"][0]
    assert v["columns"] == ["region", "channel", "revenue"] and len(v["rows"]) == 8, v
    import pandas as pd
    df = pd.read_csv(io.StringIO("\n".join(rows)))
    want = df.groupby(["region", "channel"])["revenue"].sum().to_dict()
    got = {(r[0], r[1]): r[2] for r in v["rows"]}
    assert all(abs(got[k] - want[k]) < 1e-6 for k in want), (got, want)
    why = " | ".join(r["why"] for r in d.get("refused") or [])
    for w in ("carries data", "does not return: units", "returned no rows", "returned 400 rows and a visual draws at most 200"):
        assert w in why, why
    sig = " | ".join(s_["detail"] for s_ in rep.get("plan_signals") or [] if s_.get("kind") == "dashboard")
    assert "visual Pulled in was refused" in sig, sig
    # the grammar on its own: an expression, an unknown channel, an address, an unknown mark
    for bad in ({"mark": "bar", "encoding": {"x": {"field": "a", "type": "nominal"}}, "transform": [{"calculate": "1", "as": "b"}]},
                {"mark": "bar", "encoding": {"href": {"field": "a", "type": "nominal"}}},
                {"mark": "image", "encoding": {"x": {"field": "a", "type": "nominal"}}},
                {"mark": "text", "encoding": {"text": {"value": "javascript:alert(1)"}}}):
        assert NV.validate_spec(bad, ["a"])[0] is None, bad
    clean, _ = NV.validate_spec({"mark": {"type": "line", "point": True, "color": "red"}, "encoding": {"x": {"field": "a", "type": "temporal"}}}, ["a"])
    assert clean == {"mark": {"type": "line", "point": True}, "encoding": {"x": {"field": "a", "type": "temporal"}}}, clean


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
