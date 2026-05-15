# Region polygon → per-season KDE histogram (viewer)

Status: done (2026-05-15) — implemented; custom right-side legend with
hover focus-highlight (uPlot's live legend reflowed and was dropped).

## Goal
Let the user draw a polygon on the map; show a seaborn-`kdeplot`-style panel of
the selected variable's distribution — one unit-area Gaussian-KDE curve per
enabled season plus a bold "All" curve. Polygon persists across variable
changes (only the curves recompute); honors the season on/off checkboxes.

## Decisions
- Draw tool: custom lightweight (no plugin).
- Chart: `uplot` (new dep) + its CSS.
- Curves: Gaussian KDE, Silverman bandwidth, normalized to unit area.
- Season filter: chart respects the existing season checkboxes.

## Pieces
1. **map.ts** — polygon draw + point-in-polygon
   - State: drawing flag, vertex latlngs, provisional polyline, final polygon.
   - `startPolygonDraw()`, `clearPolygon()`, `hasPolygon()`, `onPolygonChange(cb)`.
   - Click adds vertex; dblclick / Enter finishes; Esc cancels. Suppress
     doubleClickZoom + hover tooltip while drawing.
   - `tracesInPolygon(data)` → indices: project vertices + each trace via
     `map.options.crs.project` (zoom-independent, pole-correct), ray-cast.
   - Clear polygon on hemisphere rebuild / destroyMap.
2. **histogram.ts** — KDE + uPlot
   - `kde(values, gridX)` Gaussian, bw = 1.06·σ·n^(-1/5) (skip n<2 / σ=0),
     trapezoidal renorm to area 1.
   - Categorical season→color map (stable, chroma brewer), "All" = bold light.
   - uPlot line chart in a floating panel; title = variable label/unit.
3. **main.ts** — wiring
   - Sidebar "Draw region" / "Clear region" buttons + `#region-panel`.
   - onPolygonChange → cache in-polygon indices → updateRegionHistogram().
   - updateRegionHistogram(): group cached∩qcPass∩!NaN indices by
     frameCollection, keep enabled seasons; x-range = 2–98 pct of union;
     KDE per season + All; render; show panel.
   - Recompute on variable change (after ensureVariable) and season toggle;
     polygon/index cache untouched. Clear on dataset switch.

## Notes
- Panel: bottom-left, above Leaflet scale control, dark-theme styled.
- Seasons with <2 in-polygon points: no own curve but counted in "All".
