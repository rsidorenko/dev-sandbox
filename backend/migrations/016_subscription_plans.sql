-- Subscription plans: durations and pricing.
CREATE TABLE IF NOT EXISTS subscription_plans (
    plan_id TEXT NOT NULL PRIMARY KEY,
    duration_months INT NOT NULL CHECK (duration_months > 0),
    price_rubles INT NOT NULL CHECK (price_rubles > 0),
    default_device_limit INT NOT NULL DEFAULT 5 CHECK (default_device_limit > 0),
    extra_device_price_rubles INT NOT NULL DEFAULT 80 CHECK (extra_device_price_rubles >= 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO subscription_plans (plan_id, duration_months, price_rubles, default_device_limit, extra_device_price_rubles) VALUES
    ('1m', 1, 300, 5, 80),
    ('3m', 3, 750, 5, 80),
    ('6m', 6, 1350, 5, 80)
ON CONFLICT (plan_id) DO NOTHING;
