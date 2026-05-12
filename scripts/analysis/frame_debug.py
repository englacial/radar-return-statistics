"""Debug UTIG vs OPR RSSNR comparison for a single radar frame.

Plots the full-resolution radargram with surface/bed picks overlaid,
and a panel below comparing UTIG RSSNR to OPR RSSNR at matched positions.
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
from pyproj import Transformer
from scipy.spatial import cKDTree
from xopr import OPRConnection

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from radar_return_statistics.config import load_config
from radar_return_statistics.processing import (
    SURFACE_KEY,
    BED_KEY,
    extract_layer_peak_power,
    compute_rssnr_dB,
)


def _frame_bbox(frame_pairs: pd.DataFrame, buffer_m: float = 50_000.0):
    """Return a GeoJSON polygon bounding box around the frame pairs, in WGS84."""
    t = Transformer.from_crs("EPSG:3031", "EPSG:4326", always_xy=True)
    cx = frame_pairs["x"].mean()
    cy = frame_pairs["y"].mean()
    half = buffer_m
    corners_x = [cx - half, cx + half, cx + half, cx - half, cx - half]
    corners_y = [cy - half, cy - half, cy + half, cy + half, cy - half]
    lons, lats = t.transform(corners_x, corners_y)
    return {
        "type": "Polygon",
        "coordinates": [list(zip(lons, lats))],
    }


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option(
    "--pairs-csv",
    default="outputs/utig_comparison/matched_pairs.csv",
    type=click.Path(exists=True),
    show_default=True,
)
@click.option(
    "--frame-id",
    default=None,
    help="Frame ID to plot. Defaults to the frame with the most matched pairs.",
)
@click.option(
    "--output",
    "output_path",
    default="outputs/utig_comparison/frame_debug.png",
    show_default=True,
)
@click.option(
    "--layer-margin-m",
    default=None,
    type=float,
    help="Override processing.layer_margin_m from config (metres of ice).",
)
@click.option("--verbose", "-v", is_flag=True)
def main(config_path, pairs_csv, frame_id, output_path, layer_margin_m, verbose):
    pairs = pd.read_csv(pairs_csv)

    if frame_id is None:
        frame_id = pairs["opr_frame"].value_counts().index[0]

    frame_pairs = pairs[pairs["opr_frame"] == frame_id].copy().reset_index(drop=True)
    click.echo(f"Frame: {frame_id}  ({len(frame_pairs)} matched pairs)")

    config = load_config(config_path)
    opr = OPRConnection(cache_dir=config["opr"].get("cache_dir"))

    # Small bounding-box query so we only fetch frames near this location.
    bbox = _frame_bbox(frame_pairs)
    click.echo("Querying OPR...")
    frames_gdf = opr.query_frames(
        geometry=bbox,
        collections=config["query"]["collections"],
    )

    if frame_id not in frames_gdf.index:
        click.echo(f"Frame '{frame_id}' not found in OPR query result.")
        return

    stac_item = frames_gdf.loc[frame_id]

    proc = config["processing"]
    if layer_margin_m is not None:
        proc = dict(proc, layer_margin_m=layer_margin_m)
        click.echo(f"Overriding layer_margin_m: {layer_margin_m} m")

    # Load full-resolution radargram (no decimation here).
    click.echo("Loading radargram...")
    frame = opr.load_frame(stac_item, data_product=proc["data_product"])
    frame = frame.sortby("slow_time")
    n_traces = len(frame.slow_time)

    # Layer picks.
    click.echo("Loading layer picks...")
    layers = opr.get_layers(frame, include_geometry=False)
    if layers is None or SURFACE_KEY not in layers or BED_KEY not in layers:
        click.echo("Missing surface or bed picks.")
        return

    surface_twtt_da = layers[SURFACE_KEY]["twtt"]
    bed_twtt_da = layers[BED_KEY]["twtt"]

    # Align picks to the radargram slow_time grid.
    tol = pd.Timedelta(seconds=5)
    surf_aligned = surface_twtt_da.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol, fill_value=np.nan
    )
    bed_aligned = bed_twtt_da.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol, fill_value=np.nan
    )

    # Full-resolution OPR RSSNR.
    click.echo("Computing OPR RSSNR at full resolution...")
    ice_permittivity = proc["ice_permittivity"]
    v_ice = scipy.constants.c / np.sqrt(ice_permittivity)
    margin_twtt = proc["layer_margin_m"] / v_ice

    surf_twtt_peak, surf_power = extract_layer_peak_power(frame, surface_twtt_da, margin_twtt)
    bed_twtt_peak, bed_power = extract_layer_peak_power(frame, bed_twtt_da, margin_twtt)

    # Align extracted arrays back to the frame's slow_time grid (surface and bed
    # picks may have slightly different time extents, causing shape mismatches).
    tol5 = pd.Timedelta(seconds=5)
    surf_twtt_re = surf_twtt_peak.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol5, fill_value=np.nan
    )
    surf_power_re = surf_power.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol5, fill_value=np.nan
    )
    bed_twtt_re = bed_twtt_peak.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol5, fill_value=np.nan
    )
    bed_power_re = bed_power.reindex(
        slow_time=frame.slow_time, method="nearest", tolerance=tol5, fill_value=np.nan
    )

    rssnr_full = compute_rssnr_dB(
        surf_power_re.values,
        bed_power_re.values,
        surf_twtt_re.values,
        bed_twtt_re.values,
        ice_permittivity,
    )

    # Map matched pair positions to frame trace indices.
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3031", always_xy=True)
    frame_x, frame_y = transformer.transform(
        frame.Longitude.values, frame.Latitude.values
    )
    frame_tree = cKDTree(np.column_stack([frame_x, frame_y]))
    dists, frame_idx = frame_tree.query(frame_pairs[["x", "y"]].values)
    frame_pairs["frame_idx"] = frame_idx
    if verbose:
        click.echo(f"Match-to-frame distances: min={dists.min():.0f} m  max={dists.max():.0f} m")

    # --- Figure ---
    twtt_us = frame.twtt.values * 1e6
    power_dB = 10 * np.log10(np.maximum(np.abs(frame.Data.values), 1e-30))
    surf_us = surf_aligned.values * 1e6
    bed_us = bed_aligned.values * 1e6

    # Clip twtt display range.
    surf_min_us = np.nanmin(surf_us)
    bed_max_us = np.nanmax(bed_us)
    pad_us = 3.0
    twtt_mask = (twtt_us >= surf_min_us - pad_us) & (twtt_us <= bed_max_us + pad_us)

    trace_idx = np.arange(n_traces)

    fig, (ax_radar, ax_rssnr) = plt.subplots(
        2, 1, figsize=(16, 9),
        gridspec_kw={"height_ratios": [3, 1.5]},
        constrained_layout=True,
    )

    # Radargram.
    clipped = power_dB[:, twtt_mask]
    vmin = np.nanpercentile(clipped, 5)
    vmax = np.nanpercentile(clipped, 99)
    ax_radar.pcolormesh(
        trace_idx,
        twtt_us[twtt_mask],
        clipped.T,
        cmap="gray",
        vmin=vmin,
        vmax=vmax,
        rasterized=True,
        shading="nearest",
    )
    ax_radar.plot(trace_idx, surf_us, color="cyan", linewidth=1.0, label="Surface pick")
    ax_radar.plot(trace_idx, bed_us, color="red", linewidth=1.0, label="Bed pick")

    # Vertical markers at matched pair positions.
    for idx in frame_pairs["frame_idx"].values:
        ax_radar.axvline(x=idx, color="yellow", alpha=0.25, linewidth=0.6)

    ax_radar.invert_yaxis()
    ax_radar.set_ylabel("Two-way travel time (µs)")
    ax_radar.set_title(f"Radargram: {frame_id}")
    ax_radar.legend(fontsize=8, loc="upper right")
    ax_radar.set_xlim(0, n_traces - 1)

    # RSSNR panel.
    ax_rssnr.plot(
        trace_idx, rssnr_full,
        color="steelblue", linewidth=0.8, label="OPR RSSNR (full-res)", zorder=2,
    )
    ax_rssnr.scatter(
        frame_pairs["frame_idx"], frame_pairs["rssnr_utig"],
        c="tomato", s=25, zorder=4, label="UTIG RSSNR (matched)",
    )
    ax_rssnr.scatter(
        frame_pairs["frame_idx"], frame_pairs["rssnr_opr"],
        c="steelblue", s=25, marker="x", linewidths=1.0, zorder=5,
        label="OPR RSSNR (stored, decimated)",
    )
    ax_rssnr.set_xlabel("Trace index")
    ax_rssnr.set_ylabel("RSSNR (dB)")
    ax_rssnr.set_title("RSSNR comparison at matched positions")
    ax_rssnr.legend(fontsize=8)
    ax_rssnr.set_xlim(0, n_traces - 1)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    click.echo(f"Saved: {out}")


if __name__ == "__main__":
    main()
