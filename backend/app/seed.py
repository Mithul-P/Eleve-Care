"""
Seed script: demo fleet, users, historical sensor data, predictions, energy logs.

Run automatically on first start (SEED_ON_STARTUP=true, empty DB) or manually:
    python -m app.seed [--reset]

History is produced by the *same* simulator and ingestion pipeline used live,
so seeded charts, predictions and energy logs are fully consistent.

The fleet includes the "hardcore" demo elevator (elv-03): its rope set has an
abnormal and steadily widening tension imbalance. Over the seeded history you
can watch it go good -> medium -> at-risk with an explanation of why.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
from datetime import timedelta

import numpy as np

from elevcare_ml import materials as MATERIALS
from elevcare_ml import simulator as S

from .config import Settings, get_settings
from .db import connect, disconnect, utcnow
from .security import hash_password
from .services.ingestion import IngestionPipeline
from .services.sensor_hub import default_sensors
from .services.simulated_source import SimulatedSource

log = logging.getLogger(__name__)

COMPONENT_NAMES = {
    "rope": "Suspension ropes (6 x 10 mm)",
    "sheave": "Traction sheave",
    "bearing": "Sheave bearing",
    "traction": "Traction machine",
}

# Realistic default material specification per component, per elevator, so the
# fleet is never seeded at 0% material completeness (spec section 2/8 - the
# fleet-wide Materials page always has something real to list). Diversity
# across elevators keeps that list from showing the same material six times.
ROPE_PRESET_BY_ELEVATOR = {
    "elv-01": "8x19S-IWRC", "elv-02": "6x19S-FC", "elv-03": "8x19S-FC",
    "elv-04": "8x19S-IWRC", "elv-05": "PU-belt", "elv-06": "6x19S-FC",
}
SHEAVE_PRESET_BY_ELEVATOR = {
    "elv-01": "GJS-500-7", "elv-02": "GJL-250", "elv-03": "GJS-600-3",
    "elv-04": "C45", "elv-05": "GJS-500-7", "elv-06": "GJL-250",
}
BEARING_PRESET_BY_ELEVATOR = {
    "elv-01": "SRB", "elv-02": "DGBB", "elv-03": "SRB",
    "elv-04": "TRB", "elv-05": "DGBB", "elv-06": "SRB",
}
TRACTION_PARTS_GEARLESS = ["laminations", "windings", "magnets", "shaft"]
TRACTION_PARTS_GEARED = ["laminations", "windings", "worm", "shaft-c45"]


def default_material_spec(ctype: str, elevator_id: str, drive_type: str) -> dict:
    """The material spec a new component of this type/elevator ships with."""
    if ctype == "traction":
        parts = TRACTION_PARTS_GEARED if drive_type == "Geared" else TRACTION_PARTS_GEARLESS
        return {"rows": [MATERIALS.apply_preset("traction", p) for p in parts]}
    preset_by_elevator = {"rope": ROPE_PRESET_BY_ELEVATOR, "sheave": SHEAVE_PRESET_BY_ELEVATOR,
                          "bearing": BEARING_PRESET_BY_ELEVATOR}.get(ctype, {})
    preset_id = preset_by_elevator.get(elevator_id)
    return MATERIALS.apply_preset(ctype, preset_id) if preset_id else {}

# damage = hidden wear level (0 new .. 1 end of life) at the start of the history window.
# rope_imbalance_now / rope_growth: rope tension spread (%) reached at the end of the history window
# and how fast it widens (%/day) - the history starts from the correspondingly smaller spread.
FLEET = [
    {"_id": "elv-01", "name": "Tower A - Lift 1", "building": "Prestige Tech Park, Tower A",
     "location": "Bengaluru, Karnataka", "floors": 14, "capacity_kg": 1088, "speed_mps": 1.75,
     "trips_per_day": 620, "is_demo": False,
     "components": {"rope": {"damage": 0.15}, "sheave": {"damage": 0.2},
                    "bearing": {"damage": 0.18}, "traction": {"damage": 0.22}}},
    {"_id": "elv-02", "name": "Tower A - Lift 2", "building": "Prestige Tech Park, Tower A",
     "location": "Bengaluru, Karnataka", "floors": 14, "capacity_kg": 1088, "speed_mps": 1.75,
     "trips_per_day": 580, "is_demo": False,
     "components": {"rope": {"damage": 0.25}, "sheave": {"damage": 0.3},
                    "bearing": {"damage": 0.8}, "traction": {"damage": 0.3}}},
    {"_id": "elv-03", "name": "Tower A - Lift 3 (Demo)", "building": "Prestige Tech Park, Tower A",
     "location": "Bengaluru, Karnataka", "floors": 14, "capacity_kg": 1088, "speed_mps": 1.75,
     "trips_per_day": 700, "is_demo": True,
     "demo_note": "Rope set with abnormal, widening tension imbalance - watch it go good -> at-risk.",
     "components": {"rope": {"damage": 0.5, "rope_imbalance_now": 19.0, "rope_growth": 0.24},
                    "sheave": {"damage": 0.28}, "bearing": {"damage": 0.2}, "traction": {"damage": 0.25}}},
    {"_id": "elv-04", "name": "Lobby Lift", "building": "Skyline Residency",
     "location": "Chennai, Tamil Nadu", "floors": 22, "capacity_kg": 884, "speed_mps": 2.5,
     "trips_per_day": 520, "is_demo": False,
     "components": {"rope": {"damage": 0.3}, "sheave": {"damage": 0.35},
                    "bearing": {"damage": 0.3}, "traction": {"damage": 0.78}}},
    {"_id": "elv-05", "name": "Service Lift", "building": "Skyline Residency",
     "location": "Chennai, Tamil Nadu", "floors": 22, "capacity_kg": 1360, "speed_mps": 1.0,
     "trips_per_day": 380, "is_demo": False,
     "components": {"rope": {"damage": 0.2}, "sheave": {"damage": 0.76},
                    "bearing": {"damage": 0.25}, "traction": {"damage": 0.3}}},
    {"_id": "elv-06", "name": "Atrium Lift", "building": "Metro Mall",
     "location": "Hyderabad, Telangana", "floors": 5, "capacity_kg": 1275, "speed_mps": 1.0,
     "trips_per_day": 450, "is_demo": False,
     "components": {"rope": {"damage": 0.05}, "sheave": {"damage": 0.06},
                    "bearing": {"damage": 0.05}, "traction": {"damage": 0.06}}},
]

SAMPLE_COMPLAINTS = [
    ("elv-03", 3, "Lift jerks and sways when it starts moving from the ground floor."),
    ("elv-03", 1, "Rattling / slapping noise in the shaft between floors 8 and 10."),
    ("elv-02", 5, "Humming noise from the machine room when the lift is running."),
]


async def seed_users(db, s: Settings) -> None:
    for email, pw, role, name in [
        (s.seed_admin_email, s.seed_admin_password, "admin", "Admin"),
        (s.seed_staff_email, s.seed_staff_password, "staff", "Maintenance Staff"),
    ]:
        if await db.users.find_one({"email": email}):
            continue
        if not pw:
            pw = secrets.token_urlsafe(10)
            log.warning("No password configured for %s (%s) - generated one-time password: %s", email, role, pw)
        await db.users.insert_one({"_id": f"usr-{role}", "email": email, "name": name, "role": role,
                                   "password_hash": hash_password(pw), "active": True,
                                   "created_at": utcnow()})


async def seed(db, s: Settings | None = None, history_days: int | None = None, reset: bool = False) -> bool:
    """Populate the database. Returns False if data already exists (and not reset)."""
    s = s or get_settings()
    history_days = history_days or s.seed_history_days
    if reset:
        for name in ["elevators", "components", "sensor_readings", "predictions", "complaints", "sensors",
                     "energy_logs", "alerts", "simulator_state", "simulator_config", "maintenance_logs"]:
            await db[name].delete_many({})
    await seed_users(db, s)
    if await db.elevators.count_documents({}) > 0:
        return False

    rng = np.random.default_rng(s.seed_random_seed)
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    hist_start = now - timedelta(days=history_days)
    source = SimulatedSource(db, seed=s.seed_random_seed + 1)

    for e in FLEET:
        elevator = {k: v for k, v in e.items() if k != "components"}
        elevator["lift_type"] = "traction"                      # fixed: traction lifts only
        elevator.setdefault("drive_type", "Geared" if e["_id"] == "elv-05" else "Gearless PM")
        elevator["sensor_source"] = "simulated"
        elevator["status"] = "good"
        elevator["created_at"] = now
        await db.elevators.insert_one(elevator)
        for ctype, spec in e["components"].items():
            cid = f"{e['_id']}-{ctype}"
            prof = S.random_profiles(ctype, 1, rng, trips_per_day=e["trips_per_day"],
                                     imbalance_growth_pct_per_day=0.01 if ctype == "rope" else 0.0)
            state = S.initial_state(prof, rng, damage=0.0, imbalance_pct=1.5)
            # Fast-forward (no readings) from installation until the target wear level:
            # the number of simulated days gives a physically consistent install date.
            days = 0
            while state.damage[0] < spec["damage"] and days < 6000:
                S.advance(prof, state, 24.0, rng)
                days += 1
            if "rope_growth" in spec:
                # demo lift: make the LAST rope (R6) the most-loaded one so the report names it
                n_ropes = prof.rope_pattern.shape[1]
                prof.rope_pattern[0] = np.array([-1.0 / (n_ropes - 1)] * (n_ropes - 1) + [1.0])
                prof.imbalance_growth_pct_per_day[:] = spec["rope_growth"]
                start = spec["rope_imbalance_now"] - spec["rope_growth"] * history_days
                state.imbalance_pct[:] = max(2.0, start)
                # short histories: jump the wear forward so the scenario still ends at-risk
                state.damage[:] = max(state.damage[0], spec["damage"] + max(0.0, 0.3 - 0.005 * history_days))
            source.units[cid] = (prof, state)
            mat_spec = default_material_spec(ctype, e["_id"], elevator["drive_type"])
            mat_params = MATERIALS.resolve(ctype, mat_spec)["params"] if mat_spec else {}
            await db.components.insert_one({
                "_id": cid,
                "elevator_id": e["_id"],
                "type": ctype,
                "name": COMPONENT_NAMES[ctype],
                "install_date": hist_start - timedelta(days=days),
                # last technician visit: 10-60 days ago (demo lift: 40 days ago)
                "last_inspection": now - timedelta(days=40 if e["is_demo"] else int(rng.integers(10, 60))),
                "health_score": 100.0, "failure_probability": 0.0, "rul_days": None, "status": "good",
                "physics_severity": 0.0,
                "fatigue_index": float(state.fatigue_index[0]),
                "last_cycle_count": float(np.floor(state.cycle_count[0])),
                "last_reading_at": None,
                "sensor_source": "simulator",
                "material_spec": mat_spec,
                "physics_params": mat_params,
                "material_spec_updated_at": now,
                "material_spec_updated_by": "seed",
            })

    # Replay the history through the live ingestion pipeline in 12 h chunks.
    pipeline = IngestionPipeline(db, s.energy_tariff_inr_per_kwh)
    chunk_h = 12.0
    t = hist_start
    while t < now:
        readings = source.generate(t, chunk_h, 360, "batch")
        await pipeline.ingest(readings, broadcast=False)
        t += timedelta(hours=chunk_h)
    await source.save()

    # Elevator roll-up status + auto-registered sensor set (spec section 4)
    from .services.views import refresh_elevator_status
    for e in FLEET:
        await refresh_elevator_status(db, e["_id"])
        comps = [c async for c in db.components.find({"elevator_id": e["_id"]})]
        for d in default_sensors(e, comps):
            d["_id"] = f"{e['_id']}:{d['sensor_id']}"
            await db.sensors.update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)

    for elevator_id, days_ago, text in SAMPLE_COMPLAINTS:
        await db.complaints.insert_one({
            "_id": f"cmp-{secrets.token_hex(6)}",
            "elevator_id": elevator_id, "timestamp": now - timedelta(days=days_ago, hours=3),
            "description": text, "status": "open", "submitted_without_login": True,
            "contact": None, "category": "noise/vibration"})

    # Seed initial service history into maintenance_logs (Section 11 & 12)
    sample_logs = [
        {"_id": "mnt-seed-1", "elevator_id": "elv-03", "component_id": "elv-03-rope", "component_name": "Suspension ropes",
         "kind": "Inspection", "notes": "Rope tension check: detected early tension imbalance on rope R6.",
         "timestamp": now - timedelta(days=40), "by": "K. Sharma"},
        {"_id": "mnt-seed-2", "elevator_id": "elv-03", "component_id": "elv-03-sheave", "component_name": "Traction sheave",
         "kind": "Lubrication", "notes": "Cleaned groove profiles and lubricated drive bearing housing.",
         "timestamp": now - timedelta(days=120), "by": "M. Patel"},
        {"_id": "mnt-seed-3", "elevator_id": "elv-03", "component_id": "elv-03-bearing", "component_name": "Sheave bearing",
         "kind": "Adjustment", "notes": "Vibration baseline check and alignment audit.",
         "timestamp": now - timedelta(days=210), "by": "K. Sharma"},
        {"_id": "mnt-seed-4", "elevator_id": "elv-01", "component_id": "elv-01-traction", "component_name": "Traction machine",
         "kind": "Inspection", "notes": "Motor insulation test passed (450 MOhm).",
         "timestamp": now - timedelta(days=60), "by": "A. Kumar"},
        {"_id": "mnt-seed-5", "elevator_id": "elv-01", "component_id": "elv-01-bearing", "component_name": "Sheave bearing",
         "kind": "Lubrication", "notes": "Regreased main shaft bearing assembly.",
         "timestamp": now - timedelta(days=180), "by": "A. Kumar"},
        {"_id": "mnt-seed-6", "elevator_id": "elv-02", "component_id": "elv-02-bearing", "component_name": "Sheave bearing",
         "kind": "Inspection", "notes": "Bearing high vibration alert: raceway acoustic emission check.",
         "timestamp": now - timedelta(days=15), "by": "R. Nair"},
        {"_id": "mnt-seed-7", "elevator_id": "elv-02", "component_id": "elv-02-rope", "component_name": "Suspension ropes",
         "kind": "Adjustment", "notes": "Equalised rope tensions across 6 suspension ropes.",
         "timestamp": now - timedelta(days=90), "by": "R. Nair"},
    ]
    for mlog in sample_logs:
        await db.maintenance_logs.update_one({"_id": mlog["_id"]}, {"$set": mlog}, upsert=True)

    await db.simulator_config.update_one(
        {"_id": "config"}, {"$set": {"mode": s.sim_mode, "sim_time": now}}, upsert=True)
    log.info("Seeded %d elevators with %d days of history", len(FLEET), history_days)
    return True


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe demo data first")
    ap.add_argument("--days", type=int, default=None)
    args = ap.parse_args()
    s = get_settings()
    from .services.ml import load_predictor
    load_predictor(s.ml_artifact_path)
    db = await connect(s.mongo_url, s.mongo_db)
    try:
        done = await seed(db, s, args.days, reset=args.reset)
        print("seeded" if done else "database already contains data (use --reset)")
    finally:
        await disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
