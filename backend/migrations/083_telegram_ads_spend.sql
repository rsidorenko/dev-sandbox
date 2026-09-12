-- Ручной дневной расход Telegram Ads в RUB. Таблица намеренно отделена от
-- billing_events_ledger: это операторский рекламный ввод, а не платёж клиента.
-- Пустые таблицы не меняют существующую аналитику и безопасны для выкладки до кода.

CREATE TABLE IF NOT EXISTS telegram_ads_spend_daily (
    campaign_key TEXT NOT NULL
        CHECK (campaign_key ~ '^[a-z0-9][a-z0-9_-]{0,47}$'),
    spend_date DATE NOT NULL,
    amount_kopecks BIGINT NOT NULL CHECK (amount_kopecks > 0),
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_key, spend_date)
);

CREATE INDEX IF NOT EXISTS idx_telegram_ads_spend_daily_date
    ON telegram_ads_spend_daily (spend_date, campaign_key);

CREATE TABLE IF NOT EXISTS telegram_ads_spend_actions (
    id BIGSERIAL PRIMARY KEY,
    campaign_key TEXT NOT NULL,
    spend_date DATE NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('set', 'clear')),
    old_amount_kopecks BIGINT NULL CHECK (old_amount_kopecks IS NULL OR old_amount_kopecks > 0),
    new_amount_kopecks BIGINT NULL CHECK (new_amount_kopecks IS NULL OR new_amount_kopecks > 0),
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telegram_ads_spend_actions_created_at
    ON telegram_ads_spend_actions (created_at);
