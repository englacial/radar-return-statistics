# Two-regime surface-power investigation (2026-08-31)

Question: why do several Greenland P3 seasons show two flat-topped
surface-power bands with a ~15–20 dB step near ~800 m–1 km range
(baseline finding, `calibration_baseline_results.md`)? Candidates:
(A) image combining, (B) receiver/params changes.
Scripts: `two_regime_step{1..4}_*.py`; figures in
`outputs/calibration/baseline/figures/` (`two_regime_gl_seasons.png`,
`two_regime_ascope_2014.png`).

## Verdict: Hypothesis A — the seam cap forces the combined product's surface
## sample to come from img2, whose surface response is corrupted

**Evidence (2014_Greenland_P3, 5 frames across 5 segments, incl. 3 mixed-
altitude frames):**

- Per-trace `surface_source_image_index` (from `calibration.check_img_combine`)
  splits every mixed frame exactly at 780 m — precisely
  `T_end(img1) − T_guard = 7.85 − 2.64 ≈ 5.21 µs`. Regime membership is
  predicted perfectly by source image *within single frames*:

  | frame | src=1 (≤780 m) p50 | src=2 (>780 m) p50 |
  |---|---|---|
  | Data_20140502_01_031 | −40 to −47 dB range | −58 to −65 dB range |
  | Data_20140505_01_002 | −42.6 | −60.0 |
  | Data_20140412_03_001 | −40.7 | −64.9 |
  | Data_20140407_01_012 | −46.7 | −64.8 |
  | Data_20140415_01_002 (high) | — | −58.4 |

- **Hypothesis B eliminated**: all gain-affecting params are identical across
  all 5 frames / 5 segments spanning both regimes (adc_gains = 22.387 on every
  channel, presums, rx_paths, Vpp_scale all constant). Same settings on both
  sides of the step within a single frame → settings cannot explain it.

**Sign explanation** (`two_regime_ascope_2014.png`): at 700 m the surface
(4.65 µs) precedes the seam cap (5.21 µs), so the combined follows img1 —
sharp compressed surface peak (−53 dB in the example trace). At 900 m the
surface (6.0 µs) lands *past* the cap, so the combined takes img2's surface —
a broadened, suppressed hump ~15–20 dB lower. This is the corrupted
surface response `T_comb` exists to avoid ("avoid using the saturated surface
response in image 2", OPR guide): the seam cap `T_end(img1) − T_guard`
overrides that protection whenever td_surf > cap. The lower band is therefore
**not a second calibration regime — it is invalid surface-power data**: for
`surface_source_image_index ≥ 2` traces the surface sample is smeared/
suppressed, biasing surface power low by ~15–20 dB.

## 2017 / 2019 Greenland

- **2017_Greenland_P3: same phenomenon confirmed.** Mixed frame
  Data_20170424_01_061 splits at 1050 m (= its era's cap
  `T_end(img1) − T_guard ≈ 7 µs`): src=1 p50 −21.3 dB vs src=2 p50 −40.5 dB
  (19 dB step at the boundary).
- **2019_Greenland_P3: inconclusive from one frame.** The sampled mixed frame
  (Data_20190417_02_047) has an img1 gate long enough to cover all its ranges
  (all src=1 to 1756 m), so its population step at ~1 km must come from
  segments with shorter img1 gates (gates vary per segment) or another cause.
  The per-trace index at integration scale will resolve it season-wide.

## Implications for the saturation second pass (prototype results)

Store-level prototype on the full 2014 population (img1/img2 approximated by
the 780 m cap):

| population | n | fit |
|---|---|---|
| full season (current) | 49 251 | `no_plateau` (single slope −24 averages the regimes) |
| img1-sourced (r < 731 m) | 44 904 | `insufficient_support` — span 0.31 < 0.5 decades; with `min_span_decades=0.25` → **fit_ok, level −31.5 dB**, flat (−4.8 dB/dec) |
| img2-sourced (r > 831 m) | 3 953 | fit_ok at −53.7 dB — but this is a ceiling on *corrupted* data, not a clip level |

1. The already-planned restriction to `surface_source_image_index == 1`
   cleanly isolates the valid population and recovers a credible plateau for
   2014_Greenland_P3 — **but only if `min_span_decades` is relaxed (0.5 →
   ~0.25) for that subpopulation**, because low-altitude flying inherently
   spans little range. (Interacts with the user's decision to leave the span
   minimum as-is for 2019_GV — needs a user call.)
2. Traces with index ≥ 2 should never enter a ceiling fit, and their stored
   `surface_power_dB` is biased low by ~15–20 dB — this affects *all*
   surface-power uses (e.g. RSSNR), not just saturation. The planned per-trace
   index variable is exactly the needed filter; whether to also flag these in
   docs/QC guidance is a user decision.
3. No bug found in `calibration.py` — `surface_source_image_index` behaved
   exactly as designed throughout.
