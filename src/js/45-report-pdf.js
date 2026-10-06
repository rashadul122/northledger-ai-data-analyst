/* NorthLedger report PDF writer (window.NLReportPdf): the AI-written report as a professional PDF, made in the
   visitor's browser. No network, no DOM, no dependency: one file that runs as a classic script in the page and
   under Node (module.exports), so the page, the checks (tools/check_report_pdf.mjs) and the share worker (a
   sha-pinned copy) all write the same bytes.
   PDF-1.4 with the standard Helvetica family (not embedded), WinAnsi text set with the fonts' own AFM widths,
   true dashes and quotes; Letter (612 x 792 pt) or A4; a 100 pt rail and a 380 pt text column (about 70
   characters a line) inside 60 pt margins, charts, tables and callouts across 492 pt; a running header and a
   "Page X of Y" footer; numbered figures and tables with source notes; clickable references; bookmarks;
   document info, /Lang en-CA and the title shown in the viewer. Pagination: a heading stays with what follows,
   a figure is never split, a paragraph leaves at least 2 lines on each page.
   The chart registry's records (tools/fixtures/viz/spec.json) are drawn from the record alone by kind (VIZ: waterfall,
   heatmap, dot_range, pareto, slope; 'table', a kind it does not know or data it cannot read as the record's table),
   each followed by its table view (in Appendix A past 12 rows); Part 1 draws the contribution waterfall the AI did
   not place. build(model, {trace: []}) records each such figure's box, operators and cells for the checks, and
   writes the same bytes as without it.
   NLReportPdf.model(input) turns the page's state (the /report answer, the engine results posted to it, the
   visitor's choices) into the section model NLReportPdf.build(model, {paper}) renders. Every figure comes from
   the engine, or is quoted by the AI from a source cited in the same sentence: the model copies text, it never
   computes one. Part 3 draws the engine's scenario cards, and a level's historical range (group history_range: how far
   a rate or a price moved in the file's past 12-month and 3-month windows) as facts with a neutral HISTORY label, never
   a grade. A name in a script WinAnsi lacks (Cyrillic, Bengali, CJK...) is printed as a stable placeholder,
   "[name 1]", with a note (build: unforeign). NLReportPdf.shareResults trims the engine's results for a share link
   (to a byte cap the page may tighten: NLReportPdf.shareResults(results, maxBytes)).
   This file is ASCII only (it is inlined in index.html); every other character is written as a \u escape. */
(function (root) {
  'use strict';
  // Helvetica and Helvetica-Bold advance widths for WinAnsi codes 32..255 (Adobe AFM, 1/1000 em); Oblique = regular
  var WR = [278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,278,278,584,584,584,556,1015,667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611,278,278,278,469,556,333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500,334,260,334,584,0,556,0,222,556,333,1000,556,556,333,1000,667,333,1000,0,611,0,0,222,222,333,333,350,556,1000,333,1000,500,333,944,0,500,667,278,333,556,556,556,556,260,556,333,737,370,556,584,333,737,333,400,584,333,333,333,556,537,278,333,333,365,556,834,834,834,611,667,667,667,667,667,667,1000,722,667,667,667,667,278,278,278,278,722,722,778,778,778,778,778,584,778,722,722,722,722,667,667,611,556,556,556,556,556,556,889,500,556,556,556,556,278,278,278,278,556,556,556,556,556,556,556,584,611,556,556,556,556,500,556,500];
  var WB = [278,333,474,556,556,889,722,238,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,333,333,584,584,584,611,975,722,722,722,722,667,611,778,722,278,556,722,611,833,722,778,667,778,722,667,611,722,667,944,667,667,611,333,278,333,584,556,333,556,611,556,611,556,333,611,611,278,278,556,278,889,611,611,611,611,389,556,333,611,556,778,556,556,500,389,280,389,584,0,556,0,278,556,500,1000,556,556,333,1000,667,333,1000,0,611,0,0,278,278,500,500,350,556,1000,333,1000,556,333,944,0,500,667,278,333,556,556,556,556,280,556,333,737,370,556,584,333,737,333,400,584,333,333,333,611,556,278,333,333,365,556,834,834,834,611,722,722,722,722,722,722,1000,722,667,667,667,667,278,278,278,278,722,722,778,778,778,778,778,584,778,722,722,722,722,667,667,611,556,556,556,556,556,556,889,556,556,556,556,556,278,278,278,278,611,611,611,611,611,611,611,584,611,611,611,611,611,556,611,556];
  var PAPER = { letter: [612, 792], a4: [595.28, 841.89] };
  var LETTER_REGIONS = ['US', 'CA', 'MX'];
  var COL = { ink: '#16232e', body: '#2b3843', muted: '#56646f', hair: '#d9d5cb', accent: '#0b7366', accentInk: '#0a5c52',
    panel: '#f4f2ec', zebra: '#faf9f6', grid: '#e6e3da', axis: '#8f8a7e', white: '#ffffff',
    series: ['#24425c', '#0b7366', '#945400', '#6b5b95'],
    grade: { CONFIRMED: '#0a5c52', WATCH: '#945400', INSUFFICIENT: '#56646f' } };
  var GRADE_LABEL = { CONFIRMED: 'CONFIRMED', WATCH: 'WATCH', INSUFFICIENT: 'NOT ENOUGH DATA' };
  var FILE_WORD = '[your file]';   // the placeholder the engine's results and the AI's report use for the file's name
  var DOT = ' \u00b7 ';
  // Unicode to WinAnsi (cp1252) for the codes 0x80-0x9F; the rest of Latin-1 maps to itself
  var WIN = { 0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87, 0x02C6: 0x88,
    0x2030: 0x89, 0x0160: 0x8A, 0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91, 0x2019: 0x92, 0x201C: 0x93,
    0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9A, 0x203A: 0x9B,
    0x0153: 0x9C, 0x017E: 0x9E, 0x0178: 0x9F, 0x2212: 0x96 /* a minus sign is set as an en dash */ };
  // currency signs WinAnsi lacks, as the letters a reader knows them by; a few marks set as their nearest WinAnsi sign
  var CUR = { 0x20B9: 'Rs', 0x20A8: 'Rs', 0x09F3: 'Tk', 0x20BD: 'RUB', 0x20A9: 'KRW', 0x20AA: 'ILS', 0x20BA: 'TRY', 0x20B1: 'PHP',
    0x20AB: 'VND', 0x20A6: 'NGN', 0x20B4: 'UAH', 0x20BF: 'BTC', 0x20A1: 'CRC', 0x20B5: 'GHS', 0x20B8: 'KZT', 0x20BC: 'AZN', 0x20BE: 'GEL',
    0x25CF: '\x95', 0x25A0: '\x95', 0x25AA: '\x95', 0x2605: '*', 0x2606: '*', 0x2715: 'x', 0x2717: 'x', 0x2718: 'x' };
  // a Greek letter standing alone is a symbol (a change, a mean, a spread), written as its name
  var GREEK = { 0x394: 'delta', 0x3B4: 'delta', 0x3BC: 'mu', 0x3C3: 'sigma', 0x3A3: 'sum', 0x3B1: 'alpha', 0x3B2: 'beta', 0x3C0: 'pi',
    0x3BB: 'lambda', 0x3C1: 'rho', 0x3C4: 'tau', 0x3C7: 'chi', 0x3B5: 'epsilon', 0x3B8: 'theta', 0x3C6: 'phi', 0x3C9: 'omega', 0x3A9: 'Omega' };

  /* ---------------------------------------------------------------- text */
  function isMark(c) { return (c >= 0x300 && c <= 0x36F) || (c >= 0x1AB0 && c <= 0x1AFF) || (c >= 0x1DC0 && c <= 0x1DFF) || (c >= 0x20D0 && c <= 0x20FF) || (c >= 0xFE20 && c <= 0xFE2F) || (c >= 0xFE00 && c <= 0xFE0F) || c === 0x200C || c === 0x200D; }
  function isPicto(c) { return (c >= 0x1F000 && c <= 0x1FAFF) || (c >= 0x2600 && c <= 0x27BF) || (c >= 0x2B00 && c <= 0x2BFF) || (c >= 0xE0000 && c <= 0xE007F); }
  function isSymbol(c) { return (c >= 0x2000 && c <= 0x2BFF) || (c >= 0x3000 && c <= 0x303F); }
  // one code point as WinAnsi bytes: '' when it is left out (a combining mark, an emoji), null when this PDF cannot
  // show it. Latin letters with accents are WinAnsi's own or lose the accent (NFKD); nothing else is guessed.
  function encCp(c) {
    if (c === 9 || c === 10 || c === 13) return ' ';
    if ((c >= 32 && c < 127) || (c >= 0xA0 && c <= 0xFF)) return String.fromCharCode(c);
    if (WIN[c]) return String.fromCharCode(WIN[c]);
    if (CUR[c]) return CUR[c];
    if (isMark(c) || isPicto(c)) return '';
    var ch = String.fromCodePoint(c), d = ch.normalize ? ch.normalize('NFKD').replace(/[\u0300-\u036f]/g, '') : '';
    if (!d || d === ch) return null;
    var o = '';
    for (var i = 0; i < d.length; i++) {
      var k = d.charCodeAt(i);
      if ((k >= 32 && k < 127) || (k >= 0xA0 && k <= 0xFF)) o += d[i];
      else if (WIN[k]) o += String.fromCharCode(WIN[k]);
      else return null;
    }
    return o;
  }
  // a letter of a script this PDF cannot show (not a symbol, which is set as one "?")
  function foreignCp(c) { return !isSymbol(c) && !isMark(c) && encCp(c) === null; }
  // Unicode text to a WinAnsi byte string (one char code 0..255 each). A name in a script WinAnsi lacks never reaches
  // here in a built report (build() replaces it with a stable placeholder); an unknown sign becomes one "?"
  function enc(s) {
    s = String(s === null || s === undefined ? '' : s);
    if (!/[^\x20-\x7e]/.test(s)) return s;      // printable ASCII is its own WinAnsi (the loop below returns it as is)
    if (s.normalize) s = s.normalize('NFC');
    s = s.replace(/[\u2000-\u200a\u202f\u205f\u3000]/g, ' ').replace(/[\u200b\u2060\ufeff]/g, '')
      .replace(/[\u2010\u2011]/g, '-').replace(/\u2713|\u2714/g, '').replace(/\u2264/g, '<=').replace(/\u2265/g, '>=')
      .replace(/\u2248/g, '~').replace(/\u2192/g, '->').replace(/\u2190/g, '<-').replace(/\u2032/g, '\'').replace(/\u2033/g, '"');
    var out = '', unknown = false;
    for (var i = 0; i < s.length; i++) {
      var c = s.codePointAt(i);
      if (c > 0xFFFF) i++;
      var e = encCp(c);
      if (e === null) { if (!unknown) out += '?'; unknown = true; continue; }
      unknown = false;
      out += e;
    }
    return out;
  }
  // Names in a script this PDF cannot show (Cyrillic, Bengali, CJK...): each distinct name becomes "[name 1]",
  // "[name 2]"... everywhere it appears (the same placeholder for the same name), so a table, a chart and the text
  // still agree. A run is the letters of that script with their marks, joined across a space, hyphen, apostrophe or
  // period to the next such letter. A lone Greek letter is a symbol and is written as its name. Returns the text.
  function placeholders(s, st) {
    s = String(s);
    if (!/[^\x00-\x7f]/.test(s)) return s;      // ASCII holds no letter of another script
    if (s.normalize) s = s.normalize('NFC');
    var out = '', i = 0, n = s.length;
    var cpAt = function (k) { return k < n ? s.codePointAt(k) : -1; };
    var len = function (c) { return c > 0xFFFF ? 2 : 1; };
    while (i < n) {
      var c = s.codePointAt(i);
      if (!foreignCp(c)) { out += s.slice(i, i + len(c)); i += len(c); continue; }
      var j = i + len(c);
      for (;;) {
        var c2 = cpAt(j);
        if (c2 < 0) break;
        if (foreignCp(c2) || isMark(c2)) { j += len(c2); continue; }
        if ((c2 === 32 || c2 === 45 || c2 === 39 || c2 === 0x2019 || c2 === 46) && foreignCp(cpAt(j + 1))) { j += 1; continue; }
        break;
      }
      var run = s.slice(i, j);
      if (GREEK[c] && j === i + 1) { out += GREEK[c]; i = j; continue; }
      if (!Object.prototype.hasOwnProperty.call(st.map, run)) { st.n += 1; st.map[run] = '[name\u00a0' + st.n + ']'; }
      out += st.map[run]; st.hits += 1;
      i = j;
    }
    return out;
  }
  // straight quotes and spaced hyphens to typographic ones (prose only; references and addresses are left as written)
  function smart(s) {
    s = String(s === null || s === undefined ? '' : s);
    if (!/['"-]/.test(s)) return s;               // no quote and no hyphen: nothing below applies
    return s
      .replace(/(\w)'(\w)/g, '$1\u2019$2').replace(/(^|[\s(\[\u2014\u2013])'/g, '$1\u2018').replace(/'/g, '\u2019')
      .replace(/(^|[\s(\[\u2014\u2013])"/g, '$1\u201c').replace(/"/g, '\u201d')
      .replace(/ -{2,3} /g, ' \u2014 ').replace(/ - /g, ' \u2013 ')
      .replace(/(^|[\s(\[:;,])-(?=\d)/g, '$1\u2212');
  }
  function tw(s, size, font) {          // s is already encoded; font 'R' | 'B' | 'I'
    var t = font === 'B' ? WB : WR, w = 0;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); w += (c >= 32 && c <= 255) ? t[c - 32] : 0; }
    return w * size / 1000;
  }
  function twc(s, size, font, tc) { return tw(s, size, font) + (tc || 0) * s.length; }
  function esc(b) { return b.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)'); }
  // a colour as the three operands of rg or RG; each colour's text is made once (a page draws thousands), in a
  // small cache that only the palette's few colours fill
  function rgb0(hex) { var n = parseInt(hex.slice(1), 16); return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255].map(function (v) { return v.toFixed(3); }).join(' '); }
  var RGB = Object.create(null), RGB_N = 0;
  function rgb(hex) {
    if (typeof hex !== 'string') return rgb0(hex);
    var s = RGB[hex];
    if (s === undefined) { s = rgb0(hex); if (RGB_N < 64) { RGB[hex] = s; RGB_N += 1; } }
    return s;
  }
  function f2(v) { return (Math.round(v * 100) / 100).toString(); }
  // a value a drawing places: finite and at most 1e300 across, so no scale, tick or path runs to Infinity or NaN
  // (review of the chart registry, 30 Sep 2026: a slope from 1e308 to -1e308 wrote "NaN" into the page's operators);
  // a legacy chart skips a value past it, a chart registry record is drawn as its table
  var MAXV = 1e300;
  function pv(v) { return isFinite(v) && Math.abs(v) <= MAXV; }
  // one en-US number format per number of decimals, made once: toLocaleString with options builds a new
  // Intl.NumberFormat on every call, and formats with one built from the same options (the same text)
  var NF = Object.create(null);
  function nf(min, max) { var k = min + ':' + max; return NF[k] || (NF[k] = new Intl.NumberFormat('en-US', { minimumFractionDigits: min, maximumFractionDigits: max })); }
  function fmtTick(v, step) {
    var dp = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step) - 1e-9));
    return nf(dp, dp).format(Number(v));
  }
  function fmtVal(v) {
    var a = Math.abs(v), dp = a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3;
    return nf(0, dp).format(Number(v));        // maximumFractionDigits alone implies a minimum of 0
  }
  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) { hi = lo + 1; }
    var raw = (hi - lo) / (n || 4), p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    var step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
    var a = Math.floor(lo / step + 1e-9) * step, b = Math.ceil(hi / step - 1e-9) * step, t = [];
    for (var v = a; v <= b + step / 2; v += step) t.push(Math.round(v / step) * step);
    return { ticks: t, lo: a, hi: b > a ? b : a + step, step: step };
  }
  // a line chart's value range: its lowest and highest values with LINE_PAD of their span either side, and 0 only when
  // the data cross it or come within NEAR_ZERO of their own span of it (the page's U.lineSpan, src/js/00-core.js)
  var LINE_PAD = 0.07, NEAR_ZERO = 0.25;
  function lineSpan(lo, hi) {
    var span = hi - lo, pad = (span || Math.abs(hi) || 1) * LINE_PAD, a = lo - pad, b = hi + pad;
    if (lo >= 0 && (lo <= NEAR_ZERO * span || a < 0)) a = 0;
    if (hi <= 0 && (-hi <= NEAR_ZERO * span || b > 0)) b = 0;
    return [a, b];
  }
  // a share of the rows as the adapter prints one (nl_browser._pct_text): two decimals under 1%, else one
  function fmtShare(p) { return (Math.abs(p) < 1 ? p.toFixed(2) : p.toFixed(1)) + '%'; }
  // a bar chart of more than HBARS_FROM bars is drawn across, HBAR_ROW points a bar
  var HBARS_FROM = 8, HBAR_ROW = 14;
  function cap1(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
  function utf16hex(s) { var h = 'FEFF'; s = String(s); for (var i = 0; i < s.length; i++) h += ('000' + s.charCodeAt(i).toString(16).toUpperCase()).slice(-4); return '<' + h + '>'; }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function pdfDate(d) {
    var off = -d.getTimezoneOffset(), sg = off >= 0 ? '+' : '-'; off = Math.abs(off);
    return 'D:' + d.getFullYear() + pad2(d.getMonth() + 1) + pad2(d.getDate()) + pad2(d.getHours()) + pad2(d.getMinutes()) + pad2(d.getSeconds()) + sg + pad2(Math.floor(off / 60)) + '\'' + pad2(off % 60) + '\'';
  }
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  function longDate(d) { return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear(); }
  function isoDate(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function words(s, max) {                 // at most max characters, cut at a word, with an ellipsis
    s = String(s || '').replace(/[^\S\u00a0]+/g, ' ').trim();
    if (s.length <= max) return s;
    return s.slice(0, max).replace(/\s+\S*$/, '').replace(/[,;:.\s]+$/, '') + '\u2026';
  }

  /* ---------------------------------------------------------------- wave 4 (track B): the estimand, the inference labels and the forecast audit in words
     The page (src/js/52-nl2-report.js) and this writer say these things the same way: NLReportPdf.facts holds the sentences
     and figures, composed from the engine's records alone (nothing is computed here but a unit's suffix and a share). */
  var SUFFIX = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
  // the estimand's own units ("$864.0B", as nl_structure.money writes them): a function of one amount, or null when the
  // report has no estimand or its measure is a rate or an index (never summed, never abbreviated)
  function unitOf(R) {
    var e = R && R.estimand, m = e && e.measure;
    if (!m || m.type === 'rate' || m.type === 'index') return null;
    var cur = /dollar|\$|CAD|USD/i.test(String(m.uom || '')) ? '$' : '';
    return function (v, signed) {
      v = Number(v);
      if (!isFinite(v)) return 'n/a';
      var a = Math.abs(v), body = null, i;
      for (i = 0; i < SUFFIX.length; i++) if (a >= SUFFIX[i][0]) { body = (a / SUFFIX[i][0]).toFixed(1) + SUFFIX[i][1]; break; }
      if (body === null) body = a >= 1 ? a.toFixed(0) : String(Number(a.toPrecision(3)));
      if (!/[1-9]/.test(body)) v = 0;
      return (signed ? (v > 0 ? '+' : v < 0 ? '\u2212' : '') : (v < 0 ? '\u2212' : '')) + cur + body;
    };
  }
  // a waterfall's axis in the record's own units: "$200B" for ticks of 200,000,000,000 (a step label's own text names the
  // currency); null for a small axis, which keeps its plain numbers
  function compactTicks(lo, hi, step, cur) {
    var top = Math.max(Math.abs(lo), Math.abs(hi)), u = null, i, k;
    // the largest unit the step is a whole number of ("$200B", not "$0.2T"), else a tenth of one
    for (k = 1; k >= 0.1 && !u; k /= 10) for (i = 0; i < SUFFIX.length; i++) if (top >= SUFFIX[i][0] && step >= SUFFIX[i][0] * k) { u = SUFFIX[i]; break; }
    if (!u) return null;
    var r = step / u[0], dp = r >= 1 ? 0 : Math.min(2, Math.ceil(-Math.log10(r) - 1e-9));
    return function (t) { return t === 0 ? cur + '0' : (t < 0 ? '\u2212' : '') + cur + nf(dp, dp).format(Math.abs(t) / u[0]) + u[1]; };
  }
  // the unallocated step's label, as the engine now writes it and as an older report did
  var UNALLOCATED = /^(?:not allocated: suppressed cells|unallocated \(suppressed cells\))$/i;
  var UNALLOCATED_WORDS = 'Not allocated: suppressed cells';
  function stepLabel(s) { return UNALLOCATED.test(String(s)) ? UNALLOCATED_WORDS : String(s); }
  var CORRECTION_WORDS = { total_with_parts: 'a total kept together with its own parts', two_adjustments: 'both the adjusted and the unadjusted series',
    component_with_parent: 'a component kept with the total that already holds it', alt_with_total: 'an alternative total kept with the total',
    mixed_units: 'different units mixed', rate_members: 'several members of a rate or an index', unverified_members: 'several members of a dimension no total was verified for' };
  // where the slice came from, in plain words (estimand.plan_source; structure.corrections names what was corrected)
  function planSourceWords(est, st) {
    var src = est && est.plan_source;
    if (src === 'ai') return 'The AI plan chose this slice and the engine checked it: no rows of it add a total to its own parts.';
    if (src === 'ai_corrected') {
      var cs = ((st && st.corrections) || (est && est.corrections) || []).filter(function (c) { return c && c.kind; });
      var why = cs.map(function (c) { return (CORRECTION_WORDS[c.kind] || String(c.kind).replace(/_/g, ' ')) + (c.dim ? ' (' + c.dim + ')' : ''); });
      return 'Your plan was corrected: ' + (why.length ? 'its rows would have counted the same amount twice (' + why.join('; ') + '), so' : 'its rows mixed totals and parts, so') +
        ' the engine read its default slice instead.';
    }
    if (src === 'engine_default') return 'The engine chose this slice: no plan asked for another, so it reads the table\'s own headline, the total of each dimension.';
    return '';
  }
  // "adds up" for each dimension whose total was checked against its parts: the verdict, the count of months it holds
  // on, the largest gap and what no published part holds in the latest 12 months
  function sumCheckWords(c, U) {
    var parts = c.parts === undefined || c.parts === null ? 'its' : 'the ' + c.parts;
    var on = isFinite(c.complete_cells) ? (isFinite(c.within_tolerance) ? ' on ' + c.within_tolerance + ' of ' + c.complete_cells + ' months' : ' on the ' + c.complete_cells + ' months where every part has a value') : ' month by month';
    var gap = '';
    if (c.max_residual && c.max_residual.text && c.max_residual.text !== 'n/a') gap = ', largest gap ' + c.max_residual.text;
    else if (isFinite(c.max_rel_residual)) gap = ', largest gap ' + (c.max_rel_residual < 0.0001 ? 'under 0.01%' : (100 * c.max_rel_residual).toFixed(2) + '%') + ' of ' + c.total;
    var un = c.unallocated_latest && c.unallocated_latest.text ? '; not allocated to a part in the latest 12 months: ' + c.unallocated_latest.text : '';
    var head = c.verdict === 'adds_up' ? 'Adds up: ' : 'Does not add up cleanly: ';
    return head + c.total + ' = ' + parts + ' parts of ' + c.dim + on + gap + un + '.';
  }
  // the report's headline claim: results.primary names it by id or claim; its finding gives the grade
  function primaryFinding(R) {
    var p = R && R.primary, fs = (R && R.findings) || [];
    if (!p) return null;
    return fs.filter(function (f) { return f && (f.claim === (p.claim || p) || (p.id && f.id === p.id)); })[0] || null;
  }
  // the process grade (T4): the engine still grades the claim, but when the headline is an official aggregate the grade
  // is of the month-to-month noise behind the monthly figures, not of the published total
  function processOf(R, f) {
    var inf = R && R.estimand && R.estimand.inference;
    if (!inf || inf.mode !== 'official_aggregate') return null;
    var g = f && gradeOf(f.verdict || f.grade), note = String(inf.grade_label || '').replace(/^process grade:\s*/i, '');
    return { grade: g || '', word: g ? (GRADE_LABEL[g] || g) : '', note: note || 'the month-to-month noise of the monthly figures; not a test of the published total' };
  }
  function estimandView(R) {
    var e = R && R.estimand;
    if (!e || typeof e !== 'object' || !e.text) return null;
    var U = unitOf(R), F = e.figures || {}, semi = String(e.text).indexOf(';'), f = primaryFinding(R);
    var months = function (x) { return x && x.months && x.months !== 12 ? 'the ' + x.months + ' matched months' : '12 months'; };
    var figs = [];
    if (F.prior && F.prior.text) figs.push({ label: (F.prior.months && F.prior.months !== 12 ? 'The ' + F.prior.months + ' matched months before' : '12 months before'), value: String(F.prior.text) });
    if (F.latest && F.latest.text) figs.push({ label: (F.latest.months && F.latest.months !== 12 ? 'The ' + F.latest.months + ' matched months, latest' : 'Latest 12 months'), value: String(F.latest.text) });
    if (F.change && F.change.text) figs.push({ label: 'Change', value: String(F.change.text) });
    if (F.change_pct && F.change_pct.text) figs.push({ label: 'Change, %', value: String(F.change_pct.text), lead: true });
    var inf = e.inference || null, st = R.structure || null;
    return { title: semi < 0 ? String(e.text) : String(e.text).slice(0, semi), what: semi < 0 ? '' : String(e.text).slice(semi + 1).trim(), figures: figs,
      process: processOf(R, f), described: !!(inf && inf.mode === 'official_aggregate'),
      checks: (e.sum_checks || []).filter(function (c) { return c && c.dim; }).map(function (c) { return sumCheckWords(c, U); }),
      excluded: (e.excluded || []).filter(function (x) { return x && x.what; }).map(function (x) { return { what: String(x.what), why: String(x.why || '') }; }),
      source: planSourceWords(e, st), how: inf && inf.how_known && inf.how_known.length ? inf.how_known.join('; ') : '',
      revisions: inf && inf.revisions ? String(inf.revisions) : '', quality: inf && inf.quality && inf.quality.codes ? inf.quality : null };
  }
  var AUDIT_WORDS = { passes: 'the back-test passed', unclear: 'the back-test neither passed nor failed it', fails: 'the back-test failed' };
  // the forecast's back-test (forecast.audit, nl_inference.forecast_audit): the label as the engine wrote it, the status and
  // whether the engine's grade is trusted; a failed one marks the forecast "not trusted" beside its grade
  function auditView(R) {
    var a = R && R.forecast && R.forecast.audit;
    if (!a || typeof a !== 'object' || !a.label) return null;
    var st = String(a.status || 'unclear');
    return { label: String(a.label), status: st, trusted: a.trusted === true, untrusted: st === 'fails', words: AUDIT_WORDS[st] || 'the back-test is not conclusive',
      grade: String(a.grade_label || ''), line: String(a.label) + ' (' + (AUDIT_WORDS[st] || st) + (a.trusted === true ? '; trusted' : '; not trusted') + ')' };
  }
  // a trend test's method line (T1): what it fitted, its answer, and what it does in series known to have no trend
  function trendLine(t, title) {
    if (!t || typeof t !== 'object' || !t.verdict) return '';
    var v = { rising: 'a rising trend', falling: 'a falling trend', no_settled_direction: 'no settled direction', not_graded: 'a direction it does not grade' }[t.verdict] || t.verdict;
    var z = t.size || {}, c = z.cell || {};
    var s = String(title || 'Trend') + ': ' + (t.name ? String(t.name) : 'a bootstrap trend test') + (t.n ? ' on ' + t.n + ' yearly values' : '') +
      (isFinite(t.p) ? '; p ' + Number(t.p).toPrecision(2).replace(/\.?0+$/, '') : '') + (isFinite(t.p_random_walk) ? ', random-walk screen ' + Number(t.p_random_walk).toPrecision(2).replace(/\.?0+$/, '') : '') +
      '; the verdict is ' + v + '.';
    if (isFinite(z.simulated)) {
      // the adapter's compact record has {nominal, simulated, claims, newey_west}; a shared copy of an older report may also hold the series count and the cell
      s += ' On ' + (z.series ? Number(z.series).toLocaleString('en-US') + ' ' : '') + 'simulated series with no trend' + (c.n ? ' like this one (' + c.n + ' years' + (isFinite(c.rho) ? ', momentum ' + c.rho : '') + ')' : '') +
        ', the test found a trend in ' + (100 * z.simulated).toFixed(1) + '% at its ' + (100 * (z.nominal || 0.05)).toFixed(0) + '% level' +
        (isFinite(z.claims) ? ' and claimed a direction in ' + (100 * z.claims).toFixed(1) + '%' : '') +
        (isFinite(z.newey_west) ? '; the Newey-West range used before found one in ' + (100 * z.newey_west).toFixed(1) + '%' : '') + '.';
    }
    return s;
  }

  /* ---------------------------------------------------------------- the page model */
  function Doc(o) {
    var sz = PAPER[o.paper] || PAPER.letter;
    this.W = sz[0]; this.H = sz[1];
    this.L = 60; this.CW = this.W - 120;                                                        // 60 pt margins left and right
    this.RAIL = 100; this.TX = this.L + this.RAIL + 12; this.TW = this.CW - this.RAIL - 12;   // the text column
    this.TOP = this.H - 74; this.BOT = 66;
    this.pages = []; this.outline = []; this.dests = {}; this.fig = 0; this.tab = 0; this.o = o;
  }
  Doc.prototype.newPage = function (kind) { this.pg = { ops: [], links: [], kind: kind || 'body' }; this.pages.push(this.pg); this.y = kind === 'cover' ? this.H - 60 : this.TOP; return this.pg; };
  Doc.prototype.op = function (s) { this.pg.ops.push(s); };
  Doc.prototype.need = function (h) { if (this.y - h < this.BOT) { this.newPage(); return true; } return false; };
  Doc.prototype.room = function () { return this.y - this.BOT; };
  Doc.prototype.text = function (b, x, y, size, font, hex, tc) {   // b: encoded bytes
    var F = font === 'B' ? 'F2' : font === 'I' ? 'F3' : 'F1';
    this.op('BT /' + F + ' ' + size + ' Tf ' + (tc ? tc + ' Tc ' : '0 Tc ') + rgb(hex || COL.body) + ' rg ' + f2(x) + ' ' + f2(y) + ' Td (' + esc(b) + ') Tj ET');
    if (this.tt) this.tt.texts.push({ x: x, y: y, w: twc(b, size, font, tc), size: size, s: b });   // a traced figure's words
  };
  Doc.prototype.rect = function (x, y, w, h, fill, stroke, lw, dash) {
    var s = 'q ';
    if (fill) s += rgb(fill) + ' rg ';
    if (stroke) s += rgb(stroke) + ' RG ' + (lw || 0.6) + ' w ' + (dash ? '[' + dash + '] 0 d ' : '');
    s += f2(x) + ' ' + f2(y) + ' ' + f2(w) + ' ' + f2(h) + ' re ' + (fill && stroke ? 'B' : fill ? 'f' : 'S') + ' Q';
    this.op(s);
  };
  Doc.prototype.line = function (x1, y1, x2, y2, hex, lw, dash) {
    this.op('q ' + rgb(hex) + ' RG ' + (lw || 0.6) + ' w ' + (dash ? '[' + dash + '] 0 d ' : '') + f2(x1) + ' ' + f2(y1) + ' m ' + f2(x2) + ' ' + f2(y2) + ' l S Q');
  };
  Doc.prototype.circle = function (cx, cy, r, fill, stroke, lw, alpha) {
    var k = 0.5523 * r, s = 'q ' + (alpha ? '/GSa gs ' : '') + (fill ? rgb(fill) + ' rg ' : '') + (stroke ? rgb(stroke) + ' RG ' + (lw || 0.6) + ' w ' : '');
    s += f2(cx + r) + ' ' + f2(cy) + ' m ' + f2(cx + r) + ' ' + f2(cy + k) + ' ' + f2(cx + k) + ' ' + f2(cy + r) + ' ' + f2(cx) + ' ' + f2(cy + r) + ' c ' +
      f2(cx - k) + ' ' + f2(cy + r) + ' ' + f2(cx - r) + ' ' + f2(cy + k) + ' ' + f2(cx - r) + ' ' + f2(cy) + ' c ' +
      f2(cx - r) + ' ' + f2(cy - k) + ' ' + f2(cx - k) + ' ' + f2(cy - r) + ' ' + f2(cx) + ' ' + f2(cy - r) + ' c ' +
      f2(cx + k) + ' ' + f2(cy - r) + ' ' + f2(cx + r) + ' ' + f2(cy - k) + ' ' + f2(cx + r) + ' ' + f2(cy) + ' c ';
    this.op(s + (fill && stroke ? 'B' : fill ? 'f' : 'S') + ' Q');
  };
  Doc.prototype.link = function (x, y, w, h, target) { this.pg.links.push({ r: [x, y, x + w, y + h], t: target }); };
  Doc.prototype.dest = function (id) { this.dests[id] = { page: this.pages.length - 1, y: this.y + 12 }; };

  /* ---- inline text: words with their own font, colour and link (citations, grade words) ---- */
  var INLINE = /(\[S\d+\]|\bCONFIRMED\b|\bWATCH\b|\bINSUFFICIENT\b)/;
  function tokens(str, font, hex, raw) {
    return String(raw ? str : smart(str)).split(/[^\S\u00a0]+/).filter(Boolean).map(function (word) {
      return word.split(INLINE).filter(function (x) { return x !== ''; }).map(function (part) {
        var m = part.match(/^\[S(\d+)\]$/);
        if (m) return { b: enc('[' + m[1] + ']'), f: font, c: COL.accentInk, link: 'ref' + m[1] };
        if (GRADE_LABEL[part]) return { b: enc(part), f: 'B', c: COL.grade[part] || hex };
        return { b: enc(part), f: font, c: hex };
      });
    });
  }
  function segW(seg, size) { return tw(seg.b, size, seg.f); }
  function tokW(t, size) { return t.reduce(function (a, s) { return a + segW(s, size); }, 0); }
  function lineW(ln, size) { return ln.reduce(function (a, t, i) { return a + (i ? tw(' ', size, 'R') : 0) + tokW(t, size); }, 0); }
  function wrapTokens(toks, size, width) {
    var lines = [], cur = [], w = 0, sp = tw(' ', size, 'R');
    toks.forEach(function (t) {
      var w1 = tokW(t, size);
      if (cur.length && w + sp + w1 > width) { lines.push(cur); cur = []; w = 0; }
      if (w1 > width) {       // one over-long word (an address, an id): broken by characters
        var s0 = t[0], chunk = '';
        for (var i = 0; i < s0.b.length; i++) {
          if (tw(chunk + s0.b[i], size, s0.f) > width && chunk) { lines.push([[{ b: chunk, f: s0.f, c: s0.c, link: s0.link }]]); chunk = ''; }
          chunk += s0.b[i];
        }
        cur = [[{ b: chunk, f: s0.f, c: s0.c, link: s0.link }]]; w = tw(chunk, size, s0.f); return;
      }
      w += (cur.length ? sp : 0) + w1;
      cur.push(t);
    });
    if (cur.length) lines.push(cur);
    return lines;
  }
  // the last of the lines kept, ended with an ellipsis that fits the width (a trailing comma or colon goes first)
  function ellipsize(lines, size, width) {
    var last = lines[lines.length - 1].slice();
    var trimEnd = function () {
      while (last.length) {
        var t = last[last.length - 1], s = t[t.length - 1], b = s.b.replace(/[\s,;:.\x96\x97-]+$/, '');
        if (b) { last[last.length - 1] = t.slice(0, -1).concat([{ b: b, f: s.f, c: s.c, link: s.link }]); return; }
        if (t.length > 1) last[last.length - 1] = t.slice(0, -1); else last.pop();
      }
    };
    var dots = function () { var t = last[last.length - 1], s = t[t.length - 1]; return last.slice(0, -1).concat([t.slice(0, -1).concat([{ b: s.b + '\x85', f: s.f, c: s.c }])]); };
    trimEnd();
    while (last.length > 1 && lineW(dots(), size) > width) { last.pop(); trimEnd(); }
    return last.length ? lines.slice(0, -1).concat([dots()]) : lines.slice(0, -1);
  }
  Doc.prototype.drawLine = function (line, x, y, size) {
    // one text operator per run of same-style words (a smaller file, clean copy and paste); a linked word alone
    var sp = tw(' ', size, 'R'), self = this, run = null;
    var flush = function () { if (run) { self.text(run.b, run.x, y, size, run.f, run.c); run = null; } };
    line.forEach(function (t, i) {
      t.forEach(function (s, k) {
        var gap = i > 0 && k === 0, w = segW(s, size);
        if (gap) x += sp;
        if (!s.link && run && s.f === run.f && s.c === run.c) run.b += (gap ? ' ' : '') + s.b;
        else {
          flush();
          if (s.link) { self.text(s.b, x, y, size, s.f, s.c); self.link(x, y - 2, w, size + 2, s.link); }
          else run = { b: s.b, x: x, f: s.f, c: s.c };
        }
        x += w;
      });
    });
    flush();
    return x;
  };
  // a paragraph in the text column (or at x and width), split across pages leaving at least 2 lines on each
  Doc.prototype.para = function (str, o) {
    o = o || {};
    var size = o.size || 10, lead = o.lead || size * 1.45, x = o.x !== undefined ? o.x : this.TX, width = o.width || (this.TX + this.TW - x);
    var lines = wrapTokens(tokens(str, o.font || 'R', o.color || COL.body, o.raw), size, width), i = 0;
    if (o.maxLines && lines.length > o.maxLines) lines = ellipsize(lines.slice(0, o.maxLines), size, width);
    while (i < lines.length) {
      var fit = Math.floor((this.y - this.BOT) / lead), left = lines.length - i;
      if (fit < left) {
        if (fit < 2 || ((left - fit) < 2 && left < 4)) { this.newPage(); continue; }
        if (left - fit < 2) fit = left - 2;
      }
      var n = Math.min(fit, left);
      for (var k = 0; k < n; k++, i++) {
        if (i === 0 && o.marker) o.marker(this.y - size);
        this.drawLine(lines[i], x, this.y - size, size);
        this.y -= lead;
      }
      if (i < lines.length) this.newPage();
    }
    this.y -= o.after !== undefined ? o.after : 6;
  };
  Doc.prototype.linesHeight = function (str, size, width, lead, font) { return wrapTokens(tokens(str, font || 'R', COL.body), size, width).length * (lead || size * 1.45); };

  /* ---- headings: a part opener starts high on a page; a section keeps with its first lines ---- */
  Doc.prototype.h1 = function (kicker, title, id) {
    if (this.y < this.TOP - 4) { if (this.room() < 260) this.newPage(); else this.y -= 16; }
    this.dest(id);
    this.outline.push({ title: title, id: id, level: 0 });
    this.text(enc(kicker.toUpperCase()), this.L, this.y - 8, 7.5, 'B', COL.accentInk, 1.1);
    this.line(this.L, this.y - 14, this.L + 28, this.y - 14, COL.accent, 1.6);
    this.y -= 30;
    this.para(title, { x: this.L, width: this.CW, size: 17, lead: 21, font: 'B', color: COL.ink, after: 10 });
  };
  Doc.prototype.h2 = function (num, title, id, nextMin) {
    var h = this.linesHeight(title, 11.5, this.TW, 15, 'B');
    this.need(h + 6 + (nextMin || 44));
    this.y -= 6;
    this.dest(id);
    this.outline.push({ title: (num ? num + '  ' : '') + title, id: id, level: 1 });
    if (num) this.text(enc(String(num)), this.L, this.y - 11.5, 11.5, 'B', COL.accent);
    this.para(title, { size: 11.5, lead: 15, font: 'B', color: COL.ink, after: 5 });
  };
  Doc.prototype.bullets = function (items, o) {
    var self = this; o = o || {};
    items.forEach(function (t, i) {
      self.para(t, { size: o.size || 10, lead: o.lead, after: 4, marker: function (yb) {
        if (o.numbered) self.text(enc(String(i + 1) + '.'), self.TX - 16, yb, o.size || 10, 'B', COL.accentInk);
        else self.rect(self.TX - 11, yb + 2.6, 3.2, 3.2, COL.accent);
      } });
    });
    this.y -= 4;
  };
  // a filled callout box: measured first, then drawn whole on one page
  Doc.prototype.calloutHeight = function (label, str, o) {
    o = o || {};
    var w = o.width || this.CW, size = o.size || 11, lead = size * 1.42, pad = 12;
    var lines = wrapTokens(tokens(str, o.font || 'R', COL.ink), size, w - 2 * pad - 4);
    return pad * 2 + (label ? 14 : 0) + lines.length * lead - (lead - size);
  };
  Doc.prototype.callout = function (label, str, o) {
    o = o || {};
    var x = o.x !== undefined ? o.x : this.L, w = o.width || this.CW, size = o.size || 11, lead = size * 1.42, pad = 12;
    var lines = wrapTokens(tokens(str, o.font || 'R', COL.ink), size, w - 2 * pad - 4);
    var h = pad * 2 + (label ? 14 : 0) + lines.length * lead - (lead - size);
    this.need(h + 8);
    var top = this.y;
    this.rect(x, top - h, w, h, o.fill || COL.panel, o.dash ? COL.muted : null, 0.8, o.dash);
    if (!o.dash) this.rect(x, top - h, 3, h, o.rule || COL.accent);
    var yy = top - pad;
    if (label) { this.text(enc(label.toUpperCase()), x + pad + 4, yy - 7, 7.5, 'B', o.labelColor || COL.accentInk, 1); yy -= 14; }
    for (var i = 0; i < lines.length; i++) { this.drawLine(lines[i], x + pad + 4, yy - size, size); yy -= lead; }
    this.y = top - h - 12;
  };
  // the grade badge: a shape and the word, so a grade never rests on colour alone
  Doc.prototype.badge = function (grade, x, yb, note) {
    var g = gradeOf(grade), lab = enc(GRADE_LABEL[g] || g), c = COL.grade[g] || COL.muted;
    var w = twc(lab, 6.5, 'B', 0.5) + 20, h = 11, y = yb - 2.5;
    if (g === 'CONFIRMED') {
      this.rect(x, y, w, h, c);
      this.op('q 1 1 1 RG 1.1 w ' + f2(x + 4.5) + ' ' + f2(y + 5.6) + ' m ' + f2(x + 6.6) + ' ' + f2(y + 3.3) + ' l ' + f2(x + 10.2) + ' ' + f2(y + 8.2) + ' l S Q');
      this.text(lab, x + 13, y + 3.3, 6.5, 'B', COL.white, 0.5);
    } else if (g === 'WATCH') {
      this.rect(x, y, w, h, COL.white, c, 0.8);
      this.circle(x + 7.5, y + 5.5, 2.7, null, c, 0.8);
      this.op('q ' + rgb(c) + ' rg ' + f2(x + 7.5) + ' ' + f2(y + 2.8) + ' m ' + f2(x + 7.5) + ' ' + f2(y + 8.2) + ' ' + f2(x + 4.1) + ' ' + f2(y + 8.2) + ' ' + f2(x + 4.1) + ' ' + f2(y + 5.5) + ' c ' + f2(x + 4.1) + ' ' + f2(y + 2.8) + ' ' + f2(x + 7.5) + ' ' + f2(y + 2.8) + ' ' + f2(x + 7.5) + ' ' + f2(y + 2.8) + ' c f Q');
      this.text(lab, x + 13, y + 3.3, 6.5, 'B', c, 0.5);
    } else {
      w -= 7;
      this.rect(x, y, w, h, COL.white, c, 0.8, '2 1.5');
      this.text(lab, x + 6, y + 3.3, 6.5, 'B', c, 0.5);
    }
    // what the grade is a grade of, in the words beside the pill ("process grade"), drawn on the next line when it does not fit
    if (note && !(note.ok === false)) this.text(enc(note.text), note.x !== undefined ? note.x : x + w + 5, note.y !== undefined ? note.y : y + 3.3, note.size || 6.5, 'I', COL.muted);
    return w;
  };
  // a figure derived from a graded change (a run rate, a what-if) has no grade of its own: a neutral outline and
  // "from a CONFIRMED change", never the grade's own pill
  Doc.prototype.fromBadge = function (parent, x, yb) {
    var g = gradeOf(parent), lab = enc('from a ' + (GRADE_LABEL[g] || g) + ' change'), w = tw(lab, 6.5, 'B') + 12, h = 11, y = yb - 2.5;
    this.rect(x, y, w, h, COL.white, COL.hair, 0.8);
    this.text(lab, x + 6, y + 3.3, 6.5, 'B', COL.muted);
    return w;
  };
  // a fact that is not graded at all (the historical range of a level: history, not a forecast): a neutral outline and
  // its word ("HISTORY"), never a grade's pill and never "from a ... change"
  Doc.prototype.tagBadge = function (word, x, yb) {
    var lab = enc(String(word).toUpperCase()), w = twc(lab, 6.5, 'B', 0.5) + 12, h = 11, y = yb - 2.5;
    this.rect(x, y, w, h, COL.white, COL.hair, 0.8);
    this.text(lab, x + 6, y + 3.3, 6.5, 'B', COL.muted, 0.5);
    return w;
  };
  // an item's own grade, or the grade of the change it is derived from (the adapter's parent_grade, 30 Sep 2026; an
  // older report put the parent's grade on a derived item's own grade)
  var DERIVED = { contribution: 1, price_volume_mix: 1, per_unit: 1, run_rate: 1, sensitivity: 1, gap: 1 };
  function itemGrade(x) {
    if (x && x.parent_grade && (x.grade === null || x.grade === undefined || x.grade === '')) return { grade: '', parent: gradeOf(x.parent_grade) };
    if (x && DERIVED[x.group]) return { grade: '', parent: gradeOf(x.grade) };
    return { grade: gradeOf(x && x.grade), parent: '' };
  }
  function gradeOf(g) {
    g = String(g || '').toUpperCase();
    if (g === 'RECOMMEND' || g === 'CONFIRMED') return 'CONFIRMED';
    if (g === 'WATCH') return 'WATCH';
    if (g === 'INSUFFICIENT' || g === 'NOT_ENOUGH_DATA' || g === 'NOT ENOUGH DATA') return 'INSUFFICIENT';
    return '';
  }

  /* ---- the estimand: what the headline measures, its three figures, how it was checked, what was left out (wave 4, track B) ---- */
  Doc.prototype.estimand = function (v) {
    var self = this, pad = 12, W = this.CW, inner = W - 2 * pad - 4, gap = 8;
    var titleL = wrapTokens(tokens(v.title, 'B', COL.ink), 12, inner), whatL = v.what ? wrapTokens(tokens(v.what, 'R', COL.muted), 8.3, inner) : [];
    var nf0 = v.figures.length, fw = (inner - gap * Math.max(0, nf0 - 1)) / Math.max(1, nf0), figH = 46;
    var procNote = v.process ? 'process grade: ' + v.process.note : '';
    var procW = v.process && v.process.word ? twc(enc(v.process.word), 6.5, 'B', 0.5) + 20 : 0;
    var procL = procNote ? wrapTokens(tokens(procNote, 'R', COL.muted), 7.8, Math.max(120, inner - procW - 8)) : [];
    var descL = v.described ? wrapTokens(tokens('Described, not tested: this is a published total, so the engine states the change and its checks rather than testing it.', 'I', COL.body), 8, inner) : [];
    var lines = function (list, size, font, color) { return list.map(function (t) { return wrapTokens(tokens(t, font || 'R', color || COL.body), size, inner - 10); }); };
    var chkL = lines(v.checks, 8.2), exL = lines(v.excluded.map(function (x) { return x.what + (x.why ? ': ' + x.why : ''); }), 8.2);
    var srcL = v.source ? wrapTokens(tokens(v.source, 'I', COL.muted), 8, inner) : [];
    var howL = v.how ? wrapTokens(tokens('How it is known: ' + v.how + '.' + (v.revisions ? ' ' + cap1(v.revisions) + '.' : ''), 'R', COL.muted), 7.6, inner) : [];
    var count = function (ls) { return ls.reduce(function (a, l) { return a + l.length; }, 0); };
    var h = pad + 14 + titleL.length * 14.5 + (whatL.length ? 3 + whatL.length * 10.5 : 0) + 8 + figH + 8 + Math.max(procL.length * 10 + 4, procNote ? 14 : 0) + (descL.length ? 4 + descL.length * 10 : 0) +
      (chkL.length ? 6 + 11 + count(chkL) * 10.2 + chkL.length * 2 : 0) + (exL.length ? 6 + 11 + count(exL) * 10.2 + exL.length * 2 : 0) + (srcL.length ? 6 + srcL.length * 10 : 0) + (howL.length ? 5 + howL.length * 9.6 : 0) + pad;
    this.need(h + 8);
    var top = this.y, x0 = this.L + pad + 4, y = top - pad;
    this.rect(this.L, top - h, W, h, COL.panel);
    this.rect(this.L, top - h, 3, h, COL.accent);
    this.text(enc('WHAT THIS REPORT MEASURES'), x0, y - 7, 7.5, 'B', COL.accentInk, 1); y -= 14;
    titleL.forEach(function (ln) { self.drawLine(ln, x0, y - 12, 12); y -= 14.5; });
    if (whatL.length) { y -= 3; whatL.forEach(function (ln) { self.drawLine(ln, x0, y - 8.3, 8.3); y -= 10.5; }); }
    y -= 8;
    v.figures.forEach(function (f, i) {
      var fx = x0 + i * (fw + gap);
      self.rect(fx, y - figH, fw, figH, COL.white, f.lead ? COL.accent : COL.hair, f.lead ? 1.1 : 0.8);
      self.text(enc(String(f.label).toUpperCase()), fx + 7, y - 12, 6.4, 'B', COL.muted, 0.5);
      var vb = enc(f.value), vs = f.lead ? 19 : 16;
      while (tw(vb, vs, 'B') > fw - 14 && vs > 9) vs -= 1;
      self.text(vb, fx + 7, y - 35, vs, 'B', f.lead ? COL.accentInk : COL.ink);
    });
    y -= figH + 8;
    if (procNote) {
      if (procW) self.badge(v.process.grade, x0, y - 9);
      procL.forEach(function (ln, k) { self.drawLine(ln, x0 + (procW ? procW + 8 : 0), y - 9 - k * 10, 7.8); });
      y -= Math.max(procL.length * 10 + 4, 14);
    }
    if (descL.length) { y -= 4; descL.forEach(function (ln) { self.drawLine(ln, x0, y - 8, 8); y -= 10; }); }
    var list = function (head, ls) {
      if (!ls.length) return;
      y -= 6;
      self.text(enc(head.toUpperCase()), x0, y - 7, 6.8, 'B', COL.muted, 0.8); y -= 11;
      ls.forEach(function (one) {
        one.forEach(function (ln, k) { if (k === 0) self.rect(x0 + 1, y - 7, 2.6, 2.6, COL.accent); self.drawLine(ln, x0 + 10, y - 8.2, 8.2); y -= 10.2; });
        y -= 2;
      });
    };
    list('How the figure was checked', chkL);
    list('Left out, and why', exL);
    if (srcL.length) { y -= 6; srcL.forEach(function (ln) { self.drawLine(ln, x0, y - 8, 8); y -= 10; }); }
    if (howL.length) { y -= 5; howL.forEach(function (ln) { self.drawLine(ln, x0, y - 7.6, 7.6); y -= 9.6; }); }
    this.y = top - h - 12;
  };

  /* ---- key-figure tiles: the figures a reader should carry away ---- */
  Doc.prototype.kpis = function (list) {
    if (!list.length) return;
    var n = list.length, gap = 10, w = (this.CW - gap * (n - 1)) / n, h = 92, self = this;
    // a tile whose words must be read whole (k.whole: the health score's reason) takes the lines it needs, and every
    // tile grows with it; any other tile's words keep to two lines
    var subL = list.map(function (k) { var l = k.sub ? wrapTokens(tokens(k.sub, 'R', COL.muted), 7, w - 16) : []; return k.whole ? l : l.slice(0, 2); });
    list.forEach(function (k, i) { if (k.whole) h = Math.max(h, 60 + (subL[i].length - 1) * 8.5 + 12 + (gradeOf(k.grade) ? 20 : 0)); });
    if (list.some(function (k) { return k.process; })) h += 12;
    this.need(h + 14);
    var top = this.y;
    list.forEach(function (k, i) {
      var x = self.L + i * (w + gap), g = gradeOf(k.grade);
      self.rect(x, top - h, w, h, COL.white, COL.hair, 0.8);
      self.rect(x, top - 2.5, w, 2.5, g ? COL.grade[g] : COL.accent);
      wrapTokens(tokens(String(k.label).toUpperCase(), 'B', COL.muted), 6.8, w - 16).slice(0, 2).forEach(function (ln, j) { self.drawLine(ln, x + 8, top - 15 - j * 9, 6.8); });
      var v = enc(k.value), vs = 19;
      while (tw(v, vs, 'B') > w - 16 && vs > 11) vs -= 1;
      self.text(v, x + 8, top - 47, vs, 'B', COL.ink);
      subL[i].forEach(function (ln, j) { self.drawLine(ln, x + 8, top - 60 - j * 8.5, 7); });
      if (g && k.process) self.badge(g, x + 8, top - h + 9, { text: 'process grade, not a test of the published total', x: x + 8, y: top - h + 19.5, size: 6 });
      else if (g) self.badge(g, x + 8, top - h + 9);
    });
    this.y = top - h - 14;
  };

  /* ---- figures: vector charts, axes labelled, units named, a source note ---- */
  Doc.prototype.figure = function (c, meta) {
    meta = meta || {};
    var v = vizOf(c);
    if (v) return this.vizFigure(v, meta);        // a chart registry record (below): its own drawer, or its table
    var bars = c.kind === 'bars', nb = bars ? (c.series || []).filter(function (s) { return s && pv(s.value); }).length : 0;
    // more than HBARS_FROM bars (a distribution's 12 bins, 12 groups: final evaluation, 1 Oct 2026) run across, one
    // row a bar, each label whole beside its bar; fewer stand up as before
    var across = nb > HBARS_FROM;
    var h = across ? 16 + nb * HBAR_ROW + (meta.yTitle ? 14 : 4) : bars && (c.series || []).length > 6 ? 220 : 170, self = this;
    var legend = !bars && (c.series || []).filter(function (s) { return s && s.x && s.y && s.name; }).length > 1;
    var noteLines = wrapTokens(tokens(meta.source || '', 'I', COL.muted), 7.5, this.CW);
    var total = 22 + h + 30 + (legend ? 14 : 0) + noteLines.length * 10.5;
    this.need(total + 8);
    this.fig += 1;
    var cap = enc('Figure ' + this.fig + '.  ');
    this.text(cap, this.L, this.y - 9, 9, 'B', COL.accentInk);
    var tl = wrapTokens(tokens(cap1(c.title || c.kind), 'B', COL.ink), 9, this.CW - tw(cap, 9, 'B'));
    if (tl[0]) this.drawLine(tl[0], this.L + tw(cap, 9, 'B'), this.y - 9, 9);
    this.y -= 22;
    var top = this.y, bottom = top - h;
    if (across) this.hbars(c, top, bottom, meta); else if (bars) this.bars(c, top, bottom, meta); else this.xy(c, top, bottom, meta);
    this.y = bottom - 30;
    if (legend) {
      var lx = this.L + 44;
      c.series.forEach(function (s, si) {
        if (!s || !s.x || !s.y || !s.name) return;
        var nm = enc(words(s.name, 40));
        self.line(lx, self.y - 3, lx + 14, self.y - 3, COL.series[si % 4], 1.6);
        self.text(nm, lx + 18, self.y - 5.5, 7.5, 'R', COL.body);
        lx += 18 + tw(nm, 7.5, 'R') + 16;
      });
      this.y -= 14;
    }
    noteLines.forEach(function (ln) { self.drawLine(ln, self.L, self.y - 7.5, 7.5); self.y -= 10.5; });
    this.y -= 10;
  };
  Doc.prototype.bars = function (c, top, bottom, meta) {
    var ss = (c.series || []).filter(function (s) { return s && pv(s.value); }), self = this;
    if (!ss.length) return;
    var vals = ss.map(function (s) { return s.value; });
    var nt = niceTicks(Math.min(0, Math.min.apply(null, vals)), Math.max(0, Math.max.apply(null, vals)), 4);
    var x0 = this.L + 44, x1 = this.L + this.CW, pt = top - 14, pb = bottom + 4;
    var Y = function (v) { return pb + (v - nt.lo) / (nt.hi - nt.lo) * (pt - pb); };
    nt.ticks.forEach(function (t) {
      self.line(x0, Y(t), x1, Y(t), t === 0 ? COL.axis : COL.grid, t === 0 ? 0.8 : 0.5);
      var lb = enc(fmtTick(t, nt.step)); self.text(lb, x0 - 6 - tw(lb, 7, 'R'), Y(t) - 2.5, 7, 'R', COL.muted);
    });
    if (meta.yTitle) this.text(enc(words(meta.yTitle, 90)), this.L, top - 4, 7, 'I', COL.muted);
    var slot = (x1 - x0) / ss.length, bw = Math.min(46, slot * 0.56);
    ss.forEach(function (s, i) {
      var cx = x0 + slot * (i + 0.5), yv = Y(s.value), y0 = Y(0);
      self.rect(cx - bw / 2, Math.min(yv, y0), bw, Math.max(0.5, Math.abs(yv - y0)), COL.series[0]);
      var vl = enc(fmtVal(s.value)); self.text(vl, cx - tw(vl, 7.5, 'B') / 2, Math.max(yv, y0) + 3, 7.5, 'B', COL.ink);
      var lab = enc(String(s.label || s.name || ''));
      while (tw(lab, 7.5, 'R') > slot - 4 && lab.length > 3) lab = lab.slice(0, -2) + '\x85';
      self.text(lab, cx - tw(lab, 7.5, 'R') / 2, pb - 12, 7.5, 'R', COL.body);
    });
    if (meta.xTitle) { var xt = enc(words(meta.xTitle, 90)); this.text(xt, x0 + (x1 - x0) / 2 - tw(xt, 7, 'I') / 2, pb - 24, 7, 'I', COL.muted); }
  };
  // many bars, across: a row a bar, its label beside it (whole in a label column up to a third of the width, else ended
  // with an ellipsis), the value after the bar's end, a zero line when a value is negative
  Doc.prototype.hbars = function (c, top, bottom, meta) {
    var ss = (c.series || []).filter(function (s) { return s && pv(s.value); }), self = this;
    if (!ss.length) return;
    var vals = ss.map(function (s) { return s.value; }), lo = Math.min(0, Math.min.apply(null, vals)), hi = Math.max(0, Math.max.apply(null, vals));
    if (!(hi > lo)) hi = lo + 1;
    var labs = ss.map(function (s) { return enc(String(s.label || s.name || '')); });
    var vls = ss.map(function (s) { return enc(fmtVal(s.value)); });
    var vw = Math.max.apply(null, vls.map(function (v) { return tw(v, 7.5, 'B'); }));
    var LW = Math.min(this.CW * 0.34, Math.max.apply(null, labs.map(function (l) { return tw(l, 7.5, 'R'); })) + 8);
    var x0 = this.L + LW + (lo < 0 ? vw + 6 : 0), x1 = this.L + this.CW - vw - 8;
    var X = function (v) { return x0 + (v - lo) / (hi - lo) * (x1 - x0); }, z = X(0), y = top - 10;
    if (meta.yTitle) { this.text(enc(words(meta.yTitle, 90)), this.L, top - 4, 7, 'I', COL.muted); y -= 10; }
    ss.forEach(function (s, i) {
      var yb = y - (i + 1) * HBAR_ROW + 2.5, xv = X(s.value), w = Math.max(0.5, Math.abs(xv - z)), lab = labs[i];
      while (tw(lab, 7.5, 'R') > LW - 8 && lab.length > 3) lab = lab.slice(0, -2) + '\x85';
      self.text(lab, self.L + LW - 8 - tw(lab, 7.5, 'R'), yb + 1.5, 7.5, 'R', COL.body);
      self.rect(Math.min(xv, z), yb, w, HBAR_ROW - 5, COL.series[0]);
      var vl = vls[i], vx = s.value < 0 ? xv - 4 - tw(vl, 7.5, 'B') : xv + 4;
      self.text(vl, vx, yb + 1.5, 7.5, 'B', COL.ink);
    });
    if (lo < 0) this.line(z, y + 2, z, y - ss.length * HBAR_ROW, COL.axis, 0.6);
  };
  Doc.prototype.xy = function (c, top, bottom, meta) {
    var pts = [], self = this;
    (c.series || []).forEach(function (s) { if (s && s.x && s.y) for (var i = 0; i < Math.min(s.x.length, s.y.length); i++) if (pv(s.x[i]) && pv(s.y[i])) pts.push([s.x[i], s.y[i]]); });
    (c.points || []).forEach(function (p) { if (p && pv(p[0]) && pv(p[1])) pts.push(p); });
    if (!pts.length) return;
    var xs = pts.map(function (p) { return p[0]; }), ys = pts.map(function (p) { return p[1]; });
    // a line's value axis spans its data (lineSpan: an exchange rate of 1.25 to 1.40 drawn from 0 was a flat line,
    // final evaluation, 1 Oct 2026); a scatter keeps its zero
    var line = c.kind !== 'scatter' && !(c.points || []).length;
    if (line) (c.fits || []).forEach(function (f) { if (f && pv(f.y0) && pv(f.y1)) ys.push(f.y0, f.y1); });
    var span = line ? lineSpan(Math.min.apply(null, ys), Math.max.apply(null, ys)) : [Math.min(0, Math.min.apply(null, ys)), Math.max.apply(null, ys)];
    var tx = niceTicks(Math.min.apply(null, xs), Math.max.apply(null, xs), 6), ty = niceTicks(span[0], span[1], 4);
    var x0 = this.L + 44, x1 = this.L + this.CW - 6, pt = top - 14, pb = bottom + 4, L = this.L, R = this.L + this.CW;
    var X = function (v) { return x0 + (v - tx.lo) / (tx.hi - tx.lo) * (x1 - x0); }, Y = function (v) { return pb + (v - ty.lo) / (ty.hi - ty.lo) * (pt - pb); };
    ty.ticks.forEach(function (t) { self.line(x0, Y(t), x1, Y(t), t === 0 ? COL.axis : COL.grid, t === 0 ? 0.8 : 0.5); var lb = enc(fmtTick(t, ty.step)); self.text(lb, x0 - 6 - tw(lb, 7, 'R'), Y(t) - 2.5, 7, 'R', COL.muted); });
    var years = tx.step >= 1 && tx.lo >= 1800 && tx.hi <= 2200;     // an axis of years reads 2016, not 2,016
    tx.ticks.forEach(function (t) {
      self.line(X(t), pb, X(t), pb - 3, COL.axis, 0.6);
      var lb = enc(years ? String(Math.round(t)) : fmtTick(t, tx.step)), w = tw(lb, 7, 'R'), lx = Math.max(L, Math.min(R - w, X(t) - w / 2));
      self.text(lb, lx, pb - 12, 7, 'R', COL.muted);
    });
    this.line(x0, pb, x0, pt, COL.axis, 0.6);
    if (meta.yTitle) this.text(enc(words(meta.yTitle, 90)), this.L, top - 4, 7, 'I', COL.muted);
    if (meta.xTitle) { var xt = enc(words(meta.xTitle, 90)); this.text(xt, x0 + (x1 - x0) / 2 - tw(xt, 7, 'I') / 2, pb - 24, 7, 'I', COL.muted); }
    (c.series || []).forEach(function (s, si) {
      if (!s || !s.x || !s.y) return;
      var d = '', k = 0;
      for (var i = 0; i < Math.min(s.x.length, s.y.length); i++) { if (!pv(s.x[i]) || !pv(s.y[i])) continue; d += f2(X(s.x[i])) + ' ' + f2(Y(s.y[i])) + (k++ ? ' l ' : ' m '); }
      if (k > 1) self.op('q ' + rgb(COL.series[si % 4]) + ' RG 1.4 w 1 j ' + d + 'S Q');
    });
    (c.points || []).forEach(function (p) { if (p && pv(p[0]) && pv(p[1])) self.circle(X(p[0]), Y(p[1]), 1.7, COL.series[0], null, 0, true); });
    (c.fits || []).forEach(function (f) { if (f && pv(f.x0) && pv(f.y0) && pv(f.x1) && pv(f.y1)) self.line(X(f.x0), Y(f.y0), X(f.x1), Y(f.y1), COL.series[2], 1.2); });
  };

  /* ---- tables: numbers right-aligned, cells wrap (never cut), zebra rows, the header again on a new page ---- */
  // a number, a range or a percentage, with at most three words of unit after it ("58,453 CAD", "6.87 percentage points")
  var NUMERIC = /^[-+\u2212\u2013]?[$\u00a3\u20ac\u00a5]?[\d,]*\.?\d+(%|x)?( to [-+\u2212\u2013]?[$\u00a3\u20ac\u00a5]?[\d,]*\.?\d+%?)?( [A-Za-z%]{1,16}){0,3}$/;
  Doc.prototype.table = function (t, meta) {
    meta = meta || {};
    if (meta.fit && !meta.part) return this.fitTable(t, meta);
    var cols = (t.cols || []).map(String), rows = (t.rows || []).map(function (r) { return cols.map(function (_, j) { return String(r[j] === null || r[j] === undefined ? '' : r[j]); }); });
    if (!cols.length) return;
    // meta.size and meta.pad: a chart's table view set to fit (fitTable)
    var n = cols.length, size = meta.size || 8.3, hs = meta.size ? Math.round((meta.size - 1) * 10) / 10 : 7.3, pad = meta.pad || 5, ld = meta.size ? size + 2.7 : 11, hld = meta.size ? hs + 2.2 : 9.5, self = this;
    // a chart's table view (meta.fit): a suppressed cell ("<5") is one of a column's numbers, right-aligned with them
    var num = cols.map(function (_, j) { var a = rows.map(function (r) { return r[j].trim(); }), v = a.filter(function (s) { return s && !(meta.fit && s === '<5'); }); return v.length > 0 ? v.every(function (s) { return NUMERIC.test(s); }) : !!meta.fit && a.indexOf('<5') >= 0; });
    var nat = cols.map(function (c, j) { return Math.max(tw(enc(c), hs, 'B') + 0.4 * c.length, Math.max.apply(null, rows.map(function (r) { return tw(enc(smart(r[j])), size, 'R'); }).concat([12]))) + 2 * pad; });
    var total = nat.reduce(function (a, b) { return a + b; }, 0), W = this.CW, widths;
    if (total <= W) { widths = nat.map(function (w) { return w * W / total; }); if (total < W * 0.6) widths = nat.map(function (w) { return w + (W * 0.6 - total) / n; }); }
    else {
      var fixed = 0, flex = 0; nat.forEach(function (w, j) { if (num[j] || w < 70) fixed += w; else flex += w; });
      widths = nat.map(function (w, j) { return (num[j] || w < 70) ? w : Math.max(60, w * (W - fixed) / (flex || 1)); });
    }
    var tabW = widths.reduce(function (a, b) { return a + b; }, 0);
    if (tabW > W) { widths = widths.map(function (w) { return w * W / tabW; }); tabW = W; }
    var cell = function (s, j, head) { return wrapTokens(tokens(s, head ? 'B' : 'R', head ? COL.ink : COL.body), head ? hs : size, widths[j] - 2 * pad); };
    var headL = cols.map(function (c, j) { return cell(c, j, true); }), headH = Math.max.apply(null, headL.map(function (l) { return l.length; })) * hld + 8;
    var rowL = rows.map(function (r) { return r.map(function (s, j) { return cell(s, j); }); });
    var rowH = rowL.map(function (r) { return Math.max(1, Math.max.apply(null, r.map(function (l) { return l.length; }))) * ld + 6; });
    var capLines = 16, noteH = meta.source ? 20 : 6, whole = capLines + headH + rowH.reduce(function (a, b) { return a + b; }, 0) + noteH;
    if (whole > this.room() && whole < (this.TOP - this.BOT) * 0.6) this.newPage();
    this.need(capLines + headH + (rowH[0] || 0) + 10);
    this.tab += 1;
    var cap = enc('Table ' + this.tab + '.  ');
    this.text(cap, this.L, this.y - 9, 9, 'B', COL.accentInk);
    var tl = wrapTokens(tokens(cap1(t.title || ''), 'B', COL.ink), 9, this.CW - tw(cap, 9, 'B'));
    if (tl[0]) this.drawLine(tl[0], this.L + tw(cap, 9, 'B'), this.y - 9, 9);
    this.y -= capLines;
    var drawHead = function () {
      var top = self.y, x = self.L;
      self.rect(self.L, top - headH, tabW, headH, COL.panel);
      self.line(self.L, top, self.L + tabW, top, COL.ink, 0.8);
      headL.forEach(function (ls, j) { ls.forEach(function (ln, k) { self.drawLine(ln, num[j] ? x + widths[j] - pad - lineW(ln, hs) : x + pad, top - 10 - k * hld, hs); }); x += widths[j]; });
      self.line(self.L, top - headH, self.L + tabW, top - headH, COL.ink, 0.5);
      self.y = top - headH;
    };
    drawHead();
    rowL.forEach(function (r, i) {
      if (self.y - rowH[i] < self.BOT) { self.line(self.L, self.y, self.L + tabW, self.y, COL.ink, 0.5); self.newPage(); drawHead(); }
      var top = self.y, x = self.L;
      if (i % 2 === 1) self.rect(self.L, top - rowH[i], tabW, rowH[i], COL.zebra);
      r.forEach(function (ls, j) { ls.forEach(function (ln, k) { self.drawLine(ln, num[j] ? x + widths[j] - pad - lineW(ln, size) : x + pad, top - ld - k * ld, size); }); x += widths[j]; });
      self.y = top - rowH[i];
      self.line(self.L, self.y, self.L + tabW, self.y, i === rowL.length - 1 ? COL.ink : COL.hair, i === rowL.length - 1 ? 0.8 : 0.4);
    });
    this.y -= 4;
    if (meta.source) { wrapTokens(tokens(meta.source, 'I', COL.muted), 7.5, this.CW).forEach(function (ln) { self.drawLine(ln, self.L, self.y - 7.5, 7.5); self.y -= 10.5; }); }
    this.y -= 12;
  };

  // a chart's table view (spec accessibility.table_view): 8.3 pt, or smaller to 7 pt, its cells padded 3 pt; a
  // heatmap's grid turned a quarter when its figure was (meta.flip: the name of its columns) and it fits so; when even
  // 7 pt is wider than the page, its columns in parts that fit, each repeating the first column (a heatmap's 24
  // months) and numbered in its caption, "(1 of 2)"
  Doc.prototype.fitTable = function (t, meta) {
    var cols = (t.cols || []).map(String), self = this, CW = this.CW, sizes = [8.3, 7.8, 7.4, 7];
    var rows = (t.rows || []).filter(Array.isArray).map(function (r) { return cols.map(function (_, j) { return String(r[j] === null || r[j] === undefined ? '' : r[j]); }); });
    if (!cols.length) return;
    // each column's width at a size, from its widths at 1 pt (measured once: a width is linear in the size): a column
    // of numbers its widest cell, a column of words at most 110 pt (it wraps)
    var units = function (cs, rs) {
      return cs.map(function (c, j) {
        var cells = rs.map(function (r) { return r[j].trim(); }), m = 0;
        cells.forEach(function (x) { m = Math.max(m, tw(enc(smart(x)), 1, 'R')); });
        return { h: tw(enc(c), 1, 'B'), hl: 0.4 * c.length, m: m, num: cells.filter(function (x) { return x && x !== '<5'; }).every(function (x) { return NUMERIC.test(x); }) };
      });
    };
    var width = function (us, size) { return us.map(function (u) { var w = Math.max(u.h * (size - 1) + u.hl, u.m * size, 12) + 6; return u.num ? w : Math.min(w, 110); }); };
    var sum = function (a) { return a.reduce(function (x, y) { return x + y; }, 0); };
    var whole = function (cs, rs) {
      var us = units(cs, rs);
      for (var i = 0; i < sizes.length; i++) if (cs.length < 2 || sum(width(us, sizes[i])) <= CW) { self.table({ title: t.title, cols: cs, rows: rs }, { source: meta.source, fit: true, part: true, size: sizes[i], pad: 3 }); return true; }
      return false;
    };
    if (meta.flip && this.flipped && rows.length && whole([String(meta.flip)].concat(rows.map(function (r) { return r[0]; })), cols.slice(1).map(function (c, j) { return [c].concat(rows.map(function (r) { return r[j + 1]; })); }))) return;
    if (whole(cols, rows)) return;
    var w7 = width(units(cols, rows), 7), parts = [], cur = [], acc = w7[0];
    for (var j = 1; j < cols.length; j++) { if (cur.length && acc + w7[j] > CW) { parts.push(cur); cur = []; acc = w7[0]; } cur.push(j); acc += w7[j]; }
    parts.push(cur);
    parts.forEach(function (p, k) {
      self.table({ title: t.title + ' (' + (k + 1) + ' of ' + parts.length + ')', cols: [cols[0]].concat(p.map(function (x) { return cols[x]; })), rows: rows.map(function (r) { return [r[0]].concat(p.map(function (x) { return r[x]; })); }) },
        { source: k === parts.length - 1 ? meta.source : '', fit: true, part: true, size: 7, pad: 3 });
    });
  };

  /* ---- the chart registry's records (tools/fixtures/viz/spec.json, version 2026-09-30.1) ----
     Five draw kinds (waterfall, heatmap, dot_range, pareto, slope), each drawn from its record alone in the spec's
     light colours; 'table', a kind this writer does not know, or data a drawer cannot read, drawn as the record's
     table (never an empty box). The engine wrote every printed string: a drawer prints them as they are (a label that
     does not fit is cut at a character with an ellipsis, and the figure's table view shows it whole) and formats only
     its axis ticks. A heatmap's tier glyphs (a circle outlined, half filled, filled) are vectors: WinAnsi has none.
     VIZ[kind]: ok(data); lay(record, CW), the geometry measured once (lay.h: the drawing's height); draw(record, top,
     bottom), called on the Doc: every mark and word within [L, L + CW] and [bottom, top]. */
  var VC = { seq: ['#cfe9e4', '#add9d2', '#87c7bc'], falls: ['#d9e7f8', '#b7d1f1', '#94bcea'], rises: ['#f1e2d1', '#e5c8a8', '#d9ae80'],
    cell: '#16232e', supFill: '#eef0f1', supLine: '#b9b5aa', supText: '#56646f', emptyLine: '#e2dfd6', white: '#ffffff',
    total: '#24425c', rise: '#0b7366', fall: '#945400', conn: '#b9b5aa', dot: '#24425c', median: '#945400', bar: '#24425c',
    other: '#56646f', cum: '#945400', ref: '#16232e', k80: '#0b7366', flat: '#56646f', ink: '#16232e', body: '#33424e',
    muted: '#56646f', grid: '#e8e5dd', axis: '#b9b5aa' };
  var LEGACY = { line: 1, bars: 1, scatter: 1 };
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function isObj(x) { return !!x && typeof x === 'object' && !Array.isArray(x); }
  function fin(v) { return typeof v === 'number' && isFinite(v); }
  function pos(v) { return fin(v) && Math.abs(v) <= MAXV; }
  function isStr(v) { return typeof v === 'string'; }
  // a chart's viz record: the record, or the one a page record carries ({type: 'viz', data: record}); null for the
  // analyses' own charts (line, bars, scatter; or no kind and no data), which keep their drawers
  function vizOf(c) {
    if (!isObj(c)) return null;
    if (c.type === 'viz' && isObj(c.data) && isStr(c.data.kind)) return c.data;
    var k = isStr(c.kind) ? c.kind : '';
    return LEGACY[k] || (!k && !isObj(c.data)) ? null : c;
  }
  // a record drawn as its table: kind 'table', a kind with no drawer here, or data its drawer cannot read
  function asTable(r) { var V = Object.prototype.hasOwnProperty.call(VIZ, r.kind) ? VIZ[r.kind] : null; return !V || !isObj(r.data) || !V.ok(r.data); }
  // how many characters of b (encoded) fit in width
  function cutAt(b, size, font, width) {
    var t = font === 'B' ? WB : WR, w = 0;
    for (var i = 0; i < b.length; i++) { var c = b.charCodeAt(i); w += (c >= 32 && c <= 255 ? t[c - 32] : 0) * size / 1000; if (w > width + 1e-9) return i; }
    return b.length;
  }
  // b (encoded) on one line of width: whole, or cut at a character with an ellipsis (always, when more follows)
  function fitB(b, size, font, width, more) {
    if (!more && tw(b, size, font) <= width + 1e-6) return b;
    var e = (font === 'B' ? WB : WR)[0x85 - 32] * size / 1000;
    return width < e ? '' : b.slice(0, cutAt(b, size, font, width - e)).replace(/[\s,;:.\x96\x97-]+$/, '') + '\x85';
  }
  // a label in at most max lines of width, broken at spaces (a word longer than a line at a character), the last
  // line ended with an ellipsis when the rest does not fit; the lines, encoded
  function wrapLabel(s, size, font, width, max) {
    var b = enc(s).replace(/ {2,}/g, ' ').trim(), lines = [];
    while (b && lines.length < max - 1 && tw(b, size, font) > width) {
      var k = Math.max(1, cutAt(b, size, font, width)), sp = b.charAt(k) === ' ' ? k : b.lastIndexOf(' ', k), n = sp > 0 ? sp : k;
      lines.push(b.slice(0, n).replace(/ +$/, ''));
      b = b.slice(n).replace(/^ +/, '');
    }
    if (b) lines.push(fitB(b, size, font, width));
    return lines;
  }
  // wrapped lines cut to max, the last ended with an ellipsis within width (a line of one word too long for it is
  // cut at a character)
  function capLines(lines, size, width, max) {
    if (lines.length <= max) return lines;
    var out = ellipsize(lines.slice(0, max), size, width), last = out[out.length - 1];
    if (last && lineW(last, size) > width) {
      var t = last[last.length - 1], s = t[0];
      out[out.length - 1] = [[{ b: fitB(t.map(function (x) { return x.b; }).join('').replace(/\x85$/, ''), size, s.f, width, true), f: s.f, c: s.c }]];
    }
    return out;
  }
  function tierFill(t, seq) { return t > 0 ? (seq ? VC.seq : VC.rises)[Math.min(3, t) - 1] : t < 0 ? VC.falls[Math.min(3, -t) - 1] : null; }
  // an axis's ticks for [lo, hi] and their labels, the only numbers a drawer formats
  function axis(lo, hi, n, cur) {
    var nt = niceTicks(lo, hi, n), cf = cur === undefined ? null : compactTicks(nt.lo, nt.hi, nt.step, cur);
    nt.lab = nt.ticks.map(function (t) { return enc(cf ? cf(t + 0) : fmtTick(t + 0, nt.step)); });
    nt.w = nt.lab.map(function (b) { return tw(b, 7, 'R'); });
    nt.max = Math.max.apply(null, nt.w);
    return nt;
  }
  // a horizontal axis over width: as many ticks as have room for their labels (5 down to 2)
  function axisFor(lo, hi, width, cur) { var nt; for (var m = 5; m >= 2; m--) { nt = axis(lo, hi, m, cur); if (width / Math.max(1, nt.ticks.length - 1) >= nt.max + 10) break; } return nt; }
  // tick labels under a horizontal axis, each centred on its tick; one that would touch the one before is left out
  Doc.prototype.xTicks = function (nt, X, y) {
    var last = -Infinity, self = this;
    nt.ticks.forEach(function (t, k) { var w = nt.w[k], x = X(t) - w / 2; if (x < last + 4) return; self.text(nt.lab[k], x, y, 7, 'R', VC.muted); last = x + w; });
  };
  // a tier's glyph as a vector (radius 2.4 pt, stroke 0.7 pt, in the cell text's colour): |tier| 1 the outline, 2 the
  // outline with its left half filled, 3 filled
  Doc.prototype.glyph = function (cx, cy, tier) {
    var a = Math.abs(tier), r = 2.4, k = 0.5523 * r;
    if (a === 2) this.op('q ' + rgb(VC.cell) + ' rg ' + f2(cx) + ' ' + f2(cy + r) + ' m ' + f2(cx - k) + ' ' + f2(cy + r) + ' ' + f2(cx - r) + ' ' + f2(cy + k) + ' ' + f2(cx - r) + ' ' + f2(cy) + ' c ' +
      f2(cx - r) + ' ' + f2(cy - k) + ' ' + f2(cx - k) + ' ' + f2(cy - r) + ' ' + f2(cx) + ' ' + f2(cy - r) + ' c h f Q');
    this.circle(cx, cy, r, a === 3 ? VC.cell : null, VC.cell, 0.7);
  };
  // a record's geometry, measured once for its heading, its page and its drawing
  Doc.prototype.lay = function (r) {
    var c = this._lay;
    if (c && c.r === r && c.CW === this.CW) return c.v;
    var v = VIZ[r.kind].lay(r, this.CW);
    this._lay = { r: r, CW: this.CW, v: v };
    return v;
  };
  var VIZ = {
    // one bar per step from its `from` to its `to`: vertical when each gets 44 pt and its text fits over it, else a row
    // per step with its label in a column on the left; the totals in their own colour (their texts bold), a rise and
    // a fall in theirs; a dashed connector from each step's end to the next bar; each text at its bar's end; 0 on the axis
    waterfall: {
      ok: function (d) { return Array.isArray(d.steps) && d.steps.length >= 2 && d.steps.length <= 14 && d.steps.every(function (s) { return isObj(s) && isStr(s.label) && isStr(s.text) && pos(s.value) && pos(s.from) && pos(s.to); }); },
      lay: function (r, CW) {
        var st = r.data.steps, lo = 0, hi = 0;
        st.forEach(function (s) { lo = Math.min(lo, s.from, s.to); hi = Math.max(hi, s.from, s.to); });
        var font = st.map(function (s) { return s.kind === 'total' ? 'B' : UNALLOCATED.test(s.label) ? 'I' : 'R'; }), tb = st.map(function (s) { return enc(s.text); });
        var o = { n: st.length, font: font, tb: tb, tws: tb.map(function (b, k) { return tw(b, 7, font[k]); }) };
        var cur = (/^[+\u2212-]?([$\u20ac\u00a3\u00a5])/.exec(String(st[0].text)) || [])[1] || '';
        var nt = axis(lo, hi, 4, cur), x0 = nt.max + 8, slot = (CW - x0) / o.n;
        if (slot >= 44 && Math.max.apply(null, o.tws) <= slot - 4) {
          o.v = true; o.nt = nt; o.x0 = x0; o.slot = slot; o.down = st.some(function (s) { return s.to < s.from; });
          o.labels = st.map(function (s, k) { return wrapLabel(stepLabel(s.label), 7, font[k], slot - 4, 2); });
          o.h = 12 + 150 + (o.down ? 11 : 0) + 24;
          return o;
        }
        var right = 0, left = 0;
        st.forEach(function (s, k) { if (s.to >= s.from) right = Math.max(right, o.tws[k] + 5); else left = Math.max(left, o.tws[k] + 5); });
        var labW = Math.min(CW * 0.36, Math.max.apply(null, st.map(function (s, k) { return tw(enc(stepLabel(s.label)), 7, font[k]); })) + 8);
        o.labW = labW = Math.max(60, Math.min(labW, CW - left - right - 100));
        o.xa = labW + left;
        o.nt = axisFor(lo, hi, CW - right - o.xa, cur);
        o.xb = Math.min(CW - right, CW - o.nt.max / 2 - 1);
        o.labels = st.map(function (s, k) { return wrapLabel(stepLabel(s.label), 7, font[k], labW - 8, 2); });
        o.rh = o.labels.map(function (ls) { return Math.max(17, ls.length * 8.5 + 8); });
        o.h = o.rh.reduce(function (a, b) { return a + b; }, 0) + 16;
        return o;
      },
      draw: function (r, top, bottom) {
        var o = this.lay(r), st = r.data.steps, self = this, L = this.L, R = L + this.CW, nt = o.nt, i, s, prev = null;
        var col = function (x) { return x.kind === 'total' ? VC.total : x.value > 0 ? VC.rise : VC.fall; };
        if (o.v) {
          var x0 = L + o.x0, pt = top - 12, pb = bottom + 24, yb = pb + (o.down ? 11 : 0), slot = o.slot, bw = Math.min(40, slot * 0.58);
          var Y = function (v) { return yb + (v - nt.lo) / (nt.hi - nt.lo) * (pt - yb); };
          nt.ticks.forEach(function (t, k) { self.line(x0, Y(t), R, Y(t), t === 0 ? VC.axis : VC.grid, t === 0 ? 0.8 : 0.5); self.text(nt.lab[k], x0 - 5 - nt.w[k], Y(t) - 2.5, 7, 'R', VC.muted); });
          for (i = 0; i < o.n; i++) {
            s = st[i];
            var cx = x0 + slot * (i + 0.5), y1 = Y(s.from), y2 = Y(s.to), f = o.font[i];
            if (UNALLOCATED.test(s.label)) this.rect(cx - bw / 2, Math.min(y1, y2) - 2.5, bw, Math.max(0.8, Math.abs(y2 - y1)) + 5, COL.white, COL.muted, 0.8, '2 1.5');
            else this.rect(cx - bw / 2, Math.min(y1, y2), bw, Math.max(0.8, Math.abs(y2 - y1)), col(s));
            if (i < o.n - 1) this.line(cx + bw / 2, y2, cx + slot - bw / 2, y2, VC.conn, 0.6, '2 2');
            this.text(o.tb[i], cx - o.tws[i] / 2, s.to >= s.from ? y2 + 3 : y2 - 8.5, 7, f, VC.ink);
            o.labels[i].forEach(function (b, k) { self.text(b, cx - tw(b, 7, f) / 2, pb - 9 - k * 8.5, 7, f, VC.body); });
          }
          return;
        }
        var xa = L + o.xa, xb = L + o.xb, X = function (v) { return xa + (v - nt.lo) / (nt.hi - nt.lo) * (xb - xa); };
        var y = top, end = top - (o.h - 16);
        nt.ticks.forEach(function (t) { self.line(X(t), top, X(t), end, t === 0 ? VC.axis : VC.grid, t === 0 ? 0.8 : 0.5); });
        this.xTicks(nt, X, end - 10);
        for (i = 0; i < o.n; i++) {
          s = st[i];
          var mid = y - o.rh[i] / 2, by = mid - 4.5, a = X(Math.min(s.from, s.to)), e = X(Math.max(s.from, s.to)), ls = o.labels[i], fo = o.font[i];
          if (UNALLOCATED.test(s.label)) this.rect((a + e) / 2 - 3, by - 1, Math.max(6, e - a), 11, COL.white, COL.muted, 0.8, '2 1.5');
          else this.rect(a, by, Math.max(0.8, e - a), 9, col(s));
          if (prev) this.line(X(prev.to), prev.by, X(prev.to), by + 9, VC.conn, 0.6, '2 2');
          this.text(o.tb[i], s.to >= s.from ? X(s.to) + 3 : X(s.to) - 3 - o.tws[i], mid - 2.5, 7, fo, VC.ink);
          ls.forEach(function (b, k) { self.text(b, L, mid + (ls.length - 1) * 4.25 - 2.5 - k * 8.5, 7, fo, VC.body); });
          prev = { to: s.to, by: by };
          y -= o.rh[i];
        }
      }
    },
    // a grid of cells, each filled by its tier, with the engine's text (when it fits the cell less 3 pt) and the tier's
    // glyph after it (when that fits too; the glyph alone when the text does not); column labels over it (a month's
    // YYYY-MM as its name, the year over it), row labels to its left, turned a quarter when that cuts fewer column
    // labels; the legend under it from the engine's texts, and, when cells were suppressed, the engine's reason
    heatmap: {
      ok: function (d) {
        var R = d.rows, C = d.cols;
        var g = function (m, f) { return Array.isArray(m) && m.length === R.length && m.every(function (x) { return Array.isArray(x) && x.length === C.length && x.every(f); }); };
        return Array.isArray(R) && Array.isArray(C) && R.length >= 1 && R.length <= 12 && C.length >= 1 && C.length <= 24 && R.every(isStr) && C.every(isStr) &&
          g(d.text, isStr) && g(d.tier, function (t) { return t === Math.round(t) && t >= -3 && t <= 3; });
      },
      lay: function (r, CW) {
        var d = r.data;
        var ori = function (T) {
          var cl = T ? d.rows : d.cols, o = { T: T, rl: T ? d.cols : d.rows, cl: cl, rn: String((T ? d.col_label : d.row_label) || ''), cn: String((T ? d.row_label : d.col_label) || ''), cut: 0 };
          o.rlW = Math.min(CW * 0.3, Math.max(tw(enc(o.rn), 7, 'B'), Math.max.apply(null, o.rl.map(function (s) { return tw(enc(s), 7, 'R'); })))) + 6;
          o.cw = Math.min(56, (CW - o.rlW) / cl.length);
          var fits = cl.every(function (s) { return tw(enc(s), 7, 'R') <= o.cw - 2; });
          o.ym = !fits && cl.every(function (s) { return /^\d{4}-(0[1-9]|1[0-2])$/.test(s); });
          o.heads = fits ? cl.map(function (s) { return [enc(s)]; })
            : o.ym ? cl.map(function (s, k) { return [k === 0 || cl[k - 1].slice(0, 4) !== s.slice(0, 4) ? s.slice(0, 4) : '', fitB(MON[Number(s.slice(5)) - 1], 7, 'R', o.cw - 2)]; })
              : cl.map(function (s) { var ls = wrapLabel(s, 7, 'R', o.cw - 2, 2); if (/\x85$/.test(ls[ls.length - 1]) && !/\u2026$/.test(s)) o.cut += 1; return ls; });
          o.hl = Math.max.apply(null, o.heads.map(function (x) { return x.length; }));
          return o;
        };
        var o = ori(false);
        if (o.cut) { var t = ori(true); if (t.cut < o.cut) o = t; }
        o.seq = d.scale !== 'diverging'; o.nr = o.rl.length; o.nc = o.cl.length;
        var lg = (Array.isArray(d.legend) ? d.legend : []).filter(function (x) { return isObj(x) && isStr(x.text) && x.text && x.tier === Math.round(x.tier) && x.tier !== 0 && Math.abs(x.tier) <= 3; });
        var lx = 0;
        o.lr = lg.length ? 1 : 0;
        o.legend = lg.map(function (x) {
          var b = fitB(enc(x.text), 7, 'R', CW - 24), w = 21 + tw(b, 7, 'R');
          if (lx > 0 && lx + w > CW) { lx = 0; o.lr += 1; }
          var it = { t: x.tier, b: b, x: lx, row: o.lr - 1 };
          lx += w + 12;
          return it;
        });
        var sp = r.suppressed;
        o.sup = isObj(sp) && sp.cells > 0 && isStr(sp.why) && sp.why ? wrapTokens(tokens(sp.why, 'I', VC.muted), 7, CW) : [];
        o.headH = 13 + o.hl * 8.5;
        o.h = o.headH + o.nr * 14 + (o.lr ? 7 + o.lr * 11 : 0) + (o.sup.length ? 3 + o.sup.length * 9.5 : 0) + 2;
        return o;
      },
      draw: function (r, top, bottom) {
        var o = this.lay(r), d = r.data, self = this, L = this.L, gx = L + o.rlW, cw = o.cw, gw = cw * o.nc, i, j;
        var at = function (m, a, b) { return o.T ? m[b][a] : m[a][b]; };
        var cn = fitB(enc(o.cn), 7, 'B', gw);
        if (cn) this.text(cn, gx + (gw - tw(cn, 7, 'B')) / 2, top - 7.5, 7, 'B', VC.muted);
        var lastYear = -Infinity;
        o.heads.forEach(function (ls, k) {
          var x = gx + k * cw;
          ls.forEach(function (b, n) {
            if (!b) return;
            var yy = top - 17 - (o.hl - ls.length + n) * 8.5, w = tw(b, 7, 'R');
            if (o.ym && n === 0) { var xx = Math.min(x + 1, gx + gw - w); if (xx < lastYear + 3) return; self.text(b, xx, yy, 7, 'R', VC.muted); lastYear = xx + w; return; }
            self.text(b, x + (cw - w) / 2, yy, 7, 'R', VC.muted);
          });
        });
        var rn = fitB(enc(o.rn), 7, 'B', o.rlW - 6);
        if (rn) this.text(rn, L, top - 17 - (o.hl - 1) * 8.5, 7, 'B', VC.muted);
        var gt = top - o.headH;
        for (i = 0; i < o.nr; i++) {
          var yt = gt - i * 14;
          this.text(fitB(enc(o.rl[i]), 7, 'R', o.rlW - 6), L, yt - 9.5, 7, 'R', VC.body);
          for (j = 0; j < o.nc; j++) {
            var x = gx + j * cw, t = String(at(d.text, i, j)), ti = at(d.tier, i, j), fill = tierFill(ti, o.seq), tb = enc(t), op0 = this.pg.ops.length;
            if (fill) this.rect(x + 0.5, yt - 13.5, cw - 1, 13, fill);
            else if (t === '<5') this.rect(x + 0.5, yt - 13.5, cw - 1, 13, VC.supFill, VC.supLine, 0.5, '2 2');
            else this.rect(x + 0.5, yt - 13.5, cw - 1, 13, VC.white, VC.emptyLine, 0.5, t ? null : '2 2');
            // the text at 6.5 pt when it fits the cell less 3 pt, its glyph after it when that fits too; else the glyph
            var w = t ? tw(tb, 6.5, 'R') : 0, fits = !!t && w <= cw - 3, g = ti && w + 7.6 <= cw - 3 ? 7.6 : 0;
            if (fits) {
              var x1 = x + (cw - w - g) / 2;
              this.text(tb, x1, yt - 9.4, 6.5, 'R', t === '<5' ? VC.supText : VC.cell);
              if (g) this.glyph(x1 + w + 4.8, yt - 7.1, ti);
            } else if (ti) this.glyph(x + cw / 2, yt - 7, ti);
            if (this.tt) this.tt.cells.push({ r: [x, yt - 14, x + cw, yt], op0: op0, op1: this.pg.ops.length, s: t, text: fits });
          }
        }
        var ly = gt - o.nr * 14 - 7;
        o.legend.forEach(function (it) {
          var x = L + it.x, yb = ly - 8 - it.row * 11;
          self.rect(x, yb - 1, 9, 7, tierFill(it.t, o.seq));
          self.glyph(x + 14.8, yb + 2.4, it.t);
          self.text(it.b, x + 21, yb, 7, 'R', VC.body);
        });
        var sy = ly + 7 - (o.lr ? 7 + o.lr * 11 : 0) - 3;
        o.sup.forEach(function (ln, k) { self.drawLine(ln, L, sy - 7 - k * 9.5, 7); });
      }
    },
    // a row per group: a line from lo to hi, a dot at the average, a short tick at the median, the average's text over
    // the dot, "lo to hi" at the right, the group's rows in its label; a key over it and the axis under it
    dot_range: {
      ok: function (d) { return Array.isArray(d.rows) && d.rows.length >= 1 && d.rows.length <= 12 && d.rows.every(function (q) { return isObj(q) && isStr(q.label) && pos(q.center) && pos(q.lo) && pos(q.hi) && pos(q.median) && isObj(q.texts) && ['n', 'center', 'lo', 'hi'].every(function (k) { return isStr(q.texts[k]); }); }); },
      lay: function (r, CW) {
        var rows = r.data.rows, lo = Infinity, hi = -Infinity, o = {};
        rows.forEach(function (q) { lo = Math.min(lo, q.lo, q.center, q.median); hi = Math.max(hi, q.hi, q.center, q.median); });
        o.suf = rows.map(function (q) { return enc(' (' + q.texts.n + ')'); });
        o.rng = rows.map(function (q) { return enc(q.texts.lo + ' to ' + q.texts.hi); });
        o.cen = rows.map(function (q) { return enc(q.texts.center); });
        o.labW = Math.min(CW * 0.32, Math.max.apply(null, rows.map(function (q, k) { return tw(enc(q.label) + o.suf[k], 7, 'R'); }))) + 8;
        o.rtW = Math.min(CW * 0.3, Math.max.apply(null, o.rng.map(function (b) { return tw(b, 7, 'R'); }))) + 8;
        o.rng = o.rng.map(function (b) { return fitB(b, 7, 'R', o.rtW - 8); });
        o.names = rows.map(function (q, k) { return fitB(enc(q.label), 7, 'R', o.labW - 8 - tw(o.suf[k], 7, 'R')) + o.suf[k]; });
        o.names = o.names.map(function (b) { return fitB(b, 7, 'R', o.labW - 8); });
        o.xa = o.labW + 4; o.xb = CW - o.rtW - 4;
        o.nt = axisFor(lo, hi, o.xb - o.xa);
        o.h = 14 + rows.length * 20 + 16;
        return o;
      },
      draw: function (r, top, bottom) {
        var o = this.lay(r), rows = r.data.rows, self = this, L = this.L, R = L + this.CW, nt = o.nt, xa = L + o.xa, xb = L + o.xb;
        var X = function (v) { return xa + (v - nt.lo) / (nt.hi - nt.lo) * (xb - xa); };
        var ky = top - 8, kx = xa, kw = function (s) { return tw(enc(s), 7, 'R'); };
        this.circle(kx + 2.6, ky + 2.4, 2.6, VC.dot); this.text(enc('Average'), kx + 8, ky, 7, 'R', VC.muted); kx += 20 + kw('Average');
        this.line(kx + 1, ky - 1.2, kx + 1, ky + 6, VC.median, 1.2); this.text(enc('Median'), kx + 6, ky, 7, 'R', VC.muted); kx += 18 + kw('Median');
        this.line(kx, ky + 2.4, kx + 12, ky + 2.4, VC.dot, 1.2); this.text(enc('95% range'), kx + 16, ky, 7, 'R', VC.muted);
        var y0 = top - 14, end = y0 - rows.length * 20;
        nt.ticks.forEach(function (t) { self.line(X(t), y0, X(t), end, t === 0 ? VC.axis : VC.grid, 0.5); });
        this.xTicks(nt, X, end - 11);
        rows.forEach(function (q, i) {
          var mid = y0 - i * 20 - 12, w = tw(o.cen[i], 7, 'R');
          self.text(o.names[i], L, mid - 2.5, 7, 'R', VC.body);
          self.line(X(q.lo), mid, X(q.hi), mid, VC.dot, 1.2);
          self.line(X(q.median), mid - 3.5, X(q.median), mid + 3.5, VC.median, 1.2);
          self.circle(X(q.center), mid, 2.6, VC.dot);
          self.text(o.cen[i], Math.max(L + o.labW, Math.min(R - o.rtW - w, X(q.center) - w / 2)), mid + 5, 7, 'R', VC.muted);
          self.text(o.rng[i], R - o.rtW + 6, mid - 2.5, 7, 'R', VC.muted);
        });
      }
    },
    // the levels largest first, then 'other': vertical bars with the running share as a line on a right-hand 0 to 100%
    // axis when every bar has room, else a row a level with the running share in its own panel and its text at the
    // right; the 80% reference dashed; bar k80.k marked "k80", or, beyond the bars, k80's text under the chart
    pareto: {
      ok: function (d) {
        var bar = function (b) { return isObj(b) && isStr(b.label) && pos(b.value) && b.value >= 0 && isStr(b.text) && fin(b.cum_pct) && isStr(b.cum_text); };
        return Array.isArray(d.bars) && d.bars.length >= 1 && d.bars.length <= 20 && d.bars.every(bar) && (d.other === null || d.other === undefined || bar(d.other)) && isObj(d.k80) && isStr(d.k80.text) && fin(d.k80.k);
      },
      lay: function (r, CW) {
        var d = r.data, all = d.bars.concat(d.other ? [d.other] : []), o = { all: all, n: all.length };
        o.kk = d.k80.k >= 1 && d.k80.k <= d.bars.length ? d.k80.k : 0;
        o.tb = all.map(function (b) { return enc(b.text); }); o.cb = all.map(function (b) { return enc(b.cum_text); });
        o.tws = o.tb.map(function (b) { return tw(b, 7, 'R'); }); o.cws = o.cb.map(function (b) { return tw(b, 7, 'R'); });
        o.max = Math.max.apply(null, all.map(function (b) { return b.value; })) || 1;
        o.note = o.kk ? [] : wrapTokens(tokens(d.k80.text, 'R', VC.body), 7.5, CW);
        var noteH = o.note.length ? 4 + o.note.length * 10 : 0, nt = axis(0, o.max, 4), x0 = nt.max + 8, slot = (CW - x0 - 26) / o.n;
        var labels = all.map(function (b) { return wrapLabel(b.label, 7, 'R', slot - 4, 2); });
        if (slot >= 40 && Math.max.apply(null, o.tws) <= slot - 4 && labels.every(function (ls) { return !/\x85$/.test(ls[ls.length - 1]); })) {
          o.v = true; o.nt = nt; o.x0 = x0; o.slot = slot; o.labels = labels; o.noteH = noteH;
          o.h = 22 + 150 + 24 + noteH;
          return o;
        }
        o.labW = Math.min(CW * 0.3, Math.max.apply(null, all.map(function (b) { return tw(enc(b.label), 7, 'R'); }))) + 8;
        o.labels = all.map(function (b) { return fitB(enc(b.label), 7, 'R', o.labW - 8); });
        o.ctW = Math.max.apply(null, o.cws) + 4;
        o.px1 = CW - o.ctW - 8; o.px0 = o.px1 - 72;
        o.xa = o.labW; o.xb = o.px0 - 12 - Math.max.apply(null, o.tws) - (o.kk ? 4 + tw('k80', 7, 'B') : 0);
        o.noteH = noteH;
        o.h = 12 + o.n * 13 + 12 + noteH;
        return o;
      },
      draw: function (r, top, bottom) {
        var o = this.lay(r), d = r.data, self = this, L = this.L, R = L + this.CW, all = o.all, n = o.n, pts = '', k80 = enc('k80');
        var isOther = function (k) { return !!d.other && k === n - 1; };
        if (o.v) {
          var x0 = L + o.x0, x1 = R - 26, pt = top - 22, pb = bottom + 24 + o.noteH, nt = o.nt, slot = o.slot, bw = Math.min(40, slot * 0.58);
          var Y = function (v) { return pb + (v - nt.lo) / (nt.hi - nt.lo) * (pt - pb); }, P = function (p) { return pb + p / 100 * (pt - pb); };
          nt.ticks.forEach(function (t, k) { self.line(x0, Y(t), x1, Y(t), t === 0 ? VC.axis : VC.grid, t === 0 ? 0.8 : 0.5); self.text(nt.lab[k], x0 - 5 - nt.w[k], Y(t) - 2.5, 7, 'R', VC.muted); });
          [0, 50, 80, 100].forEach(function (p) { var b = enc(p + '%'); self.line(x1, P(p), x1 + 2, P(p), VC.axis, 0.5); self.text(b, x1 + 4, P(p) - 2.5, 7, 'R', VC.muted); });
          this.line(x0, P(80), x1, P(80), VC.ref, 0.6, '3 2');
          all.forEach(function (b, k) {
            var cx = x0 + slot * (k + 0.5), yv = Y(b.value), y0 = Y(0);
            self.rect(cx - bw / 2, y0, bw, Math.max(0.8, yv - y0), isOther(k) ? VC.other : VC.bar);
            self.text(o.tb[k], cx - o.tws[k] / 2, yv + 3, 7, 'R', VC.ink);
            if (k + 1 === o.kk) self.text(k80, cx - tw(k80, 7, 'B') / 2, yv + 11.5, 7, 'B', VC.k80);
            o.labels[k].forEach(function (lb, j) { self.text(lb, cx - tw(lb, 7, 'R') / 2, pb - 9 - j * 8.5, 7, 'R', VC.body); });
            pts += f2(cx) + ' ' + f2(P(b.cum_pct)) + (k ? ' l ' : ' m ');
          });
          this.op('q ' + rgb(VC.cum) + ' RG 1.2 w 1 j ' + pts + 'S Q');
          all.forEach(function (b, k) { self.circle(x0 + slot * (k + 0.5), P(b.cum_pct), 1.8, VC.cum); });
        } else {
          var xa = L + o.xa, xb = L + o.xb, px0 = L + o.px0, px1 = L + o.px1, top0 = top - 12, end = top0 - n * 13;
          var X = function (v) { return xa + v / o.max * (xb - xa); }, P2 = function (p) { return px0 + p / 100 * (px1 - px0); };
          var b0 = enc('0%'), b1 = enc('100%'), b8 = enc('80%');
          this.text(b0, px0, top - 8, 7, 'R', VC.muted);
          this.text(b1, px1 - tw(b1, 7, 'R'), top - 8, 7, 'R', VC.muted);
          this.line(px0, top0, px0, end, VC.axis, 0.5);
          this.line(P2(80), top0, P2(80), end, VC.ref, 0.6, '3 2');
          this.text(b8, P2(80) - tw(b8, 7, 'R') / 2, end - 9, 7, 'R', VC.muted);
          all.forEach(function (b, k) {
            var mid = top0 - k * 13 - 6.5, w = Math.max(0.8, X(b.value) - xa);
            self.text(o.labels[k], L, mid - 2.5, 7, 'R', VC.body);
            self.rect(xa, mid - 4, w, 8, isOther(k) ? VC.other : VC.bar);
            self.text(o.tb[k], xa + w + 3, mid - 2.5, 7, 'R', VC.ink);
            if (k + 1 === o.kk) self.text(k80, xa + w + 3 + o.tws[k] + 4, mid - 2.5, 7, 'B', VC.k80);
            self.text(o.cb[k], R - o.cws[k], mid - 2.5, 7, 'R', VC.muted);
            pts += f2(P2(b.cum_pct)) + ' ' + f2(mid) + (k ? ' l ' : ' m ');
          });
          this.op('q ' + rgb(VC.cum) + ' RG 1.2 w 1 j ' + pts + 'S Q');
          all.forEach(function (b, k) { self.circle(P2(b.cum_pct), top0 - k * 13 - 6.5, 1.8, VC.cum); });
        }
        var ny = bottom + o.noteH - 7;
        o.note.forEach(function (ln, k) { self.drawLine(ln, L, ny - k * 10, 7.5); });
      }
    },
    // two vertical axes (the two windows) on one scale, a line a row coloured by its direction; "label a_text" at the
    // left end and "b_text change_text" at the right, each end's labels spread 10 pt apart with a leader line
    slope: {
      ok: function (d) { return Array.isArray(d.rows) && d.rows.length >= 1 && d.rows.length <= 12 && isStr(d.a_label) && isStr(d.b_label) && d.rows.every(function (q) { return isObj(q) && isStr(q.label) && pos(q.a) && pos(q.b) && isStr(q.a_text) && isStr(q.b_text) && isStr(q.change_text); }); },
      lay: function (r, CW) {
        var d = r.data, rows = d.rows, o = {};
        o.at = rows.map(function (q) { return enc(' ' + q.a_text); }); o.bt = rows.map(function (q) { return enc(q.b_text + ' '); }); o.ct = rows.map(function (q) { return enc(q.change_text); });
        var atW = Math.max.apply(null, o.at.map(function (b) { return tw(b, 7, 'R'); }));
        var lw = Math.max.apply(null, rows.map(function (q, k) { return tw(enc(q.label), 7, 'R') + tw(o.at[k], 7, 'R'); }));
        var rw = Math.max.apply(null, rows.map(function (q, k) { return tw(o.bt[k], 7, 'R') + tw(o.ct[k], 7, 'B'); }));
        o.rW = rw + 12;
        o.lW = Math.max(atW + 12, Math.min(lw + 12, CW * 0.38, CW - 100 - o.rW));
        o.names = rows.map(function (q, k) { return fitB(enc(q.label), 7, 'R', o.lW - 12 - tw(o.at[k], 7, 'R')); });
        o.an = fitB(enc(d.a_label), 7, 'B', CW / 2 - 4); o.bn = fitB(enc(d.b_label), 7, 'B', CW / 2 - 4);
        o.h = Math.max(120, rows.length * 12 + 40);
        return o;
      },
      draw: function (r, top, bottom) {
        var o = this.lay(r), rows = r.data.rows, self = this, L = this.L, R = L + this.CW, xa = L + o.lW, xb = R - o.rW, lo = Infinity, hi = -Infinity;
        rows.forEach(function (q) { lo = Math.min(lo, q.a, q.b); hi = Math.max(hi, q.a, q.b); });
        var pad = (hi - lo) * 0.05 || Math.abs(hi) * 0.05 || 1;
        lo -= pad; hi += pad;
        var pt = top - 18, pb = bottom + 6, Y = function (v) { return pb + (v - lo) / (hi - lo) * (pt - pb); };
        var aw = tw(o.an, 7, 'B'), bw = tw(o.bn, 7, 'B');
        this.text(o.an, Math.max(L, xa - aw / 2), top - 8, 7, 'B', VC.muted);
        this.text(o.bn, Math.min(R - bw, xb - bw / 2), top - 8, 7, 'B', VC.muted);
        this.line(xa, pt + 4, xa, pb, VC.axis, 0.6); this.line(xb, pt + 4, xb, pb, VC.axis, 0.6);
        var dir = function (q) { return q.b > q.a ? VC.rise : q.b < q.a ? VC.fall : VC.flat; };
        rows.forEach(function (q) { var c = dir(q); self.line(xa, Y(q.a), xb, Y(q.b), c, 1.3); self.circle(xa, Y(q.a), 2.2, c); self.circle(xb, Y(q.b), 2.2, c); });
        var spread = function (ys) {
          var a = ys.map(function (y, i) { return { i: i, y: y, ly: y }; }).sort(function (p, q) { return (q.y - p.y) || (p.i - q.i); }), k, out = [];
          for (k = 1; k < a.length; k++) if (a[k].ly > a[k - 1].ly - 10) a[k].ly = a[k - 1].ly - 10;
          if (a[a.length - 1].ly < pb + 3) { a[a.length - 1].ly = pb + 3; for (k = a.length - 2; k >= 0; k--) if (a[k].ly < a[k + 1].ly + 10) a[k].ly = a[k + 1].ly + 10; }
          a.forEach(function (p) { out[p.i] = p.ly; });
          return out;
        };
        var la = spread(rows.map(function (q) { return Y(q.a); })), lb = spread(rows.map(function (q) { return Y(q.b); }));
        rows.forEach(function (q, i) {
          var ya = Y(q.a), yb = Y(q.b), w1 = tw(o.names[i], 7, 'R'), w2 = tw(o.at[i], 7, 'R'), w3 = tw(o.bt[i], 7, 'R');
          if (Math.abs(la[i] - ya) > 1) self.line(xa - 3, ya, xa - 7, la[i], VC.axis, 0.5);
          if (o.names[i]) self.text(o.names[i], xa - 8 - w2 - w1, la[i] - 2.5, 7, 'R', VC.body);
          self.text(o.at[i], xa - 8 - w2, la[i] - 2.5, 7, 'R', VC.muted);
          if (Math.abs(lb[i] - yb) > 1) self.line(xb + 3, yb, xb + 7, lb[i], VC.axis, 0.5);
          self.text(o.bt[i], xb + 8, lb[i] - 2.5, 7, 'R', VC.ink);
          self.text(o.ct[i], xb + 8 + w3, lb[i] - 2.5, 7, 'B', dir(q));
        });
      }
    }
  };
  // the figure around a record's drawing, measured first so it stays whole on its page: "Figure n." and the title (2
  // lines at most), the subtitle (2), the drawing, the summary (the second half of its accessible name: spec
  // accessibility) and the source note; a record drawn as its table is measured as its summary and the table's start
  Doc.prototype.vizMeasure = function (r, meta) {
    var c = this._vm;
    if (c && c.r === r && c.meta === meta && c.CW === this.CW && c.fig === this.fig) return c.m;
    var CW = this.CW, m = { table: asTable(r) };
    if (m.table) m.total = (r.summary ? this.linesHeight(String(r.summary), 8.5, CW, 12) : 0) + 70;
    else {
      m.capB = enc('Figure ' + (this.fig + 1) + '.  '); m.capW = tw(m.capB, 9, 'B');
      m.tl = capLines(wrapTokens(tokens(cap1(r.title || r.chart || r.kind), 'B', COL.ink), 9, CW - m.capW), 9, CW - m.capW, 2);
      m.sub = r.subtitle ? capLines(wrapTokens(tokens(String(r.subtitle), 'I', COL.muted), 7.5, CW), 7.5, CW, 2) : [];
      m.lay = this.lay(r);
      m.sum = r.summary ? wrapTokens(tokens(String(r.summary), 'R', COL.body), 8, CW) : [];
      m.notes = wrapTokens(tokens(meta.source || '', 'I', COL.muted), 7.5, CW);
      m.head = 9 + (m.tl.length - 1) * 11.5 + 4 + (m.sub.length ? 8 + (m.sub.length - 1) * 10 + 3 : 0) + 5;
      m.total = m.head + m.lay.h + 6 + (m.sum.length ? m.sum.length * 11 + 2 : 0) + m.notes.length * 10.5;
    }
    this._vm = { r: r, meta: meta, CW: CW, fig: this.fig, m: m };
    return m;
  };
  // the room a chart block asks of its page, so its heading keeps with it: a viz figure's whole height, else the 250 pt
  // the analyses' charts have always asked
  Doc.prototype.figureHeight = function (b) { var v = vizOf(b.chart); return v ? this.vizMeasure(v, b).total + 8 : 250; };
  Doc.prototype.vizFigure = function (r, meta) {
    var m = this.vizMeasure(r, meta);
    if (m.table) return this.vizTable(r, meta);
    this.need(m.total + 8);
    this.flipped = r.kind === 'heatmap' && !!m.lay.T;      // its table view (next) turns the same way
    var self = this, L = this.L, y0 = this.y, y;
    // opts.trace (the checks): the figure's box, its content-stream operators, its words and its heatmap cells
    var tr = this.tr ? { fig: this.fig + 1, kind: r.kind, chart: String(r.chart || ''), title: String(r.title || ''), page: this.pages.length - 1, op0: this.pg.ops.length, texts: [], cells: [] } : null;
    this.tt = tr;
    this.fig += 1;
    this.text(m.capB, L, y0 - 9, 9, 'B', COL.accentInk);
    m.tl.forEach(function (ln, i) { self.drawLine(ln, L + m.capW, y0 - 9 - i * 11.5, 9); });
    y = y0 - 9 - (m.tl.length - 1) * 11.5 - 4;
    m.sub.forEach(function (ln, i) { self.drawLine(ln, L, y - 8 - i * 10, 7.5); });
    if (m.sub.length) y -= 8 + (m.sub.length - 1) * 10 + 3;
    var top = y - 5, bottom = top - m.lay.h;
    VIZ[r.kind].draw.call(this, r, top, bottom);
    y = bottom - 6;
    m.sum.forEach(function (ln, i) { self.drawLine(ln, L, y - 8 - i * 11, 8); });
    if (m.sum.length) y -= m.sum.length * 11 + 2;
    m.notes.forEach(function (ln, i) { self.drawLine(ln, L, y - 7.5 - i * 10.5, 7.5); });
    y -= m.notes.length * 10.5;
    if (tr) {
      tr.op1 = this.pg.ops.length; tr.box = [L, y, L + this.CW, y0]; tr.plot = [L, bottom, L + this.CW, top];
      tr.layout = r.kind === 'heatmap' ? (m.lay.T ? 'transposed' : 'grid') : r.kind === 'waterfall' || r.kind === 'pareto' ? (m.lay.v ? 'vertical' : 'horizontal') : 'wide';
      this.tr.push(tr);
      this.tt = null;
    }
    this.y = y - 10;
  };
  // a record drawn as its table (kind 'table', a kind with no drawer here, or data its drawer cannot read; the note
  // says which): its summary above it and its title as the caption (spec draw_kinds.table), never an empty box
  Doc.prototype.vizTable = function (r, meta) {
    var t = isObj(r.table) ? r.table : {}, cols = Array.isArray(t.cols) ? t.cols : [], rows = Array.isArray(t.rows) ? t.rows.filter(Array.isArray) : [];
    // the summary keeps with the table: with all of it when the table is short enough to be moved whole (Doc.table
    // moves one under 60% of a page), else with its first rows
    var est = 16 + 20 + rows.length * 24 + 30, page = this.TOP - this.BOT;
    if (r.summary) { this.need(this.linesHeight(String(r.summary), 8.5, this.CW, 12) + (est < page * 0.6 ? est : 80)); this.para(String(r.summary), { x: this.L, width: this.CW, size: 8.5, lead: 12, after: 4 }); }
    var p0 = this.pages.length - 1;
    if (cols.length) this.table({ title: r.title || '', cols: cols, rows: rows }, { source: meta.source, fit: true });
    else this.callout('', cap1(words(r.title || r.kind || 'A chart', 100)) + ': this chart could not be drawn, and it carries no table.' + (meta.source ? ' ' + meta.source : ''), { dash: '3 2', fill: COL.white, size: 9 });
    if (this.tr) this.tr.push({ as: 'table', table: cols.length ? this.tab : null, kind: String(r.kind || ''), chart: String(r.chart || ''), title: String(r.title || ''), page: p0 });
  };

  /* ---- scenario cards (engine-computed), in rows of up to three; or the engine's reason for none ---- */
  Doc.prototype.cards = function (list, note) {
    var self = this, per = Math.min(3, list.length), gap = 10, w = (this.CW - gap * (per - 1)) / per;
    for (var r0 = 0; r0 < list.length; r0 += per) {
      var row = list.slice(r0, r0 + per);
      var shaped = row.map(function (k) {
        return { k: k, lab: wrapTokens(tokens(k.unit || '', 'R', COL.muted), 7.5, w - 20).slice(0, 4),
          asm: wrapTokens(tokens(k.assumption ? 'Assumes ' + k.assumption : '', 'R', COL.body), 8, w - 20).slice(0, 4) };
      });
      var h = 58 + Math.max.apply(null, shaped.map(function (s) { return s.lab.length * 9.5 + (s.asm.length ? 6 + s.asm.length * 10.5 : 0); })) + 24;
      this.need(h + 12);
      var top = this.y;
      shaped.forEach(function (s, i) {
        var k = s.k, x = self.L + i * (w + gap), strong = !!k.strong, yy;
        self.rect(x, top - h, w, h, strong ? COL.panel : COL.white, COL.hair, 0.8);
        self.rect(x, top - 3, w, 3, strong ? COL.accent : COL.hair);
        wrapTokens(tokens(String(k.name).toUpperCase(), 'B', strong ? COL.accentInk : COL.muted), 7.5, w - 20).slice(0, 1).forEach(function (ln) { self.drawLine(ln, x + 10, top - 17, 7.5); });
        var v = enc(k.value), vs = 20;
        // 20 pt down to 11, and on to 8 only for a figure too wide at 11 (a long unit), never past the card's edge
        while (tw(v, vs, 'B') > w - 20 && vs > 8) vs -= 1;
        self.text(v, x + 10, top - 42, vs, 'B', COL.ink);
        yy = top - 56;
        s.lab.forEach(function (ln) { self.drawLine(ln, x + 10, yy, 7.5); yy -= 9.5; });
        if (s.asm.length) { yy -= 6; s.asm.forEach(function (ln) { self.drawLine(ln, x + 10, yy, 8); yy -= 10.5; }); }
        if (gradeOf(k.grade)) self.badge(k.grade, x + 10, top - h + 9);
        else if (gradeOf(k.parent)) self.fromBadge(k.parent, x + 10, top - h + 9);
        else if (k.tag) self.tagBadge(k.tag, x + 10, top - h + 9);
      });
      this.y = top - h - 10;
    }
    if (note) this.para(note, { x: this.L, width: this.CW, size: 7.5, lead: 10.5, font: 'I', color: COL.muted, after: 10 });
  };
  Doc.prototype.noScenarios = function (reason) {
    this.callout('No scenarios', reason || 'The engine computed no scenario figures for this file.', { dash: '3 2', fill: COL.white, labelColor: COL.muted, size: 10 });
  };

  /* ---- the graded findings as a table with badges ---- */
  Doc.prototype.findingsTable = function (items) {
    var self = this, cw = this.CW - 190;
    var rowsH = items.map(function (f) { return Math.max(wrapTokens(tokens(f.claim, 'R', COL.body), 8.3, cw).length * 11 + 8, f.process || f.untrusted ? 28 : 20); });
    this.need(40 + (rowsH[0] || 0));
    this.tab += 1;
    var cap = enc('Table ' + this.tab + '.  ');
    this.text(cap, this.L, this.y - 9, 9, 'B', COL.accentInk);
    this.text(enc('Every finding the engine graded'), this.L + tw(cap, 9, 'B'), this.y - 9, 9, 'B', COL.ink);
    this.y -= 16;
    var head = function () {
      var top = self.y; self.rect(self.L, top - 18, self.CW, 18, COL.panel); self.line(self.L, top, self.L + self.CW, top, COL.ink, 0.8);
      self.text(enc('Finding'), self.L + 5, top - 12, 7.3, 'B', COL.ink); var v = enc('Value'); self.text(v, self.L + self.CW - 110 - tw(v, 7.3, 'B'), top - 12, 7.3, 'B', COL.ink);
      self.text(enc('Grade'), self.L + self.CW - 96, top - 12, 7.3, 'B', COL.ink); self.line(self.L, top - 18, self.L + self.CW, top - 18, COL.ink, 0.5); self.y = top - 18;
    };
    head();
    items.forEach(function (f, i) {
      if (self.y - rowsH[i] < self.BOT) { self.newPage(); head(); }
      var top = self.y;
      if (i % 2) self.rect(self.L, top - rowsH[i], self.CW, rowsH[i], COL.zebra);
      wrapTokens(tokens(f.claim, 'R', COL.body), 8.3, cw).forEach(function (ln, k) { self.drawLine(ln, self.L + 5, top - 12 - k * 11, 8.3); });
      var v = enc(smart(f.value || '')); self.text(v, self.L + self.CW - 110 - tw(v, 8.3, 'R'), top - 12, 8.3, 'R', COL.ink);
      if (gradeOf(f.grade)) self.badge(f.grade, self.L + self.CW - 96, top - 12);
      if (f.process || f.untrusted) self.text(enc(f.untrusted ? 'not trusted: back-test failed' : 'process grade'), self.L + self.CW - 96, top - 23.5, 6.2, 'I', f.untrusted ? COL.grade.WATCH : COL.muted);
      self.y = top - rowsH[i]; self.line(self.L, self.y, self.L + self.CW, self.y, i === items.length - 1 ? COL.ink : COL.hair, i === items.length - 1 ? 0.8 : 0.4);
    });
    this.y -= 14;
  };

  /* ---- references: numbered, the title, where it is from, the full address as a clickable link ---- */
  Doc.prototype.references = function (refs, accessed) {
    var self = this;
    refs.forEach(function (s, i) {
      var title = String(s.title || 'Source'), url = String(s.link || ''), host = (url.match(/^[a-z]+:\/+([^/?#]+)/i) || [])[1] || '';
      var shown = /[^\x20-\x7e]/.test(url) ? uriOf(url) : url;     // an address in another script, as its percent-encoded form
      var tl = wrapTokens(tokens(title, 'B', COL.ink), 9, self.TW), ul = wrapTokens([[{ b: enc(shown), f: 'R', c: COL.accentInk }]], 7.5, self.TW);
      var metaLine = enc(host.replace(/^www\./, '') + (s.date ? DOT + 'published ' + s.date : '') + DOT + 'accessed ' + accessed);
      var ml = wrapTokens([[{ b: metaLine, f: 'R', c: COL.muted }]], 7.5, self.TW);
      self.need(tl.length * 12.5 + ml.length * 11 + ul.length * 10 + 8);
      self.dests['ref' + (i + 1)] = { page: self.pages.length - 1, y: self.y + 4 };
      var n = enc('[' + (i + 1) + ']');
      self.text(n, self.TX - 12 - tw(n, 9, 'B'), self.y - 9, 9, 'B', COL.accentInk);
      tl.forEach(function (ln) { self.drawLine(ln, self.TX, self.y - 9, 9); self.y -= 12.5; });
      ml.forEach(function (ln) { self.drawLine(ln, self.TX, self.y - 7.5, 7.5); self.y -= 11; });
      if (/^https?:\/\//i.test(url)) ul.forEach(function (ln) { var y = self.y - 7.5, seg = ln[0][0]; self.text(seg.b, self.TX, y, 7.5, 'R', COL.accentInk); self.link(self.TX, y - 2, segW(seg, 7.5), 10, { uri: url }); self.y -= 10; });
      self.y -= 8;
    });
  };

  /* ---------------------------------------------------------------- emit: header, footer, contents, links, bookmarks, info */
  Doc.prototype.emit = function (meta) {
    var n = this.pages.length, W = this.W, H = this.H, self = this, objs = [];
    var FIRST_PAGE = 8, pageObj = function (i) { return FIRST_PAGE + 2 * i; }, next = FIRST_PAGE + 2 * n;
    // the running header and footer, now that the page count is known
    this.pages.forEach(function (p, i) {
      self.pg = p;
      if (meta.specimen) self.op('q 0.93 0.35 0.2 rg BT /F2 34 Tf 0.8192 0.5736 -0.5736 0.8192 150 260 Tm (' + esc(enc(meta.specimen)) + ') Tj ET Q');
      var pn = enc('Page ' + (i + 1) + ' of ' + n), pw = tw(pn, 7.5, 'B');
      var foot = enc('Confidential' + DOT + meta.confidential);
      while (tw(foot, 7, 'R') > self.CW - pw - 16 && foot.length > 20) foot = foot.slice(0, -2);
      self.line(self.L, 50, self.L + self.CW, 50, COL.hair, 0.5);
      self.text(foot, self.L, 38, 7, 'R', COL.muted);
      self.text(pn, self.L + self.CW - pw, 38, 7.5, 'B', COL.ink);
      if (p.kind !== 'cover') {
        var k = enc('NORTHLEDGER INSIGHTS' + DOT + 'DATA REPORT'); self.text(k, self.L, H - 40, 6.8, 'B', COL.accentInk, 0.9);
        var room = self.CW - twc(k, 6.8, 'B', 0.9) - 24, st = enc(meta.shortTitle);
        if (tw(st, 7.5, 'R') > room) { while (tw(st + '\x85', 7.5, 'R') > room && st.length > 4) st = st.slice(0, -1); st = st.replace(/\s+\S*$/, '') + '\x85'; }
        self.text(st, self.L + self.CW - tw(st, 7.5, 'R'), H - 40, 7.5, 'R', COL.muted);
        self.line(self.L, H - 48, self.L + self.CW, H - 48, COL.hair, 0.5);
      }
    });
    // the contents list on the cover (page numbers exist only now)
    if (meta.toc) {
      self.pg = this.pages[0];
      var yy = meta.toc.y;
      this.outline.filter(function (o) { return o.level === 0; }).forEach(function (o) {
        var d = self.dests[o.id]; if (!d) return;
        var num = enc(String(d.page + 1)), nw = tw(num, 9.5, 'B'), t = enc(smart(o.title));
        while (tw(t, 9.5, 'R') > self.CW - nw - 40 && t.length > 8) t = t.slice(0, -2) + '\x85';
        self.text(t, self.L, yy, 9.5, 'R', COL.ink);
        var x1 = self.L + tw(t, 9.5, 'R') + 6, x2 = self.L + self.CW - nw - 6;
        self.line(x1, yy + 1, x2, yy + 1, COL.hair, 0.6, '0.6 2.4');
        self.text(num, self.L + self.CW - nw, yy, 9.5, 'B', COL.ink);
        self.link(self.L, yy - 3, self.CW, 13, o.id);
        yy -= meta.toc.lead;
      });
    }
    var destArr = function (id) { var d = self.dests[id]; return d ? '[' + pageObj(d.page) + ' 0 R /XYZ 0 ' + f2(Math.min(H, d.y + 16)) + ' 0]' : null; };
    var annots = this.pages.map(function (p) {
      return p.links.map(function (l) {
        var a = typeof l.t === 'string' ? (destArr(l.t) ? '/Dest ' + destArr(l.t) : null) : '/A << /S /URI /URI (' + esc(enc(uriOf(l.t.uri))) + ') >>';
        if (!a) return null;
        var id = next++; objs[id] = '<< /Type /Annot /Subtype /Link /Rect [' + l.r.map(f2).join(' ') + '] /Border [0 0 0] ' + a + ' >>';
        return id + ' 0 R';
      }).filter(Boolean);
    });
    // bookmarks: the parts and their sections
    var outlineRoot = null, items = this.outline.filter(function (o) { return self.dests[o.id]; });
    if (items.length && items[0].level === 0) {
      outlineRoot = next++;
      var ids = items.map(function () { return next++; }), tops = [], parentOf = [];
      items.forEach(function (o, i) { if (o.level === 0) tops.push(i); parentOf[i] = o.level === 0 ? outlineRoot : ids[tops[tops.length - 1]]; });
      // every item's siblings (and its place among them) and children, grouped in one pass, in document order (a
      // search of all items for each item was quadratic: a share of 4,570 headings spent 200 ms and more here)
      var byParent = {}, posIn = [];
      items.forEach(function (o, j) { var g = byParent[parentOf[j]] || (byParent[parentOf[j]] = []); posIn[j] = g.length; g.push(j); });
      items.forEach(function (o, i) {
        var sib = byParent[parentOf[i]], k = posIn[i], kids = byParent[ids[i]] || [];
        objs[ids[i]] = '<< /Title ' + utf16hex(smart(o.title)) + ' /Parent ' + parentOf[i] + ' 0 R' + (k > 0 ? ' /Prev ' + ids[sib[k - 1]] + ' 0 R' : '') + (k < sib.length - 1 ? ' /Next ' + ids[sib[k + 1]] + ' 0 R' : '') +
          (kids.length ? ' /First ' + ids[kids[0]] + ' 0 R /Last ' + ids[kids[kids.length - 1]] + ' 0 R /Count -' + kids.length : '') + ' /Dest ' + destArr(o.id) + ' >>';
      });
      objs[outlineRoot] = '<< /Type /Outlines /First ' + ids[tops[0]] + ' 0 R /Last ' + ids[tops[tops.length - 1]] + ' 0 R /Count ' + tops.length + ' >>';
    }
    objs[1] = '<< /Type /Catalog /Pages 2 0 R /Lang (en-CA) /ViewerPreferences << /DisplayDocTitle true >>' + (outlineRoot ? ' /Outlines ' + outlineRoot + ' 0 R /PageMode /UseOutlines' : '') + ' >>';
    objs[2] = '<< /Type /Pages /Kids [' + this.pages.map(function (_, i) { return pageObj(i) + ' 0 R'; }).join(' ') + '] /Count ' + n + ' >>';
    var now = pdfDate(meta.date);
    objs[3] = '<< /Title ' + utf16hex(meta.title) + ' /Author ' + utf16hex(meta.author) + ' /Subject ' + utf16hex(meta.subject) +
      ' /Keywords ' + utf16hex(meta.keywords) + ' /Creator ' + utf16hex('NorthLedger report writer') + ' /Producer ' + utf16hex('NorthLedger PDF writer 2 (in the browser)') +
      ' /CreationDate (' + now + ') /ModDate (' + now + ') >>';
    objs[4] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>';
    objs[5] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>';
    objs[6] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>';
    objs[7] = '<< /Type /ExtGState /ca 0.45 /CA 0.45 >>';
    this.pages.forEach(function (p, i) {
      var stream = p.ops.join('\n');
      objs[pageObj(i)] = '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + W + ' ' + H + '] /Resources << /Font << /F1 4 0 R /F2 5 0 R /F3 6 0 R >> /ExtGState << /GSa 7 0 R >> >> /Contents ' + (pageObj(i) + 1) + ' 0 R' +
        (annots[i].length ? ' /Annots [' + annots[i].join(' ') + ']' : '') + ' >>';
      objs[pageObj(i) + 1] = '<< /Length ' + stream.length + ' >>\nstream\n' + stream + '\nendstream';
    });
    var out = '%PDF-1.4\n%\xe2\xe3\xcf\xd3\n', offs = [], k2;
    for (k2 = 1; k2 < next; k2++) { offs[k2] = out.length; out += k2 + ' 0 obj\n' + objs[k2] + '\nendobj\n'; }
    var xref = out.length;
    out += 'xref\n0 ' + next + '\n0000000000 65535 f \n';
    for (k2 = 1; k2 < next; k2++) out += ('000000000' + offs[k2]).slice(-10) + ' 00000 n \n';
    out += 'trailer\n<< /Size ' + next + ' /Root 1 0 R /Info 3 0 R >>\nstartxref\n' + xref + '\n%%EOF\n';
    var u8 = new Uint8Array(out.length);
    for (k2 = 0; k2 < out.length; k2++) u8[k2] = out.charCodeAt(k2) & 255;
    return u8;
  };
  function uriOf(u) { try { return encodeURI(decodeURI(String(u))); } catch (e) { return encodeURI(String(u)); } }

  /* ---------------------------------------------------------------- the report, section by section */
  // model: { title, shortTitle, question, date, subject, dataLine, preparedBy, reportId, kept[], notice, headline,
  //   kpis[{label, value, sub, grade}], summary[], parts[{kicker, title, id, blocks[]}], model }
  // a block: {type: 'h2'|'p'|'bullets'|'numbered'|'callout'|'chart'|'table'|'cards'|'noscenarios'|'findings'|'references', ...}
  var FOREIGN_NOTE = 'Some names are in a script this PDF cannot show; see the on-screen report.';
  var FOREIGN_TEXT = 'Some names in this report are in a script this PDF cannot show. Each is printed as a placeholder, [name 1], [name 2] and so on, the same placeholder wherever the same name appears; the on-screen report shows the names.';
  // the model with every name in a script this PDF cannot show replaced by its placeholder (placeholders()); a
  // figure, a table or a set of cards that holds one says so in its source line, and Appendix A says it for the text
  // a label of symbols only (emoji and marks: WinAnsi has none of them, so it printed as nothing; review of the chart
  // registry, 30 Sep 2026): "[label 1]", "[label 2]"... in a figure or a table, the same placeholder for the same label
  // in both
  function symbolsOnly(s) { return /\S/.test(s) && !enc(s).replace(/\?/g, '').trim(); }
  function unsymbol(x, sl) {
    if (typeof x === 'string') {
      if (!symbolsOnly(x)) return x;
      if (!Object.prototype.hasOwnProperty.call(sl.map, x)) { sl.n += 1; sl.map[x] = '[label\u00a0' + sl.n + ']'; }
      return sl.map[x];
    }
    if (Array.isArray(x)) return x.map(function (y) { return unsymbol(y, sl); });
    if (x && typeof x === 'object' && !(x instanceof Date)) { var o = {}; Object.keys(x).forEach(function (k) { o[k] = unsymbol(x[k], sl); }); return o; }
    return x;
  }
  function unforeign(m) {
    var st = { map: {}, n: 0, hits: 0 }, sl = { map: {}, n: 0 };
    var walk = function (x) {
      if (typeof x === 'string') return placeholders(x, st);
      if (Array.isArray(x)) return x.map(walk);
      if (x && typeof x === 'object' && !(x instanceof Date)) { var o = {}; Object.keys(x).forEach(function (k) { o[k] = k === 'link' ? x[k] : walk(x[k]); }); return o; }
      return x;
    };
    var out = {};
    Object.keys(m || {}).forEach(function (k) { if (k !== 'parts' && k !== 'date') out[k] = walk(m[k]); });
    out.date = m && m.date;
    out.parts = ((m && m.parts) || []).map(function (p) {
      var q = {};
      Object.keys(p).forEach(function (k) { if (k !== 'blocks') q[k] = walk(p[k]); });
      q.blocks = (p.blocks || []).map(function (b) {
        var h0 = st.hits, nb = walk(b);
        if (nb.type === 'chart' || nb.type === 'table') nb = unsymbol(nb, sl);
        if (st.hits > h0 && (nb.type === 'chart' || nb.type === 'table')) nb.source = (nb.source ? nb.source + ' ' : '') + FOREIGN_NOTE;
        else if (st.hits > h0 && nb.type === 'cards') nb.note = (nb.note ? nb.note + ' ' : '') + FOREIGN_NOTE;
        return nb;
      });
      return q;
    });
    if (st.n) {
      var meth = out.parts.filter(function (p) { return p.id === 'method'; })[0];
      if (meth) meth.blocks.splice(Math.min(2, meth.blocks.length), 0, { type: 'p', text: FOREIGN_TEXT, size: 8.5 });
    }
    return out;
  }
  function build(m, opts) {
    opts = opts || {};
    m = unforeign(m);
    var d = new Doc({ paper: opts.paper === 'a4' ? 'a4' : 'letter' }), date = m.date instanceof Date ? m.date : new Date(m.date || Date.now());
    // opts.trace (an array, for the checks): each viz figure's box, operators, words and cells are pushed to it; the
    // bytes are the same with it or without
    if (Array.isArray(opts.trace)) d.tr = opts.trace;
    var parts = m.parts || [];
    // ---- the cover
    d.newPage('cover');
    d.rect(0, d.H - 8, d.W, 8, COL.accent);
    d.text(enc('NORTHLEDGER INSIGHTS'), d.L, d.H - 70, 8, 'B', COL.accentInk, 1.4);
    d.text(enc('DATA REPORT'), d.L + twc(enc('NORTHLEDGER INSIGHTS'), 8, 'B', 1.4) + 14, d.H - 70, 8, 'B', COL.muted, 1.4);
    d.line(d.L, d.H - 80, d.L + 36, d.H - 80, COL.accent, 2);
    d.y = d.H - 120;
    var tl = d.linesHeight(m.title, 25, d.CW * 0.9, 30, 'B') / 30, ts = tl > 3 ? 20 : 25;
    d.para(m.title, { x: d.L, width: d.CW * 0.9, size: ts, lead: ts * 1.2, font: 'B', color: COL.ink, after: 10, maxLines: 5 });
    if (m.question) d.para(words(m.question, 260), { x: d.L, width: d.CW * 0.85, size: 12, lead: 17, color: COL.muted, after: 18, maxLines: 4 });
    // each row at most a few lines (an ellipsis ends a longer one), so the cover never runs into its contents list
    var rows = [['Subject', m.subject, 2], ['Data', m.dataLine, 2], ['Prepared', longDate(date), 1], ['Prepared by', m.preparedBy, 3], ['Report ID', m.reportId, 1]];
    if (m.kept && m.kept.length) {
      // every column the reader sent, or as many as 3 lines hold and how many more (the notice in Appendix A names all)
      var keptRow = function (k) {
        return 'Sent to the AI at the reader\'s choice: ' + m.kept.slice(0, k).join(', ') + (k < m.kept.length ? ', and ' + (m.kept.length - k) + ' more, all named in Appendix A' : '') + '. This report may contain their values.';
      };
      var nk = m.kept.length;
      while (nk > 1 && d.linesHeight(keptRow(nk), 9.5, d.CW - 96, 13) > 3 * 13) nk -= 1;
      rows.push(['Personal columns', keptRow(nk), 4]);
    }
    d.line(d.L, d.y, d.L + d.CW, d.y, COL.hair, 0.6); d.y -= 6;
    rows.forEach(function (r) {
      if (!r[1]) return;
      d.text(enc(r[0].toUpperCase()), d.L, d.y - 11, 7, 'B', r[0] === 'Personal columns' ? COL.grade.WATCH : COL.muted, 0.8);
      d.para(r[1], { x: d.L + 96, width: d.CW - 96, size: 9.5, lead: 13, color: COL.ink, after: 5, raw: r[0] === 'Report ID', maxLines: r[2] });
    });
    d.line(d.L, d.y, d.L + d.CW, d.y, COL.hair, 0.6);
    // the notice at the foot of the cover, measured first; when the contents list at its tightest would reach it, the
    // notice opens page 2 instead (a cover with many kept columns, 30 Sep 2026: the list ran over the notice)
    var nh = d.calloutHeight('About this report', m.notice, { size: 8.5 }), noticeTop = d.BOT + 12 + nh, nToc = 1 + parts.length;
    var tocY = d.y - 24 - 24 - 8, onCover = tocY - (nToc - 1) * 12 - 16 >= noticeTop;
    var floor = onCover ? noticeTop + 16 : d.BOT + 12, tocLead = Math.max(10.5, Math.min(17, (tocY - floor) / Math.max(1, nToc - 1)));
    d.y -= 24;
    d.text(enc('CONTENTS'), d.L, d.y - 8, 7.5, 'B', COL.accentInk, 1.1);
    if (onCover) { d.y = noticeTop; d.callout('About this report', m.notice, { size: 8.5, fill: COL.panel }); }
    // ---- the executive summary
    d.newPage();
    if (!onCover) d.callout('About this report', m.notice, { size: 8.5, fill: COL.panel });
    d.h1('Executive summary', 'The headline', 'summary');
    if (m.estimand) d.estimand(m.estimand);
    d.callout('Headline insight', m.headline, { size: 12.5, font: 'B' });
    d.kpis(m.kpis || []);
    if (m.summary && m.summary.length) { d.h2('', 'In brief', 'brief', 44); d.bullets(m.summary); }
    // ---- the parts, in the fixed order
    parts.forEach(function (p) {
      d.h1(p.kicker, p.title, p.id);
      (p.blocks || []).forEach(function (b, bi) {
        var nb = p.blocks[bi + 1] || {};
        if (b.type === 'h2') d.h2(b.num, b.text, b.id, nb.type === 'chart' ? d.figureHeight(nb) : nb.type === 'table' || nb.type === 'findings' ? 90 : nb.type === 'cards' ? 150 : 44);
        else if (b.type === 'p') d.para(b.text, b.wide ? { x: d.L, width: d.CW, size: b.size, lead: b.lead, font: b.font, color: b.color, after: b.after } : b);
        else if (b.type === 'bullets') d.bullets(b.items);
        else if (b.type === 'numbered') d.bullets(b.items, { numbered: true });
        else if (b.type === 'callout') d.callout(b.label, b.text, b);
        else if (b.type === 'chart') d.figure(b.chart, b);
        else if (b.type === 'table') d.table(b.table, b);
        else if (b.type === 'cards') d.cards(b.items, b.note);
        else if (b.type === 'noscenarios') d.noScenarios(b.reason);
        else if (b.type === 'findings') d.findingsTable(b.items);
        else if (b.type === 'references') d.references(b.items, longDate(date));
      });
    });
    return d.emit({ title: m.title, shortTitle: m.shortTitle || m.title, author: 'NorthLedger Insights (automated: the NorthLedger engine and ' + (m.model || 'an AI model') + ')',
      subject: m.question || '', keywords: 'NorthLedger; data report' + (m.model ? '; ' + m.model : ''), date: date,
      confidential: m.shared ? 'from a shared report, analysed in the reader\'s browser; not reviewed by a person' : 'prepared in the reader\'s browser from their own file; not reviewed by a person',
      toc: { y: tocY, lead: tocLead }, specimen: opts.specimen });
  }

  /* ---------------------------------------------------------------- the adapter: page state to the section model */
  // input: { report (the AI's markdown, with [your file] for the file's name), sources [{title, link, date}], model,
  //   repaired (sentences the honesty check removed), removed_figures [], results (the engine's results the page
  //   posted to /report, its primary claim in results.primary; a share link's copy is trimmed by shareResults and
  //   marked partial, and may carry key_figures [{label, value, text, unit, grade}], its key figures when it has no
  //   headline item of the scenarios; absent for a report saved
  //   before they were kept, or a share made before they rode along), charts, tables (used when results are absent),
  //   kept (the flagged columns the visitor sent; undefined when not recorded), name (the file's name), showName (true
  //   only when the visitor ticked "include my file name"), date, goal, shared (true when a share link's server writes
  //   it from the stored copy, not the reader's browser) }
  // A section's part comes from its heading: the report writer's fixed names first, then the words a heading uses
  // (final review, 30 Sep 2026: "Market context" and "Next steps" fell into Part 1, and Part 2 said no source was
  // cited beside 35 sources). The first rule that matches wins, so "Risks to the outlook" is a risk.
  var KIND = [
    ['exec', /^(executive summary|summary|key takeaways|key points|at a glance|in brief|tl;?dr)\b/i],
    ['headline', /^the headline\b/i], ['drove', /^what drove it\b/i], ['other', /^other findings\b/i],
    ['world', /^(in the real world|current context|the real world|outside context)\b/i], ['scen', /^scenarios?\b/i],
    ['todo', /^(what to do|recommendations?|recommended actions)\b/i], ['risks', /^(risks\b|limitations\b|what the data cannot say)/i],
    ['risks', /\b(risks?|limits?|limitations?|caveats?|cannot say|can(?:no|')t say|uncertaint(?:y|ies))\b/i],
    ['todo', /\b(next steps?|actions?|recommend\w*|what to do|to-?dos?)\b/i],
    ['scen', /\b(scenarios?|outlook|what[- ]ifs?)\b/i],
    ['world', /\b(context|markets?|outside|real world|benchmarks?|econom(?:y|ic|ics))\b/i],
    ['drove', /\b(drivers?|drove|drive|breakdowns?|broken down|why)\b/i]];
  function kindOf(head) {
    var h0 = String(head || ''), h = h0.replace(/^\d+[.)]?\s+/, '');
    for (var i = 0; i < KIND.length; i++) if (KIND[i][1].test(h)) return KIND[i][0];
    return h !== h0 ? 'num' : 'extra';
  }
  function plain(s) { return String(s || '').replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1').replace(/`([^`]*)`/g, '$1').replace(/^#+\s*/, '').trim(); }
  function parseReport(text) {
    var lines = String(text || '').replace(/\r/g, '').split('\n'), title = '', secs = [], cur = null;
    lines.forEach(function (L) {
      var h = L.match(/^#{2,3}\s+(.*)$/);
      if (h) { cur = { head: plain(h[1]), lines: [] }; cur.kind = kindOf(cur.head); secs.push(cur); return; }
      if (!cur) { if (!title && L.trim()) title = plain(L.replace(/^#\s+/, '')); else if (L.trim()) { cur = { head: '', kind: 'extra', lines: [L] }; secs.push(cur); } return; }
      cur.lines.push(L);
    });
    return { title: title, secs: secs };
  }
  function interval(why) {
    var m = String(why || '').match(/interval ([-+\u2212]?[\d.,]+%?) to ([-+\u2212]?[\d.,]+%?)/);
    return m ? m[1] + ' to ' + m[2] : '';
  }
  function sgnPct(v) { v = Number(v); return (v > 0 ? '+' : v < 0 ? '\u2212' : '') + Math.abs(v).toFixed(1) + '%'; }
  function shortClaim(c) {
    var m = String(c || '').match(/^Change in (?:the )?(.*?), latest/);
    return m ? cap1(m[1]) : String(c || '');
  }
  function reportId(s) {        // a short fingerprint of the report's text (FNV-1a, two seeds): tells versions apart
    var h1 = 0x811c9dc5, h2 = 0x01000193 ^ 0x5bd1e995;
    s = String(s || '');
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0; h2 = Math.imul(h2 ^ c, 0x5bd1e995) >>> 0; h2 ^= h2 >>> 13; }
    return ('0000000' + h1.toString(16)).slice(-8) + ('0000000' + (h2 >>> 0).toString(16)).slice(-8).slice(0, 4);
  }
  // a table's title and column names, for telling the same table apart when the AI has already placed it
  function tableSig(t) {
    var n = function (s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(); };
    return { title: n(t && t.title), cols: ((t && t.cols) || []).map(n).join('|') };
  }
  var MAX_SOURCES = 20;
  // the adapter's note that the byte budget left scenario items out of what the writer received (nl_browser.BUDGET_REFUSED)
  var LEFT_OUT = /scenario items are left out to keep what the report writer receives/i;
  // b's items added to the end of a, in place (a = a.concat(b) in a loop copies a each time: quadratic in sections)
  function append(a, b) { for (var i = 0; i < b.length; i++) a.push(b[i]); return a; }
  function model(inp) {
    inp = inp || {};
    var date = inp.date instanceof Date ? inp.date : new Date(inp.date || Date.now());
    var showName = inp.showName === true && !!inp.name, fileWord = showName ? String(inp.name) : 'your file';
    var sub = function (s) { return String(s === null || s === undefined ? '' : s).split(FILE_WORD).join(fileWord); };
    var subAll = function (x) {
      if (typeof x === 'string') return sub(x);
      if (Array.isArray(x)) return x.map(subAll);
      if (x && typeof x === 'object') { var o = {}; Object.keys(x).forEach(function (k) { o[k] = subAll(x[k]); }); return o; }
      return x;
    };
    var R = inp.results && typeof inp.results === 'object' ? subAll(inp.results) : null;
    var charts = (R && Array.isArray(R.charts) ? R.charts : subAll(inp.charts || [])) || [], tables = (R && Array.isArray(R.tables) ? R.tables : subAll(inp.tables || [])) || [];
    // every source the worker returned (it sends up to 16; 20 are kept), in its own numbering; a citation of a source
    // not in the list is left out, so every [n] in the file resolves to its reference
    var sources = (Array.isArray(inp.sources) ? inp.sources : []).slice(0, MAX_SOURCES).map(function (s) { return s && typeof s === 'object' ? s : {}; });
    var text = sub(inp.report).replace(/[ \t]*\[S(\d+)\]/g, function (m0, k) { k = Number(k); return k >= 1 && k <= sources.length ? m0 : ''; });
    var P = parseReport(text);
    var rows = R && R.input && R.input.rows, cols = R && R.input && R.input.columns;
    var analyses = (R && R.analyses) || [];
    var EV = R ? estimandView(R) : null, AV = R ? auditView(R) : null, U = R ? unitOf(R) : null;
    var methodOf = function (t) { var a = analyses.filter(function (x) { return x && x.title === t; })[0]; return a && a.method ? ' Method: ' + a.method : ''; };
    var srcNote = function (t) { return 'Source: NorthLedger engine, computed in the reader\'s browser from ' + fileWord + (rows ? ' (' + Number(rows).toLocaleString('en-US') + ' rows)' : '') + '.' + methodOf(t); };
    var usedTables = {}, usedCharts = {};
    var chartBlock = function (c) {
      var tb = tables.filter(function (t) { return t && t.title === c.title; })[0];
      return { type: 'chart', chart: c, source: srcNote(c.title), yTitle: c.y_name || c.unit || '', xTitle: c.x_name || c.x_label || (c.kind === 'bars' && tb && tb.cols ? tb.cols[0] : '') };
    };
    var tableBlock = function (t, i) { usedTables[i] = true; return { type: 'table', table: t, source: srcNote(t.title) }; };
    // a chart registry record's blocks (the page's and the share's records alike: /report's validated charts, or the
    // share's stored copy): its figure, then its table view (every figure it shows, each label whole) when that has at
    // most 12 rows; a longer one goes to Appendix A, and the figure's note says so (spec accessibility.table_view). A
    // table view too wide for the page is split by the writer (fitTable). A record drawn as its table (kind
    // 'table', a kind this writer does not draw, data it cannot read) is that table, its note saying why.
    var sentence = function (s) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s ? cap1(s) + (/[.!?]$/.test(s) ? '' : '.') : ''; };
    var vizSource = function (v, table) {
      var out = ['Source: NorthLedger engine, computed in the reader\'s browser from ' + fileWord + (rows ? ' (' + Number(rows).toLocaleString('en-US') + ' rows)' : '') + '.', sentence(v.source)];
      if ((table || v.kind !== 'heatmap') && isObj(v.suppressed) && v.suppressed.cells > 0) out.push(sentence(v.suppressed.why));
      if (table && isObj(v.degraded) && v.degraded.why) out.push(sentence(v.degraded.why));
      else if (table && v.kind !== 'table') out.push(Object.prototype.hasOwnProperty.call(VIZ, v.kind) ? 'Shown as its table: its drawing data could not be read.' : 'Shown as its table: this PDF does not draw a chart of the kind \u201c' + words(v.kind, 24) + '\u201d.');
      return out.filter(Boolean).join(' ');
    };
    var tableViews = function (v, t) {
      var d = v.data, grid = v.kind === 'heatmap' && Array.isArray(d.rows) && Array.isArray(d.cols) && t.rows.length === d.rows.length && t.cols.length === d.cols.length + 1;
      return [{ type: 'table', fit: true, flip: grid ? String(d.col_label || '') : '', table: { title: String(v.title || ''), cols: t.cols.map(String), rows: t.rows.filter(Array.isArray) } }];
    };
    var chartBlocks = function (c) {
      var v = vizOf(c);
      if (!v) return [chartBlock(c)];
      var table = asTable(v), b = { type: 'chart', chart: v, source: vizSource(v, table) }, t = v.table;
      if (table || !isObj(t) || !Array.isArray(t.cols) || !t.cols.length || !Array.isArray(t.rows) || !t.rows.length) return [b];
      var views = tableViews(v, t);
      if (t.rows.length <= 12) return [b].concat(views);
      b.source += ' Its table, with every figure it shows, is in Appendix A.';
      b.tabs = views;
      return [b];
    };
    // one section's lines to blocks: paragraphs, bullets, numbered lists, markdown tables, [CHART:n] and [TABLE:n]
    var blocksOf = function (lines) {
      var out = [], list = null, tbl = [];
      var flush = function () { if (list) { out.push(list); list = null; } };
      var flushT = function () {
        if (!tbl.length) return;
        var rs = tbl.filter(function (r) { return !/^\|?[\s:|-]+$/.test(r); }).map(function (r) {
          return r.split('|').map(function (c) { return plain(c); }).filter(function (c, k, a) { return !(k === 0 && !c) && !(k === a.length - 1 && !c); });
        });
        if (rs.length > 1) out.push({ type: 'table', table: { title: 'From the report', cols: rs[0], rows: rs.slice(1) }, source: 'A table the AI placed; its figures passed the same honesty check as the text.' });
        tbl = [];
      };
      (lines || []).forEach(function (raw) {
        var L = raw.trim(), mk = L.match(/^\[(CHART|TABLE):(\d+)\]$/);
        if (/^\|/.test(L)) { flush(); tbl.push(L); return; }
        flushT();
        if (mk) {
          flush(); var i = Number(mk[2]) - 1;
          if (mk[1] === 'CHART' && charts[i]) { usedCharts[i] = true; append(out, chartBlocks(charts[i])); }
          if (mk[1] === 'TABLE' && tables[i]) out.push(tableBlock(tables[i], i));
          return;
        }
        if (/^[-*\u2022]\s/.test(L)) { if (!list || list.type !== 'bullets') { flush(); list = { type: 'bullets', items: [] }; } list.items.push(plain(L.replace(/^[-*\u2022]\s+/, ''))); return; }
        if (/^\d+[.)]\s/.test(L)) { if (!list || list.type !== 'numbered') { flush(); list = { type: 'numbered', items: [] }; } list.items.push(plain(L.replace(/^\d+[.)]\s+/, ''))); return; }
        flush();
        if (L) out.push({ type: 'p', text: plain(L) });
      });
      flush(); flushT();
      return out;
    };
    // every section's blocks first, so a table the AI placed anywhere is known before the engine adds its own
    var execAt = -1;
    P.secs.forEach(function (s, i) { s.blocks = blocksOf(s.lines); if (s.kind === 'exec') { if (execAt < 0) execAt = i; else s.kind = 'extra'; } });
    var secsOf = function (k) { return P.secs.filter(function (s) { return s.kind === k; }); };
    var findings = (R && Array.isArray(R.findings) ? R.findings : []).filter(function (f) { return f && f.claim; });
    var findingOf = function (claim) { return claim ? findings.filter(function (f) { return f.claim === claim; })[0] || null : null; };
    var sc = R && R.scenarios && typeof R.scenarios === 'object' ? R.scenarios : null;
    var items = sc && Array.isArray(sc.items) ? sc.items.filter(function (x) { return x && x.id && x.text !== undefined && x.text !== null; }) : [];
    var item = function (id) { return items.filter(function (x) { return x.id === id; })[0]; };
    var basis = sc && sc.basis && typeof sc.basis === 'object' ? sc.basis : null;
    var biz = findings.filter(function (f) { return f.kind === 'business'; });

    // ---- key figures. The headline items of the change the scenarios break down (the engine's primary claim when it
    // is a total or a row count). When the scenarios were refused (the primary claim is an average, as a rate or a
    // price is, or nothing reconciled), the primary finding's own level, change and interval: never a row count put
    // forward in its place (final review, 30 Sep 2026: an FX report led with "Rows, the latest 12 months").
    var kpis = [], latest = item('headline.latest'), prior = item('headline.prior'), pct = item('headline.change_pct'), chg = item('headline.change');
    var basisF = basis ? findingOf(basis.claim) : null;
    var primaryOf = function () {
      var p = R && (R.primary || (sc && sc.primary));
      var named = findingOf(p && typeof p === 'object' ? p.claim : typeof p === 'string' ? p : '');
      if (named) return named;
      if (basisF && basis.reconciles === false) return basisF;
      var refusedAvg = sc && Array.isArray(sc.refused) && sc.refused.some(function (x) { return /\baverages?\b/i.test(String(x)); });
      var avg = biz.filter(function (f) { return /\baverage month of\b/i.test(f.claim); });
      var other = biz.filter(function (f) { return !/\brow volume\b/i.test(f.claim); });
      return (refusedAvg && avg[0]) || other[0] || biz[0] || null;
    };
    // the level the engine's own story states for a change ("moved from 1.396 in the year before to 1.386 in the latest year")
    var levelOf = function (f) {
      var m = String(f.claim || '').match(/^Change in (?:the )?(.*?), latest 12 months/i);
      if (!m) return null;
      var what = m[1], name = what.replace(/^(average month of|monthly total of)\s+/i, ''), rx = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      var about = /^average month of /i.test(what) ? new RegExp('^Average ' + rx + ' \\(the average month\\) moved', 'i')
        : /^monthly total of /i.test(what) ? new RegExp('^The monthly total of ' + rx + ' moved', 'i') : null;
      var lines = R && R.story && Array.isArray(R.story.what_happened) ? R.story.what_happened : [];
      for (var i = 0; about && i < lines.length; i++) {
        var mm = about.test(String(lines[i])) && String(lines[i]).match(/moved from ([-+\u2212]?[\d.,]+) in the year before to ([-+\u2212]?[\d.,]+) in the latest year/);
        if (mm) return { label: (/^average/i.test(what) ? 'Average ' : 'Total ') + name + ', the latest 12 months', latest: mm[2], prior: mm[1] };
      }
      return null;
    };
    var tileOf = function (f, label) {
      var ch = /^change in /i.test(f.claim) && isFinite(f.value) && f.value !== null;
      return { label: label || (ch ? 'Change: ' + shortClaim(f.claim) : words(f.claim, 60)), value: ch ? sgnPct(f.value) : (isFinite(f.value) && f.value !== null ? fmtVal(f.value) : ''),
        sub: interval(f.why) ? '95% interval ' + interval(f.why) : '', grade: f.verdict };
    };
    // a shared copy's own key figures (the worker keeps {label, value, text, unit, grade}, at most 8): its tiles when the
    // copy carries no headline item of the scenarios (they give the richer tiles, with the 12 months before)
    var kfs = inp.shared === true && R && Array.isArray(R.key_figures) ? R.key_figures.filter(function (k) {
      return k && typeof k === 'object' && k.text !== undefined && k.text !== null && String(k.text).trim(); }).slice(0, 4) : [];
    var lead = null;
    if (latest || pct) {
      lead = basisF;
      var proc = !!(EV && EV.process);       // an official aggregate: the engine's grade is a process grade, said on the tile
      if (latest) kpis.push({ label: EV && R.estimand.measure && R.estimand.measure.label ? R.estimand.measure.label + ', latest 12 months' : latest.label, value: String(latest.text),
        sub: prior ? String(prior.text) + ' in the 12 months before' : '', grade: itemGrade(latest).grade, process: proc });
      if (pct) kpis.push({ label: 'Change on the 12 months before', value: String(pct.text),
        sub: [chg ? String(chg.text) : '', basisF && interval(basisF.why) && !proc ? '95% interval ' + interval(basisF.why) : ''].filter(Boolean).join('; ') + (proc ? ' (described)' : ''), grade: itemGrade(pct).grade, process: proc });
    } else if (kfs.length) {
      kfs.forEach(function (k) { kpis.push({ label: String(k.label || ''), value: String(k.text), sub: '', grade: gradeOf(k.grade) }); });
    } else {
      lead = primaryOf();
      if (lead) {
        var lv = levelOf(lead);
        if (lv) kpis.push({ label: lv.label, value: lv.latest, sub: lv.prior + ' in the 12 months before', grade: lead.verdict });
        var t0 = tileOf(lead, lv ? 'Change on the 12 months before' : '');
        if (t0.value) kpis.push(t0);
      }
    }
    if (!kfs.length || latest || pct) biz.filter(function (f) { return f !== lead && f !== basisF; }).slice(0, kpis.length ? 1 : 3).forEach(function (f) { var t = tileOf(f); if (t.value) kpis.push(t); });
    // the health score's tile says what set it (results.health_explain: final evaluation, 1 Oct 2026, "0.0" for a clean
    // file whose newest row was 3.5 years old, with nothing saying why)
    var hx = R && typeof R.health_explain === 'string' ? R.health_explain.trim() : '';
    if (R && isFinite(R.health_score) && R.health_score !== null && kpis.length < 4) kpis.push(hx ? { label: 'Data health score', value: Number(R.health_score).toFixed(1), sub: 'Out of 100: ' + hx, whole: true }
      : { label: 'Data health score', value: Number(R.health_score).toFixed(1), sub: 'out of 100, from the engine\'s own checks' });

    // ---- Part 1: what drove it (the headline, what drove it, other findings, and any section no other part takes)
    var p1 = [], n1 = 0;
    P.secs.forEach(function (s, i) {
      if (['headline', 'drove', 'other', 'num', 'extra'].indexOf(s.kind) < 0) return;
      if (!s.head && !s.blocks.length) return;
      n1 += 1;
      p1.push({ type: 'h2', num: String(n1), text: s.head.replace(/^\d+[.)]?\s+/, '') || 'More from the report', id: 'p1-' + i });
      append(p1, s.blocks);
    });
    // where the change sits: the engine's contribution table and its price, volume and mix split, when the AI placed neither
    var droveIdx = -1;
    // the adapter's title, "Where the change in <measure> came from" (30 Sep 2026; "What drove the change in <measure>"
    // before, as a saved report may still carry)
    tables.forEach(function (t, i) { if (t && /^(what drove the change|where the change in\b)/i.test(String(t.title || ''))) droveIdx = i; });
    var pvm = items.filter(function (x) { return x.group === 'price_volume_mix'; });
    // ... and the contribution waterfall, the engine's viz record (in the charts, or in results.viz), when the AI placed
    // none: drawn above the table, as the AI would have placed it
    var wfOf = function (c) { var v = vizOf(c); return v && v.chart === 'contribution_waterfall' ? v : null; };
    var wf = null;
    if (!charts.some(function (c, i) { return usedCharts[i] && wfOf(c); })) {
      wf = charts.filter(wfOf)[0] || null;
      if (!wf && R && isObj(R.viz) && Array.isArray(R.viz.charts)) wf = R.viz.charts.filter(wfOf)[0] || null;
    }
    if ((droveIdx >= 0 && !usedTables[droveIdx]) || pvm.length || wf) {
      n1 += 1;
      p1.push({ type: 'h2', num: String(n1), text: 'Where the change sits', id: 'p1-sits' });
      if (basis && basis.claim) p1.push({ type: 'p', text: 'The engine split the change in ' + (basis.measure || 'the measure') + ' (' + basis.claim.charAt(0).toLowerCase() + basis.claim.slice(1) + ', graded ' + (GRADE_LABEL[gradeOf(basis.grade)] || basis.grade || 'as shown') + ') by where it sits. A contribution says where the change sits, not what caused it.' });
      if (wf) append(p1, chartBlocks(wf));
      if (droveIdx >= 0 && !usedTables[droveIdx]) p1.push(tableBlock(tables[droveIdx], droveIdx));
      if (pvm.length) p1.push({ type: 'table', table: { title: 'Price, volume and mix', cols: ['Effect', 'Amount'], rows: pvm.map(function (x) { return [x.label, String(x.text)]; }) },
        source: 'Source: NorthLedger engine, computed in the reader\'s browser from ' + fileWord + '. Price, volume and mix add up to the change; each is arithmetic on that change, with no grade of its own.' });
    }
    if (!p1.length) p1.push({ type: 'p', text: 'The report has no section on what drove the result.' });

    // ---- Part 2: in the real world
    var p2 = [];
    secsOf('world').forEach(function (s) { append(p2, s.blocks); });
    if (!p2.length) p2.push({ type: 'p', text: sources.length ? 'The AI report has no separate section on outside context; its sources are listed in Appendix B.'
      : 'This report cites no outside source: no web search was run for it, or none returned a usable source. Everything in it comes from the file.' });

    // ---- Part 3: scenarios (the AI's words, then the engine's cards; forecast base, low and high only when present).
    // A block the engine adds is marked engine: true (the page draws those under the AI's own Scenarios section); a
    // table the AI already placed in its scenarios is not drawn again.
    var p3 = [];
    secsOf('scen').forEach(function (s) { append(p3, s.blocks); });
    var placed = p3.filter(function (b) { return b.type === 'table'; }).map(function (b) { return tableSig(b.table); });
    var already = function (t) { var g = tableSig(t); return placed.some(function (x) { return (g.title && x.title === g.title) || (g.cols && x.cols === g.cols); }); };
    var cardOf = function (x, name, strong) { var g = itemGrade(x); return { name: name, value: String(x.text), unit: x.label, assumption: x.assumes || '', grade: g.grade, parent: g.parent, strong: !!strong }; };
    var rr = items.filter(function (x) { return x.group === 'run_rate' || x.group === 'sensitivity'; });
    var gaps = items.filter(function (x) { return x.group === 'gap'; });
    var fc = items.filter(function (x) { return x.group === 'forecast'; });
    var any = rr.length || gaps.length || fc.length;
    if (rr.length) {
      p3.push({ type: 'h2', num: '', text: 'Run rate and sensitivity', id: 'sc-rr', engine: true });
      p3.push({ type: 'cards', engine: true, items: rr.map(function (x) { return cardOf(x, x.group === 'sensitivity' ? 'Sensitivity' : /month/i.test(x.id) ? 'Run rate, a month' : 'Run rate, a year', x.id === 'run_rate.year'); }) });
    }
    if (gaps.length) {
      var segs = [];
      gaps.forEach(function (x) { if (x.segment && segs.indexOf(x.segment) < 0) segs.push(x.segment); });
      var col = basis && basis.segment && basis.segment.column ? basis.segment.column : 'segment';
      var gi = function (sg, part) { var x = gaps.filter(function (y) { return y.segment === sg && new RegExp('\\.' + part + '$').test(y.id); })[0]; return x ? String(x.text) : ''; };
      var pu = gaps.filter(function (y) { return /\.per_unit$/.test(y.id); })[0];
      var gapT = { title: 'Gap to the largest ' + col + ', the latest 12 months', cols: [cap1(col), 'Below the largest', 'Share gap', 'What if at the largest\'s rate per unit'],
        rows: segs.map(function (sg) { return [sg, gi(sg, 'amount'), gi(sg, 'points'), gi(sg, 'per_unit')]; }) };
      if (!already(gapT)) {
        p3.push({ type: 'h2', num: '', text: 'The gap to the largest ' + col, id: 'sc-gap', engine: true });
        p3.push({ type: 'table', engine: true, table: gapT, source: 'Source: NorthLedger engine.' + (pu && pu.assumes ? ' The what-if assumes ' + pu.assumes + '; it is arithmetic, not a forecast.' : '') });
      }
    }
    if (fc.length) {
      var hs = []; fc.forEach(function (x) { var h = (x.id.match(/^forecast\.(\d+)\./) || [])[1]; if (h && hs.indexOf(+h) < 0) hs.push(+h); });
      hs.sort(function (a, b) { return a - b; });
      var H = hs[hs.length - 1], f = function (h, part) { return item('forecast.' + h + '.' + part); };
      p3.push({ type: 'h2', num: '', text: 'Low, base and high cases', id: 'sc-fc', engine: true });
      var fcards = ['low', 'base', 'high'].map(function (part) { var x = f(H, part); return x ? cardOf(x, part === 'base' ? 'Base case' : part === 'low' ? 'Low case' : 'High case', part === 'base') : null; }).filter(Boolean);
      if (fcards.length) p3.push({ type: 'cards', engine: true, items: fcards });
      var fcT = { title: 'The forecast added up', cols: ['Months ahead', 'Low', 'Base', 'High'],
        rows: hs.map(function (h) { return [String(h), f(h, 'low') ? String(f(h, 'low').text) : '', f(h, 'base') ? String(f(h, 'base').text) : '', f(h, 'high') ? String(f(h, 'high').text) : '']; }) };
      if (hs.length > 1 && !already(fcT)) p3.push({ type: 'table', engine: true, table: fcT, source: 'Source: NorthLedger engine forecast; the low and high cases add up each month\'s own range.' });
    }
    // the historical range of a level (group history_range: engine/nl_scenarios.py _history_range): for a rate, a price
    // or an index the engine does not forecast, how far its monthly average moved in the file's own past 12-month and
    // 3-month windows. Facts about the past: a card per figure with a neutral HISTORY label (never a grade's pill),
    // the longest window first, each card's words naming its window count; the engine's own sentences under them.
    var hist = items.filter(function (x) { return x.group === 'history_range'; }), hb = [];
    if (hist.length) {
      var lags = [];
      hist.forEach(function (x) { var lg = (String(x.id).match(/^history_range\.m(\d+)\./) || [])[1]; if (lg && lags.indexOf(+lg) < 0) lags.push(+lg); });
      lags.sort(function (a, b) { return b - a; });
      var hi = function (lg, part) { return item('history_range.m' + lg + '.' + part); };
      var hcards = [], hsays = [], hzero = '';
      lags.forEach(function (lg) {
        var win = hi(lg, 'windows'), rose = hi(lg, 'rose');
        var of = win ? 'the ' + String(win.text) + ' past windows' : 'the past ' + lg + '-month windows';
        // the overlapping windows are worth fewer independent ones: "the 104 past windows (about 13 independent)"
        var ne = hi(lg, 'n_eff');
        if (win && ne) of = 'the ' + String(win.text) + ' past windows (about ' + String(ne.text) + ' independent)';
        [['p10', '1 in 10 lower', '; 1 in 10 of ' + of + ' was lower'], ['p50', 'Middle', ', the middle of ' + of + (rose ? '; it rose in ' + String(rose.text) + ' of them' : '')],
          ['p90', '1 in 10 higher', '; 1 in 10 of ' + of + ' was higher']].forEach(function (pp) {
          var x = hi(lg, pp[0]);
          if (x) hcards.push({ name: lg + '-month: ' + pp[1], value: String(x.text), unit: 'The change in the monthly average' + pp[2], assumption: '', grade: '', parent: '', tag: 'history' });
        });
        if (win && win.label) hsays.push(sentence(win.label));
        // the non-overlapping alternative: one window each year (or each quarter), none counted twice
        var no = { n: hi(lg, 'nonoverlap.n'), min: hi(lg, 'nonoverlap.min'), med: hi(lg, 'nonoverlap.median'), max: hi(lg, 'nonoverlap.max') };
        if (no.n && no.min && no.max) {
          [['nonoverlap.min', 'lowest'], ['nonoverlap.median', 'middle'], ['nonoverlap.max', 'highest']].forEach(function (pp) {
            var x = hi(lg, pp[0]);
            if (x) hcards.push({ name: lg + '-month, spaced: ' + pp[1], value: String(x.text), unit: 'The ' + pp[1] + ' of the ' + String(no.n.text) + ' non-overlapping ' + lg + '-month changes in the monthly average', assumption: '', grade: '', parent: '', tag: 'history' });
          });
        }
      });
      hist.forEach(function (x) { if (!hzero && x.assumes) hzero = sentence(x.assumes); });
      if (hcards.length) {
        hb.push({ type: 'h2', num: '', text: 'What past moves looked like (history, not a forecast)', id: 'sc-hist', engine: true });
        hb.push({ type: 'cards', engine: true, items: hcards, note: hsays.concat(hzero ? [hzero] : [], ['Facts about the file\'s own past, not graded: how far the average moved before, not how far it will move.']).join(' ') });
      }
    }
    if (any) append(p3, hb);
    if (any && sc && sc.note) p3.push({ type: 'p', engine: true, text: sc.note, wide: true, size: 7.5, lead: 10.5, font: 'I', color: COL.muted, after: 10 });
    if (!any) {
      var seenWhy = {}, why = sc && Array.isArray(sc.refused) && sc.refused.length ? sc.refused.filter(function (x) { return !LEFT_OUT.test(String(x)); }).map(function (x) { return cap1(String(x).replace(/[.\s]+$/, '')) + '.'; })
        .filter(function (x) { if (seenWhy[x]) return false; seenWhy[x] = true; return true; }).join(' ') : '';
      if (!why) { var nf = findings.filter(function (x) { return x.kind === 'forecast' && gradeOf(x.verdict) === 'INSUFFICIENT'; })[0]; if (nf && nf.why) why = 'No forecast: ' + nf.why; }
      if (!why) why = R ? 'The engine computed no run rate, sensitivity, gap or forecast for this file.'
        : inp.shared ? 'This shared copy doesn\'t carry the engine\'s full results; the figures shown are those in the report text.'
          : 'The engine\'s results were not kept with this saved report, so its scenario figures are not in this file.';
      p3.push({ type: 'noscenarios', engine: true, reason: why });
      // no scenario to add up, and the level's past range instead: the reason first, then the history
      append(p3, hb);
    }

    // the forecast and its back-test (nl_inference.forecast_audit): the engine's range and how it held when the shown model
    // was refitted at the last origins; a failed back-test marks the engine's grade "not trusted"; a row-count forecast the
    // table's layout fixes is one line saying why, never a number
    var FCR = R && R.forecast, fcb = [];
    if (FCR && (FCR.row_forecast_dropped === true || FCR.available || AV)) {
      fcb.push({ type: 'h2', num: '', text: 'The forecast and how its range held when back-tested', id: 'sc-audit', engine: true });
      if (FCR.available && Array.isArray(FCR.points) && FCR.points.length) {
        var pts = [0, 2, 5, 11].filter(function (k) { return k < FCR.points.length; }).map(function (k) { return FCR.points[k]; });
        var money = function (v) { return isFinite(v) && v !== null ? (U && Math.abs(v) >= 1e6 ? U(v) : fmtVal(v)) : 'n/a'; };
        fcb.push({ type: 'table', engine: true, table: { title: 'The engine\'s forecast' + (FCR.series ? ': ' + String(FCR.series).replace(/_/g, ' ') : ''), cols: ['Month', 'Forecast', '80% range'],
          rows: pts.map(function (q) { return [String(q.date), money(q.value), q.lo !== null && q.hi !== null && q.lo !== undefined ? money(q.lo) + ' to ' + money(q.hi) : 'n/a']; }) },
          source: 'Source: NorthLedger engine. The engine\'s grade of the forecast: ' + (FCR.verdict ? String(FCR.verdict).toUpperCase() : 'not stated') + (FCR.champion ? '; the model: ' + FCR.champion : '') + (FCR.baseline_won === true ? ' (the simple rule won the engine\'s replay)' : '') + '.' });
      } else if (FCR.row_forecast_dropped === true) {
        fcb.push({ type: 'p', engine: true, text: String(FCR.reason || 'No forecast of the rows a month is shown: rows per month are fixed by the table\'s layout.') });
      }
      if (AV) fcb.push({ type: 'callout', engine: true, label: AV.untrusted ? 'Back-test: not trusted' : 'Back-test of the range shown', size: 9.5, rule: AV.untrusted ? COL.grade.WATCH : COL.accent,
        labelColor: AV.untrusted ? COL.grade.WATCH : COL.accentInk, text: cap1(AV.line) + '.' + (AV.untrusted ? ' The engine\'s grade of the forecast stands, but it is not trusted: the back-test of its range failed.' : AV.status === 'passes' ? ' A passed back-test says the range held often enough and the model is no worse than the simple rule; it is not a promise.' : ' A back-test that neither passes nor fails says the evidence is too thin to settle it.') });
    }
    append(p3, fcb);

    // ---- Part 4: what to do; Part 5: the risks and what the data cannot say
    var p4 = [], todo = secsOf('todo');
    todo.forEach(function (s) { append(p4, s.blocks); });
    if (!p4.length) p4.push({ type: 'p', text: todo.length ? 'The AI report\'s actions section is empty.' : 'The AI report has no separate actions section.' });
    var p5 = [];
    secsOf('risks').forEach(function (s) { append(p5, s.blocks); });
    if (R && Array.isArray(R.quality_risks) && R.quality_risks.length) p5.push({ type: 'h2', num: '', text: 'Risks the plan named before the analysis', id: 'qr' }, { type: 'bullets', items: R.quality_risks.map(String) });
    if (!p5.length) p5.push({ type: 'p', text: 'The engine compares periods in observational data: a change it reports is an association with the period, not a finding about its cause.' });

    // ---- about this report (the cover's notice and Appendix A's first section). A figure may be the engine's or quoted
    // from a source the same sentence cites: the honesty check lets nothing else through (final review, 30 Sep 2026:
    // the notice said every figure was the engine's, beside a sentence quoting a cited source's figure). A removed
    // sentence carried a figure the check could not match, said whole (integration pass, 1 Oct 2026: a live PDF's
    // sentence stopped at its word "neither", right before "No person reviewed this report."; and a removed figure may
    // be the engine's own, counted in other words)
    var rep = typeof inp.repaired === 'number' && inp.repaired >= 0 ? inp.repaired : null;
    var kept = Array.isArray(inp.kept) ? inp.kept.map(String) : null;
    var notice = 'Every figure in this report was computed by the NorthLedger engine from the reader\'s own file, in their browser, or quoted from a source cited in the same sentence. An AI model' + (inp.model ? ' (' + inp.model + ')' : '') +
      ' worded the text; an honesty check compared each figure with the engine\'s results and the cited sources' +
      (rep === null ? '' : rep === 0 ? ' and removed no sentence' : ' and removed ' + rep + ' sentence' + (rep === 1 ? ', which carried' : 's, each carrying') +
        ' a figure the check could not match to the engine\'s results or to a source cited in the same sentence') +
      '. No person reviewed this report. ' + (inp.shared ? 'This copy was written from a shared link\'s stored report; the reader\'s file was never uploaded.' : 'It was generated in the reader\'s browser; nothing was sent to make this file.');
    if (kept && kept.length) notice += ' The reader chose to send these personal columns to the AI: ' + kept.join(', ') + '; this report may contain their values.';
    else if (!kept) notice += inp.shared ? ' This shared copy does not record which personal columns, if any, were sent to the AI, so it may contain personal values.'
      : ' This report was saved before the personal columns sent with it were recorded, so it may contain personal values.';
    // a step of the AI plan that set aside a tenth of the rows or more (results.plan_row_drops[].notice, the adapter's
    // _row_drops): said here, on the cover and in Appendix A, so no reader takes the figures for the whole file
    var drops = R && Array.isArray(R.plan_row_drops) ? R.plan_row_drops.filter(function (d) { return d && typeof d === 'object' && typeof d.text === 'string' && d.text; }) : [];
    drops.forEach(function (d) { if (typeof d.notice === 'string' && d.notice) notice += ' ' + d.notice; });
    var removed = Array.isArray(inp.removed_figures) ? inp.removed_figures.map(String).filter(Boolean).slice(0, 20) : [];

    // ---- Appendix A: method and data quality; Appendix B: references
    var pa = [{ type: 'h2', num: '', text: 'How this report was made', id: 'm-how' }, { type: 'p', text: notice }];
    if (removed.length) pa.push({ type: 'p', text: 'Figures in the removed sentences: ' + removed.join(', ') + '.', size: 8.5 });
    if (R) {
      if (R.partial) pa.push({ type: 'p', text: 'This shared copy carries the engine\'s key figures and scenario figures, not its full results.', size: 8.5 });
      if (R.reading) pa.push({ type: 'h2', num: '', text: 'The data', id: 'm-data' }, { type: 'p', text: R.reading });
      // a column the scan flagged as free text and the engine read as a category (privacy.released): said here as the reader
      // was told on the consent step; the report's figures may show its labels
      var rel = R.privacy && Array.isArray(R.privacy.released) ? R.privacy.released.filter(function (x) { return x && x.text; }) : [];
      if (rel.length) pa.push({ type: 'h2', num: '', text: 'Columns read as categories, not personal data', id: 'm-released' }, { type: 'bullets', items: rel.map(function (x) { return String(x.text); }) });
      if (EV) {
        var chk = (R.estimand.sum_checks || []).filter(function (c) { return c && c.dim; });
        pa.push({ type: 'h2', num: '', text: 'The table\'s structure and what was checked', id: 'm-structure' });
        if (chk.length) pa.push({ type: 'table', table: { title: 'Each total checked against its parts', cols: ['Dimension', 'Total', 'Parts', 'Months checked', 'Within tolerance', 'Largest gap', 'Not allocated, latest 12 months'],
          rows: chk.map(function (c) { return [String(c.dim), String(c.total), c.parts === undefined || c.parts === null ? '' : String(c.parts), isFinite(c.complete_cells) ? String(c.complete_cells) : '', isFinite(c.within_tolerance) ? String(c.within_tolerance) : '',
            c.max_residual && c.max_residual.text ? String(c.max_residual.text) : isFinite(c.max_rel_residual) ? (c.max_rel_residual < 0.0001 ? 'under 0.01%' : (100 * c.max_rel_residual).toFixed(2) + '%') : '', c.unallocated_latest && c.unallocated_latest.text ? String(c.unallocated_latest.text) : '']; }) },
          source: 'Source: NorthLedger engine, from the table\'s own published series. A total adds up when it is within half a unit of the last published digit of its parts on 95% or more of the months where every part has a value.' });
        var fl = R.structure && R.structure.flags, byk = fl && fl.by_kind ? Object.keys(fl.by_kind).map(function (k) { return Number(fl.by_kind[k]).toLocaleString('en-US') + ' rows ' + k.replace(/_/g, ' '); }) : [];
        var qh = EV.quality && EV.quality.codes ? Object.keys(EV.quality.codes).map(function (k) { return (k === '' ? 'no mark' : 'mark ' + k) + ' on ' + EV.quality.codes[k] + ' month' + (EV.quality.codes[k] === 1 ? '' : 's'); }) : [];
        if (byk.length || qh.length) pa.push({ type: 'bullets', items: [byk.length ? 'The publisher\'s flags in the file: ' + byk.join(', ') + '.' : '', qh.length ? 'Quality marks on the headline\'s months: ' + qh.join(', ') + '.' : ''].filter(Boolean) });
      }
      var tests = analyses.filter(function (a) { return a && a.test; });
      if (tests.length) pa.push({ type: 'h2', num: '', text: 'The trend test and its size', id: 'm-trend' }, { type: 'bullets', items: tests.map(function (a) { return trendLine(a.test, a.title); }).filter(Boolean) });
      var cut = sc && Array.isArray(sc.refused) ? sc.refused.filter(function (x) { return LEFT_OUT.test(String(x)); })[0] : '';
      if (cut) pa.push({ type: 'p', text: 'Scenario detail left out: ' + cap1(String(cut).replace(/[.\s]+$/, '')) + '.', size: 8.5 });
      // every step of the AI plan that set rows aside: its count, its share of the file's rows, the plan's reason and
      // the engine's check of it (the adapter's words)
      if (drops.length) pa.push({ type: 'h2', num: '', text: 'Rows the AI plan set aside', id: 'm-drops' }, { type: 'bullets', items: drops.map(function (d) { return d.text; }) });
      var cl = R.cleaning || {}, drows = [];
      var dropN = drops.reduce(function (a, d) { return a + (isFinite(d.rows) ? Number(d.rows) : 0); }, 0), fileN = drops.length && isFinite(drops[0].of) ? Number(drops[0].of) : 0;
      if (dropN && fileN) drows.push(['Rows in the file', fileN.toLocaleString('en-US')], ['Rows the AI plan set aside', dropN.toLocaleString('en-US') + ' (' + fmtShare(100 * dropN / fileN) + ')']);
      if (rows !== undefined && rows !== null) drows.push(['Rows read', Number(cl.rows_in !== undefined && cl.rows_in !== null ? cl.rows_in : rows).toLocaleString('en-US')]);
      if (cl.rows_clean !== undefined && cl.rows_clean !== null) drows.push(['Rows analysed', Number(cl.rows_clean).toLocaleString('en-US')]);
      if (cl.rows_quarantined !== undefined && cl.rows_quarantined !== null) drows.push(['Rows set aside', Number(cl.rows_quarantined).toLocaleString('en-US')]);
      if (cols) drows.push(['Columns', String(cols)]);
      if (isFinite(R.health_score) && R.health_score !== null) drows.push(['Data health score (0 to 100)', Number(R.health_score).toFixed(1)]);
      if (hx && isFinite(R.health_score) && R.health_score !== null) drows.push(['What set the health score', hx]);
      if (drows.length) pa.push({ type: 'table', table: { title: 'The data and its cleaning', cols: ['Item', 'Value'], rows: drows }, source: srcNote('') });
      if (Array.isArray(cl.fixes) && cl.fixes.length) pa.push({ type: 'h2', num: '', text: 'What the cleaning did', id: 'm-clean' }, { type: 'bullets', items: cl.fixes.map(String) });
      if (Array.isArray(R.health_issues) && R.health_issues.length) pa.push({ type: 'h2', num: '', text: 'Data health issues', id: 'm-issues' }, { type: 'bullets', items: R.health_issues.map(String) });
      if (findings.length) {
        pa.push({ type: 'h2', num: '', text: 'Every finding and its grade', id: 'm-find' });
        pa.push({ type: 'findings', items: findings.map(function (x) {
          var ch = x.kind === 'business' && /^change in /i.test(x.claim) && isFinite(x.value);
          // a row-count forecast the table's layout fixes: one line in the forecast block says why; here no number and no grade
          if (x.layout_artifact === true) return { claim: x.claim, value: 'not shown', grade: '' };
          var big = U && isFinite(x.value) && x.value !== null && x.kind === 'forecast' && Math.abs(x.value) >= 1e6;
          return { claim: x.claim, value: ch ? sgnPct(x.value) : big ? U(x.value) : isFinite(x.value) && x.value !== null ? fmtVal(x.value) : '', grade: x.verdict,
            process: !!(EV && EV.process && R.primary && x.claim === (R.primary.claim || R.primary)), untrusted: !!(AV && AV.untrusted && x.kind === 'forecast') };
        }) });
      }
      if (Array.isArray(R.limitations) && R.limitations.length && !secsOf('risks').length) pa.push({ type: 'h2', num: '', text: 'The engine\'s limitations', id: 'm-lim' }, { type: 'bullets', items: R.limitations.map(String) });
    } else {
      pa.push({ type: 'p', text: inp.shared ? 'This shared copy doesn\'t carry the engine\'s full results; the figures shown are those in the report text, with the engine\'s charts and tables it placed.'
        : 'This report was saved before the engine\'s results were kept with it, so its method and data-quality details are not in this file. The engine\'s charts and tables it placed are.' });
    }
    // the figures' tables of more than 12 rows, in the order of their figures
    var figTabs = [];
    [p1, p2, p3, p4, p5].forEach(function (list) { list.forEach(function (b) { if (b.tabs) { append(figTabs, b.tabs); delete b.tabs; } }); });
    if (figTabs.length) { pa.push({ type: 'h2', num: '', text: 'The tables of the figures', id: 'm-figtabs' }); append(pa, figTabs); }
    pa.push({ type: 'callout', label: 'How to read the grades', size: 8.5, text: 'CONFIRMED: the change clears the engine\'s bar and its interval excludes no change. WATCH: the estimate moved but its interval is too wide, or a rule holds it back; a possibility, not a planning number. INSUFFICIENT (shown as NOT ENOUGH DATA): the file cannot support the claim; the engine says what would settle it. A figure marked "from a CONFIRMED change" or "from a WATCH change" (a run rate, a sensitivity, a gap, a contribution) is arithmetic on that change and has no grade of its own. A grade is the engine\'s rule, not a probability that the claim is true.' });
    var pb = sources.length ? [{ type: 'references', items: sources }] : [{ type: 'p', text: 'This report cites no outside source.' }];

    var title = P.title || (R && R.story && R.story.headline) || 'NorthLedger data report';
    var exec = execAt >= 0 ? P.secs[execAt] : null, summary = [];
    if (exec) exec.blocks.forEach(function (b) { if (b.type === 'bullets' || b.type === 'numbered') append(summary, b.items); else if (b.type === 'p') summary.push(b.text); });
    var question = String(inp.goal || (R && R.goal) || '').split(FILE_WORD).join(fileWord);
    return {
      title: title, shortTitle: title, question: question, model: inp.model || '', date: date,
      subject: showName ? String(inp.name) : 'The reader\'s file (its name is left off unless they choose to show it)',
      dataLine: (rows ? Number(rows).toLocaleString('en-US') + ' rows' + (cols ? ' \u00d7 ' + cols + ' columns' : '') + '; ' : '') + 'analysed in the reader\'s browser; the file was never uploaded', shared: inp.shared === true,
      preparedBy: 'The NorthLedger engine (every figure it computed) and ' + (inp.model || 'an AI model') + ' (the wording, and any figure quoted from a source it cites), each figure checked against the engine\'s results or that source',
      reportId: reportId(inp.report), kept: kept || [], notice: notice, headline: title, kpis: kpis.slice(0, 4), summary: summary, estimand: EV,
      parts: [
        { kicker: 'Part 1' + DOT + 'What drove it', title: 'What the engine found, and why', id: 'drivers', blocks: p1 },
        { kicker: 'Part 2' + DOT + 'In the real world', title: 'What it means outside this file', id: 'context', blocks: p2 },
        { kicker: 'Part 3' + DOT + 'Scenarios', title: 'What the figures add up to', id: 'scenarios', blocks: p3 },
        { kicker: 'Part 4' + DOT + 'What to do', title: 'Recommended actions', id: 'actions', blocks: p4 },
        { kicker: 'Part 5' + DOT + 'Risks and limits', title: 'What the data cannot say', id: 'risks', blocks: p5 },
        { kicker: 'Appendix A', title: 'Method and data quality', id: 'method', blocks: pa },
        { kicker: 'Appendix B', title: 'References', id: 'refs', blocks: pb }]
    };
  }

  // The engine's results trimmed for a share link (the page sends them in the /share body; the worker keeps them for
  // the shared PDF): at most SHARE_MAX_BYTES as JSON, marked partial. The scenario block with at most 60 items, the
  // findings the key figures and the graded-findings table read, the story lines the key figures quote, the input's
  // size, the cleaning counts and the health score. Never the file's name, and no chart or table (the body carries those
  // already). maxBytes (optional) is a tighter cap (the page's share-size guard, 30 Sep 2026); null when even the
  // input's size, the cleaning counts and the health score are over it. Each item keeps its value: the worker checks an
  // item's text against its value and drops one without (integration pass, 30 Sep 2026: every item of a share was
  // dropped).
  // The items are trimmed by the worker's own priority (insight-proxy/src/report.js capScenarioItems), never from the
  // tail: the contract test of 30 Sep 2026 found the tail cut dropped the run rate, the sensitivity, the gaps and the
  // facts from 4 of 6 shared PDFs (the adapter's order puts every segment's figures per unit before them). More than 60
  // items: capItems, the worker's cut ported. Over the byte cap, what goes first is what the shared PDF draws least:
  // the per-segment detail beyond the SHARE_TOP_SEGMENTS segments with the largest contributions (the smallest
  // segment's last item first), then the other segments' figures per unit, then the facts; then the findings that are
  // not about the business and the story. The core goes last: the headline, the top segments' contributions, price,
  // volume and mix, the overall figures per unit, the run rate, the sensitivity, the top segments' gaps, the
  // forecast and a level's historical range; its segments shrink first (the sixth, then the fifth, ...), then its
  // groups go from the end of the order (the history before the forecast). Then the other findings, the scenario
  // block and the primary claim. What a later, larger cut made room for comes back, the most important first (the
  // story alone can free room for the findings cut before it). The items kept stay in the adapter's order.
  var SHARE_MAX_BYTES = 20000, SHARE_MAX_ITEMS = 60, SHARE_TOP_SEGMENTS = 6;
  // the worker's SCENARIO_PRIORITY (insight-proxy/src/report.js): a level's historical range (history_range) after the
  // forecast and before the facts (it was missing here until 30 Sep 2026, and a group not on this list goes first
  // when a cap bites)
  var SCEN_GROUPS = ['headline', 'contribution', 'price_volume_mix', 'per_unit', 'run_rate', 'sensitivity', 'gap', 'forecast', 'history_range', 'facts'];
  var PER_SEGMENT = ['contribution', 'per_unit', 'gap'];
  function utf8Len(s) {
    var n = 0;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 0xD800 && c <= 0xDBFF) { n += 4; i++; } else n += c < 0x80 ? 1 : c < 0x800 ? 2 : 3; }
    return n;
  }
  // each item's segment rank as the worker ranks segments (a segment with a contribution first, then by the size of
  // its contribution to the change, an amount and not a percent, then by where it first appears); -1 for an item
  // outside the per-segment groups or with no segment
  function segRanks(items) {
    var key = function (s) { return '~' + s; }, first = {}, size = {}, seen = [];
    items.forEach(function (it) {
      if (typeof it.segment !== 'string') return;
      var k = key(it.segment);
      if (!Object.prototype.hasOwnProperty.call(first, k)) { first[k] = seen.length; seen.push(it.segment); }
      if (it.group === 'contribution' && it.kind === 'change' && it.unit !== '%' && typeof it.value === 'number' && isFinite(it.value)) size[k] = Math.max(size[k] || 0, Math.abs(it.value));
    });
    var has = function (s) { return Object.prototype.hasOwnProperty.call(size, key(s)); };
    var ranked = seen.slice().sort(function (a, b) { return (Number(has(b)) - Number(has(a))) || ((size[key(b)] || 0) - (size[key(a)] || 0)) || (first[key(a)] - first[key(b)]); });
    var rank = {};
    ranked.forEach(function (s, i) { rank[key(s)] = i; });
    return items.map(function (it) { return PER_SEGMENT.indexOf(it.group) >= 0 && typeof it.segment === 'string' ? rank[key(it.segment)] : -1; });
  }
  // the worker's capScenarioItems: at most max items, the core (every group, the per-segment groups cut to the top
  // segments, fewer if even that is over) and then the next segments' items, the higher-ranked first
  function capItems(items, max, top) {
    if (items.length <= max) return items;
    var seg = segRanks(items), prio = function (i) { return SCEN_GROUPS.indexOf(items[i].group); };
    var all = items.map(function (_, i) { return i; }), n = top;
    var coreOf = function (m) { return all.filter(function (i) { return seg[i] < m; }); };
    var core = coreOf(n);
    while (core.length > max && n > 0) { n -= 1; core = coreOf(n); }
    var kept;
    if (core.length > max) kept = core.slice().sort(function (a, b) { return (prio(a) - prio(b)) || (a - b); }).slice(0, max);
    else kept = core.concat(all.filter(function (i) { return seg[i] >= n; }).sort(function (a, b) { return (seg[a] - seg[b]) || (prio(a) - prio(b)) || (a - b); }).slice(0, max - core.length));
    var keep = {};
    kept.forEach(function (i) { keep[i] = true; });
    return items.filter(function (_, i) { return keep[i]; });
  }
  // the items' indices in the order the byte cap drops them: {detail: before the findings, core: after them}
  function dropOrder(items, top) {
    var seg = segRanks(items), prio = function (i) { return SCEN_GROUPS.indexOf(items[i].group); };
    var all = items.map(function (_, i) { return i; }), taken = {};
    var take = function (list) { list.forEach(function (i) { taken[i] = true; }); return list; };
    var tail = function (a, b) { return (seg[b] - seg[a]) || (b - a); };
    var unknown = take(all.filter(function (i) { return prio(i) < 0; }).reverse());
    var beyond = take(all.filter(function (i) { return !taken[i] && seg[i] >= top; }).sort(tail));
    var units = take(all.filter(function (i) { return !taken[i] && items[i].group === 'per_unit' && seg[i] >= 0; }).sort(tail));
    var facts = take(all.filter(function (i) { return !taken[i] && items[i].group === 'facts'; }).reverse());
    var coreSeg = take(all.filter(function (i) { return !taken[i] && seg[i] >= 0; }).sort(tail));
    var coreRest = all.filter(function (i) { return !taken[i]; }).sort(function (a, b) { return (prio(b) - prio(a)) || (b - a); });
    return { detail: unknown.concat(beyond, units, facts), core: coreSeg.concat(coreRest) };
  }
  function shareResults(R, maxBytes) {
    if (!R || typeof R !== 'object') return null;
    var cap = typeof maxBytes === 'number' && isFinite(maxBytes) ? Math.max(0, Math.min(SHARE_MAX_BYTES, Math.floor(maxBytes))) : SHARE_MAX_BYTES;
    var sc = R.scenarios && typeof R.scenarios === 'object' ? R.scenarios : null, str = function (v, n) { return String(v === null || v === undefined ? '' : v).slice(0, n); };
    var ITEM_KEYS = ['id', 'group', 'segment', 'label', 'value', 'text', 'kind', 'unit', 'grade', 'parent_grade', 'grade_words', 'assumes'];
    var findings = (Array.isArray(R.findings) ? R.findings : []).filter(function (f) { return f && f.claim; }).slice(0, 20).map(function (f) {
      var o = { claim: str(f.claim, 300), kind: str(f.kind, 20), verdict: str(f.verdict, 20) };
      if (isFinite(f.value) && f.value !== null) o.value = f.value;
      if (f.why) o.why = str(f.why, 240);
      return o;
    });
    var story = R.story && Array.isArray(R.story.what_happened) ? { what_happened: R.story.what_happened.slice(0, 8).map(function (x) { return str(x, 300); }) } : null;
    var items = sc ? capItems((Array.isArray(sc.items) ? sc.items : []).filter(function (x) { return x && typeof x === 'object'; }), SHARE_MAX_ITEMS, SHARE_TOP_SEGMENTS).map(function (x) {
      var o = {}; ITEM_KEYS.forEach(function (k) { if (x[k] !== undefined) o[k] = typeof x[k] === 'string' ? str(x[k], k === 'label' ? 400 : 300) : x[k]; }); return o;
    }) : [];
    // gone: what the cap took ('i' + an item's index, 'f' + a finding's, 'story', 'scen', 'primary')
    var gone = {};
    // a segment's items go together: a gap item is never shared without its segment's contribution (final review,
    // 30 Sep 2026: putting cut items back one at a time put back a gap whose contribution did not fit)
    var segKey = function (i) { var x = items[i]; return PER_SEGMENT.indexOf(x.group) >= 0 && typeof x.segment === 'string' ? '~' + x.segment : null; };
    var contribOf = {};
    items.forEach(function (x, i) { var k = segKey(i); if (k && x.group === 'contribution') (contribOf[k] = contribOf[k] || []).push(i); });
    var orphan = function (i) {
      var k = segKey(i);
      return items[i].group === 'gap' && !!k && !!contribOf[k] && contribOf[k].every(function (c) { return gone['i' + c]; });
    };
    // kept whatever the cap (integration pass, 1 Oct 2026: a shared PDF lost them, so it said nothing of the rows the AI
    // plan set aside or of what set the health score): each step of the plan that set rows aside, whole (the adapter's
    // words, at most 8, as results_for_ai sends them), what set the health score, and the plan's row noun
    var drops = (Array.isArray(R.plan_row_drops) ? R.plan_row_drops : []).filter(function (d) { return d && typeof d === 'object' && typeof d.text === 'string' && d.text; }).slice(0, 8).map(function (d) {
      var o = { rows: isFinite(d.rows) ? Number(d.rows) : 0, of: isFinite(d.of) ? Number(d.of) : 0, pct: isFinite(d.pct) ? Number(d.pct) : 0, text: str(d.text, 1200) };
      if (typeof d.notice === 'string' && d.notice) o.notice = str(d.notice, 800);
      return o;
    });
    var hx = typeof R.health_explain === 'string' ? R.health_explain.trim() : '';
    var rn = typeof R.row_noun === 'string' && /^[a-z]{3,20}$/.test(R.row_noun) ? R.row_noun : '';
    // the estimand (wave 4): what the headline measures, which the shared PDF prints first; the worker rebuilds it strictly
    var est = null;
    if (R.estimand && typeof R.estimand === 'object' && R.estimand.text) {
      var E = R.estimand, fig = function (x) { return x && typeof x === 'object' && x.text ? (isFinite(x.value) && x.value !== null ? { value: x.value, text: str(x.text, 40) } : { text: str(x.text, 40) }) : null; };
      est = { text: str(E.text, 400), slice: (E.slice || []).slice(0, 8).map(function (x) { return { dim: str(x.dim, 120), member: str(x.member, 120), why: str(x.why, 200) }; }),
        measure: E.measure ? { label: str(E.measure.label, 120), uom: str(E.measure.uom, 40), type: str(E.measure.type, 20), aggregation: str(E.measure.aggregation, 60), scale_applied: E.measure.scale_applied } : null,
        comparison: E.comparison || null, figures: {},
        sum_checks: (E.sum_checks || []).slice(0, 4).map(function (c) { return { dim: str(c.dim, 120), total: str(c.total, 120), parts: c.parts, verdict: str(c.verdict, 20), complete_cells: c.complete_cells,
          max_rel_residual: c.max_rel_residual, unallocated_latest: fig(c.unallocated_latest) || undefined }; }),
        excluded: (E.excluded || []).slice(0, 6).map(function (x) { return { what: str(x.what, 120), why: str(x.why, 200) }; }), plan_source: E.plan_source };
      ['prior', 'latest', 'change', 'change_pct'].forEach(function (k) { var f = fig(E.figures && E.figures[k]); if (f) est.figures[k] = f; });
      var I = E.inference;
      if (I && typeof I === 'object' && I.mode) est.inference = { mode: I.mode, publisher: I.publisher, how_known: (I.how_known || []).slice(0, 4).map(function (x) { return str(x, 200); }), describe: I.describe, revisions: str(I.revisions, 200), grade_label: str(I.grade_label, 200) };
    }
    var render = function () {
      var out = { partial: true };
      if (R.input && typeof R.input === 'object') out.input = { rows: R.input.rows, columns: R.input.columns };
      if (isFinite(R.health_score) && R.health_score !== null) out.health_score = R.health_score;
      if (hx) out.health_explain = str(hx, 400);
      if (drops.length) out.plan_row_drops = drops;
      if (rn) out.row_noun = rn;
      if (R.cleaning && typeof R.cleaning === 'object') out.cleaning = { rows_in: R.cleaning.rows_in, rows_clean: R.cleaning.rows_clean, rows_quarantined: R.cleaning.rows_quarantined };
      if (R.primary && !gone.primary) out.primary = R.primary;
      if (est && !gone.est) out.estimand = est;
      out.findings = findings.filter(function (_, i) { return !gone['f' + i]; });
      if (story && !gone.story) out.story = story;
      if (sc && !gone.scen) out.scenarios = { basis: sc.basis && typeof sc.basis === 'object' ? sc.basis : null, refused: (Array.isArray(sc.refused) ? sc.refused : []).slice(0, 6).map(function (x) { return str(x, 300); }),
        note: str(sc.note, 600), items: items.filter(function (_, i) { return !gone['i' + i] && !orphan(i); }) };
      return out;
    };
    // what the cap takes, in order (see above)
    var order = dropOrder(items, SHARE_TOP_SEGMENTS), pre = function (p) { return function (i) { return p + i; }; };
    var fBack = findings.map(function (_, i) { return i; }).reverse();
    var units = [].concat(order.detail.map(pre('i')), fBack.filter(function (i) { return findings[i].kind !== 'business'; }).map(pre('f')), ['story'],
      order.core.map(pre('i')), fBack.filter(function (i) { return findings[i].kind === 'business'; }).map(pre('f')), ['scen', 'est', 'primary']);
    var out = render(), size = function () { return utf8Len(JSON.stringify(out)); }, cut = [];
    for (var k = 0; k < units.length && size() > cap; k++) { gone[units[k]] = true; cut.push(units[k]); out = render(); }
    if (size() > cap) return null;
    // put back what still fits, the last cut first; a segment's cut items as one batch, its contribution first, and
    // failing the whole batch the longest start of it that fits (so never a gap without its contribution)
    var done = {}, segOfUnit = function (u) { return u.charAt(0) === 'i' && /^i\d+$/.test(u) ? segKey(+u.slice(1)) : null; };
    var rank = function (u) { return SCEN_GROUPS.indexOf(items[+u.slice(1)].group); };
    for (var j = cut.length - 1; j >= 0; j--) {
      if (done[cut[j]]) continue;
      var sk = segOfUnit(cut[j]), batch = [cut[j]];
      if (sk) batch = cut.slice(0, j + 1).filter(function (u) { return !done[u] && segOfUnit(u) === sk; }).sort(function (a, b) { return (rank(a) - rank(b)) || (+a.slice(1) - +b.slice(1)); });
      batch.forEach(function (u) { done[u] = true; });
      for (var n = batch.length; n > 0; n--) {
        batch.slice(0, n).forEach(function (u) { delete gone[u]; });
        out = render();
        if (size() <= cap) break;
        batch.slice(0, n).forEach(function (u) { gone[u] = true; });
        out = render();
      }
    }
    return out;
  }

  // the paper for a browser's languages: Letter where the region is the US, Canada or Mexico (or none is named), else A4
  function paperFor(langs) {
    var list = [].concat(langs || []).filter(Boolean);
    for (var i = 0; i < list.length; i++) {
      var parts = String(list[i]).split(/[-_]/);
      for (var j = 1; j < parts.length; j++) {
        if (/^[A-Za-z]{2}$/.test(parts[j]) || /^\d{3}$/.test(parts[j])) return LETTER_REGIONS.indexOf(parts[j].toUpperCase()) >= 0 ? 'letter' : 'a4';
      }
    }
    return 'letter';
  }
  // "NorthLedger report - YYYY-MM-DD.pdf"; the file's name is added only when the visitor asks for it
  function fileName(date, name) {
    var base = 'NorthLedger report - ' + isoDate(date instanceof Date ? date : new Date());
    var nm = name ? String(name).replace(/\.[A-Za-z0-9]{1,5}$/, '').replace(/[^A-Za-z0-9 ._-]+/g, '-').replace(/^[-. ]+|[-. ]+$/g, '').slice(0, 60) : '';
    return base + (nm ? ' - ' + nm : '') + '.pdf';
  }
  var API = { build: build, model: model, fileName: fileName, paperFor: paperFor, shareResults: shareResults, sectionKind: kindOf, itemGrade: itemGrade,
    facts: { estimandView: estimandView, auditView: auditView, unitOf: unitOf, sumCheckWords: sumCheckWords, planSourceWords: planSourceWords, trendLine: trendLine,
      processOf: processOf, primaryFinding: primaryFinding, stepLabel: stepLabel, compactTicks: compactTicks, UNALLOCATED_WORDS: UNALLOCATED_WORDS },
    FILE_WORD: FILE_WORD, SHARE_MAX_BYTES: SHARE_MAX_BYTES, SHARE_MAX_ITEMS: SHARE_MAX_ITEMS, MAX_SOURCES: MAX_SOURCES, _enc: enc, _tw: tw, _smart: smart };
  if (typeof module === 'object' && module && module.exports) module.exports = API; else root.NLReportPdf = API;
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this);
