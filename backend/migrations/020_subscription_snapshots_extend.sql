-- Extend subscription snapshots with plan and device count.
ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS plan_id TEXT NULL;

ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS device_count INT NOT NULL DEFAULT 5;
