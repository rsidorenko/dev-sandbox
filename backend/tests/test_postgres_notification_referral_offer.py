"""Postgres regression: the «Спасибо за подписку» referral offer (C2) must NOT fire when the
user's most recent subscription update was a FREE grant (referral bonus / broadcast gift button /
admin add-days), not a real payment.

Background: ``_check_referral_offer_paid`` triggers on ``updated_at`` within 25h + ``plan_id IS NOT
NULL``. ``grant_subscription_days`` bumps ``updated_at`` and sets/keeps ``plan_id`` exactly like a
payment — so before the fix a user who received ANY grant got a spurious «Спасибо за подписку»
offer (reported: admin broadcast gift button → days claimed → offer pushed). The fix excludes
users whose ``updated_at`` coincides with an ``admin_subscription_actions`` audit row
(action='add_days') — every grant (referral_bonus, broadcast_claim:*, admin) writes one.

Opt-in via ``DATABASE_URL`` (skipped otherwise), like the other ``test_postgres_*.py``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.runtime.notification_scheduler import NotificationScheduler

_OFFER_TYPE = "referral_offer_paid"


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL", "").strip() or None


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping referral-offer scheduler test")
    return url


class _FakeResponse:
    status_code = 200


class _FakeHttpClient:
    """Stand-in for httpx.AsyncClient so the scheduler never hits the Telegram Bot API."""

    is_closed = False

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
        return _FakeResponse()

    async def aclose(self) -> None:
        self.is_closed = True


async def _seed_user(conn: asyncpg.Connection, uid: str, tg: int, plan: str) -> None:
    await conn.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1,$2)",
        tg,
        uid,
    )
    await conn.execute(
        "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc, updated_at) "
        "VALUES ($1,'active',$2,NOW()+INTERVAL'30 days',NOW())",
        uid,
        plan,
    )


async def _seed_grant(conn: asyncpg.Connection, uid: str, actor: str, *, created_at: str = "NOW()") -> None:
    """Аудит-строка бесплатного гранта — то, что пишет grant_subscription_days сразу после
    апсерта снапшота (created_at совпадает с updated_at снапшота)."""
    await conn.execute(
        "INSERT INTO admin_subscription_actions "
        "(actor, internal_user_id, action, days, new_active_until, plan_id, device_count, provisioning, correlation_id, created_at) "
        f"VALUES ($1,$2,'add_days',30,NOW()+INTERVAL'30 days','1m',5,'success','test-corr',{created_at})",
        actor,
        uid,
    )


async def _offer_sent(pool: asyncpg.Pool, uid: str) -> bool:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT 1 FROM notification_log WHERE internal_user_id=$1 AND notification_type=$2",
            uid,
            _OFFER_TYPE,
        )


def test_paid_offer_skips_any_free_grant_but_not_real_payment(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                await apply_postgres_migrations(conn)

            pref = "test-paid-offer-grant"
            uid_a, uid_b, uid_c, uid_d, uid_e = (f"{pref}-{s}" for s in "abcde")

            # Idempotent cleanup of any prior run (shared dev DB).
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM admin_subscription_actions WHERE internal_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM referral_bonus_awards WHERE referrer_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM notification_log WHERE internal_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM user_identities WHERE internal_user_id LIKE $1", f"{pref}%")

            async with pool.acquire() as conn:
                # A — real first payment, no grants → offer MUST fire.
                await _seed_user(conn, uid_a, 99000001, "1m")

                # B — fresh referral bonus grant → offer MUST NOT fire.
                await _seed_user(conn, uid_b, 99000002, "custom:30")
                await _seed_grant(conn, uid_b, "referral_bonus")

                # C — fresh real payment but a STALE grant 2 days ago → offer MUST fire
                # (filter is not a blanket block).
                await _seed_user(conn, uid_c, 99000003, "1m")
                await _seed_grant(conn, uid_c, "referral_bonus", created_at="NOW()-INTERVAL'2 days'")

                # D — fresh BROADCAST gift (кнопка рассылки, репортед-кейс) → MUST NOT fire.
                await _seed_user(conn, uid_d, 99000004, "1m")
                await _seed_grant(conn, uid_d, "broadcast_claim:11111111-2222-3333-4444-555555555555")

                # E — fresh admin add-days → MUST NOT fire.
                await _seed_user(conn, uid_e, 99000005, "1m")
                await _seed_grant(conn, uid_e, "admin:operator@example.com")

            scheduler = NotificationScheduler(pool=pool, bot_token="t", vless_provider=AsyncMock())
            scheduler._http_client = _FakeHttpClient()  # type: ignore[assignment]  # stub the Bot API call
            await scheduler._check_referral_offer_paid(datetime.now(UTC))

            assert await _offer_sent(pool, uid_a) == 1, "real first payment must get the paid offer"
            assert await _offer_sent(pool, uid_b) is None, (
                "a fresh referral-bonus grant must NOT trigger the paid offer"
            )
            assert await _offer_sent(pool, uid_c) == 1, (
                "a stale grant must not block the offer for a fresh real payment"
            )
            assert await _offer_sent(pool, uid_d) is None, (
                "a fresh broadcast gift claim must NOT trigger the paid offer"
            )
            assert await _offer_sent(pool, uid_e) is None, (
                "a fresh admin add-days grant must NOT trigger the paid offer"
            )
        finally:
            await pool.close()

    asyncio.run(main())
