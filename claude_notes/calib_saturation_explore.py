"""Explore surface saturation signature: surface power vs range-to-surface.

Unsaturated: P_surf(dB) ~ const - 20*log10(r) (plus surface roughness scatter).
Saturated: hard ceiling on P_surf independent of r at low altitude.
Uses the existing greenland store (surface_power_dB, surface_twtt, per-frame season).
"""
import numpy as np
import icechunk
import zarr
import scipy.constants
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

storage = icechunk.s3_storage(
    bucket="opr-radar-metrics", prefix="icechunk/greenland",
    region="us-west-2", anonymous=True,
)
repo = icechunk.Repository.open(storage=storage)
session = repo.readonly_session(branch="main")
root = zarr.open_group(session.store, mode="r")

surf_pw = root["surface_power_dB"][:]
surf_twtt = root["surface_twtt"][:]
qc = root["qc_surface_pass"][:].astype(bool)
frame_index = root["frame_index"][:]
collections = np.array(root.attrs["frame_collections"])
seasons = collections[frame_index]

r = scipy.constants.c * surf_twtt / 2.0
good = qc & np.isfinite(surf_pw) & np.isfinite(r) & (r > 100)

uniq = np.unique(seasons[good])
print("seasons:", uniq)

fig, axes = plt.subplots(2, 4, figsize=(22, 9), sharey=False)
for ax, season in zip(axes.flat, uniq):
    m = good & (seasons == season)
    rr, pp = r[m], surf_pw[m]
    if m.sum() > 60000:
        idx = np.random.default_rng(0).choice(m.sum(), 60000, replace=False)
        rr, pp = rr[idx], pp[idx]
    ax.hexbin(rr, pp, gridsize=80, bins="log", cmap="viridis", xscale="log")
    # overlay a -20log10(r) reference through the median point
    rmed = np.median(rr)
    pref = np.median(pp)
    rline = np.logspace(np.log10(rr.min()), np.log10(rr.max()), 50)
    ax.plot(rline, pref - 20 * (np.log10(rline) - np.log10(rmed)), "r--", lw=1)
    # per-range-bin 99th percentile to reveal a ceiling
    bins = np.logspace(np.log10(rr.min()), np.log10(rr.max()), 25)
    bidx = np.digitize(rr, bins)
    for b in range(1, len(bins)):
        sel = bidx == b
        if sel.sum() > 50:
            ax.plot(np.sqrt(bins[b-1]*bins[b]), np.percentile(pp[sel], 99), "w.", ms=6)
    ax.set_title(f"{season} (n={m.sum()})", fontsize=9)
    ax.set_xlabel("range to surface (m)")
    ax.set_ylabel("surface power (dB)")
fig.tight_layout()
fig.savefig("outputs/figures/saturation_power_vs_range_greenland.png", dpi=110)
print("saved outputs/figures/saturation_power_vs_range_greenland.png")
