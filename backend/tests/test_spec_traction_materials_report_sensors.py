"""Acceptance checks for: traction-only, material spec -> physics, rope fault report, sensor streaming."""
import numpy as np
import pytest


def test_no_hydraulic_and_lift_type_badge(client, staff):
    r = client.get("/elevators", headers=staff)
    assert r.status_code == 200
    for e in r.json():
        assert e["lift_type"] == "traction" and e["drive_type"] in ("Gearless PM", "Geared")
    r = client.patch("/elevators/elv-01", json={"drive_type": "Geared"}, headers=staff)
    assert r.json()["drive_type"] == "Geared"


def test_material_spec_roundtrip_and_physics_change(client, staff):
    cid = "elv-01-rope"
    # The fleet now seeds with a real material spec on every component, so
    # start this component from a genuinely blank slate to test the
    # unset -> preset -> full-spec roundtrip in isolation.
    client.put(f"/components/{cid}/material-spec", json={"spec": {}}, headers=staff)
    m = client.get(f"/components/{cid}/material-spec", headers=staff).json()
    # Clearing the spec still auto-sets gsm="Not applicable" for a steel rope
    # (the one non-blank field), so completeness sits just above zero; every
    # physics input should still be a default (gsm feeds none of them).
    assert m["completeness_pct"] <= 10 and all(i["source"] == "default" for i in m["physics_inputs"])
    assert m["schema"]["presets"][0]["id"] == "8x19S-FC"
    preset = client.post(f"/components/{cid}/material-spec/preset/8x19S-IWRC", headers=staff).json()
    assert preset["gsm"] == "Not applicable"
    spec = {**preset, "weight": "0.44 kg/m", "rope_count": 5, "sheave_diameter_mm": 480}
    r = client.put(f"/components/{cid}/material-spec", json={"spec": spec}, headers=staff)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["repredicted"] is True and out["prediction"]["status"] in ("good", "medium", "at-risk")
    back = client.get(f"/components/{cid}/material-spec", headers=staff).json()
    for k in ("material_type", "material_grade", "gsm", "thickness_mm", "composition", "weight", "strength", "coating"):
        assert back["spec"].get(k) not in (None, ""), k
    assert back["completeness_pct"] == 100 and back["blank_fields"] == []
    by_key = {i["key"]: i for i in back["physics_inputs"]}
    assert by_key["rope_breaking_load_kn"] == {**by_key["rope_breaking_load_kn"], "value": 72.0, "source": "user"}
    assert by_key["d_over_d"]["value"] == 48.0 and by_key["ropes_per_set"]["value"] == 5
    # bearing rating, sheave hardness and insulation class feed the other laws
    client.put("/components/elv-01-bearing/material-spec", json={"spec": {"dynamic_rating_c_kn": 380}}, headers=staff)
    client.put("/components/elv-01-sheave/material-spec", json={"spec": {"hardness_hb": 300, "material_type": "Forged steel C45"}}, headers=staff)
    client.put("/components/elv-01-traction/material-spec",
               json={"spec": {"rows": [{"part": "Windings", "material_type": "Enamelled copper", "material_grade": "Insulation class H",
                                         "gsm": "", "thickness_mm": 1.2, "composition": "Cu", "weight": "45 kg", "strength": "class H", "coating": "VPI"}]}}, headers=staff)
    # elv-02-rope is also seeded with a real spec by default now - blank it so
    # the "still on defaults" comparison below is meaningful.
    client.put("/components/elv-02-rope/material-spec", json={"spec": {}}, headers=staff)
    model = client.get("/analytics/model", headers=staff).json()
    inputs = {p["component_id"]: {i["key"]: i for i in p["inputs"]} for p in model["physics_inputs"]}
    assert inputs["elv-01-bearing"]["bearing_c_kn"]["source"] == "user" and inputs["elv-01-bearing"]["bearing_c_kn"]["value"] == 380
    assert inputs["elv-01-sheave"]["sheave_hardness_pa"]["value"] == pytest.approx(300 * 9.807e6)
    assert inputs["elv-01-traction"]["insulation_limit_c"]["value"] == 180
    assert inputs["elv-02-rope"]["rope_breaking_load_kn"]["source"] == "default"
    # setup view per elevator
    setup = client.get("/elevators/elv-01/material-spec", headers=staff).json()
    assert len(setup["components"]) == 4 and setup["overall_completeness_pct"] > 0
    # the GSM rule: a belt keeps GSM editable
    r = client.put(f"/components/{cid}/material-spec", json={"spec": {"material_type": "PU-coated flat steel belt", "gsm": 2400}}, headers=staff)
    assert r.json()["spec"]["gsm"] == 2400 and r.json()["gsm_applicable"]


def test_demo_rope_report_names_the_faulty_rope_and_chatbot_agrees(client, staff):
    r = client.get("/elevators/elv-03/reports/suspension-rope", headers=staff)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["available"] and rep["verdict"] == "fault" and rep["faulty_ropes"] == ["R6"]
    assert rep["banner"].startswith("FAULT DETECTED: Rope R6")
    r6 = next(x for x in rep["ropes"] if x["id"] == "R6")
    assert r6["status"] == "faulty" and r6["deviation_pct"] >= rep["limits"]["fault_pct"]
    assert sum(1 for x in rep["ropes"] if x["status"] == "faulty") == 1
    assert "R6" in rep["layman"] and "stretcher" in rep["layman"] and "x faster" in rep["layman"]
    assert rep["worst"]["wear_multiplier"] == pytest.approx((1 + r6["deviation_pct"] / 100) ** 4.5, rel=0.02)
    assert [f["key"] for f in rep["factors"]][0] in ("tension_imbalance_pct", "imbalance_trend", "fatigue_index")
    assert all("meaning" in f and "severity" in f for f in rep["factors"]) and len(rep["factors"]) == 6
    assert any(c.startswith("possible:") for c in rep["causes"]) and rep["causes"][-1] == "Confirm by inspection."
    assert rep["recommendation"]["action"].startswith(("REPLACE WHOLE ROPE SET", "RE-TENSION"))
    assert rep["recommendation"]["urgency_days"] and rep["recommendation"]["next_checkup"]
    assert len(rep["series"]) >= 3 and len(rep["series"][0]["tensions"]) == 6
    assert rep["limits"]["fault_pct"] == 10.0 and rep["limits"]["watch_pct"] == 5.0
    # healthy lift: no fault
    ok = client.get("/elevators/elv-06/reports/suspension-rope", headers=staff).json()
    assert ok["verdict"] == "no fault" and ok["banner"] == "No rope fault detected"
    # chatbot uses the same per-rope data
    a = client.post("/chatbot/query", json={"message": "Which rope is faulty and why?", "elevator_id": "elv-03"}, headers=staff).json()
    assert "R6" in a["answer"] and "FAULT DETECTED" in a["answer"]


def test_sensor_registry_live_and_ingestion(client, staff):
    eid = "elv-01"
    meta = client.get("/sensors/meta", headers=staff).json()
    assert "accelerometer" in meta["types"]
    reg = client.get(f"/elevators/{eid}/sensors", headers=staff).json()
    assert reg["source"] == "simulated" and len(reg["sensors"]) >= 12          # auto-registered by the simulator
    assert all(c["complete"] for c in reg["coverage"]), reg["coverage"]
    rope_sensors = [s for s in reg["sensors"] if s["parameter"] == "rope_tension"]
    assert sorted(s["rope_number"] for s in rope_sensors) == [1, 2, 3, 4, 5, 6]
    # add / edit / delete
    body = {"sensor_id": "S-99", "name": "Spare accel", "type": "accelerometer", "component_id": f"{eid}-bearing",
            "parameter": "vibration_waveform", "unit": "g", "interval_s": 30, "location": "test", "gain": 1.0, "offset": 0.0}
    r = client.put(f"/elevators/{eid}/sensors/S-99", json=body, headers=staff)
    assert r.status_code == 200 and r.json()["status"] == "offline"
    bad = client.put(f"/elevators/{eid}/sensors/S-98", json={**body, "sensor_id": "S-98", "parameter": "temperature"}, headers=staff)
    assert bad.status_code == 400
    assert client.delete(f"/elevators/{eid}/sensors/S-99", headers=staff).json()["ok"]
    # missing-sensor warning
    client.delete(f"/elevators/{eid}/sensors/S-03", headers=staff)
    cov = client.get(f"/elevators/{eid}/sensors", headers=staff).json()["coverage"]
    rope_cov = next(c for c in cov if c["type"] == "rope")
    assert not rope_cov["complete"] and "R3" in rope_cov["missing"][0]
    client.post(f"/elevators/{eid}/sensors/defaults", headers=staff)
    # external gateway posts data: one message per sensor, bucketed into one SensorReading
    client.put(f"/elevators/{eid}/sensors/source", json={"source": "external"}, headers=staff)
    comp_before = client.get(f"/components/{eid}-bearing/prediction", headers=staff).json()
    reg = client.get(f"/elevators/{eid}/sensors", headers=staff).json()
    bearing = [s for s in reg["sensors"] if s["component_id"] == f"{eid}-bearing"]
    accel = next(s for s in bearing if s["parameter"] == "vibration_waveform")
    rtd = next(s for s in bearing if s["parameter"] == "temperature")
    key = {"X-Ingest-Key": "ingest-key"}
    assert client.post("/ingest/sensor-data", json={"elevator_id": eid, "sensor_id": rtd["sensor_id"], "value": 41.0}).status_code in (401, 403)
    r = client.post("/ingest/sensor-data", json={"elevator_id": eid, "sensor_id": "NOPE", "value": 1}, headers=key).json()
    assert r["accepted"] is False and r["reason"] == "unregistered"
    ts = "2030-01-01T10:00:00Z"
    wave = list(np.random.default_rng(0).normal(0, 0.05, 512))                 # 512 samples → resampled to 256
    for sid, payload in [(rtd["sensor_id"], {"value": 41.0}), (accel["sensor_id"], {"waveform": wave})]:
        r = client.post("/ingest/sensor-data", json={"elevator_id": eid, "sensor_id": sid, "timestamp": ts, **payload}, headers=key)
        assert r.status_code == 200 and r.json()["accepted"], r.text
    # the bearing has no tension / current / trip-counter sensors of its own: the bucket is completed from last-known values,
    # so post the remaining parameters via the elevator's shared sensors to make one full reading
    cur = next(s for s in reg["sensors"] if s["parameter"] == "motor_current" and s["component_id"] == f"{eid}-traction")
    # register a tension + trip counter on the bearing so the reading can be assembled from its own sensors
    for sid, stype, param, unit in [("S-90", "tension_load_cell", "tension", "kN"), ("S-91", "trip_counter", "cycle_count", "trips"),
                                    ("S-92", "current_transducer", "motor_current", "A")]:
        client.put(f"/elevators/{eid}/sensors/{sid}", json={"sensor_id": sid, "name": sid, "type": stype, "component_id": f"{eid}-bearing",
                                                           "parameter": param, "unit": unit, "interval_s": 60, "location": "", "gain": 1.0, "offset": 0.0}, headers=staff)
    for sid, val in [("S-90", 52.0), ("S-91", 9_999_999.0), ("S-92", 24.0)]:
        r = client.post("/ingest/sensor-data", json={"elevator_id": eid, "sensor_id": sid, "timestamp": ts, "value": val}, headers=key)
        assert r.json()["accepted"]
    assert r.json()["reading_ingested"] is True
    live = client.get(f"/elevators/{eid}/sensors/live", headers=staff).json()
    rows = {s["sensor_id"]: s for s in live["sensors"]}
    assert rows["S-90"]["status"] == "online" and rows["S-90"]["last_value"] == 52.0 and rows["S-90"]["msg_count"] == 1
    assert live["summary"]["online"] >= 5 and live["source"] == "external"
    comp_after = client.get(f"/components/{eid}-bearing/prediction", headers=staff).json()
    assert comp_after["timestamp"].startswith("2030-01-01") and comp_after["timestamp"] != comp_before["timestamp"]
    ser = client.get(f"/elevators/{eid}/sensors/{accel['sensor_id']}/series", headers=staff).json()
    assert len(ser["waveform"]) == 512 and "spectrum" in ser
    # test tools
    sample = client.get(f"/elevators/{eid}/sensors/S-90/sample", headers=staff).json()
    assert "curl" in sample and sample["json"]["sensor_id"] == "S-90"
    t = client.post(f"/elevators/{eid}/sensors/S-90/test", headers=staff).json()
    assert t["result"]["accepted"]
    client.put(f"/elevators/{eid}/sensors/source", json={"source": "simulated"}, headers=staff)


def test_ws_ingest_rejects_bad_key_and_accepts_messages(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/ingest/elv-02?key=wrong"):
            pass
    with client.websocket_connect("/ws/ingest/elv-02?key=ingest-key") as ws:
        ws.send_json({"sensor_id": "S-09", "value": 23.5, "timestamp": "2030-01-02T00:00:00Z"})
        r = ws.receive_json()
        assert r["accepted"] is True, r
        ws.send_json({"sensor_id": "ghost", "value": 1})
        assert ws.receive_json()["reason"] == "unregistered"
