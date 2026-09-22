-- Обязательство выдачи фиксируется вместе со списанием; секретов здесь нет.
CREATE TABLE IF NOT EXISTS balance_access_jobs (
    internal_user_id TEXT NOT NULL REFERENCES user_identities(internal_user_id) ON DELETE CASCADE,
    operation_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'superseded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (internal_user_id, operation_key)
);
CREATE INDEX IF NOT EXISTS balance_access_jobs_pending_idx
    ON balance_access_jobs (next_attempt_at) WHERE status = 'pending';
