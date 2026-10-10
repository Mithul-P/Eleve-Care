"""Proactive notifications: near-failure components, due checkups, new complaints."""

from __future__ import annotations

from datetime import timedelta

from ..db import clean, iso, utcnow
from .views import next_checkup

LEVEL_ORDER = {"critical": 0, "warning": 1, "info": 2}


async def build_notifications(db, elevator_id: str | None = None) -> list[dict]:
    q = {"elevator_id": elevator_id} if elevator_id else {}
    elevators = {e["_id"]: e["name"] async for e in db.elevators.find({}, {"name": 1})}
    now = utcnow()
    items: list[dict] = []

    async for c in db.components.find(q):
        where = f"{elevators.get(c['elevator_id'], c['elevator_id'])} - {c['name']}"
        base = {"elevator_id": c["elevator_id"], "component_id": c["_id"], "component_type": c["type"]}
        rul = c.get("rul_days")
        if c.get("status") == "at-risk" or (rul is not None and rul < 30):
            items.append({**base, "level": "critical", "kind": "near_failure",
                          "title": f"{where} is at risk",
                          "message": (f"{c.get('failure_probability', 0):.0f}% failure probability within 30 days, "
                                      f"remaining life ~{(rul or 0):.0f} days. {c.get('explanation', '')}"),
                          "timestamp": iso(c.get("last_reading_at"))})
        elif c.get("status") == "medium":
            items.append({**base, "level": "warning", "kind": "attention",
                          "title": f"{where} needs attention",
                          "message": c.get("explanation", ""),
                          "timestamp": iso(c.get("last_reading_at"))})
        due = next_checkup(c)
        if due is not None and due <= now + timedelta(days=7):
            overdue = due < now
            items.append({**base, "level": "warning" if overdue else "info", "kind": "checkup_due",
                          "title": f"{where}: checkup {'overdue' if overdue else 'due'}",
                          "message": (f"Inspection {'was due' if overdue else 'is due'} on {due:%d %b %Y} "
                                      f"(interval set by current status: {c.get('status')})."),
                          "timestamp": iso(due)})

    async for comp in db.complaints.find({**q, "status": "open"}).sort("timestamp", -1).limit(20):
        items.append({"level": "info", "kind": "complaint", "elevator_id": comp["elevator_id"],
                      "complaint_id": str(comp["_id"]),
                      "title": f"New complaint - {elevators.get(comp['elevator_id'], comp['elevator_id'])}",
                      "message": comp["description"], "timestamp": iso(comp["timestamp"])})

    items.sort(key=lambda i: (LEVEL_ORDER[i["level"]], i.get("timestamp") or ""))
    return items


async def recent_alerts(db, days: int = 7, limit: int = 50) -> list[dict]:
    since = utcnow() - timedelta(days=days)
    cursor = db.alerts.find({"timestamp": {"$gte": since}}).sort("timestamp", -1).limit(limit)
    return [clean(a) async for a in cursor]
