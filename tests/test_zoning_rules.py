# -*- coding: utf-8 -*-
"""Unit tests for the hazard / zone rules (NumPy only - no QGIS needed)."""
import importlib.util
import os

import numpy as np
import pytest

_PATH = os.path.join(os.path.dirname(__file__), '..', 'flood_zoning_evacuation_planner', 'core', 'zoning_rules.py')
_spec = importlib.util.spec_from_file_location('zoning_rules', _PATH)
zr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zr)


def arr(*v):
    return np.array(v, dtype=np.float32)


def test_hazard_classes_basic():
    #             cell: 0     1     2     3     4     5     6
    design = arr(0.0, 0.2, 0.4, 0.7, 1.2, 0.4, 0.4)
    medium = arr(0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.0)
    frequent = arr(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.4)
    high, med, low = zr.hazard_classes(design, medium, frequent)
    assert high.tolist() == [False, False, False, False, True, False, True]   # deep, frequent
    assert med.tolist() == [False, False, False, True, False, True, False]    # 0.5-1 m, medium event
    assert low.tolist() == [False, False, True, False, False, False, False]   # shallow, design only


def test_classes_are_exclusive_and_complete():
    rng = np.random.default_rng(0)
    d = [rng.random(1000) * 2 for _ in range(4)]
    d.sort(key=lambda a: a.mean())
    high, med, low = zr.hazard_classes(d[3], d[2], d[1], d[0])
    assert not (high & med).any() and not (high & low).any() and not (med & low).any()
    flooded = (d[3] >= 0.3) | (d[2] >= 0.3) | (d[1] >= 0.3) | (d[0] >= 0.3)
    assert ((high | med | low) == flooded).all()  # every flooded cell gets exactly one class


def test_missing_events_and_nan():
    design = arr(np.nan, 0.35, 0.8, 1.5)
    high, med, low = zr.hazard_classes(design)
    assert high.tolist() == [False, False, False, True]
    assert med.tolist() == [False, False, True, False]
    assert low.tolist() == [False, True, False, False]


def test_settlement_rule():
    design = arr(0.6, 0.6, 1.4, 0.6)
    frequent = arr(0.4, 0.4, 0.4, 0.4)
    very_frequent = arr(0.0, 0.0, 0.0, 0.5)
    settled = np.array([True, False, True, True])
    red, orange, yellow = zr.zone_masks(design, frequent=frequent, very_frequent=very_frequent, settled=settled)
    # cell 0: settled, shallow, only frequent -> orange; cell 1: not settled -> red;
    # cell 2: deep -> red; cell 3: flooded in very frequent event -> red
    assert red.tolist() == [False, True, True, True]
    assert orange.tolist() == [True, False, False, False]
    assert not yellow.any()


def test_threshold_order_validated():
    with pytest.raises(ValueError):
        zr.hazard_classes(arr(1.0), min_depth=0.6, medium_depth=0.5)


def test_compose_zones_nesting():
    r = np.array([True, False, False, False, False])
    ro = np.array([True, True, False, False, False])
    roy = np.array([True, True, True, False, False])
    prot = np.array([False, False, False, False, True])
    z = zr.compose_zones(r, ro, roy, prot)
    assert z.tolist() == [1, 2, 3, 0, 4]


def test_relaxed_mask_returned():
    design = arr(0.6, 0.6)
    frequent = arr(0.4, 0.4)
    settled = np.array([True, False])
    red, orange, yellow, relaxed = zr.zone_masks(design, frequent=frequent, settled=settled, return_relaxed=True)
    assert relaxed.tolist() == [True, False]
    assert orange.tolist() == [True, False] and red.tolist() == [False, True]
