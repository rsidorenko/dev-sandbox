-- Закрытые причины проблем триала. Свободный текст и Telegram-данные не сохраняются.
CREATE TABLE IF NOT EXISTS trial_feedback (
    id BIGSERIAL PRIMARY KEY,
    internal_user_id TEXT NOT NULL REFERENCES user_identities(internal_user_id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL CHECK (feedback_type IN ('connection_problem', 'trial_nonpayment')),
    reason_code TEXT NOT NULL CHECK (reason_code IN (
        'install_failed', 'import_failed', 'connect_failed', 'traffic_failed',
        'too_hard', 'other', 'too_expensive', 'did_not_work', 'no_need', 'payment_issue'
    )),
    platform TEXT NOT NULL DEFAULT '' CHECK (platform IN ('', 'ios', 'android', 'mac', 'win', 'tv')),
    connection_route TEXT NOT NULL DEFAULT '' CHECK (connection_route IN ('', 'direct', 'guided')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT trial_feedback_context_check CHECK (
        (
            feedback_type = 'connection_problem'
            AND platform <> ''
            AND connection_route <> ''
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
    ),
    UNIQUE (internal_user_id, feedback_type, platform)
);

CREATE INDEX IF NOT EXISTS idx_trial_feedback_type_time
    ON trial_feedback (feedback_type, updated_at DESC);
