"""Seed the demo account (definition of done, Section 18.18):

    python scripts/seed.py

Creates demo@monthlymuse.app / monthlymuse-demo with:
  * preferences, an audience and topics
  * a Monthly Plan with 12 cycles
  * two published past months (real generated + selected messages = history matrix H)
  * the next month's cycle already AWAITING_REVIEW with 3 ranked candidates
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

EMAIL = "demo@monthlymuse.app"
PASSWORD = "monthlymuse-demo"


def month_str(offset: int) -> str:
    """offset: 0 = current month, -1 = previous, ..."""
    today = datetime.now(timezone.utc).date()
    month_index = today.year * 12 + (today.month - 1) + offset
    year, month = divmod(month_index, 12)
    return f"{year:04d}-{month + 1:02d}"


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.email == EMAIL))
        if existing is not None:
            print(f"Demo account already exists: {EMAIL} / {PASSWORD}")
            return

        catalog_service.seed_global_data(db)

        user = account_service.register(db, RegisterIn(
            email=EMAIL, password=PASSWORD, full_name="Ananya - Brew & Bean",
            timezone="Asia/Kolkata", region="India", locale="en",
        ))
        account_service.update_preferences(db, user, PreferencesIn(
            default_platform="instagram",
            voice_notes="Warm neighbourhood cafe. Friendly, never salesy. We say thanks a lot.",
            banned_words=["cheap", "guaranteed", "best ever"],
            emoji_level=1, length_preset="medium", lead_days=7, notify_email=True,
            variety=0.7, default_tones=["warm", "playful"],
        ))

        audience = catalog_service.create_audience(
            db, user.id, "Young professionals", "25-35, city workers who drop in for evening coffee.")
        topic = catalog_service.create_topic(
            db, user.id, "Evening coffee loyalty rewards",
            "Loyalty scheme for the evening rush", ["free refill", "evening"])

        plan = plan_service.create_plan(db, user, PlanIn(
            name="Brew & Bean monthly offer", topic_id=topic.id, audience_id=audience.id,
            tones=["warm", "playful"], purpose="promote", platform="instagram",
            length_preset="medium", cta_text="Visit us this weekend",
            keywords=["free refill", "evening"], post_day=1, lead_days=7,
        ))
        print(f"Plan created: {plan.name}")

        # --- two published months (builds the history matrix H + feedback learning)
        for offset, pick_rank in ((-2, 2), (-1, 1)):
            payload = GenerateIn(
                month=month_str(offset), topic_id=topic.id, topic=topic.name,
                audience_id=audience.id, tones=["warm"], purpose="promote",
                platform="instagram", length="medium", language="en",
                keywords=["free refill", "evening"], cta="Visit us this weekend",
            )
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
            db.add(ScheduledPost(cycle_id=cycle.id, message_id=chosen.id, platform="instagram",
                                 scheduled_for=post_at, status=ScheduledStatus.PUBLISHED,
                                 published_at=post_at))
            db.commit()
            print(f"  past month {payload.month}: selected rank {pick_rank} -> PUBLISHED")

        # --- the next month's cycle: generate now so it waits in review
        cycles = plan_service.list_cycles(db, user.id)
        target = next((c for c in cycles if c.status.value == "PLANNED"), None)
        if target is not None:
            payload = GenerateIn(**payload_from_cycle(db, target))
            req = start_generation(db, user, payload, trigger="scheduled", cycle=target)
            run_request(req.id)
            db.expire_all()
            print(f"  {target.target_month.strftime('%B %Y')} cycle: AWAITING_REVIEW "
                  f"(request {req.id})")

        print("\nDemo account ready:")
        print(f"  email:    {EMAIL}")
        print(f"  password: {PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
