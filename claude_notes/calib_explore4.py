"""Check individual-image availability across seasons/systems + img_comb params."""
import numpy as np
from xopr import OPRConnection

opr = OPRConnection()

tests = [
    ("2014_Greenland_P3", "CSARP_standard"),
    ("2013_Greenland_P3", "CSARP_standard"),
    ("2018_Greenland_P3", "CSARP_standard"),
    ("2019_Antarctica_GV", "CSARP_standard"),
    ("2022_Antarctica_BaslerMKB", "CSARP_standard"),
]

for coll, prod in tests:
    try:
        frames = opr.query_frames(collections=[coll], max_items=2)
        item = frames.iloc[0]
        ds = opr.load_frame(item, data_product=prod)
        pc = ds.attrs.get("param_combine") or {}
        img_comb = None
        for path in [("array_param", "img_comb"), ("combine", "img_comb")]:
            d = pc
            try:
                for k in path:
                    d = d[k]
                img_comb = np.atleast_1d(d)
                break
            except (KeyError, TypeError):
                continue
        n_imgs = None
        for path in [("combine", "imgs"), ("array_param", "imgs")]:
            d = pc
            try:
                for k in path:
                    d = d[k]
                n_imgs = len(d)
                break
            except (KeyError, TypeError):
                continue
        print(f"{coll} {item.name}: img_comb={img_comb}, n_imgs={n_imgs}")
        for img in [1, 2]:
            try:
                dsi = opr.load_frame(item, data_product=prod, image=img,
                                     allow_unlisted_products=True)
                print(f"  img {img}: OK, twtt {dsi.twtt.values[0]*1e6:.2f}..{dsi.twtt.values[-1]*1e6:.2f} us")
            except Exception as e:
                print(f"  img {img}: FAILED {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        print(f"{coll}: query/load failed {type(e).__name__}: {str(e)[:150]}")
