"""SQLAlchemy models: the full Section 9 schema (16 tables).

All PKs are UUID strings; all timestamps are timezone-aware UTC.
Enums are stored as portable strings (native_enum=False) so the schema is
identical on SQLite and PostgreSQL.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, LargeBinary,
    SmallInteger, String, Text, Time, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base, UTCDateTime


def gen_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:
        return self.value


def enum_col(enum_cls: type[enum.Enum], name: str, **kw) -> Mapped[object]:
    """Portable enum column: stored as its value string (identical on SQLite and Postgres)."""
    return mapped_column(
        SAEnum(enum_cls, name=name, native_enum=False,
               values_callable=lambda e: [m.value for m in e]),
        **kw,
    )


# --- enums ----------------------------------------------------------------

class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class CycleStatus(StrEnum):
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    GENERATION_FAILED = "GENERATION_FAILED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    REGENERATE = "regenerate"


class RequestStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class MessageStatus(StrEnum):
    SHOWN = "SHOWN"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"
    FILTERED = "FILTERED"
    ARCHIVED = "ARCHIVED"


class FeedbackType(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"
    SELECT = "select"
    REJECT = "reject"
    COPY = "copy"
    EDIT = "edit"
    REGENERATE = "regenerate"
    IGNORE = "ignore"


class ScheduledStatus(StrEnum):
    SCHEDULED = "scheduled"
    REMINDED = "reminded"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OccasionCategory(StrEnum):
    SEASON = "season"
    HOLIDAY = "holiday"
    AWARENESS = "awareness"
    BUSINESS = "business"
    CUSTOM = "custom"


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRIED = "retried"


class NotificationType(StrEnum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REMINDER = "REMINDER"
    FAILED = "FAILED"
    MISSED = "MISSED"


# --- identity -------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    locale: Mapped[str] = mapped_column(String(10), default="en")
    region: Mapped[str] = mapped_column(String(40), default="India")
    role = enum_col(UserRole, "role", default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    preferences: Mapped["UserPreferences | None"] = relationship(back_populates="user", uselist=False,
                                                                 cascade="all, delete-orphan")


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    default_language: Mapped[str] = mapped_column(String(10), default="en")
    default_platform: Mapped[str] = mapped_column(String(30), default="instagram")
    voice_notes: Mapped[str | None] = mapped_column(Text)
    banned_words: Mapped[list] = mapped_column(JSON, default=list)
    emoji_level: Mapped[int] = mapped_column(SmallInteger, default=1)
    length_mean: Mapped[float | None] = mapped_column(Float)      # learned (Section 5.2)
    length_std: Mapped[float | None] = mapped_column(Float)
    length_n: Mapped[int] = mapped_column(Integer, default=0)
    length_preset: Mapped[str] = mapped_column(String(20), default="medium")
    preference_vector: Mapped[bytes | None] = mapped_column(LargeBinary)   # u, float32 blob
    scoring_weights: Mapped[dict] = mapped_column(JSON, default=dict)      # {R,T,P,N,Len,Hist}
    tone_counts: Mapped[dict] = mapped_column(JSON, default=dict)          # {audience_key: {tone: n}} (Section 5.1.3)
    lambda_mmr: Mapped[float] = mapped_column(Float, default=0.7)
    lead_days: Mapped[int] = mapped_column(Integer, default=7)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False)
    learning_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    variety: Mapped[float] = mapped_column(Float, default=0.7)     # "More variety" slider -> lambda
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="preferences")


# --- content setup --------------------------------------------------------

class AudienceProfile(Base):
    __tablename__ = "audience_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)  # NULL = global seed
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)


class Occasion(Base):
    __tablename__ = "occasions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)  # NULL = global
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day: Mapped[int | None] = mapped_column(SmallInteger)
    date_rule: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(40), default="global")
    category = enum_col(OccasionCategory, "category", default=OccasionCategory.HOLIDAY)
    description: Mapped[str | None] = mapped_column(Text)   # one-line fact passed to the LLM
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)


# --- planning and automation ----------------------------------------------

class MonthlyPlan(Base):
    __tablename__ = "monthly_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("topics.id", ondelete="SET NULL"))
    audience_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("audience_profiles.id", ondelete="SET NULL"))
    tones: Mapped[list] = mapped_column(JSON, default=list)          # up to 2 tones
    purpose: Mapped[str] = mapped_column(String(20), default="inform")
    platform: Mapped[str] = mapped_column(String(30), default="instagram")
    language: Mapped[str] = mapped_column(String(10), default="en")
    length_preset: Mapped[str] = mapped_column(String(20), default="medium")
    cta_text: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    extra_instructions: Mapped[str | None] = mapped_column(Text)
    occasion: Mapped[str | None] = mapped_column(String(120))
    post_day: Mapped[int] = mapped_column(SmallInteger, default=1)    # 1-28 or 28 = last day
    post_time: Mapped[time] = mapped_column(Time, default=time(10, 0))
    lead_days: Mapped[int] = mapped_column(SmallInteger, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    cycles: Mapped[list["PostCycle"]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class PostCycle(Base):
    __tablename__ = "post_cycles"
    __table_args__ = (
        UniqueConstraint("plan_id", "target_month", name="uq_cycle_plan_month"),
        Index("ix_cycles_status_generate", "status", "generate_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    plan_id: Mapped[str] = mapped_column(String(32), ForeignKey("monthly_plans.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_month: Mapped[date] = mapped_column(Date, nullable=False)   # first day of month
    post_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    generate_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status = enum_col(CycleStatus, "status", default=CycleStatus.PLANNED, index=True)
    selected_message_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("generated_messages.id", ondelete="SET NULL"))
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    plan: Mapped[MonthlyPlan] = relationship(back_populates="cycles")


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    cycle_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("post_cycles.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("generated_messages.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(30), default="instagram")
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status = enum_col(ScheduledStatus, "status", default=ScheduledStatus.SCHEDULED)
    external_post_id: Mapped[str | None] = mapped_column(String(120))    # V2 publisher
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type = enum_col(NotificationType, "type")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    cycle_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("post_cycles.id", ondelete="SET NULL"))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    emailed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    job_name: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("post_cycles.id", ondelete="SET NULL"))
    status = enum_col(JobStatus, "status", default=JobStatus.RUNNING)
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


# --- AI generation --------------------------------------------------------

class GenerationRequest(Base):
    __tablename__ = "generation_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("post_cycles.id", ondelete="SET NULL"), index=True)
    parent_request_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("generation_requests.id", ondelete="SET NULL"))
    trigger = enum_col(TriggerKind, "trigger", default=TriggerKind.MANUAL)
    input: Mapped[dict] = mapped_column(JSON, default=dict)          # validated form input
    intent_text: Mapped[str | None] = mapped_column(Text)
    intent_embedding: Mapped[bytes | None] = mapped_column(LargeBinary)  # q
    status = enum_col(RequestStatus, "status", default=RequestStatus.QUEUED)
    stage: Mapped[str | None] = mapped_column(String(40))            # drives the UI tracker
    provider: Mapped[str | None] = mapped_column(String(40))         # adapter name or 'template'
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer)
    weights_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    messages: Mapped[list["GeneratedMessage"]] = relationship(back_populates="request", cascade="all, delete-orphan")


class GeneratedMessage(Base):
    __tablename__ = "generated_messages"
    __table_args__ = (Index("ix_messages_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    request_id: Mapped[str] = mapped_column(String(32), ForeignKey("generation_requests.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)               # original generated text
    final_text: Mapped[str | None] = mapped_column(Text)                  # after user edits
    intended_tone: Mapped[str | None] = mapped_column(String(40))
    style_label: Mapped[str | None] = mapped_column(String(80))           # "Warm - Story opening"
    word_count: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)          # e_c (final_text once edited)
    f_relevance: Mapped[float | None] = mapped_column(Float)
    f_tone: Mapped[float | None] = mapped_column(Float)
    f_personalization: Mapped[float | None] = mapped_column(Float)
    f_novelty: Mapped[float | None] = mapped_column(Float)
    f_length: Mapped[float | None] = mapped_column(Float)
    f_history: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    p_rank1: Mapped[float | None] = mapped_column(Float)                  # Monte-Carlo confidence
    rank: Mapped[int | None] = mapped_column(SmallInteger)                # 1-3 if displayed
    nearest_history_id: Mapped[str | None] = mapped_column(String(32))
    nearest_cosine: Mapped[float | None] = mapped_column(Float)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)     # template-generated
    status = enum_col(MessageStatus, "status", default=MessageStatus.SHOWN)
    explain: Mapped[dict] = mapped_column(JSON, default=dict)             # reasons, PCA coords
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    request: Mapped[GenerationRequest] = relationship(back_populates="messages")


# --- learning -------------------------------------------------------------

class FeedbackEvent(Base):
    __tablename__ = "feedback_events"
    __table_args__ = (Index("ix_feedback_unprocessed", "processed", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("generated_messages.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("generation_requests.id", ondelete="SET NULL"))
    event_type = enum_col(FeedbackType, "event_type")
    reward: Mapped[float] = mapped_column(Float, default=0.0)             # signed value (Section 7.3)
    reason: Mapped[str | None] = mapped_column(String(40))                # reject-reason chip
    edit_distance: Mapped[int | None] = mapped_column(Integer)            # token-level change size
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ToneStat(Base):
    __tablename__ = "tone_stats"
    __table_args__ = (UniqueConstraint("user_id", "audience_id", "tone", "structure", name="uq_arm"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    audience_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("audience_profiles.id", ondelete="CASCADE"))
    tone: Mapped[str] = mapped_column(String(40), nullable=False)
    structure: Mapped[str] = mapped_column(String(40), nullable=False)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)              # Beta posterior
    beta: Mapped[float] = mapped_column(Float, default=1.0)
    n_shown: Mapped[int] = mapped_column(Integer, default=0)
    n_selected: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# --- analytics ------------------------------------------------------------

class PostMetric(Base):
    __tablename__ = "post_metrics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    scheduled_post_id: Mapped[str] = mapped_column(String(32), ForeignKey("scheduled_posts.id", ondelete="CASCADE"), index=True)
    impressions: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    clicks: Mapped[int | None] = mapped_column(Integer)
    engagement_rate: Mapped[float | None] = mapped_column(Float)          # generated column conceptually
    source = mapped_column(String(10), default="manual")                  # manual | api
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AnalyticsDaily(Base):
    __tablename__ = "analytics_daily"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_analytics_user_day"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    generated: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[int] = mapped_column(Integer, default=0)
    regenerated: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    edited: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float | None] = mapped_column(Float)
    avg_novelty: Mapped[float | None] = mapped_column(Float)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float)
    llm_failures: Mapped[int] = mapped_column(Integer, default=0)
    fallbacks: Mapped[int] = mapped_column(Integer, default=0)
