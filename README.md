# Flood Zoning & Evacuation Planner

A QGIS plugin that turns flood-depth maps from any hydraulic model into practical
flood-risk management layers: **spatial planning zones** (where building should be
forbidden, restricted or allowed with conditions), a **river protection zone**,
**evacuation zones**, **safe havens** and **evacuation routes**.

It works for any area in the world, in any coordinate system, and needs nothing beyond
a standard QGIS installation.

> **Status:** in development (version 0.2). Zoning and evacuation tools are available;
> report tables and a user guide will follow in the first official release (1.0).

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

### 3. Evacuation zones

Finds the settlements that must be evacuated and groups them.

- A settlement is **at risk** if any part of it is within a chosen distance (default 100 m)
  of the red, orange or yellow zones (or of any flood extent you provide).
- The **whole settlement** is evacuated, not only the flooded part.
- Settlements of the same type closer than the grouping distance (default 1 km) form one
  zone. Zones get codes from the type initials, e.g. `T-01` (town), `RC-02` (refugee camp),
  `RS-03` (rural settlement).

### 4. Safe havens and evacuation routes

For every evacuation zone it finds one **safe haven** and the **routes** to it.

**Safe haven** – a single area of about the same size as the evacuation zone (factor
adjustable), on land that is:

- dry in the design flood, at least 150 m from flooded cells and 200 m from the planning zones;
- outside settlements (50 m clearance) and at least 150 m from the river;
- if a DEM is given, at least 0.5 m above the highest nearby flood water level;
- as close as possible (in travel effort) to the zone, preferably next to public
  facilities (schools, health centres) and roads.

**Evacuation routes** – large zones are split into sectors (default about 150 ha each) and
each sector gets one least-cost walking route from its nearest road point to the haven.
Travel effort per metre:

| Surface | Relative effort |
|---|---|
| Main road | 1.0 |
| Minor road / track | 1.1 |
| Footpath | 1.3 |
| Open ground | 1.8 |

Effort increases with slope (from the DEM) and with water depth (×3 shallow, ×40 deep);
the river can only be crossed where a road crosses it. Each route reports its length,
walking time (3 km/h), share of main road / minor road / path / open ground, the length
through flood water, and start and end elevations.

Road classes are read from an OpenStreetMap-style `highway` field by default; any
other field can be chosen.

### Coming next (before version 1.0)

- **Report tables** for zones, safe havens and routes (ready to paste into a report).
- User guide with a worked example.

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
| Roads and paths (optional) | Lines, e.g. OpenStreetMap roads with a `highway` field. |
| DEM (optional) | Ground elevation [m], for the freeboard check and slope on routes. |
| Model domain (optional) | Polygon of the hydraulic model extent: safe havens are only placed where the flood is known. |

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
