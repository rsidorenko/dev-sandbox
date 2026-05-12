-- Referral balance (in kopecks) and transaction log.
CREATE TABLE IF NOT EXISTS referral_balances (
    internal_user_id TEXT NOT NULL PRIMARY KEY,
    balance_kopecks BIGINT NOT NULL DEFAULT 0 CHECK (balance_kopecks >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS referral_transactions (
    transaction_id TEXT NOT NULL PRIMARY KEY,
    internal_user_id TEXT NOT NULL,
    amount_kopecks BIGINT NOT NULL,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('referral_credit', 'subscription_payment')),
    related_user_id TEXT,
    related_plan_id TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_referral_tx_user ON referral_transactions (internal_user_id, created_at DESC);
