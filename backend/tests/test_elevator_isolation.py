"""
Regression test for elevator data isolation (Section 11.C & 12).

Verifies that:
- Elevator A service history never appears on Elevator B.
- Elevator A usage profile never leaks to Elevator B.
- Age, usage, and service history are strictly isolated per elevator_id.
"""

from app.services import opdata


def test_elevator_service_history_and_cycles_isolation(client, staff):
    # 1. Fetch elevators list
    elevators = client.get("/elevators", headers=staff).json()
    assert len(elevators) >= 3, "Expected at least 3 elevators in fleet"
    e1, e2, e3 = elevators[0]["id"], elevators[1]["id"], elevators[2]["id"]

    # 2. Fetch cycles and service history for each elevator
    res1 = client.get(f"/elevators/{e1}/cycles", headers=staff)
    res2 = client.get(f"/elevators/{e2}/cycles", headers=staff)
    res3 = client.get(f"/elevators/{e3}/cycles", headers=staff)
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res3.status_code == 200

    data1 = res1.json()
    data2 = res2.json()
    data3 = res3.json()

    # Verify elevator names and IDs match requested elevator
    assert data1["elevator_id"] == e1
    assert data2["elevator_id"] == e2
    assert data3["elevator_id"] == e3

    # Verify age_and_usage isolation
    assert "age_and_usage" in data1
    assert "age_and_usage" in data2
    assert "age_and_usage" in data3
    assert data1["age_and_usage"]["installation_date"] != ""
    assert data1["age_and_usage"]["age_days"] > 0

    # Verify service history isolation
    hist1 = data1.get("service_history", [])
    hist2 = data2.get("service_history", [])
    hist3 = data3.get("service_history", [])

    # Ensure no cross-contamination between service histories
    ids1 = {item["id"] for item in hist1}
    ids2 = {item["id"] for item in hist2}
    ids3 = {item["id"] for item in hist3}

    assert ids1.isdisjoint(ids2), f"Elevator {e1} and {e2} share history logs: {ids1 & ids2}"
    assert ids1.isdisjoint(ids3), f"Elevator {e1} and {e3} share history logs: {ids1 & ids3}"
    assert ids2.isdisjoint(ids3), f"Elevator {e2} and {e3} share history logs: {ids2 & ids3}"

    # Test dedicated service-history endpoint isolation
    sh_res1 = client.get(f"/elevators/{e1}/service-history", headers=staff)
    sh_res3 = client.get(f"/elevators/{e3}/service-history", headers=staff)
    assert sh_res1.status_code == 200
    assert sh_res3.status_code == 200

    sh_ids1 = {item["id"] for item in sh_res1.json().get("history", [])}
    sh_ids3 = {item["id"] for item in sh_res3.json().get("history", [])}
    assert sh_ids1.isdisjoint(sh_ids3)


def test_elevator_age_and_usage_isolation():
    elv1 = {"_id": "elv-01", "name": "Lift 1", "floors": 14}
    elv2 = {"_id": "elv-02", "name": "Lift 2", "floors": 14}
    elv3 = {"_id": "elv-03", "name": "Lift 3", "floors": 14}

    u1 = opdata.elevator_age_and_usage(elv1)
    u2 = opdata.elevator_age_and_usage(elv2)
    u3 = opdata.elevator_age_and_usage(elv3)

    assert u1["installation_date"] != ""
    assert u2["installation_date"] != ""
    assert u3["installation_date"] != ""
    # Distinct deterministic profiles
    assert u1["age_days"] != u2["age_days"] or u1["usage_summary"]["total_trips"] != u2["usage_summary"]["total_trips"]
