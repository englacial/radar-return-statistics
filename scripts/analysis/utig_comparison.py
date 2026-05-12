"""Comparison between UTIG-processed RSSNR and OPR re-processing of BaslerJKB data.

UTIG CSV columns: snr (dB, treated as RSSNR), x, y (EPSG:3031 metres).
OPR store: config_utig.yaml (icechunk/basler_jkb).

The datasets are expected to have partial overlap: most UTIG traces will have
no nearby OPR record, and some OPR records may lack a nearby UTIG trace.
For traces that do overlap, RSSNR values should be similar but not identical.
"""

import sys
from pathlib import Path

import click
import numpy as np
import pandas as pd
import zarr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from radar_return_statistics.config import load_config
from radar_return_statistics.store import open_or_create_repo


def load_utig(csv_path: Path) -> pd.DataFrame:
    """Load UTIG CSV. x/y are EPSG:3031 metres, snr is RSSNR in dB.

    The sign convention in the UTIG file is inverted relative to our definition,
    so snr is negated on load.
    """
    df = pd.read_csv(csv_path, index_col=0)
    df = df.dropna(subset=["snr", "x", "y"])
    df = df[np.isfinite(df["snr"]) & np.isfinite(df["x"]) & np.isfinite(df["y"])]
    df["snr"] = -df["snr"]
    return df.reset_index(drop=True)


def load_opr(config: dict) -> pd.DataFrame:
    """Load OPR store, returning traces in EPSG:3031 with RSSNR."""
    repo = open_or_create_repo(config["store"])
    session = repo.readonly_session(branch="main")
    root = zarr.open_group(session.store, mode="r")

    n = root["latitude"].shape[0]
    mask = np.ones(n, dtype=bool)
    if "qc_pass" in root:
        mask = root["qc_pass"][:] == 1

    lat = root["latitude"][:][mask]
    lon = root["longitude"][:][mask]
    rssnr = root["required_surface_snr_dB"][:][mask]

    frame_id = None
    if "frame_index" in root and "frame_names" in root.attrs:
        frame_names = list(root.attrs["frame_names"])
        frame_idx = root["frame_index"][:][mask]
        frame_id = [frame_names[i] for i in frame_idx]

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3031", always_xy=True)
    x, y = transformer.transform(lon, lat)

    df = pd.DataFrame({"x": x, "y": y, "lat": lat, "lon": lon, "rssnr_opr": rssnr})
    if frame_id is not None:
        df["frame_id"] = frame_id
    return df.reset_index(drop=True)


def match_datasets(
    utig_df: pd.DataFrame, opr_df: pd.DataFrame, threshold: float
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """One-to-one spatial matching between OPR and UTIG traces.

    Queries each OPR point against the UTIG tree (OPR is the smaller dataset),
    then resolves any UTIG points claimed by multiple OPR points by keeping
    only the closest pair. Each point in either dataset appears in at most one
    matched pair.

    Returns:
        pairs: DataFrame of matched pairs
        utig_matched: boolean mask over utig_df rows
        opr_matched: boolean mask over opr_df rows
    """
    utig_tree = cKDTree(utig_df[["x", "y"]].values)
    dists, utig_idx = utig_tree.query(opr_df[["x", "y"]].values, workers=-1)

    within = dists < threshold
    candidates = pd.DataFrame({
        "opr_i": np.where(within)[0],
        "utig_i": utig_idx[within],
        "dist": dists[within],
    })

    # One-to-one: if multiple OPR points claim the same UTIG point, keep closest
    candidates = (candidates
                  .loc[candidates.groupby("utig_i")["dist"].idxmin()]
                  .reset_index(drop=True))

    u = utig_df.iloc[candidates["utig_i"].values].reset_index(drop=True)
    o = opr_df.iloc[candidates["opr_i"].values].reset_index(drop=True)

    pairs = pd.DataFrame({
        "x": u["x"].values,
        "y": u["y"].values,
        "distance_m": candidates["dist"].values,
        "rssnr_utig": u["snr"].values,
        "rssnr_opr": o["rssnr_opr"].values,
    })
    if "frame_id" in o.columns:
        pairs["opr_frame"] = o["frame_id"].values
    pairs["rssnr_diff"] = pairs["rssnr_utig"] - pairs["rssnr_opr"]

    utig_matched = np.zeros(len(utig_df), dtype=bool)
    utig_matched[candidates["utig_i"].values] = True
    opr_matched = np.zeros(len(opr_df), dtype=bool)
    opr_matched[candidates["opr_i"].values] = True

    return pairs, utig_matched, opr_matched


def make_scatter(pairs: pd.DataFrame, output_dir: Path) -> None:
    lo = min(pairs["rssnr_utig"].min(), pairs["rssnr_opr"].min())
    hi = max(pairs["rssnr_utig"].max(), pairs["rssnr_opr"].max())
    diff = pairs["rssnr_diff"].values

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)

    # Scatter coloured by distance
    ax = axes[0]
    norm = plt.Normalize(pairs["distance_m"].min(), pairs["distance_m"].max())
    sc = ax.scatter(
        pairs["rssnr_utig"], pairs["rssnr_opr"],
        c=pairs["distance_m"], cmap="plasma_r", norm=norm,
        s=4, alpha=0.4, rasterized=True,
    )
    ax.plot([lo, hi], [lo, hi], color="grey", linewidth=0.8, zorder=0)
    plt.colorbar(sc, ax=ax, label="Match distance (m)")
    ax.set_xlabel("UTIG RSSNR (dB)")
    ax.set_ylabel("OPR RSSNR (dB)")
    ax.annotate(
        f"N={len(pairs):,}\nMean diff={np.mean(diff):.2f} dB\nRMS={np.sqrt(np.mean(diff**2)):.2f} dB",
        xy=(0.04, 0.96), xycoords="axes fraction", va="top", fontsize=9,
    )
    ax.set_title("UTIG vs OPR RSSNR")

    # Difference vs UTIG RSSNR
    ax = axes[1]
    ax.scatter(
        pairs["rssnr_utig"], pairs["rssnr_diff"],
        c=pairs["distance_m"], cmap="plasma_r", norm=norm,
        s=4, alpha=0.4, rasterized=True,
    )
    ax.axhline(0, color="grey", linewidth=0.8)
    ax.set_xlabel("UTIG RSSNR (dB)")
    ax.set_ylabel("RSSNR diff: UTIG − OPR (dB)")
    ax.set_title("Difference vs UTIG RSSNR")

    fig.savefig(output_dir / "scatter.png", dpi=150)
    plt.close(fig)


def make_differences(pairs: pd.DataFrame, output_dir: Path) -> None:
    diff = pairs["rssnr_diff"].dropna().values
    fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
    counts, edges = np.histogram(diff, bins=100)
    centers = (edges[:-1] + edges[1:]) / 2
    ax.bar(centers, counts, width=np.diff(edges), color="steelblue", alpha=0.8,
           label=f"N={len(diff):,}  Mean={np.mean(diff):.2f}  RMS={np.sqrt(np.mean(diff**2)):.2f} dB")
    ax.axvline(0, color="grey", linewidth=0.8)
    ax.set_xlabel("RSSNR diff: UTIG − OPR (dB)")
    ax.set_ylabel("Count")
    ax.set_title("RSSNR difference distribution")
    ax.legend(fontsize=9)
    fig.savefig(output_dir / "differences.png", dpi=150)
    plt.close(fig)


def _xy_to_lonlat(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = Transformer.from_crs("EPSG:3031", "EPSG:4326", always_xy=True)
    lon, lat = t.transform(x, y)
    return lon, lat


def make_coverage_map(
    utig_df: pd.DataFrame,
    opr_df: pd.DataFrame,
    pairs: pd.DataFrame,
    output_dir: Path,
    subsample: int = 50000,
) -> None:
    proj = ccrs.SouthPolarStereo()
    pc = ccrs.PlateCarree()

    # Subsample UTIG for plotting (it's huge)
    rng = np.random.default_rng(0)
    utig_idx = rng.choice(len(utig_df), min(subsample, len(utig_df)), replace=False)
    utig_lon, utig_lat = _xy_to_lonlat(
        utig_df["x"].values[utig_idx], utig_df["y"].values[utig_idx]
    )
    opr_lon, opr_lat = _xy_to_lonlat(opr_df["x"].values, opr_df["y"].values)
    pairs_lon, pairs_lat = _xy_to_lonlat(pairs["x"].values, pairs["y"].values)

    all_lats = np.concatenate([utig_lat, opr_lat])
    all_lons = np.concatenate([utig_lon, opr_lon])
    lat_pad = max((all_lats.max() - all_lats.min()) * 0.05, 1.0)
    lon_pad = max((all_lons.max() - all_lons.min()) * 0.05, 1.0)
    extent = [
        all_lons.min() - lon_pad, all_lons.max() + lon_pad,
        all_lats.min() - lat_pad, all_lats.max() + lat_pad,
    ]

    fig, axes = plt.subplots(1, 2, figsize=(16, 7),
                             subplot_kw={"projection": proj}, constrained_layout=True)

    for ax in axes:
        ax.set_extent(extent, crs=pc)
        ax.add_feature(cfeature.OCEAN, color="#cce5ff")
        ax.add_feature(cfeature.LAND, color="#e8e4dc")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.5)

    # Left: coverage overview
    axes[0].scatter(utig_lon, utig_lat, s=1, color="#aaaaaa", alpha=0.3,
                    transform=pc, label=f"UTIG ({len(utig_df):,} traces, subsampled)", rasterized=True)
    axes[0].scatter(opr_lon, opr_lat, s=4, color="steelblue", alpha=0.6,
                    transform=pc, label=f"OPR ({len(opr_df):,} traces)", rasterized=True)
    axes[0].set_title("Coverage: UTIG (grey) vs OPR (blue)")
    axes[0].legend(fontsize=8, loc="lower left")

    # Right: matched pairs coloured by RSSNR difference
    vmax = np.nanpercentile(np.abs(pairs["rssnr_diff"].values), 95)
    sc = axes[1].scatter(
        pairs_lon, pairs_lat, c=pairs["rssnr_diff"].values,
        cmap="RdBu_r", vmin=-vmax, vmax=vmax, s=4, alpha=0.6,
        transform=pc, rasterized=True,
    )
    plt.colorbar(sc, ax=axes[1], label="RSSNR diff: UTIG − OPR (dB)", shrink=0.8)
    axes[1].set_title(f"Matched pairs (N={len(pairs):,}, threshold shown)")

    fig.savefig(output_dir / "coverage_map.png", dpi=150)
    plt.close(fig)


# DBSCAN/GMM were tried first but found no density gap; the distribution is
# bimodal-ish but smooth. The +8 dB hard threshold is an ad-hoc cut that isolates
# the high-offset tail (~25% of pairs) for visualization.
DIFF_GROUP_COLORS = ("steelblue", "tomato")
DIFF_GROUP_LABELS = ("rssnr_diff ≤ thr", "rssnr_diff > thr")


def split_by_diff_threshold(pairs: pd.DataFrame, threshold: float) -> np.ndarray:
    return (pairs["rssnr_diff"].values > threshold).astype(int)


def make_diff_threshold_scatter(
    pairs: pd.DataFrame, labels: np.ndarray, threshold: float, output_dir: Path
) -> None:
    lo = min(pairs["rssnr_utig"].min(), pairs["rssnr_opr"].min())
    hi = max(pairs["rssnr_utig"].max(), pairs["rssnr_opr"].max())

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)

    ax = axes[0]
    for k in (0, 1):
        mask = labels == k
        diff = pairs["rssnr_diff"][mask].values
        ax.scatter(
            pairs["rssnr_utig"][mask], pairs["rssnr_opr"][mask],
            c=DIFF_GROUP_COLORS[k], s=6, alpha=0.5, rasterized=True,
            label=f"{DIFF_GROUP_LABELS[k]} (N={mask.sum():,}, mean={np.mean(diff):.1f} dB)",
        )
    ax.plot([lo, hi], [lo, hi], color="grey", linewidth=0.8, zorder=0)
    ax.set_xlabel("UTIG RSSNR (dB)")
    ax.set_ylabel("OPR RSSNR (dB)")
    ax.set_title(f"UTIG vs OPR RSSNR (split at diff = {threshold:.0f} dB)")
    ax.legend(fontsize=9)

    ax = axes[1]
    for k in (0, 1):
        mask = labels == k
        diff = pairs["rssnr_diff"][mask].values
        counts, edges = np.histogram(diff, bins=60)
        centers = (edges[:-1] + edges[1:]) / 2
        ax.bar(
            centers, counts, width=np.diff(edges),
            color=DIFF_GROUP_COLORS[k], alpha=0.6,
            label=f"{DIFF_GROUP_LABELS[k]}: mean={np.mean(diff):.1f}  RMS={np.sqrt(np.mean(diff**2)):.1f} dB",
        )
    ax.axvline(threshold, color="grey", linewidth=0.8, linestyle="--")
    ax.axvline(0, color="grey", linewidth=0.5)
    ax.set_xlabel("RSSNR diff: UTIG − OPR (dB)")
    ax.set_ylabel("Count")
    ax.set_title("Difference distribution by group")
    ax.legend(fontsize=8)

    fig.savefig(output_dir / "diff_threshold_scatter.png", dpi=150)
    plt.close(fig)


def make_diff_threshold_map(
    pairs: pd.DataFrame, labels: np.ndarray, threshold: float, output_dir: Path
) -> None:
    proj = ccrs.SouthPolarStereo()
    pc = ccrs.PlateCarree()

    lon, lat = _xy_to_lonlat(pairs["x"].values, pairs["y"].values)
    lat_pad = max((lat.max() - lat.min()) * 0.05, 1.0)
    lon_pad = max((lon.max() - lon.min()) * 0.05, 1.0)
    extent = [lon.min() - lon_pad, lon.max() + lon_pad,
              lat.min() - lat_pad, lat.max() + lat_pad]

    fig, ax = plt.subplots(figsize=(10, 8), subplot_kw={"projection": proj},
                           constrained_layout=True)
    ax.set_extent(extent, crs=pc)
    ax.add_feature(cfeature.OCEAN, color="#cce5ff")
    ax.add_feature(cfeature.LAND, color="#e8e4dc")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.5)

    for k in (0, 1):
        mask = labels == k
        ax.scatter(
            lon[mask], lat[mask], c=DIFF_GROUP_COLORS[k], s=8, alpha=0.7,
            transform=pc, rasterized=True,
            label=f"{DIFF_GROUP_LABELS[k]} (N={mask.sum():,})",
        )

    ax.legend(fontsize=9, loc="lower left")
    ax.set_title(f"Matched pairs split at rssnr_diff = {threshold:.0f} dB")

    fig.savefig(output_dir / "diff_threshold_map.png", dpi=150)
    plt.close(fig)


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option(
    "--utig-csv", default="reference/utig-processed-snr/snr.csv",
    type=click.Path(exists=True), show_default=True,
    help="Path to UTIG SNR CSV file",
)
@click.option(
    "--threshold", default=50.0, show_default=True,
    help="Spatial match threshold in metres",
)
@click.option(
    "--output", "output_dir", default="outputs/utig_comparison",
    type=click.Path(), show_default=True,
)
@click.option(
    "--diff-threshold", default=8.0, show_default=True,
    help="rssnr_diff threshold (dB) for splitting high-offset pairs from the rest. "
         "Ad-hoc; DBSCAN/GMM found no genuine cluster boundary.",
)
@click.option("--verbose", "-v", is_flag=True)
def main(config_path, utig_csv, threshold, output_dir, diff_threshold, verbose):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Loading UTIG CSV: {utig_csv}")
    utig_df = load_utig(Path(utig_csv))
    click.echo(f"  {len(utig_df):,} UTIG traces")

    click.echo(f"Loading OPR store: {config_path}")
    config = load_config(config_path)
    opr_df = load_opr(config)
    click.echo(f"  {len(opr_df):,} OPR QC-passing traces")

    if opr_df.empty:
        click.echo("OPR store is empty — run the pipeline first.")
        return

    click.echo(f"Matching datasets (threshold={threshold} m)...")
    pairs, utig_matched, opr_matched = match_datasets(utig_df, opr_df, threshold)
    click.echo(f"  {len(pairs):,} matched pairs")
    click.echo(f"  UTIG: {utig_matched.sum():,} / {len(utig_df):,} matched "
               f"({100 * utig_matched.mean():.1f}%)")
    click.echo(f"  OPR:  {opr_matched.sum():,} / {len(opr_df):,} matched "
               f"({100 * opr_matched.mean():.1f}%)")

    if pairs.empty:
        click.echo("No matched pairs — try increasing --threshold.")
        return

    diff = pairs["rssnr_diff"].dropna().values
    click.echo()
    click.echo("RSSNR difference (UTIG − OPR):")
    click.echo(f"  Mean:   {np.mean(diff):.3f} dB")
    click.echo(f"  Std:    {np.std(diff):.3f} dB")
    click.echo(f"  RMS:    {np.sqrt(np.mean(diff**2)):.3f} dB")
    click.echo(f"  Median: {np.median(diff):.3f} dB")
    click.echo(f"  p5/p95: {np.percentile(diff, 5):.2f} / {np.percentile(diff, 95):.2f} dB")

    if verbose and "opr_frame" in pairs.columns:
        click.echo()
        click.echo("Matched pair count by OPR frame (top 20):")
        click.echo(pairs["opr_frame"].value_counts().head(20).to_string())

    pairs.to_csv(output_dir / "matched_pairs.csv", index=False)
    click.echo(f"\nSaved: {output_dir / 'matched_pairs.csv'}")

    click.echo(f"Splitting at rssnr_diff = {diff_threshold} dB...")
    labels = split_by_diff_threshold(pairs, threshold=diff_threshold)
    for k in (0, 1):
        mask = labels == k
        d = pairs["rssnr_diff"][mask].values
        click.echo(f"  {DIFF_GROUP_LABELS[k]}: N={mask.sum():,}  "
                   f"mean={np.mean(d):.2f} dB  RMS={np.sqrt(np.mean(d**2)):.2f} dB")

    click.echo("Generating plots...")
    make_scatter(pairs, output_dir)
    make_differences(pairs, output_dir)
    make_coverage_map(utig_df, opr_df, pairs, output_dir)
    make_diff_threshold_scatter(pairs, labels, diff_threshold, output_dir)
    make_diff_threshold_map(pairs, labels, diff_threshold, output_dir)
    click.echo("Saved: scatter.png  differences.png  coverage_map.png  "
               "diff_threshold_scatter.png  diff_threshold_map.png")


if __name__ == "__main__":
    main()
