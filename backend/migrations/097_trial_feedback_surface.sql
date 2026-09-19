-- Telegram и web (/check) — независимые источники ответа. Раньше web-ответ
-- мог перезаписать Telegram route из-за unique(user, type, platform).
ALTER TABLE trial_feedback
    ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT 'telegram';

-- Пустой route допустим только у существующих web connection_problem-строк.
UPDATE trial_feedback
SET surface = 'web'
WHERE feedback_type = 'connection_problem'
  AND connection_route = ''
  AND surface = 'telegram';

ALTER TABLE trial_feedback
    DROP CONSTRAINT IF EXISTS trial_feedback_internal_user_id_feedback_type_platform_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_feedback_user_type_platform_surface
    ON trial_feedback (internal_user_id, feedback_type, platform, surface);

ALTER TABLE trial_feedback DROP CONSTRAINT IF EXISTS trial_feedback_surface_check;
ALTER TABLE trial_feedback
    ADD CONSTRAINT trial_feedback_surface_check CHECK (surface IN ('telegram', 'web'));

ALTER TABLE trial_feedback DROP CONSTRAINT IF EXISTS trial_feedback_context_check;
ALTER TABLE trial_feedback
    ADD CONSTRAINT trial_feedback_context_check CHECK (
        (
            feedback_type = 'connection_problem'
            AND reason_code IN (
                'install_failed', 'import_failed', 'connect_failed',
                'traffic_failed', 'too_hard', 'other'
            )
            AND (
                (surface = 'web' AND connection_route = '')
                OR (surface = 'telegram' AND platform <> '' AND connection_route IN ('direct', 'guided'))
            )
        ) OR (
            feedback_type = 'trial_nonpayment'
            AND surface = 'telegram'
            AND platform = ''
            AND connection_route = ''
            AND reason_code IN (
                'too_expensive', 'did_not_work', 'too_hard',
                'no_need', 'payment_issue', 'other'
            )
        )
    );
