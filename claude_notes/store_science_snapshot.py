"""Snapshot / verify a store's science arrays.

  uv run python claude_notes/store_science_snapshot.py snapshot <store_path> <out.json>
  uv run python claude_notes/store_science_snapshot.py verify   <store_path> <out.json>

snapshot: records sha256 of every array's bytes (NaNs included) + attrs.
verify: recomputes and reports, per array: unchanged / MODIFIED / new.
Calibration additions are expected as 'new' or attr additions; anything
MODIFIED is a failure. Used to prove backfill + second pass touched nothing
but the new fields.
"""
import hashlib
import json
import sys

import icechunk
import numpy as np
import zarr

CALIB_VARS = {"img_comb_offset_dB", "img_comb_pair",
              "surface_source_image_index", "surface_ceiling_margin_dB",
              "surface_peak_width_us"}
CALIB_ATTRS_PREFIXES = ("frame_img_comb", "saturation", "calibration_")


def open_root(path):
    storage = icechunk.local_filesystem_storage(path)
    repo = icechunk.Repository.open(storage=storage)
    return zarr.open_group(repo.readonly_session(branch="main").store, mode="r")


def array_hash(arr):
    a = arr[:]
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def main():
    mode, path, out = sys.argv[1], sys.argv[2], sys.argv[3]
    root = open_root(path)
    current = {k: array_hash(root[k]) for k in sorted(root.array_keys())}
    attrs = root.attrs.asdict()
    non_calib_attrs = {
        k: v for k, v in attrs.items()
        if not any(k.startswith(p) for p in CALIB_ATTRS_PREFIXES)
    }
    attrs_hash = hashlib.sha256(
        json.dumps(non_calib_attrs, sort_keys=True, default=str).encode()
    ).hexdigest()

    if mode == "snapshot":
        with open(out, "w") as f:
            json.dump({"arrays": current, "attrs_hash": attrs_hash}, f, indent=1)
        print(f"snapshot: {len(current)} arrays, attrs hash {attrs_hash[:12]}")
        return

    with open(out) as f:
        base = json.load(f)
    fail = False
    for k, h in base["arrays"].items():
        if k not in current:
            print(f"MISSING array: {k}")
            fail = True
        elif current[k] != h:
            if k in CALIB_VARS:
                print(f"calibration array changed (expected): {k}")
            else:
                print(f"MODIFIED science array: {k}")
                fail = True
    new = [k for k in current if k not in base["arrays"]]
    print(f"new arrays: {sorted(new)}")
    unexpected_new = [k for k in new if k not in CALIB_VARS]
    if unexpected_new:
        print(f"UNEXPECTED new arrays: {unexpected_new}")
        fail = True
    if attrs_hash != base["attrs_hash"]:
        print("MODIFIED non-calibration attrs")
        fail = True
    n_unchanged = sum(1 for k, h in base["arrays"].items() if current.get(k) == h)
    print(f"{n_unchanged}/{len(base['arrays'])} original arrays unchanged")
    print("RESULT:", "FAIL" if fail else "PASS — science data untouched; only calibration fields added")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
