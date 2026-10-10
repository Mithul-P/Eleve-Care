"""Shared FastAPI dependencies for app-level services."""

from __future__ import annotations

from fastapi import HTTPException, Request

from .config import Settings, get_settings
from .db import get_db


def db_dep():
    return get_db()


def settings_dep() -> Settings:
    return get_settings()


def runner_dep(request: Request):
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(503, "Simulator is disabled (SIM_ENABLED=false)")
    return runner


def pipeline_dep(request: Request):
    return request.app.state.pipeline
