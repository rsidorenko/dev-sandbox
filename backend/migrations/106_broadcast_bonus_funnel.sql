-- Только события явно атрибутированного быстрого Telegram-checkout.
CREATE TABLE IF NOT EXISTS broadcast_bonus_funnel (
    job_id uuid NOT NULL REFERENCES broadcast_jobs(job_id) ON DELETE CASCADE,
    internal_user_id text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('bonus_claimed', 'plan_selected', 'payment_created')),
    event_key text NOT NULL,
    payment_id text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, internal_user_id, stage, event_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_broadcast_bonus_payment
ON broadcast_bonus_funnel(payment_id) WHERE payment_id IS NOT NULL;
