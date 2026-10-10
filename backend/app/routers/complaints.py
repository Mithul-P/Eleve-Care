"""Complaints: public (no-login) submission + staff/admin management."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import clean, utcnow
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services.broadcaster import broadcaster

router = APIRouter(tags=["complaints"])

_recent: dict[str, deque] = defaultdict(deque)


class ComplaintIn(BaseModel):
    elevator_id: str = Field(..., max_length=40)
    description: str = Field(..., min_length=10, max_length=1000)
    category: Literal["noise/vibration", "jerky ride", "door", "stuck/trapped", "other"] = "other"
    contact: str | None = Field(None, max_length=120, description="optional phone/email for follow-up")


class ComplaintUpdate(BaseModel):
    status: Literal["open", "in-progress", "resolved", "dismissed"]
    note: str = Field("", max_length=1000)


def _rate_limit(request: Request, per_minute: int) -> None:
    key = request.client.host if request.client else "unknown"
    q = _recent[key]
    now = time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(429, "Too many complaints from this device - please try again in a minute")
    q.append(now)


# ---- public (no login) ------------------------------------------------------

@router.get("/public/elevators", tags=["public"])
async def public_elevators(db=Depends(db_dep)):
    """Minimal list for the public complaint form - no health or component data."""
    return [{"id": e["_id"], "name": e["name"], "building": e["building"]}
            async for e in db.elevators.find({}, {"name": 1, "building": 1}).sort("_id", 1)]


@router.get("/public/elevators/{elevator_id}", tags=["public"])
async def public_elevator(elevator_id: str, db=Depends(db_dep)):
    e = await db.elevators.find_one({"_id": elevator_id}, {"name": 1, "building": 1})
    if not e:
        raise HTTPException(404, "Elevator not found")
    return {"id": e["_id"], "name": e["name"], "building": e["building"]}


@router.post("/complaints", status_code=201)
async def submit_complaint(body: ComplaintIn, request: Request, db=Depends(db_dep),
                           s: Settings = Depends(settings_dep)):
    _rate_limit(request, s.complaint_rate_limit_per_minute)
    e = await db.elevators.find_one({"_id": body.elevator_id}, {"name": 1})
    if not e:
        raise HTTPException(404, "Elevator not found")
    doc = {"_id": f"cmp-{secrets.token_hex(6)}", "elevator_id": body.elevator_id, "timestamp": utcnow(),
           "description": body.description.strip(), "category": body.category,
           "contact": (body.contact or "").strip() or None, "status": "open",
           "submitted_without_login": True, "history": []}
    await db.complaints.insert_one(doc)
    await broadcaster.publish("complaint", {**clean(doc), "elevator_name": e["name"]})
    return {"id": doc["_id"], "message": "Thank you - your complaint has been logged and the maintenance team notified."}


# ---- staff / admin ------------------------------------------------------------

@router.get("/complaints")
async def list_complaints(_: CurrentUser, elevator_id: str | None = None, status: str | None = None,
                          limit: int = 100, db=Depends(db_dep)):
    q: dict = {}
    if elevator_id:
        q["elevator_id"] = elevator_id
    if status:
        q["status"] = {"$in": status.split(",")}
    names = {e["_id"]: e["name"] async for e in db.elevators.find({}, {"name": 1})}
    out = []
    async for c in db.complaints.find(q).sort("timestamp", -1).limit(min(limit, 500)):
        row = clean(c)
        row["elevator_name"] = names.get(c["elevator_id"])
        out.append(row)
    return out


@router.patch("/complaints/{complaint_id}")
async def update_complaint(complaint_id: str, body: ComplaintUpdate, user: CurrentUser, db=Depends(db_dep)):
    c = await db.complaints.find_one({"_id": complaint_id})
    if not c:
        raise HTTPException(404, "Complaint not found")
    entry = {"at": utcnow(), "by": user["name"], "status": body.status, "note": body.note}
    await db.complaints.update_one({"_id": complaint_id},
                                   {"$set": {"status": body.status, "updated_at": utcnow()},
                                    "$push": {"history": entry}})
    updated = clean(await db.complaints.find_one({"_id": complaint_id}))
    await broadcaster.publish("complaint_update", updated)
    return updated
