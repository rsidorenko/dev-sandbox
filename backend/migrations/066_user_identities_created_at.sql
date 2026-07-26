-- Registration timestamp for user identities.
-- Исторически колонки created_at у user_identities не было → админ-вкладка «Статистика»
-- (метрика «новые пользователи» по дням) не могла считаться. Добавляем колонку и
-- бэкфиллим существующие строки наиболее ранней известной активностью пользователя
-- (первый accepted-платёж → старт триала → now). Новые регистрации получают now() по дефолту.
-- Миграция идемпотентна на уровне schema_migration_ledger (прогон один раз по чексумме).

ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE user_identities ui
SET created_at = COALESCE(
    (SELECT MIN(b.event_received_at)
       FROM billing_events_ledger b
      WHERE b.internal_user_id = ui.internal_user_id AND b.status = 'accepted'),
    (SELECT s.trial_started_at
       FROM subscription_snapshots s
      WHERE s.internal_user_id = ui.internal_user_id),
    now()
);

CREATE INDEX IF NOT EXISTS idx_user_identities_created_at ON user_identities (created_at);
