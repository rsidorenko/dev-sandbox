-- Лимит активаций одного промокода одним пользователем.
--   NULL = неограничено; 1 = единожды (INTEGER допускает будущее N-на-юзера без миграции).
-- Глобальный лимит по-прежнему max_redemptions / redemptions_count.
-- См. domain.promo.per_user_exhausted и _redeem_atomic в application/promo_service.py.
ALTER TABLE promo_codes
    ADD COLUMN IF NOT EXISTS max_redemptions_per_user INTEGER NULL
    CHECK (max_redemptions_per_user IS NULL OR max_redemptions_per_user >= 1);

-- Существующие коды → «единожды» (решение оператора 2026-08-19): закрывает лазейку,
-- когда после погашения пользователь мог применить тот же код и получить скидку повторно.
UPDATE promo_codes SET max_redemptions_per_user = 1 WHERE max_redemptions_per_user IS NULL;

-- «Один код на юзера» больше не абсолютное правило журнала — режим задаётся колонкой выше.
-- Per-user лимит инфорсится кодом под advisory-локом в _redeem_atomic
-- (COUNT → INSERT → INCREMENT сериализованы на пользователя), идемпотентность платежей
-- остаётся на UNIQUE(idempotency_key).
ALTER TABLE promo_redemptions
    DROP CONSTRAINT IF EXISTS promo_redemptions_user_code_uniq;

-- Индекс для per-user COUNT/EXISTS проверок (применение/оплата/погашение).
CREATE INDEX IF NOT EXISTS idx_promo_redemptions_code_user
    ON promo_redemptions (code, internal_user_id);
