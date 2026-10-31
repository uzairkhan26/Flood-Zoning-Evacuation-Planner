# -*- coding: utf-8 -*-
"""Processing algorithm: report tables (Word or HTML) from the outputs of the other tools."""
import os

from qgis.core import (
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
)

from ..core.report import build_report
from .common import guide, tr, working_crs
from .compat import TYPE_LINE, TYPE_POLYGON


def _val(v):
    """Attribute value as plain Python (NULL -> None)."""
    if v is None:
        return None
    if type(v).__name__ == 'QVariant':
        return None if v.isNull() else v.value()
    return v


class ReportTablesAlgorithm(QgsProcessingAlgorithm):
    ZONES = 'PLANNING_ZONES'
    COMPARE = 'COMPARE_ZONES'
    SETTLEMENTS = 'SETTLEMENTS'
    EVAC = 'EVACUATION_ZONES'
    HAVENS = 'SAFE_HAVENS'
    ROUTES = 'ROUTES'
    TITLE = 'TITLE'
    OUTPUT = 'OUTPUT'

    def name(self):
        return 'reporttables'

    def displayName(self):  # noqa: N802
        return tr('Report tables')

    def group(self):
        return tr('3 - Reporting')

    def groupId(self):  # noqa: N802
        return 'reporting'

    def createInstance(self):  # noqa: N802
        return ReportTablesAlgorithm()

    def helpUrl(self):  # noqa: N802
        return guide('step-5-report-tables')

    def shortHelpString(self):  # noqa: N802
        return tr(
            '<p>Writes the results of the other tools as ready-to-use tables in a <b>Word document</b> '
            '(.docx) or a <b>web page</b> (.html), to copy into your report.</p>'
            '<p>Give it any of the layers made by the plugin; only the tables with data are written:</p>'
            '<ul><li><b>Planning zones</b>: area of each zone, the flood rule used and what it means for '
            'planning. Add <i>settlements</i> to see how much built-up area lies in each zone.</li>'
            '<li><b>Planning zones of a second scenario</b> (e.g. current condition vs. after mitigation): '
            'a comparison table with the change in area.</li>'
            '<li><b>Evacuation zones</b>: settlements, area and area at risk of each zone.</li>'
            '<li><b>Safe havens</b>: size compared with the zone, distance to the flood, ground level.</li>'
            '<li><b>Evacuation routes</b>: a summary per zone and a detailed table per route (length, '
            'walking time, share on roads / paths / open ground, length through flood water).</li></ul>'
            '<p>The report starts with the key figures in a few sentences, every table has a short '
            '"How to read" note, and the terms used are explained at the end.</p>')

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterString(
            self.TITLE, tr('Report title'), 'Flood zoning and evacuation planning', optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.ZONES, tr('Planning zones (output of "Spatial planning zones")'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.COMPARE, tr('Planning zones of a second scenario, to compare with'), [TYPE_POLYGON],
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SETTLEMENTS, tr('Settlements (built-up area in each planning zone)'), [TYPE_POLYGON],
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.EVAC, tr('Evacuation zones'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.HAVENS, tr('Safe havens'), [TYPE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.ROUTES, tr('Evacuation routes'), [TYPE_LINE], optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, tr('Report'), tr('Word document (*.docx);;Web page (*.html)')))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        src = {k: self.parameterAsSource(parameters, k, context)
               for k in (self.ZONES, self.COMPARE, self.SETTLEMENTS, self.EVAC, self.HAVENS, self.ROUTES)}
        if not any(src[k] for k in (self.ZONES, self.COMPARE, self.EVAC, self.HAVENS, self.ROUTES)):
            raise QgsProcessingException(tr('Give at least one layer: planning zones, evacuation zones, '
                                            'safe havens or evacuation routes.'))
        first = next(s for s in src.values() if s is not None)
        crs = working_crs(first.sourceCrs(), first.sourceExtent(), context, feedback)
        title = self.parameterAsString(parameters, self.TITLE, context)
        path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if not path.lower().endswith(('.docx', '.html', '.htm')):
            path += '.docx'

        settled = None
        if src[self.SETTLEMENTS] is not None and (src[self.ZONES] or src[self.COMPARE]):
            geoms = [g for g, _ in self._features(src[self.SETTLEMENTS], crs, context)]
            settled = QgsGeometry.unaryUnion(geoms) if geoms else None
        feedback.setProgress(10)

        zones = self._zones(parameters, self.ZONES, src[self.ZONES], crs, context, settled, feedback, 'Scenario 1')
        compare = self._zones(parameters, self.COMPARE, src[self.COMPARE], crs, context, settled, feedback,
                              'Scenario 2')
        if compare and not zones:
            zones, compare = compare, None
        feedback.setProgress(40)

        evac = self._rows(src[self.EVAC], crs, context,
                          ['zone_id', 'settl_type', 'name', 'n_settl', 'area_ha', 'risk_ha'], area='area_ha')
        havens = self._rows(src[self.HAVENS], crs, context,
                            ['haven_id', 'zone_id', 'zone_ha', 'haven_ha', 'ratio', 'dist_flood', 'elev_min',
                             'elev_max', 'height_chk', 'facility'], area='haven_ha')
        routes = self._rows(src[self.ROUTES], crs, context,
                            ['route_id', 'zone_id', 'sector', 'settl_type', 'haven_id', 'length_m', 'walk_min',
                             'main_pct', 'minor_pct', 'path_pct', 'open_pct', 'wet_m'], length='length_m')
        for name, rows, key in (('evacuation zones', evac, 'zone_id'), ('safe havens', havens, 'haven_id'),
                                ('evacuation routes', routes, 'route_id')):
            if rows and all(r.get(key) is None for r in rows):
                feedback.pushWarning(tr('The {0} layer has no "{1}" field: is it an output of this plugin? '
                                        'Its table may be incomplete.').format(name, key))
        feedback.setProgress(80)

        rep = build_report(title, zones, compare, evac, havens, routes)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        rep.write(path)
        feedback.pushInfo(tr('{0} tables written to {1}').format(rep.n_tables(), path))
        return {self.OUTPUT: path}

    # ------------------------------------------------------------------
    @staticmethod
    def _features(source, crs, context):
        """(geometry in the working CRS, feature) for every feature with a geometry."""
        ct = None
        if source.sourceCrs() != crs:
            ct = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
        out = []
        for f in source.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            if ct is not None:
                g.transform(ct)
            if not g.isGeosValid():
                g = g.makeValid()
            out.append((g, f))
        return out

    def _rows(self, source, crs, context, names, area=None, length=None):
        """Attribute rows (dicts) of a layer; missing fields are None. Area / length are
        measured from the geometry when the field is missing."""
        if source is None:
            return None
        have = set(source.fields().names())
        rows = []
        for g, f in self._features(source, crs, context):
            r = {n: (_val(f[n]) if n in have else None) for n in names}
            if area and r.get(area) is None:
                r[area] = g.area() / 1e4
            if length and r.get(length) is None:
                r[length] = g.length()
            rows.append(r)
        return rows

    def _zones(self, parameters, key, source, crs, context, settled, feedback, default_label):
        if source is None:
            return None
        if 'zone_code' not in source.fields().names():
            feedback.pushWarning(tr('The planning zones layer has no "zone_code" field (it should be an output '
                                    'of "Spatial planning zones"); its table is skipped.'))
            return None
        have = set(source.fields().names())
        items, labels = [], set()
        for g, f in self._features(source, crs, context):
            it = {n: (_val(f[n]) if n in have else None)
                  for n in ('zone_code', 'zone', 'colour', 'hex', 'criteria', 'rule', 'scenario')}
            if it['zone_code'] is None:
                continue
            it['code'] = int(it['zone_code'])
            it['area_ha'] = g.area() / 1e4
            if settled is not None:
                it['settled_ha'] = g.intersection(settled).area() / 1e4 if g.intersects(settled) else 0.0
            if it.get('scenario'):
                labels.add(str(it['scenario']))
            items.append(it)
        label = ', '.join(sorted(labels))
        if not label:
            try:
                layer = self.parameterAsVectorLayer(parameters, key, context)
                label = layer.name() if layer is not None else ''
            except Exception:  # noqa: BLE001 - a missing name is not an error
                label = ''
        return dict(label=label or default_label, items=items)
