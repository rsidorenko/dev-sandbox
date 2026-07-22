"""Интеграционные тесты веб-эндпоинтов промокода (Фаза 4) — GET/apply/clear.

Opt-in via DATABASE_URL: гоняет РЕАЛЬНЫЕ обработчики против PostgreSQL (мigrations +
PostgresPromo*Repository), валидирует re-валидацию fail-closed, аудиторию, TTL, CSRF и
auth. Скидка считается через тот же apply_promo_discount, что и платежи — сверка
preview с реальной ценой. JWT — через _issue_jwt, CSRF — double-submit cookie
(эталон test_web_api_payment_balance).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
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
        await conn.execute(
            "DELETE FROM promo_codes WHERE code = ANY($1::text[])",
            ["WEBWELCOME", "WEBFIXED", "WEBEXPIRED", "WEBEXHAUST", "WEBINACTIVE", "WEBEXIST", "WEBNEW"],
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
        # trial_used=True → compute_is_new_user даёт False («существующий» юзер).
        await pool.execute(
            "UPDATE user_identities SET trial_used = TRUE WHERE internal_user_id = $1",
            _WEB_INTERNAL,
        )


def _make_authed_client(monkeypatch, pool):
    monkeypatch.setenv("JWT_SECRET", "test-secret-promocode-web")
    from starlette.testclient import TestClient

    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=pool)
    client = TestClient(app)
    token = _issue_jwt({"telegram_user_id": _WEB_TG, "internal_user_id": _WEB_INTERNAL, "email": _TEST_EMAIL})
    csrf = generate_csrf_token()
    client.cookies.set("session", token, domain="testserver")
    client.cookies.set("csrf_token", csrf, domain="testserver")
    return client, csrf


def test_get_returns_null_when_no_promo(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            client, _ = _make_authed_client(monkeypatch, pool)
            resp = client.get("/api/v1/user/promocode")
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"ok": True, "applied": None}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_then_get_shows_descriptor_and_ttl(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(_promo(value=50))
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
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
            got = client.get("/api/v1/user/promocode").json()
            assert got["applied"]["code"] == "WEBWELCOME"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_with_plan_returns_preview(pg_url, monkeypatch):
    """plan_id+device_count → preview: 50% от 1m (249) = 124.5 → half-up 125."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(_promo(value=50))
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBWELCOME", "plan_id": "1m", "device_count": 5},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 200, resp.text
            preview = resp.json()["preview"]
            assert preview["base_rubles"] == 249
            assert preview["discounted_rubles"] == 125
            assert preview["discount_kopecks"] == (249 - 125) * 100
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_unknown_code_404(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "NOPE"},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 404
            assert resp.json()["error"] == "not_found"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_invalid_format_400(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "AB"},  # слишком короткий
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 400
            assert resp.json()["error"] == "invalid_format"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_expired_409(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(_promo(code="WEBEXPIRED", expires_at=_PAST))
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBEXPIRED"},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 409
            assert resp.json()["error"] == "expired"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_exhausted_409(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(
                _promo(code="WEBEXHAUST", max_redemptions=1, redemptions_count=1)
            )
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBEXHAUST"},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 409
            assert resp.json()["error"] == "exhausted"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_inactive_409_ineligible(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(_promo(code="WEBINACTIVE", is_active=False))
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBINACTIVE"},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 409
            assert resp.json()["error"] == "ineligible"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_wrong_audience_existing_promo_for_new_user_409(pg_url, monkeypatch):
    """EXISTING_USERS промокод + новый юзер → wrong_audience."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool, existing=False)
            await PostgresPromoCodeRepository(pool).create(
                _promo(code="WEBEXIST", audience=PromoAudience.EXISTING_USERS)
            )
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBEXIST"},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 409
            assert resp.json()["error"] == "wrong_audience"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_wrong_audience_new_promo_for_existing_user_409(pg_url, monkeypatch):
    """NEW_USERS промокод + существующий юзер (trial_used) → wrong_audience."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool, existing=True)
            await PostgresPromoCodeRepository(pool).create(_promo(code="WEBNEW", audience=PromoAudience.NEW_USERS))
            client, csrf = _make_authed_client(monkeypatch, pool)
            resp = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBNEW"},
                headers={"x-csrf-token": csrf},
            )
            assert resp.status_code == 409
            assert resp.json()["error"] == "wrong_audience"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_clear_then_get_null(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(_promo())
            client, csrf = _make_authed_client(monkeypatch, pool)
            apply = client.post(
                "/api/v1/user/promocode/apply",
                json={"code": "WEBWELCOME"},
                headers={"x-csrf-token": csrf},
            )
            assert apply.status_code == 200
            cleared = client.post(
                "/api/v1/user/promocode/clear",
                headers={"x-csrf-token": csrf},
            )
            assert cleared.status_code == 200
            assert cleared.json()["applied"] is None
            assert client.get("/api/v1/user/promocode").json()["applied"] is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_get_null_when_applied_record_expired(pg_url, monkeypatch):
    """Истёкшая запись promo_applied (TTL прошёл) → GET отдаёт null (fail-closed)."""

    async def main() -> None:
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
            assert client.get("/api/v1/user/promocode").json()["applied"] is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_apply_requires_csrf(pg_url, monkeypatch):
    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_identity(pool)
            await PostgresPromoCodeRepository(pool).create(_promo())
            client, _ = _make_authed_client(monkeypatch, pool)
            # Нет заголовка x-csrf-token → 403 (кука есть, заголовка нет).
            resp = client.post("/api/v1/user/promocode/apply", json={"code": "WEBWELCOME"})
            assert resp.status_code == 403
            assert resp.json()["error"] == "csrf_validation_failed"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_get_requires_auth(pg_url, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-promocode-web")

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            from starlette.testclient import TestClient

            from app.web_api.app import build_web_api_app

            client = TestClient(build_web_api_app(pool=pool))  # без куки session
            resp = client.get("/api/v1/user/promocode")
            assert resp.status_code == 401
            assert resp.json()["error"] == "unauthorized"
        finally:
            await pool.close()

    asyncio.run(main())
