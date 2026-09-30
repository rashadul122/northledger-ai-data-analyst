"""Adversarial CSVs + hand-written plans for the second data-test review (29 Sep 2026). Writes into ./cases."""
import csv
import json
import os
import random

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")
os.makedirs(R, exist_ok=True)
rnd = random.Random(20260929)
TRUTH = {}


def write(name, header, rows):
    with open(os.path.join(R, name), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def plan(name, p):
    json.dump(p, open(os.path.join(R, name), "w"), indent=1)


# ---- N1: money with 15% unreadable text AND 3% refunds (negatives), mixed money formats. 10 years x 12 months x 5.
rows, truth = [], []
junk = ["pending", "see note", "refund issued", "TBD", "N/A", "void?", "call customer"]
for i in range(600):
    y, m = 2015 + i // 60, (i // 5) % 12 + 1
    region = ["North", "South", "East"][i % 3]
    v = round(rnd.uniform(20, 400) * (1.3 if region == "East" else 1.0), 2)
    r = rnd.random()
    if r < 0.15:
        cell = rnd.choice(junk)
        truth.append((region, y, None, "junk"))
    elif r < 0.18:
        v = -round(rnd.uniform(10, 150), 2)
        # half written with a minus sign, half in accounting brackets
        cell = ("-$%s" % format(-v, ",.2f")) if rnd.random() < 0.5 else ("($%s)" % format(-v, ",.2f"))
        truth.append((region, y, v, "refund"))
    else:
        cell = "$%s" % format(v, ",.2f") if rnd.random() < 0.5 else "%.2f" % v
        truth.append((region, y, v, "ok"))
    rows.append(["%d-%02d-%02d" % (y, m, 1 + i % 28), region, cell])
write("n1_money.csv", ["order_date", "region", "amount"], rows)
TRUTH["n1"] = truth
plan("n1_plan.json", {"goal": "How has revenue moved, by region?", "understanding": "orders", "kind": "transactions",
                      "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                                  {"name": "region", "semantic_type": "category", "role": "segment"},
                                  {"name": "amount", "semantic_type": "flow_amount", "role": "target", "unit": "USD"}],
                      "operations": [], "primary": "amount",
                      "analyses": [{"type": "distribution", "columns": ["amount"]},
                                   {"type": "compare", "columns": ["amount"], "by": "region"},
                                   {"type": "trend", "columns": ["amount"]},
                                   {"type": "extremes", "columns": ["amount"]},
                                   {"type": "predict", "columns": ["amount", "region"]}]})

# ---- N2: a date column mixing DD/MM/YYYY (EU branch) and MM/DD/YYYY (US branch). 9 years monthly x 2 branches,
# plus a sales column that rises in a known way. Ambiguous (day <= 12) on most rows.
rows, truth = [], []
for y in range(2016, 2025):
    for m in range(1, 13):
        for br in ("US", "EU"):
            day = rnd.randint(1, 28)
            txt = ("%02d/%02d/%d" % (m, day, y)) if br == "US" else ("%02d/%02d/%d" % (day, m, y))
            sales = round(1000 + 50 * (y - 2016) + 30 * m + rnd.gauss(0, 20), 1)
            rows.append([txt, br, sales])
            truth.append((y, m, day, br, sales))
rnd.shuffle(rows)       # an export in no particular order
write("n2_mixed_dates.csv", ["date", "branch", "sales"], rows)
plan("n2_plan.json", {"goal": "How are sales trending?", "understanding": "monthly sales by branch", "kind": "time_series_panel",
                      "columns": [{"name": "date", "semantic_type": "date", "role": "date"},
                                  {"name": "branch", "semantic_type": "category", "role": "segment"},
                                  {"name": "sales", "semantic_type": "flow_amount", "role": "target", "unit": "EUR"}],
                      "operations": [], "primary": "sales",
                      "analyses": [{"type": "trend", "columns": ["sales"]},
                                   {"type": "extremes", "columns": ["sales"]},
                                   {"type": "predict", "columns": ["sales", "branch"]}]})

# ---- N3: order_id with 30 exact duplicate rows and 25 non-duplicate repeats (an order re-issued with a new amount).
rows = []
for i in range(345):
    rows.append(["ORD-%05d" % i, "2025-%02d-%02d" % (i % 12 + 1, i % 28 + 1), round(rnd.uniform(10, 300), 2)])
dups = [list(r) for r in rows[10:40]]                         # 30 exact duplicates
reissued = [[r[0], r[1], round(r[2] + 5, 2)] for r in rows[100:125]]   # 25 same id, different amount
rows = rows + dups + reissued
write("n3_id_repeats.csv", ["order_id", "order_date", "amount"], rows)
plan("n3_plan.json", {"goal": "Order value", "understanding": "orders", "kind": "transactions",
                      "columns": [{"name": "order_id", "semantic_type": "identifier", "role": "key"},
                                  {"name": "order_date", "semantic_type": "date", "role": "date"},
                                  {"name": "amount", "semantic_type": "flow_amount", "role": "target", "unit": "$"}],
                      "operations": [], "primary": "amount",
                      "analyses": [{"type": "distribution", "columns": ["amount"]}]})
# N3b: same ids, but 30% of rows are re-issues (a line-item file: order_id is not a row key)
rows = []
for i in range(300):
    rows.append(["ORD-%05d" % i, "2025-%02d-%02d" % (i % 12 + 1, i % 28 + 1), round(rnd.uniform(10, 300), 2)])
rows = rows + [list(r) for r in rows[:20]] + [[r[0], r[1], round(r[2] + 1, 2)] for r in rows[50:150]]
write("n3b_line_items.csv", ["order_id", "order_date", "amount"], rows)

# ---- N4: yearly panel, years as integers, the AI types year as date. 6 countries x 1995..2024.
rows = []
for c in ("Canada", "Chile", "Kenya", "Japan", "Norway", "Peru"):
    base = rnd.uniform(50, 500)
    for yr in range(1995, 2025):
        rows.append([c, yr, round(base * (1.02 ** (yr - 1995)) + rnd.gauss(0, 3), 2), round(rnd.uniform(1, 9), 2)])
write("n4_year_panel.csv", ["country", "year", "gdp_bn", "inflation_pct"], rows)
plan("n4_plan.json", {"goal": "How has GDP grown across countries?", "understanding": "country-year panel", "kind": "time_series_panel",
                      "columns": [{"name": "country", "semantic_type": "category", "role": "entity"},
                                  {"name": "year", "semantic_type": "date", "role": "date"},
                                  {"name": "gdp_bn", "semantic_type": "level", "role": "target", "unit": "USD bn"},
                                  {"name": "inflation_pct", "semantic_type": "percentage", "role": "driver", "unit": "%"}],
                      "operations": [], "primary": "gdp_bn",
                      "analyses": [{"type": "trend", "columns": ["gdp_bn"]}, {"type": "rank", "columns": ["gdp_bn"]},
                                   {"type": "extremes", "columns": ["gdp_bn"]},
                                   {"type": "predict", "columns": ["gdp_bn", "inflation_pct", "country"]}]})

# ---- N5: a column the AI types as count whose values are decimals 0.5 to 3.5 (hours of therapy per visit)
rows = []
for i in range(360):
    y, m = 2016 + i // 40, (i // 3) % 12 + 1
    rows.append(["%d-%02d-%02d" % (y, m, 1 + i % 28), ["A", "B"][i % 2], "%.1f" % rnd.choice([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5])])
write("n5_count_decimals.csv", ["visit_date", "clinic", "hours"], rows)
plan("n5_plan.json", {"goal": "Therapy hours over time", "understanding": "visits", "kind": "transactions",
                      "columns": [{"name": "visit_date", "semantic_type": "date", "role": "date"},
                                  {"name": "clinic", "semantic_type": "category", "role": "segment"},
                                  {"name": "hours", "semantic_type": "count", "role": "target", "unit": "sessions"}],
                      "operations": [], "primary": "hours",
                      "analyses": [{"type": "distribution", "columns": ["hours"]}, {"type": "compare", "columns": ["hours"], "by": "clinic"},
                                   {"type": "trend", "columns": ["hours"]}]})

# ---- N6: a 0..1 percentage column (conversion rate), and N6b the same with ONE stray value of 1.5
rows = []
for i in range(240):
    y, m = 2015 + i // 24, (i // 2) % 12 + 1
    rate = round(min(max(rnd.gauss(0.12 + 0.004 * (y - 2015), 0.03), 0.001), 0.6), 4)
    rows.append(["%d-%02d-01" % (y, m), ["web", "app"][i % 2], rate])
write("n6_pct_fraction.csv", ["month", "channel", "conversion_rate"], rows)
rows_b = [list(r) for r in rows]
rows_b[77][2] = 1.5
write("n6b_pct_stray.csv", ["month", "channel", "conversion_rate"], rows_b)
plan("n6_plan.json", {"goal": "Is conversion improving?", "understanding": "monthly conversion by channel", "kind": "time_series_panel",
                      "columns": [{"name": "month", "semantic_type": "date", "role": "date"},
                                  {"name": "channel", "semantic_type": "category", "role": "segment"},
                                  {"name": "conversion_rate", "semantic_type": "percentage", "role": "target", "unit": "%"}],
                      "operations": [], "primary": "conversion_rate",
                      "analyses": [{"type": "distribution", "columns": ["conversion_rate"]},
                                   {"type": "compare", "columns": ["conversion_rate"], "by": "channel"},
                                   {"type": "trend", "columns": ["conversion_rate"]}]})

# ---- N7: the plan's date column is a birth date, which the privacy check flags; the visitor withholds it.
# A few unreadable birth dates so the date test has cells to list. Header in plain snake case (n7) and in
# title case with spaces (n7b), which lands under a different (slugged) name.
rows = []
for i in range(500):
    dob = "19%02d-%02d-%02d" % (40 + i % 58, i % 12 + 1, i % 28 + 1)
    if i % 50 == 7:
        dob = "unknown"
    rows.append([dob, "20%02d-%02d-%02d" % (16 + i // 60, (i // 5) % 12 + 1, i % 28 + 1), ["Physio", "Checkup"][i % 2],
                 "%d" % (40 + (i * 7) % 90)])
write("n7_withheld_date.csv", ["date_of_birth", "visit_date", "service", "fee"], rows)
write("n7b_withheld_date_titled.csv", ["Date Of Birth", "Visit Date", "Service", "Fee"], rows)
for nm, dcol, rest in (("n7_plan.json", "date_of_birth", ("visit_date", "service", "fee")),
                       ("n7b_plan.json", "Date Of Birth", ("Visit Date", "Service", "Fee"))):
    plan(nm, {"goal": "How have fees moved?", "understanding": "clinic visits", "kind": "transactions",
              "columns": [{"name": dcol, "semantic_type": "date", "role": "date"},
                          {"name": rest[0], "semantic_type": "date", "role": "metadata"},
                          {"name": rest[1], "semantic_type": "category", "role": "segment"},
                          {"name": rest[2], "semantic_type": "flow_amount", "role": "target", "unit": "$"}],
              "operations": [], "primary": rest[2],
              "analyses": [{"type": "trend", "columns": [rest[2]]}, {"type": "extremes", "columns": [rest[2]]},
                           {"type": "compare", "columns": [rest[2]], "by": rest[1]},
                           {"type": "predict", "columns": [rest[2], rest[1]]},
                           {"type": "rank", "columns": [rest[2]]}, {"type": "share", "columns": [rest[2]]}]})
json.dump({"date_of_birth": "withhold"}, open(os.path.join(R, "n7_dec.json"), "w"))
json.dump({"Date Of Birth": "withhold", "date_of_birth": "withhold"}, open(os.path.join(R, "n7b_dec.json"), "w"))

# ---- N8: nothing computable: free text and a category, no date, no number
rows = [["T%04d" % i, rnd.choice(["billing", "shipping", "login"]), rnd.choice(["slow reply", "item broken", "cannot sign in", ""])]
        for i in range(120)]
write("n8_nothing.csv", ["ticket", "topic", "comment"], rows)
plan("n8_plan.json", {"goal": "What are customers complaining about?", "understanding": "support tickets", "kind": "other",
                      "columns": [{"name": "ticket", "semantic_type": "identifier", "role": "key"},
                                  {"name": "topic", "semantic_type": "category", "role": "segment"},
                                  {"name": "comment", "semantic_type": "text", "role": "metadata"}],
                      "operations": [], "primary": "",
                      "analyses": [{"type": "trend", "columns": ["topic"]}, {"type": "distribution", "columns": ["topic"]}]})
# N8b: a date and text only
rows = [["2025-%02d-%02d" % (i % 12 + 1, i % 28 + 1), rnd.choice(["billing", "shipping", "login"])] for i in range(120)]
write("n8b_date_text.csv", ["opened", "topic"], rows)
plan("n8b_plan.json", {"goal": "Ticket volume", "understanding": "tickets", "kind": "other",
                       "columns": [{"name": "opened", "semantic_type": "date", "role": "date"},
                                   {"name": "topic", "semantic_type": "category", "role": "segment"}],
                       "operations": [], "primary": "", "analyses": [{"type": "trend", "columns": ["topic"]}]})
json.dump(TRUTH, open(os.path.join(R, "truth.json"), "w"))
print("written", sorted(os.listdir(R)))
