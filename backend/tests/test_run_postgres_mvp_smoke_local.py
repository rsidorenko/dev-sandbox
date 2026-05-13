"""Unit tests for local isolated PostgreSQL MVP smoke runner."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _BACKEND_DIR / "scripts" / "run_postgres_mvp_smoke_local.py"
_COMPOSE_PATH = _BACKEND_DIR / "docker-compose.postgres-smoke.yml"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_postgres_mvp_smoke_local", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runs_compose_up_smoke_and_cleanup_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "abcdeffedcba"})())
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_LOCAL_KEEP_ON_FAILURE", "0")

    recorded_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_calls.append((args, kwargs))
        command = args[0]
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55432\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    exit_code = script.main([], runner=fake_run)

    assert exit_code == 0
    assert len(recorded_calls) == 7
    assert recorded_calls[0][0][0] == ["docker", "--version"]
    assert recorded_calls[1][0][0] == ["docker", "compose", "version"]
    assert recorded_calls[2][0][0] == [
        "docker",
        "compose",
        "-p",
        "slice1-smoke-abcdeffedc",
        "-f",
        str(_COMPOSE_PATH),
        "up",
        "-d",
        "postgres",
    ]
    assert recorded_calls[3][0][0] == [
        "docker",
        "compose",
        "-p",
        "slice1-smoke-abcdeffedc",
        "-f",
        str(_COMPOSE_PATH),
        "port",
        "postgres",
        "5432",
    ]
    assert recorded_calls[4][0][0] == [
        "docker",
        "compose",
        "-p",
        "slice1-smoke-abcdeffedc",
        "-f",
        str(_COMPOSE_PATH),
        "exec",
        "-T",
        "postgres",
        "pg_isready",
        "-U",
        "postgres",
        "-d",
        "postgres_smoke_local",
    ]
    assert recorded_calls[5][0][0] == ["python", "scripts/run_postgres_mvp_smoke.py"]
    assert recorded_calls[6][0][0] == [
        "docker",
        "compose",
        "-p",
        "slice1-smoke-abcdeffedc",
        "-f",
        str(_COMPOSE_PATH),
        "down",
        "--volumes",
        "--remove-orphans",
    ]
    child_env = recorded_calls[5][1]["env"]
    assert child_env["SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS"] == "1"
    assert child_env["DATABASE_URL"] == "postgresql://postgres:postgres@127.0.0.1:55432/postgres_smoke_local"


def test_fail_fast_when_docker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if command == ["docker", "--version"]:
            raise FileNotFoundError("docker not found")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(FileNotFoundError):
        script.main([], runner=fake_run)


def test_error_output_does_not_contain_raw_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "1234567890abcdef"})())
    raw_dsn = "postgresql://postgres:postgres@127.0.0.1:54321/postgres_smoke_local"

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["up", "-d", "postgres"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:54321\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        if command == ["python", "scripts/run_postgres_mvp_smoke.py"]:
            raise subprocess.CalledProcessError(2, command)
        if "down" in command:
            return subprocess.CompletedProcess(command, 0, stdout="")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        script.main([], runner=fake_run)

    out = capsys.readouterr()
    assert raw_dsn not in out.out
    assert raw_dsn not in out.err


def test_keep_on_failure_skips_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "feedfacecafe"})())

    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["up", "-d", "postgres"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:54322\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        if command == ["python", "scripts/run_postgres_mvp_smoke.py"]:
            raise subprocess.CalledProcessError(2, command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        script.main(["--keep-on-failure"], runner=fake_run)

    assert any(cmd == ["python", "scripts/run_postgres_mvp_smoke.py"] for cmd in recorded_commands)
    assert not any("down" in cmd for cmd in recorded_commands)


def test_readiness_retries_then_succeeds_before_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "ab12cd34ef56"})())
    monkeypatch.setattr(script.time, "sleep", lambda _: None)

    readiness_attempts = 0
    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal readiness_attempts
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["up", "-d", "postgres"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55433\n")
        if "pg_isready" in command:
            readiness_attempts += 1
            if readiness_attempts < 3:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    exit_code = script.main([], runner=fake_run)

    assert exit_code == 0
    assert readiness_attempts == 3
    assert any(cmd == ["python", "scripts/run_postgres_mvp_smoke.py"] for cmd in recorded_commands)


def test_readiness_timeout_fails_fast_and_cleans_up_without_dsn_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "facefeedbeef"})())
    monkeypatch.setattr(script.time, "sleep", lambda _: None)
    monotonic_tick = {"value": 0.0}

    def fake_monotonic() -> float:
        current = monotonic_tick["value"]
        monotonic_tick["value"] += 10.0
        return current

    monkeypatch.setattr(script.time, "monotonic", fake_monotonic)
    raw_dsn = "postgresql://postgres:postgres@127.0.0.1:55434/postgres_smoke_local"

    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["up", "-d", "postgres"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55434\n")
        if "pg_isready" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="readiness check timed out"):
        script.main([], runner=fake_run)

    out = capsys.readouterr()
    assert raw_dsn not in out.out
    assert raw_dsn not in out.err
    assert not any(cmd == ["python", "scripts/run_postgres_mvp_smoke.py"] for cmd in recorded_commands)
    assert any("down" in cmd for cmd in recorded_commands)


def test_fallback_to_docker_compose_when_docker_compose_subcommand_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "beadbeadbead"})())

    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            raise subprocess.CalledProcessError(1, command)
        if command == ["docker-compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55435\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    exit_code = script.main([], runner=fake_run)

    assert exit_code == 0
    assert recorded_commands[2][:1] == ["docker-compose"]
    assert any(cmd[:1] == ["docker-compose"] and "up" in cmd for cmd in recorded_commands)


@pytest.mark.parametrize("port_output", ["", "not-a-valid-endpoint\n"])
def test_invalid_mapped_port_output_fails_fast_and_cleans_up(monkeypatch: pytest.MonkeyPatch, port_output: str) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "deadc0debeef"})())
    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout=port_output)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        script.main([], runner=fake_run)

    assert not any("pg_isready" in cmd for cmd in recorded_commands)
    assert not any(cmd == ["python", "scripts/run_postgres_mvp_smoke.py"] for cmd in recorded_commands)
    assert any("down" in cmd for cmd in recorded_commands)


def test_cleanup_runs_when_smoke_fails_without_keep_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "ccddeeff0011"})())
    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55436\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        if command == ["python", "scripts/run_postgres_mvp_smoke.py"]:
            raise subprocess.CalledProcessError(2, command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        script.main([], runner=fake_run)

    assert any("down" in cmd for cmd in recorded_commands)


def test_env_flag_keep_on_failure_skips_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "cafebabefeed"})())
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_LOCAL_KEEP_ON_FAILURE", "1")
    recorded_commands: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        recorded_commands.append(command)
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55437\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        if command == ["python", "scripts/run_postgres_mvp_smoke.py"]:
            raise subprocess.CalledProcessError(2, command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        script.main([], runner=fake_run)

    assert any(cmd == ["python", "scripts/run_postgres_mvp_smoke.py"] for cmd in recorded_commands)
    assert not any("down" in cmd for cmd in recorded_commands)


def test_cleanup_failure_is_best_effort_after_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    monkeypatch.setattr(script.uuid, "uuid4", lambda: type("U", (), {"hex": "aa11bb22cc33"})())

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(command, 0, stdout="127.0.0.1:55438\n")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting connections\n")
        if "down" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    exit_code = script.main([], runner=fake_run)

    assert exit_code == 0
    out = capsys.readouterr()
    assert "cleanup failed" in out.out


def test_cli_failure_is_redacted_and_returns_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    raw_dsn = "postgresql://postgres:postgres@127.0.0.1:55439/postgres_smoke_local"

    def fake_main(argv: Any = None) -> int:
        raise RuntimeError(f"canonical smoke failed for {raw_dsn}")

    monkeypatch.setattr(script, "main", fake_main)

    exit_code = script._run_cli([])

    assert exit_code == 1
    out = capsys.readouterr()
    assert "Local Docker smoke gate failed" in out.err
    assert "Action: verify Docker is running" in out.err
    assert "sensitive details redacted" in out.err
    assert raw_dsn not in out.out
    assert raw_dsn not in out.err
