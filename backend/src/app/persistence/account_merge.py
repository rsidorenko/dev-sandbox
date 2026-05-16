"""Merge a web-only account (telegram_user_id=0) into the real Telegram identity.

When a Telegram user links an email that was previously registered on the website
(web-only, no Telegram), all data from the phantom web account must be rekeyed
to the real Telegram identity.  This module provides a single async function that
does exactly that, inside a single database transaction.
"""

from __future__ import annotations

import hashlib
import logging

import asyncpg

_LOGGER = logging.getLogger(__name__)


def _web_internal_id(email: str) -> str:
    """Derive the internal_user_id used for web-only registrations."""
    return f"web_{hashlib.sha256(email.encode()).hexdigest()[:12]}"


def _telegram_internal_id(telegram_user_id: int) -> str:
    """Derive the internal_user_id for a Telegram user."""
    return f"u{telegram_user_id}"


async def merge_web_account_if_needed(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    email: str,
) -> bool:
    """Detect a web-only account for *email* and merge it into *telegram_user_id*.

    Returns True if a merge was performed, False if nothing to merge.

    All updates run inside a single transaction so the merge is atomic.
    """
    old_id = _web_internal_id(email)
    new_id = _telegram_internal_id(telegram_user_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Check if a web-only row exists for this email
            web_row = await conn.fetchrow(
                "SELECT telegram_user_id FROM user_emails"
                " WHERE email = $1 AND is_verified = TRUE AND telegram_user_id = 0",
                email,
            )
            if web_row is None:
                return False

            # Check that old_id actually has data — if not, just fix the email row
            has_data = await conn.fetchval(
                "SELECT 1 FROM subscription_snapshots WHERE internal_user_id = $1",
                old_id,
            )
            if not has_data:
                has_data = await conn.fetchval(
                    "SELECT 1 FROM referral_codes WHERE internal_user_id = $1",
                    old_id,
                )

            if has_data:
                # Check if Telegram identity already has a subscription
                tg_has_sub = await conn.fetchval(
                    "SELECT 1 FROM subscription_snapshots WHERE internal_user_id = $1",
                    new_id,
                )

                if tg_has_sub:
                    # Both have subscriptions — prefer Telegram, log warning
                    _LOGGER.warning(
                        "account_merge_conflict: both web=%s and tg=%s have subscriptions, preferring telegram",
                        old_id, new_id,
                    )
                    # Delete the web subscription to avoid constraint violations
                    await conn.execute(
                        "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
                        old_id,
                    )
                else:
                    await conn.execute(
                        "UPDATE subscription_snapshots SET internal_user_id = $1 WHERE internal_user_id = $2",
                        new_id, old_id,
                    )

                # Migrate issuance state
                await conn.execute(
                    "UPDATE issuance_state SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id, old_id,
                )

                # Migrate billing data
                await conn.execute(
                    "UPDATE billing_events_ledger SET internal_user_id = $1"
                    " WHERE internal_user_id = $2 AND internal_user_id IS NOT NULL",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE billing_subscription_apply_audit_events SET internal_user_id = $1"
                    " WHERE internal_user_id = $2 AND internal_user_id IS NOT NULL",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE billing_subscription_apply_records SET internal_user_id = $1"
                    " WHERE internal_user_id = $2",
                    new_id, old_id,
                )

                # Migrate referral data
                await conn.execute(
                    "UPDATE referral_codes SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE referral_balances SET internal_user_id = $1 WHERE internal_user_id = $2"
                    " AND NOT EXISTS (SELECT 1 FROM referral_balances WHERE internal_user_id = $1)",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE referral_transactions SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE referral_relationships SET referred_user_id = $1 WHERE referred_user_id = $2",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE referral_relationships SET referrer_user_id = $1 WHERE referrer_user_id = $2",
                    new_id, old_id,
                )
                await conn.execute(
                    "UPDATE referral_relationships SET referrer_of_referrer_user_id = $1"
                    " WHERE referrer_of_referrer_user_id = $2",
                    new_id, old_id,
                )

            # Reassign the email row from web-only (telegram_user_id=0) to real user
            await conn.execute(
                "UPDATE user_emails SET telegram_user_id = $1"
                " WHERE email = $2 AND telegram_user_id = 0",
                telegram_user_id,
                email,
            )

            _LOGGER.info(
                "account_merge: merged web=%s into tg=%s for email=***",
                old_id, new_id,
            )
            return True
