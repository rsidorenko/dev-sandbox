"""Merge a web-only account into the real Telegram identity.

When a Telegram user links an email that was previously registered on the website
(web-only, no Telegram), all data from the phantom web account must be rekeyed
to the real Telegram identity.  This module provides a single async function that
does exactly that, inside a single database transaction.

Web-only accounts are identified by a NON-POSITIVE ``telegram_user_id``: the
web-registration path (``app.web_api.auth``) assigns sequential negative IDs via
``web_user_id_seq`` (``-1, -2, ...``).  Real Telegram user IDs are always
positive, so ``telegram_user_id <= 0`` unambiguously means "web-only, no real
Telegram identity".  (An earlier scheme used a literal ``0``; ``<= 0`` covers
both, so any legacy rows are merged too.)

Conflict invariant: the real Telegram identity **wins** every conflict. If the
Telegram account already has its own subscription / referral code / referrer /
balance, the web account's competing row is **discarded** (never grafted on top,
never raises a unique/PK violation). So a user who already paid via Telegram
keeps that subscription and its keys intact; only data the Telegram identity lacks
(e.g. a web referral attribution, or a web subscription when the Telegram side has
none) is adopted. The merge must never raise — email linking must always succeed.
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
            # Check if a web-only row exists for this email (non-positive telegram_user_id)
            web_row = await conn.fetchrow(
                "SELECT telegram_user_id FROM user_emails"
                " WHERE email = $1 AND is_verified = TRUE AND telegram_user_id <= 0",
                email,
            )
            if web_row is None:
                return False

            # ------------------------------------------------------------------
            # Migrate the web account's data onto the real identity. Invariant: the
            # real Telegram identity (new_id) WINS every conflict — if it already
            # owns a row of a kind, the web account's competing row is discarded
            # (never grafted, never raises). A user who already paid via Telegram
            # keeps that subscription/keys; only data the Telegram identity lacks is
            # adopted. All statements are idempotent so this never throws.
            # ------------------------------------------------------------------

            # --- Subscription: prefer the ACTIVE one; tie -> Telegram wins ---
            web_state = await conn.fetchval(
                "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                old_id,
            )
            tg_state = await conn.fetchval(
                "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                new_id,
            )
            adopt_web = False  # True => web's sub/billing/issuance become Telegram's
            if web_state == "active" and tg_state != "active":
                # Web has the only LIVE subscription -> it becomes the real identity's.
                if tg_state is not None:
                    await conn.execute(
                        "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
                        new_id,
                    )
                await conn.execute(
                    "UPDATE subscription_snapshots SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id, old_id,
                )
                adopt_web = True
            elif web_state is not None and tg_state is not None:
                # Both have a subscription (Telegram active, or neither active) ->
                # Telegram wins; discard the web subscription.
                _LOGGER.warning(
                    "account_merge_conflict: both web=%s and tg=%s have subscriptions; keeping telegram",
                    old_id, new_id,
                )
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
                    old_id,
                )
            elif web_state is not None:
                # Only the web account has a subscription -> adopt it.
                await conn.execute(
                    "UPDATE subscription_snapshots SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id, old_id,
                )
                adopt_web = True

            # --- Issuance + billing: migrate ONLY when adopting the web
            # subscription. Otherwise the Telegram identity already has its own
            # (real payment) and the web account's stale rows are left orphaned
            # on old_id rather than grafted on top. ---
            if adopt_web:
                # issuance_state PK is (internal_user_id, issue_idempotency_key): drop
                # web rows whose key already exists for Telegram, then move the rest.
                await conn.execute(
                    "DELETE FROM issuance_state WHERE internal_user_id = $1"
                    " AND issue_idempotency_key IN"
                    " (SELECT issue_idempotency_key FROM issuance_state WHERE internal_user_id = $2)",
                    old_id, new_id,
                )
                await conn.execute(
                    "UPDATE issuance_state SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id, old_id,
                )
                # Billing tables key on event/fact id (not internal_user_id) -> safe move.
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

            # --- Referral: always migrate (idempotent, Telegram wins conflicts) ---
            # referral_codes PK = internal_user_id -> drop web's if Telegram has one.
            await conn.execute(
                "DELETE FROM referral_codes WHERE internal_user_id = $1"
                " AND EXISTS (SELECT 1 FROM referral_codes WHERE internal_user_id = $2)",
                old_id, new_id,
            )
            await conn.execute(
                "UPDATE referral_codes SET internal_user_id = $1 WHERE internal_user_id = $2",
                new_id, old_id,
            )
            # referral_balances PK = internal_user_id -> adopt web's only if Telegram has none.
            await conn.execute(
                "UPDATE referral_balances SET internal_user_id = $1 WHERE internal_user_id = $2"
                " AND NOT EXISTS (SELECT 1 FROM referral_balances WHERE internal_user_id = $1)",
                new_id, old_id,
            )
            # referral_transactions UNIQUE(internal_user_id, description) -> drop
            # web rows whose description already exists for Telegram, then move the rest.
            await conn.execute(
                "DELETE FROM referral_transactions WHERE internal_user_id = $1"
                " AND description IN"
                " (SELECT description FROM referral_transactions WHERE internal_user_id = $2)",
                old_id, new_id,
            )
            await conn.execute(
                "UPDATE referral_transactions SET internal_user_id = $1 WHERE internal_user_id = $2",
                new_id, old_id,
            )
            # referral_relationships UNIQUE(referred_user_id, level) -> for the
            # "referred" side, drop web rows at levels Telegram already has.
            await conn.execute(
                "DELETE FROM referral_relationships WHERE referred_user_id = $1"
                " AND level IN"
                " (SELECT level FROM referral_relationships WHERE referred_user_id = $2)",
                old_id, new_id,
            )
            await conn.execute(
                "UPDATE referral_relationships SET referred_user_id = $1 WHERE referred_user_id = $2",
                new_id, old_id,
            )
            # "referrer" / "referrer_of_referrer" sides have no unique constraint -> safe move.
            await conn.execute(
                "UPDATE referral_relationships SET referrer_user_id = $1 WHERE referrer_user_id = $2",
                new_id, old_id,
            )
            await conn.execute(
                "UPDATE referral_relationships SET referrer_of_referrer_user_id = $1"
                " WHERE referrer_of_referrer_user_id = $2",
                new_id, old_id,
            )

            # Reassign the email row from the web-only account (non-positive id) to the
            # real Telegram identity. Must run BEFORE the caller inserts its own
            # (telegram_user_id, email) row, otherwise the partial unique index
            # idx_user_emails_email_verified (one verified row per email) is violated.
            await conn.execute(
                "UPDATE user_emails SET telegram_user_id = $1"
                " WHERE email = $2 AND telegram_user_id <= 0",
                telegram_user_id,
                email,
            )

            _LOGGER.info(
                "account_merge: merged web=%s into tg=%s for email=***",
                old_id, new_id,
            )
            return True
