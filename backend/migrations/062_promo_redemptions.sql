-- Журнал погашений промокодов (append-only). См. PromoRedemptionRecord / PostgresPromoRedemptionRepository.
-- Идемпотентность: UNIQUE(idempotency_key) — повторная попытка погашения не дублирует запись.
-- Один код на пользователя: UNIQUE(code, internal_user_id) — жёсткое правило «один раз на юзера»
-- (по умолчанию для промокодов; достаточно для MVP: блогер-промокод используется подписчиком однократно).
-- base_rubles/discounted_rubles/discount_kopecks — snapshot математики скидки на момент погашения
-- (для аудита; канонический пересчёт всё равно идёт через domain.promo.apply_promo_discount).
CREATE TABLE IF NOT EXISTS promo_redemptions (
    redemption_id TEXT NOT NULL PRIMARY KEY,
    code TEXT NOT NULL,
    internal_user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    base_rubles INTEGER NOT NULL CHECK (base_rubles >= 0),
    discounted_rubles INTEGER NOT NULL CHECK (discounted_rubles >= 0),
    discount_kopecks INTEGER NOT NULL CHECK (discount_kopecks >= 0),
    payment_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT promo_redemptions_idem_uniq UNIQUE (idempotency_key),
    CONSTRAINT promo_redemptions_user_code_uniq UNIQUE (code, internal_user_id)
);

CREATE INDEX IF NOT EXISTS idx_promo_redemptions_user
    ON promo_redemptions (internal_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_promo_redemptions_code
    ON promo_redemptions (code, created_at DESC);
