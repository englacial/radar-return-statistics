# Radiometric calibration checks: image-combine errors and surface saturation

Status: **planned** (experiments done, Codex review incorporated 2026-08-31,
implementation not started).
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
download cost per frame is acceptable. Cost is benchmarked before any
production run (see step 5).

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
- **Effective combine weights are recoverable by differencing** the combined
  product against each image over the section it sourced
  (`claude_notes/calib_effective_weights.py` / `calib_residual_step.py`):
  `w_i = median(combined − img_i)` over section i is a per-frame constant with
  ~zero spread. 2016 DC8: all w=0 (combined = raw stitch, so its +1.76 dB raw
  offset is a *real* seam step). 2018_Antarctica_DC8 (weights_mode='auto' on
  the array path): w2=+107.6 dB, w3=+114.8 dB — the raw img files aren't even
  on a common gain reference there, so raw img-vs-img offsets are meaningless
  without weight recovery; the weight-corrected residual seam step is
  +0.97 / +1.36 dB.
- `img_comb_weights_mode='auto'` declared on the relevant (array/combine) path
  in 2 of 19 seasons (2018_Antarctica_DC8, 2017_Antarctica_Basler; 2019_GV
  qlook-only); most seasons declare no weights fields at all
  (`claude_notes/calib_weights_survey.py`). Because weights are recovered
  empirically, the declared mode is recorded for provenance only.

## Design

New module `src/radar_return_statistics/calibration.py`. Two integration
stages: (1) a standalone CLI for baseline runs writing to
`outputs/calibration/`, (2) hooks in `processing.py` adding store variables
(local test stores only until go-ahead).

### Check 1: image-combine consistency (per frame, during processing)

1. **Param extraction** — `find_img_comb(ds)` / `find_tpd(ds)` searching the
   known attr paths in priority order; return the vector plus a provenance
   string.
2. **Image count and waveform mapping** — the image count comes from the
   params `imgs` list (primary), each entry being a wf-adc matrix; the
   waveform index for image i is the wf entries of `imgs[i]` (e.g. 2016 DC8
   `imgs[0] = [[1,...],[1..6]]` → wf 1, adcs 1–6). `Tpd_img_i =
   radar.wfs.Tpd[wf−1]` — **never index Tpd by image number**: Tpd arrays can
   carry per-wf-adc duplicates (2018 P3: `[1,1,3,3,10,10] µs` for images with
   wf pairs). Warn if the wfs of one image disagree on Tpd. Only if `imgs` is
   missing do we fall back to a bounded probe (max 4 images), with typed
   error handling: HTTP 404 = image permanently absent; timeouts/5xx =
   transient → bounded retry, then status `load_error` (retryable later), so
   a transient failure or a missing intermediate file is never recorded as
   "no more images".
3. **Effective weight recovery** — per image i, compute the per-trace
   `median(combined − img_i)` over the section of the combined product
   sourced from img_i (between the adjacent per-trace combine boundaries,
   trimmed 0.3 µs clear of the blend zone); the frame's effective weight
   `w_i` is the median over traces. Empirically this is a per-frame constant
   with ~zero spread (see key facts), and it captures *whatever* scaling the
   combine applied — declared static weights, 'auto' weights, or undeclared
   unit/gain differences between the img files and the combined product
   (2018 DC8: >100 dB). Sanity check: if the within-section spread
   (MAD over traces) exceeds ~0.1 dB, weight recovery is unreliable → status
   `weight_recovery_failed`, raw offsets reported to parquet only.
   `img_comb_weights` / `img_comb_weights_mode` from params are recorded for
   provenance but are not needed for the measurement.
4. **Metric — residual seam offset**, per adjacent image pair (a, b): the
   overlap offset of the *weight-corrected* images,
   `(dB_a + w_a) − (dB_b + w_b)`, which equals the step actually present in
   the combined product at the seam — the quantity downstream users care
   about, valid regardless of weights mode. The raw img-vs-img offset is
   derivable (`residual − (w_a − w_b)`) and goes to the parquet as a
   diagnostic of the underlying pre-combine calibration. Details:
   - Alignment: images are subset to the same decimated slow_time selection
     as the combined frame, matched by index (verify identical trace counts;
     else nearest-time with the pipeline's 1 s tolerance, unmatched → NaN).
     Data orientation normalized to (twtt, trace) as elsewhere in
     `processing.py`; the two images keep their own twtt grids and image a is
     interpolated onto image b's grid (linear power).
   - Valid overlap per trace: `[max(T_blank, T_comb + td_surf), T_end_a − T_guard]`.
     If `img_comb` is empty/missing but multiple images exist, use
     `[td_surf + Tpd_b, T_end_a − Tpd_a]` (what T_comb/T_guard default to).
     `td_surf` is the frame's `Surface` variable (the value `img_combine`
     used), NaN → 0 per the OPR convention.
   - Per-trace `offset = median((dB_a + w_a) − (dB_b + w_b))` over bins where
     both images are >6 dB above their own noise floor (median of the last-2µs
     record tail; if an image is too short for a tail window, use its global
     10th percentile instead — the weight shifts both signal and floor
     equally, so the SNR gate is weight-independent). Require ≥10 valid bins
     per trace (else NaN) and ≥30 valid traces per pair (else pair status
     `insufficient_overlap`).
   - Sign convention (documented in variable attrs): positive = the earlier
     (lower-index, shallower) image is brighter than the later one.
5. **Computed metrics**, per frame and pair:
   - *per-trace offset* — as above (median over bins within the trace).
   - *mean offset* — mean of the per-trace offsets along the frame (the
     headline value; downstream threshold ~3 dB).
   - *offset MAD* — median absolute deviation (×1.4826) of the per-trace
     offsets: a robust spread / measurement-quality indicator, distinct from
     the mean (mean +1 dB, MAD 0.3 dB = clean and stable; mean +1 dB, MAD
     4 dB = untrustworthy measurement).
   - *drift* — difference between the mean offsets of the two frame halves.
   - *worst pair* — the pair with the largest |mean offset|, chosen **once
     per frame**; the store carries that single pair's per-trace series plus
     its pair index, so frame-level MAD/drift for the reported pair are
     exactly recomputable from the store (no per-trace pair switching, no
     max-selection bias). All pairs' full tables go to the baseline parquet.
6. **Status** (per frame; per-pair statuses in parquet):
   `ok` / `no_combine` (single image or never combined — check not
   applicable) / `images_unavailable` (404 on required images) /
   `partial_images` (some pairs measurable, some not) / `load_error`
   (transient failure, retryable) / `invalid_params` (params present but
   inconsistent) / `params_missing` / `insufficient_overlap` /
   `weight_recovery_failed` (residuals unreliable; raw offsets in parquet
   only) / `disabled`.
   **Calibration never fails a frame**: every calibration step is wrapped so
   an error degrades to a status, and the frame's science metrics are written
   regardless. Because `processed_frames` blocks automatic retries, a
   calibration-only backfill command re-runs the check for frames with
   retryable statuses (`load_error`, `disabled`) and updates the calibration
   variables in place without touching science metrics.

### Check 2: surface saturation

**Which image supplied the combined surface sample matters.** The combined
product takes the surface from img1 (low-gain) unless the surface falls
outside img1's time gate — the transition is capped at `T_end(img1) −
T_guard`, so at high AGL the surface sample comes from a higher-gain image
and is *expected* to saturate; that's precisely why the low-gain image
exists. These traces must not drive the ceiling fit.

Per-trace, during processing (images/params already loaded for check 1):
- `surface_source_image_index` — the image the combined product's surface
  sample came from: smallest i with `T_start(img_i) ≤ td_surf ≤ T_end(img_i)
  − T_guard_i`, evaluated with the frame's original `Surface` value (what
  `img_combine` used — not the later peak-refined `surface_twtt`), NaN → 0
  per the OPR convention. 1 for single-image frames (2022/2023 BaslerMKB
  images share a full-range gate → always 1). −1 when it cannot be
  determined: surface outside every gate, or images unavailable — the gate
  ends are properties of the image files, not the params, so frames without
  image files (2013_Greenland_P3) get −1 unless single-waveform.
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

1. Range to surface `r = c·surface_twtt/2`; restrict to
   `surface_source_image_index == 1` traces where the variable exists and is
   populated; else all traces. The population used is recorded per season as
   `ceiling_fit_population` ∈ {`img1_only`, `all_traces`} (the latter covers
   both pre-schema stores and seasons like 2013_Greenland_P3 where indices
   are −1 — the saturation signal there is too important to drop).
2. **Ceiling estimate per season**: binned upper-quantile (99th) power vs
   `log10(r)`.
   - *Plateau detection first*: a clean season has no physical ceiling, so a
     season-wide max would mislabel ordinary close-range returns. Fit the
     binned upper quantile vs `log10(r)` robustly (Theil–Sen); declare a
     plateau only when the fitted slope is ≫ shallower than −20 dB/decade
     (threshold set from baseline-run distributions, with bootstrap CI) over
     sufficient support: minimum traces per bin, minimum occupied bins, and
     minimum log-range span (defaults to fix during implementation; all
     recorded as metadata).
   - Season fit status: `fit_ok` / `no_plateau` / `insufficient_support`.
   - *Pile-up test*: fraction of traces within 1 dB of the ceiling
     (only when `fit_ok`).
   - *Regime check*: during the baseline run, repeat the fit per segment and
     inspect ceiling multimodality within a season (mixed receiver/waveform
     settings); if a season is multimodal, fall back to per-segment ceilings
     before writing per-trace margins.
3. **Per-trace value** `surface_ceiling_margin_dB` = ceiling −
   surface_power_dB, written **only when the season (or segment) fit is
   `fit_ok`**; NaN otherwise — no credible plateau means no margin, never a
   misleading number.
4. Diagnostics: per-season hexbin figures with −20 dB/decade reference and
   binned upper-quantile overlay.
5. Later/optional: investigate the 2013-style depressed low-altitude
   population (blanking?).

### Store schema (local stores only until approved)

Per-trace variables on `slow_time` (all **always emitted**, with NaN/−1
placeholders when disabled or unavailable, so appends stay schema-uniform):
- `img_comb_offset_dB` — float32: the frame's worst pair's per-trace signed
  **residual seam offset** (weight-corrected — the step actually present in
  the combined product; NaN where undefined). Attrs document the sign
  convention and that the pair is fixed per frame.
- `img_comb_pair` — int8: which image pair the offset series refers to
  (1 = img1/img2, 2 = img2/img3, …; −1 undefined).
- `surface_source_image_index` — int8, −1 unknown.
- `surface_ceiling_margin_dB` — float32, created at schema time (all-NaN) and
  filled by the second pass.
- (optional, pending validation) `surface_peak_width_us` — float32.

Per-frame attrs arrays (parallel to `frame_names`):
- `frame_img_comb_offset_dB` — signed mean residual offset of the worst pair
  (float).
- `frame_img_comb_status` — string (statuses above).
- `frame_img_comb_weights_mode` — string ('' / 'fixed' / 'auto'), declared
  params mode, provenance only (measurement is weight-mode-independent).
Recovered effective weights `w_i` and raw (uncorrected) offsets per pair go
to the baseline parquet, not the store.

Season-level attrs from the second pass, as a dict keyed by season name (no
bare parallel lists): per season — `ceiling_dB`, `slope_dB_per_decade`,
`slope_ci`, `pileup_fraction`, `fit_status`, `ceiling_fit_population`,
`n_traces`, `n_bins`, `log_range_span`, plus global `calibration_method_version`
and every fixed threshold used. Second-pass results also record the store
snapshot/commit they were computed from; appends/removals/reprocessing after
that snapshot mean the second pass must be re-run (the runner prints a warning
when the recorded snapshot is stale).

None of these are QC-masking; they are values downstream users filter on.

**Migration/backfill**: `_zarr_append` silently skips variables absent from an
existing store (`store.py:88`), so existing stores need explicit migration: a
backfill command creates the new arrays sized to the current `slow_time`
(NaN/−1), after which appends carry them forward. `runner.py`'s frame-attr
plumbing (`batch_frame_attrs`, currently `dict[str, dict[str, float]]` with a
`float()` cast) is extended to typed per-frame attrs so string statuses ride
along. Direct `process_frame` callers whose configs bypass `load_config`
defaults get safe behavior via `config.get(...)` defaults inside
`processing.py` (calibration off → placeholder outputs, never a KeyError).

Config: `processing.calibration.img_combine: true` (default true; disable for
quick test runs) and `processing.calibration.image_load_retries`.

## Implementation steps

1. `calibration.py`: param extraction + wf→image Tpd mapping + image loading
   (typed 404/transient handling) + effective-weight recovery + residual
   overlap offset + worst-pair selection + `surface_source_image_index`,
   pure and testable. Unit tests (`tests/unit/test_calibration.py`):
   synthetic combined+image frames with known injected seam step and known
   per-image scalings (weights must be recovered and the residual must equal
   the injected step, independent of the scalings); differing twtt grids and
   dimension orders; `weight_recovery_failed` on inconsistent sections;
   short-image noise-floor fallback; insufficient-overlap and
   transient-vs-404 statuses.
2. Saturation second-pass functions (plateau fit / pile-up / margin) on plain
   numpy arrays; unit tests with synthetic clean, clipped,
   mixed/insufficient-support, and multimodal populations (clean season must
   yield `no_plateau` and all-NaN margins).
3. Standalone CLI (`--check {img-combine,saturation}`,
   `--sample-per-segment N`) writing parquet + figures to
   `outputs/calibration/<name>/`. Sampling: frames grouped by OPR segment
   (`YYYYMMDD_SS`), N chosen per segment with a fixed seed; a run manifest
   (JSON) records method version, store snapshot, parameters, seed, selected
   frames, and per-frame failures. Add `pyarrow` to `pyproject.toml` (parquet
   currently has no engine dependency).
4. Baseline runs: saturation on all five stores (cheap); img-combine on a
   sample of Greenland P3 + ASE DC8 + one UTIG season, including at least one
   real frame per known param layout (2016-era `param_combine`, newer
   `param_array`, empty-`img_comb` 2014 era). Review distributions with user;
   set plateau-slope threshold; evaluate `surface_peak_width_us` against the
   2014 ceiling population; run the per-segment regime check.
5. Integrate: `processing.py` (behind config flag, default on),
   `runner.py` (typed frame attrs), `store.py` (schema creation, migration
   backfill, second-pass in-place updates). Benchmark download volume,
   runtime, and worker temp-disk growth on a sample before default-on
   production use (images multiply per-frame downloads ~×N; load pairs
   sequentially and release each image after use; mind the recently fixed
   /tmp cache growth). Tests: append to a pre-calibration store, enabled→
   disabled→enabled transitions, backfill of retryable statuses, second-pass
   staleness warning. Test against a local store (`test_config.yaml`).
   **Stop before any production store update.**
6. Docs & viewer (with integration): `docs/architecture.md`,
   `docs/data_access.md`, `docs/processing.md`; viewer handles the int8
   categorical variables (`surface_source_image_index`, `img_comb_pair`)
   as integers, not via the float typed-array path.

## Open questions

- Is the ~1.8 dB img-to-img offset on a healthy DC8 frame instrumental
  (Tsys/deconv per waveform) or partly an interpolation artifact? Baseline run
  will show whether it is season-stable.
- 2014_Greenland_P3 lists 7 images with empty `img_comb` — confirm what the
  combined file actually contains for that era before trusting the
  Tpd-default overlap window there.
