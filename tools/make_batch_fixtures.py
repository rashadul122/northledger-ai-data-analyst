#!/usr/bin/env python3
"""Synthetic stand-ins for three kinds of file a visitor brings (impact6, 9 Oct 2026), in the shapes real exports have:

    python tools/make_batch_fixtures.py        # writes tools/fixtures/batch/*.csv (deterministic: fixed seeds)

  sales_ledger.csv     an accounting export: one row an invoice line over two years, an invoice number, customer and
                       rep names, a unit price, a discount percent, a line revenue that is quantity x price x discount,
                       a "Total" row at the foot (as spreadsheets export them)
  survey_responses.csv one row a respondent: an age band, a region, a gender (sensitive), five 1-5 agreement ratings
                       written as words ("Agree"), a 0-10 recommend score, a free-text comment, the date answered
  worldbank_wide.csv   the World Bank's download shape: four lines of preamble, then country x indicator rows with one
                       column per year (1990-2022), blanks where the Bank has no value, a trailing comma on every line

Every figure is made up; the shapes are the point. Standard library only.
"""
import csv
import io
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "batch")


def sales_ledger():
    rnd = random.Random(20261009)
    regions = ["North", "South", "East", "West"]
    products = [("Standing desk", "Furniture", 640.0), ("Task chair", "Furniture", 310.0), ("Monitor arm", "Accessories", 95.0),
                ("USB-C dock", "Accessories", 180.0), ("27in display", "Electronics", 420.0), ("Webcam", "Electronics", 85.0),
                ("Desk lamp", "Accessories", 48.0), ("Filing cabinet", "Furniture", 270.0)]
    customers = ["Harbourview Dental", "Maple & Finch Law", "Northgate Logistics", "Brightside Schools", "Cedar Row Realty",
                 "Quill Print Co", "Tidewater Clinic", "Ironbridge Works", "Lantern Cafe Group", "Summit Insurance",
                 "Redfern Studios", "Oakline Builders"]
    reps = ["Anita Rahman", "Carlos Mendes", "Priya Nair", "Tom Okafor"]
    rows, inv = [], 10000
    day0 = 0
    import datetime as dt
    start = dt.date(2024, 1, 1)
    total = 0.0
    for d in range(0, 731):
        day = start + dt.timedelta(days=d)
        if day.weekday() >= 5:
            continue
        season = 1.0 + (0.35 if day.month in (9, 10, 11) else 0.0) + 0.0006 * d      # autumn peak and slow growth
        for _ in range(int(rnd.gauss(3.0 * season, 1.2))):
            inv += 1
            cust, rep, reg = rnd.choice(customers), reps[rnd.randrange(4)], regions[rnd.randrange(4)]
            for _l in range(rnd.randint(1, 3)):
                name, cat, price = rnd.choice(products)
                q = rnd.randint(1, 6)
                disc = rnd.choice([0, 0, 0, 5, 10])
                rev = round(q * price * (1 - disc / 100.0), 2)
                total += rev
                rows.append([inv, day.isoformat(), cust, reg, name, cat, q, "%.2f" % price, disc, "%.2f" % rev, rep])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Invoice No", "Invoice Date", "Customer", "Region", "Product", "Category", "Qty", "Unit Price", "Discount %", "Line Revenue", "Sales Rep"])
    w.writerows(rows)
    w.writerow(["Total", "", "", "", "", "", sum(int(r[6]) for r in rows), "", "", "%.2f" % total, ""])
    return buf.getvalue()


def survey():
    rnd = random.Random(7)
    ages = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
    regions = ["Ontario", "Quebec", "British Columbia", "Alberta", "Atlantic", "Prairies"]
    scale = ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"]
    comments = ["Easy to use and quick to set up", "Support took too long to reply", "Pricing is fair for what we get",
                "The reports are hard to read on a phone", "Love the weekly summary email", "Missing an export to Excel",
                "Onboarding was smooth", "Too many notifications", "", "", "Would recommend to a friend", "Slow on large files"]
    import datetime as dt
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Respondent ID", "Date Answered", "Age Group", "Gender", "Region", "Easy to use", "Good value", "Reliable",
                "Support is helpful", "Would renew", "Likelihood to recommend (0-10)", "Comments"])
    for i in range(1, 861):
        bias = rnd.gauss(0, 0.8)
        likert = lambda b: scale[max(0, min(4, int(round(2.6 + b + bias + rnd.gauss(0, 0.9)))))]
        score = max(0, min(10, int(round(7 + 1.4 * bias + rnd.gauss(0, 2)))))
        d = dt.date(2026, 5, 4) + dt.timedelta(days=rnd.randint(0, 60))
        w.writerow(["R%04d" % i, d.isoformat(), rnd.choice(ages), rnd.choice(["Female", "Male", "Non-binary", "Prefer not to say"]),
                    rnd.choice(regions), likert(0.4), likert(-0.2), likert(0.1), likert(-0.4), likert(0.0), score, rnd.choice(comments)])
    return buf.getvalue()


def worldbank():
    rnd = random.Random(1990)
    countries = [("Canada", "CAN"), ("United States", "USA"), ("Germany", "DEU"), ("Japan", "JPN"), ("India", "IND"), ("Brazil", "BRA"),
                 ("Bangladesh", "BGD"), ("Nigeria", "NGA"), ("Australia", "AUS"), ("Mexico", "MEX"), ("France", "FRA"),
                 ("South Africa", "ZAF"), ("Kenya", "KEN"), ("Vietnam", "VNM"), ("Norway", "NOR")]
    years = list(range(1990, 2023))
    inds = [("GDP (current US$)", "NY.GDP.MKTP.CD"), ("Life expectancy at birth, total (years)", "SP.DYN.LE00.IN")]
    buf = io.StringIO()
    buf.write('"Data Source","World Development Indicators",\n\n"Last Updated Date","2025-07-01",\n\n')
    w = csv.writer(buf, lineterminator=",\n", quoting=csv.QUOTE_ALL)
    w.writerow(["Country Name", "Country Code", "Indicator Name", "Indicator Code"] + [str(y) for y in years])
    for ci, (name, code) in enumerate(countries):
        base = [4e11, 9e12, 2.2e12, 3.5e12, 3.2e11, 4.6e11, 3.9e10, 3.5e10, 3.1e11, 2.6e11, 1.3e12, 1.2e11, 8e9, 6e9, 1.2e11][ci]
        growth = rnd.uniform(0.02, 0.07)
        for ind, icode in inds:
            row = [name, code, ind, icode]
            v = base
            le = rnd.uniform(52, 76)
            for k, y in enumerate(years):
                v *= 1 + growth + rnd.gauss(0, 0.03)
                le += rnd.uniform(0.1, 0.35)
                val = ("%.0f" % v) if icode.startswith("NY") else ("%.4f" % le)
                if rnd.random() < 0.02 or (code in ("VNM", "KEN") and y < 1995):
                    val = ""
                row.append(val)
            w.writerow(row)
    return buf.getvalue()


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("sales_ledger.csv", sales_ledger), ("survey_responses.csv", survey), ("worldbank_wide.csv", worldbank)):
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(fn())
        print("wrote", name, os.path.getsize(os.path.join(OUT, name)), "bytes")


if __name__ == "__main__":
    main()
