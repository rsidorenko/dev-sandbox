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
from app.web_api.middleware import require_csrf
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


def build_web_api_app(*, pool: asyncpg.Pool) -> Starlette:
    cors_origins = os.environ.get("WEB_API_CORS_ORIGINS", "").strip()

    routes = [
        Route("/api/v1/healthz", _healthz, methods=["GET"]),
        # Auth
        Route("/api/v1/auth/email/send-code", handle_send_code, methods=["POST"]),
        Route("/api/v1/auth/email/verify", handle_verify_code, methods=["POST"]),
        Route("/api/v1/auth/logout", handle_logout, methods=["POST"]),
        # Profile
        Route("/api/v1/user/profile", handle_get_profile, methods=["GET"]),
        # Keys
        Route("/api/v1/user/keys", handle_get_keys, methods=["GET"]),
        Route("/api/v1/user/keys/reissue", _with_csrf(handle_reissue_keys), methods=["POST"]),
        # Subscription management (CSRF-protected)
        Route("/api/v1/user/subscription/renew", _with_csrf(handle_renew_subscription), methods=["POST"]),
        Route("/api/v1/user/subscription/change-plan", _with_csrf(handle_change_plan), methods=["POST"]),
        Route("/api/v1/user/subscription/change-devices", _with_csrf(handle_change_devices), methods=["POST"]),
        Route("/api/v1/user/subscription/cancel", _with_csrf(handle_cancel_subscription), methods=["POST"]),
        # Trial
        Route("/api/v1/user/trial/activate", _with_csrf(handle_activate_trial), methods=["POST"]),
        # Payment
        Route("/api/v1/payment/create", _with_csrf(handle_create_payment), methods=["POST"]),
        Route("/api/v1/payment/{payment_id}/status", handle_get_payment_status, methods=["GET"]),
        # Email linking (called by bot internally, protected by INTERNAL_API_SECRET)
        Route("/api/v1/internal/email/send-code", _wrap_internal(handle_bot_send_code), methods=["POST"]),
        Route("/api/v1/internal/email/verify-code", _wrap_internal(handle_bot_verify_code), methods=["POST"]),
        # Public subscription endpoint (no auth)
        Route("/sub/{token}", handle_subscription, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    app.state.pool = pool

    from app.issuance.vless_provider import StubVlessProvider
    from app.issuance.xui_vless_provider import XuiVlessProvider

    if _truthy(os.environ.get("XUI_ENABLED")):
        app.state.vless_provider = XuiVlessProvider(pool)
    else:
        app.state.vless_provider = StubVlessProvider()

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
