"""fx_usd_cad.csv: a small file derived from real data, for the placeholder-zero test (tools/test_nl_browser.py).

Source: Statistics Canada, Table 33-10-0036-01, "Daily average foreign exchange rates in Canadian dollars, Bank of
Canada" (the U.S. dollar series, 2017-01-01 to 2026-08-31), as .work/eval/prep_datasets.py cut it into
.work/eval/data/quant_fx_usd.csv. Reproduced and distributed on an "as is" basis under the Statistics Canada Open
Licence. Only REF_DATE, VALUE and STATUS are kept, every row as published: 2,407 rates, 569 blank values marked
STATUS "..", and 550 weekend rows before April 2022 whose VALUE is 0.0000 (no rate that day, written as a zero).

    python tools/fixtures/eval/make_fx.py
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".work", "eval", "data", "quant_fx_usd.csv"))
OUT = os.path.join(HERE, "fx_usd_cad.csv")

with open(SRC, encoding="utf-8-sig", newline="") as fh:
    rows = [(r["REF_DATE"], r["VALUE"], r["STATUS"]) for r in csv.DictReader(fh)]
with open(OUT, "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["REF_DATE", "VALUE", "STATUS"])
    w.writerows(rows)
print("%s: %d rows" % (os.path.relpath(OUT), len(rows)))
