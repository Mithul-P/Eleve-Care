"""Elev Care API - FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import connect, disconnect, get_db, iso, utcnow
from .routers import analytics, auth, chatbot, complaints, components, elevators, ingest, notifications, simulator, stream
from .services.ingestion import IngestionPipeline
from .services.ml import get_predictor, load_predictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("elevcare")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # Model loading may train on first run (~1 min) - keep it off the event loop.
    await asyncio.to_thread(load_predictor, s.ml_artifact_path)
    db = await connect(s.mongo_url, s.mongo_db)
    if s.seed_on_startup:
        from .seed import seed

        if await seed(db, s):
            log.info("Database seeded with demo fleet")
    app.state.pipeline = IngestionPipeline(db, s.energy_tariff_inr_per_kwh)
    app.state.runner = None
    app.state.source = None
    if s.sim_enabled:
        from .services.runner import SimulatorRunner
        from .services.simulated_source import SimulatedSource

        app.state.source = SimulatedSource(db)
        app.state.runner = SimulatorRunner(db, s, app.state.pipeline, app.state.source)
        await app.state.runner.start()
    yield
    if app.state.runner:
        await app.state.runner.stop()
    await disconnect()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=s.app_name,
        version="0.1.0",
        description="AI + physics-informed reliability prediction for elevator suspension components.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (auth, elevators, components, analytics, complaints, chatbot, notifications, simulator, ingest, stream):
        app.include_router(r.router)

    @app.get("/health", tags=["meta"])
    async def health():
        db_ok = True
        try:
            await get_db().command("ping")
        except Exception:
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "database": db_ok,
                "model_version": get_predictor().version, "time": iso(utcnow())}

    return app


app = create_app()
