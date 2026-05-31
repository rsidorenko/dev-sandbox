-- Add Frankfurt VPN server entries (TCP+Reality, XHTTP+Reality).
-- Frankfurt node: 77.110.100.210, 3x-ui panel on port 2053.
-- Reality keys: pbk=Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do, sni=www.microsoft.com

-- 1) TCP + Reality inbound (id=1 on Frankfurt panel, port 443)
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    inbound_id, reality_pbk, reality_sid, reality_sni,
    transport_type, is_active
) VALUES (
    'Франкфурт 🔒 1.0', 'DE', '🇩🇪', '77.110.100.210', 443,
    '/ws', '', 'https://77.110.100.210:2053', 'admin', 'PLACEHOLDER_FRANKFURT_PANEL_PASSWORD',
    1, 'Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do', 'a1b2c3d4e5f6', 'www.microsoft.com',
    'tcp', TRUE
);

-- 2) XHTTP + Reality inbound (id=2 on Frankfurt panel, port 8443)
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    inbound_id, reality_pbk, reality_sid, reality_sni,
    transport_type, is_active
) VALUES (
    'Франкфурт ⚡2.0', 'DE', '🇩🇪', '77.110.100.210', 8443,
    'bravada-xhttp', '', 'https://77.110.100.210:2053', 'admin', 'PLACEHOLDER_FRANKFURT_PANEL_PASSWORD',
    2, 'Q_wpt7L8sU2O1OVBV-mpsSvgLAChIhN4hgTm0XZH4Do', 'a1b2c3d4e5f6', 'www.microsoft.com',
    'xhttp', TRUE
);
