# Radiometric calibration checks: image-combine errors and surface saturation

Status: **planned** (experiments done, implementation not started).
Branch: `calibration-checks`. **No pushes to the online S3 stores** — all runs
against local stores / `outputs/` until explicitly approved.
Experiment details: `claude_notes/radiometric_calibration_experiments.md`.

## Goal

Detect two classes of radiometric calibration problems in OPR echograms and
**expose them as per-trace/per-frame values in the store** so downstream users
can choose their own filtering thresholds (we report values and status flags,
not pass/fail decisions):

1. **Image-combine errors** — gain mismatch between the "waveform playlist"
   images stitched together by `img_combine.m`, visible as a power step at the
   per-trace combine point `max(T_blank, T_comb + td_surface)`. Reported as a
   mean dB offset per frame; downstream rejection threshold likely ~3 dB.
2. **Surface saturation** — receiver/ADC clipping of the surface return,
   visible as a hard maximum in the surface-power-vs-altitude relationship
   instead of the expected ~−20 dB/decade range dependence.

The img-combine check runs **on by default** in the pipeline (config flag to
disable): the dataset is processed once and used many times, so the extra
download cost per frame is acceptable.

## Key facts from experiments

- Individual images load via
  `opr.load_frame(item, data_product=..., image=N, allow_unlisted_products=True)`.
- Availability probe over all 19 store seasons (HEAD on img_01 URLs,
  `claude_notes/calib_img_availability_probe.py`): images published for every
  season except **2013_Greenland_P3** (season-wide 404); scattered per-frame
  404s in 2013_Antarctica_Basler, 2014_Greenland_P3, 2019_Greenland_P3 (some
  may be single-image frames where no combining occurred — distinguishable
  from params). Frames without images get status `images_unavailable` and NaN
  offsets rather than an estimate from the combined product alone.
- `img_comb` vector location varies by season:
  `param_array.array.img_comb`, `param_records.array.img_comb` (newer),
  `param_combine.{array_param,combine}.img_comb` (2016-era), qlook variants;
  sometimes present but empty (2014_Greenland_P3). `param_records.radar.wfs.Tpd`
  gives per-waveform pulse durations.
- Direct img-vs-img overlap offset is precise (~0.2–0.3 dB per-trace std). A
  healthy 2016 DC8 frame still shows a systematic ~+1.8 dB offset — not
  surprising per user (combine thresholds are hard to get exactly right);
  reinforces reporting values rather than baking in a threshold.
- Saturation is already visible in the existing stores: 2014_Greenland_P3 has a
  flat hard ceiling (~−27 dB) across all ranges; 2013 has a ceiling plus a
  depressed low-altitude population. 2016–2019 look clean.
- `img_comb_weights_mode='auto'`, where used, hides gain mismatch in the
  combined product; record it when present (overlap check on raw images is
  immune).

## Design

New module `src/radar_return_statistics/calibration.py`. Two integration
stages: (1) a standalone CLI for baseline runs writing to
`outputs/calibration/`, (2) hooks in `processing.py` adding store variables
(local test stores only until go-ahead).

### Check 1: image-combine consistency (per frame, during processing)

1. **Param extraction** — `find_img_comb(ds)` / `find_tpd(ds)` searching the
   known attr paths in priority order; return the vector plus a provenance
   string. Record `img_comb_weights` / `img_comb_weights_mode` if present.
2. **Load images** `1..N` (N from param `imgs` list, else probe until 404).
3. **Metric — overlap offset**, per adjacent image pair (a, b):
   - Valid overlap per trace: `[max(T_blank, T_comb + td_surf), T_end_a − T_guard]`.
     If `img_comb` is empty/missing but multiple images exist, use
     `[td_surf + Tpd_b, T_end_a − Tpd_a]` (what T_comb/T_guard default to).
   - Interpolate image a onto image b's twtt grid (linear power), take
     per-trace `offset = median(dB_a − dB_b)` over bins where both are >6 dB
     above each image's own noise floor (median of its record tail) — avoids
     noise-floor bias.
4. **Computed metrics**, per frame and pair:
   - *mean offset* — mean of the per-trace offsets (the headline value;
     downstream threshold ~3 dB).
   - *offset MAD* — median absolute deviation (×1.4826) of the per-trace
     offsets along the frame: a robust spread / measurement-quality
     indicator, distinct from the mean (mean +1 dB, MAD 0.3 dB = clean and
     stable; mean +1 dB, MAD 4 dB = untrustworthy measurement).
   - *drift* — difference between the mean offsets of the two frame halves
     (catches within-frame gain changes that average out).
   - *status* ∈ `ok` / `no_combine` (single image or images never combined —
     check not applicable) / `images_unavailable` (combining occurred but img
     files not published) / `params_missing`.
   Store exposure carries the per-trace offsets and the per-frame worst-pair
   summary (see schema below); full per-pair tables (mean, MAD, drift,
   n_valid) go to the baseline-run parquet. MAD and drift are recomputable
   from the per-trace store variable, so they are not duplicated in the store.

### Check 2: surface saturation

**Which image contains the surface matters.** The combined product takes the
surface from img1 (low-gain) unless the surface falls outside img1's time
gate — the transition is capped at `T_end(img1) − T_guard`, so when
`td_surf > T_end(img1) − T_guard` (high AGL) the surface sample comes from a
higher-gain image and is *expected* to saturate; that's precisely why the
low-gain image exists. These traces must not drive the ceiling fit.

Per-trace, during processing (images/params already loaded for check 1):
- `surface_image_index` = smallest i with `td_surf ≤ T_end(img_i) − T_guard_i`
  (1 for single-image frames; note 2022/2023 BaslerMKB images share a
  full-range gate, so always 1 there). Exposed in the store so downstream
  users can filter on it directly.
- Optional (evaluate during implementation): `surface_peak_width_us` — width
  of the img1 surface peak (e.g. −6 dB width around the pick). Clipping
  broadens the compressed pulse and raises sidelobes, giving direct per-trace
  saturation evidence from the radargram. Cheap since images are already
  loaded; validate against the 2014_Greenland_P3 ceiling population before
  adding to the schema.

**Ceiling detection stays on the output products, not the radargrams**: a hard
max is a property of the surface-power-vs-range *population* across a season —
no single frame spans enough range diversity to reveal it — so it is a second
pass over a completed store (cheap, no echogram loads; runs from
`surface_power_dB`, `surface_twtt`, `qc_surface_pass`, `frame_index`).
Radargram loading during processing contributes the per-trace ingredients
above; the population fit does not benefit from raw radargram access.

1. Range to surface `r = c·surface_twtt/2`; restrict to
   `surface_image_index == 1` traces (fall back to all traces with a caveat
   flag for stores written before the index exists).
2. **Ceiling estimate per season**: binned 99th-percentile power vs `log10(r)`.
   - *Slope test*: robust fit of the binned upper quantile; unsaturated
     ≈ −20 dB/decade, saturated ≈ 0.
   - *Pile-up test*: fraction of traces within 1 dB of the season ceiling.
3. **Per-trace value** `surface_ceiling_margin_dB` = season ceiling −
   surface_power_dB (small/zero ⇒ at ceiling; downstream picks the cutoff).
   Written back by the second pass alongside season-level attrs.
4. Diagnostics: per-season hexbin figures with −20 dB/decade reference and
   binned upper-quantile overlay.
5. Later/optional: investigate the 2013-style depressed low-altitude
   population (blanking?).

### Store schema (local stores only until approved)

Per-trace variables on `slow_time`:
- `img_comb_offset_dB` — float32, one value per trace: the signed offset of
  whichever image pair has the largest |offset| at that trace (NaN where
  undefined). Per-pair detail is not stored per trace; it lives in the
  per-frame attrs and baseline parquet.
- `surface_image_index` — int8, −1 unknown.
- `surface_ceiling_margin_dB` — float32, written by the second pass.
- (optional, pending validation) `surface_peak_width_us` — float32.

Per-frame attrs arrays (parallel to `frame_names`):
- `frame_img_comb_offset_dB` — scalar per frame: signed mean offset of the
  worst pair (largest |mean|).
- `frame_img_comb_status` — string per frame (see statuses above).

Season-level attrs from the second pass:
`season_saturation_slope_dB_per_decade`, `season_ceiling_dB`,
`season_ceiling_pileup_fraction` (parallel lists keyed by season name).

None of these are QC-masking; they are values downstream users filter on.
Update `docs/architecture.md` + viewer variable list when implemented.

Config: `processing.calibration.img_combine: true` (default true; disable for
quick test runs).

## Implementation steps

1. `calibration.py`: param extraction + image loading + overlap-offset +
   `surface_image_index`, pure and testable; unit tests with synthetic
   two-image frames (known injected gain step) in
   `tests/unit/test_calibration.py`.
2. Saturation second-pass functions (ceiling/slope/pile-up/margin) on plain
   numpy arrays; unit tests with synthetic clean vs clipped populations.
3. Standalone CLI (`--check {img-combine,saturation}`,
   `--sample-per-segment N`) writing parquet + figures to
   `outputs/calibration/<name>/` for baseline runs.
4. Baseline runs: saturation on all five stores (cheap); img-combine on a
   sample of Greenland P3 + ASE DC8 + one UTIG season. Review distributions
   with user. Evaluate `surface_peak_width_us` against the 2014 ceiling
   population here.
5. Integrate into `processing.py` behind the config flag (default on) +
   second-pass command; test against a local store (`test_config.yaml`).
   **Stop before any production store update.**

## Open questions

- Is the ~1.8 dB img-to-img offset on a healthy DC8 frame instrumental
  (Tsys/deconv per waveform) or partly an interpolation artifact? Baseline run
  will show whether it is season-stable.
- 2014_Greenland_P3 lists 7 images with empty `img_comb` — confirm what the
  combined file actually contains for that era before trusting the
  Tpd-default overlap window there.
