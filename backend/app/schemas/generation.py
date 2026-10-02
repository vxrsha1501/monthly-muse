"""Generation, review/compare, feedback and history schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GenerateIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")
    topic_id: str | None = None
    topic: str | None = Field(default=None, max_length=120)
    occasion: str | None = Field(default=None, max_length=120)
    audience_id: str | None = None
    tones: list[str] = Field(default_factory=lambda: ["warm"], max_length=2)
    purpose: str = "inform"
    platform: str = "instagram"
    length: str = "medium"
    language: str = "en"
    keywords: list[str] = Field(default_factory=list, max_length=6)
    cta: str | None = Field(default=None, max_length=200)
    additional_instructions: str | None = Field(default=None, max_length=500)
    cycle_id: str | None = None
    plan_id: str | None = None


class GenerateOut(BaseModel):
    request_id: str
    status: str


class FeatureBreakdown(BaseModel):
    relevance: float = 0.0
    tone: float = 0.0
    personalization: float = 0.0
    novelty: float = 0.0
    length: float = 0.0
    history: float = 0.0


class NearestPast(BaseModel):
    id: str | None = None
    cosine: float | None = None
    band: str = "Fresh"
    preview: str | None = None
    month: str | None = None


class CandidateOut(BaseModel):
    id: str
    rank: int | None = None
    text: str
    style_label: str | None = None
    tone: str | None = None
    score: float | None = None
    p_rank1: float | None = None
    confidence: str = "medium"
    word_count: int | None = None
    char_count: int | None = None
    is_fallback: bool = False
    status: str = "shown"
    filter_reason: str | None = None
    features: FeatureBreakdown = FeatureBreakdown()
    nearest_past: NearestPast = NearestPast()
    explain: dict = Field(default_factory=dict)
    selected: bool = False
    final_text: str | None = None


class StageOut(BaseModel):
    id: str
    status: str
    stage: str | None = None
    ui_stage: str | None = None
    provider: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    is_fallback: bool = False
    candidates: list[CandidateOut] = Field(default_factory=list)
    arms: list[list[str]] = Field(default_factory=list)
    retrieval: dict = Field(default_factory=dict)
    embedding_map: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class MessagePatchIn(BaseModel):
    final_text: str = Field(min_length=1, max_length=5000)


class FeedbackIn(BaseModel):
    event_type: str = Field(pattern="^(like|dislike|reject|copy|edit|regenerate|ignore)$")
    reason: str | None = Field(default=None, max_length=40)


class HistoryQuery(BaseModel):
    q: str | None = None
    tone: str | None = None
    topic: str | None = None
    month: str | None = None
    status: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    semantic: bool = False
    page: int = 1
    page_size: int = 20


class HistoryItemOut(BaseModel):
    id: str
    text: str
    month: str | None = None
    topic: str | None = None
    tone: str | None = None
    style_label: str | None = None
    platform: str | None = None
    status: str
    score: float | None = None
    novelty: float | None = None
    created_at: datetime
    similarity: float | None = None

    model_config = {"from_attributes": True}


class ApproveIn(BaseModel):
    message_id: str


class MetricIn(BaseModel):
    impressions: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    clicks: int | None = None
