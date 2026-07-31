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

### Crossover/crossing-angle test (`claude_notes/utig_crossing_angle.py`)

Prompted by the Greenland/Joe comparison, where the analogous +15 dB cluster
turned out to be cross-flight matching at line crossings. Tested whether UTIG's
+8 dB offset cluster is the same artifact by computing each track's local bearing
in EPSG:3031 (from subsequent along-track points) and the acute crossing angle
between OPR and UTIG tracks at each matched pair.

Note: OPR is 5 s-decimated (~465 m within-frame spacing) vs UTIG ~22 m, so the
flight-boundary gate in the bearing helper must be widened for OPR (2000 m) — the
200 m UTIG value nulls every OPR bearing.

Result — the two clusters are geometrically **indistinguishable**:

| Cluster | N | median angle | frac>30° | frac<10° | median match-dist |
|---|---|---|---|---|---|
| agree (≤8 dB) | 16,485 | 35.6° | 51.0% | 21.5% | 25.8 m |
| offset (>8 dB) | 5,484 | 36.6° | 52.1% | 16.7% | 25.2 m |

corr(rssnr_diff, crossing_angle) = −0.005; corr(rssnr_diff, match_distance) = −0.007.
Binned-median diff is flat (~0–2 dB) across all crossing angles (`crossing_angle.png`).

**Crossover hypothesis REJECTED for UTIG.** Unlike Greenland (where the offset
cluster had ~6 m match distance and matched a *different* flight), here both clusters
sit at ~25 m match distance with broadly-distributed crossing angles — because OPR
and UTIG are largely *different* surveys overlapping spatially, so essentially all
matches are cross-track. The +17 dB offset is independent of that geometry, so it is
intrinsic to the measurement/processing, not a track-matching artifact.

### What the bimodality IS: a weak-bed detection-sensitivity gap

Scripts: `claude_notes/utig_cluster_anatomy.py`, `utig_cluster_geo.py`, and the
`cluster_modes.png` / `cluster_map.png` plots. Hypotheses tested and ruled out:
- **Season / calibration batch** — N/A. Store is now ~all 2009 BaslerJKB (299 frames).
- **Per-frame / per-flight systematic** — NO. Offset rate varies 0–0.73 across frames
  but top-10% of frames hold only 15% of offset pairs (vs 83% for Greenland); scattered
  within frames, not whole-frame.
- **Surface-multiple mispick** — NO. bed_twtt/surface_twtt ≈ 9, nowhere near 2 (surface
  twtt is just the short air gap).
- **Single OPR field separating the clusters** — NO. corr(diff, ·) < 0.15 for bed power,
  surface power, thickness, RSSNR, match distance — no clean linear separator.

What it IS (every test points here): a **noise-floor / detection-sensitivity difference
in the weak-bed regime.**
- The difference histogram is genuinely bimodal (modes at ~−2 dB and ~+16 dB).
- The matched **UTIG** RSSNR distribution is itself bimodal (peaks ~78 and ~105–110 dB);
  the matched **OPR** distribution is unimodal and **truncates at ~110–112 dB**
  (matched OPR max 111.7 vs UTIG 126). OPR has no high-RSSNR second peak.
- Both clusters are deep interior ice (~3000 m). The offset cluster has weaker bed power
  (−119 vs −112 dB) and lower bed SNR (16.6 vs 24.3 dB) — i.e. the bed echo is fainter,
  approaching the noise floor.
- Where the bed is strong (well above both noise floors) the two measure the same bed
  power → agree (main mode). Where the bed is weak, OPR's bed-power estimate bottoms out
  near its noise floor so OPR RSSNR **saturates ~110–112 dB**, while UTIG (lower effective
  noise floor — more integration / finer focusing) follows the bed down and reports higher
  RSSNR. The ~constant gap between the two sensitivity limits → the discrete +offset.
- Three independent numbers agree on a ~15 dB sensitivity gap: the secondary diff mode
  (~+16 dB), the RSSNR ceiling gap (111.7 vs 126 ≈ 14 dB), and the low-bed-SNR effect
  (bed_snr<6 dB → 48% offset-rate vs 23% baseline).

**Conclusion:** the two clusters are "bed well above both noise floors" (agree) vs "weak
deep-interior bed that OPR's noise floor can't follow but UTIG's can" (+offset). This
refines the earlier "bed-picking failures" note: it is a weak-signal *sensitivity*
divergence at the detection limit, not random pick errors, and not a geometry/matching
artifact.

### What the UTIG distribution's two modes are by themselves

Scripts: `claude_notes/utig_self_bimodal.py` + `utig_self_hist.png` / `utig_self_modes.png`.
Looking at the **full** UTIG dataset (all 11.998M points), the RSSNR distribution has:
- a **sharp spike at exactly 0 dB** (~59k exact zeros, 0.5%) — a fill/sentinel value, plus
  some genuine near-zero (very bright bed) points; an artifact, not a geophysical mode;
- a **dominant broad mode ~78 dB** (90% of points lie 20–100 dB) — normal beds;
- a **small, broad secondary mode ~108 dB** (1.8%, std ~5 dB — a real population, not a
  repeated value).

The secondary mode is **strongly spatially segregated** (95% of 5 km cells are >80% a single
mode) into specific deep-interior regions/subglacial basins. It is the **weak-bed
population**: in the matched set the ≥100 dB mode has bed power ~14 dB weaker (−125 vs
−110 dB) and ice ~230 m thicker. UTIG RSSNR is driven mainly by **bed echo strength**
(corr RSSNR–bed_power = −0.76), with thickness/attenuation secondary (corr +0.45).

So "by itself" UTIG is really one dominant normal-bed mode plus a small weak-bed mode
(weak/deep-basin beds) and a 0-dB sentinel spike. The reason the modes looked so balanced
in the OPR comparison is sampling: **OPR's 2009 footprint over-sampled the weak-bed regions**,
inflating the high mode from 1.8% of all UTIG to 15.9% of matched points.

### Separating the modes from the OPR side via bed SNR

Script: `claude_notes/opr_snr_separation.py` + `opr_snr_separation.png`. Define OPR bed
SNR = `bed_power_dB - post_bed_noise_dB` (bed return above its local noise floor).

**OPR's RSSNR and bed SNR are complementary** — a tight negative diagonal (RSSNR vs bed
SNR hexbin). This is algebraic: RSSNR = surf_power − bed_power + geo and bed_snr =
bed_power − noise, so RSSNR + bed_snr = surf_power − noise + geo ≈ const for fixed
surface/noise/geometry. Hence **high RSSNR ⟺ low bed SNR**, and OPR's RSSNR ceiling
(~112 dB) is just where bed_snr → 0 (bed power hits the noise floor; it can't be measured
weaker). This directly confirms the saturation mechanism inferred earlier.

So low bed SNR **cleanly separates OPR's own high-RSSNR (weak-bed) mode**:

| bed SNR | N (%) | RSSNR median |
|---|---|---|
| < 6 dB | 7.6% | 98.3 |
| 6–15 dB | 20.8% | 90.7 |
| ≥ 15 dB | 71.6% | 74.7 |

- High-RSSNR mode (R≥95) has bed SNR median **4.6 dB** vs **24.7 dB** for the rest.
- AUC(low bed SNR → high-RSSNR mode) = **0.97** (near-perfect).
- `bed_snr < 6 dB`: precision 84% / recall 65% for the hi-mode; `bed_snr < 10 dB`: recall 89%.

But for predicting the **UTIG cross-dataset offset** specifically, low OPR bed SNR is only a
**modest** predictor (AUC ≈ 0.63; bed_snr<6 → 47% offset vs 23% baseline). Makes sense: OPR
bed SNR tells you when OPR is near *its* noise floor, but whether UTIG then disagrees depends
on UTIG's (unseen) sensitivity. 

**Bottom line:** yes — from OPR alone, the weak-bed / high-RSSNR mode is essentially the
low-bed-SNR population and is cleanly flagged by a bed-SNR threshold (~6–10 dB). A
`min_bed_snr_db` QC gate (already a config hook in `qc`) would remove that mode. It cannot
fully isolate the UTIG disagreement, which is a two-sided sensitivity effect.

### Does OPR bed SNR explain the bimodality of the *mismatch* (UTIG−OPR)?

Script: `claude_notes/mismatch_vs_oprsnr.py` + `mismatch_vs_oprsnr.png`. Test = condition
the mismatch on OPR bed SNR; if OPR SNR explains it, the offset mode should vanish at high
bed SNR. It does not.

Offset-mode fraction (diff>8 dB) vs OPR bed SNR: <6 dB → 47.5%; 6–12 → 41%; 12–18 → 27%;
18–25 → 19%; ≥25 dB → **18.3%** (never collapses). Within `bed_snr≥20` the mismatch is
still bimodal (main peak ~−2 dB plus a distinct ~+18 dB bump). **65% of all offset-mode
pairs have OPR bed SNR ≥12 dB** (OPR not near noise); 41% have ≥20 dB.

**Conclusion: OPR SNR only partially explains the mismatch bimodality.** Low OPR bed SNR
(OPR bottoming out at its noise floor) is a real but minority contributor — it ~triples the
offset rate (18%→47%) but accounts for a minority of offset pairs. The bulk of the +18 dB
offset mode occurs where OPR sees a good bed yet UTIG still reports ~18 dB higher; that
residual is a UTIG-side effect (UTIG's own weak-bed mode / bed-power estimate) invisible to
OPR's fields. Fully closing it needs UTIG bed-power + noise-floor (not in the CSV).

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
