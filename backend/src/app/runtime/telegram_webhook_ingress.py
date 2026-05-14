"""Telegram Bot API HTTP webhook ingress (secret-gated; no SDK; no raw secrets in responses)."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.bot_transport.normalized import normalize_command_token
from app.runtime.raw_polling import Slice1RawPollingRuntime
from app.runtime.telegram_webhook_ingress_telemetry import (
    NoopTelegramWebhookIngressTelemetry,
    TelegramWebhookIngressDecisionEvent,
    TelegramWebhookIngressTelemetry,
)
from app.security.config import ConfigurationError
from app.security.validation import ValidationError, validate_telegram_update_id
from app.security.webhook_rate_limit import WebhookRateLimiter

# Telegram Bot API: https://core.telegram.org/bots/api#setwebhook
TELEGRAM_WEBHOOK_SECRET_HEADER = "x-telegram-bot-api-secret-token"
ENV_TELEGRAM_WEBHOOK_HTTP_ENABLE = "TELEGRAM_WEBHOOK_HTTP_ENABLE"
ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN = "TELEGRAM_WEBHOOK_SECRET_TOKEN"
ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL = "TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL"
ENV_LAUNCH_PREFLIGHT_STRICT = "LAUNCH_PREFLIGHT_STRICT"
_WEBHOOK_DEDUP_NAMESPACE = "telegram_webhook_ingress_v1"
_MUTATING_COMMANDS_ACCESS_RESEND: frozenset[str] = frozenset({"/get_access", "/resend_access"})
_MUTATING_COMMANDS_OTHER: frozenset[str] = frozenset({"/success"})


def _truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes")


def _is_local_app_env(app_env: str) -> bool:
    return app_env.strip().lower() in {"development", "dev", "local", "test"}


def _min_secret_strength_ok(value: str) -> bool:
    if len(value) < 24:
        return False
    classes = 0
    if any(c.islower() for c in value):
        classes += 1
    if any(c.isupper() for c in value):
        classes += 1
    if any(c.isdigit() for c in value):
        classes += 1
    if any(not c.isalnum() for c in value):
        classes += 1
    return classes >= 2 and len(set(value)) >= 8


def _webhook_secret_header_ok(incoming: str, expected: str) -> bool:
    if not incoming:
        return False
    return secrets.compare_digest(incoming, expected)


@dataclass(frozen=True, slots=True)
class TelegramWebhookIngressSettings:
    """Ingress policy for the HTTP webhook route."""

    expected_secret: str | None
    http_path: str = "/telegram/webhook"


def load_telegram_webhook_ingress_settings_from_env(*, app_env: str) -> TelegramWebhookIngressSettings | None:
    """
    Load webhook ingress settings from the process environment.

    When ``TELEGRAM_WEBHOOK_HTTP_ENABLE`` is falsey, returns ``None`` (no HTTP webhook app).
    In local/test ``APP_ENV`` buckets, a missing ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` is allowed
    only when ``TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL`` is truthy (explicit test-only opt-in).
    In production-like ``APP_ENV``, a configured non-empty ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` is
    required whenever webhook HTTP is enabled (fail-closed).
    """
    if not _truthy_env(os.environ.get(ENV_TELEGRAM_WEBHOOK_HTTP_ENABLE)):
        return None
    secret_raw = os.environ.get(ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN, "").strip()
    if _is_local_app_env(app_env):
        if not secret_raw and not _truthy_env(os.environ.get(ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL)):
            raise ConfigurationError(f"invalid configuration: {ENV_TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL}")
        if (
            secret_raw
            and _truthy_env(os.environ.get(ENV_LAUNCH_PREFLIGHT_STRICT))
            and not _min_secret_strength_ok(secret_raw)
        ):
            raise ConfigurationError(f"weak configuration: {ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN}")
        return TelegramWebhookIngressSettings(expected_secret=secret_raw or None)
    if not secret_raw:
        raise ConfigurationError(f"missing or empty configuration: {ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN}")
    if _truthy_env(os.environ.get(ENV_LAUNCH_PREFLIGHT_STRICT)) and not _min_secret_strength_ok(secret_raw):
        raise ConfigurationError(f"weak configuration: {ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN}")
    return TelegramWebhookIngressSettings(expected_secret=secret_raw)


def _reject_unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)


async def _emit_decision_best_effort(
    telemetry: TelegramWebhookIngressTelemetry,
    event: TelegramWebhookIngressDecisionEvent,
) -> None:
    try:
        await telemetry.emit_decision(event)
    except Exception:
        return


def create_slice1_telegram_webhook_starlette_app(
    runtime: Slice1RawPollingRuntime,
    *,
    settings: TelegramWebhookIngressSettings,
    telemetry: TelegramWebhookIngressTelemetry | None = None,
    rate_limiter: WebhookRateLimiter | None = None,
) -> Starlette:
    """Starlette app with a single POST webhook route wired to ``runtime``."""
    ingress_telemetry = telemetry or NoopTelegramWebhookIngressTelemetry()
    limiter = rate_limiter or WebhookRateLimiter()

    def _extract_valid_update_id(data: dict[str, Any]) -> int | None:
        raw_update_id = data.get("update_id")
        if raw_update_id is None:
            return None
        try:
            return validate_telegram_update_id(raw_update_id)
        except ValidationError:
            return None

    def _dedup_bucket_for_command(data: dict[str, Any]) -> str | None:
        message = data.get("message")
        if not isinstance(message, dict):
            return None
        token = normalize_command_token(message.get("text"))
        if token in _MUTATING_COMMANDS_ACCESS_RESEND:
            return "access_resend"
        if token in _MUTATING_COMMANDS_OTHER:
            return "other"
        return None

    async def telegram_webhook(request: Request) -> JSONResponse:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.is_allowed(client_ip):
            return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)

        expected = settings.expected_secret
        if expected is not None:
            incoming = request.headers.get(TELEGRAM_WEBHOOK_SECRET_HEADER, "").strip()
            if not incoming:
                await _emit_decision_best_effort(
                    ingress_telemetry,
                    TelegramWebhookIngressDecisionEvent(
                        event_type="telegram_webhook_ingress_decision",
                        decision="unauthorized",
                        reason_bucket="missing_secret_header",
                        path_bucket="telegram_webhook",
                        principal_marker="telegram_webhook_redacted",
                    ),
                )
                return _reject_unauthorized()
            if not _webhook_secret_header_ok(incoming, expected):
                await _emit_decision_best_effort(
                    ingress_telemetry,
                    TelegramWebhookIngressDecisionEvent(
                        event_type="telegram_webhook_ingress_decision",
                        decision="unauthorized",
                        reason_bucket="invalid_secret_header",
                        path_bucket="telegram_webhook",
                        principal_marker="telegram_webhook_redacted",
                    ),
                )
                return _reject_unauthorized()

        raw = await request.body()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await _emit_decision_best_effort(
                ingress_telemetry,
                TelegramWebhookIngressDecisionEvent(
                    event_type="telegram_webhook_ingress_decision",
                    decision="invalid_json",
                    reason_bucket="invalid_json",
                    path_bucket="telegram_webhook",
                    principal_marker="telegram_webhook_redacted",
                ),
            )
            return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
        if not isinstance(data, dict):
            await _emit_decision_best_effort(
                ingress_telemetry,
                TelegramWebhookIngressDecisionEvent(
                    event_type="telegram_webhook_ingress_decision",
                    decision="invalid_json",
                    reason_bucket="invalid_json",
                    path_bucket="telegram_webhook",
                    principal_marker="telegram_webhook_redacted",
                ),
            )
            return JSONResponse({"ok": False, "error": "invalid_body"}, status_code=400)

        update_id = _extract_valid_update_id(data)
        if update_id is None:
            reason_bucket = "missing_update_id" if data.get("update_id") is None else "invalid_update_id"
            await _emit_decision_best_effort(
                ingress_telemetry,
                TelegramWebhookIngressDecisionEvent(
                    event_type="telegram_webhook_ingress_decision",
                    decision="invalid_update",
                    reason_bucket=reason_bucket,
                    path_bucket="telegram_webhook",
                    principal_marker="telegram_webhook_redacted",
                ),
            )
            return JSONResponse({"ok": False, "error": "invalid_update_id"}, status_code=400)

        dedup_bucket = _dedup_bucket_for_command(data)
        if dedup_bucket is not None:
            first_seen = await runtime.mark_update_first_seen(
                namespace=_WEBHOOK_DEDUP_NAMESPACE,
                command_bucket=dedup_bucket,
                telegram_update_id=update_id,
            )
            if not first_seen:
                await _emit_decision_best_effort(
                    ingress_telemetry,
                    TelegramWebhookIngressDecisionEvent(
                        event_type="telegram_webhook_ingress_decision",
                        decision="duplicate_ignored",
                        reason_bucket="duplicate_update",
                        path_bucket="telegram_webhook",
                        principal_marker="telegram_webhook_redacted",
                    ),
                )
                return JSONResponse({"ok": True})

        await _emit_decision_best_effort(
            ingress_telemetry,
            TelegramWebhookIngressDecisionEvent(
                event_type="telegram_webhook_ingress_decision",
                decision="accepted",
                reason_bucket="valid_secret",
                path_bucket="telegram_webhook",
                principal_marker="telegram_webhook_redacted",
            ),
        )
        await runtime.process_single_mapped_update(data, correlation_id=None)
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route(settings.http_path, telegram_webhook, methods=["POST"]),
        ],
    )
