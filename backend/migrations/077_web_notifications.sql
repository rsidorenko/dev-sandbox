-- Веб-уведомления для всех, кто может залогиниться на сайт (verified email в user_emails):
-- web-only (отрицательные telegram_user_id) и линкнутые TG-пользователи (положительные).
-- Источники: рассылки из админки (kind='broadcast', payload={job_id, bonus}) и сервисные
-- события notification_scheduler (kind по маппингу _WEB_KINDS, payload={notification_type}).
CREATE TABLE IF NOT EXISTS web_notifications (
    id               BIGSERIAL    PRIMARY KEY,
    internal_user_id TEXT         NOT NULL,
    kind             TEXT         NOT NULL CHECK (kind IN
        ('broadcast', 'trial_expiring', 'trial_expired', 'subscription_expiring',
         'subscription_expired', 'key_deleted', 'referral_offer')),
    title            TEXT         NOT NULL,
    body             TEXT         NULL,
    payload          JSONB        NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    read_at          TIMESTAMPTZ  NULL
);

-- Лента пользователя (dropdown, пагинация по id DESC) + счётчик непрочитанных.
CREATE INDEX IF NOT EXISTS idx_web_notifications_user_desc
    ON web_notifications (internal_user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_web_notifications_user_unread
    ON web_notifications (internal_user_id)
    WHERE read_at IS NULL;

-- Прогресс рассылки по веб-каналу (админ-панель): сколько web-получателей выбрано/уведомлено.
ALTER TABLE broadcast_jobs ADD COLUMN IF NOT EXISTS web_recipient_count  INTEGER NULL;
ALTER TABLE broadcast_jobs ADD COLUMN IF NOT EXISTS web_notified_count INTEGER NULL;
