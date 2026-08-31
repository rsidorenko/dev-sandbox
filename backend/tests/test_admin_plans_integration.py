"""Интеграционные тесты админ-эндпоинтов тарифов и runtime-цен (PostgreSQL).

Opt-in через DATABASE_URL: реальные обработчики против БД с миграциями.
Покрывает:
- CRUD цен тарифов (PATCH/DELETE, валидации 400/404, CSRF 403, аноним 401);
- жизненный цикл глобальной скидки (включение/выключение, сохранение kind/value
  при деактивации, инвалидные значения 400);
- публичный каталог /api/v1/plans отражает оверрайды и скидку;
- чекаут по runtime-цене: metadata base_amount_kopecks + best-of(промо, акция)
  с promo_code ТОЛЬКО при победе промокода;
- регресс гонки «смена цены между созданием платежа и вебхуком»: гейт вебхука
  сверяет сумму по metadata, а не по пересчёту из кода.

Тесты асинхронные: httpx.AsyncClient + ASGITransport на едином event-loop
(pytest-asyncio auto) — общий пул и ASGI-приложение живут на одном loop.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from app.issuance.vless_provider import StubVlessProvider
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app
from app.web_api.auth import _issue_jwt
from app.web_api.middleware import generate_csrf_token

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Уникальные тестовые идентификаторы (далеко от реальных значений).
_TG = 9_000_777_001
_INTERNAL = f"u{_TG}"
_PROMO_CODE = "TESTPRICING30"


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping admin plans integration tests")
    return url


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


async def _reset_pricing_state(pool: asyncpg.Pool) -> None:
    """Сброс глобального состояния цен: оверрайды прочь, скидка выключена.

    Обязательно в finally — иначе тесты в той же БД увидят чужие цены."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM plan_price_overrides")
        await conn.execute("UPDATE pricing_global_discount SET is_active = FALSE")


async def _cleanup_user(pool: asyncpg.Pool, internal_user_id: str, telegram_user_id: int) -> None:
    """Best-effort очистка строк тестового пользователя по всем таблицам."""
    tables = (
        "billing_events_ledger",
        "billing_subscription_apply_records",
        "billing_subscription_apply_audit_events",
        "subscription_snapshots",
        "issuance_state",
        "notification_state",
        "notification_log",
        "referral_transactions",
        "referral_balances",
        "referral_codes",
        "referral_relationships",
        "user_identities",
        "promo_applied",
    )
    async with pool.acquire() as conn:
        for table in tables:
            try:
                await conn.execute(f"DELETE FROM {table} WHERE internal_user_id = $1", internal_user_id)
            except asyncpg.PostgresError:
                pass  # нет колонки/таблицы в этой схеме — пропускаем
        await conn.execute("DELETE FROM promo_codes WHERE code = $1", _PROMO_CODE)
        await conn.execute("DELETE FROM promo_redemptions WHERE code = $1", _PROMO_CODE)


async def _login_client(pool) -> tuple[httpx.AsyncClient, dict]:
    """Login через реальный эндпоинт → клиент с cookie-сессией + csrf-заголовок."""
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test")
    r = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    return client, {"x-csrf-token": csrf}


def _authed_web_client(monkeypatch, pool) -> tuple[httpx.AsyncClient, dict]:
    """Клиент веб-пользователя (session JWT + csrf) поверх того же пула.

    JWT_SECRET НЕ трогаем: admin_env уже выставил свой, а админ-сессия
    (выданная _login_client ДО этого вызова) валидируется тем же секретом."""
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test")
    token = _issue_jwt({"telegram_user_id": _TG, "internal_user_id": _INTERNAL})
    csrf = generate_csrf_token()
    # ВАЖНО: без domain= — httpx>=0.28 не отправляет вручную поставленные cookie с доменом.
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    return client, {"x-csrf-token": csrf}


def _fake_yookassa(monkeypatch) -> AsyncMock:
    """create_payment без внешнего HTTP: возвращает уникальный payment_id."""
    fake_client = MagicMock()

    async def _create(*args, **kwargs):
        return SimpleNamespace(payment_id=f"test-{uuid.uuid4().hex[:12]}", confirmation_url="https://test/confirm")

    fake_client.create_payment = AsyncMock(side_effect=_create)
    monkeypatch.setattr("app.yookassa.client.YooKassaClient.from_env", MagicMock(return_value=fake_client))
    monkeypatch.setenv("YOOKASSA_ENABLED", "1")
    return fake_client.create_payment


async def test_admin_plans_crud_validations_and_public_catalog(pg_url, admin_env):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _reset_pricing_state(pool)
        client, h = await _login_client(pool)

        # исходное состояние: 7 планов, без оверрайдов, скидка выключена
        r = await client.get("/api/v1/admin/plans")
        assert r.status_code == 200, r.text
        plans = {p["plan_id"]: p for p in r.json()["plans"]}
        assert len(plans) == 7
        assert plans["1m"]["default_price_rubles"] == 249
        assert plans["1m"]["price_rubles"] == 249
        assert plans["1m"]["is_overridden"] is False
        assert r.json()["global_discount"]["is_active"] is False

        # PATCH: установка цены
        r = await client.patch("/api/v1/admin/plans/1m", json={"price_rubles": 299}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["price_rubles"] == 299
        r = await client.get("/api/v1/admin/plans")
        plans = {p["plan_id"]: p for p in r.json()["plans"]}
        assert plans["1m"]["is_overridden"] is True and plans["1m"]["price_rubles"] == 299

        # публичный каталог (без auth) отражает оверрайд + кэш-заголовок
        anon = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        )
        r = await anon.get("/api/v1/plans")
        assert r.status_code == 200, r.text
        assert r.headers.get("cache-control") == "public, max-age=30"
        pub = {p["plan_id"]: p for p in r.json()["plans"]}
        assert pub["1m"]["base_rubles"] == 299
        assert pub["365d"]["base_rubles"] == 2199  # остальные — код-дефолты
        assert r.json()["global_discount"] is None
        await anon.aclose()

        # валидации: неизвестный план / мусорная цена / вне диапазона
        assert (
            await client.patch("/api/v1/admin/plans/custom:5", json={"price_rubles": 100}, headers=h)
        ).status_code == 404
        for bad in (0, -1, 100_001, "300", None):
            r = await client.patch("/api/v1/admin/plans/1m", json={"price_rubles": bad}, headers=h)
            assert r.status_code == 400, bad

        # CSRF: без заголовка — 403; аноним — 401
        assert (await client.patch("/api/v1/admin/plans/1m", json={"price_rubles": 300})).status_code == 403
        anon = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_web_api_app(pool=pool)), base_url="http://test"
        )
        assert (await anon.get("/api/v1/admin/plans")).status_code == 401
        await anon.aclose()

        # DELETE: сброс к дефолту; повторный сброс / сброс не-оверрайднутого — 404
        r = await client.delete("/api/v1/admin/plans/1m", headers=h)
        assert r.status_code == 200 and r.json()["price_rubles"] == 249  # DELETE отдаёт цену плоско
        assert (await client.delete("/api/v1/admin/plans/1m", headers=h)).status_code == 404
        assert (await client.delete("/api/v1/admin/plans/1d", headers=h)).status_code == 404
        await client.aclose()
    finally:
        await _reset_pricing_state(pool)
        await pool.close()


async def test_global_discount_lifecycle_and_preserved_params(pg_url, admin_env):
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _reset_pricing_state(pool)
        client, h = await _login_client(pool)

        # включение: 20% с публичной подписью
        r = await client.put(
            "/api/v1/admin/plans/global-discount",
            json={"is_active": True, "discount_kind": "percent", "discount_value": 20, "public_label": "Осенняя акция"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        gd = r.json()["global_discount"]
        assert gd["is_active"] is True and gd["discount_kind"] == "percent" and gd["discount_value"] == 20
        assert gd["public_label"] == "Осенняя акция"

        # публичный каталог отдаёт дескриптор скидки
        r = await client.get("/api/v1/plans")
        pub_gd = r.json()["global_discount"]
        assert pub_gd == {
            "discount_kind": "percent",
            "discount_value": 20,
            "label": "−20%",
            "public_label": "Осенняя акция",
        }

        # инвалидные значения — 400
        for bad in (
            {"is_active": True, "discount_kind": "percent", "discount_value": 150},
            {"is_active": True, "discount_kind": "fixed_rubles", "discount_value": 0},
            {"is_active": True, "discount_kind": "fixed_rubles"},  # без значения
            {"is_active": True, "discount_kind": "bogus", "discount_value": 10},
            {"is_active": "yes"},  # не bool
        ):
            r = await client.put("/api/v1/admin/plans/global-discount", json=bad, headers=h)
            assert r.status_code == 400, bad

        # деактивация без kind/value — 200, параметры СОХРАНЯЮТСЯ в БД
        r = await client.put("/api/v1/admin/plans/global-discount", json={"is_active": False}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["global_discount"]["is_active"] is False
        r = await client.get("/api/v1/plans")
        assert r.json()["global_discount"] is None  # выключена — каталог не отдаёт

        # повторное включение без параметров поднимает сохранённые
        r = await client.put("/api/v1/admin/plans/global-discount", json={"is_active": True}, headers=h)
        assert r.status_code == 200, r.text
        gd = r.json()["global_discount"]
        assert gd["is_active"] is True and gd["discount_kind"] == "percent" and gd["discount_value"] == 20

        # аудит действий записывается
        async with pool.acquire() as conn:
            actions = await conn.fetchval(
                "SELECT count(*) FROM admin_pricing_actions WHERE action = 'set_global_discount'"
            )
            assert actions >= 3
        await client.aclose()
    finally:
        await _reset_pricing_state(pool)
        await pool.close()


async def test_checkout_runtime_price_and_bestof_metadata(pg_url, admin_env, monkeypatch):
    """Чекаут по runtime-цене: metadata base_amount_kopecks + best-of(промо, акция);
    promo_code в metadata — только когда выиграл промокод."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _reset_pricing_state(pool)
        await _cleanup_user(pool, _INTERNAL, _TG)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)"
                " ON CONFLICT (telegram_user_id) DO NOTHING",
                _TG,
                _INTERNAL,
            )

        admin, h = await _login_client(pool)
        web, wh = _authed_web_client(monkeypatch, pool)
        create_payment = _fake_yookassa(monkeypatch)

        # оверрайд 1m=300 (круглые числа — точные проценты) + акция 10%
        r = await admin.patch("/api/v1/admin/plans/1m", json={"price_rubles": 300}, headers=h)
        assert r.status_code == 200, r.text
        r = await admin.put(
            "/api/v1/admin/plans/global-discount",
            json={"is_active": True, "discount_kind": "percent", "discount_value": 10},
            headers=h,
        )
        assert r.status_code == 200, r.text

        # 1) только акция: 300 − 10% = 270
        r = await web.post("/api/v1/payment/create", json={"plan_id": "1m", "device_count": 5}, headers=wh)
        assert r.status_code == 200, r.text
        assert r.json()["amount_rubles"] == 270 and r.json()["amount_kopecks"] == 270_00
        meta = create_payment.call_args.kwargs["metadata"]
        assert meta["base_amount_kopecks"] == "30000"
        assert meta["discount_kopecks"] == "3000"
        assert "promo_code" not in meta  # промокода нет — код не должен гаситься

        # 2) применённый промокод 30% бьёт акцию 10%: 300 − 30% = 210
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO promo_codes (code, audience, discount_kind, discount_value)"
                " VALUES ($1, 'all_users', 'percent', 30) ON CONFLICT (code) DO NOTHING",
                _PROMO_CODE,
            )
            await conn.execute(
                "INSERT INTO promo_applied (internal_user_id, code, expires_at) VALUES ($1, $2, $3)"
                " ON CONFLICT (internal_user_id) DO UPDATE SET code = $2, expires_at = $3",
                _INTERNAL,
                _PROMO_CODE,
                datetime(2099, 1, 1, tzinfo=UTC),
            )
        r = await web.post("/api/v1/payment/create", json={"plan_id": "1m", "device_count": 5}, headers=wh)
        assert r.status_code == 200, r.text
        assert r.json()["amount_rubles"] == 210
        meta = create_payment.call_args.kwargs["metadata"]
        assert meta["base_amount_kopecks"] == "30000"
        assert meta["promo_code"] == _PROMO_CODE and meta["discount_kopecks"] == "9000"

        # 3) акция 40% бьёт промокод 30%: 300 − 40% = 180, промокод НЕ расходуется
        r = await admin.put(
            "/api/v1/admin/plans/global-discount",
            json={"is_active": True, "discount_kind": "percent", "discount_value": 40},
            headers=h,
        )
        assert r.status_code == 200, r.text
        r = await web.post("/api/v1/payment/create", json={"plan_id": "1m", "device_count": 5}, headers=wh)
        assert r.status_code == 200, r.text
        assert r.json()["amount_rubles"] == 180
        meta = create_payment.call_args.kwargs["metadata"]
        assert meta["discount_kopecks"] == "12000"
        assert "promo_code" not in meta  # победила акция — код не гасится вебхуком
        async with pool.acquire() as conn:
            redeemed = await conn.fetchval(
                "SELECT count(*) FROM promo_redemptions WHERE code = $1 AND internal_user_id = $2",
                _PROMO_CODE,
                _INTERNAL,
            )
            assert redeemed == 0  # и на этапе создания не погашен
        await admin.aclose()
        await web.aclose()
    finally:
        await _reset_pricing_state(pool)
        await _cleanup_user(pool, _INTERNAL, _TG)
        await pool.close()


async def test_webhook_amount_gate_race_after_price_change(pg_url, admin_env, monkeypatch):
    """Регресс гонки: цена изменена МЕЖДУ созданием платежа и вебхуком — гейт
    сверяется по base_amount_kopecks из metadata (±1 коп), а не по пересчёту."""
    from app.runtime.payment_webhook_helpers import fulfill_subscription_payment

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
        await _reset_pricing_state(pool)
        await _cleanup_user(pool, _INTERNAL, _TG)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)"
                " ON CONFLICT (telegram_user_id) DO NOTHING",
                _TG,
                _INTERNAL,
            )

        admin, h = await _login_client(pool)
        web, wh = _authed_web_client(monkeypatch, pool)
        create_payment = _fake_yookassa(monkeypatch)

        # платёж создан по цене 300
        assert (await admin.patch("/api/v1/admin/plans/1m", json={"price_rubles": 300}, headers=h)).status_code == 200
        r = await web.post("/api/v1/payment/create", json={"plan_id": "1m", "device_count": 5}, headers=wh)
        assert r.status_code == 200 and r.json()["amount_rubles"] == 300, r.text
        charge_meta = {
            k: v
            for k, v in create_payment.call_args.kwargs["metadata"].items()
            if k.startswith(("base_amount", "discount"))
        }
        assert charge_meta["base_amount_kopecks"] == "30000"

        # …админ меняет цену до прихода вебхука
        assert (await admin.patch("/api/v1/admin/plans/1m", json={"price_rubles": 249}, headers=h)).status_code == 200

        def _meta() -> dict[str, str]:
            return {
                "plan_id": "1m",
                "device_count": "5",
                "telegram_user_id": str(_TG),
                "internal_user_id": _INTERNAL,
                **charge_meta,
            }

        # (1) оплачено 300 по metadata-базе 300 → гейт ПРОХОДИТ несмотря на новую цену 249
        resp = await fulfill_subscription_payment(
            pool=pool,
            vless_provider=StubVlessProvider(),
            activation_telegram_notifier=None,
            telemetry=None,
            provider_key="yookassa",
            external_event_id=f"evt-{uuid.uuid4().hex[:12]}",
            external_payment_id=f"pay-{uuid.uuid4().hex[:12]}",
            metadata=_meta(),
            paid_amount_kopecks=300_00,
            paid_at=datetime.now(UTC),
        )
        assert resp.status_code == 200, resp.body

        # подписка реально выдана
        async with pool.acquire() as conn:
            snap = await conn.fetchrow(
                "SELECT state_label, plan_id FROM subscription_snapshots WHERE internal_user_id = $1", _INTERNAL
            )
            assert snap is not None and snap["state_label"] == "active" and snap["plan_id"] == "1m"

        # (2) та же база, но оплачено 249 (по НОВОЙ цене) → 409: metadata authoritative
        resp = await fulfill_subscription_payment(
            pool=pool,
            vless_provider=StubVlessProvider(),
            activation_telegram_notifier=None,
            telemetry=None,
            provider_key="yookassa",
            external_event_id=f"evt-{uuid.uuid4().hex[:12]}",
            external_payment_id=f"pay-{uuid.uuid4().hex[:12]}",
            metadata=_meta(),
            paid_amount_kopecks=249_00,
            paid_at=datetime.now(UTC),
        )
        assert resp.status_code == 409

        # (3) legacy-платёж БЕЗ base_amount_kopecks → пересчёт из кода (249) проходит
        legacy_meta = {
            "plan_id": "1m",
            "device_count": "5",
            "telegram_user_id": str(_TG),
            "internal_user_id": _INTERNAL,
        }
        resp = await fulfill_subscription_payment(
            pool=pool,
            vless_provider=StubVlessProvider(),
            activation_telegram_notifier=None,
            telemetry=None,
            provider_key="yookassa",
            external_event_id=f"evt-{uuid.uuid4().hex[:12]}",
            external_payment_id=f"pay-{uuid.uuid4().hex[:12]}",
            metadata=legacy_meta,
            paid_amount_kopecks=249_00,
            paid_at=datetime.now(UTC),
        )
        assert resp.status_code == 200, resp.body
        await admin.aclose()
        await web.aclose()
    finally:
        await _reset_pricing_state(pool)
        await _cleanup_user(pool, _INTERNAL, _TG)
        await pool.close()
