from datetime import datetime, timedelta, timezone

import numpy as np

STATUS_ORDER = {"at-risk": 0, "medium": 1, "good": 2}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["database"] is True


def test_login_rejects_bad_password(client):
    r = client.post("/auth/login", json={"email": "admin@elevcare.local", "password": "nope"})
    assert r.status_code == 401


def test_elevator_data_requires_login(client):
    for path in ["/elevators", "/elevators/elv-03", "/components/elv-03-rope", "/complaints", "/analytics/energy"]:
        assert client.get(path).status_code == 401, path


def test_elevator_list_and_demo_elevator(client, staff):
    elevators = client.get("/elevators", headers=staff).json()
    assert len(elevators) == 6
    ranks = [STATUS_ORDER[e["status"]] for e in elevators]
    assert ranks == sorted(ranks)
    demo = next(e for e in elevators if e["is_demo"])
    assert demo["id"] == "elv-03"


def test_components_sorted_at_risk_first(client, staff):
    e = client.get("/elevators/elv-03", headers=staff).json()
    comps = e["components"]
    assert {c["type"] for c in comps} == {"rope", "sheave", "bearing", "traction"}
    ranks = [STATUS_ORDER[c["status"]] for c in comps]
    assert ranks == sorted(ranks)
    rope = next(c for c in comps if c["type"] == "rope")
    assert rope["status"] in ("medium", "at-risk")
    assert "imbalance" in rope["explanation"].lower()


def test_component_prediction_history_waveform(client, staff):
    p = client.get("/components/elv-03-rope/prediction", headers=staff).json()
    assert 0 <= p["health_score"] <= 100 and p["RUL_days"] is not None
    assert p["contributing_factors"][0]["feature"] == "tension_imbalance_pct"
    h = client.get("/components/elv-03-rope/history?range=week", headers=staff).json()
    assert len(h["predictions"]) > 5 and len(h["sensors"]) > 10
    w = client.get("/components/elv-03-rope/waveform", headers=staff).json()
    assert len(w["waveform"]) == 256 and len(w["rope_tensions"]) == 6


def test_public_complaint_flow(client, staff):
    pub = client.get("/public/elevators").json()
    assert set(pub[0]) == {"id", "name", "building"}  # no health data exposed publicly
    r = client.post("/complaints", json={"elevator_id": "elv-01", "description": "Door closes too quickly", "category": "door"})
    assert r.status_code == 201
    cid = r.json()["id"]
    listed = client.get("/complaints?elevator_id=elv-01", headers=staff).json()
    assert any(c["id"] == cid and c["submitted_without_login"] for c in listed)
    upd = client.patch(f"/complaints/{cid}", json={"status": "resolved", "note": "adjusted"}, headers=staff).json()
    assert upd["status"] == "resolved" and upd["history"][-1]["note"] == "adjusted"
    assert client.post("/complaints", json={"elevator_id": "nope", "description": "something odd here"}).status_code == 404


def test_complaint_rate_limit(client):
    body = {"elevator_id": "elv-02", "description": "Strange humming noise"}
    codes = [client.post("/complaints", json=body).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]


def test_energy_and_sustainability(client, staff):
    en = client.get("/analytics/energy?range=week", headers=staff).json()
    assert en["totals"]["energy_kwh"] > 0
    assert en["tariff_inr_per_kwh"] == 5
    assert abs(en["totals"]["cost_inr"] - en["totals"]["energy_kwh"] * 5) < 5
    assert en["daily"] and all(d["excess_kwh"] >= 0 for d in en["daily"])
    su = client.get("/analytics/sustainability", headers=staff).json()
    assert su["operational"]["co2_kg"] > 0
    assert su["life_extension"]["avoided_embodied_co2_kg_per_year"] > 0
    el = client.get("/elevators/elv-04/energy?range=week", headers=staff).json()
    assert {r["elevator_id"] for r in el["per_elevator"]} == {"elv-04"}


def test_chatbot_rule_based(client, staff):
    r = client.post("/chatbot/query", json={"message": "Why is the demo lift rope at risk?"}, headers=staff).json()
    assert r["source"] == "rules"
    assert r["context"]["component_id"] == "elv-03-rope"
    assert "imbalance" in r["answer"].lower()
    r = client.post("/chatbot/query", json={"message": "Do you check the safety gear?"}, headers=staff).json()
    assert "technician" in r["answer"]
    n = client.get("/chatbot/notifications", headers=staff).json()
    assert n["llm_enabled"] is False


def test_simulator_toggle_requires_admin(client, staff, admin):
    assert client.post("/simulator/toggle-mode", json={"mode": "real-time"}, headers=staff).status_code == 403
    s = client.post("/simulator/toggle-mode", json={"mode": "real-time"}, headers=admin).json()
    assert s["mode"] == "real-time"
    s = client.post("/simulator/toggle-mode", json={"mode": "batch"}, headers=admin).json()
    assert s["mode"] == "batch"


def test_run_batch_ingests_readings(client, admin):
    before = client.get("/components/elv-01-bearing", headers=admin).json()["last_reading_at"]
    r = client.post("/simulator/run-batch", headers=admin).json()
    # A full batch is 24 components x 6 hourly readings = 144, but the sim
    # clock is clamped to never overshoot real time (Section 2), so once the
    # gap between sim time and now is under 6h the batch is shortened rather
    # than pushed into the future - ingested is a whole number of hourly
    # rounds (a multiple of 24) and never more than a full batch.
    assert 0 <= r["result"]["ingested"] <= 24 * 6
    assert r["result"]["ingested"] % 24 == 0
    status = client.get("/simulator/status", headers=admin).json()
    assert datetime.fromisoformat(status["sim_time"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    if r["result"]["ingested"] > 0:
        after = client.get("/components/elv-01-bearing", headers=admin).json()["last_reading_at"]
        assert after > before


def test_external_ingest_endpoint(client, staff):
    comp = client.get("/components/elv-06-traction", headers=staff).json()
    ts = datetime.fromisoformat(comp["last_reading_at"].replace("Z", "+00:00")) + timedelta(hours=1)
    rng = np.random.default_rng(0)
    reading = {"component_id": "elv-06-traction", "timestamp": ts.isoformat(), "tension": 8.6,
               "vibration_waveform": rng.normal(0, 0.03, 256).tolist(), "motor_current": 23.5,
               "temperature": 40.0, "cycle_count": comp["last_cycle_count"] + 60}
    assert client.post("/ingest/readings", json={"readings": [reading]}).status_code == 401
    r = client.post("/ingest/readings", json={"readings": [reading]}, headers={"X-Ingest-Key": "ingest-key"})
    assert r.status_code == 200 and r.json()["ingested"] == 1
    again = client.post("/ingest/readings", json={"readings": [reading]}, headers={"X-Ingest-Key": "ingest-key"})
    assert again.json()["ingested"] == 0  # duplicate timestamps are ignored


def test_replacement_resets_component(client, staff):
    r = client.post("/components/elv-02-bearing/maintenance", json={"kind": "replacement"}, headers=staff).json()
    assert r["component"]["status"] == "good" and r["component"]["fatigue_index"] == 0
    assert r["log"]["kind"] == "replacement"


def test_admin_can_create_user(client, admin, staff):
    body = {"email": "tech@example.com", "name": "Tech", "role": "staff", "password": "longpassword"}
    assert client.post("/auth/users", json=body, headers=staff).status_code == 403
    assert client.post("/auth/users", json=body, headers=admin).status_code == 201
    assert client.post("/auth/login", json={"email": "tech@example.com", "password": "longpassword"}).status_code == 200


def test_websocket_rejects_bad_token(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/stream?token=bad") as ws:
            ws.receive_text()


def test_websocket_hello(client, admin):
    token = admin["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/stream?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello" and msg["data"]["mode"] in ("batch", "real-time")
