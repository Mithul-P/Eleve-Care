"""
Simulator runner - drives the SimulatedSource in one of two modes:

* batch     : every SIM_BATCH_INTERVAL_SECONDS, generate a chunk covering
              SIM_BATCH_HOURS of simulated time (hourly readings) and ingest it
              in one go - like a gateway that uploads every 6 hours.
* real-time : every SIM_REALTIME_INTERVAL_SECONDS, one reading per component,
              streamed to dashboards over WebSocket.

A simulation clock (`simulator_config.sim_time`) timestamps readings. In
real-time mode it advances by interval x SIM_REALTIME_ACCELERATION, so demos
can compress time; with acceleration 1 it tracks the wall clock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from ..config import Settings
from ..db import as_utc, clean, iso, utcnow
from .broadcaster import broadcaster
from .ingestion import IngestionPipeline
from .simulated_source import SimulatedSource

log = logging.getLogger(__name__)

MAX_BACKFILL_HOURS = 48.0


class SimulatorRunner:
    def __init__(self, db, settings: Settings, pipeline: IngestionPipeline, source: SimulatedSource, hub=None):
        self.db = db
        self.hub = hub
        self.s = settings
        self.pipeline = pipeline
        self.source = source
        self.mode = settings.sim_mode
        self.sim_time = utcnow()
        self.running = False
        self.next_batch_at: float | None = None
        self.last_run: dict | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._last_save = time.monotonic()

    async def start(self) -> None:
        await self.source.load()
        cfg = await self.db.simulator_config.find_one({"_id": "config"}) or {}
        self.mode = cfg.get("mode", self.s.sim_mode)
        self.sim_time = as_utc(cfg.get("sim_time")) or utcnow()
        await self._catch_up()
        self.running = True
        self.next_batch_at = time.monotonic() + self.s.sim_batch_interval_seconds
        if self.mode == "real-time":
            try:
                await self.tick()
            except Exception:
                log.exception("Initial simulator tick failed")
        self._task = asyncio.create_task(self._loop(), name="simulator")
        log.info("Simulator started in %s mode (sim clock %s)", self.mode, iso(self.sim_time))

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.source.save()
        await self._save_config()

    async def _catch_up(self) -> None:
        """After downtime: backfill up to 48 h of readings, degrade silently beyond that."""
        gap_h = (utcnow() - self.sim_time).total_seconds() / 3600.0
        if gap_h <= self.s.sim_batch_hours or not self.source.units:
            return
        silent = max(0.0, gap_h - MAX_BACKFILL_HOURS)
        if silent > 0:
            self.source.advance_silently(silent)
            self.sim_time += timedelta(hours=silent)
            log.info("Simulator: %.0f h offline gap - units degraded without readings", silent)
        while (utcnow() - self.sim_time).total_seconds() / 3600.0 >= self.s.sim_batch_hours:
            await self.run_batch(broadcast=False)

    async def _mirror(self, readings) -> None:
        """Emit per-sensor events for the auto-registered sensors (Sensor inputs page) and flush stale buckets."""
        if self.hub is None or not readings:
            return
        comps = {c["_id"]: c async for c in self.db.components.find({}, {"elevator_id": 1, "type": 1})}
        await self.hub.observe_readings(readings[-len(comps) * 2:], comps)
        await self.hub.flush_stale()

    async def _save_config(self) -> None:
        await self.db.simulator_config.update_one(
            {"_id": "config"}, {"$set": {"mode": self.mode, "sim_time": self.sim_time}}, upsert=True)

    async def _loop(self) -> None:
        while self.running:
            try:
                if self.mode == "real-time":
                    await self.tick()
                    await self._sleep(self.s.sim_realtime_interval_seconds)
                else:
                    remaining = (self.next_batch_at or 0) - time.monotonic()
                    if remaining <= 0:
                        await self.run_batch()
                        self.next_batch_at = time.monotonic() + self.s.sim_batch_interval_seconds
                    else:
                        await self._sleep(min(remaining, 5.0))
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the simulator alive; log and back off
                log.exception("simulator iteration failed")
                await asyncio.sleep(5)

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run_batch(self, broadcast: bool = True) -> dict:
        async with self._lock:
            start = self.sim_time
            now = utcnow()
            # Never let a batch (scheduled or manually triggered) push the sim
            # clock past real time - clamp the batch length instead of the result.
            max_hours = max(0.0, (now - start).total_seconds() / 3600.0)
            hours = min(self.s.sim_batch_hours, max_hours)
            if hours <= 0:
                self.last_run = {"mode": "batch", "at": iso(now), "sim_from": iso(start),
                                 "sim_to": iso(start), "caught_up": True, "ingested": 0, "predictions": 0}
                if broadcast:
                    await broadcaster.publish("simulator", self.status())
                return self.last_run
            readings = self.source.generate(start, hours, 60, "batch")
            result = await self.pipeline.ingest(readings, broadcast=broadcast)
            await self._mirror(readings)
            self.sim_time = min(start + timedelta(hours=hours), now)
            await self.source.save()
            await self._save_config()
            batch_of_day = int(start.hour // self.s.sim_batch_hours) % 4 + 1
            self.last_run = {"mode": "batch", "at": iso(utcnow()), "sim_from": iso(start),
                             "sim_to": iso(self.sim_time), "sim_date": start.date().isoformat(),
                             "batch_number": batch_of_day, "batch_label": f"Batch {batch_of_day} of 4",
                             **result}
        if broadcast:
            await broadcaster.publish("simulator", self.status())
        return self.last_run

    async def tick(self) -> dict:
        async with self._lock:
            dt_s = self.s.sim_realtime_interval_seconds * self.s.sim_realtime_acceleration
            start = self.sim_time
            now = utcnow()
            # never let the real-time clock fall behind the wall clock
            if now - start > timedelta(seconds=dt_s * 3):
                start = now - timedelta(seconds=dt_s)
            # ...and never let acceleration push it past the wall clock either.
            max_s = max(0.0, (now - start).total_seconds())
            step_s = min(dt_s, max_s)
            caught_up = step_s <= 0
            if caught_up:
                self.last_run = {"mode": "real-time", "at": iso(now), "sim_to": iso(start), "caught_up": True, "ingested": 0}
                return self.last_run
            readings = self.source.generate(start, step_s / 3600.0, max(step_s / 60.0, 1e-6), "real-time")
            result = await self.pipeline.ingest(readings, waveform_every=self.s.sim_realtime_waveform_every)
            await self._mirror(readings)
            self.sim_time = min(start + timedelta(seconds=step_s), now)
            self.last_run = {"mode": "real-time", "at": iso(now), "sim_to": iso(self.sim_time), "caught_up": step_s < dt_s, **result}
            if time.monotonic() - self._last_save > 30:  # persist hidden state periodically
                await self.source.save()
                await self._save_config()
                self._last_save = time.monotonic()
        return self.last_run

    async def set_mode(self, mode: str) -> dict:
        if mode not in ("batch", "real-time"):
            raise ValueError("mode must be 'batch' or 'real-time'")
        async with self._lock:
            self.mode = mode
            await self._save_config()
            self.next_batch_at = time.monotonic() + self.s.sim_batch_interval_seconds
        self._wake.set()
        await broadcaster.publish("simulator", self.status())
        return self.status()

    def status(self) -> dict:
        nb = None
        if self.mode == "batch" and self.next_batch_at is not None:
            nb = max(0.0, self.next_batch_at - time.monotonic())
        sim_dt = self.sim_time
        batch_of_day = int(sim_dt.hour // self.s.sim_batch_hours) % 4 + 1
        # True once the sim clock has caught up to real time and cannot advance
        # further until real time itself moves on (Section 2: the ceiling).
        caught_up = (utcnow() - sim_dt).total_seconds() < 60
        return {
            "enabled": self.running,
            "mode": self.mode,
            "sim_time": iso(self.sim_time),
            "sim_date": sim_dt.date().isoformat(),
            "caught_up": caught_up,
            "batch_number": batch_of_day,
            "batch_label": f"Batch {batch_of_day} of 4",
            "batch_hours": self.s.sim_batch_hours,
            "batch_interval_seconds": self.s.sim_batch_interval_seconds,
            "next_batch_in_seconds": round(nb, 1) if nb is not None else None,
            "realtime_interval_seconds": self.s.sim_realtime_interval_seconds,
            "realtime_acceleration": self.s.sim_realtime_acceleration,
            "components": len(self.source.units) if (self.source and hasattr(self.source, "units")) else 0,
            "ws_clients": broadcaster.client_count,
            "last_run": clean(self.last_run),
        }
