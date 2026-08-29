-- SYNCBOND evolution governance — immutable signed promotion records.
-- Vault archives governance evidence only; merge/deploy authority remains elsewhere.

CREATE TABLE IF NOT EXISTS syncbond_promotion_records (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_sha256       TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256         TEXT NOT NULL UNIQUE CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    eligible_for_merge    BOOLEAN NOT NULL,
    approver_actor_id     TEXT NOT NULL,
    approval_key_id       TEXT NOT NULL,
    approval_decision     TEXT NOT NULL CHECK (approval_decision IN ('approved','rejected')),
    record                JSONB NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_syncbond_promotion_records_manifest
    ON syncbond_promotion_records (manifest_sha256, created_at DESC);
