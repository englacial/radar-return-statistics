"""Prototype: post-bed noise metrics anchored on an interpolated bed for
traces with missing bed picks.

For each trace, the bed anchor is:
  1. the pipeline's aligned bed pick (5 s reindex tolerance) where present —
     making the median metric identical to the stored post_bed_noise_dB; else
  2. a linear interpolation (in slow_time) between the segment's adjacent
     finite bed picks, for traces inside the picked span; else
  3. NaN (outside the picked span — no extrapolation).

Within the standard post-bed window [anchor + start_offset, twtt[-1] - end_offset]
three metrics are computed per trace:
  * interp_post_bed_noise_dB  - median linear power -> dB (matches pipeline def)
  * interp_post_bed_peak_dB   - max linear power -> dB
  * interp_post_bed_std_dB    - std of the dB-converted samples

Usage: uv run python claude_notes/interp_bed_noise_prototype.py
Writes per-trace results to outputs/interp_bed_noise_ase/traces.parquet.
"""
import logging
import multiprocessing
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from radar_return_statistics.config import load_config
from radar_return_statistics.processing import BED_KEY, BED_FALLBACK_KEY, SURFACE_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("interp_bed_noise")

CONFIG_PATH = "config/config_ase.yaml"
OUT_DIR = Path("outputs/interp_bed_noise_ase")
POST_START_US = 5.0
POST_END_US = 5.0


def process_one(stac_item_row, config):
    """Compute interp-anchored post-bed metrics for one frame.

    Returns dict of per-trace arrays or None. Mirrors the pipeline's
    sort/decimate/pick-alignment steps exactly so traces line up with the store.
    """
    from xopr import OPRConnection

    frame_id = stac_item_row.name
    opr = OPRConnection(cache_dir=None)
    frame = opr.load_frame(stac_item_row, data_product=config["processing"]["data_product"])
    frame = frame.sortby("slow_time")

    interval = pd.Timedelta(config["processing"]["decimate_interval"])
    times = frame.slow_time.values
    selected = [0]
    last = times[0]
    for idx in range(1, len(times)):
        if times[idx] - last >= interval:
            selected.append(idx)
            last = times[idx]
    frame = frame.isel(slow_time=selected)

    layers = opr.get_layers(frame, include_geometry=False)
    if layers is None or SURFACE_KEY not in layers:
        return None
    bed_key = BED_KEY if BED_KEY in layers else (BED_FALLBACK_KEY if BED_FALLBACK_KEY in layers else None)
    if bed_key is None:
        return None

    seg_twtt = np.asarray(layers[bed_key]["twtt"].values, dtype=float)
    seg_times = layers[bed_key]["twtt"].slow_time.values.astype("datetime64[ns]").astype(np.int64)
    finite = np.isfinite(seg_twtt)
    if not finite.any():
        return None

    st = frame.slow_time.values.astype("datetime64[ns]").astype(np.int64)

    # Pipeline-aligned pick (5 s tolerance) — anchor where a pick exists.
    aligned5 = layers[bed_key]["twtt"].reindex(
        slow_time=frame.slow_time, method="nearest",
        tolerance=pd.Timedelta(seconds=5), fill_value=np.nan,
    ).values.astype(float)

    # Interpolated anchor between adjacent finite picks, inside the picked span.
    ft, fv = seg_times[finite].astype(float), seg_twtt[finite]
    order = np.argsort(ft)
    ft, fv = ft[order], fv[order]
    interp = np.interp(st.astype(float), ft, fv)
    in_span = (st >= ft[0]) & (st <= ft[-1])
    interp[~in_span] = np.nan

    anchor = np.where(np.isfinite(aligned5), aligned5, interp)
    anchor_is_pick = np.isfinite(aligned5)

    twtt = frame.twtt.values
    data_lin = np.abs(frame.Data.values)
    if data_lin.shape[0] != twtt.size and data_lin.shape[1] == twtt.size:
        data_lin = data_lin.T

    n = data_lin.shape[1]
    med = np.full(n, np.nan)
    peak = np.full(n, np.nan)
    std = np.full(n, np.nan)
    post_hi = twtt[-1] - POST_END_US * 1e-6
    for i in range(n):
        a = anchor[i]
        if not np.isfinite(a):
            continue
        post_lo = a + POST_START_US * 1e-6
        if post_hi <= post_lo:
            continue
        mask = (twtt >= post_lo) & (twtt <= post_hi)
        if not mask.any():
            continue
        samples = data_lin[mask, i]
        samples = samples[np.isfinite(samples) & (samples > 0)]
        if samples.size == 0:
            continue
        med[i] = 10.0 * np.log10(np.median(samples))
        peak[i] = 10.0 * np.log10(samples.max())
        std[i] = float(np.std(10.0 * np.log10(samples)))

    return {
        "frame_id": [str(frame_id)] * n,
        "trace_in_frame": np.arange(n),
        "latitude": frame.Latitude.values,
        "longitude": frame.Longitude.values,
        "anchor_twtt": anchor,
        "anchor_is_pick": anchor_is_pick,
        "interp_post_bed_noise_dB": med,
        "interp_post_bed_peak_dB": peak,
        "interp_post_bed_std_dB": std,
    }


def _worker(row, config):
    warnings.filterwarnings("ignore")
    try:
        return process_one(row, config)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    import icechunk
    import zarr
    from xopr import OPRConnection
    from radar_return_statistics.runner import _get_region_geometry

    config = load_config(CONFIG_PATH)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Frames present in the ase store (process exactly these).
    s = icechunk.s3_storage(bucket="opr-radar-metrics", prefix="icechunk/ase",
                            region="us-west-2", from_env=True)
    root = zarr.open_group(icechunk.Repository.open(storage=s).readonly_session(branch="main").store, mode="r")
    store_frames = set(root.attrs["frame_names"])

    opr = OPRConnection(cache_dir=None)
    geom = _get_region_geometry(config["region"])
    frames_gdf = opr.query_frames(geometry=geom, collections=config["query"]["collections"])
    frames_gdf = frames_gdf.loc[frames_gdf.index.isin(store_frames)]
    logger.info("Processing %d frames (store has %d)", len(frames_gdf), len(store_frames))

    results = []
    n_err = 0
    mp_ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=20, mp_context=mp_ctx) as ex:
        futures = {ex.submit(_worker, row, config): fid for fid, row in frames_gdf.iterrows()}
        done = 0
        for fut in as_completed(futures):
            fid = futures[fut]
            out = fut.result()
            done += 1
            if out is None or "error" in (out or {}):
                n_err += 1
                logger.warning("Frame %s failed: %s", fid, (out or {}).get("error", "no layers"))
                continue
            results.append(pd.DataFrame(out))
            if done % 100 == 0:
                logger.info("%d/%d frames done", done, len(futures))

    df = pd.concat(results, ignore_index=True)
    df.to_parquet(OUT_DIR / "traces.parquet")
    logger.info("Wrote %d traces from %d frames (%d failures) to %s",
                len(df), df.frame_id.nunique(), n_err, OUT_DIR / "traces.parquet")


if __name__ == "__main__":
    main()
