# -*- coding: utf-8 -*-
"""Tests for the NumPy routing core (cost distance, paths, filters, k-means, planner)."""
import numpy as np
import pytest

from flood_zoning_evacuation_planner.core import costpath as cp
from flood_zoning_evacuation_planner.core import evacuation as ev


def _random_cost(seed):
    rng = np.random.default_rng(seed)
    nr, nc = rng.integers(25, 70, 2)
    cost = rng.random((nr, nc)) * 4 + 0.5
    cost[nr // 2, :nc - 4] = np.inf                       # wall with a gap
    src = np.zeros((nr, nc), bool)
    src[0, 0] = True
    return cost, src


@pytest.mark.parametrize('seed', [0, 1, 2])
def test_cost_distance_matches_scikit_image(seed):
    graph = pytest.importorskip('skimage.graph')
    cost, src = _random_cost(seed)
    d = cp.cost_distance(cost, src, res=5.0)
    ref, _ = graph.MCP_Geometric(np.where(np.isfinite(cost), cost, -1) * 5.0).find_costs([(0, 0)])
    assert (np.isfinite(d) == np.isfinite(ref)).all()
    fin = np.isfinite(d)
    assert np.allclose(d[fin], ref[fin], rtol=1e-9, atol=1e-6)


def test_cost_distance_uniform_is_8_connected_distance():
    cost = np.ones((11, 11))
    src = np.zeros_like(cost, bool)
    src[5, 5] = True
    d = cp.cost_distance(cost, src, res=2.0)
    assert d[5, 10] == pytest.approx(10.0)                # 5 straight steps of 2 m
    assert d[10, 10] == pytest.approx(5 * np.sqrt(2) * 2)  # 5 diagonal steps


def test_backtrack_follows_cheap_corridor():
    cost = np.full((30, 30), 10.0)
    cost[:, 15] = 1.0                                     # a road
    src = np.zeros_like(cost, bool)
    src[0, 15] = True
    d = cp.cost_distance(cost, src)
    path = cp.backtrack(d, cost, (29, 15))
    assert path[0] == (29, 15) and path[-1] == (0, 15)
    assert all(c == 15 for _, c in path)


def test_unreachable_is_inf():
    cost = np.ones((10, 10))
    cost[5, :] = np.inf
    src = np.zeros_like(cost, bool)
    src[0, 0] = True
    d = cp.cost_distance(cost, src)
    assert np.isinf(d[9, 9]) and np.isfinite(d[4, 9])
    assert cp.backtrack(d, cost, (9, 9)) == []


@pytest.mark.parametrize('size', [3, 9, 31])
def test_max_filter_matches_scipy(size):
    ndi = pytest.importorskip('scipy.ndimage')
    a = np.random.default_rng(3).random((57, 43))
    ref = ndi.maximum_filter(a, size=size, mode='constant', cval=-np.inf)
    assert np.allclose(cp.max_filter(a, size), ref)


def test_kmeans_separates_clusters():
    rng = np.random.default_rng(4)
    pts = np.r_[rng.normal(0, 1, (50, 2)), rng.normal(20, 1, (50, 2))]
    lab = cp.kmeans(pts, 2)
    assert len(set(lab[:50])) == 1 and len(set(lab[50:])) == 1 and lab[0] != lab[-1]


class _Grid:
    def __init__(self, res, shape):
        self.res, self.shape, self.cell_area = res, shape, res * res

    def sub(self, r0, c0, h, w):
        return _Grid(self.res, (h, w))


def _edt(mask, grid, max_dist=None):
    ndi = pytest.importorskip('scipy.ndimage')
    if not mask.any():
        return np.full(mask.shape, np.inf)
    return ndi.distance_transform_edt(~mask) * grid.res


def _big_pieces(mask, grid, min_cells):
    ndi = pytest.importorskip('scipy.ndimage')
    lab, n = ndi.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_cells
    keep[0] = False
    return keep[lab]


def test_planner_skips_land_pieces_that_are_too_small():
    pytest.importorskip('scipy.ndimage')
    n = 120
    g = _Grid(10.0, (n, n))
    cost = np.full((n, n), 1.8)
    cand = np.zeros((n, n), bool)
    for k in range(10):                       # ten small islands of land next to the village (9 cells each)
        cand[50 + (k % 5) * 6:53 + (k % 5) * 6, 30 + (k // 5) * 6:33 + (k // 5) * 6] = True
    cand[5:25, 90:110] = True                 # one big piece further away (400 cells)
    zone = dict(id='Z', rows=np.repeat(np.arange(55, 60), 5), cols=np.tile(np.arange(40, 45), 5), area_m2=50 * 100.0)
    planner = ev.EvacuationPlanner(g, cost, cand, _edt, {'search': 2000}, size_filter=_big_pieces)
    havens, routes = planner.plan([zone])
    assert len(havens) == 1 and havens[0]['rows'].max() < 25 and havens[0]['cols'].min() >= 90


def test_planner_synthetic_valley():
    pytest.importorskip('scipy.ndimage')
    n = 300
    g = _Grid(10.0, (n, n))
    rows = np.arange(n)[:, None] * np.ones((1, n))
    depth = np.clip(2.0 - np.abs(rows - 150) * 0.04, 0, None)      # flooded band rows 100-200
    river = np.abs(rows - 150) <= 2
    main = np.zeros((n, n), bool)
    main[:, 90] = True                                              # road with a bridge
    dem = 400.0 - rows * 0.1
    settl = np.zeros((n, n), bool)
    settl[175:190, 70:110] = True
    cost = ev.build_cost((n, n), {}, main=main, dem=dem, depth=depth, river=river, res=g.res)
    cand, _ = ev.candidate_land(g, {}, depth, _edt, zones=depth >= 0.3, settlements=settl, dem=dem)
    r, c = np.nonzero(settl)
    zone = dict(id='Z1', rows=r, cols=c, area_m2=settl.sum() * g.cell_area)
    cls = np.where(main, 1, 0).astype(np.uint8)
    planner = ev.EvacuationPlanner(g, cost, cand, _edt, {'search': 2500}, roads=main, depth=depth, dem=dem,
                                   road_classes=cls, size_filter=_big_pieces)
    havens, routes = planner.plan([zone])
    assert len(havens) == 1 and len(routes) == 1
    h = havens[0]
    assert abs(h['cells'] - settl.sum()) <= 2                      # same size as the settlement
    assert cand[h['rows'], h['cols']].all()                         # every haven cell is suitable
    rt = routes[0]
    assert planner.haven_label[rt['rows'][-1], rt['cols'][-1]] == h['label']   # route ends in the haven
    assert rt['shares']['main'] > 80                                 # and uses the road


def test_every_settlement_of_a_zone_gets_a_route():
    pytest.importorskip('scipy.ndimage')
    n = 120
    g = _Grid(10.0, (n, n))
    cost = np.full((n, n), 1.8)
    cand = np.zeros((n, n), bool)
    cand[5:25, 40:80] = True
    # two small settlements 600 m apart in one zone: together far below one sector (150 ha)
    a = (np.repeat(np.arange(80, 84), 4), np.tile(np.arange(20, 24), 4))
    b = (np.repeat(np.arange(80, 84), 4), np.tile(np.arange(80, 84), 4))
    rows, cols = np.r_[a[0], b[0]], np.r_[a[1], b[1]]
    parts = np.r_[np.ones(16, int), np.full(16, 2)]
    planner = ev.EvacuationPlanner(g, cost, cand, _edt, {'search': 2000}, size_filter=_big_pieces)
    zone = dict(id='Z', rows=rows, cols=cols, area_m2=32 * 100.0)
    havens, routes = planner.plan([dict(zone)])
    assert len(routes) == 1                     # without parts: one route for the whole zone
    planner = ev.EvacuationPlanner(g, cost, cand, _edt, {'search': 2000}, size_filter=_big_pieces)
    havens, routes = planner.plan([dict(zone, parts=parts)])
    assert len(havens) == 1 and len(routes) == 2
    starts = sorted(int(r['cols'][0]) for r in routes)
    assert starts[0] < 30 and starts[1] >= 80   # one route from each settlement
