"""
Energy model for a traction elevator.

    energy = standby_kW * hours + trips * kWh_per_trip * (1 + degradation_penalty)

Degradation penalty: worn components cost energy - a dragging bearing or
brake adds friction, a worn sheave causes rope slip (wasted motor work), and
an ageing motor loses efficiency. The penalty is driven by each component's
*physics severity* (0 = healthy, 1 = at alarm limit), so the cost dashboard
shows the money wasted by running degraded equipment.
"""

from __future__ import annotations

STANDBY_KW = 0.12          # controller, car lighting and fan while idle
KWH_PER_TRIP = 0.035       # mid-rise traction lift, average trip incl. regen losses

PENALTY_WEIGHTS = {"traction": 0.15, "sheave": 0.10, "bearing": 0.06, "rope": 0.05}


def degradation_penalty(severity_by_type: dict[str, float]) -> float:
    return sum(PENALTY_WEIGHTS.get(t, 0.0) * min(1.2, max(0.0, s)) for t, s in severity_by_type.items())


def interval_energy(hours: float, trips: float, severity_by_type: dict[str, float]) -> tuple[float, float]:
    """Returns (actual_kwh, baseline_kwh_if_healthy)."""
    standby = STANDBY_KW * hours
    motion = trips * KWH_PER_TRIP
    return standby + motion * (1.0 + degradation_penalty(severity_by_type)), standby + motion
