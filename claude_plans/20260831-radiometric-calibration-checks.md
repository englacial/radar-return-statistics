# Radiometric calibration checks: image-combine errors and surface saturation

Status: **steps 1–6 implemented 2026-08-31** (baseline:
`claude_notes/calibration_baseline_results.md`; two-regime resolution:
`claude_notes/two_regime_investigation.md`; integration notes:
`claude_notes/calibration_integration_notes.md`). Verified against a local
smoke store; awaiting user review before any production run. **Reminder owed
to user: revisit the never-firing piecewise saturation model once more data
has been processed.**

## User decisions from baseline review (2026-08-31)

1. **Two-regime seasons — RESOLVED** (`claude_notes/two_regime_investigation.md`):
   the second "regime" is surface-from-img2 traces forced in by the img1 gate
   cap (`T_end(img1) − T_guard`); params ruled out (identical settings both
   sides of the step within single frames). User decisions on the follow-ups
   (2026-08-31):
   - `surface_source_image_index` is a **provenance flag, not a validity
     verdict**: img2-sourced surfaces are *more likely saturated* and may
     carry a season-dependent low bias (~15–20 dB measured on 2014/2017 GL
     P3's img2 surface response), but for high-altitude DC8 seasons the
     surface landing in img2 is the normal operating geometry — blanket
     exclusion would discard ~19–27% of the 2012/2014/2016 Antarctica DC8
     seasons (`claude_notes/img2_surface_fraction.py`: greenland store ~2%
     affected overall, antarctica ~9%, concentrated in DC8 seasons). Document
     the likelihood/bias in docs + variable attrs; downstream filters.
   - The saturation second pass fits the **img1-sourced and img2-sourced
     populations separately** per season (each with its own level/status/
     support in the season dict), and reports the cross-cap step (img1 vs
     img2 population offset at the cap) as the per-season empirical bias
     estimate — measured, not assumed.
   - Per-trace `surface_ceiling_margin_dB` is computed against the ceiling of
     the trace's own source population when that population's fit is
     `fit_ok`, else NaN.
   - `min_span_decades` = **0.25 everywhere** (user decision at integration
     review: one uniform minimum, no special-casing by population; supersedes
     the earlier 0.5 whole-season minimum — 2019_GV's 0.42-decade span now
     passes support as a side effect).
2. **insufficient_overlap**: keep honest NaN (no relaxed window).
3. **Piecewise model**: keep for now; **revisit after it has run on more
   data** — remind the user at the next review checkpoint (post-integration
   test runs / production baseline).
4. **2019_GV span minimum**: leave as is.
5. **No cross-store pooling** — no guarantee the same instrument flew both
   ice sheets. **Only the antarctica and greenland stores matter for now**;
   ignore ase/utig/crosssystem in calibration work.
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
   wf pairs). Warn if the wfs of one image disagree on Tpd. Image-count
   precedence: `imgs` list (primary); else `len(img_comb)/3 + 1` when a valid
   `img_comb` vector exists; else a bounded probe capped at **3 images** (per
   user: 3 is the normal maximum, some seasons use 2, none known to use
   more — the "7 imgs" seen in 2014-era params is the old per-wf-adc format,
   not 7 real images, and is treated as suspect → verify at baseline). Typed
   error handling throughout: HTTP 404 = image permanently absent;
   timeouts/5xx = transient → bounded retry, then status `load_error`
   (retryable later), so a transient failure or a missing intermediate file
   is never recorded as "no more images".
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
     both images are >6 dB above their own noise floor. The per-image noise
     floor uses the pipeline's record-tail convention — window
     `[end − 12 µs, end − 7 µs]` per image, matching `record_tail` defaults —
     further clipped to end before `T_end − T_guard`, since images show the
     same end-of-record rolloff the tail-window study found in combined
     products (a last-2µs window would be biased low). If the image is too
     short for that window, fall back to its global 10th percentile;
     validate the fallback during the baseline run. The weight shifts signal
     and floor equally, so the SNR gate is weight-independent. Require ≥10
     valid bins per trace (else NaN) and ≥30 valid traces per pair (else
     pair status `insufficient_overlap`).
   - Sign convention (documented in variable attrs): positive = the earlier
     (lower-index, shallower) image is brighter than the later one.
5. **Computed metrics**, per frame and pair:
   - *per-trace offset* — as above (median over bins within the trace).
   - *mean offset* — mean of the per-trace offsets along the frame (the
     headline value; downstream threshold ~3 dB). Mean is a user decision;
     each per-trace value is already a median over bins, which absorbs
     within-trace outliers. The baseline run reports mean vs median per frame
     as a sensitivity check — if heavy-tailed frames make them diverge enough
     to move worst-pair selection or cross a ~3 dB threshold, revisit (MAD
     flags such frames regardless).
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

1. Range to surface `r = c·surface_twtt/2`; where
   `surface_source_image_index` is populated, fit the img1-sourced and
   img2-sourced populations **separately** (per the two-regime resolution
   above), each with its own level/status/support recorded in the season
   dict, plus the cross-cap step (img1-vs-img2 population offset at the gate
   cap) as the season's empirical img2 bias estimate. Where the index is
   unavailable (pre-schema stores, 2013_Greenland_P3's −1s), fall back to a
   single all-traces fit; `ceiling_fit_population` records which mode ran
   (`by_source_image` / `all_traces`).
2. **Ceiling estimate per season**: binned upper-quantile (99th) power vs
   `log10(r)`.
   - *Plateau detection first*: a clean season has no physical ceiling, so a
     season-wide max would mislabel ordinary close-range returns. The
     physical model is a constant clip level: partial saturation shows as a
     flat upper envelope at short range transitioning to ~−20 dB/decade
     where the unsaturated envelope drops below the clip level — a single
     whole-season slope would average the two regimes and miss it. Use a
     **contiguous low-range plateau detector / piecewise fit** (plateau
     level + breakpoint + free slope beyond), compared against a pure-slope
     model; declare a plateau only when the piecewise model wins with the
     plateau segment ≫ shallower than −20 dB/decade over sufficient support:
     minimum traces per bin, minimum occupied bins, and minimum log-range
     span (defaults to fix during implementation via the baseline runs; all
     recorded as metadata, with bootstrap CI on the plateau level). The
     plateau level is the season ceiling; margins against it remain valid at
     all ranges (distance below the clip level).
   - Season fit status: `fit_ok` / `no_plateau` / `insufficient_support`.
   - *Pile-up test*: fraction of traces within 1 dB of the ceiling
     (only when `fit_ok`).
   - *Regime check*: during the baseline run, repeat the fit per segment and
     inspect ceiling multimodality within a season (mixed receiver/waveform
     settings); if a season is multimodal, fall back to per-segment ceilings
     before writing per-trace margins. In that case the second pass writes a
     `segment_saturation` dict (keyed by segment ID, same fields as the
     season dict: level, status, support, CI) so per-trace margins remain
     explainable downstream; season attrs then record
     `ceiling_scope='segment'`.
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
and every fixed threshold used. Staleness is tracked by a **science-data
fingerprint**, not a commit ID (the second pass's own write would otherwise
make its result look stale immediately): the second pass records
`(len(slow_time), sha256(sorted processed_frames))` at fit time; any science
append/removal/reprocess changes the fingerprint, while calibration-only
writes do not. The runner warns when the current fingerprint differs from the
recorded one (second pass needs re-running).

None of these are QC-masking; they are values downstream users filter on.

**Migration/backfill**: `_zarr_append` silently skips variables absent from an
existing store (`store.py:88`) — that silent skip is removed, not worked
around: **append auto-migrates**. When an incoming dataset carries a variable
the store lacks, `_zarr_append` creates the array sized to the store's current
`slow_time` filled with the variable's fill value (NaN / −1), logs it loudly,
then appends — so an ordinary processing run against a pre-calibration store
migrates itself and can never silently drop calibration columns. (The
standalone backfill command remains for retryable-status recomputation, not as
a migration prerequisite.) `runner.py`'s frame-attr plumbing
(`batch_frame_attrs`, currently `dict[str, dict[str, float]]` with a `float()`
cast) is extended to typed per-frame attrs so string statuses ride along.
Entry-point defaults are unified by a shared config-normalization helper used
by both `load_config` and `process_frame`: calibration defaults **on**
everywhere, so behavior cannot depend on the entry point; callers that need it
off (unit tests, quick runs) set `processing.calibration.img_combine: false`
explicitly.

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
2. Saturation second-pass functions (piecewise plateau fit / pile-up /
   margin) on plain numpy arrays; unit tests with synthetic clean, fully
   clipped, **partially clipped** (plateau at short range, −20 dB/decade
   beyond the breakpoint), mixed/insufficient-support, and multimodal
   populations (clean season must yield `no_plateau` and all-NaN margins;
   partial clipping must recover the plateau level).
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
   /tmp cache growth). Tests: append to a pre-calibration store auto-migrates
   (arrays created, backfilled with fill values, loud log), enabled→
   disabled→enabled transitions, backfill of retryable statuses, fingerprint
   staleness warning (calibration-only write stays fresh; science append goes
   stale). Test against a local store (`test_config.yaml`).
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
