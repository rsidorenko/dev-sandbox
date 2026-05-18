-- Add Reality TLS parameters to vpn_servers for VLESS+TCP+Reality link generation.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS reality_pbk TEXT NOT NULL DEFAULT '';
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS reality_sid TEXT NOT NULL DEFAULT '37';
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS reality_sni TEXT NOT NULL DEFAULT 'eh.vk.ru';

-- Seed Reality public key for Helsinki server.
UPDATE vpn_servers SET
    reality_pbk = 'f1m7tkhI4Ez7GlRF7k2E55V86XLsu5jzIphl3yhKgyI',
    reality_sid = '37',
    reality_sni = 'eh.vk.ru'
WHERE server_host = '77.221.159.106';
