"""Юнит-тесты домена веб-уведомлений и хелперов Postgres-хранилища (без БД).

SQL-пути хранилища покрыты интеграционными тестами (test_web_api_notifications,
test_admin_broadcast_integration) — юниты здесь только на чистые функции.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.web_notifications import (
    WEB_NOTIFICATION_KINDS,
    compose_claim_status,
    plain_title,
)
from app.persistence.postgres_web_notifications import _insert_count, decode_payload

_MIGRATION_077 = Path(__file__).resolve().parents[1] / "migrations" / "077_web_notifications.sql"


def test_kinds_match_migration_check() -> None:
    """Доменный набор kind обязан совпадать с CHECK миграции 077 — иначе
    INSERT из scheduler/fan-out упадёт на констрейнте."""
    sql = _MIGRATION_077.read_text(encoding="utf-8")
    for kind in WEB_NOTIFICATION_KINDS:
        assert f"'{kind}'" in sql, kind


def test_plain_title_strips_tags_and_collapses_whitespace() -> None:
    assert plain_title("<b>Привет,</b> <i>мир</i>!\nНовая строка") == "Привет, мир! Новая строка"


def test_plain_title_truncates_with_ellipsis() -> None:
    result = plain_title("а" * 100, max_len=80)
    assert len(result) == 80
    assert result.endswith("…")


def test_plain_title_empty_falls_back_to_default() -> None:
    assert plain_title("") == "Сообщение от Bravada VPN"
    assert plain_title("<br>") == "Сообщение от Bravada VPN"


def test_compose_claim_status_no_bonus() -> None:
    assert compose_claim_status(bonus=None) is None
    assert compose_claim_status(bonus={}) is None


def test_compose_claim_status_days_matrix() -> None:
    now = datetime(2026, 8, 31, tzinfo=UTC)
    future = now + timedelta(days=3)
    past = now - timedelta(days=1)
    # нет claim-строки (не получатель) — кнопку не рендерим
    assert compose_claim_status(bonus={"type": "days"}, now=now) is None
    assert (
        compose_claim_status(
            bonus={"type": "days"}, days_claim={"status": "claimed", "expires_at": past}, now=now
        )
        == "claimed"
    )
    assert (
        compose_claim_status(
            bonus={"type": "days"}, days_claim={"status": "eligible", "expires_at": past}, now=now
        )
        == "expired"
    )
    assert (
        compose_claim_status(
            bonus={"type": "days"}, days_claim={"status": "eligible", "expires_at": future}, now=now
        )
        == "eligible"
    )


def test_compose_claim_status_promo_matrix() -> None:
    now = datetime(2026, 8, 31, tzinfo=UTC)
    future = now + timedelta(days=3)
    past = now - timedelta(days=1)
    assert compose_claim_status(bonus={"type": "promo"}, now=now) is None
    # уже применён тем же кодом → claimed (даже если claim-окно истекло)
    assert (
        compose_claim_status(
            bonus={"type": "promo"},
            promo_claim={"code": "SALE", "expires_at": past},
            applied_code="SALE",
            now=now,
        )
        == "claimed"
    )
    assert (
        compose_claim_status(
            bonus={"type": "promo"}, promo_claim={"code": "SALE", "expires_at": past}, now=now
        )
        == "expired"
    )
    assert (
        compose_claim_status(
            bonus={"type": "promo"}, promo_claim={"code": "SALE", "expires_at": future}, now=now
        )
        == "eligible"
    )


def test_decode_payload_variants() -> None:
    assert decode_payload(None) == {}
    assert decode_payload('{"a": 1}') == {"a": 1}
    assert decode_payload({"a": 1}) == {"a": 1}
    assert decode_payload("not json") == {}
    assert decode_payload('[1, 2]') == {}


def test_insert_count_parses_asyncpg_status() -> None:
    assert _insert_count("INSERT 0 42") == 42
    assert _insert_count("INSERT 0") == 0
    assert _insert_count("") == 0
