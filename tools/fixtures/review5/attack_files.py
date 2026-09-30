"""The final review's attack files (30 Sep 2026), rebuilt here as made-up data: every name, company, email and code
below is invented. The review's block-list check of the planner's free-text web searches let these names through
(a client column typed as a category, accented, "ue", CJK and Cyrillic spellings, two-letter surnames, cp1252 and
"|" files, and its "ordinary words" rule). The searches are now built only from engine/context_terms.json; the
adapter tests (tools/test_nl_browser.py) and allowlist_attack.py plant every string below in every field of the
plan's search items, and in its old free-text context_queries, and check that none reaches a search.

cases() -> [Case]: name, the file's bytes, the plan's column readings, the visitor's choices, the attack strings
(the review's queries and the names themselves), and the file's own values (for the check that none is in a search).
Deterministic (fixed seeds). Standard library only.
"""
import csv
import io
import random
from collections import namedtuple

Case = namedtuple("Case", "name data columns decisions strings")
MONTHS = ["2025-%02d-01" % m for m in range(1, 13)]
AS_OF = "2026-01-31"


def _csv(header, rows, sep=","):
    b = io.StringIO()
    w = csv.writer(b, delimiter=sep, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return b.getvalue()


def _col(name, role, st):
    return {"name": name, "role": role, "semantic_type": st}


def cases():
    out = []
    rng = random.Random(1)
    # 1. a client named in the notes; the client column read as an entity, then as a segment (a groupable column)
    clients = ["Acme Holdings Ltd", "Globex Corporation", "The Kettleby Group", "Initech", "Hooli"]
    rows = []
    for i in range(600):
        note = rng.choice(["called back", "sent quote", "follow up next week", "discussed renewal"])
        if i % 3 == 0:
            note += " ; parent is Acme Holdings, see Globex terms, Globex"
        rows.append([MONTHS[i % 12], clients[i % 5] if i >= 25 else "Initech", str(100 + i), note])
    data = _csv(["date", "client", "revenue", "notes"], rows).encode()
    strings = ["Acme Holdings market share 2025", "Acme market share 2025", "Globex industry outlook 2025",
               "Kettleby Group revenue 2025", "The Kettleby Group revenue", "Initech", "Hooli search engine market 2025",
               "Globex Corporation news", "acme holdings ltd", "Acme Holdings", "Globex", "Kettleby", "Hooli"]
    for role, st in (("entity", "entity"), ("segment", "category")):
        out.append(Case("clients_%s.csv" % role, data, [_col("date", "date", "date"), _col("client", role, st),
                                                         _col("revenue", "target", "flow_amount"),
                                                         _col("notes", "metadata", "free_text")], {}, strings))
    # 2. the "ordinary words" rule: a real client said often in the comments
    rows = []
    for i in range(400):
        c = "Northwind Traders" if i < 2 else rng.choice(["Contoso", "Fabrikam", "Tailspin Toys", "Wingtip"])
        note = "shipment delayed" if i % 2 else "Northwind Traders referral; Northwind Traders pricing matched"
        rows.append([MONTHS[i % 12], c, str(50 + i), note])
    out.append(Case("northwind.csv", _csv(["date", "customer_account", "amount", "comments"], rows).encode(),
                    [_col("date", "date", "date"), _col("customer_account", "entity", "entity"),
                     _col("amount", "target", "flow_amount"), _col("comments", "metadata", "free_text")], {},
                    ["Northwind Traders logistics outlook 2025", "Northwind Traders", "Contoso market 2025",
                     "Fabrikam", "Tailspin Toys", "Wingtip"]))
    # 3. a kept personal column: accented, hyphenated, CJK, Cyrillic, two-letter names
    names = ["José García", "Zoë Müller", "Jean-Pierre Dubois", "Siobhán O'Brien", "山田太郎", "李伟", "Wei Li",
             "Ng Kok Leong", "Анна Смирнова", "María de la Cruz"]
    rows = [[MONTHS[i % 12], names[i % len(names)], str(10 + i)] for i in range(300)]
    out.append(Case("staff.csv", _csv(["date", "staff_name", "sales"], rows).encode(),
                    [_col("date", "date", "date"), _col("staff_name", "segment", "category"),
                     _col("sales", "target", "flow_amount")], {"staff_name": "keep"},
                    ["Garcia", "Jose Garcia sales 2025", "Zoe Muller", "Muller", "Jean Pierre", "Dubois", "O'Brien",
                     "OBrien", "Obrien", "Brien", "Siobhan", "山田太郎", "山田太郎の売上 2025", "山田 太郎", "山田",
                     "李伟", "李伟 销售", "Li", "Wei", "Ng", "Kok Leong", "Анна Смирнова", "Смирнова", "Cruz",
                     "de la Cruz", "Maria de la Cruz", "María Cruz"] + names))
    # 4. a kept email column
    emails = ["j.smith@kettlebyfoods.com", "buyer@acmeholdings.co.uk", "zz_top99@gmail.com"]
    rows = [[MONTHS[i % 12], e, str(i)] for i, e in enumerate(emails * 50)]
    out.append(Case("emails.csv", _csv(["date", "email", "spend"], rows).encode(),
                    [_col("date", "date", "date"), _col("spend", "target", "flow_amount")], {"email": "keep"},
                    ["j smith", "smith", "buyer", "kettlebyfoods", "kettlebyfoods.com", "acmeholdings co uk",
                     "acmeholdings", "zz top", "zz_top", "gmail"] + emails))
    # 5. codes with letters
    rows = [[MONTHS[i % 12], "ACC-%s" % "".join(rng.choice("QXZJKW") for _ in range(4)),
             "SKU-ALPHA-" + rng.choice(["BRAVO", "KILO", "ZULU"]), str(i)] for i in range(200)]
    out.append(Case("codes.csv", _csv(["date", "account_code", "sku", "qty"], rows).encode(),
                    [_col("date", "date", "date"), _col("sku", "key", "code"), _col("qty", "target", "count")], {},
                    [rows[0][1], rows[0][1].split("-")[1], "SKU-ALPHA-BRAVO", "alpha bravo", "bravo", "ALPHA"]))
    # 6. brands that are ordinary words
    rows = []
    for i in range(3000):
        rows.append([MONTHS[i % 12], rng.choice(["Apple", "Target", "Nintendo", "Switch", "Trust"]), str(i % 5),
                     rng.choice(["I trust this seller", "great switch for the price", "apple juice spilled", "fine"])])
    out.append(Case("brands.csv", _csv(["date", "brand", "rating", "review_text"], rows).encode(),
                    [_col("date", "date", "date"), _col("brand", "entity", "entity"), _col("rating", "target", "rating"),
                     _col("review_text", "metadata", "free_text")], {},
                    ["Apple market share 2025", "Target retail 2025", "Nintendo console sales 2025", "Switch sales 2025",
                     "Trust in retail 2025", "Nintendo"]))
    rng = random.Random(2)
    # 7. a client list under "account", typed a segment (the review: every name went out)
    cl = ["Acme Holdings Ltd", "Kettleby Foods Inc", "Brightwater Dental Group", "Harlow & Finch LLP"] + \
        ["Client %s" % c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    rows = [[MONTHS[i % 12], cl[i % len(cl)], str(100 + i)] for i in range(3000)]
    out.append(Case("accounts_list.csv", _csv(["date", "account", "revenue"], rows).encode(),
                    [_col("date", "date", "date"), _col("account", "segment", "category"),
                     _col("revenue", "target", "flow_amount")], {},
                    ["Acme Holdings market share 2025", "Kettleby Foods outlook 2025", "Brightwater Dental revenue 2025",
                     "Kettleby", "Harlow Finch", "Dental Group Canada 2025", "Harlow & Finch LLP", "Client Q"]))
    # 8. people under a heading that is not a person word, typed a segment
    st = ["Jürgen Müller", "Işık Yılmaz", "Jørgen Løkke", "Paweł Łukaszewski", "Þóra Guðmundsdóttir", "Nguyễn Văn An"]
    rows = [[MONTHS[i % 12], st[i % len(st)], str(40 + i % 90)] for i in range(1200)]
    folded = ["Jurgen Muller", "Juergen Mueller", "Isik Yilmaz", "Jorgen Lokke", "Pawel Lukaszewski", "Łukaszewski",
              "Thora Gudmundsdottir", "Nguyen Van An", "Nguyen"]
    out.append(Case("stylists.csv", _csv(["date", "Stylist", "ticket"], rows).encode(),
                    [_col("date", "date", "date"), _col("Stylist", "segment", "category"),
                     _col("ticket", "target", "flow_amount")], {}, folded + st))
    out.append(Case("stylists_kept.csv", _csv(["date", "stylist_name", "ticket"], rows).encode(),
                    [_col("date", "date", "date"), _col("stylist_name", "segment", "category"),
                     _col("ticket", "target", "flow_amount")], {"stylist_name": "keep"}, folded + st))
    # 9. a person named only in kept free-text notes
    rows = [[MONTHS[i % 12], str(i), rng.choice(["Spoke with Zoe Muller about the invoice",
                                                 "Zoe Muller asked for a refund again today", "no answer"])]
            for i in range(500)]
    out.append(Case("notes_kept.csv", _csv(["date", "amount", "notes"], rows).encode(),
                    [_col("date", "date", "date"), _col("amount", "target", "flow_amount"),
                     _col("notes", "metadata", "free_text")], {"notes": "keep"},
                    ["Zoe Muller", "Zoe Muller refund", "Zoe Muller asked for", "Zoe Muller asked for a refund"]))
    # 10. a cp1252 file (the engine reads cp1252) with a kept person column
    nm = ["Šimon Novák", "Žofia Králová", "Œdipe Durand", "O’Brien Kelly"]
    text = _csv(["date", "customer_name", "amount"], [[MONTHS[i % 12], nm[i % 4], str(i)] for i in range(200)])
    out.append(Case("cp1252.csv", text.encode("cp1252"),
                    [_col("date", "date", "date"), _col("amount", "target", "flow_amount")], {"customer_name": "keep"},
                    ["Simon Novak", "Novak", "Šimon Novák", "Zofia Kralova", "Kralova", "Oedipe Durand", "Durand",
                     "Kelly", "O'Brien Kelly"] + nm))
    # 11. a "|" file with a kept person column
    text = _csv(["date", "region", "customer_name", "amount"],
                [[MONTHS[i % 12], "West", ["Zoe Muller", "José García"][i % 2], str(i)] for i in range(200)], sep="|")
    out.append(Case("pipe.csv", text.encode(), [_col("date", "date", "date"), _col("amount", "target", "flow_amount")],
                    {"customer_name": "keep"}, ["Zoe Muller", "Garcia", "Muller", "José García", "West"]))
    # 12. the no-plan file of the review (accounts, staff kept, a region column that holds list terms)
    rng = random.Random(5)
    accounts = ["Kettleby Foods Inc", "Brightwater Dental Group", "Harlow Finch Partners", "Acme Holdings Ltd",
                "Northwind Traders"]
    staff = ["Zoe Muller", "José García", "Jørgen Løkke"]
    rows = []
    for m in range(24):
        for a_i, a in enumerate(accounts):
            for s in staff:
                y, mo = 2024 + m // 12, m % 12 + 1
                rows.append(["%d-%02d-15" % (y, mo), a, s, rng.choice(["Ontario", "Quebec"]),
                             str(int(1000 * (a_i + 1) * (1 + 0.03 * m) + rng.randint(-80, 80)))])
    out.append(Case("accounts_regions.csv", _csv(["date", "account", "staff_name", "region", "revenue"], rows).encode(),
                    [_col("date", "date", "date"), _col("account", "entity", "entity"),
                     _col("staff_name", "segment", "category"), _col("region", "segment", "geography"),
                     _col("revenue", "target", "flow_amount")], {"staff_name": "keep"},
                    accounts + staff + ["Kettleby", "Muller", "García", "Garcia",
                                        "José García sales performance 2025", "Kettleby Foods Inc market share 2025",
                                        "Northwind Traders Ontario 2025", "zoe.muller@kettleby.com"]))
    return out


def file_values(data: bytes):
    """Every distinct cell of a file that is not a number, as text (UTF-8, else cp1252; comma, else "|")."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    sep = "|" if text.split("\n", 1)[0].count("|") > text.split("\n", 1)[0].count(",") else ","
    vals = set()
    for row in list(csv.reader(io.StringIO(text), delimiter=sep))[1:]:
        for v in row:
            v = v.strip()
            if v and not v.replace(".", "", 1).replace("-", "").isdigit():
                vals.add(v)
    return sorted(vals)
