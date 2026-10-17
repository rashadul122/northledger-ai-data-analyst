// The Word write-up (src/js/46-report-docx.js, window.NLDocx), checked as a file a reader opens.
//
//   node tools/check_report_docx.mjs                # build the write-ups from the fixtures and check each
//   node tools/check_report_docx.mjs --keep DIR     # also leave the .docx files (and LibreOffice's PDFs) in DIR
//
// Fixtures: tools/fixtures/report-docx/{sample,orders-private}.json (the engine's own reports, made by
// make_docx_fixtures.py: the results the page keeps for the PDF, the engine's report slice, the withheld values) and
// tools/fixtures/report-pdf/ship2-*-v2.json (an AI-written report and the results it was written from). Cases: the sample
// with a question (report style, Letter), the same in the business style (A4), the AI report, a personal-data file with a
// withheld column and the file's name left off, and a question the file cannot answer.
// Each .docx: the zip's members (content types, package and document relationships, document, styles, numbering,
// settings, header, footer, core and app properties, one PNG per drawn chart) and nothing else; every XML part well formed
// (a strict parser here, and xmllint when installed); every relationship's target in the zip and every r:id / r:embed in
// the document declared; every part's content type; the named styles (Title, Heading1, Heading2, Body, Caption, Table);
// the page size and the style's fonts; the sections in order; every number in the text traceable to the engine's results,
// the engine's report or the report model (the same figure, or it rounded to the digits shown), the one subtraction the
// writer makes (a column's empty cells: rows minus filled), a figure or section number, or the date; each image a PNG,
// drawn in the document at the PNG's aspect ratio, with "Figure n." and a caption naming its units; no withheld value and
// no file name unless asked, in any part; the same bytes twice. The "with the charts" zip: the .docx, one PNG per chart
// and a note naming the source file. With LibreOffice (soffice) installed, each .docx is converted to PDF: it opens, has
// at least 2 pages, and its text holds the sections and figures (and no withheld value).
// Exit 0 when every check passes, 1 otherwise.
import { readFileSync, mkdtempSync, writeFileSync, rmSync, existsSync, mkdirSync } from 'node:fs';
import { execFileSync, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { deflateSync, inflateRawSync } from 'node:zlib';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const SITE = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const PDF = require(path.join(SITE, 'src/js/45-report-pdf.js'));
const D = require(path.join(SITE, 'src/js/46-report-docx.js'));
const SALES = require(path.join(SITE, 'src/js/60-sales.js'));
D.pdf = PDF;
const FX = (f) => JSON.parse(readFileSync(path.join(SITE, 'tools/fixtures', f), 'utf8'));
const keepAt = process.argv.indexOf('--keep') > 0 ? process.argv[process.argv.indexOf('--keep') + 1] : '';

let fails = 0, passes = 0;
const ok = (c, msg) => { if (c) passes++; else { fails++; console.log('  FAIL ' + msg); } };

/* ------------------------------------------------------------ a PNG made here (a real one: the chart's stand-in) */
function crcTab() { const t = []; for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; }
const CT = crcTab();
const crc32 = (b) => { let x = 0xFFFFFFFF; for (const v of b) x = CT[(x ^ v) & 255] ^ (x >>> 8); return (x ^ 0xFFFFFFFF) >>> 0; };
function png(w, h, seed) {
  const chunk = (ty, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(ty), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([l, td, c]); };
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = 8; ih[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = 255 - (x * seed) % 120; raw[o + 1] = 255 - y % 90; raw[o + 2] = 250; }
  return new Uint8Array(Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]));
}

/* ------------------------------------------------------------ the zip, read from its central directory */
function unzip(buf) {
  buf = Buffer.from(buf);
  let e = buf.length - 22; while (e >= 0 && buf.readUInt32LE(e) !== 0x06054b50) e--;
  if (e < 0) throw new Error('no end of central directory');
  const n = buf.readUInt16LE(e + 10), off = buf.readUInt32LE(e + 16), out = new Map();
  let p = off;
  for (let i = 0; i < n; i++) {
    if (buf.readUInt32LE(p) !== 0x02014b50) throw new Error('bad central header ' + i);
    const meth = buf.readUInt16LE(p + 10), crc = buf.readUInt32LE(p + 16), csz = buf.readUInt32LE(p + 20), nl = buf.readUInt16LE(p + 28), xl = buf.readUInt16LE(p + 30), cl = buf.readUInt16LE(p + 32), lo = buf.readUInt32LE(p + 42);
    const name = buf.slice(p + 46, p + 46 + nl).toString('utf8');
    if (buf.readUInt32LE(lo) !== 0x04034b50) throw new Error('bad local header for ' + name);
    const ds = lo + 30 + buf.readUInt16LE(lo + 26) + buf.readUInt16LE(lo + 28);
    let data = buf.slice(ds, ds + csz);
    if (meth === 8) data = inflateRawSync(data); else if (meth !== 0) throw new Error('method ' + meth);
    if (crc32(data) !== crc) throw new Error('crc mismatch for ' + name);
    if (out.has(name)) throw new Error('duplicate member ' + name);
    out.set(name, data);
    p += 46 + nl + xl + cl;
  }
  return out;
}

/* ------------------------------------------------------------ XML: a strict well-formedness check */
function wellFormed(s) {
  let i = 0; const st = [];
  if (s.charCodeAt(0) === 0xFEFF) i = 1;
  const name = /^[A-Za-z_][\w.:-]*/;
  while (i < s.length) {
    const lt = s.indexOf('<', i);
    const text = s.slice(i, lt < 0 ? s.length : lt);
    if (/&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/.test(text)) return 'a bare & in text near ' + JSON.stringify(text.slice(0, 40));
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text)) return 'a control character in text';
    if (lt < 0) break;
    if (s.startsWith('<?', lt)) { const e = s.indexOf('?>', lt); if (e < 0) return 'unclosed <?'; i = e + 2; continue; }
    if (s.startsWith('<!--', lt)) { const e = s.indexOf('-->', lt); if (e < 0) return 'unclosed comment'; i = e + 3; continue; }
    const gt = s.indexOf('>', lt); if (gt < 0) return 'unclosed tag';
    let tag = s.slice(lt + 1, gt);
    if (tag.startsWith('/')) {
      const nm = tag.slice(1).trim(), top = st.pop();
      if (nm !== top) return 'closing </' + nm + '> for <' + top + '>';
    } else {
      const self = tag.endsWith('/'); if (self) tag = tag.slice(0, -1);
      const m = tag.match(name); if (!m) return 'bad tag name in <' + tag.slice(0, 30);
      const attrs = tag.slice(m[0].length), seen = {};
      const re = /\s+([A-Za-z_][\w.:-]*)\s*=\s*("([^"<]*)"|'([^'<]*)')/g; let am, used = 0;
      while ((am = re.exec(attrs))) {
        if (seen[am[1]]) return 'attribute ' + am[1] + ' twice in <' + m[0];
        seen[am[1]] = 1; used += am[0].length;
        if (/&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/.test(am[3] || am[4] || '')) return 'a bare & in an attribute of <' + m[0];
      }
      if (attrs.trim() && used !== attrs.replace(/\s+$/, '').length) return 'unreadable attributes in <' + m[0] + ': ' + attrs.slice(0, 60);
      if (!self) st.push(m[0]);
    }
    i = gt + 1;
  }
  return st.length ? 'unclosed <' + st[st.length - 1] + '>' : '';
}
const XMLLINT = spawnSync('xmllint', ['--version']).status === 0;

/* ------------------------------------------------------------ the text and the numbers */
function paraTexts(xml) {
  return [...xml.matchAll(/<w:p[ >][\s\S]*?<\/w:p>/g)].map((m) => [...m[0].matchAll(/<w:t(?: [^>]*)?>([^<]*)<\/w:t>/g)].map((t) => t[1]).join('')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&')).filter(Boolean);
}
// every number a JSON value holds: its numbers, and every number written in its strings
function numbersOf(x, out) {
  out = out || new Set();
  const walk = (v) => {
    if (typeof v === 'number' && isFinite(v)) out.add(v);
    else if (typeof v === 'string') for (const m of v.matchAll(/\d[\d,]*(?:\.\d+)?/g)) out.add(Number(m[0].replace(/,/g, '')));
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === 'object') Object.values(v).forEach(walk);
  };
  walk(x);
  return out;
}
function tokens(text) {
  // a number as a reader reads it: 3,350 | 54.8 | 2026 (a date's parts are separate tokens)
  return [...text.matchAll(/(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\d])/g)].map((m) => ({ s: m[0], v: Number(m[0].replace(/,/g, '')), dp: m[2] ? m[2].length - 1 : 0, at: m.index }));
}
function traceable(tok, src, extra) {
  if (extra.has(tok.v)) return true;
  for (const v of src) {
    const a = Math.abs(v);
    if (a === tok.v) return true;
    if (Number(a.toFixed(tok.dp)) === tok.v) return true;
    if (tok.dp === 0 && Math.round(a) === tok.v && a >= 10) return true;
  }
  return false;
}

/* ------------------------------------------------------------ one case */
const soffice = (() => { for (const c of ['soffice', '/Applications/LibreOffice.app/Contents/MacOS/soffice']) { try { if (spawnSync(c, ['--version'], { timeout: 60000 }).status === 0) return c; } catch (e) { /* not there */ } } return ''; })();
const TMP = keepAt || mkdtempSync(path.join(process.env.NL_TMP || tmpdir(), 'nl-docx-'));
if (keepAt && !existsSync(keepAt)) mkdirSync(keepAt, { recursive: true });

function runCase(c) {
  console.log('- ' + c.name);
  const inp = c.input;
  const picks = D.pickCharts(Object.assign({}, inp));
  ok(picks.length >= Math.min(2, c.minCharts === undefined ? 2 : c.minCharts) && picks.length <= 3, c.name + ': ' + picks.length + ' charts picked (2 or 3 expected)');
  if (c.firstChart !== undefined) ok(picks[0] && picks[0].index === c.firstChart, c.name + ': the most relevant chart is not chart ' + c.firstChart + ': ' + JSON.stringify(picks.map((p) => [p.index, p.score, p.title])));
  const figs = picks.map((p, i) => Object.assign({}, p, { png: png(680 + i * 40, 280 + i * 30, i + 3), w: 680 + i * 40, h: 280 + i * 30 }));
  const doc = D.writeup(inp, figs, { style: c.style, paper: c.paper });
  const bytes = D.document(doc), again = D.document(D.writeup(inp, figs, { style: c.style, paper: c.paper }));
  ok(Buffer.compare(Buffer.from(bytes), Buffer.from(again)) === 0, c.name + ': the same input made different bytes');
  const file = path.join(TMP, c.name + '.docx');
  writeFileSync(file, bytes);
  let z;
  try { z = unzip(bytes); } catch (e) { ok(false, c.name + ': the zip cannot be read: ' + e.message); return; }
  const need = ['[Content_Types].xml', '_rels/.rels', 'docProps/core.xml', 'docProps/app.xml', 'word/document.xml', 'word/styles.xml', 'word/numbering.xml',
    'word/settings.xml', 'word/header1.xml', 'word/footer1.xml', 'word/_rels/document.xml.rels'];
  need.forEach((n) => ok(z.has(n), c.name + ': no ' + n));
  const media = [...z.keys()].filter((n) => /^word\/media\//.test(n));
  ok(media.length === figs.length, c.name + ': ' + media.length + ' images for ' + figs.length + ' charts');
  ok([...z.keys()].every((n) => need.includes(n) || /^word\/media\/image\d+\.png$/.test(n)), c.name + ': unexpected members: ' + [...z.keys()].filter((n) => !need.includes(n) && !/^word\/media\//.test(n)));
  media.forEach((n) => ok(z.get(n).slice(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])), c.name + ': ' + n + ' is not a PNG'));
  // well-formed XML, every part
  for (const [n, d] of z) {
    if (!/\.(xml|rels)$/.test(n)) continue;
    const s = d.toString('utf8'), bad = wellFormed(s);
    ok(!bad, c.name + ': ' + n + ' is not well formed: ' + bad);
    if (XMLLINT) { const r = spawnSync('xmllint', ['--noout', '-'], { input: s }); ok(r.status === 0, c.name + ': xmllint refuses ' + n + ': ' + String(r.stderr).slice(0, 200)); }
  }
  // content types and relationships
  const types = z.get('[Content_Types].xml').toString(), over = new Set([...types.matchAll(/PartName="\/([^"]+)"/g)].map((m) => m[1])), defs = new Set([...types.matchAll(/Default Extension="(\w+)"/g)].map((m) => m[1]));
  for (const n of z.keys()) if (n !== '[Content_Types].xml') ok(over.has(n) || defs.has(n.split('.').pop()), c.name + ': no content type for ' + n);
  const rels = z.get('word/_rels/document.xml.rels').toString(), relIds = new Map([...rels.matchAll(/Id="(rId\d+)"[^>]*Target="([^"]+)"/g)].map((m) => [m[1], m[2]]));
  for (const [id, t] of relIds) ok(z.has('word/' + t), c.name + ': relationship ' + id + ' targets a missing part ' + t);
  const dx = z.get('word/document.xml').toString();
  for (const m of dx.matchAll(/r:(?:embed|id)="([^"]+)"/g)) ok(relIds.has(m[1]), c.name + ': the document uses an undeclared relationship ' + m[1]);
  for (const m of z.get('_rels/.rels').toString().matchAll(/Target="([^"]+)"/g)) ok(z.has(m[1]), c.name + ': the package relationship targets a missing ' + m[1]);
  const ids = [...dx.matchAll(/<wp:docPr id="(\d+)"/g)].map((m) => m[1]);
  ok(new Set(ids).size === ids.length && ids.length === figs.length, c.name + ': drawing ids ' + ids);
  // styles: the named ones the brief asks for, the style's font
  const sx = z.get('word/styles.xml').toString();
  ['Title', 'Heading1', 'Heading2', 'Body', 'Caption', 'Table'].forEach((s) => ok(new RegExp('w:styleId="' + s + '"').test(sx), c.name + ': no style ' + s));
  ok(new RegExp('w:ascii="' + (c.style === 'business' ? 'Georgia' : 'Arial') + '"').test(sx), c.name + ': the ' + (c.style || 'report') + ' style\'s font is not set');
  const pg = (dx.match(/<w:pgSz w:w="(\d+)" w:h="(\d+)"/) || []).slice(1).join('x');
  ok(pg === (c.paper === 'a4' ? '11906x16838' : '12240x15840'), c.name + ': page size ' + pg);
  // the sections, in order
  const T = paraTexts(dx), all = T.join('\n');
  const heads = ['1. The question', '2. The data as found', '3. What was done', '4. Key findings', '5. Assumptions', '6. Limits of what the data can say', '7. Can the question be answered from this file?', 'How this write-up was made'];
  let last = -1;
  heads.forEach((h) => { const at = T.indexOf(h); ok(at > last, c.name + ': section "' + h + '" missing or out of order'); last = at; });
  ok(/The original file was not changed/.test(all), c.name + ': the write-up does not say the original was not changed');
  // each figure: its title, its picture at the PNG's aspect, a caption with its units
  figs.forEach((f, i) => {
    ok(T.some((t) => t.toLowerCase().startsWith(('Figure ' + (i + 1) + '. ' + f.title.slice(0, 20)).toLowerCase())), c.name + ': no "Figure ' + (i + 1) + '." title');
  });
  const ext = [...dx.matchAll(/<wp:extent cx="(\d+)" cy="(\d+)"/g)].map((m) => Number(m[1]) / Number(m[2]));
  ext.forEach((r, i) => ok(Math.abs(r - figs[i].w / figs[i].h) < 0.01, c.name + ': figure ' + (i + 1) + ' drawn at aspect ' + r.toFixed(3) + ', its PNG is ' + (figs[i].w / figs[i].h).toFixed(3)));
  ok(T.filter((t) => /^Units: /.test(t)).length >= figs.filter((f) => f.units).length, c.name + ': a figure caption lacks its units');
  // assumptions: every one labelled
  const asm = T.slice(T.indexOf('5. Assumptions') + 1, T.indexOf('6. Limits of what the data can say'));
  ok(asm.length >= 1 && asm.filter((t) => !/^Not assumed:/.test(t)).every((t) => /^Assumption \([^)]+\): /.test(t) || /^The engine made no assumption/.test(t)), c.name + ': an assumption is not labelled: ' + JSON.stringify(asm.slice(0, 4)));
  // numbers: each one in the text traced to its source
  const model = PDF.model(inp);
  const src = numbersOf([inp.results, inp.engine, model.kpis, model.notice, model.parts, inp.goal, inp.repaired]);
  const extra = new Set(heads.map((h, i) => i + 1).concat(figs.map((f, i) => i + 1)));
  const day = doc.created; [day.getFullYear(), day.getMonth() + 1, day.getDate()].forEach((v) => extra.add(v));
  // the one subtraction the writer makes: a column's empty cells (its rows minus its filled cells)
  ((inp.engine && inp.engine.health && inp.engine.health.columns) || []).forEach((col) => { if (col.completeness) extra.add(col.completeness.n - col.completeness.k); });
  const loose = [];
  T.forEach((t) => tokens(t).forEach((k) => { if (!traceable(k, src, extra)) loose.push(k.s + ' in "' + t.slice(Math.max(0, k.at - 40), k.at + 30) + '"'); }));
  ok(!loose.length, c.name + ': ' + loose.length + ' number(s) not from the engine or the model: ' + loose.slice(0, 6).join(' | '));
  // the key figures are the model's (one model, two renderers)
  (model.kpis || []).forEach((k) => ok(all.indexOf(String(k.value)) >= 0, c.name + ': the model\'s key figure ' + k.value + ' is not in the write-up'));
  if (inp.report) ok(T[0] === model.title, c.name + ': the title is not the report model\'s: ' + T[0]);
  // privacy
  const every = [...z.values()].map((d) => d.toString('latin1')).join('\n') + '\n' + [...z.entries()].filter(([n]) => /\.xml$/.test(n)).map(([, d]) => d.toString('utf8')).join('\n');
  (c.forbid || []).forEach((v) => ok(every.indexOf(v) < 0, c.name + ': a withheld value is in the file: ' + v));
  if (!inp.showName && inp.name) ok(every.indexOf(inp.name) < 0, c.name + ': the file name is in the file without the reader asking');
  if (inp.showName && inp.name) ok(all.indexOf(inp.name) >= 0, c.name + ': the write-up does not name its source file');
  (c.expect || []).forEach((rx) => ok(rx.test(all), c.name + ': the text lacks ' + rx));
  (c.refuse || []).forEach((rx) => ok(!rx.test(all), c.name + ': the text holds ' + rx));
  // the zip with the charts
  const bz = unzip(D.bundle({ docx: bytes, docxName: 'w.docx', figures: figs, sourceName: inp.showName ? inp.name : '' }));
  ok(bz.has('w.docx') && [...bz.keys()].filter((n) => /^figure-\d+-.*\.png$/.test(n)).length === figs.length && bz.has('README.txt'), c.name + ': the bundle lacks a part: ' + [...bz.keys()]);
  const readme = bz.get('README.txt').toString();
  ok(/cannot save next to your data file/.test(readme) && (inp.showName ? readme.indexOf(inp.name) >= 0 : readme.indexOf(inp.name || '\u0000') < 0), c.name + ': the bundle note does not name the source as asked');
  // LibreOffice: it opens, and its text holds the sections
  if (soffice) {
    const r = spawnSync(soffice, ['--headless', '--convert-to', 'pdf', '--outdir', TMP, file], { timeout: 180000, encoding: 'utf8' });
    const pdf = file.replace(/\.docx$/, '.pdf');
    ok(r.status === 0 && existsSync(pdf), c.name + ': LibreOffice could not convert it: ' + (r.stderr || r.stdout || '').slice(0, 200));
    if (existsSync(pdf)) {
      const pages = Number((execFileSync('pdfinfo', [pdf]).toString().match(/Pages:\s+(\d+)/) || [])[1] || 0);
      const txt = execFileSync('pdftotext', ['-layout', pdf, '-']).toString();
      ok(pages >= 2, c.name + ': LibreOffice laid it out on ' + pages + ' page(s)');
      ['The question', 'The data as found', 'Key findings', 'Assumptions', 'Limits of what the data can say', 'Figure 1.'].forEach((h) => ok(txt.indexOf(h) >= 0, c.name + ': LibreOffice\'s PDF lacks "' + h + '"'));
      ok(/Page 1 of \d+/.test(txt), c.name + ': LibreOffice\'s PDF lacks the "Page 1 of N" footer');
      (c.forbid || []).forEach((v) => ok(txt.indexOf(v) < 0, c.name + ': LibreOffice\'s PDF shows a withheld value: ' + v));
      console.log('  ' + pages + ' pages in LibreOffice');
    }
  }
}

/* ------------------------------------------------------------ the cases */
const sample = FX('report-docx/sample.json'), priv = FX('report-docx/orders-private.json');
const ship = FX('report-pdf/ship2-results-v2.json').results, shipR = FX('report-pdf/ship2-response-v2.json');
const DAY = new Date('2026-10-16T15:00:00Z');
const base = (o) => Object.assign({ report: '', sources: [], date: DAY }, o);
// a question the file cannot answer: every business claim too thin to judge, the engine's answer and charts gone
const thin = JSON.parse(JSON.stringify(sample));
thin.results.findings.forEach((f) => { if (f.kind === 'business') { f.verdict = 'INSUFFICIENT'; f.why = 'Needs at least 24 months of history; the file has 9.'; } });
delete thin.results.answer; thin.engine.answer = null;
thin.engine.story.cannot_answer = ['The file has no column for the vacancy rate, so the question about vacancies cannot be answered.'];
thin.results.charts = thin.results.charts.slice(0, 2);
const cases = [
  { name: 'sample-report-letter', input: base({ results: sample.results, engine: sample.engine, goal: 'Is rent up on the year before, and why?', name: 'sample-messy.csv', showName: true }),
    firstChart: 0, expect: [/The engine's answer/, /Missing values by column/, /Rows set aside, and why/, /Exact duplicate row/, /Assumption \(reading: flow or level\)/, /Assumption \(scenario\)/, /sample-messy\.csv/, /written by the engine without the AI/] },
  { name: 'sample-business-a4', style: 'business', paper: 'a4', input: base({ results: sample.results, engine: sample.engine, goal: 'Is rent up on the year before, and why?', name: 'sample-messy.csv', showName: false }),
    refuse: [/sample-messy/] },
  { name: 'ship2-ai-report', input: base({ report: shipR.report, sources: shipR.sources, model: shipR.model, repaired: shipR.repaired, removed_figures: shipR.removed_figures, results: ship, kept: [], name: 'ship2-orders.csv', showName: false }),
    expect: [/The findings and their grades/, /per-column count of missing values was not kept/, /An AI model/], refuse: [/written by the engine without the AI/] },
  { name: 'orders-private', input: base({ results: priv.results, engine: priv.engine, goal: 'How did spend change?', kept: ['staff_name'], name: 'orders-private.csv', showName: false }),
    forbid: priv.forbid, expect: [/notes \(withheld: no value shown\)/] },
  { name: 'cannot-answer', minCharts: 2, input: base({ results: thin.results, engine: thin.engine, goal: 'Are vacancies rising?', name: 'sample-messy.csv', showName: false }),
    expect: [/Not from this file/, /question cannot be answered from this file/, /What data would answer it/, /no column for the vacancy rate/, /Needs at least 24 months/] }
];
cases.forEach(runCase);

/* ------------------------------------------------------------ the quarterly sales report (Track F, src/js/60-sales.js)
   NLSales.doc(rep.sales) through the same writer in the business style: the parts, the serif, the one-page executive
   summary first (a page break after it), the sections in order, one picture per chart at its aspect, every number in the
   text one the engine wrote in its record (or a figure / section number or the date), no rep code, and LibreOffice opens it.
   Fixture: tools/fixtures/report-docx/sales.json (the SYNTHETIC CRM sample's rep.sales, owner coded; and with a fiscal
   year from April, a target and '$'). */
function runSales(name, rec) {
  console.log('- ' + name);
  const doc = SALES.doc(rec, { paper: 'letter', created: DAY, source: 'your CRM export' });
  const imgs = doc.blocks.filter((b) => b.type === 'image');
  imgs.forEach((b, i) => { b.w = 680; b.h = 260 + i * 20; b.png = png(b.w, b.h, i + 2); });
  ok(imgs.length === rec.charts.length, name + ': ' + imgs.length + ' figures for ' + rec.charts.length + ' charts');
  const bytes = D.document(doc);
  ok(Buffer.compare(Buffer.from(bytes), Buffer.from(D.document(doc))) === 0, name + ': the same input made different bytes');
  const z = unzip(bytes), dx = z.get('word/document.xml').toString(), sx = z.get('word/styles.xml').toString();
  for (const [n, d] of z) if (/\.(xml|rels)$/.test(n)) { const bad = wellFormed(d.toString('utf8')); ok(!bad, name + ': ' + n + ' is not well formed: ' + bad); }
  ok(/w:ascii="Georgia"/.test(sx) && /<w:sz w:val="20"\/>/.test(sx), name + ': not the business style (Georgia, 10 pt)');
  ok([...z.keys()].filter((n) => /^word\/media\/image\d+\.png$/.test(n)).length === imgs.length, name + ': not one PNG per chart');
  const ext = [...dx.matchAll(/<wp:extent cx="(\d+)" cy="(\d+)"/g)].map((m) => Number(m[1]) / Number(m[2]));
  ext.forEach((r, i) => ok(Math.abs(r - imgs[i].w / imgs[i].h) < 0.01, name + ': figure ' + (i + 1) + ' is drawn at the wrong aspect'));
  const T = paraTexts(dx), all = T.join('\n');
  const heads = ['Executive summary', '1. Won revenue: more deals or bigger deals?', '2. By segment', '3. Sales cycle', '4. Pipeline on ' + rec.pipeline.as_of,
    '5. Top reps in ' + rec.quarter.label, '6. What is working and what needs to change', 'How this report was made'];
  let last = -1;
  heads.forEach((h) => { const at = T.indexOf(h); ok(at > last, name + ': section "' + h + '" missing or out of order'); last = at; });
  const brk = dx.indexOf('w:type="page"');
  ok(brk > dx.indexOf('Executive summary') && brk < dx.indexOf('1. Won revenue'), name + ': the executive summary is not a page of its own');
  ok(all.indexOf(rec.summary.headline) >= 0 && all.indexOf(rec.growth.text) >= 0, name + ': the engine\'s headline or growth sentence is missing');
  // every number in the text is one the engine wrote (its record's strings and values), a figure or section number, or the date
  const src = new Set();
  const walk = (v) => { if (v === null || v === undefined) return; if (typeof v === 'number') src.add(String(v)); else if (typeof v === 'string') (v.match(/\d[\d,]*(?:\.\d+)?/g) || []).forEach((x) => src.add(x.replace(/,/g, ''))); else if (typeof v === 'object') Object.values(v).forEach(walk); };
  walk(rec);
  const extra = new Set(['1', '2', '3', '4', '5', '6', String(DAY.getFullYear())]);
  const loose = [];
  T.forEach((t) => (t.match(/\d[\d,]*(?:\.\d+)?/g) || []).forEach((x) => { const k = x.replace(/,/g, '').replace(/\.$/, ''); if (!src.has(k) && !extra.has(k)) loose.push(x + ' in "' + t.slice(0, 60) + '"'); }));
  ok(!loose.length, name + ': ' + loose.length + ' number(s) not from the engine: ' + loose.slice(0, 5).join(' | '));
  ok(!/rdc_/.test(all) && /Rep A/.test(all), name + ': a coded rep is shown by its code, or not as Rep A');
  ok(/driver to be confirmed/.test(all), name + ': no "driver to be confirmed"');
  if (soffice) {
    const file = path.join(TMP, name + '.docx'); writeFileSync(file, bytes);
    const r = spawnSync(soffice, ['--headless', '--convert-to', 'pdf', '--outdir', TMP, file], { timeout: 180000, encoding: 'utf8' });
    const pdf = file.replace(/\.docx$/, '.pdf');
    ok(r.status === 0 && existsSync(pdf), name + ': LibreOffice could not convert it');
    if (existsSync(pdf)) {
      const pages = Number((execFileSync('pdfinfo', [pdf]).toString().match(/Pages:\s+(\d+)/) || [])[1] || 0);
      const txt = execFileSync('pdftotext', ['-layout', pdf, '-']).toString(), p1 = txt.split('\f')[0];
      ok(pages >= 3 && /Executive summary/.test(p1) && !/1\. Won revenue: more deals/.test(p1) && /Page 1 of \d+/.test(txt), name + ': LibreOffice\'s layout: ' + pages + ' pages, or the summary is not page 1 alone');
      console.log('  ' + pages + ' pages in LibreOffice');
    }
  }
}
const salesFx = FX('report-docx/sales.json');
runSales('sales-report-coded', salesFx.coded);
runSales('sales-report-fiscal-target', salesFx.fiscal);
if (!soffice) console.log('  (LibreOffice is not installed: the conversion checks did not run)');
if (!keepAt) rmSync(TMP, { recursive: true, force: true });
console.log((fails ? 'FAIL' : 'OK') + ': ' + passes + ' checks passed, ' + fails + ' failed (' + cases.length + ' write-ups and 2 sales reports' + (soffice ? ', each opened in LibreOffice' : '') + ')');
process.exit(fails ? 1 : 0);
