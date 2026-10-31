# -*- coding: utf-8 -*-
"""Report tables (pure Python, no QGIS needed)."""
import xml.dom.minidom
import zipfile

from flood_zoning_evacuation_planner.core import report as R


def _zones(label, a):
    return dict(label=label, items=[
        dict(code=1, zone='No development zone', colour='Red', hex='#e31a1c', criteria='c1', rule='r1',
             area_ha=a[0], settled_ha=2.0),
        dict(code=2, zone='Restricted development zone', colour='Orange', hex='#ff7f00', criteria='c2',
             rule='r2', area_ha=a[1], settled_ha=1.0),
        dict(code=2, zone='Restricted development zone', colour='Orange', hex='#ff7f00', criteria='c2',
             rule='r2', area_ha=a[2], settled_ha=0.5),         # second piece of the same zone
        dict(code=4, zone='Protection zone', colour='Green', hex='#33a02c', criteria='buffer', rule='r4',
             area_ha=5.0, settled_ha=None)])


EVAC = [dict(zone_id='V-10', settl_type='Village', name='B', n_settl=2, area_ha=20.0, risk_ha=5.0),
        dict(zone_id='V-2', settl_type='Village', name='A', n_settl=1, area_ha=10.0, risk_ha=10.0),
        dict(zone_id='C-1', settl_type='Camp', name='Camp & co <1>', n_settl=1, area_ha=30.0, risk_ha=None)]
HAVENS = [dict(haven_id='SH-V-2', zone_id='V-2', zone_ha=10.0, haven_ha=10.2, ratio=1.02, dist_flood=180,
               elev_min=401.2, elev_max=403.0, height_chk='yes', facility=None),
          dict(haven_id='SH-C-1', zone_id='C-1', zone_ha=30.0, haven_ha=29.0, ratio=0.97, dist_flood=150,
               elev_min=None, elev_max=None, height_chk='no DEM', facility='School (40 m)')]
ROUTES = [dict(route_id='V-2-R1', zone_id='V-2', sector=1, settl_type='Village', haven_id='SH-V-2',
               length_m=1500, walk_min=30, main_pct=60, minor_pct=10, path_pct=0, open_pct=30, wet_m=0),
          dict(route_id='C-1-R1', zone_id='C-1', sector=1, settl_type='Camp', haven_id='SH-C-1',
               length_m=900, walk_min=18, main_pct=0, minor_pct=0, path_pct=50, open_pct=50, wet_m=20),
          dict(route_id='C-1-R2', zone_id='C-1', sector=2, settl_type='Camp', haven_id='SH-C-1',
               length_m=2100, walk_min=42, main_pct=0, minor_pct=0, path_pct=0, open_pct=100, wet_m=0)]


def test_helpers():
    assert R.num(1234.56) == '1,234.6'
    assert R.num(None) == R.DASH and R.num(float('nan')) == R.DASH
    assert sorted(['RS-10', 'RS-2', 'BU-1'], key=R.natural_key) == ['BU-1', 'RS-2', 'RS-10']


def test_full_report(tmp_path):
    rep = R.build_report('Test', _zones('Before', (10, 6, 4)), _zones('After', (8, 5, 3)), EVAC, HAVENS, ROUTES)
    assert rep.n_tables() == 6
    tables = [c for k, c in rep.blocks if k == 'table']
    zt = tables[0]
    assert zt.rows[1][3] == '10.0'                    # the two orange pieces are added up
    assert zt.rows[-1][3] == '20.0'                   # total red + orange + yellow, protection excluded
    assert zt.rows[0][4] == '50'                      # share of red
    cmp_ = tables[1]
    assert cmp_.rows[-1][1:4] == ['20.0', '16.0', '-4.0']
    ev = tables[2]
    assert [r[0] for r in ev.rows[:-1]] == ['C-1', 'V-2', 'V-10']    # natural order
    assert ev.rows[0][-1] == 'SH-C-1' and ev.rows[2][-1] == 'none found'
    summary = [c for k, c in rep.blocks if k == 'ul'][0]
    assert any('No safe haven was found for: V-10' in s for s in summary)
    assert any('2 safe havens' in s for s in summary)
    rs = tables[4]
    assert rs.rows[0][:4] == ['C-1', 'Camp', 'SH-C-1', '2'] and rs.rows[0][4:6] == ['0.90', '2.10']
    detail = tables[5]
    assert detail.rows[2][5] == '70'                  # main + minor road share

    for name in ('r.docx', 'r.html'):
        rep.write(str(tmp_path / name))
    with zipfile.ZipFile(str(tmp_path / 'r.docx')) as z:
        assert z.testzip() is None
        for part in ('word/document.xml', 'word/styles.xml', '[Content_Types].xml', '_rels/.rels'):
            xml.dom.minidom.parseString(z.read(part))       # well-formed XML
        doc = z.read('word/document.xml').decode('utf-8')
    assert 'Camp &amp; co &lt;1&gt;' in doc and doc.count('<w:tbl>') == 6
    html = (tmp_path / 'r.html').read_text(encoding='utf-8')
    assert 'Camp &amp; co &lt;1&gt;' in html and html.count('<table>') == 6


def test_partial_report(tmp_path):
    rep = R.build_report('', routes=ROUTES)
    assert rep.n_tables() == 2
    rep.write(str(tmp_path / 'r.docx'))
    widths = [c for k, c in rep.blocks if k == 'table'][1].widths(9638)
    assert sum(widths) == 9638 and min(widths) > 300
