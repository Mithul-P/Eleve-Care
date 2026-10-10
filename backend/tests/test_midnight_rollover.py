"""
Regression test for simulation clock / midnight rollover (Section 7).

Verifies that:
- Old day: batches 1..4 (00:00, 06:00, 12:00, 18:00)
- New day: date strictly increments and batch resets to batch 1
- Batch is never 5+ for any calendar date
"""

from datetime import datetime, timedelta, timezone
from app.services.runner import SimulatorRunner
from app.config import get_settings


def test_batch_number_and_midnight_rollover():
    settings = get_settings()
    # Mock runner with dummy components
    runner = SimulatorRunner(db=None, settings=settings, pipeline=None, source=None, hub=None)

    # 1. Day 1: 00:00 -> Batch 1
    t0 = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
    runner.sim_time = t0
    st = runner.status()
    assert st["batch_number"] == 1
    assert st["sim_date"] == "2026-09-24"

    # 2. Day 1: 06:00 -> Batch 2
    t1 = datetime(2026, 9, 24, 6, 0, tzinfo=timezone.utc)
    runner.sim_time = t1
    st = runner.status()
    assert st["batch_number"] == 2
    assert st["sim_date"] == "2026-09-24"

    # 3. Day 1: 12:00 -> Batch 3
    t2 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    runner.sim_time = t2
    st = runner.status()
    assert st["batch_number"] == 3
    assert st["sim_date"] == "2026-09-24"

    # 4. Day 1: 18:00 -> Batch 4
    t3 = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
    runner.sim_time = t3
    st = runner.status()
    assert st["batch_number"] == 4
    assert st["sim_date"] == "2026-09-24"

    # 5. Crossing midnight: 18:00 + 6h -> 2026-09-25 00:00
    t4 = t3 + timedelta(hours=settings.sim_batch_hours)
    runner.sim_time = t4
    st = runner.status()
    assert st["sim_date"] == "2026-09-25", "Date must strictly increment on midnight crossing"
    assert st["batch_number"] == 1, "Batch must reset to batch 1 on midnight crossing"
    assert st["batch_number"] <= 4, "Batch number must never exceed 4 (never batch 5+)"


def test_consecutive_batch_simulation_rollover():
    settings = get_settings()
    runner = SimulatorRunner(db=None, settings=settings, pipeline=None, source=None, hub=None)

    current = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
    expected_batches = [
        ("2026-09-24", 1),
        ("2026-09-24", 2),
        ("2026-09-24", 3),
        ("2026-09-24", 4),
        ("2026-09-25", 1), # Rollover!
        ("2026-09-25", 2),
        ("2026-09-25", 3),
        ("2026-09-25", 4),
        ("2026-09-26", 1), # Rollover!
    ]

    for expected_date, expected_batch in expected_batches:
        runner.sim_time = current
        st = runner.status()
        assert st["sim_date"] == expected_date
        assert st["batch_number"] == expected_batch
        assert 1 <= st["batch_number"] <= 4, f"Invalid batch {st['batch_number']} on {expected_date}"
        current += timedelta(hours=settings.sim_batch_hours)
