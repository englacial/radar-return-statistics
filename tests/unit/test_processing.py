import types

import numpy as np
import pytest
import scipy.constants

from radar_return_statistics.processing import (
    _build_qc_checks,
    _resolve_noise_config,
    compute_noise_powers,
    compute_rssnr_dB,
    extract_layer_peak_power,
    peak_power_in_window,
    process_frame,
)
from tests.conftest import BED_IDX, BED_VAL, SURF_IDX, SURF_VAL


def test_peak_power_in_window_finds_max():
    twtt = np.linspace(0, 1e-4, 1000)
    data = np.ones(1000)
    data[500] = 100.0  # peak at midpoint
    result = peak_power_in_window(data, twtt, twtt[500], margin_twtt=5e-6)
    np.testing.assert_allclose(result, 10 * np.log10(100.0))


def test_peak_power_in_window_empty_returns_nan():
    twtt = np.linspace(0, 1e-4, 100)
    data = np.ones(100)
    result = peak_power_in_window(data, twtt, pick_twtt=1.0, margin_twtt=1e-6)
    assert np.isnan(result)


def test_compute_rssnr_dB_matches_formula():
    c = scipy.constants.c
    ice_permittivity = 3.17
    n = np.sqrt(ice_permittivity)
    surf_twtt = 2e-6
    bed_twtt = 22e-6
    surf_power_dB = 10.0
    bed_power_dB = -30.0

    r_surf = c * surf_twtt / 2
    ice_thickness = (c / n) / 2 * (bed_twtt - surf_twtt)
    r_bed_eff = r_surf + ice_thickness / n
    expected = surf_power_dB - bed_power_dB + 10 * np.log10(r_surf**2 / r_bed_eff**2)

    result = compute_rssnr_dB(surf_power_dB, bed_power_dB, surf_twtt, bed_twtt, ice_permittivity)
    np.testing.assert_allclose(result, expected, rtol=1e-10)


def test_compute_rssnr_dB_accepts_arrays():
    c = scipy.constants.c
    ice_permittivity = 3.17
    surf_twtt = np.array([2e-6, 3e-6])
    bed_twtt = np.array([22e-6, 30e-6])
    result = compute_rssnr_dB(10.0, -30.0, surf_twtt, bed_twtt, ice_permittivity)
    assert result.shape == (2,)
    assert np.all(np.isfinite(result))


def test_extract_peak_finds_correct_twtt(synthetic_frame, synthetic_layers):
    expected_twtt = synthetic_frame.twtt.values[SURF_IDX]
    peak_twtt, _ = extract_layer_peak_power(
        synthetic_frame,
        synthetic_layers["standard:surface"]["twtt"],
        margin_twtt=1e-6,
    )
    np.testing.assert_allclose(peak_twtt.values, expected_twtt, rtol=1e-9)


def test_extract_peak_power_correct_db(synthetic_frame, synthetic_layers):
    _, peak_power = extract_layer_peak_power(
        synthetic_frame,
        synthetic_layers["standard:surface"]["twtt"],
        margin_twtt=1e-6,
    )
    expected_dB = 10 * np.log10(SURF_VAL)
    np.testing.assert_allclose(peak_power.values, expected_dB, rtol=1e-9)


def test_build_qc_checks_excludes_none():
    checks = _build_qc_checks({
        "max_heading_change_deg_per_km": None,
        "min_ice_thickness_m": 100,
        "min_agl_m": None,
        "min_bed_snr_db": 5.0,
    })
    assert "heading_change" not in checks
    assert "minimum_agl" not in checks
    assert checks["ice_thickness_threshold"] == {"min_thickness_m": 100}
    assert checks["snr_bed_pick"] == {"min_snr_db": 5.0}


def test_build_qc_checks_all_none():
    assert _build_qc_checks({}) == {}


def test_process_frame_output_variables(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME_001"), minimal_proc_config)

    assert ds is not None
    assert set(ds.data_vars) == {
        "surface_twtt", "bed_twtt", "surface_elevation", "bed_elevation",
        "surface_power_dB", "bed_power_dB", "required_surface_snr_dB",
        "pre_surface_noise_dB", "post_bed_noise_dB",
        "record_tail_noise_dB",
        "post_bed_noise_interp_dB", "post_bed_peak_interp_dB", "post_bed_std_interp_dB",
        "record_end_twtt",
        "qc_pass", "qc_surface_pass", "qc_heading_pass", "qc_agl_pass",
        "bed_pick_available", "bed_pick_attempted", "bed_pick_quality", "frame_id",
    }
    assert ds.attrs["frame_bed_pick_fraction"] == 1.0
    assert ds.attrs["segment_bed_pick_fraction"] == 1.0


def test_process_frame_frame_id_filled(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers

    ds = process_frame(opr, types.SimpleNamespace(name="MY_FRAME"), minimal_proc_config)

    assert all(fid == "MY_FRAME" for fid in ds["frame_id"].values)


def test_process_frame_returns_none_on_missing_bed_layer(mocker, synthetic_frame, minimal_proc_config):
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = {"standard:surface": {}}  # no bed

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)
    assert ds is None


def test_process_frame_bed_fallback_key(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    """Bed picks published under ':bottom' (e.g. 2019_Antarctica_GV) are used when
    'standard:bottom' is absent, producing the same output."""
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers

    expected = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    fallback_layers = dict(synthetic_layers)
    fallback_layers[":bottom"] = fallback_layers.pop("standard:bottom")
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = fallback_layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    assert ds is not None
    for var in ("bed_twtt", "bed_power_dB", "required_surface_snr_dB"):
        np.testing.assert_array_equal(ds[var].values, expected[var].values)


def test_process_frame_partial_bed_picks(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    """Traces missing bed picks keep surface metrics; bed metrics are NaN;
    availability flag and fraction attrs reflect the gaps."""
    import xarray as xr

    layers = dict(synthetic_layers)
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values.copy()
    bed_twtt[:4] = np.nan  # first 4 of 10 traces unpicked
    layers["standard:bottom"] = {"twtt": xr.DataArray(
        bed_twtt, dims=["slow_time"],
        coords={"slow_time": synthetic_frame.slow_time.values},
    )}
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    assert ds is not None
    assert list(ds["bed_pick_available"].values) == [False] * 4 + [True] * 6
    assert np.isnan(ds["bed_twtt"].values[:4]).all()
    assert np.isnan(ds["bed_power_dB"].values[:4]).all()
    assert np.isfinite(ds["bed_twtt"].values[4:]).all()
    # Surface metrics survive on the unpicked traces
    assert np.isfinite(ds["surface_power_dB"].values).all()
    assert ds.attrs["frame_bed_pick_fraction"] == pytest.approx(0.6)
    assert ds.attrs["segment_bed_pick_fraction"] == pytest.approx(0.6)


def test_process_frame_min_traces_uses_pick_independent_qc(
    mocker, synthetic_frame, synthetic_layers, minimal_proc_config
):
    """A frame where most traces lack bed picks (failing the ice-thickness
    check) survives as long as enough traces pass pick-independent QC."""
    import xarray as xr

    layers = dict(synthetic_layers)
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values.copy()
    bed_twtt[:8] = np.nan  # only 2 of 10 traces picked
    layers["standard:bottom"] = {"twtt": xr.DataArray(
        bed_twtt, dims=["slow_time"],
        coords={"slow_time": synthetic_frame.slow_time.values},
    )}
    config = dict(minimal_proc_config)
    config["qc"] = {**minimal_proc_config["qc"],
                    "min_ice_thickness_m": 100, "min_traces_after_qc": 5}

    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), config)

    assert ds is not None, "frame should survive via pick-independent QC"
    assert int(ds["qc_pass"].sum()) == 2          # full QC: only picked traces
    assert int(ds["qc_surface_pass"].sum()) == 10  # no pick-independent checks enabled
    # Unpicked traces failed full QC but keep surface metrics
    assert np.isfinite(ds["surface_power_dB"].values).all()
    assert np.isnan(ds["required_surface_snr_dB"].values[:8]).all()


def test_process_frame_skips_segment_with_no_bed_picks(
    mocker, synthetic_frame, synthetic_layers, minimal_proc_config
):
    """A bed layer containing zero finite picks = picking never attempted."""
    import xarray as xr

    layers = dict(synthetic_layers)
    layers["standard:bottom"] = {"twtt": xr.DataArray(
        np.full(len(synthetic_frame.slow_time), np.nan), dims=["slow_time"],
        coords={"slow_time": synthetic_frame.slow_time.values},
    )}
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = layers

    assert process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config) is None


def test_interp_post_bed_noise_identical_where_picked(
    mocker, synthetic_frame, synthetic_layers, minimal_proc_config
):
    """With full picks, the interp-anchored median equals post_bed_noise_dB."""
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers
    # Default 5/5 us offsets leave no room after the fixture's bed (14.4 of
    # 20 us) — use a window that actually exists.
    config = dict(minimal_proc_config)
    config["processing"] = {**minimal_proc_config["processing"],
                            "noise": {"post_bed": {"start_offset_us": 1.0, "end_offset_us": 0.5}}}

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), config)

    np.testing.assert_allclose(
        ds["post_bed_noise_interp_dB"].values, ds["post_bed_noise_dB"].values,
        rtol=1e-9, equal_nan=True,
    )
    assert np.isfinite(ds["post_bed_peak_interp_dB"].values).all()
    assert (ds["post_bed_peak_interp_dB"].values >= ds["post_bed_noise_interp_dB"].values).all()
    assert (ds["post_bed_std_interp_dB"].values >= 0).all()


def test_interp_post_bed_metrics_on_gap_traces(
    mocker, synthetic_frame, synthetic_layers, minimal_proc_config
):
    """Interior gaps get interp-anchored metrics; edge gaps stay NaN."""
    import xarray as xr

    layers = dict(synthetic_layers)
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values.copy()
    bed_twtt[:2] = np.nan   # leading edge gap -> NaN metrics
    bed_twtt[5] = np.nan    # interior gap -> interp-anchored metrics
    layers["standard:bottom"] = {"twtt": xr.DataArray(
        bed_twtt, dims=["slow_time"],
        coords={"slow_time": synthetic_frame.slow_time.values},
    )}
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = layers
    config = dict(minimal_proc_config)
    config["processing"] = {**minimal_proc_config["processing"],
                            "noise": {"post_bed": {"start_offset_us": 1.0, "end_offset_us": 0.5}}}

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), config)

    noise = ds["post_bed_noise_interp_dB"].values
    assert np.isnan(noise[:2]).all(), "edge gaps must stay NaN (no extrapolation)"
    assert np.isfinite(noise[5]), "interior gap gets interp-anchored metrics"
    # Flat picks in the fixture -> interpolated anchor equals the pick value.
    # Recompute the expected median for trace 5 directly from its samples.
    twtt = synthetic_frame.twtt.values
    pick = synthetic_layers["standard:bottom"]["twtt"].values[0]
    mask = (twtt >= pick + 1.0e-6) & (twtt <= twtt[-1] - 0.5e-6)
    expected = 10 * np.log10(np.median(np.abs(synthetic_frame.Data.values[5, mask])))
    np.testing.assert_allclose(noise[5], expected, rtol=1e-9)
    # Picked traces still identical to the standard term
    picked = np.isfinite(ds["post_bed_noise_dB"].values)
    np.testing.assert_allclose(
        noise[picked], ds["post_bed_noise_dB"].values[picked], rtol=1e-9
    )


def test_record_end_twtt_matches_axis(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    """record_end_twtt equals the last twtt sample on every trace, unmasked."""
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    expected = synthetic_frame.twtt.values[-1]
    np.testing.assert_allclose(ds["record_end_twtt"].values, expected, rtol=1e-12)
    assert np.isfinite(ds["record_end_twtt"].values).all()


def test_compute_record_tail_noise_matches_median(synthetic_frame):
    from radar_return_statistics.processing import compute_record_tail_noise

    result = compute_record_tail_noise(
        synthetic_frame,
        {"record_tail": {"start_offset_us": 5.0, "end_offset_us": 0.0}},
    )

    twtt = synthetic_frame.twtt.values
    data = np.abs(synthetic_frame.Data.values)  # (slow_time, twtt)
    mask = twtt >= twtt[-1] - 5.0e-6
    expected = 10.0 * np.log10(np.median(data[:, mask], axis=1))
    np.testing.assert_allclose(result, expected)


def test_compute_record_tail_noise_blanks_record_end(synthetic_frame):
    """A nonzero end offset excludes the final samples (rolloff blanking)."""
    from radar_return_statistics.processing import compute_record_tail_noise

    # Corrupt the last 2 us with a strong rolloff (tiny values)
    frame = synthetic_frame.copy(deep=True)
    twtt = frame.twtt.values
    tail = twtt > twtt[-1] - 2.0e-6
    frame.Data.values[:, tail] = 1e-12

    corrupted = compute_record_tail_noise(
        frame, {"record_tail": {"start_offset_us": 5.0, "end_offset_us": 0.0}})
    blanked = compute_record_tail_noise(
        frame, {"record_tail": {"start_offset_us": 7.0, "end_offset_us": 2.0}})

    mask = (twtt >= twtt[-1] - 7.0e-6) & (twtt <= twtt[-1] - 2.0e-6)
    expected = 10.0 * np.log10(np.median(np.abs(frame.Data.values[:, mask]), axis=1))
    np.testing.assert_allclose(blanked, expected)
    assert (blanked > corrupted).all(), "blanked window must not see the rolloff"


def test_bed_pick_attempted_excludes_segment_edges(
    mocker, synthetic_frame, synthetic_layers, minimal_proc_config
):
    """Missing picks before the first / after the last segment pick don't count
    as attempted; interior gaps do."""
    import xarray as xr

    layers = dict(synthetic_layers)
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values.copy()
    bed_twtt[:3] = np.nan   # leading edge gap
    bed_twtt[5] = np.nan    # interior gap (censored candidate)
    bed_twtt[-2:] = np.nan  # trailing edge gap
    layers["standard:bottom"] = {"twtt": xr.DataArray(
        bed_twtt, dims=["slow_time"],
        coords={"slow_time": synthetic_frame.slow_time.values},
    )}
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    attempted = ds["bed_pick_attempted"].values
    available = ds["bed_pick_available"].values
    assert list(attempted) == [False] * 3 + [True] * 5 + [False] * 2
    censored = attempted & ~available
    assert list(np.nonzero(censored)[0]) == [5]


def test_process_frame_bed_pick_quality_passthrough(
    mocker, synthetic_frame, synthetic_layers, minimal_proc_config
):
    import xarray as xr

    layers = dict(synthetic_layers)
    quality = np.full(len(synthetic_frame.slow_time), 2.0)
    bed = dict(synthetic_layers["standard:bottom"])
    bed["quality"] = xr.DataArray(
        quality, dims=["slow_time"],
        coords={"slow_time": synthetic_frame.slow_time.values},
    )
    layers["standard:bottom"] = bed
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)
    assert (ds["bed_pick_quality"].values == 2).all()

    # Without a quality field, the flag is -1 everywhere
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers
    ds2 = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)
    assert (ds2["bed_pick_quality"].values == -1).all()


def test_process_frame_surface_fallback_key(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    """Surface picks under ':surface' (some Greenland P3 segments) are used when
    'standard:surface' is absent, producing the same output."""
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers

    expected = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    fallback_layers = dict(synthetic_layers)
    fallback_layers[":surface"] = fallback_layers.pop("standard:surface")
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = fallback_layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    assert ds is not None
    for var in ("surface_twtt", "surface_power_dB", "required_surface_snr_dB"):
        np.testing.assert_array_equal(ds[var].values, expected[var].values)


def test_process_frame_returns_none_on_layer_exception(mocker, synthetic_frame, minimal_proc_config):
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.side_effect = RuntimeError("layer load failed")

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)
    assert ds is None


def test_resolve_noise_config_fills_defaults():
    cfg = _resolve_noise_config({})
    assert cfg["pre_surface"]["start_offset_us"] == 1.0
    assert cfg["pre_surface"]["end_offset_us"] == 1.0
    assert cfg["post_bed"]["start_offset_us"] == 5.0
    assert cfg["post_bed"]["end_offset_us"] == 5.0


def test_resolve_noise_config_overrides_partial():
    cfg = _resolve_noise_config({"pre_surface": {"start_offset_us": 0.25}})
    assert cfg["pre_surface"]["start_offset_us"] == 0.25
    # untouched keys keep defaults
    assert cfg["pre_surface"]["end_offset_us"] == 1.0
    assert cfg["post_bed"]["start_offset_us"] == 5.0


def test_compute_noise_powers_matches_median(synthetic_frame, synthetic_layers):
    """Median over the noise window in linear power should round-trip to dB."""
    surface_twtt = synthetic_layers["standard:surface"]["twtt"].values
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values
    # Tight offsets so both windows fit inside the synthetic frame's 1-20 us twtt.
    noise_cfg = {
        "pre_surface": {"start_offset_us": 0.5, "end_offset_us": 0.5},
        "post_bed": {"start_offset_us": 1.0, "end_offset_us": 1.0},
    }
    pre, post = compute_noise_powers(
        synthetic_frame, surface_twtt, bed_twtt, noise_cfg,
    )
    assert pre.shape == (synthetic_frame.dims["slow_time"],)
    assert post.shape == (synthetic_frame.dims["slow_time"],)
    # Synthetic background is uniform[0.001, 0.01] linear power; median sits
    # roughly mid-range. Verify against a direct numpy computation per trace.
    twtt = synthetic_frame.twtt.values
    data = np.abs(synthetic_frame.Data.values)  # (slow_time, twtt)
    for i in range(synthetic_frame.dims["slow_time"]):
        s = surface_twtt[i]
        b = bed_twtt[i]
        pre_mask = (twtt >= twtt[0] + 0.5e-6) & (twtt <= s - 0.5e-6)
        post_mask = (twtt >= b + 1.0e-6) & (twtt <= twtt[-1] - 1.0e-6)
        expected_pre = 10 * np.log10(np.median(data[i, pre_mask]))
        expected_post = 10 * np.log10(np.median(data[i, post_mask]))
        np.testing.assert_allclose(pre[i], expected_pre, rtol=1e-9)
        np.testing.assert_allclose(post[i], expected_post, rtol=1e-9)


def test_compute_noise_powers_empty_window_returns_nan(synthetic_frame, synthetic_layers):
    surface_twtt = synthetic_layers["standard:surface"]["twtt"].values
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values
    # Offsets so large that the windows are empty for this frame.
    noise_cfg = {
        "pre_surface": {"start_offset_us": 100.0, "end_offset_us": 0.0},
        "post_bed": {"start_offset_us": 100.0, "end_offset_us": 0.0},
    }
    pre, post = compute_noise_powers(
        synthetic_frame, surface_twtt, bed_twtt, noise_cfg,
    )
    assert np.all(np.isnan(pre))
    assert np.all(np.isnan(post))


def test_compute_noise_powers_nan_pick_propagates(synthetic_frame, synthetic_layers):
    surface_twtt = synthetic_layers["standard:surface"]["twtt"].values.copy()
    bed_twtt = synthetic_layers["standard:bottom"]["twtt"].values.copy()
    surface_twtt[0] = np.nan
    bed_twtt[-1] = np.nan
    pre, post = compute_noise_powers(synthetic_frame, surface_twtt, bed_twtt, {})
    assert np.isnan(pre[0])
    assert np.isnan(post[-1])


def test_rssnr_matches_geometric_spreading_formula(mocker, synthetic_frame, synthetic_layers, minimal_proc_config):
    """RSSNR matches the reference formula: surf_power - geom_surf - (bed_power - geom_bed)."""
    opr = mocker.MagicMock()
    opr.load_frame.return_value = synthetic_frame
    opr.get_layers.return_value = synthetic_layers

    ds = process_frame(opr, types.SimpleNamespace(name="FRAME"), minimal_proc_config)

    twtt = synthetic_frame.twtt.values
    surf_twtt = twtt[SURF_IDX]
    bed_twtt = twtt[BED_IDX]
    ice_permittivity = minimal_proc_config["processing"]["ice_permittivity"]
    c = scipy.constants.c
    n = np.sqrt(ice_permittivity)
    v_ice = c / n

    r_surf = c * surf_twtt / 2
    ice_thickness = v_ice / 2 * (bed_twtt - surf_twtt)
    r_bed_eff = r_surf + ice_thickness / n

    expected = 10 * np.log10(SURF_VAL * r_surf**2 / (BED_VAL * r_bed_eff**2))
    np.testing.assert_allclose(ds["required_surface_snr_dB"].values, expected, rtol=1e-5)
