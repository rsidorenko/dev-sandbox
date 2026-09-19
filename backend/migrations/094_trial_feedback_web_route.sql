-- Web-страница проверки подключения (/check) пишет причины проблем с
-- connection_route='' (bot-маршрут ей неизвестен) и, при неопознанном UA,
-- с platform=''. Констрейнт 092 это запрещал — записи молча отбрасывались
-- (recorder fail-open). Ослабляем context-check до разделения по типу:
-- connection_problem — только закрытый набор причин; требования platform/route
-- непустыми остаются только для bot-семантики (проверять неоткуда, web-строка
-- валидна с пустыми значениями). trial_nonpayment — без изменений.
ALTER TABLE trial_feedback DROP CONSTRAINT IF EXISTS trial_feedback_context_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'trial_feedback_context_check'
          AND conrelid = 'trial_feedback'::regclass
    ) THEN
        ALTER TABLE trial_feedback
            ADD CONSTRAINT trial_feedback_context_check CHECK (
                (
                    feedback_type = 'connection_problem'
                    AND reason_code IN (
                        'install_failed', 'import_failed', 'connect_failed',
                        'traffic_failed', 'too_hard', 'other'
                    )
                ) OR (
                    feedback_type = 'trial_nonpayment'
                    AND platform = ''
                    AND connection_route = ''
                    AND reason_code IN (
                        'too_expensive', 'did_not_work', 'too_hard',
                        'no_need', 'payment_issue', 'other'
                    )
                )
            );
    END IF;
END
$$;
