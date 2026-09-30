"""The final review's privacy flow (29 Sep 2026), kept and widened to every fixture: for each file, the page's
steps with the adapter (the scan, the planner's profile after the visitor's choices, a run with the AI's plan
and its re-plan signals, and the report writer's results), under several choices. grep.mjs then builds the
exact /plan bodies (first plan and re-plan) with the page's own filters and the proxy's own validation and
prompt, and the /report body, and greps them for every personal value, every withheld column's name, every
code and the file's name.

    python tools/fixtures/review3/e2e/flow.py [--optin off|one] OUTDIR [PACKDIR]    # writes OUTDIR/flow.json and private_mix.csv
    node tools/fixtures/review3/e2e/grep.mjs OUTDIR

--optin (option B, the owner's decision of 29 Sep 2026: a flagged column the visitor keeps goes to the AI, values
included, once they tick the box that names it):
  off  no choice keeps a flagged column (every set that keeps one is left out): nothing personal may be sent
  one  one set per flagged column, keeping that column alone (every other one withheld, the default): only the
       kept column's values may be sent, never a withheld column's values or name
Without --optin, the review's own mix of choices (some keep, some code, some withhold).

The files: private_mix.csv (the reviewer's: a date of birth, an email, a staff name, a member's name, notes
naming people and a phone, and an amount cell that quotes a member), made here, and every CSV under
tools/fixtures. Without PACKDIR the adapter in engine/ runs with the engine in ../northledger-core."""
import csv
import datetime
import glob
import io
import json
import os
import random
import sys
import warnings

warnings.simplefilter("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.normpath(os.path.join(HERE, "..", ".."))
SITE = os.path.normpath(os.path.join(FX, "..", ".."))
AS_OF = "2026-09-15"
# plans and choices beside a file that the file's prefix does not name
PLAN_FOR = {"g_sparse_date_wrong_axis.csv": "f_plan.json", "n6b_pct_stray.csv": "n6_plan.json",
            "n3b_line_items.csv": "n3_plan.json"}


def private_mix():
    """The reviewer's file (final-review e2e/make_and_run.py, seed 29): 900 orders."""
    random.seed(29)
    F1 = ["Marisol", "Quintus", "Zelda", "Octavia"]
    L1 = ["Fairweather", "Vanterpool", "Kowalczyk"]
    F2 = ["Bartholomew", "Wilhelmina", "Lysander"]
    L2 = ["Okonkwo", "Delacroix"]
    STAFF = ["Dana Whitfield", "Marco Bellini", "Priya Raman", "Tomasz Nowak"]
    rows = []
    for i in range(900):
        d = datetime.date(2021, 1, 4) + datetime.timedelta(days=i * 1)
        dob = datetime.date(1950 + random.randint(0, 50), random.randint(1, 12), random.randint(1, 28)).isoformat()
        m = random.choice(F1) + " " + random.choice(L1)
        note = random.choice(["call " + random.choice(F2) + " " + random.choice(L2), "left voicemail for " + random.choice(F2),
                              "paid in full", "ask " + random.choice(L2)])
        email = "%s.%s%d@examplemail.test" % (random.choice(F2).lower(), random.choice(L2).lower(), i % 50)
        phone = "416-555-%04d" % (1000 + i % 70)
        amt = "%.2f" % (20 + (i * 7) % 480)
        if i % 41 == 0:
            amt = "see " + m
        rows.append([d.isoformat(), random.choice(["North", "South", "East", "West"]), amt, str(1 + i % 9), dob, email,
                     random.choice(STAFF), m, note, phone])
    hdr = ["order_date", "region", "amount", "units", "Date Of Birth", "customer_email", "Staff Name", "member", "notes", "phone"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(hdr)
    w.writerows(rows)
    return buf.getvalue().encode(), sorted(set(F1 + L1 + F2 + L2))


PRIVATE_MIX_PLAN = {
    "goal": "How does amount move by month and region?", "understanding": "orders", "kind": "transactions",
    "columns": [{"name": "order_date", "semantic_type": "date", "role": "date"},
                {"name": "amount", "semantic_type": "percentage", "role": "target"},
                {"name": "units", "semantic_type": "count", "role": "measure"},
                {"name": "region", "semantic_type": "category", "role": "dimension"},
                {"name": "Date Of Birth", "semantic_type": "date", "role": "metadata"},
                {"name": "member", "semantic_type": "entity", "role": "entity"},
                {"name": "notes", "semantic_type": "count", "role": "measure"},
                {"name": "Staff Name", "semantic_type": "count", "role": "measure"}],
    "operations": [], "primary": "amount",
    "analyses": [{"type": "compare", "columns": ["amount"], "by": "member"}, {"type": "trend", "columns": ["Date Of Birth"]},
                 {"type": "rank", "columns": ["amount"], "by": "Staff Name"}, {"type": "distribution", "columns": ["units"]}]}
PRIVATE_MIX_SETS = {
    "D1": {"date_of_birth": "withhold", "customer_email": "code", "staff_name": "keep", "member": "withhold", "notes": "code", "phone": "withhold"},
    "D2": {"date_of_birth": "code", "customer_email": "withhold", "staff_name": "withhold", "member": "code", "notes": "withhold", "phone": "code"},
    "D3": {},
    "D4": {"member": "keep", "date_of_birth": "keep"},
}


def plan_for(path):
    base = os.path.basename(path)
    d = os.path.dirname(path)
    names = [PLAN_FOR.get(base)] if base in PLAN_FOR else []
    pre = base.split("_")[0]
    names += [pre + "_plan.json", pre.rstrip("abcdefghijklmnopqrstuvwxyz") + "_plan.json"]
    for n in names:
        if n and os.path.exists(os.path.join(d, n)):
            return json.load(open(os.path.join(d, n), encoding="utf-8"))
    return None


def dec_for(path):
    pre = os.path.basename(path).split("_")[0]
    p = os.path.join(os.path.dirname(path), pre + "_dec.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def optin_sets(sets, flagged, mode):
    """The choices a run makes under --optin: off leaves out every set that keeps a column; one is a set per
    flagged column that can be kept (not coded as it arrived), keeping that column alone."""
    if mode == "off":
        return {k: v for k, v in sets.items() if "keep" not in v.values()}
    if mode == "one":
        return {"keep_" + f["column"]: {f["column"]: "keep"} for f in flagged if "coded as it arrived" not in f["kind"]}
    return sets


def case(NB, data, name, plan, sets, tokens=None, mode=None):
    rows = list(csv.reader(data.decode("utf-8", "replace").splitlines()))
    hdr, body = (rows[0], rows[1:]) if rows else ([], [])
    values = {h: sorted(set(r[j] for r in body if j < len(r))) for j, h in enumerate(hdr)}
    scan = NB.run(data, name, "", None, AS_OF)
    flagged = [{"column": f["column"], "kind": f["kind"]} for f in (scan.get("privacy") or {}).get("flagged") or []]
    if not sets:
        sets = {"default": {}}
        if flagged:
            sets["code_all"] = {f["column"]: "code" for f in flagged}
            sets["keep_first"] = {flagged[0]["column"]: "keep"}
    sets = optin_sets(sets, flagged, mode)
    out = {"name": name, "ok": scan.get("ok"), "flagged": flagged, "values": values, "hdr": hdr, "tokens": tokens or [],
           "plan": plan, "optin": mode, "sets": {}}
    for k, dec in sets.items():
        pj = json.loads(NB.plan_profile_json(data, name, json.dumps({f["column"]: f["kind"] for f in flagged}), json.dumps(dec), AS_OF))
        d = dict(dec)
        if plan:
            d["__plan__"] = plan
        rep = NB.run(data, name, "", d, AS_OF)
        out["sets"][k] = {"decisions": dec, "profile": pj.get("profile"), "landed": pj.get("landed"),
                          "plan_signals": rep.get("plan_signals") or [], "results": NB.results_for_ai(rep) if rep.get("ok") else None,
                          "rep_flagged": (rep.get("privacy") or {}).get("flagged")}
    return out


def main(argv):
    mode = None
    if len(argv) > 2 and argv[1] == "--optin":
        mode = argv[2]
        if mode not in ("off", "one"):
            sys.exit("--optin takes off or one")
        argv = argv[:1] + argv[3:]
    outdir = os.path.abspath(argv[1])
    os.makedirs(outdir, exist_ok=True)
    if len(argv) > 2:
        sys.path[:] = [p for p in sys.path if "northledger-core" not in p and os.path.abspath(p) != SITE]
        sys.path.insert(0, os.path.abspath(argv[2]))
    else:
        sys.path.insert(0, os.path.join(SITE, "..", "northledger-core"))
        sys.path.insert(0, os.path.join(SITE, "engine"))
    import nl_browser as NB
    data, tokens = private_mix()
    open(os.path.join(outdir, "private_mix.csv"), "wb").write(data)
    cases = {"private_mix.csv": case(NB, data, "private_mix.csv", PRIVATE_MIX_PLAN, PRIVATE_MIX_SETS, tokens, mode)}
    for path in sorted(glob.glob(os.path.join(FX, "*", "*.csv"))):
        rel = os.path.relpath(path, FX)
        dec = dec_for(path)
        sets = None
        if dec:
            sets = {"default": {}, "fixture_choices": dec}
        cases[rel] = case(NB, open(path, "rb").read(), os.path.basename(path), plan_for(path), sets, None, mode)
        print("%-40s flagged: %s" % (rel, [(f["column"], f["kind"]) for f in cases[rel]["flagged"]]))
    json.dump(cases, open(os.path.join(outdir, "flow.json"), "w"), default=str)
    print("wrote", os.path.join(outdir, "flow.json"), "with", len(cases), "files", "(--optin %s)" % mode if mode else "")


if __name__ == "__main__":
    main(sys.argv)
