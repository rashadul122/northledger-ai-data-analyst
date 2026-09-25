/* Analysis gallery charts: points per fix, reliability, the Forecast Lab (method picker,
   horizon slider, backtest-origin scrubber), seasonal factors, archetypes, control chart and
   pillar correlation. All drawn from window.NL. */
(function () {
  'use strict';
  var U = window.NLU, NL = window.NL || {};
  var P = U.PILLARS;

  /* ---- A: expected points per building, top items ---- */
  if (NL.A) U.visual('v-a-items', function (W) {
    var d = NL.A.items.slice().sort(function (a, b) { return a.rank - b.rank; }).slice(0, 12);
    var top = 26, left = Math.min(260, W * 0.5), rowH = 24, H = top + 10 + d.length * rowH + 28;
    var mx = Math.max.apply(null, d.map(function (r) { return r.expected_points_per_building; })) * 1.18;
    var x = U.scale(0, mx, left, W - 10), b = '';
    // legend strip: the bar colour is the item's tier
    b += '<rect class="bar-a" x="8" y="5" width="14" height="10" rx="2"/>' + U.txt(28, 14, 'High tier', 'lab') +
      '<rect class="bar-s" x="104" y="5" width="14" height="10" rx="2"/>' + U.txt(124, 14, 'Moderate or cosmetic', 'lab');
    U.ticks(0, mx, 4).forEach(function (t) { b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="' + (top + 6) + '" y2="' + (H - 22) + '"/>' + U.txt(x(t), H - 6, U.f(t, 1), 'lab', 'middle'); });
    d.forEach(function (r, i) {
      var y = top + 8 + i * rowH;
      b += U.txt(left - 8, y + 14, U.clip(r.item, left - 12), 'lab', 'end') + '<rect class="mk ' + (r.tier === 'High' ? 'bar-a' : 'bar-s') + '" x="' + left + '" y="' + y + '" width="' + Math.max(1, x(r.expected_points_per_building) - left).toFixed(1) +
        '" height="16" rx="3" tabindex="0"' + U.tipAttr(r.item + ' (' + r.tier + ' tier, ' + U.LABEL[r.pillar] + '): ' + U.f(r.expected_points_per_building, 2) + ' points per building; ' + U.pct(r.share_below_3, 0) + ' of buildings below full marks') + '/>' +
        U.txt(x(r.expected_points_per_building) + 5, y + 13, U.f(r.expected_points_per_building, 2), 'vlab');
    });
    return {
      svg: U.svg(W, H, 'Top items by expected City points per building from bringing them to full marks; the bar colour marks the tier, High or Moderate or cosmetic, as the legend above the bars says', b),
      table: { cols: ['Item', 'Tier', 'Pillar', 'Expected points per building', 'Share below full marks'], rows: d.map(function (r) { return [r.item, r.tier, U.LABEL[r.pillar], U.f(r.expected_points_per_building, 2), U.pct(r.share_below_3, 1)]; }) }
    };
  });

  /* ---- B: reliability ---- */
  if (NL.B) U.visual('v-b-rel', function (W) {
    var S = Math.min(W, 440), H = S, L = 44, B = H - 36, T = 10, R = S - 22;
    var x = U.scale(0, 1, L, R), y = U.scale(0, 1, B, T), b = '';
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) {
      b += '<line class="gridl" x1="' + x(t) + '" x2="' + x(t) + '" y1="' + T + '" y2="' + B + '"/><line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t) + '" y2="' + y(t) + '"/>' +
        U.txt(x(t), B + 16, U.pct(t, 0), 'lab', 'middle') + U.txt(L - 6, y(t) + 4, U.pct(t, 0), 'lab', 'end');
    });
    b += '<path d="M' + x(0) + ' ' + y(0) + 'L' + x(1) + ' ' + y(1) + '" stroke="var(--axis)" stroke-dasharray="4 4"/>' + U.txt(x(0.98), y(0.3), 'dashed: perfectly calibrated', 'lab', 'end');
    var rows = [];
    [['rel_model', 'dot-a', 'model', 'circle'], ['rel_persist', 'dot-3', 'persistence', 'square']].forEach(function (s) {
      NL.B[s[0]].forEach(function (r) {
        var t = s[2] + ', bin ' + r.bin + ': predicted ' + U.pct(r.mean_predicted, 1) + ', happened ' + U.pct(r.observed_rate, 1) + ' (' + U.int(r.n) + ' buildings)';
        var cx = x(r.mean_predicted), cy = y(r.observed_rate), rr = Math.max(4.5, Math.min(11, Math.sqrt(r.n) / 2.4));
        b += s[3] === 'circle' ? '<circle class="mk ' + s[1] + '" cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + rr.toFixed(1) + '" fill-opacity="0.85" stroke="var(--surface)" stroke-width="2" tabindex="0"' + U.tipAttr(t) + '/>'
          : '<rect class="mk ' + s[1] + '" x="' + (cx - rr).toFixed(1) + '" y="' + (cy - rr).toFixed(1) + '" width="' + (2 * rr).toFixed(1) + '" height="' + (2 * rr).toFixed(1) + '" fill-opacity="0.85" stroke="var(--surface)" stroke-width="2" tabindex="0"' + U.tipAttr(t) + '/>';
        rows.push([s[2], r.bin, U.pct(r.mean_predicted, 1), U.pct(r.observed_rate, 1), U.int(r.n)]);
      });
    });
    b += '<circle class="dot-a" cx="' + (L + 10) + '" cy="' + (T + 10) + '" r="5"/>' + U.txt(L + 20, T + 14, 'model', 'lab') + '<rect class="dot-3" x="' + (L + 75) + '" y="' + (T + 5) + '" width="10" height="10"/>' + U.txt(L + 92, T + 14, 'persistence', 'lab');
    b += U.txt((L + R) / 2, H - 2, 'predicted chance of falling below green', 'lab', 'middle');
    return { svg: '<div style="max-width:' + S + 'px">' + U.svg(S, H, 'Reliability: predicted chance of falling below green against the share that did, for the model and for persistence; marker size shows how many buildings', b) + '</div>',
      table: { cols: ['Method', 'Bin', 'Predicted', 'Happened', 'Buildings'], rows: rows } };
  });

  /* ---- C: the Forecast Lab ---- */
  var FL = NL.fl;
  if (FL) {
    var hist = {};
    FL.decomp.forEach(function (r) { hist[r.m] = r.o; });
    var months = FL.decomp.map(function (r) { return r.m; });
    var modelSel = document.getElementById('fl-model'), hIn = document.getElementById('fl-h'), oIn = document.getElementById('fl-o');
    var name = {}; FL.models.forEach(function (m) { name[m.id] = m.name; });
    if (oIn) oIn.max = String(FL.origins.length);
    if (oIn) oIn.value = String(FL.origins.length);
    var addM = function (ym, k) { var y = +ym.slice(0, 4), m = +ym.slice(5, 7) - 1 + k; y += Math.floor(m / 12); m = ((m % 12) + 12) % 12; return y + '-' + (m < 9 ? '0' : '') + (m + 1); };
    U.visual('v-fan', function (W) {
      var mid = modelSel ? modelSel.value : FL.champion, h = hIn ? +hIn.value : 12, oi = oIn ? +oIn.value : FL.origins.length;
      var live = oi >= FL.origins.length, origin, fut, pt, lo = null, hi = null, act = null;
      if (live) {
        origin = FL.forecast.origin; fut = FL.forecast.months.slice(0, h);
        pt = FL.forecast.by_model[mid].point.slice(0, h); lo = FL.forecast.by_model[mid].lo80.slice(0, h); hi = FL.forecast.by_model[mid].hi80.slice(0, h);
      } else {
        var o = FL.origins[oi];
        origin = o.origin; fut = o.months.slice(0, h); pt = o.forecasts[mid].slice(0, h); act = o.actual.slice(0, h);
        if (mid === FL.champion) { lo = o.champion_lo80.slice(0, h); hi = o.champion_hi80.slice(0, h); }
      }
      var start = addM(origin, -47), xs = [], m = start;
      while (m <= origin) { xs.push(m); m = addM(m, 1); }
      var all = xs.concat(fut);
      var hv = xs.map(function (k) { return hist[k]; });
      var vals = hv.concat(pt).concat(lo || []).concat(hi || []).concat(act || []).filter(function (v) { return v; });
      var ymin = Math.min.apply(null, vals) * 0.97, ymax = Math.max.apply(null, vals) * 1.02;
      var twoRow = W < 520, T = twoRow ? 42 : 28, L = 58, R = W - 12, H = Math.max(240, Math.min(340, Math.round(W * 0.5))) + T - 12, Bt = H - 30;
      var x = U.scale(0, all.length - 1, L, R), y = U.scale(ymin, ymax, Bt, T), b = '';
      U.ticks(ymin, ymax, 5).forEach(function (t) { b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t) + '" y2="' + y(t) + '"/>' + U.txt(L - 6, y(t) + 4, U.money(t), 'lab', 'end'); });
      all.forEach(function (k, i) { if (k.slice(5) === '01') b += U.txt(x(i), H - 10, k.slice(0, 4), 'lab', 'middle'); });
      // pandemic window shading when visible
      var c0 = all.indexOf(FL.covid.start), c1 = all.indexOf(FL.covid.end);
      if (c0 >= 0 || c1 >= 0) {
        var a0 = c0 >= 0 ? c0 : 0, a1 = c1 >= 0 ? c1 : all.length - 1;
        b += '<rect class="cov" x="' + x(a0) + '" y="' + T + '" width="' + (x(a1) - x(a0)) + '" height="' + (Bt - T) + '"/>' + U.txt(x(a0) + 4, T + 12, 'pandemic months, left out of estimation', 'lab');
      }
      var off = xs.length - 1;
      if (lo && hi) {
        var band = lo.map(function (v, i) { return [x(off + 1 + i), y(v)]; }).concat(hi.map(function (v, i) { return [x(off + 1 + i), y(v)]; }).reverse());
        b += '<path class="fan" d="' + U.path([[x(off), y(hv[off])]].concat(band.slice(0, lo.length))) + 'L' + band.slice(lo.length).map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join('L') + 'L' + x(off).toFixed(1) + ' ' + y(hv[off]).toFixed(1) + 'Z"/>';
      }
      b += '<path class="ln ln-s" d="' + U.path(hv.map(function (v, i) { return [x(i), v ? y(v) : NaN]; })) + '"/>';
      if (act) b += '<path class="ln ln-s" d="' + U.path([[x(off), y(hv[off])]].concat(act.map(function (v, i) { return [x(off + 1 + i), y(v)]; }))) + '"/>';
      b += '<path class="ln ln-a ln-dash" d="' + U.path([[x(off), y(hv[off])]].concat(pt.map(function (v, i) { return [x(off + 1 + i), y(v)]; }))) + '"/>';
      var rows = [];
      fut.forEach(function (k, i) {
        var t = U.month(k) + ': ' + name[mid] + ' ' + U.money(pt[i]) + (lo ? ', band ' + U.money(lo[i]) + ' to ' + U.money(hi[i]) : '') + (act ? ', actual ' + U.money(act[i]) : '');
        b += '<circle class="mk dot-a" cx="' + x(off + 1 + i).toFixed(1) + '" cy="' + y(pt[i]).toFixed(1) + '" r="4" tabindex="0"' + U.tipAttr(t) + '/>';
        if (act) b += '<circle class="dot-s" cx="' + x(off + 1 + i).toFixed(1) + '" cy="' + y(act[i]).toFixed(1) + '" r="3"/>';
        rows.push([U.month(k), U.money(pt[i]), lo ? U.money(lo[i]) : '', hi ? U.money(hi[i]) : '', act ? U.money(act[i]) : '']);
      });
      b += '<line x1="' + x(off) + '" x2="' + x(off) + '" y1="' + T + '" y2="' + Bt + '" stroke="var(--axis)" stroke-dasharray="2 3"/>' + U.txt(x(off) - 4, Bt - 6, 'origin ' + U.month(origin), 'lab', 'end');
      // legend in its own strip above the plot area (y 0 to T)
      b += '<path class="ln ln-s" d="M' + (L + 6) + ' 8h18"/>' + U.txt(L + 28, 12, act ? 'reported' : 'history', 'lab') +
        '<path class="ln ln-a ln-dash" d="M' + (L + 96) + ' 8h18"/>' + U.txt(L + 118, 12, 'forecast', 'lab') + (lo ? (twoRow ? '<rect class="fan" x="' + (L + 6) + '" y="19" width="18" height="10"/>' + U.txt(L + 28, 28, 'band from past errors', 'lab')
          : '<rect class="fan" x="' + (L + 180) + '" y="3" width="18" height="10"/>' + U.txt(L + 202, 12, 'band from past errors', 'lab')) : '');
      var cap = document.getElementById('fl-cap');
      if (cap) cap.textContent = (live ? 'Forecast from the latest month, ' : 'Replay: the forecast made at ' + U.month(origin) + ', against what was later reported, ') + name[mid] + (lo ? ', with its band' : ' (bands are shown for the champion only)');
      return {
        svg: U.svg(W, H, (live ? 'Forecast' : 'Past forecast from ' + U.month(origin)) + ' of U.S. retail and food services sales by ' + name[mid] + ' over ' + h + ' months, after the last four years of history', b),
        table: { cols: ['Month', 'Forecast', 'Band low', 'Band high', 'Reported'], rows: rows }
      };
    });
    var upd = function (redraw) {
      if (hIn) { document.getElementById('fl-h-out').textContent = hIn.value; hIn.setAttribute('aria-valuetext', hIn.value + ' months ahead'); }
      if (oIn) {
        var ot = +oIn.value >= FL.origins.length ? 'latest' : U.month(FL.origins[+oIn.value].origin);
        document.getElementById('fl-o-out').textContent = ot;
        oIn.setAttribute('aria-valuetext', ot === 'latest' ? 'the latest month, no replay' : 'replay from ' + ot);
      }
      if (redraw !== false) U.draw('v-fan');
    };
    upd(false);
    [modelSel, hIn, oIn].forEach(function (el) { if (el) el.addEventListener('input', upd); });
    if (modelSel) modelSel.addEventListener('change', upd);

    U.visual('v-season', function (W) {
      var d = FL.factors, L = 40, R = W - 10, T = 14, H = 220, Bt = H - 28;
      var vals = d.map(function (r) { return r.ours_pct; }).concat(d.map(function (r) { return r.census_implied_pct; }));
      var lo = Math.floor(Math.min.apply(null, vals) - 1), hi = Math.ceil(Math.max.apply(null, vals) + 1);
      var y = U.scale(lo, hi, Bt, T), bw = (R - L) / d.length, b = '';
      U.ticks(lo, hi, 5).forEach(function (t) { b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t) + '" y2="' + y(t) + '"/>' + U.txt(L - 6, y(t) + 4, U.f(t, 0) + '%', 'lab', 'end'); });
      d.forEach(function (r, i) {
        var x0 = L + i * bw + 3, w = (bw - 8) / 2;
        [[r.ours_pct, 'bar-a', 'ours'], [r.census_implied_pct, 'bar-s', 'implied by Census']].forEach(function (s, j) {
          var yv = y(s[0]), y0 = y(0);
          b += '<rect class="mk ' + s[1] + '" x="' + (x0 + j * (w + 2)).toFixed(1) + '" y="' + Math.min(yv, y0).toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + Math.max(1, Math.abs(y0 - yv)).toFixed(1) + '" tabindex="0"' + U.tipAttr(r.month + ', ' + s[2] + ': ' + (s[0] >= 0 ? '+' : '') + U.f(s[0], 1) + '% against trend') + '/>';
        });
        b += U.txt(x0 + bw / 2 - 3, H - 8, bw < 30 ? r.month.charAt(0) : r.month, 'lab', 'middle');
      });
      b += '<line x1="' + L + '" x2="' + R + '" y1="' + y(0) + '" y2="' + y(0) + '" stroke="var(--axis)"/>';
      b += '<rect class="bar-a" x="' + (R - 190) + '" y="' + T + '" width="12" height="10"/>' + U.txt(R - 174, T + 9, 'ours', 'lab') + '<rect class="bar-s" x="' + (R - 130) + '" y="' + T + '" width="12" height="10"/>' + U.txt(R - 114, T + 9, 'implied by Census', 'lab');
      return {
        svg: U.svg(W, H, 'Seasonal factor for each calendar month as a percentage against trend, ours next to the factor implied by Census seasonal adjustment', b),
        table: { cols: ['Month', 'Ours, % against trend', 'Implied by Census'], rows: d.map(function (r) { return [r.month, U.f(r.ours_pct, 1), U.f(r.census_implied_pct, 1)]; }) }
      };
    });
  }

  /* ---- D: archetypes, control chart, correlation ---- */
  if (NL.D) {
    U.visual('v-d-shape', function (W) {
      var narrow = W < 520, extra = narrow ? 18 : 0;
      var cl = NL.D.shape, panelW = W, left = Math.min(230, W * 0.46), rowH = 22, ph = 30 + extra + P.length * rowH + 20, H = ph * cl.length, b = '', rows = [];
      var mx = 0;
      cl.forEach(function (c) { P.forEach(function (p) { mx = Math.max(mx, Math.abs(c.deviation_from_own_mean[p])); }); });
      mx = Math.ceil(mx + 2);
      var x = U.scale(-mx, mx, left, panelW - 50);
      cl.forEach(function (c, k) {
        var oy = k * ph;
        b += U.txt(8, oy + 18, U.int(c.n_buildings) + ' buildings (' + U.pct(c.share, 0) + '), ' + U.f(c.pct_green, 0) + '% green', 'vlab') +
          (narrow ? U.txt(8, oy + 36, 'lags: ' + (U.LABEL[c.lagging_pillar] || c.lagging_pillar), 'lab') : U.txt(W - 8, oy + 18, 'lags: ' + (U.LABEL[c.lagging_pillar] || c.lagging_pillar), 'lab', 'end'));
        b += '<line x1="' + x(0) + '" x2="' + x(0) + '" y1="' + (oy + 26 + extra) + '" y2="' + (oy + ph - 16) + '" stroke="var(--axis)"/>';
        P.forEach(function (p, i) {
          var v = c.deviation_from_own_mean[p], y = oy + 28 + extra + i * rowH;
          b += U.txt(left - 8, y + 13, U.clip(U.LABEL[p], left - 10), 'lab', 'end') + '<rect class="mk ' + (v < 0 ? 'bar-s' : 'bar-a') + '" x="' + Math.min(x(0), x(v)).toFixed(1) + '" y="' + y + '" width="' + Math.max(1, Math.abs(x(v) - x(0))).toFixed(1) + '" height="15" rx="2" tabindex="0"' +
            U.tipAttr(U.LABEL[p] + ': ' + (v >= 0 ? '+' : '') + U.f(v, 1) + ' points against these buildings\' own average') + '/>' + U.txt((v < 0 ? x(0) + 4 : x(v) + 4), y + 12, (v >= 0 ? '+' : '') + U.f(v, 1), 'vlab');
          rows.push(['cluster ' + (k + 1), U.LABEL[p], U.f(v, 1)]);
        });
      });
      return { svg: U.svg(W, H, 'Shape clusters: for each group of buildings, how far each pillar sits above or below the group\'s own average', b), table: { cols: ['Group', 'Pillar', 'Points against own average'], rows: rows } };
    });

    U.visual('v-d-control', function (W) {
      var c = NL.D.control, pts = c.points, L = 40, R = W - 10, T = 56, H = 276, Bt = H - 30, MINN = c.min_n;
      var vals = pts.map(function (p) { return p.mean_score; }).concat([c.lower, c.upper]);
      var lo = Math.floor(Math.min.apply(null, vals) - 1), hi = Math.ceil(Math.max.apply(null, vals) + 1);
      var x = U.scale(0, pts.length - 1, L, R), y = U.scale(lo, hi, Bt, T), b = '';
      U.ticks(lo, hi, 5).forEach(function (t) { b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t) + '" y2="' + y(t) + '"/>' + U.txt(L - 6, y(t) + 4, U.f(t, 0), 'lab', 'end'); });
      var mi = function (ym) { return +ym.slice(0, 4) * 12 + +ym.slice(5, 7); };
      pts.forEach(function (p, i) {
        if (i === 0 || p.month.slice(0, 4) !== pts[i - 1].month.slice(0, 4)) b += U.txt(x(i), H - 10, p.month.slice(0, 4), 'lab', i === 0 ? 'start' : 'middle');
        if (i > 0 && mi(p.month) - mi(pts[i - 1].month) > 1) b += '<line class="gapl" x1="' + ((x(i) + x(i - 1)) / 2).toFixed(1) + '" x2="' + ((x(i) + x(i - 1)) / 2).toFixed(1) + '" y1="' + T + '" y2="' + Bt + '"/>';
      });
      [[c.upper, 'upper limit'], [c.centre, 'centre'], [c.lower, 'lower limit']].forEach(function (l) {
        var ctr = l[1] === 'centre';
        b += '<line x1="' + L + '" x2="' + R + '" y1="' + y(l[0]) + '" y2="' + y(l[0]) + '" stroke="' + (ctr ? 'var(--ink)' : 'var(--series-3)') + '" stroke-dasharray="' + (ctr ? '0' : '5 4') + '"/>' +
          (ctr ? U.txt(L + 4, y(l[0]) - 4, l[1] + ' ' + U.f(l[0], 1), 'lab') : U.txt(R - 2, y(l[0]) - 4, l[1] + ' ' + U.f(l[0], 1), 'lab', 'end'));
      });
      // months are placed evenly; where calendar months are missing between two points the line breaks
      var seg = [];
      pts.forEach(function (p, i) { if (i > 0 && mi(p.month) - mi(pts[i - 1].month) > 1) seg.push(null); seg.push([x(i), y(p.mean_score)]); });
      b += '<path class="ln ln-s" d="' + U.path(seg) + '"/>';
      pts.forEach(function (p, i) {
        var low = p.n < MINN;
        // a month the run rule flags gets a ring, so the sustained run is visible without the tooltip
        if (/^run /.test(p.flag)) b += '<circle class="mk-run" cx="' + x(i).toFixed(1) + '" cy="' + y(p.mean_score).toFixed(1) + '" r="8.5" fill="none" stroke="var(--series-3)" stroke-width="2"/>';
        b += '<circle class="mk" cx="' + x(i).toFixed(1) + '" cy="' + y(p.mean_score).toFixed(1) + '" r="4.5" fill="' + (low ? 'var(--surface)' : 'var(--series)') + '" stroke="var(--series)" stroke-width="2" tabindex="0"' +
          U.tipAttr(U.month(p.month) + ': mean ' + U.f(p.mean_score, 1) + ' over ' + U.int(p.n) + ' evaluations' + (p.flag ? ' (' + p.flag + ')' : '')) + '/>';
      });
      b += '<circle cx="' + (L + 10) + '" cy="10" r="4" fill="var(--surface)" stroke="var(--series)" stroke-width="2"/>' + U.txt(L + 18, 14, 'hollow: fewer evaluations, not flagged', 'lab') +
        '<line class="gapl" x1="' + (L + 10) + '" x2="' + (L + 10) + '" y1="20" y2="32"/>' + U.txt(L + 18, 30, 'dotted: calendar months skipped', 'lab') +
        '<circle cx="' + (L + 10) + '" cy="42" r="4" fill="var(--series)"/><circle cx="' + (L + 10) + '" cy="42" r="7" fill="none" stroke="var(--series-3)" stroke-width="2"/>' +
        U.txt(L + 22, 46, 'ringed: part of a run the run rule flags', 'lab');
      return {
        svg: U.svg(W, H, 'Monthly average proactive score of evaluations, with the median centre line and robust control limits', b),
        table: { cols: ['Month', 'Mean score', 'Evaluations', 'Flag'], rows: pts.map(function (p) { return [U.month(p.month), U.f(p.mean_score, 1), U.int(p.n), p.flag || '']; }) }
      };
    });

    U.visual('v-d-corr', function () {
      var c = NL.D.corr, ps = c.pillars;
      var h = '<div class="tscroll" tabindex="0" role="region" aria-label="Correlation table; scrolls sideways on narrow screens"><table class="dt corr"><caption class="sr">Correlation between pillar scores across buildings</caption><thead><tr><th scope="col">Pillar</th>' + ps.map(function (p) { return '<th scope="col">' + U.esc(U.LABEL[p]) + '</th>'; }).join('') + '</tr></thead><tbody>';
      ps.forEach(function (p, i) {
        h += '<tr><th scope="row">' + U.esc(U.LABEL[p]) + '</th>' + ps.map(function (q, j) {
          var v = c.matrix[i][j];
          return '<td style="background:color-mix(in srgb, var(--accent) ' + (i === j ? 0 : Math.round(Math.max(0, v) * 45)) + '%, var(--surface))">' + (i === j ? '&middot;' : U.f(v, 2)) + '</td>';
        }).join('') + '</tr>';
      });
      return { html: h + '</tbody></table></div><p class="note">Stronger colour means the two pillars rise and fall together more strongly across buildings.</p>' };
    });
  }
})();
