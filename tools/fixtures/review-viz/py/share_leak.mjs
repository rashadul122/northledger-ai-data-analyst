// the worker's own sanitizing (read-only, from ../insight-proxy beside the site); the records from share_leak_data.py
import path from 'node:path';
const SITE = new URL('../../../..', import.meta.url).pathname;
const { validateCharts, fitShareCharts } = await import(path.join(SITE, '..', 'insight-proxy', 'src', 'charts.js'));
const DIR = process.argv[2] || '.';
import fs from 'node:fs';
for (const f of ['emoji_rfa_charts.json', 'tx_rfa_charts.json']) {
  const raw = JSON.parse(fs.readFileSync(path.join(DIR, f), 'utf8'));
  const got = fitShareCharts(validateCharts(raw, 10), 200000);
  for (const c of got) {
    if (!c || !c.kind) continue;
    const d = c.data || {};
    if (c.chart === 'crosstab_heatmap') {
      let shown = 0; d.values.forEach(r => r.forEach(v => { if (v != null) shown += v; }));
      console.log(f, c.chart, 'kind', c.kind, 'inputs.rows', c.inputs && c.inputs.rows, 'sum shown', shown, '=> suppressed cell =', c.inputs.rows === null ? 'unknown (no rows read sent)' : c.inputs.rows - shown);
    }
    if (c.chart === 'theme_rating_heatmap') {
      const j1 = d.cols.indexOf('1');
      for (let i = 0; i < 3; i++) {
        if (d.n[i][d.cols.length - 1] === null || d.values[i][d.cols.length - 1] === null) { console.log(f, c.chart, 'word', d.rows[i], "'all' has no n (or is hidden): nothing to recover"); continue; }
        const all = d.values[i][d.cols.length - 1] * d.n[i][d.cols.length - 1] / 100;
        let s = 0; for (let j = 0; j < d.cols.length - 1; j++) if (d.values[i][j] != null) s += d.values[i][j] * d.n[i][j] / 100;
        console.log(f, c.chart, 'word', d.rows[i], 'cell (rating 1) text', JSON.stringify(d.text[i][j1]), '-> recovered hits', (all - s).toFixed(4));
      }
    }
    if (c.chart === 'pareto') console.log(f, 'pareto other after the worker:', JSON.stringify(d.other));
  }
}
