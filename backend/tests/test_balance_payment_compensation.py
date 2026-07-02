"""Balance-payment VLESS-failure → compensation path (Postgres branch).

Locks in the behavior that became reachable after the `_LOGGER` NameError fix:
when VLESS key creation fails during a balance payment, the deducted balance is
refunded and the subscription reverted (previously the path crashed with
NameError before reaching compensation, leaving the user charged-but-no-keys).

Uses a fake pool (no real DB) so it runs locally. `runtime_facade._get_pool_from_composition`
is monkeypatched to return the fake, which activates the Postgres branch
(`if pool is not None`); the failing provider forces the retry-then-compensate path.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from app.application.bootstrap import build_slice1_composition
from app.bot_transport.runtime_facade import _process_balance_payment
from app.bot_transport.storefront_ui import text_error_generic
from app.shared.test_helpers import run_async as _run

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_BALANCE_KOPECKS = 100_000  # well above any plan price


def _fake_row_for(sql: str):
    """Return a dict-like row shaped for the SQL the Postgres repos run."""
    s = sql.upper()
    if "KEYS_DEACTIVATED_AT, KEYS_DELETED_AT, DEVICE_COUNT" in s:  # snap_check (retry)
        return {"keys_deactivated_at": None, "keys_deleted_at": None, "device_count": 5}
    if "FROM REFERRAL_BALANCES WHERE" in s:  # balance SELECT
        return {"internal_user_id": "u200", "balance_kopecks": _BALANCE_KOPECKS, "updated_at": _NOW}
    if "BALANCE_KOPECKS = BALANCE_KOPECKS -" in s:  # debit UPDATE...RETURNING
        return {"internal_user_id": "u200", "balance_kopecks": _BALANCE_KOPECKS, "updated_at": _NOW}
    if "INSERT INTO REFERRAL_BALANCES" in s:  # credit (incl. compensation refund)
        return {"internal_user_id": "u200", "balance_kopecks": _BALANCE_KOPECKS, "updated_at": _NOW}
    return None  # subscription_snapshots reads (get_for_user) → no prior snapshot


class _FakeConn:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, sql: str, *args: object):
        self._log.append(sql)
        return _fake_row_for(sql)

    async def execute(self, sql: str, *args: object) -> str:
        self._log.append(sql)
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *args: object):
        self._log.append(sql)
        return 0


class _FakePool:
    def __init__(self) -> None:
        self.log: list[str] = []

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield _FakeConn(self.log)

    async def fetchrow(self, sql: str, *args: object):
        self.log.append(sql)
        return _fake_row_for(sql)

    async def execute(self, sql: str, *args: object) -> str:
        self.log.append(sql)
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *args: object):
        self.log.append(sql)
        return 0


class _FailingVlessProvider:
    """Provider whose key creation always fails → triggers compensation."""

    def __init__(self) -> None:
        self.create_calls = 0

    async def create_user(self, **kwargs: object):
        self.create_calls += 1
        raise RuntimeError("panel down")

    async def activate_user(self, **kwargs: object):
        raise RuntimeError("panel down")


def test_balance_payment_refunds_and_reverts_when_vless_fails(monkeypatch) -> None:
    # Skip the 3×2s retry sleeps so the test is instant.
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    composition = build_slice1_composition(bot_username="testbot", vless_provider=_FailingVlessProvider())
    _run(composition.identity.create_if_absent(200))

    pool = _FakePool()
    # Activate the Postgres branch of _process_balance_payment without a real DB.
    monkeypatch.setattr(
        "app.bot_transport.runtime_facade._get_pool_from_composition",
        lambda composition: pool,
    )

    text, _keyboard = _run(_process_balance_payment(composition, 200, plan_id="1m", device_count=5))

    # 1. The user is told it failed (not the success message).
    assert text == text_error_generic()
    # 2. VLESS creation was attempted (and retried).
    assert composition.vless_provider.create_calls == 3
    # 3. Compensation fired: balance refunded (credit) AND subscription reverted.
    logged = " ".join(pool.log).upper()
    assert "INSERT INTO REFERRAL_BALANCES" in logged, "expected refund credit"
    assert "UPDATE SUBSCRIPTION_SNAPSHOTS" in logged and "INACTIVE" in logged, "expected subscription revert"
