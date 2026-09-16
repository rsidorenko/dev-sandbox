-- Первый подтверждённый трафик один раз переносит начало триала на фактическое
-- подключение. Nullable-колонка сохраняет прежнее поведение при выключенном
-- сборщике трафика и не меняет lifecycle платных подписок.
ALTER TABLE subscription_snapshots
    ADD COLUMN IF NOT EXISTS trial_first_traffic_at TIMESTAMPTZ NULL;

-- Не выдаём повторные три дня уже подключавшимся активным триалам при rollout.
-- Резолв email выполняется только вперёд тем же способом, что и в runtime.
UPDATE subscription_snapshots s
SET trial_first_traffic_at = s.trial_started_at
WHERE s.trial_started_at IS NOT NULL
  AND s.trial_first_traffic_at IS NULL
  AND s.state_label = 'active'
  AND s.plan_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM bare_xray_traffic_daily t
      WHERE t.email IN (
          'user-' || left(s.internal_user_id, 16),
          'x-user-' || left(s.internal_user_id, 16)
      )
        AND t.stat_date >= (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
        AND t.uplink_bytes + t.downlink_bytes > 0
  );

CREATE INDEX IF NOT EXISTS idx_subscription_trial_waiting_first_traffic
    ON subscription_snapshots (trial_expires_at)
    WHERE state_label = 'active'
      AND plan_id IS NULL
      AND trial_started_at IS NOT NULL
      AND trial_first_traffic_at IS NULL;
