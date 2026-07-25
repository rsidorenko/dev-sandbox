"""Интеграционные тесты GET /api/v1/user/referral — реферальный дашборд.

Opt-in via DATABASE_URL: гоняет реальный обработчик против PostgreSQL
(migrations). Проверяет структуру ответа: код, ссылки, баланс, разбивку
L1/L2 (invited/paid), всего заработано и историю транзакций. JWT — через
_issue_jwt + cookie session (эталон test_web_api_promocode).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.auth import _issue_jwt
from app.web_api.middleware import generate_csrf_token

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Главный (реферер) аккаунт
_WEB_TG = -9_000_000_400
_WEB_INTERNAL = f"web_ref_test_{abs(_WEB_TG)}"
_TEST_EMAIL = "ref-web-test@example.com"
_REF_CODE = "TESTREF1"

# Приглашённые L1: l1a оплатил, l1b — нет
_L1A_TG, _L1A_INT = -9_000_000_401, "web_ref_l1a"
_L1B_TG, _L1B_INT = -9_000_000_402, "web_ref_l1b"
# Приглашённый L2: не оплатил
_L2_TG, _L2_INT = -9_000_000_403, "web_ref_l2"

_ALL_TG = [_WEB_TG, _L1A_TG, _L1B_TG, _L2_TG]
_ALL_INT = [_WEB_INTERNAL, _L1A_INT, _L1B_INT, _L2_INT]


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping web referral integration tests")
    return url


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM referral_transactions WHERE internal_user_id = ANY($1::text[])",
            _ALL_INT,
        )
        await conn.execute(
            "DELETE FROM referral_relationships WHERE referrer_user_id = $1 OR referred_user_id = ANY($2::text[])",
            _WEB_INTERNAL,
            _ALL_INT,
        )
        await conn.execute(
            "DELETE FROM referral_balances WHERE internal_user_id = ANY($1::text[])",
            _ALL_INT,
        )
        await conn.execute(
            "DELETE FROM referral_codes WHERE internal_user_id = $1",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "DELETE FROM billing_events_ledger WHERE internal_user_id = ANY($1::text[])",
            [_L1A_INT, _L1B_INT, _L2_INT],
        )
        await conn.execute(
            "DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])",
            _ALL_TG,
        )


async def _seed(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        # Identities
        for tg, internal in [(_WEB_TG, _WEB_INTERNAL), (_L1A_TG, _L1A_INT), (_L1B_TG, _L1B_INT), (_L2_TG, _L2_INT)]:
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                tg,
                internal,
            )
        # Referral code + balance for the referrer
        await conn.execute(
            "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            _WEB_INTERNAL,
            _REF_CODE,
        )
        await conn.execute(
            """INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, $2)
               ON CONFLICT (internal_user_id) DO UPDATE SET balance_kopecks = $2""",
            _WEB_INTERNAL,
            12500,  # 125.00 ₽
        )
        # L1 relationships (referrer = main)
        for referred in (_L1A_INT, _L1B_INT):
            await conn.execute(
                """INSERT INTO referral_relationships (relationship_id, referred_user_id, referrer_user_id, level)
                   VALUES ($1, $2, $3, 1) ON CONFLICT DO NOTHING""",
                f"rel-l1-{referred}",
                referred,
                _WEB_INTERNAL,
            )
        # L2 relationship
        await conn.execute(
            """INSERT INTO referral_relationships (relationship_id, referred_user_id, referrer_user_id, level)
               VALUES ($1, $2, $3, 2) ON CONFLICT DO NOTHING""",
            "rel-l2",
            _L2_INT,
            _WEB_INTERNAL,
        )
        # One invited (l1a) has paid → accepted billing event
        await conn.execute(
            "INSERT INTO billing_events_ledger "
            "(internal_fact_ref, billing_provider_key, external_event_id, event_type, "
            " event_effective_at, event_received_at, internal_user_id, status, ingestion_correlation_id) "
            "VALUES ($1, 'yookassa', 't-ref-1', 'payment.captured', now(), now(), $2, 'accepted', 't-cor') "
            "ON CONFLICT (internal_fact_ref) DO NOTHING",
            f"fact-{_L1A_INT}",
            _L1A_INT,
        )
        # Two commission credits → total_earned = 8715 + 5000 = 13715 kopecks (137.15 ₽)
        await conn.execute(
            """INSERT INTO referral_transactions
               (transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description)
               VALUES ($1, $2, 8715, 'referral_credit', $3, '1m', 'L1 commission')""",
            "tx-1",
            _WEB_INTERNAL,
            _L1A_INT,
        )
        await conn.execute(
            """INSERT INTO referral_transactions
               (transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description)
               VALUES ($1, $2, 5000, 'referral_credit', $3, '7d', 'L1 commission')""",
            "tx-2",
            _WEB_INTERNAL,
            _L1B_INT,
        )


def _make_authed_client(monkeypatch, pool):
    monkeypatch.setenv("JWT_SECRET", "test-secret-referral-web")
    monkeypatch.setenv("BOT_USERNAME", "bravadavpn_bot")
    from starlette.testclient import TestClient

    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=pool)
    client = TestClient(app)
    token = _issue_jwt({"telegram_user_id": _WEB_TG, "internal_user_id": _WEB_INTERNAL, "email": _TEST_EMAIL})
    csrf = generate_csrf_token()
    client.cookies.set("session", token, domain="testserver")
    client.cookies.set("csrf_token", csrf, domain="testserver")
    return client


def test_referral_dashboard_stats_and_conversion(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed(pool)
            client, _ = _make_authed_client(monkeypatch, pool)
            resp = client.get("/api/v1/user/referral")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["ok"] is True

            referral = body["referral"]
            assert referral["code"] == _REF_CODE
            assert referral["web_link"].endswith(f"/?ref={_REF_CODE}")
            assert referral["telegram_link"] == f"https://t.me/bravadavpn_bot?start=ref_{_REF_CODE}"
            assert referral["balance_rubles"] == 125.0

            stats = referral["stats"]
            assert stats["level1"] == {"invited": 2, "paid": 1}
            assert stats["level2"] == {"invited": 1, "paid": 0}
            assert stats["total_earned_rubles"] == 137.15

            txs = referral["recent_transactions"]
            assert len(txs) == 2
            assert {t["type"] for t in txs} == {"referral_credit"}
            assert {t["amount_rubles"] for t in txs} == {87.15, 50.0}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_referral_dashboard_requires_auth(pg_url, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-referral-web")

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            from starlette.testclient import TestClient

            from app.web_api.app import build_web_api_app

            client = TestClient(build_web_api_app(pool=pool))  # без куки session
            resp = client.get("/api/v1/user/referral")
            assert resp.status_code == 401
            assert resp.json()["error"] == "unauthorized"
        finally:
            await pool.close()

    asyncio.run(main())
