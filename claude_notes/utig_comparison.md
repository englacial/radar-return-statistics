# UTIG (Texas) RSSNR comparison notes

Notes from comparing UTIG-processed RSSNR (provided as a CSV) against
OPR re-processing of the same BaslerJKB seasons. Useful next time we
look at this dataset.

## Data sources

- **OPR side**: `config/config_utig.yaml` — four BaslerJKB seasons
  (2008/2016/2017/2018), no region filter, 5 s decimation, written to
  `s3://opr-radar-metrics/icechunk/utig/`. Last run produced 302 frames /
  32,796 traces (snapshot `ZP3DPMFPTBFAM6SN28H0`).
- **UTIG side**: `reference/utig-processed-snr/snr.csv` (~447 MB,
  gitignored). Columns: `snr`, `x`, `y` (EPSG:3031 m). 11.99M traces.
- **Sign convention**: the `snr` column is the *negative* of our RSSNR
  definition — `load_utig` negates it on load.

## Matching pipeline (`scripts/analysis/utig_comparison.py`)

- One-to-one match via cKDTree at 50 m. Query is OPR → UTIG (OPR is the
  smaller set), then conflicts (multiple OPR claiming one UTIG) resolved
  by minimum distance.
- Spatial coverage: only 0.2% of UTIG traces have an OPR match within
  50 m (we cover a small fraction of the BaslerJKB tracklines); 86% of
  QC-passing OPR traces find a UTIG match.

## Difference distribution

After matching (50 m, 5 s decimation):
- N = 22,466 pairs
- Mean diff = +1.17 dB, median = −0.99 dB, RMS = 11.44 dB

Distribution is bimodal-ish but smooth — DBSCAN and GMM both failed to
find a real cluster boundary. We use a `--diff-threshold` hard cut
(default 8 dB) just for visualization:
- ≤ 8 dB: N=16,837, mean −4.22 dB, RMS 8.10 dB (close to 1:1 line)
- > 8 dB: N=5,629, mean +17.28 dB, RMS 18.06 dB (a high-offset tail)

The high-offset population is probably real bed-picking failures
(wrong layer / multipath), not a layer-margin issue — see below.

## Layer-margin sweep (`scripts/analysis/margin_sweep.py`)

Recomputes OPR RSSNR at full resolution for each frame at multiple
`layer_margin_m` values and reports diff stats:

| Margin | Mean | Median | RMS |
|-------:|-----:|-------:|----:|
|  10 m | +8.76 | +7.36 | 15.60 |
|  50 m | +1.18 | −0.95 | 11.45 |
| 100 m | +2.67 | −0.17 | 11.42 |
| 250 m | +3.83 | +0.51 | 11.59 |

Takeaway: **10 m is too narrow** (the true bed peak often falls outside
the ±10 m window → OPR underestimates power, big positive bias).
50/100/250 m are within 0.2 dB RMS of each other — widening doesn't
help. The residual 11.4 dB RMS is something else (probably bed picks).

The 50 m sweep number (11.45 dB) matches the stored OPR RSSNR comparison
(11.44 dB), so the margin sweep is self-consistent.

## Per-frame debug (`scripts/analysis/frame_debug.py`)

Picks one frame (default: the one with the most matched pairs —
currently `Data_20090130_01_002`, 155 pairs) and plots the full-res
radargram with surface/bed picks overlaid and a lower panel comparing
UTIG vs OPR RSSNR at each matched position.

CLI flag `--layer-margin-m` overrides the config margin for the debug
recomputation without touching `config_utig.yaml`. Generated three
debug versions (10/50/250 m) into `outputs/utig_comparison/`.

## Things worth trying next

- Look at the high-offset (>8 dB) population spatially — is it
  concentrated on specific seasons / regions?
- Compare bed-pick TWTTs directly (UTIG layer file vs OPR `standard:bottom`)
  — if the picks themselves disagree, that's where the RMS comes from.
- The 105 perpetually-failing QC frames in the ASE pipeline (see
  `claude_plans/20260412-ase-season-processing.md`) — same idea could
  apply here: track failed frames so they aren't retried every run.
