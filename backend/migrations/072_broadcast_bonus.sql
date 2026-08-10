-- Бонус-кнопка рассылки: конфиг бонуса на job + таблица claims (eligible→claimed).
-- additive only: новая nullable JSONB-колонка на broadcast_jobs + новая таблица.
-- bonus = {"type":"days","days":N,"label":"...","ttl_days":N} (scalar, один бонус на рассылку;
-- type="promo" зарезервирован под следующий PR).
ALTER TABLE broadcast_jobs ADD COLUMN IF NOT EXISTS bonus JSONB NULL;

-- Одна строка на пару (рассылка, получатель). Заполняется строго реальными получателями
-- при отправке (BroadcastRunner, до цикла отправки) — этим обеспечивается «только получатели».
-- Atomic UPDATE eligible→claimed даёт идемпотентность (двойной клик / пересылка → UPDATE 0).
CREATE TABLE IF NOT EXISTS broadcast_bonus_claims (
    job_id           UUID        NOT NULL REFERENCES broadcast_jobs(job_id) ON DELETE CASCADE,
    telegram_user_id BIGINT      NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'eligible' CHECK (status IN ('eligible', 'claimed')),
    days             INTEGER     NOT NULL CHECK (days BETWEEN 3 AND 30),
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at       TIMESTAMPTZ NULL,
    PRIMARY KEY (job_id, telegram_user_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_bonus_claims_status ON broadcast_bonus_claims(status);
