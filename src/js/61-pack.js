/* The monthly reporting pack (Track E of plan/DASHBOARD-DESIGN.md; engine/nl_pack.py, CONTRACT §5.18): one option of
   step 6's report types (window.NLDeliver, src/js/60-deliver.js). The engine assembles the pack from every file of the
   run (nl_browser.deliver_json('monthly-pack', settings)); this file only lays the engine's record out:

     NLPack.workbook(pack, made) -> the NLX.workbook spec (src/js/56-xlsx.js): Metrics (the inputs as values, every change
                         and variance a visible formula with the engine's figure as its cached value), Watch list (formula
                         flags against ONE threshold cell), Notes, Inputs checklist
     NLPack.cover(pack, made) -> the NLDocx.document spec (src/js/46-report-docx.js): a one-page cover note, the headline
                         movements and the watch list, "driver to be confirmed" wherever the cause is not in the data
     NLPack.recipe(pack, made) -> the recipe (JSON text) that makes the same pack from next month's files
     NLPack.checklist(pack, made) -> the inputs checklist for next month (Markdown text)
     NLPack.files(pack, made) -> [{name, mime, bytes}] the four downloads (the xlsx and docx need NLX and NLDocx)
   Every figure is the engine's; this file formats them and writes the formulas that recompute them. It also runs under
   Node (module.exports) for tools/test_nl_pack.py. */
(function (root) {
  'use strict';
  var P = {};
  root.NLPack = P;
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  function arr(x) { return Array.isArray(x) ? x : []; }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function mlabel(ym) { return /^\d{4}-\d{2}$/.test(String(ym || '')) ? MONTHS[+ym.slice(5, 7) - 1] + ' ' + ym.slice(0, 4) : String(ym || ''); }
  function dp(L) {                                        // decimals a line's figures are shown with
    if (L.kind === 'rate') return 1;
    var vs = [L.cur, L.prev, L.plan].filter(isNum);
    return vs.every(function (v) { return Math.abs(v - Math.round(v)) < 1e-9; }) && vs.some(function (v) { return Math.abs(v) >= 10; }) ? 0 : (vs.every(function (v) { return Math.abs(v) >= 100; }) ? 0 : 1);
  }
  function fmt(v, d) { return isNum(v) ? v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/^-/, '−') : ''; }
  function signed(v, d) { return isNum(v) ? (v > 0 ? '+' : '') + fmt(v, d) : ''; }
  function pct(v) { return isNum(v) ? signed(v * 100, 1) + '%' : ''; }
  // a line's value as words: a fraction rate as a percent, a percent-number rate with %, else the number
  function val(L, v) {
    if (!isNum(v)) return 'no value';
    if (L.kind === 'rate') return L.scale === 100 ? fmt(v * 100, 1) + '%' : fmt(v, 1) + (/%|pct|percent/i.test(L.label + L.key) ? '%' : '');
    return fmt(v, dp(L));
  }
  function pts(v) { return isNum(v) ? signed(v, 1) + (Math.abs(v) === 1 ? ' percentage point' : ' percentage points') : ''; }
  P.val = val;
  P.monthLabel = mlabel;
  P.lineByKey = function (pack, k) { return arr(pack.lines).filter(function (l) { return l.key === k; })[0] || null; };

  // one sentence per headline line, every figure the engine's
  P.moveText = function (pack, L) {
    var cmp = pack.compare === 'forecast' ? 'the forecast' : 'plan', t = L.label + ': ' + val(L, L.cur) + ' in ' + mlabel(pack.month);
    if (isNum(L.mom)) {
      if (L.kind === 'rate') t += ', ' + (L.mom === 0 ? 'unchanged' : (L.mom > 0 ? 'up ' : 'down ') + pts(Math.abs(L.mom)).replace(/^\+/, '')) + ' on ' + mlabel(pack.prev_month) + ' (' + val(L, L.prev) + ')';
      else if (L.points) t += ', ' + (L.mom === 0 ? 'unchanged' : (L.mom > 0 ? 'up ' : 'down ') + fmt(Math.abs(L.mom), dp(L)) + ' points') + ' on ' + mlabel(pack.prev_month) + ' (' + val(L, L.prev) + ')';
      else t += ', ' + (L.mom === 0 ? 'unchanged' : (L.mom > 0 ? 'up ' : 'down ') + (isNum(L.mom_pct) ? fmt(Math.abs(L.mom_pct) * 100, 1) + '% (' + signed(L.mom, dp(L)) + ')' : signed(L.mom, dp(L)))) + ' on ' + mlabel(pack.prev_month);
    } else t += ' (no ' + mlabel(pack.prev_month) + ' figure to compare)';
    if (isNum(L.var)) {
      if (L.kind === 'rate') t += '; ' + pts(Math.abs(L.var)).replace(/^\+/, '') + (L.var >= 0 ? ' above ' : ' below ') + cmp + ' (' + val(L, L.plan) + ')';
      else if (L.points) t += '; ' + fmt(Math.abs(L.var), dp(L)) + ' points' + (L.var >= 0 ? ' above ' : ' below ') + cmp + ' (' + val(L, L.plan) + ')';
      else t += '; ' + (isNum(L.var_pct) ? fmt(Math.abs(L.var_pct) * 100, 1) + '%' : fmt(Math.abs(L.var), dp(L))) + (L.var >= 0 ? ' above ' : ' below ') + cmp + ' (' + signed(L.var, dp(L)) + ')';
    } else if (!L.planned) t += '; no ' + cmp;
    return t + '.';
  };
  P.whereText = function (pack, L) {
    var d = L.driver;
    if (!d) return '';
    return d.by.replace(/_/g, ' ') + ' ' + d.member + ': ' + signed(d.amount, dp(L)) + ' of the ' + signed(d.total, dp(L)) + (d.of === 'variance' ? ' variance' : ' change');
  };
  P.movedText = function (pack, L) {
    var out = [];
    arr(L.watch_why).forEach(function (w) {
      if (/variance/.test(w)) out.push(L.points ? fmt(Math.abs(L.var), L.kind === 'rate' ? 1 : dp(L)) + (L.kind === 'rate' ? ' pp ' : ' points ') + (L.var >= 0 ? 'above' : 'below') + ' plan' : (pack.basis === 'amount' || !isNum(L.var_pct) ? signed(L.var, dp(L)) : pct(L.var_pct)) + ' vs plan');
      else out.push(L.points ? (L.mom >= 0 ? '+' : '−') + fmt(Math.abs(L.mom), L.kind === 'rate' ? 1 : dp(L)) + (L.kind === 'rate' ? ' pp' : ' points') + ' on the month' : (pack.basis === 'amount' || !isNum(L.mom_pct) ? signed(L.mom, dp(L)) : pct(L.mom_pct)) + ' on the month');
    });
    return out.join('; ');
  };
  function thresholdText(pack) { return pack.basis === 'amount' ? fmt(pack.threshold, 0) + ' (in each line\'s own unit)' : fmt(pack.threshold * 100, 1).replace(/\.0$/, '') + '%'; }
  P.thresholdText = thresholdText;

  /* ---- the Excel workbook ---- */
  P.workbook = function (pack, made) {
    var B = function (v) { return { v: v, b: true }; }, L = arr(pack.lines), H = 6, row = {};   // H: the first line's row (row 5 is the header)
    L.forEach(function (l, i) { row[l.key] = H + i; });
    var cmp = pack.compare === 'forecast' ? 'Forecast' : 'Plan';
    var head = [B('Line'), B('Kind'), B('Last month (' + mlabel(pack.prev_month) + ')'), B('This month (' + mlabel(pack.month) + ')'), B('Change'), B('Change %'),
      B(cmp + ' (' + mlabel(pack.month) + ')'), B('Variance'), B('Variance %'), B('YTD actual'), B('YTD ' + cmp.toLowerCase()), B('YTD variance'), B('YTD variance %'), B('Source')];
    var KW = { flow: 'flow (summed)', stock: 'count at a date', level: 'level (averaged)', rate: 'rate (points)' };
    var rows = [[B('Monthly reporting pack: ' + mlabel(pack.month))],
      ['Figures computed in the browser by the NorthLedger engine from ' + arr(pack.inputs_used).map(function (x) { return x.name; }).join(', ') + '. Changes and variances are formulas; change an input value and they recompute.'],
      ['Year to date: ' + (pack.ytd_label || '') + '. Percent changes divide by |the base|. A rate\'s change is in percentage points; its % column is the relative change.'],
      null, head];
    L.forEach(function (l, i) {
      var r = H + i, d = dp(l), pt = l.kind === 'rate', sc = l.scale === 100 ? '*100' : '';
      var vf = pt ? (l.scale === 100 ? '0.0%' : '0.0') : (d ? '#,##0.0' : '#,##0'), cf = pt || l.points ? '+0.0" pt";-0.0" pt";0.0" pt"' : (d ? '+#,##0.0;-#,##0.0;0.0' : '+#,##0;-#,##0;0'), pf = '+0.0%;-0.0%;0.0%';
      var cell = function (v) { return isNum(v) ? { v: v, fmt: vf } : ''; };
      var C = 'C' + r, D = 'D' + r, G = 'G' + r, J = 'J' + r, K = 'K' + r;
      var cells = [l.label, KW[l.kind] || l.kind, cell(l.prev), cell(l.cur)];
      if (l.derived) {                                    // gross profit and margin: formulas of the revenue and cost rows
        var a = row[l.derived.of[0]], b = row[l.derived.of[1]];
        var der = function (col, v) {
          var x = col + a, y = col + b;
          return { f: l.derived.op === 'minus' ? 'IF(OR(' + x + '="",' + y + '=""),"",' + x + '-' + y + ')' : 'IF(OR(' + x + '="",' + y + '="",' + y + '=0),"",' + x + '/' + y + ')', v: isNum(v) ? v : '', fmt: vf };
        };
        cells = [l.label, KW[l.kind], der('C', l.prev), der('D', l.cur)];
      }
      cells.push({ f: 'IF(OR(' + C + '="",' + D + '=""),"",(' + D + '-' + C + ')' + sc + ')', v: isNum(l.mom) ? l.mom : '', fmt: cf });
      cells.push({ f: 'IF(OR(' + C + '="",' + D + '="",' + C + '=0),"",(' + D + '-' + C + ')/ABS(' + C + '))', v: isNum(l.mom_pct) ? l.mom_pct : '', fmt: pf });
      if (l.derived) { var a2 = row[l.derived.of[0]], b2 = row[l.derived.of[1]]; cells.push(l.planned ? { f: l.derived.op === 'minus' ? 'IF(OR(G' + a2 + '="",G' + b2 + '=""),"",G' + a2 + '-G' + b2 + ')' : 'IF(OR(G' + a2 + '="",G' + b2 + '="",G' + b2 + '=0),"",G' + a2 + '/G' + b2 + ')', v: isNum(l.plan) ? l.plan : '', fmt: vf } : ''); }
      else cells.push(cell(l.plan));
      cells.push({ f: 'IF(OR(' + D + '="",' + G + '=""),"",(' + D + '-' + G + ')' + sc + ')', v: isNum(l['var']) ? l['var'] : '', fmt: cf });
      cells.push({ f: 'IF(OR(' + D + '="",' + G + '="",' + G + '=0),"",(' + D + '-' + G + ')/ABS(' + G + '))', v: isNum(l.var_pct) ? l.var_pct : '', fmt: pf });
      if (l.derived) {
        var a3 = row[l.derived.of[0]], b3 = row[l.derived.of[1]], dv = function (col, v) { return isNum(v) ? { f: l.derived.op === 'minus' ? col + a3 + '-' + col + b3 : 'IF(' + col + b3 + '=0,"",' + col + a3 + '/' + col + b3 + ')', v: v, fmt: vf } : ''; };
        cells.push(dv('J', l.ytd_act), dv('K', l.ytd_plan));
      } else cells.push(cell(l.ytd_act), cell(l.ytd_plan));
      cells.push({ f: 'IF(OR(' + J + '="",' + K + '=""),"",(' + J + '-' + K + ')' + sc + ')', v: isNum(l.ytd_var) ? l.ytd_var : '', fmt: cf });
      cells.push({ f: 'IF(OR(' + J + '="",' + K + '="",' + K + '=0),"",(' + J + '-' + K + ')/ABS(' + K + '))', v: isNum(l.ytd_var_pct) ? l.ytd_var_pct : '', fmt: pf });
      cells.push((l.actual ? l.actual.input + ': ' + l.actual.column : '') + (l.ytd_why ? ' (YTD: ' + l.ytd_why + ')' : ''));
      rows.push(cells);
    });
    var M = 'Metrics', amount = pack.basis === 'amount';
    var wrows = [[B('Watch list: ' + mlabel(pack.month))], [B('Threshold'), amount ? { v: pack.threshold, fmt: '#,##0.##' } : { v: pack.threshold, fmt: '0.0%' },
      amount ? 'An amount, compared with each line\'s change and variance in its own unit.' : 'A percent, compared with each line\'s Change % and Variance %.'],
      ['A line is flagged WATCH when ' + (amount ? 'its Change or its Variance' : 'its Change % or its Variance %') + ' on the Metrics sheet is larger than the threshold in B2, either way. Change B2 and the flags recompute. Where a move sits is said only when the engine\'s own decomposition finds it in the data; the cause is "driver to be confirmed".'],
      null, [B('Line'), B(amount ? 'Change' : 'Change %'), B(amount ? 'Variance' : 'Variance %'), B('Flag'), B('Flagged on'), B('Where it sits (from the data)'), B('Cause')]];
    L.forEach(function (l, i) {
      var r = H + i, w = 6 + i, src1 = amount ? 'E' : 'F', src2 = amount ? 'H' : 'I', f = amount ? (l.points ? '+0.0;-0.0;0.0' : '#,##0') : '+0.0%;-0.0%;0.0%';
      var a = amount ? l.mom : l.mom_pct, b = amount ? l['var'] : l.var_pct;
      wrows.push([l.label, { f: NLXref(M, src1 + r), v: isNum(a) ? a : '', fmt: f }, { f: NLXref(M, src2 + r), v: isNum(b) ? b : '', fmt: f },
        { f: 'IF(OR(IF(ISNUMBER(B' + w + '),ABS(B' + w + ')>$B$2,FALSE),IF(ISNUMBER(C' + w + '),ABS(C' + w + ')>$B$2,FALSE)),"WATCH","")', v: l.watch ? 'WATCH' : '' },
        l.watch ? P.movedText(pack, l) : '', P.whereText(pack, l), l.watch ? l.cause : '']);
    });
    var N = pack.notes || {}, nrows = [[B('Notes')], [B('Reporting month'), mlabel(pack.month), pack.month_why || ''], [B('For'), pack.readers || '(not given)'],
      [B('Compared with'), pack.compare === 'forecast' ? 'the forecast' : 'the plan (budget)'], [B('Threshold'), thresholdText(pack)],
      [B('Year to date'), pack.ytd_label || ''], [B('Made'), made || ''], null, [B('What each line is')]];
    arr(N.definitions).forEach(function (d) { nrows.push([d.line, d.text, d.kind + (d.kind_by === 'you' ? ' (you set it)' : '')]); });
    nrows.push(null, [B('Sources')], [B('File'), B('Read as'), B('Rows kept'), B('Rows set aside'), B('Months')]);
    arr(N.sources).forEach(function (s) { nrows.push([s.input, s.role, s.rows_kept, s.rows_set_aside, s.months ? s.months.first + ' to ' + s.months.last : '']); });
    nrows.push(null, [B('Assumptions')]);
    arr(N.assumptions).forEach(function (a) { nrows.push([a]); });
    nrows.push(null, [B('Looked wrong, please check')]);
    (arr(N.wrong).length ? arr(N.wrong) : ['Nothing the engine flagged.']).forEach(function (a) { nrows.push([a]); });
    var crow = [[B('Inputs checklist for next month')], ['Drop these files into the next run (step 1, several files at once), with "Load a recipe" and this pack\'s recipe file, to make the same pack.'], null,
      [B('Order'), B('File (any digits may change)'), B('Read as'), B('Date column'), B('Scenario column'), B('Line column'), B('Measures read'), B('Grouping columns'), B('Next month')]];
    arr(pack.checklist).forEach(function (c, i) {
      crow.push([i + 1, c.pattern, c.role, c.date, c.scenario ? c.scenario + ' (' + arr(c.scenario_members).join(', ') + ')' : '', c.line_column || '', arr(c.measures).join(', '), arr(c.groups).join(', '), c.next]);
    });
    return { title: 'Monthly reporting pack ' + (pack.month || ''), sheets: [
      { name: 'Metrics', freeze: H - 1, widths: [24, 17, 25, 28, 12, 11, 25, 12, 12, 14, 14, 14, 16, 60], rows: rows,
        tables: L.length ? [{ name: 'Metrics', ref: 'A' + (H - 1) + ':N' + (H - 1 + L.length) }] : [] },
      { name: 'Watch list', freeze: 5, widths: [26, 13, 13, 9, 30, 40, 24], rows: wrows },
      { name: 'Notes', widths: [26, 90, 40, 14, 20], rows: nrows },
      { name: 'Inputs checklist', freeze: 4, widths: [7, 34, 16, 14, 24, 14, 40, 24, 40], rows: crow }] };
  };
  function NLXref(sheet, cell) { return "'" + String(sheet).replace(/'/g, "''") + "'!" + cell; }

  /* ---- the Word cover note (one page) ---- */
  P.cover = function (pack, made) {
    var L = arr(pack.lines), W = L.filter(function (l) { return l.watch; }), head = arr(pack.headline).map(function (k) { return P.lineByKey(pack, k); }).filter(Boolean);
    var synthetic = arr(pack.inputs_used).some(function (x) { return /synthetic/i.test(x.name); });
    var blocks = [{ type: 'title', text: 'Monthly pack: ' + mlabel(pack.month) },
      { type: 'subtitle', text: 'Cover note' + (pack.readers ? ' for ' + pack.readers : '') + (made ? ', ' + made : '') }];
    if (synthetic) blocks.push({ type: 'note', text: 'Made from SYNTHETIC demo files: every figure is invented by a seeded script.' });
    blocks.push({ type: 'h2', text: 'Headline movements' }, { type: 'bullets', items: head.map(function (l) { return P.moveText(pack, l); }) });
    blocks.push({ type: 'h2', text: 'Watch list (moves over ' + thresholdText(pack) + ')' });
    if (W.length) {
      var shown = W.slice(0, 8);
      blocks.push({ type: 'table', cols: ['Line', 'Moved', 'Where it sits (from the data)', 'Cause'],
        rows: shown.map(function (l) { return [l.label, P.movedText(pack, l), P.whereText(pack, l) || 'not in the data', l.cause]; }),
        widths: [3, 4, 5, 3], align: ['left', 'left', 'left', 'left'],
        note: W.length > shown.length ? 'And ' + (W.length - shown.length) + ' more in the workbook\'s Watch list sheet.' : '' });
    } else blocks.push({ type: 'p', text: 'No line moved more than the threshold this month.' });
    blocks.push({ type: 'note', text: '"Driver to be confirmed" marks every cause the data does not show; where a move sits is given only when the engine\'s own decomposition finds it in the files. The detailed variance commentary follows separately. Figures: the workbook (Metrics, with its formulas, and Notes for definitions, sources and assumptions).' });
    blocks.push({ type: 'note', text: 'Files read: ' + arr(pack.inputs_used).map(function (x) { return x.name; }).join(', ') + '. Compared with ' + (pack.compare === 'forecast' ? 'the forecast' : 'the plan') + '; year to date ' + (pack.ytd_label || '') + '.' });
    return { title: 'Monthly pack ' + mlabel(pack.month), subject: 'Monthly reporting pack cover note', style: 'business', header: 'Monthly pack: ' + mlabel(pack.month), blocks: blocks };
  };

  P.recipe = function (pack, made) {
    var r = JSON.parse(JSON.stringify(pack.recipe || {}));
    r.made = made || '';
    r.month_made_for = pack.month;
    r.note = 'Load this in step 1 ("Load a recipe") with next month\'s files: the same report type, readers, metrics, threshold and plan, and the files it read (their names with any digits allowed to change).';
    return JSON.stringify(r, null, 2) + '\n';
  };
  P.checklist = function (pack, made) {
    var t = '# Inputs checklist for next month\n\nPack: ' + mlabel(pack.month) + (made ? ' (made ' + made + ')' : '') + '. Drop these files into step 1 together, with this pack\'s recipe ("Load a recipe"), to make the same pack.\n\n';
    arr(pack.checklist).forEach(function (c, i) {
      t += (i + 1) + '. **' + c.pattern + '** (read as ' + String(c.role).replace(/_/g, ' ') + '): ' + c.next + '.\n' +
        '   - date column: `' + c.date + '`' + (c.scenario ? '; scenario column: `' + c.scenario + '` (' + arr(c.scenario_members).join(', ') + ')' : '') + (c.line_column ? '; line column: `' + c.line_column + '`' : '') + '\n' +
        '   - measures: ' + arr(c.measures).map(function (m) { return '`' + m + '`'; }).join(', ') + '\n' +
        (arr(c.groups).length ? '   - grouping columns (where a move sits): ' + arr(c.groups).map(function (m) { return '`' + m + '`'; }).join(', ') + '\n' : '');
    });
    if (arr((pack.notes || {}).wrong).length) t += '\n## Looked wrong this month (check before next month)\n\n' + pack.notes.wrong.map(function (w) { return '- ' + w; }).join('\n') + '\n';
    return t;
  };
  P.stem = function (pack) { return 'monthly-pack-' + String(pack.month || 'month'); };
  P.files = function (pack, made) {
    var out = [], X = root.NLX, Wd = root.NLDocx, enc = new TextEncoder();
    if (X) out.push({ name: P.stem(pack) + '.xlsx', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', bytes: X.workbook(P.workbook(pack, made)), label: 'Excel workbook' });
    if (Wd) { var c = P.cover(pack, made); c.created = made ? new Date(made + 'T12:00:00Z') : undefined; out.push({ name: P.stem(pack) + '-cover-note.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes: Wd.document(c), label: 'cover note' }); }
    out.push({ name: P.stem(pack) + '-recipe.json', mime: 'application/json', bytes: enc.encode(P.recipe(pack, made)), label: 'pack recipe' });
    out.push({ name: P.stem(pack) + '-checklist.md', mime: 'text/markdown', bytes: enc.encode(P.checklist(pack, made)), label: 'checklist for next month' });
    return out;
  };

  /* ---- the option in step 6's menu (browser only) ---- */
  if (typeof document === 'undefined') return;
  var ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return ENT[c]; }); }
  var last = { key: '', pack: null, busy: false };
  function today() { var d = new Date(); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function asOf(rep) { var T = window.NLTry; return (T && T.asOf && T.asOf()) || ((rep && rep.reproducibility && rep.reproducibility.parameters) || {}).as_of || ''; }
  var option = {
    id: 'monthly-pack', order: 10, title: 'Monthly reporting pack',
    blurb: 'An Excel workbook (the metrics table, month over month and against plan, formulas visible; a watch list against one threshold), a one-page Word cover note, and a recipe and checklist to rerun it next month.',
    roles: ['actuals', 'plan', 'actuals_vs_plan', 'kpi', 'trial_balance'],
    needs: [
      { what: 'This month\'s actuals', columns: ['a date or month column', 'one column per measure (revenue, cost, ...)', 'grouping columns such as region (optional)'], optional: false,
        how: 'From your finance or sales system: the transactions, or the month\'s figures, exported as CSV (or Excel).' },
      { what: 'Budget or plan', columns: ['month', 'the same measures', 'a scenario column (Actual / Budget / Forecast) or columns named like revenue_budget'], optional: true,
        how: 'Your budget export by month; or put the plan beside the actuals in one file.' },
      { what: 'KPI extract', columns: ['month', 'one column per KPI, its target beside it (nps, nps_target)'], optional: true, how: 'Your KPI report saved as CSV.' },
      { what: 'Trial balance', columns: ['month', 'account', 'debit', 'credit'], optional: true, how: 'Read as debit minus credit for each account and month.' }],
    settings: [
      { key: 'readers', label: 'Who reads this pack', kind: 'text', 'default': '', placeholder: 'e.g. the board, the CFO' },
      { key: 'metrics', label: 'Which metrics matter', kind: 'text', 'default': '', placeholder: 'empty: every line the engine finds; or e.g. revenue, churn', help: 'Names or parts of names, separated by commas.' },
      { key: 'threshold', label: 'Watch threshold', kind: 'number', 'default': 10, min: 0, step: 'any', help: 'In percent on the percent basis (10 = 10%); in each line\'s own unit on the amount basis.' },
      { key: 'basis', label: 'Compare the threshold with', kind: 'choice', 'default': 'percent', options: [{ value: 'percent', label: 'percent changes' }, { value: 'amount', label: 'amounts' }] },
      { key: 'compare', label: 'Plan to compare with', kind: 'choice', 'default': 'plan', options: [{ value: 'plan', label: 'Budget or plan' }, { value: 'forecast', label: 'Forecast' }] },
      { key: 'fiscal_start', label: 'Fiscal year starts in', kind: 'choice', 'default': '1', options: MONTHS.map(function (m, i) { return { value: String(i + 1), label: m }; }) },
      { key: 'month', label: 'Reporting month', kind: 'text', 'default': '', placeholder: 'empty: the latest complete month; or YYYY-MM' }],
    fits: function (rep) {
      var ins = arr(rep && rep.inputs).filter(function (f) { return f.ok; });
      if (!ins.length) return { ok: false, why: 'a file read by the engine' };
      var dated = ins.filter(function (f) { return f.months && ['actuals', 'actuals_vs_plan', 'kpi', 'trial_balance'].indexOf(f.role) >= 0; });
      if (!dated.length) return { ok: false, why: 'actuals: a file with a date column and measures' };
      var plan = ins.some(function (f) { return ['plan', 'actuals_vs_plan'].indexOf(f.role) >= 0; });
      return { ok: true, why: plan ? '' : 'no plan file yet: the plan columns will be empty' };
    },
    loadRecipe: function (obj) {
      if (obj.kind !== 'monthly-pack') return { ok: false, why: 'it is not a monthly pack recipe' };
      var s = obj.settings || {}, v = {};
      ['readers', 'threshold', 'basis', 'compare', 'fiscal_start'].forEach(function (k) { if (s[k] !== undefined && s[k] !== null) v[k] = k === 'fiscal_start' ? String(s[k]) : s[k]; });
      v.metrics = arr(s.metrics).join(', ');
      v.kinds = s.kinds || {};
      v.recipe = obj;
      window.NLDeliver.setSettings('monthly-pack', v);
      return { ok: true, why: '' };
    },
    render: function (slot, rep, settings) {
      var D = window.NLDeliver, fit = option.fits(rep), T = window.NLTry, wait = T && T.pendingFiles ? T.pendingFiles().length : 0;
      if (!fit.ok) { slot.innerHTML = ''; return; }
      // the run's other files are still being read: the pack waits for them (it is made again when each arrives)
      if (wait) { slot.innerHTML = '<p class="note" role="status" id="pk-status">Reading ' + wait + ' more file' + (wait === 1 ? '' : 's') + ' of this run first\u2026</p>'; return; }
      var roles = {};
      arr(rep.inputs).forEach(function (f) { if (f.role_by === 'visitor') roles[f.id] = f.role; });
      var s = {}; Object.keys(settings).forEach(function (k) { s[k] = settings[k]; });
      s.as_of = asOf(rep); s.roles = roles;
      var key = JSON.stringify(s) + '|' + arr(rep.inputs).map(function (f) { return f.id + f.sha256 + f.role; }).join(',') + '|' + ((rep.input || {}).sha256 || '');
      slot.innerHTML = '<p class="note" role="status" id="pk-status">' + (fit.why ? esc(fit.why.charAt(0).toUpperCase() + fit.why.slice(1)) + '. ' : '') + 'Making the pack from ' + arr(rep.inputs).filter(function (f) { return f.ok; }).length + ' file(s)…</p><div id="pk-out"></div>';
      var draw = function (pack) {
        var st = document.getElementById('pk-status'), out = document.getElementById('pk-out');
        if (!st || !out) return;
        if (!pack || !pack.ok) { st.textContent = 'The pack could not be made: ' + ((pack && pack.error) || 'the engine did not answer (run the file again)') + '.'; return; }
        st.textContent = (fit.why ? fit.why.charAt(0).toUpperCase() + fit.why.slice(1) + '. ' : '') + 'Reporting month: ' + mlabel(pack.month) + ' (' + pack.month_why + '). ' + pack.lines.length + ' lines, ' + pack.watch.length + ' on the watch list.';
        var W = pack.lines.filter(function (l) { return l.watch; });
        out.innerHTML = '<div class="pk-dl ai-rep-actions"><button type="button" class="btn btn-primary btn-sm" data-pk="xlsx">Excel workbook (.xlsx)</button><button type="button" class="btn btn-ghost btn-sm" data-pk="docx">Cover note (.docx)</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-pk="json">Download pack recipe (.json)</button><button type="button" class="btn btn-ghost btn-sm" data-pk="md">Checklist for next month (.md)</button>' +
          '<span class="try-share-out" id="pk-dl-out" hidden></span></div>' +
          '<h5>Watch list: moves over ' + esc(thresholdText(pack)) + '</h5>' + (W.length ? '<div class="tbl-wrap"><table class="tbl pk-watch"><thead><tr><th scope="col">Line</th><th scope="col">Moved</th><th scope="col">Where it sits (from the data)</th><th scope="col">Cause</th></tr></thead><tbody>' +
          W.map(function (l) { return '<tr><th scope="row">' + esc(l.label) + '</th><td>' + esc(P.movedText(pack, l)) + '</td><td>' + esc(P.whereText(pack, l) || 'not in the data') + '</td><td>' + esc(l.cause) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '<p class="note">No line moved more than the threshold.</p>') +
          '<details class="pk-lines"><summary>Every line, what it is and whether it goes in the pack</summary><div class="tbl-wrap"><table class="tbl"><thead><tr><th scope="col">In the pack</th><th scope="col">Line</th><th scope="col">' + esc(mlabel(pack.month)) + '</th><th scope="col">Read as</th></tr></thead><tbody>' +
          pack.lines.map(function (l) {
            return '<tr><td><input type="checkbox" checked data-pk-line="' + esc(l.key) + '" aria-label="Keep ' + esc(l.label) + ' in the pack"' + (l.derived ? ' disabled' : '') + '></td><th scope="row">' + esc(l.label) + '</th><td>' + esc(val(l, l.cur)) + '</td><td>' +
              (l.derived ? 'rate from revenue and cost' : '<select data-pk-kind="' + esc(l.key) + '" aria-label="How to read ' + esc(l.label) + '">' + [['flow', 'flow (summed over months)'], ['stock', 'count at a date'], ['level', 'level (averaged)'], ['rate', 'rate (points)']].map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === l.kind ? ' selected' : '') + '>' + o[1] + '</option>'; }).join('') + '</select>') + '</td></tr>';
          }).join('') + '</tbody></table></div><p class="note">Untick a line to leave it out, or change how a line is read; the pack is made again here, with no AI call.</p></details>' +
          '<details class="pk-notes"><summary>Looked wrong, please check (' + arr(pack.notes.wrong).length + ')</summary><ul>' + (arr(pack.notes.wrong).length ? pack.notes.wrong.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') : '<li>Nothing the engine flagged.</li>') + '</ul></details>';
        Array.prototype.forEach.call(out.querySelectorAll('button[data-pk]'), function (b) {
          b.addEventListener('click', function () {
            // the reader is the visitor's setting as it stands (the engine echoes it; a card drawn earlier may predate an edit)
            var made = today(), files = P.files(Object.assign({}, pack, { readers: String(D.settings('monthly-pack').readers || pack.readers || '') }), made), want = b.getAttribute('data-pk'), f = files.filter(function (x) { return x.name.slice(-want.length - 1) === '.' + want; })[0], o = document.getElementById('pk-dl-out');
            if (!f) { o.hidden = false; o.textContent = 'This file cannot be made in this browser.'; return; }
            D.offer(o, f.bytes, f.name, f.mime, f.label);
          });
        });
        Array.prototype.forEach.call(out.querySelectorAll('input[data-pk-line]'), function (c) {
          c.addEventListener('change', function () {
            var keep = Array.prototype.filter.call(out.querySelectorAll('input[data-pk-line]'), function (x) { return x.checked && !x.disabled; }).map(function (x) { return x.getAttribute('data-pk-line'); });
            D.setSettings('monthly-pack', { metrics: keep.length === pack.lines.filter(function (l) { return !l.derived; }).length ? '' : keep.join(', ') });
            D.open('monthly-pack');
          });
        });
        Array.prototype.forEach.call(out.querySelectorAll('select[data-pk-kind]'), function (sel) {
          sel.addEventListener('change', function () {
            var k = {}, cur = D.settings('monthly-pack').kinds || {};
            Object.keys(cur).forEach(function (x) { k[x] = cur[x]; });
            k[sel.getAttribute('data-pk-kind')] = sel.value;
            D.setSettings('monthly-pack', { kinds: k });
            D.open('monthly-pack');
          });
        });
      };
      if (last.key === key && last.pack) { draw(last.pack); return; }
      D.engine('monthly-pack', s).then(function (pack) { last.key = key; last.pack = pack; P.lastPack = pack; draw(pack); });
    }
  };
  P.option = option;
  if (window.NLDeliver && window.NLDeliver.register) window.NLDeliver.register(option);
  else (window.NLDeliverQueue = window.NLDeliverQueue || []).push(option);
})(typeof window !== 'undefined' ? window : globalThis);
if (typeof module !== 'undefined' && module.exports) module.exports = (typeof window !== 'undefined' ? window : globalThis).NLPack;
