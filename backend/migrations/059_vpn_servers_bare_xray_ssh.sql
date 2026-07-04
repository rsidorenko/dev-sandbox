-- Per-row SSH credentials for provider_kind='bare_xray' servers. The bot's XrayApiClient
-- SSHes to each bare_xray host to run grpcurl (xray gRPC API). With one server (Cloud.ru)
-- the global CLOUDRU_SSH_USER/CLOUDRU_SSH_KEY env suffices; a second bare_xray host with a
-- different key (e.g. bravada2/176.123.167.72) needs per-row creds. These columns override
-- the env; NULL = fall back to CLOUDRU_SSH_* (Cloud.ru stays unchanged).
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS ssh_user TEXT;
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS ssh_key_path TEXT;
