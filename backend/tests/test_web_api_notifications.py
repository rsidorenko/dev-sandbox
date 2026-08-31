"""Интеграционные тесты веб-эндпоинтов уведомлений (list/read/claim) — opt-in по DATABASE_URL.

Гоняет РЕАЛЬНЫЕ обработчики против PostgreSQL (migrations + реальные таблицы):
auth/CSRF, лента+unread+has_more, клейм бонус-дней (atomic claim + grant + trial_used
+ аудит broadcast_claim_web:*), клейм промокода (gate + apply_promo_intent), отказы
(already/expired/no_bonus/чужое уведомление). Каркас — test_web_api_promocode.py
(httpx.AsyncClient + ASGITransport на одном loop, JWT в Bearer, CSRF double-submit).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_promo import PostgresPromoCodeRepository
from app.web_api.auth import _issue_jwt
from app.web_api.middleware import generate_csrf_token

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

_WEB_TG = -9_000_000_300
_WEB_INTERNAL = f"web_notif_test_{abs(_WEB_TG)}"
_OTHER_INTERNAL = "web_notif_test_other"
_TEST_EMAIL = "notifications-web-test@example.com"

_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)
_PAST = datetime(2000, 1, 1, tzinfo=UTC)


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping web notifications integration tests")
    return url


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM web_notifications WHERE internal_user_id = ANY($1::text[])",
            [_WEB_INTERNAL, _OTHER_INTERNAL],
        )
        await conn.execute(
            "DELETE FROM broadcast_jobs WHERE created_by = $1", "notif-int-test"
        )  # claims каскадятся по FK
        await conn.execute("DELETE FROM promo_applied WHERE internal_user_id = $1", _WEB_INTERNAL)
        await conn.execute("DELETE FROM promo_redemptions WHERE internal_user_id = $1", _WEB_INTERNAL)
        await conn.execute("DELETE FROM promo_codes WHERE code = 'NOTIFPROMO'")
        await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = $1", _WEB_INTERNAL)
        await conn.execute("DELETE FROM admin_subscription_actions WHERE actor LIKE 'broadcast_claim_web:%'")
        await conn.execute("DELETE FROM user_identities WHERE telegram_user_id = $1", _WEB_TG)


async def _seed_identity(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        _WEB_TG,
        _WEB_INTERNAL,
    )


async def _seed_job(pool: asyncpg.Pool, job_id: str) -> None:
    await pool.execute(
        "INSERT INTO broadcast_jobs (job_id, created_by, status, message_text) "
        "VALUES ($1, 'notif-int-test', 'completed', 'test') ON CONFLICT DO NOTHING",
        job_id,
    )


async def _seed_days_claim(pool: asyncpg.Pool, job_id: str, *, days: int = 5, expires_at: datetime = _FUTURE) -> None:
    await pool.execute(
        "INSERT INTO broadcast_bonus_claims (job_id, telegram_user_id, days, expires_at) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        job_id,
        _WEB_TG,
        days,
        expires_at,
    )


async def _seed_promo_claim(
    pool: asyncpg.Pool, job_id: str, *, code: str = "NOTIFPROMO", expires_at: datetime = _FUTURE
) -> None:
    await pool.execute(
        "INSERT INTO broadcast_promo_claims (job_id, telegram_user_id, code, expires_at) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        job_id,
        _WEB_TG,
        code,
        expires_at,
    )


async def _seed_notification(
    pool: asyncpg.Pool,
    *,
    internal_user_id: str = _WEB_INTERNAL,
    kind: str = "broadcast",
    title: str = "Тест",
    body: str | None = "<b>Привет!</b>",
    payload: dict | None = None,
    read: bool = False,
) -> int:
    row = await pool.fetchrow(
        "INSERT INTO web_notifications (internal_user_id, kind, title, body, payload, read_at) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6) RETURNING id",
        internal_user_id,
        kind,
        title,
        body,
        json.dumps(payload) if payload is not None else None,
        datetime.now(UTC) if read else None,
    )
    assert row is not None
    return int(row["id"])


def _make_authed_client(monkeypatch, pool):
    monkeypatch.setenv("JWT_SECRET", "test-secret-notifications-web")
    from app.web_api.app import build_web_api_app

    app = build_web_api_app(pool=pool)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    token = _issue_jwt({"telegram_user_id": _WEB_TG, "internal_user_id": _WEB_INTERNAL, "email": _TEST_EMAIL})
    client.headers["Authorization"] = f"Bearer {token}"
    csrf = generate_csrf_token()
    client.cookies.set("csrf_token", csrf)
    return client, csrf


def _bonus_payload(job_id: str, bonus: dict) -> dict:
    return {"job_id": job_id, "bonus": bonus}


async def test_list_requires_auth(pg_url, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-notifications-web")
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        from app.web_api.app import build_web_api_app

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        )
        resp = await client.get("/api/v1/user/notifications")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"
        await client.aclose()
    finally:
        await pool.close()


async def test_list_read_all_flow(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        first = await _seed_notification(pool, title="Первое")
        await _seed_notification(pool, title="Второе", read=True)
        client, csrf = _make_authed_client(monkeypatch, pool)

        listed = await client.get("/api/v1/user/notifications")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["unread_count"] == 1
        assert body["has_more"] is False
        assert [item["id"] for item in body["items"]] == [first + 1, first]  # DESC

        # Мутация без CSRF-заголовка → 403
        denied = await client.post("/api/v1/user/notifications/read", json={"all": True})
        assert denied.status_code == 403

        marked = await client.post(
            "/api/v1/user/notifications/read",
            json={"ids": [first]},
            headers={"x-csrf-token": csrf},
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["updated"] == 1
        assert (await client.get("/api/v1/user/notifications")).json()["unread_count"] == 0
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_list_paginates_has_more(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        for _ in range(3):
            await _seed_notification(pool)
        client, _ = _make_authed_client(monkeypatch, pool)
        body = (await client.get("/api/v1/user/notifications?limit=2")).json()
        assert len(body["items"]) == 2
        assert body["has_more"] is True
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_claim_days_full_flow(pg_url, monkeypatch):
    """Успешный клейм дней: claims→claimed, снапшот продлён, trial_used, аудит, повтор 409."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        job_id = str(uuid.uuid4())
        await _seed_job(pool, job_id)
        await _seed_days_claim(pool, job_id, days=5)
        notification_id = await _seed_notification(
            pool, payload=_bonus_payload(job_id, {"type": "days", "days": 5, "label": "+5 дней"})
        )
        client, csrf = _make_authed_client(monkeypatch, pool)

        claimed = await client.post(
            f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf}
        )
        assert claimed.status_code == 200, claimed.text
        body = claimed.json()
        assert body["status"] == "ok"
        assert body["days"] == 5

        status = await pool.fetchval("SELECT status FROM broadcast_bonus_claims WHERE job_id=$1", job_id)
        assert status == "claimed"
        until = await pool.fetchval(
            "SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1",
            _WEB_INTERNAL,
        )
        assert until is not None and until > datetime.now(UTC) + timedelta(days=4)
        trial_used = await pool.fetchval(
            "SELECT trial_used FROM user_identities WHERE internal_user_id=$1", _WEB_INTERNAL
        )
        assert trial_used is True
        audit = await pool.fetchval(
            "SELECT COUNT(*) FROM admin_subscription_actions WHERE actor = $1",
            f"broadcast_claim_web:{job_id}",
        )
        assert int(audit or 0) == 1

        again = await client.post(f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf})
        assert again.status_code == 409
        assert again.json()["error"] == "already_claimed"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_claim_days_expired_410(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        job_id = str(uuid.uuid4())
        await _seed_job(pool, job_id)
        await _seed_days_claim(pool, job_id, expires_at=_PAST)
        notification_id = await _seed_notification(
            pool, payload=_bonus_payload(job_id, {"type": "days", "days": 5, "label": "+5"})
        )
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf})
        assert resp.status_code == 410
        assert resp.json()["error"] == "expired"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_claim_without_bonus_400_and_foreign_404(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        plain_id = await _seed_notification(pool, payload={"notification_type": "x"})
        foreign_id = await _seed_notification(
            pool,
            internal_user_id=_OTHER_INTERNAL,
            payload=_bonus_payload(str(uuid.uuid4()), {"type": "days", "days": 3, "label": "+3"}),
        )
        client, csrf = _make_authed_client(monkeypatch, pool)
        no_bonus = await client.post(f"/api/v1/user/notifications/{plain_id}/claim", headers={"x-csrf-token": csrf})
        assert no_bonus.status_code == 400
        assert no_bonus.json()["error"] == "no_bonus"
        foreign = await client.post(f"/api/v1/user/notifications/{foreign_id}/claim", headers={"x-csrf-token": csrf})
        assert foreign.status_code == 404
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_claim_promo_full_flow(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        job_id = str(uuid.uuid4())
        await _seed_job(pool, job_id)
        await _seed_promo_claim(pool, job_id)
        notification_id = await _seed_notification(
            pool, payload=_bonus_payload(job_id, {"type": "promo", "code": "NOTIFPROMO", "label": "Промик"})
        )
        await PostgresPromoCodeRepository(pool).create(_make_promo("NOTIFPROMO"))
        client, csrf = _make_authed_client(monkeypatch, pool)

        claimed = await client.post(
            f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf}
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json() == {"ok": True, "status": "ok", "code": "NOTIFPROMO"}
        applied_code = await pool.fetchval("SELECT code FROM promo_applied WHERE internal_user_id=$1", _WEB_INTERNAL)
        assert applied_code == "NOTIFPROMO"

        # Повторный клик — идемпотентное «уже получено» (даже после истечения claim-окна).
        again = await client.post(f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf})
        assert again.status_code == 200
        assert again.json()["status"] == "already_claimed"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_claim_promo_expired_410(pg_url, monkeypatch):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        job_id = str(uuid.uuid4())
        await _seed_job(pool, job_id)
        await _seed_promo_claim(pool, job_id, expires_at=_PAST)
        notification_id = await _seed_notification(
            pool, payload=_bonus_payload(job_id, {"type": "promo", "code": "NOTIFPROMO", "label": "Промик"})
        )
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf})
        assert resp.status_code == 410
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_claim_promo_already_used_409(pg_url, monkeypatch):
    """Once-код, уже погашенный пользователем до рассылки → already_claimed."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        job_id = str(uuid.uuid4())
        await _seed_job(pool, job_id)
        await _seed_promo_claim(pool, job_id)
        notification_id = await _seed_notification(
            pool, payload=_bonus_payload(job_id, {"type": "promo", "code": "NOTIFPROMO", "label": "Промик"})
        )
        await PostgresPromoCodeRepository(pool).create(_make_promo("NOTIFPROMO", max_redemptions_per_user=1))
        await pool.execute(
            "INSERT INTO promo_redemptions "
            "(redemption_id, code, internal_user_id, idempotency_key, "
            " base_rubles, discounted_rubles, discount_kopecks, payment_ref) "
            "VALUES ('r-notif-1', 'NOTIFPROMO', $1, 'idem-notif-1', 249, 125, 12400, 'pay-1')",
            _WEB_INTERNAL,
        )
        client, csrf = _make_authed_client(monkeypatch, pool)
        resp = await client.post(f"/api/v1/user/notifications/{notification_id}/claim", headers={"x-csrf-token": csrf})
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_claimed"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


async def test_list_enriches_claim_status(pg_url, monkeypatch):
    """GET отдаёт claim_status по claims/promo_applied — кнопка рендерится до клика."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _cleanup(pool)
        await _seed_identity(pool)
        job_id = str(uuid.uuid4())
        await _seed_job(pool, job_id)
        await _seed_days_claim(pool, job_id, days=5)
        expired_job = str(uuid.uuid4())
        await _seed_job(pool, expired_job)
        await _seed_days_claim(pool, expired_job, expires_at=_PAST)
        plain = await _seed_notification(pool, title="Без бонуса")
        eligible = await _seed_notification(
            pool, payload=_bonus_payload(job_id, {"type": "days", "days": 5, "label": "+5"})
        )
        expired = await _seed_notification(
            pool, payload=_bonus_payload(expired_job, {"type": "days", "days": 5, "label": "+5"})
        )
        client, csrf = _make_authed_client(monkeypatch, pool)

        items = {item["id"]: item for item in (await client.get("/api/v1/user/notifications")).json()["items"]}
        assert items[plain].get("claim_status") is None
        assert items[eligible]["claim_status"] == "eligible"
        assert items[expired]["claim_status"] == "expired"

        # После клейма статус в ленте становится claimed.
        claim_resp = await client.post(f"/api/v1/user/notifications/{eligible}/claim", headers={"x-csrf-token": csrf})
        assert claim_resp.status_code == 200, claim_resp.text
        items = {item["id"]: item for item in (await client.get("/api/v1/user/notifications")).json()["items"]}
        assert items[eligible]["claim_status"] == "claimed"
        await client.aclose()
    finally:
        await _cleanup(pool)
        await pool.close()


def _make_promo(code: str, *, max_redemptions_per_user: int | None = None):
    from app.domain.promo import PromoAudience, PromoCode, PromoDiscountKind

    now = datetime.now(UTC)
    return PromoCode(
        code=code,
        audience=PromoAudience.ALL_USERS,
        discount_kind=PromoDiscountKind.PERCENT,
        discount_value=10,
        max_redemptions=None,
        max_redemptions_per_user=max_redemptions_per_user,
        redemptions_count=0,
        expires_at=None,
        is_active=True,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="test",
    )
