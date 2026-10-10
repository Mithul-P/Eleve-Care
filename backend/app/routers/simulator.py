from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import runner_dep
from ..security import AdminUser, CurrentUser

router = APIRouter(prefix="/simulator", tags=["simulator"])


class ToggleRequest(BaseModel):
    mode: Literal["batch", "real-time"] | None = None  # omit to flip the current mode


@router.get("/status")
async def status(_: CurrentUser, runner=Depends(runner_dep)):
    return runner.status()


@router.post("/toggle-mode")
async def toggle_mode(_: AdminUser, body: ToggleRequest | None = None, runner=Depends(runner_dep)):
    mode = body.mode if body and body.mode else ("real-time" if runner.mode == "batch" else "batch")
    return await runner.set_mode(mode)


@router.post("/run-batch")
async def run_batch(_: AdminUser, runner=Depends(runner_dep)):
    """Generate the next batch chunk immediately (handy in demos)."""
    result = await runner.run_batch()
    return {"result": result, "status": runner.status()}
