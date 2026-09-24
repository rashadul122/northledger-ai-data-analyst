/* Report v2: two views from one report (plan §4.1). The MANAGER view is a summary: its first screen
   holds the decision (the engine's bottom line and a decision line per tested claim with its
   interval, strongest evidence first), at most three KPI tiles with evidence-grade badges and one
   "can I trust it" line (its layers fold under it), then the charts the §5 rules chose for this
   file; every finding, what would settle each WATCH and the benchmark strip fold below. No p, q,
   MSIS or DM appears there. The ANALYST view is a paper: executive summary, data and provenance
   (the per-column quality profile), methods, results (the full findings table and the model
   comparison), limitations, recommendations, reproducibility and an appendix with the set-aside
   rows and the evidence ledger. Everything is read from the report (engine/CONTRACT-v2.md); the
   page computes no finding. Every chart names the claims it supports: its link, or a click on the
   chart, highlights them. */
(function () {
  'use strict';
  var U = window.NLU || {};
  var N = {};
  window.NL2 = N;
  var esc = U.esc || function (s) { return String(s); };
  function C() { return window.NLC || {}; }
  function fm() { return C().fmt; }

  var GRADES = { CONFIRMED: ['✓', 'CONFIRMED', 'g-confirmed'], WATCH: ['◐', 'WATCH', 'g-watch'], NOT_ENOUGH_DATA: ['–', 'NOT ENOUGH DATA', 'g-ned'] };
  // a forecast's grade in its own words: a point is not "confirmed", the method is usable or not
  var FC_WORDS = { CONFIRMED: 'USABLE FOR PLANNING', WATCH: 'NOT YET SHOWN USABLE', NOT_ENOUGH_DATA: 'NOT ENOUGH DATA' };
  var ORDER = { CONFIRMED: 0, WATCH: 1, NOT_ENOUGH_DATA: 2 };
  var CHECK_NAMES = { drift_screen: 'the series drifts, so a year-on-year gap is expected (trend screen)', min_effect_p: 'the test that the change is at least the bar did not clear',
    below_recommend_bar: 'under the size the engine needs before calling it a defect', untested_trend: 'no test could run on this series',
    benchmark_condition: 'too few rows a month: the engine confirms no change in a series built from so few rows, by rule',
    composition: 'part of the change is in what the file covers (values that start or stop inside the window), not in the business',
    extreme_rows: 'a few extreme rows drive it: without them the change moves',
    step_elsewhere: 'the series steps once, part-way through, so the two years straddle the step' };
  // a check's words; what the file covers names its own column (properties, stores), not "a category"
  function checkName(c, f) {
    if (c === 'composition' && f && f.composition && f.composition.column) return 'part of the change is in what the file covers (' + plural(f.composition.column).toLowerCase() + ' that start or stop inside the window), not in the business';
    if (c === 'benchmark_condition') return (f && f.power && f.power.routed_rule ? (f.power.routed_rule.kind === 'total' ? 'too few effective rows a month for a monthly total' : 'too few rows a month for a monthly count') : routedShort(f)) + ', by rule';
    return CHECK_NAMES[c] || c.replace(/_/g, ' ');
  }
  var CHIP = { trend_windows: 'trend', fan: 'forecast', replay: 'replay', histogram_windows: 'distribution', ranked_bars: 'ranked', stacked_bars: 'cleaning', benchmark_strip: 'benchmark', table: 'methods' };
  var NOT_MEASURED = { placebo_real: 'the false-confirm rate on real series with no declared change (the placebo)', power_at_bar_matched: 'the power to confirm a change of the bar\'s size in the matched condition',
    cross_env: 'the cross-environment check (the browser engine against the desktop engine, fact by fact)' };
  var LIM_KIND = [['data', 'Data'], ['statistical', 'Statistical'], ['causal', 'Causal'], ['forecast', 'Forecast'], ['external', 'External']];

  function grade(g, kind) {
    var x = GRADES[g] || ['', String(g), ''];
    var word = kind === 'forecast' && FC_WORDS[g] ? FC_WORDS[g] : x[1];
    return '<span class="nl2-grade ' + x[2] + '" data-grade="' + esc(g) + '"><span aria-hidden="true">' + x[0] + '</span> ' + esc(word) + '</span>';
  }
  // WATCH, split: a claim whose interval lies wholly on one side of zero moved; one whose interval
  // includes zero shows no clear movement (plan §2.4)
  function moveOf(f) { return f && f.grade === 'WATCH' && f.watch && f.watch.movement ? f.watch.movement : null; }
  // a claim that stepped where what the file covers changed is not a movement of the business: it ranks
  // with the claims that show no clear movement, and its as-filed interval is not printed
  function moved(m) { return !!m && (m.kind === 'moved' || m.kind === 'cleared'); }
  function strength(f) { var m = moveOf(f); return f.grade === 'CONFIRMED' ? 0 : f.grade === 'WATCH' ? (moved(m) ? 1 : 2) : 3; }
  var MON3 = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function monName(ym) { var m = /^(\d{4})-(\d{2})$/.exec(String(ym || '')); return m ? MON3[+m[2] - 1] + ' ' + m[1] : String(ym || ''); }
  // a column name as a plural noun for the reader: property -> properties, store -> stores
  function plural(w) {
    w = String(w || '').replace(/_/g, ' ').trim();
    if (!w) return 'values';
    var l = w.toLowerCase();
    if (/(s|x|z|ch|sh)$/.test(l)) return /s$/.test(l) ? w : w + 'es';
    if (/[^aeiou]y$/.test(l)) return w.slice(0, -1) + 'ies';
    return w + 's';
  }
  // the page's plain label for a claim (the report's summary.labels), else the engine's claim
  function labelOf(r, f) { var L = (r && r.summary && r.summary.labels) || {}; return (f && L[f.id]) || (f && f.claim) || ''; }
  function isMonitoring(r, f) { return !!(r && r.summary && (r.summary.monitoring || []).indexOf(f.id) >= 0); }
  function barText(f) { return f && f.test && typeof f.test.bar === 'number' ? String(+(f.test.bar * 100).toFixed(2)) + '%' : 'the bar'; }
  function moveText(f) {
    var m = moveOf(f);
    if (!m) return '';
    if (m.kind === 'stepped') {
      var st = (m.steps || []).map(function (x) {
        var who = (x.levels || []).map(function (l) { return '\'' + l[0] + '\' ' + (l[1] === 'first appears' ? 'first appears' : l[1]); }).join('; ');
        return monName(x.month) + (typeof x.size === 'number' ? ' (' + fm().val(x.size, 'fraction') + ')' : '') + (who ? ', when ' + who : '');
      });
      return 'Stepped ' + (m.direction === 'fall' ? 'down' : 'up') + ' at ' + st.join(', and at ') + ': those steps in what the file covers explain the change, so the business is read like for like below.';
    }
    var side = m.direction === 'fall' ? 'below' : 'above';
    return m.kind === 'moved' ? 'Moved: the whole interval is ' + side + ' zero, but the ' + (m.direction === 'fall' ? 'fall' : 'rise') + ' is not yet shown to be at least ' + barText(f) + '.'
      : m.kind === 'cleared' ? 'Moved: the whole interval is ' + side + ' the ' + barText(f) + ' bar, but another check holds the grade back.'
        : 'No clear movement: the interval includes zero.';
  }
  // short: the table's marker ("moved", "no clear movement"); else the whole sentence
  function moveTag(f, short) {
    var m = moveOf(f);
    if (!m) return '';
    var t = short ? (m.kind === 'unclear' ? 'no clear movement' : m.kind === 'stepped' ? 'stepped with coverage' : 'moved') : moveText(f);
    return '<span class="nl2-move" data-move="' + esc(m.kind) + '"><span aria-hidden="true">' + (m.kind === 'stepped' ? '⤴' : m.kind !== 'unclear' ? (m.direction === 'fall' ? '▼' : '▲') : '·') + '</span> ' + esc(t) + '</span>';
  }
  // the measured power nearest a tested claim, in words (plan §1.5), or why none applies
  function powerText(f, r) {
    var p = f && f.power, num = fm().num, p1 = fm().pct1;
    if (!p) return '';
    if (p.routed) return 'Held at WATCH by rule, whatever the size of the change: ' + routedShort(f) + '.';
    var z = p.nearest;
    if (!z) return '';
    return 'Power, measured: a true ' + num(z.shift_pct, 0) + '% change was confirmed in ' + p1(z.rate) + ' of simulated series (' + num(z.k, 0) + ' of ' + num(z.N, 0) + ') in the condition nearest this claim (' + C().cellWords(z) + ').';
  }
  // why a claim is held at WATCH by rule, in the rule's own terms: a monthly total on its effective rows a
  // month against the totals' line, a count on its rows a month against the counts' line
  function routedShort(f) {
    var R = f && f.power && f.power.routed_rule, num = fm().num;
    if (R) {
      return R.kind === 'total' ? 'about ' + num(R.effective_rows_a_month, 0) + ' effective rows a month (rows divided by 1 + the squared spread of the amounts), under the ' + num(R.line, 0) + ' the engine needs for a monthly total'
        : 'about ' + num(R.rows_a_month, 0) + ' rows a month, under the ' + num(R.line, 0) + ' the engine needs for a monthly count';
    }
    return /\.total[._]|\.total$/.test(String(f && f.id)) ? 'too few effective rows a month for a monthly total' : 'too few rows a month for a monthly count';
  }
  // what the file covers, when it changed inside the compared months (the engine's composition record),
  // and the like-for-like comparison on the levels present throughout: the engine's own words and numbers
  function compText(f) {
    var c = f && f.composition, lf = c && c.like_for_like;
    if (!c || !c.words) return '';
    var s = 'What the file covers changed: ' + c.words + '.';
    if (lf && lf.estimate !== null && lf.estimate !== undefined) {
      var lci = fm().ci(lf.ci, 'fraction');
      s += ' Like for like, on the ' + fm().num(c.levels_kept, 0) + ' ' + plural(c.column).toLowerCase() + ' present in every modelled month: ' + fm().val(lf.estimate, 'fraction') +
        (lci ? ' (' + lvl(lf.level) + ' interval ' + lci + ')' : '') + (lf.tested ? (lf.grade ? ', graded ' + (GRADES[lf.grade] || ['', lf.grade])[1] : '') : ', descriptive, not tested') + '.';
    }
    return s;
  }
  function obj(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function arr(v) { return Array.isArray(v); }
  function lvl(x) { return x === null || x === undefined ? '' : String(+(x * 100).toFixed(2)) + '%'; }
  function chipLabel(ch) {
    if (!ch) return '';
    if (ch.type === 'heatmap') return ch.id.indexOf('season.') === 0 ? 'season: ' + ch.id.slice(7).replace(/_/g, ' ') : ch.id.indexOf('catmonth.') === 0 ? 'by ' + ch.id.slice(9).replace(/_/g, ' ') : ch.id === 'corr' ? 'correlation' : 'empty cells';
    return CHIP[ch.type] || ch.type;
  }

  N.isV2 = function (r) { return !!r && r.contract_version === 2 && arr(r.charts) && r.charts.length > 0; };

  /* ------------------------------------------------------------ the v2 blocks the page reads */
  N.check = function (r) {
    var bad = [], need = function (ok, what) { if (!ok) bad.push(what); return ok; };
    var same = function (a, b) { return arr(a) && arr(b) && a.length === b.length; };
    var SHAPE = {
      trend_windows: function (d) { return same(d.months, d.values) && obj(d.windows) && obj(d.window_means); },
      fan: function (d) { return arr(d.history) && arr(d.forward); },
      replay: function (d) { return arr(d.months); },
      stacked_bars: function (d) { return same(d.months, d.kept) && same(d.months, d.set_aside); },
      ranked_bars: function (d) { return arr(d.bars); },
      histogram_windows: function (d) { return arr(d.edges) && obj(d.prior) && obj(d.latest) && arr(d.prior.counts) && arr(d.latest.counts); },
      benchmark_strip: function (d) { return typeof d.available === 'boolean'; },
      table: function (d) { return arr(d.rows); },
      kpi_tiles: function (d) { return arr(d.tiles); },
      heatmap: function (d, id) {
        if (id.indexOf('season.') === 0) return arr(d.years) && arr(d.months) && arr(d.values) && d.values.length === d.years.length;
        if (id.indexOf('catmonth.') === 0) return arr(d.months) && arr(d.categories) && arr(d.counts) && d.counts.length === d.categories.length && d.counts.every(function (x) { return same(x, d.months); });
        if (id === 'missingness') return arr(d.columns) && arr(d.months) && arr(d.nulls) && d.nulls.length === d.columns.length;
        if (id === 'corr') return arr(d.measures) && arr(d.r) && arr(d.n);
        return false;
      }
    };
    (r.charts || []).forEach(function (c, i) {
      var w = 'charts[' + i + ']' + (c && typeof c.id === 'string' ? ' (' + c.id + ')' : '');
      if (!need(obj(c), w + ' is not a chart record')) return;
      need(typeof c.id === 'string' && c.id.length > 0, w + ' has no id');
      need(c.view === 'manager' || c.view === 'analyst', w + ' has no view');
      need(arr(c.finding_ids), w + ' names no claims (finding_ids)');
      if (!need(obj(c.data), w + ' has no data')) return;
      if (c.id !== 'kpi' && c.id !== 'findings_table') need(!!(C().drawer && C().drawer(c)), w + ' is a chart this page cannot draw (' + c.type + ')');
      need(!!SHAPE[c.type] && SHAPE[c.type](c.data, String(c.id)), w + ' data do not have the shape of a ' + c.type);
    });
    need(r.charts.some(function (c) { return c && c.id === 'kpi'; }), 'there is no KPI chart');
    (r.findings || []).forEach(function (f, i) {
      need(!!GRADES[f.grade], 'findings[' + i + '].grade is not an evidence grade');
      need(obj(f.effect), 'findings[' + i + '].effect is missing');
      need(f.test === null || obj(f.test), 'findings[' + i + '].test is not a test or null');
    });
    need(arr(r.charts_suppressed) && r.charts_suppressed.every(function (s) { return obj(s) && typeof s.rule === 'string' && typeof s.why === 'string'; }), 'charts_suppressed must list a rule and why');
    need(obj(r.engine) && obj(r.engine.benchmark), 'engine.benchmark is missing');
    need(obj(r.reproducibility) && obj(r.reproducibility.figures_reproduced), 'reproducibility is missing');
    need(obj(r.health) && arr(r.health.columns) && arr(r.health.dimensions), 'health.columns or health.dimensions is missing');
    need(arr(r.methods) && arr(r.limitations), 'methods or limitations is missing');
    return bad;
  };

  /* ------------------------------------------------------------ pieces */
  function byId(r) { var m = {}; (r.findings || []).forEach(function (f) { m[f.id] = f; }); return m; }
  function chartMap(r) { var m = {}; r.charts.forEach(function (c) { m[c.id] = c; }); return m; }
  function effectText(f) {
    var e = f.effect || {};
    if ((e.estimate === null || e.estimate === undefined) && e.note) return e.note;
    return fm().val(e.estimate, e.scale) + (e.scale === 'difference' && e.unit ? ' ' + e.unit : '');
  }
  function ciText(f) { var e = f.effect || {}; return fm().ci(e.ci, e.scale); }
  function fcrText(f) {
    var e = f.effect || {}, c = e.ci_fcr;
    if (!c || (c[0] === null && c[1] === null)) return '';
    var side = c[0] !== null && c[1] === null ? 'at least ' + fm().val(c[0], e.scale) : c[0] === null ? 'at most ' + fm().val(c[1], e.scale) : fm().ci(c, e.scale);
    return 'selection-adjusted: ' + side + ' (' + lvl(e.fcr_level) + ' one-sided)';
  }
  function settleOf(f) { return (f.watch && f.watch.settle) || f.needed_to_upgrade || ''; }
  function chips(f, CH, panel) {
    var out = (f.chart_ids || []).filter(function (id) {
      var c = CH[id];
      if (!c || id === 'kpi' || id === 'findings_table') return false;
      return panel === 'm' ? c.view === 'manager' && c.default_visible : c.default_visible;
    }).map(function (id) { return '<a class="nl2-chip" href="#nl2c-' + panel + '-' + esc(id) + '">' + esc(chipLabel(CH[id])) + '</a>'; });
    return out.length ? '<span class="nl2-chips"><span class="sr">Shown in: </span>' + out.join('') + '</span>' : '';
  }
  function sortBtn(label, i) { return '<th scope="col"><button type="button" class="nl2-sort" data-col="' + i + '">' + esc(label) + '</button></th>'; }

  // the findings table, compact (manager) or full (analyst); every row carries its finding id
  function ftab(r, mode, CH) {
    var P = mode === 'manager' ? 'm' : 'a', FIRST = 8;
    var cols = mode === 'manager' ? ['Claim', 'Effect [interval]', 'Grade', 'What would settle it']
      : ['Claim', 'Grade', 'Effect', 'Interval', 'p', 'q', 'Test', 'n', 'Family', 'What would settle it'];
    var h = '<div class="tscroll" tabindex="0" role="region" aria-label="' + (mode === 'manager' ? 'Every finding and its grade' : 'Findings with their tests') + '"><table class="dt nl2-ftab" data-cols="' + mode + '"><thead><tr>' +
      cols.map(sortBtn).join('') + '</tr></thead><tbody>';
    r.findings.forEach(function (f, i) {
      var t = f.test, ran = !!(t && t.ran), e = f.effect || {}, ci = ciText(f), fc = fcrText(f);
      var lv = e.level ? ' (' + lvl(e.level) + ')' : '';
      var claim = '<th scope="row"><span class="nl2-claim">' + esc(f.claim) + '</span>' + chips(f, CH, P) + '</th>';
      var g = '<td data-sort="' + strength(f) + '">' + grade(f.grade, f.kind) + (moveTag(f, true) ? ' ' + moveTag(f, true) : '') + '</td>';
      var st = '<td class="nl2-settle-cell">' + esc(settleOf(f)) + '</td>';
      var hide = mode === 'manager' && i >= FIRST ? ' hidden class="nl2-more-row"' : '';
      if (mode === 'manager') {
        h += '<tr data-fid="' + esc(f.id) + '"' + hide + '>' + claim + '<td data-sort="' + (e.estimate === null ? '' : e.estimate) + '">' + esc(effectText(f)) + (ci ? ' <span class="muted">[' + esc(ci) + ']</span>' : '') + '</td>' + g + st + '</tr>';
      } else {
        var testCell = '<td class="nl2-test">' + (t ? esc(t.name || t.method || '') + (ran ? '' : ' <span class="muted">(not run' + (t.not_run_reason ? ': ' + esc(t.not_run_reason) : '') + ')</span>') : '') + '</td>';
        var nCell = '<td class="nl2-n" data-sort="' + (t && t.n_months !== null && t.n_months !== undefined ? t.n_months : '') + '">' + (t && t.n_months ? esc(fm().num(t.n_months, 0) + ' months') + (t.n_rows ? ', ' + esc(fm().num(t.n_rows, 0) + ' rows') : '') : '') + '</td>';
        h += '<tr data-fid="' + esc(f.id) + '">' + claim + g +
          '<td class="num" data-sort="' + (e.estimate === null || e.estimate === undefined ? '' : e.estimate) + '">' + esc(effectText(f)) + '</td>' +
          '<td class="nl2-ci">' + (ci ? esc(ci) + esc(lv) + (fc ? '<br><span class="muted">' + esc(fc) + '</span>' : '') : '') + '</td>' +
          '<td class="num" data-sort="' + (ran && t.p !== null ? t.p : '') + '">' + (ran ? esc(fm().pval(t.p)) : '') + '</td>' +
          '<td class="num" data-sort="' + (ran && t.q !== null ? t.q : '') + '">' + (ran ? esc(fm().pval(t.q)) : '') + '</td>' + testCell + nCell +
          '<td class="nl2-fam">' + (t && t.family ? esc(t.family + ' (' + t.family_size + (t.family_size === 1 ? ' claim, ' : ' claims, ') + (t.fdr_method === 'alone' ? 'tested alone' : t.fdr_method) + ')') : '') + '</td>' + st + '</tr>';
      }
    });
    h += '</tbody></table></div>';
    if (mode === 'manager' && r.findings.length > FIRST) h += '<p class="tr-more nl2-more"><button type="button" class="btn btn-ghost" data-act="nl2-more">Show all ' + r.findings.length + ' findings</button></p>';
    return h;
  }

  // a figure for one chart record; the drawer fills .viz when the figure is drawn
  function fig(ch, P, R) {
    var F = byId(R), ids = ch.finding_ids || [], claims = ids.filter(function (i) { return F[i]; }).length;
    var btn = !ids.length ? '<span class="note">Descriptive: it supports no single claim.</span>'
      : '<button type="button" class="tb nl2-link" aria-label="Highlight ' + (claims ? 'the claims' : 'the evidence facts') + ' this chart supports: ' + esc(ids.map(function (i) { return F[i] ? F[i].claim : i; }).join('; ')) + '">' +
        (claims === ids.length ? (claims === 1 ? 'Show the claim it supports' : 'Show the ' + claims + ' claims it supports') : 'Show the ' + ids.length + ' evidence facts behind it') + '</button>';
    var table = ch.type === 'table' || (ch.type === 'benchmark_strip' && !ch.data.available) ? '0' : '1';
    return '<figure class="visual nl2-fig" id="nl2c-' + P + '-' + esc(ch.id) + '" data-chart="' + esc(ch.id) + '" data-rule="' + esc(ch.rule) + '" data-findings="' + esc(ids.join(' ')) + '" data-table="' + table + '" tabindex="-1">' +
      '<figcaption>' + esc(ch.title) + '</figcaption>' +
      (P === 'a' ? '<p class="nl2-why">Why it is here: ' + esc(ch.why_shown) + '.</p>' : '') +
      '<div class="viz" id="nl2v-' + P + '-' + esc(ch.id) + '"></div><div class="nl2-fignote"></div><div class="nl2-figfoot">' + btn + '</div></figure>';
  }

  /* ------------------------------------------------------------ the manager view */
  function decisionLines(r) {
    var F = r.findings, pm = r.primary_metric && r.primary_metric.finding_id;
    // a claim graded NOT ENOUGH DATA gives no line to decide on: it stays in the table and under "what would settle it"
    var picks = F.filter(function (f) { return (f.kind === 'business' || f.kind === 'forecast') && f.grade !== 'NOT_ENOUGH_DATA' && f.estimand && f.effect && f.effect.estimate !== null && f.effect.estimate !== undefined; });
    // the primary claim first (plan §4.1), then by strength of evidence: CONFIRMED, WATCH that moved, other WATCH; then the larger change
    var size = function (f) { return f.effect.scale === 'fraction' ? Math.abs(f.effect.estimate) : 0; };
    // the ledger's own line count (summary.monitoring: rows of a ledger of amounts) never takes a first-screen line
    var mon = function (f) { return isMonitoring(r, f) ? 1 : 0; };
    picks.sort(function (a, b) { return (a.id === pm ? -1 : 0) - (b.id === pm ? -1 : 0) || mon(a) - mon(b) || strength(a) - strength(b) || size(b) - size(a) || F.indexOf(a) - F.indexOf(b); });
    return picks.slice(0, 6).map(function (f) {
      var e = f.effect, ci = ciText(f), fc = fcrText(f), word = f.kind === 'forecast' ? 'range' : 'interval';
      var cf = (f.watch && f.watch.checks_failed) || [], m = moveOf(f), stepped = !!m && m.kind === 'stepped';
      // an interval that includes zero already says why the size test did not clear: that check is not repeated;
      // a claim the coverage steps explain is not held back by its size test or the trend screen either
      if (m && (m.kind === 'unclear' || stepped)) cf = cf.filter(function (c) { return c !== 'min_effect_p'; });
      if (stepped) cf = cf.filter(function (c) { return c !== 'composition' && c !== 'drift_screen'; });
      var held = cf.length ? ' <span class="nl2-held">Held back: ' + esc(cf.map(function (c) { return checkName(c, f); }).join('; ')) + '.</span>' : '';
      var mv = (moveTag(f) ? ' ' + moveTag(f) : '') + (compText(f) ? ' <span class="nl2-comp">' + esc(compText(f)) + '</span>' : '');
      var pw = f.grade === 'WATCH' && powerText(f, r) ? ' <span class="nl2-power">' + esc(powerText(f, r)) + '</span>' : '';
      var er = f.error_rate && f.error_rate.sentence ? '<span class="nl2-er">' + esc(f.error_rate.sentence) + '</span>' : '';
      // a stepped claim's as-filed interval spans the step, so it says nothing about the business: not printed
      return '<li data-fid="' + esc(f.id) + '"' + (isMonitoring(r, f) ? ' data-mon="1"' : '') + '>' + grade(f.grade, f.kind) + ' <span class="nl2-dl-claim">' + esc(labelOf(r, f)) + ':</span> <b>' + esc(effectText(f)) + '</b>' +
        (stepped ? ' on the year before, as filed' : (ci ? ' (' + lvl(e.level) + ' ' + word + ' ' + esc(ci) + ')' : '') + (fc ? '; ' + esc(fc) : '')) + '.' + mv + held + pw + er + '</li>';
    });
  }
  function tiles(r, K) {
    var F = byId(r);
    return K.data.tiles.map(function (t) {
      var v = fm().val(t.value, t.scale, t.unit), ci = fm().ci(t.ci, t.scale), f = F[t.finding_id];
      var word = f && f.kind === 'forecast' ? 'range' : 'interval';
      var unit = ['rows', 'months'].indexOf(t.unit) >= 0 ? t.unit : t.scale === 'difference' ? (t.unit || '') + ' (own units)' : '';
      return '<div class="nl2-tile" data-fid="' + esc(t.finding_id) + '"><p class="nl2-tile-lab">' + esc(t.claim) + '</p>' +
        '<p class="nl2-tile-num"><span class="nl2-tile-v">' + esc(v) + '</span>' + (unit ? ' <span class="nl2-tile-u">' + esc(unit) + '</span>' : '') + '</p>' +
        (ci ? '<p class="nl2-tile-ci">' + esc(lvl(t.ci_level) + ' ' + word + ': ' + ci) + '</p>' : t.movement && t.movement.kind === 'stepped' ? '<p class="nl2-tile-note">on the year before, as filed; read it like for like</p>' : '') + '<p class="nl2-tile-g">' + grade(t.grade, f ? f.kind : t.kind) + (f && moveTag(f, true) ? ' ' + moveTag(f, true) : '') + '</p></div>';
    }).join('');
  }
  // the conditions a benchmark cell simulated, and what was true in it
  function truthOf(z) { return z.at_bar ? 'a true change of exactly the ' + fm().num(z.shift_pct, 0) + '% bar' : 'no change at all'; }
  // a routed claim's condition in its own rule's terms (plan §3.4; fixer round, 24 Sep 2026: a monthly total
  // was described with the counts' "rows a month, under 200")
  function routedWords(R) {
    var num = fm().num;
    return R.kind === 'total'
      ? 'it is a monthly total of about ' + num(R.rows_a_month, 0) + ' rows a month whose amounts vary, which carries the noise of about ' + num(R.effective_rows_a_month, 0) +
        ' rows of one fixed amount (rows divided by 1 + the squared coefficient of variation of the amounts), under the ' + num(R.min_effective_rows_a_month, 0) + ' effective rows a month below which the benchmark could not certify the engine\'s false-confirm rate for monthly totals'
      : 'with about ' + num(R.rows_a_month, 0) + ' rows a month, under ' + num(R.min_rows_a_month, 0) + ', the engine confirms no change in a count built from so few rows';
  }
  function trustLayers(r) {
    var R = r.reproducibility.figures_reproduced || {}, c = r.cleaning, B = r.engine.benchmark || {}, h = r.health, num = fm().num, p1 = fm().pct1, F = byId(r);
    var lost = c.rows_in - c.rows_clean - c.rows_quarantined, out = [];
    out.push(['reproduced', '<b>Every figure re-ran:</b> ' + num(R.k, 0) + ' of ' + num(R.n, 0) + ' figures re-ran from their stored query, payload and seed' + (R.failed ? '; ' + num(R.failed, 0) + ' did not and were withheld' : '') + '. That makes them repeatable, not proven correct.']);
    out.push(['rows', '<b>Every row accounted for:</b> ' + num(c.rows_quarantined, 0) + ' of ' + num(c.rows_in, 0) + ' rows set aside' + (c.rows_quarantined ? ', each with its reason' : '') + '; ' + num(c.rows_clean, 0) + ' kept' + (lost > 0 ? ', ' + num(lost, 0) + ' removed by the fixes' : '') + '.']);
    var W = B.worst_cell, target = typeof B.target_pct === 'number' ? B.target_pct : null;
    var worst = W ? 'in the hardest simulated condition (' + C().cellWords(W) + ', with ' + truthOf(W) + '), ' + p1(W.rate) + ' (' + num(W.k, 0) + ' of ' + num(W.N, 0) + '; 95% interval ' + p1(W.lower95) + ' to ' + p1(W.upper95) + ')' +
      (W.above_target ? ', above the engine\'s ' + p1(target) + ' aim' : '') : '';
    var cert = C().certWords(B.certification), fileW = B.file ? 'this file: ' + num(B.file.months, 0) + ' months, noise ' + num(B.file.cv_pct, 0) + '%, momentum ' + num(B.file.phi, 2) +
      (B.file.effective_rows_a_month !== null && B.file.effective_rows_a_month !== undefined ? ', about ' + num(B.file.effective_rows_a_month, 0) + ' effective rows a month'
        : B.file.rows_a_month !== null && B.file.rows_a_month !== undefined ? ', about ' + num(B.file.rows_a_month, 0) + ' rows a month' : '') : '';
    var M = B.matched_cell;
    if (B.available && B.routed) out.push(['false_confirm', '<b>False alarms:</b> the primary claim is held at WATCH by rule: ' + esc(routedWords(B.routed)) + ', so no false-confirm rate applies to it.' +
      (worst ? ' For reference, ' + worst + '.' : '') + (cert ? ' ' + esc(cert) : '')]);
    else if (B.available && M && B.match === 'none') out.push(['false_confirm', '<b>False alarms:</b> no measured condition is like this file (' + esc(fileW) + '; ' + esc((B.file && B.file.unlike) || '') + '), so no false-confirm rate is quoted for it.' +
      (worst ? ' For reference, ' + worst + '.' : '') + (cert ? ' ' + esc(cert) : '')]);
    else if (B.available && M) out.push(['false_confirm', '<b>False alarms, measured:</b> in the ' + (B.match === 'close' ? 'simulated condition like this file\'s' : 'nearest simulated condition') + ' (' + C().cellWords(M) + '; ' + fileW + '), with ' + truthOf(M) + ', the engine said CONFIRMED in ' +
      p1(M.rate) + ' of series (' + num(M.k, 0) + ' of ' + num(M.N, 0) + ')' + (M.routed ? ', by rule, not by measurement' : '') + (worst ? '; ' + worst : '') + '. That is not the chance that a finding here is real.' + (cert ? ' ' + esc(cert) : '')]);
    else out.push(['false_confirm', '<b>False alarms:</b> not matched to this file' + (B.note ? ' (' + esc(B.note) + ')' : '') + '.' + (worst ? ' For reference, ' + worst + '.' : '') + (cert ? ' ' + esc(cert) : '')]);
    var pmF = r.primary_metric && F[r.primary_metric.finding_id], pw = pmF ? powerText(pmF, r) : '';
    out.push(['power', '<b>Power:</b> ' + (pw ? esc(pw) : 'not measured for the primary claim.')]);
    out.push(['tipping', '<b>Sensitivity to the set-aside rows:</b> how far they would have to differ from the kept rows to change an answer is not measured in this release.']);
    out.push(['health', '<b>Data health:</b> the weakest dimension, ' + esc(h.weakest || 'n/a') + ', scores ' + (h.score_min === null ? 'n/a' : Number(h.score_min).toFixed(1)) + ' of 100; the engine\'s Data Health Score (the mean of the five) is ' +
      (h.score_mean === null ? 'n/a' : Number(h.score_mean).toFixed(1)) + '. Accuracy against the source: ' + esc((h.accuracy && h.accuracy.measured) ? 'measured' : 'not measured') + '.']);
    return out;
  }
  // one line for the first screen; the layers sit under it
  function trustLine(r) {
    var R = r.reproducibility.figures_reproduced || {}, c = r.cleaning, B = r.engine.benchmark || {}, num = fm().num, p1 = fm().pct1;
    var fa = !B.available ? '' : B.routed ? 'the primary claim is held at WATCH by rule (' + (B.routed.kind === 'total' ? 'about ' + num(B.routed.effective_rows_a_month, 0) + ' effective rows a month, under the ' + num(B.routed.min_effective_rows_a_month, 0) + ' a monthly total needs'
      : 'about ' + num(B.routed.rows_a_month, 0) + ' rows a month, under the ' + num(B.routed.min_rows_a_month, 0) + ' a monthly count needs') + ')' :
      B.matched_cell && B.match === 'none' ? 'no measured condition is like this file, so no false-alarm rate is quoted' :
      B.matched_cell ? 'false alarms ' + p1(B.matched_cell.rate) + ' in the ' + (B.match === 'close' ? 'simulated condition like this file\'s' : 'nearest simulated condition') + (B.worst_cell ? ', ' + p1(B.worst_cell.rate) + ' in the hardest' : '') : '';
    return num(R.k, 0) + ' of ' + num(R.n, 0) + ' figures re-ran; ' + num(c.rows_quarantined, 0) + ' of ' + num(c.rows_in, 0) + ' rows set aside, each with its reason' + (fa ? '; ' + fa : '') + '.';
  }
  function settleGroups(list, fold) {
    var groups = [], by = {};
    list.forEach(function (f) {
      var s = settleOf(f);
      if (!s) return;
      // a claim that moved and one with no clear movement never share a group, even with the same answer
      var m = moveOf(f), key = (m ? m.kind : '') + '\u0000' + s;
      if (!by[key]) { by[key] = { settle: s, move: m ? m.kind : '', items: [] }; groups.push(by[key]); }
      by[key].items.push(f);
    });
    var mr = function (g) { return g.move === 'cleared' || g.move === 'moved' ? 0 : 1; };
    groups.sort(function (a, b) { return mr(a) - mr(b); });
    return groups.map(function (g) {
      var items = '<ul class="nl2-settle-for">' + g.items.map(function (f) { return '<li data-fid="' + esc(f.id) + '">' + grade(f.grade, f.kind) + ' ' + esc(f.claim) + '</li>'; }).join('') + '</ul>';
      // a long list that shares one answer folds away under its count (it opens for a highlight and on paper)
      if (fold && g.items.length > 3) items = '<details class="nl2-fold"><summary>' + g.items.length + ' ' + esc(fold) + '</summary>' + items + '</details>';
      var lead = g.move === 'moved' ? '<p class="nl2-move-lead" data-move="moved">Moved, size not yet shown:</p>' : g.move === 'cleared' ? '<p class="nl2-move-lead" data-move="cleared">Moved past the bar, held back by another check:</p>'
        : g.move === 'unclear' ? '<p class="nl2-move-lead" data-move="unclear">No clear movement:</p>' : '';
      return '<li data-move="' + esc(g.move) + '">' + lead + items + '<p class="nl2-settle-t">' + esc(g.settle) + '</p></li>';
    }).join('');
  }
  function manager(r, CH, parts) {
    var K = CH.kpi, B = CH.benchmark, drv = (r.charts_suppressed || []).filter(function (s) { return s.rule === '#2b'; })[0];
    var figs = r.charts.filter(function (c) { return c.view === 'manager' && c.default_visible && ['kpi', 'findings_table', 'benchmark'].indexOf(c.id) < 0; });
    var biz = r.findings.filter(function (f) { return (f.kind === 'business' || f.kind === 'forecast') && f.grade !== 'CONFIRMED'; });
    var dq = r.findings.filter(function (f) { return f.kind !== 'business' && f.kind !== 'forecast' && f.grade === 'WATCH'; });
    var causal = (r.limitations || []).filter(function (l) { return l.kind === 'causal'; })[0];
    // the first screen shows the three strongest lines; the rest fold under them
    // the ledger's own line count (data-mon) always folds: it is pipeline monitoring, not a business claim
    var all = decisionLines(r), top = all.filter(function (x) { return x.indexOf(' data-mon="1"') < 0; }).slice(0, 3);
    var rest = all.filter(function (x) { return top.indexOf(x) < 0; }), more = rest.length;
    var lines = all.length ? '<ul class="nl2-decision" aria-label="Decision lines">' + top.join('') + '</ul>' +
      (more > 0 ? '<details class="nl2-more-lines"><summary>' + more + ' more ' + (more === 1 ? 'claim' : 'claims') + '</summary><ul class="nl2-decision" aria-label="More decision lines">' + rest.join('') + '</ul></details>' : '') : '';
    // the first screen: the decision, at most three business tiles, one trust line
    // the bottom line: at most three sentences from the engine's facts (summary.lines: what moved and why, what
    // to act on, the planning number); the engine's full clause list sits in the analyst view
    var SL = (r.summary && r.summary.lines) || [];
    var bl = SL.length ? '<div class="nl2-bottom" data-summary="1">' + SL.map(function (l, i) {
      return (i === 0 ? '<h3 class="nl2-bl" ' : '<p class="nl2-bl" ') + 'data-kind="' + esc(l.kind) + '" data-findings="' + esc((l.finding_ids || []).join(' ')) + '">' + esc(l.text) + (i === 0 ? '</h3>' : '</p>');
    }).join('') + '</div>' : '<h3 class="nl2-bottom">' + esc(r.story.headline) + '</h3>';
    var h = '<article class="tr-card nl2-bottomcard"><p class="kicker">Bottom line</p>' + bl +
      lines +
      (causal ? '<p class="note">' + esc(causal.text) + '</p>' : '') + '</article>';
    if (K) h += '<div class="nl2-kpis" data-chart="kpi" data-findings="' + esc(K.finding_ids.join(' ')) + '" role="list" aria-label="' + esc(K.title) + '">' + tiles(r, K).replace(/<div class="nl2-tile"/g, '<div role="listitem" class="nl2-tile"') + '</div>';
    h += '<article class="tr-card nl2-trustcard"><p class="nl2-trust-line"><b>Can I trust it?</b> ' + esc(trustLine(r)) + '</p>' +
      '<details class="nl2-trust-more"><summary>How much to trust it, layer by layer</summary><ul class="nl2-trust">' + trustLayers(r).map(function (x) { return '<li data-layer="' + x[0] + '">' + x[1] + '</li>'; }).join('') + '</ul></details></article>';
    if (figs.length) h += '<div class="nl2-grid' + (figs.length === 1 ? ' nl2-grid-1' : '') + '">' + figs.map(function (c) { return fig(c, 'm', r); }).join('') + '</div>';
    // below the fold: every finding, what would settle each open one, and the benchmark behind the false-alarm line
    var more = '';
    if (drv) more += '<p class="nl2-drivers"><b>Top drivers</b> (contribution, not cause): not shown, because ' + esc(drv.why) + '.</p>';
    // the manager charts whose rule did not fire, one line each (§5 "Suppression"); the analyst view lists them all
    var MGR = { '#2': 'trend', '#4': 'forecast', '#5': 'forecast replay' }, gone = {};
    (r.charts_suppressed || []).forEach(function (x) { if (MGR[x.rule]) { gone[x.why] = gone[x.why] || []; if (gone[x.why].indexOf(MGR[x.rule]) < 0) gone[x.why].push(MGR[x.rule]); } });
    var goneKeys = Object.keys(gone);
    if (goneKeys.length) more += '<p class="nl2-drivers nl2-gone">' + goneKeys.map(function (w) { return '<b>No ' + esc(gone[w].join(' or ')) + ' chart:</b> ' + esc(w.replace(/[.\s]+$/, '')) + '.'; }).join(' ') + '</p>';
    var st = settleGroups(biz) + (dq.length ? settleGroups(dq, 'data-quality findings on WATCH share this answer') : '');
    if (st) more += '<article class="tr-card nl2-settle"><h3>What would settle it</h3><p class="note">For each claim not yet confirmed, what the engine says would decide it. Claims that moved come first; claims that share the same answer are listed together.</p><ul class="nl2-settle-list">' + st + '</ul></article>';
    if (CH.findings_table) more += '<article class="tr-card nl2-ftab-card" data-chart="findings_table" data-findings="' + esc(CH.findings_table.finding_ids.join(' ')) + '"><h3>' + esc(CH.findings_table.title) + '</h3>' +
      '<p class="note">Grades: ' + grade('CONFIRMED') + ' the evidence cleared every check; ' + grade('WATCH') + ' measured, not yet shown; ' + grade('NOT_ENOUGH_DATA') + ' too little data to judge. A forecast reads ' + grade('CONFIRMED', 'forecast') + ' when it beat repeating last year\'s value in the replay. A grade is the engine\'s rule, not a probability that the claim is true.</p>' + ftab(r, 'manager', CH) + '</article>';
    if (B) more += '<div class="nl2-footer">' + fig(B, 'm', r) + '</div>';
    if (more) h += '<details class="nl2-more-m"><summary>Every finding, what would settle each open one, and the false-alarm benchmark</summary>' + more + '</details>';
    return h;
  }

  /* ------------------------------------------------------------ the analyst view */
  function dl(pairs) { return '<dl class="nl2-dl">' + pairs.filter(function (p) { return p; }).map(function (p) { return '<dt>' + esc(p[0]) + '</dt><dd>' + p[1] + '</dd>'; }).join('') + '</dl>'; }
  function wil(c) { return c && c[0] !== null && c[1] !== null && c[0] !== undefined ? Number(c[0]).toFixed(1) + '–' + Number(c[1]).toFixed(1) : ''; }
  function pctCi(x) { return x && x.pct !== null && x.pct !== undefined ? esc(Number(x.pct).toFixed(1) + '%') + (wil(x.ci) ? ' <span class="muted">[' + esc(wil(x.ci)) + ']</span>' : '') : '<span class="muted">n/a</span>'; }
  function kv(o) { var k = Object.keys(o || {}); return k.length ? k.map(function (x) { return '<code>' + esc(x) + '</code> ' + esc(fm().num(o[x], 0)); }).join(', ') : ''; }
  function coltab(r) {
    var num = fm().num;
    var h = '<div class="tscroll" tabindex="0" role="region" aria-label="Quality of each column"><table class="dt nl2-coltab"><thead><tr><th scope="col">Column</th><th scope="col">Completeness</th><th scope="col">Validity</th><th scope="col">Uniqueness</th>' +
      '<th scope="col">Weakest</th><th scope="col" class="num">Claim health</th><th scope="col" class="num">Distinct</th><th scope="col">Values</th><th scope="col">Set aside, by rule</th><th scope="col">Fixed, by rule</th><th scope="col">Claims it supports</th></tr></thead><tbody>';
    r.health.columns.forEach(function (c) {
      var vals = c.withheld ? '<span class="muted">withheld: no value is shown</span>'
        : c.numeric ? 'min ' + esc(num(c.numeric.min)) + ', median ' + esc(num(c.numeric.median)) + ', max ' + esc(num(c.numeric.max))
          : c.dates ? esc(c.dates.min + ' to ' + c.dates.max)
            : (c.top_values || []).slice(0, 3).map(function (tv) { return esc(String(tv[0])) + ' <span class="muted">(' + esc(num(tv[1], 0)) + ')</span>'; }).join(', ');
      h += '<tr data-col="' + esc(c.name) + '"><th scope="row"><code>' + esc(c.name) + '</code> <span class="muted">' + esc(c.type || '') + '</span>' + (c.flagged ? ' <span class="nl2-tag">' + (c.withheld ? 'withheld' : 'flagged') + '</span>' : '') + '</th>' +
        '<td>' + pctCi(c.completeness) + '</td><td>' + pctCi(c.validity) + (c.validity && c.validity.dominant_format ? ' <span class="muted">' + esc(c.validity.dominant_format) + '</span>' : '') + '</td>' +
        '<td>' + (c.uniqueness && c.uniqueness.applicable ? pctCi(c.uniqueness) : '<span class="muted">not an id column</span>') + '</td><td>' + esc(c.weakest || '') + '</td>' +
        '<td class="num">' + (c.claim_health === null || c.claim_health === undefined ? '' : esc(Number(c.claim_health).toFixed(1))) + '</td><td class="num">' + esc(num(c.distinct, 0)) + '</td>' +
        '<td>' + vals + '</td><td>' + kv(c.quarantined_by_rule) + '</td><td>' + kv(c.fixes_by_rule) + '</td><td class="num">' + esc(num((c.safe_for || []).length, 0)) + '</td></tr>';
    });
    return h + '</tbody></table></div><p class="note">Completeness is non-empty of all rows, validity the values of the column\'s main type of its non-empty values, uniqueness distinct of non-empty (id-like columns only); each with a 95% Wilson interval in brackets. Claim health is the lowest of these for the claims that read the column.</p>';
  }
  function dims(r) {
    var num = fm().num;
    return '<div class="tscroll" tabindex="0" role="region" aria-label="Quality dimensions"><table class="dt nl2-dims"><thead><tr><th scope="col">Dimension</th><th scope="col" class="num">Score</th><th scope="col">95% interval</th><th scope="col">How it is measured</th><th scope="col">Of</th></tr></thead><tbody>' +
      r.health.dimensions.map(function (d) {
        return '<tr><th scope="row">' + esc(d.name) + (d.name === r.health.weakest ? ' <span class="nl2-tag">weakest</span>' : '') + '</th><td class="num">' + (d.score === null ? 'n/a' : esc(Number(d.score).toFixed(1))) + '</td>' +
          '<td>' + (wil(d.ci) ? esc(wil(d.ci)) : '<span class="muted">none</span>') + '</td><td>' + esc(d.ci_method || '') + '</td><td>' + (d.k !== null && d.k !== undefined ? esc(num(d.k, 0) + ' of ' + num(d.n, 0)) : '') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }
  // RFC 4180 rows of a CSV text (the set-aside download), stopping after `max` data rows
  N.csvRows = function (text, max) {
    var rows = [], row = [], f = '', q = false, i = 0, t = String(text || '');
    for (; i < t.length; i++) {
      var ch = t[i];
      if (q) { if (ch === '"') { if (t[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += ch; continue; }
      if (ch === '"') q = true;
      else if (ch === ',') { row.push(f); f = ''; }
      else if (ch === '\n' || ch === '\r') { if (ch === '\r' && t[i + 1] === '\n') i++; row.push(f); f = ''; if (row.join('') !== '') rows.push(row); row = []; if (rows.length > max) return rows; }
      else f += ch;
    }
    if (f !== '' || row.length) { row.push(f); if (row.join('') !== '') rows.push(row); }
    return rows;
  };
  function countLines(text) { var n = 0; String(text || '').split(/\r?\n/).forEach(function (l) { if (l) n++; }); return n; }
  function setAside(r) {
    var MAX = 50, rows = N.csvRows(r.downloads.quarantine_csv, MAX), head = rows.shift() || [], total = Math.max(0, countLines(r.downloads.quarantine_csv) - 1);
    rows = rows.slice(0, MAX);
    if (!head.length) return '<p class="nl2-setaside-note">No row was set aside.</p>';
    return '<p class="note nl2-setaside-note">' + (total > rows.length ? 'The first ' + rows.length + ' of ' + fm().num(total, 0) + ' set-aside rows' : 'All ' + fm().num(total, 0) + ' set-aside rows') + ', as the set-aside download holds them' + (/^source_line$/.test(head[0]) ? ' (source_line is the row\'s line in your file)' : '') + '.</p>' +
      '<div class="tscroll nl2-box" tabindex="0" role="region" aria-label="Set-aside rows"><table class="dt nl2-setaside"><thead><tr>' + head.map(function (c) { return '<th scope="col">' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (rw) { return '<tr>' + rw.map(function (v, i) { return i === 0 ? '<th scope="row">' + esc(v) + '</th>' : '<td>' + esc(v) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
  }
  function ledger(r) {
    var L;
    try { L = JSON.parse(r.downloads.ledger_json); } catch (e) { return '<p class="note">The evidence ledger could not be read here; the download holds it.</p>'; }
    var facts = [].concat((L.audit_ledger || []).map(function (f) { return ['audit', f]; }), (L.analysis_ledger || []).map(function (f) { return ['analysis', f]; }));
    return '<p class="note">' + esc(L.what || 'The engine\'s evidence ledgers') + '. ' + fm().num(facts.length, 0) + ' facts; the download adds each one\'s query, derivation and caveats.</p>' +
      '<div class="tscroll nl2-box" tabindex="0" role="region" aria-label="Evidence ledger"><table class="dt nl2-ledger"><thead><tr><th scope="col">Fact</th><th scope="col">Claim</th><th scope="col" class="num">Value</th><th scope="col">Unit</th><th scope="col">Re-ran</th><th scope="col">Ledger</th></tr></thead><tbody>' +
      facts.map(function (x) {
        var f = x[1];
        return '<tr data-fid="' + esc(f.id) + '"><th scope="row"><code>' + esc(f.id) + '</code></th><td>' + esc(f.claim || '') + '</td><td class="num">' + esc(fm().num(f.value)) + '</td><td>' + esc(f.unit || '') + '</td><td>' + (f.verified ? 'yes' : 'no') + '</td><td>' + x[0] + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }
  function methods(r) {
    var F = byId(r), num = fm().num, p = (r.reproducibility.parameters || {}), gp = p.gate_policy || {}, fc = p.forecast_config || {};
    var h = r.methods.map(function (m) {
      return '<article class="nl2-method"><h4>' + esc(m.name) + '</h4>' + (m.assumptions && m.assumptions.length ? '<ul>' + m.assumptions.map(function (a) { return '<li>' + esc(a) + '</li>'; }).join('') + '</ul>' : '') +
        (m.applies_to && m.applies_to.length ? '<p class="note">Applies to: ' + m.applies_to.map(function (i) { return esc(F[i] ? F[i].claim : i); }).join('; ') + '.</p>' : '') +
        (m.desktop_only ? '<p class="note">Runs on the desktop engine only.</p>' : '') + '</article>';
    }).join('');
    var fam = (r.tests_run && r.tests_run.families) || [];
    if (fam.length) h += '<h4>Families of tested claims</h4><div class="tscroll" tabindex="0" role="region" aria-label="Families of tested claims"><table class="dt"><thead><tr><th scope="col">Family</th><th scope="col" class="num">Claims</th><th scope="col">False-discovery control</th><th scope="col" class="num">Level</th></tr></thead><tbody>' +
      fam.map(function (f) { return '<tr><th scope="row">' + esc(f.name) + '</th><td class="num">' + esc(num(f.size, 0)) + '</td><td>' + esc(f.fdr_method === 'alone' ? 'tested alone' : f.fdr_method) + '</td><td class="num">' + esc(lvl(f.level)) + '</td></tr>'; }).join('') + '</tbody></table></div>' +
      '<p class="note">' + esc(num(r.tests_run.claims_tested, 0)) + ' claims were tested in this run.</p>';
    h += '<h4>The rules the grades follow</h4>' + dl([
      ['Size bar for a change', esc(lvl(gp.min_effect_recommend)) + ' (the change must be shown to be at least this)'],
      ['Level for CONFIRMED', esc(lvl(gp.recommend_q)) + ' within the claim\'s family (' + esc(gp.fdr_method || '') + ')'],
      ['Level for WATCH', esc(lvl(gp.watch_p))],
      ['Claim health floor', esc(num(gp.data_health_floor, 0)) + ' of 100'],
      ['History before a trend can be CONFIRMED', esc(num(gp.min_history_for_trend_recommend, 0)) + ' months'],
      ['Rows before a finding can be CONFIRMED', esc(num(gp.min_rows_recommend, 0))],
      ['Share of rows set aside that stops the analysis', esc(lvl(gp.max_quarantine_rate))],
      ['Trend screen', gp.drift_screen ? 'on' : 'off']
    ]);
    h += '<h4>Forecast set-up</h4>' + dl([
      ['Horizon', esc(num(fc.horizon, 0)) + ' months'], ['Range', esc(lvl(fc.band)) + ', ' + esc(String(fc.band_method || '').replace(/_/g, ' ')) + ', widened when errors move together (' + esc(String(fc.band_dependence || '').replace(/_/g, ' ')) + ')'],
      ['Replay', 'held out ' + esc(num(fc.min_holdout, 0)) + ' to ' + esc(num(fc.max_holdout, 0)) + ' months, at least ' + esc(num(fc.min_train, 0)) + ' months to fit'],
      ['Errors needed for a range', esc(num(fc.min_band_errors, 0))]
    ]);
    return h;
  }
  function forecastStats(r) {
    var F = r.forecast, num = fm().num, pv = fm().pval;
    if (!F || !F.available) return '<p class="nl2-nofc"><b>No forecast:</b> ' + esc(String((F && F.reason) || '').replace(/[.\s]+$/, '')) + '.</p>';
    var cv = F.coverage || {}, bt = F.baseline_test || {}, bd = F.band || {};
    return dl([
      ['Range held in the replay', esc(num(cv.hits, 0) + ' of ' + num(cv.n, 0) + ' months') + (cv.wilson ? ' (95% Wilson interval ' + esc(fm().share1(cv.wilson[0]) + ' to ' + fm().share1(cv.wilson[1])) + ')' : '') +
        '; binomial p ' + esc(pv(cv.binomial_p)) + ', Christoffersen independence p ' + esc(pv(cv.christoffersen_ind_p)) + ', conditional coverage p ' + esc(pv(cv.christoffersen_cc_p))],
      ['Against seasonal-naive', bt.method ? esc('DM ' + (bt.stat === null ? 'n/a' : Number(bt.stat).toFixed(2)) + ', df ' + num(bt.df, 0) + ', ' + num(bt.n, 0) + ' months, p ' + pv(bt.p) + (bt.gain !== null && bt.gain !== undefined ? ', error ' + (bt.gain * 100).toFixed(0) + '% lower' : '')) + ' <span class="muted">(' + esc(bt.method.replace(/_/g, ' ')) + ')</span>' : 'not run'],
      ['How the range is built', esc(String(bd.method || '').replace(/_/g, ' ') + ' from ' + num(bd.n_errors, 0) + ' replayed errors; the most it can reach is ' + fm().share1(bd.max_level) + '; widened up to ' + (bd.widened_by_max ? ((bd.widened_by_max - 1) * 100).toFixed(0) + '%' : 'n/a') + ' for errors that move together; scope: ' + String(bd.scope || '').replace(/_/g, ' '))],
      ['This range\'s own spread', bd.conditional_coverage_p10_p90 ? esc('true coverage typically ' + fm().share0(bd.conditional_coverage_p10_p90[0]) + ' to ' + fm().share0(bd.conditional_coverage_p10_p90[1]) + ' (10th to 90th percentile, given ' + num(bd.n_errors, 0) + ' errors)') : 'n/a']
    ]);
  }
  function analyst(r, CH, parts) {
    var num = fm().num, R = r.reproducibility, P = R.parameters || {}, en = r.engine, env = en.environment || {}, h = r.health;
    var secs = [['summary', 'Executive summary'], ['data', 'Data and provenance'], ['methods', 'Methods'], ['results', 'Results'], ['limits', 'Limitations and threats to validity'],
      ['recs', 'Recommendations'], ['repro', 'Reproducibility'], ['appendix', 'Appendix: set-aside rows and the evidence ledger']];
    var S = {};
    var pm = r.primary_metric, F = byId(r);
    S.summary = (pm ? '<p>The primary claim is <b>' + esc(F[pm.finding_id] ? F[pm.finding_id].claim : pm.finding_id) + '</b>, graded ' + grade(pm.grade) + '.</p>' : '<p>No claim was tested as the primary metric in this run.</p>') +
      (r.summary && r.summary.lines && r.summary.lines.length ? '<p class="nl2-engine-bottom"><b>Every graded claim, as the engine lists it:</b> ' + esc(r.story.headline) + '</p>' : '') +
      '<p class="note">The engine\'s own narrative follows; every figure in it is a checked fact. The findings table in section 4 carries each test.</p>' + parts.story;
    S.data = dl([
      ['File', '<b>' + esc(r.input.name) + '</b>, ' + esc(num(r.input.rows, 0) + ' rows × ' + num(r.input.columns, 0) + ' columns, ' + num(r.input.bytes, 0) + ' bytes')],
      ['Fingerprint (sha256)', '<code>' + esc(r.input.sha256) + '</code>'],
      ['Analysed as of', esc(P.as_of || 'the day it ran')],
      ['Analysis window', P.window ? esc(P.window.start + ' to ' + P.window.end) : 'none (no date column the engine could use)'],
      ['Health sample', esc(h.sample ? (h.sample.method === 'all' ? 'every row (' + num(h.sample.n, 0) + ')' : 'a sample of ' + num(h.sample.n, 0) + ' rows') : 'n/a')]
    ]) + parts.rolesPriv + parts.cleaning + (CH['cleaning.before_after'] ? fig(CH['cleaning.before_after'], 'a', r) : '') +
      '<h4>Quality by dimension</h4><p>The weakest dimension, <b>' + esc(h.weakest || 'n/a') + '</b>, scores ' + esc(h.score_min === null ? 'n/a' : Number(h.score_min).toFixed(1)) + '; the engine\'s Data Health Score (the mean of the five) is ' + esc(h.score_mean === null ? 'n/a' : Number(h.score_mean).toFixed(1)) + '.</p>' + dims(r) +
      '<h4>Quality by column</h4>' + coltab(r) + (CH.missingness ? fig(CH.missingness, 'a', r) : '') +
      '<p class="note">Accuracy, whether the values match the world: ' + esc((h.accuracy && h.accuracy.text) || 'not measured') + '.</p>';
    S.methods = methods(r);
    var order = ['trend_windows', 'histogram_windows', 'fan', 'replay', 'table', 'heatmap', 'ranked_bars'];
    var rank = function (c) { var i = order.indexOf(c.type); return c.id === 'corr' ? 99 : c.id.indexOf('catmonth.') === 0 ? 8 : c.id.indexOf('season.') === 0 ? 6.5 : i < 0 ? 50 : i; };
    var res = r.charts.filter(function (c) { return ['kpi', 'findings_table', 'benchmark', 'cleaning.before_after', 'missingness'].indexOf(c.id) < 0; })
      .map(function (c, i) { return [c, i]; }).sort(function (a, b) { return rank(a[0]) - rank(b[0]) || a[1] - b[1]; }).map(function (x) { return x[0]; });
    S.results = '<h4>Findings</h4><div data-chart="findings_table" data-findings="' + esc(CH.findings_table ? CH.findings_table.finding_ids.join(' ') : '') + '">' + ftab(r, 'analyst', CH) + '</div>' +
      '<p class="note">p: for a change claim, how often a result at least this strong would appear if the change were smaller than the bar; q: the same after allowing for the claims tested together in its family. Neither is the probability that the claim is true. The interval is the same test inverted; a selection-adjusted bound allows for having picked the confirmed claims out of the family.</p>' +
      '<h4>Forecast</h4>' + forecastStats(r) +
      '<h4>Charts</h4>' + res.map(function (c) {
        if (c.id === 'corr') return '<div class="nl2-ask" data-ask="corr"><p class="note">A correlation map of the ' + esc(num((c.data.measures || []).length, 0)) + ' measures is available. It is descriptive only and supports no finding, so it is off until you ask.</p><button type="button" class="btn btn-ghost" data-act="corr">Show the correlation map</button></div>';
        return c.default_visible ? fig(c, 'a', r) : '';
      }).join('') +
      '<h4>Charts not drawn, and why</h4><ul class="nl2-supp">' + (r.charts_suppressed || []).map(function (s) { return '<li data-rule="' + esc(s.rule) + '">' + esc(String(s.type || '').replace(/_/g, ' ')) + ': ' + esc(s.why) + '</li>'; }).join('') + '</ul>';
    var lim = r.limitations || [];
    var nm = [].concat((en.benchmark && en.benchmark.not_measured || []).map(function (k) { return NOT_MEASURED[k] || k.replace(/_/g, ' '); }),
      [(h.missingness && h.missingness.mcar && h.missingness.mcar.p === null) ? 'whether the empty cells are missing at random (Little\'s test)' : null,
        en.restated_since_previous === null ? 'a list of grades restated since the previous engine release' : null].filter(Boolean));
    S.limits = LIM_KIND.map(function (k) {
      var items = lim.filter(function (l) { return l.kind === k[0]; });
      return items.length ? '<h4>' + k[1] + '</h4><ul>' + items.map(function (l) { return '<li>' + esc(l.text) + (l.finding_ids && l.finding_ids.length ? ' <span class="muted">(' + esc(l.finding_ids.map(function (i) { return F[i] ? F[i].claim : i; }).join('; ')) + ')</span>' : '') + '</li>'; }).join('') + '</ul>' : '';
    }).join('') + (nm.length ? '<h4>Not measured in this release</h4><p class="note">Shown as not measured, never as zero.</p><ul>' + nm.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '');
    var todo = r.story.what_to_do || [];
    S.recs = (todo.length ? '<p class="note">The engine\'s own words; each rests on a graded finding.</p><ul class="nl2-recs">' + todo.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>' : '<p>The engine made no recommendation for this file.</p>') +
      '<h4>What would settle each open claim</h4><ul class="nl2-settle-list">' + settleGroups(r.findings.filter(function (f) { return f.grade !== 'CONFIRMED'; }), 'findings share this answer') + '</ul>';
    var seeds = Object.keys(R.seeds || {}), Bk = Object.keys(R.B || {}), FR = R.figures_reproduced || {};
    S.repro = '<div class="nl2-repro">' + dl([
      ['Figures reproduced', '<b>' + esc(num(FR.k, 0) + ' of ' + num(FR.n, 0) + ' figures') + '</b> re-ran to the same value' + (FR.failed ? '; ' + esc(num(FR.failed, 0)) + ' failed' : '') + '. A hard count: repeatable, not proven correct.'],
      ['Input sha256', '<code>' + esc(R.input_sha256) + '</code>'],
      ['Engine snapshot', '<code>' + esc(R.engine_snapshot) + '</code> (version ' + esc(en.semver || en.version) + ')'],
      ['Decision code snapshot', '<code>' + esc(R.decision_code_snapshot) + '</code>'],
      ['Environment', esc(['python ' + env.python, 'numpy ' + env.numpy, 'pandas ' + env.pandas, 'SQLite ' + env.sqlite, env.pyodide ? 'Pyodide ' + env.pyodide : 'not in a browser'].join(', '))],
      ['Benchmark receipt', esc((en.benchmark.receipt || '') + (en.benchmark.snapshot ? ', measured on change-path snapshot ' + en.benchmark.snapshot : ''))],
      ['Restated since the previous release', en.restated_since_previous === null ? 'not recorded in this release' : esc(String((en.restated_since_previous || []).length))]
    ]) + (seeds.length ? '<div class="tscroll" tabindex="0" role="region" aria-label="Seeds and resample sizes"><table class="dt nl2-seeds"><thead><tr><th scope="col">Recipe</th><th scope="col" class="num">Seed</th><th scope="col" class="num">Resamples (test)</th><th scope="col" class="num">Resamples (interval)</th></tr></thead><tbody>' +
      seeds.map(function (k) { var b = (R.B || {})[k] || {}; return '<tr><th scope="row"><code>' + esc(k) + '</code></th><td class="num">' + esc(String(R.seeds[k])) + '</td><td class="num">' + esc(b.test ? num(b.test, 0) : '') + '</td><td class="num">' + esc(b.interval ? num(b.interval, 0) : '') + '</td></tr>'; }).join('') +
      '</tbody></table></div>' : '') + (Bk.length || seeds.length ? '' : '<p class="note">No resampled test ran.</p>') + '</div>' + (CH.benchmark ? fig(CH.benchmark, 'a', r) : '');
    S.appendix = '<h4>A. Set-aside rows</h4>' + setAside(r) + '<h4>B. Evidence ledger</h4>' + ledger(r);
    return '<nav class="nl2-toc" aria-label="Sections of this report"><ol>' + secs.map(function (s) { return '<li><a href="#nl2-s-' + s[0] + '">' + esc(s[1]) + '</a></li>'; }).join('') + '</ol></nav>' +
      secs.map(function (s, i) { return '<section class="nl2-sec" data-sec="' + s[0] + '" id="nl2-s-' + s[0] + '" aria-labelledby="nl2-h-' + s[0] + '"><h3 id="nl2-h-' + s[0] + '">' + (i + 1) + '. ' + esc(s[1]) + '</h3>' + S[s[0]] + '</section>'; }).join('');
  }

  /* ------------------------------------------------------------ the page */
  // parts: { story, rolesPriv, cleaning } HTML the demo page already writes for v1 (50-try.js)
  N.html = function (r, parts) {
    var CH = chartMap(r);
    return '<div class="nl2" data-contract="2">' +
      '<div class="nl2-views" role="tablist" aria-label="How to read this report">' +
        '<button type="button" role="tab" id="nl2-tab-m" data-view="manager" aria-selected="true" aria-controls="nl2-manager">Manager view <small>summary</small></button>' +
        '<button type="button" role="tab" id="nl2-tab-a" data-view="analyst" aria-selected="false" aria-controls="nl2-analyst" tabindex="-1">Analyst view <small>the full paper</small></button></div>' +
      '<p class="sr" id="nl2-live" aria-live="polite"></p>' +
      '<div class="nl2-panel" id="nl2-manager" role="tabpanel" aria-labelledby="nl2-tab-m">' + manager(r, CH, parts) + '</div>' +
      '<div class="nl2-panel nl2-paper" id="nl2-analyst" role="tabpanel" aria-labelledby="nl2-tab-a" hidden>' + analyst(r, CH, parts) + '</div></div>';
  };

  function draw(panel, r) {
    var CH = chartMap(r);
    Array.prototype.forEach.call(panel.querySelectorAll('figure.nl2-fig'), function (f) {
      var ch = CH[f.getAttribute('data-chart')], v = f.querySelector('.viz'), note = f.querySelector('.nl2-fignote');
      if (!ch || !v || v.getAttribute('data-drawn')) return;
      var fn = C().drawer(ch);
      if (!fn) return;
      v.setAttribute('data-drawn', '1');
      U.visual(v.id, function (W) {
        var out = fn(W, ch, r) || {};
        if (note) note.innerHTML = out.note ? (/^<p/.test(out.note) ? out.note : '<p class="note">' + out.note + '</p>') : '';
        return out;
      });
    });
  }
  function select(root, view, focus) {
    Array.prototype.forEach.call(root.querySelectorAll('.nl2-views [role="tab"]'), function (t) {
      var on = t.getAttribute('data-view') === view;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      if (on && focus) t.focus();
    });
    var M = root.querySelector('#nl2-manager'), A = root.querySelector('#nl2-analyst');
    M.hidden = view !== 'manager'; A.hidden = view !== 'analyst';
    clear(root);
    draw(view === 'manager' ? M : A, root.__rep);
    if (U.redraw) U.redraw();
  }
  function clear(root) {
    Array.prototype.forEach.call(root.querySelectorAll('.nl2-hl'), function (e) { e.classList.remove('nl2-hl'); });
  }
  N.highlight = function (root, ids, from) {
    clear(root);
    var panel = root.querySelector('.nl2-panel:not([hidden])'), live = root.querySelector('#nl2-live'), F = byId(root.__rep);
    if (!panel || !ids.length) return 0;
    var hits = Array.prototype.filter.call(panel.querySelectorAll('[data-fid]'), function (e) { return ids.indexOf(e.getAttribute('data-fid')) >= 0; });
    hits.forEach(function (e) {
      if (e.hidden) e.hidden = false;
      var dt = e.closest('details');
      if (dt && !dt.open) dt.open = true;
      e.classList.add('nl2-hl');
    });
    if (live) live.textContent = hits.length ? 'Highlighted what ' + (from || 'this chart') + ' supports: ' + ids.map(function (i) { return F[i] ? F[i].claim : i; }).join('; ') : 'Nothing on this view carries that claim.';
    var first = hits.filter(function (e) { return !e.closest('.nl2-kpis'); })[0] || hits[0];
    if (first) { try { first.scrollIntoView({ block: 'nearest', behavior: 'auto' }); } catch (e) { first.scrollIntoView(); } }
    return hits.length;
  };
  function sortTable(btn) {
    var th = btn.closest('th'), table = btn.closest('table'), i = +btn.getAttribute('data-col');
    var dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
    Array.prototype.forEach.call(table.querySelectorAll('thead th'), function (x) { x.removeAttribute('aria-sort'); });
    th.setAttribute('aria-sort', dir);
    var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
    var key = function (tr) { var c = tr.children[i]; return c.hasAttribute('data-sort') ? c.getAttribute('data-sort') : c.textContent.trim(); };
    rows.sort(function (a, b) {
      var x = key(a), y = key(b), nx = parseFloat(x), ny = parseFloat(y), r;
      if (x === '' && y !== '') return 1;
      if (y === '' && x !== '') return -1;
      r = (!isNaN(nx) && !isNaN(ny) && isFinite(x) && isFinite(y)) ? nx - ny : x.localeCompare(y);
      return dir === 'ascending' ? r : -r;
    });
    rows.forEach(function (tr) { body.appendChild(tr); });
  }
  var escBound = false;
  N.mount = function (root, r) {
    root.__rep = r;
    draw(root.querySelector('#nl2-manager'), r);
    var tabs = root.querySelectorAll('.nl2-views [role="tab"]');
    Array.prototype.forEach.call(tabs, function (t, i) {
      t.addEventListener('click', function () { select(root, t.getAttribute('data-view')); });
      t.addEventListener('keydown', function (e) {
        var j = e.key === 'ArrowRight' || e.key === 'ArrowLeft' ? (i + 1) % tabs.length : e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : null;
        if (j !== null) { e.preventDefault(); select(root, tabs[j].getAttribute('data-view'), true); }
      });
    });
    root.addEventListener('click', function (e) {
      var t = e.target, b = t.closest && t.closest('button');
      if (b && b.classList.contains('nl2-link')) {
        var f = b.closest('figure');
        N.highlight(root, (f.getAttribute('data-findings') || '').split(' ').filter(Boolean), 'the ' + (f.querySelector('figcaption') || {}).textContent + ' chart');
      } else if (b && b.classList.contains('nl2-sort')) {
        sortTable(b);
      } else if (b && b.getAttribute('data-act') === 'nl2-more') {
        Array.prototype.forEach.call(root.querySelectorAll('#nl2-manager .nl2-more-row'), function (tr) { tr.hidden = false; });
        b.parentNode.remove();
      } else if (b && b.getAttribute('data-act') === 'corr') {
        var box = b.closest('.nl2-ask'), c = chartMap(r).corr;
        if (box && c) {
          var wrap = document.createElement('div');
          wrap.innerHTML = fig(c, 'a', r);
          box.parentNode.replaceChild(wrap.firstChild, box);
          draw(root.querySelector('#nl2-analyst'), r);
          var fg = document.getElementById('nl2c-a-corr');
          if (fg) fg.focus();
        }
      } else if (!b && t.closest && t.closest('figure.nl2-fig .viz svg')) {
        var fg2 = t.closest('figure.nl2-fig'), ids = (fg2.getAttribute('data-findings') || '').split(' ').filter(Boolean);
        if (ids.length) N.highlight(root, ids, 'the ' + (fg2.querySelector('figcaption') || {}).textContent + ' chart');
      }
    });
    if (!escBound) {
      escBound = true;
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        var rr = document.querySelector('.nl2');
        if (rr && rr.querySelector('.nl2-hl')) { clear(rr.parentNode || rr); var live = document.getElementById('nl2-live'); if (live) live.textContent = 'Highlight cleared.'; }
      });
    }
  };
  // before printing: both views on the page, every chart drawn at the width it has, every row shown;
  // returns what puts the page back
  N.preparePrint = function (root) {
    var undo = [], M = root.querySelector('#nl2-manager'), A = root.querySelector('#nl2-analyst');
    if (!M || !A) return function () {};
    [M, A].forEach(function (p) { if (p.hidden) { p.hidden = false; undo.push(function () { p.hidden = true; }); } });
    draw(M, root.__rep); draw(A, root.__rep);
    if (U.redraw) U.redraw();
    Array.prototype.forEach.call(root.querySelectorAll('.nl2-more-row[hidden]'), function (tr) { tr.hidden = false; undo.push(function () { tr.hidden = true; }); });
    return function () { undo.forEach(function (f) { f(); }); if (U.redraw) U.redraw(); };
  };
})();
