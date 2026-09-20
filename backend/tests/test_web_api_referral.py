"""Интеграционные тесты GET /api/v1/user/referral — реферальный дашборд.

Opt-in via DATABASE_URL: гоняет реальный обработчик против PostgreSQL (migrations).
Проверяет структуру ответа: код, ссылки, баланс, разбивку L1/L2 (invited/paid),
всего заработано и историю транзакций. Кейс чистого web-юзера («друга»).

Тесты — асинхронные, через httpx.AsyncClient + ASGITransport на едином event-loop:
общий пул и ASGI-приложение живут на одном loop (pytest-asyncio auto). Раньше
использовался sync Starlette TestClient поверх asyncio.run(main()) — пул
создавался на одном loop, а запрос гнался на другом (anyio portal) →
asyncpg «Future attached to a different loop». Тесты фактически не запускались
(CI postgres-smoke их не вызывает, full-regression делает unset DATABASE_URL).
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Главный (реферер) аккаунт
_WEB_TG = -9_000_000_400
_WEB_INTERNAL = f"web_ref_test_{abs(_WEB_TG)}"
_SUB_TOKEN = "refWebMainSubToken0000000001"
_REF_CODE = "TESTREF1"

# Приглашённые L1: l1a оплатил, l1b — нет
_L1A_TG, _L1A_INT = -9_000_000_401, "web_ref_l1a"
_L1B_TG, _L1B_INT = -9_000_000_402, "web_ref_l1b"
# Приглашённый L2: не оплатил
_L2_TG, _L2_INT = -9_000_000_403, "web_ref_l2"

# Web-only user — кейс «друга»: зарегистрирован через web (синтетический
# отрицательный telegram_user_id, internal_user_id "web_…"), БЕЗ referral-кода,
# отношений, баланса и транзакций. Эндпоинт должен вернуть 200 с авто-кодом и
# нулями, а не 404/500.
_WEB_ONLY_TG = -9_000_000_500
_WEB_ONLY_INT = "web_ref_only_friend"
_WEB_ONLY_SUB_TOKEN = "refWebOnlySubToken000000001"

_ALL_TG = [_WEB_TG, _L1A_TG, _L1B_TG, _L2_TG, _WEB_ONLY_TG]
_ALL_INT = [_WEB_INTERNAL, _L1A_INT, _L1B_INT, _L2_INT, _WEB_ONLY_INT]


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
            "DELETE FROM referral_codes WHERE internal_user_id = ANY($1::text[])",
            _ALL_INT,
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
        # Identities (у главного — subscription-токен: им аутентифицируется веб-клиент)
        await conn.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
            " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            _WEB_TG,
            _WEB_INTERNAL,
            _SUB_TOKEN,
        )
        for tg, internal in [(_L1A_TG, _L1A_INT), (_L1B_TG, _L1B_INT), (_L2_TG, _L2_INT)]:
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
        # Two commission credits → total_earned = 8715 + 5000 = 13715 kopecks (137.15 ₽).
        # Descriptions must differ — uq_referral_tx_user_desc UNIQUE(internal_user_id, description).
        await conn.execute(
            """INSERT INTO referral_transactions
               (transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description)
               VALUES ($1, $2, 8715, 'referral_credit', $3, '1m', 'L1 commission · 1m')""",
            "tx-1",
            _WEB_INTERNAL,
            _L1A_INT,
        )
        await conn.execute(
            """INSERT INTO referral_transactions
               (transaction_id, internal_user_id, amount_kopecks, transaction_type, related_user_id, related_plan_id, description)
               VALUES ($1, $2, 5000, 'referral_credit', $3, '7d', 'L1 commission · 7d')""",
            "tx-2",
            _WEB_INTERNAL,
            _L1B_INT,
        )


async def _authed_client(
    monkeypatch,
    pool,
    *,
    subscription_token,
    with_session: bool = True,
) -> httpx.AsyncClient:
    """httpx client wired to the ASGI app on the SAME loop as `pool` — no cross-loop
    asyncpg errors. Auth via Authorization: Bearer <subscription_token> — единственный
    способ входа на сайте (кук/JWT больше нет)."""
    monkeypatch.setenv("BOT_USERNAME", "bravadavpn_bot")
    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=pool)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    if with_session:
        client.headers["Authorization"] = f"Bearer {subscription_token}"
    return client


async def test_referral_dashboard_stats_and_conversion(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed(pool)
        client = await _authed_client(monkeypatch, pool, subscription_token=_SUB_TOKEN)
        resp = await client.get("/api/v1/user/referral")
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
        # Security: the cabinet must NEVER expose the raw internal description /
        # idempotency_key (which historically leaked payer ids + webhook data). Only a
        # derived clean label + level/type/plan/amount/created_at are returned.
        for t in txs:
            assert "description" not in t
            assert "idempotency_key" not in t
            assert t["label"] == "Реферальное начисление"
            assert t["level"] in (1, 2)
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_referral_dashboard_requires_auth(pg_url, monkeypatch):
    client = await _authed_client(
        monkeypatch,
        # pool must exist for build_web_api_app; auth is rejected before any DB use.
        pool=await asyncpg.create_pool(pg_url, min_size=1, max_size=2),
        subscription_token="unused",
        with_session=False,
    )
    try:
        resp = await client.get("/api/v1/user/referral")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"
    finally:
        await client.aclose()


async def test_referral_dashboard_web_only_user_empty(pg_url, monkeypatch):
    """Кейс «друга»: чистый web-only юзер (отрицательный telegram_user_id, internal_user_id
    'web_…'), БЕЗ referral-кода/отношений/баланса/транзакций. Эндпоинт резолвит
    identity по subscription-токену и возвращает 200 с авто-кодом и нулями."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        # Сеет ТОЛЬКО identity (как выдача ссылки новому web-юзеру) — без referral-данных.
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                _WEB_ONLY_TG,
                _WEB_ONLY_INT,
                _WEB_ONLY_SUB_TOKEN,
            )
        client = await _authed_client(monkeypatch, pool, subscription_token=_WEB_ONLY_SUB_TOKEN)
        resp = await client.get("/api/v1/user/referral")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        referral = body["referral"]
        # Код авто-создан (непустой) — дашборд всегда имеет ссылку.
        assert referral["code"]
        assert referral["web_link"].endswith(f"/?ref={referral['code']}")
        assert referral["telegram_link"].endswith(f"?start=ref_{referral['code']}")
        assert referral["balance_rubles"] == 0.0
        stats = referral["stats"]
        assert stats["level1"] == {"invited": 0, "paid": 0}
        assert stats["level2"] == {"invited": 0, "paid": 0}
        assert stats["total_earned_rubles"] == 0.0
        assert referral["recent_transactions"] == []
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()
