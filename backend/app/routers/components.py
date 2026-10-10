from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from elevcare_ml import materials as M
from elevcare_ml.signal import analyze_waveform

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
    # Replacement only - what material the new part is made of, so the spec
    # used by the physics laws is updated at the same moment the part is
    # actually swapped, not left stale from before the fault. Either an
    # industry preset id (schema.presets from GET .../material-spec) or a
    # full spec dict; if neither is given the existing material_spec is kept.
    material_preset_id: str | None = None
    material_spec: dict | None = None


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
        "ml_status": c.get("ml_status"),
        "status_source": c.get("status_source"),
        "confidence": c.get("confidence"),
        "rules_fired": c.get("rules_fired", []),
        "physics_severity": c.get("physics_severity"),
        "physics": c.get("physics"),
        "life_fraction": c.get("fatigue_index"),
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
              "failure_probability": p["failure_probability"], "rul_days": p["RUL_days"], "status": p["status"],
              "life_fraction": p.get("life_fraction"), "rules_fired": p.get("rules_fired", [])}
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
    """
    Latest recorded accelerometer trace with a full waveform analysis: time
    statistics, FFT spectrum, envelope (Hilbert) spectrum, the component's
    characteristic fault frequencies with SNR, band energy and a diagnosis.
    """
    c = await _get(db, component_id)
    r = await db.sensor_readings.find_one(
        {"component_id": component_id, "vibration_waveform": {"$ne": None}}, sort=[("timestamp", -1)])
    if not r:
        raise HTTPException(404, "No waveform recorded yet")
    analysis = analyze_waveform(c["type"], r["vibration_waveform"])
    return {"component_id": component_id, "timestamp": iso(r["timestamp"]), "sample_rate_hz": analysis["sample_rate_hz"],
            "waveform": r["vibration_waveform"], "spectrum": analysis["spectrum"],
            "envelope_spectrum": analysis["envelope_spectrum"], "envelope": analysis["envelope"],
            "analysis": {k: analysis[k] for k in ("stats", "peaks", "envelope_peaks", "characteristic_lines",
                                                  "band_energy_pct", "defect_band_pct", "level", "headline", "findings")},
            "features": r["features"], "rope_tensions": r.get("rope_tensions"), "mode": r.get("mode")}


class WaveformIn(BaseModel):
    waveform: list[float] = Field(..., min_length=32, max_length=4096)
    sample_rate_hz: int = Field(1000, ge=100, le=50_000)


@router.post("/{component_id}/waveform/analyze")
async def analyze_uploaded_waveform(component_id: str, body: WaveformIn, _: CurrentUser, db=Depends(db_dep)):
    """Analyse an arbitrary trace (e.g. from a handheld analyser) for this component type."""
    c = await _get(db, component_id)
    a = analyze_waveform(c["type"], body.waveform, body.sample_rate_hz)
    return {"component_id": component_id, **a}


# ---------------------------------------------------------------- material specification (spec section 2)
def material_view(c: dict) -> dict:
    spec = c.get("material_spec") or {}
    res = M.resolve(c["type"], spec)
    return {"component_id": c["_id"], "type": c["type"], "spec": spec, "schema": M.schema(c["type"]),
            "completeness_pct": res["completeness_pct"], "blank_fields": M.blank_fields(c["type"], spec),
            "gsm_applicable": M.gsm_applicable(c["type"], spec.get("material_type")),
            "physics_inputs": res["inputs"], "physics_params": res["params"], "updated_at": clean(c.get("material_spec_updated_at"))}


@router.get("/{component_id}/material-spec")
async def get_material_spec(component_id: str, _: CurrentUser, db=Depends(db_dep)):
    return material_view(await _get(db, component_id))


class MaterialSpecIn(BaseModel):
    spec: dict = Field(default_factory=dict)


@router.put("/{component_id}/material-spec")
async def put_material_spec(component_id: str, body: MaterialSpecIn, user: CurrentUser, request: Request, db=Depends(db_dep)):
    """Save the spec, resolve it into physics parameters and trigger a fresh prediction."""
    c = await _get(db, component_id)
    spec = {k: v for k, v in body.spec.items() if not str(k).startswith("$")}
    if c["type"] != "traction" and not M.gsm_applicable(c["type"], spec.get("material_type")):
        spec["gsm"] = "Not applicable"
    if c["type"] == "traction":
        rows = spec.get("rows") or []
        for row in rows:
            if not M.gsm_applicable("traction", row.get("material_type")):
                row["gsm"] = "Not applicable"
        spec["rows"] = rows
    res = M.resolve(c["type"], spec)
    now = utcnow()
    await db.components.update_one({"_id": component_id}, {"$set": {
        "material_spec": spec, "physics_params": res["params"], "material_spec_updated_at": now,
        "material_spec_updated_by": user["email"]}})
    pred = await request.app.state.pipeline.repredict(component_id, "material spec updated")
    out = material_view(await _get(db, component_id))
    out["repredicted"] = pred is not None
    out["prediction"] = {k: pred[k] for k in ("health_score", "failure_probability", "rul_days", "status")} if pred else None
    return out


@router.post("/{component_id}/material-spec/preset/{preset_id}")
async def preset_values(component_id: str, preset_id: str, _: CurrentUser, db=Depends(db_dep)):
    c = await _get(db, component_id)
    try:
        return M.apply_preset(c["type"], preset_id)
    except KeyError:
        raise HTTPException(404, "Unknown preset")


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
                       "rules_fired": [], "candidate_status": None, "status_streak": 0, "ml_status": "good",
                       "status_source": "maintenance", "confidence": "low",
                       "explanation": f"Replaced on {now:%d %b %Y} - awaiting fresh sensor readings."})
        # The material used for the new part (spec section 2/8): a chosen
        # preset wins over a raw spec dict; if neither is given, the previous
        # part's material_spec carries over unchanged (still a valid choice -
        # e.g. a like-for-like replacement).
        new_spec = None
        if body.material_preset_id:
            try:
                new_spec = M.apply_preset(c["type"], body.material_preset_id)
            except KeyError:
                raise HTTPException(404, "Unknown material preset")
        elif body.material_spec is not None:
            new_spec = {k: v for k, v in body.material_spec.items() if not str(k).startswith("$")}
        if new_spec is not None:
            if c["type"] != "traction" and not M.gsm_applicable(c["type"], new_spec.get("material_type")):
                new_spec["gsm"] = "Not applicable"
            res = M.resolve(c["type"], new_spec)
            update.update({"material_spec": new_spec, "physics_params": res["params"],
                           "material_spec_updated_at": now, "material_spec_updated_by": user["email"]})
        # start a fresh feature window so pre-replacement readings don't linger
        request.app.state.pipeline.windows[component_id] = ComponentWindow()
        source = getattr(request.app.state, "source", None)
        if source is not None:
            await source.on_maintenance(component_id, "replacement")
    await db.components.update_one({"_id": component_id}, {"$set": update})
    await refresh_elevator_status(db, c["elevator_id"])
    await broadcaster.publish("maintenance", clean(log))
    return {"ok": True, "log": clean(log), "component": component_view(await _get(db, component_id))}
