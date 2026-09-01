"""Measure inter-image gain consistency in overlap regions and power step at
combine points in the combined product."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from xopr import OPRConnection

opr = OPRConnection()
frames = opr.query_frames(collections=["2016_Antarctica_DC8"], max_items=5)
item = frames.iloc[0]

ds = opr.load_frame(item, data_product="CSARP_standard")
imgs = {i: opr.load_frame(item, data_product="CSARP_standard", image=i,
                          allow_unlisted_products=True) for i in [1, 2, 3]}

img_comb = np.array([3.e-06, -np.inf, 1.e-06, 1.e-05, -np.inf, 3.e-06])
surf = ds.Surface.values  # td_surface per trace

def db(x):
    return 10 * np.log10(np.abs(x))

# --- Inter-image overlap comparison ---
# For each adjacent image pair, interpolate onto common twtt grid in the
# overlap and compute per-trace median dB offset in a usable window:
# above the combine point (past saturated surface of the higher-gain image)
# and below the end of the earlier image minus T_guard.
for pair_idx, (a, b) in enumerate([(1, 2), (2, 3)]):
    T_comb, T_blank, T_guard = img_comb[3*pair_idx: 3*pair_idx+3]
    da, dbs = imgs[a], imgs[b]
    ta = da.twtt.values
    tb = dbs.twtt.values
    Da, Db = np.abs(da.Data.values), np.abs(dbs.Data.values)  # (slow_time, twtt)
    n_traces = Da.shape[0]
    offsets = np.full(n_traces, np.nan)
    for i in range(n_traces):
        lo = max(T_blank, T_comb + (surf[i] if np.isfinite(surf[i]) else 0.0))
        hi = ta[-1] - T_guard
        if hi <= lo:
            continue
        m = (tb >= lo) & (tb <= hi)
        if m.sum() < 5:
            continue
        pa = np.interp(tb[m], ta, Da[i])
        pb = Db[i][m]
        good = (pa > 0) & (pb > 0)
        if good.sum() < 5:
            continue
        offsets[i] = np.median(db(pa[good]) - db(pb[good]))
    print(f"img{a} vs img{b}: overlap window offsets (dB): "
          f"median={np.nanmedian(offsets):.2f}, std={np.nanstd(offsets):.2f}, "
          f"p5={np.nanpercentile(offsets,5):.2f}, p95={np.nanpercentile(offsets,95):.2f}")

# --- Combined-product step detector at combine points ---
tc = ds.twtt.values
Dc = np.abs(ds.Data.values)
half = 0.5e-6  # window on each side of the combine point
for pair_idx in range(2):
    T_comb, T_blank, T_guard = img_comb[3*pair_idx: 3*pair_idx+3]
    steps = np.full(len(surf), np.nan)
    for i in range(len(surf)):
        t0 = max(T_blank, T_comb + (surf[i] if np.isfinite(surf[i]) else 0.0))
        m_above = (tc >= t0 - half) & (tc < t0)
        m_below = (tc >= t0) & (tc < t0 + half)
        if m_above.sum() < 3 or m_below.sum() < 3:
            continue
        steps[i] = db(np.median(Dc[i][m_below])) - db(np.median(Dc[i][m_above]))
    print(f"combine point {pair_idx+1}: step (below-above, dB) "
          f"median={np.nanmedian(steps):.2f}, std={np.nanstd(steps):.2f}")

# --- Visual: A-scope of one trace, all images + combined ---
rline = 100
fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(tc * 1e6, db(Dc[rline]), 'k', lw=2, label='Combined')
for i in [1, 2, 3]:
    ax.plot(imgs[i].twtt.values * 1e6, db(np.abs(imgs[i].Data.values[rline])),
            alpha=0.7, label=f'Img {i}')
for pair_idx in range(2):
    T_comb = img_comb[3*pair_idx]
    ax.axvline((surf[rline] + T_comb) * 1e6, color='r', ls='--', alpha=0.5)
ax.axvline(surf[rline] * 1e6, color='g', ls=':', label='surface')
ax.set_xlabel('TWTT (us)'); ax.set_ylabel('Power (dB)')
ax.legend(); ax.grid(True)
fig.savefig('outputs/figures/img_combine_ascope.png', dpi=120, bbox_inches='tight')
print("saved outputs/figures/img_combine_ascope.png")
