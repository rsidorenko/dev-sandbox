"""Unit tests for referral-transaction display-label derivation and filter parsing
(``web_api/referral.py``).

These run WITHOUT PostgreSQL and guard the security guarantee that the cabinet never
surfaces raw internal ``description``/``idempotency_key`` data: the user-facing label is
always derived from transaction_type + amount sign. See migration 070 and the rework of
the "Последние начисления" history.
"""

from __future__ import annotations

from app.web_api.referral import _parse_tx_filters, _tx_label


def test_tx_label_referral_credit():
    assert _tx_label("referral_credit", 5000) == "Реферальное начисление"


def test_tx_label_subscription_payment_debit_vs_refund():
    # negative amount = a debit (paying for a subscription)
    assert _tx_label("subscription_payment", -24900) == "Оплата подписки"
    # positive amount = a refund (e.g. VLESS-failure compensation)
    assert _tx_label("subscription_payment", 24900) == "Возврат"


def test_tx_label_unknown_type_falls_back_neutrally():
    assert _tx_label("something_else", 100) == "Операция"


def test_parse_tx_filters_defaults():
    level, tx_type, limit, offset = _parse_tx_filters({})
    assert level is None
    assert tx_type is None
    assert limit == 20
    assert offset == 0


def test_parse_tx_filters_level_type_caseinsensitive_and_clamping():
    level, tx_type, limit, offset = _parse_tx_filters({"level": "2", "type": "CREDIT", "limit": "999", "offset": "5"})
    assert level == 2
    assert tx_type == "credit"  # accepted case-insensitively
    assert limit == 100  # clamped to max
    assert offset == 5


def test_parse_tx_filters_rejects_garbage():
    level, tx_type, limit, offset = _parse_tx_filters({"level": "3", "type": "bogus", "limit": "abc", "offset": "-7"})
    assert level is None  # only 1 / 2 accepted
    assert tx_type is None  # only credit / debit accepted
    assert limit == 20  # fallback on parse error
    assert offset == 0  # negative clamped to 0
