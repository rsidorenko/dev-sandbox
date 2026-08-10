from __future__ import annotations

import pytest

from app.application.telegram_command_rate_limit import (
    InMemoryTelegramCommandRateLimiter,
    NoopAllowAllTelegramCommandRateLimiter,
    TelegramCommandRateLimitKey,
)


@pytest.mark.asyncio
async def test_in_memory_limiter_allows_within_limit_and_blocks_after() -> None:
    limiter = InMemoryTelegramCommandRateLimiter(
        status_limit=2,
        status_window_seconds=60.0,
        access_resend_limit=1,
        access_resend_window_seconds=60.0,
        now_seconds=lambda: 100.0,
    )
    assert await limiter.allow(telegram_user_id=10, command_key=TelegramCommandRateLimitKey.STATUS) is True
    assert await limiter.allow(telegram_user_id=10, command_key=TelegramCommandRateLimitKey.STATUS) is True
    assert await limiter.allow(telegram_user_id=10, command_key=TelegramCommandRateLimitKey.STATUS) is False


@pytest.mark.asyncio
async def test_in_memory_limiter_is_command_scoped() -> None:
    limiter = InMemoryTelegramCommandRateLimiter(
        status_limit=1,
        status_window_seconds=60.0,
        access_resend_limit=1,
        access_resend_window_seconds=60.0,
        now_seconds=lambda: 100.0,
    )
    assert await limiter.allow(telegram_user_id=20, command_key=TelegramCommandRateLimitKey.STATUS) is True
    assert await limiter.allow(telegram_user_id=20, command_key=TelegramCommandRateLimitKey.STATUS) is False
    assert await limiter.allow(telegram_user_id=20, command_key=TelegramCommandRateLimitKey.ACCESS_RESEND) is True


@pytest.mark.asyncio
async def test_noop_limiter_always_allows() -> None:
    limiter = NoopAllowAllTelegramCommandRateLimiter()
    assert await limiter.allow(telegram_user_id=1, command_key=TelegramCommandRateLimitKey.STATUS) is True
    assert await limiter.allow(telegram_user_id=1, command_key=TelegramCommandRateLimitKey.ACCESS_RESEND) is True


@pytest.mark.asyncio
async def test_default_status_limit_is_six_per_window() -> None:
    limiter = InMemoryTelegramCommandRateLimiter(now_seconds=lambda: 0.0)
    for _ in range(6):
        assert await limiter.allow(telegram_user_id=7, command_key=TelegramCommandRateLimitKey.STATUS) is True
    assert await limiter.allow(telegram_user_id=7, command_key=TelegramCommandRateLimitKey.STATUS) is False


@pytest.mark.asyncio
async def test_default_access_resend_limit_is_three_per_window() -> None:
    limiter = InMemoryTelegramCommandRateLimiter(now_seconds=lambda: 0.0)
    for _ in range(3):
        assert await limiter.allow(telegram_user_id=8, command_key=TelegramCommandRateLimitKey.ACCESS_RESEND) is True
    assert await limiter.allow(telegram_user_id=8, command_key=TelegramCommandRateLimitKey.ACCESS_RESEND) is False


@pytest.mark.asyncio
async def test_default_broadcast_claim_limit_is_ten_per_window() -> None:
    # Бонус-кнопка рассылки: soft anti-spam (корректность выдачи = atomic claim в БД).
    limiter = InMemoryTelegramCommandRateLimiter(now_seconds=lambda: 0.0)
    for _ in range(10):
        assert await limiter.allow(telegram_user_id=9, command_key=TelegramCommandRateLimitKey.BROADCAST_CLAIM) is True
    assert await limiter.allow(telegram_user_id=9, command_key=TelegramCommandRateLimitKey.BROADCAST_CLAIM) is False


@pytest.mark.asyncio
async def test_broadcast_claim_is_command_scoped() -> None:
    # Исчерпание BROADCAST_CLAIM не блокирует другие команды того же пользователя.
    limiter = InMemoryTelegramCommandRateLimiter(
        broadcast_claim_limit=1, broadcast_claim_window_seconds=60.0, now_seconds=lambda: 0.0
    )
    assert await limiter.allow(telegram_user_id=11, command_key=TelegramCommandRateLimitKey.BROADCAST_CLAIM) is True
    assert await limiter.allow(telegram_user_id=11, command_key=TelegramCommandRateLimitKey.BROADCAST_CLAIM) is False
    assert await limiter.allow(telegram_user_id=11, command_key=TelegramCommandRateLimitKey.STATUS) is True
