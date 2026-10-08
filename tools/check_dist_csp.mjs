#!/usr/bin/env node
/* Proves the CSP and the other headers on the REAL deploy folder, in a real browser, before launch.

     PLAYWRIGHT_MODULE=/path/to/node_modules/playwright [CHROME=/path/to/chrome] \
       node tools/check_dist_csp.mjs [dist] [--no-browser]

   It serves dist/ with tools/serve_dist.mjs (which applies dist/_headers and dist/_redirects the way
   Cloudflare does), then drives headless Chrome through the whole demo with the sample file and asserts:
     1. the page, the engine worker and the other pages carry exactly the headers _headers promises;
     2. the policy is ENFORCED in this browser (a fetch to an outside address from the page is blocked and
        reported; a worker with the engine worker's policy cannot reach the outside or the AI proxy);
     3. the full demo run (sample, "Continue without AI", the report, a download) completes under the
        policy with ZERO violations, ZERO CSP-blocked requests, ZERO page errors;
     4. every request of the run is same-origin: nothing to cdn.jsdelivr.net, nothing to the AI proxy
        (no AI was chosen), all eleven pinned Pyodide files from /engine/pyodide/<tag>/;
     5. /case-study and /agent-demo load under their no-network policy without a violation.
   --no-browser runs only the server-side part of 1 (no Chrome needed).
   Not covered here (needs the AI, which costs money): opening a PDF from the AI report under the policy;
   a best-effort blob: PDF probe prints INFO only. Exit 0 only if every check passes. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createServer, parseHeaders, resolveHeaders } from './serve_dist.mjs';

const args = process.argv.slice(2);
const dist = path.resolve(args.find((a) => !a.startsWith('--')) || 'dist');
const noBrowser = args.includes('--no-browser');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const cfg = JSON.parse(fs.readFileSync(path.join(root, 'deploy', 'cloudflare.config.json'), 'utf8'));
const pins = JSON.parse(fs.readFileSync(path.join(root, cfg.runtime.pins), 'utf8'));
const site = JSON.parse(fs.readFileSync(path.join(root, 'site.config.json'), 'utf8'));
const PROXY = new URL(site.ai_proxy_url).origin;
const mode = cfg.runtime.mode;
const results = [];
function rec(name, ok, detail) { results.push({ name, ok, detail: detail || '' }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  ' + detail : '')); }
const hdrs = parseHeaders(fs.readFileSync(path.join(dist, '_headers'), 'utf8'));
const cspName = cfg.headers.csp_report_only ? 'content-security-policy-report-only' : 'content-security-policy';

const srv = createServer(dist, { probe: true });
await new Promise((r) => srv.listen(0, '127.0.0.1', r));
const base = 'http://127.0.0.1:' + srv.address().port;

async function head(p) { const r = await fetch(base + p, { redirect: 'manual' }); return r; }
function lc(h) { const o = {}; h.forEach((v, k) => { o[k.toLowerCase()] = v; }); return o; }

// ---- 1. server side: the resolved headers arrive as written
for (const p of ['/', '/case-study', '/agent-demo', '/engine/worker.js', '/engine/pack.json', '/favicon.svg', '/nope']) {
  const r = await head(p);
  const got = lc(r.headers);
  const want = resolveHeaders(hdrs, p);
  const miss = Object.keys(want).filter((k) => (got[k.toLowerCase()] || '') !== want[k]);
  rec('headers ' + p, miss.length === 0 && (p === '/nope' ? r.status === 404 : r.status === 200), miss.length ? 'differs: ' + miss.join(',') : 'status ' + r.status);
}
for (const [from, to] of [['/index.html', '/'], ['/case-study.html', '/case-study'], ['/northledger-ai-data-analyst/', '/'], ['/try', '/#try']]) {
  const r = await head(from);
  rec('redirect ' + from, (r.headers.get('location') || '') === to, 'to ' + r.headers.get('location'));
}
rec('_headers is not served', (await head('/_headers')).status === 404);

if (noBrowser) {
  srv.close();
  const bad = results.filter((r) => !r.ok);
  console.log('\n(--no-browser) ' + (bad.length ? bad.length + ' FAILED' : 'server side OK; the browser checks were NOT run'));
  process.exit(bad.length ? 1 : 0);
}

// ---- the browser
const modPath = process.env.PLAYWRIGHT_MODULE;
if (!modPath) { console.log('PLAYWRIGHT_MODULE is not set: cannot run the browser checks'); srv.close(); process.exit(2); }
const { chromium } = createRequire(import.meta.url)(modPath);
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch({ executablePath: fs.existsSync(CHROME) ? CHROME : undefined, headless: true });
const ctx = await browser.newContext({ acceptDownloads: true, serviceWorkers: 'block', viewport: { width: 1280, height: 900 } });
const reqs = [], failed = [], consoleMsgs = [], pageErrs = [];
ctx.on('request', (r) => reqs.push(r.url()));
ctx.on('requestfailed', (r) => failed.push(r.url() + ' ' + (r.failure() && r.failure().errorText)));
const origin = new URL(base).origin;
async function newPage() {
  const p = await ctx.newPage();
  p.on('console', (m) => { const t = m.text(); if (/Content Security Policy|Refused to|violates/i.test(t)) consoleMsgs.push(t.slice(0, 200)); });
  p.on('pageerror', (e) => pageErrs.push(String(e).slice(0, 200)));
  await p.addInitScript(() => { window.__csp = []; document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI), true); });
  return p;
}
async function csp(p) { return p.evaluate(() => window.__csp.slice()); }

try {
  // ---- 2. enforcement controls
  const page = await newPage();
  const resp = await page.goto(base + '/', { waitUntil: 'load' });
  rec('page loads (200) and carries a CSP', resp.status() === 200 && !!resp.headers()[cspName], 'status ' + resp.status());
  const ctrl = await page.evaluate(() => fetch('https://example.invalid/', { mode: 'no-cors' }).then(() => 'reached', () => 'blocked'));
  await page.waitForTimeout(200);
  const v = await csp(page);
  rec('control: the page cannot fetch an outside address', ctrl === 'blocked' && v.some((x) => /^connect-src/.test(x)), ctrl + '; violations ' + JSON.stringify(v));
  await page.evaluate(() => { window.__csp.length = 0; });
  const probe = await page.evaluate((targets) => new Promise((res) => {
    const w = new Worker('/__probe/worker.js'); w.onmessage = (e) => res(e.data); w.onerror = () => res({ error: 'worker failed to start' }); w.postMessage({ targets });
  }), { outside: 'https://example.invalid/', proxy: PROXY + '/' });
  rec('control: a worker under the engine worker\'s policy reaches neither the outside nor the AI proxy, but its own site',
    /^blocked/.test(probe.outside || '') && /^blocked/.test(probe.proxy || '') && probe.same_origin === 'reached', JSON.stringify(probe));
  await page.evaluate(() => { window.__csp.length = 0; });
  reqs.length = 0; failed.length = 0; consoleMsgs.length = 0; pageErrs.length = 0;

  // ---- 3. the whole demo under the policy
  const t0 = Date.now();
  await page.goto(base + '/#try', { waitUntil: 'load' });
  await page.evaluate(() => { window.__csp.length = 0; });
  await page.click('#try-sample');
  await page.waitForSelector('#try-pd:not([hidden]), #try-report:not([hidden]), #try-msg:not([hidden])', { timeout: 240000 });
  if (await page.isVisible('#try-pd')) await page.click('#try-pd-noai');
  await page.waitForSelector('#try-report:not([hidden]), #try-msg:not([hidden])', { timeout: 240000 });
  const msgShown = await page.isVisible('#try-msg');
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  rec('the sample demo runs to a report under the policy', !msgShown && await page.isVisible('#try-report'), secs + ' s' + (msgShown ? '; the page said: ' + (await page.textContent('#try-msg')).slice(0, 160) : ''));
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), page.click('#try-report [data-dl="clean_csv"]')]);
  const dp = await dl.path();
  rec('a report download (blob:) works under the policy', !!dp && fs.statSync(dp).size > 0, dl.suggestedFilename());
  const viol = await csp(page);
  rec('zero securitypolicyviolation events in the page', viol.length === 0, JSON.stringify(viol.slice(0, 4)));
  rec('zero CSP-blocked requests (the worker included)', !failed.some((f) => /BLOCKED_BY_CSP|BLOCKED/.test(f)), JSON.stringify(failed.slice(0, 4)));
  rec('zero CSP messages in the console, zero page errors', consoleMsgs.length === 0 && pageErrs.length === 0, JSON.stringify(consoleMsgs.concat(pageErrs).slice(0, 3)));
  const outside = reqs.filter((u) => !/^(data|blob):/.test(u) && new URL(u).origin !== origin);
  rec('every request of the run is same-origin (no CDN, no AI proxy)', outside.length === 0, JSON.stringify(outside.slice(0, 4)));
  if (mode === 'self') {
    const pre = '/engine/pyodide/' + pins.tag + '/';
    const got = new Set(reqs.filter((u) => u.startsWith(base + pre)).map((u) => u.slice((base + pre).length)));
    const want = [...pins.files.map((f) => f.name), 'pyodide-lock.json'];
    const missing = want.filter((n) => !got.has(n));
    rec('all eleven pinned runtime files were fetched from this site', missing.length === 0, missing.length ? 'not fetched: ' + missing.join(', ') : want.length + ' files');
  }
  rec('the engine files came from this site', ['/engine/worker.js', '/engine/northledger-browser.zip', '/engine/sample-messy.csv'].every((p) => reqs.includes(base + p)));

  // best-effort PDF probe (INFO only): a blob: PDF opened from the page inherits the page's policy
  try {
    const info = await page.evaluate(() => {
      const bytes = new TextEncoder().encode('%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF');
      return URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
    });
    const [pop] = await Promise.all([ctx.waitForEvent('page', { timeout: 8000 }), page.evaluate((u) => { const a = document.createElement('a'); a.href = u; a.target = '_blank'; a.rel = 'noopener'; document.body.appendChild(a); a.click(); a.remove(); }, info)]);
    await pop.waitForTimeout(1500);
    console.log('INFO  blob: PDF opened in a new tab: url ' + pop.url().slice(0, 40) + ' (headless Chrome may download instead of showing the viewer; check a real PDF in Chrome and Safari by hand)');
    await pop.close();
  } catch (e) { console.log('INFO  blob: PDF probe: ' + String(e.message || e).slice(0, 100) + ' (a download instead of a tab is normal in headless Chrome)'); }

  // ---- 5. the other pages
  for (const p of ['/case-study', '/agent-demo', '/northledger-ai-data-analyst/']) {
    reqs.length = 0; failed.length = 0; pageErrs.length = 0; consoleMsgs.length = 0;
    const q = await newPage();
    const r = await q.goto(base + p, { waitUntil: 'load' });
    await q.waitForTimeout(800);
    const vv = await csp(q);
    const out2 = reqs.filter((u) => !/^(data|blob):/.test(u) && new URL(u).origin !== origin);
    rec('page ' + p + ' loads with no violation, error or outside request', r.status() === 200 && vv.length === 0 && pageErrs.length === 0 && out2.length === 0 && consoleMsgs.length === 0,
      'status ' + r.status() + ' final ' + new URL(q.url()).pathname + ' ' + JSON.stringify(vv.concat(pageErrs, out2).slice(0, 3)));
    await q.close();
  }
} catch (e) {
  rec('the browser run', false, String(e && e.stack || e).slice(0, 300));
} finally {
  await browser.close(); srv.close();
}
const bad = results.filter((r) => !r.ok);
console.log('\n' + (bad.length ? bad.length + ' of ' + results.length + ' FAILED' : 'ALL ' + results.length + ' PASS'));
process.exit(bad.length ? 1 : 0);
