"""Synthetic files with personal columns for the privacy re-review (no real data)."""
import csv, random, datetime, os
random.seed(7)
HERE = os.path.dirname(os.path.abspath(__file__))
FIRST = ["Zelda", "Quintus", "Marisol", "Octavia", "Bartholomew", "Ingrid", "Thaddeus", "Philippa", "Lysander", "Wilhelmina"]
LAST = ["Vanterpool", "Okonkwo-Reyes", "Fairweather", "Kowalczyk", "Abernathy", "Delacroix"]

def people(n):
    out = []
    for i in range(n):
        f, l = random.choice(FIRST), random.choice(LAST)
        out.append((f + " " + l, "%s.%s%d@examplemail.test" % (f.lower(), l.lower().replace("-", ""), i % 40),
                    "416-555-%04d" % (1000 + i % 60)))
    return out

# A: order lines, 3 years, with personal columns: name, email, phone, date of birth (a date!), and a
# customer_since date. Some unreadable cells in the business columns so the tests fail.
rows = []
start = datetime.date(2022, 1, 3)
ppl = people(400)
for i in range(1200):
    d = start + datetime.timedelta(days=random.randint(0, 3 * 365))
    nm, em, ph = random.choice(ppl)
    dob = datetime.date(1950 + random.randint(0, 50), random.randint(1, 12), random.randint(1, 28))
    rev = round(random.uniform(20, 900), 2)
    units = random.randint(1, 40)
    disc = random.choice([0, 5, 10, 15])
    date_s = d.isoformat()
    if i % 17 == 0:
        date_s = "awaiting Zelda Vanterpool"            # a value that quotes a person, in a business column
    rev_s = "%.2f" % rev
    if i % 23 == 0:
        rev_s = "ask Marisol"
    rows.append({"order_no": 500000 + i, "order_date": date_s, "region": random.choice(["North", "South", "East", "West"]),
                 "customer_name": nm, "customer_email": em, "phone": ph, "date_of_birth": dob.isoformat(),
                 "units": units, "revenue": rev_s, "discount_pct": disc})
with open(os.path.join(HERE, "a_personal.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

# B: the only date column is personal (date_of_birth) plus a signup_date that is flagged too? and a year col
rows = []
for i in range(300):
    nm, em, ph = random.choice(ppl)
    dob = datetime.date(1940 + random.randint(0, 60), random.randint(1, 12), random.randint(1, 28))
    rows.append({"member": nm, "birth_date": dob.isoformat(), "plan": random.choice(["Basic", "Plus", "Pro"]),
                 "monthly_fee": random.choice([10, 20, 35]), "visits": random.randint(0, 30)})
with open(os.path.join(HERE, "b_dob_only.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
print("ok")
