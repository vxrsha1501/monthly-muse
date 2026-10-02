"""Scheduler jobs: cycle preparation, reminders, retries, catch-up (Section 8).

Every job records a row in job_runs (audit log) and is idempotent: work only
happens when the cycle is in an eligible state, and cycles are unique per
(plan_id, target_month).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (CycleStatus, FeedbackType, GeneratedMessage, GenerationRequest, JobRun,
                           JobStatus, MonthlyPlan, Notification, NotificationType, PostCycle,
                           RequestStatus, TriggerKind, User)
from app.db.session import SessionLocal

logger = logging.getLogger("monthlymuse.scheduler")

MAX_GENERATION_ATTEMPTS = 3


def _start_job(db: Session, name: str, cycle_id: str | None = None) -> JobRun:
    run = JobRun(job_name=name, cycle_id=cycle_id, status=JobStatus.RUNNING, attempt=1)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish_job(db: Session, run: JobRun, error: str | None = None) -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    run.status = JobStatus.FAILED if error else JobStatus.SUCCESS
    run.error = (error or "")[:500] or None
    db.commit()


def payload_from_cycle(db: Session, cycle: PostCycle) -> dict:
    """Merge plan defaults with per-cycle overrides into a GenerateIn payload."""
    from app.schemas.generation import GenerateIn
    plan = db.get(MonthlyPlan, cycle.plan_id)
    if plan is None:
        raise ValueError(f"cycle {cycle.id} has no plan")
    topic_name = plan.name
    if plan.topic_id:
        from app.db.models import Topic
        topic = db.get(Topic, plan.topic_id)
        if topic:
            topic_name = topic.name
    return dict(
        month=cycle.target_month.strftime("%Y-%m"),
        topic_id=plan.topic_id, topic=topic_name,
        occasion=cycle.overrides.get("occasion") or plan.occasion,
        audience_id=plan.audience_id,
        tones=list(plan.tones or ["warm"]), purpose=plan.purpose, platform=plan.platform,
        language=plan.language, length=plan.length_preset,
        keywords=list(plan.keywords or []), cta=plan.cta_text,
        additional_instructions=plan.extra_instructions,
        cycle_id=cycle.id, plan_id=plan.id,
    )


def _due_cycles(db: Session) -> list[PostCycle]:
    now = datetime.now(timezone.utc)
    return list(db.scalars(
        select(PostCycle).join(MonthlyPlan, MonthlyPlan.id == PostCycle.plan_id)
        .where(PostCycle.status.in_([CycleStatus.PLANNED, CycleStatus.GENERATION_FAILED]),
               PostCycle.generate_at <= now,
               MonthlyPlan.is_active.is_(True),
               MonthlyPlan.auto_generate.is_(True))
        .order_by(PostCycle.generate_at)).all())


def prepare_due_cycles(db: Session | None = None) -> dict:
    """Daily/interval job: create generation requests for cycles at lead time (8.2 steps 2-6)."""
    own = db is None
    db = db or SessionLocal()
    run = _start_job(db, "prepare_cycle")
    prepared, failed = 0, 0
    try:
        from app.services.generation_service import run_request, start_generation
        from app.schemas.generation import GenerateIn

        for cycle in _due_cycles(db):
            # idempotency: skip if a request already exists for this cycle
            existing = db.scalar(select(GenerationRequest).where(
                GenerationRequest.cycle_id == cycle.id,
                GenerationRequest.trigger == TriggerKind.SCHEDULED).limit(1))
            attempts = len(db.scalars(select(GenerationRequest).where(
                GenerationRequest.cycle_id == cycle.id)).all())
            if existing and existing.status in (RequestStatus.QUEUED, RequestStatus.RUNNING, RequestStatus.DONE):
                continue
            if attempts >= MAX_GENERATION_ATTEMPTS and cycle.status == CycleStatus.GENERATION_FAILED:
                continue
            plan = db.get(MonthlyPlan, cycle.plan_id)
            if plan is None:
                continue
            try:
                payload = GenerateIn(**payload_from_cycle(db, cycle))
                user = db.get(User, cycle.user_id)
                if user is None:
                    continue
                req = start_generation(db, user, payload, trigger="scheduled", cycle=cycle)
                run_request(req.id)   # worker process: run in-process (shares code + DB)
                prepared += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("cycle_prepare_failed cycle=%s", cycle.id)
                cycle.status = CycleStatus.GENERATION_FAILED
                db.commit()
                failed += 1
                _finish_job(db, run, error=str(exc))
                run = _start_job(db, "prepare_cycle")
        _finish_job(db, run)
    except Exception as exc:  # pragma: no cover
        _finish_job(db, run, error=str(exc))
        raise
    finally:
        if own:
            db.close()
    logger.info("prepare_due_cycles prepared=%d failed=%d", prepared, failed)
    return {"prepared": prepared, "failed": failed}


def send_reminders(db: Session | None = None) -> dict:
    """Reminders at post_at - 3d and -1d; missed cycles move to NEEDS_ATTENTION (8.2 step 7)."""
    own = db is None
    db = db or SessionLocal()
    run = _start_job(db, "send_reminder")
    sent = 0
    try:
        now = datetime.now(timezone.utc)
        review = list(db.scalars(select(PostCycle).where(
            PostCycle.status == CycleStatus.AWAITING_REVIEW,
            PostCycle.post_at > now)).all())
        for cycle in review:
            days_left = (cycle.post_at - now).total_seconds() / 86400
            if days_left <= 3 or days_left <= 1:
                wanted = NotificationType.REMINDER
                existing = db.scalar(select(Notification).where(
                    Notification.cycle_id == cycle.id, Notification.type == wanted))
                if existing is None and days_left <= 3:
                    db.add(Notification(user_id=cycle.user_id, type=wanted,
                                        title=f"{cycle.target_month.strftime('%B')} post awaiting review",
                                        body=f"{max(0, int(days_left))} day(s) until your posting date - "
                                             "pick one of the three candidates.",
                                        cycle_id=cycle.id))
                    sent += 1
                db.commit()

        missed = list(db.scalars(select(PostCycle).where(
            PostCycle.status == CycleStatus.AWAITING_REVIEW,
            PostCycle.post_at <= now)).all())
        for cycle in missed:
            cycle.status = CycleStatus.NEEDS_ATTENTION
            existing = db.scalar(select(Notification).where(
                Notification.cycle_id == cycle.id, Notification.type == NotificationType.MISSED))
            if existing is None:
                db.add(Notification(user_id=cycle.user_id, type=NotificationType.MISSED,
                                    title=f"{cycle.target_month.strftime('%B')} posting date has passed",
                                    body="This cycle needs attention - review, skip or reschedule it.",
                                    cycle_id=cycle.id))
            db.commit()
            sent += 1
        _finish_job(db, run)
    except Exception as exc:  # pragma: no cover
        _finish_job(db, run, error=str(exc))
        raise
    finally:
        if own:
            db.close()
    return {"reminders": sent}


def catch_up(db: Session | None = None) -> dict:
    """On worker start: enqueue cycles past their generation date (Section 8.5)."""
    logger.info("catch_up_scan")
    return prepare_due_cycles(db)


def extend_plans(db: Session | None = None) -> dict:
    """Keep every active plan topped up with the next 12 cycles."""
    own = db is None
    db = db or SessionLocal()
    try:
        from app.services.plan_service import ensure_cycles
        plans = list(db.scalars(select(MonthlyPlan).where(MonthlyPlan.is_active.is_(True))).all())
        created = 0
        for plan in plans:
            user = db.get(User, plan.user_id)
            if user is None:
                continue
            before = len(list(db.scalars(select(PostCycle).where(PostCycle.plan_id == plan.id)).all()))
            ensure_cycles(db, user, plan)
            after = len(list(db.scalars(select(PostCycle).where(PostCycle.plan_id == plan.id)).all()))
            created += after - before
        return {"created": created}
    finally:
        if own:
            db.close()


def run_all() -> dict:
    """One scheduler tick: extend plans, catch up, remind."""
    extend_plans()
    result = prepare_due_cycles()
    reminders = send_reminders()
    return {**result, **reminders}
