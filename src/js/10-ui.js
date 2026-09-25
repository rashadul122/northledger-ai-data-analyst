/* Page chrome: menu, theme, tour doors, report tabs, drawer, sortable tables, file explorer,
   offer chooser, copy button and the run replay. */
(function () {
  'use strict';
  var U = window.NLU, NL = window.NL || {};

  U.onReady(function () {
    /* ---- phone menu ---- */
    var mb = U.$('.menu-btn'), links = U.$('#nav-links');
    if (mb && links) {
      mb.addEventListener('click', function () {
        var open = mb.getAttribute('aria-expanded') !== 'true';
        mb.setAttribute('aria-expanded', open ? 'true' : 'false');
        links.classList.toggle('open', open);
      });
      var closeMenu = function () { mb.setAttribute('aria-expanded', 'false'); links.classList.remove('open'); };
      links.addEventListener('click', function (e) {
        if (e.target.closest('a')) closeMenu();
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && links.classList.contains('open')) { closeMenu(); mb.focus(); e.preventDefault(); }
      });
      // a tap anywhere else, or scrolling the page, puts the menu away
      document.addEventListener('click', function (e) {
        if (links.classList.contains('open') && !links.contains(e.target) && !mb.contains(e.target)) closeMenu();
      });
      window.addEventListener('scroll', function () { if (links.classList.contains('open')) closeMenu(); }, { passive: true });
    }

    /* ---- theme (remembered per viewer; the page works without storage) ---- */
    var tb = U.$('#theme-btn');
    if (tb) tb.addEventListener('click', function () {
      var root = document.documentElement;
      var cur = root.getAttribute('data-theme') ||
        (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      var next = cur === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      U.store.set('nl-theme', next);
      tb.setAttribute('aria-label', 'Switch to the ' + (next === 'dark' ? 'light' : 'dark') + ' theme');
    });

    /* ---- two doors: a short tour, remembered per viewer ---- */
    var TOURS = {
      owner: [['#report', 'The report'], ['#services', 'Offers and prices'], ['#book', 'Book an audit']],
      hiring: [['#northledger', 'The engine run'], ['#pl300', 'The Power BI project'], ['#receipts', 'Receipts']]
    };
    var tour = U.$('#tour');
    function setDoor(k) {
      U.$$('.door').forEach(function (d) { d.setAttribute('aria-pressed', d.getAttribute('data-door') === k ? 'true' : 'false'); });
      if (!tour) return;
      if (!TOURS[k]) { tour.hidden = true; return; }
      tour.innerHTML = TOURS[k].map(function (t) { return '<li><a href="' + t[0] + '">' + U.esc(t[1]) + '</a></li>'; }).join('');
      tour.hidden = false;
    }
    U.$$('.door').forEach(function (d) {
      d.addEventListener('click', function () {
        var k = d.getAttribute('data-door');
        var on = d.getAttribute('aria-pressed') !== 'true';
        setDoor(on ? k : '');
        U.store.set('nl-door', on ? k : '');
      });
    });
    var saved = U.store.get('nl-door');
    if (saved && TOURS[saved]) setDoor(saved);

    /* ---- report tabs (arrow keys move between tabs) ---- */
    var tabs = U.$$('[role="tab"]');
    function select(t, focus) {
      tabs.forEach(function (x) {
        var on = x === t;
        x.setAttribute('aria-selected', on ? 'true' : 'false');
        x.tabIndex = on ? 0 : -1;
        var p = document.getElementById(x.getAttribute('aria-controls'));
        if (p) p.hidden = !on;
      });
      if (focus) t.focus();
      U.redraw();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener('click', function () { select(t); });
      t.addEventListener('keydown', function (e) {
        var j = null;
        if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
        if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
        if (e.key === 'Home') j = 0;
        if (e.key === 'End') j = tabs.length - 1;
        if (j !== null) { e.preventDefault(); select(tabs[j], true); }
      });
    });

    /* ---- measures / model / best-practice drawer ---- */
    var drawer = U.$('#drawer'), opener = null;
    var TITLES = { measures: 'Measures', model: 'Model', practice: 'Best practice' };
    function closeDrawer(keepFocus) {
      if (!drawer || drawer.hidden) return false;
      drawer.hidden = true;
      U.$$('[data-drawer]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
      if (opener && !keepFocus) opener.focus();
      return true;
    }
    U.$$('[data-drawer]').forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.getAttribute('data-drawer');
        if (!drawer.hidden && b.getAttribute('aria-expanded') === 'true') { closeDrawer(); return; }
        opener = b;
        U.$$('[data-drawer]').forEach(function (x) { x.setAttribute('aria-expanded', x === b ? 'true' : 'false'); });
        U.$$('.dpanel', drawer).forEach(function (p) { p.hidden = p.getAttribute('data-panel') !== k; });
        U.$('#drawer-h').textContent = TITLES[k];
        drawer.hidden = false;
        drawer.focus();
      });
    });
    var dc = U.$('#drawer-close');
    if (dc) dc.addEventListener('click', function () { closeDrawer(); });
    // Esc closes the drawer and nothing else: the report's own Esc (clear the last filter) sees
    // defaultPrevented and stands down
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && closeDrawer()) e.preventDefault();
    });
    // the drawer floats over the page, so when keyboard focus moves out of it, it closes
    if (drawer) drawer.addEventListener('focusout', function (e) {
      if (e.relatedTarget && !drawer.contains(e.relatedTarget)) closeDrawer(true);
    });

    /* ---- sortable tables (buttons in the header; aria-sort) ---- */
    U.$$('table.sortable').forEach(function (tbl) {
      var ths = U.$$('thead th', tbl);
      ths.forEach(function (th, ci) {
        var b = th.querySelector('button');
        if (!b) return;
        b.addEventListener('click', function () {
          var num = b.hasAttribute('data-num');
          var dir = th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
          ths.forEach(function (x) { x.removeAttribute('aria-sort'); });
          th.setAttribute('aria-sort', dir);
          var body = tbl.tBodies[0];
          var rows = Array.prototype.slice.call(body.rows);
          var key = function (row) { var td = row.cells[ci]; return td.hasAttribute('data-sort-value') ? td.getAttribute('data-sort-value') : td.textContent.trim(); };
          var byNum = num || b.hasAttribute('data-sort-num');
          rows.sort(function (a, c) {
            var x = key(a), y = key(c);
            var r = byNum ? (parseFloat(x) - parseFloat(y)) : x.localeCompare(y);
            return dir === 'ascending' ? r : -r;
          });
          rows.forEach(function (r) { body.appendChild(r); });
        });
      });
    });

    /* ---- PBIP file explorer ---- */
    U.$$('button.pf').forEach(function (b) {
      b.addEventListener('click', function () {
        var id = b.getAttribute('aria-controls');
        U.$$('button.pf').forEach(function (o) {
          var on = o === b && o.getAttribute('aria-expanded') !== 'true';
          o.setAttribute('aria-expanded', on ? 'true' : 'false');
          var s = document.getElementById(o.getAttribute('aria-controls'));
          if (s) s.hidden = !on;
        });
        // on a narrow screen the preview sits below the whole tree: bring it into view
        var snip = document.getElementById(id);
        if (snip && !snip.hidden) {
          var q = snip.getBoundingClientRect();
          if (q.top < 0 || q.top > window.innerHeight - 80) snip.scrollIntoView({ block: 'nearest', behavior: 'instant' });
        }
      });
    });

    /* ---- which offer fits? (nothing leaves the page) ---- */
    var chooser = U.$('#chooser');
    if (chooser) chooser.addEventListener('change', function () {
      var v = function (n) { var x = chooser.querySelector('input[name="' + n + '"]:checked'); return x ? x.value : ''; };
      var q1 = v('q1'), q2 = v('q2'), q3 = v('q3');
      if (!q1 && !q2 && !q3) return;
      var pick = q2 === 'ongoing' ? 'retainer' : (q1 === 'many' || q3 === 'yes') ? 'build' : 'audit';
      U.$$('.offer').forEach(function (o) { o.classList.toggle('pickme', o.getAttribute('data-offer') === pick); });
      var o = U.$('.offer[data-offer="' + pick + '"] h3');
      U.$('#pick').textContent = 'Suggested: ' + (o ? o.textContent : pick) + (pick === 'audit' ? '. Start here either way.' : '. Most clients still start with the audit.');
    });

    /* ---- copy the contact address (only rendered once the owner adds one) ---- */
    U.$$('[data-copy]').forEach(function (b) {
      b.addEventListener('click', function () {
        var t = b.getAttribute('data-copy');
        if (navigator.clipboard) navigator.clipboard.writeText(t).then(function () { b.textContent = 'Copied'; });
      });
    });

    /* ---- play the recorded run at its real relative timing ---- */
    var play = U.$('#run-play'), speedSel = U.$('#run-speed'), clock = U.$('#run-clock');
    var rows = U.$$('#run-table tbody tr');
    var stages = NL.run || [];
    var total = stages.reduce(function (s, x) { return s + x.t; }, 0);
    var timer = null;
    if (U.reduced && speedSel) speedSel.value = '0';
    // the clock runs over the stage seconds; the lede gives this same stage total next to the wall clock
    function setClock(t) { if (clock) clock.textContent = U.f(t, 1) + ' s of the stages\' ' + U.f(total, 1) + ' s'; }
    function reset() {
      rows.forEach(function (r) { r.classList.remove('done', 'active'); var b = r.querySelector('.runbar'); if (b) b.style.width = ''; });
    }
    function stop() { if (timer) cancelAnimationFrame(timer); timer = null; if (play) play.textContent = 'Play the run'; }
    if (play) play.addEventListener('click', function () {
      if (timer) { stop(); return; }
      reset();
      var sp = +speedSel.value;
      if (!sp) { rows.forEach(function (r) { r.classList.add('done'); }); setClock(total); return; }
      play.textContent = 'Stop';
      var start = performance.now();
      var tick = function (now) {
        var el = (now - start) / 1000 * sp, acc = 0;
        rows.forEach(function (r, i) {
          var t = stages[i] ? stages[i].t : 0, b = r.querySelector('.runbar');
          if (el >= acc + t) { r.classList.add('done'); r.classList.remove('active'); if (b) b.style.width = '100%'; }
          else if (el >= acc) { r.classList.add('active'); if (b) b.style.width = (100 * (el - acc) / (t || 1)).toFixed(1) + '%'; }
          acc += t;
        });
        setClock(Math.min(el, total));
        if (el < total) timer = requestAnimationFrame(tick); else stop();
      };
      timer = requestAnimationFrame(tick);
    });
  });
})();
