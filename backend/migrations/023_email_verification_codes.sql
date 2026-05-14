-- Email verification codes (6-digit, TTL 10 minutes).
CREATE TABLE IF NOT EXISTS email_verification_codes (
    id TEXT NOT NULL PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'auth',
    telegram_user_id BIGINT,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 5,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_verification_codes_email_purpose
    ON email_verification_codes (email, purpose)
    WHERE used_at IS NULL;
