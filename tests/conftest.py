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


# --------------------------------------------------------------------------
# On GitHub Actions, report failures as annotations (visible on the run page).
# --------------------------------------------------------------------------
def _annotate(kind, title, text):
    if not os.environ.get('GITHUB_ACTIONS'):
        return
    msg = text[-3500:].replace('%', '%25').replace('\r', '').replace('\n', '%0A')
    print('::%s title=%s::%s' % (kind, title.replace(':', ' ').replace(',', ' '), msg), flush=True)


def pytest_runtest_logreport(report):
    if report.failed:
        _annotate('error', report.nodeid, str(report.longrepr))


def pytest_collectreport(report):
    if report.failed:
        _annotate('error', 'collection ' + str(report.nodeid), str(report.longrepr))


def pytest_sessionstart(session):
    try:
        from qgis.core import Qgis
        from osgeo import gdal
        _annotate('notice', 'versions', 'QGIS %s, GDAL %s' % (Qgis.QGIS_VERSION, gdal.__version__))
    except Exception as e:  # pragma: no cover
        _annotate('notice', 'versions', 'QGIS not importable: %r' % (e,))
