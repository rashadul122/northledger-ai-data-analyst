/* The analyst loop's card in the "Try it on your own file" results (engine/nl_analyst.py, CONTRACT
   §5.10): "What the analyst found". Rounds of small typed steps over the rows the engine kept; each
   insight's text is already filled from the numbers its steps recorded, so this page shows it as it
   is and computes nothing. Every insight opens to the steps behind it: the step's operation, why the
   loop ran it, what it read, the numbers it recorded and its table. Descriptive, never tested by the
   engine's gate, and the card says so in the block's own note. Everything shown here comes from a
   visitor's file, so every text is escaped. window.NLA.html(report) returns the card, or '' when the
   report has no analyst block (src/js/50-try.js calls it). */
(function () {
  'use strict';
  var A = {};
  window.NLA = A;
  // the visitor's own text, never markup: escaped here, so the card stays safe whatever loads before it
  var ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return ENT[c]; });
  }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function obj(x) { return !!x && typeof x === 'object' && !Array.isArray(x); }

  // the insight kinds, in the order an analyst reads them, with the heading each group gets
  var KINDS = [['headline', 'The headline'], ['trend', 'The long run'], ['driver', 'What carried the change'],
    ['drill', 'Inside the top group'], ['mix', 'Rate or mix'], ['relationship', 'Measures that move together'],
    ['yoy', 'Against a year earlier'], ['seasonality', 'The seasonal pattern'], ['concentration', 'How concentrated'],
    ['distribution', 'How the values spread'], ['robustness', 'Does it hold up']];
  var OPS = { change: 'change between two periods', panel_change: 'each entry\'s own change', breakdown: 'breakdown by group',
    trend: 'straight-line trend', mix: 'rate and mix split', correlate: 'rank correlation', outliers: 'unusual values',
    rank_entities: 'entries ranked', rank_labels: 'labels ranked', crosstab: 'answers crossed by group',
    yoy: 'same month a year earlier', seasonality: 'seasonal pattern', volatility: 'how much it swings',
    concentration: 'how concentrated the total is', answers: 'most common answers' };
  var SHAPES = { ledger: 'a ledger of dated rows', time_series: 'a time series', panel: 'a panel (entries over time)',
    survey: 'a survey', cross_section: 'a table with no time axis' };
  var ROWS_SHOWN = 12;

  function words(k) { return String(k === null || k === undefined ? '' : k).replace(/_/g, ' '); }
  function count(n) { return typeof n === 'number' && isFinite(n) ? Math.round(n).toLocaleString('en-US') : ''; }

  // a table cell as the step recorded it: a number only grouped for reading, never rounded past what it holds
  function cell(v) {
    if (typeof v === 'number') {
      if (!isFinite(v)) return 'not computable';
      return Math.abs(v) >= 1 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : String(+v.toPrecision(4));
    }
    if (v === null || v === undefined) return '';
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    return typeof v === 'object' ? JSON.stringify(v) : String(v);
  }

  // what a step read: its arguments in plain words (a measure, a split, a filter)
  function argsText(args) {
    if (!obj(args)) return '';
    return Object.keys(args).map(function (k) {
      var v = args[k];
      if (k === 'where' && obj(v)) return 'only rows where ' + Object.keys(v).map(function (c) { return c + ' is ' + cell(v[c]); }).join(' and ');
      if (k === 'where_not' && obj(v)) return 'leaving out rows where ' + Object.keys(v).map(function (c) { return c + ' is ' + cell(v[c]); }).join(' or ');
      if (Array.isArray(v)) return words(k) + ': ' + v.map(cell).join(', ');
      return words(k) + ': ' + cell(v);
    }).join('; ');
  }

  // the numbers a step recorded in its ledger, each as it prints in the insight
  function ledgerText(nums) {
    if (!obj(nums)) return '';
    return Object.keys(nums).filter(function (k) { return obj(nums[k]) && String(nums[k].text || '') !== ''; })
      .map(function (k) { return words(k) + ': ' + nums[k].text; }).join('; ');
  }

  // the step's table, its first ROWS_SHOWN rows, scrolling inside its own box on a phone
  function stepTable(st) {
    var t = obj(st.table) ? st.table : {}, cols = arr(t.columns), rows = arr(t.rows);
    if (!cols.length || !rows.length) return '';
    var shown = rows.slice(0, ROWS_SHOWN), num = cols.map(function (c, i) {
      return shown.some(function (r) { return Array.isArray(r) && typeof r[i] === 'number'; });
    });
    return '<div class="tscroll nla-tbl" tabindex="0" role="region" aria-label="' + esc('Table of step ' + (st.id || '')) + '"><table class="dt"><thead><tr>' +
      cols.map(function (c, i) { return '<th scope="col"' + (num[i] ? ' class="num"' : '') + '>' + esc(cell(c)) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      shown.map(function (r) {
        r = Array.isArray(r) ? r : [];
        return '<tr>' + cols.map(function (c, i) {
          var v = esc(cell(r[i]));
          return i === 0 ? '<th scope="row">' + v + '</th>' : '<td' + (num[i] ? ' class="num"' : '') + '>' + v + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>' +
      (rows.length > shown.length ? '<p class="note">The first ' + count(shown.length) + ' of ' + count(rows.length) + ' rows.</p>' : '');
  }

  function stepHtml(st) {
    var op = String(st.op || ''), how = argsText(st.args), led = ledgerText(st.numbers);
    return '<li class="nla-step"><p class="nla-step-h"><b>Step ' + esc(st.id || '') + '</b>' +
      (typeof st.round === 'number' ? ', round ' + esc(count(st.round)) : '') + ': ' + esc(OPS[op] || words(op)) + ' <code>' + esc(op) + '</code></p>' +
      (st.why ? '<p class="nla-why"><span class="muted">Why:</span> ' + esc(st.why) + '</p>' : '') +
      (how ? '<p class="nla-why"><span class="muted">Read:</span> ' + esc(how) + '</p>' : '') +
      (st.ok === false && st.refused ? '<p class="nla-why"><span class="muted">Not run:</span> ' + esc(st.refused) + '</p>' : '') +
      (led ? '<p class="nla-why"><span class="muted">Numbers recorded:</span> ' + esc(led) + '</p>' : '') +
      stepTable(st) + '</li>';
  }

  function insightHtml(ins, byId) {
    var ids = arr(ins.steps).map(String), steps = ids.map(function (id) { return byId[id]; }).filter(obj);
    return '<li class="nla-ins"><p class="nla-text">' + esc(ins.text) + '</p>' +
      (steps.length ? '<details class="more nla-how"><summary>How this was found: ' + (ids.length === 1 ? 'step ' : 'steps ') + esc(ids.join(', ')) + '</summary>' +
        '<ol class="nla-steps">' + steps.map(stepHtml).join('') + '</ol></details>' : '') + '</li>';
  }

  // one line on what the loop read: the file's shape, the rows, the rounds and steps it ran
  function readLine(b) {
    var sh = obj(b.shape) ? b.shape : {}, rows = obj(b.rows) ? b.rows : {}, bits = [];
    if (sh.kind) bits.push('Read as ' + (SHAPES[sh.kind] || words(sh.kind)) + (sh.grain ? ', by ' + words(sh.grain) : '') + (sh.date ? ', dated by ' + sh.date : '') + '.');
    var n = arr(b.steps).length, k = typeof b.rounds === 'number' ? b.rounds : null;
    var ran = (k !== null ? count(k) + (k === 1 ? ' round' : ' rounds') + ', ' : '') + count(n) + (n === 1 ? ' step' : ' steps');
    bits.push(typeof rows.kept === 'number' ? ran + ' over the ' + count(rows.kept) + ' rows the engine kept' +
      (typeof rows.set_aside === 'number' && rows.set_aside ? ' (' + count(rows.set_aside) + ' set aside, not read)' : '') + '.' : ran + '.');
    return bits.join(' ');
  }

  A.html = function (r) {
    var b = r && obj(r.analyst) ? r.analyst : null;
    if (!b) return '';
    var ins = arr(b.insights).filter(function (i) { return obj(i) && typeof i.text === 'string' && i.text.trim(); });
    var refused = arr(b.refused).filter(obj);
    if (!ins.length && !refused.length) return '';
    var byId = {};
    arr(b.steps).forEach(function (s) { if (obj(s) && s.id) byId[String(s.id)] = s; });
    // insights grouped by kind, in KINDS order; a kind this page does not know follows, under its own name
    var groups = KINDS.map(function (k) { return { kind: k[0], title: k[1], items: [] }; });
    ins.forEach(function (i) {
      var kind = String(i.kind || 'other'), g = groups.filter(function (x) { return x.kind === kind; })[0];
      if (!g) { g = { kind: kind, title: words(kind).replace(/^./, function (c) { return c.toUpperCase(); }), items: [] }; groups.push(g); }
      g.items.push(i);
    });
    var note = b.note ? String(b.note) : 'Descriptive: computed from the rows the engine kept, not tested by the engine\'s gate.';
    var h = '<section class="tr-card nla" aria-labelledby="nla-h"><p class="kicker">Descriptive, not tested</p><h3 id="nla-h">What the analyst found</h3>' +
      '<p class="note">' + esc(note) + ' Each sentence is filled from the numbers its steps recorded; open one to see them.</p>';
    if (ins.length) {
      h += '<p class="nla-read muted">' + esc(readLine(b)) + '</p>';
      groups.filter(function (g) { return g.items.length; }).forEach(function (g, gi) {
        h += '<div class="nla-group" role="group" aria-labelledby="nla-g-' + gi + '"><h4 id="nla-g-' + gi + '">' + esc(g.title) + '</h4><ul class="nla-list">' +
          g.items.map(function (i) { return insightHtml(i, byId); }).join('') + '</ul></div>';
      });
    } else {
      h += '<p>The analyst loop found nothing it could state for this file.</p>';
    }
    if (refused.length) {
      h += '<details class="more nla-refused"><summary>' + (ins.length ? 'Steps it did not run (' + count(refused.length) + ')' : 'Why it did not run') + '</summary><ul class="nla-list">' +
        refused.map(function (x) { return '<li>' + (x.op && x.op !== 'all' ? '<code>' + esc(x.op) + '</code>: ' : '') + esc(x.why || '') + '</li>'; }).join('') + '</ul></details>';
    }
    return h + '</section>';
  };
}());
