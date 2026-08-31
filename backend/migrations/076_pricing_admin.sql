-- Runtime plan pricing: admin-editable prices for the 7 fixed plans + a site-wide
-- global discount ("like a promo code applied to everyone, but without a code").
--
-- Design (spec: docs/superpowers/specs/2026-08-31-tariffs-admin-design.md):
-- - plan_price_overrides: ABSENCE of a row = code default from domain/plans.py::_PLANS.
--   The subscription_plans mirror (migration 051) stays untouched and remains a
--   read-only DB-side reference; runtime authority for the 7 fixed plan prices is
--   THIS table. Never write 051-style price-sync migrations again — they would
--   fight the admin panel.
-- - pricing_global_discount: singleton row (seeded inactive = feature off).
--   No redemption bookkeeping: it is pure pricing config, not a promo code.
-- - admin_pricing_actions: audit trail for operator pricing changes (pattern of
--   migration 064) — actor + old/new values, deliberately separate from money tables.
--
-- Empty overrides + inactive discount = exactly the pre-migration behaviour
-- (additive, backward-compatible, safe to deploy before code).

CREATE TABLE IF NOT EXISTS plan_price_overrides (
    plan_id TEXT PRIMARY KEY
        CHECK (plan_id IN ('1d', '7d', '14d', '1m', '3m', '6m', '365d')),
    price_rubles INTEGER NOT NULL CHECK (price_rubles BETWEEN 1 AND 100000),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pricing_global_discount (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    discount_kind TEXT NULL CHECK (discount_kind IN ('percent', 'fixed_rubles')),
    discount_value INTEGER NULL CHECK (discount_value IS NULL OR discount_value > 0),
    public_label TEXT NULL,
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- An active discount must be fully specified; percent is capped at 100.
    CHECK (
        NOT is_active
        OR (
            discount_kind IS NOT NULL
            AND discount_value IS NOT NULL
            AND (discount_kind <> 'percent' OR discount_value <= 100)
        )
    )
);

INSERT INTO pricing_global_discount (singleton) VALUES (TRUE) ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS admin_pricing_actions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('set_plan_price', 'reset_plan_price', 'set_global_discount')),
    plan_id TEXT NULL,
    old_value JSONB NULL,
    new_value JSONB NULL,
    correlation_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_pricing_actions_created_at
    ON admin_pricing_actions (created_at);
