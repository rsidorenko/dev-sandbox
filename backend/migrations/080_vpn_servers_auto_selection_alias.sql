-- Дополнительная ссылка «Автовыбор» на существующем bare_xray inbound.
-- Нулевой route означает, что alias выключен; миграция не меняет текущие строки.
ALTER TABLE vpn_servers
    ADD COLUMN IF NOT EXISTS auto_selection_route INTEGER NOT NULL DEFAULT 0;

ALTER TABLE vpn_servers
    ADD COLUMN IF NOT EXISTS auto_selection_label TEXT NOT NULL DEFAULT '';

ALTER TABLE vpn_servers
    ADD COLUMN IF NOT EXISTS auto_selection_country_code TEXT NOT NULL DEFAULT '';

ALTER TABLE vpn_servers
    ADD COLUMN IF NOT EXISTS auto_selection_country_flag TEXT NOT NULL DEFAULT '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_vpn_servers_auto_selection_route'
    ) THEN
        ALTER TABLE vpn_servers
            ADD CONSTRAINT chk_vpn_servers_auto_selection_route
            CHECK (auto_selection_route = 0 OR auto_selection_route BETWEEN 61440 AND 65535);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vpn_servers_auto_selection_route_per_host
    ON vpn_servers (server_host, auto_selection_route)
    WHERE auto_selection_route <> 0;
