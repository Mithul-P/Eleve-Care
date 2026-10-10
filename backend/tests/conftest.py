import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "ml"))

_ARTIFACTS = Path(tempfile.mkdtemp(prefix="elevcare-test-"))
os.environ.update({
    "MONGO_URL": "mongomock://",
    "MONGO_DB": "elevcare_test",
    "JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-signing",
    "SEED_ADMIN_PASSWORD": "admin-pass-123",
    "SEED_STAFF_PASSWORD": "staff-pass-123",
    "SEED_HISTORY_DAYS": "5",
    "SIM_ENABLED": "true",
    "SIM_BATCH_INTERVAL_SECONDS": "100000",
    "INGEST_API_KEY": "ingest-key",
    "ANTHROPIC_API_KEY": "",
    "ML_ARTIFACT_PATH": str(_ARTIFACTS / "models.joblib"),
    "COMPLAINT_RATE_LIMIT_PER_MINUTE": "3",
})


@pytest.fixture(scope="session")
def client():
    from elevcare_ml.train import train_all

    train_all(_ARTIFACTS, n_traj=10, rows_per_traj=60, seed=3, verbose=False)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin@elevcare.local", "admin-pass-123")


@pytest.fixture(scope="session")
def staff(client):
    return _login(client, "staff@elevcare.local", "staff-pass-123")


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.routers import complaints

    complaints._recent.clear()
