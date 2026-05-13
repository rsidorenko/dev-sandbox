"""Unit tests for issuance operator entrypoint (mocks, no real DB/network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.issuance_operator_main import (
    ISSUANCE_OPERATOR_ENABLE,
    async_main,
)
from app.issuance.contracts import IssuanceOutcomeCategory, IssuanceServiceResult

_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "provider_issuance_ref",
)


def _assert_no_forbidden(text: str) -> None:
    upper = text.upper()
    for frag in _FORBIDDEN:
        assert frag.upper() not in upper


def _ok_result(category: IssuanceOutcomeCategory) -> IssuanceServiceResult:
    return IssuanceServiceResult(category=category, safe_ref="issuance-ref:fake:secretish")


def _base_args(action: str = "issue") -> list[str]:
    return [
        action,
        "--internal-user-id",
        "u-1",
        "--access-profile-key",
        "ap-basic",
        "--issue-idempotency-key",
        "ik-1",
    ]


@pytest.mark.asyncio
async def test_disabled_opt_in_fails_without_db_or_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ISSUANCE_OPERATOR_ENABLE, raising=False)
    with (
        patch("app.application.issuance_operator_main.asyncpg.create_pool", new_callable=AsyncMock) as create_pool,
        patch("app.application.issuance_operator_main.FakeIssuanceProvider") as fake_provider,
    ):
        code = await async_main(_base_args())
    assert code == 1
    create_pool.assert_not_awaited()
    fake_provider.assert_not_called()
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "issuance_operator: failed category=opt_in"
    _assert_no_forbidden(out.out + out.err)


@pytest.mark.asyncio
async def test_invalid_args_return_non_zero() -> None:
    code = await async_main([])
    assert code == 1


@pytest.mark.asyncio
async def test_enabled_missing_config_fails_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ISSUANCE_OPERATOR_ENABLE, "1")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch("app.application.issuance_operator_main.asyncpg.create_pool", new_callable=AsyncMock) as create_pool:
        code = await async_main(_base_args())
    assert code == 1
    create_pool.assert_not_awaited()
    out = capsys.readouterr()
    assert "issuance_operator: failed category=config" in out.err
    assert "Traceback" not in out.err
    _assert_no_forbidden(out.out + out.err)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "category", "expected_state", "expected_delivery"),
    (
        ("issue", IssuanceOutcomeCategory.ISSUED, "issued", "none"),
        ("resend", IssuanceOutcomeCategory.DELIVERY_READY, "none", "redacted"),
        ("revoke", IssuanceOutcomeCategory.REVOKED, "revoked", "none"),
    ),
)
async def test_success_safe_stdout_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
    category: IssuanceOutcomeCategory,
    expected_state: str,
    expected_delivery: str,
) -> None:
    monkeypatch.setenv(ISSUANCE_OPERATOR_ENABLE, "yes")
    monkeypatch.setenv("BOT_TOKEN", "x" * 20)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/test")
    monkeypatch.setenv("APP_ENV", "test")
    pool = MagicMock()
    pool.close = AsyncMock()
    service = MagicMock()
    service.execute = AsyncMock(return_value=_ok_result(category))
    with (
        patch("app.application.issuance_operator_main.asyncpg.create_pool", new=AsyncMock(return_value=pool)),
        patch("app.application.issuance_operator_main.IssuanceService", return_value=service),
        patch("app.application.issuance_operator_main.PostgresIssuanceStateRepository"),
        patch("app.application.issuance_operator_main.FakeIssuanceProvider"),
    ):
        code = await async_main(_base_args(action))
    assert code == 0
    service.execute.assert_awaited_once()
    pool.close.assert_awaited_once()
    out = capsys.readouterr()
    line = out.out.strip()
    assert line == (
        "issuance_operator: ok"
        f" action={action}"
        f" outcome={category.value}"
        f" state={expected_state}"
        f" delivery={expected_delivery}"
    )
    assert out.err == ""
    _assert_no_forbidden(out.out + out.err)


@pytest.mark.asyncio
async def test_enabled_dependency_error_maps_fixed_category(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ISSUANCE_OPERATOR_ENABLE, "true")
    monkeypatch.setenv("BOT_TOKEN", "x" * 20)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/test")
    monkeypatch.setenv("APP_ENV", "test")
    with patch(
        "app.application.issuance_operator_main.asyncpg.create_pool",
        new=AsyncMock(side_effect=OSError("postgresql://secret")),
    ):
        code = await async_main(_base_args("issue"))
    assert code == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "issuance_operator: failed category=dependency"
    _assert_no_forbidden(out.out + out.err)


@pytest.mark.asyncio
async def test_invalid_correlation_id_maps_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ISSUANCE_OPERATOR_ENABLE, "1")
    args = _base_args("issue") + ["--correlation-id", "invalid"]
    with patch("app.application.issuance_operator_main.asyncpg.create_pool", new_callable=AsyncMock) as create_pool:
        code = await async_main(args)
    assert code == 1
    create_pool.assert_not_awaited()
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "issuance_operator: failed category=validation"
    _assert_no_forbidden(out.out + out.err)
