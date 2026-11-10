"""Seed the demo accounts (definition of done, Section 18.18):

    python scripts/seed.py

Creates four demo users, each with preferences, an audience, a topic,
a Monthly Plan with 12 cycles, two published past months (real generated +
selected messages = history matrix H) and the next month's cycle already
AWAITING_REVIEW with 3 ranked candidates plus feedback events:

  * demo@monthlymuse.app / monthlymuse-demo    - Brew & Bean cafe (Instagram)
  * pro@monthlymuse.app   / monthlymuse-pro     - Northline Consulting (LinkedIn)
  * studio@monthlymuse.app / monthlymuse-studio - Lumen Yoga Studio (Instagram)
  * org@monthlymuse.app   / monthlymuse-org     - GreenStep Community Trust (Facebook)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.models import (AudienceProfile, FeedbackEvent, FeedbackType,  # noqa: E402
                           GeneratedMessage, GenerationRequest, MessageStatus, MonthlyPlan,
                           PostCycle, RequestStatus, ScheduledPost, ScheduledStatus, User,
                           UserPreferences)
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.schemas.auth import RegisterIn  # noqa: E402
from app.schemas.content import PreferencesIn  # noqa: E402
from app.schemas.generation import GenerateIn  # noqa: E402
from app.schemas.plans import PlanIn  # noqa: E402
from app.scheduler.jobs import payload_from_cycle  # noqa: E402
from app.services import account_service, catalog_service, feedback_service, plan_service  # noqa: E402
from app.services.generation_service import run_request, start_generation  # noqa: E402

DEMOS = [
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
        purpose="promote", post_day=1,
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
        purpose="promote", post_day=1,
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
        purpose="announce", post_day=5,
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
        purpose="engage", post_day=10,
        feedback=[("like", 1), ("copy", 3)],
    ),
]

EMAIL = DEMOS[0]["email"]
PASSWORD = DEMOS[0]["password"]


def month_str(offset: int) -> str:
    """offset: 0 = current month, -1 = previous, ..."""
    today = datetime.now(timezone.utc).date()
    month_index = today.year * 12 + (today.month - 1) + offset
    year, month = divmod(month_index, 12)
    return f"{year:04d}-{month + 1:02d}"


def seed_demo(db, cfg: dict) -> None:
    user = db.scalar(select(User).where(User.email == cfg["email"]))
    if user is not None:
        # Idempotent: skip only when the persona + demo data already exist. A user
        # created through the public register API still gets backfilled here.
        has_plan = db.scalar(select(MonthlyPlan.id)
                             .where(MonthlyPlan.user_id == user.id).limit(1)) is not None
        if has_plan:
            print(f"Already seeded: {cfg['email']} - skipping")
            _ensure_review_feedback(db, user, cfg)
            return
        print(f"[{cfg['label']}] {cfg['email']} exists - backfilling persona and demo data")
    else:
        user = account_service.register(db, RegisterIn(
            email=cfg["email"], password=cfg["password"], full_name=cfg["full_name"],
            timezone="Asia/Kolkata", region="India", locale="en",
        ))
    account_service.update_preferences(db, user, PreferencesIn(
        default_platform=cfg["platform"],
        voice_notes=cfg["voice_notes"],
        banned_words=cfg["banned_words"],
        emoji_level=cfg["emoji_level"], length_preset="medium", lead_days=7, notify_email=True,
        variety=cfg["variety"], default_tones=cfg["tones"],
    ))

    audience = catalog_service.create_audience(db, user.id, *cfg["audience"])
    topic = catalog_service.create_topic(db, user.id, *cfg["topic"])

    plan = plan_service.create_plan(db, user, PlanIn(
        name=cfg["plan_name"], topic_id=topic.id, audience_id=audience.id,
        tones=cfg["tones"], purpose=cfg.get("purpose", "promote"), platform=cfg["platform"],
        length_preset="medium", cta_text=cfg["cta"],
        keywords=cfg["keywords"], post_day=cfg.get("post_day", 1), lead_days=7,
    ))
    print(f"[{cfg['label']}] plan created: {plan.name}")

    # --- two published months (builds the history matrix H + feedback learning)
    for offset, pick_rank in ((-2, 2), (-1, 1)):
        payload = GenerateIn(month=month_str(offset), topic_id=topic.id, topic=topic.name,
                             audience_id=audience.id, tones=cfg["tones"], purpose="promote",
                             platform=cfg["platform"], length="medium", language="en",
                             keywords=cfg["keywords"], cta=cfg["cta"])
        req = start_generation(db, user, payload, trigger="manual")
        run_request(req.id)
        db.expire_all()
        chosen = db.scalar(select(GeneratedMessage).where(
            GeneratedMessage.request_id == req.id, GeneratedMessage.rank == pick_rank))
        if chosen is None:
            chosen = db.scalar(select(GeneratedMessage).where(
                GeneratedMessage.request_id == req.id, GeneratedMessage.rank.is_not(None)))
        if chosen is None:
            print(f"  ! no candidates for {payload.month}")
            continue
        feedback_service.handle_select(db, user, chosen.id)

        year, mon = int(payload.month.split("-")[0]), int(payload.month.split("-")[1])
        post_at = datetime(year, mon, 1, 10, 0, tzinfo=timezone.utc)
        cycle = PostCycle(plan_id=plan.id, user_id=user.id,
                          target_month=datetime(year, mon, 1).date(),
                          post_at=post_at, generate_at=post_at - timedelta(days=7),
                          status="PUBLISHED", selected_message_id=chosen.id)
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        db.add(ScheduledPost(cycle_id=cycle.id, message_id=chosen.id, platform=cfg["platform"],
                             scheduled_for=post_at, status=ScheduledStatus.PUBLISHED,
                             published_at=post_at))
        db.commit()
        print(f"  [{cfg['label']}] past month {payload.month}: selected rank {pick_rank} -> PUBLISHED")

    # --- the next month's cycle: generate now so it waits in review
    cycles = plan_service.list_cycles(db, user.id)
    target = next((c for c in cycles if c.status.value == "PLANNED"), None)
    if target is not None:
        payload = GenerateIn(**payload_from_cycle(db, target))
        req = start_generation(db, user, payload, trigger="scheduled", cycle=target)
        run_request(req.id)
        db.expire_all()
        print(f"  [{cfg['label']}] {target.target_month.strftime('%B %Y')} cycle: AWAITING_REVIEW "
              f"(request {req.id})")

        # extra feedback on the review batch (likes / copies) so analytics,
        # arm posteriors and the weight learner have distinct per-user signals
        shown = list(db.scalars(select(GeneratedMessage).where(
            GeneratedMessage.request_id == req.id,
            GeneratedMessage.rank.is_not(None)).order_by(GeneratedMessage.rank)).all())
        for event_type, rank in cfg.get("feedback", []):
            pick = next((m for m in shown if m.rank == rank), None)
            if pick is not None:
                feedback_service.handle_feedback(db, user, pick.id, event_type)
                print(f"  [{cfg['label']}] feedback: {event_type} on rank {rank}")

    print(f"[{cfg['label']}] ready: {cfg['email']} / {cfg['password']}")


def _ensure_review_feedback(db, user, cfg: dict) -> None:
    """Backfill the persona's like/copy events on its review batch (idempotent).
    Every event points at a real GeneratedMessage owned by this user."""
    from app.db.models import FeedbackEvent, FeedbackType, RequestStatus
    req = db.scalar(select(GenerationRequest).where(
        GenerationRequest.user_id == user.id,
        GenerationRequest.status == RequestStatus.DONE)
        .order_by(GenerationRequest.created_at.desc()).limit(1))
    if req is None:
        return
    shown = list(db.scalars(select(GeneratedMessage).where(
        GeneratedMessage.request_id == req.id,
        GeneratedMessage.rank.is_not(None)).order_by(GeneratedMessage.rank)).all())
    for event_type, rank in cfg.get("feedback", []):
        pick = next((m for m in shown if m.rank == rank), None)
        if pick is None:
            continue
        exists = db.scalar(select(FeedbackEvent.id).where(
            FeedbackEvent.message_id == pick.id,
            FeedbackEvent.event_type == FeedbackType(event_type)).limit(1))
        if exists is None:
            feedback_service.handle_feedback(db, user, pick.id, event_type)
            print(f"  [{cfg['label']}] backfilled feedback: {event_type} on rank {rank}")


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        # global seed data (occasions, topics) - idempotent
        catalog_service.seed_global_data(db)
        for cfg in DEMOS:
            seed_demo(db, cfg)

        print("\nDemo accounts ready:")
        for cfg in DEMOS:
            print(f"  {cfg['label']:<10} {cfg['email']} / {cfg['password']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
