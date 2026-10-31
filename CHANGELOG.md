# Changelog

## 1.0.0
- Report tables tool: key figures and plain-language tables in Word (.docx) or HTML for the
  planning zones, a comparison of two scenarios, evacuation zones, safe havens and routes.
- Evacuation zones: optional barrier layer (e.g. the river channel) so that settlements on
  opposite banks are never grouped.
- Safe havens and routes: every separate settlement of a zone (more than 100 m apart) gets its own
  route; safe havens keep at least 150 m from the river layer.
- User guide with a worked example (fictional river valley), linked from each tool's Help button.
- Scripts to create the example data and re-run the example with figures.

## 0.2.0
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
