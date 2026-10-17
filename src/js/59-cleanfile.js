/* "Clean my file" (workflow step 2, track C; engine/nl_cleanfile.py, CONTRACT §5.15), inside the report's existing
   cleaning section ("What the cleaning did" / "What was set aside"). It is a second VIEW of the same cleaning, never a
   second cleaner: window.NLTry.cleanFile asks the run's own worker (engine/worker.js "cleanfile") for
     * a cleaned copy of the visitor's file: every value as the engine's cleaner wrote it, exact duplicates merged into
       the first copy, every row the analysis sets aside kept and cleaned with only its doubtful cell as written, every
       value it could not read kept as written, each named in nl_flag; blank cells in nl_missing, nothing filled in;
     * a Markdown log whose rule table is the report's own cleaning record (same rules, words and counts);
     * the "Cleaning rules" card: per column what the cleaner expects (its rules in words, each switchable and
       tunable) and the duplicate rule. Editing runs the same cleaner again in this tab, no AI call.
   Column MEANINGS come from the one shared "What each column means" card of step 1 when it is on the page
   (window.NLColumns.meaning(header), event 'nl:columns'), else from the AI plan's reading; this card keeps only what
   is cleaning-specific. An edit marks the later steps out of date: it dispatches 'nl:step-status'
   {step: 2, status: 'done', edited: true, outOfDate: [3, 4, 5, 6]}.
   Everything shown comes from the visitor's file, so every text is escaped. window.NLClean.html(report) returns the
   card (src/js/50-try.js cleaningHtml); window.NLClean.mount(root, report) fills it. */
(function () {
  'use strict';
  var K = {};
  window.NLClean = K;
  var ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return ENT[c]; }); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function num(n) { return Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  function plural(n, one, many) { return num(n) + ' ' + (Number(n) === 1 ? one : many); }
  function stem(name) { return String(name || 'file').replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'file'; }
  function emit(detail) { try { document.dispatchEvent(new CustomEvent('nl:step-status', { detail: detail })); } catch (e) { /* an old browser: no rail to tell */ } }

  var KIND = { null_like: 'Placeholders (N/A, a dash ...) read as blank', trim: 'Spaces trimmed', date: 'Read as dates', numeric: 'Read as numbers',
    boolean: 'Yes/no spellings made one', case: 'Spelling made one', range: 'Range', not_future: 'Not after a date',
    date_plausible: 'Placeholder dates (1900-01-01 ...)', series_end: 'Rows after the series ends', allowed: 'Allowed values', not_null: 'Required' };
  var CASES = [['dominant', 'The most common spelling'], ['upper', 'CAPITALS'], ['title', 'Title Case'], ['lower', 'lower case']];
  var ORDERS = [['auto', 'Decide from the values'], ['dmy', 'Day first (31/12/2025)'], ['mdy', 'Month first (12/31/2025)']];
  var st = { out: null, name: '', busy: false, root: null, rules: null };
  // the visitor's edited rules, as last applied here (null: the engine's): Build a tracker (src/js/63-tracker.js) reads
  // them so its rows are this cleaner's under the same rules
  K.rules = function () { return st.out && st.out.ok && st.out.edited ? st.rules : null; };

  K.html = function () {
    return '<article class="tr-card tr-clean" id="try-clean" aria-labelledby="try-clean-h" data-step="2" data-step-home="2">' +
      '<h3 id="try-clean-h">Clean my file</h3>' +
      '<p class="note">The cleaning above, as a copy of your whole file you can take away, with a log of every rule. Your original file is not changed. ' +
      'Where the analysis sets a row aside or cannot read a value, this copy keeps the row, cleans its other cells like every row, keeps only the doubtful value exactly as written and names it in a flag; missing values are left blank and listed, never filled in.</p>' +
      '<p class="tr-clean-status" id="try-clean-status" role="status" aria-live="polite">Making the cleaned copy…</p>' +
      '<div class="tr-clean-out" id="try-clean-out" hidden></div></article>';
  };

  function opts(list, cur) {
    return list.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (o[0] === cur ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('');
  }
  var uid = 0;
  function meaning(c) {
    var N = window.NLColumns, m = null;
    try { if (N && typeof N.meaning === 'function') m = N.meaning(c.header); } catch (e) { m = null; }
    if (m && typeof m === 'object') m = m.text || m.meaning || null;
    if (m) return '<p class="cf-ev">Means: ' + esc(m) + ' <span class="cf-src">from “What each column means”</span></p>';
    return c.ai_words ? '<p class="cf-ev">The AI plan reads it as ' + esc(c.ai_words) + '.</p>' : '';
  }
  function ruleHtml(r) {
    var id = 'cf-r' + (++uid), h = '<li class="cf-rule" data-rule="' + esc(r.name) + '" data-kind="' + esc(r.kind) + '">' +
      '<label class="cf-key" for="' + id + '"><input type="checkbox" id="' + id + '" data-k="on"' + (r.on !== false ? ' checked' : '') + '> ' +
      esc(KIND[r.kind] || r.kind) + ' <code>' + esc(r.name) + '</code></label>';
    if (r.kind === 'date') {
      h += '<div class="cf-fields"><label for="' + id + 'o">Day and month</label><select id="' + id + 'o" data-k="order">' + opts(ORDERS, r.order || 'auto') + '</select></div>';
    } else if (r.kind === 'range') {
      h += '<div class="cf-fields"><label for="' + id + 'a">At least</label><input id="' + id + 'a" data-k="min" type="text" inputmode="decimal" value="' + esc(r.min === null || r.min === undefined ? '' : r.min) + '" placeholder="no minimum">' +
        '<label for="' + id + 'b">At most</label><input id="' + id + 'b" data-k="max" type="text" inputmode="decimal" value="' + esc(r.max === null || r.max === undefined ? '' : r.max) + '" placeholder="no maximum"></div>';
    } else if (r.kind === 'case') {
      h += '<div class="cf-fields"><label for="' + id + 'm">Spelling</label><select id="' + id + 'm" data-k="mode">' + opts(CASES, r.mode) + '</select></div>';
    } else if (r.kind === 'not_future') {
      h += '<div class="cf-fields"><label for="' + id + 'd">Not after</label><input id="' + id + 'd" data-k="as_of" type="date" value="' + esc(r.as_of || '') + '"></div>';
    }
    return h + '</li>';
  }
  function colHtml(c) {
    if (c.private) {
      return '<li class="cf-col cf-private" data-col="' + esc(c.column) + '"><div class="cf-head"><b class="cf-name">' + esc(c.header) + '</b><span class="cf-src">your choice</span></div><p class="cf-ev">' +
        (c.private === 'withhold' ? 'Withheld on the personal-data step: no rule reads it, and it is left out of the cleaned copy, as from every download here. The log calls it “a withheld column”.'
          : 'Coded on the personal-data step: no rule reads it; the cleaned copy holds its codes. The log calls it “a coded column”.') + '</p></li>';
    }
    var allowed = arr(c.rules).filter(function (r) { return r.kind === 'allowed'; })[0], id = 'cf-al' + (++uid);
    return '<li class="cf-col" data-col="' + esc(c.column) + '"><div class="cf-head"><b class="cf-name">' + esc(c.header) + '</b></div>' +
      '<p class="cf-ev"><span class="cf-src">Should contain:</span> ' + esc(c.contains) + '</p>' + meaning(c) +
      (arr(c.rules).filter(function (r) { return r.kind !== 'allowed'; }).length ? '<ul class="cf-rules-l">' + c.rules.filter(function (r) { return r.kind !== 'allowed'; }).map(ruleHtml).join('') + '</ul>' : '') +
      '<div class="cf-fields"><label for="' + id + '">Allowed values</label><textarea id="' + id + '" data-k="allowed" rows="2" placeholder="any value (one per line to restrict)">' + esc(allowed ? arr(allowed.values).join('\n') : '') + '</textarea></div></li>';
  }
  function dupHtml(cd) {
    var D = cd.duplicates || {}, key = arr(D.key);
    return '<fieldset class="cf-dup"><legend>What counts as a duplicate</legend>' +
      '<label class="cf-radio"><input type="checkbox" id="cf-dup-on"' + (D.on !== false ? ' checked' : '') + '> Merge duplicates into the first copy</label>' +
      '<p class="cf-hint">Duplicates are ' + esc(D.words || 'rows equal in every column') + '. To match on some columns only, tick them:</p>' +
      '<div class="cf-keys">' + arr(cd.columns).filter(function (c) { return !c.private; }).map(function (c) {
        return '<label class="cf-key"><input type="checkbox" data-key="' + esc(c.column) + '"' + (key.indexOf(c.column) >= 0 ? ' checked' : '') + '> ' + esc(c.header) + '</label>'; }).join('') + '</div></fieldset>';
  }
  function generalHtml(cd) {
    var g = arr(cd.general);
    return g.length ? '<ul class="cf-rules-l cf-general">' + g.map(ruleHtml).join('') + '</ul>' : '';
  }

  function summaryHtml(out) {
    var S = out.summary || {}, miss = 0;
    Object.keys(S.missing_cells || {}).forEach(function (k) { miss += Number(S.missing_cells[k] || 0); });
    return '<ul class="tr-clean-kpis">' +
      '<li><span class="k">Rows</span><b>' + esc(num(S.rows_in)) + ' → ' + esc(num(S.rows_out)) + '</b></li>' +
      '<li><span class="k">Duplicates merged</span><b>' + esc(num(S.merged)) + '</b></li>' +
      '<li><span class="k">Rows flagged, kept</span><b>' + esc(num(S.flagged_rows)) + '</b></li>' +
      '<li><span class="k">Blank cells, left blank</span><b>' + esc(num(miss)) + '</b></li></ul>' +
      '<div class="tr-actions"><button type="button" class="btn btn-primary" data-clean-dl="csv">Download cleaned file (CSV)</button>' +
      '<button type="button" class="btn btn-ghost" data-clean-dl="log">Download cleaning log (Markdown)</button></div>' +
      '<p class="note">' + (S.edited ? '<b>Your edited rules.</b> The report above keeps the engine\'s own. ' : 'The same rules and counts as the cleaning above. ') +
      esc(plural(S.kept_set_aside, 'row the analysis sets aside is', 'rows the analysis sets aside are')) + ' kept, cleaned and flagged; ' +
      esc(plural(Object.keys(S.cells_restored || {}).reduce(function (a, k) { return a + Number(S.cells_restored[k] || 0); }, 0), 'unreadable value is', 'unreadable values are')) + ' kept as written. Columns: ' +
      esc(num(S.columns_in)) + ' in, ' + esc(num(S.columns_out)) + ' out (nl_flag and nl_missing added' +
      (S.withheld_left_out ? '; ' + esc(plural(S.withheld_left_out, 'withheld column', 'withheld columns')) + ' left out, as you chose' : '') + ').</p>';
  }

  function draw(root, out, said) {
    var box = root.querySelector('#try-clean-out'), status = root.querySelector('#try-clean-status');
    if (!box || !status) return;
    if (!out || !out.ok) {
      status.textContent = out && out.error ? out.error : 'The run is no longer loaded in this page; run the file again to clean it.';
      box.hidden = true;
      emit({ step: 2, status: 'attention' });
      return;
    }
    st.out = out;
    var was = box.querySelector('#try-clean-rules'), open = !!(was && was.open);   // an edit keeps the rules open
    var cd = out.card || { columns: [] };
    box.innerHTML = summaryHtml(out) +
      (arr(out.notes).length ? '<ul class="tr-clean-notes">' + out.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>' : '') +
      '<details class="more cf-rules" id="try-clean-rules"' + (open ? ' open' : '') + '><summary>Cleaning rules: what each column should contain, and what counts as a duplicate</summary>' +
      generalHtml(cd) + '<ol class="cf-cols">' + arr(cd.columns).map(colHtml).join('') + '</ol>' + dupHtml(cd) +
      '<div class="tr-actions"><button type="button" class="btn btn-ghost" data-clean-act="again">Clean again with these rules</button></div>' +
      '<p class="note">Cleaning again runs the same cleaner here, on the run this page already holds; nothing is sent and no AI is asked. The report above keeps the engine\'s rules; later steps are marked out of date.</p></details>';
    box.hidden = false;
    status.textContent = said || ('Cleaned copy ready: ' + num((out.summary || {}).rows_out) + ' rows.');
  }

  // the visitor's rules as the card stands (the cleaner checks every one and says what it refused)
  function readRules(root) {
    var rules = [], allowed = {};
    Array.prototype.forEach.call(root.querySelectorAll('.cf-rule'), function (li) {
      var e = { name: li.getAttribute('data-rule') };
      Array.prototype.forEach.call(li.querySelectorAll('[data-k]'), function (n) {
        var k = n.getAttribute('data-k');
        if (k === 'on') e.on = !!n.checked; else e[k] = n.value === '' ? null : n.value;
      });
      rules.push(e);
    });
    Array.prototype.forEach.call(root.querySelectorAll('.cf-col[data-col] textarea[data-k="allowed"]'), function (n) {
      allowed[n.closest('.cf-col').getAttribute('data-col')] = String(n.value || '').split(/\r?\n/).map(function (x) { return x.trim(); }).filter(Boolean);
    });
    var on = root.querySelector('#cf-dup-on');
    var key = Array.prototype.map.call(root.querySelectorAll('.cf-keys input:checked'), function (n) { return n.getAttribute('data-key'); });
    return { rules: rules, allowed: allowed, duplicates: { on: on ? !!on.checked : true, key: key } };
  }

  function save(text, name, mime) {
    var url = URL.createObjectURL(new Blob([text], { type: mime + ';charset=utf-8' }));
    var a = document.createElement('a');
    a.href = url; a.download = name; a.rel = 'noopener';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function ask(root, rules, said) {
    var T = window.NLTry, status = root.querySelector('#try-clean-status');
    if (!T || !T.cleanFile) return;
    st.busy = true;
    if (status) status.textContent = rules ? 'Cleaning again with your rules…' : 'Making the cleaned copy…';
    T.cleanFile(rules, function (out) {
      st.busy = false;
      draw(root, out, out && out.ok && said ? said(out) : null);
      if (out && out.ok) emit(rules && out.edited ? { step: 2, status: 'done', edited: true, outOfDate: [3, 4, 5, 6], reason: 'cleaning rules edited' } : { step: 2, status: 'done' });
    });
  }

  K.mount = function (root, rep) {
    var card = root && root.querySelector('#try-clean');
    if (!card) return;
    st.out = null; st.rules = null; st.root = card; st.name = (rep && rep.input && rep.input.name) || 'file';
    card.addEventListener('click', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('[data-clean-dl], [data-clean-act]') : null;
      if (!t || st.busy) return;
      var dl = t.getAttribute('data-clean-dl');
      if (dl && st.out && st.out.ok) {
        if (dl === 'csv') save(st.out.csv, stem(st.name) + '-cleaned.csv', 'text/csv');
        else save(st.out.log, stem(st.name) + '-cleaning-log.md', 'text/markdown');
        return;
      }
      if (t.getAttribute('data-clean-act') === 'again') {
        st.rules = readRules(card);
        ask(card, st.rules, function (out) {
          var S = out.summary || {};
          return (out.edited ? 'Cleaned again with your rules: ' : 'Cleaned again (no rule changed): ') + num(S.rows_out) + ' rows, ' + num(S.flagged_rows) + ' flagged. No AI was asked.';
        });
      }
    });
    ask(card, null, null);
  };
  // the shared column card of step 1 changed a meaning: redraw this card's meanings (no re-clean needed)
  document.addEventListener('nl:columns', function () { if (st.root && st.out && st.out.ok && !st.busy) draw(st.root, st.out, null); });
})();
