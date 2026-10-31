# -*- coding: utf-8 -*-
"""Processing algorithm: river channel polygon and protection buffer."""
import numpy as np
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)

from ..core import gridtools as gt
from .common import (
    CategoryStyler,
    geometries_to_wkb,
    grid_extent,
    grid_from_raster,
    guide,
    raster_source,
    set_styler,
    source_geometries,
    tr,
    working_crs,
)
from .compat import NUMBER_DOUBLE, TYPE_LINE, TYPE_POLYGON, WKB_MULTIPOLYGON, add_advanced, field

METHOD_POLYGONS, METHOD_RASTER, METHOD_FIXED = 0, 1, 2


class RiverCorridorAlgorithm(QgsProcessingAlgorithm):
    METHOD = 'METHOD'
    POLYGONS = 'RIVER_POLYGONS'
    LINES = 'RIVER_LINES'
    DEPTH = 'DEPTH'
    CHANNEL_DEPTH = 'CHANNEL_DEPTH'
    SEARCH = 'SEARCH_DISTANCE'
    MAX_DIST = 'MAX_DISTANCE'
    MIN_PART = 'MIN_PART_AREA'
    FIXED_WIDTH = 'FIXED_WIDTH'
    FILL_GAPS = 'FILL_GAPS'
    EXTENT = 'EXTENT'
    BUFFER = 'BUFFER_WIDTH'
    CELL_SIZE = 'CELL_SIZE'
    MAX_CELLS = 'MAX_CELLS'
    OUT_CHANNEL = 'OUTPUT_CHANNEL'
    OUT_BUFFER = 'OUTPUT_BUFFER'

    def name(self):
        return 'rivercorridor'

    def displayName(self):  # noqa: N802
        return tr('River corridor (channel and protection buffer)')

    def group(self):
        return tr('1 - Flood zoning')

    def groupId(self):  # noqa: N802
        return 'zoning'

    def createInstance(self):  # noqa: N802
        return RiverCorridorAlgorithm()

    def helpUrl(self):  # noqa: N802
        return guide('step-1-river-corridor')

    def shortHelpString(self):  # noqa: N802
        return tr(
            '<p>Creates the river channel as a polygon with its real width, and a protection buffer '
            'of a given width along both banks (the green zone of the spatial planning zones).</p>'
            '<p>Three ways to define the channel:</p>'
            '<ul>'
            '<li><b>River polygons as provided</b>: uses an existing river polygon layer '
            '(e.g. OpenStreetMap water areas).</li>'
            '<li><b>Channel from a flood-depth raster along river lines</b>: the channel is taken '
            'from the hydraulic model - the continuous strip of deep water (default 2 m) in a frequent '
            'flood (e.g. 2-year) that lies along the river lines. Works where river polygons are '
            'missing or inaccurate. River polygons, if given, are added to the result.</li>'
            '<li><b>Fixed width along river lines</b>: a simple strip of the given width.</li>'
            '</ul>'
            '<p>The result can be limited to an extent (for the raster method it is limited to the '
            'raster automatically). The buffer is a ring around the channel; the channel itself is '
            'not part of it.</p>')

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD, tr('How to define the river channel'),
            [tr('River polygons as provided'),
             tr('Channel from a flood-depth raster along river lines'),
             tr('Fixed width along river lines')], defaultValue=METHOD_RASTER))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.POLYGONS, tr('River polygons'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINES, tr('River centre lines'), [TYPE_LINE], optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEPTH, tr('Flood depth raster of a frequent event (e.g. 2-year) [m]'), optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CHANNEL_DEPTH, tr('Minimum depth of the channel in that event [m]'), NUMBER_DOUBLE, 2.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.FIXED_WIDTH, tr('Channel width for the fixed-width method / gap filling [m]'),
            NUMBER_DOUBLE, 20.0, minValue=0.1))
        self.addParameter(QgsProcessingParameterNumber(
            self.BUFFER, tr('Protection buffer width on each bank [m]'), NUMBER_DOUBLE, 30.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, tr('Limit to extent'), optional=True))

        for p in (
            QgsProcessingParameterNumber(self.SEARCH, tr('Channel must touch the river line within [m]'),
                                         NUMBER_DOUBLE, 60.0, minValue=0.0),
            QgsProcessingParameterNumber(self.MAX_DIST, tr('Ignore deep water further than [m] from the river line'),
                                         NUMBER_DOUBLE, 500.0, minValue=0.0),
            QgsProcessingParameterNumber(self.MIN_PART, tr('Ignore channel pieces smaller than [ha]'),
                                         NUMBER_DOUBLE, 0.2, minValue=0.0),
            QgsProcessingParameterBoolean(self.FILL_GAPS, tr('Fill gaps along the river lines with the fixed width'), False),
            QgsProcessingParameterNumber(self.CELL_SIZE, tr('Analysis cell size [m] (0 = raster resolution)'),
                                         NUMBER_DOUBLE, 0.0, minValue=0.0),
            QgsProcessingParameterNumber(self.MAX_CELLS, tr('Maximum number of cells (millions)'),
                                         NUMBER_DOUBLE, 150.0, minValue=1.0),
        ):
            self.addParameter(add_advanced(p))

        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_CHANNEL, tr('River channel'), TYPE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_BUFFER, tr('River protection buffer'), TYPE_POLYGON))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        method = self.parameterAsEnum(parameters, self.METHOD, context)
        polys = self.parameterAsSource(parameters, self.POLYGONS, context)
        lines = self.parameterAsSource(parameters, self.LINES, context)
        depth_layer = self.parameterAsRasterLayer(parameters, self.DEPTH, context)
        chan_depth = self.parameterAsDouble(parameters, self.CHANNEL_DEPTH, context)
        width = self.parameterAsDouble(parameters, self.FIXED_WIDTH, context)
        buf_w = self.parameterAsDouble(parameters, self.BUFFER, context)
        search = self.parameterAsDouble(parameters, self.SEARCH, context)
        max_dist = self.parameterAsDouble(parameters, self.MAX_DIST, context)
        min_part = self.parameterAsDouble(parameters, self.MIN_PART, context) * 1e4
        fill_gaps = self.parameterAsBool(parameters, self.FILL_GAPS, context)
        cell = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        max_cells = self.parameterAsDouble(parameters, self.MAX_CELLS, context) * 1e6

        if method == METHOD_POLYGONS and polys is None:
            raise QgsProcessingException(tr('Choose a river polygon layer for this method.'))
        if method in (METHOD_RASTER, METHOD_FIXED) and lines is None:
            raise QgsProcessingException(tr('Choose a river line layer for this method.'))
        if method == METHOD_RASTER and depth_layer is None:
            raise QgsProcessingException(tr('Choose a flood depth raster for this method.'))

        # CRS and clipping rectangle ------------------------------------------
        if method == METHOD_RASTER:
            grid, crs = grid_from_raster(depth_layer, cell, max_cells, context, feedback)
            rect = grid_extent(grid)
        else:
            ref = polys if method == METHOD_POLYGONS else lines
            crs = working_crs(ref.sourceCrs(), ref.sourceExtent(), context, feedback)
            rect = None
        if parameters.get(self.EXTENT):
            ext = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
            rect = ext if rect is None else rect.intersect(ext)
        feedback.setProgress(10)

        line_geoms = source_geometries(lines, crs, context, clip_rect=rect) if lines is not None else []
        poly_geoms = source_geometries(polys, crs, context, clip_rect=rect) if polys is not None else []

        # Channel ---------------------------------------------------------------
        if method == METHOD_POLYGONS:
            channel = QgsGeometry.unaryUnion(poly_geoms) if poly_geoms else QgsGeometry()
        elif method == METHOD_FIXED:
            channel = QgsGeometry.unaryUnion([g.buffer(width / 2.0, 8) for g in line_geoms])
        else:
            channel = self._channel_from_raster(depth_layer, grid, line_geoms, chan_depth, search,
                                                max_dist, min_part, feedback)
            if poly_geoms:
                channel = QgsGeometry.unaryUnion([channel] + poly_geoms) if not channel.isEmpty() \
                    else QgsGeometry.unaryUnion(poly_geoms)
        if fill_gaps and line_geoms and method != METHOD_FIXED:
            strip = QgsGeometry.unaryUnion([g.buffer(width / 2.0, 8) for g in line_geoms])
            channel = strip if channel.isEmpty() else QgsGeometry.unaryUnion([channel, strip])
        if channel.isEmpty():
            raise QgsProcessingException(tr('No river channel found with these inputs and settings.'))
        if rect is not None:
            channel = channel.clipped(rect)
        if not channel.isGeosValid():
            channel = channel.makeValid()
        feedback.setProgress(70)

        # Buffer ring -----------------------------------------------------------
        ring = channel.buffer(buf_w, 12).difference(channel) if buf_w > 0 else QgsGeometry()
        if rect is not None and not ring.isEmpty():
            ring = ring.clipped(rect)
        feedback.setProgress(90)

        # Outputs ---------------------------------------------------------------
        method_name = ['River polygons', 'Flood-depth raster channel', 'Fixed width'][method]
        f1 = QgsFields()
        for spec in (('name', 'string', 40), ('method', 'string', 40), ('area_ha', 'double', 14, 2)):
            f1.append(field(*spec))
        sink1, id1 = self.parameterAsSink(parameters, self.OUT_CHANNEL, context, f1, WKB_MULTIPOLYGON, crs)
        channel.convertToMultiType()
        feat = QgsFeature(f1)
        feat.setGeometry(channel)
        feat.setAttributes(['River channel', method_name, round(channel.area() / 1e4, 2)])
        sink1.addFeature(feat, QgsFeatureSink.FastInsert)

        f2 = QgsFields()
        for spec in (('name', 'string', 40), ('width_m', 'double', 8, 1), ('area_ha', 'double', 14, 2)):
            f2.append(field(*spec))
        sink2, id2 = self.parameterAsSink(parameters, self.OUT_BUFFER, context, f2, WKB_MULTIPOLYGON, crs)
        if not ring.isEmpty():
            ring.convertToMultiType()
            feat = QgsFeature(f2)
            feat.setGeometry(ring)
            feat.setAttributes(['River protection buffer', buf_w, round(ring.area() / 1e4, 2)])
            sink2.addFeature(feat, QgsFeatureSink.FastInsert)
        feedback.pushInfo(tr('River channel: {0:,.1f} ha; protection buffer: {1:,.1f} ha.').format(
            channel.area() / 1e4, ring.area() / 1e4 if not ring.isEmpty() else 0.0))

        set_styler(context, id1, CategoryStyler.create(
            'name', [('River channel', tr('River channel'), '#1f78b4', '#08306b', 1.0)]))
        set_styler(context, id2, CategoryStyler.create(
            'name', [('River protection buffer', tr('River protection buffer'), '#33a02c', '#1b5e20', 0.6)]))
        feedback.setProgress(100)
        return {self.OUT_CHANNEL: id1, self.OUT_BUFFER: id2}

    # ------------------------------------------------------------------
    @staticmethod
    def _channel_from_raster(layer, grid, line_geoms, chan_depth, search, max_dist, min_part, feedback):
        if not line_geoms:
            raise QgsProcessingException(tr('No river lines inside the raster extent.'))
        depth = gt.warp_to_grid(raster_source(layer), grid, 'average')
        lines_mask = gt.rasterize_wkb(geometries_to_wkb(line_geoms), grid, all_touched=True)
        near = gt.distance_to(lines_mask, grid, max_dist) <= max_dist
        with np.errstate(invalid='ignore'):
            deep = (np.nan_to_num(depth, nan=0.0) >= chan_depth) & near
        del depth
        deep = gt.opening(deep, grid, grid.res)          # cut one-cell links to the floodplain
        deep = gt.closing(deep, grid, 2 * grid.res)      # close small notches
        pieces = gt.polygonize(deep.astype(np.uint8), grid).get(1, [])
        if not pieces:
            raise QgsProcessingException(tr('No water deeper than {0:g} m found along the river lines.').format(chan_depth))
        seed = QgsGeometry.unaryUnion(line_geoms).buffer(search, 8)
        keep = []
        for w in pieces:
            g = QgsGeometry()
            g.fromWkb(w)
            if g.area() >= min_part and g.intersects(seed):
                keep.append(g)
        feedback.pushInfo(tr('Channel pieces kept: {0} of {1}.').format(len(keep), len(pieces)))
        if not keep:
            raise QgsProcessingException(tr('No channel piece touches the river lines; increase the search distance '
                                            'or lower the channel depth.'))
        channel = QgsGeometry.unaryUnion(keep)
        channel = channel.removeInteriorRings(5000.0)    # fill islands < 0.5 ha
        return channel.simplify(grid.res * 0.5)
