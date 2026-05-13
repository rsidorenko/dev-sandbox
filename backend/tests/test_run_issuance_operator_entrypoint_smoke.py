"""Tests for check_issuance_operator_entrypoint_smoke script (fixed output, no DB required)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_issuance_operator_entrypoint_smoke.py"
_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "provider_issuance_ref",
    "PRIVATE KEY",
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "check_issuance_operator_entrypoint_smoke",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_main_ok_and_fixed_line(capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()
    script.run_issuance_operator_entrypoint_smoke = lambda: None
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "issuance_operator_entrypoint_smoke: ok"
    assert out.err == ""


def test_failure_returns_fail_line_no_leak(capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()

    def fail_runner(*, runner=None) -> None:
        _ = runner
        raise RuntimeError("disabled path returned zero")

    script.run_issuance_operator_entrypoint_smoke = fail_runner
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "issuance_operator_entrypoint_smoke: fail"
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


def test_unexpected_exception_returns_failed_no_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()

    def boom() -> None:
        raise ValueError("postgresql://u:secret@h/db")

    script.run_issuance_operator_entrypoint_smoke = boom
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "issuance_operator_entrypoint_smoke: failed"
    assert "Traceback" not in out.err
    assert "postgresql://" not in out.err


def test_config_error_env_intentionally_missing_runtime_config() -> None:
    script = _load_script_module()
    env = script._config_error_check_env()
    assert env.get("ISSUANCE_OPERATOR_ENABLE") == "1"
    assert "BOT_TOKEN" not in env
    assert "DATABASE_URL" not in env


def test_leak_guard_forbidden_fragments_raise_runtime() -> None:
    script = _load_script_module()
    for frag in _FORBIDDEN:
        with pytest.raises(RuntimeError, match="leak guard"):
            script._assert_no_forbidden_output(f"prefix {frag} suffix")
