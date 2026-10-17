/* The Dashboard tab (plan/DASHBOARD-DESIGN.md Track A; engine/CONTRACT-v2.md 5.14): the third tab beside the Manager and
   Analyst views, built from the same run. The engine (engine/nl_dashboard.py) checked the design (the AI's, the sample's
   recorded one, or its own) and exported an aggregate cube: counts and sums by month and by the dashboard's columns.
   This file recomputes every tile and chart from that cube under the visitor's filters, by the engine's rules
   (nl_dashboard.query_kpi / query_series): flows added up, levels averaged and never added up over time, a figure on
   fewer than 5 rows shown as "<5" after every filter, a period with no rows left as a gap (never filled).
   It draws nothing itself: every chart is a chart-registry record (tools/fixtures/viz/spec.json, the Batch 2 kinds
   lines, area, hbar, multiples) drawn by window.NLV (55-nl-viz.js, taught those kinds by 51-nl2-charts.js), inside the
   site's figure.visual with its Table button (00-core.js U.visual); a chart the filters cannot recompute (a heatmap, a
   waterfall) is the engine's own record, drawn the same way and marked "not filtered". The tiles are the report's
   KPI tiles (.tr-kpis / .tr-kpi). Exports: one self-contained HTML file (this file, 00-core, 51 and 55 inlined, the
   cube and the page's own styles: filters work offline) and an .xlsx written here (Data, Notes and a Charts sheet of
   native Excel charts). Nothing leaves the browser. */
(function () {
  'use strict';
  var U = window.NLU || {};
  var esc = U.esc || function (s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var SMALL = 5;
  var PMAX = 30;                       // periods on a time chart (spec POINTS_MAX): a longer span is shown by quarter or year
  var OWN = Object.prototype.hasOwnProperty;
  var NLD = {};
  window.NLD = NLD;

  /* ------------------------------------------------------------ periods */
  function monthWord(ym) { return ym ? MON[+ym.slice(5, 7) - 1] + ' ' + ym.slice(0, 4) : ''; }
  function shiftM(ym, k) { var y = +ym.slice(0, 4), m = +ym.slice(5, 7) - 1 + k; y += Math.floor(m / 12); m = ((m % 12) + 12) % 12; return y + '-' + (m < 9 ? '0' : '') + (m + 1); }
  function mrange(a, b) { var out = []; for (var m = a; m <= b && out.length < 2000; m = shiftM(m, 1)) out.push(m); return out; }
  function periodOf(ym, grain) { return grain === 'month' ? ym : grain === 'quarter' ? ym.slice(0, 4) + '-Q' + (Math.floor((+ym.slice(5, 7) - 1) / 3) + 1) : ym.slice(0, 4); }
  function periodWord(p) { return /^\d{4}-\d{2}$/.test(p) ? monthWord(p) : /^\d{4}-Q\d$/.test(p) ? p.slice(5) + ' ' + p.slice(0, 4) : p; }
  NLD.grainOf = function (nMonths) { return nMonths <= PMAX ? 'month' : Math.ceil(nMonths / 3) <= PMAX ? 'quarter' : 'year'; };

  /* ------------------------------------------------------------ numbers (the only formatting: a recomputed figure) */
  function fmt(v, unit) {
    if (v === null || v === undefined || !isFinite(v)) return '';
    var a = Math.abs(v), dp = a >= 1000 ? 0 : a >= 10 ? 1 : 2;
    var s = Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: dp, minimumFractionDigits: 0 });
    var neg = v < 0 ? '−' : '';
    unit = unit || '';
    if (/^[$€£¥]$/.test(unit)) return neg + unit + s;
    if (unit === '%') return neg + s + '%';
    return neg + s + (unit ? ' ' + unit : '');
  }
  NLD.fmt = fmt;

  /* ------------------------------------------------------------ the engine's rule over the cube (nl_dashboard.query_*) */
  function dimIdx(D, key) { for (var i = 0; i < D.cube.dims.length; i++) if (D.cube.dims[i].key === key) return i; return -1; }
  function measIdx(D, key) { for (var i = 0; i < D.cube.measures.length; i++) if (D.cube.measures[i].key === key) return i; return -1; }
  function cellOk(D, c, F, where) {
    var months = D.cube.months, full = (!F.from || F.from === months[0]) && (!F.to || F.to === months[months.length - 1]);
    if (c[0] < 0) { if (!full) return false; }
    else { var m = months[c[0]]; if (F.from && m < F.from) return false; if (F.to && m > F.to) return false; }
    var cats = F.cats || {};
    for (var k in cats) {
      if (!OWN.call(cats, k) || !cats[k] || !cats[k].length) continue;
      var j = dimIdx(D, k);
      if (j >= 0 && cats[k].indexOf(D.cube.dims[j].levels[c[1 + j]]) < 0) return false;
    }
    if (where) {
      var w = dimIdx(D, where.key), hit = where.values.indexOf(D.cube.dims[w].levels[c[1 + w]]) >= 0;
      if (hit === !!where.exclude) return false;
    }
    return true;
  }
  function pick(D, F, where) { return D.cube.cells.filter(function (c) { return cellOk(D, c, F, where); }); }
  // {v, n}: a figure over some cells; v is null when it rests on fewer than 5 rows (or none)
  function agg(D, cells, mkey, how) {
    var nd = D.cube.dims.length, rows = 0, s = 0, k = 0, j = mkey ? measIdx(D, mkey) : -1;
    cells.forEach(function (c) { rows += c[1 + nd]; if (j >= 0) { s += c[2 + nd + 2 * j]; k += c[3 + nd + 2 * j]; } });
    if (j < 0 || how === 'count') return { v: rows >= SMALL ? rows : null, n: rows };
    if (k < SMALL) return { v: null, n: k };
    return { v: how === 'average' ? s / k : s, n: k };
  }
  NLD.kpi = function (D, K, F) {
    var cells = pick(D, F || {}, K.where), month = null, how = K.agg;
    if (how === 'latest') {
      var last = -1; cells.forEach(function (c) { if (c[0] > last) last = c[0]; });
      cells = last < 0 ? [] : cells.filter(function (c) { return c[0] === last; });
      month = last < 0 ? null : D.cube.months[last];
      var mj = measIdx(D, K.mkey);
      how = mj >= 0 && !D.cube.measures[mj].flow ? 'average' : 'sum';
    }
    var r = agg(D, cells, K.agg === 'count' ? null : K.mkey, K.agg === 'count' ? 'count' : how);
    return { value: r.v, n: r.n, suppressed: r.n > 0 && r.n < SMALL, month: month };
  };
  // the filtered span's months (the data's own months inside the date range), and the grain it is shown at
  function span(D, cells) {
    var lo = Infinity, hi = -1;
    cells.forEach(function (c) { if (c[0] >= 0) { if (c[0] < lo) lo = c[0]; if (c[0] > hi) hi = c[0]; } });
    if (hi < 0) return null;
    var ms = mrange(D.cube.months[lo], D.cube.months[hi]), g = NLD.grainOf(ms.length), ps = [];
    ms.forEach(function (m) { var p = periodOf(m, g); if (ps.indexOf(p) < 0) ps.push(p); });
    return { months: ms, grain: g, periods: ps };
  }
  // each period's cells (month index -> period)
  function byPeriod(D, cells, sp) {
    var out = {}; sp.periods.forEach(function (p) { out[p] = []; });
    cells.forEach(function (c) { if (c[0] >= 0) { var p = periodOf(D.cube.months[c[0]], sp.grain); if (OWN.call(out, p)) out[p].push(c); } });
    return out;
  }
  // the levels of a dimension over some cells, largest first (by |total| for a flow, rows otherwise)
  function levelsBy(D, cells, bkey, mkey, how) {
    var j = dimIdx(D, bkey), L = D.cube.dims[j].levels, tot = L.map(function () { return 0; }), rows = L.map(function () { return 0; });
    var nd = D.cube.dims.length, mj = mkey ? measIdx(D, mkey) : -1;
    cells.forEach(function (c) { var i = c[1 + j]; rows[i] += c[1 + nd]; if (mj >= 0 && how === 'sum') tot[i] += Math.abs(c[2 + nd + 2 * mj]); });
    return L.map(function (l, i) { return { i: i, level: l, size: how === 'sum' ? tot[i] : rows[i], rows: rows[i] }; })
      .filter(function (x) { return x.rows > 0; })
      .sort(function (a, b) { return (a.level === 'other') - (b.level === 'other') || b.size - a.size || (a.level < b.level ? -1 : 1); });
  }

  /* ------------------------------------------------------------ chart-registry records from the cube */
  function unitOf(D, X) { var j = X.mkey ? measIdx(D, X.mkey) : -1; return X.unit_word || (j >= 0 ? D.cube.measures[j].unit : '') || ''; }
  function whatOf(D, X) { return X.mkey ? (X.agg === 'average' ? 'Average ' : 'Total ') + X.measure : (D.grain && D.grain.noun) || 'Rows'; }
  function textOf(v, n, unit) { return v === null ? (n > 0 ? '<5' : 'no rows') : fmt(v, unit); }
  function base(X, kind, D) {
    return { kind: kind, chart: X.chart, id: 'dash.' + X.id, title: X.title, chosen_by: D.chosen_by === 'engine' ? 'engine' : 'ai', why: X.why || '',
      section: 'other', source: 'Recomputed in this browser from the engine\'s aggregate of the rows it kept, under the filters shown' };
  }
  NLD.record = function (D, X, F) {
    var cells = pick(D, F || {}, X.where), unit = unitOf(D, X), how = X.agg, sup = 0, i;
    if (X.draw === 'bar' || X.draw === 'grouped') return barRecord(D, X, cells, unit);
    var sp = span(D, cells);
    if (!sp) return { kind: 'table', chart: X.chart, title: X.title, summary: 'No rows match the filters.', table: { cols: ['Period'], rows: [] }, empty: true };
    var P = byPeriod(D, cells, sp), x = sp.periods, xl = { month: 'Month', quarter: 'Quarter', year: 'Year' }[sp.grain];
    var gaps = x.filter(function (p) { return !P[p].length; });
    var rec = base(X, X.draw === 'line' ? 'lines' : X.draw === 'area' ? 'area' : 'multiples', D);
    var groups = [null];
    if (X.bkey) {
      var lv = levelsBy(D, cells, X.bkey, X.mkey, how);
      groups = lv.slice(0, X.draw === 'multiples' ? 12 : X.draw === 'area' ? 5 : 5).map(function (z) { return z.i; });
      if (X.draw === 'area' && lv.length > 5) groups.push('rest');
    }
    var j = X.bkey ? dimIdx(D, X.bkey) : -1, L = j >= 0 ? D.cube.dims[j].levels : [];
    var inG = function (c, g) { return g === null ? true : g === 'rest' ? groups.indexOf(c[1 + j]) < 0 : c[1 + j] === g; };
    var S, sup0;
    // a stacked area cannot leave a hole in one part: a part with a period on fewer than 5 rows is counted in "other"
    // (its rows still count; nothing is filled); what still cannot be drawn is shown as the chart's table
    for (var pass = 0; pass < 8; pass++) {
      sup0 = sup; S = parts();
      if (X.draw !== 'area') break;
      var bad = S.map(function (s, k) { return groups[k] !== 'rest' && s.values.some(function (v, i) { return v === null && P[x[i]].length; }) ? groups[k] : null; }).filter(function (g) { return g !== null; });
      if (!bad.length) break;
      groups = groups.filter(function (g) { return bad.indexOf(g) < 0 && g !== 'rest'; }).concat(['rest']);
      sup = sup0;
    }
    function parts() { return groups.map(function (g) {
      var vals = [], texts = [];
      x.forEach(function (p) {
        var cs = P[p].filter(function (c) { return inG(c, g); });
        var r = agg(D, cs, X.mkey, how);
        if (X.draw === 'area' && cs.length === 0 && P[p].length) r = { v: 0, n: 0, zero: true };   // a part with no rows: its total is 0
        if (r.v === null && r.n > 0) sup++;
        vals.push(r.v); texts.push(r.zero ? fmt(0, unit) : textOf(r.v, r.n, unit));
      });
      return { label: g === null ? whatOf(D, X) : g === 'rest' ? 'other' : L[g], values: vals, texts: texts };
    }); }
    if (X.draw === 'area') { S = S.filter(function (s) { return s.label !== 'other' || s.values.some(function (v) { return v; }); }); }
    // a quarter or year the range covers only in part (its first or last): said beside it, never scaled or filled
    var full = sp.grain === 'quarter' ? 3 : sp.grain === 'year' ? 12 : 1, held = {};
    sp.months.forEach(function (m) { var p = periodOf(m, sp.grain); held[p] = (held[p] || 0) + 1; });
    var part = x.filter(function (p) { return held[p] < full; });
    var pw = function (p) { return periodWord(p) + (held[p] < full ? ' (' + held[p] + ' of ' + full + ' months)' : ''); };
    var cols = [xl].concat(S.map(function (s) { return s.label; })), rows = x.map(function (p, k) { return [pw(p)].concat(S.map(function (s) { return s.texts[k]; })); });
    rec.data = { x: x, x_label: xl, grain: sp.grain };
    if (rec.kind === 'lines') rec.data.series = S.map(function (s) { return { label: s.label, values: s.values }; });
    else if (rec.kind === 'multiples') rec.data.panels = S.map(function (s) { return { label: s.label, values: s.values }; });
    else {
      var tot = x.map(function (p) { return agg(D, P[p], X.mkey, how); });
      rec.data.parts = S.map(function (s) { return { label: s.label, values: s.values }; });
      rec.data.total = tot.map(function (t) { return t.v; });
      cols.push('Total'); rows.forEach(function (r, k) { r.push(textOf(tot[k].v, tot[k].n, unit)); });
    }
    rec.table = { cols: cols, rows: rows };
    var sub = sp.grain !== 'month' ? 'By ' + sp.grain + ': the range holds ' + sp.months.length + ' months. ' : '';
    if (part.length) sub += part.map(function (p) { return periodWord(p) + ' holds ' + held[p] + ' of its ' + full + ' months'; }).join('; ') + ' in this range: ' + (part.length === 1 ? 'its figure is' : 'their figures are') + ' not comparable with a whole ' + sp.grain + '. ';
    if (X.draw === 'area' && X.bkey && S.some(function (s) { return s.label === 'other'; })) sub += 'Groups with a ' + sp.grain + ' on fewer than 5 rows are counted in \u201cother\u201d. ';
    if (gaps.length) sub += gaps.length + ' ' + (gaps.length === 1 ? sp.grain : sp.grain + 's') + ' with no rows ' + (gaps.length === 1 ? 'is' : 'are') + ' left as ' + (gaps.length === 1 ? 'a gap' : 'gaps') + '. ';
    if (sup) { sub += 'A point on fewer than 5 rows is not drawn (“<5” in the table).'; rec.suppressed = { cells: sup, why: 'Points that rest on fewer than 5 rows are not drawn: the table shows “<5”.' }; }
    if (sub) rec.subtitle = sub.trim();
    rec.summary = (X.why ? X.why.replace(/\.?$/, '. ') : '') + periodWord(x[0]) + ' to ' + periodWord(x[x.length - 1]) + '.';
    return rec;
  };
  function barRecord(D, X, cells, unit) {
    var rec = base(X, 'hbar', D), lv = levelsBy(D, cells, X.bkey, X.mkey, X.agg), j = dimIdx(D, X.bkey), out = [], off = [], two = X.draw === 'grouped';
    if (two) {
      var sp = span(D, cells);
      if (!sp || sp.months.length < 24) return { kind: 'table', chart: X.chart, title: X.title, empty: true,
        summary: !sp ? 'No rows match the filters.' : 'This chart compares the latest 12 months with the 12 before: the date range needs 24 months or more.', table: { cols: [X.by || ''], rows: [] } };
      var last = sp.months[sp.months.length - 1], a0 = shiftM(last, -23), a1 = shiftM(last, -12), b0 = shiftM(last, -11);
      rec.data = { series: [monthWord(a0) + ' to ' + monthWord(a1), monthWord(b0) + ' to ' + monthWord(last)], rows: [] };
      lv.forEach(function (z) {
        var cs = cells.filter(function (c) { return c[1 + j] === z.i && c[0] >= 0; });
        var inR = function (lo, hi) { return cs.filter(function (c) { var m = D.cube.months[c[0]]; return m >= lo && m <= hi; }); };
        var p = agg(D, inR(a0, a1), X.mkey, X.agg), q = agg(D, inR(b0, last), X.mkey, X.agg);
        if (p.v === null || q.v === null) { off.push(z.level); return; }
        var ch = X.agg === 'sum' && p.v ? (q.v / p.v - 1) * 100 : null;
        out.push({ label: z.level, values: [p.v, q.v], texts: [fmt(p.v, unit), fmt(q.v, unit)], change_text: ch === null ? '' : (ch >= 0 ? '+' : '−') + Math.abs(ch).toFixed(1) + '%' });
      });
      rec.table = { cols: [X.by, rec.data.series[0], rec.data.series[1], 'Change'], rows: out.map(function (r) { return [r.label, r.texts[0], r.texts[1], r.change_text]; }).concat(off.map(function (l) { return [l, '<5', '<5', '']; })) };
    } else {
      rec.data = { series: [whatOf(D, X)], rows: [] };
      lv.forEach(function (z) {
        var r = agg(D, cells.filter(function (c) { return c[1 + j] === z.i; }), X.mkey, X.agg);
        if (r.v === null) { off.push(z.level); return; }
        out.push({ label: z.level, values: [r.v], texts: [fmt(r.v, unit)] });
      });
      out.sort(function (a, b) { return (a.label === 'other') - (b.label === 'other') || b.values[0] - a.values[0]; });
      rec.table = { cols: [X.by, whatOf(D, X)], rows: out.map(function (r) { return [r.label, r.texts[0]]; }).concat(off.map(function (l) { return [l, '<5']; })) };
    }
    rec.data.rows = out.slice(0, 12);
    rec.data.other = !!out.length && out[out.length - 1].label === 'other';
    rec.levels = out.map(function (r) { return r.label; });
    if (off.length) { rec.subtitle = 'Not drawn, on fewer than 5 rows: ' + off.join(', ') + '.'; rec.suppressed = { cells: off.length, why: 'Groups on fewer than 5 rows are not drawn: the table shows “<5”.' }; }
    rec.summary = X.why ? X.why.replace(/\.?$/, '.') : '';
    if (!out.length) { rec.kind = 'table'; rec.empty = true; rec.summary = 'No group has 5 or more rows under these filters.'; }
    return rec;
  }

  /* ------------------------------------------------------------ the tab */
  function ids(D) { var o = {}; D.kpis.forEach(function (k) { o[k.id] = k; }); D.charts.forEach(function (c) { o[c.id] = c; }); return o; }
  function chosenWords(D) {
    if (D.chosen_by === 'recorded') return '<p class="nld-badge nld-rec" role="note"><strong>Recorded design.</strong> ' + esc(D.recorded) + '</p>';
    if (D.chosen_by === 'ai') return '<p class="nld-badge" role="note"><strong>Designed by the AI, computed by the engine.</strong> The AI chose the tiles, charts and filters from what the engine offered; the engine checked each one and computed every figure.</p>';
    return '<p class="nld-badge" role="note"><strong>Designed by the engine.</strong> No AI design for this run: the engine chose the tiles, charts and filters from its own reading of the columns.</p>';
  }
  function head(D) {
    var I = ids(D);
    var qs = D.questions.map(function (q) {
      return '<li>' + esc(q.q) + ' <span class="nld-ans">' + q.answered_by.filter(function (i) { return I[i]; }).map(function (i) {
        var x = I[i]; return '<a href="#nld-x-' + esc(i) + '">' + esc(x.label || x.title) + '</a>';
      }).join(', ') + '</span></li>';
    }).join('');
    return '<header class="nld-head"><h3 class="nld-title">' + esc(D.title || 'Dashboard') + '</h3>' + chosenWords(D) +
      (D.audience ? '<p class="nld-aud"><strong>For:</strong> ' + esc(D.audience) + '</p>' : '') +
      (qs ? '<div class="nld-qs"><strong>The questions it answers</strong><ol>' + qs + '</ol></div>' : '') + '</header>';
  }
  function countsCard(D) {
    var items = D.kpis.concat(D.charts.filter(function (c) { return c.draw !== 'static'; })).map(function (x) {
      return '<li><strong>' + esc(x.label || x.title) + ':</strong> ' + esc(x.definition || '') + '</li>';
    }).join('');
    var opts = D.kpis.concat(D.charts.filter(function (c) { return c.draw !== 'static'; })).map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(x.label || x.title) + '</option>'; }).join('');
    return '<section class="tr-card nld-counts" aria-labelledby="nld-counts-h"><h4 id="nld-counts-h">What each column means and what one row is</h4>' +
      '<p class="note">The engine\'s reading of your file; the Dashboard counts exactly this. Correct it here and the dashboard is rebuilt in this browser.</p>' +
      '<p class="nld-grain"><strong>One row:</strong> <span id="nld-grain-t">' + esc(D.grain.text) + '</span></p>' +
      ((D.columns || []).length ? '<details class="more nld-cols"><summary>What each column means</summary><ul>' + D.columns.map(function (c) {
        var m = (window.NLColumns && window.NLColumns.meaning(c.column)) || {};
        return '<li><strong>' + esc(c.column) + '</strong>: ' + esc(c.reads_as) + (c.unit ? ', in ' + esc(c.unit) : '') +
          '<label class="nld-colnote">What it means, in your words <input type="text" maxlength="200" data-nld-col="' + esc(c.column) + '" value="' + esc(m.note || '') + '"></label></li>'; }).join('') + '</ul></details>' : '') +
      '<p class="nld-defs-h"><strong>Each tile and chart:</strong></p><ul class="nld-defs">' + items + '</ul>' +
      '<details class="more nld-edit"><summary>Edit a definition</summary><p class="note">Correct what a tile or chart counts, or what one row is. The dashboard is rebuilt here from the engine\'s aggregate: no AI call, nothing sent.</p>' +
      '<div class="nld-form"><label>Tile or chart <select id="nld-e-el">' + opts + '</select></label>' +
      '<label>Aggregation <select id="nld-e-agg"></select></label>' +
      '<label>Label <input id="nld-e-lab" type="text" maxlength="60"></label>' +
      '<label>Unit word <input id="nld-e-unit" type="text" maxlength="20"></label>' +
      '<label class="nld-wide">What one row is <input id="nld-e-grain" type="text" maxlength="200" value="' + esc(D.grain.text) + '"></label>' +
      '<p><button type="button" class="tb" data-nld="rebuild">Rebuild the dashboard</button> <span id="nld-e-msg" class="note" role="status"></span></p></div></details></section>';
  }
  function filtersBar(D) {
    var h = '<form class="nld-filters" aria-label="Filters" onsubmit="return false">', ms = D.cube.months;
    D.filters.forEach(function (f) {
      if (f.kind === 'date' && ms.length) {
        var o = function (sel) { return ms.map(function (m) { return '<option value="' + m + '"' + (m === sel ? ' selected' : '') + '>' + esc(monthWord(m)) + '</option>'; }).join(''); };
        h += '<fieldset class="nld-date"><legend>' + esc(f.column) + ' range</legend><label>From <select data-nld-f="from">' + o(ms[0]) + '</select></label>' +
          '<label>To <select data-nld-f="to">' + o(ms[ms.length - 1]) + '</select></label></fieldset>';
      } else if (f.kind === 'category') {
        var j = dimIdx(D, f.key);
        if (j < 0) return;
        h += '<label class="nld-cat">' + esc(f.column) + ' <select data-nld-f="cat" data-key="' + esc(f.key) + '"><option value="">All</option>' +
          D.cube.dims[j].levels.map(function (l) { return '<option value="' + esc(l) + '">' + esc(l) + '</option>'; }).join('') + '</select></label>';
      }
    });
    return h + '<button type="button" class="tb" data-nld="clear">Clear filters</button><p class="nld-active note" id="nld-active" role="status" aria-live="polite"></p></form>';
  }
  function tileHtml(D, K, F) {
    var r = NLD.kpi(D, K, F), unit = unitOf(D, K), v = r.value === null ? (r.n > 0 ? '<5' : '–') : fmt(r.value, K.agg === 'count' ? '' : unit);
    var sub = r.value === null ? (r.n > 0 ? 'fewer than 5 rows: not shown' : 'no rows under these filters') :
      (K.agg === 'latest' ? 'in ' + monthWord(r.month) + ', ' : '') + r.n.toLocaleString('en-US') + (K.agg === 'count' ? ' rows' : ' values');
    return '<div class="tr-kpi nld-kpi" id="nld-x-' + esc(K.id) + '" tabindex="-1"><span class="k-lab">' + esc(K.label) + '</span><span class="k-val">' + esc(v) + '</span><span class="k-sub">' + esc(sub) + '</span></div>';
  }
  function figHtml(X) {
    var st = X.draw === 'static';
    return '<figure class="visual nld-fig" data-table="1" id="nld-x-' + esc(X.id) + '" tabindex="-1" data-draw="' + esc(X.draw) + '"><figcaption>' + esc(X.title) + '</figcaption>' +
      (st ? '<p class="note nld-static">Not filtered: the engine\'s own chart of every row it kept.</p>' : '') +
      '<div class="viz" id="nld-v-' + esc(X.id) + '"></div><div class="nld-fignote"></div></figure>';
  }
  function notes(D, F, local) {
    var R = D.remarks, rows = R.rows, act = activeWords(D, F);
    var defs = D.kpis.concat(D.charts).map(function (x) { return '<li><strong>' + esc(x.label || x.title) + ':</strong> ' + esc(x.definition || (x.draw === 'static' ? 'the engine\'s own chart record, over every row it kept (not filtered).' : '')) + '</li>'; }).join('');
    return '<section class="tr-card nld-notes" aria-labelledby="nld-notes-h"><h4 id="nld-notes-h">Notes</h4>' +
      '<h5>Definitions</h5><ul>' + defs + '</ul>' +
      '<h5>Dates</h5><p>Shown: <span data-nld="shown">' + esc(shownRange(D, F)) + '</span>. The data run from ' + esc(R.data_range_text) + '.</p>' +
      '<h5>Filters</h5><p data-nld="filters">' + esc(act || 'None: every row the engine kept.') + '</p>' +
      '<h5>Rows</h5><p>' + rows.kept.toLocaleString('en-US') + ' kept of ' + rows['in'].toLocaleString('en-US') + ' read; ' + rows.set_aside.toLocaleString('en-US') + ' set aside' +
      (rows.set_aside ? ' (each reason is under \u201cNeeds your check\u201d below)' : '') + '.</p>' +
      '<h5>Rules</h5><ul>' + D.rules.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>' +
      lookedWrong(D, local) + '</section>';
  }
  // "Looked wrong, please check": what a person must look at first, then what was fixed and the health detail, folded
  function lookedWrong(D, local) {
    var R = D.remarks, need = R.needs_check || [], li = function (t) { return '<li>' + esc(t) + '</li>'; };
    var find = function (n) {
      if (n.where !== 'set_aside' && n.where !== 'blank') return '';
      var w = n.where === 'set_aside' ? 'in the set-aside rows' : 'blank in the cleaned file';
      return local ? ' <span class="note">(' + w + '; the cleaned file\'s nl_flag column marks them)</span>'
        : ' <a class="nld-find" href="#try-step-2" data-rule="' + esc(n.rule || '') + '">Find them (' + w + '; nl_flag in step 2)</a>';
    };
    return '<h5 id="nld-check-h">Looked wrong, please check</h5>' +
      '<h6>Needs your check</h6>' + (need.length ? '<ul class="nld-check">' + need.map(function (n) { return '<li>' + esc(n.text) + find(n) + '</li>'; }).join('') + '</ul>' : '<p>Nothing for a person to check.</p>') +
      ((R.fixed || []).length ? '<details class="more nld-fold"><summary>Fixed automatically (' + R.fixed.length + ' cleaning rules)</summary><ul>' + R.fixed.map(li).join('') + '</ul></details>' : '') +
      ((R.detail || []).length ? '<details class="more nld-fold"><summary>Data quality detail (' + R.detail.length + ' lines from the health check)</summary><ul>' + R.detail.map(li).join('') + '</ul></details>' : '') +
      ((R.design || []).length ? '<details class="more nld-fold"><summary>About this design (' + R.design.length + ' changed or left out)</summary><ul>' + R.design.map(li).join('') + '</ul></details>' : '');
  }
  function shownRange(D, F) { var ms = D.cube.months; return ms.length ? monthWord(F.from || ms[0]) + ' to ' + monthWord(F.to || ms[ms.length - 1]) : 'no dates'; }
  function activeWords(D, F) {
    var out = [], ms = D.cube.months;
    if (ms.length && ((F.from && F.from !== ms[0]) || (F.to && F.to !== ms[ms.length - 1]))) out.push('dates ' + shownRange(D, F));
    Object.keys(F.cats || {}).forEach(function (k) { var j = dimIdx(D, k); if (j >= 0 && F.cats[k].length) out.push(D.cube.dims[j].column + ' is ' + F.cats[k].join(' or ')); });
    return out.join('; ');
  }
  function exportsBar(local) {
    return '<div class="nld-exp" role="group" aria-label="Download this dashboard">' +
      (local ? '' : '<button type="button" class="tb" data-nld="html">Download dashboard (HTML)</button> <button type="button" class="tb" data-nld="xlsx">Download Excel workbook</button>') +
      '<p class="note">' + (local ? 'A dashboard saved from NorthLedger: the filters work in this file, offline; nothing is sent anywhere.' :
        'Both files are written in this browser, with the filters as set now; nothing is uploaded. The HTML file keeps the filters working offline.') + '</p></div>';
  }
  NLD.html = function (D, local) {
    var I = ids(D), body = '';
    D.layout.forEach(function (row) {
      var ks = row.filter(function (i) { return I[i] && !I[i].draw; }), cs = row.filter(function (i) { return I[i] && I[i].draw; });
      if (ks.length) body += '<div class="tr-kpis nld-kpis" data-nld="kpis" data-ids="' + esc(ks.join(' ')) + '"></div>';
      if (cs.length) body += '<div class="nld-row">' + cs.map(function (i) { return figHtml(I[i]); }).join('') + '</div>';
    });
    var card = NLD.cardHost && !local ? '<p class="nld-counts-link note">What this dashboard counts: <a href="#nld-counts-h">what each column means and what one row is</a> (step 1, editable).</p>' : countsCard(D);
    return '<div class="nld" data-v="' + esc(D.version) + '">' + head(D) + card + filtersBar(D) + body + notes(D, {}, local) + exportsBar(local) + '</div>';
  };
  NLD.countsCard = countsCard;

  /* ------------------------------------------------------------ mounting: filters, cross-filter, edits */
  NLD.mount = function (root, D, opts) {
    opts = opts || {};
    var box = root.querySelector('.nld');
    if (!box) return;
    var host = !opts.local && NLD.cardHost ? NLD.cardHost : null;
    if (host) host.innerHTML = countsCard(D);
    var card = host || box;
    var F = opts.F || { cats: {} };
    var draw = function (X, W) {
      var V = window.NLV;
      var rec = X.draw === 'static' ? X.record : NLD.record(D, X, F);
      if (X.draw === 'static' && opts.snapshot && opts.snapshot[X.id]) return { html: opts.snapshot[X.id] };
      if (!V || !V.draw) return { html: '<p class="note">Charts could not be drawn here.</p>' };
      if (rec.empty) return { html: '<p class="note">' + esc(rec.summary) + '</p>' };
      var out = V.draw(rec, W);
      X.__last = rec;
      var note = box.querySelector('#nld-x-' + cssId(X.id) + ' .nld-fignote');
      if (note) note.innerHTML = out.note || '';
      return out;
    };
    function cssId(s) { return String(s).replace(/[^a-z0-9_]/g, '\\$&'); }
    function render() {
      Array.prototype.forEach.call(box.querySelectorAll('[data-nld="kpis"]'), function (el) {
        el.innerHTML = el.getAttribute('data-ids').split(' ').map(function (i) { var K = ids(D)[i]; return K ? tileHtml(D, K, F) : ''; }).join('');
      });
      D.charts.forEach(function (X) {
        var id = 'nld-v-' + X.id, el = document.getElementById(id);
        if (!el) return;
        if (!el.getAttribute('data-bound') && U.visual) { el.setAttribute('data-bound', '1'); U.visual(id, function (W) { return draw(X, W); }); }
        else if (U.draw) U.draw(id);
        else el.innerHTML = draw(X, el.clientWidth || 600).html || '';
      });
      var a = activeWords(D, F), ac = box.querySelector('#nld-active');
      if (ac) ac.textContent = a ? 'Filtered: ' + a + '.' : 'No filter: every row the engine kept.';
      var sh = box.querySelector('[data-nld="shown"]'); if (sh) sh.textContent = shownRange(D, F);
      var fl = box.querySelector('[data-nld="filters"]'); if (fl) fl.textContent = a || 'None: every row the engine kept.';
      Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="cat"]'), function (s) { var v = (F.cats[s.getAttribute('data-key')] || [])[0] || ''; if (s.value !== v) s.value = v; });
      if (U.markScrollers) U.markScrollers();
    }
    NLD.state = function () { return F; };
    NLD.setFilter = function (f) { F = { from: f.from, to: f.to, cats: f.cats || {} }; render(); };
    box.addEventListener('change', function (e) {
      var s = e.target, k = s.getAttribute && s.getAttribute('data-nld-f');
      if (k === 'from' || k === 'to') {
        F[k] = s.value;
        if (F.from && F.to && F.from > F.to) { var t = F.from; F.from = F.to; F.to = t; box.querySelector('select[data-nld-f="from"]').value = F.from; box.querySelector('select[data-nld-f="to"]').value = F.to; }
        render();
      } else if (k === 'cat') { F.cats[s.getAttribute('data-key')] = s.value ? [s.value] : []; render(); }
      else if (s.id === 'nld-e-el') fillEdit();
      else if (s.getAttribute && s.getAttribute('data-nld-col') && window.NLColumns) window.NLColumns.set(s.getAttribute('data-nld-col'), { note: s.value.replace(/\s+/g, ' ').trim() });
    });
    box.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('button'), act = b && b.getAttribute('data-nld');
      if (act === 'clear') {
        F = { cats: {} };
        Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="from"]'), function (s) { s.selectedIndex = 0; });
        Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="to"]'), function (s) { s.selectedIndex = s.options.length - 1; });
        render(); return;
      }
      if (act === 'rebuild') return rebuild();
      if (act === 'html' && opts.onHtml) return opts.onHtml(D, F);
      if (act === 'xlsx' && opts.onXlsx) return opts.onXlsx(D, F);
      if (b && b.getAttribute('data-nld-x')) { crossFilter(b.getAttribute('data-nld-x'), b.getAttribute('data-level')); return; }
      // a click on a bar (or its row) filters every tile and chart by that group
      var g = e.target.closest && e.target.closest('figure.nld-fig[data-draw="bar"] svg g[data-tip]');
      if (g) {
        var fig = g.closest('figure'), X = ids(D)[fig.id.slice(6)], gs = Array.prototype.slice.call(fig.querySelectorAll('svg g[data-tip]'));
        var lv = X && X.__last && X.__last.levels ? X.__last.levels[gs.indexOf(g)] : null;
        if (X && lv) crossFilter(X.id, lv);
      }
    });
    if (host && !host.__nld) {
      host.__nld = true;
      host.addEventListener('change', function (e) {
        if (e.target.id === 'nld-e-el' && NLD.__fill) NLD.__fill();
        if (e.target.getAttribute && e.target.getAttribute('data-nld-col') && window.NLColumns) window.NLColumns.set(e.target.getAttribute('data-nld-col'), { note: e.target.value.replace(/\s+/g, ' ').trim() });
      });
      host.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('button'); if (b && b.getAttribute('data-nld') === 'rebuild' && NLD.__rebuild) NLD.__rebuild(); });
    }
    box.addEventListener('keydown', function (e) {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('figure.nld-fig[data-draw="bar"] svg g[data-tip]')) { e.preventDefault(); e.target.dispatchEvent(new MouseEvent('click', { bubbles: true })); }
    });
    function crossFilter(xid, level) {
      var X = ids(D)[xid];
      if (!X || !X.bkey || level === 'other') return;
      var cur = F.cats[X.bkey] || [];
      F.cats[X.bkey] = cur.length === 1 && cur[0] === level ? [] : [level];
      render();
    }
    function aggOptions(X) {
      var j = X.mkey ? measIdx(D, X.mkey) : -1, flow = j >= 0 && D.cube.measures[j].flow;
      var list = j < 0 ? ['count'] : (flow ? ['sum', 'average', 'count'] : ['average', 'count']);
      if (j >= 0 && !X.draw && D.cube.months.length) list.push('latest');
      var words = { sum: 'total (added up)', average: 'average', count: 'number of rows', latest: 'latest month' };
      return list.map(function (a) { return '<option value="' + a + '"' + (a === X.agg ? ' selected' : '') + '>' + words[a] + '</option>'; }).join('');
    }
    function fillEdit() {
      var sel = card.querySelector('#nld-e-el'); if (!sel) return;
      var X = ids(D)[sel.value]; if (!X) return;
      card.querySelector('#nld-e-agg').innerHTML = aggOptions(X);
      card.querySelector('#nld-e-lab').value = X.label || X.title || '';
      card.querySelector('#nld-e-unit').value = X.unit_word || '';
    }
    function rebuild() {
      var X = ids(D)[card.querySelector('#nld-e-el').value], msg = card.querySelector('#nld-e-msg');
      if (!X) return;
      var a = card.querySelector('#nld-e-agg').value, j = X.mkey ? measIdx(D, X.mkey) : -1;
      if (a === 'sum' && j >= 0 && !D.cube.measures[j].flow) { msg.textContent = 'A level is averaged, never added up over time.'; return; }
      X.agg = a;
      var lab = card.querySelector('#nld-e-lab').value.replace(/\s+/g, ' ').trim().slice(0, 60);
      if (lab) { if (X.draw) X.title = lab; else X.label = lab; }
      X.unit_word = card.querySelector('#nld-e-unit').value.replace(/\s+/g, ' ').trim().slice(0, 20);
      X.definition = defWords(X) + ' Edited here by you.';
      D.__edits = D.__edits || {};
      // one unit word per measure on the whole dashboard (nl_dashboard._unit): every tile and chart of this measure takes it
      if (X.mkey) D.kpis.concat(D.charts).forEach(function (Y) {
        if (Y === X || Y.mkey !== X.mkey || Y.unit_word === X.unit_word) return;
        Y.unit_word = X.unit_word;
        if (Y.definition) Y.definition = Y.definition.replace(/; in [^;]*?\.(?=( |$))/, X.unit_word ? '; in ' + X.unit_word + '.' : '.');
        D.__edits[Y.id] = Object.assign(D.__edits[Y.id] || {}, { unit_word: Y.unit_word, definition: Y.definition });
      });
      var j0 = X.mkey ? measIdx(D, X.mkey) : -1;
      if (j0 >= 0) D.cube.measures[j0].unit = X.unit_word;
      var g = card.querySelector('#nld-e-grain').value.replace(/\s+/g, ' ').trim().slice(0, 200);
      if (g) D.grain.text = g;
      D.__edits[X.id] = { agg: X.agg, label: X.label, title: X.title, unit_word: X.unit_word, definition: X.definition };
      if (g) D.__edits.__grain = g;
      var root2 = box.parentNode;
      root2.innerHTML = NLD.html(D, opts.local);
      NLD.mount(root2, D, Object.assign({}, opts, { F: F }));
      var c2 = host || root2, m2 = c2.querySelector('#nld-e-msg'); if (m2) m2.textContent = 'Rebuilt with your definition.';
      if (window.NLSteps && !opts.local) window.NLSteps.set(5, 'done', 'rebuilt with your definition');
      var d2 = c2.querySelector('.nld-edit'); if (d2) d2.open = true;
      var s2 = c2.querySelector('#nld-e-el'); if (s2) { s2.value = X.id; NLD.__fill(); }
    }
    function defWords(X) {
      var j = X.mkey ? measIdx(D, X.mkey) : -1, w = { sum: 'The total of ', average: 'The average of ', latest: 'The latest month\'s value of ' }[X.agg];
      var s = X.agg === 'count' || j < 0 ? 'The number of ' + ((D.grain.noun || 'rows').toLowerCase()) : w + X.measure;
      if (X.where) s += ' over the rows where ' + X.where.column + ' is ' + (X.where.exclude ? 'not ' : '') + X.where.values.join(', ');
      if (X.by) s += ', by ' + X.by;
      if (X.unit_word && X.agg !== 'count') s += '; in ' + X.unit_word;
      return s + '.';
    }
    // the filters stick under the rail on a wide screen: a jump to a tile or chart lands below them too
    var fbar = box.querySelector('.nld-filters'), fmeasure = function () {
      if (!fbar || !fbar.offsetParent) return;
      document.documentElement.style.setProperty('--nld-filt', (getComputedStyle(fbar).position === 'sticky' ? Math.ceil(fbar.getBoundingClientRect().height) : 0) + 'px');
    };
    if (fbar && window.ResizeObserver && !opts.local) new ResizeObserver(fmeasure).observe(fbar);
    if (window.NLSteps && window.NLSteps.measure) window.NLSteps.measure();
    NLD.__fill = fillEdit; NLD.__rebuild = rebuild;
    fillEdit();
    render();
  };
  /* ------------------------------------------------------------ the shared column card (step 1): window.NLColumns */
  // What each column means, as the engine read it and as the visitor corrected it; one card for every step (Track C reads
  // it for its cleaning rules). NLColumns.meaning(header) -> {column, reads_as, unit, note} or null; NLColumns.all();
  // NLColumns.set(header, {note, unit}) records a correction. Event 'nl:columns' (detail {columns}) on every change.
  var COLS = {}, COL_EDITS = {};
  var NLColumns = {
    meaning: function (h) { var c = COLS[String(h)]; return c ? Object.assign({}, c, COL_EDITS[String(h)] || {}) : null; },
    all: function () { return Object.keys(COLS).map(NLColumns.meaning); },
    set: function (h, m) {
      if (!COLS[String(h)] || !m || typeof m !== 'object') return false;
      var e = COL_EDITS[String(h)] = COL_EDITS[String(h)] || {};
      if (typeof m.note === 'string') e.note = m.note.slice(0, 200);
      if (typeof m.unit === 'string') e.unit = m.unit.slice(0, 20);
      colsChanged(); return true;
    }
  };
  window.NLColumns = NLColumns;
  function colsChanged() { try { document.dispatchEvent(new CustomEvent('nl:columns', { detail: { columns: NLColumns.all() } })); } catch (e) { /* no event */ } }
  function loadCols(D) { COLS = {}; (D.columns || []).forEach(function (c) { COLS[c.column] = { column: c.column, reads_as: c.reads_as, unit: c.unit || '', note: '' }; }); colsChanged(); }
  /* ------------------------------------------------------------ the tab in the report (52-nl2-report.js select) */
  // the visitor's filters and definition edits survive a re-run when their columns and ids still exist
  var KEEP = { F: null, edits: null, dims: null };
  NLD.show = function (panel, rep) {
    var D = rep.dashboard;
    if (!D || !D.ok) { panel.innerHTML = '<p class="note">' + esc((D && D.why) || 'No dashboard for this file.') + '</p>'; return; }
    if (panel.__nldRep === rep) return;
    panel.__nldRep = rep;
    NLD.cardHost = document.getElementById('try-counts');
    if (KEEP.edits) {
      var I = ids(D);
      Object.keys(KEEP.edits).forEach(function (k) {
        if (k === '__grain') { D.grain.text = KEEP.edits[k]; return; }
        var X = I[k], E = KEEP.edits[k];
        if (X && E) Object.keys(E).forEach(function (f) { if (E[f] !== undefined && E[f] !== null) X[f] = E[f]; });
      });
      D.__edits = KEEP.edits;
    }
    var F = { cats: {} };
    if (KEEP.F) {
      var ms = D.cube.months;
      if (KEEP.F.from && ms.indexOf(KEEP.F.from) >= 0) F.from = KEEP.F.from;
      if (KEEP.F.to && ms.indexOf(KEEP.F.to) >= 0) F.to = KEEP.F.to;
      Object.keys(KEEP.F.cats || {}).forEach(function (k) {
        var col = KEEP.dims && KEEP.dims[k], d = D.cube.dims.filter(function (x) { return x.column === col; })[0];
        if (d) F.cats[d.key] = (KEEP.F.cats[k] || []).filter(function (l) { return d.levels.indexOf(l) >= 0; });
      });
    }
    loadCols(D);
    panel.innerHTML = NLD.html(D, false);
    NLD.mount(panel, D, { F: F, onHtml: function (D2, F2) { NLD.saveHtml(D2, F2, rep.input && rep.input.name); }, onXlsx: function (D2, F2) { NLD.saveXlsx(D2, F2, rep.input && rep.input.name); } });
    var fs = panel.querySelector('select[data-nld-f="from"]'), ts = panel.querySelector('select[data-nld-f="to"]');
    if (fs && F.from) fs.value = F.from; if (ts && F.to) ts.value = F.to;
    NLD.current = { D: D, rep: rep };
  };
  // a chart pinned from "Ask your data" (Track B): {chart: a menu name, args: [column names], title, why, where?}. The
  // engine checks and computes it on the next run: the rail marks the Dashboard out of date with one Update (local, no
  // AI call). Pins survive re-runs; one whose columns are gone is refused there with its reason, in the notes.
  NLD.pins = [];
  NLD.pin = function (spec) {
    if (!spec || typeof spec !== 'object' || typeof spec.chart !== 'string') return false;
    var p = { chart: spec.chart, args: Array.isArray(spec.args) ? spec.args.map(String).slice(0, 4) : [], title: String(spec.title || '').slice(0, 90), why: String(spec.why || '').slice(0, 200) };
    if (spec.where && typeof spec.where === 'object') p.where = spec.where;
    NLD.pins.push(p);
    NLD.pins = NLD.pins.slice(-6);
    if (window.NLSteps) window.NLSteps.stale(5, 'A chart was added to the dashboard: Update builds it from your rows.');
    return true;
  };
  // called before a re-run: remember what the visitor set (by column name, so new dimension keys still match)
  NLD.remember = function () {
    if (!NLD.current || !NLD.state) return;
    var D = NLD.current.D, F = NLD.state(), dims = {};
    D.cube.dims.forEach(function (d) { dims[d.key] = d.column; });
    KEEP = { F: JSON.parse(JSON.stringify(F)), edits: D.__edits || null, dims: dims };
  };
  NLD.selfCheck = function (D) {
    var bad = [];
    D.kpis.forEach(function (K) {
      var a = NLD.kpi(D, K, {}), b = D.defaults.kpis[K.id];
      if (!b) return;
      if ((a.value === null) !== (b.value === null) || (a.value !== null && Math.abs(a.value - b.value) > 1e-6 * Math.max(1, Math.abs(b.value))) || a.n !== b.n) bad.push(K.id);
    });
    return bad;
  };

  /* ------------------------------------------------------------ exports */
  // the sources of 00-core, 51, 55 and this file, as build.py inlined them (each opens with the build mark, at-nl-file and its name)
  function sources(names) {
    var all = Array.prototype.map.call(document.querySelectorAll('script'), function (s) { return s.textContent; }).join('\n'), out = [];
    var MARK = '/*@nl' + '-file ';            // written in two parts, so this file's own text is not a mark
    names.forEach(function (n) {
      var a = all.indexOf(MARK + n + '*/');
      if (a < 0) return;
      var b = all.indexOf(MARK, a + 10);
      out.push(all.slice(a, b < 0 ? all.length : b));
    });
    return out;
  }
  function stem(name) { return String(name || 'dashboard').replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '-').slice(0, 60) || 'dashboard'; }
  function save(blob, name) {
    var url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = name; a.hidden = true; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }
  NLD.htmlFile = function (D, F) {
    var css = Array.prototype.map.call(document.querySelectorAll('style'), function (s) { return s.textContent; }).join('\n');
    var js = sources(['00-core.js', '51-nl2-charts.js', '55-nl-viz.js', '57-dashboard.js']);
    if (js.length < 4) return null;
    var snap = {};
    D.charts.forEach(function (X) {     // a chart the filters do not change is carried as it is drawn now
      if (X.draw !== 'static') return;
      var el = document.getElementById('nld-v-' + X.id); if (el) snap[X.id] = el.innerHTML;
    });
    var data = JSON.stringify({ D: D, F: F, snap: snap }).replace(/</g, '\\u003c');
    var title = D.title || 'Dashboard';
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>' + esc(title) + '</title><style>' + css + '\nbody{max-width:1180px;margin:24px auto;padding:0 16px}<\/style><\/head><body><main id="nld-root"></main><div id="tip" class="tip" hidden></div>' +
      '<script>window.NLD_EXPORT=' + data + ';<\/script><script>' + js.join('\n') + '<\/script><\/body><\/html>';
  };
  NLD.saveHtml = function (D, F, name) {
    var h = NLD.htmlFile(D, F);
    if (!h) return false;
    save(new Blob([h], { type: 'text/html;charset=utf-8' }), stem(name) + '-dashboard.html');
    return true;
  };
  NLD.saveXlsx = function (D, F, name) {
    save(new Blob([NLD.xlsx(D, F)], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), stem(name) + '-dashboard.xlsx');
    return true;
  };

  /* ------------------------------------------------------------ the workbook (56-xlsx.js NLX writes it) */
  // Data (the aggregates under the filters, never rows), Notes, Charts (native Excel charts on the Data ranges)
  NLD.workbookSpec = function (D, F) {
    F = F || {};
    var rows = [], charts = [], notesRows = [], DATA = 'Data';
    var put = function (r) { rows.push(r); return rows.length; };
    var B = function (v) { return { v: v, b: true }; };
    put([B(D.title || 'Dashboard')]);
    put(['Aggregates the engine computed from the rows it kept, under the filters below; never the rows themselves. A blank value rests on fewer than 5 rows or has no rows (see Note): it is left blank, never filled.']);
    put(['Filters: ' + (activeWords(D, F) || 'none') + '. Dates shown: ' + shownRange(D, F) + '.']);
    put(null);
    put([B('Tile'), B('Value'), B('Rows'), B('Note'), B('Definition')]);
    D.kpis.forEach(function (K) {
      var r = NLD.kpi(D, K, F);
      put([K.label, r.value === null ? null : { v: r.value, fmt: Math.abs(r.value) >= 100 || K.agg === 'count' ? 'int' : 'num2' }, { v: r.n, fmt: 'int' },
        r.value === null ? (r.n > 0 ? '<5: fewer than 5 rows' : 'no rows') : (K.agg === 'latest' ? 'in ' + monthWord(r.month) : ''), K.definition || '']);
    });
    D.charts.forEach(function (X) {
      put(null);
      put([B(X.title), X.definition || '']);
      if (X.draw === 'static') {
        var t = X.record && X.record.table;
        put(['The engine\'s own chart record over every row it kept (not filtered), as its table.']);
        if (t && t.cols) { put(t.cols.map(B)); (t.rows || []).forEach(function (r) { put(r.slice()); }); }
        return;
      }
      var rec = NLD.record(D, X, F), unit = unitOf(D, X);
      if (rec.empty) { put([rec.summary]); return; }
      var d = rec.data, head, r0, r1, S, fmtV = function (v) { return v === null ? null : { v: v, fmt: Math.abs(v) >= 100 ? 'int' : 'num2' }; };
      if (X.draw === 'bar' || X.draw === 'grouped') {
        var two = d.series.length === 2;
        head = put([B(X.by)].concat(d.series.map(function (s) { return B(s + (unit ? ' (' + unit + ')' : '')); })).concat(two ? [B('Change')] : []).concat([B('Note')]));
        r0 = rows.length + 1;
        d.rows.forEach(function (r) {
          var at = rows.length + 1, row = [r.label].concat(r.values.map(fmtV));
          // the change as a live formula with its cached value (a total's change; blank when the earlier period is 0)
          if (two) row.push({ f: 'IF(B' + at + '=0,"",(C' + at + '-B' + at + ')/B' + at + ')', v: r.values[0] ? (r.values[1] - r.values[0]) / r.values[0] : '', fmt: 'pct' });
          put(row.concat(['']));
        });
        r1 = rows.length;
        (rec.table.rows || []).filter(function (r) { return r[1] === '<5'; }).forEach(function (r) { put([r[0]].concat(d.series.map(function () { return null; })).concat(two ? [null] : []).concat(['<5: fewer than 5 rows'])); });
        if (r1 >= r0) charts.push({ type: X.draw === 'bar' ? 'bar' : 'column', title: X.title + (unit ? ' (' + unit + ')' : ''), cat: { sheet: DATA, col: 0, r0: r0, r1: r1 },
          series: d.series.map(function (s, k) { return { name: { sheet: DATA, col: 1 + k, row: head }, sheet: DATA, col: 1 + k, r0: r0, r1: r1, color: two ? (k ? '2A78D6' : 'D45A26') : '2A78D6' }; }) });
      } else {
        S = d.series || d.parts || d.panels;
        head = put([B(d.x_label)].concat(S.map(function (s) { return B(s.label); })).concat([B('Note')]));
        r0 = rows.length + 1;
        d.x.forEach(function (p, i) {
          var nts = S.map(function (s, k) { var t = rec.table.rows[i][1 + k]; return t === '<5' ? s.label + ' <5' : t === 'no rows' ? s.label + ': no rows' : ''; }).filter(Boolean);
          put([periodWord(p)].concat(S.map(function (s) { return fmtV(s.values[i]); })).concat([nts.join('; ')]));
        });
        r1 = rows.length;
        if (X.draw !== 'multiples') charts.push({ type: X.draw === 'area' ? 'area' : 'line', title: X.title + (unit ? ' (' + unit + ')' : ''), cat: { sheet: DATA, col: 0, r0: r0, r1: r1 },
          series: S.map(function (s, k) { return { name: { sheet: DATA, col: 1 + k, row: head }, sheet: DATA, col: 1 + k, r0: r0, r1: r1 }; }) });
      }
    });
    var R = D.remarks, n = function (t) { notesRows.push([t]); }, h = function (t) { notesRows.push([B(t)]); };
    h('Notes');
    h('Design'); n(D.chosen_by === 'recorded' ? D.recorded : D.chosen_by === 'ai' ? 'Designed by the AI, computed by the engine.' : 'Designed by the engine (no AI design for this run).');
    if (D.audience) n('For: ' + D.audience);
    h('What one row is'); n(D.grain.text);
    h('Definitions'); D.kpis.concat(D.charts).forEach(function (x) { n((x.label || x.title) + ': ' + (x.definition || 'the engine\'s own chart, not filtered.')); });
    h('Dates'); n('Shown: ' + shownRange(D, F) + '. The data run from ' + R.data_range_text + '.');
    h('Filters'); n(activeWords(D, F) || 'None: every row the engine kept.');
    h('Rows'); n(R.rows.kept + ' kept of ' + R.rows['in'] + ' read; ' + R.rows.set_aside + ' set aside' + (R.rows.reasons.length ? ': ' + R.rows.reasons.map(function (q) { return q.count + ' ' + q.reason; }).join('; ') : '') + '.');
    h('Rules'); D.rules.forEach(n);
    h('Looked wrong, please check');
    h('Needs your check'); ((R.needs_check || []).length ? R.needs_check.map(function (x) { return x.text + (x.where === 'set_aside' ? ' (in the set-aside rows; the cleaned file\'s nl_flag column marks them)' : x.where === 'blank' ? ' (blank in the cleaned file; nl_flag marks them)' : ''); }) : ['Nothing for a person to check.']).forEach(n);
    h('Fixed automatically'); (R.fixed || []).forEach(n);
    h('Data quality detail'); (R.detail || []).forEach(n);
    if ((R.design || []).length) { h('About this design'); R.design.forEach(n); }
    charts.forEach(function (c, i) { c.at = { col: 0, row: 3 + i * 20, cols: 10, rows: 18 }; });
    return { title: D.title || 'Dashboard', sheets: [
      { name: DATA, rows: rows, widths: [34, 18, 18, 14, 40, 16, 16, 24], freeze: 1 },
      { name: 'Notes', rows: notesRows, widths: [140], freeze: 1 },
      { name: 'Charts', rows: [[B('Charts')], ['Native Excel charts drawn from the Data sheet. Small multiples and the engine\'s own charts are tables on the Data sheet.']], widths: [16], charts: charts }
    ] };
  };
  NLD.xlsx = function (D, F) { return window.NLX.workbook(NLD.workbookSpec(D, F)); };

  /* ------------------------------------------------------------ in a saved file: mount from the data it carries */
  if (window.NLD_EXPORT && window.NLD_EXPORT.D) {
    var go = function () {
      var E = window.NLD_EXPORT, root = document.getElementById('nld-root');
      if (!root) return;
      root.innerHTML = NLD.html(E.D, true);
      NLD.mount(root, E.D, { local: true, snapshot: E.snap });
      if (E.F && (E.F.from || E.F.to || Object.keys(E.F.cats || {}).length)) {
        var f = root.querySelector('select[data-nld-f="from"]'), t = root.querySelector('select[data-nld-f="to"]');
        if (f && E.F.from) f.value = E.F.from; if (t && E.F.to) t.value = E.F.to;
        NLD.setFilter(E.F);
      }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
  }
})();
