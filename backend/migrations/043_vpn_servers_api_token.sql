-- Add api_token column for 3x-ui Bearer token authentication (v2+ panels).
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS api_token TEXT NOT NULL DEFAULT '';
