"""Can effective img_comb weights be recovered by differencing combined vs
per-image data in the section the combined product sourced from that image?

Test: 2016_Antarctica_DC8 (no weights -> expect ~0 dB in every section) and
2018_Antarctica_DC8 (img_comb_weights_mode='auto' on the array path -> expect
nonzero, possibly per-frame-constant offsets on img2/img3 sections).
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


def analyze(coll, n_imgs):
    opr = OPRConnection()
    frames = opr.query_frames(collections=[coll], max_items=1)
    item = frames.iloc[0]
    ds = opr.load_frame(item, data_product="CSARP_standard")
    img_comb = find_path(
        ds.attrs,
        ("param_array", "array", "img_comb"),
        ("param_combine", "array_param", "img_comb"),
        ("param_combine", "combine", "img_comb"),
    )
    print(f"\n=== {coll} {item.name}: img_comb={img_comb} ===")
    imgs = {}
    for i in range(1, n_imgs + 1):
        try:
            imgs[i] = opr.load_frame(item, data_product="CSARP_standard",
                                     image=i, allow_unlisted_products=True)
        except Exception as e:
            print(f"  img {i} unavailable ({type(e).__name__})")
    tc = ds.twtt.values
    Dc = db(ds.Data.values)  # (slow_time, twtt)
    surf = ds.Surface.values
    n_traces = Dc.shape[0]

    # Per-trace section boundaries (transition to img i+1)
    bounds = []  # list of arrays, one per transition
    for p in range(len(img_comb) // 3):
        T_comb, T_blank, T_guard = img_comb[3 * p: 3 * p + 3]
        t_end_earlier = imgs[p + 1].twtt.values[-1] if (p + 1) in imgs else np.inf
        td = np.where(np.isfinite(surf), surf, 0.0)
        b = np.maximum(T_blank, T_comb + td)
        b = np.minimum(b, t_end_earlier - T_guard)
        bounds.append(b)

    trim = 0.3e-6  # stay clear of blend zone / boundary bins
    for i in sorted(imgs):
        ti = imgs[i].twtt.values
        Di = db(imgs[i].Data.values)
        lo_b = bounds[i - 2] if i >= 2 else None          # section starts
        hi_b = bounds[i - 1] if i - 1 < len(bounds) else None  # section ends
        diffs = np.full(n_traces, np.nan)
        for r in range(n_traces):
            lo = (lo_b[r] + trim) if lo_b is not None else tc[0]
            hi = (hi_b[r] - trim) if hi_b is not None else tc[-1]
            lo = max(lo, ti[0], tc[0])
            hi = min(hi, ti[-1], tc[-1])
            if hi <= lo:
                continue
            m = (tc >= lo) & (tc <= hi)
            if m.sum() < 10:
                continue
            di = np.interp(tc[m], ti, Di[r])
            diffs[r] = np.median(Dc[r][m] - di)
        ok = np.isfinite(diffs)
        if ok.sum() == 0:
            print(f"  section {i}: no valid traces")
            continue
        print(f"  section {i} (combined - img{i}): n={ok.sum()}, "
              f"median={np.nanmedian(diffs):+.3f} dB, "
              f"MAD={1.4826*np.nanmedian(np.abs(diffs-np.nanmedian(diffs))):.3f}, "
              f"p5={np.nanpercentile(diffs,5):+.3f}, p95={np.nanpercentile(diffs,95):+.3f}")


analyze("2016_Antarctica_DC8", 3)
analyze("2018_Antarctica_DC8", 3)
