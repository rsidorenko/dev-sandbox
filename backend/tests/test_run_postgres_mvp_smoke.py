"""Unit tests for PostgreSQL MVP smoke helper script."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_postgres_mvp_smoke.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_postgres_mvp_smoke", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fail_fast_without_database_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        script.main()

    out = capsys.readouterr()
    assert calls == []
    assert "DATABASE_URL" not in out.out
    assert "DATABASE_URL" not in out.err


def test_fail_fast_with_empty_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("DATABASE_URL", "   ")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        script.main()


def test_runs_seven_commands_in_order_and_sets_expected_env(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    raw_db_url = "postgresql://user:secret@localhost:5432/mvpdb"
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("DATABASE_URL", raw_db_url)
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    recorded_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script.main()

    assert len(recorded_calls) == 8
    assert recorded_calls[0][0][0] == ["python", "-m", "app.persistence"]
    assert recorded_calls[1][0][0] == ["python", "scripts/run_slice1_retention_dry_run.py"]
    assert recorded_calls[2][0][0] == ["python", "scripts/check_operator_billing_ingest_apply_e2e.py"]
    assert recorded_calls[3][0][0] == ["python", "scripts/check_postgres_mvp_access_fulfillment_e2e.py"]
    assert recorded_calls[4][0][0] == ["python", "scripts/check_customer_journey_e2e.py"]
    assert recorded_calls[5][0][0] == ["python", "scripts/reconcile_expired_access.py"]
    assert recorded_calls[6][0][0] == ["python", "scripts/check_reconcile_health.py"]
    assert recorded_calls[7][0][0] == [
        "pytest",
        "-q",
        "tests/test_postgres_slice1_process_env_async.py",
        "tests/test_postgres_migration_ledger_integration.py",
    ]
    assert recorded_calls[0][1]["check"] is True
    assert recorded_calls[1][1]["check"] is True
    assert recorded_calls[2][1]["check"] is True
    assert recorded_calls[3][1]["check"] is True
    assert recorded_calls[4][1]["check"] is True
    assert recorded_calls[5][1]["check"] is True
    assert recorded_calls[6][1]["check"] is True
    assert recorded_calls[7][1]["check"] is True

    env_first = recorded_calls[0][1]["env"]
    env_second = recorded_calls[1][1]["env"]
    env_third = recorded_calls[2][1]["env"]
    env_fourth = recorded_calls[3][1]["env"]
    env_fifth = recorded_calls[4][1]["env"]
    env_sixth = recorded_calls[5][1]["env"]
    env_seventh = recorded_calls[6][1]["env"]
    env_eighth = recorded_calls[7][1]["env"]
    assert env_first["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert env_second["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert "SLICE1_USE_POSTGRES_REPOS" not in env_third
    assert env_fourth["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert env_fifth["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert env_sixth["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert env_seventh["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert env_eighth["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert env_first["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_first["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_second["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_second["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_third["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_third["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_fourth["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_fourth["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_fifth["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_sixth["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_seventh["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_eighth["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert env_fifth["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_sixth["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_seventh["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_eighth["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert env_first["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_first["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_first["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert env_second["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_second["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_second["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert "ISSUANCE_OPERATOR_ENABLE" not in env_third
    assert "TELEGRAM_ACCESS_RESEND_ENABLE" not in env_third
    assert "ADM02_ENSURE_ACCESS_ENABLE" not in env_third
    assert env_fourth["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_fourth["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_fourth["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert env_fifth["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_fifth["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_fifth["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert env_sixth["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_seventh["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_eighth["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert env_sixth["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_seventh["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_eighth["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert env_sixth["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert env_seventh["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert env_eighth["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert env_first["BOT_TOKEN"] == "1234567890tok"
    assert env_second["BOT_TOKEN"] == "1234567890tok"
    assert env_third["BOT_TOKEN"] == "1234567890tok"
    assert env_fourth["BOT_TOKEN"] == "1234567890tok"
    assert env_fifth["BOT_TOKEN"] == "1234567890tok"
    assert env_sixth["BOT_TOKEN"] == "1234567890tok"
    assert env_seventh["BOT_TOKEN"] == "1234567890tok"
    assert env_eighth["BOT_TOKEN"] == "1234567890tok"
    assert env_first["DATABASE_URL"] == raw_db_url
    assert env_third["DATABASE_URL"] == raw_db_url
    assert env_sixth["DATABASE_URL"] == raw_db_url
    assert env_seventh["DATABASE_URL"] == raw_db_url
    assert env_eighth["DATABASE_URL"] == raw_db_url
    assert env_first["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_second["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_third["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_fourth["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_fifth["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_sixth["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_seventh["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_eighth["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert env_first["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_second["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_third["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_fourth["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_fifth["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_sixth["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_seventh["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    assert env_eighth["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"
    reconcile_calls = [
        call for call in recorded_calls if call[0][0] == ["python", "scripts/reconcile_expired_access.py"]
    ]
    assert len(reconcile_calls) == 1
    assert reconcile_calls[0][1]["check"] is True


def test_build_child_env_adds_only_contract_opt_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/mvpdb")
    monkeypatch.setenv("BOT_TOKEN", "already-set-token")
    # CI may pre-set some of these env keys for other steps; clear them so the
    # contract assertion is deterministic and verifies helper behavior.
    for key in (
        "ADM02_ENSURE_ACCESS_ENABLE",
        "ACCESS_RECONCILE_MAX_INTERVAL_SECONDS",
        "BILLING_NORMALIZED_INGEST_ENABLE",
        "BILLING_SUBSCRIPTION_APPLY_ENABLE",
        "ISSUANCE_OPERATOR_ENABLE",
        "SLICE1_USE_POSTGRES_REPOS",
        "SUBSCRIPTION_DEFAULT_PERIOD_DAYS",
        "TELEGRAM_ACCESS_RESEND_ENABLE",
    ):
        monkeypatch.delenv(key, raising=False)

    base_env = dict(script.os.environ)
    child_env = script._build_child_env()
    changed_keys = {key for key, value in child_env.items() if base_env.get(key) != value}

    assert changed_keys == {
        "ADM02_ENSURE_ACCESS_ENABLE",
        "ACCESS_RECONCILE_MAX_INTERVAL_SECONDS",
        "BILLING_NORMALIZED_INGEST_ENABLE",
        "BILLING_SUBSCRIPTION_APPLY_ENABLE",
        "ISSUANCE_OPERATOR_ENABLE",
        "SLICE1_USE_POSTGRES_REPOS",
        "SUBSCRIPTION_DEFAULT_PERIOD_DAYS",
        "TELEGRAM_ACCESS_RESEND_ENABLE",
    }
    assert child_env["BILLING_NORMALIZED_INGEST_ENABLE"] == "1"
    assert child_env["BILLING_SUBSCRIPTION_APPLY_ENABLE"] == "1"
    assert child_env["ISSUANCE_OPERATOR_ENABLE"] == "1"
    assert child_env["TELEGRAM_ACCESS_RESEND_ENABLE"] == "1"
    assert child_env["ADM02_ENSURE_ACCESS_ENABLE"] == "1"
    assert child_env["ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"] == "3600"
    assert child_env["SLICE1_USE_POSTGRES_REPOS"] == "1"
    assert child_env["SUBSCRIPTION_DEFAULT_PERIOD_DAYS"] == "30"


def test_preserves_existing_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/mvpdb")
    monkeypatch.setenv("BOT_TOKEN", "already-set-token")

    recorded_envs: list[dict[str, str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_envs.append(kwargs["env"])
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script.main()

    assert len(recorded_envs) == 8
    assert recorded_envs[0]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[1]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[2]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[3]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[4]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[5]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[6]["BOT_TOKEN"] == "already-set-token"
    assert recorded_envs[7]["BOT_TOKEN"] == "already-set-token"


def test_raw_database_url_not_exposed_in_helper_error(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    raw_db_url = "postgresql://user:ultrasecret@localhost:5432/mvpdb"
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("DATABASE_URL", raw_db_url)

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=args[0])

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        script.main()

    assert raw_db_url not in str(exc_info.value)


def test_fail_fast_without_opt_in_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    raw_db_url = "postgresql://user:ultrasecret@localhost:5432/mvpdb"
    monkeypatch.delenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", raising=False)
    monkeypatch.setenv("DATABASE_URL", raw_db_url)

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(
        RuntimeError,
        match=("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS must be explicitly set for isolated/dev DB smoke runs"),
    ) as exc_info:
        script.main()

    out = capsys.readouterr()
    assert calls == []
    assert raw_db_url not in str(exc_info.value)
    assert raw_db_url not in out.out
    assert raw_db_url not in out.err


@pytest.mark.parametrize("falsey_value", ["", "0", "false", "no", "random"])
def test_fail_fast_with_falsey_opt_in_values(monkeypatch: pytest.MonkeyPatch, falsey_value: str) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/mvpdb")
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", falsey_value)

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(
        RuntimeError,
        match=("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS must be explicitly set for isolated/dev DB smoke runs"),
    ):
        script.main()

    assert calls == []


@pytest.mark.parametrize("truthy_value", [" true ", "yes"])
def test_truthy_opt_in_values_allow_run(monkeypatch: pytest.MonkeyPatch, truthy_value: str) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/mvpdb")
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", truthy_value)

    recorded_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script.main()

    assert len(recorded_calls) == 8
    assert recorded_calls[0][0][0] == ["python", "-m", "app.persistence"]
    assert recorded_calls[1][0][0] == ["python", "scripts/run_slice1_retention_dry_run.py"]
    assert recorded_calls[2][0][0] == ["python", "scripts/check_operator_billing_ingest_apply_e2e.py"]
    assert recorded_calls[3][0][0] == ["python", "scripts/check_postgres_mvp_access_fulfillment_e2e.py"]
    assert recorded_calls[4][0][0] == ["python", "scripts/check_customer_journey_e2e.py"]
    assert recorded_calls[5][0][0] == ["python", "scripts/reconcile_expired_access.py"]
    assert recorded_calls[6][0][0] == ["python", "scripts/check_reconcile_health.py"]
    assert recorded_calls[7][0][0] == [
        "pytest",
        "-q",
        "tests/test_postgres_slice1_process_env_async.py",
        "tests/test_postgres_migration_ledger_integration.py",
    ]
