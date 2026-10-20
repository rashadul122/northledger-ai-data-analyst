/*@nl-file 64-vega.js*/
/* The visuals the AI designs for the Dashboard tab (engine/nl_vega.py, AI Analyst v2 phase C, 20 Oct 2026). Each is the engine's
   result of the AI's query (D.visuals[i].columns / rows) drawn with the AI's Vega-Lite spec, which the engine checked: the spec
   never brings data, an expression or an address of its own. Vega, Vega-Lite and the expression interpreter are self-hosted
   (engine/vendor/, make_dist RUNTIME_FILES) and loaded only when a dashboard has a visual. The site's CSP allows no eval, so the
   spec is parsed into an AST and run by vega-interpreter, never compiled to code. The colours are the site's tokens (ts-1..ts-5,
   ink, muted, grid), read from the page when it draws. Every visual keeps its table and its query beside it; in a saved
   dashboard file the drawing is the snapshot taken when it was saved. */
(function () {
  'use strict';
  var NLVG = window.NLVG = {};
  var LIBS = ['engine/vendor/vega.min.js', 'engine/vendor/vega-lite.min.js', 'engine/vendor/vega-interpreter.min.js'];
  var HEIGHT = 260, CELL = 150;
  var loading = null;
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function ready() { return !!(window.vega && window.vega.View && window.vega.expressionInterpreter && window.vegaLite && window.vegaLite.compile); }
  NLVG.load = function () {
    if (ready()) return Promise.resolve();
    if (loading) return loading;
    loading = LIBS.reduce(function (p, src) {
      return p.then(function () {
        return new Promise(function (res, rej) {
          var s = document.createElement('script');
          s.src = src; s.async = false;
          s.onload = function () { res(); };
          s.onerror = function () { rej(new Error('could not load ' + src)); };
          document.head.appendChild(s);
        });
      });
    }, Promise.resolve()).then(function () { if (!ready()) throw new Error('the chart library did not start'); });
    loading.catch(function () { loading = null; });
    return loading;
  };
  function rgb(c) {
    var m = /^#([0-9a-f]{6})$/i.exec(c) || null;
    if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
    m = /^#([0-9a-f]{3})$/i.exec(c);
    if (m) return m[1].split('').map(function (h) { return parseInt(h + h, 16); });
    m = /^rgba?\((\d+)[ ,]+(\d+)[ ,]+(\d+)/i.exec(c);
    return m ? [+m[1], +m[2], +m[3]] : null;
  }
  function mix(a, b, t) {
    var x = rgb(a), y = rgb(b);
    if (!x || !y) return b;
    return 'rgb(' + x.map(function (v, i) { return Math.round(v + (y[i] - v) * t); }).join(',') + ')';
  }
  function tok(cs, name, dflt) { var v = (cs.getPropertyValue('--' + name) || '').trim(); return v || dflt; }
  // the theme: the site's categorical palette, its ink and gridlines, its font; no background (the card's own shows through)
  NLVG.theme = function () {
    var cs = getComputedStyle(document.documentElement);
    var font = (getComputedStyle(document.body).fontFamily || 'sans-serif');
    var ink = tok(cs, 'ink', '#1a1a1a'), muted = tok(cs, 'muted', '#666'), grid = tok(cs, 'grid', '#ddd'), axis = tok(cs, 'axis', muted);
    var cat = ['ts-1', 'ts-2', 'ts-3', 'ts-4', 'ts-5'].map(function (n, i) { return tok(cs, n, ['#1f6feb', '#d97706', '#059669', '#9333ea', '#dc2626'][i]); });
    var surface = tok(cs, 'surface', '#fff');
    // a sequential ramp starts at a tint of the series colour, never at the card's own colour (the lowest cell of a heatmap
    // drawn in the background colour reads as missing)
    var floor = mix(surface, cat[0], 0.22);
    return {
      background: null, font: font, padding: 4,
      view: { stroke: null },
      title: { color: ink, font: font, fontWeight: 600 },
      axis: { labelColor: muted, titleColor: ink, gridColor: grid, domainColor: axis, tickColor: axis, labelFont: font, titleFont: font, labelFontSize: 11, titleFontSize: 12, labelLimit: 160 },
      legend: { labelColor: muted, titleColor: ink, labelFont: font, titleFont: font, labelFontSize: 11, titleFontSize: 12, orient: 'bottom' },
      header: { labelColor: ink, titleColor: ink, labelFont: font, titleFont: font },
      axisXBand: { labelAngle: -35, labelAlign: 'right', labelBaseline: 'middle' },
      range: { category: cat, ramp: [floor, cat[0]], heatmap: [floor, cat[0]], diverging: [tok(cs, 'div-neg', '#c2410c'), surface, tok(cs, 'div-pos', '#1d4ed8')] },
      mark: { color: cat[0] }, bar: { color: cat[0] }, line: { color: cat[0], strokeWidth: 2 }, area: { color: cat[0], opacity: 0.85 },
      point: { color: cat[0], filled: true }, rect: { color: cat[0] }, arc: { color: cat[0] }, text: { color: ink, font: font },
      rule: { color: muted }, tick: { color: cat[0] }, boxplot: { color: cat[0] }
    };
  };
  function faceted(spec) {
    var e = spec.encoding || {};
    return !!(e.column || e.row || e.facet);
  }
  // the spec the page draws: the AI's (as the engine checked it) with the engine's rows, the site's size and theme
  // the fields a spec draws as time: the engine's dates are text YYYY-MM-DD, which a browser reads as UTC midnight while Vega
  // labels in local time, so west of Greenwich every date showed a day early (January 2024 drew as "Dec 2023"); they are
  // handed over as local dates (YYYY/MM/DD reads as local midnight)
  function temporalFields(spec) {
    var out = {};
    (function walk(v) {
      if (Array.isArray(v)) { v.forEach(walk); return; }
      if (!v || typeof v !== 'object') return;
      if (v.type === 'temporal' && typeof v.field === 'string') out[v.field] = 1;
      Object.keys(v).forEach(function (k) { walk(v[k]); });
    })(spec);
    return out;
  }
  var DAY = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$/;
  function localDate(v) {
    var m = typeof v === 'string' && DAY.exec(v);
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)) : v;
  }
  NLVG.spec = function (V, width) {
    var tf = temporalFields(V.spec || {});
    var values = (V.rows || []).map(function (r) { var o = {}; V.columns.forEach(function (c, i) { var v = Array.isArray(r) ? r[i] : r[c]; o[c] = tf[c] ? localDate(v) : v; }); return o; });
    var s = JSON.parse(JSON.stringify(V.spec || {}));
    delete s.$schema;                     // compiled here, never fetched
    s.data = { values: values };
    s.config = NLVG.theme();
    if (faceted(s)) { s.width = CELL; s.height = Math.round(CELL * 0.75); }
    else { s.width = Math.max(200, Math.floor(width || 560) - 16); s.height = HEIGHT; s.autosize = { type: 'fit', contains: 'padding' }; }
    // short band labels (star ratings, codes) stand upright; the theme's slant is for long names
    var ex = (s.encoding || {}).x;
    if (ex && ex.field && (ex.type === 'nominal' || ex.type === 'ordinal') && !(ex.axis && ex.axis.labelAngle !== undefined)) {
      var longest = values.reduce(function (m, o) { return Math.max(m, String(o[ex.field] === null || o[ex.field] === undefined ? '' : o[ex.field]).length); }, 0);
      if (longest <= 6 && values.length <= 24) s.config.axisXBand = { labelAngle: 0, labelAlign: 'center', labelBaseline: 'top' };
    }
    s.description = (V.title || '') + (V.why ? ': ' + V.why : '');
    return s;
  };
  function table(V) {
    var rows = (V.rows || []).slice(0, 200);
    return '<div class="tbl-scroll"><table class="data"><caption>' + esc(V.title) + ' (the query’s result' + (V.truncated ? ', its first ' + rows.length + ' of ' + Number(V.rows_total).toLocaleString('en-US') + ' rows' : '') + ')</caption><thead><tr>' +
      V.columns.map(function (c) { return '<th scope="col">' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr>' + V.columns.map(function (c, i) { var v = Array.isArray(r) ? r[i] : r[c]; return '<td>' + esc(typeof v === 'number' ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : v) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table></div>';
  }
  // pre: the ids' prefix (the dashboard's "nld"; the report's own copy "airv", so both can be on the page at once)
  NLVG.figHtml = function (V, pre) {
    pre = pre || 'nld';
    return '<figure class="visual nld-fig nld-vis" id="' + pre + '-x-' + esc(V.id) + '" tabindex="-1" data-draw="vega" data-vis="' + esc(V.id) + '">' +
      '<figcaption>' + esc(V.title) + '</figcaption>' +
      (V.why ? '<p class="note nld-why">' + esc(V.why) + '</p>' : '') +
      '<div class="viz nld-vg" id="' + pre + '-g-' + esc(V.id) + '" role="img" aria-label="' + esc(V.title + (V.why ? ': ' + V.why : '')) + '"><p class="note">Drawing…</p></div>' +
      '<p class="note nld-static">Not filtered: designed by the AI, computed by the engine from every row it kept' + (V.truncated ? ' (its first ' + V.rows.length + ' result rows drawn)' : '') + '.</p>' +
      '<details class="more nld-fold"><summary>Table and query</summary>' + table(V) + '<pre class="nld-sql"><code>' + esc(V.sql) + '</code></pre></details></figure>';
  };
  NLVG.html = function (D) {
    var vs = (D && D.visuals) || [];
    if (!vs.length) return '';
    return '<section class="nld-visuals" aria-labelledby="nld-vis-h"><h4 id="nld-vis-h">The analyst’s visuals</h4><div class="nld-row">' + vs.map(function (V) { return NLVG.figHtml(V, 'nld'); }).join('') + '</div></section>';
  };
  // one visual into its box: SVG, so it prints, saves into the HTML file and the PDF; a failure leaves its table
  function drawOne(V, el) {
    var w = el.clientWidth || (el.parentNode && el.parentNode.clientWidth) || 560;
    var vg = window.vegaLite.compile(NLVG.spec(V, w)).spec;
    var runtime = window.vega.parse(vg, null, { ast: true });
    var view = new window.vega.View(runtime, { expr: window.vega.expressionInterpreter, renderer: 'svg', container: el, hover: true });
    return view.runAsync().then(function () { el.setAttribute('data-drawn', '1'); var p = el.querySelector('p.note'); if (p) p.remove(); return view; });
  }
  NLVG.views = {};
  NLVG.drawAll = function (box, D, opts) {
    opts = opts || {};
    var vs = (D && D.visuals) || [], pre = opts.pre || 'nld';
    if (!vs.length || !box) return Promise.resolve();
    var fail = function (el, why) { if (el) el.innerHTML = '<p class="note">This visual could not be drawn here (' + esc(why) + '); its table and query are below.</p>'; };
    if (opts.local) {
      vs.forEach(function (V) { var el = box.querySelector('#' + pre + '-g-' + V.id); var s = opts.snapshot && opts.snapshot['vis:' + V.id]; if (el) { if (s) el.innerHTML = s; else fail(el, 'a saved file draws only the snapshot taken when it was saved'); } });
      return Promise.resolve();
    }
    return NLVG.load().then(function () {
      return Promise.all(vs.map(function (V) {
        var el = box.querySelector('#' + pre + '-g-' + V.id);
        if (!el) return null;
        if (NLVG.views[V.id]) { try { NLVG.views[V.id].finalize(); } catch (e) { /* a view already gone */ } }
        el.innerHTML = '';
        return drawOne(V, el).then(function (view) { NLVG.views[V.id] = view; }, function (e) { fail(el, String(e && e.message || e).slice(0, 120)); });
      }));
    }, function (e) {
      vs.forEach(function (V) { fail(box.querySelector('#' + pre + '-g-' + V.id), String(e && e.message || e).slice(0, 120)); });
    });
  };
  // the drawn SVG of each visual, for the saved HTML file (NLD.saveHtml's snapshot)
  NLVG.snapshot = function (box, D, snap) {
    ((D && D.visuals) || []).forEach(function (V) { var el = box && box.querySelector('#nld-g-' + V.id); if (el && el.getAttribute('data-drawn')) snap['vis:' + V.id] = el.innerHTML; });
    return snap;
  };
})();
