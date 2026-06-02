-- Yandex Cloud relay entry for Frankfurt: VLESS+Reality -> relay to Frankfurt exit.
-- Users connect to Russian IP (51.250.65.51) with SNI=eh.vk.com, traffic relays to Frankfurt.
-- Password is stored in plaintext here; operator should encrypt via config doctor.
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    inbound_id, reality_pbk, reality_sid, reality_sni,
    transport_type, api_token, is_active
) VALUES (
    'Франкфурт 📶 LTE', 'DE', '🇷🇺🇩🇪', '51.250.65.51', 443,
    '/ws', NULL,
    'https://51.250.65.51:54023/Cq6xxAccNLaSEBcR0L',
    'qRyulczB26', 'GdHso7dQaX',
    1,
    'IGuuHdGY1oEamKKN9SRG4m89XFd3HpGhKYCEIhjR8Gc', '', 'eh.vk.com',
    'tcp', 'CYMolU6rVUzEyAj1VGj0WHpYZmB0ILivLDBMKQNPV7SDDM3w',
    TRUE
);
