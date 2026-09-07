-- 079: учёт трафика bare_xray (QueryStats-коллектор).
-- Только additive: две новые таблицы, ничего не удаляем и не меняем.
--
-- bare_xray_traffic_state — последнее снятое показание per-PROCESS счётчика
-- (для диффа; абсолютные значения, не дельты). После рестарта xray счётчик
-- рождается заново — коллектор делает clamp-to-new, см. bare_xray_traffic.diff_deltas.
--
-- bare_xray_traffic_daily — накопление дельт по (MSK-дата, сервер, email);
-- UPSERT-аккумуляция: uplink_bytes = bare_xray_traffic_daily.uplink_bytes + EXCLUDED.uplink_bytes.
--
-- server_id БЕЗ FK на vpn_servers: строки серверов исторически выводились из
-- эксплуатации (см. 050_drop_lte_servers) — архив трафика не должен ломаться
-- при исчезновении строки сервера; целостность обеспечивает коллектор.

CREATE TABLE IF NOT EXISTS bare_xray_traffic_state (
    server_id     INTEGER     NOT NULL,
    email         TEXT        NOT NULL,
    uplink_last   BIGINT      NOT NULL DEFAULT 0,
    downlink_last BIGINT      NOT NULL DEFAULT 0,
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (server_id, email)
);

CREATE TABLE IF NOT EXISTS bare_xray_traffic_daily (
    stat_date      DATE        NOT NULL,
    server_id      INTEGER     NOT NULL,
    email          TEXT        NOT NULL,
    uplink_bytes   BIGINT      NOT NULL DEFAULT 0,
    downlink_bytes BIGINT      NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stat_date, server_id, email)
);

CREATE INDEX IF NOT EXISTS idx_bare_xray_traffic_daily_email_date
    ON bare_xray_traffic_daily (email, stat_date);
