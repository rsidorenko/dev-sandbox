"""Integration tests for the web balance-payment endpoints (Фаза 2).

Opt-in via DATABASE_URL — exercises the REAL handlers against PostgreSQL with a
StubVlessProvider, validating atomic debit + snapshot + idempotency (UNIQUE description
dedup + advisory lock) + commissions. Auth — Bearer subscription-token
(куки/CSRF больше не используются).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Distinctive test ids (far from any real values).
_WEB_TG = -9_000_000_100
_REFERRER_TG = 8_888_888_800_100
_WEB_INTERNAL = f"web_balance_test_{abs(_WEB_TG)}"
_SUB_TOKEN = "balWebSubToken1234567890ab"


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping web balance-payment integration tests")
    return url


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM referral_transactions WHERE internal_user_id = ANY($1::text[])",
            [_WEB_INTERNAL, f"u{_REFERRER_TG}"],
        )
        await conn.execute(
            "DELETE FROM referral_relationships WHERE referred_user_id = ANY($1::text[]) OR referrer_user_id = ANY($1::text[])",
            [_WEB_INTERNAL, f"u{_REFERRER_TG}"],
        )
        await conn.execute(
            "DELETE FROM referral_balances WHERE internal_user_id = ANY($1::text[])",
            [_WEB_INTERNAL, f"u{_REFERRER_TG}"],
        )
        await conn.execute(
            "DELETE FROM referral_codes WHERE internal_user_id = ANY($1::text[])",
            [_WEB_INTERNAL, f"u{_REFERRER_TG}"],
        )
        await conn.execute(
            "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])",
            [_WEB_TG, _REFERRER_TG],
        )


def _make_authed_client(pool):
    """httpx-клиент с Bearer subscription-token на том же loop, что и пул.
    Раньше auth не ходил в БД (JWT без lookup) и sync TestClient проходил; теперь
    require_auth делает token-lookup — anyio-портал TestClient даёт cross-loop
    asyncpg (паттерн test_web_api_notifications.py)."""
    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=pool)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client.headers["Authorization"] = f"Bearer {_SUB_TOKEN}"
    return client


def test_balance_payment_success_completes_and_debits(pg_url, monkeypatch):
    """Web user with sufficient balance → 200 completed, active subscription, debit + audit row,
    balance reduced by the plan price."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, $2)"
                    " ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks=$2",
                    _WEB_INTERNAL,
                    1000_00,
                )

            client = _make_authed_client(pool)
            resp = await client.post(
                "/api/v1/payment/balance",
                json={
                    "plan_id": "1m",
                    "device_count": 5,
                    "offer_accepted": True,
                    "offer_version": "2026-09-30",
                    "expected_amount_kopecks": 24900,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "completed"
            assert body["plan_id"] == "1m"
            assert body["amount_kopecks"] == 249_00
            assert body["active_until"]
            # Balance reduced by 249.00 → 751.00 remaining.
            assert body["balance_rubles_remaining"] == 751.0

            async with pool.acquire() as conn:
                snap = await conn.fetchrow(
                    "SELECT state_label, plan_id, device_count FROM subscription_snapshots WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                assert snap is not None and snap["state_label"] == "active" and snap["plan_id"] == "1m"
                bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                assert bal == 751_00
                debit = await conn.fetchrow(
                    "SELECT amount_kopecks, transaction_type FROM referral_transactions"
                    " WHERE internal_user_id=$1 AND transaction_type='subscription_payment'",
                    _WEB_INTERNAL,
                )
                assert debit is not None and debit["amount_kopecks"] == -249_00
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_balance_payment_insufficient_returns_402_and_no_audit(pg_url, monkeypatch):
    """Balance < total → 402, balance unchanged, NO audit row (the tx rolled back the insert)."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, $2)"
                    " ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks=$2",
                    _WEB_INTERNAL,
                    100_00,  # less than 249.00
                )

            client = _make_authed_client(pool)
            resp = await client.post(
                "/api/v1/payment/balance",
                json={
                    "plan_id": "1m",
                    "device_count": 5,
                    "offer_accepted": True,
                    "offer_version": "2026-09-30",
                    "expected_amount_kopecks": 24900,
                },
            )
            assert resp.status_code == 402, resp.text
            assert resp.json()["error"] == "insufficient_balance"

            async with pool.acquire() as conn:
                bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                assert bal == 100_00  # unchanged
                tx = await conn.fetchval(
                    "SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                assert tx == 0  # audit insert rolled back with the debit
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_balance_payment_idempotent_same_key(pg_url, monkeypatch):
    """Two requests with the same X-Idempotency-Key → completed then already_completed;
    exactly ONE debit row in the DB."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, $2)"
                    " ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks=$2",
                    _WEB_INTERNAL,
                    1000_00,
                )

            client = _make_authed_client(pool)
            headers = {"X-Idempotency-Key": "stable-key-123"}
            r1 = await client.post(
                "/api/v1/payment/balance",
                json={
                    "plan_id": "1m",
                    "device_count": 5,
                    "offer_accepted": True,
                    "offer_version": "2026-09-30",
                    "expected_amount_kopecks": 24900,
                },
                headers=headers,
            )
            assert r1.status_code == 200 and r1.json()["status"] == "completed"
            r2 = await client.post(
                "/api/v1/payment/balance",
                json={
                    "plan_id": "1m",
                    "device_count": 5,
                    "offer_accepted": True,
                    "offer_version": "2026-09-30",
                    "expected_amount_kopecks": 24900,
                },
                headers=headers,
            )
            assert r2.status_code == 200 and r2.json()["status"] == "already_completed"

            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT count(*) FROM referral_transactions"
                    " WHERE internal_user_id=$1 AND transaction_type='subscription_payment'",
                    _WEB_INTERNAL,
                )
                assert count == 1  # deduped — only one debit
                bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                assert bal == 751_00  # debited only once
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_balance_payment_invalid_plan_id(pg_url, monkeypatch):
    """Unknown plan_id → 400 invalid_plan_id (before any DB write)."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
            client = _make_authed_client(pool)
            resp = await client.post(
                "/api/v1/payment/balance",
                json={
                    "plan_id": "nonexistent",
                    "device_count": 5,
                    "offer_accepted": True,
                    "offer_version": "2026-09-30",
                    "expected_amount_kopecks": 24900,
                },
            )
            assert resp.status_code == 400 and resp.json()["error"] == "invalid_plan_id"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_add_device_balance_increases_device_count_without_extending(pg_url, monkeypatch):
    """Add-device via balance: device_count rises, active_until is UNCHANGED (no renewal),
    balance reduced by the extra-device cost."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            from datetime import UTC, datetime, timedelta

            existing_until = datetime.now(UTC) + timedelta(days=10)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, $2)"
                    " ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks=$2",
                    _WEB_INTERNAL,
                    1000_00,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label, active_until_utc, plan_id, device_count)"
                    " VALUES ($1, 'active', $2, '1m', 5)",
                    _WEB_INTERNAL,
                    existing_until,
                )

            client = _make_authed_client(pool)
            resp = await client.post(
                "/api/v1/payment/add-devices/balance",
                json={"device_count": 7, "offer_accepted": True, "offer_version": "2026-09-30"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "completed"
            assert body["device_count"] == 7

            async with pool.acquire() as conn:
                snap = await conn.fetchrow(
                    "SELECT device_count, active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                assert snap["device_count"] == 7
                # active_until must NOT have changed (no renewal).
                assert snap["active_until_utc"].replace(tzinfo=UTC, microsecond=0) == existing_until.replace(
                    microsecond=0
                )
                bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1",
                    _WEB_INTERNAL,
                )
                # extra cost: 2 devices * round(80/30*30)=80 → 160.00 → balance 1000-160=840
                assert bal == 840_00
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_add_device_balance_no_active_subscription(pg_url, monkeypatch):
    """Add-device without an active subscription → 409 no_active_subscription."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
            client = _make_authed_client(pool)
            resp = await client.post(
                "/api/v1/payment/add-devices/balance",
                json={"device_count": 7, "offer_accepted": True, "offer_version": "2026-09-30"},
            )
            assert resp.status_code == 409 and resp.json()["error"] == "no_active_subscription"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_balance_payment_credits_l1_commission_to_referrer(pg_url, monkeypatch):
    """Plan-purchase balance payment credits L1 commission to the payer's referrer
    (parity with the bot's _credit_referral_commissions)."""

    async def main() -> None:
        referrer_internal = f"u{_REFERRER_TG}"
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    _WEB_TG,
                    _WEB_INTERNAL,
                    _SUB_TOKEN,
                )
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    _REFERRER_TG,
                    referrer_internal,
                )
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, $2)"
                    " ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks=$2",
                    _WEB_INTERNAL,
                    1000_00,
                )
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)"
                    " ON CONFLICT (internal_user_id) DO UPDATE SET referral_code=$2",
                    referrer_internal,
                    "BALREF1",
                )
                await conn.execute(
                    "INSERT INTO referral_relationships"
                    " (relationship_id, referred_user_id, referrer_user_id, level)"
                    " VALUES ($1, $2, $3, 1)",
                    "rel-bal-test-1",
                    _WEB_INTERNAL,
                    referrer_internal,
                )

            client = _make_authed_client(pool)
            resp = await client.post(
                "/api/v1/payment/balance",
                json={
                    "plan_id": "1m",
                    "device_count": 5,
                    "offer_accepted": True,
                    "offer_version": "2026-09-30",
                    "expected_amount_kopecks": 24900,
                },
            )
            assert resp.status_code == 200, resp.text

            async with pool.acquire() as conn:
                # 1m L1 (balanced_v1 — дефолт reward_policy в схеме) = 0.20
                # → 249.00 * 0.20 = 49.80 → 4980 kopecks.
                credit = await conn.fetchrow(
                    "SELECT amount_kopecks, transaction_type FROM referral_transactions"
                    " WHERE internal_user_id=$1 AND transaction_type='referral_credit'",
                    referrer_internal,
                )
                assert credit is not None and credit["amount_kopecks"] == 4980
                ref_bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1",
                    referrer_internal,
                )
                assert ref_bal == 4980
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())
