"""Preferences, audiences, topics and occasions schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PreferencesIn(BaseModel):
    default_language: str = "en"
    default_platform: str = "instagram"
    voice_notes: str | None = Field(default=None, max_length=1000)
    banned_words: list[str] = Field(default_factory=list, max_length=30)
    emoji_level: int = Field(default=1, ge=0, le=2)
    length_preset: str = "medium"
    lead_days: int = Field(default=7, ge=0, le=28)
    notify_email: bool = True
    autopilot: bool = False
    learning_paused: bool = False
    variety: float = Field(default=0.7, ge=0.5, le=0.9)
    default_tones: list[str] = Field(default_factory=list, max_length=2)


class LearnedPreferences(BaseModel):
    scoring_weights: dict[str, float]
    learned_length: dict[str, float | int | None]
    preference_vector_norm: float | None
    tone_preferences: list[dict]
    arm_stats: list[dict]
    n_feedback_events: int
    learning_paused: bool


class PreferencesOut(PreferencesIn):
    length_mean: float | None = None
    length_std: float | None = None
    length_n: int = 0

    model_config = {"from_attributes": True}


class AudienceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class AudienceOut(AudienceIn):
    id: str

    model_config = {"from_attributes": True}


class TopicIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=6)


class TopicOut(TopicIn):
    id: str
    user_id: str | None = None

    model_config = {"from_attributes": True}


class OccasionOut(BaseModel):
    id: str
    name: str
    month: int
    day: int | None
    date_rule: str | None
    region: str
    category: str
    description: str | None

    model_config = {"from_attributes": True}
