-- Email addresses linked to Telegram accounts.
-- Supports email auth on website and email binding via bot.
CREATE TABLE IF NOT EXISTS user_emails (
    id TEXT NOT NULL PRIMARY KEY DEFAULT gen_random_uuid()::text,
    telegram_user_id BIGINT NOT NULL REFERENCES user_identities (telegram_user_id),
    email TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at TIMESTAMPTZ,
    UNIQUE (telegram_user_id, email)
);

-- Fast lookup: find user by verified email (for web auth).
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_emails_email_verified
    ON user_emails (email) WHERE is_verified = TRUE;

-- Fast lookup: all emails for a telegram user.
CREATE INDEX IF NOT EXISTS idx_user_emails_telegram_user_id
    ON user_emails (telegram_user_id);
