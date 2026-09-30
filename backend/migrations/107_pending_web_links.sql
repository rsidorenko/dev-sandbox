-- Новые временные ссылки изолированы от существующих аккаунтов и VPN-ключей.
CREATE SEQUENCE IF NOT EXISTS web_user_id_seq START WITH -1 INCREMENT BY -1 MINVALUE -999999999;
CREATE TABLE IF NOT EXISTS pending_web_links (
    internal_user_id TEXT PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL UNIQUE DEFAULT nextval('web_user_id_seq'),
    subscription_token TEXT NOT NULL UNIQUE,
    referral_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '48 hours'),
    promoted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS pending_web_links_expiry ON pending_web_links(expires_at)
    WHERE promoted_at IS NULL;
CREATE TABLE IF NOT EXISTS pending_web_payment_attempts (
    attempt_id TEXT PRIMARY KEY,
    internal_user_id TEXT NOT NULL REFERENCES pending_web_links(internal_user_id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('yookassa', 'cryptobot')),
    provider_payment_id TEXT,
    status TEXT NOT NULL DEFAULT 'creating' CHECK (status IN ('creating','pending','succeeded','canceled')),
    amount_kopecks BIGINT NOT NULL CHECK (amount_kopecks > 0),
    offer_version TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fulfilled_at TIMESTAMPTZ,
    UNIQUE(provider, provider_payment_id)
);
CREATE INDEX IF NOT EXISTS pending_web_payment_owner ON pending_web_payment_attempts(internal_user_id);
CREATE TABLE IF NOT EXISTS web_offer_acceptances (
    acceptance_id BIGSERIAL PRIMARY KEY,
    internal_user_id TEXT NOT NULL,
    offer_version TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    plan_id TEXT NOT NULL,
    amount_kopecks BIGINT NOT NULL
);
