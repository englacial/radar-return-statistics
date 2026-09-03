"""Radiometric calibration checks.

Check 1 — image-combine consistency: recover the effective per-image weights
the OPR combine applied (by differencing the combined product against each
image over the section it sourced), then measure the residual seam offset
actually present in the combined product at each image transition.

Check 2 — surface saturation: population-level detection of a hard ceiling in
surface power vs range-to-surface (flat-envelope rule on a binned
upper-quantile curve, fitted per surface-source-image population), run as a
second pass over store outputs.

Sign convention: positive offset = the earlier (lower-index, shallower) image
is brighter than the later one in the combined product.
"""
import logging
import time

import numpy as np

logger = logging.getLogger(__name__)

# Frame-level statuses for the image-combine check
STATUS_OK = "ok"
STATUS_NO_COMBINE = "no_combine"
STATUS_IMAGES_UNAVAILABLE = "images_unavailable"
STATUS_PARTIAL_IMAGES = "partial_images"
STATUS_LOAD_ERROR = "load_error"
STATUS_INVALID_PARAMS = "invalid_params"
STATUS_PARAMS_MISSING = "params_missing"
STATUS_INSUFFICIENT_OVERLAP = "insufficient_overlap"
STATUS_WEIGHT_RECOVERY_FAILED = "weight_recovery_failed"
STATUS_DISABLED = "disabled"

MAX_IMAGES = 3  # per user: 3 is the normal max; no known season uses more

DEFAULTS = {
    "trim_s": 0.3e-6,            # blend-zone trim around combine boundaries
    "snr_gate_db": 6.0,          # bins must exceed noise floor by this much
    "min_bins_per_trace": 10,
    "min_traces_per_pair": 30,
    "min_weight_bins": 5,
    "weight_spread_max_db": 0.1,  # MAD*1.4826 of per-trace weights above this fails recovery
    "noise_start_us": 12.0,      # record-tail-convention noise window offsets
    "noise_end_us": 7.0,
    "noise_min_bins": 5,
    "noise_fallback_percentile": 10.0,
}


# ---------------------------------------------------------------------------
# Param extraction
# ---------------------------------------------------------------------------

def _get_path(attrs, path):
    d = attrs
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


IMG_COMB_PATHS = [
    ("param_array", "array", "img_comb"),
    ("param_records", "array", "img_comb"),
    ("param_combine", "array_param", "img_comb"),
    ("param_combine", "combine", "img_comb"),
    ("param_array", "qlook", "img_comb"),
    ("param_records", "qlook", "img_comb"),
    ("param_combine", "get_heights", "qlook", "img_comb"),
    # 2012-era attr; carries the OLD 2-per-pair [T_comb, T_guard] format,
    # normalized to the modern 3-per-pair layout in find_img_comb.
    ("param_combine_wf_chan", "img_comb"),
]

TPD_PATHS = [
    ("param_records", "radar", "wfs", "Tpd"),
    ("param_array", "radar", "wfs", "Tpd"),
    ("param_combine", "radar", "wfs", "Tpd"),
    ("param_sar", "radar", "wfs", "Tpd"),
]

# 2012-era wfs is a list of per-waveform dicts (each with a scalar Tpd) rather
# than a dict of arrays.
WFS_LIST_PATHS = [
    ("param_csarp", "radar", "wfs"),
    ("param_records", "radar", "wfs"),
]

IMGS_PATHS = [
    ("param_array", "array", "imgs"),
    ("param_records", "array", "imgs"),
    ("param_combine", "combine", "imgs"),
    ("param_combine", "csarp", "imgs"),
    ("param_combine_wf_chan", "array", "imgs"),
]

WEIGHTS_MODE_PATHS = [
    ("param_array", "array", "img_comb_weights_mode"),
    ("param_combine", "combine", "img_comb_weights_mode"),
    ("param_records", "array", "img_comb_weights_mode"),
    ("param_array", "qlook", "img_comb_weights_mode"),
]


def find_img_comb(attrs):
    """Return (img_comb vector, provenance string) or (None, None).

    A present-but-empty vector returns (empty array, provenance) so callers can
    distinguish 'declared empty' (2014-era) from 'missing'.
    """
    for path in IMG_COMB_PATHS:
        v = _get_path(attrs, path)
        if v is None:
            continue
        try:
            arr = np.atleast_1d(np.asarray(v, dtype=float))
        except (TypeError, ValueError):
            continue
        # 2012-era param_combine_wf_chan uses the old 2-per-pair
        # [T_comb, T_guard] format (no T_blank; verified empirically on
        # 2012_Antarctica_DC8: transition at surf + T_comb, weights ~0).
        # Normalize to the modern [T_comb, T_blank, T_guard] layout.
        if path[0] == "param_combine_wf_chan" and arr.size and arr.size % 2 == 0:
            pairs = arr.reshape(-1, 2)
            arr = np.column_stack(
                [pairs[:, 0], np.full(len(pairs), -np.inf), pairs[:, 1]]
            ).ravel()
        return arr, ".".join(path)
    return None, None


def find_tpd(attrs):
    """Per-waveform pulse durations (seconds), or None."""
    for path in TPD_PATHS:
        v = _get_path(attrs, path)
        if v is None:
            continue
        try:
            return np.atleast_1d(np.asarray(v, dtype=float))
        except (TypeError, ValueError):
            continue
    # 2012-era: wfs is a list of per-waveform dicts with scalar Tpd
    for path in WFS_LIST_PATHS:
        v = _get_path(attrs, path)
        if isinstance(v, (list, tuple)) and v and all(isinstance(w, dict) for w in v):
            try:
                return np.array([float(w["Tpd"]) for w in v])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def find_imgs(attrs):
    """The params imgs list (wf-adc matrices), or None."""
    for path in IMGS_PATHS:
        v = _get_path(attrs, path)
        if isinstance(v, (list, tuple)) and len(v) > 0:
            return list(v)
    return None


def find_weights_mode(attrs):
    """Declared img_comb_weights_mode ('' if absent/empty)."""
    for path in WEIGHTS_MODE_PATHS:
        v = _get_path(attrs, path)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def tpd_for_images(imgs, tpd):
    """Map each image's wf entries to a pulse duration.

    imgs entries are wf-adc matrices with waveform indices in the first row
    (1-based into the radar.wfs arrays). Returns (list of Tpd seconds,
    consistent: bool) — consistent False if any image's waveforms disagree.
    """
    out, consistent = [], True
    for entry in imgs:
        m = np.atleast_2d(np.asarray(entry, dtype=float))
        # Modern layout: (2, N) with wf indices in row 0. 2012-era layout:
        # (N, 2) rows of [wf, adc] pairs — wf indices in column 0. The wf
        # axis is the one whose values are constant per image; prefer it.
        wf_vals = m[0]
        if m.shape[0] > 2 and m.shape[1] == 2:
            wf_vals = m[:, 0]
        elif m.shape[0] == 2 and m.shape[1] == 2 and len(set(m[0])) > 1 and len(set(m[:, 0])) == 1:
            wf_vals = m[:, 0]
        wfs = np.unique(wf_vals.astype(int))
        vals = [tpd[w - 1] for w in wfs if 1 <= w <= len(tpd)]
        if not vals:
            out.append(np.nan)
            consistent = False
            continue
        if len(set(np.round(vals, 12))) > 1:
            consistent = False
        out.append(float(vals[0]))
    return out, consistent


def expected_image_count(attrs):
    """Expected image count: imgs list, else img_comb-derived, else None.

    Counts above MAX_IMAGES (seen in 2014-era per-wf-adc imgs lists) are
    treated as suspect and capped.
    """
    imgs = find_imgs(attrs)
    if imgs is not None:
        return min(len(imgs), MAX_IMAGES), "imgs"
    img_comb, _ = find_img_comb(attrs)
    if img_comb is not None and img_comb.size >= 3 and img_comb.size % 3 == 0:
        return min(img_comb.size // 3 + 1, MAX_IMAGES), "img_comb"
    return None, None


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def _load_image(opr, stac_item, data_product, i, retries=2, retry_sleep_s=2.0):
    """Load image i with typed error handling.

    Returns (Dataset|None, error|None) where error is 'missing' (HTTP 404,
    permanent) or 'transient' (retried, still failing).
    """
    for attempt in range(retries + 1):
        try:
            return opr.load_frame(
                stac_item, data_product=data_product, image=i,
                allow_unlisted_products=True,
            ), None
        except FileNotFoundError:
            return None, "missing"
        except Exception:
            if attempt == retries:
                logger.warning("image %d: transient load failure", i, exc_info=True)
                return None, "transient"
            time.sleep(retry_sleep_s)


def _align_traces(ds_i, combined_ds):
    """Align an image's traces to the combined frame's slow_time.

    The pipeline decimates the combined frame, so images (always full
    resolution) are subset by nearest slow_time within the pipeline's 1 s
    pick-alignment tolerance; unmatched traces become NaN rows. Identical
    trace axes pass through untouched.
    """
    if ds_i.sizes.get("slow_time") == combined_ds.sizes["slow_time"] and np.array_equal(
        ds_i.slow_time.values, combined_ds.slow_time.values
    ):
        return ds_i
    import pandas as pd

    return ds_i.sortby("slow_time").reindex(
        slow_time=combined_ds.slow_time,
        method="nearest",
        tolerance=pd.Timedelta(seconds=1),
    )


# ---------------------------------------------------------------------------
# Geometry helpers (pure numpy)
# ---------------------------------------------------------------------------

def normalize_data_db(data, twtt):
    """abs->dB, oriented (n_traces, n_twtt)."""
    d = np.abs(np.asarray(data))
    if d.ndim != 2:
        raise ValueError("expected 2-D data")
    if d.shape[1] != twtt.size:
        if d.shape[0] == twtt.size:
            d = d.T
        else:
            raise ValueError("data shape does not match twtt")
    return 10.0 * np.log10(np.maximum(d, 1e-30))


def combine_boundaries(img_comb, td_surf, img_t_ends):
    """Per-pair, per-trace combine boundary times.

    img_comb: (n_pairs*3,) [T_comb, T_blank, T_guard] per pair.
    img_t_ends: {image index: last twtt} for the earlier image of each pair.
    Returns list of per-trace arrays (one per pair).
    """
    td = np.where(np.isfinite(td_surf), td_surf, 0.0)
    bounds = []
    for p in range(len(img_comb) // 3):
        T_comb, T_blank, T_guard = img_comb[3 * p: 3 * p + 3]
        b = np.maximum(T_blank, T_comb + td)
        t_end = img_t_ends.get(p + 1)
        if t_end is not None:
            b = np.minimum(b, t_end - T_guard)
        bounds.append(b)
    return bounds


def default_img_comb(tpds, n_images):
    """Tpd-default img_comb vector for when the params vector is empty/missing:
    T_comb = Tpd of the later image, T_blank = -inf, T_guard = Tpd of the
    earlier image."""
    vec = []
    for p in range(n_images - 1):
        vec.extend([tpds[p + 1], -np.inf, tpds[p]])
    return np.asarray(vec, dtype=float)


def recover_weights(tc, Dc_db, images_db, bounds, trim_s=DEFAULTS["trim_s"],
                    min_bins=DEFAULTS["min_weight_bins"]):
    """Effective weight per image: median(combined - img_i) over the section
    the combined product sourced from img_i.

    images_db: {idx: (twtt, dB array (n_traces, n_twtt))}.
    Returns ({idx: w}, {idx: robust spread}, {idx: n_traces_used}).
    """
    n_traces = Dc_db.shape[0]
    w, spread, n_used = {}, {}, {}
    for i, (ti, Di) in images_db.items():
        lo_b = bounds[i - 2] if i >= 2 else None
        hi_b = bounds[i - 1] if i - 1 < len(bounds) else None
        vals = []
        for r in range(n_traces):
            lo = (lo_b[r] + trim_s) if lo_b is not None else max(tc[0], ti[0])
            hi = (hi_b[r] - trim_s) if hi_b is not None else min(tc[-1], ti[-1])
            lo, hi = max(lo, ti[0], tc[0]), min(hi, ti[-1], tc[-1])
            if hi <= lo:
                continue
            m = (tc >= lo) & (tc <= hi)
            if m.sum() < min_bins:
                continue
            diff = Dc_db[r, m] - np.interp(tc[m], ti, Di[r])
            diff = diff[np.isfinite(diff)]
            if diff.size:
                vals.append(np.median(diff))
        # one NaN trace (unmatched alignment, NaN samples in the section) must
        # not poison the whole image's weight
        vals = np.asarray(vals)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            w[i], spread[i], n_used[i] = np.nan, np.nan, 0
        else:
            med = np.median(vals)
            w[i] = float(med)
            spread[i] = float(1.4826 * np.median(np.abs(vals - med)))
            n_used[i] = int(vals.size)
    return w, spread, n_used


def image_noise_floor_db(ti, Di_db, t_guard=0.0,
                         start_us=DEFAULTS["noise_start_us"],
                         end_us=DEFAULTS["noise_end_us"],
                         min_bins=DEFAULTS["noise_min_bins"],
                         fallback_percentile=DEFAULTS["noise_fallback_percentile"]):
    """Per-trace noise floor (dB): the record-tail convention (window
    [end-12us, end-7us], clipped to end before T_end - T_guard for the
    pulse-compression rolloff), floored by the per-trace 10th percentile of
    the whole record.

    The tail convention assumes the record end lies past the bed — true for
    the combined product but not for short-gate images whose record ends
    mid-ice (2012 DC8 img1 ends at 18.8 us: its tail window sits in strong
    englacial signal, inflating the floor ~45 dB and starving the SNR gate).
    Taking the elementwise min of the two estimates keeps whichever is
    uncontaminated; a slightly-low floor only admits extra bins into a
    median, which is safe."""
    fallback = np.percentile(Di_db, fallback_percentile, axis=1)
    t_end = ti[-1]
    hi = min(t_end - end_us * 1e-6, t_end - t_guard)
    lo = t_end - start_us * 1e-6
    if lo >= ti[0]:  # a truncated window would slide into top-of-record signal
        m = (ti >= lo) & (ti <= hi)
        if m.sum() >= min_bins:
            return np.minimum(np.median(Di_db[:, m], axis=1), fallback)
    return fallback


def residual_offsets(pair_a, pair_b, w_a, w_b, boundary, t_guard,
                     snr_gate_db=DEFAULTS["snr_gate_db"],
                     min_bins=DEFAULTS["min_bins_per_trace"]):
    """Per-trace residual seam offset for one image pair.

    pair_a/pair_b: (twtt, dB (n_traces, n_twtt), noise floor (n_traces,)).
    boundary: per-trace combine boundary (start of the valid overlap);
    the overlap ends at T_end(a) - T_guard. Offset = median over gated bins
    of (dB_a + w_a) - (dB_b + w_b); positive = earlier image brighter.
    """
    ta, Da, fa = pair_a
    tb, Db, fb = pair_b
    n_traces = Da.shape[0]
    out = np.full(n_traces, np.nan)
    hi = ta[-1] - t_guard
    for r in range(n_traces):
        lo = boundary[r]
        if not np.isfinite(lo) or hi <= lo:
            continue
        m = (tb >= lo) & (tb <= hi) & (tb >= ta[0])
        if not m.any():
            continue
        a_interp = np.interp(tb[m], ta, Da[r])
        b_vals = Db[r, m]
        gate = (a_interp > fa[r] + snr_gate_db) & (b_vals > fb[r] + snr_gate_db)
        if gate.sum() < min_bins:
            continue
        out[r] = np.median((a_interp[gate] + w_a) - (b_vals[gate] + w_b))
    return out


def pair_summary(offsets):
    """Mean / median / MAD / drift / n_valid of a per-trace offset series."""
    ok = np.isfinite(offsets)
    n = int(ok.sum())
    if n == 0:
        return {"mean": np.nan, "median": np.nan, "mad": np.nan,
                "drift": np.nan, "n_valid": 0}
    v = offsets[ok]
    med = float(np.median(v))
    half = len(offsets) // 2
    first, second = offsets[:half], offsets[half:]
    drift = np.nan
    if np.isfinite(first).sum() >= 5 and np.isfinite(second).sum() >= 5:
        drift = float(np.nanmean(second) - np.nanmean(first))
    return {
        "mean": float(np.mean(v)),
        "median": med,
        "mad": float(1.4826 * np.median(np.abs(v - med))),
        "drift": drift,
        "n_valid": n,
    }


def surface_source_image_index(td_surf, img_gates, guards):
    """Which image supplied the combined product's surface sample.

    img_gates: {idx: (t_start, t_end)}; guards: {idx: T_guard for the pair in
    which idx is the earlier image} (0 / absent for the last image).
    Smallest index whose gate contains td (NaN -> 0); -1 if none.
    """
    td = np.where(np.isfinite(td_surf), td_surf, 0.0)
    out = np.full(td.shape, -1, dtype=np.int8)
    for i in sorted(img_gates):
        t0, t1 = img_gates[i]
        g = guards.get(i, 0.0)
        hit = (out == -1) & (td >= t0) & (td <= t1 - g)
        out[hit] = i
    return out


# ---------------------------------------------------------------------------
# Frame-level orchestration
# ---------------------------------------------------------------------------

def check_img_combine(opr, stac_item, combined_ds, data_product,
                      params=None):
    """Run the image-combine consistency check for one frame.

    combined_ds: the already-loaded combined product. Returns a dict with
    frame status, per-pair summaries, recovered weights, worst-pair per-trace
    offsets, and surface_source_image_index (aligned to combined_ds traces).
    """
    p = dict(DEFAULTS)
    if params:
        p.update(params)
    attrs = combined_ds.attrs
    n_traces = combined_ds.sizes["slow_time"]
    result = {
        "status": STATUS_OK, "pairs": [], "weights": {}, "weight_spread": {},
        "worst_pair": -1, "offsets": np.full(n_traces, np.nan),
        "surface_source_image_index": np.full(n_traces, -1, dtype=np.int8),
        "weights_mode": find_weights_mode(attrs),
        "img_comb": None, "img_comb_provenance": None,
        "n_images_expected": None, "n_images_loaded": 0, "image_errors": {},
    }

    img_comb, provenance = find_img_comb(attrs)
    result["img_comb_provenance"] = provenance
    expected, count_source = expected_image_count(attrs)
    result["n_images_expected"] = expected
    if expected == 1:
        result["status"] = STATUS_NO_COMBINE
        result["surface_source_image_index"][:] = 1
        return result

    tc = combined_ds.twtt.values

    # Load images sequentially; convert each to numpy immediately and release
    # the dataset so at most one image's file/dataset is held at a time.
    images_db, errors = {}, {}
    for i in range(1, (expected or MAX_IMAGES) + 1):
        ds_i, err = _load_image(opr, stac_item, data_product, i,
                                retries=p.get("image_load_retries", 2))
        if ds_i is None:
            errors[i] = err
            continue
        try:
            aligned = _align_traces(ds_i, combined_ds)
            ti = np.asarray(aligned.twtt.values, dtype=float)
            images_db[i] = (ti, normalize_data_db(aligned.Data.values, ti))
        except Exception:
            logger.warning("image %d: could not align/convert", i, exc_info=True)
            errors[i] = "transient"
        finally:
            try:
                ds_i.close()
            except Exception:
                pass
    result["n_images_loaded"] = len(images_db)
    result["image_errors"] = errors
    if len(images_db) <= 1:
        # A true single-image frame has no img_NN files at all AND params
        # declaring one image (handled above); anything else with <2 loadable
        # images means combining occurred but the files aren't usable.
        if any(e == "transient" for e in errors.values()):
            result["status"] = STATUS_LOAD_ERROR
        else:
            result["status"] = STATUS_IMAGES_UNAVAILABLE
        return result

    # Pair coverage must reflect EXPECTED images, not merely loaded ones: a
    # trailing missing image (img3 404 when params declare 3) must surface as
    # an unassessed pair (-> partial_images), never silently shorten the pair
    # list and leave the frame "ok". The declared img_comb length still bounds
    # the pair count below, which protects against inflated 2014-era
    # per-wf-adc imgs lists.
    n_images = max(max(images_db), min(expected or 0, MAX_IMAGES))
    # Resolve the img_comb vector (Tpd defaults when declared empty/missing)
    if img_comb is None or img_comb.size < 3:
        tpd = find_tpd(attrs)
        imgs = find_imgs(attrs)
        if tpd is None:
            result["status"] = STATUS_PARAMS_MISSING
            return result
        if imgs is not None and len(imgs) >= n_images:
            tpds, consistent = tpd_for_images(imgs[:n_images], tpd)
            if not consistent:
                logger.info("imgs entries disagree on Tpd; using first wf per image")
        elif len(tpd) >= n_images:
            tpds = [float(t) for t in tpd[:n_images]]
        else:
            result["status"] = STATUS_INVALID_PARAMS
            return result
        if not np.all(np.isfinite(tpds)):
            result["status"] = STATUS_INVALID_PARAMS
            return result
        img_comb = default_img_comb(tpds, n_images)
        result["img_comb_provenance"] = (provenance or "") + "+tpd_default"
    result["img_comb"] = img_comb

    n_pairs_possible = min(len(img_comb) // 3, n_images - 1)
    if n_pairs_possible < 1:
        result["status"] = STATUS_INVALID_PARAMS
        return result

    Dc = normalize_data_db(combined_ds.Data.values, tc)
    td_surf = combined_ds.Surface.values if "Surface" in combined_ds else np.full(n_traces, np.nan)

    img_t_ends = {i: images_db[i][0][-1] for i in images_db}
    bounds = combine_boundaries(img_comb, td_surf, img_t_ends)

    # surface source image index
    guards = {p_ + 1: img_comb[3 * p_ + 2] for p_ in range(len(img_comb) // 3)}
    gates = {i: (images_db[i][0][0], images_db[i][0][-1]) for i in images_db}
    result["surface_source_image_index"] = surface_source_image_index(
        td_surf, gates, guards)

    # effective weights
    w, spread, n_used = recover_weights(tc, Dc, images_db, bounds,
                                        trim_s=p["trim_s"],
                                        min_bins=p["min_weight_bins"])
    result["weights"], result["weight_spread"] = w, spread
    usable = {i for i in w if np.isfinite(w[i])}
    recovery_ok = all(
        np.isfinite(spread[i]) and spread[i] <= p["weight_spread_max_db"]
        for i in usable
    ) and len(usable) >= 2
    if not recovery_ok:
        result["status"] = STATUS_WEIGHT_RECOVERY_FAILED
        w = {i: 0.0 for i in images_db}  # raw offsets only (parquet diagnostic)

    # residual offsets per adjacent pair
    floors = {i: image_noise_floor_db(
        images_db[i][0], images_db[i][1],
        t_guard=guards.get(i, 0.0),
        start_us=p["noise_start_us"], end_us=p["noise_end_us"],
        min_bins=p["noise_min_bins"],
        fallback_percentile=p["noise_fallback_percentile"],
    ) for i in images_db}

    pair_series = {}
    any_ok, any_missing = False, False
    # min_traces_per_pair is tuned against full-resolution frames (thousands of
    # traces); cap it proportionally so decimated pipeline frames (~tens of
    # traces) are not held to a far stricter relative bar.
    min_traces = min(p["min_traces_per_pair"], max(5, int(0.25 * n_traces)))
    for pr in range(n_pairs_possible):
        a, b = pr + 1, pr + 2
        if a not in images_db or b not in images_db:
            any_missing = True
            result["pairs"].append({"pair": pr + 1, "a": a, "b": b,
                                    "status": STATUS_IMAGES_UNAVAILABLE,
                                    **pair_summary(np.full(n_traces, np.nan))})
            continue
        offs = residual_offsets(
            (images_db[a][0], images_db[a][1], floors[a]),
            (images_db[b][0], images_db[b][1], floors[b]),
            w.get(a, 0.0), w.get(b, 0.0),
            bounds[pr], img_comb[3 * pr + 2],
            snr_gate_db=p["snr_gate_db"], min_bins=p["min_bins_per_trace"],
        )
        summ = pair_summary(offs)
        status = STATUS_OK if summ["n_valid"] >= min_traces else STATUS_INSUFFICIENT_OVERLAP
        result["pairs"].append({"pair": pr + 1, "a": a, "b": b, "status": status, **summ})
        if status == STATUS_OK:
            any_ok = True
            pair_series[pr + 1] = offs

    if result["status"] == STATUS_OK:
        if not any_ok:
            result["status"] = STATUS_INSUFFICIENT_OVERLAP
        elif any_missing:
            result["status"] = STATUS_PARTIAL_IMAGES

    if pair_series:
        worst = max(pair_series, key=lambda k: abs(result["pairs"][k - 1]["mean"]))
        result["worst_pair"] = worst
        result["offsets"] = pair_series[worst]
    return result


# ---------------------------------------------------------------------------
# Check 2: surface saturation (second pass, plain numpy)
# ---------------------------------------------------------------------------

SAT_DEFAULTS = {
    "quantile": 99.0,
    "n_bins": 24,
    "min_traces_per_bin": 50,
    "min_occupied_bins": 6,
    # 0.25 decades for every fit — source-restricted subpopulations inherently
    # span less range, and the user chose one uniform minimum over
    # special-casing (2026-08-31).
    "min_span_decades": 0.25,
    # Flat rule: the envelope slope must sit in a near-flat band to count as a
    # ceiling. The unsaturated envelope falls ~-20 dB/decade; a strongly
    # RISING envelope is not a clip ceiling either (gain/geometry artifact),
    # so the band is two-sided.
    "flat_slope_db_per_decade": -12.0,
    "flat_slope_max_db_per_decade": 12.0,
    "pileup_delta_db": 1.0,
    "n_boot": 200,
}

FIT_OK = "fit_ok"
NO_PLATEAU = "no_plateau"
INSUFFICIENT_SUPPORT = "insufficient_support"


def _theil_sen(x, y):
    """Theil-Sen slope + intercept (median of pairwise slopes)."""
    n = len(x)
    slopes = [(y[j] - y[i]) / (x[j] - x[i])
              for i in range(n) for j in range(i + 1, n) if x[j] != x[i]]
    if not slopes:
        return np.nan, np.nan
    slope = np.median(slopes)
    return float(slope), float(np.median(y - slope * x))


def _binned_upper_quantile(power_db, range_m, params):
    """Equal-width bins in log10(range), with adjacent sparse bins merged.

    Equal-width base bins keep bin spacing (and hence slope estimation)
    faithful to the range geometry rather than the altitude histogram. Bins
    under min_traces_per_bin are greedily merged with the next bin(s) until
    they reach the floor, so a thin ceiling-following tail (2012 DC8 img1:
    95% of traces at 400-700 m, real tail to 3 km) contributes a few wide
    bins that extend the fitted span instead of being discarded — without
    concentrating extra bins in the dense band, which would dilute the
    Theil-Sen slope and admit false flats. Bin x = median log10(r) of the
    (merged) bin's traces; a trailing merged bin still under the floor is
    dropped.
    """
    logr = np.log10(range_m)
    lo, hi = np.percentile(logr, [1, 99])
    edges = np.linspace(lo, hi, params["n_bins"] + 1)
    idx = np.digitize(logr, edges)
    xs, qs, ns = [], [], []
    pend_sel = np.zeros(logr.size, dtype=bool)
    for b in range(1, params["n_bins"] + 1):
        pend_sel |= idx == b
        n = int(pend_sel.sum())
        if n >= params["min_traces_per_bin"]:
            xs.append(float(np.median(logr[pend_sel])))
            qs.append(float(np.percentile(power_db[pend_sel], params["quantile"])))
            ns.append(n)
            pend_sel = np.zeros(logr.size, dtype=bool)
    return np.asarray(xs), np.asarray(qs), np.asarray(ns)


def fit_ceiling(power_db, range_m, params=None, rng=None):
    """Season/segment-level ceiling (clip level) detection via the
    flat-envelope rule: a binned upper-quantile envelope whose Theil-Sen slope
    is far shallower than the unsaturated ~-20 dB/decade is a ceiling.

    (A piecewise plateau+decline model for partial saturation was removed in
    method 0.4.0: it never decided a fit on any real season across all
    stores — every detection came from the flat rule — and it complicated the
    two-regime hardening. See docs/dataset_changelog.md.)

    Returns a dict: status, level, level_ci, single_slope, pileup_fraction,
    n_traces, n_bins_occupied, span_decades.
    """
    p = dict(SAT_DEFAULTS)
    if params:
        p.update(params)
    rng = rng or np.random.default_rng(0)
    ok = np.isfinite(power_db) & np.isfinite(range_m) & (range_m > 1)
    power_db, range_m = np.asarray(power_db)[ok], np.asarray(range_m)[ok]
    out = {"status": INSUFFICIENT_SUPPORT, "level": np.nan, "level_ci": (np.nan, np.nan),
           "single_slope": np.nan,
           "pileup_fraction": np.nan, "n_traces": int(ok.sum()),
           "n_bins_occupied": 0, "span_decades": np.nan}
    if power_db.size < p["min_traces_per_bin"] * p["min_occupied_bins"]:
        return out
    xs, qs, ns = _binned_upper_quantile(power_db, range_m, p)
    out["n_bins_occupied"] = len(xs)
    if len(xs) < p["min_occupied_bins"]:
        return out
    span = xs[-1] - xs[0]
    out["span_decades"] = float(span)
    if span < p["min_span_decades"]:
        return out

    single_slope, _ = _theil_sen(xs, qs)
    out["single_slope"] = single_slope

    def decide(xs_, qs_):
        s, _ = _theil_sen(xs_, qs_)
        if p["flat_slope_db_per_decade"] <= s <= p["flat_slope_max_db_per_decade"]:
            return float(np.median(qs_))  # near-flat envelope = ceiling
        return None

    level = decide(xs, qs)
    if level is None:
        out["status"] = NO_PLATEAU
        return out
    out["level"] = level
    out["status"] = FIT_OK

    # bootstrap CI on the level (resample occupied bins)
    levels = []
    for _ in range(p["n_boot"]):
        sel = np.sort(rng.integers(0, len(xs), len(xs)))
        lvl = decide(xs[sel], qs[sel])
        if lvl is not None:
            levels.append(lvl)
    if len(levels) >= 20:
        out["level_ci"] = (float(np.percentile(levels, 2.5)),
                           float(np.percentile(levels, 97.5)))
    out["pileup_fraction"] = pileup_fraction(power_db, out["level"], p["pileup_delta_db"])
    return out


def pileup_fraction(power_db, level, delta_db=SAT_DEFAULTS["pileup_delta_db"]):
    """Fraction of finite traces within delta of the ceiling level."""
    ok = np.isfinite(power_db)
    if not ok.any() or not np.isfinite(level):
        return np.nan
    return float((power_db[ok] >= level - delta_db).mean())


def ceiling_margin(power_db, fit):
    """Per-trace ceiling margin (dB): level - power. All-NaN unless fit_ok."""
    power_db = np.asarray(power_db, dtype=float)
    if fit.get("status") != FIT_OK:
        return np.full(power_db.shape, np.nan)
    return fit["level"] - power_db


def cross_cap_step(power_db, range_m, m1, m2, params=None):
    """Empirical img2-population bias: img1 minus img2 upper-quantile curves on
    a common log-range binning, over overlapping bins; if the populations are
    range-disjoint (the usual case — the gate cap splits by range), difference
    of the two adjacent boundary bins instead. NaN when either side is empty.
    """
    p = dict(SAT_DEFAULTS)
    if params:
        p.update(params)
    ok = np.isfinite(power_db) & np.isfinite(range_m) & (range_m > 1)
    m1, m2 = m1 & ok, m2 & ok
    if m1.sum() < p["min_traces_per_bin"] or m2.sum() < p["min_traces_per_bin"]:
        return np.nan
    logr = np.log10(np.asarray(range_m, dtype=float))
    lo, hi = np.percentile(logr[m1 | m2], [1, 99])
    edges = np.linspace(lo, hi, p["n_bins"] + 1)

    def q_per_bin(mask):
        idx = np.digitize(logr[mask], edges)
        pw = np.asarray(power_db)[mask]
        out = {}
        for b in range(1, p["n_bins"] + 1):
            sel = idx == b
            if sel.sum() >= p["min_traces_per_bin"]:
                out[b] = float(np.percentile(pw[sel], p["quantile"]))
        return out

    q1, q2 = q_per_bin(m1), q_per_bin(m2)
    if not q1 or not q2:
        return np.nan
    common = sorted(set(q1) & set(q2))
    if common:
        return float(np.median([q1[b] - q2[b] for b in common]))
    return q1[max(q1)] - q2[min(q2)]  # adjacent boundary bins


def fit_season_by_source(power_db, range_m, source_index, params=None):
    """Season-level saturation fits split by surface source image.

    Where the source index is populated, fit the img1-sourced (index==1) and
    img2-sourced (index>=2) populations separately (relaxed span minimum) and
    report the cross-cap step as the empirical img2 bias. Where it is not
    (pre-schema stores, images unavailable), fall back to one all-traces fit.
    """
    p = dict(SAT_DEFAULTS)
    if params:
        p.update(params)
    src = np.asarray(source_index)
    power_db = np.asarray(power_db, dtype=float)
    range_m = np.asarray(range_m, dtype=float)
    if not (src > 0).any():
        return {
            "ceiling_fit_population": "all_traces",
            "populations": {"all": fit_ceiling(power_db, range_m, p)},
            "cross_cap_step_db": np.nan,
        }
    m1, m2 = src == 1, src >= 2
    return {
        "ceiling_fit_population": "by_source_image",
        "populations": {
            "img1": fit_ceiling(power_db[m1], range_m[m1], p),
            "img2": fit_ceiling(power_db[m2], range_m[m2], p),
        },
        "cross_cap_step_db": cross_cap_step(power_db, range_m, m1, m2, p),
    }


def margins_by_source(power_db, source_index, season_fit):
    """Per-trace ceiling margin against the trace's own source population's
    fit when that fit is fit_ok; NaN otherwise (including unknown source in
    by_source_image mode)."""
    power_db = np.asarray(power_db, dtype=float)
    src = np.asarray(source_index)
    out = np.full(power_db.shape, np.nan)
    pops = season_fit["populations"]
    if season_fit["ceiling_fit_population"] == "all_traces":
        return ceiling_margin(power_db, pops["all"])
    for key, mask in (("img1", src == 1), ("img2", src >= 2)):
        fit = pops.get(key, {})
        if fit.get("status") == FIT_OK:
            out[mask] = fit["level"] - power_db[mask]
    return out
