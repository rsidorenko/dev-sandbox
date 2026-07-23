-- Durable audit trail for operator admin actions on subscriptions (e.g. add-days grant).
--
-- Deliberately SEPARATE from billing_events_ledger: these are FREE operator grants that
-- MUST NOT be counted as revenue or picked up by billing reconciliation. Like the ADM-02
-- ensure-access audit (migration 012), this stores a bounded operator marker + target +
-- before/after + the UNCHANGED plan (recorded for audit, never modified by add-days), so
-- the action is fully attributable without polluting money tables.
CREATE TABLE IF NOT EXISTS admin_subscription_actions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    internal_user_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('add_days')),
    days INTEGER NOT NULL CHECK (days > 0),
    old_active_until TIMESTAMPTZ NULL,
    new_active_until TIMESTAMPTZ NOT NULL,
    plan_id TEXT NULL,
    device_count INTEGER NULL,
    provisioning TEXT NULL,
    correlation_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_subscription_actions_created_at
    ON admin_subscription_actions (created_at);

CREATE INDEX IF NOT EXISTS idx_admin_subscription_actions_user
    ON admin_subscription_actions (internal_user_id, created_at DESC);
