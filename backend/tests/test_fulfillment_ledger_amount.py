"""Unit tests for fulfillment_processor ledger amount derivation (no DB).

Guards the regression where the paid amount was hardcoded to None in the billing
ledger record, so every real YooKassa webhook payment was stored with a NULL amount.
"""

from __future__ import annotations

from app.runtime.fulfillment_processor import _ledger_amount_currency


def test_ledger_amount_currency_captures_paid_kopecks_as_rub() -> None:
    # 12 RUB paid (the 1-day plan) -> 1200 minor units, RUB
    result = _ledger_amount_currency(1200)
    assert result is not None
    assert result.amount_minor_units == 1200
    assert result.currency_code == "RUB"


def test_ledger_amount_currency_none_when_amount_unknown() -> None:
    # The ledger amount column is intentionally nullable; unknown amount stays None.
    assert _ledger_amount_currency(None) is None


def test_ledger_amount_currency_preserves_larger_amounts() -> None:
    # A 1-month plan at 249 RUB -> 24900 minor units.
    result = _ledger_amount_currency(24900)
    assert result is not None
    assert result.amount_minor_units == 24900
    assert result.currency_code == "RUB"
