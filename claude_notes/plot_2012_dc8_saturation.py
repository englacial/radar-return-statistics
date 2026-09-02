"""Visualize the 2012_Antarctica_DC8 saturation situation after the
by-source-image split (from the calibrated local antarctica store)."""
import numpy as np
import icechunk
import zarr
import scipy.constants
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

storage = icechunk.local_filesystem_storage("outputs/icechunk_store_antarctica_local")
root = zarr.open_group(icechunk.Repository.open(storage=storage)
                       .readonly_session(branch="main").store, mode="r")

colls = np.array(root.attrs.asdict()["frame_collections"])
seasons = colls[root["frame_index"][:]]
m = (seasons == "2012_Antarctica_DC8")
qc = root["qc_surface_pass"][:].astype(bool)
pw = root["surface_power_dB"][:]
r = scipy.constants.c * root["surface_twtt"][:] / 2.0
src = root["surface_source_image_index"][:]
good = m & qc & np.isfinite(pw) & np.isfinite(r) & (r > 100)

pops = {
    "img1-sourced": good & (src == 1),
    "img2-sourced": good & (src >= 2),
    "unknown (-1)": good & (src < 0),
}
print({k: int(v.sum()) for k, v in pops.items()})

fig, axes = plt.subplots(1, 3, figsize=(20, 6), sharey=True)

def pct_overlay(ax, rr, p, color):
    bins = np.logspace(np.log10(rr.min()), np.log10(rr.max()), 20)
    bidx = np.digitize(rr, bins)
    for b in range(1, len(bins)):
        sel = bidx == b
        if sel.sum() > 50:
            ax.plot(np.sqrt(bins[b - 1] * bins[b]), np.percentile(p[sel], 99),
                    ".", color=color, ms=10, mec="k", mew=0.5)

# Panel 1: all traces (the pre-split view that gave fit_ok -33.1)
ax = axes[0]
rr, p = r[good], pw[good]
ax.hexbin(rr, p, gridsize=70, bins="log", cmap="viridis", xscale="log")
pct_overlay(ax, rr, p, "w")
ax.axhline(-33.1, color="r", ls="--", lw=1.5, label="old all-traces ceiling −33.1 dB")
ax.set_title(f"All QC traces (n={good.sum()})\npre-split fit: fit_ok(−33.1), pileup 17%")
ax.legend(loc="lower left", fontsize=9)

# Panels 2-3: split populations
for ax, (label, mask), color in zip(axes[1:], list(pops.items())[:2], ["c", "orange"]):
    rr, p = r[mask], pw[mask]
    if mask.sum() < 10:
        ax.set_title(f"{label}: n={mask.sum()}")
        continue
    ax.hexbin(rr, p, gridsize=70, bins="log", cmap="viridis", xscale="log")
    pct_overlay(ax, rr, p, color)
    span = np.log10(np.percentile(rr, 99)) - np.log10(np.percentile(rr, 1))
    ax.axhline(-33.1, color="r", ls="--", lw=1)
    ax.set_title(f"{label} (n={mask.sum()})\nrange span {span:.2f} decades "
                 f"({'FAILS' if span < 0.25 else 'passes'} 0.25 min)")

for ax in axes:
    ax.set_xlabel("range to surface (m)")
axes[0].set_ylabel("surface power (dB)")
fig.suptitle("2012_Antarctica_DC8: saturation populations after surface-source split", y=1.02)
fig.tight_layout()
out = "outputs/figures/sat_2012dc8_split.png"
fig.savefig(out, dpi=110, bbox_inches="tight")
print("saved", out)

# Range histogram per population (why the span collapsed)
fig2, ax2 = plt.subplots(figsize=(10, 4))
for (label, mask), color in zip(list(pops.items())[:2], ["tab:blue", "tab:orange"]):
    if mask.sum() > 10:
        ax2.hist(np.log10(r[mask]), bins=60, alpha=0.6, label=f"{label} (n={mask.sum()})", color=color)
ax2.axvline(np.log10(1200), color="k", ls=":", lw=1)
ax2.set_xlabel("log10(range to surface [m])")
ax2.set_ylabel("traces")
ax2.legend()
ax2.set_title("2012_Antarctica_DC8 range distribution by surface source image")
fig2.tight_layout()
out2 = "outputs/figures/sat_2012dc8_range_hist.png"
fig2.savefig(out2, dpi=110)
print("saved", out2)
