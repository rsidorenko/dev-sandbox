-- Промокоды: определения кодов (админ-управляемые). См. PromoCodeRecord / PostgresPromoCodeRepository.
-- audience: new_users (только новые) | existing_users (только не новые).
-- discount_kind: percent (1..100) | fixed_rubles (>0 рублей).
-- max_redemptions: NULL = без глобального лимита; redemptions_count растёт при погашении.
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT NOT NULL PRIMARY KEY,
    audience TEXT NOT NULL CHECK (audience IN ('new_users', 'existing_users')),
    discount_kind TEXT NOT NULL CHECK (discount_kind IN ('percent', 'fixed_rubles')),
    discount_value INTEGER NOT NULL CHECK (discount_value > 0),
    max_redemptions INTEGER,
    redemptions_count INTEGER NOT NULL DEFAULT 0 CHECK (redemptions_count >= 0),
    expires_at TIMESTAMPTZ NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    note TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (char_length(code) BETWEEN 3 AND 32),
    CHECK (discount_kind <> 'percent' OR discount_value <= 100)
);

CREATE INDEX IF NOT EXISTS idx_promo_codes_active_created
    ON promo_codes (is_active, created_at DESC);
