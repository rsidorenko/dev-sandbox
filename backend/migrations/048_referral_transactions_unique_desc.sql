-- Ensure atomic dedup for referral commission credits.
-- Without this unique index, concurrent inserts with the same (internal_user_id, description)
-- can both pass the NOT EXISTS check and cause double commission credits.
CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_tx_user_desc
    ON referral_transactions (internal_user_id, description);
