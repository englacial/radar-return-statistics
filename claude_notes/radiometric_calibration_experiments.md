# Radiometric calibration check experiments (2026-08-31)

Exploration supporting `claude_plans/20260831-radiometric-calibration-checks.md`.
Scripts: `calib_explore1.py` … `calib_explore5.py`, `calib_saturation_explore.py`.

## Loading individual images via xopr

`opr.load_frame(item, data_product="CSARP_standard", image=N, allow_unlisted_products=True)`
works (xopr 0.5.1.dev27). Image files must exist on the public OPR site
(`Data_img_NN_...mat` next to the combined file).

Availability spot check (first frame per collection):

| Collection | img files | img_comb vector | notes |
|---|---|---|---|
| 2016_Antarctica_DC8 | yes (3) | `[3e-6, -inf, 1e-6, 1e-5, -inf, 3e-6]` in `param_combine.{array_param,combine}.img_comb` | Tpd = [1, 3, 10] µs |
| 2014_Greenland_P3 | yes (≥2) | **empty** (`param_combine` present, `img_comb=[]`, 7 imgs listed) | older param format |
| 2013_Greenland_P3 | **no** (404) | empty | neither check input available |
| 2018_Greenland_P3 | yes | `param_array.array.img_comb` (also `param_records.array/qlook`) | Tpd = [1,1,3,3,10,10] µs (wf-adc pairs) |
| 2019_Antarctica_GV | yes | same as 2018 layout | |
| 2022_Antarctica_BaslerMKB | yes | same layout | img1/img2 span identical twtt — full-range images |

So the param attr path varies by season: `param_combine.*` (2016 DC8 era),
`param_array.array.img_comb` / `param_records.array.img_comb` (newer).
`radar.wfs.Tpd` under `param_records` gives per-waveform pulse durations.

## Combine-point semantics (OPR guide §img_comb)

Per adjacent image pair, three numbers `[T_comb, T_blank, T_guard]`:
transition at `max(T_blank, T_comb + td_surface)` (surface NaN → 0), limited by
`T_end(img_earlier) - T_guard`. T_comb ≈ Tpd of the later (higher-gain) image to
skip its saturated surface response; T_guard ≈ Tpd of earlier image to skip
pulse-compression rolloff. `img_comb_weights_mode='auto'` (if set) estimates and
applies a per-transition weight — would *hide* gain mismatch in the combined
product; not seen in the frames checked.

## Inter-image overlap offset (healthy frame, Data_20161014_03_001)

Per-trace median dB offset in valid overlap window
(`[max(T_blank, T_comb+td_surf), T_end_a - T_guard]`, linear interp onto common grid):

- img1 vs img2: median +1.76 dB, std 0.22 dB
- img2 vs img3: median +1.76 dB, std 0.31 dB

Very precise per trace (~0.2–0.3 dB). Note nonzero systematic offset even on a
healthy frame — thresholds must be relative to season baselines, not zero.
A-scope figure: `outputs/figures/img_combine_ascope.png` — combined follows
img1 → img2 → img3 exactly at predicted transition times; img3 shows the
saturated-surface bump above img2 at 8–10 µs (why T_comb exists).

## Combined-only step detector

Naive above/below-window median difference is dominated by the power decay
trend (−3.5 dB "step" on a clean frame). Detrended version (robust linear fits
±[0.3, 1.5] µs on each side, extrapolated to boundary):

- 2016 DC8 healthy frames: frame medians +0.5 to +2.1 dB, per-trace MAD 1.5–3.4 dB

Usable only as a frame-level aggregate and as fallback where img files are
missing; the overlap measure is ~10x more precise.

## Surface saturation signature (Greenland store, existing data)

`outputs/figures/saturation_power_vs_range_greenland.png`: surface_power_dB vs
range-to-surface (c·surface_twtt/2), per season, with per-bin 99th percentile
overlay.

- **2014_Greenland_P3**: unmistakable hard ceiling ~−27 dB; upper envelope flat
  vs range over 200 m – 5 km (expected −20 dB/decade absent).
- **2013_Greenland_P3**: ceiling ~−50 dB *plus* a strongly depressed
  low-altitude population (power drops to −140 dB at short range — possibly
  receiver blanking or saturation-distorted pulse compression).
- 2016–2019: upper envelope roughly follows −20 dB/decade; look unsaturated.

Saturation check can run entirely from existing store variables
(surface_power_dB, surface_twtt, qc_surface_pass, frame_index) — no echogram
loading needed.

## Image availability across all store seasons (2026-08-31 probe)

`calib_img_availability_probe.py`: HEAD requests on img_01 URLs for 2 frames of
each of the 19 unique seasons across the antarctica/greenland/ase/utig stores
(crosssystem store predates the `frame_collections` attr):

- All 200 (images published): 15 seasons.
- Season-wide 404: **2013_Greenland_P3** only.
- Mixed 200/404 (one of two frames): 2013_Antarctica_Basler,
  2014_Greenland_P3, 2019_Greenland_P3. Some 404s may be single-image frames
  (per the OPR guide, a lone image is written straight to the combined file
  with no img_01) — check params to distinguish `no_combine` from
  `images_unavailable`.

## Combine weights: declared vs effective (2026-08-31)

`calib_weights_survey.py` (one frame per season, all param_* attrs searched):
most seasons declare no `img_comb_weights*` fields; several declare
`img_comb_weights=[0 0]` with empty mode; **`img_comb_weights_mode='auto'`**
declared on the array/combine path only for **2018_Antarctica_DC8** and
**2017_Antarctica_Basler** (2019_Antarctica_GV: qlook paths only).

`calib_effective_weights.py`: effective per-image scaling is recoverable by
differencing the combined product against each image over the section the
combined sourced from it (`w_i = median(combined − img_i)`, per trace, then
median over traces). It is a per-frame constant with **zero** measured spread:

- 2016_Antarctica_DC8: w1=w2=w3=+0.000 dB exactly — combined is the raw
  stitch; the +1.76 dB raw inter-image offset is a *real* seam step in the
  combined product.
- 2018_Antarctica_DC8 (auto): w1=0, **w2=+107.607 dB, w3=+114.840 dB** (MAD
  0.000). The raw img files there are not on a common gain reference at all
  (auto weights and/or undeclared unit differences), so raw img-vs-img
  offsets are meaningless without weight recovery.

`calib_residual_step.py`: residual seam step = overlap offset of
weight-corrected images `(dB_a + w_a) − (dB_b + w_b)` = the step actually in
the combined product:

- 2016 DC8: +1.764 dB (MAD 0.22) and +1.757 dB (MAD 0.31) — equals raw, as
  weights are zero.
- 2018 DC8: +0.971 dB (MAD 0.80) and +1.364 dB (MAD 2.85) — the >100 dB
  corrections bring the seams to ~1 dB residual.

This makes the check weight-mode-independent: recover w_i empirically, report
the residual as the headline metric, keep raw offsets + w_i as diagnostics.
