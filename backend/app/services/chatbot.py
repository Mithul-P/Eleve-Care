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

from ..config import Settings
from ..db import clean, utcnow
from .notifications import build_notifications
from .views import next_checkup, rollup, sort_components

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
data does not contain the answer, say so plainly. Explain the physical reasoning behind \
contributing factors in plain language (e.g. rope tension imbalance overloads one rope; \
kurtosis rising means impacts from a bearing defect) and give a concrete next action.

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


def _component_brief(c: dict, elevator_name: str | None = None) -> dict:
    due = next_checkup(c)
    return {
        "component_id": c["_id"],
        "elevator": elevator_name,
        "name": c["name"],
        "type": c["type"],
        "status": c.get("status"),
        "health_score": c.get("health_score"),
        "failure_probability_pct_30d": c.get("failure_probability"),
        "remaining_useful_life_days": c.get("rul_days"),
        "fatigue_life_consumed": round(c.get("fatigue_index", 0.0), 3),
        "explanation": c.get("explanation"),
        "contributing_factors": [
            {"factor": f["label"], "value": f["display_value"], "alarm_limit": format_value(f["alarm"], f["unit"]),
             "severity_0_to_1": f["severity"], "advice": f["advice"]}
            for f in (c.get("contributing_factors") or [])],
        "last_inspection": clean(c.get("last_inspection")),
        "next_checkup_due": clean(due),
        "install_date": clean(c.get("install_date")),
    }


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
    if elevator_id and elevator_id in elevators:
        e = elevators[elevator_id]
        comps = sort_components([c for c in all_comps if c["elevator_id"] == elevator_id])
        ctx["focus_elevator"] = {
            "elevator_id": elevator_id, "name": e["name"], "building": e["building"],
            "location": e.get("location"), "is_demo": e.get("is_demo", False),
            "components": [_component_brief(c, e["name"]) for c in comps],
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
            ctx["focus_elevator"]["energy_last_30d"] = {
                "energy_kwh": round(kwh, 1), "cost_inr": round(sum(x["cost"] for x in logs), 0),
                "excess_kwh_due_to_degradation": round(kwh - base, 1), "days": len(logs)}
    if component:
        ctx["focus_component"] = _component_brief(component, elevators.get(component["elevator_id"], {}).get("name"))
    ctx["notifications"] = [
        {k: n.get(k) for k in ("level", "kind", "title", "message")}
        for n in (await build_notifications(db))[:12]]
    return ctx


# ---------------------------------------------------------------------------
# rule-based answers
# ---------------------------------------------------------------------------

def _describe(c: dict) -> str:
    rul = c.get("remaining_useful_life_days")
    s = (f"**{c['elevator']} - {c['name']}** is **{c['status']}**: health {c['health_score']:.0f}/100, "
         f"{c['failure_probability_pct_30d']:.0f}% failure probability in 30 days, "
         f"remaining life {format_rul(rul) if rul is not None else 'unknown'}.")
    return s


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

    if comp and has("why", "reason", "cause", "explain", "factor", "driver"):
        lines = [_describe(comp)]
        for f in comp["contributing_factors"]:
            lines.append(f"- {f['factor']}: {f['value']} (alarm at {f['alarm_limit']}). {f['advice']}")
        if not comp["contributing_factors"]:
            lines.append("All monitored indicators are within healthy limits.")
        return "\n".join(lines)

    if comp and has("rul", "how long", "remaining", "when", "life", "last"):
        return (_describe(comp) + f" Fatigue life consumed (Miner's rule): {comp['fatigue_life_consumed']:.0%}. "
                f"Next checkup due {(comp['next_checkup_due'] or 'n/a')[:10]}.")

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

    if elev and has("energy", "cost", "power", "kwh", "bill", "saving"):
        en = elev.get("energy_last_30d")
        if not en:
            return f"No energy data yet for {elev['name']}."
        return (f"**{elev['name']} - last {en['days']} days:** {en['energy_kwh']} kWh, "
                f"Rs {en['cost_inr']:,.0f} at Rs 5/unit. About {en['excess_kwh_due_to_degradation']} kWh of that is "
                "excess consumption attributable to component degradation (friction, slip, motor losses).")

    if comp:
        s = _describe(comp)
        if comp["contributing_factors"]:
            f = comp["contributing_factors"][0]
            s += f" Main driver: {f['factor'].lower()} {f['value']}. {f['advice']}"
        return s

    if elev:
        lines = [f"**{elev['name']}** ({elev['building']}) components, most urgent first:"]
        for c in elev["components"]:
            lines.append(f"- {c['name']}: {c['status']}, health {c['health_score']:.0f}, "
                         f"{c['failure_probability_pct_30d']:.0f}% failure risk in 30 days, "
                         f"RUL {format_rul(c['remaining_useful_life_days'] or 0)}")
        if elev["open_complaints"]:
            lines.append(f"{len(elev['open_complaints'])} open complaint(s) from users.")
        worst = elev["components"][0] if elev["components"] else None
        if worst and worst["status"] != "good":
            lines.append(f"Focus: {worst['explanation']}")
        return "\n".join(lines)

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
            "- \"Which components are at risk?\"\n- \"Why is the demo lift's rope at risk?\"\n"
            "- \"How long will the Lobby Lift traction machine last?\"\n- \"What checkups are due?\"\n"
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
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=90.0, max_retries=2)
    model = settings.chatbot_model
    kwargs: dict = {}
    if not model.startswith("claude-haiku"):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": settings.chatbot_effort}
    if model.startswith(("claude-opus-5", "claude-fable")):
        # Server-side refusal fallback: a declined request is re-run on Anthropic's recommended model.
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    user_turn = (f"<fleet_data>\n{json.dumps(ctx, default=str)}\n</fleet_data>\n\n"
                 f"Question: {message}")
    try:
        resp = await client.beta.messages.create(
            model=model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[*_clean_history(history), {"role": "user", "content": user_turn}],
            **kwargs,
        )
    except anthropic.RateLimitError:
        log.warning("Claude rate limited - using rule-based answer")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error %s - using rule-based answer", exc.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude unreachable - using rule-based answer")
        return None
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
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
