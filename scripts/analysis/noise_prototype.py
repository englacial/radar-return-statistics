"""Prototype pre_surface_noise_dB and post_bed_noise_dB extraction.

Loads a single frame, computes per-trace surface/bed peak power plus noise
power averaged in pre-surface and post-bed windows, and renders diagnostic
plots so the windowing scheme can be reviewed before wiring it into the
production pipeline.

Run:
    uv run python scripts/analysis/noise_prototype.py
"""

import sys
from pathlib import Path

import click
import numpy as np
import pandas as pd
import scipy.constants
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from xopr import OPRConnection

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from radar_return_statistics.processing import (
    SURFACE_KEY,
    BED_KEY,
    extract_layer_peak_power,
)


DEFAULT_FRAME_IDS = ("Data_20121023_04_032",)


def derive_collection(frame_id: str) -> str:
    """Infer the Antarctica DC8 collection from a frame ID like Data_YYYYMMDD_XX_NNN."""
    parts = frame_id.split("_")
    year = parts[1][:4] if parts[0] == "Data" else parts[0][:4]
    return f"{year}_Antarctica_DC8"


def median_power_in_window_dB(power_lin_trace, twtt_axis, t_start, t_end):
    """Median noise power (dB) within [t_start, t_end] for a single trace.

    Median is computed on linear power and then converted to dB. Returns nan
    if the window is empty or contains no finite samples.
    """
    if not np.isfinite(t_start) or not np.isfinite(t_end) or t_end <= t_start:
        return np.nan
    mask = (twtt_axis >= t_start) & (twtt_axis <= t_end)
    if not mask.any():
        return np.nan
    samples = power_lin_trace[mask]
    samples = samples[np.isfinite(samples)]
    if samples.size == 0:
        return np.nan
    return 10.0 * np.log10(np.median(samples))


def extract_noise_power(frame, surface_twtt, bed_twtt,
                        pre_start_offset, pre_end_offset,
                        post_start_offset, post_end_offset):
    """Compute per-trace pre-surface and post-bed noise power (dB).

    All offsets are in seconds of two-way travel time.

    Pre-surface window: [twtt_axis[0] + pre_start_offset, surface_twtt - pre_end_offset]
    Post-bed window:    [bed_twtt + post_start_offset, twtt_axis[-1] - post_end_offset]
    """
    twtt_axis = frame.twtt.values
    data_lin = np.abs(frame.Data.values)  # CSARP standard is power-detected; |Data| is linear power
    n_traces = data_lin.shape[1] if data_lin.shape[0] == twtt_axis.size else data_lin.shape[0]

    # Confirm orientation: extract_layer_peak_power treats Data as (twtt, slow_time)
    # so columns are traces.
    assert data_lin.shape[0] == twtt_axis.size, "Expected Data shape (twtt, slow_time)"
    n_traces = data_lin.shape[1]

    twtt_first = twtt_axis[0]
    twtt_last = twtt_axis[-1]

    pre_noise = np.full(n_traces, np.nan)
    post_noise = np.full(n_traces, np.nan)

    for i in range(n_traces):
        s = surface_twtt[i]
        b = bed_twtt[i]
        pre_noise[i] = median_power_in_window_dB(
            data_lin[:, i], twtt_axis,
            twtt_first + pre_start_offset,
            s - pre_end_offset,
        )
        post_noise[i] = median_power_in_window_dB(
            data_lin[:, i], twtt_axis,
            b + post_start_offset,
            twtt_last - post_end_offset,
        )

    return pre_noise, post_noise


def process_frame_id(frame_id, collection, frames_gdf, out_dir,
                     pre_start_offset_us, pre_end_offset_us,
                     post_start_offset_us, post_end_offset_us,
                     ice_permittivity, layer_margin_m, opr):
    if frames_gdf is None or frame_id not in frames_gdf.index:
        click.echo(f"Frame {frame_id} not found in segment query.")
        return
    stac_item = frames_gdf.loc[frame_id]

    click.echo(f"\n=== {frame_id} ===")
    click.echo("Loading radargram...")
    frame = opr.load_frame(stac_item, data_product="CSARP_standard")
    frame = frame.sortby("slow_time")
    click.echo(f"  shape: traces={len(frame.slow_time)}  samples={len(frame.twtt)}")
    click.echo(f"  twtt range: {frame.twtt.values[0]*1e6:.2f} - {frame.twtt.values[-1]*1e6:.2f} us")

    click.echo("Loading layer picks...")
    layers = opr.get_layers(frame, include_geometry=False)
    if layers is None or SURFACE_KEY not in layers or BED_KEY not in layers:
        click.echo("Missing surface or bed picks.")
        return

    surf_layer = layers[SURFACE_KEY]["twtt"]
    bed_layer = layers[BED_KEY]["twtt"]

    # Aligned to frame.slow_time for plotting + noise windowing
    tol = pd.Timedelta(seconds=5)
    surf_aligned = surf_layer.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol, fill_value=np.nan,
    )
    bed_aligned = bed_layer.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol, fill_value=np.nan,
    )

    # Peak power within layer_margin_m around each pick (matches production code)
    c = scipy.constants.c
    v_ice = c / np.sqrt(ice_permittivity)
    margin_twtt = layer_margin_m / v_ice

    surf_twtt_peak, surf_power = extract_layer_peak_power(frame, surf_layer, margin_twtt)
    bed_twtt_peak, bed_power = extract_layer_peak_power(frame, bed_layer, margin_twtt)

    surf_power_aligned = surf_power.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol, fill_value=np.nan,
    )
    bed_power_aligned = bed_power.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol, fill_value=np.nan,
    )

    # Noise power
    pre_start_offset = pre_start_offset_us * 1e-6
    pre_end_offset = pre_end_offset_us * 1e-6
    post_start_offset = post_start_offset_us * 1e-6
    post_end_offset = post_end_offset_us * 1e-6

    click.echo("Computing noise powers...")
    pre_noise_dB, post_noise_dB = extract_noise_power(
        frame, surf_aligned.values, bed_aligned.values,
        pre_start_offset, pre_end_offset,
        post_start_offset, post_end_offset,
    )

    valid_pre = np.isfinite(pre_noise_dB)
    valid_post = np.isfinite(post_noise_dB)
    click.echo(f"  pre_surface_noise_dB: {valid_pre.sum()}/{len(pre_noise_dB)} valid, "
               f"median={np.nanmedian(pre_noise_dB):.2f}, "
               f"p5={np.nanpercentile(pre_noise_dB, 5):.2f}, "
               f"p95={np.nanpercentile(pre_noise_dB, 95):.2f}")
    click.echo(f"  post_bed_noise_dB:    {valid_post.sum()}/{len(post_noise_dB)} valid, "
               f"median={np.nanmedian(post_noise_dB):.2f}, "
               f"p5={np.nanpercentile(post_noise_dB, 5):.2f}, "
               f"p95={np.nanpercentile(post_noise_dB, 95):.2f}")

    # --- Plot 1: radargram with surface/bed picks and noise window edges
    twtt_us = frame.twtt.values * 1e6
    power_dB_full = 10 * np.log10(np.maximum(np.abs(frame.Data.values), 1e-30))
    n_traces = len(frame.slow_time)
    trace_idx = np.arange(n_traces)

    surf_us = surf_aligned.values * 1e6
    bed_us = bed_aligned.values * 1e6

    fig, (ax_radar, ax_power) = plt.subplots(
        2, 1, figsize=(16, 10),
        gridspec_kw={"height_ratios": [2.5, 1.5]},
        constrained_layout=True,
    )

    vmin = np.nanpercentile(power_dB_full, 5)
    vmax = np.nanpercentile(power_dB_full, 99)
    ax_radar.pcolormesh(
        trace_idx, twtt_us, power_dB_full,
        cmap="gray", vmin=vmin, vmax=vmax,
        rasterized=True, shading="nearest",
    )
    ax_radar.plot(trace_idx, surf_us, color="cyan", linestyle=":", linewidth=1.0,
                  alpha=0.5, label="Surface pick")
    ax_radar.plot(trace_idx, bed_us, color="red", linestyle=":", linewidth=1.0,
                  alpha=0.5, label="Bed pick")

    # Noise window edges (in microseconds)
    pre_lo = twtt_us[0] + pre_start_offset_us
    pre_hi = surf_us - pre_end_offset_us
    post_lo = bed_us + post_start_offset_us
    post_hi = twtt_us[-1] - post_end_offset_us

    ax_radar.axhline(pre_lo, color="lightgreen", linestyle="--", linewidth=0.8, alpha=0.5,
                     label=f"Pre-surf window edges (offset {pre_end_offset_us:.2f} us)")
    ax_radar.plot(trace_idx, pre_hi, color="lightgreen", linestyle="--", linewidth=0.8, alpha=0.5)
    ax_radar.plot(trace_idx, post_lo, color="orange", linestyle="--", linewidth=0.8, alpha=0.5,
                  label=f"Post-bed window edges (offset {post_start_offset_us:.2f} us)")
    ax_radar.axhline(post_hi, color="orange", linestyle="--", linewidth=0.8, alpha=0.5)

    ax_radar.invert_yaxis()
    ax_radar.set_ylabel("Two-way travel time (us)")
    ax_radar.set_title(f"Radargram: {frame_id}  ({collection})")
    ax_radar.legend(fontsize=8, loc="upper right")
    ax_radar.set_xlim(0, n_traces - 1)

    # --- Plot 2: power vs trace
    ax_power.plot(trace_idx, surf_power_aligned.values, color="cyan",
                  linewidth=1.0, label="Surface peak power (dB)")
    ax_power.plot(trace_idx, bed_power_aligned.values, color="red",
                  linewidth=1.0, label="Bed peak power (dB)")
    ax_power.plot(trace_idx, pre_noise_dB, color="green",
                  linewidth=1.0, label="Pre-surface noise (dB)")
    ax_power.plot(trace_idx, post_noise_dB, color="darkorange",
                  linewidth=1.0, label="Post-bed noise (dB)")
    ax_power.set_xlabel("Trace index")
    ax_power.set_ylabel("Power (dB)")
    ax_power.legend(fontsize=8, loc="best", ncol=2)
    ax_power.set_xlim(0, n_traces - 1)
    ax_power.grid(True, alpha=0.3)
    ax_power.set_title(
        f"Per-trace powers  |  pre-surf offsets: start={pre_start_offset_us} us, "
        f"end={pre_end_offset_us} us  |  post-bed offsets: start={post_start_offset_us} us, "
        f"end={post_end_offset_us} us"
    )

    out_path = out_dir / f"{frame_id}_noise_windows.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    click.echo(f"Saved: {out_path}")

    # --- Plot 3: distributions
    fig2, ax_hist = plt.subplots(figsize=(10, 6), constrained_layout=True)
    bins = np.linspace(
        np.nanpercentile(np.concatenate([surf_power_aligned.values, bed_power_aligned.values,
                                          pre_noise_dB, post_noise_dB]), 1),
        np.nanpercentile(np.concatenate([surf_power_aligned.values, bed_power_aligned.values,
                                          pre_noise_dB, post_noise_dB]), 99),
        60,
    )
    ax_hist.hist(surf_power_aligned.values[~np.isnan(surf_power_aligned.values)],
                 bins=bins, alpha=0.5, color="cyan", label="Surface")
    ax_hist.hist(bed_power_aligned.values[~np.isnan(bed_power_aligned.values)],
                 bins=bins, alpha=0.5, color="red", label="Bed")
    ax_hist.hist(pre_noise_dB[valid_pre],
                 bins=bins, alpha=0.5, color="green", label="Pre-surf noise")
    ax_hist.hist(post_noise_dB[valid_post],
                 bins=bins, alpha=0.5, color="darkorange", label="Post-bed noise")
    ax_hist.set_xlabel("Power (dB)")
    ax_hist.set_ylabel("Trace count")
    ax_hist.legend()
    ax_hist.set_title(f"Power distributions  ({frame_id})")
    out_path2 = out_dir / f"{frame_id}_power_distributions.png"
    fig2.savefig(out_path2, dpi=150)
    plt.close(fig2)
    click.echo(f"Saved: {out_path2}")


@click.command()
@click.option("--frame-id", "frame_ids", multiple=True, default=DEFAULT_FRAME_IDS,
              show_default=True, help="One or more frame IDs (e.g. Data_20121023_04_032).")
@click.option("--pre-start-offset-us", default=0.0, show_default=True,
              help="Pre-surface window starts this far after twtt=0 (microseconds).")
@click.option("--pre-end-offset-us", default=1.0, show_default=True,
              help="Pre-surface window ends this far before surface pick (microseconds).")
@click.option("--post-start-offset-us", default=1.0, show_default=True,
              help="Post-bed window starts this far after bed pick (microseconds).")
@click.option("--post-end-offset-us", default=0.0, show_default=True,
              help="Post-bed window ends this far before end of record (microseconds).")
@click.option("--ice-permittivity", default=3.17, show_default=True)
@click.option("--layer-margin-m", default=50.0, show_default=True)
@click.option("--output-dir", default="outputs/noise_prototype", show_default=True)
def main(frame_ids, pre_start_offset_us, pre_end_offset_us,
         post_start_offset_us, post_end_offset_us,
         ice_permittivity, layer_margin_m, output_dir):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    opr = OPRConnection()

    # Group frames by (collection, segment) so we issue one OPR query per group.
    groups: dict[tuple[str, str], list[str]] = {}
    for fid in frame_ids:
        collection = derive_collection(fid)
        parts = fid.split("_")
        seg = f"{parts[1]}_{parts[2]}" if parts[0] == "Data" else "_".join(parts[:2])
        groups.setdefault((collection, seg), []).append(fid)

    for (collection, segment_path), fids in groups.items():
        click.echo(f"Querying OPR collection={collection} segment={segment_path}...")
        frames_gdf = opr.query_frames(collections=[collection], segment_paths=[segment_path])
        click.echo(f"  {len(frames_gdf) if frames_gdf is not None else 0} frames in segment")
        for fid in fids:
            process_frame_id(
                fid, collection, frames_gdf, out_dir,
                pre_start_offset_us, pre_end_offset_us,
                post_start_offset_us, post_end_offset_us,
                ice_permittivity, layer_margin_m, opr,
            )


if __name__ == "__main__":
    main()
