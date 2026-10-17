/* The quarterly sales report (Track F of plan/DASHBOARD-DESIGN.md; CONTRACT-v2 section 5.19): one option of step 6's
   report menu, window.NLDeliver.register({id: 'sales-report', ...}). The engine (engine/nl_sales.py) computed the report
   in the run, from the rows it kept (rep.sales); this file only places the engine's figures and words:

     NLSales.doc(rec, opts) -> the document model: {title, subject, style: 'business', paper, header, created, blocks}
                               for NLDocx.document (src/js/46-report-docx.js) and for the page's preview (one model,
                               two renderers, so they cannot disagree). A one-page executive summary first, then the
                               sections; each chart is an image block carrying its chart registry record.
     NLSales.html(doc, draw) -> the preview's markup (draw(record) -> the chart's SVG markup, window.NLV)
     NLSales.htmlFile(doc, pngs) -> a self-contained HTML file of the same document (printable to PDF)
     NLSales.NEEDS / NLSales.HOW -> step 1's checklist: the columns a deals export needs, and how to export from
                               Salesforce, HubSpot, Pipedrive and Zoho

   In the browser the card (render) shows the columns the engine read and lets the visitor change any of them, the
   fiscal year's first month, the quarter, a target for the next quarter, when an open deal counts as stalled and the
   currency; "Update the report" asks the run's own worker again (window.NLTry.salesReport -> nl_browser.sales_json): no
   AI call, nothing sent. Downloads: Word (.docx, the business style: serif, dense, the charts as PNG in the text column)
   and HTML (print it to PDF). Every figure is the engine's; nothing here computes one.

   The step-6 registry (window.NLDeliver) is Track E's (src/js/60-deliver.js, plan/DASHBOARD-DESIGN.md "Batch 2 shared
   interface AS BUILT"): this option registers on it when it is on the page, or queues itself in window.NLDeliverQueue
   (which 60-deliver.js reads on boot); only when no registry has appeared by DOMContentLoaded does this file bring a
   minimal one of the same shape (register / list / choose, the "Report types" menu in step 6, the "What do you need?"
   checklist in step 1). With Track E's registry, the registry draws the settings form and calls render again with the
   visitor's settings; with the minimal one, the card draws its own. This file is ASCII only. */
(function (root) {
  'use strict';
  var NLSales = {};
  var MINUS = '−';
  function isObj(x) { return !!x && typeof x === 'object' && !Array.isArray(x); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return x === null || x === undefined ? '' : String(x); }
  function esc(s) { return str(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  /* ---------------------------------------------------------------- what the export needs (step 1) */
  // the engine's own list (nl_sales.NEEDS, also in every rep.sales.needs): role, what, optional
  NLSales.NEEDS = [
    { role: 'id', what: 'Deal (opportunity) ID', optional: true },
    { role: 'amount', what: 'Amount (the deal\'s value)', optional: false },
    { role: 'stage', what: 'Stage (Prospecting ... Closed Won / Closed Lost)', optional: false },
    { role: 'outcome', what: 'Won / lost (or closed-won / closed-lost stages)', optional: false },
    { role: 'segment', what: 'Segment (e.g. Enterprise, Mid-Market, SMB)', optional: true },
    { role: 'owner', what: 'Owner / rep', optional: true },
    { role: 'created', what: 'Created date', optional: true },
    { role: 'close', what: 'Close date (expected close for open deals)', optional: false },
    { role: 'probability', what: 'Probability (%)', optional: true },
    { role: 'stage_changed', what: 'Last stage change (or last activity) date', optional: true }
  ];
  NLSales.HOW = [
    ['Salesforce', 'Reports > New Report > Opportunities; add the columns below (Opportunity ID, Amount, Stage, Probability (%), Created Date, Close Date, Last Stage Change Date, Opportunity Owner and your segment field); Export > Details Only > CSV.'],
    ['HubSpot', 'CRM > Deals, the table view with every pipeline; Export > CSV, with the properties below (Record ID, Amount, Deal Stage, Deal probability, Create Date, Close Date, Deal owner and your segment property).'],
    ['Pipedrive', 'Deals > the list view with all deals (open, won and lost); ... > Export filter results > CSV, with Status, Stage, Value, Deal created, Expected close date, Owner and your segment field.'],
    ['Zoho CRM', 'Setup > Data Administration > Export > Deals (or a Deals report > Export), CSV, with Stage, Amount, Probability, Created Time, Closing Date, Deal Owner and your segment field.']
  ];
  // the registry's needs shape: [{what, columns, optional, how}]
  NLSales.needs = function () {
    return NLSales.NEEDS.map(function (n) { return { what: n.what, columns: [n.role], optional: n.optional, how: '' }; });
  };

  /* ---------------------------------------------------------------- the document model */
  function kpiItem(k, L0) {
    return { label: k.label, value: k.text, sub: (k.prev_text ? L0 + ': ' + k.prev_text : '') + (k.change_text ? '; ' + k.change_text : '') };
  }
  function para(text) { return { type: 'p', text: text }; }
  function chartBlock(rec, n) {
    var cap = str(rec.summary);
    var unit = rec.measure && rec.measure.label ? rec.measure.label + (rec.measure.unit && rec.measure.unit !== '%' ? ' (' + rec.measure.unit + ')' : rec.measure.unit === '%' ? ' (percent)' : '') : '';
    return { type: 'image', chart: rec, title: str(rec.title), caption: (unit ? unit + '. ' : '') + cap, alt: str(rec.title) + '. ' + cap, width: 0.78, n: n };
  }
  // opts: {paper, created (Date), source (words for the source file)}
  NLSales.doc = function (rec, opts) {
    opts = opts || {};
    var q = rec.quarter || {}, L1 = str(q.label), L0 = str(q.prev_label), sm = rec.summary || {}, B = [];
    var charts = {}; arr(rec.charts).forEach(function (c) { charts[c.chart + (c.chart === 'grouped_bar' ? ':' + (/^Won revenue/.test(c.title) ? 'rev' : /^Win rate/.test(c.title) ? 'win' : 'cyc') : '')] = c; });
    var n = 0, fig = function (key, w) { var c = charts[key]; if (!c) return; n += 1; var b = chartBlock(c, n); if (w) b.width = w; B.push(b); };
    var src = opts.source || 'your CRM export';
    // ---- page 1: the executive summary
    B.push({ type: 'title', text: 'Quarterly sales report: ' + L1 });
    B.push({ type: 'subtitle', text: L1 + ' (' + str(q.start) + ' to ' + str(q.end) + ') against ' + L0 + '. From ' + src + ' (' + str((rec.counts || {}).rows) + ' deals), exported ' + str(rec.as_of) + '.' });
    B.push({ type: 'h1', text: 'Executive summary' });
    var ks = arr(rec.kpis), pl = rec.pipeline || {};
    B.push({ type: 'kpis', items: ks.slice(0, 4).map(function (k) { return kpiItem(k, L0); }) });
    var row2 = ks.slice(4).map(function (k) { return kpiItem(k, L0); });
    if (pl.ok && pl.value !== null && pl.value !== undefined && isObj(pl.texts)) {
      var pt = pl.texts;
      row2.push({ label: 'Open pipeline', value: pt.value, sub: pt.weighted ? pt.weighted + ' weighted' : pt.n + ' deals' });
      if (pt.stalled_share) row2.push({ label: 'Stalled', value: pt.stalled_share, sub: pt.stalled_n + ' deals; share of the open value' });
      if (pt.coverage) row2.push({ label: 'Coverage of the ' + str(pl.coverage.label) + ' target', value: pt.coverage, sub: pt.coverage_weighted ? pt.coverage_weighted + ' weighted' : '' });
    }
    if (row2.length) B.push({ type: 'kpis', items: row2.slice(0, 4) });
    if (q.note) B.push({ type: 'note', text: q.note });
    if (sm.headline) B.push({ type: 'p', text: [{ t: sm.headline, b: true }] });
    if (arr(sm.lines).length) B.push({ type: 'bullets', items: sm.lines });
    fig('pvm_waterfall', 0.6);
    var f = rec.findings || {};
    B.push({ type: 'h3', text: 'What is working' });
    B.push(arr(f.working).length ? { type: 'bullets', items: arr(f.working).slice(0, 3).map(function (i) { return i.title; }) } : para('No segment or measure improved by enough to rank in ' + L1 + '.'));
    B.push({ type: 'h3', text: 'What needs to change' });
    B.push(arr(f.change).length ? { type: 'bullets', items: arr(f.change).slice(0, 3).map(function (i) { return i.title; }) } : para('Nothing moved the wrong way by enough to rank in ' + L1 + '.'));
    B.push({ type: 'note', text: 'Every figure was computed by the NorthLedger engine from the deals in the export, in your browser; no figure rests on fewer than 5 deals. Causes the data does not show are marked “driver to be confirmed”. Details and definitions follow.' });
    B.push({ type: 'pagebreak' });
    // ---- 1. revenue
    var g = rec.growth || {};
    B.push({ type: 'h1', text: '1. Won revenue: more deals or bigger deals?' });
    B.push(para((g.text || '') + (charts.pvm_waterfall ? ' Figure 1 shows the split.' : '')));
    // ---- 2. segments
    var sg = rec.segments || {};
    B.push({ type: 'h1', text: '2. By segment' });
    if (sg.ok) {
      B.push({ type: 'table', title: 'Won revenue, deals won and the average won deal by ' + str(sg.column), cols: sg.table.cols, rows: sg.table.rows, widths: [1.5, 1.3, 1.3, 1.1, 0.8, 0.8, 1.1, 1.1], note: sg.note || '' });
      fig('grouped_bar:rev');
      if (sg.win_table) B.push({ type: 'table', title: 'Win rate by ' + str(sg.column), cols: sg.win_table.cols, rows: sg.win_table.rows, widths: [1.5, 1, 1.1, 1, 1.1, 1.1], note: 'Win rate = won / (won + lost) of the deals closed in the quarter; open deals are not in it.' });
      fig('grouped_bar:win');
    } else B.push(para(sg.why || ''));
    // ---- 3. cycle
    var cy = rec.cycle || {};
    B.push({ type: 'h1', text: '3. Sales cycle' });
    if (cy.ok) {
      B.push(para(cy.text || ''));
      if (arr(cy.segments).length) B.push({ type: 'table', title: 'Median days from created to close, won deals', cols: ['Segment', 'Median ' + L0, 'Median ' + L1], rows: cy.segments.map(function (x) { return [x.label, x.texts.prev, x.texts.cur]; }) });
      fig('grouped_bar:cyc');
    } else B.push(para(cy.why || ''));
    // ---- 4. pipeline
    B.push({ type: 'h1', text: '4. Pipeline on ' + str(pl.as_of || rec.as_of) });
    if (pl.ok) {
      B.push(para(pl.text || ''));
      if (pl.table) B.push({ type: 'table', title: 'Open deals by stage', cols: pl.table.cols, rows: pl.table.rows, note: pl.weighted !== null && pl.weighted !== undefined ? 'Weighted value = amount x probability.' : '' });
      fig('bar_chart');
    } else B.push(para(pl.why || ''));
    // ---- 5. reps
    var rp = rec.reps || {};
    B.push({ type: 'h1', text: '5. Top reps in ' + L1 });
    if (rp.ok) {
      B.push({ type: 'table', title: 'Top 3 reps by won revenue', cols: rp.table.cols, rows: rp.table.rows, note: [rp.note, rp.small_note].filter(Boolean).join(' ') });
    } else B.push(para(rp.why || ''));
    // ---- 6. findings in full
    B.push({ type: 'h1', text: '6. What is working and what needs to change' });
    B.push({ type: 'h2', text: 'Working' });
    B.push(arr(f.working).length ? { type: 'numbered', items: f.working.map(function (i) { return i.text; }) } : para('Nothing to rank.'));
    B.push({ type: 'h2', text: 'Needs to change' });
    B.push(arr(f.change).length ? { type: 'numbered', items: f.change.map(function (i) { return i.text; }) } : para('Nothing to rank.'));
    B.push({ type: 'note', text: 'Ranked by the size of the move: a revenue change as a share of ' + L0 + '\'s won revenue, a win rate in points, a cycle or the stalled share of the pipeline in proportion. The split into deal count and deal size is the data\'s; any other cause is a driver to be confirmed with the team.' });
    // ---- about
    B.push({ type: 'h1', text: 'How this report was made' });
    B.push({ type: 'table', title: 'The columns the report read', cols: ['Needed', 'Column in the export', 'Read by'],
      rows: arr(rec.mapping).map(function (m) { return [m.label + (m.optional ? '' : ' (required)'), m.column || 'none found', HOWW[m.how] || '']; }) });
    if (arr(rec.cannot).length) B.push({ type: 'bullets', items: rec.cannot.map(function (c) { return c.text; }) });
    B.push({ type: 'bullets', items: arr(rec.notes) });
    return { title: 'Quarterly sales report: ' + L1, subject: 'Won revenue, segments, cycle, pipeline and reps, ' + L1 + ' against ' + L0,
      keywords: 'sales report, CRM, ' + L1, style: 'business', paper: opts.paper || 'letter', header: 'Quarterly sales report ' + DASH + ' ' + L1,
      created: opts.created instanceof Date ? opts.created : new Date(), blocks: B };
  };
  var DASH = '–';
  var HOWW = { visitor: 'your choice', plan: 'the AI plan\'s reading', name: 'its name', values: 'its values' };
  /* ---------------------------------------------------------------- the preview (HTML), from the same model */
  NLSales.html = function (doc, draw) {
    var h = '', fn = 0, list = 0;
    arr(doc.blocks).forEach(function (b) {
      var t = b.type;
      if (t === 'title') h += '<h4 class="sr-title">' + esc(b.text) + '</h4>';
      else if (t === 'subtitle') h += '<p class="sr-sub">' + esc(b.text) + '</p>';
      else if (t === 'h1') h += '<h5 class="sr-h1">' + esc(b.text) + '</h5>';
      else if (t === 'h2' || t === 'h3') h += '<h6 class="sr-h' + t.charAt(1) + '">' + esc(b.text) + '</h6>';
      else if (t === 'p') h += '<p class="sr-p">' + (Array.isArray(b.text) ? b.text.map(function (r) { return r.b ? '<strong>' + esc(r.t) + '</strong>' : esc(r.t); }).join('') : esc(b.text)) + '</p>';
      else if (t === 'note') h += '<p class="note sr-note">' + esc(b.text) + '</p>';
      else if (t === 'bullets' || t === 'numbered') { list += 1; h += '<' + (t === 'bullets' ? 'ul' : 'ol') + ' class="sr-list">' + arr(b.items).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</' + (t === 'bullets' ? 'ul' : 'ol') + '>'; }
      else if (t === 'kpis') h += '<ul class="sr-kpis">' + arr(b.items).map(function (k) { return '<li><span class="k">' + esc(k.label) + '</span><b>' + esc(k.value) + '</b>' + (k.sub ? '<span class="s">' + esc(k.sub) + '</span>' : '') + '</li>'; }).join('') + '</ul>';
      else if (t === 'table') {
        h += (b.title ? '<p class="sr-ft">' + esc(b.title) + '</p>' : '') + '<div class="tscroll sr-tbl" tabindex="0" role="region" aria-label="' + esc(b.title || 'Table') + '"><table class="dt"><thead><tr>' +
          arr(b.cols).map(function (c) { return '<th scope="col">' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
          arr(b.rows).map(function (r) { return '<tr>' + r.map(function (c, i) { return i ? '<td class="' + (/^[−+\-<$€£]?[\d.,]/.test(str(c)) ? 'num' : '') + '">' + esc(c) + '</td>' : '<th scope="row">' + esc(c) + '</th>'; }).join('') + '</tr>'; }).join('') +
          '</tbody></table></div>' + (b.note ? '<p class="note sr-cap">' + esc(b.note) + '</p>' : '');
      } else if (t === 'image') {
        fn += 1;
        var svg = '';
        try { svg = draw ? draw(b.chart) : ''; } catch (e) { svg = ''; }
        h += '<figure class="sr-fig viz" data-chart="' + esc(b.chart && b.chart.id) + '"><figcaption class="sr-ft"><b>Figure ' + fn + '.</b> ' + esc(b.title) + '</figcaption>' +
          (svg || '<p class="note">This chart could not be drawn here; its figures are in the text and tables.</p>') + '<p class="note sr-cap">' + esc(b.caption) + '</p></figure>';
      } else if (t === 'pagebreak') h += '<hr class="sr-page" aria-hidden="true">';
    });
    return h;
  };

  // a self-contained HTML file of the document (serif, dense, print-ready: "Save as PDF" from the print dialog keeps it)
  NLSales.htmlFile = function (doc, pngs) {
    var b64 = function (u8) {
      if (typeof btoa === 'undefined') return Buffer.from(u8).toString('base64');
      var s = '', CH = 0x8000;
      for (var i = 0; i < u8.length; i += CH) s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
      return btoa(s);
    };
    var k = 0;
    var body = NLSales.html(doc, function () {
      var p = pngs && pngs[k++];
      return p && p.png ? '<img alt="" src="data:image/png;base64,' + b64(p.png) + '" width="' + Math.round(p.w / 2) + '" height="' + Math.round(p.h / 2) + '">' : '';
    });
    var paper = doc.paper === 'a4' ? 'A4' : 'letter';
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + esc(doc.title) + '</title><style>' +
      '@page{size:' + paper + ';margin:0.75in}body{font:10pt/1.25 Georgia,"Times New Roman",serif;color:#222;max-width:7in;margin:24px auto;padding:0 16px;background:#fff}' +
      '.sr-title{font-size:20pt;margin:0 0 2pt;color:#111}.sr-sub{font-style:italic;color:#5a5a5a;margin:0 0 8pt}.sr-h1{font-size:13pt;margin:12pt 0 4pt;padding-bottom:2pt;border-bottom:1px solid #111;color:#111}' +
      '.sr-h2{font-size:11pt;color:#1f3a5f;margin:8pt 0 2pt}.sr-h3{font-size:10pt;font-variant:small-caps;letter-spacing:.04em;margin:8pt 0 2pt}.sr-p{margin:0 0 4pt}.note{font-size:8pt;color:#5a5a5a;margin:2pt 0 6pt}' +
      '.sr-list{margin:0 0 4pt;padding-left:18pt}.sr-list li{margin:0 0 2pt}.sr-kpis{list-style:none;display:grid;grid-template-columns:repeat(4,1fr);gap:3pt;margin:0 0 6pt;padding:0}' +
      '.sr-kpis li{background:#f2f2f2;padding:4pt 6pt}.sr-kpis .k,.sr-kpis .s{display:block;font-size:8pt;color:#5a5a5a}.sr-kpis b{font-size:13pt;color:#111}' +
      'table{border-collapse:collapse;width:100%;font-size:8pt;margin:0 0 2pt}th,td{padding:2pt 4pt;border-bottom:.5pt solid #bfbfbf;text-align:left}thead th{background:#e9edf2;border-top:1pt solid #111;border-bottom:1pt solid #111}' +
      'td.num{text-align:right;font-variant-numeric:tabular-nums}.sr-ft{font-weight:bold;font-size:9pt;margin:8pt 0 2pt}figure{margin:6pt 0;break-inside:avoid}figure img{display:block;margin:0 auto;max-width:86%;height:auto}' +
      '.sr-page{border:0;break-after:page;margin:0}@media screen{.sr-page{border-top:1px dashed #bfbfbf;margin:16pt 0}}.print-note{font:9pt sans-serif;background:#fff8e1;padding:6pt 8pt;border:1px solid #e0c96f}@media print{.print-note{display:none}}' +
      '</style></head><body><p class="print-note">Made by NorthLedger in your browser. To keep it as a PDF: File &gt; Print &gt; Save as PDF.</p>' + body + '</body></html>';
  };

  if (typeof module === 'object' && module && module.exports) { module.exports = NLSales; return; }
  root.NLSales = NLSales;

  /* ================================================================ browser only: the step-6 option */
  var doc = document;
  function $(id) { return doc.getElementById(id); }
  var state = { sha: '', rec: null, settings: {}, busy: false };

  // the minimal registry (Track E owns the real one; this one is made only when none is on the page)
  function registry() {
    if (root.NLDeliver) return root.NLDeliver;
    var opts = [], picked = '', last = null;
    var D = { _shim: true };
    D.register = function (o) {
      if (!isObj(o) || !o.id) return;
      opts = opts.filter(function (x) { return x.id !== o.id; }).concat([o]);
      drawPick(); if (last) drawMenu(last);
    };
    D.list = function () { return opts.slice(); };
    D.choose = D.pick = function (id) { picked = id || ''; drawPick(); if (last) drawMenu(last); };
    D.chosen = function () { return picked; };
    D.get = function (id) { return opts.filter(function (x) { return x.id === id; })[0] || null; };
    function drawPick() {
      var slot = $('try-step-1-slot');
      if (!slot) return;
      var box = $('try-need');
      if (!box) { box = doc.createElement('div'); box.id = 'try-need'; box.className = 'try-need'; slot.insertBefore(box, slot.firstChild); }
      var o = opts.filter(function (x) { return x.id === picked; })[0];
      box.innerHTML = '<label class="try-need-l" for="try-need-sel"><strong>What do you need?</strong></label> <select id="try-need-sel"><option value="">An analysis of my file (the default)</option>' +
        opts.map(function (x) { return '<option value="' + esc(x.id) + '"' + (x.id === picked ? ' selected' : '') + '>' + esc(x.title) + '</option>'; }).join('') + '</select>' +
        (o ? '<div class="try-need-list"><p class="note">' + esc(o.blurb || '') + ' What to export, and the columns it reads (the engine finds them by their names; you can change any in step 6):</p><ul>' +
          arr(o.needs).map(function (n) { return '<li>' + esc(n.what) + (n.optional ? ' <span class="note">(optional)</span>' : '') + '</li>'; }).join('') + '</ul>' + (o.how ? o.how : '') + '</div>' : '');
      var sel = $('try-need-sel');
      if (sel) sel.addEventListener('change', function () { D.pick(sel.value); });
    }
    function drawMenu(rep) {
      last = rep;
      var slot = $('try-step-6-slot');
      if (!slot || !rep || !rep.ok) return;
      var box = $('try-report-types');
      if (!box) { box = doc.createElement('div'); box.id = 'try-report-types'; box.className = 'try-types'; box.setAttribute('role', 'region'); box.setAttribute('aria-label', 'Report types'); slot.appendChild(box); }
      var fit = opts.map(function (o) { var r = { ok: false, why: '' }; try { r = o.fits(rep) || r; } catch (e) { r = { ok: false, why: 'could not be checked' }; } return r; });
      var cur = picked && opts.some(function (o) { return o.id === picked; }) ? picked : '';
      box.innerHTML = '<h4 class="try-deliver-h">Report types</h4><ul class="try-types-l">' + opts.map(function (o, i) {
        return '<li><button type="button" class="btn btn-ghost btn-sm" data-type="' + esc(o.id) + '" aria-pressed="' + (o.id === cur) + '">' + esc(o.title) + '</button> <span class="try-types-st ' + (fit[i].ok ? 'ok' : 'no') + '">' +
          (fit[i].ok ? 'ready' : 'needs: ' + esc(fit[i].why)) + '</span><span class="note"> ' + esc(o.blurb || '') + '</span></li>';
      }).join('') + '</ul><div class="try-types-card" id="try-type-card"></div>';
      Array.prototype.forEach.call(box.querySelectorAll('[data-type]'), function (b) {
        b.addEventListener('click', function () { picked = b.getAttribute('data-type'); drawMenu(rep); });
      });
      var o = opts.filter(function (x) { return x.id === cur; })[0];
      if (o) { try { o.render($('try-type-card'), rep, {}); } catch (e) { $('try-type-card').textContent = 'This report could not be drawn: ' + str(e && e.message); } }
    }
    D._drawMenu = drawMenu;
    doc.addEventListener('nl:report', function (e) { drawMenu(e.detail && e.detail.report); });
    root.NLDeliver = D;
    return D;
  }

  /* ---------------------------------------------------------------- the card */
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var CURS = ['', '$', '€', '£', 'USD', 'CAD', 'EUR', 'GBP', 'AUD'];
  function fits(rep) {
    var s = rep && rep.sales;
    if (!isObj(s)) return { ok: false, why: 'deal ID, amount, stage or won/lost, close date (run the file again)' };
    if (s.fit && s.fit.ok) return { ok: true, why: str(s.fit.why) };
    return { ok: false, why: arr(s.fit && s.fit.missing).join(', ') || str(s.why) };
  }
  function draw(rec) {
    var V = root.NLV;
    if (!V) return '';
    var o = V.draw(rec, Math.min(680, Math.max(300, (($('try-sales') || {}).clientWidth || 680) - 40)));
    return o ? (o.svg || o.html || '') : '';
  }
  function render(slot, rep, settings) {
    if (!slot) return;
    var sha = (rep && rep.input && rep.input.sha256) || '';
    if (sha !== state.sha) { state.sha = sha; state.rec = rep && rep.sales; state.settings = {}; state.asked = ''; }
    // Track E's registry calls render again with the visitor's settings (its own form): ask the engine once per change
    var own = !root.NLDeliver || root.NLDeliver._shim, key = JSON.stringify(settings || {});
    if (!own && settings && Object.keys(settings).length && key !== state.asked && !state.busy) {
      state.asked = key;
      ask(slot, rep, settings);
    }
    var rec = state.rec || {};
    var h = '<section class="sr-card" id="try-sales" aria-labelledby="try-sales-h"><h4 class="sr-card-h" id="try-sales-h">Quarterly sales report</h4>';
    if (!rec.ok) {
      h += '<p class="sr-status" id="try-sales-status" role="status">' + esc(rec.why || (rec.fit && rec.fit.why) || 'This file does not read as a deals export.') + '</p>';
    } else {
      h += '<p class="sr-status" id="try-sales-status" role="status">' + esc(rec.quarter.label) + ' against ' + esc(rec.quarter.prev_label) + ', from the deals the engine kept; every figure is the engine\'s. Nothing is sent anywhere.</p>';
    }
    h += colsHtml(rec) + (!root.NLDeliver || root.NLDeliver._shim ? setHtml(rec) : '');
    if (rec.ok) {
      h += '<div class="ai-rep-actions sr-actions"><button type="button" class="btn btn-primary" data-sales-dl="docx">Download Word (.docx)</button>' +
        '<button type="button" class="btn btn-ghost" data-sales-dl="html">Download HTML (print to PDF)</button><span class="try-share-out" id="try-sales-out" hidden></span></div>' +
        '<p class="note">The Word file is in a business style (a serif type, dense, the charts as pictures in the text column): a one-page executive summary first, then the sections. The HTML file is the same document; print it and choose “Save as PDF” for a PDF.</p>' +
        '<div class="sr-preview" id="try-sales-preview" aria-label="Preview of the report">' + NLSales.html(NLSales.doc(rec, { paper: paperOf() }), draw) + '</div>';
    }
    h += '</section>';
    slot.innerHTML = h;
    bind(slot, rep);
  }
  function colsHtml(rec) {
    var found = arr(rec.mapping).filter(function (m) { return m.column; }).length, cols = arr(rec.columns);
    var h = '<details class="sr-cols" id="try-sales-cols"' + (rec.ok ? '' : ' open') + '><summary>The columns the report reads (' + found + ' of ' + arr(rec.mapping).length + ' found)</summary>' +
      '<p class="note">The engine reads a CRM\'s own names (Salesforce, HubSpot, Pipedrive, Zoho) by their words and values' + (arr(rec.mapping).some(function (m) { return m.how === 'plan'; }) ? ', and the AI plan\'s reading of a column' : '') + '. Choose another column, or none, and update the report.</p><div class="cf-fields sr-map">';
    arr(rec.mapping).forEach(function (m) {
      var id = 'sr-col-' + m.role;
      h += '<label for="' + id + '">' + esc(m.label) + (m.optional ? '' : ' *') + '</label><select id="' + id + '" data-role="' + esc(m.role) + '"><option value="">none</option>' +
        cols.map(function (c) { return '<option value="' + esc(c.header) + '"' + (c.header === m.column ? ' selected' : '') + '>' + esc(c.header) + '</option>'; }).join('') + '</select>' +
        (m.column ? '<p class="cf-hint">' + (m.from_stage ? 'won or lost read from the stage\'s values' : 'read by ' + esc(HOWW[m.how] || 'the engine')) + '</p>' : '');
    });
    h += '</div>' + (arr(rec.cannot).length ? '<ul class="sr-cannot">' + rec.cannot.map(function (c) { return '<li>' + esc(c.text) + '</li>'; }).join('') + '</ul>' : '') + '</details>';
    return h;
  }
  function setHtml(rec) {
    var s = rec.settings || {}, qs = arr(rec.quarters), kept = rec.owner_decision === 'keep';
    var sel = function (id, label, opts, val) {
      return '<label for="' + id + '">' + label + '</label><select id="' + id + '">' + opts.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(val) ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</select>';
    };
    return '<fieldset class="sr-set"><legend>Settings</legend><div class="cf-fields">' +
      sel('sr-fs', 'Fiscal year starts', MONTHS.map(function (m, i) { return [i + 1, m + (i === 0 ? ' (calendar quarters)' : '')]; }), s.fiscal_start || 1) +
      sel('sr-q', 'Quarter', [['', 'The latest complete quarter']].concat(qs.map(function (q) { return [q.label, q.label + (q.complete ? '' : ' (not over)')]; })), (rec.chosen && rec.chosen.quarter) || state.settings.quarter || '') +
      '<label for="sr-target">Target for the next quarter</label><input id="sr-target" inputmode="decimal" autocomplete="off" placeholder="optional, e.g. 1,000,000" value="' + esc(state.settings.target || '') + '">' +
      '<label for="sr-stall">Stalled after (days without a stage change)</label><input id="sr-stall" type="number" min="7" max="365" value="' + esc(s.stall_days || 30) + '">' +
      sel('sr-cur', 'Currency', CURS.map(function (c) { return [c, c || 'none (plain numbers)']; }), s.currency || '') +
      '<label for="sr-names">Rep names</label><span class="sr-names"><input type="checkbox" id="sr-names"' + (kept && s.rep_names !== false ? ' checked' : '') + (kept ? '' : ' disabled') + '> ' +
        (kept ? 'show the names (you kept the owner column)' : 'shown as Rep A, B, C: names only when you keep the owner column at the personal-data step') + '</span>' +
      '</div><div class="ai-rep-actions"><button type="button" class="btn btn-ghost" data-sales-act="update">Update the report</button></div></fieldset>';
  }
  function paperOf() {
    try { var l = [navigator.language].concat(navigator.languages || []); return root.NLReportPdf ? root.NLReportPdf.paperFor(l) : 'letter'; } catch (e) { return 'letter'; }
  }
  function collect(slot) {
    var v = function (id) { var e = $(id); return e ? e.value : ''; };
    var cols = {};
    Array.prototype.forEach.call(slot.querySelectorAll('select[data-role]'), function (s) {
      var role = s.getAttribute('data-role'), was = (arr((state.rec || {}).mapping).filter(function (m) { return m.role === role; })[0] || {}).column || '';
      if (s.value !== was || (state.settings.columns && role in state.settings.columns)) cols[role] = s.value;
    });
    var nm = $('sr-names');
    return { fiscal_start: +v('sr-fs') || 1, quarter: v('sr-q'), target: v('sr-target'), stall_days: +v('sr-stall') || 30, currency: v('sr-cur'),
      rep_names: nm ? (nm.disabled ? true : nm.checked) : true, columns: cols };
  }
  // the report again with these settings, from the run's own worker (no AI call, nothing sent)
  function ask(slot, rep, set) {
    var st = $('try-sales-status');
    state.busy = true;
    if (st) st.textContent = 'Updating the report in this browser\u2026';
    var T = root.NLTry;
    if (!T || !T.salesReport) { state.busy = false; if (st) st.textContent = 'The engine is not on this page any more: run the file again.'; return; }
    T.salesReport(set, function (out) {
      state.busy = false;
      if (!out) { var s2 = $('try-sales-status'); if (s2) s2.textContent = 'The engine no longer holds this file: run it again.'; return; }
      state.rec = out; state.settings = set; out.chosen = { quarter: set.quarter };
      render(slot, rep, set);
      var s3 = $('try-sales-status');
      if (s3) s3.textContent = (out.ok ? out.quarter.label + ' against ' + out.quarter.prev_label + ', with your settings. ' : str(out.why) + ' ') + 'No AI was asked and nothing was sent.';
    });
  }
  function bind(slot, rep) {
    var up = slot.querySelector('[data-sales-act="update"]');
    if (up) up.addEventListener('click', function () { if (!state.busy) ask(slot, rep, collect(slot)); });
    Array.prototype.forEach.call(slot.querySelectorAll('[data-sales-dl]'), function (b) {
      b.addEventListener('click', function () { download(b.getAttribute('data-sales-dl')); });
    });
  }
  // the charts as PNG (NLDocx.svgToPng: the page's own drawing, light theme, the document's serif), in document order
  function figures(d) {
    var imgs = arr(d.blocks).filter(function (b) { return b.type === 'image'; });
    var D = root.NLDocx, host = $('try-step-6') || doc.body;
    return Promise.all(imgs.map(function (b) {
      var svg = '';
      try { var o = root.NLV ? root.NLV.draw(b.chart, 680) : null; svg = o && o.svg ? o.svg : ''; } catch (e) { svg = ''; }
      if (!svg || !D) return Promise.resolve({ png: null });
      return D.svgToPng(svg, { host: host, hostClass: 'viz', width: 680, font: 'Georgia, serif' }).then(function (r) { return r; }, function () { return { png: null }; });
    })).then(function (pngs) {
      imgs.forEach(function (b, i) { b.png = pngs[i].png; b.w = pngs[i].w; b.h = pngs[i].h; });
      return pngs;
    });
  }
  function download(kind) {
    var rec = state.rec, out = $('try-sales-out');
    if (!rec || !rec.ok || !out || state.busy) return;
    state.busy = true;
    out.hidden = false; out.textContent = 'Making the ' + (kind === 'docx' ? 'Word file' : 'HTML file') + ' in this browser…';
    var d = NLSales.doc(rec, { paper: paperOf(), created: new Date() });
    var day = new Date().toISOString().slice(0, 10), base = 'Sales report ' + rec.quarter.label + ' - ' + day;
    figures(d).then(function (pngs) {
      var bytes, name, type;
      if (kind === 'docx') { bytes = root.NLDocx.document(d); name = base + '.docx'; type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'; }
      else { bytes = new TextEncoder().encode(NLSales.htmlFile(d, pngs)); name = base + '.html'; type = 'text/html'; }
      offer(out, bytes, name, type);
    }, function (e) { out.textContent = 'The file could not be made in this browser (' + str(e && e.message ? e.message : e) + ').'; })
      .then(function () { state.busy = false; });
  }
  function offer(out, bytes, name, type) {
    var url = URL.createObjectURL(new Blob([bytes], { type: type }));
    var a = doc.createElement('a');
    a.href = url; a.download = name; a.textContent = name;
    out.textContent = '';
    var dl = doc.createElement('a'); dl.href = url; dl.download = name; dl.hidden = true;
    doc.body.appendChild(dl); dl.click(); dl.remove();
    var said = doc.createElement('span');
    said.textContent = 'Saved as “' + name + '” (' + Math.max(1, Math.round(bytes.length / 1024)) + ' KB), made in this browser; nothing was sent. If no download started: ';
    out.appendChild(said); out.appendChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
  }

  var howHtml = '<details class="sr-how"><summary>How to export the deals from your CRM</summary><dl>' +
    NLSales.HOW.map(function (x) { return '<dt>' + esc(x[0]) + '</dt><dd>' + esc(x[1]) + '</dd>'; }).join('') +
    '</dl><p class="note">Export every deal of the two quarters you want to compare, won and lost, and the deals open today. A CSV or an Excel file works; it stays in your browser.</p></details>';
  NLSales.option = { id: 'sales-report', order: 20, roles: ['deals'], title: 'Quarterly sales report', blurb: 'The quarter against the one before from a CRM deals export: revenue, deals and deal size, segments, cycle, pipeline, top reps.',
    needs: NLSales.needs(), how: howHtml, fits: fits,
    settings: [{ key: 'fiscal_start', label: 'Fiscal year starts (month)', kind: 'choice', default: 1, options: MONTHS.map(function (m, i) { return { value: i + 1, label: m }; }) },
      { key: 'quarter', label: 'Quarter (blank: the latest complete one)', kind: 'text', default: '', help: 'e.g. 2025 Q4, or FY2026 Q3 with a fiscal year' },
      { key: 'target', label: 'Target for the next quarter', kind: 'number', default: '', min: 0, help: 'optional: the pipeline\'s coverage of it' },
      { key: 'stall_days', label: 'Stalled after (days without a stage change)', kind: 'number', default: 30, min: 7, max: 365, step: 1 },
      { key: 'currency', label: 'Currency', kind: 'choice', default: '', options: CURS.map(function (c) { return { value: c, label: c || 'none (plain numbers)' }; }) },
      { key: 'columns', label: 'The columns the report reads', kind: 'columns', default: {} }],
    render: render };
  // Track E's registry when it is there; else the queue it reads on boot; else, once the page is loaded, the minimal one
  if (root.NLDeliver && root.NLDeliver.register) root.NLDeliver.register(NLSales.option);
  else {
    (root.NLDeliverQueue = root.NLDeliverQueue || []).push(NLSales.option);
    var boot = function () {
      if (root.NLDeliver && root.NLDeliver.list && root.NLDeliver.list().some(function (o) { return o.id === 'sales-report'; })) return;
      var D = registry();
      arr(root.NLDeliverQueue).forEach(function (o) { D.register(o); });
      root.NLDeliverQueue = [];
    };
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot); else setTimeout(boot, 0);
  }
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this);
