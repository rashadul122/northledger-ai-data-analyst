#!/usr/bin/env node
/* Behaviour checks for the built page, run in headless Chrome through Playwright.
   Each check opens a fresh copy of the page and does what a reader would do (pick a slicer,
   press Esc, tap on a phone, switch to the dark theme), then asserts what the page shows.

     node tools/check_ui.js [path/to/index.html] [check-name ...]

   Environment: PLAYWRIGHT_MODULE (path to the playwright package; default: require('playwright')),
   CHROME (Chrome executable; default: the macOS system Chrome). Nothing here edits the page,
   starts a server or opens a port: the page is loaded from a file:// URL.
   Exit code 1 when any check fails. */
'use strict';
const path = require('path');
const fs = require('fs');

let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); } catch (e) {
  console.log('UI SKIP: Playwright not found (set PLAYWRIGHT_MODULE to the playwright package folder)');
  process.exit(2);
}
const args = process.argv.slice(2);
const pageArg = args[0] && args[0].endsWith('.html') ? args.shift() : path.join(__dirname, '..', 'index.html');
const FILE = 'file://' + path.resolve(pageArg);
const ONLY = new Set(args);
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const checks = [];
function check(name, viewport, fn, opts) { checks.push({ name, viewport, fn, opts: opts || {} }); }
const DESK = { width: 1280, height: 900 }, PHONE = { width: 390, height: 844 };

async function open(ctx, hash) {
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(FILE + (hash || ''));
  await page.waitForFunction(() => window.NLU && document.querySelector('#v-wards') && document.querySelector('#v-wards').children.length);
  await page.waitForTimeout(150);
  page.__errs = errs;
  return page;
}
async function setSel(page, id, value) {
  await page.selectOption('#' + id, value);
  await page.waitForTimeout(60);
}
function ok(cond, msg) { if (!cond) throw new Error(msg); }
const overlap = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;

/* ------------------------------------------------------------ filters, drawer, drill */
check('esc-closes-drawer-and-keeps-filters', DESK, async (ctx) => {
  const p = await open(ctx);
  await setSel(p, 'sl-district', 'Scarborough');
  await p.click('[data-drawer="measures"]');
  await p.keyboard.press('Escape');
  const r = await p.evaluate(() => ({ hidden: document.getElementById('drawer').hidden, d: NLU.state.district }));
  ok(r.hidden, 'drawer still open after Esc');
  ok(r.d === 'Scarborough', 'Esc on the drawer also removed the district filter (state now "' + r.d + '")');
});

check('esc-closes-drill-and-keeps-filters', DESK, async (ctx) => {
  const p = await open(ctx);
  await setSel(p, 'sl-year', '2024');
  await p.click('#hm-body tr[data-ward="01"] .ward-btn');
  await p.keyboard.press('Escape');
  const r = await p.evaluate(() => ({ hidden: document.getElementById('drill').hidden, y: NLU.state.eval_year }));
  ok(r.hidden && r.y === '2024', 'drill Esc: hidden=' + r.hidden + ', year=' + r.y);
});

check('drill-matches-its-row-under-filters', DESK, async (ctx) => {
  const p = await open(ctx);
  await setSel(p, 'sl-year', '2024');
  const row = await p.evaluate(() => {
    const tr = document.querySelector('#hm-body tr[data-ward="01"]');
    return { overall: tr.querySelector('td[data-col="overall"] .cv').textContent, rank: tr.querySelector('td[data-col="rank"]').textContent.trim(),
      green: tr.querySelector('td[data-col="green"]').textContent.trim() };
  });
  await p.click('#hm-body tr[data-ward="01"] .ward-btn');
  const t = await p.textContent('#drill-body');
  ok(t.indexOf('simple mean ' + row.overall) >= 0, 'drill says "' + (t.match(/simple mean [\d.]+/) || ['?'])[0] + '", row says ' + row.overall);
  ok(t.indexOf('Rank ' + row.rank + ' of') >= 0, 'drill rank differs from the row rank ' + row.rank);
});

// the drill is opened first and the filter changed after: it must follow, not keep the old story
check('drill-follows-a-filter-changed-while-it-is-open', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.click('#hm-body tr[data-ward="01"] .ward-btn');
  await setSel(p, 'sl-year', '2024');
  const row = await p.evaluate(() => {
    const tr = document.querySelector('#hm-body tr[data-ward="01"]');
    return { overall: tr.querySelector('td[data-col="overall"] .cv').textContent, rank: tr.querySelector('td[data-col="rank"]').textContent.trim() };
  });
  let r = await p.evaluate(() => ({ hidden: document.getElementById('drill').hidden, t: document.getElementById('drill-body').textContent }));
  ok(!r.hidden, 'the drill closed when the year changed');
  ok(r.t.indexOf('simple mean ' + row.overall) >= 0, 'after Year=2024 the drill says "' + (r.t.match(/simple mean [\d.]+/) || ['?'])[0] + '", its row says ' + row.overall);
  ok(r.t.indexOf('Rank ' + row.rank + ' of') >= 0, 'after Year=2024 the drill rank differs from the row rank ' + row.rank);
  await p.click('[data-hbasis="city"]');
  await p.waitForTimeout(60);
  r = await p.evaluate(() => ({ hidden: document.getElementById('drill').hidden, t: document.getElementById('drill-body').textContent }));
  ok(!r.hidden && /zero counted as zero points/.test(r.t), 'the drill did not follow the zero basis switched while it was open');
  await setSel(p, 'sl-year', '');
  await p.click('[data-hbasis="flag"]');
  await p.waitForTimeout(60);
  r = await p.evaluate(() => ({ t: document.getElementById('drill-body').textContent, want: NL.sc.wards.find((w) => w.ward === '01').overall_mean }));
  ok(r.t.indexOf('simple mean ' + r.want.toFixed(1)) >= 0 && r.t.indexOf('In this view') < 0, 'back at the default view the drill does not show the published row');
});

// every cell's colour, glyph and spoken band come from the number it prints (1 dp, half-up)
check('scorecard-cells-band-from-the-printed-value', DESK, async (ctx) => {
  const p = await open(ctx);
  const views = [['published', null, 'flag'], ['zero basis', null, 'city'], ['Year=2025', '2025', 'flag'], ['zero basis, Year=2025', '2025', 'city']];
  let cells = 0;
  const bad = [];
  for (const [name, year, basis] of views) {
    await setSel(p, 'sl-year', year || '');
    await p.click('[data-hbasis="' + basis + '"]');
    await p.waitForTimeout(60);
    const r = await p.evaluate(() => {
      const band = (v) => v >= 85 ? 'green' : v >= 70 ? 'yellow' : 'red';
      const g = { green: '●', yellow: '◐', red: '○' };
      const out = [];
      let n = 0;
      document.querySelectorAll('#hm td.hc[data-v]').forEach((td) => {
        n++;
        const shown = td.querySelector('.cv').textContent.trim();
        const v = parseFloat(td.getAttribute('data-v'));
        const c = Math.abs(+v.toPrecision(12)), half = ((v < 0 ? -1 : 1) * Math.floor(c * 10 + 0.5 + 1e-9) / 10).toFixed(1);   // decimal half-up
        const want = band(parseFloat(shown));
        const cls = (td.className.match(/\bh-(green|yellow|red)-\d\b/) || [])[1];
        const where = (td.closest('tr').getAttribute('data-ward') || td.closest('tr').textContent.slice(0, 12)) + '/' + td.getAttribute('data-col');
        if (shown !== half) out.push(where + ' prints ' + shown + ' for ' + v + ' (half-up ' + half + ')');
        if (td.getAttribute('data-band') !== want || cls !== want || td.querySelector('.gl').textContent !== g[want] || td.querySelector('.sr').textContent.trim() !== want)
          out.push(where + ' prints ' + shown + ' but is banded ' + [td.getAttribute('data-band'), cls, td.querySelector('.gl').textContent].join('/'));
      });
      return { n, out };
    });
    cells += r.n;
    r.out.slice(0, 3).forEach((x) => bad.push(name + ': ' + x));
    if (r.out.length > 3) bad.push(name + ': ' + (r.out.length - 3) + ' more');
  }
  ok(cells > 1000, 'only ' + cells + ' cells checked');
  ok(!bad.length, bad.join('; '));
});

// fewer than five buildings in a view: no values, so the page stays at ward and district level
check('small-views-are-suppressed', DESK, async (ctx) => {
  const p = await open(ctx);
  const MSG = 'Fewer than 5 buildings in this view; not shown';
  await setSel(p, 'sl-year', '2023');
  const r = await p.evaluate(() => {
    const n = NLU.sum(NLU.rowsFor()).n;
    const kv = Array.from(document.querySelectorAll('#kpis .kpi:not(.kpi-city) .k-val')).map((e) => e.textContent);
    return { n, kpis: document.getElementById('kpis').textContent, kv, story: document.getElementById('v-story').textContent };
  });
  ok(r.n > 0 && r.n < 5, 'Year=2023 holds ' + r.n + ' buildings; the check needs a view under 5');
  ok(r.kpis.indexOf(MSG) >= 0, 'KPI cards do not say "' + MSG + '"');
  ok(r.kv.every((s) => !/\d/.test(s)), 'KPI cards still print values: ' + r.kv.join(' | '));
  ok(r.story.indexOf(MSG) >= 0 && !/\d/.test(r.story.replace(MSG, '')), 'the view story prints figures for ' + r.n + ' building(s)');
  await p.evaluate(() => { document.getElementById('pg-pillars').hidden = false; NLU.redraw(); });
  for (const id of ['v-wards', 'v-ptype', 'v-pillars', 'v-matrix']) {
    const t = await p.textContent('#' + id);
    ok(t.indexOf(MSG) >= 0, id + ' shows values for a view under 5 buildings: "' + t.slice(0, 80) + '"');
  }
  // a larger view: only the wards (rows, marks, map areas) under 5 are withheld
  await setSel(p, 'sl-year', '2025');
  await setSel(p, 'sl-ptype', 'SOCIAL HOUSING');
  const s = await p.evaluate((MSG) => {
    const g = NLU.byWard(NLU.rowsFor({ ignoreWard: true }));
    const small = Object.keys(g).filter((w) => g[w].n > 0 && g[w].n < 5);
    const out = [];
    small.forEach((w) => {
      const tr = document.querySelector('#hm-body tr[data-ward="' + w + '"]');
      if (!tr || tr.textContent.indexOf(MSG) < 0 || tr.querySelector('td[data-v]')) out.push('scorecard row ' + w);
      if (document.querySelector('#v-wards [data-ward="' + w + '"] circle')) out.push('ward dot ' + w);
      const m = document.querySelector('#v-map path[data-ward="' + w + '"]');
      if (m && /average/.test(m.getAttribute('data-tip') || '')) out.push('map area ' + w);
    });
    return { small: small.length, out };
  }, MSG);
  ok(s.small > 0, 'no ward falls under 5 buildings in Year=2025 + social housing; pick another view for this check');
  ok(!s.out.length, 'values shown for wards under 5 buildings: ' + s.out.slice(0, 6).join(', '));
});

// the recorded run has one total: the replay clock ends where the lede says the stages end
check('run-clock-ends-at-the-stated-total', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.selectOption('#run-speed', '0');
  await p.click('#run-play');
  const r = await p.evaluate(() => ({ clock: document.getElementById('run-clock').textContent, lede: document.querySelector('#northledger .lede').textContent }));
  const nums = (r.clock.match(/[\d.]+ s/g) || []);
  ok(nums.length >= 2 && nums[0] === nums[nums.length - 1], 'the clock does not end at its own total: "' + r.clock + '"');
  ok(r.lede.indexOf(nums[nums.length - 1]) >= 0, 'the lede never states the clock\'s total ' + nums[nums.length - 1] + ': "' + r.lede.slice(-220) + '"');
});

// the two bar colours in Analysis A's chart are explained on the chart
check('analysis-a-bars-have-a-tier-legend', DESK, async (ctx) => {
  const p = await open(ctx);
  const t = await p.evaluate(() => Array.from(document.querySelectorAll('#v-a-items svg text')).map((x) => x.textContent));
  ok(t.indexOf('High tier') >= 0 && t.indexOf('Moderate or cosmetic') >= 0, 'no two-swatch tier legend: ' + t.slice(-4).join(' | '));
});

// the case study is a page like this one, not raw Markdown
check('case-study-is-a-styled-page', DESK, async (ctx) => {
  const p = await open(ctx);
  const hrefs = await p.evaluate(() => Array.from(document.querySelectorAll('a[href*="case-study"]')).map((a) => a.getAttribute('href')));
  ok(hrefs.length >= 2 && hrefs.every((h) => h === 'case-study.html'), 'case study links: ' + hrefs.join(', '));
  const cs = await ctx.newPage();
  const errs = [];
  cs.on('pageerror', (e) => errs.push(String(e)));
  await cs.goto(FILE.replace(/index\.html$/, 'case-study.html'));
  for (const theme of ['light', 'dark']) {
    await cs.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
    const r = await cs.evaluate(() => ({ bg: getComputedStyle(document.body).backgroundColor, h1: (document.querySelector('h1') || {}).textContent || '',
      md: /(^|\n)#{1,3} |\*\*|`/.test(document.body.innerText), lists: document.querySelectorAll('li').length, back: !!document.querySelector('a[href="index.html"]') }));
    ok(/Case study/.test(r.h1), theme + ': no heading on case-study.html');
    ok(!r.md, theme + ': raw Markdown syntax shows on case-study.html');
    ok(r.lists > 3 && r.back, theme + ': case-study.html has no lists or no link back');
    ok(r.bg !== 'rgba(0, 0, 0, 0)', theme + ': case-study.html has no page background');
  }
  const [l, d] = await cs.evaluate(() => { const a = []; ['light', 'dark'].forEach((t) => { document.documentElement.setAttribute('data-theme', t); a.push(getComputedStyle(document.body).backgroundColor); }); return a; });
  ok(l !== d, 'case-study.html looks the same in both themes');
  ok(!errs.length, 'case-study.html errors: ' + errs.join('; '));
});

check('area-only-visuals-say-so-when-year-or-type-is-set', DESK, async (ctx) => {
  const p = await open(ctx);
  const lede = await p.textContent('#report .lede');
  ok(!/every card and visual/i.test(lede), 'lede still says every card and visual filters together');
  await setSel(p, 'sl-year', '2025');
  await p.evaluate(() => { document.getElementById('pg-fix').hidden = false; document.getElementById('pg-trend').hidden = false; NLU.redraw(); });
  for (const id of ['v-fixward', 'v-risk', 'v-trend']) {
    const t = await p.textContent('#' + id);
    ok(/not filtered by year/i.test(t), id + ' has no note that it ignores the year filter');
  }
});

check('pandemic-label-clear-of-legend', DESK, async (ctx) => {
  const p = await open(ctx);
  const n = await p.evaluate(() => NL.fl.origins.length);
  let bad = 0, drawn = 0;
  for (let i = 0; i < n; i++) {
    await p.evaluate((v) => { const o = document.getElementById('fl-o'); o.value = String(v); o.dispatchEvent(new Event('input')); }, i);
    const r = await p.evaluate(() => {
      const ts = Array.from(document.querySelectorAll('#v-fan text')).map((t) => ({ s: t.textContent, b: t.getBoundingClientRect() }));
      const pan = ts.filter((t) => /pandemic/.test(t.s));
      const leg = ts.filter((t) => /^(reported|history|forecast|band from past errors)$/.test(t.s));
      return { drawn: pan.length, hit: pan.some((a) => leg.some((b) => a.b.left < b.b.right && b.b.left < a.b.right && a.b.top < b.b.bottom && b.b.top < a.b.bottom)) };
    });
    drawn += r.drawn ? 1 : 0; if (r.hit) bad++;
  }
  ok(bad === 0, 'pandemic label overlaps the legend in ' + bad + ' of ' + drawn + ' replay positions');
});

check('colour-legends-true-in-dark-theme', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { document.documentElement.setAttribute('data-theme', 'dark'); });
  const t = (await p.textContent('#v-map')) + ' ' + (await p.textContent('#v-d-corr'));
  ok(!/darker/i.test(t), 'a legend says "darker = higher", which is backwards in the dark theme');
});

check('empty-view-prints-no-na-percent', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { NLU.setFilter('ward', '09'); NLU.setFilter('eval_year', '2023'); });
  const t = await p.textContent('#kpis');
  ok(t.indexOf('n/a%') < 0, 'KPI cards print "n/a%"');
});

check('focused-ward-label-stays-readable', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.focus('#v-wards g.mk');
  await p.keyboard.press('Tab'); await p.keyboard.press('Shift+Tab');
  const s = await p.evaluate(() => { const t = document.activeElement.querySelector('text'); return t ? getComputedStyle(t).stroke + '|' + getComputedStyle(t).strokeWidth : 'none'; });
  ok(/^none/.test(s) || /\|0px$/.test(s), 'focused ward label is stroked: ' + s);
});

check('control-chart-labels-years-and-breaks-gaps', DESK, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => {
    const pts = NL.D.control.points, years = new Set(pts.map((x) => x.month.slice(0, 4)));
    const labs = Array.from(document.querySelectorAll('#v-d-control text')).map((t) => t.textContent).filter((s) => /^\d{4}$/.test(s));
    let gaps = 0;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1].month, b = pts[i].month;
      const m = (+b.slice(0, 4) * 12 + +b.slice(5, 7)) - (+a.slice(0, 4) * 12 + +a.slice(5, 7));
      if (m > 1) gaps++;
    }
    const d = document.querySelector('#v-d-control path.ln').getAttribute('d');
    return { years: years.size, labs: new Set(labs).size, gaps, moves: (d.match(/M/g) || []).length };
  });
  ok(r.labs >= r.years, 'control chart labels ' + r.labs + ' of ' + r.years + ' years');
  ok(r.moves >= r.gaps + 1, 'control chart line joins across ' + r.gaps + ' gaps (' + r.moves + ' segments)');
});

check('no-placeholders-or-doubled-words-in-copy', DESK, async (ctx) => {
  const p = await open(ctx);
  const t = await p.evaluate(() => document.body.innerText);
  for (const bad of ['[unrun]', 'none (none)', '[missing', 'n/a%', 'sits -']) ok(t.indexOf(bad) < 0, 'page text contains "' + bad + '"');
});

check('diagram-text-fits-its-boxes', DESK, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => {
    const out = [];
    const g = document.querySelector('#an-a figure.illus g.il');
    const rects = Array.from(g.querySelectorAll('rect')).map((x) => x.getBoundingClientRect());
    Array.from(g.querySelectorAll('text')).forEach((t) => {
      const b = t.getBoundingClientRect();
      const box = rects.find((r) => b.left >= r.left - 1 && b.left <= r.right && b.top >= r.top - 2 && b.bottom <= r.bottom + 2);
      if (box && b.right > box.right + 0.5) out.push(t.textContent + ' +' + (b.right - box.right).toFixed(0) + 'px');
    });
    return out;
  });
  ok(!r.length, 'text overflows its box: ' + r.join('; '));
  await p.click('#hm-body tr[data-ward="01"] .ward-btn');
  const c = await p.evaluate(() => {
    const s = document.querySelector('#drill-body svg').getBoundingClientRect();
    return Array.from(document.querySelectorAll('#drill-body svg text')).filter((t) => t.getBoundingClientRect().left < s.left - 0.5).map((t) => t.textContent);
  });
  ok(!c.length, 'drill labels cut at the left: ' + c.join('; '));
});

check('scorecard-note-names-the-right-reset', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.click('[data-hbasis="city"]');
  await p.click('#btn-reset');
  const t = await p.textContent('#hm-filter-note');
  ok(!/Reset to see/.test(t) && /Refusal flag/.test(t), 'note after Reset filters: "' + t + '"');
});

check('shared-links-scroll-to-the-report-and-reject-bad-values', DESK, async (ctx) => {
  let p = await open(ctx, '#report?district=Nowhere&year=1999&type=Castle');
  let s = await p.evaluate(() => ({ d: NLU.state.district, y: NLU.state.eval_year, t: NLU.state.property_type }));
  ok(!s.d && !s.y && !s.t, 'invalid link values accepted: ' + JSON.stringify(s));
  p = await open(ctx, '#report?district=Scarborough&type=TCHC');
  await p.waitForTimeout(400);
  const top = await p.evaluate(() => document.getElementById('report').getBoundingClientRect().top);
  ok(Math.abs(top) < 160, 'a shared filter link opens away from the report (report top at ' + top.toFixed(0) + ' px)');
});

check('ward-list-follows-the-district', DESK, async (ctx) => {
  const p = await open(ctx);
  await setSel(p, 'sl-district', 'Scarborough');
  const dis = await p.evaluate(() => document.querySelector('#sl-ward option[value="01"]').disabled);
  ok(dis, 'ward 01 still offered while the district is Scarborough');
  await p.evaluate(() => NLU.setFilter('ward', '01'));
  const s = await p.evaluate(() => NLU.state.district + '|' + NLU.state.wards.join(','));
  ok(s === '|01', 'picking a ward outside the district leaves an empty view: ' + s);
});

check('tier-column-sorts-by-weight', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { document.getElementById('pg-fix').hidden = false; document.querySelector('#pg-fix details').open = true; });
  const btn = p.locator('#ppf thead button', { hasText: 'Tier' });
  await btn.click();
  const seq = await p.evaluate(() => Array.from(document.querySelectorAll('#ppf tbody tr')).map((r) => r.cells[2].textContent.split(' ')[0]));
  const w = { High: 3, Moderate: 2, Cosmetic: 0.5 };
  const sorted = seq.every((x, i) => i === 0 || w[seq[i - 1]] >= w[x]) || seq.every((x, i) => i === 0 || w[seq[i - 1]] <= w[x]);
  ok(sorted, 'tier order after sorting: ' + Array.from(new Set(seq)).join(', '));
});

check('removing-a-chip-keeps-keyboard-focus', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { NLU.setFilter('ward', '01'); NLU.setFilter('eval_year', '2024'); });
  await p.focus('#chips button');
  await p.keyboard.press('Enter');
  const tag = await p.evaluate(() => document.activeElement.tagName + '#' + document.activeElement.id);
  ok(!/^BODY/.test(tag), 'focus fell to ' + tag);
});

check('drawer-closes-when-focus-leaves-it', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.focus('[data-drawer="measures"]');
  await p.keyboard.press('Enter');
  await p.keyboard.press('Tab'); await p.keyboard.press('Tab');
  const r = await p.evaluate(() => ({ hidden: document.getElementById('drawer').hidden, inside: document.getElementById('drawer').contains(document.activeElement) }));
  ok(r.hidden || r.inside, 'focus left the open drawer and it stayed open');
});

/* ------------------------------------------------------------ accessibility */
check('every-focusable-chart-mark-has-a-name', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { ['pg-pillars', 'pg-fix', 'pg-trend'].forEach((id) => { document.getElementById(id).hidden = false; }); NLU.redraw(); });
  const n = await p.evaluate(() => Array.from(document.querySelectorAll('svg [tabindex="0"]')).filter((e) => !(e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.querySelector('title'))).length);
  ok(n === 0, n + ' focusable chart marks have no accessible name');
});

check('forecast-sliders-have-names', DESK, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => ['fl-h', 'fl-o'].map((id) => { const e = document.getElementById(id); return id + ':' + e.labels.length + ':' + (e.getAttribute('aria-valuetext') || ''); }));
  ok(r.every((s) => /:[1-9]:.+/.test(s)), 'slider names and value texts: ' + r.join(' '));
});

check('table-toggle-keeps-its-name', DESK, async (ctx) => {
  const p = await open(ctx);
  const b = p.locator('.tbl-btn').first();
  await b.click();
  const r = await b.evaluate((e) => e.textContent + '|' + e.getAttribute('aria-pressed'));
  ok(r === 'Table|true', 'Table toggle after a click reads ' + r);
});

check('headings-do-not-skip-a-level', DESK, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => { const hs = Array.from(document.querySelectorAll('main h1, main h2, main h3, main h4')).filter((h) => h.offsetParent !== null || h.closest('[hidden]') === null); const bad = []; for (let i = 1; i < hs.length; i++) { const a = +hs[i - 1].tagName[1], b = +hs[i].tagName[1]; if (b > a + 1) bad.push(hs[i - 1].tagName + '>' + hs[i].tagName + ' "' + hs[i].textContent.slice(0, 30) + '"'); } return bad; });
  ok(!r.length, 'heading skips: ' + r.slice(0, 5).join('; '));
});

check('band-bar-labels-have-text-contrast', DESK, async (ctx) => {
  const p = await open(ctx);
  const lum = (c) => { const m = c.match(/[\d.]+/g).slice(0, 3).map((x) => { x = +x / 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; };
  for (const theme of ['light', 'dark']) {
    await p.evaluate((t) => { document.documentElement.setAttribute('data-theme', t); NLU.redraw(['v-ptype']); }, theme);
    const pairs = await p.evaluate(() => [].concat.apply([], Array.from(document.querySelectorAll('#v-ptype text.vlab')).map((t) => {
      const b = t.getBoundingClientRect(), y = b.top + b.height / 2;
      return [b.left + 2, b.right - 2].map((x) => {   // under both ends of the label
        const r = Array.from(document.querySelectorAll('#v-ptype rect')).reverse().find((q) => { const c = q.getBoundingClientRect(); return x >= c.left && x <= c.right && y >= c.top && y <= c.bottom; });
        return [getComputedStyle(t).fill, r ? getComputedStyle(r).fill : null];
      });
    })).filter((x) => x[1]));
    const worst = Math.min.apply(null, pairs.map((x) => { const a = lum(x[0]), b = lum(x[1]); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); }));
    ok(worst >= 4.5, theme + ' theme: worst in-bar label contrast ' + worst.toFixed(2) + ':1');
  }
});

check('noscript-note-and-non-affiliation', DESK, async (ctx) => {
  const p = await open(ctx);
  const html = fs.readFileSync(path.resolve(pageArg), 'utf8');
  ok(/<noscript>/.test(html), 'no <noscript> note for readers without JavaScript');
  const t = await p.evaluate(() => document.body.innerText);
  ok(/not produced by, affiliated with or endorsed by the City of Toronto/.test(t), 'no independence statement');
  ok(!/Do not suggest official status/.test(t), 'the licence instruction is printed to readers verbatim');
  const src = await p.evaluate(() => NL.sc && document.querySelectorAll('a[href*="rentsafeto-colour-coded-signs"]').length);
  ok(src > 0, 'the sign bands carry no source link');
});

check('hero-claims-match-the-analysis', DESK, async (ctx) => {
  const p = await open(ctx);
  const t = await p.textContent('.brief-card');
  ok(!/as well as our model/i.test(t), 'hero says the last score predicted as well as the model');
  ok(!/re-evaluated buildings/.test(t), 'hero counts evaluation pairs as buildings');
  const tiles = await p.textContent('.proof');
  ok(/U\.S\. retail/.test(tiles) && /seasonal-naive/i.test(tiles), 'forecast tile does not name its subject or the seasonal-naive yardstick');
});

/* ------------------------------------------------------------ phones */
check('phone-scorecard-is-the-real-table', PHONE, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => ({ d: getComputedStyle(document.querySelector('.hm-scroll')).display, rows: document.querySelectorAll('#hm-body tr').length }));
  ok(r.d !== 'none', 'the scorecard table is hidden on phones');
  await p.click('#hm-body tr[data-ward="01"] .ward-btn');
  ok(!(await p.evaluate(() => document.getElementById('drill').hidden)), 'tapping a ward does not open its detail on a phone');
}, { mobile: true });

for (const w of [320, 360, 375, 390]) {
  check('no-sideways-scroll-at-' + w, { width: w, height: 800 }, async (ctx) => {
    const p = await open(ctx);
    await p.evaluate(() => { document.querySelectorAll('.page[role="tabpanel"]').forEach((x) => { x.hidden = false; }); document.querySelectorAll('details').forEach((d) => { d.open = true; }); NLU.redraw(); });
    await p.waitForTimeout(200);
    const r = await p.evaluate(() => {
      const W = document.documentElement.clientWidth, sw = document.documentElement.scrollWidth;
      const off = [];
      if (sw > W) document.querySelectorAll('body *').forEach((e) => { const b = e.getBoundingClientRect(); if (b.right > W + 1 && b.width > 0 && !e.closest('.tscroll,.hm-scroll,.vtable,.model-wrap,pre,.illus-scroll,.tabs')) off.push(e.tagName + '.' + String(e.className).slice(0, 20) + ' ' + b.right.toFixed(0)); });
      return { W, sw, off: off.slice(0, 4) };
    });
    ok(r.sw <= r.W, 'page is ' + r.sw + ' px wide in a ' + r.W + ' px window: ' + r.off.join('; '));
  }, { mobile: true });
}

check('phone-chart-labels-fit-and-do-not-collide', PHONE, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => {
    const out = [];
    ['v-d-shape', 'v-a-items', 'v-b-rel', 'v-map', 'v-season', 'v-wards'].forEach((id) => {
      const svg = document.querySelector('#' + id + ' svg'); if (!svg) return;
      const s = svg.getBoundingClientRect();
      const ts = Array.from(svg.querySelectorAll('text')).map((t) => ({ s: t.textContent, b: t.getBoundingClientRect() }));
      ts.forEach((t) => { if (t.b.left < s.left - 1 || t.b.right > s.right + 1) out.push(id + ' cut "' + t.s.slice(0, 24) + '"'); });
      if (id === 'v-d-shape' || id === 'v-season') for (let i = 0; i < ts.length; i++) for (let j = i + 1; j < ts.length; j++) {
        const a = ts[i].b, b = ts[j].b;
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) out.push(id + ' "' + ts[i].s.slice(0, 16) + '" hits "' + ts[j].s.slice(0, 16) + '"');
      }
    });
    return out;
  });
  ok(!r.length, r.length + ' label problems: ' + r.slice(0, 6).join('; '));
}, { mobile: true });

// chart labels in the trend must not sit on its lines, dots or the method-change rule
async function trendLabelHits(p) {
  await p.evaluate(() => { document.querySelectorAll('.page[role="tabpanel"]').forEach((x) => { x.hidden = x.id !== 'pg-trend'; }); NLU.redraw(['v-trend']); });
  await p.waitForTimeout(80);
  return p.evaluate(() => {
    const svg = document.querySelector('#v-trend svg');
    const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
    const labels = Array.from(svg.querySelectorAll('text')).filter((t) => /method/.test(t.textContent));
    const dots = Array.from(svg.querySelectorAll('circle')).map((c) => c.getBoundingClientRect());
    const rules = Array.from(svg.querySelectorAll('line[stroke-dasharray]')).map((l) => l.getBoundingClientRect());
    const m = svg.getScreenCTM(), pts = [];
    svg.querySelectorAll('path.ln').forEach((pa) => {
      const L = pa.getTotalLength();
      for (let s = 0; s <= L; s += 2) { const q = pa.getPointAtLength(s); pts.push([m.a * q.x + m.e, m.d * q.y + m.f]); }
    });
    const out = [];
    labels.forEach((t) => {
      const b = t.getBoundingClientRect();
      if (dots.some((d) => hit(b, d))) out.push('"' + t.textContent + '" on a dot');
      if (pts.some((q) => q[0] > b.left && q[0] < b.right && q[1] > b.top && q[1] < b.bottom)) out.push('"' + t.textContent + '" on a line');
      if (rules.some((r) => hit(b, { left: r.left - 1, right: r.right + 1, top: r.top, bottom: r.bottom }))) out.push('"' + t.textContent + '" on the method-change rule');
      const s = svg.getBoundingClientRect();
      if (b.left < s.left - 1 || b.right > s.right + 1) out.push('"' + t.textContent + '" cut at the edge');
    });
    return { n: labels.length, out, names: labels.map((t) => t.textContent) };
  });
}
for (const [nm, vp, mob] of [['desktop', DESK, false], ['phone', PHONE, true]]) {
  check('trend-labels-clear-of-lines-and-dots-' + nm, vp, async (ctx) => {
    const p = await open(ctx);
    const r = await trendLabelHits(p);
    ok(r.names.indexOf('new method, weighted items') >= 0, 'the trend has no "new method, weighted items" label: ' + r.names.join(' | '));
    ok(!r.out.length, r.out.join('; '));
  }, { mobile: mob });
}

check('phone-scrollers-are-named-and-tabs-wrap', PHONE, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => ({
    unnamed: Array.from(document.querySelectorAll('.tscroll')).filter((e) => !(e.getAttribute('tabindex') === '0' && e.getAttribute('role') === 'region' && e.getAttribute('aria-label'))).length,
    tabs: (() => { const t = document.querySelector('.tabs'); return t.scrollWidth - t.clientWidth; })(),
    cue: document.querySelectorAll('.swipe-cue').length
  }));
  ok(r.unnamed === 0, r.unnamed + ' sideways scrollers have no focus, role or name');
  ok(r.tabs <= 1, 'report tabs run ' + r.tabs + ' px off screen');
  ok(r.cue > 0, 'no visible cue that wide tables scroll sideways');
}, { mobile: true });

check('phone-copy-fits-touch', PHONE, async (ctx) => {
  const p = await open(ctx);
  const t = await p.textContent('#report .lede');
  ok(/tap/i.test(t), 'report lede speaks only of clicks and Ctrl/Cmd');
  const cta = await p.evaluate(() => document.querySelector('.topbar .cta').innerText.replace(/\s+/g, ' ').trim());
  ok(cta === 'Book audit', 'header button reads "' + cta + '"');
}, { mobile: true });

check('phone-diagrams-are-legible', PHONE, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => Array.from(document.querySelectorAll('figure.illus svg')).map((s) => { const m = s.getScreenCTM(); const f = Math.min.apply(null, Array.from(s.querySelectorAll('text')).map((t) => parseFloat(getComputedStyle(t).fontSize))); return +(m.a * f).toFixed(1); }));
  ok(r.every((x) => x >= 9), 'diagram text on a phone is ' + r.join(', ') + ' px');
}, { mobile: true });

check('phone-header-is-short', PHONE, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => window.scrollTo({ top: 6000, behavior: 'instant' }));
  const h = await p.evaluate(() => document.querySelector('.topbar').getBoundingClientRect().height);
  ok(h <= 70, 'sticky header is ' + h.toFixed(0) + ' px tall on a phone');
}, { mobile: true });

check('phone-menu-closes-on-outside-tap-and-scroll', PHONE, async (ctx) => {
  const p = await open(ctx);
  await p.click('.menu-btn');
  await p.mouse.click(200, 700);
  let open1 = await p.evaluate(() => document.getElementById('nav-links').classList.contains('open'));
  ok(!open1, 'menu stays open after a tap outside it');
  await p.click('.menu-btn');
  await p.evaluate(() => { window.scrollTo({ top: 3000, behavior: 'instant' }); window.dispatchEvent(new Event('scroll')); });
  await p.waitForTimeout(100);
  open1 = await p.evaluate(() => document.getElementById('nav-links').classList.contains('open'));
  ok(!open1, 'menu stays open after scrolling');
}, { mobile: true });

check('phone-touch-targets-44px', PHONE, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { NLU.setFilter('ward', '01'); });
  const r = await p.evaluate(() => {
    const out = [];
    document.querySelectorAll('.tb, .sg, .menu-btn, #theme-btn, .topbar .cta, .slicers select, .chip button, .chooser label').forEach((e) => {
      const b = e.getBoundingClientRect(); if (!b.width) return;
      if (b.height < 43.5) out.push((e.id || e.className || e.tagName) + ' ' + b.height.toFixed(0));
    });
    return out;
  });
  ok(!r.length, r.length + ' controls under 44 px tall: ' + r.slice(0, 5).join('; '));
}, { mobile: true });

/* ------------------------------------------------------------ the replay page */
const DEMO = FILE.replace(/index\.html$/, 'agent-demo.html');
async function openDemo(ctx, hash) {
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(DEMO + (hash || ''));
  await page.waitForFunction(() => window.SESSIONS && document.querySelectorAll('.session-card').length === window.SESSIONS.sessions.length);
  page.__errs = errs;
  return page;
}
// what the feed shows against the first messages of one recorded session, message by message
async function feedAgainst(p, name) {
  return p.evaluate((name) => {
    const S = window.SESSIONS.sessions, s = S.find((x) => x.name === name);
    const norm = (t) => String(t || '').replace(/[^A-Za-z0-9]/g, '').slice(0, 120);
    const kids = Array.from(document.getElementById('chat-feed').children).filter((e) => e.classList.contains('msg') && !/Deliverables/.test((e.querySelector('.who') || {}).textContent || ''));
    const got = kids.map((e) => {
      if (e.classList.contains('tool')) return 'tool:' + ((e.querySelector('.tool-name') || {}).textContent || '');
      const kind = e.classList.contains('user') ? 'user' : e.classList.contains('note') ? 'note' : 'agent';
      return kind + ':' + norm((e.querySelector('.bubble') || {}).textContent);
    });
    const want = s.chat.slice(0, kids.length).map((m) => m.who === 'user' || m.who === 'agent' || m.who === 'note' ? m.who + ':' + norm(m.text) : 'tool:' + m.tool);
    const bad = got.findIndex((g, i) => g !== want[i]);
    const active = Array.from(document.querySelectorAll('.session-card')).map((c, i) => c.classList.contains('active') ? S[i].name : null).filter(Boolean);
    return { n: kids.length, bad, got: bad >= 0 ? got[bad].slice(0, 60) : '', want: bad >= 0 ? (want[bad] || 'nothing').slice(0, 60) : '',
      replays: document.querySelectorAll('#btn-replay').length, deliverables: Array.from(document.querySelectorAll('#chat-feed a.dl-btn')).map((a) => a.getAttribute('download')),
      mine: (s.downloads || []).map((d) => d.name), active };
  }, name);
}
// switching to another session while one plays must stop the first one: one transcript, one report
check('replay-switch-plays-only-the-chosen-session', DESK, async (ctx) => {
  const p = await openDemo(ctx);
  await p.waitForTimeout(3000);
  const target = await p.evaluate(() => window.SESSIONS.sessions[2].name);
  await p.click('.session-card >> nth=2');
  await p.waitForTimeout(4000);
  const r = await feedAgainst(p, target);
  ok(r.n >= 3, 'the chosen session did not start playing (' + r.n + ' messages)');
  ok(r.bad < 0, 'after switching to ' + target + ' the feed mixes sessions: message ' + (r.bad + 1) + ' is "' + r.got + '", the session has "' + r.want + '"');
  ok(r.active.length === 1 && r.active[0] === target, 'active card: ' + r.active.join(', '));
  ok(r.replays <= 1, r.replays + ' Replay buttons share one id');
  ok(r.deliverables.every((d) => r.mine.indexOf(d) >= 0), 'deliverables from another session: ' + r.deliverables.join(', '));
  ok(!p.__errs.length, 'page errors: ' + p.__errs.join('; '));
});

// each "Open the replays" link names its session, and the replay page opens and plays that one
check('replay-links-open-the-named-session', DESK, async (ctx) => {
  const p = await open(ctx);
  const hrefs = await p.evaluate(() => Array.from(document.querySelectorAll('.rp-card a')).map((a) => a.getAttribute('href')));
  ok(hrefs.length === 3, hrefs.length + ' replay cards');
  const ids = hrefs.map((h) => (h.match(/^agent-demo\.html#(.+)$/) || [])[1]);
  ok(ids.every(Boolean) && new Set(ids).size === 3, 'replay links do not each name a session: ' + hrefs.join(', '));
  for (const id of ids) {
    const d = await openDemo(ctx, '#' + id);
    await d.waitForTimeout(1200);
    const r = await feedAgainst(d, id);
    ok(r.active.length === 1 && r.active[0] === id, id + ': the active card is ' + r.active.join(', '));
    ok(r.n >= 1 && r.bad < 0, id + ': the feed does not play that session (message ' + (r.bad + 1) + ' is "' + r.got + '")');
    await d.close();
  }
  const d = await openDemo(ctx, '#no-such-session');
  await d.waitForTimeout(800);
  const first = await d.evaluate(() => window.SESSIONS.sessions[0].name);
  const r = await feedAgainst(d, first);
  ok(r.n >= 1 && r.bad < 0 && r.active[0] === first, 'an unknown session name does not fall back to the first session');
});

/* ------------------------------------------------------------ drawer, explorer, diagrams, charts */
async function drawerOverflow(p, panel) {
  await p.click('[data-drawer="' + panel + '"]');
  await p.waitForTimeout(80);
  return p.evaluate(() => {
    const dr = document.getElementById('drawer'), D = dr.getBoundingClientRect(), out = [];
    Array.from(dr.querySelectorAll('*')).forEach((e) => {
      if (e.closest('[hidden]') || e.closest('pre')) return;
      const b = e.getBoundingClientRect();
      if (!b.width) return;
      if (b.right > D.right + 0.5 || b.left < D.left - 0.5) out.push(e.tagName.toLowerCase() + (e.getAttribute('class') ? '.' + String(e.getAttribute('class')).split(' ')[0] : '') + ' ' + b.left.toFixed(0) + '-' + b.right.toFixed(0) + ' in ' + D.left.toFixed(0) + '-' + D.right.toFixed(0));
    });
    const svg = dr.querySelector('.dpanel:not([hidden]) svg.model'), boxes = [];
    if (svg) svg.querySelectorAll('g').forEach((g) => {
      const r = g.querySelector('rect').getBoundingClientRect(), t = g.querySelector('text.mt').getBoundingClientRect();
      boxes.push({ name: g.querySelector('text.mt').textContent, inside: r.left >= D.left - 0.5 && r.right <= D.right + 0.5, fits: t.right <= r.right + 0.5,
        px: +(parseFloat(getComputedStyle(g.querySelector('text.ms')).fontSize) * svg.getScreenCTM().a).toFixed(1) });
    });
    const rel = svg ? Array.from(svg.querySelectorAll('path.mrel')).map((x) => { const b = x.getBoundingClientRect(); return b.right <= D.right + 0.5 && b.left >= D.left - 0.5; }) : [];
    return { out: out.slice(0, 5), n: out.length, boxes, rel };
  });
}
for (const [nm, vp, mob] of [['desktop', DESK, false], ['phone', PHONE, true]]) {
  check('model-drawer-fits-its-width-' + nm, vp, async (ctx) => {
    const p = await open(ctx);
    const r = await drawerOverflow(p, 'model');
    ok(!r.n, r.n + ' elements in the Model drawer reach past its edges: ' + r.out.join('; '));
    ok(r.boxes.length >= 2 && r.boxes.every((b) => b.inside && b.fits), 'model boxes cut off or overflowing: ' + r.boxes.filter((b) => !b.inside || !b.fits).map((b) => b.name).join(', '));
    ok(r.rel.length >= 1 && r.rel.every(Boolean), 'the relationship line is not inside the drawer');
    ok(r.boxes.every((b) => b.px >= 9), 'model diagram text is ' + r.boxes.map((b) => b.px).join(', ') + ' px on screen');
    await p.keyboard.press('Escape');
    for (const other of ['measures', 'practice']) {
      const o = await drawerOverflow(p, other);
      ok(!o.n, o.n + ' elements in the ' + other + ' drawer reach past its edges: ' + o.out.join('; '));
      await p.keyboard.press('Escape');
    }
  }, { mobile: mob });
}

check('phone-pbip-file-opens-in-view', PHONE, async (ctx) => {
  const p = await open(ctx);
  const n = await p.evaluate(() => document.querySelectorAll('button.pf').length);
  ok(n >= 2, 'only ' + n + ' highlighted files');
  const bad = [];
  for (let i = 0; i < n; i++) {
    await p.click('button.pf >> nth=' + i);
    await p.waitForTimeout(60);
    const r = await p.evaluate((i) => {
      const b = document.querySelectorAll('button.pf')[i], s = document.getElementById(b.getAttribute('aria-controls')), q = s.getBoundingClientRect();
      return { name: b.textContent, open: !s.hidden, top: q.top, h: window.innerHeight };
    }, i);
    if (!r.open || r.top < 0 || r.top > r.h - 60) bad.push(r.name + ' opens at y=' + r.top.toFixed(0) + ' in a ' + r.h + ' px screen');
  }
  ok(!bad.length, bad.length + ' of ' + n + ' files open out of view: ' + bad.slice(0, 3).join('; '));
}, { mobile: true });

check('phone-wide-diagrams-say-they-scroll', PHONE, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { window.dispatchEvent(new Event('load')); });
  await p.waitForTimeout(100);
  const r = await p.evaluate(() => Array.from(document.querySelectorAll('.illus-scroll')).filter((e) => e.scrollWidth > e.clientWidth + 2).map((e) => {
    const c = e.previousElementSibling;
    return { cue: !!(c && c.classList.contains('swipe-cue') && !c.hidden), cls: e.classList.contains('can-scroll') };
  }));
  ok(r.length > 0 && r.every((x) => x.cue && x.cls), r.filter((x) => !x.cue || !x.cls).length + ' of ' + r.length + ' wide diagrams have no sideways cue or fade');
}, { mobile: true });

check('phone-metrics-table-keeps-a-readable-first-column', PHONE, async (ctx) => {
  const p = await open(ctx);
  const w = await p.evaluate(() => Array.from(document.querySelectorAll('table.bm tbody th')).map((th) => th.getBoundingClientRect().width).filter((x) => x > 0));
  ok(w.length >= 1 && w.every((x) => x >= 170), 'Analysis B metrics: first column ' + w.map((x) => x.toFixed(0)).join(', ') + ' px wide');
}, { mobile: true });

// the run rule's flags are drawn, not only listed; the limits sentence counts eligible months only
check('control-chart-marks-the-run-it-flags', DESK, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => {
    const c = NL.D.control, runs = c.points.filter((x) => /^run /.test(x.flag)).length;
    const svg = document.querySelector('#v-d-control svg');
    const labels = Array.from(svg.querySelectorAll('text')).map((t) => t.textContent);
    return { runs, rings: svg.querySelectorAll('.mk-run').length, legend: labels.some((t) => /run/.test(t) && /ring/.test(t)), min: c.min_n,
      text: document.getElementById('an-d').textContent.replace(/\s+/g, ' '), beyond: c.months_beyond_limits };
  });
  ok(r.runs > 0, 'no run-flagged month in the data; the check needs one');
  ok(r.rings === r.runs, r.rings + ' ringed marks for ' + r.runs + ' months the run rule flags');
  ok(r.legend, 'no legend entry for the ringed run marks');
  ok(r.text.indexOf('no single month crosses them') < 0, 'the limits sentence still says "no single month crosses them"');
  ok(r.beyond !== 0 || (r.min && r.text.indexOf('no eligible month (' + r.min + ' or more evaluations) crosses them') >= 0), 'the limits sentence does not say which months it judges (min_n ' + r.min + ')');
});

// one ward in view: its rank is the citywide one, and the ranked list asks for more wards
check('single-ward-view-ranks-citywide', PHONE, async (ctx) => {
  const p = await open(ctx);
  await p.evaluate(() => { NLU.setFilter('ward', '11'); });
  await p.waitForTimeout(80);
  await p.click('#hm-body tr[data-ward="11"] .ward-btn');
  const r = await p.evaluate(() => {
    const w = NL.sc.wards.find((x) => x.ward === '11');
    return { t: document.getElementById('drill-body').textContent, strip: document.getElementById('v-strip').textContent,
      cell: document.querySelector('#hm-body tr[data-ward="11"] td[data-col="rank"]').textContent.trim(), rank: w.rank, n: NL.sc.wards.length };
  });
  ok(r.t.indexOf('Rank 1 of 1') < 0, 'the drill says "Rank 1 of 1"');
  ok(r.t.indexOf('Rank ' + r.rank + ' of ' + r.n + ' wards citywide') >= 0, 'the drill does not give the citywide rank ' + r.rank + ' of ' + r.n + ': "' + (r.t.match(/Rank[^.]*\./) || ['?'])[0] + '"');
  ok(r.cell === String(r.rank), 'the rank cell says ' + r.cell + ', citywide rank is ' + r.rank);
  ok(/Pick more than one ward to rank them/.test(r.strip) && r.strip.indexOf('between the lowest') < 0, 'the ranked list for one ward: "' + r.strip.slice(0, 120) + '"');
  await p.click('#drill-close');
  await p.evaluate(() => { NLU.setFilter('ward', '12', { toggle: true, multi: true }); });
  await p.waitForTimeout(80);
  await p.click('#hm-body tr[data-ward="11"] .ward-btn');
  const t2 = await p.textContent('#drill-body');
  ok(/Rank [12] of 2 wards in view/.test(t2), 'two wards in view: "' + (t2.match(/Rank[^.]*\./) || ['?'])[0] + '"');
}, { mobile: true });

// Analysis B's ward chart follows the small-cell rule too
check('risk-by-ward-withholds-small-wards', DESK, async (ctx) => {
  const p = await open(ctx);
  const show = () => p.evaluate(() => { document.querySelectorAll('.page[role="tabpanel"]').forEach((x) => { x.hidden = x.id !== 'pg-trend'; }); NLU.redraw(['v-risk']); });
  await show();
  const r = await p.evaluate(() => {
    const small = NL.B.by_ward.filter((w) => w.n_pairs_test > 0 && w.n_pairs_test < NLU.MIN_N).map((w) => w.ward);
    const tips = Array.from(document.querySelectorAll('#v-risk [data-tip]')).map((e) => e.getAttribute('data-tip'));
    return { small, shown: small.filter((w) => tips.some((t) => t.indexOf('Ward ' + w + ' ') === 0)), text: document.getElementById('v-risk').textContent };
  });
  ok(r.small.length > 0, 'no ward has fewer than 5 test pairs; the check needs one');
  ok(!r.shown.length, 'wards under 5 test pairs are drawn: ' + r.shown.join(', '));
  ok(/withheld|not shown/i.test(r.text) && r.text.indexOf(String(r.small.length)) >= 0, 'the chart does not say how many wards it withholds: "' + r.text.slice(0, 100) + '"');
  await p.evaluate(() => { document.getElementById('v-risk').closest('figure').querySelector('.tbl-btn').click(); });
  await p.waitForTimeout(60);
  const rows = await p.evaluate(() => Array.from(document.querySelectorAll('#v-risk tbody th')).map((th) => th.textContent.split(' ')[0]));
  const leaked = r.small.filter((w) => rows.indexOf(w) >= 0);
  ok(rows.length > 0 && !leaked.length, 'the Table view lists wards under 5 test pairs: ' + leaked.join(', '));
});

// the replay page keeps a side margin on phones: no heading or paragraph touches the screen edge
check('replay-text-has-side-margins-on-phone', PHONE, async (ctx) => {
  const p = await openDemo(ctx);
  const r = await p.evaluate(() => {
    const W = document.documentElement.clientWidth;
    return Array.from(document.querySelectorAll('h1, h2, p, footer'))
      .filter((e) => e.offsetParent !== null && e.textContent.trim() && !e.closest('#chat-feed, .session-card, pre, table'))
      .map((e) => { const b = e.getBoundingClientRect(); return { t: e.textContent.trim().slice(0, 40), l: b.left, r: W - b.right }; })
      .filter((x) => x.l < 12 || x.r < 12);
  });
  ok(!r.length, r.length + ' text blocks touch the edge, e.g. "' + (r[0] || {}).t + '" (left ' + Math.round((r[0] || {}).l) + ' px, right ' + Math.round((r[0] || {}).r) + ' px)');
}, { mobile: true });

/* ------------------------------------------------------------ runner */
(async () => {
  const browser = await playwright.chromium.launch({ executablePath: CHROME, headless: true });
  let failed = 0, ran = 0;
  for (const c of checks) {
    if (ONLY.size && !ONLY.has(c.name)) continue;
    ran++;
    const ctx = await browser.newContext({ viewport: c.viewport, isMobile: !!c.opts.mobile, hasTouch: !!c.opts.mobile, reducedMotion: 'reduce' });
    try {
      await c.fn(ctx);
      console.log('UI PASS ' + c.name);
    } catch (e) {
      failed++;
      console.log('UI FAIL ' + c.name + ': ' + String(e.message || e).split('\n')[0]);
    }
    await ctx.close();
  }
  await browser.close();
  console.log(failed ? 'UI CHECKS: ' + failed + ' of ' + ran + ' FAILED' : 'UI CHECKS: ALL ' + ran + ' PASS');
  process.exit(failed ? 1 : 0);
})();
