"""Интеграционные тесты рассылки против PostgreSQL (opt-in через DATABASE_URL).

Покрывает:
  * SQL-резолвер аудитории — корректность счёта по категориям, дедуп UNION,
    исключение web-only из TG-канала (telegram_user_id < 0), явные цели (tg-id и email).
  * HTTP-обвязку: auth (401 без куки), CSRF (403 без токена), валидацию send
    (503 без BOT_TOKEN, 400 invalid_html, 400 no_recipients), list/status.
  * Web fan-out реального BroadcastRunner: web_notifications + claims для web-only
    (отрицательный tg-id), линкнутый — одна claim-строка на оба канала, счётчики web_*,
    рассылка «только веб-аудитория» не выходит по раннему return.

Админ/web_api-тесты НЕ входят в postgres-smoke CI — этот файл прогоняется только локально.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.persistence.postgres_migrations import apply_postgres_migrations
from app.web_api.app import build_web_api_app

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Тестовые telegram id (9900000xx) — не пересекаются с прод-данными.
_TG = {
    "active_paid": 990000001,
    "active_trial": 990000002,
    "exp_0_2": 990000003,
    "exp_3_5": 990000004,
    "exp_6_7": 990000005,
    "exp_8_30": 990000006,
    "exp_30plus": 990000007,
    "dormant": 990000008,
    "web_only": -990000009,
}
_ALL_TG = list(_TG.values())


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping broadcast integration tests")
    return url


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_ENABLE", "1")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "s3cr3t-pass")
    monkeypatch.setenv("JWT_SECRET", "integration-jwt-secret")
    monkeypatch.setenv("JWT_ADMIN_TTL_MINUTES", "60")
    monkeypatch.setenv("WEB_API_DEV_INSECURE_COOKIE", "1")


async def _insert_fixtures(pool: asyncpg.Pool) -> None:
    # ВАЖНО (PG18/asyncpg): один и тот же $1 в контекстах bigint и text даёт
    # AmbiguousParameterError, а int в text-конкат — DataError. Поэтому internal_user_id
    # собираем из ОТДЕЛЬНОГО строкового плейсхолдера: ($1, 'u'||$2, …).
    async with pool.acquire() as c:
        # Активная платная подписка.
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
            "VALUES ($1,'u'||$2, TRUE) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["active_paid"],
            str(_TG["active_paid"]),
        )
        await c.execute(
            "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc) "
            "VALUES ('u'||$1, 'active', '1m', now() + interval '30 days') "
            "ON CONFLICT (internal_user_id) DO NOTHING",
            str(_TG["active_paid"]),
        )
        # Активный триал (plan_id IS NULL, trial_expires_at > now).
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
            "VALUES ($1,'u'||$2, TRUE) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["active_trial"],
            str(_TG["active_trial"]),
        )
        await c.execute(
            "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, "
            "trial_started_at, trial_expires_at) "
            "VALUES ('u'||$1, 'active', NULL, now(), now() + interval '2 days') "
            "ON CONFLICT (internal_user_id) DO NOTHING",
            str(_TG["active_trial"]),
        )
        # Диапазоны истечения (state_label не важен — предикат игнорирует его).
        for key, days in (("exp_0_2", 1), ("exp_3_5", 4), ("exp_6_7", 7), ("exp_8_30", 15), ("exp_30plus", 40)):
            tg = _TG[key]
            await c.execute(
                "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
                "VALUES ($1,'u'||$2, TRUE) ON CONFLICT (telegram_user_id) DO NOTHING",
                tg,
                str(tg),
            )
            await c.execute(
                "INSERT INTO subscription_snapshots (internal_user_id, state_label, plan_id, active_until_utc) "
                "VALUES ('u'||$1, 'expired', '1m', now() - ($2 || ' days')::interval) "
                "ON CONFLICT (internal_user_id) DO NOTHING",
                str(tg),
                str(days),
            )
        # Спящий: trial_used=FALSE, без снапшота и без billing.
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, trial_used) "
            "VALUES ($1,'u'||$2, FALSE) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["dormant"],
            str(_TG["dormant"]),
        )
        # Web-only (отрицательный telegram_user_id) — НЕ должен попасть ни в одну категорию.
        await c.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id) "
            "VALUES ($1,'u'||$2) ON CONFLICT (telegram_user_id) DO NOTHING",
            _TG["web_only"],
            str(_TG["web_only"]),
        )
        # Email для теста резолва явной цели по почте.
        await c.execute(
            "INSERT INTO user_emails (telegram_user_id, email, is_verified) "
            "VALUES ($1, 'broadcast-test-1@example.test', TRUE) ON CONFLICT DO NOTHING",
            _TG["active_paid"],
        )
        # Verified email у web-only — попадает ТОЛЬКО в web-аудиторию (TG-канал его не видит).
        await c.execute(
            "INSERT INTO user_emails (telegram_user_id, email, is_verified) "
            "VALUES ($1, 'broadcast-test-2@example.test', TRUE) ON CONFLICT DO NOTHING",
            _TG["web_only"],
        )


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM web_notifications WHERE internal_user_id IN "
            "(SELECT ('u'||tg)::text FROM unnest($1::bigint[]) AS tg)",
            _ALL_TG,
        )
        await c.execute("DELETE FROM user_emails WHERE telegram_user_id = ANY($1::bigint[])", _ALL_TG)
        await c.execute(
            "DELETE FROM subscription_snapshots WHERE internal_user_id IN "
            "(SELECT ('u'||tg)::text FROM unnest($1::bigint[]) AS tg)",
            _ALL_TG,
        )
        await c.execute("DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])", _ALL_TG)
        await c.execute("DELETE FROM broadcast_jobs WHERE created_by = 'admin'")


class _StubClient:
    async def send_text_message(self, **_kw) -> int:
        return 1


class _CountingStubClient:
    """Запоминает chat_id TG-отправок (assert «TG-канал не видел web-only»)."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def send_text_message(self, *, chat_id, **_kw) -> int:
        self.calls.append(int(chat_id))
        return 1


async def _noop_sleep(_s: float = 0.0) -> None:
    return None


def _seed_job_sql(job_id, explicit_ids, text, bonus_json):
    """INSERT джобы в формате send-хендлера (runner читает те же поля)."""
    return (
        "INSERT INTO broadcast_jobs "
        "(job_id, created_by, status, categories, explicit_telegram_ids, message_text, "
        " parse_mode, disable_web_page_preview, total_recipients, buttons, bonus) "
        "VALUES ($1,'admin','queued','{}'::text[],$2,$3,'HTML',FALSE,0,'[]'::jsonb,$4::jsonb)",
        job_id,
        explicit_ids,
        text,
        bonus_json,
    )


def test_broadcast_estimate_and_handler_wiring(pg_url, admin_env):
    # httpx.AsyncClient + ASGITransport на ОДНОМ loop с пулом: TestClient-портал
    # гоняет приложение в отдельном event loop — asyncpg-коннектts уходят в
    # «another operation is in progress» (паттерн test_web_api_notifications.py).
    async def _run():
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            app = build_web_api_app(pool=pool)
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

            r = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
            assert r.status_code == 200, r.text
            csrf = r.json()["csrf_token"]
            h = {"x-csrf-token": csrf}

            async def estimate(categories, targets=None):
                body = {"categories": categories}
                if targets is not None:
                    body["explicit_targets"] = targets
                return await client.post("/api/v1/admin/broadcast/estimate", json=body, headers=h)

            # --- Базовые счета ДО вставки фикстур ---
            cats = [
                "all",
                "active",
                "expired_0_2d",
                "expired_3_5d",
                "expired_6_7d",
                "expired_8_30d",
                "expired_30plus",
                "dormant",
            ]
            baseline = {c: (await estimate([c])).json()["total"] for c in cats}
            baseline_all = baseline["all"]

            await _insert_fixtures(pool)

            # --- Дельты по категориям (каждая фикстура попадает ровно в одну категорию) ---
            expected_delta = {
                "all": 8,  # 9 фикстур, web-only (tg<0) исключён из TG-канала
                "active": 2,
                "expired_0_2d": 1,
                "expired_3_5d": 1,
                "expired_6_7d": 1,
                "expired_8_30d": 1,
                "expired_30plus": 1,
                "dormant": 1,
            }
            for c in cats:
                got = (await estimate([c])).json()["total"]
                assert got == baseline[c] + expected_delta[c], f"category {c}: {baseline[c]}->{got}"

            # --- Дедуп: active ⊆ all → объединение не больше all ---
            r = await estimate(["all", "active"])
            assert r.json()["total"] == baseline_all + 8, "active должен быть подмножеством all (дедуп)"

            # --- Web-only исключён из all (TG-канал), но входит в web_total ---
            r = await estimate(["all"])
            assert r.json()["total"] == baseline_all + 8
            assert r.json()["web_total"] >= 1  # web_only с verified email виден веб-каналу

            # --- Явные цели: tg-id ---
            r = await estimate([], targets=[str(_TG["exp_0_2"]), str(_TG["dormant"])])
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 2

            # --- Явные цели: email (резолв через user_emails) ---
            r = await estimate([], targets=["broadcast-test-1@example.test"])
            assert r.json()["total"] == 1
            assert _TG["active_paid"] in r.json()["explicit_resolved"]

            # --- Неразрешённая цель ---
            r = await estimate([], targets=["no-such-user-xyz@example.test"])
            assert r.json()["total"] == 0
            assert r.json()["explicit_unresolved"] == ["no-such-user-xyz@example.test"]

            # --- invalid_categories ---
            r = await estimate(["bogus"])
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_categories"

            # --- auth: без куки → 401 ---
            anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            try:
                anon_resp = await anon.post("/api/v1/admin/broadcast/estimate", json={"categories": ["all"]})
                assert anon_resp.status_code == 401
            finally:
                await anon.aclose()

            # --- CSRF: send без X-CSRF-Token → 403 (под залогиненной сессией) ---
            r = await client.post("/api/v1/admin/broadcast/send", json={"categories": ["all"], "message_text": "x"})
            assert r.status_code == 403
            assert r.json()["error"] == "csrf_validation_failed"

            # --- send без BOT_TOKEN → 503 telegram_not_configured (client is None) ---
            assert app.state.telegram_raw_client is None
            r = await client.post(
                "/api/v1/admin/broadcast/send",
                json={"categories": [], "explicit_targets": [str(_TG["active_paid"])], "message_text": "<b>hi</b>"},
                headers=h,
            )
            assert r.status_code == 503
            assert r.json()["error"] == "telegram_not_configured"

            # --- инжект фейк-клиента для проверки синхронных валидаций send ---
            app.state.telegram_raw_client = _StubClient()

            r = await client.post(
                "/api/v1/admin/broadcast/send",
                json={"categories": [], "explicit_targets": [str(_TG["active_paid"])], "message_text": "<a>без href</a>"},
                headers=h,
            )
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_html"

            r = await client.post(
                "/api/v1/admin/broadcast/send",
                json={"categories": [], "explicit_targets": ["definitely-absent@example.test"], "message_text": "ok"},
                headers=h,
            )
            assert r.status_code == 400
            assert r.json()["error"] == "no_recipients"

            # --- list/status ---
            r = await client.get("/api/v1/admin/broadcast/jobs?limit=5")
            assert r.status_code == 200
            assert "jobs" in r.json()
            r = await client.get("/api/v1/admin/broadcast/jobs/00000000-0000-0000-0000-000000000000")
            assert r.status_code == 404
            assert r.json()["error"] == "job_not_found"
            await client.aclose()
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_broadcast_web_fanout_full_flow(pg_url, admin_env):
    """Реальный BroadcastRunner + PG: линкнутый (tg>0 + email) получает оба канала,
    web-only (tg<0 + email) — только веб; claims по одному на получателя; счётчики web_*."""

    async def _run():
        import json as _json
        import uuid

        from app.web_api.admin_broadcast import BroadcastRunner

        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _insert_fixtures(pool)
            app = build_web_api_app(pool=pool)
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

            r = await client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "s3cr3t-pass"})
            assert r.status_code == 200, r.text
            h = {"x-csrf-token": r.json()["csrf_token"]}

            # estimate: TG-канал видит только положительный id, web-аудитория — обоих.
            r = await client.post(
                "/api/v1/admin/broadcast/estimate",
                json={
                    "categories": [],
                    "explicit_targets": ["broadcast-test-1@example.test", "broadcast-test-2@example.test"],
                },
                headers=h,
            )
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 1
            assert r.json()["web_total"] == 2
            assert set(r.json()["explicit_resolved"]) == {_TG["active_paid"], _TG["web_only"]}

            # Джоба в формате send-хендлера: явные цели = оба id (web-only отрицательный).
            job_id = uuid.uuid4()
            sql, *args = _seed_job_sql(
                job_id,
                [_TG["active_paid"], _TG["web_only"]],
                "<b>Привет!</b><br>Бонус внутри",
                _json.dumps({"type": "days", "days": 5, "label": "+5 дней", "ttl_days": 14}),
            )
            await pool.execute(sql, *args)

            tg_client = _CountingStubClient()
            runner = BroadcastRunner(pool=pool, client=tg_client, job_id=str(job_id), sleep=_noop_sleep)
            await runner.run()

            # Джоба: TG доставлен 1, веб — 2, статус completed.
            job = await pool.fetchrow(
                "SELECT status, total_recipients, sent_count, web_recipient_count, web_notified_count "
                "FROM broadcast_jobs WHERE job_id=$1",
                job_id,
            )
            assert job["status"] == "completed"
            assert (job["total_recipients"], job["sent_count"]) == (1, 1)
            assert (job["web_recipient_count"], job["web_notified_count"]) == (2, 2)

            # TG-канал не видел web-only.
            assert tg_client.calls == [_TG["active_paid"]]

            # web_notifications: оба внутренних id, kind=broadcast, title=plain, body=HTML,
            # payload несёт job_id + bonus (asyncpg JSONB без кодека → str).
            rows = {
                row["internal_user_id"]: row
                for row in await pool.fetch(
                    "SELECT internal_user_id, kind, title, body, payload FROM web_notifications "
                    "WHERE internal_user_id IN ($1, $2)",
                    f"u{_TG['active_paid']}",
                    f"u{_TG['web_only']}",
                )
            }
            assert set(rows) == {f"u{_TG['active_paid']}", f"u{_TG['web_only']}"}
            for row in rows.values():
                assert row["kind"] == "broadcast"
                assert row["title"] == "Привет! Бонус внутри"
                assert row["body"] == "<b>Привет!</b><br>Бонус внутри"  # как сохранено в джобе
                payload = _json.loads(row["payload"])
                assert payload["job_id"] == str(job_id)
                assert payload["bonus"]["days"] == 5

            # Claims: по ОДНОЙ строке на получателя (линкнутый сидируется один раз — общий PK).
            claims = {
                int(row["telegram_user_id"]): row["status"]
                for row in await pool.fetch(
                    "SELECT telegram_user_id, status FROM broadcast_bonus_claims WHERE job_id=$1",
                    job_id,
                )
            }
            assert claims == {_TG["active_paid"]: "eligible", _TG["web_only"]: "eligible"}
            await client.aclose()
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_broadcast_web_only_audience_no_early_return(pg_url, admin_env):
    """Регресс раннего return: TG-получателей нет, web-аудитория есть → джоба
    completed, веб-уведомление вставлено, claim сидирован (TG-отправок ноль)."""

    async def _run():
        import json as _json
        import uuid

        from app.web_api.admin_broadcast import BroadcastRunner

        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _insert_fixtures(pool)

            job_id = uuid.uuid4()
            sql, *args = _seed_job_sql(
                job_id,
                [_TG["web_only"]],  # только web-only получатель
                "<b>Только веб</b>",
                _json.dumps({"type": "days", "days": 3, "label": "+3"}),
            )
            await pool.execute(sql, *args)

            tg_client = _CountingStubClient()
            runner = BroadcastRunner(pool=pool, client=tg_client, job_id=str(job_id), sleep=_noop_sleep)
            await runner.run()

            job = await pool.fetchrow(
                "SELECT status, total_recipients, sent_count, web_recipient_count, web_notified_count "
                "FROM broadcast_jobs WHERE job_id=$1",
                job_id,
            )
            assert job["status"] == "completed"
            assert (job["total_recipients"], job["sent_count"]) == (0, 0)
            assert (job["web_recipient_count"], job["web_notified_count"]) == (1, 1)
            assert tg_client.calls == []

            notified = await pool.fetchval(
                "SELECT COUNT(*) FROM web_notifications WHERE internal_user_id=$1 AND payload->>'job_id'=$2",
                f"u{_TG['web_only']}",
                str(job_id),
            )
            assert int(notified or 0) == 1

            claim = await pool.fetchval(
                "SELECT status FROM broadcast_bonus_claims WHERE job_id=$1 AND telegram_user_id=$2",
                job_id,
                _TG["web_only"],
            )
            assert claim == "eligible"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
