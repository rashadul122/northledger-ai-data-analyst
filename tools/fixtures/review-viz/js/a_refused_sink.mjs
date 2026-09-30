// the analyst view's "Charts not drawn, and why" list (52-nl2-report.js refusedItems), its source taken verbatim from
// the file and run in jsdom with the file's own esc/arr/obj and window.NLV; rep.viz.refused as nl_viz.build writes it
// (nl_viz.py ~2145: columns = the plan item's column names, the file's own headers, not escaped)
import { readFileSync } from 'node:fs';
import { SITE, requireJsdom } from './lib.mjs';
const { JSDOM, VirtualConsole } = requireJsdom();
const dom = new JSDOM('<!doctype html><body><ul id="u"></ul></body>', { runScripts: 'outside-only', virtualConsole: new VirtualConsole() });
const w = dom.window;
for (const f of ['00-core.js', '55-nl-viz.js']) w.eval(readFileSync(SITE + '/src/js/' + f, 'utf8'));
const src = readFileSync(SITE + '/src/js/52-nl2-report.js', 'utf8');
const fn = src.match(/function refusedItems\(r\) \{[\s\S]*?\n  \}\n/)[0];
const helpers = src.match(/function esc\([^)]*\)[^\n]*\n/) ? src.match(/function esc\([^)]*\)[^\n]*\n/)[0] : 'var esc = window.NLU.esc;\n';
w.eval('var obj=function(v){return !!v&&typeof v==="object"&&!Array.isArray(v);};var arr=Array.isArray;' + helpers + fn + ';window.__refused=refusedItems;');
const rep = { viz: { refused: [{ chart: 'pareto', columns: ['<img src=x onerror="window.__pwned=1">'], why: 'a pareto needs a category column', chosen_by: 'ai' }] } };
const html = w.__refused(rep);
w.document.getElementById('u').innerHTML = html;
const img = w.document.querySelector('#u img');
console.log('refusedItems HTML: ' + html);
console.log('an <img> element with onerror in the DOM: ' + !!(img && img.getAttribute('onerror')));
