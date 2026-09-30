"""The final review's no-plan run (30 Sep 2026, made-up accounts): its results_for_ai payload, written to
noplan_results.json for the share checks (tools/check_report_pdf.mjs, share_attack.cjs). A run with no plan has no
ai_plan, so the page sends /report context_queries [] (no search).

    python tools/fixtures/review5/noplan_results.py
"""
import json
import os

import _here  # noqa: F401  (paths)
import attack_files as AF
import nl_browser as NB


def main():
    case = [c for c in AF.cases() if c.name == "accounts_regions.csv"][0]
    rep = NB.run(case.data, "accounts.csv", "Which accounts drove revenue growth?", {"staff_name": "keep"},
                 as_of=AF.AS_OF)
    assert rep["ok"] and "ai_plan" not in rep, (rep.get("error"), sorted(rep))
    with open(os.path.join(_here.HERE, "noplan_results.json"), "w", encoding="utf-8") as fh:
        json.dump(NB.results_for_ai(rep), fh, ensure_ascii=True, sort_keys=True)
        fh.write("\n")
    print("wrote noplan_results.json: %d scenario items" % len(NB.results_for_ai(rep)["scenarios"]["items"]))


if __name__ == "__main__":
    main()
