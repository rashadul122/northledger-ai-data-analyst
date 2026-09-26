#!/usr/bin/env node
/* The demo's AI summaries guard and AI payload (src/js/50-try.js, window.NLTry), checked in Node
   with no browser and no network:

     node tools/check_try_guard.js

   - The template guard (§6.3 of plan/NorthLedger-Confidence-Architecture) is one block of code
     shared byte for byte with the owner's proxy (insight-proxy/src/guard.js): the copies must match.
   - T.checkTemplate passes and T.fillTemplate fills every ok case in
     insight-proxy/test/template-vectors.json, and refuses every other case with its reason, the
     same as the proxy; generated templates get the same reasons and the same filling on both sides.
     The model writes words around slots ({F1.n1}, {F1.grade}); every figure and grade on screen is
     put in by the page from the engine's findings.
   - T.checkSummaries takes the proxy's {executive, technical, rejected, unavailable} and judges each
     part on its own (the page's guard again, on what it sent): a part that passes is shown, any other
     is set aside and T.partsNote says which and why, in plain words; T.summaryHtml fills, escapes,
     cites and puts quoted values back in this browser only.
   - T.numbersIn reads numbers exactly as the proxy does (insight-proxy/test/number-vectors.json).
   - T.aiPayload sends exactly {objective, findings: [{id, claim, verdict, value}], story} inside
     the proxy's limits (40 findings, 300-character claims, 600-character story lines, 20 lines a
     list, 16384 bytes), cutting only at a word boundary.
   Exit code 1 when any check fails. */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { pathToFileURL } = require('url');

const SITE = path.join(__dirname, '..');
const PROXY = path.join(SITE, '..', 'insight-proxy');
const BEGIN = '/* ==== TEMPLATE GUARD', END = '/* ==== END TEMPLATE GUARD ==== */';
const VECTORS = [   // a copy of insight-proxy/test/number-vectors.json, used when that folder is absent
  ['1,234', ['1234']], ['1234', ['1234']], ['1234.0', ['1234']], ['12.5%', ['12.5']], ['12.50 percent', ['12.5']],
  ['$48,200.00', ['48200']], ['-3', ['3']], ['2024-06', ['2024', '6']], ['0.5, .5 and 00.50', ['0.5', '0.5', '0.5']],
  ['3,4', ['3', '4']], ['1,23', ['1', '23']], ['12,345,678.9', ['12345678.9']], ['48,200, then', ['48200']],
  ['Unit 7 in Q4', ['7', '4']], ['1.2.3', ['1.2', '0.3']], ['no digits', []],
  ['５２０００', ['52000']], ['growth²', ['2']], ['3e3 times', ['3', '3']]
].map((v) => ({ text: v[0], numbers: v[1] }));

function load() {
  const noop = () => {};
  const document = { readyState: 'loading', addEventListener: noop, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null };
  const window = { addEventListener: noop, NL: { try: { max_bytes: 25000000, max_rows: 200000, ai_proxy_url: '' } } };
  window.window = window;
  const ctx = vm.createContext({ window, document, console, TextDecoder, TextEncoder, Uint8Array, setTimeout, clearTimeout, performance });
  for (const f of ['00-core.js', '50-try.js']) vm.runInContext(fs.readFileSync(path.join(SITE, 'src', 'js', f), 'utf8'), ctx, { filename: f });
  if (!window.NLTry) throw new Error('src/js/50-try.js did not define window.NLTry');
  return window.NLTry;
}
function block(file) {
  const t = fs.readFileSync(file, 'utf8'), a = t.indexOf(BEGIN), b = t.indexOf(END);
  if (a < 0 || b < a) return null;
  return t.slice(a, b + END.length);
}

let failed = 0, ran = 0;
function check(name, fn) {
  ran++;
  try { fn(); console.log('GUARD PASS ' + name); } catch (e) { failed++; console.log('GUARD FAIL ' + name + ': ' + String(e.message || e).split('\n')[0]); }
}
function eq(a, b, msg) { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(msg + ': got ' + x + ', want ' + y); }
function ok(c, msg) { if (!c) throw new Error(msg); }
const arr = (a) => Array.from(a || []);

const REPORT = {   // the shape the adapter returns; only the fields the guard and payload read
  findings: [
    { id: 'health.score', claim: 'Data health scored 84.6 out of 100.', verdict: 'RECOMMEND', why: 'x', kind: 'data_quality', value: 84.6 },
    { id: 'rev.total', claim: 'Revenue over the file totals $1,412,380.25 across 3 regions.', verdict: 'WATCH', why: 'x', kind: 'business', value: 1412380.25 },
    { id: 'units.growth_2025', claim: 'Units grew 18.4% on the year before.', verdict: 'INSUFFICIENT', why: 'x', kind: 'business', value: 0.1843217 }
  ],
  story: { headline: 'Volume +33.3% on the year before.', what_happened: ['The file has 3,350 rows from 2022 to 2026.'],
    why: [], what_to_do: ['Fix the 82 rows set aside.'], whats_next: [], cannot_answer: ['Nothing on costs.'] }
};
const BODY = {   // what the page sends for a report that quotes a value and the file name (placeholders)
  objective: 'Which region grows?',
  findings: [
    { id: 'measure.amount.change', claim: 'Average amount rose 12.5% on the year before, to 1,234.5.', verdict: 'RECOMMEND', value: 12.5 },
    { id: 'dq.top_region', claim: "The most common region value is '[value A]', with 40% of rows in [your file].", verdict: 'WATCH', value: 40 }
  ],
  story: { headline: 'Average amount +12.5% on the year before.', what_happened: [], why: [], what_to_do: [], whats_next: [], cannot_answer: [] }
};
const RED = [{ label: 'your file\'s name', value: 'orders <b>.csv', placeholder: '[your file]' }, { label: 'a value quoted from your data', value: 'East', placeholder: '[value A]' }];
const GOOD = {
  executive: "Average amount rose {F1.n1} on the year before, to {F1.n2}; this is {F1.grade} [F1]. The most common region is '[value A]', with {F2.n1} of rows: {F2.grade} [F2].",
  technical: 'Average amount: {F1.claim} Grade {F1.grade} [F1].\n\nMost common region: {F2.claim} Grade {F2.grade} [F2].'
};

(async () => {
  let T;
  try { T = load(); } catch (e) { console.log('GUARD FAIL load: ' + e.message); console.log('GUARD CHECKS: FAILED'); process.exit(1); }

  let vectors = VECTORS, proxy = null;
  const vf = path.join(PROXY, 'test', 'number-vectors.json'), gf = path.join(PROXY, 'src', 'guard.js'), tf = path.join(PROXY, 'test', 'template-vectors.json');
  if (fs.existsSync(vf)) vectors = JSON.parse(fs.readFileSync(vf, 'utf8'));
  if (fs.existsSync(gf)) { try { proxy = await import(pathToFileURL(gf).href); } catch (e) { proxy = null; } }

  check('the template guard in 50-try.js is byte for byte the proxy\'s', () => {
    const mine = block(path.join(SITE, 'src', 'js', '50-try.js'));
    ok(mine, 'src/js/50-try.js has no TEMPLATE GUARD block');
    if (!fs.existsSync(gf)) { console.log('    (insight-proxy/src/guard.js not next to the site: skipped)'); return; }
    const theirs = block(gf);
    ok(theirs, 'insight-proxy/src/guard.js has no TEMPLATE GUARD block');
    ok(mine === theirs, 'the two copies differ (copy the block from insight-proxy/src/guard.js)');
  });

  check('numbersIn reads the shared number cases like the proxy (' + vectors.length + ' cases' + (fs.existsSync(vf) ? ', from insight-proxy' : ', built-in copy') + ')', () => {
    vectors.forEach((v) => eq(arr(T.numbersIn(v.text)), v.numbers, JSON.stringify(v.text)));
  });

  check('numbersIn agrees with the proxy\'s extractNumbers on generated text', () => {
    if (!proxy) { console.log('    (insight-proxy/src/guard.js not next to the site: compared with the shared cases only)'); return; }
    const bits = ['1', '12', '123', '1,234', '12,345.6', '1,23', '0.5', '.25', '00.50', '3%', '$9', '-4', '2024-06', ',', '.', ' ', 'x', 'Q4', '1.2.3', '7,', '1,000,000'];
    let seed = 7;
    const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    for (let i = 0; i < 3000; i++) {
      let s = '';
      for (let k = 0, n = 1 + Math.floor(rnd() * 6); k < n; k++) s += bits[Math.floor(rnd() * bits.length)];
      eq(arr(T.numbersIn(s)), proxy.extractNumbers(s), JSON.stringify(s));
    }
  });

  check('the shared template cases: every ok case passes and fills exactly, every other is refused with its reason', () => {
    if (!fs.existsSync(tf)) { console.log('    (insight-proxy/test/template-vectors.json not next to the site: skipped)'); return; }
    const v = JSON.parse(fs.readFileSync(tf, 'utf8'));
    ok(v.cases.length >= 50, 'only ' + v.cases.length + ' cases');
    v.cases.forEach((c) => {
      const p = v.payloads[c.payload], r = arr(T.checkTemplate(c.text, p, c.register));
      if (c.ok) { eq(r, [], c.why + ' was refused'); eq(T.fillTemplate(c.text, p, c.register, { cite: 'keep' }), c.fill, c.why); }
      else ok(r.indexOf(c.code) >= 0, c.why + ': wanted ' + c.code + ', got ' + JSON.stringify(r));
      if (proxy) eq(r, proxy.checkTemplate(c.text, p, c.register), 'page and proxy disagree on: ' + c.why);
    });
  });

  check('checkTemplate and fillTemplate agree with the proxy\'s on generated templates', () => {
    if (!proxy) { console.log('    (insight-proxy/src/guard.js not next to the site: compared with the shared cases only)'); return; }
    const v = JSON.parse(fs.readFileSync(tf, 'utf8')), p = v.payloads.main;
    const bits = ['{F1.n1}', '{F1.n4}', '{F2.n1}', '{F2.n3}', '{F1.grade}', '{F3.claim}', '{S2.n1}', '{NONE_CONFIRMED}', '{F9.n1}', '[F1]', '[F2]', '[S2]',
      "'[value A]'", 'Rent collected rose ', 'fell ', 'unit ', 'paid late in ', ' months', ' years', ', ', '. ', '; ', ' and ', 'because ', 'sharply ',
      'confirmed ', 'twelve ', '12 ', '%', '-', 'will ', 'certain ', 'x', '\n\n', 'I ', '{', 'con-firmed '];
    let seed = 11;
    const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    for (let i = 0; i < 3000; i++) {
      let s = '';
      for (let k = 0, n = 1 + Math.floor(rnd() * 10); k < n; k++) s += bits[Math.floor(rnd() * bits.length)];
      const reg = i % 2 ? 'technical' : 'executive';
      eq(arr(T.checkTemplate(s, p, reg)), proxy.checkTemplate(s, p, reg), JSON.stringify(s));
      eq(T.fillTemplate(s, p, reg), proxy.fillTemplate(s, p, reg), 'fill ' + JSON.stringify(s));
    }
  });

  check('every reason code the guard can return has its own words in the note a visitor reads', () => {
    const codes = arr(T.reasonCodes);
    if (proxy) eq(codes, arr(proxy.REASON_CODES), 'the page and the proxy list different reason codes');
    ok(codes.length >= 26, 'the guard lists only ' + codes.length + ' reason codes');
    codes.forEach((c) => ok(T.reasonText([c]) !== 'did not pass the check', 'no words for the reason ' + c));
  });

  check('the review\'s attacks are refused on the page too: grade transplant, wrong-finding figure, uncited sentence, a placeholder used as a figure', () => {
    const v = JSON.parse(fs.readFileSync(tf, 'utf8'));
    const main = v.payloads.main, real = v.payloads.real;
    [[main, 'Rent collected rose {F1.n1}; {F1.grade} [F1]. Seasonality: {F1.grade} [F1].'],
      [main, 'Unit {F2.n1} paid late in {F2.n2} of {F2.n3} months: {F1.grade} [F1][F2].'],
      [real, 'Monthly revenue rose {F1.n1}; {F1.grade} [F1]. The order data is clean enough to plan on.'],
      [real, 'Monthly revenue rose {F1.n1} and average order value fell {F2.n1}; each is {F1.grade} [F1] [F2].'],
      // a quoted value stands only where its own finding puts it (review, 24 Sep 2026)
      [BODY, 'Average amount rose [value A] to {F1.n2}; {F1.grade} [F1].'],
      [BODY, 'Average amount in [value A] rose {F1.n1}; {F1.grade} [F1] [F2].'],
      [BODY, 'Average amount rose {F1.n1}{F2.n1}; {F1.grade} [F1].'],
      [BODY, 'Average amount rose {F1.n1} after the region share of {F2.n1}; {F1.grade} [F1] [F2].']
    ].forEach((x) => ok(arr(T.checkTemplate(x[1], x[0], 'executive')).length > 0, 'passed: ' + x[1]));
  });

  check('checkSummaries judges each part on its own: a passing part is kept, a failing or missing one is set aside with its reasons in plain words', () => {
    const good = T.checkSummaries({ executive: GOOD.executive, technical: GOOD.technical, model: 'm' }, BODY);
    ok(good.ok && good.executive === GOOD.executive && good.technical === GOOD.technical, 'a good pair was refused: ' + JSON.stringify(good));
    eq(good.rejected, {}, 'a good pair has no rejected part'); eq(good.unavailable, {}, 'a good pair has no unavailable part');
    // the page's own guard sets aside a part the proxy passed
    const bad = T.checkSummaries({ executive: GOOD.executive, technical: GOOD.technical + ' Revenue will rise 47.3% [F1].' }, BODY);
    ok(bad.ok && bad.executive === GOOD.executive && bad.technical === null && arr(bad.rejected.technical).indexOf('digit') >= 0, JSON.stringify(bad));
    // the proxy's per-part answer: one part and the other's reason codes (unknown codes are dropped, never shown)
    const part = T.checkSummaries({ executive: GOOD.executive, technical: null, model: 'm', rejected: { technical: ['grade_word', 'confidence', '<b>x</b>'] }, unavailable: {} }, BODY);
    ok(part.ok && part.executive === GOOD.executive && part.technical === null, JSON.stringify(part));
    eq(arr(part.rejected.technical), ['grade_word', 'confidence'], 'the proxy\'s reasons for the technical part');
    const un = T.checkSummaries({ executive: null, technical: GOOD.technical, rejected: {}, unavailable: { executive: 'upstream_timeout' } }, BODY);
    ok(un.ok && un.executive === null && un.unavailable.executive === 'upstream_timeout', JSON.stringify(un));
    ok(T.checkSummaries({ executive: null, technical: GOOD.technical, unavailable: { executive: '<img src=x>' } }, BODY).unavailable.executive === 'unknown', 'an unknown error code was kept');
    const note = T.partsNote(part), unNote = T.partsNote(un);
    ok(/technical summary was set aside/.test(note) && /wrote a grade of its own; used confidence wording/.test(note) && !/<b>/.test(note), 'the note does not say which part was set aside and why: ' + note);
    ok(/executive summary could not be written/.test(unNote) && /did not answer in time/.test(unNote), 'the note does not say why a part is missing: ' + unNote);
    eq(T.partsNote(good), '', 'a note for a good pair');
    // a missing part with no reason, the old {text} shape, nothing at all: nothing passes that should not
    const miss = T.checkSummaries({ executive: GOOD.executive }, BODY);
    ok(miss.ok && miss.technical === null && arr(miss.rejected.technical).indexOf('empty') >= 0, 'a missing technical part: ' + JSON.stringify(miss));
    ok(!T.checkSummaries({ text: 'Revenue rose.' }, BODY).ok, 'the old {text} shape passed');
    ok(!T.checkSummaries(null, BODY).ok && !T.checkSummaries({ executive: ' ', technical: ' ' }, BODY).ok, 'an empty answer passed');
  });

  check('summaryHtml fills figures and grades from the engine, cites, restores quoted values and escapes', () => {
    const h = T.summaryHtml(GOOD.executive, BODY, 'executive', RED);
    ok(h.indexOf('12.5%') >= 0 && h.indexOf('1,234.5') >= 0 && h.indexOf('40%') >= 0 && h.indexOf('confirmed') >= 0 && h.indexOf('keep watching') >= 0, h);
    ok(h.indexOf('&#39;East&#39;') >= 0 && h.indexOf('[value A]') < 0, 'the quoted value was not put back: ' + h);
    ok(!/\{[FS]\d|NONE_CONFIRMED/.test(h), 'a slot was left on screen: ' + h);
    ok(/<sup[^>]*title="Average amount rose 12\.5% on the year before, to 1,234\.5\."[^>]*>\[F1\]<\/sup>/.test(h), 'the citation does not carry its claim: ' + h);
    const t = T.summaryHtml(GOOD.technical, BODY, 'technical', RED);
    ok(t.indexOf('orders &lt;b&gt;.csv') >= 0 && t.indexOf('<b>') < 0, 'the restored file name was not escaped: ' + t);
    ok((t.match(/<p>/g) || []).length === 2 && t.indexOf('CONFIRMED') >= 0 && t.indexOf('WATCH') >= 0, 'paragraphs or technical grades are wrong: ' + t);
    // a quoted value that holds a digit is put back inside the engine's own text, never in the model's words
    const red2 = [{ label: 'a value quoted from your data', value: 'North 12', placeholder: '[value A]' }];
    const h2 = T.summaryHtml(GOOD.executive, BODY, 'executive', red2), t2 = T.summaryHtml(GOOD.technical, BODY, 'technical', red2);
    const words2 = h2.replace(/ title="[^"]*"/g, '');   // the citation's title is the engine's claim: put back there
    ok(words2.indexOf('North 12') < 0 && words2.indexOf('&#39;[value A]&#39;') >= 0 && /title="[^"]*North 12/.test(h2), 'a model-placed value with a digit was put back: ' + h2);
    ok(t2.indexOf('&#39;North 12&#39;') >= 0 && t2.indexOf('[value A]') < 0, 'the value was not put back inside the engine\'s claim: ' + t2);
  });

  check('aiPayload sends exactly the allowed fields, in the report\'s own order of priority', () => {
    const p = T.aiPayload(REPORT, 'Which region grows?');
    eq(Object.keys(p), ['objective', 'findings', 'story'], 'top keys');
    p.findings.forEach((f) => eq(Object.keys(f), ['id', 'claim', 'verdict', 'value'], 'finding keys'));
    eq(Object.keys(p.story), ['headline', 'what_happened', 'why', 'what_to_do', 'whats_next', 'cannot_answer'], 'story keys');
    eq(p.findings.filter((f) => f.id === 'rev.total')[0], { id: 'rev.total', claim: REPORT.findings[1].claim, verdict: 'WATCH', value: 1412380.25 }, 'finding copied');
    ok(JSON.stringify(p.findings).indexOf('data_quality') < 0 && JSON.stringify(p.findings).indexOf('"why"') < 0, 'a finding\'s kind or why was sent');
    // the model reads the findings in the order it is sent them (F1, F2...): the business before the file's health
    eq(p.findings.map((f) => f.id), ['rev.total', 'units.growth_2025', 'health.score'], 'a v1 report: the business findings do not come first');
    // report v2: the primary claim, then the bottom line's claims in its order, the other business claims, the
    // ledger's own monitoring counts, and the data-health findings last (the live sample opened with row volume)
    const f = (id, kind, verdict) => ({ id, claim: 'Claim of ' + id + ' is 1.5.', verdict: verdict || 'WATCH', why: 'x', kind, value: 1.5 });
    const v2 = { contract_version: 2, primary_metric: { finding_id: 'measure.rent.total.change' },
      summary: { lines: [{ kind: 'moved', text: 'x', finding_ids: ['measure.rent.total.change', 'measure.rent.like_for_like.change'] }, { kind: 'act', text: 'x', finding_ids: [] },
        { kind: 'plan', text: 'x', finding_ids: ['forecast.rent.next', 'forecast.rent.next.lo80'] }], monitoring: ['measure.volume.change_pct', 'forecast.monthly_rows.next'] },
      findings: [f('health.duplicate_rows', 'data_quality', 'RECOMMEND'), f('measure.volume.change_pct', 'business'), f('measure.rent.like_for_like.change', 'business'),
        f('measure.other.change', 'business'), f('measure.rent.total.change', 'business'), f('forecast.rent.next', 'forecast'), f('forecast.monthly_rows.next', 'forecast'),
        f('clean.rows_quarantined', 'data_quality')],
      story: { headline: 'Rent +1.5%, graded WATCH; volume +1.5%, graded WATCH.', what_happened: [], why: [], what_to_do: [], whats_next: [], cannot_answer: [] } };
    eq(T.aiPayload(v2, '').findings.map((x) => x.id), ['measure.rent.total.change', 'measure.rent.like_for_like.change', 'forecast.rent.next', 'measure.other.change',
      'measure.volume.change_pct', 'forecast.monthly_rows.next', 'health.duplicate_rows', 'clean.rows_quarantined'], 'a v2 report is not sent in the order of its bottom line');
    // the story's "why" goes sentence by sentence: its first sentence (what the file covers) is short
    // enough for an executive summary to give whole, the full line is not (the sample's is 1,044 characters)
    const why = "Part of the change is in what the file covers, not in the business: in property, 'Pape' first appears in 2024-07; 'Low-Rise' first appears in 2025-03. " +
      "The monthly total of amount steps up by +87.4% in 2025-03, when 'Low-Rise' first appears: with those steps allowed for, no drift remains. " + 'Row volume steps up by +70.2% in 2025-03. '.repeat(14).trim();
    const wy = T.aiPayload(Object.assign({}, v2, { story: Object.assign({}, v2.story, { why: [why] }) }), '', true).story.why;
    eq(wy.slice(0, 2), ["Part of the change is in what the file covers, not in the business: in property, 'Pape' first appears in 2024-07; 'Low-Rise' first appears in 2025-03.",
      "The monthly total of amount steps up by +87.4% in 2025-03, when 'Low-Rise' first appears: with those steps allowed for, no drift remains."], 'the why line is not sent sentence by sentence');
    ok(wy.length === 16 && wy.every((x) => x.length <= 600 && !/…$/.test(x)), 'the why sentences were cut or dropped: ' + JSON.stringify(wy.map((x) => x.length)));
  });

  check('aiPayload fits the proxy\'s limits, cutting only at a word boundary', () => {
    const rep = JSON.parse(JSON.stringify(REPORT));
    const long = Array(80).fill('segment North grew 12,345.6 units').join(' ');
    rep.findings = Array.from({ length: 55 }, (_, i) => ({ id: 'f.' + i, claim: long, verdict: 'WATCH', why: 'w', kind: 'business', value: i }));
    rep.story.what_happened = Array.from({ length: 30 }, () => long);
    rep.story.headline = long;
    const p = T.aiPayload(rep, 'q'.repeat(500));
    const bytes = Buffer.byteLength(JSON.stringify(p), 'utf8');
    ok(p.objective.length <= 300, 'objective ' + p.objective.length);
    ok(p.findings.length <= 40 && p.findings.length > 0, 'findings ' + p.findings.length);
    ok(p.findings.every((f) => f.claim.length <= 300), 'a claim is over 300 characters');
    ok(p.story.headline.length <= 600 && p.story.what_happened.length <= 20 && p.story.what_happened.every((s) => s.length <= 600), 'story over the limits');
    ok(bytes <= 16384, 'payload is ' + bytes + ' bytes');
    // every number the payload states was stated whole by the engine (no cut-off 12,3)
    const whole = {};
    T.numbersIn(long).forEach((n) => { whole[n] = 1; });
    const parts = [];
    p.findings.forEach((f) => parts.push(f.claim));
    parts.push(p.story.headline);
    p.story.what_happened.forEach((s) => parts.push(s));
    parts.forEach((s) => T.numbersIn(s).forEach((n) => ok(whole[n], 'a cut produced the number ' + n)));
    if (proxy) { const v = proxy.validatePayload(JSON.parse(JSON.stringify(p))); ok(v.ok, "the proxy would refuse it: " + v.detail); }   // as sent: JSON
  });

  check('aiPayload sends nothing that names a withheld column', () => {
    // the shapes a withheld column reached the payload in (review of the site, 24 Sep 2026): its
    // data-health finding, a quarantine rule named after it, and story lines that name it
    const rep = {
      privacy: { flagged: [{ column: 'salary', kind: 'name', decision: 'withhold' }, { column: 'notes', kind: 'free_text', decision: 'withhold' },
        { column: 'email', kind: 'email', decision: 'keep' }] },
      health: { columns: [{ name: 'salary', quarantined_by_rule: { salary_numeric: 2 }, fixes_by_rule: {} }, { name: 'notes', quarantined_by_rule: {}, fixes_by_rule: {} },
        { name: 'amount', quarantined_by_rule: { amount_numeric: 3 }, fixes_by_rule: {} }] },
      cleaning: { fixes: [{ rule: 'pay_grade_trim', column: 'salary', count: 4, what: 'x' }] },
      findings: [
        { id: 'health.col.notes.null_like_pct', claim: '26.3% of the notes column of payroll.csv is null-like.', verdict: 'WATCH', kind: 'data_quality', value: 26.3 },
        { id: 'clean.quarantined.salary_numeric_value_is_not_a_number', claim: '2 row(s) were quarantined because salary_numeric: value is not a number.', verdict: 'WATCH', kind: 'data_quality', value: 2 },
        { id: 'clean.fixed.pay_grade', claim: 'The pay_grade_trim rule changed 4 values.', verdict: 'WATCH', kind: 'data_quality', value: 4 },
        { id: 'clean.quarantined.amount_numeric_value_is_not_a_number', claim: '3 row(s) were quarantined because amount_numeric: value is not a number.', verdict: 'WATCH', kind: 'data_quality', value: 3 },
        { id: 'measure.volume.change_pct', claim: 'Rows rose 12.5% on the year before.', verdict: 'WATCH', kind: 'business', value: 12.5 },
        { id: 'health.col.email.null_like_pct', claim: '4.0% of the email column is null-like.', verdict: 'WATCH', kind: 'data_quality', value: 4 }
      ],
      story: { headline: 'Salary blanks: 26.3% of the SALARY column is empty.', what_happened: ['Act on: 45.2% of the notes column of payroll.csv is null-like.', 'The file has 3,350 rows.'],
        why: ['Rows rose 12.5% on the year before.'], what_to_do: ['Fix the 2 rows set aside by salary_numeric.', 'Fix the 3 rows set aside by amount_numeric.'], whats_next: [], cannot_answer: ['Footnotes are not read.'] }
    };
    const p = T.aiPayload(rep, 'What changed?');
    const terms = ['salary', 'notes', 'salary_numeric', 'pay_grade_trim'];
    // an independent reading of "names it": the term with no letter, digit or underscore before it
    // and no letter or digit after it, in any case
    const names = (s) => terms.some((w) => new RegExp('(?<![A-Za-z0-9_])' + w + '(?![A-Za-z0-9])', 'i').test(String(s)));
    const sent = [p.story.headline].concat(...['what_happened', 'why', 'what_to_do', 'whats_next', 'cannot_answer'].map((k) => p.story[k]))
      .concat(...p.findings.map((f) => [f.id, f.claim]));
    const leaks = sent.filter(names);
    ok(!leaks.length, 'the payload names a withheld column: ' + JSON.stringify(leaks.slice(0, 3)));
    eq(p.findings.map((f) => f.id), ['measure.volume.change_pct', 'clean.quarantined.amount_numeric_value_is_not_a_number', 'health.col.email.null_like_pct'], 'the findings kept');
    eq(p.story.what_to_do, ['Fix the 3 rows set aside by amount_numeric.'], 'story lines kept');
    ok(p.story.cannot_answer.length === 1 && p.story.what_happened.length === 1 && p.story.headline === '', 'a line that names no withheld column was dropped, or the headline was sent: ' + JSON.stringify(p.story));
    if (proxy) { const v = proxy.validatePayload(JSON.parse(JSON.stringify(p))); ok(v.ok, 'the proxy would refuse it: ' + v.detail); }
  });

  check('aiPayload sends the AI plan\'s analyses in the story\'s optional analyses section, one sentence a line', () => {
    const ana = { items: [{ type: 'trend', sentence: 'GCAG rose by 0.0865 per decade over 1880 to 2025 (95% range 0.0707 to 0.102). Lines are drawn for 2 series.' },
      { type: 'agreement', sentence: 'Over 1,752 shared dates, GCAG runs 0.0843 lower than GISTEMP on average.' }], refused: [] };
    const base = JSON.parse(JSON.stringify(REPORT));
    const p = T.aiPayload(Object.assign({}, base, { ai_analyses: ana }), '');
    eq(p.story.analyses, ['GCAG rose by 0.0865 per decade over 1880 to 2025 (95% range 0.0707 to 0.102).', 'Lines are drawn for 2 series.',
      'Over 1,752 shared dates, GCAG runs 0.0843 lower than GISTEMP on average.'], 'one sentence a line');
    ok(!('analyses' in T.aiPayload(base, '').story), 'no analyses: the section is not sent, so the payload keeps its old shape');
    const src = T.templateSources ? T.templateSources(p) : null;
    if (proxy) {
      const v = proxy.validatePayload(JSON.parse(JSON.stringify(p))); ok(v.ok, 'the proxy would refuse it: ' + v.detail);
      ok(v.value.story.analyses.length === 3, 'the proxy keeps the section');
      const sys = proxy.systemPrompt('executive', v.value);
      ok(/section "analyses"/.test(sys) && /(Begin|Right after \{NONE_CONFIRMED\}, begin) with the first one or two/.test(sys), 'the prompt has the summary begin with them');
      ok(!/section "analyses"/.test(proxy.systemPrompt('executive', proxy.validatePayload(JSON.parse(JSON.stringify(T.aiPayload(base, '')))).value)), 'and says nothing of them when there are none');
    }
  });

  console.log(failed ? 'GUARD CHECKS: ' + failed + ' of ' + ran + ' FAILED' : 'GUARD CHECKS: ALL ' + ran + ' PASS');
  process.exit(failed ? 1 : 0);
})();
