"""Monthly Plans, cycle pre-creation, calendar and approval (FR-12, FR-15)."""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.db.models import (CycleStatus, GeneratedMessage, MessageStatus, MonthlyPlan, Notification,
                           NotificationType, PostCycle, ScheduledPost, ScheduledStatus, User, UserPreferences)
from app.schemas.plans import CyclePatch, PlanIn, PlanPatch

logger = logging.getLogger("monthlymuse.plans")

CYCLE_HORIZON = 12


def _local_to_utc(d: date, t: time, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name or "UTC")
    local = datetime.combine(d, t, tzinfo=tz)
    return local.astimezone(timezone.utc)


def compute_cycle_dates(user: User, post_day: int, post_time: time, lead_days: int,
                        start_from: date | None = None) -> list[tuple[date, datetime, datetime]]:
    """Next 12 (target_month, post_at, generate_at) triples, honouring IANA timezones."""
    now = datetime.now(timezone.utc)
    today = start_from or now.date()
    out: list[tuple[date, datetime, datetime]] = []
    year, month = today.year, today.month
    for _ in range(CYCLE_HORIZON + 1):
        if len(out) >= CYCLE_HORIZON:
            break
        last_day = monthrange(year, month)[1]
        day = min(post_day, last_day)          # day 28+ clamps; 'last day' = 28 policy
        post_local_date = date(year, month, day)
        post_at = _local_to_utc(post_local_date, post_time, user.timezone)
        # skip the current month only if its posting moment has already passed
        current = (month == today.month and year == today.year)
        if not (current and post_at <= now):
            generate_at = post_at - timedelta(days=max(0, lead_days))
            out.append((date(year, month, 1), post_at, generate_at))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out


def ensure_cycles(db: Session, user: User, plan: MonthlyPlan) -> list[PostCycle]:
    """Idempotent: unique (plan_id, target_month) means repeated runs create nothing twice."""
    created: list[PostCycle] = []
    existing = {c.target_month for c in db.scalars(
        select(PostCycle).where(PostCycle.plan_id == plan.id)).all()}
    prefs = db.get(UserPreferences, user.id)
    lead = plan.lead_days if plan.lead_days is not None else (prefs.lead_days if prefs else 7)
    for target, post_at, generate_at in compute_cycle_dates(user, plan.post_day, plan.post_time, lead):
        if target in existing:
            continue
        db.add(PostCycle(plan_id=plan.id, user_id=user.id, target_month=target,
                         post_at=post_at, generate_at=generate_at, status=CycleStatus.PLANNED))
        created.append(PostCycle(plan_id=plan.id, user_id=user.id, target_month=target))
    db.commit()
    if created:
        logger.info("cycles_created plan=%s n=%d", plan.id, len(created))
    return created


def create_plan(db: Session, user: User, data: PlanIn) -> MonthlyPlan:
    plan = MonthlyPlan(user_id=user.id, **data.model_dump())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    ensure_cycles(db, user, plan)
    db.refresh(plan)
    return plan


def list_plans(db: Session, user_id: str) -> list[MonthlyPlan]:
    return list(db.scalars(select(MonthlyPlan).where(MonthlyPlan.user_id == user_id)
                           .order_by(MonthlyPlan.created_at.desc())).all())


def get_plan(db: Session, user_id: str, plan_id: str) -> MonthlyPlan:
    plan = db.get(MonthlyPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise NotFound("plan")
    return plan


def patch_plan(db: Session, user: User, plan_id: str, data: PlanPatch) -> MonthlyPlan:
    plan = get_plan(db, user.id, plan_id)
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(plan, key, value)
    db.commit()
    db.refresh(plan)
    # reschedule future cycles when timing or lead time changed
    _reschedule_future(db, user, plan)
    return plan


def pause_plan(db: Session, user_id: str, plan_id: str, active: bool) -> MonthlyPlan:
    plan = get_plan(db, user_id, plan_id)
    plan.is_active = active
    db.commit()
    db.refresh(plan)
    return plan


def delete_plan(db: Session, user_id: str, plan_id: str) -> None:
    plan = get_plan(db, user_id, plan_id)
    db.delete(plan)
    db.commit()


def _reschedule_future(db: Session, user: User, plan: MonthlyPlan) -> None:
    now = datetime.now(timezone.utc)
    for target, post_at, generate_at in compute_cycle_dates(
            user, plan.post_day, plan.post_time, plan.lead_days):
        cycle = db.scalar(select(PostCycle).where(PostCycle.plan_id == plan.id,
                                                  PostCycle.target_month == target))
        if cycle and cycle.status == CycleStatus.PLANNED:
            cycle.post_at = post_at
            cycle.generate_at = generate_at
    db.commit()


# --- cycles ---------------------------------------------------------------

def list_cycles(db: Session, user_id: str, date_from: date | None = None,
                date_to: date | None = None, status: str | None = None) -> list[PostCycle]:
    stmt = select(PostCycle).where(PostCycle.user_id == user_id)
    if date_from:
        stmt = stmt.where(PostCycle.target_month >= date_from)
    if date_to:
        stmt = stmt.where(PostCycle.target_month <= date_to)
    if status:
        stmt = stmt.where(PostCycle.status == status)
    return list(db.scalars(stmt.order_by(PostCycle.target_month)).all())


def get_cycle(db: Session, user_id: str, cycle_id: str) -> PostCycle:
    cycle = db.get(PostCycle, cycle_id)
    if cycle is None or cycle.user_id != user_id:
        raise NotFound("cycle")
    return cycle


def patch_cycle(db: Session, user: User, cycle_id: str, data: CyclePatch) -> PostCycle:
    cycle = get_cycle(db, user.id, cycle_id)
    updates = data.model_dump(exclude_unset=True)
    if "post_at" in updates and updates["post_at"]:
        new_post = updates["post_at"]
        if new_post.tzinfo is None:
            new_post = new_post.replace(tzinfo=timezone.utc)
        cycle.post_at = new_post
        cycle.generate_at = new_post - timedelta(days=max(0, cycle_overrides_lead(db, cycle)))
    if "status" in updates and updates["status"]:
        allowed = {CycleStatus.SKIPPED.value, CycleStatus.PLANNED.value}
        if updates["status"] not in allowed:
            raise ValidationFailed(f"Status may be set to one of {sorted(allowed)}")
        cycle.status = updates["status"]
    if "overrides" in updates and updates["overrides"] is not None:
        cycle.overrides = updates["overrides"]
    db.commit()
    db.refresh(cycle)
    return cycle


def cycle_overrides_lead(db: Session, cycle: PostCycle) -> int:
    plan = db.get(MonthlyPlan, cycle.plan_id)
    return plan.lead_days if plan else 7


def approve_cycle(db: Session, user: User, cycle_id: str, message_id: str) -> tuple[PostCycle, ScheduledPost]:
    """User picked a candidate: link it, move the cycle to SCHEDULED (Section 8.2 step 8)."""
    cycle = get_cycle(db, user.id, cycle_id)
    message = db.get(GeneratedMessage, message_id)
    if message is None or message.user_id != user.id:
        raise NotFound("message")
    final = message.final_text or message.text
    message.text = message.text
    message.final_text = final
    message.status = MessageStatus.SELECTED
    cycle.selected_message_id = message.id
    cycle.status = CycleStatus.SCHEDULED

    plan = db.get(MonthlyPlan, cycle.plan_id)
    post = ScheduledPost(cycle_id=cycle.id, message_id=message.id,
                         platform=plan.platform if plan else "instagram",
                         scheduled_for=cycle.post_at, status=ScheduledStatus.SCHEDULED)
    db.add(post)
    db.commit()
    db.refresh(cycle)
    db.refresh(post)
    logger.info("cycle_approved cycle=%s message=%s", cycle.id, message.id)
    return cycle, post


def mark_published(db: Session, user: User, scheduled_id: str) -> ScheduledPost:
    post = db.get(ScheduledPost, scheduled_id)
    if post is None:
        raise NotFound("scheduled post")
    cycle = db.get(PostCycle, post.cycle_id) if post.cycle_id else None
    post.status = ScheduledStatus.PUBLISHED
    post.published_at = datetime.now(timezone.utc)
    if cycle:
        cycle.status = CycleStatus.PUBLISHED
    db.commit()
    db.refresh(post)
    return post
