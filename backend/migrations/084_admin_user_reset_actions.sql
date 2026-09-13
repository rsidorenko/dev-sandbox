-- Неизменяемый аудит полного сброса тестовых аккаунтов из админки.
-- Таблица намеренно не ссылается FK на user_identities: identity удаляется самим действием.
CREATE TABLE IF NOT EXISTS admin_user_reset_actions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    telegram_user_id BIGINT NOT NULL,
    internal_user_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
    vpn_outcome TEXT NOT NULL CHECK (vpn_outcome IN ('success', 'not_found')),
    deleted_rows JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_admin_user_reset_actions_created_at
    ON admin_user_reset_actions (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_user_reset_actions_telegram_user
    ON admin_user_reset_actions (telegram_user_id, created_at DESC);
