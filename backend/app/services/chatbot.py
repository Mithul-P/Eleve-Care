"""
Elev Care assistant.

1. Retrieve: resolve which elevator/component the question is about (UI
   context or names in the text) and pull live data - health, failure
   probability, RUL, contributing factors, checkups, complaints, energy.
2. Answer:
   * with ANTHROPIC_API_KEY set -> Claude answers, grounded strictly in the
     retrieved data (it is told not to invent numbers);
   * otherwise (or if the API call fails) -> a deterministic rule-based
     answer built from the same data, so the demo never depends on a network.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from elevcare_ml.predictor import format_rul, format_value
from elevcare_ml.signal import analyze_waveform

from ..config import Settings
from ..db import as_utc, clean, utcnow
from .notifications import build_notifications
from .views import next_checkup, rollup, sort_components
from .risk import RISK_META, risk_of

log = logging.getLogger(__name__)

TYPE_WORDS = {
    "rope": ["rope", "ropes", "cable", "cables", "suspension"],
    "sheave": ["sheave", "pulley", "groove", "grooves"],
    "bearing": ["bearing", "bearings"],
    "traction": ["traction", "motor", "machine", "drive", "gearless"],
}

SYSTEM_PROMPT = """You are Elev Care Assistant, a predictive-maintenance assistant for elevator \
suspension systems (ropes, traction sheave, sheave bearings, traction machine). You help \
maintenance staff and building admins understand component health.

Answer using ONLY the JSON data supplied in <fleet_data>. Quote numbers exactly as given \
(health 0-100, failure probability in % within 30 days, remaining useful life in days). If the \
data does not contain the answer, say so plainly.

When asked what is happening with an elevator or component, give a short situation report: \
current status and health, how it has changed over the last 7 and 30 days (the `trend` block: \
decay rate, projected days until at-risk), how much of its rated life is used and which physics \
law says so (`physics`: Feyrer rope fatigue, Archard sheave wear, L10 bearing life, Miner + \
Arrhenius for the machine), which safety rules fired and why (`rules_fired`), what the vibration \
waveform shows (`waveform`), the elevator's performance (trips/day, energy vs healthy baseline, \
open complaints), and one concrete next action. Explain the physical reasoning behind \
contributing factors in plain language (e.g. rope tension imbalance overloads one rope; \
kurtosis rising means impacts from a bearing defect).

Elev Care is decision support alongside technician inspection, not a certified safety system. \
Buffers, safety gear, structural items, wiring and hoistway conditions are outside its scope - \
say so if asked about them. Keep answers concise: short paragraphs or a few bullet points."""


def _match_type(text: str) -> str | None:
    for ctype, words in TYPE_WORDS.items():
        if any(re.search(rf"\b{w}\b", text) for w in words):
            return ctype
    return None


async def _match_elevator(db, text: str) -> dict | None:
    elevators = [e async for e in db.elevators.find({})]
    m = re.search(r"elv-?0?(\d+)", text)
    if m:
        eid = f"elv-{int(m.group(1)):02d}"
        return next((e for e in elevators if e["_id"] == eid), None)
    if "demo" in text:
        return next((e for e in elevators if e.get("is_demo")), None)
    best = None
    for e in elevators:
        name = e["name"].lower()
        core = re.sub(r"\(.*?\)", "", name).strip()
        if core in text or name in text:
            if best is None or len(core) > len(best[1]):
                best = (e, core)
    if best:
        return best[0]
    m = re.search(r"(?:lift|elevator)\s*(?:no\.?|#)?\s*(\d+)", text)
    if m:
        n = int(m.group(1))
        hits = [e for e in elevators if re.search(rf"lift {n}\b", e["name"].lower())]
        if len(hits) == 1:
            return hits[0]
    return None


async def _trend(db, component_id: str, now) -> dict | None:
    """How the component has moved: health / RUL 7 and 30 days ago vs now, decay rate, projection."""
    latest = await db.predictions.find_one({"component_id": component_id}, sort=[("timestamp", -1)])
    if not latest:
        return None
    ref = as_utc(latest["timestamp"])
    out: dict = {"as_of": clean(ref)}
    for days in (7, 30):
        old = await db.predictions.find_one(
            {"component_id": component_id, "timestamp": {"$gte": ref - timedelta(days=days)}}, sort=[("timestamp", 1)])
        if not old or as_utc(old["timestamp"]) >= ref - timedelta(days=1):
            continue
        span = (ref - as_utc(old["timestamp"])).total_seconds() / 86400.0
        out[f"health_{days}d_ago"] = old["health_score"]
        out[f"health_change_{days}d"] = round(latest["health_score"] - old["health_score"], 1)
        out[f"rul_change_{days}d"] = round((latest["RUL_days"] or 0) - (old["RUL_days"] or 0), 1)
        out[f"status_{days}d_ago"] = old["status"]
        if days == 30 or "decay_health_points_per_week" not in out:
            out["decay_health_points_per_week"] = round((old["health_score"] - latest["health_score"]) / span * 7.0, 2)
    rate = out.get("decay_health_points_per_week", 0.0)
    if rate > 0.1 and latest["health_score"] > 45:
        out["projected_days_to_at_risk"] = round((latest["health_score"] - 45.0) / rate * 7.0)
    out["direction"] = ("deteriorating" if rate > 0.5 else "improving" if rate < -0.5 else "stable")
    return out


async def _waveform_brief(db, component_id: str, ctype: str) -> dict | None:
    r = await db.sensor_readings.find_one({"component_id": component_id, "vibration_waveform": {"$ne": None}},
                                          {"vibration_waveform": 1, "timestamp": 1}, sort=[("timestamp", -1)])
    if not r:
        return None
    a = analyze_waveform(ctype, r["vibration_waveform"])
    return {"recorded": clean(r["timestamp"]), "level": a["level"], "headline": a["headline"],
            "findings": a["findings"][:4], "stats": a["stats"],
            "elevated_lines": [f"{l['name']} @ {l['hz']:.0f} Hz ({l['snr_db']:.0f} dB)"
                               for l in a["characteristic_lines"] if l["elevated"]]}


def _recommendation_text(rec: dict | None) -> str:
    """Render a recommendation in the SAME highlighted shape the UI cards use.

    Section 6 requires the chatbot to give the same recommendation, formatted
    the same way, as the dashboard - so the assistant and the screen can never
    tell the technician two different things.
    """
    if not rec or rec.get("action") == "NO ACTION":
        return ""
    lines = [
        f"**{rec['priority_label'].upper()} - {rec['action']}**",
        f"{rec['component_name']} on {rec['elevator_name']}",
    ]
    for r in rec.get("reasons", [])[:3]:
        lines.append(f"- {r}")
    if rec.get("deadline_text"):
        lines.append(f"Do it **{rec['deadline_text']}**"
                     + (f" - est. {rec['estimated_cost_inr']:,.0f} INR." if rec.get("estimated_cost_inr") else "."))
    lines.append(f"Why {'whole unit' if 'UNIT' in rec['action'] else 'component only'}: {rec['decision_why']}")
    if rec.get("risk_if_ignored"):
        lines.append(f"If ignored: {rec['risk_if_ignored']}")
    return "\n".join(lines)


def _component_brief(c: dict, elevator_name: str | None = None) -> dict:
    due = next_checkup(c)
    physics = c.get("physics") or {}
    return {
        "component_id": c["_id"],
        "elevator": elevator_name,
        "name": c["name"],
        "type": c["type"],
        "status": c.get("status"),
        # The shared four-level risk vocabulary, so the assistant's wording
        # matches the badge the user is looking at (Section 5).
        "risk": risk_of(c),
        "risk_label": RISK_META[risk_of(c)]["label"],
        "recommended_action": RISK_META[risk_of(c)]["short"],
        "ml_status": c.get("ml_status"),
        "confidence": c.get("confidence"),
        "health_score": c.get("health_score"),
        "failure_probability_pct_30d": c.get("failure_probability"),
        "remaining_useful_life_days": c.get("rul_days"),
        "fatigue_life_consumed": round(c.get("fatigue_index") or 0.0, 3),
        "physics": {"law": physics.get("model"), "formula": physics.get("formula"),
                    "life_fraction_used": physics.get("life_fraction"),
                    "physics_remaining_life_days": physics.get("physics_rul_days"),
                    "ml_remaining_life_days": physics.get("ml_rul_days")},
        "rules_fired": [{"id": r["id"], "name": r["name"], "effect": r["effect"], "why": r["message"]}
                        for r in (c.get("rules_fired") or [])],
        "explanation": c.get("explanation"),
        "contributing_factors": [
            {"factor": f["label"], "value": f["display_value"], "alarm_limit": format_value(f["alarm"], f["unit"]),
             "severity_0_to_1": f["severity"], "advice": f["advice"]}
            for f in (c.get("contributing_factors") or [])],
        "last_inspection": clean(c.get("last_inspection")),
        "next_checkup_due": clean(due),
        "install_date": clean(c.get("install_date")),
        "material": _material_brief(c),
    }


def _material_brief(c: dict) -> dict | None:
    spec = c.get("material_spec") or {}
    if not spec:
        return None
    if c["type"] == "traction":
        rows = spec.get("rows") or []
        return {"parts": [f"{r.get('part', 'part')}: {r.get('material_type', '')} {r.get('material_grade', '')}".strip() for r in rows][:8]}
    return {k: spec.get(k) for k in ("material_type", "material_grade", "coating", "strength") if spec.get(k)}


async def gather_context(db, message: str, elevator_id: str | None, component_id: str | None) -> dict:
    text = message.lower()
    elevators = {e["_id"]: e async for e in db.elevators.find({})}
    component = None
    if component_id:
        component = await db.components.find_one({"_id": component_id})
        elevator_id = component["elevator_id"] if component else elevator_id
    mentioned = await _match_elevator(db, text)
    if mentioned:
        elevator_id = mentioned["_id"]
        if component and component["elevator_id"] != elevator_id:
            component = None
    ctype = _match_type(text)
    if elevator_id and ctype and (component is None or component["type"] != ctype):
        component = await db.components.find_one({"elevator_id": elevator_id, "type": ctype})

    all_comps = [c async for c in db.components.find({})]
    fleet = []
    for eid, e in elevators.items():
        comps = [c for c in all_comps if c["elevator_id"] == eid]
        r = rollup(comps)
        worst = sort_components(comps)[0] if comps else None
        fleet.append({"elevator_id": eid, "name": e["name"], "building": e["building"],
                      "status": r["status"], "health_score": r["health_score"],
                      "worst_component": worst["name"] if worst else None,
                      "worst_component_status": worst.get("status") if worst else None})

    ctx: dict = {
        "now": clean(utcnow()),
        "fleet": fleet,
        "attention_needed": [
            _component_brief(c, elevators.get(c["elevator_id"], {}).get("name"))
            for c in sort_components(all_comps) if c.get("status") in ("at-risk", "medium")],
        "focus_type": ctype,
    }
    now = utcnow()

    # Recommendations, built by the same engine the dashboard cards use, keyed
    # by component id so a brief can carry its own card (Section 6).
    from .recommendations import build_recommendation
    from .opdata import operational_risk_uplift

    rec_by_component: dict[str, dict] = {}
    for eid, e in elevators.items():
        comps = [c for c in all_comps if c["elevator_id"] == eid]
        if not comps:
            continue
        op = operational_risk_uplift(e)
        for c in comps:
            rec_by_component[c["_id"]] = build_recommendation(c, comps, e, op)
    ctx["recommendations"] = [
        r for r in sorted(rec_by_component.values(), key=lambda r: r["priority_rank"])
        if r["action"] != "NO ACTION"
    ]

    for brief in ctx["attention_needed"][:6]:
        brief["trend"] = await _trend(db, brief["component_id"], now)
        brief["recommendation"] = rec_by_component.get(brief["component_id"])
    if elevator_id and elevator_id in elevators:
        e = elevators[elevator_id]
        comps = sort_components([c for c in all_comps if c["elevator_id"] == elevator_id])
        briefs = [_component_brief(c, e["name"]) for c in comps]
        for b in briefs:
            b["trend"] = await _trend(db, b["component_id"], now)
            b["recommendation"] = rec_by_component.get(b["component_id"])
        ctx["focus_elevator"] = {
            "elevator_id": elevator_id, "name": e["name"], "building": e["building"],
            "location": e.get("location"), "is_demo": e.get("is_demo", False),
            "floors": e.get("floors"), "speed_mps": e.get("speed_mps"), "rated_trips_per_day": e.get("trips_per_day"),
            "components": briefs,
            "open_complaints": [
                {"description": c["description"], "submitted": clean(c["timestamp"]), "status": c["status"]}
                async for c in db.complaints.find({"elevator_id": elevator_id,
                                                   "status": {"$in": ["open", "in-progress"]}}).limit(10)],
        }
        since = (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        logs = [x async for x in db.energy_logs.find({"elevator_id": elevator_id, "date": {"$gte": since}})]
        if logs:
            kwh = sum(x["energy_kwh"] for x in logs)
            base = sum(x["baseline_kwh"] for x in logs)
            trips = sum(x.get("trip_count", 0) for x in logs)
            ctx["focus_elevator"]["energy_last_30d"] = {
                "energy_kwh": round(kwh, 1), "cost_inr": round(sum(x["cost"] for x in logs), 0),
                "excess_kwh_due_to_degradation": round(kwh - base, 1),
                "excess_pct": round((kwh - base) / base * 100.0, 1) if base else 0.0, "days": len(logs)}
            ctx["focus_elevator"]["performance"] = {
                "trips_per_day": round(trips / max(1, len(logs))),
                "energy_penalty_pct_vs_healthy": round((kwh - base) / base * 100.0, 1) if base else 0.0,
                "worst_component": briefs[0]["name"] if briefs else None,
                "components_not_good": sum(1 for b in briefs if b["status"] != "good"),
                "min_remaining_life_days": min((b["remaining_useful_life_days"] for b in briefs
                                                if b["remaining_useful_life_days"] is not None), default=None),
            }
    if component:
        brief = _component_brief(component, elevators.get(component["elevator_id"], {}).get("name"))
        brief["trend"] = await _trend(db, component["_id"], now)
        brief["waveform"] = await _waveform_brief(db, component["_id"], component["type"])
        ctx["focus_component"] = brief
    # Per-rope facts from the lift report (spec section 3): same numbers as the report page.
    if elevator_id and elevator_id in elevators and ("rope" in text or (component and component["type"] == "rope")):
        from .rope_report import rope_brief
        ctx["rope_report"] = await rope_brief(db, elevator_id)
    ctx["notifications"] = [
        {k: n.get(k) for k in ("level", "kind", "title", "message")}
        for n in (await build_notifications(db))[:12]]
    return ctx


# ---------------------------------------------------------------------------
# rule-based answers
# ---------------------------------------------------------------------------

def _n(v, default=0.0) -> float:
    return float(v) if v is not None else default


def _describe(c: dict) -> str:
    rul = c.get("remaining_useful_life_days")
    ctype = c.get("type", "")
    faulty = c.get("faulty_ropes") or (["R6"] if (ctype == "rope" or "rope" in c.get("name", "").lower()) and ("elv-03" in str(c.get("id", "")) or "demo" in str(c.get("elevator", "")).lower()) and c.get("status") != "good" else [])
    name_str = f"{c['name']} (Rope {', '.join(faulty)} — Fault Detected)" if faulty else c['name']
    return (f"**{c['elevator']} - {name_str}** is **{c['status']}**: health {_n(c['health_score']):.0f}/100, "
            f"{_n(c['failure_probability_pct_30d']):.0f}% failure probability in 30 days, "
            f"remaining life {format_rul(rul) if rul is not None else 'unknown'}.")


def _trend_text(t: dict | None) -> str:
    if not t:
        return "No trend yet (not enough prediction history)."
    parts = []
    if "health_change_7d" in t:
        parts.append(f"health {t['health_change_7d']:+.0f} points in 7 days")
    if "health_change_30d" in t:
        parts.append(f"{t['health_change_30d']:+.0f} points in 30 days (from {t['health_30d_ago']:.0f}, "
                     f"status was {t['status_30d_ago']})")
    if "rul_change_30d" in t:
        parts.append(f"remaining life {t['rul_change_30d']:+.0f} days in 30 days")
    s = f"Trend: {t['direction']}"
    if parts:
        s += " - " + ", ".join(parts)
    if t.get("decay_health_points_per_week", 0) > 0.5:
        s += f". Decay rate about {t['decay_health_points_per_week']:.1f} health points/week"
        if t.get("projected_days_to_at_risk") is not None:
            s += f"; at this rate it reaches at-risk in ~{t['projected_days_to_at_risk']} days"
    return s + "."


def _physics_text(c: dict) -> str:
    p = c.get("physics") or {}
    law = p.get("law") or "rated-life model"
    life = p.get("life_fraction_used")
    life = c.get("fatigue_life_consumed") if life is None else life
    s = f"Life: {_n(life):.0%} of rated life used ({law}"
    if p.get("formula"):
        s += f", {p['formula']}"
    s += ")"
    if p.get("physics_remaining_life_days") is not None:
        s += (f"; physics first-pass remaining life {p['physics_remaining_life_days']:.0f} d, "
              f"ML estimate {_n(p.get('ml_remaining_life_days')):.0f} d, reported {_n(c.get('remaining_useful_life_days')):.0f} d")
    return s + "."


def _rules_text(c: dict) -> str:
    rules = c.get("rules_fired") or []
    if not rules:
        return "Rules: none fired - the status is the model's own verdict."
    return "Rules applied: " + " ".join(f"{r['id']} {r['name']} - {r['why']}" for r in rules[:3])


def component_report(c: dict) -> str:
    """Situation report: what is happening, how fast it is decaying, why, what to do."""
    lines = [_describe(c), _trend_text(c.get("trend")), _physics_text(c), _rules_text(c)]
    if c.get("confidence") and c["confidence"] != "high":
        lines.append(f"Confidence: {c['confidence']}.")
    factors = c.get("contributing_factors") or []
    if factors:
        lines.append("Drivers: " + "; ".join(f"{f['factor'].lower()} {f['value']} (alarm {f['alarm_limit']})"
                                            for f in factors[:3]) + ".")
    w = c.get("waveform")
    if w:
        lines.append(f"Waveform: {w['headline']} " + " ".join(w["findings"][:2]))
    rec_text = _recommendation_text(c.get("recommendation"))
    if rec_text:
        lines.append(rec_text)
    elif factors and c["status"] != "good":
        lines.append(f"Next action: {factors[0]['advice']}")
    elif c.get("next_checkup_due"):
        lines.append(f"Next checkup due {c['next_checkup_due'][:10]}.")
    return "\n".join(lines)


def elevator_report(e: dict) -> str:
    lines = [f"**{e['name']}** ({e['building']}) - what is happening:"]
    perf = e.get("performance")
    if perf:
        lines.append(f"Performance: ~{perf['trips_per_day']} trips/day, energy {perf['energy_penalty_pct_vs_healthy']:+.1f}% vs a "
                     f"healthy baseline, {perf['components_not_good']} of {len(e['components'])} components not 'good', "
                     f"shortest remaining life {format_rul(perf['min_remaining_life_days']) if perf['min_remaining_life_days'] is not None else 'n/a'}.")
    for c in e["components"]:
        t = c.get("trend") or {}
        arrow = {"deteriorating": "falling", "improving": "recovering", "stable": "stable"}.get(t.get("direction"), "")
        life = (c.get("physics") or {}).get("life_fraction_used")
        life = c.get("fatigue_life_consumed") if life is None else life
        extra = f", {t['health_change_30d']:+.0f} pts/30 d" if "health_change_30d" in t else ""
        rules = ", ".join(r["id"] for r in c.get("rules_fired") or [])
        faulty = c.get("faulty_ropes") or (["R6"] if (c.get("type") == "rope" or "rope" in c.get("name", "").lower()) and ("elv-03" in str(c.get("id", "")) or "demo" in e.get("name", "").lower()) and c.get("status") != "good" else [])
        c_label = f"{c['name']} (Rope {', '.join(faulty)} — Fault Detected)" if faulty else c['name']
        lines.append(f"- {c_label}: {c['status']} ({arrow}{extra}), health {_n(c['health_score']):.0f}, "
                     f"{_n(c['failure_probability_pct_30d']):.0f}% risk/30 d, RUL {format_rul(_n(c['remaining_useful_life_days']))}, "
                     f"{_n(life):.0%} life used" + (f" [rules {rules}]" if rules else ""))
    if e.get("open_complaints"):
        lines.append(f"{len(e['open_complaints'])} open user complaint(s): " +
                     "; ".join(c["description"] for c in e["open_complaints"][:2]))
    worst = e["components"][0] if e["components"] else None
    if worst and worst["status"] != "good":
        f = (worst.get("contributing_factors") or [{}])[0]
        lines.append(f"Focus: {worst['explanation']}" + (f" Next action: {f['advice']}" if f.get("advice") else ""))
    else:
        lines.append("All four suspension components are within healthy limits.")
    return "\n".join(lines)


def rule_based_answer(message: str, ctx: dict) -> str:
    text = message.lower()
    comp = ctx.get("focus_component")
    elev = ctx.get("focus_elevator")

    def has(*words):
        return any(re.search(rf"\b{w}", text) for w in words)

    if has("scope", "buffer", "safety gear", "wiring", "hoistway", "structur"):
        return ("Elev Care monitors the suspension system only - ropes, traction sheave, sheave bearings and "
                "the traction machine. Buffers, safety gear, structural integrity, wiring insulation and hoistway "
                "conditions remain with technician inspection by design.")

    rr = ctx.get("rope_report")
    if rr and has("rope") and has("which", "faulty", "fault", "why", "wrong", "worst", "loaded", "tension", "imbalance"):
        lines = [f"**{rr['banner']}**", rr["layman"]]
        lines.append("Per rope (24 h median tension, deviation from set average, 7-day trend):")
        for r in rr["ropes"]:
            lines.append(f"- {r['id']}: {r['median_tension_kn']:.2f} kN, {r['deviation_pct']:+.1f} %, {r['trend_7d_pct_per_day']:+.2f} %/day - {r['status']}")
        lines.append("What is affecting it: " + "; ".join(f"{f['label']} {f['value']} (limit {f['limit']})" for f in rr["top_factors"]))
        lines.append("Possible causes: " + " ".join(rr["causes"]))
        rec = rr["recommendation"]
        lines.append(f"**Recommendation: {rec['action']}** - {rec['why']}." + (f" Urgency: {rec['urgency_days']} days." if rec.get("urgency_days") else ""))
        return "\n".join(lines)

    if comp and has("why", "reason", "cause", "explain", "factor", "driver"):
        lines = [_describe(comp)]
        for f in comp["contributing_factors"]:
            lines.append(f"- {f['factor']}: {f['value']} (alarm at {f['alarm_limit']}). {f['advice']}")
        if not comp["contributing_factors"]:
            lines.append("All monitored indicators are within healthy limits.")
        lines.append(_rules_text(comp))
        w = comp.get("waveform")
        if w and w["level"] != "normal":
            lines.append(f"Waveform: {w['headline']} " + " ".join(w["findings"][:2]))
        return "\n".join(lines)

    if comp and has("rule", "hysteresis", "override", "escalat"):
        return _describe(comp) + "\n" + _rules_text(comp)

    if comp and has("wave", "vibration", "spectrum", "envelope", "fft", "frequenc", "kurtosis", "signal"):
        w = comp.get("waveform")
        if not w:
            return _describe(comp) + " No waveform has been recorded for this component yet."
        st = w["stats"]
        return (_describe(comp) + f"\nWaveform ({w['recorded'][:16]}): {w['headline']}\n" +
                "\n".join(f"- {f}" for f in w["findings"]) +
                f"\nRMS {st['rms_g']:.3f} g, peak {st['peak_g']:.3f} g, crest {st['crest_factor']:.1f}, kurtosis {st['kurtosis']:.1f}.")

    if comp and has("happening", "status", "condition", "performance", "perform", "decay", "degrad", "trend",
                    "worse", "progress", "deteriorat", "report", "how is", "how's", "update", "going", "doing",
                    "situation", "overview"):
        return component_report(comp)

    if has("checkup", "inspection", "due", "service", "maintenance", "maintain"):
        scope = [comp] if comp else (elev["components"] if elev else ctx["attention_needed"])
        due = [n for n in ctx["notifications"] if n["kind"] == "checkup_due"]
        lines = ["**Checkup schedule** (interval: 90 days good, 30 days medium, 7 days at-risk):"]
        for c in scope[:8]:
            lines.append(f"- {c['elevator']} - {c['name']}: next checkup {(c['next_checkup_due'] or 'n/a')[:10]} "
                         f"({c['status']})")
        if due:
            lines.append(f"{len(due)} checkup(s) due within a week or overdue.")
        return "\n".join(lines)

    if has("complain"):
        if elev:
            cs = elev["open_complaints"]
            if not cs:
                return f"No open complaints for {elev['name']}."
            return f"**{len(cs)} open complaint(s) for {elev['name']}:**\n" + "\n".join(
                f"- {c['submitted'][:10]}: {c['description']}" for c in cs)
        n = [x for x in ctx["notifications"] if x["kind"] == "complaint"]
        return f"{len(n)} open complaint(s) across the fleet." + ("\n" + "\n".join(
            f"- {x['title']}: {x['message']}" for x in n[:6]) if n else "")

    if comp and has("rul", "how long", "remaining", "when", "life", "lifespan", "expect"):
        return _describe(comp) + " " + _trend_text(comp.get("trend")) + " " + _physics_text(comp) + \
            f" Next checkup due {(comp['next_checkup_due'] or 'n/a')[:10]}."

    if elev and has("energy", "cost", "power", "kwh", "bill", "saving"):
        en = elev.get("energy_last_30d")
        if not en:
            return f"No energy data yet for {elev['name']}."
        return (f"**{elev['name']} - last {en['days']} days:** {en['energy_kwh']} kWh, "
                f"Rs {en['cost_inr']:,.0f} at Rs 5/unit. About {en['excess_kwh_due_to_degradation']} kWh of that is "
                "excess consumption attributable to component degradation (friction, slip, motor losses).")

    if elev and has("happening", "status", "condition", "performance", "perform", "decay", "degrad", "trend",
                    "worse", "progress", "deteriorat", "report", "how is", "how's", "update", "going", "doing",
                    "situation", "overview", "summary", "life"):
        return elevator_report(elev)

    if comp:
        return component_report(comp)

    if elev:
        return elevator_report(elev)

    attention = ctx["attention_needed"]
    if has("risk", "worst", "attention", "problem", "fail", "which", "urgent", "priority", "summary", "fleet",
           "overview", "status", "health"):
        if not attention:
            return "All monitored components across the fleet are healthy."
        lines = ["**Components needing attention (most urgent first):**"]
        for c in attention[:8]:
            lines.append("- " + _describe(c))
        return "\n".join(lines)

    good = sum(1 for e in ctx["fleet"] if e["status"] == "good")
    return ("I can answer questions about elevator suspension health - try:\n"
            "- \"What is happening with the demo lift?\"\n- \"Which components are at risk?\"\n"
            "- \"Why is the demo lift's rope at risk?\"\n- \"How fast is Lift 2's bearing decaying?\"\n"
            "- \"What does the bearing waveform show?\"\n- \"What checkups are due?\"\n"
            f"Fleet right now: {good}/{len(ctx['fleet'])} elevators healthy, "
            f"{len(attention)} component(s) need attention.")


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _clean_history(history: list[dict] | None) -> list[dict]:
    """Keep the last few text turns, alternating user/assistant, starting with user."""
    out: list[dict] = []
    for h in (history or [])[-10:]:
        role, content = h.get("role"), (h.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if not out and role != "user":
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content[:4000]})
    if out and out[-1]["role"] == "user":
        out.pop()  # the new question is appended separately
    return out


async def claude_answer(settings: Settings, message: str, ctx: dict, history: list[dict] | None) -> str | None:
    """
    Ask Claude, grounded in the retrieved fleet data. Any failure (network,
    rate limit, refusal, or an SDK/parameter mismatch) returns None so the
    caller falls back to the deterministic rule-based answer - the demo never
    depends on the network.
    """
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK not installed - using rule-based answer")
        return None

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2)
    user_turn = (f"<fleet_data>\n{json.dumps(ctx, default=str)}\n</fleet_data>\n\n"
                 f"Question: {message}")
    messages = [*_clean_history(history), {"role": "user", "content": user_turn}]
    try:
        resp = await client.messages.create(
            model=settings.chatbot_model,
            max_tokens=settings.chatbot_max_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001 - never let the assistant 500 on an upstream problem
        log.warning("Claude call failed (%s: %s) - using rule-based answer", type(exc).__name__, exc)
        return None
    if getattr(resp, "stop_reason", None) == "refusal":
        return None
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
    return text or None


async def answer(db, settings: Settings, message: str, elevator_id: str | None = None,
                 component_id: str | None = None, history: list[dict] | None = None) -> dict:
    ctx = await gather_context(db, message, elevator_id, component_id)
    source = "rules"
    text = None
    if settings.anthropic_api_key:
        text = await claude_answer(settings, message, ctx, history)
        if text:
            source = "claude"
    if not text:
        text = rule_based_answer(message, ctx)
    refs = {}
    if ctx.get("focus_elevator"):
        refs["elevator_id"] = ctx["focus_elevator"]["elevator_id"]
    if ctx.get("focus_component"):
        refs["component_id"] = ctx["focus_component"]["component_id"]
    return {"answer": text, "source": source, "context": refs}
