-- Add TTL to subscription tokens: existing tokens get 90 days from migration run.
-- New tokens and reissued tokens get fresh TTL from application code.
ALTER TABLE user_identities
    ADD COLUMN IF NOT EXISTS subscription_token_expires_at TIMESTAMPTZ NULL;

-- Backfill: existing tokens without expiry get 90 days from now.
UPDATE user_identities
    SET subscription_token_expires_at = NOW() + INTERVAL '90 days'
    WHERE subscription_token IS NOT NULL
      AND subscription_token_expires_at IS NULL;
