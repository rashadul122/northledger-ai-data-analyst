#!/usr/bin/env python3
"""Page-by-page checks of the print PDF that verify.sh writes (Chrome's Save as PDF of index.html).

    python3 tools/check_print.py path/to/export.pdf [path/to/index.html]

A reader who saves the page as PDF should not get a blank first page or pages that are nearly
empty because a section was pushed whole to the next page. Each page is read twice, with the
poppler tools (pdftotext for its words, pdftoppm for how much of its height carries ink):

  * page 1 holds the hero heading, not only the status strip;
  * every page but the last carries text and ink over at least a fifth of its height;
  * with the page's HTML given, no page ends on a chart's caption (the chart itself on the next).

Exit 0 when every page passes, 1 when one fails, 2 when the poppler tools are missing (SKIP).
Nothing here edits the site; the page images go to a temporary folder that is removed.
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERO = "Messy data in"
MIN_INK_SHARE = 0.20          # of the page height, for every page but the last
MIN_CHARS = 60


def pages(pdf):
    out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0


def text_of(pdf, i):
    return subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), "-layout", pdf, "-"],
                          capture_output=True, text=True).stdout


def ink_share(pgm_path):
    """Share of pixel rows that carry anything darker than the page background (binary PGM)."""
    with open(pgm_path, "rb") as f:
        data = f.read()
    parts, pos = [], 0
    while len(parts) < 4:                      # magic, width, height, maxval (comments skipped)
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        parts.append(data[pos:end])
        pos = end
    pos += 1
    w, h = int(parts[1]), int(parts[2])
    px = data[pos:pos + w * h]
    bg = max(set(px[:w]), key=px[:w].count) if w else 255      # the first row's commonest shade
    inked = 0
    for r in range(h):
        row = px[r * w:(r + 1) * w]
        if sum(1 for v in row if abs(v - bg) > 40) > 2:
            inked += 1
    return inked / float(h or 1)


def captions(page_html):
    out = []
    for m in re.finditer(r"<figcaption[^>]*>(.*?)</figcaption>", page_html, re.S):
        t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())
        if len(t) > 12:
            out.append(t[:40])
    return out


def main(argv):
    if len(argv) not in (1, 2):
        print(__doc__)
        return 2
    pdf = argv[0]
    caps = captions(open(argv[1], encoding="utf-8").read()) if len(argv) == 2 else []
    if not all(shutil.which(t) for t in ("pdfinfo", "pdftotext", "pdftoppm")):
        print("PRINT SKIP: poppler tools (pdfinfo, pdftotext, pdftoppm) not found")
        return 2
    n = pages(pdf)
    if not n:
        print("PRINT FAIL: %s has no pages" % pdf)
        return 1
    fails = []
    if HERO not in text_of(pdf, 1):
        fails.append("page 1 does not hold the hero (%r); it starts a blank or strip-only page" % HERO)
    tmp = tempfile.mkdtemp(prefix="nl-print-")
    try:
        subprocess.run(["pdftoppm", "-r", "30", "-gray", pdf, os.path.join(tmp, "p")], check=True)
        imgs = sorted(f for f in os.listdir(tmp) if f.endswith(".pgm"))
        for i, fn in enumerate(imgs, 1):
            share = ink_share(os.path.join(tmp, fn))
            txt = text_of(pdf, i)
            chars = len("".join(txt.split()))
            tail = " ".join(" ".join(l.split()) for l in [x for x in txt.splitlines() if x.strip()][-2:])
            for c in caps:
                if c in tail and i < len(imgs):
                    fails.append("page %d ends on the caption %r; its chart is on the next page" % (i, c))
            last = i == len(imgs)
            if chars < MIN_CHARS and not last:
                fails.append("page %d is nearly blank (%d characters of text)" % (i, chars))
            elif share < MIN_INK_SHARE and not last:
                fails.append("page %d is mostly empty (ink over %.0f%% of its height)" % (i, share * 100))
            print("  page %2d: %5d characters, ink over %3.0f%% of the height" % (i, chars, share * 100))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("PRINT FAIL: " + f)
    print("PRINT: %d pages, %s" % (n, "all pass" if not fails else "%d problem(s)" % len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
