"""Tests for account merge logic (web-only → Telegram identity)."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg
import pytest

from app.persistence.account_merge import (
    _telegram_internal_id,
    _web_internal_id,
    merge_web_account_if_needed,
)
from app.persistence.postgres_migrations import apply_postgres_migrations


class TestInternalIdDerivation:
    """Pure function tests for ID derivation — no DB needed."""

    def test_web_id_uses_sha256_prefix(self) -> None:
        email = "user@example.com"
        expected = f"web_{hashlib.sha256(email.encode()).hexdigest()[:12]}"
        assert _web_internal_id(email) == expected

    def test_web_id_deterministic(self) -> None:
        assert _web_internal_id("a@b.com") == _web_internal_id("a@b.com")

    def test_web_id_different_for_different_emails(self) -> None:
        assert _web_internal_id("a@b.com") != _web_internal_id("c@d.com")

    def test_telegram_id_format(self) -> None:
        assert _telegram_internal_id(123456) == "u123456"

    def test_telegram_id_different_from_web(self) -> None:
        email = "user@example.com"
        assert _telegram_internal_id(123456) != _web_internal_id(email)


# ---------------------------------------------------------------------------
# Integration tests (opt-in via DATABASE_URL) — exercise the REAL merge query
# against PostgreSQL, including the partial unique index idx_user_emails_email_verified.
#
# Regression guard: web accounts are created with a NEGATIVE telegram_user_id
# (web_user_id_seq). Before the fix, account_merge queried ``telegram_user_id = 0``
# and never matched them, so web→Telegram merges silently did nothing. These tests
# pin the ``<= 0`` matching condition and the data-migration path.
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"

# Distinctive test ids. The web id is BELOW web_user_id_seq MINVALUE (-999999999),
# so it can never collide with a real web account (-1, -2, ...). The real tg ids are
# far larger than any real Telegram user id.
_WEB_TG = -9_000_000_057
_REAL_TG = 8_888_888_800_057
_INVITER_TG = 8_888_888_800_058
_INVITER2_TG = 8_888_888_800_059
_TEST_EMAIL = "account-merge-test@example.com"


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
    web_id = _web_internal_id(_TEST_EMAIL)
    tg_id = _telegram_internal_id(_REAL_TG)
    inviter_id = _telegram_internal_id(_INVITER_TG)
    inviter2_id = _telegram_internal_id(_INVITER2_TG)
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM analytics_internal_audit WHERE internal_user_id = ANY($1::text[])", [web_id, tg_id]
        )
        # user_emails has an FK to user_identities -> delete it first.
        await conn.execute("DELETE FROM user_emails WHERE email = $1", _TEST_EMAIL)
        await conn.execute(
            "DELETE FROM referral_relationships"
            " WHERE referred_user_id = ANY($1::text[])"
            " OR referrer_user_id = ANY($2::text[])",
            [web_id, tg_id],
            [inviter_id, inviter2_id],
        )
        await conn.execute(
            "DELETE FROM referral_codes WHERE internal_user_id = ANY($1::text[])",
            [web_id, tg_id, inviter_id, inviter2_id],
        )
        await conn.execute(
            "DELETE FROM referral_balances WHERE internal_user_id = ANY($1::text[])",
            [web_id, tg_id],
        )
        await conn.execute(
            "DELETE FROM subscription_snapshots WHERE internal_user_id = ANY($1::text[])",
            [web_id, tg_id],
        )
        await conn.execute(
            "DELETE FROM user_identities WHERE telegram_user_id = ANY($1::bigint[])",
            [_WEB_TG, _REAL_TG, _INVITER_TG, _INVITER2_TG],
        )


@pytest.mark.parametrize("internal_side", [None, "web", "telegram"])
def test_merge_detects_negative_id_web_account_and_reassigns_email(pg_url: str, internal_side: str | None) -> None:
    """REGRESSION: a web-only account has a NEGATIVE telegram_user_id. The merge
    must find it (``<= 0``) and reassign its verified email onto the real identity.
    Before the fix (``= 0``) it found nothing and returned False."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _WEB_TG,
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _REAL_TG,
                    _telegram_internal_id(_REAL_TG),
                )

            if internal_side is not None:
                await pool.execute(
                    "UPDATE user_identities SET is_internal = TRUE WHERE internal_user_id = $1",
                    web_internal if internal_side == "web" else _telegram_internal_id(_REAL_TG),
                )
            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True, "merge must detect a negative-id web-only account"
            flags = await pool.fetch(
                "SELECT is_internal FROM user_identities WHERE internal_user_id = ANY($1::text[])",
                [web_internal, _telegram_internal_id(_REAL_TG)],
            )
            assert all(row["is_internal"] == (internal_side is not None) for row in flags)
            assert await pool.fetchval(
                "SELECT count(*) FROM analytics_internal_audit WHERE internal_user_id = ANY($1::text[])",
                [web_internal, _telegram_internal_id(_REAL_TG)],
            ) == (0 if internal_side is None else 1)

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT telegram_user_id FROM user_emails WHERE email = $1",
                    _TEST_EMAIL,
                )
                assert row is not None
                assert row["telegram_user_id"] == _REAL_TG
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_merge_migrates_subscription_and_referral_from_web_to_telegram(pg_url: str) -> None:
    """A web account with an ACTIVE subscription and a referral relationship must
    have both rekeyed onto the real telegram identity, so the user keeps their
    subscription and the inviter keeps earning commission after the merge."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            tg_internal = _telegram_internal_id(_REAL_TG)
            inviter_internal = _telegram_internal_id(_INVITER_TG)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _WEB_TG,
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _REAL_TG,
                    tg_internal,
                )
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _INVITER_TG,
                    inviter_internal,
                )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label) VALUES ($1, 'active')",
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)",
                    inviter_internal,
                    "INVTEST1",
                )
                await conn.execute(
                    "INSERT INTO referral_relationships"
                    " (relationship_id, referred_user_id, referrer_user_id, level)"
                    " VALUES ($1, $2, $3, 1)",
                    "rel-merge-test-1",
                    web_internal,
                    inviter_internal,
                )

            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True

            async with pool.acquire() as conn:
                snap = await conn.fetchrow(
                    "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                    tg_internal,
                )
                assert snap is not None and snap["state_label"] == "active"
                old_snap = await conn.fetchval(
                    "SELECT 1 FROM subscription_snapshots WHERE internal_user_id = $1",
                    web_internal,
                )
                assert old_snap is None, "web account snapshot must be migrated, not duplicated"

                rel = await conn.fetchrow(
                    "SELECT referrer_user_id, level FROM referral_relationships"
                    " WHERE referred_user_id = $1 AND level = 1",
                    tg_internal,
                )
                assert rel is not None and rel["referrer_user_id"] == inviter_internal

                em = await conn.fetchval(
                    "SELECT telegram_user_id FROM user_emails WHERE email = $1",
                    _TEST_EMAIL,
                )
                assert em == _REAL_TG
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_merge_telegram_paid_account_wins_all_conflicts(pg_url: str) -> None:
    """The real Telegram identity (which already PAID) must win every conflict and
    the merge must never raise. Telegram has an ACTIVE subscription, its own referral
    code and its own referrer; the web account ALSO has a referral code and a
    (different) referrer. After merge: Telegram keeps its subscription/code/referrer,
    the web account's competing rows are discarded, the email is linked, no exception."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            tg_internal = _telegram_internal_id(_REAL_TG)
            inviter_internal = _telegram_internal_id(_INVITER_TG)
            inviter2_internal = _telegram_internal_id(_INVITER2_TG)
            async with pool.acquire() as conn:
                for tg, iid in (
                    (_WEB_TG, web_internal),
                    (_REAL_TG, tg_internal),
                    (_INVITER_TG, inviter_internal),
                    (_INVITER2_TG, inviter2_internal),
                ):
                    await conn.execute(
                        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                        tg,
                        iid,
                    )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                # web account: own referral code + referred by inviter1
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)",
                    web_internal,
                    "WEBCODE1",
                )
                await conn.execute(
                    "INSERT INTO referral_relationships"
                    " (relationship_id, referred_user_id, referrer_user_id, level)"
                    " VALUES ($1, $2, $3, 1)",
                    "rel-web-1",
                    web_internal,
                    inviter_internal,
                )
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)",
                    inviter_internal,
                    "INVTEST1",
                )
                # telegram account: ACTIVE (paid) subscription + own code + referred by inviter2
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label) VALUES ($1, 'active')",
                    tg_internal,
                )
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)",
                    tg_internal,
                    "TGCODE1",
                )
                await conn.execute(
                    "INSERT INTO referral_relationships"
                    " (relationship_id, referred_user_id, referrer_user_id, level)"
                    " VALUES ($1, $2, $3, 1)",
                    "rel-tg-1",
                    tg_internal,
                    inviter2_internal,
                )
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)",
                    inviter2_internal,
                    "INVTEST2",
                )

            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True

            async with pool.acquire() as conn:
                # Telegram's ACTIVE subscription preserved
                snap = await conn.fetchrow(
                    "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                    tg_internal,
                )
                assert snap is not None and snap["state_label"] == "active"
                # Telegram keeps its OWN referral code; web's discarded
                tg_code = await conn.fetchval(
                    "SELECT referral_code FROM referral_codes WHERE internal_user_id = $1",
                    tg_internal,
                )
                assert tg_code == "TGCODE1"
                web_code = await conn.fetchval(
                    "SELECT 1 FROM referral_codes WHERE internal_user_id = $1",
                    web_internal,
                )
                assert web_code is None
                # Telegram keeps its OWN referrer (no unique violation); web's discarded
                rel = await conn.fetchrow(
                    "SELECT referrer_user_id FROM referral_relationships WHERE referred_user_id = $1 AND level = 1",
                    tg_internal,
                )
                assert rel is not None and rel["referrer_user_id"] == inviter2_internal
                web_rel = await conn.fetchval(
                    "SELECT 1 FROM referral_relationships WHERE referred_user_id = $1",
                    web_internal,
                )
                assert web_rel is None
                # email linked to the real identity
                em = await conn.fetchval(
                    "SELECT telegram_user_id FROM user_emails WHERE email = $1",
                    _TEST_EMAIL,
                )
                assert em == _REAL_TG
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_merge_active_web_subscription_beats_expired_telegram(pg_url: str) -> None:
    """If only the web account has an ACTIVE subscription and the Telegram identity's
    is expired, the active one wins and becomes the Telegram identity's."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            tg_internal = _telegram_internal_id(_REAL_TG)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _WEB_TG,
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _REAL_TG,
                    tg_internal,
                )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label) VALUES ($1, 'active')",
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO subscription_snapshots (internal_user_id, state_label) VALUES ($1, 'expired')",
                    tg_internal,
                )

            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True

            async with pool.acquire() as conn:
                snap = await conn.fetchrow(
                    "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                    tg_internal,
                )
                assert snap is not None and snap["state_label"] == "active"
                web_snap = await conn.fetchval(
                    "SELECT 1 FROM subscription_snapshots WHERE internal_user_id = $1",
                    web_internal,
                )
                assert web_snap is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_merge_deletes_self_referral_loop_when_tg_user_owns_referrals_email(pg_url: str) -> None:
    """REGRESSION (P-4): если TG-юзер владеет email своего собственного реферала, merge
    web→TG замыкает связь на себя — строка (referred=tg_internal, referrer=tg_internal,
    level=1) — и все будущие платежи этого TG-юзера начисляли бы комиссию самому себе
    (эксплойт для выкачивания баланса). Self-loop не имеет бизнес-смысла; merge должен
    удалить любую такую строку, созданную перемаппированием. DELETE универсален — ловит
    self-loop независимо от того, какой UPDATE его создал (referred-side или referrer-side)."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            tg_internal = _telegram_internal_id(_REAL_TG)
            async with pool.acquire() as conn:
                # web-аккаунт (email _TEST_EMAIL) и TG-аккаунт — один и тот же человек.
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _WEB_TG,
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                    _REAL_TG,
                    tg_internal,
                )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                # web-юзер был приглашён TG-юзером (tg_internal — его реферёр). Это и есть
                # сетап self-loop: после merge referred (web) переезжает на tg_internal.
                await conn.execute(
                    "INSERT INTO referral_codes (internal_user_id, referral_code) VALUES ($1, $2)",
                    tg_internal,
                    "SELFCODE1",
                )
                await conn.execute(
                    "INSERT INTO referral_relationships"
                    " (relationship_id, referred_user_id, referrer_user_id, level)"
                    " VALUES ($1, $2, $3, 1)",
                    "rel-self-1",
                    web_internal,
                    tg_internal,
                )

            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True

            async with pool.acquire() as conn:
                # До фикса здесь оставалась бы self-loop (referred=tg_internal, referrer=tg_internal).
                self_loops = await conn.fetchval(
                    "SELECT count(*) FROM referral_relationships WHERE referrer_user_id = referred_user_id"
                )
                assert self_loops == 0, "merge must not leave a self-referral loop"
                self_for_tg = await conn.fetchval(
                    "SELECT count(*) FROM referral_relationships WHERE referred_user_id = $1 AND referrer_user_id = $1",
                    tg_internal,
                )
                assert self_for_tg == 0
                # email корректно перенесён на реальный TG-аккаунт (merge отработал).
                em = await conn.fetchval(
                    "SELECT telegram_user_id FROM user_emails WHERE email = $1",
                    _TEST_EMAIL,
                )
                assert em == _REAL_TG
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_merge_sums_web_balance_into_telegram(pg_url: str) -> None:
    """REGRESSION (P-3): web-баланс должен СКЛАДЫВАТЬСЯ с TG-балансом при merge, а не
    теряться. Раньше web-строка переносилась только если у TG баланса нет — иначе она
    оставалась orphan под web_<hash> (недостижима после reassign email), и web-юзер
    терял заработанную комиссию."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            tg_internal = _telegram_internal_id(_REAL_TG)
            async with pool.acquire() as conn:
                for tg, iid in ((_WEB_TG, web_internal), (_REAL_TG, tg_internal)):
                    await conn.execute(
                        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                        tg,
                        iid,
                    )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                # У обоих есть баланс: web = 100.00 ₽, tg = 50.00 ₽.
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, 10000)",
                    web_internal,
                )
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, 5000)",
                    tg_internal,
                )

            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True

            async with pool.acquire() as conn:
                tg_bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
                    tg_internal,
                )
                assert tg_bal == 15000  # 10000 + 5000 — суммирование, а не потеря web-баланса
                web_bal = await conn.fetchval(
                    "SELECT 1 FROM referral_balances WHERE internal_user_id = $1",
                    web_internal,
                )
                assert web_bal is None  # orphan web-строка удалена
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())


def test_merge_adopts_web_balance_when_telegram_has_none(pg_url: str) -> None:
    """Базовый случай: у TG баланса нет — web-баланс переносится на TG целиком."""

    async def main() -> None:
        pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
        try:
            await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
            await _cleanup(pool)
            web_internal = _web_internal_id(_TEST_EMAIL)
            tg_internal = _telegram_internal_id(_REAL_TG)
            async with pool.acquire() as conn:
                for tg, iid in ((_WEB_TG, web_internal), (_REAL_TG, tg_internal)):
                    await conn.execute(
                        "INSERT INTO user_identities (telegram_user_id, internal_user_id) VALUES ($1, $2)",
                        tg,
                        iid,
                    )
                await conn.execute(
                    "INSERT INTO user_emails (telegram_user_id, email, is_verified, verified_at)"
                    " VALUES ($1, $2, TRUE, now())",
                    _WEB_TG,
                    _TEST_EMAIL,
                )
                # Только web имеет баланс; у TG строки баланса нет.
                await conn.execute(
                    "INSERT INTO referral_balances (internal_user_id, balance_kopecks) VALUES ($1, 10000)",
                    web_internal,
                )

            merged = await merge_web_account_if_needed(pool, _REAL_TG, _TEST_EMAIL)
            assert merged is True

            async with pool.acquire() as conn:
                tg_bal = await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
                    tg_internal,
                )
                assert tg_bal == 10000  # web-баланс перенесён
                web_bal = await conn.fetchval(
                    "SELECT 1 FROM referral_balances WHERE internal_user_id = $1",
                    web_internal,
                )
                assert web_bal is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(main())
