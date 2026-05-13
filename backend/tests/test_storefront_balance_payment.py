"""Tests for balance payment and add/remove device storefront flows."""

from __future__ import annotations

import asyncio

from app.application.bootstrap import build_slice1_composition
from app.application.purchase_handler import build_purchase_summary
from app.bot_transport.runtime_facade import handle_slice1_telegram_update_to_rendered_message
from app.issuance.vless_provider import StubVlessProvider
from app.persistence.referral_contracts import ReferralTransactionRecord
from datetime import UTC, datetime


def _run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _callback_update(*, user_id: int, callback_data: str):
    return {
        "update_id": 9001,
        "callback_query": {
            "id": "cb1",
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "data": callback_data,
            "chat_instance": "123",
        },
    }


def _make_composition_with_balance(user_id: int = 123, balance_kopecks: int = 0):
    c = build_slice1_composition(
        bot_username="testbot",
        vless_provider=StubVlessProvider(),
    )
    _run(c.identity.create_if_absent(user_id))
    id_rec = _run(c.identity.find_by_telegram_user_id(user_id))
    if balance_kopecks > 0:
        _run(c.referral_balance_repo.credit(id_rec.internal_user_id, balance_kopecks))
    return c, id_rec.internal_user_id


_FUTURE_DATE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


# ─── Balance payment tests ────────────────────────────────────────────


def test_balance_payment_activates_subscription():
    c, uid = _make_composition_with_balance(user_id=100, balance_kopecks=300_00)
    update = _callback_update(user_id=100, callback_data="pay_balance:1m:5")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "оплачена с реферального баланса" in pkg.message_text
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap is not None
    assert snap.state_label == "active"
    assert snap.plan_id == "1m"
    assert snap.device_count == 5


def test_balance_payment_deducts_from_balance():
    c, uid = _make_composition_with_balance(user_id=101, balance_kopecks=500_00)
    update = _callback_update(user_id=101, callback_data="pay_balance:1m:5")
    _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal is not None
    assert bal.balance_kopecks == 200_00  # 500 - 300


def test_balance_payment_records_debit_transaction():
    c, uid = _make_composition_with_balance(user_id=102, balance_kopecks=300_00)
    update = _callback_update(user_id=102, callback_data="pay_balance:1m:5")
    _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    txs = _run(c.referral_transaction_repo.list_by_user(uid, limit=10))
    debit_txs = [t for t in txs if t.transaction_type == "subscription_payment"]
    assert len(debit_txs) == 1
    assert debit_txs[0].amount_kopecks == 300_00
    assert debit_txs[0].related_plan_id == "1m"


def test_balance_payment_insufficient_funds():
    c, _ = _make_composition_with_balance(user_id=103, balance_kopecks=100_00)
    update = _callback_update(user_id=103, callback_data="pay_balance:1m:5")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "Недостаточно средств" in pkg.message_text


def test_balance_payment_with_extra_devices():
    c, uid = _make_composition_with_balance(user_id=104, balance_kopecks=1000_00)
    update = _callback_update(user_id=104, callback_data="pay_balance:1m:7")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "оплачена с реферального баланса" in pkg.message_text
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap.device_count == 7
    # Cost: 300 + 2*80*1 = 460
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal.balance_kopecks == 540_00  # 1000 - 460


def test_balance_payment_3_month_plan():
    c, uid = _make_composition_with_balance(user_id=105, balance_kopecks=1000_00)
    update = _callback_update(user_id=105, callback_data="pay_balance:3m:5")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "оплачена с реферального баланса" in pkg.message_text
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal.balance_kopecks == 250_00  # 1000 - 750


# ─── Add device tests ─────────────────────────────────────────────────


def test_add_device_shows_selector():
    c, uid = _make_composition_with_balance(user_id=200, balance_kopecks=0)
    # Activate subscription first
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=5,
    )))
    update = _callback_update(user_id=200, callback_data="add_device")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "Добавление устройств" in pkg.message_text
    assert "5" in pkg.message_text


def test_add_device_with_balance_payment():
    c, uid = _make_composition_with_balance(user_id=201, balance_kopecks=500_00)
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=5,
    )))
    update = _callback_update(user_id=201, callback_data="add_dev_bal:7")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "Готово" in pkg.message_text
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap.device_count == 7
    # Cost: 2 * 80 = 160
    bal = _run(c.referral_balance_repo.get_balance(uid))
    assert bal.balance_kopecks == 340_00  # 500 - 160


def test_add_device_records_transaction():
    c, uid = _make_composition_with_balance(user_id=202, balance_kopecks=500_00)
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=5,
    )))
    update = _callback_update(user_id=202, callback_data="add_dev_bal:6")
    _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    txs = _run(c.referral_transaction_repo.list_by_user(uid, limit=10))
    debit_txs = [t for t in txs if t.transaction_type == "subscription_payment"]
    assert len(debit_txs) == 1
    assert "5 → 6" in debit_txs[0].description


def test_add_device_no_subscription():
    c, _ = _make_composition_with_balance(user_id=203, balance_kopecks=0)
    update = _callback_update(user_id=203, callback_data="add_device")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "нет активной подписки" in pkg.message_text


# ─── Remove device tests ──────────────────────────────────────────────


def test_remove_device_shows_confirm():
    c, uid = _make_composition_with_balance(user_id=300, balance_kopecks=0)
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=7,
    )))
    update = _callback_update(user_id=300, callback_data="remove_device")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "Снижение устройств" in pkg.message_text
    assert "7" in pkg.message_text
    assert "6" in pkg.message_text


def test_remove_device_confirmed():
    c, uid = _make_composition_with_balance(user_id=301, balance_kopecks=0)
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=7,
    )))
    update = _callback_update(user_id=301, callback_data="remove_dev_confirm:6")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "Готово" in pkg.message_text
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap.device_count == 6


def test_remove_device_down_to_5():
    c, uid = _make_composition_with_balance(user_id=302, balance_kopecks=0)
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=6,
    )))
    update = _callback_update(user_id=302, callback_data="remove_dev_confirm:5")
    _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    snap = _run(c.snapshots.get_for_user(uid))
    assert snap.device_count == 5


def test_remove_device_at_5_shows_settings():
    c, uid = _make_composition_with_balance(user_id=303, balance_kopecks=0)
    from app.application.interfaces import SubscriptionSnapshot
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="1m", device_count=5,
    )))
    update = _callback_update(user_id=303, callback_data="remove_device")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "Настройки подписки" in pkg.message_text


# ─── Renewal and settings enrichment tests ────────────────────────────


def test_balance_payment_extends_active_subscription():
    """Renewal should extend from current active_until, not from now."""
    c, uid = _make_composition_with_balance(user_id=400, balance_kopecks=2000_00)
    from app.application.interfaces import SubscriptionSnapshot

    # Set subscription active until 2026-06-15 (still in the future relative to test runtime)
    existing_until = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=existing_until, plan_id="1m", device_count=5,
    )))

    update = _callback_update(user_id=400, callback_data="pay_balance:1m:5")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "реферального баланса" in pkg.message_text

    snap = _run(c.snapshots.get_for_user(uid))
    assert snap is not None
    # Should extend from 2026-06-15 by 1 month → 2026-07-15
    assert snap.active_until_utc is not None
    assert snap.active_until_utc.year == 2026
    assert snap.active_until_utc.month == 7
    assert snap.active_until_utc.day == 15


def test_balance_payment_new_subscription_starts_from_now():
    """If no active subscription, active_until should be now + duration."""
    c, uid = _make_composition_with_balance(user_id=401, balance_kopecks=500_00)
    # No existing subscription
    update = _callback_update(user_id=401, callback_data="pay_balance:1m:5")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "реферального баланса" in pkg.message_text

    snap = _run(c.snapshots.get_for_user(uid))
    assert snap is not None
    assert snap.active_until_utc is not None
    # Should be approximately 1 month from now
    now = datetime.now(UTC)
    delta = snap.active_until_utc - now
    assert 25 * 86400 < delta.total_seconds() < 32 * 86400


def test_balance_payment_extends_3m():
    """3-month renewal extends from current active_until."""
    c, uid = _make_composition_with_balance(user_id=402, balance_kopecks=2000_00)
    from app.application.interfaces import SubscriptionSnapshot

    existing_until = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)
    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=existing_until, plan_id="1m", device_count=5,
    )))

    update = _callback_update(user_id=402, callback_data="pay_balance:3m:5")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "реферального баланса" in pkg.message_text

    snap = _run(c.snapshots.get_for_user(uid))
    assert snap.active_until_utc.year == 2026
    assert snap.active_until_utc.month == 9
    assert snap.active_until_utc.day == 15


def test_settings_shows_tariff_devices_expiry():
    """Settings page should display tariff name, device count, and expiry date."""
    c, uid = _make_composition_with_balance(user_id=500, balance_kopecks=0)
    from app.application.interfaces import SubscriptionSnapshot

    _run(c.snapshots.upsert_state(SubscriptionSnapshot(
        internal_user_id=uid, state_label="active",
        active_until_utc=_FUTURE_DATE, plan_id="3m", device_count=7,
    )))

    update = _callback_update(user_id=500, callback_data="subscription_settings")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "3 месяца" in pkg.message_text
    assert "7" in pkg.message_text
    assert "2099" in pkg.message_text
    assert "Настройки подписки" in pkg.message_text


def test_settings_no_subscription():
    """Settings page without active subscription."""
    c, _ = _make_composition_with_balance(user_id=501, balance_kopecks=0)
    update = _callback_update(user_id=501, callback_data="subscription_settings")
    pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c))
    assert "нет активной подписки" in pkg.message_text
