"""Spec sections 2-4: material spec -> physics, per-rope fault analysis, waveform frame lengths."""
import numpy as np
import pytest

from elevcare_ml import materials as M, physics as P, rope_report as R
from elevcare_ml.features import single_reading_features
from elevcare_ml.signal import synthesize, to_frame, waveform_features


def test_presets_autofill_and_gsm_rule():
    rope = M.apply_preset("rope", "8x19S-FC")
    assert rope["material_grade"].startswith("1570/1770") and rope["gsm"] == "Not applicable"
    belt = M.apply_preset("rope", "PU-belt")
    assert belt["gsm"] == 2400 and M.gsm_applicable("rope", belt["material_type"])
    assert not M.gsm_applicable("bearing", "anything")
    brake = M.apply_preset("traction", "brake")
    assert brake["gsm"] == 4200


def test_resolve_marks_user_vs_default_and_changes_physics():
    d = M.resolve("rope", None)
    assert all(i["source"] == "default" for i in d["inputs"]) and d["completeness_pct"] == 0
    u = M.resolve("rope", {"breaking_load_kn": 72, "rope_diameter_mm": 10, "sheave_diameter_mm": 480, "rope_count": 5})
    assert u["params"] == {"rope_breaking_load_kn": 72.0, "d_over_d": 48.0, "ropes_per_set": 5}
    # a stronger rope on a bigger sheave lasts longer (Feyrer)
    assert P.rope_cycles_to_failure(8.64, params=u["params"]) > P.rope_cycles_to_failure(8.64)
    # harder sheave wears slower (Archard), higher C gives longer L10, class H tolerates more heat
    s = M.resolve("sheave", {"hardness_hb": 300})["params"]
    assert P.sheave_wear_increment(1000, 1.0, s) < P.sheave_wear_increment(1000, 1.0)
    b = M.resolve("bearing", {"dynamic_rating_c_kn": 380})["params"]
    assert P.bearing_l10_increment(1000, 1.0, b) < P.bearing_l10_increment(1000, 1.0)
    h = M.resolve("traction", {"rows": [{"material_grade": "Insulation class H"}]})["params"]
    assert h["insulation_limit_c"] == 180
    assert P.traction_damage_increment(1000, 1.0, 30.0, h) < P.traction_damage_increment(1000, 1.0, 30.0)
    assert P.life_increment("rope", 100, 1.0, None, u["params"]) < P.life_increment("rope", 100, 1.0)


def test_completeness_and_blank_fields():
    spec = {"material_type": "x", "material_grade": "y"}
    assert 0 < M.completeness("sheave", spec) < 100
    assert "coating" in M.blank_fields("sheave", spec)
    assert M.completeness("traction", {"rows": [dict.fromkeys(M.FIELD_KEYS, "v")]}) == 100


def _history(pattern, imbalance_pct, days=30, growth=0.0, k_per_day=4):
    t = np.arange(days * k_per_day) / k_per_day
    imb = imbalance_pct - growth * (t[-1] - t)
    base = 8.64
    x = base * (1.0 + np.asarray(pattern)[None, :] * imb[:, None] / 100.0)
    return t, x + np.random.default_rng(0).normal(0, 0.02, x.shape)


def test_rope_analysis_flags_the_overloaded_rope_and_reads_thresholds():
    pattern = np.array([-0.2, -0.2, -0.2, -0.2, -0.2, 1.0])
    t, x = _history(pattern, 19.0, growth=0.24)
    r = R.analyse_ropes(t, x)
    assert r["verdict"] == "fault" and r["faulty"] == ["R6"]
    assert r["worst"]["id"] == "R6" and 17 < r["worst"]["deviation_pct"] < 21
    assert r["worst"]["wear_multiplier"] == pytest.approx((1.19) ** P.ROPE_FEYRER_B, rel=0.05)
    assert r["worst"]["days_to_limit"] == 0.0
    assert r["limits"]["watch_pct"] == P.THRESHOLDS["rope"]["rope_deviation_pct"].healthy
    # a 7 % rope trending up is "watch", and days-to-limit follows the trend
    t, x = _history(pattern, 7.0, growth=0.3)
    r = R.analyse_ropes(t, x)
    assert r["verdict"] == "no fault" and r["ropes"][5]["status"] == "watch"
    assert r["worst"]["days_to_limit"] == pytest.approx((10 - 7) / 0.3, rel=0.25)
    t, x = _history(pattern, 2.0)
    assert all(rp["status"] == "healthy" for rp in R.analyse_ropes(t, x)["ropes"])
    series = R.daily_series(t, x, 30)
    assert len(series) == 30 and len(series[0]["tensions"]) == 6


@pytest.mark.parametrize("n", [64, 256, 512])
def test_waveform_features_accept_any_frame_length(n):
    rng = np.random.default_rng(1)
    wave = synthesize("rope", np.array([0.2]), rng)[0]              # 256 samples, low-frequency sway
    src = np.linspace(0, 1, 256)
    resampled = np.interp(np.linspace(0, 1, n), src, wave)          # same signal, n samples
    assert to_frame(resampled).shape == (1, P.WAVEFORM_SAMPLES)
    f = waveform_features("rope", resampled)
    ref = waveform_features("rope", wave)
    assert f["vib_rms"][0] == pytest.approx(ref["vib_rms"][0], rel=0.3)
    reading = {"tension": 8.6, "rope_tensions": None, "vibration_waveform": list(resampled), "motor_current": 24.0, "temperature": 35.0}
    assert set(single_reading_features("bearing", reading)) >= {"vib_rms", "vib_kurtosis", "load_factor"}
    with pytest.raises(ValueError):
        to_frame(np.zeros(16))
