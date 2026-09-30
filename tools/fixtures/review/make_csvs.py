"""Adversarial CSVs + hand-written plans for the data-test review of 29 Sep 2026.

Writes a_ to e_ (CSV and plan) beside itself, reproducing the committed files byte for byte. The
f_, g_ and h_ files were written by hand during the review and are kept as they are:
  f_sparse_date.csv, g_sparse_date_wrong_axis.csv  600 orders; order_date is "TBD" on 30% of rows and
      refund_date is 45% blank. Both run with f_plan.json (refund_date is metadata). In g the refund
      dates run backwards in time, so a trend on them is a trend on the wrong axis.
  h_repeat_buyers.csv with h_plan.json and h_dec.json  200 orders; every 7th buyer email repeats
      the one before (a repeat customer, not a duplicate row); the email column is coded.
run2_replan.csv and run3_control.csv are the live end-to-end files of 29 Sep 2026 (written by the
live test's own generator, fixed seeds), with run2_plan.json (the plan the AI returned for run2) and
run3_plan.json (a plausible plan for the 10-year control file).
tools/test_nl_browser.py runs every case; run_case.py runs one against a packed engine folder."""
import csv, io, json, os, random

R = os.path.dirname(os.path.abspath(__file__))
rnd = random.Random(7)


def write(name, header, rows, raw=None):
    p = os.path.join(R, name)
    if raw is not None:
        open(p, "w", newline="").write(raw)
        return p
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return p


# A: a margin (%) column where 15% of months are legitimately negative (losses); the AI reads it as
# "percentage". Also a delivery delay (days early = negative) read as "duration".
rows = []
for i in range(120):
    y, m = 2015 + i // 12, i % 12 + 1
    margin = round(rnd.gauss(6, 3), 1)
    if i % 7 == 3:            # ~15% loss months
        margin = -round(abs(rnd.gauss(4, 2)) + 0.5, 1)
    delay = rnd.choice([-3, -2, -1, 0, 1, 2, 3, 4, 5, 2, 1, 3])
    rows.append(["%d-%02d-01" % (y, m), "north" if i % 2 else "south", margin, delay, rnd.randint(100, 900)])
write("a_negative_percent.csv", ["month", "region", "margin_pct", "delay_days", "orders"], rows)
plan_a = {"goal": "How has the margin moved?", "understanding": "monthly margin", "kind": "other",
          "columns": [{"name": "month", "semantic_type": "date", "role": "date"},
                      {"name": "region", "semantic_type": "category", "role": "segment"},
                      {"name": "margin_pct", "semantic_type": "percentage", "role": "target", "unit": "%"},
                      {"name": "delay_days", "semantic_type": "duration", "role": "driver", "unit": "days"},
                      {"name": "orders", "semantic_type": "count", "role": "driver"}],
          "operations": [], "primary": "margin_pct",
          "analyses": [{"type": "distribution", "columns": ["margin_pct"]},
                       {"type": "distribution", "columns": ["delay_days"]},
                       {"type": "compare", "columns": ["margin_pct"], "by": "region"}]}
json.dump(plan_a, open(os.path.join(R, "a_plan.json"), "w"))

# B: orders with 12% exact duplicate rows (a double-exported batch); order_id read as identifier.
rows = []
for i in range(200):
    d = "2024-%02d-%02d" % (i % 12 + 1, i % 28 + 1)
    rows.append(["ORD-%05d" % i, d, "C%03d" % rnd.randint(1, 60), round(rnd.uniform(10, 500), 2)])
dups = [list(r) for r in rows[40:64]]      # 24 exact duplicates = 10.7% of 224 rows
rows = rows + dups
write("b_duplicate_ids.csv", ["order_id", "order_date", "customer_id", "amount"], rows)
plan_b = {"goal": "What is total revenue by month?", "understanding": "orders", "kind": "other",
          "columns": [{"name": "order_id", "semantic_type": "identifier", "role": "key"},
                      {"name": "order_date", "semantic_type": "date", "role": "date"},
                      {"name": "customer_id", "semantic_type": "identifier", "role": "key"},
                      {"name": "amount", "semantic_type": "flow_amount", "role": "target", "unit": "USD"}],
          "operations": [], "primary": "amount",
          "analyses": [{"type": "distribution", "columns": ["amount"]}]}
json.dump(plan_b, open(os.path.join(R, "b_plan.json"), "w"))

# C: source_line: a quoted field with a newline, a blank line, a filtered-out aggregate row, and free
# text in a date column. Lines numbered by hand below.
raw = (
    "shipped,site,note,units\r\n"                              # line 1 header
    "2024-01-05,A,\"ok\",10\r\n"                               # line 2
    "2024-01-06,TOTAL,\"aggregate\",999\r\n"                   # line 3 (excluded by the plan)
    "2024-01-07,B,\"two\nline note\",12\r\n"                   # line 4-5 (quoted newline)
    "\r\n"                                                     # line 6 blank
    "pending carrier scan,A,\"x\",13\r\n"                      # line 7  <- date fails
)
for i in range(8, 40):
    raw += "2024-02-%02d,%s,\"n\",%d\r\n" % (i - 7, "AB"[i % 2], i)   # lines 8..39
raw += "about 21,B,\"y\",about 21\r\n"                         # line 40 <- date and count fail
raw += "2024-03-01,A,\"z\",-4\r\n"                              # line 41 <- count fails (negative)
write("c_source_lines.csv", None, None, raw=raw)
plan_c = {"goal": "Units shipped over time", "understanding": "shipments", "kind": "other",
          "columns": [{"name": "shipped", "semantic_type": "date", "role": "date"},
                      {"name": "site", "semantic_type": "category", "role": "segment"},
                      {"name": "units", "semantic_type": "count", "role": "target"}],
          "operations": [{"op": "exclude_rows", "column": "site", "values": ["TOTAL"]}],
          "primary": "units", "analyses": []}
json.dump(plan_c, open(os.path.join(R, "c_plan.json"), "w"))

# D: edge columns. sparse: 90% blank, 1 of 20 filled fails; allbad: every value fails (count read on
# words); amount read as date (12.5 / 3.75: dateutil may read these as dates); year read as date.
rows = []
for i in range(200):
    sparse = "" if i % 10 else str(i // 10)
    if i == 190:
        sparse = "n/a"
    rows.append(["2020-%02d-%02d" % (i % 12 + 1, i % 28 + 1), sparse, rnd.choice(["low", "mid", "high"]),
                 "%.2f" % rnd.uniform(1, 30), str(1990 + i % 30), rnd.choice(["Y", "N", "Y", "N", "maybe"]) if i % 9 == 0 else rnd.choice(["Y", "N"])])
write("d_edges.csv", ["when", "sparse_count", "tier", "amount", "yr", "flag"], rows)
plan_d = {"goal": "What changed?", "understanding": "edges", "kind": "other",
          "columns": [{"name": "when", "semantic_type": "date", "role": "date"},
                      {"name": "sparse_count", "semantic_type": "count", "role": "driver"},
                      {"name": "tier", "semantic_type": "count", "role": "segment"},
                      {"name": "amount", "semantic_type": "date", "role": "driver"},
                      {"name": "yr", "semantic_type": "date", "role": "driver"},
                      {"name": "flag", "semantic_type": "boolean", "role": "segment"}],
          "operations": [], "primary": "", "analyses": []}
json.dump(plan_d, open(os.path.join(R, "d_plan.json"), "w"))

# E: privacy. customer email repeats (a repeat customer) and the AI calls it an identifier; notes
# are free text with a phone number the AI reads as a date; a coded column's values.
rows = []
emails = ["ann%d@example.com" % i for i in range(60)]
for i in range(150):
    e = emails[i % 60] if i < 170 else ""
    note = "call 416-555-%04d" % i if i % 5 == 0 else "2024-0%d-1%d" % (i % 9 + 1, i % 9)
    rows.append(["2024-%02d-%02d" % (i % 12 + 1, i % 28 + 1), e, note, round(rnd.uniform(5, 90), 2)])
write("e_privacy.csv", ["order_date", "customer_email", "notes", "spend"], rows)
plan_e = {"goal": "Spend by month", "understanding": "orders by customer", "kind": "other",
          "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                      {"name": "customer_email", "semantic_type": "identifier", "role": "key"},
                      {"name": "notes", "semantic_type": "date", "role": "metadata"},
                      {"name": "spend", "semantic_type": "flow_amount", "role": "target", "unit": "currency"}],
          "operations": [], "primary": "spend", "analyses": [{"type": "distribution", "columns": ["spend"]}]}
json.dump(plan_e, open(os.path.join(R, "e_plan.json"), "w"))
print("written")
