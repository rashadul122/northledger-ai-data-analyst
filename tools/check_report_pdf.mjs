// The AI report's PDF (src/js/45-report-pdf.js, window.NLReportPdf), checked as a file a reader opens.
//
//   node tools/check_report_pdf.mjs                          # build from tools/fixtures/report-pdf and check each
//   node tools/check_report_pdf.mjs FILE.pdf [--a4] [--forbid LIST.txt] [--name STEM] [--kept COL]
//                                                            # check any PDF (the old worker's, a download)
//   NL_REPORT_PDF_WRITER=path/to/writer.js node tools/check_report_pdf.mjs
//                                                            # the same fixtures through another copy of the writer
//                                                            # (an older one, to see a check fail before its fix)
//   node tools/check_report_pdf.mjs --hashes OUT.json [--same-as BEFORE.json]
//                                                            # also the sha256 of every fixture PDF (Letter and A4,
//                                                            # each at a fixed date); --same-as fails unless each is
//                                                            # byte-identical to BEFORE.json's (a change that must not
//                                                            # move a byte: run once through the old writer with
//                                                            # NL_REPORT_PDF_WRITER, once through the new, same TZ)
//
// Structure, in pure Node: the header; every xref offset lands on its object; the trailer names Root and Info;
// the Info has a title, an author and a date; the catalog has /Lang and shows the title (/DisplayDocTitle);
// bookmarks; the page size (Letter 612 x 792 pt, A4 595.28 x 841.89 pt); "Page i of N" on every page and the
// running header on every page but the cover; the sections in the fixed order (Contents, Executive summary,
// Parts 1 to 5, Appendix A, Appendix B); every reference a clickable http(s) link and every internal link a
// destination that exists; every citation [n] in the text names a reference that is listed; only the standard
// Helvetica fonts, none embedded; no filled shape off the page; no "??" the report's own text does not hold (a name
// in a script WinAnsi lacks printed as question marks: final review, 30 Sep 2026); a number with a unit in a table
// ("58,453 CAD") right-aligned in its column.
// Geometry, with an independent oracle: poppler's `pdftotext -bbox` measures every word with its own font
// metrics: no word may sit outside the 60 pt side margins or above the header and below the footer, and no two
// words may overlap (final review, 30 Sep 2026: a cover with many kept columns printed its contents list over the
// notice, inside the margins).
// Privacy: no value of a withheld column (the forbid list) and not the visitor's file name anywhere in the text,
// the document info, the bookmarks or the link addresses, unless they opted in to the name; and the cover says
// so when they sent personal columns (naming each, or as many as fit and how many more, with all named in the file).
// With no file named, the checks run on PDFs the canonical writer makes from the fixtures: the ship2 report in the
// new sections and in the sections of 29 Sep 2026, Letter and A4, with and without the opt-ins; and the final
// review's cases (review4.json): three currencies (f3 EUR), a CONFIRMED total whose derived figures carry their
// parent's grade (f9c), region names in Cyrillic and Bengali (f10), an average headline the adapter will not break
// down (FX rates), 16 references and one citation of a source not returned, headings the writer's own names do not
// use, a cover with 14 kept columns, and a share link's copy with and without the engine's trimmed results. The same
// bytes must come out of the writer run as a classic script (as the page runs it) and as a Node module.
// The integration pass (30 Sep 2026) adds: the key figures lead with the adapter's primary claim (results.primary: f3's
// EUR total, never its rows; the FX average); the engine's "Where the change in <measure> came from" table in Part 1;
// a share's trimmed results keep each item's value, parent grade and grade words, and a tighter byte cap; and a shared
// copy that carries only the worker's key_figures leads with them.
// Exit 0 when every check passes, 1 otherwise. Needs poppler (pdftotext) for the geometry: without it that check
// FAILS (it is the only independent measure of the widths).
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.dirname(HERE);
const CANON = path.join(SITE, 'src', 'js', '45-report-pdf.js');
const WRITER = process.env.NL_REPORT_PDF_WRITER ? path.resolve(process.env.NL_REPORT_PDF_WRITER) : CANON;
const FX = path.join(HERE, 'fixtures', 'report-pdf');
const LETTER = [612, 792], A4 = [595.28, 841.89];
const ORDER = ['CONTENTS', 'EXECUTIVE SUMMARY', 'PART 1', 'PART 2', 'PART 3', 'PART 4', 'PART 5', 'APPENDIX A', 'APPENDIX B'];
const WIN = { 0x80: 0x20AC, 0x85: 0x2026, 0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201C, 0x94: 0x201D, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014 };
const requireCjs = createRequire(import.meta.url);
const METRICS = requireCjs(CANON);        // the Helvetica AFM widths, for where a right-aligned cell ends

// the words of a PDF as a reader's search would meet them: typographic quotes and dashes read as plain ones
const plainText = (s) => String(s).replace(/[\u2018\u2019]/g, '\'').replace(/[\u201c\u201d]/g, '"').replace(/[\u2013\u2014\u2212]/g, '-').replace(/\u00a0/g, ' ').toLowerCase();
// a table cell that is a number with a unit after it ("58,453 CAD", "+6.87 percentage points")
const NUM_UNIT = /^[-+\u2212\u2013]?[$\u00a3\u20ac\u00a5]?[\d,]*\.?\d+(%|x)?( to [-+\u2212\u2013]?[$\u00a3\u20ac\u00a5]?[\d,]*\.?\d+%?)?( [A-Za-z%]{1,16}){1,3}$/;

export function checkPdf(bytes, o = {}) {
  const s = Buffer.from(bytes).toString('latin1');
  const fails = [], notes = [];
  const fail = (m) => fails.push(m);
  // 1. the file skeleton
  if (!s.startsWith('%PDF-1.')) fail('no %PDF header');
  const sx = Number((s.match(/startxref\s+(\d+)\s+%%EOF\s*$/) || [])[1]);
  const xm = isFinite(sx) ? s.slice(sx).match(/^xref\s+0 (\d+)\s+([\s\S]*?)trailer\s*<<([\s\S]*?)>>\s*startxref/) : null;
  if (!xm) fail('startxref does not point at an xref table');
  else {
    const rows = xm[2].trim().split(/\n/).slice(1);
    if (rows.length !== Number(xm[1]) - 1) fail('the xref table lists ' + rows.length + ' objects but says ' + (Number(xm[1]) - 1));
    rows.forEach((r, i) => { const off = Number(r.slice(0, 10)); if (!s.startsWith((i + 1) + ' 0 obj', off)) fail('xref entry ' + (i + 1) + ' points at offset ' + off + ', which is not "' + (i + 1) + ' 0 obj"'); });
    if (!/\/Info\s+\d+ 0 R/.test(xm[3])) fail('the trailer has no /Info (no document metadata)');
    if (!/\/Root\s+\d+ 0 R/.test(xm[3])) fail('the trailer has no /Root');
  }
  const obj = (n) => { const m = s.match(new RegExp('(?:^|\\n)' + n + ' 0 obj\\n([\\s\\S]*?)\\nendobj')); return m ? m[1] : ''; };
  const dec = (v) => {
    if (!v) return '';
    if (v[0] === '<') { const h = v.slice(1, -1).replace(/^FEFF/i, ''); let out = ''; for (let i = 0; i < h.length; i += 4) out += String.fromCharCode(parseInt(h.slice(i, i + 4), 16)); return out; }
    return v.slice(1, -1);
  };
  const info = obj((s.match(/trailer\s*<<[\s\S]*?\/Info\s+(\d+) 0 R/) || [])[1]);
  const infoGet = (k) => dec((info.match(new RegExp('/' + k + '\\s*(<[0-9A-Fa-f]+>|\\((?:[^()\\\\]|\\\\.)*\\))')) || [])[1]);
  for (const k of ['Title', 'Author', 'CreationDate']) if (!infoGet(k)) fail('the document info has no /' + k);
  const catalog = obj((s.match(/\/Root\s+(\d+) 0 R/) || [])[1]);
  if (!/\/Lang\s*\(en-CA\)/.test(catalog)) fail('the catalog has no /Lang (en-CA): a screen reader guesses the language');
  if (!/\/DisplayDocTitle\s+true/.test(catalog)) fail('the viewer shows the file name, not the title (/DisplayDocTitle)');
  if (!/\/Outlines\s+\d+ 0 R/.test(catalog)) fail('no bookmarks (/Outlines)');
  // 2. fonts: the standard Helvetica family only, never embedded
  const fonts = [...s.matchAll(/\/Type \/Font \/Subtype \/(\w+) \/BaseFont \/([\w-]+)/g)].map((m) => m[1] + ' ' + m[2]);
  const okFonts = ['Type1 Helvetica', 'Type1 Helvetica-Bold', 'Type1 Helvetica-Oblique'];
  if (!fonts.length || fonts.some((f) => okFonts.indexOf(f) < 0)) fail('fonts other than the standard Helvetica family: ' + fonts.join(', '));
  if (/\/FontFile\d?\s/.test(s)) fail('a font is embedded');
  // 3. pages, their size, their text in stream order
  const pageObjs = [...s.matchAll(/(\d+) 0 obj\n<< \/Type \/Page \/Parent[^]*?endobj/g)];
  const sizes = [...new Set(pageObjs.map((m) => (m[0].match(/\/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]/) || []).slice(1).join('x')))];
  const media = (sizes[0] || '0x0').split('x').map(Number);
  const want = o.a4 ? A4 : LETTER;
  if (sizes.length !== 1 || Math.abs(media[0] - want[0]) > 0.5 || Math.abs(media[1] - want[1]) > 0.5) fail('page size ' + sizes.join(', ') + ' pt, wanted ' + want.join('x'));
  const unesc = (t) => t.replace(/\\([()\\])/g, '$1').replace(/[\x80-\x9f]/g, (c) => String.fromCharCode(WIN[c.charCodeAt(0)] || 63));
  const pages = pageObjs.map((m) => {
    const c = (m[0].match(/\/Contents (\d+) 0 R/) || [])[1];
    const body = obj(c), st = body.slice(body.indexOf('stream\n') + 7, body.lastIndexOf('\nendstream'));
    const len = Number((body.match(/\/Length (\d+)/) || [])[1]);
    if (len !== st.length) fail('page ' + (pageObjs.indexOf(m) + 1) + ': the content stream is ' + st.length + ' bytes but says ' + len);
    const texts = [...st.matchAll(/BT \/(F\d) ([\d.]+) Tf (?:([-\d.]+) Tc )?(?:([\d.]+ [\d.]+ [\d.]+) rg )?([-\d.]+) ([-\d.]+) Td \(((?:[^()\\]|\\.)*)\) Tj ET/g)]
      .map((t) => ({ f: t[1], size: +t[2], tc: +(t[3] || 0), rgb: t[4] || '', x: +t[5], y: +t[6], raw: t[7].replace(/\\([()\\])/g, '$1'), s: unesc(t[7]) }));
    const annots = ((m[0].match(/\/Annots \[([^\]]*)\]/) || [])[1] || '').match(/\d+ 0 R/g) || [];
    return { texts, annots: annots.map((r) => obj(r.split(' ')[0])), stream: st, id: m[1] };
  });
  const N = pages.length;
  notes.push(N + ' pages');
  if (!N) fail('no pages');
  // 4. "Page i of N" on every page; the running header on every page but the cover
  pages.forEach((p, i) => {
    if (!p.texts.some((t) => t.s === 'Page ' + (i + 1) + ' of ' + N)) fail('page ' + (i + 1) + ' has no "Page ' + (i + 1) + ' of ' + N + '"');
    if (i > 0 && !p.texts.some((t) => /^NORTHLEDGER INSIGHTS . DATA REPORT$/.test(t.s))) fail('page ' + (i + 1) + ' has no running header');
  });
  // 5. the reading order: the sections in the order the design fixes
  const all = pages.map((p) => p.texts.map((t) => t.s).join(' ')).join(' ');
  let at = -1;
  for (const w of ORDER) { const k = all.indexOf(w, at + 1); if (k < 0) { fail('the section "' + w + '" is missing or out of order'); break; } at = k; }
  // 6. links: every reference a live http(s) address; every internal link lands on a page of this file
  const pageIds = new Set(pages.map((p) => p.id));
  const uris = [], dests = [];
  pages.forEach((p) => p.annots.forEach((a) => {
    const u = (a.match(/\/URI \(([^)]*)\)/) || [])[1];
    if (u !== undefined) uris.push(u);
    const d = (a.match(/\/Dest \[(\d+) 0 R/) || [])[1];
    if (d !== undefined) dests.push(d);
  }));
  notes.push(uris.length + ' address links, ' + dests.length + ' internal links');
  if (o.refs !== false && !uris.length) fail('no reference is a clickable link');
  const badUri = uris.filter((u) => !/^https?:\/\/[^\s]+$/.test(u));
  if (badUri.length) fail('a link is not an http(s) address: ' + badUri[0].slice(0, 80));
  const badDest = dests.filter((d) => !pageIds.has(d));
  if (badDest.length) fail(badDest.length + ' internal links point at no page');
  for (const m of s.matchAll(/\/Dest \[(\d+) 0 R/g)) if (!pageIds.has(m[1])) { fail('a bookmark or link points at object ' + m[1] + ', not a page'); break; }
  // 6b. every citation [n] in the text is a reference listed in Appendix B (a citation is drawn on its own, linked)
  const cites = [], refNums = new Set();
  let inRefs = false;
  pages.forEach((p) => p.texts.forEach((t) => {
    if (/^APPENDIX B$/.test(t.s)) inRefs = true;
    const m = t.s.match(/^\[(\d+)\]$/);
    if (m) (inRefs ? refNums.add(Number(m[1])) : cites.push(Number(m[1])));
  }));
  const loose = [...new Set(cites.filter((n) => !refNums.has(n)))];
  notes.push(cites.length + ' citations, ' + refNums.size + ' references');
  if (loose.length) fail('citations with no listed reference: [' + loose.slice(0, 6).join('], [') + ']');
  // 6c. no "??": a character WinAnsi lacks must never print as question marks (a name gets a placeholder)
  const qs = [...new Set((all.match(/\?{2,}/g) || []).filter((q) => !(o.source && String(o.source).indexOf(q) >= 0)))];
  if (qs.length) {
    const k = all.search(/\?{2,}/);
    fail('question marks stand in for text the PDF cannot show: "' + all.slice(Math.max(0, k - 40), k + 20) + '"');
  }
  // 6d. a number with a unit in a table is right-aligned: cells that start at the same x but end at different x are
  // left-aligned (a column of text starting there too is a text column, and not judged)
  pages.forEach((p, i) => {
    const cells = p.texts.filter((t) => t.f === 'F1' && t.size === 8.3);
    const groups = {};
    cells.forEach((t) => { const k = t.x.toFixed(1); (groups[k] = groups[k] || []).push(t); });
    Object.keys(groups).forEach((k) => {
      const g = groups[k], nu = g.filter((t) => NUM_UNIT.test(t.s.trim()));
      if (nu.length < 2 || nu.length < g.length) return;
      const ends = nu.map((t) => t.x + METRICS._tw(t.raw, t.size, 'R'));
      if (Math.max(...ends) - Math.min(...ends) > 1) fail('page ' + (i + 1) + ': numbers with units are left-aligned in a table ("' + nu.slice(0, 3).map((t) => t.s).join('", "') + '")');
    });
  });
  // 7. geometry, from an independent oracle: poppler measures every word with its own font metrics
  if (o.file) {
    const L = 60, R = media[0] - 60, TOPM = 36, BOTM = 30;
    let bb = null;
    try { bb = execFileSync('pdftotext', ['-bbox', o.file, '-'], { encoding: 'utf8', maxBuffer: 64 << 20 }); } catch (e) { fail('pdftotext (poppler) could not measure the words: ' + String(e.message || e).split('\n')[0]); }
    if (bb) {
      let over = 0, words = 0, clash = 0;
      const sample = [], clashes = [];
      bb.split('<page ').slice(1).forEach((pg, i) => {
        const ws = [];
        for (const m of pg.matchAll(/<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<\/word>/g)) {
          words++;
          const [x1, y1, x2, y2] = [+m[1], +m[2], +m[3], +m[4]];
          ws.push({ x1, y1, x2, y2, w: m[5] });
          if (x1 < L - 1.5 || x2 > R + 1.5 || y1 < TOPM - 2 || y2 > media[1] - BOTM + 2) { over++; if (sample.length < 4) sample.push('p' + (i + 1) + ' "' + m[5] + '" x ' + x1.toFixed(0) + '..' + x2.toFixed(0) + ' y ' + y1.toFixed(0) + '..' + y2.toFixed(0)); }
        }
        // two words overlap when they share more than a point across and a third of the smaller's height
        ws.sort((a, b) => a.y1 - b.y1);
        for (let a = 0; a < ws.length; a++) {
          for (let b = a + 1; b < ws.length && ws[b].y1 < ws[a].y2; b++) {
            const A = ws[a], B = ws[b];
            const ox = Math.min(A.x2, B.x2) - Math.max(A.x1, B.x1), oy = Math.min(A.y2, B.y2) - Math.max(A.y1, B.y1);
            if (ox > 1 && oy > 0.34 * Math.min(A.y2 - A.y1, B.y2 - B.y1)) { clash++; if (clashes.length < 4) clashes.push('p' + (i + 1) + ' "' + A.w + '" over "' + B.w + '" at x ' + Math.max(A.x1, B.x1).toFixed(0) + ' y ' + Math.max(A.y1, B.y1).toFixed(0)); }
          }
        }
      });
      notes.push(words + ' words measured by poppler');
      if (over) fail(over + ' words outside the text area (poppler bbox): ' + sample.join('; '));
      if (clash) fail(clash + ' pairs of words overlap (poppler bbox): ' + clashes.join('; '));
    }
  }
  // 8. no filled or stroked rectangle off the page (a bar below the page edge is a cut figure)
  pages.forEach((p, i) => {
    for (const r of p.stream.matchAll(/([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) re/g)) {
      const x = +r[1], y = +r[2], w = +r[3], h = +r[4];
      if (Math.min(y, y + h) < -0.5 || Math.max(y, y + h) > media[1] + 0.5 || Math.min(x, x + w) < -0.5 || Math.max(x, x + w) > media[0] + 0.5) { fail('page ' + (i + 1) + ': a shape runs off the page (x ' + x.toFixed(0) + ', y ' + y.toFixed(0) + ', w ' + w.toFixed(0) + ', h ' + h.toFixed(0) + ')'); break; }
    }
    if (/NaN|Infinity|undefined/.test(p.stream)) fail('page ' + (i + 1) + ': the content stream holds NaN, Infinity or undefined');
  });
  // 9. privacy: what the reader did not agree to show is nowhere in the file
  const hexes = [...s.matchAll(/<(FEFF[0-9A-Fa-f]*)>/g)].map((m) => dec('<' + m[1] + '>')).join(' ');
  const hay = plainText(all + ' ' + hexes + ' ' + uris.join(' '));
  if (o.forbid) {
    const list = o.forbid.filter((x) => x.length >= 3);
    const hits = list.filter((w) => new RegExp('(^|[^a-z0-9])' + plainText(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![a-z0-9])').test(hay));
    notes.push(list.length + ' withheld values looked for');
    if (hits.length) fail(hits.length + ' withheld values in the PDF, for example ' + hits.slice(0, 3).join(', '));
  }
  if (o.name) {
    const has = hay.indexOf(plainText(o.name)) >= 0;
    if (o.nameAllowed && !has) fail('the reader opted in to their file name, but "' + o.name + '" is not in the PDF');
    if (!o.nameAllowed && has) fail('the file name "' + o.name + '" is in the PDF although the reader did not opt in');
  }
  if (o.kept) {
    // the cover names every kept column, or as many as fit and how many more; every one is named in the file
    const cover = pages[0] ? plainText(pages[0].texts.map((t) => t.s).join(' ')) : '';
    const onCover = o.kept.filter((c) => cover.indexOf(plainText(c)) >= 0).length;
    const more = (cover.match(/and (\d+) more, all named in appendix a/) || [])[1];
    if (cover.indexOf('personal columns') < 0 || !onCover || (onCover < o.kept.length && Number(more) !== o.kept.length - onCover)) fail('the cover does not say which personal columns the reader sent to the AI (' + o.kept.join(', ') + ')');
    const missing = o.kept.filter((c) => hay.indexOf(plainText(c)) < 0);
    if (missing.length) fail('kept columns named nowhere in the PDF: ' + missing.join(', '));
  }
  if (typeof o.removed === 'number') {
    const want = o.removed === 0 ? 'removed no sentence' : 'removed ' + o.removed + ' sentence';
    if (hay.indexOf(want) < 0) fail('the notice does not say the honesty check ' + want + (o.removed > 1 ? 's' : ''));
  }
  return { ok: !fails.length, fails, notes, pages: N, text: all, hay, texts: pages.map((p) => p.texts) };
}

function report(label, r) {
  console.log((r.ok ? 'PDF PASS ' : 'PDF FAIL ') + label + ' (' + r.notes.join('; ') + ')');
  r.fails.forEach((f) => console.log('  - ' + f));
  return r.ok;
}

// the text between two section markers (the kicker words drawn in capitals), as a reader's search meets it
function between(r, a, b) {
  const t = r.text, i = t.indexOf(a), j = b ? t.indexOf(b, i + 1) : t.length;
  return i < 0 ? '' : plainText(t.slice(i, j < 0 ? t.length : j));
}

const args = process.argv.slice(2);
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined; };
const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain && args[0] && !args[0].startsWith('--')) {
  const file = args[0];
  const forbid = opt('--forbid') ? readFileSync(opt('--forbid'), 'utf8').split('\n').map((x) => x.trim()).filter(Boolean) : null;
  const ok = report(path.basename(file), checkPdf(readFileSync(file), { file, a4: args.includes('--a4'), forbid, name: opt('--name'), kept: opt('--kept') ? [opt('--kept')] : null }));
  process.exit(ok ? 0 : 1);
} else if (isMain) {
  const W = requireCjs(WRITER);
  if (WRITER !== CANON) console.log('(the writer under test: ' + WRITER + ')');
  const J = (f) => JSON.parse(readFileSync(path.join(FX, f), 'utf8'));
  const forbid = readFileSync(path.join(FX, 'ship2-forbid.txt'), 'utf8').split('\n').map((x) => x.trim()).filter(Boolean);
  const NAME = 'ship2_privacy_orders.csv', STEM = 'ship2_privacy_orders';
  const date = new Date(2026, 8, 29, 22, 15, 0);
  const tmp = mkdtempSync(path.join(os.tmpdir(), 'nl-report-pdf-'));
  let ok = true;
  const hashes = {};              // "label [paper]" -> the PDF's sha256, for --hashes and --same-as
  const sha = (u8) => createHash('sha256').update(u8).digest('hex');
  const mk = (resp, results, o) => W.model(Object.assign({ report: resp.report, sources: resp.sources, model: resp.model, repaired: resp.repaired,
    removed_figures: resp.removed_figures, results, kept: [], name: NAME, showName: false, date, goal: results && results.goal }, o));
  const run = (label, m, paper, o) => {
    const u8 = W.build(m, { paper });
    hashes[label + ' [' + paper + ']'] = sha(u8);
    const f = path.join(tmp, label.replace(/[^a-z0-9]+/gi, '-') + '.pdf');
    writeFileSync(f, u8);
    const r = checkPdf(u8, Object.assign({ file: f, a4: paper === 'a4', forbid }, o));
    r.pass = report(label + ' (' + (u8.length / 1024).toFixed(0) + ' KB)', r);
    r.file = f;
    return r;
  };
  // a case's own promise, stated after the file's checks
  const expect = (label, cond, msg) => { if (!cond) { console.log('PDF FAIL ' + label + ': ' + msg); ok = false; } return cond; };
  const cases = [
    ['new sections, Letter', J('ship2-response-v2.json'), J('ship2-results-v2.json').results, {}, 'letter'],
    ['new sections, A4', J('ship2-response-v2.json'), J('ship2-results-v2.json').results, {}, 'a4'],
    ['sections of 29 Sep 2026, Letter', J('ship2-response.json'), J('ship2-results.json').results, {}, 'letter'],
    ['a saved report with no engine results, A4', J('ship2-response.json'), null, { charts: J('ship2-results.json').results.charts, tables: J('ship2-results.json').results.tables, kept: undefined }, 'a4'],
    ['opted in: file name and a kept column, Letter', J('ship2-response-v2.json'), J('ship2-results-v2.json').results, { showName: true, kept: ['customer_name'] }, 'letter'],
  ];
  for (const [label, resp, results, o, paper] of cases) {
    const m = mk(resp, results, o);
    const opted = o.showName === true;
    const r = run(label, m, paper, { name: STEM, nameAllowed: opted, kept: o.kept && o.kept.length ? o.kept : null, removed: resp.repaired, source: resp.report });
    ok = r.pass && ok;
    // an older report's derived items carry their parent's grade as their own: shown as derived, never as a grade
    if (results && results.scenarios && (results.scenarios.items || []).length) expect(label, /from a watch change/.test(between(r, 'PART 3', 'PART 4')), 'the run-rate cards do not say "from a WATCH change"');
    // the engine's contribution table the AI did not place ("Where the change in <measure> came from", the adapter's
    // title since 30 Sep 2026) is drawn in Part 1 under "Where the change sits"
    if (results && (results.tables || []).some((t) => /^where the change in /i.test(t.title))) {
      const p1 = between(r, 'PART 1', 'PART 2');
      expect(label, /where the change sits/.test(p1) && /where the change in total revenue came from/.test(p1), 'Part 1 does not draw the engine\'s "Where the change in total revenue came from" table');
    }
  }

  // ---- the final review's cases (30 Sep 2026)
  const R4 = J('review4.json'), RES = R4.results, REP = R4.reports;
  const srcs = (n, t) => Array.from({ length: n }, (_, i) => ({ title: (t || 'Source number ' + (i + 1) + ' on the Canadian dollar and trade') + ' ' + (i + 1),
    link: 'https://www.example.org/articles/2026/09/article-' + (i + 1) + '?ref=' + (i + 1), date: '2026-09-' + String(1 + (i % 28)).padStart(2, '0') }));
  const d30 = new Date(2026, 8, 30, 10, 0, 0);
  const inp = (res, report, o) => Object.assign({ report, sources: srcs(1), model: 'deepseek-v4-pro', repaired: 1, removed_figures: ['12.5'],
    results: res, kept: [], name: 'review4.csv', showName: false, date: d30, goal: res && res.goal }, o || {});
  const check4 = (label, input, paper, extra) => {
    const r = run(label, W.model(input), paper || 'letter', Object.assign({ name: 'review4', removed: input.repaired, source: String(input.report) + JSON.stringify(input.results || {}) }, extra || {}));
    ok = r.pass && ok;
    return r;
  };
  const whitePill = (r, word, a, b) => {      // a grade's own pill: its word in white on the grade's colour
    let on = false, n = 0;
    r.texts.forEach((ts) => ts.forEach((t) => { if (t.s === a) on = true; if (b && t.s === b) on = false; if (on && t.s === word && t.rgb === '1.000 1.000 1.000') n++; }));
    return n;
  };
  const std = (m, t) => REP.std.replace('{m}', m).replace('{t}', String(t));
  // three currencies: the table cells with a unit right-aligned (6d), nothing printed as "??" (6c)
  const r3 = check4('f3 EUR: three currencies, Letter', inp(RES.f3_eur, std('amount', 1)));
  check4('f3 EUR: three currencies, A4', inp(RES.f3_eur, std('amount', 1)), 'a4');
  // the plan's primary is the amount: the adapter's primary claim (results.primary) is the EUR total, and the key
  // figures lead with it, never with the row count the engine's own gate chose as its primary for this file
  const t3 = between(r3, 'EXECUTIVE SUMMARY', 'In brief');
  expect('f3 EUR', !!RES.f3_eur.primary && RES.f3_eur.primary.id === 'measure.amount.total.eur.change', 'the results carry no primary claim, or not the EUR total: ' + JSON.stringify(RES.f3_eur.primary));
  const eurAt = t3.indexOf('total amount in eur, the latest 12 months'), rowsAt = t3.search(/rows?, the latest 12 months|row volume/);
  expect('f3 EUR', eurAt >= 0 && (rowsAt < 0 || eurAt < rowsAt) && /in eur, the latest 12 months [\d,]+ eur/.test(t3), 'the key figures do not lead with the EUR total: ' + t3.slice(0, 300));
  // a CONFIRMED total: the key figures keep their pills; the derived run rates and gaps are "from a CONFIRMED change"
  let r = check4('f9c: a CONFIRMED total with derived figures (parent_grade)', inp(RES.f9c_confirmed, std('revenue', 1)));
  expect('f9c', whitePill(r, 'CONFIRMED', 'EXECUTIVE SUMMARY', 'PART 1') >= 2, 'the headline key figures lost their CONFIRMED pills');
  expect('f9c', whitePill(r, 'CONFIRMED', 'PART 3', 'PART 4') === 0, 'a derived figure in Part 3 wears the CONFIRMED pill');
  expect('f9c', /from a confirmed change/.test(between(r, 'PART 3', 'PART 4')), 'the derived figures do not say "from a CONFIRMED change"');
  expect('f9c', r.hay.indexOf('basis for action') < 0, 'the PDF calls something "a basis for action"');
  // the same results in the older form (a derived item carries the grade itself): the same neutral badge
  const old9 = JSON.parse(JSON.stringify(RES.f9c_confirmed));
  old9.scenarios.items.forEach((x) => { if (x.parent_grade) { x.grade = x.parent_grade; delete x.parent_grade; } });
  r = check4('f9c in the older form (grade only)', inp(old9, std('revenue', 1)));
  expect('f9c older form', whitePill(r, 'CONFIRMED', 'PART 3', 'PART 4') === 0 && /from a confirmed change/.test(between(r, 'PART 3', 'PART 4')), 'an older report\'s derived figures wear the CONFIRMED pill');
  // names in Cyrillic and Bengali: a placeholder each, the same one everywhere, and the note in the source lines
  r = check4('f10: region names in Cyrillic and Bengali', inp(RES.f10_scripts, REP.scripts));
  const ph = (r.text.match(/\[name\u00a0\d+\]/g) || []);
  expect('f10', new Set(ph).size === 4, 'the four region names are not four placeholders: ' + [...new Set(ph)].join(', '));
  expect('f10', /some names are in a script this pdf cannot show; see the on-screen report/.test(r.hay), 'no note says some names cannot be shown');
  expect('f10', /delta revenue/.test(r.hay), 'a lone Greek letter is not written as its name');
  const tableAt = (lab) => { const t = r.text, i = t.indexOf(lab); return i < 0 ? '' : t.slice(i, i + 400); };
  expect('f10', /\[name\u00a01\]/.test(tableAt('Where the change in total revenue came from')) && /\[name 1\]/.test(between(r, 'EXECUTIVE SUMMARY', 'PART 1')), 'the same name is not the same placeholder in the summary and the table');
  // an average headline (FX rates): the adapter refuses the breakdown; the key figures are the primary finding's own
  // (the adapter names it: results.primary, the average of the rate)
  expect('FX', !!RES.fx_average.primary && /average month of value/i.test(RES.fx_average.primary.claim) && RES.fx_average.scenarios.basis === null, 'the results do not name the average as the primary claim: ' + JSON.stringify(RES.fx_average.primary));
  r = check4('FX: an average headline, no breakdown', inp(RES.fx_average, REP.fx, { sources: srcs(3) }));
  const tiles = between(r, 'EXECUTIVE SUMMARY', 'In brief');
  expect('FX', tiles.indexOf('rows, the latest 12 months') < 0 && (tiles.indexOf('row volume') < 0 || tiles.indexOf('average value') < tiles.indexOf('row volume')), 'the key figures lead with a row count the report is not about');
  expect('FX', /average value, the latest 12 months 1\.386/.test(tiles) && /1\.396 in the 12 months before/.test(tiles), 'the key figures do not show the average\'s own level: ' + tiles.slice(0, 200));
  expect('FX', /change on the 12 months before -0\.7%/.test(tiles) && /95% interval -5\.2% to \+7\.8%/.test(tiles), 'the key figures do not show the average\'s change and interval: ' + tiles.slice(0, 300));
  expect('FX', /no scenarios/.test(between(r, 'PART 3', 'PART 4')), 'Part 3 does not say why there are no scenarios');
  // 16 references (the worker's most) and a citation of a 17th it did not return
  r = check4('16 references and one citation of a source not returned', inp(RES.fx_average, REP.refs16, { sources: srcs(16) }));
  const nRefs = (x) => Number((x.notes.join(' ').match(/(\d+) references/) || [])[1]);
  expect('16 references', nRefs(r) === 16 && / 16 address links/.test(' ' + r.notes.join(' ')), 'not every one of the 16 sources is listed and linked: ' + r.notes.join('; '));
  expect('16 references', r.text.indexOf('[17]') < 0, 'a citation of a source that was not returned is printed');
  // headings the writer's own names do not use, a title past five lines, 35 sources (20 kept), no charts
  r = check4('unexpected headings, a long title, 35 sources', inp(RES.reviews, REP.odd, { sources: srcs(35, 'An industry article about video game and electronics sales and returns in 2023 with a long headline') }));
  expect('headings', /industry sales slowed/.test(between(r, 'PART 2', 'PART 3')), '"Market context" is not in Part 2');
  expect('headings', /if volume holds/.test(between(r, 'PART 3', 'PART 4')), '"Outlook and what-ifs" is not in Part 3');
  expect('headings', /read the one-star reviews/.test(between(r, 'PART 4', 'PART 5')), '"Next steps" is not in Part 4');
  expect('headings', /ratings skew/.test(between(r, 'PART 5', 'APPENDIX A')), '"Caveats" is not in Part 5');
  expect('headings', /review volume fell/.test(between(r, 'EXECUTIVE SUMMARY', 'PART 1')), '"Key takeaways" is not the summary');
  expect('headings', r.hay.indexOf('cites no outside source') < 0 && r.hay.indexOf('recommends no action') < 0, 'a part says no source was cited, or no action recommended, beside the report\'s own sections');
  expect('headings', nRefs(r) === 20, 'not 20 references listed: ' + r.notes.join('; '));
  const coverT = r.texts[0].filter((t) => t.f === 'F2' && t.size >= 20).map((t) => t.s);
  expect('headings', coverT.length > 0 && coverT.length <= 5 && /\u2026$/.test(coverT[coverT.length - 1]), 'the cover title is cut without an ellipsis: ' + JSON.stringify(coverT.slice(-1)));
  // an AI report with no world or actions section beside sources: said as such, never "no source" or "no action"
  r = check4('no outside-context or actions section, with sources', inp(RES.f9c_confirmed, 'Revenue rose\n## Executive summary\n- Revenue rose [S1].\n## The headline: Up\nRevenue rose [S1].', { sources: srcs(2) }));
  expect('no sections', /the ai report has no separate section on outside context; its sources are listed in appendix b/.test(r.hay), 'Part 2 does not point at the sources');
  expect('no sections', /the ai report has no separate actions section/.test(r.hay) && r.hay.indexOf('recommends no action') < 0, 'Part 4 does not say the report has no actions section');
  // a cover with 14 long kept columns, a long title and a long question: nothing overlaps (7), all named
  const kept14 = Array.from({ length: 14 }, (_, i) => 'customer_primary_contact_full_legal_name_' + (i + 1));
  const longQ = 'Which of our twelve regional markets, twelve product categories and forty long-named products carried the change in revenue over the last decade, and what would happen to next year if the weakest regions sold at the strongest region\'s price per unit, including returns?';
  const longT = 'Revenue grew in the latest 12 months and most of the growth sits in a handful of regions with very long names that the table has to wrap without spilling into the margins of the page';
  for (const paper of ['letter', 'a4']) check4('14 kept columns on the cover, ' + paper, inp(RES.f9c_confirmed, longT + '\n' + std('revenue', 1).split('\n').slice(1).join('\n'), { kept: kept14, goal: longQ, repaired: 3 }), paper, { kept: kept14 });
  // a share link's copy: the older kind (no results, no kept) says so accurately; the new kind carries trimmed results
  const NOT = ['were not kept', 'saved before the personal columns', 'saved before the engine'];
  r = check4('a shared copy made before results rode along', inp(null, std('revenue', 1), { shared: true, kept: undefined, repaired: undefined, charts: RES.f9c_confirmed.charts, tables: RES.f9c_confirmed.tables }), 'a4', { removed: undefined });
  expect('old share', /this shared copy doesn't carry the engine's full results; the figures shown are those in the report text/.test(r.hay), 'the old share does not say it lacks the engine\'s full results');
  expect('old share', !NOT.some((w) => r.hay.indexOf(w) >= 0), 'the old share uses a saved report\'s wording');
  const trimmed = W.shareResults ? W.shareResults(RES.f9c_confirmed) : null;
  const tb = trimmed ? Buffer.byteLength(JSON.stringify(trimmed), 'utf8') : 0;
  expect('share results', !!trimmed && tb <= 20000 && trimmed.scenarios.items.length <= 60 && trimmed.partial === true && !/review4|\.csv/.test(JSON.stringify(trimmed)), 'the trimmed results are ' + tb + ' bytes, or over 60 items, or not marked partial');
  // every item keeps its value (the worker checks an item's text against its value and drops one without) and the
  // fields the writer reads; the primary claim goes with them
  const itemOk = (x) => (x.kind === 'date' ? typeof x.value === 'string' : typeof x.value === 'number' && isFinite(x.value)) &&
    ['id', 'group', 'label', 'text', 'kind', 'unit'].every((k) => k in x) && 'grade' in x;
  const ITEMS = RES.f9c_confirmed.scenarios.items;
  expect('share results', !!trimmed && trimmed.scenarios.items.length > 0 && trimmed.scenarios.items.every(itemOk) &&
    trimmed.scenarios.items.every((x) => { const o = ITEMS.filter((y) => y.id === x.id)[0]; return o && o.value === x.value && (o.parent_grade || null) === (x.parent_grade || null) && (o.grade_words || null) === (x.grade_words || null); }),
    'a shared item lacks its value, its parent grade or its grade words: ' + JSON.stringify((trimmed && trimmed.scenarios.items.filter((x) => !itemOk(x))[0]) || null));
  expect('share results', !!trimmed && JSON.stringify(trimmed.primary) === JSON.stringify(RES.f9c_confirmed.primary), 'the primary claim is not in the trimmed results');
  // a tighter cap (the page's share-size guard): the results shrink to it, and null when nothing fits
  const t4k = W.shareResults ? W.shareResults(RES.f9c_confirmed, 4000) : null;
  expect('share results', !!t4k && Buffer.byteLength(JSON.stringify(t4k), 'utf8') <= 4000 && W.shareResults(RES.f9c_confirmed, 20) === null, 'the trimmed results do not keep to a tighter cap');
  // a large report: f10's 54 scenario items three times over, over both caps (60 items, 20 KB)
  const big = JSON.parse(JSON.stringify(RES.f10_scripts)); big.scenarios.items = big.scenarios.items.concat(big.scenarios.items, big.scenarios.items);
  const tbig = W.shareResults ? W.shareResults(big) : null;
  expect('share results', !!tbig && Buffer.byteLength(JSON.stringify(big.scenarios), 'utf8') > 20000 && Buffer.byteLength(JSON.stringify(tbig), 'utf8') <= 20000 &&
    tbig.scenarios.items.length > 0 && tbig.scenarios.items.length <= 60, 'the trimmed results of a large report are over 20 KB or 60 items');
  r = check4('a shared copy with the engine\'s trimmed results', inp(trimmed, std('revenue', 1), { shared: true, kept: [], repaired: 1, charts: RES.f9c_confirmed.charts, tables: RES.f9c_confirmed.tables }));
  expect('new share', !NOT.some((w) => r.hay.indexOf(w) >= 0) && r.hay.indexOf('doesn\'t carry the engine\'s full results') < 0, 'a share with its results says they were not kept');
  expect('new share', /total revenue, the latest 12 months/.test(between(r, 'EXECUTIVE SUMMARY', 'PART 1')) && /run rate, a year/.test(between(r, 'PART 3', 'PART 4')), 'the shared PDF lacks the key figures or the scenario cards');
  // a shared copy whose results carry the worker's key_figures and no headline item: those are its key figures
  const kfShare = { partial: true, input: trimmed.input, health_score: trimmed.health_score, findings: trimmed.findings,
    key_figures: [{ label: 'Total revenue, the latest 12 months', value: 850673, text: '850,673 CAD', unit: 'CAD', grade: 'CONFIRMED' },
      { label: 'Change on the 12 months before', value: 29, text: '+29%', unit: '%', grade: 'CONFIRMED' }] };
  r = check4('a shared copy with the worker\'s key figures only', inp(kfShare, std('revenue', 1), { shared: true, kept: [], repaired: 1, charts: RES.f9c_confirmed.charts, tables: RES.f9c_confirmed.tables }));
  const kfT = between(r, 'EXECUTIVE SUMMARY', 'In brief');
  expect('key figures share', /total revenue, the latest 12 months 850,673 cad confirmed change on the 12 months before \+29% confirmed data health score/.test(kfT.replace(/\s+/g, ' ')) && whitePill(r, 'CONFIRMED', 'EXECUTIVE SUMMARY', 'In brief') >= 2,
    'the shared PDF does not lead with the stored key figures: ' + kfT.slice(0, 300));
  const kfPage = W.model(inp(kfShare, std('revenue', 1), { shared: false }));
  expect('key figures share', kfPage.kpis.every((k) => k.value !== '850,673 CAD'), 'the key figures of a share are read outside a shared copy');
  // a share trimmed to its cap keeps the scenario cards (the cross-repo contract test of 30 Sep 2026: the items were cut
  // from the tail, which dropped the run rate, the sensitivity, the gaps and the facts from 4 of 6 shared PDFs): for
  // every results file whose block has run-rate, sensitivity or gap items and is over the cap, the shared copy's model
  // draws the same key figures, run-rate and sensitivity cards, gap table, price, volume and mix table and forecast as
  // the full results, and its items keep the adapter's order within the caps
  const engineBlocks = (m) => {
    const out = [];
    m.parts.forEach((p) => (p.blocks || []).forEach((b) => {
      if (b.type === 'cards') b.items.forEach((it) => out.push(['card', p.kicker, it.name, it.value, it.unit, it.grade || null, it.parent || null]));
      if (b.type === 'table' && (b.engine || /^price, volume and mix$/i.test(b.table.title || ''))) out.push(['table', p.kicker, b.table.title, JSON.stringify(b.table.rows)]);
      if (b.type === 'noscenarios') out.push(['none', p.kicker, b.reason]);
    }));
    return out;
  };
  const tilesOf = (m) => m.kpis.map((k) => [k.label, k.value, k.sub, k.grade || null]);
  const SHARE_CASES = [['f3 EUR', RES.f3_eur, 'amount'], ['f9c', RES.f9c_confirmed, 'revenue'], ['f10', RES.f10_scripts, 'revenue'], ['ship2 v2', J('ship2-results-v2.json').results, 'revenue']];
  for (const [lab, res, measure] of SHARE_CASES) {
    const all = res.scenarios.items, has = (g) => all.some((x) => x.group === g);
    const tr = W.shareResults(res), trItems = tr && tr.scenarios ? tr.scenarios.items : [];
    const pos = trItems.map((x) => all.findIndex((y) => y.id === x.id));
    expect('trimmed share ' + lab, ['run_rate', 'sensitivity', 'gap'].some(has) && Buffer.byteLength(JSON.stringify(res), 'utf8') > 20000,
      'the fixture no longer has a run rate, a sensitivity or a gap over the 20 KB cap, so this case proves nothing');
    expect('trimmed share ' + lab, !!tr && Buffer.byteLength(JSON.stringify(tr), 'utf8') <= 20000 && trItems.length <= 60 && pos.every((p, i) => p >= 0 && (!i || p > pos[i - 1])),
      'the trimmed results are over 20 KB or 60 items, or their items left the adapter\'s order');
    const lost = ['headline', 'price_volume_mix', 'run_rate', 'sensitivity', 'gap', 'forecast'].filter((g) => all.filter((x) => x.group === g).length !== trItems.filter((x) => x.group === g).length);
    expect('trimmed share ' + lab, !lost.length, 'the trimmed results lost items of the core: ' + lost.join(', '));
    const tabs = { charts: res.charts, tables: res.tables };
    const mFull = W.model(inp(res, std(measure, 1), tabs)), mShared = W.model(inp(tr, std(measure, 1), Object.assign({ shared: true }, tabs)));
    const bF = engineBlocks(mFull), bS = engineBlocks(mShared);
    expect('trimmed share ' + lab, bF.some((b) => b[0] === 'card' && /^run rate|^sensitivity/i.test(b[2])) || bF.some((b) => b[0] === 'table' && /^gap to the largest/i.test(b[2])),
      'the full results draw no run-rate, sensitivity or gap block');
    expect('trimmed share ' + lab, JSON.stringify(bF) === JSON.stringify(bS),
      'the shared copy\'s scenario cards and engine tables differ from the full results\': ' + JSON.stringify(bF.filter((b) => !bS.some((c) => JSON.stringify(c) === JSON.stringify(b)))).slice(0, 400));
    expect('trimmed share ' + lab, JSON.stringify(tilesOf(mFull)) === JSON.stringify(tilesOf(mShared)), 'the shared copy\'s key figures differ from the full results\'');
  }
  // a segment's items go together (final review, 30 Sep 2026: putting cut items back one at a time put back a gap whose
  // contribution did not fit): at caps from each block's full size down, a shared gap item never stands without its
  // segment's contribution, the cap holds and the result is the same each time; the review's no-plan results
  // (tools/fixtures/review5/noplan_results.json) and a made-up block whose gap labels are shorter than its
  // contribution labels (the review's orphan) with the share cases above
  const segBlock = (segs) => {
    const it = [{ id: 'h', group: 'headline', segment: null, label: 'Total', value: 1, text: '1', kind: 'amount', unit: '', grade: 'CONFIRMED' }];
    segs.forEach((s, i) => it.push({ id: 'c' + i, group: 'contribution', segment: s, label: s + ': its contribution to the change in total revenue, the long form of the label xxxxxxxxxxxxxxxxxxxxxxxx', value: 1000 * (9 - i), text: String(1000 * (9 - i)), kind: 'change', unit: '', grade: 'CONFIRMED' }));
    segs.forEach((s, i) => { if (i) it.push({ id: 'g' + i, group: 'gap', segment: s, label: s + ': gap', value: 5, text: '5', kind: 'amount', unit: '', grade: 'CONFIRMED' }); });
    return { scenarios: { basis: {}, items: it, refused: [], note: '' }, findings: [], input: { rows: 1, columns: 1 } };
  };
  const NOPLAN = JSON.parse(readFileSync(path.join(HERE, 'fixtures', 'review5', 'noplan_results.json'), 'utf8'));
  const orphanCases = SHARE_CASES.map(([lab, res]) => [lab, res]).concat([['review5 no-plan', NOPLAN], ['review5 short gap labels', segBlock(['A', 'B', 'C', 'D'])]]);
  const orphanBad = [];
  for (const [lab, res] of orphanCases) {
    const full = Buffer.byteLength(JSON.stringify(res), 'utf8'), step = Math.max(1, Math.floor(full / 700));
    for (let cap = Math.min(full + 50, 20000); cap >= 100; cap -= step) {
      const a = W.shareResults(res, cap);
      if (!a) continue;
      const it = a.scenarios ? a.scenarios.items : [], has = new Set(it.filter((x) => x.group === 'contribution').map((x) => x.segment));
      const full0 = (res.scenarios.items || []).filter((x) => x.group === 'contribution').map((x) => x.segment);
      const o = it.filter((x) => x.group === 'gap' && full0.indexOf(x.segment) >= 0 && !has.has(x.segment));
      if (o.length || Buffer.byteLength(JSON.stringify(a), 'utf8') > cap || JSON.stringify(a) !== JSON.stringify(W.shareResults(res, cap))) { orphanBad.push(lab + ' at ' + cap + ' bytes: ' + (o.map((x) => x.id).join(', ') || 'over the cap or not the same twice')); break; }
    }
  }
  expect('share segments', !orphanBad.length, 'a shared gap item without its contribution: ' + orphanBad.join('; '));
  r = check4('f3 EUR: a shared copy trimmed to its cap', inp(W.shareResults(RES.f3_eur), std('amount', 1), { shared: true, charts: RES.f3_eur.charts, tables: RES.f3_eur.tables }));
  const p3s = between(r, 'PART 3', 'PART 4');
  expect('trimmed share f3 EUR', /run rate, a year/.test(p3s) && /sensitivity/.test(p3s) && /gap to the largest country/.test(p3s), 'the shared PDF lacks the run-rate, sensitivity or gap blocks: ' + p3s.slice(0, 300));

  // the writer as the page runs it (a classic script: window.NLReportPdf, no module) makes the same bytes
  const ctx = { window: {} };
  vm.runInNewContext(readFileSync(WRITER, 'utf8'), ctx, { filename: '45-report-pdf.js' });
  const page = ctx.window.NLReportPdf;
  const m0 = mk(J('ship2-response-v2.json'), J('ship2-results-v2.json').results, {});
  const m1 = JSON.parse(JSON.stringify(m0)); m1.date = date;
  const pageU8 = page ? page.build(m1, { paper: 'letter' }) : null;
  const eq = page && Buffer.compare(Buffer.from(pageU8), Buffer.from(W.build(m0, { paper: 'letter' }))) === 0;
  console.log((eq ? 'PDF PASS ' : 'PDF FAIL ') + 'the classic script (as the page runs it) makes the same bytes as the Node module');
  ok = eq && ok;
  if (pageU8) hashes['the classic script, new sections [letter]'] = sha(pageU8);
  // every fixture PDF's sha256 (--hashes), and the same bytes as an earlier run's (--same-as)
  if (opt('--hashes')) { writeFileSync(opt('--hashes'), JSON.stringify(hashes, null, 2) + '\n'); console.log('(' + Object.keys(hashes).length + ' PDF hashes written to ' + opt('--hashes') + ')'); }
  if (opt('--same-as')) {
    const was = JSON.parse(readFileSync(opt('--same-as'), 'utf8')), keys = [...new Set(Object.keys(was).concat(Object.keys(hashes)))];
    const moved = keys.filter((k) => was[k] !== hashes[k]);
    const letters = keys.filter((k) => /\[letter\]$/.test(k)).length, a4s = keys.filter((k) => /\[a4\]$/.test(k)).length;
    console.log((moved.length ? 'PDF FAIL ' : 'PDF PASS ') + 'every fixture PDF (' + letters + ' Letter, ' + a4s + ' A4) is byte-identical to ' + path.basename(opt('--same-as')) +
      (moved.length ? ': ' + moved.length + ' differ or are missing: ' + moved.join('; ') : ''));
    ok = !moved.length && ok;
  }
  // the download's name: the date, and the file's name only when asked for
  const n0 = W.fileName(date), n1 = W.fileName(date, NAME);
  const names = n0 === 'NorthLedger report - 2026-09-29.pdf' && n1 === 'NorthLedger report - 2026-09-29 - ship2_privacy_orders.pdf';
  console.log((names ? 'PDF PASS ' : 'PDF FAIL ') + 'the download is named "' + n0 + '", and "' + n1 + '" only with the opt-in');
  ok = names && ok;
  // the paper: Letter in the US, Canada and Mexico (and when no region is named), A4 elsewhere
  const papers = [['en-CA', 'letter'], ['fr-CA', 'letter'], ['en-US', 'letter'], ['es-MX', 'letter'], ['en', 'letter'], ['en-GB', 'a4'], ['de-DE', 'a4'], ['zh-Hant-TW', 'a4']]
    .filter(([l, p]) => W.paperFor([l]) !== p);
  console.log((papers.length ? 'PDF FAIL ' : 'PDF PASS ') + 'the paper follows the browser\'s region' + (papers.length ? ': ' + papers.map((x) => x[0]).join(', ') : ''));
  ok = !papers.length && ok;
  // the writer's source is what index.html inlines: ASCII only, no closing tag, no outside address
  const src = readFileSync(WRITER, 'utf8');
  const srcBad = [/[^\x09\x0a\x0d\x20-\x7e]/.test(src) && 'a non-ASCII character', /<\//.test(src) && 'a "</" sequence', /\b(?:https?|wss?):\/\//i.test(src) && 'an absolute address',
    /ns\.adobe\.com|purl\.org|w3\.org/i.test(src) && 'an XMP namespace'].filter(Boolean);
  console.log((srcBad.length ? 'PDF FAIL ' : 'PDF PASS ') + 'src/js/45-report-pdf.js is safe to inline' + (srcBad.length ? ': ' + srcBad.join(', ') : ''));
  ok = !srcBad.length && ok;
  console.log(ok ? 'REPORT PDF: ALL PASS' : 'REPORT PDF: FAILED');
  process.exit(ok ? 0 : 1);
}
