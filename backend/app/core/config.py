"""Application configuration (environment-driven, no secrets in the repo)."""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import ClassVar

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
    embedder: str = "sentence-transformers"  # hash (local, deterministic) | sentence-transformers
    embedding_model: str = "all-MiniLM-L6-v2"
    gemini_api_key: str = ""  # loaded from .env (MM_GEMINI_API_KEY) or os.env
    groq_api_key: str = ""  # loaded from .env or os.env
    openai_api_key: str = ""  # loaded from .env OPENAI_API_KEY or os.env
    random_seed: int = 42

    # --- LLM generation ---
    # template -> our own template engine (always works, offline)
    # openai / anthropic / ollama / gemini -> hosted adapters behind the same interface
    llm_provider: str = "gemini"  # template | gemini | openai | groq | anthropic | ollama
    llm_model: str = ""  # model for the PRIMARY provider (empty = provider default)
    openai_model: str = "gpt-4o-mini"
    gemini_model: str = "gemini-3.8-flash"
    # Groq retires models regularly (llama-3.1-8b-instant is gone): the adapter
    # walks this list and uses the first model the account can actually call.
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_models: str = "openai/gpt-oss-20b,qwen/qwen3.8-27b"
    # A hung provider must not stall the whole request: time out quickly and
    # fail over, then let the breaker skip it for a minute after a few misses.
    llm_timeout_seconds: float = 15.0
    llm_max_attempts: int = 2
    llm_circuit_failures: int = 3
    llm_circuit_cooldown_seconds: float = 60.0
    llm_monthly_budget_usd: float = 5.0

    # --- rate limiting ---
    generations_per_hour: int = 10
    requests_per_minute: int = 100

    # --- scheduler ---
    scheduler_enabled: bool = True
    # Create/repair the four demo accounts on start-up (idempotent). Hosted
    # free tiers wipe the SQLite file on every restart, so without this the
    # demo accounts disappear after each deploy.
    seed_demos_on_startup: bool = True
    scheduler_timezone: str = "Asia/Kolkata"
    lead_days_default: int = 7
    # Shared secret for the external cron -> POST /admin/scheduler/run webhook.
    # Empty = derive a stable token from secret_key (works out of the box).
    scheduler_webhook_token: str = ""

    # --- email (optional; console backend in dev) ---
    email_backend: str = "console"  # console | smtp | resend
    smtp_url: str = ""

    # --- paths ---
    # <root>/backend/app/core/config.py -> <root>/data
    data_dir: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "data",
    )

    # --- provider / model normalisation ---------------------------------
    PROVIDERS: ClassVar[tuple[str, ...]] = ("template", "gemini", "openai", "groq",
                                            "anthropic", "ollama")

    def resolve_provider(self, provider: str | None = None) -> str:
        """Map a configured value to a real adapter name.

        Tolerates a model name landing in the provider slot (e.g.
        ``MM_LLM_PROVIDER=gemini-pro-latest``), which previously made the app
        silently fall back to the template engine.
        """
        raw = (provider if provider is not None else self.llm_provider or "").strip().lower()
        if raw in self.PROVIDERS:
            return raw
        if raw.startswith(("gemini", "gemma")):
            return "gemini"
        if raw.startswith(("gpt", "o1", "o3", "o4", "text-embedding")):
            return "openai"
        if raw.startswith("claude"):
            return "anthropic"
        if raw.startswith("groq") or "llama" in raw or "mixtral" in raw:
            return "groq"
        return "template"

    @property
    def groq_models(self) -> list[str]:
        """Configured Groq model first, then known-good fallbacks."""
        ordered = [self.groq_model, *self.groq_fallback_models.split(",")]
        return list(dict.fromkeys(m.strip() for m in ordered if m.strip()))

    def model_for(self, provider: str) -> str:
        """The model name to use for one provider.

        ``MM_LLM_MODEL`` applies to whichever provider is primary; every other
        provider keeps its own default so a Gemini model name is never sent to
        Groq or OpenAI.
        """
        name = self.resolve_provider(provider)
        configured = (self.llm_provider or "").strip()
        if configured.lower() not in self.PROVIDERS and name == self.resolve_provider():
            # the provider variable actually held a model name
            return configured or self.llm_model
        if name == self.resolve_provider() and self.llm_model:
            return self.llm_model
        return {
            "openai": self.openai_model,
            "gemini": self.gemini_model,
            "groq": self.groq_model,
            "anthropic": "claude-3-5-haiku-latest",
            "ollama": "llama3.2",
        }.get(name, self.llm_model or "")

    @property
    def resolved_database_url(self) -> str:
        """SQLAlchemy URL with a driver suffix and TLS for managed Postgres.

        Neon (and Heroku) hand out ``postgresql://...`` or legacy
        ``postgres://...`` URLs; SQLAlchemy needs an explicit driver, and Neon
        requires TLS. Normalising here means a URL pasted straight from the
        Neon dashboard works unchanged.
        """
        url = (self.database_url or "").strip()
        if not url:
            return "sqlite:///./monthlymuse.db"
        scheme, _, rest = url.partition("://")
        if scheme in ("postgres", "postgresql"):
            url = f"postgresql+psycopg://{rest}"
        if url.startswith("postgresql+psycopg://") and "sslmode=" not in url:
            host = url.rsplit("@", 1)[-1].split("/", 1)[0].lower()
            if "neon.tech" in host or "render.com" in host:
                url += ("&" if "?" in url else "?") + "sslmode=require"
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def scheduler_token(self) -> str:
        """Webhook auth token (never logged, never exported)."""
        if self.scheduler_webhook_token:
            return self.scheduler_webhook_token
        import hmac
        return hmac.new(self.secret_key.encode("utf-8"),
                        b"monthlymuse.scheduler.webhook",
                        hashlib.sha256).hexdigest()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()