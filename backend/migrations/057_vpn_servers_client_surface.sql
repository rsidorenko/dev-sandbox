-- Per-row /sub/ surface: which client import gets this server's link.
-- 'default' = the foreign 3x-ui fleet (Karing and any non-Happ client), the existing behaviour.
-- 'happ' = served only when the importing client is Happ (e.g. the Cloud.ru bare_xray relay
-- entries and the h1cloud CDN whitelist-bypass key whose `extra` obfuscation is Xray-core-only).
-- Drives VlessServerConfig.surface in the provider; /sub/ filters by it. Defaults to 'default'
-- so all existing fleet rows keep their current behaviour.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS client_surface TEXT NOT NULL DEFAULT 'default';
