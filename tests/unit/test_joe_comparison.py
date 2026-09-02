"""Unit tests for the Joe Greenland RSSNR comparison matching logic."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts" / "analysis"))
import greenland_joe_comparison as gjc


def _joe(x, y, rssnr, thick):
    n = len(x)
    return pd.DataFrame({
        "x": x, "y": y, "rssnr": rssnr, "thick": thick,
        "year": np.full(n, "2014"), "segment": np.full(n, "seg"),
    })


def _opr(x, y, rssnr, thick):
    n = len(x)
    return pd.DataFrame({
        "x": x, "y": y, "rssnr_opr": rssnr, "thick_opr": thick,
        "frame_id": np.full(n, "Data_x"), "collection": np.full(n, "2014_Greenland_P3"),
    })


def test_match_pairs_nearest_within_threshold():
    joe = _joe([0.0, 100.0], [0.0, 0.0], [40.0, 50.0], [1000.0, 1200.0])
    opr = _opr([5.0, 102.0], [0.0, 0.0], [38.0, 49.0], [990.0, 1150.0])

    pairs, joe_m, opr_m = gjc.match_datasets(joe, opr, threshold=50.0)

    assert len(pairs) == 2
    assert joe_m.all() and opr_m.all()
    # diffs are Joe - OPR
    p0 = pairs.set_index("rssnr_joe").loc[40.0]
    assert p0["rssnr_diff"] == 40.0 - 38.0
    assert p0["thick_diff"] == 1000.0 - 990.0


def test_match_respects_threshold():
    joe = _joe([0.0], [0.0], [40.0], [1000.0])
    opr = _opr([200.0], [0.0], [38.0], [990.0])  # 200 m away, beyond 50 m

    pairs, joe_m, opr_m = gjc.match_datasets(joe, opr, threshold=50.0)
    assert len(pairs) == 0
    assert not joe_m.any() and not opr_m.any()


def test_match_one_to_one_keeps_closest():
    # Two OPR points both nearest the same single Joe point; keep the closer one.
    joe = _joe([0.0], [0.0], [40.0], [1000.0])
    opr = _opr([10.0, 30.0], [0.0, 0.0], [38.0, 35.0], [990.0, 980.0])

    pairs, joe_m, opr_m = gjc.match_datasets(joe, opr, threshold=50.0)
    assert len(pairs) == 1
    assert pairs.iloc[0]["distance_m"] == 10.0
    assert opr_m.sum() == 1  # only the closest OPR point is consumed
