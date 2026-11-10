"""Regression tests: the external scheduler webhook (review P12 + P16).

An outside cron pokes POST /admin/scheduler/run because serverless hosts suspend
APScheduler.  Requirements proven here: token authentication (constant-time),
idempotent repeated calls (one generation per cycle), concurrency refusal (409),
job audit records, and useful status reporting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal

URL = "/api/v1/admin/scheduler/run"


def _token_headers(token: str | None = None) -> dict:
    return {"X-Webhook-Token": token if token is not None else settings.scheduler_token}


def test_token_never_leaks_the_secret_key():
    token = settings.scheduler_token
    assert token != settings.secret_key
    assert len(token) >= 32                      # sha256 hex or an explicit strong token


def test_webhook_rejects_missing_and_wrong_tokens(client):
    assert client.post(URL).status_code == 401
    assert client.post(URL, headers=_token_headers("wrong-token")).status_code == 401
    assert client.post(URL, headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    # a valid user session alone is NOT enough (cron auth is the shared secret)
    assert client.post(URL, headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401


def _make_due_plan(client, auth_headers) -> str:
    topic = client.post("/api/v1/topics", headers=auth_headers,
                        json={"name": "Webhook update", "keywords": ["news"]}).json()
    plan = client.post("/api/v1/monthly-plans", headers=auth_headers, json={
        "name": "Webhook plan", "topic_id": topic["id"], "tones": ["warm"],
        "purpose": "inform", "platform": "instagram", "post_day": 15, "lead_days": 7,
        "cta_text": "Read more"}).json()
    db = SessionLocal()
    try:
        from app.db.models import PostCycle
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        cycle.generate_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
        return cycle.id
    finally:
        db.close()


def _requests_for(cycle_id: str) -> int:
    db = SessionLocal()
    try:
        from app.db.models import GenerationRequest
        rows = db.scalars(select(GenerationRequest).where(
            GenerationRequest.cycle_id == cycle_id)).all()
        return len(rows)
    finally:
        db.close()


def test_webhook_runs_a_tick_and_records_job_runs(client, auth_headers):
    resp = client.post(URL, headers=_token_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["result"]) >= {"prepared", "failed", "reminders"}
    assert body["jobs"], "the tick must leave job_runs audit rows"
    assert all(j["status"] in ("success", "failed", "running") for j in body["jobs"])


def test_repeated_webhook_calls_never_generate_a_cycle_twice(client, auth_headers):
    cycle_id = _make_due_plan(client, auth_headers)
    first = client.post(URL, headers=_token_headers())
    assert first.status_code == 200
    assert first.json()["result"]["prepared"] >= 1
    assert _requests_for(cycle_id) == 1, "first tick generated the due cycle exactly once"

    # hammer the endpoint: the cycle is now AWAITING_REVIEW, nothing may repeat
    for _ in range(3):
        again = client.post(URL, headers=_token_headers())
        assert again.status_code == 200
        assert again.json()["result"]["failed"] == 0
    assert _requests_for(cycle_id) == 1, "repeated ticks must be idempotent"

    from app.db.models import CycleStatus, PostCycle
    db = SessionLocal()
    try:
        assert db.get(PostCycle, cycle_id).status == CycleStatus.AWAITING_REVIEW
    finally:
        db.close()


def test_concurrent_tick_is_refused_with_409(client):
    from app.api import misc as misc_api
    acquired = misc_api._scheduler_lock.acquire(blocking=False)
    assert acquired, "lock must be free between ticks"
    try:
        resp = client.post(URL, headers=_token_headers())
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "conflict"
    finally:
        misc_api._scheduler_lock.release()
    # released again -> next tick works
    assert client.post(URL, headers=_token_headers()).status_code == 200


def test_token_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("MM_SCHEDULER_WEBHOOK_TOKEN", "explicit-cron-secret")
    from app.core.config import Settings
    fresh = Settings(_env_file=None)
    assert fresh.scheduler_token == "explicit-cron-secret"
