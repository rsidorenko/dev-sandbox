"""Интеграционные тесты веб-эндпоинтов промокода (Фаза 4) — GET/apply/clear.

Opt-in via DATABASE_URL: гоняет РЕАЛЬНЫЕ обработчики против PostgreSQL (migrations +
PostgresPromo*Repository), валидирует re-валидацию fail-closed, аудиторию, TTL, CSRF и
auth. Скидка считается через тот же apply_promo_discount, что и платежи — сверка
preview с реальной ценой. JWT — через _issue_jwt (заголовок Authorization: Bearer),
CSRF — double-submit cookie.

Тесты — асинхронные, через httpx.AsyncClient + ASGITransport на едином event-loop:
общий пул и ASGI-приложение живут на одном loop (pytest-asyncio auto). Раньше
использовался sync Starlette TestClient поверх asyncio.run(main()) — пул создавался
на одном loop, а запрос гнался на другом (anyio portal) → asyncpg «Future attached
to a different loop»; плюс httpx>=0.28 не отправляет cookie с domain="testserver".
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.domain.promo import (
    PROMO_APPLIED_TTL_HOURS,
    PromoAudience,
    PromoCode,
    PromoDiscountKind,
)
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_promo import PostgresPromoCodeRepository
from app.web_api.auth import _issue_jwt
from app.web_api.middleware import generate_csrf_token

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

_WEB_TG = -9_000_000_200
_WEB_INTERNAL = f"web_promo_test_{abs(_WEB_TG)}"
_TEST_EMAIL = "promo-web-test@example.com"

_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
_PAST = datetime(2000, 1, 1, tzinfo=UTC)


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping web promocode integration tests")
    return url


def _promo(
    *,
    code: str = "WEBWELCOME",
    audience: PromoAudience = PromoAudience.NEW_USERS,
    kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    value: int = 50,
    max_redemptions: int | None = None,
    max_redemptions_per_user: int | None = None,
    redemptions_count: int = 0,
    is_active: bool = True,
    expires_at: datetime | None = None,
) -> PromoCode:
    now = datetime.now(UTC)
    return PromoCode(
        code=code,
        audience=audience,
        discount_kind=kind,
        discount_value=value,
        max_redemptions=max_redemptions,
        max_redemptions_per_user=max_redemptions_per_user,
        redemptions_count=redemptions_count,
        expires_at=expires_at,
        is_active=is_active,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="test",
    )


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM promo_applied WHERE internal_user_id = $1", _WEB_INTERNAL)
        await conn.execute("DELETE FROM promo_redemptions WHERE internal_user_id = $1", _WEB_INTERNAL)
        # Платёжный факт «существующего» юзера тоже чистим — иначе ON CONFLICT DO NOTHING
        # оставляет его между прогонами и NEW_USERS-промокоды получают wrong_audience.
        await conn.execute(
            "DELETE FROM billing_events_ledger WHERE internal_user_id = $1",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "DELETE FROM promo_codes WHERE code = ANY($1::text[])",
            [
                "WEBWELCOME",
                "WEBFIXED",
                "WEBEXPIRED",
                "WEBEXHAUST",
                "WEBINACTIVE",
                "WEBEXIST",
                "WEBNEW",
                "WEBALL",
                "WEBONCE",
            ],
        )
        await conn.execute(
            "DELETE FROM user_identities WHERE telegram_user_id = $1",
            _WEB_TG,
        )


async def _seed_identity(pool: asyncpg.Pool, *, existing: bool = False) -> None:
    await pool.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        _WEB_TG,
        _WEB_INTERNAL,
    )
    if existing:
        # «Существующий» = когда-либо платил (billing_events_ledger status='accepted').
        # Раньше симулировалось trial_used=True, но триал больше не делает юзера «не новым».
        await pool.execute(
            "INSERT INTO billing_events_ledger "
            "(internal_fact_ref, billing_provider_key, external_event_id, event_type, "
            " event_effective_at, event_received_at, internal_user_id, status, ingestion_correlation_id) "
            "VALUES ($1, 'yookassa', 't-ext-1', 'payment.captured', now(), now(), $2, 'accepted', 't-cor') "
            "ON CONFLICT (internal_fact_ref) DO NOTHING",
            f"fact-{_WEB_INTERNAL}",
            _WEB_INTERNAL,
        )


def _make_authed_client(monkeypatch, pool):
    monkeypatch.setenv("JWT_SECRET", "test-secret-promocode-web")
    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=pool)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    token = _issue_jwt({"telegram_user_id": _WEB_TG, "internal_user_id": _WEB_INTERNAL, "email": _TEST_EMAIL})
    client.headers["Authorization"] = f"Bearer {token}"
    csrf = generate_csrf_token()
    client.cookies.set("csrf_token", csrf)
    return client, csrf


async def test_get_returns_null_when_no_promo(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        client, _ = _make_authed_client(monkeypatch, pool)
        resp = await client.get("/api/v1/user/promocode")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "applied": None}
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_then_get_shows_descriptor_and_ttl(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo(value=50))
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "webwelcome"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200, resp.text
        applied = resp.json()["applied"]
        assert applied["code"] == "WEBWELCOME"
        assert applied["discount_kind"] == "percent"
        assert applied["discount_label"] == "−50%"
        assert applied["valid"] is True
        assert 23 <= applied["remaining_hours"] <= PROMO_APPLIED_TTL_HOURS

        # GET видит тот же закреплённый промокод.
        got = (await client.get("/api/v1/user/promocode")).json()
        assert got["applied"]["code"] == "WEBWELCOME"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_with_plan_returns_preview(pg_url, monkeypatch):
    """plan_id+device_count → preview: 50% от 1m (249) = 124.5 → half-up 125."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo(value=50))
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBWELCOME", "plan_id": "1m", "device_count": 5},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200, resp.text
        preview = resp.json()["preview"]
        assert preview["base_rubles"] == 249
        assert preview["discounted_rubles"] == 125
        assert preview["discount_kopecks"] == (249 - 125) * 100
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_unknown_code_404(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "NOPE"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_invalid_format_400(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "AB"},  # слишком короткий
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_format"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_expired_409(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo(code="WEBEXPIRED", expires_at=_PAST))
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBEXPIRED"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "expired"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_exhausted_409(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(
            _promo(code="WEBEXHAUST", max_redemptions=1, redemptions_count=1)
        )
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBEXHAUST"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "exhausted"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_once_code_after_redemption_409_and_get_invalid(pg_url, monkeypatch):
    """Once-режим (max_redemptions_per_user=1): после погашения повторный apply → 409
    already_used; GET с «висящим» закреплением (сбой clear) отдаёт дескриптор с valid=false
    (fail-closed, как expired/exhausted)."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo(code="WEBONCE", max_redemptions_per_user=1))
        # Юзер уже погашал этот код (журнал), а «пин» остался висеть.
        await pool.execute(
            "INSERT INTO promo_redemptions "
            "(redemption_id, code, internal_user_id, idempotency_key, "
            " base_rubles, discounted_rubles, discount_kopecks, payment_ref) "
            "VALUES ('r-once-1', 'WEBONCE', $1, 'idem-once-1', 249, 125, 12400, 'pay-1')",
            _WEB_INTERNAL,
        )
        await pool.execute(
            "INSERT INTO promo_applied (internal_user_id, code, applied_at, expires_at) "
            "VALUES ($1, 'WEBONCE', now(), now() + interval '12 hours') "
            "ON CONFLICT (internal_user_id) DO NOTHING",
            _WEB_INTERNAL,
        )
        client, csrf = _make_authed_client(monkeypatch, pool)

        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "webonce"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_used"

        got = (await client.get("/api/v1/user/promocode")).json()
        assert got["applied"]["code"] == "WEBONCE"
        assert got["applied"]["valid"] is False
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_inactive_409_ineligible(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo(code="WEBINACTIVE", is_active=False))
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBINACTIVE"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "ineligible"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_wrong_audience_existing_promo_for_new_user_409(pg_url, monkeypatch):
    """EXISTING_USERS промокод + новый юзер → wrong_audience."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool, existing=False)
        await PostgresPromoCodeRepository(pool).create(_promo(code="WEBEXIST", audience=PromoAudience.EXISTING_USERS))
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBEXIST"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_audience"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_wrong_audience_new_promo_for_existing_user_409(pg_url, monkeypatch):
    """NEW_USERS промокод + существующий юзер (платил) → wrong_audience."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool, existing=True)
        await PostgresPromoCodeRepository(pool).create(_promo(code="WEBNEW", audience=PromoAudience.NEW_USERS))
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBNEW"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_audience"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_all_users_promo_eligible_for_both_new_and_existing(pg_url, monkeypatch):
    """ALL_USERS промокод подходит и новому, и «не новому» пользователю (200, valid)."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await PostgresPromoCodeRepository(pool).create(_promo(code="WEBALL", audience=PromoAudience.ALL_USERS))

        # 1) Новый пользователь (никогда не платил) — ALL_USERS подходит.
        await _seed_identity(pool, existing=False)
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBALL"},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"]["code"] == "WEBALL"
        assert resp.json()["applied"]["valid"] is True
        await client.aclose()

        # 2) Тот же аккаунт становится «не новым» (появился принятый платёж).
        # Сбрасываем закрепление и симулируем платный факт → ALL_USERS всё равно подходит.
        await pool.execute("DELETE FROM promo_applied WHERE internal_user_id = $1", _WEB_INTERNAL)
        await pool.execute("DELETE FROM promo_redemptions WHERE internal_user_id = $1", _WEB_INTERNAL)
        await _seed_identity(pool, existing=True)
        client2, csrf2 = _make_authed_client(monkeypatch, pool)
        resp2 = await client2.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBALL"},
            headers={"x-csrf-token": csrf2},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["applied"]["code"] == "WEBALL"
        assert resp2.json()["applied"]["valid"] is True
        await client2.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_clear_then_get_null(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo())
        client, csrf = _make_authed_client(monkeypatch, pool)
        apply = await client.post(
            "/api/v1/user/promocode/apply",
            json={"code": "WEBWELCOME"},
            headers={"x-csrf-token": csrf},
        )
        assert apply.status_code == 200
        cleared = await client.post(
            "/api/v1/user/promocode/clear",
            headers={"x-csrf-token": csrf},
        )
        assert cleared.status_code == 200
        assert cleared.json()["applied"] is None
        assert (await client.get("/api/v1/user/promocode")).json()["applied"] is None
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_get_null_when_applied_record_expired(pg_url, monkeypatch):
    """Истёкшая запись promo_applied (TTL прошёл) → GET отдаёт null (fail-closed)."""

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo())
        # Закрепляем код с expires_at в прошлом (имитация прошествия 24ч).
        from app.persistence.postgres_promo import PostgresPromoAppliedRepository

        await PostgresPromoAppliedRepository(pool).upsert(_WEB_INTERNAL, "WEBWELCOME", _PAST)
        client, _ = _make_authed_client(monkeypatch, pool)
        assert (await client.get("/api/v1/user/promocode")).json()["applied"] is None
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_apply_requires_csrf(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        await PostgresPromoCodeRepository(pool).create(_promo())
        client, _ = _make_authed_client(monkeypatch, pool)
        # Нет заголовка x-csrf-token → 403 (кука есть, заголовка нет).
        resp = await client.post("/api/v1/user/promocode/apply", json={"code": "WEBWELCOME"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "csrf_validation_failed"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_get_requires_auth(pg_url, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-promocode-web")

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        from app.web_api.app import build_web_api_app

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        )  # без Authorization
        resp = await client.get("/api/v1/user/promocode")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"
        await client.aclose()
    finally:
        await pool.close()
