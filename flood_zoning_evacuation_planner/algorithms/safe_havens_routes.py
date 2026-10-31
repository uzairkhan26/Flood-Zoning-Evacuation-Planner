# -*- coding: utf-8 -*-
"""Processing algorithm: safe havens and evacuation routes."""
import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)

from ..core import gridtools as gt
from ..core.evacuation import DEFAULTS, EvacuationPlanner, build_cost, candidate_land
from .common import (
    CategoryStyler,
    SingleStyler,
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
from .compat import (
    NUMBER_DOUBLE,
    TYPE_LINE,
    TYPE_POINT,
    TYPE_POLYGON,
    WKB_MULTILINESTRING,
    WKB_MULTIPOLYGON,
    add_advanced,
    field,
    field_names,
)
from .evacuation_zones import PALETTE

MAIN_ROADS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'motorway_link', 'trunk_link',
              'primary_link', 'secondary_link', 'tertiary_link'}
PART_GAP = 100.0   # m: settlements of one zone further apart than this get separate routes
PATHS = {'path', 'footway', 'bridleway', 'steps', 'pedestrian', 'cycleway', 'corridor'}


def road_class(value):
    """1 = main road, 2 = minor road / track, 3 = footpath (OpenStreetMap 'highway' values)."""
    v = str(value or '').strip().lower()
    if v in MAIN_ROADS:
        return 1
    if v in PATHS:
        return 3
    return 2


class SafeHavensRoutesAlgorithm(QgsProcessingAlgorithm):
    ZONES = 'EVACUATION_ZONES'
    ZONE_ID = 'ZONE_ID_FIELD'
    ZONE_TYPE = 'ZONE_TYPE_FIELD'
    ZONE_NAME = 'ZONE_NAME_FIELD'
    DEPTH = 'DESIGN_DEPTH'
    PLAN_ZONES = 'PLANNING_ZONES'
    SETTLEMENTS = 'SETTLEMENTS'
    ROADS = 'ROADS'
    ROAD_FIELD = 'ROAD_CLASS_FIELD'
    RIVER = 'RIVER'
    DEM = 'DEM'
    FACILITIES = 'FACILITIES'
    FAC_NAME = 'FACILITY_NAME_FIELD'
    DOMAIN = 'MODEL_DOMAIN'
    CELL = 'CELL_SIZE'
    MAX_CELLS = 'MAX_CELLS'
    OUT_HAVENS = 'OUTPUT_HAVENS'
    OUT_ROUTES = 'OUTPUT_ROUTES'

    NUM = [  # (key, label, default, advanced)
        ('haven_factor', 'Safe haven area as a share of the settlement area', 1.0, False),
        ('min_dist_flood', 'Safe haven at least this far from flooded ground [m]', 150.0, False),
        ('min_dist_zones', 'Safe haven at least this far from the planning zones [m]', 200.0, False),
        ('freeboard', 'Safe haven at least this much higher than the nearby flood level [m]', 0.5, False),
        ('sector_ha', 'One route for every [ha] of settlement', 150.0, False),
        ('search', 'Search for a safe haven within [m]', 10000.0, False),
        ('wl_radius', 'Distance used for the "nearby flood level" [m]', 300.0, True),
        ('clearance', 'Keep safe havens this far from settlements and other havens [m]', 50.0, True),
        ('c_main', 'Cost per metre: main road', 1.0, True),
        ('c_minor', 'Cost per metre: minor road / track', 1.1, True),
        ('c_path', 'Cost per metre: footpath', 1.3, True),
        ('c_open', 'Cost per metre: open ground', 1.8, True),
        ('slope_factor', 'Open ground: extra cost per unit slope', 20.0, True),
        ('deep_depth', 'Water depth unsafe for people and vehicles [m]', 0.3, True),
        ('very_deep_depth', 'Very deep water [m]', 1.0, True),
        ('f_shallow', 'Cost multiplier: shallow water', 3.0, True),
        ('f_deep', 'Cost multiplier: unsafe water', 40.0, True),
        ('f_very_deep', 'Extra cost multiplier: very deep water', 5.0, True),
        ('walk_kmh', 'Walking speed for the travel-time estimate [km/h]', 3.0, True),
    ]

    def name(self):
        return 'safehavensroutes'

    def displayName(self):  # noqa: N802
        return tr('Safe havens and evacuation routes')

    def group(self):
        return tr('2 - Evacuation planning')

    def groupId(self):  # noqa: N802
        return 'evacuation'

    def createInstance(self):  # noqa: N802
        return SafeHavensRoutesAlgorithm()

    def helpUrl(self):  # noqa: N802
        return guide('step-4-safe-havens-and-evacuation-routes')

    def shortHelpString(self):  # noqa: N802
        return tr(
            '<p>For every evacuation zone (output of "Evacuation zones") this tool finds a <b>safe haven</b> '
            'and the <b>evacuation routes</b> leading to it.</p>'
            '<p><b>Safe havens</b> are areas of open land of about the same size as the settlement they serve. '
            'They stay dry in the design flood, are away from flooded ground and from the planning zones, are '
            'higher than the nearby flood level (if an elevation model is given), lie outside settlements, and '
            'do not overlap. Among suitable places the one that is easiest to reach is chosen, with a preference '
            'for places near roads and public facilities (schools, health centres...).</p>'
            '<p><b>Routes</b> follow the easiest path: main roads first, then tracks, footpaths and open ground '
            '(steeper slopes are harder). Water deeper than the safe depth is avoided where possible and rivers '
            'are only crossed on roads. Large settlements are split into parts, each with its own route.</p>'
            '<p>Road classes are read from an OpenStreetMap-style <i>highway</i> field if you choose one; '
            'otherwise all roads are treated as minor roads.</p>')

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(self.ZONES, tr('Evacuation zones'), [TYPE_POLYGON]))
        self.addParameter(QgsProcessingParameterField(self.ZONE_ID, tr('Zone ID field'), 'zone_id',
                                                      self.ZONES, optional=True))
        self.addParameter(QgsProcessingParameterField(self.ZONE_TYPE, tr('Settlement type field'), 'settl_type',
                                                      self.ZONES, optional=True))
        self.addParameter(QgsProcessingParameterField(self.ZONE_NAME, tr('Zone name field'), 'name',
                                                      self.ZONES, optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEPTH, tr('Flood depth - design event (e.g. 100-year) [m]')))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PLAN_ZONES, tr('Spatial planning zones (havens keep away from them)'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SETTLEMENTS, tr('All settlements (havens are placed outside them)'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(self.ROADS, tr('Roads and paths'), [TYPE_LINE], optional=True))
        self.addParameter(QgsProcessingParameterField(self.ROAD_FIELD, tr('Road class field (OpenStreetMap "highway")'),
                                                      'highway', self.ROADS, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.RIVER, tr('River channel (crossed only on roads)'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, tr('Elevation model (DEM)'), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FACILITIES, tr('Public facilities (preferred near safe havens)'), [TYPE_POINT, TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterField(self.FAC_NAME, tr('Facility name / type field'),
                                                      parentLayerParameterName=self.FACILITIES, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DOMAIN, tr('Hydraulic model domain (havens only inside)'), [TYPE_POLYGON], optional=True))
        for key, label, default, adv in self.NUM:
            p = QgsProcessingParameterNumber(key.upper(), tr(label), NUMBER_DOUBLE, default, minValue=0.0)
            self.addParameter(add_advanced(p) if adv else p)
        self.addParameter(add_advanced(QgsProcessingParameterNumber(
            self.CELL, tr('Analysis cell size [m]'), NUMBER_DOUBLE, 10.0, minValue=0.5)))
        self.addParameter(add_advanced(QgsProcessingParameterNumber(
            self.MAX_CELLS, tr('Maximum number of cells (millions)'), NUMBER_DOUBLE, 40.0, minValue=1.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_HAVENS, tr('Safe havens'), TYPE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_ROUTES, tr('Evacuation routes'), TYPE_LINE))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        zones_src = self.parameterAsSource(parameters, self.ZONES, context)
        depth_layer = self.parameterAsRasterLayer(parameters, self.DEPTH, context)
        if zones_src is None or depth_layer is None:
            raise QgsProcessingException(tr('Evacuation zones and a design flood depth raster are required.'))
        params = {k: self.parameterAsDouble(parameters, k.upper(), context) for k, _, _, _ in self.NUM}
        params['search'] = max(params['search'], 100.0)
        cell = self.parameterAsDouble(parameters, self.CELL, context)
        max_cells = self.parameterAsDouble(parameters, self.MAX_CELLS, context) * 1e6

        def fld(key, default=None):
            v = field_names(self, parameters, key, context)
            return v[0] if v else default

        grid, crs = grid_from_raster(depth_layer, cell, max_cells, context, feedback)
        rect = grid_extent(grid)
        feedback.setProgress(5)

        # rasters --------------------------------------------------------------------------
        depth = gt.warp_to_grid(raster_source(depth_layer), grid, 'average')
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        dem = gt.warp_to_grid(raster_source(dem_layer), grid, 'bilinear') if dem_layer else None
        feedback.setProgress(15)

        def mask_of(key, all_touched=False, buffer_m=0.0):
            src = self.parameterAsSource(parameters, key, context)
            if src is None:
                return None, []
            geoms = source_geometries(src, crs, context, buffer_m=buffer_m, clip_rect=rect)
            return (gt.rasterize_wkb(geometries_to_wkb(geoms), grid, all_touched) if geoms else None), geoms

        # planning zones: only codes 1-3 if the layer comes from this plugin
        pz = self.parameterAsSource(parameters, self.PLAN_ZONES, context)
        zones_mask = None
        if pz is not None:
            ct = QgsCoordinateTransform(pz.sourceCrs(), crs, context.transformContext())
            geoms = []
            has_code = 'zone_code' in pz.fields().names()
            for f in pz.getFeatures():
                if has_code and f['zone_code'] not in (1, 2, 3):
                    continue
                g = QgsGeometry(f.geometry())
                g.transform(ct)
                geoms.append(g)
            zones_mask = gt.rasterize_wkb(geometries_to_wkb(geoms), grid) if geoms else None
        settl_mask, _ = mask_of(self.SETTLEMENTS)
        river_mask, _ = mask_of(self.RIVER)
        domain_mask, _ = mask_of(self.DOMAIN)

        roads_src = self.parameterAsSource(parameters, self.ROADS, context)
        road_field = fld(self.ROAD_FIELD)
        road_classes = np.zeros(grid.shape, np.uint8)
        masks = {1: None, 2: None, 3: None}
        if roads_src is not None:
            ct = QgsCoordinateTransform(roads_src.sourceCrs(), crs, context.transformContext())
            by_class = {1: [], 2: [], 3: []}
            use_field = road_field if road_field in roads_src.fields().names() else None
            for f in roads_src.getFeatures():
                g = QgsGeometry(f.geometry())
                if g.isEmpty():
                    continue
                g.transform(ct)
                if not g.boundingBox().intersects(rect):
                    continue
                by_class[road_class(f[use_field]) if use_field else 2].append(g)
            for k in (3, 2, 1):   # main roads drawn last so they win
                if by_class[k]:
                    masks[k] = gt.rasterize_wkb(geometries_to_wkb(by_class[k]), grid, all_touched=True)
                    road_classes[masks[k]] = k
        feedback.setProgress(30)

        fac_src = self.parameterAsSource(parameters, self.FACILITIES, context)
        fac_geoms = source_geometries(fac_src, crs, context, clip_rect=rect) if fac_src is not None else []
        fac_near = None
        if fac_geoms:
            fac_mask = gt.rasterize_wkb(geometries_to_wkb(fac_geoms), grid, all_touched=True)
            fac_near = gt.distance_to(fac_mask, grid, DEFAULTS['facility_dist']) <= DEFAULTS['facility_dist']

        # cost surface and suitable land ---------------------------------------------------
        cost = build_cost(grid.shape, params, main=masks[1], minor=masks[2], path=masks[3], dem=dem,
                          depth=depth, river=river_mask, res=grid.res)
        cand, height_known = candidate_land(grid, params, depth, gt.distance_to, zones=zones_mask,
                                            settlements=settl_mask, domain=domain_mask, dem=dem)
        if river_mask is not None and river_mask.any():   # rivers left as no-data in the depth raster
            dmin = params['min_dist_flood']
            cand &= gt.distance_to(river_mask, grid, dmin) >= dmin
        a = grid.cell_area / 1e4
        feedback.pushInfo(tr('Land suitable for safe havens: {0:,.0f} ha.').format(cand.sum() * a))
        if not cand.any():
            raise QgsProcessingException(tr('No land meets the safe-haven conditions. Relax the distances or '
                                            'the freeboard, or check the inputs.'))
        dist_wet = gt.distance_to(np.nan_to_num(depth, nan=0.0) >= DEFAULTS['wet_depth'], grid)
        feedback.setProgress(40)

        # evacuation zones -----------------------------------------------------------------
        zid_f, ztype_f, zname_f = fld(self.ZONE_ID), fld(self.ZONE_TYPE), fld(self.ZONE_NAME)
        names = zones_src.fields().names()
        ct = QgsCoordinateTransform(zones_src.sourceCrs(), crs, context.transformContext())
        zone_list, wkb_vals, zone_geoms = [], [], []
        for i, f in enumerate(zones_src.getFeatures(), 1):
            g = QgsGeometry(f.geometry())
            if g.isEmpty():
                continue
            g.transform(ct)
            zone_list.append(dict(
                idx=i, id=str(f[zid_f]) if zid_f in names and f[zid_f] not in (None, '') else 'EZ-%02d' % i,
                type=str(f[ztype_f]) if ztype_f in names and f[ztype_f] not in (None, '') else 'Settlement',
                name=str(f[zname_f]) if zname_f in names and f[zname_f] not in (None, '') else '',
                area_m2=g.area()))
            wkb_vals.append((bytes(g.asWkb().data()), i))
            zone_geoms.append(g)
        labels = gt.rasterize_values(wkb_vals, grid)
        order = np.argsort(labels, axis=None, kind='stable')
        flat = labels.ravel()[order]
        starts = np.searchsorted(flat, [z['idx'] for z in zone_list], 'left')
        ends = np.searchsorted(flat, [z['idx'] for z in zone_list], 'right')
        for z, s, e in zip(zone_list, starts, ends):
            z['rows'], z['cols'] = np.unravel_index(order[s:e], grid.shape)
        del labels, order, flat
        # separate settlements in a zone (more than PART_GAP apart) each get their own route
        for z, g in zip(zone_list, zone_geoms):
            blobs = g.buffer(PART_GAP / 2.0, 4)
            parts = blobs.asGeometryCollection() if blobs.isMultipart() else [blobs]
            if len(parts) < 2 or len(z['rows']) == 0:
                continue
            r0, c0 = int(z['rows'].min()), int(z['cols'].min())
            sub = grid.sub(r0, c0, int(z['rows'].max()) - r0 + 1, int(z['cols'].max()) - c0 + 1)
            ids = gt.rasterize_values([(bytes(p.asWkb().data()), k) for k, p in enumerate(parts, 1)], sub)
            pid = ids[z['rows'] - r0, z['cols'] - c0]
            pid[pid == 0] = 1
            z['parts'] = pid
        feedback.setProgress(45)

        # plan -------------------------------------------------------------------------------
        planner = EvacuationPlanner(grid, cost, cand, gt.distance_to, params,
                                    roads=road_classes > 0, facilities_near=fac_near, depth=depth, dem=dem,
                                    road_classes=road_classes, feedback=feedback,
                                    size_filter=lambda m, g, n: m & (gt.sieve(m, g, n) == 1))
        havens, routes = planner.plan(zone_list)
        feedback.setProgress(85)

        # outputs ------------------------------------------------------------------------------
        hf = QgsFields()
        for spec in (('haven_id', 'string', 16), ('zone_id', 'string', 12), ('settl_type', 'string', 60),
                     ('zone_name', 'string', 120), ('zone_ha', 'double', 12, 2), ('haven_ha', 'double', 12, 2),
                     ('ratio', 'double', 6, 2), ('elev_min', 'double', 8, 1), ('elev_max', 'double', 8, 1),
                     ('dist_flood', 'double', 8, 0), ('height_chk', 'string', 12), ('n_routes', 'int'),
                     ('facility', 'string', 120)):
            hf.append(field(*spec))
        hsink, hid = self.parameterAsSink(parameters, self.OUT_HAVENS, context, hf, WKB_MULTIPOLYGON, crs)
        pieces = gt.polygonize(planner.haven_label, grid)
        fac_name_f = fld(self.FAC_NAME)
        fac_feats = []
        if fac_src is not None:
            fct = QgsCoordinateTransform(fac_src.sourceCrs(), crs, context.transformContext())
            for f in fac_src.getFeatures():
                g = QgsGeometry(f.geometry())
                g.transform(fct)
                label = str(f[fac_name_f]) if fac_name_f and f[fac_name_f] not in (None, '') else tr('facility')
                fac_feats.append((g, label))
        for h in havens:
            z = h['zone']
            geom = wkb_to_geometry(pieces.get(h['label'], []))
            zc = dem[h['rows'], h['cols']] if dem is not None else np.array([])
            zc = zc[np.isfinite(zc)]
            known = height_known[h['rows'], h['cols']].mean() if dem is not None else 0.0
            chk = 'no DEM' if dem is None else ('yes' if known > 0.99 else ('partial' if known > 0 else 'no DEM data'))
            fac_txt = ''
            if fac_feats:
                best = min(((g.distance(geom), lab) for g, lab in fac_feats), default=None)
                if best and best[0] <= 500:
                    fac_txt = ('%s (inside the haven)' % best[1] if best[0] < 1
                               else '%s (%d m)' % (best[1], int(round(best[0]))))
            geom.convertToMultiType()
            f = QgsFeature(hf)
            f.setGeometry(geom)
            f.setAttributes(['SH-' + z['id'], z['id'], z['type'], z['name'], round(z['area_m2'] / 1e4, 2),
                             round(h['area_m2'] / 1e4, 2), round(h['area_m2'] / max(z['area_m2'], 1), 2),
                             round(float(zc.min()), 1) if zc.size else None,
                             round(float(zc.max()), 1) if zc.size else None,
                             round(float(dist_wet[h['rows'], h['cols']].min()), 0), chk, h['n_routes'], fac_txt])
            hsink.addFeature(f, QgsFeatureSink.FastInsert)

        rf = QgsFields()
        for spec in (('route_id', 'string', 20), ('zone_id', 'string', 12), ('sector', 'int'),
                     ('sector_ha', 'double', 12, 2), ('settl_type', 'string', 60), ('zone_name', 'string', 120),
                     ('haven_id', 'string', 16), ('length_m', 'double', 10, 0), ('walk_min', 'double', 8, 0),
                     ('main_pct', 'double', 5, 0), ('minor_pct', 'double', 5, 0), ('path_pct', 'double', 5, 0),
                     ('open_pct', 'double', 5, 0), ('wet_m', 'double', 8, 0), ('z_start', 'double', 8, 1),
                     ('z_end', 'double', 8, 1)):
            rf.append(field(*spec))
        rsink, rid = self.parameterAsSink(parameters, self.OUT_ROUTES, context, rf, WKB_MULTILINESTRING, crs)
        for r in routes:
            z = r['zone']
            xs, ys = grid.cell_xy(r['rows'], r['cols'])
            line = QgsGeometry.fromPolylineXY([QgsPointXY(float(x), float(y)) for x, y in zip(xs, ys)])
            if len(xs) > 3:
                line = line.simplify(grid.res * 0.75).smooth(2)
            line.convertToMultiType()
            sh = r.get('shares', {})
            f = QgsFeature(rf)
            f.setGeometry(line)
            f.setAttributes(['%s-R%d' % (z['id'], r['sector']), z['id'], r['sector'], round(r['sector_ha'], 2),
                             z['type'], z['name'], 'SH-' + z['id'], round(r['length_m']), round(r['walk_min']),
                             round(sh.get('main', 0)), round(sh.get('minor', 0)), round(sh.get('path', 0)),
                             round(sh.get('open', 100 if not sh else 0)), round(r['wet_m']),
                             None if r.get('z_start') is None else round(r['z_start'], 1),
                             None if r.get('z_end') is None else round(r['z_end'], 1)])
            rsink.addFeature(f, QgsFeatureSink.FastInsert)
        feedback.pushInfo(tr('{0} safe havens and {1} routes for {2} evacuation zones.').format(
            len(havens), len(routes), len(zone_list)))
        if len(havens) < len(zone_list):
            feedback.pushWarning(tr('{0} zone(s) got no safe haven - see the log above.').format(
                len(zone_list) - len(havens)))

        set_styler(context, hid, SingleStyler.create('0,176,80,170', '#006020'))
        types = sorted({z['type'] for z in zone_list})
        set_styler(context, rid, CategoryStyler.create(
            'settl_type', [(t, t, '', PALETTE[i % len(PALETTE)], 1.0) for i, t in enumerate(types)], geometry='line'))
        feedback.setProgress(100)
        return {self.OUT_HAVENS: hid, self.OUT_ROUTES: rid}
