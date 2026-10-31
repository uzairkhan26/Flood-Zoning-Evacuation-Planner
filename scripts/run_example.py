# -*- coding: utf-8 -*-
"""Run the worked example of the user guide in QGIS and draw its figures.

    python3 scripts/run_example.py example_data docs_out

Runs the five tools on the example data (see make_example_data.py), writes the outputs,
the report (Word + HTML), map figures (PNG) and a results.json with the key numbers.
Used by the 'Example and figures' GitHub workflow; needs QGIS 3.34+ (headless is fine).
"""
import json
import math
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qgis.core import (  # noqa: E402
    QgsApplication, QgsCategorizedSymbolRenderer, QgsColorRampShader, QgsFillSymbol,
    QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap, QgsLayoutItemScaleBar,
    QgsLayoutPoint, QgsLayoutSize, QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsPrintLayout,
    QgsProject, QgsRasterLayer, QgsRasterShader, QgsRendererCategory, QgsSingleBandPseudoColorRenderer,
    QgsSingleSymbolRenderer, QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsRectangle,
    QgsTextBufferSettings, QgsMapLayerLegendUtils,
)
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()
for _p in ('/usr/share/qgis/python/plugins', '/usr/lib/qgis/python/plugins'):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()
import processing  # noqa: E402

from flood_zoning_evacuation_planner.provider import FloodZoningProvider  # noqa: E402
from flood_zoning_evacuation_planner.core.zoning_rules import ZONES  # noqa: E402

PROVIDER = FloodZoningProvider()
QgsApplication.processingRegistry().addProvider(PROVIDER)


def run(alg, params):
    print('Running', alg, flush=True)
    return processing.run('floodzoning:' + alg, params)


def vlayer(path, name):
    lyr = QgsVectorLayer(path, name, 'ogr')
    assert lyr.isValid(), path
    QgsProject.instance().addMapLayer(lyr, False)
    return lyr


def rlayer(path, name):
    lyr = QgsRasterLayer(path, name)
    assert lyr.isValid(), path
    QgsProject.instance().addMapLayer(lyr, False)
    return lyr


# ----------------------------------------------------------------------------- styles
def fill(color, outline='#404040', width='0.2', style='solid'):
    return QgsFillSymbol.createSimple({'color': color, 'outline_color': outline, 'outline_width': width,
                                       'style': style})


def categorized(lyr, field, cats, kind='fill'):
    out = []
    for value, label, props in cats:
        sym = (QgsFillSymbol.createSimple(props) if kind == 'fill' else
               QgsLineSymbol.createSimple(props) if kind == 'line' else QgsMarkerSymbol.createSimple(props))
        out.append(QgsRendererCategory(value, sym, label))
    lyr.setRenderer(QgsCategorizedSymbolRenderer(field, out))


def single(lyr, sym):
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))


def labels(lyr, field, size=8, color='#000000'):
    s = QgsPalLayerSettings()
    s.fieldName = field
    fmt = QgsTextFormat()
    fmt.setFont(QFont('DejaVu Sans'))
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor('white'))
    fmt.setBuffer(buf)
    s.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(s))
    lyr.setLabelsEnabled(True)


def terrain(lyr):
    st = lyr.dataProvider().bandStatistics(1)
    lo, hi = math.floor(st.minimumValue), math.ceil(st.maximumValue)
    sh = QgsRasterShader()
    ramp = QgsColorRampShader(lo, hi)
    ramp.setColorRampType(QgsColorRampShader.Interpolated)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(lo, QColor('#f4f1e6'), '%.0f m' % lo),
        QgsColorRampShader.ColorRampItem(lo + 0.35 * (hi - lo), QColor('#e6dcc0'), ''),
        QgsColorRampShader.ColorRampItem(hi, QColor('#b89d72'), '%.0f m' % hi)])
    sh.setRasterShaderFunction(ramp)
    r = QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), 1, sh)
    r.setClassificationMin(lo)
    r.setClassificationMax(hi)
    lyr.setRenderer(r)


def depth_style(lyr):
    sh = QgsRasterShader()
    ramp = QgsColorRampShader(0, 99)
    ramp.setColorRampType(QgsColorRampShader.Discrete)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(0.3, QColor('#c6e2f7'), 'less than 0.3 m'),
        QgsColorRampShader.ColorRampItem(0.5, QColor('#8cc4ec'), '0.3 - 0.5 m'),
        QgsColorRampShader.ColorRampItem(1.0, QColor('#4a98d6'), '0.5 - 1 m'),
        QgsColorRampShader.ColorRampItem(2.0, QColor('#1f5fa8'), '1 - 2 m'),
        QgsColorRampShader.ColorRampItem(float('inf'), QColor('#0b2f6b'), 'more than 2 m')])
    sh.setRasterShaderFunction(ramp)
    r = QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), 1, sh)
    lyr.setRenderer(r)
    lyr.setOpacity(0.85)


ROAD_CATS = [('secondary', 'Main road', {'line_color': '#b2182b', 'line_width': '0.9'}),
             ('residential', 'Town street', {'line_color': '#6e6e6e', 'line_width': '0.45'}),
             ('track', 'Track', {'line_color': '#8c6d31', 'line_width': '0.5', 'line_style': 'dash'}),
             ('path', 'Footpath', {'line_color': '#5a3e1b', 'line_width': '0.35', 'line_style': 'dot'})]


# ----------------------------------------------------------------------------- figures
def figure(path, title, layers, legend_layers, extent=None, subtitle='', bar=500):
    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(270, 170))
    lab = QgsLayoutItemLabel(layout)
    lab.setText(title)
    f = QgsTextFormat()
    f.setFont(QFont('DejaVu Sans'))
    f.setSize(13)
    lab.setTextFormat(f)
    lab.attemptMove(QgsLayoutPoint(6, 3))
    lab.attemptResize(QgsLayoutSize(248, 9))
    layout.addLayoutItem(lab)
    if subtitle:
        sub = QgsLayoutItemLabel(layout)
        sub.setText(subtitle)
        f2 = QgsTextFormat()
        f2.setFont(QFont('DejaVu Sans'))
        f2.setSize(8)
        f2.setColor(QColor('#555555'))
        sub.setTextFormat(f2)
        sub.attemptMove(QgsLayoutPoint(6, 11))
        sub.attemptResize(QgsLayoutSize(248, 6))
        layout.addLayoutItem(sub)
    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(6, 18))
    m.attemptResize(QgsLayoutSize(182, 146))
    m.setLayers(layers)
    m.setCrs(layers[-1].crs())
    m.setExtent(extent or layers[-1].extent())
    m.setFrameEnabled(True)
    layout.addLayoutItem(m)
    leg = QgsLayoutItemLegend(layout)
    leg.setTitle('')
    leg.setAutoUpdateModel(False)
    root = leg.model().rootGroup()
    root.removeAllChildren()
    for lyr in legend_layers:
        node = root.addLayer(lyr)
        nodes = leg.model().layerLegendNodes(node)
        keep = [i for i, n in enumerate(nodes) if not str(n.data(Qt.DisplayRole) or '').startswith('Band')]
        if len(keep) < len(nodes):
            QgsMapLayerLegendUtils.setLegendNodeOrder(node, keep)
            leg.model().refreshLayerLegend(node)
    leg.setLinkedMap(m)
    leg.attemptMove(QgsLayoutPoint(192, 18))
    leg.setSymbolWidth(6)
    leg.setSymbolHeight(3.5)
    layout.addLayoutItem(leg)
    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle('Single Box')
    sb.setLinkedMap(m)
    sb.applyDefaultSize()
    sb.setNumberOfSegments(2)
    sb.setNumberOfSegmentsLeft(0)
    sb.setUnitsPerSegment(bar)
    sb.setUnitLabel('m')
    sb.attemptMove(QgsLayoutPoint(192, 150))
    layout.addLayoutItem(sb)
    s = QgsLayoutExporter.ImageExportSettings()
    s.dpi = 110
    res = QgsLayoutExporter(layout).exportToImage(path, s)
    print('figure', os.path.basename(path), 'ok' if res == QgsLayoutExporter.Success else res, flush=True)


# ----------------------------------------------------------------------------- main
def main(data, out):
    os.makedirs(out, exist_ok=True)
    o = lambda name: os.path.join(out, name)  # noqa: E731
    p = lambda name: os.path.join(data, name)  # noqa: E731
    results = {}

    rc = run('rivercorridor', {'METHOD': 1, 'RIVER_LINES': p('river.gpkg'), 'DEPTH': p('depth_rp2.tif'),
                               'CHANNEL_DEPTH': 2.0, 'BUFFER_WIDTH': 30,
                               'OUTPUT_CHANNEL': o('river_channel.gpkg'), 'OUTPUT_BUFFER': o('river_buffer.gpkg')})
    common = {'SETTLEMENTS': p('settlements.gpkg'), 'FACILITIES': p('facilities.gpkg'),
              'PROTECTION': rc['OUTPUT_BUFFER'], 'RIVER': rc['OUTPUT_CHANNEL']}
    pz = run('planningzones', dict(common, DESIGN_DEPTH=p('depth_rp100.tif'), MEDIUM_DEPTH=p('depth_rp50.tif'),
                                   FREQUENT_DEPTH=p('depth_rp10.tif'), VERY_FREQUENT_DEPTH=p('depth_rp2.tif'),
                                   SCENARIO='Current situation', OUTPUT=o('planning_zones_current.gpkg')))
    pzb = run('planningzones', dict(common, DESIGN_DEPTH=p('banks_rp100.tif'), MEDIUM_DEPTH=p('banks_rp50.tif'),
                                    FREQUENT_DEPTH=p('banks_rp10.tif'), VERY_FREQUENT_DEPTH=p('banks_rp2.tif'),
                                    SCENARIO='Raised banks', OUTPUT=o('planning_zones_banks.gpkg')))
    ez = run('evacuationzones', {'SETTLEMENTS': p('settlements.gpkg'), 'TYPE_FIELD': 'type', 'NAME_FIELD': 'name',
                                 'RISK_AREAS': pz['OUTPUT'], 'DISTANCE': 100, 'GROUP_DISTANCE': 1000,
                                 'BARRIERS': rc['OUTPUT_CHANNEL'],
                                 'OUTPUT': o('evacuation_zones.gpkg')})
    sh = run('safehavensroutes', {'EVACUATION_ZONES': ez['OUTPUT'], 'DESIGN_DEPTH': p('depth_rp100.tif'),
                                  'PLANNING_ZONES': pz['OUTPUT'], 'SETTLEMENTS': p('settlements.gpkg'),
                                  'ROADS': p('roads.gpkg'), 'ROAD_CLASS_FIELD': 'highway',
                                  'RIVER': rc['OUTPUT_CHANNEL'], 'DEM': p('dem.tif'),
                                  'FACILITIES': p('facilities.gpkg'), 'FACILITY_NAME_FIELD': 'name',
                                  'OUTPUT_HAVENS': o('safe_havens.gpkg'), 'OUTPUT_ROUTES': o('evacuation_routes.gpkg')})
    for ext in ('docx', 'html'):
        run('reporttables', {'TITLE': 'Example valley - flood zoning and evacuation',
                             'PLANNING_ZONES': pz['OUTPUT'], 'COMPARE_ZONES': pzb['OUTPUT'],
                             'SETTLEMENTS': p('settlements.gpkg'), 'EVACUATION_ZONES': ez['OUTPUT'],
                             'SAFE_HAVENS': sh['OUTPUT_HAVENS'], 'ROUTES': sh['OUTPUT_ROUTES'],
                             'OUTPUT': o('example_report.' + ext)})

    # numbers for the guide -----------------------------------------------------------
    def table(path, fields):
        return [{k: (f[k] if not hasattr(f[k], 'isNull') else None) for k in fields}
                for f in QgsVectorLayer(path, 'x', 'ogr').getFeatures()]

    results['planning_zones'] = table(pz['OUTPUT'], ['zone_code', 'zone', 'area_ha', 'criteria'])
    results['planning_zones_banks'] = table(pzb['OUTPUT'], ['zone_code', 'zone', 'area_ha'])
    results['evacuation_zones'] = table(ez['OUTPUT'], ['zone_id', 'settl_type', 'name', 'n_settl', 'area_ha',
                                                       'risk_ha'])
    results['safe_havens'] = table(sh['OUTPUT_HAVENS'], ['haven_id', 'zone_ha', 'haven_ha', 'ratio', 'dist_flood',
                                                         'height_chk', 'facility'])
    results['routes'] = table(sh['OUTPUT_ROUTES'], ['route_id', 'length_m', 'walk_min', 'main_pct', 'minor_pct',
                                                    'path_pct', 'open_pct', 'wet_m'])
    with open(o('results.json'), 'w') as f:
        json.dump(results, f, indent=1, default=str)

    # figures --------------------------------------------------------------------------
    dem = rlayer(p('dem.tif'), 'Ground level')
    terrain(dem)
    d100 = rlayer(p('depth_rp100.tif'), 'Flood depth, 100-year flood')
    depth_style(d100)
    d2 = rlayer(p('depth_rp2.tif'), 'Flood depth, 2-year flood')
    depth_style(d2)
    settl = vlayer(p('settlements.gpkg'), 'Settlements')
    single(settl, fill('120,120,120,90', '#303030', '0.3'))
    settl_lab = vlayer(p('settlements.gpkg'), 'Settlement names')
    single(settl_lab, fill('0,0,0,0', '0,0,0,0', '0'))
    labels(settl_lab, 'name', 7)
    roads = vlayer(p('roads.gpkg'), 'Roads and paths')
    categorized(roads, 'highway', ROAD_CATS, 'line')
    fac = vlayer(p('facilities.gpkg'), 'Public facilities')
    single(fac, QgsMarkerSymbol.createSimple({'name': 'square', 'color': '#ffffff', 'outline_color': '#000000',
                                              'size': '2.2'}))
    river = vlayer(p('river.gpkg'), 'River centre line')
    single(river, QgsLineSymbol.createSimple({'line_color': '#1f78b4', 'line_width': '0.5'}))
    full = QgsRectangle(dem.extent())

    figure(o('fig1_inputs.png'), 'Input data: 100-year flood depth, settlements, roads and facilities',
           [fac, settl_lab, roads, settl, d100, dem], [d100, settl, roads, fac, dem], full,
           'Example valley (fictional). Flood depths from a hydraulic model; roads with an OpenStreetMap "highway" field.')

    ch = vlayer(rc['OUTPUT_CHANNEL'], 'River channel')
    single(ch, fill('#1f78b4', '#08306b', '0.2'))
    bf = vlayer(rc['OUTPUT_BUFFER'], 'River protection buffer (30 m)')
    single(bf, fill('51,160,44,170', '#1b5e20', '0.2'))
    c = ch.extent().center()
    zoom = QgsRectangle(c.x() - 900, c.y() - 580, c.x() + 900, c.y() + 580)
    figure(o('fig2_river_corridor.png'), 'Step 1 - River corridor: channel and 30 m protection buffer',
           [roads, ch, bf, d2, dem], [d2, ch, bf], zoom,
           'Channel = continuous strip at least 2 m deep in the 2-year flood, along the river line (zoomed in).',
           bar=200)

    def zones_layer(path, name):
        lyr = vlayer(path, name)
        short = {1: 'No development', 2: 'Restricted development', 3: 'Conditional development',
                 4: 'River protection'}
        categorized(lyr, 'zone_code', [(c, short[c],
                                        {'color': ZONES[c]['hex'], 'outline_color': '#404040',
                                         'outline_width': '0.15'}) for c in (1, 2, 3, 4)])
        lyr.setOpacity(0.8)
        return lyr

    zc = zones_layer(pz['OUTPUT'], 'Planning zones')
    figure(o('fig3_planning_zones.png'), 'Step 2 - Spatial planning zones (current situation)',
           [fac, settl_lab, roads, settl, zc, dem], [zc, settl, roads, fac], full,
           'From the 2-, 10-, 50- and 100-year floods, with the existing-settlement rule and grid filling.')
    zb = zones_layer(pzb['OUTPUT'], 'Planning zones')
    figure(o('fig3b_planning_zones_banks.png'), 'Step 2 - Spatial planning zones (with raised banks)',
           [fac, settl_lab, roads, settl, zb, dem], [zb, settl, roads, fac], full,
           'The same tool run on the flood depths of the second scenario, for comparison.')

    ev = vlayer(ez['OUTPUT'], 'Evacuation zones')
    types = sorted({f['settl_type'] for f in ev.getFeatures()})
    pal = ['#7b2d8e', '#0046c8', '#d95f02', '#1b9e77']
    rgba = lambda h, a: '%d,%d,%d,%d' % (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16), a)  # noqa: E731
    categorized(ev, 'settl_type', [(t, t, {'color': rgba(pal[i % 4], 85), 'outline_color': pal[i % 4],
                                           'outline_width': '0.6'}) for i, t in enumerate(types)])
    labels(ev, 'zone_id', 8)
    zc_light = zones_layer(pz['OUTPUT'], 'Planning zones')
    zc_light.setOpacity(0.35)
    figure(o('fig4_evacuation_zones.png'), 'Step 3 - Evacuation zones: settlements at risk, grouped by type',
           [ev, roads, settl, zc_light, dem], [ev, settl, zc_light], full,
           'Whole settlements within 100 m of the red, orange or yellow zones; same-type settlements within 1 km grouped.')

    hv = vlayer(sh['OUTPUT_HAVENS'], 'Safe havens')
    single(hv, fill('#00a651', '#004d26', '0.5'))
    labels(hv, 'haven_id', 8, '#004d26')
    rt = vlayer(sh['OUTPUT_ROUTES'], 'Evacuation routes')
    single(rt, QgsLineSymbol.createSimple({'line_color': '#e00000', 'line_width': '0.9'}))
    figure(o('fig5_havens_routes.png'), 'Step 4 - Safe havens and evacuation routes',
           [rt, hv, ev, fac, roads, zc_light, dem], [hv, rt, ev, roads, fac, zc_light], full,
           'One safe haven per zone, about the size of the zone; one route per sector of about 150 ha.')
    print('done', flush=True)


if __name__ == '__main__':
    try:
        main(sys.argv[1], sys.argv[2])
        code = 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    os._exit(code)
