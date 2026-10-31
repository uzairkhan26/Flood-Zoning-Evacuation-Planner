# -*- coding: utf-8 -*-
"""Processing provider for the Flood Zoning & Evacuation Planner tools."""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.river_corridor import RiverCorridorAlgorithm
from .algorithms.planning_zones import PlanningZonesAlgorithm

PLUGIN_DIR = os.path.dirname(__file__)


class FloodZoningProvider(QgsProcessingProvider):
    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(RiverCorridorAlgorithm())
        self.addAlgorithm(PlanningZonesAlgorithm())

    def id(self):
        return 'floodzoning'

    def name(self):
        return 'Flood Zoning & Evacuation Planner'

    def longName(self):  # noqa: N802
        return self.name()

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, 'icons', 'icon.png'))
