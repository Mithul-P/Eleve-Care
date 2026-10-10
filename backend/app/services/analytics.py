"""
Cost, energy and sustainability analytics.

All monetary and carbon figures are *indicative* and every assumption is
returned in the API response so the dashboard can show it (no hidden magic).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..config import Settings
from ..db import utcnow
from .ml import get_predictor

RANGE_DAYS = {"week": 7, "month": 30, "quarter": 90}

# Indicative maintenance economics (INR) for a mid-rise traction lift in India.
# "emergency" includes call-out, expedited parts, overtime and secondary damage.
MAINTENANCE_COST_INR = {
    "rope": {"planned": 180_000, "emergency": 320_000, "downtime_planned_h": 8, "downtime_emergency_h": 48},
    "sheave": {"planned": 120_000, "emergency": 220_000, "downtime_planned_h": 10, "downtime_emergency_h": 60},
    "bearing": {"planned": 25_000, "emergency": 90_000, "downtime_planned_h": 4, "downtime_emergency_h": 36},
    "traction": {"planned": 350_000, "emergency": 650_000, "downtime_planned_h": 16, "downtime_emergency_h": 96},
}

# Embodied carbon of replacement parts (kg CO2e): mass x material factor.
EMBODIED_CO2_KG = {
    "rope": {"mass_kg": 220, "factor": 2.1, "material": "galvanised steel wire"},
    "sheave": {"mass_kg": 300, "factor": 1.9, "material": "cast iron"},
    "bearing": {"mass_kg": 18, "factor": 2.5, "material": "bearing steel"},
    "traction": {"mass_kg": 450, "factor": 3.5, "material": "steel, copper, magnets"},
}

# Time-based maintenance replaces parts conservatively (~75% of typical life);
# condition-based replacement lets them run to ~95% safely because wear is measured.
TIME_BASED_REPLACEMENT_FRACTION = 0.75
CONDITION_BASED_REPLACEMENT_FRACTION = 0.95

CIRCULAR_ECONOMY_NOTES = [
    {"title": "Regroove, don't replace, sheaves",
     "text": "Early detection of groove wear allows re-grooving the sheave in place, avoiding ~570 kg CO2e of new cast iron."},
    {"title": "Rope steel recycling",
     "text": "Retired galvanised ropes are ~100% recyclable steel; segregated collection keeps them out of mixed scrap."},
    {"title": "Bearing remanufacturing",
     "text": "Bearings replaced at the first sign of spalling (kurtosis rise) can often be remanufactured instead of scrapped, and prevent secondary damage to shafts."},
    {"title": "Motor rewinding",
     "text": "Catching winding over-temperature early allows rewinding traction motors rather than replacing the whole machine."},
    {"title": "Right-time replacement",
     "text": "Condition-based replacement uses more of every part's real life than calendar-based swaps, cutting part consumption and transport emissions."},
]


def _since(range_: str) -> str:
    days = RANGE_DAYS.get(range_, 30)
    return (utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")


async def energy_report(db, s: Settings, range_: str = "month", elevator_id: str | None = None) -> dict:
    q: dict = {"date": {"$gte": _since(range_)}}
    if elevator_id:
        q["elevator_id"] = elevator_id
    logs = [x async for x in db.energy_logs.find(q)]
    by_day: dict[str, dict] = defaultdict(lambda: {"energy_kwh": 0.0, "baseline_kwh": 0.0, "cost": 0.0, "trips": 0.0,
                                                   "hours": 0.0, "n": 0})
    by_elev: dict[str, dict] = defaultdict(lambda: {"energy_kwh": 0.0, "baseline_kwh": 0.0, "cost": 0.0, "trips": 0.0})
    for x in logs:
        for agg in (by_day[x["date"]], by_elev[x["elevator_id"]]):
            agg["energy_kwh"] += x["energy_kwh"]
            agg["baseline_kwh"] += x["baseline_kwh"]
            agg["cost"] += x["cost"]
            agg["trips"] += x["trip_count"]
        by_day[x["date"]]["hours"] += x.get("hours", 24.0)
        by_day[x["date"]]["n"] += 1
    tariff = s.energy_tariff_inr_per_kwh
    # A day is complete when every elevator logged ~24 h. Partial days (today, the first
    # seeded day) stay in the totals but are left out of daily charts and averages.
    complete = {day for day, d in by_day.items() if d["hours"] >= 23.0 * max(1, d["n"])}
    daily = []
    for day in sorted(complete):
        d = by_day[day]
        daily.append({"date": day, "energy_kwh": round(d["energy_kwh"], 2), "cost_inr": round(d["cost"], 2),
                      "baseline_kwh": round(d["baseline_kwh"], 2),
                      "excess_kwh": round(d["energy_kwh"] - d["baseline_kwh"], 3),
                      "excess_cost_inr": round((d["energy_kwh"] - d["baseline_kwh"]) * tariff, 2),
                      "trips": round(d["trips"])})
    total_kwh = sum(d["energy_kwh"] for d in by_day.values())
    base_kwh = sum(d["baseline_kwh"] for d in by_day.values())
    trips = sum(d["trips"] for d in by_day.values())
    n_days = max(1, len(complete))
    full_kwh = sum(by_day[d]["energy_kwh"] for d in complete)
    full_excess = sum(by_day[d]["energy_kwh"] - by_day[d]["baseline_kwh"] for d in complete)
    names = {e["_id"]: e["name"] async for e in db.elevators.find({}, {"name": 1})}
    per_elevator = sorted([
        {"elevator_id": k, "name": names.get(k, k), "energy_kwh": round(v["energy_kwh"], 1),
         "cost_inr": round(v["cost"], 0), "excess_kwh": round(v["energy_kwh"] - v["baseline_kwh"], 2),
         "excess_pct": round(100 * (v["energy_kwh"] - v["baseline_kwh"]) / max(v["baseline_kwh"], 1e-9), 2),
         "kwh_per_trip": round(v["energy_kwh"] / max(v["trips"], 1), 4)}
        for k, v in by_elev.items()], key=lambda r: -r["excess_pct"])
    excess_kwh = total_kwh - base_kwh
    return {
        "range": range_,
        "tariff_inr_per_kwh": tariff,
        "daily": daily,
        "per_elevator": per_elevator,
        "totals": {
            "days": len(by_day),
            "energy_kwh": round(total_kwh, 1),
            "cost_inr": round(total_kwh * tariff, 0),
            "baseline_kwh": round(base_kwh, 1),
            "excess_kwh": round(excess_kwh, 2),
            "excess_cost_inr": round(excess_kwh * tariff, 0),
            "complete_days": len(complete),
            "avg_daily_kwh": round(full_kwh / n_days, 2),
            "avg_daily_cost_inr": round(full_kwh * tariff / n_days, 1),
            "kwh_per_trip": round(total_kwh / max(trips, 1), 4),
            "projected_annual_cost_inr": round(full_kwh * tariff / n_days * 365, 0),
            "projected_annual_excess_cost_inr": round(full_excess * tariff / n_days * 365, 0),
        },
        "assumptions": [
            f"Tariff Rs {tariff:g} per kWh (unit).",
            "Energy = standby 0.12 kW + 0.035 kWh per trip x (1 + degradation penalty).",
            "Degradation penalty from physics severity: traction 15%, sheave 10%, bearing 6%, ropes 5% at alarm level.",
            "Excess = consumption above the same trips run on healthy components - recoverable by maintenance.",
            "Daily charts and averages use complete days only; the current partial day counts in totals.",
        ],
    }


async def maintenance_savings(db, elevator_id: str | None = None) -> dict:
    """Planned (predictive) vs emergency (reactive) cost for flagged components."""
    q: dict = {"status": {"$in": ["at-risk", "medium"]}}
    if elevator_id:
        q["elevator_id"] = elevator_id
    names = {e["_id"]: e["name"] async for e in db.elevators.find({}, {"name": 1})}
    rows = []
    async for c in db.components.find(q):
        cost = MAINTENANCE_COST_INR[c["type"]]
        p = (c.get("failure_probability") or 0.0) / 100.0
        rows.append({
            "component_id": c["_id"], "elevator": names.get(c["elevator_id"]), "component": c["name"],
            "status": c["status"], "failure_probability": c.get("failure_probability"),
            "rul_days": c.get("rul_days"),
            "planned_cost_inr": cost["planned"], "emergency_cost_inr": cost["emergency"],
            "avoided_cost_inr": cost["emergency"] - cost["planned"],
            "risk_weighted_avoided_inr": round(max(p, 0.1) * (cost["emergency"] - cost["planned"]), 0),
            "downtime_avoided_h": cost["downtime_emergency_h"] - cost["downtime_planned_h"],
        })
    rows.sort(key=lambda r: -(r["failure_probability"] or 0))
    return {
        "components": rows,
        "total_avoided_inr": sum(r["avoided_cost_inr"] for r in rows),
        "risk_weighted_avoided_inr": sum(r["risk_weighted_avoided_inr"] for r in rows),
        "downtime_avoided_h": sum(r["downtime_avoided_h"] for r in rows),
        "assumptions": [
            "Planned vs emergency costs are indicative INR figures for a mid-rise traction lift "
            "(emergency includes call-out, expedited parts, overtime and secondary damage).",
            "Risk-weighted saving = max(30-day failure probability, 10%) x (emergency - planned).",
        ],
        "cost_table": MAINTENANCE_COST_INR,
    }


async def sustainability_report(db, s: Settings, range_: str = "month") -> dict:
    energy = await energy_report(db, s, range_)
    t = energy["totals"]
    ef = s.grid_emission_kg_per_kwh
    n_days = max(1, t["complete_days"])
    full_kwh = sum(d["energy_kwh"] for d in energy["daily"])
    full_excess = sum(d["excess_kwh"] for d in energy["daily"])
    counts: dict[str, int] = defaultdict(int)
    async for c in db.components.find({}, {"type": 1}):
        counts[c["type"]] += 1

    metrics = get_predictor().metrics
    life_rows = []
    total_avoided_parts = 0.0
    total_avoided_co2 = 0.0
    for ctype, n in counts.items():
        life_days = (metrics.get(ctype) or {}).get("median_unit_life_days") or 1500
        life_y = life_days / 365.0
        tb_interval = life_y * TIME_BASED_REPLACEMENT_FRACTION
        cb_interval = life_y * CONDITION_BASED_REPLACEMENT_FRACTION
        parts_tb = n / tb_interval
        parts_cb = n / cb_interval
        emb = EMBODIED_CO2_KG[ctype]
        co2_per_part = emb["mass_kg"] * emb["factor"]
        avoided = (parts_tb - parts_cb)
        total_avoided_parts += avoided
        total_avoided_co2 += avoided * co2_per_part
        life_rows.append({
            "type": ctype, "units": n, "typical_life_years": round(life_y, 1),
            "life_extension_pct": round(100 * (CONDITION_BASED_REPLACEMENT_FRACTION / TIME_BASED_REPLACEMENT_FRACTION - 1), 1),
            "replacements_per_year_time_based": round(parts_tb, 2),
            "replacements_per_year_condition_based": round(parts_cb, 2),
            "embodied_co2_per_part_kg": round(co2_per_part, 0),
            "avoided_co2_kg_per_year": round(avoided * co2_per_part, 0),
            "material": emb["material"],
        })
    return {
        "range": range_,
        "grid_emission_kg_per_kwh": ef,
        "operational": {
            "energy_kwh": t["energy_kwh"],
            "co2_kg": round(t["energy_kwh"] * ef, 1),
            "excess_co2_kg": round(t["excess_kwh"] * ef, 2),
            "projected_annual_co2_kg": round(full_kwh / n_days * 365 * ef, 0),
            "projected_annual_excess_co2_kg": round(full_excess / n_days * 365 * ef, 1),
            "daily": [{"date": d["date"], "co2_kg": round(d["energy_kwh"] * ef, 2),
                       "excess_co2_kg": round(d["excess_kwh"] * ef, 3)} for d in energy["daily"]],
        },
        "life_extension": {
            "components": sorted(life_rows, key=lambda r: r["type"]),
            "avoided_replacements_per_year": round(total_avoided_parts, 2),
            "avoided_embodied_co2_kg_per_year": round(total_avoided_co2, 0),
        },
        "circular_economy": CIRCULAR_ECONOMY_NOTES,
        "assumptions": [
            f"Grid emission factor {ef} kg CO2/kWh (India grid average, CEA CO2 baseline database).",
            f"Time-based maintenance replaces parts at {TIME_BASED_REPLACEMENT_FRACTION:.0%} of typical life; "
            f"condition-based at {CONDITION_BASED_REPLACEMENT_FRACTION:.0%} (wear is measured, not assumed).",
            "Typical life per component type = median simulated run-to-failure life from the model training set.",
            "Embodied carbon = part mass x material emission factor (indicative cradle-to-gate values).",
        ],
    }
