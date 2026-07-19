-- Promo/discount codes. Personal codes (e.g. BRAVADA-<referral_code>) are generated
-- when a trial is about to expire and redeemed at web checkout (see web_api/payment.py).
-- Idempotent: safe to re-run. Additive only — no changes to existing tables.
CREATE TABLE IF NOT EXISTS promo_codes (
    code                    TEXT PRIMARY KEY,
    owner_internal_user_id  TEXT,                     -- NULL = generic campaign code
    discount_percent        INT  NOT NULL,            -- 0..100
    valid_from              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until             TIMESTAMPTZ,              -- NULL = no expiry
    max_uses                INT  NOT NULL DEFAULT 1,
    uses_count              INT  NOT NULL DEFAULT 0,
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promo_codes_owner ON promo_codes (owner_internal_user_id);
