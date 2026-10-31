# -*- coding: utf-8 -*-
"""Processing algorithm: evacuation zones (whole settlements at flood risk, grouped)."""
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsSpatialIndex,
)

from .compat import NUMBER_DOUBLE, TYPE_ANY_GEOMETRY, TYPE_POLYGON, WKB_MULTIPOLYGON, field, field_names
from .common import CategoryStyler, set_styler, source_geometries, tr, working_crs

PALETTE = ['#000000', '#0046c8', '#7b2d8e', '#b15928', '#1b9e77', '#d95f02', '#e7298a', '#666666']


def rgba(hex_colour, alpha):
    """'#rrggbb' -> 'r,g,b,a' (QGIS colour string with transparency)."""
    h = hex_colour.lstrip('#')
    return '%d,%d,%d,%d' % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def type_code(name, used):
    """Short code from a settlement type: initials of the first two words (e.g. 'Refugee camp' -> 'RC')."""
    words = [w for w in ''.join(ch if ch.isalnum() else ' ' for ch in name).split() if w]
    code = ''.join(w[0] for w in words[:2]).upper() or 'EZ'
    base, i = code, 2
    while code in used and used[code] != name:
        code = '%s%d' % (base, i)
        i += 1
    used[code] = name
    return code


class EvacuationZonesAlgorithm(QgsProcessingAlgorithm):
    SETTLEMENTS = 'SETTLEMENTS'
    TYPE_FIELD = 'TYPE_FIELD'
    NAME_FIELD = 'NAME_FIELD'
    RISK = 'RISK_AREAS'
    DISTANCE = 'DISTANCE'
    GROUP = 'GROUP_DISTANCE'
    WHOLE = 'WHOLE_SETTLEMENT'
    BARRIER = 'BARRIERS'
    OUTPUT = 'OUTPUT'

    def name(self):
        return 'evacuationzones'

    def displayName(self):  # noqa: N802
        return tr('Evacuation zones')

    def group(self):
        return tr('2 - Evacuation planning')

    def groupId(self):  # noqa: N802
        return 'evacuation'

    def createInstance(self):  # noqa: N802
        return EvacuationZonesAlgorithm()

    def shortHelpString(self):  # noqa: N802
        return tr(
            '<p>Finds the settlements that need to be evacuated and groups them into evacuation zones.</p>'
            '<p>A settlement is <b>at risk</b> if any part of it lies within the given distance of a risk '
            'area (for example the red, orange and yellow planning zones, or a flood extent). By default the '
            '<b>whole settlement</b> is evacuated, because if part of it floods the rest is usually not '
            'safe to live in either.</p>'
            '<p>Settlements of the same type (e.g. town, village, camp) that are closer than the grouping '
            'distance form one evacuation zone. Each zone gets a code made of the type initials and a number '
            '(e.g. RC-03 for a refugee camp), its settlement names, its area and the area actually at risk.</p>'
            '<p>Give the river (lines or the river channel polygon) as a <b>barrier</b> so that settlements on '
            'opposite banks are never put in the same zone: they would need different safe havens.</p>'
            '<p>If the risk layer has a <i>zone_code</i> field (output of "Spatial planning zones"), only '
            'zones 1-3 are used; the green river protection zone is ignored.</p>')

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SETTLEMENTS, tr('Settlements (polygons)'), [TYPE_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            self.TYPE_FIELD, tr('Settlement type field (e.g. town / village / camp)'),
            parentLayerParameterName=self.SETTLEMENTS, optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.NAME_FIELD, tr('Settlement name field'), parentLayerParameterName=self.SETTLEMENTS, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.RISK, tr('Risk areas (planning zones or flood extent)'), [TYPE_POLYGON]))
        self.addParameter(QgsProcessingParameterNumber(
            self.DISTANCE, tr('Settlement is at risk if within this distance of a risk area [m]'),
            NUMBER_DOUBLE, 100.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.GROUP, tr('Group settlements of the same type closer than [m]'), NUMBER_DOUBLE, 1000.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.BARRIER, tr('Do not group settlements across (e.g. the river)'), [TYPE_ANY_GEOMETRY], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.WHOLE, tr('Evacuate the whole settlement (not only the part at risk)'), True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, tr('Evacuation zones'), TYPE_POLYGON))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        settl = self.parameterAsSource(parameters, self.SETTLEMENTS, context)
        risk = self.parameterAsSource(parameters, self.RISK, context)
        tfield = (field_names(self, parameters, self.TYPE_FIELD, context) or [None])[0]
        nfield = (field_names(self, parameters, self.NAME_FIELD, context) or [None])[0]
        dist = self.parameterAsDouble(parameters, self.DISTANCE, context)
        group_d = self.parameterAsDouble(parameters, self.GROUP, context)
        whole = self.parameterAsBool(parameters, self.WHOLE, context)

        crs = working_crs(settl.sourceCrs(), settl.sourceExtent(), context, feedback)

        # risk areas ----------------------------------------------------------
        if 'zone_code' in risk.fields().names():
            req = QgsFeatureRequest().setFilterExpression('"zone_code" IN (1, 2, 3)')
            risk_geoms = []
            ct = QgsCoordinateTransform(risk.sourceCrs(), crs, context.transformContext())
            for f in risk.getFeatures(req):
                g = QgsGeometry(f.geometry())
                g.transform(ct)
                risk_geoms.append(g)
        else:
            risk_geoms = source_geometries(risk, crs, context)
        if not risk_geoms:
            raise QgsProcessingException(tr('The risk layer has no features.'))
        risk_area = QgsGeometry.unaryUnion(risk_geoms)
        risk_zone = risk_area.buffer(dist, 8) if dist > 0 else risk_area
        engine = QgsGeometry.createGeometryEngine(risk_zone.constGet())
        engine.prepareGeometry()
        feedback.setProgress(20)

        # settlements at risk -------------------------------------------------------
        ct = QgsCoordinateTransform(settl.sourceCrs(), crs, context.transformContext())
        at_risk = {}   # type -> list of (geom, name, risk_geom)
        total = settl.featureCount() or 1
        for i, f in enumerate(settl.getFeatures()):
            if feedback.isCanceled():
                break
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            g.transform(ct)
            if not g.isGeosValid():
                g = g.makeValid()
            if not engine.intersects(g.constGet()):
                continue
            t = str(f[tfield]) if tfield and f[tfield] not in (None, '') else 'Settlement'
            nm = str(f[nfield]).strip() if nfield and f[nfield] not in (None, '') else ''
            at_risk.setdefault(t, []).append((g, nm, g.intersection(risk_zone)))
            feedback.setProgress(20 + 50 * i / total)
        n_at_risk = sum(len(v) for v in at_risk.values())
        feedback.pushInfo(tr('{0} settlements are at risk.').format(n_at_risk))
        if not n_at_risk:
            feedback.pushWarning(tr('No settlement is within the given distance of a risk area.'))

        # group into zones ----------------------------------------------------------
        bengine = None
        bsrc = self.parameterAsSource(parameters, self.BARRIER, context)
        if bsrc is not None:
            bgeoms = source_geometries(bsrc, crs, context)
            if bgeoms:
                barrier = QgsGeometry.unaryUnion(bgeoms)
                bengine = QgsGeometry.createGeometryEngine(barrier.constGet())
                bengine.prepareGeometry()
        zones = []
        for t, items in at_risk.items():
            for members in self._groups(items, group_d, bengine):
                geom = QgsGeometry.unaryUnion([m[0] if whole else m[2] for m in members])
                riskg = QgsGeometry.unaryUnion([m[2] for m in members])
                names = sorted({m[1] for m in members if m[1]})
                c = geom.centroid().asPoint()
                zones.append(dict(type=t, geom=geom, risk=riskg, names=names, n=len(members), x=c.x(), y=c.y()))

        # ids, names and output --------------------------------------------------------
        used = {}
        codes = {t: type_code(t, used) for t in sorted(at_risk)}
        zones.sort(key=lambda z: (sorted(at_risk).index(z['type']), z['x']))
        fields = QgsFields()
        for spec in (('zone_id', 'string', 12), ('settl_type', 'string', 60), ('name', 'string', 120),
                     ('n_settl', 'int'), ('area_ha', 'double', 14, 2), ('risk_ha', 'double', 14, 2)):
            fields.append(field(*spec))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_MULTIPOLYGON, crs)
        for i, z in enumerate(zones, 1):
            zid = '%s-%02d' % (codes[z['type']], i)
            if z['names']:
                name = ', '.join(z['names'])
                if len(name) > 118:
                    name = name[:115] + '...'
            else:
                name = '%s near E%.1f N%.1f km' % (z['type'], z['x'] / 1000.0, z['y'] / 1000.0)
            g = z['geom']
            g.convertToMultiType()
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes([zid, z['type'], name, z['n'], round(g.area() / 1e4, 2), round(z['risk'].area() / 1e4, 2)])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        feedback.pushInfo(tr('{0} evacuation zones.').format(len(zones)))

        cats = [(t, t, rgba(PALETTE[i % len(PALETTE)], 60), PALETTE[i % len(PALETTE)], 1.0)
                for i, t in enumerate(sorted(at_risk))]
        if cats:
            set_styler(context, dest, CategoryStyler.create('settl_type', cats))
        return {self.OUTPUT: dest}

    @staticmethod
    def _groups(items, group_d, barrier_engine=None):
        """Settlements closer than ``group_d`` form one group, unless the shortest line between
        them crosses the barrier. Returns lists of items."""
        n = len(items)
        parent = list(range(n))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        index = QgsSpatialIndex()
        for i, (g, _, _) in enumerate(items):
            index.addFeature(i, g.boundingBox())
        for i, (g, _, _) in enumerate(items):
            box = g.boundingBox()
            box.grow(group_d)
            for j in index.intersects(box):
                if j <= i or root(i) == root(j):
                    continue
                gj = items[j][0]
                if g.distance(gj) > group_d:
                    continue
                if barrier_engine is not None and not g.intersects(gj):
                    line = g.shortestLine(gj)
                    if barrier_engine.intersects(line.constGet()):
                        continue
                parent[root(j)] = root(i)
        groups = {}
        for i in range(n):
            groups.setdefault(root(i), []).append(items[i])
        return list(groups.values())
