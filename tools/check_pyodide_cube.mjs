// The packed engine, run in the Pyodide the page runs (Python 3.12), on a SYNTHETIC official cube made here.
//
//   node tools/check_pyodide_cube.mjs                 # load engine/northledger-browser.zip in Pyodide and read the cube
//   node tools/check_pyodide_cube.mjs --emit-cube     # print the cube (CSV) and exit: the Python tests read the same bytes
//   NL_REQUIRE_PYODIDE=1 node tools/check_pyodide_cube.mjs   # a skip (no Playwright, no network) is then a failure
//
// Why it exists (6 October 2026). The structure layer (engine/nl_structure.py) is what keeps an official table from being
// read the wrong way (a table of series with totals beside their parts: its rows averaged together). Every native test ran on
// Python 3.9, where a regex flag in the middle of a pattern is a warning; Pyodide is Python 3.12, where it is an error. The
// structure layer failed to import in the page, the engine read the file the old way, and 986 green tests said nothing. This
// check runs the PACKED engine (the zip the page loads) in the page's own Pyodide, planner off, on a synthetic table in the
// Statistics Canada column layout (never a licensed or dev file), and asserts what the page would show:
//   - nl_structure imports (the error, if not, is printed as Python raised it);
//   - the packed files are the repo's files (a stale zip would test an old engine);
//   - estimand present, structure.kind "cube", usable true, no structure.error, the slice is the totals of both dimensions;
//   - the headline equals the pandas reference: the number is WRITTEN HERE (REFERENCE), computed by pandas from this
//     cube's CSV (tools/test_nl_structure.py recomputes it from `--emit-cube` and compares, native engine included);
//   - the figures are not a sum over rows (that would be 4 times the Canada x Total series: every total beside its parts).
// It must FAIL when the structure import breaks: with one regex flag moved into the middle of a pattern the engine refuses
// the cube (structure.kind "error", no estimand) and this prints the exception. Needs Node and Playwright
// (PLAYWRIGHT_MODULE=/path/to/node_modules/playwright), Chrome (CHROME) and cdn.jsdelivr.net (Pyodide, numpy, pandas).
// Wave 5e adds the cases of tools/fixtures/structure/pyodide_cases.json, run in the SAME Pyodide session: each is a file of the
// regression pack read through the adapter, and the packed engine must say what the NATIVE engine says (tools/make_pyodide_cases.py
// writes the json; tools/test_nl_regress.py checks it against the native engine): parts reporting in disjoint periods (the sorted-lookup
// subset search), a weekly table (the cadence), a French table (the accent fold, unicodedata), and the unnamed-aggregate fit on a rate
// panel with and without its aggregate (the one decision a BLAS could tip). The extra cases take about 10 s.
// Nothing is written, no port is opened. About 40 s. Exit 0 pass, 1 fail, 2 skipped (printed with the reason).
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = resolve(HERE, '..');
const ENGINE = join(SITE, 'engine');
const PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
const CASES = JSON.parse(readFileSync(join(SITE, 'tools', 'fixtures', 'structure', 'pyodide_cases.json'), 'utf8'));
const CASE_BYTES = Object.fromEntries(Object.entries(CASES).map(([name, c]) => {
  const raw = readFileSync(join(SITE, 'tools', 'fixtures', 'structure', 'regress', c.file));
  return [name, c.file.endsWith('.gz') ? gunzipSync(raw) : raw];
}));

// ---------------------------------------------------------------------------------------- the synthetic cube
// 5 regions and a Canada total, 3 industries and a Total, 36 months (2020-01 to 2022-12), values in thousands of dollars,
// every total the exact sum of its parts as published, a few region x industry cells suppressed (blank, STATUS "x"; the
// totals stay whole). The Statistics Canada layout: REF_DATE, GEO, DGUID, an industry column, Adjustments, UOM, UOM_ID,
// SCALAR_FACTOR, SCALAR_ID, VECTOR, COORDINATE, VALUE, STATUS, SYMBOL, TERMINATED, DECIMALS.
const REGIONS = ['Atlantic', 'Quebec', 'Ontario', 'Prairies', 'Pacific'];
const INDUSTRIES = ['Food and beverage retailers', 'Motor vehicle and parts dealers', 'General merchandise retailers'];
const TOTAL_GEO = 'Canada';
const TOTAL_IND = 'Total retail trade';
const LEVEL = [[1800, 2600, 1400], [5200, 7400, 3900], [8100, 11200, 6300], [3300, 4800, 2500], [2400, 3500, 1800]];
const SEASON = [0.82, 0.8, 0.95, 0.98, 1.04, 1.06, 1.05, 1.07, 1.0, 1.02, 1.08, 1.3];
const SUPPRESSED = [[1, 0, 7], [3, 2, 19], [4, 1, 20], [0, 2, 31]];            // [region, industry, month index]
const MONTHS = Array.from({ length: 36 }, (_, i) => (2020 + Math.floor(i / 12)) + '-' + String(i % 12 + 1).padStart(2, '0'));

function lcg(seed) { let s = seed >>> 0; return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296; }

function leafValues() {
  const rnd = lcg(20261006), v = REGIONS.map(() => INDUSTRIES.map(() => []));
  REGIONS.forEach((_, r) => INDUSTRIES.forEach((__, k) => {
    for (let t = 0; t < 36; t++) v[r][k].push(Math.round(LEVEL[r][k] * Math.pow(1.004, t) * SEASON[t % 12] * (1 + 0.02 * (rnd() + rnd() + rnd() - 1.5))));
  }));
  return v;
}

function cubeCsv() {
  const v = leafValues();
  const cols = ['REF_DATE', 'GEO', 'DGUID', 'North American Industry Classification System (NAICS)', 'Adjustments', 'UOM', 'UOM_ID',
    'SCALAR_FACTOR', 'SCALAR_ID', 'VECTOR', 'COORDINATE', 'VALUE', 'STATUS', 'SYMBOL', 'TERMINATED', 'DECIMALS'];
  const geos = [TOTAL_GEO].concat(REGIONS), inds = [TOTAL_IND].concat(INDUSTRIES);
  const hidden = new Set(SUPPRESSED.map((s) => s.join(',')));
  const rows = [cols];
  MONTHS.forEach((mo, t) => {
    geos.forEach((g, gi) => inds.forEach((ind, ii) => {
      let val;
      const rs = gi === 0 ? REGIONS.map((_, r) => r) : [gi - 1], ks = ii === 0 ? INDUSTRIES.map((_, k) => k) : [ii - 1];
      val = rs.reduce((a, r) => a + ks.reduce((b, k) => b + v[r][k][t], 0), 0);
      const cut = gi > 0 && ii > 0 && hidden.has([gi - 1, ii - 1, t].join(','));
      rows.push([mo, g, '2021A0000' + (11 + gi), ind, 'Unadjusted', 'Dollars', '81', 'thousands', '3',
        'v' + (100000 + gi * 10 + ii), (gi + 1) + '.' + (ii + 1), cut ? '' : String(val), cut ? 'x' : 'A', '', '', '0']);
    }));
  });
  return rows.map((r) => r.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\n') + '\n';
}

if (process.argv.includes('--emit-cube')) { process.stdout.write(cubeCsv(), () => process.exit(0)); await new Promise(() => {}); }

// The expected headline (Canada x Total retail trade, the latest 12 months against the 12 before, base units: thousands x 1000), from a
// pandas reference run on this cube's CSV (tools/test_nl_structure.py: test_w5b_the_pyodide_check_cube_...). Written here, not computed here.
const REFERENCE = {"rows": 864, "latest_total": 908572000, "prior_total": 865101000, "change_pct": 5.024962403233846, "latest_months": ["2022-01", "2022-12"], "prior_months": ["2021-01", "2021-12"]};

// ---------------------------------------------------------------------------------------- the run
const t00 = Date.now();
let playwright;
try { playwright = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright'); } catch (e) {
  skip('Playwright not found (set PLAYWRIGHT_MODULE to the playwright package folder)');
}
function skip(why) {
  console.log('PYODIDE SKIP: ' + why);
  process.exit(process.env.NL_REQUIRE_PYODIDE ? 1 : 2);
}
async function cdnReachable() {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 6000);
    const r = await fetch(PYODIDE_URL + 'pyodide.js', { method: 'HEAD', signal: ctl.signal });
    clearTimeout(t);
    return r.ok ? null : 'cdn.jsdelivr.net answered ' + r.status;
  } catch (e) { return 'cdn.jsdelivr.net cannot be reached from here (' + String(e.cause && e.cause.code || e.name || e.message) + ')'; }
}
const why = await cdnReachable();
if (why) skip(why);

const zip = readFileSync(join(ENGINE, 'northledger-browser.zip'));
const cube = Buffer.from(cubeCsv(), 'utf8');
const sha = (b) => createHash('sha256').update(b).digest('hex');
const PACKED = ['nl_browser.py', 'nl_structure.py', 'nl_scenarios.py', 'nl_viz.py', 'nl_inference.py', 'flag_vocab.json'];
const sources = Object.fromEntries(PACKED.map((f) => [f, sha(readFileSync(join(ENGINE, f)))]));

const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await playwright.chromium.launch({ executablePath: CHROME, headless: true });
let got;
try {
  const page = await (await browser.newContext()).newPage();
  await page.route('http://nl-site.test/**', async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/') return route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>engine check</body></html>' });
    if (p === '/engine.zip') return route.fulfill({ status: 200, contentType: 'application/zip', body: zip });
    if (p === '/cube.csv') return route.fulfill({ status: 200, contentType: 'text/csv', body: cube });
    if (p.startsWith('/case/') && CASE_BYTES[p.slice(6)]) return route.fulfill({ status: 200, contentType: 'text/csv', body: CASE_BYTES[p.slice(6)] });
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('http://nl-site.test/');
  got = await page.evaluate(async ({ url, names }) => {
    const t0 = performance.now();
    await new Promise((res, rej) => { const s = document.createElement('script'); s.src = url + 'pyodide.js'; s.onload = res; s.onerror = () => rej(new Error('pyodide.js did not load')); document.head.appendChild(s); });
    const py = await loadPyodide({ indexURL: url });
    await py.loadPackage(['numpy', 'pandas', 'sqlite3']);
    py.unpackArchive(await (await fetch('/engine.zip')).arrayBuffer(), 'zip', { extractDir: '/nl' });
    py.runPython('import sys\nif "/nl" not in sys.path:\n    sys.path.insert(0, "/nl")');
    const t1 = performance.now();
    const env = JSON.parse(String(py.runPython(
      'import json, platform, hashlib, pandas, numpy\n' +
      'files = {}\n' +
      'for f in ' + JSON.stringify(['nl_browser.py', 'nl_structure.py', 'nl_scenarios.py', 'nl_viz.py', 'nl_inference.py', 'flag_vocab.json']) + ':\n' +
      '    files[f] = hashlib.sha256(open("/nl/" + f, "rb").read()).hexdigest()\n' +
      'try:\n    import nl_structure\n    ierr = ""\nexcept Exception as e:\n    ierr = type(e).__name__ + ": " + str(e)[:300]\n' +
      'json.dumps({"python": platform.python_version(), "pandas": pandas.__version__, "numpy": numpy.__version__, "files": files, "import_error": ierr})')));
    const nl = py.pyimport('nl_browser');
    const buf = new Uint8Array(await (await fetch('/cube.csv')).arrayBuffer());
    const t2 = performance.now();
    const rep = JSON.parse(String(nl.run_json(buf, 'cube.csv', '', '{}', '2026-09-30')));
    const t3 = performance.now();
    const est = rep.estimand || null, st = rep.structure || null;
    // wave 5e: the extra cases, in this same session
    const cases = {};
    for (const n of names) {
      const c0 = performance.now();
      try {
        const b = new Uint8Array(await (await fetch('/case/' + n)).arrayBuffer());
        const r = JSON.parse(String(nl.run_json(b, 'table.csv', '', '{}', '2026-09-30')));
        const e2 = r.estimand || null, f2 = (e2 && e2.figures) || {}, s2 = r.structure || {};
        cases[n] = { ok: !!r.ok, estimand: !!e2, refused: !e2 && /business analysis did not run/.test((r.story && r.story.headline) || ''),
          kind: s2.kind || null, usable: s2.usable === undefined ? null : s2.usable,
          roles: Object.fromEntries((s2.dims || []).map((d) => [d.column, d.role])),
          source: e2 ? 'estimand' : null, prior: f2.prior ? f2.prior.value : null, latest: f2.latest ? f2.latest.value : null,
          pct: f2.change_pct ? f2.change_pct.value : null, seconds: (performance.now() - c0) / 1000,
          // wave 5f: the aggregation, the withheld columns, the rows the adapter left out, and the analysis ledger's amount totals
          aggregation: e2 && e2.measure ? e2.measure.aggregation : null,
          flagged: ((r.privacy && r.privacy.flagged) || []).map((f) => f.column + ':' + f.decision).sort(),
          left_out: ((r.cleaning && r.cleaning.fixes) || []).filter((f) => f.rule === 'total_rows_left_out' || f.rule === 'partial_month_left_out')
            .reduce((a, f) => a + (f.count || 0), 0),
          ledger_amount: (() => { try { const L = {}; for (const x of (JSON.parse(r.downloads.ledger_json).analysis_ledger || [])) L[x.id] = x.value;
            return [L['measure.amount.total.prior12'] === undefined ? null : L['measure.amount.total.prior12'], L['measure.amount.total.last12'] === undefined ? null : L['measure.amount.total.last12']]; } catch (x) { return null; } })() };
      } catch (err) { cases[n] = { error: String(err).slice(0, 400), seconds: (performance.now() - c0) / 1000 }; }
    }
    return {
      cases, env, load_s: (t1 - t0) / 1000, run_s: (t3 - t2) / 1000,
      ok: rep.ok, error: rep.error || null, rows: rep.input && rep.input.rows,
      layout: rep.input && rep.input.layout, headline: rep.story && rep.story.headline,
      structure: st && { kind: st.kind, usable: st.usable, error: st.error || null, reason: st.reason || null, dims: (st.dims || []).map((d) => ({ column: d.column, role: d.role, total: d.total })) },
      estimand: est && { text: est.text, slice: est.slice, plan_source: est.plan_source, reconciles: est.reconciles, comparison: est.comparison, figures: est.figures, period: est.period && est.period.noun },
      did_not_run: !!(rep.story && /did not run/.test(rep.story.headline || '')), n_business: (rep.findings || []).filter((f) => f.kind === 'business').length,
    };
  }, { url: PYODIDE_URL, names: Object.keys(CASES) });
} finally { await browser.close(); }

// ---------------------------------------------------------------------------------------- the assertions
let failed = 0;
function ok(name, cond, detail) {
  console.log((cond ? 'PYODIDE PASS ' : 'PYODIDE FAIL ') + name + (cond ? '' : ': ' + (detail || '')));
  if (!cond) failed++;
}
const close = (a, b, tol) => typeof a === 'number' && Math.abs(a - b) <= tol;
const e = got.env, st = got.structure, est = got.estimand;
console.log('PYODIDE python ' + e.python + ', pandas ' + e.pandas + ', numpy ' + e.numpy + '; load ' + got.load_s.toFixed(1) + ' s, run ' + got.run_s.toFixed(1) + ' s');

ok('the page runs Python 3.11 or later (the version whose regex rules the native tests do not apply)', /^3\.(1[1-9]|[2-9]\d)/.test(e.python), e.python);
ok('nl_structure imports in the page\'s Python', e.import_error === '', e.import_error);
ok('the packed engine files are the repo\'s files (a stale zip would test an old engine)',
  PACKED.every((f) => e.files[f] === sources[f]), PACKED.filter((f) => e.files[f] !== sources[f]).join(', ') + ' differ: run tools/pack_engine.py');
ok('the run finished (ok, no error)', got.ok === true && !got.error, String(got.error));
ok('the cube was read by its structure: structure.kind "cube", usable', !!st && st.kind === 'cube' && st.usable === true,
  st ? 'kind ' + st.kind + ', usable ' + st.usable + (st.error ? ', error ' + JSON.stringify(st.error) : '') + (e.import_error ? ' (import: ' + e.import_error + ')' : '')
    : 'no structure (the file was read the old way)');
ok('no structure.error', !!st && !st.error, JSON.stringify(st && st.error));
ok('the business analysis ran (the story does not say it did not)', !got.did_not_run && got.n_business > 0, String(got.headline).slice(0, 160));
ok('estimand present, engine default slice, reconciled to the engine\'s charted series', !!est && est.plan_source === 'engine_default' && est.reconciles === true,
  JSON.stringify(est && { plan_source: est.plan_source, reconciles: est.reconciles }));
ok('built from the right slice: the totals of both dimensions (Canada, Total retail trade), the file read as one series',
  !!est && est.slice.some((x) => x.dim === 'GEO' && x.member === TOTAL_GEO) &&
  est.slice.some((x) => /NAICS/.test(x.dim) && x.member === TOTAL_IND) &&
  !!got.layout && got.layout.layout === 'structured cube slice' && got.layout.series === 24 &&
  got.layout.where && got.layout.where.GEO === TOTAL_GEO,
  JSON.stringify({ slice: est && est.slice, layout: got.layout }));
ok('both dimensions are partitions of their parts (Canada of 5 regions, Total of 3 industries)', !!st && st.dims.some((d) => d.column === 'GEO' && d.role === 'partition' && d.total === TOTAL_GEO) &&
  st.dims.some((d) => /NAICS/.test(d.column) && d.role === 'partition' && d.total === TOTAL_IND), JSON.stringify(st && st.dims));
const F = est && est.figures;
ok('the windows are the latest 12 months and the 12 before', !!est && JSON.stringify(est.comparison) === JSON.stringify({ latest: REFERENCE.latest_months, prior: REFERENCE.prior_months }),
  JSON.stringify(est && est.comparison));
ok('the headline equals the pandas reference: latest 12-month total', !!F && close(F.latest.value, REFERENCE.latest_total, 1e-3),
  F && F.latest.value + ' vs ' + REFERENCE.latest_total);
ok('the headline equals the pandas reference: prior 12-month total', !!F && close(F.prior.value, REFERENCE.prior_total, 1e-3),
  F && F.prior.value + ' vs ' + REFERENCE.prior_total);
ok('the headline equals the pandas reference: the change in percent', !!F && close(F.change_pct.value, REFERENCE.change_pct, 1e-6),
  F && F.change_pct.value + ' vs ' + REFERENCE.change_pct);
ok('the story\'s headline states that change', !!F && String(got.headline).includes(F.change_pct.text) && /published totals/.test(got.headline),
  String(got.headline));
// wave 5e: the packed engine says in Pyodide what the native engine says (the json), case by case
let caseSeconds = 0;
for (const [name, want] of Object.entries(CASES)) {
  const g = got.cases[name] || { error: 'not run' };
  caseSeconds += g.seconds || 0;
  if (g.error) { ok('case ' + name + ': the run finished', false, g.error); continue; }
  // wave 5g: an object compares by its keys in sorted order (the native json is written sorted; a table's dimensions come in the file's order)
  const canon = (x) => Array.isArray(x) ? x.map(canon) : (x && typeof x === 'object') ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, canon(x[k])])) : x;
  const same = (a, b) => JSON.stringify(canon(a)) === JSON.stringify(canon(b));
  ok('case ' + name + ': read as the native engine reads it (ok, kind, usable, estimand, refused)',
    g.ok === want.ok && g.kind === want.kind && g.usable === want.usable && g.estimand === want.estimand && g.refused === want.refused,
    JSON.stringify({ pyodide: [g.ok, g.kind, g.usable, g.estimand, g.refused], native: [want.ok, want.kind, want.usable, want.estimand, want.refused] }));
  ok('case ' + name + ': the same role for every dimension', same(g.roles, want.roles), JSON.stringify({ pyodide: g.roles, native: want.roles }));
  ok('case ' + name + ': the same aggregation, withheld columns and rows left out', g.aggregation === (want.aggregation === undefined ? g.aggregation : want.aggregation) &&
    same(g.flagged, want.flagged) && g.left_out === want.left_out, JSON.stringify({ pyodide: [g.aggregation, g.flagged, g.left_out], native: [want.aggregation, want.flagged, want.left_out] }));
  if (want.ledger_amount && want.ledger_amount[0] !== null && want.ledger_amount[1] !== null) {
    ok('case ' + name + ': the same ledger totals of the amount', !!g.ledger_amount && close(g.ledger_amount[0], want.ledger_amount[0], 1e-6 * Math.max(1, Math.abs(want.ledger_amount[0]))) &&
      close(g.ledger_amount[1], want.ledger_amount[1], 1e-6 * Math.max(1, Math.abs(want.ledger_amount[1]))), JSON.stringify({ pyodide: g.ledger_amount, native: want.ledger_amount }));
  }
  if (want.source === 'estimand') {
    ok('case ' + name + ': the same figures (prior, latest, change)', close(g.prior, want.prior, 1e-6 * Math.max(1, Math.abs(want.prior))) &&
      close(g.latest, want.latest, 1e-6 * Math.max(1, Math.abs(want.latest))) && close(g.pct, want.pct, 1e-6),
      JSON.stringify({ pyodide: [g.prior, g.latest, g.pct], native: [want.prior, want.latest, want.pct] }));
  }
}
console.log('PYODIDE the ' + Object.keys(CASES).length + ' wave 5e cases took ' + caseSeconds.toFixed(1) + ' s');
ok('the extra cases stay within their budget (60 s in all)', caseSeconds < 60, caseSeconds.toFixed(1) + ' s');
console.log('PYODIDE ' + (failed ? failed + ' check(s) FAILED' : 'ALL PASS') + ' (' + ((Date.now() - t00) / 1000).toFixed(1) + ' s)');
process.exit(failed ? 1 : 0);
