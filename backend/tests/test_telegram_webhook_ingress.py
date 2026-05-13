"""Tests for Telegram HTTP webhook ingress (secret gate; no network)."""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.runtime.polling as polling_mod
from app.application.bootstrap import build_slice1_composition
from app.runtime.raw_polling import TelegramRawPollingClient
from app.runtime.raw_startup import build_slice1_in_memory_raw_runtime_bundle_with_default_bridge
from app.runtime.telegram_webhook_ingress import (
    ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL,
    TELEGRAM_WEBHOOK_SECRET_HEADER,
    TelegramWebhookIngressSettings,
    create_slice1_telegram_webhook_starlette_app,
    load_telegram_webhook_ingress_settings_from_env,
)
from app.runtime.telegram_webhook_ingress_telemetry import (
    TelegramWebhookIngressDecisionEvent,
    TelegramWebhookIngressTelemetry,
)
from app.security.config import ConfigurationError


class _StubRawClient:
    """Minimal raw client: no inbound fetch; outbound stubbed."""

    async def fetch_raw_updates(
        self,
        *,
        limit: int,
        offset: int | None = None,
    ) -> Sequence[object]:
        return ()

    async def send_text_message(
        self,
        chat_id: int,
        text: str,
        *,
        correlation_id: str,
    ) -> int:
        return 1


def _build_runtime():
    client = cast("TelegramRawPollingClient", _StubRawClient())
    bundle = build_slice1_in_memory_raw_runtime_bundle_with_default_bridge(
        client,
        composition=build_slice1_composition(),
    )
    return bundle.runtime


def _secret_for_tests() -> str:
    return "".join(chr(97 + (i % 6)) for i in range(40))


def _private_help_update() -> dict[str, Any]:
    return {
        "update_id": 900011,
        "message": {
            "message_id": 1,
            "from": {"id": 42, "is_bot": False, "first_name": "U"},
            "chat": {"id": 42, "type": "private"},
            "text": "/help",
        },
    }


def _private_get_access_update(*, update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": 42, "is_bot": False, "first_name": "U"},
            "chat": {"id": 42, "type": "private"},
            "text": "/get_access",
        },
    }


class _CaptureIngressTelemetry(TelegramWebhookIngressTelemetry):
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[TelegramWebhookIngressDecisionEvent] = []
        self.fail = fail

    async def emit_decision(self, event: TelegramWebhookIngressDecisionEvent) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("telemetry_sink_failure")


def _forbidden_fragments(secret: str, update: dict[str, Any]) -> tuple[str, ...]:
    update_id = str(update["update_id"])
    user_id = str(update["message"]["from"]["id"])
    return (
        secret,
        "TELEGRAM_WEBHOOK_SECRET_TOKEN=",
        "TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL=",
        "DATABASE_URL",
        "postgres://",
        "postgresql://",
        "Bearer ",
        "PRIVATE KEY",
        "BEGIN ",
        "token=",
        "vpn://",
        "provider_issuance_ref",
        "issue_idempotency_key",
        "schema_version",
        "customer_ref",
        "provider_ref",
        "checkout_attempt_id",
        "internal_user_id",
        update_id,
        user_id,
    )


@pytest.mark.asyncio
async def test_valid_secret_dispatches_pipeline() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)

    called: list[int] = []
    _orig = polling_mod.handle_slice1_telegram_update_to_runtime_action

    async def _track(*args: object, **kwargs: object) -> object:
        called.append(1)
        return await _orig(*args, **kwargs)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(
            polling_mod,
            "handle_slice1_telegram_update_to_runtime_action",
            side_effect=_track,
        ):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(_private_help_update()).encode("utf-8"),
                headers={
                    "content-type": "application/json",
                    TELEGRAM_WEBHOOK_SECRET_HEADER: secret,
                },
            )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(called) == 1


@pytest.mark.asyncio
async def test_missing_secret_header_rejects_and_pipeline_not_called() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)

    mock_handle = AsyncMock()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(_private_help_update()).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
    assert r.status_code == 401
    assert r.json() == {"ok": False, "error": "unauthorized"}
    mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_secret_header_rejects_and_pipeline_not_called() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)

    mock_handle = AsyncMock()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(_private_help_update()).encode("utf-8"),
                headers={
                    "content-type": "application/json",
                    TELEGRAM_WEBHOOK_SECRET_HEADER: secret + "x",
                },
            )
    assert r.status_code == 401
    mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_rejected_does_not_touch_dedup_guard() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    composition = runtime._inner._composition
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)

    spy = AsyncMock(wraps=composition.telegram_update_dedup.mark_if_first_seen)
    composition.telegram_update_dedup.mark_if_first_seen = spy  # type: ignore[method-assign]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/telegram/webhook",
            content=json.dumps(_private_help_update()).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 401
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_response_body_has_no_forbidden_fragments() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        for hdr in (None, secret + "bad"):
            headers = {"content-type": "application/json"}
            if hdr is not None:
                headers[TELEGRAM_WEBHOOK_SECRET_HEADER] = hdr
            r = await ac.post(
                "/telegram/webhook",
                content=b"{}",
                headers=headers,
            )
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
            ):
                assert frag not in blob


@pytest.mark.asyncio
async def test_accepted_emits_safe_telemetry_event() -> None:
    secret = _secret_for_tests()
    telemetry = _CaptureIngressTelemetry()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)
    update = _private_help_update()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/telegram/webhook",
            content=json.dumps(update).encode("utf-8"),
            headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
        )

    assert r.status_code == 200
    assert len(telemetry.events) == 1
    event = telemetry.events[0]
    assert event.event_type == "telegram_webhook_ingress_decision"
    assert event.decision == "accepted"
    assert event.reason_bucket == "valid_secret"
    assert event.path_bucket == "telegram_webhook"
    assert event.principal_marker == "telegram_webhook_redacted"
    blob = json.dumps(asdict(event), sort_keys=True).lower()
    for frag in _forbidden_fragments(secret, update):
        assert frag.lower() not in blob


@pytest.mark.asyncio
async def test_missing_secret_emits_unauthorized_telemetry_and_does_not_dispatch() -> None:
    secret = _secret_for_tests()
    telemetry = _CaptureIngressTelemetry()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)
    mock_handle = AsyncMock()
    update = _private_help_update()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(update).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

    assert r.status_code == 401
    mock_handle.assert_not_called()
    assert len(telemetry.events) == 1
    assert telemetry.events[0].decision == "unauthorized"
    assert telemetry.events[0].reason_bucket == "missing_secret_header"


@pytest.mark.asyncio
async def test_wrong_secret_emits_unauthorized_telemetry_and_does_not_dispatch() -> None:
    secret = _secret_for_tests()
    telemetry = _CaptureIngressTelemetry()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)
    mock_handle = AsyncMock()
    update = _private_help_update()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", mock_handle):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(update).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret + "x"},
            )

    assert r.status_code == 401
    mock_handle.assert_not_called()
    assert len(telemetry.events) == 1
    assert telemetry.events[0].decision == "unauthorized"
    assert telemetry.events[0].reason_bucket == "invalid_secret_header"


@pytest.mark.asyncio
async def test_invalid_json_emits_safe_invalid_json_telemetry() -> None:
    secret = _secret_for_tests()
    telemetry = _CaptureIngressTelemetry()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)
    update = _private_help_update()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/telegram/webhook",
            content=b"{",
            headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
        )

    assert r.status_code == 400
    assert r.json() == {"ok": False, "error": "invalid_json"}
    assert len(telemetry.events) == 1
    event = telemetry.events[0]
    assert event.decision == "invalid_json"
    assert event.reason_bucket == "invalid_json"
    blob = json.dumps(asdict(event), sort_keys=True).lower()
    for frag in _forbidden_fragments(secret, update):
        assert frag.lower() not in blob


@pytest.mark.asyncio
async def test_telemetry_sink_failure_does_not_change_http_response() -> None:
    secret = _secret_for_tests()
    telemetry = _CaptureIngressTelemetry(fail=True)
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        unauthorized = await ac.post("/telegram/webhook", headers={"content-type": "application/json"})
        accepted = await ac.post(
            "/telegram/webhook",
            content=json.dumps(_private_help_update()).encode("utf-8"),
            headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
        )

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"ok": False, "error": "unauthorized"}
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True}


@pytest.mark.asyncio
async def test_unauthorized_path_does_not_read_raw_body() -> None:
    secret = _secret_for_tests()
    telemetry = _CaptureIngressTelemetry()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)

    body_spy = AsyncMock(side_effect=AssertionError("request.body must not be called"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("starlette.requests.Request.body", body_spy):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(_private_help_update()).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

    assert r.status_code == 401
    assert telemetry.events
    assert telemetry.events[0].decision == "unauthorized"
    body_spy.assert_not_called()


@pytest.mark.asyncio
async def test_first_mutating_update_dispatches_once_and_writes_dedup() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)
    composition = runtime._composition
    dedup_spy = AsyncMock(wraps=composition.telegram_update_dedup.mark_if_first_seen)
    composition.telegram_update_dedup.mark_if_first_seen = dedup_spy  # type: ignore[method-assign]
    dispatch_spy = AsyncMock()
    update = _private_get_access_update(update_id=900012)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", dispatch_spy):
            r = await ac.post(
                "/telegram/webhook",
                content=json.dumps(update).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
            )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    dedup_spy.assert_awaited_once()
    dispatch_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_mutating_update_is_noop_and_not_dispatched_twice() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)
    dispatch_spy = AsyncMock()
    update = _private_get_access_update(update_id=900013)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", dispatch_spy):
            first = await ac.post(
                "/telegram/webhook",
                content=json.dumps(update).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
            )
            second = await ac.post(
                "/telegram/webhook",
                content=json.dumps(update).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
            )
    assert first.status_code == 200
    assert second.status_code == 200
    assert dispatch_spy.await_count == 1


@pytest.mark.asyncio
async def test_missing_or_malformed_update_id_rejects_without_dedup_or_dispatch() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)
    composition = runtime._composition
    dedup_spy = AsyncMock(wraps=composition.telegram_update_dedup.mark_if_first_seen)
    composition.telegram_update_dedup.mark_if_first_seen = dedup_spy  # type: ignore[method-assign]
    dispatch_spy = AsyncMock()
    missing = {"message": _private_get_access_update(update_id=5)["message"]}
    malformed = {**_private_get_access_update(update_id=5), "update_id": "oops"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch.object(polling_mod, "handle_slice1_telegram_update_to_runtime_action", dispatch_spy):
            r_missing = await ac.post(
                "/telegram/webhook",
                content=json.dumps(missing).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
            )
            r_malformed = await ac.post(
                "/telegram/webhook",
                content=json.dumps(malformed).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
            )
    assert r_missing.status_code == 400
    assert r_missing.json() == {"ok": False, "error": "invalid_update_id"}
    assert r_malformed.status_code == 400
    assert r_malformed.json() == {"ok": False, "error": "invalid_update_id"}
    dedup_spy.assert_not_called()
    dispatch_spy.assert_not_called()


def test_load_settings_none_when_http_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", raising=False)
    assert load_telegram_webhook_ingress_settings_from_env(app_env="development") is None


def test_load_settings_strict_env_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="TELEGRAM_WEBHOOK_SECRET_TOKEN"):
        load_telegram_webhook_ingress_settings_from_env(app_env="production")


def test_load_settings_local_missing_secret_requires_explicit_insecure_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.delenv(ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL, raising=False)
    with pytest.raises(ConfigurationError, match="TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL"):
        load_telegram_webhook_ingress_settings_from_env(app_env="development")


def test_load_settings_local_missing_secret_allows_only_with_insecure_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.setenv(ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL, "yes")
    s = load_telegram_webhook_ingress_settings_from_env(app_env="development")
    assert s is not None
    assert s.expected_secret is None


def test_load_settings_production_missing_secret_still_fails_when_insecure_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", raising=False)
    monkeypatch.setenv(ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL, "1")
    with pytest.raises(ConfigurationError, match="TELEGRAM_WEBHOOK_SECRET_TOKEN"):
        load_telegram_webhook_ingress_settings_from_env(app_env="production")


def test_load_settings_strict_rejects_weak_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HTTP_ENABLE", "1")
    monkeypatch.setenv("LAUNCH_PREFLIGHT_STRICT", "1")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "abcdabcdabcdabcdabcdabcd")
    with pytest.raises(ConfigurationError, match="TELEGRAM_WEBHOOK_SECRET_TOKEN"):
        load_telegram_webhook_ingress_settings_from_env(app_env="production")


@pytest.mark.asyncio
async def test_process_single_mapped_update_runs_pipeline() -> None:
    """Smoke: mapped update reaches polling runtime without fetch path."""
    runtime = _build_runtime()
    out = await runtime.process_single_mapped_update(_private_help_update(), correlation_id=None)
    assert out.received_count == 1


@pytest.mark.asyncio
async def test_secret_validation_uses_constant_time_compare_digest() -> None:
    secret = _secret_for_tests()
    settings = TelegramWebhookIngressSettings(expected_secret=secret)
    runtime = _build_runtime()
    app = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings)

    compare_calls: list[tuple[str, str]] = []

    original_compare = secrets.compare_digest

    def _compare(incoming: str, expected: str) -> bool:
        compare_calls.append((incoming, expected))
        return original_compare(incoming, expected)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.runtime.telegram_webhook_ingress.secrets.compare_digest", side_effect=_compare):
            ok = await ac.post(
                "/telegram/webhook",
                content=json.dumps(_private_help_update()).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret},
            )
            bad = await ac.post(
                "/telegram/webhook",
                content=json.dumps(_private_help_update()).encode("utf-8"),
                headers={"content-type": "application/json", TELEGRAM_WEBHOOK_SECRET_HEADER: secret + "x"},
            )
    assert ok.status_code == 200
    assert bad.status_code == 401
    assert len(compare_calls) == 2
