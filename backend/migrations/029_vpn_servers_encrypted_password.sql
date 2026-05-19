-- Add encrypted_password column for panel credentials.
-- The old panel_password column is kept for backward compatibility during migration.
-- After migrating data, operators should set panel_password to empty string.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS encrypted_password TEXT NOT NULL DEFAULT '';
