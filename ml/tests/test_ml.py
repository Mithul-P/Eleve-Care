import numpy as np
import pytest

from elevcare_ml import physics as P
from elevcare_ml import simulator as S
from elevcare_ml.features import reading_features, window_features
from elevcare_ml.signal import synthesize, waveform_features


def test_gaussian_noise_has_kurtosis_three():
    rng = np.random.default_rng(0)
    f = waveform_features("bearing", rng.normal(0, 0.05, size=(200, 256)))
    assert np.median(f["vib_kurtosis"]) == pytest.approx(3.0, abs=0.3)


def test_bearing_impacts_raise_kurtosis_and_band_energy():
    rng = np.random.default_rng(1)
    healthy = waveform_features("bearing", synthesize("bearing", np.full(50, 0.01), rng))
    damaged = waveform_features("bearing", synthesize("bearing", np.full(50, 0.6), rng))
    assert np.median(damaged["vib_kurtosis"]) > np.median(healthy["vib_kurtosis"]) + 1.5
    assert np.median(damaged["fault_band_ratio"]) > np.median(healthy["fault_band_ratio"])


def test_basquin_overload_accelerates_fatigue():
    base = P.miner_increment("rope", 1000, 1.0)
    over = P.miner_increment("rope", 1000, 1.2)
    assert over / base == pytest.approx(1.2 ** P.BASQUIN_EXPONENT["rope"])


def test_damage_grows_monotonically_and_imbalance_widens():
    rng = np.random.default_rng(2)
    prof = S.random_profiles("rope", 1, rng, imbalance_growth_pct_per_day=0.2)
    state = S.initial_state(prof, rng, damage=0.1, imbalance_pct=3.0)
    damages, imbalances = [], []
    for _ in range(200):
        S.advance(prof, state, 24, rng)
        damages.append(state.damage[0])
        imbalances.append(state.imbalance_pct[0])
    assert np.all(np.diff(damages) >= 0)
    assert imbalances[-1] > imbalances[0] + 30


def test_rope_imbalance_is_observable_in_tensions():
    rng = np.random.default_rng(3)
    prof = S.random_profiles("rope", 1, rng)
    state = S.initial_state(prof, rng, damage=0.3, imbalance_pct=15.0)
    obs = S.observe(prof, state, 12.0, rng)
    feats = reading_features("rope", obs)
    assert feats["tension_imbalance_pct"][0] == pytest.approx(15.0, abs=1.5)


def test_window_features_trend():
    t = np.arange(0, 7, 0.25)
    per = {k: np.zeros_like(t) for k in P.FEATURE_NAMES}
    per["vib_rms"] = 0.05 + 0.002 * t
    per["tension_imbalance_pct"] = 3 + 0.5 * t
    f = window_features(t, per, fatigue_index=0.4)
    assert f["rms_trend"] == pytest.approx(0.002, rel=1e-6)
    assert f["imbalance_trend"] == pytest.approx(0.5, rel=1e-6)
    assert f["fatigue_index"] == 0.4


def _feats(ctype, **overrides):
    th = P.THRESHOLDS[ctype]
    f = {k: 0.0 for k in P.FEATURE_NAMES}
    f.update({k: t.healthy * 0.9 for k, t in th.items()})
    f.update(overrides)
    return f


def test_predictor_outputs_and_status(predictor):
    good = predictor.predict("rope", _feats("rope"))
    bad = predictor.predict("rope", _feats("rope", tension_imbalance_pct=20.0, vib_rms=0.14, fatigue_index=0.9,
                                           imbalance_trend=0.3, fault_band_ratio=0.95))
    for p in (good, bad):
        assert 0 <= p["health_score"] <= 100
        assert 0 <= p["failure_probability"] <= 100
        assert p["rul_days"] >= 0
        assert p["status"] in ("good", "medium", "at-risk")
    assert good["status"] == "good"
    assert bad["health_score"] < good["health_score"]
    assert bad["status"] != "good"
    assert bad["contributing_factors"][0]["feature"] in P.THRESHOLDS["rope"]
    assert "Recommended" in bad["explanation"]


def test_model_is_monotonic_in_degradation(predictor):
    """Monotonic constraints: worsening one indicator never lowers failure risk."""
    probs = [predictor.predict("bearing", _feats("bearing", vib_kurtosis=k))["failure_probability"]
             for k in (3.0, 4.0, 5.0, 6.0, 8.0)]
    assert all(b >= a for a, b in zip(probs, probs[1:]))
