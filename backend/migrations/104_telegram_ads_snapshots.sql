-- Накопительные срезы кабинета хранятся отдельно от дневного расхода в рублях.
CREATE TABLE IF NOT EXISTS telegram_ads_snapshots (
    source_ref text PRIMARY KEY,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
