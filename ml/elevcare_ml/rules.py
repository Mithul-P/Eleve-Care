"""
Rule-based safety layer - applied *after* the ML model and the physics
severity score, *before* anything is shown to a user.

Why a rule layer on top of a learned model
------------------------------------------
* A gradient-boosted model can only be as good as the failures it has seen.
  An engineering limit (rope tension spread > 10 %, kurtosis > 6.5, winding
  temperature rise > 28 C, 100 % of rated life used) is a hard fact that
  must escalate a component whether or not the model agrees.
* The opposite is just as important: a single noisy reading, or a model that
  is confident without any physical indicator backing it, must NOT turn a
  small thing into a big alarm. Hysteresis and confirmation rules keep the
  status stable and trustworthy.

Two families of rules therefore run in order:

  ESCALATION  (small issue -> big problem prevention)
    R1 alarm-limit     an indicator past its alarm limit => at least "medium";
                       a primary indicator past alarm + a second indicator or
                       an active growth trend => "at-risk"
    R2 life-consumed   >= 100 % of rated life => at-risk; >= 85 % => medium
    R3 active-growth   a growth trend past its alarm rate while the level is
                       already half-way to alarm => escalate one step
    R4 fast-response   an indicator past alarm on each of the last 3 readings
                       => escalate now, without waiting for the 24 h median
    R5 consistency     RUL and health are capped so they can never contradict
                       the status (an at-risk part cannot show 400 days RUL)

  DE-ESCALATION  (big alarm from a small issue prevention)
    R6 unconfirmed-ml  ML says at-risk but no indicator is even half-way to
                       alarm and < 70 % life used => hold at medium, flag for
                       inspection ("model-only signal")
    R7 hysteresis      status may only *improve* after the better status has
                       been observed on 3 consecutive evaluations, and never
                       while an indicator that caused the escalation is still
                       inside the 15 % dead-band below its alarm limit
    R8 low-confidence  fewer than 4 readings in the window => confidence low
                       (never suppresses an escalation)

Every fired rule is returned with a human-readable message so the UI and the
assistant can say exactly why the status is what it is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import physics as P

STATUS_ORDER = ["good", "medium", "at-risk"]
RANK = {s: i for i, s in enumerate(STATUS_ORDER)}

HYSTERESIS_EVALUATIONS = 3      # consecutive better evaluations before improving status
HYSTERESIS_DEADBAND = 0.85      # severity below which an alarm is considered cleared (15 % of the span)
MIN_READINGS_FOR_CONFIDENCE = 4
RUL_CAP_DAYS = {"at-risk": 45.0, "medium": 180.0}
HEALTH_CAP = {"at-risk": 44.0, "medium": 71.0}
LIFE_AT_RISK = 1.0
LIFE_MEDIUM = 0.85

# The one or two indicators that define each component's failure mode. A
# primary indicator past alarm carries more weight than a secondary one.
PRIMARY_INDICATORS = {
    "rope": ("tension_imbalance_pct", "fatigue_index"),
    "sheave": ("vib_rms", "fault_band_ratio"),
    "bearing": ("vib_kurtosis", "temperature_rise"),
    "traction": ("motor_current_ratio", "temperature_rise"),
}
TREND_INDICATORS = ("rms_trend", "imbalance_trend")


@dataclass
class Rule:
    id: str
    name: str
    effect: str      # escalate | hold | cap | flag
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


RULE_CATALOG = [
    {"id": "R1", "name": "Alarm-limit override", "family": "escalation",
     "text": "An indicator past its alarm limit is at least 'medium'; a primary indicator past alarm plus a "
             "second indicator or an active growth trend is 'at-risk', whatever the ML model says."},
    {"id": "R2", "name": "Rated life consumed", "family": "escalation",
     "text": ">= 100 % of rated life (Feyrer / Archard / L10 / Miner) is 'at-risk'; >= 85 % is 'medium'."},
    {"id": "R3", "name": "Active growth", "family": "escalation",
     "text": "A growth trend above its alarm rate while the level is already half-way to alarm escalates one step: "
             "intervene while the issue is small."},
    {"id": "R4", "name": "Fast response", "family": "escalation",
     "text": "An indicator above alarm on each of the last 3 readings escalates immediately, without waiting for "
             "the 24 h median to catch up."},
    {"id": "R5", "name": "Score consistency", "family": "escalation",
     "text": "RUL and health are capped so they never contradict the status; physics remaining life caps the ML RUL "
             "once more than half of the rated life is used."},
    {"id": "R6", "name": "Unconfirmed ML signal", "family": "de-escalation",
     "text": "ML 'at-risk' with no indicator half-way to alarm and < 70 % life used is held at 'medium' and flagged "
             "for inspection: the model alone cannot raise a critical alarm."},
    {"id": "R7", "name": "Hysteresis", "family": "de-escalation",
     "text": "Status only improves after 3 consecutive better evaluations, so a component cannot flap at a boundary."},
    {"id": "R8", "name": "Low confidence", "family": "de-escalation",
     "text": "Fewer than 4 readings in the window marks the prediction low-confidence (never blocks an escalation)."},
]


def _worse(a: str, b: str) -> str:
    return a if RANK[a] >= RANK[b] else b


def _step_up(s: str) -> str:
    return STATUS_ORDER[min(len(STATUS_ORDER) - 1, RANK[s] + 1)]


def apply_rules(ctype: str, feats: dict[str, float], sev: dict[str, float], ml_status: str,
                p_fail: float, rul_days: float, health: float, context: dict | None = None) -> dict:
    """
    Combine the ML/physics verdict with the engineering rules.

    context (all optional):
        prev_status        status currently shown for the component
        status_streak      how many consecutive evaluations produced `candidate_status`
        candidate_status   the status those evaluations proposed
        readings           number of readings in the feature window
        persistent         dict feature -> min value over the last 3 readings
        physics_rul_days   (1 - life_fraction) / life consumption rate, if known
    """
    ctx = context or {}
    th = P.THRESHOLDS[ctype]
    labels = {f: t.label for f, t in th.items()}
    fired: list[Rule] = []
    status = ml_status
    life = float(feats.get("fatigue_index", 0.0))

    over = [f for f in th if f not in TREND_INDICATORS and sev.get(f, 0.0) >= 1.0]
    half = [f for f in th if f not in TREND_INDICATORS and sev.get(f, 0.0) >= 0.5]
    trending = [f for f in TREND_INDICATORS if f in th and sev.get(f, 0.0) >= 1.0]
    primary_over = [f for f in PRIMARY_INDICATORS[ctype] if f in over]

    # ---- escalation -------------------------------------------------------
    if over:
        target = "medium"
        why = ", ".join(labels[f] for f in over)
        if primary_over and (len(over) >= 2 or trending):
            target = "at-risk"
            why += " with " + ("a second indicator past alarm" if len(over) >= 2 else "an active growth trend")
        if RANK[target] > RANK[status]:
            fired.append(Rule("R1", "Alarm-limit override", "escalate",
                              f"{why} past the alarm limit: status raised to {target}."))
            status = target

    if life >= LIFE_AT_RISK and RANK["at-risk"] > RANK[status]:
        fired.append(Rule("R2", "Rated life consumed", "escalate",
                          f"{life:.0%} of rated life used ({P.PHYSICS_MODEL[ctype]['name']}): status raised to at-risk."))
        status = "at-risk"
    elif LIFE_MEDIUM <= life < LIFE_AT_RISK and RANK["medium"] > RANK[status]:
        fired.append(Rule("R2", "Rated life consumed", "escalate",
                          f"{life:.0%} of rated life used: status raised to medium - plan replacement."))
        status = "medium"

    if trending and half and status != "at-risk":
        new = _step_up(status)
        fired.append(Rule("R3", "Active growth", "escalate",
                          f"{', '.join(labels[f] for f in trending)} above alarm rate while "
                          f"{labels[half[0]]} is already half-way to its limit: raised to {new} for early intervention."))
        status = new

    persistent = ctx.get("persistent") or {}
    fast = [f for f in th if f not in TREND_INDICATORS and f in persistent
            and P.severity(ctype, f, persistent[f]) >= 1.0 and sev.get(f, 0.0) < 1.0]
    if fast and status != "at-risk":
        new = _step_up(status)
        fired.append(Rule("R4", "Fast response", "escalate",
                          f"{', '.join(labels[f] for f in fast)} above alarm on each of the last 3 readings: raised to "
                          f"{new} ahead of the 24 h average."))
        status = new

    # ---- de-escalation ----------------------------------------------------
    confidence = "high"
    if ml_status == "at-risk" and status == "at-risk" and not half and not trending and life < 0.7 and not fast:
        fired.append(Rule("R6", "Unconfirmed ML signal", "hold",
                          f"Model reports {p_fail * 100:.0f}% failure probability but no indicator is half-way to "
                          "alarm - held at medium; confirm with an inspection."))
        status = "medium"
        confidence = "medium"

    prev = ctx.get("prev_status")
    streak = int(ctx.get("status_streak") or 0)
    candidate = ctx.get("candidate_status")
    if prev in RANK and RANK[status] < RANK[prev]:
        band = [f for f in th if f not in TREND_INDICATORS and sev.get(f, 0.0) >= HYSTERESIS_DEADBAND]
        held_by_band = band if prev == "medium" else [f for f in band if f in PRIMARY_INDICATORS[ctype]]
        if held_by_band:
            fired.append(Rule("R7", "Hysteresis", "hold",
                              f"{', '.join(labels[f] for f in held_by_band)} still within 15% of the alarm limit - "
                              f"holding {prev} until it clears the dead-band."))
            candidate, status, streak = status, prev, 0
        else:
            streak = streak + 1 if candidate == status else 1
        if streak and streak < HYSTERESIS_EVALUATIONS:
            fired.append(Rule("R7", "Hysteresis", "hold",
                              f"Improvement to {status} seen {streak}/{HYSTERESIS_EVALUATIONS} times - "
                              f"holding {prev} until it is confirmed."))
            candidate, status = status, prev
        elif streak:
            candidate = status
    else:
        streak = 0
        candidate = status

    readings = ctx.get("readings")
    if readings is not None and readings < MIN_READINGS_FOR_CONFIDENCE:
        fired.append(Rule("R8", "Low confidence", "flag",
                          f"Only {readings} reading(s) in the window - treat this prediction as provisional."))
        confidence = "low"

    # ---- consistency caps -------------------------------------------------
    rul = float(rul_days)
    phys_rul = ctx.get("physics_rul_days")
    if phys_rul is not None and life >= 0.5 and phys_rul < rul:
        fired.append(Rule("R5", "Score consistency", "cap",
                          f"ML remaining life {rul:.0f} d capped to physics remaining life {phys_rul:.0f} d "
                          f"({P.PHYSICS_MODEL[ctype]['label'].lower()} {life:.0%})."))
        rul = float(phys_rul)
    cap = RUL_CAP_DAYS.get(status)
    if cap is not None and rul > cap:
        fired.append(Rule("R5", "Score consistency", "cap",
                          f"Remaining life capped to {cap:.0f} d to stay consistent with status {status}."))
        rul = cap
    h = float(health)
    hcap = HEALTH_CAP.get(status)
    if hcap is not None and h > hcap:
        h = hcap

    return {
        "status": status,
        "rul_days": rul,
        "health_score": h,
        "confidence": confidence,
        "status_source": "rules" if status != ml_status else "model",
        "rules_fired": [r.as_dict() for r in fired],
        "candidate_status": candidate,
        "status_streak": streak,
    }
