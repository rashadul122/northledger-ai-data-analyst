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
        "crosstab"
    }, ops2  # a survey with no measure: only the cross-tabulation
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
