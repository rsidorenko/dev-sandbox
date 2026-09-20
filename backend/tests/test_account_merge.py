"""Интеграционные тесты привязки ссылки-подписки (web-аккаунт → Telegram identity).

Opt-in via DATABASE_URL: реальный merge против PostgreSQL — полный перенос
таблиц, правило двух активных подписок (TG выигрывает), отказ при tg>0,
unique-index-безопасная перепривязка токена, удаление web-строки иlookup по
токену после merge (то, чем живёт ``/sub/<token>``).

Каркас — исторический email-merge-тест (``4c048f6^``), адресация web-аккаунта
переведена с email на ``subscription_token`` (link-auth, 2026-09-20).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.persistence.account_merge import (
    BindLinkError,
    bind_subscription_link,
    find_link_account,
)
from app.persistence.postgres_migrations import apply_postgres_migrations

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Тестовые id: web ниже MINVALUE web_user_id_seq, реальные tg — больше любого живого.
_WEB_TG = -9_000_000_061
_REAL_TG = 8_888_888_800_061
_OTHER_TG = 8_888_888_800_062  # второй TG-юзер (для отказа already_linked)
_INVITER_TG = 8_888_888_800_063

_WEB_INTERNAL = "web_bindtest0001"
_TG_INTERNAL = f"u{_REAL_TG}"
_OTHER_INTERNAL = f"u{_OTHER_TG}"
_INVITER_INTERNAL = f"u{_INVITER_TG}"

_WEB_TOKEN = "bindWebToken000000000001"  # токен web-аккаунта (привязываемый)
_TG_OLD_TOKEN = "bindTgOldToken00000001"  # прежний токен TG-юзера
_OTHER_TOKEN = "bindOtherTgToken000001"  # токен второго TG-юзера

_TEST_EMAIL = "bind-link-test@example.com"


def _pg_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


@pytest.fixture
def pg_url() -> str:
    url = _pg_url()
    if url is None:
        pytest.skip("DATABASE_URL not set; skipping account_merge integration tests")
    return url


async def _cleanup(pool: asyncpg.Pool) -> None:
    ids = [_WEB_INTERNAL, _TG_INTERNAL, _OTHER_INTERNAL, _INVITER_INTERNAL]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM analytics_internal_audit WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM experiment_assignments WHERE internal_user_id = ANY($1::text[])", ids)
        # user_emails держит FK на user_identities (без CASCADE) — чистим ДО identity.
        await conn.execute(
            "DELETE FROM user_emails WHERE telegram_user_id = ANY($1::bigint[])",
            [_WEB_TG, _REAL_TG, _OTHER_TG],
        )
        await conn.execute("DELETE FROM user_emails WHERE email = $1", _TEST_EMAIL)
        await conn.execute("DELETE FROM web_notifications WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM promo_applied WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM promo_redemptions WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute(
            "DELETE FROM referral_relationships"
            " WHERE referred_user_id = ANY($1::text[])"
            " OR referrer_user_id = ANY($1::text[])"
            " OR referrer_of_referrer_user_id = ANY($1::text[])",
            ids,
        )
        await conn.execute("DELETE FROM referral_transactions WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM referral_codes WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM referral_balances WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM issuance_state WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute(
            "DELETE FROM billing_subscription_apply_records WHERE internal_user_id = ANY($1::text[])",
            ids,
        )
        await conn.execute(
            "DELETE FROM billing_subscription_apply_audit_events WHERE internal_user_id = ANY($1::text[])",
            ids,
        )
        await conn.execute("DELETE FROM billing_events_ledger WHERE internal_fact_ref LIKE 'fact-bindtest%'")
        await conn.execute("DELETE FROM subscription_snapshots WHERE internal_user_id = ANY($1::text[])", ids)
        await conn.execute(
            "DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])",
            [_WEB_TG, _REAL_TG, _OTHER_TG, _INVITER_TG],
        )


async def _seed_web_account(
    pool: asyncpg.Pool,
    *,
    snapshot_state: str | None = "active",
    snapshot_plan: str = "1m",
) -> None:
    """Web-only аккаунт: identity с токеном + полный набор данных."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
            " VALUES ($1, $2, $3) ON CONFLICT (telegram_user_id) DO NOTHING",
            _WEB_TG,
            _WEB_INTERNAL,
            _WEB_TOKEN,
        )
        if snapshot_state is not None:
            await conn.execute(
                "INSERT INTO subscription_snapshots"
                " (internal_user_id, state_label, plan_id, active_until_utc, device_count)"
                " VALUES ($1, $2, $3, now() + interval '30 days', 5)"
                " ON CONFLICT (internal_user_id) DO NOTHING",
                _WEB_INTERNAL,
                snapshot_state,
                snapshot_plan,
            )
        await conn.execute(
            "INSERT INTO issuance_state"
            " (internal_user_id, issue_idempotency_key, issuance_state, provider_issuance_ref)"
            " VALUES ($1, 'bindtest-web-key', 'issued', 'xui-bindtest-web')"
            " ON CONFLICT DO NOTHING",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO billing_events_ledger"
            " (internal_fact_ref, billing_provider_key, external_event_id, event_type,"
            "  event_effective_at, event_received_at, internal_user_id, status, ingestion_correlation_id)"
            " VALUES ('fact-bindtest-web', 'yookassa', 'bindtest-ext-1', 'payment.captured',"
            "  now(), now(), $1, 'accepted', 'bindtest-cor')"
            " ON CONFLICT (internal_fact_ref) DO NOTHING",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO billing_subscription_apply_records"
            " (internal_fact_ref, internal_user_id, apply_outcome)"
            " VALUES ('fact-bindtest-web', $1, 'active_applied')"
            " ON CONFLICT (internal_fact_ref) DO NOTHING",
            _WEB_INTERNAL,
        )
        # Рефералка: код + баланс + транзакция + пригласивший (L1).
        await conn.execute(
            "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, 'BINDWEB1')"
            " ON CONFLICT (internal_user_id) DO NOTHING",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, 50000)"
            " ON CONFLICT (internal_user_id) DO NOTHING",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO referral_transactions"
            " (transaction_id, internal_user_id, amount_kopecks, transaction_type, description)"
            " VALUES ('bindtest-tx-1', $1, 50000, 'referral_credit', 'bindtest-web-credit')"
            " ON CONFLICT DO NOTHING",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id)"
            " VALUES ($1, $2) ON CONFLICT (telegram_user_id) DO NOTHING",
            _INVITER_TG,
            _INVITER_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO referral_relationships"
            " (relationship_id, referred_user_id, referrer_user_id, level)"
            " VALUES ('bindtest-rel-1', $1, $2, 1) ON CONFLICT DO NOTHING",
            _WEB_INTERNAL,
            _INVITER_INTERNAL,
        )
        # Эксперимент: web видел вариант раньше.
        await conn.execute(
            "INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant, assigned_at)"
            " VALUES ($1, 'bindtest_exp', 'control', now() - interval '5 days')"
            " ON CONFLICT DO NOTHING",
            _WEB_INTERNAL,
        )
        # Промо: закрепление + погашение (once-код).
        await conn.execute(
            "INSERT INTO promo_applied (internal_user_id, code, applied_at, expires_at)"
            " VALUES ($1, 'BINDPROMO', now(), now() + interval '12 hours')"
            " ON CONFLICT (internal_user_id) DO NOTHING",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO promo_redemptions"
            " (redemption_id, code, internal_user_id, idempotency_key,"
            "  base_rubles, discounted_rubles, discount_kopecks, payment_ref)"
            " VALUES ('bindtest-red-1', 'BINDPROMO', $1, 'bindtest-idem-1', 249, 125, 12400, 'pay-1')"
            " ON CONFLICT DO NOTHING",
            _WEB_INTERNAL,
        )
        # Колокольчик + почта (канал доставки первой ссылки).
        await conn.execute(
            "INSERT INTO web_notifications (internal_user_id, kind, title, body)"
            " VALUES ($1, 'broadcast', 'Тест', '<b>привет</b>')",
            _WEB_INTERNAL,
        )
        await conn.execute(
            "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at) VALUES ($1, $2, TRUE, now())",
            _WEB_TG,
            _TEST_EMAIL,
        )


async def _seed_telegram_user(
    pool: asyncpg.Pool,
    *,
    snapshot_state: str | None = None,
    snapshot_plan: str = "3m",
    token: str | None = _TG_OLD_TOKEN,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
            " VALUES ($1, $2, $3) ON CONFLICT (telegram_user_id) DO NOTHING",
            _REAL_TG,
            _TG_INTERNAL,
            token,
        )
        if snapshot_state is not None:
            await conn.execute(
                "INSERT INTO subscription_snapshots"
                " (internal_user_id, state_label, plan_id, active_until_utc, device_count)"
                " VALUES ($1, $2, $3, now() + interval '60 days', 3)"
                " ON CONFLICT (internal_user_id) DO NOTHING",
                _TG_INTERNAL,
                snapshot_state,
                snapshot_plan,
            )


def test_bind_link_full_transfer(pg_url: str) -> None:
    """Полный перенос: подписка/issuance/billing/рефералка/промо/эксперимент —
    на TG identity; токен перепривязан; web-строка, почта и колокольчик удалены."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_web_account(pool)
            await _seed_telegram_user(pool)  # без своей подписки, со старым токеном

            result = await bind_subscription_link(pool, _REAL_TG, _WEB_TOKEN)
            assert result.adopted_subscription is True
            assert result.discarded_web_subscription is False
            assert result.balance_kopecks_added == 500_00

            async with pool.acquire() as conn:
                # Подписка web адоптирована (у TG своей не было).
                snap = await conn.fetchrow(
                    "SELECT state_label, plan_id, device_count FROM subscription_snapshots WHERE internal_user_id = $1",
                    _TG_INTERNAL,
                )
                assert snap is not None and snap["state_label"] == "active"
                assert snap["plan_id"] == "1m" and snap["device_count"] == 5

                # Issuance + billing переехали.
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM issuance_state WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 1
                )
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM billing_events_ledger WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 1
                )
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM billing_subscription_apply_records WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 1
                )

                # Рефералка: код/баланс/транзакция/связь — на TG.
                assert (
                    await conn.fetchval(
                        "SELECT referral_code FROM referral_codes WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == "BINDWEB1"
                )
                assert (
                    await conn.fetchval(
                        "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 500_00
                )
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM referral_transactions WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 1
                )
                rel = await conn.fetchrow(
                    "SELECT referrer_user_id, level FROM referral_relationships WHERE referred_user_id = $1",
                    _TG_INTERNAL,
                )
                assert rel is not None and rel["referrer_user_id"] == _INVITER_INTERNAL

                # Эксперимент следует за человеком (ранний вариант).
                exp = await conn.fetchrow(
                    "SELECT variant FROM experiment_assignments WHERE internal_user_id = $1",
                    _TG_INTERNAL,
                )
                assert exp is not None and exp["variant"] == "control"

                # Промо: закрепление и история погашений перенесены.
                assert (
                    await conn.fetchval("SELECT code FROM promo_applied WHERE internal_user_id = $1", _TG_INTERNAL)
                    == "BINDPROMO"
                )
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM promo_redemptions WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 1
                )

                # Колокольчик и почта web-аккаунта удалены.
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM web_notifications WHERE internal_user_id = $1",
                        _WEB_INTERNAL,
                    )
                    == 0
                )
                assert await conn.fetchval("SELECT COUNT(*) FROM user_emails WHERE email = $1", _TEST_EMAIL) == 0

                # Web-identity удалена; токен перепривязан на TG-строку
                # (partial unique index не нарушен — порядок обнуление→назначение).
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM user_identities WHERE internal_user_id = $1",
                        _WEB_INTERNAL,
                    )
                    == 0
                )
                tg_row = await conn.fetchrow(
                    "SELECT subscription_token FROM user_identities WHERE internal_user_id = $1",
                    _TG_INTERNAL,
                )
                assert tg_row is not None and tg_row["subscription_token"] == _WEB_TOKEN

                # Lookup по токену (то, чем живёт /sub/<token>) → TG identity.
                owner = await conn.fetchrow(
                    "SELECT telegram_user_id, internal_user_id FROM user_identities WHERE subscription_token = $1",
                    _WEB_TOKEN,
                )
                assert owner is not None
                assert owner["internal_user_id"] == _TG_INTERNAL
                assert int(owner["telegram_user_id"]) == _REAL_TG
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_bind_link_two_active_subscriptions_telegram_wins(pg_url: str) -> None:
    """Обе подписки активны → TG-подписка сохраняется, web-подписка и её
    issuance/billing отбрасываются; баланс всё равно суммируется."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_web_account(pool)  # активная 1m
            await _seed_telegram_user(pool, snapshot_state="active", snapshot_plan="3m")

            result = await bind_subscription_link(pool, _REAL_TG, _WEB_TOKEN)
            assert result.adopted_subscription is False
            assert result.discarded_web_subscription is True

            async with pool.acquire() as conn:
                snap = await conn.fetchrow(
                    "SELECT plan_id FROM subscription_snapshots WHERE internal_user_id = $1",
                    _TG_INTERNAL,
                )
                assert snap is not None and snap["plan_id"] == "3m"
                # Web-подписка удалена; issuance НЕ перенесён (остался на web-id —
                # подписка отброшена, ключи web больше никогда не выдаются).
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM subscription_snapshots WHERE internal_user_id = $1",
                        _WEB_INTERNAL,
                    )
                    == 0
                )
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM issuance_state WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 0
                )
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM issuance_state WHERE internal_user_id = $1",
                        _WEB_INTERNAL,
                    )
                    == 1
                )
                # Баланс суммируется несмотря на конфликт подписок.
                assert (
                    await conn.fetchval(
                        "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == 500_00
                )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_bind_link_refuses_token_owned_by_telegram_account(pg_url: str) -> None:
    """Токен принадлежит TG-аккаунту (tg>0) — отказ already_linked, данных не тронуты."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_web_account(pool)
            await _seed_telegram_user(pool)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id, subscription_token)"
                    " VALUES ($1, $2, $3) ON CONFLICT (telegram_user_id) DO NOTHING",
                    _OTHER_TG,
                    _OTHER_INTERNAL,
                    _OTHER_TOKEN,
                )

            # Чужой TG-токен.
            with pytest.raises(BindLinkError) as exc_info:
                await bind_subscription_link(pool, _REAL_TG, _OTHER_TOKEN)
            assert exc_info.value.code == "already_linked"

            # Собственный текущий токен — тот же отказ.
            with pytest.raises(BindLinkError) as exc_info:
                await bind_subscription_link(pool, _REAL_TG, _TG_OLD_TOKEN)
            assert exc_info.value.code == "already_linked"

            # Ничего не изменилось.
            async with pool.acquire() as conn:
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM user_identities WHERE internal_user_id = $1",
                        _WEB_INTERNAL,
                    )
                    == 1
                )
                assert (
                    await conn.fetchval(
                        "SELECT subscription_token FROM user_identities WHERE internal_user_id = $1",
                        _TG_INTERNAL,
                    )
                    == _TG_OLD_TOKEN
                )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_bind_link_token_not_found_and_missing_identity(pg_url: str) -> None:
    """Неизвестный токен → token_not_found; без TG-identity → telegram_identity_missing."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_web_account(pool)
            await _seed_telegram_user(pool)

            with pytest.raises(BindLinkError) as exc_info:
                await bind_subscription_link(pool, _REAL_TG, "noSuchTokenAnywhere123")
            assert exc_info.value.code == "token_not_found"

            # TG-юзер без identity (в боте практически невозможно — но отказ явный).
            with pytest.raises(BindLinkError) as exc_info:
                await bind_subscription_link(pool, _OTHER_TG, _WEB_TOKEN)
            assert exc_info.value.code == "telegram_identity_missing"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_find_link_account_preview(pg_url: str) -> None:
    """Превью по токену: поля web-аккаунта для экрана подтверждения;
    tg>0-токен виден как занятый, неизвестный — None."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            await _seed_web_account(pool)
            await _seed_telegram_user(pool)

            preview = await find_link_account(pool, _WEB_TOKEN)
            assert preview is not None
            assert preview.internal_user_id == _WEB_INTERNAL
            assert preview.telegram_user_id == _WEB_TG  # < 0 → можно привязывать
            assert preview.has_active_subscription is True
            assert preview.plan_id == "1m"
            assert preview.active_until_utc is not None
            assert preview.active_until_utc > datetime.now(UTC) + timedelta(days=29)
            assert preview.device_count == 5
            assert preview.balance_kopecks == 500_00

            # Токен TG-аккаунта — существует, но знак >0 (занят).
            tg_preview = await find_link_account(pool, _TG_OLD_TOKEN)
            assert tg_preview is not None and tg_preview.telegram_user_id == _REAL_TG

            assert await find_link_account(pool, "unknownToken12345678") is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


# ─── Юнит-тесты фасада бота (без БД) ────────────────────────────────────


def test_extract_sub_link_token_variants() -> None:
    from app.bot_transport.runtime_facade import _extract_sub_link_token

    # Полная ссылка-подписка.
    assert _extract_sub_link_token("https://bravada-connect.ru/sub/AbC123-_xyz987") == ("AbC123-_xyz987")
    # Ссылка входа из письма (/auth/<token>).
    assert _extract_sub_link_token("https://bravada-connect.ru/auth/Tok_en-123456") == ("Tok_en-123456")
    # Голый токен.
    assert _extract_sub_link_token("  AbC123-_xyz987  ") == "AbC123-_xyz987"
    # Мусор: слишком коротко / посторонние символы / путь.
    assert _extract_sub_link_token("short") is None
    assert _extract_sub_link_token("подписка пожалуйста") is None
    assert _extract_sub_link_token("https://example.com/other/AbC123-_xyz987") is None
    assert _extract_sub_link_token("abc def ghi jkl mno p") is None


def test_settings_keyboard_has_bind_link_button() -> None:
    from app.bot_transport.storefront_ui import CB_BIND_LINK, settings_keyboard

    for has_sub in (True, False):
        kb = settings_keyboard(has_sub, 5 if has_sub else None)
        buttons = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
        assert CB_BIND_LINK in buttons
