from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends

from ..config import Settings
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services.analytics import energy_report, maintenance_savings, sustainability_report
from ..services.ml import get_predictor

router = APIRouter(prefix="/analytics", tags=["analytics"])
Range = Literal["week", "month", "quarter"]


@router.get("/energy")
async def fleet_energy(_: CurrentUser, range: Range = "month", db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    report = await energy_report(db, s, range)
    report["maintenance"] = await maintenance_savings(db)
    return report


@router.get("/sustainability")
async def sustainability(_: CurrentUser, range: Range = "month", db=Depends(db_dep),
                         s: Settings = Depends(settings_dep)):
    return await sustainability_report(db, s, range)


@router.get("/model")
async def model_info(_: CurrentUser):
    """Model card: version, held-out metrics and feature importances (for judges)."""
    p = get_predictor()
    from elevcare_ml import physics as P
    return {
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
