"""Regression tests: four demo accounts, each with real, isolated data.

Audit Task 5 / Task 6 / Task 11 items 8-10:
  * scripts/seed.py declares four distinct personas
  * every account carries plan + cycles + history + selection + feedback +
    notifications + arm priors, all derived from actual records
  * no ids leak between accounts
  * the login page exposes all four accounts
"""
from __future__ import annotations

import importlib.util
import os

import pytest

DEMO_PAIRS = [
    ("demo@monthlymuse.app", "monthlymuse-demo"),
    ("pro@monthlymuse.app", "monthlymuse-pro"),
    ("studio@monthlymuse.app", "monthlymuse-studio"),
    ("org@monthlymuse.app", "monthlymuse-org"),
]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def seeded(client):
    """Run the real seed script once against the test database (idempotent)."""
    path = os.path.join(_REPO_ROOT, "scripts", "seed.py")
    spec = importlib.util.spec_from_file_location("monthlymuse_seed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()
    return mod


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"{email}: {resp.status_code} {resp.text[:200]}"
    return {"Authorization": "Bearer " + resp.json()["access_token"]}


def test_seed_declares_four_distinct_personas(seeded):
    assert len(seeded.DEMOS) == 4
    emails = [d["email"] for d in seeded.DEMOS]
    passwords = [d["password"] for d in seeded.DEMOS]
    assert len(set(emails)) == 4 and len(set(passwords)) == 4
    # genuinely different use cases: platform / purpose / posting day vary
    assert len({d["platform"] for d in seeded.DEMOS}) >= 3
    assert len({d.get("post_day") for d in seeded.DEMOS}) >= 3
    assert len({d.get("purpose") for d in seeded.DEMOS}) >= 3
    assert {d["email"] for d in seeded.DEMOS} == {e for e, _ in DEMO_PAIRS}


def test_every_demo_account_has_associated_data(seeded, client):
    for email, password in DEMO_PAIRS:
        h = _login(client, email, password)
        plans = client.get("/api/v1/monthly-plans", headers=h).json()
        cycles = client.get("/api/v1/cycles", headers=h).json()
        msgs = client.get("/api/v1/messages", headers=h, params={"page_size": 50}).json()
        summary = client.get("/api/v1/analytics/summary", headers=h).json()
        learned = client.get("/api/v1/me/preferences/learned", headers=h).json()
        notes = client.get("/api/v1/notifications", headers=h).json()

        assert len(plans) >= 1, email
        assert len(cycles) >= 12, email                      # a real plan with cycles
        assert all(c["generate_at"] for c in cycles)          # every cycle knows its prep date
        assert msgs["total"] >= 6, email                      # published history + review batch
        selected = [m for m in msgs["items"] if m["status"] == "SELECTED"]
        assert len(selected) >= 2, email                      # two published months
        assert learned["n_feedback_events"] >= 4, email       # select/ignore/like/copy
        assert learned["arm_stats"], email                    # Beta priors exist
        assert len(notes) >= 1, email                         # READY_FOR_REVIEW notification
        assert len(summary["recent_activity"]) >= 4, email    # analytics from real events
        # dashboard dates come from the cycle rows
        assert summary["upcoming"], email
        assert all(u["generate_at"] for u in summary["upcoming"])


def test_demo_passwords_and_wrong_passwords(seeded, client):
    for email, password in DEMO_PAIRS:
        assert client.post("/api/v1/auth/login",
                           json={"email": email, "password": "not-the-password"}).status_code == 401
        _login(client, email, password)


def test_demo_data_is_isolated_between_accounts(seeded, client):
    snapshots: dict[str, set] = {}
    plans: dict[str, set] = {}
    cycles: dict[str, set] = {}
    for email, password in DEMO_PAIRS:
        h = _login(client, email, password)
        msgs = client.get("/api/v1/messages", headers=h, params={"page_size": 100}).json()
        snapshots[email] = {m["id"] for m in msgs["items"]}
        plans[email] = {p["id"] for p in client.get("/api/v1/monthly-plans", headers=h).json()}
        cycles[email] = {c["id"] for c in client.get("/api/v1/cycles", headers=h).json()}
    emails = list(snapshots)
    for i in range(len(emails)):
        for j in range(i + 1, len(emails)):
            a, b = emails[i], emails[j]
            assert not (snapshots[a] & snapshots[b]), f"message leak {a}/{b}"
            assert not (plans[a] & plans[b]), f"plan leak {a}/{b}"
            assert not (cycles[a] & cycles[b]), f"cycle leak {a}/{b}"


def test_cross_account_object_access_is_forbidden(seeded, client):
    owner = _login(client, *DEMO_PAIRS[0])
    other = _login(client, *DEMO_PAIRS[1])
    plan_id = client.get("/api/v1/monthly-plans", headers=owner).json()[0]["id"]
    cycle_id = client.get("/api/v1/cycles", headers=other).json()[0]["id"]
    # other user's token cannot read or patch the first user's plan
    assert client.get(f"/api/v1/monthly-plans/{plan_id}", headers=other).status_code == 404
    assert client.patch(f"/api/v1/monthly-plans/{plan_id}", headers=other,
                        json={"name": "stolen"}).status_code == 404
    # and cannot approve someone else's cycle with a foreign message
    msgs = client.get("/api/v1/messages", headers=owner, params={"page_size": 10}).json()
    pick = msgs["items"][0]
    assert client.post(f"/api/v1/cycles/{cycle_id}/approve", headers=other,
                       json={"message_id": pick["id"]}).status_code in (403, 404)


def test_login_page_exposes_all_four_accounts():
    path = os.path.join(_REPO_ROOT, "frontend", "app", "login", "page.tsx")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    for email, password in DEMO_PAIRS:
        assert email in source, f"{email} missing from the login page"
        assert password in source, f"password for {email} missing from the login page"
    assert source.count("email:") >= 4
