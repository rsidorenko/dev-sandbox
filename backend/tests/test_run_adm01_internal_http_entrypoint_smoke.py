"""Tests for check_adm01_internal_http_entrypoint_smoke script (fixed output, no listener env)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_adm01_internal_http_entrypoint_smoke.py"
_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "PRIVATE KEY",
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "check_adm01_internal_http_entrypoint_smoke",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_main_ok_and_fixed_line(capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()
    script.run_adm01_internal_http_entrypoint_smoke = lambda: None
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "adm01_internal_http_entrypoint_smoke: ok"
    assert out.err == ""


def test_disabled_check_failure_returns_fail_and_no_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    def fail_disabled(*, runner=None) -> None:
        _ = runner
        raise RuntimeError("disabled path returned non-zero")

    monkeypatch.setattr(script, "run_adm01_internal_http_entrypoint_smoke", fail_disabled)
    rc = script.main([])
    assert rc == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "adm01_internal_http_entrypoint_smoke: fail"
    for frag in _FORBIDDEN:
        assert frag not in out.err
        assert frag not in out.out


def test_config_error_check_failure_returns_fail_and_no_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    def fail_config(*, runner=None) -> None:
        _ = runner
        raise RuntimeError("config-error path returned zero")

    monkeypatch.setattr(script, "run_adm01_internal_http_entrypoint_smoke", fail_config)

    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "adm01_internal_http_entrypoint_smoke: fail"
    for frag in _FORBIDDEN:
        assert frag not in out.err
        assert frag not in out.out


def test_unexpected_exception_returns_failed_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    def boom() -> None:
        raise ValueError("postgresql://u:secret@h/db")

    monkeypatch.setattr(script, "run_adm01_internal_http_entrypoint_smoke", boom)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "adm01_internal_http_entrypoint_smoke: failed"
    assert "Traceback" not in out.err
    assert "postgresql://" not in out.err


def test_canned_config_error_env_never_sets_valid_listener_inputs() -> None:
    script = _load_script_module()
    env = script._config_error_check_env()
    assert env.get("ADM01_INTERNAL_HTTP_ENABLE") == "1"
    assert "ADM01_INTERNAL_HTTP_ALLOWLIST" not in env
    assert "ADM01_INTERNAL_HTTP_BIND_HOST" not in env
    assert "ADM01_INTERNAL_HTTP_BIND_PORT" not in env


def test_leak_guard_forbidden_fragments_raise_runtime() -> None:
    script = _load_script_module()
    for frag in _FORBIDDEN:
        with pytest.raises(RuntimeError, match="leak guard"):
            script._assert_no_forbidden_output(f"prefix {frag} suffix")
