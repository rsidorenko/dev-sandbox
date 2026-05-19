-- Subscription token for per-user web subscription URL (e.g. /sub/{token}).
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS subscription_token TEXT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_identities_sub_token ON user_identities (subscription_token) WHERE subscription_token IS NOT NULL;
