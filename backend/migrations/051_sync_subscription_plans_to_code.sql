-- Sync the subscription_plans table to the authoritative plan definitions in
-- app.domain.plans (_PLANS). The table is NOT read by the application — plans come
-- from code — but it is kept as a DB-side reference mirror so ad-hoc DB queries
-- show correct, complete tariffs. Previously it was missing the month-based plans
-- (1m/3m/6m) entirely; this upsert makes it a faithful, idempotent mirror.
--
-- Authoritative source of truth: backend/src/app/domain/plans.py
-- Prices/durations below MUST match _PLANS there.
--
-- Safe to re-run: pure INSERT ... ON CONFLICT DO UPDATE, no destructive ops,
-- on a table no application code reads.

INSERT INTO subscription_plans
    (plan_id, duration_months, duration_days, price_rubles,
     default_device_limit, extra_device_price_rubles, is_active)
VALUES
    ('1d',   0,   1,   12,   5, 80, TRUE),
    ('7d',   0,   7,   99,   5, 80, TRUE),
    ('14d',  0,   14,  169,  5, 80, TRUE),
    ('1m',   1,   30,  249,  5, 80, TRUE),
    ('3m',   3,   90,  699,  5, 80, TRUE),
    ('6m',   6,   180, 1259, 5, 80, TRUE),
    ('365d', 0,   365, 2199, 5, 80, TRUE)
ON CONFLICT (plan_id) DO UPDATE SET
    duration_months           = EXCLUDED.duration_months,
    duration_days             = EXCLUDED.duration_days,
    price_rubles              = EXCLUDED.price_rubles,
    default_device_limit      = EXCLUDED.default_device_limit,
    extra_device_price_rubles = EXCLUDED.extra_device_price_rubles,
    is_active                 = EXCLUDED.is_active;
