# -*- coding: utf-8 -*-
"""Plugin entry point: registers the Processing provider."""
from qgis.core import QgsApplication

from .provider import FloodZoningProvider


class FloodZoningEvacuationPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):  # noqa: N802
        self.provider = FloodZoningProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
