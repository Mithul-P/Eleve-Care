import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def predictor(tmp_path_factory):
    """Small, fast model bundle trained just for the tests."""
    from elevcare_ml.predictor import Predictor
    from elevcare_ml.train import train_all

    out = tmp_path_factory.mktemp("artifacts")
    train_all(out, n_traj=10, rows_per_traj=60, seed=3, verbose=False)
    return Predictor(out / "models.joblib", auto_train=False)
