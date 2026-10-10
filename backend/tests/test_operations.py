"""
Tests for the Section 1-7 additions.

The most important test in this file is the first one: Section 1's whole
point is that repeated reads return identical numbers, and that is exactly
the kind of regression that is invisible until a judge clicks a tab twice
during a demo.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import opdata, risk
from app.services.recommendations import decide_action

NOW = datetime(2026, 6, 15, 14, 30, tzinfo=timezone.utc)


def _elevator(eid="lift-test", floors=10):
    return {"_id": eid, "name": eid, "floors": floors}


# ---------------------------------------------------------------------------
# Section 1 - energy values must be stable and clock-tied
# ---------------------------------------------------------------------------

def test_energy_calendar_is_identical_across_repeated_calls():
    """The reported bug: clicking a month made the number grow. It must not."""
    e = _elevator()
    first = opdata.energy_calendar([e], 5.0, "month", NOW)
    for _ in range(10):
        assert opdata.energy_calendar([e], 5.0, "month", NOW) == first


def test_batch_view_is_identical_across_repeated_calls():
    e = _elevator()
    first = opdata.energy_calendar([e], 5.0, "batch", NOW)
    for _ in range(10):
        assert opdata.energy_calendar([e], 5.0, "batch", NOW) == first


def test_past_months_are_frozen_as_the_clock_advances():
    """A completed month must not change when 'now' moves forward."""
    e = _elevator()
    later = NOW + timedelta(days=3)
    a = {r["month"]: r for r in opdata.energy_calendar([e], 5.0, "month", NOW)["rows"]}
    b = {r["month"]: r for r in opdata.energy_calendar([e], 5.0, "month", later)["rows"]}
    for key in set(a) & set(b):
        if a[key]["state"] == "complete" and b[key]["state"] == "complete":
            assert a[key]["energy_kwh"] == b[key]["energy_kwh"], f"{key} changed"


def test_only_the_current_batch_grows_with_the_clock():
    e = _elevator()
    early = opdata.energy_calendar([e], 5.0, "batch",
                                   datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc))["rows"]
    late = opdata.energy_calendar([e], 5.0, "batch",
                                  datetime(2026, 6, 15, 16, 0, tzinfo=timezone.utc))["rows"]
    # 12:00-18:00 is the running batch at both times, and must have grown.
    assert late[2]["energy_kwh"] > early[2]["energy_kwh"]
    # Earlier, completed batches must be untouched.
    assert late[0]["energy_kwh"] == early[0]["energy_kwh"]
    assert late[1]["energy_kwh"] == early[1]["energy_kwh"]


def test_future_batches_are_upcoming_and_empty():
    rows = opdata.energy_calendar([_elevator()], 5.0, "batch", NOW)["rows"]
    assert rows[3]["state"] == "upcoming"
    assert rows[3]["energy_kwh"] == 0.0
    assert rows[3]["trips"] == 0


def test_current_month_is_month_to_date():
    rows = opdata.energy_calendar([_elevator()], 5.0, "month", NOW)["rows"]
    assert rows[-1]["state"] == "month-to-date"
    assert rows[-1]["days_counted"] == NOW.day
    assert len(rows) == 12


def test_cost_is_always_kwh_times_tariff():
    for view in ("month", "batch"):
        report = opdata.energy_calendar([_elevator()], 5.0, view, NOW)
        for r in report["rows"]:
            assert r["cost_inr"] == pytest.approx(r["energy_kwh"] * 5.0, abs=0.02)


# ---------------------------------------------------------------------------
# Section 3 - operational data
# ---------------------------------------------------------------------------

def test_operational_summary_has_all_seven_inputs():
    s = opdata.operational_summary(_elevator(), 30, NOW)
    for key in ("trips", "door_movements", "car_calls", "power_ups"):
        assert s["totals"][key] > 0 or key == "power_ups"
    assert s["errors"] and "code" in s["errors"][0] and "severity" in s["errors"][0]
    assert {x["system"] for x in s["connected_systems"]} == set(opdata.CONNECTED_SYSTEMS)


def test_operational_data_is_deterministic():
    a = opdata.operational_summary(_elevator(), 30, NOW)
    b = opdata.operational_summary(_elevator(), 30, NOW)
    assert a == b


def test_operational_risk_uplift_is_bounded():
    for i in range(20):
        up = opdata.operational_risk_uplift(_elevator(f"lift-{i}"), NOW)
        assert 0.0 <= up["uplift_pct"] <= 12.0


# ---------------------------------------------------------------------------
# Section 4 - usage cycles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("view,expected", [("week", 7), ("year", 12)])
def test_cycle_series_bucket_counts(view, expected):
    s = opdata.cycle_series(_elevator(), "rope", view, NOW)
    assert len(s["buckets"]) == expected


def test_component_cycles_never_exceed_lift_usage():
    """Consistency rule: a component cannot out-cycle the lift that drives it."""
    e = _elevator()
    trips = opdata.cycle_series(e, "traction", "month", NOW)["total"]   # one start per trip
    doors = opdata.cycle_series(e, "door", "month", NOW)["total"]
    # The door opens and closes once per trip, so it is bounded by 2x trips.
    assert doors <= trips * 2.5
    # Bearings see exactly one load reversal per trip.
    assert opdata.cycle_series(e, "bearing", "month", NOW)["total"] == trips


def test_lifetime_cycles_stay_within_plausible_bounds():
    for i in range(15):
        for ctype in opdata.CYCLE_DEFINITION:
            lt = opdata.lifetime_cycles(_elevator(f"lift-{i}", 8 + i), ctype, NOW)
            assert 0 <= lt["used_pct"] <= 100.0, f"{ctype} at {lt['used_pct']}%"


def test_past_cycle_buckets_are_frozen():
    e = _elevator()
    a = opdata.cycle_series(e, "rope", "year", NOW)["buckets"]
    b = opdata.cycle_series(e, "rope", "year", NOW + timedelta(days=2))["buckets"]
    for x, y in zip(a, b):
        if x["state"] == "complete" and y["state"] == "complete":
            assert x["cycles"] == y["cycles"]


# ---------------------------------------------------------------------------
# Section 5 - one shared risk model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prob,rul,expected", [
    (85.0, 300, "critical"),      # probability alone
    (5.0, 9, "critical"),         # short remaining life alone
    (40.0, 300, "high"),
    (5.0, 30, "high"),
    (20.0, 300, "medium"),
    (2.0, 900, "good"),
])
def test_risk_classification(prob, rul, expected):
    assert risk.classify(prob, rul) == expected


def test_risk_sorting_puts_critical_first():
    rows = [
        {"failure_probability": 5, "rul_days": 900, "health_score": 95},
        {"failure_probability": 80, "rul_days": 10, "health_score": 20},
        {"failure_probability": 40, "rul_days": 200, "health_score": 60},
    ]
    ordered = risk.sort_by_risk(rows)
    assert [risk.risk_of(r) for r in ordered] == ["critical", "high", "good"]


def test_risk_banner_text():
    rows = [{"risk": "critical"}, {"risk": "high"}, {"risk": "good"}]
    s = risk.summarise(rows)
    assert s["needs_replacement"] == 2
    assert "2 components need replacement" == s["banner"]


# ---------------------------------------------------------------------------
# Section 6 - component vs unit decision rule
# ---------------------------------------------------------------------------

def _comp(cid, ctype, prob, rul):
    return {"_id": cid, "type": ctype, "failure_probability": prob, "rul_days": rul,
            "status": "at-risk" if prob > 50 else "good", "name": ctype}


def test_isolated_worn_part_replaces_the_component_only():
    target = _comp("c1", "rope", 75, 12)
    siblings = [target, _comp("c2", "sheave", 3, 900), _comp("c3", "bearing", 2, 800),
                _comp("c4", "traction", 4, 950)]
    d = decide_action(target, siblings)
    assert d["action"] == "REPLACE COMPONENT"
    assert d["rule"] == "isolated-component"


def test_coupled_degradation_replaces_the_whole_unit():
    target = _comp("c1", "rope", 80, 10)
    siblings = [target, _comp("c2", "sheave", 65, 20), _comp("c3", "bearing", 55, 25),
                _comp("c4", "traction", 5, 900)]
    d = decide_action(target, siblings)
    assert d["action"] == "REPLACE WHOLE UNIT"
    assert d["rule"] == "coupled-degradation"


def test_beyond_rated_lifecycle_replaces_the_whole_unit():
    target = _comp("c1", "rope", 40, 200)
    d = decide_action(target, [target], cycle_usage=97.0)
    assert d["action"] == "REPLACE WHOLE UNIT"
    assert d["rule"] == "beyond-rated-lifecycle"


def test_expensive_repair_replaces_the_whole_unit():
    """Rule 3 in isolation: ONE degraded neighbour, so the coupled-degradation
    rule (which needs two) cannot fire, but the repair bill still exceeds 60%
    of a unit replacement.

    Traction (350k planned) + rope (180k) = 530k, against a 510k threshold
    (60% of the 850k unit cost).
    """
    target = _comp("c1", "traction", 90, 8)
    siblings = [target, _comp("c2", "rope", 45, 40), _comp("c3", "bearing", 2, 900)]
    d = decide_action(target, siblings)
    assert d["action"] == "REPLACE WHOLE UNIT"
    assert d["rule"] == "repair-cost-exceeds-threshold"


def test_healthy_component_needs_no_action():
    target = _comp("c1", "bearing", 2, 900)
    assert decide_action(target, [target])["action"] == "NO ACTION"


def test_healthy_component_is_never_told_to_replace_itself_for_a_sick_neighbour():
    """Regression: a perfectly healthy traction machine was getting
    'REPLACE WHOLE UNIT' purely because a coupled rope was critical and the
    combined repair math crossed the cost threshold - even though nothing was
    wrong with the traction machine itself. Rules 2-4 must only ever fire on a
    component that is itself critical or high risk.
    """
    healthy_traction = _comp("c1", "traction", 0.0, 1500)
    critical_rope = _comp("c2", "rope", 92.0, 8)
    siblings = [healthy_traction, critical_rope, _comp("c3", "sheave", 3, 900),
                _comp("c4", "bearing", 2, 900)]
    d = decide_action(healthy_traction, siblings)
    assert d["action"] == "NO ACTION"
    # The rope's own card, meanwhile, should still flag appropriately.
    d_rope = decide_action(critical_rope, siblings)
    assert d_rope["action"] in ("REPLACE COMPONENT", "REPLACE WHOLE UNIT")


# ---------------------------------------------------------------------------
# API surface - the new endpoints, end to end
# ---------------------------------------------------------------------------

def test_energy_calendar_endpoint_is_stable(client, staff):
    a = client.get("/analytics/energy/calendar?view=month", headers=staff)
    b = client.get("/analytics/energy/calendar?view=month", headers=staff)
    assert a.status_code == 200
    assert a.json()["rows"] == b.json()["rows"]
    assert len(a.json()["rows"]) == 12


def test_operational_endpoint(client, staff):
    e = client.get("/elevators", headers=staff).json()[0]
    r = client.get(f"/elevators/{e['id']}/operational", headers=staff)
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["trips"] > 0
    assert "errors" in body and "connected_systems" in body
    assert 0 <= body["risk"]["uplift_pct"] <= 12


def test_cycles_endpoint_returns_every_component_type(client, staff):
    e = client.get("/elevators", headers=staff).json()[0]
    r = client.get(f"/elevators/{e['id']}/cycles?view=week", headers=staff)
    assert r.status_code == 200
    body = r.json()
    assert set(body["components"]) == set(opdata.CYCLE_DEFINITION)
    assert len(body["components"]["rope"]["buckets"]) == 7
    assert body["lifetime"]["rope"]["rated_cycles"] > 0


def test_cycles_comparison_and_csv(client, staff):
    r = client.get("/analytics/cycles/comparison", headers=staff)
    assert r.status_code == 200
    assert all({"week", "month", "year"} <= set(row) for row in r.json()["rows"])

    csv_res = client.get("/analytics/cycles/monthly.csv", headers=staff)
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert csv_res.text.splitlines()[0] == "Elevator,Component,Month,Cycles,State"


def test_recommendations_endpoint(client, staff):
    e = client.get("/elevators", headers=staff).json()[0]
    r = client.get(f"/elevators/{e['id']}/recommendations", headers=staff)
    assert r.status_code == 200
    body = r.json()
    assert len(body["cards"]) == 4
    for card in body["cards"]:
        assert card["action"] in ("REPLACE WHOLE UNIT", "REPLACE COMPONENT", "INSPECT / ADJUST",
                                  "LUBRICATE", "MONITOR", "NO ACTION")
        assert card["decision_why"]
        assert card["priority"] in ("critical", "high", "medium", "good")
    # Critical first.
    assert body["cards"] == sorted(body["cards"], key=lambda c: c["priority_rank"])


def test_components_carry_the_shared_risk_level(client, staff):
    e = client.get("/elevators", headers=staff).json()[0]
    detail = client.get(f"/elevators/{e['id']}", headers=staff).json()
    assert "risk" in detail and "counts" in detail["risk"]
    for c in detail["components"]:
        assert c["risk"] in ("critical", "high", "medium", "good")
        assert c["risk_label"]


def test_new_endpoints_require_authentication(client):
    for path in ("/analytics/energy/calendar", "/analytics/cycles/comparison",
                 "/analytics/recommendations", "/risk/thresholds"):
        assert client.get(path).status_code in (401, 403)
