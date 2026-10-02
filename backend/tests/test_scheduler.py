"""Automation tests (Section 8): due-cycle preparation, idempotency, reminders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.scheduler.jobs import catch_up, extend_plans, prepare_due_cycles, send_reminders


def _make_plan_due_soon(client, auth_headers):
    topic = client.post("/api/v1/topics", headers=auth_headers,
                        json={"name": "Monthly update", "keywords": ["news"]}).json()
    plan = client.post("/api/v1/monthly-plans", headers=auth_headers, json={
        "name": "Auto plan", "topic_id": topic["id"], "tones": ["warm"],
        "purpose": "inform", "platform": "instagram", "post_day": 1, "lead_days": 7,
        "cta_text": "Read more",
    }).json()
    return plan


def test_prepare_due_cycle_generates_and_notifies(client, auth_headers):
    plan = _make_plan_due_soon(client, auth_headers)
    from app.db.models import CycleStatus, Notification, PostCycle

    db = SessionLocal()
    try:
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        # simulate the lead time arriving an hour ago
        cycle.generate_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
        cycle_id = cycle.id
    finally:
        db.close()

    result = prepare_due_cycles()
    assert result["prepared"] >= 1

    db = SessionLocal()
    try:
        cycle = db.get(PostCycle, cycle_id)
        assert cycle.status == CycleStatus.AWAITING_REVIEW   # state machine: PLANNED -> GENERATING -> AWAITING_REVIEW
        note = db.scalar(select(Notification).where(Notification.cycle_id == cycle_id))
        assert note is not None and note.type.value == "READY_FOR_REVIEW"
    finally:
        db.close()

    # idempotent: a second run must not create another batch
    again = prepare_due_cycles()
    assert again["prepared"] == 0
    db = SessionLocal()
    try:
        from app.db.models import GenerationRequest
        n = len(db.scalars(select(GenerationRequest).where(GenerationRequest.cycle_id == cycle_id)).all())
        assert n == 1
    finally:
        db.close()

    # review screen has three candidates for the cycle
    cycles = client.get("/api/v1/cycles", headers=auth_headers).json()
    target = next(c for c in cycles if c["id"] == cycle_id)
    assert target["status"] == "AWAITING_REVIEW"


def test_extend_plans_is_idempotent(client, auth_headers):
    plan = _make_plan_due_soon(client, auth_headers)
    first = extend_plans()
    second = extend_plans()
    assert first["created"] >= 0
    assert second["created"] == 0, "no duplicate (plan_id, target_month) rows"


def test_catch_up_after_downtime(client, auth_headers):
    plan = _make_plan_due_soon(client, auth_headers)
    from app.db.models import PostCycle
    db = SessionLocal()
    try:
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        cycle.generate_at = datetime.now(timezone.utc) - timedelta(days=3)
        db.commit()
    finally:
        db.close()
    result = catch_up()
    assert result["prepared"] >= 1     # missed runs are recovered on startup


def test_reminders_and_missed_state(client, auth_headers):
    plan = _make_plan_due_soon(client, auth_headers)
    from app.db.models import CycleStatus, Notification, PostCycle
    db = SessionLocal()
    try:
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        cycle.generate_at = datetime.now(timezone.utc) - timedelta(hours=2)
        cycle.post_at = datetime.now(timezone.utc) + timedelta(days=2)   # T-2: reminder due
        db.commit()
        cycle_id = cycle.id
    finally:
        db.close()

    prepare_due_cycles()
    reminders = send_reminders()
    assert reminders["reminders"] >= 1

    db = SessionLocal()
    try:
        note = db.scalar(select(Notification).where(Notification.cycle_id == cycle_id))
        assert note is not None
        # posting date passes with no review -> NEEDS_ATTENTION
        cycle = db.get(PostCycle, cycle_id)
        cycle.post_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()

    send_reminders()
    db = SessionLocal()
    try:
        cycle = db.get(PostCycle, cycle_id)
        assert cycle.status == CycleStatus.NEEDS_ATTENTION
    finally:
        db.close()
