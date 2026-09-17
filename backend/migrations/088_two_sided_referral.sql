-- Версионирование экономики реферальных связей и одноразовый бонус приглашённому.
-- Существующие связи сохраняют legacy-условия; новые получают balanced_v1.

ALTER TABLE referral_relationships
    ADD COLUMN IF NOT EXISTS reward_policy TEXT;

UPDATE referral_relationships
SET reward_policy = 'legacy_stack'
WHERE reward_policy IS NULL;

ALTER TABLE referral_relationships
    ALTER COLUMN reward_policy SET DEFAULT 'balanced_v1';

ALTER TABLE referral_relationships
    ALTER COLUMN reward_policy SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'referral_relationships_reward_policy_check'
    ) THEN
        ALTER TABLE referral_relationships
            ADD CONSTRAINT referral_relationships_reward_policy_check
            CHECK (reward_policy IN ('legacy_stack', 'balanced_v1')) NOT VALID;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS referral_invitee_bonus_awards (
    award_id TEXT NOT NULL PRIMARY KEY,
    referred_user_id TEXT NOT NULL,
    referrer_user_id TEXT NOT NULL,
    days_awarded INTEGER NOT NULL CHECK (days_awarded > 0),
    related_plan_id TEXT NOT NULL,
    correlation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_referral_invitee_bonus_referred UNIQUE (referred_user_id)
);

CREATE INDEX IF NOT EXISTS idx_referral_invitee_bonus_referrer
    ON referral_invitee_bonus_awards (referrer_user_id, created_at DESC);
