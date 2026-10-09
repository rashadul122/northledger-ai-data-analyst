"""Builds timeseries-records.json: the line chart's records for the report PDF's checks (impact6, 9 Oct 2026).

    python3 tools/fixtures/report-pdf/make_timeseries_fixture.py        # needs numpy and pandas, as the adapter does

{"records": [...]} holds three timeseries records, each made by the adapter (engine/nl_viz.py) on a real file's rows:
  1  StatCan 10-10-0002-01 (the repo's copy, tools/fixtures/structure/regress): five lines of monthly figures published in
     17 runs (September to March each year), so 16 gaps
  2  six of Statistics Canada's daily exchange-rate series (33-10-0036-01, a subset in the same folder), one an index: a table
     in two units draws its lead alone, one line of monthly averages
  3  the same daily table with every series in one unit: five lines
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
REG = os.path.join(SITE, "tools", "fixtures", "structure", "regress")
os.environ["NL_BROWSER_STRICT"] = "1"
sys.path[:0] = [os.path.join(SITE, "..", "northledger-core"), os.path.join(SITE, "engine")]
import nl_browser as NB  # noqa: E402


def line(name, data, as_of):
    rep = NB.run(data, name, "", {}, as_of)
    assert rep["ok"], rep.get("error")
    rec = next(c for c in rep["viz"]["charts"] if c["chart"] == "series_lines")
    return rec


def main():
    s07 = gzip.open(os.path.join(REG, "s07_statcan_debt_unpublished_months.csv.gz")).read()
    fx = gzip.open(os.path.join(REG, "s10_statcan_daily_fx_subset.csv.gz")).read()
    recs = [line("debt.csv", s07, "2026-09-30"), line("fx.csv", fx, "2026-09-29"),
            line("fx1.csv", fx.replace(b"Index, 1992=100", b"Dollars"), "2026-09-29")]
    for i, r in enumerate(recs):
        r["id"] = "viz.%d.series_lines" % (i + 1)
    with open(os.path.join(HERE, "timeseries-records.json"), "w", encoding="utf-8") as fh:
        json.dump({"records": recs}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("wrote timeseries-records.json:", [len(json.dumps(r)) for r in recs], "bytes")


if __name__ == "__main__":
    main()
