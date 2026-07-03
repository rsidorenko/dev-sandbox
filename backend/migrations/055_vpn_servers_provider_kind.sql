-- Distinguish 3x-ui panel servers ('xui') from bare-xray relay-entry servers
-- ('bare_xray'). A 'bare_xray' server has no 3x-ui panel API: the provider skips
-- panel CRUD for it and builds its /sub/ link directly from the row; user UUIDs on
-- it are mirrored by a periodic desired-state job (see scripts/mirror_cloudru_clients.py).
-- Use case: the Cloud.ru RU-relay entry that serves Happ (must stay bare-xray because
-- 3x-ui's realitySettings breaks the xtls-rprx-vision flow for Xray-core/Happ).
-- Existing fleet defaults to 'xui' (no behaviour change).
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS provider_kind TEXT NOT NULL DEFAULT 'xui';
