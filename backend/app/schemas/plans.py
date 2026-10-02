"""Monthly plans and cycles schemas."""
from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, Field


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    topic_id: str | None = None
    audience_id: str | None = None
    tones: list[str] = Field(default_factory=lambda: ["warm"], max_length=2)
    purpose: str = "inform"
    platform: str = "instagram"
    language: str = "en"
    length_preset: str = "medium"
    cta_text: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=6)
    extra_instructions: str | None = Field(default=None, max_length=500)
    occasion: str | None = None
    post_day: int = Field(default=1, ge=1, le=28)
    post_time: time = time(10, 0)
    lead_days: int = Field(default=7, ge=0, le=28)
    is_active: bool = True
    auto_generate: bool = True


class PlanPatch(BaseModel):
    name: str | None = None
    tones: list[str] | None = None
    purpose: str | None = None
    platform: str | None = None
    language: str | None = None
    length_preset: str | None = None
    cta_text: str | None = None
    keywords: list[str] | None = None
    extra_instructions: str | None = None
    occasion: str | None = None
    topic_id: str | None = None
    audience_id: str | None = None
    post_day: int | None = Field(default=None, ge=1, le=28)
    post_time: time | None = None
    lead_days: int | None = Field(default=None, ge=0, le=28)
    is_active: bool | None = None
    auto_generate: bool | None = None


class PlanOut(PlanIn):
    id: str
    user_id: str
    created_at: datetime
    next_generate_at: datetime | None = None
    last_status: str | None = None

    model_config = {"from_attributes": True}


class CycleOut(BaseModel):
    id: str
    plan_id: str
    target_month: date
    post_at: datetime
    generate_at: datetime
    status: str
    selected_message_id: str | None
    overrides: dict
    plan_name: str | None = None
    platform: str | None = None
    latest_request_id: str | None = None

    model_config = {"from_attributes": True}


class CyclePatch(BaseModel):
    post_at: datetime | None = None
    status: str | None = None  # only SKIPPED / PLANNED re-open are accepted
    overrides: dict | None = None
