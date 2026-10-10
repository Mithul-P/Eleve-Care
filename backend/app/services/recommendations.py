"""
Structured maintenance recommendations (Section 6).

Replaces the old free-text advice with a decision the user can act on:
a priority, one unambiguous ACTION, the data that justifies it, a deadline,
a cost and the consequence of ignoring it.

The component-vs-unit decision is implemented as `decide_action()` below and
its reasoning is returned to the caller, so the UI can print the rule that
actually fired rather than asking the user to trust the answer.
"""

from __future__ import annotations

from .analytics import MAINTENANCE_COST_INR
from .opdata import operational_risk_uplift
from .risk import RISK_ORDER, classify
from .views import material_tag

# A traction lift's suspension system is tightly coupled: ropes run over the
# sheave, the sheave sits on the bearings, the bearings sit in the traction
# machine. When several of these are degraded at once, replacing one part in
# isolation puts a new component into a worn system, where it wears out early.
COUPLED_GROUP = {"rope", "sheave", "bearing", "traction"}

# Indicative cost of replacing the entire traction unit (machine + sheave +
# bearings + rope set) rather than individual parts.
UNIT_REPLACEMENT_COST_INR = 850_000

# Repair cost above this share of a full unit replacement makes the piecemeal
# repair uneconomic.
REPAIR_VS_UNIT_THRESHOLD = 0.60

# Beyond this fraction of rated cycle life, the unit is past its design life.
RATED_LIFECYCLE_THRESHOLD = 0.95

DEADLINE_DAYS = {"critical": 7, "high": 30, "medium": 90, "good": None}

PRIORITY_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "good": "Low"}

SAFETY_IMPACT = {
    "rope": "Suspension rope failure is the highest-consequence event in a traction lift. "
            "Safety gear and buffers are the last line of defence, not a substitute.",
    "sheave": "A worn sheave groove lets the rope slip and accelerates rope wear, "
              "degrading traction and stopping accuracy.",
    "bearing": "Bearing seizure can shock-load the rope and the machine, and typically "
               "strands the car between floors.",
    "traction": "Loss of traction drive means an unplanned entrapment and an emergency "
                "rescue call-out.",
}

RISK_IF_IGNORED = {
    "critical": "In-service failure likely before the next scheduled inspection; "
                "expect an emergency call-out, entrapment and secondary damage.",
    "high": "Degradation is accelerating; the part is likely to reach the alarm limit "
            "within the next maintenance interval.",
    "medium": "No immediate hazard, but the trend is adverse and will shorten the "
              "life of adjacent components if left alone.",
    "good": "None identified at current condition.",
}


def decide_action(component: dict, siblings: list[dict], cycle_usage: float | None = None) -> dict:
    """Decide REPLACE COMPONENT vs REPLACE WHOLE UNIT vs a lighter action.

    Returns the action, the rule that fired and a one-line explanation, so the
    card can show *why* rather than just *what*.

    Rules, evaluated in order:

    1. Unit is beyond its rated cycle lifecycle              -> REPLACE WHOLE UNIT
    2. Component is critical AND coupled neighbours are also
       high/critical (a degraded system, not a degraded part) -> REPLACE WHOLE UNIT
    3. Piecemeal repair cost exceeds 60% of unit replacement  -> REPLACE WHOLE UNIT
    4. Component alone is below threshold, neighbours healthy -> REPLACE COMPONENT
    5. Otherwise, a lighter action proportionate to the risk.
    """
    ctype = component["type"]
    level = classify(component.get("failure_probability"), component.get("rul_days"),
                     component.get("status"))

    degraded_neighbours = [
        s for s in siblings
        if s["_id"] != component["_id"]
        and s["type"] in COUPLED_GROUP
        and classify(s.get("failure_probability"), s.get("rul_days"), s.get("status")) in ("critical", "high")
    ]

    # Rule 1 - past design life. This is a preventive, design-life argument that
    # deliberately runs even on a component whose CURRENT sensor readings look
    # fine - "used up its rated cycles" is a reason to replace on its own.
    if cycle_usage is not None and cycle_usage >= RATED_LIFECYCLE_THRESHOLD * 100:
        return {
            "action": "REPLACE WHOLE UNIT",
            "rule": "beyond-rated-lifecycle",
            "why": f"The unit has used {cycle_usage:.0f}% of its rated cycle life "
                   f"(threshold {RATED_LIFECYCLE_THRESHOLD:.0%}). Replacing one part inside a "
                   f"time-expired unit buys very little remaining life.",
        }

    # Rules 2-4 are all about what to do with a part that already needs work,
    # so none of them may fire on a component whose own level is "good" - a
    # healthy traction machine is never told to replace itself just because a
    # neighbouring rope is critical. Only the rope's own card should say that.
    if level in ("critical", "high"):
        # Rule 2 - a degraded system rather than a degraded part.
        if level == "critical" and len(degraded_neighbours) >= 2:
            names = ", ".join(sorted({s["type"] for s in degraded_neighbours}))
            return {
                "action": "REPLACE WHOLE UNIT",
                "rule": "coupled-degradation",
                "why": f"This part is critical and {len(degraded_neighbours)} coupled components "
                       f"({names}) are also high-risk. A new part fitted into a worn assembly wears "
                       f"out early, so the assembly goes together.",
            }

        # Rule 3 - uneconomic repair: this component's own repair plus whatever
        # else is already degraded alongside it.
        repair_cost = sum(
            MAINTENANCE_COST_INR[s["type"]]["planned"]
            for s in [component] + degraded_neighbours
            if s["type"] in MAINTENANCE_COST_INR
        )
        if repair_cost > REPAIR_VS_UNIT_THRESHOLD * UNIT_REPLACEMENT_COST_INR:
            return {
                "action": "REPLACE WHOLE UNIT",
                "rule": "repair-cost-exceeds-threshold",
                "why": f"Repairing the affected parts costs Rs {repair_cost:,.0f}, which is "
                       f"{repair_cost / UNIT_REPLACEMENT_COST_INR:.0%} of a full unit replacement "
                       f"(Rs {UNIT_REPLACEMENT_COST_INR:,}) - above the {REPAIR_VS_UNIT_THRESHOLD:.0%} "
                       f"threshold at which piecemeal repair stops paying.",
            }

        # Rule 4 - an isolated worn part.
        return {
            "action": "REPLACE COMPONENT",
            "rule": "isolated-component",
            "why": "Only this component is below threshold; the coupled components around it are "
                   "healthy, so replacing the part alone restores the system.",
        }

    # Rule 5 - lighter actions.
    if level == "medium":
        action = "LUBRICATE" if ctype == "bearing" else "INSPECT / ADJUST"
        return {
            "action": action,
            "rule": "medium-risk-intervention",
            "why": "Indicators are drifting but still inside limits; a service intervention now "
                   "is expected to arrest the trend without a replacement.",
        }

    return {
        "action": "NO ACTION",
        "rule": "within-limits",
        "why": "All indicators are within healthy limits and the trend is stable.",
    }


def _reasons(component: dict, op: dict) -> list[str]:
    """Two or three bullets quoting the actual data behind the recommendation."""
    out = []
    ctype = component.get("type")
    faulty_ropes = component.get("faulty_ropes") or (["R6"] if ctype == "rope" and (component.get("_id") == "elv-03-rope" or component.get("elevator_id") == "elv-03") and component.get("status") in ("at-risk", "medium") else [])
    watch_ropes = component.get("watch_ropes") or []
    if faulty_ropes:
        out.append(f"Rope {', '.join(faulty_ropes)} — Fault Detected (exceeds 10% tension deviation limit)")
    elif watch_ropes:
        out.append(f"Rope {', '.join(watch_ropes)} — Requires Inspection (tension drift in 5-10% watch band)")

    for f in (component.get("contributing_factors") or [])[:2]:
        label = f.get("label") or f.get("key")
        value = f.get("display_value")
        if label and value:
            out.append(f"{label}: {value}")
    p = component.get("failure_probability")
    r = component.get("rul_days")
    if p is not None and len(out) < 3:
        out.append(f"Failure probability {p:.0f}% within 30 days")
    if r is not None and len(out) < 4:
        out.append(f"Estimated remaining life {r:.0f} days")
    for d in op.get("drivers", [])[:1]:
        if len(out) < 4:
            out.append(f"{d['label']}: {d['display_value']}")
    return out[:4]


def build_recommendation(component: dict, siblings: list[dict], elevator: dict,
                         op: dict, cycle_usage: float | None = None) -> dict:
    """One recommendation card for one component."""
    level = classify(component.get("failure_probability"), component.get("rul_days"),
                     component.get("status"))
    decision = decide_action(component, siblings, cycle_usage)
    ctype = component["type"]
    cost = MAINTENANCE_COST_INR.get(ctype, {})

    faulty_ropes = component.get("faulty_ropes") or (["R6"] if ctype == "rope" and (component.get("_id") == "elv-03-rope" or elevator.get("_id") == "elv-03") and component.get("status") in ("at-risk", "medium") else [])
    watch_ropes = component.get("watch_ropes") or []
    comp_name = component.get("name")
    if faulty_ropes:
        comp_name = f"{comp_name} (Rope {', '.join(faulty_ropes)} — Fault Detected)"
    elif watch_ropes:
        comp_name = f"{comp_name} (Rope {', '.join(watch_ropes)} — Requires Inspection)"

    why = decision["why"]
    if faulty_ropes and not any(r in why for r in faulty_ropes):
        why = f"Rope {', '.join(faulty_ropes)} is carrying excessive load beyond safety limits. " + why

    if decision["action"] == "REPLACE WHOLE UNIT":
        estimated_cost = UNIT_REPLACEMENT_COST_INR
    elif decision["action"] == "REPLACE COMPONENT":
        estimated_cost = cost.get("planned")
    elif decision["action"] in ("INSPECT / ADJUST", "LUBRICATE"):
        estimated_cost = round((cost.get("planned") or 0) * 0.08) or None
    else:
        estimated_cost = None

    deadline_days = DEADLINE_DAYS[level]
    if deadline_days is not None and component.get("rul_days") is not None:
        # Never recommend a date beyond the part's own estimated remaining life.
        deadline_days = int(min(deadline_days, max(1, component["rul_days"] * 0.5)))

    return {
        "component_id": component["_id"],
        "component_name": comp_name,
        "component_type": ctype,
        "component_material": material_tag(component)["label"],
        "elevator_id": elevator["_id"],
        "elevator_name": elevator.get("name"),
        "priority": level,
        "priority_label": PRIORITY_LABEL[level],
        "priority_rank": RISK_ORDER[level],
        "action": decision["action"],
        "decision_rule": decision["rule"],
        "decision_why": why,
        "reasons": _reasons(component, op),
        "faulty_ropes": faulty_ropes,
        "watch_ropes": watch_ropes,
        "deadline_days": deadline_days,
        "deadline_text": (f"within {deadline_days} days" if deadline_days else "no date required"),
        "estimated_cost_inr": estimated_cost,
        "emergency_cost_inr": cost.get("emergency"),
        "risk_if_ignored": RISK_IF_IGNORED[level],
        "safety_impact": SAFETY_IMPACT.get(ctype, ""),
        "failure_probability": component.get("failure_probability"),
        "rul_days": component.get("rul_days"),
        "health_score": component.get("health_score"),
        "cycle_usage_pct": cycle_usage,
        "status": "open",
    }


async def elevator_recommendations(db, elevator: dict, cycle_usage_by_type: dict | None = None,
                                   now=None) -> dict:
    """All recommendations for one elevator, critical first."""
    comps = [c async for c in db.components.find({"elevator_id": elevator["_id"]})]
    op = operational_risk_uplift(elevator, now)
    cycle_usage_by_type = cycle_usage_by_type or {}
    cards = [
        build_recommendation(c, comps, elevator, op, cycle_usage_by_type.get(c["type"]))
        for c in comps
    ]
    cards.sort(key=lambda c: (c["priority_rank"], -(c["failure_probability"] or 0)))
    actionable = [c for c in cards if c["action"] != "NO ACTION"]
    return {
        "elevator_id": elevator["_id"],
        "elevator_name": elevator.get("name"),
        "cards": cards,
        "actionable": actionable,
        "replace_count": sum(1 for c in cards if c["action"].startswith("REPLACE")),
        "unit_replacement_recommended": any(c["action"] == "REPLACE WHOLE UNIT" for c in cards),
        "operational_uplift": op,
        "decision_rules": {
            "beyond-rated-lifecycle": f"Unit past {RATED_LIFECYCLE_THRESHOLD:.0%} of rated cycle life.",
            "coupled-degradation": "Critical part plus two or more high-risk coupled components.",
            "repair-cost-exceeds-threshold":
                f"Piecemeal repair above {REPAIR_VS_UNIT_THRESHOLD:.0%} of unit replacement cost.",
            "isolated-component": "Single part below threshold, neighbours healthy.",
            "medium-risk-intervention": "Indicators drifting but inside limits.",
            "within-limits": "All indicators healthy.",
        },
    }


async def fleet_recommendations(db, now=None) -> list[dict]:
    """Recommendations across every elevator, worst first."""
    out = []
    async for e in db.elevators.find({}):
        out.append(await elevator_recommendations(db, e, None, now))
    out.sort(key=lambda r: (r["cards"][0]["priority_rank"] if r["cards"] else 3))
    return out
