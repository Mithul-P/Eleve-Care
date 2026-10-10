"""External sensor ingestion - the drop-in path for a real sensor gateway."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import Settings
from ..deps import pipeline_dep, settings_dep
from ..services.ingestion import SensorReading

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestBatch(BaseModel):
    readings: list[SensorReading] = Field(..., min_length=1, max_length=5000)


@router.post("/readings")
async def ingest_readings(body: IngestBatch, request: Request, x_ingest_key: str = Header(""),
                          s: Settings = Depends(settings_dep), pipeline=Depends(pipeline_dep)):
    if not s.ingest_api_key:
        raise HTTPException(403, "External ingestion disabled - set INGEST_API_KEY to enable")
    if not hmac.compare_digest(x_ingest_key, s.ingest_api_key):
        raise HTTPException(401, "Invalid ingest key")
    for r in body.readings:
        r.mode = "external"
    return await pipeline.ingest(body.readings)
