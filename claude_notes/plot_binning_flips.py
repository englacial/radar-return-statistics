"""Inspect season fits that changed under quantile binning (v0.3.0)."""
import sys
import numpy as np
import icechunk
import zarr
import scipy.constants
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "src")
from radar_return_statistics.calibration import _binned_upper_quantile, SAT_DEFAULTS, fit_ceiling

CASES = [
    # (store, season, src_sel, label: old -> new)
    ("greenland", "2014_Greenland_P3", 1, "img1: fit_ok(-31.6) -> insufficient_support"),
    ("greenland", "2018_Greenland_P3", 2, "img2: insufficient_support -> fit_ok(-73.4)"),
    ("antarctica", "2012_Antarctica_DC8", 1, "img1: insufficient_support -> fit_ok(-32.1)"),
    ("antarctica", "2016_Antarctica_DC8", 1, "img1: no_plateau -> fit_ok(-26.8)"),
    ("antarctica", "2014_Antarctica_DC8", 1, "img1: no_plateau -> fit_ok(-25.3)"),
    ("antarctica", "2022_Antarctica_BaslerMKB", 1, "img1: no_plateau -> fit_ok(-42.7)"),
]

roots = {}
for store in {c[0] for c in CASES}:
    storage = icechunk.local_filesystem_storage(f"outputs/icechunk_store_{store}_local")
    roots[store] = zarr.open_group(icechunk.Repository.open(storage=storage)
                                   .readonly_session(branch="main").store, mode="r")

fig, axes = plt.subplots(2, 3, figsize=(21, 11))
for ax, (store, season, src_sel, label) in zip(axes.flat, CASES):
    root = roots[store]
    a = root.attrs.asdict()
    seasons = np.array(a["frame_collections"])[root["frame_index"][:]]
    qc = root["qc_surface_pass"][:].astype(bool)
    pw = root["surface_power_dB"][:]
    r = scipy.constants.c * root["surface_twtt"][:] / 2.0
    src = root["surface_source_image_index"][:]
    m = (seasons == season) & qc & np.isfinite(pw) & np.isfinite(r) & (r > 100)
    m &= (src == src_sel) if src_sel == 1 else (src >= 2)
    rr, p = r[m], pw[m]
    ax.hexbin(rr, p, gridsize=60, bins="log", cmap="viridis", xscale="log")
    xs, qs, ns = _binned_upper_quantile(p, rr, SAT_DEFAULTS)
    ax.plot(10 ** xs, qs, "wo-", ms=6, mec="k", lw=1, label="equal-count bin q99")
    fit = fit_ceiling(p, rr)
    if np.isfinite(fit["level"]):
        ax.axhline(fit["level"], color="r", ls="--",
                   label=f"level {fit['level']:.1f} (pileup {fit['pileup_fraction']:.1%})")
    ax.set_title(f"{season} src={src_sel} (n={m.sum()})\n{label}\n"
                 f"status={fit['status']} span={fit['span_decades']:.2f} "
                 f"slope={fit['single_slope'] if fit['single_slope'] is not None else float('nan'):.1f}",
                 fontsize=9)
    ax.legend(fontsize=8, loc="lower left")
    ax.set_xlabel("range (m)")
    ax.set_ylabel("surface power (dB)")
fig.tight_layout()
out = "outputs/figures/binning_flips.png"
fig.savefig(out, dpi=100, bbox_inches="tight")
print("saved", out)
