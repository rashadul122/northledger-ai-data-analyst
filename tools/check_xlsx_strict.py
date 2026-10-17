#!/usr/bin/env python3
"""Strict OOXML checks for the workbooks the site writes, for what Excel refuses and LibreOffice forgives: every part has
a content type, every relationship resolves, every XML part parses, schema element order (ECMA-376) in worksheets, the
workbook, styles and charts, inline dropdown lists within 255 characters, sheet names Excel accepts, cell and formula
lengths. Usage: python3 tools/check_xlsx_strict.py FILE.xlsx [...]  (exit 1 on any problem)
"""
import sys, zipfile, posixpath, re
import xml.etree.ElementTree as ET
NS_C='{http://schemas.openxmlformats.org/drawingml/2006/chart}'; NS_S='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
ORDER = {  # ECMA-376 sequences (child local names in required order)
 NS_S+'worksheet': 'sheetPr dimension sheetViews sheetFormatPr cols sheetData sheetCalcPr sheetProtection protectedRanges scenarios autoFilter sortState dataConsolidate customSheetViews mergeCells phoneticPr conditionalFormatting dataValidations hyperlinks printOptions pageMargins pageSetup headerFooter rowBreaks colBreaks customProperties cellWatches ignoredErrors smartTags drawing legacyDrawing legacyDrawingHF drawingHF picture oleObjects controls webPublishItems tableParts extLst',
 NS_S+'workbook': 'fileVersion fileSharing workbookPr workbookProtection bookViews sheets functionGroups externalReferences definedNames calcPr oleSize customWorkbookViews pivotCaches smartTagPr smartTagTypes webPublishing fileRecoveryPr webPublishObjects extLst',
 NS_S+'styleSheet': 'numFmts fonts fills borders cellStyleXfs cellXfs cellStyles dxfs tableStyles colors extLst',
 NS_C+'chartSpace': 'date1904 lang roundedCorners AlternateContent style clrMapOvr pivotSource protection chart spPr txPr externalData printSettings userShapes extLst',
 NS_C+'chart': 'title autoTitleDeleted pivotFmts view3D floor sideWall backWall plotArea legend plotVisOnly dispBlanksAs showDLblsOverMax extLst',
 NS_C+'barChart': 'barDir grouping varyColors ser dLbls gapWidth overlap serLines axId extLst',
 NS_C+'lineChart': 'grouping varyColors ser dLbls dropLines hiLowLines upDownBars marker smooth axId extLst',
 NS_C+'catAx': 'axId scaling delete axPos majorGridlines minorGridlines title numFmt majorTickMark minorTickMark tickLblPos spPr txPr crossAx crosses crossesAt auto lblAlgn lblOffset tickLblSkip tickMarkSkip noMultiLvlLbl extLst',
 NS_C+'valAx': 'axId scaling delete axPos majorGridlines minorGridlines title numFmt majorTickMark minorTickMark tickLblPos spPr txPr crossAx crosses crossesAt crossBetween majorUnit minorUnit dispUnits extLst',
 NS_C+'legend': 'legendPos legendEntry layout overlay spPr txPr extLst',
 NS_C+'title': 'tx layout overlay spPr txPr extLst',
 NS_C+'dLbls': 'dLbl delete numFmt spPr txPr dLblPos showLegendKey showVal showCatName showSerName showPercent showBubbleSize separator showLeaderLines leaderLines extLst',
}
SER = {'barChart': 'idx order tx spPr invertIfNegative pictureOptions dPt dLbls trendline errBars cat val shape extLst',
       'lineChart': 'idx order tx spPr marker dPt dLbls trendline errBars cat val smooth extLst'}
def local(t): return t.split('}')[-1]
def check_order(el, seq, where, errs):
    pos = {n: i for i, n in enumerate(seq.split())}; last = -1
    for ch in el:
        n = local(ch.tag)
        if n not in pos: errs.append('%s: <%s> not allowed in <%s>' % (where, n, local(el.tag))); continue
        if pos[n] < last: errs.append('%s: <%s> out of order in <%s>' % (where, n, local(el.tag)))
        last = max(last, pos[n])
def run(path):
    errs = []; z = zipfile.ZipFile(path); names = set(z.namelist())
    if z.testzip(): errs.append('corrupt zip member')
    ct = ET.fromstring(z.read('[Content_Types].xml'))
    defaults = {e.get('Extension').lower() for e in ct if local(e.tag) == 'Default'}
    overrides = {e.get('PartName').lstrip('/') for e in ct if local(e.tag) == 'Override'}
    for o in overrides:
        if o not in names: errs.append('content type for missing part ' + o)
    trees = {}
    for n in names:
        if n.endswith('/'): continue
        ext = n.rsplit('.', 1)[-1].lower()
        if n not in overrides and ext not in defaults: errs.append('no content type: ' + n)
        if ext in ('xml', 'rels', 'vml'):
            try: trees[n] = ET.fromstring(z.read(n))
            except ET.ParseError as e: errs.append('malformed XML %s: %s' % (n, e))
    for n, t in trees.items():
        if n.endswith('.rels'):
            base = posixpath.dirname(posixpath.dirname(n))
            for r in t:
                if r.get('TargetMode') == 'External': continue
                tgt = r.get('Target'); p = tgt.lstrip('/') if tgt.startswith('/') else posixpath.normpath(posixpath.join(base, tgt))
                if p not in names: errs.append('%s -> missing %s' % (n, p))
        for el in t.iter():
            if el.tag in ORDER: check_order(el, ORDER[el.tag], n, errs)
            if local(el.tag) in SER and el.tag.startswith(NS_C):
                for s in el.findall(NS_C + 'ser'): check_order(s, SER[local(el.tag)], n, errs)
            if el.tag == NS_C + 'plotArea':
                kinds = [local(c.tag) for c in el]
                if kinds and kinds[0] not in ('layout',) and not kinds[0].endswith('Chart'): errs.append(n + ': plotArea starts with ' + kinds[0])
            if el.tag == NS_S + 'dataValidation':
                f1 = el.find(NS_S + 'formula1')
                if f1 is not None and f1.text and f1.text.startswith('"') and len(f1.text) - 2 > 255: errs.append('%s: dropdown list %d chars (>255, Excel refuses)' % (n, len(f1.text) - 2))
            if el.tag == NS_S + 'sheet':
                nm = el.get('name') or ''
                if len(nm) > 31 or re.search(r'[\[\]:*?/\\]', nm) or nm.startswith("'"): errs.append('bad sheet name ' + repr(nm))
            if el.tag == NS_S + 'c':
                v = el.find(NS_S + 'is/' + NS_S + 't')
                if v is not None and v.text and len(v.text) > 32767: errs.append('cell over 32767 chars')
                f = el.find(NS_S + 'f')
                if f is not None and f.text and len(f.text) > 8192: errs.append('formula over 8192 chars')
    sheets = [s.get('name') for s in trees.get('xl/workbook.xml', ET.Element('x')).iter(NS_S + 'sheet')]
    charts = [n for n in names if n.startswith('xl/charts/chart')]
    nf = sum(1 for t in trees.values() for _ in t.iter(NS_S + 'f'))
    nv = sum(1 for t in trees.values() for _ in t.iter(NS_S + 'dataValidation'))
    print('%s: %d sheets %s, %d charts, %d formulas, %d dropdown rules -> %s' % (path.split('/')[-1], len(sheets), sheets, len(charts), nf, nv, 'OK' if not errs else '%d PROBLEMS' % len(errs)))
    for e in errs[:25]: print('   -', e)
    return not errs
ok = all([run(p) for p in sys.argv[1:]]); sys.exit(0 if ok else 1)
