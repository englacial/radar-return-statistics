"""Two-regime investigation step 2: load selected 2014_Greenland_P3 frames +
images, compute surface_source_image_index, correlate with power regime,
extract gain-related params, and save per-trace tables."""
import numpy as np
from xopr import OPRConnection
from radar_return_statistics import calibration as cal

SCRATCH = "/tmp/claude-1000/-home-thomasteisberg-Documents-opr-radar-return-statistics/05c7ac40-a4c0-42ac-8d62-7616ab8db9e0/scratchpad"

FRAMES = [
    "Data_20140502_01_031",  # mixed, r 270-2782
    "Data_20140505_01_002",  # mixed, r 435-1406
    "Data_20140412_03_001",  # mixed, r 425-1076
    "Data_20140407_01_012",  # pure low, r 390-891
    "Data_20140415_01_002",  # pure high, r 1671-2042
]

opr = OPRConnection()
frames = opr.query_frames(collections=["2014_Greenland_P3"])
print(f"queried {len(frames)} frames")


def find_gainish(d, prefix="", depth=0, out=None):
    if out is None:
        out = {}
    if depth > 4 or not isinstance(d, dict):
        return out
    for k, v in d.items():
        if isinstance(v, dict):
            find_gainish(v, prefix + k + ".", depth + 1, out)
        else:
            kl = k.lower()
            if any(t in kl for t in ("gain", "atten", "rx_path", "vpp",
                                     "presum", "tadc_adjust", "tsys", "blank")):
                out[prefix + k] = np.asarray(v).ravel()[:8]
    return out


for fid in FRAMES:
    item = frames.loc[fid]
    ds = opr.load_frame(item, data_product="CSARP_standard")
    res = cal.check_img_combine(opr, item, ds, "CSARP_standard")
    td = ds.Surface.values
    tc = ds.twtt.values
    Dc = cal.normalize_data_db(ds.Data.values, tc)
    # surface peak power within +-0.3us of the pick
    n = len(td)
    spw = np.full(n, np.nan)
    for r in range(n):
        if not np.isfinite(td[r]):
            continue
        m = (tc >= td[r] - 0.3e-6) & (tc <= td[r] + 0.3e-6)
        if m.any():
            spw[r] = Dc[r, m].max()
    rng = 2.99792458e8 * td / 2
    np.savez(f"{SCRATCH}/frame_{fid}.npz", rng=rng, spw=spw,
             idx=res["surface_source_image_index"], td=td)
    idx = res["surface_source_image_index"]
    print(f"\n{fid}: status={res['status']}, weights={ {k: round(v,2) for k,v in res['weights'].items()} }")
    print(f"  img gates: " + ", ".join(
        f"img{i}: n/a" for i in []) )
    for v in sorted(set(idx)):
        mm = idx == v
        if mm.sum() == 0:
            continue
        print(f"  src_idx={v}: n={mm.sum()}, range {np.nanmin(rng[mm]):.0f}-{np.nanmax(rng[mm]):.0f} m, "
              f"surf pw p50={np.nanmedian(spw[mm]):.1f} p95={np.nanpercentile(spw[mm],95):.1f} dB")
    # params (once per frame)
    g = find_gainish({k: v for k, v in ds.attrs.items() if k.startswith("param")})
    key_fields = {k: v for k, v in g.items() if any(
        t in k.lower() for t in ("adc_gains", "atten", "rx_path", "vpp", "presum"))}
    np.savez(f"{SCRATCH}/params_{fid}.npz",
             **{k.replace("/", "_"): v for k, v in key_fields.items()})
    print(f"  gainish params ({len(g)} fields): sample:")
    for k in sorted(key_fields)[:6]:
        print(f"    {k} = {key_fields[k]}")
