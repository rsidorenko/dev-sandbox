"""Unit tests for UC-05 operator entrypoint (mocks, no real DB)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.apply_billing_subscription import ApplyAcceptedBillingFactResult
from app.application.billing_subscription_apply_main import (
    BILLING_SUBSCRIPTION_APPLY_ENABLE,
    async_main,
    async_run_apply,
)
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.persistence.postgres_billing_subscription_apply import UC05PostgresApplyResult
from app.shared.types import OperationOutcomeCategory


def _ok_apply(
    *,
    op: OperationOutcomeCategory = OperationOutcomeCategory.SUCCESS,
    apply_out: BillingSubscriptionApplyOutcome = BillingSubscriptionApplyOutcome.ACTIVE_APPLIED,
    idem: bool = False,
) -> ApplyAcceptedBillingFactResult:
    return ApplyAcceptedBillingFactResult(
        operation_outcome=op,
        idempotent_replay=idem,
        apply_outcome=apply_out,
    )


@pytest.mark.asyncio
async def test_no_opt_in_does_not_call_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m:
        monkeypatch.delenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, raising=False)
        code = await async_main(["--internal-fact-ref", "ref-ok-1"])
        assert code == 1
        m.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_internal_fact_ref_no_db_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
    monkeypatch.setenv("BOT_TOKEN", "0" * 20)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:badtoken@192.0.2.0:5432/secretname")
    monkeypatch.setenv("APP_ENV", "test")
    with patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m:
        code = await async_main(["--internal-fact-ref", "has space"])
    assert code == 1
    m.assert_not_awaited()
    err = capsys.readouterr().err
    assert "secretname" not in err
    assert "badtoken" not in err
    assert "192.0.2.0" not in err
    assert "has space" not in err
    assert "failed category=validation" in err


@pytest.mark.asyncio
async def test_success_one_summary_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m:
        m.return_value = _ok_apply()
        monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
        monkeypatch.setenv("BOT_TOKEN", "0" * 20)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
        monkeypatch.setenv("APP_ENV", "test")
        code = await async_main(["--internal-fact-ref", "a.b-c:ref1"])
    assert code == 0
    m.assert_awaited_once()
    out = capsys.readouterr()
    line = out.out.strip()
    assert line.count("\n") == 0
    assert "billing_subscription_apply: ok" in line
    assert "internal_fact_ref=a.b-c:ref1" in line
    assert "outcome=success" in line
    assert "state=active_applied" in line


@pytest.mark.asyncio
async def test_idempotent_noop_exits_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m:
        m.return_value = _ok_apply(
            op=OperationOutcomeCategory.IDEMPOTENT_NOOP,
            idem=True,
        )
        monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
        monkeypatch.setenv("BOT_TOKEN", "0" * 20)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
        monkeypatch.setenv("APP_ENV", "test")
        code = await async_main(["--internal-fact-ref", "idem-ref"])
    assert code == 0
    out = capsys.readouterr().out
    assert "outcome=idempotent_noop" in out
    assert "state=active_applied" in out


@pytest.mark.asyncio
async def test_not_found_safe_stderr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m:
        m.return_value = ApplyAcceptedBillingFactResult(
            operation_outcome=OperationOutcomeCategory.NOT_FOUND,
            idempotent_replay=False,
            apply_outcome=None,
        )
        monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
        monkeypatch.setenv("BOT_TOKEN", "0" * 20)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
        monkeypatch.setenv("APP_ENV", "test")
        code = await async_main(["--internal-fact-ref", "nope-ref"])
    assert code == 1
    cap = capsys.readouterr()
    out, err = cap.out, cap.err
    assert "billing_subscription_apply: ok" not in out
    assert "failed category=not_found" in err
    assert "nope" not in err


@pytest.mark.asyncio
async def test_config_failure_no_dsn_in_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
    monkeypatch.setenv("BOT_TOKEN", "0" * 20)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m:
        code = await async_main(["--internal-fact-ref", "r1"])
    assert code == 1
    m.assert_not_awaited()
    cap0 = capsys.readouterr()
    _out, err = cap0.out, cap0.err
    assert "sensitive" not in err
    assert "postgresql" not in err
    assert "failed category=config" in err
    assert _out == "" or "billing_subscription_apply: ok" not in _out


@pytest.mark.asyncio
async def test_ok_stdout_does_not_echo_dsn(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    dsn = "postgresql://sensitiveuser:sensitive@192.0.2.0:9/x"
    monkeypatch.setenv(BILLING_SUBSCRIPTION_APPLY_ENABLE, "1")
    monkeypatch.setenv("BOT_TOKEN", "0" * 20)
    monkeypatch.setenv("APP_ENV", "test")
    with (
        patch("app.application.billing_subscription_apply_main.load_runtime_config") as lc,
        patch("app.application.billing_subscription_apply_main.async_run_apply", new_callable=AsyncMock) as m,
    ):
        lc.return_value = MagicMock(database_url=dsn)
        m.return_value = _ok_apply()
        code = await async_main(["--internal-fact-ref", "r-safe-1"])
    assert code == 0
    out, err = capsys.readouterr()
    assert dsn not in out
    assert dsn not in err


@pytest.mark.asyncio
async def test_async_run_apply_uses_postgres_atomic_uc05() -> None:
    """Wiring: :class:`PostgresAtomicUC05SubscriptionApply` per open pool, then apply_by_internal_fact_ref."""
    pool = MagicMock()
    pool.close = AsyncMock()

    async def fake_open(_dsn: str) -> object:
        return pool

    with patch("app.application.billing_subscription_apply_main.PostgresAtomicUC05SubscriptionApply") as c_atomic:
        inst = c_atomic.return_value
        inst.apply_by_internal_fact_ref = AsyncMock(
            return_value=UC05PostgresApplyResult(
                operation_outcome=OperationOutcomeCategory.SUCCESS,
                idempotent_replay=False,
                apply_outcome=BillingSubscriptionApplyOutcome.ACTIVE_APPLIED,
            )
        )
        r = await async_run_apply("wire-ref-1", dsn="postgresql://u:p@127.0.0.1:5432/w", open_pool=fake_open)
        c_atomic.assert_called_once_with(pool)
        inst.apply_by_internal_fact_ref.assert_awaited_once_with("wire-ref-1")
    assert r.apply_outcome is BillingSubscriptionApplyOutcome.ACTIVE_APPLIED
    pool.close.assert_awaited_once()
