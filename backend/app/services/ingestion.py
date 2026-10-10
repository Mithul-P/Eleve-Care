"""
Sensor ingestion pipeline - the seam between data sources and everything else.

    [SensorSource] --SensorReading--> IngestionPipeline --> sensor_readings
      simulator                         | features            predictions
      (later: MQTT / gateway /          | fatigue (Miner)     components
       POST /ingest/readings)           | ML prediction       energy_logs
                                        | energy + alerts     alerts + WebSocket

Any source only has to emit `SensorReading` objects. Swapping the simulator
for a real sensor feed therefore needs no change to the ML, API or UI code.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from elevcare_ml import physics as P
from elevcare_ml.features import TREND_WINDOW_DAYS, single_reading_features, window_features

from ..db import as_utc, clean, iso
from . import energy as E
from .broadcaster import broadcaster
from .ml import get_predictor
from .views import refresh_elevator_status

log = logging.getLogger(__name__)

STATUS_RANK = {"good": 0, "medium": 1, "at-risk": 2}
WINDOW_SPACING = timedelta(hours=1)
PREDICTION_SAVE_SPACING = timedelta(hours=1)


class SensorReading(BaseModel):
    """Canonical reading contract - what any sensor source must provide."""

    component_id: str
    timestamp: datetime
    tension: float = Field(..., description="mean suspension rope tension, kN")
    rope_tensions: list[float] | None = Field(None, description="per-rope tensions, kN (rope sets)")
    vibration_waveform: list[float] = Field(..., min_length=32, max_length=4096,
                                            description="accelerometer samples, g @ 1 kHz")
    motor_current: float = Field(..., description="traction motor current, A")
    temperature: float = Field(..., description="component temperature, C")
    cycle_count: float = Field(..., ge=0, description="cumulative trips")
    mode: Literal["batch", "real-time", "external"] = "external"


class SensorSource(Protocol):
    """A producer of SensorReadings (simulator today, real gateway tomorrow)."""

    name: str

    async def on_maintenance(self, component_id: str, kind: str) -> None: ...


def _days(ts: datetime) -> float:
    return as_utc(ts).timestamp() / 86400.0


@dataclass
class _Entry:
    ts: datetime
    feats: dict[str, float]


class ComponentWindow:
    """
    Rolling 7-day window of per-reading features for one component. Keeps at
    most ~1 entry per hour (plus the newest) so real-time streams at 3 s
    intervals don't blow up memory, while trends and 24 h medians stay valid.
    """

    def __init__(self) -> None:
        self.entries: deque[_Entry] = deque()

    def add(self, ts: datetime, feats: dict[str, float]) -> None:
        ts = as_utc(ts)
        if self.entries and ts <= self.entries[-1].ts:
            return  # out-of-order / duplicate reading
        if len(self.entries) >= 2 and self.entries[-1].ts - self.entries[-2].ts < WINDOW_SPACING:
            self.entries[-1] = _Entry(ts, feats)  # replace provisional newest entry
        else:
            self.entries.append(_Entry(ts, feats))
        cutoff = ts - timedelta(days=TREND_WINDOW_DAYS + 0.5)
        while self.entries and self.entries[0].ts < cutoff:
            self.entries.popleft()

    def features(self, fatigue_index: float) -> dict[str, float] | None:
        if not self.entries:
            return None
        t = [_days(e.ts) for e in self.entries]
        keys = self.entries[-1].feats.keys()
        per = {k: [e.feats.get(k, 0.0) for e in self.entries] for k in keys}
        return window_features(t, per, fatigue_index)


def build_reading_doc(r: SensorReading, feats: dict[str, float], keep_waveform: bool) -> dict:
    return {
        "component_id": r.component_id,
        "timestamp": as_utc(r.timestamp),
        "tension": round(r.tension, 4),
        "rope_tensions": [round(x, 4) for x in r.rope_tensions] if r.rope_tensions else None,
        "vibration_waveform": [round(x, 5) for x in r.vibration_waveform] if keep_waveform else None,
        "motor_current": round(r.motor_current, 3),
        "temperature": round(r.temperature, 3),
        "cycle_count": r.cycle_count,
        "mode": r.mode,
        "features": {k: round(v, 5) for k, v in feats.items()},
    }


class IngestionPipeline:
    def __init__(self, db, tariff: float) -> None:
        self.db = db
        self.tariff = tariff
        self.windows: dict[str, ComponentWindow] = {}
        self._last_saved_prediction: dict[str, datetime] = {}
        self._reading_counter: dict[str, int] = defaultdict(int)

    async def _window(self, component_id: str) -> ComponentWindow:
        w = self.windows.get(component_id)
        if w is None:
            w = ComponentWindow()
            latest = await self.db.sensor_readings.find_one(
                {"component_id": component_id}, {"timestamp": 1}, sort=[("timestamp", -1)])
            if latest:
                since = as_utc(latest["timestamp"]) - timedelta(days=TREND_WINDOW_DAYS + 0.5)
                cursor = self.db.sensor_readings.find(
                    {"component_id": component_id, "timestamp": {"$gte": since}},
                    {"timestamp": 1, "features": 1}).sort("timestamp", 1)
                async for doc in cursor:
                    w.add(doc["timestamp"], doc["features"])
            self.windows[component_id] = w
        return w

    def reset_window(self, component_id: str) -> None:
        self.windows.pop(component_id, None)

    async def ingest(self, readings: list[SensorReading], waveform_every: int = 1,
                     broadcast: bool = True) -> dict:
        """Process readings; returns a summary. Readings may span many components."""
        by_comp: dict[str, list[SensorReading]] = defaultdict(list)
        for r in readings:
            by_comp[r.component_id].append(r)

        comp_ids = list(by_comp)
        comps = {c["_id"]: c async for c in self.db.components.find({"_id": {"$in": comp_ids}})}
        unknown = [c for c in comp_ids if c not in comps]
        reading_docs, prediction_docs, events = [], [], []
        energy_deltas: dict[tuple[str, str], dict] = {}
        predictor = get_predictor()

        for cid, rs in by_comp.items():
            comp = comps.get(cid)
            if comp is None:
                continue
            ctype = comp["type"]
            rs.sort(key=lambda r: r.timestamp)
            window = await self._window(cid)
            fatigue = float(comp.get("fatigue_index", 0.0))
            last_cycles = comp.get("last_cycle_count")
            last_ts = as_utc(comp.get("last_reading_at"))

            for r in rs:
                if last_ts and as_utc(r.timestamp) <= last_ts:
                    continue  # already ingested
                feats = single_reading_features(ctype, r.model_dump())
                trips = max(0.0, r.cycle_count - last_cycles) if last_cycles is not None else 0.0
                # Palmgren-Miner fatigue from observed trips x observed load factor
                fatigue += float(P.miner_increment(ctype, trips, feats["load_factor"]))
                window.add(r.timestamp, feats)
                self._reading_counter[cid] += 1
                keep_wave = waveform_every <= 1 or self._reading_counter[cid] % waveform_every == 0
                reading_docs.append(build_reading_doc(r, feats, keep_wave))
                if ctype == "traction" and last_ts is not None:
                    # the traction machine's trip counter doubles as the elevator's trip meter
                    hours = (as_utc(r.timestamp) - last_ts).total_seconds() / 3600.0
                    day = as_utc(r.timestamp).strftime("%Y-%m-%d")
                    d = energy_deltas.setdefault((comp["elevator_id"], day), {"hours": 0.0, "trips": 0.0})
                    d["hours"] += min(hours, 24.0)
                    d["trips"] += trips
                last_cycles, last_ts = r.cycle_count, as_utc(r.timestamp)

            if last_ts is None or last_ts == as_utc(comp.get("last_reading_at")):
                continue
            wf = window.features(fatigue)
            pred = predictor.predict(ctype, wf)
            latest = rs[-1]
            update = {
                "health_score": pred["health_score"],
                "failure_probability": pred["failure_probability"],
                "rul_days": pred["rul_days"],
                "status": pred["status"],
                "physics_severity": pred["physics_severity"],
                "explanation": pred["explanation"],
                "contributing_factors": pred["contributing_factors"],
                "fatigue_index": fatigue,
                "last_cycle_count": last_cycles,
                "last_reading_at": last_ts,
                "model_version": pred["model_version"],
                "latest_features": wf,
            }
            await self.db.components.update_one({"_id": cid}, {"$set": update})

            prev_saved = self._last_saved_prediction.get(cid)
            if prev_saved is None or last_ts - prev_saved >= PREDICTION_SAVE_SPACING:
                prediction_docs.append({
                    "component_id": cid,
                    "elevator_id": comp["elevator_id"],
                    "timestamp": last_ts,
                    "model_version": pred["model_version"],
                    "health_score": pred["health_score"],
                    "failure_probability": pred["failure_probability"],
                    "RUL_days": pred["rul_days"],
                    "status": pred["status"],
                    "physics_severity": pred["physics_severity"],
                    "contributing_factors": [
                        {k: f[k] for k in ("feature", "label", "display_value", "severity")}
                        for f in pred["contributing_factors"]],
                })
                self._last_saved_prediction[cid] = last_ts

            old_status = comp.get("status", "good")
            # Alert on deterioration, with a 3-day de-duplication window so a
            # component hovering at a boundary doesn't spam notifications.
            recent = await self.db.alerts.find_one({
                "component_id": cid, "type": "status_change", "status": pred["status"],
                "timestamp": {"$gte": last_ts - timedelta(days=3)}})
            if STATUS_RANK[pred["status"]] > STATUS_RANK.get(old_status, 0) and not recent:
                alert = {
                    "type": "status_change",
                    "status": pred["status"],
                    "severity": "critical" if pred["status"] == "at-risk" else "warning",
                    "component_id": cid,
                    "elevator_id": comp["elevator_id"],
                    "timestamp": last_ts,
                    "message": f"{comp['name']} changed {old_status} -> {pred['status']}. {pred['explanation']}",
                    "acknowledged": False,
                }
                await self.db.alerts.insert_one(alert)
                events.append(("alert", clean(alert)))

            events.append(("component_update", {
                "component_id": cid, "elevator_id": comp["elevator_id"], "type": ctype,
                "timestamp": iso(last_ts), "mode": latest.mode,
                "health_score": pred["health_score"], "failure_probability": pred["failure_probability"],
                "rul_days": pred["rul_days"], "status": pred["status"],
                "features": {k: round(v, 5) for k, v in wf.items()},
                "reading": {
                    "tension": latest.tension, "motor_current": latest.motor_current,
                    "temperature": latest.temperature, "rope_tensions": latest.rope_tensions,
                    "vibration_waveform": latest.vibration_waveform,
                },
            }))

        if reading_docs:
            await self.db.sensor_readings.insert_many(reading_docs)
        if prediction_docs:
            await self.db.predictions.insert_many(prediction_docs)
        await self._apply_energy(energy_deltas)
        for elevator_id in {comps[c]["elevator_id"] for c in comp_ids if c in comps}:
            await refresh_elevator_status(self.db, elevator_id)

        if broadcast:
            for etype, data in events:
                await broadcaster.publish(etype, data)
        return {"ingested": len(reading_docs), "predictions": len(prediction_docs), "unknown_components": unknown}

    async def _apply_energy(self, deltas: dict[tuple[str, str], dict]) -> None:
        for (elevator_id, day), d in deltas.items():
            sev = {c["type"]: c.get("physics_severity", 0.0)
                   async for c in self.db.components.find({"elevator_id": elevator_id},
                                                          {"type": 1, "physics_severity": 1})}
            actual, baseline = E.interval_energy(d["hours"], d["trips"], sev)
            await upsert_energy(self.db, elevator_id, day, actual, baseline, d["trips"], self.tariff, d["hours"])


async def upsert_energy(db, elevator_id: str, day: str, kwh: float, baseline_kwh: float,
                        trips: float, tariff: float, hours: float) -> None:
    await db.energy_logs.update_one(
        {"_id": f"{elevator_id}:{day}"},
        {"$inc": {"energy_kwh": kwh, "baseline_kwh": baseline_kwh, "cost": kwh * tariff,
                  "trip_count": trips, "hours": hours},
         "$setOnInsert": {"elevator_id": elevator_id, "date": day,
                          "timestamp": datetime.fromisoformat(day + "T00:00:00+00:00")}},
        upsert=True,
    )
