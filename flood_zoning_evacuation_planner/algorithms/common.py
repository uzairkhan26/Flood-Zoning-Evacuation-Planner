# -*- coding: utf-8 -*-
"""Shared QGIS-side helpers: working grid, CRS handling, rasterising layers, styling."""
import math

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsRectangle,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core.gridtools import Grid

try:  # QGIS >= 3.30
    from qgis.core import Qgis
    _METRES = Qgis.DistanceUnit.Meters
except (ImportError, AttributeError):  # pragma: no cover - older QGIS
    _METRES = QgsUnitTypes.DistanceMeters


GUIDE_URL = 'https://github.com/uzairkhan26/Flood-Zoning-Evacuation-Planner/blob/main/docs/user_guide.md'


def guide(anchor):
    """Link to a section of the user guide (shown by the Help button of each tool)."""
    return GUIDE_URL + '#' + anchor


def tr(text):
    return QCoreApplication.translate('FloodZoningEvacuationPlanner', text)


# --------------------------------------------------------------------------
# CRS
# --------------------------------------------------------------------------
def is_metric(crs):
    return crs.isValid() and not crs.isGeographic() and crs.mapUnits() == _METRES


def utm_crs_for(crs, extent, transform_context):
    """WGS 84 / UTM zone CRS covering the centre of ``extent`` (given in ``crs``)."""
    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
    ct = QgsCoordinateTransform(crs, wgs84, transform_context)
    c = ct.transform(extent.center())
    zone = int(math.floor((c.x() + 180.0) / 6.0)) % 60 + 1
    epsg = (32600 if c.y() >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem('EPSG:%d' % epsg)


def working_crs(crs, extent, context, feedback):
    """Return a metric CRS: the input CRS if it is projected in metres, otherwise local UTM."""
    if is_metric(crs):
        return crs
    utm = utm_crs_for(crs, extent, context.transformContext())
    feedback.pushInfo(tr('Input CRS {0} is not projected in metres; working in {1}.').format(
        crs.authid() or crs.description(), utm.authid()))
    return utm


def transform_extent(extent, src, dst, context):
    if src == dst:
        return QgsRectangle(extent)
    ct = QgsCoordinateTransform(src, dst, context.transformContext())
    return ct.transformBoundingBox(extent)


# --------------------------------------------------------------------------
# Working grid
# --------------------------------------------------------------------------
def raster_source(layer):
    if layer is None:
        return None
    if layer.providerType() != 'gdal':
        raise QgsProcessingException(tr('Raster "{0}" must be a file-based (GDAL) raster.').format(layer.name()))
    return layer.source()


def grid_from_raster(layer, cell_size, max_cells, context, feedback):
    """Working grid covering ``layer``: metric CRS, chosen or native resolution,
    coarsened if needed so the grid has at most ``max_cells`` cells."""
    crs = working_crs(layer.crs(), layer.extent(), context, feedback)
    ext = transform_extent(layer.extent(), layer.crs(), crs, context)
    if cell_size and cell_size > 0:
        res = float(cell_size)
    else:
        native = layer.rasterUnitsPerPixelX()
        if layer.crs() != crs:  # approximate native resolution in metres
            native = max(ext.width() / max(layer.width(), 1), ext.height() / max(layer.height(), 1))
        res = float(native)
    ncells = (ext.width() / res) * (ext.height() / res)
    if max_cells and ncells > max_cells:
        new_res = res * math.sqrt(ncells / float(max_cells))
        nice = [1, 2, 2.5, 4, 5, 8, 10, 12.5, 15, 20, 25, 30, 40, 50, 75, 100]
        new_res = next((n for n in nice if n >= new_res), math.ceil(new_res))
        feedback.pushWarning(tr('The study area at {0:g} m cells would need {1:,.0f} million cells; '
                                'using {2:g} m cells instead. Set "Analysis cell size" or '
                                '"Maximum number of cells" to change this.').format(res, ncells / 1e6, new_res))
        res = new_res
    # snap the grid to multiples of the resolution so results line up across runs
    xmin = math.floor(ext.xMinimum() / res) * res
    ymin = math.floor(ext.yMinimum() / res) * res
    xmax = math.ceil(ext.xMaximum() / res) * res
    ymax = math.ceil(ext.yMaximum() / res) * res
    grid = Grid.from_bounds(xmin, ymin, xmax, ymax, res, crs.toWkt())
    feedback.pushInfo(tr('Working grid: {0} x {1} cells of {2:g} m in {3}.').format(
        grid.ncols, grid.nrows, res, crs.authid()))
    return grid, crs


def grid_extent(grid):
    xmin, ymin, xmax, ymax = grid.bounds
    return QgsRectangle(xmin, ymin, xmax, ymax)


# --------------------------------------------------------------------------
# Vector -> grid
# --------------------------------------------------------------------------
def source_geometries(source, crs, context, buffer_m=0.0, clip_rect=None):
    """Geometries of a feature source transformed to ``crs`` (optionally buffered/clipped)."""
    out = []
    if source is None:
        return out
    ct = None
    if source.sourceCrs() != crs:
        ct = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
    for f in source.getFeatures():
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        g = QgsGeometry(g)
        if ct is not None:
            g.transform(ct)
        if buffer_m > 0:
            g = g.buffer(buffer_m, 8)
        if clip_rect is not None:
            if not g.boundingBox().intersects(clip_rect):
                continue
            g = g.clipped(clip_rect)
            if g.isEmpty():
                continue
        out.append(g)
    return out


def geometries_to_wkb(geoms):
    return [bytes(g.asWkb().data()) for g in geoms if g is not None and not g.isEmpty()]


def wkb_to_geometry(wkb_list):
    """Collect WKB polygons (non-overlapping) into one multipart QgsGeometry."""
    parts = []
    for w in wkb_list:
        g = QgsGeometry()
        g.fromWkb(w)
        if not g.isEmpty():
            parts.append(g)
    if not parts:
        return QgsGeometry()
    geom = QgsGeometry.collectGeometry(parts)
    if not geom.isGeosValid():
        geom = geom.makeValid()
    return geom


# --------------------------------------------------------------------------
# Styling of outputs
# --------------------------------------------------------------------------
class CategoryStyler(QgsProcessingLayerPostProcessorInterface):
    """Applies a categorized renderer once the output layer is loaded.

    Instances must be kept alive until QGIS calls them, so they are stored in a
    class-level list.
    """
    _keep = []

    def __init__(self, field, categories, layer_name=None, geometry='polygon'):
        super().__init__()
        self.field = field
        self.categories = categories  # list of (value, label, fill hex, outline hex, opacity)
        self.layer_name = layer_name
        self.geometry = geometry

    @classmethod
    def create(cls, field, categories, layer_name=None, geometry='polygon'):
        s = cls(field, categories, layer_name, geometry)
        cls._keep.append(s)
        return s

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        cats = []
        for value, label, fill, outline, opacity in self.categories:
            if self.geometry == 'line':
                sym = QgsLineSymbol.createSimple({'line_color': outline, 'line_width': '0.9', 'capstyle': 'round'})
            else:
                sym = QgsFillSymbol.createSimple({'color': fill, 'outline_color': outline, 'outline_width': '0.3'})
            sym.setOpacity(opacity)
            cats.append(QgsRendererCategory(value, sym, label))
        layer.setRenderer(QgsCategorizedSymbolRenderer(self.field, cats))
        if self.layer_name:
            layer.setName(self.layer_name)
        layer.triggerRepaint()


class SingleStyler(QgsProcessingLayerPostProcessorInterface):
    """Applies a single fill symbol once the output layer is loaded."""
    _keep = []

    def __init__(self, fill, outline, layer_name=None):
        super().__init__()
        self.fill, self.outline, self.layer_name = fill, outline, layer_name

    @classmethod
    def create(cls, fill, outline, layer_name=None):
        s = cls(fill, outline, layer_name)
        cls._keep.append(s)
        return s

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        sym = QgsFillSymbol.createSimple({'color': self.fill, 'outline_color': self.outline, 'outline_width': '0.4'})
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
        if self.layer_name:
            layer.setName(self.layer_name)
        layer.triggerRepaint()


def set_styler(context, dest_id, styler):
    if dest_id and context.willLoadLayerOnCompletion(dest_id):
        context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(styler)
