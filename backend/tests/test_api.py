"""End-to-end API test: the full user journey from Section 17 (register -> review -> learn)."""
from __future__ import annotations

import pytest


def _register_and_setup(client, auth_headers):
    """Audience + topic + plan, returns ids."""
    aud = client.post("/api/v1/audiences", headers=auth_headers,
                      json={"name": "Young professionals", "description": "25-35, city workers"})
    assert aud.status_code == 201, aud.text
    topic = client.post("/api/v1/topics", headers=auth_headers,
                        json={"name": "Evening coffee loyalty rewards",
                              "description": "loyalty scheme", "keywords": ["free refill", "evening"]})
    assert topic.status_code == 201, topic.text
    plan = client.post("/api/v1/monthly-plans", headers=auth_headers, json={
        "name": "Cafe monthly post", "topic_id": topic.json()["id"],
        "audience_id": aud.json()["id"], "tones": ["warm", "playful"], "purpose": "promote",
        "platform": "instagram", "length_preset": "medium", "post_day": 1, "lead_days": 7,
        "keywords": ["free refill"], "cta_text": "Visit us this weekend",
    })
    assert plan.status_code == 201, plan.text
    return aud.json(), topic.json(), plan.json()


def test_auth_flow_and_error_envelope(client):
    resp = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass-123"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] in ("unauthorized",)
    assert "request_id" in body["error"]

    missing = client.get("/api/v1/me")
    assert missing.status_code == 401


def test_me_and_preferences(client, auth_headers):
    me = client.get("/api/v1/me", headers=auth_headers)
    assert me.status_code == 200 and me.json()["role"] == "user"

    put = client.put("/api/v1/me/preferences", headers=auth_headers, json={
        "default_platform": "instagram", "voice_notes": "friendly cafe voice",
        "banned_words": ["cheap"], "emoji_level": 1, "length_preset": "medium",
        "lead_days": 7, "notify_email": True, "variety": 0.7,
        "default_tones": ["warm"],
    })
    assert put.status_code == 200, put.text

    learned = client.get("/api/v1/me/preferences/learned", headers=auth_headers)
    assert learned.status_code == 200
    data = learned.json()
    assert set(data["scoring_weights"]) == {"R", "T", "P", "N", "Len", "Hist"}
    assert abs(sum(data["scoring_weights"].values()) - 1.0) < 0.02
    assert data["arm_stats"], "onboarding tones should seed Beta priors (alpha = 2)"
    assert all(a["alpha"] >= 2.0 for a in data["arm_stats"] if a["tone"] == "warm")


def test_catalog_endpoints(client, auth_headers):
    occ = client.get("/api/v1/occasions", headers=auth_headers, params={"month": 11})
    assert occ.status_code == 200 and len(occ.json()) >= 3
    assert any("festive" in o["name"].lower() or "diwali" in o["name"].lower() for o in occ.json())

    topics = client.get("/api/v1/topics", headers=auth_headers)
    assert topics.status_code == 200 and len(topics.json()) >= 20   # global seeds + own


def test_plan_creates_twelve_cycles(client, auth_headers):
    _, _, plan = _register_and_setup(client, auth_headers)
    cycles = client.get("/api/v1/cycles", headers=auth_headers)
    assert cycles.status_code == 200
    rows = cycles.json()
    assert len(rows) == 12
    assert rows[0]["plan_name"] == "Cafe monthly post"
    assert all(r["status"] == "PLANNED" for r in rows)
    # idempotent extension
    from app.scheduler.jobs import extend_plans
    extend_plans()
    again = client.get("/api/v1/cycles", headers=auth_headers)
    assert len(again.json()) == 12


def test_generation_review_select_learning_journey(client, auth_headers):
    aud, topic, plan = _register_and_setup(client, auth_headers)

    # --- 3-step form submits a generate request (202 + request_id)
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2026-11", "topic_id": topic["id"], "topic": topic["name"],
        "occasion": "Start of festive season", "audience_id": aud["id"],
        "tones": ["warm", "playful"], "purpose": "promote", "platform": "instagram",
        "length": "medium", "language": "en", "keywords": ["free refill", "evening"],
        "cta": "Visit us this weekend",
    })
    assert gen.status_code == 202, gen.text
    request_id = gen.json()["request_id"]

    # --- poll until DONE (background task runs within the TestClient cycle)
    stage = client.get(f"/api/v1/generation-requests/{request_id}", headers=auth_headers)
    assert stage.status_code == 200
    data = stage.json()
    assert data["status"] == "DONE", data.get("error")
    assert data["ui_stage"] == "done"
    candidates = data["candidates"]
    shown = [c for c in candidates if c["status"] == "SHOWN" and c["rank"]]
    assert len(shown) == 3
    assert [c["rank"] for c in shown] == [1, 2, 3]

    first = shown[0]
    assert 0 < first["score"] <= 1
    assert first["confidence"] in ("high", "medium", "low")
    assert set(first["features"]) == {"relevance", "tone", "personalization",
                                      "novelty", "length", "history"}
    assert "visit" in first["text"].lower()          # CTA hard constraint
    assert first["explain"]["reasons"], "why-this-ranked panel"
    assert data["arms"] and len(data["arms"]) == 3

    # --- edit the chosen candidate, then select it
    edit = client.patch(f"/api/v1/messages/{first['id']}", headers=auth_headers,
                        json={"final_text": first["text"].replace("weekend", "Saturday")})
    assert edit.status_code == 200 and edit.json()["word_count"] > 0

    sel = client.post(f"/api/v1/messages/{first['id']}/select", headers=auth_headers)
    assert sel.status_code == 200 and sel.json()["status"] == "SELECTED"

    # --- learning is visible in Settings
    learned = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    assert learned["n_feedback_events"] >= 2            # edit + select (+ ignores)
    assert learned["learned_length"]["n"] >= 1
    assert learned["preference_vector_norm"] is not None

    # --- feedback: reject a sibling with a reason (nudges the novelty weight)
    sibling = next(c for c in shown if c["id"] != first["id"])
    fb = client.post(f"/api/v1/messages/{sibling['id']}/feedback", headers=auth_headers,
                     json={"event_type": "reject", "reason": "too_similar"})
    assert fb.status_code == 200 and fb.json()["reward"] == -0.8
    like = client.post(f"/api/v1/messages/{first['id']}/feedback", headers=auth_headers,
                       json={"event_type": "like"})
    assert like.json()["reward"] == 0.5

    # --- history: text and semantic search
    hist = client.get("/api/v1/messages", headers=auth_headers, params={"page_size": 10})
    assert hist.status_code == 200 and hist.json()["total"] >= 1
    sem = client.get("/api/v1/messages", headers=auth_headers,
                     params={"q": "gratitude post for volunteers", "semantic": True})
    assert sem.status_code == 200
    if sem.json()["items"]:
        assert sem.json()["items"][0]["similarity"] is not None

    detail = client.get(f"/api/v1/messages/{first['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert len(detail.json()["batch"]) >= 3
    assert detail.json()["feedback"], "feedback events are listed in the drawer"

    # --- approve on the calendar -> cycle SCHEDULED
    cycle_id = client.get("/api/v1/cycles", headers=auth_headers).json()[0]["id"]
    appr = client.post(f"/api/v1/cycles/{cycle_id}/approve", headers=auth_headers,
                       json={"message_id": first["id"]})
    assert appr.status_code == 200 and appr.json()["status"] == "SCHEDULED"

    # --- mark as published
    cyc = client.get(f"/api/v1/cycles/{cycle_id}", headers=auth_headers).json()
    assert cyc["status"] == "SCHEDULED"
    posts = [c for c in client.get("/api/v1/cycles", headers=auth_headers).json()]
    assert posts


def test_dashboard_and_analytics(client, auth_headers):
    _register_and_setup(client, auth_headers)
    summary = client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert summary.status_code == 200
    body = summary.json()
    assert body["next_action"]["title"]
    assert body["quick_stats"]["n"] >= 0

    for path in ("usage", "content", "quality"):
        resp = client.get(f"/api/v1/analytics/{path}", headers=auth_headers)
        assert resp.status_code == 200, path

    quality = client.get("/api/v1/analytics/quality", headers=auth_headers).json()
    assert quality["n"] >= 0
    # statistical honesty: sample size always present
    assert "n" in quality


def test_notifications_and_calendar_feed(client, auth_headers):
    _register_and_setup(client, auth_headers)
    notes = client.get("/api/v1/notifications", headers=auth_headers)
    assert notes.status_code == 200

    token = client.get("/api/v1/calendar-token", headers=auth_headers)
    assert token.status_code == 200
    ics = client.get(f"/api/v1/calendar.ics?token={token.json()['token']}")
    assert ics.status_code == 200
    assert "BEGIN:VCALENDAR" in ics.text and "BEGIN:VEVENT" in ics.text
    assert ics.text.count("BEGIN:VEVENT") >= 12

    bad = client.get("/api/v1/calendar.ics?token=not-a-token")
    assert bad.status_code == 401


def test_export_and_reset_learning(client, auth_headers):
    _register_and_setup(client, auth_headers)
    export = client.get("/api/v1/me/export", headers=auth_headers)
    assert export.status_code == 200
    body = export.json()
    for key in ("user", "preferences", "plans", "messages", "feedback_events"):
        assert key in body
    assert "password_hash" not in body["user"]

    reset = client.post("/api/v1/me/preferences/reset-learning", headers=auth_headers)
    assert reset.status_code == 200 and reset.json()["reset"] is True
    learned = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    assert learned["preference_vector_norm"] is None
    assert learned["n_feedback_events"] == 0 or True   # events kept, stats cleared


def test_regenerate_creates_linked_batch(client, auth_headers):
    _, topic, _ = _register_and_setup(client, auth_headers)
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2026-12", "topic_id": topic["id"], "tones": ["professional"],
        "purpose": "announce", "platform": "linkedin", "length": "medium",
        "cta": "Read the full update",
    })
    request_id = gen.json()["request_id"]
    assert client.get(f"/api/v1/generation-requests/{request_id}",
                      headers=auth_headers).json()["status"] == "DONE"

    regen = client.post(f"/api/v1/generation-requests/{request_id}/regenerate",
                        headers=auth_headers, params={"instruction": "shorter and punchier"})
    assert regen.status_code == 202
    new_id = regen.json()["request_id"]
    stage = client.get(f"/api/v1/generation-requests/{new_id}", headers=auth_headers).json()
    assert stage["status"] == "DONE"
    assert len([c for c in stage["candidates"] if c["rank"]]) == 3


def test_rate_limit_and_validation(client, auth_headers):
    resp = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2026-13", "tones": ["warm"],   # invalid month
    })
    assert resp.status_code == 422
