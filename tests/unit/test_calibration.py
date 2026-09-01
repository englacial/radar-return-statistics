import numpy as np
import pytest
import xarray as xr

from radar_return_statistics import calibration as cal


# ---------------------------------------------------------------------------
# Synthetic frame builder: combined + 2 images with known per-image scalings
# (s1, s2) and a known seam step injected into the combined product.
# ---------------------------------------------------------------------------

def _profile_db(t):
    return -40.0 - 3.5e6 * t  # smooth decay, dB


def _to_linear(db):
    return 10.0 ** (db / 10.0)


def _dataset(data_db, twtt, surface, attrs, transpose=False):
    n_traces = data_db.shape[0]
    data = _to_linear(data_db)
    if transpose:
        data = data.T
        dims = ("twtt", "slow_time")
    else:
        dims = ("slow_time", "twtt")
    return xr.Dataset(
        {
            "Data": (dims, data),
            "Surface": (("slow_time",), surface),
        },
        coords={
            "slow_time": np.arange(n_traces).astype("datetime64[s]"),
            "twtt": twtt,
        },
        attrs=attrs,
    )


def make_synthetic(s1=0.0, s2=0.0, step=2.0, n_traces=100, img1_end=8e-6,
                   img_comb=(2e-6, -np.inf, 1e-6), combined_extra=None,
                   transpose_img2=False, imgs_entries=2):
    """Combined product sections: [start, b) from img1+s1, [b, end] from
    img2+s2-step (so residual seam step = +step, earlier image brighter)."""
    tc = np.arange(0, 20e-6, 20e-9)
    t1 = np.arange(0, img1_end, 20e-9)
    t2 = np.arange(0, 20e-6, 25e-9)  # deliberately different grid
    surface = np.full(n_traces, 1.0e-6)

    A = _profile_db(tc)
    # noise floors ride each image's own gain reference
    img1_db = np.tile(np.maximum(_profile_db(t1) - s1, -130.0 - s1), (n_traces, 1))
    img2_db = np.tile(np.maximum(_profile_db(t2) - step - s2, -135.0 - s2), (n_traces, 1))

    T_comb, T_blank, T_guard = img_comb
    b = np.minimum(np.maximum(T_blank, T_comb + surface), t1[-1] - T_guard)
    combined_db = np.empty((n_traces, tc.size))
    for r in range(n_traces):
        sec2 = tc >= b[r]
        combined_db[r] = A
        combined_db[r, sec2] = A[sec2] - step
        if combined_extra is not None:
            combined_db[r, sec2] += combined_extra[r]

    attrs = {
        "param_array": {"array": {
            "img_comb": np.asarray(img_comb, dtype=float),
            "imgs": [np.array([[i + 1.0], [1.0]]) for i in range(imgs_entries)],
        }},
        "param_records": {"radar": {"wfs": {"Tpd": np.array([1e-6, 2e-6])}}},
    }
    combined = _dataset(combined_db, tc, surface, attrs)
    images = {
        1: _dataset(img1_db, t1, surface, {}),
        2: _dataset(img2_db, t2, surface, {}, transpose=transpose_img2),
    }
    return combined, images


class StubOPR:
    def __init__(self, images, raises=None):
        self.images = images
        self.raises = raises or {}

    def load_frame(self, item, data_product=None, image=None,
                   allow_unlisted_products=False):
        if image in self.raises:
            raise self.raises[image]
        if image in self.images:
            return self.images[image]
        raise FileNotFoundError(f"no image {image}")


def run_check(combined, images, raises=None):
    return cal.check_img_combine(StubOPR(images, raises), None, combined,
                                 "CSARP_standard")


# ---------------------------------------------------------------------------
# Check 1 tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("s2", [0.0, 107.6])
def test_seam_step_recovered_independent_of_scalings(s2):
    combined, images = make_synthetic(s1=1.5, s2=s2, step=2.0)
    res = run_check(combined, images)
    assert res["status"] == cal.STATUS_OK
    assert res["weights"][1] == pytest.approx(1.5, abs=0.05)
    assert res["weights"][2] == pytest.approx(s2, abs=0.05)
    assert res["pairs"][0]["mean"] == pytest.approx(2.0, abs=0.15)
    assert res["worst_pair"] == 1
    ok = np.isfinite(res["offsets"])
    assert ok.sum() >= 30
    assert np.nanmean(res["offsets"]) == pytest.approx(2.0, abs=0.15)


def test_differing_grids_and_orientation():
    combined, images = make_synthetic(step=3.0, transpose_img2=True)
    res = run_check(combined, images)
    assert res["status"] == cal.STATUS_OK
    assert res["pairs"][0]["mean"] == pytest.approx(3.0, abs=0.15)


def test_weight_recovery_failed_on_inconsistent_sections():
    n = 100
    drift = np.linspace(0.0, 1.0, n)  # w2 varies along the frame
    combined, images = make_synthetic(step=2.0, combined_extra=drift)
    res = run_check(combined, images)
    assert res["status"] == cal.STATUS_WEIGHT_RECOVERY_FAILED
    # raw offsets still computed for the parquet diagnostic
    assert np.isfinite(res["offsets"]).sum() > 0


def test_short_image_noise_floor_fallback():
    t_short = np.arange(0, 6e-6, 20e-9)
    Di = np.tile(np.maximum(_profile_db(t_short), -130.0), (10, 1))
    floor = cal.image_noise_floor_db(t_short, Di, t_guard=1e-6)
    # record shorter than the 12us window start -> percentile fallback,
    # never the top-of-record signal median (~ -41 dB here)
    assert np.all(floor < -55)
    combined, images = make_synthetic(step=2.0, img1_end=6e-6)
    res = run_check(combined, images)
    assert res["status"] == cal.STATUS_OK
    assert res["pairs"][0]["mean"] == pytest.approx(2.0, abs=0.2)


def test_signal_contaminated_tail_noise_floor():
    """A record long enough for the tail window whose end lies mid-ice
    (2012 DC8 img1): the tail median sits in signal, so the floor must be
    capped by the global low percentile, not the contaminated tail."""
    t = np.arange(0, 20e-6, 20e-9)
    profile = np.full(t.size, -120.0)          # true noise
    profile[(t > 3e-6) & (t < 19e-6)] = -80.0  # englacial signal through the end
    Di = np.tile(profile, (10, 1))
    floor = cal.image_noise_floor_db(t, Di, t_guard=2e-6)
    # tail window [8, 13] us medians -80 (signal); pre-surface noise keeps the
    # 10th percentile near -120
    assert np.all(floor < -110)


def test_insufficient_overlap():
    combined, images = make_synthetic(step=2.0,
                                      img_comb=(2e-6, -np.inf, 4.95e-6))
    res = run_check(combined, images)
    assert res["status"] == cal.STATUS_INSUFFICIENT_OVERLAP
    assert res["pairs"][0]["status"] == cal.STATUS_INSUFFICIENT_OVERLAP


def test_transient_vs_missing_images():
    combined, images = make_synthetic()
    res = run_check(combined, {1: images[1]},
                    raises={2: ConnectionError("timeout")})
    assert res["status"] == cal.STATUS_LOAD_ERROR
    res = run_check(combined, {1: images[1]})  # image 2 -> FileNotFoundError
    assert res["status"] == cal.STATUS_IMAGES_UNAVAILABLE
    assert res["image_errors"][2] == "missing"


def test_no_combine_single_image():
    combined, images = make_synthetic(imgs_entries=1)
    res = run_check(combined, images)
    assert res["status"] == cal.STATUS_NO_COMBINE
    assert np.all(res["surface_source_image_index"] == 1)


def test_empty_img_comb_uses_tpd_defaults():
    combined, images = make_synthetic(step=2.0, img_comb=(2e-6, -np.inf, 1e-6))
    combined.attrs["param_array"]["array"]["img_comb"] = np.array([])
    res = run_check(combined, images)
    # Tpd = [1us, 2us] -> default [T_comb=2us, -inf, T_guard=1us], same window
    assert res["status"] == cal.STATUS_OK
    assert "tpd_default" in res["img_comb_provenance"]
    assert res["pairs"][0]["mean"] == pytest.approx(2.0, abs=0.15)


def test_surface_source_image_index():
    n = 60
    surface = np.full(n, 1e-6)
    surface[40:] = 7.5e-6   # beyond img1 end (8us) - guard (1us)
    surface[50:] = np.nan   # NaN -> 0 -> img1
    gates = {1: (0.0, 8e-6), 2: (0.0, 20e-6)}
    guards = {1: 1e-6}
    idx = cal.surface_source_image_index(surface, gates, guards)
    assert np.all(idx[:40] == 1)
    assert np.all(idx[40:50] == 2)
    assert np.all(idx[50:] == 1)


def test_tpd_for_images_wf_mapping():
    tpd = np.array([1e-6, 1e-6, 3e-6, 3e-6, 10e-6, 10e-6])
    imgs = [np.array([[1.0, 2.0], [1.0, 2.0]]),
            np.array([[3.0, 4.0], [1.0, 2.0]]),
            np.array([[5.0, 6.0], [1.0, 2.0]])]
    tpds, consistent = cal.tpd_for_images(imgs, tpd)
    assert tpds == [1e-6, 3e-6, 10e-6]
    assert consistent


def test_2012_era_param_layout():
    """param_combine_wf_chan attr, old 2-per-pair img_comb, wfs as list of
    dicts, imgs entries as (N, 2) wf-adc rows (2012_Antarctica_DC8 layout)."""
    attrs = {
        "param_combine_wf_chan": {
            "img_comb": [1e-5, 2e-6],
            "array": {"imgs": [
                np.array([[1, 1], [1, 2], [1, 3], [1, 4], [1, 5]]),
                np.array([[2, 1], [2, 2], [2, 3], [2, 4], [2, 5]]),
            ]},
        },
        "param_csarp": {"radar": {"wfs": [{"Tpd": 1e-6}, {"Tpd": 1e-5}]}},
    }
    img_comb, prov = cal.find_img_comb(attrs)
    np.testing.assert_allclose(img_comb, [1e-5, -np.inf, 2e-6])
    assert prov == "param_combine_wf_chan.img_comb"
    tpd = cal.find_tpd(attrs)
    np.testing.assert_allclose(tpd, [1e-6, 1e-5])
    imgs = cal.find_imgs(attrs)
    assert len(imgs) == 2
    tpds, consistent = cal.tpd_for_images(imgs, tpd)
    assert tpds == [1e-6, 1e-5]
    assert consistent


# ---------------------------------------------------------------------------
# Check 2 (saturation) tests
# ---------------------------------------------------------------------------

def _population(n=20000, clip=None, seed=0):
    rng = np.random.default_rng(seed)
    r = 10 ** rng.uniform(np.log10(300), np.log10(4000), n)
    p = 20.0 - 20.0 * np.log10(r) + rng.normal(0, 2.0, n)
    if clip is not None:
        p = np.minimum(p, clip)
    return p, r


def test_clean_population_no_plateau():
    p, r = _population()
    fit = cal.fit_ceiling(p, r)
    assert fit["status"] == cal.NO_PLATEAU
    margins = cal.ceiling_margin(p, fit)
    assert np.all(np.isnan(margins))


def test_fully_clipped_population():
    p, r = _population(clip=-45.0)
    # clip at -45: unsaturated envelope is ~-30 at 300m, ~-52 at 4km -> flat
    # over most of the range
    fit = cal.fit_ceiling(p, r)
    assert fit["status"] == cal.FIT_OK
    assert fit["level"] == pytest.approx(-45.0, abs=1.0)
    assert fit["pileup_fraction"] > 0.05
    margins = cal.ceiling_margin(p, fit)
    assert np.isfinite(margins).all()
    assert np.nanmin(margins) >= -1.5


def test_partially_clipped_population():
    p, r = _population(clip=-52.0)
    # envelope crosses -52 dB around r ~ 2km: plateau below, slope beyond
    fit = cal.fit_ceiling(p, r)
    assert fit["status"] == cal.FIT_OK
    assert fit["level"] == pytest.approx(-52.0, abs=1.0)


def test_insufficient_support():
    p, r = _population(n=200)
    fit = cal.fit_ceiling(p, r)
    assert fit["status"] == cal.INSUFFICIENT_SUPPORT
    assert np.all(np.isnan(cal.ceiling_margin(p, fit)))


def test_two_regime_step_not_misfit():
    # Two flat levels with a cliff between (two gain/altitude regimes, as in
    # 2014/2018 Greenland P3) must not be reported as a single-season plateau:
    # the second segment does not fall like an unsaturated envelope.
    rng = np.random.default_rng(1)
    n = 20000
    r = 10 ** rng.uniform(np.log10(300), np.log10(4000), n)
    p = np.where(r < 1000, -31.0, -53.0) + rng.normal(0, 1.5, n)
    fit = cal.fit_ceiling(p, r)
    assert fit["status"] != cal.FIT_OK
