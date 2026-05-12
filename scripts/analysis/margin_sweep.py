"""Compare UTIG vs OPR RSSNR RMS across different layer_margin_m values.

For each frame that contains matched UTIG/OPR pairs, loads the radargram once
and computes OPR RSSNR at full resolution for several layer margins, then
samples the result at the matched-pair trace positions.
"""

import logging
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import click
import numpy as np
import pandas as pd
import scipy.constants
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

logger = logging.getLogger(__name__)


def _silence():
    """Quiet noisy xopr/zarr warnings inside worker processes."""
    warnings.filterwarnings("ignore", category=UserWarning)
    logging.basicConfig(level=logging.ERROR)


def process_frame(stac_item, frame_pairs_xy, margins, config):
    """Worker: load frame, return rssnr_opr[m,n_pairs] for each margin.

    Returns (frame_id, dict[margin -> np.ndarray of OPR RSSNR per pair], frame_distances).
    """
    _silence()
    frame_id = stac_item.name if hasattr(stac_item, "name") else "unknown"
    proc = config["processing"]
    ice_permittivity = proc["ice_permittivity"]
    v_ice = scipy.constants.c / np.sqrt(ice_permittivity)

    try:
        opr = OPRConnection(cache_dir=config["opr"].get("cache_dir"))
        frame = opr.load_frame(stac_item, data_product=proc["data_product"]).sortby("slow_time")
        layers = opr.get_layers(frame, include_geometry=False)
        if layers is None or SURFACE_KEY not in layers or BED_KEY not in layers:
            return frame_id, None, None

        surf_pick = layers[SURFACE_KEY]["twtt"]
        bed_pick = layers[BED_KEY]["twtt"]

        # Build KDTree of frame trace positions (EPSG:3031).
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3031", always_xy=True)
        frame_x, frame_y = transformer.transform(
            frame.Longitude.values, frame.Latitude.values
        )
        tree = cKDTree(np.column_stack([frame_x, frame_y]))
        dists, idx = tree.query(frame_pairs_xy)

        tol5 = pd.Timedelta(seconds=5)
        results = {}
        for margin_m in margins:
            margin_twtt = margin_m / v_ice
            surf_twtt_peak, surf_power = extract_layer_peak_power(
                frame, surf_pick, margin_twtt
            )
            bed_twtt_peak, bed_power = extract_layer_peak_power(
                frame, bed_pick, margin_twtt
            )

            surf_twtt_re = surf_twtt_peak.reindex(
                slow_time=frame.slow_time, method="nearest",
                tolerance=tol5, fill_value=np.nan,
            )
            surf_power_re = surf_power.reindex(
                slow_time=frame.slow_time, method="nearest",
                tolerance=tol5, fill_value=np.nan,
            )
            bed_twtt_re = bed_twtt_peak.reindex(
                slow_time=frame.slow_time, method="nearest",
                tolerance=tol5, fill_value=np.nan,
            )
            bed_power_re = bed_power.reindex(
                slow_time=frame.slow_time, method="nearest",
                tolerance=tol5, fill_value=np.nan,
            )

            rssnr_full = compute_rssnr_dB(
                surf_power_re.values, bed_power_re.values,
                surf_twtt_re.values, bed_twtt_re.values,
                ice_permittivity,
            )
            results[margin_m] = rssnr_full[idx]

        return frame_id, results, dists
    except Exception as exc:
        return frame_id, None, str(exc)


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option(
    "--pairs-csv",
    default="outputs/utig_comparison/matched_pairs.csv",
    type=click.Path(exists=True),
    show_default=True,
)
@click.option(
    "--margins",
    default="10,50,100,250",
    help="Comma-separated layer margins in metres of ice.",
    show_default=True,
)
@click.option(
    "--max-workers", default=8, show_default=True, type=int,
    help="Parallel worker processes.",
)
@click.option(
    "--output-csv",
    default="outputs/utig_comparison/margin_sweep.csv",
    show_default=True,
)
def main(config_path, pairs_csv, margins, max_workers, output_csv):
    margins = [float(m) for m in margins.split(",")]
    pairs = pd.read_csv(pairs_csv)

    config = load_config(config_path)

    click.echo("Querying OPR for STAC items...")
    opr = OPRConnection(cache_dir=config["opr"].get("cache_dir"))
    frames_gdf = opr.query_frames(
        collections=config["query"]["collections"],
    )

    unique_frames = pairs["opr_frame"].unique().tolist()
    missing = [f for f in unique_frames if f not in frames_gdf.index]
    if missing:
        click.echo(f"WARNING: {len(missing)} frames missing from OPR query (will be skipped)")
        unique_frames = [f for f in unique_frames if f in frames_gdf.index]

    click.echo(f"Processing {len(unique_frames)} frames at margins {margins} m...")

    # Build per-frame pair tables once
    grouped = {fid: g.reset_index(drop=True) for fid, g in pairs.groupby("opr_frame")}

    # rssnr_opr_by_margin[margin] -> list of (pair_global_idx, value) collected later
    rssnr_collected = {m: np.full(len(pairs), np.nan) for m in margins}
    pair_global_idx = {fid: np.where(pairs["opr_frame"] == fid)[0] for fid in unique_frames}

    n_done = 0
    n_fail = 0
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for fid in unique_frames:
            stac_item = frames_gdf.loc[fid]
            frame_pairs = grouped[fid]
            xy = frame_pairs[["x", "y"]].values
            fut = ex.submit(process_frame, stac_item, xy, margins, config)
            futures[fut] = fid

        for fut in as_completed(futures):
            fid = futures[fut]
            try:
                _, results, _ = fut.result()
            except Exception as exc:
                click.echo(f"  {fid}: EXCEPTION: {exc}")
                n_fail += 1
                continue
            if results is None:
                n_fail += 1
            else:
                gidx = pair_global_idx[fid]
                for m in margins:
                    rssnr_collected[m][gidx] = results[m]
            n_done += 1
            if n_done % 20 == 0 or n_done == len(unique_frames):
                click.echo(f"  {n_done}/{len(unique_frames)} frames (fail={n_fail})")

    # Build output frame
    out = pairs[["x", "y", "opr_frame", "rssnr_utig"]].copy()
    for m in margins:
        out[f"rssnr_opr_m{int(m)}"] = rssnr_collected[m]
        out[f"diff_m{int(m)}"] = out["rssnr_utig"] - rssnr_collected[m]

    out.to_csv(output_csv, index=False)
    click.echo(f"\nSaved: {output_csv}")

    # Summary
    click.echo("\nRSSNR difference (UTIG − OPR) by layer margin:")
    click.echo(f"  {'margin':>8} {'N':>8} {'mean':>10} {'median':>10} {'RMS':>10} {'p5':>8} {'p95':>8}")
    for m in margins:
        d = out[f"diff_m{int(m)}"].dropna().values
        if len(d) == 0:
            click.echo(f"  {int(m):>5} m   no valid data")
            continue
        click.echo(
            f"  {int(m):>5} m  {len(d):>8d} "
            f"{np.mean(d):>9.3f}  {np.median(d):>9.3f}  "
            f"{np.sqrt(np.mean(d**2)):>9.3f}  "
            f"{np.percentile(d, 5):>7.2f}  {np.percentile(d, 95):>7.2f}"
        )


if __name__ == "__main__":
    main()
