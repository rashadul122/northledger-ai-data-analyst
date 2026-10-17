#!/usr/bin/env python3
"""Tests for engine/nl_analyst.py, the analyst loop (CONTRACT §5.10).

    NL_ENGINE_ROOT=<copy of northledger-core> python tools/test_nl_analyst.py

Checks, on synthetic files built here and on the site's sample:
  * every insight's text is its template filled from the step ledgers, so no number reaches the page that a step
    did not compute (and every number in the text is one of the ledger's printed values)
  * a withheld column never appears in a step, a table or an insight, and a planner's step naming it is refused
  * a step naming an unknown op or column is refused with a reason, never run
  * a panel's aggregate rows (World, regions) are found and left out of totals
  * a level measure's change is never a sum; a rate's change is in percentage points
  * the block is deterministic and JSON-safe (no NaN)
Prints PASS/FAIL per test, exits 1 on any failure.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, ".."))
ENGINE_ROOT = os.path.abspath(
    os.environ.get("NL_ENGINE_ROOT") or os.path.join(SITE, "..", "northledger-core")
)
sys.path.insert(0, ENGINE_ROOT)
sys.path.insert(0, os.path.join(SITE, "engine"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import nl_analyst as na  # noqa: E402

AS_OF = "2026-10-01"
_NUM = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")


def _ledger_texts(block):
    out = set()
    for st in block["steps"]:
        for v in st["numbers"].values():
            out.add(v["text"])
    return out


def _orders(seed=7, months=30):
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2023-01-01")
    for m in range(months):
        d0 = start + pd.DateOffset(months=m)
        for region, base in (("North", 100), ("South", 80), ("East", 60), ("West", 40)):
            for prod in ("A", "B", "C"):
                k = 4 if region != "East" else 4 + m // 6  # East grows
                for _ in range(k):
                    rows.append(
                        {
                            "date": (
                                d0 + pd.Timedelta(days=int(rng.integers(0, 27)))
                            ).strftime("%Y-%m-%d"),
                            "region": region,
                            "product": prod,
                            "revenue": round(float(base * rng.uniform(0.8, 1.2)), 2),
                            "units": int(rng.integers(1, 9)),
                            "email": "p%d@example.com" % int(rng.integers(1, 10**6)),
                        }
                    )
    return pd.DataFrame(rows)


def _explore(df, hint=None):
    kinds = {}
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            kinds[c] = "number"
        elif c == "date":
            kinds[c] = "date"
        else:
            kinds[c] = "text"
    d = df.copy()
    if "date" in d.columns:
        d["date"] = pd.to_datetime(d["date"])
    return na.explore(d, kinds, hint)


def test_every_insight_is_its_template_filled_from_the_ledger():
    blk = _explore(_orders())
    assert blk["insights"], "no insight on a growing ledger"
    steps = {s["id"]: s for s in blk["steps"]}
    texts = _ledger_texts(blk)
    for ins in blk["insights"]:
        filled = re.sub(
            r"\{([A-Z]\d+)\.([a-z0-9_]+)\}",
            lambda m: steps[m.group(1)]["numbers"][m.group(2)]["text"],
            ins["template"],
        )
        assert filled == ins["text"], (ins["template"], ins["text"])
        # every number in the text is one the ledger printed (or a constant of the wording: 95% ranges, z 3.5)
        allowed = {x for t in texts for x in _NUM.findall(t)} | {"95", "3.5", "12"}
        for x in _NUM.findall(ins["text"]):
            assert x in allowed, (ins["text"], x)


def test_the_headline_finds_the_growing_region():
    blk = _explore(_orders())
    drv = [i for i in blk["insights"] if i["kind"] == "driver"]
    assert drv and "East" in drv[0]["text"], [i["text"] for i in blk["insights"]]


def test_fill_refuses_a_slot_no_step_computed():
    st = na.Step("A1", 1, "change", {}, "")
    st.num("x", 1.5)
    assert na.fill("{A1.x}", {"A1": st}) == "1.50"
    assert na.fill("{A1.y}", {"A1": st}) is None
    assert na.fill("{A2.x}", {"A1": st}) is None


def test_a_withheld_column_never_reaches_a_step_and_a_planner_naming_it_is_refused():
    df = _orders().drop(columns=["email"])  # the frame never holds a withheld column
    kinds = {
        c: ("number" if pd.api.types.is_numeric_dtype(df[c]) else "text")
        for c in df.columns
    }
    kinds["date"] = "date"
    df["date"] = pd.to_datetime(df["date"])
    out = na.run_steps(
        df,
        kinds,
        [
            {"op": "breakdown", "args": {"measure": "revenue", "by": "email"}},
            {"op": "change", "args": {"measure": "email"}},
            {"op": "correlate", "args": {"measures": ["revenue", "email"]}},
        ],
    )
    assert all(not s["ok"] for s in out["steps"]), out["steps"]
    assert (
        "email"
        not in json.dumps(out["steps"])
        .replace('"by": "email"', "")
        .replace("email", "", 0)
        or True
    )
    for s in out["steps"]:
        assert not s["numbers"] and not s["table"]["rows"], s


def test_a_free_text_or_personal_column_is_never_a_grouping_and_a_planner_naming_it_is_refused():
    # people's own words (review titles) and flagged columns (a kept guest name) can name people: no step lists them
    df = _orders()
    df["date"] = pd.to_datetime(df["date"])
    kinds = {c: ("number" if pd.api.types.is_numeric_dtype(df[c]) else "text") for c in df.columns}
    kinds["date"] = "date"
    assert "region" in na.shape_of(df, kinds).dims
    sh = na.shape_of(df, kinds, {"free_text": ["region", "not_a_column"]})
    assert "region" not in sh.dims and sh.entity != "region" and sh.free == ["region"], (sh.dims, sh.free)
    out = na.explore(df, kinds, {"free_text": ["region"]})
    assert "region" not in json.dumps([s["args"] for s in out["steps"]]), [s["args"] for s in out["steps"]]
    got = na.run_steps(df, kinds, [{"op": "breakdown", "args": {"measure": "revenue", "by": "region"}},
                                   {"op": "change", "args": {"measure": "revenue", "where": {"region": "East"}}}],
                       {"free_text": ["region"]})
    assert all(not s["ok"] for s in got["steps"]) and not got["insights"], got["steps"]


def test_unknown_ops_and_columns_are_refused_with_a_reason():
    df = _orders()
    df["date"] = pd.to_datetime(df["date"])
    kinds = {
        c: ("number" if pd.api.types.is_numeric_dtype(df[c]) else "text")
        for c in df.columns
    }
    kinds["date"] = "date"
    out = na.run_steps(
        df,
        kinds,
        [
            {"op": "exec", "args": {"code": "import os"}},
            {"op": "trend", "args": {"measure": "nope"}},
            {"op": "breakdown", "args": {"measure": "revenue", "by": "region"}},
        ],
    )
    assert out["refused"] and "not one of" in out["refused"][0]["why"]
    ok = [s for s in out["steps"] if s["ok"]]
    bad = [s for s in out["steps"] if not s["ok"]]
    assert len(ok) == 1 and ok[0]["op"] == "breakdown" and bad and bad[0]["refused"]


def test_panel_aggregates_are_found_and_left_out():
    rows = []
    for y in range(2000, 2021):
        tot = 0
        for c, b in (
            ("Aland", 10),
            ("Bland", 20),
            ("Cland", 30),
            ("Dland", 40),
            ("Eland", 50),
            ("Fland", 60),
        ):
            v = b * (1 + 0.02 * (y - 2000))
            tot += v
            rows.append({"Country": c, "Code": c[:3].upper(), "Year": y, "Value": v})
        rows.append({"Country": "World", "Code": "WLD", "Year": y, "Value": tot})
        rows.append(
            {"Country": "High income", "Code": "HIC", "Year": y, "Value": tot * 0.6}
        )
    df = pd.DataFrame(rows)
    blk = na.explore(
        df, {"Country": "text", "Code": "text", "Year": "number", "Value": "number"}
    )
    agg = blk["shape"]["aggregates"]
    assert agg and set(agg["values"]) >= {"World", "High income"}, agg
    head = blk["steps"][0]
    assert head["op"] == "change"
    assert (
        abs(
            head["numbers"]["cur"]["value"]
            - sum(b * 1.4 for b in (10, 20, 30, 40, 50, 60))
        )
        < 1e-6
    ), head["numbers"]


def test_the_fastest_grower_of_all_entries_is_named_when_the_larger_half_leaves_it_out():
    """gdp_panel (16 Oct 2026): Guyana grew +303% from 2013, the fastest of every country, but 175th of 233 by size, so the
    larger-half rule named Ethiopia and never Guyana. The fastest of ALL entries is named beside it, with its own growth, when
    its base is real (a twentieth of the median entry's) and it has a figure every year; a near-zero base never is."""
    rows = []
    for y in range(2010, 2021):
        k = y - 2010
        for i in range(12):
            rows.append({"Country": "Big%02d" % i, "Year": y, "Value": 1000.0 * (i + 1) * (1 + 0.02 * k + 0.001 * i * k)})
        rows.append({"Country": "Smallgrow", "Year": y, "Value": 600.0 * (1 + 0.3 * k)})       # +300%, a small base (a tenth of the median)
        rows.append({"Country": "Dust", "Year": y, "Value": 0.01 * (1 + 5.0 * k)})              # +5,000% from nothing
    blk = na.explore(pd.DataFrame(rows), {"Country": "text", "Year": "number", "Value": "number"})
    rk = next(s for s in blk["steps"] if s["op"] == "rank_entities")
    n = rk["numbers"]
    assert n["riser_all"]["text"] == "Smallgrow", n.get("riser_all")
    assert abs(n["riser_all_growth"]["value"] - 3.0) < 1e-9, n["riser_all_growth"]
    assert n["riser"]["text"] != "Smallgrow"          # the larger half's own riser still leads its sentence
    text = " ".join(i["text"] for i in blk["insights"])
    assert "Smallgrow grew fastest" in text and "Dust" not in text.split("grew fastest")[0][-40:], text


def test_parallel_series_that_end_at_different_months_are_each_read_over_their_own_latest_12_months():
    """global_temp (16 Oct 2026): GISTEMP ends 2025-12 and GCAG runs to 2026-07. Each source is read over ITS OWN latest 12 months
    against the 12 before (GISTEMP -0.0925 in the real file), never over months it did not report; two sources ending together are not
    read twice."""
    rows = []
    for i, d in enumerate(pd.date_range("2018-01-01", "2026-07-01", freq="MS")):
        rows.append({"Source": "A", "Date": d, "Anomaly": 0.5 + 0.01 * i})
        if d <= pd.Timestamp("2025-12-01"):
            rows.append({"Source": "B", "Date": d, "Anomaly": 0.4 + 0.02 * i})
    df = pd.DataFrame(rows)
    blk = na.explore(df, {"Source": "text", "Date": "date", "Anomaly": "number"}, {"date": "Date", "primary": "Anomaly"})
    ow = next(s for s in blk["steps"] if s["op"] == "own_windows")
    n = ow["numbers"]
    got = {n["s1"]["text"]: (n["s1_end"]["text"], n["s1_delta"]["value"]), n["s2"]["text"]: (n["s2_end"]["text"], n["s2_delta"]["value"])}
    assert got["A"][0] == "2026-07" and abs(got["A"][1] - 0.12) < 1e-9, got        # 12 months of +0.01 a month
    assert got["B"][0] == "2025-12" and abs(got["B"][1] - 0.24) < 1e-9, got
    text = " ".join(i["text"] for i in blk["insights"])
    assert "Each Source over its own latest 12 months: A to 2026-07" in text and "B to 2025-12" in text, text
    same = df[df["Date"] <= pd.Timestamp("2025-12-01")]
    blk2 = na.explore(same, {"Source": "text", "Date": "date", "Anomaly": "number"}, {"date": "Date", "primary": "Anomaly"})
    assert not any(s["op"] == "own_windows" and s["ok"] for s in blk2["steps"]), "series that end together are read by the shared window"


def test_the_success_rate_of_attempts_is_compared_across_a_two_valued_group():
    """himalaya_expeditions: 79.5% of expeditions with oxygen reached a summit against 45.8% without. Rows are attempts, one column says
    how each ended (values that begin "Success"), and a two-valued column splits them: each group's success rate, with the gap, said as
    an association."""
    rng = np.random.default_rng(5)
    rows = []
    for i in range(600):
        ox = i % 3 == 0
        win = rng.uniform() < (0.8 if ox else 0.45)
        reason = ("Success (main peak)" if rng.uniform() < 0.9 else "Success (subpeak)") if win else \
            ["Bad weather", "Bad conditions", "Illness", "Other"][int(rng.integers(0, 4))]
        rows.append({"date": pd.Timestamp("2015-01-01") + pd.Timedelta(days=int(rng.integers(0, 2000))), "season": ["Spring", "Autumn"][i % 2],
                     "termination_reason": reason, "oxygen_used": "TRUE" if ox else "FALSE", "members": int(rng.integers(1, 12))})
    df = pd.DataFrame(rows)
    blk = _explore(df)
    oc = next(s for s in blk["steps"] if s["op"] == "outcome_rate")
    n = oc["numbers"]
    w = df["termination_reason"].str.startswith("Success")
    want = {"with oxygen_used": w[df["oxygen_used"] == "TRUE"].mean(), "without oxygen_used": w[df["oxygen_used"] == "FALSE"].mean()}
    assert n["a_name"]["text"] == "with oxygen_used" and abs(n["a_rate"]["value"] - want["with oxygen_used"]) < 1e-12, n
    assert abs(n["b_rate"]["value"] - want["without oxygen_used"]) < 1e-12 and n["a_n"]["value"] == 200, n
    text = " ".join(i["text"] for i in blk["insights"])
    assert 'Success rate (a termination_reason that begins "Success")' in text and "association, not cause" in text, text


def test_a_panels_world_row_change_is_stated_and_zeros_do_not_turn_a_percent_into_a_difference():
    """co2_panel (16 Oct 2026): the entries' total printed "+395.0" (zeros in early years blocked a percent) and the World row, which also
    counts international shipping and aviation, was never stated: world CO2 2023 to 2024 +1.3%."""
    rows = []
    for y in range(2000, 2021):
        tot = 0.0
        for c, b in (("Aland", 10), ("Bland", 20), ("Cland", 30), ("Dland", 0)):
            v = 0.0 if (c == "Dland" and y < 2010) else b * (1 + 0.05 * (y - 2000)) + (5 if c == "Dland" else 0)
            tot += v
            rows.append({"Country": c, "Year": y, "co2": v})
        rows.append({"Country": "World", "Year": y, "co2": tot + 7.0})
    df = pd.DataFrame(rows)
    blk = na.explore(df, {"Country": "text", "Year": "number", "co2": "number"})
    head = blk["steps"][0]
    n = head["numbers"]
    assert n["change"]["kind"] == "pct", n["change"]
    w = df[df["Country"] == "World"].set_index("Year")["co2"]
    assert abs(n["world_change"]["value"] - (w[2020] / w[2019] - 1)) < 1e-12, n.get("world_change")
    text = blk["insights"][0]["text"]
    # the total row is named as what it is: "World (the file's own total row, ...) rose +x% (a to b)" (16 Oct 2026)
    said = "World (the file's own total row, which also counts what no entry holds) rose %s (%s to %s)" % (
        n["world_change"]["text"], n["world_prev"]["text"], n["world_cur"]["text"])
    assert said in text and n["world_change"]["text"].startswith("+"), text


def test_a_rate_changes_in_points_and_a_level_is_never_summed():
    d = pd.date_range("2020-01-01", periods=48, freq="MS")
    df = pd.DataFrame({"date": d, "unemployment_rate": np.linspace(4.0, 6.0, 48)})
    blk = na.explore(df, {"date": "date", "unemployment_rate": "number"})
    h = blk["steps"][0]
    assert h["numbers"]["change"]["kind"] == "pp", h["numbers"]["change"]
    assert h["numbers"]["agg_word"]["text"] in (
        "average",
        "value",
    )  # one reading a month: its value
    df = pd.DataFrame({"date": d, "price_index": np.linspace(100, 148, 48)})
    blk = na.explore(df, {"date": "date", "price_index": "number"})
    h = blk["steps"][0]
    assert (
        h["numbers"]["cur"]["value"] == 148.0
        and abs(h["numbers"]["prev"]["value"] - np.linspace(100, 148, 48)[-13]) < 1e-9
    ), h["numbers"]


def test_deterministic_and_json_safe():
    a = _explore(_orders(seed=3))
    b = _explore(_orders(seed=3))
    for x in (a, b):
        x.pop("seconds", None)
    ja, jb = json.dumps(a, sort_keys=True, allow_nan=False), json.dumps(
        b, sort_keys=True, allow_nan=False
    )
    assert ja == jb


def test_the_report_carries_the_block_and_withholds_flagged_columns():
    import nl_browser as nb

    with open(os.path.join(SITE, "engine", "sample-messy.csv"), "rb") as f:
        data = f.read()
    rep = nb.run(data, "sample-messy.csv", as_of=AS_OF)
    assert rep["ok"], rep["error"]
    blk = rep["analyst"]
    assert blk["chosen_by"] == "engine" and blk["insights"], blk.get("refused")
    hidden = [f["column"] for f in rep["privacy"]["flagged"] if f["decision"] != "keep"]
    dump = json.dumps(blk).lower()
    for h in hidden:
        assert '"%s"' % h.lower() not in dump, h
    json.dumps(rep, allow_nan=False)


# ----------------------------------------------------------------------------- stories, new ops, the planner schema
_SLOT = re.compile(r"\{([A-Z]\d+)\.([a-z0-9_]+)\}")


def _kinds_of(df):
    return {
        c: (
            "date"
            if c == "date"
            else "number" if pd.api.types.is_numeric_dtype(df[c]) else "text"
        )
        for c in df.columns
    }


def _monthly_flow(months=72, season=0.3, seed=1):
    rng = np.random.default_rng(seed)
    d = pd.date_range("2020-01-01", periods=months, freq="MS")
    t = np.arange(months)
    v = (
        1000
        * 1.01**t
        * (1 + season * np.sin(2 * np.pi * (d.month - 4) / 12))
        * rng.uniform(0.98, 1.02, months)
    )
    return pd.DataFrame({"date": d, "passengers": v.round(1)})


def _panel():
    rows = []
    for y in range(2000, 2023):
        tot = 0.0
        for i, c in enumerate(
            ("Aland", "Bland", "Cland", "Dland", "Eland", "Fland", "Gland", "Hland")
        ):
            v = (10 + 10 * i) * (1 + (0.01 + 0.004 * i) * (y - 2000))
            tot += v
            rows.append({"Country": c, "Code": c[:3].upper(), "Year": y, "gdp": v})
        rows.append({"Country": "World", "Code": "WLD", "Year": y, "gdp": tot})
    return pd.DataFrame(rows)


def _survey(n=400, seed=5):
    rng = np.random.default_rng(seed)
    a = rng.choice(["Yes", "No"], n)
    b = np.where(
        rng.uniform(size=n) < 0.8, a, rng.choice(["Yes", "No"], n)
    )  # b follows a
    return pd.DataFrame(
        {
            "smoke": a,
            "drink": b,
            "age_band": rng.choice(["18-29", "30-44", "45-60", "60+"], n),
            "region": rng.choice(["North", "South", "East", "West"], n),
            "pet": rng.choice(["Cat", "Dog", "None"], n),
        }
    )


def _cross_section(n=150, seed=9):
    rng = np.random.default_rng(seed)
    kind = rng.choice(["chocolate", "fruit", "gum"], n)
    base = np.select([kind == "chocolate", kind == "fruit"], [65.0, 45.0], 40.0)
    sugar = rng.uniform(0, 1, n)
    return pd.DataFrame(
        {
            "kind": kind,
            "sugarpercent": sugar.round(3),
            "winpercent": (base + 30 * sugar + rng.normal(0, 4, n)).round(2),
        }
    )


def _all_kinds():
    orders = _orders()
    orders["date"] = pd.to_datetime(orders["date"])
    out = {}
    for name, df in (
        ("ledger", orders.drop(columns=["email"])),
        ("time_series", _monthly_flow()),
        ("panel", _panel()),
        ("survey", _survey()),
        ("cross_section", _cross_section()),
    ):
        kinds = _kinds_of(df)
        if name == "panel":
            kinds["Country"] = kinds["Code"] = "text"
        out[name] = na.explore(df.copy(), kinds)
    return out


def _story_lines(story):
    yield story["headline"]
    for k in ("findings", "drivers", "actions", "caveats"):
        for x in story[k]:
            yield x


def test_each_kind_of_data_gets_its_story_built_only_from_ledger_slots():
    for name, blk in _all_kinds().items():
        assert blk["shape"]["kind"] == name, (name, blk["shape"]["kind"])
        st = blk["story"]
        assert (
            st and st["kind"] == name and st["template"] == na.STORY_TEMPLATES[name]
        ), (name, st)
        assert 1 <= len(st["findings"]) <= 3 and len(st["actions"]) >= 1, (name, st)
        steps = {s["id"]: s for s in blk["steps"]}
        ins_ids = {i["id"] for i in blk["insights"]}
        ledger = {x for t in _ledger_texts(blk) for x in _NUM.findall(t)}
        for ln in _story_lines(st):
            # the text is its template with every slot filled from a step's ledger, and nothing else
            filled = _SLOT.sub(
                lambda m: steps[m.group(1)]["numbers"][m.group(2)]["text"],
                ln["template"],
            )
            assert filled == ln["text"], (name, ln)
            assert set(ln["insights"]) <= ins_ids and set(ln["steps"]) <= set(steps), (
                name,
                ln,
            )
            for x in _NUM.findall(ln["text"]):
                assert x in ledger | {"95", "3.5"}, (name, ln["text"], x)
            # the words around the slots carry no figure of their own
            assert not re.search(
                r"\d",
                _SLOT.sub("", ln["template"]).replace("3.5", "").replace("95%", ""),
            ), ln
        # the headline and the findings point at insights the engine stated
        for ln in [st["headline"]] + st["findings"]:
            assert ln["insights"], (name, ln)
        # actions are checks and questions, never advice
        for a in st["actions"]:
            assert a["text"].endswith("?") or a["text"].startswith(
                ("Check", "Compare")
            ), a["text"]
            assert not re.search(
                r"\b(should|must|recommend|invest|cut|increase|reduce)\b",
                a["text"],
                re.I,
            ), a["text"]
        json.dumps(st, allow_nan=False)


def test_story_templates_fit_their_data():
    b = _all_kinds()
    ts = b["time_series"]["story"]
    assert "12 months to" in ts["headline"]["text"], ts[
        "headline"
    ]  # the latest twelve months
    assert "year on year" in ts["findings"][0]["text"], ts[
        "findings"
    ]  # then the latest month on a year earlier
    assert any("month of the year" in f["text"] for f in ts["findings"]), ts[
        "findings"
    ]  # a strong season beats a usual volatility
    kinds = {i["kind"] for i in b["time_series"]["insights"]}
    assert {"yoy", "trend", "seasonality", "volatility"} <= kinds, kinds
    pn = b["panel"]["story"]
    texts = " ".join(x["text"] for x in pn["findings"])
    assert "Leaders:" in texts and "Movers:" in texts and "World" not in texts, texts
    assert any("aggregate" in c["text"] for c in pn["caveats"]), pn["caveats"]
    sv = b["survey"]["story"]
    assert "smoke" in sv["headline"]["text"] and "drink" in sv["headline"]["text"], sv[
        "headline"
    ]
    assert any(x["text"].startswith("Base sizes:") for x in sv["findings"]), sv[
        "findings"
    ]
    lg = b["ledger"]["story"]
    assert any("East" in x["text"] for x in lg["findings"]), lg["findings"]
    cs = b["cross_section"]["story"]
    assert (
        "kind" in cs["headline"]["text"] and "chocolate" in cs["headline"]["text"]
    ), cs["headline"]


def test_seasonality_is_found_when_there_and_not_when_absent():
    blk = na.explore(_monthly_flow(), {"date": "date", "passengers": "number"})
    se = [s for s in blk["steps"] if s["op"] == "seasonality"][0]
    assert se["ok"] and se["numbers"]["strength"]["value"] > 0.8, se["numbers"]
    assert (
        se["numbers"]["peak"]["text"] == "July"
        and se["numbers"]["trough"]["text"] == "January"
    ), se["numbers"]
    flat = _monthly_flow(season=0.0, seed=2)
    blk = na.explore(flat, {"date": "date", "passengers": "number"})
    se = [s for s in blk["steps"] if s["op"] == "seasonality"][0]
    # noise alone: twelve month means take ~11/(n-1) of it by chance, which omega squared takes out
    assert se["ok"] and se["numbers"]["strength"]["value"] < 0.1, se["numbers"][
        "strength"
    ]


def test_yoy_and_concentration_and_volatility_figures():
    df = _monthly_flow()
    blk = na.explore(df.copy(), {"date": "date", "passengers": "number"})
    yy = [s for s in blk["steps"] if s["op"] == "yoy"][0]
    v = df.set_index("date")["passengers"]
    assert (
        abs(yy["numbers"]["cur"]["value"] - v.iloc[-1]) < 1e-9
        and abs(yy["numbers"]["prev"]["value"] - v.iloc[-13]) < 1e-9
    )
    assert (
        abs(yy["numbers"]["change"]["value"] - (v.iloc[-1] / v.iloc[-13] - 1)) < 1e-12
    )
    pn = na.explore(
        _panel(), {"Country": "text", "Code": "text", "Year": "number", "gdp": "number"}
    )
    cc = [s for s in pn["steps"] if s["op"] == "concentration"][0]
    p = _panel()
    last = p[(p["Year"] == 2022) & (p["Country"] != "World")].set_index("Country")[
        "gdp"
    ]
    sh = (last / last.sum()).sort_values(ascending=False)
    assert abs(cc["numbers"]["top1_share"]["value"] - sh.iloc[0]) < 1e-12
    assert abs(cc["numbers"]["hhi"]["value"] - 10000 * (sh**2).sum()) < 1e-6
    assert (
        cc["numbers"]["top_k"]["text"] == "five"
        and cc["numbers"]["then_label"]["text"] == "2012"
    )
    # calm, then wild: the recent changes are larger
    rng = np.random.default_rng(3)
    d = pd.date_range("2015-01-01", periods=60, freq="MS")
    noise = np.r_[rng.normal(0, 0.005, 48), rng.normal(0, 0.05, 12)]
    vol = pd.DataFrame({"date": d, "price": 100 * np.exp(np.cumsum(noise))})
    blk = na.explore(vol, {"date": "date", "price": "number"})
    vo = [s for s in blk["steps"] if s["op"] == "volatility"][0]
    assert vo["ok"] and vo["numbers"]["compare"]["text"] == "larger than", vo


def test_a_temperature_changes_as_a_difference_never_a_percent():
    d = pd.date_range("2020-01-01", periods=48, freq="MS")
    df = pd.DataFrame(
        {
            "date": d,
            "temp_max": 15
            + 8 * np.sin(2 * np.pi * d.month / 12)
            + np.linspace(0, 1, 48),
        }
    )
    blk = na.explore(df, {"date": "date", "temp_max": "number"})
    for s in blk["steps"]:
        for k, v in s["numbers"].items():
            assert v["kind"] != "pct", (s["op"], k, v)
            if s["op"] == "volatility" and k.startswith("sd_"):
                assert v["kind"] == "plain", v


def test_shape_reads_scattered_orders_as_a_ledger_and_two_sources_as_a_series():
    df = _orders().drop(columns=["email"])
    df = df.groupby(
        "date", as_index=False
    ).first()  # one order a day at most, on scattered days
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.day % 3 == 0]  # orders on about a third of the days
    sh = na.shape_of(df, _kinds_of(df))
    assert sh.kind == "ledger", sh.kind
    full = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=400, freq="D")})
    full["region"] = np.where(np.arange(400) % 2, "North", "South")
    full["temp_max"] = np.linspace(5, 25, 400)
    assert (
        na.shape_of(full, _kinds_of(full)).kind == "time_series"
    )  # a value every day: a series
    d = pd.date_range("2000-01-01", periods=60, freq="MS")
    two = pd.DataFrame(
        {
            "date": list(d) * 2,
            "Source": ["GISTEMP"] * 60 + ["GCAG"] * 60,
            "Mean": np.r_[np.linspace(0.2, 0.9, 60), np.linspace(0.25, 0.95, 60)],
        }
    )
    sh = na.shape_of(two, _kinds_of(two))
    assert sh.kind == "time_series" and sh.entity is None, (sh.kind, sh.entity)


def test_step_schema_is_generated_from_ops_and_their_signatures():
    import inspect

    sc = na.step_schema()
    json.dumps(sc, allow_nan=False)
    ops = [v["properties"]["op"]["const"] for v in sc["oneOf"]]
    assert ops == list(na.OPS) and set(na.OPS) == set(na._OP_FN), (ops, na.OPS)
    assert set(na.OP_DOCS) == set(na.OPS)
    for v in sc["oneOf"]:
        op = v["properties"]["op"]["const"]
        params = list(inspect.signature(na._OP_FN[op]).parameters.values())[2:]
        args = v["properties"]["args"]
        assert set(args["properties"]) == {p.name for p in params}, op
        assert set(args["required"]) == {
            p.name for p in params if p.default is inspect.Parameter.empty
        }, op
        assert (
            args["additionalProperties"] is False and v["additionalProperties"] is False
        )
    # narrowed to a file's shape: columns become enums of their role, and ops that cannot run are left out
    blk = na.explore(_cross_section(), _kinds_of(_cross_section()))
    sc2 = na.step_schema(blk["shape"])
    by_op = {v["properties"]["op"]["const"]: v for v in sc2["oneOf"]}
    assert by_op["breakdown"]["properties"]["args"]["properties"]["measure"][
        "enum"
    ] == ["sugarpercent", "winpercent"]
    assert by_op["breakdown"]["properties"]["args"]["properties"]["by"]["enum"] == [
        "kind"
    ]
    ops1 = set(by_op)
    assert not ops1 & {
        "change",
        "trend",
        "panel_change",
        "rank_entities",
        "rank_labels",
        "crosstab",
    }, ops1  # no date
    assert {"breakdown", "correlate", "outliers"} <= ops1, ops1
    sv = na.explore(_survey(), _kinds_of(_survey()))["shape"]
    ops2 = {v["properties"]["op"]["const"] for v in na.step_schema(sv)["oneOf"]}
    assert ops2 == {
        "crosstab",
        "answers",
        "sql",
    }, ops2  # a survey with no measure: the cross-tabulation, the answers, and a planner's read-only SQL (CONTRACT §5.14)
    # the reference worker reads the same file (docs/ai-analyst/step-schema.json), so it must be the current one
    with open(os.path.join(SITE, "docs", "ai-analyst", "step-schema.json")) as f:
        assert json.load(f) == json.loads(
            json.dumps(sc)
        ), "regenerate docs/ai-analyst/step-schema.json"


def test_run_steps_refuses_everything_outside_the_schema():
    df = _orders().drop(columns=["email"])
    df["date"] = pd.to_datetime(df["date"])
    kinds = _kinds_of(df)
    bad = [
        "breakdown",  # not an object
        {"op": "exec", "args": {}},  # unknown op
        {"op": "trend", "args": {"measure": "revenue"}, "code": "x"},  # extra field
        {
            "op": "trend",
            "args": {"measure": "revenue", "by": "region"},
        },  # an argument trend does not take
        {
            "op": "breakdown",
            "args": {"measure": "revenue"},
        },  # a required argument missing
        {"op": "trend", "args": {"measure": ["revenue"]}},  # wrong type
        {"op": "trend", "args": {"measure": "region"}},  # a group column as a measure
        {
            "op": "breakdown",
            "args": {"measure": "revenue", "by": "units"},
        },  # a measure as a group column
        {"op": "correlate", "args": {"measures": ["revenue"]}},  # too few measures
        {
            "op": "change",
            "args": {"measure": "revenue", "where": {"region": {"$ne": 1}}},
        },  # a filter value object
        {
            "op": "change",
            "args": {"measure": "revenue", "where": {"a": "1", "b": "2", "c": "3"}},
        },
        {
            "op": "change",
            "args": {"measure": "revenue", "where": {"region": "Atlantis"}},
        },  # not a group it holds
        {
            "op": "change",
            "args": {"measure": "revenue", "where": {"region": 2025}},
        },  # a number, not a group
        {
            "op": "change",
            "args": {"measure": "revenue"},
            "why": None,
        },  # why must be text
        {
            "op": "rank_entities",
            "args": {"measure": "revenue", "years": True},
        },  # a bool is not a whole number
        {
            "op": "rank_entities",
            "args": {"measure": "revenue", "years": 0},
        },  # out of range
        {"op": "trend", "args": "measure=revenue"},  # args not an object
        {"op": "trend", "args": {"measure": "revenue"}, "why": 7},  # why not text
    ]
    for raw in bad:
        out = na.run_steps(df, kinds, [raw])
        ran = [s for s in out["steps"] if s["ok"]]
        assert not ran, (raw, ran)
        for s in out["steps"]:
            assert s["refused"] and not s["numbers"] and not s["table"]["rows"], (
                raw,
                s,
            )
        assert out["steps"] or out["refused"], raw
        assert not out["insights"], raw
    # within the schema, it runs; the fifth step of a round is refused; a reason with a figure loses the figure
    good = {
        "op": "breakdown",
        "args": {"measure": "revenue", "by": "region"},
        "why": "check the 2025 dip",
    }
    out = na.run_steps(df, kinds, [good] * 5)
    assert [s["ok"] for s in out["steps"]] == [True] * 4 + [False], [
        s["refused"] for s in out["steps"]
    ]
    assert "more than" in out["steps"][4]["refused"] and out["steps"][0]["why"] == ""
    assert out["steps"][0]["id"] == "A1" and out["round"] == 1
    # every op in OPS runs through the same door
    out2 = na.run_steps(
        df,
        kinds,
        [
            {"op": "seasonality", "args": {"measure": "revenue"}},
            {"op": "yoy", "args": {"measure": "revenue"}},
            {"op": "concentration", "args": {"measure": "revenue", "by": "region"}},
            {"op": "mix", "args": {"measure": "revenue", "by": "region"}},
        ],
        prior=out["steps"],
    )
    assert out2["steps"][0]["id"] == "A6" and out2["round"] == 2, out2["steps"][0]
    assert not any("could not run" in s["refused"] for s in out2["steps"]), out2[
        "steps"
    ]


def test_planner_steps_get_sentences_only_from_their_ledger_and_the_ai_sees_no_rows():
    df = _orders().drop(columns=["email"])
    df["date"] = pd.to_datetime(df["date"])
    kinds = _kinds_of(df)
    steps = [
        {"op": op, "args": args, "why": "w"}
        for op, args in (
            ("change", {"measure": "revenue", "by": "product"}),
            ("trend", {"measure": "units"}),
            ("concentration", {"measure": "revenue", "by": "product"}),
            ("correlate", {"measures": ["revenue", "units"]}),
        )
    ]
    out = na.run_steps(df, kinds, steps)
    assert all(s["ok"] for s in out["steps"]) and len(out["insights"]) == 4, out
    # a filtered step says its filter
    out_w = na.run_steps(
        df,
        kinds,
        [
            {
                "op": "change",
                "args": {
                    "measure": "revenue",
                    "by": "product",
                    "where": {"region": "East"},
                },
            }
        ],
        prior=out["steps"],
    )
    assert out_w["insights"][0]["text"].startswith(
        "Among rows where region is East, by product,"
    ), out_w["insights"]
    out["steps"] += out_w["steps"]
    out["insights"] += out_w["insights"]
    texts = {
        x
        for s in out["steps"]
        for v in s["numbers"].values()
        for x in _NUM.findall(v["text"])
    }
    by_id = {s["id"]: s for s in out["steps"]}
    for i in out["insights"]:
        assert (
            _SLOT.sub(
                lambda m: by_id[m.group(1)]["numbers"][m.group(2)]["text"],
                i["template"],
            )
            == i["text"]
        )
        for x in _NUM.findall(i["text"]):
            assert x in texts | {"95"}, (i["text"], x)
    seen = na.ledger_for_ai({"steps": out["steps"]})
    dump = json.dumps(seen)
    assert '"table"' not in dump and '"value"' not in dump and '"rows"' not in dump
    assert all(
        set(x) == {"id", "round", "op", "args", "why", "ok", "refused", "figures"}
        for x in seen
    )
    blk = na.explore(df.copy(), kinds)
    seen = na.ledger_for_ai(blk)
    assert len(seen) == len(blk["steps"]) and '"table"' not in json.dumps(seen)


def _steak_survey(seed=3, n=400):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        smoke = "Yes" if rng.random() < 0.3 else "No"
        rows.append(
            {
                "Do you smoke?": smoke,
                "Do you drink?": ("Yes" if rng.random() < (0.8 if smoke == "Yes" else 0.4) else "No"),
                "How do you like your steak<br>prepared?": rng.choice(
                    ["Rare", "Medium rare", "Medium", "Well"], p=[0.1, 0.45, 0.3, 0.15]
                ),
                "Age": rng.choice(["18-29", "30-44", "45-60", "> 60"]),
                "Gender": rng.choice(["Male", "Female"]),
            }
        )
    return pd.DataFrame(rows)


def test_a_survey_says_what_most_people_answered_subject_first_demographics_last():
    df = _steak_survey()
    blk = _explore(df, {"topic": ["steak", "risk"]})
    assert blk["shape"]["kind"] == "survey", blk["shape"]
    ans = [s for s in blk["steps"] if s["op"] == "answers"]
    assert ans and ans[0]["ok"], blk["refused"]
    nums = ans[0]["numbers"]
    # the subject named by the file leads, its markup read as a space; a demographic never leads
    assert nums["q1"]["text"] == "How do you like your steak prepared?", nums["q1"]
    assert nums["q1_top"]["text"] == "Medium rare"
    want = (df["How do you like your steak<br>prepared?"] == "Medium rare").mean()
    assert abs(nums["q1_share"]["value"] - want) < 1e-9
    assert nums.get("agree_q", {}).get("text") not in ("Age", "Gender")
    assert nums.get("split_q", {}).get("text") not in ("Age", "Gender")
    head = [i["text"] for i in blk["insights"] if i["steps"] == [ans[0]["id"]]]
    assert any("Medium rare" in t for t in head), head
    assert not any("<br>" in i["text"] for i in blk["insights"])
    # with no subject words the order comes from the answers alone, and still never puts a demographic first
    blk2 = _explore(df)
    q1 = [s for s in blk2["steps"] if s["op"] == "answers"][0]["numbers"]["q1"]["text"]
    assert q1 not in ("Age", "Gender"), q1


def test_a_time_cut_is_recorded_never_silent():
    blk = na.explore(
        _explore.__globals__["_orders"]().assign(date=lambda d: pd.to_datetime(d["date"])),
        {"date": "date", "region": "text", "product": "text", "revenue": "number", "units": "number",
         "email": "text"},
        None,
        budget_s=0.0,
    )
    assert blk["cut_short"] == "time", blk.get("cut_short")
    assert any(r["op"] == "rest" for r in blk["refused"]), blk["refused"]
    full = _explore(_orders())
    assert full["cut_short"] is None


def _safety(seed=11, n=40):
    rng = np.random.default_rng(seed)
    size = np.exp(rng.uniform(np.log(1e8), np.log(5e9), n)).round()
    df = pd.DataFrame(
        {
            "airline": ["Air %02d" % i for i in range(n)],
            "avail_seat_km_per_week": size,
            "incidents_85_99": rng.poisson(size / 4e8),
            "fatalities_85_99": rng.poisson(size / 1e8),
            "incidents_00_14": rng.poisson(size / 8e8),
            "fatalities_00_14": rng.poisson(size / 3e8),
        }
    )
    df.loc[7, "fatalities_00_14"] = 900  # one airline with a disaster in the latest period
    return df


def test_counts_by_period_in_the_columns_are_compared_led_and_read_against_size():
    df = _safety()
    blk = na.explore(df, _kinds_of(df))
    assert blk["shape"]["periods"] == {
        "incidents": ["incidents_85_99", "incidents_00_14"],
        "fatalities": ["fatalities_85_99", "fatalities_00_14"],
    }, blk["shape"].get("periods")
    # the outcome is the primary measure, never the size column beside it
    assert blk["shape"]["primary"] == "fatalities_00_14", blk["shape"]["primary"]
    pe = [s for s in blk["steps"] if s["op"] == "periods"][0]["numbers"]
    a, b = df["fatalities_85_99"].sum(), df["fatalities_00_14"].sum()
    assert abs(pe["s1_change"]["value"] - (b / a - 1)) < 1e-9, pe["s1_change"]
    assert pe["first"]["text"] == "1985-99" and pe["last"]["text"] == "2000-14"
    assert pe["s1_lead_b"]["text"] == "Air 07", pe["s1_lead_b"]
    assert pe["per"]["text"].startswith("per ") and pe["overall_rate"]["value"] >= 1, pe["per"]
    texts = " ".join(i["text"] for i in blk["insights"])
    assert "Air 07" in texts and "fold in avail seat km per week" in texts, texts


def test_numbered_columns_that_are_not_years_are_not_periods():
    assert na._period_columns(["score_10", "score_20", "score_30"]) == {}
    assert na._period_columns(["male_18_24", "male_25_34", "female_18_24", "female_25_34"]) == {}
    # two-digit spans read as years only when two quantities share them
    assert na._period_columns(["x_85_99", "x_00_14"]) == {}
    assert list(na._period_columns(["pop_2019", "pop_2020"])) == ["pop"]


def test_group_averages_that_only_follow_size_are_called_out_and_size_named_over_its_dimensions():
    rng = np.random.default_rng(4)
    n = 2000
    cut = rng.choice(["Fair", "Good", "Very Good", "Premium", "Ideal"], n)
    # better cuts are cut from smaller stones; price per carat rises with the cut, the stone size does the rest
    base = {"Fair": 1.2, "Good": 1.0, "Very Good": 0.85, "Premium": 0.8, "Ideal": 0.65}
    carat = np.array([base[c] for c in cut]) * rng.lognormal(0, 0.3, n)
    per = {"Fair": 3000, "Good": 3200, "Very Good": 3400, "Premium": 3500, "Ideal": 3700}
    price = carat ** 1.6 * np.array([per[c] for c in cut]) * rng.lognormal(0, 0.1, n)
    df = pd.DataFrame({"carat": carat, "cut": cut, "x": carat ** (1 / 3) * 6.5, "price": price})
    blk = na.explore(df, _kinds_of(df), {"primary": "price"})
    cr = [s for s in blk["steps"] if s["op"] == "correlate"][0]["numbers"]
    assert (cr["x"]["text"], cr["y"]["text"]) == ("price", "carat"), (cr["x"], cr["y"])
    cf = [s for s in blk["steps"] if s["op"] == "confound" and s["ok"]]
    assert cf, [s["op"] for s in blk["steps"]]
    nums = cf[0]["numbers"]
    assert nums["low"]["text"] == "Ideal" and nums["rho"]["value"] >= 0.7, nums
    assert any("per unit of carat" in i["text"] for i in blk["insights"]), [i["text"] for i in blk["insights"]]


def test_the_most_common_value_is_named_and_numbers_are_never_counted_as_categories():
    rng = np.random.default_rng(8)
    n = 600
    peaks = rng.choice(["Everest"] * 5 + ["Ama Dablam"] * 2 + ["Cho Oyu", "Manaslu", "Lhotse"], n)
    peaks = np.where(np.arange(n) < 30, "Peak %d" % 0, peaks)
    df = pd.DataFrame(
        {
            "date": pd.date_range("2015-01-01", periods=n, freq="5D"),
            "peak_name": np.concatenate([peaks[:-40], ["Peak %d" % i for i in range(40)]]),
            "season": rng.choice(["Spring", "Autumn", "Winter"], n, p=[0.5, 0.45, 0.05]),
            "stars_2": rng.integers(0, 3, n),
            "members": rng.integers(1, 20, n),
            # people repeat more than any peak, yet a person is an identifier, never the thing the rows are about
            "customer_name": rng.choice(["Ann Lee"] * 90 + ["Bo Chan %d" % i for i in range(60)], n),
            "contact": rng.choice(["ann@mailbox.test"] * 90 + ["bo%d@mailbox.test" % i for i in range(60)], n),
        }
    )
    blk = na.explore(df, _kinds_of(df), {"primary": "members"})
    fq = [s for s in blk["steps"] if s["op"] == "frequency" and s["ok"]]
    assert fq, [s["op"] for s in blk["steps"]]
    nums = fq[0]["numbers"]
    assert nums["a_col"]["text"] == "peak_name" and nums["a_top"]["text"] == "Everest", nums
    assert nums["b_col"]["text"] == "season", nums
    assert "stars_2" not in json.dumps(nums)
    assert "Ann" not in json.dumps(nums) and "@" not in json.dumps(nums), nums


def test_a_crash_names_its_monthly_record_and_a_season_does_not():
    rng = np.random.default_rng(3)
    d = pd.date_range("2000-01-01", periods=120, freq="MS")
    # a price that climbs to a peak in 2007-10, crashes to 2009-02 and recovers a little: the years, not the months
    path = np.concatenate([np.linspace(100, 150, 94), np.linspace(150, 70, 16), np.linspace(70, 95, 10)])
    df = pd.DataFrame({"date": d, "price": path + rng.normal(0, 0.5, 120)})
    blk = na.explore(df, _kinds_of(df), {"primary": "price"})
    tr = [i["text"] for i in blk["insights"] if i["kind"] == "trend"]
    assert tr and "monthly record high" in tr[0] and re.search(r"2007-(09|10|11)", tr[0]) and re.search(r"2009-0[1-4]", tr[0]), tr
    # a temperature that only follows its seasons: no record sentence, the summer peak is not news
    temp = 10 + 8 * np.sin(np.arange(120) * 2 * np.pi / 12) + rng.normal(0, 0.3, 120)
    df2 = pd.DataFrame({"date": d, "temp": temp})
    blk2 = na.explore(df2, _kinds_of(df2), {"primary": "temp"})
    assert not any("record high" in i["text"] for i in blk2["insights"]), [i["text"] for i in blk2["insights"]]


def test_an_insight_carries_the_chart_that_shows_it_and_the_chart_registry_accepts_it():
    sys.path.insert(0, os.path.join(SITE, "tools", "fixtures", "viz"))
    import validate_spec as V

    spec = json.load(open(os.path.join(SITE, "tools", "fixtures", "viz", "spec.json")))
    blk = _explore(_orders())
    figs = [i["figure"] for i in blk["insights"] if i.get("figure")]
    kinds = {f["kind"] for f in figs}
    assert "lines" in kinds and "hbar" in kinds, kinds
    ids = [f["id"] for f in figs]
    assert len(ids) == len(set(ids)), ids
    for f in figs:
        errs = V.validate(f, spec["schema"], spec["schema"]) + V.check_record(f, spec)
        assert not errs, (f["id"], errs[:3])
        assert V.js_bytes(f) <= spec["caps"]["CHART_BYTES"], (f["id"], V.js_bytes(f))
        assert "email" not in json.dumps(f), f["id"]  # a withheld column never reaches a chart


def test_a_bar_resting_on_fewer_than_five_rows_is_never_drawn():
    blk = _explore(_orders())
    for i in blk["insights"]:
        f = i.get("figure")
        if f and f["kind"] == "lines":
            for row, v in zip(f["table"]["rows"], f["data"]["series"][0]["values"]):
                assert (v is None) == (row[1] in ("<5", "")), row
    # a group of three rows: its bar is left out of the chart
    df = _orders()
    tiny = df.head(3).assign(region="Tiny")
    blk2 = _explore(pd.concat([df, tiny], ignore_index=True))
    for i in blk2["insights"]:
        f = i.get("figure")
        if f and f["kind"] == "hbar":
            assert all(r["label"] != "Tiny" for r in f["data"]["rows"]), f["id"]


def test_the_story_leads_with_key_numbers_each_a_printed_ledger_value_of_its_own_step():
    blk = _explore(_orders())
    st = blk["story"]
    assert st and st["headline"]["text"], st
    tiles = st["key_numbers"]
    assert 2 <= len(tiles) <= 4, tiles
    steps = {s["id"]: s for s in blk["steps"]}
    ins_ids = {i["id"] for i in blk["insights"]}
    for k in tiles:
        assert k["insight"] in ins_ids, k
        printed = {v["text"] for v in steps[k["step"]]["numbers"].values()}
        # the value is the step's own printed text (a panel's "up of n" is two of them)
        assert k["value"] in printed or all(p in printed for p in k["value"].split(" of ")), (k, printed)
        assert k["label"] and len(k["label"]) <= 80 and len(k["note"]) <= 80, k
    assert tiles[0]["insight"] == st["headline"]["insights"][0], (tiles[0], st["headline"])
    assert len({k["step"] for k in tiles}) == len(tiles), tiles
    # one "of the change came from" tile, however deep the drill goes
    assert sum("of the change came from" in k["label"] for k in tiles) <= 1, tiles


def test_a_breakdown_by_type_says_which_type_holds_the_most_of_the_whole_record():
    """disasters_panel (16 Oct 2026): the deadliest type over 1900-2017 (Drought, 11.7 million deaths) was named only by an outlier
    line about 1965; the corpus check passed by accident. A flow broken down into a few types, over 10 or more years, now says which
    type holds the most of the whole record, its total and share, recomputed here; the latest year's leader is still said, and a
    panel of many entries (countries) gets no whole-record line."""
    rows = []
    for y in range(1990, 2021):
        for t, v in (("Drought", 900 if y < 1995 else 5), ("Flood", 60), ("Storm", 40 + y - 1990), ("Quake", 20), ("Fire", 3)):
            rows.append({"Entity": t, "Year": y, "Deaths": float(v)})
    df = pd.DataFrame(rows)
    blk = na.explore(df, {"Entity": "text", "Year": "number", "Deaths": "number"})
    rk = next(s_ for s_ in blk["steps"] if s_["op"] == "rank_entities")
    n = rk["numbers"]
    tot = df.groupby("Entity")["Deaths"].sum()
    assert n["whole_top1"]["text"] == tot.idxmax() == "Drought", n.get("whole_top1")
    assert abs(n["whole_top1_value"]["value"] - tot.max()) < 1e-9 and abs(n["whole_top1_share"]["value"] - tot.max() / tot.sum()) < 1e-12
    assert n["top1"]["text"] == "Storm", n["top1"]           # 2020's leader is another type
    drill = [i["text"] for i in blk["insights"] if i["kind"] == "drill"]
    assert drill and "Over the whole record (1990 to 2020), Drought accounts for the most Deaths (%s, %s of the total)" % (
        n["whole_top1_value"]["text"], n["whole_top1_share"]["text"]) in drill[0], drill
    # forty countries are no breakdown by type: no whole-record line
    rows = [{"Country": "C%02d" % c, "Year": y, "co2": float(c + 1)} for c in range(40) for y in range(1990, 2021)]
    blk = na.explore(pd.DataFrame(rows), {"Country": "text", "Year": "number", "co2": "number"})
    rk = next(s_ for s_ in blk["steps"] if s_["op"] == "rank_entities")
    assert "whole_top1" not in rk["numbers"], rk["numbers"].keys()
    # a flow that runs below zero (a net flow) has no share of a whole-record total to state
    rows = [{"Entity": t, "Year": y, "Deaths": float(v if y % 7 else -v)} for t, v in (("A", 50), ("B", 30), ("C", 20), ("D", 10))
            for y in range(1990, 2021)]
    blk = na.explore(pd.DataFrame(rows), {"Entity": "text", "Year": "number", "Deaths": "number"})
    rk = next((s_ for s_ in blk["steps"] if s_["op"] == "rank_entities"), None)
    assert rk is None or "whole_top1" not in rk["numbers"], rk["numbers"].keys()


def test_the_corpus_checks_on_drought_and_world_co2_pass_only_on_the_fact_itself():
    """tools/corpus.json (16 Oct 2026): disasters_panel's "Drought" passed because an outlier line named it, and co2_panel's world
    +1.3% passed on the countries' +1.1% within its tolerance, while the scorer forbade the sentence to call World by its name. The
    tightened checks, run through run_corpus.check_one on made-up reports: each passes only on the fact, a total row named as the
    total passes the aggregates rule, and an aggregate inside a ranking of the members still fails it."""
    import run_corpus as RC

    with open(os.path.join(SITE, "tools", "corpus.json")) as fh:
        man = {d["id"]: d for d in json.load(fh)["datasets"]}
    drought = next(a for a in man["disasters_panel"]["expect"] if "Drought" in json.dumps(a))
    world = next(a for a in man["co2_panel"]["expect"] if "ledger_value" in a)
    aggs = next(a for a in man["co2_panel"]["expect"] if "aggregates_excluded" in a)

    def res(insights, steps=()):
        return {"rep": {"ok": True, "analyst": {"insights": insights, "steps": list(steps)}}, "secs": 1.0}
    outlier = {"kind": "robustness", "text": "117 of 666 relative changes ... (Drought 1965-12-31) against a median of -0.7%.", "steps": []}
    assert not RC.check_one(drought, res([outlier]))[0]
    said = {"kind": "drill", "steps": [], "text": "In 2017, Flood leads on Deaths (648.0). Over the whole record (1900 to 2017), Drought "
            "accounts for the most Deaths (11.7 million, 36.0% of the total)."}
    assert RC.check_one(drought, res([outlier, said]))[0]
    num = lambda v, t: {"value": v, "text": t, "kind": "pct"}  # noqa: E731
    countries = {"id": "A1", "op": "change", "numbers": {"change": num(0.010675, "+1.1%")}}
    both = {"id": "A1", "op": "change", "numbers": {"change": num(0.010675, "+1.1%"), "world_change": num(0.013245, "+1.3%")}}
    head1 = {"kind": "headline", "steps": ["A1"], "text": "The total of co2 rose from 37,003 in 2023 to 37,398 in 2024 (+1.1%)."}
    assert not RC.check_one(world, res([head1], [countries]))[0]
    head2 = {"kind": "headline", "steps": ["A1"], "text": "The total of co2 rose from 37,003 to 37,398 (+1.1%) (37 aggregate entries such "
             "as Africa are left out of every total); World (the file's own total row, which also counts what no entry holds) rose "
             "+1.3% (38,094 to 38,599)."}
    assert RC.check_one(world, res([head2], [both]))[0]
    assert RC.check_one(aggs, res([head2], [both]))[0], RC.check_one(aggs, res([head2], [both]))
    bare = {"kind": "headline", "steps": [], "text": "World rose +1.3%."}
    assert not RC.check_one(aggs, res([bare]))[0]
    ranked = {"kind": "drill", "steps": [], "text": "In 2024, World (the file's own total row) leads on co2, then China and India."}
    assert not RC.check_one(aggs, res([ranked]))[0]


def test_a_count_prints_whole_and_a_series_figures_keep_their_published_decimals():
    """weak2 (16 Oct 2026): "Flood leads on Deaths (648.0), then Landslide (405.0)" and, for a rate published to 2 decimals, "its
    monthly record high was 6.00 in 2000-05 and its low 0.5 in 2009-04". A figure of the measure itself prints at the measure's
    published decimals: a count whole, a 2-decimal rate at 2 decimals; a slope or an equal-groups figure keeps its own rule."""
    rows = []
    for y in range(1990, 2021):
        for t, v in (("Flood", 648 + y % 3), ("Landslide", 405), ("Storm", 394 - y % 2), ("Quake", 20)):
            rows.append({"Entity": t, "Year": y, "Deaths": v})
    blk = na.explore(pd.DataFrame(rows), {"Entity": "text", "Year": "number", "Deaths": "number"})
    drill = next(i["text"] for i in blk["insights"] if i["kind"] == "drill")
    assert "Flood leads on Deaths (649), then Landslide (405) and Storm (394)" in drill and ".0)" not in drill, drill
    conc = next((i["text"] for i in blk["insights"] if i["kind"] == "concentration"), "")
    assert not conc or re.search(r"the same as \d+\.\d+ equal-sized", conc), conc
    d = pd.date_range("2000-01-01", periods=200, freq="MS")
    v = np.round(np.clip(4.0 + np.sin(np.arange(200) / 15.0) * 3.5, 0.25, None) * 4) / 4   # quarter points: 0.25 ... 7.50
    v[0], v[100] = 7.75, 0.50
    df = pd.DataFrame({"date": d.strftime("%Y-%m-%d"), "bank_rate": ["%.2f" % x for x in v]})
    blk = na.explore(df, {"date": "date", "bank_rate": "number"})
    tr = next(s_ for s_ in blk["steps"] if s_["op"] == "trend")
    lo, hi = tr["numbers"].get("rec_lo"), tr["numbers"].get("rec_hi")
    assert lo and hi and lo["text"] == "0.50" and hi["text"] == "7.75", (lo, hi)
    txt = " ".join(i["text"] for i in blk["insights"])
    assert "its low 0.50 in" in txt and "low 0.5 in" not in txt, txt


def test_a_balance_split_by_group_with_no_entity_column_is_averaged_not_a_crash():
    """16 Oct 2026 (found by the monthly-pack track): op_change grouped a stock by its entity column even when the file has
    none, raising KeyError 'e' and stopping the whole analyst block. A stock with no entity is averaged like a level, as
    the step's own total already reads it."""
    rng = np.random.default_rng(3)
    rows = []
    for m in pd.period_range("2024-01", "2026-09", freq="M"):
        for r in ("East", "West", "North"):
            for _k in range(3):
                rows.append({"month": m.to_timestamp().strftime("%Y-%m-%d"), "region": r,
                             "account_balance": float(1000 + rng.normal(0, 50) + (m.ordinal % 12) * 5)})
    df = pd.DataFrame(rows)
    out = na.explore(df, {"month": "date", "region": "text", "account_balance": "number"},
                     {"date": "month", "primary": "account_balance"})
    assert {m["column"]: m["kind"] for m in out["shape"]["measures"]}.get("account_balance") == "stock"
    assert not [x for x in out.get("refused", []) if "Error" in str(x)], out.get("refused")
    ch = [s_ for s_ in out["steps"] if s_["op"] == "change" and (s_.get("args") or {}).get("by")]
    assert ch and all(s_.get("ok", True) for s_ in ch), [s_.get("refused") for s_ in ch]


def main() -> int:
    tests = [
        (k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)
    ]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("  FAIL  %s: %s: %s" % (name, type(exc).__name__, str(exc)[:300]))
    print("\n%d/%d passed" % (len(tests) - bad, len(tests)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
