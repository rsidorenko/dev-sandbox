-- Отличаем успешную доставку от терминального отказа Telegram (например, bot blocked).
-- DEFAULT сохраняет совместимость со всеми историческими и операторскими INSERT.
ALTER TABLE notification_log
    ADD COLUMN IF NOT EXISTS delivery_status TEXT NOT NULL DEFAULT 'delivered';

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
            CHECK (delivery_status IN ('delivered', 'terminal_failure'));
    END IF;
END
$$;
