"""Tests for :mod:`app.runtime.telegram_webhook_main` ASGI entrypoint (no network)."""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from starlette.testclient import TestClient

import app.runtime.polling as polling_mod
from app.runtime import telegram_webhook_main as webhook_main_mod
from app.security.config import ConfigurationError


def _reload_webhook_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    importlib.reload(webhook_main_mod)


def _synthetic_ascii_secret() -> str:
    return "".join(chr(97 + (i % 6)) for i in range(40))


def _synthetic_update_mapping() -> dict[str, Any]:
    sid = 901_001
    return {
        "update_id": sid,
        "message": {
            "message_id": 1,
            "from": {"id": sid, "is_bot": False, "first_name": "U"},
            "chat": {"id": sid, "type": "private"},
            "text": "/help",
        },
    }


def test_disabled_app_returns_503_without_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
        other = client.get("/any/path")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "disabled"}
    assert other.status_code == 503
    assert other.json() == {"ok": False, "error": "webhook_http_disabled"}


def test_disabled_non_health_emits_redacted_disabled_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with patch(
        "app.runtime.telegram_webhook_ingress_telemetry.StructuredLoggingTelegramWebhookIngressTelemetry.emit_decision",
        new_callable=AsyncMock,
    ) as emit:
        app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
        with TestClient(app) as client:
            response = client.get("/anything")
    assert response.status_code == 503
    assert emit.await_count == 1
    event = emit.await_args.args[0]
    assert event.decision == "disabled"
    assert event.reason_bucket == "webhook_disabled"
    assert event.path_bucket == "other"
    assert event.principal_marker == "telegram_webhook_redacted"


def test_build_raises_when_production_webhook_enabled_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    with pytest.raises(ConfigurationError):
        webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()


def test_build_raises_when_production_webhook_enabled_without_durable_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", _synthetic_ascii_secret())
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.delenv("SLICE1_USE_POSTGRES_REPOS", raising=False)
    with pytest.raises(ConfigurationError, match="SLICE1_USE_POSTGRES_REPOS"):
        webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()


def test_build_raises_when_local_webhook_enabled_without_secret_and_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    with pytest.raises(ConfigurationError):
        webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()


def test_build_allows_local_webhook_enabled_without_secret_only_with_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    with TestClient(app) as client:
        ready = client.get("/readyz")
        r = client.post(
            "/telegram/webhook",
            content=json.dumps(_synthetic_update_mapping()).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}
    assert r.status_code == 200


def test_enabled_readyz_returns_503_when_dependency_checker_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    async def _failing_checker() -> bool:
        return False

    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env(
        dependency_readiness_check=_failing_checker
    )
    with TestClient(app) as client:
        ready = client.get("/readyz")
        health = client.get("/healthz")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_readyz_not_ready_emits_redacted_not_ready_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    async def _failing_checker() -> bool:
        return False

    with patch(
        "app.runtime.telegram_webhook_ingress_telemetry.StructuredLoggingTelegramWebhookIngressTelemetry.emit_decision",
        new_callable=AsyncMock,
    ) as emit:
        app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env(
            dependency_readiness_check=_failing_checker
        )
        with TestClient(app) as client:
            response = client.get("/readyz")

    assert response.status_code == 503
    assert emit.await_count == 1
    event = emit.await_args.args[0]
    assert event.decision == "not_ready"
    assert event.reason_bucket == "readiness_failed"
    assert event.path_bucket == "readyz"


def test_enabled_readyz_returns_503_when_dependency_checker_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    async def _raising_checker() -> bool:
        raise RuntimeError("internal_failure")

    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env(
        dependency_readiness_check=_raising_checker
    )
    with TestClient(app) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}


def test_readyz_telemetry_failure_does_not_change_response(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    async def _failing_checker() -> bool:
        return False

    failing_emit = AsyncMock(side_effect=RuntimeError("telemetry failure"))
    with patch(
        "app.runtime.telegram_webhook_ingress_telemetry.StructuredLoggingTelegramWebhookIngressTelemetry.emit_decision",
        new=failing_emit,
    ):
        app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env(
            dependency_readiness_check=_failing_checker
        )
        with TestClient(app) as client:
            ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}


def test_enabled_app_dispatches_with_valid_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()

    called: list[int] = []
    _orig = polling_mod.handle_slice1_telegram_update_to_runtime_action

    async def _track(*args: object, **kwargs: object) -> object:
        called.append(1)
        return await _orig(*args, **kwargs)

    with (
        TestClient(app) as client,
        patch.object(
            polling_mod,
            "handle_slice1_telegram_update_to_runtime_action",
            side_effect=_track,
        ),
    ):
        r = client.post(
            "/telegram/webhook",
            content=json.dumps(_synthetic_update_mapping()).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-telegram-bot-api-secret-token": secret,
            },
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(called) == 1


def test_enabled_webhook_rejects_malformed_update_id_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    mock_handle = AsyncMock()
    with TestClient(app) as client:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = client.post(
                "/telegram/webhook",
                content=json.dumps({"update_id": "bad", "message": _synthetic_update_mapping()["message"]}).encode(
                    "utf-8"
                ),
                headers={
                    "content-type": "application/json",
                    "x-telegram-bot-api-secret-token": secret,
                },
            )
    assert r.status_code == 400
    assert r.json() == {"ok": False, "error": "invalid_update_id"}
    mock_handle.assert_not_called()


def test_enabled_unauthorized_does_not_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    mock_handle = AsyncMock()
    with TestClient(app) as client:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = client.post(
                "/telegram/webhook",
                headers={"content-type": "application/json"},
            )
    assert r.status_code == 401
    mock_handle.assert_not_called()


def test_healthz_does_not_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    mock_handle = AsyncMock()
    with TestClient(app) as client:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    mock_handle.assert_not_called()


def test_readyz_does_not_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    mock_handle = AsyncMock()
    with TestClient(app) as client:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    mock_handle.assert_not_called()


def test_readyz_failure_response_has_no_forbidden_fragments(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")

    async def _raising_checker() -> bool:
        raise RuntimeError("db_down")

    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env(
        dependency_readiness_check=_raising_checker
    )
    with TestClient(app) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    blob = (r.text + str(r.headers)).lower()
    assert secret.lower() not in blob
    for frag in (
        "database_url",
        "postgres://",
        "postgresql://",
        "bearer ",
        "private key",
        "begin ",
        "token=",
        "vpn://",
        "internal_failure",
        "db_down",
    ):
        assert frag not in blob


def test_response_has_no_secret_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    with TestClient(app) as client:
        unauthorized = client.post("/telegram/webhook", headers={"content-type": "application/json"})
        health = client.get("/healthz")
        ready = client.get("/readyz")
    blob = (unauthorized.text + health.text + ready.text).lower()
    assert secret.lower() not in blob


def test_fulfillment_route_enabled_rejects_unsigned_request(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _synthetic_ascii_secret()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setenv("BOT_TOKEN", "1234567890tok")
    monkeypatch.setenv("PAYMENT_FULFILLMENT_HTTP_ENABLE", "1")
    monkeypatch.setenv("PAYMENT_FULFILLMENT_WEBHOOK_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db?sslmode=disable")

    class _FakePool:
        async def close(self) -> None:
            return None

    async def _fake_create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _FakePool()

    monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)
    app = webhook_main_mod.build_slice1_telegram_webhook_asgi_application_from_env()
    with TestClient(app) as client:
        r = client.post("/billing/fulfillment/webhook", content=b"{}")
    assert r.status_code == 401


def test_module_app_attribute_after_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", raising=False)
    _reload_webhook_main(monkeypatch)
    assert hasattr(webhook_main_mod, "app")
    assert webhook_main_mod.app is not None
