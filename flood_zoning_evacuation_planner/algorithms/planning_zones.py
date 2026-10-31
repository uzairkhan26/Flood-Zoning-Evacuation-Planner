# -*- coding: utf-8 -*-
"""Processing algorithm: flood spatial planning zones."""
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from ..core import gridtools as gt
from ..core.zoning_rules import ZONE_GREEN, ZONE_ORANGE, ZONE_RED, ZONE_YELLOW, ZONES, zone_masks
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
    wkb_to_geometry,
)
from .compat import NUMBER_DOUBLE, TYPE_POINT, TYPE_POLYGON, WKB_MULTIPOLYGON, add_advanced, field

RESAMPLING = ['average', 'max', 'nearest']


class PlanningZonesAlgorithm(QgsProcessingAlgorithm):
    DESIGN = 'DESIGN_DEPTH'
    MEDIUM = 'MEDIUM_DEPTH'
    FREQUENT = 'FREQUENT_DEPTH'
    VERY_FREQUENT = 'VERY_FREQUENT_DEPTH'
    MIN_DEPTH = 'MIN_DEPTH'
    MID_DEPTH = 'MID_DEPTH'
    DEEP_DEPTH = 'DEEP_DEPTH'
    SETTLEMENTS = 'SETTLEMENTS'
    FACILITIES = 'FACILITIES'
    FACILITY_RADIUS = 'FACILITY_RADIUS'
    SETTLEMENT_RULE = 'SETTLEMENT_RULE'
    PROTECTION = 'PROTECTION'
    RIVER = 'RIVER'
    SCENARIO = 'SCENARIO'
    CELL_SIZE = 'CELL_SIZE'
    MAX_CELLS = 'MAX_CELLS'
    RESAMPLE = 'RESAMPLING'
    CLOSING = 'CLOSING_DISTANCE'
    HOLE_AREA = 'HOLE_AREA'
    MIN_AREA = 'MIN_AREA'
    SLIVER_AREA = 'SLIVER_AREA'
    SIMPLIFY = 'SIMPLIFY'
    OUTPUT = 'OUTPUT'
    OUTPUT_RASTER = 'OUTPUT_RASTER'

    # ------------------------------------------------------------------
    def name(self):
        return 'planningzones'

    def displayName(self):  # noqa: N802
        return tr('Spatial planning zones')

    def group(self):
        return tr('1 - Flood zoning')

    def groupId(self):  # noqa: N802
        return 'zoning'

    def createInstance(self):  # noqa: N802
        return PlanningZonesAlgorithm()

    def helpUrl(self):  # noqa: N802
        return guide('step-2-spatial-planning-zones')

    def shortHelpString(self):  # noqa: N802
        return tr(
            '<p>Turns flood-depth rasters into four planning zones:</p>'
            '<ul>'
            '<li><b>No development (red)</b>: flooded in the very frequent or frequent event, '
            'or deeper than the "deep water" depth in the design event.</li>'
            '<li><b>Restricted development (orange)</b>: flooded in the medium-probability event, '
            'or moderately deep in the design event.</li>'
            '<li><b>Conditional development (yellow)</b>: flooded only in the design event, and shallow.</li>'
            '<li><b>Protection zone (green)</b>: the river protection buffer, if provided '
            '(see the "River corridor" tool).</li>'
            '</ul>'
            '<p>Water shallower than the minimum depth is treated as low-intensity flooding and ignored. '
            'Only the design (e.g. 100-year) event is required; the other events are optional. '
            'Use depths from the same scenario (e.g. current condition, or after mitigation measures) '
            'and run the tool once per scenario to compare.</p>'
            '<p><b>Existing settlements</b>: if a settlements layer is given and the rule is enabled, '
            'areas inside settlements that are "high hazard" only because of shallow flooding in the '
            'frequent event (not flooded in the very frequent event, and shallower than the deep-water '
            'depth in the design event) are placed in the Restricted zone rather than No development.</p>'
            '<p><b>Grid filling</b>: zones are smoothed into a few continuous areas: nearby patches '
            'are joined, small holes filled and tiny patches removed. Zones are nested (red inside '
            'orange inside yellow).</p>'
            '<p>All work is done on a regular grid in a metric CRS (the depth raster CRS, or the local '
            'UTM zone if that CRS is geographic). Large areas are processed at a coarser cell size '
            'automatically; depths are aggregated by averaging.</p>')

    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DESIGN, tr('Flood depth - design / low-probability event (e.g. 100-year) [m]')))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.MEDIUM, tr('Flood depth - medium-probability event (e.g. 50-year) [m]'), optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.FREQUENT, tr('Flood depth - frequent event (e.g. 10-year) [m]'), optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.VERY_FREQUENT, tr('Flood depth - very frequent event (e.g. 2-year) [m]'), optional=True))

        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_DEPTH, tr('Minimum depth: shallower water is ignored (low intensity) [m]'),
            NUMBER_DOUBLE, 0.3, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MID_DEPTH, tr('Moderate depth in the design event (restricted zone) [m]'),
            NUMBER_DOUBLE, 0.5, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DEEP_DEPTH, tr('Deep water in the design event (no-development zone) [m]'),
            NUMBER_DOUBLE, 1.0, minValue=0.0))

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SETTLEMENTS, tr('Existing settlements / built-up areas'),
            [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FACILITIES, tr('Facilities (schools, health centres, ...)'),
            [TYPE_POINT, TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.FACILITY_RADIUS, tr('Distance around facilities treated as settled [m]'),
            NUMBER_DOUBLE, 30.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SETTLEMENT_RULE, tr('Apply the existing-settlement rule'), True))

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PROTECTION, tr('River protection buffer (becomes the green zone)'),
            [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.RIVER, tr('River channel polygons (excluded from the zones)'),
            [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.SCENARIO, tr('Scenario label (e.g. "Current condition", "With measures")'),
            '', optional=True))

        adv = [
            QgsProcessingParameterNumber(self.CELL_SIZE, tr('Analysis cell size [m] (0 = automatic)'),
                                         NUMBER_DOUBLE, 0.0, minValue=0.0),
            QgsProcessingParameterNumber(self.MAX_CELLS, tr('Maximum number of cells (millions)'),
                                         NUMBER_DOUBLE, 60.0, minValue=1.0),
            QgsProcessingParameterEnum(self.RESAMPLE, tr('Depth aggregation when cells are coarser'),
                                       [tr('Average'), tr('Maximum'), tr('Nearest')], defaultValue=0),
            QgsProcessingParameterNumber(self.CLOSING, tr('Join patches closer than [m]'),
                                         NUMBER_DOUBLE, 32.0, minValue=0.0),
            QgsProcessingParameterNumber(self.HOLE_AREA, tr('Fill holes smaller than [ha]'),
                                         NUMBER_DOUBLE, 5.0, minValue=0.0),
            QgsProcessingParameterNumber(self.MIN_AREA, tr('Remove patches smaller than [ha]'),
                                         NUMBER_DOUBLE, 3.0, minValue=0.0),
            QgsProcessingParameterNumber(self.SLIVER_AREA, tr('Merge slivers smaller than [ha] into the neighbouring zone'),
                                         NUMBER_DOUBLE, 2.0, minValue=0.0),
            QgsProcessingParameterNumber(self.SIMPLIFY, tr('Simplify output outlines by [m] (0 = no)'),
                                         NUMBER_DOUBLE, 0.0, minValue=0.0),
        ]
        for p in adv:
            self.addParameter(add_advanced(p))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, tr('Spatial planning zones'), TYPE_POLYGON))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_RASTER, tr('Zones raster (optional)'), optional=True, createByDefault=False))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        design = self.parameterAsRasterLayer(parameters, self.DESIGN, context)
        if design is None:
            raise QgsProcessingException(tr('A design-event flood depth raster is required.'))
        events = {k: self.parameterAsRasterLayer(parameters, k, context)
                  for k in (self.MEDIUM, self.FREQUENT, self.VERY_FREQUENT)}
        tmin = self.parameterAsDouble(parameters, self.MIN_DEPTH, context)
        tmid = self.parameterAsDouble(parameters, self.MID_DEPTH, context)
        tdeep = self.parameterAsDouble(parameters, self.DEEP_DEPTH, context)
        if not (tmin <= tmid <= tdeep):
            raise QgsProcessingException(tr('Depths must satisfy: minimum <= moderate <= deep.'))
        settlements = self.parameterAsSource(parameters, self.SETTLEMENTS, context)
        facilities = self.parameterAsSource(parameters, self.FACILITIES, context)
        fac_radius = self.parameterAsDouble(parameters, self.FACILITY_RADIUS, context)
        use_rule = self.parameterAsBool(parameters, self.SETTLEMENT_RULE, context)
        protection = self.parameterAsSource(parameters, self.PROTECTION, context)
        river = self.parameterAsSource(parameters, self.RIVER, context)
        scenario = self.parameterAsString(parameters, self.SCENARIO, context) or ''
        cell = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        max_cells = self.parameterAsDouble(parameters, self.MAX_CELLS, context) * 1e6
        resampling = RESAMPLING[self.parameterAsEnum(parameters, self.RESAMPLE, context)]
        closing_m = self.parameterAsDouble(parameters, self.CLOSING, context)
        hole_ha = self.parameterAsDouble(parameters, self.HOLE_AREA, context)
        min_ha = self.parameterAsDouble(parameters, self.MIN_AREA, context)
        sliver_ha = self.parameterAsDouble(parameters, self.SLIVER_AREA, context)
        simplify = self.parameterAsDouble(parameters, self.SIMPLIFY, context)

        steps = 8
        step = [0]

        def advance(msg):
            step[0] += 1
            feedback.setProgress(100.0 * step[0] / steps)
            feedback.pushInfo(msg)
            if feedback.isCanceled():
                raise QgsProcessingException(tr('Cancelled.'))

        # 1. working grid and depths ---------------------------------------
        grid, crs = grid_from_raster(design, cell, max_cells, context, feedback)
        rect = grid_extent(grid)
        depth = {'design': gt.warp_to_grid(raster_source(design), grid, resampling)}
        for key, name in ((self.MEDIUM, 'medium'), (self.FREQUENT, 'frequent'),
                          (self.VERY_FREQUENT, 'very_frequent')):
            lyr = events[key]
            depth[name] = gt.warp_to_grid(raster_source(lyr), grid, resampling) if lyr else None
        advance(tr('Flood depths read onto the working grid.'))

        # 2. settlements ----------------------------------------------------
        settled = None
        if use_rule and (settlements is not None or facilities is not None):
            settled = gt.rasterize_wkb(geometries_to_wkb(
                source_geometries(settlements, crs, context, clip_rect=rect)), grid)
            if facilities is not None:
                fac = gt.rasterize_wkb(geometries_to_wkb(
                    source_geometries(facilities, crs, context, clip_rect=rect)), grid, all_touched=True)
                settled |= gt.dilate(fac, grid, fac_radius)
        advance(tr('Settlements prepared.'))

        # 3. hazard rules ---------------------------------------------------
        red, orange, yellow, relaxed = zone_masks(depth['design'], depth['medium'], depth['frequent'],
                                                  depth['very_frequent'], settled=settled,
                                                  min_depth=tmin, medium_depth=tmid, deep_depth=tdeep,
                                                  return_relaxed=True)
        del depth
        a = grid.cell_area / 1e4
        feedback.pushInfo(tr('Raw zones before grid filling: red {0:,.1f} ha, orange {1:,.1f} ha, '
                             'yellow {2:,.1f} ha.').format(red.sum() * a, orange.sum() * a, yellow.sum() * a))
        advance(tr('Hazard rules applied.'))

        # 4. exclusions: river and protection buffer -------------------------
        prot_geoms = source_geometries(protection, crs, context, clip_rect=rect)
        river_geoms = source_geometries(river, crs, context, clip_rect=rect)
        prot_mask = gt.rasterize_wkb(geometries_to_wkb(prot_geoms), grid) if prot_geoms else None
        river_mask = gt.rasterize_wkb(geometries_to_wkb(river_geoms), grid) if river_geoms else None
        exclude = None
        for m in (prot_mask, river_mask):
            if m is not None:
                exclude = m.copy() if exclude is None else (exclude | m)
        advance(tr('River and protection areas prepared.'))

        # 5. grid filling ---------------------------------------------------
        green_mask = prot_mask
        if prot_mask is not None and river_mask is not None:
            green_mask = prot_mask & ~river_mask
        z = gt.build_zone_raster(red, orange, yellow, grid, exclude=exclude, protection=green_mask,
                                 closing_m=closing_m, hole_ha=hole_ha, min_ha=min_ha, sliver_ha=sliver_ha,
                                 not_red=relaxed if settled is not None else None)
        del red, orange, yellow, relaxed
        advance(tr('Zones generalised (grid filling).'))

        # 6. optional raster output ----------------------------------------
        raster_out = self.parameterAsOutputLayer(parameters, self.OUTPUT_RASTER, context)
        if raster_out:
            colours = {0: (0, 0, 0, 0)}
            for code, zinfo in ZONES.items():
                h = zinfo['hex'].lstrip('#')
                colours[code] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
            gt.write_geotiff(raster_out, z, grid, nodata=0, colours=colours)
        advance(tr('Raster written.') if raster_out else tr('No raster output requested.'))

        # 7. vectorise -------------------------------------------------------
        pieces = gt.polygonize(z, grid, valid=(z >= ZONE_RED) & (z <= ZONE_YELLOW))
        geoms = {code: wkb_to_geometry(pieces.get(code, [])) for code in (ZONE_RED, ZONE_ORANGE, ZONE_YELLOW)}
        if prot_geoms:
            green = QgsGeometry.unaryUnion(prot_geoms)
            if river_geoms:
                green = green.difference(QgsGeometry.unaryUnion(river_geoms))
            geoms[ZONE_GREEN] = green
        advance(tr('Zones converted to polygons.'))

        # 8. write features --------------------------------------------------
        fields = QgsFields()
        for spec in (('zone_code', 'int'), ('zone', 'string', 40), ('colour', 'string', 10),
                     ('hex', 'string', 8), ('criteria', 'string', 254), ('rule', 'string', 254),
                     ('scenario', 'string', 80), ('area_ha', 'double', 14, 2)):
            fields.append(field(*spec))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                             WKB_MULTIPOLYGON, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        crit = self._criteria(tmin, tmid, tdeep, settled is not None, bool(events[self.MEDIUM]),
                              bool(events[self.FREQUENT]), bool(events[self.VERY_FREQUENT]))
        for code in (ZONE_RED, ZONE_ORANGE, ZONE_YELLOW, ZONE_GREEN):
            g = geoms.get(code)
            if g is None or g.isEmpty():
                continue
            if simplify > 0:
                g = g.simplify(simplify)
            g.convertToMultiType()
            f = QgsFeature(fields)
            f.setGeometry(g)
            zi = ZONES[code]
            f.setAttributes([code, zi['name'], zi['colour'], zi['hex'], crit[code], zi['rule'],
                             scenario, round(g.area() / 1e4, 2)])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            feedback.pushInfo('{0}: {1:,.1f} ha'.format(zi['name'], g.area() / 1e4))
        advance(tr('Done.'))

        cats = [(c, ZONES[c]['name'], ZONES[c]['hex'], '#404040', 0.75) for c in ZONES]
        set_styler(context, dest_id, CategoryStyler.create(
            'zone_code', cats, tr('Planning zones') + (' - ' + scenario if scenario else '')))
        return {self.OUTPUT: dest_id, self.OUTPUT_RASTER: raster_out}

    # ------------------------------------------------------------------
    @staticmethod
    def _criteria(tmin, tmid, tdeep, settled, has_medium, has_frequent, has_very_frequent):
        freq = [n for n, ok in (('very frequent', has_very_frequent), ('frequent', has_frequent)) if ok]
        red = 'Deeper than {0:g} m in the design event'.format(tdeep)
        if freq:
            red = 'Flooded (>= {0:g} m) in the {1} event, or deeper than {2:g} m in the design event'.format(
                tmin, ' or '.join(freq), tdeep)
        orange = '{0:g}-{1:g} m deep in the design event'.format(tmid, tdeep)
        if has_medium:
            orange = 'Flooded (>= {0:g} m) in the medium-probability event, or '.format(tmin) + orange
        if settled:
            orange += '; plus shallow frequent flooding inside existing settlements'
        yellow = 'Flooded only in the design event, {0:g}-{1:g} m deep'.format(tmin, tmid)
        return {ZONE_RED: red, ZONE_ORANGE: orange, ZONE_YELLOW: yellow,
                ZONE_GREEN: 'River protection buffer'}
