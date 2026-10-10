from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import db_dep
from ..security import CurrentUser
from ..services.notifications import build_notifications, recent_alerts

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def notifications(_: CurrentUser, elevator_id: str | None = None, db=Depends(db_dep)):
    items = await build_notifications(db, elevator_id)
    alerts = await recent_alerts(db)
    if elevator_id:
        alerts = [a for a in alerts if a.get("elevator_id") == elevator_id]
    return {"items": items, "recent_alerts": alerts,
            "counts": {lvl: sum(1 for i in items if i["level"] == lvl) for lvl in ("critical", "warning", "info")}}
