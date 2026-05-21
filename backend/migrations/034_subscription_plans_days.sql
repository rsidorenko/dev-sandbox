-- Add duration_days column and new subscription plans (day-based durations).

-- Drop the strict duration_months > 0 check to allow day-only plans
ALTER TABLE subscription_plans DROP CONSTRAINT IF EXISTS subscription_plans_duration_months_check;

-- Add duration_days column
ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS duration_days INT;

-- Backfill existing rows from duration_months
UPDATE subscription_plans SET duration_days = duration_months * 30 WHERE duration_days IS NULL;

-- Make duration_days NOT NULL after backfill
ALTER TABLE subscription_plans ALTER COLUMN duration_days SET NOT NULL;
ALTER TABLE subscription_plans ALTER COLUMN duration_days SET DEFAULT 30;

-- Add a new check: duration_days must be positive
ALTER TABLE subscription_plans ADD CONSTRAINT subscription_plans_duration_days_check CHECK (duration_days > 0);

-- Insert new day-based plans
INSERT INTO subscription_plans (plan_id, duration_months, duration_days, price_rubles, default_device_limit, extra_device_price_rubles) VALUES
    ('1d',  0, 1,   12,   5, 80),
    ('7d',  0, 7,   99,   5, 80),
    ('14d', 0, 14,  169,  5, 80),
    ('365d', 0, 365, 2199, 5, 80)
ON CONFLICT (plan_id) DO UPDATE SET
    duration_days = EXCLUDED.duration_days,
    price_rubles = EXCLUDED.price_rubles;

-- Update existing plans to set correct duration_days and prices
UPDATE subscription_plans SET duration_days = 30,  price_rubles = 249  WHERE plan_id = '1m';
UPDATE subscription_plans SET duration_days = 90,  price_rubles = 699  WHERE plan_id = '3m';
UPDATE subscription_plans SET duration_days = 180, price_rubles = 1259 WHERE plan_id = '6m';
