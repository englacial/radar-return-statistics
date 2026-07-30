# Interactive Viewer

The `web/` directory contains a browser-based map viewer built with Vite + TypeScript. It reads radar return statistics directly from the icechunk store over HTTP and renders them as a color-mapped Leaflet map.

## Configuration

Edit `web/src/config.ts` to add/remove stores (the `STORES` list — Antarctica,
ASE, UTIG, and Greenland today) or display variables. Each store URL must point
to an icechunk store accessible via HTTP range requests (e.g., an S3 bucket
with CORS enabled and public read). The viewer includes a store switcher, and
uses the `frame_collections` root attribute to label seasons.

## Development

```bash
cd web
npm install
npm run dev
```

Then open the local URL printed by Vite (typically `http://localhost:5173`).

## Production build

```bash
cd web
npm run build
```

Output goes to `web/dist/`. Serve it with any static file host.
