-- Добавочное состояние плавной ротации; существующие UUID и служебные email не меняются.
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_email_seed TEXT;
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_previous_email_seed TEXT;
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_rotation_phase TEXT
    CHECK (vless_rotation_phase IN ('provisioning', 'waiting', 'retiring'));
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_first_traffic_at TIMESTAMPTZ;
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_rotation_checked_at TIMESTAMPTZ;

-- История нужна для атрибуции трафика после удаления старого поколения.
CREATE TABLE IF NOT EXISTS vless_key_emails (
    email TEXT PRIMARY KEY,
    internal_user_id TEXT NOT NULL REFERENCES user_identities(internal_user_id) ON DELETE CASCADE,
    transport_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vless_key_emails_user ON vless_key_emails(internal_user_id);
