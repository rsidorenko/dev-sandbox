-- Основа аналитики: только дополнительные поля, таблицы и read-only представление.
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS is_internal BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS analytics_internal_audit (
    id BIGSERIAL PRIMARY KEY,
    internal_user_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
    old_value BOOLEAN NOT NULL,
    new_value BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS funnel_interactions (
    id BIGSERIAL PRIMARY KEY,
    internal_user_id TEXT NOT NULL,
    event_name TEXT NOT NULL CHECK (event_name IN (
        'setup_started', 'platform_selected', 'connection_confirmed',
        'connection_problem', 'checkout_opened', 'payment_created'
    )),
    surface TEXT NOT NULL CHECK (surface IN ('telegram', 'web')),
    platform TEXT NOT NULL DEFAULT '' CHECK (platform IN ('', 'ios', 'android', 'mac', 'win', 'tv')),
    dedup_key TEXT NOT NULL CHECK (length(dedup_key) BETWEEN 1 AND 200),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (surface, event_name, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_funnel_interactions_user_time
    ON funnel_interactions (internal_user_id, occurred_at);

-- Факты жизненного цикла уже зафиксированы сервером. Не добавляем триггеры
-- в критические операции регистрации, триала и биллинга.
CREATE OR REPLACE VIEW funnel_events AS
    SELECT internal_user_id, 'registered'::text AS event_name,
           created_at AS occurred_at, 'server'::text AS surface,
           ''::text AS platform, 'identity:' || internal_user_id AS event_key
    FROM user_identities
    UNION ALL
    SELECT internal_user_id, 'trial_started', trial_started_at, 'server', '',
           'trial:' || internal_user_id
    FROM subscription_snapshots WHERE trial_started_at IS NOT NULL
    UNION ALL
    SELECT internal_user_id, 'payment_succeeded', event_effective_at, 'server', '',
           'payment:' || internal_fact_ref
    FROM billing_events_ledger
    WHERE status = 'accepted' AND event_type = 'subscription_activated'
  AND billing_provider_key IN ('yookassa', 'yookassa_v1', 'cryptobot', 'cryptobot_v1')
      AND internal_fact_ref NOT LIKE 'grant:%'
      AND amount_minor_units > 0 AND currency_code = 'RUB'
      AND internal_user_id IS NOT NULL
    UNION ALL
    SELECT internal_user_id, event_name, occurred_at, surface, platform,
           'interaction:' || id::text
    FROM funnel_interactions;
