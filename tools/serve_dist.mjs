#!/usr/bin/env node
/* A local stand-in for Cloudflare Workers Static Assets, for tests only: it serves dist/ and applies
   what Cloudflare applies to it, from the SAME files the deploy uploads:
     - _headers   (matching blocks in file order; the same header from several blocks is JOINED with ", ";
                   "! Name" detaches; * is a splat, :name a placeholder; host blocks ignored)
     - _redirects (first match wins; * splat, :name placeholders)
     - html_handling "auto-trailing-slash" (/index.html -> /, /name.html -> /name; /name serves name.html)
     - not_found_handling "404-page" (the nearest 404.html with a 404 status)
     - Cloudflare's default Cache-Control: public, max-age=0, must-revalidate, and an ETag
   It is NOT Cloudflare: compression, HTTP/2, the real Content-Type table and edge caching are not
   modelled. Whether the real edge behaves like this is what the preview check on workers.dev settles.

     node tools/serve_dist.mjs DIST [--port 0] [--probe]      serve; prints "listening http://127.0.0.1:PORT"
     node tools/serve_dist.mjs --resolve DIST                 print {path: {header: value}} for every file (JSON)

   --probe adds /__probe/worker.js: a tiny worker served with the headers resolved for /engine/worker.js,
   which tries to fetch an outside address and the AI proxy and reports the results; it proves, in a
   real browser, that a worker under that policy cannot reach either. Node 18 or later, no packages. */
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json', '.csv': 'text/csv; charset=utf-8', '.zip': 'application/zip', '.wasm': 'application/wasm',
  '.whl': 'application/octet-stream', '.pdf': 'application/pdf', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.md': 'text/markdown; charset=utf-8', '.py': 'text/x-python; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8', '.css': 'text/css; charset=utf-8',
};

export function parseHeaders(text) {
  const blocks = []; let cur = null;
  for (const raw of text.split('\n')) {
    if (!raw.trim() || raw.trimStart().startsWith('#')) continue;
    if (!/^[ \t]/.test(raw)) { cur = { pat: raw.trim(), hs: [] }; blocks.push(cur); continue; }
    if (!cur) continue;
    const s = raw.trim();
    if (s.startsWith('!')) { cur.hs.push(['!', s.slice(1).trim()]); continue; }
    const i = s.indexOf(':');
    if (i < 1) continue;
    cur.hs.push([s.slice(0, i).trim(), s.slice(i + 1).trim()]);
  }
  return blocks;
}
function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
export function patternRegex(pat) {
  let rx = '';
  for (const p of pat.split(/(\*|:[A-Za-z]\w*)/)) {
    if (p === '*') rx += '.*';
    else if (p.length > 1 && p[0] === ':') rx += '[^/]+';
    else rx += esc(p);
  }
  return new RegExp('^' + rx + '$');
}
export function resolveHeaders(blocks, p) {
  const out = new Map();
  for (const b of blocks) {
    if (/^https?:\/\//.test(b.pat)) continue;
    if (!patternRegex(b.pat).test(p)) continue;
    for (const [n, v] of b.hs) {
      if (n === '!') { out.delete(v.toLowerCase()); continue; }
      const k = n.toLowerCase();
      if (!out.has(k)) out.set(k, { name: n, vals: [] });
      out.get(k).vals.push(v);
    }
  }
  const o = {};
  for (const { name, vals } of out.values()) o[name] = vals.join(', ');
  return o;
}
export function servedPath(rel) {
  if (rel === 'index.html') return '/';
  if (rel.endsWith('/index.html')) return '/' + rel.slice(0, -'index.html'.length);
  if (rel.endsWith('.html')) return '/' + rel.slice(0, -5);
  return '/' + rel;
}
export function parseRedirects(text) {
  const rules = [];
  for (const raw of text.split('\n')) {
    const s = raw.trim();
    if (!s || s.startsWith('#')) continue;
    const parts = s.split(/\s+/);
    if (parts.length < 2) continue;
    rules.push({ from: parts[0], to: parts[1], code: parts[2] ? Number(parts[2]) : 302 });
  }
  return rules;
}
function matchRedirect(rules, p) {
  for (const r of rules) {
    const names = []; let rx = '';
    for (const part of r.from.split(/(\*|:[A-Za-z]\w*)/)) {
      if (part === '*') { rx += '(.*)'; names.push('splat'); }
      else if (part.length > 1 && part[0] === ':') { rx += '([^/]+)'; names.push(part.slice(1)); }
      else rx += esc(part);
    }
    const m = new RegExp('^' + rx + '$').exec(p);
    if (!m) continue;
    let to = r.to;
    names.forEach((n, i) => { to = to.split(':' + n).join(m[i + 1]); });
    return { to, code: r.code };
  }
  return null;
}
function walk(dir, base = dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, base, out); else out.push(path.relative(base, p).split(path.sep).join('/'));
  }
  return out;
}

export function createServer(dist, opts = {}) {
  const files = new Set(walk(dist));
  const hdrs = files.has('_headers') ? parseHeaders(fs.readFileSync(path.join(dist, '_headers'), 'utf8')) : [];
  const reds = files.has('_redirects') ? parseRedirects(fs.readFileSync(path.join(dist, '_redirects'), 'utf8')) : [];
  const probe = opts.probe ? `
self.onmessage = async (e) => {
  const out = {};
  for (const [k, url] of Object.entries(e.data.targets)) {
    try { await fetch(url, { mode: 'no-cors', cache: 'no-store' }); out[k] = 'reached'; } catch (err) { out[k] = 'blocked: ' + (err && err.name); }
  }
  try { const r = await fetch('/engine/pack.json', { cache: 'no-store' }); out.same_origin = r.ok ? 'reached' : 'status ' + r.status; } catch (err) { out.same_origin = 'blocked: ' + (err && err.name); }
  self.postMessage(out);
};` : null;
  const server = http.createServer((req, res) => {
    let u; try { u = new URL(req.url, 'http://x'); } catch { res.writeHead(400).end(); return; }
    let p = decodeURIComponent(u.pathname);
    const send = (status, extra, bodyPath, bodyText, rel) => {
      const h = Object.assign({}, resolveHeaders(hdrs, p), extra || {});
      const has = (n) => Object.keys(h).some((k) => k.toLowerCase() === n);
      if (!has('cache-control') && bodyPath) h['Cache-Control'] = 'public, max-age=0, must-revalidate';
      let size = 0;
      if (bodyPath) {
        const st = fs.statSync(bodyPath); size = st.size;
        if (!has('content-type')) h['Content-Type'] = TYPES[path.extname(bodyPath).toLowerCase()] || 'application/octet-stream';
        h['ETag'] = '"' + crypto.createHash('sha1').update(rel + ':' + st.size + ':' + st.mtimeMs).digest('hex').slice(0, 16) + '"';
        if (req.headers['if-none-match'] === h['ETag'] && status === 200) { res.writeHead(304, h).end(); return; }
        h['Content-Length'] = size;
      } else if (bodyText !== undefined) {
        if (!has('content-type')) h['Content-Type'] = 'text/plain; charset=utf-8';
        h['Content-Length'] = Buffer.byteLength(bodyText);
      }
      res.writeHead(status, h);
      if (req.method === 'HEAD') { res.end(); return; }
      if (bodyPath) fs.createReadStream(bodyPath).pipe(res); else res.end(bodyText || '');
    };
    if (probe && p === '/__probe/worker.js') {
      const wh = resolveHeaders(hdrs, '/engine/worker.js');
      wh['Content-Type'] = 'text/javascript; charset=utf-8';
      res.writeHead(200, wh); res.end(probe); return;
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, { Allow: 'GET, HEAD' }).end(); return; }
    const rd = matchRedirect(reds, p);
    if (rd) { send(rd.code, { Location: rd.to + (u.search || '') }, null, ''); return; }
    // html handling: auto-trailing-slash
    if (p.endsWith('/index.html')) { send(307, { Location: p.slice(0, -'index.html'.length) + (u.search || '') }, null, ''); return; }
    if (p.endsWith('.html') && files.has(p.slice(1))) { send(307, { Location: p.slice(0, -5) + (u.search || '') }, null, ''); return; }
    let rel = null;
    if (p.endsWith('/')) rel = (p.slice(1) + 'index.html');
    else if (files.has(p.slice(1)) && !p.endsWith('.html')) rel = p.slice(1);
    else if (files.has(p.slice(1) + '.html')) rel = p.slice(1) + '.html';
    else if (files.has(p.slice(1) + '/index.html')) { send(307, { Location: p + '/' + (u.search || '') }, null, ''); return; }
    if (rel && !files.has(rel)) rel = null;
    if (rel && (rel === '_headers' || rel === '_redirects')) rel = null;
    if (rel) { send(200, null, path.join(dist, rel), undefined, rel); return; }
    if (files.has('404.html')) { send(404, null, path.join(dist, '404.html'), undefined, '404.html'); return; }
    send(404, null, null, 'Not found');
  });
  return server;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const args = process.argv.slice(2);
  if (args[0] === '--resolve') {
    const dist = path.resolve(args[1] || 'dist');
    const hdrs = parseHeaders(fs.readFileSync(path.join(dist, '_headers'), 'utf8'));
    const out = {};
    for (const rel of walk(dist)) { if (rel === '_headers' || rel === '_redirects') continue; const p = servedPath(rel); out[p] = resolveHeaders(hdrs, p); }
    process.stdout.write(JSON.stringify(out));
  } else {
    const dist = path.resolve(args.find((a) => !a.startsWith('--')) || 'dist');
    const pi = args.indexOf('--port');
    const port = pi >= 0 ? Number(args[pi + 1]) : 0;
    const srv = createServer(dist, { probe: args.includes('--probe') });
    srv.listen(port, '127.0.0.1', () => console.log('listening http://127.0.0.1:' + srv.address().port));
  }
}
