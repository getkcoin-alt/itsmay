-- Switch embedding columns to 384 dim for fastembed `BAAI/bge-small-en-v1.5`.
-- Destructive: clears existing embeddings. Re-embed later if needed.

BEGIN;

DROP INDEX IF EXISTS idx_messages_embedding;
DROP INDEX IF EXISTS idx_memories_embedding;

UPDATE messages SET embedding = NULL;
UPDATE memories SET embedding = NULL;
UPDATE sessions SET embedding = NULL;

ALTER TABLE messages ALTER COLUMN embedding TYPE VECTOR(384);
ALTER TABLE memories ALTER COLUMN embedding TYPE VECTOR(384);
ALTER TABLE sessions ALTER COLUMN embedding TYPE VECTOR(384);

CREATE INDEX IF NOT EXISTS idx_messages_embedding ON messages
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

COMMIT;
