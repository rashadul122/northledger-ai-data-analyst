// shared helpers for the chart review's repro scripts (30 Sep 2026; imports the site read-only). jsdom is read from
// the node_modules that holds Playwright: PLAYWRIGHT_MODULE=/path/to/node_modules/playwright, as tools/check_ui.js reads it
import { createRequire } from 'node:module';
import pathMod from 'node:path';
export const requireJsdom = () => createRequire(pathMod.join(pathMod.dirname(process.env.PLAYWRIGHT_MODULE || ''), 'x.js'))('jsdom');
import { readFileSync } from 'node:fs';
export const ROOT = new URL('../../../../..', import.meta.url).pathname.replace(/\/$/, '');
export const PROXY = ROOT + '/insight-proxy';
export const SITE = ROOT + '/portfolio-website';
export const SPEC = JSON.parse(readFileSync(SITE + '/tools/fixtures/viz/spec.json', 'utf8'));
export const EX = Object.fromEntries(Object.entries(SPEC.examples).map(([k, v]) => [k, v.record]));
export const clone = (x) => JSON.parse(JSON.stringify(x));

// payloads sized to the caps they must fit (cell text 12, step text 24, labels 80)
export const P = {
  long: '"\'><img src=x onerror=alert(1)><script>alert(2)</script><svg onload=alert(3)>',
  s24: '"><img src=x onerror=1>',       // 23 chars
  s12: '"<i>o</i>\'',                    // 10 chars
  s40: '"><img src=x onerror=alert(40)>',   // 31 chars
};

// every string leaf of a record replaced by a payload that fits its path's cap (the record then may fail its own
// checks: the point is which strings reach a renderer)
export function poison(rec, pick) {
  const walk = (v, path) => {
    if (typeof v === 'string') return pick(path, v);
    if (Array.isArray(v)) return v.map((x, i) => walk(x, path.concat(i)));
    if (v && typeof v === 'object') { const o = {}; for (const k of Object.keys(v)) o[k] = walk(v[k], path.concat(k)); return o; }
    return v;
  };
  return walk(rec, []);
}
