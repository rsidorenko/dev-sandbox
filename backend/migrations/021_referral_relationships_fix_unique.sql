-- Fix UNIQUE constraint: allow L1 + L2 for same referred_user_id.
ALTER TABLE referral_relationships DROP CONSTRAINT IF EXISTS uq_referral_referred_user;
CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_referred_user_level ON referral_relationships (referred_user_id, level);
