-- Минимальный контекст для возврата в незавершённый заказ. URL платёжного
-- провайдера и секреты намеренно не хранятся: checkout пересоздаётся штатным flow.
ALTER TABLE funnel_interactions
    ADD COLUMN IF NOT EXISTS plan_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS device_count INTEGER NULL,
    ADD COLUMN IF NOT EXISTS payment_method TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'funnel_interactions_plan_id_check'
    ) THEN
        ALTER TABLE funnel_interactions
            ADD CONSTRAINT funnel_interactions_plan_id_check
            CHECK (plan_id IS NULL OR length(plan_id) BETWEEN 1 AND 32) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'funnel_interactions_device_count_check'
    ) THEN
        ALTER TABLE funnel_interactions
            ADD CONSTRAINT funnel_interactions_device_count_check
            CHECK (device_count IS NULL OR device_count BETWEEN 5 AND 20) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'funnel_interactions_payment_method_check'
    ) THEN
        ALTER TABLE funnel_interactions
            ADD CONSTRAINT funnel_interactions_payment_method_check
            CHECK (payment_method IS NULL OR payment_method IN ('card', 'crypto')) NOT VALID;
    END IF;
END
$$;

ALTER TABLE funnel_interactions VALIDATE CONSTRAINT funnel_interactions_plan_id_check;
ALTER TABLE funnel_interactions VALIDATE CONSTRAINT funnel_interactions_device_count_check;
ALTER TABLE funnel_interactions VALIDATE CONSTRAINT funnel_interactions_payment_method_check;

CREATE INDEX IF NOT EXISTS idx_funnel_checkout_recovery_latest
    ON funnel_interactions (internal_user_id, occurred_at DESC)
    WHERE event_name IN ('checkout_opened', 'payment_created');
