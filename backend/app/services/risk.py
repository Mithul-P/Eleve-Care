"""
ONE shared risk vocabulary for the whole application (Section 5).

Everything - tables, charts, cards, chatbot replies, notifications and
recommendations - derives its colour, label and sort order from `classify()`
so the same component can never appear as "medium" on one page and
"critical" on another.

Four levels, deliberately coarser than the model's raw probability so a
technician can act on them:

    critical  replace now     failure probability >= 60% or RUL <= 14 days
    high      replace soon    failure probability >= 35% or RUL <= 45 days
    medium    monitor         failure probability >= 15% or RUL <= 120 days
    good      no action       everything else

The legacy three-state `status` field on the component document
("at-risk" / "medium" / "good") is kept untouched for backwards
compatibility; `classify()` refines it, it does not replace it.
"""

from __future__ import annotations

# Thresholds are module-level so the frontend can fetch them and draw the
# same horizontal threshold line on its charts (Section 5.2).
CRITICAL_PROB = 60.0
HIGH_PROB = 35.0
MEDIUM_PROB = 15.0

CRITICAL_RUL_DAYS = 14
HIGH_RUL_DAYS = 45
MEDIUM_RUL_DAYS = 120

RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "good": 3}

RISK_META = {
    "critical": {"label": "Critical - replace now", "short": "REPLACE NOW", "icon": "alert",
                 "color": "var(--critical)", "tag": "REPLACE"},
    "high": {"label": "High risk - replace soon", "short": "REPLACE SOON", "icon": "alert",
             "color": "var(--serious)", "tag": "REPLACE"},
    "medium": {"label": "Medium - monitor", "short": "MONITOR", "icon": "watch",
               "color": "var(--warning)", "tag": None},
    "good": {"label": "Good", "short": "NO ACTION", "icon": "check",
             "color": "var(--good)", "tag": None},
}

THRESHOLDS = {
    "failure_probability": {"critical": CRITICAL_PROB, "high": HIGH_PROB, "medium": MEDIUM_PROB, "unit": "%"},
    "rul_days": {"critical": CRITICAL_RUL_DAYS, "high": HIGH_RUL_DAYS, "medium": MEDIUM_RUL_DAYS, "unit": "days"},
}


def classify(failure_probability: float | None, rul_days: float | None,
             status: str | None = None) -> str:
    """Return one of critical / high / medium / good.

    Probability and RUL are considered independently and the *worse* of the
    two wins: a part with a modest probability but only nine days of life
    left is still critical.
    """
    p = failure_probability if failure_probability is not None else 0.0
    r = rul_days if rul_days is not None else 10_000

    if p >= CRITICAL_PROB or r <= CRITICAL_RUL_DAYS:
        return "critical"
    if p >= HIGH_PROB or r <= HIGH_RUL_DAYS:
        return "high"
    if p >= MEDIUM_PROB or r <= MEDIUM_RUL_DAYS:
        return "medium"
    # A component the model flagged through a rule but whose numbers look calm
    # should still not show as "good".
    if status == "at-risk":
        return "high"
    if status == "medium":
        return "medium"
    return "good"


def risk_of(component: dict) -> str:
    return classify(component.get("failure_probability"), component.get("rul_days"),
                    component.get("status"))


def annotate(component: dict) -> dict:
    """Attach `risk`, `risk_label`, `risk_rank` and `replace_tag` to a view dict."""
    level = risk_of(component)
    meta = RISK_META[level]
    component["risk"] = level
    component["risk_label"] = meta["label"]
    component["risk_action"] = meta["short"]
    component["risk_rank"] = RISK_ORDER[level]
    component["replace_tag"] = meta["tag"]
    return component


def sort_by_risk(rows: list[dict]) -> list[dict]:
    """Critical first, then high, medium, good; worst health first inside a level."""
    return sorted(rows, key=lambda r: (RISK_ORDER.get(r.get("risk") or risk_of(r), 3),
                                       r.get("health_score") if r.get("health_score") is not None else 100))


def summarise(rows: list[dict]) -> dict:
    """Counts per level plus the banner text used at the top of an elevator page."""
    counts = {k: 0 for k in RISK_ORDER}
    for r in rows:
        counts[r.get("risk") or risk_of(r)] += 1
    needs_replacement = counts["critical"] + counts["high"]
    banner = None
    if needs_replacement:
        word = "component needs" if needs_replacement == 1 else "components need"
        banner = f"{needs_replacement} {word} replacement"
    return {"counts": counts, "needs_replacement": needs_replacement, "banner": banner,
            "worst": min((r.get("risk") or risk_of(r) for r in rows),
                         key=lambda k: RISK_ORDER[k], default="good")}
