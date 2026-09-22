"""Web API Starlette app — mounts all /api/v1/* routes for frontend."""

from __future__ import annotations

import logging
import os

import asyncpg
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.application.trial_channel_gate import (
    TelegramChannelMembershipChecker,
    channel_username_from_env,
    trial_channel_gate_enabled_from_env,
)
from app.web_api.admin_ad_spend import handle_admin_set_telegram_ads_spend
from app.web_api.admin_auth import handle_admin_login, handle_admin_logout
from app.web_api.admin_broadcast import (
    handle_admin_broadcast_estimate,
    handle_admin_broadcast_list,
    handle_admin_broadcast_send,
    handle_admin_broadcast_status,
    handle_admin_broadcast_test,
)
from app.web_api.admin_payments import handle_admin_payments
from app.web_api.admin_plans import (
    handle_admin_list_plans,
    handle_admin_reset_plan_price,
    handle_admin_set_global_discount,
    handle_admin_set_plan_price,
)
from app.web_api.admin_promocodes import (
    handle_admin_create_promocode,
    handle_admin_delete_promocode,
    handle_admin_list_promocodes,
    handle_admin_update_promocode,
)
from app.web_api.admin_servers import handle_admin_servers
from app.web_api.admin_stats import handle_admin_stats
from app.web_api.admin_users import (
    handle_admin_add_days,
    handle_admin_get_user,
    handle_admin_list_users,
    handle_admin_reset_test_account,
    handle_admin_set_internal,
)
from app.web_api.connection_check import (
    handle_check_feedback,
    handle_check_ping,
    handle_check_result,
    handle_check_speed,
    handle_check_speed_legacy,
    handle_check_status,
)
from app.web_api.experiments import handle_resolve_experiment
from app.web_api.link_auth import handle_link_request
from app.web_api.middleware import require_auth
from app.web_api.notifications import (
    handle_list_notifications,
    handle_notification_claim,
    handle_notifications_read,
)
from app.web_api.payment import (
    handle_create_add_device_balance_payment,
    handle_create_add_device_payment,
    handle_create_balance_payment,
    handle_create_payment,
    handle_get_payment_status,
)
from app.web_api.plans import handle_list_plans
from app.web_api.profile import (
    handle_activate_trial,
    handle_cancel_subscription,
    handle_change_plan,
    handle_get_keys,
    handle_get_profile,
    handle_reissue_keys,
    handle_renew_subscription,
)
from app.web_api.promocode import (
    handle_apply_promocode,
    handle_clear_promocode,
    handle_get_promocode,
)
from app.web_api.referral import handle_get_referral_stats, handle_get_referral_transactions
from app.web_api.subscription import handle_subscription
from app.yookassa.webhook import create_yookassa_webhook_handler

_LOGGER = logging.getLogger(__name__)


async def _healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("1", "true", "yes")


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


async def _cryptobot_webhook_handler(request: Request) -> JSONResponse:
    """Thin adapter for the CryptoBot webhook — same shape as the YooKassa one."""
    from app.cryptobot.webhook import create_cryptobot_webhook_handler

    pool: asyncpg.Pool = request.app.state.pool
    vless_provider = getattr(request.app.state, "vless_provider", None)
    notifier = getattr(request.app.state, "activation_notifier", None)
    handler = create_cryptobot_webhook_handler(
        pool=pool,
        activation_telegram_notifier=notifier,
        vless_provider=vless_provider,
    )
    return await handler(request)


def build_web_api_app(*, pool: asyncpg.Pool) -> Starlette:
    cors_origins = os.environ.get("WEB_API_CORS_ORIGINS", "").strip()

    routes = [
        Route("/api/v1/healthz", _healthz, methods=["GET"]),
        # Публичный каталог тарифов: эффективные цены + глобальная скидка (Cache-Control 30с).
        Route("/api/v1/plans", handle_list_plans, methods=["GET"]),
        # Auth: вход по ссылке-подписке (email-OTP удалён вместе с куками; почта —
        # только канал доставки ссылки и нигде не хранится)
        Route("/api/v1/auth/link/request", handle_link_request, methods=["POST"]),
        # Profile
        Route("/api/v1/user/profile", _with_auth(handle_get_profile), methods=["GET"]),
        Route(
            "/api/v1/user/experiments/resolve",
            _with_auth(handle_resolve_experiment),
            methods=["POST"],
        ),
        # Referral dashboard (stats, conversion, recent transactions)
        Route("/api/v1/user/referral", _with_auth(handle_get_referral_stats), methods=["GET"]),
        # Extended, filtered transaction history (clean fields only — no webhook data)
        Route(
            "/api/v1/user/referral/transactions",
            _with_auth(handle_get_referral_transactions),
            methods=["GET"],
        ),
        # Keys
        Route("/api/v1/user/keys", _with_auth(handle_get_keys), methods=["GET"]),
        Route("/api/v1/user/keys/reissue", _with_auth(handle_reissue_keys), methods=["POST"]),
        # Subscription management (Bearer-токен; CSRF не нужен — кук нет)
        Route("/api/v1/user/subscription/renew", _with_auth(handle_renew_subscription), methods=["POST"]),
        Route("/api/v1/user/subscription/change-plan", _with_auth(handle_change_plan), methods=["POST"]),
        Route("/api/v1/user/subscription/cancel", _with_auth(handle_cancel_subscription), methods=["POST"]),
        # Trial
        Route("/api/v1/user/trial/activate", _with_auth(handle_activate_trial), methods=["POST"]),
        # Promocode (web session) — display/apply/clear the 24h-pinned promo. Discount
        # math stays in promo_service; these are input/display surface (Фаза 4).
        Route("/api/v1/user/promocode", _with_auth(handle_get_promocode), methods=["GET"]),
        Route(
            "/api/v1/user/promocode/apply",
            _with_auth(handle_apply_promocode),
            methods=["POST"],
        ),
        Route(
            "/api/v1/user/promocode/clear",
            _with_auth(handle_clear_promocode),
            methods=["POST"],
        ),
        # Notifications (web session): лента + прочтение + клейм бонуса рассылки
        # (claims-ядра shared с ботом).
        Route("/api/v1/user/notifications", _with_auth(handle_list_notifications), methods=["GET"]),
        Route(
            "/api/v1/user/notifications/read",
            _with_auth(handle_notifications_read),
            methods=["POST"],
        ),
        Route(
            "/api/v1/user/notifications/{notification_id}/claim",
            _with_auth(handle_notification_claim),
            methods=["POST"],
        ),
        # Payment
        Route("/api/v1/payment/create", _with_auth(handle_create_payment), methods=["POST"]),
        Route("/api/v1/payment/add-devices", _with_auth(handle_create_add_device_payment), methods=["POST"]),
        Route(
            "/api/v1/payment/balance",
            _with_auth(handle_create_balance_payment),
            methods=["POST"],
        ),
        Route(
            "/api/v1/payment/add-devices/balance",
            _with_auth(handle_create_add_device_balance_payment),
            methods=["POST"],
        ),
        Route("/api/v1/payment/{payment_id}/status", _with_auth(handle_get_payment_status), methods=["GET"]),
        # Public subscription endpoint (no auth)
        Route("/sub/{token}", handle_subscription, methods=["GET"]),
        # Connection Check (bearer-токен как /sub; ping не отдаёт объёмных данных).
        Route("/api/v1/check/ping", handle_check_ping, methods=["GET"]),
        Route("/api/v1/check/speed", handle_check_speed_legacy, methods=["GET"]),
        Route("/api/v1/check/{token}", handle_check_status, methods=["GET"]),
        Route("/api/v1/check/{token}/speed", handle_check_speed, methods=["GET"]),
        Route("/api/v1/check/{token}/result", handle_check_result, methods=["POST"]),
        Route("/api/v1/check/{token}/feedback", handle_check_feedback, methods=["POST"]),
        # YooKassa webhook (no auth — signature verified internally)
        Route("/api/v1/payment/yookassa/webhook", _yookassa_webhook_handler, methods=["POST"]),
        # CryptoBot webhook (no routing auth — HMAC signature verified internally)
        Route("/api/v1/payment/cryptobot/webhook", _cryptobot_webhook_handler, methods=["POST"]),
        # Admin panel (feature-flagged via WEB_ADMIN_ENABLE; auth via admin_session cookie)
        Route("/api/v1/admin/auth/login", handle_admin_login, methods=["POST"]),
        Route("/api/v1/admin/auth/logout", handle_admin_logout, methods=["POST"]),
        Route("/api/v1/admin/payments", handle_admin_payments, methods=["GET"]),
        Route("/api/v1/admin/servers", handle_admin_servers, methods=["GET"]),
        Route("/api/v1/admin/users", handle_admin_list_users, methods=["GET"]),
        Route("/api/v1/admin/users/{internal_user_id}", handle_admin_get_user, methods=["GET"]),
        Route("/api/v1/admin/users/{internal_user_id}/internal", handle_admin_set_internal, methods=["POST"]),
        # Операторский грант дней подписки без смены тарифа (auth + CSRF в обработчике).
        Route(
            "/api/v1/admin/users/{internal_user_id}/add-days",
            handle_admin_add_days,
            methods=["POST"],
        ),
        Route(
            "/api/v1/admin/users/{internal_user_id}/reset-test-account",
            handle_admin_reset_test_account,
            methods=["POST"],
        ),
        Route("/api/v1/admin/promocodes", handle_admin_list_promocodes, methods=["GET"]),
        Route("/api/v1/admin/promocodes", handle_admin_create_promocode, methods=["POST"]),
        Route("/api/v1/admin/promocodes/{code}", handle_admin_update_promocode, methods=["PATCH"]),
        Route("/api/v1/admin/promocodes/{code}", handle_admin_delete_promocode, methods=["DELETE"]),
        # Тарифы: цены 7 планов + глобальная скидка (auth в обработчике; мутации под CSRF).
        # literal-маршрут global-discount объявлен ДО параметрического {plan_id}.
        Route("/api/v1/admin/plans", handle_admin_list_plans, methods=["GET"]),
        Route("/api/v1/admin/plans/global-discount", handle_admin_set_global_discount, methods=["PUT"]),
        Route("/api/v1/admin/plans/{plan_id}", handle_admin_set_plan_price, methods=["PATCH"]),
        Route("/api/v1/admin/plans/{plan_id}", handle_admin_reset_plan_price, methods=["DELETE"]),
        # Агрегированный дашборд статистики (auth в обработчике; GET — без CSRF).
        Route("/api/v1/admin/stats", handle_admin_stats, methods=["GET"]),
        # Ручной дневной расход рекламы: auth + CSRF внутри обработчика.
        Route("/api/v1/admin/telegram-ads/spend", handle_admin_set_telegram_ads_spend, methods=["PUT"]),
        # Рассылка (auth + CSRF в обработчике). estimate/send/test — мутации под CSRF.
        Route("/api/v1/admin/broadcast/estimate", handle_admin_broadcast_estimate, methods=["POST"]),
        Route("/api/v1/admin/broadcast/send", handle_admin_broadcast_send, methods=["POST"]),
        Route("/api/v1/admin/broadcast/test", handle_admin_broadcast_test, methods=["POST"]),
        Route("/api/v1/admin/broadcast/jobs", handle_admin_broadcast_list, methods=["GET"]),
        Route(
            "/api/v1/admin/broadcast/jobs/{job_id}",
            handle_admin_broadcast_status,
            methods=["GET"],
        ),
    ]

    app = Starlette(routes=routes)
    app.state.pool = pool

    from app.issuance.vless_provider import StubVlessProvider
    from app.issuance.xui_vless_provider import XuiVlessProvider

    if _truthy(os.environ.get("XUI_ENABLED")):
        app.state.vless_provider = XuiVlessProvider(pool)
    else:
        app.state.vless_provider = StubVlessProvider()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get("BOT_TOKEN", "").strip()
    app.state.channel_subscription_required = trial_channel_gate_enabled_from_env()
    app.state.channel_membership_checker = None
    if app.state.channel_subscription_required:
        if bot_token:
            app.state.channel_membership_checker = TelegramChannelMembershipChecker(
                bot_token,
                channel_username_from_env(),
            )
        else:
            _LOGGER.error("channel_subscription_gate_enabled_without_bot_token")

    async def _close_channel_membership_checker() -> None:
        checker = getattr(app.state, "channel_membership_checker", None)
        if checker is not None:
            await checker.aclose()

    app.add_event_handler("shutdown", _close_channel_membership_checker)

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
        # Raw-клиент переиспользуется BroadcastRunner для прямой отправки (parse_mode=HTML).
        app.state.telegram_raw_client = _raw_client
    else:
        app.state.activation_notifier = None
        app.state.telegram_raw_client = None

    # Реестр фоновых задач рассылки (держит ссылки, чтобы asyncio не убрал task).
    app.state.broadcast_tasks = {}

    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"],
            )

    return app
