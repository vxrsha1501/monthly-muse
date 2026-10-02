"""Generation behind one adapter interface (Sections 6.3-6.5).

    generate_candidates(spec) -> list[{arm, text}]

Concrete adapters: OpenAI-compatible chat, Anthropic, Ollama, and our own
template engine. Around them sit retry with exponential backoff + jitter
(1s/3s/9s), a circuit breaker (5 failures -> 60s cooldown), tolerant JSON
parsing with one repair attempt, and a budget guard.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger("monthlymuse.llm")

# $ per 1M tokens (input, output) - cost tracking metric, Section 16.3
PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "gemini-1.5-flash": (0.10, 0.40),
    "llama3.2": (0.0, 0.0),
}


class LLMUnavailable(Exception):
    """Provider failed after retries - caller falls back to templates."""


@dataclass
class PromptSpec:
    system: str
    user: str
    brief: dict = field(default_factory=dict)   # structured brief (used by the template engine)
    max_tokens: int = 1600


@dataclass
class ProviderResult:
    candidates: list[dict]
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0


# --- JSON parsing ---------------------------------------------------------

_CAND_RE = re.compile(r'"(?:arm|style)"\s*:\s*(\d+).*?"text"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)


def parse_candidates(raw: str) -> list[dict]:
    """Tolerant parse of the model's JSON: strict first, then brace-slice, then regex."""
    text = (raw or "").strip()
    # strip code fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1]
        for attempt in (candidate, candidate.replace(",}", "}").replace(",]", "]")):
            try:
                data = json.loads(attempt)
                items = data.get("candidates") or data.get("messages") or []
                out = []
                for i, item in enumerate(items):
                    if isinstance(item, str):
                        out.append({"arm": (i // 2) + 1, "text": item.strip()})
                    elif isinstance(item, dict) and item.get("text"):
                        out.append({"arm": int(item.get("arm", (i // 2) + 1)), "text": str(item["text"]).strip()})
                if out:
                    return out
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    # last resort: regex extraction
    found = [{"arm": int(a), "text": t.encode().decode("unicode_escape", errors="replace").strip()}
             for a, t in _CAND_RE.findall(text)]
    return [f for f in found if f["text"]]


def _cost(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    price = PRICING.get(model)
    if not price or prompt_tokens is None or completion_tokens is None:
        return None
    return round((prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000, 6)


# --- circuit breaker ------------------------------------------------------

class CircuitBreaker:
    def __init__(self, threshold: int | None = None, cooldown: float | None = None) -> None:
        self.threshold = threshold or settings.llm_circuit_failures
        self.cooldown = cooldown or settings.llm_circuit_cooldown_seconds
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return False
            if time.monotonic() - self.opened_at >= self.cooldown:
                self.opened_at = None   # half-open: allow a trial call
                self.failures = 0
                return False
            return True

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()
                logger.warning("circuit_open cooldown=%ss", self.cooldown)

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None


circuit = CircuitBreaker()


# --- adapters -------------------------------------------------------------

class OpenAIChatAdapter:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.llm_model
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    def complete(self, spec: PromptSpec) -> ProviderResult:
        if not self.api_key:
            raise LLMUnavailable("OPENAI_API_KEY not set")
        started = time.perf_counter()
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0.85,
                "max_tokens": spec.max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": spec.system},
                             {"role": "user", "content": spec.user}],
            },
            timeout=settings.llm_timeout_seconds,
        )
        if resp.status_code >= 500 or resp.status_code == 429:
            raise LLMUnavailable(f"provider {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMUnavailable(f"provider error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        return ProviderResult(parse_candidates(content), self.name, self.model, pt, ct,
                              _cost(self.model, pt, ct), int((time.perf_counter() - started) * 1000))


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "claude-3-5-haiku-latest"
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def complete(self, spec: PromptSpec) -> ProviderResult:
        if not self.api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY not set")
        started = time.perf_counter()
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": spec.max_tokens, "temperature": 0.85,
                  "system": spec.system, "messages": [{"role": "user", "content": spec.user}]},
            timeout=settings.llm_timeout_seconds,
        )
        if resp.status_code >= 400:
            raise LLMUnavailable(f"provider error {resp.status_code}")
        data = resp.json()
        content = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage") or {}
        pt, ct = usage.get("input_tokens"), usage.get("output_tokens")
        return ProviderResult(parse_candidates(content), self.name, self.model, pt, ct,
                              _cost(self.model, pt, ct), int((time.perf_counter() - started) * 1000))


class OllamaAdapter:
    name = "ollama"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "llama3.2"
        self.base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")

    def complete(self, spec: PromptSpec) -> ProviderResult:
        started = time.perf_counter()
        resp = httpx.post(f"{self.base_url}/api/chat",
                          json={"model": self.model, "stream": False, "format": "json",
                                "options": {"temperature": 0.85},
                                "messages": [{"role": "system", "content": spec.system},
                                             {"role": "user", "content": spec.user}]},
                          timeout=settings.llm_timeout_seconds)
        if resp.status_code >= 400:
            raise LLMUnavailable(f"ollama {resp.status_code}")
        content = resp.json().get("message", {}).get("content", "")
        return ProviderResult(parse_candidates(content), self.name, self.model, None, None, 0.0,
                              int((time.perf_counter() - started) * 1000))


class TemplateAdapter:
    """Our own template engine - always available, no network (Section 6.5 step 3)."""
    name = "template"

    def __init__(self) -> None:
        from app.ai.templates import TemplateEngine
        self.engine = TemplateEngine.load()

    def complete(self, spec: PromptSpec) -> ProviderResult:
        started = time.perf_counter()
        items = self.engine.generate(spec.brief)
        return ProviderResult(items, self.name, "templates", None, None, 0.0,
                              int((time.perf_counter() - started) * 1000))


def get_adapter(provider: str | None = None):
    provider = (provider or settings.llm_provider or "template").lower()
    if provider == "openai":
        return OpenAIChatAdapter()
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "ollama":
        return OllamaAdapter()
    return TemplateAdapter()


# --- resilient wrapper ----------------------------------------------------

BACKOFF_SECONDS = (1.0, 3.0, 9.0)


class GenerationService:
    """One interface for the pipeline: retry, breaker, repair, template fallback."""

    def __init__(self, adapter=None) -> None:
        self.adapter = adapter

    def generate(self, spec: PromptSpec) -> ProviderResult:
        adapter = self.adapter or get_adapter()
        if adapter.name != "template":
            if circuit.is_open:
                logger.warning("circuit_open_fallback")
                return self._fallback(spec, reason="circuit_open")
            result = self._with_retries(adapter, spec)
            if result is None:
                return self._fallback(spec, reason="retries_exhausted")
            if not result.candidates:
                return self._fallback(spec, reason="empty_or_malformed")
            return result
        return adapter.complete(spec)

    def _with_retries(self, adapter, spec: PromptSpec) -> ProviderResult | None:
        attempts = settings.llm_max_attempts
        for i in range(attempts):
            try:
                result = adapter.complete(spec)
                circuit.record_success()
                if result.candidates:
                    return result
                logger.warning("llm_malformed_json attempt=%d", i + 1)
            except (LLMUnavailable, httpx.HTTPError, httpx.TimeoutException, KeyError, ValueError) as exc:
                logger.warning("llm_error attempt=%d err=%s", i + 1, exc)
                circuit.record_failure()
            if i < attempts - 1:
                delay = BACKOFF_SECONDS[min(i, len(BACKOFF_SECONDS) - 1)]
                time.sleep(delay * (0.7 + 0.6 * random.random()))  # jitter
        return None

    def _fallback(self, spec: PromptSpec, reason: str) -> ProviderResult:
        logger.warning("template_fallback reason=%s", reason)
        return TemplateAdapter().complete(spec)
