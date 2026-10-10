"""
Physics constants and physics-informed reasoning for elevator suspension components.

Everything in this module is deliberately *explainable*: each constant has a
comment saying where the number comes from and why it matters, so it can be
walked through live with judges.

Scope (by design): ropes, sheave/pulley, bearings, traction machine.
Buffers, safety gear, structural items, wiring and hoistway conditions are
out of scope and stay with technician inspection.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

COMPONENT_TYPES = ("rope", "sheave", "bearing", "traction")

# ---------------------------------------------------------------------------
# Fatigue model (Basquin + Palmgren-Miner)
# ---------------------------------------------------------------------------
# Steel wire ropes and rolling bearings both fail by *fatigue*: every trip
# bends the rope over the sheave and loads the bearing once. Basquin's law says
# cycles-to-failure scale with load as N ~ L^-m, so a rope carrying 20% more
# than its share wears out ~(1.2)^m times faster. Palmgren-Miner's rule
# sums the fractional damage of every cycle: D = sum(n_i / N_i). Failure ~ D = 1.
#
# RATED_CYCLES: trips at rated load before end-of-life, per component type.
# These are indicative values for a mid-rise traction elevator, chosen so that
# typical service lives land in the 3-6 year range at ~500 trips/day.
RATED_CYCLES = {
    "rope": 1_800_000,
    "sheave": 2_400_000,
    "bearing": 2_000_000,
    "traction": 2_800_000,
}

# Basquin exponent m. Wire rope bending fatigue is commonly modelled with
# m ~ 4-5; rolling bearings follow the L10 life equation with p = 3 (ball)
# or 10/3 (roller). Sheave groove wear and motor winding ageing are
# less load-sensitive, so they get smaller exponents.
BASQUIN_EXPONENT = {
    "rope": 4.5,
    "sheave": 3.0,
    "bearing": 3.33,
    "traction": 2.0,
}

# Paris-law style acceleration: once damage has started (e.g. broken wires,
# spalling on a bearing race) it grows faster. dD/dn = base_rate * (1 + k*D).
DAMAGE_ACCELERATION = 2.5

# ---------------------------------------------------------------------------
# Rated operating values
# ---------------------------------------------------------------------------
ROPES_PER_SET = 6               # typical 6-rope 1:1 traction suspension
RATED_ROPE_TENSION_KN = 12.0    # per-rope rated working tension
NOMINAL_LOAD_FRACTION = 0.72    # ropes normally run at ~70-75% of rated tension
RATED_MOTOR_CURRENT_A = 38.0    # traction motor rated current
NOMINAL_CURRENT_FRACTION = 0.62 # healthy running current / rated
AMBIENT_TEMP_C = 28.0           # machine room ambient (Indian climate)

# Vibration sampling for the (simulated) accelerometer
SAMPLE_RATE_HZ = 1000
WAVEFORM_SAMPLES = 256

# Characteristic defect frequencies (Hz) used for FFT band-energy features.
# rope: low-frequency rope/car sway; sheave: groove-pass frequency;
# bearing: outer-race defect frequency (BPFO) + structural resonance;
# traction: electrical line frequency and its 2x harmonic (50 Hz grid).
FAULT_BANDS_HZ = {
    "rope": (4.0, 16.0),
    "sheave": (20.0, 60.0),
    "bearing": (250.0, 400.0),
    "traction": (90.0, 110.0),
}

# ---------------------------------------------------------------------------
# Condition thresholds (healthy value, alarm value) per feature
# ---------------------------------------------------------------------------
# Severity of each feature is scaled 0 (at "healthy") to 1 (at "alarm").
# Sources / reasoning:
#   * tension_imbalance_pct: rope standards and OEM guidance commonly require
#     individual rope tensions within about +/-5-10% of the set average;
#     uneven load sharing is the #1 cause of premature rope wear.
#   * vib_kurtosis: a healthy vibration signal is ~Gaussian (kurtosis 3);
#     impulsive bearing defects push it well above 6.
#   * temperature_rise: bearing/motor temperature > ~35 C above ambient
#     indicates lubrication breakdown or winding overload.
#   * motor_current_ratio: running at >95% of rated current in normal service
#     means friction/slip is eating the motor's margin.
#   * fatigue_index: Palmgren-Miner cumulative damage, 1.0 = rated life used.


@dataclass(frozen=True)
class Threshold:
    healthy: float
    alarm: float
    unit: str
    label: str


THRESHOLDS: dict[str, dict[str, Threshold]] = {
    "rope": {
        "tension_imbalance_pct": Threshold(3.0, 10.0, "%", "Rope tension imbalance"),
        "tension_deviation_pct": Threshold(2.0, 5.0, "%", "Mean tension deviation"),
        "vib_rms": Threshold(0.055, 0.12, "g", "Vibration RMS"),
        "fault_band_ratio": Threshold(0.7, 0.92, "", "Rope-sway band energy"),
        "fatigue_index": Threshold(0.5, 1.0, "", "Fatigue life consumed"),
        "rms_trend": Threshold(0.0, 0.001, "g/day", "Vibration growth rate"),
        "imbalance_trend": Threshold(0.0, 0.25, "%/day", "Imbalance growth rate"),
    },
    "sheave": {
        "vib_rms": Threshold(0.045, 0.11, "g", "Vibration RMS"),
        "fault_band_ratio": Threshold(0.55, 0.9, "", "Groove-pass band energy"),
        "motor_current_ratio": Threshold(0.64, 0.8, "x rated", "Motor current (slip)"),
        "fatigue_index": Threshold(0.5, 1.0, "", "Groove wear life consumed"),
        "rms_trend": Threshold(0.0, 0.001, "g/day", "Vibration growth rate"),
    },
    "bearing": {
        "vib_kurtosis": Threshold(3.3, 6.5, "", "Vibration kurtosis (impacts)"),
        "vib_crest": Threshold(3.2, 4.0, "", "Vibration crest factor"),
        "fault_band_ratio": Threshold(0.28, 0.48, "", "Bearing-defect band energy"),
        "temperature_rise": Threshold(12.0, 28.0, "C", "Temperature rise over ambient"),
        "vib_rms": Threshold(0.04, 0.12, "g", "Vibration RMS"),
        "fatigue_index": Threshold(0.5, 1.0, "", "L10 life consumed"),
        "rms_trend": Threshold(0.0, 0.001, "g/day", "Vibration growth rate"),
    },
    "traction": {
        "motor_current_ratio": Threshold(0.64, 0.85, "x rated", "Motor current"),
        "temperature_rise": Threshold(15.0, 28.0, "C", "Winding temperature rise"),
        "fault_band_ratio": Threshold(0.45, 0.8, "", "Electrical-frequency band energy"),
        "vib_rms": Threshold(0.042, 0.1, "g", "Vibration RMS"),
        "fatigue_index": Threshold(0.5, 1.0, "", "Insulation/brake life consumed"),
        "rms_trend": Threshold(0.0, 0.001, "g/day", "Vibration growth rate"),
    },
}

# Human-readable meaning + recommended action when a factor dominates.
FACTOR_ADVICE: dict[str, dict[str, str]] = {
    "rope": {
        "tension_imbalance_pct": "Uneven load sharing - the most-loaded rope wears exponentially faster (Basquin). Re-tension the rope set and inspect the most-loaded rope for broken wires.",
        "tension_deviation_pct": "Mean tension has drifted from commissioning value - check for rope stretch or counterweight change.",
        "vib_rms": "Rope/car sway energy rising - check guide shoes and rope tension equalisation.",
        "fault_band_ratio": "Low-frequency sway dominates the spectrum - typical of slack or unequal ropes.",
        "fatigue_index": "A large share of rated bending cycles has been used - plan rope replacement.",
        "rms_trend": "Vibration is growing week on week - degradation is active, not static.",
        "imbalance_trend": "Tension imbalance is widening day by day - intervene before the next inspection cycle.",
    },
    "sheave": {
        "vib_rms": "Sheave vibration rising - inspect groove profile for wear/undercutting.",
        "fault_band_ratio": "Groove-pass frequency dominates - classic worn/uneven groove signature.",
        "motor_current_ratio": "Motor drawing extra current - possible rope slip in worn grooves reducing traction.",
        "fatigue_index": "Sheave approaching expected groove-wear life - schedule regrooving or replacement.",
        "rms_trend": "Vibration trend increasing - wear is progressing.",
    },
    "bearing": {
        "vib_kurtosis": "Impulsive impacts detected (kurtosis >> 3) - consistent with race or rolling-element spalling.",
        "vib_crest": "High crest factor - early-stage localised bearing defect.",
        "fault_band_ratio": "Energy concentrated at bearing-defect resonance - inspect and plan replacement.",
        "temperature_rise": "Bearing running hot - check lubrication; heat accelerates wear.",
        "vib_rms": "Overall vibration rising - bearing damage spreading.",
        "fatigue_index": "Large fraction of L10 life consumed - replace at next planned stop.",
        "rms_trend": "Vibration trend increasing - defect is growing.",
    },
    "traction": {
        "motor_current_ratio": "Motor current above normal - increased friction, brake drag or winding degradation.",
        "temperature_rise": "Winding temperature elevated - insulation ages ~2x faster per 10 C (Arrhenius rule).",
        "fault_band_ratio": "Electrical-frequency vibration rising - possible rotor eccentricity or drive imbalance.",
        "vib_rms": "Machine vibration rising - check mounts, alignment and brake.",
        "fatigue_index": "Machine approaching expected service life - plan overhaul.",
        "rms_trend": "Vibration trend increasing.",
    },
}

# Feature order used by the ML models (same vector for every component type;
# each type's model simply learns which features matter for it).
FEATURE_NAMES = [
    "tension_imbalance_pct",
    "tension_deviation_pct",
    "vib_rms",
    "vib_peak",
    "vib_kurtosis",
    "vib_crest",
    "fault_band_ratio",
    "motor_current_ratio",
    "temperature_rise",
    "fatigue_index",
    "rms_trend",
    "imbalance_trend",
]

# Physics-severity weights per component type (sum to 1). These express
# *engineering* priorities and are blended with the ML probability to form the
# health score, so the score never depends on the ML model alone.
SEVERITY_WEIGHTS = {
    "rope": {"tension_imbalance_pct": 0.35, "fatigue_index": 0.2, "vib_rms": 0.15,
             "imbalance_trend": 0.15, "fault_band_ratio": 0.1, "tension_deviation_pct": 0.05},
    "sheave": {"vib_rms": 0.3, "fault_band_ratio": 0.2, "motor_current_ratio": 0.25,
               "fatigue_index": 0.2, "rms_trend": 0.05},
    "bearing": {"vib_kurtosis": 0.3, "temperature_rise": 0.2, "fault_band_ratio": 0.15,
                "fatigue_index": 0.15, "vib_rms": 0.1, "vib_crest": 0.1},
    "traction": {"motor_current_ratio": 0.3, "temperature_rise": 0.3, "fatigue_index": 0.2,
                 "vib_rms": 0.1, "fault_band_ratio": 0.1},
}

FAILURE_HORIZON_DAYS = 30  # classifier predicts P(failure within 30 days)


def severity(component_type: str, feature: str, value: float) -> float:
    """0 at the healthy value, 1 at the alarm value, clipped to [0, 1.5]."""
    th = THRESHOLDS[component_type].get(feature)
    if th is None:
        return 0.0
    span = th.alarm - th.healthy
    if span == 0:
        return 0.0
    return float(min(1.5, max(0.0, (value - th.healthy) / span)))


def miner_increment(component_type: str, cycles: float, load_factor: float) -> float:
    """
    Palmgren-Miner damage increment for `cycles` trips at `load_factor`
    (load / nominal load). Basquin: N(L) = N_rated * L^-m, so damage per
    cycle = L^m / N_rated.
    """
    m = BASQUIN_EXPONENT[component_type]
    return cycles * (np.maximum(load_factor, 0.05) ** m) / RATED_CYCLES[component_type]
