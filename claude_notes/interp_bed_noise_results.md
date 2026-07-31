# Interp-anchored post-bed noise prototype — ASE results (2026-07-29)

Prototype: `claude_notes/interp_bed_noise_prototype.py`
Outputs: `outputs/interp_bed_noise_ase/{traces.parquet,traces_aligned.parquet,censored_window_metrics.png}`

For traces with no bed pick, the bed anchor is linearly interpolated (in
slow_time) between the segment's adjacent finite picks; the standard post-bed
window `[anchor + 5 µs, record_end − 5 µs]` then yields three metrics:
`interp_post_bed_noise_dB` (median, pipeline definition), `interp_post_bed_peak_dB`,
`interp_post_bed_std_dB` (std of dB samples). Where a pick exists the anchor is
the pipeline's 5 s-aligned pick, so the median is identical to the stored term.

## Results (815 frames / 28,500 traces, 0 failures)

- **Identity verified store-wide**: all 22,857 traces with a stored
  `post_bed_noise_dB` match the prototype exactly (rtol 1e-6, 0 mismatches).
- **Coverage on censored traces**: 334/340 (98%) get valid metrics; the 6
  misses are traces where the interpolated anchor leaves no valid window.
- Censored vs picked distributions:

  | metric | picked (median / p10 / p90) | censored (median / p10 / p90) |
  |---|---|---|
  | noise median dB | −153.7 / −164.6 / −134.4 | −144.9 / −158.7 / −113.9 |
  | window peak dB | −137.5 / −158.0 / −101.2 | −109.3 / −154.7 / −90.6 |
  | std dB | 2.4 / 1.3 / 10.8 | 7.8 / 1.4 / 14.4 |
  | peak − noise dB | 9.5 / 3.6 / 38.1 | 19.9 / 4.0 / 46.6 |

- **190/334 censored traces (57%) have >15 dB of energy above the window noise
  floor** — bed-like (or clutter) energy below the interpolated bed line that
  the picker did not pick. The censored peak−noise distribution is strongly
  bimodal: a quiet cluster (nothing in the window → genuinely no detectable
  bed) and an energetic cluster (~35–45 dB above floor → energy present but
  unpicked: off-line bed, clutter, or picker conservatism).
- Caveat: the censored "noise median" runs ~9 dB hotter than picked traces'.
  Part of that is real (censored traces cluster in rough/deep areas), part is
  contamination of the window by the unpicked energy itself — the same effect
  the bimodality shows. `record_tail_noise_dB` remains the cleaner pure-noise
  floor; the interp window metrics add localization below the expected bed.

## If promoted into the pipeline

Add three surface-side (qc_surface-masked) variables computed alongside
`compute_noise_powers`, with the anchor = pipeline pick where present else
interp inside the picked span. Costs one extra window pass per trace; no new
config. Names suggested: `post_bed_noise_interp_dB`, `post_bed_peak_interp_dB`,
`post_bed_std_interp_dB`.
