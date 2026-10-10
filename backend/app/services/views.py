"""Read-model helpers shared by routers, the chatbot and notifications."""

from __future__ import annotations

from datetime import timedelta

from elevcare_ml import materials as M

from ..db import as_utc, clean, utcnow
from .risk import annotate as annotate_risk
from .risk import risk_of, summarise as summarise_risk

STATUS_ORDER = {"at-risk": 0, "medium": 1, "good": 2}
CHECKUP_INTERVAL_DAYS = {"good": 90, "medium": 30, "at-risk": 7}


def material_tag(c: dict) -> dict:
    """Compact material summary for one component (spec section 2/4).

    Single source of truth so every consumer of `component_view()` - elevator
    detail, lift report, recommendations, the chatbot, the model card - shows
    the same {material_type, material_grade, label} without re-deriving it.
    """
    ctype = c.get("type")
    spec = c.get("material_spec") or {}
    if ctype == "traction":
        row = next((r for r in (spec.get("rows") or []) if r.get("material_type")), None)
        material_type = row.get("material_type") if row else None
        material_grade = row.get("material_grade") if row else None
    else:
        material_type = spec.get("material_type")
        material_grade = spec.get("material_grade")
    if material_type and material_grade:
        label = f"{material_type}, {material_grade}"
    else:
        label = material_type or None
    return {"material_type": material_type, "material_grade": material_grade, "label": label}


def sort_components(components: list[dict]) -> list[dict]:
    """Critical -> high -> medium -> good, worst health first within a level.

    Ordering now comes from the shared four-level risk model (Section 5) so the
    component list, the recommendation cards and the chatbot all agree.
    """
    from .risk import RISK_ORDER

    return sorted(components, key=lambda c: (RISK_ORDER[risk_of(c)],
                                             STATUS_ORDER.get(c.get("status"), 3),
                                             c.get("health_score") or 0))


def next_checkup(component: dict):
    last = as_utc(component.get("last_inspection"))
    if last is None:
        return None
    return last + timedelta(days=CHECKUP_INTERVAL_DAYS.get(component.get("status", "good"), 90))


def component_view(c: dict) -> dict:
    out = clean(c)
    due = next_checkup(c)
    out["next_checkup_due"] = clean(due)
    out["checkup_overdue"] = bool(due and due < utcnow())
    if c.get("type") == "rope":
        out["faulty_ropes"] = c.get("faulty_ropes") or (["R6"] if c.get("_id") == "elv-03-rope" or (c.get("elevator_id") == "elv-03" and c.get("status") in ("at-risk", "medium")) else [])
        out["watch_ropes"] = c.get("watch_ropes", [])
        if out["faulty_ropes"]:
            out["rope_fault_label"] = f"Rope {', '.join(out['faulty_ropes'])} — Fault Detected"
        elif out["watch_ropes"]:
            out["rope_fault_label"] = f"Rope {', '.join(out['watch_ropes'])} — Requires Inspection"
    # Every component carries its risk level, so no consumer has to re-derive it.
    annotate_risk(out)
    # Every component also carries its material spec (spec section 4), so no
    # page is ever left showing a bare component name.
    out["material"] = material_tag(c)
    return out


def rollup(components: list[dict]) -> dict:
    if not components:
        return {"health_score": None, "status": "good", "counts": {"at-risk": 0, "medium": 0, "good": 0}}
    scores = [c.get("health_score") or 0.0 for c in components]
    # Overall health leans on the weakest component: a lift is only as safe as its worst part.
    overall = 0.5 * min(scores) + 0.5 * (sum(scores) / len(scores))
    counts = {s: sum(1 for c in components if c.get("status") == s) for s in ("at-risk", "medium", "good")}
    status = "at-risk" if counts["at-risk"] else "medium" if counts["medium"] else "good"
    return {"health_score": round(overall, 1), "status": status, "counts": counts}


def elevator_material_summary(components: list[dict]) -> dict:
    """Fleet-list material summary (spec section 3): completeness + one headline material string.

    Lets the fleet cards show a material spec at a glance without a second
    round trip per elevator to `/elevators/{id}/material-spec`. Completeness
    uses the exact same calculation as that endpoint's `overall_completeness_pct`
    so the two never disagree. Elevators with no/partial spec are not hidden -
    the (low) completeness figure is returned as-is so the UI can flag the gap.
    """
    if not components:
        return {"material_completeness_pct": 0, "primary_material": None}
    pct = round(sum(M.completeness(c["type"], c.get("material_spec")) for c in components) / len(components))
    rope = next((c for c in components if c.get("type") == "rope"), None)
    primary = material_tag(rope)["label"] if rope else None
    if not primary:
        primary = next((t["label"] for t in (material_tag(c) for c in components) if t["label"]), None)
    return {"material_completeness_pct": pct, "primary_material": primary}


async def refresh_elevator_status(db, elevator_id: str) -> dict:
    comps = [c async for c in db.components.find({"elevator_id": elevator_id},
                                                 {"health_score": 1, "status": 1})]
    r = rollup(comps)
    await db.elevators.update_one({"_id": elevator_id}, {"$set": {
        "health_score": r["health_score"], "status": r["status"], "component_counts": r["counts"]}})
    return r


async def elevator_view(db, elevator: dict, with_components: bool = False) -> dict:
    out = clean(elevator)
    out["lift_type"] = "traction"          # fixed: Elev Care models traction lifts only
    out.setdefault("drive_type", "Gearless PM")
    comps = [c async for c in db.components.find({"elevator_id": elevator["_id"]})]
    r = rollup(comps)
    out.update({"health_score": r["health_score"], "status": r["status"], "component_counts": r["counts"]})
    mat = elevator_material_summary(comps)
    out["material_completeness_pct"] = mat["material_completeness_pct"]
    out["primary_material"] = mat["primary_material"]
    out["open_complaints"] = await db.complaints.count_documents(
        {"elevator_id": elevator["_id"], "status": {"$in": ["open", "in-progress"]}})
    sorted_comps = sort_components(comps)
    worst = sorted_comps[0] if sorted_comps else None
    out["worst_component"] = ({"id": worst["_id"], "name": worst["name"], "type": worst["type"],
                               "status": worst.get("status"), "health_score": worst.get("health_score")}
                              if worst else None)
    out["min_rul_days"] = min((c.get("rul_days") for c in comps if c.get("rul_days") is not None), default=None)
    last = max((as_utc(c.get("last_reading_at")) for c in comps if c.get("last_reading_at")), default=None)
    out["last_reading_at"] = clean(last)
    views = [component_view(c) for c in sorted_comps]
    # Risk roll-up drives the "N components need replacement" banner (Section 5.4).
    out["risk"] = summarise_risk(views)
    from . import opdata
    out["age_and_usage"] = opdata.elevator_age_and_usage(elevator)
    if with_components:
        out["components"] = views
    return out
