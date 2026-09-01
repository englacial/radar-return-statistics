This project processes radar sounder data retrieved through the xOPR library and
extracts per-frame return statistics, storing results in an icechunk versioned store.

xOPR: https://github.com/englacial/xopr

## Output metrics

Per-trace (resampled) values stored with `slow_time` dimension:
* `surface_twtt` - surface two-way travel time (peak within margin)
* `bed_twtt` - bed two-way travel time (peak within margin)
* `surface_elevation` - surface WGS84 elevation from layer picks
* `bed_elevation` - bed WGS84 elevation from layer picks
* `surface_power_dB` - surface peak power in dB
* `bed_power_dB` - bed peak power in dB
* `required_surface_snr_dB` - surface-to-bed power ratio corrected for geometric spreading (dB);
  matches RSSNR definition from https://github.com/thomasteisberg/required_surface_snr
* `pre_surface_noise_dB` - median power (dB) in a window before the surface pick, used
  as a noise-floor estimate. Window is `[twtt[0] + pre_surface.start_offset_us,
  surface - pre_surface.end_offset_us]`.
* `post_bed_noise_dB` - median power (dB) in a window after the bed pick. Window is
  `[bed + post_bed.start_offset_us, twtt[-1] - post_bed.end_offset_us]`.
  Both noise variables are configured by `processing.noise` (defaults: pre 1/1 us,
  post 5/5 us). NaN if the requested window is empty for that trace.
* `record_tail_noise_dB` - median power (dB) in the window
  `[end - record_tail.start_offset_us, end - record_tail.end_offset_us]`
  (defaults 12/7 us) near the record end. The end gap blanks the
  post-processing rolloff seen in the final ~2-8 us of many seasons.
  Pick-independent noise-floor estimate; an upper bound where deep returns
  reach the window.
* `post_bed_noise_interp_dB` / `post_bed_peak_interp_dB` / `post_bed_std_interp_dB` -
  median / peak / std of power (dB) in the post-bed window anchored at the bed
  pick where present (median then identical to `post_bed_noise_dB`), else at a
  bed twtt linearly interpolated between the segment's adjacent picks. Defined
  on censored traces inside the picked span; NaN outside it. A peak well above
  the median flags bed-like energy below the interpolated bed that went unpicked.
* `bed_pick_available` - bed pick aligned to this trace (pre-QC)
* `bed_pick_attempted` - trace lies within the segment's picked span (between the
  segment's first and last finite bed pick); leading/trailing gaps are excluded
  from missingness analyses as segment-edge quirks
* `bed_pick_quality` - OPR layer quality flag (1 good / 2 moderate / 3 derived),
  -1 where no pick or flag unavailable
* `img_comb_offset_dB` - residual seam offset of the frame's worst image pair:
  the power step actually present in the combined product at the image-combine
  transition, after recovering the effective per-image weights the combine
  applied. Positive = the earlier (shallower) image is brighter. The pair is
  fixed per frame (`img_comb_pair`); NaN where unmeasured (see
  `frame_img_comb_status`). A downstream rejection threshold around 3 dB on
  the frame mean is typical; no threshold is applied in the store.
* `img_comb_pair` - which image pair `img_comb_offset_dB` refers to
  (1 = img1/img2, 2 = img2/img3), -1 undefined
* `surface_source_image_index` - which image the combined product's surface
  sample came from (the combine transition is capped at the earlier image's
  gate end minus its guard, so at high AGL the surface comes from a
  higher-gain image). **Provenance flag, not a validity verdict**: index >= 2
  surfaces are *more likely to be saturated* and may carry a season-dependent
  low bias (~15-20 dB measured on 2014/2017 Greenland P3's img2 surface
  response), but for high-altitude DC8 seasons surface-in-img2 is the normal
  operating geometry. Downstream users decide filtering; the per-season
  `cross_cap_step_db` attr is the measured bias estimate. -1 unknown
  (images unavailable, e.g. 2013_Greenland_P3).
* `surface_ceiling_margin_dB` - season ceiling (clip level) minus surface
  power, computed against the trace's own source-image population's ceiling.
  Written by the saturation second pass; NaN until it runs and wherever no
  credible ceiling fit exists (a clean season has no ceiling).
* `record_end_twtt` - twtt (s) of the last sample in the record; lets users
  reconstruct the record-end-relative windows (post-bed, record tail). Never
  QC-masked.
* `qc_pass` / `qc_surface_pass` / `qc_heading_pass` / `qc_agl_pass` - full QC AND,
  pick-independent AND, and individual pick-independent check flags. Surface-side
  metrics are masked by `qc_surface_pass`; bed-side metrics by `qc_pass`.
* `frame_id` - source frame identifier

Coordinates: `latitude`, `longitude`, `elevation`

Root attributes `frame_names`, `frame_collections`, `frame_bed_pick_fraction`,
`segment_bed_pick_fraction`, `frame_img_comb_offset_dB` (worst-pair mean
residual, None where unmeasured), `frame_img_comb_status` (`ok` /
`no_combine` / `images_unavailable` / `partial_images` / `load_error` /
`invalid_params` / `params_missing` / `insufficient_overlap` /
`weight_recovery_failed` / `disabled`), and `frame_img_comb_weights_mode`
are parallel lists (one entry per unique frame). The per-trace `frame_index`
array indexes into them.

The saturation second pass adds a root `saturation` attr: per-season fits
keyed by season name (per population `img1` / `img2` — or `all` where the
source index is unavailable — level, status, support, CI; plus
`cross_cap_step_db`, the measured img1-vs-img2 population offset at the gate
cap), the fit parameters, a method version, and a science-data fingerprint
(trace count + hash of `processed_frames`). Science appends make the second
pass stale (the runner warns); calibration-only writes do not.

### Missing bed picks as censored observations

Traces where picking was attempted but no bed was found (usually low bed SNR)
can be identified as:

```python
censored = qc_surface & attempted & ~available
```

where those arrays come from `qc_surface_pass`, `bed_pick_attempted`, and
`bed_pick_available`. These traces retain surface power and noise-floor
estimates (`record_tail_noise_dB`), so they can enter analyses as
right-censored required-surface-SNR observations rather than silently missing
data. Gate on `frame_bed_pick_fraction` / `segment_bed_pick_fraction` to set
the required picking-effort level. Segments with no bed layer at all (picking
never attempted) are excluded from the store entirely, so they cannot
contaminate missingness estimates.

Stores written before 2026-07-29 predate the `record_tail_noise_dB` /
`bed_pick_*` / `qc_*_pass` columns and the split masking semantics; there,
`qc_pass == False` implies all metrics NaN.

## Architecture

A Python runner flat-maps over independent frames. Icechunk
handles versioning and incremental tracking (processed frame IDs stored in the zarr group).

### Modules

* `config.py` - loads YAML config (`normalize_config` applies shared defaults)
* `processing.py` - per-frame metric extraction (ports `extract_layer_peak_power` algorithm)
* `calibration.py` - radiometric calibration checks: image-combine residual
  seam offsets (effective-weight recovery + weight-corrected overlap) and
  saturation ceiling fits (per-source-image populations)
* `store.py` - icechunk read/write, frame tracking, commits; appends
  auto-migrate (missing variables are created backfilled with fill values)
* `runner.py` - orchestration: query, diff, process (parallel), write, commit
* `__main__.py` - CLI entry point via click
* `saturation_pass.py` - second pass: fit season ceilings over a completed
  store, write `surface_ceiling_margin_dB` + season attrs
* `calibration_backfill.py` - re-run the img-combine check for frames with
  retryable statuses, updating calibration variables in place
* `calibration_cli.py` - standalone read-only baseline runs (parquet + figures)

### How to run

```bash
uv run python -m radar_return_statistics config/config_antarctica.yaml
uv run python -m radar_return_statistics config/config_antarctica.yaml --reprocess  # ignore existing frames
uv run python -m radar_return_statistics config/config_antarctica.yaml -v           # debug logging
```

Production configs: `config_greenland.yaml`, `config_antarctica.yaml`,
`config_ase.yaml`, `config_utig.yaml`, `config_crosssystem.yaml`;
`test_config.yaml` is a small local-store test run.

### Storage

Supports both local filesystem and S3 backends, configured via `store.backend` in the
YAML config. S3 uses `icechunk.s3_storage` with `from_env=True` for credential chain.
Set `AWS_PROFILE` for local development.

### Processing pipeline

1. Load config, open/create icechunk repo
2. Query frames from OPR matching region geometry
3. Filter to unprocessed frames (or all if `--reprocess`)
4. Process frames in parallel (`ProcessPoolExecutor`, spawn context)
5. Write results sequentially to icechunk (append along `slow_time`)
6. Commit with summary message; `processing.checkpoint_every: N` additionally
   commits every N frames so long runs are resumable

During step 4 each frame also runs the image-combine calibration check
(`processing.calibration.img_combine`, default on; ~3x wall time and ~2.5x
download volume per frame from the individual image loads). Calibration
failures degrade to a per-frame status and never fail a frame. After the
science data is complete, run the saturation second pass:

```bash
uv run python -m radar_return_statistics.saturation_pass config/config_antarctica.yaml
uv run python -m radar_return_statistics.calibration_backfill config/config_antarctica.yaml  # retry load_error/disabled frames
```

### Testing

`tests/unit` covers processing, config, and store logic against synthetic
frames. `tests/integration` (pytest marker `integration`, requires network)
runs the full pipeline against a local store and includes a regression test
(`test_regression.py`) that re-processes a known frame and compares it against
the public crosssystem S3 store — any mismatch means the algorithm changed
since that store was built.

### Access and visualization

See `docs/data_access.md` for reading the stores from Python/JavaScript.
`visualize_map` renders static per-variable maps; `visualize_frame` renders
per-frame profiles. The `web/` directory contains the browser viewer
(icechunk-js + Leaflet, see `docs/viewer.md`), deployed to GitHub Pages by
`.github/workflows/deploy-pages.yml`.