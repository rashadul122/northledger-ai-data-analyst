/* NorthLedger Insights v2: shared helpers. No libraries, no network. Every figure drawn here is
   computed from window.NL, which build.py inlines from the files in data/. */
(function () {
  'use strict';
  var NL = window.NL || {};
  var U = {};
  window.NLU = U;

  U.$ = function (s, r) { return (r || document).querySelector(s); };
  U.$$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  U.esc = function (s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  };
  U.f = function (v, dp) { return (v === null || v === undefined || isNaN(v)) ? 'n/a' : Number(v).toFixed(dp === undefined ? 1 : dp); };
  U.int = function (v) { return Math.round(v).toLocaleString('en-US'); };
  U.pct = function (share, dp) { return (share === null || share === undefined || isNaN(share)) ? 'n/a' : U.f(share * 100, dp === undefined ? 1 : dp) + '%'; };
  U.money = function (m) { return '$' + (m / 1000).toFixed(1) + 'B'; };   // US$ millions -> "$784.8B"
  U.MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  U.month = function (ym) { return U.MON[+ym.slice(5, 7) - 1] + ' ' + ym.slice(0, 4); };
  U.reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  U.store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private window: fine */ } }
  };
  U.PILLARS = ['envelope', 'grounds', 'entry', 'systems', 'stairs', 'interiors', 'waste', 'paperwork'];
  U.LABEL = {};
  (NL.sc && NL.sc.columns || []).forEach(function (c) { U.LABEL[c.key] = c.label; });
  U.WARDNAME = {};
  (NL.sc && NL.sc.wards || []).forEach(function (w) { U.WARDNAME[w.ward] = w.ward_name; });
  U.band = function (v) { return v >= 85 ? 'green' : v >= 70 ? 'yellow' : 'red'; };
  // scorecard cells print at 1 dp, rounded half-up on the decimal value (binary noise removed
  // first), and take their band from the number as printed, so "85.0" is always green
  U.half = function (v, dp) {
    if (v === null || v === undefined || isNaN(v)) return 'n/a';
    var k = Math.pow(10, dp === undefined ? 1 : dp), c = Math.abs(+Number(v).toPrecision(12));
    return ((v < 0 ? -1 : 1) * Math.floor(c * k + 0.5 + 1e-9) / k).toFixed(dp === undefined ? 1 : dp);
  };
  U.bandOf = function (v) { return U.band(parseFloat(U.half(v, 1))); };
  // small cells: a view of fewer than MIN_N buildings is not shown, so the public page stays at
  // ward and district level (the City's own address-level file is not re-published here)
  U.MIN_N = 5;
  U.SMALL = 'Fewer than ' + U.MIN_N + ' buildings in this view; not shown';
  U.small = function (n) { return n > 0 && n < U.MIN_N; };
  U.GLYPH = { green: '●', yellow: '◐', red: '○' };

  /* ---------------- scales and SVG ---------------- */
  U.scale = function (d0, d1, r0, r1) {
    var k = (r1 - r0) / ((d1 - d0) || 1);
    var s = function (v) { return r0 + (v - d0) * k; };
    s.inv = function (p) { return d0 + (p - r0) / k; };
    return s;
  };
  U.ticks = function (lo, hi, n) {
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log(span / n) / Math.LN10)), err = n / span * step;
    if (err <= 0.15) step *= 10; else if (err <= 0.35) step *= 5; else if (err <= 0.75) step *= 2;
    var out = [], v = Math.ceil(lo / step) * step;
    for (; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  };
  U.svg = function (w, h, label, body) {
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" role="img" aria-label="' + U.esc(label) +
      '"><title>' + U.esc(label) + '</title>' + body + '</svg>';
  };
  U.txt = function (x, y, s, cls, anchor) {
    return '<text x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" class="' + (cls || 'lab') + '"' + (anchor ? ' text-anchor="' + anchor + '"' : '') + '>' + U.esc(s) + '</text>';
  };
  U.clip = function (s, px) { var n = Math.max(4, Math.floor(px / 6.4)); return s.length > n ? s.slice(0, n - 1) + '\u2026' : s; };
  // a focusable mark gets its tooltip text as its accessible name too; pass named=true when the
  // caller writes its own aria-label and role (the ward marks, which are buttons)
  U.tipAttr = function (s, named) { return ' data-tip="' + U.esc(s) + '"' + (named ? '' : ' role="img" aria-label="' + U.esc(s) + '"'); };
  U.path = function (pts) {
    var d = '', pen = false;
    pts.forEach(function (p) {
      if (!p || isNaN(p[1])) { pen = false; return; }
      d += (pen ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1);
      pen = true;
    });
    return d;
  };

  /* ---------------- tooltip: hover, focus, touch; Esc closes ---------------- */
  var tip = null;
  function showTip(el, x, y) {
    tip = tip || document.getElementById('tip');
    if (!tip) return;
    tip.textContent = el.getAttribute('data-tip');
    tip.hidden = false;
    var r = tip.getBoundingClientRect();
    var left = Math.min(window.innerWidth - r.width - 8, Math.max(8, x + 12));
    var top = y - r.height - 12;
    if (top < 8) top = y + 16;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  U.hideTip = function () { if (tip) tip.hidden = true; };
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) showTip(el, e.clientX, e.clientY); else U.hideTip();
  });
  document.addEventListener('mousemove', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el && tip && !tip.hidden) showTip(el, e.clientX, e.clientY);
  });
  document.addEventListener('focusin', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) { var r = el.getBoundingClientRect(); showTip(el, r.left + r.width / 2, r.top); } else U.hideTip();
  });
  document.addEventListener('focusout', function () { U.hideTip(); });
  document.addEventListener('touchstart', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el && e.touches[0]) showTip(el, e.touches[0].clientX, e.touches[0].clientY); else U.hideTip();
  }, { passive: true });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') U.hideTip(); });
  window.addEventListener('scroll', function () { U.hideTip(); }, { passive: true });

  /* ---------------- visuals: render at container width, with a Table view ---------------- */
  var registry = {};
  U.visual = function (id, fn) {
    var el = document.getElementById(id);
    if (!el) return;
    registry[id] = { el: el, fn: fn, w: 0 };
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        var r = registry[id], w = Math.floor(el.clientWidth || 0);
        if (w && Math.abs(w - r.w) > 8) U.draw(id);
      }).observe(el);
    }
    var fig = el.closest('figure.visual');
    if (fig && fig.getAttribute('data-table') === '1' && !fig.querySelector('.tbl-btn')) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'tb tbl-btn'; b.textContent = 'Table';
      b.setAttribute('aria-pressed', 'false');
      b.addEventListener('click', function () {
        var on = b.getAttribute('aria-pressed') !== 'true';
        b.setAttribute('aria-pressed', on ? 'true' : 'false');   // the name stays "Table"; pressed means the table is shown
        U.draw(id);
      });
      fig.appendChild(b);
    }
    U.draw(id);
  };
  U.draw = function (id) {
    var r = registry[id];
    if (!r) return;
    var w = Math.max(260, Math.floor(r.el.clientWidth || r.el.parentNode.clientWidth || 600));
    r.w = w;
    var out;
    try { out = r.fn(w) || {}; } catch (err) { out = { html: '<p class="note">This visual could not be drawn.</p>' }; if (window.console) console.error(err); }
    var fig = r.el.closest('figure.visual');
    var btn = fig && fig.querySelector('.tbl-btn');
    var asTable = btn && btn.getAttribute('aria-pressed') === 'true';
    var html = out.html || out.svg || '';
    if (asTable && out.table) html = (out.tnote || '') + U.table(out.table);   // tnote: what the table leaves out
    r.el.innerHTML = html;
    if (html.indexOf('tscroll') >= 0) U.markScrollers();
  };
  U.redraw = function (ids) { (ids || Object.keys(registry)).forEach(U.draw); U.markScrollers(); };
  /* sideways scrollers: a visible cue and a right-edge fade while there is more to see */
  U.markScrollers = function () {
    U.$$('.tscroll, .hm-scroll, .illus-scroll').forEach(function (el) {
      if (!el.offsetParent) return;
      var more = el.scrollWidth > el.clientWidth + 2;
      var cue = el.previousElementSibling;
      if (!cue || !cue.classList.contains('swipe-cue')) {
        if (!more) return;
        cue = document.createElement('p');
        cue.className = 'swipe-cue'; cue.setAttribute('aria-hidden', 'true');
        cue.textContent = el.classList.contains('illus-scroll') ? 'Swipe or scroll sideways to see the whole diagram' : 'Swipe or scroll sideways for more columns';
        el.parentNode.insertBefore(cue, el);
        el.addEventListener('scroll', function () { el.classList.toggle('at-end', el.scrollLeft + el.clientWidth >= el.scrollWidth - 2); }, { passive: true });
      }
      cue.hidden = !more;
      el.classList.toggle('can-scroll', more);
      el.classList.toggle('at-end', el.scrollLeft + el.clientWidth >= el.scrollWidth - 2);
    });
  };
  U.table = function (t) {
    var h = '<div class="vtable"><table class="dt"><thead><tr>' + t.cols.map(function (c) { return '<th scope="col">' + U.esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
    h += t.rows.map(function (r) {
      return '<tr>' + r.map(function (c, i) { return (i === 0 ? '<th scope="row">' : '<td class="' + (typeof c === 'number' ? 'num' : '') + '">') + U.esc(c) + (i === 0 ? '</th>' : '</td>'); }).join('') + '</tr>';
    }).join('');
    return h + '</tbody></table></div>';
  };
  var rt = null;
  function onResize() {
    clearTimeout(rt);
    rt = setTimeout(function () {
      Object.keys(registry).forEach(function (id) {
        var r = registry[id];
        var w = Math.max(260, Math.floor(r.el.clientWidth || 0));
        if (w && Math.abs(w - r.w) > 8) U.draw(id);
      });
      U.markScrollers();
    }, 120);
  }
  window.addEventListener('resize', onResize);
  U.onReady = function (fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn();
  };
  window.addEventListener('load', function () { U.markScrollers(); });
  document.addEventListener('toggle', function () { U.markScrollers(); }, true);   // a <details> opened
  /* the page must read with no data too: everything static is already in the HTML */
})();
