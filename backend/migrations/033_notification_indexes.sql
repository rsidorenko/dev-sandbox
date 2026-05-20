-- Partial composite indexes for notification_scheduler queries on subscription_snapshots.
-- All are WHERE-filtered partial indexes: zero impact on writes, additive only.

-- 1. Trial expiring in <24h (notification_scheduler._check_trial_expiring)
CREATE INDEX IF NOT EXISTS idx_snap_trial_expiring
    ON subscription_snapshots (trial_expires_at)
    WHERE state_label = 'active'
      AND trial_expires_at IS NOT NULL
      AND plan_id IS NULL;

-- 2. Trial expired, keys not yet deactivated (notification_scheduler._check_trial_expired)
CREATE INDEX IF NOT EXISTS idx_snap_trial_expired
    ON subscription_snapshots (trial_expires_at)
    WHERE state_label = 'active'
      AND trial_expires_at IS NOT NULL
      AND plan_id IS NULL
      AND keys_deactivated_at IS NULL;

-- 3. Subscription expiring in ~3 days (notification_scheduler._check_subscription_expiring)
CREATE INDEX IF NOT EXISTS idx_snap_sub_expiring
    ON subscription_snapshots (active_until_utc)
    WHERE state_label = 'active'
      AND plan_id IS NOT NULL;

-- 4. Subscription expired, keys not yet deactivated (notification_scheduler._check_subscription_expired)
CREATE INDEX IF NOT EXISTS idx_snap_sub_expired
    ON subscription_snapshots (active_until_utc)
    WHERE state_label = 'active'
      AND plan_id IS NOT NULL
      AND keys_deactivated_at IS NULL;

-- 5. Grace period: keys deactivated >20 days, not yet deleted (notification_scheduler._check_keys_grace_period_expired)
CREATE INDEX IF NOT EXISTS idx_snap_grace_period
    ON subscription_snapshots (keys_deactivated_at)
    WHERE keys_deactivated_at IS NOT NULL
      AND keys_deleted_at IS NULL;
