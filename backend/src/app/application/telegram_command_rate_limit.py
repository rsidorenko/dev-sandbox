"""Безопасные in-process примитивы rate limiting команд Telegram (без копирования транспорта)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class TelegramCommandRateLimitKey(StrEnum):
    STATUS = "status"
    ACCESS_RESEND = "access_resend"
    SUPPORT = "support"


class TelegramCommandRateLimiter(Protocol):
    async def allow(
        self,
        *,
        telegram_user_id: int,
        command_key: TelegramCommandRateLimitKey,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _FixedWindowRule:
    max_requests: int
    window_seconds: float


class InMemoryTelegramCommandRateLimiter:
    """Детерминированный fixed-window лимитер по ключу (telegram user id, command key)."""

    def __init__(
        self,
        *,
        status_limit: int = 6,
        status_window_seconds: float = 60.0,
        access_resend_limit: int = 3,
        access_resend_window_seconds: float = 60.0,
        support_limit: int = 30,
        support_window_seconds: float = 60.0,
        now_seconds: Callable[[], float] = time.time,
    ) -> None:
        self._rules: dict[TelegramCommandRateLimitKey, _FixedWindowRule] = {
            TelegramCommandRateLimitKey.STATUS: _FixedWindowRule(
                max_requests=int(status_limit),
                window_seconds=float(status_window_seconds),
            ),
            TelegramCommandRateLimitKey.ACCESS_RESEND: _FixedWindowRule(
                max_requests=int(access_resend_limit),
                window_seconds=float(access_resend_window_seconds),
            ),
            TelegramCommandRateLimitKey.SUPPORT: _FixedWindowRule(
                max_requests=int(support_limit),
                window_seconds=float(support_window_seconds),
            ),
        }
        self._state: dict[tuple[int, TelegramCommandRateLimitKey], tuple[float, int]] = {}
        self._now_seconds = now_seconds

    async def allow(
        self,
        *,
        telegram_user_id: int,
        command_key: TelegramCommandRateLimitKey,
    ) -> bool:
        rule = self._rules[command_key]
        now = float(self._now_seconds())
        state_key = (telegram_user_id, command_key)
        started_at, count = self._state.get(state_key, (now, 0))
        if now - started_at >= rule.window_seconds:
            started_at = now
            count = 0
        if count >= rule.max_requests:
            self._state[state_key] = (started_at, count)
            return False
        self._state[state_key] = (started_at, count + 1)
        return True


class NoopAllowAllTelegramCommandRateLimiter:
    async def allow(
        self,
        *,
        telegram_user_id: int,
        command_key: TelegramCommandRateLimitKey,
    ) -> bool:
        _ = (telegram_user_id, command_key)
        return True
