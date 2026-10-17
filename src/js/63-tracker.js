/* "Build a tracker" (Track G, plan/DASHBOARD-DESIGN.md; engine/nl_tracker.py, CONTRACT-v2 §5.20): one option of step 6's
   report menu (window.NLDeliver, src/js/60-deliver.js, Track E). It turns the files run in this tab into an Excel tracker:
   a Data sheet (an Excel table: consistent typed columns, status and category dropdowns from Lists, formula columns that
   fill every new row, each row's source file and line), Summary (COUNTIFS / SUMIFS answering the visitor's questions),
   Lists, Instructions, Changes, Merged duplicates, Unplaced and Flags.

   ONE cleaner: the rows are the "Clean my file" view's rows (the engine's cleaner, its edits on that card included), read
   in the worker (window.NLTry.tracker -> engine/worker.js "tracker" -> nl_browser.tracker_json -> nl_tracker.build).
   ONE .xlsx writer: window.NLX.workbook (src/js/56-xlsx.js) writes the workbook spec the engine returns. Nothing is
   sent anywhere; nothing is invented or filled in.

   Several files: every file run in this tab is held by the worker (by its SHA-256); the tracker combines the ones ticked
   here, in run order (the first file's copy is kept when a row is in two files). With Track E's intake the run's own
   files (rep.inputs[], the main file and its companions) are ticked by default. Without the registry on the page (this
   file loads after 60-deliver.js; a page without it gets the minimal stand-in below, guarded by `if (!window.NLDeliver)`)
   the option draws itself in step 6.

   The tracker demo (step 1, "Tracker demo (synthetic task lists)", tools/make_sample_tracker.py): both lists in one run
   (the first analysed, the second read as a companion), "Build a tracker" chosen; the card waits for the companion and
   builds from both. Its settings are the registry's form (no ownForm). Everything shown from a file is escaped. */
(function () {
  'use strict';
  var ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return ENT[c]; }); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function num(n) { return Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  function plural(n, one, many) { return num(n) + ' ' + (Number(n) === 1 ? one : (many || one + 's')); }
  function stem(name) { return String(name || 'file').replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'file'; }
  function fire(name, detail) { try { document.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (e) { /* no CustomEvent */ } }

  /* ---- a minimal stand-in for Track E's registry (src/js/60-deliver.js), only when that file is not on the page ---- */
  if (!window.NLDeliver) {
    (function () {
      var D = { __standin: true }, opts = [], vals = {}, openId = '', rep = null;
      window.NLDeliver = D;
      D.register = function (o) { if (!o || !o.id || typeof o.render !== 'function') return false; opts = opts.filter(function (x) { return x.id !== o.id; }); opts.push(o); vals[o.id] = vals[o.id] || {}; draw(); return true; };
      D.list = function () { return opts.slice(); };
      D.get = function (id) { return opts.filter(function (o) { return o.id === id; })[0] || null; };
      D.chosen = function () { return openId; };
      D.choose = function (id) { if (D.get(id)) openId = id; fire('nl:deliver-choice', { id: openId }); };
      D.settings = function (id) {
        var o = D.get(id), out = {};
        if (!o) return out;
        arr(o.settings).forEach(function (s) { out[s.key] = s['default'] === undefined ? '' : s['default']; });
        Object.keys(vals[id] || {}).forEach(function (k) { out[k] = vals[id][k]; });
        return out;
      };
      D.setSettings = function (id, v) { if (!D.get(id) || !v) return; Object.keys(v).forEach(function (k) { vals[id][k] = v[k]; }); };
      D.inputs = function () { return rep && Array.isArray(rep.inputs) ? rep.inputs : []; };
      D.report = function () { return rep; };
      D.engine = function () { return Promise.resolve(null); };
      D.offer = function (out, bytes, name, mime, label) {
        var T = window.NLTry;
        if (T && T.offerFile) return T.offerFile(out, bytes, name, mime, label);
        return null;
      };
      D.open = function (id) { if (D.get(id)) { openId = id; draw(); } };
      D.refresh = function () { draw(); };
      function draw() {
        var slot = document.getElementById('try-step-6-slot');
        if (!slot || !rep || !rep.ok || !opts.length) return;
        var m = document.getElementById('try-types');
        if (!m) { m = document.createElement('div'); m.className = 'try-types'; m.id = 'try-types'; m.setAttribute('role', 'region'); m.setAttribute('aria-label', 'Report types'); slot.appendChild(m); }
        var o = D.get(openId);              // a type's card opens only when the visitor (or the demo) chooses it
        m.innerHTML = '<h4 class="try-deliver-h">Report types</h4><ul class="try-types-l">' + opts.map(function (x) {
          return '<li class="try-type"><button type="button" class="btn ' + (x.id === openId ? 'btn-primary' : 'btn-ghost') + ' btn-sm" data-type="' + esc(x.id) + '" aria-expanded="' + (x.id === openId) + '">' + esc(x.title) + '</button><br><span class="note">' + esc(x.blurb || '') + '</span></li>';
        }).join('') + '</ul>' + (o ? '<div class="try-type-card" id="try-type-card"><h4>' + esc(o.title) + '</h4>' + form(o) + '<div class="try-type-slot" id="try-type-slot"></div></div>' : '');
        Array.prototype.forEach.call(m.querySelectorAll('button[data-type]'), function (b) { b.addEventListener('click', function () { D.open(b.getAttribute('data-type')); }); });
        if (!o) return;
        Array.prototype.forEach.call(m.querySelectorAll('[data-key]'), function (el) {
          el.addEventListener('change', function () { var p = {}; p[el.getAttribute('data-key')] = el.value; D.setSettings(o.id, p); o.render(document.getElementById('try-type-slot'), rep, D.settings(o.id)); });
        });
        o.render(document.getElementById('try-type-slot'), rep, D.settings(o.id));
      }
      var uid = 0;
      function form(o) {
        var v = D.settings(o.id);
        return '<fieldset class="dl-set" data-set="' + esc(o.id) + '"><legend>Settings</legend>' + arr(o.settings).map(function (x) {
          var id = 'trk-f' + (++uid), h = '<div class="dl-field"><label for="' + id + '">' + esc(x.label) + '</label>';
          if (x.kind === 'choice') h += '<select id="' + id + '" data-key="' + esc(x.key) + '">' + arr(x.options).map(function (c) { return '<option value="' + esc(c.value) + '"' + (String(c.value) === String(v[x.key]) ? ' selected' : '') + '>' + esc(c.label) + '</option>'; }).join('') + '</select>';
          else h += '<input id="' + id + '" type="text" maxlength="400" data-key="' + esc(x.key) + '" value="' + esc(v[x.key]) + '"' + (x.placeholder ? ' placeholder="' + esc(x.placeholder) + '"' : '') + '>';
          return h + (x.help ? '<small class="note">' + esc(x.help) + '</small>' : '') + '</div>';
        }).join('') + '</fieldset>';
      }
      document.addEventListener('nl:report', function (e) { rep = e.detail && e.detail.report; draw(); });
    })();
  }

  /* ---- the option ---- */
  var KINDS = [['', 'Suggested from your data'], ['tasks', 'Tasks'], ['requests', 'Requests'], ['inventory', 'Inventory'], ['shifts', 'Shifts'],
    ['vendors', 'Vendors (bills and spend)'], ['other', 'Something else']];
  var TR = { ticked: {}, last: null, busy: false, seq: 0 };

  function shas(rep) {
    // the files to combine, in order: the run's own (rep.inputs: main first), the files the visitor added in this tab,
    // the file on the page; a box the visitor unticked stays off
    var out = [], cur = rep && rep.input && rep.input.sha256;
    arr(rep && rep.inputs).forEach(function (f) { if (f && f.ok !== false && f.sha256 && out.indexOf(f.sha256) < 0) out.push(f.sha256); });
    if (cur && out.indexOf(cur) < 0) out.push(cur);
    Object.keys(TR.ticked).forEach(function (s) { if (TR.ticked[s] && out.indexOf(s) < 0) out.push(s); });
    return out.filter(function (s) { return TR.ticked[s] !== false; });
  }
  function cleaningEdits() { var C = window.NLClean; try { return C && typeof C.rules === 'function' ? C.rules() : null; } catch (e) { return null; } }

  function summaryHtml(out, settings) {
    var S = out.summary || {}, sug = out.suggest || {};
    var kindLine = '<p class="trk-kind"><strong>What it tracks:</strong> ' + esc(out.kind_label || out.kind) +
      (settings && settings.kind ? ' (your choice' + (sug.kind && sug.kind !== out.kind ? '; the data looked like ' + esc(sug.kind) : '') + ')' : ' (suggested: ' + esc(sug.why || 'from your columns') + ')') + '</p>';
    var files = arr(S.files).map(function (f) {
      return '<li><b>' + esc(f.name) + '</b>: ' + plural(f.rows_in, 'row') + ' in the file; ' + num(f.placed) + ' placed' +
        (f.merged_within ? ', ' + num(f.merged_within) + ' merged (same file)' : '') + (f.merged_across ? ', ' + num(f.merged_across) + ' merged into another file\'s copy' : '') +
        (f.unplaced ? ', ' + num(f.unplaced) + ' not a record (Unplaced)' : '') + (f.withheld_left_out ? '; ' + plural(f.withheld_left_out, 'withheld column') + ' left out' : '') + '</li>';
    }).join('');
    var kpis = '<ul class="tr-clean-kpis trk-kpis">' +
      '<li><span class="k">Rows in the tracker</span><b>' + esc(num(S.rows)) + '</b></li>' +
      '<li><span class="k">Duplicates merged</span><b>' + esc(num((S.merged_within || 0) + (S.merged_across || 0))) + '</b></li>' +
      '<li><span class="k">Cells changed (listed)</span><b>' + esc(num(S.changes)) + '</b></li>' +
      '<li><span class="k">Rows flagged</span><b>' + esc(num(S.flagged_rows)) + '</b></li>' +
      '<li><span class="k">Rows not placed</span><b>' + esc(num(S.unplaced)) + '</b></li></ul>';
    var map = '<details class="more trk-map"><summary>Columns: ' + esc(arr(S.columns).length) + ' (your columns under the tracker\'s names; none dropped)</summary><table class="trk-cols"><thead><tr><th scope="col">Tracker column</th><th scope="col">From your files</th></tr></thead><tbody>' +
      arr(out.mapping).map(function (m) {
        return '<tr><td>' + esc(m.column) + '</td><td>' + (arr(m.sources).length ? arr(m.sources).map(function (s) { return esc(s.header) + ' <span class="note">(' + esc(s.file) + ')</span>'; }).join('; ') : '<span class="note">none of your files has it: added empty</span>') + '</td></tr>';
      }).join('') + (arr(S.formula_columns).length ? '<tr><td colspan="2" class="note">Formula columns: ' + esc(S.formula_columns.join(', ')) + ' (they fill every row you add)</td></tr>' : '') + '</tbody></table></details>';
    var qs = arr(out.questions).length ? '<div class="trk-qs"><p><strong>Your questions, as the Summary sheet answers them</strong></p><ul>' + arr(out.questions).map(function (q) {
      return '<li><span class="trk-q">' + esc(q.question) + '</span> <span class="note">' + esc(q.cell ? 'a formula on Summary (' + q.cell + '): ' + q.how : (q.text || '')) + '</span></li>';
    }).join('') + '</ul></div>' : '';
    return kindLine + '<ul class="trk-files">' + files + '</ul>' + kpis + qs + map;
  }

  function filesHtml(out, use) {
    var av = arr(out && out.available);
    if (av.length < 2) return '';
    return '<fieldset class="trk-pick"><legend>Files to combine (each one you ran in this tab)</legend>' + av.map(function (f, i) {
      return '<label class="trk-pick-f"><input type="checkbox" data-sha="' + esc(f.sha256) + '"' + (use.indexOf(f.sha256) >= 0 ? ' checked' : '') + '> ' + esc(f.name) + ' <span class="note">(' + plural(f.rows, 'row') + ')</span></label>';
    }).join('') + '<p class="note">Rows that are the same record in two files are merged into the first file\'s copy, and the Merged duplicates sheet lists each one.</p></fieldset>';
  }

  function build(slot, rep, settings) {
    var T = window.NLTry, st = slot.querySelector('.trk-status');
    if (!T || typeof T.tracker !== 'function') { st.textContent = 'The engine is not on this page.'; return; }
    // the run's other files are still being read (Track E's intake): the registry draws this card again on 'nl:inputs'
    var pend = typeof T.pendingFiles === 'function' ? arr(T.pendingFiles()) : [];
    if (pend.length) { st.textContent = 'Reading ' + plural(pend.length, 'more file') + ' of this run first…'; return; }
    var use = shas(rep), ask = { inputs: use, kind: settings.kind || '', questions: settings.questions || '', dup_key: settings.dup_key || 'exact', rules: cleaningEdits() };
    var key = JSON.stringify([rep && rep.input && rep.input.sha256, rep && rep.inputs ? rep.inputs.length : 0, ask]);
    // the same files and settings as the tracker on hand (a redraw, or a field left without a change): drawn from it at
    // once, the engine not asked again (and a click on its download button is not lost to a redraw)
    if (key === TR.key && TR.last) { show(slot, rep, settings, TR.last, use); return; }
    var my = ++TR.seq;
    TR.busy = true;
    st.textContent = 'Building the tracker from ' + plural(use.length, 'file') + '…';
    T.tracker(ask, function (out) {
      if (my !== TR.seq) return;
      TR.busy = false;
      TR.key = out && out.ok ? key : '';
      show(slot, rep, settings, out, use);
    });
  }
  function show(slot, rep, settings, out, use) {
    var box = slot.querySelector('.trk-out'), st = slot.querySelector('.trk-status');
    if (!box || !st) return;
    if (!out || !out.ok) {
      TR.last = out;
      st.textContent = (out && out.error) || 'The run is no longer loaded in this page; run the file again to build a tracker.';
      box.innerHTML = filesHtml(out, use);
      bindPick(slot, rep, settings);
      return;
    }
    TR.last = out;
    st.textContent = 'Tracker ready: ' + plural(out.summary.rows, 'row') + ' from ' + plural(arr(out.summary.files).length, 'file') + '. Nothing was invented or filled in.';
    box.innerHTML = filesHtml(out, use) + summaryHtml(out, settings) +
      '<div class="tr-actions"><button type="button" class="btn btn-primary" data-trk="xlsx">Download the tracker (Excel)</button><span class="try-share-out trk-dl-out" hidden></span></div>' +
      '<p class="note">Made in this browser by the page\'s Excel writer; nothing is sent. Sheets: Data (an Excel table with dropdowns and formula columns), Summary (formulas, not pasted numbers), Lists, Instructions, Changes, Merged duplicates, Unplaced and Flags.</p>';
    bindPick(slot, rep, settings);
    fire('nl:tracker', { out: out });
  }
  function bindPick(slot, rep, settings) {
    Array.prototype.forEach.call(slot.querySelectorAll('.trk-pick input[data-sha]'), function (c) {
      c.addEventListener('change', function () { TR.ticked[c.getAttribute('data-sha')] = !!c.checked; TR.key = ''; build(slot, rep, settings); });
    });
  }
  function download(slot) {
    var out = TR.last, X = window.NLX, o = slot.querySelector('.trk-dl-out');
    if (!out || !out.ok || !out.workbook || !X || !o) return;
    var bytes;
    try { bytes = X.workbook(out.workbook); } catch (e) { o.hidden = false; o.textContent = 'The workbook could not be written in this browser (' + String(e && e.message || e) + ').'; return; }
    var first = arr(out.summary && out.summary.files)[0], name = 'tracker-' + (out.kind || 'rows') + '-' + stem(first ? first.name : 'file') + '.xlsx';
    window.NLDeliver.offer(o, bytes, name, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'Excel tracker');
    if (window.NLSteps) window.NLSteps.set(6, 'done');
  }

  var option = {
    id: 'tracker',
    title: 'Build a tracker (Excel)',
    blurb: 'Turn the lists you track things in (tasks, requests, stock, shifts, bills) into one clean Excel tracker: dropdowns, formula columns, a summary that answers your questions, and every change, merge and flag listed.',
    order: 30,
    needs: [{ what: 'The files you track things in today (spreadsheets, lists, exports): one or several', optional: false,
      columns: ['an ID or a name', 'a status', 'a category or type', 'dates opened, due and closed (tasks, requests)', 'or qty and unit cost (stock), start and end (shifts), vendor and amount (bills)'],
      how: 'Save each sheet as CSV (File > Save as > CSV) or drop the .xlsx. Several files are combined by matching columns; each row keeps its source file and line.' }],
    roles: ['tasks', 'list', 'other'],
    system: 'spreadsheet or tracking tool',
    howTitle: 'How to export the lists you track things in',
    howSteps: [['Excel', 'File > Save As > CSV UTF-8 for each sheet (or drop the .xlsx: its first sheet is read).'],
      ['Google Sheets', 'File > Download > Comma-separated values (.csv), once per tab.'],
      ['Trello, Asana, Jira, Monday', 'Export the board or project as CSV from its menu (Board > Print and export, Export, Export issues > CSV).'],
      ['Several files', 'Pick them together in step 1 (hold Ctrl or Cmd): the first is analysed in full, the others are read as more files of the same run, and the tracker combines them.']],
    fits: function (rep) {
      if (!rep || !rep.ok) return { ok: false, why: 'run a file first' };
      return { ok: true, why: plural(rep.input && rep.input.rows, 'row') + ' to load' };
    },
    settings: [
      { key: 'kind', label: 'What are you tracking?', kind: 'choice', 'default': '', options: KINDS.map(function (k) { return { value: k[0], label: k[1] }; }),
        help: 'Suggested from your columns (and the AI plan\'s reading of them when the AI ran); choose another to change the tracker\'s columns.' },
      { key: 'questions', label: 'What must the tracker answer?', kind: 'text', 'default': '', placeholder: 'e.g. How many are overdue? Which open tasks are oldest? Who has the most?',
        help: 'One or more questions, separated by ? or ;. The Summary sheet answers each with a formula, or says what it would need.' },
      { key: 'dup_key', label: 'Two files hold the same record when', kind: 'choice', 'default': 'exact',
        options: [{ value: 'exact', label: 'they match in every column both files have (the cleaner\'s rule)' }, { value: 'id', label: 'they have the same ID (the first copy is kept; what differed is listed)' }] }
    ],
    render: function (slot, rep, settings) {
      if (!slot) return;
      settings = settings || {};
      slot.innerHTML = '<div class="trk" id="try-tracker" data-step="6"><p class="note">The tracker\'s rows are the cleaned rows of step 2 (the same cleaner, with any rule you edited there). A value the cleaner could not read stays as written and is flagged; a blank stays blank and is flagged; nothing is invented.</p>' +
        '<p class="tr-clean-status trk-status" role="status" aria-live="polite"></p><div class="trk-out"></div></div>';
      var root = slot.querySelector('.trk');
      root.addEventListener('click', function (e) {
        var t = e.target && e.target.closest ? e.target.closest('[data-trk]') : null;
        if (!t) return;
        var what = t.getAttribute('data-trk');
        if (what === 'xlsx') download(slot);
      });
      if (!rep || !rep.ok) { slot.querySelector('.trk-status').textContent = 'Run a file first (step 1): the tracker is built only from your data.'; return; }
      build(slot, rep, settings);
    }
  };

  var D0 = window.NLDeliver;
  if (D0 && typeof D0.register === 'function') D0.register(option);
  else (window.NLDeliverQueue = window.NLDeliverQueue || []).push(option);
  window.NLTracker = { option: option, state: function () { return { last: TR.last, key: TR.key || '' }; } };
})();
