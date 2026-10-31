# Changelog

## 0.2.0 (in development)
- Evacuation zones tool: settlements within a distance of the risk zones are evacuated as a
  whole and grouped by settlement type into coded zones (e.g. RC-01).
- Safe havens and evacuation routes tool: one safe haven per zone, about the same area as the
  zone, on dry ground away from the flood, with an optional freeboard check on a DEM and a
  preference for public facilities and roads; one least-cost walking route per sector of the
  zone, with length, walking time, share of road / path / open ground and wet length.
- Fast exact least-cost distance in pure NumPy (no extra packages).

## 0.1.0
- River corridor tool: river channel polygon (from river polygons, from the channel visible in a
  flood-depth raster along river lines, or a fixed width) and a protection buffer.
- Spatial planning zones tool: no-development, restricted, conditional and protection zones from
  up to four flood events, with an existing-settlement rule and grid filling.
- Works in any projected CRS; geographic inputs are processed in the local UTM zone.
