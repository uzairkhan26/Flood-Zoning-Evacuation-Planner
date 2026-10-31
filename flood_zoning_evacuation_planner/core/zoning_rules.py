# -*- coding: utf-8 -*-
"""Flood hazard and planning-zone rules (pure NumPy, no QGIS/GDAL dependency).

The rules follow a probability x depth logic:

* HIGH hazard   - flooded (depth >= ``min_depth``) in the very frequent or the
                  frequent event, or deeper than ``deep_depth`` in the design
                  (low-probability) event.
* MEDIUM hazard - flooded in the medium-probability event, or between
                  ``medium_depth`` and ``deep_depth`` in the design event.
* LOW hazard    - flooded only in the design event, shallower than
                  ``medium_depth``.

Planning zones:

* 1 = No development zone (red)          <- HIGH hazard
* 2 = Restricted development zone (orange) <- MEDIUM hazard (+ settlement rule)
* 3 = Conditional development zone (yellow) <- LOW hazard
* 4 = Protection zone (green)             <- river protection buffer

Settlement rule (optional): inside existing settlements, cells that are HIGH
hazard only because of shallow flooding in the frequent event - i.e. not
flooded in the very frequent event and shallower than ``deep_depth`` in the
design event - are placed in the Restricted zone instead of No development.
"""
import numpy as np

ZONE_NONE = 0
ZONE_RED = 1
ZONE_ORANGE = 2
ZONE_YELLOW = 3
ZONE_GREEN = 4

ZONES = {
    ZONE_RED: dict(
        name='No development zone', colour='Red', hex='#e31a1c',
        rule='No new buildings or infrastructure; no growth of existing settlements; '
             'relocate or protect critical facilities; keep as floodplain or farmland.'),
    ZONE_ORANGE: dict(
        name='Restricted development zone', colour='Orange', hex='#ff7f00',
        rule='No new critical facilities (health, schools, shelters, camps); only '
             'flood-proofed repair of existing buildings; raised floors.'),
    ZONE_YELLOW: dict(
        name='Conditional development zone', colour='Yellow', hex='#ffd700',
        rule='Building allowed if floors are above the design flood level plus a '
             'safety margin, with drainage and a dry access route.'),
    ZONE_GREEN: dict(
        name='Protection zone', colour='Green', hex='#33a02c',
        rule='River protection strip: no buildings, fences or obstructions; keep '
             'vegetation and maintenance access.'),
}


def _wet(depth, threshold):
    """Boolean mask of cells with depth >= threshold (NaN and None are dry)."""
    if depth is None:
        return None
    with np.errstate(invalid='ignore'):
        return np.nan_to_num(depth, nan=0.0) >= threshold


def hazard_classes(design, medium=None, frequent=None, very_frequent=None,
                   min_depth=0.3, medium_depth=0.5, deep_depth=1.0):
    """Return (high, medium, low) boolean hazard masks.

    All inputs are depth arrays (metres) on the same grid; only ``design`` is
    required. Missing events are simply ignored.
    """
    if not (min_depth <= medium_depth <= deep_depth):
        raise ValueError('Depth thresholds must satisfy min_depth <= medium_depth <= deep_depth')
    d_min = _wet(design, min_depth)
    d_med = _wet(design, medium_depth)
    d_deep = _wet(design, deep_depth)

    high = d_deep.copy()
    for ev in (frequent, very_frequent):
        w = _wet(ev, min_depth)
        if w is not None:
            high |= w

    med = d_med.copy()
    w = _wet(medium, min_depth)
    if w is not None:
        med |= w
    med &= ~high

    low = d_min & ~high & ~med
    return high, med, low


def zone_masks(design, medium=None, frequent=None, very_frequent=None,
               settled=None, min_depth=0.3, medium_depth=0.5, deep_depth=1.0, return_relaxed=False):
    """Apply the hazard rules and the settlement rule.

    Returns (red, orange, yellow) boolean masks before generalisation, plus the
    mask of cells moved from red to orange by the settlement rule if
    ``return_relaxed`` is True. ``settled`` is a boolean mask of existing
    settlements/facilities (or None).
    """
    high, med, low = hazard_classes(design, medium, frequent, very_frequent,
                                    min_depth, medium_depth, deep_depth)
    if settled is not None:
        soft = high & settled & ~_wet(design, deep_depth)
        vf = _wet(very_frequent, min_depth)
        if vf is not None:
            soft &= ~vf
    else:
        soft = np.zeros_like(high)
    red = high & ~soft
    orange = med | soft
    yellow = low
    if return_relaxed:
        return red, orange, yellow, soft
    return red, orange, yellow


def compose_zones(red, red_orange, red_orange_yellow, protection=None):
    """Build the zone-code raster from nested (cumulative) masks."""
    z = np.zeros(red.shape, np.uint8)
    z[red_orange_yellow] = ZONE_YELLOW
    z[red_orange] = ZONE_ORANGE
    z[red] = ZONE_RED
    if protection is not None:
        z[protection] = ZONE_GREEN
    return z
