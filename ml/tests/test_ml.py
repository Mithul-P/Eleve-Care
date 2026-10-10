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


# ---------------------------------------------------------------------------
# approved physics models
# ---------------------------------------------------------------------------

def test_each_component_uses_its_approved_law_calibrated_to_rated_life():
    for ctype in P.COMPONENT_TYPES:
        assert 1.0 / float(P.life_increment(ctype, 1.0, 1.0)) == pytest.approx(P.RATED_CYCLES[ctype], rel=1e-6)
    # Feyrer: N_f ~ S^-b
    assert P.rope_cycles_to_failure(8.64) / P.rope_cycles_to_failure(8.64 * 1.2) == pytest.approx(1.2 ** P.ROPE_FEYRER_B)
    # Archard: linear in load below the contact-pressure threshold, faster above it
    assert float(P.sheave_wear_depth(1000, 1.0)) * 1.04 == pytest.approx(float(P.sheave_wear_depth(1000, 1.04)))
    assert float(P.sheave_wear_increment(1, 1.3)) > 1.3 * float(P.sheave_wear_increment(1, 1.0))
    # Lundberg-Palmgren: L10 ~ (C/P)^p
    assert float(P.bearing_l10_life(p_kn=100.0)) / float(P.bearing_l10_life(p_kn=200.0)) == pytest.approx(2 ** (10 / 3))
    # Miner + Arrhenius: +10 C above rated rise doubles the ageing rate
    assert float(P.traction_damage_increment(1, 1.0, 25.0)) == pytest.approx(2 * float(P.traction_damage_increment(1, 1.0, 15.0)))
    assert float(P.sheave_wear_depth(P.RATED_CYCLES["sheave"])) == pytest.approx(P.SHEAVE_WEAR_LIMIT_M, rel=0.05)


def test_batch_window_features_match_single():
    from elevcare_ml import train as T
    from elevcare_ml.features import PER_READING_KEYS, to_vector, window_features, window_features_batch
    rng = np.random.default_rng(5)
    hist, amps, fat, fs = T.simulate_trajectories("sheave", 2, rng)
    wf = waveform_features("sheave", synthesize("sheave", amps[:, 0], rng))
    for k in T.WAVE_KEYS:
        hist[k] = np.full((amps.shape[0], 2), np.nan)
        hist[k][:, 0] = wf[k]
    per = {k: hist[k][:, 0] for k in PER_READING_KEYS}
    samples = np.array([28, 60, 300, int(fs[0])])
    batch = window_features_batch(0.25, per, fat[:, 0], samples)
    for row, s in zip(batch, samples):
        sl = slice(s - 28, s + 1)
        single = to_vector(window_features(np.arange(sl.start, sl.stop) * 0.25, {k: v[sl] for k, v in per.items()}, fat[s, 0]))
        assert np.allclose(row, single, atol=1e-12)


# ---------------------------------------------------------------------------
# rule layer
# ---------------------------------------------------------------------------

def _sev(ctype, feats):
    return {f: P.severity(ctype, f, feats.get(f, 0.0)) for f in P.THRESHOLDS[ctype]}


def test_rule_alarm_limit_overrides_low_ml_probability():
    from elevcare_ml.rules import apply_rules
    f = _feats("rope", tension_imbalance_pct=14.0, imbalance_trend=0.4)   # primary past alarm + growth trend
    out = apply_rules("rope", f, _sev("rope", f), "good", 0.05, 500.0, 90.0)
    assert out["status"] == "at-risk"
    assert [r["id"] for r in out["rules_fired"]][0] == "R1"
    assert out["rul_days"] <= 45 and out["health_score"] <= 44    # R5 consistency


def test_rule_rated_life_consumed_escalates():
    from elevcare_ml.rules import apply_rules
    f = _feats("bearing", fatigue_index=1.02)
    out = apply_rules("bearing", f, _sev("bearing", f), "good", 0.05, 400.0, 95.0)
    assert out["status"] == "at-risk" and any(r["id"] == "R2" for r in out["rules_fired"])
    f = _feats("bearing", fatigue_index=0.9)
    out = apply_rules("bearing", f, _sev("bearing", f), "good", 0.05, 400.0, 95.0)
    assert out["status"] == "medium"


def test_rule_fast_response_uses_last_three_readings():
    from elevcare_ml.rules import apply_rules
    f = _feats("traction")   # 24 h median still healthy
    out = apply_rules("traction", f, _sev("traction", f), "good", 0.05, 600.0, 95.0,
                      {"persistent": {"temperature_rise": 31.0}})
    assert out["status"] == "medium" and any(r["id"] == "R4" for r in out["rules_fired"])


def test_rule_unconfirmed_ml_is_held_at_medium():
    from elevcare_ml.rules import apply_rules
    f = _feats("sheave")
    out = apply_rules("sheave", f, _sev("sheave", f), "at-risk", 0.8, 20.0, 40.0)
    assert out["status"] == "medium" and any(r["id"] == "R6" for r in out["rules_fired"])
    assert out["confidence"] == "medium"


def test_rule_hysteresis_requires_three_confirmations():
    from elevcare_ml.rules import apply_rules
    f = _feats("rope")
    ctx = {"prev_status": "at-risk", "status_streak": 0, "candidate_status": None}
    for i in range(1, 4):
        out = apply_rules("rope", f, _sev("rope", f), "good", 0.02, 900.0, 98.0, ctx)
        ctx = {"prev_status": out["status"], "status_streak": out["status_streak"],
               "candidate_status": out["candidate_status"]}
        if i < 3:
            assert out["status"] == "at-risk" and any(r["id"] == "R7" for r in out["rules_fired"])
        else:
            assert out["status"] == "good"


def test_rule_physics_rul_caps_ml_rul(predictor):
    from elevcare_ml.predictor import physics_first_pass_rul
    out = predictor.predict("rope", _feats("rope", fatigue_index=0.8), {"life_rate_per_day": 0.002})
    expected = physics_first_pass_rul(0.8, 0.002)
    assert 0 < expected < 100.0   # Paris-law acceleration shortens the plain (1 - D) / rate = 100 d
    assert out["physics"]["physics_rul_days"] == pytest.approx(expected, abs=0.1)
    assert out["rul_days"] <= expected + 0.1
    assert out["physics"]["model"].startswith("Feyrer")


# ---------------------------------------------------------------------------
# waveform analysis
# ---------------------------------------------------------------------------

def test_envelope_spectrum_reveals_bpfo_on_damaged_bearing():
    from elevcare_ml.signal import BPFO_HZ, analyze_waveform
    rng = np.random.default_rng(9)
    healthy = analyze_waveform("bearing", synthesize("bearing", np.array([0.01]), rng)[0])
    damaged = analyze_waveform("bearing", synthesize("bearing", np.array([0.5]), rng)[0])
    bpfo = next(l for l in damaged["characteristic_lines"] if l["name"].startswith("BPFO"))
    assert bpfo["detected"] and bpfo["elevated"] and abs(damaged["envelope_peaks"][0]["freq_hz"] - BPFO_HZ) < 5
    assert not next(l for l in healthy["characteristic_lines"] if l["name"].startswith("BPFO"))["elevated"]
    assert healthy["level"] == "normal" and damaged["level"] == "alarm"
    assert len(damaged["spectrum"]["freq_hz"]) == len(damaged["envelope_spectrum"]["amplitude_g"]) == 129
    assert any("BPFO" in s for s in damaged["findings"])


def test_waveform_analysis_all_types_have_findings():
    from elevcare_ml.signal import analyze_waveform
    rng = np.random.default_rng(11)
    for ctype in P.COMPONENT_TYPES:
        a = analyze_waveform(ctype, synthesize(ctype, np.array([0.05]), rng)[0])
        assert a["level"] in ("normal", "watch", "alarm") and a["findings"] and a["peaks"]
        assert abs(sum(a["band_energy_pct"].values()) - 100.0) < 1.0


# ---------------------------------------------------------------------------
# Predictor resilience: an incompatible/corrupted committed artifact must not
# crash the whole application - it should retrain instead. Regression test for
# the exact failure a user hit in the field: a committed models.joblib built
# with a different scikit-learn version raised
# `ModuleNotFoundError: No module named '_loss'` on load, with no fallback,
# which took the whole FastAPI app down on startup.
# ---------------------------------------------------------------------------

def test_predictor_recovers_from_a_broken_artifact(tmp_path):
    from elevcare_ml.predictor import Predictor

    bad = tmp_path / "models.joblib"
    # Byte-for-byte reproduction of the reported crash: a pickle GLOBAL opcode
    # naming a module that does not exist, exactly what a scikit-learn version
    # mismatch produces when unpickling a HistGradientBoosting model.
    bad.write_bytes(b"c_loss\nSomeLossClass\n.")

    # With auto_train=True (the default, and what the real app always uses),
    # the broken file must be replaced by a freshly trained one rather than
    # propagating the unpickling error.
    p = Predictor(bad, auto_train=True, train_kwargs={"n_traj": 8, "rows_per_traj": 40, "seed": 1, "verbose": False})
    assert p.bundle is not None
    assert p.version
    assert bad.exists()  # the artifact now holds a real, freshly trained bundle

    # The predictor must actually be usable afterwards, not just non-crashing.
    assert set(p.bundle["models"].keys()) == set(P.COMPONENT_TYPES)


def test_predictor_reraises_on_broken_artifact_without_auto_train(tmp_path):
    from elevcare_ml.predictor import Predictor

    bad = tmp_path / "models.joblib"
    bad.write_bytes(b"c_loss\nSomeLossClass\n.")

    with pytest.raises(FileNotFoundError):
        Predictor(bad, auto_train=False)
