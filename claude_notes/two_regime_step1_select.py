"""Two-regime investigation step 1: pull the 2014_Greenland_P3 population from
the greenland store, characterize the regimes, and select frames for loading."""
import numpy as np
import icechunk
import zarr
import scipy.constants

storage = icechunk.s3_storage(bucket="opr-radar-metrics",
                              prefix="icechunk/greenland",
                              region="us-west-2", anonymous=True)
repo = icechunk.Repository.open(storage=storage)
root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")

pw = root["surface_power_dB"][:]
twtt = root["surface_twtt"][:]
qc = root["qc_surface_pass"][:].astype(bool)
fi = root["frame_index"][:]
names = np.array(root.attrs["frame_names"])
colls = np.array(root.attrs["frame_collections"])

for season in ["2014_Greenland_P3", "2017_Greenland_P3", "2019_Greenland_P3"]:
    m = qc & np.isfinite(pw) & np.isfinite(twtt) & (colls[fi] == season)
    r = scipy.constants.c * twtt[m] / 2
    p = pw[m]
    lo = r < 700
    hi = r > 900
    print(f"\n{season}: n={m.sum()}, "
          f"r<700m: n={lo.sum()}, p99={np.percentile(p[lo],99):.1f} dB; "
          f"r>900m: n={hi.sum()}, p99={np.percentile(p[hi],99):.1f} dB")
    np.savez(f"/tmp/claude-1000/-home-thomasteisberg-Documents-opr-radar-return-statistics/05c7ac40-a4c0-42ac-8d62-7616ab8db9e0/scratchpad/pop_{season}.npz",
             r=r, p=p, frame=fi[m])

# Frame selection for 2014: per-frame range stats
season = "2014_Greenland_P3"
m = qc & np.isfinite(pw) & np.isfinite(twtt) & (colls[fi] == season)
r_all = scipy.constants.c * twtt / 2
step = 800.0
rows = []
for f in np.unique(fi[m]):
    mm = m & (fi == f)
    rr, ppw = r_all[mm], pw[mm]
    frac_lo = float((rr < step).mean())
    rows.append((names[f], mm.sum(), rr.min(), rr.max(), frac_lo,
                 np.percentile(ppw, 95)))
rows.sort(key=lambda x: x[4])
print(f"\n2014 frames: {len(rows)}")
print("-- mostly-high-altitude (frac_lo<0.1), top p95 power:")
for row in [x for x in rows if x[4] < 0.1][:4]:
    print("   %s n=%d r=[%.0f,%.0f] frac_lo=%.2f p95=%.1f" % row)
print("-- mostly-low-altitude (frac_lo>0.9):")
for row in [x for x in rows if x[4] > 0.9][:4]:
    print("   %s n=%d r=[%.0f,%.0f] frac_lo=%.2f p95=%.1f" % row)
print("-- mixed (0.25<frac_lo<0.75), most traces:")
mixed = sorted([x for x in rows if 0.25 < x[4] < 0.75], key=lambda x: -x[1])
for row in mixed[:6]:
    print("   %s n=%d r=[%.0f,%.0f] frac_lo=%.2f p95=%.1f" % row)
