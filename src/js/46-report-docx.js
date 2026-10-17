/* window.NLDocx: a Word (.docx) writer that runs in the browser and under Node (module.exports), no library, nothing
   uploaded. Two layers:

   1. NLDocx.document(doc) -> Uint8Array, the .docx file. A generic writer, meant for reuse (Track D's research write-up,
      Track E's monthly cover note, Track F's quarterly sales report): proper OOXML (document.xml, styles.xml with named
      styles, numbering.xml, settings, a header and a "Page X of Y" footer, image parts with their relationships,
      docProps/core.xml and app.xml) in a stored zip; it opens in Word, LibreOffice and Google Docs.

      NLDocx.document({
        title: 'Document title',                     // docProps/core.xml dc:title and the running header
        subject: '', description: '', keywords: '',   // core.xml (optional)
        created: new Date(),                          // core.xml dcterms:created (optional; deterministic when given)
        style: 'report' | 'business',                 // report: sans, airy (the default); business: serif (Georgia),
                                                      //   dense, tight spacing, a clear heading hierarchy, narrower margins
        paper: 'letter' | 'a4',                       // the page (default letter)
        header: 'running header text',                // optional; the title when absent ('' for none)
        blocks: [
          { type: 'title', text },                    // style Title
          { type: 'subtitle', text },                 // style Subtitle
          { type: 'h1', text }, { type: 'h2', text }, { type: 'h3', text },        // Heading1..3 (in the outline)
          { type: 'p', text },                        // style Body; text is a string or runs [{ t, b, i }]
          { type: 'note', text },                     // style Note: small, muted
          { type: 'label', label: 'Assumption', text },  // a Body paragraph led by a bold label ("Assumption (scenario): ...")
          { type: 'bullets', items: [text, ...] }, { type: 'numbered', items: [...] },  // numbering.xml (each list restarts)
          { type: 'table', title, cols: [...], rows: [[...]], note, widths: [weights], align: ['left'|'right', ...] },
                                                      // style Table; a header row that repeats on each page; numbers
                                                      // right-aligned unless align says otherwise; title above, note below
          { type: 'image', png: Uint8Array, w, h, title, caption, alt, width: 1 },   // w, h: the PNG's pixels; width: share
                                                      // of the text column (default 1); "Figure n. title" above, caption below
          { type: 'callout', label, text },           // style Callout: a shaded, ruled box
          { type: 'kpis', items: [{ label, value, sub, grade }] },   // a row of key figures (a borderless table)
          { type: 'pagebreak' }
        ]
      });

      Named styles in styles.xml: Normal, Title, Subtitle, Heading1, Heading2, Heading3, Body, Note, Caption, FigureTitle,
      Callout, ListBody, TableText, TableHead, and the table style Table. Text is escaped for XML and stripped of the
      control characters XML refuses; every run keeps its spaces.

   2. NLDocx.writeup(input, figures) -> doc: the research write-up (Track D) from the SAME report model as the PDF:
      NLReportPdf.model(input) (src/js/45-report-pdf.js; one model, two renderers, so they cannot disagree), plus the
      engine's own report slice input.engine (the page's S.report: health.columns, cleaning, answer, story.cannot_answer)
      for what the PDF does not print (missing values per column, rows set aside by reason). Sections: the question; the
      data as found; what was done; key findings (engine figures only, with their grades) and 2 or 3 charts; assumptions
      (each labelled with where it comes from); limits; whether the question can be answered, and if not what data is
      needed; how this write-up was made. Every figure is copied from the engine's results or the model, never computed
      here, except one subtraction: a column's empty cells = its rows minus its filled cells (both the engine's).
      NLDocx.pickCharts(input) -> [{ index, chart, title, units, caption, score }]: the 2 or 3 charts to embed, ordered by
      relevance to the question (placed by the AI in its report first, then the charts of the headline or the primary
      claim, then words shared with the question). The caller renders each to PNG and passes them as figures[i] =
      { png, w, h }.

   3. Browser only: NLDocx.svgToPng(svg, opts) -> Promise<{ png, w, h }> (an SVG element or markup, its computed styles
      inlined in the light theme, drawn at 2x on a white canvas); NLDocx.make(opts) -> Promise<{ bytes, name, figures,
      bundle() }> (the page's "Download Word" in one call); NLDocx.zip(files) (a stored zip; NLX.zip when the xlsx module
      is on the page, the same format). This file is ASCII only (it is inlined in index.html). */
(function (root) {
  'use strict';
  var NLDocx = {};

  /* ---------------------------------------------------------------- bytes and the zip */
  var CRC = null;
  function crc32(b) {
    if (!CRC) { CRC = []; for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; CRC[n] = c >>> 0; } }
    var x = 0xFFFFFFFF; for (var i = 0; i < b.length; i++) x = CRC[(x ^ b[i]) & 0xFF] ^ (x >>> 8);
    return (x ^ 0xFFFFFFFF) >>> 0;
  }
  function utf8(s) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(s);
    return new Uint8Array(Buffer.from(String(s), 'utf8'));   // an old Node
  }
  // a zip of stored (uncompressed) members, deterministic (no timestamps): what a .docx is. The xlsx module's NLX.zip
  // (src/js/56-xlsx.js) writes the same format; when it is on the page it is used, so the site has one zip writer.
  function zipLocal(files) {
    var parts = [], central = [], off = 0;
    var u16 = function (v) { return [v & 255, (v >>> 8) & 255]; }, u32 = function (v) { return [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255]; };
    files.forEach(function (f) {
      var name = utf8(f.name), data = typeof f.data === 'string' ? utf8(f.data) : f.data, crc = crc32(data);
      var head = [].concat(u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0x21), u32(crc), u32(data.length), u32(data.length), u16(name.length), u16(0));
      parts.push(new Uint8Array(head), name, data);
      central.push(new Uint8Array([].concat(u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0x21), u32(crc), u32(data.length), u32(data.length),
        u16(name.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(off))), name);
      off += head.length + name.length + data.length;
    });
    var csize = central.reduce(function (s, p) { return s + p.length; }, 0);
    var end = new Uint8Array([].concat(u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length), u32(csize), u32(off), u16(0)));
    var all = parts.concat(central, [end]), n = all.reduce(function (s, p) { return s + p.length; }, 0), out = new Uint8Array(n), at = 0;
    all.forEach(function (p) { out.set(p, at); at += p.length; });
    return out;
  }
  NLDocx.zip = function (files) {
    var X = typeof window !== 'undefined' && window.NLX && typeof window.NLX.zip === 'function' ? window.NLX.zip : null;
    return X ? X(files) : zipLocal(files);
  };
  NLDocx._zipLocal = zipLocal;

  /* ---------------------------------------------------------------- XML text */
  function xe(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f￾￿]/g, '')
      .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(^|[^\ud800-\udbff])[\udc00-\udfff]/g, '$1')
      .replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }
  NLDocx._xe = xe;
  var W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
  var XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';

  /* ---------------------------------------------------------------- the two styles */
  // sizes in half-points, spacing in twentieths of a point (twips); colours without '#'
  var STYLES = {
    report: { font: 'Arial', head: 'Arial', body: 21, line: 276, after: 120, h1: 32, h2: 25, h3: 22, title: 48, subtitle: 26, small: 17, table: 18,
      margin: 1440, ink: '16232E', text: '2B3843', muted: '56646F', accent: '0A5C52', rule: 'D9D5CB', panel: 'F4F2EC', headFill: 'F4F2EC',
      h1Before: 360, h1After: 120, h2Before: 240, h2After: 80, cellPad: 80, figure: 1 },
    // Track F's business document: a serif (Georgia, on every Windows and Mac; LibreOffice substitutes a metric match),
    // information dense: tight leading and spacing, a clear hierarchy (a ruled Heading 1, a bold Heading 2, a small-caps
    // Heading 3), narrower margins, charts that sit in the text column
    business: { font: 'Georgia', head: 'Georgia', body: 20, line: 240, after: 80, h1: 26, h2: 22, h3: 20, title: 40, subtitle: 22, small: 16, table: 16,
      margin: 1080, ink: '111111', text: '222222', muted: '5A5A5A', accent: '1F3A5F', rule: 'BFBFBF', panel: 'F2F2F2', headFill: 'E9EDF2',
      h1Before: 240, h1After: 60, h2Before: 160, h2After: 40, cellPad: 50, figure: 1 }
  };
  NLDocx.STYLES = STYLES;
  var PAPER = { letter: [12240, 15840], a4: [11906, 16838] };

  function stylesXml(S) {
    var rf = function (f) { return '<w:rFonts w:ascii="' + f + '" w:hAnsi="' + f + '" w:eastAsia="' + f + '" w:cs="' + f + '"/>'; };
    var para = function (id, name, o) {
      return '<w:style w:type="paragraph"' + (o.def ? ' w:default="1"' : '') + ' w:styleId="' + id + '"' + (o.custom ? ' w:customStyle="1"' : '') + '><w:name w:val="' + name + '"/>' +
        (o.based ? '<w:basedOn w:val="' + o.based + '"/>' : '') + (o.next ? '<w:next w:val="' + o.next + '"/>' : '') + '<w:qFormat/>' +
        '<w:pPr>' + (o.keep ? '<w:keepNext/><w:keepLines/>' : '') + (o.border || '') + (o.shd ? '<w:shd w:val="clear" w:color="auto" w:fill="' + o.shd + '"/>' : '') +
        '<w:spacing w:before="' + (o.before || 0) + '" w:after="' + (o.after === undefined ? S.after : o.after) + '" w:line="' + (o.line || S.line) + '" w:lineRule="auto"/>' +
        (o.ind ? o.ind : '') + (o.jc ? '<w:jc w:val="' + o.jc + '"/>' : '') + (o.outline !== undefined ? '<w:outlineLvl w:val="' + o.outline + '"/>' : '') + '</w:pPr>' +
        '<w:rPr>' + (o.font ? rf(o.font) : '') + (o.b ? '<w:b/><w:bCs/>' : '') + (o.i ? '<w:i/><w:iCs/>' : '') + (o.caps ? '<w:smallCaps/>' : '') +
        (o.color ? '<w:color w:val="' + o.color + '"/>' : '') + (o.sz ? '<w:sz w:val="' + o.sz + '"/><w:szCs w:val="' + o.sz + '"/>' : '') + (o.spacing ? '<w:spacing w:val="' + o.spacing + '"/>' : '') + '</w:rPr></w:style>';
    };
    var bottom = function (c, sz) { return '<w:pBdr><w:bottom w:val="single" w:sz="' + (sz || 6) + '" w:space="2" w:color="' + c + '"/></w:pBdr>'; };
    var biz = S === STYLES.business;
    return XML + '<w:styles ' + W_NS + '>' +
      '<w:docDefaults><w:rPrDefault><w:rPr>' + rf(S.font) + '<w:color w:val="' + S.text + '"/><w:sz w:val="' + S.body + '"/><w:szCs w:val="' + S.body + '"/><w:lang w:val="en-CA" w:eastAsia="en-CA" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>' +
      '<w:pPrDefault><w:pPr><w:spacing w:after="' + S.after + '" w:line="' + S.line + '" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>' +
      para('Normal', 'Normal', { def: true }) +
      para('Body', 'Body', { based: 'Normal', custom: true }) +
      para('Title', 'Title', { based: 'Normal', next: 'Subtitle', font: S.head, b: true, sz: S.title, color: S.ink, after: biz ? 60 : 120, line: 240, keep: true }) +
      para('Subtitle', 'Subtitle', { based: 'Normal', next: 'Body', font: S.head, i: biz, sz: S.subtitle, color: S.muted, after: biz ? 160 : 240, line: 260 }) +
      para('Heading1', 'heading 1', { based: 'Normal', next: 'Body', font: S.head, b: true, sz: S.h1, color: S.ink, before: S.h1Before, after: S.h1After, line: 240, keep: true, outline: 0,
        border: biz ? bottom(S.ink, 8) : '' }) +
      para('Heading2', 'heading 2', { based: 'Normal', next: 'Body', font: S.head, b: true, sz: S.h2, color: S.accent, before: S.h2Before, after: S.h2After, line: 240, keep: true, outline: 1 }) +
      para('Heading3', 'heading 3', { based: 'Normal', next: 'Body', font: S.head, b: !biz, caps: biz, sz: S.h3, color: S.ink, before: S.h2Before, after: 40, line: 240, keep: true, outline: 2, spacing: biz ? 10 : 0 }) +
      para('Note', 'Note', { based: 'Normal', custom: true, sz: S.small, color: S.muted, after: biz ? 60 : 100 }) +
      para('Caption', 'caption', { based: 'Normal', next: 'Body', i: true, sz: S.small, color: S.muted, before: 40, after: biz ? 140 : 200, line: 240 }) +
      para('FigureTitle', 'Figure Title', { based: 'Normal', custom: true, next: 'Body', font: S.head, b: true, sz: S.table + 2, color: S.ink, before: biz ? 120 : 200, after: 60, line: 240, keep: true }) +
      para('Callout', 'Callout', { based: 'Normal', custom: true, sz: S.body - 1, color: S.text, shd: S.panel, before: 80, after: 160,
        border: '<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="' + S.accent + '"/></w:pBdr>', ind: '<w:ind w:left="200" w:right="120"/>' }) +
      para('ListBody', 'List Body', { based: 'Normal', custom: true, after: biz ? 40 : 60, ind: '<w:ind w:left="360" w:hanging="260"/>' }) +
      para('TableText', 'Table Text', { based: 'Normal', custom: true, sz: S.table, after: 0, line: 240 }) +
      para('TableHead', 'Table Head', { based: 'Normal', custom: true, b: true, sz: S.table, color: S.ink, after: 0, line: 240, keep: true }) +
      '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/>' +
      '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>' +
      '<w:style w:type="table" w:styleId="Table" w:customStyle="1"><w:name w:val="Table"/><w:basedOn w:val="TableNormal"/><w:qFormat/>' +
      '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="' + S.table + '"/><w:szCs w:val="' + S.table + '"/></w:rPr>' +
      '<w:tblPr><w:tblBorders><w:top w:val="single" w:sz="8" w:space="0" w:color="' + S.ink + '"/><w:bottom w:val="single" w:sz="8" w:space="0" w:color="' + S.ink + '"/>' +
      '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="' + S.rule + '"/></w:tblBorders>' +
      '<w:tblCellMar><w:top w:w="' + S.cellPad + '" w:type="dxa"/><w:left w:w="' + (S.cellPad + 28) + '" w:type="dxa"/><w:bottom w:w="' + S.cellPad + '" w:type="dxa"/><w:right w:w="' + (S.cellPad + 28) + '" w:type="dxa"/></w:tblCellMar></w:tblPr>' +
      '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:bCs/><w:color w:val="' + S.ink + '"/></w:rPr><w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="' + S.headFill + '"/></w:tcPr></w:tblStylePr></w:style>' +
      '</w:styles>';
  }

  function numberingXml() {
    // abstract 0: bullets; abstract 1: decimal. Each list is its own w:num restarting at 1 (numberingXml's nums)
    var lvl = function (i, fmt, text, font) {
      return '<w:lvl w:ilvl="' + i + '"><w:start w:val="1"/><w:numFmt w:val="' + fmt + '"/><w:lvlText w:val="' + text + '"/><w:lvlJc w:val="left"/>' +
        '<w:pPr><w:ind w:left="' + (360 + i * 360) + '" w:hanging="260"/></w:pPr>' + (font ? '<w:rPr><w:rFonts w:ascii="' + font + '" w:hAnsi="' + font + '"/></w:rPr>' : '') + '</w:lvl>';
    };
    return function (nums) {
      return XML + '<w:numbering ' + W_NS + '>' +
        '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>' + lvl(0, 'bullet', '•', 'Arial') + lvl(1, 'bullet', '–', 'Arial') + '</w:abstractNum>' +
        '<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>' + lvl(0, 'decimal', '%1.') + lvl(1, 'lowerLetter', '%2.') + '</w:abstractNum>' +
        nums.map(function (n, i) {
          return '<w:num w:numId="' + (i + 1) + '"><w:abstractNumId w:val="' + n + '"/>' + (n === 1 ? '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>' : '') + '</w:num>';
        }).join('') + '</w:numbering>';
    };
  }

  /* ---------------------------------------------------------------- the document body */
  function runs(text, o) {
    o = o || {};
    var list = Array.isArray(text) ? text : [{ t: text }];
    return list.map(function (r) {
      if (typeof r === 'string') r = { t: r };
      var rp = (r.b || o.b ? '<w:b/><w:bCs/>' : '') + (r.i || o.i ? '<w:i/><w:iCs/>' : '') + (r.color || o.color ? '<w:color w:val="' + (r.color || o.color) + '"/>' : '') +
        (r.sz || o.sz ? '<w:sz w:val="' + (r.sz || o.sz) + '"/><w:szCs w:val="' + (r.sz || o.sz) + '"/>' : '');
      // a line break inside a run's text is a w:br
      return String(r.t === null || r.t === undefined ? '' : r.t).split('\n').map(function (seg, k) {
        return '<w:r>' + (rp ? '<w:rPr>' + rp + '</w:rPr>' : '') + (k ? '<w:br/>' : '') + '<w:t xml:space="preserve">' + xe(seg) + '</w:t></w:r>';
      }).join('');
    }).join('');
  }
  function para(style, text, o) {
    o = o || {};
    var pp = '<w:pStyle w:val="' + style + '"/>' + (o.keep ? '<w:keepNext/>' : '') + (o.num ? '<w:numPr><w:ilvl w:val="' + (o.lvl || 0) + '"/><w:numId w:val="' + o.num + '"/></w:numPr>' : '') +
      (o.jc ? '<w:jc w:val="' + o.jc + '"/>' : '') + (o.spacing || '');
    return '<w:p><w:pPr>' + pp + '</w:pPr>' + runs(text, o.run) + '</w:p>';
  }
  var NUMERIC = /^[-+−–(]?\s?(?:[A-Z]{3}\s|[$€£¥])?[\d][\d,]*(?:\.\d+)?\s?(?:%|pp|percentage points|[A-Za-z]{1,4})?\)?$/;
  function isNumeric(s) { s = String(s === null || s === undefined ? '' : s).trim(); return !!s && s.length <= 32 && NUMERIC.test(s); }
  NLDocx._isNumeric = isNumeric;

  function tableXml(b, S, textW) {
    var cols = (b.cols || []).map(function (c) { return String(c === null || c === undefined ? '' : c); });
    var rows = (b.rows || []).filter(Array.isArray).map(function (r) { return cols.map(function (_, k) { var v = r[k]; return v === null || v === undefined ? '' : String(v); }); });
    if (!cols.length) return '';
    // column widths: the caller's weights, or each column's longest word-run (header and cells, capped), as twips
    var wts = Array.isArray(b.widths) && b.widths.length === cols.length ? b.widths.map(Number) : cols.map(function (c, k) {
      var m = Math.min(28, Math.max(String(c).length * 0.8, 4));
      rows.forEach(function (r) { m = Math.max(m, Math.min(48, r[k].length)); });
      return m;
    });
    var tot = wts.reduce(function (a, x) { return a + x; }, 0) || 1, ws = wts.map(function (x) { return Math.max(700, Math.round(textW * x / tot)); });
    var fix = textW - ws.reduce(function (a, x) { return a + x; }, 0); ws[ws.length - 1] += fix;
    var right = cols.map(function (_, k) {
      if (Array.isArray(b.align) && b.align[k]) return b.align[k] === 'right';
      var n = rows.filter(function (r) { return r[k] !== ''; });
      return n.length > 0 && n.every(function (r) { return isNumeric(r[k]); });
    });
    var cell = function (txt, k, head) {
      return '<w:tc><w:tcPr><w:tcW w:w="' + ws[k] + '" w:type="dxa"/></w:tcPr>' + para(head ? 'TableHead' : 'TableText', txt, { jc: right[k] ? 'right' : '' }) + '</w:tc>';
    };
    var x = '<w:tbl><w:tblPr><w:tblStyle w:val="Table"/><w:tblW w:w="' + textW + '" w:type="dxa"/><w:tblLayout w:type="fixed"/>' +
      '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr>' +
      '<w:tblGrid>' + ws.map(function (w) { return '<w:gridCol w:w="' + w + '"/>'; }).join('') + '</w:tblGrid>';
    x += '<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>' + cols.map(function (c, k) { return cell(c, k, true); }).join('') + '</w:tr>';
    rows.forEach(function (r) { x += '<w:tr><w:trPr><w:cantSplit/></w:trPr>' + r.map(function (c, k) { return cell(c, k, false); }).join('') + '</w:tr>'; });
    return x + '</w:tbl>';
  }

  function kpisXml(b, S, textW) {
    var items = (b.items || []).filter(function (k) { return k && (k.value || k.label); }).slice(0, 4);
    if (!items.length) return '';
    var w = Math.floor(textW / items.length), shade = S.panel;
    var x = '<w:tbl><w:tblPr><w:tblW w:w="' + (w * items.length) + '" w:type="dxa"/><w:tblLayout w:type="fixed"/>' +
      '<w:tblBorders><w:insideV w:val="single" w:sz="18" w:space="0" w:color="FFFFFF"/></w:tblBorders>' +
      '<w:tblCellMar><w:top w:w="100" w:type="dxa"/><w:left w:w="140" w:type="dxa"/><w:bottom w:w="100" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tblCellMar></w:tblPr>' +
      '<w:tblGrid>' + items.map(function () { return '<w:gridCol w:w="' + w + '"/>'; }).join('') + '</w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr>';
    items.forEach(function (k) {
      x += '<w:tc><w:tcPr><w:tcW w:w="' + w + '" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="' + shade + '"/></w:tcPr>' +
        para('TableText', [{ t: String(k.label || '') }], { run: { color: S.muted, sz: S.small } }) +
        para('TableText', [{ t: String(k.value || ''), b: true }], { run: { color: S.ink, sz: S.h2 + 4 } }) +
        (k.sub ? para('TableText', String(k.sub), { run: { color: S.muted, sz: S.small } }) : '') +
        (k.grade ? para('TableText', [{ t: String(k.grade), b: true }], { run: { color: S.accent, sz: S.small } }) : '') + '</w:tc>';
    });
    return x + '</w:tr></w:tbl>' + para('Note', '', { spacing: '<w:spacing w:after="60"/>' });
  }

  function drawingXml(rid, id, cx, cy, name, alt) {
    return '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="' + cx + '" cy="' + cy + '"/><wp:effectExtent l="0" t="0" r="0" b="0"/>' +
      '<wp:docPr id="' + id + '" name="' + xe(name) + '" descr="' + xe(alt) + '"/><wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>' +
      '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">' +
      '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="' + id + '" name="' + xe(name) + '" descr="' + xe(alt) + '"/><pic:cNvPicPr/></pic:nvPicPr>' +
      '<pic:blipFill><a:blip r:embed="' + rid + '"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>' +
      '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="' + cx + '" cy="' + cy + '"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>' +
      '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>';
  }

  // "2026-10-16T09:30:00Z" (W3CDTF, whole seconds)
  function w3c(d) { d = d instanceof Date && isFinite(d) ? d : new Date(); return d.toISOString().replace(/\.\d{3}Z$/, 'Z'); }

  NLDocx.document = function (doc) {
    doc = doc || {};
    var S = STYLES[doc.style] || STYLES.report, pg = PAPER[doc.paper] || PAPER.letter, M = S.margin, textW = pg[0] - 2 * M;
    var rels = [], media = [], nums = [], figN = 0, picId = 0;
    var addRel = function (type, target) { var id = 'rId' + (rels.length + 1); rels.push({ id: id, type: type, target: target }); return id; };
    var R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/';
    addRel(R + 'styles', 'styles.xml'); addRel(R + 'numbering', 'numbering.xml'); addRel(R + 'settings', 'settings.xml');
    var hdrId = addRel(R + 'header', 'header1.xml'), ftrId = addRel(R + 'footer', 'footer1.xml');
    var body = '';
    (doc.blocks || []).forEach(function (b) {
      if (!b || typeof b !== 'object') return;
      var t = b.type;
      if (t === 'title') body += para('Title', b.text);
      else if (t === 'subtitle') body += para('Subtitle', b.text);
      else if (t === 'h1' || t === 'h2' || t === 'h3') body += para('Heading' + t.charAt(1), b.text);
      else if (t === 'p') body += para('Body', b.text, { keep: !!b.keep });
      else if (t === 'note') body += para('Note', b.text, { keep: !!b.keep });
      else if (t === 'label') body += para('Body', [{ t: String(b.label || '') + ': ', b: true }].concat(Array.isArray(b.text) ? b.text : [{ t: String(b.text || '') }]));
      else if (t === 'bullets' || t === 'numbered') {
        var items = (b.items || []).filter(function (x) { return x !== null && x !== undefined && String(Array.isArray(x) ? 'x' : x).trim(); });
        if (!items.length) return;
        nums.push(t === 'bullets' ? 0 : 1);
        var nid = nums.length;
        items.forEach(function (it) { body += para('ListBody', it, { num: nid }); });
      } else if (t === 'table') {
        if (!Array.isArray(b.cols) || !b.cols.length) return;
        if (b.title) body += para('FigureTitle', b.title, { keep: true });
        body += tableXml(b, S, textW);
        body += b.note ? para('Caption', b.note) : para('Note', '', { spacing: '<w:spacing w:after="' + S.after + '"/>' });
      } else if (t === 'kpis') body += kpisXml(b, S, textW);
      else if (t === 'callout') body += para('Callout', b.label ? [{ t: String(b.label) + '. ', b: true }, { t: String(b.text || '') }] : String(b.text || ''));
      else if (t === 'pagebreak') body += '<w:p><w:r><w:br w:type="page"/></w:r></w:p>';
      else if (t === 'image') {
        var png = b.png;
        if (!png || !png.length || !(b.w > 0) || !(b.h > 0)) {
          // no picture (a chart that could not be drawn): its title and caption still say what it was
          figN += 1;
          body += para('FigureTitle', [{ t: 'Figure ' + figN + '. ', b: true }, { t: String(b.title || '') }], { keep: true });
          body += para('Caption', (b.caption ? String(b.caption) + ' ' : '') + '(The chart could not be drawn in this browser; its figures are in the text.)');
          return;
        }
        figN += 1; picId += 1;
        var name = 'image' + picId + '.png';
        media.push({ name: 'word/media/' + name, data: png });
        var rid = addRel(R + 'image', 'media/' + name);
        var share = b.width > 0 && b.width <= 1 ? b.width : 1, wTw = Math.round(textW * share), cx = wTw * 635, cy = Math.round(cx * b.h / b.w);
        var maxCy = Math.round((pg[1] - 2 * M) * 635 * 0.62);
        if (cy > maxCy) { cx = Math.round(cx * maxCy / cy); cy = maxCy; }
        body += para('FigureTitle', [{ t: 'Figure ' + figN + '. ', b: true }, { t: String(b.title || '') }], { keep: true });
        body += '<w:p><w:pPr><w:pStyle w:val="Body"/><w:keepNext/><w:spacing w:before="40" w:after="40"/><w:jc w:val="center"/></w:pPr>' +
          drawingXml(rid, picId, cx, cy, 'Figure ' + figN, b.alt || b.title || ('Figure ' + figN)) + '</w:p>';
        if (b.caption) body += para('Caption', String(b.caption));
      }
    });
    var sect = '<w:sectPr><w:headerReference w:type="default" r:id="' + hdrId + '"/><w:footerReference w:type="default" r:id="' + ftrId + '"/>' +
      '<w:pgSz w:w="' + pg[0] + '" w:h="' + pg[1] + '"/><w:pgMar w:top="' + M + '" w:right="' + M + '" w:bottom="' + M + '" w:left="' + M + '" w:header="' + Math.round(M / 2) + '" w:footer="' + Math.round(M / 2) + '" w:gutter="0"/>' +
      '<w:cols w:space="720"/></w:sectPr>';
    var documentXml = XML + '<w:document ' + W_NS + ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">' +
      '<w:body>' + body + sect + '</w:body></w:document>';
    var hdrText = doc.header === undefined ? String(doc.title || '') : String(doc.header || '');
    var small = '<w:rPr><w:color w:val="' + S.muted + '"/><w:sz w:val="' + (S.small - 1) + '"/><w:szCs w:val="' + (S.small - 1) + '"/></w:rPr>';
    var header = XML + '<w:hdr ' + W_NS + '><w:p><w:pPr><w:jc w:val="right"/><w:spacing w:after="0"/></w:pPr>' +
      (hdrText ? '<w:r>' + small + '<w:t xml:space="preserve">' + xe(hdrText.length > 110 ? hdrText.slice(0, 107).replace(/\s+\S*$/, '') + '…' : hdrText) + '</w:t></w:r>' : '') + '</w:p></w:hdr>';
    var fld = function (code) { return '<w:fldSimple w:instr=" ' + code + ' "><w:r>' + small + '<w:t>1</w:t></w:r></w:fldSimple>'; };
    var footer = XML + '<w:ftr ' + W_NS + '><w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="0"/></w:pPr>' +
      '<w:r>' + small + '<w:t xml:space="preserve">Page </w:t></w:r>' + fld('PAGE') + '<w:r>' + small + '<w:t xml:space="preserve"> of </w:t></w:r>' + fld('NUMPAGES') + '</w:p></w:ftr>';
    var settings = XML + '<w:settings ' + W_NS + '><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/>' +
      '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>';
    var created = w3c(doc.created);
    var core = XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" ' +
      'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">' +
      '<dc:title>' + xe(doc.title || '') + '</dc:title><dc:subject>' + xe(doc.subject || '') + '</dc:subject><dc:creator>NorthLedger</dc:creator>' +
      '<cp:keywords>' + xe(doc.keywords || '') + '</cp:keywords><dc:description>' + xe(doc.description || '') + '</dc:description><cp:lastModifiedBy>NorthLedger</cp:lastModifiedBy>' +
      '<dc:language>en-CA</dc:language><dcterms:created xsi:type="dcterms:W3CDTF">' + created + '</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">' + created + '</dcterms:modified></cp:coreProperties>';
    var app = XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">' +
      '<Application>NorthLedger</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><Company></Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion></Properties>';
    var docRels = XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      rels.map(function (r) { return '<Relationship Id="' + r.id + '" Type="' + r.type + '" Target="' + r.target + '"/>'; }).join('') + '</Relationships>';
    var pkgRels = XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="' + R + 'officeDocument" Target="word/document.xml"/>' +
      '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>' +
      '<Relationship Id="rId3" Type="' + R + 'extended-properties" Target="docProps/app.xml"/></Relationships>';
    var WM = 'application/vnd.openxmlformats-officedocument.wordprocessingml.';
    var types = XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>' +
      '<Override PartName="/word/document.xml" ContentType="' + WM + 'document.main+xml"/><Override PartName="/word/styles.xml" ContentType="' + WM + 'styles+xml"/>' +
      '<Override PartName="/word/numbering.xml" ContentType="' + WM + 'numbering+xml"/><Override PartName="/word/settings.xml" ContentType="' + WM + 'settings+xml"/>' +
      '<Override PartName="/word/header1.xml" ContentType="' + WM + 'header+xml"/><Override PartName="/word/footer1.xml" ContentType="' + WM + 'footer+xml"/>' +
      '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>' +
      '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>';
    return NLDocx.zip([{ name: '[Content_Types].xml', data: types }, { name: '_rels/.rels', data: pkgRels }, { name: 'docProps/core.xml', data: core },
      { name: 'docProps/app.xml', data: app }, { name: 'word/document.xml', data: documentXml }, { name: 'word/styles.xml', data: stylesXml(S) },
      { name: 'word/numbering.xml', data: numberingXml()(nums) }, { name: 'word/settings.xml', data: settings }, { name: 'word/header1.xml', data: header },
      { name: 'word/footer1.xml', data: footer }, { name: 'word/_rels/document.xml.rels', data: docRels }].concat(media));
  };

  /* ---------------------------------------------------------------- Track D: the research write-up */
  var FILE_WORD = '[your file]';
  var GRADE = { RECOMMEND: 'CONFIRMED', CONFIRMED: 'CONFIRMED', WATCH: 'WATCH', INSUFFICIENT: 'NOT ENOUGH DATA', NOT_ENOUGH_DATA: 'NOT ENOUGH DATA' };
  function gradeWord(g) { g = String(g || '').toUpperCase(); return GRADE[g] || (g ? g.replace(/_/g, ' ') : ''); }
  function isObj(x) { return !!x && typeof x === 'object' && !Array.isArray(x); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return x === null || x === undefined ? '' : String(x); }
  function cap1(s) { s = str(s).trim(); return s.charAt(0).toUpperCase() + s.slice(1); }
  function sentence(s) { s = str(s).replace(/\s+/g, ' ').trim(); return s ? cap1(s) + (/[.!?:]$/.test(s) ? '' : '.') : ''; }
  function int(v) { return Number(v).toLocaleString('en-US'); }
  function uniq(list) { var seen = {}; return list.filter(function (x) { var k = str(x).toLowerCase().trim(); if (!k || seen[k]) return false; seen[k] = true; return true; }); }
  function pdfWriter() { return (typeof root !== 'undefined' && root && root.NLReportPdf) || (typeof window !== 'undefined' && window.NLReportPdf) || NLDocx.pdf || null; }

  var STOP = {}; ('what which when where why how does did do is are was were the and for with from that this these those into over than then them they there their about after before between ' +
    'across each every much many more most less least have has had will would could should can may might your our its show tell give file data month months year years total number').split(' ').forEach(function (w) { STOP[w] = 1; });
  function words(s) { return (str(s).toLowerCase().match(/[a-z][a-z'-]{3,}/g) || []).filter(function (w) { return !STOP[w]; }).map(function (w) { return w.replace(/(ies|es|s)$/, ''); }); }

  // the charts that can be drawn as a picture (not a table record, a KPI tile or a record with nothing to draw)
  function drawable(c) {
    if (!isObj(c)) return false;
    if (c.kind === 'table' || c.chart === 'table' || c.kind === 'kpi_tiles') return false;
    return isObj(c.data) || arr(c.series).length > 0 || arr(c.points).length > 0;
  }
  function chartUnits(c) {
    var m = isObj(c.measure) ? c.measure : null;
    var u = m ? [m.label, m.unit].filter(Boolean).join(', ') : '';
    if (!u) u = [c.y_name || c.unit || '', c.x_name ? 'against ' + c.x_name : ''].filter(Boolean).join(' ');
    // an analysis's own bars ("revenue by region"): the measure the bars show, by the groups they show
    var bm = !u && c.kind === 'bars' ? str(c.title).match(/^(.+?) by (.+)$/i) : null;
    if (bm) u = bm[1] + ', by ' + bm[2];
    if (m && m.kind === 'share_pct') u = cap1(m.label || 'share') + ', percent';
    else if (m && m.kind === 'count') u = 'Rows (count)';
    return u ? cap1(u) : '';
  }
  // the chart's one-line caption: the engine's own subtitle, else its summary's first sentence (the engine's words and figures)
  function chartCaption(c, analyses) {
    var s = str(c.subtitle).trim();
    // an analysis's own chart: the first sentence the engine wrote for that analysis
    if (!s && !str(c.summary).trim()) { var a = arr(analyses).filter(function (x) { return isObj(x) && x.title === c.title; })[0]; if (a) s = str(a.sentence).trim(); }
    if (!s) { s = str(c.summary).trim(); var m = s.match(/^(.+?[.!?])(\s|$)/); if (m) s = m[1]; }
    return s;
  }

  NLDocx.pickCharts = function (inp, max) {
    inp = inp || {};
    max = max || 3;
    var R = isObj(inp.results) ? inp.results : {}, charts = arr(R.charts).length ? R.charts : arr(inp.charts);
    var q = words(inp.goal || R.goal || ''), report = str(inp.report);
    var placed = {}, k = 0;
    report.replace(/\[CHART:(\d+)\]/g, function (m0, n) { n = Number(n) - 1; if (!(n in placed)) placed[n] = k++; return m0; });
    var askWhy = /\b(why|drove|drive[sn]?|driver|explain|cause[sd]?|behind|reason)\b/i.test(str(inp.goal || R.goal || ''));
    var primary = isObj(R.primary) ? str(R.primary.claim) : str(R.primary);
    var out = [];
    charts.forEach(function (c, i) {
      if (!drawable(c)) return;
      var score = 0, why = [];
      if (i in placed) { score += 6 - Math.min(3, placed[i]); why.push('placed by the AI in its report'); }
      if (c.section === 'headline') { score += 3; why.push('the headline\'s chart'); }
      if (primary && (str(c.supports) === primary || str(c.supports).toLowerCase() === primary.toLowerCase())) { score += 2; why.push('the primary claim\'s chart'); }
      var cw = words([c.title, c.subtitle, c.supports, c.y_name, c.x_name, isObj(c.measure) ? c.measure.label : ''].join(' ')), hit = 0;
      q.forEach(function (w) { if (cw.indexOf(w) >= 0) hit += 1; });
      if (hit) { score += Math.min(3, hit); why.push(hit + ' word' + (hit === 1 ? '' : 's') + ' of the question'); }
      // a chart that splits the change (by group, a waterfall) answers a "why" question
      var splits = c.chart === 'contribution_waterfall' || (isObj(c.inputs) && c.inputs.op === 'change');
      if (splits && askWhy) { score += 2; why.push('it splits the change, and the question asks why'); }
      out.push({ index: i, chart: c, title: str(c.title || c.kind || 'Chart'), units: chartUnits(c), caption: chartCaption(c, R.analyses), score: score, why: why });
    });
    out.sort(function (a, b) { return (b.score - a.score) || (a.index - b.index); });
    return out.slice(0, max);
  };

  // the blocks under one h2 (by id) of one part of the PDF's model
  function partBlocks(m, partId) { var p = arr(m.parts).filter(function (x) { return x.id === partId; })[0]; return p ? arr(p.blocks) : []; }
  function under(blocks, h2id) {
    var out = [], on = false;
    blocks.forEach(function (b) { if (b.type === 'h2') { on = b.id === h2id; return; } if (on) out.push(b); });
    return out;
  }
  function textsOf(blocks) {
    var out = [];
    blocks.forEach(function (b) { if (b.type === 'p') out.push(b.text); else if (b.type === 'bullets' || b.type === 'numbered') out = out.concat(arr(b.items)); });
    return out.map(str).filter(Boolean);
  }

  NLDocx.writeup = function (inp, figures, opts) {
    inp = inp || {};
    opts = opts || {};
    var W = pdfWriter();
    if (!W || typeof W.model !== 'function') throw new Error('the report model (NLReportPdf) is not loaded');
    // ONE model: the PDF's own, from the same input the page passes to the PDF
    var m = W.model(inp);
    var showName = inp.showName === true && !!inp.name, file = showName ? str(inp.name) : 'your file';
    var R = isObj(inp.results) ? inp.results : {}, E = isObj(inp.engine) ? inp.engine : {};
    // the engine's own report names the file in some sentences (results_for_ai writes "[your file]" instead): each name it
    // may carry becomes the reader's choice, the file's name or "your file", as in the PDF
    // (a name with an extension, or 6 characters or more: a short bare name could be part of an ordinary word)
    var names = uniq([str(inp.name), isObj(E.input) ? str(E.input.name) : ''].filter(function (x) { return /\.\w{1,5}$/.test(x) ? x.length >= 5 : x.length >= 6; }));
    var sub = function (s) { s = str(s).split(FILE_WORD).join(file); names.forEach(function (n) { s = s.split(n).join(file); }); return s; };
    var pa = partBlocks(m, 'method'), blocks = [];
    var add = function (b) { blocks.push(b); return b; };
    var date = m.date instanceof Date ? m.date : new Date();
    var day = date.getFullYear() + '-' + ('0' + (date.getMonth() + 1)).slice(-2) + '-' + ('0' + date.getDate()).slice(-2);
    var source = showName ? '“' + str(inp.name) + '”' : 'the reader\'s file (its name left off at the reader\'s choice)';

    // the title: the AI report's own (as the PDF's), else the question; never the engine's long list of graded claims
    var title = str(inp.report).trim() ? m.title : m.question ? 'Research write-up: ' + sub(m.question).replace(/[?.\s]+$/, '') : 'Research write-up';
    add({ type: 'title', text: title });
    add({ type: 'subtitle', text: 'A research write-up of ' + (showName ? str(inp.name) : 'the reader\'s file') + DOTW + day });
    add({ type: 'note', text: 'Source data: ' + source + '. The original file was not changed: NorthLedger read a copy of it in the reader\'s browser, and nothing was uploaded. ' +
      'Every figure below was computed by the NorthLedger engine from that file.' });

    // 1. the question, and the engine's answer in one line
    add({ type: 'h1', text: '1. The question' });
    var question = sub(m.question || R.goal || '');
    add({ type: 'p', text: question ? question : 'No question was given; the engine ran its standard analysis of the file\'s main measure over time.' });
    var AN = isObj(E.answer) && E.answer.text ? E.answer : isObj(R.answer) && R.answer.text ? R.answer : null;
    if (AN) add({ type: 'callout', label: 'The engine\'s answer', text: sub(AN.text) });

    // 2. the data as found
    add({ type: 'h1', text: '2. The data as found' });
    var IN = isObj(E.input) ? E.input : isObj(R.input) ? R.input : {}, CL = isObj(E.cleaning) ? E.cleaning : isObj(R.cleaning) ? R.cleaning : {};
    var facts = [];
    if (IN.rows !== undefined && IN.rows !== null) facts.push(int(IN.rows) + ' rows');
    if (IN.columns !== undefined && IN.columns !== null) facts.push(int(IN.columns) + ' columns');
    add({ type: 'p', text: 'The file' + (facts.length ? ' has ' + facts.join(' and ') : '') + '.' + (CL.rows_clean !== undefined && CL.rows_clean !== null && CL.rows_in !== undefined && CL.rows_in !== null
      ? ' The engine analysed ' + int(CL.rows_clean) + ' of its ' + int(CL.rows_in) + ' rows and set ' + int(CL.rows_quarantined || 0) + ' aside.' : '') });
    var HC = isObj(E.health) ? arr(E.health.columns) : [];
    if (HC.length) {
      add({ type: 'table', title: 'Missing values by column', cols: ['Column', 'Type', 'Rows', 'Filled', 'Empty', 'Filled, %'],
        rows: HC.map(function (c) {
          var k = isObj(c.completeness) ? c.completeness.k : null, n = isObj(c.completeness) ? c.completeness.n : c.n;
          var pct = isObj(c.completeness) && c.completeness.pct !== null && c.completeness.pct !== undefined ? Number(c.completeness.pct).toFixed(1) + '%' : '';
          return [str(c.name) + (c.withheld ? ' (withheld: no value shown)' : c.flagged ? ' (personal)' : ''), str(c.type), n === null || n === undefined ? '' : int(n),
            k === null || k === undefined ? '' : int(k), k === null || k === undefined || n === null || n === undefined ? '' : int(n - k), pct];
        }),
        note: 'Source: NorthLedger engine, the file as it arrived. Empty counts blank cells and placeholder values (N/A, NULL, a dash) the engine read as empty; Empty is Rows minus Filled. Nothing was filled in.' });
    } else {
      add({ type: 'note', text: 'The per-column count of missing values was not kept with this saved report.' });
    }
    var reasons = arr(CL.quarantine_reasons).filter(function (x) { return isObj(x) && x.reason; });
    if (reasons.length) {
      add({ type: 'table', title: 'Rows set aside, and why', cols: ['Reason', 'Rows'],
        rows: reasons.map(function (x) { var r = str(x.reason), s = r.indexOf(': '); return [cap1(s > 0 ? r.slice(s + 2) : r.replace(/_/g, ' ')), int(x.count)]; }),
        note: 'Source: NorthLedger engine. A row set aside is left out of the analysis, not changed or deleted; the engine\'s set-aside download lists each one.' });
    }
    var drops = under(pa, 'm-drops');
    if (drops.length) { add({ type: 'h2', text: 'Rows the analysis plan set aside' }); add({ type: 'bullets', items: textsOf(drops).map(sub) }); }
    var issues = textsOf(under(pa, 'm-issues'));
    if (issues.length) { add({ type: 'h2', text: 'What looked wrong in the file' }); add({ type: 'bullets', items: issues.map(sub) }); }

    // 3. what was done
    add({ type: 'h1', text: '3. What was done' });
    var did = [];
    var fixes = arr(CL.fixes).map(function (f) { return isObj(f) ? (f.what ? sentence(f.what) + (f.column ? ' (' + f.column + (f.count !== undefined ? ': ' + int(f.count) + ' values' : '') + ')' : f.count !== undefined ? ' (' + int(f.count) + ' values)' : '') : '') : sentence(f); }).filter(Boolean);
    did.push('Cleaned the copy: ' + (fixes.length ? fixes.length + ' cleaning rule' + (fixes.length === 1 ? '' : 's') + ' applied (listed below); ' : 'no value needed a repair; ') + 'rows that could not be used were set aside, never changed.');
    arr(R.plan_applied).forEach(function (x) { did.push('Analysis plan: ' + sentence(sub(x))); });
    arr(R.analyses).forEach(function (a) { if (isObj(a) && a.title) did.push(sentence(sub(a.title)) + (a.method ? ' Method: ' + sentence(sub(a.method)) : '')); });
    did.push('Compared the latest 12 months with the 12 before and graded each change: CONFIRMED when it clears the engine\'s bar and its interval excludes no change; WATCH when it moved but the evidence is not yet conclusive; NOT ENOUGH DATA when the file is too short or thin to judge.');
    add({ type: 'numbered', items: did });
    if (fixes.length) { add({ type: 'h2', text: 'The cleaning rules applied' }); add({ type: 'bullets', items: fixes }); }

    // 4. key findings: the engine's figures, with their grades, and the charts
    add({ type: 'h1', text: '4. Key findings' });
    if (arr(m.kpis).length) add({ type: 'kpis', items: m.kpis.map(function (k) { return { label: sub(k.label), value: str(k.value), sub: sub(k.sub), grade: gradeWord(k.grade) }; }) });
    var FB = under(pa, 'm-find').filter(function (b) { return b.type === 'findings'; })[0];
    var kindOf = {};
    arr(R.findings).forEach(function (f) { if (isObj(f) && f.claim) kindOf[sub(f.claim)] = f.kind; });
    var fitems = FB ? arr(FB.items) : [];
    var biz = fitems.filter(function (x) { return kindOf[sub(x.claim)] !== 'data_quality'; }), dq = fitems.filter(function (x) { return kindOf[sub(x.claim)] === 'data_quality'; });
    if (biz.length) add({ type: 'table', title: 'The findings and their grades', cols: ['Finding', 'Figure', 'Grade'], widths: [62, 18, 20], align: ['left', 'right', 'left'],
      rows: biz.map(function (x) { return [sub(x.claim), str(x.value), gradeWord(x.grade) + (x.untrusted ? ' (back-test failed: not trusted)' : '')]; }),
      note: 'Source: NorthLedger engine. A grade is the engine\'s rule, not a probability that the claim is true; a change is an association with the period, not a cause.' });
    else add({ type: 'p', text: 'The engine graded no business finding for this file.' });
    var figs = arr(figures);
    figs.forEach(function (f) {
      if (!isObj(f)) return;
      add({ type: 'image', png: f.png, w: f.w, h: f.h, title: cap1(sub(f.title)), alt: sub(f.title) + (f.units ? ' (' + sub(f.units) + ')' : ''),
        caption: [f.units ? 'Units: ' + sub(f.units) + '.' : '', sentence(sub(f.caption)), 'Source: NorthLedger engine, from ' + file + '.'].filter(Boolean).join(' ') });
    });
    if (dq.length) add({ type: 'table', title: 'Findings about the data itself', cols: ['Finding', 'Figure', 'Grade'], widths: [62, 18, 20], align: ['left', 'right', 'left'],
      rows: dq.map(function (x) { return [sub(x.claim), str(x.value), gradeWord(x.grade)]; }), note: 'Source: NorthLedger engine\'s data checks.' });

    // 5. assumptions: every one labelled with where it comes from
    add({ type: 'h1', text: '5. Assumptions' });
    var asm = [];
    var EST = isObj(E.estimand) ? E.estimand : isObj(R.estimand) ? R.estimand : null;
    if (EST && isObj(EST.measure)) {
      var MS = EST.measure, ty = str(MS.type).toLowerCase(), nm = str(MS.label || 'the measure');
      if (ty) asm.push(['Reading: flow or level', ty === 'flow' ? nm + ' is read as a flow: its months are added up' : nm + ' is read as a ' + ty + ': its months are averaged or taken at the period\'s end, never added up over time']);
      if (MS.aggregation) asm.push(['Reading: aggregation', sentence(MS.aggregation)]);
      if (MS.uom) asm.push(['Unit', nm + ' is in ' + MS.uom]);
      if (MS.scale_applied !== undefined && MS.scale_applied !== null && MS.scale_applied !== false && MS.scale_applied !== 1) asm.push(['Scale', 'the published values were scaled by the table\'s own scale (' + str(MS.scale_applied) + ') to base units']);
      arr(EST.slice).forEach(function (s) { if (isObj(s) && s.dim) asm.push(['Slice chosen', s.dim + ' = ' + s.member + (s.why ? ': ' + s.why : '')]); });
      if (EST.plan_source && W.facts && W.facts.planSourceWords) { var ps = W.facts.planSourceWords(EST); if (ps) asm.push(['How the headline was chosen', ps]); }
    }
    // how each measure was read, in the engine's own words for its claims: a monthly total is a flow (added up over the
    // month), an average month a level (averaged, never added up over time), the row volume a count of rows a month
    var readSeen = {};
    arr(R.findings).forEach(function (f) {
      var c = isObj(f) ? str(f.claim) : '', mt = c.match(/^Change in the (monthly total|average month) of (.+?)(?:,| like for like| latest 12)/i);
      if (mt && !readSeen[mt[2].toLowerCase()]) {
        readSeen[mt[2].toLowerCase()] = 1;
        asm.push(['Reading: flow or level', /total/i.test(mt[1]) ? mt[2] + ' is read as a flow: each month\'s rows are added up to a monthly total'
          : mt[2] + ' is read as a level: each month is its average, and months are never added up']);
      } else if (/row volume/i.test(c) && !readSeen['~rows']) { readSeen['~rows'] = 1; asm.push(['Reading: volume', 'the row volume is the count of the file\'s rows in each month']); }
    });
    var pp = arr(R.findings).some(function (f) { return isObj(f) && f.value_unit === 'percentage points'; });
    if (pp) asm.push(['Reading: rates', 'a change in a rate is stated in percentage points, the difference of the two rates, not a percent of a percent']);
    if (arr(R.findings).some(function (f) { return isObj(f) && /^change in /i.test(str(f.claim)) && f.value_unit !== 'percentage points'; }))
      asm.push(['Reading: periods', 'a change compares the latest 12 months with the 12 months before, month by month']);
    // the AI planner's reading of the file (what one row is, what the columns hold): its words, labelled as its reading
    if (R.reading) asm.push(['AI plan: what one row is', sub(R.reading)]);
    arr(R.plan_applied).forEach(function (x) { asm.push(['AI plan choice', sub(x)]); });
    arr(R.quality_risks).forEach(function (x) { asm.push(['AI plan: a risk it named', sub(x)]); });
    var SC = isObj(R.scenarios) ? R.scenarios : {};
    uniq(arr(SC.items).filter(function (x) { return isObj(x) && x.assumes; }).map(function (x) { return sub(x.assumes); })).forEach(function (a) { asm.push(['Scenario', 'the figures that depend on it assume ' + a.replace(/[.\s]+$/, '')]); });
    if (asm.length) asm.forEach(function (a) { add({ type: 'label', label: 'Assumption (' + a[0].toLowerCase() + ')', text: sentence(sub(a[1])) }); });
    else add({ type: 'p', text: 'The engine made no assumption beyond reading the file as it is: no value was filled, smoothed or estimated in its place.' });
    add({ type: 'note', text: 'Not assumed: no missing value was filled in, no gap was smoothed over and no figure was estimated in place of one the file lacks.' });

    // 6. limits
    add({ type: 'h1', text: '6. Limits of what the data can say' });
    // the engine's own limitations, whole (the page's report); the ones sent to the AI, cut to its budget, only without it
    var lim = uniq((arr(E.limitations).length ? arr(E.limitations) : arr(R.limitations)).map(function (x) { return isObj(x) ? x.text : x; })
      .map(function (x) { return sentence(sub(x)); }).filter(Boolean));
    if (!lim.length) lim = ['The engine compares periods in observational data: a change it reports is an association with the period, not a finding about its cause.'];
    add({ type: 'bullets', items: lim.slice(0, 14) });

    // 7. can the question be answered from this file?
    add({ type: 'h1', text: '7. Can the question be answered from this file?' });
    var story = isObj(E.story) ? E.story : isObj(R.story) ? R.story : {};
    var cannot = uniq([].concat(arr(story.cannot_answer), arr(R.plan_refused), arr(R.analyses_refused), arr(SC.refused)).map(function (x) { return sentence(sub(isObj(x) ? (x.text || x.why) : x)); }).filter(Boolean));
    var thin = arr(R.findings).filter(function (f) { return isObj(f) && f.kind === 'business' && gradeWord(f.verdict) === 'NOT ENOUGH DATA'; });
    // answered: the engine's answer, or a business claim it could grade (CONFIRMED or WATCH)
    var answered = !!AN || arr(R.findings).some(function (f) { return isObj(f) && f.kind === 'business' && gradeWord(f.verdict) !== 'NOT ENOUGH DATA'; });
    if (!answered) {
      add({ type: 'callout', label: 'Not from this file', text: 'The question cannot be answered from this file: the engine graded no finding on it beyond NOT ENOUGH DATA' + (cannot.length ? ', for the reasons below' : '') + '.' });
    } else {
      add({ type: 'p', text: 'Yes, as far as the findings above go: their grades say how firm each one is.' + (cannot.length ? ' Some parts cannot be answered from this file:' : '') });
    }
    if (cannot.length) add({ type: 'bullets', items: cannot });
    var needs = uniq(thin.map(function (f) { return sentence(sub(f.why)); }).filter(Boolean));
    if (!answered || needs.length) {
      add({ type: 'h2', text: 'What data would answer it' });
      add({ type: 'bullets', items: needs.length ? needs : ['More months of the same measure, so the latest 12 months can be compared with the 12 before, or a file with the column the question is about.'] });
    }

    // 8. how this write-up was made
    add({ type: 'h1', text: 'How this write-up was made' });
    // with no AI report (the AI off, or its report failed) the engine writes the whole write-up: the model's notice speaks
    // of an AI's wording, so this one says what happened instead
    var engineOnly = !str(inp.report).trim();
    add({ type: 'p', text: engineOnly ? 'This write-up was written by the engine without the AI: no AI model was asked to word it, and nothing was sent to make it. Every figure and every sentence comes from the NorthLedger engine\'s results for the reader\'s own file, computed in their browser. No person reviewed it.'
      : sub(m.notice) });
    add({ type: 'note', text: 'Made in the reader\'s browser on ' + day + ' by NorthLedger from the same report model as its PDF, so the two say the same thing. A browser cannot save a file beside the original: this write-up and its charts download to the browser\'s download folder. Source file: ' + (showName ? str(inp.name) : 'the reader\'s file') + ', unchanged.' });

    return { title: title, subject: 'Research write-up' + (showName ? ': ' + str(inp.name) : ''), description: 'The question, the data as found, what was done, key findings, assumptions and limits; every figure computed by the NorthLedger engine.',
      keywords: 'NorthLedger; research write-up' + (engineOnly ? '; written by the engine without the AI' : ''), created: date, style: opts.style || 'report', paper: opts.paper || 'letter', header: title, blocks: blocks, model: m };
  };
  var DOTW = ' · ';

  // "<title> - YYYY-MM-DD.docx" (the PDF's own naming: NLReportPdf.fileName, its extension swapped)
  NLDocx.fileName = function (date, name, title, ext) {
    var W = pdfWriter(), base = W && W.fileName ? W.fileName(date, name, title).replace(/\.pdf$/, '') : 'NorthLedger write-up';
    return base + (ext || '.docx');
  };
  // the .docx and the chart PNGs in one zip, with a note naming the source file (a browser cannot save beside the data)
  NLDocx.bundle = function (o) {
    o = o || {};
    var files = [{ name: o.docxName || 'write-up.docx', data: o.docx }];
    arr(o.figures).forEach(function (f, i) {
      if (!f || !f.png || !f.png.length) return;
      var slug = str(f.title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'chart';
      files.push({ name: 'figure-' + (i + 1) + '-' + slug + '.png', data: f.png });
    });
    files.push({ name: 'README.txt', data: ['This folder holds a research write-up made by NorthLedger in your browser, and its charts as PNG files.',
      '', 'Source data: ' + (o.sourceName ? o.sourceName : 'your file (its name left off at your choice)') + '. The original file was not changed and was never uploaded.',
      'A browser cannot save next to your data file: unzip this folder beside it to keep the write-up with the data.', '',
      'Files:'].concat(files.map(function (f) { return '  ' + f.name; })).join('\r\n') + '\r\n' });
    return NLDocx.zip(files);
  };

  /* ---------------------------------------------------------------- browser: SVG to PNG, and the page's one call */
  var STYLE_PROPS = ['fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-opacity', 'stroke-linecap', 'stroke-linejoin', 'opacity',
    'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline', 'letter-spacing', 'visibility', 'display', 'paint-order'];
  // an SVG (an element in the page, or markup drawn inside `host` so the page's CSS applies) with its computed styles
  // inlined, in the light theme whatever the page shows (a document is printed on white), drawn at `scale` on white
  NLDocx.svgToPng = function (svg, o) {
    o = o || {};
    return new Promise(function (resolve, reject) {
      var doc = document, rootEl = doc.documentElement, prev = rootEl.getAttribute('data-theme'), made = null, el = svg;
      try {
        if (typeof svg === 'string') {
          made = doc.createElement('div');
          made.setAttribute('aria-hidden', 'true');
          made.style.cssText = 'position:absolute;left:-10000px;top:0;width:' + (o.width || 680) + 'px;';
          made.className = o.hostClass || '';
          made.innerHTML = svg;
          (o.host || doc.body).appendChild(made);
          el = made.querySelector('svg');
        }
        if (!el) throw new Error('no chart to draw');
        rootEl.setAttribute('data-theme', 'light');
        var clone = el.cloneNode(true), src = el.querySelectorAll('*'), dst = clone.querySelectorAll('*');
        var inline = function (from, to) {
          var cs = getComputedStyle(from), s = '';
          STYLE_PROPS.forEach(function (p) { var v = cs.getPropertyValue(p); if (v) s += p + ':' + v + ';'; });
          if (o.font && /^(text|tspan)$/i.test(from.tagName)) s += 'font-family:' + o.font + ';';
          to.setAttribute('style', s);
        };
        inline(el, clone);
        for (var i = 0; i < src.length; i++) inline(src[i], dst[i]);
        var bb = el.getBoundingClientRect(), vb = el.viewBox && el.viewBox.baseVal;
        var w = Math.round((vb && vb.width) || Number(el.getAttribute('width')) || bb.width || 680), h = Math.round((vb && vb.height) || Number(el.getAttribute('height')) || bb.height || 320);
        clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        clone.setAttribute('width', w); clone.setAttribute('height', h);
        Array.prototype.forEach.call(clone.querySelectorAll('title'), function (t) { t.remove(); });
        var markup = new XMLSerializer().serializeToString(clone);
        if (prev === null) rootEl.removeAttribute('data-theme'); else rootEl.setAttribute('data-theme', prev);
        if (made) made.remove();
        var scale = o.scale || 2, img = new Image();
        img.onload = function () {
          try {
            var cv = doc.createElement('canvas');
            cv.width = Math.round(w * scale); cv.height = Math.round(h * scale);
            var cx = cv.getContext('2d');
            cx.fillStyle = '#ffffff'; cx.fillRect(0, 0, cv.width, cv.height);
            cx.drawImage(img, 0, 0, cv.width, cv.height);
            cv.toBlob(function (blob) {
              if (!blob) { reject(new Error('the chart could not be turned into a picture')); return; }
              blob.arrayBuffer().then(function (ab) { resolve({ png: new Uint8Array(ab), w: cv.width, h: cv.height }); }, reject);
            }, 'image/png');
          } catch (e) { reject(e); }
        };
        img.onerror = function () { reject(new Error('the chart could not be read as an image')); };
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(markup);
      } catch (e) {
        if (prev === null) rootEl.removeAttribute('data-theme'); else rootEl.setAttribute('data-theme', prev);
        if (made) made.remove();
        reject(e);
      }
    });
  };

  // the page's "Download Word": opts = { input (the PDF model's input), engine (the engine's full report), chartSvg(chart, W)
  // -> SVG markup, host (an element whose CSS draws the charts), hostClass, style, paper } -> Promise<{ bytes, name, figures,
  // bundle(): Uint8Array, bundleName }>. A chart that cannot be drawn keeps its title and caption, never a blank box.
  NLDocx.make = function (opts) {
    opts = opts || {};
    var inp = Object.assign({}, opts.input || {}, { engine: opts.engine || (opts.input && opts.input.engine) || null });
    var picks = NLDocx.pickCharts(inp, opts.max || 3), style = opts.style || 'report';
    var font = style === 'business' ? 'Georgia, serif' : '';
    return Promise.all(picks.map(function (p) {
      var markup = '';
      try { markup = opts.chartSvg ? opts.chartSvg(p.chart, 680) : ''; } catch (e) { markup = ''; }
      if (!markup || markup.indexOf('<svg') < 0) return Promise.resolve(Object.assign({}, p, { png: null }));
      return NLDocx.svgToPng(markup, { host: opts.host, hostClass: opts.hostClass, width: 680, font: font })
        .then(function (r) { return Object.assign({}, p, r); }, function () { return Object.assign({}, p, { png: null }); });
    })).then(function (figs) {
      var doc = NLDocx.writeup(inp, figs, { style: style, paper: opts.paper });
      var bytes = NLDocx.document(doc), date = doc.created, named = inp.showName === true && !!inp.name;
      var name = NLDocx.fileName(date, named ? inp.name : '', doc.title);
      return { bytes: bytes, name: name, figures: figs, title: doc.title,
        bundleName: name.replace(/\.docx$/, '') + ' (write-up and charts).zip',
        bundle: function () { return NLDocx.bundle({ docx: bytes, docxName: name, figures: figs, sourceName: named ? str(inp.name) : '' }); } };
    });
  };

  if (typeof module === 'object' && module && module.exports) module.exports = NLDocx; else root.NLDocx = NLDocx;
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this);
