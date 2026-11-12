"""Regression tests: monthly regeneration must use the configured LLM provider.

The reported bug: a monthly cycle whose batch was produced by the template
engine kept returning template text after "Regenerate", while Create Post used
the configured provider. These tests reproduce both workflows against the same
shared generation service with a mocked Groq adapter - no real API calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai.llm_adapters import LLMUnavailable, ProviderResult

FAKE_MODEL = "openai/gpt-oss-120b"

TEXTS = [
    "The evening rush is back, and so is our loyalty card. Regulars who drop in after six "
    "get a free refill on their second cup, no stamp needed. We keep the corner table open "
    "for laptop workers until nine. Visit us this weekend.",
    "Some weeks deserve a slower finish. From Thursday we are lighting the back room, "
    "playing vinyl, and pouring the house blend at a gentler pace. Bring the friend you "
    "keep meaning to call. No queue, no rush. Visit us this weekend.",
    "A small thank you to everyone who waited through the renovation chaos. The new counter "
    "is finally in, the pastry case is full again, and the first ten regulars each evening "
    "get their usual on the house. Visit us this weekend.",
    "Coffee tastes better when you know the people behind it. Our beans come from a farm we "
    "visit every year, roasted in small batches two streets away. Ask for the tasting notes "
    "card at the counter and pick your favourite. Visit us this weekend.",
    "Winter is coming for the window seats, so we bought blankets. Claim one, order "
    "something warm, and stay as long as the conversation lasts. We are open late on "
    "Fridays and Saturdays and the fire is already on. Visit us this weekend.",
    "Your loyalty points now work on everything, including the new cold brew and the weekend "
    "brunch plates. Nothing expires, nothing hides behind fine print, and the team can check "
    "your balance at the till. Visit us this weekend.",
]


class FakeGroq:
    """Stands in for the hosted adapter: records calls, never touches the network."""

    name = "groq"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def complete(self, spec) -> ProviderResult:
        self.calls += 1
        if self.fail:
            raise LLMUnavailable("groq 429 rate limit", retryable=False)
        return ProviderResult(
            [{"arm": (i % 3) + 1, "text": t} for i, t in enumerate(TEXTS)],
            "groq", FAKE_MODEL, 900, 400, 0.0, 12,
        )


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit breakers are process-global; a failure test must not leak into the next."""
    from app.ai.llm_adapters import _breakers

    _breakers.clear()
    yield
    _breakers.clear()


@pytest.fixture()
def fake_groq(monkeypatch):
    """Mock only Groq: everything else still resolves the real adapter."""
    from app.ai import llm_adapters
    from app.core.config import settings

    original = llm_adapters.get_adapter
    adapter = FakeGroq()

    def patched(provider=None):
        if settings.resolve_provider(provider) == "groq":
            return adapter
        return original(provider)

    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(llm_adapters, "get_adapter", patched)
    return adapter


def _setup_cafe(client, auth_headers) -> dict:
    aud = client.post("/api/v1/audiences", headers=auth_headers,
                      json={"name": "Young professionals", "description": "25-35, city workers"})
    assert aud.status_code == 201, aud.text
    topic = client.post("/api/v1/topics", headers=auth_headers,
                        json={"name": "Evening coffee loyalty rewards",
                              "description": "loyalty scheme", "keywords": ["free refill"]})
    assert topic.status_code == 201, topic.text
    plan = client.post("/api/v1/monthly-plans", headers=auth_headers, json={
        "name": "Brew & Bean monthly offer", "topic_id": topic.json()["id"],
        "audience_id": aud.json()["id"], "tones": ["warm", "playful"], "purpose": "promote",
        "platform": "instagram", "length_preset": "medium", "post_day": 1, "lead_days": 7,
        "keywords": ["free refill", "evening"], "cta_text": "Visit us this weekend",
    })
    assert plan.status_code == 201, plan.text
    return {"audience": aud.json(), "topic": topic.json(), "plan": plan.json()}


def _first_planned_cycle(client, auth_headers, plan_id: str) -> dict:
    cycles = [c for c in client.get("/api/v1/cycles", headers=auth_headers).json()
              if c["plan_id"] == plan_id and c["status"] == "PLANNED"]
    assert cycles, "the plan should have pre-created cycles"
    return sorted(cycles, key=lambda c: c["target_month"])[0]


def _stage(client, auth_headers, request_id: str) -> dict:
    resp = client.get(f"/api/v1/generation-requests/{request_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- test case 1
def test_create_post_uses_configured_provider(client, auth_headers, fake_groq):
    ids = _setup_cafe(client, auth_headers)
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2026-12", "topic_id": ids["topic"]["id"], "tones": ["warm"],
        "purpose": "promote", "platform": "instagram", "length": "medium",
        "cta": "Visit us this weekend",
    })
    assert gen.status_code == 202, gen.text
    stage = _stage(client, auth_headers, gen.json()["request_id"])

    assert stage["status"] == "DONE"
    assert stage["provider"] == "groq"
    assert stage["model"] == FAKE_MODEL
    assert stage["is_fallback"] is False
    assert fake_groq.calls == 1
    assert all(c["is_fallback"] is False for c in stage["candidates"])


# ---------------------------------------------------------------- test case 2
def test_monthly_regeneration_switches_from_template_to_llm(client, auth_headers, fake_groq,
                                                            monkeypatch):
    """The exact reported bug: a template-generated monthly batch, then Regenerate."""
    from app.core.config import settings

    ids = _setup_cafe(client, auth_headers)
    cycle = _first_planned_cycle(client, auth_headers, ids["plan"]["id"])

    # 1. the original monthly batch, produced while the template engine was active
    monkeypatch.setattr(settings, "llm_provider", "template")
    original = client.post(f"/api/v1/cycles/{cycle['id']}/generate", headers=auth_headers)
    assert original.status_code == 202, original.text
    old = _stage(client, auth_headers, original.json()["request_id"])
    assert old["provider"] == "template" and old["is_fallback"] is True
    old_ids = {c["id"] for c in old["candidates"]}
    assert old_ids, "the template batch must have candidates"

    # 2. Regenerate with Groq configured: it must actually call Groq
    monkeypatch.setattr(settings, "llm_provider", "groq")
    regen = client.post(f"/api/v1/generation-requests/{original.json()['request_id']}/regenerate",
                        headers=auth_headers, params={"instruction": "different angle"})
    assert regen.status_code == 202, regen.text
    new = _stage(client, auth_headers, regen.json()["request_id"])

    assert new["status"] == "DONE", new.get("error")
    assert new["provider"] == "groq"
    assert new["model"] == FAKE_MODEL
    assert new["is_fallback"] is False
    assert fake_groq.calls >= 1
    assert new["source"] == "regenerate"
    new_candidates = [c for c in new["candidates"] if c["rank"]]
    assert len(new_candidates) >= 3
    assert all(c["is_fallback"] is False for c in new_candidates)
    # fresh content, not the reused template rows
    assert not ({c["id"] for c in new["candidates"]} & old_ids)
    assert {c["text"] for c in new_candidates} & set(TEXTS)

    # the monthly context survived regeneration
    assert new["brief"]["scheduled_date"] is not None
    assert new["brief"]["plan_name"] == "Brew & Bean monthly offer"
    assert new["brief"]["topic"] == "Evening coffee loyalty rewards"

    # the old batch is preserved untouched
    assert {c["id"] for c in _stage(client, auth_headers, old["id"])["candidates"]} == old_ids


# ---------------------------------------------------------------- test case 3
def test_repeated_regeneration_always_uses_the_llm(client, auth_headers, fake_groq):
    ids = _setup_cafe(client, auth_headers)
    cycle = _first_planned_cycle(client, auth_headers, ids["plan"]["id"])
    request_id = client.post(f"/api/v1/cycles/{cycle['id']}/generate",
                             headers=auth_headers).json()["request_id"]

    for _ in range(2):
        calls_before = fake_groq.calls
        resp = client.post(f"/api/v1/generation-requests/{request_id}/regenerate",
                           headers=auth_headers)
        assert resp.status_code == 202, resp.text
        request_id = resp.json()["request_id"]
        stage = _stage(client, auth_headers, request_id)
        assert stage["status"] == "DONE", stage.get("error")
        assert stage["provider"] == "groq"
        assert fake_groq.calls > calls_before

    # same cycle, no duplicate cycle rows
    cycles = [c for c in client.get("/api/v1/cycles", headers=auth_headers).json()
              if c["plan_id"] == ids["plan"]["id"]]
    assert len({c["target_month"] for c in cycles}) == len(cycles)


# ---------------------------------------------------------------- test case 4
def test_provider_failure_is_explicit_and_keeps_previous_candidates(client, auth_headers,
                                                                    fake_groq, monkeypatch):
    from app.core.config import settings

    ids = _setup_cafe(client, auth_headers)
    cycle = _first_planned_cycle(client, auth_headers, ids["plan"]["id"])

    monkeypatch.setattr(settings, "llm_provider", "template")
    original = client.post(f"/api/v1/cycles/{cycle['id']}/generate", headers=auth_headers)
    old = _stage(client, auth_headers, original.json()["request_id"])
    old_texts = [c["text"] for c in old["candidates"]]

    monkeypatch.setattr(settings, "llm_provider", "groq")
    fake_groq.fail = True
    resp = client.post(f"/api/v1/generation-requests/{old['id']}/regenerate",
                       headers=auth_headers)
    assert resp.status_code == 202, resp.text
    failed = _stage(client, auth_headers, resp.json()["request_id"])

    assert failed["status"] == "FAILED"
    assert failed["error"] and "groq" in failed["error"]
    assert failed["candidates"] == []          # no template text passed off as a result
    assert failed["is_fallback"] is False

    # previous batch intact, cycle still reachable for a retry
    kept = _stage(client, auth_headers, old["id"])
    assert kept["status"] == "DONE"
    assert [c["text"] for c in kept["candidates"]] == old_texts

    fake_groq.fail = False
    retry = client.post(f"/api/v1/generation-requests/{old['id']}/regenerate",
                        headers=auth_headers)
    retried = _stage(client, auth_headers, retry.json()["request_id"])
    assert retried["status"] == "DONE" and retried["provider"] == "groq"


# ---------------------------------------------------------------- test case 5
def test_scheduled_worker_uses_the_same_provider(client, auth_headers, fake_groq):
    """The background scheduler must not default to the template engine."""
    from app.db.models import GenerationRequest, PostCycle
    from app.db.session import SessionLocal
    from app.scheduler import jobs

    ids = _setup_cafe(client, auth_headers)
    cycle = _first_planned_cycle(client, auth_headers, ids["plan"]["id"])

    db = SessionLocal()
    row = db.get(PostCycle, cycle["id"])
    row.generate_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    db.close()

    jobs.prepare_due_cycles()

    db = SessionLocal()
    try:
        req = db.query(GenerationRequest).filter(
            GenerationRequest.cycle_id == cycle["id"]).order_by(
            GenerationRequest.created_at.desc()).first()
        assert req is not None
        assert req.provider == "groq", req.error
        assert req.model == FAKE_MODEL
    finally:
        db.close()
    assert fake_groq.calls >= 1


# ---------------------------------------------------------------- test case 6
def test_brief_is_per_cycle_and_never_invented(client, auth_headers, fake_groq):
    """Two plans must not share a brief, and missing fields stay empty."""
    first = _setup_cafe(client, auth_headers)
    other_topic = client.post("/api/v1/topics", headers=auth_headers,
                              json={"name": "Winter workshop series", "keywords": ["workshop"]})
    second_plan = client.post("/api/v1/monthly-plans", headers=auth_headers, json={
        "name": "Northline workshop plan", "topic_id": other_topic.json()["id"],
        "tones": ["professional"], "purpose": "announce", "platform": "linkedin",
        "length_preset": "long", "post_day": 10, "lead_days": 7,
        "keywords": ["workshop"], "cta_text": "Reserve your seat",
    })
    assert second_plan.status_code == 201, second_plan.text

    cafe_cycle = _first_planned_cycle(client, auth_headers, first["plan"]["id"])
    work_cycle = _first_planned_cycle(client, auth_headers, second_plan.json()["id"])
    cafe_stage = _stage(client, auth_headers, client.post(
        f"/api/v1/cycles/{cafe_cycle['id']}/generate", headers=auth_headers
    ).json()["request_id"])
    work_stage = _stage(client, auth_headers, client.post(
        f"/api/v1/cycles/{work_cycle['id']}/generate", headers=auth_headers
    ).json()["request_id"])

    assert cafe_stage["brief"]["topic"] == "Evening coffee loyalty rewards"
    assert cafe_stage["brief"]["plan_name"] == "Brew & Bean monthly offer"
    assert cafe_stage["brief"]["scheduled_date"] == cafe_cycle["post_at"][:10]
    assert work_stage["brief"]["topic"] == "Winter workshop series"
    assert work_stage["brief"]["plan_name"] == "Northline workshop plan"
    assert work_stage["brief"]["platform"] == "linkedin"
    assert work_stage["brief"]["cta"] == "Reserve your seat"
    assert work_stage["brief"]["tones"] == ["professional"]
    assert cafe_stage["brief"]["topic"] != work_stage["brief"]["topic"]

    # a manual Create Post batch has no monthly plan / posting date to show
    manual = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2026-12", "tones": ["warm"], "purpose": "inform",
        "platform": "instagram", "length": "medium",
    }).json()
    manual_stage = _stage(client, auth_headers, manual["request_id"])
    assert manual_stage["brief"]["plan_name"] is None
    assert manual_stage["brief"]["scheduled_date"] is None
    assert manual_stage["source"] == "manual"
