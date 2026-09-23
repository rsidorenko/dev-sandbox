-- Служебные измерения отделены от пользователей и StatsService.
CREATE TABLE IF NOT EXISTS vpn_quality_accounts (
    location TEXT PRIMARY KEY CHECK (location ~ '^[a-z][a-z0-9_-]{0,31}$'),
    internal_user_id TEXT NOT NULL UNIQUE REFERENCES user_identities(internal_user_id),
    entry_server_id INTEGER NOT NULL REFERENCES vpn_servers(id),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vpn_quality_runs (
    id BIGSERIAL PRIMARY KEY,
    location TEXT NOT NULL REFERENCES vpn_quality_accounts(location),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running','complete','measurement_error','skipped')),
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_vpn_quality_runs_time ON vpn_quality_runs(started_at DESC);
CREATE TABLE IF NOT EXISTS vpn_quality_samples (
    run_id BIGINT NOT NULL REFERENCES vpn_quality_runs(id),
    server_id INTEGER NOT NULL REFERENCES vpn_servers(id),
    route INTEGER NOT NULL DEFAULT 0 CHECK (route BETWEEN 0 AND 65535),
    label TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('ok','route_unknown','route_mismatch','timeout',
        'tls_error','connect_error','http_error','invalid_response','measurement_error')),
    http_code INTEGER,
    tls_ms DOUBLE PRECISION,
    ttfb_ms DOUBLE PRECISION,
    total_ms DOUBLE PRECISION,
    response_bytes INTEGER,
    PRIMARY KEY (run_id,server_id,route)
);
