-- VPN server registry: stores 3x-ui panel endpoints for VLESS key management.
CREATE TABLE IF NOT EXISTS vpn_servers (
    id SERIAL PRIMARY KEY,
    label TEXT NOT NULL,
    country_code TEXT NOT NULL,
    country_flag TEXT NOT NULL,
    server_host TEXT NOT NULL,
    server_port INT NOT NULL DEFAULT 443,
    ws_path TEXT NOT NULL DEFAULT '/ws',
    tls_sni TEXT,
    panel_url TEXT NOT NULL,
    panel_username TEXT NOT NULL,
    panel_password TEXT NOT NULL,
    inbound_id INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for active server lookups
CREATE INDEX IF NOT EXISTS idx_vpn_servers_active ON vpn_servers (is_active) WHERE is_active = TRUE;
