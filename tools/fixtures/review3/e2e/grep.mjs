// The final review's payload grep (29 Sep 2026), kept: from flow.py's OUTDIR/flow.json, build the exact bodies
// the page sends, with the page's own code (src/js/00-core.js, 50-try.js: T.planProfile, T.planWithheld,
// T.planSignals, T.planPrevious) and the proxy's own validation and prompt (../insight-proxy/src/plan.js:
// validateProfile, validateFeedback, planUserMessage; the system prompts are its fixed text):
//   /plan     the first plan's messages, and the re-plan's when the engine sent signals the page passes on
//   /report   {objective, results: results_for_ai}, as askAiReport posts it
// and grep each for: a value of a withheld or coded column (4 or more characters, not also a value of a column
// that goes as it is), a person's name token of the reviewer's file, a withheld column's name (its header and
// its landed name, whole words), a code (WITHHELD..., rdc_...) and the file's name (with and without its
// extension). Prints one line per file and choice; exits 1 on any hit.
// Option B (flow.py --optin): a kept column goes like any column, values included. Its values are counted (the
// line says how many of them each body carries), never a hit; everything else stays a hit. Under --optin off no
// choice may keep a column and no /report body may say the visitor chose to send one; under --optin one each
// choice keeps one column, and its /report body says so once, by name.
//
//   node tools/fixtures/review3/e2e/grep.mjs OUTDIR
import fs from 'fs'; import path from 'path'; import vm from 'vm'; import { fileURLToPath, pathToFileURL } from 'url';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, '..', '..', '..', '..'), PROXY = path.resolve(SITE, '..', 'insight-proxy');
const OUT = path.resolve(process.argv[2] || '.');
const noop = () => {};
const document = { readyState: 'loading', addEventListener: noop, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null };
const window = { addEventListener: noop, NL: { try: { max_bytes: 25000000, max_rows: 200000, ai_proxy_url: '' } } }; window.window = window;
const ctx = vm.createContext({ window, document, console, TextDecoder, TextEncoder, Uint8Array, setTimeout, clearTimeout, performance });
for (const f of ['00-core.js', '50-try.js']) vm.runInContext(fs.readFileSync(path.join(SITE, 'src', 'js', f), 'utf8'), ctx, { filename: f });
const T = window.NLTry;
const P = await import(pathToFileURL(path.join(PROXY, 'src', 'plan.js')).href);
const flow = JSON.parse(fs.readFileSync(path.join(OUT, 'flow.json'), 'utf8'));
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const word = (text, n) => new RegExp('(^|[^A-Za-z0-9_])' + esc(n) + '(?![A-Za-z0-9_])', 'i').test(text);
const strings = (x, out = []) => { if (typeof x === 'string') out.push(x); else if (Array.isArray(x)) x.forEach((v) => strings(v, out)); else if (x && typeof x === 'object') Object.values(x).forEach((v) => strings(v, out)); return out; };
let hits = 0, bodies = 0, replans = 0, reports = 0, keptSent = 0, keptSets = 0, mode = null;
const tally = {};
for (const [file, F] of Object.entries(flow)) {
  for (const [k, s] of Object.entries(F.sets)) {
    if (!s.profile) { console.log(file.padEnd(38), k.padEnd(16), 'no profile (the page sends no /plan)'); continue; }
    const dec = s.decisions || {}, landed = s.landed || {};
    const hdrOf = (l) => Object.keys(landed).find((h) => landed[h] === l) || l;
    const choice = {}; F.flagged.forEach((f) => { choice[f.column] = dec[f.column] || dec[hdrOf(f.column)] || 'withhold'; });
    const priv = Object.keys(choice).filter((c) => choice[c] !== 'keep');
    const kept = Object.keys(choice).filter((c) => choice[c] === 'keep');
    mode = F.optin || mode;
    const privHdrs = new Set(priv.map(hdrOf));
    const openHdrs = F.hdr.filter((h) => !privHdrs.has(h));
    // the messages: the first /plan, the re-plan (the page's own filters), the /report body
    const msgs = [];
    const body = { objective: '', profile: T.planProfile(s.profile, F.flagged, dec, landed) };
    const v = P.validateProfile(JSON.parse(JSON.stringify(body)));
    if (!v.ok) { console.log(file.padEnd(38), k.padEnd(16), 'validateProfile refused:', v.detail); continue; }
    // the user messages: the system prompts are the proxy's fixed text, the same for every file (their words,
    // "aggregates" or "notes", would read as hits)
    msgs.push(['/plan', P.planUserMessage(v.value, null)]); bodies += 1;
    if (F.plan && s.plan_signals.length) {
      const held = T.planWithheld(s.profile, F.flagged, dec, landed);
      const sent = T.planSignals(s.plan_signals, held);
      if (sent.length) {
        const f = P.validateFeedback(JSON.parse(JSON.stringify({ attempt: 1, previous_plan: T.planPrevious(F.plan, held), signals: sent })), v.value);
        if (f.ok) { msgs.push(['re-plan', P.planUserMessage(v.value, f.value)]); replans += 1; }
      }
    }
    if (s.results) { msgs.push(['/report', strings({ objective: '', results: s.results }).join('\n')]); reports += 1; }
    const found = [];
    for (const [where, text] of msgs) {
      for (const c of priv) {
        const h = hdrOf(c);
        for (const val of F.values[h] || []) {
          if (val.trim().length < 4 || openHdrs.some((oh) => (F.values[oh] || []).includes(val))) continue;
          if (text.includes(val)) found.push(where + ' value ' + h + '=' + val);
        }
        if (choice[c] === 'withhold') for (const n of new Set([c, h])) if (word(text, n)) found.push(where + ' withheld name ' + n);
      }
      for (const t of F.tokens || []) {
        const inOpen = openHdrs.some((oh) => (F.values[oh] || []).some((x) => x.toLowerCase().includes(t.toLowerCase())));
        if (!inOpen && new RegExp(esc(t), 'i').test(text)) found.push(where + ' name token ' + t);
      }
      for (const m of text.match(/WITHHELD[A-Z]+|rdc_[0-9a-f]{6,}/g) || []) found.push(where + ' code ' + m);
      const stem = F.name.replace(/\.[A-Za-z0-9]+$/, '');
      for (const n of new Set([F.name, stem])) if (n.length >= 4 && word(text, n)) found.push(where + ' file name ' + n);
    }
    // option B: the kept column's values are counted, not hits; the mode's own promise is checked
    const keptVals = new Set();
    for (const c of kept) for (const val of F.values[hdrOf(c)] || []) {
      if (val.trim().length >= 4 && msgs.some((m) => m[1].includes(val))) keptVals.add(val);
    }
    if (kept.length) { keptSets += 1; keptSent += keptVals.size; }
    const told = 'The visitor chose to send these personal columns to the AI: ';
    const rep = msgs.filter((m) => m[0] === '/report').map((m) => m[1]).join('\n');
    if (F.optin === 'off' && kept.length) found.push('opt-in off, yet the choice keeps ' + kept.join(', '));
    if (F.optin === 'off' && rep.indexOf(told) >= 0) found.push('/report says the visitor chose to send a column');
    if (F.optin === 'one' && kept.length !== 1) found.push('opt-in one, yet the choice keeps ' + kept.length + ' columns');
    if (F.optin === 'one' && rep && rep.split(told + kept.join(', ') + '.').length !== 2) found.push('/report does not say once which column the visitor chose to send');
    hits += found.length;
    found.forEach((h) => { const k = h.replace(/^(\S+) (value|withheld name|name token|code|file name).*$/, '$1 $2'); tally[k] = (tally[k] || 0) + 1; });
    console.log(file.padEnd(38), k.padEnd(16), msgs.map((m) => m[0]).join('+').padEnd(24), found.length ? 'HITS: ' + found.slice(0, 4).join(' | ') : 'clean',
      kept.length ? '(kept ' + kept.join(', ') + ': ' + keptVals.size + ' of its values sent)' : '');
  }
}
console.log('\n' + bodies + ' /plan bodies, ' + replans + ' re-plan bodies, ' + reports + ' /report bodies over ' + Object.keys(flow).length + ' files' +
  (mode ? ' (--optin ' + mode + ')' : ''));
if (keptSets) console.log(keptSets + ' choices kept a flagged column: ' + keptSent + ' of the kept columns\' values were sent, as the visitor chose (not hits)');
if (hits) console.log('by kind: ' + JSON.stringify(tally));
console.log(hits ? 'PRIVACY GREP: ' + hits + ' HITS' : 'PRIVACY GREP: 0 personal values, withheld names, codes or file names in any /plan, re-plan or /report body');
process.exit(hits ? 1 : 0);
