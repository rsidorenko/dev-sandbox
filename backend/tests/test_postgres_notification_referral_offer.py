"""Postgres regression: the «Спасибо за подписку» referral offer (C2) must NOT fire when the
user's most recent subscription update was a «free month per friend» bonus grant (C1), not a
real payment.

Background: ``_check_referral_offer_paid`` triggers on ``updated_at`` within 25h + ``plan_id IS NOT
NULL``. ``grant_subscription_days`` (referral bonus, actor=referral_bonus) bumps ``updated_at`` and
sets/keeps ``plan_id`` exactly like a payment — so before the fix a referrer who received a bonus
month got a spurious «Спасибо за подписку» offer. The fix excludes users whose ``updated_at``
coincides with a ``referral_bonus_awards`` row (referrer = this user).

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


def test_paid_offer_skips_referral_bonus_but_not_real_payment(pg_url: str) -> None:
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                await apply_postgres_migrations(conn)

            pref = "test-paid-offer-bonus"
            uid_a, uid_b, uid_c = f"{pref}-a", f"{pref}-b", f"{pref}-c"

            # Idempotent cleanup of any prior run (shared dev DB).
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM referral_bonus_awards WHERE referrer_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM notification_log WHERE internal_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id LIKE $1", f"{pref}%")
                await conn.execute("DELETE FROM user_identities WHERE internal_user_id LIKE $1", f"{pref}%")

            async with pool.acquire() as conn:
                # A — real first payment, no bonus → offer MUST fire.
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1,$2)",
                    99000001,
                    uid_a,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc, updated_at) "
                    "VALUES ($1,'active','1m',NOW()+INTERVAL'30 days',NOW())",
                    uid_a,
                )

                # B — fresh referral bonus (create-mode): updated_at == bonus.created_at → offer MUST NOT fire.
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1,$2)",
                    99000002,
                    uid_b,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc, updated_at) "
                    "VALUES ($1,'active','custom:30',NOW()+INTERVAL'30 days',NOW())",
                    uid_b,
                )
                await conn.execute(
                    "INSERT INTO referral_bonus_awards (award_id, referrer_user_id, payer_user_id, level, days_awarded, related_plan_id, created_at) "
                    "VALUES ($1,$2,$3,1,30,'1m',NOW())",
                    f"{pref}-award-b",
                    uid_b,
                    f"{pref}-payer",
                )

                # C — fresh real payment but a STALE bonus 2 days ago → offer MUST fire (filter is not a blanket block).
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1,$2)",
                    99000003,
                    uid_c,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc, updated_at) "
                    "VALUES ($1,'active','1m',NOW()+INTERVAL'30 days',NOW())",
                    uid_c,
                )
                await conn.execute(
                    "INSERT INTO referral_bonus_awards (award_id, referrer_user_id, payer_user_id, level, days_awarded, related_plan_id, created_at) "
                    "VALUES ($1,$2,$3,1,30,'1m',NOW()-INTERVAL'2 days')",
                    f"{pref}-award-c",
                    uid_c,
                    f"{pref}-payer",
                )

            scheduler = NotificationScheduler(pool=pool, bot_token="t", vless_provider=AsyncMock())
            scheduler._http_client = _FakeHttpClient()  # type: ignore[assignment]  # stub the Bot API call
            await scheduler._check_referral_offer_paid(datetime.now(UTC))

            async with pool.acquire() as conn:
                a_sent = await conn.fetchval(
                    "SELECT 1 FROM notification_log WHERE internal_user_id=$1 AND notification_type=$2",
                    uid_a,
                    _OFFER_TYPE,
                )
                b_sent = await conn.fetchval(
                    "SELECT 1 FROM notification_log WHERE internal_user_id=$1 AND notification_type=$2",
                    uid_b,
                    _OFFER_TYPE,
                )
                c_sent = await conn.fetchval(
                    "SELECT 1 FROM notification_log WHERE internal_user_id=$1 AND notification_type=$2",
                    uid_c,
                    _OFFER_TYPE,
                )

            assert a_sent == 1, "real first payment must get the paid offer"
            assert b_sent is None, "a fresh referral-bonus grant must NOT trigger the paid offer"
            assert c_sent == 1, "a stale bonus must not block the offer for a fresh real payment"
        finally:
            await pool.close()

    asyncio.run(main())
