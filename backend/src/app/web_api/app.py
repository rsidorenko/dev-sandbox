"""Web API Starlette app — mounts all /api/v1/* routes for frontend."""

from __future__ import annotations

import hmac
import logging
import os

import asyncpg
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.web_api.auth import handle_logout, handle_send_code, handle_verify_code
from app.web_api.email_link import handle_bot_send_code, handle_bot_verify_code
from app.web_api.middleware import require_auth, require_csrf
from app.web_api.payment import handle_create_payment, handle_get_payment_status
from app.web_api.profile import (
    handle_activate_trial,
    handle_cancel_subscription,
    handle_change_devices,
    handle_change_plan,
    handle_get_keys,
    handle_get_profile,
    handle_reissue_keys,
    handle_renew_subscription,
)
from app.web_api.subscription import handle_subscription
from app.yookassa.webhook import create_yookassa_webhook_handler

_LOGGER = logging.getLogger(__name__)

ENV_INTERNAL_API_SECRET = "INTERNAL_API_SECRET"


def _require_internal_auth(request: Request) -> JSONResponse | None:
    """Validate internal API secret from header. Returns error response or None if OK."""
    secret = os.environ.get(ENV_INTERNAL_API_SECRET, "").strip()
    if not secret:
        _LOGGER.error("INTERNAL_API_SECRET is not configured — internal endpoint rejected")
        return JSONResponse(
            {"ok": False, "error": "internal_api_not_configured"},
            status_code=503,
        )
    header_val = request.headers.get("X-Internal-Secret", "").strip()
    if not header_val or not hmac.compare_digest(header_val, secret):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return None


async def _healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


def _wrap_internal(handler):
    """Wrap an internal endpoint handler with mandatory secret-based auth."""
    async def _wrapped(request: Request) -> JSONResponse:
        auth_err = _require_internal_auth(request)
        if auth_err is not None:
            return auth_err
        return await handler(request)
    return _wrapped


def _with_csrf(handler):
    """Wrap a handler to enforce CSRF validation before execution."""
    async def _wrapped(request: Request) -> JSONResponse:
        csrf_err = require_csrf(request)
        if csrf_err is not None:
            return csrf_err
        return await handler(request)
    return _wrapped


def _with_auth(handler):
    """Wrap a handler to enforce JWT auth at routing level."""
    async def _wrapped(request: Request) -> JSONResponse:
        auth_result = await require_auth(request)
        if isinstance(auth_result, JSONResponse):
            return auth_result
        request.state.user = auth_result
        return await handler(request)
    return _wrapped


async def _yookassa_webhook_handler(request: Request) -> JSONResponse:
    """Thin adapter that delegates to the real handler, passing vless_provider and notifier from app.state."""
    pool: asyncpg.Pool = request.app.state.pool
    vless_provider = getattr(request.app.state, "vless_provider", None)
    notifier = getattr(request.app.state, "activation_notifier", None)
    handler = create_yookassa_webhook_handler(
        pool=pool,
        activation_telegram_notifier=notifier,
        vless_provider=vless_provider,
    )
    return await handler(request)


def build_web_api_app(*, pool: asyncpg.Pool) -> Starlette:
    cors_origins = os.environ.get("WEB_API_CORS_ORIGINS", "").strip()

    routes = [
        Route("/api/v1/healthz", _healthz, methods=["GET"]),
        # Auth
        Route("/api/v1/auth/email/send-code", handle_send_code, methods=["POST"]),
        Route("/api/v1/auth/email/verify", handle_verify_code, methods=["POST"]),
        Route("/api/v1/auth/logout", handle_logout, methods=["POST"]),
        # Profile
        Route("/api/v1/user/profile", _with_auth(handle_get_profile), methods=["GET"]),
        # Keys
        Route("/api/v1/user/keys", _with_auth(handle_get_keys), methods=["GET"]),
        Route("/api/v1/user/keys/reissue", _with_auth(_with_csrf(handle_reissue_keys)), methods=["POST"]),
        # Subscription management (auth + CSRF-protected)
        Route("/api/v1/user/subscription/renew", _with_auth(_with_csrf(handle_renew_subscription)), methods=["POST"]),
        Route("/api/v1/user/subscription/change-plan", _with_auth(_with_csrf(handle_change_plan)), methods=["POST"]),
        Route("/api/v1/user/subscription/change-devices", _with_auth(_with_csrf(handle_change_devices)), methods=["POST"]),
        Route("/api/v1/user/subscription/cancel", _with_auth(_with_csrf(handle_cancel_subscription)), methods=["POST"]),
        # Trial
        Route("/api/v1/user/trial/activate", _with_auth(_with_csrf(handle_activate_trial)), methods=["POST"]),
        # Payment
        Route("/api/v1/payment/create", _with_auth(_with_csrf(handle_create_payment)), methods=["POST"]),
        Route("/api/v1/payment/{payment_id}/status", _with_auth(handle_get_payment_status), methods=["GET"]),
        # Email linking (called by bot internally, protected by INTERNAL_API_SECRET)
        Route("/api/v1/internal/email/send-code", _wrap_internal(handle_bot_send_code), methods=["POST"]),
        Route("/api/v1/internal/email/verify-code", _wrap_internal(handle_bot_verify_code), methods=["POST"]),
        # Public subscription endpoint (no auth)
        Route("/sub/{token}", handle_subscription, methods=["GET"]),
        # YooKassa webhook (no auth — signature verified internally)
        Route("/api/v1/payment/yookassa/webhook", _yookassa_webhook_handler, methods=["POST"]),
    ]

    app = Starlette(routes=routes)
    app.state.pool = pool

    from app.issuance.vless_provider import StubVlessProvider
    from app.issuance.xui_vless_provider import XuiVlessProvider

    if _truthy(os.environ.get("XUI_ENABLED")):
        app.state.vless_provider = XuiVlessProvider(pool)
    else:
        app.state.vless_provider = StubVlessProvider()

    bot_token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or os.environ.get("BOT_TOKEN", "").strip()
    )
    if bot_token:
        from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient

        _raw_client = HttpxTelegramRawPollingClient(bot_token)

        class _WebApiActivationNotifier:
            __slots__ = ("_client",)

            def __init__(self, client: HttpxTelegramRawPollingClient) -> None:
                self._client = client

            async def send_subscription_activated_notice(
                self,
                *,
                telegram_user_id: int,
                text: str,
                reply_markup: dict | None,
                correlation_id: str,
            ) -> None:
                await self._client.send_text_message(
                    chat_id=telegram_user_id,
                    text=text,
                    correlation_id=correlation_id,
                    reply_markup=reply_markup,
                )

        app.state.activation_notifier = _WebApiActivationNotifier(_raw_client)
    else:
        app.state.activation_notifier = None

    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
                allow_credentials=True,
            )

    return app
