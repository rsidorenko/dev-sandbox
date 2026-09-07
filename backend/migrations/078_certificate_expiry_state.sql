-- Состояние сертификатного мониторинга (ветка A плана 2026-09-07):
-- периодическая проверка сроков client-facing сертификатов (main-сайт, egress-релеи,
-- CDN-origins, camo-серты entry-серверов) с алертингом оператору.
-- Только additive: одна новая таблица, существующие таблицы не затрагиваются.
CREATE TABLE IF NOT EXISTS certificate_expiry_state (
    name              TEXT         PRIMARY KEY,
    mode              TEXT         NOT NULL CHECK (mode IN ('tls', 'ssh-file')),
    not_after         TIMESTAMPTZ  NULL,
    days_left         INTEGER      NULL,
    checked_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_alert_bucket INTEGER      NULL
);
