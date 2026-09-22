-- NULL сохраняет все существующие ключи; seed меняется только при перевыпуске.
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_key_seed TEXT;
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_rotation_pending BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_previous_key_seed TEXT;
