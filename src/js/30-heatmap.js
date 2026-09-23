/* Scorecard heatmap: sorting, colour modes, the zero basis, report filters, weight sliders,
   the ward drill panel and the one-pillar strip for phones. The table ships fully written in the
   HTML with every cell bound to data/rentsafe_scorecard.json; any view that changes a printed
   value is re-rendered here from the cube and marked as computed in the browser. */
(function () {
  'use strict';
  var U = window.NLU, NL = window.NL || {};
  var table = document.getElementById('hm');
  if (!table || !NL.sc) return;
  var P = U.PILLARS, COLS = P.concat(['overall']);
  var body = document.getElementById('hm-body'), groups = document.getElementById('hm-groups');
  var ORIG = { body: body.innerHTML, groups: groups.innerHTML };
  var H = { color: 'bands', basis: 'flag', sort: null, dir: 'descending', weights: {} };
  P.forEach(function (p) { H.weights[p] = 1; });
  var DIST = {};
  NL.sc.wards.forEach(function (w) { DIST[w.ward] = w.district; });

  // band and shade come from the value as printed (1 dp, half-up), never from the raw value
  function shade(band, v) {
    v = parseFloat(U.half(v, 1));
    if (band === 'green') return v >= 95 ? 3 : v >= 90 ? 2 : 1;
    if (band === 'yellow') return v >= 80 ? 3 : v >= 75 ? 2 : 1;
    return v >= 60 ? 3 : v >= 50 ? 2 : 1;
  }
  function isDefault() { return H.basis === 'flag' && !U.filtered(); }

  /* values for every ward row and group row under the current basis and report filters */
  function model() {
    var rows = {};
    var g = U.byWard(U.rowsFor({ ignoreWard: false }));
    NL.sc.wards.forEach(function (w) {
      var s = g[w.ward];
      if (!s || !s.n) { rows[w.ward] = null; return; }
      if (U.small(s.n)) { rows[w.ward] = { small: true, n: s.n }; return; }
      var r = { n: s.n, overall: s.score_sum / s.n, green: 100 * s.green / s.n, refusals: 100 * s.refused_evals / s.n,
        units: s.units, uw: s.units ? s.score_usum / s.units : NaN, yellow: 100 * s.yellow / s.n, red: 100 * s.red / s.n };
      P.forEach(function (p) { r[p] = U.pillar(s, p, H.basis); });
      rows[w.ward] = r;
    });
    var live = NL.sc.wards.filter(function (w) { return rows[w.ward] && !rows[w.ward].small; });
    var order = live.slice().sort(function (a, b) { return rows[b.ward].overall - rows[a.ward].overall; });
    live.forEach(function (w) { rows[w.ward].rank = 1 + order.filter(function (o) { return rows[o.ward].overall > rows[w.ward].overall; }).length; rows[w.ward].rankOf = live.length; });
    // one ward in view ranks 1 of 1, which says nothing: give its rank among all wards citywide
    // under the same year, property type and basis (wards under MIN_N buildings are not ranked)
    if (live.length === 1) {
      var gc = U.byWard(U.rowsFor({ ignoreWard: true, ignoreDistrict: true }));
      var ranked = Object.keys(gc).filter(function (k) { return gc[k].n >= U.MIN_N; });
      var me = rows[live[0].ward];
      me.rank = 1 + ranked.filter(function (k) { return gc[k].score_sum / gc[k].n > me.overall; }).length;
      me.rankOf = ranked.length;
      me.citywide = true;
    }
    var grp = {};
    NL.sc.districts.concat([NL.sc.city]).forEach(function (d) {
      var inD = function (w) { return d.level === 'city' || w.district === d.label; };
      var mem = live.filter(inD);
      var held = NL.sc.wards.filter(function (w) { return inD(w) && rows[w.ward] && rows[w.ward].small; }).length;
      if (!mem.length) { grp[d.label] = held ? { none: true, held: held } : null; return; }
      var r = { held: held };
      COLS.concat(['green', 'refusals']).forEach(function (k) {
        var vs = mem.map(function (w) { return rows[w.ward][k]; }).filter(function (v) { return !isNaN(v); });
        r[k] = vs.reduce(function (a, b) { return a + b; }, 0) / (vs.length || 1);
      });
      grp[d.label] = r;
    });
    return { rows: rows, groups: grp };
  }

  function cellHtml(v, col) {
    if (v === undefined || isNaN(v)) return '<td class="hc" data-col="' + col + '">n/a</td>';
    var b = U.bandOf(v);
    return '<td class="hc h-' + b + '-' + shade(b, v) + '" data-col="' + col + '" data-v="' + v + '" data-band="' + b + '"><span class="cv">' + U.half(v, 1) +
      '</span><span class="gl" aria-hidden="true">' + U.GLYPH[b] + '</span><span class="sr"> ' + b + '</span></td>';
  }

  function rebuild() {
    if (isDefault()) {
      body.innerHTML = ORIG.body; groups.innerHTML = ORIG.groups;
      table.removeAttribute('data-fact-exempt');
    } else {
      var m = model(), h = '';
      NL.sc.wards.forEach(function (w) {
        var r = m.rows[w.ward];
        h += '<tr class="hw' + (r ? '' : ' empty') + '" data-ward="' + w.ward + '" data-district="' + U.esc(w.district) + '"><th scope="row" class="c-ward"><button type="button" class="ward-btn" aria-haspopup="dialog"><span class="wcode">' + w.ward + '</span> ' + U.esc(w.ward_name) + '</button></th>';
        if (!r) h += '<td class="hc" colspan="' + (COLS.length + 3) + '">no buildings in this view</td>';
        else if (r.small) h += '<td class="hc small-cell" colspan="' + (COLS.length + 3) + '">' + U.SMALL + '</td>';
        else {
          COLS.forEach(function (c) { h += cellHtml(r[c], c); });
          h += '<td class="num" data-col="green" data-v="' + r.green + '">' + U.f(r.green, 0) + '%</td><td class="num" data-col="refusals" data-v="' + r.refusals + '">' + U.f(r.refusals, 1) +
            '</td><td class="num" data-col="rank" data-v="' + r.rank + '">' + r.rank + '</td>';
        }
        h += '</tr>';
      });
      body.innerHTML = h;
      var g = '';
      NL.sc.districts.concat([NL.sc.city]).forEach(function (d) {
        var r = m.groups[d.label], city = d.level === 'city';
        var gn = (city ? (U.filtered() ? 'average of the wards in view' : 'average of the ' + NL.sc.wards.length + ' ward cells') : 'average of its wards in view') + (r && r.held ? ' with at least ' + U.MIN_N + ' buildings' : '');
        g += '<tr class="hg' + (city ? ' hcity' : '') + '"><th scope="row" class="c-ward">' + U.esc(d.label) + '<span class="gnote">' + gn + '</span></th>';
        if (!r) g += '<td class="hc" colspan="' + (COLS.length + 3) + '">no buildings in this view</td>';
        else if (r.none) g += '<td class="hc small-cell" colspan="' + (COLS.length + 3) + '">No ward here has ' + U.MIN_N + ' or more buildings in this view; not shown</td>';
        else { COLS.forEach(function (c) { g += cellHtml(r[c], c); }); g += '<td class="num">' + U.f(r.green, 0) + '%</td><td class="num">' + U.f(r.refusals, 1) + '</td><td class="num"><span class="muted">not ranked</span></td>'; }
        g += '</tr>';
      });
      groups.innerHTML = g;
      table.setAttribute('data-fact-exempt', 'recomputed in the browser from the cube for the chosen filters or basis');
    }
    var note = document.getElementById('hm-filter-note');
    if (note) {
      var bits = [], back = [];
      if (U.filtered()) { bits.push('filtered by the report slicers'); back.push('use Reset filters in the report'); }
      if (H.basis === 'city') { bits.push('zero counted as zero points'); back.push('pick "Refusal flag" under Zero means'); }
      note.hidden = !bits.length;
      note.textContent = bits.length ? 'This table is recomputed in your browser: ' + bits.join(', ') + '. To see the published cells, ' + back.join(' and ') + '.' : '';
    }
    applySort(); paint(); drawStrip();
    refreshDrill();
  }

  /* ---------------- colour: sign bands, or versus the City row ---------------- */
  function cityRowValues() {
    var city = groups.querySelector('tr.hcity'), v = {};
    if (!city) return v;
    U.$$('td[data-col]', city).forEach(function (td) { v[td.getAttribute('data-col')] = parseFloat(td.getAttribute('data-v')); });
    return v;
  }
  function paint() {
    var cv = cityRowValues(), band = U.state.band;
    U.$$('td.hc[data-v]', table).forEach(function (td) {
      var v = parseFloat(td.getAttribute('data-v')), col = td.getAttribute('data-col'), b = U.bandOf(v);
      td.className = td.className.replace(/\b(h|d)-(green|yellow|red|neg|pos)-\d\b|\bd-mid\b|\bdim\b/g, '').trim();
      if (H.color === 'city' && !isNaN(cv[col])) {
        var d = v - cv[col], a = Math.abs(d);
        td.classList.add(a < 0.5 ? 'd-mid' : 'd-' + (d < 0 ? 'neg' : 'pos') + '-' + (a < 2 ? 1 : a < 5 ? 2 : 3));
        td.setAttribute('title', (d >= 0 ? '+' : '') + d.toFixed(1) + ' against the City row');
      } else {
        td.classList.add('h-' + b + '-' + shade(b, v));
        td.removeAttribute('title');
      }
      if (band && b !== band) td.classList.add('dim');
    });
    var lg = document.getElementById('lg-div');
    if (lg) lg.hidden = H.color !== 'city';
  }

  /* ---------------- sorting ---------------- */
  function keyOf(tr, k) {
    if (k === 'ward') return tr.getAttribute('data-ward');
    var td = tr.querySelector('td[data-col="' + k + '"]');
    return td ? parseFloat(td.getAttribute('data-v')) : -Infinity;
  }
  function applySort() {
    U.$$('thead th', table).forEach(function (th) { th.removeAttribute('aria-sort'); });
    if (!H.sort) return;
    var th = table.querySelector('thead button[data-sort="' + H.sort + '"]');
    if (th) th.parentNode.setAttribute('aria-sort', H.dir);
    var rows = U.$$('tr.hw', body);
    rows.sort(function (a, b) {
      var x = keyOf(a, H.sort), y = keyOf(b, H.sort), r;
      if (H.sort === 'ward') r = String(x).localeCompare(String(y)); else r = (isNaN(x) ? -1e9 : x) - (isNaN(y) ? -1e9 : y);
      return H.dir === 'ascending' ? r : -r;
    });
    rows.forEach(function (r) { body.appendChild(r); });
  }
  U.$$('thead button[data-sort]', table).forEach(function (b) {
    b.addEventListener('click', function () {
      var k = b.getAttribute('data-sort');
      if (H.sort === k) H.dir = H.dir === 'descending' ? 'ascending' : 'descending';
      else { H.sort = k; H.dir = (k === 'ward' || k === 'rank' || k === 'refusals') ? 'ascending' : 'descending'; }
      applySort();
    });
  });

  /* ---------------- toggles ---------------- */
  U.$$('[data-color]').forEach(function (b) {
    b.addEventListener('click', function () {
      H.color = b.getAttribute('data-color');
      U.$$('[data-color]').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      paint(); drawStrip();
    });
  });
  U.$$('[data-hbasis]').forEach(function (b) {
    b.addEventListener('click', function () {
      H.basis = b.getAttribute('data-hbasis');
      U.$$('[data-hbasis]').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      rebuild(); weights();
    });
  });

  /* ---------------- weight sliders: sensitivity of the ranking ---------------- */
  var wgrid = document.getElementById('wgrid');
  if (wgrid) {
    wgrid.innerHTML = P.map(function (p) {
      return '<label>' + U.esc(U.LABEL[p]) + ' <span class="muted" id="wv-' + p + '">x1</span><input type="range" min="0" max="3" step="0.5" value="1" data-w="' + p + '" aria-label="Weight for ' + U.esc(U.LABEL[p]) + '"></label>';
    }).join('');
    wgrid.addEventListener('input', function (e) {
      var p = e.target.getAttribute('data-w');
      if (!p) return;
      H.weights[p] = +e.target.value;
      document.getElementById('wv-' + p).textContent = 'x' + e.target.value;
      weights();
    });
  }
  var wr = document.getElementById('w-reset');
  if (wr) wr.addEventListener('click', function () {
    P.forEach(function (p) { H.weights[p] = 1; });
    U.$$('input[data-w]').forEach(function (i) { i.value = 1; document.getElementById('wv-' + i.getAttribute('data-w')).textContent = 'x1'; });
    weights();
  });
  function composite(tr, w) {
    var s = 0, t = 0;
    P.forEach(function (p) { var v = keyOf(tr, p); if (!isNaN(v) && v > -Infinity) { s += w[p] * v; t += w[p]; } });
    return t ? s / t : NaN;
  }
  function ranks(w) {
    var rows = U.$$('tr.hw', body).filter(function (tr) { return !isNaN(composite(tr, w)); });
    var v = rows.map(function (tr) { return [tr.getAttribute('data-ward'), composite(tr, w)]; });
    var out = {};
    v.forEach(function (a) { out[a[0]] = 1 + v.filter(function (b) { return b[1] > a[1]; }).length; });
    return out;
  }
  function weights() {
    var mv = document.getElementById('movers');
    if (!mv) return;
    var eq = {}; P.forEach(function (p) { eq[p] = 1; });
    var same = P.every(function (p) { return H.weights[p] === 1; });
    U.$$('tr.hw', body).forEach(function (tr) { tr.classList.remove('hl'); });
    if (same) { mv.textContent = 'All pillars count the same. Move a slider to see which wards change places.'; return; }
    if (P.every(function (p) { return H.weights[p] === 0; })) { mv.textContent = 'Give at least one pillar some weight.'; return; }
    var a = ranks(eq), b = ranks(H.weights), moves = [];
    Object.keys(a).forEach(function (w) { if (b[w] !== undefined && b[w] !== a[w]) moves.push([w, a[w] - b[w]]); });
    moves.sort(function (x, y) { return Math.abs(y[1]) - Math.abs(x[1]); });
    moves.slice(0, 6).forEach(function (m) { var tr = body.querySelector('tr[data-ward="' + m[0] + '"]'); if (tr) tr.classList.add('hl'); });
    mv.textContent = moves.length ? 'On the weighted pillar average, ' + moves.length + ' wards change places. Biggest moves: ' + moves.slice(0, 6).map(function (m) {
      return m[0] + ' ' + U.WARDNAME[m[0]] + ' ' + (m[1] > 0 ? 'up ' : 'down ') + Math.abs(m[1]);
    }).join('; ') + '.' : 'No ward changes place under these weights.';
  }

  /* ---------------- drill panel ---------------- */
  var drill = document.getElementById('drill'), dOpener = null, dWard = null;
  // an open drill follows the filters and the zero basis: it is redrawn, without moving focus,
  // whenever the table is rebuilt
  function refreshDrill() { if (drill && !drill.hidden && dWard) openDrill(dWard, dOpener, true); }
  function openDrill(ward, btn, keepFocus) {
    var w = NL.sc.wards.filter(function (x) { return x.ward === ward; })[0];
    if (!w || !drill) return;
    dOpener = btn; dWard = ward;
    var fx = NL.A.by_ward.filter(function (x) { return x.ward === ward; })[0] || {};
    // the drill tells the same story as the row it was opened from: published cells in the default
    // view, the recomputed row (same filters, same zero basis) otherwise
    var live = !isDefault(), m = live ? model() : null, r = live ? m.rows[ward] : null, cityRow = live ? m.groups[NL.sc.city.label] : null;
    if (r && r.small) r = null;
    var small = live && m.rows[ward] && m.rows[ward].small;
    var v = {}, c = {};
    P.forEach(function (p) {
      v[p] = live ? (r ? r[p] : NaN) : w[p + '_mean'];
      c[p] = live ? (cityRow ? cityRow[p] : NaN) : NL.sc.city[p + '_mean__avg_of_wards'];
    });
    var W = Math.max(280, Math.min(640, drill.parentNode.clientWidth - 40)), left = Math.min(250, Math.round(W * 0.5)), rowH = 26, Ht = 12 + P.length * rowH + 26;
    var x = U.scale(60, 100, left, W - 50), b = '';
    [60, 70, 80, 90, 100].forEach(function (t) { b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="6" y2="' + (Ht - 22) + '"/>' + U.txt(x(t), Ht - 6, String(t), 'lab', 'middle'); });
    P.forEach(function (p, i) {
      var y = 12 + i * rowH, vv = v[p], cc = c[p];
      b += U.txt(left - 8, y + 14, U.clip(U.LABEL[p], left - 12), 'lab', 'end');
      if (!isNaN(vv)) b += '<rect class="bar-a" x="' + x(60) + '" y="' + y + '" width="' + Math.max(1, x(Math.max(60, vv)) - x(60)).toFixed(1) + '" height="16" rx="3"/>';
      if (!isNaN(cc)) b += '<line x1="' + x(cc) + '" x2="' + x(cc) + '" y1="' + (y - 3) + '" y2="' + (y + 19) + '" stroke="var(--ink)" stroke-width="2"/>';
      b += U.txt(x(Math.max(isNaN(vv) ? 60 : vv, isNaN(cc) ? 60 : cc)) + 8, y + 13, U.half(vv, 1), 'vlab');
    });
    var fixTotal = fx.total_fix_points_per_building;
    var why = [];
    if (U.filtered()) why.push('the report slicers');
    if (H.basis === 'city') why.push('zero counted as zero points');
    var head;
    if (!live) {
      head = '<p>' + U.int(w.n_buildings) + ' buildings, ' + U.int(w.units) + ' units. City score: simple mean ' + U.half(w.overall_mean, 1) + ', unit-weighted ' + U.f(w.overall_unit_weighted, 1) +
        '; ' + U.f(w.pct_green, 1) + '% green, ' + U.f(w.pct_yellow, 1) + '% yellow, ' + U.f(w.pct_red, 1) + '% red. Rank ' + w.rank + ' of ' + NL.sc.wards.length + '.</p>';
    } else if (small) {
      head = '<p>' + U.SMALL + ' (' + why.join(' and ') + ').</p>';
    } else if (!r) {
      head = '<p>No buildings of this ward are in the current view (' + why.join(' and ') + ').</p>';
    } else {
      var others = [];
      if (U.state.eval_year) others.push('year');
      if (U.state.property_type) others.push('property type');
      var rankTxt = r.citywide ? 'Rank ' + r.rank + ' of ' + r.rankOf + ' wards citywide' + (others.length ? ' (same ' + others.join(' and ') + ' filter' + (others.length > 1 ? 's' : '') + ')' : '')
        : 'Rank ' + r.rank + ' of ' + r.rankOf + ' wards in view';
      head = '<p>In this view (' + why.join(' and ') + '): ' + U.int(r.n) + ' buildings, ' + U.int(r.units) + ' units. City score: simple mean ' + U.half(r.overall, 1) + ', unit-weighted ' + U.f(r.uw, 1) +
        '; ' + U.f(r.green, 1) + '% green, ' + U.f(r.yellow, 1) + '% yellow, ' + U.f(r.red, 1) + '% red. ' + rankTxt + '.</p>';
    }
    document.getElementById('drill-h').textContent = 'Ward ' + w.ward + ' ' + w.ward_name + ', ' + w.district;
    document.getElementById('drill-body').innerHTML = head +
      (live && !r ? '' : U.svg(W, Ht, 'Pillar scores for ward ' + w.ward + ' as bars, with the City row marked' + (live ? ', for the current view' : ''), b)) +
      '<p>Points left on the table per building, from the City formula (Analysis A' + (U.filtered() ? '; all years and property types, not filtered' : '') + '): ' + U.f(fixTotal, 2) + ' in total; largest in ' +
      P.slice().sort(function (a, cc2) { return (fx[cc2 + '_fix_points'] || 0) - (fx[a + '_fix_points'] || 0); }).slice(0, 2).map(function (p) { return U.LABEL[p] + ' (' + U.f(fx[p + '_fix_points'], 2) + ')'; }).join(' and ') +
      '.' + (live ? '' : ' Refused or blocked areas: ' + U.f(w.refusal_evals_per_100, 1) + ' evaluations per hundred.') + (live && r ? ' Refused or blocked areas in this view: ' + U.f(r.refusals, 1) + ' evaluations per hundred.' : '') + '</p>' +
      '<p class="note">Unit-weighted pillar values' + (live ? ', published (all years, refusal-flag basis)' : '') + ': ' + P.map(function (p) { return U.LABEL[p] + ' ' + U.f(w[p + '_unit_weighted'], 1); }).join('; ') + '. Source: City of Toronto open data, see <a href="#receipts">receipts</a>.</p>';
    drill.hidden = false;
    if (!keepFocus) drill.focus();
  }
  function closeDrill() {
    if (!drill || drill.hidden) return false;
    drill.hidden = true;
    // the table may have been rebuilt while the drill was open: return focus to the same ward's button
    var b = dOpener && document.body.contains(dOpener) ? dOpener : table.querySelector('tr[data-ward="' + dWard + '"] .ward-btn');
    dWard = null;
    if (b) b.focus();
    return true;
  }
  table.addEventListener('click', function (e) {
    var b = e.target.closest('.ward-btn');
    if (b) openDrill(b.closest('tr').getAttribute('data-ward'), b);
  });
  var dc = document.getElementById('drill-close');
  if (dc) dc.addEventListener('click', closeDrill);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !e.defaultPrevented && closeDrill()) e.preventDefault(); });

  /* ---------------- phones: one pillar at a time, ranked ---------------- */
  var sp = document.getElementById('strip-pillar');
  if (sp) {
    sp.innerHTML = COLS.map(function (c) { return '<option value="' + c + '">' + U.esc(c === 'overall' ? 'Overall (City score)' : U.LABEL[c]) + '</option>'; }).join('');
    sp.addEventListener('change', drawStrip);
  }
  function drawStrip() {
    var el = document.getElementById('v-strip');
    if (!el || !sp) return;
    var col = sp.value;
    var d = U.$$('tr.hw', body).map(function (tr) {
      var td = tr.querySelector('td[data-col="' + col + '"]');
      return td && td.hasAttribute('data-v') ? [tr.getAttribute('data-ward'), parseFloat(td.getAttribute('data-v'))] : null;
    }).filter(Boolean).sort(function (a, b) { return b[1] - a[1]; });
    var lo = Math.min.apply(null, d.map(function (x) { return x[1]; })), hi = Math.max.apply(null, d.map(function (x) { return x[1]; }));
    if (!d.length) { el.innerHTML = '<p class="note">No ward in this view has ' + U.MIN_N + ' or more buildings.</p>'; return; }
    if (d.length === 1) {
      el.innerHTML = '<p class="note">Only one ward is in view: ' + d[0][0] + ' ' + U.esc(U.WARDNAME[d[0][0]]) + ', ' + U.half(d[0][1], 1) + ' ' + U.GLYPH[U.bandOf(d[0][1])] + '. Pick more than one ward to rank them.</p>';
      return;
    }
    el.innerHTML = '<p class="note">Ranked, highest first. The dot sits between the lowest (' + U.half(lo, 1) + ') and highest (' + U.half(hi, 1) + ') ward.</p>' + d.map(function (x, i) {
      var b = U.bandOf(x[1]), pos = hi > lo ? (x[1] - lo) / (hi - lo) * 100 : 50;
      return '<div class="strip-row"><span>' + (i + 1) + '. ' + x[0] + ' ' + U.esc(U.WARDNAME[x[0]]) + '</span><span style="position:relative;height:12px;background:var(--grid);border-radius:6px" aria-hidden="true"><i style="position:absolute;left:calc(' + pos.toFixed(1) +
        '% - 6px);top:0;width:12px;height:12px;border-radius:50%;background:var(--band-' + b + ')"></i></span><span>' + U.half(x[1], 1) + ' ' + U.GLYPH[b] + '</span></div>';
    }).join('');
  }

  U.onFilter(function () { rebuild(); weights(); });
  rebuild(); weights();
})();
