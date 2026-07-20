"""Direct unit tests for the balance-payment service (in-memory seam, pool=None).

The postgres path is exercised end-to-end by test_web_api_payment_balance.py (opt-in
DATABASE_URL); this file covers the in-memory branch used by the bot unit-test seam
and validates the BalancePaymentResult contract for both entry-points.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.balance_payment_service import (
    BalancePaymentStatus,
    add_device_from_balance,
    pay_plan_from_balance,
)
from app.application.bootstrap import build_slice1_composition
from app.application.interfaces import SubscriptionSnapshot
from app.issuance.vless_provider import StubVlessProvider
from app.shared.test_helpers import run_async as _run

_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


def _composition(user_id: int = 9001, balance_kopecks: int = 0):
    c = build_slice1_composition(bot_username="testbot", vless_provider=StubVlessProvider())
    _run(c.identity.create_if_absent(user_id))
    id_rec = _run(c.identity.find_by_telegram_user_id(user_id))
    if balance_kopecks > 0:
        _run(c.referral_balance_repo.credit(id_rec.internal_user_id, balance_kopecks))
    return c, id_rec.internal_user_id


# ── pay_plan_from_balance (in-memory) ────────────────────────────────────────


def test_pay_plan_completed_inmemory():
    c, uid = _composition(balance_kopecks=500_00)
    result = _run(
        pay_plan_from_balance(
            pool=None,
            composition=c,
            internal_user_id=uid,
            plan_id="1m",
            device_count=5,
            vless_provider=StubVlessProvider(),
        )
    )
    assert result.status is BalancePaymentStatus.COMPLETED
    assert result.plan_id == "1m"
    assert result.amount_kopecks == 249_00
    assert result.active_until_utc is not None
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal is not None and bal.balance_kopecks == 251_00  # 500 - 249
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap is not None and snap.state_label == "active" and snap.plan_id == "1m"


def test_pay_plan_insufficient_inmemory():
    c, uid = _composition(balance_kopecks=100_00)
    result = _run(pay_plan_from_balance(pool=None, composition=c, internal_user_id=uid, plan_id="1m", device_count=5))
    assert result.status is BalancePaymentStatus.INSUFFICIENT
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal is not None and bal.balance_kopecks == 100_00  # unchanged


def test_pay_plan_invalid_plan():
    c, uid = _composition(balance_kopecks=500_00)
    result = _run(pay_plan_from_balance(pool=None, composition=c, internal_user_id=uid, plan_id="nope", device_count=5))
    assert result.status is BalancePaymentStatus.INVALID_PLAN


def test_pay_plan_invalid_device_count():
    c, uid = _composition(balance_kopecks=500_00)
    result = _run(pay_plan_from_balance(pool=None, composition=c, internal_user_id=uid, plan_id="1m", device_count=0))
    assert result.status is BalancePaymentStatus.INVALID_DEVICE_COUNT


# ── add_device_from_balance (in-memory) ──────────────────────────────────────


def test_add_device_completed_inmemory():
    c, uid = _composition(balance_kopecks=500_00)
    _run(
        c.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=uid, state_label="active", active_until_utc=_FUTURE, plan_id="1m", device_count=5
            )
        )
    )
    result = _run(add_device_from_balance(pool=None, composition=c, internal_user_id=uid, new_device_count=7))
    assert result.status is BalancePaymentStatus.COMPLETED
    assert result.device_count == 7
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap is not None and snap.device_count == 7


def test_add_device_no_active_inmemory():
    c, uid = _composition(balance_kopecks=500_00)
    result = _run(add_device_from_balance(pool=None, composition=c, internal_user_id=uid, new_device_count=7))
    assert result.status is BalancePaymentStatus.NO_ACTIVE


def test_add_device_not_higher_inmemory():
    c, uid = _composition(balance_kopecks=500_00)
    _run(
        c.snapshots.upsert_state(
            SubscriptionSnapshot(
                internal_user_id=uid, state_label="active", active_until_utc=_FUTURE, plan_id="1m", device_count=7
            )
        )
    )
    result = _run(add_device_from_balance(pool=None, composition=c, internal_user_id=uid, new_device_count=7))
    assert result.status is BalancePaymentStatus.NOT_HIGHER
