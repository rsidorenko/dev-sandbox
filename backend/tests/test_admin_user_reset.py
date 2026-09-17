"""PostgreSQL-интеграция полного сброса internal/test пользователя из админки."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderResult
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_user_identity import PostgresUserIdentityRepository
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"
_TG_INTERNAL = 999_840_001
_TG_REGULAR = 999_840_002
_TG_PROVIDER_FAILURE = 999_840_003


@pytest.fixture
def pg_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        pytest.skip("DATABASE_URL not set; skipping admin user reset integration tests")
    return value


@pytest.fixture
def admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert response.status_code == 200
    return {"x-csrf-token": response.json()["csrf_token"]}


async def _cleanup(pool: asyncpg.Pool) -> None:
    telegram_ids = [_TG_INTERNAL, _TG_REGULAR, _TG_PROVIDER_FAILURE]
    user_ids = [f"u{telegram_id}" for telegram_id in telegram_ids]
    emails = [f"reset-{telegram_id}@example.test" for telegram_id in telegram_ids]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM admin_user_reset_actions WHERE telegram_user_id = ANY($1::bigint[])", telegram_ids
            )
            await conn.execute("DELETE FROM billing_ingestion_audit_events WHERE internal_fact_ref LIKE 'reset-test-%'")
            await conn.execute(
                "DELETE FROM billing_subscription_apply_audit_events WHERE internal_user_id = ANY($1::text[])", user_ids
            )
            await conn.execute(
                "DELETE FROM billing_subscription_apply_records WHERE internal_user_id = ANY($1::text[])", user_ids
            )
            await conn.execute("DELETE FROM billing_events_ledger WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM issuance_state WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM notification_log WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM web_notifications WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM promo_applied WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM promo_redemptions WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM promo_codes WHERE code = 'RESETTEST'")
            await conn.execute(
                "DELETE FROM admin_subscription_actions WHERE internal_user_id = ANY($1::text[])", user_ids
            )
            await conn.execute("DELETE FROM funnel_interactions WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM experiment_assignments WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute(
                "DELETE FROM telegram_ads_attributions WHERE internal_user_id = ANY($1::text[])", user_ids
            )
            await conn.execute(
                "DELETE FROM referral_bonus_awards WHERE payer_user_id = ANY($1::text[]) OR referrer_user_id = ANY($1::text[])",
                user_ids,
            )
            await conn.execute(
                "DELETE FROM referral_transactions WHERE internal_user_id = ANY($1::text[]) OR related_user_id = ANY($1::text[])",
                user_ids,
            )
            await conn.execute(
                "DELETE FROM referral_relationships WHERE referred_user_id = ANY($1::text[]) OR referrer_user_id = ANY($1::text[])",
                user_ids,
            )
            await conn.execute("DELETE FROM referral_balances WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute("DELETE FROM referral_codes WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute(
                "DELETE FROM email_verification_codes WHERE telegram_user_id = ANY($1::bigint[]) OR email = ANY($2::text[])",
                telegram_ids,
                emails,
            )
            await conn.execute("DELETE FROM user_emails WHERE telegram_user_id = ANY($1::bigint[])", telegram_ids)
            await conn.execute(
                "DELETE FROM bare_xray_traffic_state WHERE email LIKE 'user-u99984000%' OR email LIKE 'x-user-u99984000%'"
            )
            await conn.execute(
                "DELETE FROM bare_xray_traffic_daily WHERE email LIKE 'user-u99984000%' OR email LIKE 'x-user-u99984000%'"
            )
            await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = ANY($1::text[])", user_ids)
            await conn.execute(
                "DELETE FROM analytics_internal_audit WHERE internal_user_id = ANY($1::text[])", user_ids
            )
            await conn.execute("DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])", telegram_ids)


async def _make_identity(pool: asyncpg.Pool, telegram_user_id: int, *, internal: bool) -> str:
    internal_user_id = f"u{telegram_user_id}"
    await pool.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used, is_internal) "
        "VALUES ($1,$2,TRUE,$3)",
        telegram_user_id,
        internal_user_id,
        internal,
    )
    await pool.execute(
        "INSERT INTO subscription_snapshots "
        "(internal_user_id,state_label,active_until_utc,trial_started_at,trial_expires_at,plan_id,device_count) "
        "VALUES ($1,'active',NOW() + INTERVAL '3 days',NOW(),NOW() + INTERVAL '3 days','trial',1)",
        internal_user_id,
    )
    return internal_user_id


async def _fill_user_state(pool: asyncpg.Pool, telegram_user_id: int, internal_user_id: str) -> None:
    now = datetime.now(UTC)
    email = f"reset-{telegram_user_id}@example.test"
    fact_ref = f"reset-test-{telegram_user_id}"
    await pool.execute(
        "INSERT INTO issuance_state "
        "(internal_user_id,issue_idempotency_key,issuance_state,provider_issuance_ref) "
        "VALUES ($1,'reset-issue','issued','opaque-reset-ref')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO billing_events_ledger "
        "(internal_fact_ref,billing_provider_key,external_event_id,event_type,event_effective_at,event_received_at,"
        "internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id) "
        "VALUES ($1,'test','reset-external','subscription_activated',$2,$2,$3,100,'RUB','accepted','reset-corr')",
        fact_ref,
        now,
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO billing_ingestion_audit_events "
        "(audit_event_id,internal_fact_ref,billing_provider_key,external_event_id,ingestion_correlation_id,"
        "operation,outcome,billing_event_status,is_idempotent_replay) VALUES "
        "('reset-ingest-audit',$1,'test','reset-external','reset-corr','billing_fact_ingested','accepted',"
        "'accepted',FALSE)",
        fact_ref,
    )
    await pool.execute(
        "INSERT INTO billing_subscription_apply_records (internal_fact_ref,internal_user_id,apply_outcome) "
        "VALUES ($1,$2,'active_applied')",
        fact_ref,
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO billing_subscription_apply_audit_events "
        "(audit_event_id,internal_fact_ref,internal_user_id,billing_provider_key,external_event_id,event_type,"
        "billing_event_status,apply_outcome,reason) VALUES "
        "('reset-apply-audit',$1,$2,'test','reset-external','subscription_activated','accepted','active_applied','ok')",
        fact_ref,
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO funnel_interactions (internal_user_id,event_name,surface,dedup_key) "
        "VALUES ($1,'setup_started','telegram','reset-setup')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO experiment_assignments (internal_user_id,experiment_key,variant) "
        "VALUES ($1,'checkout_reassurance_v1','control')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO telegram_ads_attributions (internal_user_id,campaign_key,telegram_update_id) "
        "VALUES ($1,'reset_test',999840001)",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO notification_log (internal_user_id,notification_type) VALUES ($1,'trial_expiring')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO web_notifications (internal_user_id,kind,title) VALUES ($1,'trial_expiring','Test')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO user_emails (telegram_user_id,email,is_verified,verified_at) VALUES ($1,$2,TRUE,NOW())",
        telegram_user_id,
        email,
    )
    await pool.execute(
        "INSERT INTO email_verification_codes (email,code_hash,telegram_user_id,expires_at) "
        "VALUES ($1,'hash',$2,NOW() + INTERVAL '10 minutes')",
        email,
        telegram_user_id,
    )
    await pool.execute(
        "INSERT INTO promo_codes "
        "(code,audience,discount_kind,discount_value,redemptions_count,max_redemptions_per_user) "
        "VALUES ('RESETTEST','all_users','percent',10,1,1)",
    )
    await pool.execute(
        "INSERT INTO promo_applied (internal_user_id,code,expires_at) VALUES ($1,'RESETTEST',NOW() + INTERVAL '1 day')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO promo_redemptions "
        "(redemption_id,code,internal_user_id,idempotency_key,base_rubles,discounted_rubles,discount_kopecks) "
        "VALUES ('reset-redemption','RESETTEST',$1,'reset-redemption-idem',100,90,1000)",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO referral_balances (internal_user_id,balance_kopecks) VALUES ($1,1000)",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO referral_codes (internal_user_id,referral_code) VALUES ($1,'RST84001')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO referral_relationships "
        "(relationship_id,referred_user_id,referrer_user_id,level) VALUES ('reset-rel',$1,'other-test-user',1)",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO referral_transactions "
        "(transaction_id,internal_user_id,amount_kopecks,transaction_type,related_user_id) "
        "VALUES ('reset-tx',$1,1000,'referral_credit','other-test-user')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO referral_bonus_awards "
        "(award_id,referrer_user_id,payer_user_id,level,days_awarded,related_plan_id) "
        "VALUES ('reset-award','other-test-user',$1,1,30,'1m')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO referral_bonus_awards "
        "(award_id,referrer_user_id,payer_user_id,level,days_awarded,related_plan_id) "
        "VALUES ('reset-award-owned',$1,'other-payer',1,30,'1m')",
        internal_user_id,
    )
    await pool.execute(
        "INSERT INTO admin_subscription_actions "
        "(actor,internal_user_id,action,days,new_active_until,correlation_id) "
        "VALUES ('admin',$1,'add_days',3,NOW() + INTERVAL '3 days','reset-admin-action')",
        internal_user_id,
    )
    for prefix, server_id in (("user-", 984001), ("x-user-", 984002)):
        traffic_email = f"{prefix}{internal_user_id[:16]}"
        await pool.execute(
            "INSERT INTO bare_xray_traffic_state (server_id,email,uplink_last,downlink_last) VALUES ($1,$2,1,2)",
            server_id,
            traffic_email,
        )
        await pool.execute(
            "INSERT INTO bare_xray_traffic_daily "
            "(stat_date,server_id,email,uplink_bytes,downlink_bytes) VALUES ($1,$2,$3,1,2)",
            date.today(),
            server_id,
            traffic_email,
        )


class _FailingDeleteProvider:
    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult:
        del internal_user_id
        return VlessProviderResult(outcome=VlessProviderOutcome.ERROR)


def test_admin_reset_test_account_full_flow(pg_url: str, admin_env: None) -> None:
    async def run() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        client: httpx.AsyncClient | None = None
        failing_client: httpx.AsyncClient | None = None
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)

            app = build_web_api_app(pool=pool)
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            headers = await _login(client)

            regular_uid = await _make_identity(pool, _TG_REGULAR, internal=False)
            regular_path = f"/api/v1/admin/users/{regular_uid}/reset-test-account"
            response = await client.post(
                regular_path,
                headers=headers,
                json={"confirmation_telegram_user_id": _TG_REGULAR, "reason": "Проверка защиты"},
            )
            assert response.status_code == 409
            assert response.json()["error"] == "test_account_required"

            uid = await _make_identity(pool, _TG_INTERNAL, internal=True)
            await _fill_user_state(pool, _TG_INTERNAL, uid)
            await app.state.vless_provider.create_user(internal_user_id=uid)
            path = f"/api/v1/admin/users/{uid}/reset-test-account"

            mismatch = await client.post(
                path,
                headers=headers,
                json={"confirmation_telegram_user_id": _TG_INTERNAL + 1, "reason": "Проверка"},
            )
            assert mismatch.json()["error"] == "confirmation_mismatch"
            invalid_reason = await client.post(
                path,
                headers=headers,
                json={"confirmation_telegram_user_id": _TG_INTERNAL, "reason": " "},
            )
            assert invalid_reason.status_code == 400
            assert invalid_reason.json()["error"] == "invalid_reason"
            no_csrf = await client.post(
                path,
                json={"confirmation_telegram_user_id": _TG_INTERNAL, "reason": "Проверка"},
            )
            assert no_csrf.status_code == 403
            anonymous = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            try:
                no_auth = await anonymous.post(
                    path,
                    json={"confirmation_telegram_user_id": _TG_INTERNAL, "reason": "Проверка"},
                )
                assert no_auth.status_code == 401
            finally:
                await anonymous.aclose()

            response = await client.post(
                path,
                headers=headers,
                json={"confirmation_telegram_user_id": _TG_INTERNAL, "reason": "Повторный trial flow"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["telegram_user_id"] == _TG_INTERNAL
            assert body["internal_user_id"] == uid
            assert body["vpn_outcome"] == "success"
            assert body["deleted_rows"]["user_identities"] == 1
            assert body["deleted_rows"]["bare_xray_traffic_daily"] == 2
            assert body["deleted_rows"]["experiment_assignments"] == 1
            assert await pool.fetchval("SELECT count(*) FROM user_identities WHERE internal_user_id=$1", uid) == 0
            assert (
                await pool.fetchval("SELECT count(*) FROM subscription_snapshots WHERE internal_user_id=$1", uid) == 0
            )
            assert await pool.fetchval("SELECT count(*) FROM billing_events_ledger WHERE internal_user_id=$1", uid) == 0
            assert await pool.fetchval("SELECT count(*) FROM funnel_interactions WHERE internal_user_id=$1", uid) == 0
            assert (
                await pool.fetchval("SELECT count(*) FROM experiment_assignments WHERE internal_user_id=$1", uid) == 0
            )
            assert (
                await pool.fetchval("SELECT count(*) FROM bare_xray_traffic_daily WHERE email LIKE '%u999840001%'") == 0
            )
            assert await pool.fetchval("SELECT redemptions_count FROM promo_codes WHERE code='RESETTEST'") == 0
            assert await pool.fetchval("SELECT count(*) FROM referral_bonus_awards WHERE award_id='reset-award'") == 1
            assert (
                await pool.fetchval("SELECT count(*) FROM referral_bonus_awards WHERE award_id='reset-award-owned'")
                == 0
            )
            assert (
                await app.state.vless_provider.get_user_config(internal_user_id=uid)
            ).outcome == VlessProviderOutcome.NOT_FOUND

            audit = await pool.fetchrow(
                "SELECT actor,reason,vpn_outcome,deleted_rows,correlation_id "
                "FROM admin_user_reset_actions WHERE telegram_user_id=$1",
                _TG_INTERNAL,
            )
            assert audit["actor"] == "admin"
            assert audit["reason"] == "Повторный trial flow"
            assert audit["vpn_outcome"] == "success"
            assert audit["correlation_id"] == body["correlation_id"]

            recreated = await PostgresUserIdentityRepository(pool).create_if_absent(_TG_INTERNAL)
            assert recreated.internal_user_id == uid
            identity = await pool.fetchrow(
                "SELECT trial_used,is_internal FROM user_identities WHERE internal_user_id=$1",
                uid,
            )
            assert identity["trial_used"] is False
            assert identity["is_internal"] is False
            assert (
                await pool.fetchval("SELECT count(*) FROM subscription_snapshots WHERE internal_user_id=$1", uid) == 0
            )

            failure_uid = await _make_identity(pool, _TG_PROVIDER_FAILURE, internal=True)
            failing_app = build_web_api_app(pool=pool)
            failing_app.state.vless_provider = _FailingDeleteProvider()
            failing_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=failing_app), base_url="http://test")
            failing_headers = await _login(failing_client)
            failure = await failing_client.post(
                f"/api/v1/admin/users/{failure_uid}/reset-test-account",
                headers=failing_headers,
                json={"confirmation_telegram_user_id": _TG_PROVIDER_FAILURE, "reason": "Ошибка VPN"},
            )
            assert failure.status_code == 502
            assert failure.json()["error"] == "vpn_delete_failed"
            assert (
                await pool.fetchval("SELECT count(*) FROM user_identities WHERE internal_user_id=$1", failure_uid) == 1
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM subscription_snapshots WHERE internal_user_id=$1", failure_uid
                )
                == 1
            )
        finally:
            if failing_client is not None:
                await failing_client.aclose()
            if client is not None:
                await client.aclose()
            await _cleanup(pool)
            await pool.close()

    asyncio.run(run())
