"""Two-regime investigation step 3: prototype img1-only ceiling fits on the
store populations (approximating source image by td_surf vs the ~5.2us cap for
2014-era geometry), and render 2017/2019 GL hexbins."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from radar_return_statistics import calibration as cal

SCRATCH = "/tmp/claude-1000/-home-thomasteisberg-Documents-opr-radar-return-statistics/05c7ac40-a4c0-42ac-8d62-7616ab8db9e0/scratchpad"
C = 2.99792458e8

fig, axes = plt.subplots(1, 3, figsize=(18, 5))
for ax, season in zip(axes, ["2014_Greenland_P3", "2017_Greenland_P3",
                             "2019_Greenland_P3"]):
    d = np.load(f"{SCRATCH}/pop_{season}.npz")
    r, p = d["r"], d["p"]
    ax.hexbin(r, p, gridsize=70, bins="log", xscale="log", cmap="viridis")
    ax.set_title(season, fontsize=10)
    ax.set_xlabel("range (m)"); ax.set_ylabel("surface power (dB)")
fig.tight_layout()
fig.savefig("outputs/calibration/baseline/figures/two_regime_gl_seasons.png", dpi=110)
print("saved two_regime_gl_seasons.png")

# Prototype: split 2014 population at the img1-gate cap (~5.2 us => ~780 m)
d = np.load(f"{SCRATCH}/pop_2014_Greenland_P3.npz")
r, p = d["r"], d["p"]
cap_m = C * 5.21e-6 / 2  # T_end(img1)-T_guard for the 2014 geometry
margin = 50.0            # stay clear of the boundary
for label, mask in [
    ("full season (current behavior)", np.ones_like(r, bool)),
    (f"img1-sourced approx (r < {cap_m - margin:.0f} m)", r < cap_m - margin),
    (f"img2-sourced approx (r > {cap_m + margin:.0f} m)", r > cap_m + margin),
]:
    fit = cal.fit_ceiling(p[mask], r[mask])
    print(f"\n2014 GL, {label}: n={mask.sum()}")
    print("  " + ", ".join(
        f"{k}={fit[k]:.2f}" if isinstance(fit[k], float) else f"{k}={fit[k]}"
        for k in ("status", "level", "single_slope", "slope_beyond",
                  "pileup_fraction", "n_bins_occupied", "span_decades")))
    if fit["status"] == "insufficient_support":
        relax = cal.fit_ceiling(p[mask], r[mask],
                                params={"min_span_decades": 0.25})
        print("  [relaxed span 0.25] " + ", ".join(
            f"{k}={relax[k]:.2f}" if isinstance(relax[k], float) else f"{k}={relax[k]}"
            for k in ("status", "level", "single_slope", "pileup_fraction",
                      "span_decades")))
