#!/usr/bin/env node
// The monthly reporting pack's four downloads, made from an engine pack record exactly as the page makes them: the
// page's own src/js/56-xlsx.js (NLX), src/js/46-report-docx.js (NLDocx) and src/js/61-pack.js (NLPack.files) run here in
// a sandbox with a bare `window`. Used by tools/test_nl_pack.py (which reads the workbook back with openpyxl).
//     node tools/pack_files.mjs PACK.json OUT_DIR [YYYY-MM-DD]
// Writes OUT_DIR/<name> for each file and prints a JSON list [{name, bytes}].
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.join(HERE, '..');
const [packPath, outDir, made] = process.argv.slice(2);
if (!packPath || !outDir) { console.error('usage: node tools/pack_files.mjs PACK.json OUT_DIR [YYYY-MM-DD]'); process.exit(2); }
const ctx = { window: {}, TextEncoder, Date, Math, Object, Array, String, Number, isFinite, Uint8Array, JSON, RegExp, Buffer };
vm.createContext(ctx);
for (const f of ['56-xlsx.js', '46-report-docx.js', '61-pack.js']) vm.runInContext(fs.readFileSync(path.join(SITE, 'src/js', f), 'utf8'), ctx, { filename: f });
const P = ctx.window.NLPack;
const pack = JSON.parse(fs.readFileSync(packPath, 'utf8'));
fs.mkdirSync(outDir, { recursive: true });
const files = P.files(pack, made || '2026-10-01');
const out = files.map((f) => { fs.writeFileSync(path.join(outDir, f.name), Buffer.from(f.bytes)); return { name: f.name, bytes: f.bytes.length }; });
const extra = { cover: P.cover(pack, made || '2026-10-01'), workbook_sheets: P.workbook(pack, made).sheets.map((s) => s.name) };
fs.writeFileSync(path.join(outDir, 'specs.json'), JSON.stringify(extra));
console.log(JSON.stringify(out));
