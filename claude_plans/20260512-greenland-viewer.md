# Greenland support in the web viewer

The processing pipeline already supports Greenland (see `runner._get_region_geometry`
and `config/config_greenland.yaml`). The viewer is the missing piece.

## Status: PLANNED — not yet implemented.

## Where the hemisphere assumptions live

All hardcoded today in `web/src/map.ts`:

| Line(s) | Symbol | Antarctic-specific value |
|--------:|:-------|:--------|
| 7–10  | `PROJ_DEF` / `proj4.defs("EPSG:3031", ...)` | south polar stereographic (`lat_0=-90`, `lat_ts=-71`) |
| 12–14 | `GIBS_RESOLUTIONS`, `GIBS_ORIGIN`, `GIBS_BOUNDS` | tile grid for GIBS `epsg3031` endpoint |
| 16–20 | `EPSG3031` (Leaflet `Proj.CRS`) | hardcoded CRS |
| 39    | `crs: EPSG3031` in `L.map(...)` | one map instance, one CRS |
| 40    | `center: [-76, 162]`, `zoom: 2` | hardcoded near Antarctica |
| 55, 59 | tile URLs | `gibs.earthdata.nasa.gov/wmts/epsg3031/...` |

`config.ts` is hemisphere-agnostic today — `STORES` is just `{label, url}`. `main.ts`,
`store.ts`, `colormap.ts` are projection-agnostic (they work in lat/lon).

`index.html` (lines 185–188) has hardcoded `<option>`s for the dataset selector; main.ts
indexes into `STORES` by `selectedIndex`. Easier to generate options from `STORES`.

## Plan

### 1. Annotate each store with its hemisphere

Extend `StoreConfig` in `web/src/config.ts`:

```ts
export type Hemisphere = "antarctic" | "arctic";

export interface StoreConfig {
  label: string;
  url: string;
  hemisphere: Hemisphere;
}
```

Add the Greenland store:

```ts
{
  label: "Greenland",
  url: "https://opr-radar-metrics.s3.us-west-2.amazonaws.com/icechunk/greenland/",
  hemisphere: "arctic",
},
```

Backfill `hemisphere: "antarctic"` on the two existing entries.

### 2. Make `map.ts` hemisphere-aware

Refactor so the CRS/basemap/center are parameterized:

- Introduce a `HemisphereConfig` record keyed by hemisphere, each entry holding:
  - the proj4 string and EPSG code (`EPSG:3031` vs `EPSG:3413`),
  - the GIBS tile grid (resolutions/origin/bounds — likely the same 8192→32 powers-of-two grid for both, since GIBS uses a consistent scheme, but **verify** against `https://gibs.earthdata.nasa.gov/wmts/epsg3413/best/wmts.cgi?SERVICE=WMTS&REQUEST=GetCapabilities`),
  - the two basemap URLs,
  - the default `center` and `zoom`.
- Replace `initMap(containerId)` with `initMap(containerId, hemisphere)` that builds the CRS and base layers from that config.

EPSG:3413 (NSIDC Sea Ice Polar Stereographic North):
```
+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs
```

GIBS Arctic equivalents to the Antarctic basemaps:
- `https://gibs.earthdata.nasa.gov/wmts/epsg3413/best/BlueMarble_ShadedRelief_Bathymetry/default/2004-08-01/500m/{z}/{y}/{x}.jpeg`
- `https://gibs.earthdata.nasa.gov/wmts/epsg3413/best/OSM_Land_Water_Map/default/2024-01-01/250m/{z}/{y}/{x}.png` *(SCAR_Land_Water_Map is Antarctic-only — the Arctic equivalent is named differently; confirm via GIBS GetCapabilities before shipping)*

Default Greenland view: `center: [72, -40]`, `zoom: 3` (covers the whole island).

### 3. Recreate the map on hemisphere switch

Leaflet does not allow changing the CRS of a live map. In `main.ts#switchDataset`:

1. Look up the new store's hemisphere.
2. If it differs from the current map's hemisphere, tear the map down (`map.remove()`, clear the `#map` container) and re-call `initMap` for the new hemisphere.
3. If it matches, leave the map intact — only swap data layers.

This means `map.ts` needs to export a way to query the current hemisphere (e.g. return it from `initMap` or expose a getter). Encapsulate the module-level `map`/`baseLayers` so re-init wipes them cleanly.

### 4. Drive the UI selectors from config

While we're touching it: stop hardcoding dropdown options in `index.html`. In `main.ts#init`, populate `#dataset-select` from `STORES` and `#basemap-select` from `getBasemapNames()`. Keeps adding new datasets to a one-file edit.

### 5. (Optional) Persist the dataset choice in URL hash

If hemisphere-switching causes a visible flicker, the easiest UX is to write the
selected dataset into `location.hash` and pick it up on load. Skip unless it becomes
annoying.

## Out of scope for this plan

- Cross-hemisphere views (one map with both poles). Not useful; not planned.
- Per-collection sub-filters (e.g. "Greenland NW only"). Add later if useful.
- Sea ice / coastline reference overlays beyond GIBS basemaps.

## Validation

- Build with `npm run build` after edits; fix TS errors.
- Local `npm run dev`: switch between Antarctic and Greenland stores, verify
  basemap + data points project correctly and the scale bar reads sensibly at
  several zoom levels in each hemisphere.
- Compare a known Greenland trace's plotted position against its actual
  lat/lon (e.g. Summit Camp, ~72.58°N, −38.46°W) to sanity-check the
  EPSG:3413 forward projection.
