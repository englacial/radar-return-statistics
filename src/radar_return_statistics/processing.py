import logging
import warnings

import numpy as np
import pandas as pd
import scipy.constants
import xarray as xr
from xopr import OPRConnection
from xopr import qc as xopr_qc

logger = logging.getLogger(__name__)

SURFACE_KEY = "standard:surface"
BED_KEY = "standard:bottom"
# Some seasons/segments publish picks under an empty-prefix layer group
# (e.g. 2019_Antarctica_GV bed picks, some Greenland P3 segments' surface
# picks); fall back to those keys when the standard ones are absent.
BED_FALLBACK_KEY = ":bottom"
SURFACE_FALLBACK_KEY = ":surface"

# QC checks that do not depend on layer picks. Traces passing these are usable
# surface observations even when the bed pick is missing — the basis for
# treating missing bed picks as censored (low-SNR) observations downstream.
PICK_INDEPENDENT_CHECKS = ("heading_change", "minimum_agl")

# Tolerance for aligning layer picks to (decimated) trace times during peak
# extraction and pick-availability bookkeeping.
PICK_ALIGN_TOLERANCE_S = 1

DEFAULT_NOISE_CONFIG = {
    "pre_surface": {"start_offset_us": 1.0, "end_offset_us": 1.0},
    "post_bed": {"start_offset_us": 5.0, "end_offset_us": 5.0},
    # Both offsets measured back from the record end: window =
    # [end - start_offset_us, end - end_offset_us]. The end gap blanks the
    # post-processing rolloff in the final microseconds of many seasons;
    # sensitivity study (claude_notes/tail_window_sensitivity.py) found sharp
    # rolloffs recovering by 6-8 us across systems, so 7 us clears them while
    # staying near the record end.
    "record_tail": {"start_offset_us": 12.0, "end_offset_us": 7.0},
}


def _resolve_noise_config(noise_config):
    """Fill in defaults for any missing noise-config keys."""
    out = {k: dict(v) for k, v in DEFAULT_NOISE_CONFIG.items()}
    if not noise_config:
        return out
    for window, defaults in DEFAULT_NOISE_CONFIG.items():
        section = noise_config.get(window) or {}
        for key in defaults:
            if key in section:
                out[window][key] = section[key]
    return out


def peak_power_in_window(data_linear, twtt_axis, pick_twtt, margin_twtt):
    """Peak power (dB) within margin_twtt of pick_twtt. Returns nan if window is empty."""
    mask = (twtt_axis >= pick_twtt - margin_twtt) & (twtt_axis <= pick_twtt + margin_twtt)
    if not mask.any():
        return np.nan
    return 10.0 * np.log10(data_linear[mask].max())


def compute_rssnr_dB(surface_power_dB, bed_power_dB, surface_twtt, bed_twtt, ice_permittivity):
    """
    Geometry-corrected surface-to-bed SNR (dB), matching the required_surface_snr definition.

    Corrects for differential one-way geometric spreading: surface range is the air path;
    effective bed range adds the in-ice path reduced by sqrt(epsilon).
    Works on scalars or numpy arrays.
    """
    c = scipy.constants.c
    n = np.sqrt(ice_permittivity)
    r_surf = c * surface_twtt / 2
    ice_thickness = (c / n) / 2 * (bed_twtt - surface_twtt)
    r_bed_eff = r_surf + ice_thickness / n
    P_surf_lin = 10.0 ** (surface_power_dB / 10.0)
    P_bed_lin = 10.0 ** (bed_power_dB / 10.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(P_surf_lin * r_surf**2 / (P_bed_lin * r_bed_eff**2))


def compute_noise_powers(frame, surface_twtt_aligned, bed_twtt_aligned, noise_config):
    """Per-trace median noise power (dB) in pre-surface and post-bed windows.

    `surface_twtt_aligned` and `bed_twtt_aligned` must be 1-D numpy arrays whose
    indices line up with `frame.slow_time`. Window edges are configured by
    offsets in microseconds:
      pre_surface  = [twtt[0] + pre.start_offset_us, surface - pre.end_offset_us]
      post_bed     = [bed + post.start_offset_us, twtt[-1] - post.end_offset_us]
    Median is taken on linear power within the window and converted to dB.
    Empty / invalid windows return nan.
    """
    cfg = _resolve_noise_config(noise_config)
    pre_start = cfg["pre_surface"]["start_offset_us"] * 1e-6
    pre_end = cfg["pre_surface"]["end_offset_us"] * 1e-6
    post_start = cfg["post_bed"]["start_offset_us"] * 1e-6
    post_end = cfg["post_bed"]["end_offset_us"] * 1e-6

    twtt = frame.twtt.values
    data_lin = np.abs(frame.Data.values)
    # Data may be (slow_time, twtt) or (twtt, slow_time); normalize to (twtt, traces)
    if data_lin.shape[0] != twtt.size and data_lin.shape[1] == twtt.size:
        data_lin = data_lin.T
    assert data_lin.shape[0] == twtt.size, "Data twtt dimension does not match frame.twtt"

    n_traces = data_lin.shape[1]
    pre_noise = np.full(n_traces, np.nan)
    post_noise = np.full(n_traces, np.nan)

    twtt_first = twtt[0]
    twtt_last = twtt[-1]
    pre_lo = twtt_first + pre_start
    post_hi = twtt_last - post_end

    surface = np.asarray(surface_twtt_aligned)
    bed = np.asarray(bed_twtt_aligned)

    for i in range(n_traces):
        s = surface[i]
        if np.isfinite(s):
            pre_hi = s - pre_end
            if pre_hi > pre_lo:
                mask = (twtt >= pre_lo) & (twtt <= pre_hi)
                if mask.any():
                    samples = data_lin[mask, i]
                    samples = samples[np.isfinite(samples)]
                    if samples.size:
                        pre_noise[i] = 10.0 * np.log10(np.median(samples))
        b = bed[i]
        if np.isfinite(b):
            post_lo = b + post_start
            if post_hi > post_lo:
                mask = (twtt >= post_lo) & (twtt <= post_hi)
                if mask.any():
                    samples = data_lin[mask, i]
                    samples = samples[np.isfinite(samples)]
                    if samples.size:
                        post_noise[i] = 10.0 * np.log10(np.median(samples))

    return pre_noise, post_noise


def _nan_peak_pair(radar_ds):
    """All-NaN (peak_twtt, peak_power) pair aligned to radar_ds.slow_time."""
    nan = xr.DataArray(
        np.full(radar_ds.sizes["slow_time"], np.nan),
        dims=("slow_time",),
        coords={"slow_time": radar_ds.slow_time},
    )
    return nan, nan.copy()


def compute_interp_bed_window_metrics(frame, anchor_twtt, noise_config):
    """Median / peak / std of power (dB) in the post-bed window anchored at
    ``anchor_twtt`` (per-trace: the bed pick where present, else a bed twtt
    interpolated between adjacent picks).

    The window and the median definition match ``compute_noise_powers``'s
    post-bed term exactly, so where the anchor is the actual pick the median is
    identical to ``post_bed_noise_dB``. Peak is the max sample (dB); std is the
    standard deviation of the dB-converted samples. NaN anchor -> NaN metrics.
    """
    cfg = _resolve_noise_config(noise_config)
    post_start = cfg["post_bed"]["start_offset_us"] * 1e-6
    post_end = cfg["post_bed"]["end_offset_us"] * 1e-6

    twtt = frame.twtt.values
    data_lin = np.abs(frame.Data.values)
    if data_lin.shape[0] != twtt.size and data_lin.shape[1] == twtt.size:
        data_lin = data_lin.T

    n_traces = data_lin.shape[1]
    med = np.full(n_traces, np.nan)
    peak = np.full(n_traces, np.nan)
    std = np.full(n_traces, np.nan)
    post_hi = twtt[-1] - post_end
    anchor = np.asarray(anchor_twtt, dtype=float)
    for i in range(n_traces):
        a = anchor[i]
        if not np.isfinite(a):
            continue
        post_lo = a + post_start
        if post_hi <= post_lo:
            continue
        mask = (twtt >= post_lo) & (twtt <= post_hi)
        if not mask.any():
            continue
        samples = data_lin[mask, i]
        samples = samples[np.isfinite(samples)]
        if samples.size == 0:
            continue
        med[i] = 10.0 * np.log10(np.median(samples))
        pos = samples[samples > 0]
        if pos.size:
            db = 10.0 * np.log10(pos)
            peak[i] = db.max()
            std[i] = float(np.std(db))
    return med, peak, std


def compute_record_tail_noise(frame, noise_config):
    """Per-trace median power (dB) in a window near the record end.

    The window is ``[end - start_offset_us, end - end_offset_us]`` — both
    offsets measured back from the last twtt sample. A nonzero end offset
    blanks the post-processing rolloff seen in the final microseconds of many
    seasons, which would otherwise bias the estimate low.

    Pick-independent: defined whether or not a bed pick exists, giving every
    trace a noise-floor estimate from below the ice. Where deep returns reach
    the window this is an upper bound on the true noise floor (which makes
    censoring bounds derived from it conservative).
    """
    cfg = _resolve_noise_config(noise_config)
    start_offset = cfg["record_tail"]["start_offset_us"] * 1e-6
    end_offset = cfg["record_tail"]["end_offset_us"] * 1e-6

    twtt = frame.twtt.values
    data_lin = np.abs(frame.Data.values)
    if data_lin.shape[0] != twtt.size and data_lin.shape[1] == twtt.size:
        data_lin = data_lin.T

    n_traces = data_lin.shape[1]
    mask = (twtt >= twtt[-1] - start_offset) & (twtt <= twtt[-1] - end_offset)
    if not mask.any():
        return np.full(n_traces, np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices
        med = np.nanmedian(data_lin[mask, :], axis=0)
        return 10.0 * np.log10(med)


def extract_layer_peak_power(radar_ds, layer_twtt, margin_twtt):
    """Extract peak power (dB) and its TWTT within a margin around a layer pick.

    Traces with no pick (or no samples within the margin) return NaN. A layer
    with no finite picks at all returns all-NaN without touching the data.
    """
    if not np.isfinite(np.asarray(layer_twtt.values, dtype=float)).any():
        return _nan_peak_pair(radar_ds)
    t_start = np.minimum(radar_ds.slow_time.min(), layer_twtt.slow_time.min())
    t_end = np.maximum(radar_ds.slow_time.max(), layer_twtt.slow_time.max())
    layer_twtt = layer_twtt.sel(slow_time=slice(t_start, t_end))
    radar_ds = radar_ds.sel(slow_time=slice(t_start, t_end))
    layer_twtt = layer_twtt.reindex(
        slow_time=radar_ds.slow_time,
        method="nearest",
        tolerance=pd.Timedelta(seconds=1),
        fill_value=np.nan,
    )

    start_twtt = layer_twtt - margin_twtt
    end_twtt = layer_twtt + margin_twtt
    data_within_margin = radar_ds.where(
        (radar_ds.twtt >= start_twtt) & (radar_ds.twtt <= end_twtt),
        drop=True,
    )
    if data_within_margin.sizes.get("twtt", 0) == 0:
        return _nan_peak_pair(radar_ds)

    power_dB = 10 * np.log10(np.abs(data_within_margin.Data))
    peak_twtt_index = power_dB.argmax(dim="twtt")
    peak_twtt = power_dB.twtt[peak_twtt_index]
    peak_power = power_dB.isel(twtt=peak_twtt_index)

    peak_twtt = peak_twtt.drop_vars("twtt")
    peak_power = peak_power.drop_vars("twtt")

    return peak_twtt, peak_power


def _build_qc_checks(qc_config: dict) -> dict:
    """Build xopr QC checks dict from config. Only includes enabled (non-null) checks."""
    checks = {}

    val = qc_config.get("max_heading_change_deg_per_km")
    if val is not None:
        checks["heading_change"] = {"max_deg_per_km": val}

    val = qc_config.get("min_ice_thickness_m")
    if val is not None:
        checks["ice_thickness_threshold"] = {"min_thickness_m": val}

    val = qc_config.get("min_agl_m")
    if val is not None:
        checks["minimum_agl"] = {"min_agl_m": val}

    val = qc_config.get("min_bed_snr_db")
    if val is not None:
        checks["snr_bed_pick"] = {"min_snr_db": val}

    return checks


def process_frame(opr: OPRConnection, stac_item, config: dict) -> xr.Dataset | None:
    """Process a single radar frame and return a Dataset of metrics, or None on failure."""
    proc = config["processing"]
    qc_config = config.get("qc", {})
    frame_id = stac_item.name if hasattr(stac_item, "name") else stac_item.get("id", "unknown")

    try:
        frame = opr.load_frame(stac_item, data_product=proc["data_product"])
        frame = frame.sortby("slow_time")

        decimate_interval = proc.get("decimate_interval")
        if decimate_interval:
            interval = pd.Timedelta(decimate_interval)
            times = frame.slow_time.values
            selected = [0]
            last = times[0]
            for idx in range(1, len(times)):
                if times[idx] - last >= interval:
                    selected.append(idx)
                    last = times[idx]
            frame = frame.isel(slow_time=selected)

        try:
            layers = opr.get_layers(frame, include_geometry=False)
        except Exception:
            logger.warning("Frame %s: failed to load layers, skipping", frame_id)
            return None

        surface_key = bed_key = None
        if layers is not None:
            surface_key = SURFACE_KEY if SURFACE_KEY in layers else (
                SURFACE_FALLBACK_KEY if SURFACE_FALLBACK_KEY in layers else None
            )
            bed_key = BED_KEY if BED_KEY in layers else (
                BED_FALLBACK_KEY if BED_FALLBACK_KEY in layers else None
            )
        if layers is None or surface_key is None or bed_key is None:
            available = list(layers.keys()) if layers else []
            logger.warning("Frame %s: missing layer picks (available: %s), skipping",
                           frame_id, available)
            return None
        if surface_key != SURFACE_KEY:
            logger.info("Frame %s: using surface layer %r (no %r)", frame_id, surface_key, SURFACE_KEY)
        if bed_key != BED_KEY:
            logger.info("Frame %s: using bed layer %r (no %r)", frame_id, bed_key, BED_KEY)

        # Segment-level picking effort: a bed layer with zero finite picks is
        # equivalent to no bed layer (picking never attempted) — skip so these
        # flights can't contaminate downstream missingness estimates.
        seg_bed_twtt = np.asarray(layers[bed_key]["twtt"].values, dtype=float)
        segment_bed_pick_fraction = float(np.isfinite(seg_bed_twtt).mean()) if seg_bed_twtt.size else 0.0
        if segment_bed_pick_fraction == 0.0:
            logger.warning("Frame %s: bed layer present but contains no picks, skipping", frame_id)
            return None

        # Add layer picks to frame so xopr QC checks can use them. The bed pick is
        # always stored under BED_KEY regardless of source key, since xopr QC
        # checks look it up by that name.
        for frame_key, layer_key in ((SURFACE_KEY, surface_key), (BED_KEY, bed_key)):
            pick = layers[layer_key]["twtt"].reindex(
                slow_time=frame.slow_time,
                method="nearest",
                tolerance=pd.Timedelta(seconds=5),
                fill_value=np.nan,
            )
            frame[frame_key] = pick

        # Pre-QC bed pick availability at the extraction tolerance — the ground
        # truth for "was a bed pick present for this trace".
        align_tol = pd.Timedelta(seconds=PICK_ALIGN_TOLERANCE_S)
        bed_pick_aligned = layers[bed_key]["twtt"].reindex(
            slow_time=frame.slow_time, method="nearest",
            tolerance=align_tol, fill_value=np.nan,
        )
        bed_pick_available = xr.DataArray(
            np.isfinite(np.asarray(bed_pick_aligned.values, dtype=float)),
            dims=("slow_time",), coords={"slow_time": frame.slow_time},
        )

        # Picking-attempted span: traces before the segment's first finite bed
        # pick or after its last are excluded from the missingness signal —
        # leading/trailing gaps are usually segment-edge quirks, not low SNR.
        seg_pick_times = layers[bed_key]["twtt"].slow_time.values[np.isfinite(seg_bed_twtt)]
        st = frame.slow_time.values
        attempted = (st >= seg_pick_times.min()) & (st <= seg_pick_times.max())
        attempted |= bed_pick_available.values  # available always implies attempted
        bed_pick_attempted = xr.DataArray(
            attempted, dims=("slow_time",), coords={"slow_time": frame.slow_time},
        )

        # OPR per-point quality flag (1 good / 2 moderate / 3 derived) where the
        # layer source provides it; -1 where no pick or flag unavailable.
        quality_vals = np.full(len(frame.slow_time), -1, dtype=np.int8)
        try:
            quality_src = layers[bed_key]["quality"]
            q = quality_src.reindex(
                slow_time=frame.slow_time, method="nearest",
                tolerance=align_tol, fill_value=np.nan,
            ).values.astype(float)
            valid = np.isfinite(q) & bed_pick_available.values
            quality_vals[valid] = q[valid].astype(np.int8)
        except (KeyError, TypeError):
            pass
        bed_pick_quality = xr.DataArray(
            quality_vals, dims=("slow_time",), coords={"slow_time": frame.slow_time},
        )

        # Run xopr QC checks (picks already in frame, ensure_picks is a no-op).
        # Pick-independent checks (heading, AGL) gate surface metrics and frame
        # retention; the full combined mask additionally gates bed metrics.
        all_true = xr.DataArray(
            np.ones(len(frame.slow_time), dtype=bool),
            dims=("slow_time",), coords={"slow_time": frame.slow_time},
        )
        qc_checks = _build_qc_checks(qc_config)
        if qc_checks:
            frame = xopr_qc.run_qc(frame, checks=qc_checks)
            qc_mask = frame["qc"]
            qc_heading_pass = frame["qc_heading_change"] if "qc_heading_change" in frame else all_true
            qc_agl_pass = frame["qc_minimum_agl"] if "qc_minimum_agl" in frame else all_true
            qc_surface = qc_heading_pass & qc_agl_pass

            n_pass = int(qc_surface.sum())
            n_total = len(qc_surface)
            min_traces = qc_config.get("min_traces_after_qc", 10)
            if n_pass < min_traces:
                logger.warning(
                    "Frame %s: only %d/%d traces pass pick-independent QC (need %d), skipping",
                    frame_id, n_pass, n_total, min_traces)
                return None
            if int(qc_mask.sum()) < n_total:
                logger.info("Frame %s: QC filtered %d/%d traces",
                            frame_id, n_total - int(qc_mask.sum()), n_total)
        else:
            qc_mask = None
            qc_heading_pass = qc_agl_pass = qc_surface = all_true

        ice_permittivity = proc["ice_permittivity"]
        c = scipy.constants.c
        v_ice = c / np.sqrt(ice_permittivity)
        margin_twtt = proc["layer_margin_m"] / v_ice

        surface_twtt, surface_power = extract_layer_peak_power(
            frame, layers[surface_key]["twtt"], margin_twtt
        )
        bed_twtt, bed_power = extract_layer_peak_power(
            frame, layers[bed_key]["twtt"], margin_twtt
        )
        # Bed metrics are only defined where a pick exists (extraction can
        # otherwise land on spurious peaks for pickless traces).
        bed_twtt = bed_twtt.where(bed_pick_available)
        bed_power = bed_power.where(bed_pick_available)

        surface_elevation = frame.Elevation - (c / 2) * surface_twtt
        bed_elevation = surface_elevation - (v_ice / 2) * (bed_twtt - surface_twtt)

        # Required surface SNR: surface-to-bed power ratio corrected for geometric spreading.
        # Matches the RSSNR definition from https://github.com/thomasteisberg/required_surface_snr
        required_surface_snr_dB = compute_rssnr_dB(
            surface_power, bed_power, surface_twtt, bed_twtt, ice_permittivity
        )

        # Noise power in pre-surface and post-bed windows. Uses the layer-pick
        # twtts already aligned to frame.slow_time as the window anchors.
        noise_config = proc.get("noise", {})
        pre_noise_arr, post_noise_arr = compute_noise_powers(
            frame, frame[SURFACE_KEY].values, frame[BED_KEY].values, noise_config,
        )
        pre_surface_noise_dB = xr.DataArray(
            pre_noise_arr, dims=("slow_time",),
            coords={"slow_time": frame.slow_time},
        )
        post_bed_noise_dB = xr.DataArray(
            post_noise_arr, dims=("slow_time",),
            coords={"slow_time": frame.slow_time},
        )
        record_tail_noise_dB = xr.DataArray(
            compute_record_tail_noise(frame, noise_config), dims=("slow_time",),
            coords={"slow_time": frame.slow_time},
        )

        # Post-bed window metrics anchored at the pick where present, else a
        # bed twtt linearly interpolated (in slow_time) between the segment's
        # adjacent picks — defined on censored traces inside the picked span.
        seg_t = layers[bed_key]["twtt"].slow_time.values.astype("datetime64[ns]").astype(np.int64)
        finite_seg = np.isfinite(seg_bed_twtt)
        ft = seg_t[finite_seg].astype(float)
        fv = seg_bed_twtt[finite_seg]
        order = np.argsort(ft)
        ft, fv = ft[order], fv[order]
        trace_t = frame.slow_time.values.astype("datetime64[ns]").astype(np.int64).astype(float)
        interp_anchor = np.interp(trace_t, ft, fv)
        interp_anchor[(trace_t < ft[0]) | (trace_t > ft[-1])] = np.nan
        pick5 = np.asarray(frame[BED_KEY].values, dtype=float)
        anchor = np.where(np.isfinite(pick5), pick5, interp_anchor)

        interp_med, interp_peak, interp_std = compute_interp_bed_window_metrics(
            frame, anchor, noise_config
        )
        as_da = lambda arr: xr.DataArray(
            arr, dims=("slow_time",), coords={"slow_time": frame.slow_time}
        )
        post_bed_noise_interp_dB = as_da(interp_med)
        post_bed_peak_interp_dB = as_da(interp_peak)
        post_bed_std_interp_dB = as_da(interp_std)

        # Last twtt sample of the record — lets users reconstruct the windows
        # defined relative to the record end (post-bed, record tail). Recording
        # metadata, so never QC-masked.
        record_end_twtt = as_da(np.full(len(frame.slow_time), float(frame.twtt.values[-1])))

        qc_pass = qc_mask if qc_mask is not None else all_true

        # Surface-side metrics are masked only by pick-independent QC so that
        # traces missing a bed pick (or failing thin-ice / bed-SNR checks) keep
        # the surface power and noise floor needed to treat the missing bed as
        # a censored observation. Bed-side metrics use the full QC mask.
        surface_side = {
            "surface_twtt": surface_twtt,
            "surface_elevation": surface_elevation,
            "surface_power_dB": surface_power,
            "pre_surface_noise_dB": pre_surface_noise_dB,
            "record_tail_noise_dB": record_tail_noise_dB,
            "post_bed_noise_interp_dB": post_bed_noise_interp_dB,
            "post_bed_peak_interp_dB": post_bed_peak_interp_dB,
            "post_bed_std_interp_dB": post_bed_std_interp_dB,
        }
        bed_side = {
            "bed_twtt": bed_twtt,
            "bed_elevation": bed_elevation,
            "bed_power_dB": bed_power,
            "required_surface_snr_dB": required_surface_snr_dB,
            "post_bed_noise_dB": post_bed_noise_dB,
        }
        if qc_mask is not None:
            surface_side = {k: v.where(qc_surface) for k, v in surface_side.items()}
            bed_side = {k: v.where(qc_pass) for k, v in bed_side.items()}

        ds = xr.Dataset(
            {
                **surface_side,
                **bed_side,
                "qc_pass": qc_pass,
                "qc_surface_pass": qc_surface,
                "qc_heading_pass": qc_heading_pass,
                "qc_agl_pass": qc_agl_pass,
                "bed_pick_available": bed_pick_available,
                "bed_pick_attempted": bed_pick_attempted,
                "bed_pick_quality": bed_pick_quality,
                "record_end_twtt": record_end_twtt,
                "frame_id": ("slow_time", [str(frame_id)] * len(frame.slow_time)),
            },
            coords={
                "latitude": frame.Latitude,
                "longitude": frame.Longitude,
            },
        )
        ds.attrs["frame_bed_pick_fraction"] = float(bed_pick_available.values.mean())
        ds.attrs["segment_bed_pick_fraction"] = segment_bed_pick_fraction
        if "Elevation" in frame:
            ds.coords["elevation"] = frame.Elevation

        # Stash the collection (e.g. 2018_Greenland_P3) so the runner can build
        # a frame_id -> collection mapping for the viewer.
        if hasattr(stac_item, "get"):
            collection = stac_item.get("collection")
        else:
            collection = getattr(stac_item, "collection", None)
        if collection:
            ds.attrs["collection"] = str(collection)

        n_qc_pass = int(qc_pass.sum())
        logger.info("Frame %s: processed successfully (%d traces, %d pass QC)",
                    frame_id, len(ds.slow_time), n_qc_pass)
        return ds

    except Exception:
        logger.exception("Frame %s: processing failed", frame_id)
        return None
