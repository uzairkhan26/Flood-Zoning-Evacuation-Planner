# -*- coding: utf-8 -*-
"""Least-cost distance and paths on a grid, plus small helpers (pure NumPy).

``cost_distance`` computes the accumulated cost from a set of source cells over
an 8-connected grid, where moving between two neighbouring cells costs the mean
of their cell costs times the step length (1 or sqrt(2) cells) times the cell
size. This is the same definition as GRASS ``r.cost -k`` / scikit-image
``MCP_Geometric``. It uses a fast-sweeping scheme: rows are processed top-down
and bottom-up, and within a row the left/right propagation is solved exactly
with a cumulative-minimum trick, so the work is vectorised along each row.
"""
import math

import numpy as np

BARRIER = 1.0e7     # cost used for impassable cells (keeps arithmetic finite)
SQRT2 = math.sqrt(2.0)


def _row_scan(d, w):
    """Exact left->right then right->left min-plus propagation along one row.

    d: accumulated costs (n,), w: edge costs between cells j-1 and j (n-1,).
    """
    s = np.empty_like(d)
    s[0] = 0.0
    np.cumsum(w, out=s[1:])
    d = np.minimum(d, s + np.minimum.accumulate(d - s))
    # right -> left
    dr = d[::-1]
    sr = np.empty_like(d)
    sr[0] = 0.0
    np.cumsum(w[::-1], out=sr[1:])
    dr = np.minimum(dr, sr + np.minimum.accumulate(dr - sr))
    return dr[::-1]


def _eps(v):
    """Tolerance below which a decrease is treated as rounding noise."""
    return 1e-9 * (1.0 + np.abs(v))


def _row_passes(d, wh, wv, wd1, wd2, ver, seen_dn, seen_up):
    """One top-down and one bottom-up pass over the rows (in place).

    A row is only recomputed when the neighbouring row it reads from has changed
    since the last time it was processed (``ver`` = change counter per row,
    ``seen_*`` = counter value last used). Returns True if anything changed.
    """
    nr, nc = d.shape
    any_change = False
    for r in range(1, nr):              # top -> bottom
        if ver[r - 1] == seen_dn[r]:
            continue
        seen_dn[r] = ver[r - 1]
        prev = d[r - 1]
        row = np.minimum(d[r], prev + wv[r - 1])
        if nc > 1:
            row[1:] = np.minimum(row[1:], prev[:-1] + wd1[r - 1])
            row[:-1] = np.minimum(row[:-1], prev[1:] + wd2[r - 1])
            row = _row_scan(row, wh[r])
        if (row < d[r] - _eps(d[r])).any():
            d[r] = np.minimum(d[r], row)
            ver[r] += 1
            any_change = True
    for r in range(nr - 2, -1, -1):     # bottom -> top
        if ver[r + 1] == seen_up[r]:
            continue
        seen_up[r] = ver[r + 1]
        nxt = d[r + 1]
        row = np.minimum(d[r], nxt + wv[r])
        if nc > 1:
            row[:-1] = np.minimum(row[:-1], nxt[1:] + wd1[r])
            row[1:] = np.minimum(row[1:], nxt[:-1] + wd2[r])
            row = _row_scan(row, wh[r])
        if (row < d[r] - _eps(d[r])).any():
            d[r] = np.minimum(d[r], row)
            ver[r] += 1
            any_change = True
    return any_change


def cost_distance(cost, sources, res=1.0, max_iter=500):
    """Accumulated least cost from ``sources`` (bool array) over ``cost`` (float array).

    Cells with cost ``inf``/NaN or <= 0 are impassable. Returns float64 array;
    unreachable cells are ``inf``. Each iteration sweeps the rows (down and up,
    with exact left/right propagation) and then the columns; rows/columns whose
    neighbours did not change are skipped, so later iterations are cheap.
    """
    c = np.array(cost, dtype=np.float64)
    bad = ~np.isfinite(c) | (c <= 0)
    c[bad] = BARRIER
    nr, nc = c.shape
    wh = (c[:, :-1] + c[:, 1:]) * (0.5 * res)                    # (nr, nc-1)
    wv = (c[:-1, :] + c[1:, :]) * (0.5 * res)                    # (nr-1, nc)
    wd1 = (c[:-1, :-1] + c[1:, 1:]) * (0.5 * res * SQRT2)        # (r,j)->(r+1,j+1)
    wd2 = (c[:-1, 1:] + c[1:, :-1]) * (0.5 * res * SQRT2)        # (r,j+1)->(r+1,j)
    # the same edges seen from the transposed grid (columns become rows)
    t_wh, t_wv = np.ascontiguousarray(wv.T), np.ascontiguousarray(wh.T)
    t_wd1, t_wd2 = np.ascontiguousarray(wd1.T), np.ascontiguousarray(wd2.T)
    if not sources.any():
        return np.full((nr, nc), np.inf)
    d = np.full((nr, nc), 1e300)            # finite "infinity" keeps the scans free of NaN
    d[sources] = 0.0
    big = BARRIER * res * 0.5
    if nc > 1:
        for r in np.nonzero(sources.any(1))[0]:
            d[r] = _row_scan(d[r], wh[r])
    ver_r = np.zeros(nr, np.int64)
    ver_c = np.zeros(nc, np.int64)
    ver_r[sources.any(1)] = 1
    ver_c[sources.any(0)] = 1
    sdn_r, sup_r = np.full(nr, -1, np.int64), np.full(nr, -1, np.int64)
    sdn_c, sup_c = np.full(nc, -1, np.int64), np.full(nc, -1, np.int64)
    for _ in range(max_iter):
        before = d.copy()
        ch1 = _row_passes(d, wh, wv, wd1, wd2, ver_r, sdn_r, sup_r)
        if ch1:   # columns touched by the row passes must be revisited by the column passes
            ver_c[(d < before - _eps(before)).any(0)] += 1
        ch2 = False
        if nr > 1 and nc > 1:
            before = d.copy()
            dt = np.ascontiguousarray(d.T)
            ch2 = _row_passes(dt, t_wh, t_wv, t_wd1, t_wd2, ver_c, sdn_c, sup_c)
            if ch2:
                d = np.ascontiguousarray(dt.T)
                ver_r[(d < before - _eps(before)).any(1)] += 1
        if not (ch1 or ch2):
            break
    d[d >= big] = np.inf          # paths that had to cross a barrier / never reached
    return d


_NEIGH = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def backtrack(dist, cost, start, res=1.0, max_steps=None):
    """Least-cost path from ``start`` (row, col) back to the nearest source.

    ``dist`` is the output of :func:`cost_distance` for the same ``cost``.
    Returns a list of (row, col) from start to the source.
    """
    c = np.array(cost, dtype=np.float64)
    c[~np.isfinite(c) | (c <= 0)] = BARRIER
    nr, nc = dist.shape
    r, k = start
    if not np.isfinite(dist[r, k]):
        return []
    path = [(r, k)]
    max_steps = max_steps or nr * nc
    for _ in range(max_steps):
        if dist[r, k] <= 0:
            break
        best, bq = dist[r, k], None
        for dr, dk in _NEIGH:
            rr, kk = r + dr, k + dk
            if 0 <= rr < nr and 0 <= kk < nc and np.isfinite(dist[rr, kk]):
                step = (SQRT2 if dr and dk else 1.0) * res * 0.5 * (c[r, k] + c[rr, kk])
                v = dist[rr, kk] + step
                if dist[rr, kk] < best and v <= dist[r, k] + 1e-6 * max(1.0, dist[r, k]):
                    best, bq = dist[rr, kk], (rr, kk)
        if bq is None:   # numerical tie: step to the lowest neighbour
            cand = [(dist[r + dr, k + dk], (r + dr, k + dk)) for dr, dk in _NEIGH
                    if 0 <= r + dr < nr and 0 <= k + dk < nc]
            v, bq = min(cand)
            if v >= dist[r, k]:
                break
        r, k = bq
        path.append((r, k))
    return path


def kmeans(points, k, iterations=30, seed=0):
    """Simple deterministic k-means. points: (n, 2). Returns labels (n,)."""
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if k <= 1 or n <= k:
        return np.zeros(n, dtype=int) if k <= 1 else np.arange(n) % max(k, 1)
    # farthest-point initialisation (deterministic)
    rng = np.random.default_rng(seed)
    centres = [pts[rng.integers(n)]]
    d2 = ((pts - centres[0]) ** 2).sum(1)
    for _ in range(1, k):
        centres.append(pts[int(np.argmax(d2))])
        d2 = np.minimum(d2, ((pts - centres[-1]) ** 2).sum(1))
    centres = np.array(centres)
    labels = np.zeros(n, dtype=int)
    for _ in range(iterations):
        dist = ((pts[:, None, :] - centres[None, :, :]) ** 2).sum(2)
        new = dist.argmin(1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            m = labels == j
            if m.any():
                centres[j] = pts[m].mean(0)
    return labels


def _max_filter_1d(a, size, axis):
    """Sliding maximum (centred window of ``size`` cells) along ``axis`` (van Herk / Gil-Werman)."""
    if size <= 1:
        return a.copy()
    a = np.moveaxis(a, axis, -1)
    n = a.shape[-1]
    half = size // 2
    pad = np.full(a.shape[:-1] + (half,), -np.inf, dtype=np.float64)
    x = np.concatenate([pad, a.astype(np.float64), pad], axis=-1)
    m = x.shape[-1]
    nb = -(-m // size)
    x = np.concatenate([x, np.full(a.shape[:-1] + (nb * size - m,), -np.inf)], axis=-1)
    blocks = x.reshape(a.shape[:-1] + (nb, size))
    g = np.maximum.accumulate(blocks, axis=-1).reshape(a.shape[:-1] + (nb * size,))
    h = np.maximum.accumulate(blocks[..., ::-1], axis=-1)[..., ::-1].reshape(a.shape[:-1] + (nb * size,))
    out = np.maximum(h[..., 0:n], g[..., size - 1:size - 1 + n])
    return np.moveaxis(out, -1, axis)


def max_filter(a, size):
    """2-D square sliding maximum of odd ``size`` (NaN treated as -inf)."""
    x = np.where(np.isnan(a), -np.inf, a).astype(np.float64)
    return _max_filter_1d(_max_filter_1d(x, size, 0), size, 1)


def box_mean(a, size):
    """2-D mean over a square window of odd ``size`` (edges use the available cells)."""
    x = np.nan_to_num(np.asarray(a, dtype=np.float64))
    w = np.isfinite(np.asarray(a, dtype=np.float64)).astype(np.float64)
    half = size // 2

    def _sum(v):
        p = np.pad(v, half, mode='constant')
        c = p.cumsum(0).cumsum(1)
        c = np.pad(c, ((1, 0), (1, 0)), mode='constant')
        return c[size:, size:] - c[:-size, size:] - c[size:, :-size] + c[:-size, :-size]

    s, cnt = _sum(x * w), _sum(w)
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(cnt > 0, s / cnt, np.nan)
