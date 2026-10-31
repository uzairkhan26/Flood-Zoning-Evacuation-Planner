# -*- coding: utf-8 -*-
"""Small compatibility layer for QGIS 3.34 ... QGIS 4 (Qt5 / Qt6).

Several enums moved to the ``Qgis`` namespace in QGIS 3.30-3.38 and the old
names are removed in QGIS 4. This module picks whichever exists.
"""
from qgis.core import Qgis, QgsField, QgsProcessing, QgsProcessingParameterDefinition, QgsProcessingParameterNumber


def _pick(*getters):
    for g in getters:
        try:
            return g()
        except AttributeError:
            continue
    raise AttributeError('No compatible QGIS enum found')


NUMBER_DOUBLE = _pick(lambda: Qgis.ProcessingNumberParameterType.Double,
                      lambda: QgsProcessingParameterNumber.Double)
NUMBER_INTEGER = _pick(lambda: Qgis.ProcessingNumberParameterType.Integer,
                       lambda: QgsProcessingParameterNumber.Integer)
FLAG_ADVANCED = _pick(lambda: Qgis.ProcessingParameterFlag.Advanced,
                      lambda: QgsProcessingParameterDefinition.FlagAdvanced)
TYPE_POLYGON = _pick(lambda: Qgis.ProcessingSourceType.VectorPolygon,
                     lambda: QgsProcessing.TypeVectorPolygon)
TYPE_LINE = _pick(lambda: Qgis.ProcessingSourceType.VectorLine,
                  lambda: QgsProcessing.TypeVectorLine)
TYPE_POINT = _pick(lambda: Qgis.ProcessingSourceType.VectorPoint,
                   lambda: QgsProcessing.TypeVectorPoint)
TYPE_ANY_GEOMETRY = _pick(lambda: Qgis.ProcessingSourceType.VectorAnyGeometry,
                          lambda: QgsProcessing.TypeVectorAnyGeometry)


def _wkb(name):
    return _pick(lambda: getattr(Qgis.WkbType, name),
                 lambda: getattr(__import__('qgis.core', fromlist=['QgsWkbTypes']).QgsWkbTypes, name))


WKB_MULTIPOLYGON = _wkb('MultiPolygon')
WKB_MULTILINESTRING = _wkb('MultiLineString')
WKB_POINT = _wkb('Point')


def add_advanced(param):
    param.setFlags(param.flags() | FLAG_ADVANCED)
    return param


# ---- attribute fields --------------------------------------------------------
if Qgis.QGIS_VERSION_INT >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    _T = {'int': QMetaType.Type.Int, 'double': QMetaType.Type.Double, 'string': QMetaType.Type.QString}
else:  # pragma: no cover - QGIS 3.34/3.36
    from qgis.PyQt.QtCore import QVariant
    _T = {'int': QVariant.Int, 'double': QVariant.Double, 'string': QVariant.String}


def field(name, kind, length=0, prec=0):
    """QgsField that works on Qt5 and Qt6 builds. kind: 'int' | 'double' | 'string'."""
    return QgsField(name, _T[kind], len=length, prec=prec)


def field_names(alg, parameters, key, context):
    """Value of a field parameter as a list of names (parameterAsFields is deprecated in QGIS 3.40+)."""
    if hasattr(alg, 'parameterAsStrings'):
        return alg.parameterAsStrings(parameters, key, context) or []
    return alg.parameterAsFields(parameters, key, context) or []
