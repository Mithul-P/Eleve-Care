"""MongoDB connection (Motor). `mongomock://` gives an in-memory DB for dev/tests."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

log = logging.getLogger(__name__)

_client = None
_db = None


async def connect(url: str, db_name: str):
    global _client, _db
    if url.startswith("mongomock://"):
        from mongomock_motor import AsyncMongoMockClient

        log.warning("Using in-memory mongomock database - data is lost on restart.")
        _client = AsyncMongoMockClient(tz_aware=True)
    else:
        from motor.motor_asyncio import AsyncIOMotorClient

        _client = AsyncIOMotorClient(url, tz_aware=True, serverSelectionTimeoutMS=10_000)
    _db = _client[db_name]
    await ensure_indexes(_db)
    return _db


async def disconnect():
    global _client, _db
    if _client is not None:
        _client.close()
    _client = _db = None


def get_db():
    if _db is None:
        raise RuntimeError("database not connected")
    return _db


async def ensure_indexes(db) -> None:
    await db.users.create_index("email", unique=True)
    await db.components.create_index("elevator_id")
    await db.sensor_readings.create_index([("component_id", 1), ("timestamp", -1)])
    await db.predictions.create_index([("component_id", 1), ("timestamp", -1)])
    await db.energy_logs.create_index([("elevator_id", 1), ("date", 1)], unique=True)
    await db.complaints.create_index([("elevator_id", 1), ("timestamp", -1)])
    await db.alerts.create_index([("timestamp", -1)])


# ---------------------------------------------------------------------------
# serialisation helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = as_utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def clean(doc: Any) -> Any:
    """Mongo document -> JSON-friendly dict (`_id` -> `id`, datetimes -> ISO Z)."""
    if isinstance(doc, list):
        return [clean(d) for d in doc]
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            out["id" if k == "_id" else k] = clean(v)
        return out
    if isinstance(doc, datetime):
        return iso(doc)
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc
