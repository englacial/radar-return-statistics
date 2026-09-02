"""Verify reprocessing with the calibration-enabled code does NOT change any
existing per-trace values in the production stores.

Picks frames from the greenland and antarctica stores, re-runs process_frame
with the branch code (calibration ON, production configs), and compares every
pre-existing variable trace-by-trace against the stored values. Read-only.
"""
import sys
import numpy as np
import icechunk
import zarr
from xopr import OPRConnection

sys.path.insert(0, "src")
from radar_return_statistics.config import load_config
from radar_return_statistics.processing import process_frame

CASES = [
    ("antarctica", "config/config_antarctica.yaml",
     ["Data_20161104_05_037", "Data_20121025_03_022"]),
]

CALIB_VARS = {"img_comb_offset_dB", "img_comb_pair",
              "surface_source_image_index", "surface_ceiling_margin_dB"}


def open_store(name):
    storage = icechunk.s3_storage(bucket="opr-radar-metrics",
                                  prefix=f"icechunk/{name}",
                                  region="us-west-2", anonymous=True)
    repo = icechunk.Repository.open(storage=storage)
    return zarr.open_group(repo.readonly_session(branch="main").store, mode="r")


def compare(name, stored, computed):
    stored = np.asarray(stored)
    computed = np.asarray(computed)
    if stored.shape != computed.shape:
        return f"SHAPE {stored.shape} vs {computed.shape}"
    if stored.dtype.kind in "fc" or computed.dtype.kind in "fc":
        s = stored.astype(float)
        c = computed.astype(float)
        if np.array_equal(np.isnan(s), np.isnan(c)) and np.allclose(
                s[~np.isnan(s)], c[~np.isnan(c)], rtol=1e-5, atol=1e-8):
            return None
        both = ~(np.isnan(s) | np.isnan(c))
        max_diff = np.abs(s[both] - c[both]).max() if both.any() else 0.0
        nan_mismatch = int((np.isnan(s) != np.isnan(c)).sum())
        return f"DIFF max={max_diff:.3e}, nan_mismatch={nan_mismatch}"
    if stored.dtype.kind in "US" or computed.dtype.kind in "US":
        return None if np.array_equal(stored.astype(str), computed.astype(str)) else "STRING DIFF"
    return None if np.array_equal(stored, computed) else f"DIFF ({int((stored != computed).sum())} elems)"


any_fail = False
for store_name, config_path, frame_ids in CASES:
    root = open_store(store_name)
    config = load_config(config_path)
    store_fids = root["frame_id"]
    frame_names = list(root.attrs.asdict().get("frame_names", []))

    # auto-pick a second frame from a different season than the first
    resolved = []
    for fid in frame_ids:
        if fid is not None:
            resolved.append(fid)
        else:
            first_season = resolved[0].split("_")[1][:4] if resolved else ""
            pick = next((f for f in frame_names
                         if f not in resolved and f.split("_")[1][:4] != first_season), None)
            if pick:
                resolved.append(pick)

    fid_arr = store_fids[:].astype(str)
    opr = OPRConnection(cache_dir=config.get("opr", {}).get("cache_dir"))
    for fid in resolved:
        mask = fid_arr == fid
        n_store = int(mask.sum())
        if n_store == 0:
            print(f"[{store_name}] {fid}: NOT IN STORE, skipping")
            continue
        collection = None
        fi = root["frame_index"][:][mask]
        colls = root.attrs.asdict().get("frame_collections")
        if colls is not None and fi.size:
            collection = colls[int(fi[0])]
        try:
            frames = opr.query_frames(collections=[collection] if collection else None,
                                      max_items=None)
            item = frames.loc[fid]
        except Exception as e:
            print(f"[{store_name}] {fid}: STAC lookup failed ({type(e).__name__}: {e})")
            any_fail = True
            continue
        ds = process_frame(opr, item, config)
        if ds is None:
            print(f"[{store_name}] {fid}: process_frame returned None — MISMATCH vs stored frame")
            any_fail = True
            continue
        print(f"[{store_name}] {fid} ({collection}): store n={n_store}, new n={len(ds.slow_time)}, "
              f"calib status={ds.attrs.get('frame_img_comb_status', '?')}")
        if n_store != len(ds.slow_time):
            print(f"    TRACE COUNT MISMATCH")
            any_fail = True
            continue
        checked = failed = 0
        for var in sorted(set(root.array_keys())):
            if var in CALIB_VARS or var in ("slow_time", "processed_frames", "frame_index"):
                continue
            arr = root[var]
            if arr.ndim != 1 or arr.shape[0] != fid_arr.shape[0]:
                continue
            if var in ds:
                computed = ds[var].values
            elif var in ds.coords:
                computed = ds.coords[var].values
            elif var in ("latitude", "longitude", "elevation"):
                computed = ds.coords[var].values if var in ds.coords else None
            else:
                continue
            res = compare(var, arr[:][mask], computed)
            checked += 1
            if res:
                print(f"    {var}: {res}")
                failed += 1
                any_fail = True
        # frame-level attr
        try:
            fb = root.attrs.asdict()["frame_bed_pick_fraction"][int(fi[0])]
            if not np.isclose(fb, ds.attrs["frame_bed_pick_fraction"], rtol=1e-6):
                print(f"    frame_bed_pick_fraction: {fb} vs {ds.attrs['frame_bed_pick_fraction']}")
                any_fail = True
        except (KeyError, IndexError, TypeError):
            pass
        print(f"    {checked} variables compared, {failed} mismatched")

print("\nRESULT:", "FAIL — differences found" if any_fail else "PASS — reprocessing reproduces all stored values")
