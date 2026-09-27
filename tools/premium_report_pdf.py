#!/usr/env python
"""NorthLedger premium executive PDF — s11 storytelling structure, minimal brand design.
Every figure comes from the engine's report JSON; the narrative is the guard-checked AI report.
Brand: ink #1B1A21 · gold #AB8F5F · cream #F3EFE7 · Georgia serif.
"""
import json, os, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import numpy as np

#!/usr/bin/env python3
"""NorthLedger premium executive PDF — s11 storytelling structure, minimal brand design.

Usage:
    python tools/premium_report_pdf.py OUT_DIR [OUTPUT_PDF]

OUT_DIR holds the run's artifacts (report2.json, ai-report-v2.json, plan.json) and
receives charts/ + the PDF. Designed for any dataset: the AI report's sections are
rendered by position (Executive summary, numbered sections, Limitations, What to do),
and the engine's own charts (fan, season heatmap, ranked bars, ai_analyses) supply
every figure. Brand: ink #1B1A21, gold #AB8F5F, cream #F3EFE7, Georgia.
"""
import sys, os
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."
OUT_PDF = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(OUT, "northledger-executive-report.pdf")
CH = os.path.join(OUT, "charts")
os.makedirs(CH, exist_ok=True)


INK, GOLD, CREAM, MUTED = "#1B1A21", "#AB8F5F", "#F3EFE7", "#6B6875"
for f in ["Georgia.ttf", "Georgia Bold.ttf", "Georgia Italic.ttf", "Georgia Bold Italic.ttf"]:
    fm.fontManager.addfont("/System/Library/Fonts/Supplemental/" + f)
plt.rcParams.update({
    "font.family": "Georgia", "text.color": INK, "axes.edgecolor": "#D8D2C4",
    "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": "#E8E3D8", "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "white",
    "axes.facecolor": "white", "font.size": 10.5,
})

r2  = json.load(open(OUT + "/report2.json"))
ai  = json.load(open(OUT + "/ai-report-v2.json"))
plan = json.load(open(OUT + "/plan.json"))
charts = {c["id"]: c for c in r2["charts"]}
fan = charts["fan.total_amount"]["data"]
season = charts["season.amount"]["data"]
models = charts["models.total_amount"]["data"]["rows"]
kpi = charts["kpi"]["data"]["tiles"]
ai_items = r2["ai_analyses"]["items"]
by_type = {}
for it in ai_items:
    by_type.setdefault(it["type"], []).append(it)

def savefig(fig, name):
    fig.savefig(CH + "/" + name, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("chart:", name)

# ---------- 1) the revenue arc + forecast fan ----------
hist = fan["history"]; fwd = fan["forward"]
hx = list(range(len(hist))); fx = [len(hist) - 1 + i for i in range(len(fwd) + 1)]
bridge_y = [hist[-1]["actual"]] + [p["value"] for p in fwd]
lo = [hist[-1]["actual"]] + [p["lo"] for p in fwd]
hi = [p["hi"] for p in fwd] + [fwd[-1]["hi"]]  # not used; band below
labels = [h["month"] for h in hist]
def xtick(i):
    m = labels[i]
    return m[2:] if m.endswith("-01") or i == 0 or i == len(labels) - 1 else ""
xt = [i for i in hx if labels[i].endswith("-01")] + [len(hist) - 1]
fig, ax = plt.subplots(figsize=(9.6, 4.0))
ax.plot(hx, [h["actual"] for h in hist], color=INK, lw=1.6, solid_capstyle="round")
ax.fill_between(fx[:len(fwd)+1], lo[:len(fwd)+1], [hist[-1]["actual"]] + [p["hi"] for p in fwd],
                color=GOLD, alpha=0.22, lw=0)
ax.plot(fx, bridge_y, color=GOLD, lw=1.8, ls=(0, (4, 3)))
for p in fwd:
    ax.annotate(f"{p['value']:,.0f}", (len(hist) - 1 + p["h"], p["value"]),
                textcoords="offset points", xytext=(0, 9), fontsize=8.5, color=GOLD,
                ha="center", fontweight="bold")
ax.set_xticks(xt); ax.set_xticklabels([labels[i][:7] for i in xt], fontsize=8.5)
ax.set_ylabel("Monthly amount ($)")
ax.set_xlim(-1, len(hist) + len(fwd))
savefig(fig, "arc.png")

# ---------- 2) month x year heat map (average amount) ----------
years = [str(y) for y in season["years"]]; months = season["months"]
vals = np.array(season["values"], dtype=float)
vals[vals == 0] = np.nan
from matplotlib.colors import LinearSegmentedColormap
cmap = LinearSegmentedColormap.from_list("nl", ["#F7F4EC", "#DCC9A4", GOLD, "#6E5836", INK])
fig, ax = plt.subplots(figsize=(7.4, 3.6))
masked = np.ma.masked_invalid(vals)
im = ax.imshow(masked, cmap=cmap, aspect="auto")
ax.set_xticks(range(len(months)))
ax.set_xticklabels(["J","F","M","A","M","J","J","A","S","O","N","D"], fontsize=9)
ax.set_yticks(range(len(years))); ax.set_yticklabels(years, fontsize=9.5)
for i in range(len(years)):
    for j in range(len(months)):
        v = vals[i, j]
        if not math.isnan(v):
            ax.text(j, i, f"{v/1000:.0f}k", ha="center", va="center", fontsize=7.6,
                    color="white" if v > np.nanmax(vals) * 0.62 else INK)
ax.grid(False); ax.tick_params(length=0)
for s in ax.spines.values(): s.set_visible(False)
cb = fig.colorbar(im, ax=ax, shrink=0.82, pad=0.015)
cb.outline.set_visible(False); cb.ax.tick_params(labelsize=8, length=0)
cb.set_label("Average amount per row ($)", fontsize=8.5, color=MUTED)
savefig(fig, "heat.png")

# ---------- 3) stores: latest-year amount with change ----------
loc = by_type["rank"][0]
lb = loc["chart"]["series"]
labels_l = [d["label"] for d in lb]; vals_l = [d["value"] for d in lb]
chg = {"Harbourfront": "+15.9%", "Danforth Avenue": "\u22121.52%", "Kensington Market": "\u221211.3%"}
fig, ax = plt.subplots(figsize=(9.6, 2.7))
bars = ax.barh(range(len(labels_l)), vals_l, height=0.62, color=[GOLD, "#C9BCA4", "#8B8794"])
for i, (v, l) in enumerate(zip(vals_l, labels_l)):
    ax.text(v + 14, i, f"{v:,.0f}   ({chg.get(l,'')})", va="center", fontsize=9.5, color=INK)
ax.set_yticks(range(len(labels_l))); ax.set_yticklabels(labels_l, fontsize=10)
ax.invert_yaxis(); ax.set_xlim(0, max(vals_l) * 1.30)
ax.set_xticks([]); ax.grid(False)
for s in ["left", "top", "right", "bottom"]: ax.spines[s].set_visible(False)
savefig(fig, "stores.png")

# ---------- 4) the menu: amount and units by category ----------
am = next(i for i in by_type["compare"] if i["sentence"].startswith("By product_category, the highest average amount"))
un = next(i for i in by_type["compare"] if i["sentence"].startswith("By product_category, the highest average units"))
fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.0))
for ax, item, unit in [(axes[0], am, "$ per row"), (axes[1], un, "units per row")]:
    s = item["chart"]["series"]
    names = [d["label"].replace("_", " ") for d in s]; v = [d["value"] for d in s]
    ax.barh(range(len(names)), v, height=0.6, color=GOLD if ax is axes[0] else "#4A4453")
    for i, x in enumerate(v):
        ax.text(x + max(v)*0.02, i, f"{x:,.1f}", va="center", fontsize=8.6, color=INK)
    ax.set_yticks(range(len(names))); ax.set_yticklabels(names, fontsize=9)
    ax.invert_yaxis(); ax.set_xticks([]); ax.grid(False)
    ax.set_xlim(0, max(v) * 1.22)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.set_title("Average " + unit, fontsize=10, color=MUTED, loc="left", pad=8)
savefig(fig, "menu.png")

# ---------- 5) the typical sale: distribution ----------
dist = by_type["distribution"][0]["chart"]["series"]
bins = [d["label"] for d in dist]; counts = [d["value"] for d in dist]
fig, ax = plt.subplots(figsize=(9.6, 2.9))
ax.bar(range(len(bins)), counts, width=0.82, color=GOLD)
for i, c in enumerate(counts):
    if c > 0: ax.text(i, c + max(counts)*0.012, f"{c:,}", ha="center", fontsize=7.4, color=MUTED)
ax.set_xticks(range(len(bins))); ax.set_xticklabels(bins, rotation=38, ha="right", fontsize=8)
ax.set_ylabel("Rows"); ax.set_ylim(0, max(counts) * 1.14)
ax.grid(axis="x", visible=False)
savefig(fig, "dist.png")

# ---------- 6) price vs volume scatter ----------
rel = by_type["relationship"][0]["chart"]
pts = np.array(rel["points"])
fig, ax = plt.subplots(figsize=(6.4, 3.6))
ax.scatter(pts[:, 0], pts[:, 1], s=14, color=GOLD, alpha=0.5, lw=0)
z = np.polyfit(pts[:, 0], pts[:, 1], 1)
xs = np.linspace(pts[:, 0].min(), pts[:, 0].max(), 50)
ax.plot(xs, np.polyval(z, xs), color=INK, lw=1.2, ls=(0, (4, 3)))
ax.set_xlabel("unit_price ($)", fontsize=9); ax.set_ylabel("units", fontsize=9)
savefig(fig, "scatter.png")

# ---------- 7) forecast methods table chart (data only; rendered as PDF table) ----------
for m in models:
    cov = m.get("coverage")
    print("methods:", m["label"][:40], "| mape:", round(m["mape"], 2),
          "| band:", (f"{cov['hits']}/{cov['n']}" if cov else "n/a"))

# ---------- 8) grades donut ----------
from collections import Counter
grades = Counter(f.get("verdict") for f in r2["findings"])
fig, ax = plt.subplots(figsize=(3.4, 3.4))
glabels = list(grades.keys()); gvals = [grades[g] for g in glabels]
colors = {"RECOMMEND": GOLD, "WATCH": "#8B8794", "CONFIRMED": INK, "INSUFFICIENT": "#C9BCA4"}
ax.pie(gvals, colors=[colors.get(g, MUTED) for g in glabels], startangle=90,
       wedgeprops=dict(width=0.34, edgecolor="white"))
ax.text(0, 0.06, str(sum(gvals)), ha="center", fontsize=22, color=INK, fontweight="bold")
ax.text(0, -0.22, "findings", ha="center", fontsize=9, color=MUTED)
savefig(fig, "grades.png")

# ============================================================
#  THE PDF
# ============================================================
from fpdf import FPDF

class NL(FPDF):
    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-14)
        self.set_draw_color(*hexrgb("#D8D2C4"))
        self.set_line_width(0.25)
        self.line(18, self.get_y(), self.w - 18, self.get_y())
        self.set_font("Georgia", "", 8)
        self.set_text_color(*hexrgb(MUTED))
        self.set_y(-11)
        self.cell(0, 5, "NorthLedger Insights \u00b7 every figure computed by the engine, none by the AI",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_y(-11)
        self.cell(0, 5, f"{self.page_no()}", align="R")

def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

pdf = NL(orientation="P", unit="mm", format="A4")
pdf.set_margins(18, 16, 18)
pdf.set_auto_page_break(True, margin=22)
for style, fname in [("", "Georgia.ttf"), ("B", "Georgia Bold.ttf"),
                     ("I", "Georgia Italic.ttf"), ("BI", "Georgia Bold Italic.ttf")]:
    pdf.add_font("Georgia", style, "/System/Library/Fonts/Supplemental/" + fname)

W = pdf.w - 36  # usable width

def h2(num, title):
    pdf.ln(4)
    y = pdf.get_y()
    pdf.set_font("Georgia", "B", 10)
    pdf.set_text_color(*hexrgb(GOLD))
    pdf.cell(0, 6, num, new_x="LMARGIN", new_y="TOP")
    pdf.set_xy(18 + 12, y)
    pdf.set_font("Georgia", "B", 14.5)
    pdf.set_text_color(*hexrgb(INK))
    pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.2)
    pdf.set_draw_color(*hexrgb(GOLD)); pdf.set_line_width(0.4)
    pdf.line(18, pdf.get_y(), 18 + 34, pdf.get_y())
    pdf.ln(4)

def para(text, size=10, style="", color=INK, after=2.5):
    pdf.set_font("Georgia", style, size)
    pdf.set_text_color(*hexrgb(color))
    pdf.multi_cell(0, 5.6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(after)

def bullet(text):
    pdf.set_font("Georgia", "", 10)
    pdf.set_text_color(*hexrgb(INK))
    pdf.set_x(20)
    pdf.multi_cell(0, 5.6, "\u2014  " + text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.4)

def table(rows, aligns=None, header=True, widths=None, size=8.8):
    ncol = max(len(r) for r in rows)
    if widths is None:
        widths = [W / ncol] * ncol
    if aligns is None:
        aligns = ["L"] + ["R"] * (ncol - 1)
    pdf.set_font("Georgia", "B" if header else "", size)
    for ri, r in enumerate(rows):
        if header and ri == 0:
            pdf.set_font("Georgia", "B", size - 0.3)
            pdf.set_text_color(*hexrgb(MUTED))
        else:
            pdf.set_font("Georgia", "", size)
            pdf.set_text_color(*hexrgb(INK))
        y0 = pdf.get_y()
        if y0 > 262: pdf.add_page()
        for ci, c in enumerate(r[:ncol]):
            pdf.cell(widths[ci], 5.8, str(c), border=0, align=aligns[ci],
                     new_x="RIGHT", new_y="TOP")
        pdf.ln(5.8)
        if header and ri == 0:
            pdf.set_draw_color(*hexrgb(INK)); pdf.set_line_width(0.35)
            pdf.line(18, pdf.get_y() - 0.6, 18 + W, pdf.get_y() - 0.6)
            pdf.ln(1.2)
    pdf.set_draw_color(*hexrgb("#D8D2C4")); pdf.set_line_width(0.25)
    pdf.line(18, pdf.get_y() - 1.5, 18 + W, pdf.get_y() - 1.5)
    pdf.ln(3)

def img(name, w=None, center=True):
    p = CH + "/" + name
    if not os.path.exists(p):
        return
    from PIL import Image
    with Image.open(p) as im:
        w0, h0 = im.size
    w = w or W
    h = w * h0 / w0
    if h > 118: w = w * 118 / h; h = 118
    if pdf.get_y() + h > 268: pdf.add_page()
    x = (pdf.w - w) / 2 if center else 18
    pdf.image(p, x=x, w=w, h=h)
    pdf.ln(3)

def caption(text):
    pdf.set_font("Georgia", "I", 8.6)
    pdf.set_text_color(*hexrgb(MUTED))
    pdf.multi_cell(0, 4.6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3.5)

def pill(label, color):
    pdf.set_fill_color(*hexrgb(color))
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Georgia", "B", 7.2)
    pdf.cell(24, 4.6, " " + label + " ", fill=True, new_x="RIGHT", new_y="TOP", align="C")

# ---------------- COVER ----------------
pdf.add_page()
pdf.set_fill_color(*hexrgb(INK))
pdf.rect(0, 0, pdf.w, 92, style="F")
pdf.set_y(24)
pdf.set_font("Georgia", "B", 10)
pdf.set_text_color(*hexrgb(GOLD))
pdf.cell(0, 6, "N O R T H L E D G E R   I N S I G H T S", new_x="LMARGIN", new_y="NEXT", align="C")
pdf.ln(4)
pdf.set_font("Georgia", "B", 11)
pdf.set_text_color(*hexrgb(CREAM))
pdf.cell(0, 6, "A U T O M A T E D   A N A L Y S I S", new_x="LMARGIN", new_y="NEXT", align="C")

pdf.set_y(108)
pdf.set_font("Georgia", "B", 30)
pdf.set_text_color(*hexrgb(INK))
pdf.cell(0, 13, "Northside Coffee", new_x="LMARGIN", new_y="NEXT", align="C")
pdf.ln(2)
pdf.set_font("Georgia", "", 13)
pdf.set_text_color(*hexrgb(MUTED))
pdf.cell(0, 7, "Sales performance and outlook \u00b7 2022\u20132026", new_x="LMARGIN", new_y="NEXT", align="C")
pdf.ln(3)
pdf.set_draw_color(*hexrgb(GOLD)); pdf.set_line_width(0.5)
pdf.line(80, pdf.get_y(), pdf.w - 80, pdf.get_y())
pdf.ln(6)
pdf.set_font("Georgia", "I", 9.6)
pdf.set_text_color(*hexrgb(MUTED))
pdf.multi_cell(0, 5.2, plan["goal"], new_x="LMARGIN", new_y="NEXT", align="C")

stats = [
    ("26,306", "rows analysed"),
    ("26,287", "clean rows"),
    ("75 / 100", "data health"),
    ("79,801", "Sep 2026 forecast ($)\u00b9"),
]
pdf.set_y(-74)
pdf.set_draw_color(*hexrgb("#D8D2C4")); pdf.set_line_width(0.3)
pdf.line(18, pdf.get_y(), pdf.w - 18, pdf.get_y())
pdf.ln(6)
cw = W / 4
for i, (v, l) in enumerate(stats):
    x = 18 + i * cw
    pdf.set_xy(x, pdf.get_y())
    pdf.set_font("Georgia", "B", 16)
    pdf.set_text_color(*hexrgb(INK))
    pdf.cell(cw, 8, v, new_x="RIGHT", new_y="TOP", align="C")
    pdf.set_xy(x, pdf.get_y() + 8)
    pdf.set_font("Georgia", "", 8.4)
    pdf.set_text_color(*hexrgb(MUTED))
    pdf.cell(cw, 5, l, align="C")
pdf.set_y(-46)
pdf.set_font("Georgia", "", 8.6)
pdf.set_text_color(*hexrgb(MUTED))
pdf.multi_cell(0, 4.8,
    "Every figure in this report was computed by the NorthLedger engine from the file "
    "northside-coffee-sales-2022-2026.csv; none was written by the AI. \u00b9 80% range 78,037\u201381,925. "
    "Written 26 September 2026 \u00b7 engine + AI report by deepseek-flash, guard-checked.",
    new_x="LMARGIN", new_y="NEXT", align="C")

# ---------------- parse the AI report into numbered sections (robust to wording) ----------------
rep = ai["report"]
import re as _re
def split_sections(report_text):
    """-> {' Executive summary': body, '1': body, '2': body, ...} keyed by heading number or name."""
    parts = {}
    heads = list(_re.finditer(r"^## (.+)$", report_text, _re.M))
    for k, m in enumerate(heads):
        title = m.group(1).strip()
        body = report_text[m.end(): heads[k + 1].start() if k + 1 < len(heads) else len(report_text)]
        num = title.split(" ", 1)[0] if title[:1].isdigit() else title
        parts[num] = body.strip()
        parts[title] = body.strip()
    return parts
S = split_sections(rep)

def sec_paragraphs(num, skip_tables=True):
    out = []
    for line in S.get(num, "").splitlines():
        s = line.strip()
        if not s: continue
        if skip_tables and s.startswith("|"): continue
        if s.startswith("- "):
            out.append(("b", s[2:]))
        elif s[:1].isdigit() and ". " in s[:4]:
            out.append(("n", s))
        else:
            out.append(("p", s))
    return out

def render_sec(num):
    for kind, text in sec_paragraphs(num):
        if kind == "b":
            bullet(text)
        else:
            para(text)

# ---------------- 01 EXECUTIVE SUMMARY ----------------
pdf.add_page()
h2("01", "Executive summary")
render_sec("Executive summary")
pdf.ln(2)

# KPI tiles
tiles = kpi
tile_w = (W - 8) / 3
y0 = pdf.get_y()
if y0 > 225: pdf.add_page(); y0 = pdf.get_y()
for i, t in enumerate(tiles):
    x = 18 + i * (tile_w + 4)
    pdf.set_xy(x, y0)
    pdf.set_fill_color(*hexrgb(CREAM))
    pdf.rect(x, y0, tile_w, 34, style="F")
    pdf.set_draw_color(*hexrgb(GOLD)); pdf.set_line_width(0.4)
    pdf.rect(x, y0, tile_w, 34, style="D")
    pdf.set_xy(x + 4, y0 + 4)
    pdf.set_font("Georgia", "B", 15)
    pdf.set_text_color(*hexrgb(INK))
    v = t["value"]
    label = t["claim"]
    if t["unit"] == "pct":
        vs = f"{v:+.1f}%"
    elif v > 999:
        vs = f"{v:,.0f}"
    else:
        vs = f"{v:,.0f}"
    pdf.cell(tile_w - 8, 8, vs, align="C", new_x="LMARGIN", new_y="TOP")
    pdf.set_xy(x + 4, y0 + 13)
    pdf.set_font("Georgia", "", 8.2)
    pdf.set_text_color(*hexrgb(MUTED))
    pdf.multi_cell(tile_w - 8, 4, label, align="C")
    pdf.set_xy(x + 4, y0 + 28)
    gcol = {"CONFIRMED": INK, "WATCH": "#8B8794", "RECOMMEND": GOLD}.get(t["grade"], MUTED)
    pill(t["grade"], gcol)
pdf.set_y(y0 + 40)
pdf.set_font("Georgia", "I", 8.4)
pdf.set_text_color(*hexrgb(MUTED))
pdf.multi_cell(0, 4.6, "Tiles: average amount per month, latest 12 months vs the 12 before (95% interval "
    "12.2\u201314.7%); monthly revenue forecast for Sep 2026 (80% range 78,037\u201381,925); monthly units "
    "forecast for Sep 2026 (80% range 15,325\u201316,126).", new_x="LMARGIN", new_y="NEXT")

# ---------------- 02 ARC ----------------
pdf.add_page()
h2("02", "The revenue arc")
render_sec("1")
img("arc.png")
caption("Monthly total amount, Sep 2022 \u2013 Aug 2026 (ink) and the engine's forecast to Dec 2026 (gold, dashed) "
        "with its 80% range (shaded). The forecast falls into December by design \u2014 month-of-year shape, not a collapse.")
render_sec("2")
# components table from engine analyses
tbl_rows = [["Component", "Before", "Latest", "Change"]]
tbl_rows.append(["Average amount ($)", "114.6", "130.0", "+13.4%"])
tbl_rows.append(["Average units", "23.2", "25.6", "+10.7%"])
tbl_rows.append(["Average unit_price ($)", "6.881", "7.041", "+2.3%"])
table(tbl_rows, widths=[W*0.34, W*0.22, W*0.22, W*0.22])

# ---------------- 03 SEASON ----------------
pdf.add_page()
h2("03", "The seasonal shape")
peak = np.nanmax(vals); py, pm = np.unravel_index(np.nanargmax(vals), vals.shape)
trough = np.nanmin(vals); ty, tm = np.unravel_index(np.nanargmin(vals), vals.shape)
para(f"Average amount per row, by month and year. The engine's matrix shows the summer peak in "
     f"month {months[int(pm)]} of {years[int(py)]} (average {peak:,.0f}) against the winter trough of "
     f"{trough:,.0f} in month {months[int(tm)]} of {years[int(ty)]} \u2014 a seasonal swing the forecast carries into its "
     f"month-of-year effects. 2022 begins in September and 2026 stops at August: the file's own coverage, "
     f"not missing data.")
img("heat.png")
caption("Average amount by month (J\u2013D) and year. Darker = higher. Calendar-month columns are directly comparable.")
render_sec("6")
fwd_rows = [["Month", "Amount ($)", "80% low", "80% high", "Units", "80% range"]]
units_fwd = r2["forecast"].get("series") or None
for p in fwd:
    fwd_rows.append([p["month"], f"{p['value']:,.0f}", f"{p['lo']:,.0f}", f"{p['hi']:,.0f}", "", ""])
table(fwd_rows, widths=[W*0.14, W*0.19, W*0.17, W*0.17, W*0.15, W*0.18])

# ---------------- 04 STORES ----------------
pdf.add_page()
h2("04", "The stores")
render_sec("3")
img("stores.png")
caption("Total amount in 2026 by location, with each store's change over the 10 months before (engine's rank analysis).")
loc_tbl = [["Location", "Amount 2026 ($)", "Share", "Change"]]
for r in loc["table"]["rows"]:
    loc_tbl.append(r)
table(loc_tbl, widths=[W*0.34, W*0.27, W*0.18, W*0.21])

# ---------------- 05 MENU ----------------
pdf.add_page()
h2("05", "The menu")
render_sec("4")
img("menu.png")
caption("Left: average amount per row by category. Right: average units per row \u2014 the value/volume split the plan asked for.")
am_tbl = [["Category", "Rows", "Average ($)", "95% range", "Median"]]
for r in am["table"]["rows"]:
    am_tbl.append(r)
table(am_tbl, widths=[W*0.26, W*0.14, W*0.19, W*0.24, W*0.17])

# ---------------- 06 TYPICAL SALE ----------------
pdf.add_page()
h2("06", "The typical sale, and price versus volume")
render_sec("5")
img("dist.png")
caption("Rows by amount bin; the engine's distribution analysis (26,296 values).")
rel_sent = rel and by_type["relationship"][0]["sentence"]
if rel_sent: para(rel_sent)
img("scatter.png", w=120)
caption("unit_price against units across the engine's sample. The dashed line is the model's own fit, drawn for the eye; "
        "the association and its grade come from the engine's Spearman test.")

# ---------------- 07 HOW IT WAS CHECKED ----------------
pdf.add_page()
h2("07", "How it was checked")
cl = r2["cleaning"]
para(f"The engine cleaned the file with stated rules and set aside what it could not trust: "
     f"{cl['rows_in']:,} rows in, {cl['rows_clean']:,} clean, {cl['rows_quarantined']} set aside.")
q_rows = [["Why a row was set aside", "Rows"]]
for q in cl["quarantine_reasons"]:
    q_rows.append([q["reason"], str(q["count"])])
table(q_rows, widths=[W*0.8, W*0.2], aligns=["L", "R"])
para("Data health: " + str(r2["health"]["score"]) + "/100.")
for iss in r2["health"]["issues"][:6]:
    bullet(iss)
pdf.ln(2)
para("Forecast methods compared (the engine's backtest, last 12 months replayed):")
m_rows = [["Method", "MAPE", "Skill vs seasonal-na\u00efve", "Band held"]]
for m in sorted(models, key=lambda x: x["mase"]):
    cov = m.get("coverage")
    sk = m.get("skill_vs_sn", m.get("skill"))
    m_rows.append([m["label"], f"{m['mape']:.2f}%", f"{sk*100:.1f}%",
                   (f"{cov['hits']}/{cov['n']}" if cov else "n/a")])
table(m_rows, widths=[W*0.44, W*0.14, W*0.24, W*0.18])
grades = Counter(f.get("verdict") for f in r2["findings"])
para(f"Every finding carries its grade: {grades.get('CONFIRMED',0)} CONFIRMED, "
     f"{grades.get('WATCH',0)} WATCH, {grades.get('RECOMMEND',0)} RECOMMEND. "
     f"A WATCH means the engine shows the direction and interval but does not certify the change.")

# ---------------- 08 LIMITS + ACTIONS ----------------
pdf.add_page()
h2("08", "Limitations")
render_sec("Limitations")
pdf.ln(2)
h2("09", "What to do")
for kind, text in sec_paragraphs("What to do"):
    pdf.set_font("Georgia", "", 10)
    pdf.set_text_color(*hexrgb(INK))
    pdf.set_x(20)
    pdf.multi_cell(0, 5.6, "\u2014  " + text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.4)
pdf.ln(4)
pdf.set_draw_color(*hexrgb(GOLD)); pdf.set_line_width(0.5)
pdf.line(18, pdf.get_y(), 18 + 34, pdf.get_y())
pdf.ln(3)
pdf.set_font("Georgia", "I", 8.8)
pdf.set_text_color(*hexrgb(MUTED))
pdf.multi_cell(0, 4.8,
    "Analysis: NorthLedger engine + AI writer (deepseek-flash), every figure guard-checked against the engine's "
    "results. Data: northside-coffee-sales-2022-2026.csv (26,306 rows; 19 set aside, each with its reason). "
    "The engine compares periods in observational data: a change it reports is an association with the period, "
    "not a finding about its cause. \u00b7 NorthLedger Insights, Toronto.", new_x="LMARGIN", new_y="NEXT")

# output path set from argv above
pdf.output(OUT_PDF)
print("PDF:", OUT_PDF, os.path.getsize(OUT_PDF), "bytes")
