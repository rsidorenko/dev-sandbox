-- Add Cloudflare CDN (WS) transport for Helsinki server.
-- WS inbound (id=6) listens on port 80 without TLS (Cloudflare Flexible SSL terminates TLS at edge).
-- VLESS link uses CDN domain on port 443 (Cloudflare edge).
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    inbound_id, transport_type, is_active
) SELECT
    'Хельсинки ☁️4.0', 'FI', '🇫🇮', 'fi.techno-channel.ru', 443,
    '/ws', 'fi.techno-channel.ru', panel_url, panel_username, panel_password,
    7, 'cdn', TRUE
FROM vpn_servers
WHERE server_host = '77.221.159.106' AND transport_type = 'tcp'
LIMIT 1;
