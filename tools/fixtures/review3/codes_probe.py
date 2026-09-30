"""The final review's duplicate probe (29 Sep 2026), kept: does a withheld column keep the file's duplicate
structure? codes_probe.csv is 1,200 orders plus 150 repeats: 60 exact, 30 whose withheld note differs only in
letter case, 30 only in spaces, 30 only in a placeholder (N/A for a blank or a dash). It is run three ways:

  codes off   the engine reads the notes as they are (the adapter's coding switched off): its own truth
  withheld    the default: the notes are landed as codes (engine/nl_browser.py _neutralize_withheld)
  kept        the visitor keeps the notes: the engine reads them as they are

    python tools/fixtures/review3/codes_probe.py            # the adapter in engine/, the engine in ../northledger-core
    python tools/fixtures/review3/codes_probe.py PACKDIR    # an unzipped engine/northledger-browser.zip
    python tools/fixtures/review3/codes_probe.py --variants # also the codings measured and not kept

and prints, for each, the health's exact-duplicate count, the rows the cleaning set aside, and the health's
line on the notes' empty values."""
import os
import re
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))


def counts(rep):
    """(the health's exact duplicate rows, the rows set aside, the health's notes lines)."""
    dup = [x for x in rep["health"]["issues"] if "exact duplicates" in x]
    n = int(re.match(r"([\d,]+) rows", dup[0]).group(1).replace(",", "")) if dup else 0
    return n, rep["cleaning"]["rows_quarantined"], [x for x in rep["health"]["issues"] if x.startswith("notes:")]


def probe(NB):
    data = open(os.path.join(HERE, "codes_probe.csv"), "rb").read()
    out = {}
    real = NB._neutralize_withheld
    try:
        NB._neutralize_withheld = lambda db, t, cols: []
        out["codes off"] = counts(NB.run(data, "codes_probe.csv", "", None, "2026-09-15"))
    finally:
        NB._neutralize_withheld = real
    out["withheld"] = counts(NB.run(data, "codes_probe.csv", "", None, "2026-09-15"))
    out["kept"] = counts(NB.run(data, "codes_probe.csv", "", {"customer": "keep", "notes": "keep"}, "2026-09-15"))
    return out


def _coding(NB, blank):
    """_neutralize_withheld with another rule for which cells go blank (the rest byte-exact codes)."""
    import sqlite3

    def neut(db_path, table, columns):
        codes = []
        qt = '"%s"' % str(table).replace('"', '""')
        con = sqlite3.connect(db_path)
        try:
            for c in columns:
                qc = '"%s"' % str(c).replace('"', '""')
                keys, pairs = {}, []
                for (v,) in con.execute("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL" % (qc, qt, qc)).fetchall():
                    if blank(str(v)):
                        pairs.append((v, None))
                        continue
                    keys.setdefault(str(v), NB._opaque_code(len(keys)))
                    pairs.append((v, keys[str(v)]))
                con.execute("DROP TABLE IF EXISTS temp._nl_codes")
                con.execute("CREATE TEMP TABLE _nl_codes (v PRIMARY KEY, t)")
                con.executemany("INSERT OR IGNORE INTO _nl_codes VALUES (?, ?)", pairs)
                con.execute("UPDATE %s SET %s = (SELECT t FROM _nl_codes WHERE _nl_codes.v = %s.%s) WHERE %s IS NOT NULL"
                            % (qt, qc, qt, qc, qc))
                codes.extend(keys.values())
            con.commit()
        finally:
            con.close()
        return codes
    return neut


def variants(NB):
    """The codings measured for the final review and not kept: a placeholder (N/A, a dash) blank as written, and
    blank once trimmed ("  N/A  " too)."""
    data = open(os.path.join(HERE, "codes_probe.csv"), "rb").read()
    nulls = NB._null_tokens()
    out = {}
    real = NB._neutralize_withheld
    try:
        for k, blank in (("N/A blank", lambda v: v == "" or v.lower() in nulls),
                         ("N/A trimmed blank", lambda v: v.strip().lower() in nulls)):
            NB._neutralize_withheld = _coding(NB, blank)
            out[k] = counts(NB.run(data, "codes_probe.csv", "", None, "2026-09-15"))
    finally:
        NB._neutralize_withheld = real
    return out


def main(argv):
    warnings.simplefilter("ignore")
    site = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
    more = "--variants" in argv
    argv = [a for a in argv if a != "--variants"]
    if len(argv) > 1:
        sys.path[:] = [p for p in sys.path if "northledger-core" not in p and os.path.abspath(p) != site]
        sys.path.insert(0, os.path.abspath(argv[1]))
    else:
        sys.path.insert(0, os.path.join(site, "..", "northledger-core"))
        sys.path.insert(0, os.path.join(site, "engine"))
    import nl_browser as NB
    got = probe(NB)
    if more:
        got.update(variants(NB))
    for k, (dup, aside, notes) in got.items():
        print("%-18s health: %d exact duplicate rows   cleaning set aside: %d   %s" % (k, dup, aside, "; ".join(notes)[:100]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
