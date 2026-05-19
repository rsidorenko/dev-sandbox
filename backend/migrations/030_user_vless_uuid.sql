-- Store VLESS UUID per user so keys change on regeneration instead of being deterministic.
ALTER TABLE user_identities ADD COLUMN IF NOT EXISTS vless_uuid TEXT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_identities_vless_uuid ON user_identities (vless_uuid) WHERE vless_uuid IS NOT NULL;
