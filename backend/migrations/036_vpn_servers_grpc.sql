-- Add gRPC transport support to vpn_servers.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS transport_type TEXT NOT NULL DEFAULT 'tcp';
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS grpc_service_name TEXT NOT NULL DEFAULT '';

-- Insert gRPC variant of the Helsinki server (same host, different port/inbound).
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    inbound_id, is_active, encrypted_password,
    reality_pbk, reality_sid, reality_sni,
    transport_type, grpc_service_name
) VALUES (
    'Хельсинки-1-gRPC', 'FI', '🇫🇮', '77.221.159.106', 8443,
    '/ws', NULL, 'https://77.221.159.106:2053', 'admin', '',
    2, TRUE, 'enc:v1:IjJYaUaD8Z3IENpFJ3bJa1Nja34HOHH/cvCwWpdgiv04Zx5Lr+Lq3pRd/f8g9A==',
    '7qXsyMNeJp563XA6WE9_pP1esQIHWeFeIlRuWyyXa30', 'f928a5163b67c858', 'eh.vk.ru',
    'grpc', 'grpc'
);
