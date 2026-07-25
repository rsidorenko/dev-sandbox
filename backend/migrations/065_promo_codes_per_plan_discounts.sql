-- Per-tariff скидки промокодов: один код может задавать отдельную скидку для
-- каждого фиксированного тарифа (1d/7d/14d/1m/3m/6m/365d), кроме «Свой».
-- discounts_by_plan = {plan_id: value}, напр. {"1m":10,"3m":15,"365d":20}.
--
-- Две взаимоисключающие моды:
--   * плоская (legacy/единая): скидка в discount_value, discounts_by_plan IS NULL;
--   * per-tariff: discount_value IS NULL, discounts_by_plan непустой.
-- discount_value становится nullable (CHECK >0 пропускает NULL). Валидация
-- значений map (ключи ⊇ фиксированных plan_id, percent<=100) — в app-слое
-- (domain.promo.validate_discounts_by_plan), т.к. SQL-проверка JSONB избыточно сложна.
-- См. PromoCode / apply_promo_discount / PostgresPromoCodeRepository.
ALTER TABLE promo_codes ALTER COLUMN discount_value DROP NOT NULL;
ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS discounts_by_plan JSONB;
