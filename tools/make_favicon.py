#!/usr/bin/env python3
"""The site's favicon, written from one shape list so the SVG and the PNG never drift apart.

    python tools/make_favicon.py            # writes favicon.svg and favicon-32.png at the site root
    python tools/make_favicon.py --check    # exit 1 if either file differs from what this writes

A rounded square in the site's accent (--accent, #0b7366) holding three rising bars in the page
background colour (--bg, #f7f6f3): a small ledger chart. The pages link both (build.py and
tools/sanitize_agent_demo.py): browsers that read SVG icons take favicon.svg, the rest the PNG.
Needs Pillow for the PNG (drawn at 8x and scaled down, so the edges are smooth); stdlib otherwise.
"""
import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
ACCENT, PAPER = "#0b7366", "#f7f6f3"
SIZE = 32
# (x, y, w, h, radius) on a 32 x 32 grid: the tile, then the bars
TILE = (0, 0, 32, 32, 7)
BARS = [(7, 17, 5, 8, 1.2), (13.5, 12, 5, 13, 1.2), (20, 7, 5, 18, 1.2)]

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
       '<title>NorthLedger Insights</title>'
       '<rect x="{0}" y="{1}" width="{2}" height="{3}" rx="{4}" fill="%s"/>' % ACCENT).format(*TILE) + \
      "".join('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s"/>' % (b + (PAPER,)) for b in BARS) + "</svg>\n"


def png_bytes():
    from PIL import Image, ImageDraw
    k = 8
    im = Image.new("RGBA", (SIZE * k, SIZE * k), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    for (x, y, w, h, r), col in [(TILE, ACCENT)] + [(b, PAPER) for b in BARS]:
        dr.rounded_rectangle([x * k, y * k, (x + w) * k - 1, (y + h) * k - 1], radius=r * k, fill=col)
    out = io.BytesIO()
    im.resize((SIZE, SIZE), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    want = {"favicon.svg": SVG.encode("utf-8"), "favicon-32.png": png_bytes()}
    bad = []
    for name, data in want.items():
        path = os.path.join(SITE, name)
        if a.check:
            if not os.path.exists(path) or open(path, "rb").read() != data:
                bad.append(name)
        else:
            with open(path, "wb") as fh:
                fh.write(data)
            print("wrote %s (%d bytes)" % (name, len(data)))
    if bad:
        print("favicon: %s differ from tools/make_favicon.py (run it)" % ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
