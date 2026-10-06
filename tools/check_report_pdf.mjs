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
// The chart registry (wave 2B, 30 Sep 2026; tools/fixtures/viz/spec.json): viz-results.json and viz-response.json
// (made by fixtures/report-pdf/make_viz_fixtures.py from the spec's 14 examples and 4 validated illustrative records)
// build a 10-chart report (every draw kind), the edge cases (negative totals, all-empty and 1-row heatmaps, suppressed
// cells, 12 long segment labels, a non-Latin label, 24 YYYY-MM columns, 14 long column labels, a page record, a kind
// no reader knows, a heatmap whose tiers are the wrong shape, a kind with no table), the contribution waterfall the AI
// placed nowhere (Part 1 draws it) and a share link's copy, Letter and A4, each through every check above and, from
// build(model, {trace}), vizCheck: each figure's box inside the text area; every mark and word of its operators
// (parsed from the file) inside its box, no two of its words overlapping; each heatmap cell's fill, text and glyph
// inside the cell (its text when it fits, else the glyph alone); "Figure n." and the record's title over it and the
// source note under it; the legend in the engine's words; the table view after its figure (12 rows at most) or in
// Appendix A; an unknown kind or unreadable data drawn as its table, never an empty box; the trace moves no byte;
// every kind in each of its layouts; and the same bytes from the classic script for the 10-chart report.
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
    // the sentence is whole, and says what the check could not do (integration pass, 1 Oct 2026: a live PDF read
    // "...removed 4 sentences carrying a figure that was neither. No person reviewed this report."; "neither computed by
    // the engine" is also false for an engine figure the guard removed because its sentence counted it in other words)
    const flat = hay.replace(/\s+/g, ' ');
    const whole = o.removed === 0 ? '' : plainText('removed ' + o.removed + (o.removed === 1 ? ' sentence, which carried' : ' sentences, each carrying') +
      ' a figure the check could not match to the engine\'s results or to a source cited in the same sentence. no person reviewed this report.');
    if ((whole && flat.indexOf(whole) < 0) || /carrying a figure that was neither|neither computed by the engine/.test(flat)) {
      fail('the notice\'s honesty-check sentence is cut or says "neither": ' + (flat.match(/removed \d+ sentences?[^.]{0,160}\./) || [''])[0]);
    }
  }
  return { ok: !fails.length, fails, notes, pages: N, text: all, hay, texts: pages.map((p) => p.texts), streams: pages.map((p) => p.stream), media };
}

// ---- the chart registry's figures (spec tools/fixtures/viz/spec.json), checked from the PDF's own operators
// One content-stream operator's marks: every point a path passes through (re, m, l, c: a Bezier's control points
// too) and every text's box (Td, measured with the Helvetica AFM widths: ascender 718, descender 207), in points.
export function marksOf(op) {
  const toks = op.match(/\((?:[^()\\]|\\.)*\)|\[[^\]]*\]|\/[A-Za-z0-9]+|[-+]?(?:\d+\.?\d*|\.\d+)|[A-Za-z*']+/g) || [];
  const st = [], pts = [], texts = [];
  let font = 'R', size = 0, tc = 0, at = [0, 0];
  for (const t of toks) {
    if (/^[-+]?[\d.]/.test(t)) { st.push(+t); continue; }
    if (t[0] === '(') { st.push(t); continue; }
    if (t[0] === '[') continue;
    if (t[0] === '/') { if (/^\/F\d$/.test(t)) font = t === '/F2' ? 'B' : 'R'; continue; }
    if (t === 're') { const [x, y, w, h] = st.splice(-4); pts.push([x, y], [x + w, y + h]); } else if (t === 'm' || t === 'l') pts.push(st.splice(-2));
    else if (t === 'c') { const a = st.splice(-6); pts.push([a[0], a[1]], [a[2], a[3]], [a[4], a[5]]); } else if (t === 'Tf') size = st.pop();
    else if (t === 'Tc') tc = st.pop(); else if (t === 'Td') at = st.splice(-2);
    else if (t === 'Tj') {
      const raw = String(st.pop()).slice(1, -1).replace(/\\([()\\])/g, '$1'), lead = raw.length - raw.replace(/^ +/, '').length, s = raw.trim();
      const x1 = at[0] + METRICS._tw(raw.slice(0, lead), size, font) + tc * lead;
      if (s) texts.push({ x1, x2: x1 + METRICS._tw(s, size, font) + tc * s.length, y1: at[1] - 0.207 * size, y2: at[1] + 0.718 * size, s, size });
    }
    st.length = t === 'Tj' || t === 're' || t === 'm' || t === 'l' || t === 'c' || t === 'Tf' || t === 'Tc' || t === 'Td' ? st.length : 0;
  }
  return { pts, texts };
}
// A PDF's viz figures against their trace (build(model, {trace})): each figure's box inside the text area; every mark
// and every word of the figure (its operators, parsed from the file) inside its box; no two of its words overlapping;
// each heatmap cell's fill, text and glyph inside the cell; "Figure n." with the record's title over each; the source
// note under it.
export function vizCheck(r, trace) {
  const fails = [], fail = (m) => { if (fails.length < 12) fails.push(m); };
  const [PW, PH] = r.media, L = 60, R = PW - 60, TOP = PH - 74, BOT = 66;
  const inside = (p, b, t) => p[0] >= b[0] - t && p[0] <= b[2] + t && p[1] >= b[1] - t && p[1] <= b[3] + t;
  const ops = r.streams.map((st) => st.split('\n'));
  const n = { figs: 0, tables: 0, marks: 0, words: 0, cells: 0, cellText: 0, glyphOnly: 0 };
  for (const f of trace) {
    if (f.as === 'table') { n.tables++; continue; }
    n.figs++;
    const where = 'Figure ' + f.fig + ' (' + f.kind + ' ' + f.layout + ', page ' + (f.page + 1) + ')', b = f.box;
    if (b[0] < L - 0.01 || b[2] > R + 0.01 || b[1] < BOT - 0.01 || b[3] > TOP + 0.01) fail(where + ': its box [' + b.map((v) => v.toFixed(1)) + '] leaves the text area');
    const boxes = [];
    for (const op of ops[f.page].slice(f.op0, f.op1)) {
      const m = marksOf(op);
      for (const p of m.pts) { n.marks++; if (!inside(p, b, 0.8)) fail(where + ': a mark at (' + p.map((v) => v.toFixed(1)) + ') is outside its box'); }
      for (const t of m.texts) { n.words++; boxes.push(t); if (!inside([t.x1, t.y1], b, 0.5) || !inside([t.x2, t.y2], b, 0.5)) fail(where + ': "' + t.s + '" (x ' + t.x1.toFixed(1) + '..' + t.x2.toFixed(1) + ') is outside its box'); }
    }
    const pageText = boxes.map((t) => t.s).join(' ');     // the words in the order they are drawn
    boxes.sort((a, c) => a.y1 - c.y1);
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length && boxes[j].y1 < boxes[i].y2; j++) {
        const A = boxes[i], B = boxes[j], ox = Math.min(A.x2, B.x2) - Math.max(A.x1, B.x1), oy = Math.min(A.y2, B.y2) - Math.max(A.y1, B.y1);
        if (ox > 1 && oy > 0.34 * Math.min(A.y2 - A.y1, B.y2 - B.y1)) fail(where + ': "' + A.s + '" and "' + B.s + '" overlap');
      }
    }
    if (!new RegExp('^Figure ' + f.fig + '\\.').test(pageText)) fail(where + ': it does not open with "Figure ' + f.fig + '."');
    if (!/Source: NorthLedger engine/.test(pageText.replace(/\s+/g, ' '))) fail(where + ': no source note under it');
    for (const c of f.cells) {
      n.cells++;
      let texts = 0;
      for (const op of ops[f.page].slice(c.op0, c.op1)) {
        const m = marksOf(op);
        for (const p of m.pts) if (!inside(p, c.r, 0.4)) fail(where + ': cell "' + c.s + '" draws at (' + p.map((v) => v.toFixed(1)) + '), outside [' + c.r.map((v) => v.toFixed(1)) + ']');
        for (const t of m.texts) { texts++; if (!inside([t.x1, t.y1], c.r, 0) || !inside([t.x2, t.y2], c.r, 0)) fail(where + ': cell text "' + t.s + '" leaves its cell'); }
      }
      if (c.text) { n.cellText++; if (texts !== 1) fail(where + ': cell "' + c.s + '" should print its text once, printed ' + texts); } else if (texts) fail(where + ': cell "' + c.s + '" prints text it has no room for');
      else if (c.s) n.glyphOnly++;
    }
  }
  return { ok: !fails.length, fails, n };
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

  // ---- a level's historical range (pre-deploy pass, 30 Sep 2026): fx-history-results.json, the FX run's results from
  // the packed engine (make_fx_history.py), whose scenarios hold the twenty history_range items (12-month and 3-month
  // windows: count, n_eff, 10th/50th/90th percentile, rose, and the non-overlapping changes) and the facts, and no run rate, gap or forecast. Part 3 says why there are no scenarios, then draws "What
  // past moves looked like (history, not a forecast)": a card per figure, the longest window first, each with its
  // window count, a neutral HISTORY label and never a grade's pill, every card's words whole, and the engine's own
  // sentences under them; the same in a shared copy; and a share's cut keeps the history after the facts and drops
  // it before the forecast (the worker's SCENARIO_PRIORITY). Before this the writer drew none of it, and shareResults
  // dropped the history first.
  const FXH = J('fx-history-results.json'), FXR = FXH.results;
  const fxItems = FXR.scenarios.items, fxHist = fxItems.filter((x) => x.group === 'history_range');
  const fxAt = (id) => fxItems.filter((x) => x.id === id)[0];
  const fxReport = ['The Canadian dollar price of a U.S. dollar moved within its usual band', '## Executive summary',
    '- The average rate in the latest 12 months was 1.386, against 1.396 in the 12 months before, graded WATCH.',
    '- ' + fxAt('history_range.m12.windows').label, '## The headline: A flat year for the rate', 'The latest year is graded WATCH.', '[CHART:1]',
    '## Scenarios', fxAt('history_range.m12.windows').label + ' Over the 113 past 3-month windows it ran from ' + fxAt('history_range.m3.p10').text + ' to ' +
      fxAt('history_range.m3.p90').text + '.', '## What to do', '1. Compare the rate against its past range before committing money.',
    '## Risks and what the data cannot say', '- The range is history, not a forecast.'].join('\n');
  const fxInp = (res, o) => inp(res, fxReport, Object.assign({ name: 'fx_usd_cad.csv', charts: FXR.charts, tables: FXR.tables }, o || {}));
  expect('FX history', FXH.engine_snapshot === '19ec81d19e0d' && fxHist.length === 20 && fxHist.every((x) => x.grade === null && !x.parent_grade) &&
    !fxItems.some((x) => ['run_rate', 'sensitivity', 'gap', 'forecast'].indexOf(x.group) >= 0), 'the fixture is not the FX run with twenty ungraded history items and no scenario to add up');
  const HIST_CARDS = [];                  // [name, value, words] as the writer draws them, the 12-month window first
  [12, 3].forEach((lg) => {
    const w = fxAt('history_range.m' + lg + '.windows').text, rose = fxAt('history_range.m' + lg + '.rose').text, ne = fxAt('history_range.m' + lg + '.n_eff').text;
    const nn = fxAt('history_range.m' + lg + '.nonoverlap.n').text, of = 'the ' + w + ' past windows (about ' + ne + ' independent)';
    HIST_CARDS.push([lg + '-month: 1 in 10 lower', fxAt('history_range.m' + lg + '.p10').text, 'The change in the monthly average; 1 in 10 of ' + of + ' was lower'],
      [lg + '-month: Middle', fxAt('history_range.m' + lg + '.p50').text, 'The change in the monthly average, the middle of ' + of + '; it rose in ' + rose + ' of them'],
      [lg + '-month: 1 in 10 higher', fxAt('history_range.m' + lg + '.p90').text, 'The change in the monthly average; 1 in 10 of ' + of + ' was higher']);
    // the non-overlapping alternative: one window a year (or a quarter), none counted twice
    [['min', 'lowest'], ['median', 'middle'], ['max', 'highest']].forEach(([k, word]) => HIST_CARDS.push([lg + '-month, spaced: ' + word, fxAt('history_range.m' + lg + '.nonoverlap.' + k).text,
      'The ' + word + ' of the ' + nn + ' non-overlapping ' + lg + '-month changes in the monthly average']));
  });
  const sp = (t) => plainText(t).replace(/\s+/g, ' ');
  const histCheck = (label, r) => {
    const p3 = sp(between(r, 'PART 3', 'PART 4')), at = p3.indexOf('what past moves looked like (history, not a forecast)'), blk = at < 0 ? '' : p3.slice(at);
    expect(label, at >= 0, 'Part 3 has no "What past moves looked like (history, not a forecast)" block: ' + p3.slice(0, 400));
    // the reason there is nothing to add up comes first, then the history
    const ns = p3.indexOf('no scenarios');
    expect(label, ns >= 0 && ns < at && p3.indexOf('the headline is an average, so it has no parts that add up') >= 0, 'Part 3 does not say first why there are no scenarios');
    let from = 0;
    HIST_CARDS.forEach(([name, value, words]) => {
      const i = blk.indexOf(sp(name), from), v = blk.indexOf(sp(value), i), wd = blk.indexOf(sp(words), i);
      expect(label, i >= 0 && v > i && wd > v, 'the card "' + name + '" (' + value + ') is missing, out of order or its words are cut: ' + blk.slice(Math.max(0, i), i + 260));
      if (i >= 0) from = i + 1;
    });
    // a neutral HISTORY label on every card, never a grade's pill; the engine's own sentences and the zero note under them
    const pills = [];
    r.texts.forEach((ts) => ts.forEach((t) => { if (/^(CONFIRMED|WATCH|NOT ENOUGH DATA|HISTORY)$/.test(t.s) || /^from a /.test(t.s)) pills.push(t.s); }));
    const hs = pills.filter((x) => x === 'HISTORY').length;
    expect(label, hs === HIST_CARDS.length, hs + ' HISTORY labels for ' + HIST_CARDS.length + ' cards');
    expect(label, !/\b(confirmed|watch|not enough data)\b|from a (confirmed|watch)/.test(blk), 'a grade word or pill in the history block: ' + blk.slice(0, 300));
    ['history_range.m12.windows', 'history_range.m3.windows'].forEach((id) => expect(label, blk.indexOf(sp(fxAt(id).label)) >= 0, 'the engine\'s sentence is not under the cards: ' + fxAt(id).label));
    expect(label, blk.indexOf(sp(fxHist[0].assumes)) >= 0 && /not graded: how far the average moved before, not how far it will move/.test(blk), 'the zero note or the "not a forecast" line is missing');
  };
  let rh = null;
  for (const paper of ['letter', 'a4']) { rh = check4('FX history: the historical range, ' + paper, fxInp(FXR, { sources: srcs(2) }), paper, { name: 'fx_usd_cad' }); histCheck('FX history ' + paper, rh); }
  // a shared copy: the trimmed results keep all ten items, and the shared PDF draws the same cards
  const fxShare = W.shareResults(FXR), fxS = fxShare && fxShare.scenarios ? fxShare.scenarios.items : [];
  expect('FX history share', fxS.filter((x) => x.group === 'history_range').map((x) => x.id).join() === fxHist.map((x) => x.id).join() &&
    fxS.filter((x) => x.group === 'history_range').every((x) => { const o = fxAt(x.id); return o.value === x.value && o.text === x.text && o.label === x.label && o.assumes === x.assumes; }),
  'the share\'s trimmed results lost history items or their fields: ' + JSON.stringify(fxS.map((x) => x.id)));
  const rs = check4('FX history: a shared copy', fxInp(fxShare, { shared: true, kept: [], sources: srcs(2) }), 'letter', { name: 'fx_usd_cad' });
  histCheck('FX history shared', rs);
  const cardsOf = (m) => JSON.stringify(m.parts.filter((p) => p.id === 'scenarios')[0].blocks.filter((b) => b.type === 'cards' || b.type === 'noscenarios'));
  expect('FX history shared', cardsOf(W.model(fxInp(FXR))) === cardsOf(W.model(fxInp(fxShare, { shared: true }))), 'the shared copy\'s history cards differ from the full results\'');
  // a tight cap: the facts go first and the history stays whole; tighter, the history goes before a forecast item
  const sizeOf = (x) => Buffer.byteLength(JSON.stringify(x), 'utf8');
  const facts = fxItems.filter((x) => x.group === 'facts');
  const tight = W.shareResults(FXR, sizeOf(fxShare) - 40), tI = tight ? tight.scenarios.items : [];
  expect('FX history cap', !!tight && tI.filter((x) => x.group === 'history_range').length === 20 && tI.filter((x) => x.group === 'facts').length < facts.length,
    'at ' + (sizeOf(fxShare) - 40) + ' bytes the share did not drop a fact before the history: ' + JSON.stringify(tI.map((x) => x.id)));
  const fcRes = JSON.parse(JSON.stringify(FXR));
  fcRes.scenarios.items.push({ id: 'forecast.3.base', group: 'forecast', segment: null, label: 'Base case, the next 3 months', value: 60, text: '60', kind: 'count', unit: '', grade: 'CONFIRMED' });
  let histGone = null;
  for (let cap = sizeOf(W.shareResults(fcRes)); cap > 400 && !histGone; cap -= 25) {
    const t = W.shareResults(fcRes, cap), it = t && t.scenarios ? t.scenarios.items : [];
    if (t && t.scenarios && it.filter((x) => x.group === 'history_range').length < 20) histGone = { cap, it };
  }
  expect('FX history cap', !!histGone && histGone.it.some((x) => x.group === 'forecast'),
    'the history did not go before the forecast: ' + JSON.stringify(histGone && histGone.it.map((x) => x.id)));

  // ---- the final evaluation's cases (1 Oct 2026): final5-results.json (make_final5.py: the packed engine on the FX
  // fixture and on the SYNTHETIC reviews). The live PDFs drew 4 of the FX histogram's 12 bins (the payload's cap), a
  // line of a level (1.25 to 1.40 CAD per USD) on an axis from 0, a health score of 0.0 with nothing saying why, and no
  // word of the 6,694 reviews the AI plan set aside. Now: every bar, and past 8 bars a row a bar with each label whole;
  // a line's value axis spans its data; the health tile says what set the score (and Appendix A's table); a step of the
  // plan that set aside a tenth of the rows or more is said in "About this report" (the cover), and every such step
  // with its count, share, reason and the engine's check under "Rows the AI plan set aside" in Appendix A.
  const F5 = J('final5-results.json');
  const f5Report = (title, n) => [title, '## Executive summary', '- The engine read the file and checked every figure.',
    '## The headline: what the engine found', 'The figures below come from the engine.'].concat(Array.from({ length: n }, (_, i) => '[CHART:' + (i + 1) + ']'))
    .concat(['## What to do', '1. Read the figures with the rows the plan set aside in mind.', '## Risks and what the data cannot say', '- The data are observational.']).join('\n');
  const f5Inp = (key) => {
    const R = F5.runs[key].results;
    return inp(R, f5Report(key === 'fx' ? 'The rate over nine years' : 'What the reviews say', R.charts.length),
      { name: F5.runs[key].csv.split('/').pop(), charts: R.charts, tables: R.tables, kept: key === 'reviews' ? ['review_text'] : [] });
  };
  // the text a reader follows across a page break, the running header and the footer left out; and a part of it
  const body = (r) => r.texts.map((ts, i) => ts.filter((q) => q.y > 45 && (i === 0 || q.y < r.media[1] - 45)).map((q) => q.s).join(' ')).join(' ');
  const bodyBetween = (r, a, b) => { const t = body(r), i = t.indexOf(a), j = t.indexOf(b, i + 1); return i < 0 ? '' : plainText(t.slice(i, j < 0 ? t.length : j)).replace(/\s+/g, ' '); };
  // the numbers printed left of a figure's plot (its value axis) under the caption that holds `title`
  const axisOf = (r, title) => {
    for (const ts of r.texts) {
      const cap = ts.findIndex((t) => t.s.indexOf(title) >= 0 && t.f === 'F2');
      if (cap < 0) continue;
      const fig = ts.filter((t) => /^Figure \d+\./.test(t.s)).filter((t) => Math.abs(t.y - ts[cap].y) < 1)[0] || ts[cap];
      return ts.filter((t) => t.size === 7 && t.x < fig.x + 44 && t.y < ts[cap].y - 10 && t.y > ts[cap].y - 230 && /^[-\u2212]?[\d,]*\.?\d+$/.test(t.s))
        .map((t) => Number(t.s.replace(/,/g, '').replace('\u2212', '-'))).filter((v) => Math.abs(v) < 1800);   // not the first year under the axis
    }
    return null;
  };
  for (const paper of ['letter', 'a4']) {
    const fx = F5.runs.fx.results, rv = F5.runs.reviews.results;
    const rf = check4('final evaluation: FX, ' + paper, f5Inp('fx'), paper, { name: 'fx_usd_cad' });
    const bins = fx.charts.filter((c) => c.kind === 'bars')[0];
    const runs = rf.texts.reduce((a, ts) => a.concat(ts.map((t) => t.s)), []);
    const lost = bins.series.filter((s) => runs.indexOf(s.label) < 0).map((s) => s.label);
    expect('final evaluation FX ' + paper, bins.series.length === 12 && !lost.length, 'the histogram does not print its 12 bins, each label whole: missing ' + JSON.stringify(lost));
    const ax = axisOf(rf, 'Long-run');
    const ys = fx.charts.filter((c) => c.kind === 'line')[0].series[0].y;
    expect('final evaluation FX ' + paper, ax && ax.length >= 3 && ax.indexOf(0) < 0 && Math.min.apply(null, ax) > 1 && Math.min.apply(null, ax) <= Math.min.apply(null, ys) &&
      Math.max.apply(null, ax) >= Math.max.apply(null, ys), 'the trend line\'s value axis does not span its data (' + Math.min.apply(null, ys).toFixed(3) + ' to ' +
      Math.max.apply(null, ys).toFixed(3) + '): ticks ' + JSON.stringify(ax));
    const t = plainText(body(rf)).replace(/\s+/g, ' ');
    expect('final evaluation FX ' + paper, t.indexOf(plainText('out of 100: ' + fx.health_explain)) >= 0 && t.indexOf(plainText('what set the health score ' + fx.health_explain)) >= 0,
      'the health tile or the data table does not say what set the score: ' + fx.health_explain);
    // "About this report" (on the cover when it fits, else first after it) and Appendix A's "How this report was made"
    const about = (x) => x.indexOf('about this report');
    expect('final evaluation FX ' + paper, about(t) >= 0 && t.indexOf(plainText(fx.plan_row_drops[0].notice), about(t)) >= 0 &&
      bodyBetween(rf, 'APPENDIX A', 'APPENDIX B').indexOf(plainText(fx.plan_row_drops[0].notice)) >= 0,
    '"About this report" does not say the plan set aside ' + fx.plan_row_drops[0].rows + ' rows');
    const ap = bodyBetween(rf, 'APPENDIX A', 'APPENDIX B');
    expect('final evaluation FX ' + paper, ap.indexOf('rows the ai plan set aside') >= 0 && ap.indexOf(plainText(fx.plan_row_drops[0].text)) >= 0 &&
      ap.indexOf('rows in the file 3,526') >= 0 && ap.indexOf('rows the ai plan set aside 569 (16.1%)') >= 0, 'Appendix A does not list the step that set rows aside: ' + ap.slice(0, 600));
    const rr = check4('final evaluation: reviews, review_text kept, ' + paper, f5Inp('reviews'), paper, { name: 'reviews_synthetic', kept: ['review_text'] });
    const tr = plainText(body(rr)).replace(/\s+/g, ' ');
    expect('final evaluation reviews ' + paper, tr.indexOf(plainText('out of 100: ' + rv.health_explain)) >= 0 && /^0 because the newest row is 3\.5 years old \(the timeliness check\)/.test(rv.health_explain),
      'the health tile does not say the score is 0 because the newest row is 3.5 years old: ' + rv.health_explain);
    expect('final evaluation reviews ' + paper, about(tr) >= 0 && tr.indexOf(plainText(rv.plan_row_drops[0].notice), about(tr)) >= 0 && /none of these rows duplicates a kept row/.test(rv.plan_row_drops[0].notice),
      '"About this report" does not say the plan set aside the department, its reason and the engine\'s check: ' + tr.slice(0, 500));
    const apr = bodyBetween(rr, 'APPENDIX A', 'APPENDIX B');
    expect('final evaluation reviews ' + paper, apr.indexOf(plainText(rv.plan_row_drops[0].text)) >= 0 && /compared on every column but department/.test(apr),
      'Appendix A does not give the step, its reason and the engine\'s check: ' + apr.slice(0, 600));
    // the department chart keeps every group the engine compared, the lowest among them
    const dept = W.model(f5Inp('reviews')).parts.reduce((a, p) => a.concat(p.blocks), []).filter((b) => b.type === 'chart' && b.chart && /by department/.test(b.chart.title || ''))[0];
    const want = rv.analyses.filter((a) => /by department/.test(a.title))[0];
    const low = (want.sentence.match(/ and (.+?) lowest \(/) || [])[1];
    expect('final evaluation reviews ' + paper, dept && dept.chart.series.length === 5 && dept.chart.series.some((s) => s.label === low),
      'the department chart does not carry its 5 groups and the lowest one the text names (' + low + ')');
  }
  // a share link's copy of the results (NLReportPdf.shareResults, the page's) keeps every step of the plan that set rows
  // aside, what set the health score and the plan's row noun (integration pass, 1 Oct 2026: the copy lost the first two,
  // so a shared PDF said nothing of them), whatever its cap; the worker's shared PDF prints them
  {
    const rv = F5.runs.reviews.results, sr = W.shareResults(Object.assign({}, rv, { row_noun: 'review' }));
    const want = rv.plan_row_drops.map((d) => ({ rows: d.rows, of: d.of, pct: d.pct, text: d.text, notice: d.notice }));
    expect('final evaluation, a shared copy', sr && sr.partial === true && sr.row_noun === 'review' && sr.health_explain === rv.health_explain &&
      JSON.stringify(sr.plan_row_drops) === JSON.stringify(want), 'shareResults does not keep the plan\'s set-aside rows, the health explanation or the row noun: ' +
      JSON.stringify(sr && { row_noun: sr.row_noun, health_explain: sr.health_explain, drops: sr.plan_row_drops }).slice(0, 300));
    const tight = W.shareResults(Object.assign({}, rv, { row_noun: 'review' }), 4000);
    expect('final evaluation, a shared copy', !tight || (tight.row_noun === 'review' && JSON.stringify(tight.plan_row_drops) === JSON.stringify(want)),
      'under a tight cap shareResults cut the disclosure before the scenario items');
    const bad = ['Reviews', 'two words', 'ab', 7].map((x) => W.shareResults(Object.assign({}, rv, { row_noun: x })));
    expect('final evaluation, a shared copy', bad.every((x) => x && !('row_noun' in x)), 'shareResults kept a row noun that is not one lowercase word of 3 to 20 letters');
    const rs = check4('final evaluation: reviews, a shared copy, letter', Object.assign(f5Inp('reviews'), { results: sr, shared: true }), 'letter',
      { name: 'reviews_synthetic', kept: ['review_text'] });
    const ts = plainText(body(rs)).replace(/\s+/g, ' '), apx = bodyBetween(rs, 'APPENDIX A', 'APPENDIX B');
    expect('final evaluation, a shared copy', ts.indexOf(plainText(rv.plan_row_drops[0].notice)) >= 0 && apx.indexOf(plainText(rv.plan_row_drops[0].text)) >= 0 &&
      ts.indexOf(plainText(rv.health_explain)) >= 0, 'the shared PDF does not say the plan set rows aside or what set the health score');
  }

  // ---- the chart registry (wave 2B, 30 Sep 2026): the records of tools/fixtures/viz/spec.json drawn by kind
  // (fixtures/report-pdf/viz-results.json and viz-response.json, made by make_viz_fixtures.py): the 10-chart report
  // (every kind, 12 long segment labels, suppressed cells, a 10 x 12 diverging grid, a Pareto whose k80 lies beyond its
  // bars, a record degraded to 'table'), the edge cases (negative totals, all-empty and 1-row heatmaps, non-Latin
  // labels, 24 YYYY-MM columns, 14 long column labels, a short Pareto and waterfall, a page record, an analysis's bars,
  // a kind no reader knows, a heatmap whose tier grid is the wrong shape, a kind with no table), the contribution
  // waterfall the AI placed nowhere, and a share link's copy, each Letter and A4
  const VR = J('viz-results.json'), VP = J('viz-response.json');
  const vizRes = (charts) => Object.assign({}, VR.results, { charts });
  const vizInp = (name, res, o) => ({ report: VP[name].report, sources: VP[name].sources, model: VP[name].model, repaired: VP[name].repaired,
    removed_figures: VP[name].removed_figures, results: res, kept: [], name: NAME, showName: false, date, goal: res && res.goal, ...(o || {}) });
  const recOf = (c) => (c && c.type === 'viz' && c.data ? c.data : c);
  const DRAWN = ['waterfall', 'heatmap', 'dot_range', 'pareto', 'slope'];
  const vizRun = (label, input, paper, charts, also, extra) => {   // also: records drawn without a marker (after the placed ones); extra: more options for checkPdf
    const m = W.model(input), trace = [];
    const u8 = W.build(m, { paper, trace });
    const same = Buffer.compare(Buffer.from(u8), Buffer.from(W.build(W.model(input), { paper }))) === 0;
    hashes[label + ' [' + paper + ']'] = sha(u8);
    const f = path.join(tmp, label.replace(/[^a-z0-9]+/gi, '-') + '-' + paper + '.pdf');
    writeFileSync(f, u8);
    const r = checkPdf(u8, Object.assign({ file: f, a4: paper === 'a4', forbid, name: STEM, removed: input.repaired, source: String(input.report) + JSON.stringify(charts) }, extra || {}));
    ok = report(label + ', ' + paper + ' (' + (u8.length / 1024).toFixed(0) + ' KB)', r) && ok;
    const v = vizCheck(r, trace);
    const n = v.n;
    console.log((v.ok ? 'PDF PASS ' : 'PDF FAIL ') + label + ', ' + paper + ': ' + n.figs + ' figures and ' + n.tables + ' records drawn as tables; ' + n.marks + ' marks and ' + n.words +
      ' words checked against their figure boxes and each other; ' + n.cells + ' heatmap cells (' + n.cellText + ' with their text, ' + n.glyphOnly + ' the glyph alone) against their cells');
    v.fails.forEach((x) => console.log('  - ' + x));
    ok = v.ok && ok;
    expect(label + ', ' + paper, same, 'the trace changed the bytes');
    // every record placed is a figure or a table, never an empty box; an unknown kind or unreadable data is a table
    const placed = [...String(input.report).matchAll(/\[CHART:(\d+)\]/g)].map((x) => recOf(charts[Number(x[1]) - 1])).filter((c) => c && !['line', 'bars', 'scatter'].includes(c.kind)).concat(also || []);
    expect(label + ', ' + paper, trace.length === placed.length, placed.length + ' viz records placed, but ' + trace.length + ' drawn');
    const T = plainText(r.text);
    placed.forEach((c, i) => {
      const t = trace[i] || {}, drawn = DRAWN.includes(c.kind) && t.as !== 'table';
      const title = plainText(c.title || '').slice(0, 30);
      if (t.as === 'table') {
        expect(label, !DRAWN.includes(c.kind) || c.kind === 'heatmap', 'a ' + c.kind + ' record was drawn as a table: ' + c.title);
        expect(label, t.table ? T.indexOf('table ' + t.table + '. ' + title) >= 0 || T.replace(/\s+/g, ' ').indexOf('table ' + t.table + '. ' + title) >= 0 : /could not be drawn, and it carries no table/.test(T),
          'the record "' + c.title + '" (kind ' + c.kind + ') is not its table');
      } else expect(label, drawn && t.kind === c.kind && plainText(t.title) === plainText(c.title), 'record ' + (i + 1) + ' (' + c.kind + ') was not drawn as its kind: ' + JSON.stringify([t.kind, t.as]));
      // the table view: after its figure (12 rows at most), else in Appendix A with the note saying so
      if (drawn && c.table && c.table.rows && c.table.rows.length) {
        const at = T.replace(/\s+/g, ' ').indexOf('figure ' + t.fig + '. ');
        const rest = T.replace(/\s+/g, ' ').slice(at);
        const next = rest.search(/table \d+\. /), app = rest.indexOf('the tables of the figures');
        if (c.table.rows.length <= 12) expect(label, next >= 0 && rest.slice(next).replace(/^table \d+\. /, '').indexOf(title) === 0 && (app < 0 || next < app), 'Figure ' + t.fig + '\'s table view does not follow it: ' + c.title);
        else expect(label, /is in appendix a/.test(rest.slice(0, 2000)) && app >= 0 && rest.slice(app).indexOf(title) >= 0, 'Figure ' + t.fig + '\'s table (' + c.table.rows.length + ' rows) is not in Appendix A: ' + c.title);
      }
      // a heatmap's legend is the engine's own words
      if (drawn && c.kind === 'heatmap') (c.data.legend || []).forEach((x) => expect(label, T.indexOf(plainText(x.text)) >= 0, 'the legend line "' + x.text + '" is not printed'));
    });
    // pagination: a heading directly over a figure is on the figure's page (the figure itself is whole: its box above)
    const vb = [];
    m.parts.forEach((p) => p.blocks.forEach((b, i) => { if (b.type === 'chart' && b.chart && (b.chart.data || !['line', 'bars', 'scatter'].includes(b.chart.kind)) && b.chart.kind && !['line', 'bars', 'scatter'].includes(b.chart.kind)) vb.push(p.blocks[i - 1]); }));
    let headed = 0;
    vb.forEach((h, i) => {
      const t = trace[i];
      if (!h || h.type !== 'h2' || !t || t.as) return;
      headed++;
      expect(label + ', ' + paper, plainText(r.texts[t.page].map((q) => q.s).join(' ')).indexOf(plainText(h.text).slice(0, 24)) >= 0, 'the heading "' + h.text + '" is not on the page of Figure ' + t.fig + ' under it');
    });
    return { r, trace, T, headed };
  };
  const vizAll = [];
  for (const paper of ['letter', 'a4']) {
    const ten = vizRun('viz: ten charts', vizInp('ten', vizRes(VR.results.charts)), paper, VR.results.charts);
    const edge = vizRun('viz: edge cases', vizInp('edge', vizRes(VR.edge.charts)), paper, VR.edge.charts);
    vizAll.push(ten, edge);
    expect('viz edge, ' + paper, edge.headed >= 3, 'only ' + edge.headed + ' figures sit right under a heading: the pagination check proves little');
    // the unknown kinds: the "sankey" (no reader knows it), the heatmap whose tier grid is the wrong shape and the radar
    // with no table are tables, each note saying why
    expect('viz edge, ' + paper, /this pdf does not draw a chart of the kind "sankey"/.test(edge.T) && /shown as its table: its drawing data could not be read/.test(edge.T) &&
      /five measures of each region on a radar: this chart could not be drawn, and it carries no table/.test(edge.T), 'an unknown kind or unreadable data is not said to be shown as its table');
    expect('viz edge, ' + paper, edge.trace.filter((t) => t.as === 'table').map((t) => t.kind).join(',') === 'sankey,heatmap,radar', 'the tables drawn: ' + edge.trace.filter((t) => t.as === 'table').map((t) => t.kind).join(','));
    // non-Latin labels are placeholders, the same one in the figure and its table
    expect('viz edge, ' + paper, /\[name 1\] 18,420/.test(edge.T.replace(/ /g, ' ')) && /\[name 1\] 18,420 21,960/.test(edge.T.replace(/ /g, ' ').replace(/\s+/g, ' ')), 'the non-Latin city names are not the same placeholders in the slope and its table');
    // the contribution waterfall the AI did not place: Part 1 draws it under "Where the change sits", above the table
    const auto = vizRun('viz: the waterfall the AI placed nowhere', vizInp('auto', vizRes(VR.auto.charts)), paper, VR.auto.charts, VR.auto.charts);
    const p1 = between(auto.r, 'PART 1', 'PART 2').replace(/\s+/g, ' ');
    const fa = p1.indexOf('figure 1. where the change in revenue came from, by region'), ta = p1.indexOf('where the change in total revenue came from');
    expect('viz auto, ' + paper, /where the change sits/.test(p1) && fa >= 0 && ta > fa && auto.trace.length === 1 && auto.trace[0].kind === 'waterfall',
      'Part 1 does not draw the unplaced contribution waterfall above the engine\'s table');
    // ... also from results.viz (the engine's rep.viz) when the charts do not carry it; and never when the AI placed one
    const fromViz = W.model(vizInp('auto', Object.assign({}, VR.results, { charts: [], viz: { charts: VR.auto.charts } })));
    const sits = (mm) => { const bs = mm.parts[0].blocks, i = bs.findIndex((b) => b.type === 'h2' && b.id === 'p1-sits'); return i < 0 ? [] : bs.slice(i + 1).filter((b) => b.type === 'chart'); };
    expect('viz auto, ' + paper, sits(fromViz).length === 1 && sits(fromViz)[0].chart.chart === 'contribution_waterfall' && sits(W.model(vizInp('ten', vizRes(VR.results.charts)))).length === 0,
      'the contribution waterfall is not drawn under "Where the change sits" from results.viz, or is drawn there beside the AI\'s own');
    // a share link's copy: the trimmed results (no charts) and the share's stored charts: the same figures
    const shared = vizRun('viz: a shared copy', vizInp('ten', W.shareResults(vizRes(VR.results.charts)), { shared: true, charts: VR.results.charts, kept: [] }), paper, VR.results.charts);
    const sig = (x) => JSON.stringify(x.trace.map((t) => [t.as || t.kind, t.title, t.layout || '']));
    expect('viz share, ' + paper, sig(shared) === sig(ten), 'the shared copy does not draw the same figures as the full results');
  }
  // every kind drawn, in each of its layouts; heatmap cells with their text and with the glyph alone
  const layouts = new Set(vizAll.flatMap((x) => x.trace.filter((t) => !t.as).map((t) => t.kind + ' ' + t.layout)));
  const want = ['waterfall vertical', 'waterfall horizontal', 'heatmap grid', 'heatmap transposed', 'dot_range wide', 'pareto vertical', 'pareto horizontal', 'slope wide'];
  const missing = want.filter((k) => !layouts.has(k));
  console.log((missing.length ? 'PDF FAIL ' : 'PDF PASS ') + 'every draw kind in each of its layouts (' + want.join(', ') + ')' + (missing.length ? ': missing ' + missing.join(', ') : ''));
  ok = !missing.length && ok;
  const cellsOf = (pred) => vizAll.reduce((a, x) => a + x.trace.reduce((b, t) => b + (t.cells || []).filter(pred).length, 0), 0);
  expect('viz cells', cellsOf((c) => c.text) > 0 && cellsOf((c) => !c.text && c.s) > 0, 'no heatmap cell has its text, or none the glyph alone');

  // the chart review (30 Sep 2026): a value of +/-1e308 is never placed (its slope is shown as its table, and no "NaN"
  // or "Infinity" reaches the page's operators, where the writer threw or wrote them), and a label of symbols only (an
  // emoji, which WinAnsi cannot show and printed as nothing) reads "[label 1]", the same in the figure and its table
  {
    const idx = (k) => VR.results.charts.findIndex((c) => recOf(c) && recOf(c).kind === k);
    const charts = JSON.parse(JSON.stringify(VR.results.charts));
    const sl = recOf(charts[idx('slope')]), dr = recOf(charts[idx('dot_range')]);
    sl.data.rows[0].a = 1e308; sl.data.rows[0].b = -1e308;
    const was = dr.data.rows[1].label, box = String.fromCodePoint(0x1F4E6);
    dr.data.rows[1].label = box; dr.table.rows.forEach((row) => { if (row[0] === was) row[0] = box; });
    for (const paper of ['letter', 'a4']) {
      const label = 'viz: extreme values and a symbol-only label', input = vizInp('ten', vizRes(charts)), trace = [];
      let u8 = null;
      try { u8 = W.build(W.model(input), { paper, trace }); } catch (e) { expect(label + ', ' + paper, false, 'the writer threw: ' + e.message); continue; }
      hashes[label + ' [' + paper + ']'] = sha(u8);
      const f = path.join(tmp, 'viz-extremes-' + paper + '.pdf');
      writeFileSync(f, u8);
      const r = checkPdf(u8, { file: f, a4: paper === 'a4', forbid, name: STEM, removed: input.repaired, source: String(input.report) + JSON.stringify(charts) });
      ok = report(label + ', ' + paper, r) && ok;
      const v = vizCheck(r, trace);
      v.fails.forEach((x) => console.log('  - ' + x));
      ok = v.ok && ok;
      const T = plainText(r.text).replace(/\s+/g, ' ');
      const ts = trace.find((t) => plainText(t.title) === plainText(sl.title)), td = trace.find((t) => plainText(t.title) === plainText(dr.title));
      expect(label + ', ' + paper, !/NaN|Infinity/.test(Buffer.from(u8).toString('latin1')), '"NaN" or "Infinity" is written into the page');
      expect(label + ', ' + paper, ts && ts.as === 'table', 'the slope from 1e308 to -1e308 was not shown as its table: ' + JSON.stringify(ts && [ts.kind, ts.as]));
      expect(label + ', ' + paper, td && !td.as && (T.match(/\[label 1\] \(/g) || []).length >= 1 && (T.match(/\[label 1\]/g) || []).length >= 2,
        'the emoji label does not read "[label 1]" in the figure and its table');
      console.log('PDF PASS ' + label + ', ' + paper + ': checked');
    }
  }

  // ---- wave 4, track B (6 Oct 2026): a statistical table read by its structure. retail-results.json is the packed engine's
  // results on Statistics Canada table 20-10-0056-01 (make_retail.py: planner off, 36,735 rows); retail-response.json is a
  // HAND-WRITTEN TEST FIXTURE of the AI's report, never a live reply. The PDF now leads with what its headline measures
  // (the estimand: the 12-month totals, the change and its units, "described, not tested", the sum-checks with their counts,
  // what was left out and why), says the engine's grade is a PROCESS grade (the chip and the note), draws the contribution
  // waterfall's not-allocated step and each part's own change, prints the forecast's back-test beside its range (and says
  // "not trusted" when it failed, or why a dropped forecast is dropped), names the categories the engine released, and
  // opens on the estimand's headline instead of the core's forecast sentence. Every word is read from the engine's own record.
  {
    const RT = J('retail-results.json'), RTR = RT.results, RTP = J('retail-response.json');
    const E = RTR.estimand, INF = E && E.inference, AUD = RTR.forecast && RTR.forecast.audit;
    const L0 = 'retail fixture';
    expect(L0, !!(E && INF && INF.mode === 'official_aggregate' && INF.grade_label && E.sum_checks.length === 2 && AUD && AUD.status === 'passes' && AUD.trusted === true && RTR.privacy.released.length === 1) &&
      RT.engine_snapshot === '19ec81d19e0d' && RTR.story.headline === RTR.goal, 'the fixture is not the retail run with an estimand, a process grade, two sum-checks, a passed back-test and a released category');
    const northwest = RTR.scenarios.items.filter((x) => /northwest/i.test(x.id));
    expect(L0, northwest.length === 3 && RTR.scenarios.items.length === 53 && /22 of the 75 scenario items are left out/.test(RTR.scenarios.refused[0]),
      'the payload budget did not keep the part that moved against the change (the Northwest Territories) and 53 of the 75 scenario items');
    const rtInp = (res, o) => inp(res, RTP.report, Object.assign({ name: RT.name, sources: [], model: RTP.model, repaired: RTP.repaired, removed_figures: RTP.removed_figures || [], charts: res.charts, tables: res.tables }, o || {}));
    const wordsBetween = (r, word, a, b) => { let on = false, n = 0; r.texts.forEach((ts) => ts.forEach((t) => { if (t.s === a) on = true; if (t.s === b) on = false; if (on && t.s === word) n++; })); return n; };
    const noRaw = (t) => (String(t).match(/(?<![\w,.−-])\d{9,}(?![\w,])/g) || []).slice(0, 3).join(', ');
    const copy = (x) => JSON.parse(JSON.stringify(x));
    for (const paper of ['letter', 'a4']) {
      const label = 'retail: a statistical table', lp = label + ', ' + paper;
      const input = rtInp(RTR), { r, trace } = vizRun(label, input, paper, RTR.charts, null, { refs: false, name: 'retail_sales_provinces' });
      const T = sp(r.text), cover = sp(r.texts[0].map((q) => q.s).join(' '));
      // the headline is the estimand's (what, where, the window, the change in the table's units), not the core's forecast sentence
      expect(lp, /^Total retail sales, Canada, 12 months to Jul 2026: \+3\.5% \(\$864\.0B\) in the published totals$/.test(RTR.story.headline), 'the results\' headline is not the estimand\'s: ' + RTR.story.headline);
      expect(lp, cover.indexOf(sp(RTR.story.headline)) >= 0, 'the cover does not carry the estimand\'s headline');
      expect(lp, r.text.indexOf('73,046,640,000') < 0 && !/forecast at/i.test(r.text), 'the core\'s forecast sentence ("... forecast at 73,046,640,000") reaches the PDF');
      expect(lp, !noRaw(r.text), 'a raw figure of 9 or more digits is printed: ' + noRaw(r.text));
      // the estimand panel: first in the executive summary, before the key figures
      const iEx = r.text.indexOf('EXECUTIVE SUMMARY'), iEs = r.text.indexOf('WHAT THIS REPORT MEASURES'), iHl = r.text.indexOf('HEADLINE INSIGHT');
      expect(lp, iEx >= 0 && iEs > iEx && iHl > iEs, 'the estimand panel is not the first thing in the executive summary (before the key figures)');
      const EV = sp(between(r, 'WHAT THIS REPORT MEASURES', 'HEADLINE INSIGHT'));
      expect(lp, EV.indexOf(sp(E.text.split(';')[0])) >= 0 && EV.indexOf('dollars (file in thousands') >= 0 && EV.indexOf('12-month totals aug 2025-jul 2026 vs aug 2024-jul 2025') >= 0,
        'the panel does not say what is measured, in what units, over which months: ' + EV.slice(0, 260));
      let from = 0;
      const order = ['prior', 'latest', 'change', 'change_pct'].map((k) => { const i = EV.indexOf(sp(E.figures[k].text), from); if (i >= 0) from = i + 1; return i; });
      expect(lp, order.every((i) => i >= 0) && /12 months before.*latest 12 months.*change.*change, %/.test(EV), 'the panel does not print the prior, the latest, the change and the change % in that order: ' + order);
      expect(lp, EV.indexOf('described, not tested') >= 0, 'a published total is not said to be described, not tested');
      E.sum_checks.forEach((c) => {
        const k = EV.indexOf('adds up: ' + sp(c.total) + ' = the ' + c.parts + ' parts of ' + sp(c.dim) + ' on ' + c.within_tolerance + ' of ' + c.complete_cells + ' months, largest gap ' + sp(c.max_residual.text));
        expect(lp, k >= 0 && EV.slice(k).indexOf('not allocated to a part in the latest 12 months: ' + sp(c.unallocated_latest.text)) >= 0, 'the sum-check of ' + c.total + ' is not stated with its counts, its largest gap and what is not allocated');
      });
      E.excluded.forEach((x) => expect(lp, EV.indexOf(sp(x.what) + ': ' + sp(x.why)) >= 0, 'what was left out is not listed with its reason: ' + x.what));
      expect(lp, EV.indexOf('the engine chose this slice') >= 0 && EV.indexOf('how it is known') >= 0 && EV.indexOf(sp(INF.revisions)) >= 0 && EV.indexOf('(statistics canada)') >= 0, 'the slice\'s source and how the figure is known are not stated');
      // the process grade: the chip says what the grade is a grade of, the note says it is not a test of the total, the tiles repeat it
      expect(lp, EV.indexOf(sp(INF.grade_label)) >= 0 && wordsBetween(r, 'NOT ENOUGH DATA', 'WHAT THIS REPORT MEASURES', 'HEADLINE INSIGHT') === 1, 'the process-grade chip or its note is missing from the panel: ' + EV.slice(-300));
      const tiles = sp(between(r, 'HEADLINE INSIGHT', 'In brief'));
      expect(lp, (tiles.match(/process grade, not a test of the published total/g) || []).length >= 2 && tiles.indexOf('(described)') >= 0, 'the key figures do not say the grade is a process grade, or that the change is described: ' + tiles.slice(0, 300));
      const fnd = sp(between(r, 'Every finding and its grade', 'The tables of the figures'));
      expect(lp, /process grade/.test(fnd) && fnd.indexOf('not trusted') < 0, 'the findings table does not mark the change\'s grade as a process grade');
      expect(lp, T.indexOf('the grade beside it, not enough data, grades the month-to-month noise') >= 0, 'the AI report\'s own words about the process grade are lost');
      // the not-allocated step: its own row in the waterfall (a hatched step in the figure) and in its table, with each part's own change
      const f1 = T.slice(T.indexOf('figure 1. '), T.indexOf('figure 2. '));
      expect(lp, f1.indexOf('not allocated: suppressed cells') >= 0 && f1.indexOf('the unallocated step is the total less its published parts') >= 0, 'Figure 1 does not show the not-allocated step');
      const apx = T.slice(T.indexOf('the tables of the figures'));
      expect(lp, /not allocated: suppressed cells \$0\.0b/.test(apx) && /step contribution own change/.test(apx) && /ontario \+\$10\.2b \+3\.2%/.test(apx), 'the waterfall\'s table has no not-allocated row or no "Own change" column');
      const wf = trace.filter((t) => t.kind === 'waterfall');
      expect(lp, wf.length === 1 && !wf[0].as, 'the contribution waterfall is not drawn as a figure: ' + JSON.stringify(trace.map((t) => [t.kind, t.as])));
      // the forecast's back-test, beside the range and in the engine's words
      const p3 = sp(between(r, 'PART 3', 'PART 4'));
      expect(lp, p3.indexOf('the forecast and how its range held when back-tested') >= 0 && p3.indexOf('back-test of the range shown') >= 0 && p3.indexOf(sp(AUD.label) + ' (the back-test passed; trusted)') >= 0 &&
        p3.indexOf('a passed back-test says the range held often enough') >= 0 && p3.indexOf('not trusted') < 0, 'Part 3 does not print the back-test line (held k of n by horizon) beside the forecast: ' + p3.slice(-700));
      expect(lp, /\$73\.0b \$72\.4b to \$77\.7b/.test(p3) && p3.indexOf('the model: repeat the same month from last year') >= 0, 'the forecast table or its model is missing');
      // the categories the engine read as categories, in the method and data quality
      const apxA = sp(between(r, 'APPENDIX A', 'APPENDIX B'));
      expect(lp, apxA.indexOf('columns read as categories, not personal data') >= 0 && apxA.indexOf(sp(RTR.privacy.released[0].text)) >= 0, 'the released category is not in the method and data quality');
      expect(lp, apxA.indexOf('each total checked against its parts') >= 0 && /geo canada 13 265 265 \$3\.0k \$0\.0b/.test(apxA) && apxA.indexOf('the publisher\'s flags in the file: 5,430 rows suppressed') >= 0, 'the sum-check table or the publisher\'s flags are missing from Appendix A');
      if (paper === 'letter') {
        // the name of the file the visitor sent is nowhere
        expect(lp, r.hay.indexOf('retail_sales_provinces') < 0, 'the file\'s name is in the PDF');
        // a back-test that failed: "not trusted" beside the engine's grade, in the callout, the sentence and the findings table
        const bad = copy(RTR);
        Object.assign(bad.forecast.audit, { status: 'fails', trusted: false, grade_label: 'the engine\'s grade (the back-test failed it)',
          label: 'back-tested: held 9 of 23 at 1 month, 8 of 21 at 3 months, 6 of 18 at 6 months, 3 of 12 at 12 months; the model is the seasonal-naive benchmark itself' });
        const rb = check4('retail: a failed back-test', rtInp(bad), 'letter', { name: 'retail_sales_provinces', refs: false });
        const pb = sp(between(rb, 'PART 3', 'PART 4')), fb = sp(between(rb, 'Every finding and its grade', 'The tables of the figures'));
        expect('retail failed back-test', pb.indexOf('back-test: not trusted') >= 0 && pb.indexOf('held 9 of 23 at 1 month') >= 0 && pb.indexOf('(the back-test failed; not trusted)') >= 0 &&
          pb.indexOf('the engine\'s grade of the forecast stands, but it is not trusted: the back-test of its range failed') >= 0 && fb.indexOf('not trusted: back-test failed') >= 0,
        'a failed back-test is not marked "not trusted" beside the grade: ' + pb.slice(-500) + ' | ' + fb.slice(-200));
        // a back-test that neither passed nor failed says so
        const unc = copy(RTR); unc.forecast.audit.status = 'unclear'; unc.forecast.audit.trusted = false;
        const ru = check4('retail: an unclear back-test', rtInp(unc), 'letter', { name: 'retail_sales_provinces', refs: false });
        expect('retail unclear back-test', sp(between(ru, 'PART 3', 'PART 4')).indexOf('(the back-test neither passed nor failed it; not trusted)') >= 0, 'an inconclusive back-test is not said to be neither passed nor failed');
        // a row-count forecast the table's layout fixes: one line that says why, no number and no back-test
        const dr = copy(RTR);
        dr.forecast = { row_forecast_dropped: true, reason: 'the table\'s layout fixes the rows a month, so a forecast of them would forecast the layout' };
        const rd = check4('retail: a dropped row forecast', rtInp(dr), 'letter', { name: 'retail_sales_provinces', refs: false });
        const pd = sp(between(rd, 'PART 3', 'PART 4'));
        const blk = pd.slice(pd.indexOf('the table\'s layout fixes the rows a month, so a forecast of them would forecast the layout'));     // the engine's line (the AI's own paragraph above it still speaks of the range)
        expect('retail dropped forecast', blk.length < pd.length && pd.indexOf('how its range held when back-tested') < 0 && pd.indexOf('back-test of the range shown') < 0 &&
          pd.indexOf('the engine\'s forecast:') < 0 && !/\$7\d\.\db/.test(blk.slice(0, 400)), 'a dropped forecast does not say why in one line, or shows a number or a back-test: ' + pd.slice(-600));
        // no estimand (an older report, a table the structure did not read): no panel, no chip, and the report still holds together
        const none = copy(RTR); none.estimand = null; none.structure = null;
        const rn = check4('retail: no estimand', rtInp(none), 'letter', { name: 'retail_sales_provinces', refs: false });
        expect('retail no estimand', rn.text.indexOf('WHAT THIS REPORT MEASURES') < 0 && sp(between(rn, 'HEADLINE INSIGHT', 'In brief')).indexOf('process grade') < 0, 'a report with no estimand still draws the estimand panel or a process-grade note');
      }
    }
    // the trend test, as the adapter's compact record gives it (FX; the same results as the history checks)
    const pr2 = (x) => Number(x).toPrecision(2).replace(/\.?0+$/, '');
    const tt = FXR.analyses.filter((a) => a.test)[0], rf = check4('FX history: the trend test\'s size', fxInp(FXR, { sources: srcs(2) }), 'letter', { name: 'fx_usd_cad' });
    const tl = sp(between(rf, 'The trend test and its size', 'Every finding and its grade'));
    expect('FX trend test', !!tt && Object.keys(tt.test).sort().join() === 'n,name,p,p_random_walk,size,verdict' && Buffer.byteLength(JSON.stringify(tt.test), 'utf8') <= 250 && tt.test.verdict === 'no_settled_direction',
      'the forwarded trend test is not the compact record: ' + JSON.stringify(tt && tt.test));
    expect('FX trend test', tl.indexOf('on ' + tt.test.n + ' yearly values; p ' + pr2(tt.test.p) + ', random-walk screen ' + pr2(tt.test.p_random_walk) + '; the verdict is no settled direction.') >= 0 &&
      tl.indexOf('the test found a trend in ' + (100 * tt.test.size.simulated).toFixed(1) + '% at its 5% level and claimed a direction in ' + (100 * tt.test.size.claims).toFixed(1) + '%; the newey-west range used before found one in ' + (100 * tt.test.size.newey_west).toFixed(1) + '%') >= 0,
    'the trend test\'s verdict, p and simulated size are not in the method section: ' + tl.slice(0, 500));
  }

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
  const mv = W.model(vizInp('ten', vizRes(VR.results.charts))), mv1 = JSON.parse(JSON.stringify(mv)); mv1.date = date;
  const eqv = page && Buffer.compare(Buffer.from(page.build(mv1, { paper: 'a4' })), Buffer.from(W.build(mv, { paper: 'a4' }))) === 0;
  console.log((eqv ? 'PDF PASS ' : 'PDF FAIL ') + 'the classic script makes the same bytes as the Node module for the 10-chart report (A4)');
  ok = eqv && ok;
  // every fixture PDF's sha256 (--hashes), and the same bytes as an earlier run's (--same-as)
  if (opt('--hashes')) { writeFileSync(opt('--hashes'), JSON.stringify(hashes, null, 2) + '\n'); console.log('(' + Object.keys(hashes).length + ' PDF hashes written to ' + opt('--hashes') + ')'); }
  if (opt('--same-as')) {
    // every PDF BEFORE.json names must be byte-identical (a case it does not name is new: listed, not judged)
    const was = JSON.parse(readFileSync(opt('--same-as'), 'utf8')), keys = Object.keys(was), fresh = Object.keys(hashes).filter((k) => !(k in was));
    const moved = keys.filter((k) => was[k] !== hashes[k]);
    const letters = keys.filter((k) => /\[letter\]$/.test(k)).length, a4s = keys.filter((k) => /\[a4\]$/.test(k)).length;
    console.log((moved.length ? 'PDF FAIL ' : 'PDF PASS ') + 'every fixture PDF (' + letters + ' Letter, ' + a4s + ' A4) is byte-identical to ' + path.basename(opt('--same-as')) +
      (moved.length ? ': ' + moved.length + ' differ or are missing: ' + moved.join('; ') : '') + (fresh.length ? ' (' + fresh.length + ' new cases not in it: ' + fresh.join('; ') + ')' : ''));
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
