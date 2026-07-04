-- xray inbound tag for provider_kind='bare_xray' servers (e.g. the Cloud.ru RU relay
-- entry). The bot's XrayApiClient addresses a specific inbound by this tag when calling
-- xray's gRPC HandlerService.AlterInbound (AddUser/RemoveUser) to manage users live.
-- Nullable: only bare_xray rows set it; xui rows (managed via panel inbound_id) ignore it.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS xray_inbound_tag TEXT;
