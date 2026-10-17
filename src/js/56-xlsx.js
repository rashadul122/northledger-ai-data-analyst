/* window.NLX: an .xlsx writer that runs in the browser (SpreadsheetML + DrawingML in a stored zip; no library, nothing
   uploaded). Used by the Dashboard's Excel export (57-dashboard.js) and meant for reuse (the monthly reporting pack).

   var bytes = NLX.workbook({
     title: 'Workbook title',                        // docProps/core.xml
     sheets: [{
       name: 'Data',                                 // 1 to 31 characters, no []:*?/\ (cleaned if needed)
       widths: [30, 14, 14],                         // column widths in characters (optional)
       freeze: 1,                                    // rows frozen at the top (optional; a bold header row usually)
       rows: [                                       // each row an array of cells, or null for an empty row
         [{ v: 'Month', b: true }, { v: 'Rent', b: true }],
         ['Jan 2026', 81234.5],                      // a string, a number, or null / '' (an empty cell)
         ['Change', { f: 'C5-B5', v: 120, fmt: '#,##0' }],                       // a formula: f WITHOUT "=", v its cached value
         ['Share', { f: 'IF(B5=0,"",(C5-B5)/B5)', v: 0.045, fmt: 'pct' }]        // fmt: 'int', 'num', 'num2', 'pct', 'pct2' or a code
       ],
       validations: [                                // dropdown lists (optional): a range of a Lists sheet, or values
         { range: 'C2:C200', list: { sheet: 'Lists', col: 0, r0: 2, r1: 6 } },
         { range: 'D2:D200', values: ['Open', 'Done'] }
       ],
       tables: [{ name: 'Tracker', ref: 'A1:D20',      // Excel tables (optional): the first row of ref is the header,
                  calc: { 'Days open': 'Tracker[[#This Row],[Due]]-TODAY()' } }],   // with an autofilter; names are
                                                     // cleaned to letters, digits, _. calc: a column's calculated
                                                     // formula (no "="), which Excel copies into each row a visitor adds
       charts: [{                                    // native Excel charts drawn on this sheet (optional)
         type: 'line' | 'area' | 'column' | 'bar',   // area is stacked, column/bar clustered
         title: 'Rent by month',
         at: { col: 0, row: 3, cols: 10, rows: 18 }, // where it sits (0-based cells; optional, stacked by default)
         cat: { sheet: 'Data', col: 0, r0: 2, r1: 13 },          // the category labels (1-based rows)
         series: [{ name: { sheet: 'Data', col: 1, row: 1 }, sheet: 'Data', col: 1, r0: 2, r1: 13 }]
       }]
     }]
   });                                               // -> Uint8Array, the .xlsx file

   Dates: { v: '2026-10-16' (or a Date), fmt: 'date' } is written as Excel's date serial with a yyyy-mm-dd format
   ('datetime' and any code with y/m/d also work). Formulas may use any Excel function (TODAY(), COUNTIFS, SUMIFS, ...)
   and other sheets by name (NLX.ref('My sheet', 'A1') quotes the name).
   A formula cell is written as <f> with its cached <v>, so Excel shows the formula and a viewer that does not
   recalculate still shows the number. Strings are inline. Blank cells stay blank (a chart leaves a gap). */
(function () {
  'use strict';
  var NLX = {}, OWN = Object.prototype.hasOwnProperty;
  window.NLX = NLX;
  var CRC = null;
  function crc32(b) {
    if (!CRC) { CRC = []; for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; CRC[n] = c >>> 0; } }
    var x = 0xFFFFFFFF; for (var i = 0; i < b.length; i++) x = CRC[(x ^ b[i]) & 0xFF] ^ (x >>> 8);
    return (x ^ 0xFFFFFFFF) >>> 0;
  }
  function utf8(s) { return new TextEncoder().encode(s); }
  // a zip of stored (uncompressed) members, deterministic (no timestamps): what an .xlsx is
  NLX.zip = function (files) {
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
  };
  function xe(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, ''); }
  NLX.col = function (i) { var s = ''; i += 1; while (i > 0) { var m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; };
  var col = NLX.col;
  function colIdx(s) { var n = 0; for (var i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64); return n - 1; }
  function sheetName(s, used) {
    var n = String(s || 'Sheet').replace(/[\[\]:*?\/\\]/g, ' ').replace(/\s+/g, ' ').slice(0, 31).trim() || 'Sheet', k = 2, b = n;
    while (used[n.toLowerCase()]) n = b.slice(0, 28) + ' ' + (k++);
    used[n.toLowerCase()] = 1;
    return n;
  }
  function q(name) { return "'" + String(name).replace(/'/g, "''") + "'"; }
  var FMTS = { int: '#,##0', num: '#,##0.0', num2: '#,##0.00', pct: '0.0%', pct2: '0.00%', date: 'yyyy-mm-dd', datetime: 'yyyy-mm-dd hh:mm' };
  // a date as Excel's serial number (days since 1899-12-30, the 1900 system), or null
  NLX.dateSerial = function (d) {
    var t = d instanceof Date ? Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds())
      : (typeof d === 'string' && /^\d{4}-\d{2}-\d{2}/.test(d) ? Date.parse(d.length === 10 ? d + 'T00:00:00Z' : d.replace(' ', 'T') + (/Z|[+-]\d\d:?\d\d$/.test(d) ? '' : 'Z')) : NaN);
    return isFinite(t) ? Math.round((t / 86400000 + 25569) * 1e6) / 1e6 : null;
  };
  NLX.ref = function (sheet, cell) { return q(sheet) + '!' + cell; };

  NLX.workbook = function (wb) {
    var used = {}, NAME = {}, sheets = (wb.sheets || []).map(function (s) { var c = sheetName(s.name, used); NAME[String(s.name)] = c; return Object.assign({}, s, { name: c }); });
    // a reference names a sheet as the caller did: the name it was written under (cleaned of characters Excel refuses)
    var nm = function (x) { return OWN.call(NAME, String(x)) ? NAME[String(x)] : String(x); };
    // styles: one cellXfs entry per (bold, number format) pair the cells use
    var numFmts = [], xfs = [{ b: false, id: 0 }], xfKey = { 'false|0': 0 };
    function styleOf(cell) {
      var b = !!(cell && cell.b), f = cell && cell.fmt ? (FMTS[cell.fmt] || cell.fmt) : null, id = 0;
      if (f) { var i = numFmts.indexOf(f); if (i < 0) { numFmts.push(f); i = numFmts.length - 1; } id = 164 + i; }
      var key = b + '|' + id;
      if (!(key in xfKey)) { xfKey[key] = xfs.length; xfs.push({ b: b, id: id }); }
      return xfKey[key];
    }
    function cellXml(v, ref) {
      if (v === null || v === undefined || v === '') return '';
      var o = typeof v === 'object' && !(v instanceof Date) ? v : { v: v, fmt: v instanceof Date ? 'date' : undefined };
      if (o.v instanceof Date || (/^date/.test(o.fmt || '') && typeof o.v === 'string')) { var ds = NLX.dateSerial(o.v); o = Object.assign({}, o, { v: ds === null ? o.v : ds }); }
      var s = styleOf(o), sa = s ? ' s="' + s + '"' : '';
      if (o.f) {
        var fv = o.v, cached = typeof fv === 'number' && isFinite(fv) ? '<v>' + fv + '</v>' : (typeof fv === 'string' ? '<v>' + xe(fv) + '</v>' : '');
        return '<c r="' + ref + '"' + sa + (typeof fv === 'string' ? ' t="str"' : '') + '><f>' + xe(String(o.f).replace(/^=/, '')) + '</f>' + cached + '</c>';
      }
      if (typeof o.v === 'number') return isFinite(o.v) ? '<c r="' + ref + '"' + sa + '><v>' + o.v + '</v></c>' : '';
      if (o.v === null || o.v === undefined || o.v === '') return s ? '<c r="' + ref + '"' + sa + '/>' : '';
      return '<c r="' + ref + '"' + sa + ' t="inlineStr"><is><t xml:space="preserve">' + xe(o.v) + '</t></is></c>';
    }
    var R3 = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships', PKG = 'http://schemas.openxmlformats.org/package/2006/relationships';
    var files = [], charts = [], drawings = [], tables = [], tableNames = {};
    sheets.forEach(function (sh, si) {
      var x = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="' + R3 + '">';
      if (sh.freeze) x += '<sheetViews><sheetView workbookViewId="0"><pane ySplit="' + sh.freeze + '" topLeftCell="A' + (sh.freeze + 1) + '" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>';
      if (sh.widths && sh.widths.length) x += '<cols>' + sh.widths.map(function (w, i) { return '<col min="' + (i + 1) + '" max="' + (i + 1) + '" width="' + w + '" customWidth="1"/>'; }).join('') + '</cols>';
      x += '<sheetData>';
      (sh.rows || []).forEach(function (r, i) {
        if (!r) return;
        var cells = r.map(function (v, j) { return cellXml(v, col(j) + (i + 1)); }).join('');
        if (cells) x += '<row r="' + (i + 1) + '">' + cells + '</row>';
      });
      x += '</sheetData>';
      var rels = [];                                  // this sheet's relationships: its drawing and its tables
      var dv = (sh.validations || []).filter(function (v) { return v && v.range && (v.list || (v.values && v.values.length)); });
      if (dv.length) {
        x += '<dataValidations count="' + dv.length + '">' + dv.map(function (v) {
          var f = v.list ? q(nm(v.list.sheet)) + '!$' + col(v.list.col) + '$' + v.list.r0 + ':$' + col(v.list.col) + '$' + v.list.r1
            : '"' + v.values.map(function (t) { return String(t).replace(/"/g, '""').replace(/,/g, ' '); }).join(',') + '"';
          return '<dataValidation type="list" allowBlank="1" showInputMessage="1" showErrorMessage="1" sqref="' + xe(v.range) + '"><formula1>' + xe(f) + '</formula1></dataValidation>';
        }).join('') + '</dataValidations>';
      }
      var cs = sh.charts || [];
      if (cs.length) {
        var dn = drawings.length + 1, anchors = '';
        cs.forEach(function (c, k) {
          var cn = charts.length + 1, at = c.at || { col: 0, row: 2 + k * 20, cols: 10, rows: 18 };
          charts.push({ n: cn, xml: chartXml(c, nm) });
          anchors += '<xdr:twoCellAnchor><xdr:from><xdr:col>' + at.col + '</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>' + at.row + '</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>' + (at.col + at.cols) +
            '</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>' + (at.row + at.rows) + '</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to><xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="' + (k + 2) +
            '" name="Chart ' + cn + '" descr="' + xe(c.title) + '"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr><xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">' +
            '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId' + (k + 1) + '"/></a:graphicData></a:graphic></xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor>';
        });
        drawings.push({ n: dn, sheet: si + 1, first: charts.length - cs.length + 1, count: cs.length,
          xml: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="' + R3 + '">' + anchors + '</xdr:wsDr>' });
        rels.push('<Relationship Id="rId' + (rels.length + 1) + '" Type="' + R3 + '/drawing" Target="../drawings/drawing' + dn + '.xml"/>');
        x += '<drawing r:id="rId' + rels.length + '"/>';
      }
      var tb = (sh.tables || []).filter(function (t) { return t && /^[A-Z]+\d+:[A-Z]+\d+$/.test(t.ref || ''); });
      if (tb.length) {
        x += '<tableParts count="' + tb.length + '">' + tb.map(function (t) {
          var tn = tables.length + 1, m = t.ref.match(/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/), c0 = colIdx(m[1]), c1 = colIdx(m[3]), hr = +m[2];
          var head = (sh.rows || [])[hr - 1] || [], names = {}, cols = [];
          for (var c = c0; c <= c1; c++) {
            var h = head[c], t0 = String(h && typeof h === 'object' ? h.v : (h === null || h === undefined ? '' : h)).trim() || 'Column' + (c - c0 + 1), k = 2, nm = t0;
            while (names[nm.toLowerCase()]) nm = t0 + k++;
            names[nm.toLowerCase()] = 1; cols.push(nm);
          }
          var name = String(t.name || 'Table' + tn).replace(/[^A-Za-z0-9_]/g, '_').replace(/^[^A-Za-z_]/, '_') + (tableNames[String(t.name || '').toLowerCase()] ? tn : '');
          tableNames[name.toLowerCase()] = 1;
          tables.push({ n: tn, xml: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="' + tn + '" name="' + xe(name) + '" displayName="' + xe(name) + '" ref="' + t.ref +
            '" totalsRowShown="0"><autoFilter ref="' + t.ref + '"/><tableColumns count="' + cols.length + '">' + cols.map(function (cn, i) {
              var cf = t.calc && OWN.call(t.calc, cn) ? '<calculatedColumnFormula>' + xe(String(t.calc[cn]).replace(/^=/, '')) + '</calculatedColumnFormula>' : '';
              return '<tableColumn id="' + (i + 1) + '" name="' + xe(cn) + '"' + (cf ? '>' + cf + '</tableColumn>' : '/>'); }).join('') +
            '</tableColumns><tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>' });
          rels.push('<Relationship Id="rId' + (rels.length + 1) + '" Type="' + R3 + '/table" Target="../tables/table' + tn + '.xml"/>');
          return '<tablePart r:id="rId' + rels.length + '"/>';
        }).join('') + '</tableParts>';
      }
      if (rels.length) files.push({ name: 'xl/worksheets/_rels/sheet' + (si + 1) + '.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="' + PKG + '">' + rels.join('') + '</Relationships>' });
      files.push({ name: 'xl/worksheets/sheet' + (si + 1) + '.xml', data: x + '</worksheet>' });
    });
    tables.forEach(function (t) { files.push({ name: 'xl/tables/table' + t.n + '.xml', data: t.xml }); });
    drawings.forEach(function (d) {
      files.push({ name: 'xl/drawings/drawing' + d.n + '.xml', data: d.xml });
      var rel = ''; for (var k = 0; k < d.count; k++) rel += '<Relationship Id="rId' + (k + 1) + '" Type="' + R3 + '/chart" Target="../charts/chart' + (d.first + k) + '.xml"/>';
      files.push({ name: 'xl/drawings/_rels/drawing' + d.n + '.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="' + PKG + '">' + rel + '</Relationships>' });
    });
    charts.forEach(function (c) { files.push({ name: 'xl/charts/chart' + c.n + '.xml', data: c.xml }); });
    var styles = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
      (numFmts.length ? '<numFmts count="' + numFmts.length + '">' + numFmts.map(function (f, i) { return '<numFmt numFmtId="' + (164 + i) + '" formatCode="' + xe(f) + '"/>'; }).join('') + '</numFmts>' : '') +
      '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>' +
      '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="' + xfs.length + '">' +
      xfs.map(function (x) { return '<xf numFmtId="' + x.id + '" fontId="' + (x.b ? 1 : 0) + '" fillId="0" borderId="0" xfId="0"' + (x.id ? ' applyNumberFormat="1"' : '') + (x.b ? ' applyFont="1"' : '') + '/>'; }).join('') + '</cellXfs></styleSheet>';
    var ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
      sheets.map(function (s, i) { return '<Override PartName="/xl/worksheets/sheet' + (i + 1) + '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'; }).join('') +
      drawings.map(function (d) { return '<Override PartName="/xl/drawings/drawing' + d.n + '.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'; }).join('') +
      charts.map(function (c) { return '<Override PartName="/xl/charts/chart' + c.n + '.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'; }).join('') +
      tables.map(function (t) { return '<Override PartName="/xl/tables/table' + t.n + '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>'; }).join('') +
      '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>';
    var head = [
      { name: '[Content_Types].xml', data: ct },
      { name: '_rels/.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="' + PKG + '"><Relationship Id="rId1" Type="' + R3 + '/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>' },
      { name: 'docProps/core.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + xe(wb.title || 'Workbook') + '</dc:title><dc:creator>NorthLedger (in the browser)</dc:creator></cp:coreProperties>' },
      { name: 'xl/workbook.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="' + R3 + '"><sheets>' + sheets.map(function (s, i) { return '<sheet name="' + xe(s.name) + '" sheetId="' + (i + 1) + '" r:id="rId' + (i + 1) + '"/>'; }).join('') + '</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>' },
      { name: 'xl/_rels/workbook.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="' + PKG + '">' + sheets.map(function (s, i) { return '<Relationship Id="rId' + (i + 1) + '" Type="' + R3 + '/worksheet" Target="worksheets/sheet' + (i + 1) + '.xml"/>'; }).join('') + '<Relationship Id="rId' + (sheets.length + 1) + '" Type="' + R3 + '/styles" Target="styles.xml"/></Relationships>' },
      { name: 'xl/styles.xml', data: styles }
    ];
    return NLX.zip(head.concat(files));
  };

  // a chart part: line, stacked area, clustered column or bar, its series referencing cell ranges
  function chartXml(spec, nm) {
    var ns = 'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
    var PAL = ['24425C', '0B7366', '945400', '2A78D6', 'D4502C', '56646F'], DASH = ['dash', 'sysDot', 'lgDashDot', 'sysDash'];
    var ref = function (r) { return q(nm(r.sheet)) + '!$' + col(r.col) + '$' + r.r0 + ':$' + col(r.col) + '$' + r.r1; };
    var line = spec.type === 'line';
    var sers = (spec.series || []).map(function (s, k) {
      var color = PAL[k % PAL.length];
      var sp = line ? '<c:spPr><a:ln w="22225"><a:solidFill><a:srgbClr val="' + color + '"/></a:solidFill>' + (k ? '<a:prstDash val="' + DASH[(k - 1) % 4] + '"/>' : '') + '</a:ln></c:spPr><c:marker><c:symbol val="circle"/><c:size val="4"/></c:marker>'
        : '<c:spPr><a:solidFill><a:srgbClr val="' + color + '"/></a:solidFill></c:spPr>';
      var tx = s.name ? '<c:tx><c:strRef><c:f>' + q(nm(s.name.sheet)) + '!$' + col(s.name.col) + '$' + s.name.row + '</c:f></c:strRef></c:tx>' : '';
      return '<c:ser><c:idx val="' + k + '"/><c:order val="' + k + '"/>' + tx + sp + (line ? '' : '<c:invertIfNegative val="0"/>') +
        '<c:cat><c:strRef><c:f>' + ref(spec.cat) + '</c:f></c:strRef></c:cat><c:val><c:numRef><c:f>' + ref(s) + '</c:f></c:numRef></c:val>' + (line ? '<c:smooth val="0"/>' : '') + '</c:ser>';
    }).join('');
    var axes = '<c:axId val="1001"/><c:axId val="1002"/>', bar = spec.type === 'bar';
    var plot = line ? '<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>' + sers + '<c:marker val="1"/>' + axes + '</c:lineChart>'
      : spec.type === 'area' ? '<c:areaChart><c:grouping val="stacked"/><c:varyColors val="0"/>' + sers + axes + '</c:areaChart>'
      : '<c:barChart><c:barDir val="' + (bar ? 'bar' : 'col') + '"/><c:grouping val="clustered"/><c:varyColors val="0"/>' + sers + '<c:gapWidth val="60"/>' + axes + '</c:barChart>';
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><c:chartSpace ' + ns + '><c:roundedCorners val="0"/><c:chart><c:title><c:tx><c:rich><a:bodyPr/><a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr><a:r><a:t>' + xe(spec.title) +
      '</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>' + plot +
      '<c:catAx><c:axId val="1001"/><c:scaling><c:orientation val="' + (bar ? 'maxMin' : 'minMax') + '"/></c:scaling><c:delete val="0"/><c:axPos val="' + (bar ? 'l' : 'b') + '"/><c:numFmt formatCode="General" sourceLinked="1"/><c:tickLblPos val="nextTo"/><c:crossAx val="1002"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/></c:catAx>' +
      '<c:valAx><c:axId val="1002"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="' + (bar ? 'b' : 'l') + '"/><c:majorGridlines/><c:numFmt formatCode="#,##0" sourceLinked="0"/><c:tickLblPos val="nextTo"/><c:crossAx val="1001"/><c:crosses val="' + (bar ? 'max' : 'autoZero') + '"/><c:crossBetween val="between"/></c:valAx>' +
      '</c:plotArea><c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend><c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart></c:chartSpace>';
  }
})();
