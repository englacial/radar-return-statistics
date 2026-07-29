"""Eligibility probe: process 2 sample frames from each unverified Antarctic season."""
import traceback

from xopr import OPRConnection
from radar_return_statistics.config import load_config
from radar_return_statistics.processing import process_frame
from radar_return_statistics.runner import _get_region_geometry

PROBE = [
    "2013_Antarctica_Basler",
    "2017_Antarctica_Basler",
    "2017_Antarctica_P3",
    "2019_Antarctica_GV",
    "2022_Antarctica_BaslerMKB",
    "2023_Antarctica_BaslerMKB",
]

config = load_config("config/config_antarctica.yaml")
opr = OPRConnection(cache_dir="./radar_cache/")
geom = _get_region_geometry(config["region"])

for coll in PROBE:
    frames = opr.query_frames(geometry=geom, collections=[coll], max_items=2)
    if frames is None or len(frames) == 0:
        print(f"{coll}: NO FRAMES RETURNED")
        continue
    for fid, row in frames.iterrows():
        try:
            ds = process_frame(opr, row, config)
            if ds is None:
                print(f"{coll} {fid}: returned None (QC skip or no picks)")
            else:
                nb = float(ds.bed_power_dB.notnull().mean())
                print(f"{coll} {fid}: OK {len(ds.slow_time)} traces, bed_power non-NaN {nb:.0%}")
        except Exception as e:
            print(f"{coll} {fid}: FAILED {type(e).__name__}: {e}")
            traceback.print_exc()
