"""Cycles: calendar data, reschedule/skip, generate-now, approve, publish, metrics (FR-15)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.core.errors import NotFound
from app.db.models import (FeedbackType, GeneratedMessage, GenerationRequest, MessageStatus,
                           PostMetric, ScheduledPost, User)
from app.schemas.generation import ApproveIn, GenerateIn, GenerateOut, MetricIn
from app.schemas.plans import CycleOut, CyclePatch
from app.services import generation_service, plan_service
from app.services.generation_service import run_request

router = APIRouter(tags=["cycles"])


def _to_out(db: Session, cycle) -> CycleOut:
    plan = plan_service.get_plan(db, cycle.user_id, cycle.plan_id) if cycle.plan_id else None
    out = CycleOut.model_validate(cycle)
    out.plan_name = plan.name if plan else None
    out.platform = plan.platform if plan else None
    latest = db.scalars(select(GenerationRequest)
                        .where(GenerationRequest.cycle_id == cycle.id)
                        .order_by(GenerationRequest.created_at.desc()).limit(1)).first()
    out.latest_request_id = latest.id if latest else None
    return out


@router.get("/cycles", response_model=list[CycleOut])
def list_cycles(date_from: date | None = Query(default=None, alias="from"),
                date_to: date | None = Query(default=None, alias="to"),
                status: str | None = None,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_to_out(db, c) for c in plan_service.list_cycles(db, user.id, date_from, date_to, status)]


@router.get("/cycles/{cycle_id}", response_model=CycleOut)
def get_cycle(cycle_id: str, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> CycleOut:
    return _to_out(db, plan_service.get_cycle(db, user.id, cycle_id))


@router.patch("/cycles/{cycle_id}", response_model=CycleOut)
def patch_cycle(cycle_id: str, data: CyclePatch, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> CycleOut:
    return _to_out(db, plan_service.patch_cycle(db, user, cycle_id, data))


@router.post("/cycles/{cycle_id}/generate", response_model=GenerateOut, status_code=202)
def generate_now(cycle_id: str, background: BackgroundTasks,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> GenerateOut:
    """Early generation ('Generate now' on the calendar)."""
    cycle = plan_service.get_cycle(db, user.id, cycle_id)
    plan = plan_service.get_plan(db, user.id, cycle.plan_id)
    from app.db.models import Topic, AudienceProfile, MonthlyPlan
    topic = db.get(Topic, plan.topic_id) if plan.topic_id else None
    audience = db.get(AudienceProfile, plan.audience_id) if plan.audience_id else None
    payload = GenerateIn(
        month=cycle.target_month.strftime("%Y-%m"),
        topic_id=plan.topic_id, topic=topic.name if topic else plan.name,
        occasion=cycle.overrides.get("occasion") or plan.occasion,
        audience_id=plan.audience_id,
        tones=list(plan.tones or ["warm"]), purpose=plan.purpose, platform=plan.platform,
        language=plan.language, length=plan.length_preset,
        keywords=list(plan.keywords or []), cta=plan.cta_text,
        additional_instructions=plan.extra_instructions,
        cycle_id=cycle.id, plan_id=plan.id,
    )
    req = generation_service.start_generation(db, user, payload, trigger="scheduled", cycle=cycle)
    background.add_task(run_request, req.id)
    return GenerateOut(request_id=req.id, status=req.status.value)


@router.post("/cycles/{cycle_id}/approve", response_model=CycleOut)
def approve(cycle_id: str, data: ApproveIn, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)) -> CycleOut:
    cycle, _post = plan_service.approve_cycle(db, user, cycle_id, data.message_id)
    return _to_out(db, cycle)


@router.post("/scheduled-posts/{post_id}/mark-published")
def mark_published(post_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    post = db.get(ScheduledPost, post_id)
    if post is None:
        raise NotFound("scheduled post")
    cycle = plan_service.get_cycle(db, user.id, post.cycle_id) if post.cycle_id else None
    if cycle is None and post.cycle_id:
        raise NotFound("scheduled post")
    plan_service.mark_published(db, user, post_id)
    return {"id": post_id, "status": "published"}


@router.post("/scheduled-posts/{post_id}/metrics", status_code=201)
def add_metrics(post_id: str, data: MetricIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> dict:
    post = db.get(ScheduledPost, post_id)
    if post is None or (post.cycle_id and plan_service.get_cycle(db, user.id, post.cycle_id) is None):
        raise NotFound("scheduled post")
    total = sum(v or 0 for v in (data.likes, data.comments, data.shares, data.clicks))
    rate = total / data.impressions if data.impressions else None
    row = PostMetric(scheduled_post_id=post.id, impressions=data.impressions, likes=data.likes,
                     comments=data.comments, shares=data.shares, clicks=data.clicks,
                     engagement_rate=rate, source="manual")
    db.add(row)
    db.commit()
    return {"id": row.id, "engagement_rate": rate}
