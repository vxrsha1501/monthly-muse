"""Pytest fixtures: temp database, TestClient (lifespan runs init + seeds), authed user."""
from __future__ import annotations

import os
import sys

# must be set before any app import (settings are cached at import time)
TEST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_monthlymuse.db")
if os.path.exists(TEST_DB):
    try:
        os.remove(TEST_DB)
    except OSError:  # pragma: no cover
        pass
os.environ["MM_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["MM_SCHEDULER_ENABLED"] = "false"
os.environ["MM_ENVIRONMENT"] = "test"
os.environ["MM_LLM_PROVIDER"] = "template"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import create_app
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers(client):
    """A fresh registered user per test (isolated by unique email)."""
    import random
    email = f"user{random.randint(10_000, 99_999)}@example.com"
    resp = client.post("/api/v1/auth/register", json={
        "email": email, "password": "correct-horse-battery",
        "full_name": "Test User", "timezone": "Asia/Kolkata", "region": "India",
    })
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
