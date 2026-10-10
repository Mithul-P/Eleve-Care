"""Process-wide access to the trained ML predictor (decoupled from the API layer)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Make the sibling /ml package importable when running from a source checkout
# (in Docker it is pip-installed instead).
_ML_DIR = Path(__file__).resolve().parents[3] / "ml"
if _ML_DIR.exists() and str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

from elevcare_ml.predictor import Predictor  # noqa: E402

_predictor: Predictor | None = None


def load_predictor(artifact_path: str = "", train_kwargs: dict | None = None) -> Predictor:
    global _predictor
    _predictor = Predictor(artifact_path or None, auto_train=True, train_kwargs=train_kwargs)
    log.info("Loaded ML models %s", _predictor.version)
    return _predictor


def get_predictor() -> Predictor:
    if _predictor is None:
        raise RuntimeError("predictor not loaded")
    return _predictor
