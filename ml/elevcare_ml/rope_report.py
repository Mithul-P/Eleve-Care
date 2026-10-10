"""
Suspension-rope fault analysis (spec section 3), pure numpy so it is testable
without a database. The backend feeds it the stored per-rope tensions.

Per rope:  24 h median tension, deviation from the set average, 7- and 30-day
           trends of that deviation (%/day), status Healthy / Watch / Faulty.
Set level: which ropes are faulty, the Feyrer wear multiplier of the worst rope
           (1 + dev)^b, and the days until the worst rope reaches the fault
           limit at the current trend.
Thresholds come from physics.THRESHOLDS["rope"]["rope_deviation_pct"].
"""

from __future__ import annotations

import numpy as np

from . import physics as P


def _slope(t: np.ndarray, y: np.ndarray) -> float:
    if len(t) < 3 or (t.max() - t.min()) < 0.75:
        return 0.0
    tc = t - t.mean()
    d = float(np.sum(tc * tc))
    return float(np.sum(tc * (y - y.mean())) / d) if d > 0 else 0.0


def analyse_ropes(times_days: np.ndarray, tensions: np.ndarray) -> dict:
    """
    times_days (k,) ascending; tensions (k, n_ropes) in kN.
    Returns {"ropes": [...], "faulty": [ids], "verdict", "worst": {...}, "limits": {...}}.
    """
    t = np.asarray(times_days, float)
    x = np.atleast_2d(np.asarray(tensions, float))
    th = P.THRESHOLDS["rope"]["rope_deviation_pct"]
    watch, fault = th.healthy, th.alarm
    now = t[-1]
    set_mean = x.mean(axis=1)                                   # (k,)
    dev = (x - set_mean[:, None]) / np.maximum(set_mean[:, None], 1e-6) * 100.0   # (k, n)
    m24 = t >= now - 1.0
    m7 = t >= now - 7.0
    ropes = []
    for j in range(x.shape[1]):
        med = float(np.median(x[m24, j]))
        d_now = float(np.median(dev[m24, j]))
        tr7 = _slope(t[m7], dev[m7, j])
        tr30 = _slope(t, dev[:, j])
        last = dev[-P.ROPE_FAULT_PERSISTENCE:, j]
        persistent = len(last) >= P.ROPE_FAULT_PERSISTENCE and bool(np.all(last >= fault))
        # Faulty = OVER-loaded (it is the rope that tires first). A rope far BELOW average is slack, not overloaded:
        # it is reported as "watch" because it shifts load onto its neighbours.
        if d_now >= fault or persistent:
            status = "faulty"
        elif abs(d_now) >= watch or (tr7 > 0.05 and d_now > 0):
            status = "watch"
        else:
            status = "healthy"
        ropes.append({"id": f"R{j + 1}", "index": j, "median_tension_kn": round(med, 3),
                      "deviation_pct": round(d_now, 2), "trend_7d_pct_per_day": round(tr7, 4),
                      "trend_30d_pct_per_day": round(tr30, 4), "status": status, "persistent_over_limit": persistent})
    faulty = [r["id"] for r in ropes if r["status"] == "faulty"]
    worst = max(ropes, key=lambda r: r["deviation_pct"])
    over = worst["deviation_pct"] / 100.0
    wear_multiplier = float((1.0 + max(0.0, over)) ** P.ROPE_FEYRER_B)
    trend = worst["trend_7d_pct_per_day"] if worst["trend_7d_pct_per_day"] > 1e-4 else worst["trend_30d_pct_per_day"]
    if worst["deviation_pct"] >= fault:
        days_to_limit = 0.0
    elif trend > 1e-4:
        days_to_limit = (fault - worst["deviation_pct"]) / trend
    else:
        days_to_limit = None
    return {
        "ropes": ropes, "faulty": faulty, "verdict": "fault" if faulty else "no fault",
        "set_average_kn": round(float(np.median(set_mean[m24])), 3),
        "imbalance_pct": round(float(np.max(np.abs(dev[-1]))), 2),
        "worst": {"id": worst["id"], "deviation_pct": worst["deviation_pct"], "wear_multiplier": round(wear_multiplier, 2),
                  "trend_pct_per_day": round(trend, 4), "days_to_limit": None if days_to_limit is None else round(days_to_limit, 1)},
        "limits": {"watch_pct": watch, "fault_pct": fault, "persistence_readings": P.ROPE_FAULT_PERSISTENCE, "feyrer_b": P.ROPE_FEYRER_B},
    }


def daily_series(times_days: np.ndarray, tensions: np.ndarray, days: int = 30) -> list[dict]:
    """Per-day median tension per rope for the sparkline grid, oldest first."""
    t = np.asarray(times_days, float)
    x = np.atleast_2d(np.asarray(tensions, float))
    now = t[-1]
    out = []
    for d in range(days - 1, -1, -1):
        m = (t >= now - d - 1) & (t < now - d + 1e-9) if d > 0 else (t >= now - 1)
        if not m.any():
            continue
        out.append({"day": -d, "tensions": [round(float(np.median(x[m, j])), 3) for j in range(x.shape[1])]})
    return out
