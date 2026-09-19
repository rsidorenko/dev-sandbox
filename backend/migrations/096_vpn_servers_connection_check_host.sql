-- Явный публичный egress/check-host для Connection Check. server_host нельзя
-- использовать безусловно: у relay/CDN это входная точка, а не адрес выхода.
ALTER TABLE vpn_servers
    ADD COLUMN IF NOT EXISTS connection_check_host TEXT NOT NULL DEFAULT '';

-- Безопасный bootstrap только для прямых 3x-ui TCP-серверов. Relay остаются
-- пустыми до явной операторской настройки фактического egress host/IP.
UPDATE vpn_servers
SET connection_check_host = server_host
WHERE connection_check_host = ''
  AND provider_kind = 'xui'
  AND transport_type = 'tcp'
  AND label NOT ILIKE '%relay%';
