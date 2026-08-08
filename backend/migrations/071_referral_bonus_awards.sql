-- Referral "free month per friend" awards (separate ledger from cashback).
--
-- When an invited user makes their FIRST payment of a qualifying plan (>= 1 month:
-- 1m/3m/6m/365d), the direct (L1) referrer is granted bonus subscription DAYS
-- (REFERRAL_BONUS_DAYS = 30, see app/domain/referral.py). This is DISTINCT from the
-- L1/L2 percent cashback credited to referral_balances/referral_transactions — both
-- rewards coexist. Bonus days are granted via grant_subscription_days() (same path as
-- operator add-days) and audited in admin_subscription_actions, NOT in
-- billing_events_ledger (free grant — must not distort revenue/reconciliation).
--
-- Idempotency: UNIQUE (referrer_user_id, payer_user_id) guarantees the bonus is awarded
-- AT MOST ONCE per (referrer, friend) pair — exactly-once-across-replays. A replayed
-- payment webhook never reaches the award code (outer is_new_active gate in
-- process_fulfillment), and even a second qualifying payment by the same friend is a no-op.

CREATE TABLE IF NOT EXISTS referral_bonus_awards (
    award_id TEXT NOT NULL PRIMARY KEY,
    referrer_user_id TEXT NOT NULL,
    payer_user_id TEXT NOT NULL,
    level INT NOT NULL CHECK (level = 1),
    days_awarded INT NOT NULL CHECK (days_awarded > 0),
    related_plan_id TEXT NOT NULL,
    correlation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_referral_bonus_referrer_payer UNIQUE (referrer_user_id, payer_user_id)
);

-- Lookup "how many bonus days has this referrer earned" (cabinet/bot counter).
CREATE INDEX IF NOT EXISTS idx_referral_bonus_referrer
    ON referral_bonus_awards (referrer_user_id);
