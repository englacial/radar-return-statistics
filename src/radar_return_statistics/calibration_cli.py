"""Standalone CLI for baseline calibration runs (no store writes).

  uv run python -m radar_return_statistics.calibration_cli --check img-combine \
      --collection 2018_Greenland_P3 --sample-per-segment 2 --max-segments 5 --name baseline
  uv run python -m radar_return_statistics.calibration_cli --check saturation \
      --store greenland --store ase --name baseline

Writes parquet tables, figures, and a JSON run manifest to
outputs/calibration/<name>/.
"""
import json
import logging
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import numpy as np
import pandas as pd

from . import calibration as cal

logger = logging.getLogger(__name__)

METHOD_VERSION = "0.1.0"
S3_BUCKET = "opr-radar-metrics"
S3_REGION = "us-west-2"
STORES = ["antarctica", "greenland", "ase", "utig", "crosssystem"]


def _git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return None


def _segment_of(frame_id):
    # Data_20180426_03_001 -> 20180426_03
    parts = frame_id.replace("Data_", "").rsplit("_", 1)
    return parts[0]


def sample_frames(frame_ids, sample_per_segment, max_segments, seed):
    """Deterministic per-segment sampling."""
    rng = np.random.default_rng(seed)
    by_seg = {}
    for fid in sorted(frame_ids):
        by_seg.setdefault(_segment_of(fid), []).append(fid)
    segs = sorted(by_seg)
    if max_segments and len(segs) > max_segments:
        segs = sorted(rng.choice(segs, max_segments, replace=False))
    out = []
    for seg in segs:
        frames = sorted(by_seg[seg])
        if len(frames) > sample_per_segment:
            frames = sorted(rng.choice(frames, sample_per_segment, replace=False))
        out.extend(frames)
    return out


def run_img_combine(collections, sample_per_segment, max_segments, seed,
                    data_product, out_dir, manifest):
    from xopr import OPRConnection

    cache_dir = Path(tempfile.mkdtemp(prefix="opr_calib_cache_"))
    frame_rows, pair_rows, failures = [], [], []
    try:
        for coll in collections:
            opr = OPRConnection(cache_dir=str(cache_dir))
            frames = opr.query_frames(collections=[coll], max_items=2000)
            selected = sample_frames(frames.index.tolist(), sample_per_segment,
                                     max_segments, seed)
            manifest["selected_frames"][coll] = selected
            logger.info("%s: %d frames selected", coll, len(selected))
            for fid in selected:
                t0 = time.time()
                try:
                    item = frames.loc[fid]
                    ds = opr.load_frame(item, data_product=data_product)
                    res = cal.check_img_combine(opr, item, ds, data_product)
                    ds.close()
                except Exception as e:
                    logger.exception("%s: failed", fid)
                    failures.append({"frame": fid, "collection": coll,
                                     "error": f"{type(e).__name__}: {e}"})
                    continue
                w = res["weights"]
                worst = res["worst_pair"]
                worst_summ = next((p for p in res["pairs"] if p["pair"] == worst), None)
                frame_rows.append({
                    "collection": coll, "frame": fid, "status": res["status"],
                    "worst_pair": worst,
                    "mean_offset_db": worst_summ["mean"] if worst_summ else np.nan,
                    "median_offset_db": worst_summ["median"] if worst_summ else np.nan,
                    "mad_db": worst_summ["mad"] if worst_summ else np.nan,
                    "drift_db": worst_summ["drift"] if worst_summ else np.nan,
                    "n_valid": worst_summ["n_valid"] if worst_summ else 0,
                    "w1": w.get(1, np.nan), "w2": w.get(2, np.nan), "w3": w.get(3, np.nan),
                    "w_spread_max": max([s for s in res["weight_spread"].values()
                                         if np.isfinite(s)], default=np.nan),
                    "weights_mode": res["weights_mode"],
                    "img_comb_provenance": res["img_comb_provenance"],
                    "n_images_loaded": res["n_images_loaded"],
                    "n_images_expected": res["n_images_expected"],
                    "surface_img1_frac": float((res["surface_source_image_index"] == 1).mean()),
                    "seconds": round(time.time() - t0, 1),
                })
                for pr in res["pairs"]:
                    raw = pr["mean"] - (w.get(pr["a"], 0.0) - w.get(pr["b"], 0.0)) \
                        if np.isfinite(pr["mean"]) else np.nan
                    pair_rows.append({"collection": coll, "frame": fid,
                                      "raw_mean_offset_db": raw, **pr})
                # bound cache growth: clear per frame
                for f in cache_dir.glob("**/*"):
                    if f.is_file():
                        f.unlink(missing_ok=True)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

    manifest["failures"] = failures
    frames_df = pd.DataFrame(frame_rows)
    pairs_df = pd.DataFrame(pair_rows)
    frames_df.to_parquet(out_dir / "img_combine_frames.parquet")
    pairs_df.to_parquet(out_dir / "img_combine_pairs.parquet")

    if len(frames_df):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5))
        for coll, g in pairs_df[pairs_df.status == cal.STATUS_OK].groupby("collection"):
            ax.hist(g["mean"], bins=40, alpha=0.6, label=f"{coll} (n={len(g)})")
        ax.set_xlabel("residual seam offset, pair mean (dB)")
        ax.set_ylabel("frame-pairs")
        ax.axvline(0, color="k", lw=0.5)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.savefig(out_dir / "figures" / "img_combine_offsets.png", dpi=120,
                    bbox_inches="tight")
        plt.close(fig)
    return frames_df, pairs_df


def _open_store(prefix):
    import icechunk
    import zarr
    storage = icechunk.s3_storage(bucket=S3_BUCKET, prefix=f"icechunk/{prefix}",
                                  region=S3_REGION, anonymous=True)
    repo = icechunk.Repository.open(storage=storage)
    session = repo.readonly_session(branch="main")
    snapshot = getattr(session, "snapshot_id", None)
    return zarr.open_group(session.store, mode="r"), str(snapshot)


def run_saturation(stores, out_dir, manifest):
    import scipy.constants
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows, seg_rows = [], []
    for prefix in stores:
        try:
            root, snapshot = _open_store(prefix)
        except Exception as e:
            manifest["failures"].append({"store": prefix, "error": str(e)})
            continue
        manifest["store_snapshots"][prefix] = snapshot
        attrs = root.attrs.asdict()
        power = root["surface_power_dB"][:]
        twtt = root["surface_twtt"][:]
        qc_key = "qc_surface_pass" if "qc_surface_pass" in root else "qc_pass"
        qc = root[qc_key][:].astype(bool)
        r = scipy.constants.c * twtt / 2.0
        colls = attrs.get("frame_collections")
        names = attrs.get("frame_names")
        if colls is None:
            manifest["failures"].append({"store": prefix,
                                         "error": "no frame_collections attr"})
            continue
        fidx = root["frame_index"][:]
        seasons = np.asarray(colls)[fidx]
        segs = np.asarray([_segment_of(n) for n in names])[fidx] if names else None

        for season in np.unique(seasons):
            m = (seasons == season) & qc
            fit = cal.fit_ceiling(power[m], r[m])
            rows.append({"store": prefix, "season": season,
                         **{k: (str(v) if isinstance(v, tuple) else v)
                            for k, v in fit.items()}})
            # per-segment regime check (segments are altitude-uniform, so
            # two-regime seasons resolve into single-regime segments)
            if segs is not None:
                for seg in np.unique(segs[m]):
                    ms = m & (segs == seg)
                    if ms.sum() < 2000:
                        continue
                    sf = cal.fit_ceiling(power[ms], r[ms])
                    seg_rows.append({"store": prefix, "season": season, "segment": seg,
                                     "status": sf["status"], "level": sf["level"],
                                     "n_traces": sf["n_traces"]})
            # figure
            mm = m & np.isfinite(power) & np.isfinite(r) & (r > 100)
            if mm.sum() > 500:
                fig, ax = plt.subplots(figsize=(7, 5))
                rr, pp = r[mm], power[mm]
                if mm.sum() > 60000:
                    sel = np.random.default_rng(0).choice(mm.sum(), 60000, replace=False)
                    rr, pp = rr[sel], pp[sel]
                ax.hexbin(rr, pp, gridsize=70, bins="log", cmap="viridis", xscale="log")
                rline = np.logspace(np.log10(rr.min()), np.log10(rr.max()), 50)
                ax.plot(rline, np.median(pp) - 20 * (np.log10(rline) - np.log10(np.median(rr))),
                        "r--", lw=1, label="-20 dB/decade")
                if fit["status"] == cal.FIT_OK:
                    ax.axhline(fit["level"], color="w", ls="-", lw=1.5,
                               label=f"ceiling {fit['level']:.1f} dB")
                ax.set_title(f"{season} [{fit['status']}] slope={fit['single_slope']:.1f} dB/dec",
                             fontsize=9)
                ax.set_xlabel("range to surface (m)")
                ax.set_ylabel("surface power (dB)")
                ax.legend(fontsize=8)
                fig.savefig(out_dir / "figures" / f"saturation_{prefix}_{season}.png",
                            dpi=110, bbox_inches="tight")
                plt.close(fig)

    df = pd.DataFrame(rows)
    df.to_parquet(out_dir / "saturation_seasons.parquet")
    if seg_rows:
        pd.DataFrame(seg_rows).to_parquet(out_dir / "saturation_segments.parquet")
    return df


@click.command()
@click.option("--check", "checks", type=click.Choice(["img-combine", "saturation"]),
              multiple=True, required=True)
@click.option("--collection", "collections", multiple=True,
              help="Collections for img-combine")
@click.option("--store", "stores", multiple=True,
              help=f"Store prefixes for saturation ({', '.join(STORES)}); default all")
@click.option("--sample-per-segment", default=2, show_default=True)
@click.option("--max-segments", default=5, show_default=True)
@click.option("--seed", default=0, show_default=True)
@click.option("--data-product", default="CSARP_standard", show_default=True)
@click.option("--name", default="baseline", show_default=True)
@click.option("-v", "--verbose", is_flag=True)
def main(checks, collections, stores, sample_per_segment, max_segments, seed,
         data_product, name, verbose):
    """Run standalone calibration baseline checks (read-only; no store writes)."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    out_dir = Path("outputs/calibration") / name
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    manifest = {
        "method_version": METHOD_VERSION,
        "git_rev": _git_rev(),
        "started": datetime.now(timezone.utc).isoformat(),
        "args": {"checks": list(checks), "collections": list(collections),
                 "stores": list(stores) or STORES, "sample_per_segment": sample_per_segment,
                 "max_segments": max_segments, "seed": seed,
                 "data_product": data_product},
        "params": {"img_combine": cal.DEFAULTS, "saturation": cal.SAT_DEFAULTS},
        "selected_frames": {}, "store_snapshots": {}, "failures": [],
    }
    if "img-combine" in checks:
        run_img_combine(list(collections), sample_per_segment, max_segments,
                        seed, data_product, out_dir, manifest)
    if "saturation" in checks:
        run_saturation(list(stores) or STORES, out_dir, manifest)
    manifest["finished"] = datetime.now(timezone.utc).isoformat()
    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    click.echo(f"wrote {out_dir}")


if __name__ == "__main__":
    main()
