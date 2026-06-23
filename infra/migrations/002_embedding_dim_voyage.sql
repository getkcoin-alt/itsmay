-- Migrate vector columns to match the chosen embedding model dim.
-- Default: Voyage AI `voyage-3-lite` → 512 dim.
--
-- If you switch embedding models later, write a new migration (003_…) rather
-- than editing this one — the migration runner keys on the filename.
--
-- Destructive: clears existing embeddings. Re-embed later if needed.

BEGIN;

DROP INDEX IF EXISTS idx_messages_embedding;
DROP INDEX IF EXISTS idx_memories_embedding;

UPDATE messages SET embedding = NULL;
UPDATE memories SET embedding = NULL;
UPDATE sessions SET embedding = NULL;

ALTER TABLE messages ALTER COLUMN embedding TYPE VECTOR(512);
ALTER TABLE memories ALTER COLUMN embedding TYPE VECTOR(512);
ALTER TABLE sessions ALTER COLUMN embedding TYPE VECTOR(512);

CREATE INDEX IF NOT EXISTS idx_messages_embedding ON messages
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

COMMIT;
