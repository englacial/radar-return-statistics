"""Diagnose whether 2014 post-bed 'noise' values are real noise or a processing artifact.

Compares the within-window sample distributions for 2012 and 2014 frames.
Real coherent-detector noise should have a chi-square-like distribution (some
spread, exponential-ish tails). A constant digitizer floor or zero-fill would
show as a degenerate (near-delta) distribution at a single value.
"""

import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from xopr import OPRConnection

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from radar_return_statistics.processing import SURFACE_KEY, BED_KEY


CASES = [
    ("Data_20121023_04_032", "2012_Antarctica_DC8", "20121023_04"),
    ("Data_20141029_05_034", "2014_Antarctica_DC8", "20141029_05"),
]


def main():
    out_dir = Path("outputs/noise_prototype")
    out_dir.mkdir(parents=True, exist_ok=True)
    opr = OPRConnection()

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), constrained_layout=True)

    for row, (fid, collection, segment) in enumerate(CASES):
        print(f"\n=== {fid} ({collection}) ===")
        frames_gdf = opr.query_frames(collections=[collection], segment_paths=[segment])
        frame = opr.load_frame(frames_gdf.loc[fid], data_product="CSARP_standard").sortby("slow_time")
        layers = opr.get_layers(frame, include_geometry=False)
        surf = layers[SURFACE_KEY]["twtt"].reindex(
            slow_time=frame.slow_time, method="nearest",
            tolerance=pd.Timedelta(seconds=5), fill_value=np.nan,
        ).values
        bed = layers[BED_KEY]["twtt"].reindex(
            slow_time=frame.slow_time, method="nearest",
            tolerance=pd.Timedelta(seconds=5), fill_value=np.nan,
        ).values

        data_lin = np.abs(frame.Data.values)  # (n_twtt, n_traces)
        twtt = frame.twtt.values
        print(f"  twtt: {twtt[0]*1e6:.3f} to {twtt[-1]*1e6:.3f} us, dt={(twtt[1]-twtt[0])*1e9:.2f} ns")
        print(f"  Data dtype: {frame.Data.dtype}")

        # Pre-surface and post-bed sample populations across all traces
        pre_samples = []
        post_samples = []
        sample_count_pre = []
        sample_count_post = []
        for i in range(data_lin.shape[1]):
            s, b = surf[i], bed[i]
            if not (np.isfinite(s) and np.isfinite(b)):
                continue
            pre_mask = (twtt >= twtt[0] + 1e-6) & (twtt <= s - 1e-6)
            post_mask = (twtt >= b + 5e-6) & (twtt <= twtt[-1] - 5e-6)
            if pre_mask.any():
                pre_samples.append(data_lin[pre_mask, i])
                sample_count_pre.append(pre_mask.sum())
            if post_mask.any():
                post_samples.append(data_lin[post_mask, i])
                sample_count_post.append(post_mask.sum())

        pre_all = np.concatenate(pre_samples) if pre_samples else np.array([])
        post_all = np.concatenate(post_samples) if post_samples else np.array([])

        # Stats on the raw samples
        for name, arr in [("pre", pre_all), ("post", post_all)]:
            if arr.size == 0:
                print(f"  {name}: empty")
                continue
            arr_db = 10 * np.log10(np.maximum(arr, 1e-40))
            unique_vals = np.unique(arr)
            n_zeros = int((arr == 0).sum())
            print(f"  {name}: n_samples={arr.size}  n_unique={len(unique_vals)}  "
                  f"n_zeros={n_zeros}")
            print(f"    linear:   min={arr.min():.3e}  median={np.median(arr):.3e}  max={arr.max():.3e}")
            print(f"    dB:       p1={np.percentile(arr_db, 1):.2f}  median={np.median(arr_db):.2f}  "
                  f"p99={np.percentile(arr_db, 99):.2f}")
            print(f"    dB spread: p99-p1={np.percentile(arr_db, 99)-np.percentile(arr_db, 1):.2f}  "
                  f"std={arr_db.std():.2f}")

        # Plot dB histograms for both windows
        for col, (name, arr) in enumerate([("pre-surface", pre_all), ("post-bed", post_all)]):
            ax = axes[row, col]
            if arr.size == 0:
                ax.set_title(f"{fid} {name}: empty")
                continue
            arr_db = 10 * np.log10(np.maximum(arr, 1e-40))
            ax.hist(arr_db, bins=120, color="steelblue", edgecolor="none")
            ax.set_xlabel("Sample power (dB)")
            ax.set_ylabel("Count")
            ax.set_title(f"{fid}  {name} window samples (n={arr.size})")
            ax.set_yscale("log")

    out_path = out_dir / "noise_floor_check.png"
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
