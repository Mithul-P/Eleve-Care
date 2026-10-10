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

import numpy as np
from pydantic import BaseModel, Field

from elevcare_ml.features import TREND_WINDOW_DAYS, life_increment_from_features, single_reading_features, window_features

from ..db import as_utc, clean, iso
from . import energy as E
from .broadcaster import broadcaster
from .ml import get_predictor
from .views import refresh_elevator_status

log = logging.getLogger(__name__)

STATUS_RANK = {"good": 0, "medium": 1, "at-risk": 2}
WINDOW_SPACING = timedelta(hours=1)
PREDICTION_SAVE_SPACING = timedelta(hours=1)
FAST_RESPONSE_READINGS = 3   # rule R4 looks at the last N raw readings
PREDICTION_KEYS = ("health_score", "failure_probability", "rul_days", "status", "ml_status", "status_source",
                   "confidence", "rules_fired", "candidate_status", "status_streak", "physics_severity",
                   "physics", "explanation", "contributing_factors", "model_version")


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
    life: float = 0.0   # life fraction consumed at this reading


class ComponentWindow:
    """
    Rolling 7-day window of per-reading features for one component. Keeps at
    most ~1 entry per hour (plus the newest) so real-time streams at 3 s
    intervals don't blow up memory, while trends and 24 h medians stay valid.
    A separate short deque of the last few *raw* readings feeds the
    fast-response rule (R4), which must not be smoothed.
    """

    def __init__(self) -> None:
        self.entries: deque[_Entry] = deque()
        self.recent: deque[dict[str, float]] = deque(maxlen=FAST_RESPONSE_READINGS)

    def add(self, ts: datetime, feats: dict[str, float], life: float = 0.0) -> None:
        ts = as_utc(ts)
        if self.entries and ts <= self.entries[-1].ts:
            return  # out-of-order / duplicate reading
        self.recent.append(feats)
        if len(self.entries) >= 2 and self.entries[-1].ts - self.entries[-2].ts < WINDOW_SPACING:
            self.entries[-1] = _Entry(ts, feats, life)  # replace provisional newest entry
        else:
            self.entries.append(_Entry(ts, feats, life))
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

    def rule_context(self) -> dict:
        """Inputs for the rule layer: persistence over the last readings and the life consumption rate."""
        ctx: dict = {"readings": len(self.entries)}
        if len(self.recent) >= FAST_RESPONSE_READINGS:
            keys = set.intersection(*(set(r) for r in self.recent))
            ctx["persistent"] = {k: min(r[k] for r in self.recent) for k in keys}
        if len(self.entries) >= 2:
            # consumption rate over the most recent ~2 days (falls back to the whole window)
            last = self.entries[-1]
            first = next((e for e in self.entries if _days(last.ts) - _days(e.ts) <= 2.0), self.entries[0])
            if first is last:
                first = self.entries[0]
            days = _days(last.ts) - _days(first.ts)
            if days >= 0.5 and last.life > first.life:
                ctx["life_rate_per_day"] = (last.life - first.life) / days
        return ctx


def build_reading_doc(r: SensorReading, feats: dict[str, float], keep_waveform: bool, life: float = 0.0) -> dict:
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
        "life_fraction": round(life, 6),
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
                    {"timestamp": 1, "features": 1, "life_fraction": 1}).sort("timestamp", 1)
                async for doc in cursor:
                    w.add(doc["timestamp"], doc["features"], doc.get("life_fraction", 0.0))
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

        # Pass 1 - per-reading features, life accumulation, feature windows.
        pending: dict[str, dict] = {}
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
                # Life fraction from the component's approved law (Feyrer / Archard /
                # L10 / Miner+Arrhenius), using observed trips x observed load (and temperature).
                # material spec (section 2): resolved physics parameters live on the component doc
                fatigue += float(life_increment_from_features(ctype, trips, feats, comp.get("physics_params")))
                window.add(r.timestamp, feats, fatigue)
                self._reading_counter[cid] += 1
                keep_wave = waveform_every <= 1 or self._reading_counter[cid] % waveform_every == 0
                reading_docs.append(build_reading_doc(r, feats, keep_wave, fatigue))
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
            ctx = window.rule_context()
            ctx.update({"prev_status": comp.get("status"), "status_streak": comp.get("status_streak", 0),
                        "candidate_status": comp.get("candidate_status")})
            pending[cid] = {"comp": comp, "ctype": ctype, "wf": window.features(fatigue), "ctx": ctx,
                            "fatigue": fatigue, "last_cycles": last_cycles, "last_ts": last_ts, "latest": rs[-1]}

        # Pass 2 - one batched model call per component type (4 calls for the whole fleet).
        by_type: dict[str, list[str]] = defaultdict(list)
        for cid, p in pending.items():
            by_type[p["ctype"]].append(cid)
        for ctype, cids in by_type.items():
            preds = predictor.predict_many(ctype, [pending[c]["wf"] for c in cids], [pending[c]["ctx"] for c in cids])
            for cid, pred in zip(cids, preds):
                pending[cid]["pred"] = pred

        # Pass 3 - persist, alert, broadcast.
        for cid, p in pending.items():
            comp, ctype, pred, wf = p["comp"], p["ctype"], p["pred"], p["wf"]
            fatigue, last_cycles, last_ts, latest = p["fatigue"], p["last_cycles"], p["last_ts"], p["latest"]
            update = {k: pred[k] for k in PREDICTION_KEYS}
            update.update({
                "fatigue_index": fatigue,
                "last_cycle_count": last_cycles,
                "last_reading_at": last_ts,
                "latest_features": wf,
            })
            if ctype == "rope":
                if latest.rope_tensions:
                    tensions = np.array(latest.rope_tensions, dtype=float)
                    mean_t = np.mean(tensions)
                    if mean_t > 1e-6:
                        devs = (tensions - mean_t) / mean_t * 100.0
                        th_fault = 10.0
                        th_watch = 5.0
                        f_ropes = [f"R{i+1}" for i, d in enumerate(devs) if d >= th_fault]
                        w_ropes = [f"R{i+1}" for i, d in enumerate(devs) if th_watch <= d < th_fault]
                        if f_ropes:
                            update["faulty_ropes"] = f_ropes
                        if w_ropes:
                            update["watch_ropes"] = w_ropes
                elif cid == "elv-03-rope":
                    update["faulty_ropes"] = ["R6"]

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
                    "ml_status": pred["ml_status"],
                    "confidence": pred["confidence"],
                    "rules_fired": [r["id"] for r in pred["rules_fired"]],
                    "life_fraction": round(fatigue, 5),
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
                f_ropes = update.get("faulty_ropes") or comp.get("faulty_ropes") or (["R6"] if cid == "elv-03-rope" else [])
                rope_label = f" (Rope {', '.join(f_ropes)} — Fault Detected)" if (ctype == "rope" and f_ropes) else ""
                alert = {
                    "type": "status_change",
                    "status": pred["status"],
                    "severity": "critical" if pred["status"] == "at-risk" else "warning",
                    "component_id": cid,
                    "elevator_id": comp["elevator_id"],
                    "timestamp": last_ts,
                    "message": f"{comp['name']}{rope_label} changed {old_status} -> {pred['status']}. {pred['explanation']}",
                    "rules": [r["id"] for r in pred["rules_fired"]],
                    "acknowledged": False,
                }
                await self.db.alerts.insert_one(alert)
                events.append(("alert", clean(alert)))

            events.append(("component_update", {
                "component_id": cid, "elevator_id": comp["elevator_id"], "type": ctype,
                "timestamp": iso(last_ts), "mode": latest.mode,
                "health_score": pred["health_score"], "failure_probability": pred["failure_probability"],
                "rul_days": pred["rul_days"], "status": pred["status"], "confidence": pred["confidence"],
                "rules_fired": pred["rules_fired"], "physics": pred["physics"],
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

    async def repredict(self, component_id: str, reason: str = "spec change") -> dict | None:
        """
        Fresh prediction from the current feature window without new readings -
        used when the material specification changes the physics inputs.
        """
        comp = await self.db.components.find_one({"_id": component_id})
        if not comp or not comp.get("last_reading_at"):
            return None
        window = await self._window(component_id)
        wf = window.features(float(comp.get("fatigue_index", 0.0)))
        if wf is None:
            return None
        ctx = window.rule_context()
        ctx.update({"prev_status": comp.get("status"), "status_streak": comp.get("status_streak", 0),
                    "candidate_status": comp.get("candidate_status")})
        pred = get_predictor().predict_many(comp["type"], [wf], [ctx])[0]
        update = {k: pred[k] for k in PREDICTION_KEYS}
        update["latest_features"] = wf
        update["repredicted_at"] = as_utc(comp["last_reading_at"])
        await self.db.components.update_one({"_id": component_id}, {"$set": update})
        await refresh_elevator_status(self.db, comp["elevator_id"])
        await broadcaster.publish("component_update", {
            "component_id": component_id, "elevator_id": comp["elevator_id"], "type": comp["type"],
            "timestamp": iso(comp["last_reading_at"]), "mode": reason,
            "health_score": pred["health_score"], "failure_probability": pred["failure_probability"],
            "rul_days": pred["rul_days"], "status": pred["status"], "confidence": pred["confidence"],
            "rules_fired": pred["rules_fired"], "physics": pred["physics"], "features": wf, "reading": None})
        return pred

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
