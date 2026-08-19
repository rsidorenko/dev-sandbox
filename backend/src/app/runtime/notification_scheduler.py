"""Background notification scheduler for subscription/trial lifecycle events.

Runs as an async task alongside the bot polling loop.
Checks every 60 minutes for:
1. Trials expiring in <24h
2. Trials expired → deactivate keys
3. Subscriptions expiring in 3 days
4. Subscriptions expired → deactivate keys
5. Subscriptions expired >20 days → permanently delete keys
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime

import asyncpg
import httpx

from app.bot_transport.notification_texts import (
    text_keys_deleted,
    text_referral_offer_paid,
    text_referral_offer_trial,
    text_subscription_expired,
    text_subscription_expiring,
    text_trial_expired,
    text_trial_expiring,
)
from app.issuance.vless_provider import VlessProviderOutcome, VlessProviderPort

_LOGGER = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = int(os.environ.get("NOTIFICATION_CHECK_INTERVAL_SECONDS", "3600"))
_EXPIRY_WARNING_HOURS = int(os.environ.get("NOTIFICATION_EXPIRY_WARNING_HOURS", "72"))
_GRACE_PERIOD_DAYS = int(os.environ.get("NOTIFICATION_GRACE_PERIOD_DAYS", "20"))
_ADVISORY_LOCK_ID = 20260608
# Окно «недавней активации» для реферального оффера: 25 ч > 24 ч каденции тиков,
# чтобы каждого недавно активировавшего пользователя (триал или оплата) поймал хотя бы один тик.
_REFERRAL_OFFER_RECENT_HOURS = 25


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
        """Main loop: run checks every hour."""
        self._running = True
        while self._running:
            try:
                await self._run_checks()
            except Exception:
                _LOGGER.exception("notification_scheduler_check_error")
            await self._sleep(60 * 60)  # 1 hour

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

        await self._check_trial_expiring(now)
        await self._check_trial_expired(now)
        await self._check_subscription_expiring(now)
        await self._check_subscription_expired(now)
        await self._check_keys_grace_period_expired(now)
        if _referral_offer_enabled():
            await self._check_referral_offer_trial(now)
            await self._check_referral_offer_paid(now)

    async def _send_notification(
        self,
        *,
        internal_user_id: str,
        notification_type: str,
        text: str,
        keyboard: dict,
    ) -> bool:
        """Send a Telegram message and log it. Returns True if sent successfully."""
        # Get telegram_user_id
        row = await self._pool.fetchrow(
            "SELECT telegram_user_id FROM user_identities WHERE internal_user_id = $1",
            internal_user_id,
        )
        if row is None:
            return False

        telegram_user_id = row["telegram_user_id"]

        # Check dedup
        existing = await self._pool.fetchval(
            """SELECT 1 FROM notification_log
               WHERE internal_user_id = $1 AND notification_type = $2
               AND sent_date = CURRENT_DATE LIMIT 1""",
            internal_user_id,
            notification_type,
        )
        if existing:
            return False

        # Send via Telegram Bot API
        try:
            client = await self._get_http_client()
            resp = await client.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={
                    "chat_id": telegram_user_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard,
                },
            )
            if resp.status_code != 200:
                _LOGGER.warning(
                    "notification_send_failed user=%s type=%s status=%d",
                    internal_user_id,
                    notification_type,
                    resp.status_code,
                )
                return False
        except Exception:
            _LOGGER.debug("notification_send_error user=%s", internal_user_id, exc_info=True)
            return False

        # Log notification
        await self._pool.execute(
            """INSERT INTO notification_log (internal_user_id, notification_type)
               VALUES ($1, $2) ON CONFLICT DO NOTHING""",
            internal_user_id,
            notification_type,
        )
        return True

    async def _check_trial_expiring(self, now: datetime) -> None:
        """Trials expiring in <24h: send warning (batch)."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id, s.trial_expires_at
               FROM subscription_snapshots s
               WHERE s.state_label = 'active'
               AND s.trial_expires_at IS NOT NULL
               AND s.trial_expires_at > NOW()
               AND s.trial_expires_at < NOW() + INTERVAL '24 hours'
               AND s.plan_id IS NULL""",
        )
        if not rows:
            return
        text, keyboard = text_trial_expiring()
        await asyncio.gather(
            *[
                self._send_notification(
                    internal_user_id=row["internal_user_id"],
                    notification_type="trial_expiring",
                    text=text,
                    keyboard=keyboard,
                )
                for row in rows
            ]
        )

    async def _check_trial_expired(self, now: datetime) -> None:
        """Expired trials: deactivate keys, send notification."""
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
            text, keyboard = text_trial_expired()
            await self._send_notification(
                internal_user_id=uid,
                notification_type="trial_expired",
                text=text,
                keyboard=keyboard,
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
               AND s.active_until_utc > NOW() + INTERVAL '2 days'""",
        )
        if not rows:
            return

        async def _notify(row: asyncpg.Record) -> None:
            text, keyboard = text_subscription_expiring(row["active_until_utc"].date().isoformat())
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
        """Recent TRIAL activations: send the referral offer once per user (once-ever via NOT EXISTS).

        Window = _REFERRAL_OFFER_RECENT_HOURS (>1 tick cadence) so each freshly-activated trial user is
        caught by at least one hourly tick. Web-only users (no positive telegram_user_id) are filtered out."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
                FROM subscription_snapshots s
                JOIN user_identities u ON u.internal_user_id = s.internal_user_id
                WHERE s.state_label = 'active'
                AND s.plan_id IS NULL
                AND s.trial_expires_at IS NOT NULL
                AND s.updated_at > NOW() - make_interval(hours => $1)
                AND u.telegram_user_id > 0
                AND NOT EXISTS (
                    SELECT 1 FROM notification_log nl
                    WHERE nl.internal_user_id = s.internal_user_id
                    AND nl.notification_type = 'referral_offer_trial'
                )""",
            _REFERRAL_OFFER_RECENT_HOURS,
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

        Different text from the trial offer, so a trial→paid user gets TWO distinct pushes, never a repeat.
        Once-ever via NOT EXISTS (renewal/reactivation won't re-send).

        Excludes users whose most recent snapshot update was a FREE grant (grant_subscription_days:
        referral bonus, broadcast gift button, admin add-days). A grant bumps ``updated_at`` and
        sets/keeps ``plan_id`` exactly like a real payment, but the user did NOT pay — so the
        «Спасибо за подписку» offer must not fire. Detected via an admin_subscription_actions audit
        row (action='add_days') for this user whose ``created_at`` coincides with ``updated_at``
        (every grant writes it right after the snapshot upsert). A later genuine payment re-sets
        ``updated_at`` past the grant time, so the offer correctly fires again — the filter is not
        an across-the-board block."""
        rows = await self._pool.fetch(
            """SELECT s.internal_user_id
                FROM subscription_snapshots s
                JOIN user_identities u ON u.internal_user_id = s.internal_user_id
                WHERE s.state_label = 'active'
                AND s.plan_id IS NOT NULL
                AND s.updated_at > NOW() - make_interval(hours => $1)
                AND u.telegram_user_id > 0
                AND NOT EXISTS (
                    SELECT 1 FROM notification_log nl
                    WHERE nl.internal_user_id = s.internal_user_id
                    AND nl.notification_type = 'referral_offer_paid'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM admin_subscription_actions a
                    WHERE a.internal_user_id = s.internal_user_id
                    AND a.action = 'add_days'
                    AND a.created_at >= s.updated_at - INTERVAL '5 minutes'
                    AND a.created_at <= s.updated_at + INTERVAL '5 minutes'
                )""",
            _REFERRAL_OFFER_RECENT_HOURS,
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
