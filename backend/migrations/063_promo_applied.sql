-- Применённые (но ещё не погашенные) промокоды пользователей.
-- См. PromoAppliedRecord / PostgresPromoAppliedRepository.
--
-- Хранится в БД (а не in-memory), т.к. деплой делает force-recreate контейнера бэкенда
-- на каждом мердже в main — in-memory словари сбрасывались бы, и пользователь, введя
-- промокод в главном меню и выбрав тариф, терял бы скидку в момент оплаты.
--
-- Ключ — internal_user_id (один активный применённый промокод на пользователя).
-- expires_at — TTL применённого промокода (например now + 24ч); просроченные игнорируются.
-- ВНИМАНИЕ: это только «намерение» пользователя. Каноническая скидка пересчитывается при
-- оплате из актуальной строки promo_codes (повторная проверка активности/срока/лимита/аудитории),
-- этому полю доверять для денег нельзя.
CREATE TABLE IF NOT EXISTS promo_applied (
    internal_user_id TEXT NOT NULL PRIMARY KEY,
    code TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_promo_applied_expires ON promo_applied (expires_at);
