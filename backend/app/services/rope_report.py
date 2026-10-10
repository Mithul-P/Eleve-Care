"""
Lift report - suspension rope fault section (spec section 3).
Reads stored per-rope tensions, runs elevcare_ml.rope_report, and adds the
ranked factors, the plain-language explanation, causes and a recommendation.
The chatbot uses the same `rope_brief()` so the two always agree.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from elevcare_ml import physics as P
from elevcare_ml import rope_report as R

from ..db import as_utc, clean, utcnow
from .views import material_tag, next_checkup

FACTOR_MEANING = {
    "tension_imbalance_pct": "how unevenly the ropes share the car's weight - the most-loaded rope wears fastest",
    "imbalance_trend": "how quickly that unevenness is growing day by day",
    "vib_rms": "how much the ropes and car shake overall",
    "fault_band_ratio": "sway energy - the car swinging side to side because the ropes are not equally tight",
    "fatigue_index": "how much of the ropes' rated bending life has already been used (Feyrer)",
    "tension_deviation_pct": "tension drift - how far the average rope tension has moved from its commissioning value",
}
FACTOR_ORDER = ["tension_imbalance_pct", "imbalance_trend", "vib_rms", "fault_band_ratio", "fatigue_index", "tension_deviation_pct"]


def _fmt(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.1f} %"
    if unit == "%/day":
        return f"{v:.2f} %/day"
    if unit == "":
        return f"{v:.2f}"
    return f"{v:.3f} {unit}"


def _factors(feats: dict) -> list[dict]:
    th = P.THRESHOLDS["rope"]
    rows = []
    for key in FACTOR_ORDER:
        t = th[key]
        v = float(feats.get(key, 0.0) or 0.0)
        sev = P.severity("rope", key, v)
        rows.append({"key": key, "label": t.label, "value": v, "value_text": _fmt(v, t.unit), "limit": t.alarm,
                     "limit_text": _fmt(t.alarm, t.unit), "healthy": t.healthy, "unit": t.unit,
                     "severity": round(min(1.0, sev), 3), "meaning": FACTOR_MEANING[key]})
    return sorted(rows, key=lambda r: -r["severity"])


def _layman(a: dict, faulty_ids: list[str], rul_days, n_ropes: int) -> str:
    w = a["worst"]
    lim = a["limits"]["fault_pct"]
    if a["verdict"] == "fault":
        rope = faulty_ids[0]
        dev = w["deviation_pct"]
        s = (f"Rope {rope} is carrying {dev:.0f}% more load than the average rope in this set (safe limit: {lim:.0f}%). "
             f"Think of {n_ropes} people carrying one stretcher. If one person carries more, that person tires first. "
             f"Because rope fatigue rises steeply with load (Feyrer, exponent {a['limits']['feyrer_b']}), this rope is wearing about "
             f"{w['wear_multiplier']:.1f}x faster than the others.")
        if rul_days is not None:
            s += f" On the current trend the rope set reaches the end of its safe life in about {max(1, round(rul_days))} days."
        if w["trend_pct_per_day"] > 0:
            s += f" The imbalance is still widening by {w['trend_pct_per_day']:.2f} percentage points per day."
        return s
    if w["deviation_pct"] >= a["limits"]["watch_pct"]:
        s = (f"No rope is over the fault limit, but rope {w['id']} carries {w['deviation_pct']:.0f}% more than the set average "
             f"(watch from {a['limits']['watch_pct']:.0f}%, fault at {lim:.0f}%), so it wears about {w['wear_multiplier']:.1f}x faster than its neighbours.")
        if w["days_to_limit"] is not None and w["days_to_limit"] > 0:
            s += f" At the current trend it reaches the fault limit in about {round(w['days_to_limit'])} days."
        return s
    return (f"All {n_ropes} ropes share the load within {a['imbalance_pct']:.1f}% of each other (fault limit {lim:.0f}%), "
            f"so no single rope is tiring faster than the rest.")


def _causes(a: dict, feats: dict) -> list[str]:
    out = []
    if a["verdict"] == "fault" or a["worst"]["deviation_pct"] >= a["limits"]["watch_pct"]:
        out += ["possible: uneven rope length after installation or re-roping (one rope shorter, so it takes more load)",
                f"possible: worn or undercut groove under rope {a['worst']['id']} changing its effective diameter",
                "possible: broken wires or a crushed section in the most-loaded rope",
                "possible: rope stretch (constructional stretch of newer ropes in a mixed set)"]
    if float(feats.get("fault_band_ratio", 0) or 0) > P.THRESHOLDS["rope"]["fault_band_ratio"].healthy:
        out.append("possible: car sway from slack ropes or worn guide shoes feeding rope vibration")
    if float(feats.get("fatigue_index", 0) or 0) > 0.5:
        out.append("possible: rope set near the end of its bending-fatigue life, so small differences show as wear")
    if not out:
        out.append("no abnormal driver identified - keep the scheduled inspection")
    return out + ["Confirm by inspection."]


def _recommendation(a: dict, comp: dict, faulty_ids: list[str]) -> dict:
    w = a["worst"]
    fatigue = float(comp.get("fatigue_index") or 0.0)
    rul = comp.get("rul_days")
    status = comp.get("status", "good")
    if a["verdict"] == "fault":
        if fatigue >= 0.5 or w["deviation_pct"] >= 15.0 or len(faulty_ids) > 1 or status == "at-risk":
            action, why = ("REPLACE WHOLE ROPE SET",
                           f"industry practice is to replace ropes as a set when any rope reaches its discard criteria; "
                           f"{', '.join(faulty_ids)} is over the {a['limits']['fault_pct']:.0f}% limit"
                           + (f" and {fatigue * 100:.0f}% of the set's rated bending life is used" if fatigue >= 0.5 else ""))
            urgency = int(max(1, min(14, rul or 14)))
        else:
            action, why = (f"RE-TENSION THE SET, THEN REPLACE ROPE {faulty_ids[0]} ONLY IF INSPECTION CONFIRMS DAMAGE",
                           "the set is young and only one rope is over the limit; equalise first and inspect that rope for broken wires")
            urgency = int(max(1, min(30, rul or 30)))
    elif w["deviation_pct"] >= a["limits"]["watch_pct"]:
        action, why = ("RE-TENSION THE ROPE SET", f"rope {w['id']} is in the watch band; equalising now prevents accelerated wear")
        urgency = int(min(30, w["days_to_limit"] or 30))
    else:
        action, why = ("NO ACTION - ROUTINE INSPECTION", "load sharing is within limits")
        urgency = None
    due = next_checkup(comp)
    if urgency is not None:
        due = min(due, utcnow() + timedelta(days=urgency)) if due else utcnow() + timedelta(days=urgency)
    return {"action": action, "why": why, "urgency_days": urgency, "next_checkup": clean(due),
            "replace_as_set_note": "Industry practice: when any rope reaches its discard criteria, replace the whole set so all ropes have the same diameter, stretch and stiffness."}


async def rope_section(db, elevator: dict, days: int = 30) -> dict:
    comp = await db.components.find_one({"elevator_id": elevator["_id"], "type": "rope"})
    if not comp:
        return {"available": False, "reason": "no rope component"}
    end = as_utc(comp.get("last_reading_at")) or utcnow()
    since = end - timedelta(days=days)
    cursor = db.sensor_readings.find({"component_id": comp["_id"], "timestamp": {"$gte": since}, "rope_tensions": {"$ne": None}},
                                     {"timestamp": 1, "rope_tensions": 1}).sort("timestamp", 1)
    rows = [(as_utc(r["timestamp"]).timestamp() / 86400.0, r["rope_tensions"]) async for r in cursor]
    if len(rows) < 3:
        return {"available": False, "reason": "not enough per-rope readings yet"}
    n = min(len(x) for _, x in rows)
    t = np.array([r[0] for r in rows])
    x = np.array([r[1][:n] for r in rows], dtype=float)
    a = R.analyse_ropes(t, x)
    series = R.daily_series(t, x, days)
    feats = comp.get("latest_features") or {}
    rec = _recommendation(a, comp, a["faulty"])
    return {
        "available": True, "component_id": comp["_id"], "component_name": comp["name"], "rope_count": n,
        "material": material_tag(comp),
        "generated_at": clean(utcnow()), "data_from": clean(since), "data_to": clean(end), "readings": len(rows),
        "verdict": a["verdict"], "faulty_ropes": a["faulty"], "banner": (f"FAULT DETECTED: Rope {', '.join(a['faulty'])} (of {n})"
                                                                         if a["faulty"] else "No rope fault detected"),
        "ropes": a["ropes"], "series": series, "set_average_kn": a["set_average_kn"], "worst": a["worst"], "limits": a["limits"],
        "set": {"imbalance_pct": a["imbalance_pct"], "imbalance_growth_pct_per_day": float(feats.get("imbalance_trend", 0.0) or 0.0),
                "vib_rms_g": float(feats.get("vib_rms", 0.0) or 0.0), "sway_band_ratio": float(feats.get("fault_band_ratio", 0.0) or 0.0),
                "life_used_pct": round(100 * float(comp.get("fatigue_index") or 0.0), 1), "health_score": comp.get("health_score"),
                "failure_probability": comp.get("failure_probability"), "rul_days": comp.get("rul_days"), "status": comp.get("status"),
                "rules_fired": comp.get("rules_fired", [])},
        "factors": _factors(feats), "layman": _layman(a, a["faulty"], comp.get("rul_days"), n), "causes": _causes(a, feats),
        "recommendation": rec, "assistant_question": "Which rope is faulty and why?",
    }


async def rope_brief(db, elevator_id: str) -> dict | None:
    """Compact per-rope facts for the chatbot context (same numbers as the report)."""
    e = await db.elevators.find_one({"_id": elevator_id})
    if not e:
        return None
    s = await rope_section(db, e)
    if not s.get("available"):
        return None
    return {"verdict": s["verdict"], "faulty_ropes": s["faulty_ropes"], "banner": s["banner"], "rope_count": s["rope_count"],
            "material": s.get("material"),
            "ropes": [{k: r[k] for k in ("id", "median_tension_kn", "deviation_pct", "trend_7d_pct_per_day", "status")} for r in s["ropes"]],
            "worst": s["worst"], "limits": s["limits"], "layman": s["layman"], "causes": s["causes"],
            "recommendation": {k: s["recommendation"][k] for k in ("action", "why", "urgency_days", "next_checkup")},
            "top_factors": [{"label": f["label"], "value": f["value_text"], "limit": f["limit_text"], "meaning": f["meaning"]}
                            for f in s["factors"][:3]]}
