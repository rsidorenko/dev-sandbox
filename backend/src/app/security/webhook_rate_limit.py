"""In-memory sliding window rate limiter для webhook endpoints."""

from __future__ import annotations

import os
import time
from collections import defaultdict

ENV_WEBHOOK_RATE_LIMIT_PER_MINUTE = "TELEGRAM_WEBHOOK_RATE_LIMIT_PER_MINUTE"
DEFAULT_RATE_LIMIT_PER_MINUTE = 60


class WebhookRateLimiter:
    """Sliding window rate limiter по client IP (in-memory, single-process)."""

    def __init__(self, max_requests_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE) -> None:
        self._max = max_requests_per_minute
        self._windows: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.monotonic()
        window_start = now - 60.0
        requests = self._windows[client_ip]
        # Удаляем записи старше 60 секунд
        self._windows[client_ip] = [t for t in requests if t > window_start]
        if len(self._windows[client_ip]) >= self._max:
            return False
        self._windows[client_ip].append(now)
        return True

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
