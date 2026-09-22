-- Последний результат существующего опроса StatsService; секреты не сохраняются.
CREATE TABLE IF NOT EXISTS vpn_server_observations (
    server_id INTEGER PRIMARY KEY REFERENCES vpn_servers(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    checked_at TIMESTAMPTZ NOT NULL,
    last_success_at TIMESTAMPTZ
);
