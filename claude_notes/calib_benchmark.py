"""Benchmark process_frame with calibration on vs off: wall time, download
volume, and temp-cache high-water mark per frame. Read-only; no store writes."""
import shutil
import tempfile
import time
from pathlib import Path

import fsspec
from xopr import OPRConnection

from radar_return_statistics.config import normalize_config
from radar_return_statistics.processing import process_frame


def du(path):
    return sum(f.stat().st_size for f in Path(path).glob("**/*") if f.is_file())


CONFIG = normalize_config({
    "processing": {"decimate_interval": "10s", "max_workers": 1},
    "qc": {"max_heading_change_deg_per_km": 2.0},
})

opr0 = OPRConnection()
frames = opr0.query_frames(collections=["2018_Greenland_P3"], max_items=8)
selected = frames.index[:3]

for calib_on in (False, True):
    CONFIG["processing"]["calibration"]["img_combine"] = calib_on
    print(f"\n=== calibration {'ON' if calib_on else 'OFF'} ===")
    total_t = total_bytes = peak = 0
    for fid in selected:
        workdir = tempfile.mkdtemp(prefix="calib_bench_")
        fsspec.config.conf["simplecache"] = {"cache_storage": workdir}
        try:
            opr = OPRConnection(cache_dir=None)
            t0 = time.time()
            ds = process_frame(opr, frames.loc[fid], CONFIG)
            dt = time.time() - t0
            size = du(workdir)
            status = ds.attrs.get("frame_img_comb_status") if ds is not None else "FAILED"
            print(f"  {fid}: {dt:6.1f}s  cache {size/1e6:7.1f} MB  status={status}")
            total_t += dt
            total_bytes += size
            peak = max(peak, size)
        finally:
            fsspec.config.conf.pop("simplecache", None)
            shutil.rmtree(workdir, ignore_errors=True)
    print(f"  TOTAL: {total_t:.1f}s, {total_bytes/1e6:.1f} MB downloaded, "
          f"per-frame cache high-water {peak/1e6:.1f} MB")
