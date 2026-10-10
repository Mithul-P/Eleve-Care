"""
Physics-based degradation simulator for elevator suspension components.

Instead of random noise, every component carries a hidden *damage* variable
D in [0, 1] (1 = end of life) that evolves with use:

    dD = (L^m / N_rated) * trips / life_scale * (1 + k * D)
         `-- Basquin/Miner --'           `-- Paris-law acceleration

* L          load factor on the component (the most-loaded rope for a rope set)
* m          Basquin exponent (physics.BASQUIN_EXPONENT)
* life_scale per-unit manufacturing/installation variability (hidden)
* k          damage acceleration once defects have started

The *sensor readings* are generated from D (and, for ropes, the tension
imbalance), producing physically plausible signatures: widening rope
tension spread, rising vibration and impact content, creeping motor current
and temperature.

The same code produces (a) the historical seed data, (b) live batch and
real-time streams, and (c) run-to-failure trajectories to train the ML
models. All functions are vectorised over components (arrays of shape (n,)).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import physics as P
from .signal import synthesize

# Nominal per-rope tension when the car runs at its usual load.
NOMINAL_ROPE_TENSION_KN = P.RATED_ROPE_TENSION_KN * P.NOMINAL_LOAD_FRACTION

# Baseline temperature rise above ambient (C) of a healthy component in service.
BASE_TEMP_RISE = {"rope": 1.0, "sheave": 5.0, "bearing": 9.0, "traction": 11.0}


@dataclass
class Profile:
    """Static (per-unit) parameters. Arrays of shape (n,) unless noted."""

    component_type: str
    trips_per_day: np.ndarray
    life_scale: np.ndarray
    imbalance_growth_pct_per_day: np.ndarray
    temp_offset: np.ndarray
    current_offset: np.ndarray
    rope_pattern: np.ndarray  # (n, ROPES_PER_SET), zero mean, max = +1 on most-loaded rope

    @property
    def n(self) -> int:
        return int(self.trips_per_day.shape[0])


@dataclass
class State:
    """Evolving hidden state. Arrays of shape (n,)."""

    damage: np.ndarray
    imbalance_pct: np.ndarray
    cycle_count: np.ndarray
    fatigue_index: np.ndarray = field(default=None)  # Miner estimate from true load

    def __post_init__(self):
        if self.fatigue_index is None:
            self.fatigue_index = np.zeros_like(self.damage)


def _rope_pattern(rng: np.random.Generator, n: int) -> np.ndarray:
    p = rng.normal(size=(n, P.ROPES_PER_SET))
    p -= p.mean(axis=1, keepdims=True)
    # make the largest-magnitude deviation positive (the "most-loaded rope")
    idx = np.argmax(np.abs(p), axis=1)
    sign = np.sign(p[np.arange(n), idx])
    p *= sign[:, None]
    p /= np.max(p, axis=1, keepdims=True)
    return p


def random_profiles(component_type: str, n: int, rng: np.random.Generator, **overrides) -> Profile:
    prof = Profile(
        component_type=component_type,
        trips_per_day=rng.uniform(300, 750, n),
        life_scale=rng.lognormal(0.0, 0.25, n),
        imbalance_growth_pct_per_day=(
            rng.choice([0.0, 0.01, 0.03, 0.08, 0.2, 0.35], size=n, p=[0.3, 0.25, 0.2, 0.12, 0.08, 0.05])
            if component_type == "rope" else np.zeros(n)
        ),
        temp_offset=rng.normal(0.0, 1.0, n),
        current_offset=rng.normal(0.0, 0.015, n),
        rope_pattern=_rope_pattern(rng, n),
    )
    for k, v in overrides.items():
        setattr(prof, k, np.broadcast_to(np.asarray(v, dtype=float), getattr(prof, k).shape).copy())
    return prof


def initial_state(profile: Profile, rng: np.random.Generator, damage=None, imbalance_pct=None) -> State:
    n = profile.n
    return State(
        damage=np.zeros(n) if damage is None else np.broadcast_to(np.asarray(damage, float), (n,)).copy(),
        imbalance_pct=(rng.uniform(1.0, 4.0, n) if imbalance_pct is None
                       else np.broadcast_to(np.asarray(imbalance_pct, float), (n,)).copy()),
        cycle_count=np.zeros(n),
    )


def advance(profile: Profile, state: State, dt_hours: float, rng: np.random.Generator) -> np.ndarray:
    """Advance hidden state by dt_hours. Returns trips made in the interval."""
    ctype = profile.component_type
    n = profile.n
    dt_days = dt_hours / 24.0
    trips = np.maximum(0.0, profile.trips_per_day * dt_days * rng.normal(1.0, 0.1, n))
    passenger_load = rng.normal(1.0, 0.04, n)
    if ctype == "rope":
        # The most-loaded rope carries (1 + imbalance) x its fair share.
        load = passenger_load * (1.0 + state.imbalance_pct / 100.0)
    else:
        load = passenger_load
    m = P.BASQUIN_EXPONENT[ctype]
    base = trips * np.maximum(load, 0.05) ** m / P.RATED_CYCLES[ctype]
    d_damage = base / profile.life_scale * (1.0 + P.DAMAGE_ACCELERATION * state.damage)
    # A failed unit stays failed; clamp so run-to-failure loops stay finite.
    state.damage = np.minimum(state.damage + d_damage, 1.5)
    state.fatigue_index = state.fatigue_index + base
    state.cycle_count = state.cycle_count + trips
    if ctype == "rope":
        # Imbalance widens by installation drift (growth) and uneven wear.
        state.imbalance_pct = np.minimum(
            state.imbalance_pct + profile.imbalance_growth_pct_per_day * dt_days + 6.0 * d_damage, 40.0)
    return trips


def observe(profile: Profile, state: State, hour_of_day: float, rng: np.random.Generator,
            with_waveform: bool = True) -> dict[str, np.ndarray]:
    """
    Generate sensor readings (vectorised) from the current hidden state.
    `with_waveform=False` skips waveform synthesis and returns the signature
    amplitude instead (used to speed up training-set generation).
    """
    ctype = profile.component_type
    n = profile.n
    d = np.clip(state.damage, 0.0, 1.2)
    imb = state.imbalance_pct
    load_noise = rng.normal(1.0, 0.02, n)

    # --- rope tensions (kN) ---
    mean_t = NOMINAL_ROPE_TENSION_KN * load_noise
    if ctype == "rope":
        mean_t = mean_t * (1.0 - 0.04 * d)  # rope stretch relaxes tension slightly
        rope_imb = imb
    else:
        rope_imb = np.full(n, 2.0)
    rope_tensions = mean_t[:, None] * (1.0 + profile.rope_pattern * rope_imb[:, None] / 100.0)
    rope_tensions = rope_tensions + rng.normal(0.0, 0.03, rope_tensions.shape)

    # --- vibration signature amplitude ---
    if ctype == "rope":
        amp = 0.04 + 0.006 * imb + 0.08 * d**2
    elif ctype == "sheave":
        amp = 0.035 + 0.14 * d**2
    elif ctype == "bearing":
        amp = 0.01 + 0.9 * d**2.5
    else:
        amp = 0.03 + 0.12 * d**1.5
    wave = synthesize(ctype, amp, rng) if with_waveform else None

    # --- motor current (A): friction/slip/winding degradation add load ---
    sens = {
        "rope": 0.001 * imb,
        "sheave": 0.22 * d**2,
        "bearing": 0.04 * d,
        "traction": 0.30 * d**1.5,
    }[ctype]
    current = P.RATED_MOTOR_CURRENT_A * (P.NOMINAL_CURRENT_FRACTION * load_noise + profile.current_offset + sens)
    current = current + rng.normal(0.0, 0.2, n)

    # --- temperature (C): ambient daily cycle + base rise + degradation heat ---
    daily = 3.0 * np.sin(2 * np.pi * (hour_of_day - 9.0) / 24.0)
    heat = {"rope": 0.0 * d, "sheave": 3.0 * d, "bearing": 24.0 * d**2, "traction": 20.0 * d**1.5}[ctype]
    temp = P.AMBIENT_TEMP_C + daily + BASE_TEMP_RISE[ctype] + profile.temp_offset + heat + rng.normal(0, 0.6, n)

    return {
        "tension": rope_tensions.mean(axis=1),
        "rope_tensions": rope_tensions,
        "vibration_waveform": wave,
        "vib_amplitude": amp,
        "motor_current": current,
        "temperature": temp,
        "cycle_count": np.floor(state.cycle_count),
    }


def apply_maintenance(profile: Profile, state: State, idx: int | slice = slice(None)) -> None:
    """Replacement / overhaul: resets damage and re-tensions ropes."""
    state.damage[idx] = 0.02
    state.fatigue_index[idx] = 0.0
    state.imbalance_pct[idx] = 1.5
    profile.imbalance_growth_pct_per_day[idx] = 0.005


# ---------------------------------------------------------------------------
# Serialisation of a single component's simulator state (for MongoDB)
# ---------------------------------------------------------------------------

def to_doc(profile: Profile, state: State, i: int = 0) -> dict:
    return {
        "component_type": profile.component_type,
        "trips_per_day": float(profile.trips_per_day[i]),
        "life_scale": float(profile.life_scale[i]),
        "imbalance_growth_pct_per_day": float(profile.imbalance_growth_pct_per_day[i]),
        "temp_offset": float(profile.temp_offset[i]),
        "current_offset": float(profile.current_offset[i]),
        "rope_pattern": [float(x) for x in profile.rope_pattern[i]],
        "damage": float(state.damage[i]),
        "imbalance_pct": float(state.imbalance_pct[i]),
        "cycle_count": float(state.cycle_count[i]),
        "fatigue_index": float(state.fatigue_index[i]),
    }


def from_doc(doc: dict) -> tuple[Profile, State]:
    a = lambda k: np.array([float(doc[k])])  # noqa: E731
    profile = Profile(
        component_type=doc["component_type"],
        trips_per_day=a("trips_per_day"),
        life_scale=a("life_scale"),
        imbalance_growth_pct_per_day=a("imbalance_growth_pct_per_day"),
        temp_offset=a("temp_offset"),
        current_offset=a("current_offset"),
        rope_pattern=np.array([doc["rope_pattern"]], dtype=float),
    )
    state = State(
        damage=a("damage"),
        imbalance_pct=a("imbalance_pct"),
        cycle_count=a("cycle_count"),
        fatigue_index=a("fatigue_index"),
    )
    return profile, state
