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
const STUB_WORKER = `'use strict';
var REPORT = __REPORT__;
self.onmessage = function (e) {
  var m = e.data || {};
  if (m.type === 'scan') {
    self.postMessage({ type: 'stage', id: m.id, stage: 'load', state: 'done', seconds: 0.01 });
    self.postMessage({ type: 'scanned', id: m.id, result: { ok: true, error: null, flagged: REPORT.privacy.flagged.map(function (f) { return { column: f.column, kind: f.kind }; }) } });
  } else if (m.type === 'run') {
    self.postMessage({ type: 'result', id: m.id, report: REPORT });
  }
};`;

// o: { stubReport, proxy: 'unset'|'set', proxyReply: fn(body) -> {status, json} }
async function demoContext(ctx, o) {
  o = o || {};
  ctx.__reqs = [];
  ctx.on('request', (r) => ctx.__reqs.push({ url: r.url(), method: r.method(), body: r.postData() }));
  await ctx.route(DEMO_ORIGIN + '/**', (route) => {
    const u = new URL(route.request().url());
    const rel = decodeURIComponent(u.pathname).replace(/^\/+/, '');
    if (o.stubReport && rel === 'engine/worker.js') {
      return route.fulfill({ status: 200, contentType: MIME['.js'], body: STUB_WORKER.replace('__REPORT__', JSON.stringify(o.stubReport)) });
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

check('try-ai-summaries-consent-payload-guard-and-fallbacks', DESK, async (ctx) => {
  const rep = stubReport();
  let mode = 'invented';
  // what the model writes: words around slots; the page fills every figure and grade (§6 of the plan)
  const good = {
    executive: "Average amount rose {F1.n1} on the year before, to {F1.n2}; this finding is {F1.grade} [F1]. Exact duplicates account for {F2.n1} of {F2.n2} rows: {F2.grade} [F2]. The most common region is '[value A]', with {F3.n1} of rows: {F3.grade} [F3].",
    technical: 'The average amount rose {F1.n1} against the prior year, to {F1.n2} (grade {F1.grade}) [F1]. Exact duplicates: {F2.n1} of {F2.n2} rows (grade {F2.grade}) [F2]. The next-month value for monthly rows, {F4.n1}, is graded {F4.grade} [F4].'
  };
  const shown = {
    executive: "Average amount rose 12.5% on the year before, to 1,234.5; this finding is confirmed [F1]. Exact duplicates account for 12 of 1250 rows: keep watching [F2]. The most common region is 'East', with 40% of rows: keep watching [F3].",
    technical: 'The average amount rose 12.5% against the prior year, to 1,234.5 (grade CONFIRMED) [F1]. Exact duplicates: 12 of 1250 rows (grade WATCH) [F2]. The next-month value for monthly rows, 190, is graded NOT ENOUGH DATA [F4].'
  };
  const replies = {
    invented: { status: 200, json: { executive: good.executive + ' Expect 47.3 more next year [F1].', technical: good.technical, model: 'check' } },
    rejected: { status: 502, json: { error: 'rejected_wording', part: 'technical', reasons: ['grade_word', 'confidence'] } },
    busy: { status: 429, json: { error: 'upstream_busy' } },
    down: { status: 502, json: { error: 'upstream_error', upstream_status: 503 } },
    good: { status: 200, json: { executive: good.executive, technical: good.technical, model: 'check' } }
  };
  const p = await openTry(ctx, { stubReport: rep, proxy: 'set', proxyReply: () => replies[mode] });
  ok((await p.evaluate(() => window.NL.try.ai_proxy_url)) === PROXY_URL, 'the page did not pick up ai_proxy_url');
  await runStub(p);
  let pre = JSON.parse(await p.textContent('#try-ai-preview'));
  ok(JSON.stringify(pre).indexOf('\'East\'') < 0 && JSON.stringify(pre).indexOf('orders.csv') < 0 && JSON.stringify(pre).indexOf('[value A]') >= 0 && JSON.stringify(pre).indexOf('[your file]') >= 0,
    'the quoted value or the file name is in the payload by default: ' + JSON.stringify(pre.findings).slice(0, 200));
  ok(/exactly as you typed|finding's id/.test(await p.textContent('.tr-ai')) && /China/.test(await p.textContent('.tr-ai')) && /neutral heading/.test(await p.textContent('.tr-ai')),
    'the consent box does not say what is sent, where DeepSeek runs, or what the scan can miss');
  ok(/writes only the words/.test(await p.textContent('.tr-ai')), 'the consent box does not say the model writes no figure');
  await p.check('#try-ai-raw');
  pre = JSON.parse(await p.textContent('#try-ai-preview'));
  ok(JSON.stringify(pre).indexOf('\'East\'') >= 0 && JSON.stringify(pre).indexOf('[value A]') < 0, 'ticking "send them as they are" did not change the preview');
  await p.uncheck('#try-ai-raw');
  ok(await p.isDisabled('#try-ai-go'), 'Send is enabled before consent');
  await p.click('#try-ai-go', { force: true, timeout: 2000 }).catch(() => {});
  await p.waitForTimeout(200);
  ok(!ctx.__ai.length, 'something was sent before consent');
  const send = async () => { await p.check('#try-ai-ok'); await p.click('#try-ai-go'); };
  const note = async (re) => {
    await p.waitForFunction((src) => new RegExp(src).test((document.querySelector('.tr-ai-fallback') || {}).textContent || ''), re.source, { timeout: 20000 });
    return p.textContent('.tr-ai-fallback');
  };
  await send();
  let fb = await note(/set aside/);
  const sent = JSON.parse(ctx.__ai[0]);
  ok(JSON.stringify(Object.keys(sent)) === '["objective","findings","story"]' && sent.findings.every((f) => JSON.stringify(Object.keys(f)) === '["id","claim","verdict","value"]'),
    'the proxy got more than {objective, findings[id, claim, verdict, value], story}: ' + JSON.stringify(Object.keys(sent)));
  ok(JSON.stringify(sent) === JSON.stringify(JSON.parse(await p.textContent('#try-ai-preview'))), 'what was sent differs from the "Exactly what is sent" preview');
  ok(ctx.__ai[0].indexOf(rep.input.sha256) < 0 && ctx.__ai[0].indexOf(rep.downloads.clean_csv.split('\n')[1]) < 0, 'the file fingerprint or a row went to the proxy');
  ok(/executive summary wrote a number of its own/.test(fb) && /engine's story is shown/.test(fb) && !/47\.3/.test(fb), 'a number the model wrote was not refused, or its text was echoed: ' + fb);
  ok(await p.isVisible('#try-story .tr-engine-label') && !(await p.$('#try-story .tr-ai-sum')), 'a refused summary is on screen');
  mode = 'rejected'; await send();
  fb = await note(/502/);
  ok(/technical summary wrote a grade of its own; used confidence wording/.test(fb), 'the proxy\'s refusal reasons are not given: ' + fb);
  mode = 'busy'; await send();
  fb = await note(/429/);
  ok(/busy/.test(fb) && /engine's story is shown/.test(fb), 'a DeepSeek 429 does not fall back to the engine story: ' + fb);
  mode = 'down'; await send();
  fb = await note(/not available right now \(the proxy answered 502\)/);
  mode = 'good'; await send();
  await tryUntil(p, '#try-story .tr-ai-sum', 20000);
  const d = await p.evaluate(() => {
    const t = (e) => e ? e.textContent.replace(/\s+/g, ' ').trim() : null;
    const box = document.querySelector('#try-story .tr-ai-sum');
    return { label: t(box.querySelector('.tr-ai-label')), limit: t(box.querySelector('.tr-ai-limit')), heads: Array.from(box.querySelectorAll('h4')).map(t),
      executive: t(box.querySelector('[data-part="executive"]')), technical: t(box.querySelector('[data-part="technical"]')),
      cite: (box.querySelector('sup.tr-cite') || {}).title, lead: t(document.querySelector('#try-story .tr-lead')), engine: !!document.querySelector('#try-story .tr-engine-label'),
      slotsLeft: /\{[FS]\d|NONE_CONFIRMED/.test(box.textContent), toggle: !!document.querySelector('#try-story [data-wording]') };
  });
  ok(d.label === 'AI-reworded summaries (DeepSeek); every figure and grade put in by the engine', 'the AI label is wrong: ' + d.label);
  ok(/wrote only the words/.test(d.limit) && /cannot prove that a sentence means what the finding means/.test(d.limit), 'the caveat does not say what the check cannot see: ' + d.limit);
  ok(JSON.stringify(d.heads) === '["Executive summary","Technical summary"]', 'the two parts are not both shown: ' + JSON.stringify(d.heads));
  ok(d.executive === shown.executive, 'the executive summary on screen is wrong: ' + d.executive);
  ok(d.technical === shown.technical, 'the technical summary on screen is wrong: ' + d.technical);
  ok(d.cite === rep.findings[0].claim, 'a citation does not carry the engine\'s claim: ' + d.cite);
  ok(!d.slotsLeft, 'a slot was left on screen');
  ok(d.engine && d.lead === squash(rep.story.headline) && !d.toggle, 'the AI summaries replaced the engine story instead of sitting under it');
  ok(ctx.__ai.length === 5, ctx.__ai.length + ' proxy requests for 5 presses of Send');
  ok(!p.__errs.length, 'page error: ' + p.__errs[0]);
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
  ok(await p.isHidden('#try-q-wrap'), 'the question box shows although no AI wording is offered');
  await runStub(p);
  ok(!(await p.$('#try-report .tr-q')), 'the report echoes a question that nothing used');
  await p.close();
  const ctx2 = await ctx.browser().newContext({ viewport: DESK, reducedMotion: 'reduce' });
  try {
    p = await openTry(ctx2, { stubReport: rep, proxy: 'set' });
    ok(await p.isVisible('#try-q-wrap'), 'with AI wording on, the question box is hidden');
    const order = await p.evaluate(() => document.getElementById('try-q-wrap').compareDocumentPosition(document.getElementById('try-start')) & Node.DOCUMENT_POSITION_FOLLOWING);
    ok(order, 'the question box does not come before the file picker');
    ok(/used only if you choose AI summaries/.test(await p.textContent('#try-q-wrap')), 'the question box does not say what it is used for');
    await p.fill('#try-q', 'Which units pay late?');
    await p.click('#try-sample');
    await tryUntil(p, '#try-pd:not([hidden])');
    ok(await p.isDisabled('#try-q'), 'the question box can be edited while the engine runs');
    await p.click('#try-pd-go');
    await tryUntil(p, '#try-report:not([hidden])');
    ok(!(await p.isDisabled('#try-q')), 'the question box stays locked after the report');
  } finally { await ctx2.close(); }
});

check('try-personal-data-step-says-what-each-choice-does', DESK, async (ctx) => {
  const rep = stubReport();
  rep.privacy.flagged = [{ column: 'customer_email', kind: 'email; coded as it arrived', decision: 'withhold' }, { column: 'notes', kind: 'free text', decision: 'withhold' }];
  const p = await openTry(ctx, { stubReport: rep, proxy: 'unset' });
  await p.click('#try-sample');
  await tryUntil(p, '#try-pd:not([hidden])');
  const t = await p.textContent('#try-pd');
  ok(!/before any analysis|counts still work/.test(t), 'the choices still promise what the engine does not do');
  ok(/data-health findings on this page still name it and count its blanks and spellings/.test(t) && /nothing that names it is sent to an AI/.test(t) && /still leaves the column out/.test(t) && /neutral heading/.test(t), 'the choices do not say what happens: ' + t.slice(0, 240));
  ok(await p.isDisabled('#try-pd input[data-col="customer_email"][value="keep"]') && !(await p.isDisabled('#try-pd input[data-col="notes"][value="keep"]')),
    'Keep is offered for a column coded as it arrived (or refused for one that was not)');
  await p.click('#try-pd-go');
  await tryUntil(p, '#try-report:not([hidden])');
  const priv = await p.textContent('.tr-priv');
  ok(/data-health findings still name the column and count its blanks and spellings/.test(priv) && !/dropped before analysis|left out of the business analysis, the story/.test(priv), 'the report\'s personal-data note overclaims');
});

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

/* ------------------------------------------------------------ report v2: the manager and analyst views
   The engine's report contract v2 (engine/CONTRACT-v2.md) drawn as two views from one report (plan §4.1)
   with the §5 charts. These checks feed the page, through the stand-in worker, reports the adapter
   (engine/nl_browser.py) wrote natively for the site's sample and for four invented business files
   (tools/make_ui_fixtures.py: a rent roll, a sales ledger with categories, a web analytics export and
   an 18-month file), then compare what the page shows with the report, number by number. */
const PY = process.env.PY || path.join(SITE_DIR, '..', 'agent-demo', 'venv', 'bin', 'python');
const FX_NAMES = ['sample', 'rent-roll', 'sales-ledger', 'web-analytics', 'cafe-invoices-18m', 'rent-roll-gated', 'shop-margins-36m'];
let FX_DIR = null;
function fixture(name) {
  if (!FX_DIR) {
    const dir = process.env.NL_UI_FIXTURES || fs.mkdtempSync(path.join(require('os').tmpdir(), 'nl-ui-fx-'));
    if (!FX_NAMES.every((n) => fs.existsSync(path.join(dir, n + '.json')))) {
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
  const drawn = rep.charts.filter((c) => c.view === 'manager' && ['kpi', 'findings_table'].indexOf(c.id) < 0).length;
  const words = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
  moved.forEach((c) => ok(c.why_shown.indexOf('draws at most ' + words[drawn] + ' charts') >= 0 && !/at most six charts/.test(c.why_shown), c.id + ': the cap note does not count the ' + drawn + ' drawn charts: ' + c.why_shown));
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
  ok(d.supp.length === rep.charts_suppressed.length && rep.charts_suppressed.every((s, i) => d.supp[i][0] === s.rule && d.supp[i][1].indexOf(squash(s.why)) >= 0), 'the absent charts are not each listed with why: ' + JSON.stringify(d.supp.slice(0, 2)));
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
          Array.from(f.querySelectorAll('g.hc2')).filter((g) => !/^\d[\d,]*$/.test((g.querySelector('.cv2') || {}).textContent || '') || !/[○◐●]/.test((g.querySelector('.gl2') || {}).textContent || '')).length]) };
    });
    const want = rep.charts.map((c) => c.id).sort();
    ok(JSON.stringify(d.ids) === JSON.stringify(want), name + ': charts drawn ' + JSON.stringify(d.ids) + ', the rules select ' + JSON.stringify(want));
    ok(!d.empty.length, name + ': empty charts ' + JSON.stringify(d.empty));
    ok(!d.unlinked.length, name + ': charts with no claim link ' + JSON.stringify(d.unlinked));
    ok(JSON.stringify(d.supp) === JSON.stringify(rep.charts_suppressed.map((s) => s.rule)), name + ': suppressed rules listed ' + JSON.stringify(d.supp));
    d.cells.forEach(([id, n, labels, bad]) => {
      const c = chartOf(rep, id).data;
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
  await p.evaluate(() => { window.print = () => {
    const A = document.getElementById('nl2-analyst'), M = document.getElementById('nl2-manager');
    const shown = (e) => !!e && getComputedStyle(e).display !== 'none';
    window.__printed = { cls: document.body.classList.contains('print-try'), m: shown(M), a: shown(A),
      drawn: Array.from(A.querySelectorAll('figure[data-chart] .viz')).filter((v) => !v.querySelector('svg, table')).length,
      figs: A.querySelectorAll('figure[data-chart]').length, closed: document.querySelectorAll('#try-report details:not([open])').length }; }; });
  await p.click('#try-report [data-act="print"]');
  await p.waitForTimeout(150);
  const pr = await p.evaluate(() => window.__printed);
  ok(pr && pr.cls && pr.m && pr.a && pr.figs > 0 && pr.drawn === 0 && pr.closed === 0, 'while printing: ' + JSON.stringify(pr));
  await p.evaluate(() => document.body.classList.add('print-try'));
  await p.emulateMedia({ media: 'print' });
  const css = await p.evaluate(() => {
    const vis = (s) => Array.from(document.querySelectorAll(s)).filter((e) => getComputedStyle(e).display !== 'none').length;
    return { views: vis('.nl2-views'), links: vis('#try-report .nl2-link'), tbl: vis('#try-report .tbl-btn'), m: vis('#nl2-manager'), a: vis('#nl2-analyst'),
      figBreak: getComputedStyle(document.querySelector('#nl2-analyst figure[data-chart]')).breakInside, secBreak: getComputedStyle(document.querySelector('#nl2-analyst section.nl2-sec[data-sec="data"]')).breakBefore };
  });
  ok(!css.views && !css.links && !css.tbl && css.m && css.a, 'print shows the view switch or buttons, or leaves a view out: ' + JSON.stringify(css));
  ok(css.figBreak === 'avoid' && css.secBreak === 'page', 'print can split a chart, or the paper sections do not start on a new page: ' + JSON.stringify(css));
  ok(rep.charts.length > 0 && !p.__errs.length, 'page error: ' + p.__errs[0]);
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
