"""The final review's personal-column probes (29 Sep 2026), kept: one small file per case (a date, the column
under test, an amount; 240 rows), landed by the engine, then the engine's scan and the adapter's check
(engine/nl_browser.py _personal_columns) read. Each case says what it should be: "personal" (a miss is a
failure) or "not" (a flag is a false positive: the visitor can Keep it, so it is counted, not failed).

    python tools/fixtures/review3/detector_probe.py            # the adapter in engine/, the engine in ../northledger-core
    python tools/fixtures/review3/detector_probe.py PACKDIR    # an unzipped engine/northledger-browser.zip

tools/test_nl_browser.py imports CASES and run_case: every "personal" case must be flagged, and the adapter
must add nothing to the "not" cases it answers for (the engine's own flags are the engine's)."""
import csv
import datetime
import io
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import warnings

N = 240
FIRST = ["Marisol", "Quintus", "Zelda", "Octavia", "Thaddeus", "Philippa", "Lysander", "Wilhelmina", "Ingrid", "Bartholomew"]
LAST = ["Fairweather", "Vanterpool", "Kowalczyk", "Abernathy", "Delacroix", "Okonkwo"]
COMMON = ["John", "Mary", "James", "Linda", "Robert", "Patricia", "Michael", "Jennifer"]


def full(r):
    return r.choice(FIRST) + " " + r.choice(LAST)


def pick(*xs):
    return lambda r: r.choice(xs)


# name: (header, value maker, expected). Values are made up (no real people).
CASES = {
    # people's names as the reviewer wrote them (detector.py, detector2.py, detector4.py)
    "lower_names": ("customer_name", lambda r: full(r).lower(), "personal"),
    "lower_common": ("customer_name", lambda r: (r.choice(COMMON) + " " + r.choice(LAST)).lower(), "personal"),
    "upper_names": ("customer_name", lambda r: full(r).upper(), "personal"),
    "initial_surname": ("customer", lambda r: r.choice("ABCDEFGHJ") + ". " + r.choice(LAST), "personal"),
    "surname_comma_first": ("member", lambda r: r.choice(LAST) + ", " + r.choice(FIRST), "personal"),
    "cjk_names": ("customer", pick("张伟", "王芳", "李娜", "刘洋", "陈静", "山田太郎", "佐藤花子", "김민준"), "personal"),
    "arabic_names": ("customer", pick("محمد علي", "فاطمة حسن", "أحمد يوسف", "ليلى خالد"), "personal"),
    "cyrillic_names": ("customer", pick("Иван Петров", "Мария Смирнова", "Олег Кузнецов"), "personal"),
    "accented_names": ("customer", pick("José Álvarez", "Zoë Müller", "Nguyễn Văn An", "Seán Ó Briain"), "personal"),
    "particle_names": ("customer", pick("Ludwig van Beethoven", "Maria de la Cruz", "Anne van der Berg", "Leonardo da Vinci"), "personal"),
    "nombre_header": ("nombre", full, "personal"),
    "kunde_header": ("kunde", full, "personal"),
    "technician_header": ("technician", full, "personal"),
    "notes_names": ("notes", full, "personal"),
    "notes_sentences": ("notes", lambda r: "Called " + full(r) + " about the order", "personal"),
    "mixed_55pct": ("customer", lambda r: full(r) if r.random() < 0.55 else r.choice(["walk-in", "n/a", "online", "phone order"]), "personal"),
    "single_first_names": ("customer", pick(*FIRST), "personal"),
    "first_name_col": ("first_name", pick(*FIRST), "personal"),
    "member_lower": ("member", lambda r: full(r).lower(), "personal"),
    "member_upper": ("member", lambda r: full(r).upper(), "personal"),
    "member_initial": ("member", lambda r: r.choice("ABCDEFGHJ") + ". " + r.choice(LAST), "personal"),
    "member_cjk": ("member", pick("张伟", "王芳", "李娜", "刘洋", "陈静", "山田太郎"), "personal"),
    "member_arabic": ("member", pick("محمد علي", "فاطمة حسن", "أحمد يوسف"), "personal"),
    "member_particle": ("member", pick("Ludwig van Beethoven", "Maria de la Cruz", "Anne van der Berg"), "personal"),
    "member_mixed55": ("member", lambda r: full(r) if r.random() < 0.55 else r.choice(["walk-in", "guest pass", "trial"]), "personal"),
    "member_single": ("member", pick(*FIRST), "personal"),
    "member_one_person": ("member", lambda r: "Marisol Fairweather", "personal"),
    "user_one_person": ("user", lambda r: "Quintus Vanterpool", "personal"),
    "member_two_people": ("member", pick("Marisol Fairweather", "Quintus Vanterpool"), "personal"),
    "patient": ("patient", full, "personal"),
    "guest": ("guest", full, "personal"),
    "tenant": ("tenant", full, "personal"),
    "employee": ("employee", full, "personal"),
    "person": ("person", full, "personal"),
    "user": ("user", full, "personal"),
    "attendee": ("attendee", full, "personal"),
    "salesperson": ("salesperson", full, "personal"),
    "rep": ("rep", full, "personal"),
    "agent": ("agent", full, "personal"),
    "driver": ("driver", full, "personal"),
    "assigned_to": ("assigned_to", full, "personal"),
    "created_by": ("created_by", full, "personal"),
    "sold_by": ("sold_by", full, "personal"),
    "author": ("author", full, "personal"),
    "staff": ("staff", full, "personal"),
    "who": ("who", full, "personal"),
    "buyer": ("buyer", full, "personal"),
    "login_email": ("login", lambda r: "%s%d@examplemail.test" % (r.choice(FIRST).lower(), r.randint(1, 99)), "personal"),
    "tel_phone": ("tel", lambda r: "(416) 555-%04d" % r.randint(0, 9999), "personal"),
    "intl_phone": ("mobile", lambda r: "+44 20 7946 %04d" % r.randint(0, 9999), "personal"),
    "phone_nosep": ("mobile", lambda r: "416555%04d" % r.randint(0, 9999), "personal"),
    "location_addr": ("location", lambda r: "%d King Street West" % r.randint(1, 900), "personal"),
    "addr_no_suffix": ("where", lambda r: "%d Rue de Rivoli, Paris" % r.randint(1, 200), "personal"),
    "ship_address": ("ship_to", lambda r: "%d King Street West" % r.randint(1, 900), "personal"),
    "account_4_4_4": ("account", lambda r: "%04d-%04d-%04d" % (r.randint(0, 9999), r.randint(0, 9999), r.randint(0, 9999)), "personal"),
    # more names under headings the review listed (added in the fix)
    "owner_people": ("owner", full, "personal"),
    "assignee": ("assignee", full, "personal"),
    "cliente_header": ("cliente", full, "personal"),
    "account_manager": ("Account Manager", full, "personal"),
    # not personal (detector.py, detector2.py, detector3.py)
    "owner_city": ("owner_city", pick("New York", "Los Angeles", "San Francisco", "Salt Lake City", "Toronto"), "not"),
    "client_companies": ("client", pick("Acme Corp", "Northwind Traders", "Globex Industries", "Blue Sky Logistics", "Initech Systems"), "not"),
    "customer_companies": ("customer", pick("Acme Corp", "Northwind Traders", "Globex Industries", "Blue Sky Logistics", "Initech Systems"), "not"),
    "product_names": ("product", pick("Blue Widget", "Red Gadget Pro", "Steel Bracket", "Oak Table"), "not"),
    "name_products": ("name", pick("Blue Widget", "Red Gadget Pro", "Steel Bracket", "Oak Table"), "not"),
    "user_agent": ("user_agent", pick("Mozilla Firefox", "Google Chrome", "Apple Safari"), "not"),
    "owner_team": ("owner", pick("Sales Team", "Customer Success", "Field Operations"), "not"),
    "member_tier": ("member", pick("Gold Member", "Silver Plus", "Bronze Basic"), "not"),
    "sku_3_3_4": ("sku", lambda r: "%03d-%03d-%04d" % (r.randint(100, 999), r.randint(100, 999), r.randint(0, 9999)), "not"),
    "store_address": ("store_address", lambda r: "%d King Street West" % r.randint(1, 900), "not"),
    "contact_role": ("contact", pick("Head Office", "Main Reception", "Accounts Payable"), "not"),
    "member_since": ("member_since", lambda r: "2021-%02d-01" % r.randint(1, 12), "not"),
    "gold_member_city": ("member", pick("New York", "Los Angeles", "San Francisco", "Salt Lake City"), "not"),
    "pack_size_ct": ("pack_size", pick("12 ct", "24 ct", "6 ct", "36 ct"), "not"),
    "product_ct": ("product", pick("12 Ct Paper Towels", "24 Ct Bottled Water", "6 Ct Large Eggs"), "not"),
    "tile_sqft": ("item", pick("10 Sq Ft Tile", "25 Sq Ft Carpet", "100 Sq Ft Vinyl"), "not"),
    "switch_way": ("sku_desc", pick("3 Way Switch", "4 Way Splitter", "2 Way Radio"), "not"),
    "dated_dots": ("period", lambda r: "%02d.%02d.2024" % (r.randint(1, 28), r.randint(1, 12)), "not"),
    "iso_ts": ("logged", lambda r: "2024-%02d-%02d %02d:%02d" % (r.randint(1, 12), r.randint(1, 28), r.randint(0, 23), r.randint(0, 59)), "not"),
    "range_codes": ("bin", lambda r: "%03d-%03d-%04d" % (r.randint(100, 999), r.randint(100, 999), r.randint(1000, 9999)), "not"),
    "user_apps": ("user", pick("Google Chrome", "Mozilla Firefox", "Apple Safari"), "not"),
    "member_levels": ("member", pick("Gold Member", "Silver Member", "Platinum Elite"), "not"),
    "person_roles": ("person", pick("Sales Manager", "Field Technician", "Account Executive"), "not"),
    "pack_dr_pepper": ("product", pick("12 Pack Dr Pepper", "6 Pack Dr Pepper Zero"), "not"),
    "paid_by_method": ("paid_by", pick("Card", "EFT", "Cheque"), "not"),       # tools/make_ui_fixtures.py cafe invoices
    "paid_by_people": ("paid_by", full, "personal"),
    # the known limit the page states before anything is sent (src/js/50-try.js AI_CONSENT: "names under a
    # heading like "Stylist""): rare names under a heading no list knows. If a change starts flagging it,
    # the page's example must change with it
    "stylist_names": ("Stylist", full, "limit"),
}
# the not-personal cases the adapter answers for: the engine's scan flags the others by their header or shape
# (owner_city, client/customer companies, name_products, owner_team, sku_3_3_4, store_address, contact_role,
# range_codes), which this check never clears
ADAPTER_MUST_NOT_ADD = ("member_tier", "pack_size_ct", "product_ct", "tile_sqft", "switch_way", "user_apps",
                        "member_levels", "person_roles", "pack_dr_pepper", "user_agent", "product_names",
                        "member_since", "dated_dots", "iso_ts", "paid_by_method")


def file_for(key):
    hdr, make, _ = CASES[key]
    r = random.Random(key)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order_date", hdr, "amount"])
    start = datetime.date(2022, 1, 1)
    for i in range(N):
        w.writerow([(start + datetime.timedelta(days=i * 3)).isoformat(), make(r), "%.2f" % (10 + (i * 37) % 500)])
    return buf.getvalue().encode("utf-8")


def run_case(key, NB, E):
    """(the header, the engine's flag "decision(kinds)" or "-", the adapter's added kind or "-")."""
    hdr = CASES[key][0]
    tmp = tempfile.mkdtemp(prefix="nl_det_")
    try:
        src = os.path.join(tmp, "in")
        os.makedirs(src)
        p = os.path.join(src, key + ".csv")
        with open(p, "wb") as fh:
            fh.write(file_for(key))
        eng = E.open_engagement(os.path.join(tmp, "eng"), create=True)
        res = E.land(eng, p)
        con = sqlite3.connect(eng.db_path)
        rows = con.execute("SELECT column_name, kinds, decision FROM %s WHERE table_name=?" % E.COLUMNS_TABLE,
                           (res.table,)).fetchall()
        con.close()
        colmap = dict(getattr(res, "column_map", {}) or {})
        added = NB._personal_columns(eng.db_path, res.table, colmap, {r[0] for r in rows})
        landed = colmap.get(hdr, hdr)
        e = [r for r in rows if r[0] == landed]
        a = [k for c, k in added if c == landed]
        return hdr, ("%s(%s)" % (e[0][2], e[0][1])) if e else "-", a[0] if a else "-"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    warnings.simplefilter("ignore")
    here = os.path.dirname(os.path.abspath(__file__))
    site = os.path.normpath(os.path.join(here, "..", "..", ".."))
    if len(argv) > 1:
        sys.path[:] = [p for p in sys.path if "northledger-core" not in p and os.path.abspath(p) != site]
        sys.path.insert(0, os.path.abspath(argv[1]))
        import nl_browser as NB
        NB._install_stubs()
        NB._import_engine()
    else:
        sys.path.insert(0, os.path.join(site, "..", "northledger-core"))
        sys.path.insert(0, os.path.join(site, "engine"))
        import nl_browser as NB
    from northledger import engagement as E
    print("%-20s %-16s %-9s %-34s %-15s %s" % ("case", "header", "expect", "engine scan", "adapter added", "verdict"))
    misses, fp_engine, fp_adapter = [], [], []
    for key, (hdr, _, want) in CASES.items():
        h, eng, add = run_case(key, NB, E)
        flagged = eng != "-" or add != "-"
        if want == "personal" and not flagged:
            verdict = "MISS"
            misses.append(key)
        elif want == "not" and add != "-":
            verdict = "false positive (adapter)"
            fp_adapter.append(key)
        elif want == "not" and eng != "-":
            verdict = "false positive (engine)"
            fp_engine.append(key)
        elif want == "limit":
            verdict = "flagged: the page's example of a miss is wrong" if flagged else "missed (the page says so)"
        else:
            verdict = "ok"
        print("%-20s %-16s %-9s %-34s %-15s %s" % (key, h[:16], want, eng[:34], add, verdict))
    print("\nmisses: %d %s" % (len(misses), misses))
    print("false positives added by the adapter: %d %s" % (len(fp_adapter), fp_adapter))
    print("false positives of the engine's own scan: %d %s" % (len(fp_engine), fp_engine))
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
