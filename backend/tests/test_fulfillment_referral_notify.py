"""Tests for the referrer commission notification on subscription payment.

When a referral pays for a subscription, each credited referrer (L1 direct and L2 indirect)
must receive a best-effort Telegram notification with the credited amount. These tests drive
`process_fulfillment` directly with a fake pool + AsyncMock notifier + monkeypatched repos
(same technique as test_payment_fulfillment_ingress.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.persistence import postgres_referral as pgref
from app.persistence.referral_contracts import ReferralBalanceRecord
from app.runtime import fulfillment_processor as fp_mod
from app.runtime.fulfillment_processor import FulfillmentInput, process_fulfillment
from app.shared.types import OperationOutcomeCategory


class _FakeConn:
    """Minimal asyncpg.Connection mock for transaction tests."""

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, *a, **kw):
        return "OK"

    async def fetchrow(self, *a, **kw):
        return None

    async def fetchval(self, *a, **kw):
        return None


class _FakePool:
    """Minimal asyncpg.Pool mock that yields _FakeConn on acquire()."""

    @asynccontextmanager
    async def acquire(self):
        yield _FakeConn()


class _IngestResult:
    class _Record:
        internal_fact_ref = "fact-1"

    record = _Record()


class _ApplyResult:
    def __init__(self, outcome: OperationOutcomeCategory) -> None:
        self.operation_outcome = outcome
        self.idempotent_replay = outcome is OperationOutcomeCategory.IDEMPOTENT_NOOP
        self.apply_outcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED


def _payer_input(*, amount_kopecks: int = 30000, period_days: int = 30) -> FulfillmentInput:
    return FulfillmentInput(
        provider_key="provider_agnostic_v1",
        external_event_id="evt-1",
        external_payment_id="pay-1",
        telegram_user_id=12345,
        internal_user_id="u12345",
        paid_at=datetime(2026, 6, 28, tzinfo=UTC),
        period_days=period_days,
        amount_kopecks=amount_kopecks,
    )


def _mock_fulfillment_deps(
    m: pytest.MonkeyPatch,
    *,
    apply_outcome: OperationOutcomeCategory = OperationOutcomeCategory.SUCCESS,
    referrers: list | None = None,
    balance_kopecks: int = 10500,
    insert_returns: bool = True,
    find_raises: bool = False,
) -> AsyncMock:
    """Monkeypatch every DB-touching dependency of process_fulfillment + the referral path.

    Returns the credit_in_connection mock so callers can assert call counts AFTER the
    MonkeyPatch context exits (the class attribute is restored on exit)."""
    m.setattr(fp_mod.PostgresUserIdentityRepository, "create_if_absent", AsyncMock())
    m.setattr(fp_mod.PostgresSubscriptionSnapshotReader, "get_for_user_in_connection", AsyncMock(return_value=None))
    m.setattr(fp_mod.PostgresSubscriptionSnapshotReader, "upsert_state_in_connection", AsyncMock())
    m.setattr(
        fp_mod.PostgresAtomicBillingIngestion,
        "ingest_in_connection",
        AsyncMock(return_value=_IngestResult()),
    )
    m.setattr(
        fp_mod.PostgresAtomicUC05SubscriptionApply,
        "apply_in_connection",
        AsyncMock(return_value=_ApplyResult(apply_outcome)),
    )
    find = AsyncMock(side_effect=RuntimeError("boom")) if find_raises else AsyncMock(return_value=referrers or [])
    m.setattr(pgref.PostgresReferralRelationshipRepository, "find_referrers", find)
    m.setattr(
        pgref.PostgresReferralTransactionRepository,
        "append_transaction_if_description_absent_in_connection",
        AsyncMock(return_value=insert_returns),
    )
    credit = AsyncMock()
    m.setattr(pgref.PostgresReferralBalanceRepository, "credit_in_connection", credit)
    m.setattr(
        pgref.PostgresReferralBalanceRepository,
        "get_balance",
        AsyncMock(return_value=ReferralBalanceRecord(
            internal_user_id="x", balance_kopecks=balance_kopecks, updated_at=datetime(2026, 6, 28, tzinfo=UTC),
        )),
    )
    return credit


def _notifier_calls_to(notifier: AsyncMock, tg_id: int) -> list:
    return [c for c in notifier.send_subscription_activated_notice.await_args_list
            if c.kwargs.get("telegram_user_id") == tg_id]


# ── unit: telegram_user_id_from_internal ───────────────────────────────────────


def test_telegram_user_id_from_internal_parses_sign() -> None:
    from app.persistence.postgres_user_identity import telegram_user_id_from_internal
    assert telegram_user_id_from_internal("u999") == 999
    assert telegram_user_id_from_internal("u-12345") == -12345  # web user → negative → skip
    assert telegram_user_id_from_internal("garbage") is None
    assert telegram_user_id_from_internal("u") is None
    assert telegram_user_id_from_internal("") is None


# ── process_fulfillment referral-notification behaviour ─────────────────────────


@pytest.mark.asyncio
async def test_referrer_notified_with_commission_on_new_activation() -> None:
    notifier = AsyncMock()
    with pytest.MonkeyPatch.context() as m:
        _mock_fulfillment_deps(
            m, referrers=[SimpleNamespace(level=1, referrer_user_id="u999")], balance_kopecks=10500,
        )
        result = await process_fulfillment(
            pool=_FakePool(), inp=_payer_input(), activation_telegram_notifier=notifier,
        )
    assert result.operation_outcome is OperationOutcomeCategory.SUCCESS
    # Activation notice (payer 12345) + commission notice (referrer 999).
    assert notifier.send_subscription_activated_notice.await_count == 2
    ref_calls = _notifier_calls_to(notifier, 999)
    assert len(ref_calls) == 1
    kwargs = ref_calls[0].kwargs
    assert kwargs["reply_markup"] is None
    text = kwargs["text"]
    assert "Реферальная программа" in text
    assert "105" in text  # 30000 * 0.35 (1m L1 rate) = 10500 kopecks = 105 ₽
    assert "Ваш реферал оплатил подписку" in text


@pytest.mark.asyncio
async def test_l1_and_l2_referrers_both_notified() -> None:
    notifier = AsyncMock()
    with pytest.MonkeyPatch.context() as m:
        _mock_fulfillment_deps(
            m,
            referrers=[
                SimpleNamespace(level=1, referrer_user_id="u999"),
                SimpleNamespace(level=2, referrer_user_id="u888"),
            ],
        )
        await process_fulfillment(
            pool=_FakePool(), inp=_payer_input(), activation_telegram_notifier=notifier,
        )
    # payer (12345) + L1 (999) + L2 (888).
    assert notifier.send_subscription_activated_notice.await_count == 3
    l1 = _notifier_calls_to(notifier, 999)[0].kwargs
    l2 = _notifier_calls_to(notifier, 888)[0].kwargs
    assert "Ваш реферал оплатил подписку" in l1["text"] and "105" in l1["text"]   # 0.35
    assert "2-го уровня" in l2["text"] and "15" in l2["text"]                      # 0.05
    assert l1["reply_markup"] is None and l2["reply_markup"] is None


@pytest.mark.asyncio
async def test_no_referral_notice_on_idempotent_replay() -> None:
    notifier = AsyncMock()
    with pytest.MonkeyPatch.context() as m:
        credit = _mock_fulfillment_deps(
            m,
            apply_outcome=OperationOutcomeCategory.IDEMPOTENT_NOOP,
            referrers=[SimpleNamespace(level=1, referrer_user_id="u999")],
        )
        await process_fulfillment(
            pool=_FakePool(), inp=_payer_input(), activation_telegram_notifier=notifier,
        )
    # is_new_active False -> no activation notice, no referral processing, no commission notice.
    notifier.send_subscription_activated_notice.assert_not_called()
    assert credit.call_count == 0


@pytest.mark.asyncio
async def test_web_user_referrer_skipped_but_credit_applied() -> None:
    notifier = AsyncMock()
    with pytest.MonkeyPatch.context() as m:
        credit = _mock_fulfillment_deps(
            m, referrers=[SimpleNamespace(level=1, referrer_user_id="u-12345")],
        )
        await process_fulfillment(
            pool=_FakePool(), inp=_payer_input(), activation_telegram_notifier=notifier,
        )
    # Credit IS applied (web referrer still earns on their balance) ...
    assert credit.call_count == 1
    # ... but no Telegram notification can be delivered (negative id -> skip).
    assert notifier.send_subscription_activated_notice.await_count == 1  # payer activation only
    assert _notifier_calls_to(notifier, -12345) == []


@pytest.mark.asyncio
async def test_credit_applied_even_when_notifier_is_none() -> None:
    with pytest.MonkeyPatch.context() as m:
        credit = _mock_fulfillment_deps(
            m, referrers=[SimpleNamespace(level=1, referrer_user_id="u999")],
        )
        result = await process_fulfillment(pool=_FakePool(), inp=_payer_input())  # no notifier
    assert result.operation_outcome is OperationOutcomeCategory.SUCCESS
    assert credit.call_count == 1


@pytest.mark.asyncio
async def test_referral_exception_does_not_break_fulfillment() -> None:
    notifier = AsyncMock()
    with pytest.MonkeyPatch.context() as m:
        _mock_fulfillment_deps(m, find_raises=True)
        result = await process_fulfillment(
            pool=_FakePool(), inp=_payer_input(), activation_telegram_notifier=notifier,
        )
    # Payer fulfillment still succeeds; the swallowed referral error yields no commission notice.
    assert result.operation_outcome is OperationOutcomeCategory.SUCCESS
    assert notifier.send_subscription_activated_notice.await_count == 1  # payer activation only
