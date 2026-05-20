# Pre-surface and post-bed noise power variables

## Goal

Add two per-trace metrics that estimate the local noise floor on each side
of the ice column:

- `pre_surface_noise_dB` — mean power in a window from near the start of
  the record up to slightly before the surface pick.
- `post_bed_noise_dB` — mean power in a window from slightly after the bed
  pick to near the end of the record.

Together with the existing `surface_power_dB` and `bed_power_dB`, these let
us compute true SNRs (surface SNR, bed SNR) downstream and characterize the
noise environment for each frame/season.

## Config schema

Add a `processing.noise` block. All offsets are in microseconds of two-way
travel time (consistent with how twtt is most readable; the existing
`layer_margin_m` stays in metres because it has a physical interpretation
in ice, while these offsets are bounded by record edges).

```yaml
processing:
  noise:
    pre_surface:
      start_offset_us: 1.0    # window starts at twtt_axis[0] + start_offset_us
      end_offset_us: 1.0      # window ends at surface_twtt - end_offset_us
    post_bed:
      start_offset_us: 5.0    # window starts at bed_twtt + start_offset_us
      end_offset_us: 5.0      # window ends at twtt_axis[-1] - end_offset_us
```

Defaults applied in `config.py` if `processing.noise` is absent:
1 / 1 / 5 / 5 us. Pre-surface uses tight 1 us offsets — even 1 us before
the surface pick is well below the layer-peak sidelobes for the in-air
geometry. Post-bed uses larger 5 us offsets because in-ice sidelobes /
basal multiples are stronger and asymmetric on either side.

A trace produces NaN if its computed window is empty (e.g. bed pick within
`post_bed.start_offset_us` of record end). NaN propagates through the
existing QC `.where()` masking the same way the other metrics do.

## Computation

In `processing.py`, after `surf_twtt_peak / bed_twtt_peak` are computed
and aligned to `frame.slow_time`:

1. Convert config offsets us -> s.
2. Build per-trace window bounds:
   - `pre_lo = twtt_axis[0] + start`  (scalar)
   - `pre_hi = surface_twtt - end`    (per-trace)
   - `post_lo = bed_twtt + start`     (per-trace)
   - `post_hi = twtt_axis[-1] - end`  (scalar)
3. Per-trace **median** of `|frame.Data|` (linear power) in each window,
   then `10*log10(...)`. Median is rank-based, so computing it on linear
   power and then dB-converting is equivalent to taking the median of dB
   values directly, but the linear formulation matches the convention
   used for the peak-power metrics.

   Vectorization is awkward because each trace has a different window
   length, so a per-trace loop with `np.median` on the masked slice is
   the cleanest implementation. With ~800 samples * ~1700 traces per
   frame the loop is still cheap (<1 s in the prototype). If it shows up
   in profiling later, we can pad/mask into a 2D array and use
   `np.nanmedian` along axis 0.
4. Wrap results as `xr.DataArray(..., dims=("slow_time",), coords={"slow_time": frame.slow_time})`
   so they join naturally into `metric_vars` and pick up the QC mask.

Use the **median of linear power**: chosen over the linear mean because
the noise windows contain occasional bright outliers (precursor returns,
sidelobes, off-nadir clutter, basal multiples). The mean was pulled up
~18 dB above the true noise floor by these outliers in the prototype;
the median tracks the bulk of the distribution and gives a stable
thermal-noise-floor estimate for the pre-surface case while still
reflecting clutter density for the post-bed case.

## Prototype results — `Data_20121023_04_029` ... `_035` (2012_Antarctica_DC8)

Final settings: median aggregator, pre 1/1 us, post 5/5 us.

| frame | pre median (dB) | pre p95-p5 (dB) | post median (dB) | post valid / 1732 |
|-------|-----------------|-----------------|------------------|-------------------|
| 029   | -136.2          | 0.8             | -130.4           | 1732              |
| 030   | -136.6          | 0.5             | -128.8           | 1732              |
| 031   | -136.4          | 0.8             | -131.3           | 1732              |
| 032   | -136.0          | 1.0             | -117.6           | 1589              |
| 033   | -135.7          | 1.7             | -106.2           | 1521              |
| 034   | -135.3          | 7.0             | -103.7           | 1732              |
| 035   | -136.2          | 1.0             | -114.1           | 1121              |

`pre_surface_noise_dB` is essentially constant at -136 dB across all 7
frames (system noise floor), with frame 034 showing slight contamination
in some traces. `post_bed_noise_dB` varies meaningfully with the
post-bed environment: frames 029-031 sit ~5 dB above the noise floor
(clean), 033-035 sit 20-30 dB above (heavy clutter / basal scattering
visible in the radargrams). NaN counts for post-bed reflect traces where
the bed pick is within 5 us of the record end — these propagate as NaN
just like the existing layer-power metrics.

Plots: `outputs/noise_prototype/Data_20121023_04_{029..035}_noise_windows.png`
and `..._power_distributions.png`.

## Architecture / docs touchpoints

- `docs/architecture.md`: add the two new variables to the "Per-trace
  values" list.
- `config.py`: add `noise` block defaults.
- `processing.py`: add window computation, include vars in `metric_vars`.
- Tests:
  - Unit test for `mean_power_in_window` (or whatever the helper ends up
    being called): empty window -> nan, single-sample window, two-element
    sanity check.
  - Integration test that processes the test config and asserts the two
    new variables exist with the right shape and dtype.

## Migration of existing icechunk stores

The current `_zarr_append` silently skips variables that don't already
exist in the store. So:

- **New frames in a `--reprocess` run**: get the new variables on first
  write (encoded with the existing chunk size).
- **New frames in an incremental run on an existing store**: would
  *silently lack* the new variables because the existing zarr groups
  weren't created with them. This is the same behaviour that bit the
  RSSNR rollout per the comment in `store.py`.

Recommended path (gated on your approval — these touch S3):

1. Land the code change + tests + plot review.
2. For each existing S3 store (`ase`, `greenland`, `crosssystem`, `utig`):
   re-run with `--reprocess` to lay down fresh per-trace arrays that
   include the two new variables. This is the same approach used after
   RSSNR was added.
3. Update the viewer (`web/`) to expose the two new variables if/when we
   want them visible.

Alternative if a full `--reprocess` is too expensive: a one-off migration
script that loads each stored frame's existing trace range, re-fetches the
underlying radargram from OPR, computes only the noise variables, and
appends them as new zarr arrays sized to match the existing slow_time.
More complex; defer unless reprocess cost becomes an issue.

## Decisions

- Aggregator: **median** of linear power.
- Offsets: pre 1 / 1 us, post 5 / 5 us.
- Names: keep `pre_surface_noise_dB` and `post_bed_noise_dB` (the latter
  is really a noise-plus-clutter estimate, but parallel naming is
  preferred).
- `required_surface_snr_dB` behaviour unchanged for now (still uses
  `bed_power_dB` in the denominator). Any switch to noise-based SNR
  would be a separate change.
