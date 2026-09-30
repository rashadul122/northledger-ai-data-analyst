"""python run_case.py PACKDIR CSV PLAN [DECISIONS_JSON] [--off col,col] [--full]
Runs the packed engine (source trees hidden, as in the browser) with a hand-written plan."""
import json, os, sys, warnings

warnings.simplefilter("ignore")
pack, csvp, planp = sys.argv[1], sys.argv[2], sys.argv[3]
rest = sys.argv[4:]
dec = {}
off = None
full = "--full" in rest
if "--off" in rest:
    off = rest[rest.index("--off") + 1].split(",")
for a in rest:
    if a.endswith(".json"):
        dec = json.load(open(a))
sys.path[:] = [p for p in sys.path if "northledger-core" not in p and "portfolio-website" not in p]
sys.path.insert(0, pack)
import nl_browser as NB  # noqa: E402

d = dict(dec)
d["__plan__"] = json.load(open(planp))
if off is not None:
    d["__contracts_off__"] = off
rep = NB.run(open(csvp, "rb").read(), os.path.basename(csvp), "", d, "2026-09-15")
print("ok:", rep.get("ok"), rep.get("error"))
print("input rows:", rep["input"].get("rows"))
print("cleaning:", {k: rep["cleaning"][k] for k in ("rows_in", "rows_clean", "rows_quarantined")})
print("quarantine reasons:", rep["cleaning"]["quarantine_reasons"][:6])
print("flagged:", rep["privacy"]["flagged"])
k = rep.get("contracts")
if k:
    for t in k["tests"]:
        print("  TEST", json.dumps(t))
    print("  cells_flagged:", k.get("cells_flagged"), "line:", k.get("line"))
print("--- contract_flagged_csv:")
print(rep["downloads"].get("contract_flagged_csv", "(none)")[:2000])
print("--- plan applied/refused:", (rep.get("ai_plan") or {}).get("applied"), (rep.get("ai_plan") or {}).get("refused"))
print("--- plan_signals:", json.dumps(rep.get("plan_signals")))
for a in (rep.get("ai_analyses") or {}).get("items") or []:
    print("  ANALYSIS:", a.get("sentence"))
print("  analyses refused:", (rep.get("ai_analyses") or {}).get("refused"))
print("--- roles:", rep.get("roles"))
print("--- headline:", rep["story"].get("headline"))
rfa = NB.results_for_ai(rep)
print("--- results_for_ai.plan_applied:", json.dumps(rfa.get("plan_applied")))
if full:
    print("--- quarantine_csv head:")
    print("\n".join(rep["downloads"]["quarantine_csv"].splitlines()[:12]))
    print("--- clean_csv head:")
    print("\n".join(rep["downloads"]["clean_csv"].splitlines()[:6]))
    print("--- findings:")
    for f in rep["findings"][:8]:
        print("  ", f["verdict"], f["kind"], f["claim"][:200])
json.dump(rep, open(csvp + "." + os.path.basename(pack.rstrip("/")) + ".rep.json", "w"), default=str)
json.dump(rfa, open(csvp + "." + os.path.basename(pack.rstrip("/")) + ".rfa.json", "w"), default=str)
