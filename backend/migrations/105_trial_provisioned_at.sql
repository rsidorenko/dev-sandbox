-- Исходное начало известно только до первого переноса часов по VPN-трафику.
ALTER TABLE subscription_snapshots ADD COLUMN IF NOT EXISTS trial_provisioned_at timestamptz;
UPDATE subscription_snapshots
SET trial_provisioned_at = trial_started_at
WHERE trial_provisioned_at IS NULL AND trial_started_at IS NOT NULL AND trial_first_traffic_at IS NULL;
