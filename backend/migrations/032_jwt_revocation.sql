-- JWT revocation list: allows invalidating specific JWTs (e.g. on logout).
CREATE TABLE IF NOT EXISTS jwt_revocation_list (
    jti TEXT PRIMARY KEY,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Auto-cleanup: revoke entries older than 7 days are safe to remove
-- (JWT max TTL is 72h, so 7 days gives plenty of margin).
CREATE INDEX IF NOT EXISTS idx_jwt_revocation_revoked_at
    ON jwt_revocation_list (revoked_at);
