"""Contract tests for customer journey e2e smoke script output and guards."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_customer_journey_e2e.py"
_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "x-payment-signature",
    "x-payment-timestamp",
    "payment_fulfillment_webhook_secret",
    "telegram_checkout_reference_secret",
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_customer_journey_e2e", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_success_main_outputs_exact_ok_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script_module()

    async def ok_run() -> None:
        return None

    monkeypatch.setattr(script, "run_customer_journey_e2e", ok_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "customer_journey_e2e: ok"
    assert out.err == ""


def test_runtime_error_maps_to_fail_fixed_line_without_leak(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script_module()

    async def fail_run() -> None:
        raise RuntimeError("postgresql://user:secret@localhost/dev")

    monkeypatch.setattr(script, "run_customer_journey_e2e", fail_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip().splitlines() == [
        "customer_journey_e2e: fail",
        "issue_code=runtime_error",
    ]
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.err


def test_unexpected_exception_maps_to_failed_without_traceback_or_leak(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script_module()

    async def boom_run() -> None:
        raise ValueError("x-payment-signature leaked")

    monkeypatch.setattr(script, "run_customer_journey_e2e", boom_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "customer_journey_e2e: failed"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.err


@pytest.mark.parametrize(
    "missing_env",
    [
        "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS",
        "TELEGRAM_ACCESS_RESEND_ENABLE",
        "TELEGRAM_CHECKOUT_REFERENCE_SECRET",
    ],
)
def test_missing_opt_in_main_fails_with_safe_fixed_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_env: str,
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_RESEND_ENABLE", "1")
    monkeypatch.setenv("TELEGRAM_CHECKOUT_REFERENCE_SECRET", "CheckoutRef_Secret_1234567890")
    monkeypatch.delenv(missing_env, raising=False)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip().splitlines() == [
        "customer_journey_e2e: fail",
        "issue_code=required_opt_in_missing",
    ]


def test_script_contains_lifecycle_active_and_expired_path_contract() -> None:
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)" in text
    assert '"period_days": 30' in text
    assert '_assert_contains(active_status.message_text.lower(), "активн")' in text
    assert "active_until_utc=datetime(2020, 1, 1, tzinfo=UTC)" in text
    assert '_assert_contains(expired_status.message_text.lower(), "истекл")' in text
    assert "_assert_contains(expired_access.message_text, \"/renew\")" in text
    assert "expired_resend = await _render_command(command=\"/resend_access\"" in text
    assert "reconciled_rows = await _reconcile_expired_access(pool)" in text
    assert "reconciled_rows_second = await _reconcile_expired_access(pool)" in text
    assert "IssuanceStatePersistence.REVOKED" in text

