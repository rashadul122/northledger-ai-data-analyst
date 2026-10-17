/* The Try section's step rail (plan/DASHBOARD-DESIGN.md "The workflow order"): 1 Bring your data, 2 Clean, 3 Analyse,
   4 Ask, 5 Dashboard, 6 Deliver, over ONE run (one upload, one consent, one AI plan). The page's existing parts sit in
   order under the rail (src/sections/05b-try.html wraps them in #try-step-1 ... #try-step-6; nothing is duplicated:
   a step whose existing part lives inside the report links to it). Each step has a slot for new UI.

   Hooks for the other tracks (window.NLSteps; documented in plan/DASHBOARD-DESIGN.md):
     #try-step-N            the step's section (N = 1..6); #try-step-N-slot: a container for that step's own UI
     NLSteps.set(n, status, note)   status: 'waiting' | 'ready' | 'done' | 'attention' | 'stale'
     NLSteps.stale(from, why)       marks steps from..6 out of date and shows ONE "Update" button on the rail
     NLSteps.addDecision(key, val)  a choice the next Update sends to the engine (e.g. Track C's cleaning rules)
     NLSteps.update()               re-runs the engine locally with the last run's choices (no new AI call), then
                                    clears "out of date"; the dashboard keeps its filters and edits, and pinned charts
     NLSteps.go(n)                  scrolls to a step (and opens the Dashboard tab for step 5)
     document event 'nl:step-status'  detail {step, status, note, steps}: fired on every change
     document event 'nl:report'       detail {report}: a report is shown (after every run and update)
     a step's own event 'nl:step-status' with {step, edited: true, outOfDate: [n, ...]}: those steps go out of date
     data-step-home="N" on an element the report renders: moved into #try-step-N-slot once the report is shown (bind
                                    its listeners on the element itself, not on #try-report)
   The phone layout shows "Step n of 6" with the step's status and a list to jump to any step. */
(function () {
  'use strict';
  var NAMES = ['Bring your data', 'Clean', 'Analyse', 'Ask', 'Dashboard', 'Deliver'];
  var WORDS = { waiting: 'waiting', ready: 'ready', done: 'done', attention: 'needs attention', stale: 'out of date' };
  var st = NAMES.map(function () { return { status: 'waiting', note: '' }; });
  st[0].status = 'ready';
  var cur = 1, extra = {}, staleWhy = '';
  var NLSteps = { names: NAMES.slice() };
  window.NLSteps = NLSteps;
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fire(name, detail) { try { document.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (e) { /* no CustomEvent: no event */ } }

  function draw() {
    var rail = $('try-rail');
    if (!rail) return;
    var anyStale = st.some(function (s) { return s.status === 'stale'; });
    var h = '<ol class="try-rail-list">' + NAMES.map(function (n, i) {
      var s = st[i], on = i + 1 === cur;
      return '<li class="rs-' + s.status + (on ? ' rs-on' : '') + '"><a href="#try-step-' + (i + 1) + '" data-step="' + (i + 1) + '"' + (on ? ' aria-current="step"' : '') + '>' +
        '<span class="rs-n" aria-hidden="true">' + (s.status === 'done' ? '✓' : i + 1) + '</span><span class="rs-t">' + esc(n) + '</span>' +
        '<span class="rs-s">' + esc(WORDS[s.status]) + '</span><span class="sr">, step ' + (i + 1) + ' of 6</span></a></li>';
    }).join('') + '</ol>';
    var c = st[cur - 1];
    h += '<div class="try-rail-now"><p><strong>Step ' + cur + ' of 6:</strong> ' + esc(NAMES[cur - 1]) + ' <span class="rs-s">(' + esc(WORDS[c.status]) + ')</span></p>' +
      '<label class="try-rail-jump">Go to <select data-rail="jump">' + NAMES.map(function (n, i) { return '<option value="' + (i + 1) + '"' + (i + 1 === cur ? ' selected' : '') + '>' + (i + 1) + '. ' + esc(n) + ' (' + esc(WORDS[st[i].status]) + ')</option>'; }).join('') + '</select></label></div>';
    if (anyStale) h += '<p class="try-rail-stale" role="status"><span>' + esc(staleWhy || 'Something you changed is not in the steps after it yet.') + '</span> <button type="button" class="btn btn-primary btn-sm" data-rail="update">Update</button></p>';
    rail.innerHTML = h;
    for (var i = 1; i <= 6; i++) {
      var sec = $('try-step-' + i), badge = sec && sec.querySelector('.try-step-st');
      if (sec) sec.setAttribute('data-status', st[i - 1].status);
      if (badge) badge.textContent = WORDS[st[i - 1].status] + (st[i - 1].note ? ': ' + st[i - 1].note : '');
    }
  }
  NLSteps.status = function () { return st.map(function (s, i) { return { step: i + 1, status: s.status, note: s.note }; }); };
  NLSteps.set = function (n, status, note) {
    if (n < 1 || n > 6 || !WORDS[status]) return;
    st[n - 1] = { status: status, note: note || '' };
    draw();
    fire('nl:step-status', { step: n, status: status, note: note || '', steps: NLSteps.status() });
  };
  NLSteps.stale = function (from, why) {
    staleWhy = why || '';
    for (var i = Math.max(1, from); i <= 6; i++) if (st[i - 1].status !== 'waiting') st[i - 1] = { status: 'stale', note: why || '' };
    draw();
    fire('nl:step-status', { step: from, status: 'stale', note: why || '', steps: NLSteps.status() });
  };
  NLSteps.addDecision = function (k, v) { if (typeof k === 'string' && /^__[a-z_]+__$/.test(k)) extra[k] = v; };
  NLSteps.decisions = function () { return extra; };
  NLSteps.update = function () {
    if (!window.NLTry || !window.NLTry.rerun) return false;
    var d = {}; Object.keys(extra).forEach(function (k) { d[k] = extra[k]; });
    staleWhy = '';
    return window.NLTry.rerun(d);
  };
  NLSteps.go = function (n) {
    n = Math.max(1, Math.min(6, +n || 1));
    cur = n; draw();
    if (n === 5) { var t = $('nl2-tab-d'); if (t) t.click(); }
    var sec = $('try-step-' + n);
    if (sec) { sec.setAttribute('tabindex', '-1'); try { sec.scrollIntoView({ block: 'start' }); } catch (e) { sec.scrollIntoView(); } sec.focus({ preventScroll: true }); }
  };
  // a report is shown: every step's part exists now
  NLSteps.report = function (rep) {
    for (var i = 2; i <= 6; i++) { var s = $('try-step-' + i); if (s) s.hidden = false; }
    var ok = rep && rep.ok, c = rep && rep.cleaning, dash = rep && rep.dashboard;
    st[0] = { status: ok ? 'done' : 'attention', note: '' };
    st[1] = { status: ok ? 'done' : 'attention', note: c ? c.rows_clean + ' rows kept, ' + c.rows_quarantined + ' set aside' : '' };
    st[2] = { status: ok ? 'done' : 'attention', note: '' };
    st[3] = { status: ok ? 'ready' : 'waiting', note: '' };
    st[4] = { status: dash && dash.ok ? 'done' : 'attention', note: dash && dash.ok ? (dash.chosen_by === 'ai' ? 'designed by the AI' : dash.chosen_by === 'recorded' ? 'a recorded AI design' : 'designed by the engine') : (dash && dash.why) || '' };
    st[5] = { status: ok ? 'ready' : 'waiting', note: '' };
    extra = {};
    draw();
    fire('nl:step-status', { step: 0, status: 'report', note: '', steps: NLSteps.status() });
    fire('nl:report', { report: rep });
  };
  NLSteps.place = function () {
    Array.prototype.forEach.call(document.querySelectorAll('#try-report [data-step-home]'), function (el) {
      var slot = $('try-step-' + el.getAttribute('data-step-home') + '-slot');
      if (slot && !slot.contains(el)) slot.appendChild(el);
    });
  };
  NLSteps.reset = function () {
    st = NAMES.map(function () { return { status: 'waiting', note: '' }; });
    st[0] = { status: 'ready', note: '' }; cur = 1; extra = {}; staleWhy = '';
    draw();
  };
  NLSteps.running = function () { st[0] = { status: 'ready', note: 'the engine is working' }; draw(); };

  function boot() {
    var rail = $('try-rail');
    if (!rail) return;
    draw();
    rail.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[data-step]'), b = e.target.closest && e.target.closest('button[data-rail="update"]');
      if (a) { e.preventDefault(); NLSteps.go(a.getAttribute('data-step')); }
      else if (b) NLSteps.update();
    });
    document.addEventListener('click', function (e) { var o = e.target.closest && e.target.closest('[data-rail-open]'); if (o) NLSteps.go(o.getAttribute('data-rail-open')); });
    rail.addEventListener('change', function (e) { if (e.target.getAttribute('data-rail') === 'jump') NLSteps.go(e.target.value); });
    // the step in view is the current one
    if (window.IntersectionObserver) {
      var io = new IntersectionObserver(function (es) {
        es.forEach(function (en) {
          if (!en.isIntersecting) return;
          var n = +en.target.getAttribute('data-step');
          if (n && n !== cur) { cur = n; draw(); }
        });
      }, { rootMargin: '-35% 0px -55% 0px' });
      for (var i = 1; i <= 6; i++) { var s = $('try-step-' + i); if (s) io.observe(s); }
    }
    // a step that edited something says so (Track C, step 2: {step: 2, edited: true, outOfDate: [3, 4, 5, 6]}): the steps
    // after it are marked out of date, with the one Update
    document.addEventListener('nl:step-status', function (e) {
      var d = e.detail || {};
      if (!d.edited || !Array.isArray(d.outOfDate) || !d.outOfDate.length) return;
      var from = Math.min.apply(null, d.outOfDate.filter(function (n) { return n >= 1 && n <= 6; }));
      if (isFinite(from)) NLSteps.stale(from, d.note || ('You changed step ' + d.step + ': Update runs the later steps again here, with no AI call.'));
    });
    // a part rendered inside the report that belongs to another step (data-step-home="N") is moved into that step's slot
    document.addEventListener('nl:report', function () { NLSteps.place(); });
    document.addEventListener('nl:view', function (e) { if (e.detail && e.detail.view === 'dashboard' && cur !== 5) { cur = 5; draw(); } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
