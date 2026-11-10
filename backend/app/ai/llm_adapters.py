"""Generation behind one adapter interface (Sections 6.3-6.5).

    generate_candidates(spec) -> list[{arm, text}]

Concrete adapters: OpenAI-compatible chat, Anthropic, Ollama, Google Gemini, and our own
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

from app.core.config import Settings, settings

logger = logging.getLogger("monthlymuse.llm")

# $ per 1M tokens (input, output) - cost tracking metric, Section 16.3
PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "gemini-1.5-flash": (0.10, 0.40),
    "llama3.2": (0.0, 0.0),
    "groq-llama3-groq-70b-8192": (0.09, 0.09),
}


class LLMUnavailable(Exception):
    """Provider failed - caller moves on to the next provider.

    ``retryable=False`` marks failures where another attempt cannot help
    (quota exhausted, model retired, invalid key): the chain fails over
    immediately instead of sleeping through the backoff schedule.
    """

    def __init__(self, message: str, *, retryable: bool = True,
                 missing_model: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.missing_model = missing_model


@dataclass
class PromptSpec:
    system: str
    user: str
    brief: dict = field(default_factory=dict)   # structured brief (used by the template engine)
    arms: list = field(default_factory=list)    # Thompson-selected (tone, structure) arms
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

_CAND_RE = re.compile(r'"(?:arm|style)"\s*:\s*(\d+).*?"text"\s*:\"((?:[^"\\]|\\.)*)\"', re.S)
def parse_candidates(raw: str) -> list[dict]:
    """Tolerant parse of the model's JSON: strict first, then brace-slice, then regex."""
    text = (raw or "").strip()
    # strip code fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("```").strip()
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


circuit = CircuitBreaker()   # kept for backwards compatibility

# One breaker per provider: an exhausted OpenAI/Gemini free tier must never
# stop the next provider in the chain from being tried.
_breakers: dict[str, CircuitBreaker] = {}


def breaker_for(provider: str) -> CircuitBreaker:
    key = (provider or "template").lower()
    if key not in _breakers:
        _breakers[key] = CircuitBreaker()
    return _breakers[key]

# --- adapters -------------------------------------------------------------

class OpenAIChatAdapter:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.model_for("openai")
        # Read API key: prefer settings (which loads .env), fallback to os.env
        self.api_key = getattr(settings, "openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
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
        if resp.status_code == 429:
            raise LLMUnavailable(f"provider 429 quota", retryable=False)
        if resp.status_code >= 500:
            raise LLMUnavailable(f"provider {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMUnavailable(f"provider error {resp.status_code}: {resp.text[:200]}",
                                 retryable=False)
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
        # Read API key: prefer settings (which loads .env), fallback to os.env
        self.api_key = getattr(settings, "anthropic_api_key", os.environ.get("ANTHROPIC_API_KEY", ""))

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


class GeminiAdapter:
    """Google Gemini Flash adapter using the raw Google Generative Language API.

    Keeps the API key exclusively in backend environment variables.
    Never exposes API keys in frontend code.
    Reads from settings (which loads .env with MM_ prefix) or os.env directly.
    Uses the raw REST API for reliability (the deprecated google.generativeai
    SDK has known compatibility issues with Python 3.10+).
    """

    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        self.model_name = model or settings.model_for("gemini") or settings.gemini_model
        # Read API key: settings loads .env with MM_ prefix (verified working via health endpoint)
        # Read API key: prefer settings (MM_ prefix from .env), fallback to os.env
        self.api_key = getattr(settings, "gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
        if not self.api_key:
            raise LLMUnavailable("GEMINI_API_KEY not set")

    def complete(self, spec: PromptSpec) -> ProviderResult:
        if not self.api_key:
            raise LLMUnavailable("GEMINI_API_KEY not set")
        return self._call(spec, self.model_name)

    def _call(self, spec: PromptSpec, model: str) -> ProviderResult:
        started = time.perf_counter()
        try:
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent")
            resp = httpx.post(
                url,
                params={"key": self.api_key},
                json={
                    # Gemini REST rejects bare strings here: every part is an object.
                    "systemInstruction": {"parts": [{"text": spec.system}]},
                    "contents": [{"role": "user", "parts": [{"text": spec.user}]}],
                    "generationConfig": {
                        "temperature": 0.85,
                        "maxOutputTokens": spec.max_tokens,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=settings.llm_timeout_seconds,
            )
            if resp.status_code == 404 and model != settings.gemini_model:
                # stale model name in an old env var -> retry the configured default
                logger.warning("gemini_model_not_found model=%s retry=%s",
                               model, settings.gemini_model)
                return self._call(spec, settings.gemini_model)
            if resp.status_code == 429 or resp.status_code == 503:
                # free-tier quota / capacity: another attempt now cannot help
                raise LLMUnavailable(f"provider {resp.status_code}: {resp.text[:160]}",
                                     retryable=False)
            if resp.status_code >= 500:
                raise LLMUnavailable(f"provider {resp.status_code}: {resp.text[:160]}")
            if resp.status_code >= 400:
                raise LLMUnavailable(f"provider error {resp.status_code}: {resp.text[:200]}",
                                     retryable=False)
            data = resp.json()

            # Real shape: candidates[] -> content.parts[] -> text
            texts: list[str] = []
            for cand in data.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if isinstance(part, dict) and part.get("text"):
                        texts.append(str(part["text"]))
            raw = "\n".join(texts).strip()
            candidates = parse_candidates(raw) if raw else []
            if not candidates and raw:
                candidates = [{"arm": 1, "text": raw}]
            if not candidates:
                raise LLMUnavailable("gemini returned no candidates")

            usage = data.get("usageMetadata") or {}
            pt, ct = usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
            self.model_name = model
            return ProviderResult(candidates, self.name, model, pt, ct,
                                  _cost(model, pt, ct),
                                  int((time.perf_counter() - started) * 1000))
        except LLMUnavailable:
            raise
        except httpx.TimeoutException as exc:
            # hanging endpoint: fail over instead of waiting again
            logger.warning("gemini_timeout err=%s", exc)
            raise LLMUnavailable(f"gemini timeout: {exc}", retryable=False)
        except Exception as exc:
            logger.warning("gemini_error attempt err=%s", exc)
            raise LLMUnavailable(f"gemini error: {exc}")


class GroqAdapter:
    name = "groq"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.model_for("groq")
        # Read API key: prefer settings, fallback to os.env
        self.api_key = getattr(settings, "groq_api_key", os.environ.get("GROQ_API_KEY", ""))
        if not self.api_key:
            raise LLMUnavailable("GROQ_API_KEY not set")

    def complete(self, spec: PromptSpec) -> ProviderResult:
        """Try the configured model, then known-good ones if it was retired."""
        errors: list[str] = []
        for model in [self.model, *settings.groq_models]:
            try:
                result = self._call(spec, model)
                if model != self.model:
                    logger.warning("groq_model_switched from=%s to=%s", self.model, model)
                    self.model = model
                return result
            except LLMUnavailable as exc:
                if not exc.missing_model:
                    raise
                errors.append(model)
        raise LLMUnavailable(
            f"groq: no available model among {errors}", retryable=False, missing_model=True)

    def _call(self, spec: PromptSpec, model: str, json_mode: bool = True) -> ProviderResult:
        started = time.perf_counter()
        body: dict = {
            "model": model,
            "temperature": 0.85,
            "max_tokens": spec.max_tokens,
            "messages": [{"role": "system", "content": spec.system},
                         {"role": "user", "content": spec.user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if "gpt-oss" in model:
            # reasoning models spend output budget on hidden reasoning: keep it
            # low and leave headroom so the JSON answer is never truncated to "".
            body["reasoning_effort"] = "low"
            body["max_tokens"] = max(spec.max_tokens, 2400)
        resp = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          json=body, timeout=settings.llm_timeout_seconds)
        if resp.status_code == 404:
            # model retired / not enabled on this account
            raise LLMUnavailable(f"groq model {model} unavailable", retryable=False,
                                 missing_model=True)
        if resp.status_code == 429:
            raise LLMUnavailable("groq 429 rate limit", retryable=False)
        if (resp.status_code == 400 and json_mode
                and "json_validate_failed" in resp.text):
            # strict JSON rejected (empty/truncated generation): retry once
            # without the response_format constraint and parse tolerantly.
            logger.warning("groq_json_rejected model=%s retry=plain", model)
            return self._call(spec, model, json_mode=False)
        if resp.status_code >= 400:
            raise LLMUnavailable(f"groq error {resp.status_code}: {resp.text[:200]}",
                                 retryable=False)
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        return ProviderResult(parse_candidates(content), self.name, model, pt, ct,
                              _cost(model, pt, ct), int((time.perf_counter() - started) * 1000))


class TemplateAdapter:
    """Our own template engine - always available, no network (Section 6.5 step 3)."""
    name = "template"

    def __init__(self) -> None:
        from app.ai.templates import TemplateEngine
        self.engine = TemplateEngine.load()

    def complete(self, spec: PromptSpec) -> ProviderResult:
        started = time.perf_counter()
        # the sampled arms decide which tone/structure templates are rendered,
        # so personalisation genuinely reaches the fallback generator (P9)
        arms = [tuple(a) for a in (spec.arms or [])] or None
        items = self.engine.generate(spec.brief, arms=arms)
        return ProviderResult(items, self.name, "templates", None, None, 0.0,
                              int((time.perf_counter() - started) * 1000))


def get_adapter(provider: str | None = None):
    provider = settings.resolve_provider(provider)
    if provider == "openai":
        return OpenAIChatAdapter()
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "ollama":
        return OllamaAdapter()
    if provider == "gemini":
        return GeminiAdapter()
    if provider == "groq":
        return GroqAdapter()
    return TemplateAdapter()


# --- resilient wrapper ----------------------------------------------------

BACKOFF_SECONDS = (1.0, 3.0, 9.0)


class GenerationService:
    """One interface for the pipeline: retry, breaker, repair, template fallback.

    Priority order is driven by ``MM_LLM_PROVIDER`` (Gemini by default), then
    the remaining hosted adapters, then the always-available template engine:

        primary -> remaining hosted providers -> template

    Every provider has its own circuit breaker and its own model setting, so a
    stale model name or an exhausted free tier never disables the whole chain.
    """

    HOSTED_ORDER = ("gemini", "openai", "groq")

    def __init__(self, adapter=None) -> None:
        self.adapter = adapter

    def generate(self, spec: PromptSpec) -> ProviderResult:
        adapter = self.adapter or get_adapter()
        if adapter.name == "template":
            # Template used as primary (only when no LLM configured)
            return adapter.complete(spec)

        for name in self._chain(adapter.name):
            breaker = breaker_for(name)
            if breaker.is_open:
                logger.warning("circuit_open provider=%s cooldown=%ss", name, breaker.cooldown)
                continue
            try:
                provider = get_adapter(name)
            except LLMUnavailable as exc:
                logger.info("provider_skipped provider=%s reason=%s", name, exc)
                continue
            result = self._with_retries(provider, spec)
            if result is not None and result.candidates:
                logger.info("llm_success provider=%s", name)
                return result
            logger.warning("llm_exhausted provider=%s", name)

        return self._fallback(spec, reason="all_providers_unavailable")

    def _chain(self, primary: str) -> list[str]:
        chain = [settings.resolve_provider(primary or settings.llm_provider)]
        for name in self.HOSTED_ORDER:
            if name not in chain:
                chain.append(name)
        return chain

    def _with_retries(self, adapter, spec: PromptSpec) -> ProviderResult | None:
        breaker = breaker_for(adapter.name)
        attempts = settings.llm_max_attempts
        for i in range(attempts):
            try:
                result = adapter.complete(spec)
                breaker.record_success()
                if result.candidates:
                    return result
                logger.warning("llm_malformed_json attempt=%d", i + 1)
            except LLMUnavailable as exc:
                logger.warning("llm_error provider=%s attempt=%d err=%s", adapter.name, i + 1, exc)
                breaker.record_failure()
                if not exc.retryable:
                    return None          # fail over to the next provider immediately
            except httpx.TimeoutException as exc:
                logger.warning("llm_error provider=%s attempt=%d err=%s", adapter.name, i + 1, exc)
                breaker.record_failure()
                return None              # a hanging provider: move on immediately
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("llm_error provider=%s attempt=%d err=%s", adapter.name, i + 1, exc)
                breaker.record_failure()
            if i < attempts - 1:
                delay = BACKOFF_SECONDS[min(i, len(BACKOFF_SECONDS) - 1)]
                time.sleep(delay * (0.7 + 0.6 * random.random()))  # jitter
        return None

    def _fallback(self, spec: PromptSpec, reason: str) -> ProviderResult:
        logger.warning("template_fallback reason=%s", reason)
        return TemplateAdapter().complete(spec)