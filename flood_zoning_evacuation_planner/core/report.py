# -*- coding: utf-8 -*-
"""Report tables in plain language, written to Word (.docx) or HTML.

Standard library only (no python-docx needed), so it works in every QGIS install.
The input is plain Python data (lists of dicts) read from the plugin outputs by
``algorithms/report_tables.py``; this module only arranges and writes it.
"""
import datetime
import html
import re
import zipfile
from xml.sax.saxutils import escape

DASH = '–'

# short, plain names for the planning zones (the long rule text comes from the layer)
ZONE_SHORT = {1: 'No new building', 2: 'Restricted building', 3: 'Building with conditions',
              4: 'River protection strip'}


# --------------------------------------------------------------------------- helpers
def num(v, dec=1):
    """Number with thousands separator, or a dash when missing."""
    if v is None or v == '':
        return DASH
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v != v:     # NaN
        return DASH
    return '{0:,.{1}f}'.format(v, dec)


def natural_key(text):
    """Sort 'RS-2' before 'RS-10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', str(text or ''))]


def _f(v):
    try:
        v = float(v)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def _light(hex_colour, mix=0.55):
    """Lighter tint of a colour (for table cells, so text stays readable)."""
    h = (hex_colour or '#ffffff').lstrip('#')
    if len(h) != 6:
        return 'FFFFFF'
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return ''.join('%02X' % int(c + (255 - c) * mix) for c in rgb)


class Table:
    """A table: caption, column headers, rows of strings, a 'how to read' note.

    align: 'l' or 'r' per column. shade: optional fill colour (hex) per row for the
    first cell. total: index of a row to print in bold (e.g. the total row).
    """

    def __init__(self, caption, columns, rows, note='', align=None, shade=None, bold_rows=()):
        self.caption = caption
        self.columns = columns
        self.rows = [[DASH if c is None else str(c) for c in r] for r in rows]
        self.note = note
        self.align = align or ['l'] * len(columns)
        self.shade = shade or [None] * len(self.rows)
        self.bold_rows = set(bold_rows)

    def widths(self, total, char=92, pad=200):
        """Column widths (twips) that fit ``total``: numbers and single words never wrap;
        the space left goes to the text columns in proportion to their text length."""
        need, want = [], []
        for j, head in enumerate(self.columns):
            cells = [r[j] for r in self.rows]
            words = [len(w) for c in cells + [head] for w in c.split()] or [3]
            if self.align[j] == 'r':
                mn = max(max(len(c) for c in cells) if cells else 3, max(len(w) for w in head.split()))
            else:
                mn = max(words)
            mn = max(mn, (len(head) + 1) // 2)          # header on at most two lines
            lens = sorted(len(c) for c in cells) or [3]
            typical = lens[int(0.9 * (len(lens) - 1))]
            need.append(mn * char + pad)
            want.append(max(mn, min(typical, 70)) * char + pad)
        if sum(need) >= total:          # very wide table: shrink everything evenly
            f = total / float(sum(need))
            out = [int(n * f) for n in need]
        else:
            extra = [w - n for w, n in zip(want, need)]
            room = total - sum(need)
            if sum(extra) <= room:     # everything fits: share the rest in proportion to the wanted width
                out = [int(w + (room - sum(extra)) * w / float(sum(want))) for w in want]
            else:
                out = [int(n + room * e / float(sum(extra))) for n, e in zip(need, extra)]
        out[-1] += total - sum(out)
        return out


class Report:
    def __init__(self, title, subtitle=''):
        self.title = title
        self.subtitle = subtitle
        self.blocks = []          # (kind, content)

    def heading(self, text):
        self.blocks.append(('h', text))

    def para(self, text):
        self.blocks.append(('p', text))

    def bullets(self, items):
        if items:
            self.blocks.append(('ul', list(items)))

    def table(self, t):
        if t.rows:
            self.blocks.append(('table', t))

    def n_tables(self):
        return sum(1 for k, _ in self.blocks if k == 'table')

    def write(self, path):
        if str(path).lower().endswith(('.htm', '.html')):
            write_html(self, path)
        else:
            write_docx(self, path)


# --------------------------------------------------------------------------- content
def build_report(title='', zones=None, compare=None, evac=None, havens=None, routes=None):
    """Arrange the plugin outputs into a report.

    zones / compare: dict(label=str, items=[dict(code, zone, hex, criteria, rule, area_ha, settled_ha)])
    evac:   [dict(zone_id, settl_type, name, n_settl, area_ha, risk_ha)]
    havens: [dict(haven_id, zone_id, zone_ha, haven_ha, ratio, dist_flood, elev_min, elev_max,
                  height_chk, facility)]
    routes: [dict(route_id, zone_id, sector, settl_type, haven_id, length_m, walk_min,
                  main_pct, minor_pct, path_pct, open_pct, wet_m)]
    Every list is optional; only the tables with data are written.
    """
    today = datetime.date.today().strftime('%d %B %Y').lstrip('0')
    rep = Report(title or 'Flood zoning and evacuation planning', 'Report tables ' + DASH + ' ' + today)
    evac = sorted(evac or [], key=lambda z: natural_key(z.get('zone_id')))
    havens = sorted(havens or [], key=lambda h: natural_key(h.get('zone_id') or h.get('haven_id')))
    routes = sorted(routes or [], key=lambda r: natural_key(r.get('route_id')))
    zones = _merge_zone_items(zones)
    compare = _merge_zone_items(compare)

    rep.heading('Key figures')
    rep.bullets(_summary(zones, compare, evac, havens, routes))

    n = [0]

    def caption(text):
        n[0] += 1
        return 'Table %d. %s' % (n[0], text)

    if zones:
        rep.heading('Spatial planning zones')
        rep.table(_zones_table(caption, zones))
    if zones and compare:
        rep.table(_compare_table(caption, zones, compare))
    if evac:
        rep.heading('Evacuation zones')
        rep.table(_evac_table(caption, evac, havens))
    if havens:
        rep.heading('Safe havens')
        rep.table(_havens_table(caption, havens))
    if routes:
        rep.heading('Evacuation routes')
        rep.table(_routes_summary_table(caption, routes, evac))
        rep.table(_routes_table(caption, routes))
    rep.heading('Terms used')
    rep.bullets(_glossary(zones, evac, havens, routes))
    return rep


def _merge_zone_items(z):
    """Add up features with the same zone code (a layer may hold several pieces per zone)."""
    if not z or not z.get('items'):
        return None
    by = {}
    for it in z['items']:
        code = int(it.get('code') or 0)
        cur = by.setdefault(code, dict(it, area_ha=0.0, settled_ha=None))
        cur['area_ha'] += _f(it.get('area_ha')) or 0.0
        if _f(it.get('settled_ha')) is not None:
            cur['settled_ha'] = (cur['settled_ha'] or 0.0) + _f(it['settled_ha'])
    return dict(label=z.get('label') or '', items=[by[c] for c in sorted(by)],
                has_settled=any(_f(i.get('settled_ha')) is not None for i in z['items']))


def _flood_total(zones):
    return sum(i['area_ha'] for i in zones['items'] if i['code'] in (1, 2, 3))


def _summary(zones, compare, evac, havens, routes):
    s = []
    if zones:
        tot = _flood_total(zones)
        part = ', '.join('%s %s ha' % (i.get('colour') or i.get('zone'), num(i['area_ha']))
                         for i in zones['items'] if i['code'] in (1, 2, 3))
        lab = (' (%s)' % zones['label']) if zones['label'] else ''
        s.append('The flood zones%s cover %s ha in total: %s.' % (lab, num(tot), part))
        green = [i for i in zones['items'] if i['code'] == 4]
        if green:
            s.append('The river protection strip covers %s ha.' % num(green[0]['area_ha']))
        if zones['has_settled']:
            st = sum(i['settled_ha'] or 0 for i in zones['items'] if i['code'] in (1, 2))
            s.append('%s ha of existing settlements lie in the red or orange zones.' % num(st))
    if zones and compare:
        a, b = _flood_total(zones), _flood_total(compare)
        if a > 0:
            word = 'smaller' if b < a else 'larger'
            s.append('Compared with "%s", the flood zones of "%s" are %s ha (%s%%) %s.' % (
                zones['label'] or 'first scenario', compare['label'] or 'second scenario',
                num(abs(b - a)), num(abs(b - a) / a * 100, 0), word))
    if evac:
        n_settl = sum(int(_f(z.get('n_settl')) or 1) for z in evac)
        area = sum(_f(z.get('area_ha')) or 0 for z in evac)
        s.append('%d settlements in %d evacuation zones need to be evacuated (%s ha).' % (
            n_settl, len(evac), num(area)))
    if havens:
        zid = {h.get('zone_id') for h in havens}
        ratios = [_f(h.get('ratio')) for h in havens if _f(h.get('ratio')) is not None]
        txt = '%d safe havens were found' % len(havens)
        if evac:
            txt += ' (%d of %d evacuation zones)' % (len(zid & {z['zone_id'] for z in evac}), len(evac))
        if ratios:
            txt += '; each is %s to %s times the size of its zone' % (num(min(ratios), 1), num(max(ratios), 1))
        s.append(txt + '.')
        missing = [z['zone_id'] for z in evac if z['zone_id'] not in zid]
        if missing:
            s.append('No safe haven was found for: %s. Check these zones by hand.' % ', '.join(missing))
    if routes:
        lengths = [_f(r.get('length_m')) or 0 for r in routes]
        longest = max(routes, key=lambda r: _f(r.get('length_m')) or 0)
        s.append('%d evacuation routes, %s km in total. The longest is %s: %s km, about %s minutes on foot.' % (
            len(routes), num(sum(lengths) / 1000, 1), longest.get('route_id'),
            num((_f(longest.get('length_m')) or 0) / 1000, 1), num(longest.get('walk_min'), 0)))
        road = _weighted_share(routes, ('main_pct', 'minor_pct'))
        if road is not None:
            s.append('%s%% of the total route length is on roads and tracks.' % num(road, 0))
        wet = [r for r in routes if (_f(r.get('wet_m')) or 0) > 0]
        if wet:
            s.append('%d routes cross ground under deep water (0.3 m or more by default) in the design flood, '
                     '%s m in total. People there must leave before the flood arrives; check these '
                     'stretches on site.' % (
                         len(wet), num(sum(_f(r.get('wet_m')) for r in wet), 0)))
    return s


def _weighted_share(routes, keys):
    tot = sum(_f(r.get('length_m')) or 0 for r in routes)
    if tot <= 0 or all(_f(r.get(keys[0])) is None for r in routes):
        return None
    return sum((_f(r.get('length_m')) or 0) * sum(_f(r.get(k)) or 0 for k in keys) for r in routes) / tot


def _zones_table(caption, zones):
    tot = _flood_total(zones)
    cols = ['Zone', 'Flood condition', 'What it means for planning', 'Area (ha)', 'Share (%)']
    if zones['has_settled']:
        cols.append('Of which settled (ha)')
    rows, shade = [], []
    for i in zones['items']:
        name = i.get('zone') or ZONE_SHORT.get(i['code'], 'Zone %d' % i['code'])
        if i.get('colour'):
            name = '%s (%s)' % (name, i['colour'])
        share = num(i['area_ha'] / tot * 100, 0) if tot and i['code'] in (1, 2, 3) else ''
        row = [name, i.get('criteria'), i.get('rule'), num(i['area_ha']), share]
        if zones['has_settled']:
            row.append(num(i.get('settled_ha')))
        rows.append(row)
        shade.append(i.get('hex'))
    total = ['Total flood zones (red + orange + yellow)', '', '', num(tot), '100']
    if zones['has_settled']:
        total.append(num(sum(i['settled_ha'] or 0 for i in zones['items'] if i['code'] in (1, 2, 3))))
    rows.append(total)
    shade.append(None)
    lab = (' ' + DASH + ' ' + zones['label']) if zones['label'] else ''
    note = ('How to read: each row is one zone. "Flood condition" is the rule used to draw it; '
            '"Share" is its part of the red + orange + yellow area. The protection strip is not '
            'counted in the total.')
    if zones['has_settled']:
        note += ' "Of which settled" is the area of existing settlements inside the zone.'
    return Table(caption('Spatial planning zones' + lab), cols, rows, note,
                 align=['l', 'l', 'l', 'r', 'r', 'r'], shade=shade, bold_rows=[len(rows) - 1])


def _compare_table(caption, a, b):
    la, lb = a['label'] or 'Scenario 1', b['label'] or 'Scenario 2'
    if la == lb:
        la, lb = la + ' (1)', lb + ' (2)'
    ia, ib = {i['code']: i for i in a['items']}, {i['code']: i for i in b['items']}
    rows, shade = [], []
    for code in sorted(set(ia) | set(ib)):
        x, y = (ia.get(code) or {}).get('area_ha', 0.0), (ib.get(code) or {}).get('area_ha', 0.0)
        it = ia.get(code) or ib.get(code)
        name = it.get('zone') or ZONE_SHORT.get(code, '')
        if it.get('colour'):
            name = '%s (%s)' % (name, it['colour'])
        rows.append([name, num(x), num(y), _signed(y - x), _signed((y - x) / x * 100, 0) if x else DASH])
        shade.append(it.get('hex'))
    ta, tb = _flood_total(a), _flood_total(b)
    rows.append(['Total flood zones (red + orange + yellow)', num(ta), num(tb), _signed(tb - ta),
                 _signed((tb - ta) / ta * 100, 0) if ta else DASH])
    shade.append(None)
    note = ('How to read: the area of each zone in the two scenarios. A negative change means the '
            'zone is smaller in "%s" than in "%s".' % (lb, la))
    return Table(caption('Comparison of planning zones: %s and %s' % (la, lb)),
                 ['Zone', '%s (ha)' % la, '%s (ha)' % lb, 'Change (ha)', 'Change (%)'], rows, note,
                 align=['l', 'r', 'r', 'r', 'r'], shade=shade, bold_rows=[len(rows) - 1])


def _signed(v, dec=1):
    s = num(v, dec)
    return ('+' + s) if v is not None and v > 0 and s != DASH else s


def _evac_table(caption, evac, havens):
    hz = {h.get('zone_id'): h.get('haven_id') for h in havens}
    rows = []
    for z in evac:
        a, r = _f(z.get('area_ha')), _f(z.get('risk_ha'))
        rows.append([z.get('zone_id'), z.get('settl_type'), z.get('name'), num(z.get('n_settl'), 0), num(a),
                     num(r), num(r / a * 100, 0) if a and r is not None else DASH,
                     hz.get(z.get('zone_id'), 'none found' if havens else DASH)])
    tot_a = sum(_f(z.get('area_ha')) or 0 for z in evac)
    tot_r = sum(_f(z.get('risk_ha')) or 0 for z in evac)
    rows.append(['Total', '', '', num(sum(_f(z.get('n_settl')) or 0 for z in evac), 0), num(tot_a), num(tot_r),
                 num(tot_r / tot_a * 100, 0) if tot_a else DASH, ''])
    note = ('How to read: each row is a group of settlements that are evacuated together. The whole '
            'zone is evacuated, even if only part of it ("Area at risk") is in or near a flood zone.')
    return Table(caption('Evacuation zones'),
                 ['Zone', 'Settlement type', 'Settlements', 'Number of settlements', 'Zone area (ha)',
                  'Area at risk (ha)', 'At risk (%)', 'Safe haven'], rows, note,
                 align=['l', 'l', 'l', 'r', 'r', 'r', 'r', 'l'], bold_rows=[len(rows) - 1])


def _havens_table(caption, havens):
    rows = []
    for h in havens:
        lo, hi = _f(h.get('elev_min')), _f(h.get('elev_max'))
        ground = DASH if lo is None else ('%s%s%s' % (num(lo), DASH, num(hi)) if hi is not None and hi != lo
                                          else num(lo))
        rows.append([h.get('haven_id'), h.get('zone_id'), num(h.get('zone_ha')), num(h.get('haven_ha')),
                     num(h.get('ratio'), 2), num(h.get('dist_flood'), 0), ground,
                     _height_text(h.get('height_chk')), h.get('facility')])
    note = ('How to read: each zone has one safe haven of about the same size (column "Size compared '
            'with zone": 1.00 = the same area). "Distance to flood" is the shortest distance from the '
            'haven to ground flooded in the design event.')
    return Table(caption('Safe havens'),
                 ['Safe haven', 'For zone', 'Zone area (ha)', 'Safe haven area (ha)', 'Size compared with zone',
                  'Distance to flood (m)', 'Ground level (m)', 'Above flood level?', 'Nearby facility'],
                 rows, note, align=['l', 'l', 'r', 'r', 'r', 'r', 'r', 'l', 'l'])


def _height_text(v):
    v = (v or '').strip().lower()
    return {'yes': 'yes, checked', 'partial': 'partly checked', 'no dem': 'not checked (no DEM)',
            'no dem data': 'not checked (no DEM data)'}.get(v, v or DASH)


def _routes_summary_table(caption, routes, evac):
    types = {z.get('zone_id'): z.get('settl_type') for z in evac or []}
    by = {}
    for r in routes:
        by.setdefault(r.get('zone_id'), []).append(r)
    rows = []
    for zid in sorted(by, key=natural_key):
        rr = by[zid]
        L = [(_f(r.get('length_m')) or 0) / 1000 for r in rr]
        rows.append([zid, types.get(zid) or rr[0].get('settl_type'), rr[0].get('haven_id'), str(len(rr)),
                     num(min(L), 2), num(max(L), 2),
                     num(max(_f(r.get('walk_min')) or 0 for r in rr), 0),
                     num(sum(_f(r.get('wet_m')) or 0 for r in rr), 0)])
    note = ('How to read: one row per evacuation zone. Large zones are split into sectors, each with '
            'its own route to the same safe haven.')
    return Table(caption('Evacuation routes by zone (summary)'),
                 ['Zone', 'Settlement type', 'Safe haven', 'Number of routes', 'Shortest (km)', 'Longest (km)',
                  'Longest walking time (min)', 'Through flood water (m)'], rows, note,
                 align=['l', 'l', 'l', 'r', 'r', 'r', 'r', 'r'])


def _routes_table(caption, routes):
    rows = []
    for r in routes:
        roads = None
        if _f(r.get('main_pct')) is not None or _f(r.get('minor_pct')) is not None:
            roads = (_f(r.get('main_pct')) or 0) + (_f(r.get('minor_pct')) or 0)
        frm = r.get('zone_id') or ''
        if r.get('sector') not in (None, ''):
            frm = '%s, sector %s' % (frm, r.get('sector'))
        rows.append([r.get('route_id'), frm, r.get('haven_id'), num((_f(r.get('length_m')) or 0) / 1000, 2),
                     num(r.get('walk_min'), 0), num(roads, 0), num(r.get('path_pct'), 0), num(r.get('open_pct'), 0),
                     num(r.get('wet_m'), 0)])
    note = ('How to read: each route starts at the road point nearest to the middle of its sector and '
            'ends at the safe haven. The three % columns show how much of the route is on roads and '
            'tracks, on footpaths and across open ground; together they make 100%.')
    return Table(caption('Evacuation routes (detail)'),
                 ['Route', 'From', 'To safe haven', 'Length (km)', 'Walking time (min)', 'On roads (%)',
                  'On paths (%)', 'Open ground (%)', 'Through flood water (m)'], rows, note,
                 align=['l', 'l', 'l', 'r', 'r', 'r', 'r', 'r', 'r'])


def _glossary(zones, evac, havens, routes):
    g = []
    if zones:
        g.append('Design event: the largest flood used for planning (e.g. the 100-year flood). Shallow '
                 'water below the minimum depth is ignored.')
    if evac:
        g.append('Evacuation zone: one or more settlements of the same type, close to each other, that '
                 'are evacuated together. Codes are made of the type initials and a number.')
    if havens:
        g.append('Safe haven: dry, open ground outside the flood zones where people gather and stay '
                 'during a flood. "Above flood level?" says whether the ground was checked against the '
                 'nearby flood water level with a terrain model (DEM).')
    if routes:
        speeds = [(_f(r.get('length_m')) or 0) / (_f(r.get('walk_min')) or 1) * 0.06 for r in routes
                  if (_f(r.get('walk_min')) or 0) > 0 and (_f(r.get('length_m')) or 0) > 500]
        sp = sorted(speeds)[len(speeds) // 2] if speeds else None
        g.append('Evacuation route: the easiest walking route from the sector to the safe haven. Roads are '
                 'preferred, then footpaths, then open ground; deep water and steep slopes are avoided and '
                 'the river is crossed only on roads.')
        g.append('Walking time: at %s, the pace of people walking with children and belongings.' % (
            ('about %s km/h' % num(sp, 1)) if sp else 'a slow walking pace'))
        g.append('Through flood water: length of the route where the design flood is deep enough to be '
                 'unsafe (0.3 m or more by default). It is usually unavoidable for settlements inside the '
                 'floodplain, so these places must be evacuated early, before the water rises.')
    return g


# --------------------------------------------------------------------------- HTML
_CSS = """
body{font-family:Calibri,Arial,sans-serif;font-size:10.5pt;color:#222;max-width:1100px;margin:24px auto;padding:0 16px}
h1{font-size:18pt;margin:0 0 2px;color:#1f3864}
.sub{color:#666;margin:0 0 18px}
h2{font-size:13pt;color:#1f3864;border-bottom:1px solid #c9d3e6;padding-bottom:3px;margin-top:26px}
table{border-collapse:collapse;width:100%;margin:4px 0 4px;font-size:9.5pt}
th,td{border:1px solid #a6a6a6;padding:3px 6px;vertical-align:top}
th{background:#d9e2f3;text-align:left}
td.r,th.r{text-align:right}
tr.b td{font-weight:bold;background:#f2f2f2}
.cap{font-weight:bold;margin:14px 0 2px}
.note{font-style:italic;color:#555;font-size:9pt;margin:2px 0 14px}
"""


def write_html(rep, path):
    out = ['<!DOCTYPE html><html><head><meta charset="utf-8"><title>%s</title><style>%s</style></head><body>'
           % (html.escape(rep.title), _CSS),
           '<h1>%s</h1><p class="sub">%s</p>' % (html.escape(rep.title), html.escape(rep.subtitle))]
    for kind, c in rep.blocks:
        if kind == 'h':
            out.append('<h2>%s</h2>' % html.escape(c))
        elif kind == 'p':
            out.append('<p>%s</p>' % html.escape(c))
        elif kind == 'ul':
            out.append('<ul>%s</ul>' % ''.join('<li>%s</li>' % html.escape(x) for x in c))
        elif kind == 'table':
            out.append('<p class="cap">%s</p><table><tr>' % html.escape(c.caption))
            out.append(''.join('<th class="%s">%s</th>' % (a, html.escape(h)) for h, a in zip(c.columns, c.align)))
            out.append('</tr>')
            for i, row in enumerate(c.rows):
                out.append('<tr%s>' % (' class="b"' if i in c.bold_rows else ''))
                for j, (v, a) in enumerate(zip(row, c.align)):
                    style = ' style="background:#%s"' % _light(c.shade[i]) if j == 0 and c.shade[i] else ''
                    out.append('<td class="%s"%s>%s</td>' % (a, style, html.escape(v)))
                out.append('</tr>')
            out.append('</table><p class="note">%s</p>' % html.escape(c.note))
    out.append('</body></html>')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out))


# --------------------------------------------------------------------------- Word (.docx)
_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_TEXT_WIDTH = 9638          # A4 portrait, 2 cm margins, in twips
_TEXT_WIDTH_LAND = 14570    # A4 landscape, 2 cm margins

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '</Types>')
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    'officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
    'core-properties" Target="docProps/core.xml"/>'
    '</Relationships>')
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/></Relationships>')


def _style(sid, name, size, bold=False, italic=False, colour=None, before=0, after=80, keep=False, based='Normal'):
    rpr = '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size)
    if bold:
        rpr = '<w:b/>' + rpr
    if italic:
        rpr = '<w:i/>' + rpr
    if colour:
        rpr = '<w:color w:val="%s"/>' % colour + rpr
    ppr = '<w:spacing w:before="%d" w:after="%d"/>' % (before, after)
    if keep:
        ppr = '<w:keepNext/>' + ppr
    return ('<w:style w:type="paragraph" w:customStyle="1" w:styleId="%s"><w:name w:val="%s"/>'
            '<w:basedOn w:val="%s"/><w:qFormat/><w:pPr>%s</w:pPr><w:rPr>%s</w:rPr></w:style>'
            % (sid, name, based, ppr, rpr))


def _styles():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="%s">'
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
        '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        '</w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        '%s%s%s%s%s%s%s</w:styles>' % (
            _W,
            _style('Title', 'Title', 36, bold=True, colour='1F3864', after=40, based='Normal'),
            _style('Subtitle', 'Subtitle', 20, colour='666666', after=240),
            _style('Heading1', 'heading 1', 26, bold=True, colour='1F3864', before=300, after=100, keep=True),
            _style('Caption', 'caption', 20, bold=True, before=200, after=60, keep=True),
            _style('TableText', 'Table text', 17, after=0),
            _style('TableNote', 'Table note', 17, italic=True, colour='555555', before=40, after=200),
            _style('ListBullet', 'List Bullet', 21, after=60))).replace(
        'w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr>',
        'w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr>'
        '<w:ind w:left="360" w:hanging="240"/>')


def _run(text, bold=False):
    rpr = '<w:rPr><w:b/></w:rPr>' if bold else ''
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, escape(text))


def _p(text, style=None, bold=False, align=None):
    ppr = ''
    if style or align:
        ppr = '<w:pPr>%s%s</w:pPr>' % ('<w:pStyle w:val="%s"/>' % style if style else '',
                                       '<w:jc w:val="%s"/>' % align if align else '')
    return '<w:p>%s%s</w:p>' % (ppr, _run(text, bold))


def _docx_table(t, width):
    cols = t.widths(width)
    border = ''.join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/>' % s
                     for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    x = ['<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblBorders>%s</w:tblBorders>'
         '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="70" w:type="dxa"/>'
         '<w:right w:w="70" w:type="dxa"/></w:tblCellMar><w:tblLook w:val="04A0" w:firstRow="1" '
         'w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr><w:tblGrid>'
         % (width, border)]
    x.append(''.join('<w:gridCol w:w="%d"/>' % c for c in cols) + '</w:tblGrid>')

    def cell(text, wd, fill=None, bold=False, align='l'):
        shd = '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % fill if fill else ''
        return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>'
                % (wd, shd, _p(text, 'TableText', bold, 'right' if align == 'r' else None)))

    x.append('<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>')
    x.append(''.join(cell(h, cols[j], 'D9E2F3', True, t.align[j]) for j, h in enumerate(t.columns)))
    x.append('</w:tr>')
    for i, row in enumerate(t.rows):
        b = i in t.bold_rows
        x.append('<w:tr><w:trPr><w:cantSplit/></w:trPr>')
        for j, v in enumerate(row):
            fill = _light(t.shade[i]) if j == 0 and t.shade[i] else ('F2F2F2' if b else None)
            x.append(cell(v, cols[j], fill, b, t.align[j]))
        x.append('</w:tr>')
    x.append('</w:tbl>')
    return ''.join(x)


def write_docx(rep, path, landscape=False):
    width = _TEXT_WIDTH_LAND if landscape else _TEXT_WIDTH
    body = [_p(rep.title, 'Title'), _p(rep.subtitle, 'Subtitle')]
    for kind, c in rep.blocks:
        if kind == 'h':
            body.append(_p(c, 'Heading1'))
        elif kind == 'p':
            body.append(_p(c))
        elif kind == 'ul':
            body.extend(_p('•\t' + item, 'ListBullet') for item in c)
        elif kind == 'table':
            body.append(_p(c.caption, 'Caption'))
            body.append(_docx_table(c, width))
            body.append(_p(c.note, 'TableNote'))
    pg = ('<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>' if landscape
          else '<w:pgSz w:w="11906" w:h="16838"/>')
    sect = ('<w:sectPr>%s<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
            'w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>' % pg)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s"><w:body>%s%s'
           '</w:body></w:document>' % (_W, ''.join(body), sect))
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>%s</dc:title>'
            '<dc:creator>Flood Zoning &amp; Evacuation Planner</dc:creator>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created></cp:coreProperties>'
            % (escape(rep.title), now))
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', _CONTENT_TYPES)
        z.writestr('_rels/.rels', _RELS)
        z.writestr('word/_rels/document.xml.rels', _DOC_RELS)
        z.writestr('word/document.xml', doc)
        z.writestr('word/styles.xml', _styles())
        z.writestr('docProps/core.xml', core)
