-- Add Cloudflare CDN (WS) transport for Helsinki server.
-- WS inbound (id=6) listens on port 2087 with TLS (self-signed cert accepted by CF Full mode).
-- VLESS link uses CDN domain on port 2087 (Cloudflare edge proxies to origin:2087).
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    inbound_id, transport_type, is_active
) SELECT
    'Хельсинки ☁️4.0', 'FI', '🇫🇮', 'fi.techno-channel.ru', 2087,
    '/ws', 'fi.techno-channel.ru', panel_url, panel_username, panel_password,
    6, 'cdn', TRUE
FROM vpn_servers
WHERE server_host = '77.221.159.106' AND transport_type = 'tcp'
LIMIT 1;
