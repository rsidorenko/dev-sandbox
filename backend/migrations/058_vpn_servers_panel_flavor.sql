-- 3x-ui panel fork flavor. 'standard' = vanilla 3x-ui (client CRUD via update, no restart).
-- 'h1' = the h1cloud Pterodactyl h1-fork, which needs its own client schema (auth/password/
-- subId/security, no flow) AND an xray restart after client changes (it doesn't hot-apply them).
-- The provider's h1 path does a batch desired-state update + restartXrayService. See
-- XuiApiClient.restart_xray / build_h1_client.
ALTER TABLE vpn_servers ADD COLUMN IF NOT EXISTS panel_flavor TEXT NOT NULL DEFAULT 'standard';
