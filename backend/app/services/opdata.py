"""
Deterministic operational-data engine - the single source of truth for
KONE-style operational inputs (Section 3), usage cycle counts (Section 4)
and the calendar energy views (Section 1).

WHY THIS MODULE EXISTS
----------------------
The old energy view regenerated random numbers on every render, so the
figures climbed every time you clicked a tab. Everything here is generated
from a *seeded* pseudo-random stream whose seed is derived from
(elevator id, component type, calendar period). The consequences:

* the same month, week or 6-hour batch always returns the identical value,
  no matter how many times it is requested or re-rendered;
* history is frozen - a past period can never change, because its seed
  contains nothing but the elevator and the date;
* only the *current* period grows, and it grows with the real wall clock
  (the elapsed fraction of the day or of the 6-hour batch), never with a
  click;
* a server restart or a page refresh reproduces the same numbers exactly,
  with nothing persisted.

REPLACING THE SIMULATION WITH REAL DATA
---------------------------------------
Every public function takes an elevator (and optionally a component) plus a
date range and returns plain dictionaries. To switch to a real KONE feed,
replace the bodies of `_day_profile()` and `_error_events()` with a query
against the real telemetry store - the routers, the risk model and the
frontend consume the returned shape, not the generator.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .energy import KWH_PER_TRIP, STANDBY_KW

# --------------------------------------------------------------------------
# seeding
# --------------------------------------------------------------------------

def _seed(*parts: object) -> int:
    """Stable 64-bit seed from arbitrary parts.

    `hash()` is salted per process in Python 3, so it cannot be used: the same
    month would produce different numbers after a restart. md5 of the joined
    parts is stable forever.
    """
    key = "|".join(str(p) for p in parts)
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:16], 16)


def _rng(*parts: object) -> random.Random:
    return random.Random(_seed(*parts))


# --------------------------------------------------------------------------
# calendar helpers - everything is tied to the real clock
# --------------------------------------------------------------------------

BATCH_HOURS = 6
BATCH_WINDOWS = [(0, 6), (6, 12), (12, 18), (18, 24)]
BATCH_LABELS = ["00:00-06:00", "06:00-12:00", "12:00-18:00", "18:00-24:00"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_utc() -> date:
    return now_utc().date()


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def days_in_month(d: date) -> int:
    return (add_months(month_start(d), 1) - timedelta(days=1)).day


def week_start(d: date) -> date:
    """Monday of the week containing `d`."""
    return d - timedelta(days=d.weekday())


def day_elapsed_fraction(d: date, now: datetime | None = None) -> float:
    """How much of `d` has actually happened, per the real clock: 0..1."""
    now = now or now_utc()
    today = now.date()
    if d < today:
        return 1.0
    if d > today:
        return 0.0
    return min(1.0, (now - datetime.combine(d, time(0), tzinfo=timezone.utc)).total_seconds() / 86400.0)


def batch_elapsed_fraction(d: date, batch_index: int, now: datetime | None = None) -> float:
    """Elapsed fraction of one 6-hour batch on day `d`: 0 = not started, 1 = complete."""
    now = now or now_utc()
    start_h, end_h = BATCH_WINDOWS[batch_index]
    start = datetime.combine(d, time(0), tzinfo=timezone.utc) + timedelta(hours=start_h)
    end = datetime.combine(d, time(0), tzinfo=timezone.utc) + timedelta(hours=end_h)
    if now >= end:
        return 1.0
    if now <= start:
        return 0.0
    return (now - start).total_seconds() / (BATCH_HOURS * 3600.0)


# --------------------------------------------------------------------------
# per-elevator usage profile
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class UsageProfile:
    """Stable characteristics of one elevator's duty, derived from its id."""
    elevator_id: str
    base_trips_per_day: int
    car_calls_per_trip: float
    door_cycles_per_trip: float
    rope_bends_per_trip: float
    powerups_per_month: int
    commissioned_days_ago: int


def profile_for(elevator: dict) -> UsageProfile:
    eid = elevator["_id"]
    r = _rng("profile", eid)
    floors = elevator.get("floors") or 10
    # A taller building means longer trips: more rope bending and more car calls
    # per trip, but slightly fewer trips because each one takes longer.
    base = int(r.uniform(180, 340) * (1.0 - min(0.25, floors / 100.0)))
    return UsageProfile(
        elevator_id=eid,
        base_trips_per_day=max(60, base),
        car_calls_per_trip=round(r.uniform(1.05, 1.45), 3),
        door_cycles_per_trip=2.0,                       # one open + one close per trip
        rope_bends_per_trip=round(2.0 + floors / 12.0, 3),
        powerups_per_month=r.randint(1, 4),
        commissioned_days_ago=r.randint(400, 2200),
    )


def _day_profile(elevator: dict, d: date) -> dict:
    """Deterministic raw counts for ONE calendar day, before clock-proration.

    The seed contains only the elevator id and the date, so this value is
    frozen for all time once that day is in the past.
    """
    p = profile_for(elevator)
    r = _rng("day", p.elevator_id, d.isoformat())

    # Weekday/weekend duty pattern: an office lift is much quieter at weekends.
    weekday = d.weekday()
    weekend_factor = 0.45 if weekday >= 5 else 1.0
    seasonal = 1.0 + 0.06 * ((d.month % 6) - 3) / 3.0      # mild annual ripple
    noise = r.uniform(0.88, 1.12)

    trips = int(round(p.base_trips_per_day * weekend_factor * seasonal * noise))
    door_movements = int(round(trips * p.door_cycles_per_trip * r.uniform(0.98, 1.06)))
    car_calls = int(round(trips * p.car_calls_per_trip * r.uniform(0.95, 1.08)))
    # Power-ups are rare and bursty: most days none at all.
    power_ups = 1 if r.random() < (p.powerups_per_month / 30.0) else 0
    if r.random() < 0.01:
        power_ups += r.randint(1, 2)                        # occasional supply event

    return {
        "date": d.isoformat(),
        "trips": trips,
        "door_movements": door_movements,
        "car_calls": car_calls,
        "power_ups": power_ups,
    }


def day_counts(elevator: dict, d: date, now: datetime | None = None) -> dict:
    """Counts for a day, prorated by the real clock if the day is today.

    Past days return their full frozen value; future days return zeros.
    """
    raw = _day_profile(elevator, d)
    frac = day_elapsed_fraction(d, now)
    out = {"date": raw["date"], "elapsed_fraction": round(frac, 4),
           "complete": frac >= 1.0, "future": frac <= 0.0}
    for k in ("trips", "door_movements", "car_calls", "power_ups"):
        out[k] = int(round(raw[k] * frac))
    return out


def batch_counts(elevator: dict, d: date, now: datetime | None = None) -> list[dict]:
    """The four 6-hour batches of day `d` (Section 1, batch-wise view).

    The day's total is split across the batches by a fixed intraday duty curve
    (mornings and evenings are busy, the small hours are nearly idle), then the
    batch that is currently running is prorated by the real clock. Batches that
    have not started yet are returned as `upcoming` with zero counts.
    """
    raw = _day_profile(elevator, d)
    # Fixed intraday shape - not random, so batch totals always sum to the day.
    shape = [0.06, 0.34, 0.33, 0.27]
    rows = []
    for i, (start_h, end_h) in enumerate(BATCH_WINDOWS):
        frac = batch_elapsed_fraction(d, i, now)
        row = {
            "index": i,
            "label": BATCH_LABELS[i],
            "start_hour": start_h,
            "end_hour": end_h,
            "elapsed_fraction": round(frac, 4),
            "state": "complete" if frac >= 1.0 else "running" if frac > 0 else "upcoming",
        }
        for k in ("trips", "door_movements", "car_calls", "power_ups"):
            row[k] = int(round(raw[k] * shape[i] * frac))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# degradation ramp - frozen per day so historical energy never moves
# --------------------------------------------------------------------------

def _degradation_penalty(elevator: dict, d: date) -> float:
    """Energy penalty (0..~0.3) from worn components on a given date.

    Deliberately derived from the date and the elevator seed rather than from
    the component's *current* health: if it read live health, yesterday's
    energy figure would change every time a component degraded, and the
    frozen-history rule in Section 1 would break. The ramp rises slowly with
    calendar time, which is what a real degrading fleet looks like.
    """
    p = profile_for(elevator)
    r = _rng("degrade", p.elevator_id)
    slope = r.uniform(0.010, 0.045)          # penalty added per year of service
    age_years = (p.commissioned_days_ago + (d - today_utc()).days) / 365.0
    return max(0.0, min(0.30, slope * age_years))


def day_energy(elevator: dict, d: date, tariff: float, now: datetime | None = None) -> dict:
    """Energy and cost for one day. Cost is ALWAYS kWh x tariff (Section 1.4)."""
    counts = day_counts(elevator, d, now)
    hours = 24.0 * counts["elapsed_fraction"]
    penalty = _degradation_penalty(elevator, d)
    motion = counts["trips"] * KWH_PER_TRIP
    standby = STANDBY_KW * hours
    kwh = standby + motion * (1.0 + penalty)
    baseline = standby + motion
    return {
        "date": counts["date"],
        "trips": counts["trips"],
        "energy_kwh": round(kwh, 3),
        "baseline_kwh": round(baseline, 3),
        "excess_kwh": round(kwh - baseline, 3),
        "cost_inr": round(kwh * tariff, 2),
        "complete": counts["complete"],
        "future": counts["future"],
    }


def month_energy(elevator: dict, month_first: date, tariff: float,
                 now: datetime | None = None) -> dict:
    """One calendar month. The current month is month-to-date only."""
    now = now or now_utc()
    today = now.date()
    n_days = days_in_month(month_first)
    last_day = min(n_days, today.day) if month_key(month_first) == month_key(today) else n_days
    is_current = month_key(month_first) == month_key(today)
    is_future = month_first > month_start(today)

    kwh = base = trips = 0.0
    if not is_future:
        for i in range(last_day):
            d = month_first + timedelta(days=i)
            e = day_energy(elevator, d, tariff, now)
            kwh += e["energy_kwh"]
            base += e["baseline_kwh"]
            trips += e["trips"]
    return {
        "month": month_key(month_first),
        "label": month_first.strftime("%b %Y"),
        "energy_kwh": round(kwh, 2),
        "baseline_kwh": round(base, 2),
        "excess_kwh": round(kwh - base, 2),
        "cost_inr": round(kwh * tariff, 2),
        "trips": int(trips),
        "days_counted": last_day if not is_future else 0,
        "days_in_month": n_days,
        "state": "month-to-date" if is_current else "upcoming" if is_future else "complete",
    }


def week_energy(elevator: dict, week_first: date, tariff: float,
                now: datetime | None = None) -> dict:
    """One ISO week (Monday-Sunday). The current week is week-to-date only."""
    now = now or now_utc()
    today = now.date()
    cur_week_first = week_start(today)
    is_current = week_first == cur_week_first
    is_future = week_first > cur_week_first
    last_day = (min(today, week_first + timedelta(days=6)) - week_first).days + 1 if is_current else 7

    kwh = base = trips = 0.0
    if not is_future:
        for i in range(last_day):
            d = week_first + timedelta(days=i)
            e = day_energy(elevator, d, tariff, now)
            kwh += e["energy_kwh"]
            base += e["baseline_kwh"]
            trips += e["trips"]
    week_last = week_first + timedelta(days=6)
    return {
        "week": week_first.isoformat(),
        "label": f"{week_first.strftime('%d %b')}-{week_last.strftime('%d %b %Y')}",
        "energy_kwh": round(kwh, 2),
        "baseline_kwh": round(base, 2),
        "excess_kwh": round(kwh - base, 2),
        "cost_inr": round(kwh * tariff, 2),
        "trips": int(trips),
        "days_counted": last_day if not is_future else 0,
        "days_in_week": 7,
        "state": "week-to-date" if is_current else "upcoming" if is_future else "complete",
    }


def year_energy(elevator: dict, year_first: date, tariff: float,
                now: datetime | None = None) -> dict:
    """One calendar year (Jan-Dec, summed from its 12 months). The current year is year-to-date only."""
    now = now or now_utc()
    today = now.date()
    is_current = year_first.year == today.year
    is_future = year_first.year > today.year
    last_month = today.month if is_current else 12

    kwh = base = trips = 0.0
    months_counted = 0
    if not is_future:
        for m in range(1, last_month + 1):
            mf = date(year_first.year, m, 1)
            r = month_energy(elevator, mf, tariff, now)
            kwh += r["energy_kwh"]
            base += r["baseline_kwh"]
            trips += r["trips"]
            months_counted += 1
    return {
        "year": year_first.year,
        "label": str(year_first.year),
        "energy_kwh": round(kwh, 2),
        "baseline_kwh": round(base, 2),
        "excess_kwh": round(kwh - base, 2),
        "cost_inr": round(kwh * tariff, 2),
        "trips": int(trips),
        "months_counted": months_counted if not is_future else 0,
        "months_in_year": 12,
        "state": "year-to-date" if is_current else "upcoming" if is_future else "complete",
    }


def energy_calendar(elevators: list[dict], tariff: float, view: str = "month",
                    now: datetime | None = None) -> dict:
    """Section 1: the week-wise, month-wise, year-wise and batch-wise energy views.

    week view  - the last 12 ISO weeks ending with the CURRENT week, which is
                 week-to-date and only counts up to today.
    month view - the last 12 months ending with the CURRENT month, which is
                 month-to-date and only counts up to today.
    year view  - the last 5 calendar years ending with the CURRENT year, which
                 is year-to-date and only counts up to the current month.
    batch view - the four 6-hour batches of TODAY. Batches that have not
                 started show as `upcoming`; only the running batch grows, and
                 only as the real clock advances.
    """
    now = now or now_utc()
    today = now.date()

    if view == "batch":
        rows = []
        for i in range(4):
            agg = {"index": i, "label": BATCH_LABELS[i], "energy_kwh": 0.0, "baseline_kwh": 0.0,
                   "trips": 0, "door_movements": 0, "car_calls": 0, "power_ups": 0}
            state = "upcoming"
            for e in elevators:
                b = batch_counts(e, today, now)[i]
                penalty = _degradation_penalty(e, today)
                hours = BATCH_HOURS * b["elapsed_fraction"]
                motion = b["trips"] * KWH_PER_TRIP
                standby = STANDBY_KW * hours
                agg["energy_kwh"] += standby + motion * (1.0 + penalty)
                agg["baseline_kwh"] += standby + motion
                for k in ("trips", "door_movements", "car_calls", "power_ups"):
                    agg[k] += b[k]
                if b["state"] != "upcoming":
                    state = b["state"]
                agg["elapsed_fraction"] = b["elapsed_fraction"]
            agg["state"] = state
            agg["energy_kwh"] = round(agg["energy_kwh"], 3)
            agg["baseline_kwh"] = round(agg["baseline_kwh"], 3)
            agg["excess_kwh"] = round(agg["energy_kwh"] - agg["baseline_kwh"], 3)
            agg["cost_inr"] = round(agg["energy_kwh"] * tariff, 2)
            rows.append(agg)
        period_label = today.strftime("%d %b %Y")
    elif view == "week":
        rows = []
        cur_week_first = week_start(today)
        for back in range(11, -1, -1):
            w = cur_week_first - timedelta(weeks=back)
            agg = {"week": w.isoformat(), "label": (w).strftime("%d %b"),
                   "energy_kwh": 0.0, "baseline_kwh": 0.0, "cost_inr": 0.0, "trips": 0}
            state = "complete"
            for e in elevators:
                r = week_energy(e, w, tariff, now)
                agg["energy_kwh"] += r["energy_kwh"]
                agg["baseline_kwh"] += r["baseline_kwh"]
                agg["trips"] += r["trips"]
                state = r["state"]
                agg["days_counted"] = r["days_counted"]
                agg["days_in_week"] = r["days_in_week"]
            agg["energy_kwh"] = round(agg["energy_kwh"], 2)
            agg["baseline_kwh"] = round(agg["baseline_kwh"], 2)
            agg["excess_kwh"] = round(agg["energy_kwh"] - agg["baseline_kwh"], 2)
            agg["cost_inr"] = round(agg["energy_kwh"] * tariff, 2)
            agg["state"] = state
            rows.append(agg)
        period_label = f"12 weeks to {cur_week_first.strftime('%d %b %Y')}"
    elif view == "year":
        rows = []
        for back in range(4, -1, -1):
            y = today.year - back
            yf = date(y, 1, 1)
            agg = {"year": y, "label": str(y),
                   "energy_kwh": 0.0, "baseline_kwh": 0.0, "cost_inr": 0.0, "trips": 0}
            state = "complete"
            for e in elevators:
                r = year_energy(e, yf, tariff, now)
                agg["energy_kwh"] += r["energy_kwh"]
                agg["baseline_kwh"] += r["baseline_kwh"]
                agg["trips"] += r["trips"]
                state = r["state"]
                agg["months_counted"] = r["months_counted"]
                agg["months_in_year"] = r["months_in_year"]
            agg["energy_kwh"] = round(agg["energy_kwh"], 2)
            agg["baseline_kwh"] = round(agg["baseline_kwh"], 2)
            agg["excess_kwh"] = round(agg["energy_kwh"] - agg["baseline_kwh"], 2)
            agg["cost_inr"] = round(agg["energy_kwh"] * tariff, 2)
            agg["state"] = state
            rows.append(agg)
        period_label = f"5 years to {today.year}"
    else:
        rows = []
        first_of_this_month = month_start(today)
        for back in range(11, -1, -1):
            m = add_months(first_of_this_month, -back)
            agg = {"month": month_key(m), "label": m.strftime("%b %Y"),
                   "energy_kwh": 0.0, "baseline_kwh": 0.0, "cost_inr": 0.0, "trips": 0}
            state = "complete"
            for e in elevators:
                r = month_energy(e, m, tariff, now)
                agg["energy_kwh"] += r["energy_kwh"]
                agg["baseline_kwh"] += r["baseline_kwh"]
                agg["trips"] += r["trips"]
                state = r["state"]
                agg["days_counted"] = r["days_counted"]
                agg["days_in_month"] = r["days_in_month"]
            agg["energy_kwh"] = round(agg["energy_kwh"], 2)
            agg["baseline_kwh"] = round(agg["baseline_kwh"], 2)
            agg["excess_kwh"] = round(agg["energy_kwh"] - agg["baseline_kwh"], 2)
            agg["cost_inr"] = round(agg["energy_kwh"] * tariff, 2)
            agg["state"] = state
            rows.append(agg)
        period_label = f"12 months to {today.strftime('%b %Y')}"

    total_kwh = sum(r["energy_kwh"] for r in rows)
    total_excess = sum(r["excess_kwh"] for r in rows)
    return {
        "view": view,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "period_label": period_label,
        "tariff_inr_per_kwh": tariff,
        "rows": rows,
        "totals": {
            "energy_kwh": round(total_kwh, 2),
            "cost_inr": round(total_kwh * tariff, 2),
            "excess_kwh": round(total_excess, 2),
            "excess_cost_inr": round(total_excess * tariff, 2),
            "trips": sum(r["trips"] for r in rows),
        },
        "assumptions": [
            f"Cost = kWh x Rs {tariff:g} per unit, always derived from the stored kWh.",
            "Values are generated from a fixed seed per elevator and calendar period: "
            "the same period always returns the same number, on every click and after a refresh.",
            "Completed weeks, months, years and batches are frozen. Only the current "
            "to-date period and the running 6-hour batch grow, and only as the real clock advances.",
            "Energy = standby 0.12 kW x hours + trips x 0.035 kWh x (1 + degradation penalty).",
        ],
    }


# --------------------------------------------------------------------------
# error codes (Section 3.5)
# --------------------------------------------------------------------------

ERROR_CATALOG = [
    ("E-1042", "Door obstruction detected during closing", "door", "warning"),
    ("E-1105", "Door re-open count above threshold", "door", "info"),
    ("E-2203", "Drive over-current during acceleration", "traction", "critical"),
    ("E-2210", "Motor winding temperature high", "traction", "warning"),
    ("E-3011", "Rope tension imbalance outside band", "rope", "critical"),
    ("E-3044", "Sheave groove wear sensor out of range", "sheave", "warning"),
    ("E-4008", "Bearing vibration above alarm limit", "bearing", "critical"),
    ("E-4012", "Bearing lubrication interval exceeded", "bearing", "info"),
    ("E-5001", "Controller watchdog reset", "controller", "warning"),
    ("E-5107", "Position encoder drift corrected", "controller", "info"),
    ("E-6002", "Overload sensor triggered", "car", "warning"),
]

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def error_events(elevator: dict, days: int = 30, now: datetime | None = None) -> list[dict]:
    """Deterministic error log for the last `days` days, newest first.

    Error frequency rises with the elevator's degradation penalty, which is
    how a real fleet behaves and what makes the "high errors -> higher risk"
    link in Section 3 meaningful rather than decorative.
    """
    now = now or now_utc()
    today = now.date()
    rows = []
    for back in range(days):
        d = today - timedelta(days=back)
        r = _rng("errors", elevator["_id"], d.isoformat())
        penalty = _degradation_penalty(elevator, d)
        # ~0.15 events/day healthy, rising to ~1/day on a badly degraded unit.
        expected = 0.15 + penalty * 3.0
        n = 0
        while r.random() < min(0.85, expected / (n + 1)):
            n += 1
            if n >= 5:
                break
        for i in range(n):
            code, desc, subsystem, severity = r.choice(ERROR_CATALOG)
            ts = datetime.combine(d, time(0), tzinfo=timezone.utc) + timedelta(
                seconds=r.randint(0, 86399))
            if ts > now:
                continue
            rows.append({
                "id": f"{elevator['_id']}-{d.isoformat()}-{i}",
                "code": code,
                "description": desc,
                "subsystem": subsystem,
                "severity": severity,
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                # Older events are progressively more likely to be closed out.
                "resolved": back > 2 or r.random() < 0.4,
            })
    rows.sort(key=lambda x: x["timestamp"], reverse=True)
    return rows


# --------------------------------------------------------------------------
# usage cycles (Section 4)
# --------------------------------------------------------------------------

# One definition per component type, shown to the user as a "how calculated"
# tooltip. Every definition is expressed in terms of the LIFT's trips or door
# movements, which is what keeps component cycles consistent with lift usage
# (Section 4, consistency rule) - a component can never out-cycle its lift.
CYCLE_DEFINITION = {
    "rope": {"basis": "trips", "label": "Rope cycles",
             "formula": "trips x bends per trip (one bend per sheave pass)",
             "rated": 1_000_000},
    "sheave": {"basis": "trips", "label": "Sheave groove passes",
               "formula": "trips x bends per trip (same rope passes, seen from the sheave)",
               "rated": 1_200_000},
    "bearing": {"basis": "trips", "label": "Bearing load cycles",
                "formula": "trips x 1 start-stop load reversal per trip",
                "rated": 2_000_000},
    "traction": {"basis": "trips", "label": "Motor starts",
                 "formula": "one start per trip",
                 "rated": 1_500_000},
    "door": {"basis": "door_movements", "label": "Door open/close cycles",
             "formula": "door open + close movements",
             "rated": 3_000_000},
}


def _cycles_from_day(elevator: dict, day: dict, component_type: str) -> int:
    """Convert one day's lift counts into one component's cycle count."""
    p = profile_for(elevator)
    if component_type == "door":
        return day["door_movements"]
    if component_type in ("rope", "sheave"):
        return int(round(day["trips"] * p.rope_bends_per_trip))
    # bearing and traction: one cycle per trip
    return day["trips"]


def cycle_series(elevator: dict, component_type: str, view: str = "week",
                 now: datetime | None = None) -> dict:
    """Cycle counts bucketed for the week / month / year toggle (Section 4).

    week  - one bucket per day of the current week (Monday to today)
    month - one bucket per week of the current month, plus the month total
    year  - one bucket per month of the current year, plus the year total
    """
    now = now or now_utc()
    today = now.date()
    buckets = []

    if view == "week":
        start = week_start(today)
        for i in range(7):
            d = start + timedelta(days=i)
            c = day_counts(elevator, d, now)
            buckets.append({
                "label": d.strftime("%a"),
                "sub": d.strftime("%d %b"),
                "key": d.isoformat(),
                "cycles": _cycles_from_day(elevator, c, component_type),
                "state": "upcoming" if c["future"] else "running" if not c["complete"] else "complete",
            })
        period_label = f"Week of {start.strftime('%d %b %Y')}"

    elif view == "month":
        first = month_start(today)
        n = days_in_month(first)
        wk = 1
        i = 0
        while i < n:
            total = 0
            state = "complete"
            days_in_bucket = 0
            first_day = first + timedelta(days=i)
            for j in range(7):
                if i + j >= n:
                    break
                d = first + timedelta(days=i + j)
                c = day_counts(elevator, d, now)
                total += _cycles_from_day(elevator, c, component_type)
                days_in_bucket += 1
                if c["future"]:
                    state = "upcoming" if state == "upcoming" else state
                elif not c["complete"]:
                    state = "running"
            last_day = first + timedelta(days=min(i + days_in_bucket - 1, n - 1))
            buckets.append({
                "label": f"Week {wk}",
                "sub": f"{first_day.strftime('%d')}-{last_day.strftime('%d %b')}",
                "key": f"{month_key(first)}-w{wk}",
                "cycles": total,
                "state": state,
            })
            wk += 1
            i += 7
        period_label = first.strftime("%B %Y")

    else:  # year
        for m in range(1, 13):
            first = date(today.year, m, 1)
            total = 0
            state = "complete"
            if first > month_start(today):
                state = "upcoming"
            else:
                n = days_in_month(first)
                last = min(n, today.day) if m == today.month else n
                for i in range(last):
                    d = first + timedelta(days=i)
                    total += _cycles_from_day(elevator, day_counts(elevator, d, now), component_type)
                if m == today.month:
                    state = "running"
            buckets.append({
                "label": first.strftime("%b"),
                "sub": str(today.year),
                "key": month_key(first),
                "cycles": total,
                "state": state,
            })
        period_label = str(today.year)

    definition = CYCLE_DEFINITION[component_type]
    return {
        "view": view,
        "component_type": component_type,
        "period_label": period_label,
        "buckets": buckets,
        "total": sum(b["cycles"] for b in buckets),
        "definition": definition,
    }


def _avg_cycles_per_day(elevator: dict, component_type: str, now: datetime) -> float:
    """Average daily cycles over the last 28 complete days.

    Sampling 28 days rather than summing the whole service life keeps this
    fast, and the average is exactly what the daily generator converges to.
    """
    total = 0
    for back in range(1, 29):
        d = now.date() - timedelta(days=back)
        total += _cycles_from_day(elevator, _day_profile(elevator, d), component_type)
    return total / 28.0


def _effective_service_days(elevator: dict, now: datetime) -> int:
    """In-service days, capped so no component's cycle usage is physically absurd.

    The raw commissioning age is sampled independently of duty, so a
    high-traffic lift could otherwise show a rope at 140% of its rated bending
    cycles - a number that cannot exist, because the rope would have been
    replaced. The cap scales the age down uniformly across component types
    (so they stay consistent with each other) until the tightest-constrained
    type sits at the elevator's seeded target usage.

    The target is seeded between 55% and 98%, which deliberately leaves room
    for a lift to sit beyond the 95% rated-lifecycle threshold - that is what
    makes the "REPLACE WHOLE UNIT" rule reachable in a demo.
    """
    p = profile_for(elevator)
    target = _rng("usage-target", p.elevator_id).uniform(0.55, 0.98)
    caps = [
        CYCLE_DEFINITION[t]["rated"] * target / max(_avg_cycles_per_day(elevator, t, now), 1.0)
        for t in CYCLE_DEFINITION
    ]
    return int(min(p.commissioned_days_ago, min(caps)))


def lifetime_cycles(elevator: dict, component_type: str, now: datetime | None = None) -> dict:
    """Lifetime cycles used vs the rated limit, plus a cycles-based life estimate."""
    now = now or now_utc()
    definition = CYCLE_DEFINITION[component_type]
    per_day = _avg_cycles_per_day(elevator, component_type, now)
    service_days = _effective_service_days(elevator, now)

    used = int(per_day * service_days)
    rated = definition["rated"]
    remaining = max(0, rated - used)
    remaining_days = int(remaining / max(per_day, 1.0))
    return {
        "component_type": component_type,
        "label": definition["label"],
        "formula": definition["formula"],
        "cycles_used": used,
        "rated_cycles": rated,
        "used_pct": round(100.0 * used / rated, 1),
        "avg_cycles_per_day": round(per_day, 1),
        "remaining_cycles": remaining,
        "cycles_based_remaining_days": remaining_days,
        "in_service_days": service_days,
    }


def elevator_age_and_usage(elevator: dict, now: datetime | None = None) -> dict:
    """Centralized lifetime, age, and usage summary for an elevator (Section 11 & 12).
    
    Derives deterministic age and duty figures from the elevator's profile and document.
    """
    now = now or now_utc()
    p = profile_for(elevator)
    
    install_date_raw = elevator.get("installation_date") or elevator.get("commissioned_at")
    if install_date_raw:
        if isinstance(install_date_raw, str):
            try:
                install_d = date.fromisoformat(install_date_raw[:10])
            except Exception:
                install_d = now.date() - timedelta(days=p.commissioned_days_ago)
        elif isinstance(install_date_raw, datetime):
            install_d = install_date_raw.date()
        elif isinstance(install_date_raw, date):
            install_d = install_date_raw
        else:
            install_d = now.date() - timedelta(days=p.commissioned_days_ago)
        days_ago = max(1, (now.date() - install_d).days)
        install_date_str = install_d.isoformat()
    else:
        days_ago = p.commissioned_days_ago
        install_d = now.date() - timedelta(days=days_ago)
        install_date_str = install_d.isoformat()
        
    age_years = round(days_ago / 365.25, 1)
    total_trips = int(p.base_trips_per_day * days_ago)
    door_cycles = int(total_trips * p.door_cycles_per_trip)
    rope_cycles = int(total_trips * p.rope_bends_per_trip)
    
    return {
        "installation_date": install_date_str,
        "age_days": days_ago,
        "age_years": age_years,
        "age_label": f"{age_years} years ({days_ago:,} days)",
        "usage_summary": {
            "daily_trips_avg": p.base_trips_per_day,
            "total_trips": total_trips,
            "total_rope_cycles": rope_cycles,
            "total_door_cycles": door_cycles,
            "total_motor_starts": total_trips,
            "car_calls_per_trip": p.car_calls_per_trip,
        },
    }


def cycle_totals(elevator: dict, component_type: str, now: datetime | None = None) -> dict:
    """Week / month / year totals for one component - used by the comparison table."""
    return {
        "week": cycle_series(elevator, component_type, "week", now)["total"],
        "month": cycle_series(elevator, component_type, "month", now)["total"],
        "year": cycle_series(elevator, component_type, "year", now)["total"],
    }


def monthly_table(elevator: dict, component_type: str, now: datetime | None = None) -> list[dict]:
    """Jan-Dec counts for the exportable month-wise table (Section 4)."""
    series = cycle_series(elevator, component_type, "year", now)
    return [{"month": b["label"], "cycles": b["cycles"], "state": b["state"]} for b in series["buckets"]]


# --------------------------------------------------------------------------
# operational summary (Section 3)
# --------------------------------------------------------------------------

CONNECTED_SYSTEMS = ["rope", "sheave", "bearing", "traction", "door", "controller"]


def operational_summary(elevator: dict, days: int = 30, now: datetime | None = None) -> dict:
    """The seven KONE operational inputs for one elevator, with trends.

    Returns summary totals, a per-day trend series and a table for each, so the
    Operational Data tab can render a card + chart + table per item without
    doing any arithmetic of its own.
    """
    now = now or now_utc()
    today = now.date()
    trend = []
    for back in range(days - 1, -1, -1):
        d = today - timedelta(days=back)
        trend.append(day_counts(elevator, d, now))

    totals = {k: sum(t[k] for t in trend) for k in ("trips", "door_movements", "car_calls", "power_ups")}
    errors = error_events(elevator, days, now)
    by_severity = {s: sum(1 for e in errors if e["severity"] == s) for s in SEVERITY_RANK}
    unresolved = sum(1 for e in errors if not e["resolved"])

    penalty = _degradation_penalty(elevator, today)
    systems = []
    for sysname in CONNECTED_SYSTEMS:
        r = _rng("system", elevator["_id"], sysname, month_key(today))
        sys_errors = sum(1 for e in errors if e["subsystem"] == sysname)
        # Performance index: 100 = as-new; falls with degradation and error load.
        index = 100.0 - penalty * 100.0 * r.uniform(0.6, 1.4) - sys_errors * 1.5
        systems.append({
            "system": sysname,
            "performance_index": round(max(35.0, min(100.0, index)), 1),
            "errors_30d": sys_errors,
            "availability_pct": round(max(95.0, 100.0 - sys_errors * 0.15), 2),
        })

    return {
        "elevator_id": elevator["_id"],
        "window_days": days,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "totals": totals,
        "trend": trend,
        "errors": errors,
        "error_counts": {**by_severity, "total": len(errors), "unresolved": unresolved},
        "connected_systems": systems,
        "definitions": {
            "trips": "Number of times the lift completed a journey between floors.",
            "door_movements": "Count of door open/close cycles.",
            "car_calls": "Usage requests registered from the car operating panel.",
            "power_ups": "Times the controller restarted or the system powered up.",
        },
    }


def operational_risk_factors(elevator: dict, now: datetime | None = None) -> list[dict]:
    """Operational inputs expressed as risk drivers (Section 3, final requirement).

    Each factor carries a 0..1 `load` and a weight, so the caller can both
    adjust a risk score and show the user exactly which operational input
    pushed it up ("why is this component at risk?").
    """
    summary = operational_summary(elevator, 30, now)
    t = summary["totals"]
    days = summary["window_days"]
    p = profile_for(elevator)

    door_per_day = t["door_movements"] / days
    door_expected = p.base_trips_per_day * p.door_cycles_per_trip
    errors_30d = summary["error_counts"]["total"]
    critical_errors = summary["error_counts"]["critical"]

    def _factor(key, label, value, display, load, weight):
        return {"key": key, "label": label, "value": value, "display_value": display,
                "load": round(max(0.0, min(1.0, load)), 3), "weight": weight}

    return [
        _factor("door_cycles", "Door cycle load", round(door_per_day),
                f"{round(door_per_day):,}/day vs {round(door_expected):,} expected",
                (door_per_day / max(door_expected, 1) - 0.9) / 0.4, 0.30),
        _factor("error_rate", "Error codes (30 d)", errors_30d,
                f"{errors_30d} errors, {critical_errors} critical",
                errors_30d / 30.0, 0.40),
        _factor("critical_errors", "Critical faults (30 d)", critical_errors,
                f"{critical_errors} critical fault{'s' if critical_errors != 1 else ''}",
                critical_errors / 6.0, 0.20),
        _factor("power_ups", "Unplanned power-ups (30 d)", t["power_ups"],
                f"{t['power_ups']} restart{'s' if t['power_ups'] != 1 else ''}",
                t["power_ups"] / 6.0, 0.10),
    ]


def operational_risk_uplift(elevator: dict, now: datetime | None = None) -> dict:
    """Combine the operational factors into a bounded uplift on failure probability.

    Bounded at +12 percentage points so operational noise can nudge, but never
    dominate, the physics + ML prediction - the model stays the primary signal.
    """
    factors = operational_risk_factors(elevator, now)
    score = sum(f["load"] * f["weight"] for f in factors)
    uplift = round(12.0 * min(1.0, score), 2)
    drivers = sorted([f for f in factors if f["load"] > 0.25], key=lambda f: -f["load"] * f["weight"])
    return {"uplift_pct": uplift, "score": round(score, 3), "factors": factors, "drivers": drivers}
