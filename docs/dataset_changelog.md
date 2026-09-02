# Dataset changelog

## 2026-09 — Radiometric calibration fields (calibration method 0.4.0)

New per-trace variables and per-frame/season metadata for two radiometric
calibration checks, added to the **ase**, **greenland**, and **antarctica**
stores. All values are diagnostics for downstream filtering — nothing is
QC-masked by them, and **no pre-existing values changed** (verified per store:
every original array byte-identical before/after the update).

### New per-trace variables (dimension `slow_time`)

| Variable | Type | Meaning |
|---|---|---|
| `img_comb_offset_dB` | float32 | Residual seam offset (dB) of the frame's worst image pair — the power step actually present in the combined product at the image-combine boundary, measured by comparing the individual images (weight-corrected) in their overlap. Positive = the shallower image is brighter. NaN where unmeasurable (see statuses). A downstream rejection threshold of ~3 dB is suggested; season-level means vary (see below). |
| `img_comb_pair` | int8 | Which image pair the offset series refers to (1 = img1/img2, 2 = img2/img3), fixed per frame (largest \|mean offset\|); −1 undefined. |
| `surface_source_image_index` | int8 | Which image supplied the combined product's surface sample (1 = low-gain img1; ≥2 = a higher-gain image; −1 unknown). **Provenance flag, not a validity verdict**: img2-sourced surfaces are more likely saturated and may carry a season-dependent low bias (~15–20 dB measured on 2014/2017 Greenland P3), but for high-altitude DC8 operations the surface landing in img2 is normal geometry. Filter as your application requires. |
| `surface_ceiling_margin_dB` | float32 | Season ceiling minus surface power (dB), computed against the ceiling of the trace's own source-image population. Small/zero ⇒ at the ceiling (likely saturated). NaN wherever no credible ceiling was fitted — a clean season has no ceiling and therefore no margins. |

### New per-frame attributes (parallel to `frame_names`)

`frame_img_comb_offset_dB` (worst-pair mean residual, dB),
`frame_img_comb_status`, `frame_img_comb_weights_mode` (declared OPR combine
weights mode, provenance only).

Statuses: `ok`, `no_combine` (single image — check not applicable),
`images_unavailable` (img files not published), `insufficient_overlap` (no
artifact-free overlap window at the frame's geometry — e.g. 2014-era
Greenland P3 at typical AGL), `weight_recovery_failed`, `load_error`
(transient, retryable), `invalid_params`, `params_missing`, `disabled`.
NaN offsets accompany every non-`ok` status; no estimate is substituted.

### New root attribute: `saturation`

Season-keyed dict from the saturation second pass: per source-image
population — `status` (`fit_ok` / `no_plateau` / `insufficient_support`),
`level` (dB), `level_ci` (bootstrap 95%), `single_slope` (dB/decade),
`pileup_fraction`, `n_traces`, `n_bins_occupied`, `span_decades` — plus
`cross_cap_step_db` (measured img1-vs-img2 envelope offset: the season's
empirical img2 surface-power bias), `method_version`, every fixed threshold,
and a science-data fingerprint for staleness tracking. Consumers wanting
stricter acceptance than `fit_ok` can filter on `span_decades`,
`pileup_fraction`, and `level_ci`.

### Suggested QC filtering

Starting points — tighten or loosen per application:

```python
seam_ok   = np.abs(img_comb_offset_dB) < 3.0          # or np.isnan(...) to keep unmeasured
surf_ok   = surface_source_image_index == 1            # drop likely-saturated img2 surfaces
not_sat   = ~(surface_ceiling_margin_dB < 2.0)         # NaN margin (no ceiling) passes
```

- **Seam step**: reject traces (or frames, via `frame_img_comb_offset_dB`)
  with |offset| > ~3 dB. Decide explicitly how to treat NaN offsets
  (statuses like `images_unavailable` / `insufficient_overlap`): the check
  didn't run there, which is not evidence the frame is clean.
- **Surface saturation**: traces with `surface_ceiling_margin_dB` below
  ~1–2 dB sit at their season's clip level — exclude them from surface-power
  and RSSNR analyses. NaN margins mean no credible ceiling exists for that
  population, not that the trace is clean or dirty.
- **Surface provenance**: for absolute surface-power work, prefer
  `surface_source_image_index == 1`; where img2-sourced surfaces are kept,
  the season's `cross_cap_step_db` estimates their low bias.
- **Trusting a ceiling**: before leaning on a season's `level`, check its
  `span_decades` (≥0.4 is comfortable), `pileup_fraction` (well above zero),
  and `level_ci` width — weak fits (e.g. 2018_Greenland_P3 img1: pileup
  1.7%) are reported, not suppressed.

### Method notes

- **Seam offsets** are measured on the individual OPR images after
  empirically recovering each image's effective combine scaling
  (`w_i = median(combined − img_i)` over the section the combined product
  sourced from img_i), so the metric reflects the shipped combined product
  regardless of `img_comb_weights_mode` (including 'auto' seasons, where
  per-frame scalings of >100 dB were recovered on 2018_Antarctica_DC8).
- **Ceiling detection** uses a binned 99th-percentile envelope vs log range
  with equal-width bins and sparse-adjacent-bin merging (0.3.0), fitted per
  source-image population; a ceiling is declared when the Theil–Sen slope is
  far shallower than the unsaturated ~−20 dB/decade (flat rule). A piecewise
  partial-saturation model was removed (0.4.0) after deciding zero fits on
  real data across all stores.

### Headline findings shipped with this release

- Seam steps are typically ≤2 dB, but several seasons carry systematic
  image-combine errors: 2016_Greenland_P3 (+7.4±9.9 dB), 2017_Antarctica_Basler
  (−6.8±7.5 dB), 2013_Antarctica_Basler (+4.8±6.1 dB).
- Clear surface-saturation ceilings (img1 populations): 2012_Antarctica_DC8
  (−30 dB), 2014_Greenland_P3 (−31.6 dB, with a measured 16.2 dB img2 bias
  step), 2013_Greenland_P3 (−50.6 dB), 2017_Antarctica_Basler (−25.9 dB),
  among others; 2016–2019 Greenland P3 and most Basler seasons show no
  ceiling.

### Store rollout

| Store | Status |
|---|---|
| ase | updated 2026-08-31 (calibration fields + method 0.4.0 attrs) |
| greenland | updated 2026-09-01 |
| antarctica | updated 2026-09-01 |
| utig / crosssystem | not updated (out of current scope) |

Old ASE store preserved at `icechunk/ase-backup-20260831`; the greenland and
antarctica updates were additive (full icechunk history retained in place, so
the pre-calibration snapshots remain in each store's history).

### Code

Implemented on branch `calibration-checks`
([PR #4](https://github.com/englacial/radar-return-statistics/pull/4)):
`93da0ec` (checks, pipeline integration, docs, viewer), `e80d852`
(sparse-adjacent-bin merging, method 0.3.0), `d5cd19e` (piecewise model
removed, method 0.4.0), `cc16499` (this changelog).
