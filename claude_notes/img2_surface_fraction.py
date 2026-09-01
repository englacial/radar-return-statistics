"""Estimate the fraction of store traces whose surface would be img2-sourced
(surface_twtt beyond the img1 gate cap T_end(img1) - T_guard).

Rough per-season caps from 2 sampled frames' img1 gates (gates can vary by
segment/altitude, so min/max caps bound the estimate). Greenland + Antarctica
stores only (user scope). Read-only.
"""
import numpy as np
import icechunk
import zarr
from xopr import OPRConnection


def get_img_comb(attrs):
    paths = [
        ("param_array", "array", "img_comb"),
        ("param_records", "array", "img_comb"),
        ("param_combine", "array_param", "img_comb"),
        ("param_combine", "combine", "img_comb"),
        ("param_array", "qlook", "img_comb"),
        ("param_combine", "get_heights", "qlook", "img_comb"),
    ]
    for p in paths:
        d = attrs
        try:
            for k in p:
                d = d[k]
            v = np.atleast_1d(np.asarray(d, dtype=float))
            if v.size >= 3:
                return v
        except (KeyError, TypeError, ValueError):
            continue
    return None


def get_tpd(attrs):
    for pk in ("param_records", "param_array", "param_combine"):
        try:
            return np.atleast_1d(np.asarray(attrs[pk]["radar"]["wfs"]["Tpd"], dtype=float))
        except (KeyError, TypeError):
            continue
    return None


opr = OPRConnection()
print(f"{'season':32s} {'caps (us)':>16s} {'n_qc':>9s} {'frac>capmed':>11s} {'lo/hi':>13s}")

for store_name in ["greenland", "antarctica"]:
    storage = icechunk.s3_storage(bucket="opr-radar-metrics",
                                  prefix=f"icechunk/{store_name}",
                                  region="us-west-2", anonymous=True)
    repo = icechunk.Repository.open(storage=storage)
    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")
    surf_twtt = root["surface_twtt"][:]
    qc = root["qc_surface_pass"][:].astype(bool)
    frame_index = root["frame_index"][:]
    colls = np.array(root.attrs.asdict()["frame_collections"])
    seasons = colls[frame_index]
    good = qc & np.isfinite(surf_twtt)

    print(f"--- store: {store_name} (n_qc={good.sum()})")
    store_affected_med = store_affected_lo = store_affected_hi = 0
    store_total = 0
    for season in np.unique(seasons):
        m = good & (seasons == season)
        n = int(m.sum())
        store_total += n
        caps = []
        try:
            frames = opr.query_frames(collections=[season], max_items=12)
            # prefer two frames from different segments
            segs = {}
            for fid in frames.index:
                segs.setdefault(fid.rsplit("_", 1)[0], fid)
            picks = list(segs.values())[:2] or list(frames.index[:2])
            for fid in picks:
                item = frames.loc[fid]
                try:
                    img1 = opr.load_frame(item, data_product="CSARP_standard",
                                          image=1, allow_unlisted_products=True)
                except Exception:
                    continue
                ic = get_img_comb(img1.attrs)
                if ic is not None:
                    t_guard = ic[2]
                else:
                    tpd = get_tpd(img1.attrs)
                    t_guard = tpd[0] if tpd is not None else 0.0
                caps.append(float(img1.twtt.values[-1]) - float(t_guard))
        except Exception as e:
            print(f"{season:32s} query failed: {type(e).__name__}")
            continue
        if not caps:
            print(f"{season:32s} {'no img1 files':>16s} {n:9d} {'unknown':>11s}")
            continue
        cap_med, cap_lo, cap_hi = np.median(caps), min(caps), max(caps)
        st = surf_twtt[m]
        f_med = float((st > cap_med).mean())
        f_lo = float((st > cap_hi).mean())   # highest cap -> fewest affected
        f_hi = float((st > cap_lo).mean())
        store_affected_med += f_med * n
        store_affected_lo += f_lo * n
        store_affected_hi += f_hi * n
        print(f"{season:32s} {cap_med*1e6:7.2f} [{cap_lo*1e6:.2f},{cap_hi*1e6:.2f}]"
              f" {n:9d} {f_med:11.1%} {f_lo:6.1%}/{f_hi:6.1%}")
    if store_total:
        print(f"{'STORE TOTAL':32s} {'':16s} {store_total:9d} "
              f"{store_affected_med/store_total:11.1%} "
              f"{store_affected_lo/store_total:6.1%}/{store_affected_hi/store_total:6.1%}")
