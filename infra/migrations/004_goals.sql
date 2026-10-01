-- Durable Scrappy objective/task ledger.
-- Planning state is data, not executable authority. Tools still require the
-- existing policy/approval path.

CREATE TABLE IF NOT EXISTS goal_runtime_state (
    id          TEXT PRIMARY KEY,
    state       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
