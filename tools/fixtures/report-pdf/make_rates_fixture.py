"""Builds rates-records.json: the line chart, slope and year-on-year heatmap of a table of rates and dollar amounts, for the report PDF's and
the shared viewer's checks (impact7, 9 Oct 2026).

    python3 tools/fixtures/report-pdf/make_rates_fixture.py        # needs numpy and pandas, as the adapter does

{"records": [series_lines (a timeseries), slope, change_heatmap], "report": a report text with a title, "goal": the visitor's question} made by
the adapter (engine/nl_viz.py) on StatCan 10-10-0122-01 trimmed to 15 series (tools/fixtures/structure/regress/s12_statcan_interest_rates):
the percent series are charted in percentage points under the unit "Percent", a series the agency stopped publishing ends where its figures end
and says so in its label, and the dollar series are not drawn on the rates' axis.
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
os.environ["NL_BROWSER_STRICT"] = "1"
sys.path[:0] = [os.path.join(SITE, "..", "northledger-core"), os.path.join(SITE, "engine")]
import nl_browser as NB  # noqa: E402

GOAL = "How have Canadian interest rates and mortgage rates moved since 2000, and which rates rose and fell most?"


def main() -> None:
    data = gzip.open(os.path.join(SITE, "tools", "fixtures", "structure", "regress", "s12_statcan_interest_rates.csv.gz")).read()
    rep = NB.run(data, "1010012201_databaseLoadingData.csv", GOAL, {}, "2026-09-30")
    assert rep["ok"], rep.get("error")
    by = {c["chart"]: c for c in rep["viz"]["charts"]}
    recs = [by["series_lines"], by["slope"], by["change_heatmap"]]
    title = rep["story"]["headline"]
    report = ("Canadian rates: every rate fell since 2000\n\n## Executive summary\n\n- " + title + "\n\n## The headline: rates fell\n"
              "The lead series fell by 3.50 percentage points.\n\n[CHART:1]\n\n[CHART:2]\n\n[CHART:3]\n")
    out = os.path.join(HERE, "rates-records.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"records": recs, "goal": GOAL, "report": report, "period_lines": rep["period"]["lines"]}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("wrote", out, [len(json.dumps(r)) for r in recs])


if __name__ == "__main__":
    main()
