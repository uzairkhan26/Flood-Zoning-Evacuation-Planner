# -*- coding: utf-8 -*-
"""Integration tests: run the Processing algorithms inside QGIS on synthetic data.

Synthetic valley: a straight river runs west-east through the middle of a 2 km x 1 km
area. Flood depth falls linearly with distance from the river and is larger for rarer
events; the 25 m wide channel is 1 m deeper than the floodplain.
"""
import os

import numpy as np
import pytest

osgeo = pytest.importorskip('osgeo')
from osgeo import gdal, ogr, osr  # noqa: E402

EPSG = 32636
X0, Y0, RES, NX, NY = 500000.0, 1100000.0, 5.0, 400, 200   # 2 km x 1 km
RIVER_Y = Y0 - NY * RES / 2                                  # river along the middle
PEAKS = {2: 2.0, 10: 2.4, 50: 2.8, 100: 3.0}                  # depth at the bank [m]
SLOPE = 0.006                                                # depth loss per metre from the river


def _write_depth(path, peak, epsg=EPSG):
    ys = Y0 - (np.arange(NY) + 0.5) * RES
    dist = np.abs(ys - RIVER_Y)[:, None] * np.ones((1, NX))
    depth = np.maximum(0.0, peak - SLOPE * dist)
    depth[dist <= 12.5] += 1.0                              # river channel
    depth = depth.astype(np.float32)
    depth[depth <= 0] = -9999
    ds = gdal.GetDriverByName('GTiff').Create(path, NX, NY, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((X0, RES, 0, Y0, 0, -RES))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds.SetProjection(srs.ExportToWkt())
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(-9999)
    b.WriteArray(depth)
    ds = None
    if epsg != EPSG:   # reproject to another CRS (e.g. geographic)
        out = path.replace('.tif', '_%d.tif' % epsg)
        gdal.Warp(out, path, dstSRS='EPSG:%d' % epsg, srcNodata=-9999, dstNodata=-9999)
        return out
    return path


def _write_vector(path, geoms_wkt, geom_type):
    drv = ogr.GetDriverByName('GPKG')
    ds = drv.CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    lyr = ds.CreateLayer('layer', srs, geom_type)
    for wkt in geoms_wkt:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        lyr.CreateFeature(f)
    ds = None
    return path


@pytest.fixture(scope='module')
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp('synthetic')
    out = {rp: _write_depth(str(d / ('depth_rp%d.tif' % rp)), peak) for rp, peak in PEAKS.items()}
    out['design_geographic'] = _write_depth(str(d / 'depth_geo.tif'), PEAKS[100], epsg=4326)
    out['river'] = _write_vector(str(d / 'river.gpkg'), [
        'LINESTRING(%f %f, %f %f)' % (X0, RIVER_Y, X0 + NX * RES, RIVER_Y)], ogr.wkbLineString)
    # settlement 150-300 m north of the river (flooded in the frequent event, shallow)
    out['settlements'] = _write_vector(str(d / 'settlements.gpkg'), [
        'POLYGON((%f %f, %f %f, %f %f, %f %f, %f %f))' % (
            X0 + 500, RIVER_Y + 150, X0 + 800, RIVER_Y + 150, X0 + 800, RIVER_Y + 300,
            X0 + 500, RIVER_Y + 300, X0 + 500, RIVER_Y + 150)], ogr.wkbPolygon)
    out['dir'] = str(d)
    return out


def _area(layer, code=None):
    total = 0.0
    for f in layer.getFeatures():
        if code is None or f['zone_code'] == code:
            total += f.geometry().area()
    return total / 1e4


def _layer(result, key):
    from qgis.core import QgsProcessingUtils, QgsVectorLayer
    v = result[key]
    if isinstance(v, QgsVectorLayer):
        return v
    lyr = QgsVectorLayer(v, 'out', 'ogr')
    if not lyr.isValid():
        lyr = QgsProcessingUtils.mapLayerFromString(v, None)
    return lyr


def test_provider_registered(qgis_processing):
    from qgis.core import QgsApplication
    reg = QgsApplication.processingRegistry()
    assert reg.algorithmById('floodzoning:planningzones') is not None
    assert reg.algorithmById('floodzoning:rivercorridor') is not None


def test_river_corridor_from_raster(qgis_processing, data, tmp_path):
    res = qgis_processing.run('floodzoning:rivercorridor', {
        'METHOD': 1, 'RIVER_LINES': data['river'], 'DEPTH': data[2],
        'CHANNEL_DEPTH': 2.6, 'BUFFER_WIDTH': 30,
        'OUTPUT_CHANNEL': str(tmp_path / 'channel.gpkg'), 'OUTPUT_BUFFER': str(tmp_path / 'buffer.gpkg')})
    ch = _layer(res, 'OUTPUT_CHANNEL')
    bf = _layer(res, 'OUTPUT_BUFFER')
    ch_ha = sum(f.geometry().area() for f in ch.getFeatures()) / 1e4
    bf_ha = sum(f.geometry().area() for f in bf.getFeatures()) / 1e4
    # channel: six 5 m rows (~30 m) x 2000 m = 6 ha ; buffer: 2 x 30 m x 2000 m = 12 ha
    assert 4.0 < ch_ha < 7.5, ch_ha
    assert 10.0 < bf_ha < 14.5, bf_ha


def test_river_corridor_fixed_width(qgis_processing, data, tmp_path):
    res = qgis_processing.run('floodzoning:rivercorridor', {
        'METHOD': 2, 'RIVER_LINES': data['river'], 'FIXED_WIDTH': 20, 'BUFFER_WIDTH': 10,
        'OUTPUT_CHANNEL': str(tmp_path / 'c.gpkg'), 'OUTPUT_BUFFER': str(tmp_path / 'b.gpkg')})
    ch_ha = sum(f.geometry().area() for f in _layer(res, 'OUTPUT_CHANNEL').getFeatures()) / 1e4
    assert 3.8 < ch_ha < 4.3, ch_ha


def _zones(qgis_processing, data, tmp_path, **kw):
    params = {
        'DESIGN_DEPTH': data[100], 'MEDIUM_DEPTH': data[50], 'FREQUENT_DEPTH': data[10],
        'VERY_FREQUENT_DEPTH': data[2], 'MIN_DEPTH': 0.3, 'MID_DEPTH': 0.5, 'DEEP_DEPTH': 1.0,
        'SCENARIO': 'test', 'OUTPUT': str(tmp_path / ('zones_%d.gpkg' % len(os.listdir(str(tmp_path)))))}
    params.update(kw)
    return qgis_processing.run('floodzoning:planningzones', params)


def test_planning_zones_all_events(qgis_processing, data, tmp_path):
    res = _zones(qgis_processing, data, tmp_path, OUTPUT_RASTER=str(tmp_path / 'zones.tif'))
    lyr = _layer(res, 'OUTPUT')
    codes = sorted(f['zone_code'] for f in lyr.getFeatures())
    assert codes == [1, 2, 3]
    red, orange, yellow = (_area(lyr, c) for c in (1, 2, 3))
    # analytic widths (each side of the river): RP10 >= 0.3 m reaches (2.4-0.3)/0.006 = 350 m ...
    # -> red ~ 2 x 350 m x 2 km = 140 ha; RP50 >= 0.3 m reaches 417 m -> orange ~ 2 x 67 m x 2 km = 27 ha;
    # RP100 >= 0.3 m reaches 450 m -> yellow ~ 2 x 33 m x 2 km = 13 ha (grid filling changes edges slightly)
    assert 120 < red < 160, red
    assert 15 < orange < 40, orange
    assert 5 < yellow < 25, yellow
    assert os.path.exists(str(tmp_path / 'zones.tif'))
    for f in lyr.getFeatures():
        assert f['scenario'] == 'test'
        assert abs(f['area_ha'] - f.geometry().area() / 1e4) < 0.05


def test_design_only_and_protection(qgis_processing, data, tmp_path):
    rc = qgis_processing.run('floodzoning:rivercorridor', {
        'METHOD': 2, 'RIVER_LINES': data['river'], 'FIXED_WIDTH': 25, 'BUFFER_WIDTH': 30,
        'OUTPUT_CHANNEL': str(tmp_path / 'c.gpkg'), 'OUTPUT_BUFFER': str(tmp_path / 'b.gpkg')})
    res = _zones(qgis_processing, data, tmp_path, MEDIUM_DEPTH=None, FREQUENT_DEPTH=None,
                 VERY_FREQUENT_DEPTH=None, PROTECTION=rc['OUTPUT_BUFFER'], RIVER=rc['OUTPUT_CHANNEL'])
    lyr = _layer(res, 'OUTPUT')
    codes = sorted(f['zone_code'] for f in lyr.getFeatures())
    assert 4 in codes and 1 in codes
    assert 10.0 < _area(lyr, 4) < 14.5


def test_settlement_rule_changes_zones(qgis_processing, data, tmp_path):
    # With "deep water" = 2 m, cells 167-350 m from the river are high hazard only because of
    # the 10-year event; the settlement (150-300 m) overlaps that band by ~133 m x 300 m = 4 ha.
    kw = dict(SETTLEMENTS=data['settlements'], VERY_FREQUENT_DEPTH=None, DEEP_DEPTH=2.0)
    on = _layer(_zones(qgis_processing, data, tmp_path, SETTLEMENT_RULE=True, **kw), 'OUTPUT')
    off = _layer(_zones(qgis_processing, data, tmp_path, SETTLEMENT_RULE=False, **kw), 'OUTPUT')
    assert _area(on, 2) > _area(off, 2) + 2.0
    assert _area(on, 1) < _area(off, 1) - 2.0


def test_geographic_input_is_processed_in_utm(qgis_processing, data, tmp_path):
    res = _zones(qgis_processing, data, tmp_path, DESIGN_DEPTH=data['design_geographic'],
                 MEDIUM_DEPTH=None, FREQUENT_DEPTH=None, VERY_FREQUENT_DEPTH=None)
    lyr = _layer(res, 'OUTPUT')
    assert lyr.crs().authid() == 'EPSG:%d' % EPSG
    assert _area(lyr) > 100
