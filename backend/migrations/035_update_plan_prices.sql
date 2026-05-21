-- Update plan prices to new values.

-- Update day-based plan prices
UPDATE subscription_plans SET price_rubles = 12   WHERE plan_id = '1d';
UPDATE subscription_plans SET price_rubles = 99   WHERE plan_id = '7d';
UPDATE subscription_plans SET price_rubles = 169  WHERE plan_id = '14d';
UPDATE subscription_plans SET price_rubles = 2199 WHERE plan_id = '365d';

-- Update month-based plan prices
UPDATE subscription_plans SET price_rubles = 249  WHERE plan_id = '1m';
UPDATE subscription_plans SET price_rubles = 699  WHERE plan_id = '3m';
UPDATE subscription_plans SET price_rubles = 1259 WHERE plan_id = '6m';
