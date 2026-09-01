"""Probe img_01 file availability for every season in the production stores.

Answers: how often would the img-overlap check be impossible (fallback needed)?
Uses HTTP HEAD against the public OPR site; no data downloads.
"""
import icechunk
import zarr
import numpy as np
import requests
from xopr import OPRConnection

seasons = set()
for prefix in ["antarctica", "greenland", "ase", "utig", "crosssystem"]:
    storage = icechunk.s3_storage(
        bucket="opr-radar-metrics", prefix=f"icechunk/{prefix}",
        region="us-west-2", anonymous=True,
    )
    repo = icechunk.Repository.open(storage=storage)
    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")
    colls = root.attrs.asdict().get("frame_collections")
    if colls is None:
        print(f"{prefix}: no frame_collections attr, skipping")
        continue
    seasons.update(colls)
print(f"{len(seasons)} unique seasons\n")

opr = OPRConnection()
for coll in sorted(seasons):
    try:
        frames = opr.query_frames(collections=[coll], max_items=3)
    except Exception as e:
        print(f"{coll}: query failed ({type(e).__name__})")
        continue
    statuses = []
    for fid in frames.index[:2]:
        # Data_20161014_03_001 -> segment 20161014_03
        parts = fid.replace("Data_", "").rsplit("_", 1)
        seg = parts[0]
        url = (f"https://data.cresis.ku.edu/data/rds/{coll}/CSARP_standard/"
               f"{seg}/Data_img_01_{fid.replace('Data_', '')}.mat")
        try:
            r = requests.head(url, timeout=30, allow_redirects=True)
            statuses.append(str(r.status_code))
        except Exception as e:
            statuses.append(type(e).__name__)
    print(f"{coll}: img_01 HEAD -> {statuses}")
