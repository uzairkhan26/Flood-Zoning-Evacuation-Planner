# -*- coding: utf-8 -*-
"""Test setup. QGIS-dependent tests are skipped when QGIS is not installed."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from qgis.core import QgsApplication  # noqa: F401
    HAS_QGIS = True
except ImportError:  # pragma: no cover
    HAS_QGIS = False

_APP = None


@pytest.fixture(scope='session')
def qgis_processing():
    """Start a headless QGIS with Processing and this plugin's provider."""
    global _APP
    if not HAS_QGIS:
        pytest.skip('QGIS is not available')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from qgis.core import QgsApplication
    if _APP is None:
        _APP = QgsApplication([], False)
        _APP.initQgis()
        for p in ('/usr/share/qgis/python/plugins', '/usr/lib/qgis/python/plugins'):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.append(p)
        from processing.core.Processing import Processing
        Processing.initialize()
        from flood_zoning_evacuation_planner.provider import FloodZoningProvider
        QgsApplication.processingRegistry().addProvider(FloodZoningProvider())
    import processing
    return processing
