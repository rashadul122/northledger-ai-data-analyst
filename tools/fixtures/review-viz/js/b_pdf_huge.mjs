// the ±1e308 survivors of a_sanitize.mjs (slope, dot_range, heatmap) through the PDF writer and the repo's checks
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { SITE } from './lib.mjs';
const W = createRequire(import.meta.url)(SITE + '/src/js/45-report-pdf.js');
const { checkPdf, vizCheck } = await import(SITE + '/tools/check_report_pdf.mjs');
const HERE = new URL('.', import.meta.url).pathname;
const OUT = (await import('node:fs')).mkdtempSync((await import('node:os')).tmpdir() + '/review-viz-');
const VR = JSON.parse(readFileSync(SITE + '/tools/fixtures/report-pdf/viz-results.json', 'utf8'));
const recs = JSON.parse(readFileSync(HERE + 'survivors.json', 'utf8')).filter((r) => /^num:(slope|dot_range|heatmap)/.test(r.case));
for (const { case: name, rec } of recs) {
  const sec = { headline: '## The headline', drove: '## What drove it', other: '## Other findings', scenarios: '## Scenarios' };
  const report = 'T\n## Executive summary\n- One [S1].\n' + Object.entries(sec).map(([k, h]) => h + '\nA sentence.\n' + (rec.section === k ? '[CHART:1]\n' : '')).join('') + '## What to do\n1. W.';
  const input = { report, sources: [{ title: 's', link: 'https://example.org/a' }], model: 'm', results: Object.assign({}, VR.results, { charts: [rec] }), kept: [], name: 'zqxwvfile.csv', showName: false, date: new Date(Date.UTC(2026, 8, 30)), goal: 'g' };
  const trace = [];
  try {
    const u8 = W.build(W.model(input), { paper: 'letter', trace }); const f = OUT + '/huge-' + name.replace(/\W+/g, '-') + '.pdf'; writeFileSync(f, u8);
    const r = checkPdf(u8, { file: f, forbid: [], name: 'zqxwvfile', source: report + JSON.stringify(rec) }); const v = vizCheck(r, trace);
    const nan = /NaN|Infinity/.test(Buffer.from(u8).toString('latin1'));
    console.log(`${name}: drawn as ${trace[0] && (trace[0].as || trace[0].kind + ' ' + trace[0].layout)}; checkPdf ${r.ok ? 'ok' : 'FAIL ' + r.fails.slice(0, 2).join(' | ')}; vizCheck ${v.ok ? 'ok' : 'FAIL ' + v.fails.slice(0, 2).join(' | ')}; NaN/Infinity in the bytes ${nan}`);
  } catch (e) { console.log(`${name}: BUILD THREW ${e.message}`); }
}
