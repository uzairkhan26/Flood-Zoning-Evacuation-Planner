# -*- coding: utf-8 -*-
"""Raster helpers built only on GDAL and NumPy (both shipped with QGIS).

All work happens on a regular "working grid" in a projected CRS with metre
units. Masks are NumPy boolean arrays with shape (rows, cols).
"""
import math

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()


class Grid:
    """A north-up regular grid: origin (x0, y0) = top-left corner."""

    def __init__(self, x0, y0, res, ncols, nrows, wkt):
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.res = float(res)
        self.ncols = int(ncols)
        self.nrows = int(nrows)
        self.wkt = wkt

    @classmethod
    def from_bounds(cls, xmin, ymin, xmax, ymax, res, wkt):
        ncols = max(1, int(math.ceil((xmax - xmin) / res)))
        nrows = max(1, int(math.ceil((ymax - ymin) / res)))
        return cls(xmin, ymax, res, ncols, nrows, wkt)

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    @property
    def bounds(self):
        """(xmin, ymin, xmax, ymax)"""
        return (self.x0, self.y0 - self.nrows * self.res, self.x0 + self.ncols * self.res, self.y0)

    @property
    def geotransform(self):
        return (self.x0, self.res, 0.0, self.y0, 0.0, -self.res)

    @property
    def cell_area(self):
        return self.res * self.res

    def px(self, metres):
        """Distance in metres expressed in cells."""
        return metres / self.res

    def area_px(self, hectares):
        """Area in hectares expressed in number of cells (at least 1)."""
        return max(1, int(round(hectares * 10000.0 / self.cell_area)))

    def srs(self):
        s = osr.SpatialReference()
        s.ImportFromWkt(self.wkt)
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        return s


# --------------------------------------------------------------------------
# In-memory datasets
# --------------------------------------------------------------------------
_NP2GDAL = {
    np.dtype('uint8'): gdal.GDT_Byte,
    np.dtype('bool'): gdal.GDT_Byte,
    np.dtype('int32'): gdal.GDT_Int32,
    np.dtype('float32'): gdal.GDT_Float32,
    np.dtype('float64'): gdal.GDT_Float64,
}


def mem_dataset(grid, array=None, dtype=gdal.GDT_Byte):
    if array is not None:
        dtype = _NP2GDAL[array.dtype]
    ds = gdal.GetDriverByName('MEM').Create('', grid.ncols, grid.nrows, 1, dtype)
    ds.SetGeoTransform(grid.geotransform)
    ds.SetProjection(grid.wkt)
    if array is not None:
        ds.GetRasterBand(1).WriteArray(array.astype(np.uint8) if array.dtype == bool else array)
    return ds


def _ogr_memory_datasource():
    for name in ('MEM', 'Memory'):
        drv = ogr.GetDriverByName(name)
        if drv is not None and drv.GetMetadataItem('DCAP_VECTOR') == 'YES':
            return drv.CreateDataSource('mem')
    raise RuntimeError('No in-memory OGR driver available')


# --------------------------------------------------------------------------
# Raster input
# --------------------------------------------------------------------------
_RESAMPLING = {'average': 'average', 'max': 'max', 'nearest': 'near', 'bilinear': 'bilinear'}


def warp_to_grid(source, grid, resampling='average'):
    """Read any GDAL raster onto the working grid. Returns float32 array with NaN for no data."""
    ds = gdal.Warp('', source, format='MEM', outputBounds=grid.bounds,
                   xRes=grid.res, yRes=grid.res, dstSRS=grid.wkt,
                   resampleAlg=_RESAMPLING.get(resampling, resampling),
                   outputType=gdal.GDT_Float32, dstNodata=-99999.0, multithread=True)
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    arr[arr <= -99998.0] = np.nan
    if arr.shape != grid.shape:  # guard against rounding of bounds
        out = np.full(grid.shape, np.nan, np.float32)
        r, c = min(arr.shape[0], grid.nrows), min(arr.shape[1], grid.ncols)
        out[:r, :c] = arr[:r, :c]
        arr = out
    return arr


def rasterize_wkb(wkb_list, grid, all_touched=False):
    """Burn a list of WKB geometries (already in the grid CRS) into a boolean mask."""
    ds = mem_dataset(grid, dtype=gdal.GDT_Byte)
    if not wkb_list:
        return np.zeros(grid.shape, bool)
    src = _ogr_memory_datasource()
    lyr = src.CreateLayer('l', grid.srs(), ogr.wkbUnknown)
    defn = lyr.GetLayerDefn()
    for wkb in wkb_list:
        g = ogr.CreateGeometryFromWkb(bytes(wkb))
        if g is None:
            continue
        f = ogr.Feature(defn)
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    opts = ['ALL_TOUCHED=TRUE'] if all_touched else []
    gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1], options=opts)
    return ds.GetRasterBand(1).ReadAsArray() > 0


# --------------------------------------------------------------------------
# Morphology (distance based, Euclidean, via GDAL proximity)
# --------------------------------------------------------------------------
def distance_to(mask, grid, max_dist_m=None):
    """Euclidean distance (metres) from every cell to the nearest True cell.

    Cells further than ``max_dist_m`` get +inf.
    """
    src = mem_dataset(grid, mask.astype(np.uint8))
    dst = mem_dataset(grid, dtype=gdal.GDT_Float32)
    opts = ['VALUES=1', 'DISTUNITS=PIXEL', 'NODATA=-1']
    if max_dist_m is not None:
        opts.append('MAXDIST=%f' % (grid.px(max_dist_m) + 1.0))
    gdal.ComputeProximity(src.GetRasterBand(1), dst.GetRasterBand(1), opts)
    d = dst.GetRasterBand(1).ReadAsArray().astype(np.float32)
    d[d < 0] = np.inf
    return d * grid.res


def dilate(mask, grid, radius_m):
    if radius_m <= 0:
        return mask.copy()
    return distance_to(mask, grid, radius_m) <= radius_m + 1e-6


def erode(mask, grid, radius_m):
    if radius_m <= 0:
        return mask.copy()
    return ~dilate(~mask, grid, radius_m)


def closing(mask, grid, radius_m):
    return erode(dilate(mask, grid, radius_m), grid, radius_m)


def opening(mask, grid, radius_m):
    return dilate(erode(mask, grid, radius_m), grid, radius_m)


# --------------------------------------------------------------------------
# Region-size filters (GDAL sieve)
# --------------------------------------------------------------------------
def sieve(array, grid, threshold_px, mask=None, connectedness=8):
    """GDAL SieveFilter: regions smaller than ``threshold_px`` cells are merged
    into their largest neighbour. ``mask`` (bool) limits which cells take part."""
    src = mem_dataset(grid, array.astype(np.uint8))
    dst = mem_dataset(grid, dtype=gdal.GDT_Byte)
    mband = None
    if mask is not None:
        mds = mem_dataset(grid, mask.astype(np.uint8))
        mband = mds.GetRasterBand(1)
    gdal.SieveFilter(src.GetRasterBand(1), mband, dst.GetRasterBand(1), int(threshold_px), connectedness)
    return dst.GetRasterBand(1).ReadAsArray()


def fill_small_holes(mask, grid, max_hole_ha):
    if max_hole_ha <= 0:
        return mask.copy()
    s = sieve(mask, grid, grid.area_px(max_hole_ha))
    return mask | (s == 1)


def remove_small_patches(mask, grid, min_area_ha):
    if min_area_ha <= 0:
        return mask.copy()
    s = sieve(mask, grid, grid.area_px(min_area_ha))
    return mask & (s == 1)


# --------------------------------------------------------------------------
# Vector output
# --------------------------------------------------------------------------
def polygonize(codes, grid, valid=None):
    """Polygonize an integer raster. Returns {code: [wkb, ...]} for cells where
    ``valid`` is True (default: code > 0)."""
    codes = codes.astype(np.uint8)
    if valid is None:
        valid = codes > 0
    src = mem_dataset(grid, codes)
    msk = mem_dataset(grid, valid.astype(np.uint8))
    ds = _ogr_memory_datasource()
    lyr = ds.CreateLayer('p', grid.srs(), ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn('code', ogr.OFTInteger))
    gdal.Polygonize(src.GetRasterBand(1), msk.GetRasterBand(1), lyr, 0, [])
    out = {}
    lyr.ResetReading()
    for f in lyr:
        out.setdefault(f.GetField(0), []).append(bytes(f.GetGeometryRef().ExportToWkb()))
    return out


def write_geotiff(path, array, grid, nodata=None, colours=None):
    """Write a single-band GeoTIFF (optionally with a colour table {value: (r,g,b,a)})."""
    dtype = _NP2GDAL[array.dtype]
    ds = gdal.GetDriverByName('GTiff').Create(path, grid.ncols, grid.nrows, 1, dtype,
                                               options=['COMPRESS=DEFLATE', 'TILED=YES'])
    ds.SetGeoTransform(grid.geotransform)
    ds.SetProjection(grid.wkt)
    band = ds.GetRasterBand(1)
    if colours:  # must be set before any pixel is written
        ct = gdal.ColorTable()
        for v, rgba in colours.items():
            ct.SetColorEntry(int(v), tuple(rgba))
        band.SetRasterColorTable(ct)
        band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(array)
    band.FlushCache()
    ds = None


# --------------------------------------------------------------------------
# Zone generalisation ("grid filling")
# --------------------------------------------------------------------------
def generalise(mask, grid, exclude=None, closing_m=32.0, hole_ha=5.0, min_ha=3.0):
    """Join nearby patches, fill small holes and drop tiny patches.

    1. closing with radius ``closing_m`` joins patches separated by small gaps;
    2. a light opening (radius ``closing_m / 4``, at least one cell) removes thin spurs;
    3. holes smaller than ``hole_ha`` are filled;
    4. patches smaller than ``min_ha`` are removed.
    Cells in ``exclude`` are never part of the result.
    """
    m = mask.copy()
    if exclude is not None:
        m &= ~exclude
    if closing_m > 0:
        m = closing(m, grid, closing_m)
        m = opening(m, grid, max(grid.res, closing_m / 4.0))
    m = fill_small_holes(m, grid, hole_ha)
    m = remove_small_patches(m, grid, min_ha)
    if exclude is not None:
        m &= ~exclude
    return m


def build_zone_raster(red, orange, yellow, grid, exclude=None, protection=None,
                      closing_m=32.0, hole_ha=5.0, min_ha=3.0, sliver_ha=2.0, not_red=None):
    """Generalise nested zone masks and return the zone-code raster (uint8).

    Zones are nested: Red sits inside Red+Orange, which sits inside
    Red+Orange+Yellow, so the generalised result never has orange "inside" red
    holes. Remaining slivers smaller than ``sliver_ha`` are merged into the
    neighbouring zone. Cells in ``not_red`` (areas released by the settlement
    rule) are never turned red by the filling; they stay in the restricted zone.
    """
    from .zoning_rules import compose_zones, ZONE_GREEN, ZONE_ORANGE, ZONE_RED
    r = generalise(red, grid, exclude, closing_m, hole_ha, min_ha)
    if not_red is not None:
        r &= ~not_red
    ro = generalise(red | orange, grid, exclude, closing_m, hole_ha, min_ha) | r
    roy = generalise(red | orange | yellow, grid, exclude, closing_m, hole_ha, min_ha) | ro
    z = compose_zones(r, ro, roy)
    if sliver_ha > 0:
        z = sieve(z, grid, grid.area_px(sliver_ha), mask=z > 0).astype(np.uint8)
        if not_red is not None:
            z[not_red & (z == ZONE_RED)] = ZONE_ORANGE
    if exclude is not None:
        z[exclude] = 0
    if protection is not None:
        z[protection] = ZONE_GREEN
    return z
