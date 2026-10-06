/* The chart registry, page side (tools/fixtures/viz/spec.json, plan/CHART-REGISTRY-DESIGN.md). window.NLV draws
   one viz record by its draw kind (waterfall, heatmap, dot_range, pareto, slope) and draws its table for a kind it
   does not know, or data it cannot read: a record is never dropped. The engine wrote every printed string and every
   colour tier; this file only places them. The only numbers formatted here are axis ticks.
   Colour is never the only channel: a number and a glyph in every heatmap cell, signed texts on every change,
   labelled totals, a marked k80 bar, and a table view for every chart. The accessible name of each chart is its
   title and its summary. Every layout fits the width it is given, down to a 320-px phone: a label that does not
   fit is clipped at a character boundary with an ellipsis and shown whole in its tooltip and in the table view. */
(function () {
  'use strict';
  var U = window.NLU || {};
  var esc = U.esc || function (s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  };
  var NLV = {};
  window.NLV = NLV;
  var KINDS = ['waterfall', 'heatmap', 'dot_range', 'pareto', 'slope', 'table'];
  NLV.kinds = KINDS.slice();
  var GLYPH = { 1: '○', 2: '◐', 3: '●' };
  // the display caps of spec.json (caps): a record over them is not drawn as a chart, its table is shown
  var CAP = { heatRows: 12, heatCols: 24, steps: 14, bars: 20, rows: 12 };

  function obj(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function arr(v) { return Array.isArray(v); }
  function fin(v) { return typeof v === 'number' && isFinite(v); }
  // a record's own key in one of this file's tables, never an Object.prototype name (review of the chart registry,
  // 30 Sep 2026: kind "constructor" found Object as its check and its drawer, and the record's own fields went into the
  // page unescaped; "__proto__" threw)
  var OWN = Object.prototype.hasOwnProperty;
  function has(o, k) { return typeof k === 'string' && OWN.call(o, k); }
  // a value a drawing places: finite and at most 1e300 across, so no scale or tick runs to Infinity or NaN (the same
  // review: a slope from 1e308 to -1e308 drew NaN coordinates). A record past it is shown as its table
  var MAXV = 1e300;
  function pos(v) { return fin(v) && Math.abs(v) <= MAXV; }
  function str(v) { return typeof v === 'string'; }
  function f1(v) { return (Math.round(v * 10) / 10).toFixed(1); }
  function mx(list) { return list.length ? Math.max.apply(null, list) : 0; }

  /* ------------------------------------------------------------ text: measured, clipped, right to left */
  var ctx2d = null, fam = null, memo = {};
  function family() {
    if (fam === null) { try { fam = getComputedStyle(document.body).fontFamily || 'sans-serif'; } catch (e) { fam = 'sans-serif'; } }
    return fam;
  }
  // the width of a string as the page sets it (the canvas measures with the page's own font)
  function tw(s, px, wt) {
    s = String(s === null || s === undefined ? '' : s); px = px || 12; wt = wt || 400;
    var k = px + '|' + wt + '|' + s;
    if (memo[k] !== undefined) return memo[k];
    var w = null;
    try {
      if (!ctx2d) ctx2d = document.createElement('canvas').getContext('2d');
      if (ctx2d) { ctx2d.font = wt + ' ' + px + 'px ' + family(); w = ctx2d.measureText(s).width; }
    } catch (e) { w = null; }
    if (w === null || !isFinite(w) || (w === 0 && s.length)) w = s.length * px * 0.6;
    memo[k] = w;
    return w;
  }
  var seg = null;
  try { if (window.Intl && Intl.Segmenter) seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' }); } catch (e) { seg = null; }
  function graphemes(s) {
    if (seg) { var out = []; var it = seg.segment(s)[Symbol.iterator](), x; while (!(x = it.next()).done) out.push(x.value.segment); return out; }
    return Array.from ? Array.from(s) : s.split('');
  }
  // the longest start of s that fits maxW with an ellipsis, cut between two characters as a reader sees them
  function fit(s, maxW, px, wt) {
    s = String(s === null || s === undefined ? '' : s);
    if (tw(s, px, wt) <= maxW) return s;
    var g = graphemes(s), lo = 0, hi = g.length - 1;
    var at = function (n) { return g.slice(0, n).join('').replace(/\s+$/, '') + '…'; };
    while (lo < hi) { var m = (lo + hi + 1) >> 1; if (tw(at(m), px, wt) <= maxW) lo = m; else hi = m - 1; }
    return lo > 0 ? at(lo) : (tw('…', px, wt) <= maxW ? '…' : '');
  }
  var RTL = /[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc]/;
  var LTR = /[A-Za-z\u00c0-\u02af\u0370-\u052f\u0900-\u0dff\u0e00-\u0fff\u1000-\u109f\u1100-\u11ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/;
  // the direction of a label from its first strong character (dir="auto" in HTML)
  function rtl(s) {
    s = String(s);
    for (var i = 0; i < s.length; i++) { var c = s.charAt(i); if (RTL.test(c)) return true; if (LTR.test(c)) return false; }
    return false;
  }
  // one line of text; o: {anchor, cls, px, wt, maxW}. A clipped label carries the whole text as its tooltip; a
  // right-to-left label is set right to left, its anchor flipped so it lines up where a left-to-right one would
  function txt(x, y, s, o) {
    o = o || {};
    var cls = o.cls || 'lab', full = String(s === null || s === undefined ? '' : s), px = o.px || 12;
    var wt = o.wt || (/\bnlv-b\b/.test(cls) ? 700 : /\b(nlv-axn|vlab|nlv-k80t)\b/.test(cls) ? 650 : 400);
    var t = o.maxW !== undefined ? fit(full, Math.max(0, o.maxW - 1), px, wt) : full;
    var a = o.anchor || 'start', r = rtl(full);
    if (r && a !== 'middle') a = a === 'start' ? 'end' : 'start';
    return '<text x="' + f1(x) + '" y="' + f1(y) + '" class="' + cls + '"' + (a !== 'start' ? ' text-anchor="' + a + '"' : '') + (r ? ' direction="rtl"' : '') +
      (t !== full ? ' data-tip="' + esc(full) + '" data-full="' + esc(full) + '"' : '') + (o.hidden ? ' aria-hidden="true"' : '') + '>' + esc(t) + '</text>';
  }
  // the share of labels that two lines of width w would clip
  function clippedShare(labels, w) {
    return labels.length ? labels.filter(function (l) { var t = two(l, w, 12); return /…$/.test(t[0]) || /…$/.test(t[1]); }).length / labels.length : 0;
  }
  // words of a label broken into at most two lines of width w (the second clipped); a word too long for a line is clipped
  function two(s, w, px, wt) {
    var words = String(s).split(/\s+/).filter(Boolean), a = '', i = 0;
    for (; i < words.length; i++) { var t = a ? a + ' ' + words[i] : words[i]; if (tw(t, px, wt) <= w) a = t; else break; }
    if (!a) return [fit(String(s), w, px, wt), ''];
    var rest = words.slice(i).join(' ');
    return [a, rest ? fit(rest, w, px, wt) : ''];
  }

  /* ------------------------------------------------------------ axes: the only numbers formatted here */
  function tickTexts(ts) {
    var m = mx(ts.map(Math.abs)), d = m >= 1e9 ? 1e9 : m >= 1e6 ? 1e6 : m >= 1e4 ? 1e3 : 1, u = d === 1e9 ? 'B' : d === 1e6 ? 'M' : d === 1e3 ? 'k' : '';
    var step = ts.length > 1 ? Math.abs(ts[1] - ts[0]) / d : 1;
    var dp = !(step > 0) || step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log(step) / Math.LN10 - 1e-9));
    return ts.map(function (t) {
      var x = t / d;
      if (Math.abs(x) < 1e-9) return '0';
      return (x < 0 ? '−' : '') + Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp }) + u;
    });
  }
  function ticks(lo, hi, n) {
    if (!(hi > lo)) { hi = lo + 1; }
    var ts = (U.ticks ? U.ticks(lo, hi, n) : [lo, hi]).filter(function (t) { return t >= lo - 1e-9 && t <= hi + 1e-9; });
    return ts.length ? ts : [lo, hi];
  }
  // a value axis across the bottom: 2 to 5 ticks, each label clear of its neighbours and inside the drawing
  function xAxis(sx, lo, hi, L, R, y0, y1, W) {
    var n = Math.max(2, Math.min(5, Math.floor((R - L) / 72))), ts = ticks(lo, hi, n), lab = tickTexts(ts), b = '', last = -1e9;
    ts.forEach(function (t, i) {
      var x = sx(t), w = tw(lab[i], 12), cx = Math.min(W - w / 2 - 1, Math.max(w / 2 + 1, x));
      b += '<line class="gridl" x1="' + f1(x) + '" x2="' + f1(x) + '" y1="' + f1(y0) + '" y2="' + f1(y1) + '"/>';
      if (cx - w / 2 < last + 8) return;
      b += txt(cx, y1 + 14, lab[i], { anchor: 'middle' });
      last = cx + w / 2;
    });
    return b;
  }
  function yTicks(lo, hi, n) { var ts = ticks(lo, hi, n); return { ts: ts, lab: tickTexts(ts) }; }
  // a key over a chart: items {mark: fn(x, yMid) -> svg, w: the mark's width, text}, left to right from x0, a new line
  // when the next item would pass maxX; returns {svg, h}
  function keyRow(items, x0, maxX) {
    var x = x0, y = 0, out = '';
    items.forEach(function (it) {
      var w = it.w + 5 + tw(it.text, 12);
      if (x + w > maxX && x > x0) { x = x0; y += 18; }
      out += it.mark(x, y + 8) + txt(x + it.w + 5, y + 12, it.text, { maxW: maxX - x - it.w - 5 });
      x += w + 16;
    });
    return { svg: out, h: y + 18 };
  }
  function svgOf(W, H, label, body) {
    return '<svg class="nlv" viewBox="0 0 ' + W + ' ' + Math.ceil(H) + '" width="' + W + '" height="' + Math.ceil(H) + '" role="img" tabindex="0" aria-label="' + esc(label) + '"><title>' +
      esc(label) + '</title>' + body + '</svg>';
  }
  function scale(d0, d1, r0, r1) {
    if (U.scale) return U.scale(d0, d1, r0, r1);
    var k = (r1 - r0) / ((d1 - d0) || 1);
    return function (v) { return r0 + (v - d0) * k; };
  }

  /* ------------------------------------------------------------ the data a kind needs, checked before drawing */
  function tableOk(t) { return obj(t) && arr(t.cols) && t.cols.length > 0 && arr(t.rows) && t.rows.every(function (r) { return arr(r); }); }
  function grid(d, k) { return arr(d[k]) && d[k].length === d.rows.length && d[k].every(function (r) { return arr(r) && r.length === d.cols.length; }); }
  var VALID = {
    waterfall: function (d) {
      return obj(d) && arr(d.steps) && d.steps.length >= 2 && d.steps.length <= CAP.steps && d.steps.every(function (s) {
        return obj(s) && str(s.label) && str(s.text) && pos(s.value) && pos(s.from) && pos(s.to);
      });
    },
    heatmap: function (d) {
      return obj(d) && arr(d.rows) && arr(d.cols) && d.rows.length >= 1 && d.cols.length >= 1 && d.rows.length <= CAP.heatRows && d.cols.length <= CAP.heatCols &&
        d.rows.every(str) && d.cols.every(str) && grid(d, 'values') && grid(d, 'text') && grid(d, 'tier') &&
        d.values.every(function (r) { return r.every(function (v) { return v === null || fin(v); }); }) &&
        d.text.every(function (r) { return r.every(str); }) &&
        d.tier.every(function (r) { return r.every(function (t) { return t === 0 || (t === Math.round(t) && Math.abs(t) <= 3); }); });
    },
    dot_range: function (d) {
      return obj(d) && arr(d.rows) && d.rows.length >= 1 && d.rows.length <= CAP.rows && d.rows.every(function (r) {
        return obj(r) && str(r.label) && pos(r.center) && pos(r.lo) && pos(r.hi) && pos(r.median) && obj(r.texts) &&
          str(r.texts.center) && str(r.texts.lo) && str(r.texts.hi) && str(r.texts.n);
      });
    },
    pareto: function (d) {
      var bar = function (b) { return obj(b) && str(b.label) && pos(b.value) && b.value >= 0 && str(b.text) && fin(b.cum_pct) && str(b.cum_text); };
      return obj(d) && arr(d.bars) && d.bars.length >= 1 && d.bars.length <= CAP.bars && d.bars.every(bar) &&
        (d.other === null || d.other === undefined || bar(d.other)) && obj(d.k80) && fin(d.k80.k) && str(d.k80.text);
    },
    slope: function (d) {
      return obj(d) && arr(d.rows) && d.rows.length >= 1 && d.rows.length <= CAP.rows && str(d.a_label) && str(d.b_label) && d.rows.every(function (r) {
        return obj(r) && str(r.label) && pos(r.a) && pos(r.b) && str(r.a_text) && str(r.b_text) && str(r.change_text);
      });
    }
  };
  // the kind a record is drawn as: its own when this page knows it and can read its data, else 'table'
  NLV.kindOf = function (rec) {
    var k = rec && rec.kind;
    return has(VALID, k) && VALID[k](rec.data) ? k : 'table';
  };
  NLV.knows = function (rec) { return !!rec && KINDS.indexOf(rec.kind) >= 0 && NLV.kindOf(rec) === rec.kind; };
  // the accessible name: the title and the summary
  NLV.label = function (rec) {
    var t = String((rec && rec.title) || 'Chart').replace(/[.\s]+$/, '');
    return rec && rec.summary ? t + '. ' + rec.summary : t;
  };
  // a menu chart's name in the reader's words (the analyst view's "Charts not drawn, and why")
  var NAMES = { contribution_waterfall: 'Contribution waterfall', pvm_waterfall: 'Price, volume and mix waterfall', calendar_heatmap: 'Calendar heatmap (month by year)',
    change_heatmap: 'Change heatmap (segment by month)', crosstab_heatmap: 'Crosstab heatmap', theme_rating_heatmap: 'Themes by rating heatmap',
    correlation_heatmap: 'Correlation heatmap', group_ranges: 'Group ranges', pareto: 'Pareto chart', slope: 'Slope chart' };
  NLV.chartName = function (c) { return has(NAMES, c) ? NAMES[c] : String(c || 'chart').replace(/_/g, ' '); };

  /* ------------------------------------------------------------ heatmap */
  function isYM(s) { return /^\d{4}-(0[1-9]|1[0-2])$/.test(s); }
  function isMonth(s) { return isYM(s) || /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$/.test(s); }
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function heatmap(rec, W) {
    var d = rec.data, div = d.scale === 'diverging', RH = 26, top = 18;
    var state = function (i, j) {
      var v = d.values[i][j], k = d.tier[i][j];
      if (v === null) return d.text[i][j] === '' ? 'empty' : 'sup';
      return k ? 'shown' : 'neutral';
    };
    // a cell needs room for its number and its glyph (spec phone_fit: max(30, 6.6 x its longest text + 22)), and never less
    var longest = 1, wid = 0;
    d.text.forEach(function (r) { r.forEach(function (t) { longest = Math.max(longest, graphemes(t).length); wid = Math.max(wid, tw(t, 11, 650)); }); });
    var need = Math.max(30, 6.6 * longest + 22, wid + 3 + tw('●', 11) + 8);
    var labW = function (list) { return Math.max(24, Math.min(Math.max(W * 0.4, 60), 170, mx(list.map(function (s) { return tw(s, 12); })) + 8)); };
    // rows down (0) or transposed (1); each split into blocks of as many columns as fit when one block does not
    var O = [0, 1].map(function (t) {
      var down = t ? d.cols : d.rows, across = t ? d.rows : d.cols, lw = labW(down), avail = W - lw - 6;
      var per = Math.max(1, Math.floor(avail / need)), blocks = Math.ceil(across.length / per), n = Math.min(per, across.length);
      var cw = Math.min(96, avail / n);
      var clipped = across.filter(function (s) { return !isMonth(s) && tw(s, 12) > cw - 4; }).length;
      return { t: t, down: down, across: across, lw: lw, per: per, blocks: blocks, cw: cw, cost: blocks * down.length * RH + blocks * 30 + clipped * 120 };
    });
    var o = O[0].blocks === 1 ? O[0] : O[1].blocks === 1 ? O[1] : (O[0].cost <= O[1].cost ? O[0] : O[1]);
    var T = o.t === 1, down = o.down, across = o.across, cw = o.cw, gx = o.lw + 6;
    var twoLine = across.every(isYM), hh = twoLine ? 34 : 22;
    var downName = T ? d.col_label : d.row_label, acrossName = T ? d.row_label : d.col_label;
    var at = function (i, j) { return T ? [j, i] : [i, j]; };   // (down, across) -> the record's (row, col)
    var dn = fit(downName + ' ↓', W * 0.45, 12, 650), ax = Math.max(gx, tw(dn, 12, 650) + 14);
    var b = txt(0, 12, dn, { cls: 'lab nlv-axn' }) + txt(ax, 12, acrossName + ' →', { cls: 'lab nlv-axn', maxW: W - ax });
    var y = top, anyEmpty = false;
    for (var bi = 0; bi < o.blocks; bi++) {
      var c0 = bi * o.per, c1 = Math.min(across.length, c0 + o.per);
      // the header: a label over each column (a 'YYYY-MM' column shortened to its
      // month, the year under the first column and each January); a category label is clipped, never thinned
      for (var j = c0; j < c1; j++) {
        var hx = gx + (j - c0) * cw, s = across[j];
        if (twoLine) {
          b += txt(hx + cw / 2, y + 13, MON[+s.slice(5, 7) - 1], { anchor: 'middle', maxW: cw - 2 });
          if (j === c0 || s.slice(5, 7) === '01') {
            var nextJan = c1;
            for (var q = j + 1; q < c1; q++) if (across[q].slice(5, 7) === '01') { nextJan = q; break; }
            b += txt(hx + 2, y + 28, s.slice(0, 4), { maxW: (nextJan - j) * cw - 4 });
          }
        } else {
          b += txt(hx + cw / 2, y + hh - 7, s, { anchor: 'middle', maxW: cw - 4 });
        }
      }
      var y0 = y + hh;
      down.forEach(function (r, i) {
        var yy = y0 + i * RH;
        b += txt(o.lw - 2, yy + RH / 2 + 4, r, { anchor: 'end', maxW: o.lw - 4 });
        for (var j2 = c0; j2 < c1; j2++) {
          var ij = at(i, j2), ri = ij[0], cj = ij[1], xx = gx + (j2 - c0) * cw, st = state(ri, cj), t = d.text[ri][cj], k = d.tier[ri][cj];
          var rect = function (cls) { return '<rect' + (cls ? ' class="' + cls + '"' : '') + ' x="' + f1(xx + 1) + '" y="' + f1(yy + 1) + '" width="' + f1(cw - 2) + '" height="' + (RH - 2) + '" rx="2"/>'; };
          var tip = d.rows[ri] + ', ' + d.cols[cj] + ': ' + (st === 'empty' ? 'no value' : t);
          if (st === 'empty') { anyEmpty = true; b += '<g class="hc2-null" data-tip="' + esc(tip) + '">' + rect('') + '</g>'; continue; }
          var g = st === 'shown' ? GLYPH[Math.abs(k)] : '';
          var cls = st === 'sup' ? 'nlv-supr' : st === 'neutral' ? 'nlv-neur' : (div ? (k < 0 ? 'cn' : 'cp') : 'q') + Math.abs(k);
          b += '<g class="hc2' + (st === 'sup' ? ' nlv-sup' : st === 'neutral' ? ' nlv-neu' : '') + '" data-tip="' + esc(tip) + '" data-cell="' + ri + ',' + cj + '">' + rect(cls) +
            '<text class="nlv-cell" x="' + f1(xx + cw / 2) + '" y="' + (yy + RH / 2 + 4) + '" text-anchor="middle"><tspan class="cv2' + (st === 'sup' ? ' nlv-supt' : '') + '">' + esc(t) + '</tspan>' +
            (g ? '<tspan class="gl2" dx="3" aria-hidden="true">' + g + '</tspan>' : '') + '</text></g>';
        }
      });
      y = y0 + down.length * RH + (bi < o.blocks - 1 ? 12 : 4);
    }
    // the legend: each tier used (a swatch, its glyph, the engine's words), then the suppressed cells and why
    var sw = function (k) { return div ? (k < 0 ? 'f' : 'r') + Math.abs(k) : 's' + Math.abs(k); };
    var items = (arr(d.legend) ? d.legend : []).filter(function (L) { return obj(L) && fin(L.tier) && L.tier && L.tier === Math.round(L.tier) && Math.abs(L.tier) <= 3 && str(L.text); }).map(function (L) {
      return '<li><span class="nlv-sw nlv-sw-' + sw(L.tier) + '" aria-hidden="true"></span><span class="nlv-gl" aria-hidden="true">' + (GLYPH[Math.abs(L.tier)] || '') + '</span> ' + esc(L.text) + '</li>';
    });
    var S = rec.suppressed;
    if (obj(S) && S.cells > 0) items.push('<li><span class="nlv-sw nlv-sw-sup" aria-hidden="true"></span><span class="nlv-supk">&lt;5</span> ' + esc(S.why || '') + '</li>');
    if (anyEmpty) items.push('<li><span class="nlv-sw nlv-sw-emp" aria-hidden="true"></span> blank: no value for that cell</li>');
    return { body: b, H: y, after: items.length ? '<ul class="nlv-legend">' + items.join('') + '</ul>' : '', supInAfter: true, layout: (T ? 'transposed' : 'rows') + (o.blocks > 1 ? ', ' + o.blocks + ' blocks of ' + o.per : '') + ', cell ' + Math.round(cw) + ' of ' + Math.round(need) };
  }

  /* ------------------------------------------------------------ waterfall */
  // a bar's end is at `to`; its text goes past that end (right when the bar grows rightward or up, else left or down)
  function waterfall(rec, W) {
    var S = rec.data.steps, nStep = S.filter(function (s) { return s.kind !== 'total'; }).length;
    var tws = S.map(function (s) { return tw(s.text, 12, 650); });
    if (W >= 560 && nStep <= 8) {
      var vals = [0]; S.forEach(function (s) { vals.push(s.from, s.to); });
      var yt = yTicks(Math.min.apply(null, vals), Math.max.apply(null, vals), 4), L = mx(yt.lab.map(function (s) { return tw(s, 12); })) + 12;
      var slot = (W - L - 8) / S.length;
      if (slot >= 44 && mx(tws) <= slot - 6 && clippedShare(S.map(function (s) { return s.label; }), slot - 6) <= 0.25) return wfV(rec, W, L, slot);
    }
    return wfH(rec, W, tws);
  }
  // the step the total less its published parts makes (the suppressed cells), in the words a reader needs and as a dashed
  // marker, never a filled bar: an older report's label is read the same way
  var UNALLOC = /^(?:not allocated: suppressed cells|unallocated \(suppressed cells\))$/i;
  function isUnalloc(s) { return s.kind !== 'total' && UNALLOC.test(String(s.label)); }
  function stepName(s) { return UNALLOC.test(String(s.label)) ? 'Not allocated: suppressed cells' : s.label; }
  // an older report's table view says "unallocated (suppressed cells)": the same words as the drawing
  function relabelTable(t) {
    if (!t || !Array.isArray(t.rows) || !t.rows.some(function (r) { return Array.isArray(r) && UNALLOC.test(String(r[0])); })) return t;
    var o = {}; Object.keys(t).forEach(function (k) { o[k] = t[k]; });
    o.rows = t.rows.map(function (r) { return Array.isArray(r) && UNALLOC.test(String(r[0])) ? ['Not allocated: suppressed cells'].concat(r.slice(1)) : r; });
    return o;
  }
  function wfClass(s) { return s.kind === 'total' ? 'w-tot' : isUnalloc(s) ? 'w-unalloc' : s.value > 0 ? 'w-rise' : 'w-fall'; }
  function wfH(rec, W, tws) {
    var S = rec.data.steps, n = S.length, RH = 34, T = 4;
    var vals = [0]; S.forEach(function (s) { vals.push(s.from, s.to); });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (!(hi > lo)) hi = lo + 1;
    var padL = 0, padR = 0;
    S.forEach(function (s, i) { if (s.to >= s.from) padR = Math.max(padR, tws[i] + 8); else padL = Math.max(padL, tws[i] + 8); });
    var x = scale(lo, hi, padL + 1, W - padR - 1), b = '';
    var bot = T + n * RH;
    b += xAxis(x, lo, hi, padL, W - padR, T, bot, W);
    b += '<line class="nlv-zero" x1="' + f1(x(0)) + '" x2="' + f1(x(0)) + '" y1="' + T + '" y2="' + bot + '"/>';
    S.forEach(function (s, i) {
      var y = T + i * RH, a = x(s.from), z = x(s.to), right = s.to >= s.from, w = Math.abs(z - a), x0 = Math.min(a, z);
      if (w < 2) { w = isUnalloc(s) ? 7 : 2; x0 = right ? z - w : z; }
      var total = s.kind === 'total';
      b += '<g data-tip="' + esc(stepName(s) + ': ' + s.text) + '">' + txt(0, y + 12, stepName(s), { cls: total ? 'nlv-t nlv-b nlv-halo' : 'nlv-t nlv-halo', maxW: W }) +
        '<rect class="' + wfClass(s) + '" x="' + f1(x0) + '" y="' + (y + 17) + '" width="' + f1(w) + '" height="12" rx="2"/>' +
        txt(right ? z + 5 : z - 5, y + 27, s.text, { anchor: right ? 'start' : 'end', cls: 'vlab nlv-halo' }) + '</g>';
      if (i < n - 1) b += '<line class="nlv-con" x1="' + f1(z) + '" x2="' + f1(z) + '" y1="' + (y + 29) + '" y2="' + (y + RH + 17) + '"/>';
    });
    return { body: b, H: bot + 20, layout: 'horizontal' };
  }
  function wfV(rec, W, L, slot) {
    var S = rec.data.steps, n = S.length, T = 22, plotH = Math.max(200, Math.min(300, Math.round(W * 0.36))), Bt = T + plotH, LB = 18;
    var vals = [0]; S.forEach(function (s) { vals.push(s.from, s.to); });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (!(hi > lo)) hi = lo + 1;
    var y = scale(lo, hi, Bt, T), yt = yTicks(lo, hi, 4), b = '', R = W - 8, bw = Math.min(64, slot * 0.6);
    yt.ts.forEach(function (t, i) {
      b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + f1(y(t)) + '" y2="' + f1(y(t)) + '"/>' + txt(L - 6, y(t) + 4, yt.lab[i], { anchor: 'end' });
    });
    b += '<line class="nlv-zero" x1="' + L + '" x2="' + R + '" y1="' + f1(y(0)) + '" y2="' + f1(y(0)) + '"/>';
    S.forEach(function (s, i) {
      var cx = L + slot * (i + 0.5), a = y(s.from), z = y(s.to), up = s.to >= s.from, h = Math.abs(z - a), y0 = Math.min(a, z);
      if (h < 2) { h = isUnalloc(s) ? 7 : 2; y0 = up ? z - (h - 2) : z - 2; }
      var total = s.kind === 'total', lines = two(stepName(s), slot - 6, 12, total ? 700 : 400);
      b += '<g data-tip="' + esc(stepName(s) + ': ' + s.text) + '"><rect class="' + wfClass(s) + '" x="' + f1(cx - bw / 2) + '" y="' + f1(y0) + '" width="' + f1(bw) + '" height="' + f1(h) + '" rx="2"/>' +
        txt(cx, up ? z - 6 : z + 15, s.text, { anchor: 'middle', cls: 'vlab' }) +
        lines.map(function (l, k) {
          return l ? '<text x="' + f1(cx) + '" y="' + (Bt + LB + 13 + k * 14) + '" class="nlv-t' + (total ? ' nlv-b' : '') + '" text-anchor="middle"' + (rtl(s.label) ? ' direction="rtl"' : '') +
            (/…$/.test(l) ? ' data-tip="' + esc(s.label) + '" data-full="' + esc(s.label) + '"' : '') + '>' + esc(l) + '</text>' : '';
        }).join('') + '</g>';
      if (i < n - 1) b += '<line class="nlv-con" x1="' + f1(cx + bw / 2) + '" x2="' + f1(cx + slot - bw / 2) + '" y1="' + f1(z) + '" y2="' + f1(z) + '"/>';
    });
    return { body: b, H: Bt + LB + 34, layout: 'vertical' };
  }

  /* ------------------------------------------------------------ dot range */
  function dotRange(rec, W) {
    var rows = rec.data.rows, n = rows.length, all = [];
    rows.forEach(function (r) { all.push(r.lo, r.hi, r.center, r.median); });
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all), pad = (hi - lo) * 0.04 || Math.abs(hi) * 0.05 || 1;
    lo -= pad; hi += pad;
    var cName = rec.measure && rec.measure.kind === 'average' ? 'average' : 'value', b = '';
    var rng = function (r) { return r.texts.lo + ' to ' + r.texts.hi; };
    // the key: what the dot, the tick and the line are
    var key = function (x0) {
      return keyRow([{ w: 10, text: cName, mark: function (x, y) { return '<circle class="d-dot" cx="' + (x + 5) + '" cy="' + y + '" r="4"/>'; } },
        { w: 6, text: 'median', mark: function (x, y) { return '<line class="d-med" x1="' + (x + 3) + '" x2="' + (x + 3) + '" y1="' + (y - 6) + '" y2="' + (y + 6) + '"/>'; } },
        { w: 20, text: 'range', mark: function (x, y) { return '<line class="d-rng" x1="' + x + '" x2="' + (x + 20) + '" y1="' + y + '" y2="' + y + '"/>'; } }], x0, W);
    };
    if (W >= 480) {
      var lw = Math.min(Math.max(80, W * 0.3), 200, mx(rows.map(function (r) { return Math.max(tw(r.label, 12), tw('n = ' + r.texts.n, 12)); })) + 10);
      var rw = mx(rows.map(function (r) { return tw(rng(r), 12); })) + 12, L = lw + 14, R = W - rw - 10, RH = 38;
      var k1 = key(L), T = k1.h + 6;
      var x = scale(lo, hi, L, R), bot = T + n * RH;
      b += k1.svg + xAxis(x, lo, hi, L, R, T, bot, W - rw);
      rows.forEach(function (r, i) {
        var y = T + i * RH, ym = y + 21, cx = x(r.center), cw = tw(r.texts.center, 12, 650);
        b += '<g data-tip="' + esc(r.label + ': ' + r.texts.center + ' (' + rng(r) + '), median ' + r.texts.median + ', n = ' + r.texts.n) + '">' +
          txt(0, y + 15, r.label, { cls: 'nlv-t', maxW: lw }) + txt(0, y + 30, 'n = ' + r.texts.n, { maxW: lw }) +
          '<line class="d-rng" x1="' + f1(x(r.lo)) + '" x2="' + f1(x(r.hi)) + '" y1="' + ym + '" y2="' + ym + '"/>' +
          '<line class="d-med" x1="' + f1(x(r.median)) + '" x2="' + f1(x(r.median)) + '" y1="' + (ym - 7) + '" y2="' + (ym + 7) + '"/>' +
          '<circle class="d-dot" cx="' + f1(cx) + '" cy="' + ym + '" r="4.5"/>' +
          txt(Math.min(R - cw / 2, Math.max(L + cw / 2, cx)), ym - 9, r.texts.center, { anchor: 'middle', cls: 'vlab' }) +
          txt(W, ym + 4, rng(r), { anchor: 'end', cls: 'nlv-t' }) + '</g>';
      });
      return { body: b, H: bot + 22, layout: 'wide' };
    }
    // narrow: the label above its line, the centre by its dot, 'lo to hi' under the line
    var k2 = key(0), T2 = k2.h + 6, RH2 = 58, x2 = scale(lo, hi, 6, W - 6), bot2 = T2 + n * RH2;
    b += k2.svg + xAxis(x2, lo, hi, 6, W - 6, T2, bot2, W);
    rows.forEach(function (r, i) {
      var y = T2 + i * RH2, ym = y + 37, cx = x2(r.center), cw = tw(r.texts.center, 12, 650), rt = rng(r), rw2 = tw(rt, 12);
      var mid = (x2(r.lo) + x2(r.hi)) / 2, nt = ' (n = ' + r.texts.n + ')', nw = tw(nt, 12);
      b += '<g data-tip="' + esc(r.label + ': ' + r.texts.center + ' (' + rt + '), median ' + r.texts.median + ', n = ' + r.texts.n) + '">' +
        txt(0, y + 13, r.label, { cls: 'nlv-t', maxW: W - nw - 4 }) + txt(Math.min(W - nw, tw(fit(r.label, W - nw - 4, 12), 12) + 2), y + 13, nt) +
        '<line class="d-rng" x1="' + f1(x2(r.lo)) + '" x2="' + f1(x2(r.hi)) + '" y1="' + ym + '" y2="' + ym + '"/>' +
        '<line class="d-med" x1="' + f1(x2(r.median)) + '" x2="' + f1(x2(r.median)) + '" y1="' + (ym - 7) + '" y2="' + (ym + 7) + '"/>' +
        '<circle class="d-dot" cx="' + f1(cx) + '" cy="' + ym + '" r="4.5"/>' +
        txt(Math.min(W - cw / 2, Math.max(cw / 2, cx)), ym - 10, r.texts.center, { anchor: 'middle', cls: 'vlab' }) +
        txt(Math.min(W - rw2 / 2, Math.max(rw2 / 2, mid)), ym + 17, rt, { anchor: 'middle', cls: 'nlv-t' }) + '</g>';
    });
    return { body: b, H: bot2 + 22, layout: 'narrow' };
  }

  /* ------------------------------------------------------------ pareto */
  function pareto(rec, W) {
    var d = rec.data, k80 = d.k80, items = d.bars.map(function (x) { return { b: x, other: false }; });
    if (obj(d.other)) items.push({ b: d.other, other: true });
    var n = items.length, kIn = k80.k >= 1 && k80.k <= d.bars.length;
    if (W >= 480) {
      var vmax = mx(items.map(function (it) { return it.b.value; })), yt = yTicks(0, vmax * 1.14 || 1, 4);
      var L = mx(yt.lab.map(function (s) { return tw(s, 12); })) + 12, Rr = tw('100%', 12) + 12, slot = (W - L - Rr) / n;
      if (slot >= 44 && mx(items.map(function (it) { return tw(it.b.text, 12, 650); })) <= slot - 6 && clippedShare(items.map(function (it) { return it.b.label; }), slot - 6) <= 0.25) return paretoV(rec, W, items, L, Rr, slot, yt, kIn);
    }
    return paretoH(rec, W, items, kIn);
  }
  function paretoV(rec, W, items, L, Rr, slot, yt, kIn) {
    // the key over the drawing: bars, the running share, the 80% line
    var key = keyRow([{ w: 14, text: rec.measure && rec.measure.label ? rec.measure.label : 'value', mark: function (x, y) { return '<rect class="p-bar" x="' + x + '" y="' + (y - 5) + '" width="14" height="10" rx="2"/>'; } },
      { w: 18, text: 'running share (right axis)', mark: function (x, y) { return '<path class="p-cum" d="M' + x + ' ' + y + 'h18"/><circle class="p-cumd" cx="' + (x + 9) + '" cy="' + y + '" r="3"/>'; } },
      { w: 20, text: '80% of the total', mark: function (x, y) { return '<line class="p-80" x1="' + x + '" x2="' + (x + 20) + '" y1="' + y + '" y2="' + y + '"/>'; } }], L, W);
    var d = rec.data, T = key.h + 28, plotH = Math.max(200, Math.min(280, Math.round(W * 0.3))), Bt = T + plotH, R = W - Rr, b = key.svg;
    var vmax = mx(items.map(function (it) { return it.b.value; })), top = Math.max(yt.ts[yt.ts.length - 1], vmax * 1.14 || 1);
    var y = scale(0, top, Bt, T), yp = scale(0, 100, Bt, T), bw = Math.min(48, slot * 0.66);
    yt.ts.forEach(function (t, i) { b += '<line class="gridl" x1="' + L + '" x2="' + R + '" y1="' + f1(y(t)) + '" y2="' + f1(y(t)) + '"/>' + txt(L - 6, y(t) + 4, yt.lab[i], { anchor: 'end' }); });
    [0, 25, 50, 75, 100].forEach(function (p) { b += txt(R + 6, yp(p) + 4, p + '%'); });
    b += '<line class="p-80" x1="' + L + '" x2="' + R + '" y1="' + f1(yp(80)) + '" y2="' + f1(yp(80)) + '"/>';
    var pts = [];
    items.forEach(function (it, i) {
      var x = it.b, cx = L + slot * (i + 0.5), yy = y(x.value), mark = kIn && !it.other && i === d.k80.k - 1;
      var lines = two(x.label, slot - 6, 12);
      b += '<g data-tip="' + esc(x.label + ': ' + x.text + ', running share ' + x.cum_text) + '"><rect class="' + (it.other ? 'p-oth' : 'p-bar') + (mark ? ' p-k80' : '') + '" x="' + f1(cx - bw / 2) + '" y="' + f1(yy) + '" width="' + f1(bw) + '" height="' + f1(Math.max(1, Bt - yy)) + '" rx="2"/>' +
        txt(cx, yy - 6, x.text, { anchor: 'middle', cls: 'vlab' }) + (mark ? txt(cx, yy - 20, 'k80', { anchor: 'middle', cls: 'nlv-k80t' }) : '') +
        lines.map(function (l, k) {
          return l ? '<text x="' + f1(cx) + '" y="' + (Bt + 15 + k * 14) + '" class="nlv-t" text-anchor="middle"' + (rtl(x.label) ? ' direction="rtl"' : '') + (/…$/.test(l) ? ' data-tip="' + esc(x.label) + '" data-full="' + esc(x.label) + '"' : '') + '>' + esc(l) + '</text>' : '';
        }).join('') + '</g>';
      pts.push([cx, yp(Math.max(0, Math.min(100, x.cum_pct)))]);
    });
    b += '<path class="p-cum" d="' + pts.map(function (p, i) { return (i ? 'L' : 'M') + f1(p[0]) + ' ' + f1(p[1]); }).join('') + '"/>' +
      pts.map(function (p) { return '<circle class="p-cumd" cx="' + f1(p[0]) + '" cy="' + f1(p[1]) + '" r="3"/>'; }).join('');
    return { body: b, H: Bt + 44, layout: 'vertical' };
  }
  function paretoH(rec, W, items, kIn) {
    var d = rec.data, RH = 34, T = 22, cumW = mx(items.map(function (it) { return tw(it.b.cum_text, 12); })) + 12;
    var tws = items.map(function (it) { return tw(it.b.text, 12, 650); }), room = mx(tws) + 8, R = W - cumW - room;
    var vmax = mx(items.map(function (it) { return it.b.value; })) || 1, x = scale(0, vmax, 0, Math.max(40, R)), b = '', y = T;
    var tagW = tw('k80', 12, 700) + 6;
    b += txt(0, 12, rec.measure && rec.measure.label ? rec.measure.label : 'value', { cls: 'lab nlv-axn', maxW: W - tw('running share', 12) - 12 }) + txt(W, 12, 'running share', { anchor: 'end', cls: 'lab nlv-axn' });
    items.forEach(function (it, i) {
      var v = it.b, mark = kIn && !it.other && i === d.k80.k - 1, w = Math.max(1.5, x(v.value) - x(0));
      b += '<g data-tip="' + esc(v.label + ': ' + v.text + ', running share ' + v.cum_text) + '">' +
        txt(0, y + 12, v.label, { cls: 'nlv-t nlv-halo', maxW: W - cumW - 4 - (mark ? tagW : 0) }) +
        (mark ? txt(W - cumW - 4, y + 12, 'k80', { anchor: 'end', cls: 'nlv-k80t' }) : '') +
        '<rect class="' + (it.other ? 'p-oth' : 'p-bar') + (mark ? ' p-k80' : '') + '" x="0" y="' + (y + 17) + '" width="' + f1(w) + '" height="12" rx="2"/>' +
        txt(w + 5, y + 27, v.text, { cls: 'vlab' }) + txt(W, y + 27, v.cum_text, { anchor: 'end', cls: 'nlv-t' }) + '</g>';
      y += RH;
      if (mark) {
        // the 80% line as a divider row after bar k80, in the engine's words
        b += '<line class="p-80" x1="0" x2="' + f1(W) + '" y1="' + (y + 4) + '" y2="' + (y + 4) + '"/>' + txt(0, y + 19, d.k80.text, { cls: 'lab', maxW: W });
        y += 26;
      }
    });
    return { body: b, H: y + 4, layout: 'horizontal' };
  }

  /* ------------------------------------------------------------ slope */
  function dir(r) { return r.b > r.a ? 's-rise' : r.b < r.a ? 's-fall' : 's-flat'; }
  function slope(rec, W) {
    var d = rec.data, rows = d.rows;
    var aW = mx(rows.map(function (r) { return tw(r.a_text, 12); })), bW = mx(rows.map(function (r) { return tw(r.b_text, 12); })), cW = mx(rows.map(function (r) { return tw(r.change_text, 12, 700); }));
    var labMax = mx(rows.map(function (r) { return tw(r.label, 12); }));
    // wide: two axes when both ends' labels fit, each side at most 40% of the width
    var LW = Math.min(W * 0.4, labMax + 8 + aW), RW = Math.min(W * 0.4, bW + 8 + cW);
    if (W >= 360 && LW - aW - 6 >= Math.min(labMax, 48) && W - LW - RW - 32 >= 56) return slopeW(rec, W, LW, RW, aW);
    return slopeN(rec, W, aW, bW, cW);
  }
  function spread(ys, lo, hi, gap) {
    var o = ys.map(function (y, i) { return { y: y, i: i }; }).sort(function (a, b) { return a.y - b.y; });
    for (var k = 0; k < o.length; k++) o[k].p = Math.max(o[k].y, k ? o[k - 1].p + gap : lo);
    if (o.length && o[o.length - 1].p > hi) {
      o[o.length - 1].p = hi;
      for (var k2 = o.length - 2; k2 >= 0; k2--) o[k2].p = Math.min(o[k2].p, o[k2 + 1].p - gap);
    }
    var out = []; o.forEach(function (x) { out[x.i] = x.p; });
    return out;
  }
  function slopeW(rec, W, LW, RW, aW) {
    var d = rec.data, rows = d.rows, n = rows.length, T = 34, gap = 15, plotH = Math.max(150, n * gap + 30), Bt = T + plotH;
    var all = []; rows.forEach(function (r) { all.push(r.a, r.b); });
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all), pad = (hi - lo) * 0.05 || Math.abs(hi) * 0.05 || 1;
    var xA = LW + 14, xB = Math.min(W - RW - 14, xA + 480), y = scale(lo - pad, hi + pad, Bt, T), b = '';
    // each axis's name centred over it, kept inside the drawing and clear of the other
    var mid = (xA + xB) / 2, head = function (s, x, a0, a1) { var t = fit(s, a1 - a0, 12, 650), w = tw(t, 12, 650); return txt(Math.max(a0 + w / 2, Math.min(a1 - w / 2, x)), 14, t, { anchor: 'middle', cls: 'lab nlv-axn' }); };
    b += head(d.a_label, xA, 0, mid - 6) + head(d.b_label, xB, mid + 6, W);
    b += '<line class="nlv-zero" x1="' + xA + '" x2="' + xA + '" y1="' + (T - 8) + '" y2="' + (Bt + 8) + '"/><line class="nlv-zero" x1="' + xB + '" x2="' + xB + '" y1="' + (T - 8) + '" y2="' + (Bt + 8) + '"/>';
    var ya = rows.map(function (r) { return y(r.a); }), yb = rows.map(function (r) { return y(r.b); });
    var la = spread(ya, T, Bt, gap), lb = spread(yb, T, Bt, gap);
    rows.forEach(function (r, i) {
      var c = dir(r), bw = tw(r.b_text, 12);
      b += '<g data-tip="' + esc(r.label + ': ' + r.a_text + ' to ' + r.b_text + ' (' + r.change_text + ')') + '">' +
        '<line class="' + c + '" x1="' + xA + '" x2="' + xB + '" y1="' + f1(ya[i]) + '" y2="' + f1(yb[i]) + '"/>' +
        '<circle class="' + c + '-d" cx="' + xA + '" cy="' + f1(ya[i]) + '" r="3.5"/><circle class="' + c + '-d" cx="' + xB + '" cy="' + f1(yb[i]) + '" r="3.5"/>' +
        (Math.abs(la[i] - ya[i]) > 1 ? '<path class="nlv-lead" d="M' + (xA - 6) + ' ' + f1(la[i] - 4) + 'L' + (xA - 1) + ' ' + f1(ya[i]) + '"/>' : '') +
        (Math.abs(lb[i] - yb[i]) > 1 ? '<path class="nlv-lead" d="M' + (xB + 6) + ' ' + f1(lb[i] - 4) + 'L' + (xB + 1) + ' ' + f1(yb[i]) + '"/>' : '') +
        txt(xA - 8, la[i], r.a_text, { anchor: 'end', cls: 'nlv-t' }) +
        txt(xA - 8 - aW - 6, la[i], r.label, { anchor: 'end', cls: 'nlv-t', maxW: xA - 14 - aW - 6 }) +
        txt(xB + 8, lb[i], r.b_text, { cls: 'nlv-t' }) + txt(xB + 8 + bw + 6, lb[i], r.change_text, { cls: 'nlv-t nlv-b ' + c + '-t', wt: 700, maxW: Math.max(0, W - (xB + 14 + bw)) }) + '</g>';
    });
    return { body: b, H: Bt + 16, layout: 'two axes' };
  }
  function slopeN(rec, W, aW, bW, cW) {
    var d = rec.data, rows = d.rows, n = rows.length, RH = 52, T = 24, colW = cW + 12, padT = Math.max(aW, bW) + 6;
    var all = []; rows.forEach(function (r) { all.push(r.a, r.b); });
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    if (!(hi > lo)) { lo -= 1; hi += 1; }
    var L = padT, R = W - colW - padT, x = scale(lo, hi, L, Math.max(L + 20, R)), b = '';
    // the key: a hollow dot for the first window, a filled one for the second
    var key = keyRow([{ w: 10, text: d.a_label, mark: function (x, y) { return '<circle class="s-a" cx="' + (x + 5) + '" cy="' + y + '" r="4"/>'; } },
      { w: 10, text: d.b_label, mark: function (x, y) { return '<circle class="s-flat-d" cx="' + (x + 5) + '" cy="' + y + '" r="4"/>'; } }], 0, W);
    T = key.h + 6;
    b += key.svg;
    var bot = T + n * RH;
    b += xAxis(x, lo, hi, L, Math.max(L + 20, R), T, bot, W - colW);
    rows.forEach(function (r, i) {
      var y = T + i * RH, ym = y + 26, xa = x(r.a), xb = x(r.b), c = dir(r), left = Math.min(xa, xb), right = Math.max(xa, xb), mid = (xa + xb) / 2;
      var tl = xa <= xb ? r.a_text : r.b_text, tr = xa <= xb ? r.b_text : r.a_text;
      var head = Math.abs(xb - xa) >= 10 ? '<path class="' + c + '-d" d="M' + f1(xb) + ' ' + ym + 'l' + (xb > xa ? '-7 -4v8z' : '7 -4v8z') + '"/>' : '';
      b += '<g data-tip="' + esc(r.label + ': ' + r.a_text + ' to ' + r.b_text + ' (' + r.change_text + ')') + '">' +
        txt(0, y + 12, r.label, { cls: 'nlv-t', maxW: W - colW - 4 }) +
        '<line class="' + c + '" x1="' + f1(xa) + '" x2="' + f1(xb) + '" y1="' + ym + '" y2="' + ym + '"/>' + head +
        '<circle class="s-a" cx="' + f1(xa) + '" cy="' + ym + '" r="4"/><circle class="' + c + '-d" cx="' + f1(xb) + '" cy="' + ym + '" r="4"/>' +
        txt(Math.min(left + 4, mid - 3), ym + 17, tl, { anchor: 'end', cls: 'lab' }) + txt(Math.max(right - 4, mid + 3), ym + 17, tr, { cls: 'lab' }) +
        txt(W, ym + 4, r.change_text, { anchor: 'end', cls: 'nlv-t nlv-b ' + c + '-t' }) + '</g>';
    });
    return { body: b, H: bot + 22, layout: 'dumbbells' };
  }

  var DRAW = { waterfall: waterfall, heatmap: heatmap, dot_range: dotRange, pareto: pareto, slope: slope };

  /* ------------------------------------------------------------ the table: the universal fallback */
  function num(c) { return /^[−+\-]?[\d.,]+(%| \S+)?$/.test(String(c)); }
  NLV.tableHtml = function (rec) {
    var t = rec.table, title = String(rec.title || 'Chart');
    if (!tableOk(t)) return '<p class="note">This chart could not be drawn, and it carries no table to show instead.</p>';
    return '<div class="tscroll nlv-tbl" tabindex="0" role="region" aria-label="' + esc(title) + '"><table class="dt"><caption class="sr">' + esc(title) + '</caption><thead><tr>' +
      t.cols.map(function (c) { return '<th scope="col" dir="auto">' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      t.rows.map(function (r) {
        return '<tr>' + r.map(function (c, i) {
          return i === 0 ? '<th scope="row" dir="auto">' + esc(c) + '</th>' : '<td dir="auto"' + (num(c) ? ' class="num"' : '') + '>' + esc(c) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
  };
  // why a record is shown as its table, in the reader's words
  function tableWhy(rec) {
    if (obj(rec.degraded) && rec.degraded.why) { var w = String(rec.degraded.why); return w.charAt(0).toUpperCase() + w.slice(1).replace(/[.\s]+$/, '') + '.'; }
    if (rec.kind === 'table') return '';
    if (KINDS.indexOf(rec.kind) < 0) return 'This page does not draw a chart of this kind (' + String(rec.kind || 'none') + '), so its table is shown.';
    return 'This chart\'s data could not be drawn, so its table is shown.';
  }
  function notes(rec, kind, o) {
    var h = rec.summary ? '<p class="note nlv-sum">' + esc(rec.summary) + '</p>' : '', S = rec.suppressed;
    if (obj(S) && S.cells > 0 && S.why && !(o && o.supInAfter)) h += '<p class="note nlv-supn">' + esc(S.why) + '</p>';
    if (kind === 'pareto') { var k = rec.data.k80; if (k.k > rec.data.bars.length) h += '<p class="note nlv-k80n">' + esc(k.text) + '</p>'; }
    return h;
  }

  /* ------------------------------------------------------------ draw */
  // rec: one viz record; W: the width to draw in. Returns {html, svg, table, tnote, note, label, kind, layout}: html holds
  // the subtitle, the chart (svg) and its key (U.visual shows `table` instead when the figure's Table button is pressed);
  // note goes under it (the summary); a record drawn as its table has no `table` (the table is the chart).
  // a record drawn as its table: the summary above it, why it is a table (unless it is one by kind) under it
  NLV.tableOut = function (rec, why) {
    rec = obj(rec) ? rec : {};
    var w = why === undefined ? tableWhy(rec) : why, S = rec.suppressed;
    return { html: (rec.summary ? '<p class="note nlv-sum">' + esc(rec.summary) + '</p>' : '') + NLV.tableHtml(rec),
      note: (w ? '<p class="note nlv-why">' + esc(w) + '</p>' : '') + (obj(S) && S.cells > 0 && S.why ? '<p class="note nlv-supn">' + esc(S.why) + '</p>' : ''),
      label: NLV.label(rec), kind: 'table', table: null, layout: 'table' };
  };
  NLV.draw = function (rec, W) {
    W = Math.max(200, Math.floor(W || 600));
    if (!obj(rec)) return { html: '<p class="note">This chart could not be read.</p>', note: '', kind: 'table', label: 'Chart' };
    var label = NLV.label(rec), kind = NLV.kindOf(rec), sub = rec.subtitle ? '<p class="nlv-sub">' + esc(rec.subtitle) + '</p>' : '';
    if (kind === 'table') return NLV.tableOut(rec);
    var o;
    if (!has(DRAW, kind)) return NLV.tableOut(rec);
    try { o = DRAW[kind](rec, W); } catch (e) { if (window.console) console.error(e); return NLV.tableOut(rec, 'This chart could not be drawn here, so its table is shown.'); }
    var svg = svgOf(W, o.H, label, o.body);
    return { html: sub + svg + (o.after || ''), svg: svg, table: tableOk(rec.table) ? relabelTable(rec.table) : null,
      tnote: rec.subtitle ? '<p class="note">' + esc(rec.subtitle) + '</p>' : '', note: notes(rec, kind, o), label: label, kind: kind, layout: o.layout };
  };
})();
