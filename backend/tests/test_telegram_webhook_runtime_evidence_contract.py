"""Locked evidence: webhook ASGI entrypoint, smoke scope, polling independence (static + light runtime checks)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

import app.runtime.polling as polling_mod
from app.runtime.telegram_webhook_ingress import load_telegram_webhook_ingress_settings_from_env
from app.runtime.telegram_webhook_main import build_slice1_telegram_webhook_asgi_application_from_env
from app.security.config import ConfigurationError


def _repo_backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_text(rel: str) -> str:
    return (_repo_backend_dir() / rel).read_text(encoding="utf-8")


def test_webhook_main_defines_uvicorn_app_export() -> None:
    src = _read_text("src/app/runtime/telegram_webhook_main.py")
    assert "build_slice1_telegram_webhook_asgi_application_from_env" in src
    assert "\napp:" in src or src.strip().startswith("app:") or "app: Starlette" in src
    assert "create_slice1_telegram_webhook_starlette_app" in src
    assert 'Route("/healthz"' in src
    assert 'Route("/readyz"' in src
    assert "telegram_webhook_ingress_decision" in src


def test_postgres_mvp_smoke_script_does_not_start_webhook_asgi() -> None:
    body = _read_text("scripts/run_postgres_mvp_smoke.py")
    lowered = body.lower()
    assert "telegram_webhook_main" not in lowered
    assert "uvicorn" not in lowered


def test_raw_runtime_app_tests_do_not_require_webhook_secret_env_name() -> None:
    raw_tests = _read_text("tests/test_runtime_telegram_httpx_raw_app.py")
    assert "TELEGRAM_WEBHOOK_SECRET_TOKEN" not in raw_tests


def test_polling_runtime_does_not_depend_on_webhook_telemetry_module() -> None:
    polling_runtime = _read_text("src/app/runtime/raw_polling.py")
    assert "telegram_webhook_ingress_telemetry" not in polling_runtime


def test_production_like_requires_secret_when_http_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="TELEGRAM_WEBHOOK_SECRET_TOKEN"):
        load_telegram_webhook_ingress_settings_from_env(app_env="staging")


def test_local_secretless_requires_explicit_insecure_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL", raising=False)
    with pytest.raises(ConfigurationError, match="TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL"):
        load_telegram_webhook_ingress_settings_from_env(app_env="development")


def test_local_secretless_allowed_with_explicit_insecure_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL", "1")
    settings = load_telegram_webhook_ingress_settings_from_env(app_env="development")
    assert settings is not None
    assert settings.expected_secret is None


def test_disabled_health_and_ready_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    app = build_slice1_telegram_webhook_asgi_application_from_env()
    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "disabled"}


def test_enabled_readyz_dependency_semantics_and_no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "".join(chr(97 + (i % 6)) for i in range(40))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    async def _not_ready_checker() -> bool:
        return False

    app = build_slice1_telegram_webhook_asgi_application_from_env(dependency_readiness_check=_not_ready_checker)
    mock_handle = AsyncMock()
    with TestClient(app) as client:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            health = client.get("/healthz")
            ready = client.get("/readyz")
    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}
    mock_handle.assert_not_called()


def test_unauthorized_path_emits_redacted_telemetry_and_no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "".join(chr(97 + (i % 6)) for i in range(40))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    app = build_slice1_telegram_webhook_asgi_application_from_env()
    mock_handle = AsyncMock()
    with (
        patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle),
        patch(
            "app.runtime.telegram_webhook_ingress_telemetry.StructuredLoggingTelegramWebhookIngressTelemetry.emit_decision",
            new_callable=AsyncMock,
        ) as emit,
        TestClient(app) as client,
    ):
        response = client.post("/telegram/webhook", headers={"content-type": "application/json"})

    assert response.status_code == 401
    mock_handle.assert_not_called()
    assert emit.await_count == 1
    event = emit.await_args.args[0]
    assert event.decision == "unauthorized"
    assert event.principal_marker == "telegram_webhook_redacted"
    rendered = repr(event)
    assert secret not in rendered
    assert "internal_user_id" not in rendered


def test_telemetry_best_effort_does_not_break_unauthorized_http_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "".join(chr(97 + (i % 6)) for i in range(40))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    failing_emit = AsyncMock(side_effect=RuntimeError("telemetry sink down"))
    with patch(
        "app.runtime.telegram_webhook_ingress_telemetry.StructuredLoggingTelegramWebhookIngressTelemetry.emit_decision",
        new=failing_emit,
    ):
        app = build_slice1_telegram_webhook_asgi_application_from_env()
        with TestClient(app) as client:
            response = client.post("/telegram/webhook", headers={"content-type": "application/json"})

    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "unauthorized"}
