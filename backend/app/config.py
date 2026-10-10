"""Runtime configuration - everything comes from environment variables (.env)."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Elev Care API"
    environment: str = "development"

    # --- database ---
    # Use "mongomock://" to run fully in-memory without a MongoDB server (dev/tests only).
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "elevcare"

    # --- auth ---
    jwt_secret: str = Field(default="", description="HS256 signing secret; random per process if unset")
    jwt_expire_minutes: int = 12 * 60

    # Seed users. Passwords must come from the environment; if a password is not
    # provided, a random one is generated and printed once to the server log.
    seed_admin_email: str = "admin@elevcare.local"
    seed_admin_password: str = ""
    seed_staff_email: str = "staff@elevcare.local"
    seed_staff_password: str = ""

    # --- seeding ---
    seed_on_startup: bool = True
    seed_history_days: int = 60
    seed_random_seed: int = 42

    # --- simulator ---
    sim_enabled: bool = True
    sim_mode: Literal["batch", "real-time"] = "batch"
    sim_batch_hours: float = 6.0                 # simulated time covered by one batch chunk
    sim_batch_interval_seconds: float = 6 * 3600  # wall-clock time between chunks
    sim_realtime_interval_seconds: float = 3.0
    sim_realtime_acceleration: float = 1.0       # simulated seconds per wall-clock second
    sim_realtime_waveform_every: int = 5         # persist full waveform every Nth real-time reading

    # --- ML ---
    ml_artifact_path: str = ""                   # default: ml/artifacts/models.joblib

    # --- external sensor ingestion ---
    ingest_api_key: str = ""                     # enables POST /ingest/readings when set

    # --- chatbot (Claude) ---
    anthropic_api_key: str = ""                  # optional; rule-based assistant is used when absent
    chatbot_model: str = "claude-sonnet-4-6"
    chatbot_max_tokens: int = 1200

    # --- economics / sustainability ---
    energy_tariff_inr_per_kwh: float = 5.0
    grid_emission_kg_per_kwh: float = 0.71       # India grid average (CEA CO2 baseline database)

    # --- HTTP ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    complaint_rate_limit_per_minute: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.jwt_secret:
        s.jwt_secret = secrets.token_urlsafe(48)
        log.warning("JWT_SECRET not set - using a random per-process secret (tokens reset on restart).")
    return s
