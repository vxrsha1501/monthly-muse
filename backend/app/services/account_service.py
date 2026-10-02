"""Account, preferences and data-portability services (FR-01, FR-19)."""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, Unauthorized, ValidationFailed
from app.core.security import hash_password, verify_password
from app.db.models import (AudienceProfile, FeedbackEvent, GeneratedMessage, MonthlyPlan, Notification,
                           ToneStat, Topic, User, UserPreferences)
from app.schemas.auth import MeUpdate, RegisterIn
from app.schemas.content import PreferencesIn

logger = logging.getLogger("monthlymuse.account")


def register(db: Session, data: RegisterIn) -> User:
    email = data.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise Conflict("An account with this email already exists")
    user = User(
        email=email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        timezone=data.timezone,
        region=data.region,
        locale=data.locale,
    )
    db.add(user)
    db.flush()
    prefs = UserPreferences(user_id=user.id, default_language=data.locale,
                            default_platform="instagram")
    db.add(prefs)
    db.commit()
    db.refresh(user)
    logger.info("user_registered user_id=%s", user.id)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("Incorrect email or password")
    if not user.is_active:
        raise Unauthorized("Account disabled")
    from datetime import datetime, timezone
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user


def update_profile(db: Session, user: User, data: MeUpdate) -> User:
    for field in ("full_name", "timezone", "region", "locale"):
        value = getattr(data, field)
        if value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


def get_preferences(db: Session, user: User) -> UserPreferences:
    prefs = db.get(UserPreferences, user.id)
    if prefs is None:
        prefs = UserPreferences(user_id=user.id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


def update_preferences(db: Session, user: User, data: PreferencesIn) -> UserPreferences:
    prefs = get_preferences(db, user)
    if len(data.banned_words) > 30:
        raise ValidationFailed("At most 30 banned words")
    for field in ("default_language", "default_platform", "voice_notes", "banned_words",
                  "emoji_level", "length_preset", "lead_days", "notify_email",
                  "autopilot", "learning_paused", "variety"):
        setattr(prefs, field, getattr(data, field))
    prefs.lambda_mmr = data.variety
    # onboarding tones initialise the Beta priors (alpha = 2, Section 7.4)
    for tone in data.default_tones:
        from app.ai.arms import STRUCTURES
        for structure in STRUCTURES:
            stat = db.scalar(select(ToneStat).where(ToneStat.user_id == user.id,
                                                    ToneStat.tone == tone,
                                                    ToneStat.structure == structure,
                                                    ToneStat.audience_id.is_(None)))
            if stat is None:
                db.add(ToneStat(user_id=user.id, tone=tone, structure=structure, alpha=2.0, beta=1.0))
            elif stat.n_shown == 0 and stat.n_selected == 0:
                stat.alpha = max(stat.alpha, 2.0)
    db.commit()
    db.refresh(prefs)
    return prefs


def reset_learning(db: Session, user: User) -> dict:
    """Clear the learned vector, weights, tone stats and length statistics (Section 7.4)."""
    prefs = get_preferences(db, user)
    prefs.preference_vector = None
    prefs.scoring_weights = {}
    prefs.length_mean = prefs.length_std = None
    prefs.length_n = 0
    db.execute(delete(ToneStat).where(ToneStat.user_id == user.id))
    db.commit()
    logger.info("learning_reset user_id=%s", user.id)
    return {"reset": True}


def export_data(db: Session, user: User) -> dict:
    """GET /me/export - all of the user's data as JSON (Section 3.7)."""
    def rows(model, *where):
        result = db.scalars(select(model).where(model.user_id == user.id, *where)).all()
        out = []
        for row in result:
            item = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "password_hash"}
            for key, value in list(item.items()):
                if hasattr(value, "isoformat"):
                    item[key] = value.isoformat()
                elif isinstance(value, bytes):
                    item[key] = f"<vector:{len(value)} bytes>"
            out.append(item)
        return out

    return {
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name,
                 "timezone": user.timezone, "region": user.region, "created_at": user.created_at.isoformat()},
        "preferences": rows(UserPreferences),
        "audiences": rows(AudienceProfile),
        "topics": rows(Topic),
        "plans": rows(MonthlyPlan),
        "messages": rows(GeneratedMessage),
        "feedback_events": rows(FeedbackEvent),
        "tone_stats": rows(ToneStat),
        "notifications": rows(Notification),
    }


def delete_account(db: Session, user: User) -> None:
    db.delete(user)   # cascades: preferences, plans, cycles, messages, feedback, stats
    db.commit()
    logger.info("account_deleted user_id=%s", user.id)


def require_owned(db: Session, model, obj_id: str, user_id: str, label: str = "resource"):
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user_id:
        raise NotFound(label)
    return obj
