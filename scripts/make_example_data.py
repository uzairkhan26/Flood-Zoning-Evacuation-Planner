# -*- coding: utf-8 -*-
"""Create the fictional example data set used in the user guide.

    python3 scripts/make_example_data.py [output folder]     (needs GDAL + NumPy, e.g. the QGIS Python)

A 7 km x 5 km river valley (10 m cells, WGS 84 / UTM 36N, fictional place) with:
- ground elevation (dem.tif) and flood depths for the 2-, 10-, 50- and 100-year floods
  for the current situation (depth_rpXX.tif) and after raising the river banks (banks_rpXX.tif);
- a river centre line, settlements (a town, villages, a refugee camp), roads and paths with an
  OpenStreetMap-style 'highway' field, and public facilities.
"""
import math
import os
import sys

import numpy as np
from osgeo import gdal, ogr, osr

EPSG = 32636
X0, Y0, RES, NC, NR = 500000.0, 1000000.0, 10.0, 700, 500
RP_HEIGHT = {2: 1.6, 10: 2.2, 50: 2.7, 100: 3.0}      # flood level above the floodplain base at the river


def river_y(x):
    """River centre line: y as a function of x (meandering west -> east)."""
    t = x - X0
    return Y0 - 2500.0 + 350.0 * np.sin(2 * np.pi * t / 3200.0) + 120.0 * np.sin(2 * np.pi * t / 1300.0 + 0.8)


def base(x):
    return 420.0 - 0.0008 * (x - X0)


def srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(EPSG)
    return s


def write_raster(path, arr, nodata=-9999.0):
    ds = gdal.GetDriverByName('GTiff').Create(path, NC, NR, 1, gdal.GDT_Float32,
                                              ['COMPRESS=DEFLATE', 'PREDICTOR=3', 'TILED=YES'])
    ds.SetGeoTransform((X0, RES, 0, Y0, 0, -RES))
    ds.SetProjection(srs().ExportToWkt())
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(nodata)
    b.WriteArray(np.where(np.isfinite(arr), arr, nodata).astype(np.float32))
    ds = None


def write_vector(path, geom_type, rows, fields):
    if os.path.exists(path):
        os.remove(path)
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(path)
    lyr = ds.CreateLayer(os.path.splitext(os.path.basename(path))[0], srs(), geom_type)
    for name in fields:
        lyr.CreateField(ogr.FieldDefn(name, ogr.OFTString))
    for wkt, attrs in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        for k, v in attrs.items():
            f.SetField(k, v)
        lyr.CreateFeature(f)
    ds = None


def line_wkt(pts):
    return 'LINESTRING(%s)' % ','.join('%.1f %.1f' % p for p in pts)


def blob(cx, cy, rx, ry, seed, n=14):
    """Irregular, roughly elliptical polygon (a settlement outline)."""
    rng = np.random.default_rng(seed)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        f = 0.8 + 0.35 * rng.random()
        pts.append((cx + rx * f * math.cos(a), cy + ry * f * math.sin(a)))
    pts.append(pts[0])
    return 'POLYGON((%s))' % ','.join('%.1f %.1f' % p for p in pts)


def follow(offset, x_from, x_to, step=100.0):
    """Line parallel to the river at a distance ``offset`` (north > 0), smoothed."""
    xs = np.arange(x_from, x_to + 1, step)
    ys = Y0 - 2500.0 + offset + 0.55 * (river_y(xs) - (Y0 - 2500.0))
    return list(zip(xs, ys))


def distance_to_river(xs, ys):
    """Distance [m] from every cell to the river centre line (GDAL proximity on the rasterised line)."""
    drv = gdal.GetDriverByName('MEM')
    ds = drv.Create('', NC, NR, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((X0, RES, 0, Y0, 0, -RES))
    ds.SetProjection(srs().ExportToWkt())
    vds = ogr.GetDriverByName('Memory').CreateDataSource('')
    lyr = vds.CreateLayer('r', srs(), ogr.wkbLineString)
    xl = np.arange(X0, X0 + NC * RES + 1, 5.0)
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetGeometry(ogr.CreateGeometryFromWkt(line_wkt(zip(xl, river_y(xl)))))
    lyr.CreateFeature(f)
    gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1], options=['ALL_TOUCHED=TRUE'])
    out = drv.Create('', NC, NR, 1, gdal.GDT_Float32)
    out.SetGeoTransform((X0, RES, 0, Y0, 0, -RES))
    out.SetProjection(srs().ExportToWkt())
    gdal.ComputeProximity(ds.GetRasterBand(1), out.GetRasterBand(1), ['VALUES=1', 'DISTUNITS=GEO'])
    d = out.GetRasterBand(1).ReadAsArray().astype(np.float64)
    north = ys > river_y(xs)
    return d, north


def connected(wet, seed):
    """Cells of ``wet`` connected (8-neighbours) to ``seed``: flooding only from the river."""
    cur = seed & wet
    while True:
        g = cur.copy()
        g[1:] |= cur[:-1]
        g[:-1] |= cur[1:]
        g[:, 1:] |= cur[:, :-1]
        g[:, :-1] |= cur[:, 1:]
        g[1:, 1:] |= cur[:-1, :-1]
        g[:-1, :-1] |= cur[1:, 1:]
        g[1:, :-1] |= cur[:-1, 1:]
        g[:-1, 1:] |= cur[1:, :-1]
        g &= wet
        if (g == cur).all():
            return cur
        cur = g


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    xs = X0 + (np.arange(NC) + 0.5)[None, :] * RES * np.ones((NR, 1))
    ys = Y0 - (np.arange(NR) + 0.5)[:, None] * RES * np.ones((1, NC))
    d, north = distance_to_river(xs, ys)

    # ground: valley floor falling to the east, gentle floodplain, steeper hills, small ridges/hollows
    noise = (0.45 * np.sin(xs / 430.0) * np.cos(ys / 610.0) + 0.3 * np.sin((xs + ys) / 900.0)
             + 0.25 * np.cos((xs - 2 * ys) / 520.0))
    terrace = np.where(north, 0.25, 0.0)          # north bank slightly higher
    dem = (base(xs) + 1.2 + 0.0022 * d + 0.012 * np.maximum(0, d - 900) + noise + terrace
           - 3.5 * np.maximum(0, 1 - d / 25.0))
    write_raster(os.path.join(out_dir, 'dem.tif'), dem)

    river = d <= 12
    for tag, lowered in (('depth', 0.0), ('banks', 1.0)):
        for rp, h in RP_HEIGHT.items():
            wse = base(xs) + h
            depth = wse - dem
            if tag == 'banks':     # raised banks: small and medium floods stay in the channel,
                if rp <= 10:       # large floods are a little lower
                    depth = np.where(d <= 40, depth, 0.0)
                else:
                    depth = np.where(d <= 40, depth, depth - lowered * (0.45 if rp == 50 else 0.3))
            wet = connected(depth > 0.01, river)
            write_raster(os.path.join(out_dir, '%s_rp%d.tif' % (tag, rp)), np.where(wet, depth, np.nan))

    # river centre line --------------------------------------------------------------
    xl = np.arange(X0, X0 + NC * RES + 1, 25.0)
    write_vector(os.path.join(out_dir, 'river.gpkg'), ogr.wkbLineString,
                 [(line_wkt(zip(xl, river_y(xl))), {'name': 'Example river'})], ['name'])

    # settlements ---------------------------------------------------------------------
    def at(x, offset):
        return x, float(river_y(np.array([x]))[0]) + offset

    s = [
        (blob(*at(X0 + 3000, 520), 650, 330, 1), {'type': 'Town', 'name': 'Riverside town'}),
        (blob(*at(X0 + 1250, -430), 210, 140, 2), {'type': 'Village', 'name': 'Kolo'}),
        (blob(*at(X0 + 5250, 650), 230, 130, 3), {'type': 'Village', 'name': 'Mabek'}),
        (blob(*at(X0 + 5700, 820), 120, 90, 4), {'type': 'Village', 'name': 'Mabek east'}),
        (blob(*at(X0 + 6250, -320), 150, 100, 5), {'type': 'Village', 'name': 'Tiri'}),
        (blob(*at(X0 + 1800, 1650), 160, 110, 6), {'type': 'Village', 'name': 'Hilltop'}),
        (blob(*at(X0 + 4300, -700), 300, 280, 7), {'type': 'Refugee camp', 'name': 'Hope camp'}),
    ]
    write_vector(os.path.join(out_dir, 'settlements.gpkg'), ogr.wkbPolygon, s, ['type', 'name'])

    # roads and paths -----------------------------------------------------------------
    xb = X0 + 3300
    yb = float(river_y(np.array([xb]))[0])
    roads = [
        (line_wkt(follow(1450, X0, X0 + NC * RES)), {'highway': 'secondary', 'name': 'North road'}),
        (line_wkt([(xb, Y0), (xb - 60, yb + 900), (xb, yb + 80), (xb, yb - 80), (xb + 120, yb - 1300),
                   (xb + 200, Y0 - NR * RES)]), {'highway': 'secondary', 'name': 'Bridge road'}),
        (line_wkt(follow(-1350, X0, X0 + NC * RES)), {'highway': 'track', 'name': 'South track'}),
        (line_wkt([at(X0 + 1250, -430), at(X0 + 1300, -1300)]), {'highway': 'track', 'name': ''}),
        (line_wkt([at(X0 + 5250, 650), at(X0 + 5300, 1400)]), {'highway': 'track', 'name': ''}),
        (line_wkt([at(X0 + 6250, -320), at(X0 + 6200, -1300)]), {'highway': 'track', 'name': ''}),
        (line_wkt([at(X0 + 4300, -700), at(X0 + 4250, -1300)]), {'highway': 'track', 'name': ''}),
        (line_wkt([at(X0 + 2500, 520), at(X0 + 3500, 520)]), {'highway': 'residential', 'name': ''}),
        (line_wkt([at(X0 + 2700, 300), at(X0 + 2700, 800)]), {'highway': 'residential', 'name': ''}),
        (line_wkt([at(X0 + 1800, 1650), at(X0 + 1900, 1450)]), {'highway': 'path', 'name': ''}),
        (line_wkt([at(X0 + 1250, -430), at(X0 + 2200, -350), at(X0 + 3250, -150)]), {'highway': 'path', 'name': ''}),
        (line_wkt([at(X0 + 3600, 400), at(X0 + 4600, 500), at(X0 + 5250, 650)]), {'highway': 'path', 'name': ''}),
        (line_wkt([at(X0 + 5700, 820), at(X0 + 5900, 1400)]), {'highway': 'path', 'name': ''}),
    ]
    write_vector(os.path.join(out_dir, 'roads.gpkg'), ogr.wkbLineString, roads, ['highway', 'name'])

    # facilities ----------------------------------------------------------------------
    def pt(x, y):
        return 'POINT(%.1f %.1f)' % (x, y)

    fac = [
        (pt(*at(X0 + 2900, 450)), {'name': 'Town primary school', 'amenity': 'school'}),
        (pt(*at(X0 + 3150, 700)), {'name': 'Town market', 'amenity': 'marketplace'}),
        (pt(*at(X0 + 3450, 1550)), {'name': 'District health centre', 'amenity': 'clinic'}),
        (pt(*at(X0 + 4700, 1500)), {'name': 'Hill secondary school', 'amenity': 'school'}),
        (pt(*at(X0 + 4300, -650)), {'name': 'Camp clinic', 'amenity': 'clinic'}),
        (pt(*at(X0 + 1300, -1400)), {'name': 'South primary school', 'amenity': 'school'}),
    ]
    write_vector(os.path.join(out_dir, 'facilities.gpkg'), ogr.wkbPoint, fac, ['name', 'amenity'])
    print('Example data written to', os.path.abspath(out_dir))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'example_data')
