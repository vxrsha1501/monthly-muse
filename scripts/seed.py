"""Seed the demo accounts (definition of done, Section 18.18):

    python scripts/seed.py

Creates two demo users, each with preferences, an audience, a topic,
a Monthly Plan with 12 cycles, two published past months (real generated +
selected messages = history matrix H) and the next month's cycle already
AWAITING_REVIEW with 3 ranked candidates:

  * demo@monthlymuse.app / monthlymuse-demo - Brew & Bean cafe (Instagram)
  * pro@monthlymuse.app   / monthlymuse-pro  - Northline Consulting (LinkedIn)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.models import (AudienceProfile, GeneratedMessage, MessageStatus, MonthlyPlan,  # noqa: E402
                           PostCycle, ScheduledPost, ScheduledStatus, User, UserPreferences)
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
    existing = db.scalar(select(User).where(User.email == cfg["email"]))
    if existing is not None:
        print(f"Already exists: {cfg['email']} / {cfg['password']} - skipping")
        return

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
        tones=cfg["tones"], purpose="promote", platform=cfg["platform"],
        length_preset="medium", cta_text=cfg["cta"],
        keywords=cfg["keywords"], post_day=1, lead_days=7,
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

    print(f"[{cfg['label']}] ready: {cfg['email']} / {cfg['password']}")


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
