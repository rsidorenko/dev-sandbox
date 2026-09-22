-- Операционный журнал отделён от оплаты и VPN: закрытие записи не меняет доступ.
CREATE TABLE IF NOT EXISTS admin_incidents (
    id UUID PRIMARY KEY,
    source_key TEXT,
    request_fingerprint TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','ack','resolved')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS admin_incidents_active_source
    ON admin_incidents(source_key) WHERE status <> 'resolved' AND source_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS admin_incident_events (
    id BIGSERIAL PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES admin_incidents(id),
    actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create','ack','resolve','note')),
    note TEXT NOT NULL DEFAULT '' CHECK (length(note)<=1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS admin_incident_events_incident ON admin_incident_events(incident_id,id);
