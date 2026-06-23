-- Vault Zeta — initial schema (vertical slice)
-- Subset of the full architecture: identity, conversations, episodic + semantic memory.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============ identity ============
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle      TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO users (handle) VALUES ('karnveer')
ON CONFLICT (handle) DO NOTHING;

-- ============ conversations ============
CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL DEFAULT 'api',
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    summary     TEXT,
    embedding   VECTOR(768)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content      TEXT NOT NULL,
    tokens_in    INT,
    tokens_out   INT,
    latency_ms   INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding    VECTOR(768)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_embedding ON messages
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============ semantic memory ============
DO $$ BEGIN
    CREATE TYPE memory_kind AS ENUM ('episodic','semantic','factual','procedural','reflection');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            memory_kind NOT NULL,
    content         TEXT NOT NULL,
    source          TEXT,
    importance      REAL NOT NULL DEFAULT 0.5,
    decay_after     TIMESTAMPTZ,
    embedding       VECTOR(768),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    use_count       INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_user_kind ON memories (user_id, kind, importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
