from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services.analytics import energy_report, maintenance_savings
from ..services.views import component_view, elevator_view, sort_components

router = APIRouter(prefix="/elevators", tags=["elevators"])


async def _get(db, elevator_id: str) -> dict:
    e = await db.elevators.find_one({"_id": elevator_id})
    if not e:
        raise HTTPException(404, "Elevator not found")
    return e


@router.get("")
async def list_elevators(_: CurrentUser, db=Depends(db_dep)):
    out = [await elevator_view(db, e) async for e in db.elevators.find({})]
    order = {"at-risk": 0, "medium": 1, "good": 2}
    out.sort(key=lambda e: (order.get(e["status"], 3), e["health_score"] or 0))
    return out


@router.get("/{elevator_id}")
async def get_elevator(elevator_id: str, _: CurrentUser, db=Depends(db_dep)):
    return await elevator_view(db, await _get(db, elevator_id), with_components=True)


@router.get("/{elevator_id}/components")
async def elevator_components(elevator_id: str, _: CurrentUser, db=Depends(db_dep)):
    await _get(db, elevator_id)
    comps = [c async for c in db.components.find({"elevator_id": elevator_id})]
    return [component_view(c) for c in sort_components(comps)]


@router.get("/{elevator_id}/energy")
async def elevator_energy(elevator_id: str, _: CurrentUser, range: Literal["week", "month", "quarter"] = "month",
                          db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    await _get(db, elevator_id)
    report = await energy_report(db, s, range, elevator_id)
    report["maintenance"] = await maintenance_savings(db, elevator_id)
    return report
