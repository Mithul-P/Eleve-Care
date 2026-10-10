"""Read-model helpers shared by routers, the chatbot and notifications."""

from __future__ import annotations

from datetime import timedelta

from ..db import as_utc, clean, utcnow

STATUS_ORDER = {"at-risk": 0, "medium": 1, "good": 2}
CHECKUP_INTERVAL_DAYS = {"good": 90, "medium": 30, "at-risk": 7}


def sort_components(components: list[dict]) -> list[dict]:
    """at-risk -> medium -> good, worst health first within a group."""
    return sorted(components, key=lambda c: (STATUS_ORDER.get(c.get("status"), 3), c.get("health_score") or 0))


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


async def refresh_elevator_status(db, elevator_id: str) -> dict:
    comps = [c async for c in db.components.find({"elevator_id": elevator_id},
                                                 {"health_score": 1, "status": 1})]
    r = rollup(comps)
    await db.elevators.update_one({"_id": elevator_id}, {"$set": {
        "health_score": r["health_score"], "status": r["status"], "component_counts": r["counts"]}})
    return r


async def elevator_view(db, elevator: dict, with_components: bool = False) -> dict:
    out = clean(elevator)
    comps = [c async for c in db.components.find({"elevator_id": elevator["_id"]})]
    r = rollup(comps)
    out.update({"health_score": r["health_score"], "status": r["status"], "component_counts": r["counts"]})
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
    if with_components:
        out["components"] = [component_view(c) for c in sorted_comps]
    return out
