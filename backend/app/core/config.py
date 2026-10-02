"""Application configuration (environment-driven, no secrets in the repo)."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MM_", extra="ignore")

    # --- app ---
    app_name: str = "MonthlyMuse"
    api_v1: str = "/api/v1"
    environment: str = "development"  # development | test | production
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- database ---
    # SQLite by default (zero setup). Point at postgresql+psycopg://... for
    # PostgreSQL + pgvector; the schema is otherwise identical (Section 9).
    database_url: str = "sqlite:///./monthlymuse.db"

    # --- auth ---
    secret_key: str = "dev-secret-change-me-in-production"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # --- AI / embeddings ---
    embedding_dim: int = 384
    embedder: str = "hash"  # hash (local, deterministic) | sentence-transformers
    embedding_model: str = "all-MiniLM-L6-v2"
    random_seed: int = 42

    # --- LLM generation ---
    # template -> our own template engine (always works, offline)
    # openai / anthropic / ollama -> hosted adapters behind the same interface
    llm_provider: str = "template"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    llm_max_attempts: int = 3
    llm_circuit_failures: int = 5
    llm_circuit_cooldown_seconds: float = 60.0
    llm_monthly_budget_usd: float = 5.0

    # --- rate limiting ---
    generations_per_hour: int = 10
    requests_per_minute: int = 100

    # --- scheduler ---
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Kolkata"
    lead_days_default: int = 7

    # --- email (optional; console backend in dev) ---
    email_backend: str = "console"  # console | smtp | resend
    smtp_url: str = ""

    # --- paths ---
    # <root>/backend/app/core/config.py -> <root>/data
    data_dir: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "data",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
