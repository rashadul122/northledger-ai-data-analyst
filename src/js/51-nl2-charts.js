/* Report v2 charts: the §5 visual grammar of the confidence plan, hand-written SVG in the site's
   style. Each drawer takes the chart record the engine's adapter wrote (engine/CONTRACT-v2.md §3)
   and the width it has, and returns { svg | html, table, tnote, note }: the picture, the same data
   as a table (the figure's Table button), what the table leaves out, and a sentence under the
   chart. A drawer computes nothing about the business: it places the record's own numbers, and the
   only arithmetic is layout (scales, which third of the range a cell falls in) and a share of a
   total the record carries. Colours come from the site's tokens (light and dark); every mark that
   carries meaning in colour also carries a glyph or a label. */
(function () {
  'use strict';
  var U = window.NLU || {};
  var C = {};
  window.NLC = C;
  var esc = U.esc || function (s) { return String(s); };

  /* ------------------------------------------------------------ formatting */
  function num(v, dp) {
    if (v === null || v === undefined || !isFinite(v)) return 'n/a';
    return Number(v).toLocaleString('en-US', { maximumFractionDigits: dp === undefined ? 2 : dp });
  }
  function sgn1(x) { var s = Math.abs(x).toFixed(1); return s === '0.0' ? '0.0' : (x > 0 ? '+' : '-') + s; }
  // a signed difference in a measure's own units (up to 3 decimals), never a percentage
  function sgnD(x) { var s = num(Math.abs(x), 3); return s === '0' ? '0' : (x > 0 ? '+' : '-') + s; }
  var COUNTY = ['rows', 'count', 'months'];
  // a value in the scale the contract names: fraction (0.2 = +20%), pct (already percent), counts,
  // difference (a change in the measure's own units, where a percentage has no meaning)
  function val(v, scale, unit) {
    if (v === null || v === undefined || !isFinite(v)) return 'n/a';
    if (scale === 'difference') return sgnD(v);
    if (scale === 'fraction') return sgn1(unit === 'pct' ? v : v * 100) + '%';
    if (scale === 'pct') return num(v, 2) + '%';   // as the engine writes a share in its claims (45.2%, 2.45%)
    if (scale === 'money') return num(v, 0);       // a money total or its forecast: whole units, no cents
    if (COUNTY.indexOf(scale) >= 0) return num(Math.round(v), 0);
    return num(v);
  }
  // how a chart prints one value of its series: whole units for money (contract v2: data.scale "money")
  function vfmt(scale) { return scale === 'money' ? function (v) { return num(v, 0); } : function (v) { return num(v); }; }
  function ci(pair, scale) {
    if (!pair || pair[0] === null || pair[1] === null || pair[0] === undefined || pair[1] === undefined) return null;
    return val(pair[0], scale) + ' to ' + val(pair[1], scale);
  }
  function pct1(v) { return v === null || v === undefined || !isFinite(v) ? 'n/a' : Number(v).toFixed(1) + '%'; }
  function share0(f) { return f === null || f === undefined || !isFinite(f) ? 'n/a' : (f * 100).toFixed(0) + '%'; }
  function share1(f) { return f === null || f === undefined || !isFinite(f) ? 'n/a' : (f * 100).toFixed(1) + '%'; }
  function pval(p) { return p === null || p === undefined || !isFinite(p) ? 'n/a' : String(+Number(p).toPrecision(3)); }
  function axis(v) {
    var a = Math.abs(v);
    if (a >= 1e9) return num(v / 1e9, 1) + 'B';
    if (a >= 1e6) return num(v / 1e6, 1) + 'M';
    if (a >= 1e4) return num(v / 1e3, 0) + 'k';
    return num(v, a < 10 ? 2 : 0);
  }
  // a short number for a heatmap cell (the table view has the full value)
  function cell(v, isCount) {
    if (v === null || v === undefined || !isFinite(v)) return '';
    if (isCount) return num(Math.round(v), 0);
    var a = Math.abs(v);
    return num(v, a >= 100 ? 0 : a >= 10 ? 1 : 2);
  }
  function mon(ym) { return U.month ? U.month(ym) : ym; }
  function monShort(ym) { return (U.MON ? U.MON[+ym.slice(5, 7) - 1] : ym.slice(5, 7)) + ' ' + ym.slice(2, 4); }
  function tip(s) { return ' data-tip="' + esc(s) + '"'; }
  C.fmt = { num: num, vfmt: vfmt, val: val, ci: ci, sgn1: sgn1, sgnD: sgnD, pct1: pct1, share0: share0, share1: share1, pval: pval, mon: mon };

  /* ------------------------------------------------------------ shared pieces */
  function domain(vals, zeroFloor) {
    var v = vals.filter(function (x) { return x !== null && x !== undefined && isFinite(x); });
    if (!v.length) return [0, 1];
    var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
    var a = lo - pad, b = hi + pad;
    if (zeroFloor && lo >= 0 && a < 0) a = 0;
    return [a, b];
  }
  function yAxis(y, d, L, R) {
    var b = '', tk = U.ticks(d[0], d[1], 5), step = tk.length > 1 ? Math.abs(tk[1] - tk[0]) : 1;
    // small values get as many decimals as the tick step needs, so 0.022 and 0.024 do not both read 0.02
    var dp = step < 1 ? Math.min(4, Math.ceil(-Math.log(step) / Math.LN10 - 1e-9)) : null;
    tk.forEach(function (t) {
      var yy = y(t).toFixed(1);
      b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + yy + '" y2="' + yy + '"/>' + U.txt(L - 6, y(t) + 4, dp === null || Math.abs(t) >= 1e4 ? axis(t) : num(t, dp), 'lab', 'end');
    });
    return b;
  }
  // month labels under an axis: years at each January on a long series, else every few months
  function xMonths(months, x, y) {
    var n = months.length, out = '', span = Math.abs(x(Math.max(1, n - 1)) - x(0)) || 1, jan = [];
    months.forEach(function (m, i) { if (m.slice(5) === '01') jan.push(i); });
    if (n > 18 && jan.length >= 2) {
      var every = Math.max(1, Math.ceil(jan.length * 44 / span));
      jan.forEach(function (i, k) { if (k % every === 0) out += U.txt(x(i), y, months[i].slice(0, 4), 'lab', 'middle'); });
    } else {
      var step = Math.max(1, Math.ceil(n * 54 / (span + 54)));
      for (var i = 0; i < n; i += step) out += U.txt(x(i), y, monShort(months[i]), 'lab', 'middle');
    }
    return out;
  }
  function legendItem(x, y, mark, label) { return mark + U.txt(x + 22, y + 4, label, 'lab'); }
  // lays legend items out left to right, wrapping when the row is full; returns {svg, h}
  function legend(items, L, R, y0) {
    var x = L, y = y0, out = '';
    items.forEach(function (it) {
      var w = 26 + it.label.length * 6.3 + 14;
      if (x + w > R && x > L) { x = L; y += 18; }
      out += legendItem(x, y, it.mark(x, y), it.label);
      x += w;
    });
    return { svg: out, h: y - y0 + 18 };
  }
  var MK = {
    dot: function (cls) { return function (x, y) { return '<circle class="' + cls + '" cx="' + (x + 9) + '" cy="' + y + '" r="3.5"/>'; }; },
    line: function (cls) { return function (x, y) { return '<path class="' + cls + '" d="M' + x + ' ' + y + 'h18"/>'; }; },
    box: function (cls) { return function (x, y) { return '<rect class="' + cls + '" x="' + x + '" y="' + (y - 5) + '" width="18" height="10"/>'; }; }
  };

  /* ------------------------------------------------------------ #2 trend with the compared windows */
  C.trend_windows = function (W, ch, R) {
    var d = ch.data, M = d.months, V = d.values, wn = d.windows || {}, wm = d.window_means || {}, ef = d.effect || {};
    var inW = function (m, w) { return !!w && m >= w[0] && m <= w[1]; };
    var hasCi = !!(ef.ci && ef.ci[0] !== null && ef.ci[1] !== null && ef.ci[0] !== undefined && ef.ci[1] !== undefined);
    var lvlPct = Math.round((ef.level || 0.95) * 100);
    var vf = vfmt(d.scale);
    // the like-for-like line (the same series on the levels present throughout) and the months where
    // the file's coverage changed, when the engine recorded them (contract v2 §3, #2)
    var LF = d.like_for_like && Array.isArray(d.like_for_like.values) && d.like_for_like.values.length === M.length ? d.like_for_like : null;
    var ST = (d.steps || []).filter(function (s) { return M.indexOf(s.month) >= 0; });
    // the claim's own size bar (its test), for the change strip under the series
    var fnd = ((R && R.findings) || []).filter(function (f) { return (ch.finding_ids || []).indexOf(f.id) >= 0; })[0];
    var bar = fnd && fnd.test && typeof fnd.test.bar === 'number' ? fnd.test.bar : null;
    // the series panel holds the data and the two window means only; its axis says what a value is
    var yTitle = d.unit === 'rows' ? 'rows a month' : /^mean of /.test(d.unit || '') ? 'average ' + String(d.unit).slice(8) + ' a month' : (d.unit || '');
    var L = 56, Rr = W - 12;
    var lgItems = [{ mark: MK.dot('dot-s'), label: 'each month' }, { mark: MK.line('ln nl2-mean-p'), label: 'prior 12-month average' },
      { mark: MK.line('ln nl2-mean-l'), label: 'latest 12-month average' }];
    if (LF) lgItems.push({ mark: MK.line('ln nl2-lfl'), label: 'like for like' });
    // what the numbered lines mark: a change in what the file covers, a step the engine's screen found, or both
    var stCov = ST.some(function (s) { return s.kind !== 'step'; }), stStep = ST.some(function (s) { return s.kind === 'step'; });
    var stWhat = stCov && stStep ? 'where the file\'s coverage changed or the series steps' : stCov ? 'where the file\'s coverage changed' : 'where the engine\'s step screen finds the series stepping';
    if (ST.length) lgItems.push({ mark: function (x, y) { return '<path class="nl2-stepl" d="M' + (x + 9) + ' ' + (y - 7) + 'v14"/>'; }, label: (stCov && stStep ? 'coverage changed or a step' : stCov ? 'coverage changed' : 'the series steps') + ' (numbered)' });
    var lg = legend(lgItems, L, Rr, 30);
    var T0 = lg.h + 42, H0 = T0 + Math.max(170, Math.min(270, Math.round(W * 0.36))) + 30, Bt = H0 - 28;
    var dom = domain(V.concat([wm.prior, wm.latest], LF ? LF.values : []), true);
    var x = U.scale(0, Math.max(1, M.length - 1), L + 8, Rr - 8), y = U.scale(dom[0], dom[1], Bt, T0), step = (x(1) - x(0)) || 10;
    var b = U.txt(4, 14, U.clip(yTitle ? 'Values: ' + yTitle : '', Rr - 8), 'lab nl2-ytitle') + yAxis(y, dom, L, Rr);
    var span = function (w) {
      var a2 = M.indexOf(w[0]), z = M.indexOf(w[1]);
      if (a2 < 0 || z < 0) return null;
      return [x(a2) - step / 2, x(z) + step / 2];
    };
    var sp = wn.prior ? span(wn.prior) : null, sl = wn.latest ? span(wn.latest) : null;
    if (sp) b += '<rect class="nl2-win-p" x="' + sp[0].toFixed(1) + '" y="' + T0 + '" width="' + (sp[1] - sp[0]).toFixed(1) + '" height="' + (Bt - T0) + '"/>' +
      U.txt(sp[0] + 4, T0 - 6, (sp[1] - sp[0]) > 110 ? 'prior 12 months' : 'prior', 'lab');
    if (sl) b += '<rect class="nl2-win-l" x="' + sl[0].toFixed(1) + '" y="' + T0 + '" width="' + (sl[1] - sl[0]).toFixed(1) + '" height="' + (Bt - T0) + '"/>' +
      U.txt(sl[1] - 4, T0 - 6, (sl[1] - sl[0]) > 110 ? 'latest 12 months' : 'latest', 'lab', 'end');
    // a numbered line at the start of each month where the coverage changed; the note says what changed
    var stepWords = function (s) {
      var lv = s.level ? '\'' + s.level + '\'' : null;
      return s.kind === 'entered' ? (lv ? lv + ' first appears' : 'a level first appears') : s.kind === 'left' ? (lv ? lv + ' has no rows after this' : 'a level stops')
        : 'the series steps here';
    };
    ST.forEach(function (s, i) {
      var xx = x(M.indexOf(s.month)) - step / 2, t = mon(s.month) + ': ' + stepWords(s);
      b += '<g class="nl2-step"' + tip(t) + '><path class="nl2-stepl" d="M' + xx.toFixed(1) + ' ' + (T0 + 20) + 'V' + Bt + '"/>' +
        '<rect class="nl2-stepb" x="' + (xx - 8).toFixed(1) + '" y="' + (T0 + 2) + '" width="16" height="16" rx="8"/>' +
        '<text class="nl2-stepn" x="' + xx.toFixed(1) + '" y="' + (T0 + 14) + '" text-anchor="middle">' + (i + 1) + '</text></g>';
    });
    if (sp && wm.prior !== null) b += '<path class="ln nl2-mean-p" d="M' + sp[0].toFixed(1) + ' ' + y(wm.prior).toFixed(1) + 'H' + sp[1].toFixed(1) + '"' + tip('Prior 12-month average: ' + vf(wm.prior)) + '/>';
    if (sl && wm.latest !== null) b += '<path class="ln nl2-mean-l" d="M' + sl[0].toFixed(1) + ' ' + y(wm.latest).toFixed(1) + 'H' + sl[1].toFixed(1) + '"' + tip('Latest 12-month average: ' + vf(wm.latest)) + '/>';
    b += '<path class="ln nl2-thin" d="' + U.path(V.map(function (v, i) { return v === null ? null : [x(i), y(v)]; })) + '"/>';
    if (LF) {
      b += '<path class="ln nl2-lfl" d="' + U.path(LF.values.map(function (v, i) { return v === null ? null : [x(i), y(v)]; })) + '"/>';
      var rl = Math.max(1.8, Math.min(2.8, step / 3.2));
      LF.values.forEach(function (v, i) {
        if (v !== null) b += '<rect class="nl2-lfd" x="' + (x(i) - rl).toFixed(1) + '" y="' + (y(v) - rl).toFixed(1) + '" width="' + (2 * rl).toFixed(1) + '" height="' + (2 * rl).toFixed(1) + '"' + tip(mon(M[i]) + ', like for like: ' + vf(v)) + '/>';
      });
    }
    var rad = Math.max(2.2, Math.min(3.6, step / 2.6));
    V.forEach(function (v, i) {
      if (v === null) { b += '<text class="lab nl2-gap" x="' + x(i).toFixed(1) + '" y="' + (Bt - 4) + '" text-anchor="middle"' + tip(mon(M[i]) + ': under the engine\'s rows-a-month floor, left out') + '>×</text>'; return; }
      b += '<circle class="dot-s" cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="' + rad.toFixed(1) + '"' + tip(mon(M[i]) + ': ' + vf(v) + (d.rows ? ' (' + num(d.rows[i], 0) + ' rows)' : '')) + '/>';
    });
    b += '<line x1="' + L + '" x2="' + Rr + '" y1="' + Bt + '" y2="' + Bt + '" stroke="var(--axis)"/>' + xMonths(M, x, H0 - 8) + lg.svg;
    // the change strip: the effect and its interval on their own percent axis, zero and the bar marked;
    // with a like-for-like comparison, a second row for it
    var H = H0, lfEst = LF && LF.estimate !== null && LF.estimate !== undefined && isFinite(LF.estimate);
    var lfCi = lfEst && LF.ci && LF.ci[0] !== null && LF.ci[1] !== null && LF.ci[0] !== undefined && LF.ci[1] !== undefined;
    if (ef.estimate !== null && ef.estimate !== undefined && isFinite(ef.estimate)) {
      var SL = lfEst ? (W < 480 ? 86 : 104) : L, s0 = H0 + 16, sy = s0 + 30, sy2 = sy + 24, yEnd = lfEst ? sy2 : sy;
      var lo = hasCi ? ef.ci[0] : ef.estimate, hi = hasCi ? ef.ci[1] : ef.estimate;
      var ext = [lo, hi, 0].concat(bar === null ? [] : [bar, -bar], lfEst ? [LF.estimate] : [], lfCi ? LF.ci : []), a0 = Math.min.apply(null, ext), a1 = Math.max.apply(null, ext), pad = (a1 - a0) * 0.08 || 0.05;
      var sx = U.scale(a0 - pad, a1 + pad, SL + 8, Rr - 8);
      b += U.txt(4, s0, U.clip('Change, latest 12 months against the 12 before' + (hasCi ? ', with its ' + lvlPct + '% interval' : ''), Rr - 8), 'lab nl2-ytitle');
      // as many ticks as fit (about 64 px a label), whole percents printed without a decimal
      var tickPct = function (t) { var v = t * 100, r = Math.round(v); return Math.abs(v - r) < 1e-9 ? (r > 0 ? '+' : r < 0 ? '-' : '') + Math.abs(r) + '%' : sgn1(v) + '%'; };
      U.ticks(a0 - pad, a1 + pad, Math.max(2, Math.min(5, Math.floor((Rr - SL) / 64)))).forEach(function (t) {
        b += '<line class="gridl" x1="' + sx(t).toFixed(1) + '" x2="' + sx(t).toFixed(1) + '" y1="' + (sy - 12) + '" y2="' + (yEnd + 8) + '"/>' + U.txt(sx(t), yEnd + 24, tickPct(t), 'lab', 'middle');
      });
      b += '<line class="nl2-ref" x1="' + sx(0).toFixed(1) + '" x2="' + sx(0).toFixed(1) + '" y1="' + (sy - 14) + '" y2="' + (yEnd + 10) + '"/>';
      if (bar !== null) [bar, -bar].forEach(function (bb) {
        b += '<line class="nl2-bar-l" x1="' + sx(bb).toFixed(1) + '" x2="' + sx(bb).toFixed(1) + '" y1="' + (sy - 12) + '" y2="' + (yEnd + 8) + '" stroke="var(--axis)" stroke-dasharray="3 3"' + tip('The size bar: a change must be shown to be at least ' + sgn1(Math.abs(bb) * 100).replace('+', '') + '% to be CONFIRMED') + '/>';
      });
      if (lfEst) b += U.txt(SL - 2, sy + 4, 'as filed', 'lab', 'end') + U.txt(SL - 2, sy2 + 4, 'like for like', 'lab', 'end');
      if (hasCi) b += '<line class="nl2-cil" x1="' + sx(lo).toFixed(1) + '" x2="' + sx(hi).toFixed(1) + '" y1="' + sy + '" y2="' + sy + '"' + tip(lvlPct + '% interval of the change: ' + ci(ef.ci, 'fraction')) + '/>';
      b += '<circle class="dot-a" cx="' + sx(ef.estimate).toFixed(1) + '" cy="' + sy + '" r="4.5"' + tip('Change: ' + val(ef.estimate, 'fraction')) + '/>';
      if (lfEst) {
        var lfT = 'Like for like: ' + val(LF.estimate, 'fraction') + (lfCi ? ' (' + Math.round((LF.level || 0.95) * 100) + '% interval ' + ci(LF.ci, 'fraction') + ')' : ' (descriptive, not tested)');
        if (lfCi) b += '<line class="nl2-cil" x1="' + sx(LF.ci[0]).toFixed(1) + '" x2="' + sx(LF.ci[1]).toFixed(1) + '" y1="' + sy2 + '" y2="' + sy2 + '"' + tip(lfT) + '/>';
        b += '<rect class="nl2-lfd nl2-lfd-strip" x="' + (sx(LF.estimate) - 4.5).toFixed(1) + '" y="' + (sy2 - 4.5) + '" width="9" height="9"' + tip(lfT) + '/>';
      }
      H = yEnd + 34;
    }
    var lfCol = String(LF && LF.column || '').replace(/_/g, ' ').trim().toLowerCase();
    var lfPl = !lfCol ? 'values' : /(s|x|z|ch|sh)$/.test(lfCol) ? (/s$/.test(lfCol) ? lfCol : lfCol + 'es') : /[^aeiou]y$/.test(lfCol) ? lfCol.slice(0, -1) + 'ies' : lfCol + 's';
    var lfWords = LF ? LF.levels_kept + ' ' + lfPl + ' present in every modelled month' : '';
    var label = ch.title + ': ' + M.length + ' months from ' + mon(M[0]) + ' to ' + mon(M[M.length - 1]) + '; average ' + vf(wm.prior) + ' in the prior 12 months and ' + vf(wm.latest) + ' in the latest 12, a change of ' + val(ef.estimate, 'fraction') +
      (ci(ef.ci, 'fraction') ? ' (' + lvlPct + '% interval ' + ci(ef.ci, 'fraction') + ')' : '') +
      (lfEst ? '; like for like (' + lfWords + ') ' + val(LF.estimate, 'fraction') : '') +
      (ST.length ? '; numbered lines mark ' + stWhat + ': ' + ST.map(function (s) { return mon(s.month); }).join(', ') : '');
    var bandLo = hasCi && wm.prior !== null && wm.prior !== undefined ? wm.prior * (1 + ef.ci[0]) : null, bandHi = bandLo === null ? null : wm.prior * (1 + ef.ci[1]);
    var stepOf = {};
    ST.forEach(function (s, i) { stepOf[s.month] = (stepOf[s.month] ? stepOf[s.month] + '; ' : '') + (i + 1) + ': ' + stepWords(s); });
    var cols = ['Month', 'Value' + (d.unit ? ' (' + d.unit + ')' : ''), 'Rows', 'Window'].concat(LF ? ['Like for like'] : [], ST.length ? [stCov ? 'Coverage changed' : 'Step'] : []);
    var lfNote = !LF ? '' : ' The dotted line with squares is the same series like for like: only the ' + esc(lfWords) + (lfEst ? ', ' + (LF.tested ? 'graded ' + esc(String(LF.grade || '').replace(/_/g, ' ')) + ' at <b>' + esc(val(LF.estimate, 'fraction')) + '</b>' + (lfCi ? ' (' + Math.round((LF.level || 0.95) * 100) + '% interval ' + esc(ci(LF.ci, 'fraction')) + ')' : '')
      : 'a change of <b>' + esc(val(LF.estimate, 'fraction')) + '</b> (descriptive, not tested)') + '; the square on the strip' : '') + '.';
    var stNote = !ST.length ? '' : ' Numbered lines mark ' + esc(stWhat) + ': ' + ST.map(function (s, i) { return (i + 1) + ', ' + esc(stepWords(s)) + ' (' + esc(mon(s.month)) + ')'; }).join('; ') + '.';
    return {
      svg: U.svg(W, H, label, b),
      table: { cols: cols, rows: M.map(function (m, i) {
        return [m, vf(V[i]), d.rows ? num(d.rows[i], 0) : '', inW(m, wn.latest) ? 'latest' : inW(m, wn.prior) ? 'prior' : ''].concat(LF ? [vf(LF.values[i])] : [], ST.length ? [stepOf[m] || ''] : []);
      }) },
      tnote: '<p class="note">Averages: ' + vf(wm.prior) + ' in the prior 12 months, ' + vf(wm.latest) + ' in the latest 12. ' + (bandLo === null ? '' : 'The interval of the change, ' + ci(ef.ci, 'fraction') + ', applied to the prior average, is consistent with a latest average between ' + vf(bandLo) + ' and ' + vf(bandHi) + '.') +
        (LF && LF.window_means ? ' Like for like: ' + vf(LF.window_means.prior) + ' and ' + vf(LF.window_means.latest) + '.' : '') + (d.month_floor ? ' Months under ' + num(d.month_floor, 0) + ' rows show n/a.' : '') + '</p>',
      note: 'Latest 12 months against the 12 before: <b>' + esc(val(ef.estimate, 'fraction')) + '</b>' + (hasCi ? ' (' + lvlPct + '% interval ' + esc(ci(ef.ci, 'fraction')) + ')' : '') +
        '. The dots are every month, so the spread behind the two averages stays visible. The strip underneath puts the change and its interval on a percent scale' + (bar !== null ? ', with the ' + esc(sgn1(bar * 100).replace('+', '')) + '% size bar dashed either side of zero' : '') + '.' + lfNote + stNote + (d.month_floor && V.some(function (v) { return v === null; }) ? ' A month with fewer than ' + esc(num(d.month_floor, 0)) + ' rows is left out (×).' : '')
    };
  };

  /* ------------------------------------------------------------ #4 forecast fan */
  C.fan = function (W, ch, R) {
    var d = ch.data, hist = d.history || [], fw = d.forward || [], lvl = Math.round((d.level || 0.8) * 100), vf = vfmt(d.scale);
    var months = hist.map(function (p) { return p.month; }).concat(fw.map(function (p) { return p.month; }));
    var ranged = fw.length && fw.every(function (p) { return p.lo !== null && p.hi !== null; });
    var L = 56, Rr = W - 12;
    var lg = legend([{ mark: MK.line('ln ln-s'), label: 'actual' }, { mark: MK.line('ln ln-a ln-dash'), label: 'forecast' }].concat(ranged ? [{ mark: MK.box('fan'), label: lvl + '% range, each month on its own' }] : []), L, Rr, 10);
    var T0 = lg.h + 14, H = T0 + Math.max(180, Math.min(280, Math.round(W * 0.38))) + 30, Bt = H - 28;
    var vals = hist.map(function (p) { return p.actual; });
    fw.forEach(function (p) { vals.push(p.value, p.lo, p.hi); });
    var dom = domain(vals, true), x = U.scale(0, Math.max(1, months.length - 1), L + 4, Rr - 4), y = U.scale(dom[0], dom[1], Bt, T0);
    var b = yAxis(y, dom, L, Rr), off = hist.length - 1, last = hist[off];
    if (ranged && last) {
      var top = [[x(off), y(last.actual)]].concat(fw.map(function (p, i) { return [x(off + 1 + i), y(p.hi)]; }));
      var bot = fw.map(function (p, i) { return [x(off + 1 + i), y(p.lo)]; }).reverse();
      b += '<path class="fan" d="' + U.path(top) + 'L' + bot.map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join('L') + 'Z"/>';
    }
    b += '<path class="ln ln-s" d="' + U.path(hist.map(function (p, i) { return [x(i), y(p.actual)]; })) + '"/>';
    if (fw.length && last) b += '<path class="ln ln-a ln-dash" d="' + U.path([[x(off), y(last.actual)]].concat(fw.map(function (p, i) { return [x(off + 1 + i), y(p.value)]; }))) + '"/>';
    var rad = Math.max(2, Math.min(3.6, (x(1) - x(0)) / 2.4));
    fw.forEach(function (p, i) {
      b += '<circle class="dot-a" cx="' + x(off + 1 + i).toFixed(1) + '" cy="' + y(p.value).toFixed(1) + '" r="' + rad.toFixed(1) + '"' +
        tip(mon(p.month) + ': forecast ' + vf(p.value) + (p.lo !== null && p.hi !== null ? ', ' + lvl + '% range ' + vf(p.lo) + ' to ' + vf(p.hi) : '')) + '/>';
    });
    if (last) b += '<line x1="' + x(off).toFixed(1) + '" x2="' + x(off).toFixed(1) + '" y1="' + T0 + '" y2="' + Bt + '" stroke="var(--axis)" stroke-dasharray="2 3"/>';
    b += '<line x1="' + L + '" x2="' + Rr + '" y1="' + Bt + '" y2="' + Bt + '" stroke="var(--axis)"/>' + xMonths(months, x, H - 8) + lg.svg;
    var rows = hist.map(function (p) { return [p.month, vf(p.actual), '', '', '', '']; }).concat(fw.map(function (p) {
      return [p.month, '', vf(p.value), vf(p.lo), vf(p.hi), p.widened_by && p.widened_by > 1.0005 ? 'by ' + ((p.widened_by - 1) * 100).toFixed(0) + '%' : 'no'];
    }));
    var F = (R && R.forecast) || {}, band = F.band || {}, cov = (((R || {}).engine || {}).benchmark || {}).forecast_coverage_80;
    var s = 'About ' + lvl + '% of months land in ranges built this way, on average across businesses like this' +
      (cov && cov.steady && cov.momentum ? ': measured on simulated series, ' + share1(cov.steady.lo) + ' to ' + share1(cov.steady.hi) + ' for steady series and ' + share1(cov.momentum.lo) + ' to ' + share1(cov.momentum.hi) + ' for series with momentum.'
        : '; the measured rate is not quoted for this run.');
    if (band.n_errors && band.conditional_coverage_p10_p90 && band.conditional_coverage_p10_p90[0] !== null) s += ' With only ' + num(band.n_errors, 0) + ' past errors behind it, this particular range could be somewhat wider or narrower in truth: typically between ' +
      share0(band.conditional_coverage_p10_p90[0]) + ' and ' + share0(band.conditional_coverage_p10_p90[1]) + ' of months.';
    s += ' The range is for each month on its own, not for the whole path.';
    if (fw.length && fw[0].lo !== null) s += ' Plan for either end: for ' + mon(fw[0].month) + ' the low end of the range is ' + num(fw[0].lo, 0) + ' and the high end ' + num(fw[0].hi, 0) + ', around a forecast of ' + num(fw[0].value, 0) + '.';
    return {
      svg: U.svg(W, H, ch.title + ': ' + hist.length + ' months of actuals to ' + (last ? mon(last.month) : '') + ', then ' + fw.length + ' months forecast' + (ranged ? ' with the ' + lvl + '% range' : ''), b),
      table: { cols: ['Month', 'Actual', 'Forecast', lvl + '% low', lvl + '% high', 'Range widened'], rows: rows },
      note: esc(s)
    };
  };

  /* ------------------------------------------------------------ #5 backtest replay */
  C.replay = function (W, ch) {
    var d = ch.data, ms = d.months || [], vf = vfmt(d.scale);
    var L = 56, R = W - 12;
    var lg = legend([{ mark: MK.box('nl2-rbar'), label: 'range stated at the time' }, { mark: MK.line('ln nl2-tick'), label: 'forecast' },
      { mark: MK.dot('dot-s'), label: 'actual, inside' }, { mark: function (x, y) { return '<path class="nl2-miss" d="M' + (x + 5) + ' ' + (y - 4) + 'l8 8m0 -8l-8 8"/>'; }, label: 'actual, a miss' }], L, R, 10);
    var T0 = lg.h + 14, H = T0 + Math.max(150, Math.min(230, Math.round(W * 0.3))) + 30, Bt = H - 28;
    var vals = [];
    ms.forEach(function (m) { vals.push(m.actual, m.point, m.lo, m.hi); });
    var dom = domain(vals, true), x = U.scale(0, Math.max(1, ms.length - 1), L + 14, R - 14), y = U.scale(dom[0], dom[1], Bt, T0);
    var b = yAxis(y, dom, L, R), bw = Math.max(4, Math.min(22, ((x(1) - x(0)) || 30) * 0.55));
    ms.forEach(function (m, i) {
      var cx = x(i);
      if (m.lo !== null && m.hi !== null) b += '<rect class="nl2-rbar" x="' + (cx - bw / 2).toFixed(1) + '" y="' + y(m.hi).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1, y(m.lo) - y(m.hi)).toFixed(1) + '"/>';
      if (m.point !== null) b += '<path class="ln nl2-tick" d="M' + (cx - bw / 2).toFixed(1) + ' ' + y(m.point).toFixed(1) + 'h' + bw.toFixed(1) + '"/>';
      var t = mon(m.month) + ': actual ' + vf(m.actual) + ', forecast ' + vf(m.point) + ', range ' + vf(m.lo) + ' to ' + vf(m.hi) + (m.in_band ? ', inside' : ', a miss');
      if (m.in_band) b += '<circle class="dot-s" cx="' + cx.toFixed(1) + '" cy="' + y(m.actual).toFixed(1) + '" r="3.6"' + tip(t) + '/>';
      else b += '<path class="nl2-miss" d="M' + (cx - 5).toFixed(1) + ' ' + (y(m.actual) - 5).toFixed(1) + 'l10 10m0 -10l-10 10"' + tip(t) + '/>';
    });
    b += '<line x1="' + L + '" x2="' + R + '" y1="' + Bt + '" y2="' + Bt + '" stroke="var(--axis)"/>' + xMonths(ms.map(function (m) { return m.month; }), x, H - 8) + lg.svg;
    return {
      svg: U.svg(W, H, ch.title + ': ' + num(d.hits, 0) + ' of ' + num(d.n, 0) + ' replayed months landed inside the range stated at the time', b),
      table: { cols: ['Month', 'Actual', 'Forecast', 'Low', 'High', 'Inside the range?'], rows: ms.map(function (m) { return [m.month, vf(m.actual), vf(m.point), vf(m.lo), vf(m.hi), m.in_band ? 'yes' : 'no, a miss']; }) },
      note: 'Each month was forecast one month ahead from what was known then: <b>' + esc(num(d.hits, 0) + ' of ' + num(d.n, 0)) + '</b> actuals landed inside the range stated at the time. A replay this short can show a badly set range; it cannot prove a good one.'
    };
  };

  /* ------------------------------------------------------------ #6 model comparison (a table) */
  C.table_models = function (W, ch) {
    var rows = ch.data.rows || [];
    var h = '<div class="tscroll" tabindex="0" role="region" aria-label="' + esc(ch.title) + '"><table class="dt nl2-models"><thead><tr><th scope="col">Method</th><th scope="col" class="num">MASE</th><th scope="col" class="num">MAPE</th>' +
      '<th scope="col" class="num">MSIS</th><th scope="col" class="num">Skill against seasonal-naive</th><th scope="col">Range held (replay)</th><th scope="col">Against seasonal-naive (DM)</th></tr></thead><tbody>';
    rows.forEach(function (m) {
      var tags = (m.champion ? ' <span class="nl2-tag">champion</span>' : '') + (m.baseline ? ' <span class="nl2-tag">baseline</span>' : '') + (m.applicable === false ? ' <span class="nl2-tag">not applicable</span>' : '');
      h += '<tr' + (m.champion ? ' class="nl2-champ"' : '') + '><th scope="row">' + esc(m.label || m.name) + tags + '</th><td class="num">' + (m.mase === null ? 'n/a' : Number(m.mase).toFixed(2)) + '</td>' +
        '<td class="num">' + (m.mape_suppressed ? 'not shown' : pct1(m.mape)) + '</td><td class="num">' + (m.msis === null ? 'n/a' : Number(m.msis).toFixed(2)) + '</td>' +
        '<td class="num">' + (m.skill_vs_sn === null || m.skill_vs_sn === undefined ? 'n/a' : Number(m.skill_vs_sn).toFixed(2)) + '</td>' +
        '<td>' + (m.coverage ? num(m.coverage.hits, 0) + ' of ' + num(m.coverage.n, 0) : 'n/a') + '</td>' +
        '<td>' + (m.dm ? 'DM ' + Number(m.dm.stat).toFixed(2) + ', p ' + pval(m.dm.p) + ', ' + num(m.dm.n, 0) + ' months' : 'n/a') + '</td></tr>';
    });
    h += '</tbody></table></div>';
    return { html: h, note: 'Ordered by MASE (the replay\'s typical miss against the naive rule\'s; under 1 beats it); MAPE second. The range and the DM test are measured for the champion\'s whole method only, its model choice at each replayed month included.' };
  };

  /* ------------------------------------------------------------ #12 before and after cleaning */
  C.stacked_bars = function (W, ch) {
    var d = ch.data, M = d.months || [], K = d.kept || [], S = d.set_aside || [];
    var L = 50, R = W - 12;
    var lg = legend([{ mark: MK.box('nl2-kept'), label: 'kept' }, { mark: MK.box('nl2-aside'), label: 'set aside' }], L, R, 10);
    var T0 = lg.h + 12, H = T0 + Math.max(150, Math.min(230, Math.round(W * 0.3))) + 30, Bt = H - 28;
    var tot = M.map(function (m, i) { return (K[i] || 0) + (S[i] || 0); });
    var dom = [0, Math.max.apply(null, tot.concat([1])) * 1.08], x = U.scale(0, Math.max(1, M.length - 1), L + 6, R - 6), y = U.scale(dom[0], dom[1], Bt, T0);
    var b = yAxis(y, dom, L, R), bw = Math.max(1.5, Math.min(18, ((x(1) - x(0)) || 20) * 0.72));
    M.forEach(function (m, i) {
      var cx = x(i) - bw / 2, t = mon(m) + ': ' + num(K[i], 0) + ' kept, ' + num(S[i], 0) + ' set aside';
      b += '<g' + tip(t) + '><rect class="nl2-kept" x="' + cx.toFixed(1) + '" y="' + y(K[i]).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + (Bt - y(K[i])).toFixed(1) + '"/>';
      if (S[i]) b += '<rect class="nl2-aside" x="' + cx.toFixed(1) + '" y="' + y(K[i] + S[i]).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1.2, y(K[i]) - y(K[i] + S[i])).toFixed(1) + '"/>';
      b += '</g>';
    });
    b += '<line x1="' + L + '" x2="' + R + '" y1="' + Bt + '" y2="' + Bt + '" stroke="var(--axis)"/>' + xMonths(M, x, H - 8) + lg.svg;
    var sumK = K.reduce(function (a, v) { return a + v; }, 0), sumS = S.reduce(function (a, v) { return a + v; }, 0);
    var fm = d.date_formats || [];
    return {
      svg: U.svg(W, H, ch.title + ': ' + num(sumK, 0) + ' dated rows kept and ' + num(sumS, 0) + ' set aside across ' + M.length + ' months', b),
      table: { cols: ['Month', 'Rows landed', 'Kept', 'Set aside'], rows: M.map(function (m, i) { return [m, num(d.landed ? d.landed[i] : tot[i], 0), num(K[i], 0), num(S[i], 0)]; })
        .concat(d.undated_set_aside || d.undated_kept ? [['no readable date', '', num(d.undated_kept || 0, 0), num(d.undated_set_aside || 0, 0)]] : []) },
      note: 'Every row, month by month: kept, or set aside with a reason (the set-aside download lists each). ' + (d.undated_set_aside ? esc(num(d.undated_set_aside, 0)) + (d.undated_set_aside === 1 ? ' set-aside row had no readable date, so it sits in no month. ' : ' set-aside rows had no readable date, so they sit in no month. ') : '') +
        (fm.length ? '<details class="more nl2-fmts"><summary>The ' + fm.length + ' date formats the cleaner reads</summary><p class="note">' + fm.map(function (f) { return '<code>' + esc(f) + '</code>'; }).join(' ') + '</p></details>' : '')
    };
  };

  /* ------------------------------------------------------------ heat grids (#3, #8, #9, #11) */
  // spec: rows[], cols[], at(i, j) -> {v, text, tier (1..3) or null for an empty cell, tone, tip}; rowHead, colHead;
  // the grid is drawn wide (columns across) when every cell fits its number and glyph, else transposed
  var GLYPH = { 1: '○', 2: '◐', 3: '●' };
  function cellNeed(spec) {
    var maxTxt = 1;
    spec.rows.forEach(function (r, i) { spec.cols.forEach(function (c, j) { var a = spec.at(i, j); if (a && a.text) maxTxt = Math.max(maxTxt, a.text.length); }); });
    return Math.max(30, maxTxt * 6.6 + 22);
  }
  function labW(list, max) { return Math.min(max || 170, Math.max(44, 12 + Math.max.apply(null, list.map(function (s) { return String(s).length; }).concat([1])) * 6.3)); }
  function grid(W, spec) {
    var need = cellNeed(spec);
    var lab = function (list) { return labW(list, spec.labMax); };
    var wide = { rl: lab(spec.rows), n: spec.cols.length }, tall = { rl: lab(spec.cols), n: spec.rows.length };
    wide.cw = (W - wide.rl - 6) / wide.n; tall.cw = (W - tall.rl - 6) / tall.n;
    var T = wide.cw >= need || (wide.cw >= tall.cw && tall.cw < need) ? false : true;
    if (spec.force === 'wide') T = false;
    var rows = T ? spec.cols : spec.rows, cols = T ? spec.rows : spec.cols, o = T ? tall : wide;
    var at = T ? function (i, j) { return spec.at(j, i); } : spec.at;
    var colLab = function (s) { return spec.colShort ? spec.colShort(s, T) : String(s); };
    var cw = Math.min(o.cw, 96), rh = spec.rowH || 24, head = 22, top = spec.top || 0, gx = o.rl + 4;
    var H = top + head + rows.length * rh + 4, b = spec.pre || '';
    // month headers may thin out (the sequence reads on); a category header never does: it is clipped instead
    var monthly = cols.every(function (c) { return /^(\d{4}-\d{2}|[A-Z][a-z]{2}( \d{2})?)$/.test(colLab(c)); });
    var hdrEvery = monthly ? Math.max(1, Math.ceil(cols.map(colLab).reduce(function (a, s) { return Math.max(a, s.length); }, 1) * 6.4 / cw)) : 1;
    cols.forEach(function (c, j) { if (j % hdrEvery === 0) b += U.txt(gx + j * cw + cw / 2, top + head - 7, U.clip(colLab(c), cw * hdrEvery + 4), 'lab', 'middle'); });
    rows.forEach(function (r, i) {
      var yy = top + head + i * rh;
      b += U.txt(o.rl - 2, yy + rh / 2 + 4, U.clip(T ? colLab(r) : String(r), o.rl - 8), 'lab', 'end');
      cols.forEach(function (c, j) {
        var a = at(i, j), xx = gx + j * cw;
        if (a && a.skip) return;
        if (!a || a.tier === null || a.tier === undefined) {
          b += '<g class="hc2-null"' + (a && a.tip ? tip(a.tip) : '') + '><rect x="' + (xx + 1).toFixed(1) + '" y="' + (yy + 1) + '" width="' + (cw - 2).toFixed(1) + '" height="' + (rh - 2) + '" rx="2"/>' +
            U.txt(xx + cw / 2, yy + rh / 2 + 4, a && a.text ? a.text : '', 'lab', 'middle') + '</g>';
          return;
        }
        var g = GLYPH[a.tier] || '', gw = 11, tx = xx + cw / 2 - gw / 2;
        b += '<g class="hc2"' + tip(a.tip) + '><rect class="' + a.tone + a.tier + '" x="' + (xx + 1).toFixed(1) + '" y="' + (yy + 1) + '" width="' + (cw - 2).toFixed(1) + '" height="' + (rh - 2) + '" rx="2"/>' +
          '<text class="cv2" x="' + tx.toFixed(1) + '" y="' + (yy + rh / 2 + 4) + '" text-anchor="middle">' + esc(a.text) + '</text>' +
          '<text class="gl2" x="' + (tx + (a.text.length * 6.6) / 2 + 3 + gw / 2).toFixed(1) + '" y="' + (yy + rh / 2 + 4) + '" text-anchor="middle" aria-hidden="true">' + g + '</text></g>';
      });
    });
    return { svg: b, h: H, w: gx + cols.length * cw + 2, transposed: T };
  }
  // rows x months: across when every month fits, else one block of 12 months per year when a year fits,
  // else months down the side; spec: rows[], months[] ('YYYY-MM'), at(i, j) over the month index
  function monthGrid(W, spec) {
    var probe = { rows: spec.rows, cols: spec.months, at: spec.at }, need = cellNeed(probe), rl = labW(spec.rows);
    var years = [];
    spec.months.forEach(function (m) { var y = m.slice(0, 4); if (years.indexOf(y) < 0) years.push(y); });
    if ((W - rl - 6) / spec.months.length >= need || years.length < 2 || (W - rl - 6) / 12 < need) {
      return grid(W, { rows: spec.rows, cols: spec.months, at: spec.at, colShort: function (m, T) { return T ? m : monShort(m); } });
    }
    var idx = {}, MN = U.MON || ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'], out = '', y = 0;
    spec.months.forEach(function (m, j) { idx[m] = j; });
    years.forEach(function (yr) {
      var g = grid(W, { rows: spec.rows, cols: MN, force: 'wide', top: 16, at: function (i, k) {
        var j = idx[yr + '-' + (k < 9 ? '0' : '') + (k + 1)];
        return j === undefined ? { skip: true } : spec.at(i, j);
      } });
      out += '<g transform="translate(0,' + y + ')">' + U.txt(4, 12, yr, 'lab nl2-yr') + g.svg + '</g>';
      y += g.h + 8;
    });
    return { svg: out, h: y, blocks: years.length };
  }
  // thirds of the range of the values present: tier 1 lowest third, 3 highest
  function thirds(vals) {
    var v = vals.filter(function (x) { return x !== null && x !== undefined && isFinite(x); });
    var lo = v.length ? Math.min.apply(null, v) : 0, hi = v.length ? Math.max.apply(null, v) : 0, w = (hi - lo) / 3;
    var f = function (x) { if (x === null || x === undefined || !isFinite(x)) return null; if (hi === lo) return 2; return Math.min(3, 1 + Math.floor((x - lo) / w + 1e-9)); };
    f.cuts = [lo, lo + w, lo + 2 * w, hi];
    return f;
  }
  function thirdsLegend(f, isCount, what) {
    var c = f.cuts, g = function (a, b) { return cell(a, isCount) + ' to ' + cell(b, isCount); };
    if (c[0] === c[3]) return '<p class="note nl2-hlegend">Every cell is ' + esc(cell(c[0], isCount)) + ' ' + esc(what) + '.</p>';
    return '<p class="note nl2-hlegend"><span aria-hidden="true">○</span> lowest third (' + esc(g(c[0], c[1])) + '), <span aria-hidden="true">◐</span> middle (' + esc(g(c[1], c[2])) + '), <span aria-hidden="true">●</span> highest (' + esc(g(c[2], c[3])) + ') ' + esc(what) + '; darker is more. The number is printed in every cell.</p>';
  }

  C.season = function (W, ch) {
    var d = ch.data, Y = d.years || [], Mo = d.months || [], V = d.values || [], N = d.n || [], isCount = d.value === 'rows';
    var all = [];
    V.forEach(function (r) { r.forEach(function (v) { all.push(v); }); });
    var f = thirds(all), MN = U.MON || [];
    var g = grid(W, { rows: Y, cols: Mo.map(function (m) { return MN[m - 1] || String(m); }), at: function (i, j) {
      var v = V[i][j];
      if (v === null || v === undefined) return { tier: null, text: '', tip: MN[Mo[j] - 1] + ' ' + Y[i] + ': no rows in the window' };
      return { v: v, text: cell(v, isCount), tier: f(v), tone: 'q', tip: MN[Mo[j] - 1] + ' ' + Y[i] + ': ' + num(v) + (isCount ? ' rows' : ' (' + num(N[i][j], 0) + ' rows)') };
    } });
    var rows = [];
    Y.forEach(function (yr, i) { Mo.forEach(function (m, j) { if (V[i][j] !== null && V[i][j] !== undefined) rows.push([yr + '-' + (m < 10 ? '0' : '') + m, num(V[i][j]), num(N[i][j], 0)]); }); });
    return {
      svg: U.svg(W, g.h, ch.title + ': ' + Y.length + ' years by 12 months, one value in each cell', g.svg),
      table: { cols: ['Month', isCount ? 'Rows' : 'Average (' + d.value + ')', 'Rows'], rows: rows },
      note: thirdsLegend(f, isCount, isCount ? 'rows a month' : 'as the monthly average') + '<p class="note">Read down a column to compare the same month across years: a month that is high every year is seasonal, not a change.</p>'
    };
  };

  C.catmonth = function (W, ch) {
    var d = ch.data, M = d.months || [], Ks = d.categories || [], Cn = d.counts || [];
    var all = [];
    Cn.forEach(function (r) { r.forEach(function (v) { all.push(v); }); });
    var f = thirds(all);
    var g = monthGrid(W, { rows: Ks, months: M, at: function (i, j) {
      var v = Cn[i][j];
      return { v: v, text: num(v, 0), tier: f(v), tone: 'q', tip: Ks[i] + ', ' + mon(M[j]) + ': ' + num(v, 0) + ' rows' };
    } });
    return {
      svg: U.svg(W, g.h, ch.title + ': ' + Ks.length + ' categories by ' + M.length + ' months, the row count in each cell', g.svg),
      table: { cols: ['Month'].concat(Ks), rows: M.map(function (m, j) { return [m].concat(Ks.map(function (k, i) { return num(Cn[i][j], 0); })); }) },
      note: thirdsLegend(f, true, 'rows') + '<p class="note">Counts of kept rows in the analysis window. Drivers and reversals (which category moved a change) are not computed in this release, so this map flags none.</p>'
    };
  };

  C.missingness = function (W, ch, R) {
    var d = ch.data, cols = d.columns || [], M = d.months || [], rows = d.rows || [], nul = d.nulls || [];
    var keep = [], none = [];
    cols.forEach(function (c, i) { (nul[i] || []).some(function (v) { return v > 0; }) ? keep.push(i) : none.push(c); });
    var share = function (i, j) { return rows[j] ? 100 * nul[i][j] / rows[j] : null; };   // percent of the month's rows
    var all = [];
    keep.forEach(function (i) { M.forEach(function (m, j) { all.push(share(i, j)); }); });
    var f = thirds(all);
    var g = monthGrid(W, { rows: keep.map(function (i) { return cols[i]; }), months: M, at: function (a, j) {
      var i = keep[a], k = nul[i][j];
      return { v: k, text: num(k, 0), tier: f(share(i, j)), tone: 'm', tip: cols[i] + ', ' + mon(M[j]) + ': ' + num(k, 0) + ' empty of ' + num(rows[j], 0) + ' rows' };
    } });
    var nc = (((R || {}).health || {}).missingness || {}).nullity_corr || {}, mcar = (((R || {}).health || {}).missingness || {}).mcar || {};
    var extra = '';
    if (nc.columns && nc.columns.length >= 2) {
      extra += '<p class="note">Columns empty in the same rows (nullity correlation, ' + esc(num(nc.n, 0)) + ' rows): ' + nc.columns.map(function (a, i) {
        return nc.columns.slice(i + 1).map(function (b2, j) { var r = nc.matrix[i][i + 1 + j]; return esc(a) + ' with ' + esc(b2) + ' ' + (r === null ? 'n/a' : Number(r).toFixed(2)); }).join('; ');
      }).filter(Boolean).join('; ') + '.</p>';
    }
    extra += '<p class="note">Whether cells are missing at random (Little\'s test): ' + esc(mcar.conclusion || 'not run in this release') + '.</p>';
    return {
      svg: U.svg(W, g.h, ch.title + ': empty cells for ' + keep.length + ' columns across ' + M.length + ' months', g.svg),
      table: { cols: ['Month', 'Rows'].concat(cols), rows: M.map(function (m, j) { return [m, num(rows[j], 0)].concat(cols.map(function (c, i) { return num(nul[i][j], 0); })); }) },
      note: thirdsLegend(f, false, 'percent of the month\'s rows empty') +
        '<p class="note">The number is the empty cells that month; the shade is their share of the month\'s rows. ' + (none.length ? 'Columns with no empty cell in the window are left out: ' + none.map(esc).join(', ') + '.' : '') + '</p>' + extra
    };
  };

  C.corr = function (W, ch) {
    var d = ch.data, Ms = d.measures || [], Rm = d.r || [], Nm = d.n || [];
    var g = grid(W, { rows: Ms, cols: Ms, force: Ms.length <= 6 ? 'wide' : null, labMax: Math.max(64, W * 0.3), rowH: 28, at: function (i, j) {
      var r = Rm[i][j];
      if (r === null || r === undefined) return { tier: null, text: 'n/a', tip: Ms[i] + ' with ' + Ms[j] + ': not computed' };
      var a = Math.abs(r), t = a >= 0.5 ? 3 : a >= 0.3 ? 2 : 1;
      return { v: r, text: (r < 0 ? '-' : '') + Math.abs(r).toFixed(2), tier: t, tone: r < 0 ? 'cn' : 'cp', tip: Ms[i] + ' with ' + Ms[j] + ': r ' + Number(r).toFixed(2) + ', ' + num(Nm[i][j], 0) + ' rows' };
    } });
    var rows = [];
    Ms.forEach(function (a, i) { Ms.forEach(function (b2, j) { if (j > i) rows.push([a + ' with ' + b2, Rm[i][j] === null ? 'n/a' : Number(Rm[i][j]).toFixed(3), num(Nm[i][j], 0)]); }); });
    return {
      svg: U.svg(W, g.h, ch.title + ': Pearson r for each pair of ' + Ms.length + ' measures', g.svg),
      table: { cols: ['Pair', 'r', 'Rows (n)'], rows: rows },
      note: '<p class="note nl2-hlegend">Blue: they move in opposite directions; orange: together. <span aria-hidden="true">○</span> |r| under 0.3, <span aria-hidden="true">◐</span> 0.3 to 0.5, <span aria-hidden="true">●</span> 0.5 or more. ' +
        esc(d.method || 'Pearson') + '; each pair uses the rows where both are filled (n in the table view, ' + esc(num(Nm.length ? Nm[0][0] : null, 0)) + ' rows on the diagonal).</p>' +
        '<p class="note"><b>Association, not cause.</b> This map is descriptive: it supports no finding, and two measures that move together can both follow a third thing.</p>'
    };
  };

  /* ------------------------------------------------------------ #7 ranked bars */
  C.ranked_bars = function (W, ch) {
    var d = ch.data, bars = (d.bars || []).map(function (b2) { return { label: b2.label, rows: b2.rows, share: b2.share_pct, fact: b2.fact_id, main: true }; });
    if (d.other) bars.push({ label: 'all other values', rows: d.other, share: d.total ? 100 * d.other / d.total : null });
    if (d.missing) bars.push({ label: '(empty)', rows: d.missing, share: d.total ? 100 * d.missing / d.total : null });
    var lw = Math.min(Math.max(90, W * 0.34), 190), vw = 96, L = lw + 6, R = W - vw, rh = 26, H = bars.length * rh + 8;
    var mx = Math.max.apply(null, bars.map(function (b2) { return b2.rows; }).concat([1])), x = U.scale(0, mx, L, R);
    var b = '';
    bars.forEach(function (b2, i) {
      var yy = 4 + i * rh, t = b2.label + ': ' + num(b2.rows, 0) + ' rows' + (b2.share === null ? '' : ', ' + pct1(b2.share));
      b += '<g' + tip(t) + '>' + U.txt(lw, yy + rh / 2 + 4, U.clip(String(b2.label), lw - 4), 'lab', 'end') +
        '<rect class="' + (b2.main ? 'bar-s' : 'bar-muted') + '" x="' + L + '" y="' + (yy + 4) + '" width="' + Math.max(1, x(b2.rows) - L).toFixed(1) + '" height="' + (rh - 8) + '" rx="2"/>' +
        '<text class="vlab" x="' + (x(b2.rows) + 5).toFixed(1) + '" y="' + (yy + rh / 2 + 4) + '">' + esc(num(b2.rows, 0) + ' · ' + pct1(b2.share)) + '</text></g>';
    });
    return {
      svg: U.svg(W, H, ch.title + ': the top ' + (d.bars || []).length + ' values by rows, then the rest', b),
      table: { cols: ['Value', 'Rows', 'Share of rows', 'Evidence fact'], rows: bars.map(function (b2) { return [b2.label, num(b2.rows, 0), pct1(b2.share), b2.fact || '']; }) },
      note: 'Rows in the analysis window, ' + esc(num(d.total, 0)) + ' in all. The top five shares are the engine\'s share facts in the evidence ledger.'
    };
  };

  /* ------------------------------------------------------------ #10 distributions, before and after */
  C.histogram_windows = function (W, ch) {
    var d = ch.data, E = d.edges || [], P = d.prior || { values: [], counts: [] }, Q = d.latest || { values: [], counts: [] }, vf = vfmt(d.scale);
    var L = 44, R = W - 12;
    var lg = legend([{ mark: MK.box('bar-s'), label: 'prior 12 months' }, { mark: MK.box('bar-a'), label: 'latest 12 months' }], L, R, 10);
    var T0 = lg.h + 14, plotH = Math.max(130, Math.min(200, Math.round(W * 0.26))), Bt = T0 + plotH, H = Bt + 70;
    var k = Math.max(1, E.length - 1), mx = Math.max.apply(null, P.counts.concat(Q.counts, [1]));
    var x = U.scale(E[0], E[E.length - 1], L + 6, R - 6), y = U.scale(0, mx * 1.15, Bt, T0), b = '';
    U.ticks(0, mx * 1.15, Math.min(4, mx)).forEach(function (t) { if (Math.floor(t) === t) b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + y(t).toFixed(1) + '" y2="' + y(t).toFixed(1) + '"/>' + U.txt(L - 6, y(t) + 4, num(t, 0), 'lab', 'end'); });
    for (var i = 0; i < k; i++) {
      var x0 = x(E[i]), x1 = x(E[i + 1]), w = (x1 - x0) * 0.4, rng = vf(E[i]) + ' to ' + vf(E[i + 1]);
      [[P.counts[i], 'bar-s', x0 + (x1 - x0) * 0.1, 'prior'], [Q.counts[i], 'bar-a', x0 + (x1 - x0) * 0.5, 'latest']].forEach(function (s) {
        var c = s[0] || 0;
        b += '<g' + tip(rng + ': ' + num(c, 0) + ' of the ' + s[3] + ' months') + '><rect class="' + s[1] + '" x="' + s[2].toFixed(1) + '" y="' + y(c).toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + Math.max(0, Bt - y(c)).toFixed(1) + '"/>' +
          (c ? '<text class="vlab" x="' + (s[2] + w / 2).toFixed(1) + '" y="' + (y(c) - 4).toFixed(1) + '" text-anchor="middle">' + c + '</text>' : '') + '</g>';
      });
    }
    b += '<line x1="' + L + '" x2="' + R + '" y1="' + Bt + '" y2="' + Bt + '" stroke="var(--axis)"/>';
    var every = Math.max(1, Math.ceil(E.length * 48 / (R - L))), bw0 = E.length > 1 ? Math.abs(E[1] - E[0]) : 1;
    // bin edges get the decimals their width needs, so neighbouring edges never print the same
    var edp = bw0 >= 10 ? 0 : Math.min(4, Math.max(0, Math.ceil(-Math.log(bw0) / Math.LN10 - 1e-9) + 1));
    var edge = function (e) { return Math.abs(e) >= 1e4 ? axis(e) : num(e, edp); };
    E.forEach(function (e, i) { if (i % every === 0 || i === E.length - 1) b += U.txt(x(e), Bt + 15, edge(e), 'lab', 'middle'); });
    // every monthly value, as a rug under the bars: the months behind each bar
    [[P.values, 'dot-s', Bt + 34, 'prior'], [Q.values, 'dot-a', Bt + 52, 'latest']].forEach(function (s) {
      b += U.txt(L - 6, s[2] + 4, s[3], 'lab', 'end');
      (s[0] || []).forEach(function (v) { b += '<circle class="' + s[1] + ' nl2-rug" cx="' + x(v).toFixed(1) + '" cy="' + s[2] + '" r="3"' + tip(s[3] + ' month: ' + vf(v)) + '/>'; });
    });
    b += lg.svg;
    var rows = [];
    for (var j = 0; j < k; j++) rows.push([vf(E[j]) + ' to ' + vf(E[j + 1]), num(P.counts[j] || 0, 0), num(Q.counts[j] || 0, 0)]);
    return {
      svg: U.svg(W, H, ch.title + ': monthly values of the two compared windows, binned', b),
      table: { cols: ['Monthly value', 'Prior months', 'Latest months'], rows: rows },
      tnote: '<p class="note">Prior months: ' + P.values.map(function (v) { return esc(vf(v)); }).join(', ') + '. Latest months: ' + Q.values.map(function (v) { return esc(vf(v)); }).join(', ') + '.</p>',
      note: 'Each bar counts months, not rows: ' + esc(num(P.values.length, 0)) + ' monthly values in the prior window and ' + esc(num(Q.values.length, 0)) + ' in the latest. The dots under the bars are every one of them.'
    };
  };

  /* ------------------------------------------------------------ #14 benchmark strip */
  // what one benchmark condition simulated, in words: its truth (no change, or exactly the bar)
  function truthOf(z) { return z.at_bar ? 'a true change of exactly the ' + num(z.shift_pct, 0) + '% bar' : 'no change'; }
  C.cellWords = function (z) {
    return num(z.n, 0) + ' months, noise ' + num(z.cv_pct, 0) + '%, momentum ' + num(z.phi, 2) + (z.level && z.level < 1000 ? ', ' + num(z.level, 0) + ' rows a month' : '') + (z.white_sd ? ', with independent noise' : '') + (z.seasonal ? ', seasonal' : '');
  };
  C.certWords = function (cert) {
    if (!cert) return '';
    var f = cert.failed || [];
    return 'Release check: ' + num(cert.passed, 0) + ' of ' + num(cert.cells, 0) + ' simulated conditions are certified to confirm fewer than ' + pct1(cert.cap_pct) + ' of series with no change beyond the bar' +
      (f.length ? '; ' + num(f.length, 0) + (f.length === 1 ? ' is' : ' are') + ' not: ' + f.map(function (z) { return C.cellWords(z) + ' (' + pct1(z.rate) + ', ' + num(z.k, 0) + ' of ' + num(z.N, 0) + ')'; }).join('; ') : '') + '.';
  };
  C.benchmark_strip = function (W, ch, R) {
    var d = ch.data;
    if (!d.available) return { html: '<p class="note">' + esc(d.note || 'The engine\'s benchmark is not quoted for this run.') + '</p>' };
    var target = typeof d.target_pct === 'number' ? d.target_pct : null;
    var mLab = d.match === 'close' ? 'Like this file' : 'Nearest simulated condition';
    // a claim held at WATCH by rule has no false-confirm rate: its row is not drawn, the note says why
    // no rate is drawn for a claim held at WATCH by rule, nor for a file no measured condition is like
    var cells = [['matched', mLab, d.routed || d.match === 'none' ? null : d.matched_cell], ['worst', 'Hardest simulated condition', d.worst_cell]].filter(function (c) { return c[2]; });
    if (!cells.length && !d.routed) return { html: '<p class="note">' + esc(d.note || 'No tested change claim to match a benchmark condition to.') + '</p>' };
    var gp = (((R || {}).reproducibility || {}).parameters || {}).gate_policy || {}, lvl = typeof gp.recommend_q === 'number' ? gp.recommend_q * 100 : null;
    var lw = Math.min(Math.max(120, W * 0.3), 210), L = lw + 8, Rr = W - 16, rh = 40, top = 14, H = top + Math.max(1, cells.length) * rh + 30;
    var mx = Math.max.apply(null, cells.map(function (c) { return c[2].upper95; }).concat(lvl === null ? [] : [lvl * 1.3], [0.5])) * 1.12;
    var x = U.scale(0, mx, L, Rr), b = '';
    U.ticks(0, mx, 4).forEach(function (t) { b += '<line class="gridl" x1="' + x(t).toFixed(1) + '" x2="' + x(t).toFixed(1) + '" y1="' + top + '" y2="' + (H - 22) + '"/>' + U.txt(x(t), H - 8, num(t, 1) + '%', 'lab', 'middle'); });
    if (lvl !== null) b += '<line class="nl2-ref" x1="' + x(lvl).toFixed(1) + '" x2="' + x(lvl).toFixed(1) + '" y1="' + (top - 6) + '" y2="' + (H - 22) + '"/>' + U.txt(x(lvl) + 4, top + 2, 'the engine\'s ' + num(lvl, 1) + '% aim', 'lab');
    cells.forEach(function (c, i) {
      var z = c[2], yy = top + 8 + i * rh + rh / 2;
      var t = c[1] + ': ' + (z.routed ? 'by rule, nothing is confirmed in this condition (not a measurement)' : num(z.k, 0) + ' of ' + num(z.N, 0) + ' simulated series with ' + truthOf(z) + ' confirmed, ' + pct1(z.rate) + ' (95% interval ' + pct1(z.lower95) + ' to ' + pct1(z.upper95) + ')') +
        (c[0] === 'worst' && z.above_target ? '; above the engine\'s ' + pct1(target) + ' aim: its whole interval is above it' : '');
      b += '<g' + tip(t) + '>' + U.txt(lw, yy - 3, U.clip(c[1], lw - 4), 'lab', 'end') + U.txt(lw, yy + 11, U.clip(c[0] === 'worst' && z.above_target ? 'above the ' + pct1(target) + ' aim' : num(z.n, 0) + ' months, noise ' + num(z.cv_pct, 0) + '%', lw - 4), 'lab', 'end') +
        '<line class="nl2-cil" x1="' + x(z.lower95).toFixed(1) + '" x2="' + x(z.upper95).toFixed(1) + '" y1="' + yy + '" y2="' + yy + '"/>' +
        '<circle class="' + (c[0] === 'matched' ? 'dot-a' : 'dot-3') + '" cx="' + x(z.rate).toFixed(1) + '" cy="' + yy + '" r="5"/>' +
        '<text class="vlab" x="' + (x(z.upper95) + 6).toFixed(1) + '" y="' + (yy + 4) + '">' + esc(pct1(z.rate)) + '</text></g>';
    });
    var cond = function (z) { return C.cellWords(z) + ', ' + truthOf(z); };
    var F0 = d.file, eff = F0 && F0.effective_rows_a_month !== null && F0.effective_rows_a_month !== undefined;
    var fileW = F0 ? 'This file\'s primary claim: ' + num(F0.months, 0) + ' months, noise ' + num(F0.cv_pct, 0) + '%, momentum ' + num(F0.phi, 2) +
      (eff ? ', about ' + num(F0.effective_rows_a_month, 0) + ' effective rows a month (' + num(F0.rows_a_month, 0) + ' rows)' : F0.rows_a_month !== null && F0.rows_a_month !== undefined ? ', about ' + num(F0.rows_a_month, 0) + ' rows a month' : '') + '. ' : '';
    // a monthly total is routed on its effective rows against the totals' line; a count on its rows against the counts' line
    var R0 = d.routed;
    var routedW = !R0 ? '' : R0.kind === 'total'
      ? 'It is held at WATCH by rule: a monthly total of about ' + num(R0.rows_a_month, 0) + ' rows a month whose amounts vary carries the noise of about ' + num(R0.effective_rows_a_month, 0) + ' effective rows (rows divided by 1 + the squared coefficient of variation of the amounts), under the ' + num(R0.min_effective_rows_a_month, 0) + ' below which the benchmark could not certify monthly totals, so no false-confirm rate applies to it. '
      : 'It is held at WATCH by rule: with about ' + num(R0.rows_a_month, 0) + ' rows a month, under ' + num(R0.min_rows_a_month, 0) + ', the engine confirms no change in a count built from so few rows, so no false-confirm rate applies to it. ';
    var unlikeW = !R0 && d.match === 'none' ? 'No measured condition is like this file (' + (F0 && F0.unlike ? F0.unlike : 'its diagnostics are outside the simulated ones') + '), so no rate is quoted for it. ' : '';
    return {
      svg: U.svg(W, H, ch.title + ': ' + cells.map(function (c) { return c[1] + ' ' + pct1(c[2].rate); }).join('; '), b),
      table: { cols: ['Condition', 'Simulated', 'Confirmed', 'Rate', '95% interval'], rows: cells.map(function (c) {
        return [c[1], cond(c[2]) + ' (' + c[2].name + ')', c[2].routed ? 'none, by rule' : num(c[2].k, 0) + ' of ' + num(c[2].N, 0), pct1(c[2].rate), pct1(c[2].lower95) + ' to ' + pct1(c[2].upper95)];
      }) },
      note: esc(fileW + routedW + unlikeW) + 'How often the engine said CONFIRMED on simulated series with no change beyond the bar: in the ' + (d.match === 'close' ? 'condition like this file\'s' : 'simulated condition nearest this file') + ' and in the hardest one; never a pooled rate. It is not the chance that a finding here is real. ' + esc(C.certWords(d.certification))
    };
  };

  C.TYPES = { trend_windows: C.trend_windows, fan: C.fan, replay: C.replay, stacked_bars: C.stacked_bars, ranked_bars: C.ranked_bars,
    histogram_windows: C.histogram_windows, benchmark_strip: C.benchmark_strip };
  // the drawer for a chart record, or null when the page has none for it
  C.drawer = function (ch) {
    var id = String(ch.id || '');
    if (ch.type === 'heatmap') {
      if (id.indexOf('season.') === 0) return C.season;
      if (id.indexOf('catmonth.') === 0) return C.catmonth;
      if (id === 'missingness') return C.missingness;
      if (id === 'corr') return C.corr;
      return null;
    }
    if (ch.type === 'table' && id.indexOf('models.') === 0) return C.table_models;
    return C.TYPES[ch.type] || null;
  };
})();
