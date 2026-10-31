# Flood Zoning & Evacuation Planner

A QGIS plugin that turns flood-depth maps from any hydraulic model into practical
flood-risk management layers: **spatial planning zones** (where building should be
forbidden, restricted or allowed with conditions), a **river protection zone**, and,
in upcoming versions, **evacuation zones, safe havens and evacuation routes**.

It works for any area in the world, in any coordinate system, and needs nothing beyond
a standard QGIS installation.

> **Status:** in development (version 0.1). The zoning tools are available; the
> evacuation tools are being added before the first official release (1.0).

---

## Tools

All tools are in the **Processing Toolbox → Flood Zoning & Evacuation Planner**.

### 1. River corridor (channel and protection buffer)

Creates the river channel as a polygon with its real width, plus a protection strip of a
chosen width along both banks (default 30 m).

The channel can come from:

| Method | Use when |
|---|---|
| River polygons as provided | You already have good river polygons (e.g. OpenStreetMap water areas). |
| Channel from a flood-depth raster along river lines | River polygons are missing or inaccurate. The channel is the continuous strip of deep water (default ≥ 2 m) in a frequent flood (e.g. 2-year) along the river lines. |
| Fixed width along river lines | Quick estimate with a known channel width. |

### 2. Spatial planning zones

Combines flood **probability** and flood **depth** into four zones:

| Zone | Colour | Default rule | Planning meaning |
|---|---|---|---|
| No development | Red | Flooded in the very frequent / frequent event (e.g. 2- or 10-year), **or** deeper than 1 m in the design event (e.g. 100-year) | No new buildings; no growth of existing settlements; relocate or protect critical facilities |
| Restricted development | Orange | Flooded in the medium-probability event (e.g. 50-year), **or** 0.5–1 m deep in the design event | No new critical facilities; flood-proofed repairs only; raised floors |
| Conditional development | Yellow | Flooded only in the design event, 0.3–0.5 m deep | Building allowed above the design flood level plus a safety margin, with a dry access route |
| Protection zone | Green | River protection buffer | No buildings, fences or obstructions along the river |

- Water shallower than **0.3 m** is treated as low-intensity flooding and ignored.
- Only the design event is required; the other events are optional.
- **Existing-settlement rule** (optional): inside existing settlements, areas that are high
  hazard only because of shallow flooding in the frequent event are placed in the
  Restricted zone instead of No development.
- **Grid filling**: the zones are smoothed into a few continuous areas (nearby patches
  joined, small holes filled, tiny patches removed). Zones are nested: red inside orange
  inside yellow.
- Run it once per scenario (e.g. *current condition* and *with mitigation measures*) and
  compare the results.

All thresholds can be changed in the tool dialog.

### Coming next (before version 1.0)

- **Evacuation zones**: whole settlements at risk, grouped by type (urban, rural, camps…).
- **Safe havens**: dry, high ground of about the same area as each settlement, preferably
  near public facilities and roads.
- **Evacuation routes**: least-cost routes using roads first, then footpaths, then open
  ground, avoiding deep water.
- **Report tables** for zones, safe havens and routes.

---

## Installation

**From QGIS** (after the first official release): *Plugins → Manage and Install Plugins*,
search for **Flood Zoning & Evacuation Planner**.

**From a zip file** (development versions):

1. Download the latest `flood_zoning_evacuation_planner-x.y.z.zip` from the
   [Actions](../../actions) artifacts or [Releases](../../releases) page.
2. In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP*.
3. The tools appear in the Processing Toolbox.

Requires QGIS 3.34 or newer (QGIS 4 supported). No extra Python packages are needed.

---

## Inputs

| Input | Notes |
|---|---|
| Flood depth rasters [m] | One per event, from any hydraulic model (HEC-RAS, TUFLOW, LISFLOOD-FP, MIKE, …). Dry cells may be no-data or 0. |
| Settlements (optional) | Polygons of built-up areas (e.g. OpenStreetMap `landuse=residential`). |
| Facilities (optional) | Points or polygons of schools, health centres, etc. |
| River lines / polygons | For the river corridor tool. |

Large study areas are processed at a coarser cell size automatically (depths are
averaged); you can set the cell size yourself in the advanced parameters.

---

## For developers

```bash
# rule tests (NumPy only)
pytest tests/test_zoning_rules.py
# full tests inside QGIS (e.g. in the qgis/qgis Docker image)
python3 scripts/run_tests.py -v tests
# build the plugin zip
python3 scripts/package.py
```

The core rules (`core/zoning_rules.py`) are plain NumPy; raster operations
(`core/gridtools.py`) use only GDAL; the QGIS Processing algorithms are in `algorithms/`.

---

## Licence

GNU General Public License v2.0 or later. See [LICENSE](LICENSE).

## Author

Muhammad Uzair Khan — uzairkhan1478@gmail.com

Bug reports and suggestions: [GitHub issues](../../issues).
