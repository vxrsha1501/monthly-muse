"""Idempotent demo accounts with realistic posting history.

Runs at application start-up (and from ``POST /api/v1/admin/seed-demos``) so the
four demo logins exist even on hosts with an ephemeral filesystem - Render's
free tier wipes a local SQLite file on every deploy.

Each persona gets:

* preferences, an audience, a topic and a Monthly Plan with 12 cycles,
* three published past months (August, September and October relative to today)
  with a selected message, a PUBLISHED cycle and a ScheduledPost - the history
  matrix H the pipeline personalises from,
* the next planned cycle already generated and waiting in review, plus
  like/copy feedback so the analytics screens have data.

Seeding is *repairing*, not just create-once: a run interrupted halfway (or a
persona created through the public API) is detected and only its missing
months / review batch are added. History is produced with the template pipeline
so seeding is fast, offline and never spends API quota. Normal user generations
still use the configured provider chain (Gemini primary, template fallback).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm_adapters import GenerationService, TemplateAdapter
from app.ai.pipeline import Pipeline
from app.core.config import settings
from app.core.runtime import Runtime
from app.db.models import (CycleStatus, FeedbackEvent, GeneratedMessage, GenerationRequest,
                           MonthlyPlan, PostCycle, ScheduledPost, ScheduledStatus, Topic, User)
from app.scheduler.jobs import payload_from_cycle
from app.schemas.auth import RegisterIn
from app.schemas.content import PreferencesIn
from app.schemas.generation import GenerateIn
from app.schemas.plans import PlanIn
from app.services import account_service, catalog_service, feedback_service, plan_service
from app.services.generation_service import run_request, start_generation

logger = logging.getLogger("monthlymuse.demo_seed")

# Which past months become published history (offsets from the current month).
HISTORY_OFFSETS = (-3, -2, -1)
HISTORY_RANKS = (2, 1, 1)

DEMOS: list[dict] = [
    dict(
        label="Cafe",
        email="demo@monthlymuse.app",
        password="monthlymuse-demo",
        full_name="Ananya - Brew & Bean",
        voice_notes="Warm neighbourhood cafe. Friendly, never salesy. We say thanks a lot.",
        banned_words=["cheap", "guaranteed", "best ever"],
        emoji_level=1,
        variety=0.7,
        tones=["warm", "playful"],
        platform="instagram",
        audience=("Young professionals", "25-35, city workers who drop in for evening coffee."),
        topic=("Evening coffee loyalty rewards", "Loyalty scheme for the evening rush",
               ["free refill", "evening"]),
        plan_name="Brew & Bean monthly offer",
        cta="Visit us this weekend",
        keywords=["free refill", "evening"],
        purpose="promote",
        post_day=1,
        feedback=[("like", 1), ("copy", 2)],
    ),
    dict(
        label="Consulting",
        email="pro@monthlymuse.app",
        password="monthlymuse-pro",
        full_name="Meera - Northline Consulting",
        voice_notes="Professional B2B consultant. Clear, confident, practical advice, no hype.",
        banned_words=["cheap", "guaranteed", "game-changer"],
        emoji_level=0,
        variety=0.5,
        tones=["professional", "inspirational"],
        platform="linkedin",
        audience=("Small business owners", "Founders and operators of 5-50 person companies."),
        topic=("Year-end planning workshop", "Promote the December planning session",
               ["workshop", "planning"]),
        plan_name="Northline December workshop",
        cta="Reserve your seat",
        keywords=["workshop", "planning"],
        purpose="promote",
        post_day=1,
        feedback=[("like", 3), ("copy", 1)],
    ),
    dict(
        label="Yoga studio",
        email="studio@monthlymuse.app",
        password="monthlymuse-studio",
        full_name="Riya - Lumen Yoga Studio",
        voice_notes="Encouraging neighbourhood studio. Calm, warm, celebrates small progress. "
                    "Never pushy or salesy.",
        banned_words=["guaranteed", "weight loss", "perfect body"],
        emoji_level=2,
        variety=0.8,
        tones=["warm", "inspirational"],
        platform="instagram",
        audience=("Local members", "25-45, neighbourhood regulars who book classes on mobile."),
        topic=("New beginner yoga batches", "Announce the new batch timetable for the month",
               ["beginner", "batch", "timetable"]),
        plan_name="Lumen monthly timetable",
        cta="Book your first class",
        keywords=["beginner", "batch"],
        purpose="announce",
        post_day=5,
        feedback=[("copy", 2), ("like", 1)],
    ),
    dict(
        label="Community",
        email="org@monthlymuse.app",
        password="monthlymuse-org",
        full_name="Daniel - GreenStep Community Trust",
        voice_notes="Local non-profit. Grateful, factual, volunteer-first. Numbers and names, "
                    "no marketing fluff.",
        banned_words=["act now", "limited offer", "donate today"],
        emoji_level=0,
        variety=0.5,
        tones=["professional", "formal"],
        platform="facebook",
        audience=("Volunteers and donors", "Local volunteers and monthly donors aged 30-65."),
        topic=("Monthly volunteer drive", "Recruit volunteers for the monthly clean-up drive",
               ["volunteer", "clean-up"]),
        plan_name="GreenStep monthly drive",
        cta="Sign up to volunteer",
        keywords=["volunteer", "clean-up"],
        purpose="engage",
        post_day=10,
        feedback=[("like", 1), ("copy", 3)],
    ),
]


def month_str(offset: int, today: date | None = None) -> str:
    """offset: 0 = current month, -1 = previous, ..."""
    today = today or datetime.now(timezone.utc).date()
    month_index = today.year * 12 + (today.month - 1) + offset
    year, month = divmod(month_index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _month_bounds(month: str) -> tuple[date, datetime]:
    year, mon = (int(part) for part in month.split("-"))
    return date(year, mon, 1), datetime(year, mon, 1, 10, 0, tzinfo=timezone.utc)


def _template_pipeline() -> Pipeline:
    """Template-only pipeline: deterministic, offline, no API calls."""
    runtime = Runtime.init()
    return Pipeline(embedder=runtime.embedder, tone_classifier=runtime.tone_classifier,
                    generator=GenerationService(TemplateAdapter()),
                    seed=settings.random_seed)


def _backdate(db: Session, request_id: str, when: datetime) -> None:
    """Stamp a historical batch with its real posting month, not 'today'."""
    req = db.get(GenerationRequest, request_id)
    if req is not None:
        req.created_at = when
    for row in db.scalars(select(GeneratedMessage)
                          .where(GeneratedMessage.request_id == request_id)).all():
        row.created_at = when
    for event in db.scalars(select(FeedbackEvent)
                            .where(FeedbackEvent.request_id == request_id)).all():
        event.created_at = when
    db.commit()


def _apply_preferences(db: Session, user: User, cfg: dict) -> None:
    account_service.update_preferences(db, user, PreferencesIn(
        default_platform=cfg["platform"],
        voice_notes=cfg["voice_notes"],
        banned_words=cfg["banned_words"],
        emoji_level=cfg["emoji_level"],
        length_preset="medium", lead_days=7, notify_email=True,
        variety=cfg["variety"], default_tones=cfg["tones"],
    ))


def _create_plan(db: Session, user: User, cfg: dict, topic: Topic, audience) -> MonthlyPlan:
    return plan_service.create_plan(db, user, PlanIn(
        name=cfg["plan_name"], topic_id=topic.id, audience_id=audience.id,
        tones=cfg["tones"], purpose=cfg["purpose"], platform=cfg["platform"],
        length_preset="medium", cta_text=cfg["cta"],
        keywords=cfg["keywords"], post_day=cfg["post_day"], lead_days=7,
    ))


def _ensure_persona(db: Session, cfg: dict) -> tuple[User, MonthlyPlan, str]:
    """Return the persona's user + plan, creating whichever is missing."""
    user = db.scalar(select(User).where(User.email == cfg["email"]))
    if user is None:
        user = account_service.register(db, RegisterIn(
            email=cfg["email"], password=cfg["password"], full_name=cfg["full_name"],
            timezone="Asia/Kolkata", region="India", locale="en",
        ))
        _apply_preferences(db, user, cfg)
        audience = catalog_service.create_audience(db, user.id, *cfg["audience"])
        topic = catalog_service.create_topic(db, user.id, *cfg["topic"])
        return user, _create_plan(db, user, cfg, topic, audience), "created"

    plan = db.scalar(select(MonthlyPlan).where(MonthlyPlan.user_id == user.id)
                     .order_by(MonthlyPlan.created_at).limit(1))
    if plan is None:
        _apply_preferences(db, user, cfg)
        audience = catalog_service.create_audience(db, user.id, *cfg["audience"])
        topic = catalog_service.create_topic(db, user.id, *cfg["topic"])
        return user, _create_plan(db, user, cfg, topic, audience), "created"
    return user, plan, "exists"


def _topic_name(db: Session, plan: MonthlyPlan) -> str:
    topic = db.get(Topic, plan.topic_id) if plan.topic_id else None
    return topic.name if topic else ""


def _seed_history(db: Session, cfg: dict, user: User, plan: MonthlyPlan,
                  pipeline: Pipeline) -> int:
    """Generate only the published history months this plan is missing."""
    done = {c.target_month for c in db.scalars(
        select(PostCycle).where(PostCycle.plan_id == plan.id,
                                PostCycle.status == CycleStatus.PUBLISHED)).all()}
    added = 0
    for offset, pick_rank in zip(HISTORY_OFFSETS, HISTORY_RANKS):
        month = month_str(offset)
        target_month, post_at = _month_bounds(month)
        if target_month in done:
            continue

        payload = GenerateIn(
            month=month, topic_id=plan.topic_id, topic=_topic_name(db, plan),
            audience_id=plan.audience_id,
            tones=list(plan.tones or cfg["tones"]), purpose=plan.purpose,
            platform=plan.platform, length="medium", language="en",
            keywords=list(plan.keywords or cfg["keywords"]),
            cta=plan.cta_text or cfg["cta"],
        )
        req = start_generation(db, user, payload, trigger="scheduled")
        run_request(req.id, pipeline_override=pipeline)
        db.expire_all()

        chosen = db.scalar(select(GeneratedMessage).where(
            GeneratedMessage.request_id == req.id, GeneratedMessage.rank == pick_rank))
        if chosen is None:
            chosen = db.scalar(select(GeneratedMessage).where(
                GeneratedMessage.request_id == req.id,
                GeneratedMessage.rank.is_not(None)).order_by(GeneratedMessage.rank))
        if chosen is None:
            logger.warning("demo_seed no candidates user=%s month=%s", cfg["email"], month)
            continue

        feedback_service.handle_select(db, user, chosen.id)
        cycle = PostCycle(plan_id=plan.id, user_id=user.id, target_month=target_month,
                          post_at=post_at, generate_at=post_at - timedelta(days=7),
                          status=CycleStatus.PUBLISHED, selected_message_id=chosen.id,
                          created_at=post_at)
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        db.add(ScheduledPost(cycle_id=cycle.id, message_id=chosen.id,
                             platform=cfg["platform"], scheduled_for=post_at,
                             status=ScheduledStatus.PUBLISHED, published_at=post_at))
        db.commit()
        _backdate(db, req.id, post_at)
        added += 1
        logger.info("demo_seed %s history %s rank=%s", cfg["email"], month, pick_rank)
    return added


def _seed_review(db: Session, cfg: dict, user: User, plan: MonthlyPlan,
                 pipeline: Pipeline) -> bool:
    """Generate a review batch only when the plan has none waiting."""
    waiting = db.scalar(select(PostCycle.id).where(
        PostCycle.plan_id == plan.id,
        PostCycle.status == CycleStatus.AWAITING_REVIEW).limit(1))
    if waiting is not None:
        return False

    target = next((c for c in plan_service.list_cycles(db, user.id)
                   if c.plan_id == plan.id and c.status == CycleStatus.PLANNED), None)
    if target is None:
        return False

    payload = GenerateIn(**payload_from_cycle(db, target))
    req = start_generation(db, user, payload, trigger="scheduled", cycle=target)
    run_request(req.id, pipeline_override=pipeline)
    db.expire_all()

    shown = list(db.scalars(select(GeneratedMessage).where(
        GeneratedMessage.request_id == req.id,
        GeneratedMessage.rank.is_not(None)).order_by(GeneratedMessage.rank)).all())
    for event_type, rank in cfg.get("feedback", []):
        pick = next((m for m in shown if m.rank == rank), None)
        if pick is not None:
            feedback_service.handle_feedback(db, user, pick.id, event_type)
    return True


def seed_demo_accounts(db: Session) -> dict:
    """Create or repair every demo persona. Safe to call on each start-up."""
    pipeline = _template_pipeline()
    summary: dict = {"created": 0, "repaired": 0, "exists": 0, "accounts": []}
    for cfg in DEMOS:
        try:
            user, plan, state = _ensure_persona(db, cfg)
            added = _seed_history(db, cfg, user, plan, pipeline)
            reviewed = _seed_review(db, cfg, user, plan, pipeline)
            if state == "created":
                outcome = "created"
            elif added or reviewed:
                outcome = "repaired"
            else:
                outcome = "exists"
            summary[outcome] += 1
            summary["accounts"].append({
                "email": cfg["email"], "status": outcome,
                "history_added": added, "review_created": reviewed,
            })
        except Exception:  # noqa: BLE001 - one persona must not block the others
            db.rollback()
            logger.exception("demo_seed_failed email=%s", cfg["email"])
            summary["accounts"].append({"email": cfg["email"], "status": "failed"})
    logger.info("demo_seed %s", summary)
    return summary
