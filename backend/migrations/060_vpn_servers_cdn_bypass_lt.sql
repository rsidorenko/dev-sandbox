-- Second Yandex-Cloud-CDN whitelist-bypass server (provider_kind='bare_xray'), egressing via
-- Lithuania. A sibling of id=34 ("Обход белых списков", cdn.strimingset.ru -> origin 158.160.149.191
-- -> Frankfurt). This one points at the new CDN front cdn.strimingset.online -> origin 89.169.135.48
-- (GCP VM 'whitelist2', SSH user romtan / key romtan_key.pem) -> relay 2.59.162.163 (Lithuania
-- 3x-ui inbound :10444) -> internet (egress 2.59.162.163).
--
-- Cloned from id=34: same provider_kind='bare_xray', xray_inbound_tag='from-yandex-cdn',
-- client_surface='happ' (Happ-only, like id=34). link_template is copied from id=34 and then
-- adapted (.ru -> .online host/sni/host + the ", Литва" fragment). Per-row ssh_user/ssh_key_path
-- (mig 059) are set here -- romtan_key.pem is NOT the CLOUDRU_SSH_KEY, so this row overrides env.
--
-- Created is_active=FALSE: hidden from /sub/ and skipped by reconcile_bare_xray until the
-- cdn.strimingset.online CDN resource is live and the origin gRPC API is verified. Flip to TRUE
-- via a follow-up UPDATE when ready. Idempotent guard by (server_host, xray_inbound_tag).
INSERT INTO vpn_servers (
    label, country_code, country_flag, server_host, server_port,
    ws_path, tls_sni, panel_url, panel_username, panel_password,
    encrypted_password, inbound_id, reality_pbk, reality_sid, reality_sni, reality_fp,
    transport_type, api_token, link_template, provider_kind, xray_inbound_tag,
    client_surface, panel_flavor, ssh_user, ssh_key_path, is_active
) SELECT
    'Обход белых списков, Литва', '', '🌐',
    '89.169.135.48', 443,
    'api-test', NULL, '', '', '',
    '', 0, '', '', '', 'firefox',
    'xhttp', '', link_template, 'bare_xray', 'from-yandex-cdn',
    'happ', 'standard', 'romtan', '/opt/bravada/romtan_key.pem', FALSE
FROM vpn_servers
WHERE id = 34
  AND NOT EXISTS (
      SELECT 1 FROM vpn_servers
      WHERE server_host = '89.169.135.48' AND xray_inbound_tag = 'from-yandex-cdn'
  );

-- Adapt the cloned link_template for the .online CDN front and the Lithuanian label fragment.
-- Both fragments are URL-encoded (id=34 stores its fragment encoded); the ", Литва" tail was
-- generated via urllib.parse.quote('🛡️ Обход белых списков, Литва').
UPDATE vpn_servers
SET link_template = replace(
        replace(link_template,
                'cdn.strimingset.ru', 'cdn.strimingset.online'),
        '%F0%9F%9B%A1%EF%B8%8F%20%D0%9E%D0%B1%D1%85%D0%BE%D0%B4%20%D0%B1%D0%B5%D0%BB%D1%8B%D1%85%20%D1%81%D0%BF%D0%B8%D1%81%D0%BA%D0%BE%D0%B2',
        '%F0%9F%9B%A1%EF%B8%8F%20%D0%9E%D0%B1%D1%85%D0%BE%D0%B4%20%D0%B1%D0%B5%D0%BB%D1%8B%D1%85%20%D1%81%D0%BF%D0%B8%D1%81%D0%BA%D0%BE%D0%B2%2C%20%D0%9B%D0%B8%D1%82%D0%B2%D0%B0')
WHERE server_host = '89.169.135.48' AND xray_inbound_tag = 'from-yandex-cdn';
