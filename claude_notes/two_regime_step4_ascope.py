"""Two-regime step 4: (a) A-scopes at the seam in a 2014 mixed frame to explain
the sign; (b) blank/Tukey params; (c) source-index regime test on one mixed
frame each from 2017/2019 GL."""
import numpy as np
import icechunk
import zarr
import scipy.constants
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from xopr import OPRConnection
from radar_return_statistics import calibration as cal

C = scipy.constants.c
opr = OPRConnection()

# ---- (a)+(b): 2014 mixed frame A-scopes -----------------------------------
frames14 = opr.query_frames(collections=["2014_Greenland_P3"])
item = frames14.loc["Data_20140505_01_002"]
ds = opr.load_frame(item, data_product="CSARP_standard")
img1 = opr.load_frame(item, data_product="CSARP_standard", image=1,
                      allow_unlisted_products=True)
img2 = opr.load_frame(item, data_product="CSARP_standard", image=2,
                      allow_unlisted_products=True)
td = ds.Surface.values
rng = C * td / 2
tc = ds.twtt.values
Dc = cal.normalize_data_db(ds.Data.values, tc)
D1 = cal.normalize_data_db(img1.Data.values, img1.twtt.values)
D2 = cal.normalize_data_db(img2.Data.values, img2.twtt.values)
print("img1 gate:", img1.twtt.values[0] * 1e6, "-", img1.twtt.values[-1] * 1e6, "us")
print("img2 gate:", img2.twtt.values[0] * 1e6, "-", img2.twtt.values[-1] * 1e6, "us")

# blank / tukey params
for pk in [k for k in ds.attrs if k.startswith("param")]:
    def walk(d, prefix="", depth=0):
        if depth > 4 or not isinstance(d, dict):
            return
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v, prefix + k + ".", depth + 1)
            elif any(t in k.lower() for t in ("blank", "tukey", "tpd")):
                print(f"  {pk}.{prefix}{k} = {np.asarray(v).ravel()[:8]}")
    walk(ds.attrs[pk])

# pick traces just below and just above the 780 m boundary
i_lo = int(np.nanargmin(np.abs(rng - 700)))
i_hi = int(np.nanargmin(np.abs(rng - 900)))
fig, axes = plt.subplots(1, 2, figsize=(15, 5), sharey=True)
for ax, idx, label in [(axes[0], i_lo, "range 700 m (surface from img1)"),
                       (axes[1], i_hi, "range 900 m (surface from img2)")]:
    ax.plot(tc * 1e6, Dc[idx], "k", lw=2, label="combined")
    ax.plot(img1.twtt.values * 1e6, D1[idx], alpha=0.7, label="img1")
    ax.plot(img2.twtt.values * 1e6, D2[idx], alpha=0.7, label="img2")
    ax.axvline(td[idx] * 1e6, color="g", ls=":", label="surface")
    ax.axvline(5.21, color="r", ls="--", label="seam cap (5.21 us)")
    ax.set_xlim(0, 12)
    ax.set_xlabel("TWTT (us)")
    ax.set_title(label, fontsize=10)
    ax.grid(True)
axes[0].set_ylabel("power (dB)")
axes[0].legend(fontsize=8)
fig.tight_layout()
fig.savefig("outputs/calibration/baseline/figures/two_regime_ascope_2014.png", dpi=110)
print("saved two_regime_ascope_2014.png")

# ---- (c): 2017 / 2019 mixed-frame source-index test ------------------------
storage = icechunk.s3_storage(bucket="opr-radar-metrics",
                              prefix="icechunk/greenland",
                              region="us-west-2", anonymous=True)
repo = icechunk.Repository.open(storage=storage)
root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")
pw = root["surface_power_dB"][:]
tw = root["surface_twtt"][:]
qc = root["qc_surface_pass"][:].astype(bool)
fi = root["frame_index"][:]
names = np.array(root.attrs["frame_names"])
colls = np.array(root.attrs["frame_collections"])

for season, step_m in [("2017_Greenland_P3", 1050), ("2019_Greenland_P3", 1050)]:
    m = qc & np.isfinite(pw) & np.isfinite(tw) & (colls[fi] == season)
    r_all = C * tw / 2
    best, best_bal = None, 0
    for f in np.unique(fi[m]):
        mm = m & (fi == f)
        frac_lo = float((r_all[mm] < step_m).mean())
        bal = min(frac_lo, 1 - frac_lo) * mm.sum()
        if bal > best_bal:
            best, best_bal = f, bal
    fid = names[best]
    print(f"\n{season}: mixed frame {fid}")
    frames_s = opr.query_frames(collections=[season])
    item = frames_s.loc[fid]
    ds_s = opr.load_frame(item, data_product="CSARP_standard")
    res = cal.check_img_combine(opr, item, ds_s, "CSARP_standard")
    td_s = ds_s.Surface.values
    tcs = ds_s.twtt.values
    Dcs = cal.normalize_data_db(ds_s.Data.values, tcs)
    n = len(td_s)
    spw = np.full(n, np.nan)
    for r in range(n):
        if np.isfinite(td_s[r]):
            mm = (tcs >= td_s[r] - 0.3e-6) & (tcs <= td_s[r] + 0.3e-6)
            if mm.any():
                spw[r] = Dcs[r, mm].max()
    rngs = C * td_s / 2
    idx = res["surface_source_image_index"]
    print(f"  status={res['status']}")
    for v in sorted(set(idx)):
        mm = idx == v
        if mm.sum():
            print(f"  src_idx={v}: n={mm.sum()}, range {np.nanmin(rngs[mm]):.0f}-"
                  f"{np.nanmax(rngs[mm]):.0f} m, surf pw p50={np.nanmedian(spw[mm]):.1f} "
                  f"p95={np.nanpercentile(spw[mm],95):.1f} dB")
