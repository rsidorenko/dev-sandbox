"""ASGI entrypoint for slice-1 Telegram Bot API HTTP webhook (env-driven; no secrets in logs)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

import asyncpg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.observability.logging_config import configure_logging

# Initialise structured logging before any logger usage.
configure_logging()

from app.observability.logging_policy import sanitize_structured_fields  # noqa: E402
from app.persistence.slice1_postgres_wiring import (  # noqa: E402
    resolve_slice1_composition_for_runtime,
    slice1_postgres_repos_requested,
)
from app.runtime.payment_fulfillment_ingress import (  # noqa: E402
    create_payment_fulfillment_ingress_app,
    load_fulfillment_ingress_settings_from_env,
)
from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient  # noqa: E402
from app.runtime.telegram_httpx_raw_startup import build_slice1_httpx_raw_runtime_bundle  # noqa: E402
from app.runtime.telegram_webhook_ingress import (  # noqa: E402
    create_slice1_telegram_webhook_starlette_app,
    load_telegram_webhook_ingress_settings_from_env,
)
from app.runtime.telegram_webhook_ingress_telemetry import (  # noqa: E402
    StructuredLoggingTelegramWebhookIngressTelemetry,
    TelegramWebhookIngressDecisionEvent,
    TelegramWebhookIngressTelemetry,
)
from app.security.config import ConfigurationError, load_runtime_config  # noqa: E402

_LOGGER = logging.getLogger(__name__)
ReadinessCheck = Callable[[], Awaitable[bool]]


class _FulfillmentActivationTelegramNotifier:
    """Delegates proactive activation copy to the shared raw Bot API client."""

    __slots__ = ("_client",)

    def __init__(self, client: HttpxTelegramRawPollingClient) -> None:
        self._client = client

    async def send_subscription_activated_notice(
        self,
        *,
        telegram_user_id: int,
        text: str,
        reply_markup: dict[str, Any] | None,
        correlation_id: str,
    ) -> None:
        await self._client.send_text_message(
            chat_id=telegram_user_id,
            text=text,
            correlation_id=correlation_id,
            reply_markup=reply_markup,
        )


def _truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes")


def _is_local_app_env(app_env: str) -> bool:
    return app_env.strip().lower() in {"development", "dev", "local", "test"}


def _durable_dedup_required(*, app_env: str) -> bool:
    if _truthy_env(os.environ.get("LAUNCH_PREFLIGHT_STRICT")):
        return True
    return not _is_local_app_env(app_env)


def _log_webhook_main_event(*, outcome: str, detail: str) -> None:
    _LOGGER.info(
        "runtime.telegram_webhook_main",
        extra={
            "structured_fields": sanitize_structured_fields(
                {
                    "intent": "webhook_asgi_entrypoint",
                    "outcome": outcome,
                    "operation": detail,
                }
            )
        },
    )


async def _webhook_disabled_response(_: Request) -> JSONResponse:
    return JSONResponse({"ok": False, "error": "webhook_http_disabled"}, status_code=503)


async def _healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _readyz_disabled(_: Request) -> JSONResponse:
    return JSONResponse({"status": "disabled"}, status_code=503)


async def _emit_decision_best_effort(
    telemetry: TelegramWebhookIngressTelemetry,
    event: TelegramWebhookIngressDecisionEvent,
) -> None:
    try:
        await telemetry.emit_decision(event)
    except Exception:
        return


def _build_webhook_disabled_handler(telemetry: TelegramWebhookIngressTelemetry):
    async def _handler(_: Request) -> JSONResponse:
        await _emit_decision_best_effort(
            telemetry,
            TelegramWebhookIngressDecisionEvent(
                event_type="telegram_webhook_ingress_decision",
                decision="disabled",
                reason_bucket="webhook_disabled",
                path_bucket="other",
                principal_marker="telegram_webhook_redacted",
            ),
        )
        return await _webhook_disabled_response(_)

    return _handler


def _should_check_postgres_readiness(*, database_url: str | None) -> bool:
    dsn = (database_url or "").strip()
    return bool(dsn) or slice1_postgres_repos_requested()


async def _default_postgres_readiness_check(*, database_url: str) -> bool:
    conn = None
    try:
        conn = await asyncpg.connect(database_url, timeout=2)
        await conn.fetchval("SELECT 1")
    except Exception:
        return False
    finally:
        if conn is not None:
            with suppress(Exception):
                await conn.close()
    return True


def _make_dependency_readiness_checker(
    *,
    runtime_initialized: bool,
    database_url: str | None,
    postgres_check: Callable[[str], Awaitable[bool]] = _default_postgres_readiness_check,
) -> ReadinessCheck:
    async def _checker() -> bool:
        if not runtime_initialized:
            return False
        if not _should_check_postgres_readiness(database_url=database_url):
            return True
        dsn = (database_url or "").strip()
        if not dsn:
            return False
        return await postgres_check(dsn)

    return _checker


def _build_readyz_handler(
    checker: ReadinessCheck,
    *,
    telemetry: TelegramWebhookIngressTelemetry,
):
    async def _readyz(_: Request) -> JSONResponse:
        try:
            if await checker():
                return JSONResponse({"status": "ok"})
            await _emit_decision_best_effort(
                telemetry,
                TelegramWebhookIngressDecisionEvent(
                    event_type="telegram_webhook_ingress_decision",
                    decision="not_ready",
                    reason_bucket="readiness_failed",
                    path_bucket="readyz",
                    principal_marker="telegram_webhook_redacted",
                ),
            )
            return JSONResponse({"status": "not_ready"}, status_code=503)
        except Exception:
            await _emit_decision_best_effort(
                telemetry,
                TelegramWebhookIngressDecisionEvent(
                    event_type="telegram_webhook_ingress_decision",
                    decision="not_ready",
                    reason_bucket="readiness_failed",
                    path_bucket="readyz",
                    principal_marker="telegram_webhook_redacted",
                ),
            )
            _LOGGER.warning(
                "runtime.telegram_webhook_main.readiness_check_failed",
                extra={
                    "structured_fields": sanitize_structured_fields(
                        {
                            "intent": "webhook_asgi_entrypoint",
                            "outcome": "not_ready",
                            "operation": "dependency_readiness_check",
                        }
                    )
                },
            )
            return JSONResponse({"status": "not_ready"}, status_code=503)

    return _readyz


def _build_webhook_http_disabled_starlette_app() -> Starlette:
    """Disabled webhook app: ``/healthz`` alive, ``/readyz`` and other paths disabled (no BOT_TOKEN read)."""

    _log_webhook_main_event(outcome="disabled", detail="http_enable_falsey")
    telemetry = StructuredLoggingTelegramWebhookIngressTelemetry()
    return Starlette(
        routes=[
            Route("/healthz", _healthz, methods=["GET"]),
            Route("/readyz", _readyz_disabled, methods=["GET"]),
            Route("/{path:path}", _build_webhook_disabled_handler(telemetry)),
        ],
    )


def build_slice1_telegram_webhook_asgi_application_from_env(
    *,
    dependency_readiness_check: ReadinessCheck | None = None,
) -> Starlette:
    """
    Build the webhook ASGI application from the process environment.

    - Webhook HTTP disabled (default): returns a Starlette app with ``/healthz`` = 200 and
      ``/readyz`` = 503, without loading ``BOT_TOKEN`` or mounting the Telegram route.
    - Webhook HTTP enabled: loads :func:`load_runtime_config`, builds the same httpx raw bundle as
      long-polling slice-1, mounts ``create_slice1_telegram_webhook_starlette_app``, and closes
      the outbound httpx client on app shutdown (lifespan).
    - Production-like ``APP_ENV`` with HTTP enabled and missing webhook secret: raises
      :class:`ConfigurationError` (fail-closed) before accepting traffic.
    """
    app_env = os.environ.get("APP_ENV", "development").strip() or "development"
    settings = load_telegram_webhook_ingress_settings_from_env(app_env=app_env)
    if settings is None:
        return _build_webhook_http_disabled_starlette_app()

    config = load_runtime_config()
    if _durable_dedup_required(app_env=app_env) and not slice1_postgres_repos_requested():
        raise ConfigurationError("missing configuration for durable webhook dedup: SLICE1_USE_POSTGRES_REPOS=1")
    composition, runtime_pool = None, None
    if slice1_postgres_repos_requested():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Already inside event loop (uvicorn) — use nest_asyncio or defer
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                composition, runtime_pool = executor.submit(
                    asyncio.run, resolve_slice1_composition_for_runtime(config)
                ).result()
        else:
            composition, runtime_pool = asyncio.run(resolve_slice1_composition_for_runtime(config))
    raw_bundle = build_slice1_httpx_raw_runtime_bundle(
        config.bot_token,
        composition=composition,
    )
    runtime = raw_bundle.bundle.runtime
    telemetry = StructuredLoggingTelegramWebhookIngressTelemetry()
    inner = create_slice1_telegram_webhook_starlette_app(runtime, settings=settings, telemetry=telemetry)
    fulfillment_settings = load_fulfillment_ingress_settings_from_env()
    fulfillment_app: Starlette | None = None
    fulfillment_pool: asyncpg.Pool | None = None
    if fulfillment_settings is not None:
        dsn = (config.database_url or "").strip()
        if not dsn:
            raise ConfigurationError("missing or empty configuration: DATABASE_URL")
    checker = dependency_readiness_check or _make_dependency_readiness_checker(
        runtime_initialized=True,
        database_url=config.database_url,
    )
    readyz_handler = _build_readyz_handler(checker, telemetry=telemetry)

    async def _fulfillment_proxy(request: Request) -> JSONResponse:
        if fulfillment_app is None:
            return JSONResponse({"ok": False, "error": "temporarily_unavailable"}, status_code=503)
        return await fulfillment_app.router.routes[0].endpoint(request)  # type: ignore[return-value]

    @asynccontextmanager
    async def _lifespan(_: Starlette) -> AsyncIterator[None]:
        nonlocal fulfillment_pool, fulfillment_app
        if fulfillment_settings is not None:
            dsn = (config.database_url or "").strip()
            fulfillment_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            fulfillment_app = create_payment_fulfillment_ingress_app(
                pool=fulfillment_pool,
                settings=fulfillment_settings,
                activation_telegram_notifier=_FulfillmentActivationTelegramNotifier(raw_bundle.client),
            )
        _log_webhook_main_event(outcome="ready", detail="http_enabled")
        yield
        if fulfillment_pool is not None:
            await fulfillment_pool.close()
        if runtime_pool is not None:
            await runtime_pool.close()
        await raw_bundle.aclose()
        _log_webhook_main_event(outcome="shutdown", detail="client_closed")

    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        Route("/readyz", readyz_handler, methods=["GET"]),
        *list(inner.routes),
    ]
    if fulfillment_settings is not None:
        routes.append(Route(fulfillment_settings.http_path, _fulfillment_proxy, methods=["POST"]))

    # Mount web API for frontend if enabled
    web_api_enabled = _truthy_env(os.environ.get("WEB_API_ENABLE"))
    web_api_pool: asyncpg.Pool | None = None
    web_api_app: Starlette | None = None
    if web_api_enabled:
        dsn = (config.database_url or "").strip()
        if not dsn:
            raise ConfigurationError("missing or empty configuration: DATABASE_URL for web API")

    @asynccontextmanager
    async def _lifespan_with_web_api(app: Starlette) -> AsyncIterator[None]:
        nonlocal fulfillment_pool, fulfillment_app, web_api_pool, web_api_app
        if fulfillment_settings is not None:
            dsn = (config.database_url or "").strip()
            fulfillment_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            fulfillment_app = create_payment_fulfillment_ingress_app(
                pool=fulfillment_pool,
                settings=fulfillment_settings,
                activation_telegram_notifier=_FulfillmentActivationTelegramNotifier(raw_bundle.client),
            )
        if web_api_enabled:
            dsn = (config.database_url or "").strip()
            web_api_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
            from app.web_api.app import build_web_api_app

            web_api_app = build_web_api_app(pool=web_api_pool)
        _log_webhook_main_event(outcome="ready", detail="http_enabled")
        yield
        if web_api_pool is not None:
            await web_api_pool.close()
        if fulfillment_pool is not None:
            await fulfillment_pool.close()
        if runtime_pool is not None:
            await runtime_pool.close()
        await raw_bundle.aclose()
        _log_webhook_main_event(outcome="shutdown", detail="client_closed")

    async def _web_api_proxy(request: Request) -> JSONResponse:
        if web_api_app is None:
            return JSONResponse({"ok": False, "error": "temporarily_unavailable"}, status_code=503)
        # Directly call the web_api_app as ASGI sub-app
        scope = request.scope
        recv = request.receive
        sent_responses: list[dict[str, Any]] = []
        sent_body_parts: list[bytes] = []

        async def send(msg: dict[str, Any]) -> None:
            if msg["type"] == "http.response.start":
                sent_responses.append(msg)
            elif msg["type"] == "http.response.body":
                sent_body_parts.append(msg.get("body", b""))

        await web_api_app(scope, recv, send)

        if not sent_responses:
            return JSONResponse({"ok": False, "error": "internal_error"}, status_code=500)

        status_code = sent_responses[0].get("status", 500)
        body = b"".join(sent_body_parts)
        return JSONResponse(
            content=json.loads(body) if body else {},
            status_code=status_code,
        )

    if web_api_enabled:
        routes.append(Route("/api/{path:path}", _web_api_proxy, methods=["GET", "POST", "OPTIONS"]))

    lifespan = _lifespan_with_web_api if web_api_enabled else _lifespan
    return Starlette(routes=routes, lifespan=lifespan)


def _build_app_or_raise_config() -> Starlette:
    try:
        return build_slice1_telegram_webhook_asgi_application_from_env()
    except ConfigurationError:
        _LOGGER.error(
            "runtime.telegram_webhook_main",
            extra={
                "structured_fields": sanitize_structured_fields(
                    {
                        "intent": "webhook_asgi_entrypoint",
                        "outcome": "config_error",
                        "operation": "build",
                        "internal_category": "configuration_error",
                    }
                )
            },
        )
        raise


app: Starlette = _build_app_or_raise_config()

__all__ = [
    "app",
    "build_slice1_telegram_webhook_asgi_application_from_env",
]
