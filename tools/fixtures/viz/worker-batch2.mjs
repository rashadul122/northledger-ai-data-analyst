// The worker's checks for the chart grammar's four draw kinds (spec.json batch_2): lines, area, hbar, multiples.
//
// insight-proxy's src/charts.js (sanitizeChart) degrades every kind it does not know to a table at the same index, so
// until it learns these four, a shared link and /report show their tables, not their drawings. This module is the
// port: the same invariants validate_spec.py's check_record applies, in the worker's language, checked here against
// spec.json's examples (each must pass) and the broken copies validate_spec.py makes (each must be caught).
//
// To port: copy BATCH2_KINDS and checkBatch2 into src/charts.js; in sanitizeChart, after the schema caps, call
// checkBatch2(record, spec.caps) for a record whose kind is in BATCH2_KINDS and degrade it to a table when the list
// it returns is not empty, exactly as for the batch 1 kinds. Run `node tools/fixtures/viz/worker-batch2.mjs` here
// after any change to the invariants.

export const BATCH2_KINDS = ['lines', 'area', 'hbar', 'multiples'];

const close = (a, b, tol) => Math.abs(a - b) <= tol;
const increasing = (xs) => xs.every((x, i) => i === 0 || xs[i - 1] < x);
const fsum = (xs) => xs.reduce((a, b) => a + b, 0);

function checkTime(r, d, key) {
  const E = [], n = d.x.length, ss = d[key], one = key.slice(0, -1);
  if (ss.some((s) => s.values.length !== n)) return [r.kind + ': every ' + one + ' has one value per x'];
  if (!increasing(d.x)) E.push(r.kind + ': x is not in order');
  const fc = d.forecast || null;
  const rows = r.table.rows, cols = r.table.cols;
  if (rows.length !== n + (fc ? fc.x.length : 0) || cols.length !== 1 + ss.length + (fc ? 2 : 0)) {
    return E.concat([r.kind + ': the table view is a row per x (and per forecast month), a cell per ' + one]);
  }
  let supp = 0;
  for (let i = 0; i < n; i++) {
    ss.forEach((s, k) => {
      const c = rows[i][1 + k], v = s.values[i];
      if (c === '<5') supp++;
      if ((v === null) !== (c === '' || c === '<5')) {
        E.push(r.kind + ': ' + s.label + ' at ' + d.x[i] + " is null exactly when its cell is '<5' or ''");
      }
    });
  }
  if (supp !== r.suppressed.cells) E.push(r.kind + ': suppressed.cells is ' + r.suppressed.cells + ", the table has " + supp + " '<5' cells");
  if (fc) {
    const m = fc.x.length;
    if (ss.length !== 1 || d.grain !== 'month' || ['values', 'lo', 'hi'].some((k) => fc[k].length !== m) ||
        !increasing([d.x[n - 1]].concat(fc.x)) || fc.lo.some((lo, i) => lo > fc.hi[i])) {
      E.push("lines: a forecast is one series' monthly months after the last x, each with lo <= hi");
    }
  }
  return E;
}

// The invariants of one batch 2 record; [] when it may be drawn. caps is spec.json's caps (RECONCILE_TOL).
export function checkBatch2(r, caps) {
  const d = r.data, tolRel = caps.RECONCILE_TOL;
  if (!d || typeof d !== 'object') return [r.kind + ': no data'];
  try {
    if (r.kind === 'lines' || r.kind === 'multiples') {
      const E = checkTime(r, d, r.kind === 'lines' ? 'series' : 'panels');
      if (r.kind === 'multiples' && d.panels.length < 2) E.push('multiples: two panels or more');
      return E;
    }
    if (r.kind === 'area') {
      const n = d.x.length, parts = d.parts, E = [];
      if (parts.some((p) => p.values.length !== n) || d.total.length !== n) return ['area: every part and the total have one value per x'];
      if (parts.slice(0, -1).some((p) => p.label === 'other')) E.push("area: 'other' is the last part");
      for (let i = 0; i < n; i++) {
        const vals = parts.map((p) => p.values[i]);
        if (Math.min(...vals) < 0 || !close(fsum(vals), d.total[i], tolRel * Math.max(1, Math.abs(d.total[i])) + 1e-6 * vals.length)) {
          E.push('area: the parts are not >= 0 or do not add up to the total at ' + d.x[i]);
        }
      }
      if (!increasing(d.x)) E.push('area: x is not in order');
      if (r.table.rows.length !== n || r.table.cols.length !== parts.length + 2) {
        E.push('area: the table view is a row per x: the period, a cell per part and the total');
      }
      return E;
    }
    if (r.kind === 'hbar') {
      const ns = d.series.length, rows = d.rows, E = [];
      if (rows.some((x) => x.values.length !== ns || x.texts.length !== ns)) return ['hbar: each row has a value and a text per series'];
      if (d.other && rows[rows.length - 1].label !== 'other') E.push("hbar: 'other' is the last row");
      const bars = d.other ? rows.slice(0, -1) : rows;
      if (bars.some((b, i) => i > 0 && bars[i - 1].values[ns - 1] < b.values[ns - 1])) E.push('hbar: the bars are not largest first (by the latest period)');
      if (ns === 2 && rows.some((x) => !x.change_text)) E.push('hbar: two periods, so every row has its change_text');
      if (r.table.rows.length !== rows.length || r.table.cols.length !== 1 + ns + (ns === 2 ? 1 : 0)) {
        E.push('hbar: the table view is a row per bar: the label, a text per series (and the change)');
      }
      return E;
    }
  } catch (e) {
    return [r.kind + ': malformed data (' + e.message + ')'];   // a missing array is malformed, never a crash
  }
  return [r.kind + ' is not a batch 2 kind'];
}

// ---------------------------------------------------------------- self-check against spec.json
async function main() {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const url = await import('node:url');
  const here = path.dirname(url.fileURLToPath(import.meta.url));
  const spec = JSON.parse(fs.readFileSync(path.join(here, 'spec.json'), 'utf8'));
  const ex = Object.fromEntries(Object.entries(spec.examples).map(([k, v]) => [k, v.record]));
  const fails = [];
  let n = 0;
  for (const [name, r] of Object.entries(ex)) {
    if (!BATCH2_KINDS.includes(r.kind)) continue;
    n++;
    const e = checkBatch2(r, spec.caps);
    if (e.length) fails.push('example ' + name + ' refused: ' + e.join('; '));
  }
  const mut = (name, f) => { const r = structuredClone(ex[name]); f(r); return r; };
  // validate_spec.py's batch 2 breaks, less the one its kind list catches ('line' is not a draw kind)
  const breaks = [
    ["a line's series one value short", mut('lines', (r) => r.data.series[0].values.pop())],
    ["a line's point printed where it is null", mut('lines', (r) => { r.data.series[0].values[0] = null; })],
    ["a line's x out of order", mut('lines', (r) => r.data.x.reverse())],
    ['a forecast with lo over hi', mut('edge_lines_forecast', (r) => { r.data.forecast.lo[0] = 1e12; })],
    ['an area part below zero', mut('area', (r) => { r.data.parts[0].values[0] = -1; })],
    ["an area total that is not the parts' sum", mut('area', (r) => { r.data.total[0] += 1; })],
    ['bars out of order', mut('hbar', (r) => r.data.rows.reverse())],
    ['a grouped bar without its change', mut('edge_hbar_grouped', (r) => { delete r.data.rows[0].change_text; })],
    ['multiples with one panel', mut('multiples', (r) => { r.data.panels = r.data.panels.slice(0, 1); })],
    ['a line with no series array', mut('lines', (r) => { delete r.data.series; })],
  ];
  for (const [what, r] of breaks) if (!checkBatch2(r, spec.caps).length) fails.push('missed: ' + what);
  console.log('batch 2 examples kept: ' + (n - fails.filter((f) => f.startsWith('example')).length) + ' of ' + n +
    '; breaks caught: ' + (breaks.length - fails.filter((f) => f.startsWith('missed')).length) + ' of ' + breaks.length);
  fails.forEach((f) => console.log('FAIL ' + f));
  console.log(fails.length ? fails.length + ' FAILED' : 'ALL PASS');
  process.exitCode = fails.length ? 1 : 0;
}

if (import.meta.url === (await import('node:url')).pathToFileURL(process.argv[1] || '').href) await main();
