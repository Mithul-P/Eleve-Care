from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from elevcare_ml.signal import spectrum

from ..db import as_utc, clean, iso, utcnow
from ..deps import db_dep
from ..security import CurrentUser
from ..services.broadcaster import broadcaster
from ..services.ingestion import ComponentWindow
from ..services.views import component_view, refresh_elevator_status

router = APIRouter(prefix="/components", tags=["components"])

RANGE_DAYS = {"week": 7, "month": 30}
SENSOR_FIELDS = ["tension_imbalance_pct", "vib_rms", "vib_kurtosis", "vib_crest", "fault_band_ratio",
                 "motor_current_ratio", "temperature_rise", "tension_deviation_pct"]


class MaintenanceRequest(BaseModel):
    kind: Literal["inspection", "replacement"]
    notes: str = Field("", max_length=1000)


async def _get(db, component_id: str) -> dict:
    c = await db.components.find_one({"_id": component_id})
    if not c:
        raise HTTPException(404, "Component not found")
    return c


def _downsample(rows: list, max_points: int) -> list:
    if len(rows) <= max_points:
        return rows
    step = len(rows) / max_points
    picked = [rows[int(i * step)] for i in range(max_points)]
    picked[-1] = rows[-1]
    return picked


@router.get("/{component_id}")
async def get_component(component_id: str, _: CurrentUser, db=Depends(db_dep)):
    c = await _get(db, component_id)
    out = component_view(c)
    e = await db.elevators.find_one({"_id": c["elevator_id"]}, {"name": 1, "building": 1, "is_demo": 1})
    out["elevator"] = clean(e)
    out["maintenance_log"] = [clean(m) async for m in
                              db.maintenance_logs.find({"component_id": component_id}).sort("timestamp", -1).limit(10)]
    return out


@router.get("/{component_id}/prediction")
async def get_prediction(component_id: str, _: CurrentUser, db=Depends(db_dep)):
    c = await _get(db, component_id)
    return {
        "component_id": component_id,
        "timestamp": iso(c.get("last_reading_at")),
        "model_version": c.get("model_version"),
        "health_score": c.get("health_score"),
        "failure_probability": c.get("failure_probability"),
        "horizon_days": 30,
        "RUL_days": c.get("rul_days"),
        "status": c.get("status"),
        "physics_severity": c.get("physics_severity"),
        "contributing_factors": c.get("contributing_factors", []),
        "explanation": c.get("explanation"),
        "features": c.get("latest_features"),
    }


@router.get("/{component_id}/history")
async def get_history(component_id: str, _: CurrentUser, range: Literal["week", "month"] = "week",
                      db=Depends(db_dep)):
    c = await _get(db, component_id)
    end = as_utc(c.get("last_reading_at")) or utcnow()
    since = end - timedelta(days=RANGE_DAYS[range])
    preds = [{"t": iso(p["timestamp"]), "health_score": p["health_score"],
              "failure_probability": p["failure_probability"], "rul_days": p["RUL_days"], "status": p["status"]}
             async for p in db.predictions.find({"component_id": component_id, "timestamp": {"$gte": since}})
             .sort("timestamp", 1)]
    sensors = []
    cursor = db.sensor_readings.find(
        {"component_id": component_id, "timestamp": {"$gte": since}},
        {"timestamp": 1, "features": 1, "tension": 1, "motor_current": 1, "temperature": 1}).sort("timestamp", 1)
    async for r in cursor:
        row = {"t": iso(r["timestamp"]), "tension": r["tension"], "motor_current": r["motor_current"],
               "temperature": r["temperature"]}
        row.update({k: r["features"].get(k) for k in SENSOR_FIELDS})
        sensors.append(row)
    return {"component_id": component_id, "range": range, "from": iso(since), "to": iso(end),
            "predictions": _downsample(preds, 240), "sensors": _downsample(sensors, 400)}


@router.get("/{component_id}/waveform")
async def get_waveform(component_id: str, _: CurrentUser, db=Depends(db_dep)):
    await _get(db, component_id)
    r = await db.sensor_readings.find_one(
        {"component_id": component_id, "vibration_waveform": {"$ne": None}}, sort=[("timestamp", -1)])
    if not r:
        raise HTTPException(404, "No waveform recorded yet")
    freqs, amps = spectrum(r["vibration_waveform"])
    return {"component_id": component_id, "timestamp": iso(r["timestamp"]), "sample_rate_hz": 1000,
            "waveform": r["vibration_waveform"], "spectrum": {"freq_hz": freqs, "amplitude_g": amps},
            "features": r["features"], "rope_tensions": r.get("rope_tensions"), "mode": r.get("mode")}


@router.post("/{component_id}/maintenance")
async def record_maintenance(component_id: str, body: MaintenanceRequest, user: CurrentUser, request: Request,
                             db=Depends(db_dep)):
    c = await _get(db, component_id)
    now = utcnow()
    log = {"_id": f"mnt-{int(now.timestamp() * 1000)}", "component_id": component_id,
           "elevator_id": c["elevator_id"], "kind": body.kind, "notes": body.notes,
           "timestamp": now, "by": user["name"], "status_before": c.get("status"),
           "health_before": c.get("health_score")}
    await db.maintenance_logs.insert_one(log)
    update: dict = {"last_inspection": now}
    if body.kind == "replacement":
        update.update({"install_date": now, "fatigue_index": 0.0, "status": "good", "health_score": 100.0,
                       "failure_probability": 0.0, "physics_severity": 0.0, "contributing_factors": [],
                       "explanation": f"Replaced on {now:%d %b %Y} - awaiting fresh sensor readings."})
        # start a fresh feature window so pre-replacement readings don't linger
        request.app.state.pipeline.windows[component_id] = ComponentWindow()
        source = getattr(request.app.state, "source", None)
        if source is not None:
            await source.on_maintenance(component_id, "replacement")
    await db.components.update_one({"_id": component_id}, {"$set": update})
    await refresh_elevator_status(db, c["elevator_id"])
    await broadcaster.publish("maintenance", clean(log))
    return {"ok": True, "log": clean(log), "component": component_view(await _get(db, component_id))}
