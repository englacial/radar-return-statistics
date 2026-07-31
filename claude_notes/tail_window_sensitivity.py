"""Sensitivity study: where does end-of-record rolloff contaminate the record
tail, per system/season?

For sample frames across seasons, computes the median (across traces) power
profile as a function of offset from the record end, normalized to a plateau
reference (10-25 us from the end). Reports the rolloff extent (largest offset
from the end where the profile sits >1 dB below the plateau) and the bias of
candidate tail windows.

Usage: uv run python claude_notes/tail_window_sensitivity.py
Writes outputs/tail_window_sensitivity/{profiles.npz,summary.csv}
"""
import logging
import multiprocessing
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tail_sensitivity")

OUT = Path("outputs/tail_window_sensitivity")
N_PER_COLLECTION = 2
MAX_OFFSET_US = 30.0
GRID_US = np.arange(0.0, MAX_OFFSET_US, 0.05)

COLLECTIONS = {
    "antarctic": [
        "2012_Antarctica_DC8", "2014_Antarctica_DC8", "2016_Antarctica_DC8",
        "2018_Antarctica_DC8", "2013_Antarctica_P3", "2013_Antarctica_Basler",
        "2017_Antarctica_Basler", "2008_Antarctica_BaslerJKB",
        "2019_Antarctica_GV", "2023_Antarctica_BaslerMKB",
    ],
    "greenland": ["2014_Greenland_P3", "2018_Greenland_P3"],
}

CANDIDATE_WINDOWS = [  # (start_offset_us, end_offset_us) back from record end
    (5.0, 0.0),   # current behavior: last 5 us
    (7.0, 2.0),
    (10.0, 3.0),
    (10.0, 5.0),  # starting guess: 5 us window, 5 us from the end
    (15.0, 5.0),
    (15.0, 10.0),
]


def profile_one(stac_item_row, collection):
    """Median power vs offset-from-record-end for one frame."""
    from xopr import OPRConnection

    opr = OPRConnection(cache_dir=None)
    frame = opr.load_frame(stac_item_row, data_product="CSARP_standard")
    twtt = frame.twtt.values
    data = np.abs(frame.Data.values)
    if data.shape[0] != twtt.size and data.shape[1] == twtt.size:
        data = data.T
    # Median across traces per twtt sample -> dB
    with np.errstate(all="ignore"):
        prof_db = 10.0 * np.log10(np.median(data, axis=1))
    offset_us = (twtt[-1] - twtt) * 1e6

    # Interpolate onto a common offset grid (profile is defined for offsets
    # within this record's length)
    order = np.argsort(offset_us)
    grid_prof = np.interp(GRID_US, offset_us[order], prof_db[order],
                          left=np.nan, right=np.nan)

    plateau_mask = (GRID_US >= 10.0) & (GRID_US <= 25.0)
    plateau = np.nanmedian(grid_prof[plateau_mask])
    rel = grid_prof - plateau

    # Rolloff extent: largest offset from the end where the profile is more
    # than 1 dB below the plateau (scanning from the end inward).
    below = rel < -1.0
    extent = 0.0
    for k in range(len(GRID_US)):
        if below[k]:
            extent = GRID_US[k]
        elif GRID_US[k] > 2.0 and not below[k]:
            # first recovery above threshold after 2us stops the scan
            if extent > 0 and GRID_US[k] > extent:
                break
    # window biases: median of rel-profile inside each candidate window
    biases = {}
    for so, eo in CANDIDATE_WINDOWS:
        m = (GRID_US >= eo) & (GRID_US <= so)
        biases[f"bias_{so:g}_{eo:g}"] = float(np.nanmedian(rel[m]))

    return {
        "frame_id": str(stac_item_row.name),
        "collection": collection,
        "record_len_us": float((twtt[-1] - twtt[0]) * 1e6),
        "plateau_dB": float(plateau),
        "rolloff_extent_1dB_us": float(extent),
        **biases,
        "profile": grid_prof,
    }


def _worker(row, collection):
    warnings.filterwarnings("ignore")
    try:
        return profile_one(row, collection)
    except Exception as e:
        return {"error": f"{collection}/{row.name}: {type(e).__name__}: {e}"}


def main():
    from xopr import OPRConnection
    from radar_return_statistics.runner import _get_region_geometry

    OUT.mkdir(parents=True, exist_ok=True)
    opr = OPRConnection(cache_dir=None)

    jobs = []
    for area, collections in COLLECTIONS.items():
        geom = _get_region_geometry({"area": area})
        for coll in collections:
            frames = opr.query_frames(geometry=geom, collections=[coll], exclude_geometry=True)
            if frames is None or len(frames) == 0:
                logger.warning("%s: no frames", coll)
                continue
            # Mid-segment frames (avoid takeoff/turn edge frames)
            mid = frames[frames.index.str.endswith(("_008", "_010", "_012", "_015"))]
            pick = (mid if len(mid) >= N_PER_COLLECTION else frames)
            step = max(1, len(pick) // N_PER_COLLECTION)
            rows = [pick.iloc[i] for i in range(0, len(pick), step)][:N_PER_COLLECTION]
            jobs.extend((r, coll) for r in rows)

    logger.info("Profiling %d frames", len(jobs))
    results = []
    mp_ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=12, mp_context=mp_ctx) as ex:
        futs = [ex.submit(_worker, r, c) for r, c in jobs]
        for fut in as_completed(futs):
            out = fut.result()
            if "error" in out:
                logger.warning(out["error"])
                continue
            results.append(out)
            logger.info("done %s (%s): rolloff extent %.1f us",
                        out["frame_id"], out["collection"], out["rolloff_extent_1dB_us"])

    profiles = np.array([r.pop("profile") for r in results])
    df = pd.DataFrame(results)
    df.to_csv(OUT / "summary.csv", index=False)
    np.savez(OUT / "profiles.npz", grid_us=GRID_US, profiles=profiles,
             frame_ids=df.frame_id.values, collections=df.collection.values)
    logger.info("Wrote %s", OUT)

    print("\nRolloff extent (1 dB below plateau), us from record end:")
    print(df.groupby("collection").rolloff_extent_1dB_us.agg(["min", "max"]).round(2).to_string())
    print("\nCandidate window bias vs plateau (dB), worst frame per window:")
    for so, eo in CANDIDATE_WINDOWS:
        col = f"bias_{so:g}_{eo:g}"
        print(f"  window [-{so:g}, -{eo:g}] us: median bias {df[col].median():6.2f}, "
              f"worst {df[col].min():6.2f} dB")


if __name__ == "__main__":
    main()
