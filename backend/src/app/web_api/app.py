"""Web API Starlette app — mounts all /api/v1/* routes for frontend."""

from __future__ import annotations

import os
from typing import Any

import asyncpg
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.web_api.auth import handle_send_code, handle_verify_code, handle_logout
from app.web_api.email_link import handle_bot_send_code, handle_bot_verify_code
from app.web_api.payment import handle_create_payment, handle_get_payment_status
from app.web_api.profile import handle_get_profile


async def _healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


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
        # Payment
        Route("/api/v1/payment/create", handle_create_payment, methods=["POST"]),
        Route("/api/v1/payment/{payment_id}/status", handle_get_payment_status, methods=["GET"]),
        # Email linking (called by bot internally)
        Route("/api/v1/internal/email/send-code", handle_bot_send_code, methods=["POST"]),
        Route("/api/v1/internal/email/verify-code", handle_bot_verify_code, methods=["POST"]),
    ]

    app = Starlette(routes=routes)
    app.state.pool = pool

    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"],
                allow_credentials=True,
            )

    return app
