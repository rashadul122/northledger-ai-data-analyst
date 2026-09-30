"""The final review's search attack (30 Sep 2026), re-run on the allow-list design: every string the review sent past
the old block list (attack_files.py, all made up), and every name itself, is planted in every field of the plan's
search items and in its old free-text context_queries; each file also runs through the adapter itself. Prints every
search built and every file value found in one (outside a list term it holds). Writes allowlist_attack.out.

    python tools/fixtures/review5/allowlist_attack.py
"""
import io
import json
import os
import sys
from contextlib import redirect_stdout

import _here  # noqa: F401  (paths)
import attack_files as AF
import nl_browser as NB


def main() -> int:
    with open(os.path.join(_here.SITE, "engine", "context_terms.json"), encoding="utf-8") as fh:
        t = json.load(fh)
    print("list: engine/context_terms.json version %s: %d indicators, %d sectors, %d regions"
          % (t["version"], len(t["indicators"]), len(t["sectors"]), len(t["regions"])))
    total_items = total_built = 0
    reached = []
    for case in AF.cases():
        vals = sorted({NB._term_key(v) for v in AF.file_values(case.data)} - {""})
        items = []
        for s in case.strings:
            items += [{"indicator": s}, {"indicator": "retail sales", "sector": s}, {"indicator": "retail sales", "region": s},
                      {"indicator": "retail sales", "region": "Canada", "years": [2025], "query": s},
                      {"indicator": "retail sales", "years": [s]}]
        built, dropped = [], 0
        for i in range(0, len(items), NB.CONTEXT_READ):
            q, why, _k = NB._context_queries(items[i:i + NB.CONTEXT_READ])
            built += q
            dropped += len(why)
        plan = {"goal": "g", "columns": case.columns, "operations": [], "analyses": [], "primary": "",
                "context_queries": list(case.strings[:12]),
                "context": [{"indicator": "retail sales", "region": s} for s in case.strings[:3]] +
                           [{"indicator": "retail sales", "region": "Ontario", "years": [2024, 2025]}]}
        rep = NB.run(case.data, case.name, "", dict(case.decisions, __plan__=plan), AF.AS_OF)
        ap = rep.get("ai_plan") or {}
        run_q = list(ap.get("context_queries") or [])
        hits = []
        for q, it in [(q, None) for q in built] + list(zip(run_q, ap.get("context") or [])):
            used = [NB._term_key(it[f]) for f in ("sector", "indicator", "region") if it and it.get(f)] or \
                   [NB._term_key(x) for x in t["indicators"] + t["sectors"] + t["regions"] if (" %s " % NB._term_key(x)) in (" %s " % NB._term_key(q))]
            for v in vals:
                if (" %s " % v) in (" %s " % NB._term_key(q)) and not any(v in u for u in used):
                    hits.append((v, q))
        reached += [(case.name,) + h for h in hits]
        total_items += len(items)
        total_built += len(built) + len(run_q)
        print("\n== %s (%d attack strings, %d items planted: %d dropped)" % (case.name, len(case.strings), len(items), dropped))
        print("   searches built from the planted items: %s" % sorted(set(built)))
        print("   the run (ok %s): the old free-text context_queries ignored; context_queries %s; dropped %s"
              % (rep.get("ok"), run_q, ap.get("context_queries_dropped")))
        print("   file values in a search, outside a list term: %d" % len(hits))
    print("\nTOTAL: %d items planted in %d files, %d searches built, %d names or file values reaching a search"
          % (total_items, len(AF.cases()), total_built, len(reached)))
    for r in reached[:20]:
        print("   REACHED", r)
    return 1 if reached else 0


if __name__ == "__main__":
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main()
    out = buf.getvalue()
    sys.stdout.write(out)
    with open(os.path.join(_here.HERE, "allowlist_attack.out"), "w", encoding="utf-8") as fh:
        fh.write(out)
    sys.exit(rc)
