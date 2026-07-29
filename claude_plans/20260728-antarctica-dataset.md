# Plan: Full-Antarctic dataset (analogous to `greenland`)

Status: **completed** (2026-07-28) — all 15 seasons processed into
`s3://opr-radar-metrics/icechunk/antarctica`: **5,646 frames / 244,408 traces**.
Every season checked after its run (counts, NaN rates, value ranges); DC8 seasons
verified value-identical against `ase` on 186+208+176+199 shared frames, 2013_P3
against `crosssystem` (13 frames), 2008_BaslerJKB a strict subset of `utig`'s 299
frames. Spot re-processing regression passed on 3 frames across 3 systems. Maps
in `outputs/maps/antarctica/`. Zero-yield seasons (no bed picks in OPR):
2016/2018_BaslerJKB, and all but one segment of 2017_BaslerJKB.

Decisions (from Thomas, 2026-07-28):
- `min_ice_thickness_m: 100`
- Include the four BaslerJKB (UTIG) seasons at 10s decimation, despite overlap with the 5s `utig` store
- Exclude ground/GHOST seasons (2018_Antarctica_Ground, 2024_Antarctica_GroundGHOST2)
- Process one season at a time; check results after each; stop on errors/anomalies
- Config: `config/config_antarctica.yaml` (full collection list); per-season runs use scratch copies with a single collection

Goal: build `s3://opr-radar-metrics/icechunk/antarctica` covering the full Antarctic
continent, analogous to the existing `greenland` store (5,452 frames / 204k traces),
using the same metrics, decimation, and QC philosophy.

## Survey results (2026-07-28)

Full-continent `query_frames` returns **11,304 frames** across 24 collections:

| Collection | Frames | Expected status |
|---|---|---|
| 2004_Antarctica_P3chile | 127 | Exclude — no OPR layer picks |
| 2009_Antarctica_DC8 / TO / TO_Gambit | 2,246 | Exclude — missing `Heading` (QC requires it) |
| 2010/2011_Antarctica_DC8, 2011_TO | 1,511 | Exclude — missing `Heading` |
| 2012_Antarctica_DC8 | 626 | Include (already in `ase` store for G-H subregion) |
| 2013_Antarctica_Basler | 373 | Verify → include |
| 2013_Antarctica_P3 | 280 | Include (verified: processes fine, matches crosssystem store) |
| 2014_Antarctica_DC8 | 994 | Include |
| 2016_Antarctica_DC8 | 1,112 | Include |
| 2017_Antarctica_Basler | 433 | Verify → include |
| 2017_Antarctica_P3 | 185 | Include |
| 2018_Antarctica_DC8 | 794 | Include |
| 2019_Antarctica_GV | 402 | Include |
| 2022/2023_Antarctica_BaslerMKB | 1,050 | Verify → include |
| 2008/2016/2017/2018_Antarctica_BaslerJKB | 1,071 | **Decide** — UTIG platform, already has its own `utig` store (5s decimation) |
| 2018_Antarctica_Ground, 2024_Antarctica_GroundGHOST2 | 100 | **Decide** — ground traverses; heading-change QC (deg/km) may behave oddly at low speed |

Likely scope: **~6,200–7,400 frames** depending on the BaslerJKB/ground decisions —
larger than but comparable to greenland.

## Open decisions (ask before running)

1. **BaslerJKB (UTIG) seasons**: include in `antarctica` for completeness (at 10s
   decimation, duplicating the 5s `utig` store), or exclude to keep `antarctica`
   CReSIS-airborne-only like `greenland`? Recommendation: exclude initially; they can
   be appended later since incremental processing supports adding collections.
2. **Ground traverses** (2018_Ground, GHOST2): recommendation: exclude initially,
   evaluate heading-QC behavior on a sample later.

## Run findings (2026-07-28)

- **2016_Antarctica_BaslerJKB yields 0 frames**: OPR has only `standard:surface`
  picks for this season (no bed layer at all). Consistent with the utig store,
  which also contains zero 2016 frames and only 3 from 2017_BaslerJKB. Expect the
  same for 2018_BaslerJKB. These collections stay in the config (picks may be
  added to OPR later), but note every `--reprocess`-free rerun will re-attempt
  their frames since skipped frames are never marked processed.
- 2019_Antarctica_GV publishes bed picks as `:bottom`; handled via fallback in
  `processing.py` (added 2026-07-28).

## Phase 1 — per-collection eligibility probe

Small script (in `claude_notes/`): for each candidate collection, load 2–3 sample
frames and check (a) `Heading` variable present, (b) `standard:surface` /
`standard:bottom` layers present, (c) `process_frame` succeeds with production QC.
Output a table confirming the include/exclude list above. This replaces guessing from
config comments.

## Phase 2 — config

`config/config_antarctica.yaml`, modeled on `config_greenland.yaml`:

```yaml
opr:
  cache_dir: "./radar_cache/"
region:
  area: "antarctic"        # note: emits "2 invalid geometries fixed" warning (Adelie_Coast,
                           # Jason_Peninsula) — harmless, geometries are auto-fixed
query:
  collections: [<from Phase 1>]
  max_items: null
processing:
  data_product: "CSARP_standard"
  decimate_interval: "10s"   # match greenland (utig used 5s — do NOT copy that)
  layer_margin_m: 50
  ice_permittivity: 3.17
  max_workers: 20
  checkpoint_every: 200      # runner supports periodic commits — essential at this scale
  noise: {pre_surface: {start_offset_us: 1.0, end_offset_us: 1.0},
          post_bed:   {start_offset_us: 5.0, end_offset_us: 5.0}}
qc:                          # mirror greenland
  max_heading_change_deg_per_km: 2.0
  min_ice_thickness_m: null
  min_agl_m: 50
  min_bed_snr_db: null
  min_traces_after_qc: 15
store:
  backend: "s3"
  s3_bucket: "opr-radar-metrics"
  s3_prefix: "icechunk/antarctica"
  s3_region: "us-west-2"
  remove_out_of_scope: false
```

QC note: greenland used `min_ice_thickness_m: null`; `crosssystem`/`utig` used 100.
Staying with null to be "analogous to greenland" — confirm this is intended.

## Phase 3 — dry run

Local-store variant of the config (backend: local, `max_items: ~20`) spanning 2–3
collections; sanity-check trace counts, NaN rates, and maps via `visualize_map`.

## Phase 4 — production run

- One collection at a time (edit `query.collections` incrementally or run with the
  full list — `processed_frames` tracking + `checkpoint_every` make interrupted runs
  resumable either way). Per-collection runs give cleaner commit history and bound
  failure domains.
- Order: start small (2013_Antarctica_P3, 280 frames) to validate S3 writes, then
  largest seasons.
- Throughput reference: smoke run ≈ 8 s/frame single-worker including download; with
  20 workers expect very roughly 500–1500 frames/hour → order of a day of wall time
  total, dominated by OPR download bandwidth.
- Disk: `radar_cache/` will grow large (full-resolution frames); consider
  `cache_dir: null` (streaming) if disk is a concern, at the cost of re-download on
  reruns.

## Phase 5 — validation

- Spot-check: re-process a handful of frames and compare against the new S3 store
  (same pattern as `tests/integration/test_regression.py`).
- Overlap consistency: frames shared with `ase` (2012/2014/2016/2018 DC8 in G-H) and
  `crosssystem` should produce identical values where QC configs agree (note: ase and
  crosssystem used different `min_ice_thickness_m`, so trace *sets* may differ).
- Maps of all metrics for a visual once-over.
- Optionally add `Data_20131120_01_007` (or similar) as a second regression-test
  target pointing at the antarctica store.

## Risks / notes

- Antimeridian: `query_frames` handles it (`antimeridian.fix_geojson`); the 2013_P3
  spatial outlier near 180° noted in `config_ase.yaml` is only an issue for regional
  subsetting, not for a full-continent run.
- A frame that fails QC (e.g. missing Heading) logs a warning and is *not* recorded as
  processed, so it will be retried on every incremental run — keep known-bad
  collections out of `query.collections` rather than relying on per-frame failure.
- S3 costs: ~7k frames at greenland's ratio (~37 traces/frame) ≈ 260k traces — same
  order as greenland; icechunk store size will be modest, OPR egress is the main cost.
