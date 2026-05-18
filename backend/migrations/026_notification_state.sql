-- Notification state: prevents duplicate notifications and tracks sent notifications.
CREATE TABLE IF NOT EXISTS notification_log (
    id SERIAL PRIMARY KEY,
    internal_user_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prevent duplicate notifications per user per type per day (use date column for IMMUTABLE index)
ALTER TABLE notification_log ADD COLUMN IF NOT EXISTS sent_date DATE NOT NULL DEFAULT CURRENT_DATE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_log_dedup
    ON notification_log (internal_user_id, notification_type, sent_date);

-- Key lifecycle tracking
ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS keys_deactivated_at TIMESTAMPTZ NULL;

ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS keys_deleted_at TIMESTAMPTZ NULL;
