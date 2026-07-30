# Radar Return Statistics

**Just want to look at the data?** Online viewer is here:
[https://docs.englacial.org/radar-return-statistics](https://docs.englacial.org/radar-return-statistics)

Extract per-trace radar return statistics from the [xOPR](https://github.com/englacial/xopr)
archive and store results in a versioned [icechunk](https://icechunk.io/) store.

For each trace, the pipeline extracts surface and bed return power, two-way travel times,
elevations, noise-floor estimates, and the required surface SNR metric. Results are
decimated, QC-flagged, and committed to icechunk with full version history. Bed-pick
availability is tracked explicitly so that picks missing due to low SNR can be treated
as censored observations downstream rather than silently missing data.

Public stores live at `s3://opr-radar-metrics/icechunk/{antarctica,greenland}`
(see [data access](docs/data_access.md) for the full list and how to read them).

You are welcome to use them as-is, but we make no promises about their suitability for
whatever you may be doing or the long-term reliability of this URL. Please get in touch
if you're finding this useful so we can support you. Or feel free to fork this repo
and build your own.

There is an online viewer available at
[https://docs.englacial.org/radar-return-statistics](https://docs.englacial.org/radar-return-statistics)
if you just want to preview the data.

## Documentation

More documentation is in the `docs/` directory:

- **[Architecture](docs/architecture.md)** -- output metrics, module layout, and pipeline design
- **[Processing data](docs/processing.md)** -- running the pipeline, managing collections,
  configuration reference, and generating visualizations
- **[Accessing the data](docs/data_access.md)** -- reading the icechunk stores from Python
  and JavaScript, variable descriptions, and working with version history
- **[Interactive viewer](docs/viewer.md)** -- launching the browser-based map viewer (`cd web && npm run dev`)
- **[S3 setup](docs/s3-setup.md)** -- bucket, permissions, and CORS configuration

## Quick start

```bash
uv sync
uv run python -m radar_return_statistics config/test_config.yaml   # small local test run
uv run python -m radar_return_statistics config/config_antarctica.yaml  # production (needs AWS credentials)
```

## Output variables

| Variable | Description |
|----------|-------------|
| `surface_power_dB` | Surface peak return power (dB) |
| `bed_power_dB` | Bed peak return power (dB) |
| `surface_elevation` | Surface elevation (m WGS84) |
| `bed_elevation` | Bed elevation (m WGS84) |
| `required_surface_snr_dB` | Geometric-spreading-corrected surface-to-bed power ratio (dB) |
| `surface_twtt`, `bed_twtt` | Two-way travel times (s) |
| `pre_surface_noise_dB`, `post_bed_noise_dB` | Noise floor before the surface / after the bed pick (dB) |
| `record_tail_noise_dB` | Pick-independent noise floor from the record tail (dB) |
| `bed_pick_available`, `bed_pick_attempted`, `bed_pick_quality` | Bed-pick availability, picked-span membership, and OPR quality flag |
| `qc_pass`, `qc_surface_pass` | Full and pick-independent QC flags (mask bed-side / surface-side metrics respectively) |

See [docs/architecture.md](docs/architecture.md) for the complete list and definitions.
