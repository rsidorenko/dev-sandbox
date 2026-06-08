"""In-memory sliding window rate limiter для webhook endpoints."""

from __future__ import annotations

import os
import time
from collections import defaultdict

ENV_WEBHOOK_RATE_LIMIT_PER_MINUTE = "TELEGRAM_WEBHOOK_RATE_LIMIT_PER_MINUTE"
DEFAULT_RATE_LIMIT_PER_MINUTE = 60
_MAX_ENTRIES = 10000
_EVICT_EVERY = 100


class WebhookRateLimiter:
    """Sliding window rate limiter по client IP (in-memory, single-process)."""

    def __init__(self, max_requests_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE) -> None:
        self._max = max_requests_per_minute
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._call_count = 0

    def is_allowed(self, client_ip: str) -> bool:
        now = time.monotonic()
        window_start = now - 60.0
        requests = self._windows[client_ip]
        self._windows[client_ip] = [t for t in requests if t > window_start]
        if len(self._windows[client_ip]) >= self._max:
            return False
        self._windows[client_ip].append(now)
        self._call_count += 1
        if self._call_count % _EVICT_EVERY == 0:
            self._evict()
        return True

    def _evict(self) -> None:
        """Remove IPs with empty windows to prevent unbounded growth."""
        if len(self._windows) <= _MAX_ENTRIES:
            return
        now = time.monotonic()
        window_start = now - 60.0
        expired = [ip for ip, reqs in self._windows.items() if not reqs or reqs[-1] < window_start]
        for ip in expired:
            del self._windows[ip]

    @property
    def max_requests_per_minute(self) -> int:
        return self._max


def load_rate_limiter_from_env() -> WebhookRateLimiter:
    """Создаёт rate limiter с конфигурацией из env."""
    raw = os.environ.get(ENV_WEBHOOK_RATE_LIMIT_PER_MINUTE, "").strip()
    if raw:
        try:
            limit = int(raw)
            if limit < 1:
                limit = DEFAULT_RATE_LIMIT_PER_MINUTE
        except ValueError:
            limit = DEFAULT_RATE_LIMIT_PER_MINUTE
    else:
        limit = DEFAULT_RATE_LIMIT_PER_MINUTE
    return WebhookRateLimiter(max_requests_per_minute=limit)
