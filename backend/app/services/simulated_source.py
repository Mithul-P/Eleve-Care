"""
Simulated sensor source. Holds each component's hidden degradation state
(persisted in `simulator_state`) and emits SensorReadings via the physics
simulator in /ml. Replace with a real gateway source later - it only has to
produce `SensorReading` objects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np

from elevcare_ml import simulator as S

from ..db import as_utc
from .ingestion import SensorReading

log = logging.getLogger(__name__)


class SimulatedSource:
    name = "simulator"

    def __init__(self, db, seed: int | None = None) -> None:
        self.db = db
        self.rng = np.random.default_rng(seed)
        self.units: dict[str, tuple[S.Profile, S.State]] = {}

    async def load(self) -> None:
        self.units = {}
        async for doc in self.db.simulator_state.find({}):
            self.units[doc["_id"]] = S.from_doc(doc)
        log.info("Simulator loaded %d component states", len(self.units))

    async def save(self) -> None:
        for cid, (prof, state) in self.units.items():
            await self.db.simulator_state.update_one({"_id": cid}, {"$set": S.to_doc(prof, state)}, upsert=True)

    def _reading(self, cid: str, ts: datetime, mode: str) -> SensorReading:
        prof, state = self.units[cid]
        hour = ts.hour + ts.minute / 60.0
        obs = S.observe(prof, state, hour, self.rng)
        return SensorReading(
            component_id=cid,
            timestamp=ts,
            tension=float(obs["tension"][0]),
            rope_tensions=[float(x) for x in obs["rope_tensions"][0]] if prof.component_type == "rope" else None,
            vibration_waveform=[float(x) for x in obs["vibration_waveform"][0]],
            motor_current=float(obs["motor_current"][0]),
            temperature=float(obs["temperature"][0]),
            cycle_count=float(obs["cycle_count"][0]),
            mode=mode,
        )

    def generate(self, start: datetime, hours: float, step_minutes: float, mode: str) -> list[SensorReading]:
        """Advance every unit over (start, start+hours], one reading per step."""
        start = as_utc(start)
        steps = max(1, int(round(hours * 60 / step_minutes)))
        dt_h = hours / steps
        out: list[SensorReading] = []
        for i in range(1, steps + 1):
            ts = start + timedelta(hours=dt_h * i)
            for cid, (prof, state) in self.units.items():
                S.advance(prof, state, dt_h, self.rng)
                out.append(self._reading(cid, ts, mode))
        return out

    def advance_silently(self, hours: float) -> None:
        """Degrade units over a period with no readings (e.g. server downtime)."""
        step = 6.0
        remaining = hours
        while remaining > 1e-6:
            dt = min(step, remaining)
            for prof, state in self.units.values():
                S.advance(prof, state, dt, self.rng)
            remaining -= dt

    async def on_maintenance(self, component_id: str, kind: str) -> None:
        if kind != "replacement" or component_id not in self.units:
            return
        prof, state = self.units[component_id]
        S.apply_maintenance(prof, state, 0)
        await self.db.simulator_state.update_one(
            {"_id": component_id}, {"$set": S.to_doc(prof, state)}, upsert=True)
