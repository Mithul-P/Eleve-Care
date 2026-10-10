"""
Regression test for Section 2 - the batch/simulation clock must never exceed
real time, no matter the speed multiplier or a manually triggered batch run.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.db import utcnow
from app.services.runner import SimulatorRunner
from app.config import get_settings


class _FakeSource:
    """Minimal stand-in for SimulatedSource: just returns empty readings."""

    units = {"dummy": object()}

    def generate(self, start, hours, step_minutes, mode):
        return []

    async def save(self):
        return None

    def advance_silently(self, hours):
        return None


class _FakePipeline:
    async def ingest(self, readings, broadcast=True, waveform_every=None):
        return {"ingested": len(readings), "predictions": 0}


def _runner():
    settings = get_settings()
    return SimulatorRunner(db=_FakeDb(), settings=settings, pipeline=_FakePipeline(), source=_FakeSource(), hub=None)


class _FakeDb:
    class _Coll:
        async def update_one(self, *a, **kw):
            return None

    def __getattr__(self, name):
        return self._Coll()


@pytest.mark.asyncio
async def test_manual_and_scheduled_batches_never_overshoot_real_time():
    runner = _runner()
    runner.sim_time = utcnow() - timedelta(hours=1)  # 1 h behind real time
    # Batch length (SIM_BATCH_HOURS) is normally 6h - far more than the 1h gap,
    # so the clamp must shorten the batch rather than overshoot.
    result = await runner.run_batch(broadcast=False)
    assert runner.sim_time <= utcnow()
    assert result is not None

    # Running several more batches back-to-back (as "run batch now" spammed by
    # an impatient user would) must never push the clock into the future.
    for _ in range(5):
        await runner.run_batch(broadcast=False)
        assert runner.sim_time <= utcnow()
    assert runner.status()["caught_up"] is True


@pytest.mark.asyncio
async def test_realtime_ticks_at_60x_never_overshoot_real_time():
    settings = get_settings()
    settings.sim_realtime_acceleration = 60.0
    runner = _runner()
    runner.sim_time = utcnow() - timedelta(seconds=5)
    # Simulate several minutes of wall-clock ticking at 60x acceleration and
    # assert the sim clock never exceeds real time at any sampled tick.
    for _ in range(50):
        await runner.tick()
        assert runner.sim_time <= utcnow(), "sim_time overshot real time at 60x acceleration"
        await asyncio.sleep(0)  # let the wall clock move a tiny bit between ticks
    # Once caught up, further ticks must keep reporting caught_up rather than
    # silently freezing with no feedback.
    st = runner.status()
    assert st["caught_up"] is True
