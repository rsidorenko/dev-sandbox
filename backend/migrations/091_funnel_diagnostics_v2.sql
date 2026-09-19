-- Маршрут подключения — ограниченный аналитический enum. Существующие события
-- получают пустое значение и сохраняют прежнюю семантику.
ALTER TABLE funnel_interactions
    ADD COLUMN IF NOT EXISTS connection_route TEXT NOT NULL DEFAULT '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'funnel_interactions_connection_route_check'
          AND conrelid = 'funnel_interactions'::regclass
    ) THEN
        ALTER TABLE funnel_interactions
            ADD CONSTRAINT funnel_interactions_connection_route_check
            CHECK (connection_route IN ('', 'direct', 'guided')) NOT VALID;
    END IF;
END
$$;

ALTER TABLE funnel_interactions
    VALIDATE CONSTRAINT funnel_interactions_connection_route_check;

CREATE INDEX IF NOT EXISTS idx_funnel_interactions_route_time
    ON funnel_interactions (connection_route, occurred_at)
    WHERE connection_route <> '';
