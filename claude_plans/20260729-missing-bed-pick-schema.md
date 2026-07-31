# Plan: schema + processing changes for statistically usable bed-pick missingness

Status: **implemented** (2026-07-29); ase reprocessed as the pilot store.

Refinement (from discussion): a frame with zero picks inside a segment that
does have picks is **included** in the store (`frame_bed_pick_fraction = 0`);
both `frame_bed_pick_fraction` and `segment_bed_pick_fraction` are stored so
analyses can gate on segment-level effort and keep those frames in the
censored population. Only segments whose bed layer is absent or contains zero
finite picks are skipped.

## Goal

Let downstream users treat "bed pick absent because SNR was too low" as data
(censored observations) rather than silently missing rows, while keeping
never-picked flights out of that population.

Framing: on a frame where bed picking was attempted, a trace that passes all
pick-independent QC but has no bed pick is a **right-censored RSSNR
observation** — the bed return was below what the picker could detect. With the
surface power and noise floor stored for that trace (plus an external thickness
prior such as BedMachine for the geometric term), downstream can compute a
censoring bound and do Tobit/survival-style estimation or bed-detectability
maps instead of inheriting missing-not-at-random bias.

## Bias source in the current pipeline (must fix, not just add columns)

With `min_ice_thickness_m` enabled, a missing bed pick fails the ice-thickness
QC check (NaN thickness → fail). Consequences today:

1. Traces missing bed picks get **all** metrics NaN-masked — including surface
   power and noise floor, exactly the quantities needed to characterize why the
   bed wasn't picked.
2. A frame where bed picking mostly failed (low-SNR area!) can fall below
   `min_traces_after_qc` and be **dropped entirely** — the store
   systematically under-represents the hardest-to-sound regions. This is the
   "whole flight contamination" problem in miniature, and it's currently
   invisible.

## Schema additions (per-trace, `slow_time` dimension, unmasked)

| Variable | Type | Meaning |
|---|---|---|
| `bed_pick_available` | bool | A bed pick aligned to this trace (nearest within the 1 s extraction tolerance), evaluated **before** QC/masking |
| `bed_pick_quality` | int8 | OPR layer quality flag (1 good / 2 moderate / 3 derived); −1 where no pick or flag unavailable (DB-sourced layers) |
| `qc_heading_pass` | bool | heading-change check alone |
| `qc_agl_pass` | bool | minimum-AGL check alone |
| `qc_surface_pass` | bool | AND of all **pick-independent** checks (convenience) |

Root attrs, parallel to `frame_names` (like `frame_collections`):

- `frame_bed_pick_fraction`: fraction of decimated traces in the frame with a
  bed pick, pre-QC. Lets analyses set their own "picking effort" threshold
  instead of baking one into the store.
- `segment_bed_pick_fraction`: fraction of picks present across the whole
  segment's bed layer — the effort measure that keeps zero-pick frames inside
  diligently picked segments usable as censored observations.

`qc_pass` keeps its current meaning (full AND) for compatibility.

## Masking / frame-retention changes

Split QC checks into **pick-independent** (heading, AGL) and **pick-dependent**
(ice thickness, bed SNR):

- Surface-side variables (`surface_twtt`, `surface_power_dB`,
  `surface_elevation`, `pre_surface_noise_dB`) masked by pick-independent QC
  only.
- Bed-side variables (`bed_*`, `required_surface_snr_dB`, `post_bed_noise_dB`)
  masked by full QC, as today.
- `min_traces_after_qc` evaluated against **pick-independent** QC, so frames in
  low-SNR areas survive with surface-only rows.

## Frame-level policy (the "unpicked flights" gate)

- Segment has **no bed layer at all** (2016/2018_BaslerJKB pattern): skip the
  frame, as today. These never enter the store, so they can't contaminate the
  missingness population. Optionally record them in a root attr
  `skipped_no_bed_layer` for auditability.
- Segment has a bed layer: process every frame, even frames whose own traces
  have zero picks (`frame_bed_pick_fraction = 0` marks them; downstream decides
  whether segment-level effort is sufficient evidence of attempted picking).

Downstream censored-set definition then becomes:

```
qc_surface_pass & bed_pick_attempted & ~bed_pick_available & (fraction gate)
```

with the fraction gate chosen by the analysis (t≈0.25–0.5 excludes
token-effort frames; segment-level fraction keeps zero-pick frames inside
diligently picked segments).

Additions (2026-07-29, after review):

- `bed_pick_attempted` (bool, per trace): trace lies within the segment's
  picked span (first→last finite bed pick), so leading/trailing unpicked
  stretches at segment edges are excluded from the censored population.
  `bed_pick_available` always implies attempted.
- `record_tail_noise_dB` (surface-side masking): median power in the final
  `record_tail.duration_us` (default 5 µs) of the record — a pick-independent
  noise floor so every censored trace has a below-the-ice floor estimate.
  Upper bound where deep returns reach the record end (conservative censoring).

## Implementation sketch

- `processing.py`: compute aligned pick availability + quality before QC; run
  xopr QC checks individually (they already emit per-check flags) instead of
  only the combined `qc`; apply the split masking; emit the new variables.
- `store.py`: add the new arrays; write `frame_bed_pick_fraction` parallel to
  `frame_names` in `update_frame_index`.
- Config: no new knobs required; `min_traces_after_qc` semantics change is the
  only behavioral config note.
- Viewer: unaffected (additive columns); could later add a "bed pick coverage"
  display variable nearly for free.
- Tests: pick-missing trace keeps surface metrics; frame with mostly-missing
  bed survives; fraction attr correctness; quality passthrough.

## Rollout

Backfill options:

1. **Layers-only backfill** (cheap: reads layer files, no radar data) can add
   `bed_pick_available`, `bed_pick_quality`, `frame_bed_pick_fraction` to
   existing stores — but cannot recover the surface metrics already NaN-masked,
   nor resurrect frames dropped by the old `min_traces` gate.
2. **Full reprocess** (recommended for antarctica + greenland): restores
   everything. At observed throughput this is roughly 5–6 h per store of
   machine time.

Because option 1 cannot fix the two bias mechanisms above, recommend option 2
for any store that will feed the missingness analysis.
