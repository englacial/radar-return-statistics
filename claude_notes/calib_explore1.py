"""Explore loading individual images + metadata for radiometric calibration checks."""
import numpy as np
from xopr import OPRConnection

opr = OPRConnection()
frames = opr.query_frames(collections=["2016_Antarctica_DC8"], max_items=5)
print(frames.index.tolist())
item = frames.iloc[0]
print("frame:", item.name, "collection:", item.get("collection"))

ds = opr.load_frame(item, data_product="CSARP_standard")
print("\n=== combined CSARP_standard ===")
print(ds)
print("\nattrs keys:", list(ds.attrs.keys()))
for k, v in ds.attrs.items():
    s = str(v)
    print(f"  {k}: {s[:200]}")
