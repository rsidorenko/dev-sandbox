"""Tests for YooKassa webhook source verification (IP-based, per YooKassa docs).

YooKassa does NOT send an HMAC signature header; authenticity is verified by
source IP (+ object-status re-fetch via the API). The prior signature check
rejected every real notification and broke all payments — these tests lock in
the IP-based verification that replaced it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.yookassa.webhook import (
    _client_ip,
    _is_yookassa_ip,
    _verify_yookassa_source,
)


class _FakeClient:
    def __init__(self, host: str | None) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in for starlette.Request for the verification helpers."""

    def __init__(self, headers: dict[str, str] | None = None, client_host: str | None = None) -> None:
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host is not None else None


# ── _is_yookassa_ip ──────────────────────────────────────────────────────────


def test_is_yookassa_ip_recognizes_documented_ranges() -> None:
    assert _is_yookassa_ip("185.71.76.10") is True  # 185.71.76.0/27
    assert _is_yookassa_ip("185.71.77.5") is True  # 185.71.77.0/27
    assert _is_yookassa_ip("77.75.153.10") is True  # 77.75.153.0/25
    assert _is_yookassa_ip("77.75.156.11") is True  # /32
    assert _is_yookassa_ip("77.75.156.35") is True  # /32
    assert _is_yookassa_ip("77.75.154.200") is True  # 77.75.154.128/25
    assert _is_yookassa_ip("2a02:5180::1") is True  # 2a02:5180::/32


def test_is_yookassa_ip_rejects_non_yookassa() -> None:
    assert _is_yookassa_ip("8.8.8.8") is False
    assert _is_yookassa_ip("172.18.0.6") is False  # nginx docker IP (not YooKassa)
    assert _is_yookassa_ip("127.0.0.1") is False
    assert _is_yookassa_ip("not-an-ip") is False
    assert _is_yookassa_ip("") is False


# ── _client_ip ───────────────────────────────────────────────────────────────


def test_client_ip_prefers_x_forwarded_for_leftmost() -> None:
    req = _FakeRequest({"x-forwarded-for": "185.71.76.10, 10.0.0.1"}, client_host="172.18.0.6")
    assert _client_ip(req) == "185.71.76.10"


def test_client_ip_falls_back_to_x_real_ip() -> None:
    req = _FakeRequest({"x-real-ip": "77.75.156.35"}, client_host="172.18.0.6")
    assert _client_ip(req) == "77.75.156.35"


def test_client_ip_falls_back_to_connection_host() -> None:
    req = _FakeRequest({}, client_host="172.18.0.6")
    assert _client_ip(req) == "172.18.0.6"


def test_client_ip_none_when_nothing_available() -> None:
    req = _FakeRequest({}, client_host=None)
    assert _client_ip(req) is None


# ── _verify_yookassa_source ──────────────────────────────────────────────────


def test_source_accepts_yookassa_ip_via_xff() -> None:
    req = _FakeRequest({"x-forwarded-for": "185.71.76.10"})
    assert _verify_yookassa_source(req) is None


def test_source_rejects_non_yookassa_ip_when_forwarded() -> None:
    # A forwarded IP is present but it's not a YooKassa range -> reject (401).
    req = _FakeRequest({"x-forwarded-for": "8.8.8.8"})
    resp = _verify_yookassa_source(req)
    assert resp is not None
    assert resp.status_code == 401


def test_source_rejects_spoofed_xff_from_local_range() -> None:
    req = _FakeRequest({"x-forwarded-for": "172.18.0.6"})
    resp = _verify_yookassa_source(req)
    assert resp is not None
    assert resp.status_code == 401


def test_source_relies_on_api_when_no_forwarded_header() -> None:
    # No XFF/X-Real-IP at all (e.g. proxy didn't set it) -> proceed, rely on the
    # authoritative API re-fetch rather than rejecting (which would break payments).
    req = _FakeRequest({}, client_host="172.18.0.6")
    assert _verify_yookassa_source(req) is None


# ── add_device metadata parsing / amount validation ─────────────────────────


def test_parse_add_device_metadata_valid() -> None:
    from app.runtime.payment_webhook_helpers import parse_add_device_metadata as _parse_add_device_metadata

    # Legacy in-flight платеж (до R-08): только tg-id → derive f"u{tg}".
    assert _parse_add_device_metadata(
        {"telegram_user_id": "123", "new_device_count": "7", "expected_amount_kopecks": "16000"}
    ) == ("u123", 7, 16000)
    # Новый платёж: canonical internal_user_id (web-only = web_<hash>, R-14).
    assert _parse_add_device_metadata(
        {"internal_user_id": "web_abc123", "new_device_count": "7", "expected_amount_kopecks": "16000"}
    ) == ("web_abc123", 7, 16000)
    # Явный internal_user_id побеждает остаточный синтетический tg-id.
    assert _parse_add_device_metadata(
        {
            "internal_user_id": "web_abc123",
            "telegram_user_id": "-1",
            "new_device_count": "7",
            "expected_amount_kopecks": "16000",
        }
    ) == ("web_abc123", 7, 16000)


def test_parse_add_device_metadata_rejects_out_of_range_count() -> None:
    from app.runtime.payment_webhook_helpers import parse_add_device_metadata as _parse_add_device_metadata

    assert (
        _parse_add_device_metadata({"telegram_user_id": "1", "new_device_count": "0", "expected_amount_kopecks": "100"})
        is None
    )
    assert (
        _parse_add_device_metadata(
            {"telegram_user_id": "1", "new_device_count": "21", "expected_amount_kopecks": "100"}
        )
        is None
    )


def test_parse_add_device_metadata_rejects_missing_or_invalid() -> None:
    from app.runtime.payment_webhook_helpers import parse_add_device_metadata as _parse_add_device_metadata

    assert _parse_add_device_metadata({}) is None
    assert _parse_add_device_metadata({"telegram_user_id": "1", "new_device_count": "5"}) is None  # no expected amount
    assert (
        _parse_add_device_metadata({"telegram_user_id": "x", "new_device_count": "5", "expected_amount_kopecks": "100"})
        is None
    )  # non-int telegram id
    assert (
        _parse_add_device_metadata({"telegram_user_id": "0", "new_device_count": "5", "expected_amount_kopecks": "100"})
        is None
    )  # non-positive telegram id


def test_validate_add_device_amount_matches() -> None:
    from app.runtime.payment_webhook_helpers import validate_add_device_amount as _validate_add_device_amount

    assert _validate_add_device_amount(16000, 16000) is True
    assert _validate_add_device_amount(16000, 16001) is True  # +1 kop tolerance
    assert _validate_add_device_amount(16000, 15999) is True  # -1 kop tolerance


def test_validate_add_device_amount_rejects_mismatch() -> None:
    from app.runtime.payment_webhook_helpers import validate_add_device_amount as _validate_add_device_amount

    assert _validate_add_device_amount(16000, 15000) is False
    assert _validate_add_device_amount(16000, None) is False
    assert _validate_add_device_amount(0, 100) is False


# ── subscription amount with extra devices (regression: card purchases) ──────


def test_expected_subscription_kopecks_includes_extra_devices() -> None:
    """A card purchase with >5 devices must expect plan + extra-device cost.

    Previously the webhook expected only plan.price_rubles, so a 1m + 2 extra
    devices purchase (charged 409 RUB) was rejected as amount_mismatch (expected
    249 RUB) — user charged but not subscribed. Now it expects the full amount.
    """
    from app.domain.plans import get_plan
    from app.runtime.payment_webhook_helpers import expected_subscription_kopecks as _expected_subscription_kopecks

    plan_1m = get_plan("1m")
    # Default 5 devices: no extra -> just the plan price (unchanged behavior).
    assert _expected_subscription_kopecks(plan_1m, 5) == plan_1m.price_rubles * 100
    # 7 devices = 2 extra × 80 RUB (1m/30d) on top of 249 RUB.
    assert _expected_subscription_kopecks(plan_1m, 7) == (plan_1m.price_rubles + 160) * 100


def test_fulfillment_input_carries_device_count_default_and_override() -> None:
    """FulfillmentInput has a device_count field (default 5) the webhook sets so a
    card purchase with extra devices actually grants them (was hardcoded to 5)."""
    from app.domain.plans import DEFAULT_DEVICE_LIMIT
    from app.runtime.fulfillment_processor import FulfillmentInput

    assert FulfillmentInput.__dataclass_fields__["device_count"].default == DEFAULT_DEVICE_LIMIT
    inp = FulfillmentInput(
        provider_key="yookassa_v1",
        external_event_id="yookassa:p1",
        external_payment_id="p1",
        telegram_user_id=1,
        internal_user_id="u1",
        paid_at=datetime.now(UTC),
        period_days=30,
        device_count=7,
    )
    assert inp.device_count == 7


def test_amount_value_to_kopecks_is_exact() -> None:
    """Provider money strings must convert to exact integer kopecks.

    Locks in Decimal-based parsing (replaces the prior ``round(float(amount)*100)``
    path, a fragile smell against the ``*_kopecks`` money-arithmetic rule). For
    realistic 2-decimal provider amounts the result is exact; fractional-kopeck
    inputs round HALF_UP; unparseable/missing values return ``None`` so callers
    fall through to their existing amount-mismatch / validation paths.
    """
    from app.runtime.payment_webhook_helpers import amount_value_to_kopecks as _amount_value_to_kopecks

    assert _amount_value_to_kopecks("1099.99") == 109999
    assert _amount_value_to_kopecks("750.00") == 75000
    assert _amount_value_to_kopecks("300") == 30000
    assert _amount_value_to_kopecks("0.99") == 99
    # HALF_UP for fractional kopecks (defensive — providers send 2dp).
    assert _amount_value_to_kopecks("1.005") == 101
    # Unparseable / missing → None.
    assert _amount_value_to_kopecks("abc") is None
    assert _amount_value_to_kopecks(None) is None
    assert _amount_value_to_kopecks("") is None


# ── subscription identity resolution (web-only vs Telegram) ──────────────────


def test_resolve_internal_user_id_prefers_metadata_for_web_only() -> None:
    """Web-only (email) accounts: metadata carries the real 'web_<hash>' identity,
    which MUST win over the ``f"u{telegram_user_id}"`` derivation (that would yield a
    bogus 'u-1' and credit the subscription to the wrong/nonexistent account)."""
    from app.runtime.payment_webhook_helpers import (
        resolve_subscription_internal_user_id as _resolve_subscription_internal_user_id,
    )

    assert _resolve_subscription_internal_user_id({"internal_user_id": "web_abc123"}, -1) == "web_abc123"


def test_resolve_internal_user_id_falls_back_to_telegram_convention() -> None:
    """Legacy / Telegram payments without metadata.internal_user_id derive ``f"u{tg}"``."""
    from app.runtime.payment_webhook_helpers import (
        resolve_subscription_internal_user_id as _resolve_subscription_internal_user_id,
    )

    assert _resolve_subscription_internal_user_id({}, 123) == "u123"
    assert _resolve_subscription_internal_user_id({"plan_id": "1m"}, 123) == "u123"


def test_resolve_internal_user_id_ignores_empty_metadata_value() -> None:
    """An empty-string internal_user_id must fall back (truthiness), not yield ''."""
    from app.runtime.payment_webhook_helpers import (
        resolve_subscription_internal_user_id as _resolve_subscription_internal_user_id,
    )

    assert _resolve_subscription_internal_user_id({"internal_user_id": ""}, 456) == "u456"


def test_resolve_internal_user_id_empty_when_no_identity_at_all() -> None:
    """R-08: без metadata.internal_user_id и без положительного tg идентичности нет —
    «» означает «отклонить payload» (вызывающий код вернёт 400 invalid_payload)."""
    from app.runtime.payment_webhook_helpers import (
        resolve_subscription_internal_user_id as _resolve_subscription_internal_user_id,
    )

    assert _resolve_subscription_internal_user_id({}, 0) == ""
    assert _resolve_subscription_internal_user_id({}, -1) == ""


# ── metadata цен: base_amount_kopecks / discount_kopecks (промо ИЛИ акция) ───


def test_base_amount_from_metadata_prefers_fixed_value() -> None:
    """base_amount_kopecks фиксируется при создании платежа — защита «летающих»
    платежей от смены цены админом между созданием и вебхуком."""
    from app.runtime.payment_webhook_helpers import base_amount_from_metadata

    assert base_amount_from_metadata({"base_amount_kopecks": "24900"}) == 24900
    # legacy-платёж без поля → None (вебхук пересчитает из кода)
    assert base_amount_from_metadata({}) is None
    assert base_amount_from_metadata({"plan_id": "1m"}) is None
    # мусор не роняет вебхук — трактуем как отсутствие
    assert base_amount_from_metadata({"base_amount_kopecks": "oops"}) is None
    assert base_amount_from_metadata({"base_amount_kopecks": "0"}) is None


def test_discount_kopecks_from_metadata_reads_global_leg_too() -> None:
    """При победе глобальной скидки metadata несёт discount_kopecks БЕЗ promo_code:
    скидка должна читаться и без кода (сверка суммы вебхуком)."""
    from app.runtime.payment_webhook_helpers import discount_kopecks_from_metadata

    assert discount_kopecks_from_metadata({"discount_kopecks": "5000"}) == 5000
    assert discount_kopecks_from_metadata({"promo_code": "WELCOME", "discount_kopecks": "12400"}) == 12400
    assert discount_kopecks_from_metadata({}) == 0  # legacy без поля
    assert discount_kopecks_from_metadata({"discount_kopecks": "oops"}) == 0
