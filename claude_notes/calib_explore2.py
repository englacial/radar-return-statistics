"""Dig into param_combine img_comb metadata and load individual images."""
import numpy as np
from xopr import OPRConnection

opr = OPRConnection()
frames = opr.query_frames(collections=["2016_Antarctica_DC8"], max_items=5)
item = frames.iloc[0]

ds = opr.load_frame(item, data_product="CSARP_standard")
pc = ds.attrs["param_combine"]

def walk(d, prefix="", depth=0):
    if depth > 3:
        return
    for k, v in d.items():
        if isinstance(v, dict):
            walk(v, prefix + k + ".", depth + 1)
        else:
            s = str(v).replace("\n", " ")
            if any(t in k.lower() for t in ("img", "comb", "wf", "gain", "tukey", "tpd", "blank")):
                print(f"{prefix}{k} = {s[:250]}")

print("=== param_combine (img/comb/gain-related fields) ===")
walk(pc)

# waveform params (pulse durations Tpd) live in param_records.radar.wfs usually
pr = ds.attrs["param_records"]
print("\n=== param_records radar keys ===")
print(list(pr.get("radar", {}).keys()))
wfs = pr.get("radar", {}).get("wfs")
print("wfs:", str(wfs)[:500])

print("\n=== Try loading individual images ===")
for img in [1, 2, 3]:
    try:
        dsi = opr.load_frame(item, data_product="CSARP_standard", image=img,
                             allow_unlisted_products=True)
        print(f"img {img}: twtt range {dsi.twtt.values[0]*1e6:.2f}-{dsi.twtt.values[-1]*1e6:.2f} us, "
              f"shape {dsi.Data.shape}")
    except Exception as e:
        print(f"img {img}: FAILED {type(e).__name__}: {str(e)[:150]}")
