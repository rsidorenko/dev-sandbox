"""Оплата и выдача доступа сохраняются при пересечении операций и рестарте."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.application.balance_payment_service import BalancePaymentStatus, pay_plan_from_balance
from app.issuance.vless_provider import StubVlessProvider
from app.persistence.postgres_migrations import apply_postgres_migrations


@pytest.fixture
async def account():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("Нужен локальный PostgreSQL")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    uid = f"balance-recovery-{uuid.uuid4()}"
    deadline = datetime.now(UTC) + timedelta(days=10)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)",
        uid,
        -uuid.uuid4().int % -(2**60),
    )
    await pool.execute("INSERT INTO referral_balances (internal_user_id,balance_kopecks) VALUES ($1,100000)", uid)
    await pool.execute(
        "INSERT INTO subscription_snapshots (internal_user_id,state_label,plan_id,device_count,active_until_utc)"
        " VALUES ($1,'active','1m',5,$2)",
        uid,
        deadline,
    )
    try:
        yield pool, uid, deadline
    finally:
        await pool.execute("DELETE FROM referral_transactions WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM referral_balances WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
        await pool.close()


async def purchase(pool, uid, provider, key):
    return await pay_plan_from_balance(
        pool=pool,
        composition=None,
        internal_user_id=uid,
        plan_id="1m",
        device_count=5,
        correlation_id=key,
        vless_provider=provider,
    )


async def test_failed_purchase_does_not_erase_later_paid_renewal(account):
    pool, uid, deadline = account
    entered, release = asyncio.Event(), asyncio.Event()

    class FailingProvider:
        async def sync_user_access(self, **kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError("тестовый отказ панели")

    first = asyncio.create_task(purchase(pool, uid, FailingProvider(), "first"))
    await asyncio.wait_for(entered.wait(), timeout=3)
    second = await purchase(pool, uid, StubVlessProvider(), "second")
    assert second.status == BalancePaymentStatus.COMPLETED
    release.set()
    assert (await first).status == BalancePaymentStatus.PROVISIONING
    actual = await pool.fetchval("SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1", uid)
    assert actual >= deadline + timedelta(days=60)
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 50200
    assert await pool.fetchval("SELECT count(*) FROM balance_access_jobs WHERE internal_user_id=$1", uid) == 2
    assert await pool.fetchval("SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1", uid) == 2


async def test_restart_recovers_committed_payment_without_second_debit(account):
    from app.application.balance_access_jobs import recover_access_jobs

    pool, uid, deadline = account

    class CrashedProvider:
        async def sync_user_access(self, **kwargs):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await purchase(pool, uid, CrashedProvider(), "restart")
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 75100
    assert await pool.fetchval("SELECT status FROM balance_access_jobs WHERE internal_user_id=$1", uid) == "pending"
    await pool.execute(
        "UPDATE balance_access_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE internal_user_id=$1", uid
    )
    assert await recover_access_jobs(pool, StubVlessProvider()) == 1
    replay = await purchase(pool, uid, StubVlessProvider(), "restart")
    assert replay.status == BalancePaymentStatus.ALREADY_COMPLETED
    assert replay.amount_kopecks == 0
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 75100
    assert await pool.fetchval(
        "SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1", uid
    ) == deadline + timedelta(days=30)


async def test_replay_resumes_pending_payment(account):
    pool, uid, _deadline = account
    accepted = await purchase(pool, uid, None, "replay")
    assert accepted.status == BalancePaymentStatus.PROVISIONING
    replay = await purchase(pool, uid, StubVlessProvider(), "replay")
    assert replay.status == BalancePaymentStatus.ALREADY_COMPLETED
    assert await pool.fetchval("SELECT status FROM balance_access_jobs WHERE internal_user_id=$1", uid) == "done"
    assert await pool.fetchval("SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1", uid) == 1


async def test_commission_error_rolls_back_debit_snapshot_and_job(account, monkeypatch):
    pool, uid, deadline = account

    async def fail_commission(conn, payer, *args, **kwargs):
        await conn.execute(
            "UPDATE referral_balances SET balance_kopecks=balance_kopecks+123 WHERE internal_user_id=$1", payer
        )
        raise RuntimeError("тестовый сбой комиссии после записи")

    monkeypatch.setattr("app.application.balance_payment_service.credit_commissions_in_connection", fail_commission)
    result = await purchase(pool, uid, StubVlessProvider(), "rollback")
    assert result.status == BalancePaymentStatus.INTERNAL_ERROR
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 100000
    assert (
        await pool.fetchval("SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        == deadline
    )
    assert await pool.fetchval("SELECT count(*) FROM balance_access_jobs WHERE internal_user_id=$1", uid) == 0
    assert await pool.fetchval("SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1", uid) == 0


async def test_two_workers_do_not_claim_same_live_lease(account):
    from app.application.balance_access_jobs import fulfill_access_job
    from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult

    pool, uid, _deadline = account
    await purchase(pool, uid, None, "workers")
    key = await pool.fetchval("SELECT operation_key FROM balance_access_jobs WHERE internal_user_id=$1", uid)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class SlowProvider:
        async def sync_user_access(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            await release.wait()
            return VlessProviderResult(VlessProviderOutcome.SUCCESS)

    first = asyncio.create_task(fulfill_access_job(pool, SlowProvider(), uid, key))
    await asyncio.wait_for(entered.wait(), 3)
    try:
        assert await fulfill_access_job(pool, SlowProvider(), uid, key) == "pending"
    finally:
        release.set()
    assert await first == "done"
    assert len(calls) == 1


async def test_stale_worker_cannot_complete_reclaimed_job(account):
    from app.application.balance_access_jobs import fulfill_access_job
    from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult

    pool, uid, _deadline = account
    await purchase(pool, uid, None, "lease-fence")
    key = await pool.fetchval("SELECT operation_key FROM balance_access_jobs WHERE internal_user_id=$1", uid)
    new_token = uuid.uuid4()

    class ReclaimedProvider:
        async def sync_user_access(self, **kwargs):
            await pool.execute(
                "UPDATE balance_access_jobs SET lease_token=$2 WHERE internal_user_id=$1", uid, new_token
            )
            return VlessProviderResult(VlessProviderOutcome.SUCCESS)

    assert await fulfill_access_job(pool, ReclaimedProvider(), uid, key) == "pending"
    row = await pool.fetchrow("SELECT status,lease_token FROM balance_access_jobs WHERE internal_user_id=$1", uid)
    assert row["status"] == "pending" and row["lease_token"] == new_token
    await pool.execute(
        "UPDATE balance_access_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE internal_user_id=$1", uid
    )
    assert await fulfill_access_job(pool, StubVlessProvider(), uid, key) == "done"


async def test_typed_provider_failure_is_pending_and_worker_retries(account):
    from app.application.balance_access_jobs import recover_access_jobs
    from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult

    pool, uid, _deadline = account

    class UnavailableProvider:
        async def sync_user_access(self, **kwargs):
            return VlessProviderResult(VlessProviderOutcome.UNAVAILABLE)

    result = await purchase(pool, uid, UnavailableProvider(), "typed-failure")
    assert result.status == BalancePaymentStatus.PROVISIONING
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 75100
    assert await recover_access_jobs(pool, StubVlessProvider()) == 0
    await pool.execute("UPDATE balance_access_jobs SET next_attempt_at=NOW() WHERE internal_user_id=$1", uid)
    assert await recover_access_jobs(pool, StubVlessProvider()) == 1


async def test_expired_access_job_is_terminal(account):
    from app.application.balance_access_jobs import recover_access_jobs
    from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult

    pool, uid, _deadline = account
    await purchase(pool, uid, None, "expired")

    class ExpiredProvider:
        async def sync_user_access(self, **kwargs):
            return VlessProviderResult(VlessProviderOutcome.NOT_FOUND)

    assert await recover_access_jobs(pool, ExpiredProvider()) == 1
    assert await pool.fetchval("SELECT status FROM balance_access_jobs WHERE internal_user_id=$1", uid) == "superseded"
    assert await recover_access_jobs(pool, StubVlessProvider()) == 0


async def test_device_purchase_persists_access_job_and_preserves_deadline(account):
    from app.application.balance_access_jobs import recover_access_jobs
    from app.application.balance_payment_service import add_device_from_balance

    pool, uid, deadline = account
    result = await add_device_from_balance(
        pool=pool, composition=None, internal_user_id=uid, new_device_count=6, correlation_id="device"
    )
    assert result.status == BalancePaymentStatus.PROVISIONING
    assert (
        await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid)
        == 100000 - result.amount_kopecks
    )
    assert await pool.fetchval("SELECT device_count FROM subscription_snapshots WHERE internal_user_id=$1", uid) == 6
    assert (
        await pool.fetchval("SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        == deadline
    )
    assert await recover_access_jobs(pool, StubVlessProvider()) == 1
    replay = await add_device_from_balance(
        pool=pool,
        composition=None,
        internal_user_id=uid,
        new_device_count=6,
        correlation_id="device",
        vless_provider=StubVlessProvider(),
    )
    assert replay.status == BalancePaymentStatus.ALREADY_COMPLETED
    assert await pool.fetchval("SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1", uid) == 1


async def test_expired_subscription_cannot_buy_devices(account):
    from app.application.balance_payment_service import add_device_from_balance

    pool, uid, _deadline = account
    await pool.execute(
        "UPDATE subscription_snapshots SET active_until_utc=NOW()-INTERVAL '1 second' WHERE internal_user_id=$1", uid
    )
    result = await add_device_from_balance(pool=pool, composition=None, internal_user_id=uid, new_device_count=6)
    assert result.status == BalancePaymentStatus.NO_ACTIVE
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 100000
    assert await pool.fetchval("SELECT count(*) FROM balance_access_jobs WHERE internal_user_id=$1", uid) == 0


async def test_real_commission_survives_crash_and_is_not_credited_twice(account, monkeypatch):
    from unittest.mock import AsyncMock

    pool, uid, _deadline = account
    referrer = f"recovery-referrer-{uuid.uuid4()}"
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)",
        referrer,
        -uuid.uuid4().int % -(2**60),
    )
    await pool.execute(
        "INSERT INTO referral_relationships (relationship_id,referred_user_id,referrer_user_id,level) VALUES ($1,$2,$3,1)",
        f"rel-{uuid.uuid4()}",
        uid,
        referrer,
    )
    monkeypatch.setattr(
        "app.application.balance_payment_service.award_referral_bonus_month_best_effort", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.application.balance_payment_service.award_referral_invitee_bonus_best_effort", AsyncMock(return_value=None)
    )

    class CrashedProvider:
        async def sync_user_access(self, **kwargs):
            raise asyncio.CancelledError()

    try:
        with pytest.raises(asyncio.CancelledError):
            await purchase(pool, uid, CrashedProvider(), "commission-crash")
        assert (
            await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", referrer)
            == 4980
        )
        await pool.execute(
            "UPDATE balance_access_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE internal_user_id=$1", uid
        )
        result = await purchase(pool, uid, StubVlessProvider(), "commission-crash")
        assert result.status == BalancePaymentStatus.ALREADY_COMPLETED
        assert (
            await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", referrer)
            == 4980
        )
        assert (
            await pool.fetchval("SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1", referrer) == 1
        )
    finally:
        await pool.execute("DELETE FROM referral_relationships WHERE referrer_user_id=$1", referrer)
        await pool.execute("DELETE FROM referral_transactions WHERE internal_user_id=$1", referrer)
        await pool.execute("DELETE FROM referral_balances WHERE internal_user_id=$1", referrer)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", referrer)


async def test_http_handler_accepts_pending_and_replay_resumes_without_charge(account):
    import httpx

    from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult
    from app.web_api.app import build_web_api_app

    pool, uid, _deadline = account
    token = uuid.uuid4().hex
    await pool.execute("UPDATE user_identities SET subscription_token=$2 WHERE internal_user_id=$1", uid, token)
    app = build_web_api_app(pool=pool)

    class UnavailableProvider:
        async def sync_user_access(self, **kwargs):
            return VlessProviderResult(VlessProviderOutcome.UNAVAILABLE)

    app.state.vless_provider = UnavailableProvider()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": "http-pending"},
    ) as client:
        first = await client.post("/api/v1/payment/balance", json={"plan_id": "1m", "device_count": 5})
        assert first.status_code == 202 and first.json()["ok"] is True
        assert first.json()["status"] == "provisioning"
        assert "Повторно платить не нужно" in first.json()["message"]
        repeat = await client.post("/api/v1/payment/balance", json={"plan_id": "1m", "device_count": 5})
        assert repeat.status_code == 202 and repeat.json()["amount_kopecks"] == 0
        app.state.vless_provider = StubVlessProvider()
        await pool.execute("UPDATE balance_access_jobs SET next_attempt_at=NOW() WHERE internal_user_id=$1", uid)
        resumed = await client.post("/api/v1/payment/balance", json={"plan_id": "1m", "device_count": 5})
        assert resumed.status_code == 200 and resumed.json()["status"] == "already_completed"
    assert await pool.fetchval("SELECT balance_kopecks FROM referral_balances WHERE internal_user_id=$1", uid) == 75100
    assert await pool.fetchval("SELECT count(*) FROM referral_transactions WHERE internal_user_id=$1", uid) == 1


@pytest.mark.parametrize("with_promo", [False, True])
async def test_device_purchase_uses_held_connection_for_promo(account, with_promo):
    from app.application.balance_payment_service import add_device_from_balance
    from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind
    from app.persistence.postgres_promo import PostgresPromoAppliedRepository, PostgresPromoCodeRepository

    pool, uid, _deadline = account
    code = uuid.uuid4().hex.upper()
    now = datetime.now(UTC)
    if with_promo:
        await PostgresPromoCodeRepository(pool).create(
            PromoCode(
                code=code,
                audience=PromoAudience.ALL_USERS,
                discount_kind=PromoDiscountKind.PERCENT,
                discount_value=50,
                max_redemptions=None,
                redemptions_count=0,
                expires_at=None,
                is_active=True,
                note=None,
                created_at=now,
                updated_at=now,
                created_by=None,
                max_redemptions_per_user=1,
            )
        )
        await PostgresPromoAppliedRepository(pool).upsert(uid, code, now + timedelta(days=1))
    single = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)
    try:
        async with asyncio.timeout(2):
            result = await add_device_from_balance(
                pool=single,
                composition=None,
                internal_user_id=uid,
                new_device_count=6,
                correlation_id="single-connection",
                vless_provider=StubVlessProvider(),
            )
        assert result.status == BalancePaymentStatus.COMPLETED
        assert result.amount_kopecks == (4000 if with_promo else 8000)
        if with_promo:
            assert await pool.fetchval("SELECT redemptions_count FROM promo_codes WHERE code=$1", code) == 1
    finally:
        await single.close()
        await pool.execute("DELETE FROM promo_applied WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM promo_redemptions WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM promo_codes WHERE code=$1", code)
