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
# Approved degradation models - one per component (spec section 2)
# ---------------------------------------------------------------------------
# Every component is modelled with the standard, published law for its failure
# mode. All four share one shape - "cycles to failure" N(L) at load level L
# and Palmgren-Miner accumulation D = sum(n_i / N_i) - which is why one
# accumulator (`life_increment`) serves the simulator, the training set and
# the live ingestion pipeline: the physics cannot drift between them.
#
#   rope     Feyrer wire-rope bending fatigue    N_f = C (D/d)^a (S_u/S)^b
#   sheave   Archard groove wear                 Q   = k W L / H
#   bearing  Lundberg-Palmgren L10 life          L10 = (C/P)^p  [10^6 rev]
#   traction Palmgren-Miner duty-cycle fatigue   D   = sum(n_i / N_i)
#            + Arrhenius thermal ageing          rate x 2 per +10 C winding rise
#
# RATED_CYCLES: trips at nominal load before end-of-life, per component type.
# Indicative values for a mid-rise traction elevator, chosen so that typical
# service lives land in the 3-6 year range at ~500 trips/day. Each model
# below is *calibrated* so that N(L = 1) == RATED_CYCLES.
RATED_CYCLES = {
    "rope": 1_800_000,
    "sheave": 2_400_000,
    "bearing": 2_000_000,
    "traction": 2_800_000,
}

# Load exponent m of each model, i.e. N(L) ~ L^-m. The four laws give:
#   rope     b (Feyrer tension exponent, 4-5 for steel wire rope)
#   sheave   1 (Archard: wear volume is linear in load) - but see the contact
#            pressure feedback in `sheave_wear_increment`
#   bearing  p (10/3 for roller, 3 for ball bearings)
#   traction 2 (empirical duty-cycle exponent for winding/brake wear)
BASQUIN_EXPONENT = {
    "rope": 4.5,
    "sheave": 1.0,
    "bearing": 10.0 / 3.0,
    "traction": 2.0,
}

# Paris-law style acceleration: once damage has started (e.g. broken wires,
# spalling on a bearing race) it grows faster. dD/dn = base_rate * (1 + k*D).
DAMAGE_ACCELERATION = 2.5

# --- 2.1 Wire rope: Feyrer  N_f = C (D/d)^a (S_u/S)^b -------------------------
# D/d  sheave-to-rope diameter ratio; EN 81-20 requires D/d >= 40 for traction
# S_u  rope minimum breaking load (10 mm 8x19 Seale steel rope ~ 60 kN)
# S    operating tension per rope
# a, b empirical exponents (a ~ 4-5 for D/d, b ~ 4-5 for tension) - Feyrer,
#      "Wire Ropes: Tension, Endurance, Reliability", Springer.
ROPE_D_OVER_D = 40.0
ROPE_BREAKING_LOAD_KN = 60.0
ROPE_FEYRER_A = 4.5
ROPE_FEYRER_B = 4.5

# --- 2.2 Sheave: Archard  Q = k W L / H ----------------------------------------
# k  dimensionless wear coefficient - lubricated steel rope on cast iron is
#    in the 1e-7 range (Archard k spans 1e-8 unlubricated-mild to 1e-4 severe);
#    1.4e-7 makes the rated life coincide with the 1.5 mm groove-wear limit
# W  normal load on the groove (N); L sliding distance (m); H hardness (Pa)
# Groove wear depth h = Q / A_groove. End of life when h reaches the allowable
# groove wear (re-groove / replace limit).
SHEAVE_WEAR_COEFF_K = 1.4e-7
SHEAVE_HARDNESS_PA = 2.2e9          # cast iron GG25, ~220 HB
SHEAVE_GROOVE_AREA_M2 = 1.8e-3      # rope-groove contact strip per sheave
SHEAVE_SLIDE_PER_TRIP_M = 0.35      # creep/slip distance per trip
SHEAVE_NOMINAL_LOAD_N = 52_000.0    # 6 ropes x 8.64 kN, summed on the groove
SHEAVE_WEAR_LIMIT_M = 1.5e-3        # 1.5 mm groove wear -> regroove
# Contact-pressure feedback (spec 2.2): wear *rate* rises once the rope-sheave
# contact pressure exceeds a threshold (feedback with rope tension imbalance).
SHEAVE_PRESSURE_THRESHOLD = 1.05    # x nominal contact pressure
SHEAVE_PRESSURE_GAIN = 3.0

# --- 2.3 Bearing: Lundberg-Palmgren  L10 = (C/P)^p  x 10^6 revolutions --------
# C  basic dynamic load rating; P equivalent dynamic load; p = 10/3 (roller)
# Sheave bearings on a gearless machine are typically spherical roller bearings.
BEARING_C_KN = 300.0
BEARING_P_NOMINAL_KN = 100.0
BEARING_P_EXPONENT = 10.0 / 3.0
BEARING_REVS_PER_TRIP = 20.0        # ~30 m travel over a 0.5 m sheave

# --- 2.4 Traction machine: Palmgren-Miner + Arrhenius --------------------------
TRACTION_DUTY_EXPONENT = 2.0
TRACTION_RATED_TEMP_RISE_C = 15.0   # winding rise at which the rated life applies
TRACTION_ARRHENIUS_DOUBLING_C = 10.0  # "10-degree rule" for insulation ageing


# ---------------------------------------------------------------------------
# Material-specification parameters (spec section 2). Every law below takes an
# optional `params` dict resolved from the component's material_spec by
# materials.resolve(); a missing key falls back to the module constant.
# ---------------------------------------------------------------------------
PARAM_DEFAULTS = {
    "rope_breaking_load_kn": ROPE_BREAKING_LOAD_KN,
    "d_over_d": ROPE_D_OVER_D,
    "sheave_hardness_pa": SHEAVE_HARDNESS_PA,
    "bearing_c_kn": BEARING_C_KN,
    "rated_temp_rise_c": TRACTION_RATED_TEMP_RISE_C,
}


def param(params: dict | None, key: str) -> float:
    v = (params or {}).get(key)
    return float(v) if v is not None else PARAM_DEFAULTS[key]


def rope_cycles_to_failure(tension_kn, d_over_d: float | None = None, breaking_load_kn: float | None = None,
                           params: dict | None = None) -> np.ndarray:
    """
    Feyrer: N_f = C (D/d)^a (S_u/S)^b.  C is calibrated so that the rope at
    its nominal working tension lasts RATED_CYCLES bending cycles at the
    *default* geometry; a stronger rope or a larger sheave lasts longer.
    """
    s = np.maximum(np.asarray(tension_kn, dtype=float), 0.05)
    dd = d_over_d if d_over_d is not None else param(params, "d_over_d")
    su = breaking_load_kn if breaking_load_kn is not None else param(params, "rope_breaking_load_kn")
    nominal = RATED_ROPE_TENSION_KN * NOMINAL_LOAD_FRACTION
    c = RATED_CYCLES["rope"] / (ROPE_D_OVER_D ** ROPE_FEYRER_A * (ROPE_BREAKING_LOAD_KN / nominal) ** ROPE_FEYRER_B)
    return c * dd ** ROPE_FEYRER_A * (su / s) ** ROPE_FEYRER_B


def rope_fatigue_increment(cycles, load_factor, params: dict | None = None) -> np.ndarray:
    """Miner increment for a rope set: cycles / N_f(most-loaded rope tension)."""
    nominal = RATED_ROPE_TENSION_KN * NOMINAL_LOAD_FRACTION
    return np.asarray(cycles, float) / rope_cycles_to_failure(np.asarray(load_factor, float) * nominal, params=params)


def sheave_wear_depth(cycles, load_factor=1.0, params: dict | None = None) -> np.ndarray:
    """
    Archard: Q = k W L / H  (wear volume, m^3); depth h = Q / A_groove.
    Returns groove wear depth in metres for `cycles` trips at `load_factor`.
    """
    w = SHEAVE_NOMINAL_LOAD_N * np.asarray(load_factor, float)
    dist = SHEAVE_SLIDE_PER_TRIP_M * np.asarray(cycles, float)
    return SHEAVE_WEAR_COEFF_K * w * dist / param(params, "sheave_hardness_pa") / SHEAVE_GROOVE_AREA_M2


def sheave_wear_increment(cycles, load_factor, params: dict | None = None) -> np.ndarray:
    """
    Wear-life fraction consumed: h / h_limit, calibrated to RATED_CYCLES at
    nominal load, with the contact-pressure feedback of spec 2.2: above the
    pressure threshold the wear rate grows by SHEAVE_PRESSURE_GAIN per unit
    of excess pressure (the worn groove concentrates load, which wears faster).
    """
    lf = np.maximum(np.asarray(load_factor, float), 0.05)
    # the wear *limit* is a geometry constant, so the rated life is at default hardness
    base = sheave_wear_depth(cycles, lf, params) / sheave_wear_depth(RATED_CYCLES["sheave"], 1.0)
    feedback = 1.0 + SHEAVE_PRESSURE_GAIN * np.maximum(0.0, lf - SHEAVE_PRESSURE_THRESHOLD)
    return base * feedback


def bearing_l10_life(c_kn: float = BEARING_C_KN, p_kn=BEARING_P_NOMINAL_KN, p: float = BEARING_P_EXPONENT) -> np.ndarray:
    """Lundberg-Palmgren: L10 = (C/P)^p million revolutions at 90% reliability."""
    return (c_kn / np.maximum(np.asarray(p_kn, float), 0.05)) ** p


def bearing_l10_increment(cycles, load_factor, params: dict | None = None) -> np.ndarray:
    """Fraction of L10 life consumed by `cycles` trips at `load_factor`, calibrated to RATED_CYCLES at the default rating C."""
    l10_revs = bearing_l10_life(param(params, "bearing_c_kn"), p_kn=BEARING_P_NOMINAL_KN * np.asarray(load_factor, float)) * 1e6
    l10_nominal = bearing_l10_life() * 1e6
    scale = RATED_CYCLES["bearing"] * BEARING_REVS_PER_TRIP / l10_nominal
    return np.asarray(cycles, float) * BEARING_REVS_PER_TRIP / (l10_revs * scale)


def traction_damage_increment(cycles, load_factor, temperature_rise=None, params: dict | None = None) -> np.ndarray:
    """
    Palmgren-Miner over the duty cycle, N(L) = N_rated * L^-m, times an
    Arrhenius factor: insulation life halves per +10 C above the rated rise.
    """
    lf = np.maximum(np.asarray(load_factor, float), 0.05)
    d = np.asarray(cycles, float) * lf ** TRACTION_DUTY_EXPONENT / RATED_CYCLES["traction"]
    if temperature_rise is not None:
        excess = np.maximum(0.0, np.asarray(temperature_rise, float) - param(params, "rated_temp_rise_c"))
        d = d * 2.0 ** (excess / TRACTION_ARRHENIUS_DOUBLING_C)
    return d


PHYSICS_MODEL = {
    "rope": {
        "name": "Feyrer wire-rope bending fatigue",
        "formula": "N_f = C (D/d)^a (S_u/S)^b",
        "feature": "fatigue_fraction",
        "label": "Fatigue life consumed",
        "note": "cumulative bending cycles / cycles-to-failure of the most-loaded rope",
        "what_it_is": "Prof. Klaus Feyrer's wire-rope bending fatigue formulation, modeling cyclic reverse bending and tensile fatigue across traction sheaves and diverter pulleys.",
        "what_it_measures": "Fraction of cumulative bending fatigue cycles consumed relative to the endurance limit under observed rope tensile stress.",
        "where_it_exists": "Physics engine (rope_fatigue_increment), simulator (simulated_source.py), ingestion pipeline, and R6 rope tension fault detection.",
        "component_using_it": "Suspension ropes (6 x 10 mm high-carbon steel wire ropes)",
        "project_data_feeding_it": "Individual rope load cell tensions (R1..R6), elevator trip counts, car travel distance, D/d ratio, and minimum breaking load (MBL).",
        "contribution_to_prediction": "Produces fatigue_fraction which anchors RUL estimation, inputs monotonic gradient boosted trees, and triggers rules R-FAT01 / R-IMB01.",
    },
    "sheave": {
        "name": "Archard groove wear",
        "formula": "Q = k W L / H",
        "feature": "wear_depth_proxy",
        "label": "Groove wear life consumed",
        "note": "groove wear depth / allowable wear, with contact-pressure feedback",
        "what_it_is": "John F. Archard's sliding wear equation relating volume loss of material to normal contact force, sliding distance, and surface hardness.",
        "what_it_measures": "Progressive groove depth loss (mm) and undercut profile deformation caused by micro-slip during traction acceleration and deceleration.",
        "where_it_exists": "Physics engine (sheave_wear_increment), simulator (simulated_source.py), and material specification system (materials.py).",
        "component_using_it": "Traction sheave (ductile cast iron EN-GJS-500-7 / hardened alloy cast iron)",
        "project_data_feeding_it": "Rope travel distance, motor trips, sheave hardness H (HB 210 / 2.06 GPa), pitch diameter D, and radial rope pressure.",
        "contribution_to_prediction": "Produces wear_depth_proxy, directly determining physical groove severity, blending 50% into sheave health score, and bounding RUL.",
    },
    "bearing": {
        "name": "Lundberg-Palmgren L10 bearing life",
        "formula": "L10 = (C/P)^p",
        "feature": "l10_life_fraction",
        "label": "L10 life consumed",
        "note": "cumulative revolutions / L10 at the observed load",
        "what_it_is": "ISO 281 / Lundberg-Palmgren rolling contact fatigue life theory calculating 90% survival probability (L10 rating life) under dynamic load.",
        "what_it_measures": "Accumulated shaft revolutions relative to calculated L10 fatigue life before sub-surface micro-spalling or raceway flaking occurs.",
        "where_it_exists": "Physics engine (bearing_l10_increment), simulator (simulated_source.py), and bearing vibration defect band analyzer.",
        "component_using_it": "Sheave bearing (spherical/deep-groove roller bearing assembly)",
        "project_data_feeding_it": "Equivalent dynamic radial load P, shaft rotational speed (rpm), operating hours, and basic dynamic rating C (120 kN).",
        "contribution_to_prediction": "Produces l10_life_fraction which scales baseline failure hazard and cross-validates accelerometer defect-band acoustic spikes.",
    },
    "traction": {
        "name": "Palmgren-Miner duty-cycle fatigue + Arrhenius ageing",
        "formula": "D = sum(n_i / N_i)",
        "feature": "cumulative_damage_D",
        "label": "Cumulative damage D",
        "note": "duty-cycle damage, doubled per +10 C winding rise",
        "what_it_is": "Linear Palmgren-Miner cumulative cycle damage rule combined with Svante Arrhenius chemical reaction rate kinetics for stator insulation degradation.",
        "what_it_measures": "Mechanical start/stop stress damage combined with accelerated thermal dielectric ageing of stator winding insulation (Montsinger 10°C rule).",
        "where_it_exists": "Physics engine (traction_damage_increment), motor temperature watcher, and simulator (simulated_source.py).",
        "component_using_it": "Traction machine (permanent-magnet gearless synchronous motor)",
        "project_data_feeding_it": "Motor start/stop count, operating current ratio (transducer), and stator winding temperature rise over ambient (°C).",
        "contribution_to_prediction": "Calculates cumulative_damage_D to enforce thermal over-temperature penalties, driving motor RUL and Rule R-TMP01.",
    },
}


def life_increment(component_type: str, cycles, load_factor, temperature_rise=None, params: dict | None = None) -> np.ndarray:
    """
    Life-fraction consumed by `cycles` trips at `load_factor` (load / nominal),
    dispatched to the approved model of the component type. This is THE
    accumulator used everywhere (simulator, training, live pipeline).
    `params` carries the component's material-spec physics inputs (optional).
    """
    if component_type == "rope":
        return rope_fatigue_increment(cycles, load_factor, params)
    if component_type == "sheave":
        return sheave_wear_increment(cycles, load_factor, params)
    if component_type == "bearing":
        return bearing_l10_increment(cycles, load_factor, params)
    if component_type == "traction":
        return traction_damage_increment(cycles, load_factor, temperature_rise, params)
    raise ValueError(f"unknown component type {component_type}")


def miner_increment(component_type: str, cycles: float, load_factor: float) -> float:
    """Palmgren-Miner damage increment (back-compat alias for `life_increment`)."""
    return life_increment(component_type, cycles, load_factor)


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
        # per-rope status in the lift report: healthy < healthy%, watch up to alarm%, faulty >= alarm%
        "rope_deviation_pct": Threshold(5.0, 10.0, "%", "Per-rope tension deviation from set average"),
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

# Per-rope status: a rope is "Faulty" if it is above the alarm deviation for this many consecutive readings
ROPE_FAULT_PERSISTENCE = 3

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
