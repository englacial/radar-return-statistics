"""Detrended combine-point step detector on the combined product.

Fit robust linear trends of dB power vs twtt on each side of the combine point
(excluding a small guard band), extrapolate both to the boundary, and take the
difference. Compare healthy 2016_Antarctica_DC8 vs 2014_Greenland_P3.
"""
import numpy as np
from xopr import OPRConnection


def find_img_comb(ds):
    """Search param_* attrs for an img_comb vector (first non-empty wins)."""
    paths = [
        ("param_array", "array", "img_comb"),
        ("param_records", "array", "img_comb"),
        ("param_array", "qlook", "img_comb"),
        ("param_records", "qlook", "img_comb"),
        ("param_combine", "array_param", "img_comb"),
        ("param_combine", "combine", "img_comb"),
        ("param_combine", "get_heights", "qlook", "img_comb"),
    ]
    for path in paths:
        d = ds.attrs
        try:
            for k in path:
                d = d[k]
        except (KeyError, TypeError):
            continue
        v = np.atleast_1d(np.asarray(d, dtype=float))
        if v.size >= 3:
            return v
    return None


def step_at_combine(ds, img_comb, fit_us=1.5, guard_us=0.3):
    """Per-trace detrended power step at each combine point (dB)."""
    tc = ds.twtt.values
    D = np.abs(ds.Data.values)  # (slow_time, twtt)
    surf = ds.Surface.values
    n_pairs = len(img_comb) // 3
    out = []
    for p in range(n_pairs):
        T_comb, T_blank = img_comb[3 * p], img_comb[3 * p + 1]
        steps = np.full(len(surf), np.nan)
        for i in range(len(surf)):
            s = surf[i] if np.isfinite(surf[i]) else 0.0
            t0 = max(T_blank, T_comb + s)
            m_pre = (tc >= t0 - fit_us * 1e-6) & (tc <= t0 - guard_us * 1e-6)
            m_post = (tc >= t0 + guard_us * 1e-6) & (tc <= t0 + fit_us * 1e-6)
            if m_pre.sum() < 8 or m_post.sum() < 8:
                continue
            pw = 10 * np.log10(np.maximum(D[i], 1e-30))
            c_pre = np.polyfit(tc[m_pre] - t0, pw[m_pre], 1)
            c_post = np.polyfit(tc[m_post] - t0, pw[m_post], 1)
            steps[i] = c_post[1] - c_pre[1]  # both extrapolated to t0
        out.append(steps)
    return out


opr = OPRConnection()
for coll in ["2016_Antarctica_DC8", "2014_Greenland_P3"]:
    frames = opr.query_frames(collections=[coll], max_items=3)
    for j in range(min(2, len(frames))):
        item = frames.iloc[j]
        ds = opr.load_frame(item, data_product="CSARP_standard")
        img_comb = find_img_comb(ds)
        if img_comb is None or img_comb.size == 0:
            print(f"{coll} {item.name}: no img_comb vector found")
            continue
        for p, steps in enumerate(step_at_combine(ds, img_comb)):
            ok = np.isfinite(steps)
            if ok.sum() == 0:
                print(f"{coll} {item.name} pair {p+1}: no valid traces")
                continue
            print(f"{coll} {item.name} pair {p+1}: n={ok.sum()}, "
                  f"median={np.nanmedian(steps):+.2f} dB, "
                  f"MAD={1.4826*np.nanmedian(np.abs(steps-np.nanmedian(steps))):.2f}, "
                  f"frac |step|>3dB = {(np.abs(steps[ok])>3).mean():.2f}")
