import hashlib
import logging

import numpy as np
import icechunk
import xarray as xr
import zarr
from xarray.coding.times import encode_cf_datetime

logger = logging.getLogger(__name__)


# Attribute templates for the calibration variables, applied wherever the
# arrays are created (fresh store writes, append auto-migration, backfill).
CALIBRATION_VAR_ATTRS = {
    "img_comb_offset_dB": {
        "units": "dB",
        "description": (
            "Residual image-combine seam offset of the frame's worst image "
            "pair — the power step present in the combined product at the "
            "combine boundary, measured on the weight-corrected individual "
            "images in their overlap. Positive = the earlier (lower-index, "
            "shallower) image is brighter than the later one. NaN = "
            "unmeasured; see frame_img_comb_status. Suggested downstream "
            "rejection threshold ~3 dB."),
    },
    "img_comb_pair": {
        "description": (
            "Image pair img_comb_offset_dB refers to (1 = img1/img2, "
            "2 = img2/img3), chosen once per frame as the pair with the "
            "largest |mean offset|; -1 = undefined."),
    },
    "surface_source_image_index": {
        "description": (
            "Image that supplied the combined product's surface sample "
            "(1 = low-gain img1; >=2 = a higher-gain image; -1 = unknown). "
            "Provenance flag, not a validity verdict: higher-gain-sourced "
            "surfaces are more likely saturated and may carry a "
            "season-dependent low bias (see the season's cross_cap_step_db), "
            "but for high-altitude operations they are the normal geometry."),
    },
    "surface_ceiling_margin_dB": {
        "units": "dB",
        "description": (
            "Season ceiling of the trace's surface-source population minus "
            "surface_power_dB. Small/zero => at the clip ceiling (likely "
            "saturated). NaN => no credible ceiling was fitted for that "
            "population (a clean season has no ceiling), or power undefined."),
    },
}


def apply_calibration_var_attrs(root: zarr.Group) -> int:
    """Stamp the calibration attribute templates onto existing arrays that
    lack them. Returns the number of arrays updated."""
    n = 0
    for name, attrs in CALIBRATION_VAR_ATTRS.items():
        if name in root and not dict(root[name].attrs):
            root[name].attrs.update(attrs)
            n += 1
    return n


def _fill_value_for(dtype: np.dtype):
    """Fill value used when backfilling a variable that predates the store."""
    dtype = np.dtype(dtype)
    if np.issubdtype(dtype, np.floating):
        return np.nan
    if np.issubdtype(dtype, np.bool_):
        return False
    if np.issubdtype(dtype, np.signedinteger):
        return -1
    if dtype.kind in ("U", "S"):
        return ""
    return 0

# Per-trace zarr arrays are appended to over the lifetime of the store. xarray's
# default chunking on first write is the length of the first frame (~38 traces),
# which makes the viewer fire thousands of HTTP requests per variable. Force a
# sensible chunk size up front. Existing stores can be migrated with
# scripts/migrations/rechunk_per_trace_arrays.py.
PER_TRACE_CHUNK_SIZE = 10000


def make_storage(store_config: dict) -> icechunk.Storage:
    """Create icechunk Storage from config (local or S3)."""
    backend = store_config.get("backend", "local")
    if backend == "s3":
        return icechunk.s3_storage(
            bucket=store_config["s3_bucket"],
            prefix=store_config.get("s3_prefix"),
            region=store_config.get("s3_region"),
            from_env=True,
        )
    else:
        return icechunk.local_filesystem_storage(str(store_config["path"]))


def open_or_create_repo(store_config: dict) -> icechunk.Repository:
    """Open an existing icechunk repo or create a new one."""
    storage = make_storage(store_config)
    try:
        repo = icechunk.Repository.open(storage=storage)
        logger.info("Opened existing icechunk repo")
    except Exception:
        repo = icechunk.Repository.create(storage=storage)
        logger.info("Created new icechunk repo")
    return repo


def get_processed_frames(repo: icechunk.Repository) -> set[str]:
    """Get set of already-processed frame IDs from the store."""
    try:
        session = repo.readonly_session(branch="main")
        store = session.store
        root = zarr.open_group(store, mode="r")
        if "processed_frames" in root:
            return set(root["processed_frames"][:].tolist())
    except Exception:
        pass
    return set()


def _zarr_append(root: zarr.Group, ds: xr.Dataset) -> None:
    """Append dataset to existing zarr group using zarr directly.

    Bypasses xarray's to_zarr(append_dim=...) which fails when the existing
    store has CF-time-encoded slow_time that xarray can't decode.
    """
    n_new = len(ds.slow_time)

    # Collect all slow_time-dimensioned arrays
    arrays: dict[str, np.ndarray] = {}
    for name in ds.data_vars:
        if ds[name].dims == ("slow_time",):
            arrays[name] = ds[name].values
    for name in ds.coords:
        if name != "slow_time" and hasattr(ds.coords[name], "dims") and ds.coords[name].dims == ("slow_time",):
            arrays[name] = ds.coords[name].values

    # Encode slow_time to match the existing store's CF encoding
    st_arr = root["slow_time"]
    units = st_arr.attrs.get("units", "seconds since 1970-01-01")
    calendar = st_arr.attrs.get("calendar", "proleptic_gregorian")
    encoded_times, _, _ = encode_cf_datetime(ds.slow_time.values, units=units, calendar=calendar)
    arrays["slow_time"] = np.asarray(encoded_times)

    existing_size = st_arr.shape[0]
    for name, data in arrays.items():
        if name not in root:
            # Auto-migrate: a variable the store predates is created sized to
            # the current store, backfilled with its fill value, then appended
            # to like any other — never silently dropped.
            fill = _fill_value_for(data.dtype)
            logger.warning(
                "Store migration: creating missing variable %r (%s), backfilling "
                "%d existing traces with %r", name, data.dtype, existing_size, fill)
            root.create_array(name, shape=(existing_size,), dtype=data.dtype,
                              chunks=(PER_TRACE_CHUNK_SIZE,), fill_value=fill)
            if name in CALIBRATION_VAR_ATTRS:
                root[name].attrs.update(CALIBRATION_VAR_ATTRS[name])
        arr = root[name]
        old_size = arr.shape[0]
        arr.resize(old_size + n_new)
        arr[old_size:] = data


def write_frame_results(
    session: icechunk.Session,
    frame_id: str,
    results_ds: xr.Dataset,
) -> None:
    """Write frame results to icechunk store, appending along slow_time dimension."""
    store = session.store
    root = zarr.open_group(store, mode="a")
    first_write = "surface_twtt" not in root

    if first_write:
        encoding = {
            name: {"chunks": (PER_TRACE_CHUNK_SIZE,)}
            for name in (*results_ds.data_vars, *results_ds.coords)
            if "slow_time" in results_ds[name].dims
        }
        results_ds.to_zarr(store, mode="w", encoding=encoding)
    else:
        _zarr_append(root, results_ds)

    # Track processed frame
    if "processed_frames" not in root:
        root.create_array(
            "processed_frames",
            data=np.array([frame_id], dtype="U100"),
            chunks=(1000,),
        )
    else:
        existing = root["processed_frames"]
        new_size = existing.shape[0] + 1
        existing.resize(new_size)
        existing[new_size - 1] = frame_id


def remove_frames(session: icechunk.Session, frame_ids_to_remove: set[str]) -> int:
    """Remove all traces for the given frame IDs from the store.

    Rewrites all trace-indexed arrays in-place (keeping attributes) and updates
    the processed_frames index. Returns the number of traces removed.
    """
    store_obj = session.store
    root = zarr.open_group(store_obj, mode="a")

    if "frame_id" not in root:
        return 0

    frame_ids_arr = root["frame_id"][:]
    keep_mask = np.array([fid not in frame_ids_to_remove for fid in frame_ids_arr])
    n_removed = int((~keep_mask).sum())

    if n_removed > 0:
        n_total = len(frame_ids_arr)
        for key in list(root.keys()):
            arr = root[key]
            if not isinstance(arr, zarr.Array) or not arr.shape or arr.shape[0] != n_total:
                continue
            attrs = dict(arr.attrs)
            filtered = arr[:][keep_mask]
            root.create_array(key, data=filtered, chunks=arr.chunks, overwrite=True)
            root[key].attrs.update(attrs)

    if "processed_frames" in root:
        existing = root["processed_frames"][:]
        remaining = np.array([f for f in existing if f not in frame_ids_to_remove], dtype="U100")
        root.create_array("processed_frames", data=remaining, chunks=(1000,), overwrite=True)

    return n_removed


def clear_store(session: icechunk.Session) -> None:
    """Clear all data and frame tracking for a full reprocess."""
    store = session.store
    root = zarr.open_group(store, mode="a")
    for key in list(root.keys()):
        del root[key]


def update_frame_index(
    session: icechunk.Session,
    frame_collections: dict[str, str] | None = None,
    frame_scalar_attrs: dict[str, dict[str, object]] | None = None,
) -> None:
    """Rebuild frame_index (uint16 per trace) and frame_names root attribute from frame_id.

    If ``frame_collections`` is given (mapping frame_id -> collection name), it
    is unioned into the existing ``frame_collections`` root attribute (parallel
    to ``frame_names``). The viewer uses this to show full collection names —
    parsing the year from the frame id alone is ambiguous when multiple
    collections share a year.

    ``frame_scalar_attrs`` maps root-attribute names to per-frame value
    mappings (frame_id -> value), each maintained as a list parallel to
    ``frame_names`` (``None`` for frames with no recorded value). Used for
    e.g. ``frame_bed_pick_fraction`` / ``segment_bed_pick_fraction``.
    """
    store = session.store
    root = zarr.open_group(store, mode="a")

    if "frame_id" not in root:
        return

    # Snapshot previous frame -> collection mapping before we overwrite frame_names.
    prev_names = list(root.attrs.get("frame_names", []) or [])
    prev_cols = list(root.attrs.get("frame_collections", []) or [])
    prev_mapping: dict[str, str] = dict(zip(prev_names, prev_cols))

    frame_ids = root["frame_id"][:]

    seen: dict[str, int] = {}
    frame_names: list[str] = []
    for fid in frame_ids:
        if fid not in seen:
            seen[fid] = len(frame_names)
            frame_names.append(fid)

    assert len(frame_names) <= 65535, "Too many frames for uint16"

    frame_index = np.array([seen[fid] for fid in frame_ids], dtype="uint16")
    root.create_array("frame_index", data=frame_index, chunks=(10000,), overwrite=True)
    root.attrs["frame_names"] = frame_names

    # Maintain frame_collections parallel to frame_names. Merge whatever new
    # entries we were given with the previous mapping so partial inputs don't
    # drop collection info for older frames.
    if frame_collections or prev_mapping:
        if frame_collections:
            prev_mapping.update(frame_collections)
        root.attrs["frame_collections"] = [prev_mapping.get(name, "") for name in frame_names]

    # Scalar per-frame attributes, same merge semantics as frame_collections.
    # (The first to_zarr write copies the first frame's scalar attrs onto the
    # root group; anything that isn't a list is that artifact, not our index.)
    for attr_name, new_values in (frame_scalar_attrs or {}).items():
        prev_raw = root.attrs.get(attr_name, [])
        prev_vals = list(prev_raw) if isinstance(prev_raw, (list, tuple)) else []
        prev_map = dict(zip(prev_names, prev_vals))
        prev_map.update(new_values)
        if prev_map:
            root.attrs[attr_name] = [prev_map.get(name) for name in frame_names]

    logger.info("Updated frame_index: %d traces, %d unique frames", len(frame_ids), len(frame_names))


def commit_session(session: icechunk.Session, message: str) -> str:
    """Commit the session and return the snapshot ID."""
    snapshot_id = session.commit(message)
    logger.info("Committed: %s (snapshot: %s)", message, snapshot_id)
    return snapshot_id


def science_fingerprint(root: zarr.Group) -> dict:
    """Fingerprint of the science data: trace count + hash of the processed
    frame set. Science appends/removals/reprocessing change it; calibration-only
    writes (saturation second pass, backfill) do not — so second-pass staleness
    can be judged without being invalidated by its own commit."""
    n = int(root["slow_time"].shape[0]) if "slow_time" in root else 0
    frames = sorted(root["processed_frames"][:].tolist()) if "processed_frames" in root else []
    digest = hashlib.sha256("\n".join(frames).encode()).hexdigest()
    return {"n_traces": n, "frames_sha256": digest}


def saturation_stale(root: zarr.Group) -> bool | None:
    """True if the stored saturation second-pass results predate the current
    science data OR were explicitly marked stale (e.g. a calibration backfill
    rewrote surface_source_image_index, a fit input); None if no second pass
    has been run."""
    meta = root.attrs.get("saturation")
    if not meta:
        return None
    if meta.get("stale"):
        return True
    return meta.get("fingerprint") != science_fingerprint(root)


def mark_saturation_stale(root: zarr.Group) -> None:
    """Flag existing saturation results as stale (cleared by the next
    write_saturation_results). No-op when no second pass has run yet."""
    meta = root.attrs.get("saturation")
    if meta and not meta.get("stale"):
        root.attrs["saturation"] = {**meta, "stale": True}


def write_saturation_results(
    session: icechunk.Session,
    margins: np.ndarray,
    saturation_attrs: dict,
) -> None:
    """Write second-pass results: per-trace surface_ceiling_margin_dB plus the
    root `saturation` attr (season fits, params, method version), stamped with
    the current science fingerprint."""
    root = zarr.open_group(session.store, mode="a")
    n = root["slow_time"].shape[0]
    margins = np.asarray(margins, dtype=np.float32)
    if margins.shape != (n,):
        raise ValueError(f"margins shape {margins.shape} != store traces ({n},)")
    attrs = dict(CALIBRATION_VAR_ATTRS["surface_ceiling_margin_dB"])
    if "surface_ceiling_margin_dB" in root:
        attrs.update(dict(root["surface_ceiling_margin_dB"].attrs))
    root.create_array("surface_ceiling_margin_dB", data=margins,
                      chunks=(PER_TRACE_CHUNK_SIZE,), overwrite=True)
    root["surface_ceiling_margin_dB"].attrs.update(attrs)
    root.attrs["saturation"] = {**saturation_attrs,
                                "fingerprint": science_fingerprint(root)}


def update_frame_calibration(
    session: icechunk.Session,
    frame_id: str,
    per_trace: dict[str, np.ndarray],
    frame_attrs: dict[str, object],
) -> int:
    """Rewrite one frame's calibration values in place (backfill of retryable
    statuses) without touching science metrics. Returns traces updated (0 if
    the frame is absent or ANY array's trace count mismatches — nothing is
    written in that case, so the frame stays retryable). Marks existing
    saturation results stale when surface_source_image_index (a saturation
    fit input) is rewritten."""
    root = zarr.open_group(session.store, mode="a")
    if "frame_id" not in root:
        return 0
    idx = np.where(root["frame_id"][:] == frame_id)[0]
    if idx.size == 0:
        return 0
    # All-or-nothing: validate every array before any mutation. A partial
    # write recorded as success would drop the frame from retry selection.
    per_trace = {name: np.asarray(vals) for name, vals in per_trace.items()}
    for name, vals in per_trace.items():
        if vals.shape != idx.shape:
            logger.warning("Backfill %s: %s has %d values for %d traces; frame "
                           "left unwritten and retryable",
                           frame_id, name, vals.size, idx.size)
            return 0
    n_total = root["frame_id"].shape[0]
    for name, vals in per_trace.items():
        if name not in root:
            fill = _fill_value_for(vals.dtype)
            root.create_array(name, shape=(n_total,), dtype=vals.dtype,
                              chunks=(PER_TRACE_CHUNK_SIZE,), fill_value=fill)
            if name in CALIBRATION_VAR_ATTRS:
                root[name].attrs.update(CALIBRATION_VAR_ATTRS[name])
        arr = root[name]
        if idx.size and np.all(np.diff(idx) == 1):
            arr[idx[0]: idx[-1] + 1] = vals
        else:
            arr.oindex[idx] = vals

    frame_names = list(root.attrs.get("frame_names", []) or [])
    if frame_id in frame_names:
        pos = frame_names.index(frame_id)
        for attr_name, value in frame_attrs.items():
            prev = root.attrs.get(attr_name)
            lst = list(prev) if isinstance(prev, (list, tuple)) else [None] * len(frame_names)
            lst += [None] * (len(frame_names) - len(lst))
            lst[pos] = value
            root.attrs[attr_name] = lst
    if "surface_source_image_index" in per_trace:
        mark_saturation_stale(root)
    return int(idx.size)
