"""Post-run sanity check for one season of the antarctica S3 store.

Usage: uv run python claude_notes/antarctica_season_check.py <collection>
Prints frame/trace counts and per-variable stats (NaN rate, min/max/median)
for the season's traces, for eyeballing against expectations.
"""
import sys

import icechunk
import numpy as np
import zarr

collection = sys.argv[1]

storage = icechunk.s3_storage(
    bucket="opr-radar-metrics", prefix="icechunk/antarctica", region="us-west-2",
    from_env=True,
)
repo = icechunk.Repository.open(storage=storage)
root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")

frame_names = root.attrs["frame_names"]
frame_cols = root.attrs.get("frame_collections", [])
season_idx = [i for i, c in enumerate(frame_cols) if c == collection]
if not season_idx:
    print(f"{collection}: NO FRAMES IN STORE")
    sys.exit(1)

frame_index = root["frame_index"][:]
mask = np.isin(frame_index, season_idx)
n_frames = len(season_idx)
n_traces = int(mask.sum())
print(f"{collection}: {n_frames} frames, {n_traces} traces "
      f"(store total: {len(frame_names)} frames, {len(frame_index)} traces)")

VARS = ["surface_twtt", "bed_twtt", "surface_elevation", "bed_elevation",
        "surface_power_dB", "bed_power_dB", "required_surface_snr_dB",
        "pre_surface_noise_dB", "post_bed_noise_dB", "latitude", "longitude"]
print(f"\n{'variable':<24} {'nan%':>6} {'min':>10} {'median':>10} {'max':>10}")
for var in VARS:
    v = root[var][:][mask].astype(float)
    nanpct = 100 * np.isnan(v).mean()
    if np.all(np.isnan(v)):
        print(f"{var:<24} {nanpct:>5.1f}%        all-NaN")
        continue
    print(f"{var:<24} {nanpct:>5.1f}% {np.nanmin(v):>10.3g} {np.nanmedian(v):>10.3g} {np.nanmax(v):>10.3g}")

qc = root["qc_pass"][:][mask]
print(f"\nqc_pass rate among stored traces: {100 * qc.mean():.1f}%")
