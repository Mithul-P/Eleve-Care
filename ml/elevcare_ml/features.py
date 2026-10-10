"""
Feature engineering shared by training and live inference.

Two stages:
1. `reading_features` - per sensor reading: tension statistics, waveform
   signal features, normalised motor current and temperature rise.
2. `window_features`  - per component at a point in time: robust 24 h medians
   of the per-reading features + 7-day trends + the cumulative fatigue index.

Using the *same* functions for the training set and the live API guarantees
that the model sees identically computed inputs in both places.
"""

from __future__ import annotations

import numpy as np

from . import physics as P
from .signal import waveform_features
from .simulator import NOMINAL_ROPE_TENSION_KN

LEVEL_WINDOW_DAYS = 1.0
TREND_WINDOW_DAYS = 7.0

PER_READING_KEYS = [
    "tension_imbalance_pct",
    "tension_deviation_pct",
    "vib_rms",
    "vib_peak",
    "vib_kurtosis",
    "vib_crest",
    "fault_band_ratio",
    "motor_current_ratio",
    "temperature_rise",
    "dominant_freq_hz",
    "load_factor",
]


def reading_features(component_type: str, obs: dict) -> dict[str, np.ndarray]:
    """
    Vectorised per-reading features. `obs` holds arrays (n,) and
    `vibration_waveform` (n, samples); `rope_tensions` (n, ropes) is optional.
    Waveform features are skipped when no waveform is supplied.
    """
    tension = np.atleast_1d(np.asarray(obs["tension"], dtype=float))
    ropes = obs.get("rope_tensions")
    if ropes is not None and len(np.atleast_1d(ropes)) > 0:
        ropes = np.atleast_2d(np.asarray(ropes, dtype=float))
        mean = ropes.mean(axis=1)
        imbalance = np.max(np.abs(ropes - mean[:, None]), axis=1) / np.maximum(mean, 1e-6) * 100.0
        most_loaded = ropes.max(axis=1)
    else:
        imbalance = np.zeros_like(tension)
        most_loaded = tension

    out = (waveform_features(component_type, obs["vibration_waveform"])
           if obs.get("vibration_waveform") is not None else {})
    out["tension_imbalance_pct"] = imbalance
    out["tension_deviation_pct"] = np.abs(tension - NOMINAL_ROPE_TENSION_KN) / NOMINAL_ROPE_TENSION_KN * 100.0
    out["motor_current_ratio"] = np.atleast_1d(np.asarray(obs["motor_current"], float)) / P.RATED_MOTOR_CURRENT_A
    out["temperature_rise"] = np.atleast_1d(np.asarray(obs["temperature"], float)) - P.AMBIENT_TEMP_C
    # Load factor on the component used by the fatigue (Miner) accumulator.
    ref = most_loaded if component_type == "rope" else tension
    out["load_factor"] = ref / NOMINAL_ROPE_TENSION_KN
    return out


def single_reading_features(component_type: str, reading: dict) -> dict[str, float]:
    obs = {
        "tension": [reading["tension"]],
        "rope_tensions": [reading["rope_tensions"]] if reading.get("rope_tensions") else None,
        "vibration_waveform": [reading["vibration_waveform"]],
        "motor_current": [reading["motor_current"]],
        "temperature": [reading["temperature"]],
    }
    feats = reading_features(component_type, obs)
    return {k: float(v[0]) for k, v in feats.items()}


def _slope_per_day(t: np.ndarray, y: np.ndarray) -> float:
    if len(t) < 3 or (t.max() - t.min()) < 0.75:
        return 0.0
    tc = t - t.mean()
    denom = float(np.sum(tc * tc))
    if denom <= 0:
        return 0.0
    return float(np.sum(tc * (y - y.mean())) / denom)


def window_features(times_days: np.ndarray, per_reading: dict[str, np.ndarray], fatigue_index: float) -> dict[str, float]:
    """
    Aggregate a component's recent readings into one model input.
    `times_days` (k,) ascending timestamps in days; `per_reading[key]` (k,).
    """
    t = np.asarray(times_days, dtype=float)
    now = t[-1]
    level_mask = t >= now - LEVEL_WINDOW_DAYS
    trend_mask = t >= now - TREND_WINDOW_DAYS

    feats: dict[str, float] = {}
    for key in P.FEATURE_NAMES:
        if key in per_reading:
            feats[key] = float(np.median(np.asarray(per_reading[key], float)[level_mask]))
    feats["fatigue_index"] = float(fatigue_index)
    feats["rms_trend"] = max(0.0, _slope_per_day(t[trend_mask], np.asarray(per_reading["vib_rms"], float)[trend_mask]))
    feats["imbalance_trend"] = max(0.0, _slope_per_day(
        t[trend_mask], np.asarray(per_reading["tension_imbalance_pct"], float)[trend_mask]))
    return feats


def to_vector(feats: dict[str, float]) -> np.ndarray:
    return np.array([feats.get(k, 0.0) for k in P.FEATURE_NAMES], dtype=float)
