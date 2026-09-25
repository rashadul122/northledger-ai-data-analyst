/* The report: one filter state over the pre-aggregated cube, shared with the scorecard.
   KPI cards and the scorecard ship with their unfiltered values written into the HTML (bound to
   the JSON by data-fact); when a filter is set they are recomputed here, and the recomputed
   elements are marked as computed in the browser instead of bound. Reset restores them. */
(function () {
  'use strict';
  var U = window.NLU, NL = window.NL || {};
  if (!NL.cube) return;
  var F = NL.cube.fields, IX = {};
  F.forEach(function (k, i) { IX[k] = i; });
  var ROWS = NL.cube.rows, DOW = NL.cube.district_of_ward;
  var P = U.PILLARS;
  var S = U.state = { district: '', wards: [], eval_year: '', property_type: '', band: '', basis: 'flag' };
  var listeners = [];
  U.onFilter = function (fn) { listeners.push(fn); };
  U.filtered = function () { return !!(S.district || S.wards.length || S.eval_year || S.property_type); };

  /* ---------------- cube arithmetic ---------------- */
  U.rowsFor = function (opt) {
    opt = opt || {};
    return ROWS.filter(function (r) {
      var w = r[IX.ward];
      if (!opt.ignoreDistrict && S.district && DOW[w] !== S.district) return false;
      if (!opt.ignoreWard && S.wards.length && S.wards.indexOf(w) < 0) return false;
      if (S.eval_year && r[IX.eval_year] !== S.eval_year) return false;
      if (S.property_type && r[IX.property_type] !== S.property_type) return false;
      return true;
    });
  };
  U.sum = function (rows) {
    var s = {};
    F.forEach(function (k, i) { if (i >= 5) s[k] = 0; });
    rows.forEach(function (r) { for (var i = 5; i < F.length; i++) s[F[i]] += r[i]; });
    return s;
  };
  U.byWard = function (rows) {
    var g = {};
    rows.forEach(function (r) { (g[r[IX.ward]] = g[r[IX.ward]] || []).push(r); });
    var out = {};
    Object.keys(g).forEach(function (w) { out[w] = U.sum(g[w]); });
    return out;
  };
  U.pillar = function (s, p, basis) {
    return (basis || S.basis) === 'city' ? (s[p + '_cn'] ? s[p + '_csum'] / s[p + '_cn'] : NaN) : (s[p + '_n'] ? s[p + '_sum'] / s[p + '_n'] : NaN);
  };
  U.metrics = function (s) {
    return {
      n: s.n, units: s.units, mean: s.n ? s.score_sum / s.n : NaN, uw: s.units ? s.score_usum / s.units : NaN,
      green: s.n ? s.green / s.n : NaN, yellow: s.n ? s.yellow / s.n : NaN, red: s.n ? s.red / s.n : NaN,
      refusals: s.n ? 100 * s.refused_evals / s.n : NaN
    };
  };
  var CITY = U.metrics(U.sum(ROWS));
  var CITYP = {};
  P.forEach(function (p) { CITYP[p] = { flag: U.pillar(U.sum(ROWS), p, 'flag'), city: U.pillar(U.sum(ROWS), p, 'city') }; });
  U.cityPillar = function (p) { return CITYP[p][S.basis]; };

  /* ---------------- filter state, chips, URL hash ---------------- */
  function describe() {
    var out = [];
    if (S.district) out.push(['district', 'District: ' + S.district]);
    S.wards.forEach(function (w) { out.push(['ward:' + w, 'Ward ' + w + ' ' + (U.WARDNAME[w] || '')]); });
    if (S.eval_year) out.push(['eval_year', 'Latest evaluation in ' + S.eval_year]);
    if (S.property_type) out.push(['property_type', 'Type: ' + S.property_type]);
    if (S.band) out.push(['band', 'Highlight: ' + S.band]);
    return out;
  }
  U.setFilter = function (k, v, opts) {
    opts = opts || {};
    if (k === 'ward') {
      if (opts.toggle) {
        var i = S.wards.indexOf(v);
        if (opts.multi) { if (i < 0) S.wards.push(v); else S.wards.splice(i, 1); }
        else S.wards = (i >= 0 && S.wards.length === 1) ? [] : [v];
      } else S.wards = v ? [v] : [];
      // a ward outside the chosen district would leave an empty view: the ward wins
      if (S.district && S.wards.some(function (w) { return DOW[w] !== S.district; })) S.district = '';
    } else if (k === 'all') {
      S.district = ''; S.wards = []; S.eval_year = ''; S.property_type = ''; S.band = '';
    } else {
      S[k] = v;
      // a new district drops the chosen wards that lie outside it
      if (k === 'district' && v) S.wards = S.wards.filter(function (w) { return DOW[w] === v; });
    }
    sync();
  };
  function sync() {
    var map = { 'sl-district': S.district, 'sl-ward': S.wards.length === 1 ? S.wards[0] : '', 'sl-year': S.eval_year, 'sl-ptype': S.property_type, 'sl-band': S.band };
    Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) el.value = map[id]; });
    // the ward list offers only the wards of the chosen district
    U.$$('#sl-ward option').forEach(function (o) { o.disabled = !!(o.value && S.district && DOW[o.value] !== S.district); });
    var chips = U.$('#chips');
    if (chips) chips.innerHTML = describe().map(function (d) {
      return '<span class="chip">' + U.esc(d[1]) + '<button type="button" data-clear="' + U.esc(d[0]) + '" aria-label="Remove filter ' + U.esc(d[1]) + '">×</button></span>';
    }).join('');
    var q = [];
    if (S.district) q.push('district=' + encodeURIComponent(S.district));
    if (S.wards.length) q.push('ward=' + S.wards.join(','));
    if (S.eval_year) q.push('year=' + S.eval_year);
    if (S.property_type) q.push('type=' + encodeURIComponent(S.property_type));
    if (S.band) q.push('band=' + S.band);
    if (history.replaceState && (q.length || location.hash.indexOf('#report?') === 0)) {
      try { history.replaceState(null, '', q.length ? '#report?' + q.join('&') : '#report'); } catch (e) { /* some browsers refuse this on local files */ }
    }
    listeners.forEach(function (fn) { fn(); });
  }
  U.sync = sync;
  function offered(id, v) {   // a link value counts only if the slicer offers it
    return U.$$('#' + id + ' option').some(function (o) { return o.value && o.value === v; });
  }
  function fromHash() {
    var h = location.hash;
    if (h.indexOf('#report?') !== 0) return false;
    h.slice(8).split('&').forEach(function (kv) {
      var p = kv.split('='), v = '';
      try { v = decodeURIComponent(p[1] || ''); } catch (e) { v = ''; }
      if (p[0] === 'district' && offered('sl-district', v)) S.district = v;
      if (p[0] === 'ward') S.wards = v ? v.split(',').filter(function (w) { return U.WARDNAME[w]; }) : [];
      if (p[0] === 'year' && offered('sl-year', v)) S.eval_year = v;
      if (p[0] === 'type' && offered('sl-ptype', v)) S.property_type = v;
      if (p[0] === 'band' && ['green', 'yellow', 'red'].indexOf(v) >= 0) S.band = v;
    });
    if (S.district && S.wards.some(function (w) { return DOW[w] !== S.district; })) S.district = '';
    return true;
  }

  /* ---------------- KPI cards ---------------- */
  var kpiOriginal = null, kpiSub = {};
  function renderKpis() {
    var box = U.$('#kpis');
    if (!box) return;
    if (kpiOriginal === null) {
      kpiOriginal = box.innerHTML;
      U.$$('[data-kpi]', box).forEach(function (c) { var sb = c.querySelector('.k-sub'); kpiSub[c.getAttribute('data-kpi')] = sb ? sb.textContent : ''; });
    }
    var m = U.filtered() ? U.metrics(U.sum(U.rowsFor())) : CITY, small = U.filtered() && U.small(m.n);
    if (!U.filtered()) {
      if (box.getAttribute('data-recomputed')) { box.innerHTML = kpiOriginal; box.removeAttribute('data-recomputed'); box.removeAttribute('data-fact-exempt'); }
    } else {
      var set = function (k, val, sub) {
        var c = box.querySelector('[data-kpi="' + k + '"]');
        if (!c) return;
        c.querySelector('.k-val').textContent = val;
        c.querySelector('.k-val').classList.toggle('k-small', small);
        c.querySelector('.k-sub').textContent = sub === undefined ? kpiSub[k] : sub;
      };
      if (small) {
        // a view this small would describe a handful of buildings: no values, at any card
        ['buildings', 'units', 'score', 'green', 'refusals'].forEach(function (k) { set(k, 'Not shown', U.SMALL); });
      } else {
        set('buildings', U.int(m.n), 'latest City evaluation of each, in this view');
        set('units', U.int(m.units));
        set('score', U.f(m.uw, 1), 'simple mean ' + U.f(m.mean, 1));
        set('green', U.pct(m.green, 1), 'yellow ' + U.pct(m.yellow, 1) + ', red ' + U.pct(m.red, 1));
        set('refusals', U.f(m.refusals, 2));
      }
      box.setAttribute('data-recomputed', '1');
      box.setAttribute('data-fact-exempt', 'recomputed in the browser from the cube for the chosen filters');
    }
    var gb = box.querySelector('[data-kpi="green"] .bandbar');
    if (gb && small) gb.innerHTML = '';
    else if (gb) {
      var mm = m;
      gb.innerHTML = '<i class="band-g" style="width:' + (mm.green * 100) + '%;background:var(--band-green)"></i><i style="width:' + (mm.yellow * 100) +
        '%;background:var(--band-yellow)"></i><i style="width:' + (mm.red * 100) + '%;background:var(--band-red)"></i>';
    }
  }

  /* ---------------- visuals ---------------- */
  function wardStats(opt) {
    var g = U.byWard(U.rowsFor(opt || { ignoreWard: true }));
    return Object.keys(g).map(function (w) { var m = U.metrics(g[w]); m.ward = w; m.s = g[w]; return m; });
  }
  // wards under the small-cell threshold are withheld from every ward-level visual
  function shown(d) { return d.filter(function (r) { return !U.small(r.n); }); }
  function heldNote(all, kept, what) {
    var k = all.length - kept.length;
    return k ? '<p class="note vnote">' + k + ' ' + (k === 1 ? what : what + 's') + ' with fewer than ' + U.MIN_N + ' buildings in this view ' + (k === 1 ? 'is' : 'are') + ' not shown.</p>' : '';
  }
  function selected(w) { return !S.wards.length || S.wards.indexOf(w) >= 0; }
  function inBand(v) { return !S.band || U.band(v) === S.band; }

  // Ranked dots: average City score by ward (click to filter)
  U.visual('v-wards', function (W) {
    var all = wardStats(), d = shown(all).sort(function (a, b) { return b.mean - a.mean; });
    if (!all.length) return { html: '<p class="note">No buildings in this view.</p>' };
    if (!d.length) return { html: '<p class="note">' + U.SMALL + '.</p>' };
    var rowH = 20, top = 8, left = Math.min(190, W * 0.42), H = top + d.length * rowH + 30;
    var lo = Math.floor(Math.min.apply(null, d.map(function (x) { return x.mean; })) - 2), hi = Math.ceil(Math.max.apply(null, d.map(function (x) { return x.mean; })) + 1);
    var x = U.scale(lo, hi, left, W - 40), b = '';
    U.ticks(lo, hi, 4).forEach(function (t) { b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="' + top + '" y2="' + (H - 24) + '"/>' + U.txt(x(t), H - 8, U.f(t, 0), 'lab', 'middle'); });
    d.forEach(function (r, i) {
      var y = top + i * rowH + rowH / 2, on = selected(r.ward) && inBand(r.mean);
      var tipS = 'Ward ' + r.ward + ' ' + U.WARDNAME[r.ward] + ': average ' + U.f(r.mean, 1) + ' over ' + U.int(r.n) + ' buildings, ' + U.pct(r.green, 0) + ' green';
      b += '<g class="mk" tabindex="0" role="button" data-ward="' + r.ward + '"' + U.tipAttr(tipS, true) + ' aria-label="' + U.esc(tipS + '. Press Enter to filter.') + '">' +
        '<rect x="0" y="' + (y - rowH / 2) + '" width="' + W + '" height="' + rowH + '" fill="transparent"/>' +
        U.txt(left - 8, y + 4, U.clip(r.ward + ' ' + U.WARDNAME[r.ward], left - 10), 'lab', 'end') +
        '<line x1="' + x(lo) + '" x2="' + x(r.mean) + '" y1="' + y + '" y2="' + y + '" stroke="var(--grid)" stroke-width="2"/>' +
        '<circle cx="' + x(r.mean).toFixed(1) + '" cy="' + y + '" r="5.5" class="dot-a"' + (on ? '' : ' opacity="0.25"') + '/>' +
        U.txt(x(r.mean) + 9, y + 4, U.f(r.mean, 1), 'vlab') + '</g>';
    });
    return {
      svg: heldNote(all, d, 'ward') + U.svg(W, H, 'Average City score by ward in this view, highest first; ' + d[0].ward + ' ' + U.WARDNAME[d[0].ward] + ' is highest at ' + U.f(d[0].mean, 1), b),
      table: { cols: ['Ward', 'Average City score', 'Buildings', 'Share green'], rows: d.map(function (r) { return [r.ward + ' ' + U.WARDNAME[r.ward], U.f(r.mean, 1), U.int(r.n), U.pct(r.green, 0)]; }) }
    };
  });

  // Ward map
  var MAP = NL.map || [];
  var bbox = [180, 90, -180, -90];
  function eachPt(g, fn) {
    (g.type === 'Polygon' ? [g.coordinates] : g.coordinates).forEach(function (poly) { poly.forEach(function (ring) { ring.forEach(fn); }); });
  }
  MAP.forEach(function (f) { eachPt(f.geometry, function (p) { bbox[0] = Math.min(bbox[0], p[0]); bbox[1] = Math.min(bbox[1], p[1]); bbox[2] = Math.max(bbox[2], p[0]); bbox[3] = Math.max(bbox[3], p[1]); }); });
  var kx = Math.cos((bbox[1] + bbox[3]) / 2 * Math.PI / 180);
  U.visual('v-map', function (W) {
    if (!MAP.length) return { html: '<p class="note">No ward boundaries in this build.</p>' };
    var d = {}, all = wardStats(), kept = shown(all), held = {};
    kept.forEach(function (r) { d[r.ward] = r; });
    all.forEach(function (r) { if (U.small(r.n)) held[r.ward] = 1; });
    if (!all.length) return { html: '<p class="note">No buildings in this view.</p>' };
    if (!kept.length) return { html: '<p class="note">' + U.SMALL + '.</p>' };
    var vals = Object.keys(d).map(function (k) { return d[k].mean; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var gw = (bbox[2] - bbox[0]) * kx, gh = bbox[3] - bbox[1];
    var H = Math.round(W * gh / gw) + 34, sc = (W - 10) / gw;
    var px = function (p) { return [5 + (p[0] - bbox[0]) * kx * sc, 5 + (bbox[3] - p[1]) * sc]; };
    var steps = [0.36, 0.5, 0.64, 0.78, 0.92], b = '', narrow = W < 420;
    if (narrow) H += 16;
    MAP.forEach(function (f) {
      var w = f.properties.ward, r = d[w], dd = '';
      (f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates).forEach(function (poly) {
        poly.forEach(function (ring) { dd += U.path(ring.map(px)) + 'Z'; });
      });
      var op = r ? steps[Math.min(4, Math.floor((r.mean - lo) / ((hi - lo) || 1) * 5))] : 0.05;
      var on = r && selected(w) && inBand(r.mean);
      var tipS = 'Ward ' + w + ' ' + f.properties.name + (r ? ': average ' + U.f(r.mean, 1) + ' over ' + U.int(r.n) + ' buildings' : held[w] ? ': fewer than ' + U.MIN_N + ' buildings in this view, not shown' : ': no buildings in this view');
      b += '<path d="' + dd + '" class="map-a mk' + (S.wards.indexOf(w) >= 0 ? ' sel' : '') + '" fill="var(--accent)" fill-opacity="' + (on ? op : op * 0.3).toFixed(2) + '" tabindex="0" role="button" data-ward="' + w + '"' + U.tipAttr(tipS, true) + ' aria-label="' + U.esc(tipS + '. Press Enter to filter.') + '"/>';
    });
    var lx = 5, ly = H - (narrow ? 38 : 22);
    steps.forEach(function (s, i) { b += '<rect x="' + (lx + i * 26) + '" y="' + ly + '" width="24" height="10" fill="var(--accent)" fill-opacity="' + s + '" stroke="var(--axis)" stroke-width="0.5"/>'; });
    // "darker" would be backwards in the dark theme, where more colour reads lighter
    b += U.txt(lx, ly + 22, U.f(lo, 1), 'lab') + U.txt(lx + 130, ly + 22, U.f(hi, 1), 'lab', 'end') +
      (narrow ? U.txt(lx, ly + 36, 'stronger colour = higher average score', 'lab') : U.txt(lx + 140, ly + 9, 'stronger colour = higher average score', 'lab'));
    return {
      svg: heldNote(all, kept, 'ward') + U.svg(W, H, 'Map of the Toronto wards shaded by average City score in this view, from ' + U.f(lo, 1) + ' to ' + U.f(hi, 1), b),
      table: { cols: ['Ward', 'Average City score', 'Buildings'], rows: Object.keys(d).sort().map(function (w) { return [w + ' ' + U.WARDNAME[w], U.f(d[w].mean, 1), U.int(d[w].n)]; }) }
    };
  });

  // Sign bands by property type
  U.visual('v-ptype', function (W) {
    var rows = U.rowsFor(), g = {};
    rows.forEach(function (r) { var k = r[IX.property_type]; (g[k] = g[k] || []).push(r); });
    var ks = Object.keys(g).sort();
    if (!ks.length) return { html: '<p class="note">No buildings in this view.</p>' };
    if (U.small(U.sum(rows).n)) return { html: '<p class="note">' + U.SMALL + '.</p>' };
    var left = 130, rowH = 40, H = 14 + ks.length * rowH + 26, x = U.scale(0, 1, left, W - 8), b = '', t = [];
    ks.forEach(function (k, i) {
      var m = U.metrics(U.sum(g[k])), y = 10 + i * rowH, x0 = left;
      b += U.txt(left - 8, y + 18, k === 'TCHC' ? 'TCHC' : k.charAt(0) + k.slice(1).toLowerCase(), 'lab', 'end');
      if (U.small(m.n)) { b += U.txt(left + 4, y + 17, 'fewer than ' + U.MIN_N + ' buildings; not shown', 'lab'); t.push([k, 'not shown', '', '', '']); return; }
      [['green', m.green, 'band-g'], ['yellow', m.yellow, 'band-y'], ['red', m.red, 'band-r']].forEach(function (s) {
        var w = Math.max(0, x(s[1]) - x(0) - 2);
        if (w > 0) b += '<rect class="mk ' + s[2] + '" x="' + x0.toFixed(1) + '" y="' + y + '" width="' + w.toFixed(1) + '" height="24" rx="3"' + U.tipAttr(k + ', ' + s[0] + ': ' + U.pct(s[1], 1) + ' of ' + U.int(m.n) + ' buildings') + ' tabindex="0"/>';
        if (w > 56) {
          var lab = U.GLYPH[s[0]] + ' ' + U.pct(s[1], 0);
          b += '<rect x="' + (x0 + 3).toFixed(1) + '" y="' + (y + 4) + '" width="' + (lab.length * 8 + 12) + '" height="16" rx="3" fill="var(--surface)" pointer-events="none"/>' + U.txt(x0 + 6, y + 16, lab, 'vlab');
        }
        x0 += w + 2;
      });
      t.push([k, U.int(m.n), U.pct(m.green, 1), U.pct(m.yellow, 1), U.pct(m.red, 1)]);
    });
    b += U.txt(8, H - 6, '● green   ◐ yellow   ○ red, share of buildings', 'lab');
    return { svg: U.svg(W, H, 'Share of buildings in each City sign band by property type', b), table: { cols: ['Property type', 'Buildings', 'Green', 'Yellow', 'Red'], rows: t } };
  });

  // What this view says: prose from the same measures
  U.visual('v-story', function () {
    var m = U.filtered() ? U.metrics(U.sum(U.rowsFor())) : CITY;
    if (!m.n) return { html: '<p>No buildings match these filters.</p>' };
    if (U.filtered() && U.small(m.n)) return { html: '<p>' + U.SMALL + '.</p>' };
    var ws = wardStats({}).filter(function (r) { return r.n >= 5; }).sort(function (a, b) { return b.mean - a.mean; });
    var s = '<p>' + U.int(m.n) + ' buildings, ' + U.int(m.units) + ' units. ' + U.pct(m.green, 1) + ' hold a green sign; ' + U.pct(m.red, 1) + ' are red.</p>';
    s += '<p>The unit-weighted average City score is ' + U.f(m.uw, 1) + (U.filtered() ? ', against ' + U.f(CITY.uw, 1) + ' citywide.' : '.') + '</p>';
    if (ws.length > 1) {
      var lw = ws[ws.length - 1];
      s += '<p>Highest ward in view: ' + ws[0].ward + ' ' + U.WARDNAME[ws[0].ward] + ' (' + U.f(ws[0].mean, 1) + ', ' + U.int(ws[0].n) + ' buildings); lowest: ' + lw.ward + ' ' + U.WARDNAME[lw.ward] + ' (' + U.f(lw.mean, 1) + ', ' + U.int(lw.n) + ' buildings), wards with at least five buildings. A ward with few buildings can top or tail the list by chance.</p>';
    }
    var pp = P.map(function (p) { return [p, U.pillar(U.sum(U.rowsFor()), p) - U.cityPillar(p)]; }).filter(function (x) { return !isNaN(x[1]); }).sort(function (a, b) { return a[1] - b[1]; });
    if (U.filtered() && pp.length) s += '<p>Weakest pillar against the city: ' + U.LABEL[pp[0][0]] + ' (' + (pp[0][1] >= 0 ? '+' : '') + U.f(pp[0][1], 1) + ' points).</p>';
    s += '<p class="note">Descriptive: these are averages of the City\'s own scores, not tested findings.</p>';
    return { html: s };
  });

  // Pillars for this view
  U.visual('v-pillars', function (W) {
    var s = U.sum(U.rowsFor());
    if (U.small(s.n)) return { html: '<p class="note">' + U.SMALL + '.</p>' };
    var d = P.map(function (p) { return { p: p, v: U.pillar(s, p), c: U.cityPillar(p) }; });
    var left = Math.min(220, W * 0.45), rowH = 30, H = 12 + d.length * rowH + 30;
    var vals = d.map(function (x) { return x.v; }).concat(d.map(function (x) { return x.c; })).filter(function (v) { return !isNaN(v); });
    if (!vals.length) return { html: '<p class="note">No buildings in this view.</p>' };
    var lo = Math.floor(Math.min.apply(null, vals) - 2), hi = Math.min(100, Math.ceil(Math.max.apply(null, vals) + 1));
    var x = U.scale(lo, hi, left, W - 44), b = '';
    U.ticks(lo, hi, 4).forEach(function (t) { b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="6" y2="' + (H - 24) + '"/>' + U.txt(x(t), H - 8, U.f(t, 0), 'lab', 'middle'); });
    d.forEach(function (r, i) {
      var y = 10 + i * rowH + rowH / 2;
      b += U.txt(left - 8, y + 4, U.clip(U.LABEL[r.p], left - 10), 'lab', 'end');
      b += '<line x1="' + x(r.c) + '" x2="' + x(r.c) + '" y1="' + (y - 9) + '" y2="' + (y + 9) + '" stroke="var(--ink)" stroke-width="2"/>';
      if (!isNaN(r.v)) b += '<circle class="mk dot-a" cx="' + x(r.v).toFixed(1) + '" cy="' + y + '" r="6" tabindex="0"' + U.tipAttr(U.LABEL[r.p] + ': ' + U.f(r.v, 1) + ' in this view, City ' + U.f(r.c, 1)) + '/>' + U.txt(Math.max(x(r.v), x(r.c)) + 10, y + 4, U.f(r.v, 1), 'vlab');
    });
    b += U.txt(left, H - 24 + 20, '', 'lab');
    return {
      svg: U.svg(W, H, 'Pillar scores in this view as dots, with the City average as a vertical mark', b),
      table: { cols: ['Pillar', 'This view', 'City'], rows: d.map(function (r) { return [U.LABEL[r.p], U.f(r.v, 1), U.f(r.c, 1)]; }) }
    };
  });

  // District by pillar matrix with data bars
  U.visual('v-matrix', function () {
    var ds = {}, rows = U.rowsFor();
    rows.forEach(function (r) { var k = DOW[r[IX.ward]]; (ds[k] = ds[k] || []).push(r); });
    var ks = Object.keys(ds).sort();
    if (!ks.length) return { html: '<p class="note">No buildings in this view.</p>' };
    if (U.small(U.sum(rows).n)) return { html: '<p class="note">' + U.SMALL + '.</p>' };
    var sums = {}; ks.forEach(function (k) { sums[k] = U.sum(ds[k]); });
    var h = '<div class="tscroll" tabindex="0" role="region" aria-label="District by pillar table; scrolls sideways on narrow screens"><table class="dt"><caption class="sr">District by pillar, simple mean over buildings in this view</caption><thead><tr><th scope="col">District</th>' +
      P.map(function (p) { return '<th scope="col">' + U.esc(U.LABEL[p]) + '</th>'; }).join('') + '</tr></thead><tbody>';
    ks.forEach(function (k) {
      if (U.small(sums[k].n)) { h += '<tr><th scope="row">' + U.esc(k) + '</th><td colspan="' + P.length + '">fewer than ' + U.MIN_N + ' buildings; not shown</td></tr>'; return; }
      h += '<tr><th scope="row">' + U.esc(k) + '</th>' + P.map(function (p) {
        var v = U.pillar(sums[k], p);
        return '<td class="num">' + U.f(v, 1) + '<span class="dbar" style="width:' + Math.max(4, (v - 60) / 40 * 100).toFixed(0) + '%" aria-hidden="true"></span></td>';
      }).join('') + '</tr>';
    });
    return { html: h + '</tbody></table></div><p class="note">Bars run from a score of sixty to a hundred.</p>' };
  });

  // Points per fix, the risk chart and the trend are built from ward-level tables, so they cannot
  // follow the year or property-type slicers; they say so when one is set.
  function areaOnlyNote(what) {
    var off = [];
    if (S.eval_year) off.push('year');
    if (S.property_type) off.push('property type');
    if (what === 'trend' && S.district) off.push('district');
    if (!off.length) return '';
    return '<p class="note vnote">Not filtered by ' + off.join(' or ') + ': ' + (what === 'trend' ? 'the lines are citywide; pick a single ward to add its rank.' : 'this chart follows the district and ward filters only.') + '</p>';
  }

  // Points per fix by pillar for this view
  U.visual('v-fixward', function (W) {
    var bw = NL.A.by_ward, sel = bw.filter(function (r) {
      if (S.district && DOW[r.ward] !== S.district) return false;
      if (S.wards.length && S.wards.indexOf(r.ward) < 0) return false;
      return true;
    });
    var n = sel.reduce(function (a, r) { return a + r.n; }, 0);
    var d = P.map(function (p) { return { p: p, v: sel.reduce(function (a, r) { return a + r[p + '_fix_points'] * r.n; }, 0) / (n || 1) }; });
    var total = d.reduce(function (a, r) { return a + r.v; }, 0);
    var left = Math.min(220, W * 0.45), rowH = 28, H = 16 + d.length * rowH + 28;
    var mx = Math.max.apply(null, d.map(function (r) { return r.v; })) * 1.15 || 1;
    var x = U.scale(0, mx, left, W - 50), b = '';
    U.ticks(0, mx, 4).forEach(function (t) { b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="8" y2="' + (H - 22) + '"/>' + U.txt(x(t), H - 6, U.f(t, 1), 'lab', 'middle'); });
    d.forEach(function (r, i) {
      var y = 12 + i * rowH;
      b += U.txt(left - 8, y + 15, U.clip(U.LABEL[r.p], left - 10), 'lab', 'end') +
        '<rect class="mk bar-a" x="' + left + '" y="' + y + '" width="' + Math.max(1, x(r.v) - left).toFixed(1) + '" height="18" rx="3" tabindex="0"' + U.tipAttr(U.LABEL[r.p] + ': ' + U.f(r.v, 2) + ' points per building') + '/>' +
        U.txt(x(r.v) + 6, y + 14, U.f(r.v, 2), 'vlab');
    });
    var scope = S.wards.length ? 'the chosen ward' + (S.wards.length > 1 ? 's' : '') : S.district ? S.district : 'the whole city';
    return {
      svg: areaOnlyNote('fix') + U.svg(W, H, 'City points per building left on the table, by pillar, for ' + scope + ': ' + U.f(total, 2) + ' in total', b),
      table: { cols: ['Pillar', 'Points per building'], rows: d.map(function (r) { return [U.LABEL[r.p], U.f(r.v, 2)]; }).concat([['Total', U.f(total, 2)]]) }
    };
  });

  // Trend by year (two schemas), plus the chosen ward's within-year rank
  U.visual('v-trend', function (W) {
    // the two series are named in a legend strip above the plot, never on the data
    var narrow = W < 520, T = narrow ? 50 : 32;
    var c = NL.trend.city, H = 216 + T, L = 40, R = W - 16, B = H - 30;
    var years = c.map(function (r) { return r.year; });
    var x = U.scale(Math.min.apply(null, years), Math.max.apply(null, years), L + 10, R - 10);
    var y = U.scale(60, 95, B, T), b = '';
    U.ticks(60, 95, 5).forEach(function (t) { b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t) + '" y2="' + y(t) + '"/>' + U.txt(L - 6, y(t) + 4, U.f(t, 0), 'lab', 'end'); });
    U.ticks(Math.min.apply(null, years), Math.max.apply(null, years), 6).forEach(function (t) { b += U.txt(x(t), H - 8, String(t), 'lab', 'middle'); });
    b += '<line x1="' + x(2023) + '" x2="' + x(2023) + '" y1="' + T + '" y2="' + B + '" stroke="var(--series-3)" stroke-dasharray="4 4"/>' + U.txt(x(2023) + 4, B - 6, 'method changed', 'lab');
    [['pre2023', 'ln-s', 'dot-s', 'old method, five-point scale'], ['post2023', 'ln-a', 'dot-a', 'new method, weighted items']].forEach(function (s) {
      var pts = c.filter(function (r) { return r.schema === s[0] && r.n >= 100; }).sort(function (a, b2) { return a.year - b2.year; });
      b += '<path class="ln ' + s[1] + '" d="' + U.path(pts.map(function (r) { return [x(r.year), y(r.mean_score)]; })) + '"/>';
      pts.forEach(function (r) { b += '<circle class="mk ' + s[2] + '" cx="' + x(r.year) + '" cy="' + y(r.mean_score) + '" r="4.5" tabindex="0"' + U.tipAttr(r.year + ', ' + s[3] + ': mean ' + U.f(r.mean_score, 1) + ' over ' + U.int(r.n) + ' evaluations') + '/>'; });
    });
    [['ln-s', 'dot-s', 'old method, five-point scale'], ['ln-a', 'dot-a', 'new method, weighted items']].forEach(function (s, i) {
      var lx = L + (narrow ? 0 : i * 250), ly = narrow ? 12 + i * 20 : 14;
      b += '<path class="ln ' + s[0] + '" d="M' + lx + ' ' + (ly - 4) + 'h26"/><circle class="' + s[1] + '" cx="' + (lx + 13) + '" cy="' + (ly - 4) + '" r="3.5"/>' + U.txt(lx + 34, ly, s[2], 'lab');
    });
    var svg = U.svg(W, H, 'Average City score by year, old and new methods as separate lines; they are not comparable across the change', b);
    var rows = c.map(function (r) { return [String(r.year), r.schema === 'pre2023' ? 'old method' : 'new method', U.f(r.mean_score, 1), U.int(r.n)]; });
    if (S.wards.length === 1) {
      var wd = NL.trend.wards.filter(function (r) { return r.ward === S.wards[0]; });
      var y2 = U.scale(0, 100, B, T), b2 = '';
      [0, 25, 50, 75, 100].forEach(function (t) { b2 += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y2(t) + '" y2="' + y2(t) + '"/>' + U.txt(L - 6, y2(t) + 4, String(t), 'lab', 'end'); });
      U.ticks(Math.min.apply(null, years), Math.max.apply(null, years), 6).forEach(function (t) { b2 += U.txt(x(t), H - 8, String(t), 'lab', 'middle'); });
      b2 += '<path class="ln ln-a" d="' + U.path(wd.map(function (r) { return [x(r.year), y2(r.mean_pctile)]; })) + '"/>';
      wd.forEach(function (r) { b2 += '<circle class="mk dot-a" cx="' + x(r.year) + '" cy="' + y2(r.mean_pctile) + '" r="4.5" tabindex="0"' + U.tipAttr(r.year + ': ward rank ' + U.f(r.mean_pctile, 0) + ' of a hundred over ' + U.int(r.n) + ' evaluations') + '/>'; });
      svg += '<p class="note">Ward ' + S.wards[0] + ' ' + U.esc(U.WARDNAME[S.wards[0]]) + ': average within-year percentile (fifty is the middle of that year\'s evaluations).</p>' +
        U.svg(W, H, 'Within-year percentile of ward ' + S.wards[0] + ' by year', b2);
      wd.forEach(function (r) { rows.push([String(r.year), 'ward ' + r.ward + ' percentile', U.f(r.mean_pctile, 1), U.int(r.n)]); });
    }
    return { svg: areaOnlyNote('trend') + svg, table: { cols: ['Year', 'Series', 'Value', 'Evaluations'], rows: rows } };
  });

  // Risk by ward, test season
  U.visual('v-risk', function (W) {
    var inView = NL.B.by_ward.filter(function (r) {
      if (S.district && DOW[r.ward] !== S.district) return false;
      if (S.wards.length && S.wards.indexOf(r.ward) < 0) return false;
      return r.n_pairs_test > 0;
    });
    // the small-cell rule: a ward with fewer than MIN_N test pairs is withheld, and counted
    var d = inView.filter(function (r) { return !U.small(r.n_pairs_test); }).sort(function (a, b) { return b.observed_rate - a.observed_rate; });
    var held = inView.length - d.length;
    var heldNote = held ? '<p class="note vnote">' + held + ' ward' + (held > 1 ? 's' : '') + ' with fewer than ' + U.MIN_N + ' test pairs ' + (held > 1 ? 'are' : 'is') + ' withheld (not shown).</p>' : '';
    if (!d.length) return { html: areaOnlyNote('risk') + heldNote + '<p class="note">' + (held ? U.SMALL.replace('buildings', 'test pairs') : 'No test pairs in this view.') + '</p>' };
    var left = Math.min(190, W * 0.42), rowH = 20, H = 10 + d.length * rowH + 44;
    var mx = Math.max.apply(null, d.map(function (r) { return Math.max(r.observed_rate, r.mean_model_risk, r.mean_persistence_risk); })) * 1.1;
    var x = U.scale(0, mx, left, W - 14), b = '';
    U.ticks(0, mx, 4).forEach(function (t) { b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="6" y2="' + (H - 38) + '"/>' + U.txt(x(t), H - 24, U.pct(t, 0), 'lab', 'middle'); });
    d.forEach(function (r, i) {
      var y = 10 + i * rowH + rowH / 2;
      var t = 'Ward ' + r.ward + ' ' + U.WARDNAME[r.ward] + ', ' + r.n_pairs_test + ' test pairs: fell below green ' + U.pct(r.observed_rate, 0) + '; model said ' + U.pct(r.mean_model_risk, 0) + ', persistence ' + U.pct(r.mean_persistence_risk, 0);
      b += '<g class="mk" tabindex="0"' + U.tipAttr(t) + '><rect x="0" y="' + (y - rowH / 2) + '" width="' + W + '" height="' + rowH + '" fill="transparent"/>' + U.txt(left - 8, y + 4, U.clip(r.ward + ' ' + U.WARDNAME[r.ward], left - 10), 'lab', 'end') +
        '<rect class="bar-muted" x="' + left + '" y="' + (y - 5) + '" width="' + Math.max(1, x(r.observed_rate) - left).toFixed(1) + '" height="10" rx="2"/>' +
        '<circle class="dot-a" cx="' + x(r.mean_model_risk).toFixed(1) + '" cy="' + y + '" r="4.5"/>' +
        '<rect class="dot-3" x="' + (x(r.mean_persistence_risk) - 4).toFixed(1) + '" y="' + (y - 4) + '" width="8" height="8" transform="rotate(45 ' + x(r.mean_persistence_risk).toFixed(1) + ' ' + y + ')"/></g>';
    });
    b += '<rect class="bar-muted" x="' + left + '" y="' + (H - 14) + '" width="16" height="8"/>' + U.txt(left + 20, H - 6, 'what happened', 'lab') +
      '<circle class="dot-a" cx="' + (left + 130) + '" cy="' + (H - 10) + '" r="4.5"/>' + U.txt(left + 138, H - 6, 'model', 'lab') +
      '<rect class="dot-3" x="' + (left + 196) + '" y="' + (H - 14) + '" width="8" height="8"/>' + U.txt(left + 208, H - 6, 'persistence', 'lab');
    return {
      svg: areaOnlyNote('risk') + heldNote + U.svg(W, H, 'Share of buildings that fell below green at the test evaluation, by ward, with the model and persistence predictions', b),
      tnote: heldNote,
      table: { cols: ['Ward', 'Test pairs', 'Fell below green', 'Model said', 'Persistence said'], rows: d.map(function (r) { return [r.ward + ' ' + U.WARDNAME[r.ward], r.n_pairs_test, U.pct(r.observed_rate, 1), U.pct(r.mean_model_risk, 1), U.pct(r.mean_persistence_risk, 1)]; }) }
    };
  });

  /* ---------------- wiring ---------------- */
  U.onFilter(function () { renderKpis(); U.redraw(['v-wards', 'v-map', 'v-ptype', 'v-story', 'v-pillars', 'v-matrix', 'v-fixward', 'v-trend', 'v-risk']); });
  U.onReady(function () {
    U.$$('.slicers select').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var dim = sel.getAttribute('data-dim');
        U.setFilter(dim === 'ward' ? 'ward' : dim, sel.value);
      });
    });
    var chips = U.$('#chips');
    if (chips) chips.addEventListener('click', function (e) {
      var b = e.target.closest('[data-clear]');
      if (!b) return;
      var idx = U.$$('#chips button').indexOf(b);
      var k = b.getAttribute('data-clear');
      if (k.indexOf('ward:') === 0) U.setFilter('ward', k.slice(5), { toggle: true, multi: true });
      else U.setFilter(k, '');
      var left = U.$$('#chips button');
      (left.length ? left[Math.min(idx, left.length - 1)] : U.$('#sl-district')).focus();
    });
    var rs = U.$('#btn-reset');
    if (rs) rs.addEventListener('click', function () { U.setFilter('all'); });
    document.addEventListener('keydown', function (e) {
      if (e.defaultPrevented) return;
      if (e.key !== 'Escape' || !U.$('#report').contains(document.activeElement)) return;
      if (U.$('#drawer') && !U.$('#drawer').hidden) return;
      if (S.wards.length) U.setFilter('ward', S.wards[S.wards.length - 1], { toggle: true, multi: true });
      else if (S.band) U.setFilter('band', '');
      else if (S.property_type) U.setFilter('property_type', '');
      else if (S.eval_year) U.setFilter('eval_year', '');
      else if (S.district) U.setFilter('district', '');
    });
    // cross-filter: click or Enter on a ward mark
    function wardHit(e) {
      var g = e.target.closest && e.target.closest('[data-ward]');
      if (!g || !g.closest('.viz')) return;
      U.setFilter('ward', g.getAttribute('data-ward'), { toggle: true, multi: e.ctrlKey || e.metaKey });
      var again = document.querySelector('.viz [data-ward="' + g.getAttribute('data-ward') + '"]');
      if (again && e.type === 'keydown') again.focus();
    }
    document.addEventListener('click', wardHit);
    document.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { if (e.target.closest && e.target.closest('.viz [data-ward]')) { e.preventDefault(); wardHit(e); } } });
    U.$$('[data-basis]').forEach(function (b) {
      b.addEventListener('click', function () {
        S.basis = b.getAttribute('data-basis');
        U.$$('[data-basis]').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        U.redraw(['v-pillars', 'v-matrix', 'v-story']);
      });
    });
    // CSV of the current view (built in the browser only when clicked)
    var csv = U.$('#btn-csv');
    if (csv) csv.addEventListener('click', function () {
      var g = U.byWard(U.rowsFor());
      var head = ['ward', 'ward_name', 'district', 'buildings', 'units', 'mean_city_score', 'unit_weighted_city_score', 'share_green', 'refusal_evaluations_per_100'].concat(P.map(function (p) { return p + '_' + S.basis; }));
      var lines = [head.join(',')];
      var held = 0;
      Object.keys(g).sort().forEach(function (w) {
        var m = U.metrics(g[w]);
        if (U.small(m.n)) { held++; return; }
        lines.push([w, '"' + U.WARDNAME[w] + '"', '"' + DOW[w] + '"', m.n, m.units, U.f(m.mean, 2), U.f(m.uw, 2), U.f(m.green, 4), U.f(m.refusals, 2)].concat(P.map(function (p) { return U.f(U.pillar(g[w], p), 2); })).join(','));
      });
      var filt = describe().map(function (d) { return d[1]; }).join('; ') || 'no filters';
      if (held) lines.unshift('# ' + held + ' ward(s) with fewer than ' + U.MIN_N + ' buildings in this view left out');
      lines.unshift('# RentSafeTO view (' + filt + '); City of Toronto open data; Contains information licensed under the Open Government Licence - Toronto.');
      var blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'rentsafe-view.csv';
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    });
    var linked = fromHash();
    if (U.filtered() || S.band) sync(); else renderKpis();
    if (linked) {
      var rep = document.getElementById('report');
      if (rep) setTimeout(function () { rep.scrollIntoView({ behavior: 'instant', block: 'start' }); }, 0);
    }
  });
})();
