-- Holdout уведомлений: детерминированная контрольная группа (~10% подходящих
-- пользователей не получает nudge-уведомление, чтобы измерять его реальный эффект).
-- Запись со статусом 'holdout' занимает слот дедупа и наблюдаема в SQL.
ALTER TABLE notification_log DROP CONSTRAINT IF EXISTS notification_log_delivery_status_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notification_log_delivery_status_check'
          AND conrelid = 'notification_log'::regclass
    ) THEN
        ALTER TABLE notification_log
            ADD CONSTRAINT notification_log_delivery_status_check
            CHECK (delivery_status IN ('delivered', 'terminal_failure', 'holdout'));
    END IF;
END
$$;
