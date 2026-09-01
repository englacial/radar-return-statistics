# Calibration baseline results (2026-08-31)

Steps 1–4 of `claude_plans/20260831-radiometric-calibration-checks.md`.
Code: `src/radar_return_statistics/calibration.py` + `calibration_cli.py`;
tests: `tests/unit/test_calibration.py` (16 tests; full unit suite 64 passing).
Outputs: `outputs/calibration/baseline/` and `baseline_extra/`
(parquet + figures + `manifest.json`). No store writes anywhere.

## Check 1: image-combine residual seam offsets

36 frames across 5 seasons (2 frames × 5 segments for 2018_Greenland_P3,
2016_Antarctica_DC8, 2018_Antarctica_DC8; 3 frames 2014_Greenland_P3;
3 frames 2022_Antarctica_BaslerMKB). Zero load failures; statuses:
27 `ok`, 8 `insufficient_overlap` (real geometry, see below), 1 more in
extra run. All four param layouts exercised (`param_combine.array_param`,
`param_array.array`, empty-`img_comb`-era, BaslerMKB full-gate).

### Per-season residual seam offsets (worst pair, frame means, `ok` frames)

| season | pair | n | mean (dB) | frame-to-frame std | weights |
|---|---|---|---|---|---|
| 2018_Greenland_P3 | 1 | 8 | **+1.07** | 0.21 | all 0 (raw stitch) |
| 2016_Antarctica_DC8 | 1 | 8 | **+1.45** | 0.65 | all 0 (raw stitch) |
| 2016_Antarctica_DC8 | 2 | 2 | +0.12 | 0.09 | |
| 2018_Antarctica_DC8 | 1 | 8 | **−0.57** | 0.99 | w2/w3 ≈ **106–111 dB, per-frame** |
| 2018_Antarctica_DC8 | 2 | 2 | −0.07 | 0.49 | |
| 2022_Antarctica_BaslerMKB | 1 | 3 | +0.55 | 0.04 | all 0 |

- Weight recovery was rock solid everywhere: per-frame spread ≤ 0.05 dB on
  every frame, no `weight_recovery_failed`.
- **2018_Antarctica_DC8's auto weights vary frame to frame (106–111 dB)** —
  empirical recovery is mandatory, not optional, for that season; the
  declared params could never supply these. Even after auto correction one
  frame retains a **−2.92 dB** residual seam (Data_20181016_01_051) — auto
  mode does not always fix the seam.
- Distribution vs the ~3 dB downstream threshold: max |frame mean| = 2.92 dB,
  2/27 frames above 2 dB, 0 above 3 dB. A 3 dB cut would currently reject
  borderline frames only; the bulk sits at |offset| ≤ 1.5 dB.
- The healthy-frame ~+1.8 dB from exploration is confirmed as season-typical
  for 2016 DC8 (+1.45 ± 0.65) and 2018 Greenland (+1.07 ± 0.21): real, stable
  seam steps in the combined products.

### Mean vs median sensitivity (Codex #16 check)

Max |mean − median| over ok frames = **1.21 dB** (Data_20161031_06_015: mean
2.66 / median 1.45, MAD 1.04, drift +2.02 — a genuinely drifting frame).
Typical difference ≪ 0.3 dB. The mean headline stands (user decision); high
MAD/drift flags the frames where it matters, and both center measures ship in
the parquet/store.

### insufficient_overlap is real geometry, not a bug

2014_Greenland_P3 comes back `insufficient_overlap` on all frames at typical
AGL: its `img_comb` (T_comb=3 µs, T_guard=2.64 µs, found *non-empty* at
`param_combine.array_param` — the survey's "empty" was the `combine` path)
plus img1 ending at 7.85 µs leaves **no artifact-free overlap window** when
td_surf ≈ 3.2 µs (window [6.2, 5.2] µs is empty): the combine switched earlier
than the T_comb-safe point, so any measurement would include img2's saturated
surface response. The scattered insufficient_overlap frames in other seasons
(5/36) are the same geometry at high AGL. Honest NaN + status is the designed
behavior; weights still recover (2014: all ≈ 0 → raw stitch).

## Check 2: surface saturation (all five stores, read-only)

### Season fits

| store | season | status | level (dB) | overall slope (dB/dec) | pileup |
|---|---|---|---|---|---|
| antarctica | 2008_Antarctica_BaslerJKB | fit_ok | −15.3 | +2.8 | 0.03 |
| antarctica | 2012_Antarctica_DC8 | **fit_ok** | **−33.1** | −9.8 | **0.17** |
| antarctica | 2013_Antarctica_Basler | no_plateau | | −16.6 | |
| antarctica | 2013_Antarctica_P3 | insufficient_support | | | |
| antarctica | 2014_Antarctica_DC8 | no_plateau | | −25.9 | |
| antarctica | 2016_Antarctica_DC8 | no_plateau | | −19.0 | |
| antarctica | 2017_Antarctica_Basler | fit_ok | −25.5 | −4.1 | 0.02 |
| antarctica | 2017_Antarctica_BaslerJKB | insufficient_support (n=178) | | | |
| antarctica | 2017_Antarctica_P3 | no_plateau | | −27.9 | |
| antarctica | 2018_Antarctica_DC8 | fit_ok | −21.8 | +3.9 | 0.02 |
| antarctica | 2019_Antarctica_GV | insufficient_support (span 0.42 dec) | | | |
| antarctica | 2022_Antarctica_BaslerMKB | no_plateau | | −20.2 | |
| antarctica | 2023_Antarctica_BaslerMKB | no_plateau | | −15.8 | |
| greenland | 2013_Greenland_P3 | **fit_ok** | **−50.4** | +6.2 | 0.01 |
| greenland | 2014_Greenland_P3 | no_plateau (two-regime, see below) | | −24.0 | |
| greenland | 2016_Greenland_P3 | insufficient_support | | | |
| greenland | 2017_Greenland_P3 | no_plateau | | −24.3 | |
| greenland | 2018_Greenland_P3 | no_plateau | | −21.1 | |
| greenland | 2019_Greenland_P3 | no_plateau | | −29.5 | |
| ase | 2012_Antarctica_DC8 | **fit_ok** | −33.2 | −9.0 | **0.11** |
| ase | 2014_Antarctica_DC8 | fit_ok | −30.3 | −10.5 | 0.02 |
| ase | 2016_Antarctica_DC8 | no_plateau | | −12.5 | |
| ase | 2018_Antarctica_DC8 | fit_ok | −25.1 | −6.7 | 0.08 |
| utig | 2008_Antarctica_BaslerJKB | fit_ok | −14.4 | −1.4 | 0.01 |
| utig | 2017_Antarctica_BaslerJKB | insufficient_support (n=276) | | | |

Headline: **2012_Antarctica_DC8 is the most saturated season** — flat top at
−33 dB across 400 m–10 km with 11–17% of traces within 1 dB of the ceiling
(consistent with the ASE saturation discussion around Chu et al. 2021).
2013_Greenland_P3 confirms the exploration finding (razor-flat top, −50.4 dB).
Every real-data `fit_ok` came from the **flat-envelope rule** (overall
upper-quantile slope far shallower than −20 dB/decade); the piecewise
partial-saturation model never won on real data.

### Fit hardening during the baseline

The first fit version produced a false negative (2014_Greenland) and false
positives (2018_Greenland with a rising, then a −57 dB/dec plunging second
segment). Root cause: **two-regime seasons** — a flat upper envelope at one
level below ~1 km range, a ~15–20 dB step, then a second flat envelope
(altitude-dependent receiver gain/attenuation, not a clip-level crossing).
Three constraints were added to `SAT_DEFAULTS` and unit-tested against a
synthetic two-regime population:

- `max_slope_beyond_db_per_decade = −8` — the post-breakpoint segment must fall;
- `min_slope_beyond_db_per_decade = −35` — but not plunge like a regime cliff;
- `continuity_tol_db = 3` — it must continue from the plateau level.

### Two-regime seasons (key finding for review)

`figures/saturation_greenland_2014_Greenland_P3.png`: two *flat-topped* bands
(~−30 dB below ~800 m range, ~−48 dB beyond). **Both regimes individually
look ceiling-limited.** The season fit correctly refuses a single ceiling
(`no_plateau`) but thereby under-reports real saturation. Per-segment fits
cannot rescue this: segments fly at near-constant altitude, so 34/35 segment
fits returned `insufficient_support` (no range lever arm within a segment) —
season (or altitude-regime) level is the only workable granularity.

## Proposed thresholds

- **Image combine (downstream guidance, not enforced)**: |frame mean residual|
  > 3 dB, as the user anticipated — currently rejects 0/27 measured baseline
  frames (max 2.92); 2 dB would flag 2/27.
- **Saturation defaults** (all recorded in the manifest): flat-envelope rule
  at slope ≥ −12 dB/decade; piecewise acceptance needs SSE improvement ≥ 30%,
  plateau internal slope ≤ 5, slope-beyond in [−35, −8], continuity ≤ 3 dB;
  support minimums 50 traces/bin, 6 bins, 0.5 decades.

## Open questions needing user decisions

1. **Two-regime seasons**: stratify by altitude band (e.g. split at the
   step) or by receiver attenuation settings from params, fitting a ceiling
   per regime? Without this, 2014_Greenland_P3 (and possibly 2017/2019 GL)
   get no margins despite visibly ceiling-limited populations.
2. **insufficient_overlap frames** (2014_Greenland at typical AGL; scattered
   high-AGL frames elsewhere): accept honest NaN, or add a clearly-labeled
   relaxed window (which would include part of img2's saturated surface
   response)?
3. **Piecewise model**: it never fired on real data (all detections via the
   flat rule). Keep it (tested, harmless) or simplify to flat-rule-only?
4. 2019_Antarctica_GV fails on range span (0.42 < 0.5 decades) — relax
   `min_span_decades` for high-altitude-only platforms, or leave?
5. 2016 GL / 2013 Antarctica P3 / 2017 BaslerJKB fail support in their
   stores but may pass in a merged view — worth a cross-store union fit?
