"""Survey img_comb_weights / img_comb_weights_mode across all store seasons.

Loads one combined frame per season and searches every param_* attr dict for
weights-related fields.
"""
import numpy as np
import icechunk
import zarr
from xopr import OPRConnection

seasons = set()
for prefix in ["antarctica", "greenland", "ase", "utig"]:
    storage = icechunk.s3_storage(
        bucket="opr-radar-metrics", prefix=f"icechunk/{prefix}",
        region="us-west-2", anonymous=True,
    )
    repo = icechunk.Repository.open(storage=storage)
    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")
    colls = root.attrs.asdict().get("frame_collections")
    if colls:
        seasons.update(colls)


def find_fields(d, needle, prefix="", depth=0, out=None):
    if out is None:
        out = []
    if depth > 5 or not isinstance(d, dict):
        return out
    for k, v in d.items():
        if isinstance(v, dict):
            find_fields(v, needle, prefix + k + ".", depth + 1, out)
        elif needle in k.lower():
            out.append((prefix + k, v))
    return out


opr = OPRConnection()
for coll in sorted(seasons):
    try:
        frames = opr.query_frames(collections=[coll], max_items=1)
        ds = opr.load_frame(frames.iloc[0], data_product="CSARP_standard")
    except Exception as e:
        print(f"{coll}: load failed ({type(e).__name__}: {str(e)[:80]})")
        continue
    hits = []
    for pk in [k for k in ds.attrs if k.startswith("param")]:
        for path, v in find_fields(ds.attrs[pk], "img_comb_weight"):
            s = str(v).replace("\n", " ")[:80]
            hits.append(f"{pk}.{path}={s}")
    if hits:
        # dedupe identical values
        print(f"{coll} ({frames.index[0]}):")
        for h in sorted(set(hits)):
            print(f"    {h}")
    else:
        print(f"{coll} ({frames.index[0]}): no img_comb_weights fields")
