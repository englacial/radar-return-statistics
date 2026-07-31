# Record-tail window sensitivity study (2026-07-30)

Script: `claude_notes/tail_window_sensitivity.py`
Outputs: `outputs/tail_window_sensitivity/{summary.csv,profiles.npz,profiles.png}`

24 mid-segment frames across 12 collections (4 ASE DC8 seasons, 2013 P3,
2013/2017 Basler, 2008 BaslerJKB, 2019 GV, 2023 BaslerMKB, 2014/2018
Greenland P3). Median-across-traces power profile vs offset from the record
end, normalized to a 10–25 µs plateau.

## Findings

Two distinct behaviors — do not conflate them:

1. **Sharp end-of-record rolloff** (post-processing filter edge): a dip in the
   final microseconds that recovers to the plateau. Present in most seasons.
   Recovery offsets among frames with a genuine flat plateau: mostly 6–8 µs
   (2013_P3 7.1–7.3, 2014_DC8 6.4, 2017_Basler 5.8–6.2, 2014_Greenland 8.1);
   one gentle case at 10.2 µs (2018_DC8, ~1 dB level). 2019_GV and 2018_DC8
   mostly show <2 µs.
2. **No plateau at all** (11/24 frames: 2012_DC8 both frames, and one frame
   each in several other seasons): power still decaying 30 µs from the end —
   deep englacial/bed energy, not rolloff. For these the tail window is an
   upper bound on the noise floor regardless of placement; they were excluded
   from the bias statistics.

## Window choice (5 µs windows [e, e+5] back from the end, flat frames only)

| end offset e | median bias | worst bias |
|---|---|---|
| 0 (old behavior) | −2.60 | −15.54 |
| 5 (user guess)   | −0.27 | −1.43 |
| 7                | −0.12 | −1.03 |
| 8                | −0.11 | −0.91 |
| 10               | −0.08 | −0.63 |

Chosen default: **`start_offset_us: 12, end_offset_us: 7`** — clears the
common 6–8 µs rolloffs, worst residual ≈ −1 dB (one gentle-rolloff frame),
while staying close to the record end (larger offsets drift into bed/englacial
energy, e.g. 2008_JKB bumps at 17+ µs). Configurable per dataset via
`processing.noise.record_tail`.
