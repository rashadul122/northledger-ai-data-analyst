#!/usr/bin/env node
/* Behaviour checks for the built page, run in headless Chrome through Playwright.
   Each check opens a fresh copy of the page and does what a reader would do (pick a slicer,
   press Esc, tap on a phone, switch to the dark theme), then asserts what the page shows.

     node tools/check_ui.js [path/to/index.html] [check-name ...]

   Environment: PLAYWRIGHT_MODULE (path to the playwright package; default: require('playwright')),
   CHROME (Chrome executable; default: the macOS system Chrome). Nothing here edits the page,
   starts a server or opens a port: the page is loaded from a file:// URL, and the "Try it" demo
   checks serve the site folder to Chrome through Playwright's request routing. The one check that
   runs the real engine loads Pyodide from cdn.jsdelivr.net and prints SKIP with the reason when
   that address cannot be reached.
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
// every page names its icon (review of the site, 24 Sep 2026: each load logged a 404 for /favicon.ico),
// and the files it names are in the site folder
check('every-page-links-a-favicon-that-exists', DESK, async (ctx) => {
  const dir = path.dirname(path.resolve(pageArg));
  for (const name of ['index.html', 'case-study.html', 'agent-demo.html']) {
    const p = await ctx.newPage();
    await p.goto('file://' + path.join(dir, name));
    const icons = await p.evaluate(() => Array.from(document.querySelectorAll('link[rel~="icon"]')).map((l) => [l.getAttribute('href'), l.getAttribute('type')]));
    ok(icons.some((i) => i[1] === 'image/svg+xml') && icons.some((i) => i[1] === 'image/png'), name + ' links no SVG and PNG icon: ' + JSON.stringify(icons));
    icons.forEach((i) => ok(!/^(\/|https?:)/.test(i[0]) && fs.existsSync(path.join(dir, i[0])), name + ': icon ' + i[0] + ' is not a file in the site folder'));
    await p.close();
  }
});

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

/* ------------------------------------------------------------ research collaboration
   A button in the header (nav, or the phone menu), right below the hero doors and in the Book/About
   area; each leads to #collaborate, whose primary button emails site.config.json contact_email with the
   subject "Research collaboration invitation" (owner's request, 24 Sep 2026). */
const COLLAB_SUBJECT = 'Research collaboration invitation';
function siteConfig() { return JSON.parse(fs.readFileSync(path.join(path.dirname(path.resolve(pageArg)), 'site.config.json'), 'utf8')); }
async function collabReach(p) {
  // what a reader can see and press: each button's box, and whether it is the top element at its centre
  return p.evaluate(() => {
    const one = (sel) => {
      const e = Array.from(document.querySelectorAll(sel)).find((x) => x.getBoundingClientRect().width > 0);
      if (!e) return { found: false };
      e.scrollIntoView({ block: 'center', behavior: 'instant' });
      const W = document.documentElement.clientWidth, b = e.getBoundingClientRect(), top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      return { found: true, name: e.textContent.replace(/\s+/g, ' ').trim(), left: b.left, right: b.right, W, onTop: !!top && (top === e || e.contains(top)) };
    };
    const l = document.getElementById('nav-links');
    return { hero: one('#top a[href="#collaborate"]'), book: one('#book a[href="#collaborate"], #about a[href="#collaborate"]'),
      navShown: !!l && getComputedStyle(l).display !== 'none' };
  });
}
check('collab-button-on-every-width', DESK, async (ctx) => {
  const p = await open(ctx);
  const bad = [];
  for (const w of [320, 360, 375, 390, 430, 600, 768, 861, 900, 1024, 1280, 1440]) {
    await p.setViewportSize({ width: w, height: 800 });
    await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await p.waitForTimeout(60);
    const r = await collabReach(p);
    for (const k of ['hero', 'book']) {
      const x = r[k];
      if (!x.found) { bad.push(w + ' px: no ' + k + ' button'); continue; }
      if (!/research collaboration/i.test(x.name)) bad.push(w + ' px: the ' + k + ' button reads "' + x.name + '"');
      if (x.left < 0 || x.right > x.W + 0.5) bad.push(w + ' px: the ' + k + ' button runs off screen (' + Math.round(x.left) + ' to ' + Math.round(x.right) + ' of ' + x.W + ')');
      if (!x.onTop) bad.push(w + ' px: the ' + k + ' button is covered');
    }
    // the header: the nav link on a wide window, the phone menu's link on a narrow one
    await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await p.waitForTimeout(30);
    if (!r.navShown) { await p.click('.menu-btn'); await p.waitForTimeout(40); }
    const h = await p.evaluate(() => {
      const a = document.querySelector('#topbar a[href="#collaborate"]'); if (!a) return null;
      const b = a.getBoundingClientRect(), W = document.documentElement.clientWidth, top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      const l = document.getElementById('nav-links'), bar = document.querySelector('.topbar .bar').getBoundingClientRect();
      return { w: b.width, left: b.left, right: b.right, W, onTop: !!top && (top === a || a.contains(top)), name: a.textContent.trim(),
        wraps: getComputedStyle(l).position !== 'absolute' && b.bottom > bar.bottom + 0.5 };
    });
    if (!h || !h.w) bad.push(w + ' px: no collaboration link in the header' + (r.navShown ? '' : ' menu'));
    else {
      if (h.left < 0 || h.right > h.W + 0.5 || !h.onTop) bad.push(w + ' px: the header link is off screen or covered');
      if (h.wraps) bad.push(w + ' px: the header links wrap below the bar');
      if (!/collaborat/i.test(h.name)) bad.push(w + ' px: the header link reads "' + h.name + '"');
    }
    if (!r.navShown) { await p.keyboard.press('Escape'); await p.waitForTimeout(20); }
  }
  ok(!bad.length, bad.length + ' problems: ' + bad.slice(0, 4).join('; '));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the header's links never wrap onto a second line and the wordmark is never cut, at any desktop width
// (the "Collaborate" link made "Try it" and "Power BI" wrap between 861 and 1056 px, 24 Sep 2026)
check('header-links-stay-on-one-line', DESK, async (ctx) => {
  const p = await open(ctx);
  const bad = [];
  for (let w = 861; w <= 1440; w += 3) {
    await p.setViewportSize({ width: w, height: 800 });
    await p.waitForTimeout(15);
    const r = await p.evaluate(() => {
      const l = document.getElementById('nav-links'), wm = document.querySelector('.topbar .wordmark');
      if (getComputedStyle(l).position === 'absolute') return { menu: true, wrapped: [], cut: wm.scrollWidth > wm.clientWidth + 1 };
      return { wrapped: Array.from(l.querySelectorAll('a')).filter((a) => a.getClientRects().length > 1).map((a) => a.textContent), cut: wm.scrollWidth > wm.clientWidth + 1 };
    });
    if (r.wrapped.length) bad.push(w + ' px: ' + r.wrapped.join(', ') + ' wrap');
    if (r.cut) bad.push(w + ' px: the wordmark is cut');
  }
  ok(!bad.length, bad.length + ' widths fail, e.g. ' + bad.slice(0, 4).join('; '));
});

check('collab-invite-emails-the-owner-with-the-subject', DESK, async (ctx) => {
  const p = await open(ctx);
  const cfg = siteConfig();
  const email = ((cfg.collaboration && cfg.collaboration.email) || cfg.contact_email || '').trim();
  ok(email, 'site.config.json has no collaboration.email or contact_email');
  ok(await p.$('#top a[href="#collaborate"]'), 'no research collaboration button below the hero doors');
  await p.click('#top a[href="#collaborate"]');
  await p.waitForTimeout(150);
  const r = await p.evaluate(() => {
    const s = document.getElementById('collaborate'), b = s.getBoundingClientRect();
    const inv = Array.from(s.querySelectorAll('a.btn-primary')).find((a) => /invite me to a project/i.test(a.textContent));
    return { top: b.top, vh: innerHeight, hash: location.hash, href: inv ? inv.getAttribute('href') : null };
  });
  ok(r.hash === '#collaborate' && r.top < r.vh * 0.5, 'the hero button does not bring the section into view: ' + JSON.stringify(r));
  ok(r.href, 'no primary "Invite me to a project" button in the section');
  const u = new URL(r.href);
  ok(u.protocol === 'mailto:' && decodeURIComponent(u.pathname) === email, 'the invitation goes to ' + u.pathname + ', not ' + email);
  ok(u.searchParams.get('subject') === COLLAB_SUBJECT, 'the subject is "' + u.searchParams.get('subject') + '"');
  const body = (u.searchParams.get('body') || '').toLowerCase();
  const miss = ['project', 'role', 'timeline', 'data', 'links'].filter((w) => body.indexOf(w) < 0);
  ok(!miss.length && body.length <= 400, 'the prefilled body misses ' + miss.join(', ') + ' or is long (' + body.length + ' characters)');
  // keyboard: Tab from the section heading reaches the invitation, with a visible focus ring
  await p.evaluate(() => { const h = document.querySelector('#collaborate h2'); h.setAttribute('tabindex', '-1'); h.focus(); });
  let reached = null;
  for (let i = 0; i < 12 && !reached; i++) {
    await p.keyboard.press('Tab');
    reached = await p.evaluate(() => { const a = document.activeElement; return a && /invite me to a project/i.test(a.textContent) ?
      { outline: getComputedStyle(a).outlineStyle, w: parseFloat(getComputedStyle(a).outlineWidth) } : null; });
  }
  ok(reached && reached.outline !== 'none' && reached.w >= 2, 'Tab does not reach the invitation with a visible focus ring: ' + JSON.stringify(reached));
});

for (const scheme of ['light', 'dark']) {
  check('collab-section-reads-at-390-in-' + scheme, PHONE, async (ctx) => {
    await ctx.addInitScript((t) => { try { localStorage.setItem('nl-theme', t); } catch (e) { /* the check below fails */ } }, scheme);
    const p = await open(ctx);
    ok(await p.$('#collaborate'), 'no #collaborate section');
    const r = await p.evaluate(() => {
      const rgb = (s) => { const m = /rgba?\(([^)]+)\)/.exec(s); if (!m) return null; const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return v.length > 3 && v[3] === 0 ? null : v.slice(0, 3); };
      const lum = (c) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
      const bgOf = (e) => { for (let x = e; x; x = x.parentElement) { const c = rgb(getComputedStyle(x).backgroundColor); if (c) return c; } return rgb(getComputedStyle(document.body).backgroundColor) || [255, 255, 255]; };
      const s = document.getElementById('collaborate'), W = document.documentElement.clientWidth;
      let worst = 99, where = '';
      const small = [], edge = [];
      s.querySelectorAll('*').forEach((e) => {
        const own = Array.from(e.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
        const b = e.getBoundingClientRect(); if (!own || !b.width) return;
        const cs = getComputedStyle(e), a = rgb(cs.color), bg = bgOf(e);
        if (!a) return;
        const L1 = lum(a), L2 = lum(bg), cr = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
        if (cr < worst) { worst = cr; where = e.tagName + ' "' + e.textContent.trim().slice(0, 30) + '"'; }
        if (parseFloat(cs.fontSize) < 12) small.push(e.tagName + ' ' + cs.fontSize);
        if (b.left < 12 || W - b.right < 12) edge.push(e.tagName + ' "' + e.textContent.trim().slice(0, 20) + '" ' + Math.round(b.left) + '/' + Math.round(W - b.right));
      });
      const inv = Array.from(s.querySelectorAll('a.btn-primary')).find((a) => /invite me to a project/i.test(a.textContent));
      return { theme: document.documentElement.getAttribute('data-theme'), worst: +worst.toFixed(2), where, small, edge, sw: document.documentElement.scrollWidth, W,
        btnH: inv ? inv.getBoundingClientRect().height : 0 };
    });
    ok(r.theme === scheme, 'the page is not in the ' + scheme + ' theme');
    ok(r.worst >= 4.5, 'text contrast in the collaboration section is ' + r.worst + ':1 at ' + r.where);
    ok(!r.small.length, 'text under 12 px (the site\'s smallest, its kickers): ' + r.small.slice(0, 3).join('; '));
    ok(!r.edge.length, 'text touches the screen edge: ' + r.edge.slice(0, 3).join('; '));
    ok(r.sw <= r.W, 'the page is ' + r.sw + ' px wide in a ' + r.W + ' px window');
    ok(r.btnH >= 44, 'the invitation button is ' + r.btnH + ' px tall on a phone');
  }, { mobile: true });
}

// the PL-300 card: the public repository and its data release, as links a reader can follow
check('pl300-card-links-the-public-repo-and-release', PHONE, async (ctx) => {
  const p = await open(ctx);
  const r = await p.evaluate(() => Array.from(document.querySelectorAll('#pl300 a')).filter((a) => a.getBoundingClientRect().width > 0).map((a) => a.getAttribute('href')));
  const repo = 'https://github.com/rashadul122/pl300-nyc311';
  ok(r.indexOf(repo) >= 0, 'the card does not link ' + repo + ': ' + r.join(', '));
  ok(r.indexOf(repo + '/releases/tag/data-v1') >= 0, 'the card does not link the data-v1 release: ' + r.join(', '));
  const t = (await p.textContent('#pl300')).replace(/\s+/g, ' ');
  // the state the public repository records (README, 25 Sep 2026): dev reconciled, the rest not yet run
  ok(/AI-built, owner-directed/.test(t) && /dev profile/.test(t) && /reconciled/.test(t) && /full profile/.test(t) && /not yet run/.test(t),
    'the card drops "AI-built, owner-directed" or what the repository records (dev profile reconciled; full profile not yet run)');
  ok(!/not yet run in Power BI|workspace is not set up|Microsoft work account/i.test(t), 'the card still says what the repository now contradicts: ' +
    ((/not yet run in Power BI|workspace is not set up|Microsoft work account/i.exec(t) || [''])[0]));
}, { mobile: true });

// the milestone badges: one line each, never stretched to the height of a wrapped milestone name
// (review, 25 Sep 2026: 50 to 74 px tall boxes at 390 px, 25 px at 1280)
for (const w of [320, 360, 390, 1280]) {
  check('pl300-milestone-badges-stay-one-line-at-' + w, { width: w, height: 844 }, async (ctx) => {
    const p = await open(ctx);
    const r = await p.evaluate(() => Array.from(document.querySelectorAll('#pl300 .milestones .status')).map((e) => {
      const b = e.getBoundingClientRect(), li = e.closest('li').getBoundingClientRect();
      return { t: e.textContent.trim(), h: Math.round(b.height), right: Math.round(li.right - b.right), inRow: b.top >= li.top - 0.5 && b.bottom <= li.bottom + 0.5 };
    }));
    ok(r.length >= 5, 'fewer than five milestone badges: ' + r.length);
    const tall = r.filter((x) => x.h > 30);
    ok(!tall.length, 'milestone badges taller than one line: ' + tall.map((x) => x.t + ' ' + x.h + ' px').join('; '));
    ok(r.every((x) => x.inRow && x.right >= -0.5), 'a milestone badge leaves its row: ' + JSON.stringify(r.filter((x) => !x.inRow || x.right < -0.5)));
    ok(await p.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), 'the page scrolls sideways');
  }, { mobile: w < 768 });
}

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

// the header fits in narrow desktop windows too, where a classic scrollbar takes 15 px of the viewport
// (live preview, 23 Sep 2026: at 384 px "Book audit" poked 7 px past the edge; phones use overlay scrollbars)
check('header-fits-beside-a-classic-scrollbar', DESK, async (ctx) => {
  const p = await open(ctx);
  // headless Chrome hides scrollbars, so simulate one: media queries still see the full viewport while the
  // page lays out 15 px narrower, exactly what a classic scrollbar does
  await p.addStyleTag({ content: 'html { width: calc(100% - 15px) !important; }' });
  const bad = [];
  for (let w = 330; w <= 440; w += 2) {
    await p.setViewportSize({ width: w, height: 800 });
    await p.waitForTimeout(40);
    const r = await p.evaluate(() => { const W = Math.round(document.documentElement.getBoundingClientRect().width), c = document.querySelector('.topbar .cta').getBoundingClientRect();
      return { W, sw: Math.max(document.body.scrollWidth, document.querySelector('.topbar').scrollWidth), right: Math.round(c.right) }; });
    if (r.sw > r.W || r.right > r.W) bad.push(w + ' px (content ' + r.W + ', button ends ' + r.right + ')');
  }
  ok(!bad.length, 'the header overflows at ' + bad.length + ' widths, e.g. ' + bad.slice(0, 3).join('; '));
});

// both pages open light (white) even on a computer set to dark mode; dark only when the visitor picks it
// (owner's request, 23 Sep 2026), and the choice carries between the main page and the replay page
async function themeOf(p) {
  return p.evaluate(() => { const bg = getComputedStyle(document.body).backgroundColor.match(/\d+/g).map(Number);
    return { theme: document.documentElement.getAttribute('data-theme'), light: (bg[0] + bg[1] + bg[2]) / 3 > 200 }; });
}
check('pages-open-light-on-a-dark-computer', DESK, async (ctx) => {
  const p = await open(ctx);
  const a = await themeOf(p);
  ok(a.theme === 'light' && a.light, 'the main page opens ' + a.theme + ' (light background: ' + a.light + ') on a dark-mode computer');
  const d = await openDemo(ctx);
  const b = await themeOf(d);
  ok(b.theme === 'light' && b.light, 'the replay page opens ' + b.theme + ' (light background: ' + b.light + ') on a dark-mode computer');
}, { colorScheme: 'dark' });
check('theme-choice-carries-between-pages', DESK, async (ctx) => {
  const p = await open(ctx);
  await p.click('#theme-btn');
  const a = await themeOf(p);
  ok(a.theme === 'dark' && !a.light, 'the main page toggle did not switch to dark');
  const d = await openDemo(ctx);
  const b = await themeOf(d);
  ok(b.theme === 'dark' && !b.light, 'the replay page ignores the choice made on the main page (' + b.theme + ')');
  ok(await d.$('#theme-toggle'), 'the replay page has no theme toggle');
  await d.click('#theme-toggle');
  const c = await themeOf(d);
  ok(c.theme === 'light' && c.light, 'the replay page toggle did not switch back to light');
  await p.reload(); await p.waitForTimeout(150);
  const e = await themeOf(p);
  ok(e.theme === 'light', 'the main page did not pick up the choice made on the replay page (' + e.theme + ')');
}, { colorScheme: 'dark' });

// the replay page's header keeps everything inside it on a phone, theme toggle included
check('replay-header-fits-on-phone', PHONE, async (ctx) => {
  const p = await openDemo(ctx);
  const r = await p.evaluate(() => { const h = document.querySelector('header.hd').getBoundingClientRect(), W = document.documentElement.clientWidth;
    return Array.from(document.querySelectorAll('header.hd *')).filter((e) => e.offsetParent !== null)
      .map((e) => ({ t: (e.textContent || e.className || e.tagName).trim().slice(0, 30), b: e.getBoundingClientRect() }))
      .filter((x) => x.b.width && (x.b.bottom > h.bottom + 0.5 || x.b.right > W + 0.5 || x.b.top < h.top - 0.5))
      .map((x) => x.t + ' (bottom ' + Math.round(x.b.bottom) + ' vs header ' + Math.round(h.bottom) + ')'); });
  ok(!r.length, 'header content spills out: ' + r.slice(0, 2).join('; '));
  ok(await p.$('#theme-toggle'), 'no theme toggle in the replay header');
}, { mobile: true });

/* ------------------------------------------------------------ "Try it on your own file" demo
   The demo needs the page on a web address (a Web Worker cannot start from file://), so these checks
   serve the site folder under a made-up origin through Playwright's request routing: no server, no
   port. Offline checks swap engine/worker.js for a stand-in that answers with a fixed report and
   route the AI proxy to a stand-in answer; the real-engine check lets Pyodide load from
   cdn.jsdelivr.net and is skipped, saying why, when that address cannot be reached. */
const SITE_DIR = path.dirname(path.resolve(pageArg));
const DEMO_ORIGIN = 'http://nl-site.test';
const PROXY_URL = 'https://insight-proxy.nl-check.test/';
class Skip extends Error {}
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.csv': 'text/csv', '.zip': 'application/zip', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.md': 'text/markdown' };

// a complete report in THE REPORT CONTRACT's shape (the numbers are made up for the check, not engine output)
function stubReport() {
  const series = [];
  for (let i = 0; i < 30; i++) { const y = 2023 + Math.floor(i / 12), m = (i % 12) + 1; series.push({ month: y + '-' + String(m).padStart(2, '0'), actual: 100 + i * 3 + (i % 4) }); }
  return {
    ok: true, error: null, engine: { snapshot: '0123456789ab', version: '0.1.0' },
    input: { name: 'orders.csv', bytes: 48213, rows: 1250, columns: 6, sha256: 'ab'.repeat(32) },
    timings: ['read', 'profile', 'decide', 'clean', 'analyze', 'forecast', 'story'].map((s, i) => ({ stage: s, seconds: 0.1 * (i + 1) })),
    privacy: { flagged: [{ column: 'customer_email', kind: 'email', decision: 'withhold' }] },
    health: { score: 81.4, issues: ['12 rows are exact duplicates of another row.'] },
    cleaning: { rows_in: 1250, rows_clean: 1231, rows_quarantined: 19,
      fixes: [{ rule: 'date_date', column: 'order_date', count: 311, what: 'Dates written in different formats were read into one date format' },
        { rule: 'trim_text', column: null, count: 42, what: 'Leading, trailing and doubled spaces were removed' }],
      quarantine_reasons: [{ reason: 'exact_duplicates: exact duplicate row', count: 12 }, { reason: 'amount_numeric: value is not a number', count: 7 }] },
    roles: { date: 'order_date', measures: ['amount'], dimensions: ['region'], excluded: { customer_email: 'personal data decision at intake' } },
    findings: [
      { id: 'measure.amount.change', claim: 'Average amount rose 12.5% on the year before, to 1,234.5.', verdict: 'RECOMMEND', why: 'Cleared the gate at 30 months.', kind: 'business', value: 12.5 },
      { id: 'dq.duplicates', claim: '12 of 1250 rows are exact duplicates of another row', verdict: 'WATCH', why: 'Small, but a defect.', kind: 'data_quality', value: 12 },
      { id: 'dq.top_region', claim: 'The most common region value is \'East\', with 40% of rows in orders.csv.', verdict: 'WATCH', why: 'A profile fact.', kind: 'data_quality', value: 40 },
      { id: 'forecast.monthly_rows.next', claim: 'Monthly rows next month: 190', verdict: 'INSUFFICIENT', why: 'Too little history.', kind: 'forecast', value: 190 }
    ],
    forecast: { available: true, reason: 'Replayed over the last 6 months, the trend beat the naive rule.', verdict: 'RECOMMEND', champion: 'a damped trend (Holt)', baseline_won: false,
      series, forecast: [{ month: '2025-07', value: 190.5, lo: 180.25, hi: 201 }, { month: '2025-08', value: 193, lo: null, hi: null }],
      backtest: { mape: 4.26, mase: 0.61, coverage: 83.3 } },
    story: { headline: 'Average amount +12.5% on the year before; 19 rows set aside.', what_happened: ['Average amount rose from 1,097.3 to 1,234.5.'], why: ['No outside context was attached.'],
      what_to_do: ['Fix the 12 duplicate rows at the source.'], whats_next: ['Monthly rows forecast at 190.5 for 2025-07.'], cannot_answer: ['Nothing on costs.'] },
    downloads: { clean_csv: 'order_date,region,amount\n2025-01-02,East,10.5\n', quarantine_csv: 'order_date,region,amount,reason\n', ledger_json: '{"what":"stub ledger"}' }
  };
}
// The stand-in worker. The scan posts the report's flagged columns; the page's "profile" message (sent after
// the visitor's choices, only with the AI) is answered, when the report asks for it (__profile: true, or a
// profile object), with a profile in the adapter's shape (profile_for_ai: time, analysis_limits,
// looks_personal, privacy_flag) and the report's __landed map ({header: landed name}, as
// nl_browser.plan_profile_json sends it). __profileDecisions pins the choices that profile was made under:
// the worker then answers other choices with no profile, as a check that the page asked after the choices,
// with the visitor's own. A run with an AI plan echoes the data tests the engine would run (or the report's
// own __contracts and its flagged-values download), and a run of a corrected plan fails when the report says
// __revisedFails.
const STUB_WORKER = `'use strict';
var REPORT = __REPORT__;
var DEFAULT_PROFILE = __PROFILE__;
function sorted(o) { var r = {}; Object.keys(o || {}).sort().forEach(function (k) { r[k] = o[k]; }); return JSON.stringify(r); }
self.onmessage = function (e) {
  var m = e.data || {};
  if (m.type === 'scan') {
    self.postMessage({ type: 'stage', id: m.id, stage: 'load', state: 'done', seconds: 0.01 });
    self.postMessage({ type: 'scanned', id: m.id, result: { ok: true, error: null,
      flagged: REPORT.privacy.flagged.map(function (f) { return { column: f.column, kind: f.kind }; }),
      released: (REPORT.privacy.released || []).map(function (r) { return { column: r.column, header: r.header, distinct: r.distinct, text: r.text }; }) } });
  } else if (m.type === 'profile') {
    var P = REPORT.__profile ? (typeof REPORT.__profile === 'object' ? REPORT.__profile : DEFAULT_PROFILE) : null;
    if (P && REPORT.__profileDecisions && sorted(m.decisions) !== sorted(REPORT.__profileDecisions)) P = null;
    self.postMessage({ type: 'profiled', id: m.id, profile: P, landed: P ? (REPORT.__landed || null) : null });
  } else if (m.type === 'results') {
    if (REPORT.__results) self.postMessage({ type: 'results_json', id: m.id, results: REPORT.__resultsJson || { charts: [], tables: [] } });
  } else if (m.type === 'run') {
    var D = (m.options && m.options.decisions) || {}, R = REPORT;
    if (D.__plan__ && D.__plan__.revised && REPORT.__revisedFails) {
      self.postMessage({ type: 'result', id: m.id, report: { ok: false, error: 'The corrected plan stopped the engine (stub).' } });
      return;
    }
    if (D.__plan__) { R = JSON.parse(JSON.stringify(REPORT)); R.ai_plan = { goal: D.__plan__.goal, applied: [], refused: [], review: D.__plan_review__ };
      if (REPORT.__cq !== undefined) R.ai_plan.context_queries = REPORT.__cq;
      R.plan_signals = (REPORT.__signals && !D.__plan__.revised && !D.__plan_review__) ? REPORT.__signals : []; }
    if (D.__plan__ && D.__plan__.columns) {   // echo the data tests the engine would run, for the review-card checks
      var off = D.__contracts_off__ || [];
      if (REPORT.__contracts) R.contracts = JSON.parse(JSON.stringify(REPORT.__contracts));
      else {
        R.contracts = { tests: D.__plan__.columns.filter(function (c) { return c.semantic_type !== 'category'; }).map(function (c) { return { column: c.name, semantic_type: c.semantic_type, test: 'stub test', checked: off.indexOf(c.name) < 0 ? 10 : 0, failed: 0, examples: [], action: off.indexOf(c.name) < 0 ? 'passed' : 'turned off by you', unreadable: 0, out_of_range: 0, repeated: 0, unexpected: 0, misread: false, signal: false }; }), cells_flagged: 1, line: 'source_line', note: 'stub' };
        R.downloads.contract_flagged_csv = 'source_line,column,value,test,what happened\\n9,amount,140,between 0 and 100,out of range; kept by the engine\\n';
      }
    }
    if (REPORT.__echoDecisions) {          // the choices the run received, on the report's headline (the released-category check)
      R = JSON.parse(JSON.stringify(R)); var dd = {};
      Object.keys(D).sort().forEach(function (k) { if (k.indexOf('__') !== 0) dd[k] = D[k]; });
      R.story.headline = 'decisions ' + JSON.stringify(dd);
    }
    self.postMessage({ type: 'result', id: m.id, report: R });
  }
};`;
// the profile the stand-in worker sends for stubReport() (orders.csv, customer_email flagged): the adapter's
// shape, with a time block and analysis_limits that name columns (made up for the checks)
function stubProfile() {
  return { ok: true, name: 'orders.csv', rows: 1250, columns_total: 4,
    columns: [
      { name: 'order_date', filled: 1250, distinct: 540, numeric_share: 0, date_share: 1, looks_personal: false },
      { name: 'region', filled: 1250, distinct: 4, numeric_share: 0, date_share: 0, top_values: ['East', 'West', 'North', 'South'], looks_personal: false },
      { name: 'amount', filled: 1244, distinct: 1100, numeric_share: 1, min: 3.5, median: 88.2, max: 2410.75, integers: false, percent_sign: false, blank: 6 },
      { name: 'customer_email', filled: 1250, distinct: 610, numeric_share: 0, date_share: 0, looks_personal: true, privacy_flag: 'email' }],
    time: { column: 'order_date', first: '2023-01', last: '2025-06', months: 30, distinct_years: 3 },
    analysis_limits: [{ analysis: 'trend', ok: false, why: 'needs 8 or more complete years of values; the file spans 30 months (2023-01 to 2025-06), 2 complete calendar years' },
      { analysis: 'compare', ok: true, why: 'needs a column of groups with 2 or more groups of 5 or more rows; region has 4 such groups' }] };
}

// o: { stubReport, proxy: 'unset'|'set', proxyReply: fn(body) -> {status, json} }
async function demoContext(ctx, o) {
  o = o || {};
  ctx.__reqs = [];
  ctx.on('request', (r) => ctx.__reqs.push({ url: r.url(), method: r.method(), body: r.postData() }));
  await ctx.route(DEMO_ORIGIN + '/**', (route) => {
    const u = new URL(route.request().url());
    const rel = decodeURIComponent(u.pathname).replace(/^\/+/, '');
    if (o.stubReport && rel === 'engine/worker.js') {
      return route.fulfill({ status: 200, contentType: MIME['.js'], body: STUB_WORKER.replace('__REPORT__', () => JSON.stringify(o.stubReport)).replace('__PROFILE__', () => JSON.stringify(stubProfile())) });
    }
    const f = path.resolve(SITE_DIR, rel);
    if (f.indexOf(SITE_DIR + path.sep) !== 0 || !fs.existsSync(f) || !fs.statSync(f).isFile()) return route.fulfill({ status: 404, body: 'not found' });
    let body = fs.readFileSync(f);
    if (rel === 'index.html' && o.proxy) {
      const t = body.toString('utf8'), want = o.proxy === 'set' ? PROXY_URL : '';
      const n = t.replace(/"ai_proxy_url":"[^"]*"/, '"ai_proxy_url":' + JSON.stringify(want));
      if (n === t && t.indexOf('"ai_proxy_url":' + JSON.stringify(want)) < 0) throw new Error('index.html carries no ai_proxy_url to set');
      body = n;
    }
    return route.fulfill({ status: 200, contentType: MIME[path.extname(f)] || 'application/octet-stream', body });
  });
  ctx.__ai = [];
  await ctx.route(PROXY_URL + '**', (route) => {
    const req = route.request();
    const cors = { 'Access-Control-Allow-Origin': DEMO_ORIGIN, 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Allow-Methods': 'POST' };
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    ctx.__ai.push(req.postData());
    const r = (o.proxyReply || (() => ({ status: 503, json: { error: 'not_configured' } })))(JSON.parse(req.postData() || '{}'));
    return route.fulfill({ status: r.status, headers: Object.assign({ 'Content-Type': 'application/json' }, cors), body: JSON.stringify(r.json) });
  });
}
async function openTry(ctx, o) {
  await demoContext(ctx, o);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(DEMO_ORIGIN + '/index.html#try');
  await page.waitForFunction(() => window.NLTry && document.getElementById('try-sample'));
  page.__errs = errs;
  return page;
}
async function tryUntil(p, sel, ms) {
  await p.waitForSelector(sel, { timeout: ms || 60000 });
}
async function runStub(p) {
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden]), #try-msg:not([hidden])');
  ok(await p.isVisible('#try-pd'), 'the personal-data step did not appear: ' + (await p.textContent('#try-msg')));
  ok(await p.isChecked('#try-pd input[data-col="customer_email"][value="withhold"]'), 'Withhold is not ticked by default');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden]), #try-msg:not([hidden])');
  ok(await p.isVisible('#try-report'), 'no report: ' + (await p.textContent('#try-msg')));
}
// the page's own formatting, to compare what it prints with the report it was given
const fmt = (v, dp) => (v === null || v === undefined || !isFinite(v)) ? 'n/a' : Number(v).toLocaleString('en-US', { maximumFractionDigits: dp === undefined ? 2 : dp });
const squash = (s) => String(s === null || s === undefined ? '' : s).replace(/\s+/g, ' ').trim();
async function screenMatchesReport(p, r) {
  const d = await p.evaluate(() => {
    const R = document.getElementById('try-report');
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    const qa = (s, root) => Array.from((root || R).querySelectorAll(s));
    const rows = (s) => qa(s + ' tbody tr').map((tr) => Array.from(tr.children).map(t));
    return { headline: t(R.querySelector('.tr-headline')), meta: qa('.tr-meta').map(t), kpis: qa('.tr-kpi .k-val').map(t), kpiSubs: qa('.tr-kpi .k-sub').map(t),
      fixes: rows('.tr-fixes'), quar: rows('.tr-quar'), priv: rows('.tr-priv'),
      findings: qa('.tr-flist li').map((li) => [t(li.querySelector('.tr-claim')), t(li.querySelector('.badge')), t(li.querySelector('.why'))]),
      bt: qa('.tr-bt dd').map(t), nofc: t(R.querySelector('.tr-nofc')), points: qa('#try-fc-viz circle.mk').map((c) => c.getAttribute('aria-label')),
      lead: t(R.querySelector('#try-story .tr-lead')),
      story: qa('#try-story .tr-sb').map((sb) => [sb.getAttribute('data-key'), qa(':scope > ul > li', sb).map((li) => li.classList.contains('tr-grp')
        ? qa(':scope > p, :scope > ul > li', li).map(t).join(' ') : t(li))]) };
  });
  const bad = [];
  const eq = (a, b, w) => { if (JSON.stringify(a) !== JSON.stringify(b)) bad.push(w + ': shows ' + JSON.stringify(a).slice(0, 120) + ', report ' + JSON.stringify(b).slice(0, 120)); };
  const c = r.cleaning, V = ['RECOMMEND', 'WATCH', 'INSUFFICIENT'];
  eq(d.headline, squash(r.story.headline), 'headline');
  ok(d.meta[0].indexOf(fmt(r.input.rows, 0) + ' rows × ' + fmt(r.input.columns, 0) + ' columns') >= 0 && d.meta[1].indexOf(r.engine.snapshot) >= 0, 'file line: ' + d.meta.join(' / '));
  eq(d.kpis, [r.health.score === null ? 'Not scored' : fmt(r.health.score, 1) + '/100', fmt(c.rows_clean, 0), fmt(r.findings.length, 0), r.forecast.available ? (r.forecast.verdict || 'made') : 'None'], 'key numbers');
  eq(d.kpiSubs[1], V.map((v) => fmt(r.findings.filter((f) => f.verdict === v).length, 0) + ' ' + v).join(', '), 'findings by verdict');
  eq(d.fixes, c.fixes.map((f) => [f.rule, f.column === null ? 'across the file' : f.column, fmt(f.count, 0), squash(f.what)]), 'fixes');
  eq(d.quar, c.rows_quarantined ? c.quarantine_reasons.map((q) => [squash(q.reason), fmt(q.count, 0)]) : [], 'set-aside reasons');
  eq(d.priv, r.privacy.flagged.map((f) => [f.column, squash(f.kind), f.decision]), 'personal-data decisions');
  eq(d.findings, r.findings.map((f) => [squash(f.claim), f.verdict, squash(f.why)]), 'findings');
  const F = r.forecast;
  if (F.available && F.series.length) {
    const b = F.backtest;
    eq(d.bt, [b.mape === null ? 'n/a' : b.mape.toFixed(1) + '%', fmt(b.mase, 2), b.coverage === null ? 'n/a' : fmt(Math.floor(b.coverage + 1e-9), 0) + '%'], 'backtest');
    eq(d.points.length, F.forecast.length, 'forecast points');
    F.forecast.forEach((pt, i) => { const want = 'forecast ' + fmt(pt.value) + (pt.lo !== null && pt.hi !== null ? ', 80% range ' + fmt(pt.lo) + ' to ' + fmt(pt.hi) : ''); if ((d.points[i] || '').indexOf(want) < 0) bad.push('forecast point ' + pt.month + ': ' + d.points[i]); });
  } else if ((d.nofc || '').indexOf(squash(String(F.reason).replace(/[.\s]+$/, ''))) < 0) bad.push('no-forecast reason: ' + d.nofc);
  if (!r.story.headline.startsWith('The business analysis did not run')) eq(d.lead, squash(r.story.headline), 'story headline');
  const want = await p.evaluate((st) => ['what_happened', 'why', 'what_to_do', 'whats_next', 'cannot_answer'].filter((k) => st[k].length)
    .map((k) => [k, window.NLTry.groupLines(st[k]).map((g) => window.NLTry.groupText(g))]), r.story);
  eq(d.story, want.map(([k, v]) => [k, v.map(squash)]), 'story');
  ok(!bad.length, bad.slice(0, 3).join(' | '));
}
async function cdnReachable() {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 6000);
    const r = await fetch('https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js', { method: 'HEAD', signal: ctl.signal });
    clearTimeout(t);
    return r.ok ? null : 'cdn.jsdelivr.net answered ' + r.status;
  } catch (e) { return 'cdn.jsdelivr.net cannot be reached from here (' + String(e.cause && e.cause.code || e.name || e.message) + ')'; }
}

check('try-refuses-oversize-and-non-csv-before-loading-the-engine', DESK, async (ctx) => {
  const p = await openTry(ctx, {});
  const lim = await p.evaluate(() => ({ b: window.NL.try.max_bytes, r: window.NL.try.max_rows, label: window.NL.try.max_label }));
  const cases = [
    ['too big', { name: 'big.csv', mimeType: 'text/csv', buffer: Buffer.alloc(lim.b + 1, 0x61) }, 'This file is too big for the demo', 'up to ' + lim.label],
    ['too many rows', { name: 'rows.csv', mimeType: 'text/csv', buffer: Buffer.from('n\n' + '1\n'.repeat(lim.r + 1)) }, 'This file has too many rows for the demo', fmt(lim.r + 1, 0) + ' data rows'],
    ['a PDF named .csv', { name: 'report.csv', mimeType: 'text/csv', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') }, 'This is a PDF, not a CSV', 'CSV or TSV'],
    ['an Excel workbook', { name: 'book.xlsx', mimeType: 'application/octet-stream', buffer: Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.alloc(64, 1)]) }, 'This looks like an Excel workbook, not a CSV', 'Save as'],
    ['a header with no rows', { name: 'empty.csv', mimeType: 'text/csv', buffer: Buffer.from('a,b,c\n') }, 'This file has a header but no data rows', 'at least one row']
  ];
  for (const [what, file, title, body] of cases) {
    await p.setInputFiles('#try-file', file);
    await tryUntil(p, '#try-msg:not([hidden])', 20000);
    const m = await p.evaluate(() => ({ h: document.querySelector('#try-msg h3').textContent, b: document.querySelector('#try-msg p').textContent }));
    ok(m.h === title && m.b.indexOf(body) >= 0, what + ': says "' + m.h + ': ' + m.b.slice(0, 90) + '"');
  }
  const eng = ctx.__reqs.filter((r) => /worker\.js|northledger-browser\.zip|jsdelivr/.test(r.url));
  ok(!eng.length, 'a refused file still fetched ' + (eng[0] || {}).url);
  const pf = await ctx.newPage();
  await pf.goto(FILE + '#try');
  await pf.click('#try-sample');
  await pf.waitForSelector('#try-msg:not([hidden])', { timeout: 10000 });
  ok(/opened from a web address/.test(await pf.textContent('#try-msg h3')), 'on a page opened from disk the demo does not say it needs a web address');
});

check('try-report-shows-exactly-the-engine-report-and-no-ai-when-unset', DESK, async (ctx) => {
  const rep = stubReport();
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runStub(p);
  await screenMatchesReport(p, rep);
  const a = await p.evaluate(() => ({ box: !!document.querySelector('#try-ai-ok, .tr-ai'), t: document.getElementById('try').innerText }));   // what a reader sees
  ok(!a.box && !/DeepSeek|AI wording|AI summar/.test(a.t), 'with ai_proxy_url empty the demo still offers AI summaries');
  ok(!ctx.__ai.length && !ctx.__reqs.some((r) => r.body), 'a request carried a body with ai_proxy_url empty');
  const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#try-report [data-dl="clean_csv"]')]);
  ok(fs.readFileSync(await dl.path(), 'utf8') === rep.downloads.clean_csv && /-clean\.csv$/.test(dl.suggestedFilename()), 'the cleaned-CSV download is not the engine\'s text');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { acceptDownloads: true });

// The AI choice (29 Sep 2026): nothing reaches the proxy until the visitor presses "Continue with the AI"; the
// payloads that do leave carry only the allowed fields; a proxy that fails leaves the engine's own report.
check('try-ai-consent-payload-guard-and-fallbacks', DESK, async (ctx) => {
  const rep = stubReport(); rep.__profile = true; rep.__results = true;
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [], analyses: [{ type: 'trend', columns: ['amount'] }],
    columns: [{ name: 'amount', semantic_type: 'percentage' }, { name: 'id', semantic_type: 'identifier' }, { name: 'region', semantic_type: 'category' }] };
  let mode = 'good';
  const reply = (b, u) => {
    if (b.profile) return mode === 'down' ? { status: 503, json: { error: 'x' } } : { status: 200, json: { plan } };
    return mode === 'noreport' ? { status: 502, json: { error: 'upstream_error' } } : { status: 200, json: { report: 'Rent rose. [S1]', sources: [{ title: 'A source', link: 'https://example.org/a' }], model: 'check', repaired: 0 } };
  };
  const proxyReqs = (c) => c.__reqs.filter((r) => r.url.indexOf(PROXY_URL) === 0);
  const posts = (c, tail) => proxyReqs(c).filter((r) => r.method === 'POST' && r.url === PROXY_URL + tail);
  let p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: reply });
  ok((await p.evaluate(() => window.NL.try.ai_proxy_url)) === PROXY_URL, 'the page did not pick up ai_proxy_url');
  // 1. with personal columns flagged: the two buttons and the plain paragraph, and nothing sent while they are on screen
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden])');
  const t = await p.textContent('#try-pd');
  ok(/Continue with the AI/.test(t) && /Continue without AI/.test(t) && !/Continue with these choices/.test(t), 'the personal-data step lacks the two-button choice');
  ok(/DeepSeek/.test(t) && /this site's proxy/.test(t) && /Never rows\./.test(t) && /A column you withhold is left out and never named/.test(t) && /public sources on the web/.test(t) && /nothing leaves this browser/.test(t), 'the choice does not say what is sent and to whom: ' + t.slice(0, 400));
  ok(!/never columns flagged as personal/.test(t), 'the choice still says no flagged column is sent (a coded column\'s name and type are)');
  await p.waitForTimeout(300);
  ok(!proxyReqs(ctx).length, 'the proxy was contacted before the visitor chose');
  // 2. without the AI: the engine report, and zero requests to the proxy origin
  await p.click('#try-pd-noai');
  await tryUntil(p, '#try-report:not([hidden])');
  await p.waitForTimeout(500);
  ok(!proxyReqs(ctx).length, 'a request reached the proxy after "Continue without AI": ' + JSON.stringify(proxyReqs(ctx).map((r) => r.url)));
  ok(!(await p.isVisible('#try-ai-report')) && !(await p.isVisible('#try-plan-card')), 'AI output is shown after the visitor chose no AI');
  await p.close();
  // 3. with the AI: /plan only after the click, only the allowed fields; the plan runs at once (no review gate); the visitor may change it afterwards
  const c2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    p = await openTry(c2, { stubReport: rep, proxy: 'set', proxyReply: reply });
    await p.fill('#try-q', 'Which units pay late?');
    await p.click('#try-sample');
    await tryUntil(p, '#try-pd:not([hidden])');
    ok(!proxyReqs(c2).length, 'the proxy was contacted before the click');
    await p.click('#try-pd-go');
    await tryUntil(p, '#try-report:not([hidden])');
    ok(!(await p.isVisible('#try-review-go')), 'a review card blocked the run');
    const plans = posts(c2, 'plan');
    ok(plans.length === 1, plans.length + ' /plan calls after one click');
    const pb = JSON.parse(plans[0].body);
    ok(Object.keys(pb).every((k) => ['objective', 'profile', 'profile_hash'].indexOf(k) >= 0) && pb.objective === 'Which units pay late?', 'the /plan body carries more than {objective, profile, profile_hash}: ' + Object.keys(pb));
    ok(!/customer_email/.test(plans[0].body) && pb.profile.columns.length === 3 && pb.profile.time && pb.profile.time.column === 'order_date' && pb.profile.analysis_limits.length === 2,
      'the /plan profile names the withheld customer_email, or lost the other columns, the time block or the limits: ' + plans[0].body.slice(0, 400));
    await p.click('#try-plan-edit');
    await tryUntil(p, '#try-review-go');
    const rv = await p.textContent('#try-plan-card');
    ok(/amount: between 0 and 100 \(or 0 and 1\) \(percentage\)/.test(rv) && /id: unique/.test(rv) && !/region:/.test(rv), 'the review card lacks the data-test lines (or shows one for a category with no values): ' + rv.slice(0, 300));
    ok((await p.locator('#try-plan-card input[data-grp="ct"]:checked').count()) === 2, 'the data-test boxes are not both ticked by default');
    await p.uncheck('#try-plan-card input[data-grp="ct"][data-i="1"]');
    await p.click('#try-review-go');
    await tryUntil(p, '#try-plan-card table.try-contracts');
    await tryUntil(p, '#try-report:not([hidden])');
    const ct = await p.textContent('#try-plan-card table.try-contracts');
    ok(/turned off by you/.test(ct) && /passed/.test(ct), 'the Data tests card does not show the visitor\'s choice: ' + ct);
    ok(await p.isVisible('#try-plan-card [data-dl="contract_flagged_csv"]') && /Cells the data tests flagged \(1\) CSV/.test(await p.textContent('#try-plan-card')), 'no download button for the cells the data tests flagged');
    await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
    const rb = JSON.parse(posts(c2, 'report')[0].body);
    ok(Object.keys(rb).every((k) => ['objective', 'results', 'context_queries'].indexOf(k) >= 0), 'the /report body carries more than {objective, results, context_queries}: ' + Object.keys(rb));
    ok(JSON.stringify(rb).indexOf(rep.downloads.clean_csv.split('\n')[1]) < 0 && JSON.stringify(rb).indexOf(rep.input.sha256) < 0, 'a row or the file fingerprint went to the proxy');
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
    await p.close();
    // 4. fallbacks: no plan -> the engine's own rules; no report -> the engine's report and a plain note
    mode = 'down';
    const before = posts(c2, 'report').length;
    p = await openTry(c2, { stubReport: rep, proxy: 'set', proxyReply: reply });
    await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
    await tryUntil(p, '#try-report:not([hidden])');
    ok(!(await p.isVisible('#try-review-go')) && !(await p.isVisible('#try-plan-edit')), 'a plan card is shown although the planner gave no plan');
    // H1 (final review, 30 Sep 2026): with no plan the report's searches are still an array, and empty
    await p.waitForTimeout(800);
    posts(c2, 'report').slice(before).forEach((r) => { const cq = JSON.parse(r.body).context_queries; ok(Array.isArray(cq) && !cq.length, 'with the plan failed /report was sent context_queries ' + JSON.stringify(cq)); });
    await p.close();
    mode = 'noreport';
    p = await openTry(c2, { stubReport: rep, proxy: 'set', proxyReply: reply });
    await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
    await p.waitForFunction(() => /could not be written/.test(document.getElementById('try-run-note').textContent), null, { timeout: 20000 });
    ok(await p.isVisible('#try-report') && !(await p.isVisible('#try-ai-report')), 'a failed AI report hid the engine\'s report or showed an empty card');
  } finally { await c2.close(); }
  // 5. nothing flagged: the same choice is still put to the visitor before any AI call
  const c3 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    const clean = stubReport(); clean.privacy.flagged = []; clean.__profile = true;
    mode = 'good';
    p = await openTry(c3, { stubReport: clean, proxy: 'set', proxyReply: reply });
    await p.click('#try-sample');
    await tryUntil(p, '#try-pd:not([hidden])');
    ok(/Continue with the AI/.test(await p.textContent('#try-pd')) && /Continue without AI/.test(await p.textContent('#try-pd')), 'with nothing flagged the choice is not shown');
    await p.waitForTimeout(300);
    ok(!proxyReqs(c3).length, 'the proxy was contacted with nothing flagged, before the choice');
    await p.click('#try-pd-noai');
    await tryUntil(p, '#try-report:not([hidden])');
    await p.waitForTimeout(300);
    ok(!proxyReqs(c3).length, 'the proxy was contacted after "Continue without AI" with nothing flagged');
  } finally { await c3.close(); }
});

// a report whose cleaning gate tripped (the shape engine/nl_browser.py returns; figures made up)
function gatedReport() {
  const r = stubReport();
  const why = ' Why: Cleaning set aside 31% of the rows, above the 20% we accept. What would settle it: Read the set-aside rows and re-run.';
  r.story = { headline: 'The business analysis did not run: cleaning set aside 31.9% of the 254 rows, above the 20% at which the cleaning itself is suspect.',
    what_happened: [], why: [], whats_next: [], what_to_do: ['Act on: 7 of 254 rows are exact duplicates of another row.'],
    cannot_answer: ['The data cannot support this claim: 81 of 254 rows were quarantined, not dropped.' + why,
      'The data cannot support this claim: 49 rows were quarantined because of the date.' + why,
      'The data cannot support this claim: 22 rows were quarantined because of the unit.' + why] };
  r.roles = { date: null, measures: [], dimensions: [], excluded: {} };
  r.findings = r.findings.filter((f) => f.kind === 'data_quality');
  r.forecast = { available: false, reason: 'The business analysis did not run, so there is no monthly series to forecast.', verdict: null, champion: null, baseline_won: null,
    series: [], forecast: [], backtest: { mape: null, mase: null, coverage: null } };
  r.input.rows = 254;
  r.cleaning = { rows_in: 254, rows_clean: 173, rows_quarantined: 81, fixes: r.cleaning.fixes,
    quarantine_reasons: [{ reason: 'date_paid_date: could be day/month or month/day, and this column holds both orders, so these dates were not read by guessing', count: 49 },
      { reason: 'unit_numeric: value is not a number', count: 22 }, { reason: 'exact_duplicates: exact duplicate row', count: 10 }] };
  r.downloads.quarantine_csv = 'source_line,building,unit,_quarantine_reason\n4,Pape Ave,B1,x\n';
  return r;
}

check('try-a-tripped-gate-leads-with-what-to-do-and-says-it-once', DESK, async (ctx) => {
  const rep = gatedReport();
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runStub(p);
  await screenMatchesReport(p, rep);
  const d = await p.evaluate(() => {
    const R = document.getElementById('try-report'), g = document.getElementById('try-gate');
    const kids = Array.from(R.children);
    return { gate: g ? g.textContent : '', gateBeforeKpis: !!g && kids.indexOf(g) < kids.indexOf(R.querySelector('.tr-kpis')),
      roles: !!R.querySelector('.tr-roles'), text: R.textContent, groups: R.querySelectorAll('#try-story .tr-grp').length,
      whys: (R.querySelector('#try-story').textContent.match(/Why:/g) || []).length, fcSub: R.querySelector('.tr-kpis .tr-kpi:last-child .k-sub').textContent };
  });
  ok(d.gate && d.gateBeforeKpis, 'no plain card leads the report when the gate trips');
  ok(/share one cause/.test(d.gate) && /could be day\/month or month\/day/.test(d.gate) && /YYYY-MM-DD/.test(d.gate) && /line in your file/.test(d.gate), 'the gate card does not name the cause and the steps: ' + d.gate.slice(0, 200));
  ok(!/correct the rule/.test(d.text), 'the page tells a visitor to correct a rule');
  ok(!d.roles, 'the empty column-roles card is shown when the analysis did not run');
  ok(d.text.split(rep.story.headline).length - 1 === 1, 'the gate headline appears ' + (d.text.split(rep.story.headline).length - 1) + ' times');
  ok(d.groups === 1 && d.whys === 1, 'lines sharing one "Why" are not grouped: ' + d.groups + ' groups, ' + d.whys + ' Why');
  ok(!/Nothing here for this file/.test(d.text), 'empty story sections are still shown');
  ok(d.fcSub.length < 40, 'the forecast tile holds a long sentence: ' + d.fcSub);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-question-box-only-with-ai-and-locked-during-a-run', DESK, async (ctx) => {
  const rep = stubReport();
  let p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  ok(await p.isHidden('#try-q-wrap'), 'the question box shows although no AI is offered');
  await runStub(p);
  ok(!(await p.$('#try-report .tr-q')), 'the report echoes a question that nothing used');
  await p.close();
  const ctx2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    p = await openTry(ctx2, { stubReport: rep, proxy: 'set' });
    ok(await p.isVisible('#try-q-wrap'), 'with the AI offered, the question box is hidden');
    const qText = await p.textContent('#try-q-wrap');
    ok(/Continue with the AI/.test(qText) && /plans the analyses around your question/.test(qText), 'the question box does not say what it is used for: ' + qText.slice(0, 200));
    await p.fill('#try-q', 'Which units pay late?');
    await p.click('#try-sample');
    await tryUntil(p, '#try-pd:not([hidden])');
    ok(await p.isDisabled('#try-q'), 'the question box can be edited while the engine runs');
    await p.click('#try-pd-noai');
    await tryUntil(p, '#try-report:not([hidden])');
    ok(!(await p.isDisabled('#try-q')), 'the question box stays locked after the report');
  } finally { await ctx2.close(); }
});

check('try-the-visitor-may-change-the-ai-plan-after-the-engine-ran-it', DESK, async (ctx) => {
  const rep = stubReport(); rep.__profile = true;
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: ['few months'],
    operations: [{ op: 'set_aside', columns: ['notes'] }, { op: 'date_from_year', column: 'order_date' }], analyses: [{ type: 'trend', columns: ['amount'] }] };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status: 200, json: { plan } } : { status: 503, json: { error: 'x' } } });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden])');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  ok(!(await p.isVisible('#try-review-go')), 'the review card blocked the engine');
  ok(/Change the plan and run again/.test(await p.textContent('#try-plan-card')), 'no button to change the plan after the run');
  ok(!/You approved this plan/.test(await p.textContent('#try-plan-card')), 'the card claims an approval nobody gave');
  await p.click('#try-plan-edit');
  await tryUntil(p, '#try-review-go');
  ok(/Set aside: notes/.test(await p.textContent('#try-plan-card')) && (await p.inputValue('#try-review-goal')) === plan.goal, 'the review card does not show the plan that ran');
  await p.uncheck('#try-plan-card input[data-grp="op"][data-i="0"]');
  await p.click('#try-review-go');
  await p.waitForFunction(() => /You approved this plan/.test(document.getElementById('try-plan-card').textContent), null, { timeout: 20000 });
  const t = await p.textContent('#try-plan-card');
  ok(/you turned off 1 step/.test(t), 'the plan card does not report the visitor edit: ' + t.slice(0, 300));
  ok(!ctx.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').slice(1).length, 'editing the plan asked the AI for another plan');
});

// Autonomous engine (29 Sep 2026): no signals -> one /plan and no review card; signals -> exactly one corrective /plan
// carrying the feedback and no rows; never a third.
check('try-the-ai-corrects-its-plan-once-when-the-engine-finds-a-problem', DESK, async (ctx) => {
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [], analyses: [],
    columns: [{ name: 'amount', semantic_type: 'percentage', role: 'measure' }] };
  const revised = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [], analyses: [], revised: true,
    changes: 'amount is now a plain number, not a percentage', columns: [{ name: 'amount', semantic_type: 'number', role: 'measure' }] };
  const reply = (b) => b.profile ? { status: 200, json: { plan: b.feedback ? revised : plan } }
    : { status: 200, json: { report: 'Fine. [S1]', sources: [], model: 'check', repaired: 0 } };
  const plansOf = (c) => c.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').map((r) => JSON.parse(r.body));
  // (1) no signals
  const clean = stubReport(); clean.__profile = true; clean.__results = true;
  let p = await openTry(ctx, { stubReport: clean, proxy: 'set', proxyReply: reply });
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  await p.waitForTimeout(400);
  ok(plansOf(ctx).length === 1 && !plansOf(ctx)[0].feedback, plansOf(ctx).length + ' /plan requests with no signals');
  ok(!(await p.isVisible('#try-review-go')), 'a review card appeared');
  ok(/nothing to correct/.test(await p.textContent('#try-stages')) && !/The AI corrected its plan/.test(await p.textContent('#try-plan-card')), 'the stage list or card says a correction happened');
  await p.close();
  // (2) signals on the first run
  const c2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    const bad = stubReport(); bad.__profile = true; bad.__results = true;
    bad.__signals = [{ kind: 'contract_failed', column: 'x', detail: 'between 0 and 100; 40 of 100 values fail' }];
    p = await openTry(c2, { stubReport: bad, proxy: 'set', proxyReply: reply });
    await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
    await tryUntil(p, '#try-plan-card:not([hidden])');
    await p.waitForFunction(() => /The AI corrected its plan/.test(document.getElementById('try-plan-card').textContent), null, { timeout: 20000 });
    await p.waitForTimeout(500);
    const pl = plansOf(c2);
    ok(pl.length === 2, pl.length + ' /plan requests (want exactly 2)');
    const fb = pl[1].feedback;
    ok(fb && fb.attempt === 1 && fb.signals[0].kind === 'contract_failed' && fb.previous_plan.goal === plan.goal, 'the second /plan body lacks the feedback: ' + JSON.stringify(pl[1]).slice(0, 300));
    const hasRows = JSON.stringify(pl[1]).indexOf(bad.downloads.clean_csv.split('\n')[1]) >= 0 || Object.keys(pl[1]).some((k) => /rows|csv|data/i.test(k));
    ok(!hasRows, 'the second /plan body holds rows');
    const t = await p.textContent('#try-plan-card');
    ok(/x: between 0 and 100/.test(t), 'the correction block lacks the signal: ' + t.slice(0, 400));
    ok(/What changed \(computed from the two plans\):\s*amount: type percentage, now number/.test(t), 'the correction block does not compute what changed: ' + t.slice(0, 600));
    ok(/The AI's explanation \(its words\):\s*amount is now a plain number, not a percentage/.test(t), 'the AI\'s own words are not shown, labelled: ' + t.slice(0, 600));
    await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
    ok(plansOf(c2).length === 2, 'a third /plan request was made');
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
    await p.close();
  } finally { await c2.close(); }
  // (3) without the AI: zero proxy requests
  const c3 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    const bad = stubReport(); bad.__profile = true; bad.__signals = [{ kind: 'contract_failed', column: 'x', detail: 'd' }];
    p = await openTry(c3, { stubReport: bad, proxy: 'set', proxyReply: reply });
    await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-noai');
    await tryUntil(p, '#try-report:not([hidden])'); await p.waitForTimeout(400);
    ok(!c3.__reqs.some((r) => r.url.indexOf(PROXY_URL) === 0), 'a proxy request was made after "Continue without AI"');
  } finally { await c3.close(); }
});

// The re-plan that did not come back (29 Sep 2026 live test: a 504 after 140 s, and the visitor was told
// nothing once the report showed): the plan card says so where the correction would go, with the problems
// the engine found, in words that follow the cause (review, 29 Sep 2026: "the AI did not answer" was shown
// for every failure). One case for each answer the worker can give (insight-proxy/src/worker.js) and for a
// corrected plan the engine could not run; each ends "so these results use the first plan", which stand.
const REPLAN_HEAD = 'The engine found problems with the AI\'s first plan and asked it to correct itself';
const REPLAN_FAILS = [
  // [what the worker answered, status, error, the notice after REPLAN_HEAD, the progress list's note]
  ['a 504 upstream_timeout', 504, 'upstream_timeout', ', but the AI did not answer in time, so these results use the first plan.', 'the AI did not answer in time, so the first result is kept'],
  ['a 429 busy', 429, 'busy', ', but the AI service was busy, so the correction was not attempted, and so these results use the first plan.', 'the AI service was busy, so the first result is kept'],
  ['a 503 daily_cap_reached', 503, 'daily_cap_reached', ', but the AI service has reached its daily limit, so the correction was not attempted, and so these results use the first plan.', 'the AI service reached its daily limit, so the first result is kept'],
  ['a 503 daily_counter_failed', 503, 'daily_counter_failed', ', but the AI service has reached its daily limit, so the correction was not attempted, and so these results use the first plan.', 'the AI service reached its daily limit, so the first result is kept'],
  ['a 502 rejected_plan', 502, 'rejected_plan', ', and the AI answered, but its corrected plan could not be used, so these results use the first plan.', 'the corrected plan could not be used, so the first result is kept'],
  ['a 502 upstream_error', 502, 'upstream_error', ', but the request to the AI failed, so these results use the first plan.', 'the request to the AI failed, so the first result is kept'],
  ['a 503 not_configured', 503, 'not_configured', ', but the request to the AI failed, so these results use the first plan.', 'the request to the AI failed, so the first result is kept'],
  ['a corrected plan the engine could not run', 200, null, ', but the engine could not run the corrected plan, so these results use the first plan.', 'the corrected run failed, so the first result is kept']
];
check('try-a-replan-that-fails-says-why-on-the-plan-card', DESK, async (ctx) => {
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [], analyses: [],
    columns: [{ name: 'amount', semantic_type: 'date', role: 'measure' }] };
  const sigs = [{ kind: 'contract_failed', column: 'amount', detail: 'only 312 of 1,400 values read as dates (22%)' },
    { kind: 'analysis_refused', detail: 'trend: no series with 8 or more years of values' }];
  const plansOf = (c) => c.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').map((r) => JSON.parse(r.body));
  for (const [label, status, err, ending, stageNote] of REPLAN_FAILS) {
    const c = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
    try {
      const bad = stubReport(); bad.__profile = true; bad.__results = true; bad.__signals = sigs;
      bad.__revisedFails = err === null;
      bad.__contracts = { tests: [{ column: 'amount', semantic_type: 'date', test: 'a date that can be read', checked: 1400, failed: 1088, examples: ['pending'],
        action: 'probably not a date: only 312 of 1,400 values read as dates (22%); the engine kept these rows', unreadable: 1088, out_of_range: 0, repeated: 0, unexpected: 0,
        misread: true, signal: true, problem: 'only 312 of 1,400 values read as dates (22%)' }], cells_flagged: 1088, line: 'source_line', note: 'stub note' };
      const revised = Object.assign({}, plan, { revised: true, columns: [{ name: 'amount', semantic_type: 'level', role: 'measure' }] });
      const reply = (b) => b.profile ? (b.feedback ? (err === null ? { status: 200, json: { plan: revised } } : { status, json: { error: err } }) : { status: 200, json: { plan } })
        : { status: 200, json: { report: 'Fine. [S1]', sources: [], model: 'check', repaired: 0 } };
      const p = await openTry(c, { stubReport: bad, proxy: 'set', proxyReply: reply });
      await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
      await tryUntil(p, '#try-report:not([hidden])');
      await p.waitForFunction(() => /asked it to correct itself/.test(document.getElementById('try-plan-card').textContent), null, { timeout: 20000 });
      const t = (await p.textContent('#try-plan-card')).replace(/\s+/g, ' ');
      const want = REPLAN_HEAD + ending;
      ok(t.indexOf(want) >= 0, label + ': the plan card lacks the notice "' + want + '": ' + t.slice(0, 500));
      ok((t.match(/so these results use the first plan/g) || []).length === 1, label + ': the notice is not said once: ' + t.slice(0, 500));
      ok(/amount: only 312 of 1,400 values read as dates \(22%\)/.test(t) && /trend: no series with 8 or more years of values/.test(t), label + ': the notice does not list what the engine found: ' + t.slice(0, 500));
      ok(!/The AI corrected its plan/.test(t) && !/kept its plan unchanged/.test(t), label + ': the card claims a correction that never came: ' + t.slice(0, 400));
      ok(/probably not a date: only 312 of 1,400 values read as dates \(22%\); the engine kept these rows; the AI was asked to look again/.test(t), label + ': the Data tests row does not say the AI was asked to look again: ' + t.slice(0, 700));
      ok(await p.isVisible('#try-plan-card .try-plan-replan-failed'), label + ': the notice is not visible once the report shows');
      const st = (await p.textContent('#try-stages')).replace(/\s+/g, ' ');
      ok(/corrects its plan \(up to about a minute and a half\)/.test(st), 'the re-plan stage has no time hint: ' + st.slice(0, 300));
      ok(st.indexOf(stageNote) >= 0, label + ': the re-plan stage note is not "' + stageNote + '": ' + st.slice(0, 400));
      const pl = plansOf(c);
      ok(pl.length === 2 && pl[1].feedback && pl[1].feedback.signals.length === 2, label + ': ' + pl.length + ' /plan requests, or the second lacks the feedback');
      await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
      if (err === null) {
        await p.click('#try-plan-edit');
        await tryUntil(p, '#try-review-go');
        ok(/amount: a date that can be read \(date\)/.test(await p.textContent('#try-plan-card')), label + ': "Change the plan" does not start from the first plan, whose results are shown');
      }
      ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
      await p.close();
    } finally { await c.close(); }
  }
});

// The first plan that did not come: the plan card says why in the same words (a timeout, busy, the day's
// limit, a plan that failed its checks, anything else), and the engine read the file with its own rules.
check('try-a-first-plan-that-fails-says-why', DESK, async (ctx) => {
  const cases = [[504, 'upstream_timeout', 'The AI planner did not answer in time'], [429, 'busy', 'The AI service was busy, so no plan was made'],
    [503, 'daily_cap_reached', 'The AI service has reached its daily limit, so no plan was made'], [503, 'daily_counter_failed', 'The AI service has reached its daily limit, so no plan was made'],
    [502, 'rejected_plan', 'The AI answered, but its plan could not be used'], [502, 'upstream_error', 'The request to the AI planner failed'],
    [503, 'not_configured', 'The request to the AI planner failed']];
  for (const [status, err, head] of cases) {
    const rep = stubReport(); rep.__profile = true;
    const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status, json: { error: err } } : { status: 503, json: { error: 'x' } } });
    await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
    await tryUntil(p, '#try-report:not([hidden])');
    const t = (await p.textContent('#try-plan-card')).replace(/\s+/g, ' ');
    const want = head + ', so the engine read the file with its own rules.';
    ok(await p.isVisible('#try-plan-card') && t.indexOf(want) >= 0, status + ' ' + err + ': the plan card does not say "' + want + '": ' + t.slice(0, 300));
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
    await p.close();
  }
});

// "What changed" is computed from the two plans, never quoted (live probe, 29 Sep 2026: the AI said it
// "added compare of revenue by channel", which the first plan already had). The diff must list what did
// change (a column reading, an analysis dropped, a step added) and never the change the AI only claimed;
// the AI's words appear below it, labelled as its words, each cut at a word boundary with an ellipsis.
check('try-what-changed-is-computed-not-quoted', DESK, async (ctx) => {
  const cmp = { type: 'compare', columns: ['revenue'], by: 'channel' };
  const plan = { goal: 'How is revenue moving?', understanding: 'Orders.', quality_risks: [], primary: 'revenue',
    operations: [{ op: 'keep_columns', columns: ['order_date', 'channel', 'revenue'] }],
    analyses: [cmp, { type: 'trend', columns: ['revenue'] }],
    columns: [{ name: 'order_date', semantic_type: 'date', role: 'date' }, { name: 'revenue', semantic_type: 'flow_amount', role: 'target', unit: 'currency' }] };
  const long = 'Kept the order date as the date axis because the engine said the trend needs eight years and this file spans twenty three months only, so the trend was dropped for a distribution';
  const revised = { goal: 'How is revenue moving?', understanding: 'Orders.', quality_risks: [], primary: 'revenue', revised: true,
    operations: [{ op: 'keep_columns', columns: ['order_date', 'channel', 'revenue'] }, { op: 'exclude_blank', column: 'order_date' }],
    analyses: [cmp, { type: 'distribution', columns: ['revenue'] }],
    columns: [{ name: 'order_date', semantic_type: 'date', role: 'date' }, { name: 'revenue', semantic_type: 'flow_amount', role: 'target', unit: 'USD' }],
    changes: ['added compare of revenue by channel', long] };
  const reply = (b) => b.profile ? { status: 200, json: { plan: b.feedback ? revised : plan } }
    : { status: 200, json: { report: 'Fine. [S1]', sources: [], model: 'check', repaired: 0 } };
  const bad = stubReport(); bad.__profile = true; bad.__results = true;
  bad.__signals = [{ kind: 'analysis_refused', detail: 'trend: no series with 8 or more years of values' }];
  const p = await openTry(ctx, { stubReport: bad, proxy: 'set', proxyReply: reply });
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
  await tryUntil(p, '#try-plan-card:not([hidden])');
  await p.waitForFunction(() => /The AI corrected its plan/.test(document.getElementById('try-plan-card').textContent), null, { timeout: 20000 });
  const items = await p.evaluate(() => {
    const box = document.querySelector('#try-plan-card .try-plan-replan');
    const lists = box ? box.querySelectorAll('ul') : [];
    const txt = (ul) => ul ? Array.prototype.map.call(ul.querySelectorAll('li'), (li) => li.textContent.replace(/\s+/g, ' ').trim()) : [];
    return { signals: txt(lists[0]), diff: txt(lists[1]), words: txt(lists[2]), all: box ? box.textContent.replace(/\s+/g, ' ') : '' };
  });
  ok(/What changed \(computed from the two plans\)/.test(items.all), 'no computed "What changed" block: ' + items.all.slice(0, 400));
  const want = ['added the analysis distribution of revenue', 'removed the analysis trend of revenue', 'revenue: unit currency, now USD', 'added the step exclude_blank order_date'];
  want.forEach((w) => ok(items.diff.indexOf(w) >= 0, 'the diff lacks "' + w + '": ' + JSON.stringify(items.diff)));
  ok(items.diff.length === want.length, 'the diff lists more than changed: ' + JSON.stringify(items.diff));
  ok(!items.diff.some((x) => /compare/.test(x)), 'the diff lists the compare the AI only claimed to add: ' + JSON.stringify(items.diff));
  ok(/The AI's explanation \(its words\)/.test(items.all) && items.words[0] === 'added compare of revenue by channel', 'the AI\'s words are not shown under their own label: ' + items.all.slice(0, 600));
  ok(items.words[1].length <= 160 && /\u2026$/.test(items.words[1]) && long.indexOf(items.words[1].slice(0, -1)) === 0 && /[ ,]/.test(long.charAt(items.words[1].length - 1)),
    'a long explanation is not cut at a word boundary with an ellipsis: ' + JSON.stringify(items.words[1]));
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// "What changed" in detail (review, 29 Sep 2026): every field the engine runs or the report states is
// compared (goal, kind, what the AI read, the headline measure, the web searches, each column reading,
// each step with every value, the analyses), and the same items in another order are a reorder, never a
// removal and an addition. The cases run the page's own T.planDiff; the AI's words are cut by T.cutWords at
// a word boundary and never through a surrogate pair.
const DIFF_BASE = { goal: 'How is revenue moving?', kind: 'transactions', understanding: 'Orders by channel.', primary: 'revenue',
  columns: [{ name: 'order_date', semantic_type: 'date', role: 'date' }, { name: 'revenue', semantic_type: 'flow_amount', role: 'target', unit: 'USD' }, { name: 'channel', semantic_type: 'category', role: 'dimension' }],
  operations: [{ op: 'keep_columns', columns: ['order_date', 'channel', 'revenue'] }, { op: 'exclude_rows', column: 'country', values: ['Canada', 'Mexico', 'Brazil', 'Chile', 'Peru', 'Spain', 'World', 'Asia'] }, { op: 'exclude_blank', column: 'order_date' }],
  analyses: [{ type: 'compare', columns: ['revenue', 'cost'], by: 'channel' }, { type: 'trend', columns: ['revenue'] }],
  context: [{ indicator: 'retail sales', years: [2025, 2025] }, { sector: 'payments', indicator: 'market size', years: [2025] }] };
// [what the case changes, the change (a function of a copy of DIFF_BASE), what the diff must list, exactly]
const DIFF_CASES = [
  ['nothing', () => {}, []],
  ['the goal, the kind, what the AI read and the headline measure', (b) => { b.goal = 'Which channel grows?'; b.kind = 'ledger'; b.understanding = ''; b.primary = 'cost'; },
    ['the goal: "How is revenue moving?", now "Which channel grows?"', 'the kind of data: "transactions", now "ledger"', 'what the AI read: "Orders by channel.", now none', 'headline measure revenue, now cost']],
  ['a step\'s seventh value (the old diff read only the first six)', (b) => { b.operations[1].values[6] = 'Oceania'; },
    ['the step exclude_rows country: added the value Oceania; removed the value World']],
  ['a step\'s values in another order', (b) => { b.operations[1].values.reverse(); }, ['the step exclude_rows country: the same values in another order']],
  ['the steps in another order', (b) => { b.operations = [b.operations[2], b.operations[0], b.operations[1]]; }, ['the same steps in another order']],
  ['a new step with ten values, every one listed', (b) => { b.operations.push({ op: 'keep_rows', column: 'region', values: ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9', 'v10'] }); },
    ['added the step keep_rows region (v1, v2, v3, v4, v5, v6, v7, v8, v9, v10)']],
  ['a removed step', (b) => { b.operations.splice(2, 1); }, ['removed the step exclude_blank order_date']],
  ['the columns a step keeps', (b) => { b.operations[0].columns = ['order_date', 'revenue', 'cost']; }, ['the step keep_columns: added the column cost; removed the column channel']],
  ['an analysis\'s columns in another order', (b) => { b.analyses[0].columns = ['cost', 'revenue']; }, ['the analysis compare of cost, revenue by channel: the same columns in another order']],
  ['the analyses in another order', (b) => { b.analyses.reverse(); }, ['the same analyses in another order']],
  ['an analysis swapped for another', (b) => { b.analyses[1] = { type: 'distribution', columns: ['revenue'] }; }, ['added the analysis distribution of revenue', 'removed the analysis trend of revenue']],
  ['an analysis\'s grouping', (b) => { b.analyses[0].by = 'region'; }, ['added the analysis compare of revenue, cost by region', 'removed the analysis compare of revenue, cost by channel']],
  ['column readings: a role, a new column, a dropped one', (b) => { b.columns[2].role = 'group'; b.columns.push({ name: 'cost', semantic_type: 'flow_amount', role: 'measure', unit: 'USD' }); b.columns.splice(0, 1); },
    ['channel: role dimension, now group', 'now reads cost as flow_amount (measure), unit USD', 'no longer reads order_date (was date)']],
  ['a column reading\'s type and unit', (b) => { b.columns[1].semantic_type = 'level'; b.columns[1].unit = 'CAD'; }, ['revenue: type flow_amount, now level; unit USD, now CAD']],
  ['the column readings listed in another order', (b) => { b.columns.reverse(); }, []],
  ['the web searches in another order', (b) => { b.context.reverse(); }, ['the same web searches in another order']],
  ['a web search swapped', (b) => { b.context = [b.context[1], { indicator: 'interest rates', region: 'Canada', years: [2024, 2025] }]; },
    ['added the web search "interest rates Canada 2024 2025"', 'removed the web search "retail sales 2025"']],
  ['the old free-text searches only (never run, so no change)', (b) => { b.context_queries = ['Acme Holdings market share']; }, []],
  ['the AI\'s own words only (changes, quality risks, why)', (b) => { b.changes = ['kept everything']; b.quality_risks = ['few months']; b.columns[0].why = 'dates'; }, []]
];
check('try-plan-diff-lists-every-change-and-reorders-as-reorders', DESK, async (ctx) => {
  const p = await openTry(ctx, {});
  const bad = [];
  for (const [what, change, want] of DIFF_CASES) {
    const b = JSON.parse(JSON.stringify(DIFF_BASE)); change(b);
    const got = await p.evaluate((ab) => window.NLTry.planDiff(ab[0], ab[1]), [DIFF_BASE, b]);
    if (JSON.stringify(got.slice().sort()) !== JSON.stringify(want.slice().sort())) bad.push(what + ': got ' + JSON.stringify(got) + ', want ' + JSON.stringify(want));
  }
  ok(!bad.length, bad.length + ' of ' + DIFF_CASES.length + ' diff cases wrong: ' + bad.slice(0, 2).join(' | '));
  const cut = await p.evaluate(() => {
    const chart = '\u{1F4C8}';
    const words = 'Kept the order date as the date axis because the engine said the trend needs eight years and this file spans ' + chart.repeat(50) + ' twenty three months';
    const one = chart.repeat(200);
    const lone = (s) => /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(s);
    const a = window.NLTry.cutWords(words, 160), b = window.NLTry.cutWords(one, 160), c = window.NLTry.cutWords('short enough', 160);
    return { a, b, c, lone: lone(a) || lone(b), aLen: Array.from(a).length, bLen: Array.from(b).length, words };
  });
  ok(!cut.lone, 'a cut split a surrogate pair: ' + JSON.stringify(cut.b.slice(-4)));
  ok(cut.a === 'Kept the order date as the date axis because the engine said the trend needs eight years and this file spans…' && cut.aLen <= 160,
    'a long explanation is not cut at the last word boundary before the limit: ' + JSON.stringify(cut.a));
  ok(cut.bLen === 160 && /…$/.test(cut.b) && cut.c === 'short enough', 'a text with no space is not cut at a whole character, or a short one was changed: ' + cut.bLen);
});

// The AI was asked and kept its plan: the card says exactly that and lists what the engine found, never
// "corrected" and never an empty "What changed" (review, 29 Sep 2026)
check('try-a-replan-that-keeps-the-plan-says-so', DESK, async (ctx) => {
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [{ op: 'exclude_blank', column: 'amount' }],
    analyses: [{ type: 'compare', columns: ['amount'], by: 'region' }], columns: [{ name: 'amount', semantic_type: 'level', role: 'measure' }] };
  const same = Object.assign(JSON.parse(JSON.stringify(plan)), { revised: true, changes: ['The engine\'s test fails on refunds, which are real; the reading stands.'] });
  const bad = stubReport(); bad.__profile = true; bad.__results = true;
  bad.__signals = [{ kind: 'contract_failed', column: 'amount', detail: '40 of 1,250 values are below 0, so it may not be a level' }, { kind: 'analysis_refused', detail: 'trend: no series with 8 or more years of values' }];
  const reply = (b) => b.profile ? { status: 200, json: { plan: b.feedback ? same : plan } } : { status: 200, json: { report: 'Fine. [S1]', sources: [], model: 'check', repaired: 0 } };
  const p = await openTry(ctx, { stubReport: bad, proxy: 'set', proxyReply: reply });
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  await p.waitForFunction(() => /kept its plan unchanged/.test(document.getElementById('try-plan-card').textContent), null, { timeout: 20000 });
  const t = (await p.textContent('#try-plan-card')).replace(/\s+/g, ' ');
  ok(t.indexOf('The engine found problems with the AI\'s first plan and asked it to correct itself; the AI kept its plan unchanged.') >= 0, 'the card does not say the AI kept its plan: ' + t.slice(0, 400));
  ok(/What the engine found:\s*amount: 40 of 1,250 values are below 0, so it may not be a level\s*trend: no series with 8 or more years of values/.test(t), 'the card does not list the problems: ' + t.slice(0, 500));
  ok(/The AI's explanation \(its words\):\s*The engine's test fails on refunds, which are real; the reading stands\./.test(t), 'the AI\'s words are not shown under their label: ' + t.slice(0, 600));
  ok(!/Nothing in the plan changed/.test(t) && !/The AI corrected its plan/.test(t) && !/What changed/.test(t), 'the card still claims a correction or prints an empty "What changed": ' + t.slice(0, 500));
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// The privacy promise, as the visitor reads it before anything is sent (29 Sep 2026): the key sentences,
// stated here independently of the page, must be on the step with or without flagged columns, and the
// question box and the choices must say the same.
// Final review (29 Sep 2026): the summary sentence says exactly what the adapter's profile holds (a number's
// lowest, middle and highest value, the date column's first and last month, every value of a text or date
// column with at most 300 short ones: engine/nl_browser.py profile_for_ai), and the scan's limit is stated
// with an example it really misses (tools/fixtures/review3/detector_probe.py stylist_names).
const PRIVACY_PROMISE = [
  'Nothing goes to an AI unless you choose "Continue with the AI".',
  'each column\'s name, type and counts; for a column not flagged as personal, or one you keep, also its range (lowest, middle and highest number; first and last month of the date column) and, for text or dates with at most 300 different short values, those values.',
  'Never rows.',
  'A column you withhold is left out and never named; for a coded column the AI is told only its name, type and counts, never its values or range.',
  'A flagged column you keep is sent like any other column, values included, but only after you tick the box that names it.',
  'The page flags columns that look personal and can miss some (for example people\'s names under a heading like "Stylist"); if your file has such a column, continue without the AI.',
  // the web searches (final review, 30 Sep 2026): built by the adapter from fixed terms only (engine/context_terms.json)
  'The web searches use only general terms such as an indicator, a sector, a country and years, never anything from your file.',
  'Without the AI, nothing leaves this browser.'
];
check('try-consent-text-states-the-privacy-promise', DESK, async (ctx) => {
  for (const flagged of [true, false]) {
    const rep = stubReport(); rep.__profile = true;
    if (!flagged) rep.privacy.flagged = [];
    const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: () => ({ status: 503, json: { error: 'x' } }) });
    const q = (await p.textContent('#try-q-wrap')).replace(/\s+/g, ' ');
    ok(/reads a summary of your columns \(never rows, and never a column you withhold\)/.test(q), 'the question box does not state what the AI reads: ' + q.slice(0, 300));
    await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])');
    const note = (await p.textContent('#try-pd .pd-ai-note')).replace(/\s+/g, ' ');
    const missing = PRIVACY_PROMISE.filter((x) => note.indexOf(x) < 0);
    ok(!missing.length, (flagged ? 'with' : 'without') + ' flagged columns the consent lacks: ' + JSON.stringify(missing) + ' in: ' + note);
    ok(!/never columns flagged as personal|numbers only|commonest values and range/.test(note), 'the consent still makes a promise the page no longer keeps: ' + note);
    if (flagged) {
      const lab = (v) => p.textContent('#try-pd input[data-col="customer_email"][value="' + v + '"] + span');
      ok(/never sent to an AI or put in a share link, not even its name/.test(await lab('withhold')), 'Withhold does not say it is never sent or named');
      ok(/an AI is told only its name, type and counts, never its values or range/.test(await lab('code')), 'Code does not say what an AI is told');
      ok(/used like any other column/.test(await lab('keep')) && /go to the AI only if you tick the box that names it/.test(await lab('keep')),
        'Keep does not say it is used like any other column, and goes to the AI only after the box is ticked');
    }
    await p.waitForTimeout(200);
    ok(!ctx.__reqs.some((r) => r.url.indexOf(PROXY_URL) === 0), 'the proxy was contacted while the consent was on screen');
    await p.close();
  }
});

// The promise kept in what /plan receives (29 Sep 2026): a withheld column is left out and never named (not
// a column, not profile.time, not an analysis_limits text, not a re-plan signal), even when the file's
// header differs from the engine's landed name or a flagged column matches no flagged name; a coded column
// goes with only its name, type and counts, marked looks_personal; a kept column goes like any other.
const PERSONAL_KEEP_UI = ['name', 'filled', 'distinct', 'blank', 'numeric_share', 'date_share', 'integers', 'percent_sign', 'looks_personal', 'privacy_flag'];
const namesIn = (text, names) => names.filter((n) => new RegExp('(^|[^A-Za-z0-9_])' + n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![A-Za-z0-9])', 'i').test(text));
check('try-plan-profile-keeps-the-privacy-promise', DESK, async (ctx) => {
  const rep = stubReport(); rep.__results = true;
  rep.privacy.flagged = [{ column: 'customer_email', kind: 'email; coded as it arrived', decision: 'withhold' }, { column: 'notes', kind: 'free text', decision: 'withhold' },
    { column: 'staff_name', kind: 'people\'s names', decision: 'withhold' }, { column: 'visit_date', kind: 'named like personal data', decision: 'withhold' }];
  rep.__profile = { ok: true, name: 'orders.csv', rows: 240, columns_total: 7,
    columns: [
      { name: 'order_date', filled: 240, distinct: 240, numeric_share: 0, date_share: 1, looks_personal: false },
      { name: 'amount', filled: 240, distinct: 239, numeric_share: 1, min: -12.5, median: 40, max: 95, integers: false, percent_sign: false },
      { name: 'customer_email', filled: 240, distinct: 60, numeric_share: 0, date_share: 0, looks_personal: true, privacy_flag: 'email; coded as it arrived', top_values: ['ann@example.com'], values: ['ann@example.com', 'bo@example.com'], min: 1, max: 9 },
      { name: 'Notes', filled: 240, distinct: 90, numeric_share: 0, date_share: 0.3, top_values: ['call Ann Lee'], looks_personal: false, privacy_flag: 'free text' },
      { name: 'Staff Name', filled: 240, distinct: 4, numeric_share: 0, date_share: 0, top_values: ['Dana Whitfield', 'Marco Bellini'], looks_personal: false, privacy_flag: 'people\'s names' },
      { name: 'visit_date', filled: 240, distinct: 200, numeric_share: 0, date_share: 1, top_values: ['2019-03-02'], looks_personal: false, privacy_flag: 'named like personal data' },
      { name: 'Mystery', filled: 240, distinct: 30, numeric_share: 0, date_share: 0, top_values: ['Zed Quill'], looks_personal: false, privacy_flag: 'named like personal data' }],
    time: { column: 'visit_date', first: '2019-01', last: '2019-12', months: 12, distinct_years: 1 },
    analysis_limits: [{ analysis: 'trend', ok: false, why: 'needs 8 or more complete years of values; its date column visit_date is flagged as personal, so its span is not shown' },
      { analysis: 'themes', ok: false, why: 'needs a free-text column with 20 or more texts; Notes has 12' },
      { analysis: 'rank', ok: true, why: 'needs a column of entities (countries, products) and a measure; Staff Name has 4 values' }] };
  rep.__signals = [{ kind: 'contract_failed', column: 'Notes', detail: 'only 70 of 240 values read as dates (29%)' }, { kind: 'analysis_refused', detail: 'themes: notes holds too few texts' },
    { kind: 'contract_failed', column: 'amount', detail: '40 of 240 values are below 0, so it may not be a level' }, { kind: 'contract_failed', column: 'customer_email', detail: 'only 60 of 240 values are unique (25%)' }];
  const T3 = (column, action, signal) => ({ column, semantic_type: 'x', test: 'stub test', checked: 240, failed: 40, examples: [], action, unreadable: 0, out_of_range: 0, repeated: 0, unexpected: 0, misread: signal, signal });
  rep.__contracts = { tests: [T3('amount', '40 are below 0: kept by the engine; the tests changed no value', true), T3('customer_email', 'probably not an identifier: kept by the engine; the tests changed no value', true),
    T3('Notes', 'probably not a date: set aside by the engine (notes_date: value matches no known date format)', true)], cells_flagged: 3, line: 'source_line', note: 'stub note' };
  // the plan names the withheld Notes (as one the proxy's cache kept from a run that kept it could): the
  // re-plan must not send any of that back
  const plan = { goal: 'Spend by month and by Notes', understanding: 'Orders.', quality_risks: [], primary: 'amount',
    operations: [{ op: 'set_aside', columns: ['Notes'] }, { op: 'exclude_blank', column: 'amount' }],
    analyses: [{ type: 'themes', columns: ['Notes'] }, { type: 'distribution', columns: ['amount'] }, { type: 'compare', columns: ['amount'], by: 'Notes' }],
    columns: [{ name: 'amount', semantic_type: 'level', role: 'measure' }, { name: 'Notes', semantic_type: 'free_text', role: 'metadata' }] };
  const reply = (b) => b.profile ? (b.feedback ? { status: 502, json: { error: 'rejected_plan' } } : { status: 200, json: { plan } }) : { status: 503, json: { error: 'x' } };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: reply });
  const keys = await p.evaluate(() => [window.NLTry.planKeyText('ab12', { notes: 'withhold' }), window.NLTry.planKeyText('ab12', { staff_name: 'keep', customer_email: 'code', notes: 'withhold' }),
    window.NLTry.planKeyText('ab12', { staff_name: 'code', customer_email: 'code' }), window.NLTry.planKeyText('', { staff_name: 'keep' })]);
  ok(keys[0] === '' && keys[1] === 'ab12\ncustomer_email=code\nstaff_name=keep' && keys[2] !== keys[1] && keys[3] === '',
    'the plan cache key does not follow the personal-data choices (a plan made under other choices could be served): ' + JSON.stringify(keys));
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])');
  await p.check('#try-pd input[data-col="customer_email"][value="code"]');
  await p.check('#try-pd input[data-col="staff_name"][value="keep"]');
  await p.check('#try-pd-send-ok');                   // option B: the kept column goes only once its box is ticked
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  await p.waitForFunction(() => /asked it to correct itself/.test(document.getElementById('try-plan-card').textContent), null, { timeout: 20000 });
  const bodies = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').map((r) => r.body);
  ok(bodies.length === 2, bodies.length + ' /plan requests (want 2: the plan and its one correction)');
  const withheld = ['notes', 'visit_date', 'Mystery'], values = ['call Ann Lee', 'ann@example.com', 'bo@example.com', '2019-03-02', 'Zed Quill'];
  bodies.forEach((B, i) => {
    ok(!namesIn(B, withheld).length, '/plan ' + (i + 1) + ' names a withheld column: ' + JSON.stringify(namesIn(B, withheld)));
    ok(!values.some((v) => B.indexOf(v) >= 0), '/plan ' + (i + 1) + ' carries a value of a withheld or coded column: ' + JSON.stringify(values.filter((v) => B.indexOf(v) >= 0)));
  });
  const pr = JSON.parse(bodies[0]).profile, col = (n) => pr.columns.filter((c) => c.name === n)[0];
  // the file's name never goes (final review, 29 Sep 2026): the placeholder /report uses stands in
  ok(pr.name === '[your file]' && !bodies.some((B) => /orders\.csv|sample-messy/.test(B)), 'the /plan body carries the file\'s name: ' + JSON.stringify(pr.name));
  ok(JSON.stringify(pr.columns.map((c) => c.name)) === JSON.stringify(['order_date', 'amount', 'customer_email', 'Staff Name']), 'the columns sent: ' + JSON.stringify(pr.columns.map((c) => c.name)));
  const ce = col('customer_email');
  ok(Object.keys(ce).every((k) => PERSONAL_KEEP_UI.indexOf(k) >= 0) && ce.looks_personal === true && ce.distinct === 60, 'the coded column goes with more than its name, type and counts: ' + JSON.stringify(ce));
  const sn = col('Staff Name');
  ok(sn && !('privacy_flag' in sn) && JSON.stringify(sn.top_values) === JSON.stringify(['Dana Whitfield', 'Marco Bellini']), 'the kept column does not go like any other: ' + JSON.stringify(sn));
  ok(JSON.stringify(col('amount')) === JSON.stringify(rep.__profile.columns[1]), 'an unflagged column was changed');
  ok(pr.time === null, 'profile.time read from a withheld column was sent: ' + JSON.stringify(pr.time));
  const why = pr.analysis_limits.map((e) => e.why);
  ok(why[0] === 'needs 8 or more complete years of values; its date column (withheld) is flagged as personal, so its span is not shown' && why[1] === 'needs a free-text column with 20 or more texts; (withheld) has 12' &&
    why[2] === rep.__profile.analysis_limits[2].why, 'the analysis_limits texts name a withheld column, or a kept one was changed: ' + JSON.stringify(why));
  const fb = JSON.parse(bodies[1]).feedback;
  ok(JSON.stringify(fb.signals.map((x) => x.column || x.detail)) === JSON.stringify(['amount', 'customer_email']), 'the re-plan sent a signal about a withheld column, or dropped another: ' + JSON.stringify(fb.signals));
  const pp = fb.previous_plan;
  ok(pp.goal === 'Spend by month and by (withheld)' && pp.primary === 'amount' && JSON.stringify(pp.columns.map((c) => c.name)) === '["amount"]' &&
    JSON.stringify(pp.operations) === JSON.stringify([{ op: 'exclude_blank', column: 'amount' }]) && JSON.stringify(pp.analyses) === JSON.stringify([{ type: 'distribution', columns: ['amount'] }]),
    'the re-plan sent back the parts of the plan that name a withheld column, or lost the others: ' + JSON.stringify(pp));
  // the Data tests card: the engine's words as given; "the AI was asked to look again" only where it was
  const rows = await p.evaluate(() => Array.from(document.querySelectorAll('#try-plan-card table.try-contracts tbody tr')).map((tr) => [tr.children[0].textContent, tr.children[4].textContent]));
  const want = rep.__contracts.tests.map((x) => [x.column, x.action + (x.column === 'Notes' ? '' : '; the AI was asked to look again')]);
  ok(JSON.stringify(rows) === JSON.stringify(want), 'the Data tests card does not show the engine\'s words as given: ' + JSON.stringify(rows));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// The engine's gate (final review, 29 Sep 2026): when it set aside more than its limit of the rows, no new plan
// can make them readable (a re-plan could only move the analysis to another column, the result the gate exists to
// prevent), so the page asks /plan once and never again, even when a report (an older adapter) carries signals.
// The refusal shows on the plan card and in the headline, and no data test says the AI was asked to look again.
// The AI report puts the file's name back where /report had "[your file]", in this browser only.
check('try-no-replan-when-the-engine-gate-trips', DESK, async (ctx) => {
  const plan = { goal: 'How has order value changed?', understanding: 'Orders.', quality_risks: [], operations: [],
    analyses: [{ type: 'trend', columns: ['amount'] }], columns: [{ name: 'order_date', semantic_type: 'date', role: 'date' }, { name: 'amount', semantic_type: 'flow_amount', role: 'target' }] };
  const rep = stubReport(); rep.__profile = true; rep.__results = true;
  const gateLine = 'trend: the engine set aside 30.0% of the rows (180 of 600), over its 20% limit, so no analysis is drawn from the rest';
  rep.story.headline = 'The business analysis did not run: cleaning set aside 30.0% of the 600 rows, above the 20% at which the cleaning itself is suspect.';
  rep.ai_analyses = { items: [], refused: [gateLine], rows: { kept: 420, set_aside: 180 }, date: null, gate: { over: true, pct: 30, limit: 20, aside: 180, rows: 600 },
    note: 'Not computed: the engine stopped its own business analysis because it set aside too many rows for the rest to stand for the file, and the AI\'s analyses stop with it.' };
  rep.__signals = [{ kind: 'contract_failed', column: 'order_date', detail: 'only 420 of 600 values read as dates (70%)' }, { kind: 'analysis_refused', detail: gateLine }];
  rep.__contracts = { tests: [{ column: 'order_date', semantic_type: 'date', test: 'reads as a date', checked: 600, failed: 180, examples: [],
    action: 'probably not a date: only 420 of 600 values read as dates (70%); the engine set these rows aside', unreadable: 180, out_of_range: 0, repeated: 0,
    unexpected: 0, misread: true, signal: true }], cells_flagged: 180, line: 'source_line', note: 'stub' };
  const reply = (b) => b.profile ? { status: 200, json: { plan: b.feedback ? Object.assign({}, plan, { revised: true, changes: ['moved the axis'] }) : plan } }
    : { status: 200, json: { report: 'The amount column of [your file] could not be analysed.', sources: [], model: 'check', repaired: 0 } };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: reply });
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])'); await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  await p.waitForTimeout(500);
  const plans = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST');
  ok(plans.length === 1 && !JSON.parse(plans[0].body).feedback, plans.length + ' /plan requests (want exactly 1, with no feedback)');
  const card = (await p.textContent('#try-plan-card')).replace(/\s+/g, ' ');
  ok(card.indexOf('Asked for but not computed') >= 0 && card.indexOf(gateLine) >= 0, 'the plan card does not show the refusal: ' + card.slice(0, 500));
  ok(card.indexOf('probably not a date: only 420 of 600 values read as dates (70%)') >= 0 && !/asked to look again|asked it to correct itself|corrected its plan/.test(card),
    'the card does not show the data test, or says the AI was asked again: ' + card.slice(0, 600));
  ok(/set aside too many rows for any plan to read/.test(await p.textContent('#try-stages')), 'the progress list does not say why the AI was not asked again');
  ok((await p.textContent('#try-report')).indexOf('The business analysis did not run') >= 0, 'the headline does not show the refusal');
  const ai = await p.textContent('#try-ai-report .ai-rep-body');
  const reports = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'report' && r.method === 'POST');
  ok(/The amount column of sample-messy\.csv could not be analysed/.test(ai) && !/\[your file\]/.test(ai), 'the AI report does not put the file\'s name back: ' + ai.slice(0, 200));
  ok(reports.length === 1 && !/sample-messy/.test(reports[0].body), 'the /report body carries the file\'s name');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// The same promise on the adapter's real profile and report (tools/make_ui_fixtures.py orders-private: a coded
// email, a withheld note, a kept staff name, all three typed by the plan): /plan never carries a withheld or
// coded value, and the Data tests card and the flagged-cells download show the adapter's words exactly as it
// sent them, with no value of a withheld or coded column. A failure here with the page unchanged is the
// adapter's words or values (engine/nl_browser.py), which the page only renders.
check('try-real-adapter-profile-and-data-tests-keep-the-privacy-promise', DESK, async (ctx) => {
  const rep = fixture('orders-private'), prof = fixtureProfile('orders-private');
  ok(!rep.__fixture_error, 'the adapter could not make the planned orders-private run (tools/make_ui_fixtures.py): ' + rep.__fixture_error);
  const csv = fs.readFileSync(path.join(FX_DIR, 'orders-private.csv'), 'utf8').trim().split('\n').slice(1).map((l) => l.split(','));
  const emails = Array.from(new Set(csv.map((r) => r[1]))), notes = Array.from(new Set(csv.map((r) => r[2]).filter((v) => /^call /.test(v))));
  ok(emails.length > 10 && notes.length > 10, 'the fixture lost its personal values');
  ok(prof.columns.some((c) => c.name === 'staff_name') && prof.analysis_limits && prof.analysis_limits.length && 'time' in prof, 'the adapter profile lacks time, analysis_limits or the columns: ' + JSON.stringify(prof).slice(0, 300));
  rep.__profile = prof; rep.__results = true; rep.__contracts = rep.contracts; rep.__signals = rep.plan_signals || [];
  rep.__landed = fixtureLanded('orders-private'); rep.__profileDecisions = { customer_email: 'code', notes: 'withhold', staff_name: 'keep' };
  // the web searches (final review, 30 Sep 2026): the adapter built only the item whose terms are all on its list; the
  // items naming the kept staff or a buyer, and the old free-text list, left nothing; the page sends exactly that list
  const cq = (rep.ai_plan || {}).context_queries;
  ok(JSON.stringify(cq) === JSON.stringify(['consumer spending Canada 2024 2025']) && (rep.ai_plan.context_queries_dropped || []).length === 3,
    'the adapter\'s web searches for orders-private: ' + JSON.stringify(rep.ai_plan && [rep.ai_plan.context_queries, rep.ai_plan.context_queries_dropped]));
  rep.__cq = cq;
  const scan = rep.privacy.flagged.map((f) => f.column);
  ok(['customer_email', 'notes', 'staff_name'].every((c) => scan.indexOf(c) >= 0), 'the adapter no longer flags the three personal columns: ' + JSON.stringify(scan));
  const plan = { goal: 'How does spend move month by month?', understanding: 'Orders.', quality_risks: [], operations: [], analyses: [], columns: [{ name: 'spend', semantic_type: 'flow_amount', role: 'target' }] };
  const reply = (b) => b.profile ? (b.feedback ? { status: 502, json: { error: 'rejected_plan' } } : { status: 200, json: { plan } }) : { status: 503, json: { error: 'x' } };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: reply });
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])');
  await p.check('#try-pd input[data-col="customer_email"][value="code"]');
  await p.check('#try-pd input[data-col="staff_name"][value="keep"]');
  await p.check('#try-pd-send-ok');                   // option B: the kept column goes only once its box is ticked
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  await tryUntil(p, '#try-plan-card table.try-contracts');
  const bodies = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').map((r) => r.body);
  ok(bodies.length >= 1, 'no /plan request');
  bodies.forEach((B, i) => {
    ok(!namesIn(B, ['notes']).length, '/plan ' + (i + 1) + ' names the withheld notes column');
    const leak = emails.concat(notes).filter((v) => B.indexOf(v) >= 0);
    ok(!leak.length, '/plan ' + (i + 1) + ' carries a value of a withheld or coded column: ' + JSON.stringify(leak.slice(0, 3)));
  });
  const pr = JSON.parse(bodies[0]).profile, ce = pr.columns.filter((c) => c.name === 'customer_email')[0], sn = pr.columns.filter((c) => c.name === 'staff_name')[0];
  ok(ce && ce.looks_personal === true && Object.keys(ce).every((k) => PERSONAL_KEEP_UI.indexOf(k) >= 0), 'the coded email goes with more than its name, type and counts: ' + JSON.stringify(ce));
  ok(sn && !('privacy_flag' in sn), 'the kept staff_name still goes as flagged: ' + JSON.stringify(sn));
  const k = rep.contracts, sent = bodies[1] ? JSON.parse(bodies[1]).feedback.signals : [];
  const asked = (t) => (t.signal || t.misread) && sent.some((x) => x.kind === 'contract_failed' && x.column === t.column);
  const rows = await p.evaluate(() => Array.from(document.querySelectorAll('#try-plan-card table.try-contracts tbody tr')).map((tr) => tr.children[4].textContent));
  ok(JSON.stringify(rows) === JSON.stringify(k.tests.map((t) => String(t.action || '') + (asked(t) ? '; the AI was asked to look again' : ''))), 'the Data tests card does not show the adapter\'s words as given: ' + JSON.stringify(rows).slice(0, 400));
  ok(!sent.some((x) => x.column === 'notes'), 'the re-plan told the AI about the withheld notes column');
  const card = await p.textContent('#try-plan-card');
  const cardLeak = emails.concat(notes).filter((v) => card.indexOf(v) >= 0);
  ok(!cardLeak.length, 'the Data tests card shows a value of a withheld or coded column: ' + JSON.stringify(cardLeak.slice(0, 3)));
  ok(new RegExp('Cells the data tests flagged \\(' + k.cells_flagged + '\\) CSV').test(card), 'the flagged-cells button does not say what it holds: ' + card.slice(-300));
  const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#try-plan-card [data-dl="contract_flagged_csv"]')]);
  const text = fs.readFileSync(await dl.path(), 'utf8');
  ok(text === rep.downloads.contract_flagged_csv, 'the flagged-cells download is not the adapter\'s text');
  const dlLeak = emails.concat(notes).filter((v) => text.indexOf(v) >= 0);
  ok(!dlLeak.length, 'the adapter\'s flagged-cells download holds a value of a withheld or coded column: ' + JSON.stringify(dlLeak.slice(0, 3)));
  for (let i = 0; i < 40 && !ctx.__reqs.some((r) => r.url === PROXY_URL + 'report' && r.method === 'POST'); i++) await p.waitForTimeout(250);
  const rb = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'report' && r.method === 'POST').map((r) => JSON.parse(r.body));
  ok(rb.length >= 1 && rb.every((b) => JSON.stringify(b.context_queries) === JSON.stringify(cq)), '/report was not sent the adapter\'s web searches: ' + JSON.stringify(rb.map((b) => b.context_queries)));
  ok(rb.every((b) => !/Dana Whitfield|Marco Bellini|example\.org/.test(JSON.stringify(b.context_queries))), 'a name or an email went into a web search');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { acceptDownloads: true });

// The /plan body for a file with a withheld, a coded and a kept column is exactly the adapter's profile made
// AFTER the visitor's choices (engine/worker.js "profile" -> nl_browser.plan_profile_json; integration review,
// 29 Sep 2026: the profile was made at the scan, with every flagged column withheld, so a coded or kept column
// never reached the planner as chosen): the withheld notes absent and never named; the coded customer_email
// with only its name, type and counts and looks_personal: true; the kept staff_name like any column (its
// commonest values, no privacy flag); the unflagged columns as the adapter profiled them. The page's own filter
// (T.planProfile, its second layer) leaves the adapter's profile exactly as it is. The stand-in worker answers
// only the visitor's own choices (__profileDecisions), so a profile asked for before the choices, or with other
// choices, would send no /plan at all.
check('try-plan-body-holds-withheld-coded-and-kept-columns-as-chosen', DESK, async (ctx) => {
  const rep = fixture('orders-private'), prof = fixtureProfile('orders-private'), landedMap = fixtureLanded('orders-private');
  ok(!rep.__fixture_error, 'the adapter could not make the planned orders-private run (tools/make_ui_fixtures.py): ' + rep.__fixture_error);
  const byName = (pr, n) => (pr.columns || []).filter((c) => c.name === n)[0];
  // the adapter's own profile already keeps the promise (the page's filter is the second layer, not the first)
  ok(!byName(prof, 'notes') && !namesIn(JSON.stringify(prof), ['notes']).length, 'the adapter\'s profile names the withheld notes: ' + JSON.stringify(prof).slice(0, 300));
  const pce = byName(prof, 'customer_email'), psn = byName(prof, 'staff_name');
  ok(pce && pce.looks_personal === true && pce.privacy_flag && Object.keys(pce).every((k) => PERSONAL_KEEP_UI.indexOf(k) >= 0), 'the adapter\'s coded column: ' + JSON.stringify(pce));
  ok(psn && psn.looks_personal === false && !('privacy_flag' in psn) && (psn.top_values || []).length === 4, 'the adapter\'s kept column is not profiled like any other: ' + JSON.stringify(psn));
  ok(landedMap.customer_email === 'customer_email' && landedMap.staff_name === 'staff_name' && landedMap.notes === 'notes', 'the landed map: ' + JSON.stringify(landedMap));
  rep.__profile = prof; rep.__landed = landedMap; rep.__profileDecisions = { customer_email: 'code', notes: 'withhold', staff_name: 'keep' };
  const plan = { goal: 'How does spend move month by month?', understanding: 'Orders.', quality_risks: [], operations: [], analyses: [], columns: [{ name: 'spend', semantic_type: 'flow_amount', role: 'target' }] };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status: 200, json: { plan } } : { status: 503, json: { error: 'x' } } });
  // the worker's map gives the page every spelling of a withheld column (the file's header and the landed name)
  const held = await p.evaluate(() => window.NLTry.planWithheld({ columns: [] }, [{ column: 'date_of_birth', kind: 'x' }, { column: 'staff_name', kind: 'y' }],
    { staff_name: 'keep' }, { 'Date Of Birth': 'date_of_birth', 'Staff Name': 'staff_name', 'Visit Date': 'visit_date' }));
  ok(JSON.stringify(held.slice().sort()) === JSON.stringify(['Date Of Birth', 'date_of_birth']), 'the withheld spellings: ' + JSON.stringify(held));
  const sigs = await p.evaluate((h) => window.NLTry.planSignals([{ kind: 'analysis_refused', detail: 'trend: Date Of Birth is not a date' }, { kind: 'analysis_refused', detail: 'rank: Staff Name' }], h), held);
  ok(sigs.length === 1 && sigs[0].detail === 'rank: Staff Name', 'a signal naming the withheld column by its header went to the planner: ' + JSON.stringify(sigs));
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])');
  ok(!ctx.__reqs.some((r) => r.url.indexOf(PROXY_URL) === 0), 'a request reached the proxy before the visitor chose');
  await p.check('#try-pd input[data-col="customer_email"][value="code"]');
  await p.check('#try-pd input[data-col="staff_name"][value="keep"]');
  await p.check('#try-pd-send-ok');                   // option B: the kept column goes only once its box is ticked
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  const bodies = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').map((r) => r.body);
  ok(bodies.length === 1, bodies.length + ' /plan requests (want 1: the profile was made under the visitor\'s own choices)');
  const sent = JSON.parse(bodies[0]).profile;
  const canon = (x) => JSON.stringify(x, (k, v) => (v && typeof v === 'object' && !Array.isArray(v)) ? Object.keys(v).sort().reduce((o, kk) => { o[kk] = v[kk]; return o; }, {}) : v);
  ok(canon(sent) === canon(prof), 'the /plan profile is not the adapter\'s profile under the choices: ' + canon(sent).slice(0, 400) + ' vs ' + canon(prof).slice(0, 400));
  ok(JSON.stringify(sent.columns.map((c) => c.name)) === JSON.stringify(['order_date', 'customer_email', 'staff_name', 'spend']), 'the columns sent: ' + JSON.stringify(sent.columns.map((c) => c.name)));
  ok(!namesIn(bodies[0], ['notes']).length, '/plan names the withheld notes column');
  const ce = byName(sent, 'customer_email'), sn = byName(sent, 'staff_name');
  ok(ce.looks_personal === true && typeof ce.privacy_flag === 'string' && Object.keys(ce).every((k) => PERSONAL_KEEP_UI.indexOf(k) >= 0) &&
    ['top_values', 'values', 'examples', 'min', 'median', 'max'].every((k) => !(k in ce)), 'the coded column goes with more than its name, type and counts: ' + JSON.stringify(ce));
  ok(sn.looks_personal === false && !('privacy_flag' in sn) && sn.top_values.indexOf('Dana Whitfield') >= 0, 'the kept column does not go like any other: ' + JSON.stringify(sn));
  ok(sent.time && sent.time.column === 'order_date', 'profile.time: ' + JSON.stringify(sent.time));
  const csv = fs.readFileSync(path.join(FX_DIR, 'orders-private.csv'), 'utf8').trim().split('\n').slice(1).map((l) => l.split(','));
  const leak = Array.from(new Set(csv.map((r) => r[1]).concat(csv.map((r) => r[2]).filter((v) => /^call /.test(v))))).filter((v) => bodies[0].indexOf(v) >= 0);
  ok(!leak.length, '/plan carries a value of the coded or withheld column: ' + JSON.stringify(leak.slice(0, 3)));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

/* ------------------------------------------------------------ option B: sending kept personal columns
   The owner's decision (29 Sep 2026): the AI may read a flagged column's real values, but only after the
   visitor's explicit, informed agreement. Withhold stays the default and nothing personal is sent by default.
   A flagged column set to Keep (one by one, or all at once with "Keep all") puts a required, unticked box on the
   step that names those columns and says where their values go; "Continue with the AI" stays off until it is
   ticked, and no /plan request leaves before; "Continue without AI" always works and sends nothing. The plan
   card records the choice once; a report made with kept columns warns before a share link is made (a PDF is
   made from a link, so it warns too), and cancel makes none; a share body never carries the file's name; a new
   file starts every choice again. The sentences are stated here independently of the page. */
// names only in the example: an email or a phone number is coded as it arrives and cannot be kept
const OPTIN_BOX = (names) => 'Send these personal columns to the AI: ' + names.join(', ') + '. Their values (for example people\'s names) ' +
  'go to DeepSeek, a company based in China, through this site\'s proxy, and may appear in the AI\'s report and in any link you share.';
const OPTIN_RECORD = (names) => 'You chose to send these personal columns to the AI: ' + names.join(', ') + '.';
// a share link always warns (29 Sep 2026 review): in general words when nothing personal was kept
const SHARE_WARNING = 'This report may contain personal values (people\'s names, for example). Anyone with the link can see them.';
const SHARE_WARNING_GENERAL = 'This report is built from your file and may contain values from it. Anyone with the link can see them.';
const STAFF = ['Dana Whitfield', 'Marco Bellini', 'Priya Raman', 'Tomasz Nowak'];
const SHARE_LINK = PROXY_URL + 'r/abcdefghij0123456789';
// a stand-in profile that holds every column, the flagged ones marked personal (as a profile that slipped would):
// the page's own filter must drop a withheld column, and send a kept one like any column (looks_personal false)
function optinReport(coded) {
  const rep = stubReport(); rep.__results = true;
  rep.privacy.flagged = [{ column: 'customer_email', kind: coded ? 'email; coded as it arrived' : 'email', decision: 'withhold' },
    { column: 'notes', kind: 'free text', decision: 'withhold' }, { column: 'staff_name', kind: 'people\'s names', decision: 'withhold' }];
  rep.__profile = { ok: true, name: 'orders.csv', rows: 240, columns_total: 5,
    columns: [
      { name: 'order_date', filled: 240, distinct: 240, numeric_share: 0, date_share: 1, looks_personal: false },
      { name: 'amount', filled: 240, distinct: 239, numeric_share: 1, min: 3.5, median: 40, max: 95, integers: false, percent_sign: false },
      { name: 'customer_email', filled: 240, distinct: 60, numeric_share: 0, date_share: 0, top_values: ['ann@example.com', 'bo@example.com'], looks_personal: true, privacy_flag: 'email' },
      { name: 'Notes', filled: 240, distinct: 90, numeric_share: 0, date_share: 0, top_values: ['call Ann Lee', 'left at door'], looks_personal: true, privacy_flag: 'free text' },
      { name: 'Staff Name', filled: 240, distinct: 4, numeric_share: 0, date_share: 0, top_values: STAFF.slice(), values: STAFF.slice(), looks_personal: true, privacy_flag: 'people\'s names' }],
    time: { column: 'order_date', first: '2025-01', last: '2025-12', months: 12, distinct_years: 1 },
    analysis_limits: [{ analysis: 'rank', ok: true, why: 'needs a column of entities and a measure; Staff Name has 4 values' },
      { analysis: 'themes', ok: false, why: 'needs a free-text column with 20 or more texts; Notes has 12' }] };
  return rep;
}
const OPTIN_PLAN = { goal: 'Which member of staff sells most?', understanding: 'Orders.', quality_risks: [], operations: [], analyses: [{ type: 'rank', columns: ['amount'], by: 'Staff Name' }] };
// the stand-in proxy: /plan, /report and /share (each body told apart by its own keys: a /share body carries the
// report and its days, and since 30 Sep 2026 the trimmed results too, so it is told apart first); shares collects them
function optinReply(shares) {
  return (b) => {
    if (typeof b.report === 'string' && 'days' in b) { if (shares) shares.push(b); return { status: 200, json: { link: SHARE_LINK, delete_token: 'tok' } }; }
    if (b.profile) return { status: 200, json: { plan: OPTIN_PLAN } };
    if (b.results) return { status: 200, json: { report: 'Dana Whitfield sold most in [your file].', sources: [], model: 'check', repaired: 0 } };
    return { status: 503, json: { error: 'x' } };
  };
}
const proxyPosts = (ctx, tail) => ctx.__reqs.filter((r) => r.method === 'POST' && r.url === PROXY_URL + tail);
const toProxy = (ctx) => ctx.__reqs.filter((r) => r.url.indexOf(PROXY_URL) === 0);
async function optinStep(p) {
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden])');
}
async function keptRun(p, cols) {       // Keep for these columns, tick the box, continue with the AI, wait for the AI report
  await optinStep(p);
  for (const c of cols) await p.check('#try-pd input[data-col="' + c + '"][value="keep"]');
  await p.check('#try-pd-send-ok');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
}

check('try-optin-nothing-kept-shows-no-box-and-sends-the-same-plan-body', DESK, async (ctx) => {
  const rep = optinReport();
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: optinReply() });
  await optinStep(p);
  ok(!(await p.isVisible('#try-pd-send-ok')) && !/Send these personal columns/.test(await p.textContent('#try-pd')), 'a consent box is shown with every flagged column withheld');
  ok(!(await p.isDisabled('#try-pd-go')) && !(await p.isDisabled('#try-pd-noai')), 'a Continue button is off with nothing kept');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  const plans = proxyPosts(ctx, 'plan');
  ok(plans.length === 1, plans.length + ' /plan requests (want 1)');
  // exactly the body the page sent before option B: the withheld columns left out and never named
  const want = { ok: true, name: '[your file]', rows: 240, columns_total: 5, columns: [rep.__profile.columns[0], rep.__profile.columns[1]],
    time: rep.__profile.time, analysis_limits: [{ analysis: 'rank', ok: true, why: 'needs a column of entities and a measure; (withheld) has 4 values' },
      { analysis: 'themes', ok: false, why: 'needs a free-text column with 20 or more texts; (withheld) has 12' }] };
  const got = JSON.parse(plans[0].body);
  ok(JSON.stringify(got.profile) === JSON.stringify(want), 'the /plan profile changed with nothing kept: ' + JSON.stringify(got.profile).slice(0, 500));
  ok(!STAFF.concat(['ann@example.com', 'call Ann Lee']).some((v) => plans[0].body.indexOf(v) >= 0), 'a personal value went to /plan with nothing kept');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  ok(!/You chose to send/.test(await p.textContent('#try')), 'the report records an opt-in nobody made');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-optin-a-kept-column-needs-the-ticked-box-before-any-plan-request', DESK, async (ctx) => {
  const rep = optinReport(); rep.__profileDecisions = { customer_email: 'withhold', notes: 'withhold', staff_name: 'keep' };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: optinReply() });
  await optinStep(p);
  await p.check('#try-pd input[data-col="staff_name"][value="keep"]');
  ok(await p.isVisible('#try-pd-send-ok') && !(await p.isChecked('#try-pd-send-ok')), 'no unticked box after Keep');
  const lab = (await p.textContent('label[for="try-pd-send-ok"]')).replace(/\s+/g, ' ').trim();
  ok(lab === OPTIN_BOX(['staff_name']), 'the box does not say exactly what is sent and where: ' + lab);
  ok(await p.getAttribute('#try-pd-send-ok', 'required') !== null, 'the box is not marked required');
  ok(await p.isDisabled('#try-pd-go') && !(await p.isDisabled('#try-pd-noai')), 'Continue with the AI is not off before the tick, or Continue without AI is');
  const why = await p.evaluate(() => { const b = document.getElementById('try-pd-go'), ids = (b.getAttribute('aria-describedby') || '').split(/\s+/);
    return ids.map((i) => document.getElementById(i)).filter((n) => n && !n.hidden).map((n) => n.textContent).join(' '); });
  ok(/tick the box/i.test(why) && /Continue without AI sends nothing/.test(why), 'the off button has no accessible explanation: ' + why);
  // the gate holds even when the button is forced on
  await p.evaluate(() => { const b = document.getElementById('try-pd-go'); b.disabled = false; b.click(); });
  await p.waitForTimeout(400);
  ok(!toProxy(ctx).length && await p.isVisible('#try-pd'), 'a request left, or the step closed, before the box was ticked');
  await p.check('#try-pd-send-ok');
  ok(!(await p.isDisabled('#try-pd-go')), 'Continue with the AI stays off after the tick');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  const plans = proxyPosts(ctx, 'plan');
  ok(plans.length === 1, plans.length + ' /plan requests (want 1: the profile was asked for under the visitor\'s own choices)');
  const B = plans[0].body, pr = JSON.parse(B).profile;
  ok(JSON.stringify(pr.columns.map((c) => c.name)) === JSON.stringify(['order_date', 'amount', 'Staff Name']), 'the columns sent: ' + JSON.stringify(pr.columns.map((c) => c.name)));
  const sn = pr.columns[2];
  ok(sn.looks_personal === false && !('privacy_flag' in sn) && JSON.stringify(sn.values) === JSON.stringify(STAFF) && JSON.stringify(sn.top_values) === JSON.stringify(STAFF),
    'the kept column does not go like any other column, values included: ' + JSON.stringify(sn));
  ok(!namesIn(B, ['notes', 'customer_email']).length && !['ann@example.com', 'bo@example.com', 'call Ann Lee', 'left at door'].some((v) => B.indexOf(v) >= 0),
    'a withheld column is named, or its values sent: ' + B.slice(0, 400));
  ok(pr.analysis_limits[0].why === rep.__profile.analysis_limits[0].why && pr.analysis_limits[1].why === 'needs a free-text column with 20 or more texts; (withheld) has 12',
    'the limits name the withheld notes, or hide the kept staff name: ' + JSON.stringify(pr.analysis_limits));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-optin-keep-all-sets-every-column-and-one-can-go-back', DESK, async (ctx) => {
  const rep = optinReport(true);
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: optinReply() });
  await optinStep(p);
  ok((await p.textContent('#try-pd-all')).trim() === 'Keep all: let the analysis and the AI read these columns', 'no "Keep all" control, or it says something else');
  await p.click('#try-pd-all');
  const val = (c) => p.evaluate((col) => (document.querySelector('#try-pd input[data-col="' + col + '"]:checked') || {}).value, c);
  ok(await val('notes') === 'keep' && await val('staff_name') === 'keep', 'Keep all did not set every column that can be kept to Keep');
  ok(await val('customer_email') === 'withhold', 'Keep all changed a column that cannot be kept (coded as it arrived)');
  ok(/every column that can be kept/.test(await p.textContent('#try-pd')), 'Keep all does not say what it did');
  ok((await p.textContent('label[for="try-pd-send-ok"]')).replace(/\s+/g, ' ').trim() === OPTIN_BOX(['notes', 'staff_name']), 'the box does not name the kept columns');
  await p.check('#try-pd-send-ok');
  // set one back: the box names the columns left, and a tick given to another list is not kept
  await p.check('#try-pd input[data-col="notes"][value="withhold"]');
  ok((await p.textContent('label[for="try-pd-send-ok"]')).replace(/\s+/g, ' ').trim() === OPTIN_BOX(['staff_name']), 'the box still names the column set back to Withhold');
  ok(!(await p.isChecked('#try-pd-send-ok')) && await p.isDisabled('#try-pd-go'), 'a tick given for other columns still counts');
  await p.check('#try-pd-send-ok');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  const B = proxyPosts(ctx, 'plan')[0].body, pr = JSON.parse(B).profile;
  ok(JSON.stringify(pr.columns.map((c) => c.name)) === JSON.stringify(['order_date', 'amount', 'Staff Name']), 'the columns sent: ' + JSON.stringify(pr.columns.map((c) => c.name)));
  ok(!namesIn(B, ['notes', 'customer_email']).length && !['call Ann Lee', 'left at door', 'ann@example.com'].some((v) => B.indexOf(v) >= 0), 'the column set back to Withhold (or the coded one) reached /plan');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-optin-the-report-records-the-columns-sent', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: optinReport(), proxy: 'set', proxyReply: optinReply() });
  await keptRun(p, ['staff_name', 'notes']);
  const t = await p.evaluate(() => document.getElementById('try').innerText.replace(/\s+/g, ' '));
  const rec = OPTIN_RECORD(['notes', 'staff_name']);
  ok(t.split(rec).length === 2, 'the report does not state the choice exactly once (' + (t.split(rec).length - 1) + ' times): ' + rec);
  ok((await p.textContent('#try-plan-card')).indexOf(rec) >= 0, 'the record is not on the plan card');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-share-always-warns-and-cancel-makes-no-link', DESK, async (ctx) => {
  const shares = [];
  let p = await openTry(ctx, { stubReport: optinReport(), proxy: 'set', proxyReply: optinReply(shares) });
  await keptRun(p, ['staff_name']);
  const warn = async () => ((await p.textContent('#try-share-out')) || '').replace(/\s+/g, ' ');
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  ok((await warn()).indexOf(SHARE_WARNING) >= 0 && !proxyPosts(ctx, 'share').length, 'no warning before the link, or a link was made first: ' + (await warn()));
  await p.click('#try-share-no');
  await p.waitForTimeout(400);
  ok(!proxyPosts(ctx, 'share').length && !shares.length && /No link was made/.test(await warn()), 'cancel made a link, or does not say none was made');
  // the PDF is made in this browser: no warning, no link, no /share request (tools/check_ui.js try-pdf-* checks the file)
  await p.click('#try-pdf');
  await p.waitForTimeout(600);
  ok(!(await p.$('#try-share-out .try-share-warn')) && !proxyPosts(ctx, 'share').length && !shares.length, 'Download PDF asked for a share link, or warned as one');
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  ok(proxyPosts(ctx, 'share').length === 1 && shares.length === 1, proxyPosts(ctx, 'share').length + ' /share requests after the visitor agreed (want 1)');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  await p.close();
  // nothing kept: the link still warns, in general words (any report is built from the file), and is made on yes
  const c2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    p = await openTry(c2, { stubReport: optinReport(), proxy: 'set', proxyReply: optinReply() });
    await optinStep(p); await p.click('#try-pd-go');
    await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
    await p.click('#try-share');
    await tryUntil(p, '#try-share-out .try-share-warn');
    const w2 = ((await p.textContent('#try-share-out')) || '').replace(/\s+/g, ' ');
    ok(w2.indexOf(SHARE_WARNING_GENERAL) >= 0 && w2.indexOf(SHARE_WARNING) < 0 && !proxyPosts(c2, 'share').length,
      'with nothing kept the share step does not warn in general words first: ' + w2);
    await p.click('#try-share-yes');
    await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
    ok(proxyPosts(c2, 'share').length === 1, proxyPosts(c2, 'share').length + ' /share requests after yes (want 1)');
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  } finally { await c2.close(); }
});

check('try-share-body-names-no-file', DESK, async (ctx) => {
  const shares = [];
  const p = await openTry(ctx, { stubReport: optinReport(), proxy: 'set', proxyReply: optinReply(shares) });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  ok(/Dana Whitfield sold most in sample-messy\.csv/.test(await p.textContent('#try-ai-report .ai-rep-body')), 'the report on screen does not put the file\'s name back');
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  const B = proxyPosts(ctx, 'share').map((r) => r.body);
  ok(B.length === 1, B.length + ' /share requests');
  const b = JSON.parse(B[0]);
  ok(JSON.stringify(b.input) === JSON.stringify({ name: '[your file]' }), 'the share body\'s input is not "[your file]": ' + JSON.stringify(b.input));
  ok(JSON.stringify(b.kept) === '[]' && b.repaired === 0, 'with nothing kept the body does not say so ([]), or lacks the honesty count: ' + JSON.stringify({ kept: b.kept, repaired: b.repaired }));
  ok(!/sample-messy|orders\.csv/.test(B[0]), 'the share body names the file: ' + B[0].slice(0, 300));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

/* ------------------------------------------------------------ the AI report's PDF, made in this browser (29 Sep 2026)
   "Download PDF" writes the PDF here (src/js/45-report-pdf.js): a download, no /share request, bytes that start
   %PDF; the cover says which personal columns the visitor sent; the paper follows their pick (Letter or A4); the
   file's name is on it only when they tick the box. tools/check_report_pdf.mjs checks the file itself. */
const PDF_FX = path.join(SITE_DIR, 'tools', 'fixtures', 'report-pdf');
const pdfFixture = (f) => JSON.parse(fs.readFileSync(path.join(PDF_FX, f), 'utf8'));
// the words a PDF shows (its text operators, WinAnsi decoded), and its page size
function pdfWords(buf) {
  const s = Buffer.from(buf).toString('latin1'), map = { '\x91': "'", '\x92': "'", '\x93': '"', '\x94': '"', '\x96': '-', '\x97': '-', '\x95': '*', '\x85': '...' };
  const text = [...s.matchAll(/\(((?:[^()\\]|\\.)*)\) Tj/g)].map((m) => m[1].replace(/\\([()\\])/g, '$1').replace(/[\x85\x91-\x97]/g, (c) => map[c] || c)).join(' ');
  const mb = (s.match(/\/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]/) || []).slice(1).join('x');
  return { text, media: mb, head: s.slice(0, 5) };
}
// the stand-in proxy with the ship2 report in the new sections (its engine results, scenarios included, come from the worker)
function pdfReply(o) {
  const resp = pdfFixture('ship2-response-v2.json');
  return (b) => {
    if (typeof b.report === 'string' && 'days' in b) return { status: 200, json: { link: SHARE_LINK, delete_token: 'tok' } };
    if (b.profile) return { status: 200, json: { plan: OPTIN_PLAN } };
    if (b.results) return { status: 200, json: Object.assign({ report: resp.report, sources: resp.sources, model: resp.model, repaired: resp.repaired, removed_figures: resp.removed_figures }, o || {}) };
    return { status: 503, json: { error: 'x' } };
  };
}
function pdfReport() { const rep = optinReport(); rep.__resultsJson = pdfFixture('ship2-results-v2.json').results; return rep; }
async function downloadPdf(p) {
  const [dl] = await Promise.all([p.waitForEvent('download', { timeout: 20000 }), p.click('#try-pdf')]);
  return { name: dl.suggestedFilename(), bytes: fs.readFileSync(await dl.path()) };
}

check('try-pdf-downloads-in-this-browser-with-no-share-request', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
  await keptRun(p, ['staff_name']);
  const before = toProxy(ctx).length;
  const d = await downloadPdf(p);
  const w = pdfWords(d.bytes), today = await p.evaluate(() => { const t = new Date(), z = (n) => (n < 10 ? '0' : '') + n; return t.getFullYear() + '-' + z(t.getMonth() + 1) + '-' + z(t.getDate()); });
  ok(w.head === '%PDF-', 'the download is not a PDF: it starts ' + JSON.stringify(w.head));
  ok(d.name === 'NorthLedger report - ' + today + '.pdf', 'the download is named ' + JSON.stringify(d.name));
  ok(toProxy(ctx).length === before && !proxyPosts(ctx, 'share').length, 'Download PDF sent a request: ' + JSON.stringify(toProxy(ctx).slice(before).map((r) => r.url)));
  // the cover says the visitor sent a personal column, and names it
  ok(/PERSONAL COLUMNS/.test(w.text) && /Sent to the AI at the reader's choice: staff_name/.test(w.text), 'the cover does not say which personal columns were sent: ' + w.text.slice(0, 600));
  ok(/Page 1 of \d+/.test(w.text) && /EXECUTIVE SUMMARY/.test(w.text) && /PART 3 . SCENARIOS/.test(w.text) && /RUN RATE, A YEAR/.test(w.text), 'the PDF lacks its footer, sections or scenario cards');
  ok(!/sample-messy/.test(w.text) && !/sample-messy/.test(d.bytes.toString('latin1')), 'the PDF names the file without the visitor asking');
  ok(/Saved as .NorthLedger report/.test(await p.textContent('#try-share-out')) && /nothing was sent/.test(await p.textContent('#try-share-out')), 'the page does not say the PDF was saved and nothing sent');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { acceptDownloads: true });

check('try-pdf-paper-toggle-and-file-name-opt-in', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  // the default follows the browser's region (this context is en-US: Letter), and says it before the click
  ok(await p.isChecked('#try-ai-report input[name="try-pdf-paper"][value="letter"]'), 'Letter is not the default for an en-US browser');
  let d = await downloadPdf(p);
  ok(pdfWords(d.bytes).media === '612x792', 'the Letter PDF is ' + pdfWords(d.bytes).media + ' pt');
  await p.check('#try-ai-report input[name="try-pdf-paper"][value="a4"]');
  d = await downloadPdf(p);
  ok(pdfWords(d.bytes).media === '595.28x841.89', 'the A4 choice made a ' + pdfWords(d.bytes).media + ' pt PDF');
  ok(!/sample-messy/.test(d.bytes.toString('latin1')) && !/sample-messy/.test(d.name), 'without the tick the file name is on the PDF or its name');
  await p.check('#try-pdf-name');
  d = await downloadPdf(p);
  ok(/ - sample-messy\.pdf$/.test(d.name) && /sample-messy\.csv/.test(pdfWords(d.bytes).text), 'with the tick the file name is not on the PDF and its name: ' + d.name);
  ok(!toProxy(ctx).some((r) => /\/share$/.test(r.url)), 'a PDF made a /share request');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { acceptDownloads: true });

// an in-app browser (Instagram, Telegram...) ignores a download: the PDF opens in a tab instead, still with no request
check('try-pdf-in-an-in-app-browser-opens-the-file', DESK, async (ctx) => {
  const c = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce', acceptDownloads: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 350.0.0.0' });
  try {
    const p = await openTry(c, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
    await optinStep(p); await p.click('#try-pd-go');
    await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
    let downloaded = false; p.on('download', () => { downloaded = true; });
    const before = toProxy(c).length;
    const [pop] = await Promise.all([p.waitForEvent('popup', { timeout: 10000 }), p.click('#try-pdf')]);
    ok(/^blob:/.test(pop.url()) && !downloaded, 'the in-app path did not open the PDF itself: ' + pop.url());
    ok(/opened in a new tab/.test(await p.textContent('#try-share-out')) && await p.$('#try-share-out a[href^="blob:"]'), 'the page does not say the PDF opened, or offers no link to it');
    ok(toProxy(c).length === before, 'the in-app PDF sent a request');
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  } finally { await c.close(); }
});

// the honesty check's count: on the AI report card, and in the analyst view with the figures the sentences carried
check('try-ai-report-says-how-many-sentences-were-removed', DESK, async (ctx) => {
  const rep = fixture('sample'); rep.__profile = true; rep.__results = true;
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [], analyses: [] };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status: 200, json: { plan } }
    : { status: 200, json: { report: 'Rent rose in the East.\n## Executive summary\n- Rent rose.', sources: [], model: 'check', repaired: 2, removed_figures: ['1.8 times', '17'] } } });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden]), #try-msg:not([hidden])');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  const d = await p.evaluate(() => { const t = document.querySelector('#try-ai-report .ai-rep-trust'), a = document.getElementById('nl2-ai-audit');
    return { n: t && t.getAttribute('data-removed'), t: t ? t.textContent : '', a: a ? a.textContent : null, hidden: a ? a.hidden : true }; });
  ok(d.n === '2' && /Honesty check: 2 sentences removed/.test(d.t), 'the trust note does not lead with the count: ' + d.t);
  // a figure may be the engine's or a cited source's (the worker's guard): never "every figure matches the engine's own"
  const head = await p.textContent('#try-ai-report .ai-rep-head');
  // (integration pass, 1 Oct 2026: never "neither computed by the engine", which is false for an engine figure the guard
  // removed because its sentence counted it in other words)
  ok(/each carried a figure the check could not match to the engine's results or to a source cited in the same sentence\. Every figure left matches one or the other\./.test(d.t) &&
    !/neither computed|matches the engine|engine never computed/.test(d.t) &&
    /Figures by the engine or quoted from the sources it cites/.test(head), 'the trust note still says every figure is the engine\'s: ' + d.t + ' / ' + head);
  ok(!d.hidden && /2 sentences removed \(figures: 1\.8 times, 17\)/.test(d.a || ''), 'the analyst view does not say how many sentences were removed and their figures: ' + d.a);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the web searches /report may run (final review, 30 Sep 2026): exactly the adapter's list, built from fixed terms
// (engine/nl_browser.py _context_queries), always an array: [] when the engine sent none; never the plan's own words
check('try-report-sends-the-adapter-vetted-searches', DESK, async (ctx) => {
  const plan = Object.assign({}, OPTIN_PLAN, { context_queries: ['plan query one', 'plan query two'],
    context: [{ indicator: 'retail sales', region: 'Canada', years: [2025] }] });
  const bodies = [];
  const reply = (b) => {
    if (b.profile) return { status: 200, json: { plan } };
    if (b.results) { bodies.push(b); return { status: 200, json: { report: 'A report.', sources: [], model: 'check', repaired: 0 } }; }
    return { status: 503, json: { error: 'x' } };
  };
  for (const [cq, want] of [[[], []], [['retail sales Canada 2025'], ['retail sales Canada 2025']], [undefined, []], [null, []]]) {
    const rep = optinReport(); if (cq !== undefined) rep.__cq = cq;
    const c = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
    try {
      const p = await openTry(c, { stubReport: rep, proxy: 'set', proxyReply: reply });
      await optinStep(p); await p.click('#try-pd-go');
      await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
      const got = bodies[bodies.length - 1].context_queries;
      ok(Array.isArray(got) && JSON.stringify(got) === JSON.stringify(want), 'with the adapter\'s list ' + JSON.stringify(cq) + ' /report was sent ' + JSON.stringify(got) + ' (want ' + JSON.stringify(want) + ')');
      ok(JSON.stringify(bodies[bodies.length - 1]).indexOf('plan query') < 0, 'the plan\'s own search words went to /report');
      ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
    } finally { await c.close(); }
  }
});

// the new report sections read cleanly on screen, and "Scenarios" carries the engine's own cards
check('try-ai-report-new-sections-and-scenario-cards', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  const d = await p.evaluate(() => ({ k: Array.from(document.querySelectorAll('#try-ai-report .ai-sec-k')).map((e) => e.textContent),
    cards: Array.from(document.querySelectorAll('#try-ai-report .ai-scen-card')).map((e) => e.textContent.replace(/\s+/g, ' ')),
    order: Array.from(document.querySelectorAll('#try-ai-report h4.ai-sec')).map((e) => e.textContent.slice(0, 24)) }));
  ok(d.k.indexOf('The headline') === 0 && d.k.indexOf('What drove it') > 0 && d.k.indexOf('In the real world') > 0, 'the section labels are not shown: ' + JSON.stringify(d.k));
  ok(d.cards.length >= 3 && /Run rate, a year\s*152,214/i.test(d.cards[0]) && /from a WATCH change/.test(d.cards[0]), 'the scenario cards are missing or wrong: ' + JSON.stringify(d.cards.slice(0, 2)));
  const iS = d.order.findIndex((t) => /^Scenarios/.test(t)), iT = d.order.findIndex((t) => /^What to do/.test(t));
  ok(iS > 0 && iT > iS, 'Scenarios is not before What to do: ' + JSON.stringify(d.order));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

/* ------------------------------------------------------------ the final review of 30 Sep 2026: the AI report's page side
   A figure derived from a graded change (a run rate, a what-if) carries a neutral "from a CONFIRMED change", never the
   grade's own pill; a table the AI already placed in its scenarios is not drawn again; the engine's figures go under
   whatever heading the PDF calls scenarios; the Save-as-PDF cover says what went to the AI; a /share body carries what
   the shared PDF needs; a gallery entry's PDF and link carry that entry's own question. */
// the ship2 results with every derived item in the adapter's new form: grade null, its parent claim's grade as parent_grade
function parentGradeResults(grade) {
  const res = JSON.parse(JSON.stringify(pdfFixture('ship2-results-v2.json').results));
  res.scenarios.items.forEach((x) => { if (['contribution', 'price_volume_mix', 'per_unit', 'run_rate', 'sensitivity', 'gap'].indexOf(x.group) >= 0) { x.parent_grade = grade; x.grade = null; } });
  return res;
}
const GAP_MD = '| Region | Below the largest | Share gap | What if at the largest\'s rate per unit |\n|---|---|---|---|\n| West | 1 | 2 | 3 |';
check('try-ai-report-scenarios-derived-figures-and-no-repeated-table', DESK, async (ctx) => {
  const report = pdfFixture('ship2-response-v2.json').report.replace(/^## Scenarios$/m, '## Scenarios\n' + GAP_MD);
  const rep = optinReport(); rep.__resultsJson = parentGradeResults('CONFIRMED');
  let p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: pdfReply({ report }) });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  const d = await p.evaluate(() => {
    const C = Array.from(document.querySelectorAll('#try-ai-report .ai-scen-card'));
    return { n: C.length, pills: C.filter((c) => c.querySelector('.ai-scen-g')).length,
      from: C.map((c) => { const f = c.querySelector('.ai-scen-from'); return f ? f.textContent : ''; }),
      gaps: Array.from(document.querySelectorAll('#try-ai-report table')).filter((t) => Array.from(t.querySelectorAll('th')).some((th) => th.textContent.trim() === 'Below the largest')).length };
  });
  ok(d.n >= 3 && d.pills === 0 && d.from.every((t) => t === 'from a CONFIRMED change'), 'a derived figure wears a grade pill, or does not say "from a CONFIRMED change": ' + JSON.stringify(d));
  ok(d.gaps === 1, 'the gap table the AI placed in its scenarios is drawn ' + d.gaps + ' times');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  await p.close();
  // a scenarios section under another heading ("Outlook and what-ifs") still carries the engine's own cards, once
  const c2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    const r2 = pdfFixture('ship2-response-v2.json').report.replace(/^## Scenarios$/m, '## Outlook and what-ifs').replace(/^## What to do$/m, '## Scenario risks\n- One more.\n## What to do');
    p = await openTry(c2, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply({ report: r2 }) });
    await optinStep(p); await p.click('#try-pd-go');
    await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
    const e = await p.evaluate(() => ({ wraps: document.querySelectorAll('#try-ai-report .ai-scen-wrap').length, cards: document.querySelectorAll('#try-ai-report .ai-scen-card').length }));
    ok(e.wraps === 1 && e.cards >= 3, 'the engine\'s scenario figures are not under "Outlook and what-ifs" exactly once: ' + JSON.stringify(e));
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  } finally { await c2.close(); }
});

// the Save-as-PDF cover after a run with the AI: never "nothing was uploaded"; it names what went to the AI
check('try-save-as-pdf-cover-says-what-went-to-the-ai', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
  await keptRun(p, ['staff_name']);
  await p.evaluate(() => { window.print = () => { const c = document.querySelector('.tr-print-cover'); window.__cover = c ? c.textContent.replace(/\s+/g, ' ') : ''; }; });
  await p.click('#try-report [data-act="print"]');
  await p.waitForTimeout(150);
  const cov = await p.evaluate(() => window.__cover);
  ok(cov && !/nothing was uploaded/.test(cov), 'after a run with the AI the cover says nothing was uploaded: ' + cov);
  ok(/the file itself was never uploaded/.test(cov) && /Sent to the AI/.test(cov) && /summary of the file's columns \(never its rows\) and the engine's results went to an AI model/.test(cov) && /staff_name/.test(cov),
    'the cover does not say what went to the AI, or which personal column: ' + cov);
  ok(!/none by an AI/.test(cov), 'the cover says no AI was involved: ' + cov);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the /share body carries what the shared PDF is written from, and the writer, given it, never says it is missing
check('try-share-body-carries-what-the-shared-pdf-needs', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
  await keptRun(p, ['staff_name']);
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  const B = proxyPosts(ctx, 'share').map((r) => r.body);
  ok(B.length === 1, B.length + ' /share requests');
  const b = JSON.parse(B[0]), resp = pdfFixture('ship2-response-v2.json');
  ok(b.repaired === resp.repaired && JSON.stringify(b.kept) === JSON.stringify(['staff_name']), 'the body lacks the honesty check\'s count or the kept columns: ' + JSON.stringify({ repaired: b.repaired, kept: b.kept }));
  const R = b.results, bytes = Buffer.byteLength(JSON.stringify(R || null), 'utf8');
  ok(R && R.partial === true && R.scenarios && R.scenarios.items.length > 0 && R.scenarios.items.length <= 60 && bytes <= 20000 && !('charts' in R) && !('tables' in R),
    'the body\'s results are not the trimmed key figures and scenario items (' + bytes + ' bytes): ' + JSON.stringify(R).slice(0, 200));
  ok(!/sample-messy/.test(B[0]), 'the share body names the file');
  // the worker's shared PDF, from exactly these fields (insight-proxy: NLReportPdf.model(the stored share, shared: true))
  const W = require(path.join(SITE_DIR, 'src', 'js', '45-report-pdf.js'));
  const m = W.model({ report: b.report, sources: b.sources, model: b.model, goal: b.goal, charts: b.charts, tables: b.tables, results: b.results, kept: b.kept, repaired: b.repaired, shared: true, date: new Date(2026, 8, 30) });
  const w = pdfWords(W.build(m, { paper: 'letter' })).text;
  ok(!/were not kept|saved before the personal columns|saved before the engine|doesn.t carry the engine/.test(w), 'the shared PDF says its results or kept columns are missing although the body carries them');
  ok(/RUN RATE, A YEAR/.test(w) && /staff_name/.test(w) && /removed 2 sentences/.test(w), 'the shared PDF lacks the scenario cards, the kept column or the honesty count');
  // every item keeps its value (the worker checks an item's text against its value and drops one without)
  ok(R.scenarios.items.every((x) => (x.kind === 'date' ? typeof x.value === 'string' : typeof x.value === 'number' && isFinite(x.value))),
    'a shared scenario item has no value: ' + JSON.stringify(R.scenarios.items.filter((x) => typeof x.value !== 'number')[0] || null));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the /share body keeps under the page's cap, 171,000 bytes, about 10% under the worker's 190,000 (the chart registry's
// caps, 30 Sep 2026; it was 115,000 under 130,000): the engine's results give way first, trimmed to the room left; a
// report too large even without them is never sent, and the visitor reads why (integration pass, 30 Sep 2026)
const SHARE_CAP = 171000;
check('try-share-body-keeps-under-the-worker-cap', DESK, async (ctx) => {
  const resp = pdfFixture('ship2-response-v2.json'), W = require(path.join(SITE_DIR, 'src', 'js', '45-report-pdf.js'));
  const filler = (n) => 'The engine read the file in the browser and kept every figure it could check against its own totals. '.repeat(Math.ceil(n / 100) + 1).slice(0, n);
  const utf8 = (s) => Buffer.byteLength(s, 'utf8');
  // 1. over the cap even without the engine's results: no /share request, and a plain reason
  const F1 = 182000;
  let p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply({ report: resp.report + '\n' + filler(F1) }) });
  await keptRun(p, ['staff_name']);
  ok(await p.evaluate(() => window.NLTry.SHARE_BODY_MAX) === SHARE_CAP, 'the page\'s share cap is not 171,000 bytes');
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction(() => /too large for a link/.test(document.getElementById('try-share-out').textContent));
  const msg = (await p.textContent('#try-share-out')).trim();
  ok(proxyPosts(ctx, 'share').length === 0, 'a /share request was made for a body over the cap');
  const kb = Number(((msg.match(/too large for a link: ([\d,]+) KB/) || [])[1] || '0').replace(/,/g, ''));
  ok(/^The link could not be made\. This report is too large for a link: [\d,]+ KB, and a link holds at most 171 KB\. Nothing was sent\. Download the PDF to pass it on instead\.$/.test(msg) && kb > 171,
    'the visitor is not told why no link was made: ' + msg);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  await p.close();
  // 2. a body that fits only with the engine's results trimmed: about 8,000 bytes left for them (the body without them
  // is read from the first message, to the KB above)
  const base = kb * 1000 - F1 - 2, F2 = SHARE_CAP - 8000 - base - 2;
  const report2 = resp.report + '\n' + filler(F2);
  const c2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    p = await openTry(c2, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply({ report: report2 }) });
    await keptRun(p, ['staff_name']);
    await p.click('#try-share');
    await tryUntil(p, '#try-share-out .try-share-warn');
    await p.click('#try-share-yes');
    await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
    const B = proxyPosts(c2, 'share').map((r) => r.body);
    ok(B.length === 1, B.length + ' /share requests (want 1)');
    const res = pdfFixture('ship2-results-v2.json').results, b = JSON.parse(B[0]), full = W.shareResults(res);
    ok(utf8(B[0]) <= SHARE_CAP && b.report === report2, 'the /share body is ' + utf8(B[0]) + ' bytes, or its report was cut');
    // trimmed by the worker's priority to the room the body left (its cap less the body without them, the last key),
    // never from the tail (the contract test of 30 Sep 2026: the tail cut lost the run rate, the sensitivity and the
    // gaps): exactly the writer's own trim to that room, in the adapter's order, and no item of the full share left out
    // that would still have fit
    const got = b.results, kept = got && got.scenarios ? got.scenarios.items : [], ids = kept.map((x) => x.id);
    const room = SHARE_CAP - utf8(JSON.stringify(Object.assign({}, b, { results: undefined }))) - utf8(',"results":');
    const pos = ids.map((id) => res.scenarios.items.findIndex((x) => x.id === id));
    const fits = full.scenarios.items.filter((x) => ids.indexOf(x.id) < 0).filter((x) => {
      const g = JSON.parse(JSON.stringify(got)); g.scenarios.items = full.scenarios.items.filter((y) => y.id === x.id || ids.indexOf(y.id) >= 0);
      return utf8(JSON.stringify(g)) <= room;
    });
    ok(got && got.partial === true && ids.length > 0 && ids.length < full.scenarios.items.length && utf8(JSON.stringify(got)) <= room &&
      JSON.stringify(got) === JSON.stringify(W.shareResults(res, room)) && pos.every((x, i) => x >= 0 && (!i || x > pos[i - 1])) && !fits.length,
      'the engine\'s results were not trimmed by priority to the room left (' + room + ' bytes): ' + ids.length + ' of ' + full.scenarios.items.length +
      ' items' + (fits.length ? '; left out though they fit: ' + fits.map((x) => x.id).join(', ') : ''));
    ok(['run_rate', 'sensitivity'].every((g) => kept.some((x) => x.group === g)) && !kept.some((x) => x.group === 'facts' || (x.group === 'per_unit' && typeof x.segment === 'string')),
      'the room of about 8,000 bytes did not go to the run rate and the sensitivity before the facts and the segments\' figures per unit: ' + ids.join(', '));
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  } finally { await c2.close(); }
});

// the charts a link carries (the chart review, 30 Sep 2026): the worker keeps whole records from the start within 60,000
// bytes (insight-proxy/src/share.js SHARE_CHARTS_MAX_BYTES) and dropped the rest without a word; the page now keeps the
// same budget and says what the link leaves out. A record with a suppressed cell goes with no rows read (inputs.rows)
check('try-share-link-keeps-the-chart-budget-and-says-what-it-leaves-out', DESK, async (ctx) => {
  const utf8 = (s) => Buffer.byteLength(s, 'utf8');
  const ex = VIZ_SPEC.examples, big = ['edge_heatmap_diverging', 'heatmap', 'edge_heatmap_suppressed', 'edge_waterfall_12_long_labels', 'waterfall', 'edge_heatmap_diverging',
    'heatmap', 'edge_heatmap_diverging', 'heatmap', 'edge_heatmap_diverging'];
  // each record made about 2,500 bytes longer where no reader prints it (inputs.op), so ten of them pass the budget
  const charts = big.map((k, i) => { const r = Object.assign(vizCopy(ex[k].record), { id: 'viz.' + (i + 1) + '.' + ex[k].record.chart }); r.inputs = Object.assign({}, r.inputs, { op: 'x'.repeat(2500) }); return r; });
  const all = utf8(JSON.stringify(charts));
  ok(all > 60000, 'the charts are only ' + all + ' bytes: the check needs more than the budget');
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply({ charts }) });
  await keptRun(p, ['staff_name']);
  ok(await p.evaluate(() => window.NLTry.SHARE_CHARTS_MAX) === 60000, 'the page\'s chart budget is not the worker\'s 60,000 bytes');
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  const B = proxyPosts(ctx, 'share').map((r) => r.body), b = JSON.parse(B[0]), k = b.charts.length;
  ok(k > 0 && k < charts.length && utf8(JSON.stringify(b.charts)) <= 60000 && b.charts.every((c, i) => c.id === charts[i].id),
    'the link does not hold whole records from the start within 60,000 bytes: ' + k + ' records, ' + utf8(JSON.stringify(b.charts)) + ' bytes');
  const next = charts.slice(0, k + 1).map((c) => (c.suppressed && c.suppressed.cells > 0 ? Object.assign(vizCopy(c), { inputs: Object.assign({}, c.inputs, { rows: null }) }) : c));
  ok(utf8(JSON.stringify(next)) > 60000, 'a record that fits the budget was left out');
  const sup = b.charts.filter((c) => c.suppressed && c.suppressed.cells > 0);
  ok(sup.length > 0 && sup.every((c) => c.inputs.rows === null), 'a record with a suppressed cell went with its rows read: ' + JSON.stringify(sup.map((c) => c.inputs)));
  const note = (await p.textContent('#try-share-out')).replace(/\s+/g, ' ');
  ok(note.indexOf('The link holds ' + k + ' of the report\'s ' + charts.length + ' charts: a link keeps at most 60 KB of charts. The PDF has them all.') >= 0,
    'the visitor is not told what the link leaves out: ' + note.slice(-240));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// a report reopened from the gallery: its PDF and its link carry the question it answered, not the one on the page now
check('try-gallery-pdf-and-link-use-the-entry-s-own-question', DESK, async (ctx) => {
  const resp = pdfFixture('ship2-response-v2.json'), res = pdfFixture('ship2-results-v2.json').results, OLDQ = 'How did refunds move by channel last year?';
  const saved = {}; Object.keys(res).forEach((k) => { if (k !== 'charts' && k !== 'tables') saved[k] = res[k]; });
  const entry = { t: 1727700000000, title: 'An older report', goal: OLDQ, file: 'older.csv', model: 'check', report: 'An older report on refunds\n' + resp.report.split('\n').slice(1).join('\n'),
    sources: resp.sources, share: '', del: '', charts: res.charts.slice(0, 6), tables: res.tables.slice(0, 8), kept: [], results: saved, repaired: 1, removed_figures: [] };
  await ctx.addInitScript((e) => { try { localStorage.setItem('nl_try_reports_v1', JSON.stringify([e])); } catch (x) { /* the check below fails */ } }, entry);
  const p = await openTry(ctx, { stubReport: pdfReport(), proxy: 'set', proxyReply: pdfReply() });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  await p.waitForFunction(() => document.querySelectorAll('.try-prev-item').length === 2);
  const older = p.locator('.try-prev-item').nth(1);
  ok(/refunds/.test(await older.textContent()), 'the seeded report is not the second gallery entry');
  const [dl] = await Promise.all([p.waitForEvent('download', { timeout: 20000 }), older.locator('.pv-pdf').click()]);
  const text = pdfWords(fs.readFileSync(await dl.path())).text;
  ok(text.indexOf(OLDQ) >= 0 && text.indexOf(OPTIN_PLAN.goal) < 0, 'the reopened report\'s PDF does not ask its own question: ' + text.slice(0, 400));
  await older.locator('.pv-share').click();
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  const B = proxyPosts(ctx, 'share').map((r) => JSON.parse(r.body));
  ok(B.length === 1 && B[0].goal === OLDQ && /^An older report/.test(B[0].report), 'the reopened report\'s link does not carry its own question: ' + JSON.stringify(B.map((x) => x.goal)));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { acceptDownloads: true });

// on a phone the consent box sits right after the Keep choices, and a polite live note says it appeared
check('try-optin-phone-consent-box-follows-the-choices-and-is-announced', PHONE, async (ctx) => {
  const p = await openTry(ctx, { stubReport: optinReport(), proxy: 'set', proxyReply: optinReply() });
  await optinStep(p);
  await p.check('#try-pd input[data-col="staff_name"][value="keep"]');
  const d = await p.evaluate(() => {
    const box = document.getElementById('try-pd-send'), fs = Array.from(document.querySelectorAll('#try-pd fieldset.pd-col')), last = fs[fs.length - 1];
    const said = document.getElementById('try-pd-send-said'), keep = document.querySelector('#try-pd input[data-col="staff_name"][value="keep"]');
    return { after: box && box.previousElementSibling === last, gap: box.getBoundingClientRect().top - last.getBoundingClientRect().bottom,
      live: said && said.getAttribute('aria-live'), said: said ? said.textContent : '', dist: box.getBoundingClientRect().top - keep.getBoundingClientRect().top, vh: innerHeight };
  });
  ok(d.after && d.gap < 40, 'the consent box does not follow the Keep choices: ' + JSON.stringify(d));
  ok(d.dist < d.vh, 'the consent box is more than a screen below the Keep choice (' + Math.round(d.dist) + ' px)');
  ok(d.live === 'polite' && /box to tick/.test(d.said) && /staff_name/.test(d.said), 'the box is not announced: ' + JSON.stringify(d.said));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { mobile: true });

// a column coded as it arrived (an email or phone number) cannot be kept: the plan's profile reads Keep as Code
check('try-plan-profile-honours-coded-as-it-arrived', DESK, async (ctx) => {
  const p = await openTry(ctx, {});
  const got = await p.evaluate(() => {
    const prof = { ok: true, name: 'f.csv', rows: 10, columns_total: 2, columns: [
      { name: 'order_date', filled: 10, distinct: 10, numeric_share: 0, date_share: 1, looks_personal: false },
      { name: 'customer_email', filled: 10, distinct: 8, numeric_share: 0, date_share: 0, top_values: ['a@example.org'], values: ['a@example.org'], looks_personal: true, privacy_flag: 'email; coded as it arrived' }] };
    const fl = [{ column: 'customer_email', kind: 'email; coded as it arrived' }];
    return { out: window.NLTry.planProfile(prof, fl, { customer_email: 'keep' }), wh: window.NLTry.planWithheld(prof, fl, { customer_email: 'keep' }) };
  });
  const col = got.out.columns.filter((c) => c.name === 'customer_email')[0];
  ok(col && col.looks_personal === true && !('values' in col) && !('top_values' in col), 'a column coded as it arrived went as kept, values included: ' + JSON.stringify(col));
  ok(!got.wh.length, 'it is withheld instead of coded: ' + JSON.stringify(got.wh));
});

check('try-optin-a-new-file-resets-the-choices-and-the-tick', DESK, async (ctx) => {
  const p = await openTry(ctx, { stubReport: optinReport(), proxy: 'set', proxyReply: optinReply() });
  await keptRun(p, ['staff_name']);
  const before = toProxy(ctx).length;
  await optinStep(p);                                  // the next file
  const vals = await p.evaluate(() => Array.from(document.querySelectorAll('#try-pd input[type=radio]:checked')).map((x) => x.value));
  ok(vals.length === 3 && vals.every((v) => v === 'withhold'), 'a new file keeps the last file\'s choices: ' + JSON.stringify(vals));
  ok(!(await p.isVisible('#try-pd-send-ok')) && !(await p.isDisabled('#try-pd-go')), 'a new file shows the last file\'s box, or keeps Continue with the AI off');
  ok(!(await p.isVisible('#try-ai-report')), 'the last file\'s AI report is still on screen beside the new file');
  await p.check('#try-pd input[data-col="staff_name"][value="keep"]');
  ok(await p.isVisible('#try-pd-send-ok') && !(await p.isChecked('#try-pd-send-ok')) && await p.isDisabled('#try-pd-go'), 'the last file\'s tick carried over');
  await p.check('#try-pd input[data-col="staff_name"][value="withhold"]');
  await p.click('#try-pd-noai');
  await tryUntil(p, '#try-report:not([hidden])');
  await p.waitForTimeout(300);
  ok(toProxy(ctx).length === before, 'the new file sent a request with no AI chosen');
  ok(!/You chose to send/.test(await p.evaluate(() => document.getElementById('try').innerText)), 'the last file\'s opt-in record shows on the new file');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-personal-data-step-says-what-each-choice-does', DESK, async (ctx) => {
  const rep = stubReport();
  rep.privacy.flagged = [{ column: 'customer_email', kind: 'email; coded as it arrived', decision: 'withhold' }, { column: 'notes', kind: 'free text', decision: 'withhold' }];
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden])');
  const t = await p.textContent('#try-pd');
  ok(!/before any analysis|counts still work/.test(t), 'the choices still promise what the engine does not do');
  ok(/data-health findings on this page still name it and count its empty cells/.test(t) && !/blanks and spellings/.test(t) &&
    /No cleaning rule reads or changes them, so they never set a row aside; they only keep otherwise-identical rows apart, as they are in your file/.test(t) && /never sent to an AI or put in a share link, not even its name/.test(t) &&
    /the analyses leave the column out, and an AI is told only its name, type and counts, never its values or range/.test(t) && /used like any other column/.test(t) && /neutral heading \(a stylist or a vendor, say\)/.test(t),
    'the choices do not say what happens: ' + t.slice(0, 400));
  ok(!/Whatever you pick, it stays in your browser/.test(t), 'the step still says a choice keeps the column in the browser (a kept or coded column\'s summary can go to the AI)');
  ok(await p.isDisabled('#try-pd input[data-col="customer_email"][value="keep"]') && !(await p.isDisabled('#try-pd input[data-col="notes"][value="keep"]')),
    'Keep is offered for a column coded as it arrived (or refused for one that was not)');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  const priv = await p.textContent('.tr-priv');
  ok(/data-health findings still name the column and count its empty cells, never showing a value/.test(priv) &&
    /no cleaning rule reads or changes them, so they never set a row aside, and they only keep otherwise-identical rows apart, as they are in your file/.test(priv) && /never sent to an AI or put in a share link, not even its name/.test(priv) &&
    /an AI is told only its name, type and counts/.test(priv) && !/dropped before analysis|left out of the business analysis, the story/.test(priv), 'the report\'s personal-data note overclaims or misses the promise: ' + priv.slice(0, 400));
});

// a category the engine read as a category, not personal data (privacy.released; the lead's amendment AM1, WAVE 4): the
// consent step shows it in those words with its labels counted, Use is the default and Withhold is still on offer; a
// withhold reaches the engine's run, and Use sends no choice (the engine's own reading)
function releasedReport() {
  const rep = stubReport();
  rep.privacy = { flagged: [], released: [{ column: 'industry_segment', header: 'Industry segment', distinct: 30, rows: 1000,
    text: 'Read as a category, not personal data: Industry segment (30 labels)' }] };
  rep.__echoDecisions = true;
  return rep;
}
for (const pick of ['withhold', 'use']) {
  check('try-released-category-is-shown-and-' + (pick === 'withhold' ? 'can-be-withheld' : 'use-sends-no-choice'), DESK, async (ctx) => {
    const p = await openTry(ctx, { stubReport: releasedReport(), proxy: 'unset' });
    await p.click('#try-sample');
    await tryUntil(p, '#try-pd:not([hidden])');
    const t = squash(await p.textContent('#try-pd'));
    ok(/Read as a category, not personal data: Industry segment \(30 labels\)/.test(t), 'the released column is not shown in the AM1 words: ' + t.slice(0, 300));
    ok(/No column was flagged as personal/.test(t), 'the step speaks of flagged columns when none was flagged: ' + t.slice(0, 200));
    ok(await p.isChecked('#try-pd input[data-col="industry_segment"][value="use"]'), 'Use is not the default for a released category');
    ok(await p.isVisible('#try-pd input[data-col="industry_segment"][value="withhold"]'), 'Withhold is not on offer for a released category');
    if (pick === 'withhold') await p.check('#try-pd input[data-col="industry_segment"][value="withhold"]');
    await p.click('#try-pd-go');
    await tryUntil(p, '#try-report:not([hidden])');
    const shown = await p.evaluate(() => document.getElementById('try-report').innerText);
    const want = pick === 'withhold' ? 'decisions {"industry_segment":"withhold"}' : 'decisions {}';
    ok(shown.indexOf(want) >= 0, 'the run did not receive the visitor\'s choice (' + pick + '): ' + shown.slice(0, 200));
    const key = await p.evaluate(() => [window.NLTry.planKeyText('ab12', { industry_segment: 'withhold' }, [{ column: 'industry_segment' }]),
      window.NLTry.planKeyText('ab12', {}, [{ column: 'industry_segment' }])]);
    ok(key[0] === 'ab12\nindustry_segment=withhold' && key[1] === '', 'a withheld released category does not change the plan key: ' + JSON.stringify(key));
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  });
}

// wave 5e (P10): the table's own value column, read as its measure and not as a national ID number (a count of nine or more digits): the
// consent step says so in its own words, Use is the default, Withhold is still on offer (and the table then cannot be read), and the report's
// method and data-quality section carries the same line under a heading that names the measure
function releasedMeasureReport() {
  const rep = stubReport();
  rep.privacy = { flagged: [], released: [{ column: 'value', header: 'VALUE', kind: 'measure', distinct: 640, rows: 6400, min_repeat: 1,
    text: 'Read as the table\'s measure, not personal data: VALUE' }] };
  rep.__echoDecisions = true;
  return rep;
}
for (const pick of ['withhold', 'use']) {
  check('try-released-value-column-is-shown-as-the-measure-and-' + (pick === 'withhold' ? 'can-be-withheld' : 'use-sends-no-choice'), DESK, async (ctx) => {
    const p = await openTry(ctx, { stubReport: releasedMeasureReport(), proxy: 'unset' });
    await p.click('#try-sample');
    await tryUntil(p, '#try-pd:not([hidden])');
    const t = squash(await p.textContent('#try-pd'));
    ok(/Read as the table's measure, not personal data: VALUE/.test(t), 'the value column is not shown in the measure words: ' + t.slice(0, 300));
    ok(!/Read as a category, not personal data: VALUE/.test(t) && !/short list of labels/.test(t), 'the value column is called a category: ' + t.slice(0, 300));
    ok(/holds the figures it publishes/.test(t) && /You can still withhold it/.test(t), 'the step does not say what the column is and that it can be withheld: ' + t.slice(0, 400));
    ok(await p.isChecked('#try-pd input[data-col="value"][value="use"]'), 'Use is not the default for the value column');
    ok(await p.isVisible('#try-pd input[data-col="value"][value="withhold"]'), 'Withhold is not on offer for the value column');
    if (pick === 'withhold') await p.check('#try-pd input[data-col="value"][value="withhold"]');
    await p.click('#try-pd-go');
    await tryUntil(p, '#try-report:not([hidden])');
    const shown = await p.evaluate(() => document.getElementById('try-report').innerText);
    const want = pick === 'withhold' ? 'decisions {"value":"withhold"}' : 'decisions {}';
    ok(shown.indexOf(want) >= 0, 'the run did not receive the visitor\'s choice (' + pick + '): ' + shown.slice(0, 200));
    const d = await p.evaluate(() => { const l = document.querySelector('#try-report [data-released="1"]'); const h = l && l.previousElementSibling;
      return { txt: l ? l.textContent.replace(/\s+/g, ' ').trim() : '', head: h ? h.textContent : '' }; });
    ok(d.txt.indexOf('Read as the table\'s measure, not personal data: VALUE') >= 0 && /measure/.test(d.head), 'the report\'s data section does not carry the measure line under a heading that names it: ' + JSON.stringify(d));
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  });
}

check('try-save-as-pdf-prints-every-finding-and-the-email', DESK, async (ctx) => {
  const rep = stubReport();
  for (let i = 0; i < 10; i++) rep.findings.push({ id: 'dq.extra' + i, claim: 'Extra check ' + (i + 1) + ' of 10.', verdict: 'INSUFFICIENT', why: 'Made up for the check.', kind: 'data_quality', value: i });
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runStub(p);
  await p.evaluate(() => { window.print = () => { const R = document.getElementById('try-report');
    window.__printed = { cls: document.body.classList.contains('print-try'), hidden: R.querySelectorAll('.tr-flist li[hidden]').length,
      shown: Array.from(R.querySelectorAll('.tr-flist li')).filter((li) => !li.hidden).length, closed: R.querySelectorAll('details:not([open])').length }; }; });
  await p.click('#try-report [data-act="print"]');
  await p.waitForTimeout(100);
  const pr = await p.evaluate(() => window.__printed);
  ok(pr && pr.cls && pr.hidden === 0 && pr.shown === rep.findings.length && pr.closed === 0, 'while printing: ' + JSON.stringify(pr));
  const after = await p.evaluate(() => ({ cls: document.body.classList.contains('print-try'), hidden: document.querySelectorAll('#try-report .tr-flist li[hidden]').length }));
  ok(!after.cls && after.hidden === rep.findings.length - 8, 'the page did not go back after printing: ' + JSON.stringify(after));
  await p.evaluate(() => document.body.classList.add('print-try'));
  await p.emulateMedia({ media: 'print' });
  const css = await p.evaluate(() => { const a = document.querySelector('.tr-cta a[data-email]'), m = document.querySelector('.tr-more');
    return { after: a ? getComputedStyle(a, '::after').content : '', more: m ? getComputedStyle(m).display : 'none', meter: getComputedStyle(document.querySelector('.tr-meter i')).printColorAdjust }; });
  ok(css.after.indexOf(await p.evaluate(() => window.NL.try.contact_email)) >= 0, 'the printed call to action carries no email address: ' + css.after);
  ok(css.more === 'none' && css.meter === 'exact', 'print shows the "Show all" button or drops the health meter: ' + JSON.stringify(css));
});

check('try-downloads-and-chart-say-what-they-hold', DESK, async (ctx) => {
  const rep = stubReport();
  rep.downloads.clean_csv = 'source_line,order_date,region,amount\n2,2025-01-02,East,10.5\n';
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runStub(p);
  const dl = await p.textContent('.tr-dl .note');
  ok(/source_line, its line in your file/.test(dl) && /Withheld columns \(customer_email\) are left out/.test(dl), 'the downloads note does not explain the files: ' + dl);
  ok(/monthly rows/.test(await p.textContent('.tr-fc figcaption')), 'the chart does not say what its series is');
  ok(/How to read the terms/.test(await p.textContent('.tr-find')), 'no glossary for p, effect size and MASE');
  ok(/What the cleaning did/.test(await p.textContent('.tr-fixes h3')) && /Read as/.test(await p.textContent('.tr-fixes .note')), 'the fixes card calls type conversions repairs');
});

check('try-report-fits-a-phone', PHONE, async (ctx) => {
  const rep = stubReport();
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runStub(p);
  const r = await p.evaluate(() => {
    const W = document.documentElement.clientWidth;
    const small = Array.from(document.querySelectorAll('#try button, #try .btn, #try .pd-opt')).filter((e) => e.offsetParent !== null)
      .map((e) => ({ t: (e.textContent || '').trim().slice(0, 24), h: e.getBoundingClientRect().height })).filter((x) => x.h < 43.5);
    const fixRow = document.querySelector('.tr-fixes tbody tr');
    return { sideways: document.documentElement.scrollWidth - W, small, fixCue: !!(document.querySelector('.tr-fixes .swipe-cue:not([hidden])')),
      fixTall: fixRow ? fixRow.getBoundingClientRect().height : 0 };
  });
  ok(r.sideways <= 0, 'the page scrolls sideways by ' + r.sideways + ' px with a report on a phone');
  ok(!r.small.length, 'touch targets under 44 px: ' + r.small.slice(0, 3).map((x) => x.t + ' ' + Math.round(x.h)).join(', '));
  ok(!r.fixCue && r.fixTall < 140, 'the fixes table does not stack on a phone (sideways cue ' + r.fixCue + ', first row ' + Math.round(r.fixTall) + ' px tall)');
}, { mobile: true });

// The plan card's "Data tests" table (integration pass, 30 Sep 2026): five columns of the engine's words (a long column
// name, examples, the action) pushed a phone's page 86 to 149 px sideways. The table sits in the page's sideways
// scroller (.tscroll: a named region with its cue), so from 320 to 390 px the page itself never scrolls sideways.
check('try-plan-data-tests-fit-a-phone', PHONE, async (ctx) => {
  const rep = stubReport(); rep.__profile = true; rep.__results = true;
  const T = (column, test, checked, failed, examples, action) => ({ column, semantic_type: 'x', test, checked, failed, examples, action,
    unreadable: 0, out_of_range: failed, repeated: 0, unexpected: 0, misread: false, signal: false });
  rep.__contracts = { tests: [
    T('shipping_postal_code_region', 'a code of the same shape on every row', 1231, 44, ['M5V-3L9-XX', 'K1A0B1ZZZZ', 'H2X_1Y4_000'], 'kept by the engine; the tests changed no value'),
    T('amount_before_discount_cad', 'between 0 and 100 (or 0 and 1) (percentage)', 1231, 40, ['140.25', '155.50', '-3.75'], '40 are outside 0 to 100: kept by the engine; the tests changed no value'),
    T('order_date', 'a date that can be read', 1231, 0, [], 'passed')], cells_flagged: 84, line: 'source_line', note: 'The tests read every value and changed none.' };
  const plan = { goal: 'How has order value changed?', understanding: 'Orders.', quality_risks: [], operations: [], primary: 'amount',
    analyses: [{ type: 'trend', columns: ['amount'] }], columns: [{ name: 'order_date', semantic_type: 'date', role: 'date' }, { name: 'amount', semantic_type: 'flow_amount', role: 'target' }] };
  const reply = (b) => b.profile ? { status: 200, json: { plan } } : { status: 503, json: { error: 'x' } };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: reply });
  await p.click('#try-sample'); await tryUntil(p, '#try-pd:not([hidden])');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-plan-card table.try-contracts');
  await tryUntil(p, '#try-report:not([hidden])');
  let wider = 0;
  for (const w of [320, 360, 375, 390]) {
    await p.setViewportSize({ width: w, height: 844 });
    await p.waitForTimeout(260);                         // the page's resize handler marks the scrollers after 120 ms
    const r = await p.evaluate(() => {
      const t = document.querySelector('#try-plan-card table.try-contracts'), s = t && t.closest('.tscroll');
      const cue = s && s.previousElementSibling && s.previousElementSibling.classList.contains('swipe-cue') ? s.previousElementSibling : null;
      const card = document.getElementById('try-plan-card').getBoundingClientRect();
      return { W: document.documentElement.clientWidth, sideways: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        wrapped: !!s, role: s && s.getAttribute('role'), label: s && s.getAttribute('aria-label'), tab: s && s.getAttribute('tabindex'),
        more: s ? s.scrollWidth - s.clientWidth : null, cue: !!(cue && !cue.hidden), can: !!(s && s.classList.contains('can-scroll')),
        inCard: s ? s.getBoundingClientRect().right <= card.right + 0.5 : false };
    });
    ok(r.sideways <= 0, 'at ' + w + ' px the plan card\'s Data tests push the page ' + r.sideways + ' px sideways');
    ok(r.wrapped && r.role === 'region' && /Data tests/.test(r.label || '') && r.tab === '0', 'at ' + w + ' px the Data tests table is not in a named, focusable sideways scroller: ' + JSON.stringify(r));
    ok(r.inCard, 'at ' + w + ' px the Data tests scroller runs past the plan card');
    if (r.more > 2) { wider++; ok(r.cue && r.can, 'at ' + w + ' px the Data tests table scrolls sideways without its cue: ' + JSON.stringify(r)); }
  }
  ok(wider > 0, 'the Data tests table fits every phone width, so this check proves nothing about the scroller');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
}, { mobile: true });

check('try-sample-through-the-real-engine', DESK, async (ctx) => {
  const why = await cdnReachable();
  if (why) throw new Skip(why + ', so the real engine cannot load');
  const p = await openTry(ctx, { proxy: 'unset' });
  await p.evaluate(() => { const W = window.Worker; window.__rep = null;
    window.Worker = function (u, o) { const w = new W(u, o); w.addEventListener('message', (e) => { if (e.data && e.data.type === 'result') window.__rep = e.data.report; }); return w; };
    window.Worker.prototype = W.prototype; });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden]), #try-msg:not([hidden])', 240000);
  ok(await p.isVisible('#try-pd'), 'the sample did not reach the personal-data step: ' + (await p.textContent('#try-msg')));
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden]), #try-msg:not([hidden])', 240000);
  const rep = await p.evaluate(() => window.__rep);
  ok(rep && rep.ok && await p.isVisible('#try-report'), 'no report from the real engine: ' + (await p.textContent('#try-msg')));
  ok(!(await p.evaluate((r) => window.NLTry.validate(r), rep)).length, 'the engine report breaks THE REPORT CONTRACT');
  const pack = JSON.parse(fs.readFileSync(path.join(SITE_DIR, 'engine', 'pack.json'), 'utf8'));
  ok(rep.engine.snapshot === pack.engine_snapshot && rep.input.sha256 === pack.sample.sha256, 'engine snapshot or sample fingerprint differs from engine/pack.json');
  if (rep.contract_version === 2 && rep.charts.length) await managerMatches(p, rep);   // report v2: the manager view
  else await screenMatchesReport(p, rep);
  for (const k of ['clean_csv', 'quarantine_csv', 'ledger_json']) {
    const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#try-report [data-dl="' + k + '"]')]);
    const text = fs.readFileSync(await dl.path(), 'utf8');
    ok(text === rep.downloads[k], k + ' download differs from the engine output');
    if (k === 'ledger_json') JSON.parse(text);
    else {
      const lines = text.split(/\r?\n/).filter((l) => l);
      ok(lines.length - 1 === (k === 'clean_csv' ? rep.cleaning.rows_clean : rep.cleaning.rows_quarantined), k + ' has ' + (lines.length - 1) + ' rows, the report says otherwise');
      ok(!/(^|,)notes(,|$)/i.test(lines[0]), 'the withheld notes column is in ' + k);
    }
  }
  const other = ctx.__reqs.filter((r) => !/^(blob|data):/.test(r.url) && ['nl-site.test', 'cdn.jsdelivr.net'].indexOf(new URL(r.url).host) < 0);
  ok(!other.length, 'a request went to ' + (other[0] || {}).url);
  ok(!ctx.__reqs.some((r) => r.body), 'a request carried a body');
}, { acceptDownloads: true });

// The column summary for /plan made by the real engine in Pyodide AFTER the visitor's choice (engine/worker.js
// "profile" -> nl_browser.plan_profile_json): the sample's flagged Notes, coded, goes with only its name, type
// and counts and looks_personal: true, no note reaches /plan, and the unflagged columns go as the engine read
// them. The stand-in proxy answers busy, so the run goes on with the engine's own rules.
check('try-sample-profile-through-the-real-engine-after-the-choices', DESK, async (ctx) => {
  const why = await cdnReachable();
  if (why) throw new Skip(why + ', so the real engine cannot load');
  const p = await openTry(ctx, { proxy: 'set', proxyReply: () => ({ status: 429, json: { error: 'busy' } }) });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden]), #try-msg:not([hidden])', 240000);
  ok(await p.isVisible('#try-pd'), 'the sample did not reach the personal-data step: ' + (await p.textContent('#try-msg')));
  ok(!ctx.__reqs.some((r) => r.url.indexOf(PROXY_URL) === 0), 'a request reached the proxy before the visitor chose');
  await p.check('#try-pd input[data-col="notes"][value="code"]');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden]), #try-msg:not([hidden])', 240000);
  ok(await p.isVisible('#try-report'), 'no report from the real engine: ' + (await p.textContent('#try-msg')));
  const bodies = ctx.__reqs.filter((r) => r.url === PROXY_URL + 'plan' && r.method === 'POST').map((r) => r.body);
  ok(bodies.length === 1, bodies.length + ' /plan requests (want 1)');
  const pr = JSON.parse(bodies[0]).profile, names = pr.columns.map((c) => c.name);
  const notes = pr.columns.filter((c) => c.name === 'Notes')[0];
  ok(notes && notes.looks_personal === true && typeof notes.privacy_flag === 'string' && Object.keys(notes).every((k) => PERSONAL_KEEP_UI.indexOf(k) >= 0),
    'the coded Notes goes with more than its name, type and counts, or not at all: ' + JSON.stringify(notes));
  ok(['Date', 'Property', 'Amount'].every((n) => names.indexOf(n) >= 0), 'the unflagged columns sent: ' + JSON.stringify(names));
  const prop = pr.columns.filter((c) => c.name === 'Property')[0];
  ok(prop && (prop.top_values || []).length && prop.looks_personal === false, 'an unflagged column lost its values: ' + JSON.stringify(prop));
  const csv = fs.readFileSync(path.join(SITE_DIR, 'engine', 'sample-messy.csv'), 'utf8').split(/\r?\n/).slice(1);
  // each line's last field, the note (a quoted note with a comma leaves its tail, still a note's words); two words
  // or more, so a note is never mistaken for a one-word value of another column
  const vals = Array.from(new Set(csv.map((l) => { const m = l.match(/,([^,]*)$/); return m ? m[1].trim().replace(/^"|"$/g, '') : ''; }).filter((v) => v.indexOf(' ') > 0)));
  ok(vals.length > 3, 'the sample lost its notes');
  const leak = vals.filter((v) => bodies[0].indexOf(v) >= 0);
  ok(!leak.length, '/plan carries a note: ' + JSON.stringify(leak.slice(0, 3)));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

/* ------------------------------------------------------------ report v2: the manager and analyst views
   The engine's report contract v2 (engine/CONTRACT-v2.md) drawn as two views from one report (plan §4.1)
   with the §5 charts. These checks feed the page, through the stand-in worker, reports the adapter
   (engine/nl_browser.py) wrote natively for the site's sample and for four invented business files
   (tools/make_ui_fixtures.py: a rent roll, a sales ledger with categories, a web analytics export and
   an 18-month file), then compare what the page shows with the report, number by number. */
const PY = process.env.PY || path.join(SITE_DIR, '..', 'agent-demo', 'venv', 'bin', 'python');
const FX_NAMES = ['sample', 'rent-roll', 'sales-ledger', 'web-analytics', 'cafe-invoices-18m', 'rent-roll-gated', 'shop-margins-36m'];
// a statistical table read by its structure (wave 4, track B): only its report is read by the checks (no profile or landed files)
const FX_STRUCT = ['official-cube.json', 'wave5-no-total.json', 'wave5-quarterly.json', 'wave5b-one-member.json', 'wave5b-refused.json', 'wave5c-named-total.json'];
// every file the checks read: each report, the profile the page would send /plan for it, and the planned
// orders-private run (its CSV too, for the personal values that must never show)
const FX_FILES = FX_NAMES.concat(['orders-private']).map((n) => n + '.json').concat(FX_NAMES.concat(['orders-private']).map((n) => n + '.profile.json'),
  FX_NAMES.concat(['orders-private']).map((n) => n + '.landed.json'), ['orders-private.csv', 'sales-ledger-charts.json', 'viz-hostile-header.json',
    'reviews-plan-drop.json']).concat(FX_STRUCT);
let FX_DIR = null;
function fixtureProfile(name) { fixture(name); return JSON.parse(fs.readFileSync(path.join(FX_DIR, name + '.profile.json'), 'utf8')); }
function fixtureLanded(name) { fixture(name); return JSON.parse(fs.readFileSync(path.join(FX_DIR, name + '.landed.json'), 'utf8')); }
function fixture(name) {
  if (!FX_DIR) {
    const dir = process.env.NL_UI_FIXTURES || fs.mkdtempSync(path.join(require('os').tmpdir(), 'nl-ui-fx-'));
    if (!FX_FILES.every((n) => fs.existsSync(path.join(dir, n)))) {
      const py = fs.existsSync(PY) ? PY : 'python3';
      const r = require('child_process').spawnSync(py, [path.join(SITE_DIR, 'tools', 'make_ui_fixtures.py'), '--out', dir], { encoding: 'utf8' });
      if (r.status !== 0) throw new Skip('the report fixtures could not be made with ' + py + ' (' + String(r.stderr || r.error || '').trim().split('\n').pop() + ')');
    }
    FX_DIR = dir;
  }
  return JSON.parse(fs.readFileSync(path.join(FX_DIR, name + '.json'), 'utf8'));
}
async function runReport(p) {
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden]), #try-report:not([hidden]), #try-msg:not([hidden])');
  if (await p.isVisible('#try-pd')) { await p.click('#try-pd-go'); await tryUntil(p, '#try-report:not([hidden]), #try-msg:not([hidden])'); }
  ok(await p.isVisible('#try-report'), 'no report: ' + (await p.textContent('#try-msg')));
}
async function openV2(ctx, name, rep) {
  rep = rep || fixture(name);
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runReport(p);
  return { p, rep };
}
// the contract's own numbers written the way a reader reads them (an independent copy of the page's rules)
const sgn1 = (x) => { const s = Math.abs(x).toFixed(1); return s === '0.0' ? '0.0' : (x > 0 ? '+' : '-') + s; };
const COUNTY = ['rows', 'count', 'months'];
function fxVal(v, scale, unit) {
  if (v === null || v === undefined) return 'n/a';
  if (scale === 'difference') { const a = fmt(Math.abs(v), 3); return a === '0' ? '0' : (v > 0 ? '+' : '-') + a; }   // own units, never a percentage
  if (scale === 'fraction') return sgn1(unit === 'pct' ? v : v * 100) + '%';
  if (scale === 'pct') return fmt(v, 2) + '%';
  if (scale === 'money') return fmt(v, 0);   // money totals and their forecasts: whole units (review of the site, 24 Sep 2026)
  if (COUNTY.indexOf(scale) >= 0) return fmt(Math.round(v), 0);
  return fmt(v);
}
// one value of a chart's series as its table prints it: whole units when the chart's data say money
const fxSeries = (v, scale) => scale === 'money' ? fmt(v, 0) : fmt(v);
const fxCi = (ci, scale) => (!ci || ci[0] === null || ci[1] === null) ? null : fxVal(ci[0], scale) + ' to ' + fxVal(ci[1], scale);
const GRADE_WORD = { CONFIRMED: 'CONFIRMED', WATCH: 'WATCH', NOT_ENOUGH_DATA: 'NOT ENOUGH DATA' };
const GRADE_GLYPH = { CONFIRMED: '✓', WATCH: '◐', NOT_ENOUGH_DATA: '–' };
// a forecast's grade reads in its own words: the method is usable for planning, a point is not "confirmed"
const FC_WORD = { CONFIRMED: 'USABLE FOR PLANNING', WATCH: 'NOT YET SHOWN USABLE', NOT_ENOUGH_DATA: 'NOT ENOUGH DATA' };
const gradeWord = (g, kind) => kind === 'forecast' ? FC_WORD[g] : GRADE_WORD[g];
// what the page prints for a finding's effect: its note when it has no value (a forecast not offered),
// else the value in its scale, with the measure's unit for a difference
const fxEffect = (e) => (e.estimate === null || e.estimate === undefined) && e.note ? e.note : fxVal(e.estimate, e.scale) + (e.scale === 'difference' && e.unit ? ' ' + e.unit : '');
// strength of evidence: CONFIRMED, WATCH that moved (or cleared the bar), other WATCH, NOT ENOUGH DATA
// (a claim that stepped where what the file covers changed is not a movement of the business: fixer round, 24 Sep 2026)
const strengthOf = (f) => f.grade === 'CONFIRMED' ? 0 : f.grade === 'WATCH' ? ((f.watch && f.watch.movement && ['moved', 'cleared'].indexOf(f.watch.movement.kind) >= 0) ? 1 : 2) : 3;
const moveShort = (f) => f.grade === 'WATCH' && f.watch && f.watch.movement ? (f.watch.movement.kind === 'unclear' ? '· no clear movement' : f.watch.movement.kind === 'stepped' ? '⤴ stepped with coverage' : (f.watch.movement.direction === 'fall' ? '▼' : '▲') + ' moved') : '';
const chartOf = (rep, id) => rep.charts.filter((c) => c.id === id)[0];
async function tableOf(p, figSel) {
  // press the figure's Table button and read the rows it shows
  await p.click(figSel + ' .tbl-btn');
  const rows = await p.evaluate((s) => Array.from(document.querySelectorAll(s + ' .vtable tbody tr')).map((tr) => Array.from(tr.children).map((c) => c.textContent.replace(/\s+/g, ' ').trim())), figSel);
  await p.click(figSel + ' .tbl-btn');
  return rows;
}

// a routed claim's words state the rule that routed it: a monthly total its EFFECTIVE rows a month against the
// totals' line, a count its rows a month against the counts' line, and the printed number is below the printed
// line (fixer round, 24 Sep 2026: "about 217 rows a month, under 200" for a monthly total)
function routedMatchesRule(txt, R, where) {
  const t = squash(txt);
  // the rule is read from the engine's own condition words ("... effective rows a month (a monthly total)")
  if (/effective rows/.test(R.condition || '') || R.kind === 'total') {
    ok(t.indexOf(fmt(R.effective_rows_a_month, 0)) >= 0 && t.indexOf(fmt(R.min_effective_rows_a_month, 0)) >= 0 && /effective rows/.test(t), where + ': a monthly total is not described by its effective rows a month against the totals\' line: ' + t);
    ok(!/rows a month, under 200\b/.test(t) && t.indexOf('under ' + fmt(R.min_rows_a_month, 0) + ',') < 0, where + ': a monthly total is described with the counts\' rule: ' + t);
    ok(R.effective_rows_a_month < R.min_effective_rows_a_month, where + ': the routed total\'s effective rows ' + R.effective_rows_a_month + ' are not below the line ' + R.min_effective_rows_a_month);
  } else {
    ok(t.indexOf(fmt(R.rows_a_month, 0) + ' rows a month') >= 0 && t.indexOf(fmt(R.min_rows_a_month, 0)) >= 0, where + ': a count is not described by its rows a month against the counts\' line: ' + t);
    ok(R.rows_a_month < R.min_rows_a_month, where + ': the routed count\'s rows ' + R.rows_a_month + ' are not below the line ' + R.min_rows_a_month);
  }
  // every "under M" printed follows a number below it: the nearest "about N" before each "under M"
  const re = /under (?:the )?([\d,]+)/g;
  let m;
  while ((m = re.exec(t))) {
    const before = (t.slice(0, m.index).match(/about ([\d,]+)/g) || []).pop();
    if (!before) continue;
    const a = +before.replace(/[^\d]/g, ''), b = +m[1].replace(/,/g, '');
    ok(a < b, where + ': prints ' + a + ' as under ' + b + ': ' + t.slice(Math.max(0, m.index - 160), m.index + 20));
  }
}

// the manager view against the report: bottom line, decision lines, tiles, the charts the rules chose, the
// trust strip, and no p, q, MSIS or DM (plan §4.1)
async function managerMatches(p, rep) {
  const d = await p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    const M = document.getElementById('nl2-manager'), A = document.getElementById('nl2-analyst');
    return { mShown: !!M && !M.hidden && M.offsetParent !== null, aHidden: !!A && A.hidden,
      tab: t(document.querySelector('.nl2-views [aria-selected="true"]')),
      head: t(M && M.querySelector('.nl2-bottom')),
      bl: Array.from(M ? M.querySelectorAll('.nl2-bottom .nl2-bl') : []).map((x) => t(x)),
      lines: Array.from(M ? M.querySelectorAll('.nl2-decision li[data-fid]') : []).map((li) => ({ fid: li.getAttribute('data-fid'), t: t(li) })),
      tiles: Array.from(M ? M.querySelectorAll('.nl2-tile') : []).map((x) => ({ fid: x.getAttribute('data-fid'), v: t(x.querySelector('.nl2-tile-v')), ci: t(x.querySelector('.nl2-tile-ci')), g: t(x.querySelector('.nl2-grade')) })),
      charts: Array.from(M ? M.querySelectorAll('[data-chart]') : []).map((x) => x.getAttribute('data-chart')),
      trust: Array.from(M ? M.querySelectorAll('.nl2-trust li[data-layer]') : []).map((li) => [li.getAttribute('data-layer'), t(li)]),
      settle: t(M && M.querySelector('.nl2-settle')), fanNote: t(M && M.querySelector('[data-chart^="fan."] .nl2-fignote')),
      healthTile: !!document.querySelector('#try-report .tr-kpi.tr-health'),
      textNum: t(document.querySelector('#try-report .tr-kpi.tr-health .tr-textnum')),
      issues: Array.from(document.querySelectorAll('#try-report .tr-kpi.tr-health .tr-issues li')).map((li) => t(li)),
      text: M ? M.innerText : '' };
  });
  ok(d.mShown && d.aHidden && /Manager/.test(d.tab || ''), 'the manager view is not the default view (' + JSON.stringify([d.mShown, d.aHidden, d.tab]) + ')');
  // the bottom line: the report's summary sentences when it has them (fixer round, 24 Sep 2026), else the engine headline
  const SL = (rep.summary && rep.summary.lines) || [];
  if (SL.length) ok(JSON.stringify(d.bl) === JSON.stringify(SL.map((l) => squash(l.text))), 'the bottom line is not the report\'s summary: ' + JSON.stringify(d.bl));
  else ok(d.head === squash(rep.story.headline), 'the bottom line is not the engine headline: ' + d.head);
  const pm = rep.primary_metric, F = {};
  rep.findings.forEach((f) => { F[f.id] = f; });
  if (pm) ok(d.lines.length && d.lines[0].fid === pm.finding_id, 'the first decision line is not the primary claim: ' + JSON.stringify(d.lines.slice(0, 2)));
  d.lines.forEach((l) => {
    const f = F[l.fid], e = f.effect;
    ok(l.t.indexOf(gradeWord(f.grade, f.kind)) >= 0 && l.t.indexOf(GRADE_GLYPH[f.grade]) >= 0, 'decision line ' + l.fid + ' lacks its grade and glyph: ' + l.t);
    ok(l.t.indexOf(fxEffect(e)) >= 0, 'decision line ' + l.fid + ' lacks the effect ' + fxEffect(e) + ': ' + l.t);
    const ci = fxCi(e.ci, e.scale);
    const mv = f.grade === 'WATCH' && f.watch && f.watch.movement;
    // a claim the coverage steps explain: its as-filed interval spans the step and is not printed, nor "no clear movement"
    if (mv && mv.kind === 'stepped') ok(!(ci && l.t.indexOf(ci) >= 0) && !/No clear movement/.test(l.t) && /Stepped (up|down) at/.test(l.t), 'decision line ' + l.fid + ' prints the as-filed interval or "no clear movement" beside the step the engine found: ' + l.t);
    else if (ci) ok(l.t.indexOf(ci) >= 0, 'decision line ' + l.fid + ' lacks the interval ' + ci + ': ' + l.t);
    if (e.scale === 'difference') ok(!/%/.test(l.t.split('Held back')[0]), 'decision line ' + l.fid + ' prints a percentage for a change with no ratio: ' + l.t);
    if (mv && mv.kind !== 'stepped') ok(mv.kind === 'unclear' ? /No clear movement: the interval includes zero/.test(l.t) : new RegExp('Moved: the whole interval is ' + (mv.direction === 'fall' ? 'below' : 'above')).test(l.t), 'decision line ' + l.fid + ' does not say whether it moved (' + mv.kind + '): ' + l.t);
  });
  // after the primary claim, the lines run from the strongest evidence down
  // (the ledger's own line count, summary.monitoring, comes after the business claims: fixer round, 24 Sep 2026)
  const MON = (rep.summary && rep.summary.monitoring) || [];
  const rest = d.lines.slice(pm ? 1 : 0).map((l) => (MON.indexOf(l.fid) >= 0 ? 10 : 0) + strengthOf(F[l.fid]));
  ok(JSON.stringify(rest) === JSON.stringify(rest.slice().sort((a, b) => a - b)), 'the decision lines are not ordered by strength of evidence: ' + JSON.stringify(d.lines.map((l) => [l.fid, strengthOf(F[l.fid])])));
  const K = chartOf(rep, 'kpi').data.tiles;
  ok(d.tiles.length === K.length && K.length <= 3, d.tiles.length + ' KPI tiles for ' + K.length + ' in the report (the first screen holds at most three)');
  K.forEach((k, i) => {
    const s = d.tiles[i] || {};
    ok(s.fid === k.finding_id && s.v === fxVal(k.value, k.scale, k.unit), 'tile ' + (i + 1) + ' shows ' + JSON.stringify(s) + ' for ' + k.finding_id + ' ' + fxVal(k.value, k.scale, k.unit));
    ok(s.g === GRADE_GLYPH[k.grade] + ' ' + gradeWord(k.grade, (F[k.finding_id] || {}).kind), 'tile ' + k.finding_id + ' grade badge is "' + s.g + '"');
    if (k.scale === 'difference') ok(!/%/.test((s.v || '') + (s.ci || '')), 'tile ' + k.finding_id + ' prints a percentage for a change with no ratio: ' + s.v);
    const ci = fxCi(k.ci, k.scale);
    ok(ci ? (s.ci || '').indexOf(ci) >= 0 && (s.ci || '').indexOf(Math.round(k.ci_level * 100) + '%') >= 0 : !s.ci, 'tile ' + k.finding_id + ' interval: "' + s.ci + '", want ' + ci);
  });
  const want = rep.charts.filter((c) => c.view === 'manager' && c.default_visible).map((c) => c.id).sort();
  ok(JSON.stringify(d.charts.slice().sort()) === JSON.stringify(want), 'manager charts ' + JSON.stringify(d.charts) + ', the contract selects ' + JSON.stringify(want));
  ok(!/\bp\s*[=<≤]|\bq\s*[=<]|p-value|q-value|MSIS|Diebold|\bDM\b|n_eff/i.test(d.text), 'the manager view shows p, q, MSIS or DM: ' + (d.text.match(/.{0,40}(\bp\s*[=<≤]|\bq\s*[=<]|p-value|q-value|MSIS|Diebold|\bDM\b|n_eff).{0,40}/i) || [''])[0]);
  const R = rep.reproducibility.figures_reproduced, B = rep.engine.benchmark, c = rep.cleaning;
  const tr = Object.fromEntries(d.trust);
  ok((tr.reproduced || '').indexOf(fmt(R.k, 0) + ' of ' + fmt(R.n, 0)) >= 0, 'trust strip, reproduced: ' + tr.reproduced);
  ok((tr.rows || '').indexOf(fmt(c.rows_quarantined, 0) + ' of ' + fmt(c.rows_in, 0)) >= 0, 'trust strip, rows: ' + tr.rows);
  const W = B.worst_cell, fc = tr.false_confirm || '';
  if (B.available && B.routed) {
    ok(/held at WATCH by rule/.test(fc) && !/said CONFIRMED in/.test(fc), 'trust strip, a claim held at WATCH by rule is quoted a false-confirm rate: ' + fc);
    routedMatchesRule(fc, B.routed, 'trust strip');
  } else if (B.available && B.matched_cell && B.match === 'none') ok(/no measured condition is like this file/.test(fc) && !/said CONFIRMED in/.test(fc), 'trust strip quotes a rate for a file no measured condition is like: ' + fc);
  else if (B.available && B.matched_cell) {
    const M = B.matched_cell;
    ok(fc.indexOf(M.rate.toFixed(1) + '%') >= 0 && fc.indexOf(W.rate.toFixed(1) + '%') >= 0, 'trust strip, false confirmations: ' + fc);
    ok(fc.indexOf(B.match === 'close' ? 'like this file' : 'nearest simulated condition') >= 0 && fc.indexOf('momentum ' + fmt(B.file.phi, 2)) >= 0, 'trust strip: the file\'s own momentum is not beside the matched condition, or "like this file" is claimed for a distant one: ' + fc);
    ok(M.at_bar ? /exactly the \d+% bar/.test(fc) : /no change at all/.test(fc), 'trust strip: what was true in the matched condition is not said: ' + fc);
  } else ok(/not/.test(fc), 'trust strip, false confirmations, with no matched cell: ' + fc);
  if (B.available && W) ok(W.at_bar ? /hardest simulated condition \([^)]*with a true change of exactly the \d+% bar\)/.test(fc) : true, 'trust strip: the hardest condition is called a no-change condition: ' + fc);
  if (B.available && W && W.above_target) ok(/above the engine's 1(\.0)?% aim/.test(fc), 'trust strip: the hardest condition is above the 1% aim and does not say so: ' + fc);
  if (B.available && B.certification) ok(fc.indexOf(fmt(B.certification.passed, 0) + ' of ' + fmt(B.certification.cells, 0) + ' simulated conditions') >= 0, 'trust strip: the release check result is not stated: ' + fc);
  ok(/Power/.test(tr.power || ''), 'trust strip, power: ' + tr.power);
  ok(/not measured/.test(tr.tipping || ''), 'trust strip, tipping point: ' + tr.tipping);
  ok((tr.health || '').indexOf(rep.health.score_min.toFixed(1)) >= 0 && (tr.health || '').indexOf(rep.health.weakest) >= 0, 'trust strip, weakest dimension: ' + tr.health);
  // numbers stored as text, which every CSV has (30 Sep 2026): the engine's line is never an issue, and when its
  // mark-down lowers a score the Data health area says so in plain words (the trust strip, and the health tile)
  const TN = rep.health.csv_text_numbers, TNW = 'the score counts numbers stored as text, which every csv has';   // compared lower-cased
  ok(!(rep.health.issues || []).concat(d.issues || []).some((x) => /numbers are stored as text; they will sort/.test(x)), 'a health issue still says the numbers are stored as text');
  if (TN && TN.note) {
    ok(TN.note.toLowerCase().indexOf(TNW) === 0 && (tr.health || '').indexOf(squash(TN.note)) >= 0, 'trust strip: the mark-down for numbers stored as text is not said: ' + tr.health);
    if (d.healthTile) ok(d.textNum === squash(TN.note), 'the health tile does not say the score counts numbers stored as text: ' + d.textNum);
  } else {
    ok((tr.health || '').toLowerCase().indexOf(TNW) < 0 && !d.textNum, 'the Data health area speaks of numbers stored as text though no score is lowered: ' + tr.health);
  }
  rep.findings.filter((f) => f.grade === 'WATCH' && (f.kind === 'business' || f.kind === 'forecast') && f.watch && f.watch.settle).forEach((f) => {
    ok((d.settle || '').indexOf(squash(f.watch.settle)) >= 0, 'what would settle ' + f.id + ' is not shown');
  });
  return d;
}

// fixer round (24 Sep 2026): the bottom line was 7-11 "graded WATCH" clauses in engine words; the ledger's own line
// count took first-screen tiles and lines beside a money total; a monthly total was described with the counts'
// "rows a month, under 200" rule; on a phone the first screen held only the file's metadata
async function firstScreenOf(p) {
  return p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : '';
    const M = document.getElementById('nl2-manager');
    const vis = Array.from(M.querySelectorAll('.nl2-decision li[data-fid]')).filter((li) => !li.closest('details'));
    return { bl: Array.from(M.querySelectorAll('.nl2-bottom .nl2-bl')).map(t), lines: vis.map((li) => li.getAttribute('data-fid')),
      tiles: Array.from(M.querySelectorAll('.nl2-tile')).map((x) => x.getAttribute('data-fid')),
      trustLine: t(M.querySelector('.nl2-trust-line')), layers: Array.from(M.querySelectorAll('.nl2-trust li[data-layer]')).map((li) => [li.getAttribute('data-layer'), t(li)]),
      bench: t(M.querySelector('[data-chart="benchmark"] .nl2-fignote')), power: Array.from(M.querySelectorAll('.nl2-power')).map(t) };
  });
}
check('try-v2-bottom-line-is-decision-ready', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sample');
  await p.evaluate(() => document.querySelectorAll('#nl2-manager details').forEach((d) => { d.open = true; }));
  await p.waitForTimeout(200);
  const d = await firstScreenOf(p);
  const SL = (rep.summary && rep.summary.lines) || [];
  ok(SL.length >= 1 && SL.length <= 3 && d.bl.length === SL.length, 'the bottom line is not at most three summary sentences: ' + JSON.stringify(d.bl));
  const all = d.bl.join(' ');
  ok(!/graded (WATCH|CONFIRMED)|\bvolume\b|rows like for like|category values|property values|sample-messy\.csv/.test(all), 'the bottom line reads in engine vocabulary: ' + all);
  // the sample's planted story: a building added part-way, the rest measured like for like, and the planning number
  const pm = rep.findings.filter((f) => f.id === rep.primary_metric.finding_id)[0];
  const lf = pm.composition && pm.composition.like_for_like;
  ok(/East York Low-Rise/.test(d.bl[0] || '') && /like for like/.test(d.bl[0] || '') && lf && (d.bl[0] || '').indexOf(fxVal(Math.abs(lf.estimate), 'fraction').replace('+', '')) >= 0, 'the first sentence does not say what moved and why (the building, then like for like): ' + d.bl[0]);
  const fc = rep.findings.filter((f) => f.id === 'forecast.' + (rep.charts.filter((c) => c.id.indexOf('fan.') === 0)[0] || { id: 'fan.x' }).id.slice(4) + '.next')[0];
  if (fc && fc.effect.estimate !== null) ok(all.indexOf(fmt(fc.effect.estimate, 0)) >= 0, 'the bottom line does not carry the planning number ' + fmt(fc.effect.estimate, 0) + ': ' + all);
  // the ledger's own line count stays off the first screen beside a money total
  const mon = (rep.summary && rep.summary.monitoring) || [];
  ok(mon.length, 'the sample has no ledger-line claims marked as monitoring');
  ok(!d.tiles.some((x) => mon.indexOf(x) >= 0), 'a ledger-line claim takes a first-screen tile: ' + JSON.stringify(d.tiles));
  ok(!d.lines.slice(0, 3).some((x) => mon.indexOf(x) >= 0), 'a ledger-line claim takes a first-screen decision line: ' + JSON.stringify(d.lines.slice(0, 3)));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
// Live bug (24 Sep 2026): after consent and "Write the AI summaries" both summaries passed the guard but were
// drawn inside the hidden analyst view, so a visitor on the manager view saw nothing. The offer sits under the
// bottom line; the executive summary appears there, the technical one in the analyst view's summary section.
check('try-v2-ai-summaries-show-in-the-view-where-the-visitor-asks', DESK, async (ctx) => {
  // the integrated flow: the AI-written report opens in its own card, above the engine's report, where the reader is sent
  const rep = fixture('sample'); rep.__profile = true; rep.__results = true;
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: [], operations: [], analyses: [] };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status: 200, json: { plan } }
    : { status: 200, json: { report: 'Rent rose in the East. [S1]', sources: [{ title: 'A public source', link: 'https://example.org/a' }], model: 'check', repaired: 0 } } });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden]), #try-msg:not([hidden])');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  const d = await p.evaluate(() => {
    const c = document.getElementById('try-ai-report'), r = document.getElementById('try-report'), t = c.textContent;
    const a = c.querySelector('.ai-rep-wm a');
    return { before: !!(c.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING), text: t, src: c.querySelectorAll('.ai-rep-srcs a').length, home: a && a.href,
      share: !!c.querySelector('#try-share'), pdf: !!c.querySelector('#try-pdf'), old: !!document.getElementById('try-ai-go') || !!document.getElementById('try-ai-ok') };
  });
  ok(d.before && await p.isVisible('#try-ai-report') && await p.isVisible('#try-report'), 'the AI report is not shown above the engine report');
  ok(/The AI-written report/.test(d.text) && /Rent rose in the East/.test(d.text) && /Honesty check/.test(d.text), 'the AI report card lacks its heading, text or honesty note: ' + d.text.slice(0, 200));
  ok(d.src === 1 && d.share && d.pdf, 'the AI report lacks its sources or its share and PDF actions');
  ok(d.home === DEMO_ORIGIN + '/index.html', 'the watermark does not link to the page\'s own address: ' + d.home);
  ok(!d.old, 'the old consent box or summary button is offered beside the AI report');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
check('try-v2-a-routed-claim-is-described-by-its-own-rule', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sample');
  await p.evaluate(() => document.querySelectorAll('#nl2-manager details').forEach((d) => { d.open = true; }));
  await p.waitForTimeout(200);
  const d = await firstScreenOf(p);
  // a routed claim is described by its own rule everywhere the page states it
  const B = rep.engine.benchmark;
  ok(B.available && B.routed, 'the sample\'s primary claim is not routed in this report (no benchmark, or no rule applies)');
  if (B.available && B.routed) {
    routedMatchesRule(d.trustLine, B.routed, 'trust line');
    routedMatchesRule((d.layers.filter((l) => l[0] === 'false_confirm')[0] || ['', ''])[1], B.routed, 'trust layer');
    routedMatchesRule(d.bench, B.routed, 'benchmark caption');
    // every routed claim's line states its own rule and numbers (its report record power.routed_rule)
    rep.findings.filter((f) => f.power && f.power.routed && f.power.routed_rule).forEach((f) => {
      const x = d.power.filter((y) => /by rule/.test(y));
      ok(x.length, 'no decision line states a routing rule');
    });
    d.power.filter((x) => /by rule/.test(x) && x.indexOf(fmt(B.routed.effective_rows_a_month || B.routed.rows_a_month, 0) + ' ') >= 0).forEach((x) => routedMatchesRule(x, B.routed, 'decision line'));
    d.power.filter((x) => /by rule/.test(x)).forEach((x) => ok(/about [\d,]+ (effective )?rows a month/.test(x) && !/too few rows a month\)/.test(x), 'a routed decision line gives no numbers for its rule: ' + x));
  }
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
check('try-v2-phone-first-screen-holds-the-bottom-line', PHONE, async (ctx) => {
  const { p } = await openV2(ctx, 'sample');
  const r = await p.evaluate(() => {
    document.getElementById('try-report').scrollIntoView();
    const all = Array.from(document.querySelectorAll('#nl2-manager .nl2-bottom .nl2-bl'));
    const b = all.length ? all : [document.querySelector('#nl2-manager .nl2-bottom')].filter(Boolean);
    const head = document.querySelector('#try-report .tr-head').getBoundingClientRect();
    return { top: b.length ? b[0].getBoundingClientRect().top : null, bottom: b.length ? b[b.length - 1].getBoundingClientRect().bottom : null,
      head: head.height, vh: window.innerHeight };
  });
  // the file's metadata takes at most a quarter of the screen, and the whole bottom line is on it
  ok(r.head <= 0.25 * r.vh, 'on a 390 px phone the file\'s metadata takes ' + Math.round(r.head) + ' px of the ' + r.vh + ' px first screen');
  ok(r.top !== null && r.top >= 0 && r.bottom <= r.vh, 'on a 390 px phone the bottom line is not whole on the first screen of the report: ' + JSON.stringify(r));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-v2-manager-view-is-one-screen-from-the-contract', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sample');
  const d = await managerMatches(p, rep);
  const pm = rep.primary_metric, B = rep.engine.benchmark;
  const band = rep.forecast.band, cov = B.forecast_coverage_80;
  ok(d.fanNote && d.fanNote.indexOf(fmt(band.n_errors, 0) + ' past errors') >= 0 && d.fanNote.indexOf((band.conditional_coverage_p10_p90[0] * 100).toFixed(0) + '%') >= 0 &&
    d.fanNote.indexOf((cov.steady.lo * 100).toFixed(1) + '%') >= 0, 'the fan chart lacks the §1.3 sentence with its measured coverage: ' + d.fanNote);
  // the table view of every manager chart holds the chart's own data
  const trendId = 'trend.' + pm.claim_key, T = chartOf(rep, trendId).data;
  const rows = await tableOf(p, '#nl2-manager [data-chart="' + trendId + '"]');
  ok(rows.length === T.months.length && rows.every((r, i) => r[0] === T.months[i] && r[1] === fxSeries(T.values[i], T.scale)), 'the trend table is not the chart data: ' + JSON.stringify(rows.slice(0, 2)));
  // the forecast the engine puts first (review v2: the money total, not the row count), whatever its series
  const fanId = rep.charts.filter((c) => c.id.indexOf('fan.') === 0 && c.view === 'manager')[0].id, replayId = 'replay.' + fanId.slice(4);
  const fan = chartOf(rep, fanId).data, frows = await tableOf(p, '#nl2-manager [data-chart="' + fanId + '"]');
  ok(frows.length === fan.history.length + fan.forward.length, 'the fan table has ' + frows.length + ' rows for ' + (fan.history.length + fan.forward.length) + ' months');
  const rp = chartOf(rep, replayId).data, rrows = await tableOf(p, '#nl2-manager [data-chart="' + replayId + '"]');
  ok(rrows.length === rp.months.length && rrows.filter((r) => /miss/.test(r.join(' '))).length === rp.months.filter((m) => !m.in_band).length, 'the replay table does not mark its misses');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// review of the site (24 Sep 2026): a change with no ratio printed as two different percentages, a forecast
// the engine does not offer kept its point and range, WATCH did not say whether a claim moved, the trend
// box claimed to be the latest average's interval, and the manager view ran to 4.4 screens
check('try-v2-first-screen-holds-the-decision-and-no-false-numbers', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'shop-margins-36m');
  const d = await managerMatches(p, rep);
  const F = {};
  rep.findings.forEach((f) => { F[f.id] = f; });
  const nr = rep.findings.filter((f) => f.effect && f.effect.scale === 'difference');
  ok(nr.length, 'the fixture has no change with no ratio');
  const cells = await p.evaluate(() => Array.from(document.querySelectorAll('#try-report table.nl2-ftab tbody tr')).map((tr) => [tr.getAttribute('data-fid'), tr.closest('#nl2-manager') ? 'm' : 'a', Array.from(tr.children).map((c) => c.textContent.replace(/\s+/g, ' ').trim())]));
  nr.forEach((f) => {
    const rows = cells.filter((c) => c[0] === f.id);
    ok(rows.length === 2, f.id + ' is not in both findings tables');
    rows.forEach((r) => {
      const eff = r[1] === 'm' ? r[2][1] : r[2][2];
      ok(!/%/.test(eff) && eff.indexOf(fxEffect(f.effect)) === 0, f.id + ' effect in the ' + r[1] + ' table: "' + eff + '", want ' + fxEffect(f.effect));
    });
  });
  // a forecast the engine does not offer: no tile, no point, no range, anywhere
  const fc = rep.findings.filter((f) => f.kind === 'forecast' && /\.next$/.test(f.id));
  ok(fc.length && !rep.forecast.available, 'the fixture\'s forecast is offered now');
  fc.forEach((f) => {
    ok(!d.tiles.some((t) => t.fid === f.id), f.id + ' has a KPI tile though the engine does not offer it');
    cells.filter((c) => c[0] === f.id).forEach((r) => {
      const txt = r[2].join(' | ');
      ok(/not offered/.test(txt) && !/\[\d/.test(txt) && !/\d to \d/.test(r[1] === 'm' ? r[2][1] : r[2][2] + r[2][3]), f.id + ' shows a point or range in the ' + r[1] + ' table: ' + txt);
    });
  });
  // WATCH says whether each claim moved: a fee that fell inside the bar, a flat wait time
  ok(/Moved: the whole interval is below zero, but the fall is not yet shown to be at least 5%/.test((d.lines.filter((l) => l.fid === 'measure.fee.change')[0] || {}).t || ''), 'the fee line does not say it moved but is not yet shown to reach 5%');
  ok(/No clear movement/.test((d.lines.filter((l) => l.fid === 'measure.wait_minutes.change')[0] || {}).t || ''), 'the wait-time line does not say it shows no clear movement');
  const settle = await p.evaluate(() => Array.from(document.querySelectorAll('#nl2-manager .nl2-settle-list > li')).map((li) => li.getAttribute('data-move')));
  ok(settle.indexOf('moved') >= 0 && settle.indexOf('unclear') >= 0 && settle.indexOf('moved') < settle.indexOf('unclear'), 'what would settle it does not put the claims that moved first, apart from the others: ' + JSON.stringify(settle));
  // the trend chart: the series panel holds the data and the two means; the change sits on its own axis
  const tr = await p.evaluate(() => { const f = document.querySelector('#nl2-manager figure[data-chart^="trend."]'); return f ? { txt: Array.from(f.querySelectorAll('.viz svg text')).map((t) => t.textContent).join(' | '), box: !!f.querySelector('.nl2-ci-box'), note: f.querySelector('.nl2-fignote').textContent } : null; });
  ok(tr && !tr.box && !/interval of the latest average/.test(tr.txt), 'the trend chart still draws the change interval as the latest average\'s interval');
  ok(tr && /Values: (rows a month|average \S+ a month|total \S+ a month)/.test(tr.txt) && /Change, latest 12 months against the 12 before/.test(tr.txt), 'the trend chart lacks its value axis title or the change strip: ' + (tr && tr.txt.slice(0, 200)));
  // the first screen: the decision lines, at most three tiles and the trust line fit in one 900 px screen
  const top = await p.evaluate(() => { const a = document.querySelector('#nl2-manager .nl2-bottomcard').getBoundingClientRect(), b = document.querySelector('#nl2-manager .nl2-trust-line').getBoundingClientRect(); return b.bottom - a.top; });
  ok(top <= 900, 'the decision, tiles and trust line take ' + Math.round(top) + ' px, more than one 900 px screen');
  ok(!(await p.evaluate(() => /#\d|\bS2\b|\bR1\b|M1-M2/.test(document.getElementById('nl2-manager').innerText))), 'the manager view shows plan codes');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// review of the site (24 Sep 2026): money on the first screen printed as "82,201 (80% range 74,149.91 to
// 123,139.33)" beside "13,717.58"; the sample's story (a building bought mid-way steps the rent total up)
// was told in words only, with the like-for-like comparison nowhere on the manager view
const MON3 = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const monName = (ym) => MON3[+ym.slice(5, 7) - 1] + ' ' + ym.slice(0, 4);
check('try-v2-money-in-whole-units-and-the-like-for-like-line-on-the-first-chart', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sample');
  const money = rep.findings.filter((f) => f.effect && f.effect.scale === 'money' && f.effect.estimate !== null);
  ok(money.length >= 1, 'the sample has no money forecast marked as money: ' + JSON.stringify(rep.findings.filter((f) => f.kind === 'forecast').map((f) => [f.id, f.effect.scale])));
  const d = await p.evaluate(() => {
    const t = (e) => e.textContent.replace(/\s+/g, ' ').trim();
    return { tiles: Array.from(document.querySelectorAll('#nl2-manager .nl2-tile')).map((x) => [x.getAttribute('data-fid'), t(x)]),
      lines: Array.from(document.querySelectorAll('#nl2-manager .nl2-decision li[data-fid]')).map((li) => [li.getAttribute('data-fid'), t(li)]),
      rows: Array.from(document.querySelectorAll('#nl2-manager table.nl2-ftab tbody tr')).map((tr) => [tr.getAttribute('data-fid'), t(tr.children[1])]) };
  });
  money.forEach((f) => {
    const e = f.effect, pt = fmt(e.estimate, 0), rng = e.ci ? fmt(e.ci[0], 0) + ' to ' + fmt(e.ci[1], 0) : null;
    const where = [['decision line', d.lines], ['tile', d.tiles], ['findings table', d.rows]];
    where.forEach(([w, list]) => {
      list.filter((x) => x[0] === f.id).forEach((x) => {
        const head = x[1].split('Measured')[0];
        ok(head.indexOf(pt) >= 0 && (!rng || head.indexOf(rng) >= 0), f.id + ' ' + w + ' lacks ' + pt + ' / ' + rng + ' in whole units: ' + head.slice(0, 200));
        ok(!/\d\.\d/.test(head.replace(/\d+(\.\d+)?%/g, '')), f.id + ' ' + w + ' prints money with decimals: ' + head.slice(0, 200));
      });
    });
  });
  // the primary claim's trend: the like-for-like line, the numbered months where the coverage changed,
  // both in the note and the table view, and the like-for-like row on the change strip
  const pm = rep.primary_metric, id = 'trend.' + pm.claim_key, T = chartOf(rep, id).data;
  ok(T.scale === 'money' && T.like_for_like && T.steps && T.steps.length, id + ': no money scale, like-for-like line or coverage steps in the report');
  const L = T.like_for_like;
  const g = await p.evaluate((cid) => {
    const f = document.querySelector('#nl2-manager [data-chart="' + cid + '"]');
    return f ? { lfl: Array.from(f.querySelectorAll('.viz path.nl2-lfl')).filter((x) => !/h18$/.test(x.getAttribute('d'))).length, lfd: f.querySelectorAll('.viz rect.nl2-lfd').length,
      steps: Array.from(f.querySelectorAll('.viz g.nl2-step text')).map((x) => x.textContent), strip: Array.from(f.querySelectorAll('.viz text')).map((x) => x.textContent),
      note: f.querySelector('.nl2-fignote').textContent.replace(/\s+/g, ' ') } : null;
  }, id);
  ok(g, id + ' is not on the manager view');
  ok(g.lfl === 1 && g.lfd === L.values.filter((v) => v !== null).length + 1, id + ': like-for-like line ' + g.lfl + ', marks ' + g.lfd + ' for ' + L.values.length + ' months and one strip mark');
  ok(JSON.stringify(g.steps) === JSON.stringify(T.steps.map((s, i) => String(i + 1))), id + ': numbered coverage lines ' + JSON.stringify(g.steps) + ' for ' + T.steps.length + ' steps');
  ok(g.strip.indexOf('like for like') >= 0 && g.strip.indexOf('as filed') >= 0, id + ': the change strip has no like-for-like row');
  ok(g.note.indexOf(fxVal(L.estimate, 'fraction')) >= 0 && /like for like/.test(g.note) && (L.tested ? /graded /.test(g.note) : /descriptive, not tested/.test(g.note)), id + ' note does not state the like-for-like change: ' + g.note);
  T.steps.forEach((s, i) => ok(g.note.indexOf((i + 1) + ', \'' + s.level + '\'') >= 0 && g.note.indexOf(monName(s.month)) >= 0, id + ' note does not name coverage change ' + (i + 1) + ' (' + s.level + ', ' + s.month + '): ' + g.note));
  const rows = await tableOf(p, '#nl2-manager [data-chart="' + id + '"]');
  ok(rows.every((r, i) => r[4] === fxSeries(L.values[i], T.scale)), id + ' table: the like-for-like column is not the chart data: ' + JSON.stringify(rows.slice(0, 2)));
  T.steps.forEach((s) => { const r = rows.filter((x) => x[0] === s.month)[0]; ok(r && r[5].indexOf(s.level) >= 0, id + ' table does not mark ' + s.month + ': ' + JSON.stringify(r)); });
  // the primary claim's decision line says what the file covers and gives the like-for-like change
  const line = (d.lines.filter((x) => x[0] === pm.finding_id)[0] || [])[1] || '';
  ok(line.indexOf('What the file covers changed') >= 0 && line.indexOf('Like for like') >= 0 && line.indexOf(fxVal(L.estimate, 'fraction')) >= 0, 'the primary decision line lacks the coverage change and the like-for-like figure: ' + line);
  // the analyst note of a chart the cap moved counts the charts it names
  const moved = rep.charts.filter((c) => /moved to the analyst view/.test(c.why_shown));
  // the chart registry's records (rule V) are drawn in their own area and are not counted in the manager view's cap (CONTRACT §5.9)
  const drawn = rep.charts.filter((c) => c.view === 'manager' && c.type !== 'viz' && ['kpi', 'findings_table'].indexOf(c.id) < 0).length;
  const words = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
  moved.forEach((c) => ok(c.why_shown.indexOf('draws at most ' + words[drawn] + ' charts') >= 0 && !/at most six charts/.test(c.why_shown), c.id + ': the cap note does not count the ' + drawn + ' drawn charts: ' + c.why_shown));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// A chart's link counts what it highlights in words that agree with the count (integration pass, 30 Sep 2026: "Show
// the 1 evidence facts behind it"): one evidence fact, two evidence facts; one claim, and its label says so too.
check('try-v2-chart-links-count-their-evidence-in-the-singular-and-the-plural', DESK, async (ctx) => {
  const rep = JSON.parse(JSON.stringify(fixture('sample')));
  const figs = rep.charts.filter((c) => c.view === 'manager' && c.default_visible && c.type !== 'viz' && ['kpi', 'findings_table', 'benchmark'].indexOf(c.id) < 0);
  ok(figs.length >= 3, 'the sample has ' + figs.length + ' manager figures, 3 are needed');
  const fid = rep.findings[0].id;
  figs[0].finding_ids = ['ledger.fact.one']; figs[1].finding_ids = ['ledger.fact.one', 'ledger.fact.two']; figs[2].finding_ids = [fid];
  const { p } = await openV2(ctx, 'sample', rep);
  const got = await p.evaluate((ids) => ids.map((id) => {
    const b = document.querySelector('#nl2-manager [data-chart="' + id + '"] .nl2-link');
    return b ? [b.textContent, b.getAttribute('aria-label')] : null;
  }), figs.slice(0, 3).map((c) => c.id));
  ok(got[0] && got[0][0] === 'Show the 1 evidence fact behind it' && /^Highlight the evidence fact this chart supports: /.test(got[0][1]), 'one evidence fact: ' + JSON.stringify(got[0]));
  ok(got[1] && got[1][0] === 'Show the 2 evidence facts behind it' && /^Highlight the evidence facts this chart supports: /.test(got[1][1]), 'two evidence facts: ' + JSON.stringify(got[1]));
  ok(got[2] && got[2][0] === 'Show the claim it supports' && /^Highlight the claim this chart supports: /.test(got[2][1]), 'one claim: ' + JSON.stringify(got[2]));
  const all = await p.evaluate(() => Array.from(document.querySelectorAll('#try-report .nl2-link')).map((b) => b.textContent + ' | ' + b.getAttribute('aria-label')));
  ok(!all.some((t) => /\b1 evidence facts\b|\b1 claims\b/.test(t)), 'a link counts one in the plural: ' + JSON.stringify(all.filter((t) => /\b1 (evidence facts|claims)\b/.test(t))));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-v2-clicking-a-chart-highlights-the-claim-it-supports', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sample');
  const fid = rep.primary_metric.finding_id, sel = '#nl2-manager [data-chart="trend.' + rep.primary_metric.claim_key + '"]';
  await p.click(sel + ' .nl2-link');
  let h = await p.evaluate(() => ({ ids: Array.from(document.querySelectorAll('#nl2-manager .nl2-hl')).map((e) => e.getAttribute('data-fid')), live: (document.getElementById('nl2-live') || {}).textContent }));
  // The primary chart may also draw the claim's like-for-like restatement (a tested claim whose
  // parent_id is the primary, ship round): the link then highlights that child too, and nothing else.
  const figIds = await p.evaluate((s) => (document.querySelector(s).getAttribute('data-findings') || '').split(' ').filter(Boolean), sel);
  const kids = rep.findings.filter((f) => f.parent_id === fid && figIds.indexOf(f.id) >= 0).map((f) => f.id);
  ok(h.ids.filter((x) => x === fid).length >= 2 && h.ids.every((x) => x === fid || kids.indexOf(x) >= 0), 'the link did not highlight the claim ' + fid + ' (tile, line and table row) and only its own restatements ' + JSON.stringify(kids) + ': ' + JSON.stringify(h.ids));
  ok((h.live || '').indexOf(squash(rep.findings.filter((f) => f.id === fid)[0].claim)) >= 0, 'the highlight is not announced: ' + h.live);
  await p.keyboard.press('Escape');
  ok(!(await p.$('#nl2-manager .nl2-hl')), 'Esc does not clear the highlight');
  const box = await p.$(sel + ' .viz svg');
  await box.click({ position: { x: 20, y: 20 } });
  ok(await p.$('#nl2-manager [data-fid="' + fid + '"].nl2-hl'), 'clicking the chart itself does not highlight its claim');
  // and back: a finding row links to the charts that show it
  const back = await p.evaluate((f) => Array.from(document.querySelectorAll('#nl2-manager tr[data-fid="' + f + '"] a[href^="#nl2c-"]')).map((a) => a.getAttribute('href')), fid);
  const fw = rep.findings.filter((f) => f.id === fid)[0].chart_ids.filter((c) => c !== 'kpi' && c !== 'findings_table' && chartOf(rep, c).view === 'manager');
  ok(fw.every((c) => back.indexOf('#nl2c-m-' + c) >= 0), 'the claim row does not link to its charts ' + JSON.stringify(fw) + ': ' + JSON.stringify(back));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-v2-analyst-view-is-a-paper-with-every-table', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sample');
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(150);
  const d = await p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    const A = document.getElementById('nl2-analyst'), M = document.getElementById('nl2-manager');
    const rows = (s) => Array.from(A.querySelectorAll(s + ' tbody tr')).map((tr) => Array.from(tr.children).map(t));
    return { shown: !A.hidden && A.offsetParent !== null, mHidden: M.hidden,
      secs: Array.from(A.querySelectorAll('section.nl2-sec')).map((s) => [s.getAttribute('data-sec'), t(s.querySelector('h3'))]),
      fhead: Array.from(A.querySelectorAll('table.nl2-ftab thead th')).map(t), frows: Array.from(A.querySelectorAll('table.nl2-ftab tbody tr')).map((tr) => [tr.getAttribute('data-fid'), Array.from(tr.children).map(t)]),
      models: rows('table.nl2-models'), cols: Array.from(A.querySelectorAll('table.nl2-coltab tbody tr')).map((tr) => [tr.getAttribute('data-col'), Array.from(tr.children).map(t)]),
      dims: rows('table.nl2-dims'), supp: Array.from(A.querySelectorAll('.nl2-supp li[data-rule]')).map((li) => [li.getAttribute('data-rule'), t(li)]),
      repro: t(A.querySelector('.nl2-repro')), aside: rows('table.nl2-setaside').length, asideNote: t(A.querySelector('.nl2-setaside-note')),
      ledger: A.querySelectorAll('table.nl2-ledger tbody tr').length, charts: Array.from(A.querySelectorAll('[data-chart]')).map((x) => x.getAttribute('data-chart')),
      methods: t(A.querySelector('[data-sec="methods"]')), limits: t(A.querySelector('[data-sec="limits"]')) };
  });
  ok(d.shown && d.mHidden, 'the analyst view did not replace the manager view');
  const order = ['summary', 'data', 'methods', 'results', 'limits', 'recs', 'repro', 'appendix'];
  ok(JSON.stringify(d.secs.map((s) => s[0])) === JSON.stringify(order), 'analyst sections ' + JSON.stringify(d.secs.map((s) => s[0])));
  ok(d.secs.every((s, i) => (s[1] || '').indexOf((i + 1) + '.') === 0), 'the sections are not numbered like a paper: ' + JSON.stringify(d.secs.map((s) => s[1])));
  ['Claim', 'Test', 'n', 'Effect', 'Interval', 'p', 'q', 'Grade', 'What would settle it'].forEach((h) => ok(d.fhead.indexOf(h) >= 0, 'the findings table has no "' + h + '" column: ' + JSON.stringify(d.fhead)));
  ok(d.frows.length === rep.findings.length && d.frows.every((r, i) => r[0] === rep.findings[i].id), 'the findings table rows are not the findings, in order');
  const col = (h) => d.fhead.indexOf(h);
  rep.findings.forEach((f, i) => {
    const r = d.frows[i][1], t = f.test;
    const gw = GRADE_GLYPH[f.grade] + ' ' + gradeWord(f.grade, f.kind) + (moveShort(f) ? ' ' + moveShort(f) : '');
    ok(r[col('Grade')] === gw, f.id + ' grade cell ' + r[col('Grade')] + ', want ' + gw);
    if (t && t.ran) {
      ok(r[col('Test')].indexOf(t.name) >= 0 && r[col('n')].indexOf(fmt(t.n_months, 0) + ' months') >= 0, f.id + ' test or n: ' + r[col('Test')] + ' / ' + r[col('n')]);
      ok(r[col('p')] === (t.p === null ? 'n/a' : String(+t.p.toPrecision(3))) && r[col('q')] === (t.q === null ? 'n/a' : String(+t.q.toPrecision(3))), f.id + ' p/q cells ' + r[col('p')] + ' / ' + r[col('q')]);
    }
    ok(r[col('Effect')] === fxEffect(f.effect), f.id + ' effect cell ' + r[col('Effect')] + ' for ' + fxEffect(f.effect));
    const ci = fxCi(f.effect.ci, f.effect.scale);
    ok(ci ? r[col('Interval')].indexOf(ci) === 0 : r[col('Interval')] === '', f.id + ' interval cell ' + r[col('Interval')] + ' for ' + ci);
    const st = f.watch && f.watch.settle ? f.watch.settle : f.needed_to_upgrade;
    ok(squash(r[col('What would settle it')]) === squash(st || ''), f.id + ' settle cell');
  });
  const Mo = rep.forecast.models;
  ok(d.models.length === Mo.length && d.models[0][0].indexOf(Mo[0].label) >= 0 && /champion/.test(d.models[0].join(' ')), 'the model table is not forecast.models by MASE: ' + JSON.stringify(d.models[0]));
  ok(d.models.every((r, i) => r[1] === (Mo[i].mase === null ? 'n/a' : Mo[i].mase.toFixed(2))), 'a model MASE cell differs from the report');
  const C = rep.health.columns;
  ok(d.cols.length === C.length && d.cols.every((r, i) => r[0] === C[i].name), 'the column quality table is not health.columns');
  C.forEach((c, i) => {
    const cells = d.cols[i][1].join(' | ');
    ok(cells.indexOf(c.completeness.pct.toFixed(1) + '%') >= 0 && cells.indexOf(c.completeness.ci[0].toFixed(1) + '–' + c.completeness.ci[1].toFixed(1)) >= 0, c.name + ' completeness and its interval: ' + cells);
    if (c.validity && c.validity.pct !== null) ok(cells.indexOf(c.validity.pct.toFixed(1) + '%') >= 0, c.name + ' validity: ' + cells);
  });
  ok(d.dims.length === rep.health.dimensions.length, 'the quality dimensions table');
  // a contribution waterfall (the chart registry) shows the drivers: #2b is then not listed as a chart not drawn
  const wfall = rep.charts.some((c) => c.type === 'viz' && c.data && c.data.chart === 'contribution_waterfall' && c.data.kind === 'waterfall');
  const absent = rep.charts_suppressed.filter((s) => !(wfall && s.rule === '#2b'));
  ok(d.supp.length === absent.length && absent.every((s, i) => d.supp[i][0] === s.rule && d.supp[i][1].indexOf(squash(s.why)) >= 0), 'the absent charts are not each listed with why: ' + JSON.stringify(d.supp.slice(0, 2)));
  const R = rep.reproducibility;
  ok(d.repro.indexOf(R.input_sha256) >= 0 && d.repro.indexOf(R.engine_snapshot) >= 0 && d.repro.indexOf(R.decision_code_snapshot) >= 0 &&
    d.repro.indexOf(fmt(R.figures_reproduced.k, 0) + ' of ' + fmt(R.figures_reproduced.n, 0) + ' figures') >= 0, 'the reproducibility block misses a hash or the k of n count');
  Object.keys(R.seeds).forEach((k) => ok(d.repro.indexOf(String(R.seeds[k])) >= 0, 'seed of ' + k + ' is not shown'));
  const qn = rep.downloads.quarantine_csv.split(/\r?\n/).filter((l) => l).length - 1;
  ok(d.aside === Math.min(qn, 50) && (d.asideNote || '').indexOf(fmt(qn, 0)) >= 0, 'the appendix set-aside table shows ' + d.aside + ' rows for ' + qn);
  const L = JSON.parse(rep.downloads.ledger_json);
  ok(d.ledger === L.audit_ledger.length + L.analysis_ledger.length, 'the appendix ledger shows ' + d.ledger + ' facts');
  const want = rep.charts.filter((c) => c.default_visible && c.id !== 'kpi').map((c) => c.id).sort();
  ok(JSON.stringify(Array.from(new Set(d.charts)).sort()) === JSON.stringify(want), 'analyst charts ' + JSON.stringify(Array.from(new Set(d.charts)).sort()) + ' for ' + JSON.stringify(want));
  // a measure's trend (its values are averages, not row counts) through its table view
  const tm = rep.charts.filter((c) => c.type === 'trend_windows' && c.id !== 'trend.volume')[0];
  if (tm) {
    const trows = await tableOf(p, '#nl2-analyst [data-chart="' + tm.id + '"]');
    ok(trows.length === tm.data.months.length && trows.every((r, i) => r[1] === fxSeries(tm.data.values[i], tm.data.scale) && r[2] === fmt(tm.data.rows[i], 0)), tm.id + ' table is not its values and rows: ' + JSON.stringify(trows.slice(0, 2)));
  }
  rep.methods.forEach((m) => ok(d.methods.indexOf(squash(m.name)) >= 0, 'method not described: ' + m.name));
  rep.limitations.forEach((l) => ok(d.limits.indexOf(squash(l.text)) >= 0, 'limitation not listed: ' + l.text.slice(0, 60)));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-v2-each-file-draws-exactly-the-charts-its-rules-select', DESK, async (ctx) => {
  for (const name of FX_NAMES) {
    const { p, rep } = await openV2(ctx, name);
    await p.click('.nl2-views [data-view="analyst"]');
    await p.waitForTimeout(120);
    const corr = chartOf(rep, 'corr');
    if (corr) {
      ok(!(await p.$('#nl2-analyst [data-chart="corr"]')), name + ': the correlation heatmap shows before the reader asks for it');
      await p.click('#nl2-analyst [data-act="corr"]');
    }
    const d = await p.evaluate(() => {
      const figs = Array.from(document.querySelectorAll('#try-report [data-chart]'));
      return { ids: Array.from(new Set(figs.map((f) => f.getAttribute('data-chart')))).sort(),
        empty: figs.filter((f) => f.querySelector('.viz') && !f.querySelector('.viz svg *, .viz table')).map((f) => f.getAttribute('data-chart')),
        unlinked: figs.filter((f) => f.tagName === 'FIGURE' && f.getAttribute('data-findings') === null).map((f) => f.getAttribute('data-chart')),
        supp: Array.from(document.querySelectorAll('#nl2-analyst .nl2-supp li[data-rule]')).map((li) => li.getAttribute('data-rule')),
        cells: Array.from(document.querySelectorAll('#nl2-analyst [data-chart^="catmonth."]')).map((f) => [f.getAttribute('data-chart'), f.querySelectorAll('g.hc2').length,
          Array.from(f.querySelectorAll('.viz svg text.lab')).map((t) => t.textContent),
          Array.from(f.querySelectorAll('g.hc2')).filter((g) => !/^\d[\d,]*$/.test((g.querySelector('.cv2') || {}).textContent || '') || !/[○◐●]/.test((g.querySelector('.gl2') || {}).textContent || '')).length,
          (f.querySelector('.nl2-fignote') || {}).textContent || '']) };
    });
    const want = rep.charts.map((c) => c.id).sort();
    ok(JSON.stringify(d.ids) === JSON.stringify(want), name + ': charts drawn ' + JSON.stringify(d.ids) + ', the rules select ' + JSON.stringify(want));
    ok(!d.empty.length, name + ': empty charts ' + JSON.stringify(d.empty));
    ok(!d.unlinked.length, name + ': charts with no claim link ' + JSON.stringify(d.unlinked));
    // a contribution waterfall (the chart registry) shows the drivers: #2b is then not listed as a chart not drawn
    const wfall = rep.charts.some((c) => c.type === 'viz' && c.data && c.data.chart === 'contribution_waterfall' && c.data.kind === 'waterfall');
    ok(JSON.stringify(d.supp) === JSON.stringify(rep.charts_suppressed.filter((s) => !(wfall && s.rule === '#2b')).map((s) => s.rule)), name + ': suppressed rules listed ' + JSON.stringify(d.supp));
    d.cells.forEach(([id, n, labels, bad, note]) => {
      const c = chartOf(rep, id).data;
      // its note says where the change sits only beside the contribution waterfall, and never that drivers are "not
      // computed in this release" (pre-deploy pass, 30 Sep 2026: it said so beside the waterfall)
      ok(!/not computed in this release/.test(note) && /contribution waterfall shows where the change sits/.test(note) === wfall &&
        /no contribution waterfall was built for this file/i.test(note) === !wfall, name + ' ' + id + ': the map\'s note does not match the waterfall (' + wfall + '): ' + note);
      ok(n === c.categories.length * c.months.length && !bad, name + ' ' + id + ': ' + n + ' cells for ' + c.categories.length + ' x ' + c.months.length + ', ' + bad + ' without a number and a band glyph');
      // every category is named on the map (in full, or clipped with an ellipsis)
      const named = (k) => labels.some((l) => l === k || (l.endsWith('…') && k.indexOf(l.slice(0, -1)) === 0));
      ok(c.categories.every(named), name + ' ' + id + ': a category has no label on the map: ' + c.categories.filter((k) => !named(k)).join(', '));
    });
    if (rep.story.headline.indexOf('The business analysis did not run') === 0) {
      ok(await p.evaluate(() => { const g = document.getElementById('try-gate'), v = document.querySelector('#try-report .nl2');
        return !!g && !!v && !!(g.compareDocumentPosition(v) & Node.DOCUMENT_POSITION_FOLLOWING); }), name + ': the tripped gate does not lead the report');
    }
    ok(!p.__errs.length, name + ': page error: ' + p.__errs[0]);
    await p.close();
  }
});

// WCAG contrast of each heatmap cell's printed number against the cell's own fill
async function cellContrast(p) {
  return p.evaluate(() => {
    const rgb = (s) => {
      let m = /rgba?\(([^)]+)\)/.exec(s);
      if (m) { const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2]]; }
      m = /color\(srgb ([^)]+)\)/.exec(s);
      if (m) { const v = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [v[0] * 255, v[1] * 255, v[2] * 255]; }
      return null;
    };
    const lum = (c) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
    let worst = 99, where = '', n = 0;
    document.querySelectorAll('#try-report g.hc2').forEach((g) => {
      const r = g.querySelector('rect'), t = g.querySelector('.cv2');
      if (!r || !t || !r.getBoundingClientRect().width) return;
      const a = rgb(getComputedStyle(r).fill), b = rgb(getComputedStyle(t).fill);
      if (!a || !b) { worst = 0; where = 'unreadable colour ' + getComputedStyle(r).fill; return; }
      const L1 = lum(a), L2 = lum(b), cr = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      n++;
      if (cr < worst) { worst = cr; where = (g.closest('[data-chart]') || {}).getAttribute('data-chart') + ' ' + t.textContent; }
    });
    return { worst, where, n };
  });
}
for (const scheme of ['light', 'dark']) {
  check('try-v2-fits-a-phone-and-reads-in-' + scheme, PHONE, async (ctx) => {
    // the site opens light whatever the computer prefers; dark is the reader's choice, kept in localStorage
    await ctx.addInitScript((t) => { try { localStorage.setItem('nl-theme', t); } catch (e) { /* the check below fails */ } }, scheme);
    for (const name of ['sample', 'web-analytics']) {
      const { p, rep } = await openV2(ctx, name);
      ok((await p.evaluate(() => document.documentElement.getAttribute('data-theme'))) === scheme, name + ': the page is not in the ' + scheme + ' theme');
      for (const view of ['manager', 'analyst']) {
        await p.click('.nl2-views [data-view="' + view + '"]');
        await p.waitForTimeout(250);
        if (view === 'analyst' && await p.$('#nl2-analyst [data-act="corr"]')) { await p.click('#nl2-analyst [data-act="corr"]'); await p.waitForTimeout(100); }
        const r = await p.evaluate((v) => {
          const W = document.documentElement.clientWidth, P = document.getElementById('nl2-' + v);
          const svgs = Array.from(P.querySelectorAll('.viz svg')).filter((s) => s.getBoundingClientRect().width);
          const bad = svgs.map((s) => { const vb = s.viewBox.baseVal, b = s.getBoundingClientRect(), fig = s.closest('figure').getBoundingClientRect();
            return { id: s.closest('[data-chart]').getAttribute('data-chart'), k: b.width / vb.width, over: b.right - fig.right }; }).filter((x) => x.k < 0.97 || x.over > 0.5);
          const small = Array.from(P.querySelectorAll('.viz svg text')).filter((t) => t.getBoundingClientRect().width).map((t) => parseFloat(getComputedStyle(t).fontSize) * (t.ownerSVGElement.getBoundingClientRect().width / t.ownerSVGElement.viewBox.baseVal.width)).filter((f) => f < 10.5);
          const taps = Array.from(document.querySelectorAll('#try-report button, #try-report .btn')).filter((e) => e.offsetParent !== null && e.closest('#try-report') && !e.closest('[hidden]'))
            .map((e) => ({ t: (e.textContent || '').trim().slice(0, 24), h: e.getBoundingClientRect().height })).filter((x) => x.h < 43.5);
          return { sideways: document.documentElement.scrollWidth - W, bad, small: small.length, taps, n: svgs.length };
        }, view);
        ok(r.sideways <= 0, name + ' ' + view + ': the page scrolls sideways by ' + r.sideways + ' px on a phone');
        // and with every folded section opened, as a reader taps them (a chart drawn while its section was
        // closed, or a long unbroken word, must not push the page sideways)
        const opened = await p.evaluate(async (v) => {
          const P = document.getElementById('nl2-' + v), shut = Array.from(P.querySelectorAll('details:not([open])'));
          shut.forEach((d) => { d.open = true; });
          await new Promise((res) => setTimeout(res, 1500));
          const s = document.documentElement.scrollWidth - document.documentElement.clientWidth;
          shut.forEach((d) => { d.open = false; });
          return { s, n: shut.length };
        }, view);
        ok(opened.s <= 0, name + ' ' + view + ': with its ' + opened.n + ' folded sections open, the page scrolls sideways by ' + opened.s + ' px on a phone');
        ok(r.n > 0 && !r.bad.length, name + ' ' + view + ': charts shrunk or overflowing on a phone ' + JSON.stringify(r.bad.slice(0, 3)));
        ok(!r.small, name + ' ' + view + ': ' + r.small + ' chart labels under 10.5 px on a phone');
        ok(!r.taps.length, name + ' ' + view + ': touch targets under 44 px ' + r.taps.slice(0, 3).map((x) => x.t + ' ' + Math.round(x.h)).join(', '));
        if (view === 'analyst') {
          // a narrow map turns its categories into column headers: each one must still be named
          const labs = await p.evaluate(() => Array.from(document.querySelectorAll('#nl2-analyst [data-chart^="catmonth."]')).map((f) => [f.getAttribute('data-chart'), Array.from(f.querySelectorAll('.viz svg text.lab')).map((t) => t.textContent)]));
          labs.forEach(([id, labels]) => {
            const named = (k) => labels.some((l) => l === k || (l.endsWith('…') && k.indexOf(l.slice(0, -1)) === 0));
            const miss = chartOf(rep, id).data.categories.filter((k) => !named(k));
            ok(!miss.length, name + ' ' + id + ': categories with no label on a phone: ' + miss.join(', '));
          });
        }
        const c = await cellContrast(p);
        ok(c.n > 0 || view === 'manager', name + ' ' + view + ': no heatmap cells found');
        ok(c.worst >= 4.5, name + ' ' + view + ' (' + scheme + '): a heatmap number has contrast ' + c.worst.toFixed(2) + ' on its cell (' + c.where + ')');
      }
      ok(!p.__errs.length, name + ': page error: ' + p.__errs[0]);
      await p.close();
    }
  }, { mobile: true, colorScheme: scheme });
}

check('try-v2-save-as-pdf-prints-both-views-as-a-paper', DESK, async (ctx) => {
  const { p, rep } = await openV2(ctx, 'sales-ledger');
  const title0 = await p.title();
  await p.evaluate(() => { window.print = () => {
    const A = document.getElementById('nl2-analyst'), M = document.getElementById('nl2-manager'), R = document.getElementById('try-report');
    const shown = (e) => !!e && getComputedStyle(e).display !== 'none';
    const rule = document.getElementById('nl-print-page');
    window.__printed = { cls: document.body.classList.contains('print-try'), m: shown(M), a: shown(A),
      drawn: Array.from(A.querySelectorAll('figure[data-chart] .viz')).filter((v) => !v.querySelector('svg, table')).length,
      figs: A.querySelectorAll('figure[data-chart]').length, closed: document.querySelectorAll('#try-report details:not([open])').length,
      // the cover prints first: before the report (and before the AI's plan card when there is one)
      cover: (function () { const c = document.querySelector('#try-app > .tr-print-cover'); return c && (c.compareDocumentPosition(R) & 4) ? c.textContent : ''; })(),
      title: document.title, rule: rule ? rule.textContent : '', wbr: document.querySelectorAll('#try-report .nl2 code wbr').length }; }; });
  await p.click('#try-report [data-act="print"]');
  await p.waitForTimeout(150);
  const pr = await p.evaluate(() => window.__printed);
  ok(pr && pr.cls && pr.m && pr.a && pr.figs > 0 && pr.drawn === 0 && pr.closed === 0, 'while printing: ' + JSON.stringify(pr).slice(0, 300));
  // the paper version: a cover, the report's own title (never the file's name) as the browser's file name, and the
  // running header with "Page X of Y" as an @page rule that exists only while printing
  ok(/NorthLedger Insights/i.test(pr.cover) && /Confidential/.test(pr.cover) && pr.cover.indexOf(rep.input.name) < 0, 'no print cover, or it names the file: ' + pr.cover.slice(0, 200));
  ok(/nothing was uploaded/.test(pr.cover) && !/Sent to the AI/.test(pr.cover), 'a run without the AI does not say nothing was uploaded: ' + pr.cover.slice(0, 300));
  ok(/^NorthLedger report - \d{4}-\d\d-\d\d$/.test(pr.title), 'the title while printing is not "NorthLedger report - <date>": ' + pr.title);
  ok(/counter\(pages\)/.test(pr.rule) && /NORTHLEDGER INSIGHTS/.test(pr.rule) && /@page :first/.test(pr.rule), 'no running header and "Page X of Y" while printing: ' + pr.rule.slice(0, 200));
  ok(pr.wbr > 0, 'long machine ids do not break at their dots and underscores on paper');
  const after = await p.evaluate(() => ({ title: document.title, cover: !!document.querySelector('.tr-print-cover'), rule: !!document.getElementById('nl-print-page'), wbr: document.querySelectorAll('#try-report .nl2 code wbr').length }));
  ok(after.title === title0 && !after.cover && !after.rule && !after.wbr, 'the page did not go back after printing: ' + JSON.stringify(after));
  await p.evaluate(() => document.body.classList.add('print-try'));
  await p.emulateMedia({ media: 'print' });
  const css = await p.evaluate(() => {
    const vis = (s) => Array.from(document.querySelectorAll(s)).filter((e) => getComputedStyle(e).display !== 'none').length;
    const sec = (k) => getComputedStyle(document.querySelector('#nl2-analyst section.nl2-sec[data-sec="' + k + '"]')).breakBefore;
    const lastTh = document.querySelector('#nl2-analyst table.nl2-ftab[data-cols="analyst"] thead th:last-child');
    return { views: vis('.nl2-views'), links: vis('#try-report .nl2-link'), tbl: vis('#try-report .tbl-btn'), m: vis('#nl2-manager'), a: vis('#nl2-analyst'),
      figBreak: getComputedStyle(document.querySelector('#nl2-analyst figure[data-chart]')).breakInside, summary: sec('summary'), data: sec('data'), appendix: sec('appendix'),
      layout: [getComputedStyle(document.getElementById('try-report')).display, getComputedStyle(document.querySelector('#try-report .nl2')).display, getComputedStyle(document.getElementById('nl2-analyst')).display],
      lead: vis('#try-report .tr-head-compact'), settle: lastTh ? getComputedStyle(lastTh).display : 'none' };
  });
  ok(!css.views && !css.links && !css.tbl && css.m && css.a, 'print shows the view switch or buttons, or leaves a view out: ' + JSON.stringify(css));
  ok(css.figBreak === 'avoid' && css.summary === 'page' && css.appendix === 'page' && css.data !== 'page',
    'print can split a chart, or a page break is forced for other than the major parts: ' + JSON.stringify(css));
  ok(css.layout.every((d) => d === 'block'), 'the report prints as a grid (the next card lands over the last pages): ' + JSON.stringify(css.layout));
  ok(!css.lead && css.settle === 'none', 'print keeps the near-empty lead card or the repeated "What would settle it" column: ' + JSON.stringify(css));
  ok(rep.charts.length > 0 && !p.__errs.length, 'page error: ' + p.__errs[0]);
});

// The engine report's "Save as PDF" with the AI's plan card on the page (live baseline, 30 Sep 2026: the plan card
// printed first, pages 1 to 3, and the cover began halfway down page 4). The cover is page 1: it goes before everything
// that prints, the plan card starts page 2, and the page goes back as it was. The PDF is made by Chrome from the page in
// its printing state (printReport restores it on the next tick; the check holds it there until the PDF is made) and
// read page by page with poppler's pdftotext.
check('try-save-as-pdf-puts-the-cover-on-page-1-before-the-ai-plan-card', DESK, async (ctx) => {
  const rep = stubReport(); rep.__profile = true;
  const plan = { goal: 'Which region grows fastest?', understanding: 'Orders by region.', quality_risks: ['few months'],
    operations: [{ op: 'set_aside', columns: ['notes'] }], analyses: [{ type: 'trend', columns: ['amount'] }] };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status: 200, json: { plan } } : { status: 503, json: { error: 'x' } } });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden])');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  ok(await p.isVisible('#try-plan-card'), 'no AI plan card beside the report');
  await p.evaluate(() => { window.print = () => { const st = window.setTimeout; window.setTimeout = (fn) => { window.setTimeout = st; window.__restore = fn; return 0; }; }; });
  await p.click('#try-report [data-act="print"]');
  await p.emulateMedia({ media: 'print' });
  const at = await p.evaluate(() => {
    const cover = document.querySelector('.tr-print-cover'), card = document.getElementById('try-plan-card');
    const shown = (e) => { for (let x = e; x && x !== document.documentElement; x = x.parentElement) if (getComputedStyle(x).display === 'none') return false; return true; };
    const printed = Array.from(document.querySelectorAll('#try-app > *')).filter((e) => shown(e) && e.getBoundingClientRect().height > 0);
    return { printed: printed.map((e) => e.id || e.className), inReport: !!(cover && cover.closest('#try-report')),
      coverTop: cover ? cover.getBoundingClientRect().top : null, cardTop: card.getBoundingClientRect().top,
      breakAfter: cover ? getComputedStyle(cover).breakAfter : '', h1: cover ? cover.querySelector('h1').textContent : '',
      prev: shown(document.getElementById('try-prev')) };
  });
  ok(at.printed[0] === 'tr-print-cover' && at.coverTop < at.cardTop && at.breakAfter === 'page' && !at.inReport,
    'the cover is not the first thing that prints: ' + JSON.stringify(at));
  ok(!at.prev, 'the list of previous reports prints inside the report');
  const pdf = await p.pdf({ format: 'Letter', printBackground: true, preferCSSPageSize: true });
  await p.evaluate(() => { if (window.__restore) window.__restore(); });
  const f = path.join(require('os').tmpdir(), 'nl-ui-cover-' + process.pid + '.pdf');
  fs.writeFileSync(f, pdf);
  try {
    const page = (i) => require('child_process').spawnSync('pdftotext', ['-f', String(i), '-l', String(i), '-layout', f, '-'], { encoding: 'utf8' });
    const p1 = page(1);
    if (p1.error) console.log('   (pdftotext not found: the order on the page was checked, the PDF\'s pages were not)');
    else {
      const t1 = p1.stdout.replace(/\s+/g, ' '), t2 = page(2).stdout.replace(/\s+/g, ' ');
      ok(/NorthLedger Insights/i.test(t1) && /Confidential/.test(t1) && t1.indexOf(at.h1.split(' ').slice(0, 3).join(' ')) >= 0,
        'page 1 of the PDF is not the cover: ' + t1.slice(0, 300));
      ok(!/The AI's plan for this file/.test(t1) && /The AI's plan for this file/.test(t2), 'the AI plan card is not where page 2 starts: ' + t2.slice(0, 200));
    }
  } finally { fs.unlinkSync(f); }
  const after = await p.evaluate(() => ({ cover: !!document.querySelector('.tr-print-cover'), cls: document.body.classList.contains('print-try'), rule: !!document.getElementById('nl-print-page') }));
  ok(!after.cover && !after.cls && !after.rule, 'the page did not go back after printing: ' + JSON.stringify(after));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-v2-falls-back-to-the-checked-v1-report-when-the-v2-blocks-are-empty-or-broken', DESK, async (ctx) => {
  const empty = fixture('rent-roll');
  empty.charts = []; empty.charts_suppressed = [];
  empty.limitations = [{ kind: 'data', text: 'The confidence details for this report could not be built in this browser; the checked findings below are the v1 report.', finding_ids: [] }];
  let { p } = await openV2(ctx, 'rent-roll', empty);
  let d = await p.evaluate(() => ({ v2: !!document.querySelector('#try-report .nl2-views'), v1: !!document.querySelector('#try-report .tr-flist'), note: (document.querySelector('#try-report .nl2-fallback') || {}).textContent || '' }));
  ok(!d.v2 && d.v1 && /could not be built in this browser/.test(d.note), 'an empty v2 report is not shown as the v1 report with its limitation: ' + JSON.stringify(d));
  await p.close();
  const broken = fixture('rent-roll');
  broken.charts[1].data = null;
  ({ p } = await openV2(ctx, 'rent-roll', broken));
  d = await p.evaluate(() => ({ v2: !!document.querySelector('#try-report .nl2-views'), v1: !!document.querySelector('#try-report .tr-flist'), note: (document.querySelector('#try-report .nl2-fallback') || {}).textContent || '' }));
  ok(!d.v2 && d.v1 && /could not be shown/.test(d.note) && d.note.indexOf(broken.charts[1].id) >= 0, 'a broken v2 block is drawn, or the fallback does not say what was wrong: ' + JSON.stringify(d));
  await p.close();
  const short = fixture('rent-roll'), tr = short.charts.filter((c) => c.type === 'trend_windows')[0];
  tr.data.values = tr.data.values.slice(1);            // one month fewer values than months
  ({ p } = await openV2(ctx, 'rent-roll', short));
  d = await p.evaluate(() => ({ v2: !!document.querySelector('#try-report .nl2-views'), note: (document.querySelector('#try-report .nl2-fallback') || {}).textContent || '' }));
  ok(!d.v2 && d.note.indexOf(tr.id) >= 0, 'chart data whose lengths disagree are drawn: ' + JSON.stringify(d));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

/* ------------------------------------------------------------ the chart registry, page side (tools/fixtures/viz/spec.json)
   window.NLV (src/js/55-nl-viz.js) draws each viz record by its draw kind, or its table: the spec's 14 examples (and the
   engine's own ship2 records once tools/fixtures/viz/engine-charts-ship2.json exists), fed to the page as the engine's
   v2 records (type "viz", rule V) inside a real adapter report, and as the AI report's [CHART:n] figures. */
const VIZ_DIR = path.join(SITE_DIR, 'tools', 'fixtures', 'viz');
const VIZ_SPEC = JSON.parse(fs.readFileSync(path.join(VIZ_DIR, 'spec.json'), 'utf8'));
const VIZ_DRAWN = ['waterfall', 'heatmap', 'dot_range', 'pareto', 'slope'];
const VIZ_GLYPH = { 1: '○', 2: '◐', 3: '●' };
const vizCopy = (x) => JSON.parse(JSON.stringify(x));
// the engine's own records (tools/test_nl_viz.py --write): {engine_pick: {viz}, ai_pick: {viz}}, each viz as rep.viz holds it;
// a plain list, {charts} or {viz: {charts}} is read too
function vizEngineRecords() {
  const f = path.join(VIZ_DIR, 'engine-charts-ship2.json');
  if (!fs.existsSync(f)) return [];
  const j = JSON.parse(fs.readFileSync(f, 'utf8')), out = [];
  const take = (list, who) => (Array.isArray(list) ? list : []).forEach((c) => {
    const r = c && c.type === 'viz' ? c.data : c;
    if (r && typeof r.kind === 'string' && r.table && r.id) out.push(Object.assign(vizCopy(r), { __who: who }));
  });
  if (Array.isArray(j)) take(j, 'engine');
  else {
    take(j.records, 'engine'); take(j.charts, 'engine'); take(j.viz && j.viz.charts, 'engine');
    ['engine_pick', 'ai_pick'].forEach((k) => take(j[k] && j[k].viz && j[k].viz.charts, k.replace('_', ' ')));
  }
  return out;
}
// every record the checks draw, each with an id unique on the page (the spec's pattern viz.<n>.<chart>, numbered after the
// engine's own, at most VIZ_MAX of them, so the adapter's report keeps its records)
function vizRecords(extra) {
  const recs = Object.entries(VIZ_SPEC.examples).map(([k, e]) => Object.assign(vizCopy(e.record), { __name: k }))
    .concat(vizEngineRecords().map((r) => { const c = vizCopy(r); c.__name = r.__who + ' ' + r.id; delete c.__who; return c; }), extra || []);
  recs.forEach((r, i) => { r.id = 'viz.' + (i + 1 + VIZ_SPEC.caps.VIZ_MAX) + '.' + String(r.chart || 'chart').replace(/[^a-z_]/g, '').slice(0, 24); });
  return recs;
}
// a record of a kind no reader knows (it must be drawn as its table), and a heatmap whose data are malformed
function vizOddRecords() {
  const unknown = Object.assign(vizCopy(VIZ_SPEC.examples.table.record), { chart: 'sankey', kind: 'sankey', data: { flows: [[0, 1, 41]] }, title: 'Orders flowing from region to warehouse', __name: 'unknown kind' });
  delete unknown.degraded;
  const broken = Object.assign(vizCopy(VIZ_SPEC.examples.heatmap.record), { title: 'A heatmap whose rows and values disagree', __name: 'malformed heatmap' });
  broken.data.values = broken.data.values.slice(1);
  return [unknown, broken];
}
function vizV2(rec) {
  const fids = (rec.anchors || []).filter((a) => a.indexOf('finding:') === 0).map((a) => a.slice(8));
  return { id: rec.id, rule: 'V', type: 'viz', title: rec.title, view: 'manager', default_visible: true, finding_ids: fids, why_shown: rec.why, source: rec.source, data: rec };
}
// a real adapter report with the viz records appended as the engine appends them (and rep.viz with the spec's refusals)
function vizReport(recs, name) {
  const rep = vizCopy(fixture(name || 'sample'));
  recs.forEach((r) => { const c = vizCopy(r); delete c.__name; rep.charts.push(vizV2(c)); });
  rep.viz = { version: VIZ_SPEC.version, charts: recs.map((r) => { const c = vizCopy(r); delete c.__name; return c; }), refused: vizCopy(VIZ_SPEC.plan_directive.example.refused), chosen_by: 'ai' };
  if (!rep.charts_suppressed.some((x) => x.rule === '#2b')) rep.charts_suppressed.push({ rule: '#2b', type: 'driver_waterfall', why: 'which segments drive a change is not computed in this release' });
  return rep;
}
const vizLabel = (r) => String(r.title || 'Chart').replace(/[.\s]+$/, '') + (r.summary ? '. ' + r.summary : '');
// the kind the page must draw a record as: its own when valid, else its table (the checks' own reading of the spec)
function vizKind(r) {
  if (VIZ_DRAWN.indexOf(r.kind) < 0) return 'table';
  const d = r.data, a = Array.isArray;
  if (r.kind === 'heatmap') return a(d.rows) && ['values', 'text', 'tier'].every((k) => a(d[k]) && d[k].length === d.rows.length && d[k].every((x) => a(x) && x.length === d.cols.length)) ? 'heatmap' : 'table';
  return 'drawn';
}
// what a figure shows: its svg's name and focusability, its Table button, its table (pressing the button), every heatmap cell
async function vizFigure(p, sel, rec) {
  return p.evaluate(async ([sel, rec]) => {
    const f = document.querySelector(sel);
    if (!f) return { missing: true };
    const svg = f.querySelector('.viz svg.nlv'), btn = f.querySelector('.tbl-btn');
    const rows = (root) => Array.from(root.querySelectorAll('table tbody tr')).map((tr) => Array.from(tr.children).map((c) => c.textContent.replace(/\s+/g, ' ').trim()));
    const out = { fig: f.getAttribute('aria-label'), svg: !!svg, name: svg && svg.getAttribute('aria-label'), role: svg && svg.getAttribute('role'), tab: svg && svg.getAttribute('tabindex'),
      btn: !!btn, direct: rows(f.querySelector('.viz')), why: (f.querySelector('.nlv-why') || {}).textContent || '', cells: [], texts: svg ? Array.from(svg.querySelectorAll('text')).map((t) => t.textContent) : [] };
    if (svg && rec.kind === 'heatmap') {
      out.nCells = svg.querySelectorAll('g.hc2, g.hc2-null').length;
      svg.querySelectorAll('g.hc2[data-cell]').forEach((g) => { const c = g.querySelector('.cv2'), gl = g.querySelector('.gl2'); out.cells.push([g.getAttribute('data-cell'), c ? c.textContent : null, gl ? gl.textContent : '']); });
    }
    if (btn) {
      btn.click(); await new Promise((r) => setTimeout(r, 60));
      out.table = rows(f.querySelector('.viz'));
      out.sticky = (() => { const th = f.querySelector('.viz tbody th'); return th ? getComputedStyle(th).position : null; })();
      btn.click(); await new Promise((r) => setTimeout(r, 60));
      out.back = !!f.querySelector('.viz svg.nlv');
    }
    return out;
  }, [sel, rec]);
}
function vizFigureOk(d, rec, where) {
  const kind = vizKind(rec), want = rec.table.rows.map((r) => r.map((c) => String(c).replace(/\s+/g, ' ').trim()));
  ok(!d.missing, where + ': no figure');
  if (kind === 'table') {
    ok(!d.svg && !d.btn && JSON.stringify(d.direct) === JSON.stringify(want), where + ' (' + rec.__name + ') is not drawn as its table, with no Table button: ' + JSON.stringify({ svg: d.svg, btn: d.btn, rows: d.direct.length }));
    if (VIZ_DRAWN.indexOf(rec.kind) < 0 && rec.kind !== 'table') ok(/does not draw a chart of this kind \(/.test(d.why), where + ': an unknown kind does not say why it is a table: ' + d.why);
    return;
  }
  ok(d.svg && d.name === vizLabel(rec) && d.role === 'img' && d.tab === '0', where + ' (' + rec.__name + '): the chart has no svg named by its title and summary, or is not focusable: ' + JSON.stringify([d.svg, d.name && d.name.slice(0, 80), d.role, d.tab]));
  ok(d.fig === vizLabel(rec), where + ': the figure is not named by the title and summary: ' + d.fig);
  ok(d.btn && JSON.stringify(d.table) === JSON.stringify(want) && d.back, where + ' (' + rec.__name + '): the Table button does not show the record\'s table and back: ' + JSON.stringify({ btn: d.btn, rows: (d.table || []).length, want: want.length, back: d.back }));
  ok(d.sticky === 'sticky', where + ': the table view\'s first column does not stay put when it scrolls (' + d.sticky + ')');
  if (rec.kind === 'heatmap') {
    const H = rec.data, bad = [];
    ok(d.nCells === H.rows.length * H.cols.length, where + ': ' + d.nCells + ' cells for ' + H.rows.length + ' x ' + H.cols.length);
    const got = {};
    d.cells.forEach(([k, t, g]) => { got[k] = [t, g]; });
    H.values.forEach((row, i) => row.forEach((v, j) => {
      const c = got[i + ',' + j], tier = H.tier[i][j];
      if (v !== null && (!c || !c[0] || c[0] !== H.text[i][j])) bad.push(i + ',' + j + ' prints ' + JSON.stringify(c && c[0]) + ' for ' + H.text[i][j]);
      if (v !== null && (c ? c[1] : '') !== (tier ? VIZ_GLYPH[Math.abs(tier)] : '')) bad.push(i + ',' + j + ' glyph ' + JSON.stringify(c && c[1]) + ' for tier ' + tier);
      if (v === null && H.text[i][j] === '<5' && (!c || c[0] !== '<5')) bad.push(i + ',' + j + ' does not read <5');
    }));
    ok(!bad.length, where + ' (' + rec.__name + '): cells without their number or glyph: ' + bad.slice(0, 3).join('; '));
  }
  if (rec.kind === 'waterfall') ok(rec.data.steps.every((s) => d.texts.indexOf(s.text) >= 0), where + ': a step\'s text is not printed: ' + rec.data.steps.filter((s) => d.texts.indexOf(s.text) < 0).map((s) => s.text).join(', '));
  if (rec.kind === 'pareto') ok(rec.data.bars.every((b) => d.texts.indexOf(b.text) >= 0), where + ': a bar\'s text is not printed');
  if (rec.kind === 'slope') ok(rec.data.rows.every((r) => d.texts.indexOf(r.change_text) >= 0), where + ': a row\'s change is not printed');
}

check('viz-every-kind-draws-with-its-name-and-a-table-button', DESK, async (ctx) => {
  const recs = vizRecords(vizOddRecords()), rep = vizReport(recs);
  const { p } = await openV2(ctx, 'sample', rep);
  const st = await p.evaluate(() => ({ v2: !!document.querySelector('#try-report .nl2-views'), fb: (document.querySelector('#try-report .nl2-fallback') || {}).textContent || '',
    drivers: Array.from(document.querySelectorAll('#nl2-manager .nl2-drivers')).map((x) => x.textContent).join(' '),
    kick: Array.from(document.querySelectorAll('#nl2-manager .nl2-viz [data-drivers="1"]')).map((f) => [f.getAttribute('data-chart'), (f.querySelector('.nl2-kick') || {}).textContent]) }));
  ok(st.v2 && !st.fb, 'the report with viz records fell back to v1: ' + st.fb.slice(0, 200));
  // the report's first contribution waterfall (the engine's own when it built one) shows the drivers
  const wf = rep.charts.filter((c) => c.type === 'viz' && c.data.chart === 'contribution_waterfall' && c.data.kind === 'waterfall')[0];
  ok(!/Top drivers.*not shown/.test(st.drivers) && st.kick.length === 1 && st.kick[0][0] === wf.id && /Top drivers/.test(st.kick[0][1]),
    'the "Top drivers ... not shown" line did not become the contribution waterfall: ' + JSON.stringify(st));
  for (const r of recs) vizFigureOk(await vizFigure(p, '[id="nl2c-m-' + r.id + '"]', r), r, 'manager ' + r.id);
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(200);
  for (const r of recs.slice(0, 6)) vizFigureOk(await vizFigure(p, '[id="nl2c-a-' + r.id + '"]', r), r, 'analyst ' + r.id);
  // the analyst view lists the refused charts in the engine's words, and no longer says the drivers are not computed
  const sup = await p.evaluate(() => ({ refused: Array.from(document.querySelectorAll('#nl2-analyst .nl2-supp li[data-refused]')).map((li) => [li.getAttribute('data-refused'), li.textContent]),
    rules: Array.from(document.querySelectorAll('#nl2-analyst .nl2-supp li[data-rule]')).map((li) => li.getAttribute('data-rule')) }));
  const R = VIZ_SPEC.plan_directive.example.refused;
  ok(sup.refused.length === R.length && R.every((x, i) => sup.refused[i][0] === x.chart && sup.refused[i][1].indexOf(x.why) >= 0 && /asked for by the AI/.test(sup.refused[i][1])),
    'the refused charts are not listed with the engine\'s reasons: ' + JSON.stringify(sup.refused));
  ok(sup.rules.indexOf('#2b') < 0, 'the analyst view still lists the driver waterfall as not computed beside the contribution waterfall');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the layout on phones and a desktop: no sideways page, no chart shrunk or spilling out of its figure, no text outside its
// drawing or on top of another, no text under 10.5 px, and every clipped label whole in its tooltip
async function vizFit(p) {
  return p.evaluate(() => {
    const W = document.documentElement.clientWidth, bad = [];
    Array.from(document.querySelectorAll('#nl2-manager .nl2-viz figure')).forEach((f) => {
      const id = f.getAttribute('data-chart'), svg = f.querySelector('.viz svg.nlv');
      if (!svg) return;
      const sb = svg.getBoundingClientRect(), fb = f.getBoundingClientRect(), k = sb.width / svg.viewBox.baseVal.width;
      if (!sb.width) return;                  // a view not on screen
      if (k < 0.97 || sb.right > fb.right + 0.5 || sb.left < fb.left - 0.5) bad.push(id + ' shrunk or spilling (scale ' + k.toFixed(2) + ')');
      const T = Array.from(svg.querySelectorAll('text')).filter((t) => t.textContent.trim() && t.getBoundingClientRect().width);
      const R = T.map((t) => t.getBoundingClientRect());
      T.forEach((t, i) => {
        const q = R[i];
        if (q.left < sb.left - 0.5 || q.right > sb.right + 0.5 || q.top < sb.top - 0.5 || q.bottom > sb.bottom + 0.5) bad.push(id + ' text outside its drawing: ' + t.textContent);
        const els = [t].concat(Array.from(t.querySelectorAll('tspan')));
        els.forEach((e) => { const fs = parseFloat(getComputedStyle(e).fontSize) * k; if (fs < 10.5) bad.push(id + ' text under 10.5 px: ' + e.textContent + ' ' + fs.toFixed(1)); });
        if (/…$/.test(t.textContent)) { const full = t.getAttribute('data-full') || ''; if (!full || full.indexOf(t.textContent.slice(0, -1).trim()) !== 0) bad.push(id + ' clipped label without its whole text: ' + t.textContent); }
      });
      for (let i = 0; i < T.length; i++) for (let j = i + 1; j < T.length; j++) {
        const a = R[i], c = R[j];
        if (a.left < c.right - 0.5 && c.left < a.right - 0.5 && a.top < c.bottom - 1 && c.top < a.bottom - 1) bad.push(id + ' labels collide: "' + T[i].textContent + '" and "' + T[j].textContent + '"');
      }
    });
    // what pushes the page sideways, when something does: the innermost elements past the right edge
    const clipped = (e) => { for (let x = e.parentElement; x && x !== document.body; x = x.parentElement) { const o = getComputedStyle(x).overflowX; if (o !== 'visible') return true; } return false; };
    const past = Array.from(document.querySelectorAll('body *')).filter((e) => e.getBoundingClientRect().right > W + 1 && e.getBoundingClientRect().width && !clipped(e) && !Array.from(e.children).some((c) => c.getBoundingClientRect().right > W + 1))
      .map((e) => (e.closest('#try-report') ? 'report: ' : 'outside the report: ') + e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '') + ' ' + Math.round(e.getBoundingClientRect().right) + 'px "' + (e.textContent || '').trim().slice(0, 40) + '"');
    return { sideways: document.documentElement.scrollWidth - W, bad, past, n: document.querySelectorAll('#nl2-manager .nl2-viz svg.nlv').length };
  });
}
for (const w of [320, 360, 390]) {
  check('viz-fits-a-' + w + '-px-phone', { width: w, height: 800 }, async (ctx) => {
    const planned = fixture('sales-ledger-charts');
    ok(!planned.__fixture_error, 'the adapter could not make the planned charts run (tools/make_ui_fixtures.py): ' + planned.__fixture_error);
    const o2 = await openV2(ctx, 'sales-ledger-charts', planned);
    await o2.p.waitForTimeout(250);
    const r2 = await vizFit(o2.p);
    // the report itself (the plan card's data tests table of a planned run is outside it: it is noted, not judged here)
    const inRep = r2.past.filter((x) => x.indexOf('report: ') === 0);
    ok(r2.n === planned.viz.charts.filter((x) => vizKind(x) !== 'table').length && !inRep.length && !r2.bad.length,
      'the engine\'s own charts at ' + w + ' px: ' + r2.n + ' drawn; past the edge: ' + inRep.slice(0, 3).join('; ') + '; ' + r2.bad.slice(0, 4).join(' | '));
    if (r2.sideways > 0) console.log('   note: at ' + w + ' px the planned run\'s page is ' + r2.sideways + ' px too wide: ' + r2.past.slice(0, 2).join('; '));
    ok(!o2.p.__errs.length, 'page error: ' + o2.p.__errs[0]);
    await o2.p.close();
    const recs = vizRecords(), { p } = await openV2(ctx, 'sample', vizReport(recs));
    await p.waitForTimeout(250);
    const r = await vizFit(p);
    ok(r.n >= recs.filter((x) => vizKind(x) !== 'table').length, r.n + ' charts drawn at ' + w + ' px');
    ok(r.sideways <= 0, 'the page scrolls sideways by ' + r.sideways + ' px at ' + w + ' px: ' + r.past.slice(0, 3).join('; '));
    ok(!r.bad.length, r.bad.length + ' layout faults at ' + w + ' px: ' + r.bad.slice(0, 4).join(' | '));
    // every table view, opened, scrolls inside its own box
    const tv = await p.evaluate(async () => {
      const B = Array.from(document.querySelectorAll('#nl2-manager .nl2-viz .tbl-btn'));
      B.forEach((b) => b.click());
      await new Promise((res) => setTimeout(res, 300));
      const s = document.documentElement.scrollWidth - document.documentElement.clientWidth;
      B.forEach((b) => b.click());
      return { s, n: B.length };
    });
    ok(tv.n > 0 && tv.s <= 0, 'with the ' + tv.n + ' table views open the page scrolls sideways by ' + tv.s + ' px');
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  }, { mobile: true });
}
check('viz-fits-a-desktop', DESK, async (ctx) => {
  const recs = vizRecords(), { p } = await openV2(ctx, 'sample', vizReport(recs));
  await p.waitForTimeout(250);
  const r = await vizFit(p);
  ok(r.sideways <= 0 && !r.bad.length, 'desktop layout faults: sideways ' + r.sideways + '; ' + r.bad.slice(0, 4).join(' | '));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the fills resolve to the spec's colours in each theme (the build's tokens), and each cell's number reads at 4.5:1
for (const scheme of ['light', 'dark']) {
  check('viz-colours-are-the-' + scheme + '-tokens', PHONE, async (ctx) => {
    await ctx.addInitScript((t) => { try { localStorage.setItem('nl-theme', t); } catch (e) { /* the check below fails */ } }, scheme);
    const recs = vizRecords(), { p } = await openV2(ctx, 'sample', vizReport(recs));
    ok((await p.evaluate(() => document.documentElement.getAttribute('data-theme'))) === scheme, 'the page is not in the ' + scheme + ' theme');
    const C = VIZ_SPEC.colors[scheme], hex = (h) => { const x = h.replace('#', ''); return 'rgb(' + [0, 2, 4].map((i) => parseInt(x.slice(i, i + 2), 16)).join(', ') + ')'; };
    const bad = [];
    for (const r of recs.filter((x) => x.kind === 'heatmap' && vizKind(x) === 'heatmap')) {
      const fills = await p.evaluate((id) => Array.from(document.querySelectorAll('[id="nl2c-m-' + id + '"] g.hc2[data-cell] rect')).map((e) => [e.parentNode.getAttribute('data-cell'), getComputedStyle(e).fill]), r.id);
      fills.forEach(([k, fill]) => {
        const [i, j] = k.split(',').map(Number), t = r.data.tier[i][j], v = r.data.values[i][j];
        const want = v === null ? C.suppressed.fill : !t ? C.neutral.fill : r.data.scale === 'diverging' ? (t < 0 ? C.falls : C.rises)[Math.abs(t)] : C.sequential[t];
        if (fill !== hex(want)) bad.push(r.__name + ' ' + k + ' ' + fill + ' (want ' + want + ')');
      });
    }
    const bars = await p.evaluate(() => Array.from(document.querySelectorAll('#nl2-manager .nl2-viz rect.w-tot, #nl2-manager .nl2-viz rect.w-rise, #nl2-manager .nl2-viz rect.w-fall')).map((e) => [e.getAttribute('class'), getComputedStyle(e).fill]));
    const WF = { 'w-tot': C.waterfall.total, 'w-rise': C.waterfall.rise, 'w-fall': C.waterfall.fall };
    ok(bars.length > 0, 'no waterfall bars found');
    bars.forEach(([c, fill]) => { if (fill !== hex(WF[c])) bad.push(c + ' ' + fill + ' (want ' + WF[c] + ')'); });
    ok(!bad.length, bad.length + ' fills are not the ' + scheme + ' tokens: ' + bad.slice(0, 4).join('; '));
    const cc = await cellContrast(p);
    ok(cc.n > 0 && cc.worst >= 4.5, 'a heatmap number has contrast ' + cc.worst.toFixed(2) + ' on its cell in the ' + scheme + ' theme (' + cc.where + ')');
    ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  }, { mobile: true, colorScheme: scheme });
}

// the engine's own charts for an AI plan (tools/make_ui_fixtures.py sales-ledger-charts): each drawn with its name and a
// Table button, the engine's refusals listed under "Charts not drawn, and why" in its words
check('viz-the-engine-s-charts-for-an-ai-plan-draw-and-its-refusals-are-listed', DESK, async (ctx) => {
  const rep = fixture('sales-ledger-charts');
  ok(!rep.__fixture_error, 'the adapter could not make the planned charts run (tools/make_ui_fixtures.py): ' + rep.__fixture_error);
  const V = rep.charts.filter((c) => c.type === 'viz');
  ok(V.length === rep.viz.charts.length && V.length > 0 && rep.viz.chosen_by === 'ai', 'the report\'s viz records and rep.viz.charts disagree: ' + V.length + ' and ' + rep.viz.charts.length);
  const { p } = await openV2(ctx, 'sales-ledger-charts', rep);
  ok(await p.evaluate(() => !!document.querySelector('#try-report .nl2-views') && !document.querySelector('#try-report .nl2-fallback')), 'the planned run\'s report fell back to v1');
  for (const c of V) vizFigureOk(await vizFigure(p, '[id="nl2c-m-' + c.id + '"]', c.data), Object.assign({ __name: c.data.chart }, c.data), 'manager ' + c.id);
  const why = await p.evaluate(() => Array.from(document.querySelectorAll('#nl2-manager .nl2-viz figure .nl2-why')).map((x) => x.textContent));
  ok(why.length === V.length && why.every((t) => /^Asked for by the AI: /.test(t)), 'the AI\'s reasons are not shown under its charts: ' + JSON.stringify(why.slice(0, 2)));
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(200);
  const refused = await p.evaluate(() => Array.from(document.querySelectorAll('#nl2-analyst .nl2-supp li[data-refused]')).map((li) => [li.getAttribute('data-refused'), li.textContent]));
  ok(refused.length === rep.viz.refused.length && rep.viz.refused.every((x, i) => refused[i][0] === x.chart && refused[i][1].indexOf(x.why) >= 0),
    'the engine\'s refusals are not listed in its words: ' + JSON.stringify(refused) + ' for ' + JSON.stringify(rep.viz.refused));
  const r = await vizFit(p);
  ok(r.sideways <= 0, 'the page scrolls sideways by ' + r.sideways + ' px');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the chart review of 30 Sep 2026: a CSV header that is markup, in a chart the AI asked for and the engine refused
// (tools/make_ui_fixtures.py viz-hostile-header, a real adapter run): the analyst view's "Charts not drawn, and why"
// names it as text, and no element is made from it (it went into the page through innerHTML unescaped)
check('viz-a-refused-chart-names-a-markup-header-as-text', DESK, async (ctx) => {
  const HOSTILE = '<img src=x onerror=alert(1)>';
  const rep = fixture('viz-hostile-header');
  ok(!rep.__fixture_error, 'the adapter could not make the hostile-header run (tools/make_ui_fixtures.py): ' + rep.__fixture_error);
  ok(rep.viz.refused.some((x) => (x.columns || []).indexOf(HOSTILE) >= 0), 'the engine did not refuse the chart on the header: ' + JSON.stringify(rep.viz.refused));
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  const dialogs = [];
  p.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss().catch(() => {}); });
  await runReport(p);
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(300);
  const st = await p.evaluate((h) => ({
    items: Array.from(document.querySelectorAll('#nl2-analyst .nl2-supp li[data-refused]')).map((li) => li.textContent),
    made: Array.from(document.querySelectorAll('#try-report img, #try-report [onerror], #try-report script')).map((e) => e.outerHTML.slice(0, 80)),
    html: Array.from(document.querySelectorAll('#nl2-analyst .nl2-supp li[data-refused]')).map((li) => li.innerHTML).join(' ')
  }), HOSTILE);
  ok(st.items.some((t) => t.indexOf(' on ' + HOSTILE + ', amount: ') >= 0), 'the refused chart does not name the header as text: ' + JSON.stringify(st.items));
  ok(!st.made.length, 'the header made an element in the report: ' + st.made.join(' | '));
  ok(st.html.indexOf('&lt;img src=x onerror=alert(1)&gt;') >= 0 && st.html.indexOf('<img') < 0, 'the header is not escaped in the list: ' + st.html.slice(0, 200));
  ok(!dialogs.length, 'the header ran script: a dialog opened (' + dialogs[0] + ')');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// an unknown kind (or data a reader cannot read) is drawn as its table and never sends the page back to the v1 report
check('viz-an-unknown-kind-shows-its-table-and-keeps-the-v2-report', DESK, async (ctx) => {
  const recs = vizOddRecords();
  recs.forEach((r, i) => { r.id = 'viz.' + (i + 1 + VIZ_SPEC.caps.VIZ_MAX) + '.' + r.chart; });
  const rep = vizReport(recs);
  const { p } = await openV2(ctx, 'sample', rep);
  const d = await p.evaluate((r) => ({ v2: !!document.querySelector('#try-report .nl2-views'), fb: (document.querySelector('#try-report .nl2-fallback') || {}).textContent || '', bad: window.NL2.check(r) }), rep);
  ok(d.v2 && !d.fb && d.bad.length === 0, 'an unknown or unreadable viz record sent the page back to v1: ' + JSON.stringify(d.bad.slice(0, 3)) + ' ' + d.fb.slice(0, 160));
  for (const r of recs) vizFigureOk(await vizFigure(p, '[id="nl2c-m-' + r.id + '"]', r), r, r.__name);
  // a record with no table at all is the one thing still refused, in words
  const none = vizCopy(rep); none.charts.filter((c) => c.type === 'viz')[0].data.table = null;
  ok((await p.evaluate((r) => window.NL2.check(r), none)).some((x) => /do not have the shape of a viz/.test(x)), 'a viz record with no table is not refused');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the AI report's [CHART:n] markers: every kind drawn from the list /report sent back (never the page's own copy), each
// with its name and a Table button; the analyses' own charts get the Table button too; a shared link carries that list
check('viz-ai-report-markers-draw-every-kind-from-the-validated-list', DESK, async (ctx) => {
  // one record of each kind, and one of a kind no reader knows
  const V = vizRecords(vizOddRecords().slice(0, 1)).filter((r, i, a) => a.findIndex((x) => x.kind === r.kind) === i);
  const bars = { kind: 'bars', title: 'Revenue by region', series: [{ label: 'East', value: 44616.75 }, { label: 'North', value: 42350.45 }] };
  const validated = V.map((r) => { const c = vizCopy(r); delete c.__name; return c; }).concat([bars]);
  const local = validated.slice().reverse();          // the page's own copy, in another order: it must not be what is drawn
  const report = 'Revenue rose in [your file].\n## The headline: revenue rose\n' + validated.map((c, i) => '[CHART:' + (i + 1) + ']').join('\n') + '\n## What to do\n- Check it.';
  const rep = optinReport(); rep.__resultsJson = { charts: local, tables: [] };
  const shares = [];
  const reply = (b) => {
    if (typeof b.report === 'string' && 'days' in b) { shares.push(b); return { status: 200, json: { link: SHARE_LINK, delete_token: 'tok' } }; }
    if (b.profile) return { status: 200, json: { plan: OPTIN_PLAN } };
    if (b.results) return { status: 200, json: { report, sources: [], model: 'check', repaired: 0, charts: validated, tables: [] } };
    return { status: 503, json: { error: 'x' } };
  };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: reply });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  await p.waitForTimeout(200);
  const caps = await p.evaluate(() => Array.from(document.querySelectorAll('#try-ai-report .ai-rep-figure > figcaption')).map((x) => x.textContent));
  ok(JSON.stringify(caps) === JSON.stringify(validated.map((c) => c.title)), 'the [CHART:n] figures are not /report\'s charts in its order: ' + JSON.stringify(caps));
  const figs = await p.evaluate(async () => {
    const out = [];
    for (const f of Array.from(document.querySelectorAll('#try-ai-report .ai-rep-figure'))) {
      const svg = f.querySelector('.viz svg'), btn = f.querySelector('.tbl-btn');
      const rows = () => Array.from(f.querySelectorAll('.viz table tbody tr')).map((tr) => Array.from(tr.children).map((c) => c.textContent.replace(/\s+/g, ' ').trim()));
      const o = { svg: !!svg, name: svg && svg.getAttribute('aria-label'), btn: !!btn, direct: rows() };
      if (btn) { btn.click(); await new Promise((r) => setTimeout(r, 60)); o.table = rows(); btn.click(); await new Promise((r) => setTimeout(r, 60)); o.back = !!f.querySelector('.viz svg'); }
      out.push(o);
    }
    return out;
  });
  validated.forEach((c, i) => {
    const d = figs[i] || {};
    if (c.kind === 'bars') { ok(d.svg && d.btn && d.back && JSON.stringify(d.table) === JSON.stringify([['East', '44,616.75'], ['North', '42,350.45']]), 'the analyses\' bars chart has no Table button or the wrong table: ' + JSON.stringify(d)); return; }
    const want = c.table.rows.map((r) => r.map(String));
    if (vizKind(c) === 'table') { ok(!d.svg && !d.btn && JSON.stringify(d.direct) === JSON.stringify(want), '[CHART:' + (i + 1) + '] (' + c.kind + ') is not its table: ' + JSON.stringify(d).slice(0, 200)); return; }
    ok(d.svg && d.name === vizLabel(c) && d.btn && d.back && JSON.stringify(d.table) === JSON.stringify(want), '[CHART:' + (i + 1) + '] (' + c.kind + ') is not drawn with its name and a Table button: ' + JSON.stringify(d).slice(0, 240));
  });
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  // as a link carries them (the chart review, 30 Sep 2026): a record with a suppressed cell goes with no rows read
  const linked = validated.map((c) => (c.suppressed && c.suppressed.cells > 0 && c.inputs ? Object.assign(vizCopy(c), { inputs: Object.assign({}, c.inputs, { rows: null }) }) : c));
  ok(validated.some((c) => c.suppressed && c.suppressed.cells > 0), 'no validated record suppresses a cell: the check proves little');
  ok(shares.length === 1 && JSON.stringify(shares[0].charts) === JSON.stringify(linked), 'the shared link does not carry the validated charts: ' + JSON.stringify((shares[0] || {}).charts || null).slice(0, 160));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// screenshots of every kind, light and dark, at 390 px and on a desktop: only when VIZ_SHOTS names a folder
check('viz-screenshots', DESK, async (ctx) => {
  const dir = process.env.VIZ_SHOTS;
  if (!dir) throw new Skip('set VIZ_SHOTS to a folder to write them');
  fs.mkdirSync(dir, { recursive: true });
  const recs = vizRecords();
  let n = 0;
  for (const [label, vp, mobile] of [['390', { width: 390, height: 844 }, true], ['desktop', DESK, false]]) {
    for (const scheme of ['light', 'dark']) {
      const c = await ctx.browser().newContext({ viewport: vp, isMobile: mobile, hasTouch: mobile, reducedMotion: 'reduce', colorScheme: scheme });
      try {
        await c.addInitScript((t) => { try { localStorage.setItem('nl-theme', t); } catch (e) { /* light */ } }, scheme);
        const { p } = await openV2(c, 'sample', vizReport(recs));
        await p.waitForTimeout(300);
        for (const r of recs) {
          const el = await p.$('[id="nl2c-m-' + r.id + '"]');
          if (!el) continue;
          await el.scrollIntoViewIfNeeded();
          await el.screenshot({ path: path.join(dir, r.__name.replace(/[^a-z0-9_]+/gi, '-') + '-' + scheme + '-' + label + '.png') });
          n++;
        }
      } finally { await c.close(); }
    }
  }
  console.log('   ' + n + ' screenshots in ' + dir);
});

/* ------------------------------------------------------------ the final evaluation (1 Oct 2026), page side
   The live page drew the first 10 bars of an analysis chart (the payload had cut them to 4), and nothing on it said the
   AI plan had set aside a fifth of the reviews, or why the health score read 0.0. Now the AI report draws every bar the
   engine sent and a line whose value axis spans its data; the plan card lists each step that set rows aside with its
   count, share, reason and the engine's check; the bottom line says so when a step set aside a tenth of the rows or
   more; the trust strip's Data health line says what set the score; and the theme chart of the kept review_text is
   drawn (option B). */
check('try-final5-plan-card-bottom-line-and-health-say-what-happened', DESK, async (ctx) => {
  const rep = fixture('reviews-plan-drop');
  ok(!rep.__fixture_error, 'the adapter could not make the reviews-plan-drop run (tools/make_ui_fixtures.py): ' + rep.__fixture_error);
  const { p } = await openV2(ctx, null, rep);
  const d = await p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : '';
    return { drops: Array.from(document.querySelectorAll('#try-plan-card .try-plan-drops li')).map(t),
      steps: Array.from(document.querySelectorAll('#try-plan-card li')).map(t),
      bl: Array.from(document.querySelectorAll('#nl2-manager .nl2-bottomcard .nl2-plandrop')).map(t),
      health: t(document.querySelector('#nl2-manager .nl2-trust li[data-layer="health"]')),
      figs: Array.from(document.querySelectorAll('#nl2-manager figure')).map((f) => t(f.querySelector('figcaption, h4, .nl2-figtitle')) || t(f).slice(0, 80)).join(' | '),
      text: t(document.getElementById('try-report')) };
  });
  const D = rep.ai_plan.row_drops;
  ok(D.length === 1 && d.drops.length === 1 && d.drops[0] === squash(D[0].text), 'the plan card does not list the step that set rows aside: ' + JSON.stringify(d.drops));
  ok(/^Set aside 445 rows \(17\.3% of the file's 2,565\) where department is All Electronics\. The plan's reason: /.test(d.drops[0]) &&
    /The engine checked: none of these rows duplicates a kept row \(compared on every column but department\)\.$/.test(d.drops[0]), 'the disclosure lacks its count, share, reason or check: ' + d.drops[0]);
  ok(d.steps.some((x) => /^dropped 445 rows \(17\.3%\) where department is one of 1 value/.test(x)), 'the step\'s own line does not give its share: ' + JSON.stringify(d.steps.slice(0, 6)));
  ok(d.bl.length === 1 && d.bl[0] === squash(D[0].notice) && /^The AI plan set aside 445 rows \(17\.3%\): /.test(d.bl[0]), 'the bottom line does not say the plan set aside a sixth of the rows: ' + JSON.stringify(d.bl));
  ok(d.health.indexOf(squash(rep.health.explain.replace(/[.\s]+$/, ''))) >= 0 && /0 because the newest row is 3\.5 years old \(the timeliness check\)/.test(d.health),
    'the Data health line does not say what set the score: ' + d.health);
  ok(d.text.indexOf('What the review_text texts say, by rating') >= 0, 'the theme chart of the kept review_text is not drawn: ' + d.figs.slice(0, 300));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

check('try-final5-ai-report-draws-every-bar-and-a-line-that-spans-its-data', DESK, async (ctx) => {
  const fx = pdfFixture('final5-results.json').runs.fx.results;
  const rep = optinReport(); rep.__resultsJson = fx;
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: pdfReply({ charts: fx.charts, tables: fx.tables }) });
  await optinStep(p); await p.click('#try-pd-go');
  await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
  const figs = await p.evaluate(() => Array.from(document.querySelectorAll('#try-ai-report .ai-rep-figure')).map((f) => ({
    cap: (f.querySelector('figcaption') || {}).textContent || '', bars: f.querySelectorAll('svg rect.mk').length,
    ticks: Array.from(f.querySelectorAll('svg text')).filter((x) => x.getAttribute('text-anchor') === 'end').map((x) => x.textContent) })));
  const bins = fx.charts.filter((c) => c.kind === 'bars')[0], line = fx.charts.filter((c) => c.kind === 'line')[0];
  const fb = figs.filter((f) => f.cap === bins.title)[0], fl = figs.filter((f) => f.cap === line.title)[0];
  ok(fb && fb.bars === 12, 'the histogram does not draw its 12 bars: ' + JSON.stringify(fb && fb.bars));
  const ys = line.series[0].y, tk = fl ? fl.ticks.map((x) => Number(String(x).replace(/,/g, '').replace('\u2212', '-'))) : [];
  ok(fl && tk.length >= 3 && tk.indexOf(0) < 0 && Math.min.apply(null, tk) > 1 && Math.min.apply(null, tk) >= Math.min.apply(null, ys) - 0.05 &&
    Math.max.apply(null, tk) <= Math.max.apply(null, ys) + 0.05, 'the trend line\'s value axis does not span its data (' + Math.min.apply(null, ys).toFixed(3) + ' to ' +
    Math.max.apply(null, ys).toFixed(3) + '): ticks ' + JSON.stringify(fl && fl.ticks));
  // the shared rule: a level far from 0 spans its data; data near 0 or crossing it take 0 in
  const sp = await p.evaluate(() => [window.NLU.lineSpan(1.25, 1.4), window.NLU.lineSpan(5, 100), window.NLU.lineSpan(-3, 10), window.NLU.lineSpan(-10, -1)]);
  ok(sp[0][0] > 1.2 && sp[0][1] < 1.42 && sp[1][0] === 0 && sp[2][0] < -3 && sp[2][1] > 10 && sp[3][1] === 0, 'U.lineSpan: ' + JSON.stringify(sp));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

// the plan's row noun (integration pass, 1 Oct 2026): the page copies plan.row_noun into the results it sends to /report
// (results.row_noun: the worker's count-noun check reads it and its plural as words for rows), and a share link's results
// keep it, with every step of the plan that set rows aside and what set the health score, which the worker's shared PDF
// prints (the share's copy of the results lost both before); a row noun that is not one lowercase word is never sent
check('try-report-results-carry-the-row-noun-and-a-share-keeps-the-plan-disclosure', DESK, async (ctx) => {
  const rv = pdfFixture('final5-results.json').runs.reviews.results;
  const go = async (c, noun) => {
    const rep = optinReport(); rep.__resultsJson = rv;
    const base = pdfReply({ charts: rv.charts, tables: rv.tables });
    const p = await openTry(c, { stubReport: rep, proxy: 'set', proxyReply: (b) => b.profile ? { status: 200, json: { plan: Object.assign({}, OPTIN_PLAN, { row_noun: noun }) } } : base(b) });
    await optinStep(p); await p.click('#try-pd-go');
    await tryUntil(p, '#try-ai-report:not([hidden]) .ai-rep-body', 20000);
    return p;
  };
  const p = await go(ctx, 'review');
  const B = proxyPosts(ctx, 'report').map((r) => JSON.parse(r.body));
  ok(B.length === 1 && B[0].results && B[0].results.row_noun === 'review', 'the /report results do not carry the plan\'s row noun: ' + JSON.stringify(B.map((b) => b.results && b.results.row_noun)));
  ok(B[0].results.health_explain === rv.health_explain && JSON.stringify(B[0].results.plan_row_drops) === JSON.stringify(rv.plan_row_drops), 'the /report results lost the plan\'s set-aside rows or the health explanation');
  await p.click('#try-share');
  await tryUntil(p, '#try-share-out .try-share-warn');
  await p.click('#try-share-yes');
  await p.waitForFunction((l) => document.getElementById('try-share-out').textContent.indexOf(l) >= 0, SHARE_LINK);
  const S = proxyPosts(ctx, 'share').map((r) => JSON.parse(r.body));
  ok(S.length === 1 && S[0].results, S.length + ' /share requests, or none with results');
  const R = S[0].results, want = rv.plan_row_drops.map((d) => ({ rows: d.rows, of: d.of, pct: d.pct, text: d.text, notice: d.notice }));
  ok(R.row_noun === 'review' && R.health_explain === rv.health_explain && JSON.stringify(R.plan_row_drops) === JSON.stringify(want),
    'the share\'s results do not keep the row noun, the health explanation or the plan\'s set-aside rows: ' + JSON.stringify({ row_noun: R.row_noun, health_explain: R.health_explain, drops: R.plan_row_drops }).slice(0, 400));
  // the worker's shared PDF, from exactly this body (insight-proxy: NLReportPdf.model(the stored share, shared: true))
  const W = require(path.join(SITE_DIR, 'src', 'js', '45-report-pdf.js')), b = S[0];
  const m = W.model({ report: b.report, sources: b.sources, model: b.model, goal: b.goal, charts: b.charts, tables: b.tables, results: b.results, kept: b.kept, repaired: b.repaired, shared: true, date: new Date(2026, 9, 1) });
  const w = pdfWords(W.build(m, { paper: 'letter' })).text.replace(/\s+/g, ' ');
  ok(w.indexOf(squash(rv.plan_row_drops[0].notice)) >= 0 && /rows the ai plan set aside/i.test(w) && w.indexOf(squash(rv.plan_row_drops[0].text)) >= 0,
    'the shared PDF does not say the plan set rows aside: ' + w.slice(0, 300));
  ok(w.indexOf(squash(rv.health_explain)) >= 0, 'the shared PDF does not say what set the health score: ' + rv.health_explain);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  // a row noun that is not one lowercase word of 3 to 20 letters (a to z) is not sent
  for (const bad of ['Reviews!', 'customer review', 'ab']) {
    const c = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
    try {
      const q = await go(c, bad);
      const B2 = proxyPosts(c, 'report').map((r) => JSON.parse(r.body));
      ok(B2.length === 1 && !('row_noun' in B2[0].results), 'the row noun ' + JSON.stringify(bad) + ' was sent: ' + JSON.stringify(B2.map((x) => x.results.row_noun)));
      ok(!q.__errs.length, 'page error: ' + q.__errs[0]);
    } finally { await c.close(); }
  }
});

/* ------------------------------------------------------------ wave 4, track B: the estimand first, the process grade, the audit
   A statistical table (tools/make_ui_fixtures.py "official-cube": a synthetic cube in Statistics Canada's shape) is run through
   the adapter; the page must open on what the headline measures (the estimand), say that the engine's grade is a grade of the
   monthly noise, show the not-allocated step and each part's own change, print the back-test beside the forecast (and say "not
   trusted" when it failed), and say why a dropped forecast is dropped. The sentences are the writer's (45-report-pdf.js), so
   the checks read them from the report, not from the page. */
const cubeReport = () => vizCopy(fixture('official-cube'));
const noBigNumbers = (txt, where) => { const m = String(txt).match(/(?<![\d,.])\d{9,}(?![\d,])/g); ok(!m, where + ' prints a raw number of 9 or more digits: ' + (m || []).slice(0, 3).join(', ')); };
async function openCube(ctx, rep) {
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await runReport(p);
  ok(await p.evaluate(() => !!document.querySelector('#try-report .nl2-views') && !document.querySelector('#try-report .nl2-fallback')), 'the statistical table\'s report fell back to v1');
  await p.evaluate(() => document.querySelectorAll('#try-report details').forEach((d) => { d.open = true; }));
  await p.waitForTimeout(200);
  return p;
}
/* wave 5 (generality): a table with no total row, and a quarterly table, say so on the page. SYNTHETIC tables (make_ui_fixtures.py
   "wave5-no-total", "wave5-quarterly"): the estimand card prints that the headline is built from its parts and why it cannot be
   checked against a total, and a quarterly table's figures and checks say quarters, never months. */
check('wave5-no-total-row-and-quarterly-tables-are-said-on-the-page', DESK, async (ctx) => {
  const nt = vizCopy(fixture('wave5-no-total')), qt = vizCopy(fixture('wave5-quarterly'));
  ok(nt.estimand && nt.estimand.built_from && nt.estimand.built_from.n === 5 && qt.estimand && qt.estimand.period && qt.estimand.period.kind === 'quarter', 'the fixtures carry no built_from or period record');
  const read = (p) => p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null, m = document.getElementById('nl2-manager');
    return { title: t(document.querySelector('#nl2-manager .nl2-est-title')), what: t(document.querySelector('#nl2-manager .nl2-est-what')),
      figs: Array.from(document.querySelectorAll('#nl2-manager .nl2-estimand .nl2-est-fig dt')).map(t), checks: Array.from(document.querySelectorAll('#nl2-manager .nl2-est-checks li')).map(t),
      excl: Array.from(document.querySelectorAll('#nl2-manager .nl2-est-excl li')).map(t), structure: t(document.querySelector('#nl2-analyst .nl2-structure')) || '', page: t(m) };
  });
  const p1 = await openCube(ctx, nt), a = await read(p1);
  ok(a.title === 'the sum of 5 regions' && /built from 5 regions; this table has no total row/.test(a.what), 'the estimand card does not say the headline is built from 5 regions with no total row: ' + JSON.stringify([a.title, a.what]));
  ok(a.checks.length === 1 && /^Not possible to check: GEO has no total row, so the 5 parts are added up, month by month\. 2 part-months are suppressed in the two windows\.$/.test(a.checks[0]) && !/adds up/i.test(a.checks[0]), 'the sum-check line is not "not possible": ' + JSON.stringify(a.checks));
  ok(/the sum of its 5 parts \(no total row\)/.test(a.structure) && /parts/.test(a.structure), 'the analyst view\'s structure table does not say GEO is the sum of its parts: ' + a.structure.slice(0, 300));
  await p1.close();
  const p2 = await openCube(ctx, qt), b = await read(p2);
  ok(b.figs.join('|') === '4 quarters before|Latest 4 quarters|Change|Change, %' && /4-quarter averages Q1 2023–Q4 2023 vs Q1 2022–Q4 2022/.test(b.what), 'a quarterly table\'s estimand card does not say quarters: ' + JSON.stringify([b.figs, b.what]));
  ok(!/\bmonths?\b/i.test(b.figs.join(' ') + ' ' + b.what + ' ' + b.checks.join(' ')) && /on 48 of 48 quarters/.test(b.checks[0]) && /in the latest 4 quarters/.test(b.checks[0]), 'a quarterly table\'s estimand card says months: ' + JSON.stringify([b.figs, b.what, b.checks]));
  ok(qt.forecast.available === false && /forecast reads monthly series only/.test(qt.forecast.reason) && !qt.forecast.audit, 'the quarterly table has a forecast or an audit: ' + JSON.stringify(qt.forecast.reason));
  await p2.close();
});
/* wave 5b: a rate table with no total member shows ONE member and says it is not a national figure (the estimand card, its left-out line and the
   headline), and is never described as a published total; a table of series whose structure layer could not run is refused with the plain
   reason, the page draws no estimand card and no structure table, and says it once. SYNTHETIC tables (make_ui_fixtures.py "wave5b-one-member",
   "wave5b-refused": the second is read while nl_structure cannot be imported). */
check('wave5b-one-member-and-a-refused-table-are-said-on-the-page', DESK, async (ctx) => {
  const om = vizCopy(fixture('wave5b-one-member')), rf = vizCopy(fixture('wave5b-refused'));
  ok(om.estimand && om.estimand.single_member && om.estimand.single_member.member === 'Echo' && !om.estimand.inference, 'the one-member fixture carries no single_member, or is described: ' + JSON.stringify(om.estimand && om.estimand.single_member));
  ok(rf.structure && rf.structure.kind === 'error' && rf.structure.usable === false && !rf.estimand && /^The business analysis did not run: this file looks like a table of series with totals/.test(rf.story.headline), 'the refused fixture is not a refusal: ' + JSON.stringify([rf.structure, rf.story.headline]));
  const p1 = await openCube(ctx, om);
  const a = await p1.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    return { what: t(document.querySelector('#nl2-manager .nl2-est-what')), excl: Array.from(document.querySelectorAll('#nl2-manager .nl2-est-excl li')).map(t),
      described: !!document.querySelector('#nl2-manager .nl2-est-desc'), page: t(document.getElementById('try-report')),
      structure: t(document.querySelector('#nl2-analyst .nl2-structure')) || '' };
  });
  ok(/one member shown: Echo; this table has no total member, so this is not a national figure/.test(a.what), 'the estimand card does not say one member is shown and it is not a national figure: ' + a.what);
  ok(a.excl.some((x) => /one member shown, not a national figure: this table has no total member, and a rate is never added or averaged across members/.test(x)), 'the left-out line does not say it: ' + JSON.stringify(a.excl));
  ok(!a.described && !/published total/i.test(a.what), 'a table with one member shown is described as a published total');
  ok(a.page.indexOf('(one member shown, not a national figure)') >= 0, 'the headline does not say one member is shown: ' + om.story.headline);
  ok(/GEO/.test(a.structure) && /single/.test(a.structure), 'the analyst view does not list GEO as read one member at a time: ' + a.structure.slice(0, 200));
  ok(!p1.__errs.length, 'page error: ' + p1.__errs[0]);
  await p1.close();
  const p2 = await openTry(ctx, { stubReport: rf, proxy: 'unset' });
  await runReport(p2);
  const b = await p2.evaluate(() => ({ text: document.getElementById('try-report').textContent.replace(/\s+/g, ' '), card: !!document.querySelector('#try-report .nl2-estimand'),
    structure: !!document.querySelector('#try-report .nl2-structure'), kpis: !!document.querySelector('#try-report .tr-kpis') }));
  ok(b.text.split(rf.story.headline).length - 1 === 1 && /could not run\. An average over its rows would count totals and parts together, so no figure is shown/.test(b.text), 'the refusal is not said once, in full: ' + b.text.slice(0, 300));
  ok(!b.card && !b.structure, 'a refused table draws an estimand card or a structure table');
  ok(!/months? to [A-Z][a-z]{2} 20\d\d: [+-]/.test(b.text), 'a refused table prints a change');
  ok(!p2.__errs.length, 'page error: ' + p2.__errs[0]);
  await p2.close();
});
/* wave 5c: a price index with a named whole country (Canada) that no sum-check can verify is the headline, and the estimand card, its left-out
   line, the structure table and the headline say so; it is still a named total, so it is described as a published one, and never the
   more dominant province. SYNTHETIC (make_ui_fixtures.py "wave5c-named-total": make_cubes.index_two_bases). */
check('wave5c-a-named-total-no-check-can-verify-is-said-on-the-page', DESK, async (ctx) => {
  const rep = vizCopy(fixture('wave5c-named-total'));
  ok(rep.estimand && !rep.estimand.single_member && rep.estimand.inference && rep.estimand.inference.mode === 'official_aggregate', 'the fixture carries a single_member, or is not described: ' + JSON.stringify(rep.estimand && rep.estimand.single_member));
  ok(/^2002 base, Canada, 12 months to Dec 2022: .* in the published totals$/.test(rep.story.headline), 'the headline is not the named total\'s: ' + rep.story.headline);
  const p = await openCube(ctx, rep);
  const a = await p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    return { title: t(document.querySelector('#nl2-manager .nl2-est-title')), what: t(document.querySelector('#nl2-manager .nl2-est-what')),
      excl: Array.from(document.querySelectorAll('#nl2-manager .nl2-est-excl li')).map(t), described: !!document.querySelector('#nl2-manager .nl2-est-desc'),
      page: t(document.getElementById('try-report')), structure: t(document.querySelector('#nl2-analyst .nl2-structure')) || '' };
  });
  ok(a.title === 'Canada \u00b7 2002 base', 'the estimand card is not titled by the named total: ' + a.title);
  ok(/Canada: the named total; not verifiable by a sum-check \(an index cannot be summed\)/.test(a.what), 'the estimand card does not say Canada is the named total no sum-check can verify: ' + a.what);
  ok(a.excl.some((x) => /Canada is the named total; not verifiable by a sum-check \(an index cannot be summed\)/.test(x)), 'the left-out line does not say it: ' + JSON.stringify(a.excl));
  ok(a.described && !/one member shown/i.test(a.what + a.excl.join(' ')), 'a named total is not described as a published total, or says one member is shown');
  ok(a.page.indexOf(rep.story.headline) >= 0 && !/Ontario/.test(a.what + a.excl.join(' ')), 'the page does not lead with the named total: ' + a.page.slice(0, 200));
  ok(/GEO/.test(a.structure) && /Canada/.test(a.structure) && /rate aggregate/.test(a.structure), 'the analyst view does not list Canada as the GEO dimension\'s aggregate: ' + a.structure.slice(0, 200));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
  await p.close();
});
check('estimand-leads-the-report-and-the-headline-is-the-estimands', DESK, async (ctx) => {
  const rep = cubeReport(), E = rep.estimand;
  ok(E && E.figures && E.inference && E.inference.mode === 'official_aggregate', 'the fixture carries no official-aggregate estimand');
  // the headline: the estimand composed (what, window, change in the units of the table, "in the published totals"), no invented subject
  ok(/^VALUE, Total, 12 months to Aug 2026: \+3\.8% \(\$347\.6M\) in the published totals$/.test(rep.story.headline), 'the headline is not the estimand\'s: ' + rep.story.headline);
  ok(rep.summary.lines[0].text === rep.story.headline && rep.summary.lines[0].kind === 'moved', 'the first summary sentence is not the headline: ' + JSON.stringify(rep.summary.lines[0]));
  const p = await openCube(ctx, rep);
  const d = await p.evaluate(() => {
    const m = document.getElementById('nl2-manager'), first = m.querySelector('.tr-card'), t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    const figs = Array.from(document.querySelectorAll('#nl2-manager .nl2-estimand .nl2-est-fig')).map((f) => [t(f.querySelector('dt')), t(f.querySelector('dd')), f.classList.contains('nl2-est-lead')]);
    return { firstIsEstimand: !!first && first.classList.contains('nl2-estimand'), n: m.querySelectorAll('.nl2-estimand').length, title: t(document.querySelector('#nl2-manager .nl2-est-title')),
      figs, desc: t(document.querySelector('#nl2-manager .nl2-est-desc')), proc: t(document.querySelector('#nl2-manager .nl2-est-proc')), chip: t(document.querySelector('#nl2-manager .nl2-est-proc .nl2-grade')),
      checks: Array.from(document.querySelectorAll('#nl2-manager .nl2-est-checks li')).map(t), excl: Array.from(document.querySelectorAll('#nl2-manager .nl2-est-excl li')).map(t), src: t(document.querySelector('#nl2-manager .nl2-est-src')),
      how: t(document.querySelector('#nl2-manager .nl2-est-how')), noSvg: (() => { const c = m.cloneNode(true); c.querySelectorAll('svg').forEach((x) => x.remove()); return t(c); })(), bl: Array.from(document.querySelectorAll('#nl2-manager .nl2-bottomcard li, #nl2-manager .nl2-bottomcard p')).map(t).join(' '), man: t(m) };
  });
  ok(d.firstIsEstimand && d.n === 1, 'the first card of the report is not the one estimand card: ' + JSON.stringify({ first: d.firstIsEstimand, n: d.n }));
  ok(d.title === E.text || d.title.indexOf(E.measure.label) === 0 || d.title.indexOf('Total') >= 0, 'the estimand card does not open with the estimand\'s text: ' + d.title);
  // prior, latest, change and change % in the report's own words, the change the prominent one
  const want = { prior: E.figures.prior.text, latest: E.figures.latest.text, change: E.figures.change.text, change_pct: E.figures.change_pct.text };
  Object.keys(want).forEach((k) => ok(d.figs.some((f) => f[1].indexOf(want[k]) >= 0), 'the estimand card does not print ' + k + ' ' + want[k] + ': ' + JSON.stringify(d.figs)));
  ok(d.figs.filter((f) => f[2]).length === 1 && d.figs.filter((f) => f[2])[0][1].indexOf(want.change) >= 0 || d.figs.filter((f) => f[2])[0][1].indexOf(want.change_pct) >= 0, 'the described change is not the prominent figure: ' + JSON.stringify(d.figs));
  ok(/described, not tested/i.test(d.desc || ''), 'a published total is not said to be described, not tested: ' + d.desc);
  // the sum-check: its verdict and counts, the largest gap, in the table's units
  const sc = E.sum_checks[0], chk = d.checks.join(' | ');
  ok(/adds up/i.test(chk) && chk.indexOf(String(sc.within_tolerance) + ' of ' + String(sc.complete_cells)) >= 0 && chk.indexOf(sc.max_residual.text) >= 0, 'the sum-check is not stated with its counts and its largest gap (' + sc.within_tolerance + ' of ' + sc.complete_cells + ', ' + sc.max_residual.text + '): ' + chk);
  ok(d.excl.some((x) => /5 other members/.test(x) && /never added/.test(x)), 'what was left out, and why, is not listed: ' + JSON.stringify(d.excl));
  ok(/engine|default|no plan|your own|chose/i.test(d.src || '') && d.src.indexOf('engine_default') < 0, 'the plan\'s source is not in plain words: ' + d.src);
  // the process grade: the chip names what the grade is a grade of, and the note says it is not a test of the total
  ok(/\(process grade\)/.test(d.chip || '') && /month-to-month noise in 36 monthly totals; not a test of the published total/.test(d.proc || ''), 'the process-grade chip or note is missing: ' + JSON.stringify({ chip: d.chip, proc: d.proc }));
  ok(await p.evaluate(() => document.querySelectorAll('#try-report [data-process="1"]').length >= 1), 'no grade chip is marked as a process grade');
  // the card names its own units and no raw 9-digit figure is printed anywhere in the report
  noBigNumbers(d.noSvg, 'the manager view');
  ok(d.man.indexOf('$347.6M') >= 0 && d.man.indexOf('$334.8M') >= 0, 'the amounts are not in the estimand\'s units ($347.6M, $334.8M)');
  ok(d.bl.indexOf(rep.story.headline) >= 0 || d.man.indexOf(rep.story.headline) >= 0, 'the bottom line does not carry the estimand headline: ' + d.bl.slice(0, 200));
  // the same card opens the analyst view's estimand section
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(150);
  const a = await p.evaluate(() => { const c = document.getElementById('nl2-analyst').cloneNode(true); c.querySelectorAll('svg').forEach((x) => x.remove()); return { n: document.querySelectorAll('#nl2-analyst .nl2-estimand').length, txt: (c.textContent || '').replace(/\s+/g, ' ') }; });
  ok(a.n === 1 && /process grade/.test(a.txt), 'the analyst view has no estimand section with the process grade: ' + a.n);
  noBigNumbers(a.txt, 'the analyst view');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
check('waterfall-shows-the-unallocated-step-and-each-parts-own-change', DESK, async (ctx) => {
  const rep = cubeReport(), rec = rep.charts.filter((c) => c.id.indexOf('viz.') === 0 && c.data && c.data.kind === 'waterfall')[0];
  ok(rec, 'the statistical table has no contribution waterfall');
  const steps = rec.data.data.steps, un = steps.filter((s) => s.label === 'Not allocated: suppressed cells');
  ok(un.length === 1 && rec.data.table.cols.indexOf('Own change') === 2 && rec.data.table.rows.some((r) => r[0] === 'Not allocated: suppressed cells'), 'the record does not carry the not-allocated step and the Own change column');
  // the waterfall decomposes the CHANGE: a Start of 0 first, the Total change last (final integration pass)
  ok(steps[0].label === 'Start' && steps[0].value === 0 && steps[steps.length - 1].label === 'Total change' && steps[steps.length - 1].text === rep.estimand.figures.change.text, 'the waterfall does not run from a Start of 0 to the Total change: ' + JSON.stringify([steps[0], steps[steps.length - 1]]));
  const p = await openCube(ctx, rep);
  const d = await p.evaluate(async () => {
    const svg = document.querySelector('#try-report svg.nlv .w-unalloc') ? document.querySelector('#try-report svg.nlv .w-unalloc').closest('svg') : null;
    if (!svg) return { missing: true };
    const fig = svg.closest('figure'), btn = fig && fig.querySelector('.tbl-btn');
    const out = { un: svg.querySelectorAll('.w-unalloc').length, tips: Array.from(svg.querySelectorAll('[data-tip]')).map((g) => g.getAttribute('data-tip')), texts: Array.from(svg.querySelectorAll('text')).map((t) => t.textContent), btn: !!btn };
    if (btn) { btn.click(); await new Promise((r) => setTimeout(r, 80)); out.th = Array.from(fig.querySelectorAll('.vtable thead th, table thead th')).map((x) => x.textContent.trim()); out.rows = Array.from(fig.querySelectorAll('table tbody tr')).map((tr) => Array.from(tr.children).map((c) => c.textContent.replace(/\s+/g, ' ').trim())); }
    return out;
  });
  ok(!d.missing && d.un >= 1, 'the not-allocated step is not drawn as its own (hatched) step: ' + JSON.stringify(d).slice(0, 200));
  ok(d.tips.some((t) => t.indexOf('Not allocated: suppressed cells: \u2212$26.2M') === 0), 'the step is not named "Not allocated: suppressed cells" with its amount: ' + d.tips.join(' | '));
  ok(d.texts.every((t) => !/^\d{9,}$/.test(t.replace(/[,\s]/g, ''))), 'an axis prints a raw number: ' + d.texts.join(' | '));
  // its table (the Table button): the step is a row, and each part has its own change
  ok(d.btn && d.th && d.th.indexOf('Own change') >= 0 && d.rows.some((r) => r[0] === 'Not allocated: suppressed cells'), 'the waterfall\'s table has no not-allocated row or no Own change column: ' + JSON.stringify([d.th, d.rows]).slice(0, 300));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
check('waterfall-parts-fill-the-chart-and-a-rounding-step-is-drawn-like-a-suppressed-one', DESK, async (ctx) => {
  const rep = cubeReport(), rec = rep.charts.filter((c) => c.id.indexOf('viz.') === 0 && c.data && c.data.kind === 'waterfall')[0];
  ok(rec, 'the statistical table has no contribution waterfall');
  // the same record with the step's cause read as rounding: the page draws it the same hatched way and says so
  rec.data.data.steps.forEach((s) => { if (s.label === 'Not allocated: suppressed cells') s.label = 'Not allocated: rounding'; });
  rec.data.table.rows.forEach((r) => { if (r[0] === 'Not allocated: suppressed cells') r[0] = 'Not allocated: rounding'; });
  const p = await openCube(ctx, rep);
  const d = await p.evaluate(() => {
    const svg = document.querySelector('#try-report svg.nlv .w-unalloc') ? document.querySelector('#try-report svg.nlv .w-unalloc').closest('svg') : null;
    if (!svg) return { missing: true };
    const box = (e) => { const b = e.getBBox(); return { w: b.width, h: b.height }; };
    const tot = Array.from(svg.querySelectorAll('rect.w-tot')).map(box), steps = Array.from(svg.querySelectorAll('rect.w-rise, rect.w-fall')).map(box);
    const ext = (b) => Math.max(b.w, b.h);
    return { un: svg.querySelectorAll('.w-unalloc').length, tips: Array.from(svg.querySelectorAll('[data-tip]')).map((g) => g.getAttribute('data-tip')),
      texts: Array.from(svg.querySelectorAll('text')).map((t) => t.textContent), lastTot: ext(tot[tot.length - 1] || { w: 0, h: 0 }),
      biggest: Math.max.apply(null, steps.map(ext).concat([0])), nTot: tot.length };
  });
  ok(!d.missing && d.un === 1, 'a "Not allocated: rounding" step is not drawn as the hatched step: ' + JSON.stringify(d).slice(0, 200));
  ok(d.tips.some((t) => t.indexOf('Not allocated: rounding: \u2212$26.2M') === 0) && !d.tips.some((t) => /suppressed cells/.test(t)), 'the step is not named "Not allocated: rounding": ' + d.tips.join(' | '));
  ok(d.nTot === 2 && d.tips[0].indexOf('Start: $0') === 0 && d.tips[d.tips.length - 1].indexOf('Total change: ' + rep.estimand.figures.change.text) === 0, 'the chart does not run from "Start: $0" to "Total change": ' + d.tips.join(' | '));
  // the steps fill the chart: the largest part is a visible share of the Total change's bar (the levels view drew it as a hairline, about 1%)
  ok(d.biggest >= 0.1 * d.lastTot && d.lastTot > 20, 'the parts do not fill the chart: the largest step is ' + d.biggest + ' of the total change\'s ' + d.lastTot);
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
check('the-forecast-audit-is-printed-beside-the-range-and-a-failed-one-says-not-trusted', DESK, async (ctx) => {
  const base = cubeReport(), A = base.forecast.audit;
  ok(A && A.horizons && A.horizons.length && A.label, 'the fixture has no forecast audit');
  let p = await openCube(ctx, base);
  let d = await p.evaluate(() => ({ lines: Array.from(document.querySelectorAll('#try-report .nl2-audit')).map((e) => e.textContent.replace(/\s+/g, ' ').trim()), untrusted: document.querySelectorAll('#try-report .nl2-untrusted').length,
    man: document.getElementById('nl2-manager').textContent.replace(/\s+/g, ' '), tbl: Array.from(document.querySelectorAll('#try-report .nl2-audit-table tbody tr')).length }));
  ok(d.lines.length >= 1 && d.lines.every((l) => /held 3 of 3 at 1 month/.test(l)), 'the back-test line (held k of n by horizon) is not beside the forecast: ' + JSON.stringify(d.lines));
  ok(d.untrusted === 0, 'a back-test that neither passed nor failed is marked not trusted: ' + d.untrusted);
  // a back-test that failed: "not trusted" beside the engine's grade, in the finding, the tile and the table
  const bad = cubeReport(); bad.forecast.audit = Object.assign({}, bad.forecast.audit, { status: 'fails', trusted: false, label: 'back-tested: held 0 of 3 at 1 month, 0 of 1 at 3 months', grade_label: 'the engine\'s grade (the back-test failed it)' });
  bad.forecast.audit.horizons = bad.forecast.audit.horizons.map((h) => Object.assign({}, h, { held: 0, wilson: [0, 0.56], status: 'fails' }));
  await p.close();
  p = await openCube(ctx, bad);
  d = await p.evaluate(() => ({ un: Array.from(document.querySelectorAll('#try-report .nl2-untrusted')).map((e) => e.textContent.replace(/\s+/g, ' ').trim()), lines: Array.from(document.querySelectorAll('#try-report .nl2-audit')).map((e) => e.textContent.replace(/\s+/g, ' ').trim()),
    fcGrade: Array.from(document.querySelectorAll('#try-report [data-grade="CONFIRMED"]')).length }));
  ok(d.un.length >= 1 && d.un.every((x) => /not trusted/.test(x)), 'a failed back-test is not marked "not trusted": ' + JSON.stringify(d.un));
  ok(d.lines.some((l) => /held 0 of 3 at 1 month/.test(l)), 'the failed back-test\'s counts are not printed: ' + JSON.stringify(d.lines));
  // a dropped forecast of the rows a month: one line that says why, and no back-test beside it
  const dr = cubeReport(); dr.forecast.row_forecast_dropped = { reason: 'the table\'s layout fixes the rows a month, so a forecast of them would forecast the layout' };
  dr.findings.forEach((f) => { if (f.kind === 'forecast') f.layout_artifact = true; });
  await p.close();
  p = await openCube(ctx, dr);
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(150);
  d = await p.evaluate(() => ({ txt: document.getElementById('try-report').textContent.replace(/\s+/g, ' '), audit: document.querySelectorAll('#try-report .nl2-audit').length }));
  ok(d.txt.indexOf('No forecast of the rows a month is shown: the table\'s layout fixes the rows a month') >= 0, 'a dropped forecast does not say why in one line: ' + d.txt.slice(0, 200));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});
check('categories-the-engine-released-reach-the-reports-method-and-data-quality', DESK, async (ctx) => {
  const rep = cubeReport(), TEXT = 'The column GEO (a short list of 6 labels, each repeated) was read as categories, not as personal data.';
  rep.privacy = Object.assign({}, rep.privacy, { released: [{ column: 'GEO', text: TEXT }] });
  const p = await openCube(ctx, rep);
  await p.click('.nl2-views [data-view="analyst"]');
  await p.waitForTimeout(150);
  const d = await p.evaluate(() => { const l = document.querySelector('#try-report [data-released="1"]'); return { found: !!l, txt: l ? l.textContent.replace(/\s+/g, ' ').trim() : '', inMethod: !!(l && l.closest('#nl2-analyst')) }; });
  ok(d.found && d.txt.indexOf(TEXT) >= 0 && d.inMethod, 'the released category is not in the report\'s method and data-quality section: ' + JSON.stringify(d));
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
});

/* ------------------------------------------------------------ runner */
(async () => {
  const browser = await playwright.chromium.launch({ executablePath: CHROME, headless: true });
  let failed = 0, ran = 0, skipped = 0;
  for (const c of checks) {
    if (ONLY.size && !ONLY.has(c.name)) continue;
    ran++;
    const ctx = await browser.newContext({ viewport: c.viewport, isMobile: !!c.opts.mobile, hasTouch: !!c.opts.mobile, reducedMotion: 'reduce', colorScheme: c.opts.colorScheme || 'light',
      acceptDownloads: !!c.opts.acceptDownloads });
    try {
      await c.fn(ctx);
      console.log('UI PASS ' + c.name);
    } catch (e) {
      if (e instanceof Skip) { skipped++; console.log('UI SKIP ' + c.name + ': ' + e.message); }
      else { failed++; console.log('UI FAIL ' + c.name + ': ' + String(e.message || e).split('\n')[0]); }
    }
    await ctx.close();
  }
  await browser.close();
  console.log(failed ? 'UI CHECKS: ' + failed + ' of ' + ran + ' FAILED' : 'UI CHECKS: ALL ' + (ran - skipped) + ' PASS' + (skipped ? ', ' + skipped + ' SKIPPED (reason printed above)' : ''));
  process.exit(failed ? 1 : 0);
})();
