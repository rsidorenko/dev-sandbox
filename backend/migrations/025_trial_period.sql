-- Trial period: 3-day free VPN for new users.
ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMPTZ NULL;

ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMPTZ NULL;

-- Track whether a user has used their trial (one-time only).
ALTER TABLE user_identities
    ADD COLUMN IF NOT EXISTS trial_used BOOLEAN NOT NULL DEFAULT FALSE;
