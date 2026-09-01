"""Calibration-only backfill: re-run the image-combine check for frames whose
frame_img_comb_status is retryable and update their calibration variables in
place (science metrics untouched; processed_frames unchanged).

  uv run python -m radar_return_statistics.calibration_backfill <config.yaml>
"""
import logging

import click
import numpy as np
import zarr
from xopr import OPRConnection

from . import store as store_mod
from .config import load_config
from .processing import decimate_frame, run_frame_calibration

logger = logging.getLogger(__name__)

RETRYABLE = ("load_error", "disabled")


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option("--statuses", default=",".join(RETRYABLE), show_default=True,
              help="Comma-separated statuses to retry")
@click.option("--limit", default=0, show_default=True, help="Max frames (0 = all)")
@click.option("-v", "--verbose", is_flag=True)
def main(config_path: str, statuses: str, limit: int, verbose: bool) -> None:
    """Re-run the img-combine check for frames with retryable statuses."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    retry_set = {s.strip() for s in statuses.split(",") if s.strip()}
    config = load_config(config_path)
    repo = store_mod.open_or_create_repo(config["store"])
    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")

    names = list(root.attrs.get("frame_names", []) or [])
    status_list = list(root.attrs.get("frame_img_comb_status", []) or [])
    colls = list(root.attrs.get("frame_collections", []) or [])
    targets = [
        (name, colls[i] if i < len(colls) else "")
        for i, name in enumerate(names)
        if i < len(status_list) and status_list[i] in retry_set
    ]
    if limit:
        targets = targets[:limit]
    if not targets:
        click.echo("No frames with retryable calibration status")
        return
    logger.info("Backfilling calibration for %d frames", len(targets))

    opr = OPRConnection(cache_dir=config["opr"].get("cache_dir"))
    by_coll: dict[str, list[str]] = {}
    for name, coll in targets:
        by_coll.setdefault(coll, []).append(name)

    session = repo.writable_session("main")
    n_done = 0
    for coll, frame_ids in by_coll.items():
        try:
            frames_gdf = opr.query_frames(collections=[coll] if coll else None,
                                          max_items=5000)
        except Exception:
            logger.exception("Query failed for collection %r", coll)
            continue
        for fid in frame_ids:
            if fid not in frames_gdf.index:
                logger.warning("Frame %s not found in STAC query; skipping", fid)
                continue
            try:
                frame = opr.load_frame(frames_gdf.loc[fid],
                                       data_product=config["processing"]["data_product"])
                frame = frame.sortby("slow_time")
                frame = decimate_frame(frame, config["processing"].get("decimate_interval"))
            except Exception:
                logger.exception("Frame %s: combined load failed; leaving status", fid)
                continue
            calib = run_frame_calibration(opr, frames_gdf.loc[fid], frame,
                                          config["processing"], frame_id=fid)
            frame.close()
            per_trace = {
                "img_comb_offset_dB": calib["img_comb_offset_dB"],
                "img_comb_pair": calib["img_comb_pair"],
                "surface_source_image_index": calib["surface_source_image_index"],
            }
            frame_attrs = {
                "frame_img_comb_status": calib["status"],
                "frame_img_comb_weights_mode": calib["weights_mode"],
                "frame_img_comb_offset_dB": calib["frame_offset"],
            }
            n = store_mod.update_frame_calibration(session, fid, per_trace, frame_attrs)
            if n:
                n_done += 1
                logger.info("Frame %s: backfilled %d traces (status %s)",
                            fid, n, calib["status"])
            else:
                logger.warning("Frame %s: no traces updated (missing or trace-count "
                               "mismatch)", fid)

    if n_done:
        store_mod.commit_session(session, f"[calibration] backfill {n_done} frames")
    click.echo(f"backfilled {n_done}/{len(targets)} frames")


if __name__ == "__main__":
    main()
