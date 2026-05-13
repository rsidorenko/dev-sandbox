"""Unit tests for slice-1 retention dry-run helper script."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.retention_boundary_assertions import assert_no_retention_secret_fragments

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "run_slice1_retention_dry_run.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_slice1_retention_dry_run", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _classify_retention_boundary_exception_for_tests(exc: BaseException) -> str:
    msg = str(exc).lower()
    if isinstance(exc, RuntimeError) and "database_url is required" in msg:
        return "config_error"
    if isinstance(exc, subprocess.CalledProcessError):
        return "dependency_error"
    return "unexpected_error"


def test_fail_fast_without_database_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()
    monkeypatch.delenv("DATABASE_URL", raising=False)

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="DATABASE_URL is required") as exc_info:
        script.main()

    out = capsys.readouterr()
    assert calls == []
    assert _classify_retention_boundary_exception_for_tests(exc_info.value) == "config_error"
    assert "DATABASE_URL" not in out.out
    assert "DATABASE_URL" not in out.err
    assert out.out == ""
    assert out.err == ""


def test_fail_fast_with_whitespace_only_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "   ")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required") as exc_info:
        script.main()

    captured = capsys.readouterr()
    assert _classify_retention_boundary_exception_for_tests(exc_info.value) == "config_error"
    assert captured.out == ""
    assert captured.err == ""


def test_runs_single_subprocess_with_expected_argv_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    raw_db_url = "postgresql://user:secret@localhost:5432/retentiondb"
    monkeypatch.setenv("DATABASE_URL", raw_db_url)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLICE1_RETENTION_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SLICE1_RETENTION_BATCH_LIMIT", raising=False)
    monkeypatch.delenv("SLICE1_RETENTION_MAX_ROUNDS", raising=False)

    recorded_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script.main()

    assert len(recorded_calls) == 1
    assert recorded_calls[0][0][0] == [
        "python",
        "-m",
        "app.persistence.slice1_retention_manual_cleanup_main",
    ]
    assert recorded_calls[0][1]["check"] is True
    assert recorded_calls[0][1]["cwd"] == _BACKEND_DIR

    env = recorded_calls[0][1]["env"]
    assert env["SLICE1_RETENTION_DRY_RUN"] == "1"
    assert env["BOT_TOKEN"] == "1234567890tok"
    assert env["SLICE1_RETENTION_TTL_SECONDS"] == "86400"
    assert env["SLICE1_RETENTION_BATCH_LIMIT"] == "100"
    assert env["SLICE1_RETENTION_MAX_ROUNDS"] == "5"
    assert env["DATABASE_URL"] == raw_db_url


def test_preserves_existing_bot_token_and_retention_env(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/retentiondb")
    monkeypatch.setenv("BOT_TOKEN", "already-set-token-12345")
    monkeypatch.setenv("SLICE1_RETENTION_TTL_SECONDS", "3600")
    monkeypatch.setenv("SLICE1_RETENTION_BATCH_LIMIT", "50")
    monkeypatch.setenv("SLICE1_RETENTION_MAX_ROUNDS", "10")

    recorded_envs: list[dict[str, str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_envs.append(kwargs["env"])
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script.main()

    assert len(recorded_envs) == 1
    env = recorded_envs[0]
    assert env["SLICE1_RETENTION_DRY_RUN"] == "1"
    assert env["BOT_TOKEN"] == "already-set-token-12345"
    assert env["SLICE1_RETENTION_TTL_SECONDS"] == "3600"
    assert env["SLICE1_RETENTION_BATCH_LIMIT"] == "50"
    assert env["SLICE1_RETENTION_MAX_ROUNDS"] == "10"


def test_forces_dry_run_when_parent_has_slice1_retention_dry_run_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/retentiondb")
    monkeypatch.setenv("SLICE1_RETENTION_DRY_RUN", "0")

    recorded_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    script.main()

    assert len(recorded_calls) == 1
    assert recorded_calls[0][1]["env"]["SLICE1_RETENTION_DRY_RUN"] == "1"


def test_raw_database_url_not_exposed_in_helper_error(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    raw_db_url = "postgresql://user:ultrasecret@localhost:5432/retentiondb"
    monkeypatch.setenv("DATABASE_URL", raw_db_url)

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=args[0])

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        script.main()

    err = str(exc_info.value)
    assert _classify_retention_boundary_exception_for_tests(exc_info.value) == "dependency_error"
    assert raw_db_url not in err
    assert_no_retention_secret_fragments(err)
