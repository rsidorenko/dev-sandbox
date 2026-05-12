-- Two-level referral tracking.
CREATE TABLE IF NOT EXISTS referral_relationships (
    relationship_id TEXT NOT NULL PRIMARY KEY,
    referred_user_id TEXT NOT NULL,
    referrer_user_id TEXT NOT NULL,
    level INT NOT NULL CHECK (level IN (1, 2)),
    referrer_of_referrer_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_referral_referred_user UNIQUE (referred_user_id)
);

CREATE INDEX IF NOT EXISTS idx_referral_rel_referrer ON referral_relationships (referrer_user_id);
CREATE INDEX IF NOT EXISTS idx_referral_rel_referrer_of_referrer ON referral_relationships (referrer_of_referrer_user_id);
