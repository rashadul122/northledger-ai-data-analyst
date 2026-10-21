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
    cap = NV.ROWS_MAX
    NV.ROWS_MAX = 300                       # a 400-row result is over the cap here (the real cap, 2,000, lets a scatter of a small file draw)
    try:
        rep = NB.run(("\n".join(rows) + "\n").encode(), "o.csv", "", {"__plan__": plan}, "2026-10-20")
    finally:
        NV.ROWS_MAX = cap
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
    for w in ("carries data", "does not return: units", "returned no rows", "returned 400 rows and a visual draws at most 300"):
        assert w in why, why
    ra = NB.results_for_ai(rep)                                                   # phase E: the writer sees each visual as a card
    kn = ra.get("key_numbers") or []                                              # phase G: and the dashboard's tiles in the page's words
    assert kn and kn[0]["text"].startswith("Revenue: ") and "{:,.0f}".format(sum(float(r.split(",")[3]) for r in rows[1:])) in kn[0]["text"], kn
    assert [x["id"] for x in ra.get("visuals") or []] == ["mix"] and len(ra["visuals"][0]["rows"]) == 8, ra.get("visuals")
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


def test_key_influencers_are_intervals_that_exclude_zero_and_a_partial_last_month_is_marked():
    """Phase D (20 Oct 2026): a group whose average truly differs is found with its Welch interval; noise is not; a two-level column
    is one finding, not its mirror; data that stop on the 9th mark their last month as not whole."""
    rnd = random.Random(7)
    rows = ["order_date,channel,region,revenue"]
    for i in range(600):
        ch = "Online" if i % 2 else "Store"
        rg = "NESW"[rnd.randrange(4)]
        rev = (150 if ch == "Online" else 100) + rnd.gauss(0, 20)
        day = "2024-%02d-%02d" % (1 + i % 9, 1 + i % 28) if i % 9 != 8 else "2024-09-%02d" % (1 + i % 9)
        rows.append("%s,%s,%s,%.2f" % (day, ch, rg, rev))
    d = NB.run(("\n".join(rows) + "\n").encode(), "o.csv", "", None, "2026-10-20")["dashboard"]
    inf = d.get("influencers") or {}
    assert inf.get("measure") == "revenue", inf
    chans = [x for x in inf["items"] if x["column"] == "channel"]
    assert len(chans) == 1 and abs(abs(chans[0]["diff"]) - 50) < 8 and chans[0]["lo"] * chans[0]["hi"] > 0, chans
    assert not any(x["column"] == "region" for x in inf["items"]), inf["items"]          # random regions: no finding
    assert d["cube"]["last_day"] == "2024-09-09" and d["cube"]["last_month_full"] is False, (d["cube"]["last_day"], d["cube"]["last_month_full"])


def test_a_records_files_tiles_read_the_step_its_charts_show_and_skip_a_month_still_filling():
    """20 Oct 2026: phase A's row floor of 1 made every records file's tiles read month by month (Amazon's 24 years compared 27 reviews
    in August with July); a published series is told by the engine's reading (cube.series). And a records file whose data stop on the
    9th compares its last whole month, never the 9 days."""
    rnd = random.Random(3)
    rows = ["review_id,review_date,rating"] + ["R%05d,%d-%02d-%02d,%.1f" % (i, 2010 + i % 12, 1 + (i // 12) % 12, 1 + i % 27, rnd.uniform(1, 5))
                                               for i in range(3000)]
    plan = {"dashboard": {"kpis": [{"id": "chg", "measure": "rating", "agg": "change", "label": "Rating change"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "r.csv", "", {"__plan__": plan}, "2026-10-20")["dashboard"]
    k = d["defaults"]["kpis"]["chg"]
    assert d["cube"]["series"] is False and k["period"] and len(k["period"]) == 4, k            # by year, not "2021-12"
    rows = ["order_id,order_date,amount"] + ["O%04d,2024-%02d-%02d,%.2f" % (i, 1 + i % 6, 1 + i % 28, 10 + rnd.uniform(0, 7)) for i in range(600)] + \
        ["P%04d,2024-07-%02d,10" % (i, 1 + i % 9) for i in range(20)]
    plan = {"dashboard": {"kpis": [{"id": "chg", "measure": "amount", "agg": "change", "label": "Amount change"},
                                   {"id": "lo", "measure": "amount", "agg": "low", "label": "Lowest month"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "o.csv", "", {"__plan__": plan}, "2026-10-20")["dashboard"]
    k, lo = d["defaults"]["kpis"]["chg"], d["defaults"]["kpis"]["lo"]
    assert d["cube"]["last_month_full"] is False and k["period"] == "2024-06" and k["base_period"] == "2024-05", k
    assert lo["period"] != "2024-07", lo                                                          # 9 days are never the "lowest month"


def test_a_kept_category_a_scatter_and_questions_on_visuals():
    """20 Oct 2026, live on penguins: a kept "sex" column (flagged by its header) was refused on every chart; a 344-point scatter was
    refused at 200 rows; questions answered by the AI's visuals were refused as answering nothing. A refused visual's question goes."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "palmer-penguins.csv"), "rb").read()
    sc = {"mark": "point", "encoding": {"x": {"field": "flipper_length_mm", "type": "quantitative"},
                                        "y": {"field": "body_mass_g", "type": "quantitative"}, "color": {"field": "species", "type": "nominal"}}}
    plan = {"goal": "penguins", "kind": "other", "dashboard": {
        "kpis": [{"id": "n", "measure": "rows", "agg": "count", "label": "Penguins"}],
        "questions": [{"q": "How do flippers relate to mass?", "answered_by": ["scat"]}, {"q": "Which sex is heavier?", "answered_by": ["bysex"]},
                      {"q": "What of the ghost?", "answered_by": ["ghost"]}],
        "charts": [{"id": "bysex", "chart": "bar_chart", "args": ["sex", "body_mass_g"], "title": "Mass by sex"}],
        "visuals": [{"id": "scat", "title": "Flipper length against body mass", "sql": "SELECT flipper_length_mm, body_mass_g, species FROM kept_rows", "spec": sc},
                    {"id": "ghost", "title": "Bad", "sql": "SELECT nope FROM kept_rows", "spec": {"mark": "bar", "encoding": {"x": {"field": "nope", "type": "nominal"}}}}]}}
    d = NB.run(data, "p.csv", "", {"sex": "keep", "__plan__": plan}, None)["dashboard"]
    assert [c["id"] for c in d["charts"]] == ["bysex"], d["refused"]
    assert [(v["id"], v["rows_total"]) for v in d["visuals"]] == [("scat", 344)], d.get("visuals")
    assert [q["answered_by"] for q in d["questions"]] == [["scat"], ["bysex"]], d["questions"]
    assert any("ghost" in r["element"] for r in d["refused"]), d["refused"]
    # withheld, the same column stays out
    d2 = NB.run(data, "p.csv", "", {"sex": "withhold", "__plan__": plan}, None)["dashboard"]
    assert not any(c["id"] == "bysex" for c in d2["charts"]), d2["charts"]


def test_a_trend_that_names_the_files_own_date_draws_the_row_count():
    """20 Oct 2026, live on NYC 311 and Chicago crimes: "requests per month" passed the date column as the line's measure and was
    refused, so two daily files had no trend at all. The file's own date is the time axis; with nothing else the line counts rows."""
    import random
    import datetime as _d
    random.seed(3)
    rows = ["created_date,agency,complaint"]
    for _i in range(900):
        day = _d.date(2023, 1, 1) + _d.timedelta(days=random.randrange(730))
        rows.append("%s,%s,%s" % (day.isoformat(), random.choice(["NYPD", "DOT", "HPD"]), random.choice(["Noise", "Heat", "Parking"])))
    plan = {"goal": "requests", "kind": "other", "dashboard": {
        "kpis": [{"id": "n", "measure": "rows", "agg": "count", "label": "Requests"}],
        "charts": [{"id": "trend", "chart": "line_chart", "args": ["created_date"], "title": "Requests per month"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "r.csv", "", {"__plan__": plan}, None)["dashboard"]
    assert [c["id"] for c in d["charts"]] == ["trend"], d["refused"]


def test_an_indicator_table_compares_one_characteristic_at_a_time():
    """20 Oct 2026, live on the 2021 census profile (14 places x 1,624 characteristics): with no AI design the engine's own
    dashboard added C1_COUNT_TOTAL over every characteristic and every place, Canada included, into 6.4 billion, and charted it
    by DGUID. Each row measures a different thing: each chart is one characteristic by place, and a filtered value keeps its level."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "statcan-census-profile-2021-provinces.csv"), "rb").read()
    d = NB.run(data, "c.csv", "", {"__plan__": {"goal": "read it", "kind": "other"}}, None)["dashboard"]
    assert [k["id"] for k in d["kpis"]] == ["rows"], d["kpis"]
    assert d["charts"] and all(c["id"].startswith("ind_") and c["where"]["column"] == "CHARACTERISTIC_NAME" for c in d["charts"]), \
        [(c["id"], c.get("where")) for c in d["charts"]]
    assert not any("counted in" in r["why"] for r in d["refused"]), d["refused"]
    assert any(r["element"].startswith("the totals of") for r in d["refused"]), d["refused"]
    assert all(f["column"] != "DGUID" for f in d["filters"]), d["filters"]
    assert all(" has the highest " in q["q"] and " Average" not in q["q"] for q in d["questions"]), d["questions"]


def test_a_visual_names_the_year_the_plan_read_as_the_date():
    """20 Oct 2026, live on OWID: the plan's date_from_year dropped "year" for "date", and the AI's visual, written against the
    file's own headers, asked for year and was refused. The year stays a column by its own name in the kept rows."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "owid-co2-panel.csv"), "rb").read()
    sc = {"mark": "point", "encoding": {"x": {"field": "gdp_pc", "type": "quantitative"}, "y": {"field": "co2_pc", "type": "quantitative"}}}
    plan = {"goal": "co2", "kind": "other", "operations": [{"op": "date_from_year", "column": "year"}], "dashboard": {
        "kpis": [{"id": "n", "measure": "rows", "agg": "count", "label": "Country-years"}],
        "visuals": [{"id": "pc", "title": "Per person against GDP", "spec": sc,
                     "sql": "SELECT country, co2_per_capita AS co2_pc, gdp / population AS gdp_pc FROM kept_rows "
                            "WHERE year = 2018 AND gdp IS NOT NULL AND population > 0"}]}}
    d = NB.run(data, "owid.csv", "", {"__plan__": plan}, None)["dashboard"]
    assert [(v["id"], v["rows_total"] > 100) for v in d.get("visuals") or []] == [("pc", True)], d["refused"]


def test_a_peak_tile_on_records_never_claims_a_single_reading():
    """20 Oct 2026, live on the Fremont Bridge's hourly counts: the AI labelled a peak tile "Busiest single reading" and it showed
    1,187,146 cyclists, a month's total. On a file of records the peak is a period's; a label naming one reading is replaced."""
    import datetime as _d
    rows = ["Date,Total"]
    t0 = _d.datetime(2024, 1, 1)
    for h in range(24 * 120):
        rows.append("%s,%d" % ((t0 + _d.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"), 50 + (h % 24) * 7))
    plan = {"goal": "bikes", "kind": "other", "dashboard": {"kpis": [
        {"id": "pk", "measure": "Total", "agg": "peak", "label": "Busiest single reading", "unit_word": "cyclists"},
        {"id": "mo", "measure": "Total", "agg": "peak", "label": "Record month", "unit_word": "cyclists"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "b.csv", "", {"__plan__": plan}, None)["dashboard"]
    lab = {k["id"]: k["label"] for k in d["kpis"]}
    assert lab.get("pk") == "Highest Total" and lab.get("mo") == "Record month", (lab, d["refused"])
    assert any("single reading" in r["why"] for r in d["refused"]), d["refused"]


def test_a_trend_split_by_a_category_counts_and_a_filter_reads_dogs_as_dog():
    """20 Oct 2026, live on Austin's animal shelter outcomes: "Outcome mix over time" named only animal_type and was refused as "not a
    number column", and the "Dog outcomes" tile was refused because the AI wrote the value in another case. A line or area split by a
    category alone counts the rows of each; a filter value matches the file's own level whatever its case, or a plural s."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "austin-animal-center-outcomes.csv"), "rb").read()
    plan = {"goal": "outcomes", "kind": "other", "dashboard": {
        "kpis": [{"id": "dogs", "measure": "rows", "agg": "count", "label": "Other outcomes", "where": {"column": "animal_type", "values": ["others"]}}],
        "charts": [{"id": "mix", "chart": "area_chart", "args": ["animal_type"], "title": "Outcome mix over time"},
                   {"id": "lines", "chart": "line_chart", "args": ["datetime", "animal_type"], "title": "Outcomes by animal type"}]}}
    d = NB.run(data, "a.csv", "", {"__plan__": plan}, None)["dashboard"]
    assert [k["id"] for k in d["kpis"]] == ["dogs"] and [v.lower() for v in d["kpis"][0]["where"]["values"]] == ["other"], (d["kpis"], d["refused"])
    # the name column kept, as the page keeps every flagged column (owner, 21 Oct 2026: nothing withheld): seven pets named Dog no
    # longer hide the animal type "Dog"; withheld, they still do
    plan["dashboard"]["kpis"][0]["where"]["values"] = ["dogs"]
    kept = NB.run(data, "a.csv", "", {"name": "keep", "__plan__": plan}, None)["dashboard"]
    assert [v.lower() for v in kept["kpis"][0]["where"]["values"]] == ["dog"], (kept["kpis"], kept["refused"])
    held = NB.run(data, "a.csv", "", {"name": "withhold", "__plan__": plan}, None)["dashboard"]
    assert not held["kpis"] or held["kpis"][0]["id"] != "dogs" or "Dog" not in held["kpis"][0]["where"]["values"], held["kpis"]
    got = {c["id"]: (c["draw"], c["agg"]) for c in d["charts"]}
    assert got.get("mix") == ("area", "count") and got.get("lines") == ("line", "count"), (got, d["refused"])


def test_a_fraction_shown_in_percent_is_on_the_percent_scale():
    """20 Oct 2026, live on college majors: the AI tiled Unemployment_rate (0.0697 on average, a fraction) in "%", and the
    dashboard printed "0.07%"; ShareWomen 0.52 became "0.52%". A measure in "%" is read as the engine reads a percentage
    (fractions when 95% of the bare values lie between 0 and 1), so the tiles say 6.97% and 52%; a percent column stays as it is."""
    f = os.path.join(os.path.dirname(SITE), "corpus", "universal", "college-majors-earnings-ranked.csv")
    plan = {"goal": "majors", "kind": "other", "dashboard": {"kpis": [
        {"id": "u", "measure": "Unemployment_rate", "agg": "average", "label": "Average unemployment rate", "unit_word": "%"},
        {"id": "w", "measure": "ShareWomen", "agg": "average", "label": "Average share women", "unit_word": "%"},
        {"id": "m", "measure": "Median", "agg": "average", "label": "Average median earnings", "unit_word": "USD"}]}}
    d = NB.run(open(f, "rb").read(), "c.csv", "", {"__plan__": plan}, None)["dashboard"]
    import nl_dashboard as ND
    v = {k["id"]: ND.query_kpi(d["cube"], k)["value"] for k in d["kpis"]}
    assert 5 < v["u"] < 9 and 45 < v["w"] < 60 and 35000 < v["m"] < 45000, (v, d["refused"])


def test_a_latest_total_is_never_labelled_an_average():
    """20 Oct 2026, live on international football results: "Latest month's average home goals" showed 1,334 goals, the latest month's
    total of a flow. A latest, peak or low tile of a flow labelled as an average is relabelled to the total it is; an average tile stays."""
    f = os.path.join(os.path.dirname(SITE), "corpus", "universal", "international-football-results.csv")
    plan = {"goal": "goals", "kind": "other",
            "columns": [{"name": "home_score", "semantic_type": "count", "role": "target", "unit": "goals"}], "dashboard": {"kpis": [
        {"id": "l", "measure": "home_score", "agg": "latest", "label": "Latest month's average home goals", "unit_word": "goals"},
        {"id": "a", "measure": "home_score", "agg": "average", "label": "Average home goals per match", "unit_word": "goals"}]}}
    d = NB.run(open(f, "rb").read(), "f.csv", "", {"__plan__": plan}, None)["dashboard"]
    lab = {k["id"]: k["label"] for k in d["kpis"]}
    assert lab == {"l": "Latest total home_score", "a": "Average home goals per match"}, (lab, d["refused"])


def test_a_busiest_month_tile_reads_the_peak_month_of_rows():
    """20 Oct 2026, live on Chicago crimes: "Busiest month" counted every row (119,887). A rows tile asked for the peak, or named for
    the busiest period, reads the highest month's rows, and says when; the Ask analyst never reads a beat or community area as a total."""
    import random
    import datetime as _d
    random.seed(9)
    rows = ["date,primary_type,beat,community_area"]
    for i in range(1200):
        day = _d.date(2023, 1, 1) + _d.timedelta(days=random.randrange(365))
        rows.append("%s,%s,%d,%d" % (day.isoformat(), random.choice(["THEFT", "BATTERY"]), random.randint(111, 2535), random.randint(1, 77)))
    rows += ["2023-07-%02d,THEFT,%d,25" % (1 + i % 28, 1000 + i) for i in range(300)]      # July is the busiest month
    plan = {"goal": "crime", "kind": "other", "dashboard": {"kpis": [
        {"id": "b", "measure": "rows", "agg": "count", "label": "Busiest month"},
        {"id": "p", "measure": "rows", "agg": "peak", "label": "Peak"},
        {"id": "n", "measure": "rows", "agg": "count", "label": "Reported incidents"}]}}
    rep = NB.run(("\n".join(rows) + "\n").encode(), "c.csv", "", {"__plan__": plan}, None)
    d = rep["dashboard"]
    import nl_dashboard as ND
    got = {k["id"]: ND.query_kpi(d["cube"], k) for k in d["kpis"]}
    assert got["b"]["value"] == got["p"]["value"] > 300 and got["b"]["value"] < got["n"]["value"], {k: v["value"] for k, v in got.items()}
    assert str(got["b"]["period"]).startswith("2023-07"), got["b"]
    assert {k["id"]: k["label"] for k in d["kpis"]}["b"] == "Busiest month", d["kpis"]      # a year of data reads by month
    measured = {str(m.get("column") if isinstance(m, dict) else m) for m in (rep["analyst"].get("shape") or {}).get("measures") or []}
    assert not ({"beat", "community_area"} & measured), measured


def test_a_busiest_month_tile_over_eleven_years_says_year():
    """20 Oct 2026, live on Chicago crimes (2004-2015): "Busiest month" read the busiest year, 72,885 in 2014, because a tile reads the
    charts' step over the whole range. The label names the step it reads; a peak over a year of data keeps "month" (the test above)."""
    import random
    import datetime as _d
    random.seed(4)
    rows = ["date,primary_type,location"] + ["%s,%s,%s" % ((_d.datetime(2004, 1, 1) + _d.timedelta(minutes=random.randrange(4000 * 1440))).strftime("%Y-%m-%d %H:%M"),
                                                         random.choice(["THEFT", "BATTERY", "ASSAULT"]),
                                                         random.choice(["STREET", "RESIDENCE", "SIDEWALK", "STORE"])) for _ in range(12000)]
    plan = {"goal": "crime", "kind": "other", "dashboard": {"kpis": [{"id": "b", "measure": "rows", "agg": "peak", "label": "Busiest month"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "c.csv", "", {"__plan__": plan}, None)["dashboard"]
    assert not d["cube"].get("series"), "the file must read as records"
    assert d["kpis"][0]["label"] == "Busiest year" and len(str(d["defaults"]["kpis"]["b"]["period"])) == 4, (d["kpis"], d["defaults"])


def test_owid_co2_reads_in_the_publishers_million_tonnes():
    """20 Oct 2026, live: OWID's co2 (million tonnes, by its codebook) was tiled "37,398 tonnes" because the AI wrote "tonnes", through
    two prompt changes. The engine knows Our World in Data's CO2 file by its headers and uses the codebook's unit; per person stays
    tonnes per person; another file's units are the plan's."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "owid-co2-panel.csv"), "rb").read()
    plan = {"goal": "co2", "kind": "other", "operations": [{"op": "date_from_year", "column": "year"}],
            "columns": [{"name": "co2", "semantic_type": "flow_amount", "role": "target", "unit": "tonnes"},
                        {"name": "co2_per_capita", "semantic_type": "level", "role": "driver", "unit": "tonnes"}],
            "dashboard": {"kpis": [{"id": "e", "measure": "co2", "agg": "latest", "label": "Latest emissions", "unit_word": "tonnes"},
                                   {"id": "p", "measure": "co2_per_capita", "agg": "average", "label": "Per person", "unit_word": "tonnes"}]}}
    d = NB.run(data, "owid.csv", "", {"__plan__": plan}, None)["dashboard"]
    u = {k["id"]: k["unit_word"] for k in d["kpis"]}
    assert u == {"e": "million tonnes", "p": "tonnes per person"}, (u, d["refused"])
    assert NB._owid_unit("share_global_co2") == "%" and NB._owid_unit("gdp") == "" and NB._owid_unit("coal_co2") == "million tonnes"
    other = [{"name": "co2", "unit": "kg"}]
    NB._published_units(other, ["date", "co2", "region"])
    assert other[0]["unit"] == "kg"                        # not Our World in Data's file: the plan's unit stands


def test_owid_planned_without_the_year_step_still_reads_its_years_and_never_sums_a_latest():
    """20 Oct 2026, live: the AI planned the same OWID file without date_from_year; every time tile was refused for having no date,
    and "Latest global emissions" showed every row's co2 added up (1,803,057 million tonnes). A dashboard asking for time on a file
    whose one time column is "year" reads the year as the date; a latest or peak tile that still has no date is dropped, never summed."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "owid-co2-panel.csv"), "rb").read()
    plan = {"goal": "co2", "kind": "other", "columns": [{"name": "co2", "semantic_type": "flow_amount", "role": "target", "unit": "tonnes"},
                                                        {"name": "country", "semantic_type": "entity", "role": "entity", "unit": ""}],
            "dashboard": {"kpis": [{"id": "w", "measure": "co2", "agg": "latest", "label": "Latest global emissions",
                                    "where": {"column": "country", "values": ["World"]}}],
                          "charts": [{"id": "t", "chart": "line_chart", "args": ["co2"], "title": "Emissions over time",
                                      "where": {"column": "country", "values": ["World"]}}]}}
    rep = NB.run(data, "owid.csv", "", {"__plan__": plan}, None)
    d = rep["dashboard"]
    import nl_dashboard as ND
    k = {x["id"]: ND.query_kpi(d["cube"], x) for x in d["kpis"]}
    assert "w" in k and 30000 < k["w"]["value"] < 45000 and str(k["w"]["period"]).startswith("2024"), (k, d["refused"])
    assert [c["id"] for c in d["charts"]] == ["t"], d["refused"]
    # no year column the plan can read: the latest tile goes rather than adding every row up
    import io
    import pandas as pd
    f = pd.read_csv(io.BytesIO(data), usecols=["country", "co2"], nrows=3000).dropna()
    d2 = NB.run(f.to_csv(index=False).encode(), "noyear.csv", "", {"__plan__": plan}, None)["dashboard"]
    assert not any(x["id"] == "w" for x in d2["kpis"]), d2["kpis"]


def test_a_police_district_number_is_a_category_on_the_dashboard():
    """20 Oct 2026, live on Chicago crimes: "Incidents by police district" was refused, "district is not a category column", once district
    was (rightly) no measure. A number that is a label (district, ZIP code, ward, beat, community area) is a category to split by."""
    import random
    random.seed(2)
    rows = ["date,primary_type,district,amount"] + ["2024-%02d-%02d %02d:%02d,%s,%d,%d" % (
        1 + i % 12, 1 + i % 28, i % 24, i % 60, random.choice(["THEFT", "BATTERY"]), random.randint(1, 25), random.randint(1, 900))
        for i in range(2000)]
    plan = {"goal": "crime", "kind": "other", "dashboard": {"charts": [
        {"id": "d", "chart": "bar_chart", "args": ["district"], "title": "Incidents by police district"},
        {"id": "a", "chart": "bar_chart", "args": ["amount"], "title": "Not a category"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "c.csv", "", {"__plan__": plan}, None)["dashboard"]
    assert "d" in [c["id"] for c in d["charts"]], d["refused"]
    assert "a" not in [c["id"] for c in d["charts"]], d["charts"]


def test_a_true_false_column_gives_a_share_tile():
    """20 Oct 2026, live on Chicago crimes: "Share domestic" was refused, "domestic is not a number column". A true/false column as a
    tile's measure is the share of rows that are true, in %, averaged; it stays a category to split a chart by."""
    import random
    random.seed(6)
    rows = ["date,primary_type,domestic"] + ["2024-%02d-%02d %02d:%02d,%s,%s" % (1 + i % 12, 1 + i % 28, i % 24, i % 60,
                                              random.choice(["THEFT", "BATTERY"]), "true" if i % 4 == 0 else "false") for i in range(2000)]
    plan = {"goal": "crime", "kind": "other", "dashboard": {
        "kpis": [{"id": "s", "measure": "domestic", "agg": "sum", "label": "Share domestic", "unit_word": "incidents"}],
        "charts": [{"id": "b", "chart": "bar_chart", "args": ["domestic"], "title": "Incidents by domestic"}]}}
    d = NB.run(("\n".join(rows) + "\n").encode(), "c.csv", "", {"__plan__": plan}, None)["dashboard"]
    import nl_dashboard as ND
    k = {x["id"]: x for x in d["kpis"]}
    assert "s" in k and k["s"]["unit_word"] == "%" and k["s"]["agg"] == "average", (d["kpis"], d["refused"])
    uniq = set(rows[1:])                                   # the engine reads an exact repeat of a row as one row
    want = 100.0 * sum(r.endswith(",true") for r in uniq) / len(uniq)
    assert abs(ND.query_kpi(d["cube"], k["s"])["value"] - want) < 0.05, (ND.query_kpi(d["cube"], k["s"]), want)
    assert "b" in [c["id"] for c in d["charts"]], d["refused"]


def test_owid_world_and_continent_rows_are_never_added_to_the_countries():
    """20 Oct 2026, live: "Latest global emissions 88,498 million tonnes" (the world's 2024 figure is about 38,600): the tile added a year's
    rows with World, the continents and the income groups among them. OWID's aggregates (blank iso_code) are set aside; a total is the
    countries' total, and a filter to World reads it."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "owid-co2-panel.csv"), "rb").read()
    plan = {"goal": "co2", "kind": "other", "operations": [{"op": "date_from_year", "column": "year"}],
            "columns": [{"name": "co2", "semantic_type": "flow_amount", "role": "target", "unit": "million tonnes"},
                        {"name": "country", "semantic_type": "entity", "role": "entity", "unit": ""}],
            "dashboard": {"kpis": [{"id": "all", "measure": "co2", "agg": "latest", "label": "Latest global emissions"},
                                   {"id": "w", "measure": "co2", "agg": "latest", "label": "World",
                                    "where": {"column": "country", "values": ["World"]}}]}}
    d = NB.run(data, "owid.csv", "", {"__plan__": plan}, None)["dashboard"]
    import nl_dashboard as ND
    v = {k["id"]: ND.query_kpi(d["cube"], k)["value"] for k in d["kpis"]}
    assert 36000 < v["all"] < 39000 and v["w"] == v["all"], (v, d["refused"])


def test_a_rows_change_tile_compares_two_periods_never_the_total():
    """21 Oct 2026, live on Austin's shelter: "Outcomes, latest month vs a year earlier" showed 49,991, every row. A rows tile asked for
    the change reads the latest period's rows against the same period a year earlier."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "austin-animal-center-outcomes.csv"), "rb").read()
    plan = {"goal": "outcomes", "kind": "other", "dashboard": {"kpis": [
        {"id": "c", "measure": "rows", "agg": "change", "vs": "year", "label": "Outcomes, latest month vs a year earlier"},
        {"id": "n", "measure": "rows", "agg": "count", "label": "Outcomes"}]}}
    d = NB.run(data, "a.csv", "", {"__plan__": plan}, None)["dashboard"]
    import nl_dashboard as ND
    k = {x["id"]: ND.query_kpi(d["cube"], x) for x in d["kpis"]}
    assert k["c"]["base"] is not None and k["c"]["period"] and k["c"]["base_period"], k["c"]
    assert abs(k["c"]["value"]) < k["n"]["value"] / 4, k


def test_a_trend_naming_a_second_date_column_reads_the_files_date():
    """21 Oct 2026, live on Austin's shelter: "Outcomes by month and year" named monthyear (month labels, "05-2025", beside datetime)
    and outcome_type, and was refused as "not a number column". A second date column is the same time axis: the chart counts each
    outcome type's rows over time."""
    data = open(os.path.join(os.path.dirname(SITE), "corpus", "universal", "austin-animal-center-outcomes.csv"), "rb").read()
    plan = {"goal": "outcomes", "kind": "other", "dashboard": {"charts": [
        {"id": "m", "chart": "line_chart", "args": ["monthyear", "outcome_type"], "title": "Outcomes by month and year"}]}}
    rep = NB.run(data, "a.csv", "", {"__plan__": plan}, None)
    d = rep["dashboard"]
    got = {c["id"]: (c["draw"], c["agg"]) for c in d["charts"]}
    assert got.get("m") == ("line", "count"), (got, d["refused"])


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
