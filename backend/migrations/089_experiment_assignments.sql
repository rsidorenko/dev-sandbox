-- Стабильные server-side назначения A/B-вариантов. Строка создаётся при первой
-- реальной экспозиции авторизованного пользователя и больше не меняет вариант.
CREATE TABLE IF NOT EXISTS experiment_assignments (
    internal_user_id TEXT NOT NULL,
    experiment_key TEXT NOT NULL CHECK (length(experiment_key) BETWEEN 1 AND 80),
    variant TEXT NOT NULL CHECK (length(variant) BETWEEN 1 AND 40),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (internal_user_id, experiment_key)
);

CREATE INDEX IF NOT EXISTS idx_experiment_assignments_key_time
    ON experiment_assignments (experiment_key, assigned_at DESC);
