"""
Inference: turn a component's feature vector into health, failure probability,
RUL and a human-readable explanation.

Hybrid score
------------
    health = 100 * (1 - 0.5 * P_fail - 0.5 * S_physics)

* P_fail     - ML probability of failure within 30 days (monotonic GBM)
* S_physics  - weighted physics severity: how far each indicator has moved
               from its healthy value towards its alarm limit
               (physics.THRESHOLDS, physics.SEVERITY_WEIGHTS)

Blending means the score reacts to an engineering limit being crossed even
before the ML model has seen a similar failure, and the ML model contributes
learned interactions (e.g. imbalance *and* high fatigue together).

Status (sorted at-risk -> medium -> good in the UI) - two stages:
  1. model verdict
    at-risk : P_fail >= 0.5  or health < 45  or RUL < 30 days
    medium  : P_fail >= 0.2  or health < 72  or RUL < 120 days
    good    : otherwise
  2. rule layer (rules.py) - engineering limits, rated life, growth trends and
     fast-response escalate; unconfirmed model signals, hysteresis and
     low-confidence de-escalate / hold. Every fired rule is reported.

RUL is hybrid: the physics first pass (1 - life_fraction) / consumption rate
caps the ML regressor once more than half the rated life is used (spec 5.2).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import joblib
import numpy as np

from . import physics as P
from .features import to_matrix
from .rules import apply_rules

log = logging.getLogger(__name__)

DEFAULT_ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "models.joblib"
RUL_CAP_DAYS = 3650.0

COMPONENT_LABELS = {"rope": "Suspension ropes", "sheave": "Traction sheave",
                    "bearing": "Sheave bearing", "traction": "Traction machine"}


def physics_first_pass_rul(life_fraction: float, life_rate_per_day: float) -> float:
    """
    Spec 5.2 first pass: remaining life from the component's life law alone.
    The simulator's documented Paris-law term (dD/dn grows as (1 + k D)) is
    included, so the estimate integrates  dD / (rate (1 + k D))  from the
    current life fraction to 1:  t = ln((1 + k) / (1 + k D)) / (k rate).
    With k -> 0 this is the plain (1 - D) / rate.
    """
    k = P.DAMAGE_ACCELERATION
    d = min(max(float(life_fraction), 0.0), 1.0)
    r = max(float(life_rate_per_day), 1e-9) * (1.0 + k * d)   # observed rate already includes today's acceleration
    base_rate = r / (1.0 + k * d)
    t = np.log((1.0 + k) / (1.0 + k * d)) / (k * base_rate)
    return float(np.clip(t, 0.0, RUL_CAP_DAYS))


def classify(health: float, p_fail: float, rul_days: float) -> str:
    if p_fail >= 0.5 or health < 45 or rul_days < 30:
        return "at-risk"
    if p_fail >= 0.2 or health < 72 or rul_days < 120:
        return "medium"
    return "good"


def physics_severity(ctype: str, feats: dict[str, float]) -> tuple[float, dict[str, float]]:
    weights = P.SEVERITY_WEIGHTS[ctype]
    sev = {f: P.severity(ctype, f, feats.get(f, 0.0)) for f in P.THRESHOLDS[ctype]}
    total_w = sum(weights.values())
    score = sum(w * min(1.0, sev.get(f, 0.0)) for f, w in weights.items()) / total_w
    return float(score), sev


def format_value(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "g":
        return f"{v:.3f} g"
    if unit == "C":
        return f"{v:.1f} C"
    if unit == "x rated":
        return f"{v * 100:.0f}% of rated"
    if unit in ("g/day",):
        return f"{v * 1000:.2f} mg/day"
    if unit == "%/day":
        return f"{v:.2f} %/day"
    if unit == "":
        return f"{v:.2f}"
    return f"{v:.2f} {unit}"


def format_rul(days: float) -> str:
    if days >= 730:
        return f"~{days / 365:.1f} years"
    if days >= 60:
        return f"~{days / 30.4:.0f} months"
    return f"~{days:.0f} days"


class Predictor:
    """Loads the trained bundle once; thread-safe lazy (re)training if missing."""

    _lock = threading.Lock()

    def __init__(self, artifact_path: str | Path | None = None, auto_train: bool = True,
                 train_kwargs: dict | None = None):
        self.path = Path(artifact_path) if artifact_path else DEFAULT_ARTIFACT
        with self._lock:
            if not self.path.exists():
                self._train_fresh(auto_train, train_kwargs, reason="no artifact found")
            try:
                self.bundle = joblib.load(self.path)
            except Exception as exc:
                # A committed model artifact is a pickle of scikit-learn's own
                # objects (including private classes such as HistGradientBoosting's
                # internal loss functions). Unpickling is only guaranteed to work
                # with the *exact* scikit-learn version that created the file - a
                # different version (even a compatible-looking one), a corrupted
                # download, or a git checkout that mangled a binary file all
                # surface as an unpickling error, most often
                # `ModuleNotFoundError: No module named '_loss'` when the
                # scikit-learn version differs from the one that trained the
                # model. Rather than let that crash the whole application on
                # startup, retrain a fresh set of models in the same place -
                # training takes seconds, and the retrained bundle always
                # matches whatever scikit-learn is actually installed.
                log.warning("Could not load model artifact at %s (%s: %s) - "
                           "retraining fresh models with the installed scikit-learn "
                           "version instead.", self.path, type(exc).__name__, exc)
                self._train_fresh(auto_train, train_kwargs,
                                  reason=f"existing artifact failed to load ({type(exc).__name__})")
                self.bundle = joblib.load(self.path)
        self.version: str = self.bundle["version"]

    def _train_fresh(self, auto_train: bool, train_kwargs: dict | None, reason: str) -> None:
        if not auto_train:
            raise FileNotFoundError(f"model artifact unusable at {self.path} ({reason}) and auto_train is disabled")
        from .train import train_all

        log.warning("Training models at %s (%s)...", self.path, reason)
        train_all(self.path.parent, **(train_kwargs or {}))

    @property
    def metrics(self) -> dict:
        return {k: v["metrics"] for k, v in self.bundle["models"].items()}

    def importances(self, ctype: str) -> dict[str, float]:
        return self.bundle["models"][ctype]["importances"]

    def predict_many(self, ctype: str, feats_list: list[dict[str, float]],
                     contexts: list[dict | None] | None = None) -> list[dict]:
        """
        Batched inference (one sklearn call for all components of a type).
        `contexts[i]` carries the rule-layer inputs for component i - see
        rules.apply_rules - and may be None.
        """
        if not feats_list:
            return []
        m = self.bundle["models"][ctype]
        X = to_matrix(feats_list)
        p_fail = m["classifier"].predict_proba(X)[:, 1]
        rul = np.clip(np.expm1(m["regressor"].predict(X)), 0.0, RUL_CAP_DAYS)
        contexts = contexts or [None] * len(feats_list)
        return [self._compose(ctype, f, float(p), float(r), c)
                for f, p, r, c in zip(feats_list, p_fail, rul, contexts)]

    def predict(self, ctype: str, feats: dict[str, float], context: dict | None = None) -> dict:
        return self.predict_many(ctype, [feats], [context])[0]

    # ------------------------------------------------------------------
    def _compose(self, ctype: str, feats: dict[str, float], p_fail: float, rul: float,
                 context: dict | None = None) -> dict:
        s_phys, sev = physics_severity(ctype, feats)
        health = float(np.clip(100.0 * (1.0 - 0.5 * p_fail - 0.5 * s_phys), 0.0, 100.0))
        ml_status = classify(health, p_fail, rul)
        ctx = dict(context or {})
        life = float(feats.get("fatigue_index", 0.0))
        rate = ctx.get("life_rate_per_day")
        if ctx.get("physics_rul_days") is None and rate and rate > 0:
            ctx["physics_rul_days"] = physics_first_pass_rul(life, rate)
        ruled = apply_rules(ctype, feats, sev, ml_status, p_fail, rul, health, ctx)
        status, rul_f, health_f = ruled["status"], ruled["rul_days"], ruled["health_score"]
        factors = self.contributing_factors(ctype, feats, sev)
        model = P.PHYSICS_MODEL[ctype]
        return {
            "health_score": round(health_f, 1),
            "failure_probability": round(p_fail * 100.0, 1),  # percent, within horizon
            "horizon_days": P.FAILURE_HORIZON_DAYS,
            "rul_days": round(rul_f, 1),
            "status": status,
            "ml_status": ml_status,
            "status_source": ruled["status_source"],
            "confidence": ruled["confidence"],
            "rules_fired": ruled["rules_fired"],
            "candidate_status": ruled["candidate_status"],
            "status_streak": ruled["status_streak"],
            "physics_severity": round(s_phys, 3),
            "physics": {
                "model": model["name"], "formula": model["formula"], "feature": model["feature"],
                "label": model["label"], "life_fraction": round(life, 4),
                "ml_rul_days": round(rul, 1),
                "physics_rul_days": (round(float(ctx["physics_rul_days"]), 1)
                                     if ctx.get("physics_rul_days") is not None else None),
            },
            "contributing_factors": factors,
            "explanation": self.explain(ctype, status, health_f, p_fail, rul_f, factors, ruled["rules_fired"]),
            "model_version": self.version,
            "features": {k: round(float(v), 5) for k, v in feats.items()},
        }

    def contributing_factors(self, ctype: str, feats: dict[str, float], sev: dict[str, float],
                             top_k: int = 4) -> list[dict]:
        imp = self.importances(ctype)
        weights = P.SEVERITY_WEIGHTS[ctype]
        rows = []
        for f, th in P.THRESHOLDS[ctype].items():
            s = sev.get(f, 0.0)
            # rank = how far past healthy x how much physics + the model care
            score = s * (weights.get(f, 0.05) + 0.5 * imp.get(f, 0.0))
            rows.append({
                "feature": f,
                "label": th.label,
                "value": round(float(feats.get(f, 0.0)), 5),
                "display_value": format_value(float(feats.get(f, 0.0)), th.unit),
                "healthy": th.healthy,
                "alarm": th.alarm,
                "unit": th.unit,
                "severity": round(s, 3),
                "model_importance": round(imp.get(f, 0.0), 3),
                "physics_weight": round(weights.get(f, 0.0), 3),
                "impact": round(score, 4),
                "advice": P.FACTOR_ADVICE[ctype].get(f, ""),
            })
        rows.sort(key=lambda r: r["impact"], reverse=True)
        return [r for r in rows if r["severity"] > 0.05][:top_k]

    @staticmethod
    def explain(ctype: str, status: str, health: float, p_fail: float, rul: float, factors: list[dict],
                rules_fired: list[dict] | None = None) -> str:
        name = COMPONENT_LABELS[ctype]
        head = {
            "at-risk": f"{name} AT RISK",
            "medium": f"{name} needs attention",
            "good": f"{name} healthy",
        }[status]
        summary = (f"{head}: health {health:.0f}/100, {p_fail * 100:.0f}% failure probability within "
                   f"{P.FAILURE_HORIZON_DAYS} days, estimated remaining life {format_rul(rul)}.")
        if not factors:
            return summary + " All monitored indicators are within healthy limits."
        drivers = []
        for f in factors[:2]:
            limit = format_value(f["alarm"], f["unit"])
            state = "above alarm limit" if f["severity"] >= 1 else "trending towards limit"
            drivers.append(f"{f['label'].lower()} {f['display_value']} ({state} {limit})")
        text = summary + " Key drivers: " + "; ".join(drivers) + "."
        if status != "good" and factors[0]["advice"]:
            text += " Recommended: " + factors[0]["advice"]
        esc = [r for r in (rules_fired or []) if r["effect"] in ("escalate", "hold")]
        if esc:
            text += f" Rule {esc[0]['id']} ({esc[0]['name'].lower()}) applied: {esc[0]['message']}"
        return text
