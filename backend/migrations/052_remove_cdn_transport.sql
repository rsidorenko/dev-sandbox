-- Remove the CDN (Cloudflare / techno-channel WebSocket) transport entirely.
-- The 2.0 CDN configs (Helsinki fi.techno-channel.ru, Frankfurt de.techno-channel.online)
-- were unreliable in practice and are retired from the fleet. Also renumber the surviving
-- xhttp "3.0" configs to "2.0" (Helsinki / Frankfurt / LA / Lithuania) so each city now
-- reads 1.0 (tcp/Reality) + 2.0 (xhttp/Reality). LTE (tcp) is untouched (not xhttp).
-- Idempotent: safe to re-run — DELETE matches no rows, REPLACE matches no "3.0".
DELETE FROM vpn_servers WHERE transport_type = 'cdn';
UPDATE vpn_servers
SET label = REPLACE(label, '3.0', '2.0')
WHERE transport_type = 'xhttp' AND label LIKE '%3.0%';
