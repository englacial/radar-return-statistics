"""Step-5 integration tests: schema auto-migration, calibration placeholders and
enable/disable transitions, backfill-in-place, saturation second pass with
split populations, and science-fingerprint staleness."""
import logging
import types

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import zarr

from radar_return_statistics import calibration as cal
from radar_return_statistics import store as store_mod
from radar_return_statistics.processing import process_frame
from radar_return_statistics.saturation_pass import run_saturation_pass


def _make_ds(n_traces=5, frame_id="F", hour_offset=0, with_calibration=False,
             offset_value=1.5):
    slow_times = pd.date_range(f"2012-01-01 {hour_offset:02d}:00:00",
                               periods=n_traces, freq="10s")
    data = {
        "surface_twtt": ("slow_time", np.full(n_traces, 5e-6)),
        "surface_power_dB": ("slow_time", np.full(n_traces, 10.0)),
        "qc_pass": ("slow_time", np.ones(n_traces, dtype=bool)),
        "frame_id": ("slow_time", [frame_id] * n_traces),
    }
    if with_calibration:
        data.update({
            "img_comb_offset_dB": ("slow_time",
                                   np.full(n_traces, offset_value, dtype=np.float32)),
            "img_comb_pair": ("slow_time", np.ones(n_traces, dtype=np.int8)),
            "surface_source_image_index": ("slow_time", np.ones(n_traces, dtype=np.int8)),
            "surface_ceiling_margin_dB": ("slow_time",
                                          np.full(n_traces, np.nan, dtype=np.float32)),
        })
    return xr.Dataset(data, coords={
        "slow_time": slow_times,
        "latitude": ("slow_time", np.full(n_traces, -75.0)),
        "longitude": ("slow_time", np.full(n_traces, 160.0)),
    })


@pytest.fixture
def local_repo(tmp_path):
    return store_mod.open_or_create_repo({"backend": "local", "path": str(tmp_path / "s")})


def _root(repo):
    return zarr.open_group(repo.readonly_session(branch="main").store, mode="r")


# ---------------------------------------------------------------------------
# Auto-migration on append
# ---------------------------------------------------------------------------

def test_append_to_precalibration_store_automigrates(local_repo, caplog):
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", _make_ds(5, "F1", 0))
    with caplog.at_level(logging.WARNING):
        store_mod.write_frame_results(
            session, "F2", _make_ds(3, "F2", 1, with_calibration=True))
    store_mod.commit_session(session, "test")

    assert any("Store migration" in r.message for r in caplog.records)
    root = _root(local_repo)
    off = root["img_comb_offset_dB"][:]
    assert off.shape == (8,)
    assert np.all(np.isnan(off[:5]))          # backfilled fill value
    assert np.allclose(off[5:], 1.5)
    pair = root["img_comb_pair"][:]
    assert np.all(pair[:5] == -1) and np.all(pair[5:] == 1)
    assert np.all(root["surface_source_image_index"][:5] == -1)


def test_enabled_disabled_enabled_transitions(local_repo):
    # Disabled frames still emit placeholder columns (schema stays uniform),
    # so appends in any enabled/disabled order line up.
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", _make_ds(2, "F1", 0, True, 2.0))
    disabled = _make_ds(2, "F2", 1, True)
    disabled["img_comb_offset_dB"][:] = np.nan
    disabled["img_comb_pair"][:] = -1
    disabled["surface_source_image_index"][:] = -1
    store_mod.write_frame_results(session, "F2", disabled)
    store_mod.write_frame_results(session, "F3", _make_ds(2, "F3", 2, True, 3.0))
    store_mod.commit_session(session, "test")

    root = _root(local_repo)
    off = root["img_comb_offset_dB"][:]
    assert np.allclose(off[[0, 1]], 2.0) and np.allclose(off[[4, 5]], 3.0)
    assert np.all(np.isnan(off[[2, 3]]))


# ---------------------------------------------------------------------------
# Processing emits placeholders / real columns
# ---------------------------------------------------------------------------

def _minimal_config(img_combine):
    return {
        "processing": {
            "data_product": "CSARP_standard", "decimate_interval": None,
            "layer_margin_m": 50, "ice_permittivity": 3.17, "max_workers": 1,
            "calibration": {"img_combine": img_combine},
        },
        "qc": {"max_heading_change_deg_per_km": None, "min_ice_thickness_m": None,
               "min_agl_m": None, "min_bed_snr_db": None, "min_traces_after_qc": 1},
    }


def test_process_frame_calibration_disabled_placeholders(
        mocker, synthetic_frame, synthetic_layers):
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers
    ds = process_frame(opr, types.SimpleNamespace(name="F"), _minimal_config(False))
    assert np.all(np.isnan(ds["img_comb_offset_dB"].values))
    assert np.all(ds["img_comb_pair"].values == -1)
    assert np.all(ds["surface_source_image_index"].values == -1)
    assert ds.attrs["frame_img_comb_status"] == cal.STATUS_DISABLED
    assert "frame_img_comb_offset_dB" not in ds.attrs
    # disabled: the (mock) opr must not be asked for image products
    assert not any(kw.get("image") for _, kw in opr.load_frame.call_args_list)


def test_process_frame_calibration_enabled_degrades_to_status(
        mocker, synthetic_frame, synthetic_layers):
    # Synthetic frames carry no OPR params: the check must degrade to a status
    # (params_missing), never fail the frame.
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers
    ds = process_frame(opr, types.SimpleNamespace(name="F"), _minimal_config(True))
    assert ds is not None
    assert ds.attrs["frame_img_comb_status"] == cal.STATUS_PARAMS_MISSING
    assert np.all(np.isnan(ds["img_comb_offset_dB"].values))


# ---------------------------------------------------------------------------
# Backfill in place
# ---------------------------------------------------------------------------

def test_update_frame_calibration_in_place(local_repo):
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", _make_ds(3, "F1", 0, True, np.nan))
    store_mod.write_frame_results(session, "F2", _make_ds(4, "F2", 1, True, np.nan))
    store_mod.update_frame_index(session)
    root_w = zarr.open_group(session.store, mode="a")
    root_w.attrs["frame_img_comb_status"] = ["load_error", "load_error"]
    store_mod.commit_session(session, "initial")

    session = local_repo.writable_session("main")
    n = store_mod.update_frame_calibration(
        session, "F2",
        per_trace={
            "img_comb_offset_dB": np.full(4, 0.7, dtype=np.float32),
            "img_comb_pair": np.full(4, 1, dtype=np.int8),
            "surface_source_image_index": np.full(4, 2, dtype=np.int8),
        },
        frame_attrs={"frame_img_comb_status": "ok",
                     "frame_img_comb_offset_dB": 0.7},
    )
    store_mod.commit_session(session, "backfill")
    assert n == 4

    root = _root(local_repo)
    off = root["img_comb_offset_dB"][:]
    assert np.all(np.isnan(off[:3])) and np.allclose(off[3:], 0.7)
    names = list(root.attrs["frame_names"])
    statuses = list(root.attrs["frame_img_comb_status"])
    assert dict(zip(names, statuses)) == {"F1": "load_error", "F2": "ok"}
    offsets_attr = list(root.attrs["frame_img_comb_offset_dB"])
    assert offsets_attr[names.index("F2")] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Split-population second pass + cross-cap step
# ---------------------------------------------------------------------------

def _two_population_arrays(n=30000, seed=0, level1=-31.0, level2=-53.0):
    """img1 clipped at level1 below the 1 km cap, img2 clipped at level2 beyond
    — the resolved two-regime structure with a known cross-cap step."""
    rng = np.random.default_rng(seed)
    r = 10 ** rng.uniform(np.log10(300), np.log10(4000), n)
    src = np.where(r < 1000, 1, 2).astype(np.int8)
    # a clip truncates from above: noise spreads the population downward only
    p = np.where(src == 1, level1, level2) - np.abs(rng.normal(0, 1.0, n))
    return p, r, src


def test_fit_season_by_source_split_populations():
    p, r, src = _two_population_arrays()
    fit = cal.fit_season_by_source(p, r, src)
    assert fit["ceiling_fit_population"] == "by_source_image"
    assert fit["populations"]["img1"]["status"] == cal.FIT_OK
    assert fit["populations"]["img1"]["level"] == pytest.approx(-31.0, abs=1.5)
    assert fit["populations"]["img2"]["status"] == cal.FIT_OK
    assert fit["populations"]["img2"]["level"] == pytest.approx(-53.0, abs=1.5)
    assert fit["cross_cap_step_db"] == pytest.approx(22.0, abs=3.0)

    margins = cal.margins_by_source(p, src, fit)
    assert np.isfinite(margins).all()
    # each trace's margin is against its own population's ceiling
    assert np.median(margins[src == 1]) == pytest.approx(
        fit["populations"]["img1"]["level"] - np.median(p[src == 1]), abs=0.3)


def test_fit_season_by_source_falls_back_without_index():
    p, r, _ = _two_population_arrays()
    fit = cal.fit_season_by_source(p, r, np.full(p.shape, -1, dtype=np.int8))
    assert fit["ceiling_fit_population"] == "all_traces"
    assert "all" in fit["populations"]


def test_saturation_pass_writes_margins_and_attrs(local_repo):
    n = 12000
    p, r, src = _two_population_arrays(n=n, seed=1)
    twtt = 2.0 * r / 299792458.0
    slow_times = pd.date_range("2012-01-01", periods=n, freq="s")
    ds = xr.Dataset(
        {
            "surface_twtt": ("slow_time", twtt),
            "surface_power_dB": ("slow_time", p),
            "qc_pass": ("slow_time", np.ones(n, dtype=bool)),
            "surface_source_image_index": ("slow_time", src),
            "frame_id": ("slow_time", ["F1"] * n),
        },
        coords={"slow_time": slow_times,
                "latitude": ("slow_time", np.full(n, -75.0)),
                "longitude": ("slow_time", np.full(n, 160.0))},
    )
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", ds)
    store_mod.update_frame_index(session, frame_collections={"F1": "test_season"})
    store_mod.commit_session(session, "data")

    root = _root(local_repo)
    margins, sat_attrs = run_saturation_pass(root)
    season = sat_attrs["seasons"]["test_season"]
    assert season["ceiling_fit_population"] == "by_source_image"
    assert season["cross_cap_step_db"] == pytest.approx(22.0, abs=3.0)
    assert np.isfinite(margins).sum() > 0.9 * n

    session = local_repo.writable_session("main")
    store_mod.write_saturation_results(session, margins, sat_attrs)
    store_mod.commit_session(session, "second pass")
    root = _root(local_repo)
    assert np.isfinite(root["surface_ceiling_margin_dB"][:]).sum() > 0.9 * n
    assert root.attrs["saturation"]["seasons"]["test_season"]["cross_cap_step_db"] is not None


# ---------------------------------------------------------------------------
# Fingerprint staleness
# ---------------------------------------------------------------------------

def test_fingerprint_staleness(local_repo):
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", _make_ds(5, "F1", 0, True))
    store_mod.commit_session(session, "science")

    root = _root(local_repo)
    assert store_mod.saturation_stale(root) is None  # no second pass yet

    # Second pass -> fresh (its own write must not look stale)
    session = local_repo.writable_session("main")
    store_mod.write_saturation_results(
        session, np.full(5, np.nan, dtype=np.float32), {"seasons": {}})
    store_mod.commit_session(session, "second pass")
    assert store_mod.saturation_stale(_root(local_repo)) is False

    # Calibration-only backfill write -> still fresh
    session = local_repo.writable_session("main")
    store_mod.update_frame_calibration(
        session, "F1",
        per_trace={"img_comb_offset_dB": np.full(5, 0.1, dtype=np.float32)},
        frame_attrs={})
    store_mod.commit_session(session, "backfill")
    assert store_mod.saturation_stale(_root(local_repo)) is False

    # Science append -> stale
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F2", _make_ds(3, "F2", 1, True))
    store_mod.commit_session(session, "more science")
    assert store_mod.saturation_stale(_root(local_repo)) is True


def test_backfill_source_index_marks_saturation_stale(local_repo):
    """A backfill that rewrites surface_source_image_index (a saturation fit
    input) must invalidate existing saturation results; the next second pass
    clears the flag."""
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", _make_ds(5, "F1", 0, True))
    store_mod.write_saturation_results(
        session, np.full(5, np.nan, dtype=np.float32), {"seasons": {}})
    store_mod.commit_session(session, "science + second pass")
    assert store_mod.saturation_stale(_root(local_repo)) is False

    session = local_repo.writable_session("main")
    n = store_mod.update_frame_calibration(
        session, "F1",
        per_trace={"surface_source_image_index": np.full(5, 2, dtype=np.int8)},
        frame_attrs={})
    store_mod.commit_session(session, "backfill with source index")
    assert n == 5
    assert store_mod.saturation_stale(_root(local_repo)) is True

    session = local_repo.writable_session("main")
    store_mod.write_saturation_results(
        session, np.full(5, np.nan, dtype=np.float32), {"seasons": {}})
    store_mod.commit_session(session, "re-run second pass")
    assert store_mod.saturation_stale(_root(local_repo)) is False


def test_update_frame_calibration_mismatch_writes_nothing(local_repo):
    """Any array-length mismatch aborts the whole frame update (no partial
    write recorded as success), leaving the frame retryable."""
    session = local_repo.writable_session("main")
    store_mod.write_frame_results(session, "F1", _make_ds(4, "F1", 0, True, np.nan))
    store_mod.update_frame_index(session)
    root_w = zarr.open_group(session.store, mode="a")
    root_w.attrs["frame_img_comb_status"] = ["load_error"]
    store_mod.commit_session(session, "initial")

    session = local_repo.writable_session("main")
    n = store_mod.update_frame_calibration(
        session, "F1",
        per_trace={
            "img_comb_offset_dB": np.full(4, 0.7, dtype=np.float32),   # correct
            "surface_source_image_index": np.full(3, 1, dtype=np.int8),  # WRONG length
        },
        frame_attrs={"frame_img_comb_status": "ok"},
    )
    assert n == 0
    # nothing to commit — the session must hold zero changes
    with pytest.raises(Exception, match="no changes"):
        store_mod.commit_session(session, "attempted backfill")
    root = _root(local_repo)
    assert np.all(np.isnan(root["img_comb_offset_dB"][:]))   # nothing written
    assert list(root.attrs["frame_img_comb_status"]) == ["load_error"]  # retryable
