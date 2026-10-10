"""
Operational data, usage cycles, calendar energy and recommendations.

Everything here reads from `services.opdata`, which is deterministic and tied
to the real clock, so the same figures appear on every page and on every
render (Section 7).
"""

from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..config import Settings
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services import opdata
from ..services.recommendations import elevator_recommendations, fleet_recommendations
from ..services.risk import RISK_META, THRESHOLDS

router = APIRouter(tags=["operations"])

CycleView = Literal["week", "month", "year"]
EnergyView = Literal["month", "batch", "week", "year"]
COMPONENT_TYPES = list(opdata.CYCLE_DEFINITION.keys())


async def _elevator(db, elevator_id: str) -> dict:
    e = await db.elevators.find_one({"_id": elevator_id})
    if not e:
        raise HTTPException(404, "Elevator not found")
    return e


async def _all_elevators(db) -> list[dict]:
    return [e async for e in db.elevators.find({})]


# ---------------------------------------------------------------------------
# Section 1 - calendar energy (month-wise / batch-wise), frozen history
# ---------------------------------------------------------------------------

@router.get("/analytics/energy/calendar")
async def energy_calendar(_: CurrentUser, view: EnergyView = "month", elevator_id: str | None = None,
                          db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    """Month-wise or batch-wise energy.

    Repeated calls with the same parameters return byte-identical figures:
    the generator is seeded per (elevator, period) and the only thing that can
    move a number is the real clock advancing into a new period.
    """
    elevators = [await _elevator(db, elevator_id)] if elevator_id else await _all_elevators(db)
    report = opdata.energy_calendar(elevators, s.energy_tariff_inr_per_kwh, view)
    report["elevator_id"] = elevator_id
    report["per_elevator"] = [
        {
            "elevator_id": e["_id"],
            "name": e.get("name"),
            **{k: v for k, v in opdata.energy_calendar([e], s.energy_tariff_inr_per_kwh, view)["totals"].items()},
        }
        for e in elevators
    ]
    report["per_elevator"].sort(key=lambda r: -r["energy_kwh"])
    return report


# ---------------------------------------------------------------------------
# Section 3 - KONE operational data inputs
# ---------------------------------------------------------------------------

@router.get("/elevators/{elevator_id}/operational")
async def operational(elevator_id: str, _: CurrentUser, days: int = 30, db=Depends(db_dep)):
    """The seven operational inputs, with a trend series and an error log."""
    e = await _elevator(db, elevator_id)
    summary = opdata.operational_summary(e, min(max(days, 7), 90))
    summary["elevator_name"] = e.get("name")
    summary["risk"] = opdata.operational_risk_uplift(e)
    return summary


@router.get("/elevators/{elevator_id}/operational/risk")
async def operational_risk(elevator_id: str, _: CurrentUser, db=Depends(db_dep)):
    """Which operational inputs are pushing this elevator's risk up, and by how much."""
    e = await _elevator(db, elevator_id)
    return opdata.operational_risk_uplift(e)


# ---------------------------------------------------------------------------
# Section 4 - usage cycles
# ---------------------------------------------------------------------------

@router.get("/elevators/{elevator_id}/cycles")
async def elevator_cycles(elevator_id: str, _: CurrentUser, view: CycleView = "week",
                          component_type: str | None = None, db=Depends(db_dep)):
    """Cycle counts for a lift and each of its components, bucketed by the view.

    With no `component_type`, every component type is returned so the page can
    render the lift total and the per-component breakdown from one call.
    """
    e = await _elevator(db, elevator_id)
    types = [component_type] if component_type else COMPONENT_TYPES
    for t in types:
        if t not in opdata.CYCLE_DEFINITION:
            raise HTTPException(400, f"Unknown component type '{t}'")
    series = {t: opdata.cycle_series(e, t, view) for t in types}
    lifetime = {t: opdata.lifetime_cycles(e, t) for t in types}
    # The lift's own usage, so components can be checked against it.
    lift = opdata.cycle_series(e, "traction", view)   # one motor start per trip = one lift trip
    age_and_usage = opdata.elevator_age_and_usage(e)
    # Service history isolated strictly by elevator_id (Section 11)
    service_history = []
    async for m in db.maintenance_logs.find({"elevator_id": elevator_id}).sort("timestamp", -1).limit(25):
        service_history.append({
            "id": str(m.get("_id")),
            "date": m.get("timestamp").isoformat() if hasattr(m.get("timestamp"), "isoformat") else str(m.get("timestamp")),
            "action": m.get("kind", "Maintenance"),
            "component": m.get("component_name") or m.get("component_id"),
            "notes": m.get("notes", ""),
            "by": m.get("by", "Staff"),
        })
    return {
        "elevator_id": elevator_id,
        "elevator_name": e.get("name"),
        "view": view,
        "period_label": lift["period_label"],
        "lift_trips": {"buckets": lift["buckets"], "total": lift["total"]},
        "components": series,
        "lifetime": lifetime,
        "age_and_usage": age_and_usage,
        "service_history": service_history,
        "definitions": opdata.CYCLE_DEFINITION,
    }


@router.get("/elevators/{elevator_id}/service-history")
async def elevator_service_history(elevator_id: str, _: CurrentUser, db=Depends(db_dep)):
    """Service history for one elevator strictly isolated by elevator_id (Section 11)."""
    e = await _elevator(db, elevator_id)
    history = []
    async for m in db.maintenance_logs.find({"elevator_id": elevator_id}).sort("timestamp", -1).limit(50):
        history.append({
            "id": str(m.get("_id")),
            "date": m.get("timestamp").isoformat() if hasattr(m.get("timestamp"), "isoformat") else str(m.get("timestamp")),
            "action": m.get("kind", "Maintenance"),
            "component": m.get("component_name") or m.get("component_id"),
            "notes": m.get("notes", ""),
            "by": m.get("by", "Staff"),
        })
    return {"elevator_id": elevator_id, "elevator_name": e.get("name"), "history": history}


@router.get("/analytics/cycles/comparison")
async def cycles_comparison(_: CurrentUser, component_type: str | None = None, db=Depends(db_dep)):
    """Lift 1 vs Lift 2 vs Lift 3 - week / month / year totals side by side."""
    if component_type and component_type not in opdata.CYCLE_DEFINITION:
        raise HTTPException(400, f"Unknown component type '{component_type}'")
    types = [component_type] if component_type else COMPONENT_TYPES
    rows = []
    for e in await _all_elevators(db):
        row = {"elevator_id": e["_id"], "name": e.get("name"), "by_type": {}}
        for t in types:
            row["by_type"][t] = opdata.cycle_totals(e, t)
        # Headline totals use motor starts, i.e. one per trip.
        row.update(opdata.cycle_totals(e, component_type or "traction"))
        rows.append(row)
    rows.sort(key=lambda r: -r["year"])
    return {"component_type": component_type or "traction", "rows": rows,
            "definitions": opdata.CYCLE_DEFINITION}


@router.get("/analytics/cycles/monthly")
async def cycles_monthly(_: CurrentUser, component_type: str | None = None, db=Depends(db_dep)):
    """Month-wise (Jan-Dec) counts for every lift and every component type."""
    types = [component_type] if component_type else COMPONENT_TYPES
    out = []
    for e in await _all_elevators(db):
        for t in types:
            for row in opdata.monthly_table(e, t):
                out.append({"elevator_id": e["_id"], "elevator": e.get("name"),
                            "component_type": t, **row})
    return {"rows": out}


@router.get("/analytics/cycles/monthly.csv")
async def cycles_monthly_csv(_: CurrentUser, component_type: str | None = None, db=Depends(db_dep)):
    """The same month-wise table as a CSV download (Section 4)."""
    data = await cycles_monthly(_, component_type, db)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Elevator", "Component", "Month", "Cycles", "State"])
    for r in data["rows"]:
        w.writerow([r["elevator"], r["component_type"], r["month"], r["cycles"], r["state"]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="elev-care-monthly-cycles.csv"'},
    )


# ---------------------------------------------------------------------------
# Sections 5 & 6 - risk vocabulary and recommendations
# ---------------------------------------------------------------------------

@router.get("/risk/thresholds")
async def risk_thresholds(_: CurrentUser):
    """The single risk definition the frontend uses for badges and chart lines."""
    return {"levels": RISK_META, "thresholds": THRESHOLDS}


@router.get("/elevators/{elevator_id}/recommendations")
async def elevator_recs(elevator_id: str, _: CurrentUser, db=Depends(db_dep)):
    e = await _elevator(db, elevator_id)
    usage = {t: opdata.lifetime_cycles(e, t)["used_pct"] for t in ("rope", "sheave", "bearing", "traction")}
    return await elevator_recommendations(db, e, usage)


@router.get("/analytics/recommendations")
async def fleet_recs(_: CurrentUser, db=Depends(db_dep)):
    return {"elevators": await fleet_recommendations(db)}
