from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends

from ..config import Settings
from ..db import clean
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services.analytics import energy_report, maintenance_savings, sustainability_report
from ..services.ml import get_predictor
from ..services.views import material_tag

router = APIRouter(prefix="/analytics", tags=["analytics"])
Range = Literal["week", "month", "year"]


@router.get("/materials")
async def materials_overview(_: CurrentUser, db=Depends(db_dep)):
    """Fleet-wide material specification list (spec section 2/8): every

    component of every elevator in one table, so the person doesn't have to
    open each elevator's Materials & specification page to see what's used
    where, or what still needs filling in.
    """
    from elevcare_ml import materials as M

    names = {e["_id"]: e.get("name") async for e in db.elevators.find({}, {"name": 1})}
    rows = []
    async for c in db.components.find(
        {}, {"name": 1, "type": 1, "elevator_id": 1, "material_spec": 1,
             "material_spec_updated_at": 1, "material_spec_updated_by": 1}):
        tag = material_tag(c)
        rows.append({
            "component_id": c["_id"],
            "component_name": c["name"],
            "component_type": c["type"],
            "elevator_id": c["elevator_id"],
            "elevator_name": names.get(c["elevator_id"], c["elevator_id"]),
            "material_type": tag["material_type"],
            "material_grade": tag["material_grade"],
            "material_label": tag["label"],
            "completeness_pct": M.completeness(c["type"], c.get("material_spec")),
            "updated_at": clean(c.get("material_spec_updated_at")),
            "updated_by": c.get("material_spec_updated_by"),
        })
    rows.sort(key=lambda r: ((r["elevator_name"] or ""), r["component_type"]))
    overall = round(sum(r["completeness_pct"] for r in rows) / len(rows)) if rows else 0
    return {"components": rows, "overall_completeness_pct": overall,
            "incomplete_count": sum(1 for r in rows if r["completeness_pct"] < 100)}


@router.get("/energy")
async def fleet_energy(_: CurrentUser, range: Range = "month", elevator_id: str | None = None,
                       db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    report = await energy_report(db, s, range, elevator_id)
    report["maintenance"] = await maintenance_savings(db, elevator_id)
    return report


@router.get("/sustainability")
async def sustainability(_: CurrentUser, range: Range = "month", elevator_id: str | None = None,
                         db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    return await sustainability_report(db, s, range, elevator_id)


@router.get("/model")
async def model_info(_: CurrentUser, db=Depends(db_dep)):
    """Model card: version, held-out metrics and feature importances (for judges)."""
    p = get_predictor()
    from elevcare_ml import physics as P
    from elevcare_ml.rules import RULE_CATALOG
    from elevcare_ml import materials as M
    physics_inputs = []
    async for c in db.components.find({}, {"name": 1, "type": 1, "elevator_id": 1, "material_spec": 1}):
        res = M.resolve(c["type"], c.get("material_spec"))
        physics_inputs.append({"component_id": c["_id"], "name": c["name"], "type": c["type"], "elevator_id": c["elevator_id"],
                               "material": material_tag(c),
                               "completeness_pct": res["completeness_pct"], "inputs": res["inputs"]})
    return {
        "physics_models": P.PHYSICS_MODEL,
        "physics_inputs": physics_inputs,
        "physics_defaults": {"rope_breaking_load_kn": P.ROPE_BREAKING_LOAD_KN, "d_over_d": P.ROPE_D_OVER_D,
                             "sheave_hardness_pa": P.SHEAVE_HARDNESS_PA, "bearing_c_kn": P.BEARING_C_KN,
                             "rated_temp_rise_c": P.TRACTION_RATED_TEMP_RISE_C},
        "rules": RULE_CATALOG,
        "version": p.version,
        "trained_at": p.bundle["trained_at"],
        "horizon_days": p.bundle["horizon_days"],
        "algorithm": "HistGradientBoosting (monotonic constraints) + physics severity blend",
        "feature_names": p.bundle["feature_names"],
        "metrics": p.metrics,
        "importances": {k: p.importances(k) for k in P.COMPONENT_TYPES},
        "thresholds": {ct: {f: {"healthy": t.healthy, "alarm": t.alarm, "unit": t.unit, "label": t.label}
                            for f, t in P.THRESHOLDS[ct].items()} for ct in P.COMPONENT_TYPES},
    }
