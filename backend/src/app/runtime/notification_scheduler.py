"""Background notification scheduler for subscription/trial lifecycle events.

Runs as an async task alongside the bot polling loop.
Checks with a configurable cadence for trial conversion stages, subscription
expiry, key lifecycle and referral offers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from app.bot_transport.custom_emoji import (
    apply_custom_emoji,
    apply_keyboard_custom_emoji,
    custom_emoji_enabled_from_env,
)
from app.bot_transport.notification_texts import (
    text_checkout_abandoned,
    text_keys_deleted,
    text_referral_offer_paid,
    text_referral_offer_trial,
    text_subscription_expired,
    text_subscription_expiring,
    text_trial_activation_nudge,
    text_trial_connection_nudge,
    text_trial_expired,
    text_trial_expired_followup,
    text_trial_expiring,
    text_trial_expiring_6h,
)
from app.domain.plans import get_plan
from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderPort
from app.persistence.postgres_web_notifications import PostgresWebNotificationRepository

_LOGGER = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = int(os.environ.get("NOTIFICATION_CHECK_INTERVAL_SECONDS", "900"))
_EXPIRY_WARNING_HOURS = int(os.environ.get("NOTIFICATION_EXPIRY_WARNING_HOURS", "72"))
_GRACE_PERIOD_DAYS = int(os.environ.get("NOTIFICATION_GRACE_PERIOD_DAYS", "20"))
_ADVISORY_LOCK_ID = 20260608
_TRIAL_WARNING_HOURS = 24
_TRIAL_URGENT_HOURS = 6
_TRIAL_PAYWALL_RETRY_HOURS = 48
_TRIAL_FOLLOWUP_DELAY_HOURS = 24
_TRIAL_FOLLOWUP_WINDOW_HOURS = 48
_ACTIVATION_NUDGE_DELAY_MINUTES = 15
_ACTIVATION_NUDGE_WINDOW_HOURS = 48
_CONNECTION_NUDGE_DELAY_MINUTES = 15
_CHECKOUT_NUDGE_DELAY_MINUTES = 30
_CHECKOUT_NUDGE_WINDOW_HOURS = 24
# Окно «недавней активации» платной подписки оставляет запас относительно cadence,
# чтобы после первой оплаты пользователь попал хотя бы в один тик.
_REFERRAL_OFFER_RECENT_HOURS = 25

# Веб-канал: notification_type → (kind, title). kind ⊆ CHECK-множества миграции 077
# (контракт-тест в test_notification_scheduler_web.py). body — исходный текст уведомления.
_WEB_KINDS: dict[str, tuple[str, str]] = {
    "trial_activation_nudge_15m": ("broadcast", "Бесплатный VPN готов"),
    "trial_connection_nudge_15m": ("broadcast", "Завершите подключение"),
    "trial_expiring": ("trial_expiring", "Триал заканчивается"),
    "trial_expiring_24h": ("trial_expiring", "До конца триала меньше суток"),
    "trial_expiring_6h": ("trial_expiring", "До конца триала несколько часов"),
    "trial_expired": ("trial_expired", "Триал истёк"),
    "trial_expired_followup_24h": ("trial_expired", "VPN можно возобновить"),
    "checkout_abandoned_30m": ("trial_expiring", "Оплата не завершена"),
    "subscription_expiring_3d": ("subscription_expiring", "Подписка заканчивается"),
    "subscription_expired": ("subscription_expired", "Подписка истекла"),
    "keys_deleted": ("key_deleted", "Ключи удалены"),
    "referral_offer_trial": ("referral_offer", "Реферальная программа"),
    "referral_offer_paid": ("referral_offer", "Реферальная программа"),
}

_MSK = ZoneInfo("Europe/Moscow")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _format_msk_datetime(value: datetime) -> str:
    return value.astimezone(_MSK).strftime("%d.%m в %H:%M")


def _web_notification_body(text: str, *, kind: str) -> str:
    """Веб рендерит HTML только для broadcast; у остальных типов убираем Telegram-теги."""
    return text if kind == "broadcast" else _HTML_TAG_RE.sub("", text)


def _checkout_resume_path(
    plan_id: str | None,
    device_count: int | None,
    payment_method: str | None,
) -> str | None:
    if plan_id is None or get_plan(plan_id) is None or device_count is None or not 5 <= device_count <= 20:
        return None
    path = f"/payment/{quote(plan_id, safe=':')}?devices={device_count}"
    if payment_method in ("card", "crypto"):
        path += f"&method={payment_method}"
    return path


def _referral_offer_enabled(env_getter=os.environ.get) -> bool:
    """Referral offer push kill-switch. Default ON (empty/missing → True); explicit false-y → OFF."""
    raw = (env_getter("REFERRAL_OFFER_ENABLE") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


class NotificationScheduler:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        bot_token: str,
        vless_provider: VlessProviderPort,
    ) -> None:
        self._pool = pool
        self._bot_token = bot_token
        self._vless_provider = vless_provider
        self._running = False
        self._http_client: httpx.AsyncClient | None = None

    async def run(self) -> None:
        """Main loop: run checks with the configured cadence."""
        self._running = True
        while self._running:
            try:
                await self._run_checks()
            except Exception:
                _LOGGER.exception("notification_scheduler_check_error")
            await self._sleep(_CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    async def aclose(self) -> None:
        if self._http_client is not None:
            with contextlib.suppress(Exception):
                await self._http_client.aclose()
            self._http_client = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=15.0)
        return self._http_client

    async def _sleep(self, seconds: int) -> None:
        """Interruptible sleep."""
        for _ in range(seconds):
            if not self._running:
                return
            await asyncio.sleep(1)

    async def _run_checks(self) -> None:
        async with self._pool.acquire() as conn:
            locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_ID)
            if not locked:
                _LOGGER.info("notification_scheduler tick skipped — another instance holds the lock")
                return
            try:
                await self._run_checks_inner()
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_ID)

    async def _run_checks_inner(self) -> None:
        now = datetime.now(UTC)
        _LOGGER.info("notification_scheduler.run_checks at=%s", now.isoformat())

        await self._check_trial_activation_nudge(now)
        await self._check_trial_connection_nudge(now)
        await self._check_checkout_abandoned(now)
        await self._check_trial_expiring(now)
        await self._check_trial_expiring_urgent(now)
        await self._check_trial_expired(now)
        await self._check_trial_expired_paywall(now)
        await self._check_trial_expired_followup(now)
        await self._check_subscription_expiring(now)
        await self._check_subscription_expired(now)
        await self._check_keys_grace_period_expired(now)
        if _referral_offer_enabled():
            await self._check_referral_offer_trial(now)
            await self._check_referral_offer_paid(now)

    async def _check_trial_activation_nudge(self, now: datetime) -> None:
        """Одно касание новым пользователям, которые не запустили доступный триал."""
        rows = await self._pool.fetch(
            """SELECT u.internal_user_id
               FROM user_identities u
               LEFT JOIN subscription_snapshots s ON s.internal_user_id = u.internal_user_id
               WHERE u.is_internal = FALSE
               AND u.trial_used = FALSE
               AND u.created_at <= NOW() - make_interval(mins => $1)
               AND u.created_at > NOW() - make_interval(hours => $2)
               AND s.trial_started_at IS NULL
               AND s.plan_id IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = u.internal_user_id
                   AND n.notification_type = $3
               )""",
            _ACTIVATION_NUDGE_DELAY_MINUTES,
            _ACTIVATION_NUDGE_WINDOW_HOURS,
            "trial_activation_nudge_15m",
        )
        if not rows:
            return
        text, keyboard = text_trial_activation_nudge()
        await asyncio.gather(
            *[
                self._send_notification(
                    internal_user_id=row["internal_user_id"],
                    notification_type="trial_activation_nudge_15m",
                    text=text,
                    keyboard=keyboard,
                )
                for row in rows
            ]
        )

    async def _check_trial_connection_nudge(self, now: datetime) -> None:
        """Одно касание активного триала без первого VPN-трафика."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id,
                      COALESCE((
                          SELECT e.platform FROM funnel_interactions e
                          WHERE e.internal_user_id = s.internal_user_id
                            AND e.event_name = 'platform_selected'
                            AND e.occurred_at >= s.trial_started_at
                          ORDER BY e.occurred_at DESC LIMIT 1
                      ), '') AS platform
               FROM subscription_snapshots s
               JOIN user_identities u ON u.internal_user_id = s.internal_user_id
               WHERE u.is_internal = FALSE
               AND s.state_label = 'active'
               AND s.plan_id IS NULL
               AND s.trial_started_at IS NOT NULL
               AND s.trial_started_at <= NOW() - make_interval(mins => $1)
               AND COALESCE(s.active_until_utc, s.trial_expires_at) > NOW()
               AND NOT EXISTS (
                   SELECT 1 FROM bare_xray_traffic_daily t
                   WHERE t.email IN ('user-' || left(s.internal_user_id, 16),
                                     'x-user-' || left(s.internal_user_id, 16))
                     AND t.stat_date >= (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
                     AND t.uplink_bytes + t.downlink_bytes > 0
               )
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = $2
               )""",
            _CONNECTION_NUDGE_DELAY_MINUTES,
            "trial_connection_nudge_15m",
        )
        if not rows:
            return

        async def _notify(row: asyncpg.Record) -> None:
            text, keyboard = text_trial_connection_nudge(platform=row["platform"])
            await self._send_notification(
                internal_user_id=row["internal_user_id"],
                notification_type="trial_connection_nudge_15m",
                text=text,
                keyboard=keyboard,
            )

        await asyncio.gather(*[_notify(row) for row in rows])

    async def _check_checkout_abandoned(self, now: datetime) -> None:
        """Одно напоминание, если checkout триала не завершён за полчаса."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id, checkout.plan_id, checkout.device_count,
                      checkout.payment_method, checkout.surface
               FROM subscription_snapshots s
               JOIN user_identities u ON u.internal_user_id = s.internal_user_id
               JOIN LATERAL (
                   SELECT e.occurred_at AS opened_at, e.plan_id, e.device_count,
                          e.payment_method, e.surface
                   FROM funnel_interactions e
                   WHERE e.internal_user_id = s.internal_user_id
                     AND e.event_name IN ('checkout_opened', 'payment_created')
                     AND e.occurred_at >= s.trial_started_at
                   ORDER BY e.occurred_at DESC
                   LIMIT 1
               ) checkout ON TRUE
               WHERE u.is_internal = FALSE
               AND s.state_label = 'active'
               AND s.plan_id IS NULL
               AND s.trial_started_at IS NOT NULL
               AND checkout.opened_at <= NOW() - make_interval(mins => $1)
               AND checkout.opened_at > NOW() - make_interval(hours => $2)
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = $3
               )""",
            _CHECKOUT_NUDGE_DELAY_MINUTES,
            _CHECKOUT_NUDGE_WINDOW_HOURS,
            "checkout_abandoned_30m",
        )
        if not rows:
            return

        async def _notify(row: asyncpg.Record) -> None:
            plan_id = row["plan_id"]
            device_count = row["device_count"]
            payment_method = row["payment_method"]
            text, keyboard = text_checkout_abandoned(plan_id=plan_id, device_count=device_count)
            resume_path = _checkout_resume_path(plan_id, device_count, payment_method)
            web_payload = None
            if resume_path is not None:
                web_payload = {"action": {"href": resume_path, "label": "Продолжить заказ"}}
            await self._send_notification(
                internal_user_id=row["internal_user_id"],
                notification_type="checkout_abandoned_30m",
                text=text,
                keyboard=keyboard,
                web_payload=web_payload,
            )

        await asyncio.gather(*[_notify(row) for row in rows])

    async def _send_notification(
        self,
        *,
        internal_user_id: str,
        notification_type: str,
        text: str,
        keyboard: dict,
        web_payload: dict | None = None,
    ) -> bool:
        """Двухканальная доставка: веб (verified email) + Telegram (tg_id > 0).

        Дедуп notification_log — ОДНА запись на оба канала (по ней же тики
        пропускают повтор). Возвращает True, если доставлено хотя бы по одному
        каналу. Web-only (tg_id <= 0) раньше фейлились каждый час (TG-400 на
        отрицательный chat_id) — теперь получают веб-уведомление и закрываются
        дедуп-записью.
        """
        row = await self._pool.fetchrow(
            "SELECT i.telegram_user_id, "
            "EXISTS (SELECT 1 FROM user_emails e "
            "        WHERE e.telegram_user_id = i.telegram_user_id AND e.is_verified = TRUE) AS has_email "
            "FROM user_identities i WHERE i.internal_user_id = $1",
            internal_user_id,
        )
        if row is None:
            return False

        telegram_user_id = row["telegram_user_id"]
        has_email = bool(row["has_email"])

        # Holdout контрольной группы: не отправляем, но занимает слот дедупа
        # наблюдаемой записью 'holdout'. Сбой вставки — обычная отправка (fail-open).
        from app.application.notification_holdout import is_notification_holdout

        if is_notification_holdout(internal_user_id, notification_type):
            try:
                await self._pool.execute(
                    """INSERT INTO notification_log (internal_user_id, notification_type, delivery_status)
                       VALUES ($1, $2, 'holdout') ON CONFLICT DO NOTHING""",
                    internal_user_id,
                    notification_type,
                )
                _LOGGER.info(
                    "notification_holdout user=%s type=%s",
                    internal_user_id,
                    notification_type,
                )
                return False
            except Exception:
                _LOGGER.warning(
                    "notification_holdout_write_failed user=%s type=%s",
                    internal_user_id,
                    notification_type,
                )

        # Дедуп общий на оба канала
        existing = await self._pool.fetchval(
            """SELECT 1 FROM notification_log
               WHERE internal_user_id = $1 AND notification_type = $2
               AND sent_date = CURRENT_DATE LIMIT 1""",
            internal_user_id,
            notification_type,
        )
        if existing:
            return False

        delivered = False
        retryable_failure = False
        telegram_terminal_failure = False

        # --- Веб-канал (у кого есть verified email — «может залогиниться») ---
        web_kind = _WEB_KINDS.get(notification_type)
        if has_email and web_kind is not None:
            try:
                kind, title = web_kind
                payload = {"notification_type": notification_type}
                if web_payload:
                    payload.update(web_payload)
                await PostgresWebNotificationRepository(self._pool).insert_one(
                    internal_user_id=internal_user_id,
                    kind=kind,
                    title=title,
                    body=_web_notification_body(text, kind=kind),
                    payload=payload,
                )
                delivered = True
            except Exception:
                retryable_failure = True
                _LOGGER.exception("notification_web_insert_failed user=%s type=%s", internal_user_id, notification_type)

        # --- TG-канал ---
        if telegram_user_id > 0:
            try:
                client = await self._get_http_client()
                tg_text = text
                tg_keyboard = keyboard
                if custom_emoji_enabled_from_env():
                    tg_text = apply_custom_emoji(text, enabled=True)
                    tg_keyboard = apply_keyboard_custom_emoji(keyboard, enabled=True)
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": telegram_user_id,
                        "text": tg_text,
                        "parse_mode": "HTML",
                        "reply_markup": tg_keyboard,
                    },
                )
                if resp.status_code == 403:
                    telegram_terminal_failure = True
                    _LOGGER.info(
                        "notification_send_terminal user=%s type=%s status=403",
                        internal_user_id,
                        notification_type,
                    )
                elif resp.status_code != 200:
                    retryable_failure = True
                    _LOGGER.warning(
                        "notification_send_failed user=%s type=%s status=%d",
                        internal_user_id,
                        notification_type,
                        resp.status_code,
                    )
                else:
                    delivered = True
            except Exception:
                retryable_failure = True
                _LOGGER.debug("notification_send_error user=%s", internal_user_id, exc_info=True)

        terminal_failure = telegram_terminal_failure and not delivered and not retryable_failure
        if not delivered and not terminal_failure:
            # Временный сбой доступного канала — без дедупа, ретрай следующим тиком.
            return False

        # Одна запись на оба канала: успешная доставка либо терминально закрытая попытка.
        await self._pool.execute(
            """INSERT INTO notification_log (internal_user_id, notification_type, delivery_status)
               VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
            internal_user_id,
            notification_type,
            "delivered" if delivered else "terminal_failure",
        )
        return delivered

    async def _check_trial_expiring(self, now: datetime) -> None:
        """Обычное предупреждение в непересекающемся окне (6 ч; 24 ч]."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id,
                      COALESCE(s.active_until_utc, s.trial_expires_at) AS access_expires_at,
                      EXISTS (
                          SELECT 1 FROM bare_xray_traffic_daily t
                          WHERE t.email IN ('user-' || left(s.internal_user_id, 16),
                                            'x-user-' || left(s.internal_user_id, 16))
                            AND t.stat_date >= (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
                            AND t.uplink_bytes + t.downlink_bytes > 0
                      ) AS has_traffic,
                      COALESCE((
                          SELECT e.platform FROM funnel_interactions e
                          WHERE e.internal_user_id = s.internal_user_id
                            AND e.event_name = 'platform_selected'
                            AND e.occurred_at >= s.trial_started_at
                          ORDER BY e.occurred_at DESC LIMIT 1
                      ), '') AS platform
               FROM subscription_snapshots s
               WHERE s.state_label = 'active'
               AND s.trial_expires_at IS NOT NULL
               AND COALESCE(s.active_until_utc, s.trial_expires_at)
                   <= NOW() + make_interval(hours => $1)
               AND COALESCE(s.active_until_utc, s.trial_expires_at)
                   > NOW() + make_interval(hours => $2)
               AND s.plan_id IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = ANY($3::text[])
               )""",
            _TRIAL_WARNING_HOURS,
            _TRIAL_URGENT_HOURS,
            ["trial_expiring", "trial_expiring_24h"],
        )
        if not rows:
            return

        async def _notify(row: asyncpg.Record) -> None:
            text, keyboard = text_trial_expiring(
                active_until=_format_msk_datetime(row["access_expires_at"]),
                has_traffic=bool(row["has_traffic"]),
                platform=row["platform"],
            )
            await self._send_notification(
                internal_user_id=row["internal_user_id"],
                notification_type="trial_expiring_24h",
                text=text,
                keyboard=keyboard,
            )

        await asyncio.gather(*[_notify(row) for row in rows])

    async def _check_trial_expiring_urgent(self, now: datetime) -> None:
        """Срочное предупреждение в окне (0 ч; 6 ч]."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id,
                      COALESCE(s.active_until_utc, s.trial_expires_at) AS access_expires_at,
                      EXISTS (
                          SELECT 1 FROM bare_xray_traffic_daily t
                          WHERE t.email IN ('user-' || left(s.internal_user_id, 16),
                                            'x-user-' || left(s.internal_user_id, 16))
                            AND t.stat_date >= (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
                            AND t.uplink_bytes + t.downlink_bytes > 0
                      ) AS has_traffic,
                      COALESCE((
                          SELECT e.platform FROM funnel_interactions e
                          WHERE e.internal_user_id = s.internal_user_id
                            AND e.event_name = 'platform_selected'
                            AND e.occurred_at >= s.trial_started_at
                          ORDER BY e.occurred_at DESC LIMIT 1
                      ), '') AS platform
               FROM subscription_snapshots s
               WHERE s.state_label = 'active'
               AND s.trial_expires_at IS NOT NULL
               AND COALESCE(s.active_until_utc, s.trial_expires_at) > NOW()
               AND COALESCE(s.active_until_utc, s.trial_expires_at)
                   <= NOW() + make_interval(hours => $1)
               AND s.plan_id IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = $2
               )""",
            _TRIAL_URGENT_HOURS,
            "trial_expiring_6h",
        )
        if not rows:
            return

        async def _notify(row: asyncpg.Record) -> None:
            text, keyboard = text_trial_expiring_6h(
                active_until=_format_msk_datetime(row["access_expires_at"]),
                has_traffic=bool(row["has_traffic"]),
                platform=row["platform"],
            )
            await self._send_notification(
                internal_user_id=row["internal_user_id"],
                notification_type="trial_expiring_6h",
                text=text,
                keyboard=keyboard,
            )

        await asyncio.gather(*[_notify(row) for row in rows])

    async def _check_trial_expired(self, now: datetime) -> None:
        """Завершённые триалы: отозвать ключи и зафиксировать приостановку."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
               FROM subscription_snapshots s
               WHERE s.state_label = 'active'
               AND s.trial_expires_at IS NOT NULL
               AND s.trial_expires_at <= NOW()
               AND s.plan_id IS NULL
               AND s.keys_deactivated_at IS NULL
               AND (s.active_until_utc IS NULL OR s.active_until_utc <= NOW())""",
        )
        for row in rows:
            uid = row["internal_user_id"]
            # Deactivate VLESS keys
            revoke_ok = False
            try:
                result = await self._vless_provider.revoke_user(internal_user_id=uid)
                revoke_ok = result.outcome in (VlessProviderOutcome.SUCCESS, VlessProviderOutcome.NOT_FOUND)
            except Exception:
                _LOGGER.exception("notification_scheduler.revoke_failed user=%s", uid)
            if not revoke_ok:
                _LOGGER.warning("notification_scheduler.revoke_skipped user=%s — will retry next cycle", uid)
                continue
            # Update state only after successful revoke
            await self._pool.execute(
                """UPDATE subscription_snapshots
                   SET state_label = 'expired', keys_deactivated_at = NOW(), updated_at = NOW()
                   WHERE internal_user_id = $1""",
                uid,
            )

    async def _check_trial_expired_paywall(self, now: datetime) -> None:
        """Paywall после фактической деактивации; сбой доставки ретраится следующим тиком."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
               FROM subscription_snapshots s
               WHERE s.state_label = 'expired'
               AND s.trial_expires_at IS NOT NULL
               AND s.plan_id IS NULL
               AND s.keys_deactivated_at IS NOT NULL
               AND s.keys_deactivated_at >= NOW() - make_interval(hours => $1)
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = $2
               )""",
            _TRIAL_PAYWALL_RETRY_HOURS,
            "trial_expired",
        )
        if not rows:
            return
        text, keyboard = text_trial_expired()
        await asyncio.gather(
            *[
                self._send_notification(
                    internal_user_id=row["internal_user_id"],
                    notification_type="trial_expired",
                    text=text,
                    keyboard=keyboard,
                )
                for row in rows
            ]
        )

    async def _check_trial_expired_followup(self, now: datetime) -> None:
        """Одно напоминание через 24–48 часов после приостановки триала."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
               FROM subscription_snapshots s
               WHERE s.state_label = 'expired'
               AND s.trial_expires_at IS NOT NULL
               AND s.plan_id IS NULL
               AND s.keys_deactivated_at IS NOT NULL
               AND s.keys_deactivated_at <= NOW() - make_interval(hours => $1)
               AND s.keys_deactivated_at > NOW() - make_interval(hours => $2)
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = $3
               )""",
            _TRIAL_FOLLOWUP_DELAY_HOURS,
            _TRIAL_FOLLOWUP_WINDOW_HOURS,
            "trial_expired_followup_24h",
        )
        if not rows:
            return
        text, keyboard = text_trial_expired_followup()
        await asyncio.gather(
            *[
                self._send_notification(
                    internal_user_id=row["internal_user_id"],
                    notification_type="trial_expired_followup_24h",
                    text=text,
                    keyboard=keyboard,
                )
                for row in rows
            ]
        )

    async def _check_subscription_expiring(self, now: datetime) -> None:
        """Subscriptions expiring in 3 days: send renewal reminder (batch)."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id, s.active_until_utc
               FROM subscription_snapshots s
               WHERE s.state_label = 'active'
               AND s.plan_id IS NOT NULL
               AND s.active_until_utc > NOW()
               AND s.active_until_utc < NOW() + INTERVAL '3 days'
               AND s.active_until_utc > NOW() + INTERVAL '2 days'
               AND NOT EXISTS (
                   SELECT 1 FROM notification_log n
                   WHERE n.internal_user_id = s.internal_user_id
                   AND n.notification_type = 'subscription_expiring_3d'
                   AND n.sent_at >= s.active_until_utc - INTERVAL '3 days'
                   AND n.sent_at < s.active_until_utc
               )""",
        )
        if not rows:
            return

        async def _notify(row: asyncpg.Record) -> None:
            text, keyboard = text_subscription_expiring(_format_msk_datetime(row["active_until_utc"]))
            await self._send_notification(
                internal_user_id=row["internal_user_id"],
                notification_type="subscription_expiring_3d",
                text=text,
                keyboard=keyboard,
            )

        await asyncio.gather(*[_notify(r) for r in rows])

    async def _check_subscription_expired(self, now: datetime) -> None:
        """Expired subscriptions: deactivate keys, send notification."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
               FROM subscription_snapshots s
               WHERE s.state_label = 'active'
               AND s.plan_id IS NOT NULL
               AND s.active_until_utc <= NOW()
               AND s.keys_deactivated_at IS NULL""",
        )
        for row in rows:
            uid = row["internal_user_id"]
            revoke_ok = False
            try:
                result = await self._vless_provider.revoke_user(internal_user_id=uid)
                revoke_ok = result.outcome in (VlessProviderOutcome.SUCCESS, VlessProviderOutcome.NOT_FOUND)
            except Exception:
                _LOGGER.exception("notification_scheduler.revoke_failed user=%s", uid)
            if not revoke_ok:
                _LOGGER.warning("notification_scheduler.revoke_skipped user=%s — will retry next cycle", uid)
                continue
            await self._pool.execute(
                """UPDATE subscription_snapshots
                   SET state_label = 'expired', keys_deactivated_at = NOW(), updated_at = NOW()
                   WHERE internal_user_id = $1""",
                uid,
            )
            text, keyboard = text_subscription_expired()
            await self._send_notification(
                internal_user_id=uid,
                notification_type="subscription_expired",
                text=text,
                keyboard=keyboard,
            )

    async def _check_keys_grace_period_expired(self, now: datetime) -> None:
        """Keys deactivated >20 days ago: permanently delete."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
               FROM subscription_snapshots s
               WHERE s.keys_deactivated_at IS NOT NULL
               AND s.keys_deleted_at IS NULL
               AND s.keys_deactivated_at < NOW() - INTERVAL '20 days'""",
        )
        for row in rows:
            uid = row["internal_user_id"]
            delete_ok = False
            try:
                result = await self._vless_provider.delete_user(internal_user_id=uid)
                delete_ok = result.outcome in (VlessProviderOutcome.SUCCESS, VlessProviderOutcome.NOT_FOUND)
            except Exception:
                _LOGGER.exception("notification_scheduler.delete_failed user=%s", uid)
            if not delete_ok:
                _LOGGER.warning("notification_scheduler.delete_skipped user=%s — will retry next cycle", uid)
                continue
            await self._pool.execute(
                """UPDATE subscription_snapshots
                   SET keys_deleted_at = NOW(), updated_at = NOW()
                   WHERE internal_user_id = $1""",
                uid,
            )
            text, keyboard = text_keys_deleted()
            await self._send_notification(
                internal_user_id=uid,
                notification_type="keys_deleted",
                text=text,
                keyboard=keyboard,
            )

    async def _check_referral_offer_trial(self, now: datetime) -> None:
        """Активные триалы с первым VPN-трафиком: один реферальный оффер."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
                FROM subscription_snapshots s
                JOIN user_identities u ON u.internal_user_id = s.internal_user_id
                WHERE s.state_label = 'active'
                AND s.plan_id IS NULL
                AND s.trial_expires_at IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM bare_xray_traffic_daily t
                    WHERE t.email IN ('user-' || left(s.internal_user_id, 16),
                                      'x-user-' || left(s.internal_user_id, 16))
                      AND t.stat_date >= (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
                      AND t.uplink_bytes + t.downlink_bytes > 0
                )
                AND NOT EXISTS (
                    SELECT 1 FROM notification_log nl
                    WHERE nl.internal_user_id = s.internal_user_id
                    AND nl.notification_type = 'referral_offer_trial'
                )""",
        )
        if not rows:
            return
        text, keyboard = text_referral_offer_trial()
        await asyncio.gather(
            *[
                self._send_notification(
                    internal_user_id=row["internal_user_id"],
                    notification_type="referral_offer_trial",
                    text=text,
                    keyboard=keyboard,
                )
                for row in rows
            ]
        )

    async def _check_referral_offer_paid(self, now: datetime) -> None:
        """Recent PAID activations (first payment or renewal): send the referral offer once per user.

        Пользователь, уже видевший trial-оффер, не получает второй оффер после оплаты.

        Excludes users whose most recent snapshot update was a FREE grant (grant_subscription_days:
        referral bonus, broadcast gift button, admin add-days). A grant bumps ``updated_at`` and
        sets/keeps ``plan_id`` exactly like a real payment, but the user did NOT pay — so the
        «Спасибо за подписку» offer must not fire. Detected via an admin_subscription_actions audit
        row (action='add_days') for this user whose ``created_at`` coincides with ``updated_at``
        (every grant writes it right after the snapshot upsert). A later genuine payment re-sets
        ``updated_at`` past the grant time, so the offer correctly fires again — the filter is not
        an across-the-board block. Web-only (tg_id <= 0) получают оффер веб-каналом."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
                FROM subscription_snapshots s
                JOIN user_identities u ON u.internal_user_id = s.internal_user_id
                WHERE s.state_label = 'active'
                AND s.plan_id IS NOT NULL
                AND s.updated_at > NOW() - make_interval(hours => $1)
                AND NOT EXISTS (
                    SELECT 1 FROM notification_log nl
                    WHERE nl.internal_user_id = s.internal_user_id
                    AND nl.notification_type = ANY($2::text[])
                )
                AND NOT EXISTS (
                    SELECT 1 FROM admin_subscription_actions a
                    WHERE a.internal_user_id = s.internal_user_id
                    AND a.action = 'add_days'
                    AND a.created_at >= s.updated_at - INTERVAL '5 minutes'
                    AND a.created_at <= s.updated_at + INTERVAL '5 minutes'
                )""",
            _REFERRAL_OFFER_RECENT_HOURS,
            ["referral_offer_trial", "referral_offer_paid"],
        )
        if not rows:
            return
        text, keyboard = text_referral_offer_paid()
        await asyncio.gather(
            *[
                self._send_notification(
                    internal_user_id=row["internal_user_id"],
                    notification_type="referral_offer_paid",
                    text=text,
                    keyboard=keyboard,
                )
                for row in rows
            ]
        )


def start_notification_scheduler(
    *,
    pool: asyncpg.Pool,
    bot_token: str,
    vless_provider: VlessProviderPort,
) -> NotificationScheduler:
    """Create and return a scheduler (caller starts it as an async task)."""
    return NotificationScheduler(pool=pool, bot_token=bot_token, vless_provider=vless_provider)
