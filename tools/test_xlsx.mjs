#!/usr/bin/env node
// Tests for src/js/56-xlsx.js (window.NLX, the in-browser .xlsx writer reused by the Dashboard, the monthly pack and the
// tracker workbook): one workbook with every feature (several sheets with names that need escaping, bold frozen header,
// widths, number, percent and date formats, formulas with cached values incl. TODAY(), COUNTIFS and SUMIFS and a
// cross-sheet reference, a dropdown from a Lists range and an inline one, an Excel table with its autofilter, native
// line, column, bar and area charts, a table's calculated column), then its zip entries, every XML part well-formed, and the parts that say what
// Excel needs. With the site's venv (../agent-demo/venv) or PY: openpyxl opens it and reads back the formulas, the
// validations, the table and the charts.
//     node tools/test_xlsx.mjs
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.join(HERE, '..');
const ctx = { window: {}, TextEncoder, Date, Math, Object, Array, String, Number, isFinite, Uint8Array, JSON, RegExp };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(SITE, 'src/js/56-xlsx.js'), 'utf8'), ctx);
const NLX = ctx.window.NLX;
let bad = 0, n = 0;
function test(name, fn) { n++; try { fn(); console.log('PASS', name); } catch (e) { bad++; console.log('FAIL', name, e.message); } }
function ok(c, m) { if (!c) throw new Error(m); }

const B = (v) => ({ v, b: true });
const spec = {
  title: 'Test & workbook',
  sheets: [
    { name: "Q3 'tracker'", freeze: 1, widths: [24, 12, 12, 14, 12],
      rows: [[B('Task'), B('Owner'), B('Status'), B('Due'), B('Cost')],
        ['Fix roof', 'Ana', 'Open', { v: '2026-10-20', fmt: 'date' }, 1200],
        ['Paint', 'Ben', 'Done', { v: '2026-09-01', fmt: 'date' }, 300.5],
        ['Plumbing', 'Ana', 'Open', { v: '2026-11-02', fmt: 'date' }, 450],
        null,
        ['Open tasks', { f: 'COUNTIFS(C2:C4,"Open")', v: 2, fmt: 'int' }],
        ['Open cost', { f: 'SUMIFS(E2:E4,C2:C4,"Open")', v: 1650, fmt: 'num2' }],
        ['Overdue', { f: 'COUNTIFS(D2:D4,"<"&TODAY(),C2:C4,"Open")', v: 0 }],
        ['Share done', { f: 'IF(SUM(E2:E4)=0,"",SUMIFS(E2:E4,C2:C4,"Done")/SUM(E2:E4))', v: 300.5 / 1950.5, fmt: 'pct' }],
        ['Lists count', { f: NLX.ref('Lists', 'B1'), v: 3 }]],
      validations: [{ range: 'C2:C200', list: { sheet: 'Lists', col: 0, r0: 2, r1: 4 } }, { range: 'B2:B200', values: ['Ana', 'Ben', 'Cy, Jr'] }],
      tables: [{ name: 'Tracker 1', ref: 'A1:E4', calc: { Cost: 'Tracker_1[[#This Row],[Cost]]*1', Nope: 'X' } }] },
    { name: 'Lists', rows: [[B('Status'), { f: 'COUNTA(A2:A4)', v: 3 }], ['Open'], ['Doing'], ['Done']] },
    { name: 'Charts/[1]', rows: [[B('Charts')], ['Month', 'A', 'B'], ['Jan', 1, 2], ['Feb', 3, null], ['Mar', 2, 5]],
      charts: ['line', 'column', 'bar', 'area'].map((t, i) => ({ type: t, title: t + ' chart', at: { col: 4, row: i * 18, cols: 8, rows: 16 },
        cat: { sheet: 'Charts/[1]', col: 0, r0: 3, r1: 5 },
        series: [1, 2].map((c) => ({ name: { sheet: 'Charts/[1]', col: c, row: 2 }, sheet: 'Charts/[1]', col: c, r0: 3, r1: 5 })) })) }
  ]
};
const bytes = NLX.workbook(spec);

// a stored zip, read back
function unzip(b) {
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength), out = {};
  let at = 0;
  while (dv.getUint32(at, true) === 0x04034b50) {
    const method = dv.getUint16(at + 8, true), size = dv.getUint32(at + 18, true), nl = dv.getUint16(at + 26, true), xl = dv.getUint16(at + 28, true);
    if (method !== 0) throw new Error('not stored');
    const name = new TextDecoder().decode(b.slice(at + 30, at + 30 + nl));
    out[name] = new TextDecoder().decode(b.slice(at + 30 + nl + xl, at + 30 + nl + xl + size));
    at += 30 + nl + xl + size;
  }
  return out;
}
const Z = unzip(bytes);
// well-formed XML: every tag closed in order, attributes quoted, entities known
function wellFormed(x) {
  const body = x.replace(/^<\?xml[^>]*\?>/, ''), st = [];
  const re = /<(\/?)([A-Za-z_][\w:.-]*)((?:\s+[\w:.-]+="[^"<]*")*)\s*(\/?)>|<|&(?!(amp|lt|gt|quot|apos|#\d+);)/g;
  let m;
  while ((m = re.exec(body))) {
    if (!m[2]) throw new Error('a stray < or & at ' + m.index + ': ' + body.slice(m.index, m.index + 40));
    if (m[1]) { if (st.pop() !== m[2]) throw new Error('</' + m[2] + '> closes the wrong element'); }
    else if (!m[4]) st.push(m[2]);
  }
  if (st.length) throw new Error('unclosed <' + st.pop() + '>');
}

test('zip entries', () => {
  for (const e of ['[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', 'xl/styles.xml', 'xl/worksheets/sheet1.xml',
    'xl/worksheets/sheet2.xml', 'xl/worksheets/sheet3.xml', 'xl/worksheets/_rels/sheet1.xml.rels', 'xl/worksheets/_rels/sheet3.xml.rels', 'xl/tables/table1.xml',
    'xl/drawings/drawing1.xml', 'xl/drawings/_rels/drawing1.xml.rels', 'xl/charts/chart1.xml', 'xl/charts/chart4.xml', 'docProps/core.xml']) ok(e in Z, 'missing ' + e);
});
test('every XML part is well-formed', () => { for (const [k, v] of Object.entries(Z)) { try { wellFormed(v); } catch (e) { throw new Error(k + ': ' + e.message); } } });
test('sheet names escaped and quoted in references', () => {
  ok(/name="Q3 'tracker'"/.test(Z['xl/workbook.xml']) && /name="Charts 1"/.test(Z['xl/workbook.xml']), Z['xl/workbook.xml'].slice(0, 400));
  ok(Z['xl/charts/chart1.xml'].includes("<c:f>'Charts 1'!$A$3:$A$5</c:f>") && !Z['xl/charts/chart1.xml'].includes('[1]'), 'a chart refers to the sheet by a name Excel refuses');
  ok(Z['xl/worksheets/sheet1.xml'].includes("<f>'Lists'!B1</f>"), 'a cross-sheet formula is not quoted');
});
test('formulas with cached values, frozen bold header, formats', () => {
  const s = Z['xl/worksheets/sheet1.xml'];
  ok(s.includes('<f>COUNTIFS(C2:C4,&quot;Open&quot;)</f><v>2</v>'), 'COUNTIFS');
  ok(s.includes('<f>SUMIFS(E2:E4,C2:C4,&quot;Open&quot;)</f><v>1650</v>'), 'SUMIFS');
  ok(/TODAY\(\)/.test(s) && /state="frozen"/.test(s) && /ySplit="1"/.test(s), 'TODAY or the frozen row');
  ok(/<c r="D2" s="\d+"><v>46315<\/v><\/c>/.test(s), 'the date is not a serial with a format: ' + (s.match(/<c r="D2"[^/]*\/c>/) || [])[0]);
  const st = Z['xl/styles.xml'];
  ok(st.includes('formatCode="yyyy-mm-dd"') && st.includes('formatCode="0.0%"') && /<font><b\/>/.test(st), 'formats or bold');
});
test('dropdowns and the table', () => {
  const s = Z['xl/worksheets/sheet1.xml'];
  ok(s.includes('<dataValidation type="list"') && s.includes("<formula1>'Lists'!$A$2:$A$4</formula1>") && s.includes('<formula1>&quot;Ana,Ben,Cy  Jr&quot;</formula1>'), 'validations');
  ok(s.indexOf('<dataValidations') > s.indexOf('</sheetData>') && s.indexOf('<tableParts') > s.indexOf('<dataValidations'), 'element order');
  const t = Z['xl/tables/table1.xml'];
  ok(t.includes('name="Tracker_1"') && t.includes('<autoFilter ref="A1:E4"/>') && t.includes('<tableColumn id="4" name="Due"/>'), t.slice(0, 300));
  // a calculated column (the tracker's formula columns): Excel copies it into every row a visitor adds; a name the
  // table does not hold is ignored
  ok(t.includes('<tableColumn id="5" name="Cost"><calculatedColumnFormula>Tracker_1[[#This Row],[Cost]]*1</calculatedColumnFormula></tableColumn>') && !t.includes('>X<'), t);
  ok(Z['[Content_Types].xml'].includes('/xl/tables/table1.xml'), 'the table has no content type');
});
test('native charts', () => {
  const kinds = [1, 2, 3, 4].map((i) => Z['xl/charts/chart' + i + '.xml']);
  ok(kinds[0].includes('<c:lineChart>') && kinds[1].includes('<c:barDir val="col"/>') && kinds[2].includes('<c:barDir val="bar"/>') && kinds[3].includes('<c:grouping val="stacked"/>'), 'chart kinds');
  ok(kinds.every((k) => k.includes('<c:dispBlanksAs val="gap"/>')), 'a blank must stay a gap');
});
test('dates', () => {
  ok(NLX.dateSerial('2026-10-16') === 46311 && NLX.dateSerial('1900-03-01') === 61 && NLX.dateSerial('nope') === null, 'serials');
});
const PY = process.env.PY || path.join(SITE, '..', 'agent-demo', 'venv', 'bin', 'python');
if (fs.existsSync(PY)) {
  const f = path.join(os.tmpdir(), 'nlx-test-' + process.pid + '.xlsx');
  fs.writeFileSync(f, bytes);
  test('openpyxl opens it', () => {
    const code = 'import sys,openpyxl\nwb=openpyxl.load_workbook(sys.argv[1])\nws=wb.worksheets[0]\n' +
      'print(wb.sheetnames, ws["B6"].value, ws.freeze_panes, len(ws.data_validations.dataValidation), list(ws.tables), len(wb.worksheets[2]._charts), ws["D2"].number_format, ws["D2"].value)';
    const r = spawnSync(PY, ['-c', code, f], { encoding: 'utf8' });
    ok(r.status === 0, (r.stderr || '').trim().split('\n').pop());
    const o = r.stdout.trim();
    ok(o.includes('=COUNTIFS(C2:C4,"Open")') && o.includes('A2') && o.includes(' 2 ') && o.includes("['Tracker_1']") && o.includes(' 4 yyyy-mm-dd 2026-10-20'), o);
  });
  fs.unlinkSync(f);
} else console.log('(no python with openpyxl: the openpyxl check was not run)');
console.log(n - bad + ' of ' + n + ' passed');
process.exit(bad ? 1 : 0);
