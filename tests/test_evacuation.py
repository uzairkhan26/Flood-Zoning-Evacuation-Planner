# -*- coding: utf-8 -*-
"""End-to-end test in QGIS: river corridor -> planning zones -> evacuation zones -> safe havens & routes.

Synthetic 4 km x 4 km valley (10 m cells), river running west-east through the middle,
design flood up to ~675 m from the river (>= 0.3 m), ground rising away from the river.
A village sits in the floodplain north of the river, a camp south of it. A north-south
main road crosses the river on a bridge; an east-west track runs 1.1 km north.
"""
import pytest

osgeo = pytest.importorskip('osgeo')
import numpy as np  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402

EPSG = 32636
X0, Y0, RES, N = 600000.0, 1000000.0, 10.0, 400
RIV = Y0 - N * RES / 2          # river y


def _srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(EPSG)
    return s


def _raster(path, arr, nodata=-9999):
    ds = gdal.GetDriverByName('GTiff').Create(path, N, N, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((X0, RES, 0, Y0, 0, -RES))
    ds.SetProjection(_srs().ExportToWkt())
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(nodata)
    b.WriteArray(arr.astype(np.float32))
    ds = None
    return path


def _vector(path, geom_type, rows, fields=()):
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(path)
    lyr = ds.CreateLayer('layer', _srs(), geom_type)
    for name in fields:
        lyr.CreateField(ogr.FieldDefn(name, ogr.OFTString))
    for wkt, attrs in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        for k, v in attrs.items():
            f.SetField(k, v)
        lyr.CreateFeature(f)
    ds = None
    return path


def _box(x0, y0, x1, y1):
    return 'POLYGON((%f %f,%f %f,%f %f,%f %f,%f %f))' % (x0, y0, x1, y0, x1, y1, x0, y1, x0, y0)


@pytest.fixture(scope='module')
def valley(tmp_path_factory):
    d = tmp_path_factory.mktemp('valley')
    ys = Y0 - (np.arange(N) + 0.5) * RES
    dist = np.abs(ys - RIV)[:, None] * np.ones((1, N))
    out = {}
    for rp, peak in ((2, 2.2), (10, 2.6), (50, 2.85), (100, 3.0)):
        dep = np.maximum(0.0, peak - 0.004 * dist)
        dep[dist <= 15] += 1.0
        dep[dep <= 0] = -9999
        out[rp] = _raster(str(d / ('depth%d.tif' % rp)), dep)
    out['dem'] = _raster(str(d / 'dem.tif'), 400.0 + 0.01 * dist)
    out['river'] = _vector(str(d / 'river.gpkg'), ogr.wkbLineString,
                           [('LINESTRING(%f %f,%f %f)' % (X0, RIV, X0 + N * RES, RIV), {})])
    out['settlements'] = _vector(str(d / 'settl.gpkg'), ogr.wkbPolygon, [
        (_box(X0 + 1300, RIV + 250, X0 + 1500, RIV + 400), {'kind': 'Village', 'name': 'North village'}),
        (_box(X0 + 2200, RIV - 650, X0 + 2500, RIV - 350), {'kind': 'Camp', 'name': 'South camp'}),
        (_box(X0 + 300, RIV + 1600, X0 + 400, RIV + 1700), {'kind': 'Village', 'name': 'Dry village'}),
    ], fields=('kind', 'name'))
    out['roads'] = _vector(str(d / 'roads.gpkg'), ogr.wkbLineString, [
        ('LINESTRING(%f %f,%f %f)' % (X0 + 1500, Y0, X0 + 1500, Y0 - N * RES), {'highway': 'secondary'}),
        ('LINESTRING(%f %f,%f %f)' % (X0, RIV + 1100, X0 + N * RES, RIV + 1100), {'highway': 'track'}),
    ], fields=('highway',))
    out['facilities'] = _vector(str(d / 'fac.gpkg'), ogr.wkbPoint, [
        ('POINT(%f %f)' % (X0 + 1550, RIV + 1150), {'name': 'School'})], fields=('name',))
    return out


def _lyr(value):
    from qgis.core import QgsVectorLayer
    return value if isinstance(value, QgsVectorLayer) else QgsVectorLayer(value, 'o', 'ogr')


def test_full_workflow(qgis_processing, valley, tmp_path):
    run = qgis_processing.run
    rc = run('floodzoning:rivercorridor', {
        'METHOD': 1, 'RIVER_LINES': valley['river'], 'DEPTH': valley[2], 'CHANNEL_DEPTH': 2.5,
        'BUFFER_WIDTH': 30, 'OUTPUT_CHANNEL': str(tmp_path / 'ch.gpkg'), 'OUTPUT_BUFFER': str(tmp_path / 'bf.gpkg')})
    pz = run('floodzoning:planningzones', {
        'DESIGN_DEPTH': valley[100], 'MEDIUM_DEPTH': valley[50], 'FREQUENT_DEPTH': valley[10],
        'VERY_FREQUENT_DEPTH': valley[2], 'SETTLEMENTS': valley['settlements'], 'FACILITIES': valley['facilities'],
        'PROTECTION': rc['OUTPUT_BUFFER'], 'RIVER': rc['OUTPUT_CHANNEL'], 'OUTPUT': str(tmp_path / 'pz.gpkg')})
    ez = run('floodzoning:evacuationzones', {
        'SETTLEMENTS': valley['settlements'], 'TYPE_FIELD': 'kind', 'NAME_FIELD': 'name',
        'RISK_AREAS': pz['OUTPUT'], 'DISTANCE': 100, 'GROUP_DISTANCE': 1000, 'OUTPUT': str(tmp_path / 'ez.gpkg')})
    zones = list(_lyr(ez['OUTPUT']).getFeatures())
    assert sorted(z['name'] for z in zones) == ['North village', 'South camp']   # the dry village is not at risk
    assert {z['zone_id'].split('-')[0] for z in zones} == {'C', 'V'}          # type initials

    sh = run('floodzoning:safehavensroutes', {
        'EVACUATION_ZONES': ez['OUTPUT'], 'DESIGN_DEPTH': valley[100], 'PLANNING_ZONES': pz['OUTPUT'],
        'SETTLEMENTS': valley['settlements'], 'ROADS': valley['roads'], 'ROAD_CLASS_FIELD': 'highway',
        'RIVER': rc['OUTPUT_CHANNEL'], 'DEM': valley['dem'], 'FACILITIES': valley['facilities'],
        'FACILITY_NAME_FIELD': 'name', 'SECTOR_HA': 5,
        'OUTPUT_HAVENS': str(tmp_path / 'sh.gpkg'), 'OUTPUT_ROUTES': str(tmp_path / 'rt.gpkg')})
    havens = list(_lyr(sh['OUTPUT_HAVENS']).getFeatures())
    routes = list(_lyr(sh['OUTPUT_ROUTES']).getFeatures())
    assert len(havens) == 2
    for h in havens:
        assert 0.9 <= h['ratio'] <= 1.2, h['ratio']
        assert h['dist_flood'] >= 150
        assert h['height_chk'] == 'yes'
        assert h['n_routes'] >= 1
    # camp is 9 ha -> 2 sectors of ~5 ha; village 3 ha -> 1 sector
    assert len(routes) == 3
    for r in routes:
        assert r['length_m'] > 100
        assert r['haven_id'] == 'SH-' + r['zone_id']
    # the north village evacuates along the main road
    village = [r for r in routes if r['zone_name'] == 'North village'][0]
    assert village['main_pct'] > 50
