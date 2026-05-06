"""Find crossover points between radar flight lines and compare measured values."""

import sys
from pathlib import Path

import click
import numpy as np
import pandas as pd
import zarr
import icechunk
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import unary_union
from scipy.spatial import cKDTree
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import cartopy.crs as ccrs
import cartopy.feature as cfeature

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from radar_return_statistics.config import load_config
from radar_return_statistics.store import open_or_create_repo

C = 299792458.0
V_ICE = C / np.sqrt(3.17)

VARIABLES = {
    "surface_elevation":       {"label": "Surface Elevation",    "unit": "m"},
    "bed_elevation":           {"label": "Bed Elevation",         "unit": "m"},
    "ice_thickness":           {"label": "Ice Thickness",         "unit": "m"},
    "surface_power_dB":        {"label": "Surface Power",         "unit": "dB"},
    "bed_power_dB":            {"label": "Bed Power",             "unit": "dB"},
    "required_surface_snr_dB": {"label": "Required Surface SNR", "unit": "dB"},
}


def _bearing(x, y, idx):
    """Compute bearing (degrees) at trace idx along a path."""
    n = len(x)
    if n == 1:
        return 0.0
    i0 = max(0, idx - 1)
    i1 = min(n - 1, idx + 1)
    if i0 == i1:
        i0, i1 = (0, 1) if idx == 0 else (n - 2, n - 1)
    dx = x[i1] - x[i0]
    dy = y[i1] - y[i0]
    return np.degrees(np.arctan2(dx, dy))


def _acute_angle(b1, b2):
    """Acute angle (0-90°) between two bearings."""
    diff = abs(b1 - b2) % 180.0
    return min(diff, 180.0 - diff)


def _components(geom):
    """Iterate individual polygon components of a geometry."""
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            if g.geom_type == "Polygon":
                yield g
            elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
                yield from _components(g)


def load_data(config):
    repo = open_or_create_repo(config["store"])
    session = repo.readonly_session(branch="main")
    root = zarr.open_group(session.store, mode="r")

    lat = root["latitude"][:]
    lon = root["longitude"][:]
    qc = root["qc_pass"][:]
    surface_twtt = root["surface_twtt"][:]
    bed_twtt = root["bed_twtt"][:]
    surface_elevation = root["surface_elevation"][:]
    bed_elevation = root["bed_elevation"][:]
    surface_power_dB = root["surface_power_dB"][:]
    bed_power_dB = root["bed_power_dB"][:]
    required_surface_snr_dB = root["required_surface_snr_dB"][:]
    frame_index = root["frame_index"][:].astype(np.uint16)
    frame_names = list(root.attrs["frame_names"])

    mask = qc == 1
    idx = np.where(mask)[0]

    ice_thickness = (bed_twtt[mask] - surface_twtt[mask]) * V_ICE / 2

    data = {
        "lat": lat[mask],
        "lon": lon[mask],
        "surface_elevation": surface_elevation[mask],
        "bed_elevation": bed_elevation[mask],
        "ice_thickness": ice_thickness,
        "surface_power_dB": surface_power_dB[mask],
        "bed_power_dB": bed_power_dB[mask],
        "required_surface_snr_dB": required_surface_snr_dB[mask],
        "frame_index": frame_index[mask],
        "orig_idx": idx,
    }
    return data, frame_names


def find_crossovers(data, frame_names, threshold, verbose):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3031", always_xy=True)
    x_all, y_all = transformer.transform(data["lon"], data["lat"])
    fi = data["frame_index"]
    n_frames = len(frame_names)

    # Build per-frame arrays of local indices into the qc-masked arrays
    frame_local = {}
    for f in range(n_frames):
        local = np.where(fi == f)[0]
        if len(local) >= 2:
            frame_local[f] = local

    frames_with_data = sorted(frame_local.keys())
    n_valid = len(frames_with_data)

    # Build LineStrings for each frame
    lines = {}
    for f in frames_with_data:
        locs = frame_local[f]
        coords = list(zip(x_all[locs], y_all[locs]))
        lines[f] = LineString(coords)

    pairs_checked = 0
    crossovers = []
    half = threshold / 2.0

    for ii, fi_idx in enumerate(frames_with_data):
        buf_i = lines[fi_idx].buffer(half)
        for jj in range(ii + 1, n_valid):
            fj_idx = frames_with_data[jj]
            pairs_checked += 1

            buf_j = lines[fj_idx].buffer(half)
            intersection = buf_i.intersection(buf_j)
            if intersection.is_empty:
                continue

            merged = unary_union(intersection)
            locs_i = frame_local[fi_idx]
            locs_j = frame_local[fj_idx]
            xi, yi = x_all[locs_i], y_all[locs_i]
            xj, yj = x_all[locs_j], y_all[locs_j]

            for comp in _components(merged):
                minx, miny, maxx, maxy = comp.bounds

                # Restrict to traces in the bounding box
                mask_i = (xi >= minx) & (xi <= maxx) & (yi >= miny) & (yi <= maxy)
                mask_j = (xj >= minx) & (xj <= maxx) & (yj >= miny) & (yj <= maxy)
                if not mask_i.any() or not mask_j.any():
                    continue

                sub_i = np.where(mask_i)[0]
                sub_j = np.where(mask_j)[0]
                pts_i = np.column_stack([xi[sub_i], yi[sub_i]])
                pts_j = np.column_stack([xj[sub_j], yj[sub_j]])

                tree_j = cKDTree(pts_j)
                dists, nn_j = tree_j.query(pts_i)
                best_i_local = int(np.argmin(dists))
                best_j_local = int(nn_j[best_i_local])
                min_dist = float(dists[best_i_local])

                if min_dist > threshold:
                    continue

                # Map back to locs arrays
                i_loc = sub_i[best_i_local]
                j_loc = sub_j[best_j_local]

                b_i = _bearing(xi, yi, i_loc)
                b_j = _bearing(xj, yj, j_loc)
                angle = _acute_angle(b_i, b_j)

                if angle < 20.0:
                    continue  # nearly parallel, not a true crossing

                # Crossover coordinates: midpoint of the two closest traces
                cx = (xi[i_loc] + xj[j_loc]) / 2.0
                cy = (yi[i_loc] + yj[j_loc]) / 2.0

                gi = locs_i[i_loc]
                gj = locs_j[j_loc]

                row = {
                    "frame_a": frame_names[fi_idx],
                    "frame_b": frame_names[fj_idx],
                    "distance_m": min_dist,
                    "angle_deg": angle,
                    "x_3031": cx,
                    "y_3031": cy,
                }
                for var in VARIABLES:
                    row[f"{var}_a"] = float(data[var][gi])
                    row[f"{var}_b"] = float(data[var][gj])
                    row[f"{var}_diff"] = row[f"{var}_a"] - row[f"{var}_b"]

                crossovers.append(row)

    if verbose:
        click.echo(f"Frame pairs checked: {pairs_checked}, crossovers found: {len(crossovers)}")

    return pd.DataFrame(crossovers), pairs_checked


def _clean(df, var):
    """Drop rows where var_a or var_b is NaN."""
    return df.dropna(subset=[f"{var}_a", f"{var}_b", f"{var}_diff"])


def make_map(df, output_dir):
    transformer_inv = Transformer.from_crs("EPSG:3031", "EPSG:4326", always_xy=True)
    all_lons, all_lats = transformer_inv.transform(df["x_3031"].values, df["y_3031"].values)
    df = df.copy()
    df["_lon"] = all_lons
    df["_lat"] = all_lats

    lat_pad = (all_lats.max() - all_lats.min()) * 0.1 + 0.5
    lon_pad = (all_lons.max() - all_lons.min()) * 0.1 + 0.5
    extent = [all_lons.min() - lon_pad, all_lons.max() + lon_pad,
              all_lats.min() - lat_pad, all_lats.max() + lat_pad]

    fig = plt.figure(figsize=(18, 12), constrained_layout=True)
    proj = ccrs.SouthPolarStereo()

    for i, var in enumerate(VARIABLES):
        ax = fig.add_subplot(2, 3, i + 1, projection=proj)
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.OCEAN, color="lightblue")
        ax.add_feature(cfeature.LAND, color="#e8e4dc")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.5)

        dfc = _clean(df, var)
        sc = ax.scatter(
            dfc["_lon"].values, dfc["_lat"].values,
            c=dfc[f"{var}_diff"].abs().values,
            cmap="viridis", s=20, transform=ccrs.PlateCarree(),
        )
        meta = VARIABLES[var]
        plt.colorbar(sc, ax=ax, label=f"|{meta['label']} diff| ({meta['unit']})", shrink=0.7)
        ax.set_title(meta["label"])

    fig.savefig(output_dir / "map.png", dpi=150)
    plt.close(fig)


def make_scatter(df, output_dir):
    fig = plt.figure(figsize=(15, 10), constrained_layout=True)
    varlist = list(VARIABLES.keys())
    norm = plt.Normalize(vmin=20, vmax=90)
    cmap = cm.plasma

    for i, var in enumerate(varlist):
        ax = fig.add_subplot(2, 3, i + 1)
        dfc = _clean(df, var)
        meta = VARIABLES[var]

        a_vals = dfc[f"{var}_a"].values
        b_vals = dfc[f"{var}_b"].values
        angles = dfc["angle_deg"].values

        lo = min(a_vals.min(), b_vals.min())
        hi = max(a_vals.max(), b_vals.max())
        ax.plot([lo, hi], [lo, hi], color="grey", linewidth=0.8, zorder=0)

        sc = ax.scatter(a_vals, b_vals, c=angles, cmap=cmap, norm=norm, s=15, alpha=0.7)

        n = len(dfc)
        rms = float(np.sqrt(np.mean(dfc[f"{var}_diff"].values ** 2)))
        ax.annotate(f"N={n}\nRMS={rms:.2f}", xy=(0.04, 0.94), xycoords="axes fraction",
                    va="top", fontsize=8)
        ax.set_xlabel(f"{meta['label']} A ({meta['unit']})", fontsize=8)
        ax.set_ylabel(f"{meta['label']} B ({meta['unit']})", fontsize=8)
        ax.set_title(meta["label"])

    sm = cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=fig.axes, label="Crossing angle (°)", shrink=0.6, pad=0.02)

    fig.savefig(output_dir / "scatter.png", dpi=150)
    plt.close(fig)


def make_differences(df, output_dir):
    fig = plt.figure(figsize=(15, 10), constrained_layout=True)
    varlist = list(VARIABLES.keys())

    for i, var in enumerate(varlist):
        ax = fig.add_subplot(2, 3, i + 1)
        dfc = _clean(df, var)
        meta = VARIABLES[var]
        diffs = dfc[f"{var}_diff"].values

        counts, edges = np.histogram(diffs, bins="auto")
        centers = (edges[:-1] + edges[1:]) / 2
        ax.bar(centers, counts, width=np.diff(edges), align="center", alpha=0.8)

        mean = float(np.mean(diffs))
        std = float(np.std(diffs))
        rms = float(np.sqrt(np.mean(diffs ** 2)))
        ax.annotate(f"mean={mean:.2f}\nstd={std:.2f}\nRMS={rms:.2f}",
                    xy=(0.97, 0.97), xycoords="axes fraction",
                    ha="right", va="top", fontsize=8)
        ax.set_xlabel(f"A − B ({meta['unit']})", fontsize=8)
        ax.set_ylabel("Count", fontsize=8)
        ax.set_title(meta["label"])

    fig.savefig(output_dir / "differences.png", dpi=150)
    plt.close(fig)


def make_summary(df):
    rows = []
    for var in VARIABLES:
        dfc = _clean(df, var)
        diffs = dfc[f"{var}_diff"].values
        rows.append({
            "variable": var,
            "N": len(dfc),
            "mean_diff": float(np.mean(diffs)),
            "std_diff": float(np.std(diffs)),
            "rms_diff": float(np.sqrt(np.mean(diffs ** 2))),
            "median_abs_diff": float(np.median(np.abs(diffs))),
        })
    return pd.DataFrame(rows)


def print_summary(summary):
    hdr = f"{'Variable':<30} {'N':>6} {'Mean':>10} {'Std':>10} {'RMS':>10} {'MedAbs':>10}"
    click.echo(hdr)
    click.echo("-" * len(hdr))
    for _, row in summary.iterrows():
        click.echo(
            f"{row['variable']:<30} {int(row['N']):>6} "
            f"{row['mean_diff']:>10.3f} {row['std_diff']:>10.3f} "
            f"{row['rms_diff']:>10.3f} {row['median_abs_diff']:>10.3f}"
        )


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option("--threshold", default=1000.0, help="Crossover distance threshold in metres")
@click.option("--output", "output_dir", default="outputs/crossovers", type=click.Path())
@click.option("--verbose", "-v", is_flag=True)
def main(config_path, threshold, output_dir, verbose):
    config = load_config(config_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    click.echo("Loading data from store...")
    data, frame_names = load_data(config)
    click.echo(f"Loaded {len(data['lat'])} QC-passing traces across {len(frame_names)} frames")

    click.echo(f"Finding crossovers (threshold={threshold} m)...")
    df, pairs_checked = find_crossovers(data, frame_names, threshold, verbose)
    click.echo(f"Found {len(df)} crossovers from {pairs_checked} frame pairs checked")

    if df.empty:
        click.echo("No crossovers found — nothing to plot.")
        return

    csv_path = output_dir / "crossovers.csv"
    df.to_csv(csv_path, index=False)
    click.echo(f"Saved: {csv_path}")

    summary = make_summary(df)
    summary_path = output_dir / "summary.csv"
    summary.to_csv(summary_path, index=False)
    click.echo(f"Saved: {summary_path}")
    click.echo()
    print_summary(summary)
    click.echo()

    click.echo("Generating map.png...")
    make_map(df, output_dir)
    click.echo(f"Saved: {output_dir / 'map.png'}")

    click.echo("Generating scatter.png...")
    make_scatter(df, output_dir)
    click.echo(f"Saved: {output_dir / 'scatter.png'}")

    click.echo("Generating differences.png...")
    make_differences(df, output_dir)
    click.echo(f"Saved: {output_dir / 'differences.png'}")


if __name__ == "__main__":
    main()
