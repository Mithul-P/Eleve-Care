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

Status rules (sorted at-risk -> medium -> good in the UI):
    at-risk : P_fail >= 0.5  or health < 45  or RUL < 30 days
    medium  : P_fail >= 0.2  or health < 72  or RUL < 120 days
    good    : otherwise
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import joblib
import numpy as np

from . import physics as P
from .features import to_vector

log = logging.getLogger(__name__)

DEFAULT_ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "models.joblib"
RUL_CAP_DAYS = 3650.0

COMPONENT_LABELS = {"rope": "Suspension ropes", "sheave": "Traction sheave",
                    "bearing": "Sheave bearing", "traction": "Traction machine"}


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
                if not auto_train:
                    raise FileNotFoundError(f"model artifact not found: {self.path}")
                from .train import train_all

                log.warning("No trained models at %s - training now (one-off)...", self.path)
                train_all(self.path.parent, **(train_kwargs or {}))
            self.bundle = joblib.load(self.path)
        self.version: str = self.bundle["version"]

    @property
    def metrics(self) -> dict:
        return {k: v["metrics"] for k, v in self.bundle["models"].items()}

    def importances(self, ctype: str) -> dict[str, float]:
        return self.bundle["models"][ctype]["importances"]

    def predict_many(self, ctype: str, feats_list: list[dict[str, float]]) -> list[dict]:
        if not feats_list:
            return []
        m = self.bundle["models"][ctype]
        X = np.vstack([to_vector(f) for f in feats_list])
        p_fail = m["classifier"].predict_proba(X)[:, 1]
        rul = np.clip(np.expm1(m["regressor"].predict(X)), 0.0, RUL_CAP_DAYS)
        return [self._compose(ctype, f, float(p), float(r)) for f, p, r in zip(feats_list, p_fail, rul)]

    def predict(self, ctype: str, feats: dict[str, float]) -> dict:
        return self.predict_many(ctype, [feats])[0]

    # ------------------------------------------------------------------
    def _compose(self, ctype: str, feats: dict[str, float], p_fail: float, rul: float) -> dict:
        s_phys, sev = physics_severity(ctype, feats)
        health = float(np.clip(100.0 * (1.0 - 0.5 * p_fail - 0.5 * s_phys), 0.0, 100.0))
        status = classify(health, p_fail, rul)
        factors = self.contributing_factors(ctype, feats, sev)
        return {
            "health_score": round(health, 1),
            "failure_probability": round(p_fail * 100.0, 1),  # percent, within horizon
            "horizon_days": P.FAILURE_HORIZON_DAYS,
            "rul_days": round(rul, 1),
            "status": status,
            "physics_severity": round(s_phys, 3),
            "contributing_factors": factors,
            "explanation": self.explain(ctype, status, health, p_fail, rul, factors),
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
    def explain(ctype: str, status: str, health: float, p_fail: float, rul: float, factors: list[dict]) -> str:
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
        return text
