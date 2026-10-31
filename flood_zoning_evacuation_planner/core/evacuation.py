# -*- coding: utf-8 -*-
"""Safe havens and evacuation routes (pure NumPy planning logic).

The QGIS algorithm prepares the input arrays on a working grid; this module
decides where each safe haven goes and traces the routes. A ``distance_fn``
(mask, grid, max_dist_m) -> distance array in metres is injected so that the
plugin can use GDAL proximity while tests can use any implementation.
"""
import heapq
import math

import numpy as np

from .costpath import backtrack, box_mean, cost_distance, kmeans, max_filter

DEFAULTS = dict(
    haven_factor=1.0,        # safe haven area / settlement area
    min_dist_flood=150.0,    # m from any flooded cell
    min_dist_zones=200.0,    # m from any planning zone
    freeboard=0.5,           # m above the highest nearby flood level
    wl_radius=300.0,         # m: "nearby" for the flood-level check
    clearance=50.0,          # m from settlements and other havens
    sector_ha=150.0,         # one route per this many hectares of settlement
    search=10000.0,          # m: search radius for a safe haven
    c_main=1.0, c_minor=1.1, c_path=1.3, c_open=1.8,   # cost per metre by surface
    slope_factor=20.0,       # open-ground cost x (1 + slope_factor * slope)
    wet_depth=0.01, deep_depth=0.3, very_deep_depth=1.0,
    f_shallow=3.0, f_deep=40.0, f_very_deep=5.0,       # water multipliers
    pref_facility=0.8, pref_road=0.85,                  # cost multipliers that favour good sites
    facility_dist=150.0, road_dist=60.0,
    walk_kmh=3.0,
)


def build_cost(shape, params, main=None, minor=None, path=None, dem=None, depth=None, river=None, res=1.0):
    """Cost per metre of moving through each cell (inf = impassable)."""
    p = dict(DEFAULTS, **params)
    cost = np.full(shape, p['c_open'], np.float64)
    if dem is not None:
        z = box_mean(dem, 5)
        gy, gx = np.gradient(np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0.0), res)
        slope = np.hypot(gx, gy)
        slope[~np.isfinite(slope)] = 0.0
        cost *= 1.0 + p['slope_factor'] * np.clip(slope, 0, 1)
    any_road = np.zeros(shape, bool)
    for m, c in ((path, p['c_path']), (minor, p['c_minor']), (main, p['c_main'])):
        if m is not None:
            cost[m] = c
            any_road |= m
    if depth is not None:
        d = np.nan_to_num(depth, nan=0.0)
        cost[(d >= p['wet_depth']) & (d < p['deep_depth'])] *= p['f_shallow']
        cost[d >= p['deep_depth']] *= p['f_deep']
        cost[d >= p['very_deep_depth']] *= p['f_very_deep']
    if river is not None:
        crossing = np.zeros(shape, bool)
        for m in (main, minor):
            if m is not None:
                crossing |= m
        cost[river & ~crossing] = np.inf
    return cost


def candidate_land(grid, params, depth, distance_fn, zones=None, settlements=None, domain=None, dem=None):
    """Cells where a safe haven may be placed, and a mask of where the height check was possible."""
    p = dict(DEFAULTS, **params)
    d = np.nan_to_num(depth, nan=0.0)
    wet = d >= p['wet_depth']
    cand = ~wet & (distance_fn(wet, grid, p['min_dist_flood']) >= p['min_dist_flood'])
    if zones is not None and zones.any():
        cand &= distance_fn(zones, grid, p['min_dist_zones']) >= p['min_dist_zones']
    if settlements is not None and settlements.any():
        cand &= distance_fn(settlements, grid, p['clearance']) > p['clearance']
    if domain is not None:
        cand &= domain
    height_known = np.zeros(d.shape, bool)
    if dem is not None:
        valid = np.isfinite(dem)
        wl = np.where(wet & valid, np.nan_to_num(dem) + d, -np.inf)
        size = 2 * int(round(p['wl_radius'] / grid.res)) + 1
        near_wl = max_filter(wl, size)
        ok = ~valid | np.isinf(near_wl) | (np.nan_to_num(dem) >= near_wl + p['freeboard'])
        cand &= ok
        height_known = valid
    return cand, height_known


class EvacuationPlanner:
    """Places one safe haven per evacuation zone and traces one route per sector."""

    def __init__(self, grid, cost, candidate, distance_fn, params=None, roads=None, facilities_near=None,
                 depth=None, dem=None, road_classes=None, feedback=None, size_filter=None):
        self.g = grid
        self.p = dict(DEFAULTS, **(params or {}))
        self.cost = cost
        self.cand = candidate
        self.dist = distance_fn
        self.roads = roads if roads is not None else np.zeros(cost.shape, bool)
        self.fac_near = facilities_near
        self.depth = None if depth is None else np.nan_to_num(depth, nan=0.0)
        self.dem = dem
        self.road_classes = road_classes  # uint8: 0 open, 1 main, 2 minor, 3 path
        self.used = np.zeros(cost.shape, bool)
        self.haven_label = np.zeros(cost.shape, np.int32)
        self.fb = feedback
        # size_filter(mask, grid, min_cells) -> mask without 8-connected pieces smaller than min_cells
        self.size_filter = size_filter
        self.road_near = None
        if self.roads.any():
            self.road_near = distance_fn(self.roads, grid, self.p['road_dist']) <= self.p['road_dist']

    # ------------------------------------------------------------------
    def _log(self, msg):
        if self.fb is not None:
            self.fb.pushInfo(msg)

    def _window(self, rows, cols, margin):
        nr, nc = self.cost.shape
        r0, r1 = max(int(rows.min()) - margin, 0), min(int(rows.max()) + margin + 1, nr)
        c0, c1 = max(int(cols.min()) - margin, 0), min(int(cols.max()) + margin + 1, nc)
        return r0, r1, c0, c1

    def _sectors(self, rows, cols, area_ha):
        n = max(1, int(math.ceil(area_ha / self.p['sector_ha'])))
        pts = np.c_[rows, cols].astype(float)
        if n == 1:
            return np.zeros(len(rows), int), 1
        if len(pts) > 20000:   # cluster a sample, then assign every cell to the nearest centre
            idx = np.linspace(0, len(pts) - 1, 20000).astype(int)
            lab_s = kmeans(pts[idx], n)
            centres = np.array([pts[idx][lab_s == k].mean(0) for k in range(n) if (lab_s == k).any()])
            lab = ((pts[:, None, :] - centres[None]) ** 2).sum(2).argmin(1)
            return lab, len(centres)
        lab = kmeans(pts, n)
        return lab, int(lab.max()) + 1

    def _origins(self, rows, cols, labels, nsec):
        out = []
        for k in range(nsec):
            m = labels == k
            if not m.any():
                continue
            rr, cc = rows[m], cols[m]
            r0, c0 = rr.mean(), cc.mean()
            onroad = self.roads[rr, cc]
            if onroad.any():
                j = np.argmin((rr[onroad] - r0) ** 2 + (cc[onroad] - c0) ** 2)
                out.append((int(rr[onroad][j]), int(cc[onroad][j]), m.sum()))
            else:
                j = np.argmin((rr - r0) ** 2 + (cc - c0) ** 2)
                out.append((int(rr[j]), int(cc[j]), m.sum()))
        return out

    @staticmethod
    def _grow(avail, seed, target):
        """Grow a compact area of ``target`` cells over ``avail`` from ``seed``.

        Dijkstra over the 8-connected grid (steps of 1 and sqrt 2), stopped as soon as
        ``target`` cells are reached. Returns (area, None), or (None, reached) if the
        connected land around the seed is too small.
        """
        nr, nc = avail.shape
        done = np.zeros(avail.shape, bool)
        best = {seed: 0.0}
        heap = [(0.0, int(seed[0]), int(seed[1]))]
        taken = []
        s2 = math.sqrt(2.0)
        while heap and len(taken) < target:
            dd, r, k = heapq.heappop(heap)
            if done[r, k]:
                continue
            done[r, k] = True
            taken.append((r, k))
            for dr, dk in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                rr, kk = r + dr, k + dk
                if 0 <= rr < nr and 0 <= kk < nc and avail[rr, kk] and not done[rr, kk]:
                    nd = dd + (s2 if dr and dk else 1.0)
                    if nd < best.get((rr, kk), np.inf):
                        best[(rr, kk)] = nd
                        heapq.heappush(heap, (nd, rr, kk))
        if len(taken) < target:
            return None, done
        out = np.zeros(avail.shape, bool)
        idx = np.array(taken)
        out[idx[:, 0], idx[:, 1]] = True
        return out, None

    # ------------------------------------------------------------------
    def _find_haven(self, rows, cols, origins, target, margin_m):
        """Search for a haven within ``margin_m`` of the zone. Returns (haven_mask, window) or (None, window)."""
        res = self.g.res
        r0, r1, c0, c1 = self._window(rows, cols, int(margin_m / res))
        win = (slice(r0, r1), slice(c0, c1))
        cost_w = self.cost[win]
        src = np.zeros(cost_w.shape, bool)
        for orow, ocol, _ in origins:
            src[orow - r0, ocol - c0] = True
        cum = cost_distance(cost_w, src, res)
        sub = self.g.sub(r0, c0, r1 - r0, c1 - c0)
        used_w = self.used[win]
        used_near = (self.dist(used_w, sub, self.p['clearance']) <= self.p['clearance']) if used_w.any() \
            else np.zeros(cost_w.shape, bool)
        avail = self.cand[win] & ~used_near
        if self.size_filter is not None and avail.any():
            avail = self.size_filter(avail, sub, target)   # only pieces of land big enough for the haven
        pref = np.ones(cost_w.shape)
        if self.fac_near is not None:
            pref[self.fac_near[win]] *= self.p['pref_facility']
        if self.road_near is not None:
            pref[self.road_near[win]] *= self.p['pref_road']
        score = np.where(avail & np.isfinite(cum), cum * pref, np.inf)
        for _try in range(40):
            k = int(np.argmin(score))
            if not np.isfinite(score.flat[k]):
                break
            seed = np.unravel_index(k, score.shape)
            haven, too_small = self._grow(avail, seed, target)
            if haven is not None:
                return haven, (r0, r1, c0, c1)
            score[too_small] = np.inf
        return None, (r0, r1, c0, c1)

    def plan(self, zones):
        """zones: list of dict(id, rows, cols, area_m2, ...). Returns (havens, routes).

        The haven search starts close to the zone (2 km or twice the zone size)
        and widens step by step up to ``search`` metres.
        """
        res = self.g.res
        havens, routes = [], []
        for hid, z in enumerate(sorted(zones, key=lambda z: z['area_m2']), 1):
            rows, cols = z['rows'], z['cols']
            if len(rows) == 0:
                self._log('Zone %s is outside the flood raster; skipped.' % z['id'])
                continue
            target = max(1, int(round(z['area_m2'] * self.p['haven_factor'] / self.g.cell_area)))
            labels, nsec = self._sectors(rows, cols, z['area_m2'] / 1e4)
            origins = self._origins(rows, cols, labels, nsec)
            size_m = max(rows.max() - rows.min(), cols.max() - cols.min()) * res
            margin = min(max(2000.0, 2.0 * size_m, 3.0 * math.sqrt(target * self.g.cell_area)), self.p['search'])
            haven = None
            while True:
                haven, (r0, r1, c0, c1) = self._find_haven(rows, cols, origins, target, margin)
                if haven is not None or margin >= self.p['search']:
                    break
                margin = min(margin * 2.0, self.p['search'])
            if haven is None:
                self._log('No room for a safe haven of %.1f ha within %.0f km of zone %s.'
                          % (target * self.g.cell_area / 1e4, self.p['search'] / 1000, z['id']))
                continue
            hr, hc = np.nonzero(haven)
            hr, hc = hr + r0, hc + c0
            self.used[hr, hc] = True
            self.haven_label[hr, hc] = hid
            havens.append(dict(label=hid, zone=z, rows=hr, cols=hc, cells=len(hr),
                               area_m2=len(hr) * self.g.cell_area, n_routes=0))
            # routes: same window in which the haven was found (so every origin can reach it)
            q0, q1, p0, p1 = r0, r1, c0, c1
            win = (slice(q0, q1), slice(p0, p1))
            cost_w = self.cost[win]
            dist = cost_distance(cost_w, self.haven_label[win] == hid, res)
            for s, (orow, ocol, ncell) in enumerate(origins, 1):
                path = backtrack(dist, cost_w, (orow - q0, ocol - p0), res)
                if not path:
                    self._log('Zone %s: no route found from sector %d.' % (z['id'], s))
                    continue
                pr = np.array([q[0] for q in path]) + q0
                pc = np.array([q[1] for q in path]) + p0
                routes.append(self._route_stats(z, havens[-1], s, ncell, pr, pc))
                havens[-1]['n_routes'] += 1
            self._log('Zone %s: safe haven %.1f ha, %d route(s).' % (z['id'], havens[-1]['area_m2'] / 1e4,
                                                                     havens[-1]['n_routes']))
        return havens, routes

    def _route_stats(self, zone, haven, sector, ncell, pr, pc):
        res = self.g.res
        step = np.r_[0.0, np.where((np.diff(pr) != 0) & (np.diff(pc) != 0), math.sqrt(2.0), 1.0) * res]
        length = float(step.sum())
        out = dict(zone=zone, haven=haven, sector=sector, sector_ha=ncell * self.g.cell_area / 1e4,
                   rows=pr, cols=pc, length_m=length, walk_min=length / (self.p['walk_kmh'] * 1000.0 / 60.0))
        shares = {}
        if self.road_classes is not None:
            cls = self.road_classes[pr, pc]
            for k, name in ((1, 'main'), (2, 'minor'), (3, 'path'), (0, 'open')):
                shares[name] = float(step[cls == k].sum() / length * 100.0) if length > 0 else 0.0
        out['shares'] = shares
        out['wet_m'] = float(step[self.depth[pr, pc] >= self.p['deep_depth']].sum()) if self.depth is not None else 0.0
        if self.dem is not None:
            z0, z1 = self.dem[pr[0], pc[0]], self.dem[pr[-1], pc[-1]]
            out['z_start'] = float(z0) if np.isfinite(z0) else None
            out['z_end'] = float(z1) if np.isfinite(z1) else None
        return out
