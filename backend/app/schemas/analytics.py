"""Analytics, dashboard (notification) schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NextAction(BaseModel):
    title: str
    subtitle: str | None = None
    cta_label: str | None = None
    cta_href: str | None = None
    status: str | None = None


class UpcomingCycle(BaseModel):
    id: str
    target_month: str
    post_at: datetime
    generate_at: datetime
    status: str
    plan_name: str | None = None


class QuickStats(BaseModel):
    messages_generated: int = 0
    selection_rate: float | None = None
    selection_rate_ci: list[float] | None = None
    avg_novelty: float | None = None
    tone_mix: dict[str, int] = Field(default_factory=dict)
    n: int = 0


class ActivityEvent(BaseModel):
    id: str
    kind: str
    label: str
    created_at: datetime


class DashboardOut(BaseModel):
    next_action: NextAction | None = None
    this_month: UpcomingCycle | None = None
    upcoming: list[UpcomingCycle] = Field(default_factory=list)
    quick_stats: QuickStats = QuickStats()
    recent_activity: list[ActivityEvent] = Field(default_factory=list)


class MetricCard(BaseModel):
    label: str
    value: float | int | None
    detail: str | None = None
    n: int | None = None


class UsageOut(BaseModel):
    cards: list[MetricCard] = Field(default_factory=list)
    weekly: list[dict] = Field(default_factory=list)
    regeneration_rate: float | None = None
    edit_rate: float | None = None
    avg_time_to_approve_hours: float | None = None


class ContentOut(BaseModel):
    tone_distribution: dict[str, int] = Field(default_factory=dict)
    topic_distribution: dict[str, int] = Field(default_factory=dict)
    entropy: float | None = None
    entropy_n: int = 0
    avg_novelty: float | None = None
    novelty_trend: list[dict] = Field(default_factory=list)
    repetition_alerts: list[dict] = Field(default_factory=list)
    length_distribution: list[dict] = Field(default_factory=list)
    month_tone_heatmap: list[dict] = Field(default_factory=list)


class QualityOut(BaseModel):
    avg_selected_score: float | None = None
    score_selection_correlation: float | None = None
    top1_agreement: float | None = None
    mrr: float | None = None
    latency_p50: int | None = None
    latency_p95: int | None = None
    llm_failure_rate: float | None = None
    fallback_rate: float | None = None
    tone_selection_test: dict = Field(default_factory=dict)
    selection_by_tone: list[dict] = Field(default_factory=list)
    n: int = 0


class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    body: str | None
    cycle_id: str | None
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
