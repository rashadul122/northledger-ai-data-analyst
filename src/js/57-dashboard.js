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
  var GRANK = { month: 0, quarter: 1, year: 2 };
  // the step a chart reads: the span's (grainOf), never finer than the data's own (cube.grain: a yearly series is never shown by month)
  function grainFor(D, nMonths) { var a = NLD.grainOf(nMonths), b = (D.cube && D.cube.grain) || 'month'; return GRANK[b] > GRANK[a] ? b : a; }
  function nativeOf(D) { return (D.cube && D.cube.grain) || 'month'; }
  // the fewest rows a figure may rest on: 5 for a file of records, 1 for a table of published figures (nl_dashboard.observation_table)
  // a table of published figures (nl_dashboard.observation_table): its tiles read its own step; told by the engine's reading, never
  // by the row floor (1 for every file since phase A)
  function isSeries(D) { return !!((D.cube && D.cube.series) || (D.cube && D.cube.series === undefined && D.observations)); }
  function smallOf(D) { var k = D.cube && D.cube.small; return typeof k === 'number' && k >= 1 ? k : SMALL; }
  NLD.periodWord = periodWord;

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
    var small = smallOf(D);
    if (j < 0 || how === 'count') return { v: rows >= small ? rows : null, n: rows };
    if (k < small) return { v: null, n: k };
    return { v: how === 'average' ? s / k : s, n: k };
  }
  // [{p, v, n}] in time order at a step (the data's own by default): a flow's total, a level's average, the rows (no measure); a period
  // on fewer rows than the floor has v null (nl_dashboard.period_values)
  function periodValues(D, cells, mkey, grain) {
    var nd = D.cube.dims.length, j = mkey ? measIdx(D, mkey) : -1, flow = j >= 0 && D.cube.measures[j].flow, by = {}, small = smallOf(D);
    grain = grain || nativeOf(D);
    cells.forEach(function (c) {
      if (c[0] < 0) return;
      var p = periodOf(D.cube.months[c[0]], grain), a = by[p] || (by[p] = { s: 0, k: 0 });
      if (j < 0) a.k += c[1 + nd]; else { a.s += c[2 + nd + 2 * j]; a.k += c[3 + nd + 2 * j]; }
    });
    return Object.keys(by).sort().map(function (p) {
      var a = by[p];
      return { p: p, n: a.k, v: a.k < small ? null : j < 0 ? a.k : flow ? a.s : a.s / a.k };
    });
  }
  NLD.periodValues = periodValues;
  function yearBack(p, grain) { return grain === 'year' ? String(+p.slice(0, 4) - 1) : (+p.slice(0, 4) - 1) + p.slice(4); }
  function firstMonth(D, p, grain) { for (var i = 0; i < D.cube.months.length; i++) if (periodOf(D.cube.months[i], grain) === p) return D.cube.months[i]; return null; }
  // a tile that reads its measure period by period (nl_dashboard._series_kpi): latest, change (from the period before, or vs 'year'),
  // peak and low, each with its period
  // the step a tile reads (nl_dashboard.tile_grain): a published series its own; records the step their charts show
  function tileGrain(D, cells) {
    if (isSeries(D)) return nativeOf(D);
    var lo = null, hi = null;
    cells.forEach(function (c) { if (c[0] >= 0) { var m = D.cube.months[c[0]]; if (lo === null || m < lo) lo = m; if (hi === null || m > hi) hi = m; } });
    if (lo === null) return nativeOf(D);
    return grainFor(D, (+hi.slice(0, 4) - +lo.slice(0, 4)) * 12 + (+hi.slice(5, 7) - +lo.slice(5, 7)) + 1);
  }
  function heldMonths(D, cells, g) {
    if (g === 'month' || isSeries(D)) return null;
    var last = null; cells.forEach(function (c) { if (c[0] >= 0) { var m = D.cube.months[c[0]]; if (last === null || m > last) last = m; } });
    if (!last) return null;
    var full = g === 'quarter' ? 3 : 12, held = (+last.slice(5, 7) - 1) % full + 1;
    return held < full ? held : null;
  }
  // nl_dashboard._partial_last: a records file's last month, read by month, still filling
  function partialLast(D, pv, g) { var ms = D.cube.months; return g === 'month' && !isSeries(D) && D.cube.last_month_full === false && pv.length > 0 && pv[pv.length - 1].p === ms[ms.length - 1]; }
  function seriesKpi(D, K, cells) {
    var g = tileGrain(D, cells), pv = periodValues(D, cells, K.mkey, g), mj0 = measIdx(D, K.mkey);
    if (partialLast(D, pv, g) && (K.agg === 'change' || K.agg === 'peak' || K.agg === 'low' || (mj0 >= 0 && D.cube.measures[mj0].flow))) pv = pv.slice(0, -1);
    var have = pv.filter(function (x) { return x.v !== null; });
    var none = { value: null, n: pv.reduce(function (a, x) { return a + x.n; }, 0), suppressed: !!pv.length && !have.length, month: null, period: null, base: null, base_period: null, pct: null };
    if (!have.length) return none;
    if (K.agg === 'peak' || K.agg === 'low') {
      var b = null;
      have.forEach(function (x) { if (!b || (K.agg === 'peak' ? x.v >= b.v : x.v <= b.v)) b = x; });
      return Object.assign(none, { value: b.v, n: b.n, suppressed: false, period: b.p, month: firstMonth(D, b.p, g) });
    }
    var last = pv[pv.length - 1];
    if (last.v === null) return Object.assign(none, { n: last.n, suppressed: true, period: last.p, month: firstMonth(D, last.p, g) });
    var held = heldMonths(D, cells, g);
    if (K.agg === 'latest') return Object.assign(none, { value: last.v, n: last.n, suppressed: false, period: last.p, month: firstMonth(D, last.p, g), held: held });
    var want = K.vs === 'year' ? yearBack(last.p, g) : null, prev = null;
    for (var i = pv.length - 2; i >= 0; i--) {
      if (want !== null && pv[i].p !== want) { if (pv[i].p < want) break; continue; }
      prev = pv[i]; break;
    }
    if (!prev || prev.v === null) return Object.assign(none, { n: last.n, period: last.p, month: firstMonth(D, last.p, g) });
    var d = last.v - prev.v;
    return Object.assign(none, { value: d, n: last.n + prev.n, suppressed: false, period: last.p, month: firstMonth(D, last.p, g),
      base: prev.v, base_period: prev.p, pct: prev.v ? d / Math.abs(prev.v) * 100 : null, held: held });
  }
  NLD.kpi = function (D, K, F) {
    var cells = pick(D, F || {}, K.where), month = null, how = K.agg;
    // a rows tile's highest or lowest period reads the rows period by period (nl_dashboard.query_kpi does the same)
    if ((how === 'latest' || how === 'change' || how === 'peak' || how === 'low') && (K.mkey || how === 'peak' || how === 'low')) return seriesKpi(D, K, cells);
    if (how === 'latest') {
      var last = -1; cells.forEach(function (c) { if (c[0] > last) last = c[0]; });
      cells = last < 0 ? [] : cells.filter(function (c) { return c[0] === last; });
      month = last < 0 ? null : D.cube.months[last];
      var mj = measIdx(D, K.mkey);
      how = mj >= 0 && !D.cube.measures[mj].flow ? 'average' : 'sum';
    }
    var r = agg(D, cells, K.agg === 'count' ? null : K.mkey, K.agg === 'count' ? 'count' : how);
    return { value: r.v, n: r.n, suppressed: r.n > 0 && r.n < smallOf(D), month: month };
  };
  // the filtered span's months (the data's own months inside the date range), and the grain it is shown at
  // `want`: the reader's drill choice (F.grain: year, quarter or month), never finer than the data's own step, and by month only
  // while the span fits a long line (GLONG, 240 points in 51-nl2-charts.js); otherwise the span's own step
  var DRILL_MAX = { month: 240, quarter: 240, year: 400 };
  function span(D, cells, want) {
    var lo = Infinity, hi = -1;
    cells.forEach(function (c) { if (c[0] >= 0) { if (c[0] < lo) lo = c[0]; if (c[0] > hi) hi = c[0]; } });
    if (hi < 0) return null;
    var ms = mrange(D.cube.months[lo], D.cube.months[hi]), g = grainFor(D, ms.length), ps = [];
    if (want && OWN.call(GRANK, want) && GRANK[want] >= GRANK[nativeOf(D)]) {
      var n = want === 'month' ? ms.length : want === 'quarter' ? Math.ceil(ms.length / 3) : Math.ceil(ms.length / 12);
      if (n <= DRILL_MAX[want]) g = want;
    }
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
  // a series' name: "Average X" / "Total X", or X alone when each point is one published figure (plain)
  function whatOf(D, X, plain) { return X.mkey ? (plain ? '' : X.agg === 'average' ? 'Average ' : 'Total ') + X.measure : (D.grain && D.grain.noun) || 'Rows'; }
  function textOf(v, n, unit) { return v === null ? (n > 0 ? '<5' : 'no rows') : fmt(v, unit); }
  // a change, signed: "+1.2 pp" for a percentage, "+1,240" otherwise
  function fmtChange(v, unit) {
    if (v === null || v === undefined || !isFinite(v)) return '';
    var s = (v > 0 ? '+' : v < 0 ? '−' : '') + fmt(Math.abs(v), unit === '%' ? '' : unit);
    return unit === '%' ? s + ' pp' : s;
  }
  NLD.fmtChange = fmtChange;
  function base(X, kind, D) {
    return { kind: kind, chart: X.chart, id: 'dash.' + X.id, title: X.title, chosen_by: D.chosen_by === 'engine' ? 'engine' : 'ai', why: X.why || '',
      section: 'other', source: 'Recomputed in this browser from the engine\'s aggregate of the rows it kept, under the filters shown' };
  }
  NLD.record = function (D, X, F) {
    var cells = pick(D, F || {}, X.where), unit = unitOf(D, X), how = X.agg, sup = 0, i;
    if (X.draw === 'bar' || X.draw === 'grouped') return barRecord(D, X, cells, unit);
    if (X.draw === 'change' || X.draw === 'periods') return ownRecord(D, X, cells, unit);
    var sp = span(D, cells, F && F.grain);
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
      return { label: g === null ? whatOf(D, X, D.observations && sp.grain === nativeOf(D)) : g === 'rest' ? 'other' : L[g], values: vals, texts: texts };
    }); }
    if (X.draw === 'area') { S = S.filter(function (s) { return s.label !== 'other' || s.values.some(function (v) { return v; }); }); }
    // every part folded into "other" (each group has a thin period somewhere): no split is left to stack, so the total is drawn as a
    // line, and said (19 Oct 2026: Amazon review volume by department fell back to its table)
    var asLine = false;
    if (X.draw === 'area' && S.length < 2) {
      asLine = true;
      var totS = x.map(function (p) { return agg(D, P[p], X.mkey, how); });
      S = [{ label: whatOf(D, X), values: totS.map(function (t) { return t.v; }), texts: totS.map(function (t) { return textOf(t.v, t.n, unit); }) }];
      rec.kind = 'lines';
    }
    // a stacked area cannot draw a hole: the first and last periods where a part rests on too few rows are left off the chart (and
    // said), never filled (19 Oct 2026: Amazon reviews by category from 1999 fell back to their table on the thin early years)
    var cut0 = 0, cut1 = 0;
    if (X.draw === 'area' && !asLine) {
      var holed = function (i) { return S.some(function (s) { return s.values[i] === null; }); };
      while (cut0 < x.length && holed(cut0)) cut0++;
      while (cut1 < x.length - cut0 && holed(x.length - 1 - cut1)) cut1++;
      if ((cut0 || cut1) && x.length - cut0 - cut1 >= 2) {
        var keepI = function (a) { return a.slice(cut0, a.length - cut1); };
        S.forEach(function (s) { s.values = keepI(s.values); s.texts = keepI(s.texts); });
        x = keepI(x);
      } else { cut0 = cut1 = 0; }
    }
    // a quarter or year the range covers only in part (its first or last): said beside it, never scaled or filled
    // (only when months are put together: a yearly series' own years are whole)
    var coarse = sp.grain !== nativeOf(D), full = !coarse ? 0 : sp.grain === 'quarter' ? 3 : sp.grain === 'year' ? 12 : 1, held = {};
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
    var sub = coarse ? 'By ' + sp.grain + ': the range holds ' + sp.months.length + ' months. ' : '';
    if (part.length) sub += part.map(function (p) { return periodWord(p) + ' holds ' + held[p] + ' of its ' + full + ' months'; }).join('; ') + ' in this range: ' + (part.length === 1 ? 'its figure is' : 'their figures are') + ' not comparable with a whole ' + sp.grain + '. ';
    if (asLine) sub += 'Drawn as the total: every ' + (X.by || 'group') + ' has a ' + sp.grain + ' on too few rows to stack. ';
    if (cut0 || cut1) sub += 'Shown ' + periodWord(x[0]) + ' to ' + periodWord(x[x.length - 1]) + ': the ' + sp.grain + 's outside rest on too few rows in a part. ';
    if (X.draw === 'area' && X.bkey && S.some(function (s) { return s.label === 'other'; })) sub += 'Groups with a ' + sp.grain + ' on fewer than 5 rows are counted in \u201cother\u201d. ';
    if (gaps.length) sub += gaps.length + ' ' + (gaps.length === 1 ? sp.grain : sp.grain + 's') + ' with no rows ' + (gaps.length === 1 ? 'is' : 'are') + ' left as ' + (gaps.length === 1 ? 'a gap' : 'gaps') + '. ';
    if (sup) { sub += 'A point on fewer than 5 rows is not drawn (“<5” in the table).'; rec.suppressed = { cells: sup, why: 'Points that rest on fewer than 5 rows are not drawn: the table shows “<5”.' }; }
    if (sub) rec.subtitle = sub.trim();
    rec.summary = (X.why ? X.why.replace(/\.?$/, '. ') : '') + periodWord(x[0]) + ' to ' + periodWord(x[x.length - 1]) + '.';
    return rec;
  };
  // a measure's change from each period to the next ('change'), and its average (a level) or total (a flow) by decade, or by year
  // for a shorter range ('periods'), in time order (nl_dashboard OWN_CHARTS): a line for a long run of changes, bars for up to 20
  function ownRecord(D, X, cells, unit) {
    var sp = span(D, cells);
    if (!sp) return { kind: 'table', chart: X.chart, title: X.title, summary: 'No rows match the filters.', table: { cols: ['Period'], rows: [] }, empty: true };
    var rec = base(X, 'hbar', D), g = sp.grain, xl = { month: 'Month', quarter: 'Quarter', year: 'Year' }[g];
    if (X.draw === 'change') {
      var pv = periodValues(D, cells, X.mkey, g), ch = [];
      for (var i = 1; i < pv.length; i++) ch.push({ p: pv[i].p, v: pv[i].v !== null && pv[i - 1].v !== null ? pv[i].v - pv[i - 1].v : null });
      if (ch.length < 2) return { kind: 'table', chart: X.chart, title: X.title, empty: true, summary: 'A change needs three ' + g + 's or more in the range.', table: { cols: [xl], rows: [] } };
      var label = 'Change from the ' + g + ' before';
      rec.table = { cols: [xl, label], rows: ch.map(function (c) { return [periodWord(c.p), c.v === null ? 'no figure' : fmtChange(c.v, unit)]; }) };
      if (ch.length <= 20 && ch.every(function (c) { return c.v !== null; })) {
        rec.data = { series: [label], rows: ch.map(function (c) { return { label: periodWord(c.p), values: [c.v], texts: [fmtChange(c.v, unit)] }; }), order: 'given' };
        rec.levels = ch.map(function (c) { return periodWord(c.p); });
      } else {
        rec.kind = 'lines';
        rec.data = { x: ch.map(function (c) { return c.p; }), x_label: xl, grain: g, series: [{ label: label, values: ch.map(function (c) { return c.v; }) }] };
      }
      var up = ch.filter(function (c) { return c.v > 0; }).length, dn = ch.filter(function (c) { return c.v < 0; }).length;
      rec.subtitle = 'Rose in ' + up + ' and fell in ' + dn + ' of ' + ch.length + ' ' + g + 's' + (unit === '%' ? '; a change of a percentage is in percentage points (pp)' : '') + '.';
      rec.summary = (X.why ? X.why.replace(/\.?$/, '. ') : '') + periodWord(ch[0].p) + ' to ' + periodWord(ch[ch.length - 1].p) + '.';
      return rec;
    }
    // by decade when the range spans 20 years or more, else by year
    var years = sp.months.length / 12, dec = years >= 20, flow = (function () { var j = measIdx(D, X.mkey); return j >= 0 && D.cube.measures[j].flow; })();
    var nd = D.cube.dims.length, j = measIdx(D, X.mkey), by = {}, small = smallOf(D);
    cells.forEach(function (c) {
      if (c[0] < 0) return;
      var y = +D.cube.months[c[0]].slice(0, 4), key = dec ? String(Math.floor(y / 10) * 10) : String(y), a = by[key] || (by[key] = { s: 0, k: 0, ys: {} });
      a.s += c[2 + nd + 2 * j]; a.k += c[3 + nd + 2 * j]; a.ys[y] = 1;
    });
    var keys = Object.keys(by).sort().slice(-20), out = [], off = [];
    keys.forEach(function (k) {
      var a = by[k], lab = dec ? k + 's' : k, held = Object.keys(a.ys).length;
      if (a.k < small) { off.push(lab); return; }
      var v = flow ? a.s : a.s / a.k;
      out.push({ label: lab + (dec && held < 10 ? ' (' + held + ' of 10 years)' : ''), values: [v], texts: [fmt(v, unit)] });
    });
    if (!out.length) return { kind: 'table', chart: X.chart, title: X.title, empty: true, summary: 'No ' + (dec ? 'decade' : 'year') + ' has enough rows under these filters.', table: { cols: [dec ? 'Decade' : 'Year'], rows: [] } };
    var what = (flow ? 'Total ' : 'Average ') + X.measure;
    rec.data = { series: [what], rows: out, order: 'given' };
    rec.levels = out.map(function (r) { return r.label; });
    rec.table = { cols: [dec ? 'Decade' : 'Year', what], rows: out.map(function (r) { return [r.label, r.texts[0]]; }).concat(off.map(function (l) { return [l, '<' + small]; })) };
    var part = out.filter(function (r) { return / of 10 years\)$/.test(r.label); });
    rec.subtitle = (dec ? 'Each decade\'s ' : 'Each year\'s ') + (flow ? 'total' : 'average') + ', in time order' + (part.length ? '; a decade the data cover only in part says how many of its years it holds' : '') + '.';
    rec.summary = X.why ? X.why.replace(/\.?$/, '.') : '';
    return rec;
  }
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
        out.push({ label: z.level, values: [r.v], texts: [fmt(r.v, unit)], n: r.n });
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

  /* ------------------------------------------------------------ what stands out (computed here, under the filters) */
  // Sentences the engine's rules write from the cube for the headline measure (and its split, and how the measures move together),
  // recomputed on every filter: where it is now and its last move, its high and low, its change over the range, its largest rise
  // and fall from one period to the next, a run of rises or falls, the recent average against the whole range's. No AI writes a figure.
  function pctW(v) { return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1) + '%'; }
  // the series the sentences read: the first tile or time chart of a measure, else a time chart of the rows (a count)
  function lead(D) {
    var xs = D.kpis.concat(D.charts).filter(function (x) { return x.mkey && x.draw !== 'static' && x.draw !== 'bar' && x.draw !== 'grouped'; });
    if (xs[0]) return xs[0];
    return D.charts.filter(function (x) { return x.draw === 'line' && !x.mkey && !x.bkey; })[0] || null;
  }
  NLD.insights = function (D, F) {
    var out = [], X = lead(D), g = null;
    if (X && D.cube.months.length) seriesSay(D, F, X, out, function (gg) { g = gg; });
    splitSay(D, F, out);
    if (g) togetherSay(D, F, g, out);
    reportSay(D, out);
    return out.slice(0, 10);
  };
  function seriesSay(D, F, X, out, setG) {
    var cells = pick(D, F || {}, X.where), sp = span(D, cells, F && F.grain);
    if (!sp) return;
    setG(sp.grain);
    var unit = X.mkey ? unitOf(D, X) : '', g = sp.grain, gs = g + 's', nat = g === nativeOf(D);
    var name = !X.mkey ? 'the number of ' + String((D.grain && D.grain.noun) || 'rows').toLowerCase() :
      unit === '%' ? String(X.measure).replace(/\s*\(%\)$/, '') : X.measure;      // the name ends "(%)", the figure says "%"
    var pv = periodValues(D, cells, X.mkey, g).filter(function (x) { return x.v !== null; });
    if (pv.length >= 2) {
      var a = pv[pv.length - 2], b = pv[pv.length - 1], d = b.v - a.v;
      out.push((nat ? '' : 'By ' + g + ': ') + 'In ' + periodWord(b.p) + ', ' + name + ' was ' + fmt(b.v, unit) + ', ' +
        (d === 0 ? 'unchanged from ' + periodWord(a.p) : (d > 0 ? 'up ' : 'down ') + fmtChange(Math.abs(d), unit).replace(/^\+/, '') + ' from ' + periodWord(a.p) +
          (unit !== '%' && a.v ? ' (' + pctW(d / Math.abs(a.v) * 100) + ')' : '')) + '.');
    }
    if (pv.length >= 3) {
      var hi = pv[0], lo = pv[0], last = pv[pv.length - 1], first = pv[0];
      pv.forEach(function (x) { if (x.v >= hi.v) hi = x; if (x.v <= lo.v) lo = x; });
      out.push((hi === last ? 'The latest ' + g + ' is the highest in the range shown: ' : 'Highest: ') + fmt(hi.v, unit) + ' in ' + periodWord(hi.p) +
        (lo === last ? '; the latest is also the lowest.' : '. Lowest: ' + fmt(lo.v, unit) + ' in ' + periodWord(lo.p) + '.'));
      var tot = last.v - first.v;
      // a first-to-last change smaller than the series' usual move from one period to the next is noise, and is said to be (19 Oct
      // 2026: Amazon ratings "fell 0.13 from 2000 to 2023" beside the report's "no clear trend")
      var moves = []; for (var q = 1; q < pv.length; q++) moves.push(Math.abs(pv[q].v - pv[q - 1].v));
      moves.sort(function (a, b) { return a - b; });
      var usual = moves.length ? moves[Math.floor(moves.length / 2)] : 0, small = usual > 0 && Math.abs(tot) < 2 * usual;
      out.push('From ' + periodWord(first.p) + ' to ' + periodWord(last.p) + ' it ' + (tot > 0 ? (small ? 'ended ' : 'rose ') : tot < 0 ? (small ? 'ended ' : 'fell ') : 'ended where it began, ') +
        (tot ? fmtChange(Math.abs(tot), unit).replace(/^\+/, '') + (small ? (tot > 0 ? ' higher' : ' lower') : '') + (unit !== '%' && first.v ? ' (' + pctW(tot / Math.abs(first.v) * 100) + ')' : '') : '') +
        ', over ' + pv.length + ' ' + gs + ' with a figure' +
        (small ? ': less than twice its usual move from one ' + g + ' to the next (' + fmtChange(usual, unit).replace(/^\+/, '') + '), so within its normal swing, not a clear change.' : '.'));
      var steps = [];
      for (var i = 1; i < pv.length; i++) steps.push({ p: pv[i].p, q: pv[i - 1].p, d: pv[i].v - pv[i - 1].v });
      var up = steps.reduce(function (m, s) { return !m || s.d > m.d ? s : m; }, null), dn = steps.reduce(function (m, s) { return !m || s.d < m.d ? s : m; }, null);
      if (up && up.d > 0) out.push('Largest rise from one ' + g + ' to the next: ' + fmtChange(up.d, unit) + ' in ' + periodWord(up.p) + (dn && dn.d < 0 ? '; largest fall: ' + fmtChange(dn.d, unit) + ' in ' + periodWord(dn.p) : '') + '.');
      else if (dn && dn.d < 0) out.push('Largest fall from one ' + g + ' to the next: ' + fmtChange(dn.d, unit) + ' in ' + periodWord(dn.p) + '.');
      var run = 0, sg = 0;
      for (var k = steps.length - 1; k >= 0; k--) { var t = steps[k].d > 0 ? 1 : steps[k].d < 0 ? -1 : 0; if (!t || (sg && t !== sg)) break; sg = t; run++; }
      if (run >= 3) out.push('It has ' + (sg > 0 ? 'risen' : 'fallen') + ' ' + run + ' ' + gs + ' in a row, up to ' + periodWord(last.p) + '.');
      if (pv.length >= 20) {
        var rec10 = pv.slice(-10), avg = function (xs) { return xs.reduce(function (s, x) { return s + x.v; }, 0) / xs.length; };
        out.push('The last 10 ' + gs + ' averaged ' + fmt(avg(rec10), unit) + ', against ' + fmt(avg(pv), unit) + ' over all ' + pv.length + '.');
      }
    }
  }
  // the split: the group with the most and the least (the dashboard's first two bar charts)
  function splitSay(D, F, out) {
    D.charts.filter(function (c) { return c.draw === 'bar' && c.bkey; }).slice(0, 2).forEach(function (B) {
      var r = barRecord(D, B, pick(D, F || {}, B.where), unitOf(D, B)), all = (r.data && r.data.rows) || [], rows = all.filter(function (x) { return x.label !== 'other' && x.label !== '(blank)'; });
      if (rows.length < 2) return;
      // a share is of the whole (every group, "other" and blanks included): Chicago's police read "56.8% of the groups shown" beside
      // the report's 49.8% of the salary bill (19 Oct 2026)
      var cellsB = pick(D, F || {}, B.where), wholeV = agg(D, cellsB, B.agg === 'count' || !B.mkey ? null : B.mkey, B.agg === 'count' || !B.mkey ? 'count' : 'sum').v;
      var whole = function () { return wholeV || 0; };
      var tot = B.agg === 'sum' ? whole() : 0, top = rows[0], bot = rows[rows.length - 1];
      var cnt = B.agg === 'count' || !B.mkey, share = cnt ? whole() : tot;
      // an average names its rows: a top group on 42 of 19,789 reviews is not the market's verdict; a total or a count says its share
      var note = function (x, first) {
        var bits = [];
        if (!cnt && x.n) bits.push(x.n.toLocaleString('en-US') + ' rows');
        if (first && share > 0 && (B.agg === 'sum' || cnt)) bits.push((x.values[0] / share * 100).toFixed(1) + '% of the total');
        return bits.length ? ' (' + bits.join(', ') + ')' : '';
      };
      out.push(B.by + ': ' + top.label + ' has the ' + (B.agg === 'sum' ? 'largest total' : cnt ? 'most ' + String((D.grain && D.grain.noun) || 'rows').toLowerCase() : 'highest average') + ', ' + top.texts[0] + note(top, true) +
        '; ' + bot.label + ' the ' + (B.agg === 'sum' ? 'smallest' : cnt ? 'fewest' : 'lowest') + ', ' + bot.texts[0] + note(bot, false) + '.');
    });
  }
  // the report's own ranking charts on the tab (not filtered): their top and bottom, as the report computed them
  function reportSay(D, out) {
    D.charts.filter(function (c) { return c.draw === 'static' && c.record && c.record.kind === 'hbar'; }).slice(0, 2).forEach(function (X) {
      var d = X.record.data || {}, rows = (d.rows || []).filter(function (r) { return r && r.label !== 'other' && r.texts && r.texts[0] && r.values && typeof r.values[0] === 'number'; });
      // a ranking only: a histogram's bins ("5 to 5.5") have no highest or lowest member
      if (rows.length < 3 || (d.series || []).length !== 1 || rows.every(function (r) { return /^[−-]?[\d.,]+ to [−-]?[\d.,]+$/.test(r.label); })) return;
      var hi = rows[0], lo = rows[0];
      rows.forEach(function (r) { if (r.values[0] > hi.values[0]) hi = r; if (r.values[0] < lo.values[0]) lo = r; });
      out.push(X.title + ' (every row, not filtered): highest ' + hi.label + ', ' + hi.texts[0] + '; lowest ' + lo.label + ', ' + lo.texts[0] + '.');
    });
  }
  // how the measures move together: the correlation of their changes from one period to the next (never of their levels: two
  // trending series always look related)
  function togetherSay(D, F, g, out) {
    var gs = g + 's';
    var ms = []; D.kpis.concat(D.charts).forEach(function (x) { if (x.mkey && x.draw !== 'static' && ms.map(function (m) { return m.mkey; }).indexOf(x.mkey) < 0) ms.push(x); });
    for (var i2 = 0; i2 < ms.length && i2 < 3; i2++) for (var j2 = i2 + 1; j2 < ms.length && j2 < 3; j2++) {
      var A = periodValues(D, pick(D, F || {}, null), ms[i2].mkey, g), Bv = periodValues(D, pick(D, F || {}, null), ms[j2].mkey, g), byB = {};
      Bv.forEach(function (x) { byB[x.p] = x.v; });
      var da = [], db = [];
      for (var q = 1; q < A.length; q++) {
        var p0 = A[q - 1].p, p1 = A[q].p;
        if (A[q].v === null || A[q - 1].v === null || byB[p1] === undefined || byB[p0] === undefined || byB[p1] === null || byB[p0] === null) continue;
        da.push(A[q].v - A[q - 1].v); db.push(byB[p1] - byB[p0]);
      }
      if (da.length < 10) continue;
      var mA = da.reduce(function (s, v) { return s + v; }, 0) / da.length, mB = db.reduce(function (s, v) { return s + v; }, 0) / db.length, sab = 0, saa = 0, sbb = 0;
      da.forEach(function (v, z) { sab += (v - mA) * (db[z] - mB); saa += (v - mA) * (v - mA); sbb += (db[z] - mB) * (db[z] - mB); });
      var rr = saa && sbb ? sab / Math.sqrt(saa * sbb) : 0;
      if (Math.abs(rr) >= 0.5) out.push(ms[i2].measure + ' and ' + ms[j2].measure + ': their changes from one ' + g + ' to the next ' + (rr > 0 ? 'move together' : 'move in opposite directions') +
        ' (correlation ' + rr.toFixed(2) + ' over ' + da.length + ' ' + gs + '). Moving together is not proof that one drives the other.');
    }
  }
  function insightsCard() {
    return '<section class="tr-card nld-insights" aria-labelledby="nld-ins-h"><h4 id="nld-ins-h">What stands out</h4><ul data-nld="insights"></ul>' +
      '<p class="note">Computed in this browser from the engine\'s aggregate, under the filters shown; no AI wrote these figures.</p></section>';
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
        // past five years of months the lists hold years (a 24-year review file listed 287 months; 19 Oct 2026): From takes a year's
        // first month in the data, To its last
        var byYear = nativeOf(D) === 'month' && ms.length > 60, firstOf = {}, lastOf = {};
        ms.forEach(function (m) { var y = m.slice(0, 4); if (!firstOf[y]) firstOf[y] = m; lastOf[y] = m; });
        var o = function (sel, end) {
          if (byYear) return Object.keys(firstOf).sort().map(function (y) { var v = end ? lastOf[y] : firstOf[y]; return '<option value="' + v + '"' + (v === sel ? ' selected' : '') + '>' + y + '</option>'; }).join('');
          return ms.map(function (m) { return '<option value="' + m + '"' + (m === sel ? ' selected' : '') + '>' + esc(periodWord(periodOf(m, nativeOf(D)))) + '</option>'; }).join('');
        };
        h += '<fieldset class="nld-date"><legend>' + esc(f.label ? (byYear ? 'Years' : f.label) + ' (' + f.column + ')' : f.column + ' range') + '</legend><label>From <select data-nld-f="from">' + o(ms[0], false) + '</select></label>' +
          '<label>To <select data-nld-f="to">' + o(ms[ms.length - 1], true) + '</select></label></fieldset>';
      } else if (f.kind === 'category') {
        var j = dimIdx(D, f.key);
        if (j < 0) return;
        h += '<label class="nld-cat">' + esc(f.label || f.column) + ' <select data-nld-f="cat" data-key="' + esc(f.key) + '"><option value="">All</option>' +
          D.cube.dims[j].levels.map(function (l) { return '<option value="' + esc(l) + '">' + esc(l) + '</option>'; }).join('') + '</select></label>';
      }
    });
    // drill: the step the time charts read (Power BI's drill up and down); Auto is the span's own
    if (ms.length > 1 && D.charts.some(function (X) { return X.draw === 'line' || X.draw === 'area' || X.draw === 'multiples'; })) {
      var gs = ['year', 'quarter', 'month'].filter(function (g) { return GRANK[g] >= GRANK[nativeOf(D)]; });
      if (gs.length > 1) h += '<label class="nld-cat nld-drill">Time by <select data-nld-f="grain"><option value="">Auto</option>' +
        gs.map(function (g) { return '<option value="' + g + '">' + g.charAt(0).toUpperCase() + g.slice(1) + '</option>'; }).join('') + '</select></label>';
    }
    return h + '<button type="button" class="tb" data-nld="clear">Clear filters</button><p class="nld-active note" id="nld-active" role="status" aria-live="polite"></p></form>';
  }
  function tileHtml(D, K, F) {
    var r = NLD.kpi(D, K, F), unit = unitOf(D, K), small = smallOf(D);
    var v = r.value === null ? (r.n > 0 && r.suppressed ? '<' + small : '–') : K.agg === 'change' ? fmtChange(r.value, unit) : fmt(r.value, K.agg === 'count' ? '' : unit);
    var sub = tileSub(D, K, r, unit, small), tr = tileTrend(D, K, F, unit);
    return '<div class="tr-kpi nld-kpi" id="nld-x-' + esc(K.id) + '" tabindex="-1"><span class="k-lab">' + esc(K.label) + '</span><span class="k-val">' + esc(v) + '</span><span class="k-sub">' + esc(sub) + '</span>' +
      (tr.var ? '<span class="k-var">' + esc(tr.var) + '</span>' : '') + tr.spark + '</div>';
  }
  // a tile's trend (Power BI's KPI card): its measure period by period as a sparkline, and for a total, average or count its last
  // complete period against the one before. The arrow carries no colour: whether up is good depends on the measure. A period still
  // filling (a year of 9 months) is not compared. The sparkline is decoration; the variance line says the same in words.
  var SPARK_N = 24;
  function tileTrend(D, K, F, unit) {
    var out = { spark: '', var: '' };
    if (!D.cube.months.length || (K.mkey && measIdx(D, K.mkey) < 0) || (!K.mkey && K.agg !== 'count')) return out;
    var cells = pick(D, F || {}, K.where), g = tileGrain(D, cells);
    var pv = periodValues(D, cells, K.agg === 'count' ? null : K.mkey, g), mj = K.mkey ? measIdx(D, K.mkey) : -1;
    // an average tile of a flow reads each period's average, never its total (20 Oct 2026: "Average helpful votes: 2022: 922")
    if (K.agg === 'average' && mj >= 0 && D.cube.measures[mj].flow) pv = pv.map(function (x) { return { p: x.p, n: x.n, v: x.v === null || !x.n ? null : x.v / x.n }; });
    var held = heldMonths(D, cells, g), lastM = null;
    cells.forEach(function (c) { if (c[0] >= 0 && (lastM === null || D.cube.months[c[0]] > lastM)) lastM = D.cube.months[c[0]]; });
    // the data's last month still filling (cube.last_month_full: data to the 15th): its period is not compared or drawn
    var partial = D.cube.last_month_full === false && lastM === D.cube.months[D.cube.months.length - 1];
    if ((held || partial) && pv.length) pv = pv.slice(0, -1);
    var have = pv.filter(function (x) { return x.v !== null; }).slice(-SPARK_N);
    if (have.length >= 3) {
      var lo = Math.min.apply(null, have.map(function (x) { return x.v; })), hi = Math.max.apply(null, have.map(function (x) { return x.v; }));
      var W = 120, H = 26, k = have.length - 1, sy = hi > lo ? (H - 4) / (hi - lo) : 0;
      var pts = have.map(function (x, i) { return (i * W / k).toFixed(1) + ',' + (H - 2 - (sy ? (x.v - lo) * sy : (H - 4) / 2)).toFixed(1); });
      out.spark = '<svg class="k-spark" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" aria-hidden="true" focusable="false"><polyline points="' + pts.join(' ') +
        '" fill="none" stroke="var(--ts-1)" stroke-width="1.6" stroke-linejoin="round"/><circle cx="' + pts[k].split(',')[0] + '" cy="' + pts[k].split(',')[1] + '" r="2.2" fill="var(--ts-1)"/></svg>';
    }
    if ((K.agg === 'sum' || K.agg === 'average' || K.agg === 'count') && pv.length >= 2) {
      var a = pv[pv.length - 2], b = pv[pv.length - 1];
      if (a.v !== null && b.v !== null) {
        var u = K.agg === 'count' ? '' : unit, d = b.v - a.v, pct = a.v ? d / Math.abs(a.v) * 100 : null;
        var arrow = d > 0 ? '\u25B2 ' : d < 0 ? '\u25BC ' : '';
        out.var = periodWord(b.p) + ': ' + fmt(b.v, u) + (K.agg === 'average' ? ' (' + b.n.toLocaleString('en-US') + ' values)' : '') + ', ' + arrow + (pct !== null && isFinite(pct) && u !== '%' ? Math.abs(pct).toFixed(1) + '%' : fmtChange(d, u).replace(/^[+−]/, '')) +
          ' ' + (d > 0 ? 'above' : d < 0 ? 'below' : 'level with') + ' ' + periodWord(a.p);
      }
    }
    return out;
  }
  // the words under a tile's figure: when it is from, against what, on how many rows
  function tileSub(D, K, r, unit, small) {
    if (r.value === null) {
      if (r.suppressed) return 'fewer than ' + small + ' rows: not shown';
      if (K.agg === 'change' && r.period) return periodWord(r.period) + ': no earlier ' + nativeOf(D) + ' to compare with';
      return 'no rows under these filters';
    }
    var vals = r.n.toLocaleString('en-US') + (K.agg === 'count' ? ' rows' : r.n === 1 ? ' value' : ' values');
    if (K.agg === 'change') {
      var pct = r.pct !== null && r.pct !== undefined && isFinite(r.pct) && unit !== '%' ? ' (' + (r.pct >= 0 ? '+' : '−') + Math.abs(r.pct).toFixed(1) + '%)' : '';
      return periodWord(r.period) + (r.held ? ' (' + r.held + ' of its ' + (r.period.length === 4 ? 12 : 3) + ' months)' : '') + ' against ' + periodWord(r.base_period) + ' (' + fmt(r.base, unit) + ')' + pct;
    }
    if (K.agg === 'latest' || K.agg === 'peak' || K.agg === 'low') {
      var when = r.period ? periodWord(r.period) + (r.held ? ' (' + r.held + ' of its ' + (r.period.length === 4 ? 12 : 3) + ' months)' : '') : monthWord(r.month);
      return 'in ' + when + (D.observations ? '' : ', ' + vals);
    }
    return vals;
  }
  function figHtml(X) {
    var st = X.draw === 'static';
    return '<figure class="visual nld-fig" data-table="1" id="nld-x-' + esc(X.id) + '" tabindex="-1" data-draw="' + esc(X.draw) + '"><figcaption>' + esc(X.title) + '</figcaption>' +
      (st ? '<p class="note nld-static">Not filtered: the engine\'s own chart of every row it kept.</p>' : '') +
      '<div class="viz" id="nld-v-' + esc(X.id) + '"></div><div class="nld-fignote"></div></figure>';
  }
  function notes(D, F, local) {
    var R = D.remarks, rows = R.rows, act = activeWords(D, F);
    var defs = D.kpis.concat(D.charts, (D.visuals || []).map(function (V) { return { title: V.title, definition: 'the AI\'s query over every row the engine kept (not filtered), drawn with its own Vega-Lite design: ' + V.sql }; })).map(function (x) { return '<li><strong>' + esc(x.label || x.title) + ':</strong> ' + esc(x.definition || (x.draw === 'static' ? 'the engine\'s own chart record, over every row it kept (not filtered).' : '')) + '</li>'; }).join('');
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
  function shownRange(D, F) { var ms = D.cube.months, w = function (m) { return periodWord(periodOf(m, nativeOf(D))); }; return ms.length ? w(F.from || ms[0]) + ' to ' + w(F.to || ms[ms.length - 1]) : 'no dates'; }
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
  /* ------------------------------------------------------------ what drives it (AI Analyst v2 phase D): key influencers and a decomposition */
  // the measure a decomposition splits: the first tile's flow measure (a total adds up across groups), else the rows
  function decompMeasure(D) {
    for (var i = 0; i < D.kpis.length; i++) { var j = D.kpis[i].mkey ? measIdx(D, D.kpis[i].mkey) : -1; if (j >= 0 && D.cube.measures[j].flow) return D.kpis[i]; }
    return null;
  }
  function driversHtml(D) {
    var inf = D.influencers && D.influencers.items && D.influencers.items.length ? D.influencers : null;
    var canSplit = D.cube.months.length > 1 && D.cube.dims.length;
    if (!inf && !canSplit) return '';
    var h = '<section class="tr-card nld-drivers" aria-labelledby="nld-drv-h"><h4 id="nld-drv-h">What drives it</h4>';
    if (canSplit) {
      h += '<h5>What made up the last change</h5><p class="note">The change in the last whole ' + esc(nativeOf(D)) + ' against the one before, split by a column; ' +
        'the parts add up to the change. It follows the filters.</p><label class="nld-cat">Split by <select data-nld-f="split">' +
        D.cube.dims.map(function (d) { return '<option value="' + esc(d.key) + '">' + esc(d.column) + '</option>'; }).join('') + '</select></label>' +
        '<div data-nld="decomp" aria-live="polite"></div>';
    }
    if (inf) {
      var j = -1; D.cube.measures.forEach(function (m, i) { if (m.column === inf.measure) j = i; });
      var u = j >= 0 ? D.cube.measures[j].unit || '' : '', big = Math.max.apply(null, inf.items.map(function (x) { return Math.abs(x.diff); }));
      h += '<h5>Key influencers of ' + esc(inf.measure) + '</h5><p class="note">' + esc(inf.note) + ' Each group on ' + inf.rows_min + ' rows or more; only a difference whose 95% interval excludes zero is listed, the interval widened for the ' + (inf.tests || 1) + ' groups compared (Bonferroni).</p><ul class="nld-infl">' +
        inf.items.map(function (x) {
          var w = big ? Math.max(2, Math.round(Math.abs(x.diff) / big * 100)) : 0;
          return '<li><span class="nld-infl-t">When <strong>' + esc(x.column) + '</strong> is <strong>' + esc(x.level) + '</strong>, the average ' + esc(inf.measure) + ' is ' + esc(fmt(x.mean, u)) +
            (x.ratio ? ' (' + x.ratio.toFixed(2) + '\u00d7 the rest\u2019s ' + esc(fmt(x.rest_mean, u)) + ')' : ' (the rest\u2019s: ' + esc(fmt(x.rest_mean, u)) + ')') +
            '; difference ' + esc(fmtChange(x.diff, u)) + ', 95% interval ' + esc(fmtChange(x.lo, u)) + ' to ' + esc(fmtChange(x.hi, u)) + '; ' + x.rows.toLocaleString('en-US') + ' rows.</span>' +
            '<span class="nld-infl-b" aria-hidden="true"><span style="width:' + w + '%;background:' + (x.diff >= 0 ? 'var(--div-pos)' : 'var(--div-neg)') + '"></span></span></li>';
        }).join('') + '</ul>';
    }
    return h + '</section>';
  }
  // the decomposition under the filters: [{level, a, b, d}] for the last whole period against the one before
  NLD.decomp = function (D, F, dimKey) {
    var K = decompMeasure(D), j = dimIdx(D, dimKey);
    if (j < 0) return null;
    var cells = pick(D, F || {}, null), g = tileGrain(D, cells), nd = D.cube.dims.length, mj = K ? measIdx(D, K.mkey) : -1;
    var ps = {}; cells.forEach(function (c) { if (c[0] >= 0) ps[periodOf(D.cube.months[c[0]], g)] = 1; });
    var order = Object.keys(ps).sort(), lastM = null;
    cells.forEach(function (c) { if (c[0] >= 0 && (lastM === null || D.cube.months[c[0]] > lastM)) lastM = D.cube.months[c[0]]; });
    if (heldMonths(D, cells, g) || (D.cube.last_month_full === false && lastM === D.cube.months[D.cube.months.length - 1])) order = order.slice(0, -1);
    if (order.length < 2) return null;
    var pa = order[order.length - 2], pb = order[order.length - 1], L = D.cube.dims[j].levels, a = L.map(function () { return 0; }), b = L.map(function () { return 0; });
    cells.forEach(function (c) {
      if (c[0] < 0) return;
      var p = periodOf(D.cube.months[c[0]], g), v = mj >= 0 ? c[2 + nd + 2 * mj] : c[1 + nd];
      if (p === pa) a[c[1 + j]] += v; else if (p === pb) b[c[1 + j]] += v;
    });
    var parts = L.map(function (l, i) { return { level: l, a: a[i], b: b[i], d: b[i] - a[i] }; }).filter(function (x) { return x.a || x.b; })
      .sort(function (x, y) { return Math.abs(y.d) - Math.abs(x.d); });
    var ta = a.reduce(function (s, v) { return s + v; }, 0), tb = b.reduce(function (s, v) { return s + v; }, 0);
    return { measure: K ? 'Total ' + K.measure : 'Rows', unit: K ? unitOf(D, K) : '', pa: pa, pb: pb, ta: ta, tb: tb, parts: parts };
  };
  function decompHtml(D, r) {
    if (!r) return '<p class="note">Not enough whole periods under these filters to compare.</p>';
    var big = Math.max.apply(null, r.parts.map(function (x) { return Math.abs(x.d); }).concat([1e-12])), u = r.unit, top = r.parts.slice(0, 8), rest = r.parts.slice(8);
    var restD = rest.reduce(function (s, x) { return s + x.d; }, 0);
    var row = function (lab, d) { var w = Math.max(1, Math.round(Math.abs(d) / big * 100)); return '<li><span class="nld-dc-l">' + esc(lab) + '</span><span class="nld-dc-b" aria-hidden="true"><span style="width:' + w + '%;background:' + (d >= 0 ? 'var(--div-pos)' : 'var(--div-neg)') + '"></span></span><span class="nld-dc-v">' + esc(fmtChange(d, u) || '0') + '</span></li>'; };
    return '<p><strong>' + esc(r.measure) + '</strong>: ' + esc(fmt(r.ta, u)) + ' in ' + esc(periodWord(r.pa)) + ', ' + esc(fmt(r.tb, u)) + ' in ' + esc(periodWord(r.pb)) +
      ' (' + esc(fmtChange(r.tb - r.ta, u) || 'no change') + ').</p><ul class="nld-dc">' + top.map(function (x) { return row(x.level, x.d); }).join('') +
      (rest.length ? row(rest.length + ' more', restD) : '') + '</ul>';
  }
  NLD.html = function (D, local) {
    var I = ids(D), body = '';
    D.layout.forEach(function (row) {
      var ks = row.filter(function (i) { return I[i] && !I[i].draw; }), cs = row.filter(function (i) { return I[i] && I[i].draw; });
      if (ks.length) body += '<div class="tr-kpis nld-kpis" data-nld="kpis" data-ids="' + esc(ks.join(' ')) + '"></div>';
      if (cs.length) body += '<div class="nld-row">' + cs.map(function (i) { return figHtml(I[i]); }).join('') + '</div>';
    });
    var card = NLD.cardHost && !local ? '<p class="nld-counts-link note">What this dashboard counts: <a href="#nld-counts-h">what each column means and what one row is</a> (step 1, editable).</p>' : countsCard(D);
    var vis = window.NLVG && D.visuals && D.visuals.length ? window.NLVG.html(D) : '';   // the AI's own visuals (64-vega.js): not filtered
    vis += driversHtml(D);
    return '<div class="nld" data-v="' + esc(D.version) + '">' + head(D) + card + filtersBar(D) + insightsCard() + body + vis + notes(D, {}, local) + exportsBar(local) + '</div>';
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
      // the time chart a period was picked on keeps every period (the pick is highlighted, not filtered away: Power BI's cross-highlight)
      var FX = F.src === X.id ? Object.assign({}, F, { from: undefined, to: undefined }) : F;
      var rec = X.draw === 'static' ? X.record : NLD.record(D, X, FX);
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
      Array.prototype.forEach.call(box.querySelectorAll('figure.nld-fig svg [data-x].nld-pick'), function (e) { e.classList.remove('nld-pick'); });
      if (F.src && F.pick) Array.prototype.forEach.call(box.querySelectorAll('#nld-x-' + cssId(F.src) + ' svg [data-x="' + F.pick + '"]'), function (e) { e.classList.add('nld-pick'); });
      var ins = box.querySelector('[data-nld="insights"]');
      if (ins) {
        var said = NLD.insights(D, F);
        ins.innerHTML = said.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('');
        var card0 = ins.closest ? ins.closest('.nld-insights') : null;
        if (card0) card0.hidden = !said.length;                   // nothing the engine can say under these filters: no empty card
      }
      var dc = box.querySelector('[data-nld="decomp"]'), ds = box.querySelector('select[data-nld-f="split"]');
      if (dc && ds) dc.innerHTML = decompHtml(D, NLD.decomp(D, F, ds.value));
      var a = activeWords(D, F), ac = box.querySelector('#nld-active');
      if (ac) ac.textContent = a ? 'Filtered: ' + a + '.' : 'Showing everything: ' + shownRange(D, F) + '.';
      var sh = box.querySelector('[data-nld="shown"]'); if (sh) sh.textContent = shownRange(D, F);
      var fl = box.querySelector('[data-nld="filters"]'); if (fl) fl.textContent = a || 'None: every row the engine kept.';
      Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="cat"]'), function (s) { var v = (F.cats[s.getAttribute('data-key')] || [])[0] || ''; if (s.value !== v) s.value = v; });
      if (U.markScrollers) U.markScrollers();
    }
    NLD.state = function () { return F; };
    NLD.setFilter = function (f) { F = { from: f.from, to: f.to, cats: f.cats || {}, grain: f.grain }; var gs = box.querySelector('select[data-nld-f="grain"]'); if (gs) gs.value = f.grain || ''; render(); };
    box.addEventListener('change', function (e) {
      var s = e.target, k = s.getAttribute && s.getAttribute('data-nld-f');
      if (k === 'from' || k === 'to') {
        F[k] = s.value; delete F.src; delete F.pick;
        if (F.from && F.to && F.from > F.to) { var t = F.from; F.from = F.to; F.to = t; box.querySelector('select[data-nld-f="from"]').value = F.from; box.querySelector('select[data-nld-f="to"]').value = F.to; }
        render();
      } else if (k === 'cat') { F.cats[s.getAttribute('data-key')] = s.value ? [s.value] : []; render(); }
      else if (k === 'grain') { F.grain = s.value || undefined; render(); }
      else if (k === 'split') render();
      else if (s.id === 'nld-e-el') fillEdit();
      else if (s.getAttribute && s.getAttribute('data-nld-col') && window.NLColumns) window.NLColumns.set(s.getAttribute('data-nld-col'), { note: s.value.replace(/\s+/g, ' ').trim() });
    });
    box.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('button'), act = b && b.getAttribute('data-nld');
      if (act === 'clear') {
        F = { cats: {} };
        Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="grain"]'), function (s) { s.value = ''; });
        Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="from"]'), function (s) { s.selectedIndex = 0; });
        Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="to"]'), function (s) { s.selectedIndex = s.options.length - 1; });
        render(); return;
      }
      if (act === 'rebuild') return rebuild();
      if (act === 'html' && opts.onHtml) return opts.onHtml(D, F);
      if (act === 'xlsx' && opts.onXlsx) return opts.onXlsx(D, F);
      if (b && b.getAttribute('data-nld-x')) { crossFilter(b.getAttribute('data-nld-x'), b.getAttribute('data-level')); return; }
      // a click on a point of a time chart filters every tile and chart to that period (again: back to every date); the date lists
      // stay the keyboard's way to the same filter
      var pt = e.target.closest && e.target.closest('figure.nld-fig svg [data-x]');
      if (pt && /^(line|area|multiples)$/.test(pt.closest('figure').getAttribute('data-draw') || '')) { periodFilter(pt.getAttribute('data-x'), pt.closest('figure').id.slice(6)); return; }
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
    function periodFilter(p, xid) {
      var g = /^\d{4}-\d{2}$/.test(p) ? 'month' : /^\d{4}-Q\d$/.test(p) ? 'quarter' : /^\d{4}$/.test(p) ? 'year' : null;
      if (!g) return;
      var ms = D.cube.months.filter(function (m) { return periodOf(m, g) === p; });
      if (!ms.length) return;
      var all = D.cube.months, same = F.from === ms[0] && F.to === ms[ms.length - 1];
      F.from = same ? all[0] : ms[0]; F.to = same ? all[all.length - 1] : ms[ms.length - 1];
      if (same) { delete F.src; delete F.pick; } else { F.src = xid; F.pick = p; }
      Array.prototype.forEach.call(box.querySelectorAll('select[data-nld-f="from"], select[data-nld-f="to"]'), function (sel) {
        var v = sel.getAttribute('data-nld-f') === 'from' ? F.from : F.to;
        if (Array.prototype.some.call(sel.options, function (o) { return o.value === v; })) sel.value = v;
      });
      render();
    }
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
      if (j >= 0 && !X.draw && D.cube.months.length) list.push('latest', 'change', 'peak', 'low');
      var g = nativeOf(D), words = { sum: 'total (added up)', average: 'average', count: 'number of rows', latest: 'latest ' + g,
        change: 'change from the ' + g + ' before', peak: 'highest ' + g, low: 'lowest ' + g };
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
      var g = nativeOf(D), j = X.mkey ? measIdx(D, X.mkey) : -1, w = { sum: 'The total of ', average: 'The average of ', latest: 'The latest ' + g + '\'s value of ',
        change: 'The change from the ' + g + ' before in ', peak: 'The highest ' + g + '\'s value of ', low: 'The lowest ' + g + '\'s value of ' }[X.agg];
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
    if (window.NLVG && D.visuals && D.visuals.length) window.NLVG.drawAll(box, D, { local: !!opts.local, snapshot: opts.snapshot });
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
  // the report's map (chart geo_map, 54-nl-geo.js), first on the dashboard: where each place stands, a year at a time on one legend
  function mapCard(rep, panel) {
    var v = (rep && rep.viz && rep.viz.charts) || [], g = v.filter(function (c) { return c && c.kind === 'geo'; })[0];
    if (!g || !window.NLV || !window.NLV.draw) return '';
    var W = Math.max(300, Math.min(980, (panel.clientWidth || 900) - 40));
    var o = window.NLV.draw(g, W);
    return '<section class="tr-card nld-map" aria-label="' + esc(g.title || 'Map') + '"><h4 class="nld-map-h">' + esc(g.title || 'Map') + '</h4>' + o.html + '</section>';
  }
  NLD.show = function (panel, rep) {
    var D = rep.dashboard;
    if (!D || !D.ok) {
      var m0 = mapCard(rep, panel);
      panel.innerHTML = m0 + '<p class="note">' + esc((D && D.why) || 'No dashboard for this file.') + '</p>';
      return;
    }
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
    var mc = mapCard(rep, panel);
    if (mc) { var head0 = panel.querySelector('.nld-head'); if (head0) head0.insertAdjacentHTML('afterend', mc); else panel.insertAdjacentHTML('afterbegin', mc); }
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
    var js = sources(['00-core.js', '51-nl2-charts.js', '55-nl-viz.js', '57-dashboard.js', '64-vega.js']);
    if (js.length < 5) return null;
    var snap = {};
    D.charts.forEach(function (X) {     // a chart the filters do not change is carried as it is drawn now
      if (X.draw !== 'static') return;
      var el = document.getElementById('nld-v-' + X.id); if (el) snap[X.id] = el.innerHTML;
    });
    if (window.NLVG) window.NLVG.snapshot(document, D, snap);   // the AI's visuals, as drawn now (a saved file loads no chart library)
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
        r.value === null ? (r.suppressed ? '<' + smallOf(D) + ': fewer than ' + smallOf(D) + ' rows' : 'no rows') : (K.agg === 'latest' || K.agg === 'peak' || K.agg === 'low' ? 'in ' + (r.period ? periodWord(r.period) : monthWord(r.month)) : K.agg === 'change' ? periodWord(r.period) + ' against ' + periodWord(r.base_period) : ''), K.definition || '']);
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
      if (X.draw === 'bar' || X.draw === 'grouped' || rec.kind === 'hbar') {
        var two = d.series.length === 2;
        head = put([B(X.by || rec.table.cols[0])].concat(d.series.map(function (s) { return B(s + (unit ? ' (' + unit + ')' : '')); })).concat(two ? [B('Change')] : []).concat([B('Note')]));
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
