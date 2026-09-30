"""CORRECTNESS on transactions: negative values, a segment in one window only, a month with 0 rows, ties, recomputed with
plain pandas from downloads.clean_csv."""
from common import *
import math
import numpy as np
import pandas as pd

def make(seed=3, gap_month="2025-03", refunds=True, tie=True):
    rng = random.Random(seed)
    stores = ["North", "South", "East", "West", "Central", "Harbor", "Airport", "Outlet", "Mall", "Uptown", "Kiosk"]
    rows = []
    for k in range(26):                       # 2024-01 .. 2026-02
        y, m = 2024 + k // 12, k % 12 + 1
        ym = "%04d-%02d" % (y, m)
        if ym == gap_month:
            continue                           # a month with 0 rows
        for si, st in enumerate(stores):
            if st == "Uptown" and k < 14:      # enters in the latest window with many rows
                continue
            if st == "Mall" and k >= 12:       # leaves: only in the prior window
                continue
            if st == "Kiosk":                  # 3 rows in all: 1 prior (k=13), 2 latest
                if k not in (13, 20, 22):
                    continue
                n = 1
            else:
                n = 4 + si % 3
            for _ in range(n):
                u = rng.randint(1, 6)
                p = rng.uniform(10, 30) * (1.08 if k >= 14 else 1.0)
                rev = round(u * p, 2)
                if refunds and rng.random() < 0.12:
                    rev = -rev                 # refunds are negative revenue
                rows.append(["%s-%02d" % (ym, rng.randint(1, 28)), st, rng.choice(["web", "phone", "store"]), "%.2f" % rev, str(u)])
    if tie:                                    # two stores with identical totals and row counts in the whole file
        rows.append(["2025-06-10", "Harbor", "web", "0.00", "1"])
    return csv_bytes(["order_date", "store", "channel", "revenue", "units"], rows)

PLAN = {"goal": "Which stores drove revenue?", "kind": "transactions", "primary": "revenue",
        "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                    {"name": "store", "semantic_type": "category", "role": "segment"},
                    {"name": "channel", "semantic_type": "category", "role": "segment"},
                    {"name": "revenue", "semantic_type": "flow_amount", "role": "target", "unit": "USD"},
                    {"name": "units", "semantic_type": "count", "role": "driver"}],
        "operations": [], "analyses": []}
CH = [{"kind": k, "columns": c, "why": "x"} for k, c in [
    ("contribution_waterfall", ["store", "revenue"]), ("slope", ["store", "revenue"]), ("calendar_heatmap", ["revenue"]),
    ("change_heatmap", ["store", "revenue"]), ("crosstab_heatmap", ["store", "channel"]), ("pareto", ["store", "revenue"]),
    ("group_ranges", ["revenue", "store"]), ("pvm_waterfall", ["revenue", "units", "store"])]]
data = make()
open(os.path.join(OUT, "tx.csv"), "wb").write(data)
rep = run(data, "tx.csv", dict(PLAN, charts=CH), as_of="2026-03-15")
summary(rep)
df = clean_df(rep)
df["v"] = pd.to_numeric(df["revenue"].where(df["revenue"] != ""), errors="coerce")
df["ym"] = pd.to_datetime(df["order_date"], errors="coerce").dt.strftime("%Y-%m")
print("rows kept", len(df), "negatives", int((df["v"] < 0).sum()), "months", df["ym"].nunique())
recs = {c["chart"]: c for c in rep["viz"]["charts"]}
bad = []

sc = rep["scenarios"]; b = sc.get("basis") or {}
print("scenarios windows", b.get("windows"), "segment", (b.get("segment") or {}).get("column"), "levels", (b.get("segment") or {}).get("levels"),
      "folded", (b.get("segment") or {}).get("folded"), "entered", (b.get("segment") or {}).get("entered"), "exited", (b.get("segment") or {}).get("exited"))
if "contribution_waterfall" in recs and b:
    w = recs["contribution_waterfall"]; st = w["data"]["steps"]
    pm = NB._month_range(*b["windows"]["prior"]); lm = NB._month_range(*b["windows"]["latest"])
    P = df[df["ym"].isin(pm)]; L = df[df["ym"].isin(lm)]
    prior, latest = math.fsum(P["v"]), math.fsum(L["v"])
    print("WATERFALL steps:", [(s["label"], s["text"], s["from"], s["to"]) for s in st])
    if abs(st[0]["value"] - prior) > 1e-6 or abs(st[-1]["value"] - latest) > 1e-6: bad.append(("wf totals", st[0]["value"], prior, st[-1]["value"], latest))
    if abs(math.fsum(s["value"] for s in st[1:-1]) - (latest - prior)) > 1e-6: bad.append("wf steps do not add up")
    run_ = st[0]["value"]
    for s in st[1:-1]:
        if abs(s["from"] - run_) > 1e-6 or abs(s["to"] - (run_ + s["value"])) > 1e-6: bad.append(("wf running", s))
        run_ = s["to"]
    # each named step equals that store's latest minus prior; every shown step rests on >= 5 rows in the windows it uses
    for s in st[1:-1]:
        name = s["label"].replace(" (new)", "").replace(" (left)", "")
        if name == "other":
            continue
        a, bb = P[P["store"] == name], L[L["store"] == name]
        want = math.fsum(bb["v"]) - math.fsum(a["v"])
        if abs(want - s["value"]) > 1e-6: bad.append(("wf step value", s["label"], s["value"], want))
        if " (new)" in s["label"] and len(bb) < 5: bad.append(("wf entered <5", s["label"], len(bb)))
        if " (left)" in s["label"] and len(a) < 5: bad.append(("wf exited <5", s["label"], len(a)))
        if "(" not in s["label"] and (len(a) < 5 or len(bb) < 5): bad.append(("wf both <5", s["label"], len(a), len(bb)))
    print("WATERFALL summary:", w["summary"]); print("  suppressed:", w["suppressed"]); print("  table:", w["table"]["rows"])
if "slope" in recs and b:
    s_ = recs["slope"]
    for r in s_["data"]["rows"]:
        name = r["label"].replace(" (new)", "").replace(" (left)", "")
        if name == "other":
            print("SLOPE other row:", r)
            continue
        a, bb = P[P["store"] == name], L[L["store"] == name]
        if abs(math.fsum(a["v"]) - r["a"]) > 1e-6 or abs(math.fsum(bb["v"]) - r["b"]) > 1e-6: bad.append(("slope", r))
    print("SLOPE rows:", [(r["label"], r["a_text"], r["b_text"], r["change_text"]) for r in s_["data"]["rows"]])
    print("SLOPE summary:", s_["summary"])
if "calendar_heatmap" in recs:
    c = recs["calendar_heatmap"]; d = c["data"]
    for i, y in enumerate(d["rows"]):
        for j in range(12):
            ym = "%s-%02d" % (y, j + 1); g = df[df["ym"] == ym]
            n = len(g); v = math.fsum(g["v"]) if n else None
            got, t = d["values"][i][j], d["text"][i][j]
            if n == 0 and (got is not None or t != ""): bad.append(("cal empty", ym, got, t))
            if 0 < n < 5 and (got is not None or t != "<5"): bad.append(("cal <5", ym, got, t))
            if n >= 5 and (got is None or abs(got - v) > 1e-6 or t != NB._fmt(got)): bad.append(("cal", ym, got, v, t))
    print("CAL row", d["rows"], "gap cell 2025-03:", d["values"][d["rows"].index("2025")][2], repr(d["text"][d["rows"].index("2025")][2]))
    print("CAL legend", d["legend"], "summary", c["summary"])
if "pareto" in recs:
    p = recs["pareto"]; d = p["data"]
    tot = df.groupby("store")["v"].apply(lambda s: math.fsum(s)); cnt = df.groupby("store").size()
    print("PARETO bars:", [(x["label"], x["text"], x["cum_text"]) for x in d["bars"]], "other:", d["other"], "k80", d["k80"])
    print("PARETO store counts:", cnt.to_dict())
if "crosstab_heatmap" in recs:
    x = recs["crosstab_heatmap"]; d = x["data"]
    ct = pd.crosstab(df["store"], df["channel"])
    for i, r in enumerate(d["rows"]):
        for j, cc in enumerate(d["cols"]):
            if r == "other": continue
            n = int(ct.loc[r, cc]) if r in ct.index and cc in ct.columns else 0
            got = d["values"][i][j]
            if n >= 5 and got != n: bad.append(("xt", r, cc, got, n))
            if 0 < n < 5 and got is not None: bad.append(("xt <5 shown", r, cc, got, n))
    shown = sum(v for row in d["values"] for v in row if v is not None)
    print("XTAB suppressed", x["suppressed"], "inputs.rows", x["inputs"]["rows"], "sum shown", shown, "=> total - shown =", x["inputs"]["rows"] - shown)
if "group_ranges" in recs:
    g = recs["group_ranges"]
    print("GROUPS:", [(r["label"], r["n"], r["texts"]["center"], r["texts"]["lo"], r["texts"]["hi"]) for r in g["data"]["rows"]], g["suppressed"])
    print("GROUPS summary:", g["summary"])
if "change_heatmap" in recs:
    c = recs["change_heatmap"]; d = c["data"]
    for i, r in enumerate(d["rows"]):
        for j, m in enumerate(d["cols"]):
            p = NB._shift_month(m, -12)
            sel = df["store"] == r if r != "other" else ~df["store"].isin([x for x in d["rows"] if x != "other"])
            A, B = df[sel & (df["ym"] == p)], df[sel & (df["ym"] == m)]
            got, t = d["values"][i][j], d["text"][i][j]
            n = min(len(A), len(B))
            if len(A) == 0 or len(B) == 0:
                if got is not None: bad.append(("chg should be empty", r, m, got))
            elif n < 5:
                if got is not None or t != "<5": bad.append(("chg <5", r, m, got, t, len(A), len(B)))
            elif math.fsum(A["v"]) <= 0:
                if got is not None: bad.append(("chg neg base shown", r, m, got))
            else:
                want = 100 * (math.fsum(B["v"]) / math.fsum(A["v"]) - 1)
                if got is None or abs(got - want) > 1e-6 * max(1, abs(want)): bad.append(("chg", r, m, got, want))
    print("CHANGE rows", d["rows"], "cols", d["cols"][:3], "...", "supp", c["suppressed"], "legend", d["legend"])
print("\nMISMATCHES:", bad if bad else "none")
json.dump(rep["viz"], open(os.path.join(OUT, "tx_viz.json"), "w"), indent=1)
