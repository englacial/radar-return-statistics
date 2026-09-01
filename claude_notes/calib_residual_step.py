"""Residual seam step in the combined product = overlap offset of
effective-weight-corrected images: (dB_a + w_a) - (dB_b + w_b), where
w_i = median(combined - img_i) over the section sourced from img_i.

2016 DC8 (w=0): residual should equal the raw ~+1.76 dB offset -> a real step.
2018 DC8 (auto): residual should be near 0 if auto weighting smoothed the seam.
"""
import numpy as np
from xopr import OPRConnection


def find_path(attrs, *paths):
    for path in paths:
        d = attrs
        try:
            for k in path:
                d = d[k]
            return np.atleast_1d(np.asarray(d, dtype=float))
        except (KeyError, TypeError, ValueError):
            continue
    return None


def db(x):
    return 10 * np.log10(np.maximum(np.abs(x), 1e-30))


def analyze(coll):
    opr = OPRConnection()
    frames = opr.query_frames(collections=[coll], max_items=1)
    item = frames.iloc[0]
    ds = opr.load_frame(item, data_product="CSARP_standard")
    img_comb = find_path(ds.attrs,
                         ("param_array", "array", "img_comb"),
                         ("param_combine", "array_param", "img_comb"))
    imgs = {i: opr.load_frame(item, data_product="CSARP_standard", image=i,
                              allow_unlisted_products=True) for i in [1, 2, 3]}
    tc = ds.twtt.values
    Dc = db(ds.Data.values)
    surf = ds.Surface.values
    td = np.where(np.isfinite(surf), surf, 0.0)
    n_traces = Dc.shape[0]

    bounds = []
    for p in range(len(img_comb) // 3):
        T_comb, T_blank, T_guard = img_comb[3 * p: 3 * p + 3]
        b = np.minimum(np.maximum(T_blank, T_comb + td),
                       imgs[p + 1].twtt.values[-1] - T_guard)
        bounds.append(b)

    # Effective weight per image: median over traces of median(combined - img)
    # in that image's section (trimmed).
    trim = 0.3e-6
    w = {}
    for i in imgs:
        ti = imgs[i].twtt.values
        Di = db(imgs[i].Data.values)
        vals = []
        for r in range(n_traces):
            lo = bounds[i - 2][r] + trim if i >= 2 else max(tc[0], ti[0])
            hi = bounds[i - 1][r] - trim if i - 1 < len(bounds) else min(tc[-1], ti[-1])
            lo, hi = max(lo, ti[0]), min(hi, ti[-1])
            m = (tc >= lo) & (tc <= hi)
            if m.sum() < 10:
                continue
            vals.append(np.median(Dc[r][m] - np.interp(tc[m], ti, Di[r])))
        w[i] = np.median(vals)
    print(f"\n=== {coll} {item.name} ===")
    print(f"  effective weights: " + ", ".join(f"w{i}={w[i]:+.3f} dB" for i in w))

    # Residual overlap offset with weights applied
    for p, (a, b) in enumerate([(1, 2), (2, 3)]):
        T_comb, T_blank, T_guard = img_comb[3 * p: 3 * p + 3]
        ta, tb_ = imgs[a].twtt.values, imgs[b].twtt.values
        Da, Db_ = db(imgs[a].Data.values), db(imgs[b].Data.values)
        res = np.full(n_traces, np.nan)
        for r in range(n_traces):
            lo = max(T_blank, T_comb + td[r])
            hi = ta[-1] - T_guard
            m = (tb_ >= lo) & (tb_ <= hi)
            if m.sum() < 5:
                continue
            pa = np.interp(tb_[m], ta, Da[r]) + w[a]
            pb = Db_[r][m] + w[b]
            res[r] = np.median(pa - pb)
        ok = np.isfinite(res)
        print(f"  residual step img{a}/img{b} (combined-product seam): n={ok.sum()}, "
              f"median={np.nanmedian(res):+.3f} dB, "
              f"MAD={1.4826*np.nanmedian(np.abs(res-np.nanmedian(res))):.3f}")


analyze("2016_Antarctica_DC8")
analyze("2018_Antarctica_DC8")
