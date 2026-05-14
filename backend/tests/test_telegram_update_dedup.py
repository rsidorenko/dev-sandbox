from __future__ import annotations

from app.application.telegram_update_dedup import (
    InMemoryTelegramUpdateDedupGuard,
    dedup_key_hash_for_update,
)
from app.shared.test_helpers import run_async as _run


def test_dedup_key_hash_is_stable_and_non_raw() -> None:
    h1 = dedup_key_hash_for_update(command_bucket="status", telegram_update_id=123)
    h2 = dedup_key_hash_for_update(command_bucket="status", telegram_update_id=123)
    h3 = dedup_key_hash_for_update(command_bucket="access_resend", telegram_update_id=123)
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64
    assert "123" not in h1
    assert "status" not in h1


def test_in_memory_dedup_first_seen_then_duplicate() -> None:
    async def main() -> None:
        guard = InMemoryTelegramUpdateDedupGuard(now_seconds=lambda: 10.0)
        assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=10) is True
        assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=10) is False

    _run(main())


def test_in_memory_dedup_is_bucket_scoped() -> None:
    async def main() -> None:
        guard = InMemoryTelegramUpdateDedupGuard(now_seconds=lambda: 10.0)
        assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=11) is True
        assert await guard.mark_if_first_seen(command_bucket="access_resend", telegram_update_id=11) is True

    _run(main())


def test_in_memory_dedup_allows_after_expiry() -> None:
    ticks = {"value": 10.0}

    def _now() -> float:
        return ticks["value"]

    async def main() -> None:
        guard = InMemoryTelegramUpdateDedupGuard(ttl_seconds=5.0, now_seconds=_now)
        assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=77) is True
        assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=77) is False
        ticks["value"] = 16.0
        assert await guard.mark_if_first_seen(command_bucket="status", telegram_update_id=77) is True

    _run(main())
