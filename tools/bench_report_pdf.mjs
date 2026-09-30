// CPU time of the report PDF writer (src/js/45-report-pdf.js, window.NLReportPdf) on the inputs that cost it most.
// The share worker runs a byte copy of this writer on the Cloudflare free plan (10 ms of CPU a request), so the
// measure is main-thread CPU (process.threadCpuUsage), not wall time, for the one call the worker makes:
// build(model(input), {paper}).
//
//   node tools/bench_report_pdf.mjs                         # the canonical writer: warm median and p95, cold first call
//   node tools/bench_report_pdf.mjs --against OLD.js        # OLD.js beside it ("before" and "after"), and the bytes of
//                                                           # every case, Letter and A4, compared (exit 1 on a difference)
//   options: --writer W.js (instead of the canonical), --case a,b (some cases), --iters N (warm calls, at most; 200),
//            --budget MS (warm CPU a case may spend; 6000), --cold K (fresh processes per case; 9), --json FILE (raw
//            results), --no-prewarm (count ICU's first use in the cold call too)
//
// The cases: small (the ship2 report as a share: the live report, its 6 sources, 5 charts and 5 tables); the largest
// fixture (the largest PDF tools/check_report_pdf.mjs makes: review4's "unexpected headings, a long title, 35
// sources", with the engine's full results); the largest share (a 32,000-character report, 6 charts, 8 tables of 12
// rows, 12 sources); every share field at its cap (that share with every string at its cap, and the engine's results
// trimmed by shareResults to their 20 KB); and 4,570 headings (the 32,000-character report "## h / w" 4,570 times:
// an adversarial share, one bookmark each).
// Warm: 20 calls first, then up to --iters calls within --budget (at least 30), each timed alone, in a process of
// its own per writer and case. Cold: the first call in a fresh process (the writer already loaded, its load time reported
// apart), --cold processes a case; ICU's first use (normalize, a collator) is made before it unless --no-prewarm,
// so the cold figure is the writer's own code. The date is fixed and TZ=UTC, so the bytes are reproducible.
// Imported, it runs nothing: CASES (each case's model() input) and call are exported for other measures.
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SELF = fileURLToPath(import.meta.url);
const isMain = process.argv[1] && path.resolve(process.argv[1]) === SELF;
if (isMain) process.env.TZ = 'UTC';
const CANON = path.join(path.dirname(HERE), 'src', 'js', '45-report-pdf.js');
const FX = path.join(HERE, 'fixtures', 'report-pdf');
const J = (f) => JSON.parse(readFileSync(path.join(FX, f), 'utf8'));
const requireCjs = createRequire(import.meta.url);
const DATE = new Date(Date.UTC(2026, 8, 30, 12, 0, 0));
const cpu = () => { const u = process.threadCpuUsage(); return (u.user + u.system) / 1000; };

// ---------------------------------------------------------------- the inputs (the writer's model() input)
// a stored share as the worker hands it to the writer (sharePdfInput): never the file's name, shared: true
const share = (d, extra) => Object.assign({ report: d.report, sources: d.sources, model: d.model, goal: d.goal, charts: d.charts || [], tables: d.tables || [], date: DATE, shared: true }, extra || {});
function smallShare() {
  const resp = J('ship2-response.json'), res = J('ship2-results.json').results;
  return { report: resp.report, goal: res.goal, model: resp.model, sources: resp.sources.map((s) => ({ title: s.title, link: s.link })), charts: res.charts || [], tables: res.tables || [] };
}
function largestShare() {
  const para = 'The engine found that the measure rose over the period, driven mostly by the largest segment [S1]. ';
  let rep = '# A long report\n\n## Executive summary\n- one\n- two\n\n';
  for (let i = 1; i <= 6; i++) rep += `## ${i} Section ${i}\n${para.repeat(8)}\n\n[CHART:${i}]\n\n[TABLE:${i}]\n\n- a\n- b\n\n`;
  rep += '## What to do\n1. act\n\n## Risks and what the data cannot say\n' + para.repeat(4);
  while (rep.length < 32000) rep += '\n\n' + para.repeat(5);
  const xs = Array.from({ length: 60 }, (_, i) => 1966 + i);
  return { report: rep.slice(0, 32000), goal: 'How has trade with the United States evolved over six decades, by sector?', model: 'deepseek-flash',
    sources: Array.from({ length: 12 }, (_, i) => ({ title: 'Source ' + i + ' on the economy and trade, a long title with context', link: 'https://www150.statcan.gc.ca/n1/daily-quotidien/2509' + String(i).padStart(2, '0') + '/dq2509' + i + 'a-eng.htm' })),
    charts: Array.from({ length: 6 }, (_, k) => ({ kind: ['line', 'bars', 'scatter'][k % 3], title: 'Chart ' + k, x_name: 'year', y_name: 'value',
      ...(k % 3 === 1 ? { series: [0, 1, 2, 3].map((j) => ({ label: 'b' + j, value: j * 10 + 1 })) }
        : { series: [0, 1, 2, 3].map((j) => ({ name: 's' + j, x: xs, y: xs.map((x) => Math.round((Math.sin(x + j) * 100 + 200) * 1000) / 1000) })) }),
      ...(k % 3 === 2 ? { points: xs.concat(xs).map((x, i) => [i, Math.round(Math.cos(i) * 50 * 1000) / 1000]) } : {}) })),
    tables: Array.from({ length: 8 }, (_, k) => ({ title: 'Table ' + k, cols: ['A', 'B', 'C', 'D', 'E', 'F'],
      rows: Array.from({ length: 12 }, (_, r) => ['row ' + r, '1,234', '5.6%', 'x', 'y', 'z']) })) };
}
// the largest share with every string at its cap (titles 160, cells 80, names 80, the goal 300, links 150)
function maxShare() {
  const s = largestShare();
  const pad = (t, n) => (t + ' ' + 'lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt '.repeat(8)).slice(0, n);
  s.goal = pad(s.goal, 300);
  s.sources = s.sources.map((x) => ({ title: pad(x.title, 200), link: (x.link + '?ref=' + 'a'.repeat(400)).slice(0, 150) }));
  s.charts = s.charts.map((c) => ({ ...c, title: pad(c.title, 160), x_name: pad('year', 60), y_name: pad('value', 60),
    series: c.series.map((x) => (x.x ? { ...x, name: pad(x.name, 80) } : { ...x, label: pad(x.label, 80) })) }));
  s.tables = s.tables.map((t) => ({ title: pad(t.title, 160), cols: t.cols.map((c) => pad(c, 60)),
    rows: t.rows.map((r) => r.map((v, j) => (j === 0 ? pad(v, 80) : j < 3 ? v : pad(v, 80)))) }));
  return s;
}
// the largest PDF of tools/check_report_pdf.mjs: review4's odd headings, a long title, 35 sources (20 kept), full results
function largestFixture() {
  const R4 = J('review4.json');
  const t = 'An industry article about video game and electronics sales and returns in 2023 with a long headline';
  const sources = Array.from({ length: 35 }, (_, i) => ({ title: t + ' ' + (i + 1), link: 'https://www.example.org/articles/2026/09/article-' + (i + 1) + '?ref=' + (i + 1), date: '2026-09-' + String(1 + (i % 28)).padStart(2, '0') }));
  return { report: R4.reports.odd, sources, model: 'deepseek-v4-pro', repaired: 1, removed_figures: ['12.5'], results: R4.results.reviews, kept: [],
    name: 'review4.csv', showName: false, date: DATE, goal: R4.results.reviews.goal };
}
export const CASES = {
  'small': { what: 'the ship2 report as a share', input: () => share(smallShare()) },
  'largest-fixture': { what: 'the largest check fixture (35 sources, full results)', input: () => largestFixture() },
  'largest-share': { what: '32,000-char report, 6 charts, 8 tables, 12 sources', input: () => share(largestShare()) },
  'at-cap': { what: 'every share field at its cap, results at 20 KB', input: (W) => {
    // f10's scenarios (the reviews file's are now refused: its primary claim is an average), three times over
    const big = JSON.parse(JSON.stringify(J('review4.json').results.f10_scripts));
    big.scenarios.items = big.scenarios.items.concat(big.scenarios.items, big.scenarios.items);
    return share(maxShare(), { results: W.shareResults(big) });
  } },
  'headings-4570': { what: 'a 32,000-char report of 4,570 headings', input: () => share(smallShare(), { report: ('## h\nw\n').repeat(4570).slice(0, 32000) }) },
};
export const call = (W, input, paper) => W.build(W.model(input), { paper: paper || 'letter' });

// ---------------------------------------------------------------- a child: one writer, one case, warm or cold
const argv = process.argv.slice(2);
const opt = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
if (isMain && argv[0] === '--child') {
  const [, writer, name, mode, iters, budget, prewarm] = argv;
  if (mode === 'cold' && prewarm === '1') { 'é Å'.normalize('NFKD'); new Intl.Collator('fr').compare('a', 'b'); }
  const t0 = cpu();
  const W = requireCjs(path.resolve(writer));
  const load = cpu() - t0;
  const input = CASES[name].input(W);
  const once = () => { const a = cpu(); const u8 = call(W, input); return { ms: cpu() - a, bytes: u8.length }; };
  const first = once();
  const out = { name, load, first: first.ms, bytes: first.bytes };
  if (mode === 'warm') {
    const pre = [];
    for (let i = 0; i < 20; i++) pre.push(once().ms);
    const est = pre.slice().sort((a, b) => a - b)[10];
    const n = Math.max(30, Math.min(Number(iters), Math.floor(Number(budget) / Math.max(est, 0.01))));
    out.warm = [];
    for (let i = 0; i < n; i++) out.warm.push(once().ms);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
  process.exit(0);
}

// ---------------------------------------------------------------- the parent: children, the table, the bytes
function main() {
  const pct = (xs, p) => { const s = xs.slice().sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)]; };
  const f = (v) => (v >= 100 ? v.toFixed(0) : v.toFixed(2)).padStart(7);
  const child = (writer, name, mode) => JSON.parse(execFileSync(process.execPath, [SELF, '--child', writer, name, mode, opt('--iters', '200'), opt('--budget', '6000'), argv.includes('--no-prewarm') ? '0' : '1'],
    { env: Object.assign({}, process.env, { TZ: 'UTC' }), maxBuffer: 64 << 20 }).toString());
  const after = path.resolve(opt('--writer', CANON)), before = opt('--against') ? path.resolve(opt('--against')) : null;
  const writers = before ? [['before', before], ['after', after]] : [['writer', after]];
  const names = opt('--case') ? opt('--case').split(',') : Object.keys(CASES);
  const K = Number(opt('--cold', '9'));
  for (const n of names) if (!CASES[n]) { console.error('unknown case ' + n + ' (the cases: ' + Object.keys(CASES).join(', ') + ')'); process.exit(2); }
  console.log('report PDF writer, main-thread CPU ms of build(model(input)), node ' + process.version + (before ? '\n  before: ' + before + '\n  after:  ' + after : '\n  writer: ' + after));
  console.log('case             writer   warm med    p95     max    (n)  | cold 1st med   p95    (k)  | load med |  PDF KB');
  const raw = {};
  let identical = true;
  for (const name of names) {
    raw[name] = {};
    for (const [label, w] of writers) raw[name][label] = { warm: child(w, name, 'warm'), cold: [] };
    for (let k = 0; k < K; k++) for (const [label, w] of writers) raw[name][label].cold.push(child(w, name, 'cold'));   // interleaved
    writers.forEach(([label], i) => {
      const r = raw[name][label], ws = r.warm.warm, cs = r.cold.map((c) => c.first), ls = r.cold.map((c) => c.load);
      console.log((i ? '' : name).padEnd(17) + label.padEnd(7) + f(pct(ws, 0.5)) + ' ' + f(pct(ws, 0.95)) + ' ' + f(Math.max(...ws)) + ('(' + ws.length + ')').padStart(7) +
        '  | ' + f(pct(cs, 0.5)) + '   ' + f(pct(cs, 0.95)) + ('(' + cs.length + ')').padStart(5) + '  | ' + f(pct(ls, 0.5)) + '  | ' + (r.warm.bytes / 1024).toFixed(0).padStart(6));
    });
    if (before) {
      // the same bytes from both writers, Letter and A4 (each writer in this process, the date fixed)
      const WB = requireCjs(before), WA = requireCjs(after), sh = (u8) => createHash('sha256').update(u8).digest('hex').slice(0, 16);
      const same = ['letter', 'a4'].map((paper) => {
        const a = call(WB, CASES[name].input(WB), paper), b = call(WA, CASES[name].input(WA), paper), eq = Buffer.compare(Buffer.from(a), Buffer.from(b)) === 0;
        identical = identical && eq;
        return paper + ' ' + (eq ? 'identical, ' + a.length + ' B, sha256 ' + sh(a) : 'DIFFERENT: ' + a.length + ' B ' + sh(a) + ' vs ' + b.length + ' B ' + sh(b));
      });
      console.log(''.padEnd(17) + 'bytes: ' + same.join('; '));
      raw[name].same = same;
    }
  }
  console.log('warm: 20 calls first, then each call timed alone; cold: the first call in a fresh process, ' + (argv.includes('--no-prewarm') ? 'ICU\'s first use included' : 'after ICU\'s first use') + '; load: require() of the writer');
  if (before) console.log(identical ? 'BENCH PASS every case makes the same bytes in both writers, Letter and A4' : 'BENCH FAIL a case makes different bytes');
  if (opt('--json')) writeFileSync(opt('--json'), JSON.stringify(raw, null, 1) + '\n');
  process.exit(identical ? 0 : 1);
}
if (isMain) main();
