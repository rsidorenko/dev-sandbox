-- Update LTE relay SNI from eh.vk.ru to max.ru for mobile internet bypass.
-- max.ru is the MAX messenger domain (VK, integrated with Gosuslugi) — whitelisted by Russian mobile operators.
UPDATE vpn_servers
SET reality_sni = 'max.ru'
WHERE id = 10 AND server_host = '84.252.131.102';
