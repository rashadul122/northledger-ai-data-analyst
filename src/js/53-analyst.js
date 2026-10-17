/* The analyst loop's card in the "Try it on your own file" results (engine/nl_analyst.py, CONTRACT
   §5.10): "What the analyst found". Rounds of small typed steps over the rows the engine kept; each
   insight's text is already filled from the numbers its steps recorded, so this page shows it as it
   is and computes nothing. Every insight opens to the steps behind it: the step's operation, why the
   loop ran it, what it read, the numbers it recorded and its table. Descriptive, never tested by the
   engine's gate, and the card says so in the block's own note. Everything shown here comes from a
   visitor's file, so every text is escaped. window.NLA.html(report) returns the card, or '' when the
   report has no analyst block (src/js/50-try.js calls it); window.NLA.mount(root) then draws each insight's chart. */
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
  var KINDS = [['headline', 'The headline'], ['planned', 'What you asked'], ['trend', 'The long run'], ['driver', 'What carried the change'],
    ['drill', 'Inside the top group'], ['mix', 'Rate or mix'], ['relationship', 'Measures that move together'],
    ['yoy', 'Against a year earlier'], ['seasonality', 'The seasonal pattern'], ['concentration', 'How concentrated'],
    ['distribution', 'How the values spread'], ['robustness', 'Does it hold up']];
  var OPS = { change: 'change between two periods', panel_change: 'each entry\'s own change', breakdown: 'breakdown by group',
    trend: 'straight-line trend', mix: 'rate and mix split', correlate: 'rank correlation', outliers: 'unusual values',
    rank_entities: 'entries ranked', rank_labels: 'labels ranked', crosstab: 'answers crossed by group',
    yoy: 'same month a year earlier', seasonality: 'seasonal pattern', volatility: 'how much it swings',
    concentration: 'how concentrated the total is', answers: 'most common answers',
    periods: 'counts by period, read against size', confound: 'group averages read against size',
    frequency: 'what the rows are mostly about', sql: 'a read-only SQL query on the rows the engine kept',
    rate_mix: 'a ratio\'s change split into rate and mix' };
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
    return Object.keys(args).filter(function (k) { return k !== 'sql'; }).map(function (k) {
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

  // an sql step's query (Ask your data with SQL, CONTRACT §5.16): shown as written, read-only, with a Copy button
  function sqlHtml(st) {
    var q = obj(st.args) && typeof st.args.sql === 'string' ? st.args.sql : '';
    if (!q) return '';
    return '<div class="nla-sql"><p class="nla-sql-h"><span class="nla-ro">Read-only SQL</span> run in your browser on the table <code>kept_rows</code> (the rows the engine kept)' +
      '<button type="button" class="btn btn-ghost btn-sm nla-copy" data-copy-sql="1">Copy SQL</button></p>' +
      '<pre class="nla-sql-q" tabindex="0" aria-label="The SQL query"><code>' + esc(q) + '</code></pre></div>';
  }

  function stepHtml(st) {
    var op = String(st.op || ''), how = argsText(st.args), led = ledgerText(st.numbers);
    return '<li class="nla-step"><p class="nla-step-h"><b>Step ' + esc(st.id || '') + '</b>' +
      (typeof st.round === 'number' ? ', round ' + esc(count(st.round)) : '') + ': ' + esc(OPS[op] || words(op)) + ' <code>' + esc(op) + '</code></p>' +
      (st.why ? '<p class="nla-why"><span class="muted">Why:</span> ' + esc(st.why) + '</p>' : '') +
      (how ? '<p class="nla-why"><span class="muted">Read:</span> ' + esc(how) + '</p>' : '') +
      (st.ok === false && st.refused ? '<p class="nla-why"><span class="muted">Not run:</span> ' + esc(st.refused) + '</p>' : '') +
      (led ? '<p class="nla-why"><span class="muted">Numbers recorded:</span> ' + esc(led) + '</p>' : '') +
      sqlHtml(st) + stepTable(st) + '</li>';
  }

  // the chart behind an insight (ins.figure, a chart registry record the engine built from the step's own numbers):
  // a figure drawn by window.NLV at the width it has, with its Table button, once the card is on the page (A.mount)
  var FIGS = {}, figN = 0;
  function figureHtml(rec) {
    if (!obj(rec) || typeof rec.kind !== 'string' || !obj(rec.data)) return '';
    var id = 'nla-fig-' + (++figN);
    FIGS[id] = rec;
    return '<figure class="visual nla-fig" data-table="1" data-kind="' + esc(rec.kind) + '"><figcaption>' + esc(rec.title || 'Chart') + '</figcaption>' +
      '<div class="viz" id="' + id + '"></div><div class="nla-fig-note"></div></figure>';
  }
  A.mount = function (root) {
    if (root) bindActions(root);                  // the report card's own buttons (Copy SQL, Add to the dashboard)
    var panel = document.getElementById('nla-askp');
    if (panel) { bindPanel(panel); bindAsk(panel); }
    var U = window.NLU;
    if (!root || !U || !U.visual || !window.NLV) return;
    if (window.NLC && window.NLC.extendNLV) window.NLC.extendNLV(window.NLV);
    Array.prototype.forEach.call(root.querySelectorAll('.nla-fig .viz[id^="nla-fig-"]'), function (v) {
      var rec = FIGS[v.id], note = v.parentNode.querySelector('.nla-fig-note');
      if (!rec) return;
      U.visual(v.id, function (W) {
        if (v.clientWidth) W = Math.min(W, Math.floor(v.clientWidth));
        var o = window.NLV.draw(rec, W);
        if (note) note.innerHTML = o.note || '';
        return o;
      });
    });
  };

  // "Add this chart to the dashboard" (step 4 Ask -> step 5 Dashboard, plan/DASHBOARD-DESIGN.md): an asked answer's
  // chart, as its chart registry record, goes to window.NL_DASH_PINS and an 'nl:pin-chart' event the dashboard reads
  var PINS = {};
  function pinHtml(ins) {
    if (ins.kind !== 'planned' || !obj(ins.figure) || !ins.id) return '';
    PINS[String(ins.id)] = ins;
    var on = arr(window.NL_DASH_PINS).some(function (p) { return p && p.pinned_from && p.pinned_from.insight === ins.id; });
    return '<p class="nla-pin-row"><button type="button" class="btn btn-ghost btn-sm nla-pin" data-pin="' + esc(ins.id) + '"' + (on ? ' disabled' : '') + '>' +
      (on ? 'Added to the dashboard' : 'Add this chart to the dashboard') + '</button></p>';
  }
  A.pin = function (insId) {
    var ins = PINS[String(insId)];
    if (!ins || !obj(ins.figure)) return null;
    var list = window.NL_DASH_PINS = Array.isArray(window.NL_DASH_PINS) ? window.NL_DASH_PINS : [];
    var had = list.filter(function (p) { return p && p.pinned_from && p.pinned_from.insight === ins.id; })[0];
    if (had) return had;
    var rec = JSON.parse(JSON.stringify(ins.figure));
    rec.pinned_from = { source: 'ask-your-data', insight: ins.id, question: A.lastQuestion || '', steps: arr(ins.steps).map(String), text: ins.text };
    list.push(rec);
    try { document.dispatchEvent(new CustomEvent('nl:pin-chart', { detail: rec })); } catch (e) { /* an old browser keeps the list */ }
    return rec;
  };

  function insightHtml(ins, byId) {
    var ids = arr(ins.steps).map(String), steps = ids.map(function (id) { return byId[id]; }).filter(obj);
    var sqlStep = steps.filter(function (x) { return x.op === 'sql' && x.ok; })[0];
    return '<li class="nla-ins' + (obj(ins.figure) ? ' nla-story' : '') + '"' + (ins.id ? ' id="' + esc(anchor(ins.id)) + '"' : '') + '><p class="nla-text">' + esc(ins.text) + '</p>' +
      (sqlStep ? sqlHtml(sqlStep) + stepTable(sqlStep) : '') + figureHtml(ins.figure) + pinHtml(ins) +
      (steps.length ? '<details class="more nla-how"><summary>How this was found: ' + (ids.length === 1 ? 'step ' : 'steps ') + esc(ids.join(', ')) + '</summary>' +
        '<ol class="nla-steps">' + steps.map(stepHtml).join('') + '</ol></details>' : '') + '</li>';
  }

  // the story in brief (rep.analyst.story, built by the engine from the insights alone): the headline as a lead, the
  // key numbers as tiles, then the findings, what drove them, what to check next and what to keep in mind. Every line
  // links down to the insight that proves it, where its chart and its steps are.
  function anchor(id) { return 'nla-' + String(id).replace(/[^A-Za-z0-9_-]/g, ''); }
  function proofLink(line, ids) {
    var to = arr(line && line.insights).filter(function (i) { return ids[String(i)]; })[0] || (line && ids[String(line.insight)] ? line.insight : null);
    return to ? ' <a class="nla-see" href="#' + esc(anchor(to)) + '">' + (ids[String(to)] === 'chart' ? 'see the chart' : 'see how') + '</a>' : '';
  }
  function storyLines(title, lines, ids, ordered) {
    lines = arr(lines).filter(function (l) { return obj(l) && typeof l.text === 'string' && l.text.trim(); });
    if (!lines.length) return '';
    var tag = ordered ? 'ol' : 'ul';
    return '<div class="nla-brief-part"><h5>' + esc(title) + '</h5><' + tag + ' class="nla-brief-list">' +
      lines.map(function (l) { return '<li>' + esc(l.text) + proofLink(l, ids) + '</li>'; }).join('') + '</' + tag + '></div>';
  }
  function briefHtml(st, ids) {
    if (!obj(st) || !obj(st.headline) || !st.headline.text) return '';
    var tiles = arr(st.key_numbers).filter(function (k) { return obj(k) && k.value; }).slice(0, 4);
    return '<div class="nla-brief" role="group" aria-labelledby="nla-brief-h"><h4 id="nla-brief-h">The story in brief</h4>' +
      '<p class="nla-lead">' + esc(st.headline.text) + proofLink(st.headline, ids) + '</p>' +
      (tiles.length ? '<ul class="nla-kpis" aria-label="Key numbers">' + tiles.map(function (k) {
        var inner = '<span class="nla-kpi-v">' + esc(k.value) + '</span><span class="nla-kpi-l">' + esc(k.label || '') + '</span>' +
          (k.note ? '<span class="nla-kpi-n">' + esc(k.note) + '</span>' : '');
        return '<li class="nla-kpi">' + (ids[String(k.insight)] ? '<a class="nla-kpi-in" href="#' + esc(anchor(k.insight)) + '">' + inner + '</a>' : '<div class="nla-kpi-in">' + inner + '</div>') + '</li>';
      }).join('') + '</ul>' : '') +
      storyLines('What stands out', st.findings, ids, true) +
      storyLines('What drove it', st.drivers, ids, false) +
      storyLines('What to check next', st.actions, ids, false) +
      storyLines('Keep in mind', st.caveats, ids, false) + '</div>';
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

  // "Ask your data" (docs/ai-analyst/PROTOCOL.md): shown only when src/js/50-try.js sets A.onAsk, which it does when
  // the visitor chose the AI. The question and the steps so far (their printed figures, never a row) go to the AI
  // planner; the steps it asks for run here, in the engine, checked again, and every figure in an answer is the
  // engine's. A.onAsk(question, say) calls say(text, busy) as it goes.
  A.onAsk = null;
  A.aiOn = false;      // set by src/js/50-try.js: whether the AI plans the checks (else the engine does)
  A.example = '';      // a question to offer with one click (the sales sample's)
  function askHtml(b) {
    if (typeof A.onAsk !== 'function') return '';
    var asked = arr(b.asked).filter(function (q) { return typeof q === 'string' && q; });
    return '<form class="nla-ask" novalidate><label for="nla-q">A question about these rows, in your own words: the answer is computed from your file, with the query behind it</label>' +
      (asked.length ? '<p class="nla-asked muted">You asked: ' + asked.map(function (q) { return '\u201c' + esc(q) + '\u201d'; }).join(', ') + '</p>' : '') +
      '<div class="nla-ask-row"><input id="nla-q" name="q" type="text" maxlength="300" autocomplete="off" placeholder="' + esc(A.example ? 'e.g. ' + A.example : 'e.g. Why is revenue down in a region this quarter?') + '" required>' +
      '<button type="submit" class="btn btn-primary">Ask</button></div>' +
      (A.example ? '<p class="nla-ask-ex">Try: <button type="button" class="btn btn-ghost btn-sm" data-ask-example="' + esc(A.example) + '">' + esc(A.example) + '</button></p>' : '') +
      '<p class="nla-ask-note muted">' + (A.aiOn ? 'The AI only picks which checks to run, a SQL query among them: it sees your column names and the figures above, never a row. '
        : 'The AI is off, so the engine plans the checks itself from your question. ') +
      'The checks and any SQL run here, in your browser, on the rows the engine kept (never a withheld column), and every number in the answer is computed by the engine.</p>' +
      '<p class="nla-ask-status" role="status" aria-live="polite"></p></form>';
  }
  function bindActions(root) {
    // on the card itself (never an outer box too: a refresh mounts the new card, and a click must act once)
    if (root && !(root.matches && root.matches('section.nla'))) root = root.querySelector('section.nla');
    if (!root || root.getAttribute('data-nla-acts')) return;
    root.setAttribute('data-nla-acts', '1');
    root.addEventListener('click', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!t) return;
      if (t.hasAttribute('data-copy-sql')) {
        var code = t.closest('.nla-sql').querySelector('code'), txt = code ? code.textContent : '';
        var done = function (ok) { t.textContent = ok ? 'Copied' : 'Select the query to copy it'; setTimeout(function () { t.textContent = 'Copy SQL'; }, 1600); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(function () { done(true); }, function () { done(false); });
        else done(false);
      } else if (t.hasAttribute('data-pin')) {
        if (A.pin(t.getAttribute('data-pin'))) { t.textContent = 'Added to the dashboard'; t.disabled = true; }
      } else if (t.hasAttribute('data-ask-example')) {
        var f = t.closest('form.nla-ask'), input = f && f.querySelector('input');
        if (input) { input.value = t.getAttribute('data-ask-example'); }
        if (f && f.requestSubmit) f.requestSubmit(); else if (f) f.dispatchEvent(new Event('submit', { cancelable: true }));
      }
    });
  }
  function bindAsk(root) {
    if (!(root.matches && root.matches('section.nla-askp'))) bindActions(root);
    var f = root.querySelector('form.nla-ask');
    if (!f || f.getAttribute('data-bound')) return;
    f.setAttribute('data-bound', '1');
    var input = f.querySelector('input'), btn = f.querySelector('button'), out = f.querySelector('.nla-ask-status');
    function say(text, busy) {
      if (out) out.textContent = text || '';
      if (input) input.disabled = !!busy;
      if (btn) btn.disabled = !!busy;
      f.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var q = input ? String(input.value || '').replace(/\s+/g, ' ').trim().slice(0, 300) : '';
      if (q.length < 3) { say('Type a question first.', false); if (input) input.focus(); return; }
      A.lastQuestion = q;
      if (typeof A.onAsk === 'function') A.onAsk(q, say);
    });
  }
  // the card again, in place, after an AI round added to the block; the page scrolls to the first new insight
  A.refresh = function (root, r, focusId) {
    var old = root && root.querySelector('section.nla');
    if (!old) return;
    var box = document.createElement('div');
    box.innerHTML = A.html(r);
    var sec = box.querySelector('section.nla');
    if (!sec) return;
    old.parentNode.replaceChild(sec, old);
    A.mount(sec);
    var to = focusId ? document.getElementById(anchor(focusId)) : null;
    if (to) {
      to.setAttribute('tabindex', '-1');
      if (to.scrollIntoView) to.scrollIntoView({ block: 'start', behavior: 'smooth' });
      try { to.focus({ preventScroll: true }); } catch (e) { /* an old browser */ }
    }
  };

  A.html = function (r) {
    var b = r && obj(r.analyst) ? r.analyst : null;
    if (!b) return '';
    FIGS = {};
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
      var ids = {};
      ins.forEach(function (i) { if (i.id) ids[String(i.id)] = obj(i.figure) ? 'chart' : 'text'; });
      h += briefHtml(b.story, ids);
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
    // the question box and its answers live in step 4 (A.askPanel): the card says where
    return h + (typeof A.onAsk === 'function' ? '<p class="nla-goask"><a href="#try-step-4">Ask your own question about this data in step 4</a></p>' : '') + '</section>';
  };

  // ---- step 4 "Ask" (owner, 17 Oct 2026: "I can't see any answer it gave when I ask two questions; that should be easily
  // navigated"): the question box and every question with its answer, newest first, in one panel the step rail's "4 Ask" opens.
  // Each answer leads with the engine's sentence written from the query's result, then that result as a table, then the query.
  function answerHtml(a, n) {
    if (!obj(a)) return '';
    var cols = arr(a.columns), rows = arr(a.rows);
    var table = cols.length && rows.length ? '<div class="tscroll"><table class="dt nla-qa-t"><thead><tr>' +
      cols.map(function (c) { return '<th scope="col">' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r, ri) {
        return '<tr>' + arr(r).map(function (c, i) {
          if (i === 0) return '<th scope="row">' + esc(c) + '</th>';
          var w = i === 1 && arr(a.bars).length === rows.length ? a.bars[ri] : null;
          return '<td class="num">' + (typeof w === 'number' && isFinite(w) ? '<span class="nla-qa-bar" aria-hidden="true" style="width:' + Math.max(1, Math.round(w * 120)) + 'px"></span>' : '') + esc(c) + '</td>';
        }).join('') + '</tr>';
      }).join('') +
      '</tbody></table></div>' + (a.rows_total > rows.length ? '<p class="muted nla-qa-more">The first ' + count(rows.length) + ' of ' + count(a.rows_total) + ' rows.</p>' : '') : '';
    var extra = arr(a.findings).filter(function (x) { return typeof x === 'string' && x; });
    return '<li class="nla-qa' + (a.failed ? ' nla-qa-fail' : '') + '" id="nla-qa-' + n + '" tabindex="-1">' +
      '<p class="nla-qa-q"><span class="kicker">Question ' + (n + 1) + '</span> ' + esc(a.question || '') + '</p>' +
      '<p class="nla-qa-a">' + esc(a.text || '') + '</p>' + table +
      (extra.length ? '<ul class="nla-qa-x">' + extra.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') +
      (a.sql ? '<details class="nla-sql"><summary>The query behind this answer</summary><div class="nla-sql-h"><button type="button" class="btn btn-ghost btn-sm nla-copy" data-copy-sql>Copy SQL</button></div><pre class="nla-sql-q"><code>' + esc(a.sql) + '</code></pre></details>' : '') +
      (a.pin ? '<p><button type="button" class="btn btn-ghost btn-sm" data-pin="' + esc(a.pin) + '">Add to the dashboard</button></p>' : '') +
      (a.by ? '<p class="muted nla-qa-by">' + esc(a.by) + '</p>' : '') + '</li>';
  }
  function threadHtml(b) {
    var list = arr(b && b.answers);
    if (!list.length) return '<li class="nla-qa-none muted">Your questions and their answers will appear here, the newest first.</li>';
    return list.map(function (a, i) { return { a: a, i: i }; }).reverse().map(function (x) { return answerHtml(x.a, x.i); }).join('');
  }
  A.askPanel = function (r) {
    if (typeof A.onAsk !== 'function') return '';
    var b = r && obj(r.analyst) ? r.analyst : {};
    return '<section class="tr-card nla-askp" id="nla-askp" data-step-home="4" aria-labelledby="nla-askp-h"><h3 id="nla-askp-h">Ask your data</h3>' +
      askHtml(b) + '<ol class="nla-thread" id="nla-thread" aria-label="Your questions and their answers, the newest first">' + threadHtml(b) + '</ol></section>';
  };
  // the list again after an answer, and the newest answer brought into view and focused
  A.showAnswer = function (r, n) {
    var ol = document.getElementById('nla-thread');
    if (!ol) return;
    ol.innerHTML = threadHtml(r && r.analyst);
    var li = document.getElementById('nla-qa-' + n);
    if (li) {
      if (li.scrollIntoView) li.scrollIntoView({ block: 'start', behavior: 'smooth' });
      try { li.focus({ preventScroll: true }); } catch (e) { /* an old browser */ }
    }
  };
  function bindPanel(p) {
    if (!p || p.getAttribute('data-nla-acts')) return;
    p.setAttribute('data-nla-acts', '1');
    p.addEventListener('click', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!t) return;
      if (t.hasAttribute('data-copy-sql')) {
        var code = t.closest('.nla-sql').querySelector('code'), txt = code ? code.textContent : '';
        var done = function (ok) { t.textContent = ok ? 'Copied' : 'Select the query to copy it'; setTimeout(function () { t.textContent = 'Copy SQL'; }, 1600); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(function () { done(true); }, function () { done(false); });
        else done(false);
      } else if (t.hasAttribute('data-pin')) {
        if (A.pin(t.getAttribute('data-pin'))) { t.textContent = 'Added to the dashboard'; t.disabled = true; }
      } else if (t.hasAttribute('data-ask-example')) {
        var f = t.closest('form.nla-ask'), input = f && f.querySelector('input');
        if (input) { input.value = t.getAttribute('data-ask-example'); }
        if (f && f.requestSubmit) f.requestSubmit(); else if (f) f.dispatchEvent(new Event('submit', { cancelable: true }));
      }
    });
  }
}());
