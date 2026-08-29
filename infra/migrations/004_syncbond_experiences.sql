-- SYNCBOND v5 — distilled continuity experiences.
-- Raw operational logs remain in Scrappy OS; this table stores only verified,
-- intentionally distilled outcomes suitable for Vault continuity.

CREATE TABLE IF NOT EXISTS syncbond_experiences (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id       UUID NOT NULL,
    remote_objective_id  UUID NOT NULL,
    user_id              UUID REFERENCES users(id) ON DELETE SET NULL,
    outcome              TEXT NOT NULL CHECK (outcome IN ('succeeded','failed','partial','blocked')),
    summary              TEXT NOT NULL,
    evidence             JSONB NOT NULL DEFAULT '[]'::jsonb,
    lessons              JSONB NOT NULL DEFAULT '[]'::jsonb,
    source               TEXT NOT NULL DEFAULT 'scrappy-os',
    envelope             JSONB NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (correlation_id, remote_objective_id)
);

CREATE INDEX IF NOT EXISTS idx_syncbond_experiences_user_created
    ON syncbond_experiences (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_syncbond_experiences_correlation
    ON syncbond_experiences (correlation_id);
