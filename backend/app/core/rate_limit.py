"""In-process sliding-window rate limiting per user and per IP (no extra dependency)."""
from __future__ import annotations

import threading
import time
from collections import deque

from app.core.config import settings
from app.core.errors import RateLimited


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise RateLimited(f"Limit of {limit} requests per {int(window_seconds)}s reached",
                                  {"limit": limit, "window_seconds": int(window_seconds)})
            bucket.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def rate_limit(key: str, kind: str = "api") -> None:
    """kind: 'api' -> requests/min per key; 'generation' -> generations/hour per user."""
    if kind == "generation":
        limiter.check(f"gen:{key}", settings.generations_per_hour, 3600)
    else:
        limiter.check(f"api:{key}", settings.requests_per_minute, 60)
