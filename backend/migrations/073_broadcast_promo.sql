-- Кнопка промокода в рассылке: реестр получателей + TTL (gate «только получатели» + срок годности).
-- additive only: новая таблица, отдельная от broadcast_bonus_claims (там CHECK days 3..30 и status-машина
-- для atomic grant бесплатных дней; promo — это скидка, apply идемпотентен сам по себе через
-- promo_applied PK=internal_user_id, поэтому status-машина здесь не нужна).
-- bonus = {"type":"promo","code":"...","label":"...","ttl_days":N} (скаляр, один бонус на рассылку).
CREATE TABLE IF NOT EXISTS broadcast_promo_claims (
    job_id           UUID        NOT NULL REFERENCES broadcast_jobs(job_id) ON DELETE CASCADE,
    telegram_user_id BIGINT      NOT NULL,
    code             TEXT        NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (job_id, telegram_user_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_promo_claims_expires ON broadcast_promo_claims(expires_at);
