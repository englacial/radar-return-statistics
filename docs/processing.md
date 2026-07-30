# Processing Data

This guide covers how to process radar sounder data and store the results in icechunk.

## Setup

Install dependencies with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

AWS credentials are required for S3-backed stores. Set `AWS_PROFILE` or use the standard
AWS credential chain. Credentials are loaded via `icechunk.s3_storage(from_env=True)`.

## Configuration

Processing is controlled by a YAML config file. Production configs live in
`config/` (`config_greenland.yaml`, `config_antarctica.yaml`, `config_ase.yaml`,
`config_utig.yaml`, `config_crosssystem.yaml`); `config/test_config.yaml` is a
small local-store test run.

### Region selection

The `region` section defines the geographic area to query:

```yaml
region:
  area: "antarctic"        # or "greenland"
  subregion: "G-H"  # MEaSUREs Antarctic Boundaries subregion (optional)
```

Omitting `subregion`/`name` queries the full area (used by the greenland and
antarctica configs).

Using `subregion` automatically includes associated ice shelves from the MEaSUREs dataset.
Available subregions: `A-Ap`, `Ap-B`, `B-C`, `C-Cp`, `Cp-D`, `D-Dp`, `Dp-E`, `E-Ep`,
`Ep-F`, `F-G`, `G-H`, `H-Hp`, `Hp-I`, `I-Ipp`, `Ipp-J`, `J-Jpp`, `Jpp-K`, `K-A`.

Alternatively, specify regions by name:

```yaml
region:
  area: "antarctic"
  name: ["Thwaites", "Pine_Island"]
```

### Store configuration

Local filesystem:

```yaml
store:
  backend: "local"
  path: "outputs/icechunk_store"
```

S3:

```yaml
store:
  backend: "s3"
  s3_bucket: "opr-radar-metrics"
  s3_prefix: "icechunk/ase"
  s3_region: "us-west-2"
```

### Processing settings

```yaml
processing:
  data_product: "CSARP_standard"
  decimate_interval: "10s"   # keep ~1 trace per interval of slow_time
  layer_margin_m: 50         # peak search window around each layer pick
  ice_permittivity: 3.17
  max_workers: 20
  checkpoint_every: 200      # optional: commit every N frames (resumable long runs)
  noise:
    pre_surface: {start_offset_us: 1.0, end_offset_us: 1.0}
    post_bed:    {start_offset_us: 5.0, end_offset_us: 5.0}
    record_tail: {duration_us: 5.0}   # pick-independent noise floor window
```

Set `opr.cache_dir` to a path to cache downloaded frames locally (fast reruns,
large disk footprint) or `null` to stream (used by the full-continent configs).

### QC settings

```yaml
qc:
  max_heading_change_deg_per_km: 2.0  # null to disable
  min_ice_thickness_m: 100
  min_agl_m: 50
  min_bed_snr_db: null
  min_traces_after_qc: 15
```

Checks are split into **pick-independent** (heading change, minimum AGL) and
**pick-dependent** (ice thickness, bed SNR — both fail where the bed pick is
missing). Surface-side metrics are masked by the pick-independent AND
(`qc_surface_pass`); bed-side metrics by the full AND (`qc_pass`).
`min_traces_after_qc` counts pick-independent passes, so frames whose bed was
largely unpickable (low SNR) are still stored with surface-only rows — see
`claude_plans/20260729-missing-bed-pick-schema.md` for the rationale.

Frames whose segment has no bed layer (or a bed layer with zero picks) are
skipped entirely and are not marked processed, so they are re-attempted on
every incremental run.

Note: some older seasons (pre-2012) lack the `Heading` variable and will fail the
heading change check. Set `max_heading_change_deg_per_km: null` to process these.
Some seasons (e.g. 2019_Antarctica_GV) publish bed picks under the `:bottom`
layer key; the pipeline falls back to it automatically when `standard:bottom`
is absent.

## Running the pipeline

### Process all collections matching the config

```bash
uv run python -m radar_return_statistics config/config_antarctica.yaml
uv run python -m radar_return_statistics config/config_antarctica.yaml -v        # debug logging
uv run python -m radar_return_statistics config/config_antarctica.yaml --reprocess  # reprocess all
uv run python -m radar_return_statistics config/config_antarctica.yaml -m "Custom commit message"
```

### Collection management

The collections utility lists available data and allows processing by collection name:

```bash
# List all collections in the region with processing status
uv run python -m radar_return_statistics.collections config/config_antarctica.yaml

# Process specific collections
uv run python -m radar_return_statistics.collections config/config_antarctica.yaml process 2018_Antarctica_DC8
uv run python -m radar_return_statistics.collections config/config_antarctica.yaml process 2016_Antarctica_DC8 2014_Antarctica_DC8

# Limit frames per collection
uv run python -m radar_return_statistics.collections config/config_antarctica.yaml process 2018_Antarctica_DC8 --max-items 50

# Custom commit message
uv run python -m radar_return_statistics.collections config/config_antarctica.yaml process 2018_Antarctica_DC8 -m "Initial 2018 processing"

# Reprocess (clear existing data for selected collections)
uv run python -m radar_return_statistics.collections config/config_antarctica.yaml process 2018_Antarctica_DC8 --reprocess
```

When no commit message is specified, the commit automatically includes the collection
name(s) in the message, e.g. `2018_Antarctica_DC8: Processed 199 frames (7078 traces)`.

## Icechunk versioning

Each processing run creates a new commit in the icechunk store. You can inspect the
commit history:

```python
import icechunk
from radar_return_statistics.store import make_storage
from radar_return_statistics.config import load_config

config = load_config("config/config_antarctica.yaml")
storage = make_storage(config["store"])
repo = icechunk.Repository.open(storage=storage)

for snap in repo.ancestry(branch="main"):
    print(f"{snap.id} | {snap.written_at} | {snap.message}")
```

To roll back to a previous snapshot:

```python
repo.reset_branch("main", "SNAPSHOT_ID_HERE")
```

## Generating visualizations

### Static maps (all variables)

```bash
uv run python -m radar_return_statistics.visualize_map config/config_antarctica.yaml
uv run python -m radar_return_statistics.visualize_map config/config_antarctica.yaml --output-dir outputs/maps
uv run python -m radar_return_statistics.visualize_map config/config_antarctica.yaml -v surface_power_dB -v bed_power_dB
```

### Per-frame figures

```bash
uv run python -m radar_return_statistics.visualize_frame config/config_antarctica.yaml Data_20181022_01_010
uv run python -m radar_return_statistics.visualize_frame config/config_antarctica.yaml Data_20181022_01_010 --output-dir outputs/figures
```

### Interactive web viewer

The `web/` directory contains a browser-based viewer that reads directly from the S3
icechunk store using `@carbonplan/icechunk-js`. To run locally:

```bash
cd web
npm install
npx vite
```

The store URL is configured in `web/src/config.ts`.
