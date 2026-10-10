"""
Sensor input streaming (spec section 4).

* Registry (Mongo `sensors`): one document per physical sensor, mapped to a
  component and a parameter of the reading contract.
* SensorHub (in memory): last value / last seen / message rate per sensor, a
  short ring buffer per sensor for live charts, and per-component *buckets*
  that collect individual sensor values until a complete `SensorReading` can be
  assembled and handed to the normal IngestionPipeline. Everything downstream
  (features, ML, rules, alerts, energy) is unchanged.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from elevcare_ml import physics as P

from ..db import as_utc, clean, utcnow
from .broadcaster import broadcaster
from .ingestion import SensorReading

log = logging.getLogger(__name__)

SENSOR_TYPES = {
    "tension_load_cell": {"label": "Tension load cell", "unit": "kN", "params": ["tension", "rope_tension"]},
    "accelerometer": {"label": "Accelerometer (vibration)", "unit": "g", "params": ["vibration_waveform"]},
    "current_transducer": {"label": "Motor current transducer", "unit": "A", "params": ["motor_current"]},
    "temperature": {"label": "Temperature (RTD / thermocouple)", "unit": "°C", "params": ["temperature"]},
    "speed_encoder": {"label": "Speed encoder", "unit": "m/s", "params": ["speed"]},
    "trip_counter": {"label": "Trip counter", "unit": "trips", "params": ["cycle_count"]},
}
PARAMETERS = ["tension", "rope_tension", "vibration_waveform", "motor_current", "temperature", "cycle_count", "speed"]

# Coverage required per component type: (parameter, how many) - rope_tension needs one per rope.
COVERAGE = {
    "rope": [("rope_tension", "per_rope"), ("vibration_waveform", 1)],
    "sheave": [("vibration_waveform", 1), ("motor_current", 1)],
    "bearing": [("vibration_waveform", 1), ("temperature", 1)],
    "traction": [("motor_current", 1), ("temperature", 1), ("vibration_waveform", 1), ("cycle_count", 1)],
}
BUCKET_SECONDS = 10.0        # values within this window belong to one reading
BUCKET_FLUSH_SECONDS = 30.0  # an incomplete bucket older than this is completed from last-known values
RING = 120                   # points kept per sensor for the live chart


def sensor_doc_id(elevator_id: str, sensor_id: str) -> str:
    return f"{elevator_id}:{sensor_id}"


def coverage(components: list[dict], sensors: list[dict], rope_count: int) -> list[dict]:
    """Checklist per component: required sensors vs registered ones."""
    out = []
    for c in components:
        mine = [s for s in sensors if s["component_id"] == c["_id"]]
        items, missing = [], []
        for param, need in COVERAGE.get(c["type"], []):
            have = [s for s in mine if s["parameter"] == param]
            if need == "per_rope":
                ropes_have = sorted({s.get("rope_number") for s in have if s.get("rope_number")})
                n_need = rope_count
                ok = len(ropes_have) >= n_need
                label = f"one tension load cell per rope (R1..R{n_need})"
                detail = f"{len(ropes_have)}/{n_need} ropes covered"
                if not ok:
                    missing.append(f"tension sensors for ropes " + ", ".join(f"R{i}" for i in range(1, n_need + 1) if i not in ropes_have))
            else:
                ok = len(have) >= need
                label = {"vibration_waveform": "accelerometer", "motor_current": "motor current transducer",
                         "temperature": "temperature sensor", "cycle_count": "trip counter"}.get(param, param)
                detail = f"{len(have)}/{need}"
                if not ok:
                    missing.append(label)
            items.append({"parameter": param, "label": label, "ok": ok, "detail": detail})
        out.append({"component_id": c["_id"], "component_name": c["name"], "type": c["type"], "items": items,
                    "missing": missing, "complete": not missing})
    return out


class _Bucket:
    def __init__(self, start: float) -> None:
        self.start = start
        self.values: dict[str, float] = {}
        self.ropes: dict[int, float] = {}
        self.waveform: list[float] | None = None
        self.ts: datetime | None = None


class SensorHub:
    def __init__(self, db, pipeline) -> None:
        self.db = db
        self.pipeline = pipeline
        self.registry: dict[str, dict] = {}                     # doc_id -> sensor doc
        self.state: dict[str, dict] = {}                        # doc_id -> live state
        self.ring: dict[str, deque] = defaultdict(lambda: deque(maxlen=RING))
        self.waves: dict[str, list[float]] = {}                 # last waveform per accelerometer
        self.buckets: dict[str, _Bucket] = {}                   # component_id -> open bucket
        self.last_known: dict[str, dict] = defaultdict(dict)    # component_id -> {param: value}
        self.unregistered: deque = deque(maxlen=50)
        self.source: dict[str, str] = {}                        # elevator_id -> simulated|external
        self._last_broadcast: dict[str, float] = {}             # elevator_id -> last broadcast monotonic time

    # ---------------------------------------------------------------- registry
    async def load(self) -> None:
        self.registry = {d["_id"]: d async for d in self.db.sensors.find({})}
        async for e in self.db.elevators.find({}, {"sensor_source": 1}):
            self.source[e["_id"]] = e.get("sensor_source", "simulated")
        for did, d in self.registry.items():
            st = self.state.setdefault(did, {"msg_count": 0, "rate_per_min": 0.0, "last_seen": None, "last_value": None, "_times": deque(maxlen=60)})
            st["last_seen"] = as_utc(d.get("last_seen"))
            st["last_value"] = d.get("last_value")

    def sensors_of(self, elevator_id: str) -> list[dict]:
        return sorted((d for d in self.registry.values() if d["elevator_id"] == elevator_id), key=lambda d: d["sensor_id"])

    async def upsert(self, doc: dict) -> dict:
        doc["_id"] = sensor_doc_id(doc["elevator_id"], doc["sensor_id"])
        await self.db.sensors.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
        self.registry[doc["_id"]] = doc
        self.state.setdefault(doc["_id"], {"msg_count": 0, "rate_per_min": 0.0, "last_seen": None, "last_value": None, "_times": deque(maxlen=60)})
        return doc

    async def delete(self, elevator_id: str, sensor_id: str) -> bool:
        did = sensor_doc_id(elevator_id, sensor_id)
        r = await self.db.sensors.delete_one({"_id": did})
        self.registry.pop(did, None); self.state.pop(did, None); self.ring.pop(did, None)
        return r.deleted_count > 0

    async def set_source(self, elevator_id: str, source: str) -> None:
        self.source[elevator_id] = source
        await self.db.elevators.update_one({"_id": elevator_id}, {"$set": {"sensor_source": source}})

    # ---------------------------------------------------------------- live state
    def _touch(self, did: str, ts: datetime, value, waveform: list[float] | None = None, seen: datetime | None = None) -> None:
        """`ts` = reading timestamp (chart); `seen` = arrival time (online/stale/offline), defaults to ts."""
        st = self.state.setdefault(did, {"msg_count": 0, "rate_per_min": 0.0, "last_seen": None, "last_value": None, "_times": deque(maxlen=60)})
        now = time.monotonic()
        st["msg_count"] += 1
        st["_times"].append(now)
        window = [t for t in st["_times"] if now - t <= 60.0]
        st["rate_per_min"] = float(len(window))
        st["last_seen"] = seen or ts
        st["last_value"] = value
        if waveform is not None:
            self.waves[did] = waveform
        if value is not None:
            self.ring[did].append({"t": ts, "v": value})

    def status_of(self, did: str) -> dict:
        d = self.registry[did]
        st = self.state.get(did, {})
        seen = st.get("last_seen")
        expected = float(d.get("interval_s") or 60.0)
        now = time.monotonic()
        times = st.get("_times")
        if times is not None:
            window = [t for t in times if now - t <= 60.0]
            rate = float(len(window))
            st["rate_per_min"] = rate
        else:
            rate = 0.0
        if seen is None:
            status = "offline"
        else:
            age = (utcnow() - as_utc(seen)).total_seconds()
            status = "online" if age <= 2 * expected + 5 else "stale" if age <= 10 * expected + 60 else "offline"
        return {**clean(d), "status": status, "last_seen": clean(seen), "last_value": st.get("last_value"),
                "msg_count": st.get("msg_count", 0), "rate_per_min": rate}

    def series(self, did: str) -> dict:
        return {"points": [{"t": clean(p["t"]), "v": p["v"]} for p in self.ring.get(did, [])],
                "waveform": self.waves.get(did)}

    async def _maybe_broadcast(self, elevator_id: str) -> None:
        now = time.monotonic()
        if now - self._last_broadcast.get(elevator_id, 0.0) >= 1.0:
            self._last_broadcast[elevator_id] = now
            await broadcaster.publish("sensor_update", {"elevator_id": elevator_id, "at": clean(utcnow())})

    # ---------------------------------------------------------------- simulated feed
    async def observe_readings(self, readings: list[SensorReading], components: dict[str, dict]) -> None:
        """Mirror simulator readings onto the registered (auto-registered) sensors so the live page is populated."""
        by_comp: dict[str, dict] = {}
        for did, d in self.registry.items():
            by_comp.setdefault(d["component_id"], []).append(d)
        touched = set()
        now = utcnow()   # simulated messages "arrive" now even when the sim clock timestamps them earlier (batch mode)
        for r in readings:
            comp = components.get(r.component_id)
            if not comp or self.source.get(comp["elevator_id"], "simulated") != "simulated":
                continue
            for d in by_comp.get(r.component_id, []):
                p = d["parameter"]
                if p == "tension":
                    v = r.tension
                elif p == "rope_tension":
                    i = int(d.get("rope_number") or 1) - 1
                    v = r.rope_tensions[i] if r.rope_tensions and i < len(r.rope_tensions) else None
                elif p == "vibration_waveform":
                    v = float(max(abs(x) for x in r.vibration_waveform))
                    self._touch(d["_id"], r.timestamp, round(v, 4), r.vibration_waveform, seen=now); continue
                elif p == "motor_current":
                    v = r.motor_current
                elif p == "temperature":
                    v = r.temperature
                elif p == "cycle_count":
                    v = r.cycle_count
                elif p == "speed":
                    v = float(comp.get("speed_mps") or 1.75)
                else:
                    v = None
                self._touch(d["_id"], r.timestamp, None if v is None else round(float(v), 4), seen=now)
            touched.add(comp["elevator_id"])
        for eid in touched:
            await self._maybe_broadcast(eid)

    # ---------------------------------------------------------------- external ingest
    async def push(self, elevator_id: str, sensor_id: str, timestamp: datetime | None, value=None,
                   values: list[float] | None = None, waveform: list[float] | None = None) -> dict:
        did = sensor_doc_id(elevator_id, sensor_id)
        d = self.registry.get(did)
        ts = as_utc(timestamp) if timestamp else utcnow()
        if d is None:
            self.unregistered.appendleft({"elevator_id": elevator_id, "sensor_id": sensor_id, "at": clean(ts)})
            return {"accepted": False, "reason": "unregistered", "sensor_id": sensor_id}
        gain, offset = float(d.get("gain") or 1.0), float(d.get("offset") or 0.0)
        param = d["parameter"]
        if param == "vibration_waveform":
            raw = waveform if waveform is not None else values
            if not raw or not 32 <= len(raw) <= 4096:
                return {"accepted": False, "reason": "waveform must have 32-4096 samples"}
            wave = [gain * float(x) + offset for x in raw]
            self._touch(did, ts, round(max(abs(x) for x in wave), 4), wave)
        else:
            if value is None:
                if values:
                    value = sum(values) / len(values)
                else:
                    return {"accepted": False, "reason": "value required"}
            v = gain * float(value) + offset
            self._touch(did, ts, round(v, 4))
            wave = None
        # bucket → reading
        cid = d["component_id"]
        b = self.buckets.get(cid)
        if b is None or (ts.timestamp() - b.start) > BUCKET_SECONDS:
            if b is not None:
                await self._complete(cid, b, force=True)
            b = self.buckets[cid] = _Bucket(ts.timestamp())
        b.ts = ts
        if param == "vibration_waveform":
            b.waveform = wave
        elif param == "rope_tension":
            b.ropes[int(d.get("rope_number") or 1)] = v
        else:
            b.values[param] = v
        result = await self._complete(cid, b)
        await self._maybe_broadcast(elevator_id)
        return {"accepted": True, "sensor_id": sensor_id, "component_id": cid, "parameter": param, "reading_ingested": result}

    def _rope_count(self, cid: str) -> int:
        n = [d.get("rope_number") for d in self.registry.values() if d["component_id"] == cid and d["parameter"] == "rope_tension"]
        return max([int(x) for x in n if x] or [P.ROPES_PER_SET])

    async def flush_stale(self) -> None:
        """Complete buckets that have waited too long (called from the simulator loop / on demand)."""
        now = time.time()
        for cid, b in list(self.buckets.items()):
            if now - b.start > BUCKET_FLUSH_SECONDS:
                await self._complete(cid, b, force=True)

    async def _complete(self, cid: str, b: _Bucket, force: bool = False) -> bool:
        lk = self.last_known[cid]
        ropes = [b.ropes[i] for i in sorted(b.ropes)] if b.ropes else None
        tension = b.values.get("tension") or (sum(ropes) / len(ropes) if ropes else None)
        have = {"tension": tension is not None, "vibration_waveform": b.waveform is not None,
                "motor_current": "motor_current" in b.values, "temperature": "temperature" in b.values,
                "cycle_count": "cycle_count" in b.values}
        n_ropes = self._rope_count(cid)
        ropes_complete = ropes is None or len(ropes) >= n_ropes
        if not (all(have.values()) and ropes_complete):
            if not force:
                return False
            # fill from last known values; a reading without any waveform ever seen can't be assembled
            tension = tension if tension is not None else lk.get("tension")
            wave = b.waveform if b.waveform is not None else lk.get("vibration_waveform")
            cur = b.values.get("motor_current", lk.get("motor_current"))
            temp = b.values.get("temperature", lk.get("temperature"))
            cyc = b.values.get("cycle_count", lk.get("cycle_count"))
            if ropes is not None and len(ropes) < n_ropes and lk.get("rope_tensions"):
                ropes = ropes + list(lk["rope_tensions"][len(ropes):n_ropes])
            if None in (tension, wave, cur, temp, cyc):
                self.buckets.pop(cid, None)
                return False
        else:
            wave, cur, temp, cyc = b.waveform, b.values["motor_current"], b.values["temperature"], b.values["cycle_count"]
        reading = SensorReading(component_id=cid, timestamp=b.ts or utcnow(), tension=float(tension),
                                rope_tensions=[float(x) for x in ropes] if ropes else None,
                                vibration_waveform=[float(x) for x in wave], motor_current=float(cur),
                                temperature=float(temp), cycle_count=float(cyc), mode="external")
        lk.update({"tension": tension, "vibration_waveform": wave, "motor_current": cur, "temperature": temp,
                   "cycle_count": cyc, "rope_tensions": ropes})
        self.buckets.pop(cid, None)
        await self.pipeline.ingest([reading])
        return True

    async def persist_state(self) -> None:
        for did, st in self.state.items():
            if did in self.registry and st.get("last_seen"):
                await self.db.sensors.update_one({"_id": did}, {"$set": {"last_seen": st["last_seen"], "last_value": st["last_value"]}})


# ---------------------------------------------------------------- default registry
def default_sensors(elevator: dict, components: list[dict], rope_count: int = P.ROPES_PER_SET) -> list[dict]:
    """The sensor set the simulator auto-registers for a demo elevator."""
    eid = elevator["_id"]
    out, n = [], 0

    def add(name, stype, comp, parameter, unit, interval_s, location, rope_number=None):
        nonlocal n
        n += 1
        out.append({"elevator_id": eid, "sensor_id": f"S-{n:02d}", "name": name, "type": stype, "component_id": comp["_id"],
                    "rope_number": rope_number, "parameter": parameter, "unit": unit, "interval_s": interval_s,
                    "location": location, "gain": 1.0, "offset": 0.0, "source": "simulator", "created_at": utcnow()})

    by_type = {c["type"]: c for c in components}
    if "rope" in by_type:
        for i in range(1, rope_count + 1):
            add(f"Rope {i} tension load cell", "tension_load_cell", by_type["rope"], "rope_tension", "kN", 60, f"Rope termination R{i}, car side", i)
        add("Rope set accelerometer", "accelerometer", by_type["rope"], "vibration_waveform", "g", 60, "Car top, rope hitch plate")
    if "sheave" in by_type:
        add("Sheave accelerometer", "accelerometer", by_type["sheave"], "vibration_waveform", "g", 60, "Sheave frame, radial")
        add("Sheave drive current transducer", "current_transducer", by_type["sheave"], "motor_current", "A", 60, "Drive output phase U")
    if "bearing" in by_type:
        add("Sheave bearing accelerometer", "accelerometer", by_type["bearing"], "vibration_waveform", "g", 60, "Bearing housing, drive end")
        add("Sheave bearing RTD", "temperature", by_type["bearing"], "temperature", "°C", 60, "Bearing housing PT100")
    if "traction" in by_type:
        add("Motor current transducer", "current_transducer", by_type["traction"], "motor_current", "A", 60, "Drive output phase U")
        add("Winding thermocouple", "temperature", by_type["traction"], "temperature", "°C", 60, "Stator winding, hottest slot")
        add("Machine accelerometer", "accelerometer", by_type["traction"], "vibration_waveform", "g", 60, "Machine bedplate, vertical")
        add("Speed encoder", "speed_encoder", by_type["traction"], "speed", "m/s", 60, "Motor shaft encoder")
        add("Trip counter", "trip_counter", by_type["traction"], "cycle_count", "trips", 60, "Controller trip register")
    return out
