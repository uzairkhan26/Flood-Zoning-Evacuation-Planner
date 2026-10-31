# -*- coding: utf-8 -*-
"""Flood Zoning & Evacuation Planner - QGIS plugin.

Copyright (C) 2026 Muhammad Uzair Khan
Licensed under the GNU General Public License v2 or (at your option) any later version.
"""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import FloodZoningEvacuationPlugin
    return FloodZoningEvacuationPlugin(iface)
