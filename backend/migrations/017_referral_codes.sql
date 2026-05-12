-- Unique referral code for each user.
CREATE TABLE IF NOT EXISTS referral_codes (
    internal_user_id TEXT NOT NULL PRIMARY KEY,
    referral_code TEXT NOT NULL UNIQUE CHECK (char_length(referral_code) >= 4 AND char_length(referral_code) <= 32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON referral_codes (referral_code);
