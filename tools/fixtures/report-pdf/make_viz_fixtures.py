"""Builds viz-results.json and viz-response.json: the chart registry's records for the report PDF's checks.

    python3 tools/fixtures/report-pdf/make_viz_fixtures.py

From tools/fixtures/viz/spec.json (version 2026-09-30.1): its 14 example records, as they are, on the ship2 results
(ship2-results-v2.json: its scenarios, findings, tables and input), plus four illustrative records the examples do not
cover, each checked with the spec's own check_record (and its JSON Schema): a change heatmap of 4 regions x 24
months (YYYY-MM columns too narrow for their labels, a table view of 25 columns), a crosstab of 3 ratings x 14 long
category names (drawn turned a quarter), a Pareto of 8 bars whose k80 is a bar (drawn as vertical bars), and a
contribution waterfall of 3 regions (vertical bars, its totals only 2 digits long). Three records a reader must
survive are written on purpose outside the schema: a kind no reader knows ("sankey", not degraded by a worker), a
heatmap whose tier grid is the wrong shape, and a kind no reader knows with no table.

The results (viz-results.json):
  results      the 10-chart report: every draw kind (10 records, AI_CHARTS_MAX), the ship2 results around them
  edge         the edge cases: negative totals, all-empty and 1-row heatmaps, non-Latin labels, the 24-month and
               14-column heatmaps, the short Pareto, a page record ({type: 'viz', data}), an analysis's bars chart,
               the unknown kind, the malformed heatmap and the unknown kind with no table
  auto         the contribution waterfall alone (the AI report places none: Part 1 draws it)
The reports (viz-response.json): ten, edge and auto, each {report, sources, model, repaired, removed_figures}.
"""
import copy
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VIZ = os.path.join(os.path.dirname(HERE), 'viz')
sys.path.insert(0, VIZ)
import validate_spec  # noqa: E402

SPEC = validate_spec.load(os.path.join(VIZ, 'spec.json'))
EX = {k: v['record'] for k, v in SPEC['examples'].items()}
MINUS = '−'


def pct_text(v):
    """A percent as the engine writes a cell's text: 3 significant digits, a sign, a true minus."""
    a = abs(v)
    s = ('%.3g' % a)
    if 'e' in s:
        s = '%.0f' % a
    return ('+' if v > 0 else MINUS if v < 0 else '') + s + '%'


def int_text(v):
    return '{:,}'.format(int(round(v)))


def tertiles(xs):
    """numpy's 'linear' 1/3 and 2/3 percentiles."""
    s = sorted(xs)
    def q(p):
        pos = (len(s) - 1) * p
        lo = math.floor(pos)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)
    return q(1 / 3), q(2 / 3)


def base(chart, kind, section, n, title, subtitle, measure, summary, source, columns, rows, months, op):
    return {'id': 'viz.%d.%s' % (n, chart), 'chart': chart, 'kind': kind, 'title': title, 'subtitle': subtitle,
            'section': section, 'supports': 'Change in the monthly total of revenue, latest 12 months against the 12 before',
            'anchors': [], 'grade': None, 'parent_grade': None, 'chosen_by': 'ai',
            'why': 'Illustrative: a case the spec examples do not cover.', 'measure': measure, 'data': {},
            'table': {'cols': [], 'rows': []}, 'summary': summary, 'suppressed': {'cells': 0, 'why': ''},
            'source': source, 'inputs': {'columns': columns, 'rows': rows, 'months': months, 'op': op}}


def change_heatmap_24():
    rows = ['East', 'North', 'South', 'West']
    cols = ['%d-%02d' % (y, m) for y in (2024, 2025) for m in range(1, 13)]
    vals, texts, ns = [], [], []
    seed = 20260930
    supp = 0
    for i, _ in enumerate(rows):
        vr, tr, nr = [], [], []
        for j, _ in enumerate(cols):
            seed = (seed * 1103515245 + 12345) % 2147483648
            u = seed / 2147483648.0
            if (i == 2 and j in (3, 4)) or (i == 3 and j == 17):
                vr.append(None); tr.append('<5'); nr.append(None); supp += 1
                continue
            if i == 1 and j == 0:
                vr.append(None); tr.append(''); nr.append(0)
                continue
            v = round((u - 0.42) * 70, 4)
            vr.append(v); tr.append(pct_text(v)); nr.append(5 + int(u * 30))
        vals.append(vr); texts.append(tr); ns.append(nr)
    shown = [abs(v) for row in vals for v in row if v not in (None, 0)]
    q1, q2 = tertiles(shown)
    mx = max(shown)
    tier = [[0 if v is None or v == 0 else (1 if v > 0 else -1) * (1 if abs(v) <= q1 else 2 if abs(v) <= q2 else 3) for v in row] for row in vals]
    used = sorted({t for row in tier for t in row if t})
    words = {-3: '%s to %s' % (pct_text(-mx), pct_text(-q2)), -2: '%s to %s' % (pct_text(-q2), pct_text(-q1)), -1: '%s to 0%%' % pct_text(-q1),
             1: '0%% to %s' % pct_text(q1), 2: '%s to %s' % (pct_text(q1), pct_text(q2)), 3: '%s to %s' % (pct_text(q2), pct_text(mx))}
    r = base('change_heatmap', 'heatmap', 'drove', 4, 'Revenue by region and month, change on the year before, 2024 and 2025',
             "Each region's revenue in the month against the same month a year before, in percent",
             {'label': 'revenue', 'unit': '', 'kind': 'change_pct'},
             'Illustrative: 24 months of year-on-year change by region; the widest swings are in South and West. '
             'Three cells rest on fewer than 5 rows and one month has no rows.',
             'Illustrative: 4 regions over 24 months (the columns the PDF names by month and year)',
             ['revenue', 'region'], 900, ['2023-01', '2025-12'], 'revenue added up by region and month, percent change on the same month a year before')
    r['data'] = {'rows': rows, 'cols': cols, 'values': vals, 'text': texts, 'tier': tier, 'n': ns, 'scale': 'diverging',
                 'legend': [{'tier': t, 'text': words[t]} for t in used], 'row_label': 'region', 'col_label': 'Month'}
    r['suppressed'] = {'cells': supp, 'why': '%d cells rest on fewer than 5 rows in one of the two years' % supp}
    r['table'] = {'cols': ['region'] + cols, 'rows': [[rows[i]] + texts[i] for i in range(len(rows))]}
    return r


def crosstab_long():
    rows = ['1 star', '3 stars', '5 stars']
    cols = ['Headsets and gaming audio', 'Controllers and gamepads', 'Keyboards and mice for gaming', 'Consoles, refurbished',
            'Virtual reality headsets', 'Charging stations and docks', 'Replacement shells and buttons', 'Racing wheels and pedals',
            'Streaming and capture cards', 'Gaming chairs and desks', 'Memory cards and storage', 'Cables, adapters and hubs',
            'Protective cases and bags', 'Other gaming accessories']
    counts = [[31, 44, 12, 9, 17, 8, 22, 5, 6, 11, 7, 14, 9, 13],
              [18, 26, 15, 6, 9, 5, 11, 7, 5, 8, 6, 9, 5, 10],
              [212, 305, 146, 48, 97, 61, 88, 39, 42, 57, 44, 71, 50, 66]]
    vals = [[float(c) for c in row] for row in counts]
    shown = [v for row in vals for v in row]
    q1, q2 = tertiles(shown)
    tier = [[1 if v <= q1 else 2 if v <= q2 else 3 for v in row] for row in vals]
    r = base('crosstab_heatmap', 'heatmap', 'other', 5, 'Reviews by star rating and product category, 2022',
             'Reviews in 2022 by stars and by the 13 largest product categories and all others',
             {'label': 'rows', 'unit': '', 'kind': 'count'},
             'Five-star reviews dominate every category; controllers (305) and headsets (212) hold the most. '
             'One-star reviews are most common for controllers (44) and headsets (31).',
             'Illustrative: 14 long category names (the PDF turns the grid a quarter so they read whole)',
             ['stars', 'category'], 2106, ['2022-01', '2022-12'], 'rows counted by stars and category')
    r['data'] = {'rows': rows, 'cols': cols, 'values': vals, 'text': [[int_text(v) for v in row] for row in vals], 'tier': tier,
                 'n': [[int(v) for v in row] for row in vals], 'scale': 'sequential',
                 'legend': [{'tier': 1, 'text': '5 to %s' % int_text(q1)}, {'tier': 2, 'text': '%s to %s' % (int_text(q1), int_text(q2))},
                            {'tier': 3, 'text': '%s to 305' % int_text(q2)}], 'row_label': 'stars', 'col_label': 'category'}
    r['table'] = {'cols': ['stars'] + cols, 'rows': [[rows[i]] + [int_text(v) for v in vals[i]] for i in range(3)]}
    return r


def pareto_short():
    labels = ['Headsets', 'Controllers', 'Keyboards', 'Mice', 'Chairs', 'Cables', 'Docks', 'Cases']
    vals = [300, 250, 180, 120, 90, 40, 20, 10]
    other = 25
    total = sum(vals) + other
    run, bars = 0, []
    for lab, v in zip(labels, vals):
        run += v
        bars.append({'label': lab, 'value': v, 'text': int_text(v), 'cum_pct': round(100.0 * run / total, 6), 'cum_text': '%.1f%%' % (100.0 * run / total)})
    k = next(i + 1 for i, b in enumerate(bars) if b['cum_pct'] >= 80)
    r = base('pareto', 'pareto', 'other', 6, 'Revenue by product: the top 8 and the rest',
             'Revenue in 2025 by product, largest first, with the running share of all %s' % int_text(total),
             {'label': 'revenue', 'unit': '', 'kind': 'amount'},
             'Headsets lead with 300 of the %s; %d of the 14 products make up 80%% of the revenue.' % (int_text(total), k),
             'Illustrative: 14 products, 6 of them folded into other',
             ['revenue', 'product'], 480, ['2025-01', '2025-12'], 'revenue added up by product, largest first')
    r['data'] = {'bars': bars, 'other': {'label': 'other', 'value': other, 'text': int_text(other), 'cum_pct': 100, 'cum_text': '100.0%', 'n_entities': 6},
                 'total': {'value': total, 'text': int_text(total)}, 'k80': {'k': k, 'of': 14, 'text': '%d of the 14 products account for 80%% of the revenue' % k}}
    r['table'] = {'cols': ['product', 'Revenue', 'Running share'], 'rows': [[b['label'], b['text'], b['cum_text']] for b in bars] + [['other', int_text(other), '100.0%']]}
    return r


def waterfall_short():
    st = [('12 months before', 58.0, 'total'), ('North', 9.5, 'step'), ('South', -4.0, 'step'), ('East', 2.5, 'step'), ('Latest 12 months', 66.0, 'total')]
    steps, run = [], 0.0
    for lab, v, k in st:
        if k == 'total':
            steps.append({'label': lab, 'value': v, 'text': int_text(v), 'kind': 'total', 'from': 0, 'to': v})
            run = v
        else:
            steps.append({'label': lab, 'value': v, 'text': ('+' if v > 0 else MINUS) + ('%g' % abs(v)), 'kind': 'step', 'from': run, 'to': run + v})
            run += v
    r = base('contribution_waterfall', 'waterfall', 'drove', 7, 'Where the change in orders came from, by region',
             "Orders, 2024 against 2025: each region's contribution to the change",
             {'label': 'orders', 'unit': '', 'kind': 'count'},
             'Orders went from 58 to 66 (+8): North added +9.5 and East +2.5, South took away ' + MINUS + '4.',
             'Illustrative: a small total, so the vertical bars have short texts',
             ['orders', 'region'], 124, ['2024-01', '2025-12'], 'orders counted by region in each window')
    r['data'] = {'steps': steps, 'basis': {'split': 'segment', 'finding_id': None, 'column': 'region', 'prior': ['2024-01', '2024-12'], 'latest': ['2025-01', '2025-12']},
                 'change': {'value': 8.0, 'text': '+8'}}
    r['table'] = {'cols': ['Step', 'Orders'], 'rows': [[s['label'], s['text']] for s in steps]}
    return r


def checked(r):
    errs = validate_spec.check_record(r, SPEC)
    errs += validate_spec.validate(r, SPEC['schema'], SPEC['schema'])
    if errs:
        raise SystemExit('%s does not pass the spec: %s' % (r['id'], '; '.join(errs[:5])))
    return r


def main():
    ship2 = json.load(open(os.path.join(HERE, 'ship2-results-v2.json')))['results']
    extra = [checked(f()) for f in (change_heatmap_24, crosstab_long, pareto_short, waterfall_short)]
    for k, r in EX.items():
        checked(r) if r['kind'] != 'table' else None
    ten = [EX['waterfall'], EX['edge_waterfall_pvm'], EX['heatmap'], EX['dot_range'], EX['pareto'], EX['slope'],
           EX['table'], EX['edge_heatmap_diverging'], EX['edge_heatmap_suppressed'], EX['edge_waterfall_12_long_labels']]
    ten = [copy.deepcopy(r) for r in ten]
    for i, r in enumerate(ten):
        r['id'] = 'viz.%d.%s' % (i + 1, r['chart'])
    sankey = copy.deepcopy(EX['table'])
    sankey['kind'] = 'sankey'
    sankey['id'] = 'viz.9.sankey_flow'
    sankey.pop('degraded', None)
    sankey['data'] = {'links': [['East', 'Warehouse A', 41], ['East', 'Warehouse B', 30]]}
    bad = copy.deepcopy(EX['edge_heatmap_one_row'])
    bad['id'] = 'viz.10.theme_rating_heatmap'
    bad['data']['tier'] = [[0, 0, 1]]           # the wrong shape: 1 x 3 for a 1 x 5 grid
    bad['title'] = 'What the comment texts say, by rating (a record whose tier grid is the wrong shape)'
    radar = {'id': 'viz.11.radar', 'chart': 'radar', 'kind': 'radar', 'title': 'Five measures of each region on a radar',
             'subtitle': '', 'section': 'other', 'supports': '', 'anchors': [], 'grade': None, 'parent_grade': None, 'chosen_by': 'ai',
             'why': 'A kind no reader knows, with no table.', 'measure': {'label': 'rows', 'unit': '', 'kind': 'count'}, 'data': {'spokes': 5},
             'table': {'cols': [], 'rows': []}, 'summary': 'A radar of five measures.', 'suppressed': {'cells': 0, 'why': ''},
             'source': 'Illustrative', 'inputs': {'columns': [], 'rows': None, 'months': None, 'op': ''}}
    page_rec = {'id': 'viz.12.group_ranges', 'rule': 'V', 'type': 'viz', 'title': EX['dot_range']['title'], 'view': 'manager', 'default_visible': True,
                'finding_ids': [], 'why_shown': '', 'source': EX['dot_range']['source'], 'data': copy.deepcopy(EX['dot_range'])}
    bars = copy.deepcopy(next(c for c in ship2['charts'] if c.get('kind') == 'bars'))
    edge = [EX['edge_waterfall_negative_totals'], EX['edge_heatmap_all_empty'], EX['edge_heatmap_one_row'], EX['edge_slope_non_latin'],
            extra[0], extra[1], extra[2], extra[3], sankey, bad, radar, page_rec, bars]
    edge = [copy.deepcopy(r) for r in edge]
    res = copy.deepcopy(ship2)
    res['charts'] = ten
    out = {'about': 'The chart registry records for the report PDF checks (tools/check_report_pdf.mjs), made by make_viz_fixtures.py '
                    'from tools/fixtures/viz/spec.json %s and ship2-results-v2.json. results: the 10-chart report; edge: the edge cases '
                    '(charts only, on the same results); auto: the contribution waterfall the AI did not place.' % SPEC['version'],
           'spec_version': SPEC['version'], 'results': res, 'edge': {'charts': edge}, 'auto': {'charts': [copy.deepcopy(EX['waterfall'])]}}
    with open(os.path.join(HERE, 'viz-results.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write('\n')

    resp2 = json.load(open(os.path.join(HERE, 'ship2-response-v2.json')))
    src = [{'title': s['title'], 'link': s['link']} for s in resp2['sources'][:4]]
    ten_report = '\n'.join([
        'Revenue rose 18.7% on 1.3% fewer orders, and East carried most of it',
        '## Executive summary',
        '- Revenue rose 18.7%, from 128,256 to 152,214, graded WATCH [S1].',
        '- East contributed +16,220 of the +23,958 change and North +7,116.',
        '## The headline: revenue rose, month by month',
        'Revenue rose in most months of 2025 against 2024.',
        '[CHART:3]',
        'The Canadian dollar moved within a few percent a month over ten years.',
        '[CHART:8]',
        '## What drove it: East and price',
        'East carried most of the change, and price more than volume.',
        '[CHART:1]',
        '[CHART:2]',
        'A sales file of 12 territories shows how a long list of parts reads.',
        '[CHART:10]',
        '## Other findings: order value by region',
        'North has the highest average order and West the lowest.',
        '[CHART:4]',
        '[CHART:5]',
        '[CHART:9]',
        '[CHART:7]',
        '## In the real world',
        'Retail demand was firm [S2].',
        '## Scenarios',
        'East overtook North.',
        '[CHART:6]',
        '## What to do',
        '1. Watch East.',
        '## Risks and what the data cannot say',
        '- The change is an association with the period, not its cause.'])
    edge_report = '\n'.join([
        'The edge cases of the chart registry',
        '## Executive summary',
        '- Every kind the PDF draws, at its edges [S1].',
        '## The headline: losses and empty grids',
        '[CHART:1]',
        '[CHART:2]',
        '[CHART:3]',
        '## What drove it: long axes',
        '[CHART:5]',
        '[CHART:8]',
        '## Other findings: what a reader must survive',
        '[CHART:6]',
        '[CHART:7]',
        '[CHART:9]',
        '[CHART:10]',
        '[CHART:11]',
        '[CHART:12]',
        '[CHART:13]',
        '## Scenarios',
        '[CHART:4]',
        '## What to do',
        '1. Read the tables.',
        '## Risks and what the data cannot say',
        '- Illustrative records.'])
    auto_report = '\n'.join([
        'Revenue rose 18.7%, most of it in East',
        '## Executive summary',
        '- Revenue rose 18.7% [S1].',
        '## The headline: revenue rose',
        'Revenue rose from 128,256 to 152,214.',
        '## What to do',
        '1. Watch East.'])
    common = {'sources': src, 'model': 'deepseek-flash', 'repaired': 0, 'removed_figures': []}
    resp = {'about': 'The AI reports placing the records of viz-results.json (make_viz_fixtures.py).',
            'ten': dict(report=ten_report, **common), 'edge': dict(report=edge_report, **common), 'auto': dict(report=auto_report, **common)}
    with open(os.path.join(HERE, 'viz-response.json'), 'w', encoding='utf-8') as f:
        json.dump(resp, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print('viz-results.json: %d + %d + 1 charts; viz-response.json: 3 reports' % (len(ten), len(edge)))


if __name__ == '__main__':
    main()
