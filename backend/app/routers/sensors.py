"""Sensor input streaming (spec section 4): registry, live monitor, external ingestion."""

from __future__ import annotations

import hmac
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from elevcare_ml import physics as P

from ..config import Settings, get_settings
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services.broadcaster import broadcaster
from ..services.sensor_hub import COVERAGE, PARAMETERS, SENSOR_TYPES, SensorHub, coverage, default_sensors, sensor_doc_id

router = APIRouter(tags=["sensors"])


def hub_dep(request: Request) -> SensorHub:
    return request.app.state.hub


class SensorIn(BaseModel):
    sensor_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=120)
    type: Literal["tension_load_cell", "accelerometer", "current_transducer", "temperature", "speed_encoder", "trip_counter"]
    component_id: str
    rope_number: int | None = Field(None, ge=1, le=12)
    parameter: Literal["tension", "rope_tension", "vibration_waveform", "motor_current", "temperature", "cycle_count", "speed"]
    unit: str = Field("", max_length=16)
    interval_s: float = Field(60.0, gt=0, le=86400)
    location: str = Field("", max_length=200)
    gain: float = 1.0
    offset: float = 0.0


async def _elevator(db, elevator_id: str) -> dict:
    e = await db.elevators.find_one({"_id": elevator_id})
    if not e:
        raise HTTPException(404, "Elevator not found")
    return e


async def _rope_count(db, elevator_id: str) -> int:
    c = await db.components.find_one({"elevator_id": elevator_id, "type": "rope"}, {"physics_params": 1})
    return int((c or {}).get("physics_params", {}).get("ropes_per_set") or P.ROPES_PER_SET)


@router.get("/sensors/meta")
async def sensor_meta(_: CurrentUser):
    return {"types": SENSOR_TYPES, "parameters": PARAMETERS, "coverage": COVERAGE}


@router.get("/elevators/{elevator_id}/sensors")
async def list_sensors(elevator_id: str, _: CurrentUser, db=Depends(db_dep), hub: SensorHub = Depends(hub_dep)):
    e = await _elevator(db, elevator_id)
    comps = [c async for c in db.components.find({"elevator_id": elevator_id}, {"name": 1, "type": 1})]
    sensors = hub.sensors_of(elevator_id)
    n = await _rope_count(db, elevator_id)
    return {"elevator_id": elevator_id, "source": hub.source.get(elevator_id, e.get("sensor_source", "simulated")),
            "rope_count": n, "sensors": [hub.status_of(s["_id"]) for s in sensors],
            "components": [{"id": c["_id"], "name": c["name"], "type": c["type"]} for c in comps],
            "coverage": coverage(comps, sensors, n)}


class SourceIn(BaseModel):
    source: Literal["simulated", "external"]


@router.put("/elevators/{elevator_id}/sensors/source")
async def set_source(elevator_id: str, body: SourceIn, _: CurrentUser, request: Request, db=Depends(db_dep), hub: SensorHub = Depends(hub_dep)):
    await _elevator(db, elevator_id)
    await hub.set_source(elevator_id, body.source)
    runner = getattr(request.app.state, "runner", None)
    if body.source == "simulated" and runner and runner.running:
        await runner.tick()
    return {"elevator_id": elevator_id, "source": body.source}


@router.put("/elevators/{elevator_id}/sensors/{sensor_id}")
async def upsert_sensor(elevator_id: str, sensor_id: str, body: SensorIn, user: CurrentUser, db=Depends(db_dep),
                        hub: SensorHub = Depends(hub_dep)):
    await _elevator(db, elevator_id)
    if body.sensor_id != sensor_id:
        raise HTTPException(400, "sensor_id in path and body differ")
    comp = await db.components.find_one({"_id": body.component_id, "elevator_id": elevator_id})
    if not comp:
        raise HTTPException(400, "component does not belong to this elevator")
    if body.parameter not in SENSOR_TYPES[body.type]["params"]:
        raise HTTPException(400, f"{body.type} cannot measure {body.parameter}")
    if body.parameter == "rope_tension" and (comp["type"] != "rope" or not body.rope_number):
        raise HTTPException(400, "rope_tension needs the rope component and a rope number")
    doc = body.model_dump()
    doc.update({"elevator_id": elevator_id, "source": "external", "updated_by": user["email"]})
    if body.component_id and comp["type"] != "rope":
        doc["rope_number"] = None
    await hub.upsert(doc)
    return hub.status_of(sensor_doc_id(elevator_id, sensor_id))


@router.delete("/elevators/{elevator_id}/sensors/{sensor_id}")
async def delete_sensor(elevator_id: str, sensor_id: str, _: CurrentUser, hub: SensorHub = Depends(hub_dep)):
    if not await hub.delete(elevator_id, sensor_id):
        raise HTTPException(404, "Sensor not found")
    return {"ok": True}


@router.post("/elevators/{elevator_id}/sensors/defaults")
async def register_defaults(elevator_id: str, _: CurrentUser, db=Depends(db_dep), hub: SensorHub = Depends(hub_dep)):
    e = await _elevator(db, elevator_id)
    comps = [c async for c in db.components.find({"elevator_id": elevator_id})]
    added = 0
    for d in default_sensors(e, comps, await _rope_count(db, elevator_id)):
        if sensor_doc_id(elevator_id, d["sensor_id"]) not in hub.registry:
            await hub.upsert(d); added += 1
    return {"added": added, "total": len(hub.sensors_of(elevator_id))}


@router.get("/elevators/{elevator_id}/sensors/live")
async def live(elevator_id: str, _: CurrentUser, request: Request, db=Depends(db_dep), hub: SensorHub = Depends(hub_dep)):
    e = await _elevator(db, elevator_id)
    source = hub.source.get(elevator_id, e.get("sensor_source", "simulated"))
    sensors = hub.sensors_of(elevator_id)
    rows = [hub.status_of(s["_id"]) for s in sensors]
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("online", "stale", "offline")}
    if source == "external":
        gateway_connected = counts["online"] > 0
        gateway_status = "Online / Real-time" if gateway_connected else "Gateway disconnected — data may not be current"
    else:
        runner = getattr(request.app.state, "runner", None)
        runner_running = runner.running if runner else True
        gateway_connected = runner_running or counts["online"] > 0
        gateway_status = "Online / Real-time" if (counts["online"] > 0 or runner_running) else "Simulated"

    return {"elevator_id": elevator_id, "source": source,
            "gateway_connected": gateway_connected,
            "gateway_status": gateway_status,
            "sensors": rows, "summary": {**counts, "total": len(rows), "rate_per_min": round(sum(r["rate_per_min"] for r in rows), 1)},
            "unregistered": list(hub.unregistered)[:20], "stream_clients": broadcaster.client_count}


@router.get("/elevators/{elevator_id}/sensors/{sensor_id}/series")
async def series(elevator_id: str, sensor_id: str, _: CurrentUser, hub: SensorHub = Depends(hub_dep)):
    did = sensor_doc_id(elevator_id, sensor_id)
    if did not in hub.registry:
        raise HTTPException(404, "Sensor not found")
    out = hub.series(did)
    if out.get("waveform"):
        from elevcare_ml.signal import spectrum
        f, a = spectrum(out["waveform"])
        out["spectrum"] = {"freqs": f, "amp": a}
    return {**hub.status_of(did), **out}


# ---------------------------------------------------------------- ingestion
class SensorData(BaseModel):
    elevator_id: str
    sensor_id: str
    timestamp: datetime | None = None
    value: float | None = None
    values: list[float] | None = Field(None, max_length=4096)
    waveform: list[float] | None = Field(None, min_length=32, max_length=4096)


def _check_key(key: str, s: Settings) -> None:
    if not s.ingest_api_key:
        raise HTTPException(403, "External ingestion disabled - set INGEST_API_KEY to enable")
    if not hmac.compare_digest(key or "", s.ingest_api_key):
        raise HTTPException(401, "Invalid ingest key")


@router.post("/ingest/sensor-data")
async def ingest_sensor_data(body: SensorData, x_ingest_key: str = Header(""), s: Settings = Depends(settings_dep),
                             hub: SensorHub = Depends(hub_dep)):
    _check_key(x_ingest_key, s)
    return await hub.push(body.elevator_id, body.sensor_id, body.timestamp, body.value, body.values, body.waveform)


@router.post("/elevators/{elevator_id}/sensors/{sensor_id}/test")
async def send_test_reading(elevator_id: str, sensor_id: str, _: CurrentUser, hub: SensorHub = Depends(hub_dep)):
    """'Send test reading' button: pushes a plausible value through the same path as a gateway."""
    did = sensor_doc_id(elevator_id, sensor_id)
    d = hub.registry.get(did)
    if not d:
        raise HTTPException(404, "Sensor not found")
    sample = sample_payload(d)
    r = await hub.push(elevator_id, sensor_id, None, sample.get("value"), sample.get("values"), sample.get("waveform"))
    return {"sent": sample, "result": r}


def sample_payload(d: dict) -> dict:
    import numpy as np
    from elevcare_ml.signal import synthesize
    from elevcare_ml.simulator import NOMINAL_ROPE_TENSION_KN
    base = {"elevator_id": d["elevator_id"], "sensor_id": d["sensor_id"], "timestamp": None}
    p = d["parameter"]
    if p == "vibration_waveform":
        wave = synthesize("rope", np.array([0.05]), np.random.default_rng(1))[0]
        return {**base, "waveform": [round(float(x), 5) for x in wave[:64]]}
    val = {"tension": NOMINAL_ROPE_TENSION_KN * 6, "rope_tension": NOMINAL_ROPE_TENSION_KN, "motor_current": P.RATED_MOTOR_CURRENT_A * P.NOMINAL_CURRENT_FRACTION,
           "temperature": P.AMBIENT_TEMP_C + 10.0, "cycle_count": 1_250_000, "speed": 1.75}[p]
    return {**base, "value": round(float(val), 3)}


@router.get("/elevators/{elevator_id}/sensors/{sensor_id}/sample")
async def sample(elevator_id: str, sensor_id: str, _: CurrentUser, hub: SensorHub = Depends(hub_dep), s: Settings = Depends(settings_dep)):
    did = sensor_doc_id(elevator_id, sensor_id)
    d = hub.registry.get(did)
    if not d:
        raise HTTPException(404, "Sensor not found")
    payload = sample_payload(d)
    payload["timestamp"] = "2026-01-01T00:00:00Z"
    curl = ("curl -X POST $API/ingest/sensor-data -H 'Content-Type: application/json' "
            f"-H 'X-Ingest-Key: {'<INGEST_API_KEY>' if not s.ingest_api_key else '$INGEST_API_KEY'}' -d '{json.dumps(payload)}'")
    return {"json": payload, "curl": curl, "websocket": f"$WS/ws/ingest/{elevator_id}?key=$INGEST_API_KEY (send the same JSON per message)"}


@router.websocket("/ws/ingest/{elevator_id}")
async def ws_ingest(ws: WebSocket, elevator_id: str, key: str = ""):
    s = get_settings()
    if not s.ingest_api_key or not hmac.compare_digest(key, s.ingest_api_key):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub: SensorHub = ws.app.state.hub
    try:
        while True:
            raw = await ws.receive_text()
            try:
                body = SensorData(**{"elevator_id": elevator_id, **json.loads(raw)})
                r = await hub.push(elevator_id, body.sensor_id, body.timestamp, body.value, body.values, body.waveform)
            except Exception as exc:  # malformed message: report, keep the socket open
                r = {"accepted": False, "reason": str(exc)[:200]}
            await ws.send_text(json.dumps(r, default=str))
    except WebSocketDisconnect:
        pass
