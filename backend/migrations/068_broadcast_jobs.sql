-- Broadcast (рассылка) jobs — учёт массовых рассылок оператора из админ-панели.
--
-- Каждая строка = один запуск рассылки: снимок запроса (категории + явные цели),
-- текст сообщения, parse_mode (HTML), и инкрементальные счётчики доставки, которые
-- обновляются фоновой задачей BroadcastRunner в процессе отправки.
-- Таблица НЕ хранит тела сообщений получателей и не связана с billing/notification —
-- это отдельная операторская поверхность, изолированная от lifecycle-уведомлений.
CREATE TABLE IF NOT EXISTS broadcast_jobs (
    job_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN
                          ('queued', 'running', 'completed', 'failed', 'interrupted', 'cancelled')),
    categories          TEXT[] NOT NULL DEFAULT '{}',
    explicit_telegram_ids BIGINT[] NOT NULL DEFAULT '{}',
    message_text        TEXT NOT NULL,
    parse_mode          TEXT NOT NULL DEFAULT 'HTML',
    disable_web_page_preview BOOLEAN NOT NULL DEFAULT FALSE,
    total_recipients    INTEGER NOT NULL DEFAULT 0,
    sent_count          INTEGER NOT NULL DEFAULT 0,
    failed_count        INTEGER NOT NULL DEFAULT 0,
    blocked_count       INTEGER NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NULL,
    finished_at         TIMESTAMPTZ NULL,
    last_error          TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_created_at
    ON broadcast_jobs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_status
    ON broadcast_jobs (status);
