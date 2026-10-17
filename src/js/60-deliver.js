/* Step 6 "Deliver": ONE menu of report types, one registry (plan/DASHBOARD-DESIGN.md "Batch 2 shared interface", built
   by Track E; CONTRACT §5.18), and step 1's side of it: "What do you need?" (the chosen type's upload checklist and its
   settings), the files of this run (several per run: the main file and its companions, rep.inputs) and "Load a recipe".

   window.NLDeliver
     register(option)   option: {id, title, blurb, order, needs: [{what, columns, optional, how}], roles: [...],
                        fits(rep) -> {ok, why}, settings: [{key, label, kind: 'text'|'number'|'choice'|'columns', default,
                        options: [{value, label}], help, min, max, step}], render(slot, rep, settings), loadRecipe(json)?}
                        A type that loads before this file pushes its option to window.NLDeliverQueue instead.
     list() / get(id) / chosen() ('' = an analysis) / choose(id) / settings(id) / setSettings(id, values)
     open(id)           step 6: draws that type's card (its settings form, then render(slot, rep, settings))
     refresh()          redraws step 1's checklist and files and step 6's menu (fits() of each); on 'nl:report', 'nl:inputs'
     inputs()           the current rep.inputs: [{id, name, primary, role, rows_kept, columns, months, scenario, pairs ...}]
     engine(kind, settings) -> Promise<out|null>   the worker's nl_browser.deliver_json(kind, settings) over every input
     loadRecipe(obj)    obj.kind picks the type; then its loadRecipe(obj), else setSettings(kind, obj.settings)
     offer(out, bytes, name, mime, label)   the page's one download helper (the PDF's and the Word write-up's)
   Events on document: 'nl:deliver-choice' {id}, 'nl:deliver-settings' {id, settings}; it listens to 'nl:report' and
   'nl:inputs' {inputs} (50-try.js fires that when a companion file has been read).
   Everything shown that came from a file (names, columns) is escaped. Nothing is sent anywhere. */
(function () {
  'use strict';
  var D = {}, opts = [], vals = {}, pick = '', openId = '', rep = null;
  window.NLDeliver = D;
  var ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return ENT[c]; }); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function $(id) { return document.getElementById(id); }
  function fire(name, detail) { try { document.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (e) { /* no CustomEvent */ } }
  function num(n) { return Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  function store(k, v) { try { if (v === null) window.sessionStorage.removeItem(k); else window.sessionStorage.setItem(k, v); } catch (e) { /* storage off */ } }
  function stored(k) { try { return window.sessionStorage.getItem(k); } catch (e) { return null; } }
  var ROLES = [['actuals', 'Actuals (dated figures)'], ['plan', 'Budget, plan or forecast'], ['actuals_vs_plan', 'Actuals and plan side by side'],
    ['kpi', 'KPI extract'], ['trial_balance', 'Trial balance'], ['deals', 'CRM deals export'], ['tasks', 'Tasks or requests'],
    ['list', 'A list (no dates)'], ['other', 'Something else']];
  function roleWord(r) { for (var i = 0; i < ROLES.length; i++) if (ROLES[i][0] === r) return ROLES[i][1]; return String(r || ''); }
  D.roles = function () { return ROLES.slice(); };
  D.roleWord = roleWord;

  /* ---- the registry ---- */
  D.register = function (o) {
    if (!o || typeof o.id !== 'string' || !/^[a-z][a-z0-9-]{1,40}$/.test(o.id) || typeof o.render !== 'function') return false;
    opts = opts.filter(function (x) { return x.id !== o.id; });
    opts.push(o);
    opts.sort(function (a, b) { return (+a.order || 99) - (+b.order || 99) || (a.id < b.id ? -1 : 1); });
    if (!vals[o.id]) vals[o.id] = {};
    if (stored('nl.deliver.choice') === o.id && !pick) pick = o.id;
    draw();
    return true;
  };
  D.list = function () { return opts.slice(); };
  D.get = function (id) { for (var i = 0; i < opts.length; i++) if (opts[i].id === id) return opts[i]; return null; };
  D.chosen = function () { return pick; };
  D.choose = function (id) {
    pick = D.get(id) ? id : '';
    store('nl.deliver.choice', pick || null);
    var sel = $('try-need-sel'); if (sel && sel.value !== pick) sel.value = pick;
    drawNeed();
    fire('nl:deliver-choice', { id: pick });
  };
  D.settings = function (id) {
    var o = D.get(id), out = {};
    if (!o) return out;
    arr(o.settings).forEach(function (s) { out[s.key] = s['default'] === undefined ? '' : s['default']; });
    var v = vals[id] || {};
    Object.keys(v).forEach(function (k) { out[k] = v[k]; });
    return out;
  };
  D.setSettings = function (id, values) {
    if (!D.get(id) || !values || typeof values !== 'object') return;
    vals[id] = vals[id] || {};
    Object.keys(values).forEach(function (k) { vals[id][k] = values[k]; });
    fire('nl:deliver-settings', { id: id, settings: D.settings(id) });
  };
  D.inputs = function () { return rep && Array.isArray(rep.inputs) ? rep.inputs : []; };
  D.report = function () { return rep; };
  D.engine = function (kind, settings) {
    return new Promise(function (resolve) {
      var T = window.NLTry;
      if (!T || typeof T.deliver !== 'function') return resolve(null);
      T.deliver(kind, settings || {}, function (out) { resolve(out || null); });
    });
  };
  D.offer = function (out, bytes, name, mime, label) {
    var T = window.NLTry;
    if (T && typeof T.offerFile === 'function') return T.offerFile(out, bytes, name, mime, label);
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([bytes], { type: mime })); a.download = name; a.textContent = 'Save the ' + label;
    out.hidden = false; out.textContent = ''; out.appendChild(a);
  };
  D.loadRecipe = function (obj) {
    if (!obj || typeof obj !== 'object') return { ok: false, why: 'it is not a recipe (a JSON object)' };
    var o = D.get(obj.kind);
    if (!o) return { ok: false, why: 'it is for a report type this page does not have (' + String(obj.kind || 'none').slice(0, 40) + ')' };
    var r = typeof o.loadRecipe === 'function' ? o.loadRecipe(obj) : null;
    if (!r) { D.setSettings(o.id, obj.settings || {}); r = { ok: true, why: '' }; }
    if (r.ok) D.choose(o.id);
    return r;
  };

  /* ---- a settings form (step 1 under the checklist, and the step 6 card) ---- */
  var uid = 0;
  function formHtml(o, scope) {
    var v = D.settings(o.id), s = arr(o.settings);
    if (!s.length) return '';
    return '<fieldset class="dl-set" data-set="' + esc(o.id) + '" data-scope="' + esc(scope) + '"><legend>Settings for the ' + esc(o.title.toLowerCase()) + '</legend>' + s.map(function (x) {
      var id = 'dl-f' + (++uid), cur = v[x.key], help = x.help ? '<small class="note" id="' + id + 'h">' + esc(x.help) + '</small>' : '';
      var desc = x.help ? ' aria-describedby="' + id + 'h"' : '', h = '<div class="dl-field" data-kind="' + esc(x.kind) + '"><label for="' + id + '">' + esc(x.label) + '</label>';
      if (x.kind === 'choice') {
        h += '<select id="' + id + '" data-key="' + esc(x.key) + '"' + desc + '>' + arr(x.options).map(function (c) {
          return '<option value="' + esc(c.value) + '"' + (String(c.value) === String(cur) ? ' selected' : '') + '>' + esc(c.label) + '</option>';
        }).join('') + '</select>';
      } else if (x.kind === 'number') {
        h += '<input id="' + id + '" type="number" inputmode="decimal" data-key="' + esc(x.key) + '" value="' + esc(cur) + '"' +
          (x.min !== undefined ? ' min="' + esc(x.min) + '"' : '') + (x.max !== undefined ? ' max="' + esc(x.max) + '"' : '') + (x.step !== undefined ? ' step="' + esc(x.step) + '"' : '') + desc + '>';
      } else if (x.kind === 'columns') {
        var have = {}, cols = [];
        D.inputs().forEach(function (f) { arr(f.columns).forEach(function (c) { if (!c.withheld && !have[c.name]) { have[c.name] = 1; cols.push(c.name); } }); });
        var on = Array.isArray(cur) ? cur : String(cur || '').split(',').map(function (t) { return t.trim(); }).filter(Boolean);
        h += cols.length ? '<span class="dl-cols" id="' + id + '" data-key="' + esc(x.key) + '" data-cols="1"' + desc + '>' + cols.map(function (c, i) {
          return '<label><input type="checkbox" value="' + esc(c) + '"' + (on.indexOf(c) >= 0 ? ' checked' : '') + '> ' + esc(c) + '</label>';
        }).join('') + '</span>' : '<input id="' + id + '" type="text" data-key="' + esc(x.key) + '" value="' + esc(on.join(', ')) + '" placeholder="column names, separated by commas"' + desc + '>';
      } else {
        h += '<input id="' + id + '" type="text" data-key="' + esc(x.key) + '" value="' + esc(cur) + '" maxlength="200"' + (x.placeholder ? ' placeholder="' + esc(x.placeholder) + '"' : '') + desc + '>';
      }
      return h + help + '</div>';
    }).join('') + '</fieldset>';
  }
  function bindForm(root, o, after) {
    Array.prototype.forEach.call(root.querySelectorAll('fieldset.dl-set[data-set="' + o.id + '"] [data-key]'), function (el) {
      var key = el.getAttribute('data-key');
      var read = function () {
        if (el.getAttribute('data-cols')) return Array.prototype.filter.call(el.querySelectorAll('input'), function (i) { return i.checked; }).map(function (i) { return i.value; });
        var x = arr(o.settings).filter(function (s) { return s.key === key; })[0] || {};
        if (x.kind === 'number') { var n = parseFloat(el.value); return isFinite(n) ? n : x['default']; }
        return el.value;
      };
      el.addEventListener('change', function () { var p = {}; p[key] = read(); D.setSettings(o.id, p); if (after) after(); });
    });
  }

  /* ---- step 1: "What do you need?", its checklist, the files of this run, a recipe ---- */
  function needRoot() {
    var r = $('try-need');
    if (r) return r;
    var start = $('try-start');
    if (!start || !start.parentNode) return null;
    r = document.createElement('div'); r.className = 'try-need'; r.id = 'try-need';
    start.parentNode.insertBefore(r, start.nextSibling);
    return r;
  }
  function drawNeed() {
    var root = needRoot();
    if (!root) return;
    if (!opts.length) { root.hidden = true; return; }
    root.hidden = false;
    var o = D.get(pick);
    var h = '<div class="try-need-row"><label for="try-need-sel"><strong>What do you need?</strong></label> <select id="try-need-sel">' +
      '<option value="">An analysis of my file (the report, the dashboard and a write-up)</option>' +
      opts.map(function (x) { return '<option value="' + esc(x.id) + '"' + (x.id === pick ? ' selected' : '') + '>' + esc(x.title) + '</option>'; }).join('') + '</select>' +
      ' <button type="button" class="btn btn-ghost btn-sm" id="try-recipe-load">Load a recipe</button><input type="file" id="try-recipe-file" class="sr" tabindex="-1" accept=".json,application/json" aria-label="Choose a recipe file (.json)">' +
      '</div><p class="note try-need-out" id="try-recipe-out" role="status" aria-live="polite"' + (D._recipeNote ? '' : ' hidden') + '>' + esc(D._recipeNote || '') + '</p>';
    if (o) {
      h += '<div class="try-need-card" id="try-need-card"><p class="try-need-blurb">' + esc(o.blurb || '') + '</p>' +
        '<p class="note"><strong>What to drop in</strong> (pick them together: hold Ctrl or Cmd to choose several files). A browser page cannot open a folder or connect to your finance system by itself, so export each file and pick them here; or paste the figures below.</p>' +
        '<ol class="try-need-list" id="try-need-list">' + arr(o.needs).map(function (n) {
          return '<li><strong>' + esc(n.what) + '</strong>' + (n.optional ? ' <span class="tag">optional</span>' : '') +
            (arr(n.columns).length ? '<br><span class="note">Columns: ' + arr(n.columns).map(function (c) { return '<code>' + esc(c) + '</code>'; }).join(', ') + '</span>' : '') +
            (n.how ? '<br><span class="note">' + esc(n.how) + '</span>' : '') + '</li>';
        }).join('') + '</ol>' + formHtml(o, 'need') + '</div>';
    }
    h += '<details class="try-paste" id="try-paste"><summary>Paste figures instead</summary><label for="try-paste-text" class="note">Paste a small table (copied from a spreadsheet, or CSV text with a header row). It is read in this browser as one more file, named “pasted-figures.csv”.</label>' +
      '<textarea id="try-paste-text" rows="5" spellcheck="false" placeholder="month,revenue,revenue_budget&#10;2026-08,120000,125000&#10;2026-09,118000,126000"></textarea>' +
      '<button type="button" class="btn btn-ghost btn-sm" id="try-paste-use">Use the pasted figures</button> <span class="note" id="try-paste-out" role="status"></span></details>' +
      '<div class="try-files" id="try-files" role="region" aria-label="The files in this run"' + (D.inputs().length || (window.NLTry && window.NLTry.pendingFiles && window.NLTry.pendingFiles().length) ? '' : ' hidden') + '></div>';
    root.innerHTML = h;
    var sel = $('try-need-sel');
    sel.addEventListener('change', function () { D.choose(sel.value); });
    var lb = $('try-recipe-load'), lf = $('try-recipe-file');
    lb.addEventListener('click', function () { lf.click(); });
    lf.addEventListener('change', function () {
      var f = lf.files && lf.files[0];
      lf.value = '';
      if (!f) return;
      f.text().then(function (t) {
        var obj = null, r;
        try { obj = JSON.parse(t); } catch (e) { obj = null; }
        r = obj ? D.loadRecipe(obj) : { ok: false, why: 'it is not JSON' };
        D._recipeNote = r.ok ? 'Recipe loaded from ' + f.name + ': its settings are set below. Pick this month\'s files; the files it read last time are listed in its checklist.' : 'That file could not be used as a recipe: ' + r.why + '.';
        drawNeed();
        if (r.ok && openId) D.open(openId);
      });
    });
    var pu = $('try-paste-use'), pt = $('try-paste-text'), po = $('try-paste-out');
    pu.addEventListener('click', function () {
      var txt = String(pt.value || '').replace(/\r\n?/g, '\n').trim();
      if (!txt || txt.indexOf('\n') < 0) { po.textContent = 'Paste a header row and at least one row of figures.'; return; }
      if (txt.indexOf(',') < 0 && txt.indexOf('\t') >= 0) txt = txt.split('\n').map(function (l) { return l.split('\t').map(function (c) { return /[",]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c; }).join(','); }).join('\n');
      var T = window.NLTry, bytes = new TextEncoder().encode(txt + '\n');
      if (!T || typeof T.addFiles !== 'function') { po.textContent = 'The page cannot read it here.'; return; }
      var r = T.addFiles([{ name: 'pasted-figures.csv', size: bytes.length, getBuffer: function () { return Promise.resolve(bytes.buffer.slice(0)); } }]);
      po.textContent = r === 'companion' ? 'Read as one more file of this run (below).' : r === 'main' ? 'Read as your file: the run has started.' : 'The engine is busy; try again when it has finished.';
    });
    if (o) bindForm(root, o, function () { if (openId === o.id) drawCard(); });
    drawFiles();
  }
  function drawFiles() {
    var box = $('try-files');
    if (!box) return;
    var T = window.NLTry, ins = D.inputs(), pend = T && T.pendingFiles ? T.pendingFiles() : [];
    if (!ins.length && !pend.length) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    var h = '<h4 class="try-files-h">The files in this run</h4><ul class="try-files-l">' + ins.map(function (f, i) {
      var id = 'try-role-' + esc(f.id), held = arr(f.withheld).length;
      return '<li class="try-file' + (f.ok ? '' : ' try-file-bad') + '" data-input="' + esc(f.id) + '"><span class="try-file-n"><b>' + esc(f.name) + '</b> ' +
        (f.primary ? '<span class="tag">main file: analysed in full</span>' : '<span class="tag">also read</span>') + '</span>' +
        (f.ok ? '<span class="note">' + num(f.rows_kept) + ' rows kept' + (f.rows_set_aside ? ', ' + num(f.rows_set_aside) + ' set aside' : '') +
          (f.months ? ', ' + esc(f.months.first) + ' to ' + esc(f.months.last) : '') +
          (held ? '; ' + held + (held === 1 ? ' column' : ' columns') + ' withheld as personal data' + (f.primary ? ' (your choice)' : ' (the engine\'s default for an extra file: run it as the main file to choose)') : '') + '</span>'
          : '<span class="note">Not read: ' + esc(f.error || 'the engine could not read it') + '</span>') +
        (f.ok ? '<label class="try-file-role" for="' + id + '">Read as <select id="' + id + '" data-input="' + esc(f.id) + '">' + ROLES.map(function (r) {
          return '<option value="' + r[0] + '"' + (r[0] === f.role ? ' selected' : '') + '>' + esc(r[1]) + '</option>'; }).join('') +
          (ROLES.some(function (r) { return r[0] === f.role; }) ? '' : '<option value="' + esc(f.role) + '" selected>' + esc(f.role) + '</option>') + '</select></label>' +
          '<span class="note try-file-why">' + (f.role_by === 'visitor' ? 'You chose it.' : f.role_by === 'recipe' ? 'As the recipe read it.' : 'The engine\'s guess: ' + esc(f.role_why) + '.') + '</span>' : '') +
        (!f.primary && f.ok && T && T.makeMain ? ' <button type="button" class="btn btn-ghost btn-sm" data-main="' + i + '">Analyse this one in full instead</button>' : '') + '</li>';
    }).join('') + pend.map(function (p) { return '<li class="try-file try-file-wait"><span class="try-file-n"><b>' + esc(p.name) + '</b> <span class="tag">waiting</span></span><span class="note">' + esc(p.note || 'read after the main file') + '</span></li>'; }).join('') + '</ul>' +
      (T && T.addFiles && ins.length ? '<button type="button" class="btn btn-ghost btn-sm" id="try-files-add">Add files to this run</button><input type="file" id="try-files-more" class="sr" tabindex="-1" multiple accept=".csv,.tsv,.txt,.xlsx,.xlsm" aria-label="Add files to this run">' : '');
    box.innerHTML = h;
    Array.prototype.forEach.call(box.querySelectorAll('select[data-input]'), function (s) {
      s.addEventListener('change', function () {
        var f = ins.filter(function (x) { return x.id === s.getAttribute('data-input'); })[0];
        if (f) { f.role = s.value; f.role_by = 'visitor'; f.role_why = 'you chose it'; }
        fire('nl:inputs', { inputs: ins, edited: true });
      });
    });
    Array.prototype.forEach.call(box.querySelectorAll('button[data-main]'), function (b) {
      b.addEventListener('click', function () { if (T && T.makeMain) T.makeMain(+b.getAttribute('data-main')); });
    });
    var add = $('try-files-add'), more = $('try-files-more');
    if (add && more) {
      add.addEventListener('click', function () { more.click(); });
      more.addEventListener('change', function () {
        var fs = Array.prototype.slice.call(more.files || []);
        more.value = '';
        if (fs.length) T.addFiles(fs.map(function (f) { return { name: f.name, size: f.size, getBuffer: function () { return f.arrayBuffer(); } }; }));
      });
    }
  }
  D.roleOf = function (inputs, want) { return arr(inputs).filter(function (f) { return f.ok && arr(want).indexOf(f.role) >= 0; }); };

  /* ---- step 6: the menu of report types, and the chosen type's card ---- */
  function menuRoot() {
    var slot = $('try-step-6-slot');
    if (!slot) return null;
    var m = $('try-types');
    if (!m) {
      m = document.createElement('div'); m.className = 'try-types'; m.id = 'try-types'; m.setAttribute('role', 'region'); m.setAttribute('aria-label', 'Report types');
      slot.appendChild(m);
    } else if (m.parentNode !== slot) slot.appendChild(m);
    return m;
  }
  function fitsOf(o) {
    var f = { ok: false, why: 'run a file first' };
    if (!rep || !rep.ok) return f;
    try { f = o.fits ? o.fits(rep) : { ok: true, why: '' }; } catch (e) { f = { ok: false, why: 'it could not check this run' }; }
    return f && typeof f === 'object' ? f : { ok: !!f, why: '' };
  }
  function drawMenu() {
    var m = menuRoot();
    if (!m) return;
    if (!opts.length || !rep || !rep.ok) { m.hidden = true; return; }
    m.hidden = false;
    m.innerHTML = '<h4 class="try-deliver-h">Report types</h4><p class="note">Each one is made in this browser from the rows the engine kept, from every file of this run. Pick one to see what it needs and to make its files.</p>' +
      '<ul class="try-types-l">' + opts.map(function (o) {
        var f = fitsOf(o);
        return '<li class="try-type' + (o.id === openId ? ' try-type-on' : '') + '"><button type="button" class="btn ' + (o.id === openId ? 'btn-primary' : 'btn-ghost') + ' btn-sm" data-type="' + esc(o.id) + '" aria-expanded="' + (o.id === openId) + '" aria-controls="try-type-card">' + esc(o.title) + '</button> ' +
          '<span class="try-type-fit ' + (f.ok ? 'fit-ok' : 'fit-no') + '">' + (f.ok ? 'ready' : 'needs: ' + esc(f.why || 'files of another kind')) + '</span><br><span class="note">' + esc(o.blurb || '') + '</span></li>';
      }).join('') + '</ul><div class="try-type-card" id="try-type-card"' + (openId ? '' : ' hidden') + '></div>';
    Array.prototype.forEach.call(m.querySelectorAll('button[data-type]'), function (b) {
      b.addEventListener('click', function () { var id = b.getAttribute('data-type'); if (openId === id) { openId = ''; drawMenu(); } else D.open(id); });
    });
    if (openId) drawCard();
  }
  function drawCard() {
    var card = $('try-type-card'), o = D.get(openId);
    if (!card || !o) return;
    card.hidden = false;
    var f = fitsOf(o);
    card.innerHTML = '<h4>' + esc(o.title) + '</h4>' + (f.ok ? '' : '<p class="note try-type-needs" role="status">This run does not have what it needs yet: ' + esc(f.why || 'other files') + '. Step 1 lists what to drop in.</p>') +
      formHtml(o, 'card') + '<div class="try-type-slot" id="try-type-slot"></div>';
    bindForm(card, o, function () { drawCard(); });
    try { o.render($('try-type-slot'), rep, D.settings(o.id)); } catch (e) { $('try-type-slot').innerHTML = '<p class="note">This report type could not be drawn (' + esc(e && e.message || e) + ').</p>'; }
  }
  D.open = function (id) {
    if (!D.get(id)) return;
    openId = id;
    drawMenu();
    if (window.NLSteps && typeof window.NLSteps.go === 'function') window.NLSteps.go(6);
  };
  D.refresh = function () { draw(); };
  function draw() { drawNeed(); drawMenu(); }

  function boot() {
    var q = window.NLDeliverQueue;
    if (Array.isArray(q)) q.splice(0).forEach(function (o) { D.register(o); });
    window.NLDeliverQueue = { push: function (o) { D.register(o); return 0; } };
    draw();
    document.addEventListener('nl:report', function (e) { rep = e.detail && e.detail.report; if (openId && !D.get(openId)) openId = ''; if (!openId && pick && rep && rep.ok) openId = pick; draw(); });
    document.addEventListener('nl:inputs', function () { draw(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
