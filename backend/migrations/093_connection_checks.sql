-- Результаты автоматической проверки подключения (feat/vpn-connection-check).
-- Одна строка = один прогон страницы /check/<token>. IP-адреса пользователей
-- намеренно не хранятся (privacy): только факт выхода через наш сервер, метка
-- сервера, задержка/скорость и платформа.
CREATE TABLE IF NOT EXISTS connection_checks (
    id BIGSERIAL PRIMARY KEY,
    internal_user_id TEXT NOT NULL REFERENCES user_identities (internal_user_id) ON DELETE CASCADE,
    connected BOOLEAN NOT NULL DEFAULT FALSE,
    detected_server_label TEXT NOT NULL DEFAULT '',
    latency_ms INT CHECK (latency_ms IS NULL OR (latency_ms >= 0 AND latency_ms <= 60000)),
    throughput_kbps INT CHECK (throughput_kbps IS NULL OR (throughput_kbps >= 0 AND throughput_kbps <= 1000000)),
    platform TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- История проверок пользователя (новые сверху) + свежий срез для когорт.
CREATE INDEX IF NOT EXISTS idx_connection_checks_user_time
    ON connection_checks (internal_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_connection_checks_time
    ON connection_checks (created_at DESC);
