# Calibration integration (steps 5–6) — 2026-08-31

Branch `calibration-checks`, uncommitted. No S3 writes; all runs local.

## What changed

- `config.py` — `normalize_config()` shared by `load_config` and
  `process_frame`; `processing.calibration.{img_combine: true,
  image_load_retries: 2}` defaults ON at every entry point.
- `calibration.py` —
  - images now load **sequentially** and convert to numpy immediately
    (dataset closed per image);
  - `_align_traces`: images reindexed to the combined frame's (decimated)
    slow_time by nearest within 1 s; unmatched traces → NaN. Needed because
    the pipeline decimates the combined frame but images are full-resolution
    (the CLI passed full-res frames so this never triggered before).
  - `min_traces_per_pair` capped proportionally
    (`min(30, max(5, 0.25·n_traces))`) — the absolute 30 was tuned against
    full-res frames (~1% of traces) and was a far stricter relative bar for
    ~40-trace decimated frames.
  - new: `fit_season_by_source` (img1 / img2 populations fit separately,
    `min_span_decades_source_restricted: 0.25`), `cross_cap_step`
    (empirical img2 bias), `margins_by_source`.
- `processing.py` — `run_frame_calibration()` (never fails a frame; statuses
  degrade); emits `img_comb_offset_dB` (float32), `img_comb_pair` (int8),
  `surface_source_image_index` (int8), `surface_ceiling_margin_dB` (all-NaN
  float32) every frame regardless of enablement; frame attrs
  `frame_img_comb_status` / `frame_img_comb_weights_mode` /
  `frame_img_comb_offset_dB` (only when measured). `decimate_frame()` factored
  out for backfill reuse. Offsets stored only for `ok` / `partial_images`
  (`weight_recovery_failed` residuals stay parquet-only, per plan).
- `store.py` — `_zarr_append` auto-migrates (missing variables created sized
  to the store, fill NaN/−1/False/"", loud warning) instead of silently
  skipping; `science_fingerprint` (n_traces + sha256 of sorted
  processed_frames), `saturation_stale`, `write_saturation_results`,
  `update_frame_calibration` (in-place backfill; contiguous slice or oindex).
- `runner.py` — `FRAME_ATTR_CASTS` typed per-frame attrs (strings ride along;
  NaN floats → None so attrs stay JSON-valid); stale-second-pass warning after
  the final commit.
- New commands: `saturation_pass.py` (`--dry-run`; fits per season by source
  image, writes margins + `saturation` root attr with fingerprint),
  `calibration_backfill.py` (`--statuses load_error,disabled`, `--limit`).
- Viewer: `toFloat64Array` byte-reinterpret bug fixed (int arrays now convert
  element-wise); int8 sentinel (−1) mapped to NaN on load
  (`INT_SENTINEL_VARIABLES`); `integer` formatting flag; variables added to
  config + dropdown (`img_comb_pair` loadable but not a dropdown layer —
  it's bookkeeping for the offset).
- Docs: architecture.md (variables, frame/season attrs, pipeline step, cost),
  processing.md (calibration config + commands), data_access.md (downstream
  filtering summary, user's "provenance not validity" framing).
- Tests: `tests/unit/test_calibration_integration.py` (9 tests: migration,
  enabled/disabled transitions, placeholder emission, degrade-to-status,
  in-place backfill, split-population fit + cross-cap recovery, second-pass
  write, fingerprint staleness). Suite: **73 passed**.
- **Existing-test modification (flagged)**: the exact-variable-set assertion
  in `test_process_frame_output_variables` gained the four new variable
  names — unavoidable given the user-approved schema; nothing else touched.

## Verification (local smoke store, outputs/icechunk_store_calib_smoke)

`test_config.yaml`-equivalent run (David/Drygalski, 3 frames of
2013_Antarctica_P3, calibration on): statuses `['ok', 'insufficient_overlap',
'insufficient_overlap']`, frame offset +0.51 dB on the ok frame, per-trace
offsets finite exactly on that frame, `surface_source_image_index` all 1,
margins all-NaN. Second pass: `by_source_image` mode,
`insufficient_support` both populations (117 traces — expected), attrs +
fingerprint written, `saturation_stale` False after its own commit. Backfill
CLI: correctly reports nothing retryable.

## Benchmark (3 real 2018_Greenland_P3 frames, sequential, streaming cache)

| | wall time | downloaded | per-frame cache high-water |
|---|---|---|---|
| calibration OFF | 21.1 s | 200 MB | 118 MB |
| calibration ON | 65.3 s | 512 MB | 318 MB |

≈ **3.1× wall time, 2.6× download volume, 2.7× temp disk** per frame with the
check on (this season has 3 images). Worker per-frame cache cleanup already
bounds disk (unchanged). For production planning: multiply current per-store
processing time/bandwidth by ~3.

Benchmark statuses: Data_20180426_03_001 `ok` (+1.1 dB, matches baseline);
both Data_20180404_02_* frames `insufficient_overlap` with n_valid=0 at full
resolution too — real geometry, not a decimation artifact (verified
explicitly).

## Reprocessing regression verification (2026-08-31, post-review)

`claude_notes/reprocess_regression_check.py`: re-ran `process_frame` with the
branch code (calibration ON, production configs) on 5 frames — 2018/2013
Greenland P3, 2022 BaslerMKB, 2016/2012 Antarctica DC8 — and compared all 25
pre-existing per-trace variables trace-by-trace against the production store
values (NaN-pattern equality + float tolerance). **0 mismatches on all 5
frames**; trace counts identical. Calibration statuses: ok (2016 DC8,
BaslerMKB), insufficient_overlap (2018 GL), params_missing (2013 GL — no
imgs; 2012 DC8 — see below).

Follow-up worth one look pre-production: **2012_Antarctica_DC8 returns
`params_missing`** even though its img files exist (availability probe was
200/200). Its 2012-era attrs presumably carry neither a recognized img_comb
path nor usable Tpd. Since 2012 DC8 is the most saturated season, recovering
its seam check (extend param search or Tpd-default window) may be worthwhile.

## 2012-era param support + noise-floor fix (2026-08-31, post-review)

**2012_Antarctica_DC8 `params_missing` resolved** — three era differences, all
handled in `calibration.py` (+ unit tests, suite at 75):
1. Combine params live under `param_combine_wf_chan` (new search path).
2. Old 2-element-per-pair `img_comb` format `[T_comb, T_guard]` (no T_blank),
   normalized to modern `[T_comb, -inf, T_guard]` — applied only under the
   2012-era attr so modern 6-element vectors are never misread. Verified
   empirically: combined follows img1 until ≈ surf + 10 µs (T_comb = Tpd2),
   weight recovery w2 = +0.025 dB (spread 0.004).
3. `radar.wfs` is a list of per-waveform dicts (Tpd extracted per dict);
   `imgs` entries are (N,2) wf-adc rows (wf axis chosen as the constant one).

**Noise-floor contamination fix**: the first rerun then hit
`insufficient_overlap` because img1's record ends mid-ice (18.8 µs), putting
the record-tail noise window (6.8–11.8 µs) in strong englacial signal — floor
inflated ~45 dB, SNR gate starved. `image_noise_floor_db` now takes the
elementwise min of the tail median and the global 10th percentile (a
slightly-low floor only admits extra bins into a median — safe). Not
2012-specific: any short-gate image whose record ends above the bed had the
same hazard.

**Final regression check: all 5 frames PASS** (0/25 variable mismatches each);
2012 DC8 now status `ok`. Note the floor change makes the SNR gate slightly
more permissive than in the earlier baseline parquet — offsets are
medians-over-bins so ok-frame numbers move negligibly, and the production run
supersedes the baseline anyway.
