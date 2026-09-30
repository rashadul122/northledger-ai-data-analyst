// The final review's share cases (30 Sep 2026), on the writer as it stands (src/js/45-report-pdf.js shareResults): at
// every cap from the full size down, a shared gap item never stands without its segment's contribution; the no-plan
// results (noplan_results.json) and a made-up block whose gap labels are shorter than its contribution labels (the
// review's orphan). Writes share_attack.out.
//   node tools/fixtures/review5/share_attack.cjs
const fs = require('fs');
const path = require('path');
const P = require(path.join(__dirname, '..', '..', '..', 'src', 'js', '45-report-pdf.js'));
const len = (o) => Buffer.byteLength(JSON.stringify(o), 'utf8');
const lines = [];
const say = (s) => { lines.push(s); console.log(s); };
function mk(segs, cLab, gLab) {
  const items = [{ id: 'h', group: 'headline', segment: null, label: 'Total', value: 1, text: '1', kind: 'amount' }];
  segs.forEach((s, i) => items.push({ id: 'c' + i, group: 'contribution', segment: s, label: s + cLab, value: 1000 * (9 - i), text: String(1000 * (9 - i)), kind: 'change', unit: '' }));
  segs.forEach((s, i) => { if (i) items.push({ id: 'g' + i, group: 'gap', segment: s, label: s + gLab, value: 5, text: '5', kind: 'amount', unit: '' }); });
  return { scenarios: { basis: {}, items, refused: [], note: '' }, findings: [], input: { rows: 1, columns: 1 } };
}
function sweep(label, R) {
  const full = len(R);
  let orphan = 0, over = 0, nondet = 0, nulls = 0, first = null;
  for (let cap = full + 50; cap >= 100; cap -= 1) {
    const a = P.shareResults(R, cap), b = P.shareResults(R, cap);
    if (JSON.stringify(a) !== JSON.stringify(b)) nondet++;
    if (!a) { nulls++; continue; }
    if (len(a) > cap) over++;
    const it = a.scenarios ? a.scenarios.items : [];
    const cons = new Set(it.filter((x) => x.group === 'contribution').map((x) => x.segment));
    const o = it.filter((x) => x.group === 'gap' && typeof x.segment === 'string' && !cons.has(x.segment));
    if (o.length) { orphan++; if (!first) first = { cap, kept: it.map((x) => x.id) }; }
  }
  say(label + ': full ' + full + ' bytes; caps swept down to 100: ' + JSON.stringify({ orphanGap: orphan, overCap: over, nondeterministic: nondet, nothingFits: nulls }) + (first ? ' first orphan ' + JSON.stringify(first) : ''));
  return orphan + over + nondet;
}
let bad = 0;
bad += sweep('no-plan results (44 items)', JSON.parse(fs.readFileSync(path.join(__dirname, 'noplan_results.json'), 'utf8')));
bad += sweep('gap labels shorter than contribution labels', mk(['A', 'B', 'C', 'D'], ': its contribution to the change in total revenue, the long form of the label xxxxxxxxxxxxxxxxxxxxxxxx', ': gap'));
say(bad ? 'SHARE FAIL' : 'SHARE PASS: no orphan gap, never over the cap, the same result each time');
fs.writeFileSync(path.join(__dirname, 'share_attack.out'), lines.join('\n') + '\n');
process.exit(bad ? 1 : 0);
