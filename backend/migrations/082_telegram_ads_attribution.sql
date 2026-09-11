-- Неизменяемая first-touch атрибуция Telegram Ads. Исторические источники не бэкфиллим.
CREATE TABLE IF NOT EXISTS telegram_ads_attributions (
    internal_user_id TEXT PRIMARY KEY
        REFERENCES user_identities (internal_user_id) ON DELETE CASCADE,
    campaign_key TEXT NOT NULL
        CHECK (campaign_key ~ '^[a-z0-9][a-z0-9_-]{0,47}$'),
    telegram_update_id BIGINT NOT NULL CHECK (telegram_update_id >= 0),
    attributed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_telegram_ads_attributions_campaign_time
    ON telegram_ads_attributions (campaign_key, attributed_at);
