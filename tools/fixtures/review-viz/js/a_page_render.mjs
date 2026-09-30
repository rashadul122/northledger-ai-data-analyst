// A(1). The page drawer (55-nl-viz.js, with 00-core.js for window.NLU) in jsdom: every survivor of a_sanitize.mjs
// drawn at 320, 600 and 900 px, inserted with innerHTML as U.draw does, its table view too; then the DOM is searched
// for anything a record string could have made: an element that is not SVG/HTML the drawer writes, an on* attribute.
// Then the drawer's own contract with raw (unsanitized) records: prototype-named kinds.
import { readFileSync } from 'node:fs';
import { SITE, requireJsdom } from './lib.mjs';
const { JSDOM, VirtualConsole } = requireJsdom();
const HERE = new URL('.', import.meta.url).pathname;

const vc = new VirtualConsole();   // jsdom's "not implemented: canvas" goes nowhere
const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', { runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.eval(readFileSync(SITE + '/src/js/00-core.js', 'utf8'));
w.eval(readFileSync(SITE + '/src/js/55-nl-viz.js', 'utf8'));
const NLV = w.NLV, U = w.NLU, host = w.document.getElementById('host');

const ALLOWED = new Set(['svg', 'title', 'text', 'tspan', 'rect', 'line', 'circle', 'path', 'g', 'p', 'ul', 'li', 'span', 'div', 'table', 'caption', 'thead', 'tbody', 'tr', 'th', 'td']);
function scan(html) {
  host.innerHTML = html;
  const bad = [];
  for (const el of host.querySelectorAll('*')) {
    const n = el.localName;
    if (!ALLOWED.has(n)) bad.push('<' + n + '>');
    for (const a of el.attributes) if (/^on/i.test(a.name) || /javascript:/i.test(a.value)) bad.push(n + '@' + a.name + '=' + a.value.slice(0, 40));
  }
  return bad;
}
const recs = JSON.parse(readFileSync(HERE + 'survivors.json', 'utf8'));
let drawn = 0, sinks = 0, threw = 0, nan = 0, kinds = {};
const report = [];
for (const { case: name, rec } of recs) {
  for (const W of [320, 600, 900]) {
    let o;
    try { o = NLV.draw(rec, W); } catch (e) { threw++; report.push(`THREW ${name} @${W}: ${e.message}`); continue; }
    drawn++;
    kinds[o.kind] = (kinds[o.kind] || 0) + 1;
    const parts = [o.html || '', o.note || '', o.tnote || '', o.table ? U.table(o.table) : ''];
    for (const h of parts) {
      const bad = scan(h);
      if (bad.length) { sinks++; report.push(`SINK ${name} @${W} kind=${o.kind}: ${bad.slice(0, 4).join(' ')}`); }
      if (/="NaN"|NaN,|="-?Infinity"/.test(h)) { nan++; if (W === 600) report.push(`NaN/Infinity coordinates: ${name} kind=${o.kind}`); }
    }
    // the payload shows as text somewhere (escaped, not dropped): a spot check on the title
    if (W === 600 && /onerror=alert\(1\)/.test(String(rec.title)) && !/onerror=alert\(1\)/.test(host.textContent + (o.label || ''))) report.push(`payload title not visible as text: ${name}`);
  }
}
console.log(`page drawer: ${drawn} drawings of ${recs.length} sanitized survivors x 3 widths; kinds drawn ${JSON.stringify(kinds)}; ` +
  `unescaped sinks ${sinks}; throws ${threw}; drawings with NaN/Infinity coordinates ${nan}`);
if (report.length) console.log(report.join('\n'));

// the drawer's own contract (spec: a kind it does not know is drawn as its table), fed records the worker did not see
console.log('\nraw records straight into NLV.draw / NLV.kindOf (no sanitizeChart):');
const base = recs.find((r) => r.case === 'xss:waterfall').rec;
const raw = (kind, extra) => Object.assign(JSON.parse(JSON.stringify(base)), { kind }, extra || {});
for (const [label, rec] of [
  ['kind "sankey"', raw('sankey')],
  ['kind "constructor" + body/after', raw('constructor', { H: 20, body: '<image href="x" onerror="alert(document.domain)"/>', after: '<img src=x onerror=alert(7)>' })],
  ['kind "__proto__"', raw('__proto__')],
  ['kind "toString"', raw('toString')],
  ['kind "valueOf"', raw('valueOf')],
  ['kind "hasOwnProperty"', raw('hasOwnProperty')],
]) {
  let k, o, err = '';
  try { k = NLV.kindOf(rec); } catch (e) { k = 'THREW ' + e.constructor.name + ': ' + e.message; }
  try { o = NLV.draw(rec, 600); } catch (e) { err = 'THREW ' + e.constructor.name + ': ' + e.message; }
  const bad = o ? scan((o.html || '') + (o.note || '')) : [];
  console.log(`  ${label}: kindOf -> ${k}; draw -> ${err || 'kind ' + o.kind + ', layout ' + o.layout}${bad.length ? '; INJECTED: ' + bad.join(' ') : ''}`);
}
