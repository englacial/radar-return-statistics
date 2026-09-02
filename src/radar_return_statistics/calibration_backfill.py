"""Calibration-only backfill: run the image-combine check for frames whose
frame_img_comb_status is retryable — or absent entirely (pseudo-status
``missing``, for stores written before the calibration schema) — and update
their calibration variables in place. Science metrics and processed_frames are
never touched.

  uv run python -m radar_return_statistics.calibration_backfill <config.yaml>
  uv run python -m radar_return_statistics.calibration_backfill <config.yaml> \\
      --statuses missing --workers 8   # populate a pre-calibration store

Commits every --commit-every updated frames, so an interrupted run resumes by
re-running the same command (already-backfilled frames have a status and drop
out of the ``missing`` selection).
"""
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed

import click
import numpy as np
import zarr
from xopr import OPRConnection

from . import store as store_mod
from .config import load_config
from .processing import decimate_frame, run_frame_calibration

logger = logging.getLogger(__name__)

RETRYABLE = ("load_error", "disabled")
MISSING = "missing"


def _calibrate_frame_worker(stac_item_row, frame_id, config):
    """Load one frame and run calibration; returns (per_trace, frame_attrs).

    Streaming mode (no cache_dir) spools downloads into a per-frame temp dir
    removed when the frame is done — same disk-leak guard as
    runner._process_frame_worker; without it each pool worker's cache grows
    unbounded for the life of the run.
    """
    cache_dir = config["opr"].get("cache_dir")
    if cache_dir:
        return _calibrate_frame(OPRConnection(cache_dir=cache_dir),
                                stac_item_row, frame_id, config)

    import shutil
    import tempfile

    import fsspec

    workdir = tempfile.mkdtemp(prefix="rrs_calib_cache_")
    fsspec.config.conf["simplecache"] = {"cache_storage": workdir}
    try:
        return _calibrate_frame(OPRConnection(cache_dir=None),
                                stac_item_row, frame_id, config)
    finally:
        fsspec.config.conf.pop("simplecache", None)
        shutil.rmtree(workdir, ignore_errors=True)


def _calibrate_frame(opr, stac_item_row, frame_id, config):
    frame = opr.load_frame(stac_item_row, data_product=config["processing"]["data_product"])
    frame = frame.sortby("slow_time")
    frame = decimate_frame(frame, config["processing"].get("decimate_interval"))
    calib = run_frame_calibration(opr, stac_item_row, frame,
                                  config["processing"], frame_id=frame_id)
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
    return per_trace, frame_attrs


@click.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option("--statuses", default=",".join(RETRYABLE), show_default=True,
              help=f"Comma-separated statuses to retry ('{MISSING}' selects "
                   "frames with no calibration status at all)")
@click.option("--limit", default=0, show_default=True, help="Max frames (0 = all)")
@click.option("--workers", default=0, show_default=True,
              help="Parallel frame workers (0 = processing.max_workers)")
@click.option("--commit-every", default=100, show_default=True,
              help="Commit after this many updated frames")
@click.option("-v", "--verbose", is_flag=True)
def main(config_path: str, statuses: str, limit: int, workers: int,
         commit_every: int, verbose: bool) -> None:
    """Run the img-combine check for frames with retryable/missing status."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    retry_set = {s.strip() for s in statuses.split(",") if s.strip()}
    config = load_config(config_path)
    repo = store_mod.open_or_create_repo(config["store"])
    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")

    names = list(root.attrs.get("frame_names", []) or [])
    status_list = list(root.attrs.get("frame_img_comb_status", []) or [])
    colls = list(root.attrs.get("frame_collections", []) or [])

    def effective_status(i):
        if i < len(status_list) and status_list[i]:
            return status_list[i]
        return MISSING

    targets = [
        (name, colls[i] if i < len(colls) else "")
        for i, name in enumerate(names)
        if effective_status(i) in retry_set
    ]
    if limit:
        targets = targets[:limit]
    if not targets:
        click.echo("No frames with retryable calibration status")
        return
    n_workers = workers or config["processing"].get("max_workers", 4)
    logger.info("Backfilling calibration for %d frames (%d workers)",
                len(targets), n_workers)

    opr = OPRConnection(cache_dir=config["opr"].get("cache_dir"))
    by_coll: dict[str, list[str]] = {}
    for name, coll in targets:
        by_coll.setdefault(coll, []).append(name)

    # Resolve STAC rows up front (queries are per-collection and cheap).
    jobs = []  # (frame_id, stac_row)
    for coll, frame_ids in by_coll.items():
        try:
            frames_gdf = opr.query_frames(collections=[coll] if coll else None,
                                          max_items=None)
        except Exception:
            logger.exception("Query failed for collection %r", coll)
            continue
        for fid in frame_ids:
            if fid not in frames_gdf.index:
                logger.warning("Frame %s not found in STAC query; skipping", fid)
                continue
            jobs.append((fid, frames_gdf.loc[fid]))

    session = repo.writable_session("main")
    n_done = n_failed = n_since_commit = 0

    def write_result(fid, per_trace, frame_attrs):
        nonlocal n_done, n_since_commit, session
        n = store_mod.update_frame_calibration(session, fid, per_trace, frame_attrs)
        if n:
            n_done += 1
            n_since_commit += 1
            logger.info("Frame %s: backfilled %d traces (status %s) [%d/%d]",
                        fid, n, frame_attrs["frame_img_comb_status"], n_done, len(jobs))
        else:
            logger.warning("Frame %s: no traces updated (missing or trace-count "
                           "mismatch)", fid)
        if n_since_commit >= commit_every:
            store_mod.commit_session(session, f"[calibration] backfill {n_since_commit} frames")
            n_since_commit = 0
            session = repo.writable_session("main")

    if n_workers <= 1:
        for fid, row in jobs:
            try:
                per_trace, frame_attrs = _calibrate_frame_worker(row, fid, config)
            except Exception:
                logger.exception("Frame %s: backfill failed; leaving status", fid)
                n_failed += 1
                continue
            write_result(fid, per_trace, frame_attrs)
    else:
        # spawn: workers must not inherit thread state (icechunk tokio runtime)
        mp_ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=mp_ctx) as ex:
            futures = {ex.submit(_calibrate_frame_worker, row, fid, config): fid
                       for fid, row in jobs}
            for fut in as_completed(futures):
                fid = futures[fut]
                try:
                    per_trace, frame_attrs = fut.result()
                except Exception:
                    logger.exception("Frame %s: backfill failed; leaving status", fid)
                    n_failed += 1
                    continue
                write_result(fid, per_trace, frame_attrs)

    if n_since_commit:
        store_mod.commit_session(session, f"[calibration] backfill {n_since_commit} frames")
    click.echo(f"backfilled {n_done}/{len(jobs)} frames ({n_failed} failed)")


if __name__ == "__main__":
    main()
